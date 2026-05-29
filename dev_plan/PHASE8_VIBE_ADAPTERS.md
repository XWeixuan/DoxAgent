# Phase 8 Vibe-Trading Adapter Notes

Phase 8 starts external-agent migration after the DoxAgent contracts,
Blackboard Service, workflow, sample, audit, and recovery layers are stable.
This first slice adapts two `HKUDS/Vibe-Trading` multi-agent research teams:
`macro_rates_fx_desk` and `fundamental_research_team`.

## Boundary

- DoxAgent does not import the Vibe-Trading runtime.
- DoxAgent does not read `references/external_agent_sources` at runtime.
- The source presets are normalized into DoxAgent-owned specs under the adapter
  package so the project remains testable after reference sources are ignored by
  git.
- Vibe tool and skill names are preserved as metadata only. The adapter does
  not execute shell, file, URL, backtest, or factor-analysis tools.
- Stable Blackboard writes remain outside this adapter. The modules return
  `AgentResult` objects with structured payloads and evidence refs only.

## MacroContextAgentModule

`MacroContextAgentModule` wraps the `macro_rates_fx_desk` preset. It preserves
four roles and the original two-layer DAG:

- `rates_analyst`
- `fx_strategist`
- `commodity_inflation_analyst`
- `macro_pm`, dependent on the first three outputs

The module input is `goal`, `timeframe`, and optional metadata. The output is a
standard `AgentResult` for `C2` with a `MacroContextResult` payload containing
rates, FX, commodity/inflation, macro allocation, risk scenarios, monitoring
dashboard, task graph, agent outputs, and a short Markdown summary.

## FundamentalBriefAgentModule

`FundamentalBriefAgentModule` wraps the `fundamental_research_team` preset. It
preserves four roles and the original two-layer DAG:

- `financial_analyst`
- `valuation_analyst`
- `quality_analyst`
- `report_editor`, dependent on the first three outputs

The module input is `target`, `market`, and optional metadata. The output is a
standard `AgentResult` for `C1` with a `FundamentalBriefResult` payload
containing financial analysis, valuation, quality/moat, investment rating,
thesis, risks, catalysts, task graph, agent outputs, and a short Markdown
summary.

## Non-goals

This phase does not call real LLMs, DoxAtlas, market data, fact-check services,
or the original Vibe worker runtime. It also does not migrate other external
agent teams. Later phases can replace the deterministic renderer with
ModelGateway-backed execution while preserving these module contracts.
