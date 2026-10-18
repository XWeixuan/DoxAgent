"""HTTP and job contracts owned by the Codex worker."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from doxagent.codex_runtime.schema import (
    CODEX_D1_WORKFLOW_VERSION,
    CodexResearchAgentRole,
    CodexResearchNode,
    CodexWorkflowVersion,
    ResearchLane,
    utc_now,
)


class WorkerModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ThreadForkRequest(WorkerModel):
    parent_thread_id: str = Field(min_length=1)
    run_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
    idempotency_key: str = Field(min_length=1, max_length=512)
    model: str | None = None
    model_provider: str | None = None


class TemplateRegisterRequest(WorkerModel):
    job_id: str


class ResearchPackRequest(WorkerModel):
    files: dict[str, str] | None = None


class CaseMaterializeRequest(WorkerModel):
    packs: dict[str, str] = Field(default_factory=dict)
    files: dict[str, str] = Field(default_factory=dict)


class W3PrepareRequest(WorkerModel):
    operation_key: str = Field(min_length=1, max_length=512)
    record_id: str
    source_case_id: str | None
    parent_thread_id: str
    run_id: str
    attempt_id: str
    phase: Literal["trade_decision"] = "trade_decision"
    research_pack_id: str
    reference_pack_id: str
    case_overlay_sha: str
    profile: dict[str, str | None]
    capability_contract_version: str = "instructions-only-v1"
    cutoff_at: datetime


class WorkspaceWriteRequest(WorkerModel):
    content: str
    expected_sha256: str | None = None
    content_type: str = "text/plain; charset=utf-8"


class WorkspaceFileResponse(WorkerModel):
    relative_path: str
    sha256: str
    size_bytes: int
    content_type: str
    content: str | None = None


class WorkspaceInventory(WorkerModel):
    run_id: str
    files: list[WorkspaceFileResponse]


class WorkspaceInventoryQuery(WorkerModel):
    paths: list[str] = Field(default_factory=list, max_length=512)
    prefixes: list[str] = Field(default_factory=list, max_length=32)


class WorkerRunRequest(WorkerModel):
    task_phase: Literal["w3_template", "trade_decision", "post_decision_operations"] | None = None
    web_search_mode: Literal["disabled", "live"] | None = None
    workflow_version: CodexWorkflowVersion = CODEX_D1_WORKFLOW_VERSION
    research_lane: ResearchLane = ResearchLane.LEGACY_DOCUMENT1
    run_id: str
    ticker: str
    node: CodexResearchNode
    agent_role: CodexResearchAgentRole
    attempt_id: str
    cutoff_at: datetime = Field(default_factory=utc_now)
    prompt: str
    output_schema: dict[str, Any]
    thread_id: str | None = None
    model: str | None = None
    model_provider: str | None = None
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "max"
    read_only: bool = False
    data_mcp_enabled: bool = True
    research_assets_mcp_enabled: bool = False
    w3_entry_version: str | None = None
    o4_operations_enabled: bool = False
    initialization_id: str | None = None
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=512)
    allow_subagents: bool = False
    max_subagents: int = Field(default=2, ge=0, le=2)
    timeout_seconds: int = Field(default=1800, ge=30, le=7200)
    execution_lane: Literal["realtime", "background"] | None = None
    deadline_at: datetime | None = None


class WorkerTokenUsage(WorkerModel):
    input_tokens: int = Field(default=0, ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    reasoning_output_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)


class WorkerLoopEvent(WorkerModel):
    sequence: int = Field(ge=0)
    event: str = "item.completed"
    item_type: str
    name: str
    status: str
    duration_ms: int | None = Field(default=None, ge=0)
    summary: str = ""


class WorkerTurnTelemetry(WorkerModel):
    usage: WorkerTokenUsage = Field(default_factory=WorkerTokenUsage)
    observed_usage: dict[str, int | None] | None = None
    usage_source: str = "sdk_last_request"
    usage_complete: bool = False
    sdk_duration_ms: int | None = Field(default=None, ge=0)
    worker_wall_time_ms: int | None = Field(default=None, ge=0)
    mcp_call_count: int = Field(default=0, ge=0)
    command_call_count: int = Field(default=0, ge=0)
    subagent_call_count: int = Field(default=0, ge=0)
    file_change_count: int = Field(default=0, ge=0)
    slowest_steps: list[WorkerLoopEvent] = Field(default_factory=list)
    failures: list[str] = Field(default_factory=list)
    events: list[WorkerLoopEvent] = Field(default_factory=list)


class WorkerJob(WorkerModel):
    resource_receipt: dict[str, Any] = Field(default_factory=dict)
    execution_phase: str = "QUEUED"
    wait_reason: str | None = None
    infra_recovery_count: int = 0
    next_retry_at: float = 0
    worker_generation: str | None = None
    cleanup_error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    job_id: str
    run_id: str
    attempt_id: str
    request_sha256: str | None = None
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    thread_id: str | None = None
    turn_id: str | None = None
    final_response: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    telemetry: WorkerTurnTelemetry | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("error_code", mode="before")
    @classmethod
    def normalize_error_code(cls, value: object) -> str | None:
        if value is None:
            return None
        return str(value)


class WorkerEvent(WorkerModel):
    sequence: int
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
