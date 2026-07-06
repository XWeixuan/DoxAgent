# DoxAgent Blackboard Eval Contract

This file defines the local evaluation contract for DoxAgent Blackboard
initialization. It is intentionally small: hard gates, rubrics, this SOP, an
append-only record file, and one Brief State export script.

## Purpose

DoxAgent initializes a ticker-level Blackboard that should become a high-quality,
reviewable, and iteratively improvable investment state. A useful run must not
only pass schemas; it must produce:

- a stable Global Research document with fundamental, macro, industry, market
  trace, and market narrative sections;
- stable Expectation Unit documents that separate realized facts from open
  variables and explain price-in or not-priced-in reasoning;
- KnownEvents, MonitoringConfig, and MonitoringPolicy documents that support
  later monitoring;
- Working Memory, Commit Log, evidence refs, objections, delegations, and
  LangSmith traces sufficient for human and LLM-as-judge review.

This contract exists so Codex can run a repeatable baseline, judge it, record
failures and hypotheses, commit the baseline, modify the repo, rerun, and compare
the retest under the same standard.

## Files

- `eval/blackboard_hard_gates.yaml`: mandatory pass/fail gates.
- `eval/blackboard_rubrics.yaml`: 1-5 soft scoring rubrics.
- `eval/blackboard_eval_contract.md`: this contract, judge principles, SOP, and
  report format.
- `eval/blackboard_eval_records.md`: append-only evaluation records.
- `eval/export_brief_state.py`: local `run_id` to Brief State JSON exporter.

Generated Brief State exports should be written under
`eval/brief_state_exports/` unless a specific output path is needed.

## Judge Principles

1. Judge the stable Blackboard, not just raw agent text.
2. Treat schema validation as a hard prerequisite, not a quality score.
3. Use Brief State JSON for result review and LangSmith for process review.
4. Never score process rubrics from Brief State alone.
5. Do not infer tool usage from prose. Check LangSmith tool calls and final
   AgentResult tool summaries.
6. If evidence is inconclusive, score the run for how honestly it handles
   inconclusive evidence, not for forcing a confident conclusion.
7. Do not change hard gates or rubrics between a baseline and its retest.
8. Do not claim a modification worked unless baseline and retest were judged
   under the same contract.
9. Keep diagnosis specific enough to produce a testable optimization hypothesis.
10. Separate "workflow did not complete" from "workflow completed but quality is
    not investment-useful."

## Required Inputs Per Evaluation

- Git commit or dirty-tree snapshot description.
- Real test command and environment switches.
- `run_id`.
- Brief State JSON exported by `eval/export_brief_state.py`.
- LangSmith trace or MCP query notes for the same `run_id`.
- Hard gate results from `blackboard_hard_gates.yaml`.
- Rubric scores from `blackboard_rubrics.yaml`.

## Brief State Export

Run from the repository root:

```powershell
uv run python eval\export_brief_state.py <run_id>
```

Optional explicit output:

```powershell
uv run python eval\export_brief_state.py <run_id> --output eval\brief_state_exports\<run_id>.json
```

The exporter reads the configured workflow storage directly. It writes a JSON
envelope containing:

- export metadata;
- storage status;
- the human-facing Brief State view;
- agent metrics placeholder;
- raw stable documents from the belief state;
- checkpoints;
- Working Memory;
- Commit Log;
- objections;
- delegations;
- evidence refs when the local exporter includes them;
- a compact eval index for quick navigation.

This export is the result-review entry point. It is not enough for final scoring
because process rubrics require LangSmith loops, tool calls, and agent
trajectory.

## Hard Validator Compatibility

Debug Viewer has been removed. The current JSON export keeps the
`hard_validators` envelope for compatibility and marks validators as `not_run`;
hard-gate review should use the exported stable documents plus workflow and
LangSmith evidence.

- Legacy `evidence_reference_integrity`: checked that key stable beliefs, expectation
  fields, known events, objections, and state-changing commits have locatable
  evidence refs with required source metadata. It does not judge whether the
  evidence is sufficient or persuasive.
- Legacy `langsmith_trajectory_tool_boundary`: checked the locally persisted ReAct
  audit/tool-call mirror in Working Memory against current workflow and agent
  tool boundaries. It flags missing local trajectories, forbidden tools,
  failed tool calls inside successful AgentResults, and declared-but-unexecuted
  tool evidence. Remote LangSmith MCP review is still required for final process
  scoring.
- Legacy `commit_log_state_mutation_consistency`: checked that stable Blackboard
  documents can be explained by Commit Log mutations and that commit targets
  remain consistent with final state.

Do not treat `not_run` validator envelopes as pass/fail evidence. Recreate any
required hard-gate checks from the exported documents, workflow records, and
LangSmith traces.

## LangSmith Review Expectations

For the evaluated `run_id`, inspect at least these trajectory slices:

- C1/C2/C3/O4 loops for `BuildGlobalResearch`.
- O1 loops for `GenerateExpectationConstruction`,
  `ResolveExpectationConstruction`, `GenerateExpectationDetails`, and
  `GenerateGlobalNarrativeReport`.
- A1 and field-review loops for construction/detail review.
- A2 loops for delegated retrieval or fact verification, if any.
- O2 loops for `GenerateMonitoringConfig` and `GenerateMonitoringPolicy`.
- Tool calls for any cited evidence or source ids.
- Any parse/schema/write/tool-prefetch failure metadata.

The minimum trace metadata expected on child runs is `run_id`, `agent_name`,
`workflow_node`, `task_type`, and a loop or step index when applicable.

## Eval SOP

1. Start from a known git state. If changes already exist, record that fact.
2. Run the real Blackboard initialization test or workflow command.
3. Capture the resulting `run_id`.
4. Export Brief State JSON:
   `uv run python eval\export_brief_state.py <run_id>`.
5. Use LangSmith MCP to inspect loops, tool calls, and trajectory for the same
   `run_id`.
6. Evaluate every hard gate in `blackboard_hard_gates.yaml`.
7. Score every rubric in `blackboard_rubrics.yaml`, with reasons.
8. Append a baseline record to `eval/blackboard_eval_records.md`.
9. Cluster failures by category and write an optimization hypothesis.
10. Commit the baseline before modifying code, prompts, skills, schemas, or
    workflow logic.
11. Apply the planned modification.
12. Rerun the same class of test.
13. Export the retest Brief State JSON and inspect LangSmith again.
14. Re-score using the same hard gates and rubrics.
15. Append retest results under the same eval record.
16. Accept the modification only if hard gates are not regressed and target
    rubrics improve without unacceptable degradation elsewhere.

## Suggested Real Test Entry Points

Use the project-specific real API and Postgres settings already configured for
this repo. Example smoke entry point:

```powershell
$env:DOXAGENT_RUN_REAL_API_TESTS='1'
$env:DOXAGENT_STORAGE_MODE='postgres'
uv run pytest -p no:cacheprovider -s tests\test_phase17_real_initialization_smoke.py::test_real_initialization_expectation_units_smoke
```

For full finalization, continue or invoke the workflow until
`FinalizeInitialization` is complete, then evaluate that final `run_id`.
Intermediate stops are useful for diagnosis but must not be claimed as a full
Blackboard initialization pass.

## Failure Categories

Use these categories in eval records. Add a short subtype when helpful.

- `workflow_completion`: node ordering, finalization, resume, idempotency.
- `agent_output_contract`: parse, schema, top-level protocol, final_payload.
- `blackboard_persistence`: Working Memory, Commit Log, evidence refs, Postgres.
- `brief_state_visibility`: viewer/export gaps or missing stable documents.
- `evidence_integrity`: unsupported claims, missing source refs, fabricated ids.
- `tool_trajectory`: missing/forbidden/redundant tool calls.
- `agent_role_boundary`: wrong agent doing the wrong task or tool exposure drift.
- `review_objection_loop`: objections, delegations, dedupe, resolution quality.
- `research_quality`: shallow or non-investable research synthesis.
- `price_in_reasoning`: weak known-news, old-news, or market reaction handling.
- `monitoring_actionability`: weak monitoring config or policy.
- `traceability`: missing LangSmith links, metadata, or reproducibility details.

## Baseline Record Format

Append one section per baseline run in `eval/blackboard_eval_records.md`.

```markdown
## YYYY-MM-DD HH:mm - <ticker> - baseline

### Test Info
- Git state:
- Command:
- Environment:
- run_id:
- LangSmith project/run link or MCP query:
- Brief State JSON:
- Evaluator:

### Hard Gates
| Gate | Result | Evidence | Notes |
| --- | --- | --- | --- |
| HG01 | pass/fail | | |

### Rubrics
| Rubric | Score | Reason |
| --- | ---: | --- |
| R01 | 1-5 | |

### Failure Categories
- category:
  - issue:
  - evidence:
  - severity:

### Optimization Hypothesis
- Hypothesis:
- Expected metric movement:
- Risk:

### Proposed Modification Plan
- Change 1:
- Change 2:
- Files likely touched:

### Baseline Commit
- Commit hash:
- Commit message:
```

## Retest Record Format

Append retest details below the matching baseline section.

```markdown
### Retest - YYYY-MM-DD HH:mm
- Git state:
- Command:
- run_id:
- LangSmith project/run link or MCP query:
- Brief State JSON:

#### Hard Gate Delta
| Gate | Baseline | Retest | Delta | Notes |
| --- | --- | --- | --- | --- |

#### Rubric Delta
| Rubric | Baseline | Retest | Delta | Notes |
| --- | ---: | ---: | ---: | --- |

#### Result
- Improved:
- Regressed:
- Hard gates still failing:
- Accept modification: yes/no
- Reason:
- Follow-up hypothesis:
```

## Acceptance Rule For Optimization Claims

A modification can be called effective only when all are true:

- baseline and retest use the same hard gates and rubrics;
- the target failure category improves in evidence-backed ways;
- no new hard gate fails;
- regressions are explicitly listed and judged acceptable;
- the record contains both Brief State JSON paths and LangSmith trajectory notes.
