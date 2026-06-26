# Step9 Test Baseline Known Failures

本文冻结 Step8 后、Step9 验收补口后的 full pytest 基线。

## 执行记录

第一次执行：

```powershell
uv run pytest -q
```

结果出现 15 个 `tmp_path` setup errors，原因是本地 `.tmp-uv/tmp/pytest-of-WEIXUANXIE` 临时目录权限异常。该结果不作为有效测试基线。

第二次执行使用新的 workspace 内临时目录，但测试命令仍为：

```powershell
uv run pytest -q
```

有效结果：

```text
428 passed, 23 skipped, 25 failed, 8 warnings
```

Step9 targeted suite：

```powershell
uv run pytest -p no:cacheprovider tests/test_initialization_characterization.py tests/test_document2_canonical_contracts.py tests/test_phase5_initialization_workflow.py tests/test_workflow_normalizer.py -q
```

结果：

```text
59 passed, 3 warnings
```

## 分类定义

| 类别 | 处理方式 |
| --- | --- |
| A | Step8/Step9 引入或影响 Document1/2 当前主路径的回归，必须本轮修。 |
| B | 既有基线、Document3、Phase 文案、dev_plan 旧文件、旧 schema/legacy patch 测试，本轮只记录不修。 |
| C | 可能影响 Document1ContextPack、market evidence、ReAct runtime 兼容或下一轮 smoke 的信号；先判断是否阻塞。 |

## 当前结论

| 类别 | 数量 | Step9 处置 |
| --- | ---: | --- |
| A | 0 | 无需修 runtime。Step9 targeted suite 已通过。 |
| B | 19 | 记录为 known baseline failure，不在本轮修复。 |
| C | 6 | 当前不阻塞下一轮 Document2 eval/smoke；需要在对应专项迁移中处理。 |

## 25 个 full pytest failures 分类表

| # | Test | 失败摘要 | 分类 | Step9 判断 |
| ---: | --- | --- | --- | --- |
| 1 | `tests/test_baseline.py::test_phase0_baseline_files_exist` | 缺少 `dev_plan/PHASE0_BASELINE.md`。 | B | dev_plan 历史文件缺口；不属于 Step9 Document1/2 workflow runtime。 |
| 2 | `tests/test_phase13_real_workflow.py::test_expectation_patch_count_requires_one_to_three` | 旧测试仍调用 `ExpectationDetailResult`/patch validation，当前 detail 主路径已切到 candidate。 | B | 旧 patch contract 测试；不恢复 old detail patch path。 |
| 3 | `tests/test_phase13_real_workflow.py::test_expectation_detail_quality_rejects_empty_realized_facts` | 旧 fixture 期望 detail result 里有 `proposed_patches`，当前 mock detail 已是 candidate。 | B | 旧 schema fixture；不回滚 Step4。 |
| 4 | `tests/test_phase13_real_workflow.py::test_agent_runner_workflow_completes_with_structured_agent_result_json` | workflow 继续到 Document3 monitoring registry，缺 `monitoring.update_ticker_config`。 | B | Document3/monitoring registry 基线；不在 Step9 修。 |
| 5 | `tests/test_phase13_real_workflow.py::test_global_narrative_tool_call_fragment_is_replaced_with_chinese_fallback` | GlobalNarrativeReport 逻辑已执行，但后续 Document3 monitoring registry blocker 使 full workflow blocked。 | C | GNR targeted guard 已覆盖；不阻塞下一轮 Document2 eval/smoke。 |
| 6 | `tests/test_phase13_real_workflow.py::test_agent_runner_resume_latest_after_manual_blocker_resolution` | 手动解 blocker 后继续到 Document3 monitoring registry blocker。 | B | Document3 continuation baseline；不修。 |
| 7 | `tests/test_phase13_real_workflow.py::test_agent_runner_partial_retry_does_not_duplicate_completed_global_commit` | BuildGlobalResearch resume 后继续到 Document3 monitoring registry blocker。 | C | Document1 commit/idempotency targeted guards 通过；当前不阻塞。 |
| 8 | `tests/test_phase14_global_research_integration.py::test_global_research_resume_reuses_completed_agent_sections_after_failure` | 旧断言期望首次 retryable failure 后 `BLOCKED`，当前为 retry 后 `RUNNING`。 | C | 既有 Document1 recovery 行为漂移信号；不由 Step9 引入，不阻塞。 |
| 9 | `tests/test_phase14_global_research_integration.py::test_o2_monitoring_tasks_use_node_specific_write_targets` | 继续到 Document3 monitoring registry blocker。 | B | Document3/monitoring tool registry；不修。 |
| 10 | `tests/test_phase14_global_research_integration.py::test_global_research_inputs_round_trip_for_resume` | resume 后继续到 Document3 monitoring registry blocker。 | C | Document1 input round-trip targeted guard 已覆盖；当前不阻塞。 |
| 11 | `tests/test_phase15_o1_a1_a2_realization.py::test_workflow_uses_a2_retrieval_to_complete_delegation_and_o1_resolves_objection` | 旧 runner 仍输出 `ExpectationDetailResult` patch payload，当前 workflow 要求 `ExpectationDetailCandidateResult`。 | B | Legacy phase15 fixture；不恢复 old detail patch path。 |
| 12 | `tests/test_phase15_o1_a1_a2_realization.py::test_o1_revision_delegation_completes_after_o1_resolves_review_objection` | 同上，旧 detail fixture 与 candidate schema 不一致。 | B | Legacy phase15 fixture；不修。 |
| 13 | `tests/test_phase15_o1_a1_a2_realization.py::test_construction_objection_resolution_revises_shells_without_pending_patches` | construction transaction 已通过，但测试继续到 detail，旧 runner 输出 old patch schema 导致 blocked。 | B | Step9 construction transaction 已由 characterization tests 覆盖；此旧测试不修。 |
| 14 | `tests/test_phase15_o1_a1_a2_realization.py::test_objection_resolution_batches_large_unresolved_sets` | 期望旧 resolver batching result count，当前 resolver/transaction 行为已变化。 | B | Document2 legacy resolver batching old expectation；不修。 |
| 15 | `tests/test_phase15_o1_a1_a2_realization.py::test_workflow_prefetches_missing_o1_detail_narrative_tool_evidence` | 旧 detail schema / ReAct compatibility 路径与 candidate schema 不一致。 | C | ReAct/detail compatibility 信号；targeted candidate path 通过，不阻塞，但后续 ReAct schema migration 需处理。 |
| 16 | `tests/test_phase15_o1_a1_a2_realization.py::test_workflow_converts_direct_document_outputs_to_blackboard_patches` | 旧 direct document-to-patch path 期望，与 canonical candidate/revision path 冲突。 | B | Legacy direct patch conversion；不修。 |
| 17 | `tests/test_phase15_o1_a1_a2_realization.py::test_a1_workflow_nodes_receive_minimal_doxatlas_tool_sets` | 旧 A1 tool contract 断言与当前 role-scoped review context 不一致。 | B | Tool contract old assertion；不在 Step9 修。 |
| 18 | `tests/test_phase15_o1_a1_a2_realization.py::test_reviewer_nodes_receive_role_specific_tool_sets` | 旧 reviewer tool-set 断言与当前 compact review context 不一致。 | B | Role tool-set old assertion；不修。 |
| 19 | `tests/test_phase15_o1_a1_a2_realization.py::test_workflow_blocks_when_a2_search_retrieval_has_no_sufficient_evidence` | 旧 flow 在到达 A2 branch 前已被 old detail schema 阻断。 | B | Legacy fixture ordering；不修。 |
| 20 | `tests/test_phase15_o1_a1_a2_realization.py::test_c1_reviewer_objection_blocks_expectation_promotion` | 旧 flow 在 detail schema 阶段 blocked，未到 reviewer objection assertion。 | B | Legacy phase15 fixture；不修。 |
| 21 | `tests/test_phase15_o1_a1_a2_realization.py::test_o1_accepting_objection_requires_revised_expectation_patch` | 旧测试期望 accepted objection 必须返回 patch；当前 resolver 要 `Document2ResolutionPlan`/candidate revision。 | B | Old patch semantics；不修。 |
| 22 | `tests/test_phase15_o1_a1_a2_realization.py::test_o1_revised_patch_replaces_pending_expectation_patch` | 旧 patch replacement 期望，当前 canonical revision + projection path 已不同。 | B | Old pending patch replacement test；不修。 |
| 23 | `tests/test_phase20_initialization_quality_hardening.py::test_monitoring_policy_normalizer_builds_document3_action_payloads` | Document3 policy normalizer 调用缺失 `_payload_string_list` helper。 | B | Document3 baseline failure；Step9 不顺手重构 Document3。 |
| 24 | `tests/test_phase6_e2e_sample.py::test_phase6_sample_documents_include_monitoring_outputs_without_trading_execution` | sample 文案缺少“不触发券商下单”。 | B | Phase6 文案/sample baseline；不修。 |
| 25 | `tests/test_phase8_o4_market_trace.py::test_market_trace_agent_registry_exposes_market_trace_schema` | 旧断言期望 O4 output schema 仅 `ResearchSection`，当前为 `ResearchSection|MonitoringPolicyDocument`。 | C | O4/market trace registry 信号；不阻塞 Document2 eval/smoke，后续 registry contract cleanup 处理。 |

## Step9 A 类判断

本轮新增/更新的 Step9 护栏均通过：

- construction transaction success / empty revision rejection / identity drift rejection。
- placeholder typed finding producer。
- placeholder finding 不创建 legacy objection。
- promotion consumption of blocking finding。
- normalizer 不恢复 flat expectation_unit guessing。

因此当前 25 个 full pytest failures 中没有 Step9 必须修复的 A 类回归。

## 下一轮前置判断

可以进入下一轮真实 Document2 eval/smoke 前准备，但应带着以下已知前提：

- full pytest 仍不是绿色基线。
- phase15 legacy tests 仍大量依赖旧 `ExpectationDetailResult` / raw patch semantics。
- ReAct old schema compatibility 需要单独迁移，不能混入 Document2 smoke blocker 修复。
- Document3/monitoring failures 不应在 Document2 eval 前顺手修，除非下一轮目标显式切到 Document3。
