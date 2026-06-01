# Phase 3.6 Global Research Module Integration

## Summary

Phase 3.6 connects the migrated C1/C2/C3/O4 capabilities to the initialization
workflow. `BuildGlobalResearch` now runs the Phase 8 modules, stores their raw
outputs in Working Memory, assembles a `GlobalResearchDocument`, and submits the
stable document through `BlackboardService.submit_patch()`.

The workflow runner remains DoxAgent-owned. C1/C2/C3 use deterministic migrated
adapters, and O4 uses the native mock market trace provider by default. Real
provider replacement remains a later concern.

## Key Changes

- Added `GlobalResearchInputs` to carry market/geography/timeframe, industry
  scope, universe, benchmark/peer, and market trace settings.
- Added `GlobalResearchModuleRunner` to call `FundamentalBriefAgentModule`,
  `MacroContextAgentModule`, `IndustryResearchAgentModule`, and
  `MarketTraceAgentModule` without writing Blackboard state.
- Added `GlobalResearchAssembler` to map the four module outputs into
  `GlobalResearchDocument` sections and extract downstream context for O1/O2.
- `market_narrative_report` is explicitly marked as pending O1/DoxAtlas
  integration so it cannot be confused with a completed narrative conclusion.
- `BlackboardInitializationWorkflow.run()` accepts optional `research_inputs`;
  checkpoint metadata stores the resolved inputs for resume/debug use.
- In `execution_mode="agent_runner"`, `BuildGlobalResearch` now uses the module
  runner and assembler instead of relying on one agent to produce the entire
  document patch.

## Boundary Rules

- Phase 8 modules still return `AgentResult` and do not write Blackboard.
- Stable Global Research state is written only through Blackboard Service.
- The assembler requires C1/C2/C3/O4 results and evidence for all non-placeholder
  sections.
- C3 downstream hints, C2 monitoring dashboard, C1 risks/catalysts, and O4
  price/technical context are preserved in checkpoint metadata and Working
  Memory for later O1/O2 phases.

## Validation

Tests cover:

- Running all four migrated modules.
- Assembling a valid five-section `GlobalResearchDocument`.
- Preserving evidence, markdown summaries, skill metadata, unknowns, and
  downstream hints.
- Blocking on missing module outputs or missing evidence.
- Workflow `BuildGlobalResearch` submission through Blackboard.
- Input JSON round trip plus resume without duplicate Global Research commits.

Regression commands:

```powershell
uv run pytest
uv run ruff check .
uv run mypy src
```
