"""Event-per-file Canonical Revision Bundle loader."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from doxagent.event_library.contracts import (
    CanonicalEventRevision,
    CanonicalRevisionBundle,
    CanonicalRevisionBundleManifest,
)


@dataclass(frozen=True)
class BundleLoadIssue:
    code: str
    message: str
    item_id: str | None = None


@dataclass(frozen=True)
class TolerantBundleLoadResult:
    bundle: CanonicalRevisionBundle
    issues: list[BundleLoadIssue] = field(default_factory=list)
    invalid_event_paths: list[str] = field(default_factory=list)
    invalid_delta_ids: list[str] = field(default_factory=list)


class RevisionBundleIO:
    @staticmethod
    def load(path: str | Path) -> CanonicalRevisionBundle:
        root = Path(path)
        manifest_contract = CanonicalRevisionBundleManifest.model_validate_json(
            (root / "manifest.json").read_text(encoding="utf-8")
        )
        manifest = manifest_contract.model_dump(mode="json", exclude={"event_revisions"})
        event_paths = manifest_contract.event_revisions
        events: list[dict[str, Any]] = []
        for relative in event_paths:
            candidate = (root / str(relative)).resolve()
            if root.resolve() not in candidate.parents:
                raise ValueError("Bundle event path escapes the Bundle root")
            events.append(
                RevisionBundleIO._without_retired_fact_entities(
                    json.loads(candidate.read_text(encoding="utf-8"))
                )
            )
        retirements_path = root / "retirements.json"
        residual_path = root / "residual_delta_resolutions.jsonl"
        retirements = (
            json.loads(retirements_path.read_text(encoding="utf-8"))
            if retirements_path.exists()
            else []
        )
        residuals = []
        if residual_path.exists():
            residuals = [
                json.loads(line)
                for line in residual_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        review_decisions = RevisionBundleIO._jsonl(root / "reference_review_decisions.jsonl")
        date_ledger = RevisionBundleIO._jsonl(root / "date_resolution_ledger.jsonl")
        reference_ledger = RevisionBundleIO._jsonl(root / "reference_view_decision_ledger.jsonl")
        return CanonicalRevisionBundle.model_validate(
            {
                **manifest,
                "event_revisions": events,
                "event_retirements": retirements,
                "residual_delta_resolutions": residuals,
                "reference_review_decisions": review_decisions,
                "date_resolution_ledger": date_ledger,
                "reference_view_decision_ledger": reference_ledger,
            }
        )

    @staticmethod
    def load_tolerant(path: str | Path) -> TolerantBundleLoadResult:
        """Keep a strict manifest while degrading malformed Event files locally."""

        root = Path(path).resolve()
        manifest_contract = CanonicalRevisionBundleManifest.model_validate_json(
            (root / "manifest.json").read_text(encoding="utf-8")
        )
        manifest = manifest_contract.model_dump(mode="json", exclude={"event_revisions"})
        events: list[dict[str, Any]] = []
        issues: list[BundleLoadIssue] = []
        invalid_paths: list[str] = []
        invalid_delta_ids: set[str] = set()
        for relative in manifest_contract.event_revisions:
            candidate = (root / relative).resolve()
            if root not in candidate.parents:
                raise ValueError("Bundle event path escapes the Bundle root")
            raw = ""
            try:
                raw = candidate.read_text(encoding="utf-8")
                payload = RevisionBundleIO._without_retired_fact_entities(json.loads(raw))
                event = CanonicalEventRevision.model_validate(payload)
            except Exception as exc:
                invalid_paths.append(relative)
                discovered = sorted(set(re.findall(r"\bD[1-9]\d*\b", raw)))
                invalid_delta_ids.update(discovered)
                issues.append(
                    BundleLoadIssue(
                        code="EVENT_FILE_INVALID",
                        message=f"{relative}: {type(exc).__name__}",
                        item_id=relative,
                    )
                )
                continue
            events.append(event.model_dump(mode="json"))
        from doxagent.codex_runtime.recovery import ingest_model, json_value

        from .contracts import (
            DateResolutionLedgerEntry,
            EventRetirement,
            ReferenceReviewDecision,
            ReferenceViewDecisionLedgerEntry,
            ResidualDeltaResolution,
        )

        def rows(filename, model, key):
            path = root / filename
            if not path.exists():
                return []
            text = path.read_text(encoding="utf-8")
            if filename.endswith(".jsonl"):
                chunks = text.splitlines()
            else:
                try:
                    parsed = json_value(text)
                    chunks = [json.dumps(v) for v in parsed] if isinstance(parsed, list) else [text]
                except ValueError:
                    chunks = [text]
            accepted = {}
            conflicted = set()
            for index, chunk in enumerate(chunks):
                if not chunk.strip():
                    continue
                try:
                    raw = json_value(chunk)
                    if model is ResidualDeltaResolution and isinstance(raw, dict):
                        if "resolution" not in raw and "disposition" in raw:
                            raw["resolution"] = raw.pop("disposition")
                    item = ingest_model(model, raw).model_dump(mode="json")
                    identity = key(item)
                    if identity in accepted and accepted[identity] != item:
                        conflicted.add(identity)
                        raise ValueError("conflicting duplicate record")
                    accepted[identity] = item
                except (ValueError, TypeError) as exc:
                    invalid_delta_ids.update(re.findall(r"\bD[1-9]\d*\b", chunk))
                    issues.append(
                        BundleLoadIssue(
                            code="ROW_QUARANTINED",
                            message=f"{filename}:{index + 1}: {type(exc).__name__}",
                            item_id=filename,
                        )
                    )
            return [item for key, item in accepted.items() if key not in conflicted]

        retirements = rows("retirements.json", EventRetirement, lambda r: r["event_id"])
        residuals = rows(
            "residual_delta_resolutions.jsonl", ResidualDeltaResolution, lambda r: r["delta_id"]
        )
        review_decisions = rows(
            "reference_review_decisions.jsonl", ReferenceReviewDecision, lambda r: r["event_id"]
        )
        date_ledger = rows(
            "date_resolution_ledger.jsonl",
            DateResolutionLedgerEntry,
            lambda r: (r["semantic_role"], r.get("event_id"), r.get("fact_id"), r.get("delta_id")),
        )
        reference_ledger = rows(
            "reference_view_decision_ledger.jsonl",
            ReferenceViewDecisionLedgerEntry,
            lambda r: r["event_id"],
        )
        event_ids = [e["event_id"] for e in events]
        duplicates = {key for key in event_ids if event_ids.count(key) > 1}
        if duplicates:
            issues.append(
                BundleLoadIssue(
                    code="DUPLICATE_EVENTS_QUARANTINED", message=str(sorted(duplicates))
                )
            )
            events = [e for e in events if e["event_id"] not in duplicates]
        bundle = CanonicalRevisionBundle.model_validate(
            {
                **manifest,
                "event_revisions": events,
                "event_retirements": retirements,
                "residual_delta_resolutions": residuals,
                "reference_review_decisions": review_decisions,
                "date_resolution_ledger": date_ledger,
                "reference_view_decision_ledger": reference_ledger,
            }
        )
        return TolerantBundleLoadResult(
            bundle=bundle,
            issues=issues,
            invalid_event_paths=invalid_paths,
            invalid_delta_ids=sorted(invalid_delta_ids),
        )

    @staticmethod
    def _without_retired_fact_entities(payload: Any) -> Any:
        """Normalize legacy Bundle Facts into the current Canonical contract."""

        if not isinstance(payload, dict):
            return payload
        normalized = dict(payload)
        facts = normalized.get("facts")
        if isinstance(facts, list):
            normalized["facts"] = [
                ({key: value for key, value in fact.items() if key != "entities"})
                if isinstance(fact, dict)
                else fact
                for fact in facts
            ]
        return normalized

    @staticmethod
    def _jsonl(path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    @staticmethod
    def _tolerant_residuals(path: Path) -> tuple[list[dict[str, Any]], int]:
        rows = RevisionBundleIO._jsonl(path)
        output: list[dict[str, Any]] = []
        normalized = 0
        allowed = {"delta_id", "resolution", "target_event_id", "target_fact_id"}
        for row in rows:
            payload = dict(row)
            if "resolution" not in payload and "disposition" in payload:
                payload["resolution"] = payload["disposition"]
                normalized += 1
            output.append({key: value for key, value in payload.items() if key in allowed})
        return output, normalized
