# Document2 v2 agent contract

Read every file named by the task before reasoning. Treat `shell.json` and the pinned input
artifacts as the only durable business state. Return exactly one JSON object matching the supplied
schema. Research may use Data MCP and native web search when the task permits it.

Use natural-language semantic identifiers. Preserve source roles, time scope, `as_of`, evidence
boundaries, and uncertainty. Never invent a value merely to fill a field. Citation failures are
non-blocking: retain the citation text or record an empty citation list instead of refusing the
business output.

Recoverable operational mistakes are not workflow blockers. If a PowerShell, shell, Python,
validation, or file-inspection command fails because of syntax, quoting, piping, parsing, path, or
invocation mistakes, correct the command or use an equivalent safe method and continue. Do not
stop the workflow or request user assistance solely because such a command failed. Report a real
blocker only when required input or authority remains unavailable after reasonable safe recovery.

O1 should normally preserve the Shell/Unit structure supplied by O0. This is a prompt-level
research discipline, not an orchestration restriction: when the research genuinely requires a
structural correction, return the best complete Shell object directly.

For citation lineage, retain upstream `D1-O#` references, use current-attempt `O#` aliases for new
Data MCP observations, retain public source URLs when appropriate, and identify the injected legacy
Narrative Research report as `DoxAtlas:<source_run_id>`. Do not invent an alias when lineage is
unknown; unresolved citation strings remain non-blocking.
