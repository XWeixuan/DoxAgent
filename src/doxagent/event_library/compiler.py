"""Published views and immutable O2 Frozen View materialization."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from doxagent.event_library.contracts import (
    CanonicalEvent,
    CanonicalEventRevision,
    CanonicalRevisionBundleManifest,
    CanonicalSubjectTimeMarker,
    DeltaBatch,
    FrozenViewManifest,
    ReferenceReviewCandidate,
    RuntimePackageDelta,
)
from doxagent.event_library.repository import EventLibraryRepository

REFERENCE_VIEW_CONTRACT_VERSION = "reference-view-md-v3"
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
    by_id = sorted(events, key=lambda item: int(item.event_id[1:]))
    return sorted(by_id, key=lambda item: item.occurred_at, reverse=True)


def _reference_fact_lines(event: CanonicalEvent) -> list[str]:
    """Render non-singleton Facts without repeating an Event-equivalent time."""

    if len(event.facts) == 1:
        return []
    lines = ["facts:", ""]
    for fact in event.facts:
        proposition = _wire_cell(fact.proposition)
        if fact.subject_time == CanonicalSubjectTimeMarker.SAME:
            lines.append(f"- {proposition}")
            continue
        subject_time = _wire_cell(fact.subject_time or "null")
        lines.append(f"- [{subject_time}] {proposition}")
    return lines


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
            if not _summary_duplicates_title(
                title=event.title, summary=event.known_event_summary
            ):
                cells.append(_wire_cell(event.known_event_summary))
            lines.append(" | ".join(cells))
        return "" if not lines else "\n".join(lines) + "\n"

    def reference_events(
        self, ticker: str, version: int | None = None
    ) -> list[CanonicalEvent]:
        selected = self._repository.published_version(ticker) if version is None else version
        return [
            event
            for event in _event_order(self._repository.published_events(ticker, selected))
            if event.include_in_reference_view
        ]

    def reference_view(self, ticker: str, version: int | None = None) -> str:
        sections = ["fields: event_id | occurred_at | title", ""]
        for event in self.reference_events(ticker, version):
            sections.extend(
                [
                    " | ".join(
                        (
                            event.event_id,
                            _wire_cell(_display_occurrence_time(event.occurred_at)),
                            _wire_cell(event.title),
                        )
                    ),
                    f"event_type: {_wire_cell(event.event_type)}",
                    f"canonical_summary: {_wire_cell(event.canonical_summary)}",
                    *_reference_fact_lines(event),
                    "",
                ]
            )
        return "\n".join(sections).rstrip() + "\n"

    def event_detail(
        self, ticker: str, event_id: str, version: int | None = None
    ) -> CanonicalEvent | None:
        return self._repository.get_event(ticker, event_id, version)

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
    ) -> tuple[Path, FrozenViewManifest]:
        if not batches:
            raise ValueError("at least one Delta batch is required")
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
                            time_anchors=sorted({item.time for item in members}),
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
                "time": item.time,
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
        if upstream_context_manifest is not None:
            upstream_path = "upstream/o2_upstream_context_manifest.json"
            (root / upstream_path).write_text(
                _json_text(upstream_context_manifest), encoding="utf-8"
            )
        (root / "schemas" / "canonical_event.schema.json").write_text(
            _json_text(CanonicalEventRevision.model_json_schema()), encoding="utf-8"
        )
        (root / "schemas" / "revision_bundle.schema.json").write_text(
            _json_text(CanonicalRevisionBundleManifest.model_json_schema()), encoding="utf-8"
        )
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
            canonical_event_schema_path="schemas/canonical_event.schema.json",
            revision_bundle_schema_path="schemas/revision_bundle.schema.json",
        )
        (root / "manifest.json").write_text(
            _json_text(manifest.model_dump(mode="json")), encoding="utf-8"
        )
        return root, manifest

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
                    f"- Occurred: {event.occurred_at}",
                    f"- Type: {event.event_type}",
                    f"- Summary: {event.canonical_summary}",
                    "",
                    *[f"- {fact.fact_id}: {fact.proposition}" for fact in event.facts],
                    "",
                ]
            )
        markdown_path.write_text("\n".join(sections), encoding="utf-8")
        reference = self.reference_view(ticker, selected)
        agent_reference_path.write_text(reference, encoding="utf-8")
        human_reference_path.write_text(reference, encoding="utf-8")
        known_index_path.write_text(
            self.known_event_index(ticker, selected), encoding="utf-8"
        )
        return {
            "json": json_path,
            "markdown": markdown_path,
            "reference_view_agent": agent_reference_path,
            "reference_view_human": human_reference_path,
            "known_event_index": known_index_path,
        }
