# Phase 8 Financial Services Industry Research Adapter Notes

This phase adapts the `anthropics/financial-services` Market Researcher into a
DoxAgent industry research module. The implementation is intentionally
mock-first and keeps the external project as a read-only reference.

## Boundary

- DoxAgent does not import Anthropic Managed Agent or Claude plugin runtime.
- DoxAgent does not read `references/external_agent_sources` at runtime.
- CapIQ and FactSet MCP connectors are represented as future data-provider
  targets, not executed services.
- The adapter does not write Blackboard state, generate patches, distribute
  research, or produce Word, PowerPoint, or spreadsheet files.

## Workflow

`IndustryResearchAgentModule` exposes the migrated Market Researcher as a
standard DoxAgent `AgentResult` for `C3`. The internal deterministic workflow is:

1. `market-researcher` scopes the sector/theme, angle, universe, and key metrics.
2. `sector-overview` builds market size, growth, structure, value chain, drivers,
   and why-now claims.
3. `competitive-analysis` maps players, positioning, recent moves, moats, and
   vulnerabilities.
4. `comps-analysis` spreads peer operating metrics and valuation multiples with
   consistent definitions and outlier flags.
5. `idea-generation` creates a three-to-five name shortlist.
6. `note-writer` assembles JSON and concise Markdown only.

## Source Discipline

The Phase 8 fixture provider returns mock source refs, sourced claims, peer comps,
idea candidates, and unknowns. Every market-size, growth, valuation multiple,
peer metric, and idea candidate must have `source_refs` and `confidence`, or the
missing part must remain in `unknowns`.

`unknowns` is the explicit holding area for unverified TAM, stale/unknown fiscal
periods, incomparable peers, or disconnected real data providers. These fields
must not be treated as stable business facts until a later DoxAtlas, CapIQ, or
FactSet provider validates them.

## Future Replacement

Later phases can replace `MockIndustryResearchDataProvider` with real DoxAtlas,
CapIQ, FactSet, or filing-backed providers while preserving the
`IndustryResearchAgentModule.run(...) -> AgentResult` contract.
