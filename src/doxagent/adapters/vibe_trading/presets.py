"""DoxAgent-owned copies of the first Vibe-Trading team specifications."""

from doxagent.adapters.vibe_trading.specs import (
    VibeAgentSpec,
    VibeTaskSpec,
    VibeTeamSpec,
    VibeVariableSpec,
)


def macro_rates_fx_desk_spec() -> VibeTeamSpec:
    return VibeTeamSpec(
        name="macro_rates_fx_desk",
        title="Macro / Rates / FX Desk",
        description=(
            "Cross-asset macro desk with global rates analysis, FX strategy, "
            "commodity/inflation analysis, and macro portfolio synthesis."
        ),
        agents=[
            VibeAgentSpec(
                agent_id="rates_analyst",
                role="Global Rates & Yield Curve Analyst",
                system_prompt=(
                    "Analyze the global rates environment and yield curve signals for {goal} "
                    "over {timeframe}. Preserve the Vibe-Trading rates workflow: US rates, "
                    "China rates, ECB/BOJ/BOE policy, 2s10s and 3m10Y curve signals, MOVE "
                    "and credit spreads, then translate rate expectations into equity, gold, "
                    "crypto, and EM asset implications."
                ),
                tools=["bash", "read_file", "write_file", "load_skill", "read_url"],
                skills=["macro-analysis", "global-macro", "credit-analysis"],
            ),
            VibeAgentSpec(
                agent_id="fx_strategist",
                role="FX Strategist",
                system_prompt=(
                    "Analyze the FX landscape for {goal} over {timeframe}. Preserve the "
                    "Vibe-Trading FX workflow: DXY and dollar-smile assessment, USD/CNY and "
                    "USDCNH spread, PBOC fix behavior, HKD peg dynamics, EUR/USD, USD/JPY, "
                    "crypto as short-USD exposure, hedging needs, carry trades, and EM FX risk."
                ),
                tools=["bash", "read_file", "write_file", "load_skill", "read_url"],
                skills=["global-macro", "macro-analysis", "yfinance"],
            ),
            VibeAgentSpec(
                agent_id="commodity_inflation_analyst",
                role="Commodity & Inflation Analyst",
                system_prompt=(
                    "Analyze commodity and inflation dynamics for {goal} over {timeframe}. "
                    "Preserve the Vibe-Trading framework: energy supply-demand and OPEC, "
                    "natural gas seasonality, gold real-rate sensitivity, copper as growth "
                    "proxy, US CPI/PCE, China CPI/PPI, food prices, and inflation-to-allocation "
                    "implications across commodities, TIPS, growth equities, bonds, and gold."
                ),
                tools=["bash", "read_file", "write_file", "load_skill", "read_url"],
                skills=["commodity-analysis", "global-macro", "seasonal"],
            ),
            VibeAgentSpec(
                agent_id="macro_pm",
                role="Macro Portfolio Manager",
                system_prompt=(
                    "Synthesize rates, FX, and commodity/inflation outputs into a macro-driven "
                    "cross-asset allocation. Preserve the Vibe-Trading synthesis: 2x2 macro "
                    "regime, asset class weights, key macro trades, duration stance, FX hedging, "
                    "commodity positioning, bull/base/bear/tail scenarios, and monitoring "
                    "thresholds."
                ),
                tools=["bash", "read_file", "write_file", "load_skill", "backtest"],
                skills=[
                    "asset-allocation",
                    "risk-analysis",
                    "hedging-strategy",
                    "strategy-generate",
                ],
            ),
        ],
        tasks=[
            VibeTaskSpec(
                task_id="task-rates",
                agent_id="rates_analyst",
                prompt_template=(
                    "Analyze the global rates environment: Fed path, yield curve signals, "
                    "China-US rate differential, and cross-asset rate implications. Goal: "
                    "{goal}. Horizon: {timeframe}."
                ),
            ),
            VibeTaskSpec(
                task_id="task-fx",
                agent_id="fx_strategist",
                prompt_template=(
                    "Analyze the FX landscape: DXY, USD/CNY, HKD peg, major crosses, and "
                    "portfolio hedging implications. Goal: {goal}. Horizon: {timeframe}."
                ),
            ),
            VibeTaskSpec(
                task_id="task-commodity-inflation",
                agent_id="commodity_inflation_analyst",
                prompt_template=(
                    "Analyze commodity and inflation dynamics: energy, metals, inflation "
                    "indicators, and asset allocation implications. Goal: {goal}. Horizon: "
                    "{timeframe}."
                ),
            ),
            VibeTaskSpec(
                task_id="task-macro-allocation",
                agent_id="macro_pm",
                prompt_template=(
                    "Synthesize rates, FX, and commodity/inflation analyses into a macro-driven "
                    "cross-asset allocation recommendation. Goal: {goal}. Horizon: {timeframe}."
                ),
                depends_on=["task-rates", "task-fx", "task-commodity-inflation"],
                input_from={
                    "rates": "task-rates",
                    "fx": "task-fx",
                    "commodity_inflation": "task-commodity-inflation",
                },
            ),
        ],
        variables=[
            VibeVariableSpec(
                name="goal",
                description="Macro investment objective.",
            ),
            VibeVariableSpec(
                name="timeframe",
                description="Investment horizon.",
            ),
        ],
    )


def fundamental_research_team_spec() -> VibeTeamSpec:
    return VibeTeamSpec(
        name="fundamental_research_team",
        title="Fundamental Deep Research Team",
        description=(
            "Financial, valuation, and quality analysis in parallel, followed by a research "
            "editor synthesis into a buy-side deep research report."
        ),
        agents=[
            VibeAgentSpec(
                agent_id="financial_analyst",
                role="Financial Analyst",
                system_prompt=(
                    "Conduct comprehensive financial statement analysis of {target} in the "
                    "{market} market. Preserve the Vibe-Trading financial workflow: income "
                    "statement quality, margin trends, expense ratios, earnings quality, asset "
                    "quality, debt service, equity changes, operating cash flow, capex, free "
                    "cash flow, financing reliance, peer comparison, and financial risk warnings."
                ),
                tools=["bash", "read_file", "write_file", "load_skill", "factor_analysis"],
                skills=["financial-statement", "fundamental-filter"],
            ),
            VibeAgentSpec(
                agent_id="valuation_analyst",
                role="Valuation Analyst",
                system_prompt=(
                    "Conduct comprehensive valuation analysis of {target} in the {market} "
                    "market. Preserve the Vibe-Trading valuation workflow: DCF, DDM when "
                    "relevant, comparable companies, historical percentile, PEG, asset-based "
                    "approaches, industry-specific multiples, target price, valuation range, "
                    "margin of safety, and re-rating catalysts."
                ),
                tools=["bash", "read_file", "write_file", "load_skill", "factor_analysis"],
                skills=["valuation-model", "earnings-forecast"],
            ),
            VibeAgentSpec(
                agent_id="quality_analyst",
                role="Quality Analyst",
                system_prompt=(
                    "Conduct business quality assessment of {target} in the {market} market. "
                    "Preserve the Vibe-Trading quality workflow: five moat types, moat "
                    "durability, competitive threats, management capital allocation, execution, "
                    "shareholder alignment, integrity, industry concentration, share trend, "
                    "price-war risk, TAM, and long-term holding viability."
                ),
                tools=["bash", "read_file", "write_file", "load_skill", "read_url"],
                skills=["fundamental-filter", "web-reader"],
            ),
            VibeAgentSpec(
                agent_id="report_editor",
                role="Research Report Editor",
                system_prompt=(
                    "Synthesize financial, valuation, and quality outputs into a complete deep "
                    "investment research report for {target} in the {market} market. Preserve "
                    "the Vibe-Trading report editor workflow: consistency checks, priority "
                    "ranking, rating logic, target price, thesis, financial quality summary, "
                    "valuation basis, moat and growth summary, ranked risks, and catalysts."
                ),
                tools=["bash", "read_file", "write_file", "load_skill"],
                skills=["report-generate"],
            ),
        ],
        tasks=[
            VibeTaskSpec(
                task_id="task-financial",
                agent_id="financial_analyst",
                prompt_template=(
                    "Conduct deep financial statement analysis of {target}, focusing on "
                    "financial quality, profitability, cash flow health, and debt risk."
                ),
            ),
            VibeTaskSpec(
                task_id="task-valuation",
                agent_id="valuation_analyst",
                prompt_template=(
                    "Conduct multi-method cross-validated valuation of {target}, including "
                    "DCF, comparable company method, and historical valuation method. Provide "
                    "a target price range."
                ),
            ),
            VibeTaskSpec(
                task_id="task-quality",
                agent_id="quality_analyst",
                prompt_template=(
                    "Conduct moat assessment, management quality analysis, and competitive "
                    "landscape research on {target}."
                ),
            ),
            VibeTaskSpec(
                task_id="task-report",
                agent_id="report_editor",
                prompt_template=(
                    "Synthesize the research outputs from all three analysts to produce a "
                    "complete deep research report on {target}, with a clear investment rating "
                    "and target price."
                ),
                depends_on=["task-financial", "task-valuation", "task-quality"],
                input_from={
                    "financial": "task-financial",
                    "valuation": "task-valuation",
                    "quality": "task-quality",
                },
            ),
        ],
        variables=[
            VibeVariableSpec(
                name="target",
                description="Research subject.",
            ),
            VibeVariableSpec(
                name="market",
                description="Market.",
            ),
        ],
    )
