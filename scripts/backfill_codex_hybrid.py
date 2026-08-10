"""Idempotently backfill Codex D1 v2 structured state into Supabase."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import sqlite3
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from doxagent.codex_runtime.client import HttpCodexWorkerClient, WorkspaceClient
from doxagent.codex_runtime.repository import (
    PostgresCodexRuntimeRepository,
    SQLiteCodexRuntimeRepository,
)
from doxagent.codex_runtime.schema import (
    ArtifactKind,
    ArtifactRef,
    Document1V2Bundle,
    NodeAttempt,
    PublishedDocument,
    ThreadRecord,
    WorkflowCheckpoint,
    WorkflowEvent,
)
from doxagent.settings import DoxAgentSettings

ModelT = TypeVar("ModelT", bound=BaseModel)


def _records(path: Path, record_type: str, model: type[ModelT]) -> list[ModelT]:
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            """SELECT payload_json FROM codex_runtime_records
               WHERE record_type=? ORDER BY run_id, sort_order, updated_at""",
            (record_type,),
        ).fetchall()
    return [model.model_validate_json(row[0]) for row in rows]


async def _artifact_content(
    workspace: WorkspaceClient,
    artifact: ArtifactRef,
) -> str:
    file = await workspace.read_text(artifact.run_id, artifact.relative_path)
    raw = file.content.encode("utf-8")
    if hashlib.sha256(raw).hexdigest() != artifact.sha256:
        raise RuntimeError(f"artifact SHA256 mismatch: {artifact.artifact_id}")
    if len(raw) != artifact.size_bytes:
        raise RuntimeError(f"artifact size mismatch: {artifact.artifact_id}")
    return file.content


async def backfill(args: argparse.Namespace) -> None:
    settings = DoxAgentSettings()
    sqlite_path = Path(args.sqlite_path or settings.codex_runtime_sqlite_path)
    if not settings.database_url:
        raise RuntimeError("DOXAGENT_DATABASE_URL is required")
    bearer = settings.codex_worker_bearer_token
    secret = settings.codex_capability_secret
    if not bearer or not secret:
        raise RuntimeError("Codex worker bearer and capability secrets are required")

    local = SQLiteCodexRuntimeRepository(sqlite_path)
    remote = PostgresCodexRuntimeRepository(settings.database_url, evidence_repository=local)
    workspace = HttpCodexWorkerClient(
        args.worker_url or settings.codex_worker_base_url,
        bearer,
        capability_secret=secret,
    )

    checkpoints = _records(sqlite_path, "checkpoints", WorkflowCheckpoint)
    threads = _records(sqlite_path, "threads", ThreadRecord)
    attempts = _records(sqlite_path, "attempts", NodeAttempt)
    artifacts = _records(sqlite_path, "artifacts", ArtifactRef)
    events = _records(sqlite_path, "events", WorkflowEvent)
    bundles = _records(sqlite_path, "bundles", Document1V2Bundle)
    runs = {item.run_id for item in checkpoints}
    if not runs:
        raise RuntimeError("no Codex runs found in SQLite")

    with remote.transaction():
        for item in checkpoints:
            remote.save_checkpoint(item)
        for item in threads:
            remote.save_thread(item)
        for item in attempts:
            remote.save_attempt(item)
        for item in artifacts:
            remote.save_artifact(item)
        for item in events:
            remote.append_event(item)

    published_by_run: dict[str, list[ArtifactRef]] = {}
    for item in artifacts:
        if item.published and item.kind in {
            ArtifactKind.REPORT,
            ArtifactKind.BUNDLE,
            ArtifactKind.MANIFEST,
        }:
            published_by_run.setdefault(item.run_id, []).append(item)
    documents: list[PublishedDocument] = []
    for run_id, references in published_by_run.items():
        for reference in references:
            content = await _artifact_content(workspace, reference)
            raw = content.encode("utf-8")
            if len(raw) > 2 * 1024 * 1024:
                raise RuntimeError("PUBLISHED_DOCUMENT_STORAGE_REQUIRED: " + reference.artifact_id)
            bundle = next((item for item in bundles if item.run_id == run_id), None)
            if bundle is None or bundle.published_at is None:
                raise RuntimeError(f"published artifact has no published bundle: {run_id}")
            documents.append(
                PublishedDocument(
                    artifact_id=reference.artifact_id,
                    run_id=run_id,
                    artifact_kind=reference.kind.value,
                    sha256=reference.sha256,
                    size_bytes=reference.size_bytes,
                    content_type=reference.content_type,
                    content_text=content,
                    published_at=bundle.published_at,
                )
            )
    with remote.transaction():
        for document in documents:
            remote.save_published_document(document)
        for item in bundles:
            remote.save_bundle(item)
            if item.status == "published" and item.published_at is not None:
                remote.mark_run_published(item.run_id, item.published_at)

    with remote.transaction():
        for run_id in sorted(runs):
            expected = {
                "threads": sum(item.run_id == run_id for item in threads),
                "attempts": sum(item.run_id == run_id for item in attempts),
                "artifacts": sum(item.run_id == run_id for item in artifacts),
                "events": sum(item.run_id == run_id for item in events),
                "documents": len(published_by_run.get(run_id, [])),
            }
            actual = {
                "threads": sum(
                    remote.get_thread(run_id, role) is not None
                    for role in {item.agent_role.value for item in threads if item.run_id == run_id}
                ),
                "attempts": len(remote.list_attempts(run_id, 500)),
                "artifacts": len(remote.list_artifacts(run_id, 500)),
                "events": len(remote.list_events(run_id, -1, 500)),
                "documents": sum(
                    remote.get_published_document(run_id, item.artifact_id) is not None
                    for item in published_by_run.get(run_id, [])
                ),
            }
            if actual != expected:
                raise RuntimeError(
                    f"backfill count mismatch for {run_id}: expected={expected}, actual={actual}"
                )
            bundle = remote.get_bundle(run_id)
            if bundle is None or bundle.handoff is None:
                raise RuntimeError(f"backfill bundle/handoff missing for {run_id}")
            print(f"verified {run_id}: {actual}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sqlite-path")
    parser.add_argument("--worker-url", default="http://127.0.0.1:8791")
    args = parser.parse_args()
    asyncio.run(backfill(args))


if __name__ == "__main__":
    main()
