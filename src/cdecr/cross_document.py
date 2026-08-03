"""Incremental Atomic Event and Event Package engine for standalone CDECR."""

from __future__ import annotations

import hashlib
import json
import math
import re
import threading
import traceback
import uuid
from collections import defaultdict
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal, TypeVar

from pydantic import BaseModel, ValidationError

from cdecr.atomic_identity import (
    CanonicalIdentityView,
    IdentityComparison,
    atomic_identity_text,
    canonical_identity_view,
    compare_identity_groups,
    resolved_identity_evidence,
)
from cdecr.atomic_identity_contracts import (
    AtomicIdentitySidecar,
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
from cdecr.atomic_merge_invariant import (
    AtomicMergeInvariantResult,
    AtomicMergeInvariantRule,
    evaluate_atomic_merge_invariant,
    first_unlocked_atomic_candidate,
)
from cdecr.atomic_recall_audit import (
    append_ranked_candidate_snapshot,
    candidate_root_id,
)
from cdecr.bulk_epoch.indexes import MultiKeyBoundedIndex
from cdecr.canonical_field_resolution import (
    FIELD_RESOLVER_VERSION,
    CanonicalFieldResolutionEngine,
    field_links_hash,
    package_field_links_hash,
)
from cdecr.contracts import (
    AnalystActionIdentityProfile,
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
    PackageFamily,
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
    AtomicCandidateAssessment,
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
    PackagePairBoundary,
    PackagePairDecision,
    PackagePairDecisionBatch,
    PackagePairEvaluation,
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
    build_n12_event_card,
    build_n12_package_card,
    build_slim_package_view,
    canonical_package_members,
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

ENGINE_VERSION = "cdecr-cross-document-v24-late-convergence"
PROMPT_VERSION = "cdecr-cross-document-prompts-v17-late-convergence"
WIRE_PROTOCOL_VERSION = "cdecr-cross-document-wire-atomic-dictionary-v9"
ATOMIC_ASSIGNMENT_POLICY_VERSION = "atomic-assignment-policy-v5-late-convergence"
ATOMIC_DECISION_MENTION_BATCH = 3
PACKAGE_DECISION_EVENT_BATCH = 12
PACKAGE_MERGE_PAIR_BATCH = 12
ATOMIC_TOP_K = 5
PACKAGE_TOP_K = 6
PACKAGE_N13_CANDIDATES_PER_TOUCHED = 5
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


def _compact_identity_card(
    sidecar: AtomicIdentitySidecar,
    view: CanonicalIdentityView,
    *,
    event_time: object,
) -> dict[str, object]:
    """Return the model-visible N9 identity card without compiler metadata duplication."""

    card: dict[str, object] = {}
    if sidecar.referent:
        card["referent"] = sidecar.referent
    if sidecar.occurrence:
        card["occurrence"] = sidecar.occurrence
    if sidecar.facet:
        card["facet"] = sidecar.facet
    if view.canonical_participants_by_role:
        card["roles"] = view.canonical_participants_by_role
    if view.canonical_locations:
        card["locations"] = view.canonical_locations
    if view.canonical_named_objects:
        card["objects"] = view.canonical_named_objects
    if isinstance(event_time, BaseModel):
        raw_time = event_time.model_dump(mode="json")
        compact_time = [
            raw_time.get("event_start"),
            raw_time.get("event_end"),
            raw_time.get("precision"),
            raw_time.get("reference_period_id"),
        ]
        while compact_time and compact_time[-1] is None:
            compact_time.pop()
        if any(value is not None for value in compact_time):
            card["time"] = compact_time
    return card


def _compact_quantities(mention: EventMention) -> list[list[object]]:
    """Keep the metric/value/comparator semantics while removing repeated JSON keys."""

    return [
        [item.metric_id, item.role.value, item.value, item.unit, item.raw_text]
        for item in mention.quantities
    ]


def _minimal_evidence_context(source: SourceMessage, mention: EventMention) -> str:
    """Select the smallest complete evidence sentence, capped at 600 characters."""

    passages: list[str] = []
    for span in mention.evidence_spans:
        raw = source.title if span.field == "title" else source.text
        left = max(
            raw.rfind(".", 0, span.start_char),
            raw.rfind("!", 0, span.start_char),
            raw.rfind("?", 0, span.start_char),
            raw.rfind("\n", 0, span.start_char),
        )
        sentence_start = 0 if left < 0 else left + 1
        right_candidates = [
            position
            for separator in (".", "!", "?", "\n")
            if (position := raw.find(separator, span.end_char)) >= 0
        ]
        sentence_end = min(right_candidates) + 1 if right_candidates else len(raw)
        sentence = raw[sentence_start:sentence_end].strip()
        if sentence:
            passages.append(sentence)
    context = " ".join(dict.fromkeys(passages)).strip()
    if not context:
        context = mention.source_claim or mention.canonical_proposition
    return context[:600]


_N9_BOUNDARY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("number", re.compile(r"(?:[$€£]?\d|\d[%x×])", re.IGNORECASE)),
    (
        "comparator",
        re.compile(
            r"(?:>|<|at least|more than|less than|about|approximately|range)",
            re.IGNORECASE,
        ),
    ),
    (
        "session",
        re.compile(
            r"(?:pre[- ]market|after[- ]hours|intraday|market open|close)",
            re.IGNORECASE,
        ),
    ),
    ("period", re.compile(r"(?:q[1-4]|fy\s*\d{2,4}|quarter|year|month|week)", re.IGNORECASE)),
    ("state", re.compile(r"(?:actual|guidance|forecast|expects?|plans?|reported)", re.IGNORECASE)),
    (
        "qualitative",
        re.compile(
            r"(?:supply|demand|shortage|streak|record|line of sight)",
            re.IGNORECASE,
        ),
    ),
)


def _claim_boundary_signals(value: str) -> set[str]:
    return {name for name, pattern in _N9_BOUNDARY_PATTERNS if pattern.search(value)}


def _normalized_claim(value: str) -> str:
    return " ".join(re.sub(r"[^\w]+", " ", value.casefold()).split())


def _select_representative_claims(
    canonical_proposition: str,
    claims: Sequence[str],
) -> list[str]:
    """Select up to two non-duplicative claims that add identity-boundary evidence."""

    canonical_normalized = _normalized_claim(canonical_proposition)
    canonical_signals = _claim_boundary_signals(canonical_proposition)
    ranked: list[tuple[int, int, str, set[str]]] = []
    seen: set[str] = set()
    for claim in claims:
        normalized = _normalized_claim(claim)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        canonical_tokens = set(canonical_normalized.split())
        claim_tokens = set(normalized.split())
        overlap = len(canonical_tokens & claim_tokens) / max(
            1, len(canonical_tokens | claim_tokens)
        )
        if normalized == canonical_normalized or overlap >= 0.88:
            continue
        signals = _claim_boundary_signals(claim)
        added_signals = signals - canonical_signals
        if not added_signals and not (signals and overlap < 0.65):
            continue
        ranked.append((len(added_signals), len(signals), claim, signals))
    ranked.sort(key=lambda item: (-item[0], -item[1], len(item[2]), item[2]))
    selected: list[str] = []
    covered = set(canonical_signals)
    for _, _, claim, signals in ranked:
        if selected and not (signals - covered):
            continue
        selected.append(claim[:600])
        covered.update(signals)
        if len(selected) == 2:
            break
    return selected


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
            "incoming_primary_metric_trust_reason": (incoming.primary_metric_trust_reason),
            "existing_primary_metric_ids": sorted(existing_metrics),
            "incoming_principal_company_ids": sorted(incoming_companies),
            "incoming_principal_company_field_paths": (incoming.principal_company_field_paths),
            "incoming_principal_company_trust_reason": (incoming.principal_company_trust_reason),
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
        RecallRoute.INCUMBENT_MEMBERSHIP: 0.30,
        RecallRoute.PARENT_CONTEXT: 0.28,
        RecallRoute.MEMBER_IDENTITY: 0.24,
        RecallRoute.SAME_SOURCE_MEMBER: 0.10,
        RecallRoute.MEMBER_EMBEDDING: 0.08,
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
        request = request.model_copy(
            update={
                "metadata": {
                    **request.metadata,
                    "stage": call_stage,
                    "priority": "repair" if repaired else "normal",
                }
            }
        )
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
        hard_cannot_link_mode: str = HardCannotLinkMode.ENFORCE.value,
        package_conflict_mode: str = PackageConflictMode.OFF.value,
        n9_wire_protocol: str = "on",
        n12_wire_protocol: str = "on",
        n13_wire_protocol: str = "on",
        n9_active_requests: int = 24,
        n12_active_requests: int = 24,
        n13_active_requests: int = 24,
        atomic_enforced_rules: Sequence[str] | None = None,
        knowledge_base: V2KnowledgeBase | None = None,
    ) -> None:
        self.registry = registry
        self.embedding_client = embedding_client
        self.m2_client = m2_client
        self.m3_client = m3_client
        self.model_m1 = model_m1
        self.model_m2 = model_m2
        self.model_m3 = model_m3
        requested_hard_cannot_link_mode = HardCannotLinkMode(hard_cannot_link_mode)
        self.legacy_hard_cannot_link_requested_mode = requested_hard_cannot_link_mode
        self.hard_cannot_link_mode = requested_hard_cannot_link_mode
        default_atomic_enforced_rules = {
            AtomicMergeInvariantRule.PRIMARY_METRIC_FAMILY,
            AtomicMergeInvariantRule.METRIC,
            AtomicMergeInvariantRule.COMPLETE_REFERENT,
            AtomicMergeInvariantRule.ASSERTION_STATE,
        }
        self.atomic_enforced_rules = frozenset(
            (AtomicMergeInvariantRule(value) for value in atomic_enforced_rules)
            if atomic_enforced_rules is not None
            else default_atomic_enforced_rules
        )
        self.package_conflict_mode = PackageConflictMode(package_conflict_mode)
        if n9_wire_protocol != "on":
            raise ValueError("N9 optimized dictionary protocol is mandatory")
        if n12_wire_protocol != "on":
            raise ValueError("N12 optimized dictionary protocol is mandatory")
        if n13_wire_protocol != "on":
            raise ValueError("N13 optimized dictionary protocol is mandatory")
        self.n9_wire_protocol = n9_wire_protocol
        self.n12_wire_protocol = n12_wire_protocol
        self.n13_wire_protocol = n13_wire_protocol
        self.n9_active_requests = max(1, n9_active_requests)
        self.n12_active_requests = max(1, n12_active_requests)
        self.n13_active_requests = max(1, n13_active_requests)
        self.n13_pair_local_apply = True
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
            "legacy_hard_cannot_link_requested_mode": (
                self.legacy_hard_cannot_link_requested_mode.value
            ),
            "atomic_merge_invariant_enforced_rules": sorted(
                rule.value for rule in self.atomic_enforced_rules
            ),
            "package_conflict_mode": self.package_conflict_mode.value,
            "atomic_assignment_policy_version": ATOMIC_ASSIGNMENT_POLICY_VERSION,
            "package_assignment_policy_version": PACKAGE_ASSIGNMENT_POLICY_VERSION,
            "package_profile_compiler_version": PACKAGE_PROFILE_COMPILER_VERSION,
            "package_boundary_policy_version": PACKAGE_BOUNDARY_POLICY_VERSION,
            "hold_policy": "removed",
            "wire_protocol_version": WIRE_PROTOCOL_VERSION,
            "n13_wire_protocol": self.n13_wire_protocol,
        }

    def processing_key(
        self,
        message_id: str,
        mentions: Sequence[EventMention],
    ) -> str:
        return _hash_json(
            {
                "message_id": message_id,
                "execution_mode": "INCREMENTAL",
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

    def process(
        self,
        message_id: str,
    ) -> CrossDocumentResult:
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
            current_atomic_events = self.registry.list_current_atomic_events(limit=10000)
            largest_cluster = max(
                current_atomic_events,
                key=lambda event: len(event.mention_ids),
                default=None,
            )
            largest_size = len(largest_cluster.mention_ids) if largest_cluster is not None else 0
            hard_violation_count = sum(
                assignment.action is AtomicAction.MERGE and bool(assignment.hard_conflicts)
                for assignment in atomic_assignments
            )
            candidate_counts["atomic_max_cluster_size"] = largest_size
            candidate_counts["atomic_hard_conflict_violations"] = hard_violation_count
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
                            largest_cluster.event_id if largest_cluster is not None else None
                        ),
                        "max_cluster_size": largest_size,
                        "top_cluster_predicted_pairs": (largest_size * (largest_size - 1) // 2),
                        "hard_conflict_violation_count": hard_violation_count,
                        "top_cluster_fp_contribution": ("REQUIRES_GOLD_EVALUATION"),
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

    def process_batch(
        self,
        message_ids: Sequence[str],
    ) -> list[CrossDocumentResult]:
        ordered_ids = sorted(
            dict.fromkeys(message_ids),
            key=lambda message_id: (
                (
                    source.published_at
                    if (source := self.registry.get_source(message_id)) is not None
                    else datetime.max.replace(tzinfo=UTC)
                ),
                message_id,
            ),
        )
        return [self.process(message_id) for message_id in ordered_ids]

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
        provisional_by_id: dict[str, tuple[AtomicEvent, EventMention, CompiledMentionIdentity]] = {}
        provisional_index = MultiKeyBoundedIndex(bucket_limit=12)

        def provisional_keys(
            mention: EventMention,
            identity: CompiledMentionIdentity,
        ) -> list[str]:
            profile = identity.identity_profile
            assert profile is not None
            keys = [f"family:{mention.event_family.value}"]
            keys.append(f"profile:{_hash_json(profile.model_dump(mode='json'))}")
            keys.extend(f"entity:{value}" for value in core_entity_ids_from_profile(profile))
            vector = mention_vectors.get(mention.mention_id, [])
            if vector:
                strongest = sorted(
                    range(len(vector)), key=lambda index: (-abs(vector[index]), index)
                )[:6]
                keys.append(
                    "vector:"
                    + ":".join(
                        f"{index}{'p' if vector[index] >= 0 else 'n'}" for index in strongest
                    )
                )
            return keys

        def register_provisional(
            mention: EventMention,
            identity: CompiledMentionIdentity,
        ) -> None:
            profile = identity.identity_profile
            assert profile is not None
            item = (
                singleton_atomic_event(mention, identity_profile=profile, provisional=True),
                mention,
                identity,
            )
            provisional_by_id[item[0].event_id] = item
            provisional_index.add(item[0].event_id, provisional_keys(mention, identity))

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
                register_provisional(mention, compiled_identity)
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
            scheduler_ids = provisional_index.query(
                provisional_keys(mention, compiled_identity),
                limit=12,
            )
            for provisional_id in scheduler_ids:
                provisional_event, prior_mention, prior_compiled = provisional_by_id[provisional_id]
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
                    provisional_item = provisional_by_id.get(event_id)
                    representatives = [] if provisional_item is None else [provisional_item[1]]
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
                    active_conflicts, discriminant_payload = _compiled_active_hard_conflicts(
                        compiled_identity,
                        representative_compiled,
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
                                    "enforced": False,
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
                effective_conflicts: list[HardConflictCode] = []
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
            register_provisional(mention, compiled_identity)
        return output

    def _atomic_decisions(
        self,
        mentions: list[EventMention],
        candidates: dict[str, list[AtomicCandidate]],
        compiled: dict[str, CompiledMentionIdentity],
        models: _AuditedModels,
    ) -> dict[str, AtomicAssignmentDecision]:
        deterministic_by_mention: dict[str, list[AtomicCandidateAssessment]] = {}
        eligible: dict[str, list[AtomicCandidate]] = {}
        decisions: dict[str, AtomicAssignmentDecision] = {}
        for mention in mentions:
            incoming_sidecar = compiled[mention.mention_id].atomic_identity_sidecar
            assert incoming_sidecar is not None
            model_candidates: list[AtomicCandidate] = []
            deterministic_assessments: list[AtomicCandidateAssessment] = []
            for candidate in candidates[mention.mention_id]:
                candidate_sidecar = candidate.identity_sidecar
                assert candidate_sidecar is not None
                invariant = evaluate_atomic_merge_invariant(
                    incoming_sidecar,
                    candidate_sidecar,
                    enforced_rules=self.atomic_enforced_rules,
                )
                if not invariant.enforced_rules:
                    model_candidates.append(candidate)
                    continue
                applicable_axes = set(incoming_sidecar.applicable_axes).intersection(
                    candidate_sidecar.applicable_axes
                )
                enforced_axes: set[IdentityAxis] = set()
                if set(invariant.enforced_rules).intersection(
                    {
                        AtomicMergeInvariantRule.PRIMARY_METRIC_FAMILY,
                        AtomicMergeInvariantRule.METRIC,
                    }
                ):
                    enforced_axes.add(IdentityAxis.FACET)
                if AtomicMergeInvariantRule.COMPLETE_REFERENT in invariant.enforced_rules:
                    enforced_axes.add(IdentityAxis.REFERENT)
                if AtomicMergeInvariantRule.ASSERTION_STATE in invariant.enforced_rules:
                    enforced_axes.add(IdentityAxis.OCCURRENCE)
                deterministic_verdicts = deterministic_axis_verdicts(
                    incoming_sidecar,
                    candidate_sidecar,
                )
                assessment = AtomicCandidateAssessment.model_validate(
                    {
                        "candidate_event_id": candidate.event.event_id,
                        "relation": AtomicSemanticRelation.RELATED_NOT_SAME,
                        "axis_assessments": [
                            {
                                "axis": axis,
                                "verdict": (
                                    IdentityAxisVerdict.CONFLICT
                                    if axis in enforced_axes
                                    else deterministic_verdicts.get(
                                        axis, IdentityAxisVerdict.AMBIGUOUS
                                    )
                                ),
                            }
                            for axis in sorted(applicable_axes, key=str)
                        ],
                        "claim_conflict": False,
                        "identity_differences": [
                            f"DETERMINISTIC_NOT_SAME:{rule.value}"
                            for rule in invariant.enforced_rules
                        ],
                    }
                )
                deterministic_assessments.append(assessment)
                self.registry.append_decision_audit(
                    DecisionAuditRecord(
                        audit_id=(
                            f"atomic-n9-deterministic:{models.run_id}:"
                            f"{mention.mention_id}:{candidate.event.event_id}"
                        ),
                        run_id=models.run_id,
                        decision_type="ATOMIC_N9_DETERMINISTIC_NOT_SAME",
                        subject_id=mention.mention_id,
                        payload={
                            "candidate_event_id": candidate.event.event_id,
                            "rules": [rule.value for rule in invariant.enforced_rules],
                            "axes": [axis.value for axis in sorted(enforced_axes, key=str)],
                            "assessment": "DETERMINISTIC_NOT_SAME",
                            "sent_to_model": False,
                        },
                    )
                )
            eligible[mention.mention_id] = model_candidates
            deterministic_by_mention[mention.mention_id] = deterministic_assessments
            if deterministic_assessments and not model_candidates:
                decisions[mention.mention_id] = AtomicAssignmentDecision(
                    mention_id=mention.mention_id,
                    action=AtomicAction.CREATE_NEW,
                    merge_target_event_id=None,
                    candidate_assessments=deterministic_assessments,
                    related_candidate_event_ids=[
                        item.candidate_event_id for item in deterministic_assessments
                    ],
                    possible_duplicate_atomic_ids=[],
                )
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
            atomic_short_by_full: dict[str, str] = {}
            for mention in batch_mentions:
                for candidate in batch_candidates[mention.mention_id]:
                    atomic_short_by_full.setdefault(
                        candidate.event.event_id,
                        f"a{len(atomic_short_by_full) + 1}",
                    )
            candidate_short_by_full: dict[str, dict[str, str]] = {}
            candidate_full_by_short: dict[str, dict[str, str]] = {}
            for mention in batch_mentions:
                full_id = mention.mention_id
                mention_short_id = mention_short_by_full[full_id]
                mapping = {
                    candidate.event.event_id: atomic_short_by_full[candidate.event.event_id]
                    for candidate in batch_candidates[full_id]
                }
                candidate_short_by_full[full_id] = mapping
                candidate_full_by_short[mention_short_id] = {
                    short_id: candidate_id for candidate_id, short_id in mapping.items()
                }
            expected = {
                mention_short_by_full[mention_id]: set(candidate_short_by_full[mention_id].values())
                for mention_id in batch_candidates
            }
            model_atoms: dict[str, dict[str, object]] = {}
            for mention in batch_mentions:
                for candidate in batch_candidates[mention.mention_id]:
                    event = candidate.event
                    short_event_id = atomic_short_by_full[event.event_id]
                    if short_event_id in model_atoms:
                        continue
                    representatives = [
                        item
                        for mention_id in event.representative_mention_ids
                        if (item := self.registry.get_mention(mention_id)) is not None
                    ]
                    raw_claims = event.consensus_claims.get("source_claims", [])
                    claims = raw_claims if isinstance(raw_claims, list) else []
                    all_representative_claims = [
                        str(item.get("source_claim") or item.get("canonical_proposition"))
                        for item in claims
                        if isinstance(item, dict)
                        and (item.get("source_claim") or item.get("canonical_proposition"))
                    ]
                    candidate_sidecar = candidate.identity_sidecar
                    assert candidate_sidecar is not None
                    atom: dict[str, object] = {
                        "prop": event.canonical_proposition,
                        "family": event.event_family.value,
                        "identity": _compact_identity_card(
                            candidate_sidecar,
                            canonical_identity_view(self.registry, representatives),
                            event_time=event.time,
                        ),
                    }
                    representative_claims = _select_representative_claims(
                        event.canonical_proposition,
                        all_representative_claims,
                    )
                    if representative_claims:
                        atom["claims"] = representative_claims
                    if event.event_id.startswith("provisional:"):
                        atom["provisional"] = True
                    model_atoms[short_event_id] = atom
            model_mentions: dict[str, dict[str, object]] = {}
            wire_tasks: list[dict[str, object]] = []
            edge_by_pair: dict[tuple[str, str], dict[str, object]] = {}
            for mention in batch_mentions:
                mention_short_id = mention_short_by_full[mention.mention_id]
                compiled_identity = compiled[mention.mention_id]
                profile = compiled_identity.identity_profile
                assert profile is not None
                incoming_sidecar = compiled_identity.atomic_identity_sidecar
                assert incoming_sidecar is not None
                source = self.registry.get_source(mention.message_id)
                incoming_view = canonical_identity_view(self.registry, [mention])
                incoming_payload: dict[str, object] = {
                    "prop": mention.canonical_proposition,
                    "family": mention.event_family.value,
                    "identity": _compact_identity_card(
                        incoming_sidecar,
                        incoming_view,
                        event_time=mention.time,
                    ),
                }
                quantities = _compact_quantities(mention)
                if quantities:
                    incoming_payload["quantities"] = quantities
                evidence = (
                    _minimal_evidence_context(source, mention)
                    if source is not None
                    else (mention.source_claim or mention.canonical_proposition)[:600]
                )
                if evidence and _normalized_claim(evidence) != _normalized_claim(
                    mention.canonical_proposition
                ):
                    incoming_payload["evidence"] = evidence
                if mention.source_claim and _normalized_claim(mention.source_claim) not in {
                    _normalized_claim(evidence),
                    _normalized_claim(mention.canonical_proposition),
                }:
                    incoming_payload["claim"] = mention.source_claim[:600]
                model_mentions[mention_short_id] = incoming_payload
                task_edges: list[dict[str, object]] = []
                for candidate in batch_candidates[mention.mention_id]:
                    short_event_id = candidate_short_by_full[mention.mention_id][
                        candidate.event.event_id
                    ]
                    edge: dict[str, object] = {"atomic": short_event_id}
                    edge_by_pair[(mention_short_id, short_event_id)] = edge
                    task_edges.append(edge)
                wire_tasks.append({"mention": mention_short_id, "candidates": task_edges})
            expected_axes: dict[str, dict[str, set[IdentityAxis]]] = {}
            deterministic_axis_map: dict[
                str, dict[str, dict[IdentityAxis, IdentityAxisVerdict]]
            ] = {}
            enforced_axes_map: dict[str, dict[str, set[IdentityAxis]]] = {}
            exact_signature_matches: set[tuple[str, str]] = set()
            for mention in batch_mentions:
                mention_short_id = mention_short_by_full[mention.mention_id]
                incoming_sidecar = compiled[mention.mention_id].atomic_identity_sidecar
                assert incoming_sidecar is not None
                expected_axes[mention_short_id] = {}
                deterministic_axis_map[mention_short_id] = {}
                enforced_axes_map[mention_short_id] = {}
                for candidate in batch_candidates[mention.mention_id]:
                    candidate_short_id = candidate_short_by_full[mention.mention_id][
                        candidate.event.event_id
                    ]
                    candidate_sidecar = candidate.identity_sidecar
                    assert candidate_sidecar is not None
                    applicable_axes = set(incoming_sidecar.applicable_axes).intersection(
                        candidate_sidecar.applicable_axes
                    )
                    expected_axes[mention_short_id][candidate_short_id] = applicable_axes
                    deterministic_axis_map[mention_short_id][candidate_short_id] = {
                        axis: verdict
                        for axis, verdict in deterministic_axis_verdicts(
                            incoming_sidecar,
                            candidate_sidecar,
                        ).items()
                        if axis in applicable_axes
                    }
                    invariant = evaluate_atomic_merge_invariant(
                        incoming_sidecar,
                        candidate_sidecar,
                        enforced_rules=self.atomic_enforced_rules,
                    )
                    enforced_rules = set(invariant.enforced_rules)
                    enforced_axes: set[IdentityAxis] = set()
                    if enforced_rules.intersection(
                        {
                            AtomicMergeInvariantRule.PRIMARY_METRIC_FAMILY,
                            AtomicMergeInvariantRule.METRIC,
                        }
                    ):
                        enforced_axes.add(IdentityAxis.FACET)
                    if AtomicMergeInvariantRule.COMPLETE_REFERENT in enforced_rules:
                        enforced_axes.add(IdentityAxis.REFERENT)
                    if AtomicMergeInvariantRule.ASSERTION_STATE in enforced_rules:
                        enforced_axes.add(IdentityAxis.OCCURRENCE)
                    enforced_axes_map[mention_short_id][candidate_short_id] = (
                        enforced_axes.intersection(applicable_axes)
                    )
                    edge = edge_by_pair[(mention_short_id, candidate_short_id)]
                    axis_alias = {
                        IdentityAxis.REFERENT: "R",
                        IdentityAxis.OCCURRENCE: "O",
                        IdentityAxis.FACET: "F",
                    }
                    edge["axes"] = [axis_alias[axis] for axis in sorted(applicable_axes, key=str)]
                    if incoming_sidecar.signature_hash == candidate_sidecar.signature_hash:
                        exact_signature_matches.add((mention_short_id, candidate_short_id))
                        edge["exact"] = True
                    conflict_axes = sorted(
                        axis.value
                        for axis, verdict in deterministic_axis_map[mention_short_id][
                            candidate_short_id
                        ].items()
                        if verdict is IdentityAxisVerdict.CONFLICT
                    )
                    if conflict_axes:
                        edge["warnings"] = [
                            axis_alias[IdentityAxis(value)] for value in conflict_axes
                        ]
                    if enforced_axes_map[mention_short_id][candidate_short_id]:
                        edge["enforced"] = sorted(
                            axis_alias[axis]
                            for axis in enforced_axes_map[mention_short_id][candidate_short_id]
                        )
            wire_payload: dict[str, object] = {
                "mentions": model_mentions,
                "atomics": model_atoms,
                "tasks": wire_tasks,
            }
            request = StructuredModelRequest(
                system_prompt=_prompt("atomic_coreference.md"),
                user_prompt=compact_json(wire_payload),
                json_schema=AtomicDecisionBatch.model_json_schema(),
            )
            invalid_task_errors: dict[str, str] = {}

            def adapt_and_audit_atomic_payload(
                payload: object,
                attempt: str,
            ) -> object:
                adapted = adapt_atomic_payload(payload)
                if not isinstance(adapted, dict) or not isinstance(adapted.get("decisions"), list):
                    return adapted

                raw_decisions = [item for item in adapted["decisions"] if isinstance(item, dict)]
                returned_mentions: list[str] = []
                for item in raw_decisions:
                    mention_id = item.get("mention_id")
                    if isinstance(mention_id, str):
                        returned_mentions.append(mention_id)
                mention_duplicates = sorted(
                    {value for value in returned_mentions if returned_mentions.count(value) > 1}
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
                        [item for item in raw_assessments if isinstance(item, dict)]
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
                    candidate_extra = sorted(set(returned_candidates) - expected_candidates)
                    candidate_missing = sorted(expected_candidates - set(returned_candidates))

                    filtered: list[dict[str, object]] = []
                    seen_assessments: dict[str, dict[str, object]] = {}
                    for assessment in assessments:
                        candidate_id = assessment.get("candidate_event_id")
                        if (
                            not isinstance(candidate_id, str)
                            or candidate_id not in expected_candidates
                        ):
                            continue
                        raw_axes = assessment.get("axis_assessments")
                        if isinstance(raw_axes, list):
                            allowed_axes = {
                                axis.value for axis in expected_axes[mention_id][candidate_id]
                            }
                            filtered_axes: list[dict[str, object]] = []
                            seen_axes: dict[str, object] = {}
                            dropped_axes: list[str] = []
                            conflicting_axes: list[str] = []
                            for raw_axis in raw_axes:
                                if not isinstance(raw_axis, dict):
                                    continue
                                axis = raw_axis.get("axis")
                                if not isinstance(axis, str) or axis not in allowed_axes:
                                    if isinstance(axis, str):
                                        dropped_axes.append(axis)
                                    continue
                                if axis in seen_axes:
                                    if seen_axes[axis] != raw_axis.get("verdict"):
                                        conflicting_axes.append(axis)
                                    else:
                                        dropped_axes.append(axis)
                                    continue
                                seen_axes[axis] = raw_axis.get("verdict")
                                filtered_axes.append(raw_axis)
                            assessment["axis_assessments"] = filtered_axes
                            if conflicting_axes:
                                assessment["_conflicting_duplicate_axes"] = sorted(
                                    set(conflicting_axes)
                                )
                            if dropped_axes:
                                normalizations.append(
                                    {
                                        "mention_id": mention_id,
                                        "candidate_event_id": candidate_id,
                                        "kind": "NON_APPLICABLE_OR_DUPLICATE_AXES_DROPPED",
                                        "axes": sorted(set(dropped_axes)),
                                    }
                                )
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
                        if isinstance(target, str) and target not in expected_candidates
                        else None
                    )
                    same_candidates = [
                        item.get("candidate_event_id")
                        for item in filtered
                        if item.get("relation") == AtomicSemanticRelation.SAME_EVENT.value
                        and isinstance(item.get("candidate_event_id"), str)
                    ]
                    if (
                        decision.get("action") == AtomicAction.MERGE.value
                        and (not isinstance(target, str) or target not in expected_candidates)
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
                                if isinstance(value, str) and value in expected_candidates
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
                                        "verdict": deterministic_axis_map[mention_id][candidate_id][
                                            axis
                                        ].value,
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
                    if safe_item is None or mention_id in mention_duplicates:
                        invalid_task_errors[mention_id] = (
                            "MISSING_DECISION"
                            if safe_item is None
                            else "CONFLICTING_DUPLICATE_DECISION"
                        )
                        safe_item = conservative_decision(mention_id)
                        task_diff = task_diffs.get(mention_id)
                        if not isinstance(task_diff, dict):
                            task_diff = {}
                            task_diffs[mention_id] = task_diff
                        task_diff["fallback"] = {
                            "action": AtomicAction.CREATE_NEW.value,
                            "reason": "N9_MISSING_OR_DUPLICATE_DECISION_CREATE_NEW",
                        }
                        safe_decisions.append(safe_item)
                        continue

                    raw_assessments = safe_item.get("candidate_assessments")
                    assessment_values = (
                        [item for item in raw_assessments if isinstance(item, dict)]
                        if isinstance(raw_assessments, list)
                        else []
                    )
                    assessments_by_candidate: dict[str, list[dict[str, object]]] = {}
                    for assessment in assessment_values:
                        candidate_id = assessment.get("candidate_event_id")
                        if isinstance(candidate_id, str) and candidate_id in expected[mention_id]:
                            assessments_by_candidate.setdefault(candidate_id, []).append(assessment)

                    valid_assessments: list[AtomicCandidateAssessment] = []
                    invalid_assessments: list[dict[str, object]] = []
                    candidate_order = list(candidate_full_by_short[mention_id])
                    for candidate_id in candidate_order:
                        candidate_values = assessments_by_candidate.get(candidate_id, [])
                        if len(candidate_values) != 1:
                            invalid_assessments.append(
                                {
                                    "candidate_event_id": candidate_id,
                                    "code": (
                                        "MISSING_ASSESSMENT"
                                        if not candidate_values
                                        else "CONFLICTING_DUPLICATE_ASSESSMENT"
                                    ),
                                }
                            )
                            continue
                        candidate_value = dict(candidate_values[0])
                        raw_conflicting_axes = candidate_value.pop(
                            "_conflicting_duplicate_axes",
                            [],
                        )
                        conflicting_axes = (
                            raw_conflicting_axes if isinstance(raw_conflicting_axes, list) else []
                        )
                        if conflicting_axes:
                            invalid_assessments.append(
                                {
                                    "candidate_event_id": candidate_id,
                                    "code": "CONFLICTING_DUPLICATE_AXIS",
                                    "axes": conflicting_axes,
                                }
                            )
                            continue
                        raw_axes = candidate_value.get("axis_assessments")
                        axes = (
                            [dict(item) for item in raw_axes if isinstance(item, dict)]
                            if isinstance(raw_axes, list)
                            else []
                        )
                        axis_by_name = {
                            item.get("axis"): item
                            for item in axes
                            if isinstance(item.get("axis"), str)
                        }
                        for axis in sorted(
                            expected_axes[mention_id][candidate_id],
                            key=str,
                        ):
                            if axis.value not in axis_by_name:
                                recovered_verdict = (
                                    IdentityAxisVerdict.MATCH
                                    if (mention_id, candidate_id) in exact_signature_matches
                                    else IdentityAxisVerdict.CONFLICT
                                    if axis in enforced_axes_map[mention_id][candidate_id]
                                    else IdentityAxisVerdict.AMBIGUOUS
                                )
                                axes.append(
                                    {
                                        "axis": axis.value,
                                        "verdict": recovered_verdict.value,
                                    }
                                )
                                normalizations.append(
                                    {
                                        "mention_id": mention_id,
                                        "candidate_event_id": candidate_id,
                                        "kind": "MISSING_AXIS_FILLED_FROM_DETERMINISTIC_VIEW",
                                        "axis": axis.value,
                                        "verdict": recovered_verdict.value,
                                    }
                                )
                        candidate_value["axis_assessments"] = axes
                        try:
                            parsed_assessment = AtomicCandidateAssessment.model_validate(
                                candidate_value
                            )
                        except ValidationError as exc:
                            invalid_assessments.append(
                                {
                                    "candidate_event_id": candidate_id,
                                    "code": "ASSESSMENT_SCHEMA_INVALID",
                                    "errors": [
                                        {
                                            "field_path": [
                                                str(part) for part in error.get("loc", ())
                                            ],
                                            "type": str(error.get("type", "value_error")),
                                        }
                                        for error in exc.errors(
                                            include_input=False,
                                            include_url=False,
                                        )[:10]
                                    ],
                                }
                            )
                            continue
                        verdict_by_axis = {
                            item.axis: item.verdict for item in parsed_assessment.axis_assessments
                        }
                        if (
                            mention_id,
                            candidate_id,
                        ) in exact_signature_matches and any(
                            verdict is not IdentityAxisVerdict.MATCH
                            for verdict in verdict_by_axis.values()
                        ):
                            parsed_assessment = parsed_assessment.model_copy(
                                update={
                                    "axis_assessments": [
                                        item.model_copy(
                                            update={"verdict": IdentityAxisVerdict.MATCH}
                                        )
                                        for item in parsed_assessment.axis_assessments
                                    ]
                                }
                            )
                            normalizations.append(
                                {
                                    "mention_id": mention_id,
                                    "candidate_event_id": candidate_id,
                                    "kind": "EXACT_SIGNATURE_AXES_NORMALIZED_TO_MATCH",
                                }
                            )
                        elif any(
                            verdict_by_axis.get(axis) is not IdentityAxisVerdict.CONFLICT
                            for axis in enforced_axes_map[mention_id][candidate_id]
                        ):
                            parsed_assessment = parsed_assessment.model_copy(
                                update={
                                    "axis_assessments": [
                                        (
                                            item.model_copy(
                                                update={"verdict": IdentityAxisVerdict.CONFLICT}
                                            )
                                            if item.axis
                                            in enforced_axes_map[mention_id][candidate_id]
                                            else item
                                        )
                                        for item in parsed_assessment.axis_assessments
                                    ]
                                }
                            )
                            normalizations.append(
                                {
                                    "mention_id": mention_id,
                                    "candidate_event_id": candidate_id,
                                    "kind": "ENFORCED_CONFLICT_AXIS_RESTORED",
                                }
                            )
                        if parsed_assessment.relation is AtomicSemanticRelation.SAME_EVENT and any(
                            item.verdict is IdentityAxisVerdict.CONFLICT
                            for item in parsed_assessment.axis_assessments
                        ):
                            parsed_assessment = parsed_assessment.model_copy(
                                update={"relation": AtomicSemanticRelation.RELATED_NOT_SAME}
                            )
                            normalizations.append(
                                {
                                    "mention_id": mention_id,
                                    "candidate_event_id": candidate_id,
                                    "kind": "CONFLICT_AXIS_SAME_RELATION_NORMALIZED",
                                }
                            )
                        valid_assessments.append(parsed_assessment)

                    valid_assessments.sort(
                        key=lambda item: candidate_order.index(item.candidate_event_id)
                    )
                    same_ids = [
                        item.candidate_event_id
                        for item in valid_assessments
                        if item.relation is AtomicSemanticRelation.SAME_EVENT
                    ]
                    related_ids = [
                        item.candidate_event_id
                        for item in valid_assessments
                        if item.relation is AtomicSemanticRelation.RELATED_NOT_SAME
                    ]
                    requested_target = safe_item.get("merge_target_event_id")
                    target = (
                        requested_target
                        if isinstance(requested_target, str) and requested_target in same_ids
                        else (same_ids[0] if same_ids else None)
                    )
                    normalized_decision = AtomicAssignmentDecision(
                        mention_id=mention_id,
                        action=(
                            AtomicAction.MERGE if target is not None else AtomicAction.CREATE_NEW
                        ),
                        merge_target_event_id=target,
                        candidate_assessments=valid_assessments,
                        related_candidate_event_ids=related_ids,
                        possible_duplicate_atomic_ids=[
                            candidate_id for candidate_id in same_ids if candidate_id != target
                        ],
                    )
                    task_diff = task_diffs.get(mention_id)
                    if not isinstance(task_diff, dict):
                        task_diff = {}
                        task_diffs[mention_id] = task_diff
                    task_diff["assessment_recovery"] = {
                        "valid_candidate_ids": [
                            item.candidate_event_id for item in valid_assessments
                        ],
                        "invalid_candidates": invalid_assessments,
                        "final_action": normalized_decision.action.value,
                        "merge_target": normalized_decision.merge_target_event_id,
                    }
                    safe_decisions.append(normalized_decision.model_dump(mode="json"))
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
                    if not set(assessment_by_id).issubset(candidate_ids):
                        raise ValueError("atomic decision assessments contain unknown candidates")
                    for candidate_id, assessment in assessment_by_id.items():
                        if {item.axis for item in assessment.axis_assessments} != expected_axes[
                            decision.mention_id
                        ][candidate_id]:
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
                    raise CrossDocumentPipelineError(stage, "post_validation_invariant") from exc
                return normalized

            def conservative_batch(error: CrossDocumentPipelineError) -> AtomicDecisionBatch:
                adapted = adapt_and_audit_atomic_payload(
                    {"decisions": []},
                    "orchestration_fallback",
                )
                self.registry.append_decision_audit(
                    DecisionAuditRecord(
                        audit_id=f"atomic-n9-batch-degraded:{models.run_id}:{batch_index}",
                        run_id=models.run_id,
                        decision_type="ATOMIC_N9_BATCH_DEGRADED",
                        subject_id=f"batch:{batch_index}",
                        payload={
                            "code": error.code,
                            "stage": error.stage,
                            "temporary_placeholder": "PENDING_ITEM_REPAIR",
                        },
                    )
                )
                return AtomicDecisionBatch.model_validate(adapted)

            escalated = False
            try:
                output = invoke(ModelTier.M2, "atomic_coreference")
            except CrossDocumentPipelineError as exc:
                output = conservative_batch(exc)
                invalid_task_errors.update(
                    {mention_id: f"N9_BATCH_FAILED:{exc.code}" for mention_id in expected}
                )

            if invalid_task_errors:
                task_by_mention = {
                    task["mention"]: task
                    for task in wire_tasks
                    if isinstance(task.get("mention"), str)
                }

                def repair_invalid_task(
                    item: tuple[str, str],
                ) -> tuple[str, AtomicAssignmentDecision | None]:
                    mention_id, error = item
                    task = task_by_mention[mention_id]
                    candidate_refs = task["candidates"]
                    assert isinstance(candidate_refs, list)
                    candidate_ids = {
                        ref["atomic"]
                        for ref in candidate_refs
                        if isinstance(ref, dict) and isinstance(ref.get("atomic"), str)
                    }
                    repair_request = StructuredModelRequest(
                        system_prompt=(
                            _prompt("atomic_coreference.md")
                            + "\nRepair exactly this one invalid task and return it once."
                        ),
                        user_prompt=compact_json(
                            {
                                "validation_error": error,
                                "mentions": {mention_id: model_mentions[mention_id]},
                                "atomics": {
                                    candidate_id: model_atoms[candidate_id]
                                    for candidate_id in sorted(candidate_ids)
                                },
                                "tasks": [task],
                            }
                        ),
                        json_schema=AtomicDecisionBatch.model_json_schema(),
                    )
                    try:
                        result = models._structured(
                            tier=ModelTier.M2,
                            stage="atomic_coreference_item",
                            request=repair_request,
                            repaired=True,
                        )
                        repaired = AtomicDecisionBatch.model_validate(
                            adapt_atomic_payload(result.payload)
                        )
                        if len(repaired.decisions) != 1:
                            raise ValueError("item repair must return exactly one decision")
                        decision = repaired.decisions[0]
                        if decision.mention_id != mention_id:
                            raise ValueError("item repair returned the wrong mention")
                        if {
                            assessment.candidate_event_id
                            for assessment in decision.candidate_assessments
                        } != expected[mention_id]:
                            raise ValueError("item repair candidate coverage mismatch")
                        for assessment in decision.candidate_assessments:
                            if {axis.axis for axis in assessment.axis_assessments} != expected_axes[
                                mention_id
                            ][assessment.candidate_event_id]:
                                raise ValueError("item repair identity axis coverage mismatch")
                        if decision.action is AtomicAction.MERGE:
                            target = decision.merge_target_event_id
                            by_id = {
                                assessment.candidate_event_id: assessment
                                for assessment in decision.candidate_assessments
                            }
                            if (
                                target not in by_id
                                or by_id[target].relation is not AtomicSemanticRelation.SAME_EVENT
                            ):
                                raise ValueError("item repair merge target is not SAME_EVENT")
                        status = "SUCCEEDED"
                        error_code = None
                    except (CrossDocumentPipelineError, ValidationError, ValueError) as exc:
                        decision = None
                        status = "FAILED_ISOLATED"
                        error_code = (
                            exc.code
                            if isinstance(exc, CrossDocumentPipelineError)
                            else type(exc).__name__
                        )
                    self.registry.append_decision_audit(
                        DecisionAuditRecord(
                            audit_id=stable_id(
                                "atomic-n9-item-repair",
                                {
                                    "run": models.run_id,
                                    "batch": batch_index,
                                    "mention": mention_id,
                                },
                            ),
                            run_id=models.run_id,
                            decision_type="ATOMIC_N9_ITEM_REPAIR",
                            subject_id=f"batch:{batch_index}:{mention_id}",
                            payload={
                                "status": status,
                                "validation_error": error,
                                "error_code": error_code,
                            },
                        )
                    )
                    return mention_id, decision

                repair_items = sorted(invalid_task_errors.items())
                with ThreadPoolExecutor(max_workers=min(8, len(repair_items))) as repair_pool:
                    repaired_by_mention = dict(repair_pool.map(repair_invalid_task, repair_items))
                retained_decisions: list[AtomicAssignmentDecision] = []
                for decision in output.decisions:
                    repaired = repaired_by_mention.get(decision.mention_id, decision)
                    if repaired is not None:
                        retained_decisions.append(repaired)
                output = AtomicDecisionBatch(decisions=retained_decisions)
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
                try:
                    output = invoke(ModelTier.M3, "atomic_coreference_escalation")
                except CrossDocumentPipelineError as exc:
                    self.registry.append_decision_audit(
                        DecisionAuditRecord(
                            audit_id=(
                                f"atomic-n9-escalation-degraded:{models.run_id}:{batch_index}"
                            ),
                            run_id=models.run_id,
                            decision_type="ATOMIC_N9_ESCALATION_DEGRADED",
                            subject_id=f"batch:{batch_index}",
                            payload={
                                "code": exc.code,
                                "fallback": "RETAIN_M2_THEN_CREATE_NEW_FOR_UNCERTAIN",
                            },
                        )
                    )
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
                                and not any(
                                    assessment.relation is AtomicSemanticRelation.SAME_EVENT
                                    for assessment in decision.candidate_assessments
                                )
                                else decision
                            )
                            for decision in output.decisions
                        ]
                    }
                )
            restored_output = restore_persistent_ids(output)
            merged_decisions: list[AtomicAssignmentDecision] = []
            for decision in restored_output.decisions:
                deterministic = deterministic_by_mention.get(decision.mention_id, [])
                if not deterministic:
                    merged_decisions.append(decision)
                    continue
                assessments = [*decision.candidate_assessments, *deterministic]
                candidate_order = [item.event.event_id for item in candidates[decision.mention_id]]
                assessments.sort(key=lambda item: candidate_order.index(item.candidate_event_id))
                related = [
                    item.candidate_event_id
                    for item in assessments
                    if item.relation is AtomicSemanticRelation.RELATED_NOT_SAME
                ]
                merged_decisions.append(
                    decision.model_copy(
                        update={
                            "candidate_assessments": assessments,
                            "related_candidate_event_ids": related,
                        }
                    )
                )
            return batch_index, restored_output.model_copy(update={"decisions": merged_decisions})

        if not batches:
            return decisions
        with ThreadPoolExecutor(max_workers=min(self.n9_active_requests, len(batches))) as executor:
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
        sync_embeddings: bool = True,
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
                    "N9_UNJUDGEABLE_FAILED_SINGLETON",
                )
                self.registry.append_decision_audit(
                    DecisionAuditRecord(
                        audit_id=f"n9-missing-decision:{run_id}:{mention.mention_id}",
                        run_id=run_id,
                        decision_type="ATOMIC_ASSIGNMENT_DEGRADED",
                        subject_id=mention.mention_id,
                        payload={
                            "reason": reason,
                            "action": "RETAIN_AS_SINGLETON_WITHOUT_SEMANTIC_VERDICT",
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

            selected_merge_target = (
                decision.merge_target_event_id
                if decision is not None and action is AtomicAction.MERGE
                else None
            )
            eligible_duplicate_short_ids: list[str] | None = None
            if decision is not None and action is AtomicAction.MERGE:
                incoming_sidecar = compiled_identity.atomic_identity_sidecar
                assert incoming_sidecar is not None
                assessment_by_id = {
                    item.candidate_event_id: item for item in decision.candidate_assessments
                }
                candidate_by_id = {
                    item.event.event_id: item for item in candidates[mention.mention_id]
                }
                same_candidate_ids = [
                    item.event.event_id
                    for item in candidates[mention.mention_id]
                    if (
                        item.event.event_id in assessment_by_id
                        and assessment_by_id[item.event.event_id].relation
                        is AtomicSemanticRelation.SAME_EVENT
                    )
                ]
                ordered_same_ids = [
                    candidate_id
                    for candidate_id in [
                        selected_merge_target,
                        *same_candidate_ids,
                    ]
                    if candidate_id is not None
                ]
                ordered_same_ids = list(dict.fromkeys(ordered_same_ids))
                invariant_evaluations = []
                for candidate_id in ordered_same_ids:
                    candidate = candidate_by_id[candidate_id]
                    candidate_sidecar = candidate.identity_sidecar
                    assert candidate_sidecar is not None
                    invariant = evaluate_atomic_merge_invariant(
                        incoming_sidecar,
                        candidate_sidecar,
                        enforced_rules=self.atomic_enforced_rules,
                    )
                    self.registry.append_decision_audit(
                        DecisionAuditRecord(
                            audit_id=(
                                f"atomic-merge-invariant:{run_id}:"
                                f"{mention.mention_id}:{candidate_id}"
                            ),
                            run_id=run_id,
                            decision_type="ATOMIC_MERGE_INVARIANT",
                            subject_id=mention.mention_id,
                            payload={
                                "candidate_event_id": candidate_id,
                                "selected_by_n9": candidate_id == selected_merge_target,
                                "evaluation": invariant.model_dump(mode="json"),
                                "legacy_hard_cannot_link_mode": (self.hard_cannot_link_mode.value),
                            },
                        )
                    )
                    predicted_member_count = len(candidate.event.mention_ids) + 1
                    if invariant.triggered_rules or predicted_member_count >= 8:
                        self.registry.append_decision_audit(
                            DecisionAuditRecord(
                                audit_id=(
                                    f"atomic-supercluster-guard:{run_id}:"
                                    f"{mention.mention_id}:{candidate_id}"
                                ),
                                run_id=run_id,
                                decision_type="ATOMIC_SUPERCLUSTER_GUARD",
                                subject_id=candidate_id,
                                payload={
                                    "mode": "shadow",
                                    "incoming_mention_id": mention.mention_id,
                                    "predicted_member_count": predicted_member_count,
                                    "triggered_rules": [
                                        rule.value for rule in invariant.triggered_rules
                                    ],
                                    "would_lock": (
                                        invariant.result is AtomicMergeInvariantResult.LOCKED_OUT
                                    ),
                                    "shadow_would_lock": bool(invariant.triggered_rules),
                                },
                            )
                        )
                    invariant_evaluations.append((candidate_id, invariant))
                unlocked_candidate = first_unlocked_atomic_candidate(invariant_evaluations)
                if unlocked_candidate is not None:
                    selected_merge_target = unlocked_candidate
                    eligible_duplicate_short_ids = [
                        candidate_id
                        for candidate_id, evaluation in invariant_evaluations
                        if candidate_id != selected_merge_target
                        and evaluation.result is not AtomicMergeInvariantResult.LOCKED_OUT
                    ]
                    if selected_merge_target != decision.merge_target_event_id:
                        reason = "MERGE_INVARIANT_ALTERNATE_TARGET"
                else:
                    action = AtomicAction.CREATE_NEW
                    selected_merge_target = None
                    reason = "MERGE_INVARIANT_ALL_SAME_LOCKED_CREATE_NEW"

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
                    materialize(value)
                    for value in (
                        eligible_duplicate_short_ids
                        if eligible_duplicate_short_ids is not None
                        else decision.possible_duplicate_atomic_ids
                    )
                ]
                if selected_merge_target is not None:
                    candidate_event_id = materialize(selected_merge_target)
                    selected = assessment_by_id[selected_merge_target]
                    relation = selected.relation
                    claim_conflict = selected.claim_conflict
                    identity_differences = selected.identity_differences

            if action is AtomicAction.MERGE and candidate_event_id is not None:
                target = touched.get(candidate_event_id) or self.registry.get_current_atomic_event(
                    candidate_event_id
                )
                if target is None:
                    raise CrossDocumentPipelineError("atomic_update", "candidate_missing")
                try:
                    event = add_mention_to_atomic(
                        target,
                        mention,
                        known_mentions=list(known_mentions.values()),
                        claim_conflict=claim_conflict,
                        identity_differences=identity_differences,
                        incoming_profile=profile,
                    )
                except ValidationError as exc:
                    self.registry.append_decision_audit(
                        DecisionAuditRecord(
                            audit_id=stable_id(
                                "audit",
                                {
                                    "run": run_id,
                                    "mention": mention.mention_id,
                                    "target": candidate_event_id,
                                    "reason": "N9_MERGE_APPLY_INVALID_SINGLETON",
                                },
                            ),
                            run_id=run_id,
                            decision_type="ATOMIC_APPLY_DEGRADED",
                            subject_id=mention.mention_id,
                            payload={
                                "reason": "N9_MERGE_APPLY_INVALID_SINGLETON",
                                "attempted_target_event_id": candidate_event_id,
                                "validation_errors": exc.errors(include_url=False),
                                "action": "CREATE_NEW_SINGLETON",
                            },
                        )
                    )
                    event = singleton_atomic_event(mention, identity_profile=profile)
                    action = AtomicAction.CREATE_NEW
                    reason = "N9_MERGE_APPLY_INVALID_SINGLETON"
                    candidate_event_id = None
                    relation = None
                    claim_conflict = False
                    identity_differences = []
                    possible_duplicate_atomic_ids = []
                self.registry.save_atomic_event(event)
                retained_possible_duplicates: list[str] = []
                absorbed_duplicates: list[str] = []
                for duplicate_event_id in (
                    possible_duplicate_atomic_ids if action is AtomicAction.MERGE else []
                ):
                    duplicate_root = self.registry.resolve_atomic_event_root(duplicate_event_id)
                    target_root = self.registry.resolve_atomic_event_root(event.event_id)
                    if duplicate_root == target_root:
                        continue
                    duplicate_event = touched.get(
                        duplicate_root
                    ) or self.registry.get_current_atomic_event(duplicate_root)
                    if duplicate_event is None or len(duplicate_event.mention_ids) != 1:
                        retained_possible_duplicates.append(duplicate_event_id)
                        continue
                    duplicate_memberships = self.registry.list_packages_for_event(duplicate_root)
                    target_memberships = self.registry.list_packages_for_event(target_root)
                    if duplicate_memberships and (
                        not target_memberships
                        or self.registry.resolve_package_root(duplicate_memberships[0].package_id)
                        != self.registry.resolve_package_root(target_memberships[0].package_id)
                    ):
                        retained_possible_duplicates.append(duplicate_event_id)
                        continue
                    duplicate_mention_id = duplicate_event.mention_ids[0]
                    duplicate_mention = known_mentions.get(
                        duplicate_mention_id
                    ) or self.registry.get_mention(duplicate_mention_id)
                    if duplicate_mention is None:
                        retained_possible_duplicates.append(duplicate_event_id)
                        continue
                    try:
                        event = add_mention_to_atomic(
                            event,
                            duplicate_mention,
                            known_mentions=[
                                *known_mentions.values(),
                                duplicate_mention,
                            ],
                            claim_conflict=False,
                            identity_differences=[],
                            incoming_profile=duplicate_event.identity_profile,
                        )
                    except ValidationError as exc:
                        retained_possible_duplicates.append(duplicate_event_id)
                        self.registry.append_decision_audit(
                            DecisionAuditRecord(
                                audit_id=stable_id(
                                    "audit",
                                    {
                                        "run": run_id,
                                        "mention": mention.mention_id,
                                        "duplicate": duplicate_root,
                                        "reason": "N9_SINGLETON_ABSORPTION_INVALID_RETAINED",
                                    },
                                ),
                                run_id=run_id,
                                decision_type="ATOMIC_APPLY_DEGRADED",
                                subject_id=duplicate_root,
                                payload={
                                    "reason": "N9_SINGLETON_ABSORPTION_INVALID_RETAINED",
                                    "target_event_id": event.event_id,
                                    "validation_errors": exc.errors(include_url=False),
                                    "action": "RETAIN_POSSIBLE_DUPLICATE",
                                },
                            )
                        )
                        continue
                    self.registry.save_atomic_event(event)
                    self.registry.save_atomic_redirect(
                        source_event_id=duplicate_root,
                        target_event_id=event.event_id,
                        run_id=run_id,
                        reason="N9_SAME_SINGLETON_ABSORPTION",
                    )
                    absorbed_duplicates.append(duplicate_root)
                possible_duplicate_atomic_ids = retained_possible_duplicates
                if absorbed_duplicates:
                    self.registry.append_decision_audit(
                        DecisionAuditRecord(
                            audit_id=stable_id(
                                "audit",
                                {
                                    "run": run_id,
                                    "mention": mention.mention_id,
                                    "target": event.event_id,
                                    "absorbed": sorted(absorbed_duplicates),
                                },
                            ),
                            run_id=run_id,
                            decision_type="ATOMIC_SINGLETON_ABSORPTION",
                            subject_id=event.event_id,
                            payload={
                                "incoming_mention_id": mention.mention_id,
                                "target_event_id": event.event_id,
                                "absorbed_event_ids": sorted(absorbed_duplicates),
                                "constraints": {
                                    "duplicate_member_count": 1,
                                    "sidecar_allowlist_passed": True,
                                    "package_membership_compatible": True,
                                },
                            },
                        )
                    )
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
        if sync_embeddings:
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
        members: dict[str, AtomicEvent] = {}
        for event_id in package.member_event_ids:
            event = self.registry.get_current_atomic_event(event_id)
            if event is not None:
                members[event.event_id] = event
        return [members[event_id] for event_id in sorted(members)]

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

    def _event_mentions(self, event: AtomicEvent) -> list[EventMention]:
        return [
            mention
            for mention_id in event.mention_ids
            if (mention := self.registry.get_mention(mention_id)) is not None
        ]

    def _event_shares_source(
        self,
        event: AtomicEvent,
        members: Sequence[AtomicEvent],
    ) -> bool:
        message_ids = {mention.message_id for mention in self._event_mentions(event)}
        return any(
            mention.message_id in message_ids
            for member in members
            for mention in self._event_mentions(member)
        )

    @staticmethod
    def _mention_originators(mention: EventMention) -> set[str]:
        values = {
            participant.surface.strip().casefold()
            for participant in mention.participants
            if participant.role in {ParticipantRole.ACTOR, ParticipantRole.SUBJECT}
            and participant.surface.strip()
        }
        if not values and mention.source_claim and mention.source_claim.strip():
            values.add(mention.source_claim.strip().casefold())
        return values

    def _events_share_parent_context(
        self,
        left: AtomicEvent,
        right: AtomicEvent,
    ) -> bool:
        """Conservative document-local parent block signal, never a merge verdict."""

        blocked = {EventFamily.MARKET_MOVEMENT, EventFamily.ANALYST_ACTION}
        if left.event_family in blocked or right.event_family in blocked:
            return False
        for left_mention in self._event_mentions(left):
            for right_mention in self._event_mentions(right):
                if left_mention.message_id != right_mention.message_id:
                    continue
                if not self._mention_originators(left_mention).intersection(
                    self._mention_originators(right_mention)
                ):
                    continue
                for left_span in left_mention.evidence_spans:
                    for right_span in right_mention.evidence_spans:
                        if left_span.field != right_span.field:
                            continue
                        gap = max(
                            0,
                            max(left_span.start_char, right_span.start_char)
                            - min(left_span.end_char, right_span.end_char),
                        )
                        if gap <= 150:
                            return True
        return False

    def _package_candidates_v13(
        self,
        event: AtomicEvent,
        seed: PackageSeed,
        *,
        active_packages: dict[str, EventPackage],
        package_embeddings: dict[str, list[float]],
        atomic_embeddings: dict[str, list[float]],
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
                    RecallRoute.INCUMBENT_MEMBERSHIP.value
                )
        incoming_identity = _hash_json(event.identity_profile.model_dump(mode="json"))
        for package in active_packages.values():
            members = self._package_member_events(package)
            if any(
                _hash_json(member.identity_profile.model_dump(mode="json")) == incoming_identity
                for member in members
            ):
                recalled.setdefault(package.package_id, set()).add(
                    RecallRoute.MEMBER_IDENTITY.value
                )
            if self._event_shares_source(event, members):
                recalled.setdefault(package.package_id, set()).add(
                    RecallRoute.SAME_SOURCE_MEMBER.value
                )
            if any(self._events_share_parent_context(event, member) for member in members):
                recalled.setdefault(package.package_id, set()).add(RecallRoute.PARENT_CONTEXT.value)
        similarities: dict[str, float] = {}
        member_similarities: dict[str, float] = {}
        if atomic_vector is not None:
            for package_id, vector in package_embeddings.items():
                similarity = _cosine(atomic_vector, vector)
                similarities[package_id] = similarity
                if similarity >= EMBEDDING_RECALL_THRESHOLD:
                    recalled.setdefault(package_id, set()).add(
                        RecallRoute.PROPOSITION_EMBEDDING.value
                    )
            for package_id, package in active_packages.items():
                member_similarity = max(
                    (
                        _cosine(atomic_vector, member_vector)
                        for member_id in package.member_event_ids
                        if (member_vector := atomic_embeddings.get(member_id)) is not None
                    ),
                    default=-1.0,
                )
                if member_similarity >= -1.0:
                    member_similarities[package_id] = member_similarity
                if member_similarity >= EMBEDDING_RECALL_THRESHOLD:
                    recalled.setdefault(package_id, set()).add(RecallRoute.MEMBER_EMBEDDING.value)
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
            candidate_member_similarity = member_similarities.get(candidate_package.package_id)
            effective_similarity: float | None = (
                max(
                    value
                    for value in (candidate_similarity, candidate_member_similarity)
                    if value is not None
                )
                if candidate_similarity is not None or candidate_member_similarity is not None
                else None
            )
            ranked.append(
                PackageCandidate(
                    package=candidate_package,
                    recall_routes=routes,
                    recall_score=_package_recall_score(routes, effective_similarity),
                    embedding_similarity=candidate_similarity,
                    member_embedding_similarity=candidate_member_similarity,
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
        selected: list[PackageCandidate] = []

        def take(values: Sequence[PackageCandidate], limit: int) -> None:
            added = 0
            for value in values:
                if value not in selected:
                    selected.append(value)
                    added += 1
                if added >= limit:
                    break

        take(
            [item for item in ranked if RecallRoute.INCUMBENT_MEMBERSHIP in item.recall_routes],
            1,
        )
        take(
            [
                item
                for item in ranked
                if set(item.recall_routes).intersection(
                    {
                        RecallRoute.CANONICAL_ARTIFACT,
                        RecallRoute.PACKAGE_ANCHOR,
                        RecallRoute.PARENT_CONTEXT,
                    }
                )
            ],
            2,
        )
        take(
            [item for item in ranked if RecallRoute.SAME_SOURCE_MEMBER in item.recall_routes],
            1,
        )
        take(
            [
                item
                for item in ranked
                if set(item.recall_routes).intersection(
                    {
                        RecallRoute.MEMBER_IDENTITY,
                        RecallRoute.MEMBER_EMBEDDING,
                        RecallRoute.PROPOSITION_EMBEDDING,
                    }
                )
            ],
            2,
        )
        for item in ranked:
            if len(selected) >= PACKAGE_TOP_K:
                break
            if item not in selected:
                selected.append(item)
        result = selected[:PACKAGE_TOP_K]
        candidate_payload = {
            "limit": PACKAGE_TOP_K,
            "candidates": [
                {
                    "package_id": item.package.package_id,
                    "routes": [route.value for route in item.recall_routes],
                    "container_similarity": item.embedding_similarity,
                    "member_similarity": item.member_embedding_similarity,
                }
                for item in result
            ],
        }
        self.registry.append_decision_audit(
            DecisionAuditRecord(
                audit_id=stable_id(
                    "audit",
                    {
                        "run": run_id,
                        "event": event.event_id,
                        "type": "package_candidate_slots",
                        "payload": _hash_json(candidate_payload),
                    },
                ),
                run_id=run_id,
                decision_type="PACKAGE_CANDIDATE_SLOTS",
                subject_id=event.event_id,
                payload=candidate_payload,
            )
        )
        candidate_counts["package_recalled"] += len(result)
        if self.package_conflict_mode is PackageConflictMode.ENFORCE:
            candidate_counts["package_hard_blocked"] += sum(
                bool(item.hard_conflicts) for item in result
            )
        return result

    def _n12_boundary_conflicts(
        self,
        event: AtomicEvent,
        seed: PackageSeed,
        candidate: PackageCandidate,
    ) -> list[str]:
        """Expose only high-confidence, compact N12 non-membership evidence."""

        conflicts = {
            conflict.value
            for conflict in package_hard_conflicts(seed, candidate.package)
            if conflict
            in {
                HardConflictCode.PACKAGE_KIND,
                HardConflictCode.PACKAGE_FAMILY,
                HardConflictCode.PACKAGE_ARTIFACT,
                HardConflictCode.PACKAGE_PERIOD,
            }
        }
        members = canonical_package_members(self.registry, candidate.package)
        if members and event.event_family is EventFamily.MARKET_MOVEMENT:
            if all(member.event_family is not EventFamily.MARKET_MOVEMENT for member in members):
                conflicts.add("REACTION_FAMILY")
        elif members and all(
            member.event_family is EventFamily.MARKET_MOVEMENT for member in members
        ):
            conflicts.add("REACTION_FAMILY")

        incoming_profile = event.identity_profile
        if isinstance(incoming_profile, AnalystActionIdentityProfile):
            analyst_profiles = [
                member.identity_profile
                for member in members
                if isinstance(member.identity_profile, AnalystActionIdentityProfile)
            ]
            if analyst_profiles:
                incoming_fields = incoming_profile.fields
                institutions = {profile.fields.institution_id for profile in analyst_profiles}
                if incoming_fields.institution_id not in institutions:
                    conflicts.add(HardConflictCode.ANALYST_INSTITUTION.value)
                report_ids = {
                    profile.fields.report_id
                    for profile in analyst_profiles
                    if profile.fields.report_id
                }
                if (
                    incoming_fields.report_id
                    and report_ids
                    and incoming_fields.report_id not in report_ids
                ):
                    conflicts.add(HardConflictCode.REPORT_IDENTITY.value)
        return sorted(conflicts)

    def _joint_package_decisions(
        self,
        *,
        unresolved: dict[str, list[PackageCandidate]],
        events: dict[str, AtomicEvent],
        seeds: dict[str, PackageSeed],
        models: _AuditedModels,
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
            package_ids = sorted(
                {
                    candidate.package.package_id
                    for event_id in event_ids
                    for candidate in unresolved[event_id]
                }
            )
            package_short_by_full = {
                package_id: f"p{index}" for index, package_id in enumerate(package_ids, start=1)
            }
            package_full_by_short = {
                short_id: package_id for package_id, short_id in package_short_by_full.items()
            }
            expected = {
                event_short_by_full[event_id]: {
                    package_short_by_full[candidate.package.package_id]
                    for candidate in unresolved[event_id]
                }
                for event_id in event_ids
            }
            candidate_by_package_id = {
                candidate.package.package_id: candidate
                for event_id in event_ids
                for candidate in unresolved[event_id]
            }
            request_events = {
                event.event_id: event for event_id in event_ids for event in [events[event_id]]
            }
            for candidate in candidate_by_package_id.values():
                for member_id in candidate.package.member_event_ids:
                    member = self.registry.get_current_atomic_event(member_id)
                    if member is not None:
                        request_events[member.event_id] = member
            source_short_ids = request_local_source_ids(
                self.registry, list(request_events.values())
            )
            wire_packages: dict[str, object] = {}
            for package_id, candidate in candidate_by_package_id.items():
                short_package_id = package_short_by_full[package_id]
                related_event_ids = [
                    event_id
                    for event_id in event_ids
                    if any(item.package.package_id == package_id for item in unresolved[event_id])
                ]
                preferred_anchor_ids = sorted(
                    {
                        anchor_id
                        for event_id in related_event_ids
                        for anchor_id in seeds[event_id].package_anchor_ids
                    }
                )
                wire_packages[short_package_id] = build_n12_package_card(
                    self.registry,
                    candidate.package,
                    source_short_ids=source_short_ids,
                    preferred_anchor_ids=preferred_anchor_ids,
                    preferred_events=[events[event_id] for event_id in related_event_ids],
                )
            wire_events: dict[str, object] = {}
            for event_id in event_ids:
                short_event_id = event_short_by_full[event_id]
                wire_events[short_event_id] = build_n12_event_card(
                    self.registry,
                    events[event_id],
                    source_short_ids=source_short_ids,
                )
            wire_tasks: list[dict[str, object]] = []
            for event_id in event_ids:
                wire_candidate_refs: list[dict[str, object]] = []
                for candidate in unresolved[event_id]:
                    wire_candidate_ref: dict[str, object] = {
                        "package_id": package_short_by_full[candidate.package.package_id],
                        "routes": [route.value for route in candidate.recall_routes],
                    }
                    if candidate.embedding_similarity is not None:
                        wire_candidate_ref["similarity"] = candidate.embedding_similarity
                    if candidate.member_embedding_similarity is not None:
                        wire_candidate_ref["member_similarity"] = (
                            candidate.member_embedding_similarity
                        )
                    boundary_conflicts = self._n12_boundary_conflicts(
                        events[event_id],
                        seeds[event_id],
                        candidate,
                    )
                    if boundary_conflicts:
                        wire_candidate_ref["boundary_conflicts"] = boundary_conflicts
                    wire_candidate_refs.append(wire_candidate_ref)
                short_event_id = event_short_by_full[event_id]
                wire_tasks.append(
                    {
                        "event_id": short_event_id,
                        "seed": seeds[event_id].model_dump(
                            mode="json", exclude={"local_anchor_hints"}
                        ),
                        "candidates": wire_candidate_refs,
                    }
                )
            wire_payload: dict[str, object] = {
                "events": wire_events,
                "packages": wire_packages,
                "tasks": wire_tasks,
            }
            request = StructuredModelRequest(
                system_prompt=_prompt("package_assignment.md"),
                user_prompt=compact_json(wire_payload),
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

            invalid_task_errors: dict[str, str] = {}

            def adapt_and_audit(payload: object, attempt: str) -> object:
                adapted = adapt(payload)
                if not isinstance(adapted, dict) or not isinstance(adapted.get("decisions"), list):
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
                                relation != PackageAssignmentRelation.EXTERNAL_RELATED.value
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

                raw_decisions = [item for item in adapted["decisions"] if isinstance(item, dict)]
                returned_event_ids = [
                    item.get("event_id")
                    for item in raw_decisions
                    if isinstance(item.get("event_id"), str)
                ]
                decision_by_event = {
                    item["event_id"]: item
                    for item in raw_decisions
                    if isinstance(item.get("event_id"), str) and item["event_id"] in expected
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
                                item.candidate_package_id for item in parsed.candidate_assessments
                            }
                            if actual != expected[event_id]:
                                raise ValueError("candidate coverage mismatch")
                        except (ValidationError, ValueError):
                            fallback_reason = "N12_INVALID_TASK"
                    if fallback_reason is not None:
                        if attempt == "initial":
                            invalid_task_errors[event_id] = fallback_reason
                        raw = {
                            "event_id": event_id,
                            "candidate_assessments": [],
                            "ranked_member_package_ids": [],
                            "selected_member_package_id": None,
                            "selection_reason": None,
                        }
                        normalizations.append(
                            {
                                "event_id": event_id,
                                "kind": "TASK_DEGRADED_TO_NEUTRAL_OMISSION",
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
                    if not actual.issubset(expected[decision.event_id]):
                        raise ValueError("package decisions must not contain unknown candidates")

            try:
                output = models.typed(
                    tier=tier,
                    stage="package_assignment",
                    request=request,
                    output_type=PackageDecisionBatch,
                    validator=validate,
                    attempt_payload_adapter=adapt_and_audit,
                )
            except CrossDocumentPipelineError as exc:
                invalid_task_errors.update(
                    {event_id: f"N12_BATCH_FAILED:{exc.code}" for event_id in expected}
                )
                output = PackageDecisionBatch.model_validate(
                    adapt_and_audit(
                        {"decisions": []},
                        "orchestration_fallback",
                    )
                )
                self.registry.append_decision_audit(
                    DecisionAuditRecord(
                        audit_id=f"package-n12-batch-degraded:{models.run_id}:{batch_index}",
                        run_id=models.run_id,
                        decision_type="PACKAGE_N12_BATCH_DEGRADED",
                        subject_id=f"batch:{batch_index}",
                        payload={
                            "code": exc.code,
                            "temporary_placeholder": "PENDING_ITEM_REPAIR",
                        },
                    )
                )

            if invalid_task_errors:
                task_by_event = {
                    task["event_id"]: task
                    for task in wire_tasks
                    if isinstance(task.get("event_id"), str)
                }

                def repair_invalid_task(
                    item: tuple[str, str],
                ) -> tuple[str, PackageAssignmentDecision | None]:
                    short_event_id, error = item
                    task = task_by_event[short_event_id]
                    candidate_refs = task["candidates"]
                    assert isinstance(candidate_refs, list)
                    candidate_ids = {
                        ref["package_id"]
                        for ref in candidate_refs
                        if isinstance(ref, dict) and isinstance(ref.get("package_id"), str)
                    }
                    compact_repair_payload = {
                        "validation_error": error,
                        "events": {short_event_id: wire_events[short_event_id]},
                        "packages": {
                            package_id: wire_packages[package_id]
                            for package_id in sorted(candidate_ids)
                        },
                        "tasks": [task],
                    }
                    repair_request = StructuredModelRequest(
                        system_prompt=(
                            _prompt("package_assignment.md")
                            + "\nRepair exactly this one invalid task and return it once."
                        ),
                        user_prompt=compact_json(compact_repair_payload),
                        json_schema=PackageDecisionBatch.model_json_schema(),
                    )
                    audit_payload: dict[str, object]
                    try:
                        result = models._structured(
                            tier=tier,
                            stage="package_assignment_item",
                            request=repair_request,
                            repaired=True,
                        )
                        repaired_batch = PackageDecisionBatch.model_validate(adapt(result.payload))
                        if len(repaired_batch.decisions) != 1:
                            raise ValueError("item repair must return exactly one decision")
                        decision = repaired_batch.decisions[0]
                        if decision.event_id != short_event_id:
                            raise ValueError("item repair returned the wrong event")
                        actual = {
                            assessment.candidate_package_id
                            for assessment in decision.candidate_assessments
                        }
                        if actual != expected[short_event_id]:
                            raise ValueError("item repair candidate coverage mismatch")
                        audit_payload = {
                            "status": "SUCCEEDED",
                            "validation_error": error,
                            "model_call_id": models.current_model_call_id(),
                        }
                    except (CrossDocumentPipelineError, ValidationError, ValueError) as exc:
                        decision = None
                        audit_payload = {
                            "status": "FAILED_ISOLATED",
                            "validation_error": error,
                            "error": (
                                exc.code
                                if isinstance(exc, CrossDocumentPipelineError)
                                else type(exc).__name__
                            ),
                        }
                    self.registry.append_decision_audit(
                        DecisionAuditRecord(
                            audit_id=stable_id(
                                "package-n12-item-repair",
                                {
                                    "run": models.run_id,
                                    "batch": batch_index,
                                    "event": short_event_id,
                                },
                            ),
                            run_id=models.run_id,
                            decision_type="PACKAGE_N12_ITEM_REPAIR",
                            subject_id=f"batch:{batch_index}:{short_event_id}",
                            payload=audit_payload,
                        )
                    )
                    return short_event_id, decision

                repair_items = sorted(invalid_task_errors.items())
                with ThreadPoolExecutor(max_workers=min(2, len(repair_items))) as repair_pool:
                    repaired_by_event = dict(repair_pool.map(repair_invalid_task, repair_items))
                retained_decisions: list[PackageAssignmentDecision] = []
                for decision in output.decisions:
                    repaired = repaired_by_event.get(decision.event_id, decision)
                    if repaired is not None:
                        retained_decisions.append(repaired)
                output = PackageDecisionBatch(decisions=retained_decisions)
            restored: list[PackageAssignmentDecision] = []
            for decision in output.decisions:
                full_event_id = event_full_by_short[decision.event_id]
                restored.append(
                    decision.model_copy(
                        update={
                            "event_id": full_event_id,
                            "candidate_assessments": [
                                assessment.model_copy(
                                    update={
                                        "candidate_package_id": package_full_by_short[
                                            assessment.candidate_package_id
                                        ]
                                    }
                                )
                                for assessment in decision.candidate_assessments
                            ],
                            "ranked_member_package_ids": [
                                package_full_by_short[value]
                                for value in decision.ranked_member_package_ids
                            ],
                            "selected_member_package_id": (
                                None
                                if decision.selected_member_package_id is None
                                else package_full_by_short[decision.selected_member_package_id]
                            ),
                        }
                    )
                )
            return batch_index, PackageDecisionBatch(decisions=restored)

        with ThreadPoolExecutor(
            max_workers=min(self.n12_active_requests, len(batches))
        ) as executor:
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
                atomic_embeddings=atomic_embeddings,
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
        parent_group_by_event: dict[str, str] = {}
        for event in sorted(events, key=lambda item: item.event_id):
            compatible = [
                other
                for other in events
                if other.event_id < event.event_id
                and seeds[other.event_id].package_kind is seeds[event.event_id].package_kind
                and seeds[other.event_id].package_family is seeds[event.event_id].package_family
                and self._events_share_parent_context(event, other)
            ]
            if compatible:
                parent_group_by_event[event.event_id] = parent_group_by_event.get(
                    compatible[0].event_id, compatible[0].event_id
                )
            else:
                parent_group_by_event[event.event_id] = event.event_id
        parent_context_id_by_group = {
            group_id: stable_id(
                "parent-context",
                {
                    "group": group_id,
                    "messages": sorted(
                        {
                            mention.message_id
                            for grouped_event in events
                            if parent_group_by_event[grouped_event.event_id] == group_id
                            for mention in self._event_mentions(grouped_event)
                        }
                    ),
                },
            )
            for group_id in set(parent_group_by_event.values())
        }
        provisional_package_by_group: dict[str, EventPackage] = {}
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
            if previous is not None and selected_id != previous.package_id:
                previous_candidate = next(
                    (
                        candidate
                        for candidate in candidates[event.event_id]
                        if candidate.package.package_id == previous.package_id
                    ),
                    None,
                )
                selected_candidate = next(
                    (
                        candidate
                        for candidate in candidates[event.event_id]
                        if candidate.package.package_id == selected_id
                    ),
                    None,
                )
                old_parent_strength = (
                    len(
                        set(previous_candidate.recall_routes).intersection(
                            {
                                RecallRoute.CANONICAL_ARTIFACT,
                                RecallRoute.PACKAGE_ANCHOR,
                                RecallRoute.PARENT_CONTEXT,
                            }
                        )
                    )
                    if previous_candidate is not None
                    else 0
                )
                new_parent_strength = (
                    len(
                        set(selected_candidate.recall_routes).intersection(
                            {
                                RecallRoute.CANONICAL_ARTIFACT,
                                RecallRoute.PACKAGE_ANCHOR,
                                RecallRoute.PARENT_CONTEXT,
                            }
                        )
                    )
                    if selected_candidate is not None
                    else 0
                )
                previous_external = any(
                    assessment.candidate_package_id == previous.package_id
                    and assessment.relation is PackageAssignmentRelation.EXTERNAL_RELATED
                    for assessment in assessments
                )
                trusted_parent = bool(
                    selected_candidate is not None
                    and seed.anchor_artifact_id
                    and seed.anchor_artifact_id
                    in self._trusted_package_anchor_ids(selected_candidate.package)
                )
                move_allowed = bool(
                    (previous_candidate and previous_candidate.hard_conflicts)
                    or new_parent_strength > old_parent_strength
                    or trusted_parent
                    or previous_external
                )
                if not move_allowed:
                    requested_id = selected_id
                    selected_id = previous.package_id
                    self.registry.append_decision_audit(
                        DecisionAuditRecord(
                            audit_id=stable_id(
                                "audit",
                                {
                                    "run": run_id,
                                    "event": event.event_id,
                                    "type": "keep_incumbent",
                                },
                            ),
                            run_id=run_id,
                            decision_type="PACKAGE_KEEP_INCUMBENT",
                            subject_id=event.event_id,
                            payload={
                                "incumbent_package_id": previous.package_id,
                                "requested_package_id": requested_id,
                                "old_parent_strength": old_parent_strength,
                                "new_parent_strength": new_parent_strength,
                                "reason": "MOVE_REQUIRES_STRONGER_PARENT_OR_BOUNDARY_EVIDENCE",
                            },
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
                parent_group = parent_group_by_event[event.event_id]
                provisional = provisional_package_by_group.get(parent_group)
                safe_grouping = bool(
                    previous is None
                    and not any(
                        assessment.relation is PackageAssignmentRelation.EXTERNAL_RELATED
                        for assessment in assessments
                    )
                )
                if provisional is not None and safe_grouping:
                    package = compiler.compile(
                        provisional,
                        [*self._package_member_events(provisional), event],
                        seed=seed,
                    )
                    if package.version != provisional.version:
                        self.registry.save_package(package)
                    provisional_package_by_group[parent_group] = package
                    selected_id = package.package_id
                    reason = "SAME_BATCH_PARENT_CONTEXT_GROUP"
                    self.registry.append_decision_audit(
                        DecisionAuditRecord(
                            audit_id=stable_id(
                                "audit",
                                {
                                    "run": run_id,
                                    "event": event.event_id,
                                    "type": "parent_context_group",
                                },
                            ),
                            run_id=run_id,
                            decision_type="PACKAGE_PARENT_CONTEXT_GROUP",
                            subject_id=event.event_id,
                            payload={
                                "parent_context_id": parent_context_id_by_group[parent_group],
                                "target_package_id": package.package_id,
                                "reason": "SAME_MESSAGE_ORIGINATOR_LOCAL_EVIDENCE_BLOCK",
                            },
                        )
                    )
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
                    if safe_grouping:
                        provisional_package_by_group[parent_group] = package
                    reason = (
                        "NO_PACKAGE_CANDIDATE"
                        if not candidates[event.event_id]
                        else "NO_MEMBER_CREATE_NEW_PACKAGE"
                    )
                action = (
                    PackageAction.ADD_TO_PACKAGE
                    if reason == "SAME_BATCH_PARENT_CONTEXT_GROUP"
                    else PackageAction.CREATE_NEW_PACKAGE
                )
                relation = (
                    PackageAssignmentRelation.MEMBER
                    if reason == "SAME_BATCH_PARENT_CONTEXT_GROUP"
                    else (
                        PackageAssignmentRelation.UNCERTAIN
                        if any(
                            assessment.relation is PackageAssignmentRelation.UNCERTAIN
                            for assessment in assessments
                        )
                        else PackageAssignmentRelation.NOT_RELATED
                    )
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

            with ThreadPoolExecutor(
                max_workers=min(self.n12_active_requests, len(batches))
            ) as executor:
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

    def _trusted_package_field_ids(
        self,
        package: EventPackage,
        namespaces: set[FieldNamespace],
    ) -> set[str]:
        trusted: set[str] = set()
        for event in self._package_member_events(package):
            for mention_id in event.mention_ids:
                for link in self.registry.list_field_links_for_mention(mention_id):
                    entry = self.registry.resolve_field_registry_entry(link.registry_id)
                    if entry is not None and entry.namespace in namespaces and entry.external_id:
                        trusted.add(entry.external_id)
        return trusted

    def _package_trading_sessions(self, package: EventPackage) -> set[str]:
        aliases = {
            "pre_market": "PRE_MARKET",
            "premarket": "PRE_MARKET",
            "pre-market": "PRE_MARKET",
            "regular": "REGULAR",
            "regular_session": "REGULAR",
            "regular session": "REGULAR",
            "market_hours": "REGULAR",
            "market hours": "REGULAR",
            "after_hours": "AFTER_HOURS",
            "after hours": "AFTER_HOURS",
            "after-hours": "AFTER_HOURS",
            "post_market": "AFTER_HOURS",
            "post-market": "AFTER_HOURS",
        }
        sessions: set[str] = set()
        for event in self._package_member_events(package):
            for mention_id in event.mention_ids:
                mention = self.registry.get_mention(mention_id)
                if mention is None:
                    continue
                for attribute in mention.open_attributes:
                    key = attribute.key.strip().casefold().replace("-", "_").replace(" ", "_")
                    if key not in {"trading_session", "market_session"}:
                        continue
                    value = attribute.value.strip().casefold()
                    normalized = aliases.get(value) or aliases.get(
                        value.replace("-", "_").replace(" ", "_")
                    )
                    if normalized is not None:
                        sessions.add(normalized)
        return sessions

    def _package_pair_external_relation_signal(
        self,
        left: EventPackage,
        right: EventPackage,
    ) -> bool:
        left_ids = set(left.member_event_ids)
        right_ids = set(right.member_event_ids)
        for candidate in self.registry.list_package_external_relation_candidates():
            target_root = self.registry.resolve_package_root(candidate.target_package_id)
            if (candidate.source_event_id in left_ids and target_root == right.package_id) or (
                candidate.source_event_id in right_ids and target_root == left.package_id
            ):
                return True
        return False

    def _package_source_count(self, package: EventPackage) -> int:
        source_ids: set[str] = set()
        for event in self._package_member_events(package):
            for mention_id in event.mention_ids:
                mention = self.registry.get_mention(mention_id)
                if mention is not None:
                    source_ids.add(mention.message_id)
        return len(source_ids)

    def _package_is_mixed(self, package: EventPackage) -> bool:
        members = self._package_member_events(package)
        reaction = {
            EventFamily.MARKET_MOVEMENT,
            EventFamily.ANALYST_ACTION,
        }
        families = {event.event_family for event in members}
        if families.intersection(reaction) and families.difference(reaction):
            return True
        institutions = {
            event.identity_profile.fields.institution_id
            for event in members
            if isinstance(event.identity_profile, AnalystActionIdentityProfile)
        }
        return len(institutions) > 1

    def _package_n13_profile_hash(self, package: EventPackage) -> str:
        members = self._package_member_events(package)
        return _hash_json(
            {
                "member_roots": sorted(
                    {self.registry.resolve_atomic_event_root(event.event_id) for event in members}
                ),
                "primary_anchor_id": package.primary_anchor_id,
                "anchor_conflict": package.anchor_conflict,
                "trusted_artifact_roots": sorted(self._trusted_package_anchor_ids(package)),
                "anchor_period_id": package.anchor_period_id,
                "package_kind": package.package_kind.value,
                "package_family": package.package_family.value,
                "anchor_entities": sorted(package.anchor_entities),
                "trading_sessions": sorted(self._package_trading_sessions(package)),
                "representative_identity_hashes": sorted(
                    {
                        _hash_json(event.identity_profile.model_dump(mode="json"))
                        for event in representative_package_members(members)
                    }
                ),
            }
        )

    def _package_pair_external_guard(
        self,
        left: EventPackage,
        right: EventPackage,
    ) -> str | None:
        left_artifacts = self._trusted_package_anchor_ids(left)
        right_artifacts = self._trusted_package_anchor_ids(right)
        if left_artifacts and right_artifacts and left_artifacts.isdisjoint(right_artifacts):
            return "DIFFERENT_TRUSTED_ARTIFACT_GUARD"
        left_periods = self._trusted_package_field_ids(
            left,
            {FieldNamespace.FISCAL_PERIOD},
        )
        right_periods = self._trusted_package_field_ids(
            right,
            {FieldNamespace.FISCAL_PERIOD},
        )
        if left_periods and right_periods and left_periods.isdisjoint(right_periods):
            return "DIFFERENT_EXACT_PERIOD_GUARD"
        left_families = {event.event_family for event in self._package_member_events(left)}
        right_families = {event.event_family for event in self._package_member_events(right)}
        if (
            left_families == {EventFamily.MARKET_MOVEMENT}
            and right_families == {EventFamily.MARKET_MOVEMENT}
            and (left_sessions := self._package_trading_sessions(left))
            and (right_sessions := self._package_trading_sessions(right))
            and left_sessions.isdisjoint(right_sessions)
        ):
            return "DIFFERENT_EXACT_TRADING_SESSION_GUARD"
        left_institutions = self._trusted_package_field_ids(
            left,
            {FieldNamespace.PARTICIPANT_INSTITUTION},
        )
        right_institutions = self._trusted_package_field_ids(
            right,
            {FieldNamespace.PARTICIPANT_INSTITUTION},
        )
        if (
            left_institutions
            and right_institutions
            and left_institutions.isdisjoint(right_institutions)
        ):
            return "DIFFERENT_ANALYST_INSTITUTION_GUARD"
        return None

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
        if set(left.package_anchor_ids).intersection(right.package_anchor_ids):
            routes.add(RecallRoute.PACKAGE_ANCHOR)
        if set(left.anchor_entities).intersection(right.anchor_entities):
            routes.add(RecallRoute.CORE_ENTITY)
        left_members = self._package_member_events(left)
        right_members = self._package_member_events(right)
        if any(
            self._events_share_parent_context(left_event, right_event)
            for left_event in left_members
            for right_event in right_members
        ):
            routes.add(RecallRoute.PARENT_CONTEXT)
        if any(self._event_shares_source(left_event, right_members) for left_event in left_members):
            routes.add(RecallRoute.SAME_SOURCE_MEMBER)
        if _package_ranges_near(left, right):
            routes.add(RecallRoute.TIME_WINDOW)
        left_identities = {
            _hash_json(event.identity_profile.model_dump(mode="json")) for event in left_members
        }
        right_identities = {
            _hash_json(event.identity_profile.model_dump(mode="json")) for event in right_members
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

    def _package_pair_boundary(
        self,
        left: EventPackage,
        right: EventPackage,
        routes: Sequence[RecallRoute],
    ) -> PackagePairBoundary:
        left_artifacts = self._trusted_package_anchor_ids(left)
        right_artifacts = self._trusted_package_anchor_ids(right)
        shared_artifacts = sorted(left_artifacts.intersection(right_artifacts))
        conflicting_artifacts = sorted(
            left_artifacts.union(right_artifacts)
            if left_artifacts and right_artifacts and left_artifacts.isdisjoint(right_artifacts)
            else set()
        )
        left_issuers = self._trusted_package_field_ids(left, {FieldNamespace.PARTICIPANT_COMPANY})
        right_issuers = self._trusted_package_field_ids(right, {FieldNamespace.PARTICIPANT_COMPANY})
        left_institutions = self._trusted_package_field_ids(
            left, {FieldNamespace.PARTICIPANT_INSTITUTION}
        )
        right_institutions = self._trusted_package_field_ids(
            right, {FieldNamespace.PARTICIPANT_INSTITUTION}
        )
        left_periods = self._trusted_package_field_ids(left, {FieldNamespace.FISCAL_PERIOD})
        right_periods = self._trusted_package_field_ids(right, {FieldNamespace.FISCAL_PERIOD})
        left_families = {event.event_family for event in self._package_member_events(left)}
        right_families = {event.event_family for event in self._package_member_events(right)}
        reaction_families = {EventFamily.MARKET_MOVEMENT, EventFamily.ANALYST_ACTION}
        left_sessions = self._package_trading_sessions(left)
        right_sessions = self._package_trading_sessions(right)
        return PackagePairBoundary(
            shared_artifact_ids=shared_artifacts,
            shared_anchor_ids=sorted(
                set(left.package_anchor_ids).intersection(right.package_anchor_ids)
            ),
            conflicting_artifact_ids=conflicting_artifacts,
            shared_parent_context=RecallRoute.PARENT_CONTEXT in routes,
            shared_source_member=RecallRoute.SAME_SOURCE_MEMBER in routes,
            member_identity_support=RecallRoute.MEMBER_IDENTITY in routes,
            time_support=RecallRoute.TIME_WINDOW in routes,
            issuer_conflict=bool(
                left_issuers and right_issuers and left_issuers.isdisjoint(right_issuers)
            ),
            reaction_boundary=bool(
                left_families.intersection(reaction_families)
                != right_families.intersection(reaction_families)
            ),
            analyst_boundary=bool(
                left_institutions
                and right_institutions
                and left_institutions.isdisjoint(right_institutions)
            ),
            period_boundary=bool(
                left_periods and right_periods and left_periods.isdisjoint(right_periods)
            ),
            session_boundary=bool(
                left_sessions and right_sessions and left_sessions.isdisjoint(right_sessions)
            ),
            left_member_count=len(left.member_event_ids),
            right_member_count=len(right.member_event_ids),
        )

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
                atomic_embeddings=atomic_embeddings,
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
        task_hook: Callable[
            [tuple[str, str], str, PackagePairMergeDecision | None, str | None], None
        ]
        | None = None,
        apply_started_hook: Callable[[], None] | None = None,
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
        guard_decisions: list[PackagePairMergeDecision] = []
        reused_decisions: list[PackagePairMergeDecision] = []
        pair_routes: dict[tuple[str, str], list[RecallRoute]] = {}
        pair_profiles: dict[tuple[str, str], tuple[int, int, str, str]] = {}
        decision_sources: dict[tuple[str, str], Literal["M0", "GUARD", "M3", "REUSED"]] = {}
        m3_pairs: list[tuple[EventPackage, EventPackage, list[RecallRoute], float | None]] = []
        for touched_id in sorted(result_ids):
            left = active.get(touched_id)
            if left is None:
                continue
            recalled_for_touched: list[
                tuple[EventPackage, EventPackage, list[RecallRoute], float | None]
            ] = []
            for right in active.values():
                if left.package_id == right.package_id:
                    continue
                pair_ids = (
                    min(left.package_id, right.package_id),
                    max(left.package_id, right.package_id),
                )
                if pair_ids in seen_pairs:
                    continue
                source_package = active[pair_ids[0]]
                target_package = active[pair_ids[1]]
                routes, similarity = self._package_pair_signals(
                    source_package, target_package, embeddings
                )
                if not routes:
                    continue
                weak_routes = {
                    RecallRoute.CORE_ENTITY,
                    RecallRoute.TIME_WINDOW,
                    RecallRoute.PROPOSITION_EMBEDDING,
                    RecallRoute.SAME_SOURCE_MEMBER,
                }
                if len(routes) == 1 and routes[0] in weak_routes:
                    continue
                recalled_for_touched.append((source_package, target_package, routes, similarity))
            recalled_for_touched.sort(
                key=lambda item: (
                    -_package_recall_score(item[2], item[3]),
                    item[0].package_id,
                    item[1].package_id,
                )
            )
            for source_package, target_package, routes, similarity in recalled_for_touched[
                :PACKAGE_N13_CANDIDATES_PER_TOUCHED
            ]:
                pair_key = (source_package.package_id, target_package.package_id)
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)
                left_hash = self._package_n13_profile_hash(source_package)
                right_hash = self._package_n13_profile_hash(target_package)
                pair_routes[pair_key] = routes
                pair_profiles[pair_key] = (
                    source_package.version,
                    target_package.version,
                    left_hash,
                    right_hash,
                )
                if self._package_pair_external_relation_signal(
                    source_package,
                    target_package,
                ):
                    self.registry.append_decision_audit(
                        DecisionAuditRecord(
                            audit_id=(
                                f"package-external-relation-shadow:{models.run_id}:"
                                f"{_hash_json(pair_key)[:16]}"
                            ),
                            run_id=models.run_id,
                            decision_type="PACKAGE_EXTERNAL_RELATION_GUARD_SHADOW",
                            subject_id=f"{pair_key[0]}->{pair_key[1]}",
                            payload={
                                "signal": "N12_EXTERNAL_RELATION_CANDIDATE",
                                "enforcement": "ADVISORY_ONLY",
                            },
                        )
                    )
                guard_reason = self._package_pair_external_guard(source_package, target_package)
                if guard_reason is not None:
                    decision_sources[pair_key] = "GUARD"
                    guard_decisions.append(
                        PackagePairMergeDecision(
                            source_package_id=source_package.package_id,
                            target_package_id=target_package.package_id,
                            relation=PackageMergeRelation.DIFFERENT_PACKAGE,
                            reason=guard_reason,
                        )
                    )
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
                    decision_sources[pair_key] = "M0"
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
                    shadow_rules: list[str] = []
                    if RecallRoute.PARENT_CONTEXT in routes:
                        shadow_rules.append("M0_B_LOCAL_PARENT_CONTEXT")
                    if (
                        source_package.package_family is PackageFamily.EARNINGS_DISCLOSURE
                        and target_package.package_family is PackageFamily.EARNINGS_DISCLOSURE
                        and source_package.anchor_period_id
                        and source_package.anchor_period_id == target_package.anchor_period_id
                        and RecallRoute.PARENT_CONTEXT in routes
                    ):
                        shadow_rules.append("M0_C_EARNINGS_PARENT")
                    if shadow_rules:
                        self.registry.append_decision_audit(
                            DecisionAuditRecord(
                                audit_id=stable_id(
                                    "audit",
                                    {
                                        "run": run_id,
                                        "source": source_package.package_id,
                                        "target": target_package.package_id,
                                        "type": "package_m0_shadow",
                                    },
                                ),
                                run_id=run_id,
                                decision_type="PACKAGE_M0_SHADOW",
                                subject_id=source_package.package_id,
                                payload={
                                    "target_package_id": target_package.package_id,
                                    "rules": shadow_rules,
                                    "action": "OBSERVE_ONLY",
                                },
                            )
                        )
                    prior = self.registry.get_package_pair_evaluation(
                        left_package_id=source_package.package_id,
                        right_package_id=target_package.package_id,
                        left_profile_hash=left_hash,
                        right_profile_hash=right_hash,
                    )
                    if prior is not None:
                        decision_sources[pair_key] = "REUSED"
                        reused_decisions.append(
                            PackagePairMergeDecision(
                                source_package_id=source_package.package_id,
                                target_package_id=target_package.package_id,
                                relation=prior.relation,
                                reason=f"UNCHANGED_PROFILE_REUSE:{prior.evaluation_id}",
                            )
                        )
                        continue
                    decision_sources[pair_key] = "M3"
                    m3_pairs.append((source_package, target_package, routes, similarity))

        if task_hook is not None:
            for pair_key in sorted(seen_pairs):
                task_hook(pair_key, "RUNNING", None, None)

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
                request_events = {
                    event.event_id: event
                    for package_id in package_ids
                    for event in self._package_member_events(package_by_id[package_id])
                }
                source_short_ids = request_local_source_ids(
                    self.registry, list(request_events.values())
                )
                slim_packages: dict[str, object] = {}
                anchor_short_by_full = {
                    anchor_id: f"h{index}"
                    for index, anchor_id in enumerate(
                        sorted(
                            {
                                anchor_id
                                for package_id in package_ids
                                for anchor_id in package_by_id[package_id].package_anchor_ids
                            }
                        ),
                        start=1,
                    )
                }
                for package_id in package_ids:
                    package = package_by_id[package_id]
                    slim_view = build_slim_package_view(
                        self.registry,
                        package,
                        source_short_ids=source_short_ids,
                    )
                    if package.package_anchor_ids:
                        slim_view["anchors"] = [
                            anchor_short_by_full[value] for value in package.package_anchor_ids
                        ]
                    if package.primary_anchor_id is not None:
                        slim_view["primary_anchor"] = anchor_short_by_full[
                            package.primary_anchor_id
                        ]
                    if package.anchor_conflict:
                        slim_view["anchor_conflict"] = True
                    slim_packages[short_by_full[package_id]] = slim_view
                wire_pairs: list[dict[str, object]] = []
                for left, right, routes, similarity in pairs:
                    source_id = short_by_full[left.package_id]
                    target_id = short_by_full[right.package_id]
                    wire_pairs.append(
                        {
                            "id": pair_ids[(source_id, target_id)],
                            "left": source_id,
                            "right": target_id,
                            "routes": [route.value for route in routes],
                            "similarity": (None if similarity is None else round(similarity, 3)),
                        }
                    )
                wire_payload: dict[str, object] = {
                    "batch_index": batch_index,
                    "batch_count": len(batches),
                    "packages": slim_packages,
                    "pairs": wire_pairs,
                }
                field_bytes = {
                    key: len(compact_json(value).encode("utf-8"))
                    for key, value in wire_payload.items()
                }
                self.registry.append_decision_audit(
                    DecisionAuditRecord(
                        audit_id=stable_id(
                            "audit",
                            {
                                "run": run_id,
                                "batch": batch_index,
                                "type": "package_n13_payload_profile",
                            },
                        ),
                        run_id=run_id,
                        decision_type="PACKAGE_N13_PAYLOAD_PROFILE",
                        subject_id=f"batch:{batch_index}",
                        payload={
                            "protocol": self.n13_wire_protocol,
                            "field_bytes": field_bytes,
                            "package_count": len(slim_packages),
                            "pair_count": len(wire_pairs),
                            "payload_bytes": len(compact_json(wire_payload).encode("utf-8")),
                        },
                    )
                )
                request = StructuredModelRequest(
                    system_prompt=_prompt("package_merge.md"),
                    user_prompt=compact_json(wire_payload),
                    json_schema=PackageMergeWireDecisionBatch.model_json_schema(),
                )
                failed_pair_ids: set[str] = set()

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
                            if isinstance(source_id, str) and isinstance(target_id, str):
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
                    failed_pairs: list[dict[str, str]] = []
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
                            failed_pair_ids.add(pair_id)
                            failed_pairs.append(
                                {
                                    "pair_id": pair_id,
                                    "reason": "MISSING_DUPLICATE_OR_INVALID_PAIR",
                                }
                            )
                            continue
                        assert pair_item is not None
                        adapted.append(pair_item)
                    if failed_pairs:
                        self.registry.append_decision_audit(
                            DecisionAuditRecord(
                                audit_id=(
                                    f"package-n13-unjudgeable:{models.run_id}:"
                                    f"{batch_index}:{_hash_json(failed_pairs)[:16]}"
                                ),
                                run_id=models.run_id,
                                decision_type="PACKAGE_N13_PAIR_FAILED",
                                subject_id=f"batch:{batch_index}",
                                payload={
                                    "action": "UNJUDGEABLE_FAILED",
                                    "pairs": failed_pairs,
                                },
                            )
                        )
                    return {"decisions": adapted}

                def validate(output: PackageMergeWireDecisionBatch) -> None:
                    actual = {item.pair_id for item in output.decisions}
                    if len(actual) != len(output.decisions) or not actual.issubset(pair_by_id):
                        raise ValueError(
                            "package merge decisions contain duplicate or unknown pairs"
                        )

                try:
                    output = models.typed(
                        tier=ModelTier.M3,
                        stage="package_merge",
                        request=request,
                        output_type=PackageMergeWireDecisionBatch,
                        validator=validate,
                        payload_adapter=adapt,
                    )
                except CrossDocumentPipelineError as exc:
                    output = PackageMergeWireDecisionBatch(decisions=[])
                    failed_pair_ids.update(pair_by_id)
                    self.registry.append_decision_audit(
                        DecisionAuditRecord(
                            audit_id=f"package-n13-batch-failed:{models.run_id}:{batch_index}",
                            run_id=models.run_id,
                            decision_type="PACKAGE_N13_PAIR_FAILED",
                            subject_id=f"batch:{batch_index}",
                            payload={
                                "code": exc.code,
                                "action": "UNJUDGEABLE_FAILED",
                                "pair_ids": sorted(pair_by_id),
                            },
                        )
                    )
                if failed_pair_ids:
                    wire_pair_by_id = {str(item["id"]): item for item in wire_pairs}

                    def repair_pair(
                        pair_id: str,
                    ) -> tuple[str, PackagePairMergeDecision | None]:
                        pair = wire_pair_by_id[pair_id]
                        left = str(pair["left"])
                        right = str(pair["right"])
                        repair_request = StructuredModelRequest(
                            system_prompt=(
                                _prompt("package_merge.md")
                                + "\nRepair exactly this one invalid pair and return it once."
                            ),
                            user_prompt=compact_json(
                                {
                                    "validation_error": "MISSING_DUPLICATE_OR_INVALID_PAIR",
                                    "packages": {
                                        left: slim_packages[left],
                                        right: slim_packages[right],
                                    },
                                    "pairs": [pair],
                                }
                            ),
                            json_schema=PackageMergeWireDecisionBatch.model_json_schema(),
                        )
                        try:
                            result = models._structured(
                                tier=ModelTier.M3,
                                stage="package_merge_item",
                                request=repair_request,
                                repaired=True,
                            )
                            raw = result.payload
                            repaired = PackageMergeWireDecisionBatch.model_validate(raw)
                            if len(repaired.decisions) != 1:
                                raise ValueError("pair repair must return one decision")
                            decision = repaired.decisions[0]
                            if decision.pair_id != pair_id:
                                raise ValueError("pair repair returned the wrong pair")
                            full_pair = pair_by_id[pair_id]
                            restored = PackagePairMergeDecision(
                                source_package_id=full_by_short[full_pair[0]],
                                target_package_id=full_by_short[full_pair[1]],
                                relation=decision.relation,
                                reason=decision.reason,
                            )
                            status = "SUCCEEDED"
                            error_code = None
                        except (CrossDocumentPipelineError, ValidationError, ValueError) as exc:
                            restored = None
                            status = "FAILED_ISOLATED"
                            error_code = (
                                exc.code
                                if isinstance(exc, CrossDocumentPipelineError)
                                else type(exc).__name__
                            )
                        self.registry.append_decision_audit(
                            DecisionAuditRecord(
                                audit_id=stable_id(
                                    "package-n13-pair-repair",
                                    {
                                        "run": models.run_id,
                                        "batch": batch_index,
                                        "pair": pair_id,
                                    },
                                ),
                                run_id=models.run_id,
                                decision_type="PACKAGE_N13_PAIR_REPAIR",
                                subject_id=f"batch:{batch_index}:{pair_id}",
                                payload={"status": status, "error_code": error_code},
                            )
                        )
                        return pair_id, restored

                    with ThreadPoolExecutor(
                        max_workers=min(8, len(failed_pair_ids))
                    ) as repair_pool:
                        repaired_pairs = dict(repair_pool.map(repair_pair, sorted(failed_pair_ids)))
                    repaired_decisions = [
                        decision for decision in repaired_pairs.values() if decision is not None
                    ]
                else:
                    repaired_decisions = []
                return (
                    batch_index,
                    PackageMergeDecisionBatch(
                        decisions=[
                            PackagePairMergeDecision(
                                source_package_id=full_by_short[pair_by_id[decision.pair_id][0]],
                                target_package_id=full_by_short[pair_by_id[decision.pair_id][1]],
                                relation=decision.relation,
                                reason=decision.reason,
                            )
                            for decision in output.decisions
                        ]
                        + repaired_decisions
                    ),
                )

            with ThreadPoolExecutor(
                max_workers=min(self.n13_active_requests, len(batches))
            ) as executor:
                outputs = list(executor.map(process_batch, enumerate(batches)))
            for _, output in sorted(outputs):
                m3_decisions.extend(output.decisions)

        decisions = [
            *m0_decisions,
            *guard_decisions,
            *reused_decisions,
            *m3_decisions,
        ]
        if task_hook is not None:
            decision_by_pair = {
                tuple(sorted((decision.source_package_id, decision.target_package_id))): decision
                for decision in decisions
            }
            for pair_key in sorted(seen_pairs):
                decision = decision_by_pair.get(pair_key)
                if decision is None:
                    task_hook(pair_key, "FAILED", None, "UNJUDGEABLE_FAILED")
                else:
                    task_hook(pair_key, "SUCCEEDED", decision, None)
        if apply_started_hook is not None:
            apply_started_hook()
        eligible_same_decisions: list[
            tuple[PackagePairMergeDecision, PackagePairBoundary, int]
        ] = []
        for decision in decisions:
            if decision.relation is PackageMergeRelation.SAME_PACKAGE:
                left_package = active.get(decision.source_package_id)
                right_package = active.get(decision.target_package_id)
                routes = pair_routes.get(
                    (
                        min(
                            decision.source_package_id,
                            decision.target_package_id,
                        ),
                        max(
                            decision.source_package_id,
                            decision.target_package_id,
                        ),
                    ),
                    [],
                )
                boundary = (
                    self._package_pair_boundary(left_package, right_package, routes)
                    if left_package is not None and right_package is not None
                    else None
                )
                tier_a = bool(
                    RecallRoute.SHARED_ATOMIC_EVENT in routes
                    or (boundary and boundary.shared_artifact_ids)
                    or (boundary and boundary.shared_anchor_ids)
                )
                tier_b = bool(
                    boundary
                    and min(boundary.left_member_count, boundary.right_member_count) <= 4
                    and boundary.independent_positive_count >= 2
                )
                apply_allowed = bool(
                    getattr(self, "n13_pair_local_apply", True)
                    and boundary is not None
                    and not boundary.hard_blocked
                    and (tier_a or tier_b)
                )
                if apply_allowed:
                    assert boundary is not None
                    eligible_same_decisions.append((decision, boundary, 2 if tier_a else 1))
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
                    decision_id=decision_id,
                    run_id=run_id,
                    decision=decision,
                )
                self.registry.append_decision_audit(
                    DecisionAuditRecord(
                        audit_id=f"package-same-not-applied:{decision_id}",
                        run_id=run_id,
                        decision_type="SAME_PACKAGE_NOT_APPLIED_WEAK_BOUNDARY",
                        subject_id=decision.source_package_id,
                        payload={
                            "target_package_id": decision.target_package_id,
                            "routes": [route.value for route in routes],
                            "shared_primary_anchor": bool(boundary and boundary.shared_anchor_ids),
                            "anchor_conflict": bool(
                                (left_package and left_package.anchor_conflict)
                                or (right_package and right_package.anchor_conflict)
                            ),
                            "pair_boundary": (
                                boundary.model_dump(mode="json") if boundary else None
                            ),
                            "reason": decision.reason,
                        },
                    )
                )
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

        # One fixed hub-and-spoke round. A small fragment may be consumed once;
        # a hub may absorb at most four independent spokes. No newly merged root
        # is recalled again in this epoch.
        same_decisions: list[PackagePairMergeDecision] = []
        consumed_spokes: set[str] = set()
        spokes_by_hub: dict[str, int] = defaultdict(int)
        for decision, _boundary, _tier in sorted(
            eligible_same_decisions,
            key=lambda item: (
                -item[2],
                -item[1].independent_positive_count,
                item[0].source_package_id,
                item[0].target_package_id,
            ),
        ):
            left = active[decision.source_package_id]
            right = active[decision.target_package_id]
            hub, spoke = sorted(
                (left, right),
                key=lambda package: (-len(package.member_event_ids), package.package_id),
            )
            if (
                spoke.package_id in consumed_spokes
                or len(spoke.member_event_ids) > 4
                or spokes_by_hub[hub.package_id] >= 4
            ):
                continue
            same_decisions.append(
                decision.model_copy(
                    update={
                        "source_package_id": spoke.package_id,
                        "target_package_id": hub.package_id,
                    }
                )
            )
            consumed_spokes.add(spoke.package_id)
            spokes_by_hub[hub.package_id] += 1
        components = _same_package_components(same_decisions)
        for component in components:
            component_same = [
                decision
                for decision in same_decisions
                if decision.source_package_id in component
                and decision.target_package_id in component
            ]
            applied_pairs = {
                frozenset((item.source_package_id, item.target_package_id))
                for item in component_same
            }
            contradictory = [
                decision
                for decision in decisions
                if decision.relation is PackageMergeRelation.DIFFERENT_PACKAGE
                and frozenset((decision.source_package_id, decision.target_package_id))
                in applied_pairs
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
            late_boundary = PackageBoundaryGate(self.registry).evaluate(
                merged, list(member_events.values())
            )
            if late_boundary.severity == "BLOCKING_CONFLICT":
                self.registry.append_decision_audit(
                    DecisionAuditRecord(
                        audit_id=stable_id(
                            "audit",
                            {
                                "run": run_id,
                                "target": target.package_id,
                                "type": "n13_late_plan_blocked",
                            },
                        ),
                        run_id=run_id,
                        decision_type="N13_LATE_MERGE_PLAN_BLOCKED",
                        subject_id=target.package_id,
                        payload={
                            "sources": sorted(package.package_id for package in sources),
                            "finding": late_boundary.model_dump(mode="json"),
                            "action": "KEEP_COMPONENTS_SEPARATE",
                        },
                    )
                )
                continue
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

        for decision in decisions:
            pair_key = (
                min(decision.source_package_id, decision.target_package_id),
                max(decision.source_package_id, decision.target_package_id),
            )
            profile = pair_profiles.get(pair_key)
            source = decision_sources.get(pair_key)
            if profile is None or source is None:
                continue
            left_version, right_version, left_hash, right_hash = profile
            left_root = self.registry.resolve_package_root(pair_key[0])
            right_root = self.registry.resolve_package_root(pair_key[1])
            membership_changed = bool(
                decision.relation is PackageMergeRelation.SAME_PACKAGE
                and left_root is not None
                and left_root == right_root
            )
            self.registry.save_package_pair_evaluation(
                PackagePairEvaluation(
                    evaluation_id=stable_id(
                        "package-pair-evaluation",
                        {
                            "run": run_id,
                            "left": pair_key[0],
                            "right": pair_key[1],
                            "left_hash": left_hash,
                            "right_hash": right_hash,
                        },
                    ),
                    run_id=run_id,
                    left_package_id=pair_key[0],
                    right_package_id=pair_key[1],
                    left_version=left_version,
                    right_version=right_version,
                    left_profile_hash=left_hash,
                    right_profile_hash=right_hash,
                    routes=pair_routes[pair_key],
                    relation=decision.relation,
                    decision_source=source,
                    deterministic_rule=(decision.reason if source in {"M0", "GUARD"} else None),
                    membership_changed=membership_changed,
                )
            )

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

            with ThreadPoolExecutor(
                max_workers=min(self.n13_active_requests, len(batches))
            ) as executor:
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
