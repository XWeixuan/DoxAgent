# Step 1.5 Test Baseline Known Failures

本文冻结 Step 1 behavior-preserving extraction 之后、Step 2 之前的测试基线分层。

基线来源：Step 1 后执行 `uv run pytest -q`，结果为 `397 passed, 23 skipped, 16 failed`。本轮 Step 1.5 只处理会直接阻塞 Step 2 的 A 类失败；B 类仅记录；C 类补窄护栏并判断是否阻塞 Step 2。

## 分类定义

| 类别 | 处理方式 |
| --- | --- |
| A | 本轮拆分或 Step 2 会直接依赖，必须现在修。 |
| B | 既有基线、Document3、Phase6 文案、Document2 legacy blocker 或无关 dev_plan，本轮只记录不修。 |
| C | 可能影响 Document1ContextPack、GlobalNarrativeReport 或 market evidence，先补窄护栏并判断是否阻塞 Step 2。 |

## 当前结论

| 类别 | 数量 | Step 1.5 处置 |
| --- | ---: | --- |
| A | 0 | 无需修 runtime。 |
| B | 11 | 记录为 known baseline failure，不在本轮修复。 |
| C | 5 | 已补 Step2 前窄 characterization guards；当前不阻塞 Step 2。 |

## 16 个 full pytest failures 分类表

| # | Test | 失败摘要 | 分类 | Step 1.5 判断 |
| ---: | --- | --- | --- | --- |
| 1 | `tests/test_baseline.py::test_phase0_baseline_files_exist` | 缺少 `dev_plan/PHASE0_BASELINE.md`。 | B | dev_plan/baseline 文件缺口；不属于 Document1/2 workflow 拆分边界。 |
| 2 | `tests/test_phase13_real_workflow.py::test_agent_runner_workflow_completes_with_structured_agent_result_json` | `ResolveMonitoringConfig` 需要 `monitoring.update_ticker_config`，runner 无 tool registry。 | B | Document3/monitoring registry 基线问题；不修。 |
| 3 | `tests/test_phase13_real_workflow.py::test_global_narrative_tool_call_fragment_is_replaced_with_chinese_fallback` | GlobalNarrativeReport 之后继续跑到 Document3 monitoring registry blocker。 | C | 影响 GlobalNarrativeReport 信号；已新增 stop-after narrative 护栏，不阻塞 Step 2。 |
| 4 | `tests/test_phase13_real_workflow.py::test_agent_runner_resume_latest_after_manual_blocker_resolution` | 继续跑到 Document3 monitoring registry blocker。 | B | Document3/resume 路径基线问题；不修。 |
| 5 | `tests/test_phase13_real_workflow.py::test_agent_runner_partial_retry_does_not_duplicate_completed_global_commit` | 继续跑到 Document3 monitoring registry blocker，遮蔽后续 commit 断言。 | C | 涉及 global research commit 幂等信号；当前失败点不在 Document1，Step2 前以窄 Document1/GNR commit guard 覆盖，不阻塞。 |
| 6 | `tests/test_phase14_global_research_integration.py::test_global_research_resume_reuses_completed_agent_sections_after_failure` | 旧断言期望首次 retryable failure 后 `BLOCKED`，当前行为为同节点 retry 后 `RUNNING`。 | C | 属于 Document1 builder/recovery 行为漂移信号；现有 Phase5 已覆盖 retry-once 成功路径，本轮不回滚行为，不阻塞 Step 2。 |
| 7 | `tests/test_phase14_global_research_integration.py::test_o2_monitoring_tasks_use_node_specific_write_targets` | 继续跑到 Document3 monitoring registry blocker。 | B | Document3/monitoring 写入目标路径；不修。 |
| 8 | `tests/test_phase14_global_research_integration.py::test_global_research_inputs_round_trip_for_resume` | resume 不设 stop-after，继续跑到 Document3 monitoring registry blocker。 | C | 可能影响 Document1 inputs/context；已新增 builder/context 护栏验证 research inputs round trip，不阻塞。 |
| 9 | `tests/test_phase15_o1_a1_a2_realization.py::test_workflow_uses_a2_retrieval_to_complete_delegation_and_o1_resolves_objection` | 继续跑到 Document3 monitoring registry blocker。 | B | Document2/Document3 交界旧链路；不修。 |
| 10 | `tests/test_phase15_o1_a1_a2_realization.py::test_o1_revision_delegation_completes_after_o1_resolves_review_objection` | 继续跑到 Document3 monitoring registry blocker。 | B | Document2/Document3 交界旧链路；不修。 |
| 11 | `tests/test_phase15_o1_a1_a2_realization.py::test_objection_resolution_batches_large_unresolved_sets` | 期望 5 个 result，实际 2 个。 | B | Document2 legacy resolver batching；明确不修 Document2 blocker。 |
| 12 | `tests/test_phase15_o1_a1_a2_realization.py::test_workflow_converts_direct_document_outputs_to_blackboard_patches` | 继续跑到 Document3 monitoring registry blocker。 | B | Document2/Document3 旧链路；不修。 |
| 13 | `tests/test_phase15_o1_a1_a2_realization.py::test_a1_workflow_nodes_receive_minimal_doxatlas_tool_sets` | 期望 A1 `ReviewExpectationFields` 有 DoxAtlas tools，当前 override 为空。 | B | Document2 legacy tool contract 旧断言；本轮不改 tool normalizer/compat。 |
| 14 | `tests/test_phase15_o1_a1_a2_realization.py::test_o1_revised_patch_replaces_pending_expectation_patch` | `KeyError: expectation_unit`。 | B | Document2 legacy patch/resolution blocker；不修。 |
| 15 | `tests/test_phase6_e2e_sample.py::test_phase6_sample_documents_include_monitoring_outputs_without_trading_execution` | sample 文案缺少“不触发券商下单”。 | B | Phase6 文案/sample 基线；不修。 |
| 16 | `tests/test_phase8_o4_market_trace.py::test_market_trace_agent_registry_exposes_market_trace_schema` | 期望 `ResearchSection`，当前为 `ResearchSection\|MonitoringPolicyDocument`。 | C | 触及 O4/market trace schema 信号，但不是 Step2 blocker；Document1 builder guard 已覆盖 O4 作为 market trace section 的当前合同。 |

## Step 2 前新增护栏

新增/更新 `tests/test_initialization_characterization.py`：

| 护栏 | 覆盖点 |
| --- | --- |
| Document1 builder 行为冻结 | `BuildGlobalResearch` stop-after 生成单个 `GlobalResearchDocument`，C1/C2/C3/O4 输入、权限、research inputs、commit count 与 `market_narrative_report=None`。 |
| Document1 context 行为冻结 | `GenerateExpectationConstruction` 的 O1 `global_research_context` shape，四个 Document1 section 可见，`market_narrative_report` 对 O1 construction 不可见。 |
| GenerateGlobalNarrativeReport 行为冻结 | tool-call-only narrative section 在 `GenerateGlobalNarrativeReport` stop-after 前被 fallback 替换，并只提交 `document.market_narrative_report`，不穿透到 Document3。 |

## Step 2 Gate

进入 Step 2 前必须通过：

- `uv run pytest tests/test_initialization_characterization.py -q`
- targeted workflow suite：`uv run pytest tests/test_initialization_characterization.py tests/test_phase5_initialization_workflow.py tests/test_phase16_react_harness.py tests/test_workflow_normalizer.py -q`

full pytest 的 16 个 failure 保持为 known baseline，不作为 Step 2 的阻塞条件；若 Step 2 改动触及 C 类对应行为，需要先更新或收紧相应 characterization guard。
