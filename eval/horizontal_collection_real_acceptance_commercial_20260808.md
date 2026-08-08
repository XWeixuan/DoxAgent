# Horizontal Collection 商业数据源真实验收（2026-08-08）

## 范围与方法

- 使用当前 `.env` 与 `.env.providers.local` 构建 `default_real_tool_registry`，直接调用已注册的实际 client；未调用任何 LLM。
- 每个 business tool 仅执行 **一次**最小真实调用。没有对相同认证、订阅或 Gateway 故障重试。
- 固定标的为 `AAPL`；IBKR snapshot/history 使用公开已知的股票 `conid=265598`。所有 args、输出键与计数均为非敏感摘要；本记录不保存 API key、响应正文或 raw payload。
- `record count` 为工具直接暴露的列表计数；复合工具只暴露 provider 分组时，记录为 `composite`，避免把各供应商的异构 payload 误合并计数。

## 验收结果

| Tool | 非敏感最小 args | 状态 | error.code | 耗时 | 顶层输出键 / record count | summary / 结论 |
|---|---|---|---|---:|---|---|
| `ibkr.contract_search` | `symbol=AAPL` | FAILED | `auth_failed` | 991 ms | — | IBKR Gateway / 认证未就绪；未重试。 |
| `ibkr.market_snapshot` | `symbol=AAPL`, `conid=265598`, fields=`31,84,86` | FAILED | `auth_failed` | 1,140 ms | — | 同一认证边界；未重试。 |
| `ibkr.market_history` | `symbol=AAPL`, `conid=265598`, `period=1m`, `bar=1d` | FAILED | `auth_failed` | 1,095 ms | — | 同一认证边界；未重试。 |
| `benzinga.short_interest` | `symbol=AAPL` | BLOCKED | `tool_execution_failed` | <1 ms | — | 当前配置没有 `BENZINGA_API_KEY`，在发出 HTTP 请求前失败。 |
| `benzinga.management_guidance` | `symbol=AAPL` | BLOCKED | `tool_execution_failed` | <1 ms | — | 同上。 |
| `benzinga.transcripts` | `symbol=AAPL` | BLOCKED | `tool_execution_failed` | <1 ms | — | 同上。 |
| `benzinga.analyst_events` | `symbol=AAPL`, `event_type=ratings` | BLOCKED | `tool_execution_failed` | <1 ms | — | 同上。 |
| `benzinga.market_signals` | `symbol=AAPL`, `signal_type=option_activity` | BLOCKED | `tool_execution_failed` | <1 ms | — | 同上。 |
| `fmp.sell_side_estimates` | `symbol=AAPL`, `period=annual` | SUCCEEDED | — | 4,384 ms | `provider`, `symbol`, `sell_side_estimates`, `provider_errors`, `source_coordinates`; composite, `provider_errors=0` | 当前 sell-side estimates / targets / ratings 端点均成功。 |
| `fmp.valuation_snapshot` | `symbol=AAPL` | SUCCEEDED | — | 3,798 ms | `provider`, `symbol`, `valuation_snapshot`, `provider_errors`, `source_coordinates`; composite, `provider_errors=0` | Trailing valuation 输入端点成功；该工具未计算派生 multiple。 |
| `fmp.transcript_fallback` | `symbol=AAPL`, `year=2025`, `quarter=4` | FAILED | `upstream_provider_error` | 1,806 ms | — | 单次 transcript endpoint probe 未返回可用结果；未重复调用。需在后续供应商 entitlement / 可用季度验证中处理。 |
| `twelvedata.sell_side_estimates` | `symbol=AAPL` | SUCCEEDED | — | 1,990 ms | `provider`, `symbol`, `sell_side_estimates`, `provider_errors`, `source_coordinates`; composite, `provider_errors=0` | EPS 与 revenue estimate 均返回可用结果。 |
| `twelvedata.daily_ohlcv` | `symbol=AAPL`, `outputsize=5` | SUCCEEDED | — | 893 ms | `provider`, `symbol`, `interval`, `ohlcv`, `meta`, `market_evidence_snapshot`, `fallback_tool`, `source_coordinates`; `ohlcv=5` | 现有日线工具可用，并产出 market-evidence snapshot。 |
| `finnhub.ownership_and_insiders` | `symbol=AAPL` | PARTIAL | `finnhub_partial_subrequest_failure` | 2,327 ms | `provider`, `symbol`, `ownership_and_insiders`, `provider_errors`, `source_coordinates`; composite, `provider_errors=1` | 两个子 endpoint 中一项可用、一项不可用；部分成功语义正确保留。 |
| `finnhub.company_news_events` | `symbol=AAPL`, `date_from=2026-07-01`, `date_to=2026-08-08` | SUCCEEDED | — | 1,881 ms | `provider`, `symbol`, `company_news_events`, `provider_errors`, `source_coordinates`; composite, `provider_errors=0` | 公司新闻与 earnings-event 子请求成功。 |
| `finnhub.company_peers` | `symbol=AAPL` | SUCCEEDED | — | 2,058 ms | `provider`, `symbol`, `peers`, `source_coordinates`; `peers=12` | 现有 peers 工具成功。 |

## 结论与上线门槛

1. **可用：** FMP estimates/valuation、Twelve Data estimates/daily OHLCV、Finnhub news/events、Finnhub peers。
2. **部分可用：** Finnhub ownership/insiders；Collection Manifest 必须保留该 target 的 `PARTIAL` 与子 endpoint 错误，不能把结果提升为完整 ownership State。
3. **阻断：** Benzinga 需要注入 `BENZINGA_API_KEY` 后才能开始 endpoint entitlement probe；这是配置缺失，不是 endpoint 已验收失败。
4. **阻断：** IBKR 需要已认证 Client Portal Gateway / 有效会话及所需 market-data subscriptions。三项 `auth_failed` 同源，后续只应在 Gateway 准备完成后做一次新的验收轮次。
5. **待供应商确认：** FMP transcript fallback 的单次探测为 `upstream_provider_error`。在确认计划权限或选择已有 transcript 年/季前，不应将其标记为 production-ready。

## 复核

本次 provider 代码与验收测试复跑：

- `tests/test_commercial_provider_tools.py`：`6 passed`
- `tests/test_phase11_real_tools.py`：`36 passed`（3 个第三方依赖 warning）
- `ruff check`（商业 provider 与验收测试）：通过
- `git diff --check`（商业 provider 与验收测试）：通过
