"""Attempt-scoped cleaned Observation contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from doxagent.codex_runtime.schema import utc_now


class ObservationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PersistedObservation(ObservationModel):
    schema_version: Literal["attempt_observation/1.0"] = "attempt_observation/1.0"
    run_id: str
    attempt_id: str
    alias: str = ""
    block_id: str
    tool_call_id: str
    tool_name: str
    title: str
    locator: str
    block_type: str
    content: Any
    content_hash: str
    source_locator: str | None = None
    source_coordinates: Any | None = None
    provider: str
    method_version: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class ObservationCallRecord(ObservationModel):
    schema_version: Literal["observation_call/1.0"] = "observation_call/1.0"
    run_id: str
    attempt_id: str
    tool_call_id: str
    tool_name: str
    provider: str
    input_payload: dict[str, Any]
    execution_status: str
    availability: str
    block_aliases: list[str]
    selected_aliases: list[str]
    delivery_mode: Literal["inline", "pack"]
    original_chars: int = Field(ge=0)
    raw_payload_bytes: int = Field(ge=0)
    inline_bytes: int = Field(ge=0)
    pack_bytes: int = Field(ge=0)
    latency_ms: int = Field(ge=0)
    created_at: datetime = Field(default_factory=utc_now)
