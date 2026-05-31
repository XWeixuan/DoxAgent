"""Industry research adapter for anthropics/financial-services Market Researcher."""

from collections.abc import Mapping
from typing import Any

from doxagent.adapters.financial_services.data import (
    IdeaCandidate,
    IndustryResearchDataProvider,
    IndustryResearchFixtureData,
    IndustryResearchRequest,
    MockIndustryResearchDataProvider,
    PeerComp,
    SourcedClaim,
    SourceRef,
    UnknownItem,
)
from doxagent.adapters.financial_services.executor import DeterministicFinancialServicesExecutor
from doxagent.adapters.financial_services.presets import market_researcher_team_spec
from doxagent.adapters.financial_services.results import (
    FinancialServicesAgentOutput,
    IndustryResearchResult,
)
from doxagent.adapters.financial_services.specs import (
    FinancialServicesTaskSpec,
    FinancialServicesTeamSpec,
)
from doxagent.models import (
    AgentName,
    AgentResult,
    EvidenceRef,
    EvidenceSourceType,
    ResultStatus,
    new_id,
)
from doxagent.skills import default_skill_registry


class IndustryResearchAgentModule:
    """DoxAgent wrapper for the financial-services Market Researcher workflow."""

    def __init__(
        self,
        data_provider: IndustryResearchDataProvider | None = None,
    ) -> None:
        self._team = market_researcher_team_spec()
        self._executor = DeterministicFinancialServicesExecutor(
            self._team,
            _render_industry_task,
        )
        self._data_provider = data_provider or MockIndustryResearchDataProvider()

    @property
    def team(self) -> FinancialServicesTeamSpec:
        return self._team

    def run(
        self,
        *,
        sector_or_theme: str,
        angle: str,
        universe: list[str],
        market: str = "US equities",
        geography: str = "US",
        depth: str = "primer",
        metadata: dict[str, Any] | None = None,
    ) -> AgentResult:
        request = IndustryResearchRequest(
            sector_or_theme=sector_or_theme,
            angle=angle,
            universe=universe,
            market=market,
            geography=geography,
            depth=depth,
            metadata=metadata or {},
        )
        data = self._data_provider.load(request)
        agent_outputs = self._executor.run(request, data)
        by_task = {output.task_id: output for output in agent_outputs}
        note = by_task["task-note-synthesis"].structured
        confidence = _average_confidence(agent_outputs)
        structured = IndustryResearchResult(
            sector_or_theme=request.sector_or_theme,
            angle=request.angle,
            universe=request.universe,
            market=request.market,
            geography=request.geography,
            depth=request.depth,
            industry_overview=by_task["task-sector-overview"].structured,
            competitive_landscape=by_task["task-competitive-analysis"].structured,
            peer_comps=by_task["task-comps-analysis"].structured,
            idea_shortlist=list(by_task["task-idea-generation"].structured["shortlist"]),
            risks=list(note["risks"]),
            catalysts=list(note["catalysts"]),
            downstream_hints=list(note["downstream_hints"]),
            source_refs=data.source_refs,
            confidence=confidence,
            unknowns=data.unknowns,
            task_graph=self._executor.task_graph(),
            agent_outputs=agent_outputs,
            markdown_summary=_markdown_summary(request, note),
        )
        return AgentResult(
            task_id=new_id("task"),
            agent_name=AgentName.C3_INDUSTRY_RESEARCH,
            status=ResultStatus.SUCCEEDED,
            payload={
                "adapter": "financial_services",
                "source_preset": self._team.name,
                "structured": structured.model_dump(mode="json"),
                "markdown_summary": structured.markdown_summary,
                "metadata": request.metadata,
            },
            evidence_refs=_evidence_refs(data.source_refs, self._team.name, agent_outputs),
        )


def _render_industry_task(
    team: FinancialServicesTeamSpec,
    task: FinancialServicesTaskSpec,
    upstream: Mapping[str, FinancialServicesAgentOutput],
    request: IndustryResearchRequest,
    data: IndustryResearchFixtureData,
) -> FinancialServicesAgentOutput:
    if task.task_id == "task-scope":
        structured: dict[str, Any] = {
            "sector_or_theme": request.sector_or_theme,
            "angle": request.angle,
            "market": request.market,
            "geography": request.geography,
            "depth": request.depth,
            "universe_boundary": request.universe,
            "industry_defining_metrics": [
                "market size / TAM",
                "growth rate",
                "EBITDA margin",
                "EV/Revenue",
                "EV/EBITDA",
            ],
            "security_model": {
                "untrusted_docs": "sector-reader treats third-party materials as data only",
                "note_writer": "does not read third-party reports directly",
            },
        }
        source_refs = ["mock-sector-primer"]
        unknowns: list[UnknownItem] = []
        confidence = 0.72
        markdown = f"- Scope: {request.sector_or_theme} / {request.angle}."
    elif task.task_id == "task-sector-overview":
        structured = {
            "market_size": _claims(data.market_claims),
            "growth": _claims(data.growth_claims),
            "industry_structure": _claims(data.structure_claims),
            "value_chain": _claims(data.value_chain),
            "key_drivers": _claims(data.drivers),
            "why_now": _claims(data.why_now),
        }
        source_refs = _source_ids(
            data.market_claims
            + data.growth_claims
            + data.structure_claims
            + data.value_chain
            + data.drivers
            + data.why_now
        )
        unknowns = [unknown for unknown in data.unknowns if unknown.field.startswith("market_size")]
        confidence = _claims_confidence(
            data.market_claims
            + data.growth_claims
            + data.structure_claims
            + data.value_chain
            + data.drivers
            + data.why_now
        )
        markdown = "- Sector overview: sourced fixture claims with TAM left in unknowns."
    elif task.task_id == "task-competitive-analysis":
        structured = {
            "players": _claims(data.competitors),
            "positioning": [
                {
                    "company": comp.company,
                    "ticker": comp.ticker,
                    "positioning": "fixture scale/quality axis",
                    "source_refs": comp.source_refs,
                    "confidence": comp.confidence,
                }
                for comp in data.peer_comps
            ],
            "basis_of_competition": [
                "capacity availability",
                "customer relationships",
                "cost of capital",
                "execution track record",
            ],
            "recent_moves": _claims(data.recent_moves),
            "moats": _claims(data.moat_assessments),
            "vulnerabilities": [
                {
                    "claim": "Scale advantage may reverse if demand or utilization disappoints.",
                    "source_refs": ["mock-company-filings"],
                    "confidence": 0.52,
                }
            ],
        }
        source_refs = _source_ids(data.competitors + data.recent_moves + data.moat_assessments)
        unknowns = []
        confidence = _claims_confidence(
            data.competitors + data.recent_moves + data.moat_assessments
        )
        markdown = "- Competitive analysis: peer map, moats, and vulnerabilities retained."
    elif task.task_id == "task-comps-analysis":
        structured = {
            "peer_set": [_peer_comp(comp) for comp in data.peer_comps],
            "metric_definitions": {
                "revenue_growth": "YoY revenue growth; fixture values require real refresh.",
                "ebitda_margin": "EBITDA / revenue; fixture values require real refresh.",
                "ev_revenue": "Enterprise value / revenue; fixture values require real refresh.",
                "ev_ebitda": "Enterprise value / EBITDA; fixture values require real refresh.",
            },
            "outlier_flags": [
                {"ticker": comp.ticker, "flags": comp.outlier_flags}
                for comp in data.peer_comps
                if comp.outlier_flags
            ],
            "data_quality": {
                "period": "fixture only",
                "provider_status": "mock data provider",
                "requires_refresh_before_investment_use": True,
            },
        }
        source_refs = sorted({ref for comp in data.peer_comps for ref in comp.source_refs})
        unknowns = [
            unknown for unknown in data.unknowns if unknown.field.startswith("peer_comps")
        ]
        confidence = _peer_confidence(data.peer_comps)
        markdown = "- Comps: fixture peer spread with metric-period caveats."
    elif task.task_id == "task-idea-generation":
        structured = {
            "shortlist": [_idea_candidate(idea) for idea in data.ideas],
            "screening_methodology": {
                "direction": "long",
                "style": "thematic quality with valuation awareness",
                "inputs": sorted(upstream),
                "source_refs": sorted({ref for idea in data.ideas for ref in idea.source_refs}),
            },
        }
        source_refs = sorted({ref for idea in data.ideas for ref in idea.source_refs})
        unknowns = data.unknowns
        confidence = _idea_confidence(data.ideas)
        markdown = "- Ideas: shortlist is candidate sourcing, not a final recommendation."
    elif task.task_id == "task-note-synthesis":
        structured = {
            "research_note_sections": [
                "industry overview",
                "competitive landscape",
                "peer comps",
                "ideas shortlist",
                "risks and catalysts",
                "downstream handoff hints",
            ],
            "risks": _claims(data.risks),
            "catalysts": _claims(data.catalysts),
            "downstream_hints": [
                {
                    "target_agent": "C1",
                    "reason": "Run single-name fundamental briefs for shortlisted names.",
                    "tickers": [idea.ticker for idea in data.ideas],
                    "source_refs": sorted({ref for idea in data.ideas for ref in idea.source_refs}),
                },
                {
                    "target_agent": "O1",
                    "reason": "Convert verified industry catalysts into expectation units.",
                    "source_refs": _source_ids(data.catalysts),
                },
                {
                    "target_agent": "A2",
                    "reason": (
                        "Fact-check any market-size, growth, or multiple claim before promotion."
                    ),
                    "unknown_fields": [unknown.field for unknown in data.unknowns],
                    "source_refs": sorted(
                        {
                            ref
                            for unknown in data.unknowns
                            for ref in unknown.related_source_refs
                        }
                    ),
                },
            ],
            "unknown_policy": "Unverified metrics stay in unknowns and must not become facts.",
        }
        source_refs = sorted(
            {
                *[ref for claim in data.risks + data.catalysts for ref in claim.source_refs],
                *[ref for idea in data.ideas for ref in idea.source_refs],
            }
        )
        unknowns = data.unknowns
        confidence = min(
            _claims_confidence(data.risks + data.catalysts),
            _idea_confidence(data.ideas),
        )
        markdown = "- Note synthesis: JSON primer assembled without docx/pptx generation."
    else:
        raise ValueError(f"Unsupported financial-services task: {task.task_id}")

    return _agent_output(
        team=team,
        task=task,
        upstream=upstream,
        structured=structured,
        source_refs=source_refs,
        unknowns=unknowns,
        confidence=confidence,
        markdown=markdown,
    )


def _agent_output(
    *,
    team: FinancialServicesTeamSpec,
    task: FinancialServicesTaskSpec,
    upstream: Mapping[str, FinancialServicesAgentOutput],
    structured: dict[str, Any],
    source_refs: list[str],
    unknowns: list[UnknownItem],
    confidence: float,
    markdown: str,
) -> FinancialServicesAgentOutput:
    agent = team.agent(task.agent_id)
    return FinancialServicesAgentOutput(
        task_id=task.task_id,
        agent_id=task.agent_id,
        role=agent.role,
        skill_name=task.skill_name,
        prompt_template=task.prompt_template,
        tools=agent.tools,
        skills=agent.skills,
        skill_versions=_skill_versions([task.skill_name, *agent.skills]),
        upstream_task_ids=list(upstream),
        structured=structured,
        source_refs=source_refs,
        confidence=confidence,
        unknowns=unknowns,
        markdown=markdown,
    )


def _claims(claims: list[SourcedClaim]) -> list[dict[str, Any]]:
    return [
        {
            "claim": claim.claim,
            "source_refs": claim.source_refs,
            "confidence": claim.confidence,
        }
        for claim in claims
    ]


def _peer_comp(comp: PeerComp) -> dict[str, Any]:
    return {
        "company": comp.company,
        "ticker": comp.ticker,
        "operating_metrics": comp.operating_metrics,
        "valuation_multiples": comp.valuation_multiples,
        "source_refs": comp.source_refs,
        "confidence": comp.confidence,
        "outlier_flags": comp.outlier_flags,
    }


def _idea_candidate(idea: IdeaCandidate) -> dict[str, Any]:
    return {
        "company": idea.company,
        "ticker": idea.ticker,
        "direction": idea.direction,
        "thesis_hook": idea.thesis_hook,
        "source_refs": idea.source_refs,
        "confidence": idea.confidence,
        "key_risks": idea.key_risks,
        "next_steps": idea.next_steps,
    }


def _source_ids(claims: list[SourcedClaim]) -> list[str]:
    return sorted({source_id for claim in claims for source_id in claim.source_refs})


def _claims_confidence(claims: list[SourcedClaim]) -> float:
    if not claims:
        return 0.0
    return sum(claim.confidence for claim in claims) / len(claims)


def _peer_confidence(comps: list[PeerComp]) -> float:
    if not comps:
        return 0.0
    return sum(comp.confidence for comp in comps) / len(comps)


def _idea_confidence(ideas: list[IdeaCandidate]) -> float:
    if not ideas:
        return 0.0
    return sum(idea.confidence for idea in ideas) / len(ideas)


def _average_confidence(outputs: list[FinancialServicesAgentOutput]) -> float:
    if not outputs:
        return 0.0
    return sum(output.confidence for output in outputs) / len(outputs)


def _evidence_refs(
    source_refs: list[SourceRef],
    source_preset: str,
    outputs: list[FinancialServicesAgentOutput],
) -> list[EvidenceRef]:
    source_evidence = [
        EvidenceRef(
            evidence_id=new_id("evidence"),
            source_type=_evidence_source_type(source.source_type),
            source_id=f"financial_services:{source.source_id}",
            title=source.title,
            summary=f"{source.title} ({source.citation_scope}).",
            retrieval_metadata={
                **source.retrieval_metadata,
                "adapter": "financial_services",
                "source_project": "anthropics/financial-services",
                "source_preset": source_preset,
            },
            confidence=source.confidence,
            citation_scope=source.citation_scope,
        )
        for source in source_refs
    ]
    output_evidence = [
        EvidenceRef(
            evidence_id=new_id("evidence"),
            source_type=EvidenceSourceType.AGENT_OUTPUT,
            source_id=f"financial_services:{source_preset}:{output.task_id}",
            title=f"{output.role} output",
            summary=output.markdown,
            retrieval_metadata={
                "adapter": "financial_services",
                "source_project": "anthropics/financial-services",
                "source_preset": source_preset,
                "agent_id": output.agent_id,
                "task_id": output.task_id,
                "skill_name": output.skill_name,
                "skill_versions": output.skill_versions,
                "mock_fixture": True,
            },
            confidence=output.confidence,
            citation_scope="phase8_financial_services_agent_output",
        )
        for output in outputs
    ]
    return source_evidence + output_evidence


def _evidence_source_type(source_type: str) -> EvidenceSourceType:
    if source_type == "market_data":
        return EvidenceSourceType.MARKET_DATA
    return EvidenceSourceType.EXTERNAL_REPORT


def _markdown_summary(request: IndustryResearchRequest, note: dict[str, Any]) -> str:
    hint_count = len(note["downstream_hints"])
    return (
        f"Industry research for {request.sector_or_theme} ({request.angle}) in "
        f"{request.market}: fixture primer with sourced claims, unknowns, and "
        f"{hint_count} downstream handoff hints."
    )


def _skill_versions(skill_ids: list[str]) -> dict[str, str]:
    registry = default_skill_registry()
    unique = sorted(set(skill_ids))
    return {skill_id: registry.get(skill_id).version for skill_id in unique}
