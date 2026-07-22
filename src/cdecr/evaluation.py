"""Deterministic selection and safe manifests for the MU step-two evaluation set."""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Sequence
from typing import Any, Literal

from pydantic import Field, JsonValue

from cdecr.contracts import EventFamily, EventMention, StrictModel
from cdecr.single_document_contracts import SingleDocumentResult


class EvaluationManifestRow(StrictModel):
    source_row_id: str
    document_fingerprint: str
    length_bucket: Literal["short", "medium", "long"]
    text_chars: int = Field(ge=200)
    source_name: str
    expected_event_families: list[EventFamily]
    review_status: Literal["PENDING", "REVIEWED"]


class GoldMention(StrictModel):
    event_family: EventFamily
    predicate_keywords: list[str] = Field(min_length=1)
    evidence_texts: list[str] = Field(min_length=1)
    projection_core: dict[str, JsonValue] | None = None


class GoldDocument(StrictModel):
    message_id: str
    mentions: list[GoldMention]


class EvaluationMetrics(StrictModel):
    document_count: int = Field(ge=0)
    completed_count: int = Field(ge=0)
    expected_mention_count: int = Field(ge=0)
    extracted_mention_count: int = Field(ge=0)
    matched_mention_count: int = Field(ge=0)
    event_recall: float = Field(ge=0, le=1)
    mention_precision: float = Field(ge=0, le=1)
    projection_core_accuracy: float | None = Field(default=None, ge=0, le=1)
    valid_schema_rate: float = Field(ge=0, le=1)
    valid_evidence_rate: float = Field(ge=0, le=1)


def length_bucket(text_chars: int) -> Literal["short", "medium", "long"]:
    if text_chars >= 10_000:
        return "long"
    if text_chars >= 2_000:
        return "medium"
    return "short"


def _round_robin(rows: Sequence[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in sorted(
        rows,
        key=lambda item: (
            str(item["message"]["source_name"]).casefold(),
            str(item["source_row_id"]),
        ),
    ):
        grouped[str(row["message"]["source_name"])].append(row)
    sources = deque(sorted(grouped, key=str.casefold))
    result: list[dict[str, Any]] = []
    while sources and len(result) < count:
        source = sources.popleft()
        result.append(grouped[source].pop(0))
        if grouped[source]:
            sources.append(source)
    if len(result) != count:
        raise ValueError(f"evaluation bucket contains fewer than {count} rows")
    return result


def select_mu_evaluation_rows(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Select all four long rows plus ten medium and ten short rows by source rotation."""

    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        bucket = length_bucket(len(str(row["message"]["text"])))
        buckets[bucket].append(row)
    if len(buckets["long"]) != 4:
        raise ValueError("the fixed MU baseline must contain exactly four long documents")
    selected = [
        *sorted(buckets["long"], key=lambda item: str(item["source_row_id"])),
        *_round_robin(buckets["medium"], 10),
        *_round_robin(buckets["short"], 10),
    ]
    if len({str(row["message"]["source_name"]) for row in selected}) < 8:
        raise ValueError("evaluation selection must cover at least eight sources")
    return selected


def _projection_value(mention: EventMention, path: str) -> JsonValue | None:
    if mention.schema_projection is None:
        return None
    value: Any = mention.schema_projection.model_dump(mode="json")
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value  # type: ignore[no-any-return]


def _matches(gold: GoldMention, mention: EventMention) -> bool:
    if gold.event_family is not mention.event_family:
        return False
    predicate_text = (f"{mention.predicate.normalized} {mention.canonical_proposition}").casefold()
    if not any(keyword.casefold() in predicate_text for keyword in gold.predicate_keywords):
        return False
    evidence = " ".join(span.text for span in mention.evidence_spans).casefold()
    return any(
        expected.casefold() in evidence or evidence in expected.casefold()
        for expected in gold.evidence_texts
    )


def evaluate_results(
    results: Sequence[SingleDocumentResult],
    gold_documents: Sequence[GoldDocument],
) -> EvaluationMetrics:
    result_by_message = {item.message_id: item for item in results}
    expected_count = sum(len(item.mentions) for item in gold_documents)
    extracted_count = sum(len(item.mentions) for item in results)
    matched_count = 0
    projection_correct = 0
    projection_total = 0
    valid_schema = 0
    valid_evidence = 0
    completed = 0
    for result in results:
        if result.status.value == "SUCCEEDED":
            completed += 1
        for mention in result.mentions:
            EventMention.model_validate(mention.model_dump(mode="json"))
            valid_schema += 1
            if mention.evidence_spans:
                valid_evidence += 1
    for gold_document in gold_documents:
        document_result = result_by_message.get(gold_document.message_id)
        if document_result is None:
            continue
        available = list(enumerate(document_result.mentions))
        used: set[int] = set()
        for gold in gold_document.mentions:
            match = next(
                (
                    (index, mention)
                    for index, mention in available
                    if index not in used and _matches(gold, mention)
                ),
                None,
            )
            if match is None:
                if gold.projection_core:
                    projection_total += len(gold.projection_core)
                continue
            index, mention = match
            used.add(index)
            matched_count += 1
            if gold.projection_core:
                projection_total += len(gold.projection_core)
                projection_correct += sum(
                    _projection_value(mention, path) == expected
                    for path, expected in gold.projection_core.items()
                )
    return EvaluationMetrics(
        document_count=len(results),
        completed_count=completed,
        expected_mention_count=expected_count,
        extracted_mention_count=extracted_count,
        matched_mention_count=matched_count,
        event_recall=matched_count / expected_count if expected_count else 1.0,
        mention_precision=matched_count / extracted_count if extracted_count else 1.0,
        projection_core_accuracy=(
            projection_correct / projection_total if projection_total else None
        ),
        valid_schema_rate=valid_schema / extracted_count if extracted_count else 1.0,
        valid_evidence_rate=valid_evidence / extracted_count if extracted_count else 1.0,
    )
