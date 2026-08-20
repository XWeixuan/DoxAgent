"""Incremental Atomic Event and Event Package engine for standalone CDECR."""

from __future__ import annotations

import hashlib
import json
import math
import re
import threading
import traceback
import uuid
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from time import perf_counter
from typing import Literal, TypeVar, cast

from pydantic import BaseModel, ValidationError

from cdecr.atomic_exact_recall import ExactCosineRecallIndex
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
from cdecr.bulk_epoch.candidate_cards import AtomicCandidateCard
from cdecr.bulk_epoch.embedding import (
    EmbeddingBatchExecutor,
    EmbeddingBatchTelemetry,
    EmbeddingWorkItem,
)
from cdecr.bulk_epoch.indexes import MultiKeyBoundedIndex
from cdecr.bulk_epoch.stage_runtime import StageReadSnapshot, StageWriteBuffer
from cdecr.bulk_epoch.writer import BulkWriter
from cdecr.canonical_field_resolution import (
    FIELD_RESOLVER_VERSION,
    CanonicalFieldResolutionEngine,
    field_links_hash,
)
from cdecr.contracts import (
    AtomicAction,
    AtomicEvent,
    AtomicSemanticRelation,
    EventFamily,
    EventMention,
    IdentityProfile,
    ParticipantRole,
    SourceMessage,
)
from cdecr.coreference_rules import (
    add_mention_to_atomic,
    core_entity_ids_from_profile,
    hard_cannot_link,
    reference_period_from_profile,
    singleton_atomic_event,
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
    PackageAssignmentRecord,
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
from cdecr.package_global_clustering import (
    PACKAGE_V3_MODEL,
    PACKAGE_V3_REASONING_EFFORT,
    PackageWorkflowV3Service,
)
from cdecr.package_projection import project_frozen_partition_v3
from cdecr.parent_occurrence import ParentOccurrenceService
from cdecr.ports import (
    CDECRRegistry,
    DecisionAuditRecord,
    EmbeddingClient,
    EmbeddingResult,
    ResponsesModelRequest,
    StructuredModelClient,
    StructuredModelRequest,
    StructuredModelResult,
)
from cdecr.provider_resilience import classify_provider_error, is_provider_failure
from cdecr.scheduler import take_scheduled_call_metrics
from cdecr.single_document_contracts import ModelCallSummary
from cdecr.wire import compact_json, wire_ref_metadata

ENGINE_VERSION = "cdecr-cross-document-v31-package-global-registry-v3"
PROMPT_VERSION = "cdecr-package-global-registry-prompts-v3"
WIRE_PROTOCOL_VERSION = "cdecr-cross-document-wire-atomic-dictionary-v9"
ATOMIC_ASSIGNMENT_POLICY_VERSION = "atomic-assignment-policy-v5-late-convergence"
ATOMIC_DECISION_MENTION_BATCH = 3
N9_BATCH_PACKING_VERSION = "n9-candidate-overlap-packing-v1"


@dataclass(frozen=True)
class N9TaskFailure:
    error_code: str
    retryable: bool


@dataclass(frozen=True)
class N9BatchOutcome:
    batch_index: int
    decisions: tuple[AtomicAssignmentDecision, ...]
    failures: Mapping[str, N9TaskFailure]


def _pack_mentions_by_candidate_overlap(
    mentions: Sequence[EventMention],
    candidates: Mapping[str, Sequence[AtomicCandidate]],
) -> list[list[EventMention]]:
    remaining = {item.mention_id: item for item in mentions}
    candidate_ids = {
        mention_id: {item.event.event_id for item in values}
        for mention_id, values in candidates.items()
    }
    batches: list[list[EventMention]] = []
    while remaining:
        seed_id = min(remaining)
        batch = [remaining.pop(seed_id)]
        batch_union = set(candidate_ids.get(seed_id, set()))
        while remaining and len(batch) < ATOMIC_DECISION_MENTION_BATCH:
            def rank(
                mention_id: str,
                current: frozenset[str] = frozenset(batch_union),
            ) -> tuple[int, float, int, str]:
                values = candidate_ids.get(mention_id, set())
                shared = len(current.intersection(values))
                union = len(current.union(values))
                jaccard = shared / union if union else 0.0
                return (-shared, -jaccard, len(values), mention_id)

            selected_id = min(remaining, key=rank)
            batch.append(remaining.pop(selected_id))
            batch_union.update(candidate_ids.get(selected_id, set()))
        batches.append(batch)
    return batches
ATOMIC_TOP_K = 5
EMBEDDING_RECALL_THRESHOLD = 0.82
HIGH_IMPACT_FAMILIES = {
    EventFamily.TRANSACTION_CAPITAL,
    EventFamily.REGULATORY_LEGAL_POLICY,
    EventFamily.INCIDENT_GEOPOLITICAL,
}
_LOCATION_FIELD_NAMESPACES = set(ATOMIC_FIELD_RECALL_NAMESPACES)
_T = TypeVar("_T", bound=BaseModel)
_BatchItem = TypeVar("_BatchItem")


def _balanced_request_batches(
    items: Sequence[_BatchItem],
    *,
    nominal_size: int,
    active_requests: int,
    max_balanced_size: int,
) -> list[list[_BatchItem]]:
    """Avoid a one-request tail wave without increasing normal batch size broadly."""

    batches = [
        list(items[offset : offset + nominal_size]) for offset in range(0, len(items), nominal_size)
    ]
    if (
        len(batches) > active_requests
        and len(batches) % active_requests == 1
        and math.ceil(len(items) / (len(batches) - 1)) <= max_balanced_size
    ):
        batch_count = len(batches) - 1
        base, extra = divmod(len(items), batch_count)
        balanced: list[list[_BatchItem]] = []
        offset = 0
        for index in range(batch_count):
            size = base + (1 if index < extra else 0)
            balanced.append(list(items[offset : offset + size]))
            offset += size
        return balanced
    return batches


class CrossDocumentPipelineError(RuntimeError):
    def __init__(self, stage: str, code: str, *, repair_payload: str | None = None) -> None:
        self.stage = stage
        self.code = code
        self.repair_payload = repair_payload
        super().__init__(f"cross-document stage {stage!r} failed: {code}")


def _hash_json(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _active_atomic_target(
    registry: CDECRRegistry,
    touched: Mapping[str, AtomicEvent],
    event_id: str,
) -> tuple[str, AtomicEvent | None]:
    """Resolve an Apply target through redirects before consulting local state."""

    root_id = registry.resolve_atomic_event_root(event_id)
    return root_id, touched.get(root_id) or registry.get_current_atomic_event(root_id)


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


def _model_result_transport_metadata(result: StructuredModelResult) -> dict[str, object]:
    return {
        "transport": result.transport,
        "output_mode": result.output_mode,
        "effective_reasoning_effort": result.effective_reasoning_effort,
        "provider_key_fingerprint": result.provider_key_fingerprint,
        "parse_diagnostics": result.parse_diagnostics,
    }


def _model_error_parse_metadata(exc: Exception) -> dict[str, object]:
    if not isinstance(exc, ModelAdapterError):
        return {}
    return {
        "exception_class": type(exc).__name__,
        "provider_key_fingerprint": exc.provider_key_fingerprint,
        "provider_status_code": exc.status_code,
        "provider_failure_class": classify_provider_error(exc).value,
        "physical_attempt_count": int(getattr(exc, "physical_attempt_count", 1)),
        "retry_attempt_count": int(getattr(exc, "retry_attempt_count", 0)),
        "circuit_state": str(getattr(exc, "circuit_state", "CLOSED")),
        "circuit_wait_ms": int(getattr(exc, "circuit_wait_ms", 0)),
        "provider_wait_ms": int(getattr(exc, "provider_wait_ms", 0)),
        "backoff_ms": int(getattr(exc, "backoff_ms", 0)),
        "parse_diagnostics": exc.parse_diagnostics,
    }


def _resolved_field_ids(
    registry: CDECRRegistry,
    mentions: Sequence[EventMention],
    *,
    allowed: set[FieldNamespace],
    read_snapshot: StageReadSnapshot | None = None,
) -> list[tuple[FieldNamespace, str]]:
    resolved: set[tuple[FieldNamespace, str]] = set()
    for mention in mentions:
        links = (
            read_snapshot.field_links_by_mention.get(mention.mention_id, ())
            if read_snapshot is not None
            else registry.list_field_links_for_mention(mention.mention_id)
        )
        for link in links:
            root = (
                read_snapshot.field_entries_by_id.get(link.registry_id)
                if read_snapshot is not None
                else registry.resolve_field_registry_entry(link.registry_id)
            )
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


def is_content_repairable(error: Exception) -> bool:
    """Return whether another LLM call can repair the returned content itself."""

    if isinstance(error, (ValidationError, ValueError)):
        return True
    if isinstance(error, CrossDocumentPipelineError):
        return error.code in {
            "invalid_json",
            "invalid_json_shape",
            "structured_output_invalid",
        }
    return False


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
        m4_client: StructuredModelClient | None = None,
        model_m4: str | None = None,
        responses_m4_client: StructuredModelClient | None = None,
        responses_model_m4: str | None = None,
    ) -> None:
        self.registry = registry
        self.run_id = run_id
        self.embedding_client = embedding_client
        self.m2_client = m2_client
        self.m3_client = m3_client
        self.m4_client = m4_client or m3_client
        self.responses_m4_client = responses_m4_client or self.m4_client
        self.model_m1 = model_m1
        self.model_m2 = model_m2
        self.model_m3 = model_m3
        self.model_m4 = model_m4 or model_m3
        self.responses_model_m4 = responses_model_m4 or self.model_m4
        self.summaries = summaries
        self._call_context = threading.local()

    def _client_and_model(
        self,
        execution_tier: ModelTier,
        *,
        responses: bool = False,
    ) -> tuple[StructuredModelClient, str]:
        if responses and execution_tier is ModelTier.M4:
            return self.responses_m4_client, self.responses_model_m4
        if execution_tier is ModelTier.M4:
            return self.m4_client, self.model_m4
        if execution_tier is ModelTier.M3:
            return self.m3_client, self.model_m3
        return self.m2_client, self.model_m2

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
        execution_tier: ModelTier | None = None,
    ) -> _T:
        lane = execution_tier or tier
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
                repaired = self._structured(
                    tier=tier,
                    execution_tier=lane,
                    stage=stage,
                    request=repair,
                    repaired=True,
                )
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
            result = self._structured(
                tier=tier,
                execution_tier=lane,
                stage=stage,
                request=request,
                repaired=False,
            )
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

    def typed_response(
        self,
        *,
        tier: ModelTier,
        stage: str,
        request: ResponsesModelRequest,
        output_type: type[_T],
        validator: Callable[[_T], None],
        execution_tier: ModelTier | None = None,
    ) -> _T:
        """Execute one audited Responses request with one same-contract repair."""

        lane = execution_tier or tier

        def execute(value: ResponsesModelRequest, *, repaired: bool) -> StructuredModelResult:
            call_stage = f"{stage}_repair" if repaired else stage
            client, model = self._client_and_model(lane, responses=True)
            complete_response = getattr(client, "complete_response", None)
            if not callable(complete_response):
                raise CrossDocumentPipelineError(call_stage, "responses_api_unavailable")
            call_id = str(uuid.uuid4())
            prepared = value.model_copy(
                update={
                    "metadata": {
                        **value.metadata,
                        "stage": call_stage,
                        "priority": "repair" if repaired else "normal",
                        "model_profile": tier.value,
                        "scheduler_lane": lane.value,
                    }
                }
            )
            input_hash = _hash_json(prepared.input)
            schema_hash = _hash_json(prepared.json_schema)
            started = perf_counter()
            try:
                result = cast(StructuredModelResult, complete_response(prepared))
            except Exception as exc:
                scheduled = take_scheduled_call_metrics(client)
                latency, code = _safe_error(exc)
                self.registry.record_model_call(
                    model_call_id=call_id,
                    run_id=self.run_id,
                    tier=tier.value,
                    model=model,
                    status="FAILED",
                    input_tokens=(
                        exc.input_tokens if isinstance(exc, ModelAdapterError) else None
                    ),
                    output_tokens=(
                        exc.output_tokens if isinstance(exc, ModelAdapterError) else None
                    ),
                    latency_ms=latency,
                    error_code=code,
                    metadata={
                        **prepared.metadata,
                        "attempt": "repair" if repaired else "initial",
                        "queue_wait_ms": scheduled.queue_wait_ms if scheduled else 0,
                        "output_mode": prepared.output_mode,
                        **_model_error_parse_metadata(exc),
                    },
                    stage=call_stage,
                    prompt_version=PROMPT_VERSION,
                    schema_hash=schema_hash,
                    input_hash=input_hash,
                )
                raise CrossDocumentPipelineError(call_stage, code) from exc
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
                    **prepared.metadata,
                    "attempt": "repair" if repaired else "initial",
                    "reasoning_tokens": result.reasoning_tokens,
                    "cached_input_tokens": result.cached_input_tokens,
                    "response_id": result.response_id,
                    "output_mode": prepared.output_mode,
                    "elapsed_ms": round((perf_counter() - started) * 1000),
                    "output_hash": _hash_json(result.payload),
                    **_model_result_transport_metadata(result),
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
                )
            )
            return result

        try:
            initial = execute(request, repaired=False)
            output = output_type.model_validate(initial.payload)
            validator(output)
            return output
        except (ValidationError, ValueError, CrossDocumentPipelineError) as first_error:
            if not is_content_repairable(first_error):
                raise
            repair_input = [
                *request.input,
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "repair": "Correct the prior invalid output using only input IDs.",
                            "validation_error": str(first_error)[:1200],
                        },
                        ensure_ascii=False,
                    ),
                },
            ]
            repaired = execute(
                request.model_copy(update={"input": repair_input}), repaired=True
            )
            output = output_type.model_validate(repaired.payload)
            validator(output)
            return output

    def typed_many(
        self,
        *,
        tier: ModelTier,
        stage: str,
        requests: Sequence[StructuredModelRequest],
        output_type: type[_T],
        validators: Sequence[Callable[[_T], None]],
        execution_tier: ModelTier | None = None,
    ) -> list[_T | Exception]:
        """Validate one parallel stage wave without creating a private HTTP/thread pool."""

        lane = execution_tier or tier

        if len(requests) != len(validators):
            raise ValueError("typed_many requests and validators must align")
        if not requests:
            return []
        client, model = self._client_and_model(lane)
        complete_many = getattr(client, "complete_many", None)
        if complete_many is None:
            raw_results: list[StructuredModelResult | Exception] = []
            for request in requests:
                try:
                    raw_results.append(client.complete(request))
                except Exception as exc:  # pragma: no cover - compatibility client
                    raw_results.append(exc)
        else:
            prepared = [
                request.model_copy(
                    update={
                        "metadata": {
                            **request.metadata,
                            "stage": stage,
                            "priority": "normal",
                            "model_profile": tier.value,
                            "scheduler_lane": lane.value,
                        }
                    }
                )
                for request in requests
            ]
            raw_results = list(complete_many(prepared))

        outputs: list[_T | Exception] = []
        for index, (request, validator, raw) in enumerate(
            zip(requests, validators, raw_results, strict=True)
        ):
            call_id = str(uuid.uuid4())
            metadata = {
                **_structured_request_metadata(request),
                "model_profile": tier.value,
                "scheduler_lane": lane.value,
            }
            input_hash = _hash_json(
                {"system": request.system_prompt, "user": request.user_prompt}
            )
            schema_hash = _hash_json(request.json_schema)
            if isinstance(raw, Exception):
                latency, code = _safe_error(raw)
                self.registry.record_model_call(
                    model_call_id=call_id,
                    run_id=self.run_id,
                    tier=tier.value,
                    model=model,
                    status="FAILED",
                    input_tokens=(raw.input_tokens if isinstance(raw, ModelAdapterError) else None),
                    output_tokens=(
                        raw.output_tokens if isinstance(raw, ModelAdapterError) else None
                    ),
                    latency_ms=latency,
                    error_code=code,
                    metadata={
                        **metadata,
                        "attempt": "initial",
                        "batch_index": index,
                        **_model_error_parse_metadata(raw),
                    },
                    stage=stage,
                    prompt_version=PROMPT_VERSION,
                    schema_hash=schema_hash,
                    input_hash=input_hash,
                )
                self.summaries.append(
                    ModelCallSummary(
                        stage=stage,
                        tier=tier.value,
                        model=model,
                        latency_ms=latency,
                        status="FAILED",
                        error_code=code,
                        request_item_count=_metadata_int(metadata, "request_item_count"),
                        request_payload_bytes=_metadata_int(metadata, "request_payload_bytes"),
                    )
                )
                outputs.append(CrossDocumentPipelineError(stage, code))
                continue
            self.registry.record_model_call(
                model_call_id=call_id,
                run_id=self.run_id,
                tier=tier.value,
                model=raw.model,
                status="SUCCEEDED",
                input_tokens=raw.input_tokens,
                output_tokens=raw.output_tokens,
                latency_ms=raw.latency_ms,
                error_code=None,
                metadata={
                    **metadata,
                    "attempt": "initial",
                    "batch_index": index,
                    "output_hash": _hash_json(raw.payload),
                    **_model_result_transport_metadata(raw),
                },
                stage=stage,
                prompt_version=PROMPT_VERSION,
                schema_hash=schema_hash,
                input_hash=input_hash,
            )
            self.summaries.append(
                ModelCallSummary(
                    stage=stage,
                    tier=tier.value,
                    model=raw.model,
                    input_tokens=raw.input_tokens,
                    output_tokens=raw.output_tokens,
                    latency_ms=raw.latency_ms,
                    request_item_count=_metadata_int(metadata, "request_item_count"),
                    request_payload_bytes=_metadata_int(metadata, "request_payload_bytes"),
                )
            )
            try:
                value = output_type.model_validate(raw.payload)
                validator(value)
                outputs.append(value)
            except (ValidationError, ValueError) as exc:
                repair_request = StructuredModelRequest(
                    system_prompt=(
                        "Repair this one invalid task. Return only a corrected object and use "
                        "only IDs present in the original request."
                    ),
                    user_prompt=json.dumps(
                        {
                            "original_request": request.user_prompt,
                            "invalid_payload": raw.payload,
                            "validation_error": str(exc)[:1000],
                        },
                        ensure_ascii=False,
                    ),
                    json_schema=output_type.model_json_schema(),
                )
                try:
                    outputs.append(
                        self.typed(
                            tier=tier,
                            execution_tier=lane,
                            stage=stage,
                            request=repair_request,
                            output_type=output_type,
                            validator=validator,
                        )
                    )
                except Exception as repair_exc:
                    outputs.append(repair_exc)
        return outputs

    def _structured(
        self,
        *,
        tier: ModelTier,
        execution_tier: ModelTier | None = None,
        stage: str,
        request: StructuredModelRequest,
        repaired: bool,
    ) -> StructuredModelResult:
        call_stage = f"{stage}_repair" if repaired else stage
        lane = execution_tier or tier
        client, model = self._client_and_model(lane)
        call_id = str(uuid.uuid4())
        self._call_context.model_call_id = call_id
        request = request.model_copy(
            update={
                "metadata": {
                    **request.metadata,
                    "stage": call_stage,
                    "priority": "repair" if repaired else "normal",
                    "model_profile": tier.value,
                    "scheduler_lane": lane.value,
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
                    "attempt_count": scheduled.attempt_count if scheduled else 1,
                    "provider_wait_ms": scheduled.provider_wait_ms if scheduled else 0,
                    "backoff_ms": scheduled.backoff_ms if scheduled else 0,
                    "physical_attempt_count": scheduled.attempt_count if scheduled else 1,
                    "retry_attempt_count": max(0, scheduled.attempt_count - 1) if scheduled else 0,
                    "circuit_state": scheduled.circuit_state if scheduled else "CLOSED",
                    "circuit_wait_ms": scheduled.circuit_wait_ms if scheduled else 0,
                    **_model_error_parse_metadata(exc),
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
                "attempt_count": scheduled.attempt_count if scheduled else 1,
                "provider_wait_ms": scheduled.provider_wait_ms if scheduled else 0,
                "backoff_ms": scheduled.backoff_ms if scheduled else 0,
                "physical_attempt_count": scheduled.attempt_count if scheduled else 1,
                "retry_attempt_count": max(0, scheduled.attempt_count - 1) if scheduled else 0,
                "circuit_state": scheduled.circuit_state if scheduled else "CLOSED",
                "circuit_wait_ms": scheduled.circuit_wait_ms if scheduled else 0,
                "output_hash": _hash_json(result.payload),
                "cache_hit": False,
                **_model_result_transport_metadata(result),
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
        m4_client: StructuredModelClient | None = None,
        package_m4_client: StructuredModelClient | None = None,
        model_m1: str = "qwen3.7-text-embedding",
        model_m2: str = "deepseek-v4-flash",
        model_m3: str = "deepseek-v4-flash",
        model_m4: str | None = None,
        package_model_m4: str = PACKAGE_V3_MODEL,
        hard_cannot_link_mode: str = HardCannotLinkMode.ENFORCE.value,
        n9_wire_protocol: str = "on",
        n9_active_requests: int = 24,
        n9_overlap_batch_packing: bool = False,
        parent_compact_wire_dto: bool = True,
        atomic_enforced_rules: Sequence[str] | None = None,
        knowledge_base: V2KnowledgeBase | None = None,
        package_registry_scope_id: str = "cdecr-default",
        package_v3_batch_size: int = 200,
        package_v3_context_token_budget: int = 100_000,
        package_v3_context_reserve_tokens: int = 8_000,
        package_v3_description_token_budget: int = 32_000,
        package_v3_description_active_requests: int = 16,
        package_v3_reasoning_effort: Literal["none", "low", "high", "max"] = "low",
        package_v3_description_reasoning_effort: Literal[
            "none", "low", "high", "max"
        ] = "none",
        package_v3_strict_output: bool = False,
        atomic_cosine_backend: str = "matrix",
    ) -> None:
        self.registry = registry
        self.embedding_client = embedding_client
        self.m2_client = m2_client
        self.m3_client = m3_client
        self.m4_client = m4_client or m3_client
        self.package_m4_client = package_m4_client or self.m4_client
        self.model_m1 = model_m1
        self.model_m2 = model_m2
        self.model_m3 = model_m3
        self.model_m4 = model_m4 or model_m3
        self.package_model_m4 = package_model_m4
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
        if n9_wire_protocol != "on":
            raise ValueError("N9 optimized dictionary protocol is mandatory")
        self.n9_wire_protocol = n9_wire_protocol
        self.n9_active_requests = max(1, n9_active_requests)
        self.n9_overlap_batch_packing = n9_overlap_batch_packing
        self.parent_compact_wire_dto = parent_compact_wire_dto
        self._embedding_telemetry_by_stage: dict[str, EmbeddingBatchTelemetry] = {}
        self._bulk_audit_degraded = False
        self._bulk_batch_audit_write = False
        self.knowledge_base = knowledge_base or V2KnowledgeBase()
        self.package_registry_scope_id = package_registry_scope_id
        self.package_service = PackageWorkflowV3Service(
            registry=registry,
            batch_size=package_v3_batch_size,
            context_token_budget=package_v3_context_token_budget,
            context_reserve_tokens=package_v3_context_reserve_tokens,
            description_pack_token_budget=package_v3_description_token_budget,
            description_active_requests=package_v3_description_active_requests,
            reasoning_effort=package_v3_reasoning_effort,
            description_reasoning_effort=package_v3_description_reasoning_effort,
            strict_output=package_v3_strict_output,
        )
        self.atomic_cosine_backend = atomic_cosine_backend

    @property
    def model_config(self) -> dict[str, object]:
        return {
            "m1": self.model_m1,
            "m2": self.model_m2,
            "m3": self.model_m3,
            "m4": self.model_m4,
            "atomic_top_k": ATOMIC_TOP_K,
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
            "atomic_assignment_policy_version": ATOMIC_ASSIGNMENT_POLICY_VERSION,
            "n9_overlap_batch_packing": self.n9_overlap_batch_packing,
            "parent_compact_wire_dto": self.parent_compact_wire_dto,
            "package_partition_policy_version": "package-global-registry-v3",
            "package_v3_model": self.package_model_m4,
            "package_v3_reasoning_effort": PACKAGE_V3_REASONING_EFFORT,
            "package_v3_description_reasoning_effort": (
                self.package_service.description_reasoning_effort
            ),
            "hold_policy": "removed",
            "wire_protocol_version": WIRE_PROTOCOL_VERSION,
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
                "v2_catalog_hash": self.knowledge_base.catalog_hash,
                "field_resolver_version": FIELD_RESOLVER_VERSION,
                "field_prompt_version": FIELD_PROMPT_VERSION,
                "candidate_retriever_version": CANDIDATE_RETRIEVER_VERSION,
                "field_resolution_configuration_hash": field_resolution_configuration_hash(
                    catalog_hash=self.knowledge_base.catalog_hash
                ),
                "identity_compiler_version": IDENTITY_COMPILER_VERSION,
                "hard_cannot_link_mode": self.hard_cannot_link_mode.value,
                "atomic_assignment_policy_version": ATOMIC_ASSIGNMENT_POLICY_VERSION,
                "package_partition_policy_version": "package-global-registry-v3",
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
                m4_client=self.m4_client,
                model_m1=self.model_m1,
                model_m2=self.model_m2,
                model_m3=self.model_m3,
                model_m4=self.model_m4,
                summaries=summaries,
            )
            package_models = _AuditedModels(
                registry=self.registry,
                run_id=run_id,
                embedding_client=self.embedding_client,
                m2_client=self.m2_client,
                m3_client=self.m3_client,
                m4_client=self.m4_client,
                responses_m4_client=self.package_m4_client,
                model_m1=self.model_m1,
                model_m2=self.model_m2,
                model_m3=self.model_m3,
                model_m4=self.model_m4,
                responses_model_m4=self.package_model_m4,
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
            if getattr(self, "_last_n9_task_failures", {}):
                raise CrossDocumentPipelineError(
                    "atomic_coreference", "provider_retryable_tasks_pending"
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
            final_atomic_events = self.registry.list_current_atomic_events(limit=10000)
            parent_service = ParentOccurrenceService(
                registry=self.registry,
                compact_wire_dto=self.parent_compact_wire_dto,
            )
            pool = parent_service.build_parent_occurrence_pool(
                events=final_atomic_events,
                mentions=None,
                sources=None,
                existing_packages=self.registry.list_current_packages(limit=10000),
                models=models,
                run_id=run_id,
                persistence_scope_id=f"{self.package_registry_scope_id}:induction",
            )
            if pool.status != "FINALIZED":
                result = CrossDocumentResult(
                    run_id=run_id,
                    processing_key=processing_key,
                    message_id=message_id,
                    status=CrossDocumentStatus.PARTIAL_PARENT_RESOLUTION,
                    atomic_events=atomic_events,
                    packages=[],
                    atomic_assignments=atomic_assignments,
                    package_assignments=[],
                    model_calls=summaries,
                    candidate_counts=candidate_counts,
                    failure_stage="parent_occurrence",
                    error_code="PARTIAL_PARENT_RESOLUTION",
                    started_at=started_at,
                    finished_at=datetime.now(UTC),
                )
                return result
            parent_result = self.package_service.run(
                events=final_atomic_events,
                proposals=pool.proposals,
                external_links=pool.external_links,
                models=package_models,
                run_id=run_id,
                registry_scope_id=self.package_registry_scope_id,
            )
            if parent_result.status != "FINALIZED" or parent_result.partition is None:
                result = CrossDocumentResult(
                    run_id=run_id,
                    processing_key=processing_key,
                    message_id=message_id,
                    status=CrossDocumentStatus.PARTIAL_PARENT_RESOLUTION,
                    atomic_events=atomic_events,
                    packages=[],
                    atomic_assignments=atomic_assignments,
                    package_assignments=[],
                    model_calls=summaries,
                    candidate_counts=candidate_counts,
                    failure_stage="package_v3_registry",
                    error_code="PARTIAL_PACKAGE_REGISTRY",
                    started_at=started_at,
                    finished_at=datetime.now(UTC),
                )
                return result
            existing_packages = self.registry.list_current_packages(limit=10000)
            (
                packages,
                memberships,
                package_assignments,
                external_relations,
                redirects,
            ) = project_frozen_partition_v3(
                parent_result.partition,
                events=final_atomic_events,
                existing_packages=existing_packages,
                run_id=run_id,
            )
            self.registry.activate_package_partition_v3(
                packages=packages,
                memberships=memberships,
                assignments=package_assignments,
                external_relations=external_relations,
                redirects=redirects,
                run_id=run_id,
            )
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
        self,
        mentions: list[EventMention],
        models: _AuditedModels,
        *,
        use_batch_executor: bool = False,
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
        if use_batch_executor and missing:
            generated, telemetry = EmbeddingBatchExecutor().run(
                items=[
                    EmbeddingWorkItem(
                        owner_id=mention.mention_id,
                        text=mention.canonical_proposition,
                        input_hash=hashlib.sha256(
                            mention.canonical_proposition.encode("utf-8")
                        ).hexdigest(),
                    )
                    for mention in missing
                ],
                models=models,
                registry=self.registry,
                owner_kind="event_mention",
                stage="atomic_recall_m1",
            )
            vectors.update(generated)
            self._embedding_telemetry_by_stage["atomic_recall_m1"] = telemetry
            return vectors
        for offset in range(0, len(missing), 8):
            batch = missing[offset : offset + 8]
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
        *,
        use_batch_executor: bool = False,
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
        if use_batch_executor and missing:
            generated, telemetry = EmbeddingBatchExecutor().run(
                items=[
                    EmbeddingWorkItem(
                        owner_id=event.event_id,
                        text=identity_text,
                        input_hash=input_hash,
                    )
                    for event, identity_text, input_hash in missing
                ],
                models=models,
                registry=self.registry,
                owner_kind="atomic_event",
                stage="atomic_identity_embedding",
            )
            vectors.update(generated)
            self._embedding_telemetry_by_stage["atomic_identity_embedding"] = telemetry
            return vectors
        for offset in range(0, len(missing), 8):
            batch = missing[offset : offset + 8]
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
        candidate_started = perf_counter()
        active_events = {
            event.event_id: event for event in self.registry.list_current_atomic_events(limit=10000)
        }
        read_snapshot = StageReadSnapshot.load(
            self.registry,
            events=list(active_events.values()),
            packages=[],
        )
        output: dict[str, list[AtomicCandidate]] = {}
        provisional_by_id: dict[str, tuple[AtomicEvent, EventMention, CompiledMentionIdentity]] = {}
        provisional_index = MultiKeyBoundedIndex(bucket_limit=12)
        identity_compiler = IdentityCompiler(
            registry=self.registry,
            catalog_hash=self.knowledge_base.catalog_hash,
        )
        candidate_cards: dict[str, AtomicCandidateCard] = {}
        audit_buffer: list[DecisionAuditRecord] = []
        exact_cosine_index = (
            ExactCosineRecallIndex(atomic_vectors)
            if self.atomic_cosine_backend == "matrix"
            else None
        )
        matrix_scores_by_mention = (
            exact_cosine_index.scores_many(mention_vectors, chunk_size=128)
            if exact_cosine_index is not None
            else {}
        )

        def build_candidate_card(
            event: AtomicEvent,
            representatives: Sequence[EventMention],
            prepared: Sequence[CompiledMentionIdentity] | None = None,
        ) -> AtomicCandidateCard:
            representative_values = tuple(representatives)
            compiled_values = tuple(prepared or [
                identity_compiler.compile(representative)
                for representative in representative_values
            ])
            sidecars = [
                item.atomic_identity_sidecar
                for item in compiled_values
                if item.atomic_identity_sidecar is not None
            ]
            sidecar = (
                combine_atomic_identity_sidecars(sidecars)
                if sidecars
                else compile_atomic_identity_sidecar(
                    representative_values[0], event.identity_profile
                )
            )
            return AtomicCandidateCard(
                event=event,
                representative_mentions=representative_values,
                representative_compiled=compiled_values,
                representative_sidecar=sidecar,
                resolved_identity_evidence=tuple(
                    evidence
                    for representative in representative_values
                    for evidence in resolved_identity_evidence(self.registry, representative)
                ),
                identity_profile_hash=_hash_json(
                    event.identity_profile.model_dump(mode="json")
                ),
                candidate_root_id=candidate_root_id(self.registry, event.event_id),
                source_ids=frozenset(item.message_id for item in representative_values),
                snapshot_hash=read_snapshot.snapshot_hash,
            )

        for event in active_events.values():
            representatives = [
                read_snapshot.mentions_by_id[mention_id]
                for mention_id in event.representative_mention_ids
                if mention_id in read_snapshot.mentions_by_id
            ]
            if representatives:
                candidate_cards[event.event_id] = build_candidate_card(event, representatives)

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
            candidate_cards[item[0].event_id] = build_candidate_card(
                item[0], [mention], [identity]
            )
            provisional_index.add(item[0].event_id, provisional_keys(mention, identity))
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
                matrix_scores = (
                    matrix_scores_by_mention.get(mention.mention_id, {})
                    if exact_cosine_index is not None
                    else {}
                )
                cutoff = None
                if matrix_scores:
                    ordered_raw = sorted(
                        matrix_scores.items(), key=lambda item: (-item[1], item[0])
                    )
                    cutoff = ordered_raw[min(ATOMIC_TOP_K, len(ordered_raw)) - 1][1]
                for event_id, candidate_vector in atomic_vectors.items():
                    score = (
                        matrix_scores.get(event_id, 0.0)
                        if exact_cosine_index is not None
                        else _cosine(vector, candidate_vector)
                    )
                    if abs(score - EMBEDDING_RECALL_THRESHOLD) < 1e-10 or (
                        cutoff is not None and abs(score - cutoff) < 1e-10
                    ):
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
            incoming_evidence = resolved_identity_evidence(self.registry, mention)
            for event_id, recall_routes in routes.items():
                event = active_events[event_id]
                card = candidate_cards.get(event_id)
                representatives = list(card.representative_mentions) if card is not None else []
                if card is not None:
                    candidate_sidecars[event_id] = card.representative_sidecar
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
                    representative_compiled = (
                        list(card.representative_compiled) if card is not None else []
                    )
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
                    existing_evidence = (
                        list(card.resolved_identity_evidence) if card is not None else []
                    )
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
                        audit_buffer.append(
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
                        candidate_root_id=(
                            card.candidate_root_id
                            if card is not None
                            else candidate_root_id(self.registry, event_id)
                        ),
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
                        or candidate_cards[item.event.event_id].candidate_root_id
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
                audit_sink=audit_buffer.append,
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
                audit_sink=audit_buffer.append,
            )
            output[mention.mention_id] = ranked[:ATOMIC_TOP_K]
            candidate_counts["atomic_recalled"] += len(output[mention.mention_id])
            candidate_counts["atomic_hard_conflict_observed"] += sum(
                bool(observed_by_event.get(item.event.event_id))
                for item in output[mention.mention_id]
            )
            register_provisional(mention, compiled_identity)
        if getattr(self, "_bulk_batch_audit_write", False):
            audit_result = self.registry.append_decision_audits(audit_buffer, chunk_size=512)
        else:
            inserted = sum(self.registry.append_decision_audit(item) for item in audit_buffer)
            audit_result = {
                "inserted": inserted,
                "reused": len(audit_buffer) - inserted,
                "conflicted": 0,
                "degraded": 0,
                "transactions": len(audit_buffer),
            }
        if audit_result["conflicted"] or audit_result["degraded"]:
            self._bulk_audit_degraded = True
        self._last_atomic_candidate_telemetry = {
            "snapshot_load_ms": read_snapshot.load_ms,
            "snapshot_query_count": read_snapshot.query_count,
            "candidate_prepare_ms": round((perf_counter() - candidate_started) * 1000),
            "candidate_pair_count": sum(len(values) for values in output.values()),
            "pair_registry_read_count": 0,
            "audit_buffered_count": len(audit_buffer),
            "audit_transaction_count": audit_result["transactions"],
            "audit_degraded": bool(
                audit_result["conflicted"] or audit_result["degraded"]
            ),
        }
        return output

    def _atomic_decisions(
        self,
        mentions: list[EventMention],
        candidates: dict[str, list[AtomicCandidate]],
        compiled: dict[str, CompiledMentionIdentity],
        models: _AuditedModels,
    ) -> dict[str, AtomicAssignmentDecision]:
        audit_buffer: list[DecisionAuditRecord] = []

        def flush_audits() -> None:
            if getattr(self, "_bulk_batch_audit_write", False):
                result = self.registry.append_decision_audits(audit_buffer, chunk_size=512)
                if result["conflicted"] or result["degraded"]:
                    self._bulk_audit_degraded = True
            else:
                for audit in audit_buffer:
                    self.registry.append_decision_audit(audit)
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
                audit_buffer.append(
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
        frozen_candidate_snapshot = {
            mention.mention_id: tuple(
                item.event.event_id for item in eligible[mention.mention_id]
            )
            for mention in eligible_mentions
        }
        sequential_batches = [
            eligible_mentions[offset : offset + ATOMIC_DECISION_MENTION_BATCH]
            for offset in range(0, len(eligible_mentions), ATOMIC_DECISION_MENTION_BATCH)
        ]
        batches = (
            _pack_mentions_by_candidate_overlap(eligible_mentions, eligible)
            if self.n9_overlap_batch_packing
            else sequential_batches
        )

        def unique_card_count(values: Sequence[Sequence[EventMention]]) -> int:
            return sum(
                len(
                    {
                        candidate.event.event_id
                        for mention in batch
                        for candidate in eligible[mention.mention_id]
                    }
                )
                for batch in values
            )

        overlaps: list[int] = []
        for batch in batches:
            candidate_sets = [
                {item.event.event_id for item in eligible[mention.mention_id]}
                for mention in batch
            ]
            overlaps.append(
                sum(
                    len(left.intersection(right))
                    for index, left in enumerate(candidate_sets)
                    for right in candidate_sets[index + 1 :]
                )
            )
        overlaps.sort()

        def percentile(values: Sequence[int], ratio: float) -> int:
            if not values:
                return 0
            return values[min(len(values) - 1, round((len(values) - 1) * ratio))]

        sequential_unique = unique_card_count(sequential_batches)
        packed_unique = unique_card_count(batches)
        self._last_n9_packing_telemetry = {
            "packing_version": N9_BATCH_PACKING_VERSION,
            "enabled": self.n9_overlap_batch_packing,
            "mention_count": len(eligible_mentions),
            "batch_count": len(batches),
            "batch_size_max": max((len(batch) for batch in batches), default=0),
            "sequential_unique_card_count": sequential_unique,
            "packed_unique_card_count": packed_unique,
            "card_reuse_reduction_ratio": (
                (sequential_unique - packed_unique) / sequential_unique
                if sequential_unique
                else 0.0
            ),
            "batch_overlap_p50": percentile(overlaps, 0.50),
            "batch_overlap_p95": percentile(overlaps, 0.95),
            "mention_exact_coverage": len(
                {mention.mention_id for batch in batches for mention in batch}
            )
            == len(eligible_mentions),
            "candidate_exact_coverage": all(
                tuple(item.event.event_id for item in eligible[mention.mention_id])
                == frozen_candidate_snapshot[mention.mention_id]
                for mention in eligible_mentions
            ),
            "apply_order_hash": _hash_json(
                [
                    mention.mention_id
                    for mention in sorted(
                        eligible_mentions, key=lambda item: item.mention_id
                    )
                ]
            ),
        }

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
        ) -> N9BatchOutcome:
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
            item_provider_failures: dict[str, N9TaskFailure] = {}

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
                *,
                execution_tier: ModelTier | None = None,
            ) -> AtomicDecisionBatch:
                raw = models.typed(
                    tier=tier,
                    execution_tier=execution_tier,
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
                if is_provider_failure(exc):
                    failure = N9TaskFailure(error_code=exc.code, retryable=True)
                    return N9BatchOutcome(
                        batch_index=batch_index,
                        decisions=(),
                        failures={
                            mention.mention_id: failure for mention in batch_mentions
                        },
                    )
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
                ) -> tuple[str, AtomicAssignmentDecision | None, N9TaskFailure | None]:
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
                        task_failure = (
                            N9TaskFailure(error_code=error_code, retryable=True)
                            if isinstance(exc, CrossDocumentPipelineError)
                            and is_provider_failure(exc)
                            else None
                        )
                    else:
                        task_failure = None
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
                    return mention_id, decision, task_failure

                repair_items = sorted(invalid_task_errors.items())
                with ThreadPoolExecutor(max_workers=min(8, len(repair_items))) as repair_pool:
                    repaired_results = list(repair_pool.map(repair_invalid_task, repair_items))
                repaired_by_mention = {
                    mention_id: decision
                    for mention_id, decision, _ in repaired_results
                }
                item_provider_failures.update(
                    {
                        mention_id: failure
                        for mention_id, _, failure in repaired_results
                        if failure is not None
                    }
                )
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
                    output = invoke(
                        ModelTier.M2,
                        "atomic_coreference_escalation",
                        execution_tier=ModelTier.M3,
                    )
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
            return N9BatchOutcome(
                batch_index=batch_index,
                decisions=tuple(merged_decisions),
                failures=item_provider_failures,
            )

        if not batches:
            self._last_n9_task_failures = {}
            flush_audits()
            return decisions
        with ThreadPoolExecutor(max_workers=min(self.n9_active_requests, len(batches))) as executor:
            outputs = list(executor.map(process_batch, enumerate(batches)))
        failures: dict[str, N9TaskFailure] = {}
        for output in sorted(outputs, key=lambda item: item.batch_index):
            decisions.update({item.mention_id: item for item in output.decisions})
            failures.update(output.failures)
        self._last_n9_task_failures = failures
        flush_audits()
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
        chunked_apply: bool = False,
        stage_writer: BulkWriter | None = None,
        apply_checkpoint_context: dict[str, str] | None = None,
        completed_apply_chunks: dict[str, dict[str, object]] | None = None,
    ) -> tuple[list[AtomicEvent], list[AtomicAssignmentRecord]]:
        touched: dict[str, AtomicEvent] = {}
        assignments: list[AtomicAssignmentRecord] = []
        provisional_map: dict[str, str | None] = {}
        stage_records: list[dict[str, object]] = []
        apply_telemetry = {"transactions": 0, "retries": 0, "degraded": 0}
        write_buffer = (
            StageWriteBuffer(stage_writer, default_chunk_size=64)
            if chunked_apply and stage_writer is not None
            else None
        )
        apply_chunk_index = 0

        def collect_write_results() -> None:
            if write_buffer is None:
                return
            for result in write_buffer.barrier():
                for key in apply_telemetry:
                    apply_telemetry[key] += result[key]

        def flush_stage_records(*, wait: bool = False) -> None:
            nonlocal apply_chunk_index
            if not stage_records:
                if wait:
                    collect_write_results()
                return
            records, stage_records[:] = list(stage_records), []
            if apply_checkpoint_context is not None:
                task_id = f"atomic-apply:{apply_chunk_index:06d}"
                identities: list[dict[str, object]] = []
                for record in records:
                    event_record = cast(AtomicEvent | None, record.get("event"))
                    assignment_record = cast(
                        AtomicAssignmentRecord | None, record.get("assignment")
                    )
                    identities.append(
                        {
                            "event": (
                                None
                                if event_record is None
                                else (event_record.event_id, event_record.version)
                            ),
                            "assignment": (
                                None
                                if assignment_record is None
                                else assignment_record.assignment_id
                            ),
                        }
                    )
                input_hash = _hash_json(identities)
                completed = (completed_apply_chunks or {}).get(task_id)
                apply_chunk_index += 1
                if (
                    completed is not None
                    and completed.get("input_hash") == input_hash
                    and completed.get("snapshot_hash")
                    == apply_checkpoint_context["snapshot_hash"]
                ):
                    if wait:
                        collect_write_results()
                    return
                records[0] = {
                    **records[0],
                    "checkpoint": {
                        "epoch_id": apply_checkpoint_context["epoch_id"],
                        "stage": "ATOMIC_APPLY",
                        "task_id": task_id,
                        "input_hash": input_hash,
                        "snapshot_hash": apply_checkpoint_context["snapshot_hash"],
                        "decision_ref": {"row_count": len(records)},
                    },
                }
            if write_buffer is not None:
                frozen_records = tuple(records)
                write_buffer.submit(
                    partial(
                        self.registry.save_atomic_stage_batch,
                        frozen_records,
                        chunk_size=len(frozen_records),
                    )
                )
                if wait:
                    collect_write_results()
            else:
                result = self.registry.save_atomic_stage_batch(records, chunk_size=64)
                for key in apply_telemetry:
                    apply_telemetry[key] += result[key]
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
                if chunked_apply:
                    stage_records.append({"assignment": record})
                else:
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
                # A previously materialized provisional candidate may have been absorbed as a
                # singleton earlier in this same Apply pass.  Always write through the redirect
                # root; otherwise later Mentions keep extending the redirected source Atomic and
                # disappear from the active Atomic/Package view.
                candidate_event_id, target = _active_atomic_target(
                    self.registry, touched, candidate_event_id
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
                duplicate_absorption_requested = bool(
                    possible_duplicate_atomic_ids if action is AtomicAction.MERGE else []
                )
                event_persisted = False
                if duplicate_absorption_requested:
                    if chunked_apply:
                        flush_stage_records(wait=True)
                    self.registry.save_atomic_event(event)
                    event_persisted = True
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
                    event_persisted = True
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
                event_persisted = False
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
            assignment_audit = DecisionAuditRecord(
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
            if chunked_apply:
                stage_records.append(
                    {
                        "event": None if event_persisted else event,
                        "assignment": record,
                        "audits": [assignment_audit],
                    }
                )
                if len(stage_records) >= 64:
                    flush_stage_records()
            else:
                if not event_persisted:
                    self.registry.save_atomic_event(event)
                self.registry.save_atomic_assignment(record)
                self.registry.append_decision_audit(assignment_audit)
            assignments.append(record)
        if chunked_apply:
            flush_stage_records(wait=True)
            self._last_atomic_apply_telemetry = apply_telemetry
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
