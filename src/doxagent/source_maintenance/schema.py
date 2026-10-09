from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Stage(StrEnum):
    OBSERVED = "OBSERVED"
    TRIAGE = "TRIAGE"
    AUTO_RECOVERY = "AUTO_RECOVERY"
    CODING = "CODING"
    VERIFY = "VERIFY"
    APPLY = "APPLY"
    OBSERVE = "OBSERVE"
    RECOVERED = "RECOVERED"
    STABLE = "STABLE"
    WAITING_WINDOW = "WAITING_WINDOW"
    HUMAN_REQUIRED = "HUMAN_REQUIRED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    ROLLED_BACK = "ROLLED_BACK"
    CANCELLED = "CANCELLED"


class ActionKind(StrEnum):
    RECONNECT_EXTERNAL_DRIVER = "RECONNECT_EXTERNAL_DRIVER"
    SELECT_EXISTING_COMBINATION = "SELECT_EXISTING_COMBINATION"
    PATCH_SOURCE_PARAMETERS = "PATCH_SOURCE_PARAMETERS"
    APPLY_SOURCE_PATCH = "APPLY_SOURCE_PATCH"
    ROLLBACK_SOURCE_UPDATE = "ROLLBACK_SOURCE_UPDATE"
    REQUEST_HUMAN_MAINTENANCE = "REQUEST_HUMAN_MAINTENANCE"


class HealthSample(Model):
    sample_id: str = Field(default_factory=lambda: new_id("sample"))
    source_id: str
    binding_id: str | None = None
    affected_bindings: list[str] = Field(default_factory=list)
    ticker: str | None = None
    stage: str = "ACQUISITION"
    operation_id: str
    started_at: datetime
    completed_at: datetime = Field(default_factory=utcnow)
    eligible: bool = True
    enabled: bool = True
    window: str = "REALTIME"
    status: str
    category: str | None = None
    reason: str | None = None
    query_attempts: int = 0
    query_successes: int = 0
    query_failures: int = 0
    query_deferred: int = 0
    valid_content: bool = False
    site_id: str | None = None
    identity_id: str | None = None
    runtime_kind: str | None = None
    driver_epoch: int | None = None
    driver_recovery_failed: bool = False
    queue_ms: int | None = None
    service_ms: int | None = None
    article_key: str | None = None
    accessible_candidate: bool = True
    target_interval_seconds: int = 60
    due_at: datetime | None = None
    in_flight: bool = False


class Incident(Model):
    incident_id: str = Field(
        default_factory=lambda: new_id("incident"), pattern=r"^[A-Za-z0-9_-]+$"
    )
    resource_key: str
    failure_class: str
    source_ids: list[str] = Field(default_factory=list)
    affected_bindings: list[str] = Field(default_factory=list)
    stage: Stage = Stage.OBSERVED
    first_observed_at: datetime
    last_observed_at: datetime
    updated_at: datetime = Field(default_factory=utcnow)
    recovered_at: datetime | None = None
    generation: int = 0
    owner: str | None = None
    lease_until: datetime | None = None
    round_count: int = 0
    human_action: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)


class ProposedAction(Model):
    kind: ActionKind
    target: str
    parameters: dict[str, Any] = Field(default_factory=dict)


class RepairReport(Model):
    root_cause: str
    confidence: float = Field(ge=0, le=1)
    evidence_refs: list[str]
    failure_scope: str
    proposed_actions: list[ProposedAction]
    changed_files: list[str]
    tests: list[str]
    affected_sites: list[str]
    expected_recovery: str
    remaining_items: list[str]
    human_action: str | None


class RepairRound(Model):
    round_id: str = Field(default_factory=lambda: new_id("round"))
    incident_id: str
    number: int
    status: str = "PENDING"
    started_at: datetime = Field(default_factory=utcnow)
    deadline: datetime
    thread_id: str | None = None
    turn_id: str | None = None
    worktree: str
    evidence_path: str
    candidate_commit: str | None = None
    feedback: dict[str, Any] | None = None
    report: RepairReport | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    receipt: dict[str, Any] = Field(default_factory=dict)


class DeploymentManifest(Model):
    source_commit: str
    images: dict[str, str]
    file_hashes: dict[str, str]
    overlay_root: str | None = None
    config_revisions: dict[str, int] = Field(default_factory=dict)
    applied_patches: list[str] = Field(default_factory=list)


class ActionReceipt(Model):
    action_id: str
    incident_id: str
    resource_key: str
    kind: ActionKind
    target: str
    status: str = "PREPARED"
    created_at: datetime = Field(default_factory=utcnow)
    before: dict[str, Any] = Field(default_factory=dict)
    after: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
