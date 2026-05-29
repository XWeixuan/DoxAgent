# DoxAgent

DoxAgent is a message-side equity research agent system. The first development
phase builds only the project baseline and the scaffolding needed for the later
Blackboard initialization workflow.

## Phase 0 Scope

Phase 0 establishes the Python project structure, dependency configuration,
baseline documentation, and empty validation tests. It does not implement
Blackboard state management, agent execution, workflow orchestration, external
GitHub agent adapters, DoxAtlas integration, market data calls, fact-checking
search, monitoring, or trading.

## Local Setup

This project uses Python 3.11 managed by `uv`. A system Python installation is
not required.

On PowerShell, use a workspace-local uv cache if the global uv cache is broken:

```powershell
$env:UV_CACHE_DIR = "$PWD\.uv-cache"
```

Install Python and sync dependencies:

```powershell
uv python install 3.11
uv sync --group dev
```

Run the baseline checks:

```powershell
uv run pytest
uv run ruff check .
uv run mypy src
```

## Architecture Boundaries

- Microsoft Agent Framework is the future runtime and workflow shell, not the
  model gateway or business state owner.
- Phase 0 uses the `agent-framework-core` package instead of the all-in-one
  `agent-framework` meta package to avoid optional Azure integrations.
- Model Gateway will own provider routing, retries, structured output, and
  LangSmith wrapping.
- Blackboard Service will own Working Memory, Belief State, Objection,
  Delegation, Evidence, and Commit Log.
- External GitHub agent projects remain references only until the adapter phase.
- DoxAtlas, market data, and fact-checking are mock/fixture based in Phase 0.

## Phase 1 Contracts

Core domain contracts live under `src/doxagent/models`. Phase 1 defines
Pydantic schemas for `AgentTask`, `AgentResult`, `BlackboardPatch`,
`EvidenceRef`, `Objection`, `Delegation`, and the five Blackboard work
documents from the PRD. These contracts are serialization-friendly and do not
import Microsoft Agent Framework types.

Phase 1 still does not implement Blackboard persistence, MAF runners, workflows,
tools, model calls, or external adapters.

## Phase 2 Model Gateway

Model Gateway code lives under `src/doxagent/gateway`. It provides an async
`ModelClient` boundary, mock client, OpenAI and Anthropic SDK adapters,
centralized LangSmith wrapping, normalized errors, fallback handling, and audit
summaries. Phase 2 tests use fake SDK clients only and do not call real model
providers.

## Phase 3 Blackboard Service

Blackboard Service code lives under `src/doxagent/blackboard`. Phase 3 provides
an in-memory repository, run initialization, Working Memory writes, Belief State
patch submission, obstruction checks for unresolved objections and active
delegations, lifecycle helpers, and Commit Log entries. It does not add
database persistence, workflow execution, agent runtime behavior, or tool calls.

## Phase 4 Agent Runtime Boundary

Agent runtime code lives under `src/doxagent/agents`, context snapshots live
under `src/doxagent/context`, and controlled mock tools live under
`src/doxagent/tools`. Phase 4 establishes the `AgentTask -> AgentResult`
boundary, default agent registry, permission-bounded Context Builder, mock tool
registry, and `ToolResult` to `EvidenceRef` conversion. It does not run real MAF
agents, real model calls, external DoxAtlas calls, market-data calls, or
workflow orchestration.

## Phase 5 Initialization Workflow

Initialization workflow code lives under `src/doxagent/workflows`. Phase 5 adds
a deterministic Blackboard initialization runner, in-memory checkpoint/resume,
mock agent result factory, document dependency checks, obstruction handling, and
five-document Belief State promotion through `BlackboardService.submit_patch`.
It still does not call real MAF workflows, model providers, DoxAtlas, market
data, fact-check services, or external GitHub agent projects.

## Phase 6 Mock Ticker Sample

Phase 6 sample inputs and generated output live under
`examples/phase6_mock_ticker`. The runnable module
`doxagent.examples.phase6_mock_run` executes the Phase 5 workflow for the mock
fixture and exports a review JSON containing five documents, evidence, Working
Memory, Commit Log, objection/delegation lifecycle summaries, and residual risk
notes.

Generate the review artifact:

```powershell
uv run python -m doxagent.examples.phase6_mock_run --output examples/phase6_mock_ticker/generated_run.json
```

Run without `--output` to print a compact summary. The sample is fixture-only:
it does not call real services, execute trades, expose broker behavior, or start
real-time monitoring.

## Phase 7 Audit and Recovery

Audit helpers live under `src/doxagent/audit`. Phase 7 adds a read-only
`AuditQueryService` for Commit Log queries, field traceability, unresolved
objection reports, and blocking delegation reports. It also adds
`build_run_debug_report` for workflow/debug summaries and same-process recovery
tests around blocked checkpoints, dependency violations, failed agent results,
and missing evidence.

The Phase 7 audit layer is in-memory and read-only. It does not replace
Blackboard Commit Log, does not persist runs to disk or a database, and does not
turn LangSmith/model traces into business audit records.

## Phase 8 Vibe-Trading Adapters

Vibe-Trading adapter modules live under `src/doxagent/adapters/vibe_trading`.
Phase 8 starts with two read-only reference migrations:
`MacroContextAgentModule` wraps `macro_rates_fx_desk`, and
`FundamentalBriefAgentModule` wraps `fundamental_research_team`.

Both modules preserve the original multi-agent role split, task dependencies,
tool/skill metadata, and synthesis shape, then return standard `AgentResult`
objects with dedicated structured payload schemas and short Markdown summaries.
They do not import the Vibe-Trading runtime, read ignored reference sources at
runtime, execute Vibe tools, call real model/data services, or write Blackboard
state directly.

Example:

```python
from doxagent.adapters import FundamentalBriefAgentModule, MacroContextAgentModule

macro = MacroContextAgentModule().run(
    goal="US equity allocation",
    timeframe="tactical 1-3 months",
)
fundamental = FundamentalBriefAgentModule().run(
    target="AAPL",
    market="US equities",
)
```

Financial-services adapter modules live under
`src/doxagent/adapters/financial_services`. `IndustryResearchAgentModule`
adapts the `anthropics/financial-services` Market Researcher into a DoxAgent
industry research capability. It preserves the source workflow shape: scope,
sector overview, competitive analysis, comps analysis, idea generation, and
note synthesis.

The Phase 8 industry module uses a DoxAgent-owned mock data provider. It returns
JSON plus concise Markdown, with source refs, confidence, and unknowns preserved
for market-size, growth, peer comps, idea shortlist, risks, catalysts, and
downstream hints. It does not run Anthropic Managed Agent, Claude plugin,
CapIQ/FactSet MCP, real DoxAtlas, real market data, or Blackboard writes.

Example:

```python
from doxagent.adapters import IndustryResearchAgentModule

industry = IndustryResearchAgentModule().run(
    sector_or_theme="US data-center power",
    angle="supply gap",
    universe=["VST", "CEG", "ETR", "NRG"],
)
```

## Phase 8 O4 Market Trace

The native O4 market trace module lives under `src/doxagent/agents/market_trace`.
`MarketTraceAgentModule` adapts the useful OHLCV and quote orchestration ideas
from `hermes-finance` without importing Hermes runtime, LlamaIndex tools, or
Hermes cache/rate limiter code.

The module returns a standard `AgentResult` for `O4` with a `MarketTraceResult`
payload covering quote context, OHLCV summary, benchmark/peer relative
performance, volume analysis, technical signals, valuation context, data
quality, source refs, unknowns, and concise Markdown. It does not write
Blackboard state, execute trades, start monitoring, or provide trading advice.

Example:

```python
from doxagent.agents import MarketTraceAgentModule

trace = MarketTraceAgentModule().run(
    ticker="AAPL",
    period="1y",
    interval="1d",
    benchmarks=["SPY"],
    peers=["MSFT", "GOOGL"],
)
```

## Project Layout

```text
src/doxagent/
  adapters/
  audit/
  agents/
  blackboard/
  context/
  core/
  gateway/
  models/
  tools/
  workflows/
tests/
dev_plan/
examples/
references/
```
