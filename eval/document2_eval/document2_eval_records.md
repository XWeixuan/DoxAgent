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
