"""Workflow state models for Blackboard initialization."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from doxagent.models import BlackboardPatch, DocumentType, NonEmptyStr


class WorkflowModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class WorkflowNode(StrEnum):
    START_TICKER_INITIALIZATION = "StartTickerInitialization"
    BUILD_GLOBAL_RESEARCH = "BuildGlobalResearch"
    REVIEW_GLOBAL_RESEARCH = "ReviewGlobalResearch"
    GENERATE_EXPECTATION_CONSTRUCTION = "GenerateExpectationConstruction"
    REVIEW_EXPECTATION_CONSTRUCTION = "ReviewExpectationConstruction"
    RESOLVE_EXPECTATION_CONSTRUCTION = "ResolveExpectationConstruction"
    GENERATE_EXPECTATION_DETAILS = "GenerateExpectationDetails"
    GENERATE_EXPECTATION_UNITS = "GenerateExpectationUnits"
    REVIEW_EXPECTATION_FIELDS = "ReviewExpectationFields"
    RESOLVE_OBJECTIONS_AND_DELEGATIONS = "ResolveObjectionsAndDelegations"
    PROMOTE_EXPECTATION_TO_BELIEF_STATE = "PromoteExpectationToBeliefState"
    GENERATE_GLOBAL_NARRATIVE_REPORT = "GenerateGlobalNarrativeReport"
    GENERATE_KNOWN_EVENTS = "GenerateKnownEvents"
    GENERATE_MONITORING_CONFIG = "GenerateMonitoringConfig"
    REVIEW_MONITORING_CONFIG = "ReviewMonitoringConfig"
    RESOLVE_MONITORING_CONFIG = "ResolveMonitoringConfig"
    GENERATE_MONITORING_POLICY = "GenerateMonitoringPolicy"
    REVIEW_MONITORING_POLICY = "ReviewMonitoringPolicy"
    RESOLVE_MONITORING_POLICY = "ResolveMonitoringPolicy"
    FINALIZE_INITIALIZATION = "FinalizeInitialization"


class WorkflowNodeStatus(StrEnum):
    PENDING = "pending"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"


class WorkflowRunStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    FAILED = "failed"


class WorkflowRunSummary(WorkflowModel):
    run_id: NonEmptyStr
    ticker: NonEmptyStr
    completed_nodes: list[WorkflowNode] = Field(default_factory=list)
    stable_document_types: list[DocumentType] = Field(default_factory=list)
    commit_count: int = 0
    working_memory_count: int = 0
    unresolved_objection_count: int = 0
    blocking_delegation_count: int = 0
    notes: list[NonEmptyStr] = Field(default_factory=list)


class WorkflowCheckpoint(WorkflowModel):
    run_id: NonEmptyStr
    ticker: NonEmptyStr
    status: WorkflowRunStatus = WorkflowRunStatus.RUNNING
    completed_nodes: list[WorkflowNode] = Field(default_factory=list)
    node_statuses: dict[WorkflowNode, WorkflowNodeStatus] = Field(default_factory=dict)
    next_node: WorkflowNode | None = None
    stable_document_types: list[DocumentType] = Field(default_factory=list)
    pending_patches: list[BlackboardPatch] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    summary: WorkflowRunSummary | None = None


class WorkflowExecutionResult(WorkflowModel):
    status: WorkflowRunStatus
    checkpoint: WorkflowCheckpoint
    summary: WorkflowRunSummary
    error: NonEmptyStr | None = None
