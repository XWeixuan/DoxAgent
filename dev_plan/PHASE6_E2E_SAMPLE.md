# Phase 6 End-to-End Mock Ticker Sample

## Scope

Phase 6 adds an audit-oriented vertical slice on top of the Phase 5 initialization
workflow. It provides fixture inputs, a module entrypoint, generated sample JSON,
and tests that confirm a reviewer can inspect documents, evidence, objections,
delegations, Commit Log entries, and run summary output.

This phase does not change the workflow state machine, add persistence, call real
DoxAtlas or market-data services, call model providers, execute trades, or adapt
external GitHub agent projects.

## Sample Layout

The root `examples/phase6_mock_ticker` folder contains:

- `fixture.json`: deterministic mock input data for ticker `NVDA`;
- `generated_run.json`: generated review artifact from the current workflow.

The generated artifact is intentionally a review file, not a database snapshot or
formal business audit store.

## Runner

Run the sample with:

```powershell
uv run python -m doxagent.examples.phase6_mock_run --output examples/phase6_mock_ticker/generated_run.json
```

Without `--output`, the module prints a compact summary to stdout. With
`--output`, it writes a complete JSON artifact that preserves document ordering.

## Exported Review Surface

The exporter includes fixture data, workflow status, completed nodes, five
document outputs, evidence refs, Working Memory summaries, Commit Log summaries,
objection lifecycle summaries, delegation lifecycle summaries, and residual risk
notes. The sample is explicitly marked as mock data to avoid confusing it with
real investment research or live external-service output.
