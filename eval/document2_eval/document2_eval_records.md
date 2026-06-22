# Document 2 评估记录

本文件用于追加记录 Document 2 预期单元 eval 循环。当前文件先提供模板，后续每一次可评估真实运行都应追加在本文件末尾。

记录原则：

- 每个可评估 run 都要记录，包括 partial、blocked、failed 或质量门槛失败的 run。
- baseline 和 retest 必须写在同一个 eval section 下，便于比较。
- 不要把 pending patch 写成 stable expectation_unit。
- 不要把 Document 2 focused smoke 写成完整 Blackboard 初始化通过。
- 如果本轮只停在 `GenerateExpectationDetails`、`ReviewExpectationFields` 或 `ResolveObjectionsAndDelegations`，必须在标题和结果里标注 partial scope。

## Baseline 记录模板

```markdown
## YYYY-MM-DD HH:mm - <ticker> - Document 2 baseline - <stop_after>

### Test Info
- Git state:
- Source run_id:
- Source state:
- Source Brief State JSON:
- Execution mode: clone/in-place
- Command:
- Environment:
- Execution run_id:
- Stop after:
- Brief State JSON:
- LangSmith project/run link or MCP query:
- Evaluator:

### Scope Decision
- Eval mode: detail_only/field_review/resolve/promote
- Can judge stable expectation_unit: yes/no
- Cannot claim:

### Hard Gates
| Gate | Result | Evidence | Notes |
| --- | --- | --- | --- |
| D2-HG01 | pass/fail | | |
| D2-HG02 | pass/fail | | |
| D2-HG03 | pass/fail | | |
| D2-HG04 | pass/fail | | |
| D2-HG05 | pass/fail | | |
| D2-HG06 | pass/fail | | |
| D2-HG07 | pass/fail/not_applicable | | |
| D2-HG08 | pass/fail/not_applicable | | |
| D2-HG09 | pass/fail/not_applicable | | |
| D2-HG10 | pass/fail | | |
| D2-HG11 | pass/fail | | |
| D2-HG12 | pass/fail | | |
| D2-HG13 | pass/fail | | |

### Rubrics
| Rubric | Score | Reason |
| --- | ---: | --- |
| D2-R01 | 1-5 | |
| D2-R02 | 1-5 | |
| D2-R03 | 1-5 | |
| D2-R04 | 1-5 | |
| D2-R05 | 1-5 | |
| D2-R06 | 1-5 | |
| D2-R07 | 1-5 | |
| D2-R08 | 1-5 | |
| D2-R09 | 1-5 | |
| D2-R10 | 1-5 | |
| D2-R11 | 1-5 | |
| D2-R12 | 1-5 | |
| D2-R13 | 1-5 | |
| D2-R14 | 1-5 | |

### Document 2 State Summary
- Pending expectation patches:
- Stable expectation_unit count:
- Open objections:
- Blocking delegations:
- Promotion blocker summary:
- Working Memory entries of interest:
- Commit Log entries of interest:

### Failure Categories
- category:
  - issue:
  - evidence:
  - severity:
  - suspected root cause:

### Optimization Hypothesis
- Hypothesis:
- Expected hard-gate movement:
- Expected rubric movement:
- Risk:
- How to test:

### Proposed Modification Plan
- Change 1:
- Change 2:
- Files likely touched:
- Tests or smoke commands:

### Baseline Commit Or Snapshot
- Commit hash:
- Dirty-tree notes:
```

## Retest 记录模板

```markdown
### Retest - YYYY-MM-DD HH:mm - <stop_after>
- Git state:
- Command:
- Environment:
- Source run_id:
- Execution run_id:
- Brief State JSON:
- LangSmith project/run link or MCP query:

#### Scope Decision
- Eval mode:
- Can judge stable expectation_unit: yes/no

#### Hard Gate Delta
| Gate | Baseline | Retest | Delta | Notes |
| --- | --- | --- | --- | --- |
| D2-HG01 | | | | |
| D2-HG02 | | | | |
| D2-HG03 | | | | |
| D2-HG04 | | | | |
| D2-HG05 | | | | |
| D2-HG06 | | | | |
| D2-HG07 | | | | |
| D2-HG08 | | | | |
| D2-HG09 | | | | |
| D2-HG10 | | | | |
| D2-HG11 | | | | |
| D2-HG12 | | | | |
| D2-HG13 | | | | |

#### Rubric Delta
| Rubric | Baseline | Retest | Delta | Notes |
| --- | ---: | ---: | ---: | --- |
| D2-R01 | | | | |
| D2-R02 | | | | |
| D2-R03 | | | | |
| D2-R04 | | | | |
| D2-R05 | | | | |
| D2-R06 | | | | |
| D2-R07 | | | | |
| D2-R08 | | | | |
| D2-R09 | | | | |
| D2-R10 | | | | |
| D2-R11 | | | | |
| D2-R12 | | | | |
| D2-R13 | | | | |
| D2-R14 | | | | |

#### Result
- Improved:
- Regressed:
- Hard gates still failing:
- Accept modification: yes/no
- Reason:
- Follow-up hypothesis:
```

## 2026-06-22 17:17 - MU - Document 2 baseline blocked - PromoteExpectationToBeliefState

### Test Info
- Git state: local/cloud baseline commit `9d0ba29 feat: harden document2 eval workflow`; local dirty tree contained unrelated full-flow eval artifacts and this diagnostic fix work after the run started.
- Source run_id: `run_58f5afce8b9441ca804a2cde1ad9aec8`
- Source state: PASS Document 1-only. Ticker `MU`; latest checkpoint `status=running`, `completed_nodes=[StartTickerInitialization, BuildGlobalResearch]`, `next_node=ReviewGlobalResearch`, `stable_document_types=[global_research]`, `pending_patch_count=0`, unresolved objections `0`, blocking delegations `0`.
- Execution mode: `clone`
- Command: `docker compose run --rm -e DOXAGENT_RUN_REAL_API_TESTS=1 -e DOXAGENT_STORAGE_MODE=postgres debug-viewer python eval/run_document2_expectation_units_smoke.py run_58f5afce8b9441ca804a2cde1ad9aec8 --mode clone --stop-after PromoteExpectationToBeliefState --export-brief-state`
- Environment: cloud server `doxagent-hk`, `/root/doxagent`, Docker image built from `9d0ba29`, `debug-viewer` service healthy.
- Execution run_id: `run_8b311e60a116451ea1e2ec71eaca58a5`
- Stop after: `PromoteExpectationToBeliefState`
- Brief State JSON: not exported because the run was manually stopped after a resolver stall; DB checkpoint evidence retained.
- Remote log: `/root/doxagent/.eval_runs/document2-loop1-20260622T164313+0800.log`
- LangSmith MCP query: project `DoxAgent`; visible traces for `O1.GenerateExpectationConstruction.LOOP2` and `A1.ReviewExpectationConstruction.LOOP2/5/7`; no new trace after `2026-06-22T08:49:03Z`.
- Evaluator: Codex, strict diagnostic baseline.

### Optimization Hypothesis
- Hypothesis: `ResolveExpectationConstruction` has a no-op path bug for runs with zero construction objections/delegations. The node should immediately mark itself completed, but `_mark_completed()` calls `_summary()`, and `_summary()` full-loads the whole Postgres Blackboard run, including oversized cloned Working Memory. In this run that left the process polling sockets with no new checkpoint, no LangSmith trace, and no business failure entry.
- Expected hard-gate movement: D2-HG02 should move from fail to pass for the construction-resolver segment; D2-HG11 should improve because the workflow will no longer silently stall on summary generation; later gates become evaluable instead of blocked.
- Expected rubric movement: D2-R13 and D2-R14 should improve immediately because retest can produce a completed artifact chain; content-quality rubrics D2-R03 through D2-R10 remain unproven until retest reaches detail/review/resolve/promotion.
- Risk: A too-broad shortcut could hide real A1 construction objections or A2 delegations. The fix must only avoid full-run loading; it must still run A2/O1 resolver when unresolved blockers exist.
- How to test: add a mock regression proving no-op construction resolver does not call repository `get`; rerun cloud Document2 eval from the same source and verify the checkpoint advances beyond `ResolveExpectationConstruction`.

### Proposed Modification Plan
- Change 1: Add repository/service lightweight read APIs for unresolved objections, blocking delegations, and summary counts.
- Change 2: Change `_resolve_expectation_construction` to use lightweight blocker reads before running A2/O1 resolver; only real blockers trigger agent work.
- Change 3: Change workflow `_summary()` to use lightweight count aggregation instead of full `BlackboardRun` loading.
- Files touched: `src/doxagent/blackboard/repository.py`, `src/doxagent/blackboard/postgres_repository.py`, `src/doxagent/blackboard/service.py`, `src/doxagent/workflows/initialization.py`, `tests/test_phase5_initialization_workflow.py`, `changelog`, `eval/document2_eval/document2_eval_records.md`.
- Tests or smoke commands: local mock regression `uv run pytest -q tests\test_phase5_initialization_workflow.py::test_construction_resolver_noop_avoids_full_blackboard_load tests\test_phase5_initialization_workflow.py::test_initialization_workflow_runs_mock_ticker_to_completion`; retest must be cloud-only with same source run and same stop_after.

### Scope Decision
- Eval mode: `promote`
- Can judge stable expectation_unit: no
- Cannot claim: stable `expectation_unit`, detail patch quality, field-review quality, resolver quality, promotion quality, or optimization success.

### Hard Gates
| Gate | Result | Evidence | Notes |
| --- | --- | --- | --- |
| D2-HG01 | pass | Source checkpoint is Document 1-only with only `global_research` stable and no blockers. | Valid seed. |
| D2-HG02 | fail | Latest execution checkpoint at `ResolveExpectationConstruction`, not target `PromoteExpectationToBeliefState`; container stopped after 27+ minutes without new checkpoint. | Workflow completion blocker. |
| D2-HG03 | pass | `expectation_shells` metadata contains three MU shells: AI/HBM bull case, cycle/valuation risk, HBM competition risk. | Shells are diagnosable, though not final quality proof. |
| D2-HG04 | fail | No pending detail patches; run never reached `GenerateExpectationDetails`. | Not evaluable beyond construction. |
| D2-HG05 | fail | Shell evidence exists, but detail-level fact/price/variable evidence cannot be judged. | Promote-level gate failed due missing details. |
| D2-HG06 | fail | No realized facts or price-reaction fields produced. | Blocked before detail generation. |
| D2-HG07 | fail | Field review did not run. | Blocked before field review. |
| D2-HG08 | fail | Resolver for field objections did not run. | Blocked before field-review lifecycle. |
| D2-HG09 | fail | Stable `expectation_unit` count is 0. | No promotion. |
| D2-HG10 | fail | LangSmith traces visible until `ReviewExpectationConstruction`; no trace for the stalled resolver period. | Process trace incomplete for blocker. |
| D2-HG11 | fail | Stall had no business-audit failure entry in checkpoint or Working Memory. | This is the main debug target. |
| D2-HG12 | fail | Run stalled before detail work; evidence suggests oversized full-run loading during summary/no-op path. | Context/payload control issue, not content quality. |
| D2-HG13 | fail | No downstream continuation after construction review. | Memory continuity cannot be proven. |

### Rubrics
| Rubric | Score | Reason |
| --- | ---: | --- |
| D2-R01 | 3 | Document 1 handoff is valid and shells use upstream MU context, but the run did not reach usable Document 2 output. |
| D2-R02 | 3 | Three shells are differentiated and directionally clear; strict score capped because details/promotion are missing. |
| D2-R03 | 1 | No realized facts or price reactions were generated. |
| D2-R04 | 1 | No detail-level price-in/not-priced-in reasoning was generated. |
| D2-R05 | 1 | No detail-level key variables were generated. |
| D2-R06 | 1 | No event monitoring direction was generated. |
| D2-R07 | 2 | Construction shell evidence exists via DoxAtlas refs, but detail claim traceability is absent. |
| D2-R08 | 1 | Field review did not run. |
| D2-R09 | 1 | Objection/delegation lifecycle for detail fields did not run. |
| D2-R10 | 1 | No stable `expectation_unit`; promotion readiness cannot be claimed. |
| D2-R11 | 2 | Tool/trace use is partially visible for construction, but the stall is not audited as a business event. |
| D2-R12 | 2 | The system did not fabricate certainty, but no downstream uncertainty discipline is evaluable. |
| D2-R13 | 3 | DB checkpoints, remote log, LangSmith traces, and socket/process evidence are sufficient to reproduce the blocker, but no final artifact exists. |
| D2-R14 | 4 | Failure category and root cause are specific and directly testable without changing rubrics. |

### Document 2 State Summary
- Pending expectation patches: 0
- Stable expectation_unit count: 0
- Open objections: 0
- Blocking delegations: 0
- Promotion blocker summary: workflow stalled before details/promotion despite no construction blockers.
- Working Memory entries of interest: `agent_result` for O1 construction at `2026-06-22T08:45:39Z`; `a1_expectation_construction_review` at `2026-06-22T08:50:10Z`.
- Commit Log entries of interest: 1 carried from cloned Document 1 `global_research` commit.

### Failure Categories
- category: `workflow_completion`
  - issue: run stalled at `ResolveExpectationConstruction` and never reached target stop_after.
  - evidence: latest checkpoint `checkpoint_33ce4ec0738942fcbcbf65cd85aa00c3`, `next_node=ResolveExpectationConstruction`, created `2026-06-22T08:50:15Z`; no newer checkpoint by `2026-06-22T09:17Z`.
  - severity: high/blocking
  - suspected root cause: no-op resolver summary path full-loads oversized run state instead of using lightweight counts.
- category: `traceability`
  - issue: process stall was visible in DB/process/socket evidence but not persisted as Working Memory or checkpoint error.
  - evidence: Python process waiting in `do_poll`; sockets included CLOSE-WAIT external connections; no LangSmith trace after `A1.ReviewExpectationConstruction.LOOP7`.
  - severity: high
  - suspected root cause: blocking occurred outside audited agent result path.
- category: `optimization_readiness`
  - issue: content quality is not evaluable until the workflow reaches detail generation.
  - evidence: no pending patches or stable expectation units.
  - severity: medium
  - suspected root cause: infrastructure/persistence read path blocks before content-quality stages.

### Baseline Commit Or Snapshot
- Commit hash: `9d0ba29`
- Dirty-tree notes: baseline execution started before the current lightweight-summary fix. Local worktree also had unrelated full-flow eval artifacts (`eval/blackboard_eval_records.md`, `eval/blackboard_rubrics.yaml`, brief_state exports) that were not part of this Document2 record.
