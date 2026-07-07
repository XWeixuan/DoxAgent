"""Contracts for model usage audit events."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

JsonObject = dict[str, Any]


class ModelUsageModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModelUsageEvent(ModelUsageModel):
    """One provider-neutral model call audit event."""

    event_id: str = Field(default_factory=lambda: f"mue_{uuid4().hex}")
    provider: str
    model: str
    status: str
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    retry_count: int = Field(default=0, ge=0)
    fallback_used: bool = False
    latency_seconds: float | None = Field(default=None, ge=0)
    ticker: str | None = None
    run_id: str | None = None
    workflow_node: str | None = None
    runtime_node: str | None = None
    agent_name: str | None = None
    task_type: str | None = None
    source_message_id: str | None = None
    execution_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
    raw_usage: JsonObject = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("provider", "model", "status")
    @classmethod
    def _non_empty_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be empty")
        return normalized

    @field_validator("ticker")
    @classmethod
    def _normalize_ticker(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().upper()
        return normalized or None

    @field_validator(
        "run_id",
        "workflow_node",
        "runtime_node",
        "agent_name",
        "task_type",
        "source_message_id",
        "execution_id",
        "error_code",
        "error_message",
    )
    @classmethod
    def _optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None
