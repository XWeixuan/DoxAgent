"""Deterministic Step-3 quality metrics over Published consumer views."""

from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import Field

from doxagent.event_library.compiler import EventLibraryViewCompiler
from doxagent.event_library.contracts import (
    CanonicalRevisionBundle,
    MUGoldExpectation,
    StrictModel,
)
from doxagent.event_library.reference_review import occurrence_time_matches_precision
from doxagent.event_library.repository import EventLibraryRepository


class EventLibraryQualityReport(StrictModel):
    ticker: str
    version: int = Field(ge=0)
    active_event_count: int = Field(ge=0)
    active_fact_count: int = Field(ge=0)
    important_event_count: int = Field(ge=0)
    reference_event_count: int = Field(ge=0)
    stable_event_id_retention: float = Field(ge=0, le=1)
    known_index_event_coverage: float = Field(ge=0, le=1)
    event_detail_fact_coverage: float = Field(ge=0, le=1)
    reference_important_event_recall: float | None = Field(default=None, ge=0, le=1)
    semantic_release_gate_passed: bool
    reference_to_full_payload_ratio: float = Field(ge=0)
    reference_view_token_ratio: float = Field(ge=0)
    reference_duplicate_event_mentions: int = Field(ge=0)
    runtime_mention_count: Literal[0] = 0
    delta_total: int = Field(ge=0)
    delta_resolution_rate: float = Field(ge=0, le=1)
    pending_delta_ratio: float = Field(ge=0, le=1)
    o2_invalid_bundle_ratio: float = Field(ge=0, le=1)
    o2_repair_run_ratio: float = Field(ge=0, le=1)
    o2_partial_bundle_ratio: float = Field(ge=0, le=1)
    reference_review_scheduled: int = Field(ge=0)
    reference_review_history: int = Field(ge=0)
    importance_reference_quadrants: dict[str, int] = Field(default_factory=dict)
    current_state_critical_recall: float | None = Field(default=None, ge=0, le=1)
    exclusion_precision: float | None = Field(default=None, ge=0, le=1)
    superseded_false_positive_rate: float | None = Field(default=None, ge=0, le=1)
    current_baseline_false_negative_rate: float | None = Field(default=None, ge=0, le=1)
    d2_current_state_coverage: float | None = Field(default=None, ge=0, le=1)
    d3_maintenance_change_recall: float | None = Field(default=None, ge=0, le=1)


class SemanticQualityIssue(StrictModel):
    code: str
    severity: Literal["ERROR", "WARNING"]
    message: str
    item_id: str | None = None


class EventLibrarySemanticReport(StrictModel):
    issue_count: int = Field(ge=0)
    important_event_count: int = Field(ge=0)
    reference_event_count: int = Field(ge=0)
    unknown_occurrence_count: int = Field(ge=0)
    release_gate_passed: bool
    issues: list[SemanticQualityIssue] = Field(default_factory=list)


def compile_bundle_semantic_report(
    bundle: CanonicalRevisionBundle, *, mode: str | None = None
) -> EventLibrarySemanticReport:
    """Run post-schema semantic gates without presenting them as schema failures."""

    issues: list[SemanticQualityIssue] = []
    titles: dict[str, str] = {}
    for event in bundle.event_revisions:
        if not occurrence_time_matches_precision(
            event.occurred_at, event.occurrence_time_precision
        ):
            issues.append(
                SemanticQualityIssue(
                    code="OCCURRENCE_TIME_PRECISION_MISMATCH",
                    severity="ERROR",
                    message="occurred_at does not match occurrence_time_precision",
                    item_id=event.event_id,
                )
            )
        normalized_title = " ".join(re.findall(r"[a-z0-9]+", event.title.casefold()))
        prior = titles.get(normalized_title)
        if prior is not None:
            issues.append(
                SemanticQualityIssue(
                    code="DUPLICATE_EVENT_TITLE",
                    severity="ERROR",
                    message=f"Event title duplicates {prior}; occurrence identity requires review",
                    item_id=event.event_id,
                )
            )
        titles[normalized_title] = event.event_id
        if len(event.facts) > 25:
            issues.append(
                SemanticQualityIssue(
                    code="EXCESSIVE_EVENT_FACT_COUNT",
                    severity="ERROR",
                    message=(
                        "Event has more than 25 Facts and requires an occurrence-boundary review"
                    ),
                    item_id=event.event_id,
                )
            )
        for fact in event.facts:
            if fact.assertion_state.value in {"PLANNED", "RUMORED", "DENIED"}:
                issues.append(
                    SemanticQualityIssue(
                        code="ASSERTION_STATE_COMPATIBILITY_ALIAS",
                        severity="WARNING",
                        message=("New or substantively revised Facts should use PLAN/RUMOR/DENIAL"),
                        item_id=fact.fact_id,
                    )
                )
    important = sum(item.is_important for item in bundle.event_revisions)
    reference = sum(item.include_in_reference_view for item in bundle.event_revisions)
    unknown = sum(
        item.occurrence_time_precision.value == "UNKNOWN" for item in bundle.event_revisions
    )
    if mode == "INITIALIZE" and bundle.event_revisions:
        if not important:
            issues.append(
                SemanticQualityIssue(
                    code="INITIALIZATION_IMPORTANT_ALL_FALSE",
                    severity="ERROR",
                    message=(
                        "A non-empty initialization requires targeted importance reconsideration"
                    ),
                )
            )
        if not reference:
            issues.append(
                SemanticQualityIssue(
                    code="INITIALIZATION_REFERENCE_ALL_FALSE",
                    severity="ERROR",
                    message=(
                        "A non-empty initialization requires targeted Reference-view "
                        "reconsideration"
                    ),
                )
            )
        if unknown / len(bundle.event_revisions) > 0.25:
            issues.append(
                SemanticQualityIssue(
                    code="UNKNOWN_OCCURRENCE_RATIO_HIGH",
                    severity="ERROR",
                    message=("More than 25% of initialization Events have UNKNOWN occurrence time"),
                )
            )
        has_ongoing = any(
            fact.assertion_state.value == "ONGOING"
            for event in bundle.event_revisions
            for fact in event.facts
        )
        has_relations = any(
            event.related_event_ids or event.derived_from_event_ids or event.supersedes_event_id
            for event in bundle.event_revisions
        ) or bool(bundle.event_retirements)
        if has_ongoing and len(bundle.event_revisions) > 1 and not has_relations:
            issues.append(
                SemanticQualityIssue(
                    code="ONGOING_MILESTONES_WITHOUT_RELATION",
                    severity="ERROR",
                    message=(
                        "Ongoing/milestone evidence exists but the initialization has no "
                        "Event relations"
                    ),
                )
            )
    return EventLibrarySemanticReport(
        issue_count=len(issues),
        important_event_count=important,
        reference_event_count=reference,
        unknown_occurrence_count=unknown,
        release_gate_passed=not any(item.severity == "ERROR" for item in issues),
        issues=issues,
    )


def compile_quality_report(
    repository: EventLibraryRepository,
    *,
    ticker: str,
    version: int | None = None,
    gold: MUGoldExpectation | None = None,
) -> EventLibraryQualityReport:
    selected = repository.published_version(ticker) if version is None else version
    events = repository.published_events(ticker, selected)
    prior = repository.published_events(ticker, selected - 1) if selected > 1 else []
    event_ids = {event.event_id for event in events}
    prior_ids = {event.event_id for event in prior}
    retained = len(event_ids & prior_ids) / len(prior_ids) if prior_ids else 1.0
    compiler = EventLibraryViewCompiler(repository)
    index_ids = {
        line.split(" | ", 1)[0]
        for line in compiler.known_event_index(ticker, selected).splitlines()
        if line.strip()
    }
    detail_fact_count = sum(
        len(detail.facts)
        for event_id in event_ids
        if (detail := compiler.event_detail(ticker, event_id, selected)) is not None
    )
    fact_count = sum(len(event.facts) for event in events)
    reference_events = compiler.reference_events(ticker, selected)
    reference_ids = [event.event_id for event in reference_events]
    reference = compiler.reference_view(ticker, selected)
    important_ids = {event.event_id for event in events if event.is_important}
    reference_important = important_ids & set(reference_ids)
    full_text = json.dumps(
        [event.model_dump(mode="json") for event in events],
        ensure_ascii=False,
        sort_keys=True,
    )
    full_bytes = len(full_text.encode("utf-8"))
    reference_bytes = len(reference.encode("utf-8"))
    full_tokens = re.findall(r"\S+", full_text)
    reference_tokens = re.findall(r"\S+", reference)
    operations = repository.operational_quality_counts(ticker)
    delta_total = operations["delta_total"]
    delta_pending = operations["delta_pending"]
    o2_runs = operations["o2_run_total"]
    reference_id_set = set(reference_ids)

    def recall(ids: list[str]) -> float | None:
        expected = set(ids)
        return None if not expected else len(expected & reference_id_set) / len(expected)

    def false_negative_rate(ids: list[str]) -> float | None:
        expected = set(ids)
        return None if not expected else len(expected - reference_id_set) / len(expected)

    def false_positive_rate(ids: list[str]) -> float | None:
        expected_excluded = set(ids)
        return None if not expected_excluded else len(expected_excluded & reference_id_set) / len(
            expected_excluded
        )

    exclusion_precision: float | None = None
    if gold is not None:
        labeled = set(gold.expected_reference_event_ids) | set(gold.expected_excluded_event_ids)
        predicted_excluded = labeled - reference_id_set
        exclusion_precision = (
            None
            if not predicted_excluded
            else len(predicted_excluded & set(gold.expected_excluded_event_ids))
            / len(predicted_excluded)
        )
    quadrants = {
        "important_reference": sum(
            item.is_important and item.include_in_reference_view for item in events
        ),
        "important_not_reference": sum(
            item.is_important and not item.include_in_reference_view for item in events
        ),
        "not_important_reference": sum(
            not item.is_important and item.include_in_reference_view for item in events
        ),
        "not_important_not_reference": sum(
            not item.is_important and not item.include_in_reference_view for item in events
        ),
    }
    return EventLibraryQualityReport(
        ticker=ticker.upper(),
        version=selected,
        active_event_count=len(events),
        active_fact_count=fact_count,
        important_event_count=len(important_ids),
        reference_event_count=len(reference_ids),
        stable_event_id_retention=retained,
        known_index_event_coverage=(len(index_ids & event_ids) / len(event_ids) if events else 1.0),
        event_detail_fact_coverage=(detail_fact_count / fact_count if fact_count else 1.0),
        reference_important_event_recall=(
            len(reference_important) / len(important_ids) if important_ids else None
        ),
        # Bundle semantic validation owns release approval. Published diagnostics must
        # not reintroduce an importance=>Reference implication or quadrant quota.
        semantic_release_gate_passed=True,
        reference_to_full_payload_ratio=(reference_bytes / full_bytes if full_bytes else 0.0),
        reference_view_token_ratio=(
            len(reference_tokens) / len(full_tokens) if full_tokens else 0.0
        ),
        reference_duplicate_event_mentions=len(reference_ids) - len(set(reference_ids)),
        delta_total=delta_total,
        delta_resolution_rate=((delta_total - delta_pending) / delta_total if delta_total else 1.0),
        pending_delta_ratio=(delta_pending / delta_total if delta_total else 0.0),
        o2_invalid_bundle_ratio=(
            operations["o2_invalid_bundle_runs"] / o2_runs if o2_runs else 0.0
        ),
        o2_repair_run_ratio=(operations["o2_repair_runs"] / o2_runs if o2_runs else 0.0),
        o2_partial_bundle_ratio=(
            operations["o2_partial_bundle_runs"] / o2_runs if o2_runs else 0.0
        ),
        reference_review_scheduled=operations["reference_review_scheduled"],
        reference_review_history=operations["reference_review_history"],
        importance_reference_quadrants=quadrants,
        current_state_critical_recall=(
            None if gold is None else recall(gold.current_state_critical_event_ids)
        ),
        exclusion_precision=exclusion_precision,
        superseded_false_positive_rate=(
            None if gold is None else false_positive_rate(gold.superseded_event_ids)
        ),
        current_baseline_false_negative_rate=(
            None if gold is None else false_negative_rate(gold.current_baseline_event_ids)
        ),
        d2_current_state_coverage=(
            None if gold is None else recall(gold.d2_current_state_required_event_ids)
        ),
        d3_maintenance_change_recall=(
            None if gold is None else recall(gold.d3_maintenance_change_event_ids)
        ),
    )
