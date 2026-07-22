"""Isolated Dreamer -> Grounder versus direct-Grounder experiment.

This module deliberately stops before Judge and normalization.  It uses a dedicated
Registry per arm and stores full-text artifacts only under the caller-selected local
path (normally ``.tmp``).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError

from cdecr.config import CDECRSettings
from cdecr.contracts import EventMention, NonEmptyString, SourceMessage, StrictModel
from cdecr.models import (
    DashScopeEmbeddingClient,
    DashScopeStructuredModelClient,
    ModelAdapterError,
    ModelTier,
)
from cdecr.normalization import CATALOG_VERSION
from cdecr.ports import StructuredModelRequest
from cdecr.preprocessing import PIPELINE_VERSION, grounder_context, preprocess_source
from cdecr.registry import SQLiteCDECRRegistry
from cdecr.single_document import (
    PROMPT_VERSION,
    SingleDocumentProcessor,
    _AuditedStructuredClient,
    _hash_json,
    _prompt,
)
from cdecr.single_document_contracts import (
    DreamCandidate,
    GroundedMentionDraft,
    GrounderModelOutput,
    GrounderOutput,
    JudgeRouting,
    ModelCallSummary,
    ProcessingStatus,
    SingleDocumentResult,
)
from cdecr.step4_evaluation import (
    M4ReviewOutput,
    _m4_review_request,
    _record_m4_call,
)

EXPERIMENT_VERSION = "cdecr-dreamer-grounder-ab-v1"
DIRECT_MODE = "DIRECT_DOCUMENT_DISCOVERY"
DIRECT_SOURCE_ID = "DIRECT_DOCUMENT"


class ExperimentManifestRow(StrictModel):
    cohort: Literal["prior_ab", "prior_step2", "new_db_review"] | None = None
    source_row_id: NonEmptyString
    document_fingerprint: NonEmptyString
    message_id: NonEmptyString
    text_chars: int = Field(ge=200, le=5000)
    paragraph_count: int = Field(ge=1)
    source_name: NonEmptyString
    title: NonEmptyString
    review_status: Literal["REVIEWED"]
    single_article: Literal[True]
    aggregate_shape: Literal[False]
    transcript_shape: Literal[False]
    review_note: NonEmptyString


class ExperimentManifest(StrictModel):
    manifest_version: Literal["cdecr-dreamer-grounder-ab-v1"]
    query: dict[str, object]
    selection_policy: dict[str, object]
    # Keep the reviewed-corpus contract bounded while allowing larger follow-up
    # experiments to reuse the same Grounder-only cutoff runner.
    rows: list[ExperimentManifestRow] = Field(min_length=1, max_length=200)


class ArmDocumentResult(StrictModel):
    source_row_id: NonEmptyString
    message_id: NonEmptyString
    status: Literal["SUCCEEDED", "FAILED"]
    run_id: NonEmptyString
    dream_candidates: list[DreamCandidate]
    grounder: GrounderOutput | None
    mentions: list[EventMention]
    model_calls: list[ModelCallSummary]
    error_code: str | None = None


class ArmArtifact(StrictModel):
    experiment_version: Literal["cdecr-dreamer-grounder-ab-v1"] = (
        "cdecr-dreamer-grounder-ab-v1"
    )
    arm: Literal["dreamer_grounder", "grounder_only"]
    model_m2: NonEmptyString
    model_m3: NonEmptyString
    output_mode: Literal["json_object"] = "json_object"
    reasoning_effort: Literal["none"] = "none"
    prompt_sha256: NonEmptyString
    documents: list[ArmDocumentResult]


class PairedReviewDocument(StrictModel):
    message_id: NonEmptyString
    dreamer_label: Literal["X", "Y"]
    grounder_label: Literal["X", "Y"]
    review: M4ReviewOutput


class PairedReviewArtifact(StrictModel):
    experiment_version: Literal["cdecr-dreamer-grounder-ab-v1"] = (
        "cdecr-dreamer-grounder-ab-v1"
    )
    model: NonEmptyString
    documents: list[PairedReviewDocument]


class ArmDocumentMetrics(StrictModel):
    message_id: NonEmptyString
    gold_count: int = Field(ge=0)
    extracted_count: int = Field(ge=0)
    supported_count: int = Field(ge=0)
    recalled_gold_count: int = Field(ge=0)
    precision: float = Field(ge=0, le=1)
    recall: float = Field(ge=0, le=1)
    f1: float = Field(ge=0, le=1)


class AggregateMetrics(StrictModel):
    document_count: int = Field(ge=0)
    completed_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    extracted_count: int = Field(ge=0)
    supported_count: int = Field(ge=0)
    gold_count: int = Field(ge=0)
    recalled_gold_count: int = Field(ge=0)
    micro_precision: float = Field(ge=0, le=1)
    micro_recall: float = Field(ge=0, le=1)
    micro_f1: float = Field(ge=0, le=1)
    macro_f1: float = Field(ge=0, le=1)
    per_document: list[ArmDocumentMetrics]


class ComparisonReport(StrictModel):
    experiment_version: Literal["cdecr-dreamer-grounder-ab-v1"] = (
        "cdecr-dreamer-grounder-ab-v1"
    )
    dreamer_grounder: AggregateMetrics
    grounder_only: AggregateMetrics
    macro_f1_delta_dreamer_minus_direct: float
    bootstrap_95_ci: tuple[float, float]
    dreamer_wins: int = Field(ge=0)
    ties: int = Field(ge=0)
    direct_wins: int = Field(ge=0)
    obvious_difference: bool
    obvious_difference_rule: NonEmptyString
    completed_pair_count: int = Field(ge=0)
    completed_pair_macro_f1_delta: float
    completed_pair_bootstrap_95_ci: tuple[float, float]
    obvious_difference_without_failed_documents: bool


def _write_json(value: StrictModel, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _load_corpus(
    snapshot_path: Path, manifest_path: Path
) -> tuple[ExperimentManifest, list[tuple[ExperimentManifestRow, SourceMessage]]]:
    manifest = ExperimentManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    rows = {
        str(item["source_row_id"]): item
        for line in snapshot_path.read_text(encoding="utf-8").splitlines()
        if (item := json.loads(line))
    }
    selected: list[tuple[ExperimentManifestRow, SourceMessage]] = []
    for row in manifest.rows:
        raw = rows.get(row.source_row_id)
        if raw is None:
            raise ValueError(f"snapshot is missing reviewed row {row.source_row_id}")
        if raw.get("document_fingerprint") != row.document_fingerprint:
            raise ValueError(f"fingerprint mismatch for reviewed row {row.source_row_id}")
        source = SourceMessage.model_validate(raw["message"])
        if (
            source.message_id != row.message_id
            or source.title != row.title
            or source.source_name != row.source_name
            or len(source.text) != row.text_chars
        ):
            raise ValueError(f"reviewed metadata mismatch for row {row.source_row_id}")
        selected.append((row, source))
    return manifest, selected


def _build_processor(
    settings: CDECRSettings, registry: SQLiteCDECRRegistry
) -> SingleDocumentProcessor:
    api_key = settings.require_dashscope()
    fallback = settings.dashscope_fallback_api_keys()
    embedding = DashScopeEmbeddingClient(
        api_key=api_key,
        base_url=settings.dashscope_base_url,
        model=settings.model_m1,
        dimensions=settings.embedding_dimensions,
        timeout_seconds=settings.model_timeout_seconds,
        fallback_api_keys=fallback,
    )
    m2 = DashScopeStructuredModelClient(
        tier=ModelTier.M2,
        api_key=api_key,
        base_url=settings.dashscope_base_url,
        model=settings.model_m2,
        timeout_seconds=settings.model_timeout_seconds,
        fallback_api_keys=fallback,
    )
    m3 = DashScopeStructuredModelClient(
        tier=ModelTier.M3,
        api_key=api_key,
        base_url=settings.dashscope_base_url,
        model=settings.model_m3,
        timeout_seconds=settings.model_timeout_seconds,
        fallback_api_keys=fallback,
    )
    m4 = DashScopeStructuredModelClient(
        tier=ModelTier.M4,
        api_key=api_key,
        base_url=settings.dashscope_base_url,
        model=settings.model_m4,
        timeout_seconds=settings.model_timeout_seconds,
        fallback_api_keys=fallback,
    )
    return SingleDocumentProcessor(
        registry=registry,
        embedding_client=embedding,
        m2_client=m2,
        m3_client=m3,
        m4_client=m4,
        model_m1=settings.model_m1,
        model_m2=settings.model_m2,
        model_m3=settings.model_m3,
        model_m4=settings.model_m4,
    )


def _direct_ground(
    *,
    processor: SingleDocumentProcessor,
    source: SourceMessage,
    document: object,
    run_id: str,
    summaries: list[ModelCallSummary],
) -> GrounderOutput:
    if DIRECT_MODE not in _prompt("grounder.md"):
        raise ValueError("grounder prompt has not been temporarily enabled for direct mode")
    request = StructuredModelRequest(
        system_prompt=_prompt("grounder.md"),
        user_prompt=json.dumps(
            {
                "mode": DIRECT_MODE,
                "document": grounder_context(document, []),  # type: ignore[arg-type]
                "candidates": [],
                "direct_source_id": "c1",
            },
            ensure_ascii=False,
        ),
        json_schema=GrounderModelOutput.model_json_schema(),
    )
    client = _AuditedStructuredClient(
        client=processor.m3_client,
        registry=processor.registry,
        run_id=run_id,
        tier=ModelTier.M3,
        model=processor.model_m3,
        stage="grounder_direct",
        summaries=summaries,
    )

    def validate(output: GrounderModelOutput) -> None:
        for draft in output.drafts:
            draft.source_candidate_ids = ["c1"]
            processor._validate_draft_quotes_exist(  # noqa: SLF001 - isolated experiment
                draft.mention, document  # type: ignore[arg-type]
            )

    output = processor._invoke_typed(  # noqa: SLF001 - isolated experiment
        client=client,
        request=request,
        output_type=GrounderModelOutput,
        semantic_validator=validate,
        stage="grounder_direct",
    )
    drafts: list[GroundedMentionDraft] = []
    for item in output.drafts:
        identity = _hash_json(item.model_dump(mode="json"))
        drafts.append(
            GroundedMentionDraft(
                draft_id=f"draft:{source.message_id}:{identity}",
                source_candidate_ids=[DIRECT_SOURCE_ID],
                mention=item.mention,
            )
        )
    return GrounderOutput(drafts=drafts, issue_flags=output.issue_flags)


def _run_one_document(
    *,
    arm: Literal["dreamer_grounder", "grounder_only"],
    row: ExperimentManifestRow,
    source: SourceMessage,
    processor: SingleDocumentProcessor,
    registry: SQLiteCDECRRegistry,
    prompt_hash: str,
) -> ArmDocumentResult:
    run_id = str(uuid.uuid4())
    processing_key = _hash_json(
        {
            "experiment": EXPERIMENT_VERSION,
            "arm": arm,
            "message_id": source.message_id,
            "fingerprint": row.document_fingerprint,
            "prompt_sha256": prompt_hash,
            "models": processor.model_config,
        }
    )
    started_at = datetime.now(UTC)
    summaries: list[ModelCallSummary] = []
    candidates: list[DreamCandidate] = []
    run_started = False
    try:
        run_started = registry.start_document_run(
            run_id=run_id,
            processing_key=processing_key,
            message_id=source.message_id,
            pipeline_version=f"{PIPELINE_VERSION}:experiment",
            prompt_version=f"{PROMPT_VERSION}:experiment",
            catalog_version=CATALOG_VERSION,
            model_config={**processor.model_config, "experiment_arm": arm},
        )
        if not run_started:
            raise ValueError("experiment document run was not started")
        preprocessing = preprocess_source(source)
        registry.save_preprocessing_result(run_id, preprocessing)
        if arm == "dreamer_grounder":
            candidates = registry.get_latest_dream_candidates_for_processing_key(
                processing_key
            )
            if not candidates:
                candidates = processor._dream(  # noqa: SLF001 - isolated experiment
                    source, preprocessing.document, run_id, summaries
                )
                registry.save_dream_candidates(run_id, candidates)
            grounder = processor._ground(  # noqa: SLF001 - isolated experiment
                source,
                preprocessing.document,
                candidates,
                run_id,
                processing_key,
                summaries,
            )
        else:
            grounder = _direct_ground(
                processor=processor,
                source=source,
                document=preprocessing.document,
                run_id=run_id,
                summaries=summaries,
            )
        mentions_by_id: dict[str, EventMention] = {}
        for draft in grounder.drafts:
            mention = processor._materialize_mention(  # noqa: SLF001 - experiment endpoint
                source, preprocessing.document, draft.mention
            )
            mentions_by_id.setdefault(mention.mention_id, mention)
        result = SingleDocumentResult(
            run_id=run_id,
            message_id=source.message_id,
            processing_key=processing_key,
            status=ProcessingStatus.SUCCEEDED,
            mentions=list(mentions_by_id.values()),
            model_calls=summaries,
            judge_routing=JudgeRouting(invoked=False, reasons=[]),
            normalization_decisions=[],
            failures=[],
            started_at=started_at,
            finished_at=datetime.now(UTC),
        )
        registry.complete_document_run(result)
        return ArmDocumentResult(
            source_row_id=row.source_row_id,
            message_id=source.message_id,
            status="SUCCEEDED",
            run_id=run_id,
            dream_candidates=candidates,
            grounder=grounder,
            mentions=result.mentions,
            model_calls=summaries,
        )
    except Exception as exc:
        if run_started:
            registry.fail_document_run(run_id, error_code=type(exc).__name__)
        return ArmDocumentResult(
            source_row_id=row.source_row_id,
            message_id=source.message_id,
            status="FAILED",
            run_id=run_id,
            dream_candidates=candidates,
            grounder=None,
            mentions=[],
            model_calls=summaries,
            error_code=type(exc).__name__,
        )


def run_arm(
    *,
    arm: Literal["dreamer_grounder", "grounder_only"],
    snapshot_path: Path,
    manifest_path: Path,
    registry_path: Path,
    output_path: Path,
    workers: int,
    limit: int = 10,
    settings: CDECRSettings,
) -> ArmArtifact:
    _, corpus = _load_corpus(snapshot_path, manifest_path)
    if not 1 <= limit <= len(corpus):
        raise ValueError("arm limit must be within the reviewed corpus")
    corpus = corpus[:limit]
    prompt_hash = hashlib.sha256(_prompt("grounder.md").encode()).hexdigest()
    registry = SQLiteCDECRRegistry(registry_path)
    registry.initialize()
    for row, source in corpus:
        registry.save_source(source, fingerprint=row.document_fingerprint)
    processor = _build_processor(settings, registry)
    documents: list[ArmDocumentResult] = []
    if output_path.exists():
        previous = ArmArtifact.model_validate_json(output_path.read_text(encoding="utf-8"))
        if previous.arm != arm or previous.prompt_sha256 != prompt_hash:
            raise ValueError("existing arm checkpoint does not match this run")
        # Successful documents are resumable; failed provider calls must be retried once
        # credentials or account state have been repaired.
        documents = [item for item in previous.documents if item.status == "SUCCEEDED"]
    completed_ids = {item.message_id for item in documents}
    pending = [(row, source) for row, source in corpus if source.message_id not in completed_ids]

    def checkpoint() -> ArmArtifact:
        order = {source.message_id: index for index, (_, source) in enumerate(corpus)}
        artifact = ArmArtifact(
            arm=arm,
            model_m2=settings.model_m2,
            model_m3=settings.model_m3,
            prompt_sha256=prompt_hash,
            documents=sorted(documents, key=lambda item: order[item.message_id]),
        )
        _write_json(artifact, output_path)
        return artifact

    with ThreadPoolExecutor(max_workers=max(1, min(workers, 3))) as executor:
        futures = {
            executor.submit(
                _run_one_document,
                arm=arm,
                row=row,
                source=source,
                processor=processor,
                registry=registry,
                prompt_hash=prompt_hash,
            ): source.message_id
            for row, source in pending
        }
        for future in as_completed(futures):
            documents.append(future.result())
            checkpoint()
    return checkpoint()


def _combined_result(
    source: SourceMessage,
    dreamer: ArmDocumentResult,
    direct: ArmDocumentResult,
) -> tuple[SingleDocumentResult, Literal["X", "Y"]]:
    dreamer_label: Literal["X", "Y"] = (
        "X" if int(hashlib.sha256(source.message_id.encode()).hexdigest(), 16) % 2 == 0 else "Y"
    )
    direct_label: Literal["X", "Y"] = "Y" if dreamer_label == "X" else "X"
    mentions = [
        mention.model_copy(update={"mention_id": f"{dreamer_label}:{mention.mention_id}"})
        for mention in dreamer.mentions
    ] + [
        mention.model_copy(update={"mention_id": f"{direct_label}:{mention.mention_id}"})
        for mention in direct.mentions
    ]
    now = datetime.now(UTC)
    return (
        SingleDocumentResult(
            run_id=f"paired-review:{source.message_id}",
            message_id=source.message_id,
            processing_key=f"paired-review:{source.message_id}",
            status=ProcessingStatus.SUCCEEDED,
            mentions=mentions,
            model_calls=[],
            judge_routing=JudgeRouting(invoked=False, reasons=[]),
            normalization_decisions=[],
            failures=[],
            started_at=now,
            finished_at=now,
        ),
        dreamer_label,
    )


def run_review(
    *,
    snapshot_path: Path,
    manifest_path: Path,
    dreamer_path: Path,
    direct_path: Path,
    registry_path: Path,
    output_path: Path,
    workers: int,
    settings: CDECRSettings,
) -> PairedReviewArtifact:
    _, corpus = _load_corpus(snapshot_path, manifest_path)
    dreamer = ArmArtifact.model_validate_json(dreamer_path.read_text(encoding="utf-8"))
    direct = ArmArtifact.model_validate_json(direct_path.read_text(encoding="utf-8"))
    dreamer_by_id = {item.message_id: item for item in dreamer.documents}
    direct_by_id = {item.message_id: item for item in direct.documents}
    expected_ids = {source.message_id for _, source in corpus}
    if set(dreamer_by_id) != expected_ids or set(direct_by_id) != expected_ids:
        raise ValueError("paired review requires an outcome from both arms for every document")
    registry = SQLiteCDECRRegistry(registry_path)
    registry.initialize()
    api_key = settings.require_dashscope()
    client = DashScopeStructuredModelClient(
        tier=ModelTier.M4,
        api_key=api_key,
        base_url=settings.dashscope_base_url,
        model=settings.model_m4,
        timeout_seconds=settings.model_timeout_seconds,
        fallback_api_keys=settings.dashscope_fallback_api_keys(),
    )
    reviewed: list[PairedReviewDocument] = []
    if output_path.exists():
        previous = PairedReviewArtifact.model_validate_json(output_path.read_text(encoding="utf-8"))
        if previous.model != settings.model_m4:
            raise ValueError("existing review checkpoint uses a different model")
        reviewed = list(previous.documents)
    reviewed_ids = {item.message_id for item in reviewed}

    def review_one(source: SourceMessage) -> PairedReviewDocument:
        combined, dreamer_label = _combined_result(
            source, dreamer_by_id[source.message_id], direct_by_id[source.message_id]
        )
        request = _m4_review_request(source, combined)
        extracted_ids = {mention.mention_id for mention in combined.mentions}

        def validate_review(output: M4ReviewOutput) -> None:
            classified = [
                mention_id
                for mention in output.mentions
                for mention_id in mention.source_mention_ids
            ] + list(output.unsupported_extracted_mention_ids)
            if len(classified) != len(set(classified)) or set(classified) != extracted_ids:
                raise ValueError("paired review must classify every mention exactly once")
            for mention in output.mentions:
                for evidence in mention.evidence_texts:
                    if len(evidence) > 500 or not (
                        evidence in source.title or evidence in source.text
                    ):
                        raise ValueError("paired review evidence must be an exact substring")

        invalid_output: object | None = None
        validation_error: object | None = None
        try:
            first = client.complete(request)
        except ModelAdapterError as exc:
            _record_m4_call(
                registry=registry,
                model=settings.model_m4,
                request=request,
                status="FAILED",
                error=exc,
                repaired=False,
            )
            if exc.code != "invalid_json" or exc.raw_response_text is None:
                raise
            invalid_output = exc.raw_response_text
            validation_error = "invalid_json"
        else:
            _record_m4_call(
                registry=registry,
                model=settings.model_m4,
                request=request,
                status="SUCCEEDED",
                result=first,
                repaired=False,
            )
            try:
                review = M4ReviewOutput.model_validate(first.payload)
                validate_review(review)
            except (ValidationError, ValueError) as exc:
                invalid_output = first.payload
                validation_error = (
                    exc.errors(include_input=False, include_url=False)
                    if isinstance(exc, ValidationError)
                    else str(exc)
                )
            else:
                successful_direct_label: Literal["X", "Y"] = (
                    "Y" if dreamer_label == "X" else "X"
                )
                return PairedReviewDocument(
                    message_id=source.message_id,
                    dreamer_label=dreamer_label,
                    grounder_label=successful_direct_label,
                    review=review,
                )

        repair = StructuredModelRequest(
            system_prompt=(
                "Repair the paired M4 review into one valid JSON object. Classify every "
                "extracted mention exactly once and retain only exact document evidence."
            ),
            user_prompt=json.dumps(
                {
                    "original_request": request.user_prompt,
                    "invalid_output": invalid_output,
                    "validation_error": validation_error,
                },
                ensure_ascii=False,
                default=str,
            ),
            json_schema=M4ReviewOutput.model_json_schema(),
        )
        repaired_result = client.complete(repair)
        _record_m4_call(
            registry=registry,
            model=settings.model_m4,
            request=repair,
            status="SUCCEEDED",
            result=repaired_result,
            repaired=True,
        )
        review = M4ReviewOutput.model_validate(repaired_result.payload)
        validate_review(review)
        direct_label: Literal["X", "Y"] = "Y" if dreamer_label == "X" else "X"
        return PairedReviewDocument(
            message_id=source.message_id,
            dreamer_label=dreamer_label,
            grounder_label=direct_label,
            review=review,
        )

    pending = [source for _, source in corpus if source.message_id not in reviewed_ids]
    order = {source.message_id: index for index, (_, source) in enumerate(corpus)}

    def checkpoint() -> PairedReviewArtifact:
        artifact = PairedReviewArtifact(
            model=settings.model_m4,
            documents=sorted(reviewed, key=lambda item: order[item.message_id]),
        )
        _write_json(artifact, output_path)
        return artifact

    with ThreadPoolExecutor(max_workers=max(1, min(workers, 3))) as executor:
        futures = {executor.submit(review_one, source): source.message_id for source in pending}
        for future in as_completed(futures):
            reviewed.append(future.result())
            checkpoint()
    return checkpoint()


def _f1(precision: float, recall: float) -> float:
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def _arm_metrics(
    *,
    arm: Literal["dreamer", "direct"],
    artifact: ArmArtifact,
    reviews: PairedReviewArtifact,
) -> AggregateMetrics:
    results = {item.message_id: item for item in artifact.documents}
    per_document: list[ArmDocumentMetrics] = []
    for item in reviews.documents:
        label = item.dreamer_label if arm == "dreamer" else item.grounder_label
        result = results[item.message_id]
        extracted_ids = {f"{label}:{mention.mention_id}" for mention in result.mentions}
        supported_ids = {
            mention_id
            for gold in item.review.mentions
            for mention_id in gold.source_mention_ids
            if mention_id.startswith(f"{label}:")
        }
        recalled = sum(
            any(value.startswith(f"{label}:") for value in gold.source_mention_ids)
            for gold in item.review.mentions
        )
        precision = len(supported_ids) / len(extracted_ids) if extracted_ids else 1.0
        recall = recalled / len(item.review.mentions) if item.review.mentions else 1.0
        per_document.append(
            ArmDocumentMetrics(
                message_id=item.message_id,
                gold_count=len(item.review.mentions),
                extracted_count=len(extracted_ids),
                supported_count=len(supported_ids),
                recalled_gold_count=recalled,
                precision=precision,
                recall=recall,
                f1=_f1(precision, recall),
            )
        )
    extracted = sum(item.extracted_count for item in per_document)
    supported = sum(item.supported_count for item in per_document)
    gold = sum(item.gold_count for item in per_document)
    recalled = sum(item.recalled_gold_count for item in per_document)
    precision = supported / extracted if extracted else 1.0
    recall = recalled / gold if gold else 1.0
    return AggregateMetrics(
        document_count=len(per_document),
        completed_count=sum(item.status == "SUCCEEDED" for item in artifact.documents),
        failed_count=sum(item.status == "FAILED" for item in artifact.documents),
        extracted_count=extracted,
        supported_count=supported,
        gold_count=gold,
        recalled_gold_count=recalled,
        micro_precision=precision,
        micro_recall=recall,
        micro_f1=_f1(precision, recall),
        macro_f1=(sum(item.f1 for item in per_document) / len(per_document)),
        per_document=per_document,
    )


def compare(
    dreamer: ArmArtifact, direct: ArmArtifact, reviews: PairedReviewArtifact
) -> ComparisonReport:
    dreamer_metrics = _arm_metrics(arm="dreamer", artifact=dreamer, reviews=reviews)
    direct_metrics = _arm_metrics(arm="direct", artifact=direct, reviews=reviews)
    direct_by_id = {item.message_id: item for item in direct_metrics.per_document}
    deltas = [
        item.f1 - direct_by_id[item.message_id].f1
        for item in dreamer_metrics.per_document
    ]
    rng = random.Random(20260721)
    bootstrap = sorted(
        sum(rng.choice(deltas) for _ in deltas) / len(deltas) for _ in range(10_000)
    )
    low = bootstrap[249]
    high = bootstrap[9749]
    delta = dreamer_metrics.macro_f1 - direct_metrics.macro_f1
    direct_status = {item.message_id: item.status for item in direct.documents}
    completed_deltas = [
        item.f1 - direct_by_id[item.message_id].f1
        for item in dreamer_metrics.per_document
        if direct_status[item.message_id] == "SUCCEEDED"
    ]
    completed_rng = random.Random(20260721)
    completed_bootstrap = sorted(
        sum(completed_rng.choice(completed_deltas) for _ in completed_deltas)
        / len(completed_deltas)
        for _ in range(10_000)
    )
    completed_low = completed_bootstrap[249]
    completed_high = completed_bootstrap[9749]
    completed_delta = sum(completed_deltas) / len(completed_deltas)
    return ComparisonReport(
        dreamer_grounder=dreamer_metrics,
        grounder_only=direct_metrics,
        macro_f1_delta_dreamer_minus_direct=delta,
        bootstrap_95_ci=(low, high),
        dreamer_wins=sum(value > 1e-12 for value in deltas),
        ties=sum(abs(value) <= 1e-12 for value in deltas),
        direct_wins=sum(value < -1e-12 for value in deltas),
        obvious_difference=abs(delta) >= 0.05 and (low > 0 or high < 0),
        obvious_difference_rule=(
            "absolute macro-F1 delta >= 0.05 and paired bootstrap 95% CI excludes 0"
        ),
        completed_pair_count=len(completed_deltas),
        completed_pair_macro_f1_delta=completed_delta,
        completed_pair_bootstrap_95_ci=(completed_low, completed_high),
        obvious_difference_without_failed_documents=(
            abs(completed_delta) >= 0.05
            and (completed_low > 0 or completed_high < 0)
        ),
    )


def _write_markdown(report: ComparisonReport, path: Path) -> None:
    rows = []
    direct_by_id = {
        item.message_id: item for item in report.grounder_only.per_document
    }
    for item in report.dreamer_grounder.per_document:
        other = direct_by_id[item.message_id]
        rows.append(
            f"| `{item.message_id.rsplit(':', 1)[-1]}` | {item.extracted_count} | "
            f"{item.precision:.3f} | {item.recall:.3f} | {item.f1:.3f} | "
            f"{other.extracted_count} | {other.precision:.3f} | {other.recall:.3f} | "
            f"{other.f1:.3f} |"
        )
    dg = report.dreamer_grounder
    direct = report.grounder_only
    dg_completion = f"{dg.completed_count}/{dg.document_count}"
    direct_completion = f"{direct.completed_count}/{direct.document_count}"
    text = f"""# Dreamer -> Grounder vs. Grounder-only

| Metric | Dreamer -> Grounder | Grounder-only |
|---|---:|---:|
| Completed documents | {dg_completion} | {direct_completion} |
| Extracted mentions | {dg.extracted_count} | {direct.extracted_count} |
| Micro precision | {dg.micro_precision:.3f} | {direct.micro_precision:.3f} |
| Micro recall | {dg.micro_recall:.3f} | {direct.micro_recall:.3f} |
| Micro F1 | {dg.micro_f1:.3f} | {direct.micro_f1:.3f} |
| Macro F1 | {dg.macro_f1:.3f} | {direct.macro_f1:.3f} |

Macro-F1 delta (Dreamer minus direct): `{report.macro_f1_delta_dreamer_minus_direct:.3f}`.
Paired bootstrap 95% CI: `[{report.bootstrap_95_ci[0]:.3f}, {report.bootstrap_95_ci[1]:.3f}]`.
Obvious difference under the predeclared rule: `{str(report.obvious_difference).lower()}`.
Sensitivity excluding failed documents: delta 
`{report.completed_pair_macro_f1_delta:.3f}`, 95% CI 
`[{report.completed_pair_bootstrap_95_ci[0]:.3f}, 
{report.completed_pair_bootstrap_95_ci[1]:.3f}]`, obvious difference 
`{str(report.obvious_difference_without_failed_documents).lower()}`.

| Row ID | DG n | DG P | DG R | DG F1 | G n | G P | G R | G F1 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
{chr(10).join(rows)}
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m cdecr.dreamer_grounder_experiment")
    commands = parser.add_subparsers(dest="command", required=True)
    arm = commands.add_parser("arm")
    arm.add_argument("--arm", choices=("dreamer_grounder", "grounder_only"), required=True)
    arm.add_argument("--snapshot", type=Path, required=True)
    arm.add_argument("--manifest", type=Path, required=True)
    arm.add_argument("--registry", type=Path, required=True)
    arm.add_argument("--output", type=Path, required=True)
    arm.add_argument("--workers", type=int, default=3)
    arm.add_argument("--limit", type=int, default=10)
    review = commands.add_parser("review")
    review.add_argument("--snapshot", type=Path, required=True)
    review.add_argument("--manifest", type=Path, required=True)
    review.add_argument("--dreamer-output", type=Path, required=True)
    review.add_argument("--direct-output", type=Path, required=True)
    review.add_argument("--registry", type=Path, required=True)
    review.add_argument("--output", type=Path, required=True)
    review.add_argument("--workers", type=int, default=3)
    comparison = commands.add_parser("compare")
    comparison.add_argument("--dreamer-output", type=Path, required=True)
    comparison.add_argument("--direct-output", type=Path, required=True)
    comparison.add_argument("--review", type=Path, required=True)
    comparison.add_argument("--output-json", type=Path, required=True)
    comparison.add_argument("--output-markdown", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "arm":
        arm_artifact = run_arm(
            arm=args.arm,
            snapshot_path=args.snapshot,
            manifest_path=args.manifest,
            registry_path=args.registry,
            output_path=args.output,
            workers=args.workers,
            limit=args.limit,
            settings=CDECRSettings(),
        )
        print(
            json.dumps(
                {
                    "ok": True,
                    "arm": arm_artifact.arm,
                    "documents": len(arm_artifact.documents),
                }
            )
        )
        return 0
    if args.command == "review":
        review_artifact = run_review(
            snapshot_path=args.snapshot,
            manifest_path=args.manifest,
            dreamer_path=args.dreamer_output,
            direct_path=args.direct_output,
            registry_path=args.registry,
            output_path=args.output,
            workers=args.workers,
            settings=CDECRSettings(),
        )
        print(
            json.dumps(
                {"ok": True, "reviewed_documents": len(review_artifact.documents)}
            )
        )
        return 0
    dreamer = ArmArtifact.model_validate_json(args.dreamer_output.read_text(encoding="utf-8"))
    direct = ArmArtifact.model_validate_json(args.direct_output.read_text(encoding="utf-8"))
    reviews = PairedReviewArtifact.model_validate_json(args.review.read_text(encoding="utf-8"))
    report = compare(dreamer, direct, reviews)
    _write_json(report, args.output_json)
    _write_markdown(report, args.output_markdown)
    print(json.dumps({"ok": True, "obvious_difference": report.obvious_difference}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
