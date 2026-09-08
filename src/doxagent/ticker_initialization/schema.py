"""Workflow outcomes, execution identities, and compact activation references."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(UTC)


def semantic_day(at: datetime) -> str:
    """ET 02:00 boundary; the spring-forward gap naturally advances to 03:00."""
    from doxagent.semantic_clock import semantic_day as resolve

    return resolve(at).isoformat()


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class NodeSpec(Model):
    key: str = Field(min_length=1)
    block: str = Field(min_length=1)
    dependencies: list[str] = Field(default_factory=list)
    inputs: dict[str, Any] = Field(default_factory=dict)


class NodeResult(Model):
    """Only usable completed artifacts are returned; warnings never branch the DAG."""

    artifacts: dict[str, Any] = Field(default_factory=dict)
    quality_annotations: list[str] = Field(default_factory=list)


class RunRecord(Model):
    schema_version: str = "ticker-initialization-v2"
    initialization_id: str
    ticker: str
    research_cutoff_at: datetime
    semantic_day: str
    status: RunStatus = RunStatus.QUEUED
    phase: str = "UPSTREAM"
    monitor_mode: str = "TRADING"
    workflow_version: str = "V2"
    operation_kind: str = "INITIALIZE"
    base_revision: str | None = None
    control_epoch: int | None = None
    control_operation_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    state_seq: int = 0
    error: str | None = None
    manual_resume_required: bool = False


class Lease(Model):
    initialization_id: str
    owner: str
    token: int


class NodeRecord(Model):
    key: str
    block: str
    dependencies: list[str]
    inputs: dict[str, Any]
    status: str = "PENDING"
    generation: int = 1
    ordinal: int = 0
    execution_id: str | None = None
    execution_version: dict[str, str] = Field(default_factory=dict)
    receipt: dict[str, Any] = Field(default_factory=dict)
    result: NodeResult | None = None
    error: str | None = None


def progress_phase(node: NodeRecord) -> str:
    """Stable UI-facing semantics, independent of internal node/group names."""
    key = str(node.inputs.get("managed_by") or node.key)
    if key.startswith("o4.configure"):
        return "O4_CONFIGURE"
    if key.startswith("o4.deliver"):
        return "O4_DELIVER"
    return {
        "D1": "UPSTREAM",
        "CDECR": "UPSTREAM",
        "REGISTER": "REGISTER",
        "ACTIVATION": "ACTIVATE",
        "BUS_START": "START_BUS",
        "RUNTIME_START": "START_RUNTIME",
    }.get(node.block, node.block)


class InitializationError(RuntimeError):
    pass


class LeaseLost(InitializationError):
    pass


class BudgetExhausted(InitializationError):
    pass
