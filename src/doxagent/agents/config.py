"""Agent runtime configuration and registry."""

from collections.abc import Iterable
from typing import Literal

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


AgentExecutionMode = Literal["react", "single_shot", "caller_planned_tools"]


class AgentRuntimeConfig(AgentRuntimeModel):
    execution_mode: AgentExecutionMode = "react"
    prompt_block_ids: list[NonEmptyStr] = Field(default_factory=list)
    default_internal_task_skill_ids: list[NonEmptyStr] = Field(default_factory=list)
    default_external_skill_package_ids: list[NonEmptyStr] = Field(default_factory=list)
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
            agent_name=AgentName.W1_RUNTIME_NOVELTY,
            role=AgentRole.SYSTEM,
            task_types=[TaskType.RUNTIME_W1_NOVELTY],
            runtime=AgentRuntimeConfig(
                execution_mode="single_shot",
                prompt_block_ids=["runtime.w1"],
                readable_context_scopes=[
                    DocumentType.KNOWN_EVENTS.value,
                    "runtime_source_message",
                    "monitoring_event",
                ],
                writable_targets=[],
                allowed_tools=[],
                output_schema="W1Result",
                can_raise_objection=False,
                can_delegate=False,
                can_propose_patch=False,
            ),
        ),
        AgentDefinition(
            agent_name=AgentName.W2_RUNTIME_POLICY,
            role=AgentRole.SYSTEM,
            task_types=[TaskType.RUNTIME_W2_POLICY],
            runtime=AgentRuntimeConfig(
                execution_mode="single_shot",
                prompt_block_ids=["runtime.w2"],
                readable_context_scopes=[
                    DocumentType.MONITORING_POLICY.value,
                    "runtime_source_message",
                    "monitoring_event",
                ],
                writable_targets=[],
                allowed_tools=[],
                output_schema="W2Result",
                can_raise_objection=False,
                can_delegate=False,
                can_propose_patch=False,
            ),
        ),
        AgentDefinition(
            agent_name=AgentName.O1_EXPECTATION_OWNER,
            role=AgentRole.OPERATOR,
            task_types=[
                TaskType.GENERATE_GLOBAL_RESEARCH,
                TaskType.GENERATE_GLOBAL_NARRATIVE_REPORT,
                TaskType.GENERATE_EXPECTATION_UNIT,
                TaskType.GENERATE_EXPECTATION_DETAIL,
                TaskType.REVIEW_EXPECTATION_FIELD,
                TaskType.GENERATE_KNOWN_EVENTS,
            ],
            runtime=AgentRuntimeConfig(
                prompt_block_ids=["agent.o1"],
                default_internal_task_skill_ids=[
                    "doxagent-source-discipline",
                ],
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
                    "doxa_get_narrative_report",
                ],
                output_schema=(
                    "ExpectationShellConstructionResult|ExpectationDetailCandidateResult|"
                    "ExpectationDetailResult|ExpectationConstructionResult|"
                    "Document2ResolutionPlan|KnownEventsDocument|ResearchSection"
                ),
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
                TaskType.REVIEW_MONITORING_POLICY,
                TaskType.RESOLVE_MONITORING_CONFIG,
            ],
            runtime=AgentRuntimeConfig(
                prompt_block_ids=["agent.o2"],
                default_internal_task_skill_ids=[
                    "doxagent-source-discipline",
                    "monitoring-config",
                ],
                readable_context_scopes=[
                    DocumentType.GLOBAL_RESEARCH.value,
                    DocumentType.EXPECTATION_UNIT.value,
                    DocumentType.KNOWN_EVENTS.value,
                    "working_memory",
                    "delegations",
                ],
                writable_targets=[
                    DocumentType.MONITORING_CONFIG.value,
                ],
                allowed_tools=[
                    "anysearch.search",
                    "tavily.search",
                    "monitoring.get_ticker_config",
                    "monitoring.list_status",
                    "monitoring.recent_events",
                ],
                output_schema="MonitoringConfigDocument",
                can_delegate=True,
                can_raise_objection=True,
                can_propose_patch=True,
            ),
        ),
        AgentDefinition(
            agent_name=AgentName.O3_TRADING_STRATEGY,
            role=AgentRole.OPERATOR,
            task_types=[TaskType.RUNTIME_O3_JUDGMENT],
            runtime=AgentRuntimeConfig(
                prompt_block_ids=["agent.o3"],
                default_internal_task_skill_ids=[
                    "doxagent-source-discipline",
                    "monitoring-policy",
                ],
                readable_context_scopes=[
                    DocumentType.GLOBAL_RESEARCH.value,
                    DocumentType.EXPECTATION_UNIT.value,
                    DocumentType.KNOWN_EVENTS.value,
                    DocumentType.MONITORING_CONFIG.value,
                    DocumentType.MONITORING_POLICY.value,
                    "working_memory",
                    "objections",
                ],
                writable_targets=[
                    DocumentType.KNOWN_EVENTS.value,
                    DocumentType.MONITORING_CONFIG.value,
                    DocumentType.MONITORING_POLICY.value,
                ],
                allowed_tools=[
                    "anysearch.search",
                    "tavily.search",
                    "yfinance.daily_ohlcv",
                    "twelvedata.daily_ohlcv",
                    "monitoring.recent_events",
                ],
                output_schema="O3Result",
                can_raise_objection=True,
                can_delegate=False,
                can_propose_patch=True,
            ),
        ),
        AgentDefinition(
            agent_name=AgentName.O4_MARKET_TRACE,
            role=AgentRole.OPERATOR,
            task_types=[
                TaskType.GENERATE_GLOBAL_RESEARCH,
                TaskType.REVIEW_EXPECTATION_FIELD,
                TaskType.GENERATE_MONITORING_POLICY,
                TaskType.RESOLVE_MONITORING_POLICY,
            ],
            runtime=AgentRuntimeConfig(
                prompt_block_ids=["agent.o4"],
                default_internal_task_skill_ids=[
                    "doxagent-source-discipline",
                    "ticker_price_tracking",
                    "monitoring-policy",
                ],
                default_external_skill_package_ids=[
                    "ohlcv-orchestration",
                    "quote-context",
                    "relative-performance",
                    "technical-signal-analysis",
                    "market-data-quality",
                ],
                readable_context_scopes=[
                    DocumentType.GLOBAL_RESEARCH.value,
                    DocumentType.EXPECTATION_UNIT.value,
                    DocumentType.KNOWN_EVENTS.value,
                    DocumentType.MONITORING_CONFIG.value,
                    "working_memory",
                    "objections",
                ],
                writable_targets=[
                    DocumentType.GLOBAL_RESEARCH.value,
                    DocumentType.MONITORING_POLICY.value,
                ],
                allowed_tools=[
                    "twelvedata.daily_ohlcv",
                    "yfinance.daily_ohlcv",
                    "finnhub.trade_stream",
                ],
                output_schema="ResearchSection|MonitoringPolicyDocument",
                can_raise_objection=True,
                can_propose_patch=True,
            ),
        ),
        AgentDefinition(
            agent_name=AgentName.A1_DOXATLAS_AUDIT,
            role=AgentRole.AUDIT,
            task_types=[TaskType.REVIEW_EXPECTATION_FIELD],
            runtime=AgentRuntimeConfig(
                prompt_block_ids=["agent.a1"],
                default_internal_task_skill_ids=[
                    "doxagent-source-discipline",
                ],
                readable_context_scopes=[
                    DocumentType.EXPECTATION_UNIT.value,
                    "working_memory",
                    "objections",
                ],
                writable_targets=[],
                allowed_tools=[
                    "doxa_query_analysis",
                    "doxa_get_analysis",
                    "doxa_query_propositions",
                    "doxa_get_event_source",
                    "doxa_get_social_result",
                    "doxa_get_social_result_detail",
                    "doxa_get_media_result",
                    "doxa_get_media_result_detail",
                    "doxa_get_ignored_propositions",
                ],
                output_schema="DoxAtlasAuditResult",
                can_raise_objection=True,
                can_delegate=True,
            ),
        ),
        AgentDefinition(
            agent_name=AgentName.A2_FACT_CHECK,
            role=AgentRole.AUDIT,
            task_types=[
                TaskType.FACT_CHECK,
                TaskType.DELEGATED_RETRIEVAL,
                TaskType.REVIEW_EXPECTATION_FIELD,
            ],
            runtime=AgentRuntimeConfig(
                prompt_block_ids=["agent.a2"],
                default_internal_task_skill_ids=[],
                readable_context_scopes=[
                    DocumentType.EXPECTATION_UNIT.value,
                    DocumentType.KNOWN_EVENTS.value,
                    "delegations",
                ],
                writable_targets=[],
                allowed_tools=[
                    "anysearch.search",
                    "tavily.search",
                    "tavily.extract",
                ],
                output_schema="DelegatedRetrievalResult",
                can_raise_objection=True,
            ),
        ),
        AgentDefinition(
            agent_name=AgentName.C1_FUNDAMENTAL_RESEARCH,
            role=AgentRole.CONSULTANT,
            task_types=[
                TaskType.GENERATE_GLOBAL_RESEARCH,
                TaskType.REVIEW_EXPECTATION_FIELD,
                TaskType.REVIEW_MONITORING_CONFIG,
            ],
            runtime=AgentRuntimeConfig(
                prompt_block_ids=["agent.c1"],
                default_internal_task_skill_ids=[
                    "doxagent-source-discipline",
                    "fundamental-research",
                ],
                default_external_skill_package_ids=[
                    "financial-statement",
                    "valuation-model",
                ],
                readable_context_scopes=["working_memory"],
                writable_targets=[DocumentType.GLOBAL_RESEARCH.value],
                allowed_tools=[
                    "sec.company_facts_and_filings",
                    "sec.filing_sections",
                    "alpha.company_overview",
                    "alpha.financial_statements",
                    "alpha.shares_outstanding",
                    "alpha.earnings_events",
                    "yfinance.hk_basic_snapshot",
                    "tavily.search",
                ],
                output_schema="ResearchSection",
                can_raise_objection=True,
                can_delegate=True,
                can_propose_patch=True,
            ),
        ),
        AgentDefinition(
            agent_name=AgentName.C2_MACRO_RESEARCH,
            role=AgentRole.CONSULTANT,
            task_types=[TaskType.GENERATE_GLOBAL_RESEARCH],
            runtime=AgentRuntimeConfig(
                prompt_block_ids=["agent.c2"],
                default_internal_task_skill_ids=["doxagent-source-discipline"],
                default_external_skill_package_ids=[
                    "macro-analysis",
                ],
                readable_context_scopes=["working_memory"],
                writable_targets=[DocumentType.GLOBAL_RESEARCH.value],
                allowed_tools=[
                    "fred.series_observations",
                    "bls.timeseries",
                    "bea.nipa_data",
                    "fed.fomc_calendar_materials",
                    "polymarket.market_probability",
                    "twelvedata.daily_ohlcv",
                    "yfinance.daily_ohlcv",
                ],
                output_schema="ResearchSection",
                can_delegate=True,
                can_propose_patch=True,
            ),
        ),
        AgentDefinition(
            agent_name=AgentName.C3_INDUSTRY_RESEARCH,
            role=AgentRole.CONSULTANT,
            task_types=[
                TaskType.GENERATE_GLOBAL_RESEARCH,
                TaskType.REVIEW_EXPECTATION_FIELD,
                TaskType.REVIEW_MONITORING_CONFIG,
            ],
            runtime=AgentRuntimeConfig(
                prompt_block_ids=["agent.c3"],
                default_internal_task_skill_ids=[
                    "doxagent-source-discipline",
                    "industry-research",
                ],
                default_external_skill_package_ids=[
                    "sector-overview",
                    "competitive-analysis",
                ],
                readable_context_scopes=["working_memory"],
                writable_targets=[DocumentType.GLOBAL_RESEARCH.value],
                allowed_tools=[
                    "finnhub.company_peers",
                    "sec.company_facts_and_filings",
                    "fmp.sector_performance",
                    "tavily.search",
                    "tavily.extract",
                ],
                output_schema="ResearchSection",
                can_raise_objection=True,
                can_delegate=True,
                can_propose_patch=True,
            ),
        ),
    ]


def default_agent_registry() -> AgentRegistry:
    return AgentRegistry(default_agent_definitions())
