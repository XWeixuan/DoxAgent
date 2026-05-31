# Phase 3.3 Skills Registry and Injection

## Summary

This phase promotes migrated external skill names from adapter metadata into a
DoxAgent-owned, code-first Skill Registry. Skills are queryable, versioned, and
injectable into `AgentTask` through a small `SkillBundle`.

The implementation does not run external runtimes, read
`references/external_agent_sources` at runtime, persist skills to Supabase, or
call real LLM providers. It establishes the boundary needed for a future real
AgentRunner.

## Boundaries

- `SkillRegistry` stores static code-first skill definitions.
- `SkillInjectionPolicy` selects the minimal skill set from agent defaults,
  task-type matches, and explicit `input_context["skill_ids"]`.
- `SkillInjector` returns a copied `AgentTask` with an injected `SkillBundle`.
- `MockAgentRunner` exposes skill ids and versions in payloads for contract
  visibility.
- Context snapshots can include skill summaries, but Blackboard state is not
  modified by skill injection.

## Registered External Skills

- Vibe-Trading macro: `macro-analysis`, `global-macro`, `credit-analysis`,
  `yfinance`, `commodity-analysis`, `seasonal`, `asset-allocation`,
  `risk-analysis`, `hedging-strategy`, `strategy-generate`.
- Vibe-Trading fundamental: `financial-statement`, `fundamental-filter`,
  `valuation-model`, `earnings-forecast`, `web-reader`, `report-generate`.
- financial-services Market Researcher: `market-researcher`,
  `sector-overview`, `competitive-analysis`, `comps-analysis`,
  `idea-generation`, `note-writer`.
- Hermes/O4 migration: `ohlcv-orchestration`, `quote-context`,
  `relative-performance`, `technical-signal-analysis`, `market-data-quality`.

## Non-goals

- No real LLM AgentRunner.
- No MAF runtime integration.
- No Supabase skill persistence.
- No external source tree runtime reads.
- No Blackboard or Commit Log mutation from skill injection.
