"""Global Research workflow integration for migrated C1/C2/C3/O4 modules."""

import json
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from doxagent.adapters import (
    FundamentalBriefAgentModule,
    IndustryResearchAgentModule,
    MacroContextAgentModule,
)
from doxagent.agents import MarketTraceAgentModule
from doxagent.models import (
    AgentName,
    AgentResult,
    EvidenceRef,
    EvidenceSourceType,
    GlobalResearchDocument,
    ResearchSection,
    ResultStatus,
    new_id,
)
from doxagent.workflows.errors import WorkflowContractError


class GlobalResearchInputs(BaseModel):
    """Inputs used by the Phase 3.6 Global Research module integration."""

    model_config = ConfigDict(extra="forbid")

    market: str = "US equities"
    geography: str = "US"
    timeframe: str = "initialization 3-12 months"
    sector_or_theme: str | None = None
    industry_angle: str = "initialization"
    universe: list[str] = Field(default_factory=list)
    benchmarks: list[str] = Field(default_factory=lambda: ["SPY"])
    peers: list[str] = Field(default_factory=list)
    market_trace_period: str = "1y"
    market_trace_interval: str = "1d"

    def resolved(self, ticker: str) -> "GlobalResearchInputs":
        return self.model_copy(
            update={
                "sector_or_theme": self.sector_or_theme or f"{ticker} industry context",
                "universe": self.universe or [ticker],
            },
            deep=True,
        )


class GlobalResearchModuleRunner:
    """Run the migrated C1/C2/C3/O4 modules without writing Blackboard state."""

    def __init__(
        self,
        *,
        fundamental_module: FundamentalBriefAgentModule | None = None,
        macro_module: MacroContextAgentModule | None = None,
        industry_module: IndustryResearchAgentModule | None = None,
        market_trace_module: MarketTraceAgentModule | None = None,
    ) -> None:
        self.fundamental_module = fundamental_module or FundamentalBriefAgentModule()
        self.macro_module = macro_module or MacroContextAgentModule()
        self.industry_module = industry_module or IndustryResearchAgentModule()
        self.market_trace_module = market_trace_module or MarketTraceAgentModule()

    def run(self, ticker: str, inputs: GlobalResearchInputs) -> list[AgentResult]:
        resolved = inputs.resolved(ticker)
        metadata = {
            "workflow_node": "BuildGlobalResearch",
            "integration_phase": "3.6",
            "mock_fixture": True,
        }
        return [
            self.fundamental_module.run(
                target=ticker,
                market=resolved.market,
                metadata=metadata,
            ),
            self.macro_module.run(
                goal=f"{ticker} global research context",
                timeframe=resolved.timeframe,
                metadata=metadata,
            ),
            self.industry_module.run(
                sector_or_theme=resolved.sector_or_theme or f"{ticker} industry context",
                angle=resolved.industry_angle,
                universe=resolved.universe,
                market=resolved.market,
                geography=resolved.geography,
                metadata=metadata,
            ),
            self.market_trace_module.run(
                ticker=ticker,
                period=resolved.market_trace_period,
                interval=resolved.market_trace_interval,
                benchmarks=resolved.benchmarks,
                peers=resolved.peers,
                metadata={key: str(value) for key, value in metadata.items()},
            ),
        ]


class GlobalResearchAssembler:
    """Assemble C1/C2/C3/O4 module outputs into one GlobalResearchDocument."""

    def assemble(
        self,
        ticker: str,
        inputs: GlobalResearchInputs,
        results: list[AgentResult],
    ) -> GlobalResearchDocument:
        by_agent = {result.agent_name: self._require_success(result) for result in results}
        missing = [
            agent.value
            for agent in (
                AgentName.C1_FUNDAMENTAL_RESEARCH,
                AgentName.C2_MACRO_RESEARCH,
                AgentName.C3_INDUSTRY_RESEARCH,
                AgentName.O4_MARKET_TRACE,
            )
            if agent not in by_agent
        ]
        if missing:
            raise WorkflowContractError(
                f"Global Research module outputs missing required agents: {', '.join(missing)}"
            )

        return GlobalResearchDocument(
            document_id=new_id("doc"),
            ticker=ticker,
            created_at=datetime.now(UTC),
            fundamental_report=self._section(
                by_agent[AgentName.C1_FUNDAMENTAL_RESEARCH],
                AgentName.C1_FUNDAMENTAL_RESEARCH,
                "fundamental",
            ),
            macro_report=self._section(
                by_agent[AgentName.C2_MACRO_RESEARCH],
                AgentName.C2_MACRO_RESEARCH,
                "macro",
            ),
            industry_report=self._section(
                by_agent[AgentName.C3_INDUSTRY_RESEARCH],
                AgentName.C3_INDUSTRY_RESEARCH,
                "industry",
            ),
            market_narrative_report=self._pending_market_narrative_section(ticker, inputs),
            market_trace_report=self._section(
                by_agent[AgentName.O4_MARKET_TRACE],
                AgentName.O4_MARKET_TRACE,
                "market_trace",
            ),
        )

    def downstream_context(self, results: list[AgentResult]) -> dict[str, Any]:
        context: dict[str, Any] = {
            "fundamental": {},
            "macro": {},
            "industry": {},
            "market_trace": {},
        }
        for result in results:
            structured = self._structured(result)
            if result.agent_name is AgentName.C1_FUNDAMENTAL_RESEARCH:
                context["fundamental"] = {
                    "risks": structured.get("risks", []),
                    "catalysts": structured.get("catalysts", []),
                    "thesis": structured.get("thesis", []),
                }
            elif result.agent_name is AgentName.C2_MACRO_RESEARCH:
                context["macro"] = {
                    "risk_scenarios": structured.get("risk_scenarios", []),
                    "monitoring_dashboard": structured.get("monitoring_dashboard", []),
                    "macro_allocation": structured.get("macro_allocation", {}),
                }
            elif result.agent_name is AgentName.C3_INDUSTRY_RESEARCH:
                context["industry"] = {
                    "downstream_hints": structured.get("downstream_hints", []),
                    "risks": structured.get("risks", []),
                    "catalysts": structured.get("catalysts", []),
                    "unknowns": structured.get("unknowns", []),
                }
            elif result.agent_name is AgentName.O4_MARKET_TRACE:
                context["market_trace"] = {
                    "quote_context": structured.get("quote_context", {}),
                    "relative_performance": structured.get("relative_performance", []),
                    "technical_signals": structured.get("technical_signals", {}),
                    "unknowns": structured.get("unknowns", []),
                }
        return context

    def _require_success(self, result: AgentResult) -> AgentResult:
        if result.status is not ResultStatus.SUCCEEDED:
            message = result.error.message if result.error is not None else "unknown error"
            raise WorkflowContractError(
                f"Global Research module failed for {result.agent_name.value}: {message}"
            )
        return result

    def _section(self, result: AgentResult, author: AgentName, label: str) -> ResearchSection:
        structured = self._structured(result)
        evidence_refs = list(result.evidence_refs)
        if not evidence_refs:
            raise WorkflowContractError(f"{label} module output has no evidence refs.")
        summary = str(
            result.payload.get("markdown_summary")
            or structured.get("markdown_summary")
            or f"{label} module output"
        )
        return ResearchSection(
            text=self._section_text(label, structured, summary),
            summary=summary,
            evidence_refs=evidence_refs,
            author_agent=author,
            reviewer_agents=[AgentName.O1_EXPECTATION_OWNER],
        )

    def _pending_market_narrative_section(
        self,
        ticker: str,
        inputs: GlobalResearchInputs,
    ) -> ResearchSection:
        evidence = EvidenceRef(
            evidence_id=new_id("evidence"),
            source_type=EvidenceSourceType.AGENT_OUTPUT,
            source_id=f"workflow:pending_market_narrative:{ticker}",
            title="Pending market narrative integration",
            summary="Market narrative report awaits O1/DoxAtlas integration in a later phase.",
            retrieval_metadata={
                "integration_phase": "3.6",
                "pending_dependency": "O1/DoxAtlas narrative research",
                "market": inputs.market,
            },
            confidence=0.2,
            citation_scope="global_research.market_narrative_report",
        )
        return ResearchSection(
            text=(
                "Pending O1/DoxAtlas narrative integration. This section is a placeholder "
                "and must not be treated as a completed market narrative conclusion."
            ),
            summary="Pending O1/DoxAtlas narrative integration.",
            evidence_refs=[evidence],
            author_agent=AgentName.O1_EXPECTATION_OWNER,
            reviewer_agents=[],
        )

    def _structured(self, result: AgentResult) -> dict[str, Any]:
        structured = result.payload.get("structured")
        if not isinstance(structured, dict):
            raise WorkflowContractError(
                f"Global Research module output is not structured JSON: {result.agent_name.value}"
            )
        return structured

    def _section_text(
        self,
        label: str,
        structured: dict[str, Any],
        summary: str,
    ) -> str:
        return (
            f"{summary}\n\n"
            f"Structured {label} module output:\n"
            f"{json.dumps(structured, ensure_ascii=True, default=str)}"
        )
