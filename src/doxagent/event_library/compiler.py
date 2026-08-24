"""Published views and immutable O2 Frozen View materialization."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from doxagent.event_library.contracts import (
    CanonicalEvent,
    CanonicalEventRevision,
    CanonicalRevisionBundleManifest,
    DeltaBatch,
    FrozenViewManifest,
)
from doxagent.event_library.repository import EventLibraryRepository


def _json_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n"


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _wire_cell(value: str) -> str:
    return " ".join(value.replace("|", r"\|").split())


def _event_order(events: list[CanonicalEvent]) -> list[CanonicalEvent]:
    by_id = sorted(events, key=lambda item: int(item.event_id[1:]))
    return sorted(by_id, key=lambda item: item.occurred_at, reverse=True)


class EventLibraryViewCompiler:
    def __init__(self, repository: EventLibraryRepository) -> None:
        self._repository = repository

    def known_event_index(self, ticker: str, version: int | None = None) -> str:
        events = self._repository.published_events(ticker, version)
        lines = [
            " | ".join(
                (
                    event.event_id,
                    _wire_cell(event.occurred_at),
                    _wire_cell(event.title),
                    _wire_cell(event.known_event_summary),
                )
            )
            for event in _event_order(events)
        ]
        return "" if not lines else "\n".join(lines) + "\n"

    def reference_view(self, ticker: str, version: int | None = None) -> dict[str, Any]:
        selected = self._repository.published_version(ticker) if version is None else version
        events = [
            event
            for event in _event_order(self._repository.published_events(ticker, selected))
            if event.include_in_reference_view
        ]
        return {
            "ticker": ticker.upper(),
            "version": selected,
            "events": [
                {
                    "event_id": event.event_id,
                    "occurred_at": event.occurred_at,
                    "event_type": event.event_type,
                    "title": event.title,
                    "canonical_summary": event.canonical_summary,
                    "is_important": event.is_important,
                }
                for event in events
            ],
        }

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
        reference_review_candidates: list[str] | None = None,
    ) -> tuple[Path, FrozenViewManifest]:
        if not batches:
            raise ValueError("at least one Delta batch is required")
        ticker = batches[0].ticker
        base_version = batches[0].base_library_version
        if any(
            item.ticker != ticker or item.base_library_version != base_version
            for item in batches
        ):
            raise ValueError("Frozen View batches must share ticker and base_library_version")
        delta_ids = [item.delta_id for batch in batches for item in batch.items]
        if len(delta_ids) != len(set(delta_ids)):
            raise ValueError("combined Frozen View Delta IDs must be unique")
        events = self._repository.published_events(ticker, base_version)
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
        (root / "delta" / "pending_atomics.json").write_text(
            _json_text(pending_atomics), encoding="utf-8"
        )
        (root / "delta" / "runtime_hints.json").write_text(
            _json_text(runtime_hints), encoding="utf-8"
        )
        (root / "review" / "reference_review_candidates.json").write_text(
            _json_text(reference_review_candidates or []), encoding="utf-8"
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
            reference_review_candidates_path="review/reference_review_candidates.json",
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
        return {"json": json_path, "markdown": markdown_path}
