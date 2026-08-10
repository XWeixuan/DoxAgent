# Document 1 v2 execution rules

You are running one bounded node of `codex_d1_v2`. The legacy workflow is not
available for mutation. Treat every file under `context/` and every attempt
`input/` directory as immutable. Write no files unless the node prompt names the
destination. Never access paths outside the current run workspace.

Use program-collected values as governed measurements. A target status of
`EMPTY`, `FAILED`, or `UNAVAILABLE` is an explicit unknown, not zero. Distinguish
facts, interpretation, and uncertainty. Use Data MCP semantic tools when the
program-collected context is insufficient; call `data_tool_guide` when routing
is unclear. Small results return inline observations, while large results return
an Observation Pack plus selected observations. Read only a needed block through
`data_read_observation`. Cite Data MCP and Source Capture observations only with
the attempt-local form `【cite:O#】`. Citation or Source Capture warnings do not
block completion.

Never copy an `O#` citation alias from an upstream node context: aliases are valid
only in the attempt that created them. If the current node needs to cite the same
evidence, call Data MCP again and cite the new attempt-local alias.
Never invent an `O#` alias. If no tool returned an observation in the current
attempt, omit citation markers instead of guessing one.

Subagents are permitted only for C1, C3, and O4-A, at most two, and must be
read-only. Return exactly one structured JSON completion matching the supplied
schema. Do not create Document 2 objects, Expectation Units, gaps, trading
recommendations, or monitoring rules.
