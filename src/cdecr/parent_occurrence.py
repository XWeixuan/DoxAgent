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

from cdecr.bulk_epoch.embedding import EmbeddingBatchExecutor, EmbeddingWorkItem
from cdecr.contracts import (
    AtomicEvent,
    EventMention,
    EventPackage,
    ExternalRelationType,
    MembershipRelation,
    PackageFamily,
    SourceMessage,
)
from cdecr.field_coreference_contracts import CanonicalFieldLink, CanonicalFieldRegistryEntry
from cdecr.models import ModelTier
from cdecr.parent_occurrence_contracts import (
    AtomicDocumentSlice,
    CanonicalParentPrototype,
    FrozenParentExternalLink,
    FrozenParentGroup,
    FrozenParentPartition,
    ParentContextBlock,
    ParentExternalLinkProposal,
    ParentInductionBatch,
    ParentInductionDecision,
    ParentInductionDocument,
    ParentInductionGroup,
    ParentMembershipDecision,
    ParentOccurrenceStageResult,
    ParentProposalCard,
    ParentResolutionBatch,
    ParentStageFailure,
)
from cdecr.parent_occurrence_signals import (
    CandidateEdge,
    MergeGuardStatus,
    ParentBoundarySignature,
    ParentRole,
    aggregate_signatures,
    build_candidate_graph,
    compile_parent_signatures,
    cosine,
    proposal_merge_guard,
    weighted_microcomponents,
)
from cdecr.ports import CDECRRegistry, DecisionAuditRecord, StructuredModelRequest

_T = TypeVar("_T", bound=BaseModel)
_PROMPT_ROOT = Path(__file__).parent / "prompts" / "v1"
_CHECKPOINT_CONTRACT_VERSION = "parent-occurrence-v2.0r-contract-2"
_SIGNAL_COMPILER_VERSION = "parent-occurrence-v2.0r-signal-2"
_CANDIDATE_POLICY_VERSION = "parent-occurrence-v2.0r-broad-recall-2"


def _hash(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _short(prefix: str, value: object) -> str:
    return f"{prefix}:{_hash(value)[:24]}"


def _partition_snapshot_hash(snapshot_hash: str) -> str:
    """Version the immutable partition cache with every decision contract input."""

    return _hash(
        {
            "input_snapshot": snapshot_hash,
            "contract": _CHECKPOINT_CONTRACT_VERSION,
            "compiler": _SIGNAL_COMPILER_VERSION,
            "candidate_policy": _CANDIDATE_POLICY_VERSION,
            "induction_prompt": (
                _PROMPT_ROOT / "parent_occurrence_induction.md"
            ).read_text(encoding="utf-8"),
            "resolution_prompt": (
                _PROMPT_ROOT / "parent_occurrence_resolution.md"
            ).read_text(encoding="utf-8"),
            "induction_schema": ParentInductionBatch.model_json_schema(),
            "resolution_schema": ParentResolutionBatch.model_json_schema(),
        }
    )


def _dedupe(values: Sequence[str], *, limit: int | None = None) -> list[str]:
    output = list(dict.fromkeys(value.strip() for value in values if value and value.strip()))
    return output if limit is None else output[:limit]


def _as_int(value: object) -> int:
    return value if isinstance(value, int) else 0


def _string_set(value: object) -> set[str]:
    if not isinstance(value, (list, tuple, set, frozenset)):
        return set()
    return {str(item) for item in value}


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


def _event_participants(event: AtomicEvent) -> list[str]:
    fields = event.identity_profile.fields.model_dump(mode="json")
    values: list[str] = []
    for key, value in fields.items():
        if "issuer" not in key and "company" not in key and "participant" not in key:
            continue
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, list):
            values.extend(str(item) for item in value)
    return _dedupe(values)


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


@dataclass
class _ResolvedParent:
    group_id: str
    proposal_ids: list[str]
    existing_package_ids: list[str]
    canonical_label: str


class ParentOccurrenceService:
    """The sole bulk and incremental Package business service."""

    def __init__(
        self,
        *,
        registry: CDECRRegistry,
        induction_active_requests: int = 96,
        resolution_active_requests: int = 128,
        reconcile_active_requests: int = 96,
        induction_max_documents: int = 4,
        induction_max_slices: int = 48,
        resolution_max_proposals: int = 24,
        resolution_max_existing_parents: int = 12,
        resolution_max_input_tokens: int = 12_000,
        route_structured_quota: int = 16,
        route_semantic_quota: int = 24,
        route_total_k: int = 32,
        context_soft_token_budget: int = 6_000,
    ) -> None:
        self.registry = registry
        self.induction_active_requests = max(1, induction_active_requests)
        self.resolution_active_requests = max(1, resolution_active_requests)
        self.reconcile_active_requests = max(1, reconcile_active_requests)
        self.induction_max_documents = max(1, induction_max_documents)
        self.induction_max_slices = max(1, min(96, induction_max_slices))
        self.resolution_max_proposals = max(2, resolution_max_proposals)
        self.resolution_max_existing_parents = max(0, resolution_max_existing_parents)
        self.resolution_max_input_tokens = max(2_000, resolution_max_input_tokens)
        self.route_structured_quota = max(4, route_structured_quota)
        self.route_semantic_quota = max(2, route_semantic_quota)
        self.route_total_k = max(2, route_total_k)
        self.context_soft_token_budget = max(1_000, context_soft_token_budget)

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
            size = len(document.model_dump_json().encode("utf-8"))
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
    ) -> tuple[list[ParentInductionDecision], list[ParentStageFailure], dict[str, int]]:
        prompt = self._prompt("parent_occurrence_induction.md") + prompt_suffix
        schema = ParentInductionBatch.model_json_schema()
        input_hashes = {
            item.task_id: _hash(
                {
                    "contract_version": _CHECKPOINT_CONTRACT_VERSION,
                    "compiler_version": _SIGNAL_COMPILER_VERSION,
                    "prompt": prompt,
                    "schema": schema,
                    "document": item.model_dump(mode="json"),
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
                            {"document_tasks": [item.model_dump(mode="json") for item in batch]},
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
                        "input_hash": _hash([item.model_dump(mode="json") for item in batch]),
                        "output_hash": _hash(result.model_dump(mode="json")),
                        "partition": result.model_dump(mode="json"),
                    },
                )
            )

        requests, validators = requests_for(batches)
        results = models.typed_many(
            tier=ModelTier.M3,
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
            tier=ModelTier.M3,
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
        return (
            output,
            failures,
            {
                "suspect_group_count": len(suspect_documents),
                "repartition_request_count": int(telemetry.get("induction_request_count", 0)),
                "repartition_failure_count": len(failures),
                "model_wall_ms": int(telemetry.get("model_wall_ms", 0)),
            },
        )

    @staticmethod
    def _prototypes(
        packages: Sequence[EventPackage],
        events_by_id: Mapping[str, AtomicEvent],
        signatures_by_event: Mapping[str, ParentBoundarySignature],
    ) -> list[CanonicalParentPrototype]:
        output: list[CanonicalParentPrototype] = []
        for package in sorted(packages, key=lambda item: item.package_id):
            events = [
                events_by_id[item] for item in package.member_event_ids if item in events_by_id
            ]
            signature = aggregate_signatures(
                [
                    signatures_by_event[item.event_id]
                    for item in events
                    if item.event_id in signatures_by_event
                ]
            )
            output.append(
                CanonicalParentPrototype(
                    parent_ref=f"H{len(output) + 1}",
                    package_id=package.package_id,
                    scope=package.parent_scope,
                    package_family=package.package_family,
                    canonical_label=package.canonical_title,
                    event_ids=sorted(package.member_event_ids),
                    participants=list(signature.issuer_ids),
                    period_ids=list(signature.period_ids),
                    parent_role=signature.role.value,
                    object_cues=list(signature.object_ids),
                    artifact_cues=list(signature.artifact_ids),
                    institution_cues=list(signature.institution_ids),
                    counterparty_cues=list(signature.counterparty_ids),
                    market_scope=list(signature.market_scope),
                    representative_facts=_dedupe(
                        [event.canonical_proposition for event in events], limit=6
                    ),
                )
            )
        return output

    def _embed_cards(
        self,
        proposals: Sequence[ParentProposalCard],
        prototypes: Sequence[CanonicalParentPrototype],
        *,
        models: Any,
    ) -> tuple[list[ParentProposalCard], list[CanonicalParentPrototype], dict[str, int]]:
        cards: list[tuple[str, str, str]] = []
        for proposal in proposals:
            text = " | ".join(
                [
                    proposal.label,
                    proposal.package_family.value,
                    proposal.parent_role,
                    *proposal.participants,
                    *proposal.period_ids,
                    *proposal.object_cues,
                    *proposal.artifact_cues,
                    *proposal.metric_cues,
                    *proposal.institution_cues,
                    *proposal.counterparty_cues,
                    *proposal.market_scope,
                    *proposal.representative_facts,
                ]
            )
            cards.append(("parent_proposal", proposal.proposal_id, text))
        for prototype in prototypes:
            text = " | ".join(
                [
                    prototype.canonical_label,
                    prototype.package_family.value,
                    prototype.parent_role,
                    *prototype.participants,
                    *prototype.period_ids,
                    *prototype.object_cues,
                    *prototype.artifact_cues,
                    *prototype.institution_cues,
                    *prototype.counterparty_cues,
                    *prototype.market_scope,
                    *prototype.representative_facts,
                ]
            )
            cards.append(("parent_prototype", prototype.package_id, text))
        vectors: dict[tuple[str, str], list[float]] = {}
        misses: list[tuple[str, str, str]] = []
        cache_hits = 0
        for owner_kind, owner_id, text in cards:
            input_hash = _hash({"compiler": _SIGNAL_COMPILER_VERSION, "text": text})
            cached = self.registry.get_embedding(
                owner_kind=owner_kind,
                owner_id=owner_id,
                model=models.model_m1,
                input_hash=input_hash,
            )
            if cached is None:
                misses.append((owner_kind, owner_id, text))
            else:
                vectors[(owner_kind, owner_id)] = list(cached.vector)
                cache_hits += 1
        model_wall_ms = 0
        embedding_batch_count = 0
        embedding_retry_count = 0
        embedding_failure_count = 0
        if misses:
            model_started = perf_counter()
            executor = EmbeddingBatchExecutor(
                preferred_batch_size=10,
                fallback_batch_size=5,
                active_requests=4,
            )
            for owner_kind in sorted({item[0] for item in misses}):
                generated, telemetry = executor.run(
                    items=[
                        EmbeddingWorkItem(
                            owner_id=owner_id,
                            text=text,
                            input_hash=_hash({"compiler": _SIGNAL_COMPILER_VERSION, "text": text}),
                        )
                        for kind, owner_id, text in misses
                        if kind == owner_kind
                    ],
                    models=models,
                    registry=self.registry,
                    owner_kind=owner_kind,
                    stage="parent_profile_embedding",
                )
                vectors.update(
                    {(owner_kind, owner_id): vector for owner_id, vector in generated.items()}
                )
                embedding_batch_count += telemetry.batch_count
                embedding_retry_count += telemetry.retry_count
                embedding_failure_count += len(telemetry.failed_owner_ids)
            model_wall_ms = round((perf_counter() - model_started) * 1000)
        proposal_values = [
            item.model_copy(
                update={"embedding": vectors.get(("parent_proposal", item.proposal_id), [])}
            )
            for item in proposals
        ]
        prototype_values = [
            item.model_copy(
                update={"embedding": vectors.get(("parent_prototype", item.package_id), [])}
            )
            for item in prototypes
        ]
        return (
            proposal_values,
            prototype_values,
            {
                "embedding_cache_hit_count": cache_hits,
                "embedding_cache_miss_count": len(misses),
                "embedding_model_wall_ms": model_wall_ms,
                "embedding_batch_count": embedding_batch_count,
                "embedding_retry_count": embedding_retry_count,
                "embedding_failure_count": embedding_failure_count,
            },
        )

    @staticmethod
    def _card_signature(
        item: ParentProposalCard | CanonicalParentPrototype,
    ) -> ParentBoundarySignature:
        try:
            role = ParentRole(item.parent_role)
        except ValueError:
            role = ParentRole.OTHER
        return ParentBoundarySignature(
            role=role,
            event_ids=tuple(sorted(set(item.event_ids))),
            issuer_ids=tuple(sorted(set(item.participants))),
            period_ids=tuple(sorted(set(item.period_ids))),
            artifact_ids=tuple(sorted(set(item.artifact_cues))),
            institution_ids=tuple(sorted(set(item.institution_cues))),
            object_ids=tuple(sorted(set(item.object_cues))),
            metric_ids=(
                tuple(sorted(set(item.metric_cues))) if isinstance(item, ParentProposalCard) else ()
            ),
            market_scope=tuple(sorted(set(item.market_scope))),
            counterparty_ids=tuple(sorted(set(item.counterparty_cues))),
            family_ids=(item.package_family.value,),
            source_ids=(
                tuple(sorted(set(item.document_refs)))
                if isinstance(item, ParentProposalCard)
                else ()
            ),
        )

    @staticmethod
    def _stable_card_id(
        item: ParentProposalCard | CanonicalParentPrototype,
    ) -> str:
        return item.proposal_id if isinstance(item, ParentProposalCard) else item.package_id

    def _resolution_tasks(
        self,
        proposals: Sequence[ParentProposalCard],
        prototypes: Sequence[CanonicalParentPrototype],
        *,
        inherited_bridge_ledger: Sequence[tuple[str, str, float]] = (),
    ) -> tuple[
        list[tuple[list[ParentProposalCard], list[CanonicalParentPrototype]]],
        dict[str, object],
        list[tuple[str, str, float]],
    ]:
        proposal_by_ref = {item.proposal_ref: item for item in proposals}
        prototype_by_ref = {item.parent_ref: item for item in prototypes}
        all_cards: dict[str, ParentProposalCard | CanonicalParentPrototype] = {
            **proposal_by_ref,
            **prototype_by_ref,
        }
        refs = sorted(all_cards)
        signatures = {ref: self._card_signature(item) for ref, item in all_cards.items()}
        embeddings = {ref: item.embedding for ref, item in all_cards.items()}
        graph = build_candidate_graph(
            refs,
            signatures=signatures,
            embeddings=embeddings,
            max_structured_neighbors=self.route_structured_quota,
            max_semantic_neighbors=self.route_semantic_quota,
            max_edges_per_ref=self.route_total_k,
        )
        stable_to_refs: dict[str, list[str]] = defaultdict(list)
        for ref, item in all_cards.items():
            stable_to_refs[self._stable_card_id(item)].append(ref)
            if isinstance(item, ParentProposalCard):
                for supporting_id in item.supporting_proposal_ids:
                    stable_to_refs[supporting_id].append(ref)
        extra_edges: list[CandidateEdge] = []
        for left_id, right_id, strength in inherited_bridge_ledger:
            for left_ref in stable_to_refs.get(left_id, ()):
                for right_ref in stable_to_refs.get(right_id, ()):
                    if left_ref == right_ref:
                        continue
                    pair = tuple(sorted((left_ref, right_ref)))
                    extra_edges.append(
                        CandidateEdge(
                            left_ref=pair[0],
                            right_ref=pair[1],
                            strength=max(0.01, strength),
                            routes=("bridge_ledger",),
                            semantic_score=cosine(
                                embeddings.get(pair[0], ()), embeddings.get(pair[1], ())
                            ),
                        )
                    )
        edge_by_key = {edge.key: edge for edge in [*graph.edges, *extra_edges]}
        components, cut_edges = weighted_microcomponents(
            refs,
            tuple(edge_by_key.values()),
            max_size=self.resolution_max_proposals,
        )
        token_split_count = 0
        token_cut_edges: list[CandidateEdge] = []
        bounded_components: list[list[str]] = []
        pending_components = list(components)
        while pending_components:
            component = pending_components.pop(0)
            estimated_tokens = (
                sum(len(all_cards[ref].model_dump_json()) for ref in component) + 800
            ) // 4
            if estimated_tokens <= self.resolution_max_input_tokens or len(component) < 4:
                bounded_components.append(component)
                continue
            component_refs = set(component)
            internal_edges = [
                edge
                for edge in edge_by_key.values()
                if edge.left_ref in component_refs and edge.right_ref in component_refs
            ]
            split_components, split_edges = weighted_microcomponents(
                component,
                internal_edges,
                max_size=max(2, len(component) // 2),
            )
            if len(split_components) < 2:
                ordered = sorted(
                    component, key=lambda ref: self._stable_card_id(all_cards[ref])
                )
                midpoint = len(ordered) // 2
                split_components = [ordered[:midpoint], ordered[midpoint:]]
                left_refs = set(split_components[0])
                split_edges = [
                    edge
                    for edge in internal_edges
                    if (edge.left_ref in left_refs) != (edge.right_ref in left_refs)
                ]
            pending_components.extend(split_components)
            token_cut_edges.extend(split_edges)
            token_split_count += 1
        components = bounded_components
        tasks: list[tuple[list[ParentProposalCard], list[CanonicalParentPrototype]]] = []
        for component in components:
            if len(component) < 2:
                continue
            task_proposals = [proposal_by_ref[ref] for ref in component if ref in proposal_by_ref]
            if not task_proposals:
                continue
            task_prototypes = [
                prototype_by_ref[ref] for ref in component if ref in prototype_by_ref
            ][: self.resolution_max_existing_parents]
            tasks.append((task_proposals, task_prototypes))
        stable_ledger = [
            (
                self._stable_card_id(all_cards[edge.left_ref]),
                self._stable_card_id(all_cards[edge.right_ref]),
                edge.strength,
            )
            for edge in [*cut_edges, *token_cut_edges]
        ]
        covered_refs = {item.proposal_ref for task, _ in tasks for item in task}
        lineage_universe = {
            lineage_id
            for item in proposals
            for lineage_id in (item.supporting_proposal_ids or [item.proposal_id])
        }
        covered_lineage = {
            lineage_id
            for ref in covered_refs
            for lineage_id in (
                proposal_by_ref[ref].supporting_proposal_ids
                or [proposal_by_ref[ref].proposal_id]
            )
        }
        proposal_coverage_bps = (
            round(len(covered_lineage) * 10000 / len(lineage_universe))
            if lineage_universe
            else 10000
        )
        return (
            tasks,
            {
                "candidate_proposal_count": len(covered_refs),
                "candidate_pair_evaluation_count": graph.semantic_pair_evaluations,
                "candidate_coverage_bps": proposal_coverage_bps,
                "covered_lineage_ids": sorted(covered_lineage),
                "lineage_universe_ids": sorted(lineage_universe),
                "qualified_neighbor_proposal_count": len(covered_refs),
                "no_qualified_neighbor_proposal_count": len(proposals) - len(covered_refs),
                "eligible_candidate_edge_count": graph.eligible_edge_count,
                "selected_candidate_edge_count": graph.selected_edge_count,
                "truncated_candidate_edge_count": graph.truncated_edge_count,
                "candidate_route_counts": graph.route_counts,
                "hard_negative_pair_count": 0,
                "bridge_ledger_count": len(stable_ledger),
                "token_budget_split_count": token_split_count,
                "resolution_max_input_tokens": self.resolution_max_input_tokens,
            },
            stable_ledger,
        )

    @staticmethod
    def _validate_resolution(
        value: ParentResolutionBatch,
        proposals: Sequence[ParentProposalCard],
        prototypes: Sequence[CanonicalParentPrototype],
    ) -> None:
        expected = {item.proposal_ref for item in proposals}
        actual = {ref for group in value.groups for ref in group.proposal_refs}
        if actual != expected:
            raise ValueError("resolution groups must cover every input proposal exactly once")
        allowed_parents = {item.parent_ref for item in prototypes}
        actual_parents = {ref for group in value.groups for ref in group.existing_parent_refs}
        if not actual_parents.issubset(allowed_parents):
            raise ValueError("resolution output contains an unknown prototype ref")

    def _resolve_wave(
        self,
        proposals: Sequence[ParentProposalCard],
        prototypes: Sequence[CanonicalParentPrototype],
        *,
        models: Any,
        run_id: str,
        checkpoint_scope_id: str,
        stage: str,
        inherited_bridge_ledger: Sequence[tuple[str, str, float]] = (),
        forced_tasks: Sequence[tuple[list[ParentProposalCard], list[CanonicalParentPrototype]]]
        | None = None,
        prompt_suffix: str = "",
    ) -> tuple[
        list[_ResolvedParent],
        list[ParentStageFailure],
        dict[str, object],
        list[tuple[str, str, float]],
    ]:
        if forced_tasks is None:
            tasks, recall_telemetry, bridge_ledger = self._resolution_tasks(
                proposals,
                prototypes,
                inherited_bridge_ledger=inherited_bridge_ledger,
            )
        else:
            tasks = list(forced_tasks)
            bridge_ledger = list(inherited_bridge_ledger)
            covered = {item.proposal_ref for task, _ in tasks for item in task}
            lineage_universe = {
                lineage_id
                for item in proposals
                for lineage_id in (item.supporting_proposal_ids or [item.proposal_id])
            }
            covered_lineage = {
                lineage_id
                for task, _ in tasks
                for item in task
                for lineage_id in (item.supporting_proposal_ids or [item.proposal_id])
            }
            recall_telemetry = {
                "candidate_proposal_count": len(covered),
                "candidate_pair_evaluation_count": 0,
                "candidate_coverage_bps": (
                    round(len(covered_lineage) * 10000 / len(lineage_universe))
                    if lineage_universe
                    else 10000
                ),
                "covered_lineage_ids": sorted(covered_lineage),
                "lineage_universe_ids": sorted(lineage_universe),
                "qualified_neighbor_proposal_count": len(covered),
                "no_qualified_neighbor_proposal_count": len(proposals) - len(covered),
                "eligible_candidate_edge_count": 0,
                "selected_candidate_edge_count": 0,
                "truncated_candidate_edge_count": 0,
                "candidate_route_counts": {},
                "hard_negative_pair_count": 0,
                "bridge_ledger_count": len(inherited_bridge_ledger),
            }
        assigned_refs = {item.proposal_ref for task, _ in tasks for item in task}
        resolved: list[_ResolvedParent] = [
            _ResolvedParent(
                group_id=_short("parent", item.proposal_id),
                proposal_ids=[item.proposal_id],
                existing_package_ids=item.existing_package_ids,
                canonical_label=item.label,
            )
            for item in proposals
            if item.proposal_ref not in assigned_refs
        ]
        task_payloads: list[dict[str, object]] = []
        for task_proposals, task_prototypes in tasks:
            task_payload: dict[str, object] = {
                "proposals": [item.model_dump(mode="json") for item in task_proposals],
                "prototypes": [item.model_dump(mode="json") for item in task_prototypes],
            }
            task_payloads.append(task_payload)
        resolution_prompt = self._prompt("parent_occurrence_resolution.md") + prompt_suffix
        resolution_schema = ParentResolutionBatch.model_json_schema()
        task_hashes = [
            _hash(
                {
                    "contract_version": _CHECKPOINT_CONTRACT_VERSION,
                    "compiler_version": _SIGNAL_COMPILER_VERSION,
                    "candidate_policy_version": _CANDIDATE_POLICY_VERSION,
                    "prompt": resolution_prompt,
                    "schema": resolution_schema,
                    "payload": item,
                }
            )
            for item in task_payloads
        ]
        stage_code: Literal[
            "PARENT_INDUCE", "PARENT_RESOLVE_R1", "PARENT_RESOLVE_R2", "PARENT_RECONCILE"
        ] = (
            "PARENT_RECONCILE"
            if "reconcile" in stage or "admission" in stage
            else "PARENT_RESOLVE_R2"
            if stage.endswith("r2")
            else "PARENT_RESOLVE_R1"
        )
        checkpoint_by_task = {
            str(item["task_id"]): item
            for item in self.registry.list_parent_occurrence_checkpoints(
                run_id=checkpoint_scope_id, stage=stage_code
            )
        }
        reused_results: dict[int, ParentResolutionBatch] = {}
        pending_indices: list[int] = []
        for index, task_hash in enumerate(task_hashes):
            task_id = f"{stage}:{index}"
            checkpoint = checkpoint_by_task.get(task_id)
            if (
                checkpoint is not None
                and checkpoint.get("status") == "SUCCEEDED"
                and checkpoint.get("input_hash") == task_hash
                and isinstance(checkpoint.get("payload"), dict)
            ):
                try:
                    value = ParentResolutionBatch.model_validate(checkpoint["payload"])
                    self._validate_resolution(value, *tasks[index])
                except (ValueError, TypeError):
                    pending_indices.append(index)
                else:
                    reused_results[index] = value
            else:
                pending_indices.append(index)

        requests = [
            StructuredModelRequest(
                system_prompt=resolution_prompt,
                user_prompt=json.dumps(
                    task_payloads[index], ensure_ascii=False, separators=(",", ":")
                ),
                json_schema=resolution_schema,
            )
            for index in pending_indices
        ]
        validators: list[Callable[[ParentResolutionBatch], None]] = [
            partial(
                self._validate_resolution,
                proposals=tasks[index][0],
                prototypes=tasks[index][1],
            )
            for index in pending_indices
        ]
        start_write = self.registry.save_parent_occurrence_checkpoints(
            run_id=checkpoint_scope_id,
            records=[
                {
                    "stage": stage_code,
                    "task_id": f"{stage}:{index}",
                    "input_hash": task_hashes[index],
                    "status": "RUNNING",
                }
                for index in pending_indices
            ],
        )
        model_started = perf_counter()
        pending_results = (
            models.typed_many(
                tier=ModelTier.M3,
                stage=stage,
                requests=requests,
                output_type=ParentResolutionBatch,
                validators=validators,
            )
            if requests
            else []
        )
        results_by_index: dict[int, ParentResolutionBatch | Exception] = dict(reused_results)
        results_by_index.update(zip(pending_indices, pending_results, strict=True))
        model_wall_ms = round((perf_counter() - model_started) * 1000)
        failures: list[ParentStageFailure] = []
        proposal_by_ref = {item.proposal_ref: item for item in proposals}
        prototype_by_ref = {item.parent_ref: item for item in prototypes}
        finish_records: list[dict[str, object]] = []
        audits: list[DecisionAuditRecord] = []
        for index, (task_proposals, task_prototypes) in enumerate(tasks):
            result = results_by_index[index]
            task_id = f"{stage}:{index}"
            if isinstance(result, Exception):
                error_code = str(getattr(result, "code", type(result).__name__))
                failures.append(
                    ParentStageFailure(
                        stage=stage_code,
                        task_id=task_id,
                        error_code=error_code,
                    )
                )
                finish_records.append(
                    {
                        "stage": stage_code,
                        "task_id": task_id,
                        "input_hash": task_hashes[index],
                        "status": "FAILED_RETRYABLE",
                        "error_code": error_code,
                    }
                )
                resolved.extend(
                    _ResolvedParent(
                        group_id=_short("parent", proposal.proposal_id),
                        proposal_ids=[proposal.proposal_id],
                        existing_package_ids=proposal.existing_package_ids,
                        canonical_label=proposal.label,
                    )
                    for proposal in task_proposals
                )
                continue
            for group in result.groups:
                proposal_ids = [proposal_by_ref[ref].proposal_id for ref in group.proposal_refs]
                package_ids = [
                    prototype_by_ref[ref].package_id
                    for ref in group.existing_parent_refs
                    if ref in prototype_by_ref
                ]
                resolved.append(
                    _ResolvedParent(
                        group_id=_short(
                            "parent",
                            {
                                "proposals": sorted(proposal_ids),
                                "packages": sorted(package_ids),
                            },
                        ),
                        proposal_ids=sorted(proposal_ids),
                        existing_package_ids=sorted(package_ids),
                        canonical_label=group.canonical_label,
                    )
                )
            if index in reused_results:
                continue
            finish_records.append(
                {
                    "stage": stage_code,
                    "task_id": task_id,
                    "input_hash": task_hashes[index],
                    "status": "SUCCEEDED",
                    "payload": result.model_dump(mode="json"),
                }
            )
            audits.append(
                DecisionAuditRecord(
                    audit_id=_short(
                        "audit-parent-resolution", {"run": run_id, "stage": stage, "batch": index}
                    ),
                    run_id=run_id,
                    decision_type=stage.upper(),
                    subject_id=f"batch:{index}",
                    payload={
                        "proposal_refs": [item.proposal_ref for item in task_proposals],
                        "prototype_refs": [item.parent_ref for item in task_prototypes],
                        "input_hash": task_hashes[index],
                        "output_hash": _hash(result.model_dump(mode="json")),
                        "partition": result.model_dump(mode="json"),
                    },
                )
            )
        finish_write = self.registry.save_parent_occurrence_checkpoints(
            run_id=checkpoint_scope_id, records=finish_records
        )
        audit_write = self.registry.append_decision_audits(audits, chunk_size=512)
        return (
            resolved,
            failures,
            {
                **recall_telemetry,
                "task_count": len(tasks),
                "request_count": len(pending_indices),
                "successful_request_count": len(tasks) - len(failures),
                "reused_task_count": len(reused_results),
                "resolved_group_count": len(resolved),
                "checkpoint_transaction_count": int(start_write.get("transactions", 0))
                + int(finish_write.get("transactions", 0)),
                "audit_transaction_count": int(audit_write.get("transactions", 0)),
                "model_wall_ms": model_wall_ms,
            },
            bridge_ledger,
        )

    def _resolved_guard_reasons(
        self,
        item: _ResolvedParent,
        proposals_by_id: Mapping[str, ParentProposalCard],
        prototypes_by_package: Mapping[str, CanonicalParentPrototype],
    ) -> tuple[str, ...]:
        cards: list[ParentProposalCard | CanonicalParentPrototype] = [
            proposals_by_id[value] for value in item.proposal_ids if value in proposals_by_id
        ]
        cards.extend(
            prototypes_by_package[value]
            for value in item.existing_package_ids
            if value in prototypes_by_package
        )
        reasons: set[str] = set()
        for index, left in enumerate(cards):
            left_signature = self._card_signature(left)
            for right in cards[index + 1 :]:
                decision = proposal_merge_guard(left_signature, self._card_signature(right))
                if decision.status is MergeGuardStatus.REVIEW:
                    reasons.update(decision.reasons)
        return tuple(sorted(reasons))

    def _repair_guarded_unions(
        self,
        resolved: Sequence[_ResolvedParent],
        proposals: Sequence[ParentProposalCard],
        prototypes: Sequence[CanonicalParentPrototype],
        *,
        models: Any,
        run_id: str,
        checkpoint_scope_id: str,
        wave: str,
    ) -> tuple[list[_ResolvedParent], list[ParentStageFailure], dict[str, object]]:
        """Rejudge only new unions with explicit proposal-level boundary evidence."""

        proposals_by_id = {item.proposal_id: item for item in proposals}
        prototypes_by_package = {item.package_id: item for item in prototypes}
        guarded = [
            item
            for item in resolved
            if self._resolved_guard_reasons(item, proposals_by_id, prototypes_by_package)
        ]
        if not guarded:
            return list(resolved), [], {
                "review_group_count": 0,
                "request_count": 0,
                "rejected_union_count": 0,
            }
        forced_tasks: list[
            tuple[list[ParentProposalCard], list[CanonicalParentPrototype]]
        ] = []
        for item in guarded:
            forced_tasks.append(
                (
                    [
                        proposals_by_id[value]
                        for value in item.proposal_ids
                        if value in proposals_by_id
                    ],
                    [
                        prototypes_by_package[value]
                        for value in item.existing_package_ids
                        if value in prototypes_by_package
                    ],
                )
            )
        guarded_proposal_ids = {
            proposal.proposal_id for task, _ in forced_tasks for proposal in task
        }
        flattened_proposals = [proposal for task, _ in forced_tasks for proposal in task]
        flattened_prototypes = list(
            {
                prototype.package_id: prototype
                for _, task in forced_tasks
                for prototype in task
            }.values()
        )
        repaired, failures, telemetry, _ = self._resolve_wave(
            flattened_proposals,
            flattened_prototypes,
            models=models,
            run_id=run_id,
            checkpoint_scope_id=checkpoint_scope_id,
            stage=f"parent_merge_admission_{wave}",
            forced_tasks=forced_tasks,
            prompt_suffix=(
                "\nRecheck only this proposed merge using the same parent definition. Keep "
                "different child facts together when they share one parent; separate only a "
                "materially different occurrence, report, agreement, reaction, or continuing "
                "matter."
            ),
        )
        repaired_by_input: dict[frozenset[str], list[_ResolvedParent]] = defaultdict(list)
        for item in repaired:
            task_key = next(
                (
                    frozenset(proposal.proposal_id for proposal in task)
                    for task, _ in forced_tasks
                    if set(item.proposal_ids).issubset(
                        {proposal.proposal_id for proposal in task}
                    )
                ),
                None,
            )
            if task_key is not None:
                repaired_by_input[task_key].append(item)
        accepted: list[_ResolvedParent] = [
            item
            for item in resolved
            if not set(item.proposal_ids).intersection(guarded_proposal_ids)
        ]
        rejected_union_count = 0
        for task, _ in forced_tasks:
            key = frozenset(proposal.proposal_id for proposal in task)
            candidate = repaired_by_input.get(key, [])
            if not candidate or any(
                self._resolved_guard_reasons(item, proposals_by_id, prototypes_by_package)
                for item in candidate
            ):
                rejected_union_count += 1
                accepted.extend(
                    _ResolvedParent(
                        group_id=_short("parent", proposal.proposal_id),
                        proposal_ids=[proposal.proposal_id],
                        existing_package_ids=proposal.existing_package_ids,
                        canonical_label=proposal.label,
                    )
                    for proposal in task
                )
            else:
                accepted.extend(candidate)
        return accepted, failures, {
            **telemetry,
            "review_group_count": len(guarded),
            "rejected_union_count": rejected_union_count,
        }

    @staticmethod
    def _reduce_proposals(
        resolved: Sequence[_ResolvedParent],
        proposals: Sequence[ParentProposalCard],
    ) -> list[ParentProposalCard]:
        by_id = {item.proposal_id: item for item in proposals}
        output: list[ParentProposalCard] = []
        for item in resolved:
            referenced = [by_id[value] for value in item.proposal_ids if value in by_id]
            members = list({member.proposal_id: member for member in referenced}.values())
            if not members:
                continue
            event_ids = _dedupe([value for member in members for value in member.event_ids])
            family = Counter(value.package_family for value in members).most_common(1)[0][0]
            scope = Counter(value.scope for value in members).most_common(1)[0][0]
            output.append(
                ParentProposalCard(
                    proposal_ref=f"P{len(output) + 1}",
                    proposal_id=_short(
                        "provisional-parent",
                        {
                            "compiler": _SIGNAL_COMPILER_VERSION,
                            "proposals": sorted(item.proposal_ids),
                            "events": event_ids,
                            "packages": sorted(item.existing_package_ids),
                            "label": item.canonical_label,
                            "scope": scope,
                            "family": family.value,
                        },
                    ),
                    supporting_proposal_ids=_dedupe(
                        [value for member in members for value in member.supporting_proposal_ids]
                    ),
                    scope=scope,
                    package_family=family,
                    label=item.canonical_label,
                    atomic_refs=_dedupe(
                        [value for member in members for value in member.atomic_refs]
                    ),
                    event_ids=event_ids,
                    membership_by_event={
                        key: value
                        for member in members
                        for key, value in member.membership_by_event.items()
                        if key in set(event_ids)
                    },
                    document_refs=_dedupe(
                        [value for member in members for value in member.document_refs]
                    ),
                    existing_package_ids=_dedupe(item.existing_package_ids),
                    participants=_dedupe(
                        [value for member in members for value in member.participants]
                    ),
                    period_ids=_dedupe(
                        [value for member in members for value in member.period_ids]
                    ),
                    parent_role=Counter(value.parent_role for value in members).most_common(1)[0][
                        0
                    ],
                    object_cues=_dedupe(
                        [value for member in members for value in member.object_cues]
                    ),
                    artifact_cues=_dedupe(
                        [value for member in members for value in member.artifact_cues]
                    ),
                    metric_cues=_dedupe(
                        [value for member in members for value in member.metric_cues]
                    ),
                    institution_cues=_dedupe(
                        [value for member in members for value in member.institution_cues]
                    ),
                    counterparty_cues=_dedupe(
                        [value for member in members for value in member.counterparty_cues]
                    ),
                    market_scope=_dedupe(
                        [value for member in members for value in member.market_scope]
                    ),
                    representative_facts=_dedupe(
                        [value for member in members for value in member.representative_facts],
                        limit=6,
                    ),
                    embedding=members[0].embedding,
                )
            )
        return output

    @staticmethod
    def _deduplicate_final_event_ownership(
        proposals: Sequence[ParentProposalCard],
        base_proposals: Sequence[ParentProposalCard],
        slices: Mapping[str, AtomicDocumentSlice],
    ) -> tuple[list[ParentProposalCard], int]:
        base_by_id = {item.proposal_id: item for item in base_proposals}
        owners_by_event: dict[str, list[int]] = defaultdict(list)
        for index, proposal in enumerate(proposals):
            for event_id in proposal.event_ids:
                owners_by_event[event_id].append(index)
        selected_owner: dict[str, int] = {}
        duplicate_count = 0
        for event_id, owners in owners_by_event.items():
            if len(owners) == 1:
                selected_owner[event_id] = owners[0]
                continue
            duplicate_count += len(owners) - 1

            def owner_key(index: int, *, current_event_id: str = event_id) -> tuple[int, int, str]:
                proposal = proposals[index]
                lineage_support = sum(
                    current_event_id in base_by_id[proposal_id].event_ids
                    for proposal_id in proposal.supporting_proposal_ids
                    if proposal_id in base_by_id
                )
                return (-lineage_support, len(proposal.event_ids), proposal.proposal_id)

            selected_owner[event_id] = min(owners, key=owner_key)
        output: list[ParentProposalCard] = []
        for index, proposal in enumerate(proposals):
            event_ids = [
                event_id
                for event_id in proposal.event_ids
                if selected_owner.get(event_id, index) == index
            ]
            if not event_ids:
                continue
            kept_events = set(event_ids)
            output.append(
                proposal.model_copy(
                    update={
                        "event_ids": event_ids,
                        "atomic_refs": [
                            atomic_ref
                            for atomic_ref in proposal.atomic_refs
                            if slices[atomic_ref].event_id in kept_events
                        ],
                        "membership_by_event": {
                            event_id: relation
                            for event_id, relation in proposal.membership_by_event.items()
                            if event_id in kept_events
                        },
                    }
                )
            )
        return output, duplicate_count

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

    def run(
        self,
        *,
        events: Sequence[AtomicEvent],
        mentions: Sequence[EventMention] | None,
        sources: Sequence[SourceMessage] | None,
        existing_packages: Sequence[EventPackage],
        models: Any,
        run_id: str,
        persistence_scope_id: str | None = None,
    ) -> ParentOccurrenceStageResult:
        started = perf_counter()
        persistence_scope_id = persistence_scope_id or run_id
        snapshot = PackageStageSnapshotV2.load(
            registry=self.registry,
            events=events,
            mentions=mentions,
            sources=sources,
            packages=existing_packages,
        )
        partition_snapshot_hash = _partition_snapshot_hash(snapshot.snapshot_hash)
        load_partition = getattr(
            self.registry, "get_parent_occurrence_partition_for_snapshot", None
        )
        if callable(load_partition):
            cached_payload = load_partition(
                run_id=persistence_scope_id,
                snapshot_hash=partition_snapshot_hash,
            )
            if isinstance(cached_payload, dict):
                cached_partition = FrozenParentPartition.model_validate(cached_payload)
                return ParentOccurrenceStageResult(
                    status="FINALIZED",
                    partition=cached_partition,
                    telemetry={
                        "snapshot_query_count": snapshot.query_count,
                        "snapshot_load_ms": snapshot.load_ms,
                        "resumed_from_frozen_partition": True,
                        "partition_hash": cached_partition.partition_hash,
                        "resolution_wave_count": 0,
                        "model_wall_ms": 0,
                    },
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
            return ParentOccurrenceStageResult(
                status="PARTIAL_PARENT_RESOLUTION",
                failures=induction_failures,
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
            if document.task_id in decided_tasks:
                continue
            decisions.append(
                self._fallback_induction_decision(document, slices, signatures)
            )
        decisions, repartition_failures, repartition_telemetry = self._repartition_suspect_groups(
            decisions,
            documents_by_task,
            slices,
            signatures,
            sources_by_id=snapshot.sources_by_id,
            models=models,
            run_id=run_id,
            checkpoint_scope_id=persistence_scope_id,
        )
        all_failures = [*induction_failures, *repartition_failures]
        proposals, external_link_proposals = self._proposals(
            decisions, documents_by_task, slices, events_by_id, signatures
        )
        proposal_write = self.registry.save_parent_occurrence_proposals(
            run_id=persistence_scope_id,
            records=[item.model_dump(mode="json") for item in proposals],
        )
        prototypes = self._prototypes(existing_packages, events_by_id, signatures)
        proposals, prototypes, embedding_telemetry = self._embed_cards(
            proposals, prototypes, models=models
        )
        r1, r1_failures, r1_telemetry, bridge_ledger = self._resolve_wave(
            proposals,
            prototypes,
            models=models,
            run_id=run_id,
            checkpoint_scope_id=persistence_scope_id,
            stage="parent_resolution_r1",
        )
        all_failures.extend(r1_failures)
        r1, r1_admission_failures, r1_admission_telemetry = self._repair_guarded_unions(
            r1,
            proposals,
            prototypes,
            models=models,
            run_id=run_id,
            checkpoint_scope_id=persistence_scope_id,
            wave="r1",
        )
        all_failures.extend(r1_admission_failures)
        r1_proposals = self._reduce_proposals(r1, proposals)
        r1_proposals, prototypes, r2_embedding = self._embed_cards(
            r1_proposals, prototypes, models=models
        )
        r2, r2_failures, r2_telemetry, bridge_ledger = self._resolve_wave(
            r1_proposals,
            prototypes,
            models=models,
            run_id=run_id,
            checkpoint_scope_id=persistence_scope_id,
            stage="parent_resolution_r2",
            inherited_bridge_ledger=bridge_ledger,
        )
        all_failures.extend(r2_failures)
        r2, r2_admission_failures, r2_admission_telemetry = self._repair_guarded_unions(
            r2,
            r1_proposals,
            prototypes,
            models=models,
            run_id=run_id,
            checkpoint_scope_id=persistence_scope_id,
            wave="r2",
        )
        all_failures.extend(r2_admission_failures)
        if (
            _as_int(r1_telemetry.get("request_count", 0))
            and not _as_int(r1_telemetry.get("successful_request_count", 0))
            and _as_int(r2_telemetry.get("request_count", 0))
            and not _as_int(r2_telemetry.get("successful_request_count", 0))
        ):
            return ParentOccurrenceStageResult(
                status="PARTIAL_PARENT_RESOLUTION",
                failures=all_failures,
                telemetry={
                    "snapshot_query_count": snapshot.query_count,
                    **induction_telemetry,
                    **embedding_telemetry,
                    "proposal_write": proposal_write,
                    "r1": r1_telemetry,
                    "r2": r2_telemetry,
                    "global_provider_unavailable": True,
                },
            )
        final_proposals = self._reduce_proposals(r2, r1_proposals)
        final_proposals, duplicate_event_ownership_count = self._deduplicate_final_event_ownership(
            final_proposals, proposals, slices
        )
        merge_admission_telemetry = {
            "r1": r1_admission_telemetry,
            "r2": r2_admission_telemetry,
            "request_count": _as_int(r1_admission_telemetry.get("request_count"))
            + _as_int(r2_admission_telemetry.get("request_count")),
            "review_group_count": _as_int(r1_admission_telemetry.get("review_group_count"))
            + _as_int(r2_admission_telemetry.get("review_group_count")),
            "rejected_union_count": _as_int(
                r1_admission_telemetry.get("rejected_union_count")
            )
            + _as_int(r2_admission_telemetry.get("rejected_union_count")),
        }
        groups: list[FrozenParentGroup] = []
        for proposal in final_proposals:
            family = Counter(
                _family_for_event(events_by_id[event_id]) for event_id in proposal.event_ids
            ).most_common(1)[0][0]
            groups.append(
                FrozenParentGroup(
                    group_id=_short("parent", proposal.proposal_id),
                    scope=proposal.scope,
                    package_family=family,
                    canonical_label=proposal.label,
                    proposal_ids=proposal.supporting_proposal_ids,
                    existing_package_ids=proposal.existing_package_ids,
                    event_ids=proposal.event_ids,
                    membership_by_event=proposal.membership_by_event,
                    document_refs=proposal.document_refs,
                )
            )
        groups_by_proposal: dict[str, list[str]] = defaultdict(list)
        for group in groups:
            for proposal_id in group.proposal_ids:
                groups_by_proposal[proposal_id].append(group.group_id)
        proposal_to_group = {
            proposal_id: group_ids[0]
            for proposal_id, group_ids in groups_by_proposal.items()
            if len(group_ids) == 1
        }
        event_to_group = {
            event_id: group.group_id for group in groups for event_id in group.event_ids
        }
        external_support: dict[tuple[str, str, ExternalRelationType], set[str]] = defaultdict(set)
        for link in external_link_proposals:
            target_group_id = proposal_to_group.get(link.target_proposal_id)
            if (
                target_group_id is None
                or event_to_group.get(link.source_event_id) == target_group_id
            ):
                continue
            external_support[(link.source_event_id, target_group_id, link.relation)].add(
                link.document_ref
            )
        frozen_external_links = [
            FrozenParentExternalLink(
                source_event_id=source_event_id,
                target_group_id=target_group_id,
                relation=relation,
                supporting_document_refs=sorted(document_refs),
            )
            for (source_event_id, target_group_id, relation), document_refs in sorted(
                external_support.items(),
                key=lambda item: (item[0][0], item[0][1], str(item[0][2])),
            )
        ]
        covered = {event_id for group in groups for event_id in group.event_ids}
        expected = {item.event_id for item in events}
        if covered != expected:
            return ParentOccurrenceStageResult(
                status="PARTIAL_PARENT_RESOLUTION",
                failures=[
                    ParentStageFailure(
                        stage="PARENT_RECONCILE",
                        task_id="coverage",
                        error_code="ATOMIC_COVERAGE_INCOMPLETE",
                    )
                ],
                telemetry={
                    "covered_atomic_count": len(covered),
                    "expected_atomic_count": len(expected),
                },
            )
        partition_payload = {
            "groups": [
                item.model_dump(mode="json")
                for item in sorted(groups, key=lambda value: value.group_id)
            ],
            "external_links": [item.model_dump(mode="json") for item in frozen_external_links],
        }
        partition_hash = _hash(
            {"snapshot": partition_snapshot_hash, "partition": partition_payload}
        )
        partition = FrozenParentPartition(
            partition_hash=partition_hash,
            snapshot_hash=partition_snapshot_hash,
            groups=groups,
            external_links=frozen_external_links,
        )
        partition_inserted = self.registry.save_parent_occurrence_partition(
            run_id=persistence_scope_id,
            partition_hash=partition.partition_hash,
            snapshot_hash=partition.snapshot_hash,
            status=partition.status,
            payload=partition.model_dump(mode="json"),
        )
        package_stage_wall_ms = round((perf_counter() - started) * 1000)
        provider_wall_ms = sum(
            _as_int(value)
            for value in (
                induction_telemetry.get("model_wall_ms", 0),
                repartition_telemetry.get("model_wall_ms", 0),
                embedding_telemetry.get("embedding_model_wall_ms", 0),
                r1_telemetry.get("model_wall_ms", 0),
                r2_embedding.get("embedding_model_wall_ms", 0),
                r2_telemetry.get("model_wall_ms", 0),
                r1_admission_telemetry.get("model_wall_ms", 0),
                r2_admission_telemetry.get("model_wall_ms", 0),
            )
        )
        telemetry: dict[str, object] = {
            "snapshot_query_count": snapshot.query_count,
            "snapshot_load_ms": snapshot.load_ms,
            "pair_registry_read_count": 0,
            **induction_telemetry,
            **signal_telemetry,
            **repartition_telemetry,
            **embedding_telemetry,
            "proposal_write": proposal_write,
            "partition_inserted": partition_inserted,
            "r2_embedding_cache_hit_count": r2_embedding["embedding_cache_hit_count"],
            "r2_embedding_cache_miss_count": r2_embedding["embedding_cache_miss_count"],
            "r1": r1_telemetry,
            "r2": r2_telemetry,
            "merge_admission": merge_admission_telemetry,
            "resolution_wave_count": 2,
            "reconcile_wave_count": 0,
            "r1_candidate_coverage_bps": _as_int(r1_telemetry.get("candidate_coverage_bps", 0)),
            "r2_candidate_coverage_bps": round(
                len(
                    _string_set(r1_telemetry.get("covered_lineage_ids"))
                    | _string_set(r2_telemetry.get("covered_lineage_ids"))
                )
                * 10000
                / max(1, len(proposals))
            ),
            "retryable_failure_count": len(all_failures),
            "duplicate_event_ownership_resolved_count": duplicate_event_ownership_count,
            "final_boundary_split_count": 0,
            "partition_group_count": len(groups),
            "external_relation_count": len(frozen_external_links),
            "card_full_appearance_count": len(proposals) + len(r1_proposals),
            "card_neighbor_appearance_count": 0,
            "package_stage_wall_ms": package_stage_wall_ms,
            "provider_wall_ms": provider_wall_ms,
            "local_planning_ms": max(0, package_stage_wall_ms - provider_wall_ms),
        }
        self.registry.append_decision_audit(
            DecisionAuditRecord(
                audit_id=_short(
                    "audit-package-partition-v2", {"run": run_id, "partition": partition_hash}
                ),
                run_id=run_id,
                decision_type="PACKAGE_PARTITION_V2",
                subject_id=partition_hash,
                payload={
                    "snapshot_hash": snapshot.snapshot_hash,
                    "partition_hash": partition_hash,
                    "group_count": len(groups),
                    "event_count": len(expected),
                    "supporting_proposals": {item.group_id: item.proposal_ids for item in groups},
                },
            )
        )
        return ParentOccurrenceStageResult(
            status="FINALIZED",
            partition=partition,
            failures=all_failures,
            telemetry=telemetry,
        )
