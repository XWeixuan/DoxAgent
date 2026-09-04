# O4 CONFIGURE → DELIVER Pilot

This Pilot runs the first two O4 nodes against isolated Message Bus and Crawler Plane state. It does
not run `O4_REPAIR`, a scheduler, Runtime consumption, or any production database.

## Prepare

Use a published Document 3 `PolicySet` JSON and its corresponding Document 2 JSON. From the DoxAgent
repository root:

```powershell
$env:DOXAGENT_O4_OPERATIONS_CAPABILITY_SECRET = '<at-least-32-byte-pilot-secret>'
uv run doxagent-monitoring-o4-pilot prepare `
  --case-id mu-o4-pilot-001 `
  --ticker MU `
  --policy-set D:\path\policy_set.json `
  --document2 D:\path\document2.json
```

Open the returned `case_root` as a new Codex App task and paste its `PILOT_TASK.md`. The case root is
the inner `monitoring-o4-<ticker>-main` directory; do not open its parent.

## Pilot issue records

Every CONFIGURE or DELIVER request pre-creates a node-local
`requests/<request_id>/audit/pilot_issues.md`. The executing Agent must initialize its start time,
append problems as they are encountered, and finish it with the required summary and severity counts.
The issue log covers execution difficulty, blockers, tool/state ambiguity, evidence and verification
gaps, and Prompt/Skill/Schema/Workspace problems. It is the only place for Pilot meta-analysis;
formal completion JSON, Configuration Plans, checkpoints, and Settlements retain their production
schemas.

The issue log is observational and must not become a crawler-delivery or Message Bus startup gate.
An expected undelivered source remains a business result in the checkpoint/Settlement; duplicate it
in the issue log only when it also exposes a workflow, tool, or usability defect. If no issue occurs,
the final summary must say so explicitly.

## Advance to DELIVER

O4 writes the strict CONFIGURE result to the request-local `output/completion.json`. Validate and
advance it with:

```powershell
uv run doxagent-monitoring-o4-pilot advance --case-root <absolute-case-root>
```

If CONFIGURE produced at least one `NEW_CRAWLER_REQUIRED` Source Need, the command installs the
DELIVER task and node-scoped MCP capability. Continue in the same Codex App task and paste the
updated `PILOT_TASK.md`; do not create a second task. If no crawler gap exists, the production-
equivalent workflow ends after CONFIGURE.

If the existing task still reports CONFIGURE-only tools after the transition, reissue the active
node capability without resetting the case:

```powershell
$env:DOXAGENT_O4_OPERATIONS_CAPABILITY_SECRET = '<same-secret-used-by-prepare>'
uv run doxagent-monitoring-o4-pilot refresh `
  --case-root <absolute-case-root>
```

The command updates `.codex/o4_operations_capability.token` and the task configuration while
preserving the Plan, checkpoint, working packages, and the same ticker thread. Reload/reconnect
the `o4_operations` MCP server in that same Codex App task if its tool list is still cached; never
create a replacement task or thread. The client allow-list contains the union of O4 tool names so
this reload is safe across node transitions; the signed token and server-side check still enforce
the active node's narrower permission set.

After DELIVER writes its completion, run the same `advance` command again. The coordinator validates
correlation and exact work-item settlement, persists Pilot-only O4 state, and starts the ticker only
inside the isolated Message Bus database. Incomplete delivery remains a valid degraded terminal
result and never blocks that start.

## Create a DELIVER-only rerun

When CONFIGURE has already produced a valid plan but a DELIVER attempt was blocked by a stale or
missing capability, create a new isolated case from the source case without the capability secret:

```powershell
uv run doxagent-monitoring-o4-pilot clone-deliver `
  --source-case-root D:\DoxAgentPilot\cases\monitoring_o4\<source-case>\monitoring-o4-<ticker>-main `
  --target-case-root D:\DoxAgentPilot\cases\monitoring_o4\<new-case>\monitoring-o4-<ticker>-main
```

The source must be active at `o4_deliver`. The command copies and freezes the CONFIGURE Plan,
completion and issue log, copies the crawler working/registry and Message Bus state into the new
case, rewrites copied absolute paths, and resets DELIVER output/checkpoint to `PENDING`. It reuses
the source's verified signed DELIVER capability, so the target inner directory must retain the
signed `monitoring-o4-<ticker>-main` name. Open the target `PILOT_TASK.md` as a new Codex App task;
it is already at DELIVER and must not run CONFIGURE or create another task/thread.

For this cloned, DELIVER-only case, `status` and `advance` work without a capability secret. After
the Agent writes the target `requests/<request_id>/output/completion.json`, run `advance` again to
perform settlement validation and start the isolated ticker monitor. A capability refresh still
requires the original secret, and the copied token has its original expiry.

## Inspect

```powershell
uv run doxagent-monitoring-o4-pilot status --case-root <absolute-case-root>
```

Authoritative files are `case_manifest.json`, `coordinator_state.json`, request-local inputs and
outputs, `state/*.sqlite3`, and `crawler-plane/{working,releases,artifacts,cassettes}`. Frozen input
hashes are checked on every advance. `status` also lists every `pilot_issue_logs` path. Review those
logs after each node and again before planning workflow changes. Delete the case directory to remove
all Pilot state after the Codex task is no longer needed.
