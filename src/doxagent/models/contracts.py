"""Agent task and result contract models."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from doxagent.models.blackboard import BlackboardPatch, Delegation, Objection
from doxagent.models.common import AgentName, ResultStatus, TaskType
from doxagent.models.ids import NonEmptyStr
from doxagent.prompts.schema import PromptBundle
from doxagent.skills.schema import SkillBundle


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class RunMetadata(ContractModel):
    run_id: NonEmptyStr
    ticker: NonEmptyStr
    workflow_node: NonEmptyStr | None = None
    parent_task_id: NonEmptyStr | None = None
    created_at: datetime
    labels: dict[str, str] = Field(default_factory=dict)


class AgentPermissions(ContractModel):
    readable_context_scopes: list[NonEmptyStr] = Field(default_factory=list)
    writable_targets: list[NonEmptyStr] = Field(default_factory=list)
    allowed_tools: list[NonEmptyStr] = Field(default_factory=list)
    can_raise_objection: bool = False
    can_delegate: bool = False
    can_propose_patch: bool = False
    can_access_private_memory: bool = False


class AgentTask(ContractModel):
    task_id: NonEmptyStr
    ticker: NonEmptyStr
    agent_name: AgentName
    task_type: TaskType
    input_context: dict[str, Any]
    required_output_schema: NonEmptyStr
    permissions: AgentPermissions
    run_metadata: RunMetadata
    prompt_bundle: PromptBundle | None = None
    skill_bundle: SkillBundle | None = None


class ToolCallSummary(ContractModel):
    tool_name: NonEmptyStr
    status: ResultStatus
    input_summary: NonEmptyStr
    output_summary: NonEmptyStr | None = None


class AgentError(ContractModel):
    code: NonEmptyStr
    message: NonEmptyStr
    retryable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


class AgentResult(ContractModel):
    task_id: NonEmptyStr
    agent_name: AgentName
    status: ResultStatus
    payload: dict[str, Any] = Field(default_factory=dict)
    proposed_patches: list[BlackboardPatch] = Field(default_factory=list)
    objections: list[Objection] = Field(default_factory=list)
    delegations: list[Delegation] = Field(default_factory=list)
    tool_calls: list[ToolCallSummary] = Field(default_factory=list)
    error: AgentError | None = None

    @model_validator(mode="after")
    def failed_results_must_include_error(self) -> "AgentResult":
        if self.status is ResultStatus.FAILED and self.error is None:
            raise ValueError("failed AgentResult must include error")
        return self
