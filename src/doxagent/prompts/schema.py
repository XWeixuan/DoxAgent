"""Prompt and skill package definitions for agent prompt assembly."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from doxagent.models.common import AgentName, TaskType
from doxagent.models.ids import NonEmptyStr


class PromptModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class PromptResourceKind(StrEnum):
    PROMPT_BLOCK = "prompt_block"
    INTERNAL_TASK_SKILL = "internal_task_skill"
    EXTERNAL_SKILL_PACKAGE = "external_skill_package"


class PromptBlockType(StrEnum):
    SYSTEM = "system"
    AGENT = "agent"
    WORKFLOW = "workflow"


class ExternalSkillSource(StrEnum):
    DOXAGENT = "doxagent"
    VIBE_TRADING = "vibe_trading"
    FINANCIAL_SERVICES = "financial_services"
    HERMES_FINANCE = "hermes_finance"


class PromptResourceSummary(PromptModel):
    resource_id: NonEmptyStr
    name: NonEmptyStr
    version: NonEmptyStr
    kind: PromptResourceKind
    body: NonEmptyStr


class PromptBlockDefinition(PromptModel):
    resource_id: NonEmptyStr
    name: NonEmptyStr
    version: NonEmptyStr
    kind: PromptResourceKind = PromptResourceKind.PROMPT_BLOCK
    block_type: PromptBlockType
    applicable_agents: list[AgentName] = Field(default_factory=list)
    applicable_task_types: list[TaskType] = Field(default_factory=list)
    workflow_nodes: list[NonEmptyStr] = Field(default_factory=list)
    body: NonEmptyStr

    def summarize(self) -> PromptResourceSummary:
        return PromptResourceSummary(
            resource_id=self.resource_id,
            name=self.name,
            version=self.version,
            kind=self.kind,
            body=self.body,
        )


class InternalTaskSkillDefinition(PromptModel):
    resource_id: NonEmptyStr
    name: NonEmptyStr
    version: NonEmptyStr
    kind: PromptResourceKind = PromptResourceKind.INTERNAL_TASK_SKILL
    manual_only: bool = False
    applicable_agents: list[AgentName] = Field(default_factory=list)
    applicable_task_types: list[TaskType] = Field(default_factory=list)
    workflow_nodes: list[NonEmptyStr] = Field(default_factory=list)
    output_requirements: list[NonEmptyStr] = Field(default_factory=list)
    guardrails: list[NonEmptyStr] = Field(default_factory=list)
    body: NonEmptyStr

    def summarize(self) -> PromptResourceSummary:
        return PromptResourceSummary(
            resource_id=self.resource_id,
            name=self.name,
            version=self.version,
            kind=self.kind,
            body=self.body,
        )


class ExternalSkillPackageDefinition(PromptModel):
    resource_id: NonEmptyStr
    name: NonEmptyStr
    version: NonEmptyStr
    kind: PromptResourceKind = PromptResourceKind.EXTERNAL_SKILL_PACKAGE
    source_project: NonEmptyStr
    source_kind: ExternalSkillSource
    applicable_agents: list[AgentName] = Field(default_factory=list)
    applicable_task_types: list[TaskType] = Field(default_factory=list)
    allowed_tools: list[NonEmptyStr] = Field(default_factory=list)
    output_requirements: list[NonEmptyStr] = Field(default_factory=list)
    guardrails: list[NonEmptyStr] = Field(default_factory=list)
    body: NonEmptyStr

    def summarize(self) -> PromptResourceSummary:
        return PromptResourceSummary(
            resource_id=self.resource_id,
            name=self.name,
            version=self.version,
            kind=self.kind,
            body=self.body,
        )


PromptDefinition = (
    PromptBlockDefinition | InternalTaskSkillDefinition | ExternalSkillPackageDefinition
)


class PromptBundle(PromptModel):
    prompt_blocks: list[PromptResourceSummary] = Field(default_factory=list)
    internal_task_skills: list[PromptResourceSummary] = Field(default_factory=list)
    external_skill_packages: list[PromptResourceSummary] = Field(default_factory=list)

    @property
    def prompt_block_ids(self) -> list[str]:
        return [item.resource_id for item in self.prompt_blocks]

    @property
    def internal_task_skill_ids(self) -> list[str]:
        return [item.resource_id for item in self.internal_task_skills]

    @property
    def external_skill_package_ids(self) -> list[str]:
        return [item.resource_id for item in self.external_skill_packages]

    @property
    def versions(self) -> dict[str, str]:
        return {
            item.resource_id: item.version
            for item in [
                *self.prompt_blocks,
                *self.internal_task_skills,
                *self.external_skill_packages,
            ]
        }


class AssembledPrompt(PromptModel):
    instructions: NonEmptyStr
    user_prompt: NonEmptyStr
    metadata: dict[str, str] = Field(default_factory=dict)
