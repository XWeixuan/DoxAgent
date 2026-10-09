# Codex research lane common execution contract

You are running one bounded node of the workflow and research lane declared in
`task.json`. Work only inside the current run workspace. Treat `context/` and every
attempt `input/` directory as immutable for direct Agent file operations. Write only
the output files named by `task.json`. Data MCP and Source Capture MCP may create their
own governed attempt-scoped projections; never edit those service-managed paths.

Before researching, read `task.md`, `task.json`, `context.json`, every file listed in
`required_skills`, and `horizontal.json` when present. Distinguish facts,
interpretation, and uncertainty. Use Data MCP semantic tools for governed data and cite
only observations available in this attempt with `【cite:O#】`; never invent an alias.
`EMPTY`, `FAILED`, and `UNAVAILABLE` mean unknown, never zero.

For progressive nodes, complete required sections in order and atomically refresh
`report_draft.md`, `progress.json`, and `observation_candidates.json`. The final
`report_markdown` must exactly mirror the completed draft after newline normalization.
Structured C4e relation and C4f Future Node stages write only their respective
completion JSON and keep `report_markdown` empty. C4e network-build instead follows
its string schema: write the full Markdown to `markdown_output_path` and return that
body as a JSON string. Do not return the relation/Future Node envelope in this stage.
Shared C4 thread history does not override the current stage's prompt, skill or schema.
Do not create Document 2 objects, formal gap objects, trading
recommendations, or monitoring rules.
