"""Durable contracts for initialization repair incidents."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from doxagent.ticker_initialization.schema import utc_now


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IncidentStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUCCEEDED = "SUCCEEDED"
    HUMAN_REQUIRED = "HUMAN_REQUIRED"
    CANCELLED = "CANCELLED"


class IncidentPhase(StrEnum):
    CONTEXT = "CONTEXT"
    CODING = "CODING"
    VERIFY = "VERIFY"
    QUEUE = "QUEUE"
    EXECUTE = "EXECUTE"
    RECORD = "RECORD"


class RoundStatus(StrEnum):
    STARTED = "STARTED"
    AGENT_RUNNING = "AGENT_RUNNING"
    VERIFIED = "VERIFIED"
    QUEUED = "QUEUED"
    EXECUTING = "EXECUTING"
    FINISHED = "FINISHED"
    FAILED = "FAILED"


class RepairIncident(Model):
    incident_id: str
    initialization_id: str
    ticker: str
    status: IncidentStatus = IncidentStatus.ACTIVE
    phase: IncidentPhase = IncidentPhase.CONTEXT
    current_round_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    source_image_id: str | None = None
    source_revision: str | None = None
    source_hash: str | None = None
    worktree_path: str | None = None
    branch_name: str | None = None
    thread_id: str | None = None
    control_epoch: int | None = None
    last_error: str | None = None
    retry_at: datetime | None = None
    payload: dict[str, object] = Field(default_factory=dict)


class RepairRound(Model):
    round_id: str
    incident_id: str
    seq: int = Field(ge=1)
    failed_state_seq: int = Field(ge=0)
    target_nodes: list[str]
    node_ordinals: dict[str, int]
    status: RoundStatus = RoundStatus.STARTED
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    thread_turn_ids: list[str] = Field(default_factory=list)
    control_operation_id: str | None = None
    repair_commit: str | None = None
    image_id: str | None = None
    agent_container: str | None = None
    executor_container: str | None = None
    cdecr_executor_container: str | None = None
    tests: list[dict[str, object]] = Field(default_factory=list)
    exit_code: int | None = None
    result: str | None = None
    payload: dict[str, object] = Field(default_factory=dict)


class NodeBudget(Model):
    incident_id: str
    node_key: str
    rounds_started: int = Field(default=0, ge=0, le=3)
    first_failure: dict[str, object]
    latest_failure: dict[str, object]
    crossed_at: datetime | None = None
    crossed_state_seq: int | None = None
    alias_of: str | None = None


class RepairAgentReport(Model):
    root_cause: str = Field(min_length=1)
    evidence: list[str]
    changed_files: dict[str, str]
    tests: list[dict[str, object]]
    downstream_implications: list[str]
    remaining_items: list[str]


class IssueEntry(Model):
    entry_id: str
    incident_id: str
    round_id: str | None = None
    kind: str
    created_at: datetime = Field(default_factory=utc_now)
    content: dict[str, object]
