# DoxAgent Dashboard State API 全量 Mock 说明

日期：2026-06-30

本文说明第一阶段前端可直接调用的 Dashboard State API mock 服务。该服务严格以仓库根目录的 `Dashboard State API 契约.md` 为路径和响应结构准绳，只返回内存 fixture，不连接真实 DB、workflow、runtime scheduler、Message Bus SQLite 或 Persistent Runtime。

## 启动方式

安装依赖后在仓库根目录运行：

```powershell
uv run python -m doxagent.dashboard_api --host 127.0.0.1 --port 8780
```

或使用 uvicorn：

```powershell
uv run uvicorn doxagent.dashboard_api.app:app --host 127.0.0.1 --port 8780
```

本地 Base URL：

```text
http://127.0.0.1:8780/api/dashboard/v1
```

健康检查：

```text
GET http://127.0.0.1:8780/healthz
```

## Mock 模式切换

当前只实现全量 mock 模式：

```powershell
$env:DOXAGENT_DASHBOARD_API_MODE = "mock"
```

如果设置为其他值，服务会启动失败，避免误以为已经接入真实后端。后续接真实聚合服务时，应新增 real/hybrid mode，而不是复用当前 fixture store。

鉴权默认是本地开发友好的开放 mock：

```powershell
$env:DOXAGENT_DASHBOARD_AUTH_MODE = "mock-open"
```

需要测试契约中的 401/403 时可切换：

```powershell
$env:DOXAGENT_DASHBOARD_AUTH_MODE = "mock-required"
```

`mock-required` 下请求必须带：

```http
Authorization: Bearer dev-mock-token
```

如果 token 为 `forbidden`，mock 会返回契约格式的 `FORBIDDEN`。

## 已覆盖接口

所有普通响应统一为：

```json
{
  "data": {},
  "meta": {
    "request_id": "req_xxx",
    "generated_at": "2026-06-30T12:00:00Z",
    "source": "dashboard_state_api"
  }
}
```

错误响应统一为：

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "请求的 Dashboard mock 资源不存在。",
    "retryable": false,
    "details": {}
  },
  "request_id": "req_xxx"
}
```

已覆盖契约接口：

| Method | Path |
| --- | --- |
| `GET` | `/overview` |
| `GET` | `/tickers` |
| `GET` | `/tickers/{ticker}` |
| `POST` | `/tickers` |
| `POST` | `/tickers/{ticker}/pause` |
| `DELETE` | `/tickers/{ticker}` |
| `POST` | `/tickers/{ticker}/restart` |
| `GET` | `/tickers/{ticker}/documents/current` |
| `GET` | `/tickers/{ticker}/documents/{document_type}/versions` |
| `GET` | `/tickers/{ticker}/documents/{document_type}/versions/{version_id}` |
| `GET` | `/tickers/{ticker}/known-events` |
| `GET` | `/tickers/{ticker}/policies` |
| `GET` | `/tickers/{ticker}/message-bus/overview` |
| `GET` | `/tickers/{ticker}/message-bus/messages` |
| `GET` | `/tickers/{ticker}/message-bus/config` |
| `PATCH` | `/tickers/{ticker}/message-bus/config/{source_id}` |
| `DELETE` | `/tickers/{ticker}/message-bus/config/{source_id}` |
| `GET` | `/tickers/{ticker}/runtime/overview` |
| `GET` | `/tickers/{ticker}/runtime/graph` |
| `GET` | `/tickers/{ticker}/runtime/nodes/{node_id}` |
| `GET` | `/tickers/{ticker}/runtime/executions` |
| `GET` | `/tickers/{ticker}/runtime/executions/{execution_id}` |
| `GET` | `/tickers/{ticker}/audit/revenue` |
| `POST` | `/tickers/{ticker}/audit/revenue/run` |
| `GET` | `/tickers/{ticker}/audit/cost` |
| `GET` | `/tickers/{ticker}/audit/cost/details` |
| `GET` | `/events` |

## Mock 数据场景

内置 ticker 覆盖典型前端状态：

| ticker | 场景 |
| --- | --- |
| `MU` | 正常运行，有 Document 当前/历史版本、Known Events、Policy、消息流、runtime 处理记录、收益审计和成本审计。 |
| `ASTS` | 降级状态，有 source timeout、runtime failed execution 和处理中审计状态。 |
| `NVDA` | 初始化中，适合测试加载态。 |
| `EMPTY` | 已停止且大部分列表为空，适合测试空状态。 |

操作类接口只修改当前进程内 mock fixture：

- `POST /tickers`：新增一个内存 ticker；已存在且未停止时返回 `TICKER_ALREADY_RUNNING`。
- `POST /pause`、`POST /restart`、`DELETE /tickers/{ticker}`：只改 mock ticker 状态。
- Message Bus config mutation：只改 mock source binding，不写入真实配置。
- `POST /audit/revenue/run`：返回 `calculating`，不触发真实审计任务。

## SSE 调试

前端可直接连接：

```text
GET http://127.0.0.1:8780/api/dashboard/v1/events?ticker=MU
```

支持契约中的筛选：

```text
event_types=runtime.execution.updated,trade_intent.created
last_event_id=evt_mock_1001
```

自动化测试或快速探活可加隐藏参数 `once=true`，让 mock 发送一轮事件后结束：

```text
GET /api/dashboard/v1/events?ticker=MU&event_types=runtime.execution.updated&once=true
```

默认事件类型覆盖：

- `message_bus.message.created`
- `runtime.execution.updated`
- `runtime.execution.failed`
- `trade_intent.created`
- `audit.cost.status_changed`
- `dashboard.heartbeat`

## 当前仍未实现

- 未接 Supabase 真实鉴权；当前仅提供 mock-open/mock-required/mock-forbidden。
- 未接真实 DB、Blackboard、runtime scheduler、Monitoring Message Bus 或 Persistent Runtime。
- 未提供前端页面；本任务只提供后端 mock API。
- 未提供 real/hybrid 聚合模式；后续应新增独立 service，不要让 mock fixture 变成真实数据适配层。
- 未实现真实收益审计、成本审计和真实 SSE broker；当前数据均为 fixture。

## 验证命令

```powershell
uv run pytest -q tests\test_dashboard_mock_api.py
uv run ruff check src\doxagent\dashboard_api tests\test_dashboard_mock_api.py
```
