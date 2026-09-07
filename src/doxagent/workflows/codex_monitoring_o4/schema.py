"""Strict contracts for the Codex SDK monitoring configuration agent (O4)."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal, cast
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from doxagent.codex_runtime.schema import CodexMonitoringO4Node


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


class O4Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class O4RequestStatus(StrEnum):
    HELD = "HELD"
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    DEGRADED = "DEGRADED"
    INTERRUPTED = "INTERRUPTED"
    FAILED = "FAILED"
    ASSOCIATED = "ASSOCIATED"


class SourceNeedPriority(StrEnum):
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class SourceNeedResolution(StrEnum):
    KEEP_DEFAULT = "KEEP_DEFAULT"
    CONFIGURE_REGISTERED_SOURCE = "CONFIGURE_REGISTERED_SOURCE"
    ENABLE_EXISTING_CRAWLER = "ENABLE_EXISTING_CRAWLER"
    NEW_CRAWLER_REQUIRED = "NEW_CRAWLER_REQUIRED"
    NO_DEDICATED_SOURCE = "NO_DEDICATED_SOURCE"


class DeliveryItemStatus(StrEnum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    REPLAN_REQUIRED = "REPLAN_REQUIRED"
    HUMAN_INTERVENTION_REQUIRED = "HUMAN_INTERVENTION_REQUIRED"


class DeliveryProgressState(StrEnum):
    PROGRESSING = "PROGRESSING"
    STALLED = "STALLED"
    INFEASIBLE = "INFEASIBLE"


class RepairFinalStatus(StrEnum):
    RESOLVED = "RESOLVED"
    STILL_FAILED = "STILL_FAILED"
    RECONFIGURATION_REQUIRED = "RECONFIGURATION_REQUIRED"
    HUMAN_INTERVENTION_REQUIRED = "HUMAN_INTERVENTION_REQUIRED"


class FailureLayer(StrEnum):
    MESSAGE_BUS_CONFIG = "MESSAGE_BUS_CONFIG"
    SCHEDULER_POLLING = "SCHEDULER_POLLING"
    CRAWLER_TRANSPORT = "CRAWLER_TRANSPORT"
    CRAWLER_DISCOVERY = "CRAWLER_DISCOVERY"
    CONTENT_EXTRACTION = "CONTENT_EXTRACTION"
    ALERT_POLICY = "ALERT_POLICY"
    SOURCE_VIABILITY = "SOURCE_VIABILITY"


class SourceCandidate(O4Model):
    candidate_id: str
    display_name: str
    url: str
    evidence: list[str] = Field(min_length=1)
    crawler_id: str | None = None
    source_id: str | None = None


class SourceNeedPlanItem(O4Model):
    source_need_id: str
    policy_ids: list[str] = Field(min_length=1)
    disclosure_actor: str
    disclosure_channel: str
    observability_target: str
    priority: SourceNeedPriority = SourceNeedPriority.NORMAL
    resolution: SourceNeedResolution
    rationale: str
    existing_source_id: str | None = None
    existing_crawler_id: str | None = None
    desired_binding: dict[str, Any] = Field(default_factory=dict)
    primary_candidate: SourceCandidate | None = None
    alternative_candidates: list[SourceCandidate] = Field(default_factory=list, max_length=3)

    @model_validator(mode="after")
    def new_crawler_has_a_closed_candidate_set(self) -> SourceNeedPlanItem:
        if self.resolution is SourceNeedResolution.NEW_CRAWLER_REQUIRED:
            if self.primary_candidate is None:
                raise ValueError("NEW_CRAWLER_REQUIRED needs a primary_candidate")
        elif self.primary_candidate is not None or self.alternative_candidates:
            raise ValueError("candidates are only valid for NEW_CRAWLER_REQUIRED")
        ids = [item.candidate_id for item in self.alternative_candidates]
        if self.primary_candidate is not None:
            ids.append(self.primary_candidate.candidate_id)
        if len(ids) != len(set(ids)):
            raise ValueError("candidate_id values must be unique per Source Need")
        return self


class MonitoringConfigurationPlan(O4Model):
    plan_id: str = Field(default_factory=lambda: new_id("o4_plan"))
    plan_version: int = Field(default=1, ge=1)
    ticker: str
    policy_set_id: str
    policy_set_version: int = Field(ge=1)
    policy_set_sha256: str
    document2_ref: str
    baseline_observed_at: datetime
    baseline_summary: dict[str, Any]
    source_needs: list[SourceNeedPlanItem]
    applied_existing_changes: list[dict[str, Any]] = Field(default_factory=list)
    admission_evidence: list[dict[str, Any]] = Field(default_factory=list)
    deliberate_omissions: list[str] = Field(default_factory=list)
    stopping_rationale: str
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        value = value.strip().upper()
        if not value:
            raise ValueError("ticker is required")
        return value

    @model_validator(mode="after")
    def plan_has_unique_complete_needs(self) -> MonitoringConfigurationPlan:
        ids = [item.source_need_id for item in self.source_needs]
        if len(ids) != len(set(ids)):
            raise ValueError("source_need_id values must be unique")
        covered = {policy for item in self.source_needs for policy in item.policy_ids}
        if not covered:
            raise ValueError("configuration plan must cover at least one policy")
        return self


class DeliveryWorkItemCheckpoint(O4Model):
    source_need_id: str
    candidate_id: str | None = None
    crawler_id: str | None = None
    version: int | None = Field(default=None, ge=1)
    stage: str = "NOT_STARTED"
    delivery_stage: str = "NOT_STARTED"
    progress_state: DeliveryProgressState = DeliveryProgressState.PROGRESSING
    consecutive_stalled_cycles: int = Field(default=0, ge=0)
    latest_execution_id: str | None = None
    latest_evidence_refs: list[str] = Field(default_factory=list)
    previous_blocker: str | None = None
    latest_blocker: str | None = None
    next_hypothesis: str | None = None
    exhausted_candidate_ids: list[str] = Field(default_factory=list)
    # Deprecated compatibility field. It is persisted and parsed but never
    # controls delivery effort or candidate exhaustion.
    cycles_used: int = Field(default=0, ge=0)
    last_failure: str | None = None
    status: DeliveryItemStatus = DeliveryItemStatus.PENDING
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def synchronize_stage_compatibility(self) -> DeliveryWorkItemCheckpoint:
        if self.delivery_stage == "NOT_STARTED" and self.stage != "NOT_STARTED":
            self.delivery_stage = self.stage
        elif self.stage == "NOT_STARTED" and self.delivery_stage != "NOT_STARTED":
            self.stage = self.delivery_stage
        return self


class DeliveryCheckpoint(O4Model):
    plan_id: str
    plan_version: int = Field(ge=1)
    ticker: str
    items: list[DeliveryWorkItemCheckpoint] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=utc_now)


class DeliveryItemSettlement(O4Model):
    source_need_id: str
    status: DeliveryItemStatus
    selected_candidate_id: str | None = None
    crawler_id: str | None = None
    crawler_version: int | None = Field(default=None, ge=1)
    certification_run_id: str | None = None
    source_id: str | None = None
    binding_id: str | None = None
    constraints: list[str] = Field(default_factory=list)
    human_request: str | None = None
    evidence: list[str] = Field(default_factory=list)


class DeliverySettlement(O4Model):
    node: Literal[CodexMonitoringO4Node.DELIVER] = CodexMonitoringO4Node.DELIVER
    request_id: str
    plan_id: str
    plan_version: int = Field(ge=1)
    ticker: str
    items: list[DeliveryItemSettlement]
    summary: str
    completed_at: datetime = Field(default_factory=utc_now)

    @property
    def degraded(self) -> bool:
        return any(item.status is not DeliveryItemStatus.COMPLETED for item in self.items)


class RepairTrigger(O4Model):
    trigger_type: Literal["message_bus", "crawler"]
    alert_id: str
    alert_code: str
    repeat_count: int = Field(default=1, ge=1)
    source_id: str
    binding_id: str
    crawler_id: str | None = None
    execution_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RepairSettlement(O4Model):
    node: Literal[CodexMonitoringO4Node.REPAIR] = CodexMonitoringO4Node.REPAIR
    request_id: str
    ticker: str
    trigger: RepairTrigger
    diagnosed_failure_layer: FailureLayer
    action_taken: list[str]
    crawler_version_before: int | None = None
    crawler_version_after: int | None = None
    configuration_changed: bool = False
    regression_evidence: list[str] = Field(default_factory=list)
    certification_evidence: list[str] = Field(default_factory=list)
    health_verification: list[str] = Field(default_factory=list)
    final_status: RepairFinalStatus
    reconfiguration_evidence: list[str] = Field(default_factory=list)
    completed_at: datetime = Field(default_factory=utc_now)


class ConfigureCompletion(O4Model):
    node: Literal[CodexMonitoringO4Node.CONFIGURE] = CodexMonitoringO4Node.CONFIGURE
    request_id: str
    plan: MonitoringConfigurationPlan
    warnings: list[str] = Field(default_factory=list)


class O4Request(O4Model):
    request_id: str = Field(default_factory=lambda: new_id("o4_request"))
    ticker: str
    node: CodexMonitoringO4Node
    payload: dict[str, Any]
    dedupe_key: str
    logical_request_id: str = Field(default_factory=lambda: new_id("o4_logical"))
    continuation_seq: int = Field(default=0, ge=0)
    initialization_id: str | None = None
    status: O4RequestStatus = O4RequestStatus.PENDING
    associated_request_id: str | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("ticker")
    @classmethod
    def request_ticker(cls, value: str) -> str:
        value = value.strip().upper()
        if not value:
            raise ValueError("ticker is required")
        return value


class O4ThreadSlot(O4Model):
    ticker: str
    thread_id: str
    model: str
    updated_at: datetime = Field(default_factory=utc_now)


class RepairClaim(O4Model):
    repair_key: str
    owner_request_id: str
    linked_request_ids: list[str] = Field(default_factory=list)
    expires_at: datetime
    completed_at: datetime | None = None


class O4RunResult(O4Model):
    request: O4Request
    plan: MonitoringConfigurationPlan | None = None
    delivery: DeliverySettlement | None = None
    repair: RepairSettlement | None = None
    monitoring_started: bool = False
    degraded_reasons: list[str] = Field(default_factory=list)


def strict_json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Return an SDK-compatible strict JSON schema."""

    def walk(value: Any) -> Any:
        if isinstance(value, list):
            return [walk(item) for item in value]
        if not isinstance(value, dict):
            return value
        result = {key: walk(item) for key, item in value.items()}
        if result.get("type") == "object" or "properties" in result:
            result.setdefault("additionalProperties", False)
            properties = result.get("properties", {})
            if isinstance(properties, dict):
                result["required"] = list(properties)
        return result

    return cast(dict[str, Any], walk(schema))


__all__ = [name for name in globals() if not name.startswith("_")]
