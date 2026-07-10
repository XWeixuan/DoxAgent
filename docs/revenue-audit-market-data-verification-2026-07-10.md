# 收益审计分钟行情能力验证（2026-07-10）

本记录来自真实 HTTP 请求，不是仅依据供应商文档作出的判断。测试未把 Token、响应 Header 或完整行情 Payload 写入仓库。

## Benzinga Historical Bars

测试接口：`GET https://api.benzinga.com/api/v2/bars`。

参数包含指定 ticker、`interval=1m`、`session=REGULAR` 及起止时间。分别请求了近期完整交易日 `2026-07-08` 和约 30 天前的 `2026-06-10`，两次都返回 HTTP 401，未取得任何分钟 K 线。

结论：当前提供的 Benzinga Token 不能用于该 Historical Bars 接口。401 无法区分 Token 本身无效、订阅计划未授权或接口级权限未开通，因此本阶段不继续死磕 Benzinga，也不把它设为默认源。若后续开通权限，可将 `DOXAGENT_REVENUE_AUDIT_MARKET_DATA_PROVIDER` 改为 `benzinga` 后重新执行同一验证。

## Twelve Data 替代源

测试接口：`GET https://api.twelvedata.com/time_series`，使用项目已配置的凭证。

请求 AAPL、`interval=1min`、`timezone=America/New_York`、交易日 `2026-06-10`。请求成功，取得 390 根通过校验的正常时段分钟 K 线，时间覆盖 09:30 至 15:59 ET；OHLCV 可解析且时间有序。

结论：Twelve Data 满足当前低频、意图级收益审计的最小需求，当前默认源设为 `twelvedata`。结果记录 `twelvedata:time_series:1min`，供应商失败会显式写入单条审计状态，不会自动猜价、回退日线或影响 Persistent Runtime。

同日还以隔离的内存交易意图执行了完整审计：系统口径锚点为 10:00 ET，得到 10:00 入场、15:50 退出、`completed/audited` 状态和模拟 PnL。该步骤验证了项目 provider、分钟选择、滑点计算和审计结果持久化接口的组合，而没有写入运行时数据库。

## 尚需持续观察

- 长期历史覆盖、额度和高峰期稳定性应通过实际审计运行继续观察。
- 当前只做低频批量请求，并按 ticker/日期合并请求，不适合作为实时行情链路。
- 行情源迁移至 Broker 前，应保留 provider 接口、结果数据源和方法版本字段，避免历史结果失去可追溯性。
