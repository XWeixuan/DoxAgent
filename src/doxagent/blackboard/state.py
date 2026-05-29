"""In-memory Blackboard service state models."""

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from doxagent.models import (
    AgentName,
    BeliefStateSnapshot,
    CommitLogEntry,
    Delegation,
    Objection,
    WorkingMemoryEntry,
)
from doxagent.models.ids import NonEmptyStr, new_id


class ServiceModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorkflowState(StrEnum):
    INITIALIZED = "initialized"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class BlackboardRun(ServiceModel):
    run_id: NonEmptyStr
    ticker: NonEmptyStr
    created_by: AgentName
    created_at: datetime
    workflow_state: WorkflowState = WorkflowState.INITIALIZED
    working_memory: list[WorkingMemoryEntry] = Field(default_factory=list)
    belief_state: BeliefStateSnapshot
    objections: list[Objection] = Field(default_factory=list)
    delegations: list[Delegation] = Field(default_factory=list)
    commit_log: list[CommitLogEntry] = Field(default_factory=list)


def create_empty_run(ticker: str, created_by: AgentName) -> BlackboardRun:
    now = datetime.now(UTC)
    return BlackboardRun(
        run_id=new_id("run"),
        ticker=ticker,
        created_by=created_by,
        created_at=now,
        belief_state=BeliefStateSnapshot(
            snapshot_id=new_id("belief"),
            ticker=ticker,
            created_at=now,
        ),
    )
