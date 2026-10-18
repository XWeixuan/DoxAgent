"""Durable source ownership, human tasks and bounded maintenance contracts."""

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from .schema import SiteModel, new_id, utc_now


class SourceAccessScope(SiteModel):
    scope_id: str = Field(pattern=r"^(source|publisher):[a-z0-9_.:-]+$")
    source_id: str | None = None
    owner_site_id: str
    revision: int = Field(default=0, ge=0)
    enabled: bool = True
    primary_identity_id: str
    fallback_identity_id: str | None = None
    verification_plan: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def distinct_members(self):
        if self.primary_identity_id == self.fallback_identity_id:
            raise ValueError("primary and fallback must be independent identities")
        if self.source_id and self.scope_id != f"source:{self.source_id}":
            raise ValueError("source scope must match its source_id")
        return self


class OwnedBrowserTarget(SiteModel):
    allocation_id: str = Field(default_factory=lambda: new_id("allocation"))
    identity_id: str
    browser_instance_id: str
    worker_epoch: int = Field(ge=0)
    target_id: str | None = None
    owner_kind: Literal["BUSINESS", "CHALLENGE", "LOGIN"] = "BUSINESS"
    owner_ref: str | None = None
    state: Literal["ALLOCATING", "LIVE", "CLOSE_PENDING", "CLOSED", "LOST"] = "ALLOCATING"
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @property
    def marker(self) -> str:
        return f"about:blank#doxagent-{self.allocation_id}"


class ChallengeTask(SiteModel):
    task_id: str = Field(default_factory=lambda: new_id("challenge"))
    scope_id: str
    identity_id: str
    site_id: str
    purpose: str
    operation_id: str
    combination_id: str | None = None
    original_url: str
    original_target_id: str | None
    browser_instance_id: str
    allocation_id: str
    state: Literal["WAITING_HUMAN", "VERIFYING", "RESOLVED", "TARGET_LOST"] = "WAITING_HUMAN"
    reason: str
    opened_at: datetime = Field(default_factory=utc_now)
    resolved_at: datetime | None = None
    last_check: datetime = Field(default_factory=utc_now)
    predecessor_task_id: str | None = None
    superseded_by: str | None = None
    seen_count: int = 1


class MaintenanceSession(SiteModel):
    session_id: str = Field(default_factory=lambda: new_id("maintenance"))
    kind: Literal["LOGIN", "CHALLENGE"]
    scope_id: str
    identity_id: str
    site_id: str
    task_id: str | None = None
    allocation_id: str | None = None
    target_id: str | None = None
    control_token: str | None = None
    browser_instance_id: str | None = None
    credential_event_committed: bool = False
    state: Literal["OPEN", "RELEASED", "CLOSED"] = "OPEN"
    generation: int = 1
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


MaintenanceAction = Literal[
    "OPEN_LOGIN",
    "OPEN_CHALLENGE",
    "VERIFY_LOGIN",
    "VERIFY_CHALLENGE",
    "RELEASE_VIEWER",
    "CLOSE_LOGIN",
    "REOPEN_LOST_CHALLENGE",
    "ACTIVATE_PRIMARY",
]


class MaintenanceCommand(SiteModel):
    action: MaintenanceAction
    idempotency_key: str = Field(min_length=1, max_length=200)
    scope_id: str | None = None
    identity_id: str | None = None
    session_id: str | None = None
    task_id: str | None = None
    verification_url: str | None = None
    confirm_discard: bool = False


class MaintenanceOperation(MaintenanceCommand):
    action: MaintenanceAction | Literal["ACTIVATE_FALLBACK"]
    operation_id: str = Field(default_factory=lambda: new_id("maintenance_op"))
    state: Literal["QUEUED", "RUNNING", "DONE", "FAILED", "CANCELLED", "INTERRUPTED"] = "QUEUED"
    stage: str = "QUEUED"
    stage_started_at: datetime = Field(default_factory=utc_now)
    deadline_at: datetime
    result: dict = Field(default_factory=dict)
    error: str | None = None
    updated_at: datetime = Field(default_factory=utc_now)
