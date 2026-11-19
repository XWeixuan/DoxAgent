"""Durable source ownership, human tasks and bounded maintenance contracts."""

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from .schema import SiteModel, new_id, utc_now


def dual_active_crawler(scope, purpose):
    return bool(scope and scope.owner_site_id == "reuters"
                and scope.source_id == "reuters_site_search"
                and str(purpose) == "CRAWLER"
                and scope.verification_plan.get("crawler_routing") == "dual_active")


def parallel_source_runtime(scope, purpose):
    """Reuters pool is opt-in per purpose; legacy dual-active remains crawler-only."""
    return bool(dual_active_crawler(scope, purpose) or (
        scope and scope.owner_site_id == "reuters" and scope.source_id == "reuters_site_search"
        and str(purpose) in {"CRAWLER", "BODY"}
        and scope.verification_plan.get(str(purpose).lower() + "_routing") == "active_pool"))


def scope_identity_ids(scope):
    if not scope:
        return []
    return [member for member in [scope.primary_identity_id, scope.fallback_identity_id,
        *scope.verification_plan.get("additional_active_identity_ids", [])] if member]


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
        extra = self.verification_plan.get("additional_active_identity_ids", [])
        if (not isinstance(extra, list) or len(extra) > 1
                or any(not isinstance(member, str) or not member for member in extra)):
            raise ValueError("at most one additional active identity is supported")
        if extra and not (self.owner_site_id == "reuters" and self.source_id == "reuters_site_search"
                          and self.verification_plan.get("crawler_routing") == "active_pool"):
            raise ValueError("additional identity requires Reuters active pool")
        if len(scope_identity_ids(self)) != len(set(scope_identity_ids(self))):
            raise ValueError("scope members must be distinct")
        cap = self.verification_plan.get("max_parallel_concurrency")
        if cap is not None and (type(cap) is not int or not 1 <= cap <= 4):
            raise ValueError("parallel concurrency must be between one and four")
        interval = self.verification_plan.get("min_parallel_interval_ms")
        if interval is not None and (type(interval) is not int or interval < 0):
            raise ValueError("parallel interval must be a non-negative integer")
        return self


class OwnedBrowserTarget(SiteModel):
    allocation_id: str = Field(default_factory=lambda: new_id("allocation"))
    identity_id: str
    browser_instance_id: str
    worker_epoch: int = Field(ge=0)
    target_id: str | None = None
    owner_kind: Literal["BUSINESS", "CHALLENGE", "LOGIN", "RECOVERY"] = "BUSINESS"
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
    recovery_id: str | None = None
    resolution_reason: str | None = None
    resolved_by: str | None = None


class BrowserRecovery(SiteModel):
    recovery_id: str = Field(default_factory=lambda: new_id("recovery"))
    identity_id: str
    scope_id: str
    site_id: str
    generation: int = 0
    phase: Literal["OBSERVING", "BACKOFF", "PROBING", "RESOLVED"] = "OBSERVING"
    assessment: dict = Field(default_factory=dict)
    handling_mode: Literal["AUTO", "HUMAN"] = "AUTO"
    manual_review_reason: str | None = None
    manual_review_at: datetime | None = None
    challenge_task_id: str | None = None
    original_allocation_id: str | None = None
    original_target_id: str | None = None
    original_instance_id: str | None = None
    original_url: str | None = None
    page_status: Literal["PRESENT", "PRESENT_BUT_UNREADABLE", "LOST", "UNKNOWN"] = "UNKNOWN"
    page_checked_at: datetime | None = None
    opened_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    next_due_at: datetime = Field(default_factory=utc_now)
    next_probe_at: datetime | None = None
    resolved_at: datetime | None = None
    attempt_count: int = 0
    last_real_probe_at: datetime | None = None
    last_probe_attempt_at: datetime | None = None
    probe_id: str | None = None
    probe_stage: str | None = None
    probe_started_at: datetime | None = None
    probe_finished_at: datetime | None = None
    last_error: str | None = None
    resolved_by: str | None = None
    affected_contexts: list[dict] = Field(default_factory=list, max_length=8)
    verified_contexts: list[str] = Field(default_factory=list, max_length=8)
    lease_owner: str | None = None
    lease_until: datetime | None = None
    fingerprint: str | None = None
    cleanup_pending: bool = False
    automation_active: bool = True
    scene_preparation: dict = Field(default_factory=dict)
    document_recovery: dict = Field(default_factory=dict)


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
