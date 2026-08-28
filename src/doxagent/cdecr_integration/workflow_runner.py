"""Shared explicit-message CDECR document/Bulk Epoch entrypoint and freezer."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any, Protocol

from cdecr.contracts import AtomicEvent, EventPackage
from cdecr.ports import CDECRRegistry
from doxagent.cdecr_integration.contracts import CDECRWorkflowResult, RuntimeRegistryBinding
from doxagent.event_library.contracts import (
    CanonicalAssertionState,
    FrozenRuntimeAtomic,
    FrozenRuntimeSnapshot,
    OccurrenceDateCandidate,
    OccurrenceDateCandidateSource,
    RuntimePackageSnapshot,
)


class DocumentBatchProcessor(Protocol):
    def process_batch(self, message_ids: list[str]) -> Sequence[Any]: ...


class BulkEpochProcessor(Protocol):
    @property
    def last_epoch_id(self) -> str | None: ...

    def process_batch(self, message_ids: list[str]) -> Sequence[Any]: ...


class CDECRWorkflowRunner:
    """Run the existing production processors with an explicit immutable message set."""

    def __init__(
        self,
        *,
        binding: RuntimeRegistryBinding,
        registry: CDECRRegistry,
        document_processor: DocumentBatchProcessor,
        bulk_epoch_engine: BulkEpochProcessor,
    ) -> None:
        self.binding = binding
        self.registry = registry
        self.document_processor = document_processor
        self.bulk_epoch_engine = bulk_epoch_engine

    def run(
        self, message_ids: Sequence[str], *, as_of: datetime | None = None
    ) -> CDECRWorkflowResult:
        requested = list(dict.fromkeys(message_ids))
        if not requested:
            return CDECRWorkflowResult(
                market=self.binding.market,
                ticker=self.binding.ticker,
                runtime_scope=self.binding.runtime_scope,
                status="FINALIZED_NOOP",
                message_ids=[],
                document_count=0,
                eligible_document_count=0,
                completed_at=datetime.now(UTC),
            )
        missing = [item for item in requested if self.registry.get_source(item) is None]
        if missing:
            raise ValueError(f"unknown SourceMessage IDs: {', '.join(sorted(missing))}")
        documents = list(self.document_processor.process_batch(requested))
        status_by_message = {
            str(item.message_id): _enum_value(getattr(item, "status", ""))
            for item in documents
        }
        if set(status_by_message) != set(requested):
            raise ValueError("document processor did not return complete message ID coverage")
        eligible = [item for item in requested if status_by_message[item] == "SUCCEEDED"]
        if not eligible:
            return CDECRWorkflowResult(
                market=self.binding.market,
                ticker=self.binding.ticker,
                runtime_scope=self.binding.runtime_scope,
                status="FINALIZED_NOOP",
                message_ids=requested,
                document_count=len(documents),
                eligible_document_count=0,
                completed_at=datetime.now(UTC),
            )
        activation = getattr(self.registry, "activate_runtime_eligibility", None)
        deactivation = getattr(self.registry, "deactivate_runtime_eligibility", None)
        activity_payload = activation(as_of=as_of, days=60) if as_of and activation else None
        try:
            self.bulk_epoch_engine.process_batch(eligible)
            epoch_id = self.bulk_epoch_engine.last_epoch_id
            if epoch_id is not None and activity_payload is not None:
                snapshot_reader = getattr(self.registry, "runtime_eligibility_snapshot", None)
                final_payload = (
                    snapshot_reader() if snapshot_reader is not None else activity_payload
                ) or activity_payload
                final_payload = {**activity_payload, **final_payload}
                encoded = json.dumps(final_payload, ensure_ascii=False, sort_keys=True)
                self.registry.save_bulk_epoch_artifact(
                    epoch_id=epoch_id,
                    artifact_kind="runtime_activity_v1",
                    artifact_hash=hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
                    upstream_hash=epoch_id,
                    payload=final_payload,
                )
        finally:
            if deactivation is not None:
                deactivation()
        epoch_id = self.bulk_epoch_engine.last_epoch_id
        if epoch_id is None:
            raise RuntimeError("Bulk Epoch engine completed without exposing epoch_id")
        epoch = self.registry.get_bulk_epoch(epoch_id)
        if epoch is None or str(epoch.get("status")) != "FINALIZED":
            raise RuntimeError(f"CDECR epoch {epoch_id!r} did not reach FINALIZED")
        return CDECRWorkflowResult(
            market=self.binding.market,
            ticker=self.binding.ticker,
            runtime_scope=self.binding.runtime_scope,
            status="FINALIZED",
            message_ids=requested,
            document_count=len(documents),
            eligible_document_count=len(eligible),
            epoch_id=epoch_id,
            completed_at=datetime.now(UTC),
        )

    def freeze_finalized_snapshot(
        self,
        *,
        epoch_id: str,
        as_of: datetime,
        eligible_atomic_ids: set[str] | None = None,
    ) -> FrozenRuntimeSnapshot:
        epoch = self.registry.get_bulk_epoch(epoch_id)
        if epoch is None or str(epoch.get("status")) != "FINALIZED":
            raise ValueError("only a FINALIZED CDECR epoch can be frozen")
        atomics = sorted(
            (
                item
                for item in self.registry.list_current_atomic_events(limit=10_000)
                if eligible_atomic_ids is None or item.event_id in eligible_atomic_ids
            ),
            key=lambda item: item.event_id,
        )
        packages = sorted(
            (
                item.model_copy(
                    update={
                        "member_event_ids": [
                            event_id
                            for event_id in item.member_event_ids
                            if eligible_atomic_ids is None
                            or event_id in eligible_atomic_ids
                        ]
                    }
                )
                for item in self.registry.list_current_packages(limit=10_000)
                if eligible_atomic_ids is None
                or any(event_id in eligible_atomic_ids for event_id in item.member_event_ids)
            ),
            key=lambda item: item.package_id,
        )
        package_ids_by_atomic: dict[str, list[str]] = {}
        for package in packages:
            for atomic_id in package.member_event_ids:
                package_ids_by_atomic.setdefault(atomic_id, []).append(package.package_id)
        occurrence_candidates_by_atomic: dict[str, list[OccurrenceDateCandidate]] = {}
        source_ids_by_atomic: dict[str, list[str]] = {}
        for item in atomics:
            candidates, source_ids = _atomic_occurrence_inputs(item, self.registry)
            occurrence_candidates_by_atomic[item.event_id] = candidates
            source_ids_by_atomic[item.event_id] = source_ids
        frozen_atomics = [
            FrozenRuntimeAtomic(
                runtime_atomic_id=item.event_id,
                version=item.version,
                proposition=item.canonical_proposition,
                time=_event_time(item),
                subject_time=_subject_time(item),
                occurrence_date_candidates=occurrence_candidates_by_atomic[item.event_id],
                source_message_ids=source_ids_by_atomic[item.event_id],
                assertion_state=CanonicalAssertionState(item.assertion_state.value),
                entities=_identity_entities(item),
                runtime_package_ids=sorted(package_ids_by_atomic.get(item.event_id, [])),
            )
            for item in atomics
        ]
        frozen_packages = [
            _package_snapshot(
                item,
                candidates_by_atomic=occurrence_candidates_by_atomic,
                source_ids_by_atomic=source_ids_by_atomic,
            )
            for item in packages
        ]
        identity = {
            "runtime_scope": self.binding.runtime_scope,
            "epoch_id": epoch_id,
            "atomics": [item.model_dump(mode="json") for item in frozen_atomics],
            "packages": [item.model_dump(mode="json") for item in frozen_packages],
        }
        digest = hashlib.sha256(
            json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        return FrozenRuntimeSnapshot(
            contract_version="frozen-runtime-time-v2",
            snapshot_id=f"runtime-snapshot:{digest[:24]}",
            runtime_scope=self.binding.runtime_scope,
            epoch_id=epoch_id,
            market=self.binding.market,
            ticker=self.binding.ticker,
            as_of=as_of,
            atomics=frozen_atomics,
            packages=frozen_packages,
        )


def _enum_value(value: object) -> str:
    return str(getattr(value, "value", value))


def _time_part(value: datetime | date | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _event_time(event: AtomicEvent) -> str:
    start = _time_part(event.time.event_start)
    end = _time_part(event.time.event_end)
    reference = event.time.reference_period_id
    observed: str | None
    if start and end and start != end:
        observed = f"{start}..{end}"
    else:
        observed = start or end
    if observed and reference:
        return f"{observed} | {reference}"
    return observed or reference or "UNKNOWN"


def _subject_time(event: AtomicEvent) -> str | None:
    if event.time.reference_period_id:
        return event.time.reference_period_id
    if event.assertion_state.value in {"PLANNED", "EXPECTED", "HYPOTHETICAL"}:
        raw = _event_time(event)
        return None if raw == "UNKNOWN" else raw
    return None


def _identity_entities(event: AtomicEvent) -> list[str]:
    payload = event.identity_profile.model_dump(mode="json")
    values: set[str] = set()
    identity_keys = {
        "entity_id",
        "issuer_id",
        "company_id",
        "institution_id",
        "instrument_id",
        "person_id",
        "counterparty_id",
        "product_id",
    }

    def walk(value: object, key: str | None = None) -> None:
        if isinstance(value, dict):
            for nested_key, nested in value.items():
                walk(nested, str(nested_key))
        elif isinstance(value, list):
            for nested in value:
                walk(nested, key)
        elif key in identity_keys and isinstance(value, str) and value.strip():
            values.add(value.strip())

    walk(payload)
    return sorted(values)[:12]


def _atomic_occurrence_inputs(
    event: AtomicEvent, registry: CDECRRegistry
) -> tuple[list[OccurrenceDateCandidate], list[str]]:
    candidates: list[OccurrenceDateCandidate] = []
    source_ids: set[str] = set()
    mentions = [registry.get_mention(mention_id) for mention_id in event.mention_ids]
    for mention in mentions:
        if mention is None:
            continue
        source = registry.get_source(mention.message_id)
        if source is None:
            continue
        source_ids.add(source.message_id)
        source_kind = (
            OccurrenceDateCandidateSource.OFFICIAL_RELEASE_DATE
            if str(source.source_type.value) in {"FILING", "ANNOUNCEMENT"}
            else OccurrenceDateCandidateSource.SOURCE_PUBLISHED_AT
        )
        candidates.append(
            OccurrenceDateCandidate(
                candidate_date=source.published_at.date(),
                source_kind=source_kind,
                source_id=source.message_id,
                source_message_id=source.message_id,
            )
        )
        mentioned = mention.time.event_start or mention.time.event_end
        if (
            mentioned is not None
            and mention.time.precision.value in {"DAY", "TIMESTAMP"}
            and mention.assertion_state.value not in {"PLANNED", "EXPECTED", "HYPOTHETICAL"}
        ):
            candidates.append(
                OccurrenceDateCandidate(
                    candidate_date=(
                        mentioned.date() if isinstance(mentioned, datetime) else mentioned
                    ),
                    source_kind=OccurrenceDateCandidateSource.PROPOSITION_EVIDENCE,
                    source_id=mention.mention_id,
                    source_message_id=source.message_id,
                    evidence=mention.canonical_proposition,
                )
            )
    if event.assertion_state.value not in {"PLANNED", "EXPECTED", "HYPOTHETICAL"}:
        observed = event.time.event_start or event.time.event_end
        if observed is not None and event.time.precision.value in {"DAY", "TIMESTAMP"}:
            candidates.append(
                OccurrenceDateCandidate(
                    candidate_date=observed.date() if isinstance(observed, datetime) else observed,
                    source_kind=OccurrenceDateCandidateSource.RUNTIME_CONFIRMED_OCCURRENCE,
                    source_id=event.event_id,
                )
            )
    unique: dict[tuple[str, str, str | None], OccurrenceDateCandidate] = {}
    for item in candidates:
        key = (item.candidate_date.isoformat(), item.source_kind.value, item.source_message_id)
        unique[key] = item
    priority = {
        OccurrenceDateCandidateSource.PROPOSITION_EVIDENCE: 1,
        OccurrenceDateCandidateSource.OFFICIAL_RELEASE_DATE: 2,
        OccurrenceDateCandidateSource.RUNTIME_CONFIRMED_OCCURRENCE: 3,
        OccurrenceDateCandidateSource.SOURCE_PUBLISHED_AT: 4,
        OccurrenceDateCandidateSource.FOCUSED_WEB_SEARCH: 5,
    }
    return (
        sorted(
            unique.values(),
            key=lambda item: (priority[item.source_kind], item.candidate_date, item.source_id),
        ),
        sorted(source_ids),
    )


def _package_snapshot(
    package: EventPackage,
    *,
    candidates_by_atomic: dict[str, list[OccurrenceDateCandidate]],
    source_ids_by_atomic: dict[str, list[str]],
) -> RuntimePackageSnapshot:
    candidates = [
        item.model_copy(update={"runtime_package_id": package.package_id})
        for atomic_id in package.member_event_ids
        for item in candidates_by_atomic.get(atomic_id, [])
    ]
    return RuntimePackageSnapshot(
        runtime_package_id=package.package_id,
        version=package.version,
        title=package.canonical_title,
        member_runtime_atomic_ids=sorted(package.member_event_ids),
        source_message_ids=sorted(
            {
                source_id
                for atomic_id in package.member_event_ids
                for source_id in source_ids_by_atomic.get(atomic_id, [])
            }
        ),
        occurrence_date_candidates=sorted(
            {
                (item.candidate_date, item.source_kind, item.source_id): item for item in candidates
            }.values(),
            key=lambda item: (item.candidate_date, item.source_kind.value, item.source_id),
        ),
    )
