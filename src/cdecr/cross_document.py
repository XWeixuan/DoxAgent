"""Incremental Atomic Event and Event Package engine for standalone CDECR."""

from __future__ import annotations

import hashlib
import json
import math
import traceback
import uuid
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from cdecr.contracts import (
    AtomicAction,
    AtomicEvent,
    AtomicSemanticRelation,
    EventFamily,
    EventMention,
    EventPackage,
    ExternalEventRelation,
    ExternalRelationType,
    MembershipRelation,
    PackageAction,
    PackageAssignmentRelation,
    PackageExternalRelation,
    PackageKind,
    PackageMembership,
    PackageMergeRelation,
)
from cdecr.coreference_rules import (
    add_event_to_package,
    add_mention_to_atomic,
    bounded_package_exact_match,
    core_entity_ids_from_mention,
    hard_cannot_link,
    identity_profile_for_mention,
    merge_packages,
    package_hard_conflicts,
    package_seed_for_event,
    packages_obviously_same,
    reference_period_from_profile,
    singleton_atomic_event,
    singleton_package,
    stable_id,
)
from cdecr.cross_document_contracts import (
    AtomicAssignmentRecord,
    AtomicCandidate,
    AtomicDecisionBatch,
    AtomicPairDecision,
    CrossDocumentResult,
    CrossDocumentStatus,
    HardConflictCode,
    HoldKind,
    HoldRecord,
    PackageAssignmentRecord,
    PackageCandidate,
    PackageDecisionBatch,
    PackageMergeDecisionBatch,
    PackagePairDecision,
    PackagePairMergeDecision,
    PackageSeed,
    RecallRoute,
)
from cdecr.models import (
    STRUCTURED_OUTPUT_MODE,
    STRUCTURED_REASONING_EFFORT,
    ModelAdapterError,
    ModelTier,
)
from cdecr.ports import (
    CDECRRegistry,
    DecisionAuditRecord,
    EmbeddingClient,
    EmbeddingResult,
    StructuredModelClient,
    StructuredModelRequest,
    StructuredModelResult,
)
from cdecr.single_document_contracts import ModelCallSummary

ENGINE_VERSION = "cdecr-cross-document-v5"
PROMPT_VERSION = "cdecr-cross-document-prompts-v4"
ATOMIC_DECISION_MENTION_BATCH = 3
PACKAGE_DECISION_EVENT_BATCH = 12
PACKAGE_MERGE_PAIR_BATCH = 12
MODEL_CONCURRENCY = 3
ATOMIC_TOP_K = 5
PACKAGE_TOP_K = 5
EMBEDDING_RECALL_THRESHOLD = 0.82
HIGH_IMPACT_FAMILIES = {
    EventFamily.TRANSACTION_CAPITAL,
    EventFamily.REGULATORY_LEGAL_POLICY,
    EventFamily.INCIDENT_GEOPOLITICAL,
}

_T = TypeVar("_T", bound=BaseModel)


class CrossDocumentPipelineError(RuntimeError):
    def __init__(
        self, stage: str, code: str, *, repair_payload: str | None = None
    ) -> None:
        self.stage = stage
        self.code = code
        self.repair_payload = repair_payload
        super().__init__(f"cross-document stage {stage!r} failed: {code}")


def _hash_json(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return max(-1.0, min(1.0, dot / (left_norm * right_norm)))


def _prompt(name: str) -> str:
    return (Path(__file__).parent / "prompts" / "v1" / name).read_text(encoding="utf-8")


class _AuditedModels:
    def __init__(
        self,
        *,
        registry: CDECRRegistry,
        run_id: str,
        embedding_client: EmbeddingClient,
        m2_client: StructuredModelClient,
        m3_client: StructuredModelClient,
        model_m1: str,
        model_m2: str,
        model_m3: str,
        summaries: list[ModelCallSummary],
    ) -> None:
        self.registry = registry
        self.run_id = run_id
        self.embedding_client = embedding_client
        self.m2_client = m2_client
        self.m3_client = m3_client
        self.model_m1 = model_m1
        self.model_m2 = model_m2
        self.model_m3 = model_m3
        self.summaries = summaries

    def embed(self, texts: Sequence[str], *, stage: str) -> EmbeddingResult:
        call_id = str(uuid.uuid4())
        input_hash = _hash_json(list(texts))
        try:
            result = self.embedding_client.embed(texts)
        except Exception as exc:
            latency, code = _safe_error(exc)
            self.registry.record_model_call(
                model_call_id=call_id,
                run_id=self.run_id,
                tier=ModelTier.M1.value,
                model=self.model_m1,
                status="FAILED",
                input_tokens=None,
                output_tokens=None,
                latency_ms=latency,
                error_code=code,
                metadata={"input_count": len(texts)},
                stage=stage,
                prompt_version=PROMPT_VERSION,
                input_hash=input_hash,
            )
            self.summaries.append(
                ModelCallSummary(
                    stage=stage,
                    tier=ModelTier.M1.value,
                    model=self.model_m1,
                    latency_ms=latency,
                    status="FAILED",
                    error_code=code,
                )
            )
            raise CrossDocumentPipelineError(stage, code) from exc
        self.registry.record_model_call(
            model_call_id=call_id,
            run_id=self.run_id,
            tier=ModelTier.M1.value,
            model=result.model,
            status="SUCCEEDED",
            input_tokens=result.input_tokens,
            output_tokens=None,
            latency_ms=result.latency_ms,
            error_code=None,
            metadata={"input_count": len(texts), "dimensions": result.dimensions},
            stage=stage,
            prompt_version=PROMPT_VERSION,
            input_hash=input_hash,
        )
        self.summaries.append(
            ModelCallSummary(
                stage=stage,
                tier=ModelTier.M1.value,
                model=result.model,
                input_tokens=result.input_tokens,
                latency_ms=result.latency_ms,
            )
        )
        return result

    def typed(
        self,
        *,
        tier: ModelTier,
        stage: str,
        request: StructuredModelRequest,
        output_type: type[_T],
        validator: Callable[[_T], None],
    ) -> _T:
        request_key = _hash_json({"stage": stage, "user": request.user_prompt})[:16]

        def audit_validation_failure(
            exc: ValidationError | ValueError, *, attempt: str
        ) -> None:
            if isinstance(exc, ValidationError):
                all_errors = exc.errors(include_input=False, include_url=False)
                errors = [
                    {
                        "loc": [str(part) for part in error["loc"]],
                        "type": str(error["type"]),
                    }
                    for error in all_errors[:100]
                ]
                payload: dict[str, object] = {
                    "stage": stage,
                    "attempt": attempt,
                    "error_kind": "schema_validation",
                    "error_count": len(all_errors),
                    "errors_truncated": len(all_errors) > len(errors),
                    "errors": errors,
                }
            else:
                payload = {
                    "stage": stage,
                    "attempt": attempt,
                    "error_kind": "semantic_validation",
                    "error_code": "requested_pair_coverage"
                    if "cover exactly" in str(exc).casefold()
                    else "semantic_constraint",
                }
            self.registry.append_decision_audit(
                DecisionAuditRecord(
                    audit_id=(
                        f"cross-structured-validation:{self.run_id}:{stage}:"
                        f"{request_key}:{attempt}"
                    ),
                    run_id=self.run_id,
                    decision_type="CROSS_STRUCTURED_VALIDATION_FAILURE",
                    subject_id=stage,
                    payload=payload,
                )
            )

        def repair_and_validate(invalid_payload: object, validation_error: object) -> _T:
            repair = StructuredModelRequest(
                system_prompt=(
                    "Repair the prior invalid CDECR output. Return only a corrected JSON object; "
                    "do not add IDs that were absent from the request."
                ),
                user_prompt=json.dumps(
                    {
                        "original_request": request.user_prompt,
                        "invalid_payload": invalid_payload,
                        "validation_error": validation_error,
                    },
                    ensure_ascii=False,
                ),
                json_schema=output_type.model_json_schema(),
            )
            try:
                repaired = self._structured(
                    tier=tier, stage=stage, request=repair, repaired=True
                )
                output = output_type.model_validate(repaired.payload)
                validator(output)
                return output
            except (CrossDocumentPipelineError, ValidationError, ValueError) as exc:
                if isinstance(exc, (ValidationError, ValueError)):
                    audit_validation_failure(exc, attempt="repair")
                raise CrossDocumentPipelineError(stage, "structured_output_invalid") from exc

        try:
            result = self._structured(tier=tier, stage=stage, request=request, repaired=False)
        except CrossDocumentPipelineError as first_error:
            if (
                first_error.code in {"invalid_json", "invalid_json_shape"}
                and first_error.repair_payload is not None
            ):
                return repair_and_validate(first_error.repair_payload, first_error.code)
            raise
        try:
            output = output_type.model_validate(result.payload)
            validator(output)
            return output
        except (ValidationError, ValueError) as first_error:
            audit_validation_failure(first_error, attempt="initial")
            if isinstance(first_error, ValidationError):
                validation_error: object = first_error.errors(
                    include_input=False, include_url=False
                )
            else:
                validation_error = str(first_error)
            return repair_and_validate(result.payload, validation_error)

    def _structured(
        self,
        *,
        tier: ModelTier,
        stage: str,
        request: StructuredModelRequest,
        repaired: bool,
    ) -> StructuredModelResult:
        call_stage = f"{stage}_repair" if repaired else stage
        client = self.m3_client if tier is ModelTier.M3 else self.m2_client
        model = self.model_m3 if tier is ModelTier.M3 else self.model_m2
        call_id = str(uuid.uuid4())
        input_hash = _hash_json({"system": request.system_prompt, "user": request.user_prompt})
        schema_hash = _hash_json(request.json_schema)
        try:
            result = client.complete(request)
        except Exception as exc:
            latency, code = _safe_error(exc)
            self.registry.record_model_call(
                model_call_id=call_id,
                run_id=self.run_id,
                tier=tier.value,
                model=model,
                status="FAILED",
                input_tokens=exc.input_tokens if isinstance(exc, ModelAdapterError) else None,
                output_tokens=exc.output_tokens if isinstance(exc, ModelAdapterError) else None,
                latency_ms=latency,
                error_code=code,
                metadata={},
                stage=call_stage,
                prompt_version=PROMPT_VERSION,
                schema_hash=schema_hash,
                input_hash=input_hash,
            )
            self.summaries.append(
                ModelCallSummary(
                    stage=call_stage,
                    tier=tier.value,
                    model=model,
                    input_tokens=(
                        exc.input_tokens if isinstance(exc, ModelAdapterError) else None
                    ),
                    output_tokens=(
                        exc.output_tokens if isinstance(exc, ModelAdapterError) else None
                    ),
                    latency_ms=latency,
                    status="FAILED",
                    error_code=code,
                    repaired=repaired,
                )
            )
            raise CrossDocumentPipelineError(
                call_stage,
                code,
                repair_payload=(
                    exc.raw_response_text if isinstance(exc, ModelAdapterError) else None
                ),
            ) from exc
        self.registry.record_model_call(
            model_call_id=call_id,
            run_id=self.run_id,
            tier=tier.value,
            model=result.model,
            status="SUCCEEDED",
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            latency_ms=result.latency_ms,
            error_code=None,
            metadata={},
            stage=call_stage,
            prompt_version=PROMPT_VERSION,
            schema_hash=schema_hash,
            input_hash=input_hash,
        )
        self.summaries.append(
            ModelCallSummary(
                stage=call_stage,
                tier=tier.value,
                model=result.model,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                latency_ms=result.latency_ms,
                repaired=repaired,
            )
        )
        return result


class CrossDocumentEngine:
    def __init__(
        self,
        *,
        registry: CDECRRegistry,
        embedding_client: EmbeddingClient,
        m2_client: StructuredModelClient,
        m3_client: StructuredModelClient,
        model_m1: str = "text-embedding-v4",
        model_m2: str = "deepseek-v4-flash",
        model_m3: str = "qwen3.7-plus",
    ) -> None:
        self.registry = registry
        self.embedding_client = embedding_client
        self.m2_client = m2_client
        self.m3_client = m3_client
        self.model_m1 = model_m1
        self.model_m2 = model_m2
        self.model_m3 = model_m3

    @property
    def model_config(self) -> dict[str, object]:
        return {
            "m1": self.model_m1,
            "m2": self.model_m2,
            "m3": self.model_m3,
            "atomic_top_k": ATOMIC_TOP_K,
            "package_top_k": PACKAGE_TOP_K,
            "structured_output_mode": STRUCTURED_OUTPUT_MODE,
            "reasoning_effort": STRUCTURED_REASONING_EFFORT,
            "schema_projection": "disabled_for_model_output",
        }

    def processing_key(self, message_id: str, mentions: Sequence[EventMention]) -> str:
        return _hash_json(
            {
                "message_id": message_id,
                "mention_ids": [mention.mention_id for mention in mentions],
                "mention_hashes": [
                    _hash_json(mention.model_dump(mode="json")) for mention in mentions
                ],
                "engine_version": ENGINE_VERSION,
                "prompt_version": PROMPT_VERSION,
                "model_config": self.model_config,
            }
        )

    def process(self, message_id: str) -> CrossDocumentResult:
        source = self.registry.get_source(message_id)
        if source is None:
            raise ValueError(f"unknown source message {message_id!r}")
        document_result = self.registry.get_latest_completed_document_result_for_message(message_id)
        mentions = (
            list(document_result.mentions)
            if document_result is not None
            else self.registry.list_mentions_for_message(message_id)
        )
        mentions.sort(key=lambda item: item.mention_id)
        processing_key = self.processing_key(message_id, mentions)
        completed = self.registry.get_completed_cross_document_result(processing_key)
        if completed is not None:
            return completed.model_copy(update={"reused": True, "model_calls": []})

        run_id = str(uuid.uuid4())
        started_at = datetime.now(UTC)
        started = self.registry.start_cross_document_run(
            run_id=run_id,
            processing_key=processing_key,
            message_id=message_id,
            engine_version=ENGINE_VERSION,
            prompt_version=PROMPT_VERSION,
            model_config=self.model_config,
        )
        if not started:
            completed = self.registry.get_completed_cross_document_result(processing_key)
            if completed is not None:
                return completed.model_copy(update={"reused": True, "model_calls": []})
            raise CrossDocumentPipelineError("registry", "run_not_started")

        summaries: list[ModelCallSummary] = []
        atomic_assignments: list[AtomicAssignmentRecord] = []
        package_assignments: list[PackageAssignmentRecord] = []
        holds: list[str] = []
        candidate_counts = {
            "atomic_recalled": 0,
            "atomic_hard_blocked": 0,
            "package_recalled": 0,
            "package_hard_blocked": 0,
        }
        try:
            models = _AuditedModels(
                registry=self.registry,
                run_id=run_id,
                embedding_client=self.embedding_client,
                m2_client=self.m2_client,
                m3_client=self.m3_client,
                model_m1=self.model_m1,
                model_m2=self.model_m2,
                model_m3=self.model_m3,
                summaries=summaries,
            )
            mention_vectors = self._embed_mentions(mentions, models)
            candidates = self._atomic_candidates(
                mentions, mention_vectors, run_id=run_id, candidate_counts=candidate_counts
            )
            decisions = self._atomic_decisions(mentions, candidates, models)
            atomic_events, atomic_assignments, atomic_holds = self._apply_atomic(
                mentions,
                candidates,
                decisions,
                mention_vectors,
                run_id=run_id,
            )
            holds.extend(atomic_holds)
            atomic_events = self._correct_atomic(atomic_events, mentions, run_id=run_id)
            packages, package_assignments, package_holds = self._assign_packages(
                atomic_events,
                mentions,
                mention_vectors,
                models,
                run_id=run_id,
                candidate_counts=candidate_counts,
            )
            holds.extend(package_holds)
            packages, correction_holds = self._correct_packages(packages, models, run_id=run_id)
            holds.extend(correction_holds)
            result = CrossDocumentResult(
                run_id=run_id,
                processing_key=processing_key,
                message_id=message_id,
                status=CrossDocumentStatus.SUCCEEDED,
                atomic_events=atomic_events,
                packages=packages,
                atomic_assignments=atomic_assignments,
                package_assignments=package_assignments,
                hold_ids=holds,
                model_calls=summaries,
                candidate_counts=candidate_counts,
                started_at=started_at,
                finished_at=datetime.now(UTC),
            )
            self.registry.complete_cross_document_run(result)
            return result
        except Exception as exc:
            stage = exc.stage if isinstance(exc, CrossDocumentPipelineError) else "orchestration"
            code = exc.code if isinstance(exc, CrossDocumentPipelineError) else type(exc).__name__
            safe_frames = [
                {
                    "file": Path(frame.filename).name,
                    "function": frame.name,
                    "line": frame.lineno,
                }
                for frame in traceback.extract_tb(exc.__traceback__)
                if "cdecr" in Path(frame.filename).parts
            ][-12:]
            self.registry.append_decision_audit(
                DecisionAuditRecord(
                    audit_id=f"cross-document-failure:{run_id}",
                    run_id=run_id,
                    decision_type="CROSS_DOCUMENT_FAILURE",
                    subject_id=message_id,
                    payload={"stage": stage, "error_code": code, "frames": safe_frames},
                )
            )
            self.registry.fail_cross_document_run(run_id, error_code=code)
            return CrossDocumentResult(
                run_id=run_id,
                processing_key=processing_key,
                message_id=message_id,
                status=CrossDocumentStatus.FAILED,
                atomic_events=[],
                packages=[],
                atomic_assignments=atomic_assignments,
                package_assignments=package_assignments,
                hold_ids=holds,
                model_calls=summaries,
                candidate_counts=candidate_counts,
                failure_stage=stage,
                error_code=code,
                started_at=started_at,
                finished_at=datetime.now(UTC),
            )

    def process_batch(self, message_ids: Sequence[str]) -> list[CrossDocumentResult]:
        return [self.process(message_id) for message_id in message_ids]

    def _embed_mentions(
        self, mentions: list[EventMention], models: _AuditedModels
    ) -> dict[str, list[float]]:
        stored = {
            item.owner_id: item
            for item in self.registry.list_latest_embeddings(
                owner_kind="event_mention", model=self.model_m1, limit=100000
            )
        }
        vectors: dict[str, list[float]] = {}
        missing: list[EventMention] = []
        for mention in mentions:
            input_hash = hashlib.sha256(
                mention.canonical_proposition.encode("utf-8")
            ).hexdigest()
            existing = stored.get(mention.mention_id)
            if existing is not None and existing.input_hash == input_hash:
                vectors[mention.mention_id] = existing.vector
            else:
                missing.append(mention)
        for offset in range(0, len(missing), 10):
            batch = missing[offset : offset + 10]
            if not batch:
                continue
            result = models.embed(
                [mention.canonical_proposition for mention in batch], stage="atomic_recall_m1"
            )
            for mention, vector in zip(batch, result.vectors, strict=True):
                vectors[mention.mention_id] = vector
                self.registry.save_embedding(
                    owner_kind="event_mention",
                    owner_id=mention.mention_id,
                    model=result.model,
                    input_hash=hashlib.sha256(
                        mention.canonical_proposition.encode("utf-8")
                    ).hexdigest(),
                    vector=vector,
                )
        return vectors

    def _atomic_candidates(
        self,
        mentions: list[EventMention],
        mention_vectors: dict[str, list[float]],
        *,
        run_id: str,
        candidate_counts: dict[str, int],
    ) -> dict[str, list[AtomicCandidate]]:
        active_events = {
            event.event_id: event for event in self.registry.list_current_atomic_events(limit=10000)
        }
        stored_embeddings = {
            item.owner_id: item.vector
            for item in self.registry.list_latest_embeddings(
                owner_kind="atomic_event", model=self.model_m1, limit=100000
            )
            if item.owner_id in active_events
        }
        output: dict[str, list[AtomicCandidate]] = {}
        provisional: list[tuple[AtomicEvent, EventMention]] = []
        for mention in mentions:
            if self.registry.get_atomic_event_for_mention(mention.mention_id) is not None:
                output[mention.mention_id] = []
                provisional.append((singleton_atomic_event(mention, provisional=True), mention))
                continue
            profile = identity_profile_for_mention(mention)
            start = mention.time.event_start.isoformat() if mention.time.event_start else None
            end = mention.time.event_end.isoformat() if mention.time.event_end else start
            source_fingerprint = self.registry.get_source_fingerprint(mention.message_id)
            route_ids = self.registry.recall_atomic_event_ids(
                entity_ids=core_entity_ids_from_mention(mention),
                event_family=mention.event_family.value,
                normalized_predicate=mention.predicate.normalized,
                schema_type=profile.schema_type,
                reference_period_id=reference_period_from_profile(profile),
                event_start=start,
                event_end=end,
                source_fingerprint=source_fingerprint,
                per_route_limit=20,
            )
            scores: dict[str, float] = {}
            routes: dict[str, set[RecallRoute]] = {
                event_id: {RecallRoute(value) for value in values}
                for event_id, values in route_ids.items()
                if event_id in active_events
            }
            vector = mention_vectors.get(mention.mention_id)
            if vector is not None:
                for event_id, candidate_vector in stored_embeddings.items():
                    score = _cosine(vector, candidate_vector)
                    scores[event_id] = score
                    if score >= EMBEDDING_RECALL_THRESHOLD:
                        routes.setdefault(event_id, set()).add(RecallRoute.PROPOSITION_EMBEDDING)
            for provisional_event, prior_mention in provisional:
                provisional_routes = _provisional_routes(mention, prior_mention)
                score = _cosine(vector or [], mention_vectors.get(prior_mention.mention_id, []))
                if score >= EMBEDDING_RECALL_THRESHOLD:
                    provisional_routes.add(RecallRoute.PROPOSITION_EMBEDDING)
                if provisional_routes:
                    active_events[provisional_event.event_id] = provisional_event
                    routes[provisional_event.event_id] = provisional_routes
                    scores[provisional_event.event_id] = score
            ranked: list[AtomicCandidate] = []
            for event_id, recall_routes in routes.items():
                event = active_events[event_id]
                representatives = [
                    item
                    for item in (
                        self.registry.get_mention(value)
                        for value in event.representative_mention_ids
                    )
                    if item is not None
                ]
                if event_id.startswith("provisional:"):
                    representatives = [
                        prior
                        for provisional_event, prior in provisional
                        if provisional_event.event_id == event_id
                    ]
                conflicts = hard_cannot_link(
                    mention, event, representative_mentions=representatives
                )
                route_score = min(1.0, 0.35 + 0.12 * len(recall_routes))
                score = max(route_score, (scores.get(event_id, 0.0) + 1.0) / 2.0)
                ranked.append(
                    AtomicCandidate(
                        event=event,
                        recall_routes=sorted(recall_routes, key=str),
                        recall_score=score,
                        hard_conflicts=conflicts,
                    )
                )
                if conflicts:
                    self.registry.append_decision_audit(
                        DecisionAuditRecord(
                            audit_id=stable_id(
                                "audit",
                                {"run": run_id, "mention": mention.mention_id, "event": event_id},
                            ),
                            run_id=run_id,
                            decision_type="ATOMIC_HARD_CANNOT_LINK",
                            subject_id=mention.mention_id,
                            payload={
                                "candidate_event_id": event_id,
                                "conflicts": [value.value for value in conflicts],
                            },
                        )
                    )
            ranked.sort(
                key=lambda item: (
                    bool(item.hard_conflicts),
                    -item.recall_score,
                    item.event.event_id,
                )
            )
            output[mention.mention_id] = ranked[:ATOMIC_TOP_K]
            candidate_counts["atomic_recalled"] += len(output[mention.mention_id])
            candidate_counts["atomic_hard_blocked"] += sum(
                bool(item.hard_conflicts) for item in output[mention.mention_id]
            )
            provisional.append((singleton_atomic_event(mention, provisional=True), mention))
        return output

    def _atomic_decisions(
        self,
        mentions: list[EventMention],
        candidates: dict[str, list[AtomicCandidate]],
        models: _AuditedModels,
    ) -> dict[tuple[str, str], AtomicPairDecision]:
        eligible = {
            mention.mention_id: [
                item for item in candidates[mention.mention_id] if not item.hard_conflicts
            ]
            for mention in mentions
        }
        decisions: dict[tuple[str, str], AtomicPairDecision] = {}
        eligible_mentions = [mention for mention in mentions if eligible[mention.mention_id]]
        batches = [
            eligible_mentions[offset : offset + ATOMIC_DECISION_MENTION_BATCH]
            for offset in range(0, len(eligible_mentions), ATOMIC_DECISION_MENTION_BATCH)
        ]

        def process_batch(
            indexed_batch: tuple[int, list[EventMention]],
        ) -> tuple[int, AtomicDecisionBatch]:
            batch_index, batch_mentions = indexed_batch
            batch_ids = {mention.mention_id for mention in batch_mentions}
            batch_candidates = {
                mention_id: values
                for mention_id, values in eligible.items()
                if mention_id in batch_ids
            }
            expected = {
                (mention_id, candidate.event.event_id)
                for mention_id, values in batch_candidates.items()
                for candidate in values
            }
            tier = (
                ModelTier.M3
                if any(
                    mention.event_family in HIGH_IMPACT_FAMILIES
                    or len(batch_candidates[mention.mention_id]) >= 4
                    for mention in batch_mentions
                )
                else ModelTier.M2
            )
            request = StructuredModelRequest(
                system_prompt=_prompt("atomic_coreference.md"),
                user_prompt=json.dumps(
                    {
                        "batch_index": batch_index,
                        "batch_count": len(batches),
                        "mentions": [
                            mention.model_dump(mode="json") for mention in batch_mentions
                        ],
                        "candidates": {
                            mention_id: [item.model_dump(mode="json") for item in values]
                            for mention_id, values in batch_candidates.items()
                        },
                    },
                    ensure_ascii=False,
                ),
                json_schema=AtomicDecisionBatch.model_json_schema(),
            )

            def validate(
                output: AtomicDecisionBatch,
                *,
                expected: set[tuple[str, str]] = expected,
            ) -> None:
                actual = {
                    (item.mention_id, item.candidate_event_id) for item in output.decisions
                }
                if actual != expected:
                    raise ValueError(
                        "atomic decision output must cover exactly the requested pairs"
                    )

            output = models.typed(
                tier=tier,
                stage="atomic_coreference",
                request=request,
                output_type=AtomicDecisionBatch,
                validator=validate,
            )
            return batch_index, output

        if not batches:
            return decisions
        with ThreadPoolExecutor(max_workers=min(MODEL_CONCURRENCY, len(batches))) as executor:
            outputs = list(executor.map(process_batch, enumerate(batches)))
        for _, output in sorted(outputs):
            decisions.update(
                {
                    (item.mention_id, item.candidate_event_id): item
                    for item in output.decisions
                }
            )
        return decisions

    def _apply_atomic(
        self,
        mentions: list[EventMention],
        candidates: dict[str, list[AtomicCandidate]],
        decisions: dict[tuple[str, str], AtomicPairDecision],
        mention_vectors: dict[str, list[float]],
        *,
        run_id: str,
    ) -> tuple[list[AtomicEvent], list[AtomicAssignmentRecord], list[str]]:
        touched: dict[str, AtomicEvent] = {}
        assignments: list[AtomicAssignmentRecord] = []
        holds: list[str] = []
        provisional_map: dict[str, str | None] = {}
        known_mentions = {
            mention.mention_id: mention
            for event in self.registry.list_current_atomic_events(limit=10000)
            for mention_id in event.mention_ids
            if (mention := self.registry.get_mention(mention_id)) is not None
        }
        known_mentions.update({mention.mention_id: mention for mention in mentions})
        for mention in mentions:
            provisional_id = singleton_atomic_event(mention, provisional=True).event_id
            recovered = self.registry.get_atomic_event_for_mention(mention.mention_id)
            if recovered is not None:
                touched[recovered.event_id] = recovered
                provisional_map[provisional_id] = recovered.event_id
                record = self._atomic_record(
                    run_id=run_id,
                    mention=mention,
                    candidate_event_id=recovered.event_id,
                    resulting_event_id=recovered.event_id,
                    action=AtomicAction.MERGE,
                    relation=AtomicSemanticRelation.SAME_EVENT,
                    reason="RECOVERED_EXISTING_ASSIGNMENT",
                )
                self.registry.save_atomic_assignment(record)
                assignments.append(record)
                continue
            eligible = [item for item in candidates[mention.mention_id] if not item.hard_conflicts]
            blocked_conflicts = sorted(
                {
                    conflict
                    for item in candidates[mention.mention_id]
                    for conflict in item.hard_conflicts
                },
                key=str,
            )
            pair_decisions = [
                decisions[(mention.mention_id, item.event.event_id)]
                for item in eligible
                if (mention.mention_id, item.event.event_id) in decisions
            ]
            selected, action, reason = _select_atomic_action(pair_decisions)
            if not eligible:
                action, reason = AtomicAction.CREATE_NEW, "NO_ELIGIBLE_CANDIDATE"
            if action is AtomicAction.HOLD:
                hold = HoldRecord(
                    hold_id=stable_id("hold", {"run": run_id, "mention": mention.mention_id}),
                    run_id=run_id,
                    kind=HoldKind.ATOMIC_ASSIGNMENT,
                    subject_id=mention.mention_id,
                    candidate_ids=[item.event.event_id for item in eligible],
                    reason_codes=[reason],
                    payload={
                        "decisions": [item.model_dump(mode="json") for item in pair_decisions]
                    },
                )
                self.registry.save_hold(hold)
                holds.append(hold.hold_id)
                record = self._atomic_record(
                    run_id=run_id,
                    mention=mention,
                    action=action,
                    reason=reason,
                    hard_conflicts=blocked_conflicts,
                )
                self.registry.save_atomic_assignment(record)
                assignments.append(record)
                provisional_map[provisional_id] = None
                continue

            candidate_event_id: str | None = None
            relation: AtomicSemanticRelation | None = None
            claim_conflict = False
            identity_conflicts: list[str] = []
            if selected is not None:
                relation = selected.relation
                claim_conflict = selected.claim_conflict
                identity_conflicts = selected.identity_conflicts
                candidate_event_id = selected.candidate_event_id
                if candidate_event_id.startswith("provisional:"):
                    candidate_event_id = provisional_map.get(candidate_event_id)
                    if candidate_event_id is None:
                        action = AtomicAction.HOLD
                        reason = "PROVISIONAL_TARGET_NOT_ASSIGNED"
            if action is AtomicAction.HOLD:
                hold = HoldRecord(
                    hold_id=stable_id("hold", {"run": run_id, "mention": mention.mention_id}),
                    run_id=run_id,
                    kind=HoldKind.ATOMIC_ASSIGNMENT,
                    subject_id=mention.mention_id,
                    candidate_ids=[value for value in [candidate_event_id] if value],
                    reason_codes=[reason],
                    payload={},
                )
                self.registry.save_hold(hold)
                holds.append(hold.hold_id)
                record = self._atomic_record(
                    run_id=run_id,
                    mention=mention,
                    action=action,
                    reason=reason,
                    hard_conflicts=blocked_conflicts,
                )
                self.registry.save_atomic_assignment(record)
                assignments.append(record)
                provisional_map[provisional_id] = None
                continue

            if action is AtomicAction.MERGE and candidate_event_id is not None:
                target = touched.get(candidate_event_id) or self.registry.get_current_atomic_event(
                    candidate_event_id
                )
                if target is None:
                    raise CrossDocumentPipelineError("atomic_update", "candidate_missing")
                event = add_mention_to_atomic(
                    target,
                    mention,
                    known_mentions=list(known_mentions.values()),
                    claim_conflict=claim_conflict,
                    identity_conflicts=identity_conflicts,
                )
                self.registry.save_atomic_event(event)
            else:
                event = singleton_atomic_event(mention)
                self.registry.save_atomic_event(event)
                if action is AtomicAction.CREATE_AND_LINK and candidate_event_id is not None:
                    relation_object = ExternalEventRelation(
                        relation_id=stable_id(
                            "relation",
                            {
                                "source": event.event_id,
                                "target": candidate_event_id,
                                "type": ExternalRelationType.RELATED_TO.value,
                            },
                        ),
                        source_event_id=event.event_id,
                        target_event_id=candidate_event_id,
                        relation=ExternalRelationType.RELATED_TO,
                    )
                    self.registry.save_external_relation(relation_object)
            vector = mention_vectors.get(mention.mention_id)
            if vector is not None:
                self.registry.save_embedding(
                    owner_kind="atomic_event",
                    owner_id=event.event_id,
                    model=self.model_m1,
                    input_hash=hashlib.sha256(
                        event.canonical_proposition.encode("utf-8")
                    ).hexdigest(),
                    vector=vector,
                )
            touched[event.event_id] = event
            provisional_map[provisional_id] = event.event_id
            record = self._atomic_record(
                run_id=run_id,
                mention=mention,
                candidate_event_id=candidate_event_id,
                resulting_event_id=event.event_id,
                action=action,
                relation=relation,
                claim_conflict=claim_conflict,
                identity_conflicts=identity_conflicts,
                reason=reason,
                hard_conflicts=blocked_conflicts,
            )
            self.registry.save_atomic_assignment(record)
            self.registry.append_decision_audit(
                DecisionAuditRecord(
                    audit_id=stable_id("audit", {"assignment": record.assignment_id}),
                    run_id=run_id,
                    decision_type="ATOMIC_ASSIGNMENT",
                    subject_id=mention.mention_id,
                    payload=record.model_dump(mode="json"),
                )
            )
            assignments.append(record)
        return list(touched.values()), assignments, holds

    def _atomic_record(
        self,
        *,
        run_id: str,
        mention: EventMention,
        action: AtomicAction,
        reason: str,
        candidate_event_id: str | None = None,
        resulting_event_id: str | None = None,
        relation: AtomicSemanticRelation | None = None,
        claim_conflict: bool = False,
        identity_conflicts: list[str] | None = None,
        hard_conflicts: list[HardConflictCode] | None = None,
    ) -> AtomicAssignmentRecord:
        return AtomicAssignmentRecord(
            assignment_id=stable_id(
                "atomic-assignment", {"run": run_id, "mention": mention.mention_id}
            ),
            run_id=run_id,
            mention_id=mention.mention_id,
            candidate_event_id=candidate_event_id,
            resulting_event_id=resulting_event_id,
            action=action,
            relation=relation,
            hard_conflicts=hard_conflicts or [],
            claim_conflict=claim_conflict,
            identity_conflicts=identity_conflicts or [],
            reason=reason,
        )

    def _correct_atomic(
        self, events: list[AtomicEvent], mentions: list[EventMention], *, run_id: str
    ) -> list[AtomicEvent]:
        active = {item.event_id: item for item in events}
        all_current = self.registry.list_current_atomic_events(limit=10000)
        known_mentions = {
            mention.mention_id: mention
            for event in all_current
            for mention_id in event.mention_ids
            if (mention := self.registry.get_mention(mention_id)) is not None
        }
        known_mentions.update({item.mention_id: item for item in mentions})
        for event in list(active.values()):
            for other in all_current:
                if event.event_id == other.event_id:
                    continue
                if (
                    event.identity_profile.model_dump(mode="json")
                    != other.identity_profile.model_dump(mode="json")
                    or event.canonical_proposition.casefold()
                    != other.canonical_proposition.casefold()
                ):
                    continue
                target, source = sorted([event, other], key=lambda item: item.event_id)
                current = self.registry.get_current_atomic_event(target.event_id) or target
                for mention_id in source.mention_ids:
                    if mention_id in current.mention_ids:
                        continue
                    mention = known_mentions.get(mention_id)
                    if mention is None:
                        continue
                    current = add_mention_to_atomic(
                        current,
                        mention,
                        known_mentions=list(known_mentions.values()),
                        claim_conflict=False,
                        identity_conflicts=[],
                    )
                    self.registry.save_atomic_event(current)
                self.registry.save_atomic_redirect(
                    source_event_id=source.event_id,
                    target_event_id=target.event_id,
                    run_id=run_id,
                    reason="M0_OBVIOUS_DUPLICATE",
                )
                active.pop(source.event_id, None)
                active[target.event_id] = current
        return list(active.values())

    def _assign_packages(
        self,
        events: list[AtomicEvent],
        article_mentions: list[EventMention],
        mention_vectors: dict[str, list[float]],
        models: _AuditedModels,
        *,
        run_id: str,
        candidate_counts: dict[str, int],
    ) -> tuple[list[EventPackage], list[PackageAssignmentRecord], list[str]]:
        events = list({event.event_id: event for event in events}.values())
        seeds: dict[str, PackageSeed] = {}
        candidates: dict[str, list[PackageCandidate]] = {}
        existing_assignments: dict[str, EventPackage] = {}
        package_embeddings = {
            item.owner_id: item.vector
            for item in self.registry.list_latest_embeddings(
                owner_kind="event_package", model=self.model_m1, limit=100000
            )
        }
        article_map = {item.mention_id: item for item in article_mentions}
        for event in events:
            existing = self.registry.list_packages_for_event(event.event_id)
            if existing:
                existing_assignments[event.event_id] = existing[0]
                candidates[event.event_id] = []
                continue
            event_mentions = [
                mention
                for mention_id in event.mention_ids
                if (mention := article_map.get(mention_id) or self.registry.get_mention(mention_id))
                is not None
            ]
            seed = package_seed_for_event(event, event_mentions)
            seeds[event.event_id] = seed
            start = seed.time_range.start.isoformat() if seed.time_range.start else None
            end = seed.time_range.end.isoformat() if seed.time_range.end else start
            recalled = self.registry.recall_package_ids(
                package_kind=seed.package_kind.value,
                package_family=seed.package_family.value,
                anchor_entities=seed.anchor_entities,
                local_anchor_hint=seed.local_anchor_hint,
                anchor_artifact_id=seed.anchor_artifact_id,
                anchor_period_id=seed.anchor_period_id,
                time_start=start,
                time_end=end,
                per_route_limit=20,
            )
            vector = next(
                (mention_vectors[value] for value in event.mention_ids if value in mention_vectors),
                None,
            )
            scores: dict[str, float] = {}
            if vector is not None:
                for package_id, candidate_vector in package_embeddings.items():
                    score = _cosine(vector, candidate_vector)
                    scores[package_id] = score
                    if score >= EMBEDDING_RECALL_THRESHOLD:
                        recalled.setdefault(package_id, set()).add("PROPOSITION_EMBEDDING")
            ranked: list[PackageCandidate] = []
            for package_id, route_names in recalled.items():
                package = self.registry.get_current_package(package_id)
                if package is None:
                    continue
                conflicts = package_hard_conflicts(seed, package)
                route_score = min(1.0, 0.35 + 0.12 * len(route_names))
                score = max(route_score, (scores.get(package_id, 0.0) + 1.0) / 2.0)
                ranked.append(
                    PackageCandidate(
                        package=package,
                        recall_routes=sorted(
                            {RecallRoute(value) for value in route_names}, key=str
                        ),
                        recall_score=score,
                        hard_conflicts=conflicts,
                    )
                )
                if conflicts:
                    self.registry.append_decision_audit(
                        DecisionAuditRecord(
                            audit_id=stable_id(
                                "audit",
                                {
                                    "run": run_id,
                                    "event": event.event_id,
                                    "package": package_id,
                                },
                            ),
                            run_id=run_id,
                            decision_type="PACKAGE_HARD_CANNOT_LINK",
                            subject_id=event.event_id,
                            payload={
                                "candidate_package_id": package_id,
                                "conflicts": [value.value for value in conflicts],
                            },
                        )
                    )
            ranked.sort(
                key=lambda item: (
                    bool(item.hard_conflicts),
                    -item.recall_score,
                    item.package.package_id,
                )
            )
            candidates[event.event_id] = ranked[:PACKAGE_TOP_K]
            candidate_counts["package_recalled"] += len(candidates[event.event_id])
            candidate_counts["package_hard_blocked"] += sum(
                bool(item.hard_conflicts) for item in candidates[event.event_id]
            )

        deterministic: dict[str, PackagePairDecision] = {}
        unresolved: dict[str, list[PackageCandidate]] = {}
        for event in events:
            if event.event_id in existing_assignments:
                continue
            seed = seeds[event.event_id]
            eligible = [item for item in candidates[event.event_id] if not item.hard_conflicts]
            exact_matches = [
                item for item in eligible if bounded_package_exact_match(seed, item.package)
            ]
            if len(exact_matches) == 1:
                exact = exact_matches[0]
                deterministic[event.event_id] = PackagePairDecision(
                    event_id=event.event_id,
                    candidate_package_id=exact.package.package_id,
                    relation=PackageAssignmentRelation.MEMBER,
                    membership_relation=seed.membership_relation,
                )
                continue
            reaction = _reaction_candidate(event, eligible)
            if reaction is not None:
                deterministic[event.event_id] = reaction
                continue
            if eligible:
                unresolved[event.event_id] = eligible

        semantic: dict[tuple[str, str], PackagePairDecision] = {}
        if unresolved:
            event_map = {event.event_id: event for event in events}
            unresolved_ids = sorted(unresolved)
            batches = [
                unresolved_ids[offset : offset + PACKAGE_DECISION_EVENT_BATCH]
                for offset in range(0, len(unresolved_ids), PACKAGE_DECISION_EVENT_BATCH)
            ]

            def process_package_batch(
                indexed_batch: tuple[int, list[str]],
            ) -> tuple[int, PackageDecisionBatch]:
                batch_index, event_ids = indexed_batch
                batch_candidates = {event_id: unresolved[event_id] for event_id in event_ids}
                expected = {
                    (event_id, candidate.package.package_id)
                    for event_id, values in batch_candidates.items()
                    for candidate in values
                }
                tier = (
                    ModelTier.M3
                    if any(len(values) >= 4 for values in batch_candidates.values())
                    else ModelTier.M2
                )
                request = StructuredModelRequest(
                    system_prompt=_prompt("package_assignment.md"),
                    user_prompt=json.dumps(
                        {
                            "batch_index": batch_index,
                            "batch_count": len(batches),
                            "events": {
                                event_id: event_map[event_id].model_dump(mode="json")
                                for event_id in event_ids
                            },
                            "seeds": {
                                event_id: seeds[event_id].model_dump(mode="json")
                                for event_id in event_ids
                            },
                            "candidates": {
                                event_id: [item.model_dump(mode="json") for item in values]
                                for event_id, values in batch_candidates.items()
                            },
                        },
                        ensure_ascii=False,
                    ),
                    json_schema=PackageDecisionBatch.model_json_schema(),
                )

                def validate(output: PackageDecisionBatch) -> None:
                    actual = {
                        (item.event_id, item.candidate_package_id)
                        for item in output.decisions
                    }
                    if actual != expected:
                        raise ValueError("package decisions must cover exactly requested pairs")

                output = models.typed(
                    tier=tier,
                    stage="package_assignment",
                    request=request,
                    output_type=PackageDecisionBatch,
                    validator=validate,
                )
                return batch_index, output

            with ThreadPoolExecutor(
                max_workers=min(MODEL_CONCURRENCY, len(batches))
            ) as executor:
                outputs = list(executor.map(process_package_batch, enumerate(batches)))
            for _, output in sorted(outputs):
                semantic.update(
                    {
                        (item.event_id, item.candidate_package_id): item
                        for item in output.decisions
                    }
                )

        packages: dict[str, EventPackage] = {
            package.package_id: package for package in existing_assignments.values()
        }
        assignments: list[PackageAssignmentRecord] = []
        holds: list[str] = []
        for event in events:
            if event.event_id in existing_assignments:
                package = existing_assignments[event.event_id]
                record = self._package_record(
                    run_id=run_id,
                    event=event,
                    action=PackageAction.ADD_TO_PACKAGE,
                    relation=PackageAssignmentRelation.MEMBER,
                    candidate_package_id=package.package_id,
                    resulting_package_id=package.package_id,
                    reason="RECOVERED_EXISTING_MEMBERSHIP",
                )
                self.registry.save_package_assignment(record)
                assignments.append(record)
                continue
            seed = seeds[event.event_id]
            pair_values = [
                semantic[(event.event_id, item.package.package_id)]
                for item in candidates[event.event_id]
                if (event.event_id, item.package.package_id) in semantic
            ]
            selected = deterministic.get(event.event_id)
            if selected is None:
                selected = _select_package_decision(pair_values)
            action, reason = _package_action(
                selected, pair_values, bool(candidates[event.event_id])
            )
            if action is PackageAction.HOLD:
                hold = HoldRecord(
                    hold_id=stable_id(
                        "hold", {"run": run_id, "event": event.event_id, "kind": "package"}
                    ),
                    run_id=run_id,
                    kind=HoldKind.PACKAGE_ASSIGNMENT,
                    subject_id=event.event_id,
                    candidate_ids=[item.package.package_id for item in candidates[event.event_id]],
                    reason_codes=[reason],
                    payload={"decisions": [item.model_dump(mode="json") for item in pair_values]},
                )
                self.registry.save_hold(hold)
                holds.append(hold.hold_id)
                record = self._package_record(
                    run_id=run_id,
                    event=event,
                    action=action,
                    reason=reason,
                )
                self.registry.save_package_assignment(record)
                assignments.append(record)
                continue
            if action is PackageAction.CREATE_NEW_PACKAGE:
                package = singleton_package(event, seed)
                self.registry.save_package(package)
                relation = seed.membership_relation
                self._save_membership(event, package, relation)
            elif selected is not None and action is PackageAction.ADD_TO_PACKAGE:
                target = self.registry.get_current_package(selected.candidate_package_id)
                if target is None:
                    raise CrossDocumentPipelineError("package_update", "candidate_missing")
                package = add_event_to_package(target, event)
                self.registry.save_package(package)
                relation = selected.membership_relation or seed.membership_relation
                self._save_membership(event, package, relation)
            elif selected is not None and action is PackageAction.LINK_EXTERNALLY:
                package = self.registry.get_current_package(selected.candidate_package_id)
                if package is None or selected.external_relation is None:
                    raise CrossDocumentPipelineError("package_relation", "candidate_missing")
                external_relation_record = PackageExternalRelation(
                    relation_id=stable_id(
                        "package-relation",
                        {
                            "event": event.event_id,
                            "package": package.package_id,
                            "type": selected.external_relation.value,
                        },
                    ),
                    source_event_id=event.event_id,
                    target_package_id=package.package_id,
                    relation=selected.external_relation,
                )
                existing_relation_ids = {
                    item.relation_id
                    for item in self.registry.list_package_external_relations(
                        source_event_id=event.event_id
                    )
                }
                if external_relation_record.relation_id not in existing_relation_ids:
                    self.registry.save_package_external_relation(external_relation_record)
            else:
                raise CrossDocumentPipelineError("package_update", "invalid_action")
            packages[package.package_id] = package
            vector = next(
                (mention_vectors[value] for value in event.mention_ids if value in mention_vectors),
                None,
            )
            if vector is not None:
                self.registry.save_embedding(
                    owner_kind="event_package",
                    owner_id=package.package_id,
                    model=self.model_m1,
                    input_hash=hashlib.sha256(
                        package.canonical_summary.encode("utf-8")
                    ).hexdigest(),
                    vector=vector,
                )
            record = self._package_record(
                run_id=run_id,
                event=event,
                action=action,
                relation=selected.relation if selected else PackageAssignmentRelation.NOT_RELATED,
                candidate_package_id=selected.candidate_package_id if selected else None,
                resulting_package_id=package.package_id,
                reason=reason,
            )
            self.registry.save_package_assignment(record)
            self.registry.append_decision_audit(
                DecisionAuditRecord(
                    audit_id=stable_id("audit", {"assignment": record.assignment_id}),
                    run_id=run_id,
                    decision_type="PACKAGE_ASSIGNMENT",
                    subject_id=event.event_id,
                    payload=record.model_dump(mode="json"),
                )
            )
            assignments.append(record)
        return list(packages.values()), assignments, holds

    def _package_record(
        self,
        *,
        run_id: str,
        event: AtomicEvent,
        action: PackageAction,
        reason: str,
        relation: PackageAssignmentRelation | None = None,
        candidate_package_id: str | None = None,
        resulting_package_id: str | None = None,
    ) -> PackageAssignmentRecord:
        return PackageAssignmentRecord(
            assignment_id=stable_id("package-assignment", {"run": run_id, "event": event.event_id}),
            run_id=run_id,
            event_id=event.event_id,
            candidate_package_id=candidate_package_id,
            resulting_package_id=resulting_package_id,
            action=action,
            relation=relation,
            reason=reason,
        )

    def _save_membership(
        self,
        event: AtomicEvent,
        package: EventPackage,
        relation: MembershipRelation,
    ) -> None:
        self.registry.save_membership(
            PackageMembership(
                membership_id=stable_id(
                    "membership",
                    {
                        "event": event.event_id,
                        "package": package.package_id,
                        "relation": relation.value,
                    },
                ),
                event_id=event.event_id,
                package_id=package.package_id,
                relation=relation,
            )
        )

    def _correct_packages(
        self, packages: list[EventPackage], models: _AuditedModels, *, run_id: str
    ) -> tuple[list[EventPackage], list[str]]:
        active = {item.package_id: item for item in packages}
        all_packages = {
            item.package_id: item for item in self.registry.list_current_packages(limit=10000)
        }
        ambiguous: list[tuple[EventPackage, EventPackage]] = []
        for package in list(active.values()):
            for other in all_packages.values():
                if package.package_id == other.package_id:
                    continue
                if packages_obviously_same(package, other):
                    target, source = sorted([package, other], key=lambda item: item.package_id)
                    merged = self._merge_package_pair(
                        target, source, run_id=run_id, reason="M0_OBVIOUS_DUPLICATE"
                    )
                    active.pop(source.package_id, None)
                    active[target.package_id] = merged
                    continue
                if _ambiguous_episode_pair(package, other):
                    sorted_pair = sorted([package, other], key=lambda item: item.package_id)
                    pair = (sorted_pair[0], sorted_pair[1])
                    if pair not in ambiguous:
                        ambiguous.append(pair)
        holds: list[str] = []
        if ambiguous:
            batches = [
                ambiguous[offset : offset + PACKAGE_MERGE_PAIR_BATCH]
                for offset in range(0, len(ambiguous), PACKAGE_MERGE_PAIR_BATCH)
            ]

            def process_merge_batch(
                indexed_batch: tuple[int, list[tuple[EventPackage, EventPackage]]],
            ) -> tuple[int, PackageMergeDecisionBatch]:
                batch_index, pairs = indexed_batch
                expected = {(left.package_id, right.package_id) for left, right in pairs}
                request = StructuredModelRequest(
                    system_prompt=_prompt("package_merge.md"),
                    user_prompt=json.dumps(
                        {
                            "batch_index": batch_index,
                            "batch_count": len(batches),
                            "pairs": [
                                {
                                    "source": left.model_dump(mode="json"),
                                    "target": right.model_dump(mode="json"),
                                }
                                for left, right in pairs
                            ],
                        },
                        ensure_ascii=False,
                    ),
                    json_schema=PackageMergeDecisionBatch.model_json_schema(),
                )

                def validate(output: PackageMergeDecisionBatch) -> None:
                    actual = {
                        (item.source_package_id, item.target_package_id)
                        for item in output.decisions
                    }
                    if actual != expected:
                        raise ValueError(
                            "package merge decisions must cover exactly requested pairs"
                        )

                output = models.typed(
                    tier=ModelTier.M3,
                    stage="package_merge",
                    request=request,
                    output_type=PackageMergeDecisionBatch,
                    validator=validate,
                )
                return batch_index, output

            with ThreadPoolExecutor(
                max_workers=min(MODEL_CONCURRENCY, len(batches))
            ) as executor:
                outputs = list(executor.map(process_merge_batch, enumerate(batches)))
            for _, output in sorted(outputs):
                for decision in output.decisions:
                    self._apply_package_merge_decision(
                        decision,
                        active=active,
                        holds=holds,
                        run_id=run_id,
                    )
        for package in active.values():
            if len(package.member_event_ids) > 25:
                hold = HoldRecord(
                    hold_id=stable_id(
                        "hold", {"run": run_id, "package": package.package_id, "overexpanded": True}
                    ),
                    run_id=run_id,
                    kind=HoldKind.PACKAGE_CORRECTION,
                    subject_id=package.package_id,
                    candidate_ids=[],
                    reason_codes=["PACKAGE_OVEREXPANSION"],
                    payload={"member_count": len(package.member_event_ids)},
                )
                self.registry.save_hold(hold)
                holds.append(hold.hold_id)
        return list(active.values()), holds

    def _apply_package_merge_decision(
        self,
        decision: PackagePairMergeDecision,
        *,
        active: dict[str, EventPackage],
        holds: list[str],
        run_id: str,
    ) -> None:
        decision_id = stable_id(
            "package-merge-decision",
            {
                "run": run_id,
                "source": decision.source_package_id,
                "target": decision.target_package_id,
            },
        )
        self.registry.save_package_merge_decision(
            decision_id=decision_id, run_id=run_id, decision=decision
        )
        if decision.relation is PackageMergeRelation.SAME_PACKAGE:
            left = self.registry.get_current_package(decision.source_package_id)
            right = self.registry.get_current_package(decision.target_package_id)
            if left is not None and right is not None:
                target, source = sorted([left, right], key=lambda item: item.package_id)
                merged = self._merge_package_pair(
                    target, source, run_id=run_id, reason="M3_SAME_PACKAGE"
                )
                active.pop(source.package_id, None)
                active[target.package_id] = merged
        elif decision.relation is PackageMergeRelation.UNCERTAIN:
            hold = HoldRecord(
                hold_id=stable_id("hold", {"decision": decision_id}),
                run_id=run_id,
                kind=HoldKind.PACKAGE_MERGE,
                subject_id=decision.source_package_id,
                candidate_ids=[decision.target_package_id],
                reason_codes=["PACKAGE_MERGE_UNCERTAIN"],
                payload=decision.model_dump(mode="json"),
            )
            self.registry.save_hold(hold)
            holds.append(hold.hold_id)

    def _merge_package_pair(
        self, target: EventPackage, source: EventPackage, *, run_id: str, reason: str
    ) -> EventPackage:
        current = self.registry.get_current_package(target.package_id) or target
        merged = merge_packages(current, source)
        if merged.version != current.version:
            self.registry.save_package(merged)
        for membership in self.registry.list_memberships_for_package(source.package_id):
            if membership.event_id not in merged.member_event_ids:
                continue
            self.registry.save_membership(
                membership.model_copy(
                    update={
                        "membership_id": stable_id(
                            "membership",
                            {
                                "event": membership.event_id,
                                "package": merged.package_id,
                                "relation": membership.relation.value,
                            },
                        ),
                        "package_id": merged.package_id,
                        "version": 1,
                    }
                )
            )
        self.registry.save_package_redirect(
            source_package_id=source.package_id,
            target_package_id=target.package_id,
            run_id=run_id,
            reason=reason,
        )
        return merged


def _safe_error(exc: Exception) -> tuple[int, str]:
    if isinstance(exc, ModelAdapterError):
        return exc.latency_ms, exc.code
    return 0, type(exc).__name__


def _provisional_routes(current: EventMention, prior: EventMention) -> set[RecallRoute]:
    routes: set[RecallRoute] = set()
    if current.event_family is prior.event_family:
        routes.add(RecallRoute.EVENT_FAMILY)
    if current.predicate.normalized == prior.predicate.normalized:
        routes.add(RecallRoute.SCHEMA_IDENTITY)
    if set(core_entity_ids_from_mention(current)).intersection(core_entity_ids_from_mention(prior)):
        routes.add(RecallRoute.CORE_ENTITY)
    if current.time.reference_period_id and (
        current.time.reference_period_id == prior.time.reference_period_id
    ):
        routes.add(RecallRoute.TIME_WINDOW)
    return routes


def _select_atomic_action(
    decisions: list[AtomicPairDecision],
) -> tuple[AtomicPairDecision | None, AtomicAction, str]:
    same = [
        item
        for item in decisions
        if item.relation is AtomicSemanticRelation.SAME_EVENT
        and not item.identity_conflicts
    ]
    if len(same) == 1:
        return (
            same[0],
            AtomicAction.MERGE,
            "UNIQUE_SAME_EVENT",
        )
    if len(same) > 1:
        return None, AtomicAction.HOLD, "MULTIPLE_SAME_EVENT_CANDIDATES"
    related = [
        item
        for item in decisions
        if item.relation is AtomicSemanticRelation.RELATED_NOT_SAME
    ]
    if len(related) == 1:
        return (
            related[0],
            AtomicAction.CREATE_AND_LINK,
            "RELATED_NOT_SAME",
        )
    if len(related) > 1:
        return None, AtomicAction.HOLD, "MULTIPLE_RELATED_CANDIDATES"
    if decisions and all(
        item.relation is AtomicSemanticRelation.UNRELATED
        for item in decisions
    ):
        return None, AtomicAction.CREATE_NEW, "ALL_CANDIDATES_UNRELATED"
    return None, AtomicAction.HOLD, "ATOMIC_DECISION_UNCERTAIN"


def _reaction_candidate(
    event: AtomicEvent, candidates: list[PackageCandidate]
) -> PackagePairDecision | None:
    if event.event_family is EventFamily.MARKET_MOVEMENT:
        relation = ExternalRelationType.MARKET_REACTION_TO
    elif event.event_family is EventFamily.ANALYST_ACTION:
        candidates = [
            item for item in candidates if item.package.package_family.value != "ANALYST_REPORT"
        ]
        relation = ExternalRelationType.ANALYST_REACTION_TO
    else:
        return None
    if not candidates:
        return None
    candidate = max(candidates, key=lambda item: item.recall_score)
    return PackagePairDecision(
        event_id=event.event_id,
        candidate_package_id=candidate.package.package_id,
        relation=PackageAssignmentRelation.EXTERNAL_RELATED,
        external_relation=relation,
    )


def _select_package_decision(
    decisions: list[PackagePairDecision],
) -> PackagePairDecision | None:
    members = [
        item
        for item in decisions
        if item.relation is PackageAssignmentRelation.MEMBER
    ]
    if len(members) == 1:
        return members[0]
    external = [
        item
        for item in decisions
        if item.relation is PackageAssignmentRelation.EXTERNAL_RELATED
    ]
    if len(external) == 1:
        return external[0]
    return None


def _package_action(
    selected: PackagePairDecision | None,
    decisions: list[PackagePairDecision],
    had_candidates: bool,
) -> tuple[PackageAction, str]:
    if selected is not None and selected.relation is PackageAssignmentRelation.MEMBER:
        return PackageAction.ADD_TO_PACKAGE, "PACKAGE_MEMBER"
    if selected is not None and selected.relation is PackageAssignmentRelation.EXTERNAL_RELATED:
        return PackageAction.LINK_EXTERNALLY, "PACKAGE_EXTERNAL_RELATED"
    if not had_candidates:
        return PackageAction.CREATE_NEW_PACKAGE, "NO_PACKAGE_CANDIDATE"
    if decisions and all(
        item.relation is PackageAssignmentRelation.NOT_RELATED
        for item in decisions
    ):
        return PackageAction.CREATE_NEW_PACKAGE, "ALL_PACKAGES_NOT_RELATED"
    if not decisions:
        return PackageAction.CREATE_NEW_PACKAGE, "ALL_PACKAGE_CANDIDATES_HARD_BLOCKED"
    return PackageAction.HOLD, "PACKAGE_DECISION_UNCERTAIN"


def _ambiguous_episode_pair(left: EventPackage, right: EventPackage) -> bool:
    if (
        left.package_kind is not PackageKind.EPISODE
        or right.package_kind is not PackageKind.EPISODE
    ):
        return False
    if left.package_family is not right.package_family:
        return False
    if not set(left.anchor_entities).intersection(right.anchor_entities):
        return False
    left_words = set(left.canonical_title.casefold().split())
    right_words = set(right.canonical_title.casefold().split())
    union = left_words | right_words
    return bool(union and len(left_words & right_words) / len(union) >= 0.45)
