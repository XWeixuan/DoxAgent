# MU Global Research formal run acceptance

## Verdict

- Runtime and workflow: **PASS**. The real Codex worker completed and published the full `codex_global_research_v1` graph for MU.
- Current prompt/skill injection: **PASS**. The seeded lane bundle resolved every node resource from the current canonical `prompts/agents/` and `prompts/internal_task_skills/` files. The actual C4 attempt received the full task, AGENTS, and internal-skill payloads rather than a short placeholder.
- Research artifact generation: **PASS**. C1, C3, and C5 produced non-trivial complete reports; C4 produced the intended structured-only snapshots.
- Citation acceptance for this historical run: **FAIL**. The published manifest contained one unresolved C3 alias (`O660`) and the old assembler reused bare `O#` aliases across node attempts.
- Post-run remediation: **PASS**. New-lane node completion now rejects unresolved aliases (therefore retrying/failing the affected node before publication), and final assembly deterministically assigns a run-wide citation namespace. The already-published run was not mutated.

## Run identity

| Field | Value |
|---|---|
| Run ID | `mu-global-formal-20260820-01` |
| Ticker | `MU` |
| Workflow | `codex_global_research_v1` |
| Lane | `global_research` |
| Research cutoff | `2026-08-20T11:12:40-04:00` |
| Model | `gpt-5.6-luna` |
| Effort | `max` |
| Final runtime status | `published` |
| Published at | `2026-08-20T16:28:15.068803Z` |
| Final document artifact | `95cb06f8e8364aa187427119ce8ef4e4` |
| Citation manifest artifact | `433e5868e810422ba542fb3bb4f6402f` |

## Executed graph

The observed event order was:

1. `workflow.started`
2. `c4_pre_scan` succeeded
3. `c1` succeeded
4. `c3` succeeded
5. `c5` succeeded
6. `c4_enrichment` succeeded
7. `workflow.published`

C1 and C3 ran in parallel after C4 pre-scan. All five nodes succeeded on their first attempt; there were no retries or failed nodes. No C2, O4, O4-A/O4-B, C4 finalization, or event-registry node was executed.

## Artifact shape

| Artifact | Size / count |
|---|---:|
| C1 report | 22,815 bytes |
| C3 report | 42,683 bytes |
| C5 report | 23,407 bytes |
| Final Global Research document | 89,014 bytes |
| Entity relations | 25 |
| Future nodes | 26 |
| Citation uses in final body | 365 |
| Citation manifest entries | 66 total / 65 resolved / 1 unresolved |

C4 pre-scan and enrichment report files were empty by design because those nodes return structured entity/future-node snapshots in `completion.json`.

## Citation defect and remediation evidence

The old final assembler merged node-local aliases without renumbering. Six aliases were shared by more than one node attempt, and C3 referenced unresolved `O660`. The new aggregate planner:

- rejects any unresolved entry before final publication;
- requires an attempt identity for every citation;
- remaps `(attempt_id, local_alias)` deterministically to a globally unique final alias;
- rewrites only aggregate-document copies, preserving attempt-local reports and historical artifacts.

Focused verification:

- `pytest`: 35 passed;
- Ruff: passed;
- mypy: passed for the four touched source modules;
- real-artifact replay: the raw manifest was correctly blocked at `c3-1-1ade78904d/O660`; the 65 resolved entries then replayed with 65 unique body aliases and no cross-node collision.

## Infrastructure and persistence

- Rebuilt `doxagent-dashboard:latest` and `doxagent-codex-worker:latest` after the citation fix, force-recreated both services, and confirmed both container health checks.
- Authenticated worker readiness returned `ready=true`, `authenticated=true`, with seven visible models.
- Dashboard `/healthz` returned `ok=true`, `mode=real`, `auth_mode=supabase`.
- Applied remote Supabase migration `202608200001_codex_research_lanes` transactionally and verified both lane bundle tables plus lane-aware run/checkpoint columns and constraints.
- The bounded Supabase Global Research bundle was retained; high-text observations and attempt artifacts remained in the local SQLite/workspace layer.

## Follow-up observation

C4 pre-scan generated roughly 670 attempt-local observations. This did not break correctness or Supabase egress boundaries, but it is a material runtime/context-volume optimization target before high-frequency production use.
