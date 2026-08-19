"""Set-based parent occurrence induction, resolution, and frozen partitioning."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from time import perf_counter
from typing import Any, Literal, TypeVar

from pydantic import BaseModel

from cdecr.contracts import (
    AtomicEvent,
    EventMention,
    EventPackage,
    MembershipRelation,
    PackageFamily,
    SourceMessage,
)
from cdecr.field_coreference_contracts import CanonicalFieldLink, CanonicalFieldRegistryEntry
from cdecr.models import ModelTier
from cdecr.parent_occurrence_contracts import (
    AtomicDocumentSlice,
    ParentContextBlock,
    ParentExternalLinkProposal,
    ParentInductionBatch,
    ParentInductionDecision,
    ParentInductionDocument,
    ParentInductionGroup,
    ParentMembershipDecision,
    ParentProposalCard,
    ParentStageFailure,
)
from cdecr.parent_occurrence_signals import (
    MergeGuardStatus,
    ParentBoundarySignature,
    aggregate_signatures,
    compile_parent_signatures,
    proposal_merge_guard,
)
from cdecr.ports import CDECRRegistry, DecisionAuditRecord, StructuredModelRequest

_T = TypeVar("_T", bound=BaseModel)
_PROMPT_ROOT = Path(__file__).parent / "prompts" / "v1"
_CHECKPOINT_CONTRACT_VERSION = "parent-occurrence-v2.0r-contract-4-induction-recovery"
_SIGNAL_COMPILER_VERSION = "parent-occurrence-v2.0r-signal-2"
_PARENT_INPUT_WIRE_VERSION = "parent-induction-compact-wire-v1"


def _hash(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _short(prefix: str, value: object) -> str:
    return f"{prefix}:{_hash(value)[:24]}"


def _dedupe(values: Sequence[str], *, limit: int | None = None) -> list[str]:
    output = list(dict.fromkeys(value.strip() for value in values if value and value.strip()))
    return output if limit is None else output[:limit]


def _family_for_event(event: AtomicEvent) -> PackageFamily:
    value = event.event_family.value
    if value in {"FINANCIAL_PERFORMANCE", "GUIDANCE_EXPECTATION"}:
        return PackageFamily.EARNINGS_DISCLOSURE
    if value == "ANALYST_ACTION":
        return PackageFamily.ANALYST_REPORT
    if value == "TRANSACTION_CAPITAL":
        return PackageFamily.TRANSACTION
    if value == "REGULATORY_LEGAL_POLICY":
        return PackageFamily.REGULATORY_LEGAL
    if value == "INCIDENT_GEOPOLITICAL":
        return PackageFamily.OPERATIONAL_INCIDENT
    if value == "PRODUCT_SCIENCE":
        return PackageFamily.PRODUCT_SCIENCE
    return PackageFamily.OTHER


def _evidence_for(mention: EventMention) -> list[str]:
    records = [item.text for item in mention.evidence_records if item.text]
    if records:
        return _dedupe(records, limit=2)
    return _dedupe([mention.source_claim or "", mention.canonical_proposition], limit=2)


@dataclass(frozen=True)
class PackageStageSnapshotV2:
    events: tuple[AtomicEvent, ...]
    mentions_by_id: dict[str, EventMention]
    sources_by_id: dict[str, SourceMessage]
    field_links_by_mention: dict[str, tuple[CanonicalFieldLink, ...]]
    field_entries_by_id: dict[str, CanonicalFieldRegistryEntry]
    packages: tuple[EventPackage, ...]
    snapshot_hash: str
    query_count: int
    load_ms: int

    @classmethod
    def load(
        cls,
        *,
        registry: CDECRRegistry,
        events: Sequence[AtomicEvent],
        mentions: Sequence[EventMention] | None,
        sources: Sequence[SourceMessage] | None,
        packages: Sequence[EventPackage],
    ) -> PackageStageSnapshotV2:
        started = perf_counter()
        query_count = 0
        if mentions is None:
            mentions = registry.list_all_mentions(limit=1_000_000)
            query_count += 1
        if sources is None:
            sources = registry.list_all_sources(limit=100_000)
            query_count += 1
        mention_ids = sorted({item.mention_id for item in mentions})
        get_links = getattr(registry, "get_field_links_for_mentions", None)
        raw_links = get_links(mention_ids) if mention_ids and callable(get_links) else {}
        if mention_ids and callable(get_links):
            query_count += 1
        entry_ids = {link.registry_id for values in raw_links.values() for link in values}
        field_entries: dict[str, CanonicalFieldRegistryEntry] = {}
        if entry_ids:
            list_entries = getattr(registry, "list_field_registry_entries", None)
            entries = (
                list_entries(limit=min(100_000, max(10_000, len(entry_ids) * 2)))
                if callable(list_entries)
                else []
            )
            query_count += int(callable(list_entries))
            by_id = {item.id: item for item in entries}
            for entry_id in sorted(entry_ids):
                current = by_id.get(entry_id)
                seen: set[str] = set()
                while current is not None and current.redirect_to and current.id not in seen:
                    seen.add(current.id)
                    current = by_id.get(current.redirect_to)
                if current is not None:
                    field_entries[entry_id] = current
        event_values = tuple(sorted(events, key=lambda item: item.event_id))
        package_values = tuple(sorted(packages, key=lambda item: item.package_id))
        identity = {
            "events": [(item.event_id, item.version) for item in event_values],
            "mentions": sorted(item.mention_id for item in mentions),
            "sources": sorted(item.message_id for item in sources),
            "packages": [(item.package_id, item.version) for item in package_values],
            "field_links": sorted(
                (mention_id, link.field_path, link.registry_id)
                for mention_id, values in raw_links.items()
                for link in values
            ),
        }
        return cls(
            events=event_values,
            mentions_by_id={item.mention_id: item for item in mentions},
            sources_by_id={item.message_id: item for item in sources},
            field_links_by_mention={
                key: tuple(sorted(value, key=lambda item: (item.field_path, item.registry_id)))
                for key, value in raw_links.items()
            },
            field_entries_by_id=field_entries,
            packages=package_values,
            snapshot_hash=_hash(identity),
            query_count=query_count,
            load_ms=round((perf_counter() - started) * 1000),
        )


@dataclass(frozen=True)
class ParentOccurrencePoolResult:
    """Frozen output of the retained single-document Parent Induction stage."""

    status: Literal["FINALIZED", "PARTIAL_PARENT_INDUCTION"]
    snapshot_hash: str
    proposals: tuple[ParentProposalCard, ...] = ()
    external_links: tuple[ParentExternalLinkProposal, ...] = ()
    failures: tuple[ParentStageFailure, ...] = ()
    telemetry: dict[str, object] | None = None


class ParentOccurrenceService:
    """Document-local Parent Induction service for the Package V3 occurrence pool."""

    def __init__(
        self,
        *,
        registry: CDECRRegistry,
        induction_active_requests: int = 96,
        induction_max_documents: int = 4,
        induction_max_slices: int = 48,
        context_soft_token_budget: int = 6_000,
        compact_wire_dto: bool = True,
    ) -> None:
        self.registry = registry
        self.induction_active_requests = max(1, induction_active_requests)
        self.induction_max_documents = max(1, induction_max_documents)
        self.induction_max_slices = max(1, min(96, induction_max_slices))
        self.context_soft_token_budget = max(1_000, context_soft_token_budget)
        self.compact_wire_dto = compact_wire_dto

    @staticmethod
    def _prompt(name: str) -> str:
        return (_PROMPT_ROOT / name).read_text(encoding="utf-8")

    @staticmethod
    def _document_context(
        source: SourceMessage,
        evidence_by_atomic: Mapping[str, Sequence[str]],
        *,
        soft_character_budget: int = 24_000,
        window_radius: int = 320,
    ) -> tuple[list[ParentContextBlock], dict[str, list[str]]]:
        """Deduplicate article context and return resolvable request-local refs."""

        article = source.text.strip()
        evidence_values = _dedupe(
            [value for values in evidence_by_atomic.values() for value in values]
        )
        blocks: list[str] = []
        if len(article) <= 12_000:
            blocks = [article]
        else:
            lead = article[: min(2_000, len(article))].strip()
            if lead:
                blocks.append(lead)
            windows: list[tuple[int, int]] = []
            for evidence in evidence_values:
                offset = article.find(evidence)
                if offset < 0:
                    continue
                windows.append(
                    (
                        max(0, offset - window_radius),
                        min(len(article), offset + len(evidence) + window_radius),
                    )
                )
            merged: list[tuple[int, int]] = []
            for start, end in sorted(windows):
                if merged and start <= merged[-1][1] + 80:
                    merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
                else:
                    merged.append((start, end))
            blocks.extend(article[start:end].strip() for start, end in merged)
            blocks = _dedupe(blocks)
            retained: list[str] = []
            used = 0
            for block in blocks:
                if retained and used + len(block) > soft_character_budget:
                    continue
                retained.append(block)
                used += len(block)
            blocks = retained or [article[:soft_character_budget]]
        context = [
            ParentContextBlock(block_ref=f"B{index}", text=text)
            for index, text in enumerate(blocks, start=1)
            if text
        ]
        refs_by_atomic: dict[str, list[str]] = {}
        for atomic_ref, atomic_evidence in evidence_by_atomic.items():
            refs = [
                item.block_ref
                for item in context
                if any(value and value in item.text for value in atomic_evidence)
            ]
            refs_by_atomic[atomic_ref] = refs or [context[0].block_ref]
        return context, refs_by_atomic

    def _slices(
        self, snapshot: PackageStageSnapshotV2
    ) -> tuple[
        list[ParentInductionDocument],
        dict[str, AtomicDocumentSlice],
        dict[str, ParentBoundarySignature],
        dict[str, object],
    ]:
        signatures, compile_telemetry = compile_parent_signatures(
            snapshot.events,
            mentions_by_id=snapshot.mentions_by_id,
            field_links_by_mention=snapshot.field_links_by_mention,
            field_entries_by_id=snapshot.field_entries_by_id,
            sources_by_id=snapshot.sources_by_id,
        )
        by_document: dict[str, list[AtomicDocumentSlice]] = defaultdict(list)
        slices: dict[str, AtomicDocumentSlice] = {}
        event_by_id = {item.event_id: item for item in snapshot.events}
        atomic_counter = 0
        for event in snapshot.events:
            mentions_by_document: dict[str, list[EventMention]] = defaultdict(list)
            for mention_id in event.mention_ids:
                mention = snapshot.mentions_by_id.get(mention_id)
                if mention is not None and mention.message_id in snapshot.sources_by_id:
                    mentions_by_document[mention.message_id].append(mention)
            for message_id in sorted(mentions_by_document):
                atomic_counter += 1
                source = snapshot.sources_by_id[message_id]
                signature = signatures[event.event_id]
                slice_id = _short("slice", {"event": event.event_id, "document": message_id})
                item = AtomicDocumentSlice(
                    slice_id=slice_id,
                    atomic_ref=f"A{atomic_counter}",
                    event_id=event.event_id,
                    document_ref=message_id,
                    document_fingerprint=_hash(
                        {"title": source.title, "text": source.text, "url": source.url}
                    ),
                    proposition=event.canonical_proposition,
                    event_family=event.event_family.value,
                    time=event.time.model_dump(mode="json"),
                    period_id=event.time.reference_period_id,
                    participants=list(signature.issuer_ids),
                    object_cues=list(signature.object_ids),
                    artifact_cues=list(signature.artifact_ids),
                    metric_cues=list(signature.metric_ids),
                    institution_cues=list(signature.institution_ids),
                    counterparty_cues=list(signature.counterparty_ids),
                    market_scope=list(signature.market_scope),
                    parent_role=signature.role.value,
                    evidence_refs=[],
                )
                by_document[message_id].append(item)
                slices[item.atomic_ref] = item
        documents: list[ParentInductionDocument] = []
        for index, (message_id, values) in enumerate(sorted(by_document.items()), start=1):
            source = snapshot.sources_by_id[message_id]
            evidence_by_atomic = {
                item.atomic_ref: _dedupe(
                    [
                        text
                        for mention_id in event_by_id[item.event_id].mention_ids
                        if (
                            (mention := snapshot.mentions_by_id.get(mention_id)) is not None
                            and mention.message_id == message_id
                        )
                        for text in _evidence_for(mention)
                    ],
                    limit=2,
                )
                for item in values
            }
            context, refs_by_atomic = self._document_context(
                source,
                evidence_by_atomic,
                soft_character_budget=self.context_soft_token_budget * 4,
            )
            values = [
                item.model_copy(update={"evidence_refs": refs_by_atomic[item.atomic_ref]})
                for item in values
            ]
            for item in values:
                slices[item.atomic_ref] = item
            documents.append(
                ParentInductionDocument(
                    task_id=f"D{index}",
                    document_ref=message_id,
                    title=source.title,
                    published_at=source.published_at.isoformat(),
                    source_name=source.source_name,
                    document_context=context,
                    atomics=sorted(values, key=lambda item: item.atomic_ref),
                )
            )
        return (
            documents,
            slices,
            signatures,
            {
                "boundary_signature_count": len(signatures),
                "boundary_cue_known": compile_telemetry.known_counts,
                "boundary_cue_unknown": compile_telemetry.unknown_counts,
                "boundary_signature_hash": _hash(
                    {key: value.stable_payload() for key, value in sorted(signatures.items())}
                ),
                "context_block_count": sum(len(item.document_context) for item in documents),
                "context_character_count": sum(
                    len(block.text) for item in documents for block in item.document_context
                ),
            },
        )

    def _pack_documents(
        self,
        documents: Sequence[ParentInductionDocument],
    ) -> list[list[ParentInductionDocument]]:
        batches: list[list[ParentInductionDocument]] = []
        current: list[ParentInductionDocument] = []
        slice_count = 0
        estimated_bytes = 0
        for document in documents:
            count = len(document.atomics)
            size = len(
                json.dumps(
                    self._induction_document_wire(document),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
            exclusive = count > self.induction_max_slices
            if current and (
                exclusive
                or len(current) >= self.induction_max_documents
                or slice_count + count > self.induction_max_slices
                or estimated_bytes + size > 72_000
            ):
                batches.append(current)
                current, slice_count, estimated_bytes = [], 0, 0
            if count > 96:
                for offset in range(0, count, 96):
                    shard = document.model_copy(
                        update={
                            "task_id": f"{document.task_id}:S{offset // 96 + 1}",
                            "atomics": document.atomics[offset : offset + 96],
                        }
                    )
                    batches.append([shard])
                continue
            current.append(document)
            slice_count += count
            estimated_bytes += size
            if exclusive:
                batches.append(current)
                current, slice_count, estimated_bytes = [], 0, 0
        if current:
            batches.append(current)
        return batches

    def _induction_document_wire(self, document: ParentInductionDocument) -> dict[str, object]:
        if not self.compact_wire_dto:
            return document.model_dump(mode="json", exclude_none=True)
        atomics: list[dict[str, object]] = []
        for item in document.atomics:
            cues = {
                key: value
                for key, value in {
                    "participants": item.participants,
                    "objects": item.object_cues,
                    "artifacts": item.artifact_cues,
                    "metrics": item.metric_cues,
                    "institutions": item.institution_cues,
                    "counterparties": item.counterparty_cues,
                    "market_scope": item.market_scope,
                    "period_id": item.period_id,
                }.items()
                if value not in (None, [], {})
            }
            wire: dict[str, object] = {
                "atomic_ref": item.atomic_ref,
                "proposition": item.proposition,
                "family": item.event_family,
                "role": item.parent_role,
            }
            if item.time:
                wire["time"] = item.time
            if cues:
                wire["cues"] = cues
            if item.evidence_refs:
                wire["evidence_refs"] = item.evidence_refs
            atomics.append(wire)
        return {
            "task_id": document.task_id,
            "title": document.title,
            "published_at": document.published_at,
            "source_name": document.source_name,
            "document_context": [
                item.model_dump(mode="json", exclude_none=True)
                for item in document.document_context
            ],
            "atomics": atomics,
        }

    @staticmethod
    def _validate_induction(
        value: ParentInductionBatch,
        documents: Sequence[ParentInductionDocument],
    ) -> None:
        expected_tasks = {item.task_id for item in documents}
        actual_tasks = {item.task_id for item in value.decisions}
        if actual_tasks != expected_tasks:
            raise ValueError("induction decisions must cover exactly the input document tasks")
        expected_by_task = {
            item.task_id: {atomic.atomic_ref for atomic in item.atomics} for item in documents
        }
        for decision in value.decisions:
            actual = [member.atomic_ref for group in decision.groups for member in group.members]
            if len(actual) != len(set(actual)) or set(actual) != expected_by_task[decision.task_id]:
                raise ValueError("induction groups must cover every input Atomic exactly once")
            group_ids = {item.local_group_id for item in decision.groups}
            for group in decision.groups:
                member_refs = {item.atomic_ref for item in group.members}
                for link in group.external_links:
                    if link.source_atomic_ref not in member_refs:
                        raise ValueError("external link must be nested under its source group")
                    if link.target_local_group_id not in group_ids:
                        raise ValueError("external link target must be a local group")
                    if link.target_local_group_id == group.local_group_id:
                        raise ValueError("external link target must be another parent group")

    def _induce(
        self,
        documents: Sequence[ParentInductionDocument],
        *,
        models: Any,
        run_id: str,
        checkpoint_scope_id: str,
        prompt_suffix: str = "",
        model_stage: str = "parent_induction",
    ) -> tuple[list[ParentInductionDecision], list[ParentStageFailure], dict[str, object]]:
        prompt = self._prompt("parent_occurrence_induction.md") + prompt_suffix
        schema = ParentInductionBatch.model_json_schema()
        wire_by_task = {
            item.task_id: self._induction_document_wire(item) for item in documents
        }
        effective_wire_version = (
            _PARENT_INPUT_WIRE_VERSION if self.compact_wire_dto else "parent-induction-legacy-wire"
        )
        input_hashes = {
            item.task_id: _hash(
                {
                    "contract_version": _CHECKPOINT_CONTRACT_VERSION,
                    "compiler_version": _SIGNAL_COMPILER_VERSION,
                    "wire_version": effective_wire_version,
                    "prompt": prompt,
                    "schema": schema,
                    "document": wire_by_task[item.task_id],
                }
            )
            for item in documents
        }
        checkpoint_by_task = {
            str(item["task_id"]): item
            for item in self.registry.list_parent_occurrence_checkpoints(
                run_id=checkpoint_scope_id, stage="PARENT_INDUCE"
            )
        }
        decisions: list[ParentInductionDecision] = []
        pending_documents: list[ParentInductionDocument] = []
        reused_task_count = 0
        for document in documents:
            checkpoint = checkpoint_by_task.get(document.task_id)
            if (
                checkpoint is not None
                and checkpoint.get("status") == "SUCCEEDED"
                and checkpoint.get("input_hash") == input_hashes[document.task_id]
                and isinstance(checkpoint.get("payload"), dict)
            ):
                try:
                    decision = ParentInductionDecision.model_validate(checkpoint["payload"])
                    self._validate_induction(ParentInductionBatch(decisions=[decision]), [document])
                except (ValueError, TypeError):
                    pending_documents.append(document)
                else:
                    decisions.append(decision)
                    reused_task_count += 1
            else:
                pending_documents.append(document)

        batches = self._pack_documents(pending_documents)
        start_write = self.registry.save_parent_occurrence_checkpoints(
            run_id=checkpoint_scope_id,
            records=[
                {
                    "stage": "PARENT_INDUCE",
                    "task_id": item.task_id,
                    "input_hash": input_hashes[item.task_id],
                    "status": "RUNNING",
                }
                for item in pending_documents
            ],
        )
        model_started = perf_counter()
        failures: list[ParentStageFailure] = []
        audits: list[DecisionAuditRecord] = []
        finish_records: list[dict[str, object]] = []
        retry_documents: list[ParentInductionDocument] = []

        def requests_for(
            values: Sequence[Sequence[ParentInductionDocument]],
        ) -> tuple[list[StructuredModelRequest], list[Callable[[ParentInductionBatch], None]]]:
            return (
                [
                    StructuredModelRequest(
                        system_prompt=(
                            prompt
                        ),
                        user_prompt=json.dumps(
                            {
                                "document_tasks": [
                                    wire_by_task[item.task_id] for item in batch
                                ]
                            },
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                        json_schema=schema,
                    )
                    for batch in values
                ],
                [partial(self._validate_induction, documents=batch) for batch in values],
            )

        def accept_batch(
            batch: Sequence[ParentInductionDocument],
            result: ParentInductionBatch,
        ) -> None:
            decisions.extend(result.decisions)
            decision_by_task = {item.task_id: item for item in result.decisions}
            finish_records.extend(
                {
                    "stage": "PARENT_INDUCE",
                    "task_id": document.task_id,
                    "input_hash": input_hashes[document.task_id],
                    "status": "SUCCEEDED",
                    "payload": decision_by_task[document.task_id].model_dump(mode="json"),
                }
                for document in batch
            )
            task_ids = [item.task_id for item in batch]
            audits.append(
                DecisionAuditRecord(
                    audit_id=_short("audit-parent-induction", {"run": run_id, "tasks": task_ids}),
                    run_id=run_id,
                    decision_type="PARENT_INDUCTION_PARTITION",
                    subject_id=f"tasks:{_hash(task_ids)[:12]}",
                    payload={
                        "task_ids": task_ids,
                        "input_hash": _hash(
                            [wire_by_task[item.task_id] for item in batch]
                        ),
                        "output_hash": _hash(result.model_dump(mode="json")),
                        "partition": result.model_dump(mode="json"),
                    },
                )
            )

        requests, validators = requests_for(batches)
        results = models.typed_many(
            tier=ModelTier.M2,
            execution_tier=ModelTier.M3,
            stage=model_stage,
            requests=requests,
            output_type=ParentInductionBatch,
            validators=validators,
        )
        for batch, result in zip(batches, results, strict=True):
            if isinstance(result, Exception):
                retry_documents.extend(batch)
            else:
                accept_batch(batch, result)

        retry_batches = [[item] for item in retry_documents]
        retry_requests, retry_validators = requests_for(retry_batches)
        retry_results = models.typed_many(
            tier=ModelTier.M2,
            execution_tier=ModelTier.M3,
            stage=model_stage,
            requests=retry_requests,
            output_type=ParentInductionBatch,
            validators=retry_validators,
        )
        for batch, result in zip(retry_batches, retry_results, strict=True):
            document = batch[0]
            if isinstance(result, Exception):
                error_code = str(getattr(result, "code", type(result).__name__))
                failures.append(
                    ParentStageFailure(
                        stage="PARENT_INDUCE",
                        task_id=document.task_id,
                        error_code=error_code,
                    )
                )
                finish_records.append(
                    {
                        "stage": "PARENT_INDUCE",
                        "task_id": document.task_id,
                        "input_hash": input_hashes[document.task_id],
                        "status": "FAILED_RETRYABLE",
                        "error_code": error_code,
                    }
                )
            else:
                accept_batch(batch, result)

        model_wall_ms = round((perf_counter() - model_started) * 1000)
        finish_write = self.registry.save_parent_occurrence_checkpoints(
            run_id=checkpoint_scope_id, records=finish_records
        )
        audit_write = self.registry.append_decision_audits(audits, chunk_size=512)
        return (
            decisions,
            failures,
            {
                "induction_task_count": len(documents),
                "induction_reused_task_count": reused_task_count,
                "induction_request_count": len(batches) + len(retry_batches),
                "induction_item_retry_count": len(retry_batches),
                "induction_checkpoint_transaction_count": int(start_write.get("transactions", 0))
                + int(finish_write.get("transactions", 0)),
                "induction_audit_transaction_count": int(audit_write.get("transactions", 0)),
                "model_wall_ms": model_wall_ms,
                "input_wire_version": effective_wire_version,
                "compact_wire_enabled": self.compact_wire_dto,
            },
        )

    def _proposals(
        self,
        decisions: Sequence[ParentInductionDecision],
        documents: Mapping[str, ParentInductionDocument],
        slices: Mapping[str, AtomicDocumentSlice],
        events_by_id: Mapping[str, AtomicEvent],
        signatures_by_event: Mapping[str, ParentBoundarySignature],
    ) -> tuple[list[ParentProposalCard], list[ParentExternalLinkProposal]]:
        output: list[ParentProposalCard] = []
        proposal_by_local_group: dict[tuple[str, str], str] = {}
        for decision in decisions:
            document = documents[decision.task_id.split(":S", maxsplit=1)[0]]
            for group in decision.groups:
                member_slices = [slices[item.atomic_ref] for item in group.members]
                event_ids = _dedupe([item.event_id for item in member_slices])
                member_events = [events_by_id[event_id] for event_id in event_ids]
                family = Counter(_family_for_event(event) for event in member_events).most_common(
                    1
                )[0][0]
                signature = aggregate_signatures(
                    [signatures_by_event[event_id] for event_id in event_ids]
                )
                proposal_id = _short(
                    "parent-proposal",
                    {
                        "compiler": _SIGNAL_COMPILER_VERSION,
                        "document": member_slices[0].document_fingerprint,
                        "events": sorted(event_ids),
                        "scope": group.scope,
                        "family": family.value,
                        "label": group.label,
                        "membership": sorted(
                            (
                                slices[item.atomic_ref].event_id,
                                item.membership_relation.value,
                            )
                            for item in group.members
                        ),
                        "boundary": signature.stable_payload(),
                    },
                )
                proposal_by_local_group[(decision.task_id, group.local_group_id)] = proposal_id
                output.append(
                    ParentProposalCard(
                        proposal_ref=f"P{len(output) + 1}",
                        proposal_id=proposal_id,
                        supporting_proposal_ids=[proposal_id],
                        scope=group.scope,
                        package_family=family,
                        label=group.label,
                        atomic_refs=[item.atomic_ref for item in member_slices],
                        event_ids=event_ids,
                        membership_by_event={
                            slices[item.atomic_ref].event_id: item.membership_relation
                            for item in group.members
                        },
                        document_refs=[document.document_ref],
                        participants=list(signature.issuer_ids),
                        period_ids=list(signature.period_ids),
                        parent_role=signature.role.value,
                        object_cues=list(signature.object_ids),
                        artifact_cues=list(signature.artifact_ids),
                        metric_cues=list(signature.metric_ids),
                        institution_cues=list(signature.institution_ids),
                        counterparty_cues=list(signature.counterparty_ids),
                        market_scope=list(signature.market_scope),
                        representative_facts=_dedupe(
                            [item.proposition for item in member_slices], limit=6
                        ),
                    )
                )
        external_links: list[ParentExternalLinkProposal] = []
        seen_links: set[tuple[str, str, str]] = set()
        for decision in decisions:
            document = documents[decision.task_id.split(":S", maxsplit=1)[0]]
            for group in decision.groups:
                for link in group.external_links:
                    source_event_id = slices[link.source_atomic_ref].event_id
                    target_proposal_id = proposal_by_local_group.get(
                        (decision.task_id, link.target_local_group_id)
                    )
                    if target_proposal_id is None:
                        continue
                    key = (source_event_id, target_proposal_id, link.relation.value)
                    if key in seen_links:
                        continue
                    seen_links.add(key)
                    external_links.append(
                        ParentExternalLinkProposal(
                            source_event_id=source_event_id,
                            target_proposal_id=target_proposal_id,
                            relation=link.relation,
                            document_ref=document.document_ref,
                        )
                    )
        unique_output: dict[str, ParentProposalCard] = {}
        for item in output:
            existing = unique_output.get(item.proposal_id)
            if existing is None:
                unique_output[item.proposal_id] = item
                continue
            if existing.model_dump(exclude={"proposal_ref"}) != item.model_dump(
                exclude={"proposal_ref"}
            ):
                raise ValueError(
                    f"parent proposal ID collision for {item.proposal_id!r}"
                )
        output = [
            item.model_copy(update={"proposal_ref": f"P{index}"})
            for index, item in enumerate(
                sorted(unique_output.values(), key=lambda value: value.proposal_id), start=1
            )
        ]
        return output, external_links

    @staticmethod
    def _group_requires_repartition(
        group: ParentInductionGroup,
        slices: Mapping[str, AtomicDocumentSlice],
        signatures_by_event: Mapping[str, ParentBoundarySignature],
    ) -> bool:
        signatures = [
            signatures_by_event[slices[item.atomic_ref].event_id]
            for item in group.members
            if item.atomic_ref in slices and slices[item.atomic_ref].event_id in signatures_by_event
        ]
        return any(
            proposal_merge_guard(left, right).status is MergeGuardStatus.REVIEW
            for index, left in enumerate(signatures)
            for right in signatures[index + 1 :]
        )

    def _repartition_suspect_groups(
        self,
        decisions: Sequence[ParentInductionDecision],
        documents_by_task: Mapping[str, ParentInductionDocument],
        slices: Mapping[str, AtomicDocumentSlice],
        signatures_by_event: Mapping[str, ParentBoundarySignature],
        sources_by_id: Mapping[str, SourceMessage] | None = None,
        *,
        models: Any,
        run_id: str,
        checkpoint_scope_id: str,
    ) -> tuple[list[ParentInductionDecision], list[ParentStageFailure], dict[str, int]]:
        suspect_documents: list[ParentInductionDocument] = []
        suspect_keys: list[tuple[str, str]] = []
        for decision in decisions:
            source_document = documents_by_task[decision.task_id.split(":S", maxsplit=1)[0]]
            atomics_by_ref = {item.atomic_ref: item for item in source_document.atomics}
            for group in decision.groups:
                if not self._group_requires_repartition(group, slices, signatures_by_event):
                    continue
                refs = {item.atomic_ref for item in group.members}
                atomics = [atomics_by_ref[ref] for ref in sorted(refs)]
                used_blocks = {ref for item in atomics for ref in item.evidence_refs}
                selected_context = [
                    item
                    for item in source_document.document_context
                    if item.block_ref in used_blocks
                ]
                source = (sources_by_id or {}).get(source_document.document_ref)
                if source is not None and len(source.text) > 12_000:
                    evidence_by_atomic = {
                        item.atomic_ref: [
                            block.text
                            for block in selected_context
                            if block.block_ref in item.evidence_refs
                        ]
                        or [item.proposition]
                        for item in atomics
                    }
                    selected_context, widened_refs = self._document_context(
                        source,
                        evidence_by_atomic,
                        soft_character_budget=self.context_soft_token_budget * 8,
                        window_radius=960,
                    )
                    atomics = [
                        item.model_copy(update={"evidence_refs": widened_refs[item.atomic_ref]})
                        for item in atomics
                    ]
                task_id = f"{decision.task_id}:R{len(suspect_documents) + 1}"
                suspect_documents.append(
                    source_document.model_copy(
                        update={
                            "task_id": task_id,
                            "document_context": selected_context,
                            "atomics": atomics,
                        }
                    )
                )
                suspect_keys.append((decision.task_id, group.local_group_id))
        if not suspect_documents:
            return list(decisions), [], {"suspect_group_count": 0, "repartition_request_count": 0}

        repaired, failures, telemetry = self._induce(
            suspect_documents,
            models=models,
            run_id=run_id,
            checkpoint_scope_id=checkpoint_scope_id,
            prompt_suffix=(
                "\nRepartition only these Atomics using the same parent definition. Review the "
                "evidence for the whole group. A differing field or event family is diagnostic, "
                "not an automatic split."
            ),
            model_stage="parent_induction_repartition",
        )
        repaired_by_task = {item.task_id: item for item in repaired}
        failed_tasks = {item.task_id for item in failures}
        replacement_by_key: dict[tuple[str, str], list[ParentInductionGroup]] = {}
        for index, (document, key) in enumerate(
            zip(suspect_documents, suspect_keys, strict=True), start=1
        ):
            original = next(
                group
                for decision in decisions
                if decision.task_id == key[0]
                for group in decision.groups
                if group.local_group_id == key[1]
            )
            repaired_decision = repaired_by_task.get(document.task_id)
            if repaired_decision is None or document.task_id in failed_tasks:
                replacement_by_key[key] = [original]
                continue
            prefix = f"{original.local_group_id}_R{index}_"
            id_map = {
                group.local_group_id: f"{prefix}{offset}"
                for offset, group in enumerate(repaired_decision.groups, start=1)
            }
            replacement_by_key[key] = [
                group.model_copy(
                    update={
                        "local_group_id": id_map[group.local_group_id],
                        "external_links": [
                            link.model_copy(
                                update={"target_local_group_id": id_map[link.target_local_group_id]}
                            )
                            for link in group.external_links
                        ],
                    }
                )
                for group in repaired_decision.groups
            ]
        output: list[ParentInductionDecision] = []
        for decision in decisions:
            remapped_groups: list[ParentInductionGroup] = []
            for group in decision.groups:
                replacements = replacement_by_key.get(
                    (decision.task_id, group.local_group_id), [group]
                )
                for replacement in replacements:
                    retained_links = []
                    for link in replacement.external_links:
                        target_replacements = replacement_by_key.get(
                            (decision.task_id, link.target_local_group_id)
                        )
                        if target_replacements is None:
                            retained_links.append(link)
                        elif len(target_replacements) == 1:
                            retained_links.append(
                                link.model_copy(
                                    update={
                                        "target_local_group_id": target_replacements[
                                            0
                                        ].local_group_id
                                    }
                                )
                            )
                        # A relation to a group that split into several parents is ambiguous.
                        # It is non-membership metadata, so omit it instead of guessing or
                        # blocking the document.
                    remapped_groups.append(
                        replacement.model_copy(update={"external_links": retained_links})
                    )
            output.append(decision.model_copy(update={"groups": remapped_groups}))
        request_count = telemetry.get("induction_request_count", 0)
        model_wall_ms = telemetry.get("model_wall_ms", 0)
        return (
            output,
            failures,
            {
                "suspect_group_count": len(suspect_documents),
                "repartition_request_count": (
                    request_count if isinstance(request_count, int) else 0
                ),
                "repartition_failure_count": len(failures),
                "model_wall_ms": model_wall_ms if isinstance(model_wall_ms, int) else 0,
            },
        )

    @staticmethod
    def _fallback_induction_decision(
        document: ParentInductionDocument,
        slices: Mapping[str, AtomicDocumentSlice],
        signatures: Mapping[str, ParentBoundarySignature],
    ) -> ParentInductionDecision:
        """Keep provider failure local without manufacturing one singleton per Atomic."""

        buckets: list[list[AtomicDocumentSlice]] = []
        for atomic in sorted(document.atomics, key=lambda item: item.atomic_ref):
            signature = signatures[slices[atomic.atomic_ref].event_id]
            selected: list[AtomicDocumentSlice] | None = None
            for bucket in buckets:
                bucket_signatures = [
                    signatures[slices[item.atomic_ref].event_id] for item in bucket
                ]
                shares_issuer = any(
                    set(signature.issuer_ids).intersection(item.issuer_ids)
                    for item in bucket_signatures
                )
                same_role = all(signature.role is item.role for item in bucket_signatures)
                compatible = all(
                    proposal_merge_guard(signature, item).status is MergeGuardStatus.PASS
                    for item in bucket_signatures
                )
                if shares_issuer and same_role and compatible:
                    selected = bucket
                    break
            if selected is None:
                selected = []
                buckets.append(selected)
            selected.append(atomic)
        return ParentInductionDecision(
            task_id=document.task_id,
            groups=[
                ParentInductionGroup(
                    local_group_id=f"fallback-{index}",
                    scope="PARENT_OCCURRENCE",
                    label=group[0].proposition,
                    members=[
                        ParentMembershipDecision(
                            atomic_ref=item.atomic_ref,
                            membership_relation=MembershipRelation.COMPONENT_OF,
                        )
                        for item in group
                    ],
                )
                for index, group in enumerate(buckets, start=1)
            ],
        )

    def build_parent_occurrence_pool(
        self,
        *,
        events: Sequence[AtomicEvent],
        mentions: Sequence[EventMention] | None,
        sources: Sequence[SourceMessage] | None,
        existing_packages: Sequence[EventPackage],
        models: Any,
        run_id: str,
        persistence_scope_id: str | None = None,
    ) -> ParentOccurrencePoolResult:
        """Run only the retained document-local induction and compile Parent Occurrences."""

        persistence_scope_id = persistence_scope_id or run_id
        snapshot = PackageStageSnapshotV2.load(
            registry=self.registry,
            events=events,
            mentions=mentions,
            sources=sources,
            packages=existing_packages,
        )
        events_by_id = {item.event_id: item for item in events}
        documents, slices, signatures, signal_telemetry = self._slices(snapshot)
        decisions, induction_failures, induction_telemetry = self._induce(
            documents,
            models=models,
            run_id=run_id,
            checkpoint_scope_id=persistence_scope_id,
        )
        if induction_failures and not decisions:
            return ParentOccurrencePoolResult(
                status="PARTIAL_PARENT_INDUCTION",
                snapshot_hash=snapshot.snapshot_hash,
                failures=tuple(induction_failures),
                telemetry={
                    "snapshot_query_count": snapshot.query_count,
                    "snapshot_load_ms": snapshot.load_ms,
                    **induction_telemetry,
                    **signal_telemetry,
                },
            )
        documents_by_task = {item.task_id: item for item in documents}
        decided_tasks = {item.task_id for item in decisions}
        for document in documents:
            if document.task_id not in decided_tasks:
                decisions.append(
                    self._fallback_induction_decision(document, slices, signatures)
                )
        proposals, external_links = self._proposals(
            decisions, documents_by_task, slices, events_by_id, signatures
        )
        proposal_write = self.registry.save_parent_occurrence_proposals(
            run_id=persistence_scope_id,
            records=[item.model_dump(mode="json") for item in proposals],
        )
        return ParentOccurrencePoolResult(
            status="FINALIZED",
            snapshot_hash=snapshot.snapshot_hash,
            proposals=tuple(proposals),
            external_links=tuple(external_links),
            failures=tuple(induction_failures),
            telemetry={
                "snapshot_query_count": snapshot.query_count,
                "snapshot_load_ms": snapshot.load_ms,
                **induction_telemetry,
                **signal_telemetry,
                "repartition_enabled": False,
                "suspect_group_count": 0,
                "repartition_request_count": 0,
                "repartition_repair_count": 0,
                "proposal_write": proposal_write,
                "parent_occurrence_count": len(proposals),
                "external_relation_count": len(external_links),
            },
        )
