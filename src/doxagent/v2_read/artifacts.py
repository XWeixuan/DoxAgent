"""Index exact, checksum-verified published artifacts into immutable page objects."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from doxagent.api_v2.dto import VERSION, available, coverage, missing, validate
from doxagent.codex_runtime.schema import FutureNode
from doxagent.workflows.codex_document2.schema import Document2Document

from .projectors import timestamp
from .repository import ReadStore, encode


class PublishedArtifacts:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()

    def record(self, kind: str, identity: str, run_id: str) -> dict[str, Any]:
        db = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=2)
        try:
            row = db.execute(
                "SELECT payload_json FROM codex_runtime_records WHERE "
                "record_type=? AND record_key=? AND run_id=?",
                (kind, identity, run_id),
            ).fetchone()
            if row is None:
                raise KeyError("PINNED_ARTIFACT_MISSING")
            return json.loads(row[0])
        finally:
            db.close()

    def body(self, run_id: str, identity: str) -> tuple[dict[str, Any], str]:
        artifact = self.record("artifacts", identity, run_id)
        if not artifact["published"]:
            raise ValueError("artifact is not published")
        document = self.record("published_documents", identity, run_id)
        text = document.get("content_text")
        if text is None:
            raise ValueError("REMOTE_CONTENT_BACKFILL_REQUIRED")
        data = text.encode("utf-8")
        if (
            hashlib.sha256(data).hexdigest() != artifact["sha256"]
            or len(data) != artifact["size_bytes"]
        ):
            raise ValueError("PUBLISHED_CHECKSUM_MISMATCH")
        if document["sha256"] != artifact["sha256"]:
            raise ValueError("PUBLISHED_CHECKSUM_MISMATCH")
        return document, text


def resource(value: Any, *, reason: str = "NOT_PRODUCED") -> dict[str, Any]:
    return {
        "state": "AVAILABLE" if value is not None else "NOT_PRODUCED",
        "data": value,
        "reason": None if value is not None else reason,
        "coverage": coverage(complete=value is not None),
    }


class ArtifactIndexer:
    def __init__(self, store: ReadStore, source: PublishedArtifacts) -> None:
        self.store, self.source = store, source

    def index(self, run_id: str, ticker: str, *, lineage: str) -> list[dict[str, Any]]:
        if not lineage:
            raise ValueError("PROVENANCE_UNVERIFIED")
        bundle = self.source.record("bundles", run_id, run_id)
        if bundle["ticker"] != ticker or bundle["status"] != "published":
            raise ValueError("PINNED_ARTIFACT_MISSING")
        if bundle["research_lane"] == "global_research":
            return self.research(bundle, lineage)
        if bundle["research_lane"] == "document2":
            return self.expectations(bundle, lineage)
        raise ValueError("unsupported page research lane")

    @staticmethod
    def run(bundle: dict[str, Any]) -> dict[str, Any]:
        return {
            "run_id": bundle["run_id"],
            "ticker": bundle["ticker"],
            "run_status": "PUBLISHED",
            "publication_status": "PUBLISHED",
            "publication_state": bundle.get("publication_state", "COMPLETE"),
            "created_at": timestamp(bundle["created_at"]),
            "published_at": available(timestamp(bundle["published_at"])),
            "is_active": False,
            "quality_annotations": ["PARTIAL"]
            if bundle.get("publication_state") == "PARTIAL"
            else [],
        }

    @staticmethod
    def document(ref: dict[str, Any], *, schema: str) -> dict[str, Any]:
        return {
            "run_id": ref["run_id"],
            "artifact_id": ref["artifact_id"],
            "content_sha256": ref["sha256"],
            "schema_version": schema,
            "published_at": timestamp(ref["published_at"]),
        }

    def research(self, bundle: dict[str, Any], lineage: str) -> list[dict[str, Any]]:
        run_id, ticker = bundle["run_id"], bundle["ticker"]
        document, _ = self.source.body(run_id, bundle["handoff"]["document_artifact_id"])
        reference = self.document(document, schema=bundle["workflow_version"])
        sections, records = [], []
        files = []
        for section in ("C1", "C3", "C5"):
            artifact = bundle["reports"].get(section.lower()) or bundle["reports"].get(section)
            if artifact is None:
                raise ValueError("published research missing required section")
            _, text = self.source.body(run_id, artifact["artifact_id"])
            content = self.store.put_content(ticker, text, "text/markdown")
            sections.append({"section": section, "content": resource(content)})
            files.append(
                {
                    "section": section,
                    "entry": section + ".md",
                    "state": "INCLUDED",
                    "artifact_id": artifact["artifact_id"],
                    "sha256": content["sha256"],
                    "size_bytes": content["size_bytes"],
                    "reason": None,
                }
            )
            records.extend(self.citations(bundle, content["content_id"], text))
            records.append(
                {
                    "kind": "research_section",
                    "ticker": ticker,
                    "id": run_id + ":" + section,
                    "parent": run_id,
                    "data": content,
                }
            )
        future = bundle["future_nodes"]
        content = self.store.put_content(ticker, encode(future), "application/json")
        sections.append({"section": "FUTURE_NODES", "content": resource(content)})
        files.append(
            {
                "section": "FUTURE_NODES",
                "entry": "future_nodes.json",
                "state": "INCLUDED",
                "artifact_id": None,
                "sha256": content["sha256"],
                "size_bytes": content["size_bytes"],
                "reason": None,
            }
        )
        records.append(
            {
                "kind": "research_download",
                "ticker": ticker,
                "id": run_id,
                "data": validate(
                    "ResearchDownloadManifest",
                    {
                        "contract_version": VERSION,
                        "ticker": ticker,
                        "run_id": run_id,
                        "files": files,
                    },
                ),
            }
        )
        for ordinal, node in enumerate(future):
            identity = hashlib.sha256(encode([run_id, ordinal, node]).encode()).hexdigest()
            value = {
                **FutureNode.model_validate(node).model_dump(),
                "item_key": identity,
                "ordinal": ordinal,
            }
            validate("FutureNodeRow", value)
            records.append(
                {
                    "kind": "future_node",
                    "ticker": ticker,
                    "id": identity,
                    "parent": run_id,
                    "sort": f"{999999999 - ordinal:09d}",
                    "data": value,
                }
            )
        summary = {
            "run": self.run(bundle),
            "document": resource(reference),
            "global_research_status": "PUBLISHED",
            "updated_at": missing(),
            "sections": sections,
        }
        validate("ResearchSummary", summary)
        records.extend(
            [
                {"kind": "research", "ticker": ticker, "id": run_id, "data": summary},
                {
                    "kind": "research_run",
                    "ticker": ticker,
                    "id": run_id,
                    "data": summary["run"],
                    "sort": timestamp(bundle["published_at"]),
                },
                {"kind": "document_ref", "ticker": ticker, "id": run_id, "data": reference},
                {
                    "kind": "artifact_lineage",
                    "ticker": ticker,
                    "id": run_id,
                    "data": {"lineage": lineage},
                },
            ]
        )
        return records

    @staticmethod
    def citations(bundle: dict[str, Any], content_id: str, text: str) -> list[dict[str, Any]]:
        import re

        entries = (bundle.get("citation_manifest") or {}).get("entries", [])
        by_alias = {item["alias"]: item for item in entries}
        aliases = list(dict.fromkeys(re.findall(r"【cite:([^】]+)】", text)))
        records = []
        for ordinal, alias in enumerate(aliases):
            entry = by_alias.get(alias)
            identity = hashlib.sha256(
                encode([bundle["run_id"], content_id, alias]).encode()
            ).hexdigest()
            value = {
                "citation_key": identity,
                "alias": alias,
                "status": "RESOLVED" if entry and entry["resolved"] else "UNRESOLVED",
                "origin_run_id": bundle["run_id"],
                "origin_attempt_id": entry.get("attempt_id") if entry else None,
                "source_id": entry.get("source_id") if entry else None,
                "url": entry.get("url") if entry else None,
                "title": entry.get("title") if entry else None,
                "warning": None if entry and entry["resolved"] else "UNRESOLVED_REFERENCE",
            }
            records.append(
                {
                    "kind": "citation",
                    "ticker": bundle["ticker"],
                    "id": identity,
                    "parent": content_id,
                    "sort": f"{999999999 - ordinal:09d}",
                    "data": validate("Citation", value),
                }
            )
        return records

    def expectations(self, bundle: dict[str, Any], lineage: str) -> list[dict[str, Any]]:
        run_id, ticker = bundle["run_id"], bundle["ticker"]
        document, text = self.source.body(run_id, bundle["handoff"]["document2_artifact_id"])
        native = Document2Document.model_validate_json(text).model_dump(mode="json")
        reference = self.document(document, schema=native["schema_version"])
        shells = {shell["shell_id"]: shell for shell in native["shells"]}
        outcomes = native["shell_outcomes"] or [
            {"shell_id": identity, "status": "completed"} for identity in shells
        ]
        records, ids = [], []
        for ordinal, outcome in enumerate(outcomes):
            identity = outcome["shell_id"]
            ids.append(identity)
            shell = shells.get(identity)
            seed = shell or outcome.get("seed") or {"core_question": "", "boundary_rule": ""}
            content = (
                self.store.put_content(ticker, encode(shell), "application/json") if shell else None
            )
            summary = {
                "shell_id": identity,
                "ordinal": ordinal,
                "core_question": seed["core_question"],
                "boundary_rule": seed["boundary_rule"],
                "status": "COMPLETED" if shell else "FAILED",
                "unit_count": available(len(shell["units"]))
                if shell
                else missing("NOT_PRODUCED", "NOT_PRODUCED"),
                "failed_stage": None if shell else outcome["failed_stage"],
                "failure_kind": None if shell else outcome["failure_kind"],
                "failure": None,
                "content": resource(content),
            }
            if not shell:
                summary["failure"] = {
                    "code": outcome["error_code"] or "SHELL_FAILED",
                    "message": "Shell publication failed",
                    "retryable": False,
                    "request_id": run_id,
                    "fields": [],
                    "content_id": None,
                }
            validate("ShellSummary", summary)
            records.append(
                {
                    "kind": "shell",
                    "ticker": ticker,
                    "id": run_id + ":" + identity,
                    "parent": run_id,
                    "sort": f"{999999999 - ordinal:09d}",
                    "data": summary,
                }
            )
            for index, unit in enumerate(shell["units"] if shell else []):
                validate("ExpectationUnit", unit)
                records.append(
                    {
                        "kind": "unit",
                        "ticker": ticker,
                        "id": run_id + ":" + identity + ":" + unit["expectation_id"],
                        "parent": run_id + ":" + identity,
                        "sort": f"{999999999 - index:09d}",
                        "data": unit,
                    }
                )
        records.extend(
            [
                {
                    "kind": "expectations_index",
                    "ticker": ticker,
                    "id": run_id,
                    "data": {
                        "run": self.run(bundle),
                        "runtime_activation_id": None,
                        "document": reference,
                        "citation_status": bundle["citation_status"],
                        "default_shell_id": ids[0] if ids else None,
                        "publication_state": bundle["publication_state"],
                    },
                },
                {
                    "kind": "expectations_run",
                    "ticker": ticker,
                    "id": run_id,
                    "data": self.run(bundle),
                    "sort": timestamp(bundle["published_at"]),
                },
                {"kind": "document_ref", "ticker": ticker, "id": run_id, "data": reference},
                {
                    "kind": "artifact_lineage",
                    "ticker": ticker,
                    "id": run_id,
                    "data": {"lineage": lineage},
                },
            ]
        )
        return records
