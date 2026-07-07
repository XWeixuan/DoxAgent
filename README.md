# DoxAgent

DoxAgent is a message-side equity research agent system. The first development
phase builds only the project baseline and the scaffolding needed for the later
Blackboard initialization workflow.

启动测试用blackboard初始化结果展示的命令：
.\scripts\debug-viewer.cmd 8765
If 8765 is busy, open the URL printed by the launcher.
Remote tunnel only; do not keep this running while testing the local viewer on 8765:
ssh -N -L 8765:127.0.0.1:8765 doxagent-hk
.\scripts\debug-viewer.cmd
网页：
http://127.0.0.1:8765
http://127.0.0.1:8765/langsmith-renderer.html
---
监测管线
.\scripts\monitoring-viewer.cmd 8766
http://127.0.0.1:8766
---
启动测试：
$env:DOXAGENT_RUN_REAL_API_TESTS="1"
uv run pytest -m real_api tests/test_phase17_real_initialization_smoke.py

前端启动方式：
uv run python -m doxagent.dashboard_api --host 127.0.0.1 --port 8780
cd frontend/dashboard
npm run dev
http://localhost:5173/

前后端build：
ssh doxagent-hk 'cd /root/doxagent && docker compose build dashboard runtime-scheduler'
ssh doxagent-hk 'cd /root/doxagent && docker compose up -d --force-recreate dashboard runtime-scheduler && docker compose stop monitoring-poller || true'
ssh doxagent-hk 'cd /root/doxagent && docker compose restart dashboard runtime-scheduler'
ssh doxagent-hk 'cd /root/doxagent && docker compose stop dashboard runtime-scheduler'
ssh doxagent-hk 'cd /root/doxagent && docker compose logs -f dashboard runtime-scheduler'




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
    period="3mo",
    interval="1d",
    benchmarks=["SPY"],
    peers=["MSFT", "GOOGL"],
)
```

## Post-MVP 3.1 Persistence

Blackboard persistence adds a Supabase/Postgres path while keeping the default
local mode in memory. Business state and workflow recovery state are stored
separately: `BlackboardService` owns runs, Working Memory, Belief State,
Commit Log, objections, delegations, and evidence, while workflow checkpoint
repositories own checkpoint history and latest-resume state.

The migration is available at
`supabase/migrations/202605300001_blackboard_workflow_persistence.sql`. It
creates a dedicated `doxagent` schema and does not include Supabase project
URLs, passwords, keys, or access tokens.

Configuration stays environment-only:

```powershell
$env:DOXAGENT_STORAGE_MODE = "postgres"
$env:DOXAGENT_DATABASE_URL = "postgresql://..."
```

Use URL encoding for special characters in database passwords. Tests do not
connect to Supabase unless a future explicit integration-test flag is provided.

## Local Debug Viewer And LangSmith Rendering

The local debug viewer serves read-only review pages for persisted Blackboard
runs and a LangSmith custom output renderer:

```powershell
.\scripts\debug-viewer.cmd 8765
```

Open `http://127.0.0.1:8765` to inspect Brief State documents and agent metrics.
If port `8765` is already occupied, the launcher automatically uses the next
free port and prints the URL to open.
The script defaults to `DOXAGENT_STORAGE_MODE=postgres` and repo-local `.tmp-uv`
cache/temp directories. If PowerShell script execution is enabled, the equivalent
entrypoint is:

```powershell
.\scripts\debug-viewer.ps1 -Port 8765
```

Keep that local process running while viewing LangSmith. In LangSmith, configure
Custom Output Rendering on the `DoxAgent` tracing project with:

```text
http://127.0.0.1:8765/langsmith-renderer.html
```

If the renderer does not appear, open the renderer URL directly in the same
browser first. Chrome may also require allowing local network access for
`https://smith.langchain.com`; Safari and Brave can block plain HTTP localhost
iframes, in which case expose the local viewer through an HTTPS tunnel and use
that tunnel URL in LangSmith.

The renderer only formats LangSmith's posted `outputs` and `metadata.inputs`
for display. The original raw JSON panels remain available in LangSmith.

## Post-MVP 3.3 Skills

Skill management lives under `src/doxagent/skills`. The code-first
`SkillRegistry` registers DoxAgent-owned and migrated external skills, including
Vibe-Trading macro/fundamental skills, financial-services Market Researcher
skills, and Hermes/O4 market trace skills. `SkillInjector` attaches a
versioned `SkillBundle` to `AgentTask` without mutating Blackboard state.

Current skill injection is a contract boundary for future real AgentRunner work:
it does not run external runtimes, read ignored reference repositories, persist
skills to Supabase, or call real LLM providers. Adapter outputs now include
skill version metadata while preserving their existing `skills` fields.

## Post-MVP 3.4 MAF Agent Runtime

The first real Agent Runtime lives under `src/doxagent/agents/runtime`.
`ModelGatewayAgentRunner` uses Microsoft Agent Framework as the execution shell
while keeping DoxAgent boundaries intact: tasks enter as `AgentTask`, model calls
go through `ModelGateway`, skills are injected by `SkillInjector`, tools go
through `ToolRegistry`, and output returns as `AgentResult`.

`MafAgentAdapter` now delegates to the real runner instead of returning a
placeholder error. Runtime tool mode can be `disabled`, `mock`, or `real`.
Because the Phase 3.2 real tools are still under debugging, normal tests use
fake gateway responses and mock tools while preserving the same tool names,
permissions, and `ToolResult` contract expected by the real tool layer.

The runtime does not replace workflow orchestration, call model providers
directly, write Blackboard state, stream responses, execute trades, or treat
LangSmith/model tracing as Commit Log audit.

## Post-MVP 3.5 Real Workflow Execution

Initialization workflow real-execution support lives in
`src/doxagent/workflows`. `BlackboardInitializationWorkflow` now supports
`execution_mode="mock"` for existing deterministic tests and
`execution_mode="agent_runner"` for the real `AgentTask -> AgentResult`
boundary. The workflow runner remains DoxAgent-owned; MAF is only used through
the 3.4 agent runner.

In `agent_runner` mode, workflow nodes build richer task context, normalize
structured runner output through `WorkflowAgentResultNormalizer`, write agent
results to Working Memory, submit valid patches through `BlackboardService`,
and save checkpoint metadata for runtime/tool mode, agent result summaries, and
blocking errors.

This phase does not call real providers by default, does not replace the
workflow with MAF workflow runtime, and does not implement O3.

## Post-MVP 3.6 Global Research Integration

Global Research module integration lives in `src/doxagent/workflows`.
`GlobalResearchModuleRunner` calls the migrated Phase 8 C1/C2/C3 modules and
native O4 market trace module. `GlobalResearchAssembler` maps those outputs into
the five-section `GlobalResearchDocument` used by the Blackboard.

`BlackboardInitializationWorkflow.run()` accepts optional `research_inputs` for
market, geography, timeframe, industry scope, universe, benchmark/peer, and O4
market trace settings. In `execution_mode="agent_runner"`, the
`BuildGlobalResearch` node now stores C1/C2/C3/O4 raw outputs in Working Memory,
assembles a stable Global Research patch, and submits it through
`BlackboardService`.

The `market_narrative_report` section is deliberately marked as pending
O1/DoxAtlas narrative integration in this phase. It is not a completed narrative
conclusion. C3 downstream hints, C2 monitoring dashboard, C1 risks/catalysts,
and O4 price/technical context are preserved for later O1/O2 work.

## Post-MVP 3.7/3.8 O1/A1/A2 Realization

O1/A1/A2 realization adds structured contracts for expectation construction,
DoxAtlas audit, and delegated retrieval. O1 now owns sourced expectation-unit
and known-event outputs, A1 audits expectation fields against DoxAtlas evidence,
and A2 has been repositioned as a Tavily-only retrieval and fact-check delegate.

A2 keeps the internal `A2` agent id for compatibility, but its default tool
permissions are limited to `tavily.search` and `tavily.extract`. Other agents
route external information gaps to A2 through `DelegatedRetrievalRequest` and
`create_a2_retrieval_delegation(...)`; A2 never writes Blackboard state
directly. `ResolveObjectionsAndDelegations` can now call A2 in
`agent_runner` mode, complete delegations when Tavily evidence is sufficient,
and leave the workflow blocked when evidence is missing.

Normal tests still use fake gateway/mock tools and do not call real Tavily,
DoxAtlas, LLM providers, Supabase, or broker services.

## Prompt And Skill Separation

Prompt resources live under `prompts/` as Markdown files with TOML front matter.
They are split into three categories so users can review and edit behavior
without touching Python code:

- `prompts/system`, `prompts/agents`, and `prompts/workflows` contain system,
  role, and workflow prompt blocks.
- `prompts/internal_task_skills` contains DoxAgent-owned SOPs such as O1
  expectation construction, A1 DoxAtlas audit, and A2 Tavily retrieval.
- `prompts/external_skill_packages` contains optional migrated packages from
  Vibe-Trading, financial-services, and Hermes/O4.

`PromptRegistry` and `PromptInjector` select these resources into
`AgentTask.prompt_bundle`. `PromptAssembler` builds the final runtime prompt.
The legacy `SkillRegistry` now acts as a compatibility layer for external skill
packages only, so system prompts and internal SOPs are no longer mixed with
external skills.

## Project Layout

```text
prompts/
src/doxagent/
  adapters/
  audit/
  agents/
  blackboard/
  context/
  core/
  gateway/
  models/
  skills/
  tools/
  workflows/
tests/
dev_plan/
examples/
references/
```
