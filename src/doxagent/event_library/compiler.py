"""Published views and immutable O2 Frozen View materialization."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast
from zoneinfo import ZoneInfo

from doxagent.event_library.contracts import (
    CandidateMap,
    CanonicalEvent,
    CanonicalEventRevision,
    CanonicalRevisionBundleManifest,
    DateResolutionLedgerEntry,
    DeltaBatch,
    EventRetirement,
    FrozenViewManifest,
    ReferenceReviewCandidate,
    ReferenceReviewDecision,
    ReferenceViewDecisionLedgerEntry,
    ReferenceViewDeltaSnapshot,
    ResidualDeltaResolution,
    RuntimePackageDelta,
    SurveyDeltaCatalog,
    TimeReferenceRepairReason,
    TimeReferenceRepairWorkItem,
    WaveIndex,
)
from doxagent.event_library.reference_review import occurrence_anchor, occurrence_start
from doxagent.event_library.repository import EventLibraryRepository

REFERENCE_VIEW_CONTRACT_VERSION = "reference-view-md-v4"
KNOWN_EVENT_INDEX_CONTRACT_VERSION = "known-event-index-v2"
_EASTERN = ZoneInfo("America/New_York")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TRAILING_SUMMARY_BOILERPLATE = {"event", "occurrence"}


def _json_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n"


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _wire_cell(value: str) -> str:
    return " ".join(value.replace("|", r"\|").split())


def _display_occurrence_time(value: str) -> str:
    """Render precise zoned timestamps as an Eastern calendar date.

    A date or timezone-naive value is never shifted because the source contract
    does not establish which timezone it belongs to.
    """

    cleaned = value.strip()
    if _ISO_DATE.fullmatch(cleaned):
        return cleaned
    try:
        parsed = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
    except ValueError:
        return cleaned
    if parsed.tzinfo is None:
        return parsed.date().isoformat() if parsed.time().isoformat() == "00:00:00" else cleaned
    return parsed.astimezone(_EASTERN).date().isoformat()


def _summary_key(value: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.findall(r"[a-z0-9]+", normalized)


def _summary_duplicates_title(*, title: str, summary: str) -> bool:
    title_tokens = _summary_key(title)
    summary_tokens = _summary_key(summary)
    if summary_tokens == title_tokens:
        return True
    while summary_tokens and summary_tokens[-1] in _TRAILING_SUMMARY_BOILERPLATE:
        summary_tokens.pop()
    return summary_tokens == title_tokens


def _event_order(events: list[CanonicalEvent]) -> list[CanonicalEvent]:
    def order(item: CanonicalEvent) -> tuple[bool, int, int]:
        anchor = occurrence_anchor(item.occurred_at, item.occurrence_time_precision)
        return (
            anchor is None,
            -(anchor.toordinal() if anchor is not None else 0),
            int(item.event_id[1:]),
        )

    return sorted(events, key=order)


def _described_schema(model: Any, *, title: str, description: str) -> dict[str, Any]:
    schema: dict[str, Any] = model.model_json_schema()
    schema["title"] = title
    schema["description"] = description
    return schema


def _new_event_revision_schema() -> dict[str, Any]:
    schema = _described_schema(
        CanonicalEventRevision,
        title="Canonical Event Revision",
        description=(
            "A complete new or revised Event under maintenance-v3, including exact "
            "Fact occurrence dates, full Fact membership and Delta consumption."
        ),
    )
    fact_schema = (schema.get("$defs") or {}).get("CanonicalFactRevision")
    if isinstance(fact_schema, dict):
        required = list(fact_schema.get("required") or [])
        for field in ("fact_occurred_at", "fact_occurrence_time_precision"):
            if field not in required:
                required.append(field)
        fact_schema["required"] = required
    return schema


def _new_bundle_manifest_schema() -> dict[str, Any]:
    schema = _described_schema(
        CanonicalRevisionBundleManifest,
        title="Revision Bundle Manifest",
        description="maintenance-v3 manifest identity and Event-file index.",
    )
    contract = (schema.get("properties") or {}).get("contract_version")
    if isinstance(contract, dict):
        contract.pop("enum", None)
        contract["const"] = "event-library-maintenance-v3"
        contract["default"] = "event-library-maintenance-v3"
    return schema


def _reference_fact_lines(event: CanonicalEvent) -> list[str]:
    """Render Fact occurrence and subject time without conflating either one."""

    lines = ["facts:", ""]
    for fact in event.facts:
        proposition = _wire_cell(fact.proposition)
        occurred = _wire_cell(fact.fact_occurred_at or "LEGACY_UNAVAILABLE")
        if fact.fact_occurred_at == "SAME":
            occurred = _wire_cell(event.occurred_at)
        subject = _wire_cell(fact.subject_time or "null")
        lines.append(f"- Fact occurred_at: {occurred}")
        lines.append(f"  Fact subject_time: {subject}")
        lines.append(f"  Proposition: {proposition}")
    return lines


def _fact_occurrence_display(event: CanonicalEvent, value: str | None) -> str:
    if value == "SAME":
        return event.occurred_at
    return value or "LEGACY_UNAVAILABLE"


class EventLibraryViewCompiler:
    def __init__(self, repository: EventLibraryRepository) -> None:
        self._repository = repository

    def known_event_index(self, ticker: str, version: int | None = None) -> str:
        events = self._repository.published_events(ticker, version)
        lines: list[str] = []
        for event in _event_order(events):
            cells = [
                event.event_id,
                _wire_cell(_display_occurrence_time(event.occurred_at)),
                _wire_cell(event.title),
            ]
            if not _summary_duplicates_title(title=event.title, summary=event.known_event_summary):
                cells.append(_wire_cell(event.known_event_summary))
            lines.append(" | ".join(cells))
        return "" if not lines else "\n".join(lines) + "\n"

    def reference_events(self, ticker: str, version: int | None = None) -> list[CanonicalEvent]:
        selected = self._repository.published_version(ticker) if version is None else version
        return [
            event
            for event in _event_order(self._repository.published_events(ticker, selected))
            if event.include_in_reference_view
        ]

    def reference_view(
        self, ticker: str, version: int | None = None, *, include_basis: bool = False
    ) -> str:
        return self._render_reference_events(
            ticker,
            self.reference_events(ticker, version),
            include_basis=include_basis,
        )

    def _render_reference_events(
        self,
        ticker: str,
        events: list[CanonicalEvent],
        *,
        include_basis: bool = False,
    ) -> str:
        sections = ["fields: event_id | event_time | precision | title", ""]
        for event in events:
            basis = (
                self._repository.latest_reference_view_basis(
                    ticker=ticker, event_id=event.event_id
                )
                if include_basis
                else None
            )
            sections.extend(
                [
                    " | ".join(
                        (
                            event.event_id,
                            _wire_cell(_display_occurrence_time(event.occurred_at)),
                            event.occurrence_time_precision.value,
                            _wire_cell(event.title),
                        )
                    ),
                    f"event_type: {_wire_cell(event.event_type)}",
                    f"canonical_summary: {_wire_cell(event.canonical_summary)}",
                    *([] if basis is None else [f"reference_view_basis: {_wire_cell(basis)}"]),
                    *_reference_fact_lines(event),
                    "",
                ]
            )
        return "\n".join(sections).rstrip() + "\n"

    def reference_view_delta(
        self,
        ticker: str,
        *,
        from_version: int,
        to_version: int,
        persist: bool = True,
    ) -> ReferenceViewDeltaSnapshot:
        if to_version <= from_version:
            raise ValueError("Reference View Delta requires an increasing version range")
        current_version = self._repository.published_version(ticker)
        if to_version > current_version:
            raise ValueError("Reference View Delta target is not Published")
        previous = (
            {}
            if from_version == 0
            else {
                event.event_id: event
                for event in self.reference_events(ticker, from_version)
            }
        )
        current = {
            event.event_id: event for event in self.reference_events(ticker, to_version)
        }
        changed = [
            event
            for event_id, event in current.items()
            if event_id not in previous
            or self._render_reference_events(ticker, [event])
            != self._render_reference_events(ticker, [previous[event_id]])
        ]
        snapshot = ReferenceViewDeltaSnapshot(
            ticker=ticker.upper(),
            from_library_version=from_version,
            to_library_version=to_version,
            reference_view_delta=self._render_reference_events(ticker, changed),
            removed_event_ids=sorted(set(previous) - set(current)),
        )
        if persist:
            self._repository.save_reference_view_delta(snapshot)
        return snapshot

    def event_detail(
        self, ticker: str, event_id: str, version: int | None = None
    ) -> CanonicalEvent | None:
        return self._repository.get_event(ticker, event_id, version)

    def time_reference_repair_worklist(
        self,
        ticker: str,
        *,
        as_of: datetime,
        version: int | None = None,
        affected_event_ids: set[str] | None = None,
    ) -> list[TimeReferenceRepairWorkItem]:
        """Deterministically scope V1->V2 repair without changing Published data."""

        output: list[TimeReferenceRepairWorkItem] = []
        broad = {"MONTH", "QUARTER", "YEAR", "INTERVAL", "UNKNOWN"}
        for event in self._repository.published_events(ticker, version):
            if affected_event_ids is not None and event.event_id not in affected_event_ids:
                continue
            reasons: set[TimeReferenceRepairReason] = set()
            fact_ids: set[str] = set()
            for fact in event.facts:
                if fact.fact_occurred_at is None:
                    reasons.add(TimeReferenceRepairReason.LEGACY_FACT_OCCURRENCE_MISSING)
                    fact_ids.add(fact.fact_id)
                if (
                    fact.fact_occurred_at == "SAME"
                    and event.occurrence_time_precision.value in broad
                ):
                    reasons.add(TimeReferenceRepairReason.BROAD_EVENT_FACT_SAME)
                    fact_ids.add(fact.fact_id)
            start = occurrence_start(event.occurred_at, event.occurrence_time_precision)
            if start is not None and start > as_of.astimezone(UTC).date():
                reasons.add(TimeReferenceRepairReason.FUTURE_OCCURRENCE_AFTER_AS_OF)
            if (
                self._repository.latest_reference_view_basis(
                    ticker=ticker, event_id=event.event_id
                )
                is None
            ):
                reasons.add(TimeReferenceRepairReason.REFERENCE_BASIS_MISSING)
            if event.supersedes_event_id is not None:
                reasons.add(TimeReferenceRepairReason.SUPERSESSION_CONTEXT)
            if reasons:
                output.append(
                    TimeReferenceRepairWorkItem(
                        event_id=event.event_id,
                        fact_ids=sorted(fact_ids, key=lambda item: int(item[1:])),
                        reasons=sorted(reasons, key=lambda item: item.value),
                    )
                )
        return output

    def materialize_frozen_view(
        self,
        *,
        run_root: str | Path,
        run_id: str,
        mode: Literal["INITIALIZE", "INCREMENTAL"],
        batches: list[DeltaBatch],
        as_of: datetime,
        reference_review_candidates: list[ReferenceReviewCandidate] | None = None,
        upstream_context_manifest: dict[str, Any] | None = None,
        upstream_d1_reports: dict[str, str] | None = None,
    ) -> tuple[Path, FrozenViewManifest]:
        if not batches:
            raise ValueError("at least one Delta batch is required")
        if upstream_context_manifest is not None and upstream_d1_reports is None:
            raise ValueError(
                "Published D1 C1/C3/C5 report bodies are required with upstream context"
            )
        ticker = batches[0].ticker
        base_version = batches[0].base_library_version
        if any(
            item.ticker != ticker or item.base_library_version != base_version for item in batches
        ):
            raise ValueError("Frozen View batches must share ticker and base_library_version")
        delta_ids = [item.delta_id for batch in batches for item in batch.items]
        if len(delta_ids) != len(set(delta_ids)):
            raise ValueError("combined Frozen View Delta IDs must be unique")
        events = self._repository.published_events(ticker, base_version)
        effective_packages = [package for batch in batches for package in batch.runtime_packages]
        if not effective_packages:
            for batch in batches:
                for hint in batch.runtime_hints:
                    members = [
                        item
                        for item in batch.items
                        if hint.runtime_hint_id in item.runtime_hint_ids
                    ]
                    if not members:
                        continue
                    effective_packages.append(
                        RuntimePackageDelta(
                            runtime_hint_id=hint.runtime_hint_id,
                            title=hint.title,
                            runtime_package_version=1,
                            member_delta_ids=[item.delta_id for item in members],
                            time_anchors=sorted(
                                {item.time for item in members if item.time is not None}
                            ),
                            subject_time_anchors=sorted(
                                {
                                    item.subject_time
                                    for item in members
                                    if item.subject_time is not None
                                }
                            ),
                            entity_anchors=sorted(
                                {entity for item in members for entity in item.entities}
                            ),
                        )
                    )
        identity_payload = {
            "run_id": run_id,
            "mode": mode,
            "ticker": ticker,
            "base_library_version": base_version,
            "as_of": as_of.isoformat(),
            # Batch disposition status changes after publication and must not alter
            # the identity of the already-frozen decision input for run recovery.
            "delta_batches": [
                batch.model_dump(mode="json", exclude={"status"}) for batch in batches
            ],
            "published_events": [event.model_dump(mode="json") for event in events],
            "reference_review_candidates": reference_review_candidates or [],
            "upstream_context_manifest": upstream_context_manifest,
            "upstream_d1_reports": upstream_d1_reports,
            "runtime_packages": [item.model_dump(mode="json") for item in effective_packages],
            "known_event_index_contract_version": KNOWN_EVENT_INDEX_CONTRACT_VERSION,
        }
        frozen_view_id = f"fv-{_hash(identity_payload)[:24]}"
        root = Path(run_root) / "context" / "event_library" / frozen_view_id
        if root.exists():
            manifest_path = root / "manifest.json"
            if not manifest_path.is_file():
                raise ValueError("existing Frozen View is incomplete")
            existing = FrozenViewManifest.model_validate_json(
                manifest_path.read_text(encoding="utf-8")
            )
            if existing.frozen_view_id != frozen_view_id or existing.run_id != run_id:
                raise ValueError("existing Frozen View identity mismatch")
            return root, existing
        (root / "events").mkdir(parents=True)
        (root / "delta").mkdir()
        (root / "review").mkdir()
        (root / "upstream").mkdir()
        (root / "schemas").mkdir()
        (root / "examples" / "bundle" / "events").mkdir(parents=True)
        (root / "known_event_index.md").write_text(
            self.known_event_index(ticker, base_version), encoding="utf-8"
        )
        for event in events:
            (root / "events" / f"{event.event_id}.json").write_text(
                _json_text(event.model_dump(mode="json")), encoding="utf-8"
            )
        pending_atomics = [
            {
                "delta_id": item.delta_id,
                "proposition": item.proposition,
                "raw_time": item.time,
                "subject_time": item.subject_time,
                "occurrence_date_candidates": [
                    candidate.model_dump(mode="json")
                    for candidate in item.occurrence_date_candidates
                ],
                **(
                    {"source_message_ids": item.source_message_ids}
                    if item.source_message_ids
                    else {}
                ),
                "assertion_state": item.assertion_state.value,
                "entities": item.entities,
                "runtime_hint_ids": item.runtime_hint_ids,
                "target_suggestion_ids": item.target_suggestion_ids,
            }
            for batch in batches
            for item in batch.items
        ]
        runtime_hints = [
            hint.model_dump(mode="json") for batch in batches for hint in batch.runtime_hints
        ]
        runtime_packages = [item.model_dump(mode="json") for item in effective_packages]
        (root / "delta" / "pending_atomics.json").write_text(
            _json_text(pending_atomics), encoding="utf-8"
        )
        (root / "delta" / "runtime_hints.json").write_text(
            _json_text(runtime_hints), encoding="utf-8"
        )
        (root / "delta" / "runtime_packages.json").write_text(
            _json_text(runtime_packages), encoding="utf-8"
        )
        package_lines = [
            " | ".join(
                (
                    package["runtime_hint_id"],
                    _wire_cell(str(package["title"])),
                    ",".join(package["member_delta_ids"]),
                    ",".join(package["time_anchors"]),
                    ",".join(package["subject_time_anchors"]),
                    ",".join(package["entity_anchors"]),
                )
            )
            for package in runtime_packages
        ]
        (root / "delta" / "package_index.md").write_text(
            "" if not package_lines else "\n".join(package_lines) + "\n",
            encoding="utf-8",
        )
        (root / "review" / "reference_review_candidates.json").write_text(
            _json_text(
                [item.model_dump(mode="json") for item in reference_review_candidates or []]
            ),
            encoding="utf-8",
        )
        upstream_path: str | None = None
        upstream_d1_manifest_path: str | None = None
        upstream_d1_paths: dict[str, str] = {}
        if upstream_context_manifest is not None:
            upstream_path = "upstream/o2_upstream_context_manifest.json"
            frozen_upstream = dict(upstream_context_manifest)
            if upstream_d1_reports is not None:
                if set(upstream_d1_reports) != {"c1", "c3", "c5"}:
                    raise ValueError("O2 Frozen View requires exactly D1 C1/C3/C5 report bodies")
                d1_root = root / "upstream" / "d1"
                d1_root.mkdir()
                artifact_rows: dict[str, dict[str, Any]] = {}
                for role in ("c1", "c3", "c5"):
                    content = upstream_d1_reports[role]
                    encoded = content.encode("utf-8")
                    relative = f"upstream/d1/{role}.md"
                    (root / relative).write_text(content, encoding="utf-8")
                    upstream_d1_paths[role] = relative
                    artifact_rows[role] = {
                        "relative_path": relative,
                        "sha256": hashlib.sha256(encoded).hexdigest(),
                        "size_bytes": len(encoded),
                        "source_artifact": dict(
                            (upstream_context_manifest.get("research_artifacts") or {}).get(role)
                            or {}
                        ),
                    }
                upstream_d1_manifest_path = "upstream/d1/artifact_manifest.json"
                (root / upstream_d1_manifest_path).write_text(
                    _json_text(
                        {
                            "d1_run_id": upstream_context_manifest.get("d1_run_id"),
                            "published_at": upstream_context_manifest.get("d1_published_at"),
                            "reports": artifact_rows,
                        }
                    ),
                    encoding="utf-8",
                )
                frozen_upstream["frozen_d1_artifacts"] = artifact_rows
            (root / upstream_path).write_text(_json_text(frozen_upstream), encoding="utf-8")

        schema_specs = [
            (
                "canonical_event_revision.schema.json",
                CanonicalEventRevision,
                "Canonical Event Revision",
                "A complete new or revised Event, including full Fact membership "
                "and Delta consumption.",
                "JSON",
                True,
            ),
            (
                "date_resolution_ledger.schema.json",
                DateResolutionLedgerEntry,
                "Date Resolution Ledger Entry",
                "Traceable Event/Fact occurrence or subject-time resolution record.",
                "JSONL",
                True,
            ),
            (
                "reference_view_decision_ledger.schema.json",
                ReferenceViewDecisionLedgerEntry,
                "Reference View Decision Ledger Entry",
                "Auditable importance and current-state Reference decision basis.",
                "JSONL",
                True,
            ),
            (
                "revision_bundle_manifest.schema.json",
                CanonicalRevisionBundleManifest,
                "Revision Bundle Manifest",
                "Manifest-only identity and Event-file index; retirements, residuals "
                "and review decisions use separate files.",
                "JSON",
                True,
            ),
            (
                "event_retirement.schema.json",
                EventRetirement,
                "Event Retirement",
                "Lifecycle redirect for a stable or same-Bundle temporary Event.",
                "JSON array items",
                False,
            ),
            (
                "residual_delta_resolution.schema.json",
                ResidualDeltaResolution,
                "Residual Delta Resolution",
                "One formal residual resolution. The wire field is resolution; "
                "disposition is legacy read compatibility only.",
                "JSONL",
                False,
            ),
            (
                "reference_review_decision.schema.json",
                ReferenceReviewDecision,
                "Reference Review Decision",
                "Model-selected flags plus deterministic frozen-clock review fields.",
                "JSONL",
                False,
            ),
            (
                "candidate_map.schema.json",
                CandidateMap,
                "Incremental Candidate Map",
                "High-recall Event Detail access map; it is navigation, not an "
                "occurrence decision.",
                "JSON",
                False,
            ),
            (
                "survey_delta_catalog.schema.json",
                SurveyDeltaCatalog,
                "Survey Delta Catalog",
                "Exactly-once assignment of each Survey D# to an occurrence or "
                "KEEP_PENDING_* navigation key.",
                "JSON",
                False,
            ),
            (
                "wave_index.schema.json",
                WaveIndex,
                "Initialization Wave Index",
                "Exactly-once wave accounting with draft paths and unresolved "
                "reconciliation questions.",
                "JSON",
                False,
            ),
        ]
        schema_index: list[dict[str, Any]] = []
        for filename, model, title, description, wire_format, required in schema_specs:
            schema = (
                _new_event_revision_schema()
                if model is CanonicalEventRevision
                else (
                    _new_bundle_manifest_schema()
                    if model is CanonicalRevisionBundleManifest
                    else _described_schema(model, title=title, description=description)
                )
            )
            (root / "schemas" / filename).write_text(_json_text(schema), encoding="utf-8")
            schema_index.append(
                {
                    "path": f"schemas/{filename}",
                    "format": wire_format,
                    "required": required,
                    "applies_to": title,
                }
            )
        # Compatibility path retained for old prompts, explicitly manifest-only.
        legacy_manifest_schema = _described_schema(
            CanonicalRevisionBundleManifest,
            title="Revision Bundle Manifest Only (Legacy Filename)",
            description=(
                "Compatibility alias for revision_bundle_manifest.schema.json. "
                "This is not a schema for the complete multi-file Revision Bundle."
            ),
        )
        (root / "schemas" / "revision_bundle.schema.json").write_text(
            _json_text(legacy_manifest_schema), encoding="utf-8"
        )
        (root / "schemas" / "schema_index.json").write_text(
            _json_text(
                {"contract_version": "event-library-schema-index-v1", "files": schema_index}
            ),
            encoding="utf-8",
        )
        self._write_minimal_bundle_example(root / "examples" / "bundle")
        manifest = FrozenViewManifest(
            frozen_view_id=frozen_view_id,
            run_id=run_id,
            mode=mode,
            ticker=ticker,
            as_of=as_of,
            base_library_version=base_version,
            delta_batch_ids=[item.batch_id for item in batches],
            published_event_count=len(events),
            pending_delta_count=len(pending_atomics),
            known_event_index_path="known_event_index.md",
            event_details_path="events",
            pending_atomics_path="delta/pending_atomics.json",
            runtime_hints_path="delta/runtime_hints.json",
            runtime_packages_path="delta/runtime_packages.json",
            package_index_path="delta/package_index.md",
            reference_review_candidates_path="review/reference_review_candidates.json",
            upstream_context_manifest_path=upstream_path,
            upstream_d1_artifact_manifest_path=upstream_d1_manifest_path,
            upstream_d1_report_paths=upstream_d1_paths,
            canonical_event_schema_path="schemas/canonical_event_revision.schema.json",
            revision_bundle_schema_path="schemas/revision_bundle_manifest.schema.json",
        )
        (root / "manifest.json").write_text(
            _json_text(manifest.model_dump(mode="json")), encoding="utf-8"
        )
        return root, manifest

    @staticmethod
    def _write_minimal_bundle_example(root: Path) -> None:
        """Write a generic wire example; it is documentation, never business input."""

        (root / "manifest.json").write_text(
            _json_text(
                {
                    "contract_version": "event-library-maintenance-v3",
                    "run_id": "example-run",
                    "ticker": "XYZ",
                    "base_library_version": 3,
                    "delta_batch_ids": ["example-delta-batch"],
                    "event_revisions": ["events/T1.json", "events/E7.json"],
                }
            ),
            encoding="utf-8",
        )
        event: dict[str, Any] = {
            "event_id": "T1",
            "ticker": "XYZ",
            "title": "XYZ announced a product milestone",
            "event_type": "PRODUCT_MILESTONE",
            "occurred_at": "2026-01-15",
            "occurrence_time_precision": "DAY",
            "status": "ACTIVE",
            "canonical_summary": "XYZ announced a product milestone.",
            "known_event_summary": "On 2026-01-15, XYZ announced a product milestone.",
            "is_important": True,
            "include_in_reference_view": True,
            "related_event_ids": ["E7"],
            "supersedes_event_id": None,
            "derived_from_event_ids": [],
            "facts": [
                {
                    "fact_id": "TF1",
                    "proposition": "XYZ announced the milestone.",
                    "assertion_state": "ACTUAL",
                    "subject_time": "SAME",
                    "fact_occurred_at": "SAME",
                    "fact_occurrence_time_precision": "DAY",
                    "consumes_delta_ids": ["D1"],
                }
            ],
            "price_analysis": None,
        }
        (root / "events" / "T1.json").write_text(_json_text(event), encoding="utf-8")
        existing = dict(event)
        existing.update(
            {
                "event_id": "E7",
                "related_event_ids": ["T1"],
                "facts": [{**event["facts"][0], "fact_id": "F9", "consumes_delta_ids": ["D2"]}],
            }
        )
        (root / "events" / "E7.json").write_text(_json_text(existing), encoding="utf-8")
        (root / "retirements.json").write_text(
            _json_text(
                [
                    {
                        "event_id": "E6",
                        "redirect_to_event_id": "E7",
                        "reason": "MERGED_DUPLICATE_OCCURRENCE",
                    }
                ]
            ),
            encoding="utf-8",
        )
        (root / "residual_delta_resolutions.jsonl").write_text(
            "\n".join(
                (
                    json.dumps(
                        {
                            "delta_id": "D3",
                            "resolution": "DUPLICATE_FACT",
                            "target_event_id": "E7",
                            "target_fact_id": "F9",
                        }
                    ),
                    json.dumps(
                        {
                            "delta_id": "D4",
                            "resolution": "KEEP_PENDING",
                            "target_event_id": None,
                            "target_fact_id": None,
                        }
                    ),
                )
            )
            + "\n",
            encoding="utf-8",
        )
        (root / "reference_review_decisions.jsonl").write_text(
            "\n".join(
                json.dumps(
                    {
                        "event_id": event_id,
                        "reviewed_at": "2026-01-20T00:00:00Z",
                        "review_mode": "IMPLICIT",
                        "candidate_reason": "PERIODIC_10D",
                        "changed": event_id == "T1",
                        "include_in_reference_view": True,
                        "is_important": True,
                        "reference_view_basis": "CURRENT_BASELINE",
                        "next_review_at": "2026-01-30T00:00:00Z",
                        "note": "Still useful.",
                    }
                )
                for event_id in ("T1", "E7")
            )
            + "\n",
            encoding="utf-8",
        )
        (root / "date_resolution_ledger.jsonl").write_text(
            "\n".join(
                json.dumps(row)
                for row in cast(
                    tuple[dict[str, Any], ...],
                    (
                    {
                        "delta_id": "D1",
                        "runtime_atomic_id": "runtime-example-1",
                        "runtime_package_id": None,
                        "source_message_id": "source-example-1",
                        "candidates": [],
                        "selected_date": "2026-01-15",
                        "selected_precision": "DAY",
                        "semantic_role": "EVENT_OCCURRENCE",
                        "status": "RESOLVED",
                        "event_id": "T1",
                        "fact_id": None,
                        "subject_time": None,
                        "note": None,
                    },
                    {
                        "delta_id": "D1",
                        "runtime_atomic_id": "runtime-example-1",
                        "runtime_package_id": None,
                        "source_message_id": "source-example-1",
                        "candidates": [],
                        "selected_date": "2026-01-15",
                        "selected_precision": "DAY",
                        "semantic_role": "FACT_OCCURRENCE",
                        "status": "RESOLVED",
                        "event_id": "T1",
                        "fact_id": "TF1",
                        "subject_time": "SAME",
                        "note": None,
                    },
                    {
                        "delta_id": "D2",
                        "runtime_atomic_id": "runtime-example-2",
                        "runtime_package_id": None,
                        "source_message_id": "source-example-2",
                        "candidates": [],
                        "selected_date": "2026-01-15",
                        "selected_precision": "DAY",
                        "semantic_role": "EVENT_OCCURRENCE",
                        "status": "RESOLVED",
                        "event_id": "E7",
                        "fact_id": None,
                        "subject_time": None,
                        "note": None,
                    },
                    {
                        "delta_id": "D2",
                        "runtime_atomic_id": "runtime-example-2",
                        "runtime_package_id": None,
                        "source_message_id": "source-example-2",
                        "candidates": [],
                        "selected_date": "2026-01-15",
                        "selected_precision": "DAY",
                        "semantic_role": "FACT_OCCURRENCE",
                        "status": "RESOLVED",
                        "event_id": "E7",
                        "fact_id": "F9",
                        "subject_time": "SAME",
                        "note": None,
                    },
                    {
                        "delta_id": "D3",
                        "runtime_atomic_id": "runtime-example-3",
                        "runtime_package_id": None,
                        "source_message_id": None,
                        "candidates": [],
                        "selected_date": None,
                        "selected_precision": None,
                        "semantic_role": "EVENT_OCCURRENCE",
                        "status": "UNRESOLVED",
                        "event_id": None,
                        "fact_id": None,
                        "subject_time": None,
                        "note": "Retained Pending.",
                    },
                    {
                        "delta_id": "D4",
                        "runtime_atomic_id": "runtime-example-4",
                        "runtime_package_id": None,
                        "source_message_id": None,
                        "candidates": [],
                        "selected_date": None,
                        "selected_precision": None,
                        "semantic_role": "EVENT_OCCURRENCE",
                        "status": "UNRESOLVED",
                        "event_id": None,
                        "fact_id": None,
                        "subject_time": None,
                        "note": "Retained Pending.",
                    },
                    ),
                )
            )
            + "\n",
            encoding="utf-8",
        )
        (root / "reference_view_decision_ledger.jsonl").write_text(
            "\n".join(
                json.dumps(
                    {
                        "event_id": event_id,
                        "is_important": True,
                        "include_in_reference_view": True,
                        "reference_view_basis": "CURRENT_BASELINE",
                        "note": "Defines the current product baseline.",
                        "review_reason": "NEW_OR_MODIFIED",
                        "as_of": "2026-01-20T00:00:00Z",
                    }
                )
                for event_id in ("T1", "E7")
            )
            + "\n",
            encoding="utf-8",
        )

    def export_published(
        self, *, ticker: str, output_dir: str | Path, version: int | None = None
    ) -> dict[str, Path]:
        selected = self._repository.published_version(ticker) if version is None else version
        events = self._repository.published_events(ticker, selected)
        target = Path(output_dir)
        target.mkdir(parents=True, exist_ok=True)
        json_path = target / f"event_library_v{selected}.json"
        markdown_path = target / f"event_library_v{selected}.md"
        agent_reference_path = target / f"reference_view_agent_v{selected}.md"
        human_reference_path = target / f"reference_view_human_v{selected}.md"
        known_index_path = target / f"known_event_index_v{selected}.md"
        reference_delta_path = target / f"reference_view_delta_v{selected}.json"
        json_path.write_text(
            _json_text(
                {
                    "ticker": ticker.upper(),
                    "version": selected,
                    "events": [event.model_dump(mode="json") for event in events],
                }
            ),
            encoding="utf-8",
        )
        sections = [
            f"# {ticker.upper()} Canonical Event Library V{selected}",
            "",
        ]
        for event in _event_order(events):
            sections.extend(
                [
                    f"## {event.event_id} — {event.title}",
                    "",
                    f"- Event time: {event.occurred_at} [{event.occurrence_time_precision.value}]",
                    f"- Type: {event.event_type}",
                    f"- Summary: {event.canonical_summary}",
                    "",
                    *[
                        (
                            f"- {fact.fact_id}: occurred_at="
                            f"{_fact_occurrence_display(event, fact.fact_occurred_at)}; "
                            f"subject_time={fact.subject_time or 'null'}; {fact.proposition}"
                        )
                        for fact in event.facts
                    ],
                    "",
                ]
            )
        markdown_path.write_text("\n".join(sections), encoding="utf-8")
        reference = self.reference_view(ticker, selected)
        human_reference = self.reference_view(ticker, selected, include_basis=True)
        agent_reference_path.write_text(reference, encoding="utf-8")
        human_reference_path.write_text(human_reference, encoding="utf-8")
        known_index_path.write_text(self.known_event_index(ticker, selected), encoding="utf-8")
        exports = {
            "json": json_path,
            "markdown": markdown_path,
            "reference_view_agent": agent_reference_path,
            "reference_view_human": human_reference_path,
            "known_event_index": known_index_path,
        }
        base_version = self._repository.library_base_version(ticker, selected)
        if base_version is not None and selected > base_version:
            delta = self.reference_view_delta(
                ticker,
                from_version=base_version,
                to_version=selected,
                persist=not self._repository.read_only,
            )
            reference_delta_path.write_text(delta.model_dump_json(indent=2), encoding="utf-8")
            exports["reference_view_delta"] = reference_delta_path
        return exports
