# Phase 0 Baseline

## Decisions

- Primary stack: Python 3.11 with `uv`.
- Package name: `doxagent`.
- Project layout: `src/` package layout with tests under `tests/`.
- Dependency strategy: minimal real runtime dependencies plus development tools.
- External capability strategy: DoxAtlas, market data, and fact-checking use
  mock/fixture mode in Phase 0.
- External GitHub agent projects remain under `references/` and are not copied,
  imported, or added as dependencies during Phase 0.

## Runtime Dependencies

- `agent-framework-core>=1.6,<2` for the future Microsoft Agent Framework
  runtime and workflow shell.
- `langsmith>=0.8,<1` for later tracing wrappers.
- `openai>=2,<3` and `anthropic>=0.71,<1` for later Model Gateway providers.
- `pydantic>=2,<3` and `pydantic-settings>=2,<3` for future schema and settings.
- `python-dotenv>=1.2,<2` for local environment loading.
- `httpx>=0.28,<1` for future HTTP integrations.

The project intentionally does not depend on the all-in-one `agent-framework`
meta package, LangChain, LangGraph, Vibe-Trading, Hermes, or financial-services
in Phase 0. The meta package currently pulls optional Azure integrations that
are outside the Phase 0 scope and can make dependency resolution less stable.

## Boundary Rules

- Microsoft Agent Framework must not own model calls, business state, or Belief
  State write decisions.
- Model Gateway will own provider routing, fallback, retry policy, structured
  output normalization, and LangSmith wrapping.
- Blackboard Service will own Working Memory, Belief State, Objection,
  Delegation, Evidence, and Commit Log.
- Context Builder will later assemble minimal task context for agents.
- Tool and MCP layers must not implicitly mutate stable Blackboard state.
- Adapter code for external GitHub projects must wait until DoxAgent contracts
  and validation boundaries are stable.

## Local Verification

Use a workspace-local uv cache if needed:

```powershell
$env:UV_CACHE_DIR = "$PWD\.uv-cache"
```

Then run:

```powershell
uv python install 3.11
uv sync --group dev
uv run pytest
uv run ruff check .
uv run mypy src
```

Phase 0 verification must not call real LLMs, DoxAtlas, market data APIs, or
external search providers.
