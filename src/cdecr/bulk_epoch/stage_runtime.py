"""Shared deterministic runtime primitives for BULK_EPOCH stages.

The objects in this module are deliberately stage-scoped.  They replace repeated
Registry reads without becoming a second persistence model.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import Future
from dataclasses import dataclass
from functools import partial
from time import perf_counter
from typing import Any, TypeVar

from cdecr.bulk_epoch.writer import BulkWriter
from cdecr.contracts import (
    AtomicEvent,
    EventMention,
    EventPackage,
)
from cdecr.field_coreference_contracts import CanonicalFieldLink, CanonicalFieldRegistryEntry
from cdecr.ports import CDECRRegistry, DecisionAuditRecord

_T = TypeVar("_T")


def _stable_hash(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class StageReadSnapshot:
    mentions_by_id: dict[str, EventMention]
    events_by_id: dict[str, AtomicEvent]
    packages_by_id: dict[str, EventPackage]
    field_links_by_mention: dict[str, tuple[CanonicalFieldLink, ...]]
    field_entries_by_id: dict[str, CanonicalFieldRegistryEntry]
    package_ids_by_event: dict[str, tuple[str, ...]]
    embeddings_by_owner: dict[str, tuple[float, ...]]
    snapshot_hash: str
    load_ms: int
    query_count: int

    @classmethod
    def load(
        cls,
        registry: CDECRRegistry,
        *,
        mentions: Sequence[EventMention] | None = None,
        events: Sequence[AtomicEvent] | None = None,
        packages: Sequence[EventPackage] | None = None,
        embedding_owner_kind: str | None = None,
        embedding_model: str | None = None,
    ) -> StageReadSnapshot:
        started = perf_counter()
        query_count = 0
        mention_values = list(mentions) if mentions is not None else registry.list_all_mentions(
            limit=1_000_000
        )
        if mentions is None:
            query_count += 1
        event_values = list(events) if events is not None else registry.list_current_atomic_events(
            limit=10_000
        )
        if events is None:
            query_count += 1
        package_values = list(packages) if packages is not None else registry.list_current_packages(
            limit=10_000
        )
        if packages is None:
            query_count += 1
        mention_ids = sorted({mention.mention_id for mention in mention_values})
        links = registry.get_field_links_for_mentions(mention_ids) if mention_ids else {}
        if mention_ids:
            query_count += 1
        entry_ids = sorted({link.registry_id for values in links.values() for link in values})
        entries: dict[str, CanonicalFieldRegistryEntry] = {}
        if entry_ids:
            # list_field_registry_entries is one SQL query and also preserves redirects as
            # explicit data; callers resolve only the IDs they actually use.
            all_entries = {
                entry.id: entry
                for entry in registry.list_field_registry_entries(
                    limit=min(100_000, max(10_000, len(entry_ids) * 2))
                )
            }
            for entry_id in entry_ids:
                current = all_entries.get(entry_id)
                seen: set[str] = set()
                while current is not None and current.redirect_to and current.id not in seen:
                    seen.add(current.id)
                    current = all_entries.get(current.redirect_to)
                if current is not None:
                    entries[entry_id] = current
                else:
                    entry = registry.resolve_field_registry_entry(entry_id)
                    query_count += 1
                    if entry is not None:
                        entries[entry_id] = entry
            query_count += 1
        package_ids = registry.list_packages_for_events(
            sorted({event.event_id for event in event_values})
        )
        if event_values:
            query_count += 1
        embedding_values: dict[str, tuple[float, ...]] = {}
        if embedding_owner_kind and embedding_model:
            embedding_values = {
                item.owner_id: tuple(item.vector)
                for item in registry.list_latest_embeddings(
                    owner_kind=embedding_owner_kind,
                    model=embedding_model,
                    limit=100_000,
                )
            }
            query_count += 1
        identity = {
            "mentions": sorted((item.mention_id, item.message_id) for item in mention_values),
            "events": sorted((item.event_id, item.version) for item in event_values),
            "packages": sorted((item.package_id, item.version) for item in package_values),
            "links": sorted(
                (mention_id, link.field_path, link.registry_id)
                for mention_id, values in links.items()
                for link in values
            ),
            "embeddings": sorted(embedding_values),
        }
        return cls(
            mentions_by_id={item.mention_id: item for item in mention_values},
            events_by_id={item.event_id: item for item in event_values},
            packages_by_id={item.package_id: item for item in package_values},
            field_links_by_mention={
                key: tuple(sorted(value, key=lambda item: (item.field_path, item.registry_id)))
                for key, value in links.items()
            },
            field_entries_by_id=entries,
            package_ids_by_event={key: tuple(sorted(value)) for key, value in package_ids.items()},
            embeddings_by_owner=embedding_values,
            snapshot_hash=_stable_hash(identity),
            load_ms=round((perf_counter() - started) * 1000),
            query_count=query_count,
        )

    def mentions_for_event(self, event: AtomicEvent) -> list[EventMention]:
        return [
            self.mentions_by_id[mention_id]
            for mention_id in event.mention_ids
            if mention_id in self.mentions_by_id
        ]

    def resolved_entry(self, link: CanonicalFieldLink) -> CanonicalFieldRegistryEntry | None:
        return self.field_entries_by_id.get(link.registry_id)


@dataclass
class StageWriteTelemetry:
    submitted_count: int = 0
    completed_count: int = 0
    retry_count: int = 0
    degraded_count: int = 0
    audit_buffered_count: int = 0
    audit_transaction_count: int = 0
    task_transaction_count: int = 0
    apply_chunk_count: int = 0


class StageWriteBuffer:
    """Non-blocking producer facade over the process-local single writer."""

    def __init__(self, writer: BulkWriter, *, default_chunk_size: int = 64) -> None:
        self.writer = writer
        self.default_chunk_size = max(1, default_chunk_size)
        self._futures: list[Future[Any]] = []
        self.telemetry = StageWriteTelemetry()

    def submit(self, operation: Callable[[], _T]) -> Future[_T]:
        future = self.writer.submit(operation)
        self._futures.append(future)
        self.telemetry.submitted_count += 1
        return future

    def submit_chunks(
        self,
        records: Sequence[_T],
        operation: Callable[[Sequence[_T]], Any],
        *,
        chunk_size: int | None = None,
    ) -> list[Future[Any]]:
        size = max(1, chunk_size or self.default_chunk_size)
        return [
            self.submit(partial(operation, tuple(records[offset : offset + size])))
            for offset in range(0, len(records), size)
        ]

    def submit_audits(
        self,
        registry: CDECRRegistry,
        records: Sequence[DecisionAuditRecord],
        *,
        chunk_size: int = 512,
    ) -> list[Future[Any]]:
        self.telemetry.audit_buffered_count += len(records)
        futures = self.submit_chunks(
            sorted(records, key=lambda item: item.audit_id),
            lambda chunk: registry.append_decision_audits(chunk, chunk_size=len(chunk)),
            chunk_size=chunk_size,
        )
        self.telemetry.audit_transaction_count += len(futures)
        return futures

    def barrier(self) -> list[Any]:
        results: list[Any] = []
        futures, self._futures = self._futures, []
        for future in futures:
            results.append(future.result())
            self.telemetry.completed_count += 1
        return results


class DecisionAuditBuffer:
    """Stage-local audit collector; payload and audit identity remain unchanged."""

    def __init__(self) -> None:
        self.records: list[DecisionAuditRecord] = []

    def append(self, record: DecisionAuditRecord) -> None:
        self.records.append(record)

    def extend(self, records: Iterable[DecisionAuditRecord]) -> None:
        self.records.extend(records)

    def flush(self, registry: CDECRRegistry, *, chunk_size: int = 512) -> dict[str, int]:
        records, self.records = self.records, []
        if not records:
            return {
                "inserted": 0,
                "reused": 0,
                "conflicted": 0,
                "degraded": 0,
                "transactions": 0,
                "retries": 0,
            }
        return registry.append_decision_audits(records, chunk_size=chunk_size)
