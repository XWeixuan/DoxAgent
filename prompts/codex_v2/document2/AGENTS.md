# Document2 workspace contract

## Current attempt

Read `agent.md`, the active `skill.md`, `task.json`, `context.json`, and `output_schema.json`
before reasoning. Read research artifacts required by the task or active skill; consult other
supplied materials as that skill directs.

Treat the inputs explicitly supplied to this attempt as its working state. Do not assume hidden
state or memory from earlier model calls.

## Input and version authority

Use the contract and version declared by the current task and context, rather than inferring them
from asset filenames or an earlier attempt. `output_schema.json` defines the returned object shape.
The active agent prompt defines the role; the active internal skill defines the current stage's
reasoning and use of materials.

Optional inputs may be absent or unavailable. Use what is actually present without inventing missing
content. Absence alone does not prevent a valid output unless the active task requires that input.

## Output contract

Return exactly one JSON object matching the supplied schema. Emit required fields, using `[]` for
empty lists. Keep undeclared fields out of the response and preserve the schema's field names and
types. Never invent a value merely to fill required structure.

## Time, evidence, and provenance

Respect the information-availability boundary supplied by the task or context, such as `as_of` or
`research_cutoff_at`. Keep the time of an observation distinct from retrieval time. Later retrieval
does not make later observations valid evidence for an earlier dated claim; use it only as permitted
by the active task and preserve the temporal distinction. Temporal mismatch remains non-blocking:
exclude non-equivalent observations from dated claims and continue with usable evidence.

Preserve source provenance, temporal scope, stated uncertainty, and important evidence boundaries.
Keep observations, management statements, forecasts, and inferences distinguishable.

Preserve upstream reference strings when carrying evidence forward, including `D1-O#` lineage.
Follow the current task's convention for new material: current-attempt `O#` aliases for Data MCP
observations, public source URLs where appropriate, and `DoxAtlas:<source_run_id>` for supplied
Narrative provenance. Preserve received source-qualified references without renumbering them or
inventing unknown lineage. Citation-resolution failure alone is non-blocking unless the active task
states otherwise.

## Tool use and recovery

Use tools permitted by the active task when they materially improve the result.

Recoverable operational mistakes are not workflow blockers. For command, parsing, quoting, path,
validation, or file-inspection errors, correct the command or use an equivalent safe method and
continue. Do not stop the workflow or request user assistance solely because such an operation
failed. Report a real blocker only when required input, tool authority, or execution capability
remains unavailable after reasonable recovery.
