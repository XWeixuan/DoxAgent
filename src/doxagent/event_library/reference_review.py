"""Deterministic Reference Event Review clock and candidate rules."""

from __future__ import annotations

import calendar
import re
from datetime import UTC, date, datetime, timedelta

from doxagent.event_library.contracts import ReferenceReviewMode, ReferenceReviewReason

_DAY = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")
_MONTH = re.compile(r"^\s*(\d{4})-(\d{2})\s*$")
_QUARTER = re.compile(r"^\s*(\d{4})[- ]?Q([1-4])\s*$", re.IGNORECASE)


def occurrence_anchor(value: str) -> date | None:
    """Return an exact day, range end, month end, or quarter end."""

    days = _DAY.findall(value)
    if days:
        year, month, day = days[-1]
        try:
            return date(int(year), int(month), int(day))
        except ValueError:
            return None
    month_match = _MONTH.fullmatch(value)
    if month_match:
        year, month = map(int, month_match.groups())
        if 1 <= month <= 12:
            return date(year, month, calendar.monthrange(year, month)[1])
        return None
    quarter_match = _QUARTER.fullmatch(value)
    if quarter_match:
        year, quarter = map(int, quarter_match.groups())
        month = quarter * 3
        return date(year, month, calendar.monthrange(year, month)[1])
    return None


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
    return ReferenceReviewMode.EXPLICIT, ReferenceReviewReason.EXPIRED_30D, None


def initial_review_reason(anchor: date | None, as_of: datetime) -> ReferenceReviewReason:
    if anchor is None:
        return ReferenceReviewReason.TIME_UNRESOLVED
    boundary = datetime.combine(anchor + timedelta(days=30), datetime.min.time(), UTC)
    return (
        ReferenceReviewReason.NEW_OR_MODIFIED
        if as_of.astimezone(UTC) < boundary
        else ReferenceReviewReason.EXPIRED_30D
    )
