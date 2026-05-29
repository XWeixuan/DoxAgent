"""Agent runtime configuration and registry."""

from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict, Field

from doxagent.agents.errors import UnknownAgentError
from doxagent.models import (
    AgentName,
    AgentPermissions,
    AgentRole,
    DocumentType,
    NonEmptyStr,
    TaskType,
)


class AgentRuntimeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AgentRuntimeConfig(AgentRuntimeModel):
    role_instruction: NonEmptyStr
    readable_context_scopes: list[NonEmptyStr] = Field(default_factory=list)
    writable_targets: list[NonEmptyStr] = Field(default_factory=list)
    allowed_tools: list[NonEmptyStr] = Field(default_factory=list)
    output_schema: NonEmptyStr
    can_raise_objection: bool = False
    can_delegate: bool = False
    can_propose_patch: bool = False
    can_access_private_memory: bool = False

    def to_permissions(self) -> AgentPermissions:
        return AgentPermissions(
            readable_context_scopes=list(self.readable_context_scopes),
            writable_targets=list(self.writable_targets),
            allowed_tools=list(self.allowed_tools),
            can_raise_objection=self.can_raise_objection,
            can_delegate=self.can_delegate,
            can_propose_patch=self.can_propose_patch,
            can_access_private_memory=self.can_access_private_memory,
        )


class AgentDefinition(AgentRuntimeModel):
    agent_name: AgentName
    role: AgentRole
    task_types: list[TaskType] = Field(default_factory=list)
    runtime: AgentRuntimeConfig


class AgentRegistry:
    def __init__(self, definitions: Iterable[AgentDefinition] = ()) -> None:
        self._definitions: dict[AgentName, AgentDefinition] = {}
        for definition in definitions:
            self.register(definition)

    def register(self, definition: AgentDefinition) -> None:
        self._definitions[definition.agent_name] = definition

    def get(self, agent_name: AgentName) -> AgentDefinition:
        try:
            return self._definitions[agent_name].model_copy(deep=True)
        except KeyError as exc:
            raise UnknownAgentError(f"Unknown agent: {agent_name}") from exc

    def names(self) -> list[AgentName]:
        return sorted(self._definitions, key=str)


def default_agent_definitions() -> list[AgentDefinition]:
    return [
        AgentDefinition(
            agent_name=AgentName.O1_EXPECTATION_OWNER,
            role=AgentRole.OPERATOR,
            task_types=[
                TaskType.GENERATE_EXPECTATION_UNIT,
                TaskType.REVIEW_EXPECTATION_FIELD,
                TaskType.GENERATE_KNOWN_EVENTS,
            ],
            runtime=AgentRuntimeConfig(
                role_instruction="Own expectation-unit synthesis and field-level promotion.",
                readable_context_scopes=[
                    DocumentType.GLOBAL_RESEARCH.value,
                    DocumentType.EXPECTATION_UNIT.value,
                    DocumentType.KNOWN_EVENTS.value,
                    "working_memory",
                    "objections",
                    "delegations",
                ],
                writable_targets=[
                    DocumentType.EXPECTATION_UNIT.value,
                    DocumentType.KNOWN_EVENTS.value,
                ],
                allowed_tools=[
                    "doxatlas.query",
                    "market_data.snapshot",
                    "fact_check.search",
                    "external_research.mock",
                ],
                output_schema="ExpectationUnitDocument",
                can_raise_objection=True,
                can_delegate=True,
                can_propose_patch=True,
            ),
        ),
        AgentDefinition(
            agent_name=AgentName.O2_MONITORING_CONFIG,
            role=AgentRole.OPERATOR,
            task_types=[
                TaskType.GENERATE_MONITORING_CONFIG,
                TaskType.GENERATE_MONITORING_POLICY,
            ],
            runtime=AgentRuntimeConfig(
                role_instruction="Translate accepted expectations into monitoring items.",
                readable_context_scopes=[
                    DocumentType.EXPECTATION_UNIT.value,
                    DocumentType.KNOWN_EVENTS.value,
                    "working_memory",
                    "delegations",
                ],
                writable_targets=[
                    DocumentType.MONITORING_CONFIG.value,
                    DocumentType.MONITORING_POLICY.value,
                ],
                allowed_tools=["doxatlas.query", "fact_check.search"],
                output_schema="MonitoringConfigDocument|MonitoringPolicyDocument",
                can_delegate=True,
                can_propose_patch=True,
            ),
        ),
        AgentDefinition(
            agent_name=AgentName.O4_MARKET_TRACE,
            role=AgentRole.OPERATOR,
            task_types=[TaskType.GENERATE_GLOBAL_RESEARCH],
            runtime=AgentRuntimeConfig(
                role_instruction="Explain price action and market narrative causality.",
                readable_context_scopes=[
                    DocumentType.GLOBAL_RESEARCH.value,
                    "working_memory",
                    "objections",
                ],
                writable_targets=[DocumentType.GLOBAL_RESEARCH.value],
                allowed_tools=[
                    "market_data.snapshot",
                    "market_data.quote",
                    "market_data.ohlcv",
                    "market_data.multiple_quotes",
                    "doxatlas.query",
                ],
                output_schema="MarketTraceResult",
                can_raise_objection=True,
                can_propose_patch=True,
            ),
        ),
        AgentDefinition(
            agent_name=AgentName.A1_DOXATLAS_AUDIT,
            role=AgentRole.AUDIT,
            task_types=[TaskType.REVIEW_EXPECTATION_FIELD],
            runtime=AgentRuntimeConfig(
                role_instruction="Audit whether claims are grounded in DoxAtlas evidence.",
                readable_context_scopes=[
                    DocumentType.EXPECTATION_UNIT.value,
                    "working_memory",
                    "objections",
                ],
                writable_targets=[],
                allowed_tools=["doxatlas.query", "doxatlas.source_lookup"],
                output_schema="AuditFinding",
                can_raise_objection=True,
            ),
        ),
        AgentDefinition(
            agent_name=AgentName.A2_FACT_CHECK,
            role=AgentRole.AUDIT,
            task_types=[TaskType.FACT_CHECK, TaskType.REVIEW_EXPECTATION_FIELD],
            runtime=AgentRuntimeConfig(
                role_instruction="Check factual claims and report support or uncertainty.",
                readable_context_scopes=[
                    DocumentType.EXPECTATION_UNIT.value,
                    DocumentType.KNOWN_EVENTS.value,
                    "delegations",
                ],
                writable_targets=[],
                allowed_tools=["fact_check.search", "external_research.mock"],
                output_schema="FactCheckFinding",
                can_raise_objection=True,
            ),
        ),
        AgentDefinition(
            agent_name=AgentName.C1_FUNDAMENTAL_RESEARCH,
            role=AgentRole.CONSULTANT,
            task_types=[TaskType.GENERATE_GLOBAL_RESEARCH],
            runtime=AgentRuntimeConfig(
                role_instruction="Draft fundamental research sections for the global document.",
                readable_context_scopes=["working_memory"],
                writable_targets=[DocumentType.GLOBAL_RESEARCH.value],
                allowed_tools=["external_research.mock", "fact_check.search"],
                output_schema="ResearchSection",
                can_delegate=True,
                can_propose_patch=True,
            ),
        ),
        AgentDefinition(
            agent_name=AgentName.C2_MACRO_RESEARCH,
            role=AgentRole.CONSULTANT,
            task_types=[TaskType.GENERATE_GLOBAL_RESEARCH],
            runtime=AgentRuntimeConfig(
                role_instruction="Draft macro research sections for the global document.",
                readable_context_scopes=["working_memory"],
                writable_targets=[DocumentType.GLOBAL_RESEARCH.value],
                allowed_tools=["external_research.mock"],
                output_schema="ResearchSection",
                can_delegate=True,
                can_propose_patch=True,
            ),
        ),
        AgentDefinition(
            agent_name=AgentName.C3_INDUSTRY_RESEARCH,
            role=AgentRole.CONSULTANT,
            task_types=[TaskType.GENERATE_GLOBAL_RESEARCH],
            runtime=AgentRuntimeConfig(
                role_instruction="Draft industry research sections for the global document.",
                readable_context_scopes=["working_memory"],
                writable_targets=[DocumentType.GLOBAL_RESEARCH.value],
                allowed_tools=["external_research.mock", "doxatlas.query"],
                output_schema="ResearchSection",
                can_delegate=True,
                can_propose_patch=True,
            ),
        ),
    ]


def default_agent_registry() -> AgentRegistry:
    return AgentRegistry(default_agent_definitions())
