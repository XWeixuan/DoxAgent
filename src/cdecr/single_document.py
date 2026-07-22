"""Single-document Dreamer → Grounder → mandatory M4 Judge orchestration."""

from __future__ import annotations

import hashlib
import json
import traceback
import uuid
from collections import Counter
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from importlib import resources
from typing import TypeVar

from pydantic import ValidationError

from cdecr.contracts import (
    EventFamily,
    EventMention,
    EventTime,
    OpenAttribute,
    Participant,
    ParticipantRole,
    Quantity,
    SourceMessage,
    StrictModel,
    TimePrecision,
)
from cdecr.models import (
    STRUCTURED_OUTPUT_MODE,
    STRUCTURED_REASONING_EFFORT,
    ModelAdapterError,
    ModelTier,
)
from cdecr.normalization import CATALOG_VERSION, NormalizationEngine
from cdecr.ports import (
    CDECRRegistry,
    DecisionAuditRecord,
    EmbeddingClient,
    EmbeddingResult,
    StructuredModelClient,
    StructuredModelRequest,
    StructuredModelResult,
)
from cdecr.preprocessing import (
    PIPELINE_VERSION,
    align_unique_evidence_locator,
    exact_document_fingerprint,
    grounder_context,
    locate_unique_evidence_text,
    locator_to_evidence,
    preprocess_source,
)
from cdecr.single_document_contracts import (
    DreamCandidate,
    DreamerModelOutput,
    EvidenceLocator,
    FailureSummary,
    GroundedMentionDraft,
    GrounderModelOutput,
    GrounderOutput,
    JudgeAction,
    JudgeDecisionRecord,
    JudgeModelOutput,
    JudgeRouting,
    MentionDraft,
    ModelCallSummary,
    NormalizationDecision,
    PreprocessedDocument,
    ProcessingStatus,
    SingleDocumentResult,
)

PROMPT_VERSION = "single-document-prompts-v6"
GROUNDER_CANDIDATE_BATCH = 24
JUDGE_DRAFT_BATCH = 24
_T = TypeVar("_T", bound=StrictModel)


class SingleDocumentPipelineError(RuntimeError):
    def __init__(
        self, stage: str, code: str, *, repair_payload: str | None = None
    ) -> None:
        self.stage = stage
        self.code = code
        self.repair_payload = repair_payload
        super().__init__(f"single-document stage {stage} failed: {code}")


def _hash_json(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _prompt(name: str) -> str:
    return resources.files("cdecr.prompts.v1").joinpath(name).read_text(encoding="utf-8").strip()


def _repaired_validation_error_code(exc: ValidationError | ValueError) -> str:
    if isinstance(exc, ValidationError):
        return "schema_validation_failed_after_repair"
    message = str(exc).casefold()
    if any(
        token in message
        for token in ("evidence", "segment", "locator", "offset", "slice")
    ):
        return "evidence_validation_failed_after_repair"
    return "semantic_validation_failed_after_repair"


def _safe_semantic_error_code(exc: ValueError) -> str:
    message = str(exc).casefold()
    mappings = (
        ("unknown evidence segment", "evidence_unknown_segment"),
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
        try:
            result = self.client.embed(texts)
        except Exception as exc:
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
                metadata={"input_count": len(texts)},
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
                )
            )
            raise SingleDocumentPipelineError(self.stage, code) from exc
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
            )
        )
        return result


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

    def complete(self, request: StructuredModelRequest) -> StructuredModelResult:
        repaired = request.system_prompt.casefold().startswith("repair")
        call_stage = f"{self.stage}_repair" if repaired else self.stage
        call_id = str(uuid.uuid4())
        input_hash = _hash_json({"system": request.system_prompt, "user": request.user_prompt})
        schema_hash = _hash_json(request.json_schema)
        try:
            result = self.client.complete(request)
        except Exception as exc:
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
                metadata={},
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
            raise SingleDocumentPipelineError(
                call_stage,
                code,
                repair_payload=(
                    exc.raw_response_text if isinstance(exc, ModelAdapterError) else None
                ),
            ) from exc
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
            metadata={},
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
                output_tokens=result.output_tokens,
                latency_ms=result.latency_ms,
                repaired=repaired,
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
        model_m1: str = "text-embedding-v4",
        model_m2: str = "deepseek-v4-flash",
        model_m3: str = "qwen3.7-plus",
        model_m4: str = "qwen3.7-max",
    ) -> None:
        self.registry = registry
        self.embedding_client = embedding_client
        self.m2_client = m2_client
        self.m3_client = m3_client
        self.m4_client = m4_client
        self.model_m1 = model_m1
        self.model_m2 = model_m2
        self.model_m3 = model_m3
        self.model_m4 = model_m4

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
                "catalog_version": CATALOG_VERSION,
                "model_config": self.model_config,
            }
        )

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
            catalog_version=CATALOG_VERSION,
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
            title_embedding = title_client.embed([source.title])
            self.registry.save_embedding(
                owner_kind="source_title",
                owner_id=source.message_id,
                model=title_embedding.model,
                input_hash=hashlib.sha256(source.title.encode("utf-8")).hexdigest(),
                vector=title_embedding.vectors[0],
            )

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
                    run_id,
                    summaries,
                )
                self.registry.save_judge_decisions(run_id, judge_decisions)
                accepted = self._apply_judge(drafts, judge_decisions)
            else:
                accepted = []

            materialized_mentions = [
                self._materialize_mention(source, preprocessing.document, draft)
                for draft in accepted
            ]
            mentions_by_id: dict[str, EventMention] = {}
            duplicate_mention_ids: Counter[str] = Counter()
            for mention in materialized_mentions:
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
            normalization_embedding = _AuditedEmbeddingClient(
                client=self.embedding_client,
                registry=self.registry,
                run_id=run_id,
                model=self.model_m1,
                stage="normalization_m1",
                summaries=summaries,
            )
            normalization_m2 = _AuditedStructuredClient(
                client=self.m2_client,
                registry=self.registry,
                run_id=run_id,
                tier=ModelTier.M2,
                model=self.model_m2,
                stage="normalization_m2",
                summaries=summaries,
            )
            normalizer = NormalizationEngine(
                embedding_client=normalization_embedding,
                fallback_client=normalization_m2,
            )
            normalized_mentions: list[EventMention] = []
            normalization_decisions: list[NormalizationDecision] = []
            for mention in mentions:
                normalized, mention_decisions = normalizer.normalize(
                    mention, ticker_hints=source.ticker_hints
                )
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
                        payload={"error_code": code, "frames": safe_frames},
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
        return [self.process(message_id) for message_id in message_ids]

    def _invoke_typed(
        self,
        *,
        client: _AuditedStructuredClient,
        request: StructuredModelRequest,
        output_type: type[_T],
        semantic_validator: Callable[[_T], None],
        stage: str,
    ) -> _T:
        request_key = _hash_json(
            {"stage": stage, "user_prompt": request.user_prompt}
        )[:16]

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
                    if (
                        isinstance(precision, str)
                        and precision not in allowed_time_precisions
                    ):
                        event_time["precision"] = TimePrecision.UNKNOWN.value
                        normalized_fields["time.precision"] += 1

            if output_type is GrounderModelOutput:
                drafts = payload.get("drafts")
                if isinstance(drafts, list):
                    for draft in drafts:
                        if isinstance(draft, dict):
                            normalize_mention(draft.get("mention"))
            elif output_type is JudgeModelOutput:
                decisions = payload.get("decisions")
                if isinstance(decisions, list):
                    for decision in decisions:
                        if not isinstance(decision, dict):
                            continue
                        normalize_mention(decision.get("revised_mention"))
                        split_mentions = decision.get("split_mentions")
                        if isinstance(split_mentions, list):
                            for mention in split_mentions:
                                normalize_mention(mention)
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
                    "error_code": _safe_semantic_error_code(exc),
                }
            client.registry.append_decision_audit(
                DecisionAuditRecord(
                    audit_id=(
                        f"structured-validation:{client.run_id}:{stage}:"
                        f"{request_key}:{attempt}"
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
                ),
                json_schema=output_type.model_json_schema(),
            )
            try:
                repaired = client.complete(repair)
                normalized_payload = normalize_model_enums(
                    repaired.payload, attempt="repair"
                )
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
                first_error.code in {"invalid_json", "invalid_json_shape"}
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
            if isinstance(first_error, ValidationError):
                validation_error: object = first_error.errors(
                    include_input=False, include_url=False
                )
            else:
                validation_error = str(first_error)
            return repair_and_validate(result.payload, validation_error)

    def _dream(
        self,
        source: SourceMessage,
        document: PreprocessedDocument,
        run_id: str,
        summaries: list[ModelCallSummary],
    ) -> list[DreamCandidate]:
        tier = (
            ModelTier.M3
            if document.is_long_document or document.is_complex_document
            else ModelTier.M2
        )
        raw_client = self.m3_client if tier is ModelTier.M3 else self.m2_client
        model = self.model_m3 if tier is ModelTier.M3 else self.model_m2
        client = _AuditedStructuredClient(
            client=raw_client,
            registry=self.registry,
            run_id=run_id,
            tier=tier,
            model=model,
            stage="dreamer",
            summaries=summaries,
        )
        outputs: list[DreamerModelOutput] = []
        for block in document.document_blocks:
            segment_by_id = {item.segment_id: item for item in document.segments}
            exposed_lengths = {
                segment_id: len(segment_by_id[segment_id].text) for segment_id in block.segment_ids
            }
            for context_segment in document.segments[:2]:
                marker = f"[{context_segment.segment_id}]\n"
                marker_start = block.common_context.find(marker)
                if marker_start < 0:
                    continue
                content_start = marker_start + len(marker)
                next_marker = block.common_context.find("\n\n[", content_start)
                content_end = len(block.common_context) if next_marker < 0 else next_marker
                exposed_lengths[context_segment.segment_id] = max(
                    exposed_lengths.get(context_segment.segment_id, 0),
                    content_end - content_start,
                )
            user_payload = {
                "message_id": source.message_id,
                "allowed_segment_ids": list(exposed_lengths),
                "segment_lengths": exposed_lengths,
                "common_context": block.common_context,
                "block": block.text,
            }
            request = StructuredModelRequest(
                system_prompt=_prompt("dreamer.md"),
                user_prompt=json.dumps(user_payload, ensure_ascii=False),
                json_schema=DreamerModelOutput.model_json_schema(),
            )

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

            output = self._invoke_typed(
                client=client,
                request=request,
                output_type=DreamerModelOutput,
                semantic_validator=validate_dreamer,
                stage="dreamer",
            )
            outputs.append(output)
            if any(reconciliation.values()):
                self.registry.append_decision_audit(
                    DecisionAuditRecord(
                        audit_id=f"dreamer-evidence-reconciliation:{run_id}:{block.block_id}",
                        run_id=run_id,
                        decision_type="DREAMER_EVIDENCE_RECONCILIATION",
                        subject_id=block.block_id,
                        payload=reconciliation,
                    )
                )
        unique: dict[str, DreamCandidate] = {}
        for output in outputs:
            for candidate in output.candidates:
                identity = _hash_json(candidate.model_dump(mode="json"))
                item = DreamCandidate(
                    candidate_id=f"candidate:{source.message_id}:{identity}",
                    **candidate.model_dump(),
                )
                unique[item.candidate_id] = item
        return list(unique.values())

    def _validate_dreamer_output(
        self,
        output: DreamerModelOutput,
        *,
        exposed_lengths: dict[str, int],
        document: PreprocessedDocument,
        source: SourceMessage,
    ) -> tuple[int, int]:
        retained = []
        invalid_locators = 0
        dropped_candidates = 0
        for item in output.candidates:
            valid_locations: dict[str, EvidenceLocator] = {}
            for locator in item.evidence_locations:
                try:
                    aligned = align_unique_evidence_locator(locator, document, source)
                    if (
                        aligned.segment_id not in exposed_lengths
                        or aligned.end_char > exposed_lengths[aligned.segment_id]
                    ):
                        raise ValueError("Dreamer evidence must use an exposed segment")
                    locator_to_evidence(aligned, document, source)
                except ValueError:
                    invalid_locators += 1
                    continue
                valid_locations[_hash_json(aligned.model_dump(mode="json"))] = aligned
            if not valid_locations:
                dropped_candidates += 1
                continue
            item.evidence_locations = list(valid_locations.values())
            retained.append(item)
        output.candidates = retained
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
        ] or [[]]
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
            short_candidates = [
                {
                    "candidate_id": short_id,
                    "statement": candidate.statement,
                    "evidence_locations": [
                        locator.model_dump(mode="json")
                        for locator in candidate.evidence_locations
                    ],
                }
                for short_id, candidate in zip(short_to_full, batch, strict=True)
            ]
            request = StructuredModelRequest(
                system_prompt=_prompt("grounder.md"),
                user_prompt=json.dumps(
                    {
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
                    "model": self.model_m3,
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
                tier=ModelTier.M3,
                model=self.model_m3,
                stage="grounder",
                summaries=summaries,
            )
            candidate_ids = set(short_to_full)

            def validate(output: GrounderModelOutput) -> None:
                known_segments = {segment.segment_id for segment in document.segments}
                for item in output.drafts:
                    if len(item.source_candidate_ids) != len(set(item.source_candidate_ids)):
                        raise ValueError("Grounder candidate IDs must be unique")
                    if any(value not in candidate_ids for value in item.source_candidate_ids):
                        raise ValueError("Grounder returned an unknown Dreamer candidate")
                    quoted_segments = {
                        evidence.segment_id
                        for evidence in [
                            *item.mention.evidence_locations,
                            *(
                                attribute.evidence_location
                                for attribute in item.mention.open_attributes
                            ),
                        ]
                    }
                    if not quoted_segments.issubset(known_segments):
                        raise ValueError("Grounder returned an unknown evidence segment")

            output = self._invoke_typed(
                client=client,
                request=request,
                output_type=GrounderModelOutput,
                semantic_validator=validate,
                stage="grounder",
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
            persisted = GrounderOutput(
                drafts=persisted_drafts,
                issue_flags=output.issue_flags,
            )
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
        return GrounderOutput(
            drafts=list(drafts_by_id.values()),
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
        for evidence in draft.evidence_locations:
            locate_unique_evidence_text(evidence, document, source)
        for attribute in draft.open_attributes:
            locate_unique_evidence_text(attribute.evidence_location, document, source)

    def _judge(
        self,
        source: SourceMessage,
        document: PreprocessedDocument,
        drafts: list[GroundedMentionDraft],
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
                        participant.surface.casefold()
                        for participant in item.mention.participants
                    )
                ),
                item.draft_id,
            )

        ordered = sorted(drafts, key=grouping_key)
        batches = [
            ordered[index : index + JUDGE_DRAFT_BATCH]
            for index in range(0, len(ordered), JUDGE_DRAFT_BATCH)
        ]

        def process_batch(
            indexed_batch: tuple[int, list[GroundedMentionDraft]],
        ) -> tuple[int, JudgeModelOutput]:
            batch_index, batch = indexed_batch
            client = _AuditedStructuredClient(
                client=self.m4_client,
                registry=self.registry,
                run_id=run_id,
                tier=ModelTier.M4,
                model=self.model_m4,
                stage="judge",
                summaries=summaries,
            )
            request = StructuredModelRequest(
                system_prompt=_prompt("judge.md"),
                user_prompt=json.dumps(
                    {
                        "batch_index": batch_index,
                        "batch_count": len(batches),
                        "document": grounder_context(
                            document, [draft.mention for draft in batch]
                        ),
                        "drafts": [item.model_dump(mode="json") for item in batch],
                    },
                    ensure_ascii=False,
                ),
                json_schema=JudgeModelOutput.model_json_schema(),
            )
            draft_ids = {item.draft_id for item in batch}
            draft_by_id = {item.draft_id: item for item in batch}

            def validate(output: JudgeModelOutput) -> None:
                targets = [item.target_draft_id for item in output.decisions]
                if set(targets) != draft_ids or len(targets) != len(set(targets)):
                    raise ValueError("Judge must return exactly one decision for every draft")
                for item in output.decisions:
                    if item.target_mention_id is not None:
                        if (
                            item.target_mention_id not in draft_ids
                            or item.target_mention_id == item.target_draft_id
                        ):
                            raise ValueError("Judge target must name another retained draft")
                    if item.revised_mention is not None:
                        self._validate_draft_evidence(item.revised_mention, document, source)
                    for split in item.split_mentions:
                        self._validate_draft_evidence(split, document, source)
                    if item.attribute is not None:
                        locate_unique_evidence_text(
                            item.attribute.evidence_location, document, source
                        )
                    if item.action is JudgeAction.ACCEPT and item.revised_mention is None:
                        self._validate_draft_evidence(
                            draft_by_id[item.target_draft_id].mention,
                            document,
                            source,
                        )

            output = self._invoke_typed(
                client=client,
                request=request,
                output_type=JudgeModelOutput,
                semantic_validator=validate,
                stage="judge",
            )
            return batch_index, output

        with ThreadPoolExecutor(max_workers=min(3, len(batches))) as executor:
            outputs = list(executor.map(process_batch, enumerate(batches)))
        decisions: list[JudgeDecisionRecord] = []
        for _, output in sorted(outputs):
            for item in output.decisions:
                identity = _hash_json(item.model_dump(mode="json"))
                decisions.append(
                    JudgeDecisionRecord(
                        decision_id=f"judge:{source.message_id}:{identity}",
                        **item.model_dump(),
                    )
                )
        return decisions

    def _apply_judge(
        self,
        drafts: list[GroundedMentionDraft],
        decisions: list[JudgeDecisionRecord],
    ) -> list[MentionDraft]:
        by_target = {item.target_draft_id: item for item in decisions}
        retained: dict[str, list[MentionDraft]] = {}
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

    def _materialize_mention(
        self,
        source: SourceMessage,
        document: PreprocessedDocument,
        draft: MentionDraft,
    ) -> EventMention:
        self._validate_draft_evidence(draft, document, source)
        evidence = [
            locator_to_evidence(
                locate_unique_evidence_text(item, document, source), document, source
            )
            for item in draft.evidence_locations
        ]
        mention_id = deterministic_mention_id(source.message_id, draft, evidence)
        attributes = [
            OpenAttribute(
                key=item.key,
                value=item.value,
                evidence_span=locator_to_evidence(
                    locate_unique_evidence_text(item.evidence_location, document, source),
                    document,
                    source,
                ),
            )
            for item in draft.open_attributes
        ]
        mention = EventMention(
            mention_id=mention_id,
            message_id=source.message_id,
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
            local_package_hint=draft.local_package_hint,
        )
        mention.validate_evidence(source)
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
                    "evidence": [item.model_dump(mode="json") for item in prior.evidence_spans],
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
        return result
