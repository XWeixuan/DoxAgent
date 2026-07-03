# Runtime Scheduler API Handoff

日期：2026-07-02  
用途：给后续接真实 Dashboard State API 的 Codex 快速定位统一运行调度模块。

## 必读边界

- Dashboard API 契约唯一依据是根目录 `Dashboard State API 契约.md`，不要恢复旧 `api_contract.md`。
- 当前 `src/doxagent/dashboard_api/` 是 fixture-backed mock，不连接真实 DB、workflow、runtime scheduler、Message Bus 或 Persistent Runtime。接真实后端时应新增 real/hybrid 聚合层，不要把 mock fixture 改成真实适配器。
- 正式 ticker runtime 入口是 `python -m doxagent.runtime_scheduler.cli run-loop` 或其 Python 等价调用。旧 `python -m doxagent.monitoring.cli poll-forever` 只能作为 Message Bus 调试/底层入口，不能在正式 runtime 模式下并行轮询同一 ticker/source。

## 可直接复用的后端入口

- Python facade：`doxagent.runtime_scheduler.DashboardStateAPI`
- Service：`doxagent.runtime_scheduler.UnifiedRuntimeSchedulerService`
- 常驻 loop：`doxagent.runtime_scheduler.RuntimeSchedulerLoop`
- CLI smoke：`python -m doxagent.runtime_scheduler.cli overview|detail|start|pause|stop|tick|tick-all|run-loop|message-bus|runtime|trade-intents|audit`

`DashboardStateAPI.get_ticker(ticker)` 已返回后端联调最重要的聚合块：

- `state`
- `document_status`
- `message_bus_status`
- `runtime_status`
- `trade_intents`
- `exceptions`
- `refresh_requests`
- `audit_events`

## API 接入建议

- 真实 FastAPI route 先包一层 assembler，把 facade/service 的内部模型裁剪成 `Dashboard State API 契约.md` 的 `data/meta/error` 响应；不要让前端直接依赖内部 Pydantic 模型。
- `/tickers`、`/tickers/{ticker}`、`POST /tickers`、`pause`、`delete/stop` 可优先接 `DashboardStateAPI`。
- `/message-bus/*` 需要组合 `message_bus_status`、`MonitoringBusService.status_snapshot()`、recent messages/config。
- `/runtime/*` 需要组合 `runtime_status`、`PersistentRuntimeExecutionService.recent_executions()`、`runtime_observations()`。
- 成本统计尚未完整接入，`llm_call_count` 应继续显示 `null` / `not_yet_integrated`，不要伪造 0。

## 运行配置

常用 SQLite 配置：

```powershell
$env:DOXAGENT_RUNTIME_SCHEDULER_STORAGE_MODE="sqlite"
$env:DOXAGENT_RUNTIME_SCHEDULER_SQLITE_PATH=".tmp/runtime_scheduler.sqlite3"
$env:DOXAGENT_MONITORING_STORAGE_MODE="sqlite"
$env:DOXAGENT_MONITORING_SQLITE_PATH=".tmp/monitoring_message_bus.sqlite3"
$env:DOXAGENT_PERSISTENT_RUNTIME_STORAGE_MODE="sqlite"
$env:DOXAGENT_PERSISTENT_RUNTIME_SQLITE_PATH=".tmp/persistent_runtime_execution.sqlite3"
```

## 验证入口

- 聚焦测试：`uv run pytest -p no:cacheprovider tests\test_phase25_runtime_scheduler.py tests\test_phase21_monitoring_message_bus.py tests\test_phase23_persistent_runtime_execution.py`
- MU 演示：`uv run python eval\run_runtime_scheduler_mu_e2e.py`
- CLI loop smoke：`uv run python -m doxagent.runtime_scheduler.cli run-loop --max-iterations 1 --sleep-seconds 0 --quiet`

MU 演示会优先查真实 MU 文档；若本地没有可用 Document 1/2/3，会在报告中明确标注 fixture 文档和模拟标准消息边界。 broker 仍未接入，trade intent 只代表 `recorded_only` 候选意图。
