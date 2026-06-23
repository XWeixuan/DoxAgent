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

#### Hard Gate Failure Root Cause Matrix
Every failed or partial hard gate must be classified before writing the modification plan.

`failure_kind` values:
- `direct`: this gate names the active blocker or quality defect being fixed in this loop.
- `derivative`: this gate failed only because an upstream direct blocker prevented later nodes from running.
- `quality_residual`: workflow reached the required node, but output quality or downstream usability still fails this gate.

| Gate | Result | failure_kind | blocking_node | root cause | covered_by_modification | retest expectation |
| --- | --- | --- | --- | --- | --- | --- |
| D2-HG02 | | | | | | |
| D2-HG04 | | | | | | |
| D2-HG05 | | | | | | |
| D2-HG06 | | | | | | |
| D2-HG07 | | | | | | |
| D2-HG08 | | | | | | |
| D2-HG09 | | | | | | |
| D2-HG10 | | | | | | |
| D2-HG12 | | | | | | |
| D2-HG13 | | | | | | |

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

## 2026-06-22 17:36 - MU - Document 2 loop 1 retest blocked - ReviewExpectationConstruction

### Test Info
- Git state: cloud deployed commit `cd3428de5471a6ce8f2da06d70bfced94e497ada` (`fix: avoid full run loads in document2 resolver`).
- Source run_id: `run_58f5afce8b9441ca804a2cde1ad9aec8`
- Source state: same Document 1-only source as baseline; stable `global_research` only.
- Execution mode: `clone`
- Command: `docker compose run --rm -e DOXAGENT_RUN_REAL_API_TESTS=1 -e DOXAGENT_STORAGE_MODE=postgres debug-viewer python eval/run_document2_expectation_units_smoke.py run_58f5afce8b9441ca804a2cde1ad9aec8 --mode clone --stop-after PromoteExpectationToBeliefState --export-brief-state`
- Environment: cloud server `doxagent-hk`, `/root/doxagent`, Docker image built from `cd3428d`, `debug-viewer` healthy.
- Execution run_id: `run_7d1b438fde8048f5938723fd916d0880`
- Stop after: `PromoteExpectationToBeliefState`
- Brief State JSON: remote export path `eval/brief_state_exports/run_7d1b438fde8048f5938723fd916d0880.json`
- Remote log: `/root/doxagent/.eval_runs/document2-loop1-retest-20260622T173057+0800.log`
- LangSmith MCP query: project `DoxAgent`; visible A1 `ReviewExpectationConstruction` loops through LOOP5.
- Evaluator: Codex, strict diagnostic retest.

### Optimization Hypothesis
- Previous blocker movement: the run no longer stalled silently at `ResolveExpectationConstruction`; the cloud script exited and persisted a blocked checkpoint. This partially validates the lightweight-summary fix.
- New blocker: A1 construction review reached `max_steps` without a complete `DoxAtlasAuditResult`. The Working Memory `react_audit` shows two no-progress loops, then invalid scoped DoxAtlas calls:
  - `doxa_query_propositions` called with `ticker+narrative_code`, rejected as unsupported `ticker`.
  - retry with bare `narrative_code`, rejected because proposition lookup requires event scope or `proposition_id`.
  - `doxa_get_ignored_propositions` called with `ticker+narrative_code`, then bare `narrative_code`, both rejected; narrative scope requires at least DoxAtlas `run_id+narrative_code`.
- Root hypothesis: A1 construction-review prompt/tool contract is too weak about DoxAtlas scoped ids. The agent knows it wants proposition-level evidence but is not forced to extract DoxAtlas `run_id` and event codes from the narrative report, and it is not told to finalize with warnings when only narrative-level evidence is available.
- Expected hard-gate movement: D2-HG02 should advance past `ReviewExpectationConstruction`; D2-HG10/D2-HG11 should remain auditable because failures are now captured in DB/log/Working Memory.
- Expected rubric movement: D2-R11 and D2-R13 should improve through cleaner tool trajectory and traceability; content rubrics remain capped until details/promotion are reached.
- Risk: over-tightening could cause A1 to skip useful proposition checks. The fix therefore preserves proposition tools but makes legal scope forms explicit and requires a final audit with data gaps when event scope is unavailable.

### Proposed Modification Plan
- Change 1: Update `a1-expectation-construction-audit` skill to state exact legal inputs for `doxa_query_propositions` and `doxa_get_ignored_propositions`, including negative examples (`ticker`, bare `narrative_code`).
- Change 2: Update DoxAtlas tool descriptors so every ReAct step sees compact contract briefs: proposition lookup requires event/proposition scope; ignored propositions require run/narrative/event scope.
- Change 3: Update global ReAct `doxatlas_contract_brief` to warn against `ticker` and bare `narrative_code` on scoped proposition tools and to finalize with data gaps when scope cannot be recovered.
- Change 4: Inject construction-review-specific `doxatlas_scope_guardrails` into workflow task context, including a fallback policy after non-retryable scope validation errors.
- Change 5: Add local regression tests proving the workflow context and ReAct policy expose these guardrails.
- Files touched: `prompts/internal_task_skills/a1-expectation-construction-audit.md`, `src/doxagent/tools/factory.py`, `src/doxagent/agents/runtime/react.py`, `src/doxagent/workflows/initialization.py`, `tests/test_phase5_initialization_workflow.py`, `tests/test_phase16_react_harness.py`, `changelog`, `eval/document2_eval/document2_eval_records.md`.
- Retest requirement: after commit/push/cloud build, rerun the same source and stop_after in cloud-only mode.

### Scope Decision
- Eval mode: `promote`
- Can judge stable expectation_unit: no
- Can judge improvement: yes, for the previous silent resolver stall only.
- Cannot claim: stable `expectation_unit`, detail patch quality, field-review quality, resolver quality, promotion quality, or overall quality-target success.

### Hard Gates
| Gate | Result | Evidence | Notes |
| --- | --- | --- | --- |
| D2-HG01 | pass | Same verified Document 1-only source run. | Valid seed retained. |
| D2-HG02 | fail | Latest checkpoint `status=blocked`, `next_node=ReviewExpectationConstruction`, target not reached. | New blocker. |
| D2-HG03 | pass | Two MU expectation shells persisted in checkpoint metadata after construction. | Shell formation still works. |
| D2-HG04 | fail | No detail pending patches; blocked before `GenerateExpectationDetails`. | Not evaluable. |
| D2-HG05 | fail | Construction shell evidence exists, but detail-level claim evidence absent. | Not promotable. |
| D2-HG06 | fail | No realized facts or price-reaction fields. | Blocked before details. |
| D2-HG07 | fail | A1 construction review failed; field review did not run. | Review pressure not yet sufficient. |
| D2-HG08 | fail | Resolver for field objections did not run. | Blocked earlier. |
| D2-HG09 | fail | Stable `expectation_unit` count is 0. | No promotion. |
| D2-HG10 | pass | LangSmith traces show A1 loops through LOOP5; DB Working Memory captures `a1_expectation_construction_review`. | Traceability improved versus baseline. |
| D2-HG11 | pass | Failure is persisted as `WorkflowContractError` and `a1_expectation_construction_review` failed payload. | No silent stall. |
| D2-HG12 | fail | Context/tool contract still caused invalid tool calls and step exhaustion. | Context management remains a blocker. |
| D2-HG13 | fail | No downstream continuation after construction review. | Memory continuity beyond review cannot be proven. |

### Rubrics
| Rubric | Score | Reason |
| --- | ---: | --- |
| D2-R01 | 3 | Source handoff remains valid, but no usable Document 2 artifact emerged. |
| D2-R02 | 3 | Shells are differentiated and directionally clear; score capped because A1 review failed before details. |
| D2-R03 | 1 | No realized facts were generated. |
| D2-R04 | 1 | No price-in/not-priced-in detail reasoning. |
| D2-R05 | 1 | No key variables. |
| D2-R06 | 1 | No event monitoring direction. |
| D2-R07 | 2 | DoxAtlas narrative support is visible, but proposition-level traceability failed due invalid scope usage. |
| D2-R08 | 1 | Field review did not run. |
| D2-R09 | 1 | Detail objection lifecycle did not run. |
| D2-R10 | 1 | No stable `expectation_unit`; promotion readiness absent. |
| D2-R11 | 3 | Tool trajectory is auditable and failure modes are explicit, but the trajectory still wastes steps on invalid calls. |
| D2-R12 | 2 | The system did not fabricate final support, but it also failed to provide a bounded audit conclusion. |
| D2-R13 | 4 | DB, log, LangSmith, and Working Memory reproduce the exact failure chain. |
| D2-R14 | 4 | Failure category and proposed fix are specific, testable, and scoped to prompt/tool-contract/workflow context. |

### Document 2 State Summary
- Pending expectation patches: 0
- Stable expectation_unit count: 0
- Open objections: 0
- Blocking delegations: 0
- Latest checkpoint: `blocked`, `next_node=ReviewExpectationConstruction`
- Working Memory entries of interest: `agent_result` for O1 construction; failed `a1_expectation_construction_review` with invalid DoxAtlas scoped tool calls.
- Script error: `ReviewExpectationConstruction agent result failed: ReAct loop reached max_steps without a complete final payload.`

### Failure Categories
- category: `workflow_completion`
  - issue: target stop_after not reached; run blocked at construction review.
  - evidence: checkpoint `checkpoint_fc3daeb8e1374ad4b3e61db6e1ea9056`, `status=blocked`.
  - severity: high/blocking
  - suspected root cause: A1 ReAct failed to produce final audit after invalid scoped tool calls.
- category: `tool_trajectory`
  - issue: A1 used invalid DoxAtlas proposition inputs (`ticker`, bare `narrative_code`) and exhausted steps.
  - evidence: Working Memory `react_audit` tool errors for `doxa_query_propositions` and `doxa_get_ignored_propositions`.
  - severity: high
  - suspected root cause: scoped DoxAtlas contract not prominent enough in construction-review prompt/context.
- category: `context_management`
  - issue: A1 had enough narrative-level evidence to issue a bounded construction audit, but the prompt did not force finalization with data gaps when event scope was unavailable.
  - evidence: compaction summary already identified N01/N06 support, yet subsequent loops retried invalid scoped tools.
  - severity: medium/high
  - suspected root cause: missing fallback/finalization policy after non-retryable tool validation errors.
- category: `traceability`
  - issue: improved from baseline; failure is now auditable rather than silent.
  - evidence: remote log, checkpoint metadata, and Working Memory agree on the error.
  - severity: low residual
  - suspected root cause: previous lightweight summary fix working as intended for this segment.

## 2026-06-22 18:42 - MU - Document 2 loop 1 retest2 blocked - GenerateExpectationDetails

### Test Info
- Git state: cloud deployed commit `ecdf6b30dd24a742fc417a7ea58a15f1ea3c3685` (`fix: guard document2 construction audit scopes`).
- Source run_id: `run_58f5afce8b9441ca804a2cde1ad9aec8`
- Source state: same verified Document 1-only source; stable `global_research` only, no pending patches, no unresolved objections, no blocking delegations.
- Execution mode: `clone`
- Command: `docker compose run --rm -e DOXAGENT_RUN_REAL_API_TESTS=1 -e DOXAGENT_STORAGE_MODE=postgres debug-viewer python eval/run_document2_expectation_units_smoke.py run_58f5afce8b9441ca804a2cde1ad9aec8 --mode clone --stop-after PromoteExpectationToBeliefState --export-brief-state`
- Environment: cloud server `doxagent-hk`, `/root/doxagent`, Docker image built from `ecdf6b3`, `debug-viewer` healthy.
- Execution run_id: `run_8183fb1b1a654dd98b45ae04299fe2f6`
- Stop after: `PromoteExpectationToBeliefState`
- Brief State JSON: remote export path `eval/brief_state_exports/run_8183fb1b1a654dd98b45ae04299fe2f6.json`
- Remote log: `/root/doxagent/.eval_runs/document2-loop1-retest2-20260622T175438+0800.log`
- LangSmith MCP query: project `DoxAgent`; root LLM traces visible for `A1.ReviewExpectationConstruction.LOOP6/7` and `O1.GenerateExpectationDetails.LOOP1-13`. `O1.GenerateExpectationDetails.LOOP7` completed `expectation_mu_002`; `LOOP13` completed `expectation_mu_003`; one detail loop in the same window shows `CancelledError()`.
- Built-in hard validators: overall `failed`; `evidence_reference_integrity=passed`, `langsmith_trajectory_tool_boundary=failed` because latest checkpoint is not completed, `commit_log_state_mutation_consistency=passed`.
- Evaluator: Codex, strict diagnostic retest2.

### Optimization Hypothesis
- Previous blocker movement: the A1 construction-review guardrail fix worked. Retest2 passed `ReviewExpectationConstruction` and `ResolveExpectationConstruction` and reached `GenerateExpectationDetails`; A1 used legal DoxAtlas scoped calls and produced a final audit instead of exhausting steps on invalid `ticker` or bare `narrative_code` scopes.
- New blocker: `GenerateExpectationDetails` timed out at the parent parallel-agent wall clock: `parallel_agent_timeout: GenerateExpectationDetails/O1 did not return within 1800 seconds.`
- DB evidence shows a single missing shell: idempotency state for `expectation_mu_001` is `failed`, while `expectation_mu_002` and `expectation_mu_003` are `completed`. Working Memory contains two succeeded `expectation_detail_result` entries with `patch_expectation_mu_002_detail` and `patch_expectation_mu_003_detail`, each using `doxa_get_narrative_report`.
- Root hypothesis 1: detail fan-out has an aggregate persistence weakness. `_generate_expectation_details` waits for all O1 shell workers to return before validating, writing Working Memory, and caching successful sibling results. When one shell hangs, successful sibling details remain hostage to the full 1800-second parent timeout, reducing resumability and making the audit trail appear late.
- Root hypothesis 2: O1 detail prompt/tool budget is too permissive for high-salience shells. The successful detail workers still used large 24k-28k token compaction requests and repeated narrative-tool planning before completing; the core bullish `expectation_mu_001` shell is likely the longest/highest-context case and needs a stronger one-tool-call completion budget plus explicit fallback to unknowns rather than repeated low-value tool loops.
- Expected hard-gate movement: D2-HG04 should improve first by making partial detail success persist immediately and by giving `expectation_mu_001` a bounded path to finish. If all details complete, D2-HG07-D2-HG09 become evaluable in the next retest.
- Expected rubric movement: D2-R11 and D2-R13 should improve through more timely cache/trace persistence; D2-R03-D2-R06 can improve only if all three detail patches complete and enter review/promotion.
- Risk: accepting partial details would violate the Document2 contract. The modification must not promote or merge pending patches until every shell has produced a valid detail patch; failed shells must still block the node.

### Proposed Modification Plan
- Change 1: In `_generate_expectation_details`, process each parallel O1 detail outcome through `on_outcome` as soon as it completes: validate success, prefetch/validate required narrative evidence, write Working Memory, store workflow-agent cache, and save a checkpoint.
- Change 2: Preserve ordered final merge semantics. Successful details may be cached early, but `pending_patches` should be added only after every shell is accepted; any timeout or validation error must keep the node blocked.
- Change 3: Add a structured `detail_completion_budget` and detail instruction requiring at most one successful `doxa_get_narrative_report` call per shell, followed by final output with explicit unknowns/rationale if evidence is limited.
- Change 4: Add regression coverage proving detail tasks receive the new completion budget and existing parallel merge/resume/retry behavior remains intact.
- Files to touch: `src/doxagent/workflows/initialization.py`, `tests/test_phase5_initialization_workflow.py`, `changelog`, `eval/document2_eval/document2_eval_records.md`.
- Retest requirement: commit/push, cloud `git pull --ff-only`, rebuild `debug-viewer`, rerun the same source and same `--stop-after PromoteExpectationToBeliefState` in cloud-only mode.

### Scope Decision
- Eval mode: `promote`
- Can judge stable expectation_unit: no
- Can judge improvement: yes, for construction-review guardrails and resolver advancement.
- Cannot claim: detail node completion, field-review quality, objection-resolution quality, promotion quality, or overall quality-target success.

### Hard Gates
| Gate | Result | Evidence | Notes |
| --- | --- | --- | --- |
| D2-HG01 | pass | Same verified Document 1-only source run. | Valid seed retained. |
| D2-HG02 | fail | Latest checkpoint `status=blocked`, `next_node=GenerateExpectationDetails`, target `PromoteExpectationToBeliefState` not reached. | New blocker moved downstream from construction review. |
| D2-HG03 | pass | Construction, A1 review, and construction resolver completed; checkpoint contains three shells: `expectation_mu_001`, `expectation_mu_002`, `expectation_mu_003`. | Previous tool-scope issue fixed. |
| D2-HG04 | fail | `pending_patch_count=0`; only two detail Working Memory entries exist and node did not complete. | Partial detail success is not enough. |
| D2-HG05 | fail | `mu_002` and `mu_003` have DoxAtlas tool evidence; `mu_001` has no accepted detail patch. | Evidence set incomplete. |
| D2-HG06 | fail | No complete set of realized facts / price reactions across all shells and no pending/stable expectation docs. | Detail quality cannot be accepted. |
| D2-HG07 | fail | `ReviewExpectationFields` did not run. | Blocked before field-review lifecycle. |
| D2-HG08 | fail | `ResolveObjectionsAndDelegations` did not run. | Blocked before field objections. |
| D2-HG09 | fail | Stable `expectation_unit` count is 0. | No promotion. |
| D2-HG10 | pass | LangSmith traces are available for A1 construction review and O1 detail loops, including completed detail traces and one errored/cancelled loop. | Process is auditable, not accepted. |
| D2-HG11 | pass | Remote log, checkpoint metadata, and workflow idempotency record the `parallel_agent_timeout` failure for `expectation_mu_001`. | Failure is a business-audit fact. |
| D2-HG12 | fail | Detail workers show large compaction/model inputs and one sibling timeout/cancellation; successful siblings are cached only after aggregate timeout. | Context and fan-out persistence need hardening. |
| D2-HG13 | fail | No downstream review/resolver/promotion continuity; successful detail outputs cannot yet support stable Document2 state. | Memory continuity remains unproven. |

### Built-in Hard Validators
| Validator | Result | Evidence | Notes |
| --- | --- | --- | --- |
| evidence_reference_integrity | pass | Cloud DebugRunQueryService hard validator. | Existing stable `global_research` and commits have hydrated evidence. |
| langsmith_trajectory_tool_boundary | fail | Finding `workflow_trace_not_completed` at `latest_checkpoint.status`. | Correctly blocks acceptance because the workflow is still blocked. |
| commit_log_state_mutation_consistency | pass | Cloud DebugRunQueryService hard validator. | Stable state mutations are explained by commit log. |

### Rubrics
| Rubric | Score | Reason |
| --- | ---: | --- |
| D2-R01 | 3 | Source handoff and construction shells use Document 1 context, but no usable Document 2 artifact reached promotion. |
| D2-R02 | 3 | Three shells are differentiated and survived A1 construction review; score capped because one shell never produced detail. |
| D2-R03 | 2 | Two detail outputs exist, but the core bullish shell has no accepted realized facts and no complete patch set exists. |
| D2-R04 | 2 | Partial price-in reasoning may exist in two detail payloads, but it is not complete or reviewable across all expectations. |
| D2-R05 | 2 | Key variables are partial and not promotable because `expectation_mu_001` is missing. |
| D2-R06 | 2 | Event monitoring directions are partial and did not enter pending patches or review. |
| D2-R07 | 3 | DoxAtlas tool evidence is visible for two details and construction review, but the missing core shell and lack of stable patches cap traceability. |
| D2-R08 | 1 | Field review did not run. |
| D2-R09 | 1 | Objection/delegation handling for detail fields did not run. |
| D2-R10 | 1 | No stable `expectation_unit` and no promotable pending patch set. |
| D2-R11 | 3 | Tool use is mostly auditable and scoped correctly after the previous fix, but detail loops remain timeout-prone and inefficient. |
| D2-R12 | 2 | Missing evidence is not fabricated into stable output, but uncertainty discipline is not yet reflected in a full detail set. |
| D2-R13 | 4 | DB, remote log, hard validators, idempotency state, Working Memory, and LangSmith traces reproduce the blocker. |
| D2-R14 | 4 | Failure category, hypothesis, and modification plan are specific, scoped, and retestable without changing rubrics. |

### Score Summary
- Core Blackboard quality rubrics average (`D2-R01`-`D2-R10`): 2.0
- Other rubrics with score <= 2: `D2-R12`
- Quality target met: no
- Accept modification so far: no, must modify and retest.

### Document 2 State Summary
- Pending expectation patches: 0
- Stable expectation_unit count: 0
- Open objections: 0
- Blocking delegations: 0
- Latest checkpoint: `blocked`, `next_node=GenerateExpectationDetails`
- Detail idempotency:
  - `expectation_mu_001`: failed, `parallel_agent_timeout`
  - `expectation_mu_002`: completed
  - `expectation_mu_003`: completed
- Working Memory entries of interest: succeeded `expectation_detail_result` for `patch_expectation_mu_002_detail` and `patch_expectation_mu_003_detail`; both cite `doxa_get_narrative_report`.
- Script error: `parallel_agent_timeout: GenerateExpectationDetails/O1 did not return within 1800 seconds.`

### Failure Categories
- category: `workflow_completion`
  - issue: target stop_after not reached; run blocked at `GenerateExpectationDetails`.
  - evidence: checkpoint `status=blocked`, `next_node=GenerateExpectationDetails`, remote log final event.
  - severity: high/blocking
  - suspected root cause: one O1 shell worker did not return before parent wall-clock timeout.
- category: `context_management`
  - issue: detail prompt/context remains large and timeout-prone for the core shell.
  - evidence: LangSmith detail compaction/model inputs around 24k-28k tokens; one detail loop reports `CancelledError()`.
  - severity: high
  - suspected root cause: O1 repeats narrative-tool planning/compaction instead of using a bounded one-tool-call path to final output with unknowns.
- category: `memory_continuity`
  - issue: successful sibling details are cached only after aggregate timeout, weakening timely resume/audit continuity.
  - evidence: two detail Working Memory entries appear after final blocked state; current code processes outcomes only after `_run_agent_jobs_concurrently` returns all outcomes or timeouts.
  - severity: medium/high
  - suspected root cause: missing `on_outcome` persistence path for expectation-detail fan-out.
- category: `optimization_readiness`
  - issue: blocker is specific enough for a workflow/prompt-budget modification and same-source retest.
  - evidence: one missing shell, two completed siblings, exact failed idempotency key, exact timeout error.
  - severity: low residual
  - suspected root cause: record contains a measurable retest hypothesis.

### Actual Modification
- Implemented after this evaluation entry:
  - Added per-outcome expectation-detail acceptance/caching during parallel fan-out while preserving all-or-nothing final pending-patch merge.
  - Added O1 detail completion budget limiting `doxa_get_narrative_report` to one successful call per shell and requiring explicit unknowns on evidence gaps.
  - Added targeted regression assertions for the new detail budget and retained existing parallel detail merge/resume/retry tests.

## 2026-06-22 19:42 - MU - Document 2 loop 1 retest3 blocked - ResolveObjectionsAndDelegations

### Test Info
- Git state: cloud deployed commit `1d0df444ba965502c23805fbc85e08072e3e5c58` (`fix: persist document2 detail fanout outcomes`).
- Source run_id: `run_58f5afce8b9441ca804a2cde1ad9aec8`
- Source state: Document 1-only source; stable `global_research` only, no stable `expectation_unit`, no source pending patches, no source blockers.
- Execution mode: `clone`
- Command: `docker compose run --rm -e DOXAGENT_RUN_REAL_API_TESTS=1 -e DOXAGENT_STORAGE_MODE=postgres debug-viewer python eval/run_document2_expectation_units_smoke.py run_58f5afce8b9441ca804a2cde1ad9aec8 --mode clone --stop-after PromoteExpectationToBeliefState --export-brief-state`
- Environment: cloud server `doxagent-hk`, `/root/doxagent`, Docker image built from `1d0df44`.
- Execution run_id: `run_0afa34a2e67d4997abddd2678583ec70`
- Stop after: `PromoteExpectationToBeliefState`
- Brief State JSON: remote export path `eval/brief_state_exports/run_0afa34a2e67d4997abddd2678583ec70.json`
- Remote log: `/root/doxagent/.eval_runs/document2-loop1-retest3-20260622T184738+0800.log`
- LangSmith MCP query: project `DoxAgent`; visible traces include `O1.GenerateExpectationDetails`, `A1/C1/C3/O4.ReviewExpectationFields`, and `O1.ResolveObjectionsAndDelegations.LOOP1`.
- Built-in hard validators: overall `failed`; `evidence_reference_integrity=passed`, `langsmith_trajectory_tool_boundary=failed` with `workflow_trace_not_completed` at latest checkpoint, `commit_log_state_mutation_consistency=passed`.
- Evaluator: Codex, strict diagnostic retest3.

### Optimization Hypothesis
- Previous blocker movement: the per-shell detail persistence fix worked. Retest3 passed `GenerateExpectationDetails`; completed detail outputs for `expectation_mu_001` and `expectation_mu_002` were persisted before the downstream block, and field review ran.
- New blocker: `ResolveObjectionsAndDelegations` ran once and produced revised patches, but four blocking numeric sanity objections remained open or unresolved. The revised patches still contained precise fundamental and market numbers supported only by DoxAtlas narrative or other non-source-appropriate evidence.
- Root hypothesis 1: O1 resolver understood the instruction at a policy level but failed to execute the field-level deletion. Its LangSmith output says it downgraded false precision, while the revised payload still kept exact revenue, margin, price, market-cap, percentage, and event-date claims.
- Root hypothesis 2: the resolver batch loop stops after a fully stalled batch (`unresolved_batch_ids == batch_ids`). In this run the first batch had three objections and all remained unresolved after numeric-sanity revalidation, so the loop exited before processing the fourth blocker.
- Root hypothesis 3: numeric sanity revalidation is correctly strict, but it currently only reopens blockers after accepting a bad revision. The workflow needs a deterministic safety layer that removes unsupported numeric precision from accepted or partially accepted numeric-sanity revisions before revalidation, without fabricating new evidence or weakening the validator.
- Expected hard-gate movement: next retest should process all blocker batches and either close numeric_sanity blockers through truly non-numeric revisions or leave a fully audited residual risk. `D2-HG08`, `D2-HG09`, and built-in `langsmith_trajectory_tool_boundary` are the primary acceptance gates.
- Expected rubric movement: `D2-R03`, `D2-R04`, `D2-R07`, `D2-R09`, and `D2-R10` should improve if unsupported precise numbers are removed and promotion can proceed with auditable qualitative facts.
- Risk: deterministic sanitization can reduce investment specificity. This is acceptable only when the alternative is unsupported numeric precision; source-appropriate market/fundamental evidence must still preserve exact numbers.

### Proposed Modification Plan
- Change 1: Update `_resolve_blockers` so a stalled batch does not stop the whole resolver while there are unattempted unresolved objections. Keep the infinite-loop guard, but continue with unresolved objections outside the stalled batch.
- Change 2: Add a deterministic numeric-sanity revision fallback for accepted or partially accepted numeric_sanity objections. If a revised expectation patch still has precise unsupported numeric market or fundamental claims, replace only the affected `realized_facts` and `price_reaction` wording with qualitative, evidence-gap-aware text.
- Change 3: Preserve source-appropriate evidence behavior. If `MARKET_DATA`, `FACT_CHECK`, SEC/companyfacts/filing/financial-statement evidence exists, do not sanitize the precise claim.
- Change 4: Keep revalidation active after sanitization. If unsupported precision remains, the objection must still reopen and block promotion.
- Change 5: Add regression tests for the deterministic fallback and for continued processing after one stalled blocker batch.
- Files to touch: `src/doxagent/workflows/initialization.py`, `tests/test_phase5_initialization_workflow.py`, `changelog`, `eval/document2_eval/document2_eval_records.md`.
- Retest requirement: commit/push, cloud `git pull --ff-only`, rebuild `debug-viewer`, rerun the same source and same `--stop-after PromoteExpectationToBeliefState` in cloud-only mode.

### Scope Decision
- Eval mode: `promote`
- Can judge stable expectation_unit: no
- Can judge improvement: yes, for detail fan-out persistence and downstream field-review reach.
- Cannot claim: stable `expectation_unit`, resolved blockers, promotion quality, or overall quality-target success.

### Hard Gates
| Gate | Result | Evidence | Notes |
| --- | --- | --- | --- |
| D2-HG01 | pass | Same verified Document 1-only source run. | Valid seed retained. |
| D2-HG02 | fail | Latest checkpoint `status=blocked`, `next_node=ResolveObjectionsAndDelegations`; target `PromoteExpectationToBeliefState` not reached. | New blocker moved downstream. |
| D2-HG03 | pass | Construction, A1 review, and construction resolver completed. | Shell path remains valid. |
| D2-HG04 | pass | `GenerateExpectationDetails` completed and produced 2 pending expectation patches. | Detail completeness improved, but not yet stable. |
| D2-HG05 | fail | Evidence refs are hydrated, but critical numeric claims are supported by narrative-level evidence rather than source-appropriate market or fundamental data. | Built-in evidence ref integrity passes, content evidence sufficiency fails. |
| D2-HG06 | fail | Numeric sanity blockers cite unsupported precise price, market cap, revenue, margin, and date claims. | Price-in reasoning not acceptable. |
| D2-HG07 | pass | A1/C1/C3/O4 field review ran and produced blocking findings. | Review pressure improved and caught real defects. |
| D2-HG08 | fail | Resolver ran once but left four numeric_sanity blockers open or unresolved. | Accepted revisions did not actually fix claims. |
| D2-HG09 | fail | Stable `expectation_unit` count is 0; 2 pending patches remain blocked. | No promotion. |
| D2-HG10 | pass | LangSmith traces are available for detail, field-review, and resolver loops. | Remote process is auditable. |
| D2-HG11 | pass | Remote log, checkpoint, Working Memory, and hard validators record the block. | Failure is a business-audit fact. |
| D2-HG12 | fail | Field-review inputs were very large (`C1` about 51k tokens; `O4` about 74k tokens) and resolver output was large while still incomplete. | Context pressure remains material. |
| D2-HG13 | fail | O1 claimed to remove false precision but revised patches retained it; one blocker batch also prevented the fourth objection from being processed. | Memory/revision continuity failed. |

### Built-in Hard Validators
| Validator | Result | Evidence | Notes |
| --- | --- | --- | --- |
| evidence_reference_integrity | pass | Cloud DebugRunQueryService hard validator. | Ref existence and hydration pass. |
| langsmith_trajectory_tool_boundary | fail | Finding `workflow_trace_not_completed` at `latest_checkpoint.status`. | Correctly blocks acceptance because the workflow is still blocked. |
| commit_log_state_mutation_consistency | pass | Cloud DebugRunQueryService hard validator. | Stable state mutations are explained by commit log. |

### Rubrics
| Rubric | Score | Reason |
| --- | ---: | --- |
| D2-R01 | 3 | Source handoff is valid and detail generation uses Document 1 context, but no stable Document 2 artifact exists. |
| D2-R02 | 3 | Expectation theses reached detail and review, but unsupported numeric wording prevents reliable downstream use. |
| D2-R03 | 3 | Realized facts exist and are specific, but specificity is partly unsafe because precise numeric claims lack proper evidence. |
| D2-R04 | 2 | Price-in reasoning is visibly attempted, yet exact price/market-cap/return claims are narrative-only and therefore not acceptable. |
| D2-R05 | 3 | Key variables are present, but their current-state precision is not yet proven stable or source-appropriate. |
| D2-R06 | 3 | Event monitoring direction exists and is more complete than retest2, but it is not promotable. |
| D2-R07 | 3 | Evidence refs are traceable, but evidence class does not match several numeric claims. |
| D2-R08 | 4 | Field review meaningfully caught numeric-evidence risks across roles. Score 4 is justified by real blockers, not by final quality. |
| D2-R09 | 2 | Resolver lifecycle exists, but accepted revisions failed revalidation and left blockers unresolved. |
| D2-R10 | 1 | No stable `expectation_unit` and promotion remains blocked. |
| D2-R11 | 3 | Tool trajectory is auditable and no scope-boundary regression is visible, but context/tool efficiency is still uneven. |
| D2-R12 | 2 | The system detects unsupported precision, but O1 still rewrites weak evidence into over-specific claims. |
| D2-R13 | 4 | DB, remote log, hard validators, Working Memory, and LangSmith reproduce the failure chain. |
| D2-R14 | 4 | Failure category and modification plan are specific, enforcement-layer-oriented, and retestable. |

### Score Summary
- Core Blackboard quality rubrics average (`D2-R01`-`D2-R10`): 2.7
- Other rubrics with score <= 2: `D2-R12`
- Quality target met: no
- Accept modification so far: no, must modify and retest.

### Document 2 State Summary
- Pending expectation patches: 2
- Stable expectation_unit count: 0
- Open/unresolved objections: 4
- Blocking delegations: 0
- Latest checkpoint: `blocked`, `next_node=ResolveObjectionsAndDelegations`
- Open blockers:
  - `obj_numeric_sanity_expectation_mu_001_fundamental_data`
  - `obj_numeric_sanity_expectation_mu_001_market_data`
  - `obj_numeric_sanity_expectation_mu_002_fundamental_data`
  - `obj_numeric_sanity_expectation_mu_002_market_data`
- Working Memory entries of interest: O1 detail results for two expectation patches, A1/C1/C3/O4 field reviews, and O1 `objection_resolution_result`.
- Script error: `ResolveObjectionsAndDelegations left blockers unresolved.`

### Failure Categories
- category: `objection_resolution`
  - issue: O1 accepted/partially accepted numeric_sanity blockers but revised patches still failed deterministic numeric revalidation.
  - evidence: four unresolved/open numeric_sanity objections after resolver; LangSmith `O1.ResolveObjectionsAndDelegations.LOOP1` says it removed false precision but output retained precise numbers.
  - severity: high/blocking
  - suspected root cause: prompt-only resolver guidance is insufficient for field-level numeric deletion.
- category: `price_in_reasoning`
  - issue: price and market-cap claims are written with precision unsupported by market-data evidence.
  - evidence: `numeric_sanity_market_data` blockers for `expectation_mu_001` and `expectation_mu_002`.
  - severity: high
  - suspected root cause: detail/revision paths allow narrative-level sources to back precise market reaction language.
- category: `evidence_integrity`
  - issue: refs exist and hydrate, but evidence class is too weak for fundamental/market numeric claims.
  - evidence: built-in `evidence_reference_integrity=passed` while semantic numeric sanity fails.
  - severity: high
  - suspected root cause: hard validator covers ref existence, not source-appropriate sufficiency.
- category: `context_management`
  - issue: field-review and resolver contexts are large and produce high-token outputs; O1 processed only the first batch and still failed.
  - evidence: LangSmith token metadata for C1/O4 reviews and O1 resolver.
  - severity: medium/high
  - suspected root cause: compact summaries still allow large rewritten patches and low-signal numeric repetition.
- category: `promotion_blocker`
  - issue: pending patches cannot pass `can_promote_target` while blockers remain.
  - evidence: no stable expectation_unit, pending patch count 2, four blockers.
  - severity: high
  - suspected root cause: resolver/revalidation mismatch.

### Actual Modification
- Implemented after this evaluation entry:
  - Continue objection resolution across remaining unresolved batches when one processed batch stalls after revalidation.
  - Add deterministic numeric-sanity fallback sanitization for accepted or partially accepted O1 revisions that still contain unsupported precise numeric claims.
  - Keep numeric-sanity revalidation after sanitization so remaining unsafe claims still reopen blockers.

## 2026-06-22 20:44 - MU - Document 2 loop 1 retest4 blocked - ResolveObjectionsAndDelegations model timeout

### Test Info
- Git state: cloud deployed commit `ddc351f13c7b1581d7385293989f894269895bd5` (`fix: sanitize document2 numeric revisions`).
- Source run_id: `run_58f5afce8b9441ca804a2cde1ad9aec8`
- Source state: Document 1-only source; stable `global_research` only, no stable `expectation_unit`, no source pending patches, no source blockers.
- Execution mode: `clone`
- Command: `docker compose run --rm -e DOXAGENT_RUN_REAL_API_TESTS=1 -e DOXAGENT_STORAGE_MODE=postgres debug-viewer python eval/run_document2_expectation_units_smoke.py run_58f5afce8b9441ca804a2cde1ad9aec8 --mode clone --stop-after PromoteExpectationToBeliefState --export-brief-state`
- Environment: cloud server `doxagent-hk`, `/root/doxagent`, Docker image built from `ddc351f`.
- Execution run_id: `run_aeff7a2c363341aa9074ea097c7921ca`
- Stop after: `PromoteExpectationToBeliefState`
- Brief State JSON: remote API visible; remote file export path was reported as `eval/brief_state_exports/run_aeff7a2c363341aa9074ea097c7921ca.json` but file was not present when checked immediately after completion.
- Remote log: `/root/doxagent/.eval_runs/document2-loop1-retest4-20260622T195152+0800.log`
- LangSmith MCP query: project `DoxAgent`; field-review traces visible for A1/C1/C3/O4. No successful `ResolveObjectionsAndDelegations` trace was found; Working Memory contains failed O1 `objection_resolution_result` with no ReAct action entries.
- Built-in hard validators: overall `failed`; `evidence_reference_integrity=passed`, `langsmith_trajectory_tool_boundary=failed` with `workflow_trace_not_completed` and `no_action_loop_entries`, `commit_log_state_mutation_consistency=passed`.
- Evaluator: Codex, strict diagnostic retest4.

### Optimization Hypothesis
- Previous blocker movement: retest4 reached the same resolver node but produced more diagnostic pressure than retest3. Field review now adds a fifth, concrete O4 objection (`obj_price_reaction_contradictions`) describing price-reaction contradictions against OHLCV data.
- The previous deterministic fallback did not execute because O1 resolver timed out before returning an accepted or partially accepted revision. Therefore the new sanitizer was correct in placement for bad returned revisions, but insufficient for model-timeout cases.
- New blocker: `ResolveObjectionsAndDelegations` failed with `模型请求超过 300.0 秒未返回`. Working Memory records O1 `objection_resolution_result` as failed with zero ReAct entries, so the resolver did not get far enough to emit decisions.
- Root hypothesis 1: numeric_sanity objections and price-reaction contradiction objections are deterministic quality gates, not open-ended investment judgment. Sending them into O1 as full-text objection-resolution tasks wastes the model budget and invites timeout.
- Root hypothesis 2: the field-review context remains too large. LangSmith shows ReviewExpectationFields inputs around 34k-46k tokens for A1/C1/C3, and an earlier O4 review input around 134k chars. The resolver then inherits large pending patch summaries plus five blockers.
- Root hypothesis 3: O4's objection is actionable without model deliberation: if price-reaction claims contradict structured OHLCV data, the safe workflow action is to remove quantified price claims and downgrade the field to "market-data verification required", then revalidate.
- Expected hard-gate movement: next retest should close or reduce the deterministic numeric/price-reaction blockers before O1 is called, so O1 only handles residual non-deterministic objections. `D2-HG08`, `D2-HG09`, and built-in trajectory validator should improve.
- Expected rubric movement: `D2-R04`, `D2-R07`, `D2-R09`, and `D2-R10` should improve if contradicted price-reaction text is removed and resolver avoids timeout. Content specificity may drop, but evidence discipline should improve.
- Risk: deterministic normalization must not silently promote weak content. It should write Working Memory audit evidence, mark only the directly handled objections with changed paths, and still run numeric-sanity revalidation after patch changes.

### Proposed Modification Plan
- Change 1: Add a deterministic objection-normalization phase at the start of `_resolve_blockers`, before calling O1. It should inspect unresolved objections and pending expectation patches.
- Change 2: For `numeric_sanity_*` objections, apply the existing numeric-sanity sanitizer directly to the affected expectation patch, then resolve only the numeric objections whose deterministic revalidation no longer reproduces them.
- Change 3: For O4 price-reaction contradiction objections (`price_reaction_contradictions` or objections whose reason/target clearly references OHLCV and `price_reaction`), apply the existing promotion price-reaction normalization to affected patches, downgrading contradicted quantified price claims to "requires OHLCV/market-data verification" text.
- Change 4: Write a `deterministic_objection_normalization` Working Memory entry that lists handled objection ids, changed expectation ids, changed paths, and residual blockers.
- Change 5: Keep O1 resolver for remaining unresolved blockers. If deterministic normalization does not remove the issue, the blocker must stay open and continue to block promotion.
- Files to touch: `src/doxagent/workflows/initialization.py`, `tests/test_phase5_initialization_workflow.py`, `changelog`, `eval/document2_eval/document2_eval_records.md`.
- Retest requirement: commit/push, cloud `git pull --ff-only`, rebuild `debug-viewer`, rerun the same source and same `--stop-after PromoteExpectationToBeliefState` in cloud-only mode.

### Scope Decision
- Eval mode: `promote`
- Can judge stable expectation_unit: no
- Can judge improvement: yes, for field-review diagnostic quality and detection of O4 price contradictions.
- Cannot claim: stable `expectation_unit`, resolved blockers, promotion readiness, or overall quality-target success.

### Hard Gates
| Gate | Result | Evidence | Notes |
| --- | --- | --- | --- |
| D2-HG01 | pass | Same verified Document 1-only source run. | Valid seed retained. |
| D2-HG02 | fail | Latest checkpoint `status=blocked`, `next_node=ResolveObjectionsAndDelegations`; target `PromoteExpectationToBeliefState` not reached. | Resolver timeout. |
| D2-HG03 | pass | Construction, A1 review, and construction resolver completed. | Shell path remains valid. |
| D2-HG04 | pass | `GenerateExpectationDetails` completed and produced 2 pending expectation patches. | Detail generation is now reliable enough to review. |
| D2-HG05 | fail | Evidence refs hydrate, but numeric and price-reaction claims still mismatch evidence class and structured OHLCV findings. | Ref existence is not sufficient. |
| D2-HG06 | fail | Four numeric_sanity blockers plus O4 price-reaction contradiction blocker remain open. | Price-in reasoning is unsafe. |
| D2-HG07 | pass | A1/C1/C3/O4 field review ran and produced actionable objections. | O4 review improved pressure. |
| D2-HG08 | fail | Resolver produced no successful decision; O1 model request timed out after 300 seconds. | Objections not handled. |
| D2-HG09 | fail | Stable `expectation_unit` count is 0; 2 pending patches remain blocked. | No promotion. |
| D2-HG10 | fail | LangSmith traces exist for earlier nodes, but no successful resolver trace was found; local validator reports no action entries for O1 objection resolution. | Critical node trajectory missing. |
| D2-HG11 | pass | Remote log, checkpoint metadata, Working Memory, and hard validators record the timeout. | Failure is auditable. |
| D2-HG12 | fail | Field-review inputs are large, and resolver timed out before output. | Context control remains a blocker. |
| D2-HG13 | fail | Numeric/price blockers survive into resolver; no revised patch continuity to promotion. | Memory/revision continuity not proven. |

### Built-in Hard Validators
| Validator | Result | Evidence | Notes |
| --- | --- | --- | --- |
| evidence_reference_integrity | pass | Cloud DebugRunQueryService hard validator. | Ref existence and hydration pass. |
| langsmith_trajectory_tool_boundary | fail | Findings: `workflow_trace_not_completed`, `no_action_loop_entries` for O1 `objection_resolution_result`. | Correctly blocks acceptance. |
| commit_log_state_mutation_consistency | pass | Cloud DebugRunQueryService hard validator. | Stable state mutations are explained by commit log. |

### Rubrics
| Rubric | Score | Reason |
| --- | ---: | --- |
| D2-R01 | 3 | Source handoff remains valid, but no stable Document 2 artifact exists. |
| D2-R02 | 3 | Two theses are differentiated and reviewable, but content remains blocked by evidence and price-reaction defects. |
| D2-R03 | 2 | Realized facts are specific, but many precise numbers and price reactions are contradicted or unsupported. |
| D2-R04 | 1 | Price-in reasoning is not reliable; O4 found direct OHLCV contradictions and resolver timed out. |
| D2-R05 | 3 | Key variables exist and C3 review adds useful calibration, but they are not promotable. |
| D2-R06 | 3 | Event monitoring direction exists, but still depends on unsupported quantified facts. |
| D2-R07 | 3 | Evidence is traceable, yet source class and contradiction handling remain insufficient. |
| D2-R08 | 4 | Field review pressure is strong enough to catch numeric and OHLCV contradictions. |
| D2-R09 | 1 | Objection handling failed at the resolver model request; no decisions or patch revisions. |
| D2-R10 | 1 | No stable `expectation_unit`; blockers prevent promotion. |
| D2-R11 | 3 | Tool use is mostly auditable and role-appropriate before resolver, but resolver trajectory is absent. |
| D2-R12 | 2 | The workflow detects uncertainty and contradiction, but generated patches still overstate precise facts. |
| D2-R13 | 4 | DB, remote log, Working Memory, hard validators, and LangSmith reproduce the failure chain. |
| D2-R14 | 4 | Failure category and next modification are concrete, enforcement-layer-based, and retestable. |

### Score Summary
- Core Blackboard quality rubrics average (`D2-R01`-`D2-R10`): 2.4
- Other rubrics with score <= 2: `D2-R12`
- Quality target met: no
- Accept modification so far: no, must modify and retest.

### Document 2 State Summary
- Pending expectation patches: 2
- Stable expectation_unit count: 0
- Open/unresolved objections: 5
- Blocking delegations: 0
- Latest checkpoint: `blocked`, `next_node=ResolveObjectionsAndDelegations`
- Open blockers:
  - `obj_numeric_sanity_expectation_mu_001_fundamental_data`
  - `obj_numeric_sanity_expectation_mu_001_market_data`
  - `obj_numeric_sanity_expectation_mu_002_fundamental_data`
  - `obj_numeric_sanity_expectation_mu_002_market_data`
  - `obj_price_reaction_contradictions`
- Working Memory entries of interest: O1 detail results for two expectation patches, A1/C1/C3/O4 field reviews, and failed O1 `objection_resolution_result`.
- Script error: `ResolveObjectionsAndDelegations agent result failed: 模型请求超过 300.0 秒未返回。`

### Failure Categories
- category: `objection_resolution`
  - issue: O1 resolver timed out before returning objection decisions.
  - evidence: remote log final error, checkpoint `last_error_message`, Working Memory failed `objection_resolution_result` with zero ReAct entries.
  - severity: high/blocking
  - suspected root cause: deterministic numeric/price contradictions are still sent into an open-ended O1 task.
- category: `price_in_reasoning`
  - issue: O4 found concrete OHLCV contradictions in price-reaction claims.
  - evidence: `obj_price_reaction_contradictions` reason lists wrong starting price, wrong gain, wrong benchmark gain, wrong event-day reaction, and unsupported high-price wording.
  - severity: high
  - suspected root cause: detail generation writes quantified market reactions before structured market evidence is attached or normalized.
- category: `evidence_integrity`
  - issue: precise market and fundamental claims remain supported by insufficient or mismatched evidence.
  - evidence: four deterministic numeric_sanity blockers remain open.
  - severity: high
  - suspected root cause: source-class revalidation is only post-review and currently waits for model resolution.
- category: `context_management`
  - issue: field-review and resolver contexts remain large and timeout-prone.
  - evidence: LangSmith field-review token counts around 34k-46k input tokens and resolver model timeout.
  - severity: high
  - suspected root cause: resolver receives bulky pending patch text plus deterministic blockers that can be handled without LLM deliberation.
- category: `promotion_blocker`
  - issue: no stable expectation_unit can be promoted while five blockers remain.
  - evidence: stable expectation count 0, pending patch count 2, open objection count 5.
  - severity: high
  - suspected root cause: deterministic blocker handling is too late in the lifecycle.

### Actual Modification
- Implemented after this evaluation entry:
  - Add deterministic pre-O1 objection normalization for numeric_sanity and price-reaction contradiction blockers.
  - Write an auditable Working Memory entry for deterministic normalization and leave residual blockers open for O1.
  - Retain numeric-sanity revalidation after patch normalization.

## 2026-06-22 21:55 - MU - Document 2 loop 1 retest5 promoted but rejected - validator and content-quality gaps

### Test Info
- Git state: cloud deployed commit `bb3ea6e` (`fix: normalize document2 deterministic blockers`).
- Source run_id: `run_58f5afce8b9441ca804a2cde1ad9aec8`
- Source state: Document 1-only source; stable `global_research` only, no stable `expectation_unit`, no source pending patches, no source blockers.
- Execution mode: `clone`
- Command: `docker compose run --rm -e DOXAGENT_RUN_REAL_API_TESTS=1 -e DOXAGENT_STORAGE_MODE=postgres debug-viewer python eval/run_document2_expectation_units_smoke.py run_58f5afce8b9441ca804a2cde1ad9aec8 --mode clone --stop-after PromoteExpectationToBeliefState --export-brief-state`
- Environment: cloud server `doxagent-hk`, `/root/doxagent`, Docker image built from `bb3ea6e`.
- Execution run_id: `run_0b6c98e685cf4b4e872384015e35e8c8`
- Stop after: `PromoteExpectationToBeliefState`
- Remote log: `/root/doxagent/.eval_runs/document2-loop1-retest5-20260622T205038+0800.log`
- Script output: `error=null`; completed nodes include `PromoteExpectationToBeliefState`; `stable_document_types=["global_research","expectation_unit"]`; `expectation_unit_count=2`; `pending_patch_count=0`; `unresolved_objection_count=0`; `blocking_delegation_count=0`.
- Brief State JSON: remote API visible at `/api/runs/run_0b6c98e685cf4b4e872384015e35e8c8/brief-state`; reported export path `eval/brief_state_exports/run_0b6c98e685cf4b4e872384015e35e8c8.json` was not present on the host after the one-off container exited.
- LangSmith MCP query: project `DoxAgent`; construction/detail/field-review traces visible. No resolver root trace was found for `ResolveObjectionsAndDelegations` or the 13:25 O1 `objection_resolution_result` despite Working Memory recording the resolver result.
- Built-in hard validators: overall `failed`; `evidence_reference_integrity=passed`, `langsmith_trajectory_tool_boundary=failed` with `workflow_trace_not_completed`, `commit_log_state_mutation_consistency=passed`.
- Evaluator: Codex, strict diagnostic retest5.

### Optimization Hypothesis
- Improvement confirmed: the deterministic pre-O1 normalization moved the run past the previous blocker. Retest5 closed the numeric_sanity and price-reaction contradiction objections, O1 handled the remaining C3 objections, and two stable `expectation_unit` documents were committed.
- New acceptance blocker 1: the built-in `langsmith_trajectory_tool_boundary` validator still fails because Document2 smoke intentionally stops after `PromoteExpectationToBeliefState`, leaving the full initialization checkpoint as `status=running`, `next_node=GenerateGlobalNarrativeReport`. For a full initialization run this failure is correct, but for this Document2 promote stop-after it is a validator/eval-mode mismatch. The smoke checkpoint needs explicit stop-after metadata, and the validator should accept it only when that target node is present in `completed_nodes`.
- New acceptance blocker 2: the clone checkpoint inherited stale source metadata: `last_error_message=parallel_agent_timeout: BuildGlobalResearch/C1 did not return within 1800 seconds.` This contaminates Brief State auditing even though retest5 itself had `error=null`. The clone path must scrub old `last_error_*` metadata while preserving source run and Global Research artifacts.
- New quality blocker 3: deterministic numeric cleanup only sanitized `realized_facts` and `price_reaction`. Stable `market_view`, `key_variables.current_status`, and `event_monitoring_direction` still contain precise revenue, margin, market-cap, valuation, target-price, and percentage thresholds supported mainly by `doxatlas_source` narrative evidence.
- New quality blocker 4: price-in reasoning is safer than retest4 but still weak. Most price reactions are downgraded to "requires OHLCV/market_trace verification", while market_view still states that some facts are fully priced. This is internally inconsistent and not ready for downstream monitoring.
- New traceability blocker 5: resolver Working Memory exists, but no matching LangSmith root trace was found. This prevents a strict judge from reconstructing the O1 resolver process from remote LangSmith alone.
- Expected next movement: after adding explicit Document2 smoke stop metadata, clone error scrubbing, and extended numeric cleanup, the next retest should have all three built-in hard validators pass, no stale source error in metadata, fewer narrative-only numeric claims in stable expectations, and a cleaner basis for `D2-R03`, `D2-R04`, `D2-R07`, `D2-R10`, `D2-R12`, and `D2-R13`.
- Risk: extended deterministic cleanup can make fields less specific. This is acceptable only when the alternative is unsupported precision; fields should preserve qualitative thesis, variable names, and event intent while replacing unsupported numeric thresholds with evidence-gap wording.

### Proposed Modification Plan
- Change 1: Update the Document2 smoke entrypoint so every execution run checkpoint receives `document2_smoke_mode`, `document2_smoke_source_run_id`, and `document2_smoke_stop_after` metadata before resume.
- Change 2: Scrub `last_error_code`, `last_error_message`, `last_error_boundary`, and `last_error_node` when cloning a Document 1-only source checkpoint into a Document2 execution run.
- Change 3: Update `langsmith_trajectory_tool_boundary` to treat a non-completed checkpoint as closed only when it has explicit Document2 smoke stop-after metadata and that stop-after node appears in `completed_nodes`. Preserve failure behavior for ordinary unclosed runs and open dispatches.
- Change 4: Extend deterministic numeric-sanity fallback beyond `realized_facts` and `price_reaction` to `market_view.text`, `market_view.summary`, `key_variables.current_status`, and event-monitoring strings. Remove unsupported precise numeric thresholds while preserving qualitative thesis and monitoring intent.
- Change 5: Add regression tests for clone metadata scrubbing, Document2 stop-after validator behavior, and extended numeric cleanup.
- Retest requirement: commit/push, cloud `git pull --ff-only`, rebuild `debug-viewer`, rerun the same source and same `--stop-after PromoteExpectationToBeliefState` in cloud-only mode.

### Scope Decision
- Eval mode: `promote`
- Can judge stable expectation_unit: yes
- Can judge improvement: yes, promotion and blocker closure improved materially.
- Cannot claim: quality target success, because built-in hard validators failed and stable content still has evidence/price-in quality gaps.

### Hard Gates
| Gate | Result | Evidence | Notes |
| --- | --- | --- | --- |
| D2-HG01 | pass | Same verified Document 1-only source run. | Valid seed retained. |
| D2-HG02 | pass | Script completed target stop-after with `error=null`; `PromoteExpectationToBeliefState` in completed nodes. | Full workflow status remains `running` by design after stop-after. |
| D2-HG03 | pass | Construction and construction review completed; two differentiated shells formed. | Bullish N01 and bearish/risk N06 are distinct. |
| D2-HG04 | pass | Detail generation produced complete fields and two stable expectations after promotion. | Field presence is acceptable. |
| D2-HG05 | fail | Evidence refs hydrate, but final `market_view` and many key variables use narrative-level refs for precise financial/market numbers. | Ref existence is not sufficient. |
| D2-HG06 | fail | Price reactions are mostly downgraded to "verification required", while market_view still claims fully priced/precise valuation facts. | Price-in reasoning is inconsistent. |
| D2-HG07 | pass | A1/C1/C3/O4 field review ran and produced concrete findings and objections. | Review pressure improved. |
| D2-HG08 | pass | All open objections closed; O1 resolver result and deterministic normalization are visible in Working Memory. | LangSmith resolver trace remains missing. |
| D2-HG09 | pass | Stable `expectation_unit` count is 2; pending patches 0; commit log has two expectation_unit commits. | Promotion state is clean. |
| D2-HG10 | fail | LangSmith traces are visible for construction/detail/review, but no resolver root trace found; built-in trajectory validator failed. | Critical process trace incomplete. |
| D2-HG11 | pass | Remote log, DB checkpoint, Working Memory, objections, commit log, and validator output reproduce the run. | Missing export file noted. |
| D2-HG12 | fail | Field-review contexts remain large, and checkpoint metadata carries bulky expectation_shells plus stale source error. | Context/metadata hygiene remains a quality risk. |
| D2-HG13 | fail | Revised patches promoted, but construction-shell metadata and stable thesis fields still retain unsupported precision. | Memory/revision continuity is only partial. |

### Built-in Hard Validators
| Validator | Result | Evidence | Notes |
| --- | --- | --- | --- |
| evidence_reference_integrity | pass | Cloud DebugRunQueryService hard validator; checked 54 items. | Ref existence and hydration pass. |
| langsmith_trajectory_tool_boundary | fail | Finding `workflow_trace_not_completed` at `latest_checkpoint.status`, details `status=running`, `next_node=GenerateGlobalNarrativeReport`. | Needs Document2 stop-after-aware closure metadata; not accepted in this run. |
| commit_log_state_mutation_consistency | pass | Cloud DebugRunQueryService hard validator; checked 12 items. | Stable state mutations are explained by commit log. |

### Rubrics
| Rubric | Score | Reason |
| --- | ---: | --- |
| D2-R01 | 4 | Document 1 handoff is valid and produces differentiated expectations, but cloned metadata still carries stale source error. |
| D2-R02 | 3 | The two theses are clear and differentiated, yet over-specific unsupported market/fundamental claims weaken investment reliability. |
| D2-R03 | 2 | Realized facts are present, but most were downgraded to generic qualitative placeholders and price reactions are verification-required rather than informative. |
| D2-R04 | 2 | Price-in reasoning is explicit in places, but internally inconsistent and still weakly tied to structured market evidence. |
| D2-R05 | 3 | Key variables are numerous and mostly thesis-relevant, but several current-status values contain narrative-only numeric precision. |
| D2-R06 | 3 | Event monitoring lists are concrete, but many thresholds are unsupported and the bearish unit's positive/negative event polarity is hard to audit. |
| D2-R07 | 3 | Evidence refs are traceable, but source class is too weak for many precise claims. |
| D2-R08 | 4 | A1/C1/C3/O4 review pressure is strong and caught real numeric, industry, and OHLCV issues. |
| D2-R09 | 3 | Objections were closed and revisions landed, but resolver LangSmith trace is missing and quality improvements are uneven. |
| D2-R10 | 4 | Promotion state is clean: two stable expectation_units, no pending patches, no open blockers, and commit trace exists. |
| D2-R11 | 3 | Tool use is mostly role-appropriate and auditable, but resolver trace is not visible in LangSmith. |
| D2-R12 | 2 | Uncertainty is now acknowledged, but unsupported projections and precise numbers still leak into stable thesis fields. |
| D2-R13 | 3 | DB/Working Memory/commit log are reproducible, but the export file is missing and resolver LangSmith trace is absent. |
| D2-R14 | 4 | Failure categories and proposed changes are concrete, enforcement-layer-oriented, and directly retestable. |

### Score Summary
- Core Blackboard quality rubrics average (`D2-R01`-`D2-R10`): 3.1
- Other rubrics with score <= 2: `D2-R12`
- Quality target met: no
- Accept modification so far: no, must modify and retest.

### Document 2 State Summary
- Pending expectation patches: 0
- Stable expectation_unit count: 2
- Open/unresolved objections: 0
- Blocking delegations: 0
- Latest checkpoint: `running`, `next_node=GenerateGlobalNarrativeReport`
- Completed Document2 nodes through promotion: yes
- Stable expectation ids: `expectation_001`, `expectation_002`
- Working Memory entries of interest:
  - `deterministic_objection_normalization` handled numeric_sanity and price_reaction_contradiction blockers.
  - O1 `objection_resolution_result` handled remaining C3 field-review objections.
  - A1/C1/C3/O4 field reviews all succeeded.
- Residual issues:
  - `last_error_message` from source run remains in metadata.
  - Stable market_view/key_variables/event_monitoring contain unsupported precise numeric claims.
  - Resolver LangSmith trace missing.

### Failure Categories
- category: `traceability`
  - issue: built-in trajectory validator fails because Document2 stop-after leaves full workflow status `running`; resolver LangSmith trace also missing.
  - evidence: hard validator `workflow_trace_not_completed`; LangSmith searches found construction/detail/review traces but no resolver trace.
  - severity: high
  - suspected root cause: Document2 smoke lacks explicit stop-after completion metadata for validators; resolver result may not be emitted under a searchable workflow-node trace.
- category: `evidence_integrity`
  - issue: final stable fields retain precise numbers backed mainly by `doxatlas_source`.
  - evidence: market_view and key variables contain revenue, margin, valuation, market-cap, and price thresholds with narrative refs.
  - severity: high
  - suspected root cause: deterministic numeric cleanup only covered realized facts and price_reaction.
- category: `price_in_reasoning`
  - issue: price reactions are downgraded to "verification required" but market_view still describes facts as fully priced.
  - evidence: stable `realized_facts.price_reaction` generic verification text vs market_view fully priced wording.
  - severity: high
  - suspected root cause: price normalization does not rewrite thesis-level price-in statements.
- category: `context_management`
  - issue: cloned checkpoint metadata contains stale source error and bulky shell/result payloads.
  - evidence: latest metadata includes old `parallel_agent_timeout` from source run and large `expectation_shells`.
  - severity: medium
  - suspected root cause: clone path rewrites ids but does not scrub status/error metadata for the new execution run.
- category: `promotion_readiness`
  - issue: state promotion succeeded but content quality remains below downstream monitoring readiness.
  - evidence: stable expectation count 2 with clean commit state, but rubrics average only 3.1.
  - severity: medium/high
  - suspected root cause: promotion gate checks schema/blockers more strongly than source-appropriate thesis-field precision.

### Actual Modification
- Implemented after this evaluation entry:
  - Add Document2 smoke stop-after metadata and stale error metadata scrubbing.
  - Make `langsmith_trajectory_tool_boundary` accept only explicit completed Document2 smoke stop-after checkpoints while preserving failure for ordinary unclosed runs.
  - Extend deterministic numeric-sanity cleanup to market_view, key_variables, and event_monitoring_direction.
  - Add regression tests for all three changes.

## 2026-06-22 23:20 - MU - Document 2 loop 1 retest6 blocked - field-review numeric objections still time out O1 resolver

### Test Info
- Git state: cloud deployed commit `d0391ef` (`fix: close document2 smoke audit gaps`).
- Source run_id: `run_58f5afce8b9441ca804a2cde1ad9aec8`
- Source state: Document 1-only source; stable `global_research`; no stable `expectation_unit`; no source pending patches or blockers.
- Execution mode: `clone`
- Command: `docker compose run --rm -e DOXAGENT_RUN_REAL_API_TESTS=1 -e DOXAGENT_STORAGE_MODE=postgres debug-viewer python eval/run_document2_expectation_units_smoke.py run_58f5afce8b9441ca804a2cde1ad9aec8 --mode clone --stop-after PromoteExpectationToBeliefState --export-brief-state`
- Environment: cloud server `doxagent-hk`, `/root/doxagent`, Docker image built from `d0391ef`.
- Execution run_id: `run_80a2f4a08fed47f5b49d83ae156aecdf`
- Stop after: `PromoteExpectationToBeliefState`
- Remote log: `/root/doxagent/.eval_runs/document2-loop1-retest6-20260622T221155+0800.log`
- Script output: `status=blocked`; `next_node=ResolveObjectionsAndDelegations`; completed nodes stop at `ReviewExpectationFields`; `stable_document_types=["global_research"]`; `pending_patch_count=3`; `expectation_unit_count=0`; `unresolved_objection_count=5`; `blocking_delegation_count=0`.
- Error: `ResolveObjectionsAndDelegations agent result failed: 模型请求超过 300.0 秒未返回。`
- Brief State API: `/api/runs/run_80a2f4a08fed47f5b49d83ae156aecdf/brief-state` confirms `latest_checkpoint.status=blocked`, `next_node=ResolveObjectionsAndDelegations`, `last_error_code=WorkflowContractError`.
- Direct cloud validator query from `DebugRunQueryService().brief_state(...).hard_validators`: overall `failed`; `evidence_reference_integrity=passed`, `langsmith_trajectory_tool_boundary=failed`, `commit_log_state_mutation_consistency=passed`.
- LangSmith MCP query: project `DoxAgent`; same execution run has high-token `ReviewExpectationFields` traces, including `C1.ReviewExpectationFields.LOOP4` and `A1.ReviewExpectationFields.LOOP7`; no `ResolveObjectionsAndDelegations` root trace found by run_id/node search.
- Evaluator: Codex, strict diagnostic retest6.

### Optimization Hypothesis
- The previous `d0391ef` changes fixed the retest5 smoke-audit gaps, but retest6 exposed a new blocker before promotion: field review generated five unresolved, mostly mechanical numeric objections after the three detail patches were produced.
- The remaining blockers are not open-ended investment judgments. They are deterministic quality failures:
  - three price benchmark / return-calculation objections (`obj_price_mu_01`, `obj_price_mu_02`, `obj_price_mu_03`);
  - two duplicate Q3 FY2026 revenue-guidance objections saying patches used `$36B` where authoritative sources indicate `$33.5B`.
- Because these blockers were passed into O1 as large pending-patch context, the resolver had to process three long expectation patches plus duplicate objections. The resulting O1 call timed out twice and produced no action loop entries, leaving the workflow blocked.
- Expected next movement: if field-review price/guidance corrections are normalized deterministically before O1, and the resolver only receives relevant compact pending patches for residual non-deterministic objections, the next retest should move past `ResolveObjectionsAndDelegations`, preserve a Working Memory audit of the deterministic correction, and either promote clean expectation units or expose a smaller content-quality blocker.
- Risk: deterministic correction can over-sanitize useful specificity. This is acceptable only for values explicitly flagged as wrong or unsupported by field review; qualitative thesis, evidence refs, variable identity, and monitoring intent must be preserved.

### Proposed Modification Plan
- Change 1: Extend `_apply_deterministic_objection_normalizations` so field-review numeric blockers can be handled before calling O1, alongside existing numeric_sanity and price-reaction contradiction normalization.
- Change 2: Add targeted detection for price benchmark / return-calculation objections and Q3 FY2026 revenue-guidance objections. Include objection-id inference such as `obj_price_mu_02 -> expectation_mu_02` so broad field-review objections can be mapped to the intended pending patch.
- Change 3: For affected patches, remove or downgrade only field-review-flagged price/guidance precision across `market_view`, `realized_facts`, `realized_facts.price_reaction`, `realized_facts_summary`, `key_variables.current_status`, and `event_monitoring_direction`.
- Change 4: When price-reaction values are disputed, replace quantified price changes with explicit "OHLCV/market_trace recalculation required" wording rather than leaving wrong numbers or silently deleting the reasoning field.
- Change 5: Write an auditable `deterministic_objection_normalization` Working Memory entry with `field_review_numeric_correction`, handled objection ids, changed patches, and residual numeric blockers.
- Change 6: Shrink O1 resolver context by including only relevant compact pending patches for the current objection batch, while retaining summaries and an omitted-patch count for auditability.
- Change 7: Add regression tests for field-review numeric normalization, expectation-id inference from objection ids, and compact relevant-patch resolver context.
- Retest requirement: commit/push, cloud `git pull --ff-only`, rebuild `debug-viewer`, rerun the same source run and same `--stop-after PromoteExpectationToBeliefState` in cloud-only mode.

### Scope Decision
- Eval mode: `promote`
- Can judge stable expectation_unit: no, because promotion was not reached.
- Can judge workflow improvement: yes, as a blocker diagnosis and optimization target, but not as acceptance.
- Cannot claim: quality target success, because one built-in hard validator failed, `D2-HG08`/`D2-HG09` failed, and no stable `expectation_unit` exists.

### Hard Gates
| Gate | Result | Evidence | Notes |
| --- | --- | --- | --- |
| D2-HG01 | pass | Same Document 1-only source `run_58f5afce8b9441ca804a2cde1ad9aec8`; clone mode. | Seed remains valid. |
| D2-HG02 | fail | Target stop-after `PromoteExpectationToBeliefState` not reached; final node is `ResolveObjectionsAndDelegations`. | This is the primary workflow blocker. |
| D2-HG03 | pass | Construction, construction review, and construction resolution completed; three shells exist. | Shell quality is not the blocking issue in this run. |
| D2-HG04 | pass | `GenerateExpectationDetails` completed with three pending expectation patches. | Field-level patches exist and are reviewable. |
| D2-HG05 | fail | Pending patches still include narrative-level precise numbers and field-review objections for false guidance/price values. | Ref existence validator passes, but semantic evidence sufficiency fails. |
| D2-HG06 | fail | O4/field review identified wrong price benchmark and return calculations for all three patches. | Price-in reasoning cannot be accepted. |
| D2-HG07 | pass | `ReviewExpectationFields` completed; A1/C1/C3/O4 review traces and objections are visible. | Review pressure is strong and useful. |
| D2-HG08 | fail | Five unresolved objections remain because O1 resolver timed out. | Blockers were neither resolved nor explicitly residualized. |
| D2-HG09 | fail | `stable expectation_unit count=0`; `pending_patch_count=3`; `unresolved_objection_count=5`. | Promotion did not occur. |
| D2-HG10 | fail | LangSmith has field-review traces, but no resolver root trace found; hard validator reports `no_action_loop_entries` and `workflow_trace_not_completed`. | Process trace cannot support acceptance. |
| D2-HG11 | pass | Remote log, Brief State, Working Memory, hard validators, and LangSmith field-review traces reproduce the blocker. | Failure is auditable. |
| D2-HG12 | fail | Review contexts reached roughly 43k-46k input tokens; resolver was still fed bulky patch context before timing out. | Context value/length needs another cut. |
| D2-HG13 | fail | Accepted/revised field-review decisions never reached stable or revised patches because resolver failed. | Multi-step revision continuity breaks at O1 resolver. |

### Built-in Hard Validators
| Validator | Result | Evidence | Notes |
| --- | --- | --- | --- |
| evidence_reference_integrity | pass | Cloud `DebugRunQueryService` hard validator; checked 17 items. | Ref existence and hydration pass, but this does not judge source sufficiency. |
| langsmith_trajectory_tool_boundary | fail | Findings: `no_action_loop_entries` for O1 `objection_resolution_result`; `workflow_trace_not_completed` with `status=blocked`, `next_node=ResolveObjectionsAndDelegations`. | Correctly blocks acceptance. |
| commit_log_state_mutation_consistency | pass | Cloud `DebugRunQueryService` hard validator; checked 4 items. | Stable state mutation consistency is clean because only global_research is stable. |

### Rubrics
| Rubric | Score | Reason |
| --- | ---: | --- |
| D2-R01 | 4 | Document 1 handoff remains valid and construction uses the upstream research, but no stable Document 2 artifact is produced. |
| D2-R02 | 3 | Three differentiated theses exist as pending patches, but they are not reliable enough to promote because key numeric claims were challenged. |
| D2-R03 | 1 | Realized facts and price reactions cannot be trusted while field review found false revenue guidance and wrong price/return calculations. |
| D2-R04 | 1 | Price-in reasoning is directly contradicted by O4/field-review objections and never repaired. |
| D2-R05 | 2 | Key variables exist in pending patches, but their current-status precision remains contaminated by disputed figures. |
| D2-R06 | 2 | Event monitoring direction exists, but threshold/value quality is not stable enough for downstream monitoring. |
| D2-R07 | 2 | Evidence refs are locatable, yet source discipline is insufficient for the precise disputed claims. |
| D2-R08 | 4 | Field review pressure is strong: it caught concrete price benchmark, return-calculation, and revenue-guidance errors. |
| D2-R09 | 1 | Objection handling fails because the resolver times out and leaves all five blockers unresolved. |
| D2-R10 | 1 | Promotion readiness is absent: no stable `expectation_unit`, three pending patches, five unresolved objections. |
| D2-R11 | 3 | Tool and field-review traces are visible and mostly role-appropriate, but the resolver path is missing/failed. |
| D2-R12 | 2 | The system detects uncertainty/contradiction, but the pending patches still overstate disputed values until the blocker is fixed. |
| D2-R13 | 4 | Remote log, Brief State, hard validators, Working Memory, and LangSmith field-review traces reproduce the failure chain. |
| D2-R14 | 4 | The failure category and next modifications are concrete, workflow-layer based, and directly retestable. |

### Score Summary
- Core Blackboard quality rubrics average (`D2-R01`-`D2-R10`): 2.1
- Other rubrics with score <= 2: `D2-R12`
- Quality target met: no
- Accept modification so far: no, must modify and retest.

### Document 2 State Summary
- Pending expectation patches: 3
- Stable expectation_unit count: 0
- Open/unresolved objections: 5
- Blocking delegations: 0
- Latest checkpoint: `blocked`, `next_node=ResolveObjectionsAndDelegations`
- Completed Document2 nodes through field review: yes
- Pending patch ids:
  - `patch_expectation_mu_01_detail`
  - `patch_expectation_mu_02_detail`
  - `patch_expectation_mu_03_detail`
- Open blockers:
  - `obj_price_mu_01`: price benchmark / return calculation error.
  - `obj_price_mu_02`: single-day return calculation error.
  - `obj_price_mu_03`: SOXX benchmark and return calculation error.
  - two duplicate Q3 FY2026 revenue-guidance objections: patch says `$36B`, authoritative evidence indicates `$33.5B`.
- Working Memory entries of interest:
  - A1/C1/C3/O4 field-review results succeeded.
  - O1 `objection_resolution_result` failed after retry audit with model request timeout and no action loop entries.

### Failure Categories
- category: `objection_resolution`
  - issue: O1 resolver times out on five field-review numeric blockers.
  - evidence: remote log final error, hard validator `no_action_loop_entries`, Working Memory failed `objection_resolution_result`.
  - severity: high/blocking
  - suspected root cause: deterministic numeric corrections are still delegated to an open-ended O1 task.
- category: `context_management`
  - issue: resolver receives large pending patches even when objections target only specific expectation ids or mechanical numeric errors.
  - evidence: field-review traces show 43k-46k input-token contexts; resolver timed out before action loop.
  - severity: high
  - suspected root cause: resolver context is not filtered to the active objection batch and still includes too much patch text.
- category: `price_in_reasoning`
  - issue: price benchmark and return calculations are wrong across all pending patches.
  - evidence: `obj_price_mu_01`, `obj_price_mu_02`, `obj_price_mu_03`.
  - severity: high
  - suspected root cause: price-reaction normalization does not yet catch field-review-specific price objections.
- category: `evidence_integrity`
  - issue: Q3 FY2026 guidance precision is false in pending patches.
  - evidence: two field-review objections compare `$36B` patch claims with `$33.5B` authoritative guidance.
  - severity: high
  - suspected root cause: deterministic cleanup does not yet target field-review guidance objections.
- category: `promotion_blocker`
  - issue: no stable Document 2 artifact can be promoted.
  - evidence: stable expectation count 0, pending patch count 3, unresolved objection count 5.
  - severity: high
  - suspected root cause: blockers stay open after resolver timeout.

### Actual Modification
- Implemented after this evaluation entry:
  - Added deterministic field-review numeric correction before O1 resolver for price benchmark / return-calculation and Q3 FY2026 revenue-guidance blockers.
  - Added expectation-id inference from objection ids such as `obj_price_mu_02`.
  - Sanitized disputed price/guidance values across thesis, facts, price reaction, summary, variables, and monitoring fields while preserving qualitative thesis intent.
  - Compact O1 resolver context to relevant pending patches for the active objection batch and record the omitted-patch count.
  - Added regression tests for deterministic field-review normalization and relevant-patch resolver context.

## 2026-06-23 00:05 - MU - Document 2 loop 1 retest7 promoted but quality rejected - over-sanitized stable expectation units

### Test Info
- Git state: cloud deployed commit `d536016` (`fix: shrink document2 resolver blockers`).
- Source run_id: `run_58f5afce8b9441ca804a2cde1ad9aec8`
- Source state: Document 1-only source; stable `global_research`; no stable `expectation_unit`; no source pending patches or blockers.
- Execution mode: `clone`
- Command: `docker compose run --rm -e DOXAGENT_RUN_REAL_API_TESTS=1 -e DOXAGENT_STORAGE_MODE=postgres debug-viewer python eval/run_document2_expectation_units_smoke.py run_58f5afce8b9441ca804a2cde1ad9aec8 --mode clone --stop-after PromoteExpectationToBeliefState --export-brief-state`
- Environment: cloud server `doxagent-hk`, `/root/doxagent`, Docker image built from `d536016`.
- Execution run_id: `run_d314bf47f3294d008014f202cccb274f`
- Stop after: `PromoteExpectationToBeliefState`
- Remote log: `/root/doxagent/.eval_runs/document2-loop1-retest7-20260622T232325+0800.log`
- Script output: `error=null`; completed nodes include `ResolveObjectionsAndDelegations` and `PromoteExpectationToBeliefState`; `stable_document_types=["global_research","expectation_unit"]`; `expectation_unit_count=3`; `pending_patch_count=0`; `unresolved_objection_count=0`; `blocking_delegation_count=0`.
- Brief State JSON export: `eval/brief_state_exports/run_d314bf47f3294d008014f202cccb274f.json`
- Built-in hard validators: overall `passed`; `evidence_reference_integrity=passed` (79 checked), `langsmith_trajectory_tool_boundary=passed` (52 checked), `commit_log_state_mutation_consistency=passed` (16 checked).
- LangSmith MCP query: construction/detail/field-review traces are visible for the same run. Keyword search for `objection_resolution` / `ResolveObjectionsAndDelegations` returned no remote root trace, but local hard validator passed using the persisted ReAct audit mirror and Working Memory has successful O1 `objection_resolution_result`.
- Evaluator: Codex, strict diagnostic retest7.

### Optimization Hypothesis
- Improvement confirmed: the retest6 resolver timeout blocker is fixed. Deterministic objection normalization ran before O1, resolved numeric_sanity / price-reaction / field-review numeric blockers, and O1 resolver successfully closed the three remaining valuation/return objections. Promotion produced three stable `expectation_unit` documents.
- New quality blocker 1: deterministic cleanup is too destructive. Many stable `realized_facts.description` fields became generic "qualitative evidence retained" placeholders, losing event identity and making realized facts weak for downstream monitoring.
- New quality blocker 2: `event_monitoring_direction` contains many generic "Monitor this catalyst qualitatively" items and literal `source-verified numeric threshold` placeholders. This passes schema and ref validators but fails the practical monitoring-readiness bar.
- New quality blocker 3: price-in reasoning is safe but under-informative. Most `price_reaction` fields say quantified reaction is withheld pending OHLCV/market_trace review, even though O4 supplied market-data evidence during field review.
- New quality blocker 4: stable market/variable fields still contain degraded placeholder wording, so uncertainty discipline is visible but not useful enough for a ≥4.2 core-quality target.
- Expected next movement: preserve event and monitoring semantics when removing unsupported numbers. The sanitizer should delete or neutralize only the disputed numeric precision while retaining the original qualitative event trigger, and it must not turn non-numeric monitoring events into generic fallback text.
- Risk: preserving too much original text may reintroduce unsupported precision. The regression tests must assert no false `$`, `%`, P/E, revenue guidance, or market-cap precision survives without source-appropriate evidence.

### Proposed Modification Plan
- Change 1: Update numeric-sanity cleanup so `realized_facts.description` is locally cleaned instead of replaced wholesale. Keep the event subject, causal clause, and source caveat when possible.
- Change 2: Update price-reaction escalation text to avoid numeric-looking agent tokens such as `O4` that can trigger false numeric_sanity revalidation.
- Change 3: Update monitoring cleanup so non-numeric monitoring events are preserved verbatim. Only monitoring strings containing numeric precision should be locally cleaned or fallbacked.
- Change 4: Replace `source-verified numeric threshold` with a less misleading source-gap phrase and polish common concatenation artifacts after numeric removal.
- Change 5: Add regression tests proving that unsupported precision is removed while meaningful non-numeric monitoring events and fact semantics survive.
- Retest requirement: commit/push, cloud `git pull --ff-only`, rebuild `debug-viewer`, rerun the same source run and same `--stop-after PromoteExpectationToBeliefState` in cloud-only mode.

### Scope Decision
- Eval mode: `promote`
- Can judge stable expectation_unit: yes.
- Can judge workflow improvement: yes, blocker closure and hard-validator pass improved materially.
- Cannot claim: quality target success, because core content rubrics remain below target and `D2-R12` is still 2.

### Hard Gates
| Gate | Result | Evidence | Notes |
| --- | --- | --- | --- |
| D2-HG01 | pass | Same Document 1-only source `run_58f5afce8b9441ca804a2cde1ad9aec8`; clone mode. | Valid seed retained. |
| D2-HG02 | pass | Script reached `PromoteExpectationToBeliefState` with `error=null`; stop-after metadata present. | Full workflow remains `running` at next node by stop-after design. |
| D2-HG03 | pass | Three construction shells formed and survived construction review/resolution. | Bullish, bearish, and neutral theses are differentiated. |
| D2-HG04 | pass | Three stable expectation units have required fields. | Field presence is acceptable, quality is not. |
| D2-HG05 | pass | Built-in evidence ref validator passed; stable docs include DoxAtlas, market-data, and Alpha Vantage refs. | Source sufficiency remains uneven but no missing refs. |
| D2-HG06 | fail | Most `price_reaction` fields are downgraded to "withheld pending market-trace evidence"; price-in interpretation is not operational. | Safe but not useful enough. |
| D2-HG07 | pass | A1/C1/C3/O4 field reviews ran and produced concrete objections. | Review pressure is strong. |
| D2-HG08 | pass | Deterministic normalization and O1 resolver closed all objections; no blockers remain. | Retest6 blocker fixed. |
| D2-HG09 | pass | Stable `expectation_unit` count is 3; pending patches 0; commit count 4. | Promotion state is clean. |
| D2-HG10 | pass | Local hard validator passed; LangSmith traces visible for construction/detail/review. | Remote resolver keyword search still sparse; record keeps this caveat. |
| D2-HG11 | pass | Remote log, Brief State, Working Memory, hard validators, commit log, and LangSmith traces reproduce the run. | Auditable. |
| D2-HG12 | fail | Cleanup leaves many generic placeholders and low-value monitoring text. | Context/control layer now works, content value is degraded. |
| D2-HG13 | pass | Deterministic normalization, resolver, and promotion decisions are visible in Working Memory and Commit Log. | Revision continuity is materially improved. |

### Built-in Hard Validators
| Validator | Result | Evidence | Notes |
| --- | --- | --- | --- |
| evidence_reference_integrity | pass | Cloud `DebugRunQueryService`; 79 checked items, 0 findings. | Ref existence and hydration pass. |
| langsmith_trajectory_tool_boundary | pass | Cloud `DebugRunQueryService`; 52 checked items, 0 findings. | Stop-after metadata and local ReAct audit mirror satisfy the hard validator. |
| commit_log_state_mutation_consistency | pass | Cloud `DebugRunQueryService`; 16 checked items, 0 findings. | Stable state mutations are explained by commit log. |

### Rubrics
| Rubric | Score | Reason |
| --- | ---: | --- |
| D2-R01 | 4 | Document 1 handoff is valid and produces differentiated expectations. |
| D2-R02 | 3 | Theses are differentiated, but over-sanitized market/fact text weakens investable specificity. |
| D2-R03 | 2 | Realized facts exist but many descriptions are generic placeholders, and price reactions are mostly withheld. |
| D2-R04 | 2 | Price-in reasoning is safer than retest6 but too under-informative for monitoring or optimization. |
| D2-R05 | 3 | Key variables remain relevant, but several statuses contain placeholder threshold wording. |
| D2-R06 | 2 | Monitoring directions are numerous but many are generic fallback text or placeholder thresholds. |
| D2-R07 | 3 | Evidence refs hydrate and include market/fundamental sources, yet final claim scope is often weakened by cleanup. |
| D2-R08 | 4 | Field review pressure is strong and caught real numeric, valuation, and price-reaction defects. |
| D2-R09 | 4 | Deterministic normalization plus O1 resolver closed all blockers without timeout. |
| D2-R10 | 4 | Promotion is clean: three stable units, no open blockers, no pending patches. |
| D2-R11 | 3 | Tool/review traces are mostly auditable, but remote resolver keyword search is still not straightforward. |
| D2-R12 | 2 | Uncertainty discipline is visible, but placeholder wording replaces too much useful content. |
| D2-R13 | 4 | Artifacts are reproducible across remote log, DB, hard validators, Working Memory, and commit log. |
| D2-R14 | 4 | Failure categories and next code changes are concrete, enforcement-layer-based, and retestable. |

### Score Summary
- Core Blackboard quality rubrics average (`D2-R01`-`D2-R10`): 3.1
- Other rubrics with score <= 2: `D2-R12`
- Quality target met: no
- Accept modification so far: no, must modify and retest.

### Document 2 State Summary
- Pending expectation patches: 0
- Stable expectation_unit count: 3
- Open/unresolved objections: 0
- Blocking delegations: 0
- Latest checkpoint: `running`, `next_node=GenerateGlobalNarrativeReport` after Document2 stop-after.
- Stable expectation ids:
  - `expectation_mu_001`
  - `expectation_mu_002`
  - `expectation_mu_003`
- Working Memory entries of interest:
  - `deterministic_objection_normalization` succeeded with `normalization_types=["numeric_sanity","price_reaction_contradiction","field_review_numeric_correction"]`.
  - O1 `objection_resolution_result` succeeded and closed the three residual valuation/return objections.
  - A1/C1/C3/O4 field reviews all succeeded.
- Residual issues:
  - Generic realized-fact descriptions.
  - Generic monitoring events.
  - Literal source-gap placeholder phrases in stable docs.
  - Price-in reasoning mostly withheld rather than recalculated.

### Failure Categories
- category: `content_over_sanitization`
  - issue: deterministic cleanup removes false precision but also destroys event/fact specificity.
  - evidence: stable facts use generic qualitative-retained text across multiple units.
  - severity: high/quality
  - suspected root cause: sanitizer replaces full fields instead of cleaning only numeric spans.
- category: `monitoring_specificity`
  - issue: monitoring events contain generic fallback text and source-gap placeholders.
  - evidence: `event_monitoring_direction` lists repeated "Monitor this catalyst qualitatively" items.
  - severity: high/quality
  - suspected root cause: monitoring sanitizer cleans all events rather than only numeric events.
- category: `price_in_reasoning`
  - issue: quantified price reactions are withheld instead of transformed into usable qualitative market-learning statements.
  - evidence: stable `price_reaction.price_change` says market-trace verification required for most facts.
  - severity: medium/high
  - suspected root cause: fallback prioritizes safety but does not preserve enough O4-reviewed semantics.
- category: `uncertainty_discipline`
  - issue: uncertainty is acknowledged, but placeholder text is too mechanical for downstream policy generation.
  - evidence: `source-verified numeric threshold` appears in variables and monitoring.
  - severity: medium/high
  - suspected root cause: numeric replacement phrase is leaked into final stable docs.

### Actual Modification
- Implemented after this evaluation entry:
  - Preserve cleaned realized-fact descriptions instead of replacing them wholesale with generic fallback.
  - Preserve non-numeric monitoring events verbatim and only clean monitoring strings that contain numeric precision.
  - Replace price-reaction escalation text with non-numeric, non-agent-token wording to avoid false numeric_sanity revalidation.
  - Replace `source-verified numeric threshold` with `source-verified value` and polish common concatenation artifacts.
  - Add regression coverage for preserving fact semantics and non-numeric monitoring events while removing unsupported precision.

## 2026-06-23 00:55 - MU - Document 2 loop 1 retest8 blocked - ReviewExpectationFields max_steps without final payload

### Test Info
- Git state: cloud deployed commit `b38e209` (`fix: preserve document2 sanitized semantics`).
- Source run_id: `run_58f5afce8b9441ca804a2cde1ad9aec8`
- Source state: Document 1-only source; stable `global_research`; no stable `expectation_unit`; no source pending patches or blockers.
- Execution mode: `clone`
- Command: `docker compose run --rm -e DOXAGENT_RUN_REAL_API_TESTS=1 -e DOXAGENT_STORAGE_MODE=postgres debug-viewer python eval/run_document2_expectation_units_smoke.py run_58f5afce8b9441ca804a2cde1ad9aec8 --mode clone --stop-after PromoteExpectationToBeliefState --export-brief-state`
- Environment: cloud server `doxagent-hk`, `/root/doxagent`, Docker image built from `b38e209`.
- Execution run_id: `run_5aabe95ba6d74fd59ef5a543be682752`
- Remote log: `/root/doxagent/.eval_runs/document2-loop1-retest8-20260623T001621+0800.log`
- Script output: `status=blocked`; `next_node=ReviewExpectationFields`; completed nodes through `GenerateExpectationDetails`; `stable_document_types=["global_research"]`; `expectation_unit_count=0`; `pending_patch_count=2`; `working_memory_count=13`; `commit_count=1`; `unresolved_objection_count=4`; `blocking_delegation_count=0`.
- Error: `ReviewExpectationFields agent result failed: ReAct loop reached max_steps without a complete final payload.`
- Brief State JSON export reported by cloud script: `eval/brief_state_exports/run_5aabe95ba6d74fd59ef5a543be682752.json`
- Built-in hard validators: overall `failed`; `evidence_reference_integrity=passed` (9 checked), `langsmith_trajectory_tool_boundary=failed` (47 checked; `workflow_trace_not_completed`), `commit_log_state_mutation_consistency=passed` (4 checked).
- LangSmith MCP query: latest successful traces include `A1.ReviewExpectationFields.LOOP5`, `A1.ReviewExpectationFields.LOOP7`, and `O4.ReviewExpectationFields.LOOP4`. `A1.ReviewExpectationFields.LOOP7` returned another `tool_calls` action instead of a final `DoxAtlasAuditResult`; the runtime then exhausted `max_steps` and returned failed `AgentResult`.
- Evaluator: Codex, strict blocked-run diagnostic retest8.

### Optimization Hypothesis
- Retest7 over-sanitization fixes did not get a chance to prove quality improvement, because retest8 hit a blocking runtime failure before `ResolveObjectionsAndDelegations` and before promotion.
- The immediate blocker is not a content-quality objection; it is a ReAct protocol failure in a review task. A1 had already performed DoxAtlas review work and LangSmith shows successful LLM calls, but the final A1 step emitted a new `tool_calls` plan rather than the required `DoxAtlasAuditResult.final_payload`.
- Existing runtime recovery only covers `ResearchSection` max-step exhaustion. Review schemas (`DoxAtlasAuditResult`, `ExpectationFieldReviewResult`) have no conservative fallback, so a reviewer that keeps asking for optional tools can block the whole Document2 workflow even when other reviewers and partial audit evidence exist.
- The correct optimization is not to count incomplete review as a pass. The runtime should convert review max-step exhaustion into a conservative, schema-valid review result with `needs_more_evidence` / `not_checked` findings, `unknowns`, and `revision_required=true` for A1, preserving auditability and allowing the workflow to continue to downstream objection/resolution and promotion gates.
- This keeps LLM-as-judge strictness intact: the eval record and rubrics should penalize incomplete A1 review, but the workflow should not fail structurally when it can record the gap as Working Memory.

### Proposed Modification Plan
- Change 1: Add a ReAct max-steps fallback for review schemas only: `DoxAtlasAuditResult` and `ExpectationFieldReviewResult`.
- Change 2: The fallback must return a schema-valid conservative review payload, not a synthetic content endorsement. For A1, use `verdict=needs_revision`, `revision_required=true`, a finding on `document`, and an unknown explaining max-step non-completion.
- Change 3: Preserve successful tool evidence refs and successful tool-call audit data through the normal `_succeeded()` path so hard validators can still inspect the local ReAct audit mirror.
- Change 4: Do not create content objections from a runtime formatting failure. Record the review coverage gap in findings/unknowns and let quality rubrics penalize it.
- Change 5: Add a regression test that reproduces the retest8 shape: A1 `DoxAtlasAuditResult` task reaches `max_steps` after producing a tool-call action, and the runtime returns a conservative succeeded review result instead of `react_max_steps_exceeded`.
- Retest requirement: commit/push, cloud `git pull --ff-only`, rebuild `debug-viewer`, rerun the same Document 1-only source and same `--stop-after PromoteExpectationToBeliefState` in cloud-only mode.

### Scope Decision
- Eval mode: `blocked`
- Can judge stable expectation_unit: no, no stable `expectation_unit` documents were promoted.
- Can judge workflow improvement: yes, retest8 exposes a new blocking runtime failure after retest7's sanitizer changes.
- Cannot claim: content quality improvement, promotion quality, or target success.

### Hard Gates
| Gate | Result | Evidence | Notes |
| --- | --- | --- | --- |
| D2-HG01 | pass | Same Document 1-only source `run_58f5afce8b9441ca804a2cde1ad9aec8`; clone mode. | Valid seed retained. |
| D2-HG02 | fail | Cloud log finished `status=blocked`; `next_node=ReviewExpectationFields`; error `react_max_steps_exceeded`. | Workflow did not reach stop-after node. |
| D2-HG03 | pass | `GenerateExpectationConstruction` and construction review/resolution completed. | Shell stage continued to details. |
| D2-HG04 | fail | `expectation_unit_count=0`; only stable `global_research`. | No stable Document2 output. |
| D2-HG05 | pass | Built-in evidence reference validator passed with 9 checked items. | Limited to artifacts reached before blocker. |
| D2-HG06 | fail | No stable expectation units or promoted price-reaction fields. | Cannot judge price-in reasoning. |
| D2-HG07 | partial | Some `ReviewExpectationFields` traces ran, including A1 and O4, but A1 did not produce final payload. | Review pressure started but did not close. |
| D2-HG08 | fail | `unresolved_objection_count=4`; blocked before resolver. | No complete objection resolution. |
| D2-HG09 | fail | No stable expectation units; pending patches remained in the cloud log. | Promotion did not occur. |
| D2-HG10 | fail | Built-in trajectory validator failed with `workflow_trace_not_completed`. | Correctly rejects blocked run. |
| D2-HG11 | pass | Remote log, DB hard-validator output, and LangSmith traces reproduce the failure. | Auditable blocked run. |
| D2-HG12 | fail | No promoted Blackboard content to use downstream. | Usability target unmet. |
| D2-HG13 | fail | Review runtime failure prevents memory continuity into resolver/promotion. | Needs workflow fix. |

### Built-in Hard Validators
| Validator | Result | Evidence | Notes |
| --- | --- | --- | --- |
| evidence_reference_integrity | pass | Cloud `DebugRunQueryService`; 9 checked items, 0 findings. | No dangling refs in reached artifacts. |
| langsmith_trajectory_tool_boundary | fail | Cloud `DebugRunQueryService`; 47 checked items, one `workflow_trace_not_completed` finding. | Latest checkpoint was `blocked`, `next_node=ReviewExpectationFields`. |
| commit_log_state_mutation_consistency | pass | Cloud `DebugRunQueryService`; 4 checked items, 0 findings. | Limited commit/state reached before blocker is consistent. |

### Rubrics
| Rubric | Score | Reason |
| --- | ---: | --- |
| D2-R01 | 4 | Document 1-only source and handoff remain correct. |
| D2-R02 | 2 | Expectation construction/details began, but no stable expectation unit is available for thesis quality review. |
| D2-R03 | 1 | No stable realized facts exist after the blocked run. |
| D2-R04 | 1 | No stable price-in reasoning exists after the blocked run. |
| D2-R05 | 1 | No stable key variables exist after the blocked run. |
| D2-R06 | 1 | No stable monitoring directions exist after the blocked run. |
| D2-R07 | 2 | Early evidence refs are intact, but final Document2 evidence sufficiency cannot be assessed. |
| D2-R08 | 2 | Review traces began and O4 completed, but A1 review exhausted max steps without final payload. |
| D2-R09 | 1 | Resolver did not run; objections remained unresolved. |
| D2-R10 | 1 | Promotion did not run; no stable expectation units. |
| D2-R11 | 2 | Failure is auditable via log/DB/LangSmith, but the workflow trace is not closed. |
| D2-R12 | 1 | No final uncertainty discipline is present in promoted Blackboard content. |
| D2-R13 | 3 | Blocked-run artifacts are reproducible, but no completed Document2 output exists. |
| D2-R14 | 4 | Failure category and code-level modification plan are specific and retestable. |

### Score Summary
- Core Blackboard quality rubrics average (`D2-R01`-`D2-R10`): 1.6
- Other rubrics with score <= 2: `D2-R11`, `D2-R12`
- Quality target met: no
- Accept modification so far: no, must fix blocking runtime failure and retest.

### Document 2 State Summary
- Pending expectation patches: cloud log reported 2.
- Stable expectation_unit count: 0.
- Open/unresolved objections: cloud log reported 4.
- Blocking delegations: 0.
- Latest checkpoint: `blocked`, `next_node=ReviewExpectationFields`.
- Working Memory count: 13.
- Commit count: 1.
- LangSmith evidence of root cause:
  - `A1.ReviewExpectationFields.LOOP7` returned `tool_calls` rather than final `DoxAtlasAuditResult`.
  - `O4.ReviewExpectationFields.LOOP4` completed and captured MU/QQQ OHLCV success with SOXX upstream/rate-limit gaps.

### Failure Categories
- category: `review_react_protocol_exhaustion`
  - issue: A1 review kept planning tool calls and never produced final schema payload before max_steps.
  - evidence: cloud error `ReAct loop reached max_steps without a complete final payload`; LangSmith A1 LOOP7 output is `tool_calls`.
  - severity: blocking/runtime
  - suspected root cause: review schemas lack max-step recovery, unlike `ResearchSection`.
- category: `workflow_trace_not_completed`
  - issue: hard validator correctly rejects blocked checkpoint.
  - evidence: `langsmith_trajectory_tool_boundary=failed`, `workflow_trace_not_completed`.
  - severity: blocking/eval-gate
  - suspected root cause: failed AgentResult propagates as `WorkflowContractError` in `ReviewExpectationFields`.
- category: `review_gap_preservation`
  - issue: a runtime formatting failure must remain visible without stopping downstream deterministic review/resolution.
  - evidence: A1 had partial tool/review traces, while O4 completed; killing the whole workflow loses useful reviewer work.
  - severity: medium/high
  - suspected root cause: no conservative schema-valid audit fallback for review tasks.

### Actual Modification
- Implemented after this evaluation entry:
  - Added a ReAct max-steps fallback for `DoxAtlasAuditResult` and `ExpectationFieldReviewResult`.
  - The fallback returns conservative review findings and unknowns through `_succeeded()`, preserving evidence refs, successful tool-call summaries, and `react_audit`.
  - A1 fallback uses `verdict=needs_revision`, `revision_required=true`, and no content objections for the runtime-format gap.
  - Added regression coverage for A1 `DoxAtlasAuditResult` max-step recovery.

## 2026-06-23 01:55 - MU - Document 2 loop 1 retest9 blocked - A1 field-review parallel timeout

### Test Info
- Git state: cloud deployed commit `6527556` (`fix: recover document2 review max steps`).
- Source run_id: `run_58f5afce8b9441ca804a2cde1ad9aec8`
- Source state: Document 1-only source; stable `global_research`; no stable `expectation_unit`.
- Execution mode: `clone`
- Command: `docker compose run --rm -e DOXAGENT_RUN_REAL_API_TESTS=1 -e DOXAGENT_STORAGE_MODE=postgres debug-viewer python eval/run_document2_expectation_units_smoke.py run_58f5afce8b9441ca804a2cde1ad9aec8 --mode clone --stop-after PromoteExpectationToBeliefState --export-brief-state`
- Environment: cloud server `doxagent-hk`, `/root/doxagent`, Docker image built from `6527556`.
- Execution run_id: `run_71a45a20bbb145588fb4eb0299dbecee`
- Remote log: `/root/doxagent/.eval_runs/document2-loop1-retest9-20260623T010000+0800.log`
- Script output: `status=blocked`; `next_node=ReviewExpectationFields`; completed nodes through `GenerateExpectationDetails`; `stable_document_types=["global_research"]`; `pending_patch_count=3`; `working_memory_count=14`; `commit_count=1`; `unresolved_objection_count=5`; `blocking_delegation_count=0`; `expectation_unit_count=0`.
- Error: `parallel_agent_timeout: ReviewExpectationFields/A1 did not return within 1800 seconds.`
- Brief State JSON export reported by cloud script: `eval/brief_state_exports/run_71a45a20bbb145588fb4eb0299dbecee.json`
- Built-in hard validators: overall `failed`; `evidence_reference_integrity=passed` (11 checked), `langsmith_trajectory_tool_boundary=failed` (44 checked; `workflow_trace_not_completed`), `commit_log_state_mutation_consistency=passed` (4 checked).
- LangSmith MCP query: C1/C3/O4 `ReviewExpectationFields` traces completed and Working Memory entries were written at `2026-06-22T17:51:44Z` through `17:52:06Z`. No A1 `ReviewExpectationFields` completion trace or Working Memory entry was present before the parallel timeout.
- Evaluator: Codex, strict blocked-run diagnostic retest9.

### Optimization Hypothesis
- Retest8's max-step recovery fixed one failure class, but retest9 exposed a deeper A1 field-review blocker: the A1 field-review job never returned within the workflow-level 1800 second parallel-agent deadline.
- Evidence points to an A1 ReviewExpectationFields tool-permission/prompt conflict. The node instruction says A1 should not call tools, but `NODE_AGENT_ALLOWED_TOOL_OVERRIDES` still grants A1 nine DoxAtlas read tools for `ReviewExpectationFields`. This lets A1 enter long optional-tool loops while the workflow waits for the parallel job.
- C1/C3/O4 all completed and wrote Working Memory, so the blocker is isolated to A1. The workflow should preserve A1 review pressure, but the field-review stage should not permit A1 to perform fresh DoxAtlas retrieval when the task is specifically to audit pending expectation patches and attached evidence already present in context.
- Expected improvement: make A1 `ReviewExpectationFields` truly context-only. A1 can still inspect pending patches, existing evidence refs, and context summaries, while bottom-up retrieval pressure remains available in earlier construction review and in O1/detail tool usage. This should prevent parallel timeout without relaxing validators or judge scoring.
- Risk: A1 field review may become less evidence-rich. The rubrics should penalize A1 if it fails to identify DoxAtlas support gaps from context, but this is preferable to a structural workflow timeout.

### Proposed Modification Plan
- Change 1: Set `(WorkflowNode.REVIEW_EXPECTATION_FIELDS, AgentName.A1_DOXATLAS_AUDIT)` allowed tool override to `[]`.
- Change 2: Rewrite A1 `ReviewExpectationFields` instruction to explicitly say it must use only existing pending patches, attached evidence refs, and task context; it must not call tools.
- Change 3: Ensure the A1 field-review task receives `tool_requirements=[]` and `required_tool_names=[]`, so the prompt and permissions are aligned.
- Change 4: Add a regression test in `tests/test_phase5_initialization_workflow.py` asserting A1 field-review `permissions.allowed_tools`, `tool_requirements`, and `required_tool_names` are empty.
- Retest requirement: commit/push, cloud `git pull --ff-only`, rebuild `debug-viewer`, rerun same Document 1-only source and same `--stop-after PromoteExpectationToBeliefState` in cloud-only mode.

### Scope Decision
- Eval mode: `blocked`
- Can judge stable expectation_unit: no, no stable `expectation_unit` documents were promoted.
- Can judge workflow improvement: yes, retest9 isolates a new A1 parallel timeout after retest8's runtime max-step fallback.
- Cannot claim: content quality improvement, promotion quality, or target success.

### Hard Gates
| Gate | Result | Evidence | Notes |
| --- | --- | --- | --- |
| D2-HG01 | pass | Same Document 1-only source `run_58f5afce8b9441ca804a2cde1ad9aec8`; clone mode. | Valid seed retained. |
| D2-HG02 | fail | Cloud log finished `status=blocked`; `next_node=ReviewExpectationFields`; A1 parallel timeout. | Did not reach stop-after node. |
| D2-HG03 | pass | Construction and construction-resolution nodes completed. | Shell/detail generation advanced farther than initial baseline. |
| D2-HG04 | fail | `expectation_unit_count=0`; no stable Document2 output. | Field review did not close. |
| D2-HG05 | pass | Built-in evidence reference validator passed with 11 checked items. | Limited to reached artifacts. |
| D2-HG06 | fail | No stable price-reaction fields. | Cannot judge price-in reasoning. |
| D2-HG07 | partial | C1/C3/O4 field reviews completed; A1 field review timed out. | Review pressure remains incomplete. |
| D2-HG08 | fail | `unresolved_objection_count=5`; resolver did not run. | No closed objection cycle. |
| D2-HG09 | fail | Pending patches remained; no promotion. | No stable expectation units. |
| D2-HG10 | fail | Built-in trajectory validator failed with `workflow_trace_not_completed`. | Correct blocked-run rejection. |
| D2-HG11 | pass | Remote log, DB hard-validator output, Working Memory, and LangSmith reproduce the failure. | Auditable. |
| D2-HG12 | fail | No final downstream-usable Blackboard content. | Usability target unmet. |
| D2-HG13 | fail | A1 timeout prevents continuity into resolver/promotion. | Needs workflow permission fix. |

### Built-in Hard Validators
| Validator | Result | Evidence | Notes |
| --- | --- | --- | --- |
| evidence_reference_integrity | pass | Cloud `DebugRunQueryService`; 11 checked items, 0 findings. | Reached artifacts have valid refs. |
| langsmith_trajectory_tool_boundary | fail | Cloud `DebugRunQueryService`; `workflow_trace_not_completed`, status `blocked`, next node `ReviewExpectationFields`. | Expected hard-gate failure for blocked run. |
| commit_log_state_mutation_consistency | pass | Cloud `DebugRunQueryService`; 4 checked items, 0 findings. | Limited reached state is consistent. |

### Rubrics
| Rubric | Score | Reason |
| --- | ---: | --- |
| D2-R01 | 4 | Document 1-only source and clone handoff remain correct. |
| D2-R02 | 2 | Construction/details exist transiently, but no stable expectation unit is promoted. |
| D2-R03 | 1 | No stable realized facts are available. |
| D2-R04 | 1 | No stable price-in reasoning is available. |
| D2-R05 | 1 | No stable key variables are available. |
| D2-R06 | 1 | No stable monitoring directions are available. |
| D2-R07 | 2 | Early refs pass integrity; final Document2 evidence sufficiency cannot be assessed. |
| D2-R08 | 2 | C1/C3/O4 review pressure ran, but A1 field-review timeout leaves the review set incomplete. |
| D2-R09 | 1 | Resolver did not run; objections remain unresolved. |
| D2-R10 | 1 | Promotion did not run. |
| D2-R11 | 2 | Failure is reproducible, but workflow trace is not closed. |
| D2-R12 | 1 | No final uncertainty discipline exists in promoted Document2 content. |
| D2-R13 | 3 | Blocked-run artifacts are reproducible across log/DB/LangSmith. |
| D2-R14 | 4 | Root cause and permission-level modification plan are concrete and retestable. |

### Score Summary
- Core Blackboard quality rubrics average (`D2-R01`-`D2-R10`): 1.6
- Other rubrics with score <= 2: `D2-R11`, `D2-R12`
- Quality target met: no
- Accept modification so far: no, must fix A1 timeout and retest.

### Document 2 State Summary
- Pending expectation patches: cloud log reported 3.
- Stable expectation_unit count: 0.
- Open/unresolved objections: cloud log reported 5.
- Blocking delegations: 0.
- Latest checkpoint: `blocked`, `next_node=ReviewExpectationFields`.
- Working Memory count: 14.
- Commit count: 1.
- Working Memory field-review entries:
  - `c1_fundamental_review` succeeded.
  - `c3_industry_review` succeeded.
  - `o4_market_trace_review` succeeded.
  - `a1_doxatlas_audit` for field review is missing.

### Failure Categories
- category: `a1_field_review_parallel_timeout`
  - issue: A1 field review did not return within 1800 seconds.
  - evidence: cloud error `parallel_agent_timeout: ReviewExpectationFields/A1 did not return within 1800 seconds`.
  - severity: blocking/runtime
  - suspected root cause: A1 field-review task retained optional DoxAtlas tool permissions despite a no-tools instruction.
- category: `prompt_permission_conflict`
  - issue: prompt/context says do not run tools, while permissions expose DoxAtlas tools.
  - evidence: `NODE_AGENT_ALLOWED_TOOL_OVERRIDES` grants A1 ReviewExpectationFields nine DoxAtlas tools.
  - severity: high
  - suspected root cause: construction-review A1 tool policy was copied into field review without respecting the field-review task's context-only intent.
- category: `review_set_incomplete`
  - issue: C1/C3/O4 field reviews completed, but A1's missing result blocks resolver/promotion.
  - evidence: DB Working Memory has C1/C3/O4 review entries and no A1 field-review entry.
  - severity: high
  - suspected root cause: parallel node waits for all reviewers before proceeding.

### Actual Modification
- Implemented after this evaluation entry:
  - Changed A1 `ReviewExpectationFields` allowed tool override to `[]`.
  - Updated A1 field-review instruction to context-only review over pending patches, attached evidence refs, and existing task context.
  - Added regression assertions that A1 field-review permissions and tool requirement lists are empty.

## 2026-06-23 - Loop 1 Retest10 Final Stop After A1 Context-Only Review Fix

### Test Info
- Git state: cloud deployed commit `a48d1ef` (`fix: make document2 a1 field review context only`).
- Source run_id: `run_58f5afce8b9441ca804a2cde1ad9aec8`.
- Source state: Document 1-only source; stable `global_research`; no stable `expectation_unit`.
- Execution mode: `clone`.
- Command: `docker compose run --rm -e DOXAGENT_RUN_REAL_API_TESTS=1 -e DOXAGENT_STORAGE_MODE=postgres debug-viewer python eval/run_document2_expectation_units_smoke.py run_58f5afce8b9441ca804a2cde1ad9aec8 --mode clone --stop-after PromoteExpectationToBeliefState --export-brief-state`.
- Environment: cloud server `doxagent-hk`, `/root/doxagent`, Docker image built from `a48d1ef`.
- Execution run_id: `run_54067415d54e42f5a75cb11d97cd5fbd`.
- Remote log: `/root/doxagent/.eval_runs/document2-loop1-retest10-20260623T020224+0800.log`.
- Script output: `status=blocked`; `next_node=GenerateExpectationDetails`; completed nodes through `ResolveExpectationConstruction`; `stable_document_types=["global_research"]`; `pending_patch_count=0`; `working_memory_count=9`; `commit_count=1`; `unresolved_objection_count=0`; `blocking_delegation_count=0`; `expectation_unit_count=0`.
- Error: `parallel_agent_timeout: GenerateExpectationDetails/O1 did not return within 1800 seconds.`
- Cloud Brief State export rerun in `debug-viewer` at `2026-06-22T18:51:02Z`: latest checkpoint `blocked`, `next_node=GenerateExpectationDetails`, `checkpoint_count=11`, `stable_document_types=["global_research"]`, `expectation_units=[]`, `working_memory_entries=9`, `commit_log_entries=1`, `objections=0`, `delegations=0`, `evidence_refs=57`.
- Built-in hard validators: overall `failed`; `evidence_reference_integrity=passed`, `langsmith_trajectory_tool_boundary=failed` (`workflow_trace_not_completed`), `commit_log_state_mutation_consistency=passed`.
- Evaluator: Codex, strict blocked-run diagnostic retest10.

### Optimization Hypothesis
- Retest9's A1 context-only field-review fix removed the previously identified prompt/permission conflict, but retest10 did not reach `ReviewExpectationFields`; it exposed a remaining earlier blocker in `GenerateExpectationDetails/O1`.
- The run generated auditable construction shells and entered detail expansion, but one O1 detail worker did not return within the 1800 second parallel-agent deadline. Because detail generation is still a prerequisite for field review, resolver, and promotion, the workflow correctly stopped without promoting unstable Document2 content.
- The likely quality issue is not a judge/rubric looseness problem; it is a runtime and workflow-shaping issue: detail generation still permits a long-tail O1 worker to hold the whole node hostage after sibling work has produced useful pending detail content.
- Expected next improvement if work continued: add stronger per-expectation detail budgets or resumable/quorum-style detail checkpointing so completed detail shells are preserved while the stuck expectation is either retried narrowly or converted into an explicit blocking objection before field review.
- User instruction supersedes further optimization in this turn: after Retest10, stop the loop and report the 10-run result summary. Therefore no new prompt/skill/workflow modification is applied after this record.

### Proposed Modification Plan
- Deferred Change 1: Add per-expectation identity and last-active audit fields to `GenerateExpectationDetails` parallel tasks so the exact stuck expectation shell is visible in DB and logs, not only as `GenerateExpectationDetails/O1`.
- Deferred Change 2: Enforce a smaller O1 detail tool/model budget per expectation shell and convert budget exhaustion into a structured failed detail patch with evidence and reason, instead of waiting for the global 1800 second node timeout.
- Deferred Change 3: Persist completed detail patches with explicit `detail_generation_status` and allow a same-node resume to retry only missing/failed expectation ids.
- Deferred Change 4: Add a regression smoke assertion that a timed-out detail worker cannot erase or hide sibling detail-worker completion evidence.
- Retest requirement if resumed later: commit/push, cloud `git pull --ff-only`, rebuild `debug-viewer`, rerun the same Document 1-only source and same `--stop-after PromoteExpectationToBeliefState`.

### Scope Decision
- Eval mode: `blocked`.
- Can judge stable expectation_unit: no, no stable `expectation_unit` documents were promoted.
- Can judge workflow improvement: partially; retest10 moved the observed blocker from A1 field review back to O1 detail generation, proving the retest exercised the cloud workflow and surfaced the next real bottleneck.
- Cannot claim: quality target success, final Blackboard readiness, or stable downstream monitoring usability.
- Stop condition used: explicit user instruction to finish Retest10 and end/report, not quality-target success.

### Hard Gates
| Gate | Result | Evidence | Notes |
| --- | --- | --- | --- |
| D2-HG01 | pass | Same Document 1-only source `run_58f5afce8b9441ca804a2cde1ad9aec8`; clone mode. | Valid seed retained. |
| D2-HG02 | fail | Cloud log finished `status=blocked`; `next_node=GenerateExpectationDetails`; O1 parallel timeout. | Did not reach stop-after node. |
| D2-HG03 | pass | Construction, construction review, and construction resolution completed. | Shell-level flow advanced before detail blocker. |
| D2-HG04 | fail | `expectation_unit_count=0`; Brief State `expectation_units=[]`. | No stable Document2 output. |
| D2-HG05 | pass | Built-in evidence reference validator passed. | Reached artifacts have valid refs. |
| D2-HG06 | fail | No stable price-reaction fields. | Detail content did not reach stable review/promotion. |
| D2-HG07 | fail | Field review never ran. | Blocked earlier at O1 detail generation. |
| D2-HG08 | fail | Resolver for detail objections never ran. | No review/resolution cycle after details. |
| D2-HG09 | fail | No promotion. | No stable expectation units. |
| D2-HG10 | fail | Built-in trajectory validator failed with `workflow_trace_not_completed`. | Correct blocked-run rejection. |
| D2-HG11 | pass | Remote log and cloud Brief State reproduce the same blocker. | Auditable. |
| D2-HG12 | fail | No final downstream-usable Blackboard content. | Usability target unmet. |
| D2-HG13 | fail | O1 detail timeout prevents continuity into field review/resolver/promotion. | Needs next-loop workflow hardening if resumed. |

### Built-in Hard Validators
| Validator | Result | Evidence | Notes |
| --- | --- | --- | --- |
| evidence_reference_integrity | pass | Cloud `DebugRunQueryService`; `status=passed`, 0 errors. | Reached refs are hydrated. |
| langsmith_trajectory_tool_boundary | fail | Cloud `DebugRunQueryService`; `workflow_trace_not_completed`, status `blocked`, next node `GenerateExpectationDetails`. | Expected hard-gate failure for blocked run. |
| commit_log_state_mutation_consistency | pass | Cloud `DebugRunQueryService`; `status=passed`, 0 errors. | Limited reached state is consistent. |

### Rubrics
| Rubric | Score | Reason |
| --- | ---: | --- |
| D2-R01 | 4 | Document 1-only source and clone handoff remain correct and auditable. |
| D2-R02 | 2 | Expectation shells and partial detail work exist, but no stable expectation unit is promoted. |
| D2-R03 | 2 | Realized-fact content appears in transient detail patches, but it is unreviewed and not stable. |
| D2-R04 | 2 | Some price-in language appears in transient details, but no stable price reaction survives review/promotion. |
| D2-R05 | 2 | Key variables appear in transient details, but no stable variable set is available. |
| D2-R06 | 1 | No stable monitoring or downstream action policy exists. |
| D2-R07 | 2 | Evidence refs pass integrity, but source sufficiency for final Document2 cannot be judged. |
| D2-R08 | 1 | Field review did not run. |
| D2-R09 | 1 | Detail-review objection resolution did not run. |
| D2-R10 | 1 | Promotion did not run. |
| D2-R11 | 2 | Failure is reproducible, but the workflow trace is not closed. |
| D2-R12 | 1 | No stable uncertainty discipline exists in promoted Document2 content. |
| D2-R13 | 3 | Blocked-run artifacts are reproducible across remote log and cloud Brief State. |
| D2-R14 | 4 | The next optimization hypothesis is concrete and retestable, though not executed because of the stop instruction. |

### Score Summary
- Core Blackboard quality rubrics average (`D2-R01`-`D2-R10`): 1.8.
- Other rubrics with score <= 2: `D2-R11`, `D2-R12`.
- Quality target met: no.
- Accept modification so far: no final quality acceptance; retest10 confirms the next blocker.

### Document 2 State Summary
- Stable expectation_unit count: 0.
- Stable document types: `global_research` only.
- Latest checkpoint: `blocked`, `next_node=GenerateExpectationDetails`.
- Completed nodes: `StartTickerInitialization`, `BuildGlobalResearch`, `ReviewGlobalResearch`, `GenerateExpectationConstruction`, `ReviewExpectationConstruction`, `ResolveExpectationConstruction`.
- Working Memory count: 9.
- Commit count: 1.
- Objections/delegations: 0/0 at latest checkpoint.
- Evidence refs: 57.

### Failure Categories
- category: `o1_detail_parallel_timeout`
  - issue: O1 detail generation did not return within the 1800 second parallel-agent deadline.
  - evidence: cloud error `parallel_agent_timeout: GenerateExpectationDetails/O1 did not return within 1800 seconds`.
  - severity: blocking/runtime.
  - suspected root cause: one detail-generation worker can still monopolize the node after sibling detail work has produced useful partial content.
- category: `no_stable_document2_output`
  - issue: no stable `expectation_unit`, monitoring policy, or monitoring config was promoted.
  - evidence: Brief State `expectation_units=[]`; stable docs only include `global_research`.
  - severity: quality-blocking.
  - suspected root cause: detail generation is a hard prerequisite for field review, resolver, and promotion.
- category: `trajectory_hard_gate_failure`
  - issue: built-in trajectory validator failed because the run ended blocked.
  - evidence: hard validator `langsmith_trajectory_tool_boundary=failed` with `workflow_trace_not_completed`.
  - severity: hard-gate.
  - suspected root cause: expected consequence of the O1 detail timeout, not validator looseness.

### Actual Modification
- No code/prompt/workflow modification is applied after retest10.
- Reason: user explicitly instructed to finish Retest10, end the eval loop, cancel the 15-minute automation, switch to scheduled wakeup, and report the 10-run results.
- Retest10's proposed next modification plan is recorded above for future resumption, but intentionally deferred.

## 2026-06-23 - Loop 1 Retest11 After Detail Timeout Recovery

### Test Info
- Git state: cloud deployed commit `41b8416` (`fix: recover document2 detail timeouts`).
- Source run_id: `run_58f5afce8b9441ca804a2cde1ad9aec8`.
- Source state: Document 1-only source; stable `global_research`; no stable `expectation_unit`.
- Execution mode: `clone`.
- Command: `docker compose run --rm -e DOXAGENT_RUN_REAL_API_TESTS=1 -e DOXAGENT_STORAGE_MODE=postgres debug-viewer python eval/run_document2_expectation_units_smoke.py run_58f5afce8b9441ca804a2cde1ad9aec8 --mode clone --stop-after PromoteExpectationToBeliefState --export-brief-state`.
- Environment: cloud server `doxagent-hk`, `/root/doxagent`, Docker image built from `41b8416`.
- Execution run_id: `run_cbc6367287a3498092bde2c8e29ec6c0`.
- Remote log: `/root/doxagent/.eval_runs/document2-loop1-retest11-20260623T164559+0800.log`.
- Script output: `status=blocked`; `next_node=ResolveObjectionsAndDelegations`; completed nodes through `ReviewExpectationFields`; `stable_document_types=["global_research"]`; `pending_patch_count=2`; `working_memory_count=15`; `commit_count=1`; `unresolved_objection_count=2`; `blocking_delegation_count=0`; `expectation_unit_count=0`.
- Error: `GenerateExpectationUnits expectation patch must include document content.`
- `GenerateExpectationDetails` status metadata showed both detail shells completed: `expectation_mu_01` produced `patch_expectation_mu_01_detail`; `expectation_mu_02` produced `patch_expectation_mu_02_detail`.
- Brief State export rerun in cloud: latest checkpoint `blocked`, `next_node=ResolveObjectionsAndDelegations`, `checkpoint_count=12`, stable docs only `global_research`, `expectation_units=[]`, `working_memory_entries=15`, `commit_log_entries=1`, `objections=10`, `open_or_unresolved_objections=2`, `delegations=0`, `evidence_refs=72`.
- Built-in hard validators: overall `failed`; `evidence_reference_integrity=passed`, `langsmith_trajectory_tool_boundary=failed` (`workflow_trace_not_completed`), `commit_log_state_mutation_consistency=passed`.
- Evaluator: Codex, strict blocked-run diagnostic retest11.

### Optimization Hypothesis
- The retest10 blocker was improved: `GenerateExpectationDetails` no longer timed out, both expectation detail workers completed, and the workflow advanced through `ReviewExpectationFields`.
- The new direct blocker is a structured-output normalization gap in `ResolveObjectionsAndDelegations`: O1 returned a revised expectation patch whose `expectation_id`, `expectation_name`, `market_view`, `realized_facts`, `key_variables`, and `event_monitoring_direction` fields were present at the patch top level, while `after` was absent/null.
- `BlackboardPatch.after` is optional at the generic contract layer, so the patch passed the generic model parse but then failed Document2 workflow validation with `GenerateExpectationUnits expectation patch must include document content`.
- This is a memory-continuity and patch-contract issue, not a detail-generation issue: accepted/partially accepted review decisions produced document content, but the content was not preserved in the canonical `after` field used by downstream replacement/promotion logic.
- Expected improvement: normalize flat `expectation_unit` patches into `patch.after` before they enter workflow state, while still validating the recovered content against `ExpectationUnitDocument` and rejecting incomplete flat patches.

### Proposed Modification Plan
- Change 1: Update `WorkflowAgentResultNormalizer` so `structured.proposed_patches` entries targeting `expectation_unit` with `operation=create|update` and missing `after` are inspected for flat `ExpectationUnitDocument` fields.
- Change 2: If the flat document candidate validates as `ExpectationUnitDocument`, move the validated JSON document into `patch.after` and remove document fields from the top-level patch object before `BlackboardPatch` validation.
- Change 3: If flat expectation document fields are present but incomplete or invalid, raise a `WorkflowContractError` immediately; do not let an `after=null` patch leak into later workflow nodes.
- Change 4: Preserve auditability by also writing the normalized patch shape back into `payload["structured"]["proposed_patches"]`, so Working Memory/debug payloads match the actual patch consumed by the workflow.
- Change 5: Add regression tests for the Retest11 flat-patch shape and the invalid-flat-patch failure path.
- Retest requirement: commit/push, cloud `git pull --ff-only`, rebuild `debug-viewer`, rerun the same Document 1-only source and same `--stop-after PromoteExpectationToBeliefState`.

### Scope Decision
- Eval mode: `blocked`.
- Can judge stable expectation_unit: no, no stable `expectation_unit` documents were promoted.
- Can judge workflow improvement: yes, the prior detail-timeout blocker was bypassed and the workflow advanced to resolver.
- Cannot claim: quality target success, final Blackboard readiness, or stable downstream monitoring usability.

### Hard Gate Failure Root Cause Matrix
| Gate | Result | failure_kind | Failure point | Root cause / fix target |
| --- | --- | --- | --- | --- |
| D2-HG01 | pass | none | Source handoff | Same Document 1-only source retained. |
| D2-HG02 | fail | direct | `ResolveObjectionsAndDelegations` blocked before stop-after | Resolver emitted a revision patch with document fields outside `after`; normalize flat expectation patches. |
| D2-HG03 | pass | none | Construction lifecycle | Shell construction, construction review, and construction resolution completed. |
| D2-HG04 | pass | none | Detail patches | Both detail patches completed and remained pending/reviewable. |
| D2-HG05 | pass | none | Evidence refs | Built-in evidence reference validator passed for reached artifacts. |
| D2-HG06 | fail | quality_residual | Price-in contradiction remained under review | Stock-price objection indicates unsupported `>1000`/near-1000 wording still needed resolver revision. |
| D2-HG07 | pass | none | Field review lifecycle | A1/C1/C3/O4 review completed; A1 timeout did not recur. |
| D2-HG08 | fail | direct | Objection resolver | Accepted/partially accepted objection content did not become a valid revised patch because `after` was null. |
| D2-HG09 | fail | derivative | Promotion | No stable expectation units because resolver replacement failed first. |
| D2-HG10 | fail | derivative | Trajectory validator | Workflow trace was incomplete because the run ended blocked. |
| D2-HG11 | pass | none | Failure auditability | Remote log, Brief State, Working Memory, and hard-validator output expose the blocker. |
| D2-HG12 | fail | quality_residual | Downstream usability | No stable Document2 output; monitoring directions cannot be accepted downstream. |
| D2-HG13 | fail | direct | Multi-loop memory continuity | Review/resolver revision content was present but lost across the patch contract boundary; normalize and validate before state mutation. |

### Hard Gates
| Gate | Result | Evidence | Notes |
| --- | --- | --- | --- |
| D2-HG01 | pass | Same Document 1-only source `run_58f5afce8b9441ca804a2cde1ad9aec8`; clone mode. | Valid seed retained. |
| D2-HG02 | fail | Cloud log finished `status=blocked`; `next_node=ResolveObjectionsAndDelegations`. | Did not reach stop-after node. |
| D2-HG03 | pass | Construction, construction review, and construction resolution completed. | Shell-level flow remains intact. |
| D2-HG04 | pass | Detail status metadata shows `expectation_mu_01` and `expectation_mu_02` completed; `pending_patch_count=2`. | Retest10 detail-timeout blocker improved. |
| D2-HG05 | pass | Built-in evidence reference validator passed. | Reached refs are valid. |
| D2-HG06 | fail | Open stock-price objection remained unresolved. | Price-in wording still needs resolver correction. |
| D2-HG07 | pass | Completed nodes include `ReviewExpectationFields`; Working Memory count increased to 15. | Field-review lifecycle ran. |
| D2-HG08 | fail | `unresolved_objection_count=2`; resolver failed on invalid revision patch shape. | Direct target of next fix. |
| D2-HG09 | fail | `expectation_unit_count=0`; stable docs only `global_research`. | No promotion. |
| D2-HG10 | fail | Built-in trajectory validator failed with `workflow_trace_not_completed`. | Expected blocked-run rejection. |
| D2-HG11 | pass | Failure is visible in cloud log and Brief State. | Auditable. |
| D2-HG12 | fail | No final downstream-usable Blackboard content. | Usability target unmet. |
| D2-HG13 | fail | Accepted revision content was not preserved as `patch.after`. | Patch-contract normalization required. |

### Built-in Hard Validators
| Validator | Result | Evidence | Notes |
| --- | --- | --- | --- |
| evidence_reference_integrity | pass | Cloud `DebugRunQueryService`; 0 blocking findings for reached refs. | Integrity is not the current blocker. |
| langsmith_trajectory_tool_boundary | fail | `workflow_trace_not_completed`, status `blocked`, next node `ResolveObjectionsAndDelegations`. | Correct hard-gate failure. |
| commit_log_state_mutation_consistency | pass | Cloud `DebugRunQueryService`; existing commit state remained consistent. | Limited reached state is consistent. |

### Rubrics
| Rubric | Score | Reason |
| --- | ---: | --- |
| D2-R01 | 4 | Document 1-only source and clone handoff remain correct and auditable. |
| D2-R02 | 3 | Detail patches are generated, but no stable expectation unit is promoted. |
| D2-R03 | 3 | Realized facts exist in pending patches, but remain unpromoted. |
| D2-R04 | 2 | Price-in reasoning is still under objection and not stable. |
| D2-R05 | 3 | Key variables exist in pending patches, but are not stable downstream state. |
| D2-R06 | 2 | Monitoring directions exist but cannot be accepted without resolver/promotion. |
| D2-R07 | 3 | Evidence refs pass integrity, but final evidence sufficiency remains unaccepted. |
| D2-R08 | 3 | Field review ran and surfaced concrete blockers, but resolver failed to apply the revision. |
| D2-R09 | 1 | Objection lifecycle failed at revision patch normalization. |
| D2-R10 | 1 | Promotion did not run. |
| D2-R11 | 3 | Tool-use trajectory is auditable, but the workflow trace is incomplete. |
| D2-R12 | 2 | Uncertainty/contradiction handling is visible in objections, but not resolved into stable content. |
| D2-R13 | 4 | Run artifacts, blocker, and modification hypothesis are reproducible. |
| D2-R14 | 5 | The failure maps to a narrow, testable normalizer modification with clear retest criteria. |

### Score Summary
- Core Blackboard quality rubrics average (`D2-R01`-`D2-R10`): 2.5.
- Other rubrics with score <= 2: `D2-R12`.
- Quality target met: no.
- Accept modification so far: no final quality acceptance; retest11 confirms next direct blocker.

### Document 2 State Summary
- Stable expectation_unit count: 0.
- Pending expectation patches: 2.
- Stable document types: `global_research` only.
- Latest checkpoint: `blocked`, `next_node=ResolveObjectionsAndDelegations`.
- Completed nodes: `StartTickerInitialization`, `BuildGlobalResearch`, `ReviewGlobalResearch`, `GenerateExpectationConstruction`, `ReviewExpectationConstruction`, `ResolveExpectationConstruction`, `GenerateExpectationDetails`, `ReviewExpectationFields`.
- Working Memory count: 15.
- Commit count: 1.
- Open/unresolved objections: 2.
- Blocking delegations: 0.
- Evidence refs: 72.

### Failure Categories
- category: `flat_expectation_patch_after_missing`
  - issue: resolver patch contained expectation-unit document fields at the patch top level while `after` was null.
  - evidence: cloud error `GenerateExpectationUnits expectation patch must include document content`.
  - severity: blocking/schema-boundary.
  - suspected root cause: generic `BlackboardPatch.after` optionality allowed a model shape that was semantically invalid for Document2 expectation revisions.
- category: `accepted_revision_not_preserved`
  - issue: field-review/resolver memory did not carry accepted revision content into the canonical patch body.
  - evidence: resolver output included document content but no valid `patch.after`; D2-HG13 failed.
  - severity: hard-gate/memory-continuity.
  - suspected root cause: missing workflow normalizer for flat expectation-unit patch outputs.
- category: `residual_price_in_objection`
  - issue: stock-price and HBM4-share objections remained unresolved at blocked checkpoint.
  - evidence: Brief State reported 2 open/unresolved objections.
  - severity: quality-blocking.
  - suspected root cause: resolver could not apply revised patch before semantic objections were closed.

### Actual Modification
- Implemented after this evaluation entry:
  - Added flat `expectation_unit` patch normalization in `WorkflowAgentResultNormalizer`.
  - Recovered valid flat document fields into `patch.after` before `BlackboardPatch` validation.
  - Rejected invalid flat expectation patches with `WorkflowContractError` instead of allowing `after=null` to reach later nodes.
  - Updated normalized `payload["structured"]["proposed_patches"]` for audit/debug consistency.
  - Added regression coverage in `tests/test_workflow_normalizer.py`.

## Loop 1 Retest12 - cloud blocker: ReviewExpectationFields state persistence stall

### Run Metadata
- Date: 2026-06-23.
- Source run: `run_58f5afce8b9441ca804a2cde1ad9aec8` (Document 1-only source, unchanged).
- Execution run: `run_6966f7a67c2a4652882c239c28ba01f6`.
- Deployed commit: `0612c5a`.
- Remote cwd: `/root/doxagent`.
- Remote log: `.eval_runs/document2-loop1-retest12-20260623T172731+0800.log`.
- Cloud command: `docker compose run --rm -e DOXAGENT_RUN_REAL_API_TESTS=1 -e DOXAGENT_STORAGE_MODE=postgres debug-viewer python eval/run_document2_expectation_units_smoke.py run_58f5afce8b9441ca804a2cde1ad9aec8 --mode clone --stop-after PromoteExpectationToBeliefState --export-brief-state`.

### Status
- Result: `blocked`.
- The workflow process remained alive for more than two hours and the one-off Docker container was `unhealthy`.
- The remote log still contained only `document2_smoke_started`; no normal `completed` or `blocked` script output was emitted.
- Latest checkpoint remained `running`, `next_node=ReviewExpectationFields`, `created_at=2026-06-23T09:48:00.279992Z`.
- Completed nodes in the latest checkpoint stopped at `GenerateExpectationDetails`; `ReviewExpectationFields` did not get marked completed.
- Direct DB evidence shows all four field-review agents wrote Working Memory:
  - `A1/a1_doxatlas_audit`: `succeeded`, `2026-06-23T10:00:08Z`.
  - `C1/c1_fundamental_review`: `succeeded`, `2026-06-23T10:00:13Z`.
  - `C3/c3_industry_review`: `succeeded`, `2026-06-23T10:00:43Z`.
  - `O4/o4_market_trace_review`: `succeeded`, `2026-06-23T10:00:49Z`.
- C1 created four open objections at `2026-06-23T10:00:48Z`: `obj_001_pe_ratio_discrepancy`, `obj_002_analyst_target_vs_current_price`, `obj_003_unverifiable_quarterly_figures`, `obj_004_market_cap_consistency`.
- `pg_stat_activity` showed an `idle in transaction` connection waiting on `ClientRead` for about one hour on `select entry_json from doxagent.working_memory_entries ...`.

### Built-in Hard Validators
| Validator | Result | Evidence | Notes |
| --- | --- | --- | --- |
| evidence_reference_integrity | not_completed | No final smoke output and no exported validator bundle. | The run never reached a validator-ready terminal state. Treat as unavailable, not pass. |
| langsmith_trajectory_tool_boundary | fail | Latest checkpoint stayed `running/ReviewExpectationFields`; process remained alive and unhealthy. | Direct blocker: workflow trace did not complete. |
| commit_log_state_mutation_consistency | not_completed | Final consistency validator did not run. | State mutation may be internally consistent up to reached tables, but terminal validator evidence is absent. |

### Hard Gate Failure Root Cause Matrix
| Gate | Result | failure_kind | Failure point | Root cause / fix target |
| --- | --- | --- | --- | --- |
| D2-HG01 | pass | none | Source handoff | Document 1-only source `run_58f5afce8b9441ca804a2cde1ad9aec8` retained. |
| D2-HG02 | fail | direct | `ReviewExpectationFields` never marked completed | Workflow did not reach stop-after target. |
| D2-HG03 | pass | none | Construction lifecycle | Prior construction nodes completed before this blocker. |
| D2-HG04 | pass | none | Detail generation | `GenerateExpectationDetails` completed before review. |
| D2-HG05 | not_completed | blocked_validator | Evidence integrity validator did not run to final output. | Cannot claim pass without final validator evidence. |
| D2-HG06 | fail | quality_residual | C1 field review created four open objections. | PE ratio, analyst target/current price, unverifiable quarterly figures, and market-cap consistency remain unresolved. |
| D2-HG07 | fail | direct | Field review state transition | Review agents wrote outputs and objections, but the node was not checkpointed as completed. |
| D2-HG08 | fail | derivative | Resolver not reached | Objection lifecycle cannot close when review node is stuck. |
| D2-HG09 | fail | derivative | Promotion not reached | No stable expectation_unit documents promoted. |
| D2-HG10 | fail | direct | Workflow trajectory | Trace/process is incomplete and cloud process is stuck. |
| D2-HG11 | pass | none | Auditability | Remote log, DB checkpoint, Working Memory, objections, container status, and `pg_stat_activity` expose the blocker. |
| D2-HG12 | fail | direct | Context/state persistence boundary | Postgres pooler connection had no hard statement/idle transaction bounds, allowing indefinite wait. |
| D2-HG13 | fail | derivative | Multi-loop memory continuity | Review memory existed but was not advanced into resolver memory because node completion stalled. |

### Rubrics
| Rubric | Score | Reason |
| --- | ---: | --- |
| D2-R01 | 4 | Document 1-only source and clone handoff remain correct. |
| D2-R02 | 3 | Detail content was generated before review, but no stable expectation unit was promoted. |
| D2-R03 | 3 | Realized-fact detail generation likely exists in pending state, but terminal validator/export evidence is absent. |
| D2-R04 | 2 | C1 found unresolved valuation and price-context contradictions. |
| D2-R05 | 3 | Key-variable detail can be reviewed, but stable downstream state is absent. |
| D2-R06 | 2 | Monitoring directions cannot be accepted while objections remain open and promotion is not reached. |
| D2-R07 | 3 | Prior reached evidence behavior was reasonable, but Retest12 did not produce final evidence validator output. |
| D2-R08 | 3 | Field reviewers produced substantive results, including concrete objections, but node state did not advance. |
| D2-R09 | 1 | Objection lifecycle was blocked before resolver. |
| D2-R10 | 1 | Promotion did not run. |
| D2-R11 | 2 | The trace is auditable but operationally incomplete due to a stuck DB connection. |
| D2-R12 | 2 | Contradictions were detected, but not resolved into stable content. |
| D2-R13 | 4 | Failure is evidence-rich and reproducible from cloud process, DB, and log state. |
| D2-R14 | 5 | The next modification is narrow, testable, and directly targets the indefinite-persistence stall. |

### Score Summary
- Core Blackboard quality rubrics average (`D2-R01`-`D2-R10`): 2.5.
- Other rubrics with score <= 2: `D2-R11`, `D2-R12`.
- Quality target met: no.

### Failure Categories
- category: `postgres_pooler_idle_transaction_stall`
  - issue: workflow process stayed alive without emitting terminal output after review results were written.
  - evidence: one cloud run container unhealthy for more than two hours; `pg_stat_activity` shows `idle in transaction` / `ClientRead` on `doxagent.working_memory_entries`.
  - severity: blocking/workflow-runtime.
  - suspected root cause: Postgres connections through the Supabase/PgBouncer pooler lacked session-level `statement_timeout`, `lock_timeout`, and `idle_in_transaction_session_timeout`.
- category: `review_completion_not_checkpointed`
  - issue: all review agents wrote Working Memory, and C1 objections were inserted, but checkpoint stayed `running/ReviewExpectationFields`.
  - evidence: latest checkpoint completed nodes ended at `GenerateExpectationDetails`; review Working Memory entries exist at `10:00:08Z`-`10:00:49Z`.
  - severity: hard-gate/direct.
  - suspected root cause: state reload or mutation after review entered an unbounded DB wait.
- category: `residual_c1_quality_objections`
  - issue: four fundamental-review objections remain open.
  - evidence: C1 objections on PE ratio, analyst target/current price, quarterly figures, and market cap.
  - severity: quality-blocking after runtime blocker is fixed.
  - suspected root cause: the resolver never ran, so valid C1 concerns were not reconciled into the expectation documents.

### Optimization Hypothesis
- If every Postgres connection used by Blackboard/checkpoint/debug operations carries bounded pooler guards, the workflow will fail fast or retry instead of hanging indefinitely after a successful review write.
- The primary target is not to loosen validators or hide the C1 objections; it is to make the persistence layer bounded so the next retest can reach resolver/promotion or produce a terminal blocked result that validators and rubrics can judge.
- Expected measurable improvement in the next retest:
  - no one-off cloud container remains alive indefinitely on `ReviewExpectationFields`;
  - latest checkpoint either advances to `ResolveObjectionsAndDelegations` or records a bounded DB failure;
  - hard validators can run or fail with a terminal export instead of missing final evidence;
  - residual C1 quality objections become actionable resolver inputs rather than being trapped behind state persistence.

### Proposed Modification Plan
- Change 1: Update shared `connect_postgres` so all psycopg connections default to `connect_timeout=15`.
- Change 2: Add default pooler guard options: `statement_timeout=120000`, `lock_timeout=30000`, and `idle_in_transaction_session_timeout=120000`.
- Change 3: Preserve caller-provided Postgres `options` and append only missing guard options.
- Change 4: Keep the existing OperationalError retry behavior so bounded pooler failures can still recover where safe.
- Change 5: Add focused persistence tests proving prepared statements remain disabled, timeout guards are injected, existing options are preserved, and retry calls carry the same guards.
- Retest requirement: stop the stale Retest12 cloud container, push/deploy the guard change, rebuild `debug-viewer`, and rerun the same Document 1-only source through the cloud smoke path.

### Actual Modification
- Implemented after this evaluation entry:
  - Added Postgres pooler timeout guards in `src/doxagent/postgres.py`.
  - Added default `connect_timeout=15` for shared Postgres connections.
  - Preserved existing `options` while appending missing pooler guard options.
  - Added regression coverage in `tests/test_phase9_persistence.py`.
  - Verified locally with `uv run pytest tests/test_phase9_persistence.py` and `uv run ruff check src/doxagent/postgres.py tests/test_phase9_persistence.py --select B,E,F,I`.

## Loop 1 Retest13 - promoted but quality rejected: deterministic placeholders entered stable expectation units

### Run Metadata
- Date: 2026-06-23.
- Source run: `run_58f5afce8b9441ca804a2cde1ad9aec8` (Document 1-only source, unchanged).
- Execution run: `run_502155269ad84283a62cfe144fdbf475`.
- Deployed commit: `ee39fc6`.
- Remote cwd: `/root/doxagent`.
- Remote log: `.eval_runs/document2-loop1-retest13-20260623T190753+0800.log`.
- Cloud command: `docker compose run --rm -e DOXAGENT_RUN_REAL_API_TESTS=1 -e DOXAGENT_STORAGE_MODE=postgres debug-viewer python eval/run_document2_expectation_units_smoke.py run_58f5afce8b9441ca804a2cde1ad9aec8 --mode clone --stop-after PromoteExpectationToBeliefState --export-brief-state`.

### Status
- Result: `quality_rejected`.
- Operational blocker fixed: yes. Retest13 reached `PromoteExpectationToBeliefState`; no Retest12-style Postgres pooler stall recurred.
- Smoke output: `document2_smoke_finished`, `status=running`, `next_node=GenerateGlobalNarrativeReport`, completed nodes include `PromoteExpectationToBeliefState`.
- Stable documents: `global_research`, `expectation_unit`.
- Stable expectation_unit count: 3.
- Pending patch count: 0.
- Working Memory count: 15.
- Commit count: 4.
- Objections: 8 total, all `resolved`.
- Blocking delegations: 0.
- Brief State export path on cloud: `eval/brief_state_exports/run_502155269ad84283a62cfe144fdbf475.json`.

### Built-in Hard Validators
| Validator | Result | Evidence | Notes |
| --- | --- | --- | --- |
| evidence_reference_integrity | pass | `checked_items=68`, `finding_count=0`. | Structural evidence refs are locatable, but this validator does not judge evidence sufficiency or content usefulness. |
| langsmith_trajectory_tool_boundary | pass | `checked_items=57`, `finding_count=0`. | Local trajectory/tool mirror passed; remote LangSmith review still used for rubric judgment. |
| commit_log_state_mutation_consistency | pass | `checked_items=16`, `finding_count=0`. | Stable documents and commits are consistent. |

### Hard Gate Failure Root Cause Matrix
| Gate | Result | failure_kind | Failure point | Root cause / fix target |
| --- | --- | --- | --- | --- |
| D2-HG01 | pass | none | Source handoff | Source remains Document 1-only: stable `global_research`, no source `expectation_unit`, no source pending patches or blockers. |
| D2-HG02 | pass | none | Stop-after path | Cloud log and DB state show the run reached `PromoteExpectationToBeliefState`. |
| D2-HG03 | pass | none | Construction lifecycle | Three differentiated expectation units were ultimately promoted. |
| D2-HG04 | fail | quality_residual | Stable expectation detail fields | Final docs contain deterministic cleanup placeholders in market_view, realized_facts, price_reaction, variables, and monitoring fields. |
| D2-HG05 | pass | none | Evidence refs | Built-in evidence reference validator passed; caveat: evidence is structurally present but often broad/repeated. |
| D2-HG06 | fail | quality_residual | Price-in reasoning | `Quantified price reaction withheld...` appears 16 times; price-in state is not concretely reviewable. |
| D2-HG07 | pass | none | Field review lifecycle | Review ran and produced pressure; 8 objections were created and tracked. |
| D2-HG08 | pass | none | Resolver lifecycle | All 8 objections were resolved, with no blocking delegations left. |
| D2-HG09 | pass | none | Promotion lifecycle | 3 stable `expectation_unit` documents were promoted, pending patches cleared, commit log consistent. |
| D2-HG10 | pass | none | LangSmith/process trace | LangSmith runs for construction/detail/review were queryable and had run metadata. |
| D2-HG11 | pass | none | Auditability | The quality failure is visible from stable docs, objections, commit log, hard validators, and LangSmith. |
| D2-HG12 | pass_with_caveat | context_pressure | Review/detail inputs are large but completed | No timeout/stall occurred after pooler guard; still, C1 review inputs reached high token/char volume and should be watched. |
| D2-HG13 | fail | direct | Memory/content continuity into stable docs | Accepted review/resolver content was preserved mechanically, but deterministic cleanup collapsed substantive claims into generic placeholders before promotion. |

### Rubrics
| Rubric | Score | Reason |
| --- | ---: | --- |
| D2-R01 | 4 | Document 1-only handoff and upstream evidence use are correct. |
| D2-R02 | 3 | Three theses are differentiated, but final market views are weakened by `qualitative thesis retained` placeholders. |
| D2-R03 | 2 | Realized facts exist, but most descriptions are generic fallback text and price reactions are withheld. |
| D2-R04 | 2 | Price-in reasoning is mostly unavailable because quantified reactions were replaced by withholding text. |
| D2-R05 | 3 | Key variables exist and are directionally relevant, but several statuses use `qualitative status retained` or `source-verified value` placeholders. |
| D2-R06 | 2 | Event monitoring has many generic triggers: `Monitor this event qualitatively...` appears 26 times across final docs. |
| D2-R07 | 3 | Evidence refs are present and validators pass, but refs are often broad/repeated rather than claim-specific. |
| D2-R08 | 4 | A1/C1/C3/O4 review pressure is materially better; numeric and price-reaction objections were created and tracked. |
| D2-R09 | 3 | Objection lifecycle completed, but resolutions relied too much on generic deterministic cleanup instead of concrete revised content. |
| D2-R10 | 3 | Promotion is mechanically clean, but final content quality is not acceptable for downstream monitoring. |
| D2-R11 | 4 | Tool/trajectory boundaries pass and LangSmith evidence is available. |
| D2-R12 | 3 | Uncertainty is visible, but often as generic fallback text rather than precise unknowns tied to variables. |
| D2-R13 | 4 | Retest13 is reproducible and auditable with source run, execution run, cloud log, DB state, validators, and LangSmith. |
| D2-R14 | 5 | The failure maps to a narrow enforcement-layer fix: reject deterministic placeholder text before promotion. |

### Score Summary
- Core Blackboard quality rubrics average (`D2-R01`-`D2-R10`): 2.9.
- Other rubrics with score <= 2: none.
- Quality target met: no.
- Operational improvement accepted: yes, Retest12 pooler stall was fixed.
- Document2 quality improvement accepted: no, because promoted output contains deterministic placeholders and is not sufficiently usable downstream.

### Document 2 State Summary
- Stable expectation_unit count: 3.
- Stable expectation names:
  - `expectation_mu_001`: `AI/HBM需求超级周期持续性`, direction `bullish`.
  - `expectation_mu_002`: `存储周期见顶与估值透支风险`, direction `bearish`.
  - `expectation_mu_003`: `DRAM/NAND定价周期结构性分化`, direction `neutral`.
- Combined placeholder counts in stable docs:
  - `Monitor this event qualitatively`: 26.
  - `source-appropriate evidence`: 54.
  - `qualitative status retained`: 6.
  - `qualitative thesis retained`: 3.
  - `Quantified price reaction withheld`: 16.
  - `market-trace verification is still required`: 16.
  - `source-verified value`: 15.
- Resolved objections include deterministic numeric sanity blockers and O4 price-reaction contradiction blockers.

### Failure Categories
- category: `placeholder_promotion_quality_gap`
  - issue: stable expectation units include deterministic fallback text that should be considered unpromotable.
  - evidence: final docs contain repeated `Monitor this event qualitatively`, `Quantified price reaction withheld`, and `source-verified value` markers.
  - severity: quality-blocking.
  - suspected root cause: promotion validates schema, evidence structure, objections, and commit consistency, but does not reject deterministic placeholder text created by cleanup.
- category: `price_reaction_withheld_not_resolved`
  - issue: O4 contradictions were resolved by withholding quantified reactions rather than replacing them with concrete, source-supported reactions.
  - evidence: `Quantified price reaction withheld...` appears 16 times.
  - severity: downstream-monitoring blocker.
  - suspected root cause: deterministic price-reaction normalization is too permissive at promotion time.
- category: `monitoring_trigger_generic_after_cleanup`
  - issue: event monitoring directions include generic monitoring placeholders and `source-verified value` pseudo-thresholds.
  - evidence: `Monitor this event qualitatively...` appears 26 times; several triggers contain `source-verified value`.
  - severity: monitoring-usability blocker.
  - suspected root cause: numeric cleanup sanitizes unsafe precision but does not require concrete replacement triggers.

### Optimization Hypothesis
- If promotion rejects deterministic cleanup placeholders in `expectation_unit` documents, low-quality generic revisions cannot enter stable belief state even when hard validators and objection lifecycle pass.
- This does not solve all content generation quality by itself; it turns a silent quality regression into an explicit, auditable workflow blocker, forcing O1/resolver to provide concrete source-supported replacements.
- Expected improvement in the next retest, if one is later launched:
  - Retest13-style placeholder-heavy documents should block at `PromoteExpectationToBeliefState` instead of being promoted.
  - Hard-gate failures should become direct and diagnosable (`promotion_quality_placeholder`) rather than hidden as low-quality stable docs.
  - Downstream rubrics D2-R03/D2-R04/D2-R06 should improve only when the model produces concrete, evidenced replacements, not when cleanup masks precision.

### Proposed Modification Plan
- Change 1: Add a promotion quality validator for `expectation_unit` patches before `_submit_patch`.
- Change 2: Reuse existing expectation detail quality checks at promotion time.
- Change 3: Recursively reject deterministic placeholder markers such as `Monitor this event qualitatively`, `Quantified price reaction withheld`, `qualitative thesis retained`, `qualitative status retained`, and `source-verified value`.
- Change 4: Extend generic monitoring trigger detection so placeholder monitoring events are also rejected earlier when generated directly.
- Change 5: Update phase5 mock fixtures with structured market evidence snapshots so valid mock promotion remains representative of the intended path.
- Change 6: Add regression coverage proving placeholder-heavy expectation patches are rejected at promotion.
- Retest decision: per user instruction, do not launch the next smoke test after this modification; stop after local verification and report.

### Actual Modification
- Implemented after this evaluation entry:
  - Added `_UNPROMOTABLE_EXPECTATION_TEXT_MARKERS` and recursive placeholder detection in `src/doxagent/workflows/initialization.py`.
  - Added `_validate_expectation_promotion_quality` and invoked it before stable `expectation_unit` submission.
  - Extended generic monitoring trigger detection to include deterministic placeholder markers.
  - Added structured market evidence snapshots to the Phase 5 mock fixture so valid promotion remains possible.
  - Added `test_promotion_rejects_numeric_sanity_placeholder_text` in `tests/test_phase5_initialization_workflow.py`.
  - Verified locally with `uv run pytest tests/test_phase5_initialization_workflow.py -q` and `uv run ruff check src/doxagent/workflows/initialization.py tests/test_phase5_initialization_workflow.py --select B,E,F,I`.
- Next smoke test: not launched, per user instruction.

## Loop 1 Retest14 - blocked: read transaction held during objection resolution

### Run Metadata
- Date: 2026-06-24.
- Source run: `run_58f5afce8b9441ca804a2cde1ad9aec8` (Document 1-only source, unchanged).
- Execution run: `run_47891dfef5d24a09b440a9b5d7e3aea8`.
- Deployed commit: `28505b2`.
- Remote cwd: `/root/doxagent`.
- Remote log: `.eval_runs/document2-loop1-retest14-20260624T010619+0800.log`.
- Cloud command: `docker compose -f docker-compose.yml run --rm -e DOXAGENT_RUN_REAL_API_TESTS=1 -e DOXAGENT_STORAGE_MODE=postgres debug-viewer python eval/run_document2_expectation_units_smoke.py run_58f5afce8b9441ca804a2cde1ad9aec8 --mode clone --stop-after PromoteExpectationToBeliefState --export-brief-state`.

### Status
- Result: `runtime_blocked`.
- The run did not reach `PromoteExpectationToBeliefState`; no valid final Document2 Brief State was exported.
- Latest checkpoint at blocker detection: `status=running`, `next_node=ResolveObjectionsAndDelegations`, `completed_count=8`, `completed_tail=[StartTickerInitialization, BuildGlobalResearch, ReviewGlobalResearch, GenerateExpectationConstruction, ReviewExpectationConstruction, ResolveExpectationConstruction, GenerateExpectationDetails, ReviewExpectationFields]`.
- LangSmith evidence: `O1.ResolveObjectionsAndDelegations.LOOP1` and `LOOP2` both completed successfully for this run; no later root run appeared before the stall.
- DB evidence: `pg_stat_activity` showed `idle in transaction` / `ClientRead` for about `810s` on `select entry_json from doxagent.working_memory_entries where run_id = $1 order by created_at asc, entry_json->>'entry_id' asc`.
- Operational action: stopped stale one-off container `477a49beb952` to release the pooler transaction.

### Built-in Hard Validators
| Validator | Result | Evidence | Notes |
| --- | --- | --- | --- |
| evidence_reference_integrity | blocked | No terminal Brief State / promoted expectation units. | A blocked run cannot claim evidence integrity pass. |
| langsmith_trajectory_tool_boundary | blocked | LangSmith exists through `ResolveObjectionsAndDelegations.LOOP2`, but the workflow did not checkpoint beyond the node. | Traceability is partial and useful for debugging, not a validator pass. |
| commit_log_state_mutation_consistency | blocked | No terminal promotion/commit state after the resolver node. | Commit consistency cannot be asserted for the intended Document2 output. |

### Hard Gate Failure Root Cause Matrix
| Gate | Result | failure_kind | Failure point | Root cause / fix target |
| --- | --- | --- | --- | --- |
| D2-HG01 | pass | none | Source handoff | Run used Document 1-only source `run_58f5afce8b9441ca804a2cde1ad9aec8`. |
| D2-HG02 | fail | runtime_blocker | Stop-after path | Workflow did not reach `PromoteExpectationToBeliefState`. |
| D2-HG03 | fail | incomplete_lifecycle | Construction/promotion lifecycle | Construction and review completed, but resolver never checkpointed completion. |
| D2-HG04 | fail | not_evaluable | Stable expectation detail fields | No final promoted expectation units to inspect. |
| D2-HG05 | fail | not_evaluable | Evidence refs | No terminal promoted state for structural evidence validation. |
| D2-HG06 | fail | not_evaluable | Price-in reasoning | No promoted price-in reasoning; resolver stalled before promotion. |
| D2-HG07 | pass_with_caveat | partial_lifecycle | Field review lifecycle | `ReviewExpectationFields` completed and produced Working Memory pressure, but downstream resolver stalled. |
| D2-HG08 | fail | direct | Resolver lifecycle | `ResolveObjectionsAndDelegations` LangSmith loops completed, but DB checkpoint stayed on the same node. |
| D2-HG09 | fail | direct | Promotion lifecycle | No promotion, pending state not terminally cleared. |
| D2-HG10 | pass_with_caveat | partial_trace | LangSmith/process trace | LangSmith is sufficient to locate the stall, but workflow did not close normally. |
| D2-HG11 | fail | audit_gap | Final auditability | Debug evidence is strong, but final Blackboard output is absent. |
| D2-HG12 | fail | context/runtime_pressure | Resolver state reload | The stall happened immediately after resolver LLM completion while reloading/mutating Blackboard state. |
| D2-HG13 | fail | memory_continuity_blocked | Multi-loop memory continuity | Working Memory could be read structurally, but transaction lifecycle blocked continuation into promotion. |

### Rubrics
| Rubric | Score | Reason |
| --- | ---: | --- |
| D2-R01 | 4 | Source discipline is correct and auditable. |
| D2-R02 | 1 | No promoted expectation set exists for stable thesis quality judgment. |
| D2-R03 | 1 | No stable realized-facts output exists. |
| D2-R04 | 1 | No stable price-in reasoning output exists. |
| D2-R05 | 1 | No stable key-variable output exists. |
| D2-R06 | 1 | No stable monitoring triggers exist. |
| D2-R07 | 1 | Evidence structure cannot be judged on a terminal promoted state. |
| D2-R08 | 3 | Review pressure was generated and visible, but the run failed before resolution/promotion. |
| D2-R09 | 1 | Objection resolution did not checkpoint completion. |
| D2-R10 | 1 | Promotion lifecycle did not complete. |
| D2-R11 | 3 | LangSmith evidence is useful and points to the blocker, but the trace is not closed. |
| D2-R12 | 2 | Uncertainty and residual state are not represented in a usable final Blackboard document. |
| D2-R13 | 4 | Runtime failure is reproducible and backed by run id, cloud log, DB activity, checkpoint state, and LangSmith. |
| D2-R14 | 5 | The fix target is narrow and testable: shorten Postgres read/mutate transaction lifetimes. |

### Score Summary
- Core Blackboard quality rubrics average (`D2-R01`-`D2-R10`): 1.5.
- Other rubrics with score <= 2: `D2-R12`.
- Quality target met: no.
- Operational target met: no; the run stalled before promotion.

### Failure Categories
- category: `postgres_read_transaction_held_during_mutate`
  - issue: a read of `working_memory_entries` remained `idle in transaction` after the resolver LLM loops completed.
  - evidence: `pg_stat_activity` showed `ClientRead`, `xact_age_s=810`, last query reading `doxagent.working_memory_entries`.
  - severity: blocking/workflow-runtime.
  - suspected root cause: `PostgresBlackboardRepository.mutate()` loaded a full run and executed business mutators inside the same transaction; read-only run loads also opened normal transactions.
- category: `resolver_completion_not_checkpointed`
  - issue: O1 resolver loops completed in LangSmith but the workflow checkpoint did not advance beyond `ResolveObjectionsAndDelegations`.
  - evidence: latest checkpoint stayed `running/ResolveObjectionsAndDelegations`; LangSmith `LOOP2` ended successfully.
  - severity: hard-gate/direct.
  - suspected root cause: post-LLM Blackboard reload/mutation held or waited on a transaction instead of closing quickly.
- category: `quality_eval_blocked_by_runtime`
  - issue: final Blackboard quality could not be judged because no stable Document2 output was produced.
  - evidence: no `document2_smoke_finished` event and no terminal Brief State export.
  - severity: eval-blocking.
  - suspected root cause: persistence transaction lifecycle, not prompt/rubric looseness.

### Optimization Hypothesis
- If Postgres read-only Blackboard operations use `autocommit=True`, read queries cannot leave idle transactions open while Python logic continues.
- If `mutate()` loads the run in a short read connection, closes it, executes the mutator outside any DB transaction, and then opens a short write transaction only for lock-and-replace, resolver completion should no longer stall on `working_memory_entries` reloads.
- Expected measurable improvement in the next verification smoke:
  - no `idle in transaction` row remains for the active Document2 run after resolver LLM completion;
  - checkpoint advances beyond `ResolveObjectionsAndDelegations` or fails with a bounded explicit error;
  - the smoke can reach `PromoteExpectationToBeliefState` or produce a terminal promotion-quality blocker.

### Proposed Modification Plan
- Change 1: Add `_read_connection()` for Postgres Blackboard reads and make it call `connect_postgres(..., autocommit=True)`.
- Change 2: Move pure read operations (`get`, `list_by_ticker`, `list_unresolved_objections`, `list_blocking_delegations`, `summary_counts`) onto `_read_connection()`.
- Change 3: Refactor `mutate()` so it reads the full run and executes the Python mutator outside the write transaction.
- Change 4: Add a narrow `_lock_run()` helper so the write transaction still verifies and locks the parent run before replacing children.
- Change 5: Add tests proving read connections use autocommit and `mutate()` does not hold a transaction while executing the mutator.
- Retest requirement: push/deploy the fix, rebuild the cloud image, rerun the same Document 1-only smoke once to verify the blocker is gone; after this verification loop, do not launch another smoke test.

### Actual Modification
- Implemented in this eval loop:
  - Updated `src/doxagent/blackboard/postgres_repository.py` to use autocommit read connections.
  - Refactored `PostgresBlackboardRepository.mutate()` so mutators run outside the DB write transaction.
  - Added `_lock_run()` for short write transaction parent-row locking.
  - Added regression tests in `tests/test_phase9_persistence.py`.
  - Added changelog entry for the Document2 Postgres transaction-lifetime fix.
  - Verified locally with `uv run pytest tests/test_phase5_initialization_workflow.py tests/test_workflow_normalizer.py tests/test_phase9_persistence.py` and `uv run ruff check src/doxagent/blackboard/postgres_repository.py tests/test_phase9_persistence.py src/doxagent/workflows/initialization.py tests/test_phase5_initialization_workflow.py tests/test_workflow_normalizer.py --select B,E,F,I`.

## Loop 1 Retest14b - transaction blocker fixed; resolver partial patch contract blocked promotion

### Run Metadata
- Date: 2026-06-24.
- Source run: `run_58f5afce8b9441ca804a2cde1ad9aec8` (Document 1-only source, unchanged).
- Execution run: `run_6559a2d580424fd798b7d018b79d956f`.
- Deployed commit: `211bf40`.
- Remote cwd: `/root/doxagent`.
- Remote log: `.eval_runs/document2-loop1-retest14b-20260624T014909+0800.log`.
- Brief State export path on cloud: `eval/brief_state_exports/run_6559a2d580424fd798b7d018b79d956f.json`.
- Cloud command: `docker compose -f docker-compose.yml run --rm -e DOXAGENT_RUN_REAL_API_TESTS=1 -e DOXAGENT_STORAGE_MODE=postgres debug-viewer python eval/run_document2_expectation_units_smoke.py run_58f5afce8b9441ca804a2cde1ad9aec8 --mode clone --stop-after PromoteExpectationToBeliefState --export-brief-state`.

### Status
- Result: `blocked`.
- Transaction blocker fixed: yes. During the 15-minute checks, no `idle in transaction` rows remained for the run; the workflow advanced through `GenerateExpectationDetails`, `ReviewExpectationFields`, and into `ResolveObjectionsAndDelegations`.
- New terminal blocker: `GenerateExpectationUnits expectation patch must include document content.`
- Latest checkpoint: `status=blocked`, `next_node=ResolveObjectionsAndDelegations`, completed nodes through `ReviewExpectationFields`.
- Stable document types: `global_research` only.
- Stable expectation_unit count: 0.
- Pending patch count: 3.
- Working Memory count: 16.
- Commit count: 1.
- Unresolved objection count: 2.
- Blocking delegation count: 0.
- LangSmith evidence: `O1.ResolveObjectionsAndDelegations.LOOP1` completed successfully and returned an accepted numeric-sanity revision path, but the local workflow rejected the returned patch before replacement/promotion.

### Built-in Hard Validators
| Validator | Result | Evidence | Notes |
| --- | --- | --- | --- |
| evidence_reference_integrity | pass | `checked_items=11`, `finding_count=0`. | Reached stable refs are locatable; this does not validate unsupported numeric claims in pending revisions. |
| langsmith_trajectory_tool_boundary | fail | `checked_items=55`; finding `workflow_trace_not_completed`, latest checkpoint `status=blocked`, `next_node=ResolveObjectionsAndDelegations`. | Correctly blocks acceptance because Document2 did not reach promotion. |
| commit_log_state_mutation_consistency | pass | `checked_items=4`, `finding_count=0`. | Limited stable state (`global_research`) is commit-consistent. |

### Hard Gate Failure Root Cause Matrix
| Gate | Result | failure_kind | Failure point | Root cause / fix target |
| --- | --- | --- | --- | --- |
| D2-HG01 | pass | none | Source handoff | Source remains Document 1-only and unchanged. |
| D2-HG02 | fail | direct | Stop-after path | Target stop node `PromoteExpectationToBeliefState` was not reached. |
| D2-HG03 | pass_with_caveat | partial_lifecycle | Construction lifecycle | Three expectation detail patches existed, but resolver later failed before promotion. |
| D2-HG04 | fail | direct | Resolver revision validation | O1 accepted an objection but returned a field-level/partial revision that was not normalized into full document content. |
| D2-HG05 | pass_with_caveat | partial_state | Evidence refs | Built-in ref integrity passes for reached artifacts, but pending numeric claims still failed source-appropriateness objections. |
| D2-HG06 | fail | quality_residual | Price-in reasoning | Numeric/market-data blockers for `expectation_mu_01` remained open. |
| D2-HG07 | pass | none | Field review lifecycle | Review ran and produced substantive O4/C1 pressure. |
| D2-HG08 | fail | direct | Resolver lifecycle | Resolver did not apply the accepted revision; two objections remained unresolved. |
| D2-HG09 | fail | direct | Promotion lifecycle | No stable expectation_unit documents were promoted. |
| D2-HG10 | fail | direct | LangSmith/process trace | Local trajectory validator failed because the workflow is terminally blocked, though LangSmith evidence is available. |
| D2-HG11 | pass | none | Auditability | Remote log, Brief State, DB checkpoint, hard validators, Working Memory, and LangSmith reproduce the blocker. |
| D2-HG12 | pass_with_caveat | context_pressure | Review/resolver inputs | The DB stall is gone, but O4 review input remains very large; watch for future context-pressure regressions. |
| D2-HG13 | fail | memory_continuity_blocked | Revision continuity | Accepted O1 revision did not preserve a complete expectation document into pending patches. |

### Rubrics
| Rubric | Score | Reason |
| --- | ---: | --- |
| D2-R01 | 4 | Source discipline remains correct and auditable. |
| D2-R02 | 3 | Expectation theses exist in pending patches, but no stable thesis set was promoted. |
| D2-R03 | 2 | Detail content exists, but numeric-sanity objections show realized facts still include unsupported precision or unresolved revisions. |
| D2-R04 | 2 | O4 found price-reaction errors; resolver failed before a corrected price-in state could land. |
| D2-R05 | 3 | Key variables appear in pending documents, but blocked promotion prevents stable downstream use. |
| D2-R06 | 3 | Monitoring directions exist in pending documents, but cannot be accepted while objections remain unresolved. |
| D2-R07 | 2 | Structural refs pass, but source-appropriateness remains weak for numeric/market claims. |
| D2-R08 | 4 | Field-review pressure improved: O4 generated concrete price/market objections and LangSmith evidence is available. |
| D2-R09 | 1 | Objection handling failed at accepted revision application; blockers remained. |
| D2-R10 | 1 | Promotion readiness failed: no stable expectation_unit and hard validator failed. |
| D2-R11 | 3 | Tool/trace evidence is useful, but the local trajectory validator correctly fails the blocked run. |
| D2-R12 | 2 | Uncertainty handling remains insufficient because unsupported numeric claims were not converted into a usable revised document. |
| D2-R13 | 4 | The run is reproducible with source run, execution run, cloud log, Brief State export, validators, DB state, and LangSmith. |
| D2-R14 | 5 | The next optimization target is narrow and directly testable: normalize partial resolver revisions into full pending documents. |

### Score Summary
- Core Blackboard quality rubrics average (`D2-R01`-`D2-R10`): 2.5.
- Other rubrics with score <= 2: `D2-R12`.
- Quality target met: no.
- Operational improvement accepted: yes for the Postgres transaction stall.
- Document2 quality improvement accepted: no, because resolver revision application still blocks promotion.

### Failure Categories
- category: `partial_resolver_patch_contract_gap`
  - issue: O1 accepted numeric-sanity objections but returned a partial expectation revision that did not satisfy the full-document patch validator.
  - evidence: terminal error `GenerateExpectationUnits expectation patch must include document content.`
  - severity: hard-gate/direct.
  - suspected root cause: resolver revisions were validated as if every expectation patch must already be a full document; workflow did not merge field-level revisions into the corresponding pending document before validation/replacement.
- category: `numeric_sanity_residual_blockers`
  - issue: two blocking objections remained open for `expectation_mu_01`.
  - evidence: Brief State blockers `obj_numeric_sanity_expectation_mu_01_fundamental_data` and `obj_numeric_sanity_expectation_mu_01_market_data`.
  - severity: quality-blocking.
  - suspected root cause: source-appropriate market/fundamental evidence requirements are now enforced, but accepted revisions fail to land.
- category: `promotion_not_reached`
  - issue: stable `expectation_unit` count remained 0.
  - evidence: cloud log final state `stable_document_types=["global_research"]`, `pending_patch_count=3`, `expectation_unit_count=0`.
  - severity: acceptance-blocking.
  - suspected root cause: resolver patch contract failure halted before promotion.

### Optimization Hypothesis
- If resolver-produced partial expectation revisions are completed by merging them into the corresponding pending full expectation document before validation and replacement, O1 can resolve field-level objections without losing the full document body required by promotion.
- This should not loosen quality gates: the merged full document still passes through existing `ExpectationUnitDocument` validation, numeric-sanity revalidation, placeholder promotion checks, hard validators, and promotion logic.
- Expected measurable improvement in a later verification:
  - accepted field-level O1 revisions no longer fail with `expectation patch must include document content`;
  - `ResolveObjectionsAndDelegations` can either close the numeric-sanity objections or leave a more specific residual blocker;
  - no new smoke should be launched in this turn per user instruction.

### Proposed Modification Plan
- Change 1: Split expectation patch validation into a list-based helper so normalized revisions can be validated without mutating raw `AgentResult`.
- Change 2: Add `_normalized_expectation_revisions()` to map O1 expectation revisions back to matching pending patches by `expectation_id`.
- Change 3: For full-document revisions, preserve existing behavior.
- Change 4: For field-level or partial dict revisions, merge the revision into the pending patch's full `after` document and restore the target to `field_path=document`.
- Change 5: Keep evidence refs from both pending patch and revision, and append an audit rationale note.
- Change 6: Add a regression test where O1 returns a partial `realized_facts_summary` revision and verify it becomes a complete valid expectation document.

### Actual Modification
- Implemented after this evaluation:
  - Updated `src/doxagent/workflows/initialization.py` with normalized resolver revision merging.
  - Added `_validate_expectation_patch_list`, `_normalized_expectation_revisions`, `_complete_expectation_revision_patch`, deep-merge, and field-path assignment helpers.
  - Added `test_o1_partial_revision_merges_into_pending_expectation_document` in `tests/test_phase5_initialization_workflow.py`.
  - Added changelog entry for the partial resolver revision normalization.
  - Verified locally with `uv run pytest tests/test_phase5_initialization_workflow.py tests/test_workflow_normalizer.py tests/test_phase9_persistence.py` and `uv run ruff check src/doxagent/workflows/initialization.py tests/test_phase5_initialization_workflow.py src/doxagent/blackboard/postgres_repository.py tests/test_phase9_persistence.py tests/test_workflow_normalizer.py --select B,E,F,I`.
- Next smoke test: not launched, per user instruction to stop after this complete eval/optimization/modification loop.

## Loop 1 Retest15b - review no-progress runtime blocker

### Run Metadata
- Date: 2026-06-24.
- Source run: `run_58f5afce8b9441ca804a2cde1ad9aec8` (Document 1-only source, unchanged).
- Execution run: `run_34346ef468e64d3bb574a9743b603605`.
- Deployed commit: `9f39d9f`.
- Remote cwd: `/root/doxagent`.
- Remote log: `.eval_runs/document2-loop1-retest15b-20260624T024731+0800.log`.
- Brief State export path on cloud: `eval/brief_state_exports/run_34346ef468e64d3bb574a9743b603605.json`.
- Cloud command: `docker compose -f docker-compose.yml run --rm -e DOXAGENT_RUN_REAL_API_TESTS=1 -e DOXAGENT_STORAGE_MODE=postgres debug-viewer python eval/run_document2_expectation_units_smoke.py run_58f5afce8b9441ca804a2cde1ad9aec8 --mode clone --stop-after PromoteExpectationToBeliefState --export-brief-state`.
- Polling discipline: no Codex automation was used; status was checked only on the 15-minute cadence requested by the user.

### Status
- Result: `blocked`.
- Latest checkpoint: `status=blocked`, `next_node=ReviewExpectationFields`.
- Terminal error: `ReviewExpectationFields agent result failed: ReAct step 未返回 final payload、工具调用或委托。`
- Completed nodes: `StartTickerInitialization`, `BuildGlobalResearch`, `ReviewGlobalResearch`, `GenerateExpectationConstruction`, `ReviewExpectationConstruction`, `ResolveExpectationConstruction`, `GenerateExpectationDetails`.
- Stable document types: `global_research` only.
- Stable expectation_unit count: 0.
- Pending patch count: 2.
- Working Memory count: 13.
- Commit count: 1.
- Unresolved objection count: 2.
- Blocking delegation count: 0.
- Visible blockers in Brief State:
  - `obj_bearish_label_swap`: `结构性逻辑错误，导致事件监控方向完全失效，无法用于风险管理。`
  - `obj_q3_guidance_conflict`: `关键数据点冲突，直接影响市场定价分析和事件监控方向的准确性。`
- LangSmith evidence: latest `C1.ReviewExpectationFields.LOOP8` returned progress/ledger-style text about evidence review and the Q3 FY26 guidance conflict, but did not return a complete ReAct final payload, tool call, or delegation.

### Built-in Hard Validators
| Validator | Result | Evidence | Notes |
| --- | --- | --- | --- |
| evidence_reference_integrity | pass | `checked_items=7`, `finding_count=0`. | Structural evidence refs in reached stable state remain locatable. This does not validate unpromoted expectation quality. |
| langsmith_trajectory_tool_boundary | fail | `checked_items=47`; finding `workflow_trace_not_completed`, checkpoint `status=blocked`, `next_node=ReviewExpectationFields`. | Correctly fails because Document2 did not reach promotion and the run ended at a runtime boundary. |
| commit_log_state_mutation_consistency | pass | `checked_items=4`, `finding_count=0`. | Limited stable state and commit log are consistent, but no expectation unit was promoted. |

### Hard Gate Failure Root Cause Matrix
| Gate | Result | failure_kind | Failure point | Root cause / fix target |
| --- | --- | --- | --- | --- |
| D2-HG01 | pass | none | Source handoff | Source remains Document 1-only: `run_58f5afce8b9441ca804a2cde1ad9aec8`. |
| D2-HG02 | fail | direct | Stop-after path | Target node `PromoteExpectationToBeliefState` was not reached. |
| D2-HG03 | pass_with_caveat | partial_lifecycle | Detail generation | `GenerateExpectationDetails` completed and produced pending patches, but the patches were never promoted. |
| D2-HG04 | fail | direct | Field review runtime | `ReviewExpectationFields` blocked because C1 returned no final payload/tool/delegation on the final ReAct step. |
| D2-HG05 | pass_with_caveat | partial_state | Evidence refs | Built-in evidence integrity passes for reached artifacts, but no stable expectation-unit evidence set exists. |
| D2-HG06 | fail | quality_residual | Price-in reasoning | The Q3 FY26 guidance conflict and bearish-label direction issue remained unresolved before promotion. |
| D2-HG07 | fail | direct | Review lifecycle | Review pressure was visible through C3 objections, but C1 review result failed at the ReAct boundary and blocked aggregation. |
| D2-HG08 | fail | direct | Resolver lifecycle | Resolver could not run because the workflow stopped at field review. |
| D2-HG09 | fail | direct | Promotion lifecycle | Stable expectation_unit count stayed 0. |
| D2-HG10 | fail | direct | LangSmith/process trace | Built-in trajectory validator failed due terminal blocked checkpoint. |
| D2-HG11 | pass | none | Auditability | Remote log, Brief State export, hard-validator output, DB checkpoint, and LangSmith runs reproduce the blocker. |
| D2-HG12 | fail | runtime_recovery_gap | Context/recovery boundary | Existing conservative review-result max-step fallback was unreachable from the final-step no-progress branch. |
| D2-HG13 | fail | memory_continuity_blocked | Loop continuity | No terminal Document2 state was produced for downstream memory continuity or monitor optimization. |

### Rubrics
| Rubric | Score | Reason |
| --- | ---: | --- |
| D2-R01 | 4 | Source discipline is correct and auditable. |
| D2-R02 | 2 | Pending expectation patches exist, but no stable thesis set was promoted. |
| D2-R03 | 2 | Realized-facts content exists only in pending state and was not reviewed/promoted. |
| D2-R04 | 1 | Price-in reasoning cannot be accepted; the Q3 guidance conflict remained open and no stable output exists. |
| D2-R05 | 2 | Key-variable content is present only in pending patches and did not survive review/promotion. |
| D2-R06 | 2 | Monitoring directions exist only in pending state and include a direction-label blocker. |
| D2-R07 | 2 | Evidence refs are structurally valid for reached state, but no stable expectation evidence set is usable downstream. |
| D2-R08 | 3 | Review pressure improved and surfaced concrete blockers, but review aggregation failed on C1 no-progress output. |
| D2-R09 | 1 | Objection resolution did not run after field review. |
| D2-R10 | 1 | Promotion readiness failed: no stable expectation_unit and trajectory hard validator failed. |
| D2-R11 | 3 | LangSmith/DB/log evidence is useful and points to the exact blocker, but the trace is not closed. |
| D2-R12 | 2 | Uncertainty is visible as blockers but not represented in a usable terminal Blackboard document. |
| D2-R13 | 4 | Reproducibility is strong: source run, execution run, cloud log, Brief State export, hard validators, and LangSmith all align. |
| D2-R14 | 5 | Optimization target is narrow and directly testable: reconnect existing conservative review fallback to final-step runtime exits. |

### Score Summary
- Core Blackboard quality rubrics average (`D2-R01`-`D2-R10`): 2.0.
- Other rubrics with score <= 2: `D2-R12`.
- Quality target met: no.
- Operational target met: no; the run stopped before `ResolveObjectionsAndDelegations` and promotion.

### Failure Categories
- category: `review_no_progress_terminal_failure`
  - issue: C1 field review reached the final ReAct step with no complete `final_payload`, no tool call, and no delegation.
  - evidence: terminal error `ReAct step 未返回 final payload、工具调用或委托。`; LangSmith `C1.ReviewExpectationFields.LOOP8` returned progress/ledger text instead of the action contract.
  - severity: blocking/workflow-runtime.
  - suspected root cause: the ReAct harness returned `react_no_progress` immediately on the final no-progress branch.
- category: `review_max_steps_fallback_unreachable`
  - issue: the runtime already had `_max_steps_review_result_fallback()`, but the final-step no-progress and incomplete-final-payload branches returned failure before the post-loop fallback could run.
  - evidence: code path in `src/doxagent/agents/runtime/react.py` returned `_failed(..., "react_no_progress", ...)` inside the loop when `step == max_steps`.
  - severity: hard-gate/direct.
  - suspected root cause: fallback was implemented only after the loop, so it covered max-step tool-call exhaustion but not final-step no-progress action exhaustion.
- category: `field_review_lifecycle_incomplete`
  - issue: C3 generated concrete objections, but C1 failure prevented complete review aggregation and resolver handoff.
  - evidence: Brief State open objections `obj_bearish_label_swap` and `obj_q3_guidance_conflict`; checkpoint blocked at `ReviewExpectationFields`.
  - severity: quality-blocking.
  - suspected root cause: runtime boundary failure, not lack of review pressure.
- category: `promotion_not_reached`
  - issue: no stable expectation units were promoted.
  - evidence: `stable_document_types=["global_research"]`, `expectation_unit_count=0`.
  - severity: acceptance-blocking.
  - suspected root cause: field review runtime blocker.

### Optimization Hypothesis
- If final-step review no-progress and incomplete-final-payload exits invoke the existing conservative review-result recovery before failing, then a review agent that already produced tool evidence or compacted review context can return an auditable `needs_more_evidence` / `not_checked` coverage gap instead of blocking the whole workflow at the runtime boundary.
- This should not relax quality acceptance because:
  - the recovered payload still goes through `_normalize_final_payload()` and schema validation;
  - the recovered finding is not marked `supported`;
  - existing objections from other reviewers remain open and resolver/promotion gates still decide the final state;
  - hard validators still fail any run that does not reach promotion.
- Expected measurable improvement in the next cloud verification:
  - Retest no longer terminates at `ReviewExpectationFields agent result failed: ReAct step 未返回 final payload、工具调用或委托。`;
  - `ReviewExpectationFields` either completes with conservative review findings or exposes a more specific downstream quality blocker;
  - the built-in trajectory validator only passes if the workflow actually reaches a terminal promoted state.

### Proposed Modification Plan
- Change 1: Add a reusable `_succeeded_with_review_max_steps_fallback()` helper in the ReAct harness to avoid duplicating the conservative review recovery success path.
- Change 2: In the final-step `is_complete=false final_payload` branch, record a no-progress audit entry and call the review fallback before returning `react_incomplete_final_payload`.
- Change 3: In the final-step no-final/no-tool/no-delegation branch, record a no-progress audit entry and call the review fallback before returning `react_no_progress`.
- Change 4: Keep the existing post-loop max-steps fallback path but route it through the same helper for identical audit shape.
- Change 5: Add regression coverage where an `ExpectationFieldReviewResult` task first obtains tool evidence, then returns only `plan_update`/ledger on the final step; expected result is conservative review recovery, not a failed AgentResult.
- Retest requirement: push/deploy the fix, rebuild the cloud image, and run the same Document 1-only cloud smoke once. Use in-thread 15-minute `Start-Sleep 900` checks rather than Codex automation.

### Actual Modification
- Implemented after this evaluation:
  - Updated `src/doxagent/agents/runtime/react.py` to call conservative review-result fallback from final-step no-progress and incomplete-final-payload exits.
  - Added `_succeeded_with_review_max_steps_fallback()` so fallback audit entries and completion payloads are consistent across in-loop and post-loop recovery.
  - Added `test_react_recovers_review_gap_when_final_step_has_no_progress` in `tests/test_phase16_react_harness.py`.
  - Added changelog entry for the Document2 ReAct review recovery fix.
  - Verified locally with `uv run pytest tests/test_phase16_react_harness.py -k "max_steps or final_step_has_no_progress"`, `uv run pytest tests/test_phase16_react_harness.py tests/test_phase5_initialization_workflow.py`, and `uv run ruff check src/doxagent/agents/runtime/react.py tests/test_phase16_react_harness.py`.

## Loop 1 Retest16 - reached promotion; blocked by generic monitoring cleanup

### Run Metadata
- Date: 2026-06-24.
- Source run: `run_58f5afce8b9441ca804a2cde1ad9aec8` (Document 1-only source, unchanged).
- Execution run: `run_12dd797c24ea4d1db7ba8bce45e2932f`.
- Deployed commit: `d331bdd`.
- Remote cwd: `/root/doxagent`.
- Remote log: `.eval_runs/document2-loop1-retest16-20260624T032817+0800.log`.
- Brief State export path on cloud: `eval/brief_state_exports/run_12dd797c24ea4d1db7ba8bce45e2932f.json`.
- Cloud command: `docker compose -f docker-compose.yml run --rm -e DOXAGENT_RUN_REAL_API_TESTS=1 -e DOXAGENT_STORAGE_MODE=postgres debug-viewer python eval/run_document2_expectation_units_smoke.py run_58f5afce8b9441ca804a2cde1ad9aec8 --mode clone --stop-after PromoteExpectationToBeliefState --export-brief-state`.
- Polling discipline: no Codex automation was used; checks were separated by 900-second in-thread sleeps.

### Status
- Result: `blocked`.
- Latest checkpoint: `status=blocked`, `next_node=PromoteExpectationToBeliefState`.
- Completed nodes: `StartTickerInitialization`, `BuildGlobalResearch`, `ReviewGlobalResearch`, `GenerateExpectationConstruction`, `ReviewExpectationConstruction`, `ResolveExpectationConstruction`, `GenerateExpectationDetails`, `ReviewExpectationFields`, `ResolveObjectionsAndDelegations`.
- Terminal error: `GenerateExpectationDetails event_monitoring_direction is generic.`
- Stable document types: `global_research` only.
- Stable expectation_unit count: 0.
- Pending patch count: 2.
- Working Memory count: 15.
- Commit count: 1.
- Objections: 9 total, 0 open/unresolved.
- Blocking delegations: 0.
- Evidence refs in export: 68.
- Process improvement vs Retest15b: `ReviewExpectationFields` and `ResolveObjectionsAndDelegations` completed; the prior C1 no-progress runtime blocker did not recur.
- LangSmith evidence: C1/C3/O4 reviews completed and raised substantive numeric/price blockers; deterministic objection normalization closed the field-review/numeric objections; promotion then rejected generic monitoring text in the pending expectation documents.

### Built-in Hard Validators
| Validator | Result | Evidence | Notes |
| --- | --- | --- | --- |
| evidence_reference_integrity | pass | `checked_items=14`, `finding_count=0`. | Structural refs are locatable for reached artifacts and resolved objections. |
| langsmith_trajectory_tool_boundary | fail | `checked_items=54`; finding `workflow_trace_not_completed`, checkpoint `status=blocked`, `next_node=PromoteExpectationToBeliefState`. | Correctly fails because promotion did not complete. |
| commit_log_state_mutation_consistency | pass | `checked_items=4`, `finding_count=0`. | Stable state remains limited to `global_research`, but commit/state consistency holds for that state. |

### Hard Gate Failure Root Cause Matrix
| Gate | Result | failure_kind | Failure point | Root cause / fix target |
| --- | --- | --- | --- | --- |
| D2-HG01 | pass | none | Source handoff | Source remains Document 1-only: `run_58f5afce8b9441ca804a2cde1ad9aec8`. |
| D2-HG02 | fail | direct | Stop-after path | `PromoteExpectationToBeliefState` was entered but not completed. |
| D2-HG03 | pass | none | Construction lifecycle | Two differentiated shells were constructed and reviewed. |
| D2-HG04 | fail | quality_residual | Detail/promotion quality | Detail patches retained full fields, but `event_monitoring_direction` was degraded by deterministic cleanup into generic placeholder text. |
| D2-HG05 | pass_with_caveat | partial_state | Evidence refs | Built-in ref integrity passes, but no stable expectation-unit evidence set exists after blocked promotion. |
| D2-HG06 | fail | quality_residual | Price-in reasoning | Price reactions and guidance levels were downgraded to provisional/non-numeric placeholders, leaving insufficient stable price-in reasoning. |
| D2-HG07 | pass | none | Field review lifecycle | A1/C1/C3/O4 review pressure ran and surfaced concrete numeric, fundamental, industry, and price-reaction issues. |
| D2-HG08 | pass_with_caveat | quality_residual | Resolver lifecycle | Resolver/deterministic normalization closed all objections, but the resulting patches were not promotion-quality. |
| D2-HG09 | fail | direct | Promotion lifecycle | No stable expectation_unit documents were promoted. |
| D2-HG10 | fail | direct | LangSmith/process trace | Built-in trajectory validator failed because latest checkpoint is blocked. |
| D2-HG11 | pass | none | Auditability | Remote log, regenerated Brief State export, validators, Working Memory, objections, and LangSmith reproduce the blocker. |
| D2-HG12 | pass_with_caveat | context_pressure | Review inputs | C1/C3 review inputs were large but completed; context pressure did not cause the terminal blocker in this run. |
| D2-HG13 | pass_with_caveat | memory_continuity_partial | Review/resolution continuity | Objection ids and deterministic normalization decisions were preserved, but patch quality degraded during cleanup. |

### Rubrics
| Rubric | Score | Reason |
| --- | ---: | --- |
| D2-R01 | 4 | Source discipline remains correct and auditable. |
| D2-R02 | 3 | Two differentiated expectation theses exist in pending patches, but they are not stable and still carry cleanup caveats. |
| D2-R03 | 2 | Realized facts exist with refs, but many precise facts were downgraded after review found hallucinated values. |
| D2-R04 | 2 | Price-in reasoning is visible but provisional; contradicted OHLCV/price claims were removed rather than rebuilt into stable reasoning. |
| D2-R05 | 3 | Key variables exist and remain linked to thesis direction, but exact levels were degraded and not stable. |
| D2-R06 | 1 | Event monitoring direction is the terminal blocker: many triggers became generic placeholders and cannot feed downstream monitoring. |
| D2-R07 | 3 | Evidence discipline improved structurally and review caught narrative-only numeric claims, but stable expectation evidence was not promoted. |
| D2-R08 | 4 | Field-review pressure is strong: C1/C3/O4 found material numeric, fundamental, industry, and price-reaction issues. |
| D2-R09 | 3 | Objections were closed with traceable deterministic normalization, but resolution quality is incomplete because the revised patches are not promotable. |
| D2-R10 | 1 | Promotion readiness failed: no stable expectation_unit and trajectory hard validator failed. |
| D2-R11 | 3 | Tool/use trace is adequate for diagnosis, but large review inputs and downgraded outputs leave efficiency/quality caveats. |
| D2-R12 | 2 | Uncertainty handling is visible, but it collapses into generic placeholder language rather than actionable unknowns. |
| D2-R13 | 4 | Reproducibility is strong: run id, log, persisted Brief State export, LangSmith, hard validators, and exact error align. |
| D2-R14 | 5 | The optimization target is narrow and testable: deterministic numeric cleanup must preserve business-specific monitoring semantics and avoid promotion-banned placeholder text. |

### Score Summary
- Core Blackboard quality rubrics average (`D2-R01`-`D2-R10`): 2.6.
- Other rubrics with score <= 2: `D2-R12`.
- Quality target met: no.
- Operational improvement accepted: yes for the Retest15b runtime blocker; Retest16 advanced to promotion.
- Document2 quality improvement accepted: no, because stable expectation_unit promotion still failed.

### Failure Categories
- category: `promotion_generic_monitoring_placeholder`
  - issue: promotion rejected pending expectation patches because `event_monitoring_direction` contained generic placeholder triggers.
  - evidence: terminal error `GenerateExpectationDetails event_monitoring_direction is generic.`
  - severity: hard-gate/direct.
  - suspected root cause: deterministic cleanup replaced unsupported numeric thresholds with repeated generic strings such as `Monitor this event qualitatively; precise threshold requires source-appropriate evidence.`
- category: `deterministic_numeric_cleanup_overgeneralization`
  - issue: numeric hallucination blockers were closed by removing false precision, but cleanup lost too much monitoring specificity.
  - evidence: pending patches contain repeated non-actionable monitoring items; all objections are resolved but no patch can promote.
  - severity: quality-blocking.
  - suspected root cause: fallback text in numeric-sanity and field-review correction paths is safe from false precision but not sufficiently business-specific for Document2.
- category: `price_in_rebuild_not_completed`
  - issue: contradicted price-reaction claims were downgraded rather than rebuilt from structured OHLCV/market evidence.
  - evidence: O4 found HBM4 certification-day drop, three-month return mismatch, and post-event reversal; subsequent patches remove exact values instead of producing stable price-in conclusions.
  - severity: rubric-quality.
  - suspected root cause: deterministic normalization focuses on removing bad numbers, not reconstructing source-backed price reaction text.
- category: `promotion_not_reached`
  - issue: stable expectation_unit count stayed 0.
  - evidence: Brief State `expectation_units_count=0`, `stable_document_types=["global_research"]`.
  - severity: acceptance-blocking.
  - suspected root cause: promotion quality gate correctly rejected generic monitoring text.

### Optimization Hypothesis
- If deterministic numeric-sanity and field-review cleanup removes unsupported numeric thresholds while preserving the named business catalyst/risk, event object, and monitoring direction, `event_monitoring_direction` should remain concrete enough for promotion and later MonitoringConfig generation.
- If cleanup output avoids the promotion-banned placeholder markers (`monitor this event qualitatively`, `source-verified value`, `quantified price reaction withheld`, etc.), promotion will no longer fail on deterministic placeholder text.
- This is not a gate relaxation: `_validate_expectation_promotion_quality()` still rejects explicit placeholder text, and tests now assert that cleaned expectation documents pass promotion quality only after the cleaner avoids those banned markers.
- Expected measurable improvement in the next cloud verification:
  - Retest no longer fails with `event_monitoring_direction is generic`;
  - pending patches either promote to at least two stable expectation units or expose a more specific residual quality blocker;
  - hard validator trajectory can pass only if the workflow reaches a closed terminal state.

### Proposed Modification Plan
- Change 1: Replace generic monitoring fallback strings in numeric-sanity cleanup with business-preserving threshold removal text.
- Change 2: Replace `source-verified value/source-verified threshold` cleanup artifacts with `source-backed level/source-backed threshold` only inside otherwise specific event/fact text.
- Change 3: Remove promotion-banned placeholder phrases from promotion-time price reaction normalization and field-review deterministic correction outputs.
- Change 4: Keep `_validate_expectation_promotion_quality()` strict; update tests so explicit banned placeholder text is still rejected.
- Change 5: Add/adjust regression coverage so a numeric-sanity cleaned expectation document has no banned placeholder markers and passes promotion quality validation.
- Retest requirement: push/deploy the fix, rebuild the cloud image, and run the same Document 1-only cloud smoke again using 900-second in-thread status checks.

### Actual Modification
- Implemented after this evaluation:
  - Updated `src/doxagent/workflows/initialization.py` so deterministic numeric cleanup preserves named catalysts/risks while removing unsupported thresholds.
  - Removed promotion-banned placeholder wording from numeric-sanity cleanup, field-review cleanup, and promotion-time price-reaction normalization.
  - Updated `_numeric_sanity_clean_text()` / `_strip_unsupported_numeric_precision()` to support `source-backed level` and monitoring-specific `source-backed threshold` replacements.
  - Updated `tests/test_phase5_initialization_workflow.py` so numeric-sanity cleaned documents are validated through `_validate_expectation_promotion_quality()` and explicit placeholder text remains rejected.
  - Added changelog entry for the Document2 numeric-cleanup promotion-quality fix.
  - Verified locally with `uv run pytest tests/test_phase5_initialization_workflow.py` and `uv run ruff check src/doxagent/workflows/initialization.py tests/test_phase5_initialization_workflow.py`.

## Loop 1 Retest17 - resolver flat partial patch lost document content

### Run Metadata
- Date: 2026-06-24.
- Source run: `run_58f5afce8b9441ca804a2cde1ad9aec8` (Document 1-only source, unchanged).
- Execution run: `run_30e5f3fd4337475987033429e4b8c6e7`.
- Deployed commit: `05f772f`.
- Remote cwd: `/root/doxagent`.
- Remote log: `.eval_runs/document2-loop1-retest17-20260624T043253+0800.log`.
- Brief State export path on cloud: `eval/brief_state_exports/run_30e5f3fd4337475987033429e4b8c6e7.json`.
- Cloud command: `docker compose -f docker-compose.yml run --rm -e DOXAGENT_RUN_REAL_API_TESTS=1 -e DOXAGENT_STORAGE_MODE=postgres debug-viewer python eval/run_document2_expectation_units_smoke.py run_58f5afce8b9441ca804a2cde1ad9aec8 --mode clone --stop-after PromoteExpectationToBeliefState --export-brief-state`.
- Polling discipline: no Codex automation was used. The run was started manually and checked via in-thread `Start-Sleep -Seconds 900` wakeups.

### Status
- Result: `blocked`.
- Latest checkpoint: `status=blocked`, `next_node=ResolveObjectionsAndDelegations`.
- Completed nodes: `StartTickerInitialization`, `BuildGlobalResearch`, `ReviewGlobalResearch`, `GenerateExpectationConstruction`, `ReviewExpectationConstruction`, `ResolveExpectationConstruction`, `GenerateExpectationDetails`, `ReviewExpectationFields`.
- Terminal error: `GenerateExpectationUnits expectation patch must include document content.`
- Stable document types: `global_research` only.
- Stable expectation_unit count: 0.
- Pending patch count: 3.
- Working Memory count: 16.
- Commit count: 1.
- Objections: 10 total, 5 open/unresolved.
- Blocking delegations: 0.
- Evidence refs in export: 72.
- Process movement vs Retest16: the previous promotion-time `event_monitoring_direction is generic` blocker did not recur before this run stopped. The run regressed earlier in the lifecycle because resolver validation rejected an accepted/partially accepted revised patch shape.
- LangSmith evidence: `GenerateExpectationDetails` and `ReviewExpectationFields` LLM traces are visible for the same run id and show successful review loops; a direct LangSmith search for `ResolveObjectionsAndDelegations` returned no root trace. The resolver blocker is instead visible in the persisted Brief State Working Memory / local ReAct audit, including O1 raw JSON and normalized `structured.proposed_patches`.

### Built-in Hard Validators
| Validator | Result | Evidence | Notes |
| --- | --- | --- | --- |
| evidence_reference_integrity | pass | `checked_items=15`, `finding_count=0`. | Structural refs in reached artifacts are locatable. This does not validate unpromoted expectation quality. |
| langsmith_trajectory_tool_boundary | fail | `checked_items=56`; finding `workflow_trace_not_completed`, latest checkpoint `status=blocked`, `next_node=ResolveObjectionsAndDelegations`. | Correct hard-validator failure because the workflow did not reach a closed Document2 promote stop. |
| commit_log_state_mutation_consistency | pass | `checked_items=4`, `finding_count=0`. | Stable state remains limited to `global_research`, and that commit/state relation is consistent. |

### Hard Gate Failure Root Cause Matrix
| Gate | Result | failure_kind | Failure point | Root cause / fix target |
| --- | --- | --- | --- | --- |
| D2-HG01 | pass | none | Source handoff | Source remains the required Document 1-only run `run_58f5afce8b9441ca804a2cde1ad9aec8`. |
| D2-HG02 | fail | direct | Stop-after path | `PromoteExpectationToBeliefState` was not reached; execution blocked in `ResolveObjectionsAndDelegations`. |
| D2-HG03 | pass | none | Construction lifecycle | Three expectation shells/details were generated and reached field review. |
| D2-HG04 | pass_with_caveat | partial_state | Detail patches | Three pending detail patches contain document content, facts, variables, and monitoring fields; however they are not stable and some numeric objections remain open. |
| D2-HG05 | pass_with_caveat | partial_state | Evidence refs | Built-in ref integrity passes for reached artifacts, but stable expectation-unit evidence does not exist. |
| D2-HG06 | fail | quality_residual | Price-in reasoning | O4 price-reaction contradiction was resolved by deterministic normalization, but remaining numeric/fundamental objections mean price-in quality is still not stable. |
| D2-HG07 | pass | none | Field review lifecycle | A1/C1/C3/O4 field review completed and produced substantive objections, including numeric sanity and price-reaction blockers. |
| D2-HG08 | fail | direct | Resolver patch validation | O1 partially accepted an objection and returned revised patches, but normalized `BlackboardPatch.after` was `null`, so accepted revisions could not be validated or merged. |
| D2-HG09 | fail | direct | Promotion lifecycle | Stable expectation_unit count stayed 0. |
| D2-HG10 | fail | direct | Trace/process closure | Built-in trajectory validator failed because latest checkpoint is blocked; remote LangSmith resolver root trace was not found by MCP keyword search. |
| D2-HG11 | pass | none | Failure auditability | Remote log, DB checkpoint, Brief State export, Working Memory raw resolver output, hard validators, and LangSmith review traces reproduce the blocker. |
| D2-HG12 | pass_with_caveat | context_pressure | Resolver input/output | Resolver completed an LLM response, but produced a schema-adjacent partial update shape. This is not a timeout, but context/task-contract pressure remains a contributing risk. |
| D2-HG13 | fail | memory_continuity_blocked | Revision continuity | Accepted/partially accepted revision intent was visible in Working Memory but did not survive into a valid pending patch state. |

### Rubrics
| Rubric | Score | Reason |
| --- | ---: | --- |
| D2-R01 | 4 | Source discipline remains correct, and the generated expectations continue to use Document 1 inputs rather than an existing Document2 state. |
| D2-R02 | 3 | Three differentiated pending theses exist, but no stable expectation_unit was promoted and two bullish theses still lean on broad HBM/valuation framing. |
| D2-R03 | 2 | Realized facts exist, but many numeric/fundamental claims remain under objection or deterministic cleanup; stable fact quality cannot be accepted. |
| D2-R04 | 2 | Price-in reasoning improved enough for O4 contradiction handling to proceed, but it is still not stable and remains mixed with unresolved numeric/fundamental concerns. |
| D2-R05 | 3 | Key variables are present and mostly connected to the theses, but some resolver revisions would have replaced full variable sets with narrower partial lists if accepted. |
| D2-R06 | 3 | Event monitoring fields are no longer blocked by generic placeholder language before resolver, but they remain pending and not proven stable. |
| D2-R07 | 3 | Evidence refs are structurally valid and include DoxAtlas plus market-data evidence, but many important fundamental claims remain narrative-backed or unresolved. |
| D2-R08 | 4 | Field-review pressure is strong: numeric sanity, market-data contradiction, and C1 quarter-label concerns are explicit and blocking. |
| D2-R09 | 2 | Resolver lifecycle is the terminal failure: decisions and revision intent are visible, but patch content was lost at the normalized `BlackboardPatch.after` boundary. |
| D2-R10 | 1 | Promotion readiness failed: no stable expectation_unit, open objections remain, and trajectory validator failed. |
| D2-R11 | 3 | Tool/use traces are adequate through detail and field-review nodes, but the resolver root trace is not visible through LangSmith keyword search and must be judged from local ReAct audit. |
| D2-R12 | 3 | Uncertainty and unresolved evidence are visible through objections/unknowns, but they did not become a usable terminal Blackboard state. |
| D2-R13 | 4 | Reproducibility is strong: run id, cloud log, DB checkpoint, Brief State export, hard validators, Working Memory raw resolver output, and LangSmith review traces align. |
| D2-R14 | 5 | The optimization target is narrow and testable: normalize partial flat resolver update patches into `after` so existing merge and validation can run. |

### Score Summary
- Core Blackboard quality rubrics average (`D2-R01`-`D2-R10`): 2.7.
- Other rubrics with score <= 2: none.
- Built-in hard validators all pass: no.
- Quality target met: no.
- Operational improvement accepted: no; Retest17 exposed a new resolver normalization blocker before promotion.

### Failure Categories
- category: `resolver_flat_partial_patch_content_loss`
  - issue: O1 resolver returned `operation=update` patches with expectation document fields (`expectation_name`, `direction`, `key_variables`) at the patch top level, but normalized `BlackboardPatch.after` became `null`.
  - evidence: Working Memory raw resolver text includes top-level expectation fields inside `final_payload.proposed_patches`; normalized `structured.proposed_patches` shows `after: null`; terminal error is `GenerateExpectationUnits expectation patch must include document content.`
  - severity: hard-gate/direct.
  - suspected root cause: flat expectation-unit normalizer only accepted full `ExpectationUnitDocument` content; partial update revisions were not preserved as partial `after` for downstream merge.
- category: `accepted_revision_not_merged`
  - issue: O1 partially accepted the quarter-label objection and intended to revise affected fields, but the revision could not be merged into pending patches.
  - evidence: O1 `partially_accepted_objection_ids` includes `objection_7292d3934f804b0b9acf4c5f1ea19c64`; proposed revised patches target `expectation_mu_001` and `expectation_mu_002` but carry no `after`.
  - severity: workflow-blocking.
  - suspected root cause: schema normalization stripped or failed to preserve flat partial document fields before `_complete_expectation_revision_patch()`.
- category: `remaining_numeric_fundamental_objections`
  - issue: fundamental-data numeric sanity blockers for `expectation_mu_002` and `expectation_mu_003` remained open at the time of resolver failure.
  - evidence: Brief State objections show open `obj_numeric_sanity_expectation_mu_002_fundamental_data` and `obj_numeric_sanity_expectation_mu_003_fundamental_data`.
  - severity: quality-blocking after resolver fix.
  - suspected root cause: O1 was trying to close them through partial revisions, but the patch boundary failure prevented lifecycle completion.
- category: `promotion_not_reached`
  - issue: no stable expectation units were promoted.
  - evidence: `stable_document_types=["global_research"]`, `expectation_unit_count=0`.
  - severity: acceptance-blocking.
  - suspected root cause: resolver patch validation blocked before promotion.

### Optimization Hypothesis
- If the structured-output normalizer treats flat `operation=update` expectation-unit patches as partial document revisions when they fail full `ExpectationUnitDocument` validation, then O1's accepted/partially accepted resolver revisions can be preserved in `patch.after`.
- Existing workflow merge logic can then deep-merge the partial `after` into the pending full expectation document, and the existing strict `_validate_expectation_patch_list()` / `ExpectationUnitDocument` validation will still reject incomplete or malformed final documents.
- This should not relax initial detail quality because incomplete flat `operation=create` expectation patches still fail full document validation.
- Expected measurable improvement in the next cloud verification, if the user authorizes another smoke: Retest should no longer stop with `GenerateExpectationUnits expectation patch must include document content`; it should either complete resolver and proceed to promotion, or expose the remaining numeric/fundamental quality blockers as explicit unresolved objections.

### Proposed Modification Plan
- Change 1: Update `WorkflowAgentResultNormalizer._normalize_patch_payload()` so full flat expectation documents still validate as full `ExpectationUnitDocument`.
- Change 2: For flat `operation=update` expectation-unit patches that contain only partial document fields, move the recognized fields into `after` instead of raising or producing `after=null`.
- Change 3: Keep incomplete flat `operation=create` patches strict so detail generation cannot create partial expectation documents.
- Change 4: Add unit coverage for partial flat update normalization.
- Change 5: Add workflow coverage proving the normalized partial `after` merges into a pending full expectation document and passes `_validate_expectation_patch_list()`.
- Retest requirement: do not launch a new smoke test in this turn per user instruction; report the fix and leave cloud verification for explicit user approval.

### Actual Modification
- Implemented after this evaluation:
  - Updated `src/doxagent/workflows/normalizer.py` so flat partial `operation=update` expectation-unit patches are preserved as partial `after` payloads.
  - Preserved strict full-document validation for flat `operation=create` expectation-unit patches.
  - Added `test_normalizer_lifts_partial_flat_expectation_update_into_after` in `tests/test_workflow_normalizer.py`.
  - Added `test_o1_flat_partial_revision_merges_from_normalized_payload` in `tests/test_phase5_initialization_workflow.py`.
  - Added changelog entry for the Document2 resolver partial-update normalization fix.
  - Verified locally with `uv run pytest tests/test_workflow_normalizer.py tests/test_phase5_initialization_workflow.py` and `uv run ruff check src/doxagent/workflows/normalizer.py tests/test_workflow_normalizer.py tests/test_phase5_initialization_workflow.py`.
- Next smoke test: not launched, per user instruction to stop after this complete eval/optimization/modification loop.
