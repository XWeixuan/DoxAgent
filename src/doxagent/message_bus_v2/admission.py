"""Shared publication eligibility, independent of provider query windows and dedupe."""

from datetime import UTC, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, model_validator


class AdmissionContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    policy_version: int = 1
    mode: Literal["REALTIME", "CLOSED_SWEEP"] = "REALTIME"
    sweep_id: str | None = None
    source_task_id: str | None = None
    window_start: datetime | None = None
    cutoff: datetime | None = None

    @model_validator(mode="after")
    def validate_window(self) -> "AdmissionContext":
        if self.mode == "CLOSED_SWEEP":
            if not (self.sweep_id and self.source_task_id and self.window_start and self.cutoff):
                raise ValueError("sweep ownership and window required")
            if (
                self.window_start.tzinfo is None
                or self.cutoff.tzinfo is None
                or self.window_start >= self.cutoff
            ):
                raise ValueError("invalid sweep window")
        return self


def evaluate_admission(
    published_at: datetime, context: AdmissionContext | None, now: datetime, basis: str = "EXACT"
) -> str | None:
    """None means eligible. Date/unknown inputs use today+yesterday, never fake precision."""
    context = context or AdmissionContext()
    if published_at.tzinfo is None or now.tzinfo is None:
        return "INVALID_TIMEZONE"
    if basis in {"DATE", "UNKNOWN_FIRST_SEEN"}:
        # Calendar dates, not a rolling 48h window. ET matches the US news workflow.
        today = (context.cutoff or now).astimezone(ZoneInfo("America/New_York")).date()
        day = published_at.astimezone(ZoneInfo("America/New_York")).date()
        return None if today - timedelta(days=1) <= day <= today else "OUTSIDE_RECENT_DATES"
    if context.mode == "CLOSED_SWEEP":
        assert context.window_start is not None and context.cutoff is not None
        return (
            None
            if context.window_start <= published_at < context.cutoff
            else "OUTSIDE_SWEEP_WINDOW"
        )
    age = (now.astimezone(UTC) - published_at.astimezone(UTC)).total_seconds()
    if age < -60:
        return "FUTURE_PUBLICATION"
    return "EXPIRED_PUBLICATION" if age > 1800 else None
