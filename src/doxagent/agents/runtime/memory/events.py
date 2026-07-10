"""Append-only task event log for one ReAct AgentTask."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TaskEvent(BaseModel):
    """One immutable, ordered runtime fact."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    sequence: int = Field(ge=1)
    kind: str = Field(min_length=1)
    created_at: datetime
    step: int | None = Field(default=None, ge=1)
    payload: dict[str, Any] = Field(default_factory=dict)


class TaskEventLog:
    """Append-only owner of task events.

    Payloads are copied both on write and read so callers cannot mutate recorded
    history through a retained dictionary reference.
    """

    def __init__(self) -> None:
        self._events: list[TaskEvent] = []

    def append(
        self,
        kind: str,
        payload: dict[str, Any] | None = None,
        *,
        step: int | None = None,
    ) -> TaskEvent:
        event = TaskEvent(
            sequence=len(self._events) + 1,
            kind=kind,
            created_at=datetime.now(UTC),
            step=step,
            payload=deepcopy(payload or {}),
        )
        self._events.append(event)
        return event.model_copy(deep=True)

    def events(self) -> tuple[TaskEvent, ...]:
        return tuple(event.model_copy(deep=True) for event in self._events)

    def audit(self) -> list[dict[str, Any]]:
        audit: list[dict[str, Any]] = []
        for event in self._events:
            item = event.model_dump(mode="json", exclude={"payload"})
            item.update(deepcopy(event.payload))
            audit.append(item)
        return audit

    def __len__(self) -> int:
        return len(self._events)
