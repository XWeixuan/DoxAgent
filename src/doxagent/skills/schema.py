"""Skill registry and injection contract models."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from doxagent.models.common import AgentName, TaskType
from doxagent.models.ids import NonEmptyStr


class SkillModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, frozen=True)


class SkillSource(StrEnum):
    DOXAGENT = "doxagent"
    VIBE_TRADING = "vibe_trading"
    FINANCIAL_SERVICES = "financial_services"
    HERMES_FINANCE = "hermes_finance"


class SkillContent(SkillModel):
    prompt_fragment: NonEmptyStr
    analysis_framework: NonEmptyStr
    output_requirements: list[NonEmptyStr] = Field(default_factory=list)
    guardrails: list[NonEmptyStr] = Field(default_factory=list)


class SkillDefinition(SkillModel):
    skill_id: NonEmptyStr
    name: NonEmptyStr
    version: NonEmptyStr
    source_project: NonEmptyStr
    source_kind: SkillSource
    applicable_agents: list[AgentName] = Field(default_factory=list)
    applicable_task_types: list[TaskType] = Field(default_factory=list)
    allowed_tools: list[NonEmptyStr] = Field(default_factory=list)
    content: SkillContent


class SkillSummary(SkillModel):
    skill_id: NonEmptyStr
    name: NonEmptyStr
    version: NonEmptyStr
    source_kind: SkillSource
    source_project: NonEmptyStr
    prompt_fragment: NonEmptyStr
    analysis_framework: NonEmptyStr
    output_requirements: list[NonEmptyStr] = Field(default_factory=list)
    allowed_tools: list[NonEmptyStr] = Field(default_factory=list)
    guardrails: list[NonEmptyStr] = Field(default_factory=list)


class SkillBundle(SkillModel):
    skills: list[SkillSummary] = Field(default_factory=list)

    @property
    def skill_ids(self) -> list[str]:
        return [skill.skill_id for skill in self.skills]

    @property
    def skill_versions(self) -> dict[str, str]:
        return {skill.skill_id: skill.version for skill in self.skills}


def summarize_skill(definition: SkillDefinition) -> SkillSummary:
    return SkillSummary(
        skill_id=definition.skill_id,
        name=definition.name,
        version=definition.version,
        source_kind=definition.source_kind,
        source_project=definition.source_project,
        prompt_fragment=definition.content.prompt_fragment,
        analysis_framework=definition.content.analysis_framework,
        output_requirements=list(definition.content.output_requirements),
        allowed_tools=list(definition.allowed_tools),
        guardrails=list(definition.content.guardrails),
    )
