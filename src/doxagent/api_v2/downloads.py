"""Explicit downloads stream only the selected, already indexed immutable run."""

from __future__ import annotations

import hashlib
import os
import tempfile
import zipfile
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import Response, StreamingResponse

from doxagent.v2_read.repository import encode

from .errors import ApiFailure


def install(app: FastAPI) -> None:
    @app.get("/api/doxagent/v2/tickers/{ticker}/expectations/runs/{run_id}/download")
    async def expectations_download(ticker: str, run_id: str, request: Request) -> Any:
        app.state.query(request, set())
        store = app.state.store
        reference = store.get("document_ref", ticker, run_id)
        if not reference or not store.get("expectations_index", ticker, run_id):
            raise ApiFailure("RESOURCE_NOT_FOUND", 404)
        from doxagent.v2_read.artifacts import PublishedArtifacts
        source = os.environ.get("DOXAGENT_CODEX_RUNTIME_SQLITE_PATH")
        if not source:
            raise ApiFailure("PINNED_ARTIFACT_MISSING", 503)
        try:
            document, text = PublishedArtifacts(source).body(run_id, reference["artifact_id"])
        except (KeyError, ValueError, OSError):
            raise ApiFailure("PINNED_ARTIFACT_MISSING", 503) from None
        if document["sha256"] != reference["content_sha256"]:
            raise ApiFailure("CONTENT_CORRUPT", 503)
        return Response(
            content=text.encode("utf-8"), media_type="application/json",
            headers={"Content-Disposition": 'attachment; filename="document2.json"',
                     "ETag": '"' + reference["content_sha256"] + '"',
                     "Cache-Control": "private, no-store"},
        )

    @app.get("/api/doxagent/v2/tickers/{ticker}/research/runs/{run_id}/download")
    async def download(ticker: str, run_id: str, request: Request) -> Any:
        app.state.query(request, set())
        store = app.state.store
        manifest = store.get("research_download", ticker, run_id)
        research = store.get("research", ticker, run_id)
        if not manifest or not research:
            raise ApiFailure("RESOURCE_NOT_FOUND", 404)
        references = {s["section"]: s["content"]["data"] for s in research["sections"]}
        archive = tempfile.SpooledTemporaryFile(max_size=1_048_576)
        try:
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zipped:
                zipped.writestr("manifest.json", encode(manifest))
                for entry in manifest["files"]:
                    if entry["state"] != "INCLUDED":
                        continue
                    ref = references[entry["section"]]
                    offset, digest = 0, hashlib.sha256()
                    with zipped.open(entry["entry"], "w") as output:
                        while offset < ref["size_bytes"]:
                            _, text, end = store.content(ticker, ref["content_id"], offset, 65536)
                            if end <= offset:
                                raise ApiFailure("CONTENT_CORRUPT", 503)
                            raw = text.encode("utf-8")
                            digest.update(raw)
                            output.write(raw)
                            offset = end
                    if digest.hexdigest() != entry["sha256"] or offset != entry["size_bytes"]:
                        raise ApiFailure("CONTENT_CORRUPT", 503)
            archive.seek(0)
        except BaseException:
            archive.close()
            raise

        def chunks() -> Any:
            try:
                while raw := archive.read(65536):
                    yield raw
            finally:
                archive.close()

        return StreamingResponse(
            chunks(),
            media_type="application/zip",
            headers={
                "Content-Disposition": 'attachment; filename="research.zip"',
                "Cache-Control": "private, no-store",
            },
        )
