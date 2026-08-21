# Data MCP Agent-facing Observation 投影真实验收（2026-08-20）

## 1. 变更边界

本轮只调整 Agent 可见的数据投影与展示字段，不修改任何数据源 provider、内容提取、确定性清洗、分段、选块、截断、降噪或状态判定逻辑。

完整的运行时与审计字段继续保存在私有 SQLite canonical Observation 中；workspace mirror、MCP inline result、Observation Pack、`data_read_observation` 与 citation validation 等 Agent-facing surface 统一使用精简投影。旧 attempt 已存在的完整 mirror 仍可读取和校验，不做破坏性迁移。

Agent-facing 单条 Observation 现在固定为：

```json
{
  "alias": "O18",
  "title": "applied_cutoff",
  "content": "2026-08-18",
  "source": {
    "provider": "sec",
    "locator": "0001045810-26-000069"
  }
}
```

以下字段不再高频暴露给 Agent，但仍保留在私有 canonical 记录中供持久化、校验与审计使用：`schema_version`、`run_id`、`attempt_id`、`block_id`、`tool_call_id`、`tool_name`、`locator`、`block_type`、`content_hash`、`retrieved_at`、`method_version` 等。

## 2. 代码与契约结果

- 新增统一 Observation 投影函数，避免 workspace、MCP 与 Pack 各自定义字段。
- Data MCP tool result 顶层只保留 Agent 执行决策所需的 `execution_status`、`availability`、`summary`、`delivery`、`source`，以及存在时的 `warnings` / `error`。
- Observation Pack manifest/catalog 移除 run、attempt、tool-call、hash 等 Agent 无需阅读的内部字段；文本正文不变。
- citation mirror 校验同时接受新精简投影与旧完整 Observation JSON，保证历史 attempt 兼容。
- source capture 沿用相同 Observation store，因此自动获得精简 workspace mirror；其既有提取逻辑未改动。

以用户提供的 O18 为普通样本测量：完整 JSON 为 1,972 字符，精简投影为 162 字符，减少 91.8%，`content` 完全一致。首轮真实验收新生成的 184 条 Observation 中，Agent mirror 合计比对应完整 canonical JSON 减少 26.5%；该总体比例较低是因为大段正文被按要求原样保留。

## 3. 自动化验证

- `pytest tests/test_data_mcp_runtime.py tests/test_codex_d1_attempt_protocol.py tests/test_codex_d1_pilot.py -q`：51 passed。
- Ruff：通过。
- strict mypy：通过（7 个本轮相关文件）。
- stdio MCP 实际启动并调用 `sec_issuer_filings`：调用成功，返回 5 条 Observation；顶层和 Observation 字段均符合精简契约。
- 测试明确比对 Agent projection 与私有 canonical 的 `content` 相等，并确认私有 canonical hash 仍然存在。

## 4. 全量 Data MCP 真实调用验收

注册表共 81 个数据工具，其中 79 个具有 Data MCP contract；Tavily search 与 AnySearch 当前不属于 Data MCP contract。对 79 条 contract 路径逐一验收：68 个只读、可暴露工具执行真实 provider 调用；7 个静态 entitlement-unavailable 工具和 4 个非只读工具验证了受控不可用/不暴露契约。

所有 79 条路径均通过以下投影检查：

- Agent 顶层字段符合精简契约；
- 每条 Agent Observation 仅含 `alias/title/content/source`；
- Agent `content` 与私有 canonical `content` 完全一致；
- workspace mirror 使用精简投影且正文一致；
- partial、empty、failed、unavailable 结果也没有泄漏内部运行时字段。

68 个可暴露工具的最终 provider 结果：

| 状态 | 数量 | 说明 |
|---|---:|---|
| succeeded | 57 | 正常返回预期数据；部分 provider 自身标记 degraded，但调用成功 |
| partial | 3 | `congress.legislative_actions`、`ir.official_updates`、`sam.contract_opportunities` |
| empty | 1 | `sec.material_contracts_projects`；保持既有状态/正文逻辑，仅验证投影 |
| failed | 7 | 4 个 IBKR 本地网关不可达；另有 3 个外部数据/作用域结果，见下文 |

失败项并非由本轮字段投影引入，且其失败返回同样通过精简契约：

- `ibkr.contract_search`、`ibkr.market_history`、`ibkr.market_snapshot`、`ibkr.trade_tape`：`ibkr_gateway_unavailable`，当前本地 TWS/Gateway 连接状态不可用。
- `market.trade_tape`：`empty_stream_sample`，当前样本窗口无成交流数据。
- `tavily.extract`：`empty_or_low_quality_result`，provider 未返回达到质量门槛的正文。
- `doxa_get_social_result_detail`：`invalid_scope`，动态选中的事件没有可用 social-detail scope；同一作用域下的 Doxa/DoxAtlas 其余依赖工具均成功。

另外 11 个不执行普通只读调用的 contract 也按设计通过 gate：

- entitlement-unavailable：`benzinga.analyst_events`、`benzinga.management_guidance`、`benzinga.market_signals`、`fmp.sector_performance`、`fmp.sell_side_estimates`、`fmp.valuation_snapshot`、`twelvedata.sell_side_estimates`。
- 非只读/不向普通 Data MCP agent 暴露：`doxa_run_analysis`、`doxa_run_narrative_research`、`finnhub.trade_stream`、`monitoring.update_ticker_config`。

机器可读验收明细保存在 `.tmp/data_mcp_projection_acceptance_20260820.json` 与 `.tmp/data_mcp_projection_recheck_20260820.json`。这些文件仅为本地验收证据，不属于生产输出契约。

## 5. 验收结论

共性问题已按“只改输出字段”边界完成：Agent 不再需要阅读 Observation 的 runtime/persistence 元数据；完整审计信息仍在私有持久化层可用；所有 Data MCP contract 路径均通过投影与正文等价性验收。当前剩余异常均为既有 entitlement、provider、网关或动态作用域状态，不需要也不应通过改动内容提取/清洗逻辑来掩盖。
