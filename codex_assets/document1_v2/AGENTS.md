# Document 1 v2 common execution contract

You are running one bounded node of `codex_d1_v2`. Work only inside the current
run workspace. Treat `context/` and every attempt `input/` directory as
immutable. Write only the output files named by `task.json`.

Before researching, read `task.md`, `task.json`, `context.json`, every file
listed in `required_skills`, and `horizontal.json` when present. Distinguish
facts, interpretation, and uncertainty. Use Data MCP semantic tools for
governed data and `data_tool_guide` when routing is unclear. Read only needed
Observation Pack blocks. Cite only observations available in this attempt with
`【cite:O#】`; never invent an alias. A citation warning is soft, but an
unsupported claim must not be presented as cited.

Program values are governed measurements. `EMPTY`, `FAILED`, and `UNAVAILABLE`
mean unknown, never zero. Agent-found metrics may supplement, but never replace,
a governed program value. Do not create Document 2 objects, Expectation Units,
formal `PotentialGap` objects, trading recommendations, or monitoring rules.

For nodes with progressive output, draft one required section at a time. After
each section, atomically refresh `report_draft.md` and `progress.json`. Keep
`observation_candidates.json` synchronized with the final structured response.
In `progress.json`, write only `status` and the ordered `completed_sections`
drawn verbatim from `task.json`; static section definitions remain in the input.
The final `report_markdown` must exactly mirror the completed draft after newline
normalization.
