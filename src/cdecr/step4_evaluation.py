"""Repeatable real-corpus acceptance reporting for CDECR step four."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections import Counter, defaultdict
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from importlib import import_module
from itertools import combinations
from pathlib import Path
from typing import BinaryIO, Literal

from pydantic import Field, ValidationError

from cdecr.contracts import (
    AnalystActionProjection,
    EventFamily,
    EventMention,
    FinancialMetricProjection,
    NonEmptyString,
    PackageFamily,
    SourceMessage,
    StrictModel,
)
from cdecr.coreference_rules import hard_cannot_link, singleton_atomic_event
from cdecr.cross_document_contracts import CrossDocumentResult, CrossDocumentStatus
from cdecr.evaluation import GoldDocument, GoldMention
from cdecr.models import ModelAdapterError
from cdecr.ports import StructuredModelClient, StructuredModelRequest, StructuredModelResult
from cdecr.registry import SQLiteCDECRRegistry
from cdecr.single_document_contracts import (
    ModelCallSummary,
    ProcessingStatus,
    SingleDocumentResult,
)

M4_REVIEW_VERSION: Literal["cdecr-m4-gold-review-v2"] = "cdecr-m4-gold-review-v2"


class M4ReviewMention(StrictModel):
    event_family: EventFamily
    predicate_keywords: list[NonEmptyString] = Field(min_length=1, max_length=3)
    evidence_texts: list[NonEmptyString] = Field(min_length=1, max_length=3)
    source_mention_ids: list[NonEmptyString]


class M4ReviewOutput(StrictModel):
    mentions: list[M4ReviewMention]
    unsupported_extracted_mention_ids: list[NonEmptyString]
    review_notes: list[NonEmptyString]


class M4ReviewRecord(StrictModel):
    message_id: NonEmptyString
    mentions: list[GoldMention]
    unsupported_extracted_mention_ids: list[NonEmptyString]
    review_notes: list[NonEmptyString]


class M4ReviewArtifact(StrictModel):
    review_version: Literal["cdecr-m4-gold-review-v2"] = M4_REVIEW_VERSION
    model: NonEmptyString
    documents: list[GoldDocument]
    reviews: list[M4ReviewRecord]
    m4_review_status: Literal["COMPLETED"] = "COMPLETED"
    human_review_status: Literal["PENDING"] = "PENDING"


class Step4CorpusRow(StrictModel):
    source_row_id: str
    document_fingerprint: str
    length_bucket: Literal["short", "medium", "long"]
    text_chars: int = Field(ge=200)
    source_name: str
    expected_event_families: list[EventFamily]
    review_status: Literal["PENDING", "REVIEWED"]


class Step4DocumentOutcome(StrictModel):
    source_row_id: str
    message_id: str
    document_fingerprint: str
    length_bucket: Literal["short", "medium", "long"]
    source_name: str
    document_status: str
    event_status: str | None = None
    mention_count: int = Field(ge=0)
    atomic_assignment_count: int = Field(ge=0)
    package_assignment_count: int = Field(ge=0)
    model_call_count: int = Field(ge=0)
    failure_stage: str | None = None
    error_code: str | None = None


class Step4BoundaryOutcome(StrictModel):
    evaluated_cases: int = Field(ge=0)
    violations: int = Field(ge=0)
    violation_ids: list[str]


class Step4CallBudget(StrictModel):
    call_count: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    latency_ms: int = Field(ge=0)
    calls_by_stage: dict[str, int]
    calls_by_tier: dict[str, int]
    recall_or_hard_link_llm_violations: int = Field(ge=0)


class Step4Idempotency(StrictModel):
    rerun_model_call_delta: int = Field(ge=0)
    rerun_mention_delta: int = Field(ge=0)
    rerun_atomic_delta: int = Field(ge=0)
    rerun_package_delta: int = Field(ge=0)
    all_successful_documents_reused: bool
    all_successful_events_reused: bool


class Step4AcceptanceReport(StrictModel):
    report_version: Literal["cdecr-step4-v1"] = "cdecr-step4-v1"
    corpus_manifest_version: str
    selected_document_count: int = Field(ge=0)
    distinct_source_count: int = Field(ge=0)
    completed_document_count: int = Field(ge=0)
    completed_event_count: int = Field(ge=0)
    failed_document_count: int = Field(ge=0)
    valid_mention_schema_rate: float = Field(ge=0, le=1)
    valid_evidence_span_rate: float = Field(ge=0, le=1)
    atomic_event_count: int = Field(ge=0)
    package_count: int = Field(ge=0)
    external_relation_count: int = Field(ge=0)
    documents: list[Step4DocumentOutcome]
    boundaries: dict[str, Step4BoundaryOutcome]
    call_budget: Step4CallBudget
    idempotency: Step4Idempotency
    m4_review_status: Literal["PENDING", "COMPLETED"] = "PENDING"
    projection_evaluation_status: Literal["DEFERRED_BY_USER"] = "DEFERRED_BY_USER"
    acceptance_passed: bool


@contextmanager
def step4_evaluation_lock(registry_path: Path) -> Iterator[None]:
    """Hold a process-level advisory lock next to the evaluation Registry."""

    lock_path = registry_path.with_suffix(registry_path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle: BinaryIO = lock_path.open("a+b")
    if handle.seek(0, os.SEEK_END) == 0:
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    acquired = False
    try:
        if os.name == "nt":
            import msvcrt

            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                acquired = True
            except OSError as exc:
                raise RuntimeError("another CDECR step-four evaluation is running") from exc
        else:  # pragma: no cover - exercised on POSIX CI only
            fcntl = import_module("fcntl")

            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError as exc:
                raise RuntimeError("another CDECR step-four evaluation is running") from exc
        yield
    finally:
        try:
            handle.seek(0)
            if acquired and os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            elif acquired:  # pragma: no cover - exercised on POSIX CI only
                fcntl = import_module("fcntl")

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def load_step4_corpus(
    snapshot_path: Path, manifest_path: Path, *, limit: int = 24
) -> tuple[str, list[tuple[Step4CorpusRow, SourceMessage]]]:
    """Load the ignored full text while validating every tracked safe-manifest identity."""

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_version = str(manifest["manifest_version"])
    rows = [
        Step4CorpusRow.model_validate(item)
        for item in manifest["rows"][:limit]
    ]
    snapshot_by_id: dict[str, dict[str, object]] = {}
    with snapshot_path.open(encoding="utf-8") as handle:
        for line in handle:
            item = json.loads(line)
            snapshot_by_id[str(item["source_row_id"])] = item
    selected: list[tuple[Step4CorpusRow, SourceMessage]] = []
    for row in rows:
        snapshot = snapshot_by_id.get(row.source_row_id)
        if snapshot is None:
            raise ValueError(f"snapshot is missing tracked row {row.source_row_id}")
        if snapshot.get("document_fingerprint") != row.document_fingerprint:
            raise ValueError(f"fingerprint mismatch for tracked row {row.source_row_id}")
        source = SourceMessage.model_validate(snapshot["message"])
        if len(source.text) != row.text_chars or source.source_name != row.source_name:
            raise ValueError(f"safe manifest metadata mismatch for row {row.source_row_id}")
        selected.append((row, source))
    return manifest_version, selected


def _stable_hash(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _m4_review_request(
    source: SourceMessage, result: SingleDocumentResult
) -> StructuredModelRequest:
    extracted_mentions = [
        {
            "mention_id": mention.mention_id,
            "event_family": mention.event_family.value,
            "predicate": mention.predicate.model_dump(mode="json"),
            "canonical_proposition": mention.canonical_proposition,
            "assertion_state": mention.assertion_state.value,
            "evidence_texts": [span.text for span in mention.evidence_spans],
        }
        for mention in result.mentions
    ]
    return StructuredModelRequest(
        system_prompt=(
            "You are the offline M4 reviewer for one news document. Build an exhaustive, "
            "atomic event gold record from the document and independently check every extracted "
            "mention. Use only exact document evidence. The current contract intentionally "
            "excludes "
            "schema_projection and all financial metric, guidance, and analyst-action projection "
            "schemas. Do not emit those fields. Each extracted mention_id must appear exactly "
            "once, "
            "either in one gold mention's source_mention_ids or in "
            "unsupported_extracted_mention_ids. A genuinely missing event may have an empty "
            "source_mention_ids list. Evidence text must be an exact case-sensitive substring of "
            "the "
            "title or body and no longer than 500 characters."
        ),
        user_prompt=json.dumps(
            {
                "document": {
                    "message_id": source.message_id,
                    "title": source.title,
                    "text": source.text,
                    "published_at": source.published_at.isoformat(),
                    "source_name": source.source_name,
                },
                "extracted_mentions": extracted_mentions,
            },
            ensure_ascii=False,
        ),
        json_schema=M4ReviewOutput.model_json_schema(),
    )


def _validate_m4_review(
    output: M4ReviewOutput,
    *,
    source: SourceMessage,
    result: SingleDocumentResult,
) -> M4ReviewRecord:
    extracted_ids = {mention.mention_id for mention in result.mentions}
    classified_ids = [
        mention_id
        for mention in output.mentions
        for mention_id in mention.source_mention_ids
    ] + list(output.unsupported_extracted_mention_ids)
    if len(classified_ids) != len(set(classified_ids)):
        raise ValueError("M4 review classifies an extracted mention more than once")
    if set(classified_ids) != extracted_ids:
        raise ValueError("M4 review must classify every extracted mention exactly once")
    searchable = (source.title, source.text)
    gold_mentions: list[GoldMention] = []
    for mention in output.mentions:
        for evidence in mention.evidence_texts:
            if len(evidence) > 500 or not any(evidence in field for field in searchable):
                raise ValueError("M4 review evidence must be an exact bounded document substring")
        gold_mentions.append(
            GoldMention(
                event_family=mention.event_family,
                predicate_keywords=mention.predicate_keywords,
                evidence_texts=mention.evidence_texts,
                projection_core=None,
            )
        )
    return M4ReviewRecord(
        message_id=source.message_id,
        mentions=gold_mentions,
        unsupported_extracted_mention_ids=output.unsupported_extracted_mention_ids,
        review_notes=output.review_notes,
    )


def _reconcile_m4_review(
    output: M4ReviewOutput,
    *,
    source: SourceMessage,
    result: SingleDocumentResult,
) -> M4ReviewRecord:
    """Conservatively reconcile a schema-valid M4 review after its repair was still invalid."""

    extracted_ids = {mention.mention_id for mention in result.mentions}
    classified: set[str] = set()
    gold_mentions: list[GoldMention] = []
    dropped_gold = 0
    dropped_ids = 0
    searchable = (source.title, source.text)
    for mention in output.mentions:
        evidence = [
            value
            for value in mention.evidence_texts
            if len(value) <= 500 and any(value in field for field in searchable)
        ]
        if not evidence:
            dropped_gold += 1
            continue
        source_ids = []
        for mention_id in mention.source_mention_ids:
            if mention_id not in extracted_ids or mention_id in classified:
                dropped_ids += 1
                continue
            classified.add(mention_id)
            source_ids.append(mention_id)
        gold_mentions.append(
            GoldMention(
                event_family=mention.event_family,
                predicate_keywords=mention.predicate_keywords,
                evidence_texts=evidence,
                projection_core=None,
            )
        )
    unsupported: list[str] = []
    for mention_id in output.unsupported_extracted_mention_ids:
        if mention_id in extracted_ids and mention_id not in classified:
            classified.add(mention_id)
            unsupported.append(mention_id)
        else:
            dropped_ids += 1
    missing = sorted(extracted_ids - classified)
    unsupported.extend(missing)
    note = (
        "AUTOMATIC_CONSERVATIVE_RECONCILIATION: "
        f"dropped_gold={dropped_gold}; dropped_or_duplicate_ids={dropped_ids}; "
        f"missing_marked_unsupported={len(missing)}"
    )
    return M4ReviewRecord(
        message_id=source.message_id,
        mentions=gold_mentions,
        unsupported_extracted_mention_ids=unsupported,
        review_notes=[*output.review_notes, note],
    )


def _record_m4_call(
    *,
    registry: SQLiteCDECRRegistry,
    model: str,
    request: StructuredModelRequest,
    status: Literal["SUCCEEDED", "FAILED"],
    result: StructuredModelResult | None = None,
    error: ModelAdapterError | None = None,
    repaired: bool,
) -> None:
    registry.record_model_call(
        model_call_id=str(uuid.uuid4()),
        run_id=None,
        tier="m4",
        model=model,
        status=status,
        input_tokens=(
            result.input_tokens
            if result is not None
            else getattr(error, "input_tokens", None)
        ),
        output_tokens=(
            result.output_tokens if result is not None else getattr(error, "output_tokens", None)
        ),
        latency_ms=result.latency_ms if result is not None else getattr(error, "latency_ms", 0),
        error_code=getattr(error, "code", None),
        metadata={"offline_review": True},
        stage="m4_gold_review_repair" if repaired else "m4_gold_review",
        prompt_version=M4_REVIEW_VERSION,
        schema_hash=_stable_hash(request.json_schema),
        input_hash=_stable_hash(
            {"system": request.system_prompt, "user": request.user_prompt}
        ),
    )


def _review_one_document(
    *,
    source: SourceMessage,
    result: SingleDocumentResult,
    client: StructuredModelClient,
    registry: SQLiteCDECRRegistry,
    model: str,
) -> M4ReviewRecord:
    request = _m4_review_request(source, result)
    invalid_output: object | None = None
    validation_error: object | None = None
    try:
        first = client.complete(request)
    except ModelAdapterError as exc:
        _record_m4_call(
            registry=registry,
            model=model,
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
            model=model,
            request=request,
            status="SUCCEEDED",
            result=first,
            repaired=False,
        )
        try:
            parsed = M4ReviewOutput.model_validate(first.payload)
            return _validate_m4_review(parsed, source=source, result=result)
        except (ValidationError, ValueError) as exc:
            invalid_output = first.payload
            validation_error = (
                exc.errors(include_input=False, include_url=False)
                if isinstance(exc, ValidationError)
                else str(exc)
            )

    repair = StructuredModelRequest(
        system_prompt=(
            "Repair the prior M4 review into one valid JSON object. Preserve supported content, "
            "classify every extracted mention exactly once, use only exact document evidence, and "
            "do not add schema_projection or any financial projection schema."
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
    try:
        repaired_result = client.complete(repair)
    except ModelAdapterError as exc:
        _record_m4_call(
            registry=registry,
            model=model,
            request=repair,
            status="FAILED",
            error=exc,
            repaired=True,
        )
        raise
    _record_m4_call(
        registry=registry,
        model=model,
        request=repair,
        status="SUCCEEDED",
        result=repaired_result,
        repaired=True,
    )
    try:
        repaired_output = M4ReviewOutput.model_validate(repaired_result.payload)
    except ValidationError as exc:
        raise ValueError("M4 review remained schema-invalid after one repair") from exc
    try:
        return _validate_m4_review(repaired_output, source=source, result=result)
    except ValueError:
        return _reconcile_m4_review(repaired_output, source=source, result=result)


def run_m4_reviews(
    *,
    corpus: Sequence[tuple[Step4CorpusRow, SourceMessage]],
    registry: SQLiteCDECRRegistry,
    client: StructuredModelClient,
    model: str,
    output_path: Path,
) -> M4ReviewArtifact:
    """Build a resumable M4-assisted gold artifact without claiming human sign-off."""

    checkpoint_path = output_path.with_suffix(output_path.suffix + ".checkpoint.json")
    records: list[M4ReviewRecord] = []
    if checkpoint_path.exists():
        checkpoint = M4ReviewArtifact.model_validate_json(
            checkpoint_path.read_text(encoding="utf-8")
        )
        if checkpoint.model != model:
            raise ValueError("M4 checkpoint model does not match current configuration")
        records = list(checkpoint.reviews)
    reviewed_ids = {record.message_id for record in records}
    source_ids = {source.message_id for _, source in corpus}
    if not reviewed_ids <= source_ids:
        raise ValueError("M4 checkpoint contains messages outside the selected corpus")

    for _, source in corpus:
        if source.message_id in reviewed_ids:
            continue
        result = registry.get_latest_completed_document_result_for_message(source.message_id)
        if result is None or result.status is not ProcessingStatus.SUCCEEDED:
            raise ValueError("M4 review requires a successful persisted document result")
        records.append(
            _review_one_document(
                source=source,
                result=result,
                client=client,
                registry=registry,
                model=model,
            )
        )
        artifact = M4ReviewArtifact(
            model=model,
            documents=[
                GoldDocument(message_id=record.message_id, mentions=record.mentions)
                for record in records
            ],
            reviews=records,
        )
        write_step4_json(artifact.model_dump(mode="json"), checkpoint_path)

    artifact = M4ReviewArtifact(
        model=model,
        documents=[
            GoldDocument(message_id=record.message_id, mentions=record.mentions)
            for record in records
        ],
        reviews=records,
    )
    write_step4_json(artifact.model_dump(mode="json"), output_path)
    return artifact


def write_step4_json(payload: object, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    temporary.replace(path)


def _budget(calls: Sequence[ModelCallSummary]) -> Step4CallBudget:
    by_stage = Counter(item.stage for item in calls)
    by_tier = Counter(item.tier for item in calls)
    llm_recall_violations = sum(
        item.tier != "m1"
        and ("recall" in item.stage.casefold() or "hard_cannot_link" in item.stage.casefold())
        for item in calls
    )
    return Step4CallBudget(
        call_count=len(calls),
        input_tokens=sum(item.input_tokens or 0 for item in calls),
        output_tokens=sum(item.output_tokens or 0 for item in calls),
        latency_ms=sum(item.latency_ms for item in calls),
        calls_by_stage=dict(sorted(by_stage.items())),
        calls_by_tier=dict(sorted(by_tier.items())),
        recall_or_hard_link_llm_violations=llm_recall_violations,
    )


def _result_packages(
    registry: SQLiteCDECRRegistry, mention: EventMention
) -> tuple[str | None, set[str]]:
    event = registry.get_atomic_event_for_mention(mention.mention_id)
    if event is None:
        return None, set()
    return event.event_id, {
        package.package_id for package in registry.list_packages_for_event(event.event_id)
    }


def _boundary_report(
    registry: SQLiteCDECRRegistry, mentions: Sequence[EventMention]
) -> dict[str, Step4BoundaryOutcome]:
    names = (
        "hard_cannot_link_false_merge",
        "different_metric_same_earnings_package",
        "different_period_atomic_or_package_merge",
        "same_analyst_target_missed_merge",
        "reaction_in_earnings_package",
    )
    case_counts: Counter[str] = Counter()
    violation_ids: defaultdict[str, list[str]] = defaultdict(list)

    def record(name: str, case_id: str, violation: bool) -> None:
        case_counts[name] += 1
        if violation:
            violation_ids[name].append(case_id)

    located = {
        mention.mention_id: _result_packages(registry, mention) for mention in mentions
    }
    for left, right in combinations(mentions, 2):
        if left.message_id == right.message_id:
            continue
        left_event, left_packages = located[left.mention_id]
        right_event, right_packages = located[right.mention_id]
        case_id = f"{left.mention_id}|{right.mention_id}"
        conflicts = hard_cannot_link(
            left, singleton_atomic_event(right), representative_mentions=[right]
        )
        if conflicts:
            record(
                "hard_cannot_link_false_merge",
                case_id,
                left_event is not None and left_event == right_event,
            )

        left_projection = left.schema_projection
        right_projection = right.schema_projection
        if isinstance(left_projection, FinancialMetricProjection) and isinstance(
            right_projection, FinancialMetricProjection
        ):
            left_financial = left_projection.fields
            right_financial = right_projection.fields
            if (
                left_financial.issuer_id == right_financial.issuer_id
                and left_financial.period_id == right_financial.period_id
                and left_financial.metric_id != right_financial.metric_id
            ):
                record(
                    "different_metric_same_earnings_package",
                    case_id,
                    left_event == right_event or not (left_packages & right_packages),
                )
            if (
                left_financial.issuer_id == right_financial.issuer_id
                and left_financial.period_id != right_financial.period_id
            ):
                record(
                    "different_period_atomic_or_package_merge",
                    case_id,
                    left_event == right_event or bool(left_packages & right_packages),
                )
        if isinstance(left_projection, AnalystActionProjection) and isinstance(
            right_projection, AnalystActionProjection
        ):
            left_analyst = left_projection.fields
            right_analyst = right_projection.fields
            same_target = (
                left_analyst.institution_id == right_analyst.institution_id
                and left_analyst.company_id == right_analyst.company_id
                and left_analyst.action == right_analyst.action
                and left_analyst.target_to is not None
                and left_analyst.target_to == right_analyst.target_to
                and left_analyst.currency == right_analyst.currency
                and left_analyst.report_date == right_analyst.report_date
            )
            if same_target:
                record(
                    "same_analyst_target_missed_merge",
                    case_id,
                    left_event != right_event,
                )

    for mention in mentions:
        if mention.event_family not in {
            EventFamily.MARKET_MOVEMENT,
            EventFamily.ANALYST_ACTION,
        }:
            continue
        _, package_ids = located[mention.mention_id]
        earnings_memberships = [
            package_id
            for package_id in package_ids
            if (
                package := registry.get_current_package(package_id)
            ) is not None
            and package.package_family is PackageFamily.EARNINGS_DISCLOSURE
        ]
        record(
            "reaction_in_earnings_package",
            mention.mention_id,
            bool(earnings_memberships),
        )

    return {
        name: Step4BoundaryOutcome(
            evaluated_cases=case_counts[name],
            violations=len(violation_ids[name]),
            violation_ids=violation_ids[name],
        )
        for name in names
    }


def build_step4_report(
    *,
    manifest_version: str,
    corpus: Sequence[tuple[Step4CorpusRow, SourceMessage]],
    document_results: Sequence[SingleDocumentResult],
    event_results: Sequence[CrossDocumentResult | None],
    registry: SQLiteCDECRRegistry,
    idempotency: Step4Idempotency,
) -> Step4AcceptanceReport:
    document_by_id = {item.message_id: item for item in document_results}
    event_by_id = {item.message_id: item for item in event_results if item is not None}
    outcomes: list[Step4DocumentOutcome] = []
    mentions: list[EventMention] = []
    valid_mentions = 0
    valid_evidence = 0
    for row, source in corpus:
        document = document_by_id[source.message_id]
        event = event_by_id.get(source.message_id)
        for mention in document.mentions:
            EventMention.model_validate(mention.model_dump(mode="json"))
            valid_mentions += 1
            mention.validate_evidence(source)
            valid_evidence += 1
            mentions.append(mention)
        outcomes.append(
            Step4DocumentOutcome(
                source_row_id=row.source_row_id,
                message_id=source.message_id,
                document_fingerprint=row.document_fingerprint,
                length_bucket=row.length_bucket,
                source_name=row.source_name,
                document_status=document.status.value,
                event_status=event.status.value if event is not None else None,
                mention_count=len(document.mentions),
                atomic_assignment_count=len(event.atomic_assignments) if event else 0,
                package_assignment_count=len(event.package_assignments) if event else 0,
                model_call_count=(
                    len(document.model_calls) + (len(event.model_calls) if event else 0)
                ),
                failure_stage=(
                    document.failures[0].stage
                    if document.failures
                    else (event.failure_stage if event else None)
                ),
                error_code=(
                    document.failures[0].error_code
                    if document.failures
                    else (event.error_code if event else None)
                ),
            )
        )
    boundaries = _boundary_report(registry, mentions)
    mention_count = len(mentions)
    completed_documents = sum(
        item.status is ProcessingStatus.SUCCEEDED for item in document_results
    )
    completed_events = sum(
        item is not None and item.status is CrossDocumentStatus.SUCCEEDED
        for item in event_results
    )
    boundary_violations = sum(item.violations for item in boundaries.values())
    budget = _budget(registry.list_model_call_summaries())
    m4_review_status: Literal["PENDING", "COMPLETED"] = (
        "COMPLETED"
        if corpus and all(row.review_status == "REVIEWED" for row, _ in corpus)
        else "PENDING"
    )
    acceptance = (
        len(corpus) == 24
        and completed_documents == 24
        and completed_events == 24
        and valid_mentions == mention_count
        and valid_evidence == mention_count
        and boundary_violations == 0
        and budget.recall_or_hard_link_llm_violations == 0
        and idempotency.rerun_model_call_delta == 0
        and idempotency.rerun_mention_delta == 0
        and idempotency.rerun_atomic_delta == 0
        and idempotency.rerun_package_delta == 0
        and idempotency.all_successful_documents_reused
        and idempotency.all_successful_events_reused
        and m4_review_status == "COMPLETED"
    )
    return Step4AcceptanceReport(
        corpus_manifest_version=manifest_version,
        selected_document_count=len(corpus),
        distinct_source_count=len({source.source_name for _, source in corpus}),
        completed_document_count=completed_documents,
        completed_event_count=completed_events,
        failed_document_count=len(corpus) - completed_documents,
        valid_mention_schema_rate=valid_mentions / mention_count if mention_count else 1.0,
        valid_evidence_span_rate=valid_evidence / mention_count if mention_count else 1.0,
        atomic_event_count=len(registry.list_current_atomic_events()),
        package_count=len(registry.list_current_packages()),
        external_relation_count=len(registry.list_package_external_relations()),
        documents=outcomes,
        boundaries=boundaries,
        call_budget=budget,
        idempotency=idempotency,
        m4_review_status=m4_review_status,
        acceptance_passed=acceptance,
    )


def write_step4_report(report: Step4AcceptanceReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    temporary.replace(path)
