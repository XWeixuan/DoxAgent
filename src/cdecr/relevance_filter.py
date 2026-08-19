"""Candidate-scoped equity relevance Gate shared by production and evaluation."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Generic, Literal, TypeVar

from pydantic import Field

from cdecr.contracts import SourceMessage, StrictModel
from cdecr.model_boundary import model_datetime
from cdecr.ports import ResponsesModelRequest, StructuredModelRequest
from cdecr.single_document_contracts import (
    DocumentBlock,
    DreamCandidate,
    DreamerModelOutput,
    PreprocessedDocument,
)

RELEVANCE_PROMPT_VERSION = "relevance-filter-v3-ticker"
_T = TypeVar("_T")


class RelevanceMode(StrEnum):
    OFF = "off"
    SHADOW = "shadow"
    ENFORCE = "enforce"


class RelevanceEvent(StrictModel):
    id: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    exact_evidence: list[str] = Field(min_length=1)


class RelevanceInput(StrictModel):
    target: str = Field(min_length=1)
    events: list[RelevanceEvent]


class RelevanceDecision(StrictModel):
    id: str = Field(min_length=1)
    relevance: Literal["RELEVANT", "IRRELEVANT"]


class RelevanceOutput(StrictModel):
    results: list[RelevanceDecision]


@dataclass(frozen=True)
class TypedInvocation(Generic[_T]):
    output: _T
    response_id: str | None


@dataclass(frozen=True)
class CandidateGateDecision:
    candidate_id: str
    relevance: Literal["RELEVANT", "IRRELEVANT"] | None
    keep: bool
    reason: str


@dataclass(frozen=True)
class RelevanceSelection:
    retained: list[DreamCandidate]
    decisions: list[CandidateGateDecision]
    ignored_ids: tuple[str, ...]


def target_profile_for_source(
    source: SourceMessage,
    profiles: Mapping[str, str],
) -> str | None:
    """Resolve one configured profile or an unambiguous ticker-only target."""

    normalized = {str(key).strip().upper(): str(value).strip() for key, value in profiles.items()}
    matches = [normalized[ticker] for ticker in source.ticker_hints if ticker in normalized]
    unique = list(dict.fromkeys(value for value in matches if value))
    if len(unique) == 1:
        return unique[0]
    if unique:
        return None
    tickers = list(dict.fromkeys(ticker.strip().upper() for ticker in source.ticker_hints))
    return tickers[0] if len(tickers) == 1 else None


def ticker_target_instruction(target: str) -> str:
    """Keep the relevance target explicit without injecting a business profile."""

    return (
        f"The target is the U.S.-listed company identified by stock ticker {target}; "
        "judge relevance to that company or security."
    )


def dreamer_block_exposed_lengths(
    document: PreprocessedDocument,
    block: DocumentBlock,
) -> dict[str, int]:
    """Return the exact segment prefix lengths exposed to one Dreamer block."""

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
    return exposed_lengths


def dreamer_block_request(
    *,
    source: SourceMessage,
    document: PreprocessedDocument,
    block: DocumentBlock,
    system_prompt: str,
    zero_recovery: bool = False,
    strict: bool = False,
) -> StructuredModelRequest:
    """Build the production Dreamer request for one document block."""

    exposed_lengths = dreamer_block_exposed_lengths(document, block)
    user_payload = {
        "published_at": model_datetime(source.published_at),
        "allowed_segment_ids": list(exposed_lengths),
        "common_context": block.common_context,
        "block": block.text,
    }
    return StructuredModelRequest(
        system_prompt=(
            system_prompt
            + (
                "\n\nA prior pass returned no candidates. Recheck the title and "
                "every exposed segment for any explicit, independently "
                "truth-evaluable event. Return empty only if none exists."
                if zero_recovery
                else ""
            )
        ),
        user_prompt=json.dumps(user_payload, ensure_ascii=False),
        json_schema=DreamerModelOutput.model_json_schema(),
        output_mode="json_schema" if strict else "json_object",
        schema_name="cdecr_dreamer_output",
        strict=strict,
    )


def dreamer_response_request(
    request: StructuredModelRequest,
    *,
    reasoning_effort: Literal["none", "low", "high", "max"] = "none",
    strict: bool = False,
) -> ResponsesModelRequest:
    """Compile a normal typed Dreamer request onto the Responses transport."""

    return ResponsesModelRequest(
        input=[
            {"role": "system", "content": request.system_prompt},
            {"role": "user", "content": request.user_prompt},
        ],
        json_schema=request.json_schema,
        output_mode="json_schema" if strict else "json_object",
        schema_name="cdecr_dreamer_output",
        strict=strict,
        reasoning_effort=reasoning_effort,
        session_cache=True,
        metadata=request.metadata,
    )


def responses_request_from_structured(
    request: StructuredModelRequest,
    *,
    previous_response_id: str | None,
    reasoning_effort: Literal["none", "low", "high", "max"],
) -> ResponsesModelRequest:
    return ResponsesModelRequest(
        input=[
            {"role": "system", "content": request.system_prompt},
            {"role": "user", "content": request.user_prompt},
        ],
        json_schema=request.json_schema,
        output_mode=request.output_mode,
        schema_name=request.schema_name,
        strict=request.strict,
        previous_response_id=previous_response_id,
        reasoning_effort=reasoning_effort,
        session_cache=True,
        metadata=request.metadata,
    )


def relevance_response_request(
    *,
    target: str,
    candidates: Sequence[DreamCandidate],
    system_prompt: str,
    strict: bool = False,
) -> tuple[ResponsesModelRequest, dict[str, str]]:
    short_to_full = {
        f"c{index}": candidate.candidate_id
        for index, candidate in enumerate(candidates, start=1)
    }
    gate_input = RelevanceInput(
        target=target,
        events=[
            RelevanceEvent(
                id=short_id,
                statement=candidate.statement,
                exact_evidence=[item.text for item in candidate.evidence_locations],
            )
            for short_id, candidate in zip(short_to_full, candidates, strict=True)
        ],
    )
    request = ResponsesModelRequest(
        input=[
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": json.dumps(gate_input.model_dump(mode="json"), ensure_ascii=False),
            },
        ],
        json_schema=RelevanceOutput.model_json_schema(),
        output_mode="json_schema" if strict else "json_object",
        schema_name="cdecr_relevance_output",
        strict=strict,
        previous_response_id=None,
        reasoning_effort="none",
        session_cache=False,
        metadata={"candidate_count": len(candidates)},
    )
    return request, short_to_full


def frozen_relevance_response_request(
    *,
    dreamer_request: StructuredModelRequest,
    frozen_output: DreamerModelOutput,
    target: str,
    candidates: Sequence[DreamCandidate],
    relevance_system_prompt: str,
) -> tuple[ResponsesModelRequest, dict[str, str]]:
    """Rebuild the two-turn transcript without using a live Dreamer response."""

    request, short_to_full = relevance_response_request(
        target=target,
        candidates=candidates,
        system_prompt=relevance_system_prompt,
    )
    # Relevance is deliberately independent from Dreamer transport/session
    # state.  Frozen evaluation and production therefore share exactly the
    # same two-message request.
    del dreamer_request, frozen_output
    return request, short_to_full


def validate_relevance_coverage(
    payload: object,
    *,
    expected_short_ids: set[str],
) -> RelevanceOutput:
    """Require every input candidate exactly once before any item can be dropped."""

    output = RelevanceOutput.model_validate(payload)
    actual = [item.id for item in output.results]
    if len(actual) != len(set(actual)) or set(actual) != expected_short_ids:
        raise ValueError("relevance results must cover every candidate id exactly once")
    return output


def select_candidates_fail_open(
    candidates: Sequence[DreamCandidate],
    payload: object,
    *,
    short_to_full: Mapping[str, str],
) -> RelevanceSelection:
    """Apply valid binary decisions item-by-item; every protocol defect keeps the item."""

    candidate_by_id = {candidate.candidate_id: candidate for candidate in candidates}
    raw_results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(raw_results, list):
        decisions = [
            CandidateGateDecision(candidate.candidate_id, None, True, "INVALID_BATCH_PAYLOAD")
            for candidate in candidates
        ]
        return RelevanceSelection(list(candidates), decisions, ())

    values: dict[str, list[str | None]] = defaultdict(list)
    ignored_ids: set[str] = set()
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        short_id = item.get("id")
        if not isinstance(short_id, str) or short_id not in short_to_full:
            if isinstance(short_id, str):
                ignored_ids.add(short_id)
            continue
        relevance = item.get("relevance")
        values[short_id].append(relevance if isinstance(relevance, str) else None)

    decision_by_full: dict[str, CandidateGateDecision] = {}
    for short_id, full_id in short_to_full.items():
        labels = values.get(short_id, [])
        valid = [label for label in labels if label in {"RELEVANT", "IRRELEVANT"}]
        if not labels:
            decision = CandidateGateDecision(full_id, None, True, "MISSING_ID")
        elif len(labels) != 1:
            reason = "DUPLICATE_CONFLICT" if len(set(valid)) > 1 else "DUPLICATE_ID"
            decision = CandidateGateDecision(full_id, None, True, reason)
        elif len(valid) != 1:
            decision = CandidateGateDecision(full_id, None, True, "INVALID_ENUM")
        else:
            label = valid[0]
            assert label in {"RELEVANT", "IRRELEVANT"}
            decision = CandidateGateDecision(
                full_id,
                label,  # type: ignore[arg-type]
                label == "RELEVANT",
                "VALID_DECISION",
            )
        decision_by_full[full_id] = decision

    decisions = [decision_by_full[candidate.candidate_id] for candidate in candidates]
    retained = [candidate_by_id[item.candidate_id] for item in decisions if item.keep]
    return RelevanceSelection(retained, decisions, tuple(sorted(ignored_ids)))
