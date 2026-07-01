# Persistent Runtime Execution PRD 验收审计

日期：2026-06-24

本文只记录当前 worktree 对 `dev_plan/Persistent_Runtime_Execution_PRD.md` 第 22 节 50 条验收标准的证据状态。状态含义：

- 已验证：已有代码路径和定向测试覆盖。
- 部分验证：核心实现存在，但证据仍偏间接或仍有运行边界未证明。
- 未完成/未证明：不能作为完成证据。

## 当前验证命令

- `uv run pytest -q tests\test_phase23_persistent_runtime_execution.py tests\test_phase4_agent_runtime.py`
- `uv run pytest -q tests\test_phase16_react_harness.py::test_react_enforces_global_tool_call_batch_budget tests\test_phase16_react_harness.py::test_react_lazily_validates_runtime_o3_result_schema tests\test_phase16_react_harness.py::test_react_runtime_o3_prompt_includes_output_contract tests\test_phase23_persistent_runtime_execution.py tests\test_phase4_agent_runtime.py`
- `uv run ruff check src\doxagent\persistent_runtime src\doxagent\agents\runtime\react.py src\doxagent\agents\runtime\runner.py src\doxagent\models\common.py src\doxagent\agents\config.py tests\test_phase16_react_harness.py tests\test_phase23_persistent_runtime_execution.py tests\test_phase4_agent_runtime.py`
- `uv run mypy src\doxagent\persistent_runtime src\doxagent\agents\runtime\react.py src\doxagent\agents\runtime\runner.py src\doxagent\models\common.py src\doxagent\agents\config.py tests\test_phase16_react_harness.py tests\test_phase23_persistent_runtime_execution.py tests\test_phase4_agent_runtime.py`

当前结果：46 passed；ruff passed；mypy passed。

全量 `uv run pytest -q` 当前结果：394 passed、23 skipped、16 failed。失败主要集中在缺失旧 `dev_plan/PHASE0_BASELINE.md`、初始化 workflow 的 `ResolveMonitoringConfig` tool registry、旧 O4 schema 断言等，不能作为 Persistent Runtime 完成证明或否定证明。

## 验收矩阵

| # | 状态 | 当前证据 |
|---|---|---|
| 1 | 已验证 | `execute_event()` 消费 media Message Bus event，见 `test_execute_event_stream_item_converts_message_and_routes_once`。 |
| 2 | 已验证 | `execute_events()` / `execute_social_batch()` 消费 social batch，见 social batch tests。 |
| 3 | 已验证 | `_run_w1_w2()` 使用 `ThreadPoolExecutor(max_workers=2)`，相关 node trace 覆盖 W1/W2。 |
| 4 | 已验证 | social batch 对每条消息运行 W1/W2，见 `test_execute_events_groups_social_by_polling_window_and_preserves_batch_id`。 |
| 5 | 已验证 | `W1Result` schema 与 prompt `runtime.w1`。 |
| 6 | 已验证 | Route Engine 只使用 `is_new` 和 `confidence` 做 W1 主路由。 |
| 7 | 已验证 | `W2Result` schema 与 prompt `runtime.w2`。 |
| 8 | 已验证 | `W2Result` 无 `confidence` 字段，extra forbid。 |
| 9 | 已验证 | `W2Type` 仅四类。 |
| 10 | 已验证 | prompt、schema tests、Route Engine NULL 路由。 |
| 11 | 已验证 | prompt、schema tests、Irrelevant archive 路由。 |
| 12 | 已验证 | W2 prompt 与 legacy PRD 对齐移除 cache 类型。 |
| 13 | 已验证 | W2 prompt 和 tests 区分 NULL / Irrelevant。 |
| 14 | 已验证 | `test_media_new_dtc_high_confidence_bypasses_o3_and_records_trade`。 |
| 15 | 已验证 | DTC high/medium 只触发 O3 Known Events update-only，不进交易判断 O3。 |
| 16 | 已验证 | media new + EBA Route Engine 到 O3。 |
| 17 | 已验证 | media new + NULL Route Engine 到 O3。 |
| 18 | 已验证 | media new + Irrelevant archive。 |
| 19 | 已验证 | media old + DTC/EBA high archive。 |
| 20 | 已验证 | media old + DTC/EBA medium/low 到 A2。 |
| 21 | 已验证 | media old + NULL/Irrelevant archive。 |
| 22 | 已验证 | social old archive。 |
| 23 | 已验证 | social new + non-Irrelevant 到 A2。 |
| 24 | 已验证 | social A2 passed 到 O3。 |
| 25 | 已验证 | prompt 要求与 `test_heuristic_w2_applies_stricter_irrelevant_threshold_for_social`。 |
| 26 | 已验证 | batch_window_id 合并，未做 semantic clustering，见 social batch O3 context test。 |
| 27 | 已验证 | O3 prompt、service timeout、ReAct `max_steps`、per-tool limit、全局 `max_tool_call_batches=1`、O3Result prompt contract 与 lazy schema validation 均已实现；见 phase16 O3 schema/contract/tool batch tests 与 phase23 O3 budget test。 |
| 28 | 已验证 | `O3RuntimeBudget.target_seconds=120` 默认与超时测试。 |
| 29 | 已验证 | O3 timeout route 到 Trading Records，不进 ingest_queue。 |
| 30 | 已验证 | TradingRecord `status=recorded_with_exception`、`exception_type=o3_timeout`。 |
| 31 | 已验证 | `O3PrimaryAction` 五类动作与 O3 action tests。 |
| 32 | 已验证 | O3 patch log + runtime Known Events current state。 |
| 33 | 已验证 | Known Events patch 直接 upsert current state，后续 W1 自动预装消费。 |
| 34 | 已验证 | `KnownEventsPatchLog` / `RuntimeKnownEvent` 最小审计字段。 |
| 35 | 已验证 | objection / objection_note route 与 record 区分。 |
| 36 | 已验证 | ticker daily blocking objection limit。 |
| 37 | 已验证 | objection_note 进入 `daily_close_review` ingest queue。 |
| 38 | 已验证 | runtime route 只持久化 `ingest_queue` / `archive`，无 cache route。 |
| 39 | 已验证 | `IngestQueueItem` 保留 DoxAtlas/research agent 可消费 flags；未接 DoxAtlas。 |
| 40 | 已验证 | `ArchiveItem` 只留审计 payload。 |
| 41 | 已验证 | O3 prompt 禁 broker；TradingRecord 无 broker/order 字段。 |
| 42 | 已验证 | 当前只记录 trade intent，不真实下单。 |
| 43 | 已验证 | trade 路径统一 `save_trading_record()`。 |
| 44 | 已验证 | TradingRecord 最小 trade intent 字段，无完整交易账本字段。 |
| 45 | 已验证 | `RuntimeExecutionRecord`、side-effect tables、exception log、node traces。 |
| 46 | 已验证 | W1/W2/A2/O3 retry/fallback tests 覆盖关键失败路径。 |
| 47 | 已验证 | source_message idempotency tests。 |
| 48 | 已验证 | URL/content_hash/url_hash/source_time/social batch item duplicate archive tests。 |
| 49 | 已验证 | `runtime_observations()` 暴露状态、耗时、最终去向和副作用。 |
| 50 | 已验证 | legacy Document3 PRD 和旧顶层 PRD 已声明 Persistent Runtime 优先边界，runtime package 独立，O3/W2/cache/Known Events/O3 budget 语义已与生成阶段文档拆开；全仓旧初始化测试失败不落在 Persistent Runtime 边界证明上。 |

## 当前剩余风险

1. 全量 pytest 仍有旧线失败；虽然不是 Persistent Runtime 定向失败，但仓库级 CI 若强制跑全量测试，仍需要在后续旧初始化/Phase0 维护任务中修复或隔离这些红灯。
