# 交易意图级 Paper Trading 收益审计

收益审计是独立于 Persistent Runtime 的低频批处理能力。它只评估已沉淀的 `TradingRecord / TradeIntent`，不下单、不维护账户和持仓，也不会因行情失败阻塞消息监测或交易意图生成。

## 计算口径

每条交易意图独立计算三个结果：

- `ideal_signal`：严格使用消息 `published_at`；缺失时仅该口径为 `missing_time`。
- `message_bus`：严格使用固化的 Message Bus `event_time`。
- `system_executable`：使用交易意图生成并落库时间；Dashboard 默认口径。

新交易意图在 `TradingRecord.audit_snapshot` 中固化发布时间、抓取/标准化/入队时间、Runtime 开始时间、意图生成时间、来源、Policy、消息 ID、execution ID 和轻量摘要。历史数据只使用能够可靠恢复的值，不以相邻时间字段静默替代。

所有时间按 `America/New_York` 转换。入场使用锚点所在分钟向上取整后第一根正常时段 `1m` K 线的 `open`，绝不使用锚点之前的价格。15:50 ET 之后才出现的锚点顺延至后续实际有行情的正常交易日。退出选择入场当日最接近 15:50 ET、且晚于入场的有效分钟 `open`；无有效组合时标记 `missing_market_data`。

Long 和 Short 都保存理论收益及双边不利滑点后的模拟收益。默认单边滑点 5 bps，本金规则为 `$10,000 × {small: 0.5, normal: 1, aggressive: 2}`。周期收益率为 `sum(PnL) / sum(虚拟本金)`，不是逐笔收益率相加。

## 行情源

行情 provider 是可替换的，但不会在单次审计中静默自动切换：

- `twelvedata`：当前默认，使用 `/time_series` 的 `1min` 数据。
- `benzinga`：使用 `/api/v2/bars`、`interval=1m`、`session=REGULAR`；只有当前 Token 具备 Historical Bars 权限时启用。

每条审计结果记录实际 provider 和精确数据源标识。provider 请求失败时保留短错误原因，成功记录和其他口径不会丢失。真实能力验证见 [revenue-audit-market-data-verification-2026-07-10.md](./revenue-audit-market-data-verification-2026-07-10.md)。

## 配置

环境变量及默认值见仓库根目录 `.env.example`：

```text
DOXAGENT_REVENUE_AUDIT_STORAGE_MODE=sqlite
DOXAGENT_REVENUE_AUDIT_SQLITE_PATH=.tmp/revenue_audit.sqlite3
DOXAGENT_REVENUE_AUDIT_MARKET_DATA_PROVIDER=twelvedata
DOXAGENT_REVENUE_AUDIT_METHOD_VERSION=paper-trade-v1
DOXAGENT_REVENUE_AUDIT_SLIPPAGE_BPS=5
DOXAGENT_REVENUE_AUDIT_BASE_NOTIONAL_USD=10000
DOXAGENT_REVENUE_AUDIT_SMALL_MULTIPLIER=0.5
DOXAGENT_REVENUE_AUDIT_NORMAL_MULTIPLIER=1.0
DOXAGENT_REVENUE_AUDIT_AGGRESSIVE_MULTIPLIER=2.0
DOXAGENT_REVENUE_AUDIT_AUTO_TRIGGER_HOUR_ET=18
DOXAGENT_REVENUE_AUDIT_LOOP_SLEEP_SECONDS=60
DOXAGENT_REVENUE_AUDIT_MARKET_DATA_TIMEOUT_SECONDS=30
```

配置快照被哈希为 `config_fingerprint`；结果唯一键包含交易意图、收益口径、方法版本和配置指纹。因此同配置补跑执行 upsert，新版本或新参数不会静默覆盖旧结果。

## 运行与补跑

Docker Compose 的 `revenue-auditor` 独立服务每分钟做一次低成本到期检查，只在 18:00 ET 后对当天运行中的 ticker 发起审计：

```powershell
docker compose up -d revenue-auditor dashboard
```

人工补跑可通过 Dashboard，也可直接运行：

```powershell
uv run python -m doxagent.revenue_audit.cli audit-date MU 2026-07-08
uv run python -m doxagent.revenue_audit.cli run-due --now 2026-07-08T18:00:00-04:00
```

Dashboard 接口提供独立的 overview、daily trend、keyset-paginated records 和单意图三口径 detail。页面不固定轮询，只在首次加载、条件切换、手动刷新/补跑和 `audit.revenue.status_changed` SSE 后请求。

## 持久化与查询边界

收益审计当前写入共享卷中的独立 SQLite 文件，不把分钟大 Payload 或历史消息正文迁入 Supabase。表内只保留 run、轻量时间/价格/收益列和最多三条单意图详情结果。

所有读取都要求 ticker、日期范围、口径、方法版本和配置指纹；列表还要求 limit/cursor。趋势在数据库侧按日聚合，概览使用加权收益率。查询计划回归测试验证范围查询使用 `revenue_audit_results_scope_idx`，代码中不使用 `select *`，也不会按交易意图逐条查询数据库。

