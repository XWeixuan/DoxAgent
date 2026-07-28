"""Incremental Atomic Event and Event Package engine for standalone CDECR."""

from __future__ import annotations

import hashlib
import json
import math
import threading
import traceback
import uuid
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal, TypeVar

from pydantic import BaseModel, ValidationError

from cdecr.atomic_identity import (
    IdentityComparison,
    atomic_identity_text,
    canonical_identity_view,
    compare_identity_groups,
    resolved_identity_evidence,
)
from cdecr.atomic_identity_contracts import (
    IdentityAxis,
    IdentityAxisVerdict,
)
from cdecr.atomic_identity_sidecar import (
    append_atomic_ranker_shadow_audit,
    combine_atomic_identity_sidecars,
    compile_atomic_identity_sidecar,
    deterministic_axis_verdicts,
    rank_atomic_candidates,
)
from cdecr.atomic_recall_audit import (
    append_ranked_candidate_snapshot,
    candidate_root_id,
)
from cdecr.canonical_field_resolution import (
    FIELD_RESOLVER_VERSION,
    CanonicalFieldResolutionEngine,
    field_links_hash,
    package_field_links_hash,
)
from cdecr.contracts import (
    AtomicAction,
    AtomicEvent,
    AtomicSemanticRelation,
    EventFamily,
    EventMention,
    EventPackage,
    ExternalRelationType,
    IdentityProfile,
    MembershipDecisionAction,
    MembershipRelation,
    PackageAction,
    PackageAssignmentRelation,
    PackageBoundaryAction,
    PackageExternalRelation,
    PackageExternalRelationCandidate,
    PackageKind,
    PackageMembership,
    PackageMembershipDecision,
    PackageMergeRelation,
    PackageQualityState,
    ParticipantRole,
    SourceMessage,
)
from cdecr.coreference_rules import (
    add_event_to_package,
    add_mention_to_atomic,
    bounded_package_exact_match,
    core_entity_ids_from_profile,
    hard_cannot_link,
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
    AtomicAssignmentDecision,
    AtomicAssignmentRecord,
    AtomicCandidate,
    AtomicDecisionBatch,
    CrossDocumentResult,
    CrossDocumentStatus,
    HardCannotLinkMode,
    HardConflictCode,
    PackageAssignmentDecision,
    PackageAssignmentRecord,
    PackageCandidate,
    PackageCandidateAssessment,
    PackageConflictMode,
    PackageDecisionBatch,
    PackageMergeDecisionBatch,
    PackageMergePlan,
    PackageMergeWireDecisionBatch,
    PackagePairDecision,
    PackagePairDecisionBatch,
    PackagePairMergeDecision,
    PackageSeed,
    RecallRoute,
)
from cdecr.field_coreference import (
    CANDIDATE_RETRIEVER_VERSION,
    FIELD_PROMPT_VERSION,
    FieldCoreferenceResolver,
    field_resolution_configuration_hash,
)
from cdecr.field_coreference_contracts import (
    ATOMIC_FIELD_RECALL_NAMESPACES,
    FieldNamespace,
)
from cdecr.identity_compiler import (
    IDENTITY_COMPILER_VERSION,
    CompiledMentionIdentity,
    IdentityCompiler,
)
from cdecr.kb_v2 import V2KnowledgeBase
from cdecr.models import (
    STRUCTURED_OUTPUT_MODE,
    STRUCTURED_REASONING_EFFORT,
    ModelAdapterError,
    ModelTier,
)
from cdecr.package_engine import (
    PACKAGE_ASSIGNMENT_POLICY_VERSION,
    PACKAGE_BOUNDARY_POLICY_VERSION,
    PACKAGE_PROFILE_COMPILER_VERSION,
    PackageBoundaryGate,
    PackageProfileCompiler,
    atomic_surface_evidence,
    build_package_decision_view,
    canonical_package_sort_key,
    package_is_n13_repairable,
    package_retrieval_text,
    representative_package_members,
    request_local_source_ids,
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
from cdecr.scheduler import take_scheduled_call_metrics
from cdecr.single_document_contracts import ModelCallSummary
from cdecr.wire import compact_json, wire_ref_metadata

ENGINE_VERSION = "cdecr-cross-document-v16"
PROMPT_VERSION = "cdecr-cross-document-prompts-v10"
WIRE_PROTOCOL_VERSION = "cdecr-cross-document-wire-task-local-v6"
ATOMIC_ASSIGNMENT_POLICY_VERSION = "atomic-assignment-policy-v3-axis-assessment"
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
_LOCATION_FIELD_NAMESPACES = set(ATOMIC_FIELD_RECALL_NAMESPACES)
_PACKAGE_FIELD_NAMESPACES = {
    FieldNamespace.PACKAGE_ANCHOR,
    FieldNamespace.ARTIFACT_FILING,
    FieldNamespace.ARTIFACT_EARNINGS_RELEASE,
    FieldNamespace.ARTIFACT_PRESS_RELEASE,
    FieldNamespace.ARTIFACT_REPORT,
    FieldNamespace.ARTIFACT_AGREEMENT,
}

_T = TypeVar("_T", bound=BaseModel)


class CrossDocumentPipelineError(RuntimeError):
    def __init__(self, stage: str, code: str, *, repair_payload: str | None = None) -> None:
        self.stage = stage
        self.code = code
        self.repair_payload = repair_payload
        super().__init__(f"cross-document stage {stage!r} failed: {code}")


def _hash_json(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _structured_request_metadata(request: StructuredModelRequest) -> dict[str, object]:
    try:
        payload: object = json.loads(request.user_prompt)
    except json.JSONDecodeError:
        payload = None
    request_item_count = 1
    candidate_count = 0
    if isinstance(payload, dict):
        for key in ("mentions", "events", "pairs", "tasks", "candidates"):
            value = payload.get(key)
            if isinstance(value, (list, dict)):
                request_item_count = max(request_item_count, len(value))
                if "candidate" in key:
                    candidate_count = max(candidate_count, len(value))
    return {
        "batch_key": _hash_json({"schema": request.json_schema, "user": request.user_prompt})[:24],
        "request_item_count": request_item_count,
        "candidate_count": candidate_count,
        "request_payload_bytes": len(request.user_prompt.encode("utf-8")),
        **wire_ref_metadata(payload),
    }


def _metadata_int(metadata: dict[str, object], key: str) -> int:
    value = metadata.get(key)
    return value if isinstance(value, int) else 0


def _resolved_field_ids(
    registry: CDECRRegistry,
    mentions: Sequence[EventMention],
    *,
    allowed: set[FieldNamespace],
) -> list[tuple[FieldNamespace, str]]:
    resolved: set[tuple[FieldNamespace, str]] = set()
    for mention in mentions:
        for link in registry.list_field_links_for_mention(mention.mention_id):
            root = registry.resolve_field_registry_entry(link.registry_id)
            if root is not None and root.namespace in allowed:
                resolved.add((root.namespace, root.id))
    return sorted(resolved, key=lambda item: (item[0].value, item[1]))


def _mention_local_context(source: SourceMessage, mention: EventMention) -> str:
    passages: list[str] = []
    for span in mention.evidence_spans:
        raw = source.title if span.field == "title" else source.text
        start = max(0, span.start_char - 300)
        end = min(len(raw), span.end_char + 300)
        passages.append(raw[start:end])
    context = "\n".join(dict.fromkeys(passages)).strip()
    if context:
        return context[:4000]
    return mention.source_claim or mention.canonical_proposition


def _compiled_active_hard_conflicts(
    incoming: CompiledMentionIdentity,
    existing: Sequence[CompiledMentionIdentity],
) -> tuple[list[HardConflictCode], dict[str, object]]:
    conflicts: set[HardConflictCode] = set()
    existing_metrics = {
        item.primary_metric_id for item in existing if item.primary_metric_id is not None
    }
    if (
        incoming.primary_metric_id is not None
        and existing_metrics
        and incoming.primary_metric_id not in existing_metrics
    ):
        conflicts.add(HardConflictCode.METRIC)
    existing_companies = {
        company_id for item in existing for company_id in item.principal_company_ids
    }
    incoming_companies = set(incoming.principal_company_ids)
    if (
        incoming_companies
        and existing_companies
        and incoming_companies.isdisjoint(existing_companies)
    ):
        conflicts.add(HardConflictCode.ISSUER)
    return (
        sorted(conflicts, key=str),
        {
            "incoming_primary_metric_id": incoming.primary_metric_id,
            "incoming_primary_metric_field_path": incoming.primary_metric_field_path,
            "incoming_primary_metric_trust_reason": (
                incoming.primary_metric_trust_reason
            ),
            "existing_primary_metric_ids": sorted(existing_metrics),
            "incoming_principal_company_ids": sorted(incoming_companies),
            "incoming_principal_company_field_paths": (
                incoming.principal_company_field_paths
            ),
            "incoming_principal_company_trust_reason": (
                incoming.principal_company_trust_reason
            ),
            "existing_principal_company_ids": sorted(existing_companies),
        },
    )


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return max(-1.0, min(1.0, dot / (left_norm * right_norm)))


def _package_recall_score(
    routes: Sequence[RecallRoute],
    embedding_similarity: float | None,
) -> float:
    """Explainable additive recall score; no signal is an assignment verdict."""

    weights = {
        RecallRoute.CANONICAL_ARTIFACT: 0.34,
        RecallRoute.PACKAGE_ANCHOR: 0.32,
        RecallRoute.SHARED_ATOMIC_EVENT: 0.30,
        RecallRoute.MEMBER_IDENTITY: 0.24,
        RecallRoute.CORE_ENTITY: 0.12,
        RecallRoute.TIME_WINDOW: 0.10,
        RecallRoute.LOCAL_PACKAGE_HINT: 0.08,
        RecallRoute.PACKAGE_KIND_FAMILY: 0.04,
    }
    score = sum(weights.get(route, 0.0) for route in set(routes))
    if embedding_similarity is not None:
        score += max(0.0, embedding_similarity) * 0.16
    return min(1.0, score)


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
        self._call_context = threading.local()

    def current_model_call_id(self) -> str | None:
        value = getattr(self._call_context, "model_call_id", None)
        return value if isinstance(value, str) else None

    def record_wire_shadow(
        self,
        *,
        stage: str,
        operation: Literal[
            "normal_assignment",
            "boundary_reassessment",
            "reaction_member_repair",
            "merge_review",
        ],
        trigger: Literal[
            "candidate_recall",
            "package_boundary",
            "reaction_boundary",
            "package_recall",
        ],
        attempt: Literal["initial", "repair", "escalation"],
        batch_index: int,
        baseline_payload: dict[str, object],
        optimized_payload: dict[str, object],
    ) -> None:
        baseline_json = compact_json(baseline_payload)
        optimized_json = compact_json(optimized_payload)
        baseline_bytes = len(baseline_json.encode("utf-8"))
        optimized_bytes = len(optimized_json.encode("utf-8"))
        invocation_context = {
            "stage": stage,
            "operation": operation,
            "trigger": trigger,
            "batch_index": batch_index,
            "attempt": attempt,
        }
        self.registry.append_decision_audit(
            DecisionAuditRecord(
                audit_id=stable_id(
                    "wire-shadow",
                    {
                        "run": self.run_id,
                        "invocation": invocation_context,
                    },
                ),
                run_id=self.run_id,
                decision_type="WIRE_PAYLOAD_SHADOW",
                subject_id=(
                    f"{stage}:{operation}:{trigger}:{batch_index}:{attempt}"
                ),
                payload={
                    "mode": "shadow_not_sent",
                    "wire_protocol_version": WIRE_PROTOCOL_VERSION,
                    "invocation_context": invocation_context,
                    "baseline_payload_bytes": baseline_bytes,
                    "optimized_payload_bytes": optimized_bytes,
                    "estimated_savings_bytes": max(0, baseline_bytes - optimized_bytes),
                    "estimated_savings_ratio": (
                        0.0
                        if baseline_bytes == 0
                        else round(
                            max(0, baseline_bytes - optimized_bytes) / baseline_bytes,
                            6,
                        )
                    ),
                    "baseline_hash": hashlib.sha256(baseline_json.encode("utf-8")).hexdigest(),
                    "optimized_hash": hashlib.sha256(optimized_json.encode("utf-8")).hexdigest(),
                    **wire_ref_metadata(optimized_payload),
                },
            )
        )

    def embed(self, texts: Sequence[str], *, stage: str) -> EmbeddingResult:
        call_id = str(uuid.uuid4())
        input_hash = _hash_json(list(texts))
        payload_bytes = sum(len(value.encode("utf-8")) for value in texts)
        try:
            result = self.embedding_client.embed(texts)
        except Exception as exc:
            scheduled = take_scheduled_call_metrics(self.embedding_client)
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
                metadata={
                    "attempt": "initial",
                    "request_item_count": len(texts),
                    "request_payload_bytes": payload_bytes,
                    "queue_wait_ms": scheduled.queue_wait_ms if scheduled else 0,
                    "cache_hit": False,
                },
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
                    queue_wait_ms=scheduled.queue_wait_ms if scheduled else 0,
                    request_item_count=len(texts),
                    request_payload_bytes=payload_bytes,
                )
            )
            raise CrossDocumentPipelineError(stage, code) from exc
        scheduled = take_scheduled_call_metrics(self.embedding_client)
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
            metadata={
                "attempt": "initial",
                "request_item_count": len(texts),
                "request_payload_bytes": payload_bytes,
                "queue_wait_ms": scheduled.queue_wait_ms if scheduled else 0,
                "dimensions": result.dimensions,
                "cache_hit": False,
            },
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
                queue_wait_ms=scheduled.queue_wait_ms if scheduled else 0,
                request_item_count=len(texts),
                request_payload_bytes=payload_bytes,
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
        payload_adapter: Callable[[object], object] | None = None,
        attempt_payload_adapter: Callable[[object, str], object] | None = None,
    ) -> _T:
        request_key = _hash_json({"stage": stage, "user": request.user_prompt})[:16]

        def adapt_payload(payload: object, *, attempt: str) -> object:
            if attempt_payload_adapter is not None:
                adapted = attempt_payload_adapter(payload, attempt)
            elif payload_adapter is not None:
                adapted = payload_adapter(payload)
            else:
                return payload
            if _hash_json(adapted) != _hash_json(payload):
                self.registry.append_decision_audit(
                    DecisionAuditRecord(
                        audit_id=(
                            f"cross-model-adapter:{self.run_id}:{stage}:{request_key}:{attempt}"
                        ),
                        run_id=self.run_id,
                        decision_type="CROSS_MODEL_ADAPTER_FALLBACK",
                        subject_id=stage,
                        payload={"attempt": attempt, "strategy": "safe_representation_only"},
                    )
                )
            return adapted

        def audit_validation_failure(exc: ValidationError | ValueError, *, attempt: str) -> None:
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
                constraint = str(exc)
                payload = {
                    "stage": stage,
                    "attempt": attempt,
                    "error_kind": "semantic_validation",
                    "error_code": "requested_pair_coverage"
                    if "cover exactly" in constraint.casefold()
                    else "semantic_constraint",
                    "constraint": constraint[:500],
                }
            self.registry.append_decision_audit(
                DecisionAuditRecord(
                    audit_id=(
                        f"cross-structured-validation:{self.run_id}:{stage}:{request_key}:{attempt}"
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
                repaired = self._structured(tier=tier, stage=stage, request=repair, repaired=True)
                output = output_type.model_validate(
                    adapt_payload(repaired.payload, attempt="repair")
                )
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
            output = output_type.model_validate(adapt_payload(result.payload, attempt="initial"))
            validator(output)
            return output
        except (ValidationError, ValueError) as first_error:
            audit_validation_failure(first_error, attempt="initial")
            if isinstance(first_error, ValidationError):
                validation_error: object = [
                    {
                        "loc": [str(part) for part in error["loc"]],
                        "type": str(error["type"]),
                    }
                    for error in first_error.errors(
                        include_input=False,
                        include_url=False,
                    )[:100]
                ]
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
        self._call_context.model_call_id = call_id
        input_hash = _hash_json({"system": request.system_prompt, "user": request.user_prompt})
        schema_hash = _hash_json(request.json_schema)
        request_metadata = _structured_request_metadata(request)
        try:
            result = client.complete(request)
        except Exception as exc:
            scheduled = take_scheduled_call_metrics(client)
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
                metadata={
                    **request_metadata,
                    "attempt": "repair" if repaired else "initial",
                    "queue_wait_ms": scheduled.queue_wait_ms if scheduled else 0,
                    "cache_hit": False,
                },
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
                    input_tokens=(exc.input_tokens if isinstance(exc, ModelAdapterError) else None),
                    output_tokens=(
                        exc.output_tokens if isinstance(exc, ModelAdapterError) else None
                    ),
                    latency_ms=latency,
                    status="FAILED",
                    error_code=code,
                    repaired=repaired,
                    queue_wait_ms=scheduled.queue_wait_ms if scheduled else 0,
                    request_item_count=_metadata_int(request_metadata, "request_item_count"),
                    candidate_count=_metadata_int(request_metadata, "candidate_count"),
                    request_payload_bytes=_metadata_int(request_metadata, "request_payload_bytes"),
                    wire_ref_count=_metadata_int(request_metadata, "wire_ref_count"),
                )
            )
            raise CrossDocumentPipelineError(
                call_stage,
                code,
                repair_payload=(
                    exc.raw_response_text if isinstance(exc, ModelAdapterError) else None
                ),
            ) from exc
        scheduled = take_scheduled_call_metrics(client)
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
            metadata={
                **request_metadata,
                "attempt": "repair" if repaired else "initial",
                "queue_wait_ms": scheduled.queue_wait_ms if scheduled else 0,
                "output_hash": _hash_json(result.payload),
                "cache_hit": False,
            },
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
                queue_wait_ms=scheduled.queue_wait_ms if scheduled else 0,
                request_item_count=_metadata_int(request_metadata, "request_item_count"),
                candidate_count=_metadata_int(request_metadata, "candidate_count"),
                request_payload_bytes=_metadata_int(request_metadata, "request_payload_bytes"),
                wire_ref_count=_metadata_int(request_metadata, "wire_ref_count"),
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
        model_m1: str = "qwen3.7-text-embedding",
        model_m2: str = "deepseek-v4-flash",
        model_m3: str = "qwen3.7-plus",
        hard_cannot_link_mode: str = HardCannotLinkMode.SHADOW.value,
        package_conflict_mode: str = PackageConflictMode.OFF.value,
        n9_wire_protocol: str = "shadow",
        n12_wire_protocol: str = "shadow",
        n13_wire_protocol: str = "shadow",
        knowledge_base: V2KnowledgeBase | None = None,
    ) -> None:
        self.registry = registry
        self.embedding_client = embedding_client
        self.m2_client = m2_client
        self.m3_client = m3_client
        self.model_m1 = model_m1
        self.model_m2 = model_m2
        self.model_m3 = model_m3
        self.hard_cannot_link_mode = HardCannotLinkMode(hard_cannot_link_mode)
        self.package_conflict_mode = PackageConflictMode(package_conflict_mode)
        allowed_protocols = {"legacy", "shadow", "canary", "on"}
        if n9_wire_protocol not in allowed_protocols:
            raise ValueError("invalid N9 wire protocol")
        if n12_wire_protocol not in allowed_protocols:
            raise ValueError("invalid N12 wire protocol")
        if n13_wire_protocol not in allowed_protocols:
            raise ValueError("invalid N13 wire protocol")
        if n9_wire_protocol in {"canary", "on"}:
            raise ValueError("N9 task-local assessment protocol has not passed its node A/B gate")
        if n12_wire_protocol in {"canary", "on"}:
            raise ValueError("N12 task-local no-ranking protocol has not passed its node A/B gate")
        if n13_wire_protocol in {"canary", "on"}:
            raise ValueError("N13 pair-inline protocol has not passed its full business gate")
        self.n9_wire_protocol = n9_wire_protocol
        self.n12_wire_protocol = n12_wire_protocol
        self.n13_wire_protocol = n13_wire_protocol
        self.knowledge_base = knowledge_base or V2KnowledgeBase()

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
            "hard_cannot_link_mode": self.hard_cannot_link_mode.value,
            "package_conflict_mode": self.package_conflict_mode.value,
            "atomic_assignment_policy_version": ATOMIC_ASSIGNMENT_POLICY_VERSION,
            "package_assignment_policy_version": PACKAGE_ASSIGNMENT_POLICY_VERSION,
            "package_profile_compiler_version": PACKAGE_PROFILE_COMPILER_VERSION,
            "package_boundary_policy_version": PACKAGE_BOUNDARY_POLICY_VERSION,
            "hold_policy": "removed",
            "wire_protocol_version": WIRE_PROTOCOL_VERSION,
            "n13_wire_protocol": self.n13_wire_protocol,
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
                "resolved_field_links_hash": field_links_hash(self.registry, list(mentions)),
                "package_field_links_hash": package_field_links_hash(
                    self.registry,
                    list(mentions),
                    catalog_hash=self.knowledge_base.catalog_hash,
                ),
                "v2_catalog_hash": self.knowledge_base.catalog_hash,
                "field_resolver_version": FIELD_RESOLVER_VERSION,
                "field_prompt_version": FIELD_PROMPT_VERSION,
                "candidate_retriever_version": CANDIDATE_RETRIEVER_VERSION,
                "field_resolution_configuration_hash": field_resolution_configuration_hash(
                    catalog_hash=self.knowledge_base.catalog_hash
                ),
                "identity_compiler_version": IDENTITY_COMPILER_VERSION,
                "hard_cannot_link_mode": self.hard_cannot_link_mode.value,
                "package_conflict_mode": self.package_conflict_mode.value,
                "atomic_assignment_policy_version": ATOMIC_ASSIGNMENT_POLICY_VERSION,
                "package_assignment_policy_version": PACKAGE_ASSIGNMENT_POLICY_VERSION,
                "package_profile_compiler_version": PACKAGE_PROFILE_COMPILER_VERSION,
                "package_boundary_policy_version": PACKAGE_BOUNDARY_POLICY_VERSION,
                "hold_policy": "removed",
                "wire_protocol_version": WIRE_PROTOCOL_VERSION,
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
        run_id = str(uuid.uuid4())
        started_at = datetime.now(UTC)
        self.registry.start_cross_document_trace(
            trace_id=run_id,
            message_id=message_id,
            engine_version=ENGINE_VERSION,
            prompt_version=PROMPT_VERSION,
            model_config=self.model_config,
        )
        # A completed result is keyed from the field-link snapshot that was
        # actually applied.  Check that snapshot before N5.5 recall/Decide:
        # later documents may add global field candidates, but replaying an
        # already-applied message must not spend tokens resolving previously
        # unresolved auxiliary fields.  A real redirect or link change still
        # changes this key and therefore reaches the rebuild guard below.
        pre_resolution_key = self.processing_key(message_id, mentions)
        completed = self.registry.get_completed_cross_document_result(pre_resolution_key)
        if completed is not None:
            self.registry.finish_cross_document_trace(run_id, status="REUSED")
            return completed.model_copy(update={"reused": True, "model_calls": []})

        field_resolver = FieldCoreferenceResolver(
            registry=self.registry,
            embedding_client=self.embedding_client,
            model_client=self.m2_client,
            embedding_model=self.model_m1,
            catalog_hash=self.knowledge_base.catalog_hash,
        )
        canonical_fields = CanonicalFieldResolutionEngine(
            registry=self.registry,
            knowledge_base=self.knowledge_base,
            field_resolver=field_resolver,
        )
        try:
            field_summary = canonical_fields.resolve_document(source, mentions, run_id=run_id)
            canonical_fields.resolve_package_hints(source, mentions, run_id=run_id)
        except Exception as exc:
            processing_key = self.processing_key(message_id, mentions)
            error_code = str(getattr(exc, "code", type(exc).__name__))
            self.registry.append_decision_audit(
                DecisionAuditRecord(
                    audit_id=stable_id(
                        "audit", {"run": run_id, "stage": "canonical_field_resolution"}
                    ),
                    run_id=run_id,
                    decision_type="CROSS_DOCUMENT_FAILURE",
                    subject_id=message_id,
                    payload={
                        "stage": "canonical_field_resolution",
                        "error_code": error_code,
                    },
                )
            )
            self.registry.fail_cross_document_run(run_id, error_code=error_code)
            return CrossDocumentResult(
                run_id=run_id,
                processing_key=processing_key,
                message_id=message_id,
                status=CrossDocumentStatus.FAILED,
                atomic_events=[],
                packages=[],
                atomic_assignments=[],
                package_assignments=[],
                model_calls=[],
                candidate_counts={
                    "atomic_recalled": 0,
                    "atomic_hard_conflict_observed": 0,
                    "package_recalled": 0,
                    "package_hard_blocked": 0,
                    "package_hard_conflict_observed": 0,
                },
                failure_stage="canonical_field_resolution",
                error_code=error_code,
                started_at=started_at,
                finished_at=datetime.now(UTC),
            )
        compiler = IdentityCompiler(registry=self.registry, catalog_hash=field_summary.catalog_hash)
        compiled = {mention.mention_id: compiler.compile(mention) for mention in mentions}
        processing_key = self.processing_key(message_id, mentions)
        completed = self.registry.get_completed_cross_document_result(processing_key)
        if completed is not None:
            self.registry.finish_cross_document_trace(run_id, status="REUSED")
            return completed.model_copy(update={"reused": True, "model_calls": []})

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
                self.registry.finish_cross_document_trace(run_id, status="REUSED")
                return completed.model_copy(update={"reused": True, "model_calls": []})
            raise CrossDocumentPipelineError("registry", "run_not_started")

        summaries: list[ModelCallSummary] = []
        atomic_assignments: list[AtomicAssignmentRecord] = []
        package_assignments: list[PackageAssignmentRecord] = []
        candidate_counts = {
            "atomic_recalled": 0,
            "atomic_hard_conflict_observed": 0,
            "package_recalled": 0,
            "package_hard_blocked": 0,
            "package_hard_conflict_observed": 0,
        }
        try:
            for mention_id, identity in compiled.items():
                self.registry.append_decision_audit(
                    DecisionAuditRecord(
                        audit_id=stable_id(
                            "audit",
                            {
                                "run": run_id,
                                "mention": mention_id,
                                "stage": "identity_compiler",
                            },
                        ),
                        run_id=run_id,
                        decision_type="IDENTITY_COMPILED",
                        subject_id=mention_id,
                        payload=identity.model_dump(mode="json"),
                    )
                )
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
            current_events = self.registry.list_current_atomic_events(limit=10000)
            eligible_mentions = [
                mention
                for mention in mentions
                if compiled[mention.mention_id].identity_profile is not None
            ]
            with ThreadPoolExecutor(max_workers=2) as embedding_executor:
                atomic_future = embedding_executor.submit(
                    self._sync_atomic_embeddings, current_events, models
                )
                mention_future = embedding_executor.submit(
                    self._embed_mentions, eligible_mentions, models
                )
                atomic_vectors = atomic_future.result()
                mention_vectors = mention_future.result()
            candidates = self._atomic_candidates(
                mentions,
                mention_vectors,
                compiled,
                atomic_vectors,
                run_id=run_id,
                candidate_counts=candidate_counts,
            )
            decisions = self._atomic_decisions(
                mentions,
                candidates,
                compiled,
                models,
            )
            atomic_events, atomic_assignments = self._apply_atomic(
                mentions,
                candidates,
                decisions,
                compiled,
                models,
                run_id=run_id,
            )
            atomic_events = self._correct_atomic(atomic_events, mentions, run_id=run_id)
            packages, package_assignments = self._assign_packages_v13(
                atomic_events,
                mentions,
                mention_vectors,
                models,
                run_id=run_id,
                candidate_counts=candidate_counts,
            )
            packages = self._correct_packages_v13(packages, models, run_id=run_id)
            current_atomic_events = self.registry.list_current_atomic_events(
                limit=10000
            )
            largest_cluster = max(
                current_atomic_events,
                key=lambda event: len(event.mention_ids),
                default=None,
            )
            largest_size = (
                len(largest_cluster.mention_ids) if largest_cluster is not None else 0
            )
            hard_violation_count = sum(
                assignment.action is AtomicAction.MERGE
                and bool(assignment.hard_conflicts)
                for assignment in atomic_assignments
            )
            candidate_counts["atomic_max_cluster_size"] = largest_size
            candidate_counts["atomic_hard_conflict_violations"] = (
                hard_violation_count
            )
            self.registry.append_decision_audit(
                DecisionAuditRecord(
                    audit_id=stable_id(
                        "atomic-cluster-monitor",
                        {"run": run_id, "message": message_id},
                    ),
                    run_id=run_id,
                    decision_type="ATOMIC_CLUSTER_MONITOR",
                    subject_id=message_id,
                    payload={
                        "largest_event_id": (
                            largest_cluster.event_id
                            if largest_cluster is not None
                            else None
                        ),
                        "max_cluster_size": largest_size,
                        "top_cluster_predicted_pairs": (
                            largest_size * (largest_size - 1) // 2
                        ),
                        "hard_conflict_violation_count": hard_violation_count,
                        "top_cluster_fp_contribution": (
                            "REQUIRES_GOLD_EVALUATION"
                        ),
                    },
                )
            )
            result = CrossDocumentResult(
                run_id=run_id,
                processing_key=processing_key,
                message_id=message_id,
                status=CrossDocumentStatus.SUCCEEDED,
                atomic_events=atomic_events,
                packages=packages,
                atomic_assignments=atomic_assignments,
                package_assignments=package_assignments,
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
                    payload={
                        "stage": stage,
                        "error_code": code,
                        "frames": safe_frames,
                        **(
                            {"technical_detail": str(exc)[:300]}
                            if isinstance(exc, (AttributeError, NameError))
                            else {}
                        ),
                    },
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
            input_hash = hashlib.sha256(mention.canonical_proposition.encode("utf-8")).hexdigest()
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

    def _sync_atomic_embeddings(
        self,
        events: Sequence[AtomicEvent],
        models: _AuditedModels,
    ) -> dict[str, list[float]]:
        vectors: dict[str, list[float]] = {}
        missing: list[tuple[AtomicEvent, str, str]] = []
        for event in sorted(events, key=lambda item: item.event_id):
            representatives = [
                mention
                for mention_id in event.representative_mention_ids
                if (mention := self.registry.get_mention(mention_id)) is not None
            ]
            identity_text = atomic_identity_text(event, representatives)
            input_hash = hashlib.sha256(identity_text.encode("utf-8")).hexdigest()
            existing = self.registry.get_embedding(
                owner_kind="atomic_event",
                owner_id=event.event_id,
                model=self.model_m1,
                input_hash=input_hash,
            )
            if existing is not None:
                vectors[event.event_id] = existing.vector
            else:
                missing.append((event, identity_text, input_hash))
        for offset in range(0, len(missing), 10):
            batch = missing[offset : offset + 10]
            result = models.embed(
                [identity_text for _, identity_text, _ in batch],
                stage="atomic_identity_embedding",
            )
            for (event, _, input_hash), vector in zip(batch, result.vectors, strict=True):
                self.registry.save_embedding(
                    owner_kind="atomic_event",
                    owner_id=event.event_id,
                    model=result.model,
                    input_hash=input_hash,
                    vector=vector,
                )
                vectors[event.event_id] = vector
        return vectors

    def _atomic_candidates(
        self,
        mentions: list[EventMention],
        mention_vectors: dict[str, list[float]],
        compiled: dict[str, CompiledMentionIdentity],
        atomic_vectors: dict[str, list[float]],
        *,
        run_id: str,
        candidate_counts: dict[str, int],
    ) -> dict[str, list[AtomicCandidate]]:
        active_events = {
            event.event_id: event for event in self.registry.list_current_atomic_events(limit=10000)
        }
        output: dict[str, list[AtomicCandidate]] = {}
        provisional: list[tuple[AtomicEvent, EventMention, CompiledMentionIdentity]] = []
        identity_compiler = IdentityCompiler(
            registry=self.registry,
            catalog_hash=self.knowledge_base.catalog_hash,
        )
        for mention in mentions:
            compiled_identity = compiled[mention.mention_id]
            profile = compiled_identity.identity_profile
            if profile is None:
                output[mention.mention_id] = []
                continue
            if self.registry.get_atomic_event_for_mention(mention.mention_id) is not None:
                output[mention.mention_id] = []
                provisional.append(
                    (
                        singleton_atomic_event(mention, identity_profile=profile, provisional=True),
                        mention,
                        compiled_identity,
                    )
                )
                continue
            start = mention.time.event_start.isoformat() if mention.time.event_start else None
            end = mention.time.event_end.isoformat() if mention.time.event_end else start
            source_fingerprint = self.registry.get_source_fingerprint(mention.message_id)
            route_ids = self.registry.recall_atomic_event_ids(
                entity_ids=core_entity_ids_from_profile(profile),
                event_family=mention.event_family.value,
                normalized_predicate=str(getattr(profile.fields, "normalized_predicate", "")),
                schema_type=profile.schema_type,
                reference_period_id=reference_period_from_profile(profile),
                event_start=start,
                event_end=end,
                source_fingerprint=source_fingerprint,
                field_ids=_resolved_field_ids(
                    self.registry,
                    [mention],
                    allowed=set(ATOMIC_FIELD_RECALL_NAMESPACES),
                ),
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
                for event_id, candidate_vector in atomic_vectors.items():
                    score = _cosine(vector, candidate_vector)
                    scores[event_id] = score
                    if score >= EMBEDDING_RECALL_THRESHOLD:
                        routes.setdefault(event_id, set()).add(RecallRoute.PROPOSITION_EMBEDDING)
            for provisional_event, prior_mention, prior_compiled in provisional:
                assert prior_compiled.identity_profile is not None
                provisional_routes = _provisional_routes(
                    mention,
                    profile,
                    prior_mention,
                    prior_compiled.identity_profile,
                )
                score = _cosine(vector or [], mention_vectors.get(prior_mention.mention_id, []))
                if score >= EMBEDDING_RECALL_THRESHOLD:
                    provisional_routes.add(RecallRoute.PROPOSITION_EMBEDDING)
                if provisional_routes:
                    active_events[provisional_event.event_id] = provisional_event
                    routes[provisional_event.event_id] = provisional_routes
                    scores[provisional_event.event_id] = score
            ranked: list[AtomicCandidate] = []
            observed_by_event: dict[str, list[HardConflictCode]] = {}
            candidate_sidecars = {}
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
                        for provisional_event, prior, _ in provisional
                        if provisional_event.event_id == event_id
                    ]
                representative_sidecars = []
                for representative in representatives:
                    representative_identity = identity_compiler.compile(representative)
                    if representative_identity.atomic_identity_sidecar is not None:
                        representative_sidecars.append(
                            representative_identity.atomic_identity_sidecar
                        )
                if representative_sidecars:
                    candidate_sidecars[event_id] = combine_atomic_identity_sidecars(
                        representative_sidecars
                    )
                else:
                    candidate_sidecars[event_id] = compile_atomic_identity_sidecar(
                        mention,
                        event.identity_profile,
                    )
                observed_conflicts: list[HardConflictCode] = []
                if self.hard_cannot_link_mode is not HardCannotLinkMode.OFF:
                    observed_conflicts = hard_cannot_link(
                        mention,
                        event,
                        incoming_profile=profile,
                        representative_mentions=representatives,
                    )
                    observed_conflicts = [
                        conflict
                        for conflict in observed_conflicts
                        if conflict
                        not in {
                            HardConflictCode.METRIC,
                            HardConflictCode.ISSUER,
                        }
                    ]
                    representative_compiled = [
                        identity_compiler.compile(representative)
                        for representative in representatives
                    ]
                    active_conflicts, discriminant_payload = (
                        _compiled_active_hard_conflicts(
                            compiled_identity,
                            representative_compiled,
                        )
                    )
                    observed_conflicts.extend(active_conflicts)
                    observed_conflicts = [
                        conflict
                        for conflict in observed_conflicts
                        if conflict
                        not in {
                            HardConflictCode.CORE_SUBJECT,
                            HardConflictCode.COUNTERPARTY,
                            HardConflictCode.LOCATION_ASSET,
                        }
                    ]
                    incoming_evidence = resolved_identity_evidence(self.registry, mention)
                    existing_evidence = [
                        evidence
                        for representative in representatives
                        for evidence in resolved_identity_evidence(self.registry, representative)
                    ]
                    for role, conflict in (
                        (ParticipantRole.SUBJECT, HardConflictCode.CORE_SUBJECT),
                        (ParticipantRole.COUNTERPARTY, HardConflictCode.COUNTERPARTY),
                    ):
                        comparison = compare_identity_groups(
                            [item for item in incoming_evidence if item.role is role],
                            [item for item in existing_evidence if item.role is role],
                        )
                        if comparison is IdentityComparison.DIFFERENT:
                            observed_conflicts.append(conflict)
                    location_comparison = compare_identity_groups(
                        [
                            item
                            for item in incoming_evidence
                            if item.namespace in _LOCATION_FIELD_NAMESPACES
                            or item.field_path.startswith("locations[")
                        ],
                        [
                            item
                            for item in existing_evidence
                            if item.namespace in _LOCATION_FIELD_NAMESPACES
                            or item.field_path.startswith("locations[")
                        ],
                    )
                    if location_comparison is IdentityComparison.DIFFERENT:
                        observed_conflicts.append(HardConflictCode.LOCATION_ASSET)
                    observed_conflicts = sorted(set(observed_conflicts), key=str)
                    if observed_conflicts:
                        self.registry.append_decision_audit(
                            DecisionAuditRecord(
                                audit_id=stable_id(
                                    "audit",
                                    {
                                        "run": run_id,
                                        "mention": mention.mention_id,
                                        "event": event_id,
                                        "mode": self.hard_cannot_link_mode.value,
                                    },
                                ),
                                run_id=run_id,
                                decision_type="ATOMIC_HARD_CANNOT_LINK_OBSERVED",
                                subject_id=mention.mention_id,
                                payload={
                                    "candidate_event_id": event_id,
                                    "observed_conflicts": [
                                        value.value for value in observed_conflicts
                                    ],
                                    "mode": self.hard_cannot_link_mode.value,
                                    "enforced": (
                                        self.hard_cannot_link_mode is HardCannotLinkMode.ENFORCE
                                    ),
                                    "active_conflicts": [
                                        value.value
                                        for value in observed_conflicts
                                        if value
                                        in {
                                            HardConflictCode.METRIC,
                                            HardConflictCode.ASSERTION_STATE,
                                            HardConflictCode.ISSUER,
                                        }
                                    ],
                                    "active_discriminants": discriminant_payload,
                                },
                            )
                        )
                observed_by_event[event_id] = observed_conflicts
                effective_conflicts = (
                    [
                        conflict
                        for conflict in observed_conflicts
                        if conflict
                        in {
                            HardConflictCode.METRIC,
                            HardConflictCode.ASSERTION_STATE,
                            HardConflictCode.ISSUER,
                        }
                    ]
                    if self.hard_cannot_link_mode is HardCannotLinkMode.ENFORCE
                    else []
                )
                route_score = min(1.0, 0.35 + 0.12 * len(recall_routes))
                score = max(route_score, (scores.get(event_id, 0.0) + 1.0) / 2.0)
                ranked.append(
                    AtomicCandidate(
                        event=event,
                        recall_routes=sorted(recall_routes, key=str),
                        recall_score=score,
                        hard_conflicts=effective_conflicts,
                        identity_sidecar=candidate_sidecars[event_id],
                        candidate_root_id=candidate_root_id(self.registry, event_id),
                        raw_embedding_similarity=scores.get(event_id),
                    )
                )
            if self.hard_cannot_link_mode is HardCannotLinkMode.ENFORCE:
                ranked.sort(
                    key=lambda item: (
                        bool(item.hard_conflicts),
                        -item.recall_score,
                        item.event.event_id,
                    )
                )
            else:
                ranked.sort(key=lambda item: (-item.recall_score, item.event.event_id))
            incoming_sidecar = compiled_identity.atomic_identity_sidecar
            assert incoming_sidecar is not None
            shadow_ranked = rank_atomic_candidates(
                candidates=ranked,
                incoming_sidecar=incoming_sidecar,
                candidate_sidecars=candidate_sidecars,
                candidate_roots={
                    item.event.event_id: (
                        item.candidate_root_id
                        or candidate_root_id(self.registry, item.event.event_id)
                    )
                    for item in ranked
                },
                raw_embedding_similarities=scores,
            )
            append_atomic_ranker_shadow_audit(
                registry=self.registry,
                run_id=run_id,
                mention_id=mention.mention_id,
                legacy_ranked=ranked,
                shadow_ranked=shadow_ranked,
                top_k=ATOMIC_TOP_K,
            )
            append_ranked_candidate_snapshot(
                registry=self.registry,
                run_id=run_id,
                mention_id=mention.mention_id,
                ranked_candidates=ranked,
                raw_embedding_similarities=scores,
                observed_conflicts={
                    event_id: [value.value for value in values]
                    for event_id, values in observed_by_event.items()
                },
                selected_top_k=ATOMIC_TOP_K,
            )
            output[mention.mention_id] = ranked[:ATOMIC_TOP_K]
            candidate_counts["atomic_recalled"] += len(output[mention.mention_id])
            candidate_counts["atomic_hard_conflict_observed"] += sum(
                bool(observed_by_event.get(item.event.event_id))
                for item in output[mention.mention_id]
            )
            provisional.append(
                (
                    singleton_atomic_event(mention, identity_profile=profile, provisional=True),
                    mention,
                    compiled_identity,
                )
            )
        return output

    def _atomic_decisions(
        self,
        mentions: list[EventMention],
        candidates: dict[str, list[AtomicCandidate]],
        compiled: dict[str, CompiledMentionIdentity],
        models: _AuditedModels,
    ) -> dict[str, AtomicAssignmentDecision]:
        eligible = {
            mention.mention_id: [
                item for item in candidates[mention.mention_id] if not item.hard_conflicts
            ]
            for mention in mentions
        }
        decisions: dict[str, AtomicAssignmentDecision] = {}
        eligible_mentions = [mention for mention in mentions if eligible[mention.mention_id]]
        batches = [
            eligible_mentions[offset : offset + ATOMIC_DECISION_MENTION_BATCH]
            for offset in range(0, len(eligible_mentions), ATOMIC_DECISION_MENTION_BATCH)
        ]

        def adapt_atomic_payload(payload: object) -> object:
            if not isinstance(payload, dict) or not isinstance(payload.get("decisions"), list):
                return payload
            decisions: list[dict[str, object]] = []
            for raw_decision in payload["decisions"]:
                if not isinstance(raw_decision, dict):
                    continue
                decision = {
                    key: raw_decision[key]
                    for key in (
                        "mention_id",
                        "action",
                        "merge_target_event_id",
                        "candidate_assessments",
                        "related_candidate_event_ids",
                        "possible_duplicate_atomic_ids",
                    )
                    if key in raw_decision
                }
                action = decision.get("action")
                if isinstance(action, str):
                    normalized_action = action.strip().upper()
                    if normalized_action in {item.value for item in AtomicAction}:
                        decision["action"] = normalized_action
                decision.setdefault("related_candidate_event_ids", [])
                decision.setdefault("possible_duplicate_atomic_ids", [])
                raw_assessments = decision.get("candidate_assessments")
                if isinstance(raw_assessments, list):
                    assessments: list[dict[str, object]] = []
                    for raw_assessment in raw_assessments:
                        if not isinstance(raw_assessment, dict):
                            continue
                        assessment = {
                            key: raw_assessment[key]
                            for key in (
                                "candidate_event_id",
                                "relation",
                                "axis_assessments",
                                "claim_conflict",
                                "identity_differences",
                            )
                            if key in raw_assessment
                        }
                        if (
                            "identity_differences" not in assessment
                            and "identity_conflicts" in raw_assessment
                        ):
                            assessment["identity_differences"] = raw_assessment[
                                "identity_conflicts"
                            ]
                        relation = assessment.get("relation")
                        if isinstance(relation, str):
                            normalized_relation = relation.strip().upper()
                            if normalized_relation in {
                                item.value for item in AtomicSemanticRelation
                            }:
                                assessment["relation"] = normalized_relation
                        raw_axis_assessments = assessment.get("axis_assessments")
                        if isinstance(raw_axis_assessments, list):
                            normalized_axes: list[dict[str, object]] = []
                            for raw_axis in raw_axis_assessments:
                                if not isinstance(raw_axis, dict):
                                    continue
                                axis = raw_axis.get("axis")
                                verdict = raw_axis.get("verdict")
                                if isinstance(axis, str):
                                    axis = axis.strip().upper()
                                if isinstance(verdict, str):
                                    verdict = verdict.strip().upper()
                                normalized_axes.append(
                                    {
                                        "axis": axis,
                                        "verdict": verdict,
                                    }
                                )
                            assessment["axis_assessments"] = normalized_axes
                        assessment.setdefault("claim_conflict", False)
                        assessment.setdefault("identity_differences", [])
                        assessments.append(assessment)
                    decision["candidate_assessments"] = assessments
                decisions.append(decision)
            return {"decisions": decisions}

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
            mention_short_by_full = {
                mention.mention_id: f"m{index}"
                for index, mention in enumerate(batch_mentions, start=1)
            }
            mention_full_by_short = {
                short_id: full_id for full_id, short_id in mention_short_by_full.items()
            }
            candidate_short_by_full: dict[str, dict[str, str]] = {}
            candidate_full_by_short: dict[str, dict[str, str]] = {}
            for mention in batch_mentions:
                full_id = mention.mention_id
                mention_short_id = mention_short_by_full[full_id]
                mapping = {
                    candidate.event.event_id: f"{mention_short_id}c{index}"
                    for index, candidate in enumerate(
                        batch_candidates[full_id], start=1
                    )
                }
                candidate_short_by_full[full_id] = mapping
                candidate_full_by_short[mention_short_id] = {
                    short_id: candidate_id
                    for candidate_id, short_id in mapping.items()
                }
            expected = {
                mention_short_by_full[mention_id]: set(candidate_short_by_full[mention_id].values())
                for mention_id in batch_candidates
            }
            model_atoms: dict[str, object] = {}
            for mention in batch_mentions:
                for candidate in batch_candidates[mention.mention_id]:
                    event = candidate.event
                    representatives = [
                        item
                        for mention_id in event.representative_mention_ids
                        if (item := self.registry.get_mention(mention_id)) is not None
                    ]
                    raw_claims = event.consensus_claims.get("source_claims", [])
                    claims = raw_claims if isinstance(raw_claims, list) else []
                    representative_claims = [
                        str(item.get("source_claim") or item.get("canonical_proposition"))
                        for item in claims
                        if isinstance(item, dict)
                        and (item.get("source_claim") or item.get("canonical_proposition"))
                    ]
                    short_event_id = candidate_short_by_full[mention.mention_id][event.event_id]
                    candidate_sidecar = candidate.identity_sidecar
                    assert candidate_sidecar is not None
                    model_atoms[short_event_id] = {
                        "event_id": short_event_id,
                        "canonical_proposition": event.canonical_proposition,
                        "event_family": event.event_family.value,
                        "identity_profile": event.identity_profile.model_dump(mode="json"),
                        "identity_adapter": candidate_sidecar.adapter_kind.value,
                        "identity_axes": [
                            axis.value for axis in candidate_sidecar.applicable_axes
                        ],
                        "time": event.time.model_dump(mode="json"),
                        "representative_source_claims": representative_claims[:3],
                        **canonical_identity_view(self.registry, representatives).model_dump(
                            mode="json"
                        ),
                        "is_provisional": event.event_id.startswith("provisional:"),
                    }
            legacy_tasks: list[dict[str, object]] = []
            for mention in batch_mentions:
                mention_short_id = mention_short_by_full[mention.mention_id]
                compiled_identity = compiled[mention.mention_id]
                profile = compiled_identity.identity_profile
                assert profile is not None
                incoming_sidecar = compiled_identity.atomic_identity_sidecar
                assert incoming_sidecar is not None
                source = self.registry.get_source(mention.message_id)
                incoming_view = canonical_identity_view(self.registry, [mention])
                incoming_payload = {
                    "mention_id": mention_short_id,
                    "canonical_proposition": mention.canonical_proposition,
                    "event_family": mention.event_family.value,
                    "assertion_state": mention.assertion_state.value,
                    "identity_profile": profile.model_dump(mode="json"),
                    "identity_adapter": incoming_sidecar.adapter_kind.value,
                    "identity_axes": [
                        axis.value for axis in incoming_sidecar.applicable_axes
                    ],
                    "time": mention.time.model_dump(mode="json"),
                    "claim_values": [item.model_dump(mode="json") for item in mention.quantities],
                    "source_claim": mention.source_claim,
                    "evidence_excerpts": (
                        [_mention_local_context(source, mention)]
                        if source is not None
                        else [mention.canonical_proposition]
                    ),
                    **incoming_view.model_dump(mode="json"),
                }
                legacy_candidates: list[dict[str, object]] = []
                for candidate in batch_candidates[mention.mention_id]:
                    short_event_id = candidate_short_by_full[mention.mention_id][
                        candidate.event.event_id
                    ]
                    atom = model_atoms[short_event_id]
                    assert isinstance(atom, dict)
                    legacy_candidates.append(
                        {
                            **atom,
                        }
                    )
                legacy_tasks.append({"incoming": incoming_payload, "candidates": legacy_candidates})
            expected_axes: dict[str, dict[str, set[IdentityAxis]]] = {}
            deterministic_axis_map: dict[
                str, dict[str, dict[IdentityAxis, IdentityAxisVerdict]]
            ] = {}
            exact_signature_matches: set[tuple[str, str]] = set()
            for mention in batch_mentions:
                mention_short_id = mention_short_by_full[mention.mention_id]
                incoming_sidecar = compiled[
                    mention.mention_id
                ].atomic_identity_sidecar
                assert incoming_sidecar is not None
                expected_axes[mention_short_id] = {}
                deterministic_axis_map[mention_short_id] = {}
                for candidate in batch_candidates[mention.mention_id]:
                    candidate_short_id = candidate_short_by_full[mention.mention_id][
                        candidate.event.event_id
                    ]
                    candidate_sidecar = candidate.identity_sidecar
                    assert candidate_sidecar is not None
                    expected_axes[mention_short_id][candidate_short_id] = set(
                        incoming_sidecar.applicable_axes
                    )
                    deterministic_axis_map[mention_short_id][candidate_short_id] = (
                        deterministic_axis_verdicts(
                            incoming_sidecar,
                            candidate_sidecar,
                        )
                    )
                    if incoming_sidecar.signature_hash == candidate_sidecar.signature_hash:
                        exact_signature_matches.add(
                            (mention_short_id, candidate_short_id)
                        )
            legacy_payload: dict[str, object] = {
                "batch_index": batch_index,
                "batch_count": len(batches),
                "tasks": legacy_tasks,
            }
            wire_payload: dict[str, object] = {
                "tasks": [
                    {
                        "mention": legacy_task["incoming"],
                        "candidates": legacy_task["candidates"],
                    }
                    for legacy_task in legacy_tasks
                ]
            }
            if self.n9_wire_protocol == "shadow":
                models.record_wire_shadow(
                    stage="atomic_coreference",
                    operation="normal_assignment",
                    trigger="candidate_recall",
                    attempt="initial",
                    batch_index=batch_index,
                    baseline_payload=legacy_payload,
                    optimized_payload=wire_payload,
                )
            request = StructuredModelRequest(
                system_prompt=_prompt("atomic_coreference.md"),
                user_prompt=compact_json(legacy_payload),
                json_schema=AtomicDecisionBatch.model_json_schema(),
            )

            def adapt_and_audit_atomic_payload(
                payload: object,
                attempt: str,
            ) -> object:
                adapted = adapt_atomic_payload(payload)
                if not isinstance(adapted, dict) or not isinstance(
                    adapted.get("decisions"), list
                ):
                    return adapted

                raw_decisions = [
                    item for item in adapted["decisions"] if isinstance(item, dict)
                ]
                returned_mentions: list[str] = []
                for item in raw_decisions:
                    mention_id = item.get("mention_id")
                    if isinstance(mention_id, str):
                        returned_mentions.append(mention_id)
                mention_duplicates = sorted(
                    {
                        value
                        for value in returned_mentions
                        if returned_mentions.count(value) > 1
                    }
                )
                mention_extra = sorted(set(returned_mentions) - set(expected))
                mention_missing = sorted(set(expected) - set(returned_mentions))

                normalized_decisions: list[dict[str, object]] = []
                seen_decisions: dict[str, dict[str, object]] = {}
                task_diffs: dict[str, object] = {}
                normalizations: list[dict[str, object]] = []
                for decision in raw_decisions:
                    mention_id = decision.get("mention_id")
                    if not isinstance(mention_id, str) or mention_id not in expected:
                        continue
                    prior = seen_decisions.get(mention_id)
                    if prior is not None:
                        if prior == decision:
                            normalizations.append(
                                {
                                    "mention_id": mention_id,
                                    "kind": "IDENTICAL_DECISION_DEDUPLICATED",
                                }
                            )
                            continue
                        normalized_decisions.append(decision)
                        continue
                    seen_decisions[mention_id] = decision

                    expected_candidates = expected[mention_id]
                    raw_assessments = decision.get("candidate_assessments")
                    assessments = (
                        [
                            item
                            for item in raw_assessments
                            if isinstance(item, dict)
                        ]
                        if isinstance(raw_assessments, list)
                        else []
                    )
                    returned_candidates: list[str] = []
                    for assessment in assessments:
                        candidate_id = assessment.get("candidate_event_id")
                        if isinstance(candidate_id, str):
                            returned_candidates.append(candidate_id)
                    candidate_duplicates = sorted(
                        {
                            value
                            for value in returned_candidates
                            if returned_candidates.count(value) > 1
                        }
                    )
                    candidate_extra = sorted(
                        set(returned_candidates) - expected_candidates
                    )
                    candidate_missing = sorted(
                        expected_candidates - set(returned_candidates)
                    )

                    filtered: list[dict[str, object]] = []
                    seen_assessments: dict[str, dict[str, object]] = {}
                    for assessment in assessments:
                        candidate_id = assessment.get("candidate_event_id")
                        if (
                            not isinstance(candidate_id, str)
                            or candidate_id not in expected_candidates
                        ):
                            continue
                        prior_assessment = seen_assessments.get(candidate_id)
                        if prior_assessment is not None:
                            if prior_assessment == assessment:
                                normalizations.append(
                                    {
                                        "mention_id": mention_id,
                                        "candidate_event_id": candidate_id,
                                        "kind": "IDENTICAL_ASSESSMENT_DEDUPLICATED",
                                    }
                                )
                                continue
                            filtered.append(assessment)
                            continue
                        seen_assessments[candidate_id] = assessment
                        filtered.append(assessment)
                    decision["candidate_assessments"] = filtered

                    target = decision.get("merge_target_event_id")
                    invalid_target = (
                        target
                        if isinstance(target, str)
                        and target not in expected_candidates
                        else None
                    )
                    same_candidates = [
                        item.get("candidate_event_id")
                        for item in filtered
                        if item.get("relation")
                        == AtomicSemanticRelation.SAME_EVENT.value
                        and isinstance(item.get("candidate_event_id"), str)
                    ]
                    if (
                        decision.get("action") == AtomicAction.MERGE.value
                        and (
                            not isinstance(target, str)
                            or target not in expected_candidates
                        )
                        and len(same_candidates) == 1
                    ):
                        decision["merge_target_event_id"] = same_candidates[0]
                        normalizations.append(
                            {
                                "mention_id": mention_id,
                                "kind": "UNIQUE_SAME_EVENT_TARGET_RESTORED",
                                "target_before": target,
                                "target_after": same_candidates[0],
                            }
                        )

                    for field in (
                        "related_candidate_event_ids",
                        "possible_duplicate_atomic_ids",
                    ):
                        values = decision.get(field)
                        if isinstance(values, list):
                            decision[field] = [
                                value
                                for value in values
                                if isinstance(value, str)
                                and value in expected_candidates
                            ]

                    task_diffs[mention_id] = {
                        "expected": sorted(expected_candidates),
                        "returned": returned_candidates,
                        "missing": candidate_missing,
                        "extra": candidate_extra,
                        "duplicates": candidate_duplicates,
                        "invalid_target": invalid_target,
                    }
                    normalized_decisions.append(decision)

                for mention_id in mention_missing:
                    task_diffs[mention_id] = {
                        "expected": sorted(expected[mention_id]),
                        "returned": [],
                        "missing": sorted(expected[mention_id]),
                        "extra": [],
                        "duplicates": [],
                        "invalid_target": None,
                    }
                safe_decisions: list[dict[str, object]] = []
                normalized_by_mention = {
                    item.get("mention_id"): item
                    for item in normalized_decisions
                    if isinstance(item.get("mention_id"), str)
                }

                def conservative_decision(mention_id: str) -> dict[str, object]:
                    return {
                        "mention_id": mention_id,
                        "action": AtomicAction.CREATE_NEW.value,
                        "merge_target_event_id": None,
                        "candidate_assessments": [
                            {
                                "candidate_event_id": candidate_id,
                                "relation": AtomicSemanticRelation.UNRELATED.value,
                                "axis_assessments": [
                                    {
                                        "axis": axis.value,
                                        "verdict": deterministic_axis_map[mention_id][
                                            candidate_id
                                        ][axis].value,
                                    }
                                    for axis in sorted(
                                        expected_axes[mention_id][candidate_id],
                                        key=str,
                                    )
                                ],
                                "claim_conflict": False,
                                "identity_differences": [],
                            }
                            for candidate_id in sorted(expected[mention_id])
                        ],
                        "related_candidate_event_ids": [],
                        "possible_duplicate_atomic_ids": [],
                    }

                for mention_id in sorted(expected):
                    safe_item = normalized_by_mention.get(mention_id)
                    fallback_reason: str | None = None
                    if safe_item is None or mention_id in mention_duplicates:
                        fallback_reason = "N9_MISSING_OR_DUPLICATE_DECISION_CREATE_NEW"
                    else:
                        try:
                            parsed = AtomicAssignmentDecision.model_validate(safe_item)
                            assessment_by_id = {
                                assessment.candidate_event_id: assessment
                                for assessment in parsed.candidate_assessments
                            }
                            if set(assessment_by_id) != expected[mention_id]:
                                raise ValueError("candidate coverage mismatch")
                            for candidate_id, parsed_assessment in assessment_by_id.items():
                                returned_axes = {
                                    item.axis
                                    for item in parsed_assessment.axis_assessments
                                }
                                if returned_axes != expected_axes[mention_id][candidate_id]:
                                    raise ValueError("identity axis coverage mismatch")
                                verdict_by_axis = {
                                    item.axis: item.verdict
                                    for item in parsed_assessment.axis_assessments
                                }
                                if (
                                    mention_id,
                                    candidate_id,
                                ) in exact_signature_matches and any(
                                    verdict
                                    is not IdentityAxisVerdict.MATCH
                                    for verdict in verdict_by_axis.values()
                                ):
                                    raise ValueError(
                                        "exact canonical identity cannot be non-match"
                                    )
                                if any(
                                    deterministic_verdict
                                    is IdentityAxisVerdict.CONFLICT
                                    and verdict_by_axis.get(axis)
                                    is IdentityAxisVerdict.AMBIGUOUS
                                    for axis, deterministic_verdict in
                                    deterministic_axis_map[mention_id][
                                        candidate_id
                                    ].items()
                                ):
                                    raise ValueError(
                                        "deterministic conflict cannot be ambiguous"
                                    )
                            if parsed.action is AtomicAction.MERGE and (
                                parsed.merge_target_event_id not in expected[mention_id]
                                or assessment_by_id[parsed.merge_target_event_id].relation
                                is not AtomicSemanticRelation.SAME_EVENT
                            ):
                                raise ValueError("invalid merge target")
                        except (ValidationError, ValueError, KeyError):
                            fallback_reason = "N9_INVALID_TASK_CREATE_NEW"
                    if fallback_reason is not None:
                        safe_item = conservative_decision(mention_id)
                        task_diff = task_diffs.get(mention_id)
                        if not isinstance(task_diff, dict):
                            task_diff = {}
                            task_diffs[mention_id] = task_diff
                        task_diff["fallback"] = {
                            "action": AtomicAction.CREATE_NEW.value,
                            "reason": fallback_reason,
                        }
                    assert safe_item is not None
                    safe_decisions.append(safe_item)
                normalized_decisions = safe_decisions
                validation_payload = {
                    "model_call_id": models.current_model_call_id(),
                    "attempt": attempt,
                    "batch_index": batch_index,
                    "mentions": {
                        "expected": sorted(expected),
                        "returned": returned_mentions,
                        "missing": mention_missing,
                        "extra": mention_extra,
                        "duplicates": mention_duplicates,
                    },
                    "tasks": task_diffs,
                    "normalizations": normalizations,
                }
                self.registry.append_decision_audit(
                    DecisionAuditRecord(
                        audit_id=(
                            f"atomic-n9-validation:{models.run_id}:"
                            f"{batch_index}:{attempt}:"
                            f"{_hash_json(validation_payload)[:16]}"
                        ),
                        run_id=models.run_id,
                        decision_type="ATOMIC_N9_VALIDATION",
                        subject_id=f"batch:{batch_index}:{attempt}",
                        payload=validation_payload,
                    )
                )
                return {"decisions": normalized_decisions}

            def validate_coverage(output: AtomicDecisionBatch) -> None:
                if {item.mention_id for item in output.decisions} != set(expected):
                    raise ValueError("atomic decisions must cover exactly the requested mentions")
                for decision in output.decisions:
                    candidate_ids = expected[decision.mention_id]
                    assessment_by_id = {
                        item.candidate_event_id: item for item in decision.candidate_assessments
                    }
                    if set(assessment_by_id) != candidate_ids:
                        raise ValueError(
                            "atomic decision assessments must cover exactly "
                            "the requested candidates"
                        )
                    for candidate_id, assessment in assessment_by_id.items():
                        if {
                            item.axis for item in assessment.axis_assessments
                        } != expected_axes[decision.mention_id][candidate_id]:
                            raise ValueError(
                                "atomic identity axes must cover exactly the applicable axes"
                            )

            def validate_model_semantics(output: AtomicDecisionBatch) -> None:
                validate_coverage(output)
                for decision in output.decisions:
                    candidate_ids = expected[decision.mention_id]
                    assessment_by_id = {
                        item.candidate_event_id: item for item in decision.candidate_assessments
                    }
                    if decision.action is AtomicAction.MERGE:
                        target = decision.merge_target_event_id
                        if target not in candidate_ids:
                            raise ValueError("merge target must be an input candidate")
                        if (
                            target is None
                            or assessment_by_id[target].relation
                            is not AtomicSemanticRelation.SAME_EVENT
                        ):
                            raise ValueError("merge target must be assessed SAME_EVENT")

            def normalize_derived_fields(
                output: AtomicDecisionBatch,
            ) -> AtomicDecisionBatch:
                normalized: list[AtomicAssignmentDecision] = []
                for decision in output.decisions:
                    related = sorted(
                        item.candidate_event_id
                        for item in decision.candidate_assessments
                        if item.relation is AtomicSemanticRelation.RELATED_NOT_SAME
                    )
                    same = sorted(
                        item.candidate_event_id
                        for item in decision.candidate_assessments
                        if item.relation is AtomicSemanticRelation.SAME_EVENT
                    )
                    target = decision.merge_target_event_id
                    if (
                        decision.action is AtomicAction.MERGE
                        and target not in same
                        and len(same) == 1
                    ):
                        # The relation assessment is the semantic decision. When the
                        # model copies a long event id incorrectly but identifies
                        # exactly one SAME_EVENT candidate, restore that exact input
                        # id instead of failing an otherwise unambiguous decision.
                        target = same[0]
                    duplicates = (
                        sorted(candidate_id for candidate_id in same if candidate_id != target)
                        if decision.action is AtomicAction.MERGE
                        else []
                    )
                    normalized.append(
                        decision.model_copy(
                            update={
                                "merge_target_event_id": target,
                                "related_candidate_event_ids": related,
                                "possible_duplicate_atomic_ids": duplicates,
                            }
                        )
                    )
                return output.model_copy(update={"decisions": normalized})

            def restore_persistent_ids(
                output: AtomicDecisionBatch,
            ) -> AtomicDecisionBatch:
                restored: list[AtomicAssignmentDecision] = []
                for decision in output.decisions:
                    full_mention_id = mention_full_by_short[decision.mention_id]
                    candidate_map = candidate_full_by_short[decision.mention_id]
                    restored_assessments = [
                        assessment.model_copy(
                            update={
                                "candidate_event_id": candidate_map[assessment.candidate_event_id]
                            }
                        )
                        for assessment in decision.candidate_assessments
                    ]
                    restored.append(
                        decision.model_copy(
                            update={
                                "mention_id": full_mention_id,
                                "merge_target_event_id": (
                                    candidate_map[decision.merge_target_event_id]
                                    if decision.merge_target_event_id is not None
                                    else None
                                ),
                                "candidate_assessments": restored_assessments,
                                "related_candidate_event_ids": [
                                    candidate_map[value]
                                    for value in decision.related_candidate_event_ids
                                ],
                                "possible_duplicate_atomic_ids": [
                                    candidate_map[value]
                                    for value in decision.possible_duplicate_atomic_ids
                                ],
                            }
                        )
                    )
                return output.model_copy(update={"decisions": restored})

            def validate_semantics(output: AtomicDecisionBatch) -> None:
                validate_coverage(output)
                for decision in output.decisions:
                    candidate_ids = expected[decision.mention_id]
                    assessment_by_id = {
                        item.candidate_event_id: item for item in decision.candidate_assessments
                    }
                    related = {
                        item.candidate_event_id
                        for item in decision.candidate_assessments
                        if item.relation is AtomicSemanticRelation.RELATED_NOT_SAME
                    }
                    if set(decision.related_candidate_event_ids) != related:
                        raise ValueError("related candidate ids must match assessments")
                    if decision.action is AtomicAction.MERGE:
                        target = decision.merge_target_event_id
                        if target not in candidate_ids:
                            raise ValueError("merge target must be an input candidate")
                        if (
                            target is None
                            or assessment_by_id[target].relation
                            is not AtomicSemanticRelation.SAME_EVENT
                        ):
                            raise ValueError("merge target must be assessed SAME_EVENT")
                        same = {
                            item.candidate_event_id
                            for item in decision.candidate_assessments
                            if item.relation is AtomicSemanticRelation.SAME_EVENT
                        }
                        if set(decision.possible_duplicate_atomic_ids) != same - {target}:
                            raise ValueError(
                                "possible duplicate ids must contain other SAME_EVENT candidates"
                            )
                    elif decision.possible_duplicate_atomic_ids:
                        raise ValueError(
                            "CREATE_NEW must not include possible duplicate atomic ids"
                        )

            def invoke(
                tier: ModelTier,
                stage: str,
            ) -> AtomicDecisionBatch:
                raw = models.typed(
                    tier=tier,
                    stage=stage,
                    request=request,
                    output_type=AtomicDecisionBatch,
                    validator=validate_model_semantics,
                    attempt_payload_adapter=adapt_and_audit_atomic_payload,
                )
                normalized = normalize_derived_fields(raw)
                try:
                    validate_semantics(normalized)
                except ValueError as exc:
                    raise CrossDocumentPipelineError(
                        stage, "post_validation_invariant"
                    ) from exc
                return normalized

            escalated = False
            try:
                output = invoke(ModelTier.M2, "atomic_coreference")
            except CrossDocumentPipelineError as exc:
                if exc.code != "structured_output_invalid":
                    raise
                self.registry.append_decision_audit(
                    DecisionAuditRecord(
                        audit_id=(f"cross-n9-escalation:{models.run_id}:{batch_index}:m2-invalid"),
                        run_id=models.run_id,
                        decision_type="ATOMIC_N9_ESCALATED",
                        subject_id=f"batch:{batch_index}",
                        payload={
                            "reason": "M2_BUSINESS_VALIDATION_FAILED",
                            "error_code": (
                                exc.code
                            ),
                        },
                    )
                )
                output = invoke(ModelTier.M3, "atomic_coreference_escalation")
                escalated = True
            needs_m3 = any(
                any(
                    assessment.relation is AtomicSemanticRelation.UNCERTAIN
                    for assessment in decision.candidate_assessments
                )
                or sum(
                    assessment.relation is AtomicSemanticRelation.SAME_EVENT
                    for assessment in decision.candidate_assessments
                )
                > 1
                for decision in output.decisions
            )
            if needs_m3 and not escalated:
                output = invoke(ModelTier.M3, "atomic_coreference_escalation")
            if any(
                assessment.relation is AtomicSemanticRelation.UNCERTAIN
                for decision in output.decisions
                for assessment in decision.candidate_assessments
            ):
                output = output.model_copy(
                    update={
                        "decisions": [
                            (
                                decision.model_copy(
                                    update={
                                        "action": AtomicAction.CREATE_NEW,
                                        "merge_target_event_id": None,
                                        "possible_duplicate_atomic_ids": [],
                                    }
                                )
                                if any(
                                    assessment.relation is AtomicSemanticRelation.UNCERTAIN
                                    for assessment in decision.candidate_assessments
                                )
                                else decision
                            )
                            for decision in output.decisions
                        ]
                    }
                )
            return batch_index, restore_persistent_ids(output)

        if not batches:
            return decisions
        with ThreadPoolExecutor(max_workers=min(MODEL_CONCURRENCY, len(batches))) as executor:
            outputs = list(executor.map(process_batch, enumerate(batches)))
        for _, output in sorted(outputs):
            decisions.update({item.mention_id: item for item in output.decisions})
        return decisions

    def _apply_atomic(
        self,
        mentions: list[EventMention],
        candidates: dict[str, list[AtomicCandidate]],
        decisions: dict[str, AtomicAssignmentDecision],
        compiled: dict[str, CompiledMentionIdentity],
        models: _AuditedModels,
        *,
        run_id: str,
    ) -> tuple[list[AtomicEvent], list[AtomicAssignmentRecord]]:
        touched: dict[str, AtomicEvent] = {}
        assignments: list[AtomicAssignmentRecord] = []
        provisional_map: dict[str, str | None] = {}
        known_mentions = {
            mention.mention_id: mention
            for event in self.registry.list_current_atomic_events(limit=10000)
            for mention_id in event.mention_ids
            if (mention := self.registry.get_mention(mention_id)) is not None
        }
        known_mentions.update({mention.mention_id: mention for mention in mentions})
        for mention in mentions:
            compiled_identity = compiled[mention.mention_id]
            profile = compiled_identity.identity_profile
            assert profile is not None
            provisional_id = singleton_atomic_event(
                mention, identity_profile=profile, provisional=True
            ).event_id
            recovered = self.registry.get_atomic_event_for_mention(mention.mention_id)
            if recovered is not None:
                prior_assignment = self.registry.get_latest_atomic_assignment_for_mention(
                    mention.mention_id
                )
                if (
                    prior_assignment is None
                    or prior_assignment.identity_processing_key != compiled_identity.processing_key
                    or prior_assignment.assignment_policy_version
                    != ATOMIC_ASSIGNMENT_POLICY_VERSION
                ):
                    raise CrossDocumentPipelineError(
                        "atomic_assignment",
                        "DERIVED_STATE_REBUILD_REQUIRED",
                    )
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
                    identity_processing_key=compiled_identity.processing_key,
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
            decision = decisions.get(mention.mention_id)
            if not eligible:
                action, reason = AtomicAction.CREATE_NEW, "NO_ELIGIBLE_CANDIDATE"
            elif decision is None:
                action, reason = (
                    AtomicAction.CREATE_NEW,
                    "N9_MISSING_DECISION_CREATE_NEW",
                )
                self.registry.append_decision_audit(
                    DecisionAuditRecord(
                        audit_id=f"n9-missing-decision:{run_id}:{mention.mention_id}",
                        run_id=run_id,
                        decision_type="ATOMIC_ASSIGNMENT_DEGRADED",
                        subject_id=mention.mention_id,
                        payload={
                            "reason": reason,
                            "action": AtomicAction.CREATE_NEW.value,
                        },
                    )
                )
            else:
                action = decision.action
                relations = {item.relation for item in decision.candidate_assessments}
                if action is AtomicAction.MERGE:
                    reason = "N9_MERGE"
                elif AtomicSemanticRelation.UNCERTAIN in relations:
                    reason = "N9_UNCERTAIN_CREATE_NEW"
                elif AtomicSemanticRelation.RELATED_NOT_SAME in relations:
                    reason = "N9_RELATED_CREATE_NEW"
                else:
                    reason = "N9_CREATE_NEW"

            def materialize(candidate_id: str) -> str:
                if not candidate_id.startswith("provisional:"):
                    return candidate_id
                resolved = provisional_map.get(candidate_id)
                if resolved is None:
                    raise CrossDocumentPipelineError(
                        "atomic_assignment", "PROVISIONAL_TARGET_NOT_ASSIGNED"
                    )
                return resolved

            candidate_event_id: str | None = None
            relation: AtomicSemanticRelation | None = None
            claim_conflict = False
            identity_differences: list[str] = []
            related_candidate_event_ids: list[str] = []
            possible_duplicate_atomic_ids: list[str] = []
            if decision is not None:
                assessment_by_id = {
                    item.candidate_event_id: item for item in decision.candidate_assessments
                }
                related_candidate_event_ids = [
                    materialize(value) for value in decision.related_candidate_event_ids
                ]
                possible_duplicate_atomic_ids = [
                    materialize(value) for value in decision.possible_duplicate_atomic_ids
                ]
                if decision.merge_target_event_id is not None:
                    candidate_event_id = materialize(decision.merge_target_event_id)
                    selected = assessment_by_id[decision.merge_target_event_id]
                    relation = selected.relation
                    claim_conflict = selected.claim_conflict
                    identity_differences = selected.identity_differences

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
                    identity_differences=identity_differences,
                    incoming_profile=profile,
                )
                self.registry.save_atomic_event(event)
            else:
                event = singleton_atomic_event(mention, identity_profile=profile)
                self.registry.save_atomic_event(event)
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
                identity_differences=identity_differences,
                related_candidate_event_ids=related_candidate_event_ids,
                possible_duplicate_atomic_ids=possible_duplicate_atomic_ids,
                reason=reason,
                hard_conflicts=blocked_conflicts,
                identity_processing_key=compiled_identity.processing_key,
            )
            self.registry.save_atomic_assignment(record)
            self.registry.append_decision_audit(
                DecisionAuditRecord(
                    audit_id=stable_id("audit", {"assignment": record.assignment_id}),
                    run_id=run_id,
                    decision_type="ATOMIC_ASSIGNMENT",
                    subject_id=mention.mention_id,
                    payload={
                        "assignment": record.model_dump(mode="json"),
                        "incoming_mention_hash": _hash_json(mention.model_dump(mode="json")),
                        "candidate_refs": [
                            {
                                "event_id": candidate.event.event_id,
                                "version": candidate.event.version,
                                "identity_hash": _hash_json(
                                    candidate.event.identity_profile.model_dump(mode="json")
                                ),
                            }
                            for candidate in candidates.get(mention.mention_id, [])
                        ],
                        "resulting_event_version": event.version,
                        "n9_decision": (
                            None if decision is None else decision.model_dump(mode="json")
                        ),
                    },
                )
            )
            assignments.append(record)
        self._sync_atomic_embeddings(list(touched.values()), models)
        return list(touched.values()), assignments

    def _atomic_record(
        self,
        *,
        run_id: str,
        mention: EventMention,
        action: AtomicAction,
        reason: str,
        identity_processing_key: str,
        candidate_event_id: str | None = None,
        resulting_event_id: str | None = None,
        relation: AtomicSemanticRelation | None = None,
        claim_conflict: bool = False,
        identity_differences: list[str] | None = None,
        related_candidate_event_ids: list[str] | None = None,
        possible_duplicate_atomic_ids: list[str] | None = None,
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
            identity_differences=identity_differences or [],
            related_candidate_event_ids=related_candidate_event_ids or [],
            possible_duplicate_atomic_ids=possible_duplicate_atomic_ids or [],
            identity_processing_key=identity_processing_key,
            assignment_policy_version=ATOMIC_ASSIGNMENT_POLICY_VERSION,
            reason=reason,
        )

    def _correct_atomic(
        self, events: list[AtomicEvent], mentions: list[EventMention], *, run_id: str
    ) -> list[AtomicEvent]:
        all_current = self.registry.list_current_atomic_events(limit=10000)
        seen: set[tuple[str, str]] = set()
        for event in events:
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
                pair = (source.event_id, target.event_id)
                if pair in seen:
                    continue
                seen.add(pair)
                self.registry.append_decision_audit(
                    DecisionAuditRecord(
                        audit_id=stable_id(
                            "audit",
                            {
                                "run": run_id,
                                "type": "atomic_duplicate_candidate",
                                "source": source.event_id,
                                "target": target.event_id,
                            },
                        ),
                        run_id=run_id,
                        decision_type="ATOMIC_DUPLICATE_CANDIDATE",
                        subject_id=source.event_id,
                        payload={
                            "source_event_id": source.event_id,
                            "target_event_id": target.event_id,
                            "reason": "EXACT_PROFILE_AND_PROPOSITION_MATCH",
                        },
                    )
                )
        return events

    def _package_member_events(self, package: EventPackage) -> list[AtomicEvent]:
        return [
            event
            for event_id in package.member_event_ids
            if (event := self.registry.get_current_atomic_event(event_id)) is not None
        ]

    def _sync_package_embeddings(
        self,
        packages: Sequence[EventPackage],
        models: _AuditedModels,
    ) -> dict[str, list[float]]:
        stored = {
            item.owner_id: item
            for item in self.registry.list_latest_embeddings(
                owner_kind="event_package", model=self.model_m1, limit=100000
            )
        }
        vectors: dict[str, list[float]] = {}
        missing: list[tuple[EventPackage, str, str]] = []
        for package in packages:
            if not package.member_event_ids:
                continue
            representatives = representative_package_members(self._package_member_events(package))
            text = package_retrieval_text(package, representatives)
            input_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            current = stored.get(package.package_id)
            if current is not None and current.input_hash == input_hash:
                vectors[package.package_id] = current.vector
            else:
                missing.append((package, text, input_hash))
        for offset in range(0, len(missing), 10):
            batch = missing[offset : offset + 10]
            result = models.embed([text for _, text, _ in batch], stage="package_embedding_m1")
            for (package, _, input_hash), vector in zip(batch, result.vectors, strict=True):
                self.registry.save_embedding(
                    owner_kind="event_package",
                    owner_id=package.package_id,
                    model=result.model,
                    input_hash=input_hash,
                    vector=vector,
                )
                vectors[package.package_id] = vector
        return vectors

    def _package_seed_and_key(
        self,
        event: AtomicEvent,
        mentions: list[EventMention],
    ) -> tuple[PackageSeed, str, str, str]:
        seed = package_seed_for_event(event, mentions)
        resolved_fields = _resolved_field_ids(
            self.registry,
            mentions,
            allowed=_PACKAGE_FIELD_NAMESPACES,
        )
        anchor_ids = [canonical_id for _, canonical_id in resolved_fields]
        canonical_artifacts = [
            entry.external_id or entry.id
            for namespace, canonical_id in resolved_fields
            if namespace is not FieldNamespace.PACKAGE_ANCHOR
            and (entry := self.registry.resolve_field_registry_entry(canonical_id)) is not None
        ]
        artifacts = sorted(set(seed.artifact_candidate_ids) | set(canonical_artifacts))
        seed = seed.model_copy(
            update={
                "package_anchor_ids": anchor_ids,
                "artifact_candidate_ids": artifacts,
                "anchor_conflict": len(artifacts) > 1,
                "anchor_artifact_id": artifacts[0] if len(artifacts) == 1 else None,
            }
        )
        seed_hash = _hash_json(seed.model_dump(mode="json"))
        links_hash = package_field_links_hash(
            self.registry,
            mentions,
            catalog_hash=self.knowledge_base.catalog_hash,
        )
        assignment_key = _hash_json(
            {
                "atomic_event_id": event.event_id,
                "atomic_event_version": event.version,
                "package_seed_hash": seed_hash,
                "package_field_links_hash": links_hash,
                "catalog_hash": self.knowledge_base.catalog_hash,
                "resolver_version": FIELD_RESOLVER_VERSION,
                "assignment_policy_version": PACKAGE_ASSIGNMENT_POLICY_VERSION,
                "prompt_version": PROMPT_VERSION,
                "model_config": self.model_config,
            }
        )
        return seed, assignment_key, seed_hash, links_hash

    def _package_candidates_v13(
        self,
        event: AtomicEvent,
        seed: PackageSeed,
        *,
        active_packages: dict[str, EventPackage],
        package_embeddings: dict[str, list[float]],
        atomic_vector: list[float] | None,
        existing_packages: Sequence[EventPackage],
        run_id: str,
        candidate_counts: dict[str, int],
    ) -> list[PackageCandidate]:
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
            package_anchor_ids=seed.package_anchor_ids,
            per_route_limit=20,
        )
        for package in existing_packages:
            if package.quality_state is PackageQualityState.ACTIVE:
                recalled.setdefault(package.package_id, set()).add(
                    RecallRoute.SHARED_ATOMIC_EVENT.value
                )
        incoming_identity = _hash_json(event.identity_profile.model_dump(mode="json"))
        for package in active_packages.values():
            if any(
                _hash_json(member.identity_profile.model_dump(mode="json")) == incoming_identity
                for member in self._package_member_events(package)
            ):
                recalled.setdefault(package.package_id, set()).add(
                    RecallRoute.MEMBER_IDENTITY.value
                )
        similarities: dict[str, float] = {}
        if atomic_vector is not None:
            for package_id, vector in package_embeddings.items():
                similarity = _cosine(atomic_vector, vector)
                similarities[package_id] = similarity
                if similarity >= EMBEDDING_RECALL_THRESHOLD:
                    recalled.setdefault(package_id, set()).add(
                        RecallRoute.PROPOSITION_EMBEDDING.value
                    )
        ranked: list[PackageCandidate] = []
        for package_id, route_names in recalled.items():
            candidate_package = active_packages.get(package_id)
            if (
                candidate_package is None
                or candidate_package.quality_state is not PackageQualityState.ACTIVE
            ):
                continue
            conflicts: list[HardConflictCode] = []
            if self.package_conflict_mode is not PackageConflictMode.OFF:
                conflicts = package_hard_conflicts(seed, candidate_package)
                if conflicts:
                    candidate_counts["package_hard_conflict_observed"] += 1
                    self.registry.append_decision_audit(
                        DecisionAuditRecord(
                            audit_id=stable_id(
                                "audit",
                                {
                                    "run": run_id,
                                    "event": event.event_id,
                                    "package": candidate_package.package_id,
                                    "mode": self.package_conflict_mode.value,
                                },
                            ),
                            run_id=run_id,
                            decision_type="PACKAGE_HARD_CONFLICT_SHADOW",
                            subject_id=event.event_id,
                            payload={
                                "candidate_package_id": candidate_package.package_id,
                                "mode": self.package_conflict_mode.value,
                                "conflicts": [value.value for value in conflicts],
                            },
                        )
                    )
            routes = sorted({RecallRoute(value) for value in route_names}, key=str)
            candidate_similarity = similarities.get(candidate_package.package_id)
            ranked.append(
                PackageCandidate(
                    package=candidate_package,
                    recall_routes=routes,
                    recall_score=_package_recall_score(routes, candidate_similarity),
                    embedding_similarity=candidate_similarity,
                    hard_conflicts=conflicts,
                )
            )
        ranked.sort(
            key=lambda item: (
                bool(item.hard_conflicts)
                if self.package_conflict_mode is PackageConflictMode.ENFORCE
                else False,
                -item.recall_score,
                item.package.package_id,
            )
        )
        result = ranked[:PACKAGE_TOP_K]
        candidate_counts["package_recalled"] += len(result)
        if self.package_conflict_mode is PackageConflictMode.ENFORCE:
            candidate_counts["package_hard_blocked"] += sum(
                bool(item.hard_conflicts) for item in result
            )
        return result

    def _joint_package_decisions(
        self,
        *,
        unresolved: dict[str, list[PackageCandidate]],
        events: dict[str, AtomicEvent],
        seeds: dict[str, PackageSeed],
        models: _AuditedModels,
        wire_operation: Literal[
            "normal_assignment",
            "boundary_reassessment",
            "reaction_member_repair",
            "merge_review",
        ] = "normal_assignment",
        wire_trigger: Literal[
            "candidate_recall",
            "package_boundary",
            "reaction_boundary",
            "package_recall",
        ] = "candidate_recall",
        wire_attempt: Literal["initial", "repair", "escalation"] = "initial",
    ) -> dict[str, PackageAssignmentDecision]:
        if not unresolved:
            return {}
        unresolved_ids = sorted(unresolved)
        tiered_ids: dict[ModelTier, list[str]] = {
            ModelTier.M2: [],
            ModelTier.M3: [],
        }
        for event_id in unresolved_ids:
            complex_decision = (
                len(unresolved[event_id]) > 1
                or seeds[event_id].anchor_conflict
                or (
                    seeds[event_id].package_kind is PackageKind.BOUNDED
                    and not seeds[event_id].package_anchor_ids
                    and not seeds[event_id].anchor_artifact_id
                )
            )
            tiered_ids[ModelTier.M3 if complex_decision else ModelTier.M2].append(event_id)
        batches: list[tuple[ModelTier, list[str]]] = []
        for tier in (ModelTier.M2, ModelTier.M3):
            values = tiered_ids[tier]
            batches.extend(
                (tier, values[offset : offset + PACKAGE_DECISION_EVENT_BATCH])
                for offset in range(0, len(values), PACKAGE_DECISION_EVENT_BATCH)
            )

        def process_batch(
            indexed_batch: tuple[int, tuple[ModelTier, list[str]]],
        ) -> tuple[int, PackageDecisionBatch]:
            batch_index, (tier, event_ids) = indexed_batch
            event_short_by_full = {
                event_id: f"e{index}" for index, event_id in enumerate(event_ids, start=1)
            }
            event_full_by_short = {
                short_id: full_id for full_id, short_id in event_short_by_full.items()
            }
            package_short_by_full: dict[str, dict[str, str]] = {}
            package_full_by_short: dict[str, dict[str, str]] = {}
            for event_id in event_ids:
                short_event_id = event_short_by_full[event_id]
                mapping = {
                    candidate.package.package_id: f"{short_event_id}c{index}"
                    for index, candidate in enumerate(
                        unresolved[event_id], start=1
                    )
                }
                package_short_by_full[event_id] = mapping
                package_full_by_short[short_event_id] = {
                    short_id: package_id
                    for package_id, short_id in mapping.items()
                }
            expected = {
                event_short_by_full[event_id]: set(package_short_by_full[event_id].values())
                for event_id in event_ids
            }
            model_events: dict[str, object] = {}
            model_seeds: dict[str, object] = {}
            model_candidates: dict[str, list[object]] = {}
            candidate_by_task_id = {
                package_short_by_full[event_id][candidate.package.package_id]: candidate
                for event_id in event_ids
                for candidate in unresolved[event_id]
            }
            request_events = {
                event.event_id: event for event_id in event_ids for event in [events[event_id]]
            }
            for candidate in candidate_by_task_id.values():
                for member_id in candidate.package.member_event_ids:
                    member = self.registry.get_current_atomic_event(member_id)
                    if member is not None:
                        request_events[member.event_id] = member
            source_short_ids = request_local_source_ids(
                self.registry, list(request_events.values())
            )
            model_packages: dict[str, object] = {}
            for short_package_id, candidate in candidate_by_task_id.items():
                view = build_package_decision_view(
                    self.registry,
                    candidate,
                    source_short_ids=source_short_ids,
                ).model_dump(mode="json", exclude_none=True)
                package_payload = view["package"]
                assert isinstance(package_payload, dict)
                package_payload["package_id"] = short_package_id
                package_payload["member_event_count"] = len(
                    package_payload.pop("member_event_ids", [])
                )
                view.pop("retrieval_signals", None)
                model_packages[short_package_id] = view
            for event_id in event_ids:
                short_event_id = event_short_by_full[event_id]
                event_payload = events[event_id].model_dump(mode="json")
                event_payload["event_id"] = short_event_id
                event_payload["mention_count"] = len(event_payload.pop("mention_ids", []))
                event_payload["representative_mention_count"] = len(
                    event_payload.pop("representative_mention_ids", [])
                )
                event_payload.pop("consensus_claims", None)
                surface_evidence, source_ids = atomic_surface_evidence(
                    self.registry,
                    events[event_id],
                    source_short_ids=source_short_ids,
                )
                event_payload["surface_evidence"] = surface_evidence.model_dump(
                    mode="json", exclude_none=True
                )
                event_payload["source_ids"] = source_ids
                model_events[short_event_id] = event_payload
                model_seeds[short_event_id] = seeds[event_id].model_dump(mode="json")
                views: list[object] = []
                for candidate in unresolved[event_id]:
                    view = build_package_decision_view(
                        self.registry,
                        candidate,
                        source_short_ids=source_short_ids,
                    ).model_dump(mode="json", exclude_none=True)
                    views.append(
                        {
                            "package_id": package_short_by_full[event_id][
                                candidate.package.package_id
                            ],
                            "retrieval_signals": view["retrieval_signals"],
                        }
                    )
                model_candidates[short_event_id] = views
            legacy_candidates: dict[str, list[object]] = {}
            for short_event_id, candidate_refs in model_candidates.items():
                expanded: list[object] = []
                for candidate_ref in candidate_refs:
                    assert isinstance(candidate_ref, dict)
                    short_package_id = candidate_ref["package_id"]
                    assert isinstance(short_package_id, str)
                    package_view = model_packages[short_package_id]
                    assert isinstance(package_view, dict)
                    expanded.append(
                        {
                            **package_view,
                            "retrieval_signals": candidate_ref["retrieval_signals"],
                        }
                    )
                legacy_candidates[short_event_id] = expanded
            legacy_payload: dict[str, object] = {
                "batch_index": batch_index,
                "batch_count": len(batches),
                "events": model_events,
                "seeds": model_seeds,
                "candidates": legacy_candidates,
            }
            wire_payload: dict[str, object] = {
                "tasks": [
                    {
                        "event": model_events[event_short_by_full[event_id]],
                        "seed": model_seeds[event_short_by_full[event_id]],
                        "candidates": legacy_candidates[event_short_by_full[event_id]],
                    }
                    for event_id in event_ids
                ]
            }
            if self.n12_wire_protocol == "shadow":
                models.record_wire_shadow(
                    stage="package_assignment",
                    operation=wire_operation,
                    trigger=wire_trigger,
                    attempt=wire_attempt,
                    batch_index=batch_index,
                    baseline_payload=legacy_payload,
                    optimized_payload=wire_payload,
                )
            request = StructuredModelRequest(
                system_prompt=_prompt("package_assignment.md"),
                user_prompt=compact_json(legacy_payload),
                json_schema=PackageDecisionBatch.model_json_schema(),
            )

            def adapt(payload: object) -> object:
                if not isinstance(payload, dict) or not isinstance(payload.get("decisions"), list):
                    return payload
                adapted: list[object] = []
                for raw in payload["decisions"]:
                    if not isinstance(raw, dict):
                        adapted.append(raw)
                        continue
                    item = dict(raw)
                    assessments = item.get("candidate_assessments")
                    if isinstance(assessments, list):
                        normalized: list[object] = []
                        for assessment in assessments:
                            if not isinstance(assessment, dict):
                                normalized.append(assessment)
                                continue
                            value = dict(assessment)
                            relation = value.get("relation")
                            if isinstance(relation, str):
                                value["relation"] = relation.strip().upper()
                            normalized.append(value)
                        item["candidate_assessments"] = normalized
                    adapted.append(item)
                return {"decisions": adapted}

            def adapt_and_audit(payload: object, attempt: str) -> object:
                adapted = adapt(payload)
                if not isinstance(adapted, dict) or not isinstance(
                    adapted.get("decisions"), list
                ):
                    return adapted
                normalizations: list[dict[str, object]] = []
                for raw in adapted["decisions"]:
                    if not isinstance(raw, dict):
                        continue
                    assessments = raw.get("candidate_assessments")
                    if not isinstance(assessments, list):
                        continue
                    member_ids: list[str] = []
                    reason_by_id: dict[str, str] = {}
                    for assessment in assessments:
                        if not isinstance(assessment, dict):
                            continue
                        candidate_id = assessment.get("candidate_package_id")
                        relation = assessment.get("relation")
                        reason = assessment.get("reason")
                        if isinstance(candidate_id, str) and isinstance(reason, str):
                            reason_by_id[candidate_id] = reason
                        if relation == PackageAssignmentRelation.MEMBER.value:
                            if isinstance(candidate_id, str) and candidate_id not in member_ids:
                                member_ids.append(candidate_id)
                            if assessment.get("external_relation") is not None:
                                assessment["external_relation"] = None
                                normalizations.append(
                                    {
                                        "event_id": raw.get("event_id"),
                                        "candidate_package_id": candidate_id,
                                        "kind": "MEMBER_EXTERNAL_DETAIL_CLEARED",
                                    }
                                )
                        else:
                            if assessment.get("membership_relation") is not None:
                                assessment["membership_relation"] = None
                                normalizations.append(
                                    {
                                        "event_id": raw.get("event_id"),
                                        "candidate_package_id": candidate_id,
                                        "kind": "NON_MEMBER_MEMBERSHIP_DETAIL_CLEARED",
                                    }
                                )
                            if (
                                relation
                                != PackageAssignmentRelation.EXTERNAL_RELATED.value
                                and assessment.get("external_relation") is not None
                            ):
                                assessment["external_relation"] = None
                                normalizations.append(
                                    {
                                        "event_id": raw.get("event_id"),
                                        "candidate_package_id": candidate_id,
                                        "kind": "NON_EXTERNAL_DETAIL_CLEARED",
                                    }
                                )

                    raw_ranked = raw.get("ranked_member_package_ids")
                    ranked = (
                        [
                            value
                            for value in raw_ranked
                            if isinstance(value, str) and value in member_ids
                        ]
                        if isinstance(raw_ranked, list)
                        else []
                    )
                    ranked = list(dict.fromkeys(ranked))
                    ranked.extend(value for value in member_ids if value not in ranked)
                    if raw_ranked != ranked:
                        normalizations.append(
                            {
                                "event_id": raw.get("event_id"),
                                "kind": "MEMBER_RANKING_REBUILT",
                                "before": raw_ranked,
                                "after": ranked,
                            }
                        )
                        raw["ranked_member_package_ids"] = ranked

                    selected = ranked[0] if ranked else None
                    if raw.get("selected_member_package_id") != selected:
                        normalizations.append(
                            {
                                "event_id": raw.get("event_id"),
                                "kind": "SELECTED_MEMBER_ALIGNED",
                                "before": raw.get("selected_member_package_id"),
                                "after": selected,
                            }
                        )
                        raw["selected_member_package_id"] = selected
                    selection_reason = raw.get("selection_reason")
                    if selected is None:
                        normalized_reason = None
                    elif isinstance(selection_reason, str) and selection_reason.strip():
                        normalized_reason = selection_reason
                    else:
                        normalized_reason = reason_by_id.get(
                            selected, "TOP_RANKED_MEMBER_ASSESSMENT"
                        )
                    if selection_reason != normalized_reason:
                        normalizations.append(
                            {
                                "event_id": raw.get("event_id"),
                                "kind": "SELECTION_REASON_ALIGNED",
                            }
                        )
                        raw["selection_reason"] = normalized_reason

                raw_decisions = [
                    item for item in adapted["decisions"] if isinstance(item, dict)
                ]
                returned_event_ids = [
                    item.get("event_id")
                    for item in raw_decisions
                    if isinstance(item.get("event_id"), str)
                ]
                decision_by_event = {
                    item["event_id"]: item
                    for item in raw_decisions
                    if isinstance(item.get("event_id"), str)
                    and item["event_id"] in expected
                }
                safe_decisions: list[dict[str, object]] = []
                for event_id in sorted(expected):
                    raw = decision_by_event.get(event_id)
                    fallback_reason: str | None = None
                    if raw is None or returned_event_ids.count(event_id) != 1:
                        fallback_reason = "N12_MISSING_OR_DUPLICATE_TASK"
                    else:
                        try:
                            parsed = PackageAssignmentDecision.model_validate(raw)
                            actual = {
                                item.candidate_package_id
                                for item in parsed.candidate_assessments
                            }
                            if actual != expected[event_id]:
                                raise ValueError("candidate coverage mismatch")
                        except (ValidationError, ValueError):
                            fallback_reason = "N12_INVALID_TASK"
                    if fallback_reason is not None:
                        raw = {
                            "event_id": event_id,
                            "candidate_assessments": [
                                {
                                    "candidate_package_id": candidate_id,
                                    "relation": (
                                        PackageAssignmentRelation.NOT_RELATED.value
                                    ),
                                    "reason": (
                                        "N12_INVALID_TASK_CREATE_NEW_PACKAGE"
                                    ),
                                }
                                for candidate_id in sorted(expected[event_id])
                            ],
                            "ranked_member_package_ids": [],
                            "selected_member_package_id": None,
                            "selection_reason": None,
                        }
                        normalizations.append(
                            {
                                "event_id": event_id,
                                "kind": "TASK_DEGRADED_TO_CREATE_NEW_PACKAGE",
                                "reason": fallback_reason,
                            }
                        )
                    safe_decisions.append(raw)
                adapted["decisions"] = safe_decisions

                if normalizations:
                    payload_hash = _hash_json(
                        {
                            "batch_index": batch_index,
                            "attempt": attempt,
                            "normalizations": normalizations,
                        }
                    )
                    self.registry.append_decision_audit(
                        DecisionAuditRecord(
                            audit_id=(
                                f"package-n12-normalization:{models.run_id}:"
                                f"{batch_index}:{attempt}:{payload_hash[:16]}"
                            ),
                            run_id=models.run_id,
                            decision_type="PACKAGE_N12_NORMALIZATION",
                            subject_id=f"batch:{batch_index}:{attempt}",
                            payload={
                                "model_call_id": models.current_model_call_id(),
                                "attempt": attempt,
                                "batch_index": batch_index,
                                "normalizations": normalizations,
                            },
                        )
                    )
                return adapted

            def validate(output: PackageDecisionBatch) -> None:
                if {item.event_id for item in output.decisions} != set(expected):
                    raise ValueError("package decisions must cover exactly requested events")
                for decision in output.decisions:
                    actual = {item.candidate_package_id for item in decision.candidate_assessments}
                    if actual != expected[decision.event_id]:
                        raise ValueError(
                            "package decisions must cover exactly requested candidates"
                        )

            output = models.typed(
                tier=tier,
                stage="package_assignment",
                request=request,
                output_type=PackageDecisionBatch,
                validator=validate,
                attempt_payload_adapter=adapt_and_audit,
            )
            restored: list[PackageAssignmentDecision] = []
            for decision in output.decisions:
                full_event_id = event_full_by_short[decision.event_id]
                restored_package_ids = package_full_by_short[decision.event_id]
                restored.append(
                    decision.model_copy(
                        update={
                            "event_id": full_event_id,
                            "candidate_assessments": [
                                assessment.model_copy(
                                    update={
                                        "candidate_package_id": restored_package_ids[
                                            assessment.candidate_package_id
                                        ]
                                    }
                                )
                                for assessment in decision.candidate_assessments
                            ],
                            "ranked_member_package_ids": [
                                restored_package_ids[value]
                                for value in decision.ranked_member_package_ids
                            ],
                            "selected_member_package_id": (
                                None
                                if decision.selected_member_package_id is None
                                else restored_package_ids[decision.selected_member_package_id]
                            ),
                        }
                    )
                )
            return batch_index, PackageDecisionBatch(decisions=restored)

        with ThreadPoolExecutor(max_workers=min(MODEL_CONCURRENCY, len(batches))) as executor:
            outputs = list(executor.map(process_batch, enumerate(batches)))
        decisions: dict[str, PackageAssignmentDecision] = {}
        for _, output in sorted(outputs):
            decisions.update({item.event_id: item for item in output.decisions})
        return decisions

    def _rebuild_package_after_membership_change(
        self,
        package_id: str,
        compiler: PackageProfileCompiler,
        models: _AuditedModels,
        *,
        sync_embedding: bool = True,
    ) -> EventPackage | None:
        package = self.registry.get_current_package(package_id)
        if package is None:
            return None
        memberships = self.registry.list_memberships_for_package(package.package_id)
        events = [
            event
            for membership in memberships
            if (event := self.registry.get_current_atomic_event(membership.event_id)) is not None
        ]
        if events:
            rebuilt = compiler.compile(package, events)
        else:
            rebuilt = package.model_copy(
                update={
                    "member_event_ids": [],
                    "quality_state": PackageQualityState.QUARANTINED,
                    "version": package.version + 1,
                }
            )
        if rebuilt.version != package.version:
            self.registry.save_package(rebuilt)
        if sync_embedding:
            self._sync_package_embeddings([rebuilt], models)
        return rebuilt

    def _assign_packages_v13(
        self,
        events: list[AtomicEvent],
        article_mentions: list[EventMention],
        mention_vectors: dict[str, list[float]],
        models: _AuditedModels,
        *,
        run_id: str,
        candidate_counts: dict[str, int],
    ) -> tuple[list[EventPackage], list[PackageAssignmentRecord]]:
        del mention_vectors
        compiler = PackageProfileCompiler(self.registry)
        event_map = {event.event_id: event for event in events}
        events = list(event_map.values())
        article_map = {item.mention_id: item for item in article_mentions}
        current_packages = self.registry.list_current_packages(limit=10000)
        active_packages = {
            package.package_id: package
            for package in current_packages
            if package.quality_state is PackageQualityState.ACTIVE
        }
        package_embeddings = self._sync_package_embeddings(list(active_packages.values()), models)
        atomic_embeddings = {
            item.owner_id: item.vector
            for item in self.registry.list_latest_embeddings(
                owner_kind="atomic_event", model=self.model_m1, limit=100000
            )
        }
        seeds: dict[str, PackageSeed] = {}
        keys: dict[str, tuple[str, str, str]] = {}
        candidates: dict[str, list[PackageCandidate]] = {}
        reusable: dict[str, EventPackage] = {}
        for event in events:
            mentions = [
                mention
                for mention_id in event.mention_ids
                if (mention := article_map.get(mention_id) or self.registry.get_mention(mention_id))
                is not None
            ]
            seed, assignment_key, seed_hash, links_hash = self._package_seed_and_key(
                event,
                mentions,
            )
            seeds[event.event_id] = seed
            keys[event.event_id] = (assignment_key, seed_hash, links_hash)
            existing = self.registry.list_packages_for_event(event.event_id)
            latest = self.registry.get_latest_package_assignment_for_event(event.event_id)
            if (
                existing
                and latest is not None
                and latest.package_assignment_key == assignment_key
                and existing[0].quality_state is PackageQualityState.ACTIVE
            ):
                reusable[event.event_id] = existing[0]
                candidates[event.event_id] = []
                continue
            candidates[event.event_id] = self._package_candidates_v13(
                event,
                seed,
                active_packages=active_packages,
                package_embeddings=package_embeddings,
                atomic_vector=atomic_embeddings.get(event.event_id),
                existing_packages=existing,
                run_id=run_id,
                candidate_counts=candidate_counts,
            )
        unresolved: dict[str, list[PackageCandidate]] = {}
        for event_id, values in candidates.items():
            if event_id in reusable:
                continue
            eligible = [
                candidate
                for candidate in values
                if not (
                    self.package_conflict_mode is PackageConflictMode.ENFORCE
                    and candidate.hard_conflicts
                )
            ]
            if eligible:
                unresolved[event_id] = eligible
        decisions = self._joint_package_decisions(
            unresolved=unresolved,
            events=event_map,
            seeds=seeds,
            models=models,
        )
        packages: dict[str, EventPackage] = {}
        dirty_packages: dict[str, EventPackage] = {}
        assignments: list[PackageAssignmentRecord] = []
        for event in events:
            assignment_key, seed_hash, links_hash = keys[event.event_id]
            if event.event_id in reusable:
                package = reusable[event.event_id]
                record = self._package_record(
                    run_id=run_id,
                    event=event,
                    action=PackageAction.ADD_TO_PACKAGE,
                    relation=PackageAssignmentRelation.MEMBER,
                    candidate_package_id=package.package_id,
                    resulting_package_id=package.package_id,
                    ranked_member_package_ids=[package.package_id],
                    selected_member_package_id=package.package_id,
                    selection_reason="ASSIGNMENT_KEY_MATCH",
                    package_assignment_key=assignment_key,
                    package_seed_hash=seed_hash,
                    package_field_links_hash=links_hash,
                    reason="REUSED_CURRENT_MEMBERSHIP_BY_ASSIGNMENT_KEY",
                )
                self.registry.save_package_assignment(record)
                packages[package.package_id] = package
                assignments.append(record)
                continue

            seed = seeds[event.event_id]
            decision = decisions.get(event.event_id)
            assessments = decision.candidate_assessments if decision is not None else []
            selected_id = decision.selected_member_package_id if decision is not None else None
            previous_values = self.registry.list_packages_for_event(event.event_id)
            previous = previous_values[0] if previous_values else None
            for assessment in assessments:
                if assessment.relation is PackageAssignmentRelation.EXTERNAL_RELATED:
                    assert assessment.external_relation is not None
                    self.registry.save_package_external_relation_candidate(
                        PackageExternalRelationCandidate(
                            candidate_id=stable_id(
                                "package-external-candidate",
                                {
                                    "run": run_id,
                                    "event": event.event_id,
                                    "package": assessment.candidate_package_id,
                                    "relation": assessment.external_relation.value,
                                },
                            ),
                            run_id=run_id,
                            source_event_id=event.event_id,
                            target_package_id=assessment.candidate_package_id,
                            relation=assessment.external_relation,
                            reason=assessment.reason,
                            prompt_version=PROMPT_VERSION,
                            model=(
                                self.model_m3
                                if len(candidates[event.event_id]) > 1 or seed.anchor_conflict
                                else self.model_m2
                            ),
                        )
                    )
            if selected_id is not None:
                target = self.registry.get_current_package(selected_id)
                if target is None:
                    raise CrossDocumentPipelineError("package_update", "candidate_missing")
                package = compiler.compile(
                    target, [*self._package_member_events(target), event], seed=seed
                )
                if package.version != target.version:
                    self.registry.save_package(package)
                action = PackageAction.ADD_TO_PACKAGE
                relation = PackageAssignmentRelation.MEMBER
                reason = "N12_SELECTED_CANONICAL_MEMBER"
            else:
                proposed = compiler.compile_singleton(event, seed)
                existing_proposed = self.registry.get_current_package(proposed.package_id)
                if existing_proposed is None:
                    package = proposed
                    self.registry.save_package(package)
                else:
                    package = compiler.compile(existing_proposed, [event], seed=seed)
                    if package.version != existing_proposed.version:
                        self.registry.save_package(package)
                action = PackageAction.CREATE_NEW_PACKAGE
                relation = (
                    PackageAssignmentRelation.UNCERTAIN
                    if any(
                        assessment.relation is PackageAssignmentRelation.UNCERTAIN
                        for assessment in assessments
                    )
                    else PackageAssignmentRelation.NOT_RELATED
                )
                reason = (
                    "NO_PACKAGE_CANDIDATE"
                    if not candidates[event.event_id]
                    else "NO_MEMBER_CREATE_NEW_PACKAGE"
                )

            membership_relation = next(
                (
                    assessment.membership_relation
                    for assessment in assessments
                    if assessment.candidate_package_id == selected_id
                    and assessment.membership_relation is not None
                ),
                seed.membership_relation,
            )
            if previous is None:
                self.registry.save_membership_decision(
                    PackageMembershipDecision(
                        decision_id=stable_id(
                            "membership-decision",
                            {
                                "run": run_id,
                                "event": event.event_id,
                                "target": package.package_id,
                            },
                        ),
                        run_id=run_id,
                        action=MembershipDecisionAction.ADD,
                        event_id=event.event_id,
                        target_package_id=package.package_id,
                        relation=membership_relation,
                        reason=reason,
                    )
                )
            elif previous.package_id != package.package_id:
                self.registry.save_membership_decision(
                    PackageMembershipDecision(
                        decision_id=stable_id(
                            "membership-decision",
                            {
                                "run": run_id,
                                "event": event.event_id,
                                "source": previous.package_id,
                                "target": package.package_id,
                            },
                        ),
                        run_id=run_id,
                        action=MembershipDecisionAction.MOVE,
                        event_id=event.event_id,
                        source_package_id=previous.package_id,
                        target_package_id=package.package_id,
                        relation=membership_relation,
                        reason=reason,
                    )
                )
                rebuilt = self._rebuild_package_after_membership_change(
                    previous.package_id,
                    compiler,
                    models,
                    sync_embedding=False,
                )
                if rebuilt is not None:
                    packages[rebuilt.package_id] = rebuilt
                    dirty_packages[rebuilt.package_id] = rebuilt
            dirty_packages[package.package_id] = package
            packages[package.package_id] = package
            for assessment in assessments:
                if (
                    assessment.relation is PackageAssignmentRelation.MEMBER
                    and assessment.candidate_package_id != selected_id
                ):
                    self.registry.append_decision_audit(
                        DecisionAuditRecord(
                            audit_id=stable_id(
                                "audit",
                                {
                                    "run": run_id,
                                    "event": event.event_id,
                                    "alternate_member": assessment.candidate_package_id,
                                },
                            ),
                            run_id=run_id,
                            decision_type="PACKAGE_ALTERNATE_MEMBER_CANDIDATE",
                            subject_id=event.event_id,
                            payload=assessment.model_dump(mode="json"),
                        )
                    )
            record = self._package_record(
                run_id=run_id,
                event=event,
                action=action,
                relation=relation,
                candidate_package_id=selected_id,
                resulting_package_id=package.package_id,
                candidate_assessments=assessments,
                ranked_member_package_ids=(
                    decision.ranked_member_package_ids if decision is not None else []
                ),
                selected_member_package_id=selected_id,
                selection_reason=(decision.selection_reason if decision is not None else None),
                package_assignment_key=assignment_key,
                package_seed_hash=seed_hash,
                package_field_links_hash=links_hash,
                reason=reason,
            )
            self.registry.save_package_assignment(record)
            self.registry.append_decision_audit(
                DecisionAuditRecord(
                    audit_id=stable_id("audit", {"assignment": record.assignment_id}),
                    run_id=run_id,
                    decision_type="PACKAGE_ASSIGNMENT",
                    subject_id=event.event_id,
                    payload={
                        "assignment": record.model_dump(mode="json"),
                        "incoming_event_ref": {
                            "event_id": event.event_id,
                            "version": event.version,
                            "identity_hash": _hash_json(
                                event.identity_profile.model_dump(mode="json")
                            ),
                        },
                        "candidate_refs": [
                            {
                                "package_id": candidate.package.package_id,
                                "version": candidate.package.version,
                                "profile_hash": _hash_json(
                                    candidate.package.model_dump(mode="json")
                                ),
                            }
                            for candidate in candidates[event.event_id]
                        ],
                        "resulting_package_version": package.version,
                    },
                )
            )
            assignments.append(record)
        if dirty_packages:
            self._sync_package_embeddings(
                [dirty_packages[key] for key in sorted(dirty_packages)], models
            )
        return list(packages.values()), assignments

    def _assign_packages(
        self,
        events: list[AtomicEvent],
        article_mentions: list[EventMention],
        mention_vectors: dict[str, list[float]],
        models: _AuditedModels,
        source: SourceMessage,
        canonical_fields: CanonicalFieldResolutionEngine,
        *,
        run_id: str,
        candidate_counts: dict[str, int],
    ) -> tuple[list[EventPackage], list[PackageAssignmentRecord]]:
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
            event_mentions = [
                mention
                for mention_id in event.mention_ids
                if (mention := article_map.get(mention_id) or self.registry.get_mention(mention_id))
                is not None
            ]
            canonical_fields.resolve_package_hints(source, event_mentions, run_id=run_id)
            existing = self.registry.list_packages_for_event(event.event_id)
            if existing:
                existing_assignments[event.event_id] = existing[0]
                candidates[event.event_id] = []
                continue
            seed = package_seed_for_event(event, event_mentions)
            resolved_package_fields = _resolved_field_ids(
                self.registry,
                event_mentions,
                allowed=_PACKAGE_FIELD_NAMESPACES,
            )
            package_anchor_ids = [canonical_id for _, canonical_id in resolved_package_fields]
            artifact_ids = [
                entry.external_id or entry.id
                for namespace, canonical_id in resolved_package_fields
                if namespace is not FieldNamespace.PACKAGE_ANCHOR
                and (entry := self.registry.resolve_field_registry_entry(canonical_id)) is not None
            ]
            seed = seed.model_copy(
                update={
                    "package_anchor_ids": package_anchor_ids,
                    "anchor_artifact_id": (
                        artifact_ids[0] if len(set(artifact_ids)) == 1 else seed.anchor_artifact_id
                    ),
                }
            )
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
                package_anchor_ids=seed.package_anchor_ids,
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
            ) -> tuple[int, PackagePairDecisionBatch]:
                batch_index, event_ids = indexed_batch
                batch_candidates = {event_id: unresolved[event_id] for event_id in event_ids}
                event_short_by_full = {
                    event_id: f"e{index}" for index, event_id in enumerate(event_ids, start=1)
                }
                event_full_by_short = {
                    short_id: full_id for full_id, short_id in event_short_by_full.items()
                }
                package_short_by_full: dict[str, dict[str, str]] = {}
                package_full_by_short: dict[str, dict[str, str]] = {}
                for event_id, values in batch_candidates.items():
                    mapping = {
                        candidate.package.package_id: f"p{index}"
                        for index, candidate in enumerate(values, start=1)
                    }
                    package_short_by_full[event_id] = mapping
                    package_full_by_short[event_short_by_full[event_id]] = {
                        short_id: full_id for full_id, short_id in mapping.items()
                    }
                expected = {
                    (
                        event_short_by_full[event_id],
                        package_short_by_full[event_id][candidate.package.package_id],
                    )
                    for event_id, values in batch_candidates.items()
                    for candidate in values
                }
                tier = (
                    ModelTier.M3
                    if any(len(values) >= 4 for values in batch_candidates.values())
                    else ModelTier.M2
                )
                model_events: dict[str, object] = {}
                model_seeds: dict[str, object] = {}
                model_candidates: dict[str, list[object]] = {}
                for event_id in event_ids:
                    event_short_id = event_short_by_full[event_id]
                    event_payload = event_map[event_id].model_dump(mode="json")
                    event_payload["event_id"] = event_short_id
                    event_payload["mention_count"] = len(event_payload.pop("mention_ids", []))
                    event_payload["representative_mention_count"] = len(
                        event_payload.pop("representative_mention_ids", [])
                    )
                    model_events[event_short_id] = event_payload
                    model_seeds[event_short_id] = seeds[event_id].model_dump(mode="json")
                    candidate_payloads: list[object] = []
                    for candidate in batch_candidates[event_id]:
                        candidate_payload = candidate.model_dump(mode="json")
                        package_payload = candidate_payload["package"]
                        assert isinstance(package_payload, dict)
                        package_payload["package_id"] = package_short_by_full[event_id][
                            candidate.package.package_id
                        ]
                        package_payload["member_event_count"] = len(
                            package_payload.pop("member_event_ids", [])
                        )
                        candidate_payloads.append(candidate_payload)
                    model_candidates[event_short_id] = candidate_payloads
                request = StructuredModelRequest(
                    system_prompt=_prompt("package_assignment.md"),
                    user_prompt=json.dumps(
                        {
                            "batch_index": batch_index,
                            "batch_count": len(batches),
                            "events": model_events,
                            "seeds": model_seeds,
                            "candidates": model_candidates,
                        },
                        ensure_ascii=False,
                    ),
                    json_schema=PackagePairDecisionBatch.model_json_schema(),
                )

                def adapt_package_payload(payload: object) -> object:
                    if not isinstance(payload, dict) or not isinstance(
                        payload.get("decisions"), list
                    ):
                        return payload
                    adapted: list[dict[str, object]] = []
                    for raw in payload["decisions"]:
                        if not isinstance(raw, dict):
                            continue
                        item = {
                            key: raw[key]
                            for key in (
                                "event_id",
                                "candidate_package_id",
                                "relation",
                                "membership_relation",
                                "external_relation",
                            )
                            if key in raw
                        }
                        relation = item.get("relation")
                        if isinstance(relation, str):
                            relation = relation.strip().upper()
                            item["relation"] = relation
                        event_short_id = item.get("event_id")
                        if (
                            relation == PackageAssignmentRelation.MEMBER.value
                            and "membership_relation" not in item
                            and isinstance(event_short_id, str)
                            and event_short_id in event_full_by_short
                        ):
                            item["membership_relation"] = seeds[
                                event_full_by_short[event_short_id]
                            ].membership_relation.value
                        if relation != PackageAssignmentRelation.MEMBER.value:
                            item.pop("membership_relation", None)
                        if relation != PackageAssignmentRelation.EXTERNAL_RELATED.value:
                            item.pop("external_relation", None)
                        adapted.append(item)
                    return {"decisions": adapted}

                def validate(output: PackagePairDecisionBatch) -> None:
                    actual = {
                        (item.event_id, item.candidate_package_id) for item in output.decisions
                    }
                    if actual != expected:
                        raise ValueError("package decisions must cover exactly requested pairs")

                output = models.typed(
                    tier=tier,
                    stage="package_assignment",
                    request=request,
                    output_type=PackagePairDecisionBatch,
                    validator=validate,
                    payload_adapter=adapt_package_payload,
                )
                restored = PackagePairDecisionBatch(
                    decisions=[
                        decision.model_copy(
                            update={
                                "event_id": event_full_by_short[decision.event_id],
                                "candidate_package_id": package_full_by_short[decision.event_id][
                                    decision.candidate_package_id
                                ],
                            }
                        )
                        for decision in output.decisions
                    ]
                )
                return batch_index, restored

            with ThreadPoolExecutor(max_workers=min(MODEL_CONCURRENCY, len(batches))) as executor:
                outputs = list(executor.map(process_package_batch, enumerate(batches)))
            for _, output in sorted(outputs):
                semantic.update(
                    {(item.event_id, item.candidate_package_id): item for item in output.decisions}
                )

        packages: dict[str, EventPackage] = {
            package.package_id: package for package in existing_assignments.values()
        }
        assignments: list[PackageAssignmentRecord] = []
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
            if reason == "PACKAGE_DECISION_UNCERTAIN":
                self.registry.append_decision_audit(
                    DecisionAuditRecord(
                        audit_id=stable_id(
                            "audit",
                            {
                                "run": run_id,
                                "event": event.event_id,
                                "type": "package_assignment_uncertain",
                            },
                        ),
                        run_id=run_id,
                        decision_type="PACKAGE_ASSIGNMENT_UNCERTAIN",
                        subject_id=event.event_id,
                        payload={
                            "candidate_package_ids": [
                                item.package.package_id for item in candidates[event.event_id]
                            ],
                            "decisions": [item.model_dump(mode="json") for item in pair_values],
                            "fallback_action": PackageAction.CREATE_NEW_PACKAGE.value,
                        },
                    )
                )
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
        return list(packages.values()), assignments

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
        candidate_assessments: list[PackageCandidateAssessment] | None = None,
        ranked_member_package_ids: list[str] | None = None,
        selected_member_package_id: str | None = None,
        selection_reason: str | None = None,
        package_assignment_key: str = "legacy",
        package_seed_hash: str = "legacy",
        package_field_links_hash: str = "legacy",
    ) -> PackageAssignmentRecord:
        return PackageAssignmentRecord(
            assignment_id=stable_id("package-assignment", {"run": run_id, "event": event.event_id}),
            run_id=run_id,
            event_id=event.event_id,
            candidate_package_id=candidate_package_id,
            resulting_package_id=resulting_package_id,
            action=action,
            relation=relation,
            candidate_assessments=candidate_assessments or [],
            ranked_member_package_ids=ranked_member_package_ids or [],
            selected_member_package_id=selected_member_package_id,
            selection_reason=selection_reason,
            package_assignment_key=package_assignment_key,
            package_seed_hash=package_seed_hash,
            package_field_links_hash=package_field_links_hash,
            assignment_policy_version=PACKAGE_ASSIGNMENT_POLICY_VERSION,
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

    def _trusted_package_anchor_ids(self, package: EventPackage) -> set[str]:
        trusted: set[str] = set()
        for anchor_id in package.package_anchor_ids:
            entry = self.registry.resolve_field_registry_entry(anchor_id)
            if entry is not None and entry.external_id:
                trusted.add(entry.external_id)
        if package.anchor_artifact_id:
            trusted.add(package.anchor_artifact_id)
        return trusted

    def _package_source_count(self, package: EventPackage) -> int:
        source_ids: set[str] = set()
        for event in self._package_member_events(package):
            for mention_id in event.mention_ids:
                mention = self.registry.get_mention(mention_id)
                if mention is not None:
                    source_ids.add(mention.message_id)
        return len(source_ids)

    def _package_pair_signals(
        self,
        left: EventPackage,
        right: EventPackage,
        embeddings: dict[str, list[float]],
    ) -> tuple[list[RecallRoute], float | None]:
        routes: set[RecallRoute] = set()
        if set(left.member_event_ids).intersection(right.member_event_ids):
            routes.add(RecallRoute.SHARED_ATOMIC_EVENT)
        if self._trusted_package_anchor_ids(left).intersection(
            self._trusted_package_anchor_ids(right)
        ):
            routes.add(RecallRoute.CANONICAL_ARTIFACT)
        if set(left.anchor_entities).intersection(right.anchor_entities):
            routes.add(RecallRoute.CORE_ENTITY)
        if _package_ranges_near(left, right):
            routes.add(RecallRoute.TIME_WINDOW)
        left_identities = {
            _hash_json(event.identity_profile.model_dump(mode="json"))
            for event in self._package_member_events(left)
        }
        right_identities = {
            _hash_json(event.identity_profile.model_dump(mode="json"))
            for event in self._package_member_events(right)
        }
        if left_identities.intersection(right_identities):
            routes.add(RecallRoute.MEMBER_IDENTITY)
        if (
            left.lifecycle_state
            and right.lifecycle_state
            and left.lifecycle_state == right.lifecycle_state
        ):
            routes.add(RecallRoute.LIFECYCLE_COMPATIBILITY)
        similarity: float | None = None
        left_vector = embeddings.get(left.package_id)
        right_vector = embeddings.get(right.package_id)
        if left_vector is not None and right_vector is not None:
            similarity = _cosine(left_vector, right_vector)
            if similarity >= 0.65:
                routes.add(RecallRoute.PROPOSITION_EMBEDDING)
        return sorted(routes, key=str), similarity

    def _repair_reaction_members_v13(
        self,
        package: EventPackage,
        member_events: list[AtomicEvent],
        active: dict[str, EventPackage],
        package_embeddings: dict[str, list[float]],
        compiler: PackageProfileCompiler,
        models: _AuditedModels,
        *,
        run_id: str,
    ) -> set[str]:
        """Re-run N12-B for reaction members before freezing an overexpanded package."""

        reaction_families = {
            EventFamily.MARKET_MOVEMENT,
            EventFamily.ANALYST_ACTION,
        }
        suspicious = [event for event in member_events if event.event_family in reaction_families]
        if not suspicious or len(suspicious) == len(member_events):
            return set()
        atomic_embeddings = {
            item.owner_id: item.vector
            for item in self.registry.list_latest_embeddings(
                owner_kind="atomic_event", model=self.model_m1, limit=100000
            )
        }
        seeds: dict[str, PackageSeed] = {}
        candidates: dict[str, list[PackageCandidate]] = {}
        event_map = {event.event_id: event for event in suspicious}
        local_counts = {
            "package_recalled": 0,
            "package_hard_blocked": 0,
            "package_hard_conflict_observed": 0,
        }
        for event in suspicious:
            mentions = [
                mention
                for mention_id in event.mention_ids
                if (mention := self.registry.get_mention(mention_id)) is not None
            ]
            seed = package_seed_for_event(event, mentions)
            resolved = _resolved_field_ids(
                self.registry,
                mentions,
                allowed=_PACKAGE_FIELD_NAMESPACES,
            )
            anchor_ids = [
                canonical_id
                for namespace, canonical_id in resolved
                if namespace is FieldNamespace.PACKAGE_ANCHOR
            ]
            artifact_ids = sorted(
                {
                    entry.external_id
                    for namespace, canonical_id in resolved
                    if namespace is FieldNamespace.PACKAGE_ANCHOR
                    and (entry := self.registry.resolve_field_registry_entry(canonical_id))
                    is not None
                    and entry.external_id
                }
            )
            seed = seed.model_copy(
                update={
                    "package_anchor_ids": sorted(set(anchor_ids)),
                    "artifact_candidate_ids": artifact_ids,
                    "anchor_artifact_id": (artifact_ids[0] if len(artifact_ids) == 1 else None),
                    "anchor_conflict": len(artifact_ids) > 1,
                }
            )
            seeds[event.event_id] = seed
            candidates[event.event_id] = self._package_candidates_v13(
                event,
                seed,
                active_packages=active,
                package_embeddings=package_embeddings,
                atomic_vector=atomic_embeddings.get(event.event_id),
                existing_packages=[package],
                run_id=run_id,
                candidate_counts=local_counts,
            )
        decisions = self._joint_package_decisions(
            unresolved=candidates,
            events=event_map,
            seeds=seeds,
            models=models,
            wire_operation="reaction_member_repair",
            wire_trigger="reaction_boundary",
            wire_attempt="repair",
        )
        affected: set[str] = set()
        for event in suspicious:
            decision = decisions[event.event_id]
            selected_id = decision.selected_member_package_id
            if selected_id == package.package_id:
                continue
            seed = seeds[event.event_id]
            if selected_id is None:
                natural_singleton = compiler.compile_singleton(event, seed)
                natural_root = self.registry.resolve_package_root(natural_singleton.package_id)
                if natural_root is None:
                    target = natural_singleton
                else:
                    # A merged package leaves the original singleton ID redirected.
                    # Reusing it would resolve straight back to the mixed source package,
                    # so boundary repair needs a fresh, deterministic split identity.
                    target = natural_singleton.model_copy(
                        update={
                            "package_id": stable_id(
                                "package-boundary-split",
                                {
                                    "event": event.event_id,
                                    "source": package.package_id,
                                    "source_version": package.version,
                                },
                            ),
                            "version": 1,
                        }
                    )
                existing = self.registry.get_current_package(target.package_id)
                if existing is not None:
                    target = compiler.compile(
                        existing,
                        [*self._package_member_events(existing), event],
                        seed=seed,
                    )
                if target.package_id == package.package_id:
                    continue
            else:
                existing = self.registry.get_current_package(selected_id)
                if existing is None:
                    raise CrossDocumentPipelineError("package_boundary_repair", "candidate_missing")
                target = compiler.compile(
                    existing,
                    [*self._package_member_events(existing), event],
                    seed=seed,
                )
            current_target = self.registry.get_current_package(target.package_id)
            if current_target is None or target.version != current_target.version:
                self.registry.save_package(target)
            membership_relation = next(
                (
                    assessment.membership_relation
                    for assessment in decision.candidate_assessments
                    if assessment.candidate_package_id == selected_id
                    and assessment.membership_relation is not None
                ),
                seed.membership_relation,
            )
            self.registry.save_membership_decision(
                PackageMembershipDecision(
                    decision_id=stable_id(
                        "boundary-membership-decision",
                        {
                            "run": run_id,
                            "event": event.event_id,
                            "source": package.package_id,
                            "target": target.package_id,
                        },
                    ),
                    run_id=run_id,
                    action=MembershipDecisionAction.MOVE,
                    event_id=event.event_id,
                    source_package_id=package.package_id,
                    target_package_id=target.package_id,
                    relation=membership_relation,
                    reason="N13_BOUNDARY_REASSESSMENT",
                )
            )
            rebuilt_source = self._rebuild_package_after_membership_change(
                package.package_id, compiler, models
            )
            if rebuilt_source is not None:
                active[package.package_id] = rebuilt_source
            self._sync_package_embeddings([target], models)
            active[target.package_id] = target
            affected.update({package.package_id, target.package_id})
            self.registry.append_decision_audit(
                DecisionAuditRecord(
                    audit_id=stable_id(
                        "audit",
                        {
                            "run": run_id,
                            "event": event.event_id,
                            "type": "package_boundary_reassessment",
                        },
                    ),
                    run_id=run_id,
                    decision_type="PACKAGE_BOUNDARY_REASSESSMENT",
                    subject_id=event.event_id,
                    payload=decision.model_dump(mode="json"),
                )
            )
        return affected

    def _correct_packages_v13(
        self,
        packages: list[EventPackage],
        models: _AuditedModels,
        *,
        run_id: str,
    ) -> list[EventPackage]:
        compiler = PackageProfileCompiler(self.registry)
        all_current = {
            package.package_id: package
            for package in self.registry.list_current_packages(limit=10000)
        }
        active = {
            package_id: package
            for package_id, package in all_current.items()
            if package_is_n13_repairable(package)
        }
        result_ids = {
            package.package_id
            for package in packages
            if self.registry.resolve_package_root(package.package_id) is not None
        }
        embeddings = self._sync_package_embeddings(list(active.values()), models)
        seen_pairs: set[tuple[str, str]] = set()
        m0_decisions: list[PackagePairMergeDecision] = []
        m3_pairs: list[tuple[EventPackage, EventPackage, list[RecallRoute], float | None]] = []
        for touched_id in sorted(result_ids):
            left = active.get(touched_id)
            if left is None:
                continue
            for right in active.values():
                if left.package_id == right.package_id:
                    continue
                pair_ids = (
                    min(left.package_id, right.package_id),
                    max(left.package_id, right.package_id),
                )
                if pair_ids in seen_pairs:
                    continue
                seen_pairs.add(pair_ids)
                source_package = active[pair_ids[0]]
                target_package = active[pair_ids[1]]
                routes, similarity = self._package_pair_signals(
                    source_package, target_package, embeddings
                )
                if not routes:
                    continue
                shared_atomic = RecallRoute.SHARED_ATOMIC_EVENT in routes
                trusted_artifact = RecallRoute.CANONICAL_ARTIFACT in routes
                obvious_container = bool(
                    source_package.package_kind is target_package.package_kind
                    and source_package.package_family is target_package.package_family
                    and (
                        set(source_package.anchor_entities).intersection(
                            target_package.anchor_entities
                        )
                        or RecallRoute.MEMBER_IDENTITY in routes
                    )
                )
                if shared_atomic or (trusted_artifact and obvious_container):
                    m0_decisions.append(
                        PackagePairMergeDecision(
                            source_package_id=source_package.package_id,
                            target_package_id=target_package.package_id,
                            relation=PackageMergeRelation.SAME_PACKAGE,
                            reason=(
                                "SHARED_CURRENT_ATOMIC_ROOT"
                                if shared_atomic
                                else "SAME_TRUSTED_CANONICAL_ARTIFACT"
                            ),
                        )
                    )
                else:
                    m3_pairs.append((source_package, target_package, routes, similarity))

        m3_decisions: list[PackagePairMergeDecision] = []
        if m3_pairs:
            batches = [
                m3_pairs[offset : offset + PACKAGE_MERGE_PAIR_BATCH]
                for offset in range(0, len(m3_pairs), PACKAGE_MERGE_PAIR_BATCH)
            ]

            def process_batch(
                indexed_batch: tuple[
                    int,
                    list[
                        tuple[
                            EventPackage,
                            EventPackage,
                            list[RecallRoute],
                            float | None,
                        ]
                    ],
                ],
            ) -> tuple[int, PackageMergeDecisionBatch]:
                batch_index, pairs = indexed_batch
                package_ids = sorted(
                    {package.package_id for left, right, _, _ in pairs for package in (left, right)}
                )
                short_by_full = {
                    package_id: f"p{index}" for index, package_id in enumerate(package_ids, start=1)
                }
                full_by_short = {short_id: full_id for full_id, short_id in short_by_full.items()}
                pair_ids = {
                    (
                        short_by_full[left.package_id],
                        short_by_full[right.package_id],
                    ): f"r{index}"
                    for index, (left, right, _, _) in enumerate(pairs, start=1)
                }
                pair_by_id = {pair_id: pair for pair, pair_id in pair_ids.items()}
                package_by_id = {
                    package.package_id: package
                    for left, right, _, _ in pairs
                    for package in (left, right)
                }
                first_signals_by_package: dict[str, tuple[list[RecallRoute], float | None]] = {}
                for left, right, routes, similarity in pairs:
                    first_signals_by_package.setdefault(left.package_id, (routes, similarity))
                    first_signals_by_package.setdefault(right.package_id, (routes, similarity))
                model_packages: dict[str, object] = {}
                request_events = {
                    event.event_id: event
                    for package_id in package_ids
                    for event in self._package_member_events(package_by_id[package_id])
                }
                source_short_ids = request_local_source_ids(
                    self.registry, list(request_events.values())
                )
                for package_id in package_ids:
                    routes, similarity = first_signals_by_package[package_id]
                    package = package_by_id[package_id]
                    view = build_package_decision_view(
                        self.registry,
                        PackageCandidate(
                            package=package,
                            recall_routes=routes,
                            recall_score=_package_recall_score(routes, similarity),
                            embedding_similarity=similarity,
                        ),
                        source_short_ids=source_short_ids,
                    ).model_dump(mode="json", exclude_none=True)
                    payload = view["package"]
                    assert isinstance(payload, dict)
                    short_package_id = short_by_full[package_id]
                    payload["package_id"] = short_package_id
                    payload["member_event_count"] = len(payload.pop("member_event_ids", []))
                    view.pop("retrieval_signals", None)
                    model_packages[short_package_id] = view
                model_pairs: list[dict[str, object]] = []
                for left, right, routes, similarity in pairs:
                    model_pairs.append(
                        {
                            "pair_id": pair_ids[
                                (
                                    short_by_full[left.package_id],
                                    short_by_full[right.package_id],
                                )
                            ],
                            "source_package_id": short_by_full[left.package_id],
                            "target_package_id": short_by_full[right.package_id],
                            "retrieval_signals": {
                                "routes": [route.value for route in routes],
                                "embedding_similarity": (
                                    None if similarity is None else round(similarity, 3)
                                ),
                            },
                        }
                    )
                legacy_pairs: list[dict[str, object]] = []
                for pair in model_pairs:
                    source_id = pair["source_package_id"]
                    target_id = pair["target_package_id"]
                    assert isinstance(source_id, str)
                    assert isinstance(target_id, str)
                    source_view = model_packages[source_id]
                    target_view = model_packages[target_id]
                    assert isinstance(source_view, dict)
                    assert isinstance(target_view, dict)
                    signals = pair["retrieval_signals"]
                    legacy_pairs.append(
                        {
                            "pair_id": pair["pair_id"],
                            "source": {
                                **source_view,
                                "retrieval_signals": signals,
                            },
                            "target": {
                                **target_view,
                                "retrieval_signals": signals,
                            },
                        }
                    )
                legacy_payload: dict[str, object] = {
                    "batch_index": batch_index,
                    "batch_count": len(batches),
                    "pairs": legacy_pairs,
                }
                wire_pairs: list[dict[str, object]] = []
                for pair in model_pairs:
                    source_id = pair["source_package_id"]
                    target_id = pair["target_package_id"]
                    assert isinstance(source_id, str)
                    assert isinstance(target_id, str)
                    source_view = model_packages[source_id]
                    target_view = model_packages[target_id]
                    assert isinstance(source_view, dict)
                    assert isinstance(target_view, dict)
                    wire_pairs.append(
                        {
                            "source": source_view,
                            "target": target_view,
                            "retrieval_signals": pair["retrieval_signals"],
                        }
                    )
                wire_payload: dict[str, object] = {"pairs": wire_pairs}
                if self.n13_wire_protocol == "shadow":
                    models.record_wire_shadow(
                        stage="package_merge",
                        operation="merge_review",
                        trigger="package_recall",
                        attempt="initial",
                        batch_index=batch_index,
                        baseline_payload=legacy_payload,
                        optimized_payload=wire_payload,
                    )
                request = StructuredModelRequest(
                    system_prompt=_prompt("package_merge.md"),
                    user_prompt=compact_json(legacy_payload),
                    json_schema=PackageMergeWireDecisionBatch.model_json_schema(),
                )

                def adapt(payload: object) -> object:
                    if not isinstance(payload, dict) or not isinstance(
                        payload.get("decisions"), list
                    ):
                        return payload
                    adapted_by_pair: dict[str, dict[str, object]] = {}
                    duplicate_pair_ids: set[str] = set()
                    for raw in payload["decisions"]:
                        if not isinstance(raw, dict):
                            continue
                        item = dict(raw)
                        pair_id = item.get("pair_id")
                        if not isinstance(pair_id, str):
                            source_id = item.get("source_package_id")
                            target_id = item.get("target_package_id")
                            if isinstance(source_id, str) and isinstance(
                                target_id, str
                            ):
                                pair_id = pair_ids.get((source_id, target_id))
                        if not isinstance(pair_id, str) or pair_id not in pair_by_id:
                            continue
                        relation = item.get("relation")
                        if isinstance(relation, str):
                            item["relation"] = relation.strip().upper()
                        item = {
                            "pair_id": pair_id,
                            "relation": item.get("relation"),
                            "reason": item.get("reason"),
                        }
                        if pair_id in adapted_by_pair:
                            duplicate_pair_ids.add(pair_id)
                        adapted_by_pair[pair_id] = item
                    adapted: list[dict[str, object]] = []
                    fallbacks: list[dict[str, str]] = []
                    for pair_id in sorted(pair_by_id):
                        pair_item = adapted_by_pair.get(pair_id)
                        valid = pair_item is not None and pair_id not in duplicate_pair_ids
                        if valid:
                            try:
                                PackageMergeWireDecisionBatch.model_validate(
                                    {"decisions": [pair_item]}
                                )
                            except ValidationError:
                                valid = False
                        if not valid:
                            pair_item = {
                                "pair_id": pair_id,
                                "relation": PackageMergeRelation.DIFFERENT_PACKAGE.value,
                                "reason": "N13_INVALID_PAIR_NO_MERGE",
                            }
                            fallbacks.append(
                                {
                                    "pair_id": pair_id,
                                    "reason": "MISSING_DUPLICATE_OR_INVALID_PAIR",
                                }
                            )
                        assert pair_item is not None
                        adapted.append(pair_item)
                    if fallbacks:
                        self.registry.append_decision_audit(
                            DecisionAuditRecord(
                                audit_id=(
                                    f"package-n13-degraded:{models.run_id}:"
                                    f"{batch_index}:{_hash_json(fallbacks)[:16]}"
                                ),
                                run_id=models.run_id,
                                decision_type="PACKAGE_N13_DEGRADED",
                                subject_id=f"batch:{batch_index}",
                                payload={
                                    "fallback": "DIFFERENT_PACKAGE",
                                    "pairs": fallbacks,
                                },
                            )
                        )
                    return {"decisions": adapted}

                def validate(output: PackageMergeWireDecisionBatch) -> None:
                    actual = {item.pair_id for item in output.decisions}
                    if actual != set(pair_by_id):
                        raise ValueError(
                            "package merge decisions must cover exactly requested pairs"
                        )

                output = models.typed(
                    tier=ModelTier.M3,
                    stage="package_merge",
                    request=request,
                    output_type=PackageMergeWireDecisionBatch,
                    validator=validate,
                    payload_adapter=adapt,
                )
                return (
                    batch_index,
                    PackageMergeDecisionBatch(
                        decisions=[
                            PackagePairMergeDecision(
                                source_package_id=full_by_short[
                                    pair_by_id[decision.pair_id][0]
                                ],
                                target_package_id=full_by_short[
                                    pair_by_id[decision.pair_id][1]
                                ],
                                relation=decision.relation,
                                reason=decision.reason,
                            )
                            for decision in output.decisions
                        ]
                    ),
                )

            with ThreadPoolExecutor(max_workers=min(MODEL_CONCURRENCY, len(batches))) as executor:
                outputs = list(executor.map(process_batch, enumerate(batches)))
            for _, output in sorted(outputs):
                m3_decisions.extend(output.decisions)

        decisions = [*m0_decisions, *m3_decisions]
        for decision in decisions:
            if decision.relation is PackageMergeRelation.SAME_PACKAGE:
                continue
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
            if decision.relation is PackageMergeRelation.UNCERTAIN:
                self.registry.append_decision_audit(
                    DecisionAuditRecord(
                        audit_id=f"package-merge-uncertain:{decision_id}",
                        run_id=run_id,
                        decision_type="PACKAGE_MERGE_UNCERTAIN",
                        subject_id=decision.source_package_id,
                        payload=decision.model_dump(mode="json"),
                    )
                )

        same_decisions = [
            decision
            for decision in decisions
            if decision.relation is PackageMergeRelation.SAME_PACKAGE
        ]
        components = _same_package_components(same_decisions)
        for component in components:
            contradictory = [
                decision
                for decision in decisions
                if decision.relation is PackageMergeRelation.DIFFERENT_PACKAGE
                and decision.source_package_id in component
                and decision.target_package_id in component
            ]
            component_same = [
                decision
                for decision in same_decisions
                if decision.source_package_id in component
                and decision.target_package_id in component
            ]
            decision_ids = [
                stable_id(
                    "package-merge-decision",
                    {
                        "run": run_id,
                        "source": decision.source_package_id,
                        "target": decision.target_package_id,
                    },
                )
                for decision in component_same
            ]
            if contradictory:
                for decision_id, decision in zip(decision_ids, component_same, strict=True):
                    self.registry.save_package_merge_decision(
                        decision_id=decision_id,
                        run_id=run_id,
                        decision=decision,
                    )
                self.registry.append_decision_audit(
                    DecisionAuditRecord(
                        audit_id=stable_id(
                            "audit",
                            {
                                "run": run_id,
                                "type": "package_merge_contradiction",
                                "component": sorted(component),
                            },
                        ),
                        run_id=run_id,
                        decision_type="PACKAGE_MERGE_CONTRADICTION",
                        subject_id=sorted(component)[0],
                        payload={
                            "component": sorted(component),
                            "contradictions": [
                                item.model_dump(mode="json") for item in contradictory
                            ],
                            "action": "KEEP_SEPARATE_AND_QUARANTINE",
                        },
                    )
                )
                for package_id in component:
                    package = active[package_id]
                    quarantined = package.model_copy(
                        update={
                            "quality_state": PackageQualityState.QUARANTINED,
                            "version": package.version + 1,
                        }
                    )
                    self.registry.save_package(quarantined)
                    self._sync_package_embeddings([quarantined], models)
                    active[package_id] = quarantined
                continue

            component_packages = [active[package_id] for package_id in component]
            target = min(
                component_packages,
                key=lambda package: canonical_package_sort_key(
                    package,
                    trusted_anchor_count=len(self._trusted_package_anchor_ids(package)),
                    source_count=self._package_source_count(package),
                ),
            )
            sources = [
                package for package in component_packages if package.package_id != target.package_id
            ]
            member_events = {
                event.event_id: event
                for package in component_packages
                for event in self._package_member_events(package)
            }
            anchor_ids = sorted(
                {
                    anchor_id
                    for package in component_packages
                    for anchor_id in package.package_anchor_ids
                }
            )
            artifact_ids = {
                package.anchor_artifact_id
                for package in component_packages
                if package.anchor_artifact_id
            }
            base = target.model_copy(
                update={
                    "anchor_entities": sorted(
                        {
                            entity
                            for package in component_packages
                            for entity in package.anchor_entities
                        }
                    ),
                    "package_anchor_ids": anchor_ids,
                    "anchor_artifact_id": (
                        next(iter(artifact_ids)) if len(artifact_ids) == 1 else None
                    ),
                    "member_event_ids": sorted(member_events),
                }
            )
            merged = compiler.compile(base, list(member_events.values())).model_copy(
                update={"version": target.version + 1}
            )
            representatives = representative_package_members(list(member_events.values()))
            retrieval_text = package_retrieval_text(merged, representatives)
            embedding = models.embed([retrieval_text], stage="package_merge_embedding_m1")
            plan = PackageMergePlan(
                plan_id=stable_id(
                    "package-merge-plan",
                    {
                        "run": run_id,
                        "target": target.package_id,
                        "sources": sorted(package.package_id for package in sources),
                    },
                ),
                target_package_id=target.package_id,
                source_package_ids=sorted(package.package_id for package in sources),
                decision_ids=decision_ids,
                reason="N13_SAME_PACKAGE_COMPONENT",
            )
            self.registry.apply_package_merge_plan(
                plan=plan,
                decisions=component_same,
                merged_package=merged,
                run_id=run_id,
                embedding_model=embedding.model,
                embedding_input_hash=hashlib.sha256(retrieval_text.encode("utf-8")).hexdigest(),
                embedding_vector=embedding.vectors[0],
            )
            for source_package in sources:
                active.pop(source_package.package_id, None)
                result_ids.discard(source_package.package_id)
            active[target.package_id] = merged
            result_ids.add(target.package_id)

        gate = PackageBoundaryGate(self.registry)
        for package_id in sorted(result_ids):
            candidate = active.get(package_id)
            if candidate is None or not candidate.member_event_ids:
                continue
            members = self._package_member_events(candidate)
            finding = gate.evaluate(candidate, members)
            if PackageBoundaryAction.REMOVE_MEMBER not in finding.actions:
                continue
            affected = self._repair_reaction_members_v13(
                candidate,
                members,
                active,
                embeddings,
                compiler,
                models,
                run_id=run_id,
            )
            result_ids.update(affected)
        for package_id in sorted(result_ids):
            gated_package = active.get(package_id) or self.registry.get_current_package(package_id)
            if gated_package is None or not gated_package.member_event_ids:
                continue
            finding = gate.evaluate(gated_package, self._package_member_events(gated_package))
            if gated_package.quality_state is not finding.quality_state:
                gated = gated_package.model_copy(
                    update={
                        "quality_state": finding.quality_state,
                        "version": gated_package.version + 1,
                    }
                )
                self.registry.save_package(gated)
                self._sync_package_embeddings([gated], models)
                active[package_id] = gated
                gated_package = gated
            if finding.reasons:
                self.registry.append_decision_audit(
                    DecisionAuditRecord(
                        audit_id=stable_id(
                            "audit",
                            {
                                "run": run_id,
                                "package": package_id,
                                "type": "package_boundary_gate",
                            },
                        ),
                        run_id=run_id,
                        decision_type="PACKAGE_BOUNDARY_GATE",
                        subject_id=package_id,
                        payload=finding.model_dump(mode="json"),
                    )
                )
        result: list[EventPackage] = []
        for package_id in sorted(result_ids):
            current = active.get(package_id)
            if current is not None:
                result.append(current)
        return result

    def _correct_packages(
        self, packages: list[EventPackage], models: _AuditedModels, *, run_id: str
    ) -> list[EventPackage]:
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
        if ambiguous:
            batches = [
                ambiguous[offset : offset + PACKAGE_MERGE_PAIR_BATCH]
                for offset in range(0, len(ambiguous), PACKAGE_MERGE_PAIR_BATCH)
            ]

            def process_merge_batch(
                indexed_batch: tuple[int, list[tuple[EventPackage, EventPackage]]],
            ) -> tuple[int, PackageMergeDecisionBatch]:
                batch_index, pairs = indexed_batch
                package_ids = sorted({package.package_id for pair in pairs for package in pair})
                short_by_full = {
                    package_id: f"p{index}" for index, package_id in enumerate(package_ids, start=1)
                }
                full_by_short = {short_id: full_id for full_id, short_id in short_by_full.items()}
                expected = {
                    (short_by_full[left.package_id], short_by_full[right.package_id])
                    for left, right in pairs
                }
                model_pairs: list[dict[str, object]] = []
                for left, right in pairs:
                    source_payload = left.model_dump(mode="json")
                    target_payload = right.model_dump(mode="json")
                    source_payload["package_id"] = short_by_full[left.package_id]
                    target_payload["package_id"] = short_by_full[right.package_id]
                    source_payload["member_event_count"] = len(
                        source_payload.pop("member_event_ids", [])
                    )
                    target_payload["member_event_count"] = len(
                        target_payload.pop("member_event_ids", [])
                    )
                    model_pairs.append({"source": source_payload, "target": target_payload})
                request = StructuredModelRequest(
                    system_prompt=_prompt("package_merge.md"),
                    user_prompt=json.dumps(
                        {
                            "batch_index": batch_index,
                            "batch_count": len(batches),
                            "pairs": model_pairs,
                        },
                        ensure_ascii=False,
                    ),
                    json_schema=PackageMergeDecisionBatch.model_json_schema(),
                )

                def adapt_merge_payload(payload: object) -> object:
                    if not isinstance(payload, dict) or not isinstance(
                        payload.get("decisions"), list
                    ):
                        return payload
                    adapted: list[dict[str, object]] = []
                    for raw in payload["decisions"]:
                        if not isinstance(raw, dict):
                            continue
                        item = {
                            key: raw[key]
                            for key in (
                                "source_package_id",
                                "target_package_id",
                                "relation",
                            )
                            if key in raw
                        }
                        relation = item.get("relation")
                        if isinstance(relation, str):
                            item["relation"] = relation.strip().upper()
                        adapted.append(item)
                    return {"decisions": adapted}

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
                    payload_adapter=adapt_merge_payload,
                )
                restored = PackageMergeDecisionBatch(
                    decisions=[
                        decision.model_copy(
                            update={
                                "source_package_id": full_by_short[decision.source_package_id],
                                "target_package_id": full_by_short[decision.target_package_id],
                            }
                        )
                        for decision in output.decisions
                    ]
                )
                return batch_index, restored

            with ThreadPoolExecutor(max_workers=min(MODEL_CONCURRENCY, len(batches))) as executor:
                outputs = list(executor.map(process_merge_batch, enumerate(batches)))
            for _, output in sorted(outputs):
                for decision in output.decisions:
                    self._apply_package_merge_decision(
                        decision,
                        active=active,
                        run_id=run_id,
                    )
        for package in active.values():
            if len(package.member_event_ids) > 25:
                self.registry.append_decision_audit(
                    DecisionAuditRecord(
                        audit_id=stable_id(
                            "audit",
                            {
                                "run": run_id,
                                "package": package.package_id,
                                "type": "package_overexpansion",
                            },
                        ),
                        run_id=run_id,
                        decision_type="PACKAGE_OVEREXPANSION",
                        subject_id=package.package_id,
                        payload={"member_count": len(package.member_event_ids)},
                    )
                )
        return list(active.values())

    def _apply_package_merge_decision(
        self,
        decision: PackagePairMergeDecision,
        *,
        active: dict[str, EventPackage],
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
            self.registry.append_decision_audit(
                DecisionAuditRecord(
                    audit_id=stable_id(
                        "audit",
                        {
                            "run": run_id,
                            "decision": decision_id,
                            "type": "package_merge_uncertain",
                        },
                    ),
                    run_id=run_id,
                    decision_type="PACKAGE_MERGE_UNCERTAIN",
                    subject_id=decision.source_package_id,
                    payload={
                        "candidate_package_id": decision.target_package_id,
                        "decision": decision.model_dump(mode="json"),
                        "action": "KEEP_SEPARATE",
                    },
                )
            )

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


def _provisional_routes(
    current: EventMention,
    current_profile: IdentityProfile,
    prior: EventMention,
    prior_profile: IdentityProfile,
) -> set[RecallRoute]:
    routes: set[RecallRoute] = set()
    if current.event_family is prior.event_family:
        routes.add(RecallRoute.EVENT_FAMILY)
    if current_profile.model_dump(mode="json") == prior_profile.model_dump(mode="json"):
        routes.add(RecallRoute.SCHEMA_IDENTITY)
    if set(core_entity_ids_from_profile(current_profile)).intersection(
        core_entity_ids_from_profile(prior_profile)
    ):
        routes.add(RecallRoute.CORE_ENTITY)
    current_period = reference_period_from_profile(current_profile)
    prior_period = reference_period_from_profile(prior_profile)
    if current_period and current_period == prior_period:
        routes.add(RecallRoute.TIME_WINDOW)
    return routes


def _package_ranges_near(left: EventPackage, right: EventPackage) -> bool:
    def as_date(value: date | datetime | None) -> date | None:
        if isinstance(value, datetime):
            return value.date()
        return value

    left_start = as_date(left.time_range.start)
    left_end = as_date(left.time_range.end) or left_start
    right_start = as_date(right.time_range.start)
    right_end = as_date(right.time_range.end) or right_start
    if left_start is None or left_end is None or right_start is None or right_end is None:
        return False
    return not (
        left_end.toordinal() + 45 < right_start.toordinal()
        or right_end.toordinal() + 45 < left_start.toordinal()
    )


def _same_package_components(
    decisions: Sequence[PackagePairMergeDecision],
) -> list[set[str]]:
    parents: dict[str, str] = {}

    def root(value: str) -> str:
        parents.setdefault(value, value)
        while parents[value] != value:
            parents[value] = parents[parents[value]]
            value = parents[value]
        return value

    def union(left: str, right: str) -> None:
        left_root = root(left)
        right_root = root(right)
        if left_root != right_root:
            parents[max(left_root, right_root)] = min(left_root, right_root)

    for decision in decisions:
        union(decision.source_package_id, decision.target_package_id)
    components: dict[str, set[str]] = {}
    for package_id in parents:
        components.setdefault(root(package_id), set()).add(package_id)
    return [component for component in components.values() if len(component) > 1]


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
    members = [item for item in decisions if item.relation is PackageAssignmentRelation.MEMBER]
    if len(members) == 1:
        return members[0]
    external = [
        item for item in decisions if item.relation is PackageAssignmentRelation.EXTERNAL_RELATED
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
        item.relation is PackageAssignmentRelation.NOT_RELATED for item in decisions
    ):
        return PackageAction.CREATE_NEW_PACKAGE, "ALL_PACKAGES_NOT_RELATED"
    if not decisions:
        return PackageAction.CREATE_NEW_PACKAGE, "ALL_PACKAGE_CANDIDATES_HARD_BLOCKED"
    return PackageAction.CREATE_NEW_PACKAGE, "PACKAGE_DECISION_UNCERTAIN"


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
