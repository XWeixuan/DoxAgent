"""Set-based parent occurrence induction, resolution, and frozen partitioning."""

from __future__ import annotations

import hashlib
import json
import math
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
    PackageFamily,
    SourceMessage,
)
from cdecr.models import ModelTier
from cdecr.parent_occurrence_contracts import (
    AtomicDocumentSlice,
    CanonicalParentPrototype,
    FrozenParentExternalLink,
    FrozenParentGroup,
    FrozenParentPartition,
    ParentExternalLinkProposal,
    ParentInductionBatch,
    ParentInductionDecision,
    ParentInductionDocument,
    ParentOccurrenceStageResult,
    ParentProposalCard,
    ParentResolutionBatch,
    ParentStageFailure,
)
from cdecr.ports import CDECRRegistry, DecisionAuditRecord, StructuredModelRequest

_T = TypeVar("_T", bound=BaseModel)
_PROMPT_ROOT = Path(__file__).parent / "prompts" / "v1"


def _hash(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _short(prefix: str, value: object) -> str:
    return f"{prefix}:{_hash(value)[:24]}"


def _dedupe(values: Sequence[str], *, limit: int | None = None) -> list[str]:
    output = list(dict.fromkeys(value.strip() for value in values if value and value.strip()))
    return output if limit is None else output[:limit]


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left:
        return -1.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    norm_left = math.sqrt(sum(value * value for value in left))
    norm_right = math.sqrt(sum(value * value for value in right))
    if not norm_left or not norm_right:
        return -1.0
    return dot / (norm_left * norm_right)


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


def _event_cues(event: AtomicEvent) -> tuple[list[str], list[str], list[str]]:
    fields = event.identity_profile.fields.model_dump(mode="json")
    objects: list[str] = []
    artifacts: list[str] = []
    metrics: list[str] = []
    for key, value in fields.items():
        values = value if isinstance(value, list) else [value]
        rendered = [str(item) for item in values if item not in (None, "")]
        if any(token in key for token in ("artifact", "filing", "release", "report_identity")):
            artifacts.extend(rendered)
        elif "metric" in key:
            metrics.extend(rendered)
        elif any(token in key for token in ("object", "product", "instrument", "counterparty")):
            objects.extend(rendered)
    return _dedupe(objects), _dedupe(artifacts), _dedupe(metrics)


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
        event_values = tuple(sorted(events, key=lambda item: item.event_id))
        package_values = tuple(sorted(packages, key=lambda item: item.package_id))
        identity = {
            "events": [(item.event_id, item.version) for item in event_values],
            "mentions": sorted(item.mention_id for item in mentions),
            "sources": sorted(item.message_id for item in sources),
            "packages": [(item.package_id, item.version) for item in package_values],
        }
        return cls(
            events=event_values,
            mentions_by_id={item.mention_id: item for item in mentions},
            sources_by_id={item.message_id: item for item in sources},
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
    event_ids: list[str] | None = None


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
    ) -> None:
        self.registry = registry
        self.induction_active_requests = max(1, induction_active_requests)
        self.resolution_active_requests = max(1, resolution_active_requests)
        self.reconcile_active_requests = max(1, reconcile_active_requests)
        self.induction_max_documents = max(1, induction_max_documents)
        self.induction_max_slices = max(1, min(96, induction_max_slices))
        self.resolution_max_proposals = max(2, resolution_max_proposals)
        self.resolution_max_existing_parents = max(0, resolution_max_existing_parents)

    @staticmethod
    def _prompt(name: str) -> str:
        return (_PROMPT_ROOT / name).read_text(encoding="utf-8")

    def _slices(
        self, snapshot: PackageStageSnapshotV2
    ) -> tuple[list[ParentInductionDocument], dict[str, AtomicDocumentSlice]]:
        by_document: dict[str, list[AtomicDocumentSlice]] = defaultdict(list)
        slices: dict[str, AtomicDocumentSlice] = {}
        atomic_counter = 0
        for event in snapshot.events:
            mentions_by_document: dict[str, list[EventMention]] = defaultdict(list)
            for mention_id in event.mention_ids:
                mention = snapshot.mentions_by_id.get(mention_id)
                if mention is not None and mention.message_id in snapshot.sources_by_id:
                    mentions_by_document[mention.message_id].append(mention)
            for message_id, mentions in sorted(mentions_by_document.items()):
                atomic_counter += 1
                source = snapshot.sources_by_id[message_id]
                objects, artifacts, metrics = _event_cues(event)
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
                    participants=_event_participants(event),
                    object_cues=objects,
                    artifact_cues=artifacts,
                    metric_cues=metrics,
                    evidence=_dedupe(
                        [text for mention in mentions for text in _evidence_for(mention)],
                        limit=2,
                    ),
                )
                by_document[message_id].append(item)
                slices[item.atomic_ref] = item
        documents: list[ParentInductionDocument] = []
        for index, (message_id, values) in enumerate(sorted(by_document.items()), start=1):
            source = snapshot.sources_by_id[message_id]
            documents.append(
                ParentInductionDocument(
                    task_id=f"D{index}",
                    document_ref=message_id,
                    title=source.title,
                    published_at=source.published_at.isoformat(),
                    source_name=source.source_name,
                    article=source.text,
                    atomics=sorted(values, key=lambda item: item.atomic_ref),
                )
            )
        return documents, slices

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
    ) -> tuple[list[ParentInductionDecision], list[ParentStageFailure], dict[str, int]]:
        input_hashes = {item.task_id: _hash(item.model_dump(mode="json")) for item in documents}
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
                    self._validate_induction(
                        ParentInductionBatch(decisions=[decision]), [document]
                    )
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
        ) -> tuple[
            list[StructuredModelRequest], list[Callable[[ParentInductionBatch], None]]
        ]:
            return (
                [
                    StructuredModelRequest(
                        system_prompt=self._prompt("parent_occurrence_induction.md"),
                        user_prompt=json.dumps(
                            {
                                "document_tasks": [
                                    item.model_dump(mode="json") for item in batch
                                ]
                            },
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                        json_schema=ParentInductionBatch.model_json_schema(),
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
                    audit_id=_short(
                        "audit-parent-induction", {"run": run_id, "tasks": task_ids}
                    ),
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
            stage="parent_induction",
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
            stage="parent_induction",
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
    ) -> tuple[list[ParentProposalCard], list[ParentExternalLinkProposal]]:
        output: list[ParentProposalCard] = []
        proposal_by_local_group: dict[tuple[str, str], str] = {}
        for decision in decisions:
            document = documents[decision.task_id.split(":S", maxsplit=1)[0]]
            for group in decision.groups:
                member_slices = [slices[item.atomic_ref] for item in group.members]
                event_ids = _dedupe([item.event_id for item in member_slices])
                proposal_id = _short(
                    "parent-proposal",
                    {
                        "document": member_slices[0].document_fingerprint,
                        "events": sorted(event_ids),
                        "scope": group.scope,
                        "family": group.package_family.value,
                    },
                )
                proposal_by_local_group[(decision.task_id, group.local_group_id)] = proposal_id
                output.append(
                    ParentProposalCard(
                        proposal_ref=f"P{len(output) + 1}",
                        proposal_id=proposal_id,
                        supporting_proposal_ids=[proposal_id],
                        scope=group.scope,
                        package_family=group.package_family,
                        label=group.label,
                        atomic_refs=[item.atomic_ref for item in member_slices],
                        event_ids=event_ids,
                        membership_by_event={
                            slices[item.atomic_ref].event_id: item.membership_relation
                            for item in group.members
                        },
                        document_refs=[document.document_ref],
                        participants=_dedupe(
                            [value for item in member_slices for value in item.participants]
                        ),
                        period_ids=_dedupe(
                            [item.period_id or "" for item in member_slices if item.period_id]
                        ),
                        object_cues=_dedupe(
                            [value for item in member_slices for value in item.object_cues]
                        ),
                        artifact_cues=_dedupe(
                            [value for item in member_slices for value in item.artifact_cues]
                        ),
                        metric_cues=_dedupe(
                            [value for item in member_slices for value in item.metric_cues]
                        ),
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
                    target_proposal_id = proposal_by_local_group[
                        (decision.task_id, link.target_local_group_id)
                    ]
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
        output = [
            item.model_copy(update={"proposal_ref": f"P{index}"})
            for index, item in enumerate(
                sorted(output, key=lambda value: value.proposal_id), start=1
            )
        ]
        return output, external_links

    @staticmethod
    def _prototypes(
        packages: Sequence[EventPackage],
        events_by_id: Mapping[str, AtomicEvent],
    ) -> list[CanonicalParentPrototype]:
        output: list[CanonicalParentPrototype] = []
        for package in sorted(packages, key=lambda item: item.package_id):
            events = [
                events_by_id[item] for item in package.member_event_ids if item in events_by_id
            ]
            output.append(
                CanonicalParentPrototype(
                    parent_ref=f"H{len(output) + 1}",
                    package_id=package.package_id,
                    scope=package.parent_scope,
                    package_family=package.package_family,
                    canonical_label=package.canonical_title,
                    event_ids=sorted(package.member_event_ids),
                    participants=_dedupe(
                        [value for event in events for value in _event_participants(event)]
                    ),
                    period_ids=_dedupe([event.time.reference_period_id or "" for event in events]),
                    artifact_cues=_dedupe(
                        [value for event in events for value in _event_cues(event)[1]]
                    ),
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
                    *proposal.participants,
                    *proposal.period_ids,
                    *proposal.object_cues,
                    *proposal.artifact_cues,
                    *proposal.metric_cues,
                    *proposal.representative_facts,
                ]
            )
            cards.append(("parent_proposal", proposal.proposal_id, text))
        for prototype in prototypes:
            text = " | ".join(
                [
                    prototype.canonical_label,
                    prototype.package_family.value,
                    *prototype.participants,
                    *prototype.period_ids,
                    *prototype.artifact_cues,
                    *prototype.representative_facts,
                ]
            )
            cards.append(("parent_prototype", prototype.package_id, text))
        vectors: dict[tuple[str, str], list[float]] = {}
        misses: list[tuple[str, str, str]] = []
        cache_hits = 0
        for owner_kind, owner_id, text in cards:
            input_hash = _hash(text)
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
                            input_hash=_hash(text),
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
    def _candidate_score(
        left: ParentProposalCard,
        right: ParentProposalCard | CanonicalParentPrototype,
    ) -> float:
        right_events = set(right.event_ids)
        right_participants = set(right.participants)
        right_periods = set(right.period_ids)
        score = 0.0
        if set(left.event_ids).intersection(right_events):
            score += 4.0
        if left.package_family == right.package_family:
            score += 0.6
        if set(left.participants).intersection(right_participants):
            score += 0.8
        if set(left.period_ids).intersection(right_periods):
            score += 0.8
        if set(left.artifact_cues).intersection(right.artifact_cues):
            score += 2.0
        score += max(0.0, _cosine(left.embedding, right.embedding))
        return score

    @staticmethod
    def _candidate_keys(
        item: ParentProposalCard | CanonicalParentPrototype,
    ) -> list[tuple[str, ...]]:
        """Compile bounded recall keys; keys retrieve candidates but never decide identity."""

        keys: set[tuple[str, ...]] = set()
        family = item.package_family.value
        for event_id in item.event_ids:
            keys.add(("event", event_id))
        for participant in item.participants:
            keys.add(("participant_family", participant, family))
            for period in item.period_ids:
                keys.add(("participant_period", participant, period))
        for period in item.period_ids:
            keys.add(("period_family", period, family))
        for artifact in item.artifact_cues:
            keys.add(("artifact", artifact))
        if isinstance(item, ParentProposalCard):
            for value in item.object_cues:
                keys.add(("object_family", value, family))
            for value in item.metric_cues:
                for period in item.period_ids:
                    keys.add(("metric_period", value, period))
        vector = item.embedding
        if vector:
            # Four small sign bands provide deterministic approximate semantic buckets
            # without an all-pairs cosine scan.  Structured keys remain the primary route.
            width = min(16, len(vector))
            for offset in range(0, width, 4):
                bits = "".join("1" if value >= 0 else "0" for value in vector[offset : offset + 4])
                keys.add(("embedding_lsh", str(offset // 4), bits))
        return sorted(keys)

    def _resolution_tasks(
        self,
        proposals: Sequence[ParentProposalCard],
        prototypes: Sequence[CanonicalParentPrototype],
    ) -> tuple[
        list[tuple[list[ParentProposalCard], list[CanonicalParentPrototype]]], dict[str, int]
    ]:
        proposal_by_ref = {item.proposal_ref: item for item in proposals}
        prototype_by_ref = {item.parent_ref: item for item in prototypes}
        proposal_index: dict[tuple[str, ...], list[str]] = defaultdict(list)
        prototype_index: dict[tuple[str, ...], list[str]] = defaultdict(list)
        for proposal in proposals:
            for key in self._candidate_keys(proposal):
                proposal_index[key].append(proposal.proposal_ref)
        for prototype in prototypes:
            for key in self._candidate_keys(prototype):
                prototype_index[key].append(prototype.parent_ref)

        adjacency: dict[str, set[str]] = {item.proposal_ref: set() for item in proposals}
        prototype_choices: dict[str, list[str]] = defaultdict(list)
        candidate_proposals = 0
        pair_evaluations = 0
        for left in proposals:
            proposal_refs: set[str] = set()
            parent_refs: set[str] = set()
            for key in self._candidate_keys(left):
                # A pathological generic bucket cannot monopolize a task.  Stable
                # head/tail sampling retains diverse neighbors without quadratic work.
                values = proposal_index.get(key, [])
                bounded = values if len(values) <= 64 else [*values[:32], *values[-32:]]
                proposal_refs.update(bounded)
                parent_values = prototype_index.get(key, [])
                bounded_parents = (
                    parent_values
                    if len(parent_values) <= 32
                    else [*parent_values[:16], *parent_values[-16:]]
                )
                parent_refs.update(bounded_parents)
            proposal_refs.discard(left.proposal_ref)
            scored: list[tuple[float, str, str]] = []
            for ref in sorted(proposal_refs)[:96]:
                score = self._candidate_score(left, proposal_by_ref[ref])
                pair_evaluations += 1
                if score >= 1.35 or set(left.event_ids).intersection(
                    proposal_by_ref[ref].event_ids
                ):
                    scored.append((score, "P", ref))
            for ref in sorted(parent_refs)[:48]:
                score = self._candidate_score(left, prototype_by_ref[ref])
                pair_evaluations += 1
                if score >= 1.35 or set(left.event_ids).intersection(
                    prototype_by_ref[ref].event_ids
                ):
                    scored.append((score, "H", ref))
            if scored:
                candidate_proposals += 1
            for _, kind, target in sorted(scored, reverse=True)[:24]:
                if kind == "P":
                    adjacency[left.proposal_ref].add(target)
                    adjacency[target].add(left.proposal_ref)
                else:
                    prototype_choices[left.proposal_ref].append(target)

        proposals_by_parent: dict[str, list[str]] = defaultdict(list)
        for proposal_ref, chosen_parent_refs in prototype_choices.items():
            for parent_ref in chosen_parent_refs:
                proposals_by_parent[parent_ref].append(proposal_ref)
        for refs in proposals_by_parent.values():
            ordered = sorted(refs)
            for left_ref, right_ref in zip(ordered, ordered[1:], strict=False):
                adjacency[left_ref].add(right_ref)
                adjacency[right_ref].add(left_ref)

        visited: set[str] = set()
        tasks: list[tuple[list[ParentProposalCard], list[CanonicalParentPrototype]]] = []
        claimed_prototypes: set[str] = set()
        for start_ref in sorted(adjacency):
            if start_ref in visited or (
                not adjacency[start_ref] and not prototype_choices[start_ref]
            ):
                continue
            stack = [start_ref]
            component: list[str] = []
            while stack:
                current = stack.pop()
                if current in visited:
                    continue
                visited.add(current)
                component.append(current)
                stack.extend(sorted(adjacency[current] - visited, reverse=True))
            for offset in range(0, len(component), self.resolution_max_proposals):
                component_refs = sorted(component[offset : offset + self.resolution_max_proposals])
                task_parent_refs = _dedupe(
                    [value for ref in component_refs for value in prototype_choices.get(ref, [])],
                    limit=self.resolution_max_existing_parents,
                )
                task_parent_refs = [
                    value for value in task_parent_refs if value not in claimed_prototypes
                ]
                claimed_prototypes.update(task_parent_refs)
                tasks.append(
                    (
                        [proposal_by_ref[ref] for ref in component_refs],
                        [
                            prototype_by_ref[ref]
                            for ref in task_parent_refs
                            if ref in prototype_by_ref
                        ],
                    )
                )
        return tasks, {
            "candidate_proposal_count": candidate_proposals,
            "candidate_pair_evaluation_count": pair_evaluations,
            "candidate_coverage_bps": (
                round(candidate_proposals * 10000 / len(proposals)) if proposals else 10000
            ),
        }

    @staticmethod
    def _validate_resolution(
        value: ParentResolutionBatch,
        proposals: Sequence[ParentProposalCard],
        prototypes: Sequence[CanonicalParentPrototype],
        *,
        require_disjoint_events: bool = False,
        review_event_id_by_ref: Mapping[str, str] | None = None,
    ) -> None:
        expected = {item.proposal_ref for item in proposals}
        actual = {ref for group in value.groups for ref in group.proposal_refs}
        if actual != expected:
            raise ValueError("resolution groups must cover every input proposal exactly once")
        allowed_parents = {item.parent_ref for item in prototypes}
        actual_parents = {ref for group in value.groups for ref in group.existing_parent_refs}
        if not actual_parents.issubset(allowed_parents):
            raise ValueError("resolution output contains an unknown prototype ref")
        if require_disjoint_events:
            event_map = review_event_id_by_ref or {
                event_id: event_id
                for item in proposals
                for event_id in item.event_ids
            }
            expected_refs = set(event_map)
            actual_refs = [event_ref for group in value.groups for event_ref in group.event_refs]
            remainder_groups = [
                group for group in value.groups if group.includes_remaining_events
            ]
            if len(remainder_groups) != 1:
                raise ValueError("review output needs exactly one remaining-events group")
            if len(actual_refs) != len(set(actual_refs)) or not set(actual_refs).issubset(
                expected_refs
            ):
                raise ValueError("review groups contain duplicate or unknown Atomic refs")
        elif any(
            not group.proposal_refs
            or group.event_refs
            or group.includes_remaining_events
            for group in value.groups
        ):
            raise ValueError("ordinary resolution groups require proposals and no event IDs")

    def _resolve_wave(
        self,
        proposals: Sequence[ParentProposalCard],
        prototypes: Sequence[CanonicalParentPrototype],
        *,
        models: Any,
        run_id: str,
        checkpoint_scope_id: str,
        stage: str,
        review: bool = False,
        review_events: Mapping[str, AtomicEvent] | None = None,
    ) -> tuple[list[_ResolvedParent], list[ParentStageFailure], dict[str, int]]:
        tasks, recall_telemetry = self._resolution_tasks(proposals, prototypes)
        assigned_refs = {item.proposal_ref for task, _ in tasks for item in task}
        resolved: list[_ResolvedParent] = [
            _ResolvedParent(
                group_id=_short("parent", item.proposal_id),
                proposal_ids=[item.proposal_id],
                existing_package_ids=[],
                canonical_label=item.label,
            )
            for item in proposals
            if item.proposal_ref not in assigned_refs
        ]
        suffix = (
            "\nReview mode uses one default parent plus explicit exceptions. Put every "
            "proposal_ref on the single includes_remaining_events group; all other groups use "
            "proposal_refs=[] and list their event_refs. A disclosure, its market or analyst "
            "reaction, a separate contract, and a broad industry or valuation theme are different "
            "parents. The default receives every unlisted event_ref."
            if review
            else ""
        )
        review_event_maps: list[dict[str, str]] = []
        task_payloads: list[dict[str, object]] = []
        for task_proposals, task_prototypes in tasks:
            event_ids = sorted(
                {event_id for proposal in task_proposals for event_id in proposal.event_ids}
            )
            event_id_by_ref = {
                f"E{index}": event_id for index, event_id in enumerate(event_ids, start=1)
            }
            event_ref_by_id = {event_id: ref for ref, event_id in event_id_by_ref.items()}
            proposal_payloads: list[dict[str, object]] = []
            for proposal in task_proposals:
                payload = proposal.model_dump(mode="json")
                if review:
                    payload["event_refs"] = [
                        event_ref_by_id[event_id] for event_id in proposal.event_ids
                    ]
                    payload.pop("event_ids", None)
                proposal_payloads.append(payload)
            review_event_maps.append(event_id_by_ref if review else {})
            task_payload: dict[str, object] = {
                "proposals": proposal_payloads,
                "prototypes": [item.model_dump(mode="json") for item in task_prototypes],
            }
            if review:
                events_by_id = review_events or {}
                atomic_events: list[dict[str, object]] = []
                for event_ref, event_id in event_id_by_ref.items():
                    event = events_by_id.get(event_id)
                    if event is None:
                        continue
                    object_cues, artifact_cues, metric_cues = _event_cues(event)
                    atomic_events.append(
                        {
                            "event_ref": event_ref,
                            "fact": event.canonical_proposition,
                            "family": event.event_family.value,
                            "assertion": event.assertion_state.value,
                            "period": event.time.reference_period_id,
                            "participants": _event_participants(event),
                            "objects": object_cues,
                            "artifacts": artifact_cues,
                            "metrics": metric_cues,
                        }
                    )
                task_payload["atomic_events"] = atomic_events
            task_payloads.append(task_payload)
        task_hashes = [_hash(item) for item in task_payloads]
        stage_code: Literal[
            "PARENT_INDUCE", "PARENT_RESOLVE_R1", "PARENT_RESOLVE_R2", "PARENT_RECONCILE"
        ] = (
            "PARENT_RECONCILE"
            if review
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
                    self._validate_resolution(
                        value,
                        *tasks[index],
                        require_disjoint_events=review,
                        review_event_id_by_ref=review_event_maps[index],
                    )
                except (ValueError, TypeError):
                    pending_indices.append(index)
                else:
                    reused_results[index] = value
            else:
                pending_indices.append(index)

        requests = [
            StructuredModelRequest(
                system_prompt=self._prompt("parent_occurrence_resolution.md") + suffix,
                user_prompt=json.dumps(
                    task_payloads[index], ensure_ascii=False, separators=(",", ":")
                ),
                json_schema=ParentResolutionBatch.model_json_schema(),
            )
            for index in pending_indices
        ]
        validators: list[Callable[[ParentResolutionBatch], None]] = [
            partial(
                self._validate_resolution,
                proposals=tasks[index][0],
                prototypes=tasks[index][1],
                require_disjoint_events=review,
                review_event_id_by_ref=review_event_maps[index],
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
        tier = ModelTier.M4 if review else ModelTier.M3
        model_started = perf_counter()
        pending_results = models.typed_many(
            tier=tier,
            stage=stage,
            requests=requests,
            output_type=ParentResolutionBatch,
            validators=validators,
        )
        results_by_index: dict[int, ParentResolutionBatch | Exception] = dict(reused_results)
        results_by_index.update(
            zip(pending_indices, pending_results, strict=True)
        )
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
                continue
            review_refs_by_group: dict[str, set[str]] = {}
            if review:
                event_ref_by_id = {
                    event_id: event_ref
                    for event_ref, event_id in review_event_maps[index].items()
                }
                explicit_owner = {
                    event_ref: group.resolution_group_id
                    for group in result.groups
                    for event_ref in group.event_refs
                }
                remainder_group = next(
                    group for group in result.groups if group.includes_remaining_events
                )
                proposal_event_refs = {
                    proposal.proposal_ref: {
                        event_ref_by_id[event_id] for event_id in proposal.event_ids
                    }
                    for proposal in task_proposals
                }
                for group in result.groups:
                    review_refs_by_group[group.resolution_group_id] = set(group.event_refs)
                for event_ref in review_event_maps[index]:
                    if event_ref in explicit_owner:
                        continue
                    candidates: list[tuple[int, str]] = []
                    for group in result.groups:
                        support = sum(
                            event_ref in proposal_event_refs.get(proposal_ref, set())
                            for proposal_ref in group.proposal_refs
                        )
                        if support:
                            candidates.append((support, group.resolution_group_id))
                    owner_id = (
                        min(candidates, key=lambda value: (-value[0], value[1]))[1]
                        if candidates
                        else remainder_group.resolution_group_id
                    )
                    review_refs_by_group[owner_id].add(event_ref)
            for group in result.groups:
                proposal_ids = [proposal_by_ref[ref].proposal_id for ref in group.proposal_refs]
                package_ids = [
                    prototype_by_ref[ref].package_id
                    for ref in group.existing_parent_refs
                    if ref in prototype_by_ref
                ]
                group_event_refs = (
                    review_refs_by_group[group.resolution_group_id]
                    if review
                    else set()
                )
                group_event_ids = (
                    sorted(
                        review_event_maps[index][event_ref]
                        for event_ref in group_event_refs
                    )
                    if review
                    else None
                )
                resolved.append(
                    _ResolvedParent(
                        group_id=_short(
                            "parent",
                            {
                                "proposals": sorted(proposal_ids),
                                "packages": sorted(package_ids),
                                "events": group_event_ids or [],
                            },
                        ),
                        proposal_ids=sorted(proposal_ids),
                        existing_package_ids=sorted(package_ids),
                        canonical_label=group.canonical_label,
                        event_ids=group_event_ids,
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
                "reused_task_count": len(reused_results),
                "resolved_group_count": len(resolved),
                "checkpoint_transaction_count": int(start_write.get("transactions", 0))
                + int(finish_write.get("transactions", 0)),
                "audit_transaction_count": int(audit_write.get("transactions", 0)),
                "model_wall_ms": model_wall_ms,
            },
        )

    @staticmethod
    def _reduce_proposals(
        resolved: Sequence[_ResolvedParent],
        proposals: Sequence[ParentProposalCard],
    ) -> list[ParentProposalCard]:
        by_id = {item.proposal_id: item for item in proposals}
        output: list[ParentProposalCard] = []
        for item in resolved:
            referenced = [by_id[value] for value in item.proposal_ids if value in by_id]
            selected_event_ids = set(item.event_ids or [])
            supporting = (
                [
                    proposal
                    for proposal in proposals
                    if selected_event_ids.intersection(proposal.event_ids)
                ]
                if item.event_ids is not None
                else referenced
            )
            if item.event_ids is not None and not selected_event_ids:
                continue
            members = list(
                {
                    member.proposal_id: member
                    for member in [*referenced, *supporting]
                }.values()
            )
            if not members:
                continue
            event_ids = (
                sorted(selected_event_ids)
                if item.event_ids is not None
                else _dedupe([value for member in members for value in member.event_ids])
            )
            family = Counter(value.package_family for value in members).most_common(1)[0][0]
            scope = Counter(value.scope for value in members).most_common(1)[0][0]
            output.append(
                ParentProposalCard(
                    proposal_ref=f"P{len(output) + 1}",
                    proposal_id=_short(
                        "provisional-parent",
                        {
                            "proposals": sorted(item.proposal_ids),
                            "events": event_ids,
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
                    object_cues=_dedupe(
                        [value for member in members for value in member.object_cues]
                    ),
                    artifact_cues=_dedupe(
                        [value for member in members for value in member.artifact_cues]
                    ),
                    metric_cues=_dedupe(
                        [value for member in members for value in member.metric_cues]
                    ),
                    representative_facts=_dedupe(
                        [value for member in members for value in member.representative_facts],
                        limit=6,
                    ),
                    embedding=members[0].embedding,
                )
            )
        return output

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
        documents, slices = self._slices(snapshot)
        decisions, failures, induction_telemetry = self._induce(
            documents,
            models=models,
            run_id=run_id,
            checkpoint_scope_id=persistence_scope_id,
        )
        if failures:
            return ParentOccurrenceStageResult(
                status="PARTIAL_PARENT_RESOLUTION",
                failures=failures,
                telemetry={
                    "snapshot_query_count": snapshot.query_count,
                    "snapshot_load_ms": snapshot.load_ms,
                    **induction_telemetry,
                },
            )
        documents_by_task = {item.task_id: item for item in documents}
        proposals, external_link_proposals = self._proposals(decisions, documents_by_task, slices)
        proposal_write = self.registry.save_parent_occurrence_proposals(
            run_id=persistence_scope_id,
            records=[item.model_dump(mode="json") for item in proposals],
        )
        prototypes = self._prototypes(existing_packages, {item.event_id: item for item in events})
        proposals, prototypes, embedding_telemetry = self._embed_cards(
            proposals, prototypes, models=models
        )
        r1, r1_failures, r1_telemetry = self._resolve_wave(
            proposals,
            prototypes,
            models=models,
            run_id=run_id,
            checkpoint_scope_id=persistence_scope_id,
            stage="parent_resolution_r1",
        )
        if r1_failures:
            return ParentOccurrenceStageResult(
                status="PARTIAL_PARENT_RESOLUTION",
                failures=r1_failures,
                telemetry={
                    "snapshot_query_count": snapshot.query_count,
                    **induction_telemetry,
                    **embedding_telemetry,
                    "proposal_write": proposal_write,
                    "r1": r1_telemetry,
                },
            )
        r1_proposals = self._reduce_proposals(r1, proposals)
        r1_proposals, prototypes, r2_embedding = self._embed_cards(
            r1_proposals, prototypes, models=models
        )
        r2, r2_failures, r2_telemetry = self._resolve_wave(
            r1_proposals,
            prototypes,
            models=models,
            run_id=run_id,
            checkpoint_scope_id=persistence_scope_id,
            stage="parent_resolution_r2",
        )
        if r2_failures:
            return ParentOccurrenceStageResult(
                status="PARTIAL_PARENT_RESOLUTION",
                failures=r2_failures,
                telemetry={
                    "snapshot_query_count": snapshot.query_count,
                    **induction_telemetry,
                    **embedding_telemetry,
                    "proposal_write": proposal_write,
                    "r1": r1_telemetry,
                    "r2": r2_telemetry,
                },
            )
        final_proposals = self._reduce_proposals(r2, r1_proposals)
        refs_by_event: dict[str, list[str]] = defaultdict(list)
        for proposal in final_proposals:
            for event_id in proposal.event_ids:
                refs_by_event[event_id].append(proposal.proposal_ref)
        conflict_refs = {ref for refs in refs_by_event.values() if len(refs) > 1 for ref in refs}
        review_proposals = [
            item
            for item in final_proposals
            if len(item.atomic_refs) >= 12
            or len(item.event_ids) >= 32
            or item.proposal_ref in conflict_refs
        ]
        reconcile_telemetry: dict[str, int] = {"request_count": 0, "resolved_group_count": 0}
        if review_proposals:
            reviewed, review_failures, reconcile_telemetry = self._resolve_wave(
                review_proposals,
                prototypes,
                models=models,
                run_id=run_id,
                checkpoint_scope_id=persistence_scope_id,
                stage="parent_reconcile",
                review=True,
                review_events={item.event_id: item for item in events},
            )
            if review_failures:
                return ParentOccurrenceStageResult(
                    status="PARTIAL_PARENT_RESOLUTION",
                    failures=review_failures,
                    telemetry={
                        "snapshot_query_count": snapshot.query_count,
                        "proposal_write": proposal_write,
                        "r1": r1_telemetry,
                        "r2": r2_telemetry,
                        "reconcile": reconcile_telemetry,
                    },
                )
            reviewed_ids = {item.proposal_id for item in review_proposals}
            r2 = [
                _ResolvedParent(
                    group_id=_short("parent", item.proposal_id),
                    proposal_ids=[item.proposal_id],
                    existing_package_ids=item.existing_package_ids,
                    canonical_label=item.label,
                )
                for item in final_proposals
                if item.proposal_id not in reviewed_ids
            ] + reviewed
            final_proposals = self._reduce_proposals(r2, final_proposals)
        groups: list[FrozenParentGroup] = []
        for proposal in final_proposals:
            groups.append(
                FrozenParentGroup(
                    group_id=_short("parent", proposal.proposal_id),
                    scope=proposal.scope,
                    package_family=proposal.package_family,
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
        partition_hash = _hash({"snapshot": snapshot.snapshot_hash, "partition": partition_payload})
        partition = FrozenParentPartition(
            partition_hash=partition_hash,
            snapshot_hash=snapshot.snapshot_hash,
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
            int(value)
            for value in (
                induction_telemetry.get("model_wall_ms", 0),
                embedding_telemetry.get("embedding_model_wall_ms", 0),
                r1_telemetry.get("model_wall_ms", 0),
                r2_embedding.get("embedding_model_wall_ms", 0),
                r2_telemetry.get("model_wall_ms", 0),
                reconcile_telemetry.get("model_wall_ms", 0),
            )
        )
        telemetry: dict[str, object] = {
            "snapshot_query_count": snapshot.query_count,
            "snapshot_load_ms": snapshot.load_ms,
            "pair_registry_read_count": 0,
            **induction_telemetry,
            **embedding_telemetry,
            "proposal_write": proposal_write,
            "partition_inserted": partition_inserted,
            "r2_embedding_cache_hit_count": r2_embedding["embedding_cache_hit_count"],
            "r2_embedding_cache_miss_count": r2_embedding["embedding_cache_miss_count"],
            "r1": r1_telemetry,
            "r2": r2_telemetry,
            "reconcile": reconcile_telemetry,
            "resolution_wave_count": 2 + int(bool(review_proposals)),
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
            telemetry=telemetry,
        )
