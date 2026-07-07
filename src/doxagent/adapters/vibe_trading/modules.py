"""Phase 8 Vibe-Trading adapter modules."""

from collections.abc import Mapping
from typing import Any

from doxagent.adapters.vibe_trading.executor import DeterministicVibeTeamExecutor
from doxagent.adapters.vibe_trading.presets import (
    fundamental_research_team_spec,
    macro_rates_fx_desk_spec,
)
from doxagent.adapters.vibe_trading.results import (
    FundamentalBriefResult,
    MacroContextResult,
    VibeAgentOutput,
)
from doxagent.adapters.vibe_trading.specs import VibeTaskSpec, VibeTeamSpec
from doxagent.models import (
    AgentName,
    AgentResult,
    EvidenceRef,
    EvidenceSourceType,
    ResultStatus,
    new_id,
)
from doxagent.skills import UnknownSkillError, default_skill_registry


class MacroContextAgentModule:
    """DoxAgent wrapper for Vibe-Trading's macro_rates_fx_desk preset."""

    def __init__(self) -> None:
        self._team = macro_rates_fx_desk_spec()
        self._executor = DeterministicVibeTeamExecutor(self._team, _render_macro_task)

    @property
    def team(self) -> VibeTeamSpec:
        return self._team

    def run(
        self,
        goal: str,
        timeframe: str,
        metadata: dict[str, Any] | None = None,
    ) -> AgentResult:
        variables = {"goal": goal, "timeframe": timeframe}
        agent_outputs = self._executor.run(variables)
        by_task = {output.task_id: output for output in agent_outputs}
        macro_allocation = by_task["task-macro-allocation"].structured
        structured = MacroContextResult(
            goal=goal,
            timeframe=timeframe,
            rates=by_task["task-rates"].structured,
            fx=by_task["task-fx"].structured,
            commodity_inflation=by_task["task-commodity-inflation"].structured,
            macro_allocation=macro_allocation,
            risk_scenarios=list(macro_allocation["risk_scenarios"]),
            monitoring_dashboard=list(macro_allocation["monitoring_dashboard"]),
            task_graph=self._executor.task_graph(),
            agent_outputs=agent_outputs,
            markdown_summary=_macro_markdown_summary(goal, timeframe, macro_allocation),
        )
        return _to_agent_result(
            agent_name=AgentName.C2_MACRO_RESEARCH,
            source_preset=self._team.name,
            structured=structured.model_dump(mode="json"),
            markdown_summary=structured.markdown_summary,
            metadata=metadata,
            outputs=agent_outputs,
        )


class FundamentalBriefAgentModule:
    """DoxAgent wrapper for Vibe-Trading's fundamental_research_team preset."""

    def __init__(self) -> None:
        self._team = fundamental_research_team_spec()
        self._executor = DeterministicVibeTeamExecutor(self._team, _render_fundamental_task)

    @property
    def team(self) -> VibeTeamSpec:
        return self._team

    def run(
        self,
        target: str,
        market: str,
        metadata: dict[str, Any] | None = None,
    ) -> AgentResult:
        variables = {"target": target, "market": market}
        agent_outputs = self._executor.run(variables)
        by_task = {output.task_id: output for output in agent_outputs}
        report = by_task["task-report"].structured
        structured = FundamentalBriefResult(
            target=target,
            market=market,
            financial_analysis=by_task["task-financial"].structured,
            valuation=by_task["task-valuation"].structured,
            quality=by_task["task-quality"].structured,
            investment_rating=dict(report["investment_rating"]),
            thesis=list(report["thesis"]),
            risks=list(report["risks"]),
            catalysts=list(report["catalysts"]),
            task_graph=self._executor.task_graph(),
            agent_outputs=agent_outputs,
            markdown_summary=_fundamental_markdown_summary(target, market, report),
        )
        return _to_agent_result(
            agent_name=AgentName.C1_FUNDAMENTAL_RESEARCH,
            source_preset=self._team.name,
            structured=structured.model_dump(mode="json"),
            markdown_summary=structured.markdown_summary,
            metadata=metadata,
            outputs=agent_outputs,
        )


def _render_macro_task(
    team: VibeTeamSpec,
    task: VibeTaskSpec,
    upstream: Mapping[str, VibeAgentOutput],
    variables: dict[str, str],
) -> VibeAgentOutput:
    if task.task_id == "task-rates":
        structured: dict[str, Any] = {
            "rate_regime": "fixture: restrictive but transition-sensitive",
            "us_rates": {
                "fed_path": "Watch market-implied cuts versus sticky inflation.",
                "curve_signal": "2s10s and 3m10Y remain core recession/timing checks.",
                "real_rates": "Real-rate direction is the key cross-asset discount-rate input.",
            },
            "china_rates": {
                "policy_stance": "Track LPR, MLF, RRR, and liquidity operations.",
                "rate_differential": "China-US spread remains a CNY and flow pressure gauge.",
            },
            "asset_implications": {
                "equities": "Earnings-yield gap narrows when 10Y yields rise.",
                "gold": "More sensitive to real-rate direction than nominal yields.",
                "crypto": "Generally benefits from falling real rates and weaker USD liquidity.",
                "em_assets": "Vulnerable when US yields and USD both rise.",
            },
        }
        markdown = "- Rates: restrictive baseline; watch curve steepening and real-rate relief."
    elif task.task_id == "task-fx":
        structured = {
            "usd_assessment": {
                "dxy": "Use DXY trend plus dollar-smile attribution.",
                "positioning": "Monitor CFTC extremes for crowded USD exposure.",
            },
            "cny_hkd": {
                "usdcny": "Assess onshore/offshore spread and PBOC fixing bias.",
                "hkd_peg": "Track USD/HKD location in the 7.75-7.85 band and HIBOR pressure.",
            },
            "portfolio_implications": {
                "hedging": "Hedge USD strength risk where revenues or costs are mismatched.",
                "crypto": "USD strength remains a headwind for BTC-like liquidity exposure.",
                "em_fx": "Screen for rate-differential and current-account vulnerability.",
            },
        }
        markdown = "- FX: USD regime and CNY/HKD pressure are key portfolio risk channels."
    elif task.task_id == "task-commodity-inflation":
        structured = {
            "energy": {
                "oil": "Track OPEC stance, US production, and demand surprises.",
                "gas": "Seasonal storage levels drive tactical inflation pressure.",
            },
            "metals": {
                "gold": "Real rates and central bank buying dominate the signal.",
                "copper": "Treat copper as a China demand and global growth proxy.",
            },
            "inflation_allocation": {
                "rising_inflation": "Favor commodities, TIPS, and real assets.",
                "falling_inflation": "Favor growth equities and long-duration bonds.",
                "stagflation": "Favor gold, cash, and defensive equities.",
            },
        }
        markdown = (
            "- Commodities/inflation: energy and real-rate sensitive metals set hedge demand."
        )
    elif task.task_id == "task-macro-allocation":
        structured = {
            "macro_regime": "fixture: late-cycle disinflation watch",
            "allocation": [
                {
                    "asset_class": "Equities",
                    "weight": "neutral",
                    "rationale": "Await rate relief.",
                },
                {
                    "asset_class": "Fixed income",
                    "weight": "modest overweight",
                    "rationale": "Curve optionality.",
                },
                {
                    "asset_class": "Commodities",
                    "weight": "selective",
                    "rationale": "Gold as hedge.",
                },
                {
                    "asset_class": "Crypto",
                    "weight": "risk-budgeted",
                    "rationale": "USD and real-rate beta.",
                },
                {"asset_class": "Cash", "weight": "buffer", "rationale": "Event-risk dry powder."},
            ],
            "key_trades": [
                "Long duration if real yields break lower.",
                "Keep USD hedges for EM and crypto-sensitive exposure.",
                "Hold gold hedge against stagflation or geopolitical shocks.",
            ],
            "risk_scenarios": [
                {"case": "bull", "probability": "25%", "action": "Increase growth beta."},
                {"case": "base", "probability": "50%", "action": "Maintain balanced risk."},
                {"case": "bear", "probability": "20%", "action": "Raise cash and quality."},
                {"case": "tail", "probability": "5%", "action": "Use gold/USD hedges."},
            ],
            "monitoring_dashboard": [
                {
                    "indicator": "US 10Y real yield",
                    "threshold": "trend break",
                    "action": "Reprice duration and growth.",
                },
                {"indicator": "DXY", "threshold": "sharp breakout", "action": "Review FX hedges."},
                {
                    "indicator": "USDCNH spread",
                    "threshold": "persistent widening",
                    "action": "Reduce China beta.",
                },
                {
                    "indicator": "WTI/Brent",
                    "threshold": "supply shock",
                    "action": "Raise inflation hedge.",
                },
                {
                    "indicator": "HY OAS",
                    "threshold": "credit widening",
                    "action": "Cut cyclical risk.",
                },
            ],
            "upstream_inputs": sorted(upstream),
        }
        markdown = "- Macro PM: balanced allocation with duration optionality and USD/gold hedges."
    else:
        raise ValueError(f"Unsupported macro task: {task.task_id}")

    return _agent_output(team, task, upstream, variables, structured, markdown)


def _render_fundamental_task(
    team: VibeTeamSpec,
    task: VibeTaskSpec,
    upstream: Mapping[str, VibeAgentOutput],
    variables: dict[str, str],
) -> VibeAgentOutput:
    target = variables["target"]
    market = variables["market"]

    if task.task_id == "task-financial":
        structured: dict[str, Any] = {
            "financial_health_score": 7,
            "earnings_quality": "moderate-to-high quality fixture assessment",
            "income_statement": {
                "revenue_quality": "Separate recurring core revenue from one-off items.",
                "margins": "Track gross and net margin trend versus peers.",
                "expenses": "Watch SG&A discipline and R&D intensity.",
            },
            "balance_sheet": {
                "asset_quality": "Monitor receivable days, inventory turnover, and goodwill.",
                "liabilities": "Assess interest-bearing debt and maturity matching.",
            },
            "cash_flow": {
                "operating_cash_flow": "Compare OCF with net income for earnings management risk.",
                "free_cash_flow": "Use FCF margin and FCF yield as quality cross-checks.",
            },
            "risks": [
                {"risk": "cash conversion deterioration", "severity": "medium"},
                {"risk": "margin compression", "severity": "medium"},
            ],
        }
        markdown = f"- Financials: {target} shows fixture-level solid but monitorable quality."
    elif task.task_id == "task-valuation":
        structured = {
            "valuation_conclusion": "fair value with selective upside",
            "dcf": {
                "wacc": "fixture assumption",
                "terminal_growth": "fixture assumption",
                "range": {"bear": "below spot", "base": "near spot", "bull": "above spot"},
            },
            "relative_valuation": {
                "multiples": ["P/E", "P/B", "EV/EBITDA", "EV/Sales"],
                "peer_view": "Compare premium/discount to growth and ROIC spread.",
            },
            "historical_percentile": "Use 5-year percentile against changed fundamentals.",
            "target_price": {"horizon": "12 months", "range": "fixture target range"},
            "catalysts": [
                {"type": "positive", "event": "earnings revision"},
                {"type": "negative", "event": "multiple compression"},
            ],
        }
        markdown = f"- Valuation: {target} is treated as fair with catalyst-dependent upside."
    elif task.task_id == "task-quality":
        structured = {
            "moat_rating": "moderate",
            "moat_scores": {
                "brand": 3,
                "network_effects": 2,
                "cost_advantage": 3,
                "switching_costs": 2,
                "licenses_resources": 2,
            },
            "management_quality_score": 7,
            "competitive_landscape": {
                "market_position": "Established participant in the fixture market.",
                "share_trend": "Stable to improving.",
                "competitive_threats": ["price competition", "technology disruption"],
            },
            "long_term_viability": "medium-term to long-term hold candidate if moat holds.",
        }
        markdown = f"- Quality: {target} has a moderate moat and acceptable management quality."
    elif task.task_id == "task-report":
        structured = {
            "investment_rating": {
                "rating": "Hold / selective accumulate",
                "target_price": "fixture 12-month target range",
                "market": market,
            },
            "thesis": [
                "Financial quality is acceptable but requires cash conversion monitoring.",
                "Valuation appears fair, so upside depends on positive catalysts.",
                "Moat is moderate rather than dominant, limiting rating aggressiveness.",
            ],
            "financial_quality_summary": upstream["task-financial"].structured["earnings_quality"],
            "valuation_summary": upstream["task-valuation"].structured["valuation_conclusion"],
            "moat_growth_summary": upstream["task-quality"].structured["moat_rating"],
            "risks": [
                {"risk": "earnings quality reversal", "severity": "medium"},
                {"risk": "valuation multiple compression", "severity": "medium"},
                {"risk": "moat erosion", "severity": "medium"},
            ],
            "catalysts": [
                {"window": "3-6 months", "event": "earnings revisions"},
                {"window": "6-12 months", "event": "margin recovery or moat validation"},
            ],
            "upstream_inputs": sorted(upstream),
        }
        markdown = f"- Report: {target} ({market}) receives a cautious fixture rating."
    else:
        raise ValueError(f"Unsupported fundamental task: {task.task_id}")

    return _agent_output(team, task, upstream, variables, structured, markdown)


def _agent_output(
    team: VibeTeamSpec,
    task: VibeTaskSpec,
    upstream: Mapping[str, VibeAgentOutput],
    variables: dict[str, str],
    structured: dict[str, Any],
    markdown: str,
) -> VibeAgentOutput:
    agent = team.agent(task.agent_id)
    return VibeAgentOutput(
        task_id=task.task_id,
        agent_id=task.agent_id,
        role=agent.role,
        prompt_template=task.prompt_template.format(**variables),
        tools=agent.tools,
        skills=agent.skills,
        skill_versions=_skill_versions(agent.skills),
        upstream_task_ids=list(upstream),
        structured=structured,
        markdown=markdown,
    )


def _to_agent_result(
    agent_name: AgentName,
    source_preset: str,
    structured: dict[str, Any],
    markdown_summary: str,
    metadata: dict[str, Any] | None,
    outputs: list[VibeAgentOutput],
) -> AgentResult:
    return AgentResult(
        task_id=new_id("task"),
        agent_name=agent_name,
        status=ResultStatus.SUCCEEDED,
        payload={
            "adapter": "vibe_trading",
            "source_preset": source_preset,
            "structured": structured,
            "markdown_summary": markdown_summary,
            "metadata": metadata or {},
        },
        evidence_refs=_evidence_refs(source_preset, outputs),
    )


def _evidence_refs(source_preset: str, outputs: list[VibeAgentOutput]) -> list[EvidenceRef]:
    return [
        EvidenceRef(
            evidence_id=new_id("evidence"),
            source_type=EvidenceSourceType.AGENT_OUTPUT,
            source_id=f"vibe_trading:{source_preset}:{output.task_id}",
            title=f"{output.role} adapter output",
            summary=output.markdown,
            retrieval_metadata={
                "adapter": "vibe_trading",
                "source_project": "HKUDS/Vibe-Trading",
                "source_preset": source_preset,
                "agent_id": output.agent_id,
                "task_id": output.task_id,
                "skill_versions": output.skill_versions,
                "mock_fixture": True,
            },
            confidence=0.65,
            citation_scope="phase8_vibe_adapter_agent_output",
        )
        for output in outputs
    ]


def _macro_markdown_summary(
    goal: str,
    timeframe: str,
    macro_allocation: dict[str, Any],
) -> str:
    return (
        f"Macro context for {goal} over {timeframe}: "
        f"{macro_allocation['macro_regime']}. "
        "Use the monitoring dashboard before changing allocation."
    )


def _fundamental_markdown_summary(
    target: str,
    market: str,
    report: dict[str, Any],
) -> str:
    rating = report["investment_rating"]["rating"]
    return (
        f"Fundamental brief for {target} in {market}: {rating}. "
        "Financial quality, valuation, and moat outputs are preserved as separate inputs."
    )


def _skill_versions(skill_ids: list[str]) -> dict[str, str]:
    registry = default_skill_registry()
    versions: dict[str, str] = {}
    for skill_id in skill_ids:
        try:
            versions[skill_id] = registry.get(skill_id).version
        except UnknownSkillError:
            continue
    return versions
