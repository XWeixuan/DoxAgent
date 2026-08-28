"""Deterministic Reference Event Review clock and candidate rules."""

from __future__ import annotations

import calendar
import re
from datetime import UTC, date, datetime, timedelta

from doxagent.event_library.contracts import (
    CanonicalEvent,
    OccurrenceTimePrecision,
    ReferenceReviewMode,
    ReferenceReviewReason,
)

_DAY = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")
_MONTH = re.compile(r"^\s*(\d{4})-(\d{2})\s*$")
_QUARTER = re.compile(r"^\s*(\d{4})[- ]?Q([1-4])\s*$", re.IGNORECASE)
_YEAR = re.compile(r"^\s*(\d{4})\s*$")


def occurrence_anchor(
    value: str, precision: OccurrenceTimePrecision | str | None = None
) -> date | None:
    """Return the shared view/review anchor for one Canonical occurrence time."""

    cleaned = value.strip()
    if cleaned == "UNKNOWN":
        return None
    if ".." in cleaned:
        _start, end = cleaned.rsplit("..", 1)
        return occurrence_anchor(end)
    normalized_precision = None if precision is None else OccurrenceTimePrecision(str(precision))
    if normalized_precision is OccurrenceTimePrecision.TIMESTAMP:
        try:
            parsed = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed.date() if parsed.tzinfo is not None else None

    days = _DAY.findall(cleaned)
    if days:
        year, month, day = days[-1]
        try:
            return date(int(year), int(month), int(day))
        except ValueError:
            return None
    month_match = _MONTH.fullmatch(cleaned)
    if month_match:
        year, month = map(int, month_match.groups())
        if 1 <= month <= 12:
            return date(year, month, calendar.monthrange(year, month)[1])
        return None
    quarter_match = _QUARTER.fullmatch(cleaned)
    if quarter_match:
        year, quarter = map(int, quarter_match.groups())
        month = quarter * 3
        return date(year, month, calendar.monthrange(year, month)[1])
    year_match = _YEAR.fullmatch(cleaned)
    if year_match:
        return date(int(year_match.group(1)), 12, 31)
    try:
        parsed = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.date() if parsed.tzinfo is not None else None


def occurrence_start(
    value: str, precision: OccurrenceTimePrecision | str | None = None
) -> date | None:
    """Return the first calendar date represented by an occurrence wire value."""

    cleaned = value.strip()
    if cleaned == "UNKNOWN":
        return None
    if ".." in cleaned:
        start, _end = cleaned.split("..", 1)
        return occurrence_start(start)
    normalized = None if precision is None else OccurrenceTimePrecision(str(precision))
    if normalized is OccurrenceTimePrecision.MONTH or _MONTH.fullmatch(cleaned):
        match = _MONTH.fullmatch(cleaned)
        if match is None:
            return None
        try:
            return date(int(match.group(1)), int(match.group(2)), 1)
        except ValueError:
            return None
    if normalized is OccurrenceTimePrecision.QUARTER or _QUARTER.fullmatch(cleaned):
        match = _QUARTER.fullmatch(cleaned)
        if match is None:
            return None
        year, quarter = map(int, match.groups())
        try:
            return date(year, (quarter - 1) * 3 + 1, 1)
        except ValueError:
            return None
    if normalized is OccurrenceTimePrecision.YEAR or _YEAR.fullmatch(cleaned):
        match = _YEAR.fullmatch(cleaned)
        if match is None:
            return None
        try:
            return date(int(match.group(1)), 1, 1)
        except ValueError:
            return None
    return occurrence_anchor(cleaned, precision)


def occurrence_time_matches_precision(value: str, precision: OccurrenceTimePrecision) -> bool:
    """Hard wire check for the frozen precision-to-value mapping."""

    cleaned = value.strip()
    if precision is OccurrenceTimePrecision.UNKNOWN:
        return cleaned == "UNKNOWN"
    if precision is OccurrenceTimePrecision.DAY:
        return _DAY.fullmatch(cleaned) is not None and occurrence_anchor(cleaned) is not None
    if precision is OccurrenceTimePrecision.MONTH:
        return _MONTH.fullmatch(cleaned) is not None and occurrence_anchor(cleaned) is not None
    if precision is OccurrenceTimePrecision.QUARTER:
        return _QUARTER.fullmatch(cleaned) is not None
    if precision is OccurrenceTimePrecision.YEAR:
        return _YEAR.fullmatch(cleaned) is not None
    if precision is OccurrenceTimePrecision.TIMESTAMP:
        try:
            parsed = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
        except ValueError:
            return False
        return parsed.tzinfo is not None
    if precision is OccurrenceTimePrecision.INTERVAL:
        if cleaned.count("..") != 1:
            return False
        start, end = cleaned.split("..", 1)
        start_anchor = occurrence_anchor(start)
        end_anchor = occurrence_anchor(end)
        return start_anchor is not None and end_anchor is not None and start_anchor <= end_anchor
def event_review_anchor(event: CanonicalEvent) -> date | None:
    """Use episode end/max Fact occurrence, never a Fact subject horizon."""

    anchors: list[date] = []
    event_anchor = occurrence_anchor(event.occurred_at, event.occurrence_time_precision)
    if event_anchor is not None:
        anchors.append(event_anchor)
    for fact in event.facts:
        value = fact.fact_occurred_at
        if value is None:
            continue
        if value == "SAME":
            if event_anchor is not None:
                anchors.append(event_anchor)
            continue
        anchor = occurrence_anchor(value, fact.fact_occurrence_time_precision)
        if anchor is not None:
            anchors.append(anchor)
    return max(anchors) if anchors else None


def classify_review(
    *,
    anchor: date | None,
    as_of: datetime,
    include_in_reference_view: bool,
    last_reviewed_at: datetime | None = None,
) -> tuple[ReferenceReviewMode, ReferenceReviewReason, datetime | None]:
    """Classify one completed judgment and compute its next frozen-clock review."""

    current = as_of.astimezone(UTC)
    if anchor is None:
        return (
            ReferenceReviewMode.EXPLICIT,
            ReferenceReviewReason.TIME_UNRESOLVED,
            current + timedelta(days=7) if include_in_reference_view else None,
        )
    boundary = datetime.combine(anchor + timedelta(days=30), datetime.min.time(), UTC)
    if current < boundary:
        next_at = min(current + timedelta(days=10), boundary)
        return ReferenceReviewMode.IMPLICIT, ReferenceReviewReason.PERIODIC_10D, next_at
    if include_in_reference_view:
        return (
            ReferenceReviewMode.EXPLICIT,
            ReferenceReviewReason.INCLUDED_RECHECK_7D,
            current + timedelta(days=7),
        )
    return ReferenceReviewMode.EXPLICIT, ReferenceReviewReason.AGE_REVIEW_DUE_30D, None


def initial_review_reason(anchor: date | None, as_of: datetime) -> ReferenceReviewReason:
    if anchor is None:
        return ReferenceReviewReason.TIME_UNRESOLVED
    boundary = datetime.combine(anchor + timedelta(days=30), datetime.min.time(), UTC)
    return (
        ReferenceReviewReason.NEW_OR_MODIFIED
        if as_of.astimezone(UTC) < boundary
        else ReferenceReviewReason.AGE_REVIEW_DUE_30D
    )
