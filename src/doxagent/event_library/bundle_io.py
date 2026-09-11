"""Event-per-file Canonical Revision Bundle loader."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Hashable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from doxagent.event_library.contracts import (
    CanonicalAssertionState,
    CanonicalEventRevision,
    CanonicalEventType,
    CanonicalObjectStatus,
    CanonicalRevisionBundle,
    CanonicalRevisionBundleManifest,
    DateResolutionLedgerEntry,
    DateResolutionStatus,
    DateSemanticRole,
    OccurrenceDateCandidate,
    OccurrenceTimePrecision,
    ReferenceViewBasis,
    ReferenceViewDecisionLedgerEntry,
)
from doxagent.event_library.o2_wire import (
    O2DateLedgerWire,
    O2EventRevisionWire,
    O2FactRevisionWire,
)
from doxagent.event_library.reference_review import (
    occurrence_start,
    occurrence_time_matches_precision,
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
    raw_bundle_hash: str | None = None
    normalization_actions: list[dict[str, Any]] = field(default_factory=list)
    rejected_records: list[dict[str, Any]] = field(default_factory=list)
    recovered_delta_ids: list[str] = field(default_factory=list)


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
        """Strictly identify a Bundle, then recover its O2 records field by field."""

        root = Path(path).resolve()
        manifest_contract = CanonicalRevisionBundleManifest.model_validate_json(
            (root / "manifest.json").read_text(encoding="utf-8")
        )
        manifest = manifest_contract.model_dump(mode="json", exclude={"event_revisions"})
        events: list[dict[str, Any]] = []
        issues: list[BundleLoadIssue] = []
        invalid_paths: list[str] = []
        invalid_delta_ids: set[str] = set()
        recovered_delta_ids: set[str] = set()
        actions: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        used_event_ids: set[str] = set()
        used_fact_ids: set[str] = set()
        next_temp_event = 1
        next_temp_fact = 1

        def next_event_id() -> str:
            nonlocal next_temp_event
            while f"T{next_temp_event}" in used_event_ids:
                next_temp_event += 1
            value = f"T{next_temp_event}"
            next_temp_event += 1
            return value

        def next_fact_id() -> str:
            nonlocal next_temp_fact
            while f"TF{next_temp_fact}" in used_fact_ids:
                next_temp_fact += 1
            value = f"TF{next_temp_fact}"
            next_temp_fact += 1
            return value

        for relative in manifest_contract.event_revisions:
            candidate = (root / relative).resolve()
            if root not in candidate.parents:
                raise ValueError("Bundle event path escapes the Bundle root")
            raw = ""
            try:
                raw = candidate.read_text(encoding="utf-8")
                parsed = json.loads(raw)
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
                rejected.append(
                    {"path": relative, "record_type": "event", "reason": type(exc).__name__}
                )
                continue
            if not isinstance(parsed, dict):
                invalid_paths.append(relative)
                rejected.append({"path": relative, "record_type": "event", "reason": "not_object"})
                issues.append(
                    BundleLoadIssue(
                        code="EVENT_FILE_INVALID",
                        message=f"{relative}: event payload is not an object",
                        item_id=relative,
                    )
                )
                continue
            original_id = str(parsed.get("event_id") or "").strip()
            if re.fullmatch(r"E[1-9]\d*", original_id) and original_id in used_event_ids:
                discovered = sorted(set(re.findall(r"\bD[1-9]\d*\b", raw)))
                invalid_delta_ids.update(discovered)
                rejected.append(
                    {
                        "path": relative,
                        "record_type": "event",
                        "record_id": original_id,
                        "reason": "duplicate_stable_event_id",
                    }
                )
                issues.append(
                    BundleLoadIssue(
                        code="DUPLICATE_STABLE_EVENT_REJECTED",
                        message=f"{relative}: retained first {original_id} revision",
                        item_id=original_id,
                    )
                )
                continue
            event_id = original_id
            if not re.fullmatch(r"(?:E|T)[1-9]\d*", event_id) or event_id in used_event_ids:
                event_id = next_event_id()
                actions.append(
                    {
                        "code": "EVENT_ID_NORMALIZED",
                        "path": relative,
                        "from": original_id or None,
                        "to": event_id,
                    }
                )
            try:
                payload = RevisionBundleIO._normalize_event_wire(
                    parsed,
                    event_id=event_id,
                    ticker=manifest_contract.ticker,
                    used_fact_ids=used_fact_ids,
                    next_fact_id=next_fact_id,
                    actions=actions,
                    path=relative,
                )
                event = CanonicalEventRevision.model_validate(payload)
            except Exception as exc:
                invalid_paths.append(relative)
                discovered = sorted(set(re.findall(r"\bD[1-9]\d*\b", raw)))
                invalid_delta_ids.update(discovered)
                rejected.append(
                    {
                        "path": relative,
                        "record_type": "event",
                        "record_id": event_id,
                        "reason": type(exc).__name__,
                    }
                )
                issues.append(
                    BundleLoadIssue(
                        code="EVENT_FILE_UNREPRESENTABLE",
                        message=f"{relative}: {type(exc).__name__}",
                        item_id=event_id,
                    )
                )
                continue
            used_event_ids.add(event.event_id)
            recovered_delta_ids.update(
                delta_id for fact in event.facts for delta_id in fact.consumes_delta_ids
            )
            events.append(event.model_dump(mode="json"))
        from doxagent.codex_runtime.recovery import ingest_model, json_value

        from .contracts import (
            EventRetirement,
            ReferenceReviewDecision,
            ResidualDeltaResolution,
        )

        def rows(
            filename: str,
            model: type[BaseModel],
            key: Callable[[dict[str, Any]], Hashable],
        ) -> list[dict[str, Any]]:
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
            accepted: dict[Hashable, dict[str, Any]] = {}
            conflicted: set[Hashable] = set()
            for index, chunk in enumerate(chunks):
                if not chunk.strip():
                    continue
                try:
                    raw = json_value(chunk)
                    if model is ResidualDeltaResolution and isinstance(raw, dict):
                        if "resolution" not in raw and "disposition" in raw:
                            raw["resolution"] = raw.pop("disposition")
                    if model is DateResolutionLedgerEntry and isinstance(raw, dict):
                        raw = RevisionBundleIO._normalize_date_ledger_wire(
                            raw,
                            actions=actions,
                            path=filename,
                            line=index + 1,
                        )
                    if model is ReferenceViewDecisionLedgerEntry and isinstance(raw, dict):
                        raw = RevisionBundleIO._normalize_reference_ledger_wire(
                            raw,
                            actions=actions,
                            path=filename,
                            line=index + 1,
                        )
                    item = ingest_model(model, raw).model_dump(mode="json")
                    identity = key(item)
                    if identity in accepted and accepted[identity] != item:
                        conflicted.add(identity)
                        raise ValueError("conflicting duplicate record")
                    accepted[identity] = item
                except (ValueError, TypeError) as exc:
                    issues.append(
                        BundleLoadIssue(
                            code="ROW_QUARANTINED",
                            message=f"{filename}:{index + 1}: {type(exc).__name__}",
                            item_id=filename,
                        )
                    )
                    rejected.append(
                        {
                            "path": filename,
                            "line": index + 1,
                            "record_type": "sidecar",
                            "reason": type(exc).__name__,
                        }
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
            raw_bundle_hash=RevisionBundleIO._directory_hash(root),
            normalization_actions=actions,
            rejected_records=rejected,
            recovered_delta_ids=sorted(recovered_delta_ids),
        )

    @staticmethod
    def _normalize_event_wire(
        payload: dict[str, Any],
        *,
        event_id: str,
        ticker: str,
        used_fact_ids: set[str],
        next_fact_id: Any,
        actions: list[dict[str, Any]],
        path: str,
    ) -> dict[str, Any]:
        wire = O2EventRevisionWire.model_validate(payload)
        if RevisionBundleIO._text(wire.ticker).upper() != ticker:
            actions.append({"code": "EVENT_TICKER_NORMALIZED", "path": path, "to": ticker})
        raw_facts = wire.facts if isinstance(wire.facts, list) else []
        if not isinstance(wire.facts, list):
            actions.append({"code": "FACTS_CONTAINER_NORMALIZED", "path": path})
        facts: list[dict[str, Any]] = []
        local_fact_ids: set[str] = set()
        for index, raw_fact in enumerate(raw_facts):
            if not isinstance(raw_fact, dict):
                actions.append({"code": "FACT_ROW_SKIPPED", "path": path, "index": index})
                continue
            fact_wire = O2FactRevisionWire.model_validate(raw_fact)
            proposition = RevisionBundleIO._text(fact_wire.proposition)
            if not proposition:
                actions.append(
                    {"code": "FACT_WITHOUT_PROPOSITION_SKIPPED", "path": path, "index": index}
                )
                continue
            fact_id = RevisionBundleIO._text(fact_wire.fact_id)
            if (
                not re.fullmatch(r"(?:F|TF)[1-9]\d*", fact_id)
                or fact_id in used_fact_ids
                or fact_id in local_fact_ids
            ):
                replacement = next_fact_id()
                actions.append(
                    {
                        "code": "FACT_ID_NORMALIZED",
                        "path": path,
                        "from": fact_id or None,
                        "to": replacement,
                    }
                )
                fact_id = replacement
            assertion = RevisionBundleIO._enum_value(
                fact_wire.assertion_state,
                {item.value for item in CanonicalAssertionState},
                CanonicalAssertionState.UNKNOWN.value,
            )
            if assertion != fact_wire.assertion_state:
                actions.append(
                    {
                        "code": "FACT_ASSERTION_STATE_NORMALIZED",
                        "path": path,
                        "index": index,
                        "to": assertion,
                    }
                )
            occurred = RevisionBundleIO._text(fact_wire.fact_occurred_at) or None
            precision: str | None = RevisionBundleIO._enum_value(
                fact_wire.fact_occurrence_time_precision,
                {item.value for item in OccurrenceTimePrecision},
                OccurrenceTimePrecision.UNKNOWN.value,
            )
            if occurred is None:
                precision = None
            consumes = RevisionBundleIO._unique_ids(fact_wire.consumes_delta_ids, r"D[1-9]\d*")
            if not isinstance(fact_wire.consumes_delta_ids, list) or len(consumes) != len(
                fact_wire.consumes_delta_ids
            ):
                actions.append(
                    {"code": "FACT_CONSUMES_NORMALIZED", "path": path, "index": index}
                )
            facts.append(
                {
                    "fact_id": fact_id,
                    "proposition": proposition,
                    "assertion_state": assertion,
                    "subject_time": (
                        None
                        if fact_wire.subject_time is None
                        else RevisionBundleIO._text(fact_wire.subject_time)
                    ),
                    "fact_occurred_at": occurred,
                    "fact_occurrence_time_precision": precision,
                    "consumes_delta_ids": consumes,
                }
            )
            local_fact_ids.add(fact_id)
            used_fact_ids.add(fact_id)
        title = RevisionBundleIO._text(wire.title)
        summary = RevisionBundleIO._text(wire.canonical_summary)
        known = RevisionBundleIO._text(wire.known_event_summary)
        fallback = title or summary or (facts[0]["proposition"] if facts else "")
        if not fallback:
            raise ValueError("Event has no recoverable title, summary, or proposition")
        title = title or fallback
        summary = summary or title
        known = known or summary
        if not RevisionBundleIO._text(wire.title):
            actions.append({"code": "EVENT_TITLE_DEFAULTED", "path": path})
        if not RevisionBundleIO._text(wire.canonical_summary):
            actions.append({"code": "EVENT_SUMMARY_DEFAULTED", "path": path})
        if not RevisionBundleIO._text(wire.known_event_summary):
            actions.append({"code": "EVENT_KNOWN_SUMMARY_DEFAULTED", "path": path})
        if not facts:
            raise ValueError("Event has no recoverable Fact proposition")
        occurred_at: str | None = RevisionBundleIO._text(wire.occurred_at) or None
        precision = RevisionBundleIO._enum_value(
            wire.occurrence_time_precision,
            {item.value for item in OccurrenceTimePrecision},
            OccurrenceTimePrecision.UNKNOWN.value,
        )
        if precision != wire.occurrence_time_precision:
            actions.append(
                {"code": "EVENT_OCCURRENCE_PRECISION_NORMALIZED", "path": path, "to": precision}
            )
        if occurred_at is None:
            occurred_at, precision, source_fact_id = RevisionBundleIO._event_time_from_facts(facts)
            if source_fact_id is None:
                actions.append({"code": "EVENT_OCCURRENCE_LEFT_NULL", "path": path})
            else:
                actions.append(
                    {
                        "code": "EVENT_OCCURRENCE_FACT_FALLBACK",
                        "path": path,
                        "fact_id": source_fact_id,
                        "to": occurred_at,
                        "precision": precision,
                    }
                )
        elif occurred_at == "UNKNOWN":
            precision = OccurrenceTimePrecision.UNKNOWN.value
        event_type = RevisionBundleIO._enum_value(
            wire.event_type,
            {item.value for item in CanonicalEventType},
            CanonicalEventType.OTHER_CORPORATE_EVENT.value,
        )
        if event_type != wire.event_type:
            actions.append(
                {"code": "EVENT_TYPE_NORMALIZED", "path": path, "to": event_type}
            )
        if not isinstance(wire.is_important, bool):
            actions.append({"code": "EVENT_IMPORTANCE_DEFAULTED", "path": path, "to": False})
        if not isinstance(wire.include_in_reference_view, bool):
            actions.append({"code": "EVENT_REFERENCE_DEFAULTED", "path": path, "to": False})
        if event_id.startswith("T") and wire.price_analysis is not None:
            actions.append({"code": "PRICE_ANALYSIS_CLEARED", "path": path})
        status = RevisionBundleIO._enum_value(
            wire.status,
            {item.value for item in CanonicalObjectStatus},
            CanonicalObjectStatus.ACTIVE.value,
        )
        if status != wire.status:
            actions.append({"code": "EVENT_STATUS_NORMALIZED", "path": path, "to": status})
        related = RevisionBundleIO._unique_ids(wire.related_event_ids, r"(?:E|T)[1-9]\d*")
        derived = RevisionBundleIO._unique_ids(wire.derived_from_event_ids, r"(?:E|T)[1-9]\d*")
        related = [item for item in related if item != event_id]
        derived = [item for item in derived if item != event_id]
        supersedes = RevisionBundleIO._text(wire.supersedes_event_id) or None
        if supersedes is not None and (
            not re.fullmatch(r"(?:E|T)[1-9]\d*", supersedes) or supersedes == event_id
        ):
            supersedes = None
        return {
            "event_id": event_id,
            "ticker": ticker,
            "title": title,
            "event_type": event_type,
            "occurred_at": occurred_at,
            "occurrence_time_precision": precision,
            "canonical_summary": summary,
            "known_event_summary": known,
            "is_important": wire.is_important if isinstance(wire.is_important, bool) else False,
            "include_in_reference_view": (
                wire.include_in_reference_view
                if isinstance(wire.include_in_reference_view, bool)
                else False
            ),
            "facts": facts,
            "price_analysis": None if event_id.startswith("T") else wire.price_analysis,
            "related_event_ids": related,
            "supersedes_event_id": supersedes,
            "derived_from_event_ids": derived,
            "status": status,
        }

    @staticmethod
    def _event_time_from_facts(
        facts: list[dict[str, Any]],
    ) -> tuple[str | None, str, str | None]:
        """Select a deterministic Event-time fallback without using subject_time."""

        candidates: list[tuple[bool, int, int, str, str, str]] = []
        exact_days: list[tuple[bool, int, int, str, str, str]] = []
        for index, fact in enumerate(facts):
            value = RevisionBundleIO._text(fact.get("fact_occurred_at"))
            if not value or value in {"SAME", "UNKNOWN"}:
                continue
            is_exact_day = occurrence_time_matches_precision(
                value, OccurrenceTimePrecision.DAY
            )
            raw_precision = RevisionBundleIO._text(
                fact.get("fact_occurrence_time_precision")
            )
            precision = RevisionBundleIO._inferred_occurrence_precision(value, raw_precision)
            start = occurrence_start(value, precision)
            candidate = (
                start is None,
                0 if start is None else start.toordinal(),
                index,
                value,
                precision,
                str(fact["fact_id"]),
            )
            candidates.append(candidate)
            if is_exact_day:
                exact_days.append(candidate)

        pool = exact_days or candidates
        if not pool:
            return None, OccurrenceTimePrecision.UNKNOWN.value, None
        selected = min(pool, key=lambda item: (item[0], item[1], item[2]))
        return selected[3], selected[4], selected[5]

    @staticmethod
    def _inferred_occurrence_precision(value: str, fallback: str) -> str:
        for precision in (
            OccurrenceTimePrecision.DAY,
            OccurrenceTimePrecision.MONTH,
            OccurrenceTimePrecision.QUARTER,
            OccurrenceTimePrecision.YEAR,
            OccurrenceTimePrecision.INTERVAL,
            OccurrenceTimePrecision.TIMESTAMP,
        ):
            if occurrence_time_matches_precision(value, precision):
                return precision.value
        if fallback in {item.value for item in OccurrenceTimePrecision}:
            return fallback
        return OccurrenceTimePrecision.UNKNOWN.value

    @staticmethod
    def _text(value: Any) -> str:
        return value.strip() if isinstance(value, str) else ""

    @staticmethod
    def _normalize_date_ledger_wire(
        payload: dict[str, Any],
        *,
        actions: list[dict[str, Any]],
        path: str,
        line: int,
    ) -> dict[str, Any]:
        wire = O2DateLedgerWire.model_validate(payload)
        role = RevisionBundleIO._enum_value(
            wire.semantic_role,
            {item.value for item in DateSemanticRole},
            "",
        )
        if not role:
            raise ValueError("date ledger semantic_role is not representable")
        status = RevisionBundleIO._enum_value(
            wire.status,
            {item.value for item in DateResolutionStatus},
            DateResolutionStatus.UNRESOLVED.value,
        )
        selected = RevisionBundleIO._text(wire.selected_date) or None
        if selected is not None:
            try:
                from datetime import date

                date.fromisoformat(selected)
            except ValueError:
                selected = None
                status = DateResolutionStatus.UNRESOLVED.value
        if status == DateResolutionStatus.RESOLVED.value and selected is None:
            status = DateResolutionStatus.UNRESOLVED.value
            actions.append(
                {
                    "code": "DATE_STATUS_NORMALIZED",
                    "path": path,
                    "line": line,
                    "to": status,
                }
            )
        if (
            status == DateResolutionStatus.GENUINELY_PERIOD_WIDE.value
            and role != DateSemanticRole.EVENT_OCCURRENCE.value
        ):
            status = DateResolutionStatus.UNRESOLVED.value
            actions.append(
                {
                    "code": "DATE_STATUS_NORMALIZED",
                    "path": path,
                    "line": line,
                    "to": status,
                }
            )
        event_id = RevisionBundleIO._text(wire.event_id) or None
        fact_id = RevisionBundleIO._text(wire.fact_id) or None
        if event_id is not None and not re.fullmatch(r"(?:E|T)[1-9]\d*", event_id):
            event_id = None
        if fact_id is not None and not re.fullmatch(r"(?:F|TF)[1-9]\d*", fact_id):
            fact_id = None
        if role == DateSemanticRole.FACT_OCCURRENCE.value and fact_id is None:
            raise ValueError("Fact occurrence ledger row has no representable fact_id")
        candidates = []
        if isinstance(wire.candidates, list):
            allowed = {
                "candidate_date",
                "source_kind",
                "source_id",
                "source_message_id",
                "runtime_package_id",
                "evidence",
            }
            for candidate in wire.candidates:
                if not isinstance(candidate, dict):
                    continue
                try:
                    candidates.append(
                        OccurrenceDateCandidate.model_validate(
                            {key: value for key, value in candidate.items() if key in allowed}
                        ).model_dump(mode="json")
                    )
                except ValueError:
                    actions.append(
                        {
                            "code": "DATE_CANDIDATE_SKIPPED",
                            "path": path,
                            "line": line,
                        }
                    )
        delta_id = RevisionBundleIO._text(wire.delta_id) or None
        if delta_id is not None and not re.fullmatch(r"D[1-9]\d*", delta_id):
            delta_id = None
        precision = RevisionBundleIO._enum_value(
            wire.selected_precision,
            {item.value for item in OccurrenceTimePrecision},
            OccurrenceTimePrecision.UNKNOWN.value,
        )
        return {
            "delta_id": delta_id,
            "runtime_atomic_id": RevisionBundleIO._text(wire.runtime_atomic_id) or None,
            "runtime_package_id": RevisionBundleIO._text(wire.runtime_package_id) or None,
            "source_message_id": RevisionBundleIO._text(wire.source_message_id) or None,
            "candidates": candidates,
            "selected_date": selected,
            "selected_precision": precision,
            "semantic_role": role,
            "status": status,
            "event_id": event_id,
            "fact_id": fact_id,
            "subject_time": RevisionBundleIO._text(wire.subject_time) or None,
            "note": RevisionBundleIO._text(wire.note) or None,
        }

    @staticmethod
    def _normalize_reference_ledger_wire(
        payload: dict[str, Any],
        *,
        actions: list[dict[str, Any]],
        path: str,
        line: int,
    ) -> dict[str, Any]:
        normalized = dict(payload)
        basis_value = RevisionBundleIO._enum_value(
            payload.get("reference_view_basis"),
            {item.value for item in ReferenceViewBasis},
            "",
        )
        if basis_value:
            basis = ReferenceViewBasis(basis_value)
            if normalized.get("include_in_reference_view") != basis.includes:
                normalized["include_in_reference_view"] = basis.includes
                actions.append(
                    {
                        "code": "REFERENCE_LEDGER_FLAG_NORMALIZED",
                        "path": path,
                        "line": line,
                    }
                )
        return normalized

    @staticmethod
    def _enum_value(value: Any, allowed: set[str], default: str) -> str:
        candidate = RevisionBundleIO._text(value).upper()
        return candidate if candidate in allowed else default

    @staticmethod
    def _unique_ids(value: Any, pattern: str) -> list[str]:
        if not isinstance(value, list):
            return []
        output: list[str] = []
        for item in value:
            candidate = RevisionBundleIO._text(item)
            if re.fullmatch(pattern, candidate) and candidate not in output:
                output.append(candidate)
        return output

    @staticmethod
    def _directory_hash(root: Path) -> str:
        digest = hashlib.sha256()
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            digest.update(path.relative_to(root).as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
        return digest.hexdigest()

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
