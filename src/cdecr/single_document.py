"""Single-document Dreamer → Grounder → mandatory M4 Judge orchestration."""

from __future__ import annotations

import hashlib
import json
import re
import traceback
import uuid
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from importlib import resources
from typing import Literal, TypeVar, cast

from pydantic import ValidationError

from cdecr.contracts import (
    AssertionState,
    EventFamily,
    EventMention,
    EventTime,
    EvidenceRecord,
    EvidenceRecordStatus,
    EvidenceSpan,
    OpenAttribute,
    Participant,
    ParticipantRole,
    Quantity,
    QuantityRole,
    SourceMessage,
    StrictModel,
    TimePrecision,
)
from cdecr.mention_finalization import FINALIZATION_VERSION, MentionFinalizer
from cdecr.model_boundary import model_datetime
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
    ResponsesModelClient,
    ResponsesModelRequest,
    StructuredModelClient,
    StructuredModelRequest,
    StructuredModelResult,
)
from cdecr.preprocessing import (
    PIPELINE_VERSION,
    exact_document_fingerprint,
    grounder_context,
    locate_unique_evidence_text,
    locator_to_evidence,
    preprocess_source,
    reconcile_evidence_text,
)
from cdecr.provider_resilience import classify_provider_error, is_provider_failure
from cdecr.relevance_filter import (
    RELEVANCE_PROMPT_VERSION,
    CandidateGateDecision,
    RelevanceMode,
    TypedInvocation,
    dreamer_block_exposed_lengths,
    dreamer_block_request,
    relevance_response_request,
    responses_request_from_structured,
    select_candidates_fail_open,
    target_profile_for_source,
    ticker_target_instruction,
    validate_relevance_coverage,
)
from cdecr.scheduler import take_scheduled_call_metrics
from cdecr.single_document_contracts import (
    DocumentBlock,
    DreamCandidate,
    DreamerModelOutput,
    EvidenceLocator,
    EvidenceText,
    FailureSummary,
    GroundedMentionDraft,
    GroundedMentionDraftInput,
    GrounderModelOutput,
    GrounderOutput,
    JudgeAcceptedCommand,
    JudgeAction,
    JudgeAttributeMergeCommand,
    JudgeCommandOutput,
    JudgeDecisionRecord,
    JudgeDraftInput,
    JudgeDuplicateCommand,
    JudgeMentionChanges,
    JudgeMentionDraft,
    JudgeRejectedCommand,
    JudgeRouting,
    JudgeSplitCommand,
    MentionDraft,
    ModelCallSummary,
    NormalizationDecision,
    OpenAttributeDraft,
    PreprocessedDocument,
    ProcessingStatus,
    RejectedCandidateDraft,
    RejectedCandidateRecord,
    SingleDocumentResult,
    normalize_event_time_semantics,
    validate_event_time_semantics,
)

PROMPT_VERSION = "single-document-prompts-v20-selective-recovery"
GROUNDER_CANDIDATE_BATCH = 24
JUDGE_DRAFT_BATCH = 24
_T = TypeVar("_T", bound=StrictModel)
GrounderNormalizationStatus = Literal[
    "UNCHANGED",
    "NORMALIZED_VALID",
    "NORMALIZED_STILL_INVALID",
    "UNSAFE_TO_NORMALIZE",
]
_PLAIN_NUMBER = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
GROUNDER_NORMALIZATION_POLICY_VERSION = "grounder-safe-shape-normalization-v2-shadow-outcome"


class SingleDocumentPipelineError(RuntimeError):
    def __init__(self, stage: str, code: str, *, repair_payload: str | None = None) -> None:
        self.stage = stage
        self.code = code
        self.repair_payload = repair_payload
        super().__init__(f"single-document stage {stage} failed: {code}")


def _hash_json(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _structured_request_metadata(request: StructuredModelRequest) -> dict[str, object]:
    try:
        payload: object = json.loads(request.user_prompt)
    except json.JSONDecodeError:
        payload = None
    request_item_count = 1
    candidate_count = 0
    if isinstance(payload, dict):
        for key in ("drafts", "mentions", "events", "pairs", "tasks", "candidates"):
            value = payload.get(key)
            if isinstance(value, (list, dict)):
                request_item_count = max(request_item_count, len(value))
                if "candidate" in key:
                    candidate_count = max(candidate_count, len(value))
    metadata = request.metadata
    return {
        "batch_key": _hash_json({"schema": request.json_schema, "user": request.user_prompt})[:24],
        "request_item_count": request_item_count,
        "candidate_count": candidate_count,
        "request_payload_bytes": len(request.user_prompt.encode("utf-8")),
        "request_transport": metadata.get("transport"),
        "request_reasoning_effort": metadata.get("reasoning_effort"),
        "request_previous_response_id": metadata.get("previous_response_id"),
        "request_session_cache": metadata.get("session_cache"),
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


def _published_at_model(value: datetime) -> str:
    return model_datetime(value)


def _compact_model_schema(schema: dict[str, object]) -> dict[str, object]:
    """Keep the internal schema intact; the provider adapter compacts the wire copy."""

    return schema


def _judge_mention(mention: MentionDraft) -> JudgeMentionDraft:
    return JudgeMentionDraft.model_validate(
        mention.model_dump(mode="json")
    )


def _judge_mention_to_persistent(mention: JudgeMentionDraft) -> MentionDraft:
    return MentionDraft.model_validate(mention.model_dump(mode="json"))


def _apply_judge_changes(mention: MentionDraft, changes: JudgeMentionChanges) -> MentionDraft:
    payload = mention.model_dump(mode="json")
    for field_name in changes.model_fields_set:
        payload[field_name] = getattr(changes, field_name)
    return MentionDraft.model_validate(payload)


def _prompt(name: str) -> str:
    return resources.files("cdecr.prompts.v1").joinpath(name).read_text(encoding="utf-8").strip()


def _repaired_validation_error_code(exc: ValidationError | ValueError) -> str:
    if isinstance(exc, ValidationError):
        return "schema_validation_failed_after_repair"
    message = str(exc).casefold()
    if any(token in message for token in ("evidence", "segment", "locator", "offset", "slice")):
        return "evidence_validation_failed_after_repair"
    return "semantic_validation_failed_after_repair"


def _safe_semantic_error_code(exc: ValueError) -> str:
    message = str(exc).casefold()
    mappings = (
        ("multiple_primary_metrics", "MULTIPLE_PRIMARY_METRICS"),
        ("guidance_assertion_conflict", "GUIDANCE_ASSERTION_CONFLICT"),
        ("opposing_core_actions", "OPPOSING_CORE_ACTIONS"),
        ("opposing_subject_actions", "OPPOSING_SUBJECT_ACTIONS"),
        ("likely_fragmentation", "LIKELY_FRAGMENTATION"),
        ("generic_umbrella_duplicate", "GENERIC_UMBRELLA_DUPLICATE"),
        ("unknown evidence segment", "evidence_unknown_segment"),
        ("does not occur", "evidence_text_not_found"),
        ("ambiguous within", "evidence_text_ambiguous"),
        ("outside its source segment", "evidence_segment_offset_out_of_bounds"),
        ("does not match segment slice", "evidence_segment_slice_mismatch"),
        ("outside the source field", "evidence_source_offset_out_of_bounds"),
        ("does not match the source slice", "evidence_source_slice_mismatch"),
        ("dreamer evidence must use", "evidence_not_exposed_to_dreamer"),
        ("unknown dreamer candidate", "grounder_unknown_candidate"),
        ("judge must return exactly", "judge_incomplete_decisions"),
        ("judge target must name", "judge_invalid_target"),
    )
    return next((code for token, code in mappings if token in message), "semantic_constraint")


def _validation_business_code(
    *,
    error_type: str,
    message: str,
    location: Sequence[object],
) -> str:
    normalized = message.casefold()
    if "metric-bearing mention requires exactly one primary quantity" in normalized:
        return "PRIMARY_QUANTITY_COUNT"
    if "time without event bounds must use unknown precision" in normalized:
        return "TIME_PRECISION_WITHOUT_BOUNDS"
    if "evidence" in normalized or any("evidence" in str(part).casefold() for part in location):
        return "EVIDENCE_FIELD_SHAPE"
    if error_type == "missing":
        return "MISSING_REQUIRED_FIELD"
    if error_type == "extra_forbidden":
        return "EXTRA_FIELD"
    if error_type in {"enum", "literal_error"}:
        return "INVALID_ENUM"
    if any(token in error_type for token in ("string_pattern", "string_too_short")) and any(
        token in str(part).casefold() for part in location for token in ("id", "candidate")
    ):
        return "INVALID_SHORT_ID"
    if error_type.startswith("value_error"):
        return "BUSINESS_CONSTRAINT"
    return "OTHER_SCHEMA_ERROR"


def _safe_validation_errors(
    exc: ValidationError,
    *,
    limit: int = 100,
) -> list[dict[str, object]]:
    """Serialize Pydantic diagnostics without leaking input, ctx exceptions, or tracebacks."""

    safe: list[dict[str, object]] = []
    for error in exc.errors(include_input=False, include_url=False)[:limit]:
        location = [str(part) for part in error.get("loc", ())]
        error_type = str(error.get("type", "value_error"))
        message = str(error.get("msg", "validation failed"))[:240]
        safe.append(
            {
                "field_path": location,
                "type": error_type,
                "code": _validation_business_code(
                    error_type=error_type,
                    message=message,
                    location=location,
                ),
                "message": message,
            }
        )
    return safe


def normalize_grounder_draft_shape(
    value: object,
) -> tuple[object, GrounderNormalizationStatus, list[str]]:
    """Apply only representation-preserving fixes before Grounder validation."""

    if not isinstance(value, dict):
        return value, "UNSAFE_TO_NORMALIZE", []
    normalized = json.loads(json.dumps(value, ensure_ascii=False))
    rules: list[str] = []
    mention = normalized.get("mention")
    if not isinstance(mention, dict):
        return normalized, "UNCHANGED", rules

    aliases = (("evidence", "evidence_locations"),)
    for old, new in aliases:
        if old not in mention:
            continue
        if new in mention and mention[new] != mention[old]:
            return value, "UNSAFE_TO_NORMALIZE", [f"ALIAS_CONFLICT:{old}:{new}"]
        mention.setdefault(new, mention.pop(old))
        rules.append(f"ALIAS:{old}:{new}")

    evidence_values: list[object] = []
    main_evidence = mention.get("evidence_locations")
    if isinstance(main_evidence, list):
        evidence_values.extend(main_evidence)
    open_attributes = mention.get("open_attributes")
    if isinstance(open_attributes, list):
        evidence_values.extend(
            attribute.get("evidence_location")
            for attribute in open_attributes
            if isinstance(attribute, dict)
        )
    for evidence in evidence_values:
        if not isinstance(evidence, dict):
            continue
        for key in ("start_char", "end_char"):
            if key in evidence:
                evidence.pop(key)
                rules.append(f"DROP_EVIDENCE_{key.upper()}")
        for old, new in (("segment_ref", "segment_id"), ("quote", "text")):
            if old not in evidence:
                continue
            if new in evidence and evidence[new] != evidence[old]:
                return value, "UNSAFE_TO_NORMALIZE", [f"ALIAS_CONFLICT:{old}:{new}"]
            evidence.setdefault(new, evidence.pop(old))
            rules.append(f"ALIAS:{old}:{new}")

    quantities = mention.get("quantities")
    if isinstance(quantities, list):
        for quantity in quantities:
            if not isinstance(quantity, dict):
                continue
            raw_number = quantity.get("value")
            if isinstance(raw_number, str) and _PLAIN_NUMBER.fullmatch(raw_number):
                quantity["value"] = (
                    float(raw_number) if "." in raw_number else int(raw_number)
                )
                rules.append("NUMERIC_STRING_TO_NUMBER")

    if mention.get("source_claim") == "":
        mention["source_claim"] = None
        rules.append("EMPTY_SOURCE_CLAIM_TO_NULL")
    event_time = mention.get("time")
    if isinstance(event_time, dict):
        for key in ("event_start", "event_end", "reference_period_id"):
            if event_time.get(key) == "":
                event_time[key] = None
                rules.append(f"EMPTY_{key.upper()}_TO_NULL")

    if not rules:
        return normalized, "UNCHANGED", []
    try:
        GroundedMentionDraftInput.model_validate(normalized)
    except ValidationError:
        return normalized, "NORMALIZED_STILL_INVALID", sorted(set(rules))
    return normalized, "NORMALIZED_VALID", sorted(set(rules))


def _grounder_primary_shadow_candidate(value: object) -> tuple[bool, str]:
    """Recognize only the no-choice, one-quantity PRIMARY repair in shadow mode."""

    if not isinstance(value, dict) or not isinstance(value.get("mention"), dict):
        return False, "DRAFT_SHAPE_UNAVAILABLE"
    mention = value["mention"]
    quantities = mention.get("quantities")
    if not isinstance(quantities, list) or len(quantities) != 1:
        return False, "QUANTITY_COUNT_NOT_ONE"
    quantity = quantities[0]
    if not isinstance(quantity, dict):
        return False, "QUANTITY_SHAPE_UNAVAILABLE"
    metric_id = quantity.get("metric_id")
    if not isinstance(metric_id, str) or not metric_id or metric_id == "UNKNOWN_METRIC":
        return False, "PRIMARY_METRIC_NOT_EXPLICIT"
    if str(quantity.get("role", "")).upper() == "PRIMARY":
        return False, "ALREADY_PRIMARY"
    proposed = json.loads(json.dumps(value, ensure_ascii=False))
    proposed["mention"]["quantities"][0]["role"] = "PRIMARY"
    try:
        GroundedMentionDraftInput.model_validate(proposed)
    except ValidationError:
        return False, "PROPOSED_DRAFT_STILL_INVALID"
    return True, "SINGLE_EXPLICIT_METRIC_QUANTITY"


def _grounder_primary_shadow_proposal(value: object) -> object | None:
    safe, _ = _grounder_primary_shadow_candidate(value)
    if not safe:
        return None
    proposed = json.loads(json.dumps(value, ensure_ascii=False))
    proposed["mention"]["quantities"][0]["role"] = "PRIMARY"
    return dict(proposed)


def _grounder_shadow_projection(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict) or not isinstance(value.get("mention"), dict):
        return None
    mention = value["mention"]
    return {
        "source_candidate_ids": sorted(value.get("source_candidate_ids") or []),
        "canonical_proposition": mention.get("canonical_proposition"),
        "event_family": mention.get("event_family"),
        "predicate": mention.get("predicate"),
        "participants": mention.get("participants"),
        "quantities": mention.get("quantities"),
        "time": mention.get("time"),
        "assertion_state": mention.get("assertion_state"),
        "evidence_spans": mention.get("evidence_spans"),
        "local_package_hint": mention.get("local_package_hint"),
        "relation_to_anchor": mention.get("relation_to_anchor"),
    }


def _grounder_shadow_changed_fields(
    proposed: dict[str, object] | None,
    repaired: dict[str, object] | None,
) -> list[str]:
    if proposed is None or repaired is None:
        return ["OUTPUT_SHAPE"]
    return sorted(
        key for key in proposed.keys() | repaired.keys() if proposed.get(key) != repaired.get(key)
    )


def _mention_semantic_codes(mention: MentionDraft) -> set[str]:
    codes: set[str] = set()
    primary_count = sum(quantity.role is QuantityRole.PRIMARY for quantity in mention.quantities)
    if mention.quantities and primary_count != 1:
        codes.add("MULTIPLE_PRIMARY_METRICS")
    predicate = mention.predicate.normalized.casefold()
    proposition = mention.canonical_proposition.casefold()
    if (
        mention.event_family is EventFamily.GUIDANCE_EXPECTATION
        and mention.assertion_state is AssertionState.ACTUAL
        and re.search(r"\bguide(?:_|)metric\b", predicate)
    ):
        codes.add("GUIDANCE_ASSERTION_CONFLICT")
    opposing_pairs = (
        (r"\brais(?:e|ed|ing)\b", r"\blower(?:ed|ing)?\b|\bcut\b"),
        (r"\bincreas(?:e|ed|ing)\b", r"\bdecreas(?:e|ed|ing)\b"),
        (r"\bbuy(?:s|ing)?\b|\bbought\b", r"\bsell(?:s|ing)?\b|\bsold\b"),
        (r"\binflow\b", r"\boutflow\b"),
        (r"\benter(?:ed|ing)?\b", r"\bexit(?:ed|ing)?\b"),
    )
    opposing = any(
        re.search(left, proposition) and re.search(right, proposition)
        for left, right in opposing_pairs
    )
    if opposing:
        codes.add("OPPOSING_CORE_ACTIONS")
        core_subjects = {
            participant.surface.casefold()
            for participant in mention.participants
            if participant.role in {ParticipantRole.SUBJECT, ParticipantRole.ACTOR}
        }
        if len(core_subjects) > 1:
            codes.add("OPPOSING_SUBJECT_ACTIONS")
    return codes


def _judge_semantic_failures(
    output: JudgeCommandOutput,
    draft_by_short: dict[str, GroundedMentionDraft],
    validated_accepted: dict[str, MentionDraft],
    validated_splits: dict[str, list[MentionDraft]],
) -> dict[str, set[str]]:
    failures: dict[str, set[str]] = {}
    final_mentions: list[tuple[str, set[str], MentionDraft]] = []
    for accepted_command in output.accepted:
        original = draft_by_short.get(accepted_command.id)
        if original is None:
            continue
        mention = validated_accepted.get(accepted_command.id, original.mention)
        codes = _mention_semantic_codes(mention)
        if codes:
            failures.setdefault(accepted_command.id, set()).update(codes)
        final_mentions.append((accepted_command.id, set(original.source_candidate_ids), mention))
    for split_command in output.split:
        replacements = validated_splits.get(split_command.id)
        original = draft_by_short.get(split_command.id)
        if replacements is None or original is None:
            continue
        signatures: set[str] = set()
        for mention in replacements:
            failures.setdefault(split_command.id, set()).update(_mention_semantic_codes(mention))
            primary_metric = next(
                (
                    quantity.metric_id
                    for quantity in mention.quantities
                    if quantity.role is QuantityRole.PRIMARY
                ),
                None,
            )
            signature = _hash_json(
                {
                    "subjects": sorted(
                        participant.surface.casefold()
                        for participant in mention.participants
                        if participant.role in {ParticipantRole.SUBJECT, ParticipantRole.ACTOR}
                    ),
                    "predicate": mention.predicate.normalized,
                    "primary_metric": primary_metric,
                    "assertion": mention.assertion_state.value,
                    "time": mention.time.model_dump(mode="json"),
                }
            )
            if signature in signatures:
                failures.setdefault(split_command.id, set()).add("LIKELY_FRAGMENTATION")
            signatures.add(signature)
            final_mentions.append((split_command.id, set(original.source_candidate_ids), mention))
    for index, (left_id, left_lineage, left) in enumerate(final_mentions):
        if left.quantities or not re.search(
            r"\b(report(?:ed)?|disclos(?:e|ed))\b.*\b(earnings|results)\b",
            left.canonical_proposition,
            re.I,
        ):
            continue
        if re.search(
            r"\b(filing|release|agreement|sign(?:ed|ing))\b",
            left.canonical_proposition,
            re.I,
        ):
            continue
        left_subjects = {
            participant.surface.casefold()
            for participant in left.participants
            if participant.role in {ParticipantRole.SUBJECT, ParticipantRole.ACTOR}
        }
        for right_index, (_, right_lineage, right) in enumerate(final_mentions):
            if right_index == index:
                continue
            right_subjects = {
                participant.surface.casefold()
                for participant in right.participants
                if participant.role in {ParticipantRole.SUBJECT, ParticipantRole.ACTOR}
            }
            if (
                left_lineage.intersection(right_lineage)
                and right.quantities
                and left_subjects
                and left_subjects == right_subjects
            ):
                failures.setdefault(left_id, set()).add("GENERIC_UMBRELLA_DUPLICATE")
    return {item_id: codes for item_id, codes in failures.items() if codes}


def _judge_semantic_codes(
    output: JudgeCommandOutput,
    draft_by_short: dict[str, GroundedMentionDraft],
    validated_accepted: dict[str, MentionDraft],
    validated_splits: dict[str, list[MentionDraft]],
) -> set[str]:
    return {
        code
        for codes in _judge_semantic_failures(
            output,
            draft_by_short,
            validated_accepted,
            validated_splits,
        ).values()
        for code in codes
    }


def deterministic_mention_id(
    message_id: str, draft: MentionDraft, evidence: Sequence[object]
) -> str:
    evidence_payload = [
        item.model_dump(mode="json") if isinstance(item, StrictModel) else item for item in evidence
    ]
    digest = _hash_json(
        {
            "processing_version": {
                "pipeline": PIPELINE_VERSION,
                "prompt": PROMPT_VERSION,
            },
            "message_id": message_id,
            "evidence": evidence_payload,
            "predicate": draft.predicate.normalized,
            "assertion_state": draft.assertion_state.value,
        }
    )
    return f"mention:{digest}"


def _evidence_record_status(exc: ValueError) -> EvidenceRecordStatus:
    message = str(exc).casefold()
    if "unknown evidence segment" in message:
        return EvidenceRecordStatus.SEGMENT_NOT_FOUND
    if any(token in message for token in ("offset", "outside")):
        return EvidenceRecordStatus.OFFSET_INVALID
    if any(token in message for token in ("slice", "match")):
        return EvidenceRecordStatus.TEXT_MISMATCH
    return EvidenceRecordStatus.TEXT_NOT_FOUND


class _AuditedEmbeddingClient:
    def __init__(
        self,
        *,
        client: EmbeddingClient,
        registry: CDECRRegistry,
        run_id: str,
        model: str,
        stage: str,
        summaries: list[ModelCallSummary],
    ) -> None:
        self.client = client
        self.registry = registry
        self.run_id = run_id
        self.model = model
        self.stage = stage
        self.summaries = summaries

    def embed(self, texts: Sequence[str]) -> EmbeddingResult:
        call_id = str(uuid.uuid4())
        input_hash = _hash_json(list(texts))
        payload_bytes = sum(len(value.encode("utf-8")) for value in texts)
        try:
            result = self.client.embed(texts)
        except Exception as exc:
            scheduled = take_scheduled_call_metrics(self.client)
            latency = exc.latency_ms if isinstance(exc, ModelAdapterError) else 0
            code = exc.code if isinstance(exc, ModelAdapterError) else type(exc).__name__
            self.registry.record_model_call(
                model_call_id=call_id,
                run_id=self.run_id,
                tier=ModelTier.M1.value,
                model=self.model,
                status="FAILED",
                input_tokens=exc.input_tokens if isinstance(exc, ModelAdapterError) else None,
                output_tokens=exc.output_tokens if isinstance(exc, ModelAdapterError) else None,
                latency_ms=latency,
                error_code=code,
                metadata={
                    "attempt": "initial",
                    "request_item_count": len(texts),
                    "request_payload_bytes": payload_bytes,
                    "queue_wait_ms": scheduled.queue_wait_ms if scheduled else 0,
                    "cache_hit": False,
                },
                stage=self.stage,
                prompt_version=PROMPT_VERSION,
                input_hash=input_hash,
            )
            self.summaries.append(
                ModelCallSummary(
                    stage=self.stage,
                    tier="m1",
                    model=self.model,
                    latency_ms=latency,
                    status="FAILED",
                    error_code=code,
                    queue_wait_ms=scheduled.queue_wait_ms if scheduled else 0,
                    request_item_count=len(texts),
                    request_payload_bytes=payload_bytes,
                )
            )
            raise SingleDocumentPipelineError(self.stage, code) from exc
        scheduled = take_scheduled_call_metrics(self.client)
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
            stage=self.stage,
            prompt_version=PROMPT_VERSION,
            input_hash=input_hash,
        )
        self.summaries.append(
            ModelCallSummary(
                stage=self.stage,
                tier="m1",
                model=result.model,
                input_tokens=result.input_tokens,
                latency_ms=result.latency_ms,
                queue_wait_ms=scheduled.queue_wait_ms if scheduled else 0,
                request_item_count=len(texts),
                request_payload_bytes=payload_bytes,
            )
        )
        return result


class _ResponsesStructuredAdapter:
    """Present one Responses conversation turn through the existing typed invocation path."""

    def __init__(
        self,
        *,
        client: ResponsesModelClient,
        reasoning_effort: Literal["none", "low", "high", "max"],
        previous_response_id: str | None = None,
        preset_request: ResponsesModelRequest | None = None,
    ) -> None:
        self.client = client
        self.reasoning_effort = reasoning_effort
        self.previous_response_id = previous_response_id
        self.preset_request = preset_request

    def complete(self, request: StructuredModelRequest) -> StructuredModelResult:
        response_request = (
            self.preset_request.model_copy(update={"metadata": request.metadata})
            if self.preset_request is not None
            else responses_request_from_structured(
                request,
                previous_response_id=self.previous_response_id,
                reasoning_effort=self.reasoning_effort,
            )
        )
        return self.client.complete_response(response_request)

    def take_last_call_metrics(self) -> object | None:
        getter = getattr(self.client, "take_last_call_metrics", None)
        return getter() if callable(getter) else None


class _AuditedStructuredClient:
    def __init__(
        self,
        *,
        client: StructuredModelClient,
        registry: CDECRRegistry,
        run_id: str,
        tier: ModelTier,
        model: str,
        stage: str,
        summaries: list[ModelCallSummary],
    ) -> None:
        self.client = client
        self.registry = registry
        self.run_id = run_id
        self.tier = tier
        self.model = model
        self.stage = stage
        self.summaries = summaries
        self.repair_invocations = 0
        self.last_result: StructuredModelResult | None = None

    def complete(self, request: StructuredModelRequest) -> StructuredModelResult:
        repaired = request.system_prompt.casefold().startswith("repair")
        if repaired:
            self.repair_invocations += 1
        call_stage = f"{self.stage}_repair" if repaired else self.stage
        request = request.model_copy(
            update={
                "metadata": {
                    **request.metadata,
                    "stage": call_stage,
                    "priority": "repair" if repaired else "normal",
                }
            }
        )
        call_id = str(uuid.uuid4())
        input_hash = _hash_json({"system": request.system_prompt, "user": request.user_prompt})
        schema_hash = _hash_json(request.json_schema)
        request_metadata = _structured_request_metadata(request)
        try:
            result = self.client.complete(request)
        except Exception as exc:
            scheduled = take_scheduled_call_metrics(self.client)
            latency = exc.latency_ms if isinstance(exc, ModelAdapterError) else 0
            code = exc.code if isinstance(exc, ModelAdapterError) else type(exc).__name__
            self.registry.record_model_call(
                model_call_id=call_id,
                run_id=self.run_id,
                tier=self.tier.value,
                model=self.model,
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
                    tier=self.tier.value,
                    model=self.model,
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
                )
            )
            raise SingleDocumentPipelineError(
                call_stage,
                code,
                repair_payload=(
                    exc.raw_response_text if isinstance(exc, ModelAdapterError) else None
                ),
            ) from exc
        scheduled = take_scheduled_call_metrics(self.client)
        self.last_result = result
        self.registry.record_model_call(
            model_call_id=call_id,
            run_id=self.run_id,
            tier=self.tier.value,
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
                "cache_hit": bool(result.cached_input_tokens),
                "cached_input_tokens": result.cached_input_tokens,
                "response_id": result.response_id,
                "reasoning_tokens": result.reasoning_tokens,
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
                tier=self.tier.value,
                model=result.model,
                input_tokens=result.input_tokens,
                cached_input_tokens=result.cached_input_tokens,
                output_tokens=result.output_tokens,
                latency_ms=result.latency_ms,
                repaired=repaired,
                queue_wait_ms=scheduled.queue_wait_ms if scheduled else 0,
                request_item_count=_metadata_int(request_metadata, "request_item_count"),
                candidate_count=_metadata_int(request_metadata, "candidate_count"),
                request_payload_bytes=_metadata_int(request_metadata, "request_payload_bytes"),
            )
        )
        return result


class SingleDocumentProcessor:
    def __init__(
        self,
        *,
        registry: CDECRRegistry,
        embedding_client: EmbeddingClient,
        m2_client: StructuredModelClient,
        m3_client: StructuredModelClient,
        m4_client: StructuredModelClient,
        dreamer_responses_client: ResponsesModelClient | None = None,
        relevance_responses_client: ResponsesModelClient | None = None,
        relevance_filter_mode: RelevanceMode | Literal["off", "shadow", "enforce"] = (
            RelevanceMode.ENFORCE
        ),
        relevance_target_profiles: Mapping[str, str] | None = None,
        model_m1: str = "qwen3.7-text-embedding",
        model_m2: str = "qwen3.8-flash",
        model_m3: str = "qwen3.8-flash",
        model_m4: str = "qwen3.8-flash",
        document_workers: int = 3,
        document_block_concurrency: int = 3,
        document_concurrency: int | None = None,
        grounder_safe_normalization: bool = True,
        grounder_primary_normalization: Literal["off", "shadow"] = "shadow",
    ) -> None:
        self.registry = registry
        self.embedding_client = embedding_client
        self.m2_client = m2_client
        self.m3_client = m3_client
        self.m4_client = m4_client
        self.dreamer_responses_client = dreamer_responses_client or cast(
            ResponsesModelClient, m2_client
        )
        self.relevance_responses_client = relevance_responses_client or cast(
            ResponsesModelClient, m2_client
        )
        self.relevance_filter_mode = RelevanceMode(relevance_filter_mode)
        self.relevance_target_profiles = dict(relevance_target_profiles or {})
        self.grounder_safe_normalization = grounder_safe_normalization
        self.grounder_primary_normalization = grounder_primary_normalization
        self.model_m1 = model_m1
        self.model_m2 = model_m2
        self.model_m3 = model_m3
        self.model_m4 = model_m4
        # Keep the old constructor keyword as a compatibility alias, but do not let
        # document-level fan-out implicitly multiply per-document block concurrency.
        if document_concurrency is not None:
            document_workers = document_concurrency
            document_block_concurrency = document_concurrency
        self.document_workers = max(1, document_workers)
        self.document_block_concurrency = max(1, document_block_concurrency)
        self._document_block_executor = ThreadPoolExecutor(
            max_workers=self.document_block_concurrency,
            thread_name_prefix="cdecr-document-block",
        )

    def close(self) -> None:
        self._document_block_executor.shutdown(wait=True)

    @property
    def model_config(self) -> dict[str, object]:
        return {
            "m1": self.model_m1,
            "m2": self.model_m2,
            "m3": self.model_m3,
            "m4": self.model_m4,
            "embedding_dimensions": 1024,
            "grounder_tier": "m3",
            "judge_tier": "m4",
            "dreamer_transport": "responses",
            "dreamer_reasoning_effort": "none",
            "relevance_filter": {
                "mode": self.relevance_filter_mode.value,
                "prompt_version": RELEVANCE_PROMPT_VERSION,
                "transport": "responses_json_object",
                "reasoning_effort": "none",
                "previous_response_id": None,
                "session_cache": False,
                "target_profiles": self.relevance_target_profiles,
            },
            "grounder_safe_normalization": self.grounder_safe_normalization,
            "grounder_primary_normalization": self.grounder_primary_normalization,
            "grounder_normalization_policy_version": (
                GROUNDER_NORMALIZATION_POLICY_VERSION
            ),
            "structured_output_mode": STRUCTURED_OUTPUT_MODE,
            "reasoning_effort": STRUCTURED_REASONING_EFFORT,
            "schema_projection": "disabled",
        }

    def processing_key(self, source: SourceMessage) -> str:
        return _hash_json(
            {
                "message_id": source.message_id,
                "source_fingerprint": exact_document_fingerprint(source),
                "pipeline_version": PIPELINE_VERSION,
                "prompt_version": PROMPT_VERSION,
                "catalog_version": FINALIZATION_VERSION,
                "model_config": self.model_config,
            }
        )

    def _normalize_draft_time(
        self,
        draft: MentionDraft,
        *,
        run_id: str,
        stage: str,
        subject_id: str,
    ) -> MentionDraft:
        normalized, details = normalize_event_time_semantics(draft.time)
        if details is None:
            return draft
        updated = draft.model_copy(update={"time": normalized})
        before_hash = _hash_json(draft.model_dump(mode="json"))
        after_hash = _hash_json(updated.model_dump(mode="json"))
        self.registry.append_decision_audit(
            DecisionAuditRecord(
                audit_id=(
                    f"time-semantic-normalization:{run_id}:{stage}:{subject_id}:{before_hash[:16]}"
                ),
                run_id=run_id,
                decision_type="TIME_SEMANTIC_NORMALIZATION",
                subject_id=subject_id,
                payload={
                    "stage": stage,
                    **details,
                    "object_hash_before": before_hash,
                    "object_hash_after": after_hash,
                },
            )
        )
        return updated

    def _reconcile_draft_evidence(
        self,
        draft: MentionDraft,
        document: PreprocessedDocument,
        source: SourceMessage,
        *,
        run_id: str,
        stage: str,
        subject_id: str,
        source_candidate_ids: Sequence[str],
        candidate_anchors: Sequence[EvidenceLocator] = (),
        fallback_main_evidence: Sequence[EvidenceText] | None = None,
    ) -> MentionDraft:
        # Evidence is model-authored source data. Location is best-effort metadata:
        # never rewrite, drop, or trigger an LLM repair because location failed.
        # Final compact audits are emitted after the stable Mention ID is known.
        del run_id, stage, subject_id, source_candidate_ids, fallback_main_evidence
        del candidate_anchors
        main_anchors: list[EvidenceLocator] = []
        for evidence in draft.evidence_locations:
            try:
                reconciliation = reconcile_evidence_text(evidence, document, source)
            except ValueError:
                continue
            main_anchors.append(reconciliation.locator)
        for attribute in draft.open_attributes:
            try:
                reconcile_evidence_text(
                    attribute.evidence_location,
                    document,
                    source,
                    candidate_anchors=main_anchors,
                )
            except ValueError:
                continue
        return draft

    def process(self, message_id: str) -> SingleDocumentResult:
        source = self.registry.get_source(message_id)
        if source is None:
            raise ValueError(f"unknown source message {message_id!r}")
        processing_key = self.processing_key(source)
        completed = self.registry.get_completed_document_result(processing_key)
        if completed is not None:
            return completed.model_copy(update={"reused": True, "model_calls": []})

        run_id = str(uuid.uuid4())
        started_at = datetime.now(UTC)
        started = self.registry.start_document_run(
            run_id=run_id,
            processing_key=processing_key,
            message_id=message_id,
            pipeline_version=PIPELINE_VERSION,
            prompt_version=PROMPT_VERSION,
            catalog_version=FINALIZATION_VERSION,
            model_config=self.model_config,
        )
        if not started:
            completed = self.registry.get_completed_document_result(processing_key)
            if completed is not None:
                return completed.model_copy(update={"reused": True, "model_calls": []})
            raise SingleDocumentPipelineError("registry", "run_not_started")

        summaries: list[ModelCallSummary] = []
        judge_routing = JudgeRouting(invoked=False, reasons=[])
        try:
            known = self.registry.list_preprocessed_documents(exclude_message_id=message_id)
            preprocessing = preprocess_source(source, known_documents=known)
            self.registry.save_preprocessing_result(run_id, preprocessing)
            if preprocessing.reusable_message_id is not None:
                previous = self.registry.get_latest_completed_document_result_for_message(
                    preprocessing.reusable_message_id
                )
                if previous is not None:
                    return self._complete_reused(
                        source=source,
                        previous=previous,
                        run_id=run_id,
                        processing_key=processing_key,
                        started_at=started_at,
                    )

            title_client = _AuditedEmbeddingClient(
                client=self.embedding_client,
                registry=self.registry,
                run_id=run_id,
                model=self.model_m1,
                stage="title_embedding",
                summaries=summaries,
            )
            # Keep the title embedding as the fail-fast prerequisite. Running
            # Dreamer speculatively would spend tokens after a failed M1 call.
            title_embedding = title_client.embed([source.title])
            candidates = self.registry.get_latest_dream_candidates_for_processing_key(
                processing_key
            )
            if candidates:
                self.registry.append_decision_audit(
                    DecisionAuditRecord(
                        audit_id=f"dreamer-stage-reuse:{run_id}",
                        run_id=run_id,
                        decision_type="DREAMER_STAGE_REUSE",
                        subject_id=message_id,
                        payload={"candidate_count": len(candidates)},
                    )
                )
            else:
                candidates = self._dream(source, preprocessing.document, run_id, summaries)
                self.registry.save_dream_candidates(run_id, candidates)
            self.registry.save_embedding(
                owner_kind="source_title",
                owner_id=source.message_id,
                model=title_embedding.model,
                input_hash=hashlib.sha256(source.title.encode("utf-8")).hexdigest(),
                vector=title_embedding.vectors[0],
            )
            grounder = self._ground(
                source,
                preprocessing.document,
                candidates,
                run_id,
                processing_key,
                summaries,
            )
            judge_routing = JudgeRouting(
                invoked=bool(grounder.drafts),
                reasons=["all_grounder_drafts_m4"] if grounder.drafts else [],
            )
            drafts = grounder.drafts
            if drafts:
                judge_decisions = self._judge(
                    source,
                    preprocessing.document,
                    drafts,
                    candidates,
                    run_id,
                    summaries,
                )
                self.registry.save_judge_decisions(run_id, judge_decisions)
                accepted = self._apply_judge(drafts, judge_decisions)
                accepted_lineage = self._judge_lineage(drafts, judge_decisions)
            else:
                accepted = []
                accepted_lineage = []

            materialized_with_lineage: list[tuple[EventMention, dict[str, object]]] = []
            for index, (draft, lineage) in enumerate(zip(accepted, accepted_lineage, strict=True)):
                lineage_candidate_ids = lineage.get("source_candidate_ids", [])
                if not isinstance(lineage_candidate_ids, list):
                    lineage_candidate_ids = []
                try:
                    mention = self._materialize_mention(
                        source,
                        preprocessing.document,
                        draft,
                        run_id=run_id,
                        source_candidate_ids=[str(value) for value in lineage_candidate_ids],
                        candidates=candidates,
                    )
                except (ValidationError, ValueError) as exc:
                    self.registry.append_decision_audit(
                        DecisionAuditRecord(
                            audit_id=f"mention-materialization-degraded:{run_id}:{index}",
                            run_id=run_id,
                            decision_type="MENTION_MATERIALIZATION_DEGRADED",
                            subject_id=f"{source.message_id}:{index}",
                            payload={
                                "error_code": type(exc).__name__,
                                "draft_hash": _hash_json(draft.model_dump(mode="json")),
                            },
                        )
                    )
                    continue
                materialized_with_lineage.append((mention, lineage))
            mentions_by_id: dict[str, EventMention] = {}
            lineage_by_mention: dict[str, list[dict[str, object]]] = {}
            duplicate_mention_ids: Counter[str] = Counter()
            for mention, lineage in materialized_with_lineage:
                lineage_by_mention.setdefault(mention.mention_id, []).append(lineage)
                existing = mentions_by_id.get(mention.mention_id)
                if existing is None:
                    mentions_by_id[mention.mention_id] = mention
                    continue
                duplicate_mention_ids[mention.mention_id] += 1
                mentions_by_id[mention.mention_id] = min(
                    (existing, mention),
                    key=lambda item: item.model_dump_json(),
                )
            if duplicate_mention_ids:
                self.registry.append_decision_audit(
                    DecisionAuditRecord(
                        audit_id=f"document-mention-deduplication:{run_id}",
                        run_id=run_id,
                        decision_type="DOCUMENT_MENTION_DEDUPLICATION",
                        subject_id=message_id,
                        payload={
                            "duplicate_identity_count": sum(duplicate_mention_ids.values()),
                            "retained_mention_count": len(mentions_by_id),
                        },
                    )
                )
            mentions = list(mentions_by_id.values())
            finalizer = MentionFinalizer()
            normalized_mentions: list[EventMention] = []
            normalization_decisions: list[NormalizationDecision] = []
            for mention in mentions:
                normalized, mention_decisions = finalizer.finalize(mention)
                normalized.validate_evidence(source)
                normalized_mentions.append(normalized)
                normalization_decisions.extend(mention_decisions)
            self.registry.save_normalization_decisions(run_id, normalization_decisions)
            result = SingleDocumentResult(
                run_id=run_id,
                message_id=message_id,
                processing_key=processing_key,
                status=ProcessingStatus.SUCCEEDED,
                mentions=normalized_mentions,
                model_calls=summaries,
                judge_routing=judge_routing,
                normalization_decisions=normalization_decisions,
                failures=[],
                started_at=started_at,
                finished_at=datetime.now(UTC),
            )
            self.registry.complete_document_run(result)
            for mention in normalized_mentions:
                self.registry.append_decision_audit(
                    DecisionAuditRecord(
                        audit_id=f"mention-derivation:{run_id}:{mention.mention_id}",
                        run_id=run_id,
                        decision_type="MENTION_DERIVATION",
                        subject_id=mention.mention_id,
                        payload={
                            "message_id": message_id,
                            "final_mention_id": mention.mention_id,
                            "derivations": lineage_by_mention.get(mention.mention_id, []),
                            "normalization_decision_ids": [
                                item.decision_id
                                for item in normalization_decisions
                                if item.mention_id == mention.mention_id
                            ],
                        },
                    )
                )
            return result
        except Exception as exc:
            stage = exc.stage if isinstance(exc, SingleDocumentPipelineError) else "pipeline"
            code = (
                exc.code
                if isinstance(exc, (SingleDocumentPipelineError, ModelAdapterError))
                else type(exc).__name__
            )
            if not isinstance(exc, (SingleDocumentPipelineError, ModelAdapterError)):
                safe_frames = [
                    {
                        "file": frame.filename.replace("\\", "/").rsplit("/", 1)[-1],
                        "function": frame.name,
                        "line": frame.lineno,
                    }
                    for frame in traceback.extract_tb(exc.__traceback__)
                    if "/cdecr/" in frame.filename.replace("\\", "/")
                ][-12:]
                self.registry.append_decision_audit(
                    DecisionAuditRecord(
                        audit_id=f"document-exception-stack:{run_id}",
                        run_id=run_id,
                        decision_type="DOCUMENT_EXCEPTION_STACK",
                        subject_id=message_id,
                        payload={
                            "error_code": code,
                            "message": str(exc)[:400],
                            "frames": safe_frames,
                        },
                    )
                )
            self.registry.append_decision_audit(
                DecisionAuditRecord(
                    audit_id=f"document-failure:{run_id}",
                    run_id=run_id,
                    decision_type="DOCUMENT_PROCESSING_FAILURE",
                    subject_id=message_id,
                    payload={"stage": stage, "error_code": code},
                )
            )
            self.registry.fail_document_run(run_id, error_code=code)
            return SingleDocumentResult(
                run_id=run_id,
                message_id=message_id,
                processing_key=processing_key,
                status=ProcessingStatus.FAILED,
                mentions=[],
                model_calls=summaries,
                judge_routing=judge_routing,
                normalization_decisions=[],
                failures=[FailureSummary(stage=stage, error_code=code)],
                started_at=started_at,
                finished_at=datetime.now(UTC),
            )

    def process_batch(self, message_ids: Sequence[str]) -> list[SingleDocumentResult]:
        if not message_ids:
            return []
        indexed = list(enumerate(message_ids))
        first_by_fingerprint: dict[str, tuple[int, str]] = {}
        deferred: list[tuple[int, str]] = []
        for index, message_id in indexed:
            source = self.registry.get_source(message_id)
            if source is None:
                raise ValueError(f"unknown source message {message_id!r}")
            fingerprint = exact_document_fingerprint(source)
            if fingerprint in first_by_fingerprint:
                deferred.append((index, message_id))
            else:
                first_by_fingerprint[fingerprint] = (index, message_id)
        representatives = sorted(first_by_fingerprint.values())
        results: dict[int, SingleDocumentResult] = {}

        def run(items: list[tuple[int, str]]) -> None:
            if not items:
                return
            with ThreadPoolExecutor(
                max_workers=min(self.document_workers, len(items))
            ) as executor:
                values = executor.map(self.process, [message_id for _, message_id in items])
                for (index, _), result in zip(items, values, strict=True):
                    results[index] = result

        # Preserve the current reuse rule: only exact duplicates wait for a
        # representative; normalized/URL/near duplicates remain independent.
        run(representatives)
        run(deferred)
        return [results[index] for index, _ in indexed]

    def _invoke_typed(
        self,
        *,
        client: _AuditedStructuredClient,
        request: StructuredModelRequest,
        output_type: type[_T],
        semantic_validator: Callable[[_T], None],
        stage: str,
        repair_on_failure: bool = True,
        invalid_grounder_draft_collector: (
            Callable[[object, list[dict[str, object]]], None] | None
        ) = None,
    ) -> _T:
        request_key = _hash_json({"stage": stage, "user_prompt": request.user_prompt})[:16]

        def normalize_model_enums(payload: object, *, attempt: str) -> object:
            if not isinstance(payload, dict):
                return payload
            normalized_fields: Counter[str] = Counter()
            allowed_families = {item.value for item in EventFamily}
            allowed_participant_roles = {item.value for item in ParticipantRole}
            allowed_time_precisions = {item.value for item in TimePrecision}

            def normalize_mention(value: object) -> None:
                if not isinstance(value, dict):
                    return
                family = value.get("event_family")
                if isinstance(family, str) and family not in allowed_families:
                    value["event_family"] = EventFamily.OTHER.value
                    normalized_fields["event_family"] += 1
                participants = value.get("participants")
                if isinstance(participants, list):
                    for participant in participants:
                        if not isinstance(participant, dict):
                            continue
                        role = participant.get("role")
                        if isinstance(role, str) and role not in allowed_participant_roles:
                            participant["role"] = ParticipantRole.OTHER.value
                            normalized_fields["participants.role"] += 1
                event_time = value.get("time")
                if isinstance(event_time, dict):
                    precision = event_time.get("precision")
                    if isinstance(precision, str) and precision not in allowed_time_precisions:
                        event_time["precision"] = TimePrecision.UNKNOWN.value
                        normalized_fields["time.precision"] += 1

            if output_type is GrounderModelOutput:
                drafts = payload.get("drafts")
                if isinstance(drafts, list):
                    retained_drafts: list[object] = []
                    for draft_index, draft in enumerate(drafts, start=1):
                        if isinstance(draft, dict):
                            normalize_mention(draft.get("mention"))
                        original_hash = _hash_json(draft)
                        normalization_status: GrounderNormalizationStatus = "UNCHANGED"
                        normalization_rules: list[str] = []
                        if self.grounder_safe_normalization:
                            draft, normalization_status, normalization_rules = (
                                normalize_grounder_draft_shape(draft)
                            )
                        if normalization_rules or normalization_status == "UNSAFE_TO_NORMALIZE":
                            after_hash = _hash_json(draft)
                            self.registry.append_decision_audit(
                                DecisionAuditRecord(
                                    audit_id=(
                                        f"grounder-shape-normalization:{client.run_id}:"
                                        f"{request_key}:{draft_index}"
                                    ),
                                    run_id=client.run_id,
                                    decision_type="GROUNDER_DRAFT_SHAPE_NORMALIZATION",
                                    subject_id=f"{request_key}:g{draft_index}",
                                    payload={
                                        "draft_ref": f"g{draft_index}",
                                        "status": normalization_status,
                                        "rule_codes": normalization_rules,
                                        "before_shape_hash": original_hash,
                                        "after_shape_hash": after_hash,
                                        "avoided_llm_repair": (
                                            normalization_status == "NORMALIZED_VALID"
                                        ),
                                    },
                                )
                            )
                        try:
                            GroundedMentionDraftInput.model_validate(draft)
                        except ValidationError as exc:
                            safe_errors = _safe_validation_errors(exc, limit=20)
                            if self.grounder_primary_normalization == "shadow" and any(
                                item["code"] == "PRIMARY_QUANTITY_COUNT"
                                for item in safe_errors
                            ):
                                raw_mention = (
                                    draft.get("mention")
                                    if isinstance(draft, dict)
                                    else None
                                )
                                quantities = (
                                    raw_mention.get("quantities")
                                    if isinstance(raw_mention, dict)
                                    else None
                                )
                                primary_count = sum(
                                    isinstance(item, dict)
                                    and str(item.get("role", "")).upper() == "PRIMARY"
                                    for item in quantities or []
                                )
                                safe_candidate, shadow_reason = (
                                    _grounder_primary_shadow_candidate(draft)
                                )
                                self.registry.append_decision_audit(
                                    DecisionAuditRecord(
                                        audit_id=(
                                            f"grounder-primary-shadow:{client.run_id}:"
                                            f"{request_key}:{draft_index}"
                                        ),
                                        run_id=client.run_id,
                                        decision_type="GROUNDER_PRIMARY_NORMALIZATION_SHADOW",
                                        subject_id=f"{request_key}:g{draft_index}",
                                        payload={
                                            "draft_ref": f"g{draft_index}",
                                            "quantity_count": len(quantities or []),
                                            "primary_count": primary_count,
                                            "safe_candidate": safe_candidate,
                                            "would_avoid_repair": safe_candidate,
                                            "reason": shadow_reason,
                                        },
                                    )
                                )
                            normalized_fields["grounder.invalid_draft_dropped"] += 1
                            if invalid_grounder_draft_collector is not None:
                                invalid_grounder_draft_collector(
                                    draft,
                                    safe_errors,
                                )
                            continue
                        retained_drafts.append(draft)
                    payload["drafts"] = retained_drafts
                rejected_candidates = payload.get("rejected_candidates")
                if isinstance(rejected_candidates, list):
                    retained_rejections: list[object] = []
                    for rejection in rejected_candidates:
                        try:
                            RejectedCandidateDraft.model_validate(rejection)
                        except ValidationError:
                            normalized_fields["grounder.invalid_rejection_dropped"] += 1
                            continue
                        retained_rejections.append(rejection)
                    payload["rejected_candidates"] = retained_rejections
                issue_flags = payload.get("issue_flags")
                if isinstance(issue_flags, list):
                    retained_flags = [
                        flag for flag in issue_flags if isinstance(flag, str) and flag.strip()
                    ]
                    if len(retained_flags) != len(issue_flags):
                        normalized_fields["grounder.invalid_issue_flag_dropped"] += len(
                            issue_flags
                        ) - len(retained_flags)
                    payload["issue_flags"] = retained_flags
            elif output_type is JudgeCommandOutput:
                command_groups = [
                    payload.get("accepted"),
                    payload.get("rejected"),
                    payload.get("split"),
                    payload.get("duplicates"),
                    payload.get("attribute_merges"),
                ]
                for commands in command_groups:
                    if not isinstance(commands, list):
                        continue
                    for command in commands:
                        if not isinstance(command, dict):
                            continue
                        reason = command.get("reason")
                        if isinstance(reason, str) and len(reason) > 240:
                            command["reason"] = reason[:240]
                            normalized_fields["reason.truncated_to_240"] += 1
                accepted = payload.get("accepted")
                if isinstance(accepted, list):
                    for command in accepted:
                        if not isinstance(command, dict):
                            continue
                        changes = command.get("changes")
                        if changes is None or changes == {}:
                            if "changes" in command:
                                command.pop("changes")
                                normalized_fields["accepted.changes.omitted_empty"] += 1
                            continue
                        normalize_mention(changes)
                split_commands = payload.get("split")
                if isinstance(split_commands, list):
                    for command in split_commands:
                        if not isinstance(command, dict):
                            continue
                        mentions = command.get("mentions")
                        if isinstance(mentions, list):
                            for mention in mentions:
                                normalize_mention(mention)
                command_types: dict[str, type[StrictModel]] = {
                    "accepted": JudgeAcceptedCommand,
                    "rejected": JudgeRejectedCommand,
                    "split": JudgeSplitCommand,
                    "duplicates": JudgeDuplicateCommand,
                    "attribute_merges": JudgeAttributeMergeCommand,
                }
                for group_name, command_type in command_types.items():
                    raw_commands = payload.get(group_name)
                    if not isinstance(raw_commands, list):
                        continue
                    retained_commands: list[object] = []
                    for command in raw_commands:
                        try:
                            command_type.model_validate(command)
                        except ValidationError:
                            normalized_fields[f"judge.{group_name}.invalid_command_dropped"] += 1
                            continue
                        retained_commands.append(command)
                    payload[group_name] = retained_commands
            if normalized_fields:
                client.registry.append_decision_audit(
                    DecisionAuditRecord(
                        audit_id=(
                            f"model-enum-normalization:{client.run_id}:{stage}:"
                            f"{request_key}:{attempt}"
                        ),
                        run_id=client.run_id,
                        decision_type="MODEL_ENUM_NORMALIZATION",
                        subject_id=stage,
                        payload={
                            "attempt": attempt,
                            "fields": dict(normalized_fields),
                            "strategy": "unknown_model_enum_to_contract_unknown",
                        },
                    )
                )
            return payload

        def audit_validation_failure(exc: ValidationError | ValueError, *, attempt: str) -> None:
            if isinstance(exc, ValidationError):
                all_errors = exc.errors(include_input=False, include_url=False)
                errors = _safe_validation_errors(exc)
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
                    "error_code": _safe_semantic_error_code(exc),
                }
            client.registry.append_decision_audit(
                DecisionAuditRecord(
                    audit_id=(
                        f"structured-validation:{client.run_id}:{stage}:{request_key}:{attempt}"
                    ),
                    run_id=client.run_id,
                    decision_type="STRUCTURED_VALIDATION_FAILURE",
                    subject_id=stage,
                    payload=payload,
                )
            )

        def repair_and_validate(invalid_payload: object, validation_error: object) -> _T:
            repair = StructuredModelRequest(
                system_prompt=(
                    "Repair the previous invalid structured output. Return only a corrected object "
                    "matching the schema and every evidence/candidate constraint."
                ),
                user_prompt=json.dumps(
                    {
                        "original_request": request.user_prompt,
                        "invalid_payload": invalid_payload,
                        "validation_error": validation_error,
                    },
                    ensure_ascii=False,
                    default=str,
                ),
                json_schema=output_type.model_json_schema(),
            )
            try:
                repaired = client.complete(repair)
                normalized_payload = normalize_model_enums(repaired.payload, attempt="repair")
                output = output_type.model_validate(normalized_payload)
                semantic_validator(output)
                return output
            except (SingleDocumentPipelineError, ValidationError, ValueError) as exc:
                if isinstance(exc, (ValidationError, ValueError)):
                    audit_validation_failure(exc, attempt="repair")
                code = (
                    exc.code
                    if isinstance(exc, SingleDocumentPipelineError)
                    else _repaired_validation_error_code(exc)
                )
                raise SingleDocumentPipelineError(stage, code) from exc

        try:
            result = client.complete(request)
        except SingleDocumentPipelineError as first_error:
            if (
                repair_on_failure
                and first_error.code in {"invalid_json", "invalid_json_shape"}
                and first_error.repair_payload is not None
            ):
                return repair_and_validate(first_error.repair_payload, first_error.code)
            raise
        try:
            normalized_payload = normalize_model_enums(result.payload, attempt="initial")
            output = output_type.model_validate(normalized_payload)
            semantic_validator(output)
            return output
        except (ValidationError, ValueError) as first_error:
            audit_validation_failure(first_error, attempt="initial")
            if not repair_on_failure:
                if isinstance(first_error, ValidationError):
                    error_code = "schema_validation_failed_without_batch_repair"
                else:
                    error_code = "semantic_validation_failed_without_batch_repair"
                raise SingleDocumentPipelineError(
                    stage,
                    error_code,
                ) from first_error
            if isinstance(first_error, ValidationError):
                validation_error: object = _safe_validation_errors(first_error)
            else:
                validation_error = str(first_error)
            return repair_and_validate(result.payload, validation_error)

    def _invoke_typed_with_metadata(
        self,
        *,
        client: _AuditedStructuredClient,
        request: StructuredModelRequest,
        output_type: type[_T],
        semantic_validator: Callable[[_T], None],
        stage: str,
        repair_on_failure: bool = True,
    ) -> TypedInvocation[_T]:
        output = self._invoke_typed(
            client=client,
            request=request,
            output_type=output_type,
            semantic_validator=semantic_validator,
            stage=stage,
            repair_on_failure=repair_on_failure,
        )
        response_id = client.last_result.response_id if client.last_result is not None else None
        return TypedInvocation(output=output, response_id=response_id)

    def _dream(
        self,
        source: SourceMessage,
        document: PreprocessedDocument,
        run_id: str,
        summaries: list[ModelCallSummary],
    ) -> list[DreamCandidate]:
        tier = ModelTier.M2
        raw_client = _ResponsesStructuredAdapter(
            client=self.dreamer_responses_client,
            reasoning_effort="none",
        )
        model = self.model_m2
        client = _AuditedStructuredClient(
            client=raw_client,
            registry=self.registry,
            run_id=run_id,
            tier=tier,
            model=model,
            stage="dreamer",
            summaries=summaries,
        )

        def process_block(
            block: DocumentBlock, *, zero_recovery: bool = False
        ) -> DreamerModelOutput:
            request = dreamer_block_request(
                source=source,
                document=document,
                block=block,
                system_prompt=_prompt("dreamer.md"),
                zero_recovery=zero_recovery,
            )
            exposed_lengths = dreamer_block_exposed_lengths(document, block)

            reconciliation: dict[str, int] = {}

            def validate_dreamer(
                output: DreamerModelOutput,
                *,
                exposed_lengths: dict[str, int] = exposed_lengths,
                reconciliation: dict[str, int] = reconciliation,
            ) -> None:
                invalid_locators, dropped_candidates = self._validate_dreamer_output(
                    output,
                    exposed_lengths=exposed_lengths,
                    document=document,
                    source=source,
                )
                reconciliation.clear()
                reconciliation.update(
                    {
                        "invalid_evidence_locators": invalid_locators,
                        "dropped_candidates": dropped_candidates,
                    }
                )

            response_id: str | None = None
            try:
                call_stage = "dreamer_zero_recovery" if zero_recovery else "dreamer"
                call_client = (
                    _AuditedStructuredClient(
                        client=raw_client,
                        registry=self.registry,
                        run_id=run_id,
                        tier=tier,
                        model=model,
                        stage=call_stage,
                        summaries=summaries,
                    )
                    if zero_recovery
                    else client
                )
                invocation = self._invoke_typed_with_metadata(
                    client=call_client,
                    request=request,
                    output_type=DreamerModelOutput,
                    semantic_validator=validate_dreamer,
                    stage=call_stage,
                )
                output = invocation.output
                response_id = invocation.response_id
            except SingleDocumentPipelineError as exc:
                output = DreamerModelOutput(candidates=[])
                self.registry.append_decision_audit(
                    DecisionAuditRecord(
                        audit_id=(
                            f"dreamer-block-degraded:{run_id}:{block.block_id}:"
                            f"{'zero-recovery' if zero_recovery else 'initial'}"
                        ),
                        run_id=run_id,
                        decision_type="DREAMER_BLOCK_DEGRADED",
                        subject_id=block.block_id,
                        payload={
                            "code": exc.code,
                            "fallback": "EMPTY_BLOCK_CANDIDATES",
                        },
                    )
                )
            if any(reconciliation.values()):
                self.registry.append_decision_audit(
                    DecisionAuditRecord(
                        audit_id=(
                            f"dreamer-evidence-reconciliation:{run_id}:{block.block_id}:"
                            f"{'zero-recovery' if zero_recovery else 'initial'}"
                        ),
                        run_id=run_id,
                        decision_type="DREAMER_EVIDENCE_RECONCILIATION",
                        subject_id=block.block_id,
                        payload=reconciliation,
                    )
                )
            return self._apply_relevance_gate(
                source=source,
                document=document,
                block=block,
                output=output,
                response_id=response_id,
                run_id=run_id,
                summaries=summaries,
                attempt="zero-recovery" if zero_recovery else "initial",
            )

        outputs = list(
            self._document_block_executor.map(process_block, document.document_blocks)
        )
        if not any(output.candidates for output in outputs):
            outputs = list(
                self._document_block_executor.map(
                    lambda block: process_block(block, zero_recovery=True),
                    document.document_blocks,
                )
            )
            self.registry.append_decision_audit(
                DecisionAuditRecord(
                    audit_id=f"dreamer-zero-recovery:{run_id}",
                    run_id=run_id,
                    decision_type="DREAMER_ZERO_CANDIDATE_RECOVERY",
                    subject_id=source.message_id,
                    payload={
                        "recovered_candidate_count": sum(
                            len(output.candidates) for output in outputs
                        )
                    },
                )
            )
        unique: dict[str, DreamCandidate] = {}
        for output in outputs:
            for candidate in output.candidates:
                evidence_locations: list[EvidenceLocator] = []
                try:
                    for evidence in candidate.evidence_locations:
                        reconciliation = reconcile_evidence_text(
                            evidence,
                            document,
                            source,
                        )
                        locator_to_evidence(
                            reconciliation.locator,
                            document,
                            source,
                        )
                        evidence_locations.append(reconciliation.locator)
                except ValueError:
                    # The same deterministic reconciliation already succeeded in
                    # _validate_dreamer_output. Treat unexpected drift locally.
                    continue
                identity = _hash_json(
                    {
                        "statement": candidate.statement,
                        "evidence_locations": [
                            item.model_dump(mode="json") for item in evidence_locations
                        ],
                    }
                )
                item = DreamCandidate(
                    candidate_id=f"candidate:{source.message_id}:{identity}",
                    statement=candidate.statement,
                    evidence_locations=evidence_locations,
                )
                unique[item.candidate_id] = item
        return list(unique.values())

    def _apply_relevance_gate(
        self,
        *,
        source: SourceMessage,
        document: PreprocessedDocument,
        block: DocumentBlock,
        output: DreamerModelOutput,
        response_id: str | None,
        run_id: str,
        summaries: list[ModelCallSummary],
        attempt: str,
    ) -> DreamerModelOutput:
        if self.relevance_filter_mode is RelevanceMode.OFF or not output.candidates:
            return output

        target = target_profile_for_source(source, self.relevance_target_profiles)
        skip_reason = "TARGET_PROFILE_MISSING_OR_AMBIGUOUS" if target is None else None
        pseudo_candidates: list[DreamCandidate] = []
        if skip_reason is None:
            try:
                for index, candidate in enumerate(output.candidates, start=1):
                    pseudo_candidates.append(
                        DreamCandidate(
                            candidate_id=f"{block.block_id}:c{index}",
                            statement=candidate.statement,
                            evidence_locations=[
                                reconcile_evidence_text(locator, document, source).locator
                                for locator in candidate.evidence_locations
                            ],
                        )
                    )
            except ValueError:
                skip_reason = "CANDIDATE_RECONCILIATION_DRIFT"

        decisions: list[CandidateGateDecision]
        ignored_ids: tuple[str, ...] = ()
        if skip_reason is not None:
            decisions = [
                CandidateGateDecision(
                    candidate_id=f"{block.block_id}:c{index}",
                    relevance=None,
                    keep=True,
                    reason=skip_reason,
                )
                for index, _ in enumerate(output.candidates, start=1)
            ]
            selected_indices = set(range(len(output.candidates)))
        else:
            assert target is not None
            relevance_prompt = (
                _prompt("relevance_filter.md")
                + "\n\n"
                + ticker_target_instruction(target)
            )
            response_request, short_to_full = relevance_response_request(
                target=target,
                candidates=pseudo_candidates,
                system_prompt=relevance_prompt,
            )
            adapter = _ResponsesStructuredAdapter(
                client=self.relevance_responses_client,
                reasoning_effort="none",
                previous_response_id=None,
                preset_request=response_request,
            )
            client = _AuditedStructuredClient(
                client=adapter,
                registry=self.registry,
                run_id=run_id,
                tier=ModelTier.M2,
                model=self.model_m2,
                stage="dreamer_relevance",
                summaries=summaries,
            )
            request = StructuredModelRequest(
                system_prompt=relevance_prompt,
                user_prompt=str(response_request.input[-1]["content"]),
                json_schema=response_request.json_schema,
                metadata={
                    "candidate_count": len(pseudo_candidates),
                    "transport": "responses_json_object",
                    "reasoning_effort": "none",
                    "previous_response_id": None,
                    "session_cache": False,
                },
            )
            raw_result: StructuredModelResult | None = None
            failure_code: str | None = None
            try:
                raw_result = client.complete(request)
                validate_relevance_coverage(
                    raw_result.payload,
                    expected_short_ids=set(short_to_full),
                )
            except (SingleDocumentPipelineError, ValueError, ValidationError) as exc:
                failure_code = getattr(exc, "code", type(exc).__name__)
                repair_prompt = (
                    relevance_prompt
                    + "\n\nRepair the prior batch response. Return every supplied candidate id "
                    "exactly once and no other id."
                )
                repair_response_request = response_request.model_copy(
                    update={
                        "input": [
                            {"role": "system", "content": repair_prompt},
                            response_request.input[-1],
                        ],
                        "previous_response_id": None,
                        "session_cache": False,
                        "metadata": {
                            **response_request.metadata,
                            "repair_of": failure_code,
                        },
                    }
                )
                repair_adapter = _ResponsesStructuredAdapter(
                    client=self.relevance_responses_client,
                    reasoning_effort="none",
                    previous_response_id=None,
                    preset_request=repair_response_request,
                )
                repair_client = _AuditedStructuredClient(
                    client=repair_adapter,
                    registry=self.registry,
                    run_id=run_id,
                    tier=ModelTier.M2,
                    model=self.model_m2,
                    stage="dreamer_relevance",
                    summaries=summaries,
                )
                repair_request = request.model_copy(
                    update={"system_prompt": "Repair relevance batch coverage."}
                )
                try:
                    raw_result = repair_client.complete(repair_request)
                    validate_relevance_coverage(
                        raw_result.payload,
                        expected_short_ids=set(short_to_full),
                    )
                except (SingleDocumentPipelineError, ValueError, ValidationError) as repair_exc:
                    failure_code = getattr(repair_exc, "code", type(repair_exc).__name__)
                    raw_result = None

            if raw_result is None:
                decisions = [
                    CandidateGateDecision(
                        candidate_id=candidate.candidate_id,
                        relevance=None,
                        keep=True,
                        reason=f"GATE_CALL_FAILED:{failure_code}",
                    )
                    for candidate in pseudo_candidates
                ]
                selected_indices = set(range(len(output.candidates)))
            else:
                selection = select_candidates_fail_open(
                    pseudo_candidates,
                    raw_result.payload,
                    short_to_full=short_to_full,
                )
                decisions = selection.decisions
                ignored_ids = selection.ignored_ids
                kept_ids = {candidate.candidate_id for candidate in selection.retained}
                selected_indices = {
                    index
                    for index, candidate in enumerate(pseudo_candidates)
                    if candidate.candidate_id in kept_ids
                }

        relevant_count = sum(item.relevance == "RELEVANT" for item in decisions)
        irrelevant_count = sum(item.relevance == "IRRELEVANT" for item in decisions)
        fail_open_count = sum(item.relevance is None for item in decisions)
        simulated_dropped = len(output.candidates) - len(selected_indices)
        enforced = self.relevance_filter_mode is RelevanceMode.ENFORCE
        self.registry.append_decision_audit(
            DecisionAuditRecord(
                audit_id=(f"dreamer-relevance:{run_id}:{block.block_id}:{attempt}"),
                run_id=run_id,
                decision_type="DREAMER_RELEVANCE_GATE",
                subject_id=block.block_id,
                payload={
                    "mode": self.relevance_filter_mode.value,
                    "prompt_version": RELEVANCE_PROMPT_VERSION,
                    "target": target,
                    "before_count": len(output.candidates),
                    "after_count": (
                        len(selected_indices) if enforced else len(output.candidates)
                    ),
                    "simulated_dropped_count": simulated_dropped,
                    "relevant_count": relevant_count,
                    "irrelevant_count": irrelevant_count,
                    "fail_open_count": fail_open_count,
                    "ignored_ids": list(ignored_ids),
                    "decisions": [
                        {
                            "candidate_id": item.candidate_id,
                            "relevance": item.relevance,
                            "keep": item.keep,
                            "reason": item.reason,
                        }
                        for item in decisions
                    ],
                },
            )
        )
        if not enforced:
            return output
        return output.model_copy(
            update={
                "candidates": [
                    candidate
                    for index, candidate in enumerate(output.candidates)
                    if index in selected_indices
                ]
            }
        )

    def _validate_dreamer_output(
        self,
        output: DreamerModelOutput,
        *,
        exposed_lengths: dict[str, int],
        document: PreprocessedDocument,
        source: SourceMessage,
    ) -> tuple[int, int]:
        invalid_locators = 0
        dropped_candidates = 0
        retained_candidates = []
        for item in output.candidates:
            reconciled_evidence: list[EvidenceText] = []
            valid_candidate = True
            for evidence in item.evidence_locations:
                try:
                    reconciliation = reconcile_evidence_text(
                        evidence,
                        document,
                        source,
                    )
                    aligned = reconciliation.locator
                    if (
                        aligned.segment_id not in exposed_lengths
                        or aligned.end_char > exposed_lengths[aligned.segment_id]
                    ):
                        raise ValueError("Dreamer evidence must use an exposed segment")
                    locator_to_evidence(aligned, document, source)
                    reconciled_evidence.append(
                        EvidenceText(
                            segment_id=aligned.segment_id,
                            text=aligned.text,
                        )
                    )
                except ValueError:
                    invalid_locators += 1
                    valid_candidate = False
                    break
            if not valid_candidate:
                dropped_candidates += 1
                continue
            retained_candidates.append(
                item.model_copy(update={"evidence_locations": reconciled_evidence})
            )
        output.candidates = retained_candidates
        return invalid_locators, dropped_candidates

    def _ground(
        self,
        source: SourceMessage,
        document: PreprocessedDocument,
        candidates: list[DreamCandidate],
        run_id: str,
        processing_key: str,
        summaries: list[ModelCallSummary],
    ) -> GrounderOutput:
        if not candidates:
            self.registry.append_decision_audit(
                DecisionAuditRecord(
                    audit_id=f"grounder-empty-candidates:{run_id}",
                    run_id=run_id,
                    decision_type="GROUNDER_SKIPPED",
                    subject_id=source.message_id,
                    payload={"reason": "NO_DREAM_CANDIDATES"},
                )
            )
            return GrounderOutput(drafts=[], issue_flags=[])
        segment_order = {item.segment_id: index for index, item in enumerate(document.segments)}

        def candidate_position(item: DreamCandidate) -> tuple[int, str]:
            positions = [
                segment_order.get(locator.segment_id, len(segment_order))
                for locator in item.evidence_locations
            ]
            return (min(positions, default=len(segment_order)), item.candidate_id)

        ordered_candidates = sorted(candidates, key=candidate_position)
        batches = [
            ordered_candidates[index : index + GROUNDER_CANDIDATE_BATCH]
            for index in range(0, len(ordered_candidates), GROUNDER_CANDIDATE_BATCH)
        ]
        drafts_by_id: dict[str, GroundedMentionDraft] = {}
        issue_flags: dict[str, None] = {}

        def process_batch(
            indexed_batch: tuple[int, list[DreamCandidate]],
        ) -> tuple[int, GrounderOutput, bool]:
            batch_index, batch = indexed_batch
            short_to_full = {
                f"c{index}": candidate.candidate_id
                for index, candidate in enumerate(batch, start=1)
            }
            candidate_by_short = {
                short_id: candidate
                for short_id, candidate in zip(short_to_full, batch, strict=True)
            }
            short_candidates = [
                {
                    "candidate_id": short_id,
                    "statement": candidate.statement,
                    "evidence_locations": [
                        locator.model_dump(mode="json") for locator in candidate.evidence_locations
                    ],
                }
                for short_id, candidate in zip(short_to_full, batch, strict=True)
            ]
            request = StructuredModelRequest(
                system_prompt=_prompt("grounder.md"),
                user_prompt=json.dumps(
                    {
                        "published_at": _published_at_model(source.published_at),
                        "batch_index": batch_index,
                        "batch_count": len(batches),
                        "document": grounder_context(document, batch),
                        "candidates": short_candidates,
                    },
                    ensure_ascii=False,
                ),
                json_schema=GrounderModelOutput.model_json_schema(),
            )
            batch_key = _hash_json(
                {
                    "processing_key": processing_key,
                    "model": self.model_m2,
                    "prompt_version": PROMPT_VERSION,
                    "request": request.user_prompt,
                }
            )
            cached = self.registry.get_grounder_batch(
                processing_key=processing_key, batch_key=batch_key
            )
            if cached is not None:
                return batch_index, cached, True
            client = _AuditedStructuredClient(
                client=self.m3_client,
                registry=self.registry,
                run_id=run_id,
                tier=ModelTier.M2,
                model=self.model_m2,
                stage="grounder",
                summaries=summaries,
            )
            candidate_ids = set(short_to_full)
            disposition_failures: list[dict[str, list[str]]] = []
            invalid_schema_drafts: list[tuple[object, list[dict[str, object]]]] = []
            primary_shadow_by_invalid_index: dict[int, dict[str, object]] = {}

            def collect_invalid_schema_draft(
                draft: object,
                errors: list[dict[str, object]],
            ) -> None:
                invalid_schema_drafts.append((draft, errors))
                invalid_index = len(invalid_schema_drafts)
                raw_source_ids = (
                    draft.get("source_candidate_ids") if isinstance(draft, dict) else None
                )
                source_ids = (
                    [value for value in raw_source_ids if isinstance(value, str)]
                    if isinstance(raw_source_ids, list)
                    else []
                )
                primary_error = any(
                    item.get("code") == "PRIMARY_QUANTITY_COUNT" for item in errors
                )
                safe_candidate, shadow_reason = (
                    _grounder_primary_shadow_candidate(draft)
                    if primary_error
                    else (False, "NOT_PRIMARY_ERROR")
                )
                proposed = _grounder_primary_shadow_proposal(draft)
                primary_shadow_by_invalid_index[invalid_index] = {
                    "safe_candidate": safe_candidate,
                    "shadow_reason": shadow_reason,
                    "original_draft_hash": _hash_json(draft),
                    "proposed_output_hash": _hash_json(proposed) if proposed is not None else None,
                    "source_candidate_set_hash": _hash_json(sorted(source_ids)),
                    "semantic_projection_hash": (
                        _hash_json(_grounder_shadow_projection(proposed))
                        if proposed is not None
                        else None
                    ),
                    "proposed_projection": _grounder_shadow_projection(proposed),
                }
                self.registry.append_decision_audit(
                    DecisionAuditRecord(
                        audit_id=(f"grounder-invalid-draft:{run_id}:{batch_index}:{invalid_index}"),
                        run_id=run_id,
                        decision_type="GROUNDER_INVALID_DRAFT",
                        subject_id=(f"grounder-batch:{batch_index}:invalid:{invalid_index}"),
                        payload={
                            "draft_index": invalid_index,
                            "source_candidate_ids": source_ids,
                            "errors": errors,
                            "next_action": "INDIVIDUAL_ITEM_REPAIR",
                        },
                    )
                )

            def validate_items(output: GrounderModelOutput) -> None:
                retained: list[GroundedMentionDraftInput] = []
                degraded: list[dict[str, object]] = []
                for draft_index, item in enumerate(output.drafts, start=1):
                    if len(item.source_candidate_ids) != len(set(item.source_candidate_ids)):
                        degraded.append(
                            {
                                "draft_index": draft_index,
                                "reason": "DUPLICATE_SOURCE_CANDIDATE_IDS",
                            }
                        )
                        continue
                    if any(value not in candidate_ids for value in item.source_candidate_ids):
                        degraded.append(
                            {
                                "draft_index": draft_index,
                                "reason": "UNKNOWN_SOURCE_CANDIDATE_ID",
                            }
                        )
                        continue
                    subject_id = f"grounder:{batch_index}:g{draft_index}"
                    try:
                        normalized = self._normalize_draft_time(
                            item.mention,
                            run_id=run_id,
                            stage="grounder",
                            subject_id=subject_id,
                        )
                        anchors = [
                            locator
                            for candidate_id in item.source_candidate_ids
                            for locator in candidate_by_short[candidate_id].evidence_locations
                        ]
                        item.mention = self._reconcile_draft_evidence(
                            normalized,
                            document,
                            source,
                            run_id=run_id,
                            stage="grounder",
                            subject_id=subject_id,
                            source_candidate_ids=item.source_candidate_ids,
                            candidate_anchors=anchors,
                        )
                        validate_event_time_semantics(item.mention.time)
                    except ValueError as exc:
                        degraded.append(
                            {
                                "draft_index": draft_index,
                                "reason": _safe_semantic_error_code(exc),
                            }
                        )
                        continue
                    retained.append(item)
                output.drafts = retained
                if degraded:
                    self.registry.append_decision_audit(
                        DecisionAuditRecord(
                            audit_id=f"grounder-item-degraded:{run_id}:{batch_index}",
                            run_id=run_id,
                            decision_type="GROUNDER_ITEM_DEGRADED",
                            subject_id=f"grounder-batch:{batch_index}",
                            payload={"items": degraded},
                        )
                    )

            try:
                output = self._invoke_typed(
                    client=client,
                    request=request,
                    output_type=GrounderModelOutput,
                    semantic_validator=lambda _output: None,
                    stage="grounder",
                    repair_on_failure=False,
                    invalid_grounder_draft_collector=collect_invalid_schema_draft,
                )
                root_degraded = False
            except SingleDocumentPipelineError as exc:
                if is_provider_failure(exc):
                    raise
                output = GrounderModelOutput(
                    drafts=[],
                    rejected_candidates=[],
                    issue_flags=[],
                )
                root_degraded = True
                self.registry.append_decision_audit(
                    DecisionAuditRecord(
                        audit_id=f"grounder-batch-degraded:{run_id}:{batch_index}",
                        run_id=run_id,
                        decision_type="GROUNDER_BATCH_DEGRADED",
                        subject_id=f"grounder-batch:{batch_index}",
                        payload={
                            "code": exc.code,
                            "fallback": "EMPTY_BATCH_OUTPUT",
                        },
                    )
                )

            def repair_invalid_draft(
                indexed_invalid: tuple[int, tuple[object, list[dict[str, object]]]],
            ) -> list[GroundedMentionDraftInput]:
                invalid_index, (invalid_draft, validation_errors) = indexed_invalid
                if not isinstance(invalid_draft, dict):
                    return []
                raw_source_ids = invalid_draft.get("source_candidate_ids")
                if not isinstance(raw_source_ids, list):
                    return []
                source_ids = list(
                    dict.fromkeys(
                        value
                        for value in raw_source_ids
                        if isinstance(value, str) and value in candidate_ids
                    )
                )
                if not source_ids:
                    return []
                shadow = primary_shadow_by_invalid_index.get(invalid_index, {})

                def record_shadow_outcome(
                    *,
                    status: str,
                    replacements: list[GroundedMentionDraftInput],
                ) -> None:
                    safe_candidate = bool(shadow.get("safe_candidate"))
                    repaired_payloads = [
                        item.model_dump(mode="json") for item in replacements
                    ]
                    repaired_projection = (
                        _grounder_shadow_projection(repaired_payloads[0])
                        if len(repaired_payloads) == 1
                        else None
                    )
                    proposed_projection = shadow.get("proposed_projection")
                    lineage_exact = bool(
                        len(repaired_payloads) == 1
                        and sorted(repaired_payloads[0].get("source_candidate_ids") or [])
                        == sorted(source_ids)
                    )
                    semantic_exact = bool(
                        len(repaired_payloads) == 1
                        and proposed_projection == repaired_projection
                    )
                    if not safe_candidate:
                        outcome = "NOT_SAFE_CANDIDATE"
                    elif status == "FAILED":
                        outcome = "REPAIR_FAILED"
                    elif len(repaired_payloads) > 1:
                        outcome = "REPAIR_SPLIT_OR_MULTI_OUTPUT"
                    elif len(repaired_payloads) != 1 or not lineage_exact:
                        outcome = "REPAIR_DROPPED_OR_PARTIAL"
                    elif semantic_exact:
                        outcome = "EQUIVALENT_TO_REPAIR"
                    else:
                        outcome = "CRITICAL_FIELD_DIFFERENCE"
                    payload = {
                        "grounder_batch_index": batch_index,
                        "invalid_draft_index": invalid_index,
                        "original_draft_hash": shadow.get("original_draft_hash"),
                        "proposed_output_hash": shadow.get("proposed_output_hash"),
                        "source_candidate_set_hash": shadow.get("source_candidate_set_hash"),
                        "semantic_projection_hash": shadow.get("semantic_projection_hash"),
                        "repair_status": status,
                        "repair_output_count": len(repaired_payloads),
                        "lineage_exact_match": lineage_exact,
                        "semantic_projection_exact_match": semantic_exact,
                        "changed_field_codes": _grounder_shadow_changed_fields(
                            proposed_projection if isinstance(proposed_projection, dict) else None,
                            repaired_projection,
                        ),
                        "would_have_avoided_repair": outcome == "EQUIVALENT_TO_REPAIR",
                        "outcome_code": outcome,
                    }
                    self.registry.append_decision_audit(
                        DecisionAuditRecord(
                            audit_id=(
                                f"grounder-primary-shadow-outcome:{run_id}:"
                                f"{batch_index}:{invalid_index}"
                            ),
                            run_id=run_id,
                            decision_type="GROUNDER_PRIMARY_NORMALIZATION_SHADOW_OUTCOME",
                            subject_id=(
                                f"grounder-batch:{batch_index}:invalid:{invalid_index}"
                            ),
                            payload=payload,
                        )
                    )
                repair_candidates = [
                    {
                        "candidate_id": source_id,
                        "statement": candidate_by_short[source_id].statement,
                        "evidence_locations": [
                            locator.model_dump(mode="json")
                            for locator in candidate_by_short[source_id].evidence_locations
                        ],
                    }
                    for source_id in source_ids
                ]
                repair_client = _AuditedStructuredClient(
                    client=self.m3_client,
                    registry=self.registry,
                    run_id=run_id,
                    tier=ModelTier.M2,
                    model=self.model_m2,
                    stage="grounder_item_repair",
                    summaries=summaries,
                )
                repair_request = StructuredModelRequest(
                    system_prompt=(
                        _prompt("grounder.md")
                        + "\n\nRepair one invalid Grounder draft using `business_errors`. "
                        "Preserve its supported meaning and candidate lineage. Return all and "
                        "only the atomic replacement draft(s) needed to fix those errors; "
                        "retain each replacement's subject/object, action and polarity, "
                        "Assertion State, period/session, PRIMARY metric, comparison/range, "
                        "and explicit source. Do not repair unrelated drafts."
                    ),
                    user_prompt=json.dumps(
                        {
                            "published_at": _published_at_model(source.published_at),
                            "document": grounder_context(
                                document,
                                [candidate_by_short[value] for value in source_ids],
                            ),
                            "candidates": repair_candidates,
                            "invalid_draft": invalid_draft,
                            "business_errors": validation_errors,
                        },
                        ensure_ascii=False,
                    ),
                    json_schema=GrounderModelOutput.model_json_schema(),
                )

                try:
                    repaired_output = self._invoke_typed(
                        client=repair_client,
                        request=repair_request,
                        output_type=GrounderModelOutput,
                        # A locally invalid lineage must not discard otherwise legal
                        # replacements. Filter by the supplied candidate boundary below;
                        # unresolved candidates remain eligible for missing recovery.
                        semantic_validator=lambda _output: None,
                        stage="grounder_item_repair",
                        repair_on_failure=False,
                    )
                    legal_replacements = [
                        draft
                        for draft in repaired_output.drafts
                        if draft.source_candidate_ids
                        and all(
                            candidate_id in source_ids
                            for candidate_id in draft.source_candidate_ids
                        )
                    ]
                    returned_ids = {
                        candidate_id
                        for draft in legal_replacements
                        for candidate_id in draft.source_candidate_ids
                    }
                    unresolved_ids = sorted(set(source_ids) - returned_ids)
                    if unresolved_ids or len(legal_replacements) != len(repaired_output.drafts):
                        self.registry.append_decision_audit(
                            DecisionAuditRecord(
                                audit_id=(
                                    f"grounder-item-repair-partial:{run_id}:"
                                    f"{batch_index}:{invalid_index}"
                                ),
                                run_id=run_id,
                                decision_type="GROUNDER_ITEM_REPAIR_PARTIAL",
                                subject_id=(
                                    f"grounder-batch:{batch_index}:invalid:{invalid_index}"
                                ),
                                payload={
                                    "source_candidate_ids": source_ids,
                                    "recovered_candidate_ids": sorted(returned_ids),
                                    "unresolved_candidate_ids": unresolved_ids,
                                    "ignored_draft_count": (
                                        len(repaired_output.drafts) - len(legal_replacements)
                                    ),
                                    "fallback": "RETAIN_LEGAL_REPLACEMENTS",
                                },
                            )
                        )
                    record_shadow_outcome(status="SUCCEEDED", replacements=legal_replacements)
                    return legal_replacements
                except Exception as exc:
                    error_code = (
                        exc.code
                        if isinstance(exc, SingleDocumentPipelineError)
                        else type(exc).__name__
                    )
                    self.registry.append_decision_audit(
                        DecisionAuditRecord(
                            audit_id=(
                                f"grounder-item-repair-degraded:{run_id}:{batch_index}:"
                                f"{invalid_index}"
                            ),
                            run_id=run_id,
                            decision_type="GROUNDER_ITEM_REPAIR_DEGRADED",
                            subject_id=f"grounder-batch:{batch_index}:invalid:{invalid_index}",
                            payload={
                                "code": error_code,
                                "fallback": "DROP_INVALID_DRAFT_ONLY",
                                "source_candidate_ids": source_ids,
                            },
                        )
                    )
                    record_shadow_outcome(status="FAILED", replacements=[])
                    return []

            repaired_drafts: list[GroundedMentionDraftInput] = []
            if invalid_schema_drafts:
                repair_workers = min(3, len(invalid_schema_drafts))
                with ThreadPoolExecutor(max_workers=repair_workers) as repair_executor:
                    repair_futures = [
                        repair_executor.submit(repair_invalid_draft, item)
                        for item in enumerate(invalid_schema_drafts, start=1)
                    ]
                    # Validate already-legal drafts while independent item repairs run.
                    validate_items(output)
                    repaired_drafts = [
                        repaired_draft
                        for future in repair_futures
                        for repaired_draft in future.result()
                    ]
            else:
                validate_items(output)
            if repaired_drafts:
                repaired_output = GrounderModelOutput(
                    drafts=repaired_drafts,
                    rejected_candidates=[],
                    issue_flags=[],
                )
                validate_items(repaired_output)
                output.drafts.extend(repaired_output.drafts)

            # Candidate disposition is unique, but a compound candidate may
            # legitimately support several distinct atomic drafts. Collapse
            # only semantically identical drafts and union their lineage.
            retained_by_signature: dict[str, GroundedMentionDraftInput] = {}
            for draft in output.drafts:
                signature = _hash_json(draft.mention.model_dump(mode="json"))
                existing = retained_by_signature.get(signature)
                if existing is None:
                    retained_by_signature[signature] = draft
                    continue
                retained_by_signature[signature] = existing.model_copy(
                    update={
                        "source_candidate_ids": list(
                            dict.fromkeys(
                                [
                                    *existing.source_candidate_ids,
                                    *draft.source_candidate_ids,
                                ]
                            )
                        )
                    }
                )
            retained_drafts = list(retained_by_signature.values())
            seen_used = {
                candidate_id
                for draft in retained_drafts
                for candidate_id in draft.source_candidate_ids
            }
            retained_rejections: list[RejectedCandidateDraft] = []
            seen_rejected: set[str] = set()
            duplicate_rejected: set[str] = set()
            overlap_rejected: set[str] = set()
            extra: set[str] = set()
            for rejection in output.rejected_candidates:
                if rejection.id not in candidate_ids:
                    extra.add(rejection.id)
                    continue
                if rejection.id in seen_used:
                    overlap_rejected.add(rejection.id)
                    continue
                if rejection.id in seen_rejected:
                    duplicate_rejected.add(rejection.id)
                    continue
                seen_rejected.add(rejection.id)
                retained_rejections.append(rejection)
            output.drafts = retained_drafts
            output.rejected_candidates = retained_rejections
            missing = sorted(candidate_ids - seen_used - seen_rejected)
            disposition_failure = {
                "duplicate_candidate_ids": sorted(duplicate_rejected),
                "overlap_candidate_ids": sorted(overlap_rejected),
                "missing_candidate_ids": missing,
                "extra_candidate_ids": sorted(extra),
            }
            if any(disposition_failure.values()):
                disposition_failures.append(disposition_failure)
                self.registry.append_decision_audit(
                    DecisionAuditRecord(
                        audit_id=f"grounder-disposition-degraded:{run_id}:{batch_index}",
                        run_id=run_id,
                        decision_type="GROUNDER_DISPOSITION_DEGRADED",
                        subject_id=f"grounder-batch:{batch_index}",
                        payload={
                            **disposition_failure,
                            "fallback": "RETAIN_VALID_ITEMS_AND_LEAVE_MISSING_UNRESOLVED",
                        },
                    )
                )
            persisted_drafts: list[GroundedMentionDraft] = []
            for item in output.drafts:
                full_ids = [short_to_full[value] for value in item.source_candidate_ids]
                identity = _hash_json(
                    {
                        "source_candidate_ids": full_ids,
                        "mention": item.mention.model_dump(mode="json"),
                    }
                )
                persisted_drafts.append(
                    GroundedMentionDraft(
                        draft_id=f"draft:{source.message_id}:{identity}",
                        source_candidate_ids=full_ids,
                        mention=item.mention,
                    )
                )
            persisted_rejections = [
                RejectedCandidateRecord(
                    candidate_id=short_to_full[item.id],
                    code=item.code,
                )
                for item in output.rejected_candidates
            ]
            persisted = GrounderOutput(
                drafts=persisted_drafts,
                rejected_candidates=persisted_rejections,
                issue_flags=output.issue_flags,
            )
            rejection_codes = Counter(item.code for item in output.rejected_candidates)
            self.registry.append_decision_audit(
                DecisionAuditRecord(
                    audit_id=f"grounder-candidate-disposition:{run_id}:{batch_index}",
                    run_id=run_id,
                    decision_type="GROUNDER_CANDIDATE_DISPOSITION",
                    subject_id=f"grounder-batch:{batch_index}",
                    payload={
                        "candidate_count": len(batch),
                        "used_candidate_count": len(
                            {
                                candidate_id
                                for item in output.drafts
                                for candidate_id in item.source_candidate_ids
                            }
                        ),
                        "rejected_candidate_count": len(output.rejected_candidates),
                        "rejected_code_counts": dict(rejection_codes),
                        "missing_candidate_ids": missing,
                        "duplicate_candidate_ids": sorted(duplicate_rejected),
                        "candidate_fanout_count": sum(
                            1
                            for candidate_id in seen_used
                            if sum(
                                candidate_id in item.source_candidate_ids for item in output.drafts
                            )
                            > 1
                        ),
                        "initial_disposition_failures": disposition_failures,
                        "repair_triggered": bool(invalid_schema_drafts),
                        "repair_scope": (
                            "INDIVIDUAL_INVALID_DRAFT" if invalid_schema_drafts else None
                        ),
                    },
                )
            )
            if not root_degraded and not missing:
                self.registry.save_grounder_batch(
                    run_id=run_id,
                    processing_key=processing_key,
                    batch_key=batch_key,
                    output=persisted,
                )
            return batch_index, persisted, False

        worker_count = min(3, len(batches))
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            batch_results = list(executor.map(process_batch, enumerate(batches)))
        rejected_by_candidate: dict[str, RejectedCandidateRecord] = {}
        for batch_index, output, reused in sorted(batch_results):
            if reused:
                self.registry.append_decision_audit(
                    DecisionAuditRecord(
                        audit_id=f"grounder-batch-reuse:{run_id}:{batch_index}",
                        run_id=run_id,
                        decision_type="GROUNDER_BATCH_REUSE",
                        subject_id=f"grounder-batch:{batch_index}",
                        payload={"batch_index": batch_index},
                    )
                )
            for issue in output.issue_flags:
                issue_flags[issue] = None
            for item in output.drafts:
                drafts_by_id[item.draft_id] = item
            for rejection in output.rejected_candidates:
                rejected_by_candidate.setdefault(rejection.candidate_id, rejection)

        used_candidate_ids = {
            candidate_id
            for draft in drafts_by_id.values()
            for candidate_id in draft.source_candidate_ids
        }
        missing_candidates = [
            candidate
            for candidate in ordered_candidates
            if candidate.candidate_id not in used_candidate_ids
            and candidate.candidate_id not in rejected_by_candidate
        ]
        recovery_failed_ids: list[str] = []
        recovery_provider_failure = False
        if missing_candidates:
            recovery_short_to_full = {
                f"c{index}": candidate.candidate_id
                for index, candidate in enumerate(missing_candidates, start=1)
            }
            recovery_candidate_by_short = {
                short_id: candidate
                for short_id, candidate in zip(
                    recovery_short_to_full,
                    missing_candidates,
                    strict=True,
                )
            }
            recovery_ids = set(recovery_short_to_full)
            recovery_client = _AuditedStructuredClient(
                client=self.m3_client,
                registry=self.registry,
                run_id=run_id,
                tier=ModelTier.M2,
                model=self.model_m2,
                stage="grounder_missing_recovery",
                summaries=summaries,
            )
            recovery_request = StructuredModelRequest(
                system_prompt=(
                    _prompt("grounder.md")
                    + "\n\nReturn every supplied candidate exactly once. Recover the smallest "
                    "complete atomic fact or facts supported by its evidence; split independent "
                    "facts and reject an umbrella exhausted by recovered children. Missing "
                    "optional detail alone is not a defect. Return no other IDs."
                ),
                user_prompt=json.dumps(
                    {
                        "published_at": _published_at_model(source.published_at),
                        "document": grounder_context(document, missing_candidates),
                        "candidates": [
                            {
                                "candidate_id": short_id,
                                "statement": candidate.statement,
                                "evidence_locations": [
                                    locator.model_dump(mode="json")
                                    for locator in candidate.evidence_locations
                                ],
                            }
                            for short_id, candidate in recovery_candidate_by_short.items()
                        ],
                    },
                    ensure_ascii=False,
                ),
                json_schema=GrounderModelOutput.model_json_schema(),
            )

            try:
                recovered = self._invoke_typed(
                    client=recovery_client,
                    request=recovery_request,
                    output_type=GrounderModelOutput,
                    # Coverage is reconciled candidate by candidate below. A missing,
                    # duplicate, overlapping or foreign ID is local degradation, not a
                    # reason to throw away every legal disposition in the response.
                    semantic_validator=lambda _output: None,
                    stage="grounder_missing_recovery",
                    repair_on_failure=False,
                )
                legal_recovered_drafts = [
                    item
                    for item in recovered.drafts
                    if item.source_candidate_ids
                    and all(
                        candidate_id in recovery_ids for candidate_id in item.source_candidate_ids
                    )
                ]
                recovered_legal_ids: set[str] = set()
                for recovered_index, recovered_item in enumerate(
                    legal_recovered_drafts,
                    start=1,
                ):
                    try:
                        normalized = self._normalize_draft_time(
                            recovered_item.mention,
                            run_id=run_id,
                            stage="grounder_missing_recovery",
                            subject_id=f"recovered:{recovered_index}",
                        )
                        anchors = [
                            locator
                            for candidate_id in recovered_item.source_candidate_ids
                            for locator in recovery_candidate_by_short[
                                candidate_id
                            ].evidence_locations
                        ]
                        mention = self._reconcile_draft_evidence(
                            normalized,
                            document,
                            source,
                            run_id=run_id,
                            stage="grounder_missing_recovery",
                            subject_id=f"recovered:{recovered_index}",
                            source_candidate_ids=recovered_item.source_candidate_ids,
                            candidate_anchors=anchors,
                        )
                        validate_event_time_semantics(mention.time)
                    except (ValidationError, ValueError):
                        continue
                    full_ids = [
                        recovery_short_to_full[value]
                        for value in recovered_item.source_candidate_ids
                    ]
                    identity = _hash_json(
                        {
                            "source_candidate_ids": full_ids,
                            "mention": mention.model_dump(mode="json"),
                        }
                    )
                    recovered_draft = GroundedMentionDraft(
                        draft_id=f"draft:{source.message_id}:{identity}",
                        source_candidate_ids=full_ids,
                        mention=mention,
                    )
                    drafts_by_id[recovered_draft.draft_id] = recovered_draft
                    recovered_legal_ids.update(recovered_item.source_candidate_ids)
                retained_recovery_rejections: list[RejectedCandidateDraft] = []
                seen_recovery_rejections: set[str] = set()
                for recovery_rejection in recovered.rejected_candidates:
                    if (
                        recovery_rejection.id not in recovery_ids
                        or recovery_rejection.id in recovered_legal_ids
                        or recovery_rejection.id in seen_recovery_rejections
                    ):
                        continue
                    seen_recovery_rejections.add(recovery_rejection.id)
                    retained_recovery_rejections.append(recovery_rejection)
                    rejected_by_candidate.setdefault(
                        recovery_short_to_full[recovery_rejection.id],
                        RejectedCandidateRecord(
                            candidate_id=recovery_short_to_full[recovery_rejection.id],
                            code=recovery_rejection.code,
                        ),
                    )
                recovered_disposed = recovered_legal_ids.union(
                    recovery_rejection.id for recovery_rejection in retained_recovery_rejections
                )
                recovery_failed_ids = sorted(
                    recovery_short_to_full[short_id]
                    for short_id in recovery_ids - recovered_disposed
                )
                if (
                    recovery_failed_ids
                    or len(legal_recovered_drafts) != len(recovered.drafts)
                    or len(retained_recovery_rejections) != len(recovered.rejected_candidates)
                ):
                    self.registry.append_decision_audit(
                        DecisionAuditRecord(
                            audit_id=f"grounder-missing-recovery-partial:{run_id}",
                            run_id=run_id,
                            decision_type="GROUNDER_MISSING_RECOVERY_PARTIAL",
                            subject_id=source.message_id,
                            payload={
                                "candidate_ids": [
                                    candidate.candidate_id for candidate in missing_candidates
                                ],
                                "recovered_candidate_ids": sorted(
                                    recovery_short_to_full[short_id]
                                    for short_id in recovered_disposed
                                ),
                                "failed_candidate_ids": recovery_failed_ids,
                                "ignored_draft_count": (
                                    len(recovered.drafts) - len(legal_recovered_drafts)
                                ),
                                "ignored_rejection_count": (
                                    len(recovered.rejected_candidates)
                                    - len(retained_recovery_rejections)
                                ),
                                "fallback": "RETAIN_LEGAL_DISPOSITIONS",
                            },
                        )
                    )
            except Exception as exc:
                recovery_failed_ids = [candidate.candidate_id for candidate in missing_candidates]
                recovery_provider_failure = is_provider_failure(exc)
                error_code = (
                    exc.code if isinstance(exc, SingleDocumentPipelineError) else type(exc).__name__
                )
                self.registry.append_decision_audit(
                    DecisionAuditRecord(
                        audit_id=f"grounder-missing-recovery-failed:{run_id}",
                        run_id=run_id,
                        decision_type="GROUNDER_MISSING_RECOVERY_FAILED",
                        subject_id=source.message_id,
                        payload={
                            "candidate_ids": recovery_failed_ids,
                            "code": error_code,
                            "disposition": "FAILED_TECHNICAL",
                            "item_recovery_suppressed": recovery_provider_failure,
                        },
                    )
                )

        # A root-level or partial recovery failure is isolated to compact candidate
        # chunks. Legal earlier dispositions are retained; this pass never reruns
        # the document or any already disposed candidate.
        if recovery_failed_ids and not recovery_provider_failure:
            failed_candidate_map = {
                candidate.candidate_id: candidate
                for candidate in missing_candidates
                if candidate.candidate_id in set(recovery_failed_ids)
            }
            item_chunks = [
                list(failed_candidate_map.values())[offset : offset + 4]
                for offset in range(0, len(failed_candidate_map), 4)
            ]

            def recover_item_chunk(
                indexed_chunk: tuple[int, list[DreamCandidate]],
            ) -> tuple[int, list[GroundedMentionDraft], list[RejectedCandidateRecord], list[str]]:
                chunk_index, chunk = indexed_chunk
                short_to_candidate = {
                    f"c{index}": candidate for index, candidate in enumerate(chunk, start=1)
                }
                client = _AuditedStructuredClient(
                    client=self.m3_client,
                    registry=self.registry,
                    run_id=run_id,
                    tier=ModelTier.M2,
                    model=self.model_m2,
                    stage="grounder_missing_item_recovery",
                    summaries=summaries,
                )
                request = StructuredModelRequest(
                    system_prompt=(
                        _prompt("grounder.md")
                        + "\n\nReturn every supplied candidate exactly once. Recover the smallest "
                        "complete atomic fact or facts supported by its evidence; split "
                        "independent "
                        "facts and reject an umbrella exhausted by recovered children. Missing "
                        "optional detail alone is not a defect. Return no other IDs."
                    ),
                    user_prompt=json.dumps(
                        {
                            "published_at": _published_at_model(source.published_at),
                            "document": grounder_context(document, chunk),
                            "candidates": [
                                {
                                    "candidate_id": short_id,
                                    "statement": candidate.statement,
                                    "evidence_locations": [
                                        locator.model_dump(mode="json")
                                        for locator in candidate.evidence_locations
                                    ],
                                }
                                for short_id, candidate in short_to_candidate.items()
                            ],
                        },
                        ensure_ascii=False,
                    ),
                    json_schema=GrounderModelOutput.model_json_schema(),
                )
                try:
                    output = self._invoke_typed(
                        client=client,
                        request=request,
                        output_type=GrounderModelOutput,
                        semantic_validator=lambda _output: None,
                        stage="grounder_missing_item_recovery",
                        repair_on_failure=False,
                    )
                except Exception:
                    return chunk_index, [], [], [item.candidate_id for item in chunk]
                known_short_ids = set(short_to_candidate)
                disposed: set[str] = set()
                recovered_drafts: list[GroundedMentionDraft] = []
                for draft_index, item in enumerate(output.drafts, start=1):
                    if (
                        not item.source_candidate_ids
                        or any(value not in known_short_ids for value in item.source_candidate_ids)
                        or any(value in disposed for value in item.source_candidate_ids)
                    ):
                        continue
                    try:
                        normalized = self._normalize_draft_time(
                            item.mention,
                            run_id=run_id,
                            stage="grounder_missing_item_recovery",
                            subject_id=f"chunk:{chunk_index}:draft:{draft_index}",
                        )
                        mention = self._reconcile_draft_evidence(
                            normalized,
                            document,
                            source,
                            run_id=run_id,
                            stage="grounder_missing_item_recovery",
                            subject_id=f"chunk:{chunk_index}:draft:{draft_index}",
                            source_candidate_ids=item.source_candidate_ids,
                            candidate_anchors=[
                                locator
                                for candidate_id in item.source_candidate_ids
                                for locator in short_to_candidate[candidate_id].evidence_locations
                            ],
                        )
                        validate_event_time_semantics(mention.time)
                    except (ValidationError, ValueError):
                        continue
                    full_ids = [
                        short_to_candidate[value].candidate_id
                        for value in item.source_candidate_ids
                    ]
                    identity = _hash_json(
                        {
                            "source_candidate_ids": full_ids,
                            "mention": mention.model_dump(mode="json"),
                        }
                    )
                    recovered_drafts.append(
                        GroundedMentionDraft(
                            draft_id=f"draft:{source.message_id}:{identity}",
                            source_candidate_ids=full_ids,
                            mention=mention,
                        )
                    )
                    disposed.update(item.source_candidate_ids)
                recovered_rejections: list[RejectedCandidateRecord] = []
                for rejection in output.rejected_candidates:
                    if rejection.id not in known_short_ids or rejection.id in disposed:
                        continue
                    disposed.add(rejection.id)
                    recovered_rejections.append(
                        RejectedCandidateRecord(
                            candidate_id=short_to_candidate[rejection.id].candidate_id,
                            code=rejection.code,
                        )
                    )
                failed = [
                    candidate.candidate_id
                    for short_id, candidate in short_to_candidate.items()
                    if short_id not in disposed
                ]
                return chunk_index, recovered_drafts, recovered_rejections, failed

            with ThreadPoolExecutor(max_workers=min(8, len(item_chunks))) as recovery_pool:
                item_results = list(recovery_pool.map(recover_item_chunk, enumerate(item_chunks)))
            item_failed_ids: list[str] = []
            for _, recovered_drafts, recovered_rejections, failed in sorted(item_results):
                for draft in recovered_drafts:
                    drafts_by_id[draft.draft_id] = draft
                for rejection in recovered_rejections:
                    rejected_by_candidate.setdefault(rejection.candidate_id, rejection)
                item_failed_ids.extend(failed)
            self.registry.append_decision_audit(
                DecisionAuditRecord(
                    audit_id=f"grounder-missing-item-recovery:{run_id}",
                    run_id=run_id,
                    decision_type="GROUNDER_MISSING_ITEM_RECOVERY",
                    subject_id=source.message_id,
                    payload={
                        "candidate_count": len(failed_candidate_map),
                        "chunk_count": len(item_chunks),
                        "failed_technical_candidate_ids": sorted(item_failed_ids),
                        "failure_semantics": "ITEM_LOCAL_NON_BLOCKING",
                    },
                )
            )
            recovery_failed_ids = item_failed_ids

        final_used_candidate_ids = {
            candidate_id
            for draft in drafts_by_id.values()
            for candidate_id in draft.source_candidate_ids
        }
        final_missing_ids = sorted(
            candidate.candidate_id
            for candidate in ordered_candidates
            if candidate.candidate_id not in final_used_candidate_ids
            and candidate.candidate_id not in rejected_by_candidate
        )
        self.registry.append_decision_audit(
            DecisionAuditRecord(
                audit_id=f"grounder-document-disposition:{run_id}",
                run_id=run_id,
                decision_type="GROUNDER_DOCUMENT_DISPOSITION",
                subject_id=source.message_id,
                payload={
                    "candidate_count": len(ordered_candidates),
                    "used_candidate_count": len(final_used_candidate_ids),
                    "rejected_candidate_count": len(rejected_by_candidate),
                    "missing_before_recovery": [
                        candidate.candidate_id for candidate in missing_candidates
                    ],
                    "failed_technical_candidate_ids": sorted(
                        set(recovery_failed_ids).union(final_missing_ids)
                    ),
                    "business_coverage_complete": not final_missing_ids,
                },
            )
        )
        return GrounderOutput(
            drafts=list(drafts_by_id.values()),
            rejected_candidates=list(rejected_by_candidate.values()),
            issue_flags=list(issue_flags),
        )

    def _validate_draft_quotes_exist(
        self, draft: MentionDraft, document: PreprocessedDocument
    ) -> None:
        segments = {item.segment_id: item.text for item in document.segments}
        for evidence in [
            *draft.evidence_locations,
            *(item.evidence_location for item in draft.open_attributes),
        ]:
            segment_text = segments.get(evidence.segment_id)
            if segment_text is None:
                raise ValueError(f"unknown evidence segment {evidence.segment_id}")
            if evidence.text not in segment_text:
                raise ValueError("evidence text does not occur in its source segment")

    def _validate_draft_evidence(
        self,
        draft: MentionDraft,
        document: PreprocessedDocument,
        source: SourceMessage,
    ) -> None:
        validate_event_time_semantics(draft.time)
        for evidence in draft.evidence_locations:
            locate_unique_evidence_text(evidence, document, source)
        for attribute in draft.open_attributes:
            locate_unique_evidence_text(attribute.evidence_location, document, source)

    def _judge(
        self,
        source: SourceMessage,
        document: PreprocessedDocument,
        drafts: list[GroundedMentionDraft],
        candidates: list[DreamCandidate],
        run_id: str,
        summaries: list[ModelCallSummary],
    ) -> list[JudgeDecisionRecord]:
        if not drafts:
            return []

        def grouping_key(item: GroundedMentionDraft) -> tuple[str, tuple[str, ...], str]:
            return (
                item.mention.predicate.normalized.casefold(),
                tuple(
                    sorted(
                        participant.surface.casefold() for participant in item.mention.participants
                    )
                ),
                item.draft_id,
            )

        ordered = sorted(drafts, key=grouping_key)
        candidate_by_id = {item.candidate_id: item for item in candidates}
        batches = [
            ordered[index : index + JUDGE_DRAFT_BATCH]
            for index in range(0, len(ordered), JUDGE_DRAFT_BATCH)
        ]

        def process_batch(
            indexed_batch: tuple[int, list[GroundedMentionDraft]],
        ) -> tuple[int, list[JudgeDecisionRecord]]:
            batch_index, batch = indexed_batch
            short_to_full = {
                f"d{index}": draft.draft_id for index, draft in enumerate(batch, start=1)
            }
            draft_by_short = {
                short_id: draft for short_id, draft in zip(short_to_full, batch, strict=True)
            }
            model_drafts = [
                JudgeDraftInput(
                    id=short_id,
                    mention=_judge_mention(draft.mention),
                ).model_dump(mode="json")
                for short_id, draft in draft_by_short.items()
            ]
            client = _AuditedStructuredClient(
                client=self.m4_client,
                registry=self.registry,
                run_id=run_id,
                tier=ModelTier.M2,
                model=self.model_m2,
                stage="judge",
                summaries=summaries,
            )
            request = StructuredModelRequest(
                system_prompt=_prompt("judge.md"),
                user_prompt=json.dumps(
                    {
                        "published_at": _published_at_model(source.published_at),
                        "batch_index": batch_index,
                        "batch_count": len(batches),
                        "document": grounder_context(document, [draft.mention for draft in batch]),
                        "drafts": model_drafts,
                    },
                    ensure_ascii=False,
                ),
                json_schema=_compact_model_schema(JudgeCommandOutput.model_json_schema()),
            )
            draft_ids = set(short_to_full)
            validated_accepted: dict[str, MentionDraft] = {}
            validated_splits: dict[str, list[MentionDraft]] = {}
            dropped_attribute_merges: set[str] = set()
            invalid_cross_commands: set[str] = set()
            semantic_degraded_ids: set[str] = set()
            semantic_failures_by_id: dict[str, set[str]] = {}
            coverage_missing_ids: set[str] = set()
            coverage_duplicate_ids: set[str] = set()
            coverage_extra_ids: set[str] = set()

            def draft_anchors(short_id: str) -> list[EvidenceLocator]:
                return [
                    locator
                    for candidate_id in draft_by_short[short_id].source_candidate_ids
                    if (candidate := candidate_by_id.get(candidate_id)) is not None
                    for locator in candidate.evidence_locations
                ]

            def validate(output: JudgeCommandOutput) -> None:
                validated_accepted.clear()
                validated_splits.clear()
                dropped_attribute_merges.clear()
                invalid_cross_commands.clear()
                semantic_degraded_ids.clear()
                semantic_failures_by_id.clear()
                coverage_missing_ids.clear()
                coverage_duplicate_ids.clear()
                coverage_extra_ids.clear()
                targets = [
                    *[item.id for item in output.accepted],
                    *[item.id for item in output.rejected],
                    *[item.id for item in output.split],
                    *[item.id for item in output.duplicates],
                    *[item.id for item in output.attribute_merges],
                ]
                accepted_ids = {item.id for item in output.accepted}
                for duplicate_command in output.duplicates:
                    if (
                        duplicate_command.keep_id not in accepted_ids
                        or duplicate_command.keep_id == duplicate_command.id
                    ):
                        invalid_cross_commands.add(duplicate_command.id)
                for merge_command in output.attribute_merges:
                    if (
                        merge_command.keep_id not in accepted_ids
                        or merge_command.keep_id == merge_command.id
                    ):
                        invalid_cross_commands.add(merge_command.id)
                for accepted_command in output.accepted:
                    if accepted_command.id not in draft_by_short:
                        continue
                    try:
                        original = draft_by_short[accepted_command.id].mention
                        accepted_mention = original
                        if accepted_command.changes is not None:
                            accepted_mention = _apply_judge_changes(
                                accepted_mention, accepted_command.changes
                            )
                        accepted_mention = self._normalize_draft_time(
                            accepted_mention,
                            run_id=run_id,
                            stage="judge",
                            subject_id=accepted_command.id,
                        )
                        accepted_mention = self._reconcile_draft_evidence(
                            accepted_mention,
                            document,
                            source,
                            run_id=run_id,
                            stage="judge",
                            subject_id=accepted_command.id,
                            source_candidate_ids=draft_by_short[
                                accepted_command.id
                            ].source_candidate_ids,
                            candidate_anchors=draft_anchors(accepted_command.id),
                            fallback_main_evidence=(
                                original.evidence_locations
                                if accepted_command.changes is not None
                                and accepted_command.changes.evidence_locations is not None
                                else None
                            ),
                        )
                        validate_event_time_semantics(accepted_mention.time)
                        validated_accepted[accepted_command.id] = accepted_mention
                    except (ValidationError, ValueError):
                        validated_accepted[accepted_command.id] = draft_by_short[
                            accepted_command.id
                        ].mention
                for split_command in output.split:
                    if split_command.id not in draft_by_short:
                        continue
                    validated_split: list[MentionDraft] = []
                    for split_index, split_mention in enumerate(
                        split_command.mentions,
                        start=1,
                    ):
                        try:
                            persistent = _judge_mention_to_persistent(split_mention)
                            subject_id = f"{split_command.id}:split:{split_index}"
                            persistent = self._normalize_draft_time(
                                persistent,
                                run_id=run_id,
                                stage="judge",
                                subject_id=subject_id,
                            )
                            persistent = self._reconcile_draft_evidence(
                                persistent,
                                document,
                                source,
                                run_id=run_id,
                                stage="judge",
                                subject_id=subject_id,
                                source_candidate_ids=draft_by_short[
                                    split_command.id
                                ].source_candidate_ids,
                                candidate_anchors=draft_anchors(split_command.id),
                            )
                            validate_event_time_semantics(persistent.time)
                            validated_split.append(persistent)
                        except (ValidationError, ValueError):
                            validated_split = []
                            break
                    if len(validated_split) == len(split_command.mentions):
                        validated_splits[split_command.id] = validated_split
                for merge_command in output.attribute_merges:
                    if (
                        merge_command.keep_id not in draft_by_short
                        or merge_command.keep_id not in accepted_ids
                    ):
                        continue
                    try:
                        target = draft_by_short[merge_command.keep_id]
                        probe = target.mention.model_copy(
                            update={
                                "open_attributes": [
                                    *target.mention.open_attributes,
                                    merge_command.attribute,
                                ]
                            }
                        )
                        reconciled = self._reconcile_draft_evidence(
                            probe,
                            document,
                            source,
                            run_id=run_id,
                            stage="judge",
                            subject_id=f"{merge_command.id}:attribute",
                            source_candidate_ids=target.source_candidate_ids,
                            candidate_anchors=draft_anchors(merge_command.keep_id),
                        )
                        if len(reconciled.open_attributes) == len(target.mention.open_attributes):
                            dropped_attribute_merges.add(merge_command.id)
                        else:
                            merge_command.attribute = reconciled.open_attributes[-1]
                    except ValueError:
                        dropped_attribute_merges.add(merge_command.id)
                target_set = set(targets)
                coverage_missing_ids.update(draft_ids - target_set)
                coverage_extra_ids.update(target_set - draft_ids)
                coverage_duplicate_ids.update(
                    value for value in targets if targets.count(value) > 1
                )
                if coverage_missing_ids or coverage_extra_ids or coverage_duplicate_ids:
                    coverage_payload = {
                        "missing": sorted(coverage_missing_ids),
                        "extra": sorted(coverage_extra_ids),
                        "duplicates": sorted(coverage_duplicate_ids),
                        "recovery": "TARGETED_COVERAGE_REQUEST",
                    }
                    self.registry.append_decision_audit(
                        DecisionAuditRecord(
                            audit_id=(
                                f"judge-coverage-degraded:{run_id}:{batch_index}:"
                                f"{_hash_json(coverage_payload)[:16]}"
                            ),
                            run_id=run_id,
                            decision_type="JUDGE_COVERAGE_DEGRADED",
                            subject_id=f"judge-batch:{batch_index}",
                            payload=coverage_payload,
                        )
                    )
                semantic_failures = _judge_semantic_failures(
                    output,
                    draft_by_short,
                    validated_accepted,
                    validated_splits,
                )
                semantic_failures_by_id.update(semantic_failures)
                for short_id, codes in sorted(semantic_failures.items()):
                    for code in sorted(codes):
                        self.registry.append_decision_audit(
                            DecisionAuditRecord(
                                audit_id=(
                                    f"judge-semantic:{run_id}:{batch_index}:"
                                    f"{short_id}:{code}:initial"
                                ),
                                run_id=run_id,
                                decision_type="JUDGE_SEMANTIC_VALIDATION",
                                subject_id=short_to_full.get(short_id, short_id),
                                payload={
                                    "code": code,
                                    "repair_attempt": False,
                                },
                            )
                        )

            command_group_names = (
                "accepted",
                "rejected",
                "split",
                "duplicates",
                "attribute_merges",
            )

            def command_ids(value: JudgeCommandOutput) -> list[str]:
                return [
                    command.id
                    for group_name in command_group_names
                    for command in getattr(value, group_name)
                ]

            def without_command_ids(
                value: JudgeCommandOutput,
                removed_ids: set[str],
            ) -> JudgeCommandOutput:
                return JudgeCommandOutput.model_validate(
                    {
                        group_name: [
                            command.model_dump(mode="json", exclude_unset=True)
                            for command in getattr(value, group_name)
                            if command.id not in removed_ids
                        ]
                        for group_name in command_group_names
                    }
                )

            def merge_command_outputs(
                base: JudgeCommandOutput,
                additions: Sequence[JudgeCommandOutput],
            ) -> JudgeCommandOutput:
                payload = {
                    group_name: [
                        command.model_dump(mode="json", exclude_unset=True)
                        for command in getattr(base, group_name)
                    ]
                    for group_name in command_group_names
                }
                for addition in additions:
                    for group_name in command_group_names:
                        payload[group_name].extend(
                            command.model_dump(mode="json", exclude_unset=True)
                            for command in getattr(addition, group_name)
                        )
                return JudgeCommandOutput.model_validate(payload)

            try:
                output = self._invoke_typed(
                    client=client,
                    request=request,
                    output_type=JudgeCommandOutput,
                    semantic_validator=validate,
                    stage="judge",
                    repair_on_failure=False,
                )
            except SingleDocumentPipelineError as exc:
                validated_accepted.clear()
                validated_splits.clear()
                output = JudgeCommandOutput(
                    accepted=[
                        JudgeAcceptedCommand(
                            id=short_id,
                            reason="JUDGE_BATCH_FAILED_RETAIN_GROUNDER_DRAFT",
                        )
                        for short_id in draft_by_short
                    ]
                )
                self.registry.append_decision_audit(
                    DecisionAuditRecord(
                        audit_id=f"judge-batch-degraded:{run_id}:{batch_index}",
                        run_id=run_id,
                        decision_type="JUDGE_BATCH_DEGRADED",
                        subject_id=f"judge-batch:{batch_index}",
                        payload={
                            "code": exc.code,
                            "fallback": "ACCEPT_GROUNDER_DRAFT",
                            "draft_count": len(batch),
                        },
                    )
                )
                validate(output)

            # Drop unknown commands and every conflicting duplicate command before
            # recovering only the affected in-batch IDs.
            recovery_ids = set(coverage_missing_ids)
            recovery_ids.update(coverage_duplicate_ids.intersection(draft_ids))
            if coverage_extra_ids or coverage_duplicate_ids:
                output = without_command_ids(
                    output,
                    coverage_extra_ids | coverage_duplicate_ids,
                )
                validate(output)
                recovery_ids.update(coverage_missing_ids)

            if recovery_ids:
                recovery_client = _AuditedStructuredClient(
                    client=self.m4_client,
                    registry=self.registry,
                    run_id=run_id,
                    tier=ModelTier.M2,
                    model=self.model_m2,
                    stage="judge_coverage_recovery",
                    summaries=summaries,
                )
                recovery_request = StructuredModelRequest(
                    system_prompt=(
                        _prompt("judge.md")
                        + "\n\nReturn exactly one Judge command for every supplied missing "
                        "draft ID. Review only these drafts under the same Eventhood, "
                        "Atomicity, field-correctness, and consolidation rules; do not return "
                        "any other ID."
                    ),
                    user_prompt=json.dumps(
                        {
                            "published_at": _published_at_model(source.published_at),
                            "document": grounder_context(
                                document,
                                [
                                    draft_by_short[short_id].mention
                                    for short_id in sorted(recovery_ids)
                                ],
                            ),
                            "drafts": [
                                JudgeDraftInput(
                                    id=short_id,
                                    mention=_judge_mention(draft_by_short[short_id].mention),
                                ).model_dump(mode="json")
                                for short_id in sorted(recovery_ids)
                            ],
                        },
                        ensure_ascii=False,
                    ),
                    json_schema=_compact_model_schema(JudgeCommandOutput.model_json_schema()),
                )

                def validate_coverage_recovery(value: JudgeCommandOutput) -> None:
                    ids = command_ids(value)
                    if len(ids) != len(set(ids)) or set(ids) != recovery_ids:
                        raise ValueError("judge_coverage_recovery_must_exactly_cover_targets")

                try:
                    recovered = self._invoke_typed(
                        client=recovery_client,
                        request=recovery_request,
                        output_type=JudgeCommandOutput,
                        semantic_validator=validate_coverage_recovery,
                        stage="judge_coverage_recovery",
                        repair_on_failure=False,
                    )
                    output = merge_command_outputs(output, [recovered])
                except Exception as exc:
                    error_code = (
                        exc.code
                        if isinstance(exc, SingleDocumentPipelineError)
                        else type(exc).__name__
                    )
                    output = merge_command_outputs(
                        output,
                        [
                            JudgeCommandOutput(
                                accepted=[
                                    JudgeAcceptedCommand(
                                        id=short_id,
                                        reason="JUDGE_COVERAGE_RECOVERY_FAILED_RETAIN_GROUNDER_DRAFT",
                                    )
                                    for short_id in sorted(recovery_ids)
                                ]
                            )
                        ],
                    )
                    self.registry.append_decision_audit(
                        DecisionAuditRecord(
                            audit_id=(f"judge-coverage-recovery-failed:{run_id}:{batch_index}"),
                            run_id=run_id,
                            decision_type="JUDGE_COVERAGE_RECOVERY_FAILED",
                            subject_id=f"judge-batch:{batch_index}",
                            payload={
                                "target_ids": sorted(recovery_ids),
                                "code": error_code,
                                "fallback": "ACCEPT_GROUNDER_ITEMS",
                            },
                        )
                    )
                validate(output)

            def business_error_payload(codes: set[str]) -> list[dict[str, str]]:
                corrections = {
                    "MULTIPLE_PRIMARY_METRICS": (
                        "Split independent primary metric facts; keep comparisons and bounds "
                        "with the metric they qualify."
                    ),
                    "OPPOSING_CORE_ACTIONS": (
                        "Split opposing actions unless the evidence expresses one bounded "
                        "transition."
                    ),
                    "OPPOSING_SUBJECT_ACTIONS": (
                        "Keep each subject with only its own action and supported fields."
                    ),
                    "GENERIC_UMBRELLA_DUPLICATE": (
                        "Reject an exhausted umbrella, or preserve it only when it has an "
                        "independent artifact or action."
                    ),
                    "LIKELY_FRAGMENTATION": (
                        "Do not create duplicate or qualifier-only replacement Mentions."
                    ),
                    "GUIDANCE_ASSERTION_CONFLICT": (
                        "Align the predicate and Assertion State with the supported fact."
                    ),
                }
                return [
                    {
                        "code": code,
                        "required_correction": corrections.get(
                            code,
                            (
                                "Correct this business-invalid Mention without losing "
                                "supported fields."
                            ),
                        ),
                    }
                    for code in sorted(codes)
                ]

            initial_semantic_failures = {
                short_id: set(codes)
                for short_id, codes in semantic_failures_by_id.items()
                if short_id in draft_ids
            }

            def repair_semantic_item(
                item: tuple[str, set[str]],
            ) -> tuple[str, JudgeCommandOutput | None, str | None]:
                short_id, codes = item
                repair_client = _AuditedStructuredClient(
                    client=self.m4_client,
                    registry=self.registry,
                    run_id=run_id,
                    tier=ModelTier.M2,
                    model=self.model_m2,
                    stage="judge_item_repair",
                    summaries=summaries,
                )
                current_command = without_command_ids(
                    output,
                    draft_ids - {short_id},
                )
                peer_ids = [
                    peer_id
                    for peer_id in draft_by_short
                    if peer_id != short_id
                    and (
                        set(draft_by_short[peer_id].source_candidate_ids)
                        & set(draft_by_short[short_id].source_candidate_ids)
                    )
                ][:6]
                repair_request = StructuredModelRequest(
                    system_prompt=(
                        _prompt("judge.md")
                        + "\n\nRepair exactly one Judge decision using `business_errors`. "
                        "Return exactly one command for `target_id` and no command for any "
                        "other ID. Preserve every source-supported fact and exact evidence; "
                        "apply the error-specific correction rather than only rewriting the "
                        "reason. If the draft is compound, SPLIT it into complete atomic "
                        "replacements. If a generic umbrella is exhausted by the supplied "
                        "peer facts and has no independent artifact or action, REJECT it."
                    ),
                    user_prompt=json.dumps(
                        {
                            "published_at": _published_at_model(source.published_at),
                            "target_id": short_id,
                            "drafts": [
                                JudgeDraftInput(
                                    id=short_id,
                                    mention=_judge_mention(draft_by_short[short_id].mention),
                                ).model_dump(mode="json")
                            ],
                            "target_draft": JudgeDraftInput(
                                id=short_id,
                                mention=_judge_mention(draft_by_short[short_id].mention),
                            ).model_dump(mode="json"),
                            "invalid_command": current_command.model_dump(mode="json"),
                            "business_errors": business_error_payload(codes),
                            "peer_drafts": [
                                JudgeDraftInput(
                                    id=peer_id,
                                    mention=_judge_mention(draft_by_short[peer_id].mention),
                                ).model_dump(mode="json")
                                for peer_id in peer_ids
                            ],
                            "document": grounder_context(
                                document,
                                [
                                    draft_by_short[short_id].mention,
                                    *[draft_by_short[peer_id].mention for peer_id in peer_ids],
                                ],
                            ),
                        },
                        ensure_ascii=False,
                    ),
                    json_schema=_compact_model_schema(JudgeCommandOutput.model_json_schema()),
                )

                def validate_item_repair(value: JudgeCommandOutput) -> None:
                    ids = command_ids(value)
                    if ids != [short_id]:
                        raise ValueError("judge_item_repair_must_return_exactly_one_target")

                try:
                    repaired = self._invoke_typed(
                        client=repair_client,
                        request=repair_request,
                        output_type=JudgeCommandOutput,
                        semantic_validator=validate_item_repair,
                        stage="judge_item_repair",
                        repair_on_failure=False,
                    )
                    return short_id, repaired, None
                except Exception as exc:
                    error_code = (
                        exc.code
                        if isinstance(exc, SingleDocumentPipelineError)
                        else type(exc).__name__
                    )
                    return short_id, None, error_code

            if initial_semantic_failures:
                with ThreadPoolExecutor(
                    max_workers=min(3, len(initial_semantic_failures))
                ) as repair_executor:
                    repairs = list(
                        repair_executor.map(
                            repair_semantic_item,
                            sorted(initial_semantic_failures.items()),
                        )
                    )
                repaired_by_id = {
                    short_id: repaired for short_id, repaired, _ in repairs if repaired is not None
                }
                output = without_command_ids(output, set(initial_semantic_failures))
                output = merge_command_outputs(
                    output,
                    [repaired_by_id[short_id] for short_id in sorted(repaired_by_id)],
                )
                unrepaired_ids = set(initial_semantic_failures) - set(repaired_by_id)
                if unrepaired_ids:
                    output = merge_command_outputs(
                        output,
                        [
                            JudgeCommandOutput(
                                accepted=[
                                    JudgeAcceptedCommand(
                                        id=short_id,
                                        reason="SEMANTIC_REPAIR_FAILED_RETAIN_GROUNDER_ITEM",
                                    )
                                    for short_id in sorted(unrepaired_ids)
                                ]
                            )
                        ],
                    )
                    semantic_degraded_ids.update(unrepaired_ids)
                validate(output)
                still_invalid_ids = set(semantic_failures_by_id)
                if still_invalid_ids:
                    output = without_command_ids(output, still_invalid_ids)
                    output = merge_command_outputs(
                        output,
                        [
                            JudgeCommandOutput(
                                accepted=[
                                    JudgeAcceptedCommand(
                                        id=short_id,
                                        reason="SEMANTIC_REPAIR_FAILED_RETAIN_GROUNDER_ITEM",
                                    )
                                    for short_id in sorted(still_invalid_ids)
                                ]
                            )
                        ],
                    )
                    semantic_degraded_ids.update(still_invalid_ids)
                    validate(output)
                    self.registry.append_decision_audit(
                        DecisionAuditRecord(
                            audit_id=f"judge-semantic-degraded:{run_id}:{batch_index}",
                            run_id=run_id,
                            decision_type="JUDGE_SEMANTIC_DEGRADED",
                            subject_id=f"judge-batch:{batch_index}",
                            payload={
                                "invalid_ids": sorted(still_invalid_ids),
                                "fallback": "ACCEPT_GROUNDER_ITEMS",
                            },
                        )
                    )
            persisted: list[JudgeDecisionRecord] = []
            decided_short_ids: set[str] = set()

            def append_decision(
                *,
                short_id: str,
                action: JudgeAction,
                reason: str,
                revised_mention: MentionDraft | None = None,
                split_mentions: list[MentionDraft] | None = None,
                keep_id: str | None = None,
                attribute: OpenAttributeDraft | None = None,
            ) -> None:
                if short_id not in short_to_full or short_id in decided_short_ids:
                    return
                decided_short_ids.add(short_id)
                provisional = JudgeDecisionRecord(
                    decision_id="pending",
                    target_draft_id=short_to_full[short_id],
                    action=action,
                    reason=reason,
                    revised_mention=revised_mention,
                    split_mentions=split_mentions or [],
                    target_mention_id=(short_to_full[keep_id] if keep_id is not None else None),
                    attribute=attribute,
                )
                identity = _hash_json(provisional.model_dump(mode="json", exclude={"decision_id"}))
                persisted.append(
                    provisional.model_copy(
                        update={"decision_id": f"judge:{source.message_id}:{identity}"}
                    )
                )

            for accepted_command in output.accepted:
                if accepted_command.id not in draft_by_short:
                    continue
                revised = validated_accepted.get(
                    accepted_command.id,
                    draft_by_short[accepted_command.id].mention,
                )
                original = draft_by_short[accepted_command.id].mention
                append_decision(
                    short_id=accepted_command.id,
                    action=JudgeAction.ACCEPT,
                    reason=(
                        "SEMANTIC_REPAIR_FAILED_RETAIN_GROUNDER_ITEM"
                        if accepted_command.id in semantic_degraded_ids
                        else accepted_command.reason
                    ),
                    revised_mention=(revised if revised != original else None),
                )
            for rejected_command in output.rejected:
                append_decision(
                    short_id=rejected_command.id,
                    action=JudgeAction.REJECT,
                    reason=rejected_command.reason,
                )
            for split_command in output.split:
                if split_command.id not in validated_splits:
                    append_decision(
                        short_id=split_command.id,
                        action=JudgeAction.ACCEPT,
                        reason="INVALID_SPLIT_RETAIN_GROUNDER_DRAFT",
                    )
                    continue
                append_decision(
                    short_id=split_command.id,
                    action=JudgeAction.SPLIT,
                    reason=split_command.reason,
                    split_mentions=validated_splits[split_command.id],
                )
            for duplicate_command in output.duplicates:
                if duplicate_command.id in invalid_cross_commands:
                    append_decision(
                        short_id=duplicate_command.id,
                        action=JudgeAction.ACCEPT,
                        reason="INVALID_DUPLICATE_RETAIN_GROUNDER_DRAFT",
                    )
                    continue
                append_decision(
                    short_id=duplicate_command.id,
                    action=JudgeAction.DUPLICATE,
                    reason=duplicate_command.reason,
                    keep_id=duplicate_command.keep_id,
                )
            for merge_command in output.attribute_merges:
                if (
                    merge_command.id in dropped_attribute_merges
                    or merge_command.id in invalid_cross_commands
                ):
                    append_decision(
                        short_id=merge_command.id,
                        action=JudgeAction.ACCEPT,
                        reason="INVALID_ATTRIBUTE_MERGE_RETAIN_GROUNDER_DRAFT",
                    )
                    continue
                append_decision(
                    short_id=merge_command.id,
                    action=JudgeAction.MERGE_AS_ATTRIBUTE,
                    reason=merge_command.reason,
                    keep_id=merge_command.keep_id,
                    attribute=merge_command.attribute,
                )
            for short_id in sorted(draft_ids - decided_short_ids):
                append_decision(
                    short_id=short_id,
                    action=JudgeAction.ACCEPT,
                    reason="JUDGE_MISSING_DECISION_RETAIN_GROUNDER_DRAFT",
                )
            return batch_index, persisted

        with ThreadPoolExecutor(max_workers=min(3, len(batches))) as executor:
            outputs = list(executor.map(process_batch, enumerate(batches)))
        return [decision for _, batch_decisions in sorted(outputs) for decision in batch_decisions]

    def _apply_judge(
        self,
        drafts: list[GroundedMentionDraft],
        decisions: list[JudgeDecisionRecord],
    ) -> list[MentionDraft]:
        draft_ids = {item.draft_id for item in drafts}
        targets = [item.target_draft_id for item in decisions]
        if set(targets) != draft_ids or len(targets) != len(set(targets)):
            raise SingleDocumentPipelineError("judge", "incomplete_decision_coverage")
        by_target = {item.target_draft_id: item for item in decisions}
        accepted_targets = {
            item.target_draft_id for item in decisions if item.action is JudgeAction.ACCEPT
        }
        for decision in decisions:
            if decision.action not in {
                JudgeAction.DUPLICATE,
                JudgeAction.MERGE_AS_ATTRIBUTE,
            }:
                continue
            if (
                decision.target_mention_id not in accepted_targets
                or decision.target_mention_id == decision.target_draft_id
            ):
                raise SingleDocumentPipelineError("judge", "invalid_keep_target")
        retained: dict[str, list[MentionDraft]] = {}
        draft_by_id = {item.draft_id: item for item in drafts}
        for draft in drafts:
            decision = by_target[draft.draft_id]
            if decision.action is JudgeAction.ACCEPT:
                retained[draft.draft_id] = [decision.revised_mention or draft.mention]
            elif decision.action is JudgeAction.SPLIT:
                retained[draft.draft_id] = list(decision.split_mentions)
            elif decision.action in {
                JudgeAction.REJECT,
                JudgeAction.DUPLICATE,
                JudgeAction.MERGE_AS_ATTRIBUTE,
            }:
                retained[draft.draft_id] = []
        for decision in decisions:
            if decision.action is not JudgeAction.DUPLICATE:
                continue
            target = decision.target_mention_id
            if target is None or not retained.get(target):
                raise SingleDocumentPipelineError("judge", "invalid_duplicate_target")
            mention = retained[target][0]
            supplemental = draft_by_id[decision.target_draft_id].mention.evidence_locations
            combined = {
                (item.segment_id, item.text): item
                for item in [*mention.evidence_locations, *supplemental]
            }
            retained[target][0] = mention.model_copy(
                update={"evidence_locations": list(combined.values())}
            )
        for decision in decisions:
            if decision.action is not JudgeAction.MERGE_AS_ATTRIBUTE:
                continue
            target = decision.target_mention_id
            if target is None or not retained.get(target) or decision.attribute is None:
                raise SingleDocumentPipelineError("judge", "invalid_attribute_merge_target")
            mention = retained[target][0]
            retained[target][0] = mention.model_copy(
                update={"open_attributes": [*mention.open_attributes, decision.attribute]}
            )
        return [mention for draft in drafts for mention in retained[draft.draft_id]]

    def _judge_lineage(
        self,
        drafts: list[GroundedMentionDraft],
        decisions: list[JudgeDecisionRecord],
    ) -> list[dict[str, object]]:
        draft_by_id = {draft.draft_id: draft for draft in drafts}
        decision_by_target = {decision.target_draft_id: decision for decision in decisions}
        contributors: dict[str, list[JudgeDecisionRecord]] = {
            draft.draft_id: [] for draft in drafts
        }
        for decision in decisions:
            if (
                decision.action
                in {
                    JudgeAction.DUPLICATE,
                    JudgeAction.MERGE_AS_ATTRIBUTE,
                }
                and decision.target_mention_id in contributors
            ):
                contributors[decision.target_mention_id].append(decision)

        lineage: list[dict[str, object]] = []
        for draft in drafts:
            decision = decision_by_target[draft.draft_id]
            if decision.action is JudgeAction.ACCEPT:
                related = [decision, *contributors[draft.draft_id]]
                source_drafts = [draft_by_id[item.target_draft_id] for item in related]
                lineage.append(
                    {
                        "derivation_kind": "ACCEPT",
                        "grounder_draft_ids": [item.draft_id for item in source_drafts],
                        "source_candidate_ids": sorted(
                            {
                                candidate_id
                                for item in source_drafts
                                for candidate_id in item.source_candidate_ids
                            }
                        ),
                        "judge_decision_ids": [item.decision_id for item in related],
                        "judge_actions": [item.action.value for item in related],
                    }
                )
            elif decision.action is JudgeAction.SPLIT:
                for split_index in range(len(decision.split_mentions)):
                    lineage.append(
                        {
                            "derivation_kind": "SPLIT",
                            "split_index": split_index,
                            "grounder_draft_ids": [draft.draft_id],
                            "source_candidate_ids": list(draft.source_candidate_ids),
                            "judge_decision_ids": [decision.decision_id],
                            "judge_actions": [decision.action.value],
                        }
                    )
        return lineage

    def _materialize_mention(
        self,
        source: SourceMessage,
        document: PreprocessedDocument,
        draft: MentionDraft,
        *,
        run_id: str,
        source_candidate_ids: Sequence[str] = (),
        candidates: Sequence[DreamCandidate] = (),
    ) -> EventMention:
        source_candidate_id_set = set(source_candidate_ids)
        lineage_anchors = [
            locator
            for candidate in candidates
            if candidate.candidate_id in source_candidate_id_set
            for locator in candidate.evidence_locations
        ]
        lineage_anchor_sources: dict[tuple[str, int, int, str], list[str]] = {}
        for candidate in candidates:
            if candidate.candidate_id not in source_candidate_id_set:
                continue
            for locator in candidate.evidence_locations:
                lineage_anchor_sources.setdefault(
                    (
                        locator.segment_id,
                        locator.start_char,
                        locator.end_char,
                        locator.text,
                    ),
                    [],
                ).append(candidate.candidate_id)

        def recover_from_candidate_anchor(
            raw: EvidenceText,
            candidate_anchors: Sequence[EvidenceLocator],
        ) -> EvidenceLocator | None:
            raw_tokens = set(re.findall(r"[a-z0-9]+", raw.text.casefold()))
            raw_numbers = set(re.findall(r"\d+(?:\.\d+)?", raw.text))
            raw_entities = {
                token.casefold() for token in re.findall(r"\b[A-Z][A-Za-z0-9&.-]+\b", raw.text)
            }
            if len(raw_tokens) < 2:
                return None
            compatible: dict[
                tuple[str, int, int, str],
                EvidenceLocator,
            ] = {}
            for anchor in candidate_anchors:
                if anchor.segment_id != raw.segment_id:
                    continue
                try:
                    locator_to_evidence(anchor, document, source)
                except ValueError:
                    continue
                anchor_tokens = set(re.findall(r"[a-z0-9]+", anchor.text.casefold()))
                anchor_numbers = set(re.findall(r"\d+(?:\.\d+)?", anchor.text))
                if raw_numbers - anchor_numbers:
                    continue
                if raw_entities - anchor_tokens:
                    continue
                coverage = len(raw_tokens.intersection(anchor_tokens)) / len(raw_tokens)
                if coverage < 0.75:
                    continue
                compatible[
                    (
                        anchor.segment_id,
                        anchor.start_char,
                        anchor.end_char,
                        anchor.text,
                    )
                ] = anchor
            return next(iter(compatible.values())) if len(compatible) == 1 else None

        def locate(
            raw: EvidenceText,
            *,
            candidate_anchors: Sequence[EvidenceLocator] = (),
        ) -> tuple[
            EvidenceRecord,
            EvidenceSpan | None,
            str,
            EvidenceLocator | None,
        ]:
            try:
                reconciliation = reconcile_evidence_text(
                    raw,
                    document,
                    source,
                    candidate_anchors=candidate_anchors,
                )
                span = locator_to_evidence(reconciliation.locator, document, source)
                return (
                    EvidenceRecord(
                        segment_id=reconciliation.locator.segment_id,
                        text=reconciliation.locator.text,
                        status=EvidenceRecordStatus.VERIFIED,
                        error_code=(
                            None
                            if reconciliation.resolution == "EXACT"
                            else reconciliation.resolution
                        ),
                    ),
                    span,
                    reconciliation.resolution,
                    reconciliation.locator,
                )
            except ValueError as exc:
                recovered = recover_from_candidate_anchor(raw, candidate_anchors)
                if recovered is not None:
                    span = locator_to_evidence(recovered, document, source)
                    return (
                        EvidenceRecord(
                            segment_id=recovered.segment_id,
                            text=recovered.text,
                            status=EvidenceRecordStatus.VERIFIED,
                            error_code="DREAM_CANDIDATE_ANCHOR_RECOVERY",
                        ),
                        span,
                        "DREAM_CANDIDATE_ANCHOR_RECOVERY",
                        recovered,
                    )
                return (
                    EvidenceRecord(
                        segment_id=raw.segment_id,
                        text=raw.text,
                        status=_evidence_record_status(exc),
                        error_code=_safe_semantic_error_code(exc),
                    ),
                    None,
                    _safe_semantic_error_code(exc),
                    None,
                )

        main_results = [
            locate(item, candidate_anchors=lineage_anchors) for item in draft.evidence_locations
        ]
        evidence_records = [item[0] for item in main_results]
        evidence = [item[1] for item in main_results if item[1] is not None]
        main_anchors = [item[3] for item in main_results if item[3] is not None]
        raw_identity_evidence = [
            {
                "kind": "MAIN",
                "index": index,
                **item.model_dump(mode="json"),
            }
            for index, item in enumerate(draft.evidence_locations)
        ]
        raw_identity_evidence.extend(
            {
                "kind": "ATTRIBUTE",
                "index": index,
                **item.evidence_location.model_dump(mode="json"),
            }
            for index, item in enumerate(draft.open_attributes)
        )
        mention_id = deterministic_mention_id(source.message_id, draft, raw_identity_evidence)
        attributes: list[OpenAttribute] = []
        attribute_results: list[
            tuple[
                EvidenceRecord,
                EvidenceSpan | None,
                str,
                EvidenceLocator | None,
            ]
        ] = []
        for item in draft.open_attributes:
            result = locate(
                item.evidence_location,
                candidate_anchors=main_anchors,
            )
            record, span, _, _ = result
            attribute_results.append(result)
            attributes.append(
                OpenAttribute(
                    key=item.key,
                    value=item.value,
                    evidence_record=record,
                    evidence_span=span,
                )
            )
        mention = EventMention(
            mention_id=mention_id,
            message_id=source.message_id,
            evidence_records=evidence_records,
            evidence_spans=evidence,
            canonical_proposition=draft.canonical_proposition,
            source_claim=draft.source_claim,
            event_family=draft.event_family,
            predicate=draft.predicate,
            participants=[
                Participant(surface=item.surface, role=item.role, entity_id=None)
                for item in draft.participants
            ],
            locations=draft.locations,
            time=EventTime(**draft.time.model_dump()),
            assertion_state=draft.assertion_state,
            quantities=[Quantity(**item.model_dump()) for item in draft.quantities],
            open_attributes=attributes,
            schema_projection=None,
        )
        mention.validate_evidence(source)
        for kind, results in (
            ("MAIN", main_results),
            ("ATTRIBUTE", attribute_results),
        ):
            raw_values = (
                draft.evidence_locations
                if kind == "MAIN"
                else [item.evidence_location for item in draft.open_attributes]
            )
            for index, (record, _, repair_kind, _) in enumerate(results):
                original_hash = hashlib.sha256(raw_values[index].text.encode("utf-8")).hexdigest()
                final_hash = hashlib.sha256(record.text.encode("utf-8")).hexdigest()
                recovered_anchor = results[index][3]
                recovered_candidate_ids = (
                    lineage_anchor_sources.get(
                        (
                            recovered_anchor.segment_id,
                            recovered_anchor.start_char,
                            recovered_anchor.end_char,
                            recovered_anchor.text,
                        ),
                        [],
                    )
                    if repair_kind == "DREAM_CANDIDATE_ANCHOR_RECOVERY"
                    and recovered_anchor is not None
                    else []
                )
                self.registry.append_decision_audit(
                    DecisionAuditRecord(
                        audit_id=(
                            f"mention-evidence:{run_id}:{mention_id}:{kind}:"
                            f"{index}:{original_hash[:16]}"
                        ),
                        run_id=run_id,
                        decision_type="MENTION_EVIDENCE_LOCATION",
                        subject_id=mention_id,
                        payload={
                            "mention_id": mention_id,
                            "evidence_kind": kind,
                            "evidence_index": index,
                            "status": record.status.value,
                            "error_code": record.error_code,
                            "first_divergence_stage": "MATERIALIZE",
                            "repair_kind": repair_kind,
                            "candidate_ids": recovered_candidate_ids,
                            "original_model_text_hash": original_hash,
                            "final_source_text_hash": final_hash,
                        },
                    )
                )
        return mention

    def _complete_reused(
        self,
        *,
        source: SourceMessage,
        previous: SingleDocumentResult,
        run_id: str,
        processing_key: str,
        started_at: datetime,
    ) -> SingleDocumentResult:
        mentions: list[EventMention] = []
        id_map: dict[str, str] = {}
        for prior in previous.mentions:
            provisional = prior.model_copy(update={"message_id": source.message_id})
            identity = _hash_json(
                {
                    "pipeline_version": PIPELINE_VERSION,
                    "message_id": source.message_id,
                    "evidence": [
                        item.model_dump(mode="json")
                        for item in (
                            prior.evidence_records
                            if prior.evidence_records
                            else prior.evidence_spans
                        )
                    ],
                    "predicate": prior.predicate.normalized,
                    "assertion_state": prior.assertion_state.value,
                }
            )
            mention_id = f"mention:{identity}"
            copied = provisional.model_copy(update={"mention_id": mention_id})
            copied.validate_evidence(source)
            mentions.append(copied)
            id_map[prior.mention_id] = mention_id
        decisions: list[NormalizationDecision] = []
        for prior_decision in previous.normalization_decisions:
            mapped_mention_id = id_map.get(prior_decision.mention_id)
            if mapped_mention_id is None:
                continue
            method = prior_decision.method
            digest = hashlib.sha256(
                f"{mapped_mention_id}|{prior_decision.field_path}|{method.value}".encode()
            ).hexdigest()
            decisions.append(
                prior_decision.model_copy(
                    update={
                        "decision_id": f"normalization:{digest}",
                        "mention_id": mapped_mention_id,
                    }
                )
            )
        self.registry.save_normalization_decisions(run_id, decisions)
        result = SingleDocumentResult(
            run_id=run_id,
            message_id=source.message_id,
            processing_key=processing_key,
            status=ProcessingStatus.SUCCEEDED,
            mentions=mentions,
            model_calls=[],
            judge_routing=previous.judge_routing,
            normalization_decisions=decisions,
            failures=[],
            started_at=started_at,
            finished_at=datetime.now(UTC),
            reused=True,
        )
        self.registry.complete_document_run(result)
        for prior_mention_id, mention_id in id_map.items():
            self.registry.append_decision_audit(
                DecisionAuditRecord(
                    audit_id=f"mention-derivation:{run_id}:{mention_id}",
                    run_id=run_id,
                    decision_type="MENTION_DERIVATION",
                    subject_id=mention_id,
                    payload={
                        "message_id": source.message_id,
                        "final_mention_id": mention_id,
                        "derivations": [
                            {
                                "derivation_kind": "EXACT_DOCUMENT_REUSE",
                                "source_mention_id": prior_mention_id,
                                "source_document_run_id": previous.run_id,
                            }
                        ],
                        "normalization_decision_ids": [
                            item.decision_id for item in decisions if item.mention_id == mention_id
                        ],
                    },
                )
            )
        return result
