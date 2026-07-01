# Dashboard State API 契约

日期：2026-06-30  
范围：DoxAgent Dashboard 第一阶段前端所需的后端状态聚合接口。  
依据：`dev_plan/FRONTEND_PRD.md`、当前 `runtime_scheduler`、`monitoring`、`persistent_runtime`、`debug_viewer`、`gateway` 实现排查。

> Phase 26 起，本文件是 Dashboard State API 的唯一对齐依据；旧根目录 `api_contract.md` 已弃用并删除，不再作为后续前端或后端状态接口设计依据。

## 1. 结论与边界

### 1.1 核心结论

当前 DoxAgent 已经具备一批可复用的底层能力：

- `runtime_scheduler` 已有 Python 级 `DashboardStateAPI` facade、ticker 状态、启动/暂停/停止、Document 可用性、Message Bus 状态、Runtime 消费状态、trade intent、异常与调度审计。
- `monitoring` 已有 Message Bus source/binding/poll_state/raw/standard/event_stream、CLI 和本地 viewer。
- `persistent_runtime` 已有 W1/W2/A2/O3 运行记录、route decision、node trace、TradingRecord、ingest_queue、archive、Known Events patch、objection、exception log。
- `debug_viewer` 已有 run list、summary、brief state、agent metrics，但它是研发调试视图，不应成为新前端直接消费的 API。
- `gateway` 已有 `ModelUsage` 与 `ModelAuditSummary`，但尚无统一成本审计落库、价格表和按 ticker/node/model 聚合服务。

当前缺口也很明确：

- 尚未实现正式 `FastAPI + React + shadcn/ui` Dashboard 服务。
- 尚未实现 Supabase dev 用户鉴权中间件。
- 尚未实现 Dashboard State API 的 HTTP route、SSE event stream、统一错误响应、分页筛选排序层。
- Document 1/2/3 目前可读可用性与原始文档，但缺前端稳定卡片化 schema、历史版本接口和中文 label 映射。
- 收益审计缺买入/卖出价、滑点、退出价、收益计算与审计任务。
- 成本审计缺统一 model usage 明细表、成本价格表和聚合服务。

### 1.2 实现状态定义

本文每个接口使用以下状态：

| 状态 | 含义 |
| --- | --- |
| `existing` | 目标 Dashboard State API 形态已经存在或几乎可直接挂载。 |
| `partial` | 相关底层服务、CLI、viewer 或模型已存在，但缺正式 HTTP/FastAPI、鉴权、聚合或前端稳定 schema。 |
| `missing` | 当前没有可靠数据源或核心计算能力。 |
| `proposed` | 契约新增设计，当前项目尚未实现。 |

### 1.3 总体 API 原则

- 前端只访问 Dashboard State API，不直接读 Blackboard、workflow checkpoint、Monitoring SQLite、Persistent Runtime SQLite 或 Debug Viewer 内部 API。
- Dashboard State API 负责字段转换、中文 label、分页、筛选、排序、状态枚举、错误响应和 SSE。
- 内部表结构可以变化，但 Dashboard State API 的前端 schema 应保持稳定。
- 所有 mutation 接口必须后端再次鉴权，并写入审计事件。
- 当前阶段所有 trade 相关对象都只能表示 `trade_intent` 或 `trading_record`，不能暗示真实 broker order。

## 2. 全局约定

### 2.1 Base URL

```text
https://agent.doxatlas.com/api/dashboard/v1
```

本地开发可使用：

```text
http://127.0.0.1:<dashboard_port>/api/dashboard/v1
```

当前项目状态：`proposed`。现有 HTTP 服务是 `debug_viewer` 的 `127.0.0.1:8765` 和 `monitoring_viewer` 的 `127.0.0.1:8766`，不是正式 Dashboard State API。

### 2.2 认证与权限

所有接口默认需要：

```http
Authorization: Bearer <supabase_jwt>
```

或同源 cookie session。后端必须校验：

- 用户已登录。
- 用户属于 dev 层级。
- mutation 操作具备后端权限。
- SSE 连接也必须鉴权。

错误：

```json
{
  "error": {
    "code": "UNAUTHORIZED",
    "message": "请先登录。",
    "retryable": false,
    "details": {}
  },
  "request_id": "req_01"
}
```

当前实现状态：`missing`。仓库未发现正式 DoxAgent Dashboard Supabase auth middleware；`debug_viewer` 与 `monitoring_viewer` 是本地 stdlib HTTP 服务，且包含开放 CORS，不能作为生产鉴权实现复用。

### 2.3 通用成功响应

```json
{
  "data": {},
  "meta": {
    "request_id": "req_01",
    "generated_at": "2026-06-30T12:00:00Z",
    "source": "dashboard_state_api"
  }
}
```

字段含义：

| 字段 | 含义 |
| --- | --- |
| `data` | 业务数据。 |
| `meta.request_id` | 请求追踪 ID。 |
| `meta.generated_at` | API 聚合完成时间。 |
| `meta.source` | 数据由 Dashboard State API 聚合。 |

当前实现状态：`proposed`。

### 2.4 通用错误响应

```json
{
  "error": {
    "code": "TICKER_ALREADY_RUNNING",
    "message": "该标的已在监测中。",
    "retryable": false,
    "details": {
      "ticker": "MU"
    }
  },
  "request_id": "req_01"
}
```

错误枚举：

| code | HTTP | 含义 |
| --- | --- | --- |
| `UNAUTHORIZED` | 401 | 未登录或 token 无效。 |
| `FORBIDDEN` | 403 | 非 dev 用户或无 mutation 权限。 |
| `NOT_FOUND` | 404 | ticker、document、message、execution 不存在。 |
| `INVALID_PARAMS` | 422 | 参数不合法。 |
| `CONFLICT` | 409 | 状态冲突，例如已运行、已删除。 |
| `UPSTREAM_UNAVAILABLE` | 503 | 内部数据源不可用。 |
| `INTERNAL_ERROR` | 500 | 未预期错误。 |

当前实现状态：`proposed`。现有 tool/provider 有错误结构，但 Dashboard API 尚无统一错误层。

### 2.5 分页、筛选、排序

列表接口统一支持：

```text
limit=50
cursor=<opaque_cursor>
sort=-created_at
```

约定：

- `limit` 默认 50，最大 200。
- `cursor` 是不透明字符串，前端不解析。
- `sort` 使用字段名，前缀 `-` 表示倒序。
- 时间筛选统一使用 ISO 8601 UTC 字符串。

分页响应：

```json
{
  "items": [],
  "page": {
    "limit": 50,
    "next_cursor": "cur_abc",
    "has_more": true
  }
}
```

当前实现状态：`proposed`。现有 repository 多为 `limit` 或全量 list，未实现 cursor。

### 2.6 通用状态枚举

运行状态：

```text
initializing | running | paused | stopped | degraded | blocked
```

健康状态：

```text
normal | degraded | blocked | unknown
```

状态颜色：

```text
green | blue | yellow | red | gray
```

Document 可用性：

```text
available | missing | stale | invalid
```

消息处理状态：

```text
received | cleaned | deduplicated | w1_running | w2_running | workers_completed |
a2_running | o3_running | routed_to_trading_records | routed_to_ingest_queue |
routed_to_archive | objection_created | objection_note_created |
known_events_updated | failed_with_exception
```

W2 动作类型：

```text
DTC | EBA | NULL | Irrelevant
```

对应内部枚举：

- `TickerRunStatus`：`src/doxagent/runtime_scheduler/schema.py`
- `RuntimeHealth`：`src/doxagent/runtime_scheduler/schema.py`
- `DocumentAvailability`：`src/doxagent/runtime_scheduler/schema.py`
- `W2Type`：`src/doxagent/persistent_runtime/schema.py`

### 2.7 接口索引与路径变量约定

除 SSE 外，所有普通接口默认返回 `application/json`。若单个接口未列 `Request body`，表示不接收 body；若未列 `Query params`，表示无额外 query 参数。所有 path 均相对 `Base URL`。

通用路径变量：

| 变量 | 类型 | 含义 |
| --- | --- | --- |
| `ticker` | string | 股票代码，服务端统一转大写。 |
| `document_type` | string | `document1 | document2 | document3`。 |
| `version_id` | string | Dashboard 文档版本 ID，不能暴露内部 checkpoint 路径。 |
| `source_id` | string | Message Bus source ID。 |
| `node_id` | string | Runtime 图节点 ID。 |
| `execution_id` | string | Dashboard runtime execution ID。 |

接口索引：

| HTTP method | path | 前端用途 | 当前实现状态 |
| --- | --- | --- | --- |
| `GET` | `/overview` | Overview KPI 与 ticker 卡片 | `partial` |
| `GET` | `/tickers` | ticker 列表与运行状态 | `partial` |
| `GET` | `/tickers/{ticker}` | 单 ticker 页面状态初始化 | `partial` |
| `POST` | `/tickers` | 启动 ticker | `partial` |
| `POST` | `/tickers/{ticker}/pause` | 暂停 ticker | `partial` |
| `DELETE` | `/tickers/{ticker}` | 删除 ticker 监测配置 | `partial` |
| `POST` | `/tickers/{ticker}/restart` | 重启 ticker | `proposed` |
| `GET` | `/tickers/{ticker}/documents/current` | Document 1/2/3 当前版本 | `partial` |
| `GET` | `/tickers/{ticker}/documents/{document_type}/versions` | Document 历史版本列表 | `proposed` |
| `GET` | `/tickers/{ticker}/documents/{document_type}/versions/{version_id}` | Document 历史版本详情 | `proposed` |
| `GET` | `/tickers/{ticker}/known-events` | Known Events 列表 | `partial` |
| `GET` | `/tickers/{ticker}/policies` | Monitoring Execution Policy | `partial` |
| `GET` | `/tickers/{ticker}/message-bus/overview` | Message Bus KPI | `partial` |
| `GET` | `/tickers/{ticker}/message-bus/messages` | Live Message Stream | `partial` |
| `GET` | `/tickers/{ticker}/message-bus/config` | Message Bus 配置状态 | `partial` |
| `PATCH` | `/tickers/{ticker}/message-bus/config/{source_id}` | 更新 source binding | `partial` |
| `DELETE` | `/tickers/{ticker}/message-bus/config/{source_id}` | 删除 source binding | `partial` |
| `GET` | `/tickers/{ticker}/runtime/overview` | Runtime Execution KPI | `partial` |
| `GET` | `/tickers/{ticker}/runtime/graph` | Runtime 链路图 | `proposed` |
| `GET` | `/tickers/{ticker}/runtime/nodes/{node_id}` | Runtime 节点详情 | `proposed` |
| `GET` | `/tickers/{ticker}/runtime/executions` | Runtime 处理记录列表 | `partial` |
| `GET` | `/tickers/{ticker}/runtime/executions/{execution_id}` | Runtime 单次处理详情 | `proposed` |
| `GET` | `/tickers/{ticker}/audit/revenue` | 收益审计 | `missing` |
| `POST` | `/tickers/{ticker}/audit/revenue/run` | 手动触发收益审计 | `proposed` |
| `GET` | `/tickers/{ticker}/audit/cost` | 成本审计 | `missing` |
| `GET` | `/tickers/{ticker}/audit/cost/details` | 成本审计明细 | `missing` |
| `GET` | `/events` | SSE runtime event stream | `proposed` |

## 3. 全局 Overview 状态

### 3.1 GET `/overview`

用途：Overview 页面顶部 KPI、全局健康状态、ticker 卡片摘要。  
前端页面/组件：`/overview`，KPI 卡片、启动 ticker 区域、ticker 状态卡片列表。  
实现状态：`partial`。`runtime_scheduler.overview()` 已有 ticker 状态，但容器状态、Dashboard API 状态、全局 Message Bus 统计、今日成本和收益仍缺聚合。

HTTP method：

```text
GET
```

Path：

```text
/overview
```

Query params：

| 参数 | 类型 | 必填 | 含义 |
| --- | --- | --- | --- |
| `date` | string | 否 | 统计日期，默认当前交易日，格式 `YYYY-MM-DD`。 |
| `tz` | string | 否 | 展示时区，默认 `America/New_York`。 |

Response schema：

```json
{
  "data": {
    "generated_at": "2026-06-30T12:00:00Z",
    "system": {
      "container_status": "normal",
      "dashboard_api_status": "normal",
      "message_bus_status": "degraded",
      "status_color": "yellow"
    },
    "kpis": {
      "running_ticker_count": 2,
      "today_message_count": 128,
      "today_dtc_count": 3,
      "today_token_cost_usd": null,
      "exception_count": 1
    },
    "tickers": []
  },
  "meta": {
    "request_id": "req_01",
    "generated_at": "2026-06-30T12:00:00Z"
  }
}
```

字段含义：

| 字段 | 含义 |
| --- | --- |
| `system.container_status` | dashboard 相关容器健康状态。 |
| `system.dashboard_api_status` | Dashboard API 自身健康状态。 |
| `system.message_bus_status` | Message Bus 总体状态。 |
| `kpis.running_ticker_count` | 当前 `running/degraded` ticker 数。 |
| `kpis.today_message_count` | 今日进入 Message Bus 的标准消息数。 |
| `kpis.today_dtc_count` | 今日 Direct Trade Candidate 数。 |
| `kpis.today_token_cost_usd` | 今日模型成本，当前可为空。 |
| `kpis.exception_count` | 今日异常数量。 |
| `tickers` | `TickerCard[]`，结构见 4.1。 |

状态枚举：

- `container_status/dashboard_api_status/message_bus_status`：`normal | degraded | blocked | unknown`
- `status_color`：`green | yellow | red | gray`

示例 JSON：

```json
{
  "data": {
    "generated_at": "2026-06-30T12:00:00Z",
    "system": {
      "container_status": "normal",
      "dashboard_api_status": "normal",
      "message_bus_status": "normal",
      "status_color": "green"
    },
    "kpis": {
      "running_ticker_count": 1,
      "today_message_count": 42,
      "today_dtc_count": 1,
      "today_token_cost_usd": null,
      "exception_count": 0
    },
    "tickers": [
      {
        "ticker": "MU",
        "status": "running",
        "status_label": "运行中",
        "health": "normal",
        "last_message_at": "2026-06-30T11:58:00Z",
        "last_worker_processed_at": "2026-06-30T11:59:10Z",
        "today_dtc_count": 1,
        "today_cost_usd": null
      }
    ]
  },
  "meta": {
    "request_id": "req_01",
    "generated_at": "2026-06-30T12:00:00Z"
  }
}
```

当前可能数据来源：

- `src/doxagent/runtime_scheduler/service.py::overview`
- `src/doxagent/runtime_scheduler/schema.py::DashboardOverview`
- `src/doxagent/monitoring/service.py::status_snapshot`
- `src/doxagent/persistent_runtime/service.py::runtime_observations`
- `docker-compose.yml` 中现有 `debug-viewer` healthcheck 可作为容器健康参考，但不覆盖未来 dashboard 容器。

## 4. Ticker 列表与运行状态

### 4.1 GET `/tickers`

用途：Overview ticker 卡片列表。  
前端页面/组件：`/overview` ticker 状态卡片。  
实现状态：`partial`。已有 `DashboardStateAPI.list_tickers()`，缺正式 HTTP、分页和今日聚合字段。

Query params：

| 参数 | 类型 | 必填 | 含义 |
| --- | --- | --- | --- |
| `status` | string | 否 | 运行状态筛选。 |
| `health` | string | 否 | 健康状态筛选。 |
| `limit` | int | 否 | 默认 50。 |
| `cursor` | string | 否 | 分页游标。 |
| `sort` | string | 否 | 默认 `ticker`。 |

Response schema：

```json
{
  "data": {
    "items": [
      {
        "ticker": "MU",
        "status": "running",
        "status_label": "运行中",
        "health": "normal",
        "session_phase": "formal_monitoring",
        "started_at": "2026-06-30T12:00:00Z",
        "updated_at": "2026-06-30T12:05:00Z",
        "last_message_at": "2026-06-30T12:04:00Z",
        "last_worker_processed_at": "2026-06-30T12:04:20Z",
        "today_dtc_count": 1,
        "today_cost_usd": null,
        "last_error": null
      }
    ],
    "page": {
      "limit": 50,
      "next_cursor": null,
      "has_more": false
    }
  }
}
```

字段含义：

| 字段 | 含义 |
| --- | --- |
| `ticker` | 股票代码，大写。 |
| `status` | 内部运行状态。 |
| `status_label` | 中文展示文案。 |
| `health` | 健康状态。 |
| `session_phase` | 当前美东交易时段。 |
| `last_message_at` | 最近标准消息或 event stream 时间。 |
| `last_worker_processed_at` | 最近 runtime execution 时间。 |
| `today_dtc_count` | 今日 DTC/trade intent 数。 |
| `today_cost_usd` | 今日成本，当前可能为空。 |

状态枚举：

- `status`：`initializing | running | paused | stopped | degraded | blocked`
- `health`：`normal | degraded | blocked | unknown`
- `session_phase`：`pre_market_digest | formal_monitoring | off_hours_low_frequency`

示例 JSON：见上方 schema。

当前可能数据来源：

- `src/doxagent/runtime_scheduler/schema.py::TickerRunState`
- `src/doxagent/runtime_scheduler/service.py::overview`
- `src/doxagent/runtime_scheduler/service.py::event_processing_status`
- `src/doxagent/persistent_runtime/repository.py::list_trading_records`

### 4.2 GET `/tickers/{ticker}`

用途：单 ticker 顶栏、页面状态同步、卡片点击后详情初始化。  
前端页面/组件：所有 `/ticker/:ticker/*` 页面。  
实现状态：`partial`。已有 `DashboardStateAPI.get_ticker()` 返回复合详情，缺 HTTP 和前端稳定裁剪。

Path params：

| 参数 | 类型 | 含义 |
| --- | --- | --- |
| `ticker` | string | 股票代码。 |

Response schema：

```json
{
  "data": {
    "ticker": "MU",
    "state": {
      "status": "running",
      "health": "normal",
      "session_phase": "formal_monitoring",
      "document_run_id": "run_123",
      "last_error": null
    },
    "document_status": {},
    "message_bus_status": {},
    "runtime_status": {},
    "audit_summary": {
      "today_dtc_count": 1,
      "today_revenue_audit_status": "not_started",
      "today_cost_audit_status": "missing"
    }
  }
}
```

字段含义：

| 字段 | 含义 |
| --- | --- |
| `state` | ticker 运行状态摘要。 |
| `document_status` | Document 1/2/3 可用性摘要。 |
| `message_bus_status` | Message Bus 配置和积压摘要。 |
| `runtime_status` | runtime execution 积压、执行、异常摘要。 |
| `audit_summary` | 收益和成本审计入口摘要。 |

当前可能数据来源：

- `src/doxagent/runtime_scheduler/service.py::detail`
- `src/doxagent/runtime_scheduler/schema.py::TickerRunDetail`

## 5. Ticker 启动、暂停、删除、重启

### 5.1 POST `/tickers`

用途：开启新标的监测。  
前端页面/组件：Overview 启动 ticker 表单。  
实现状态：`partial`。已有 `start_ticker`，缺 HTTP、鉴权、幂等错误语义和前端反馈包装。

Request body：

```json
{
  "ticker": "MU",
  "force_initialize": false,
  "reason": "手动启动监测"
}
```

字段含义：

| 字段 | 类型 | 必填 | 含义 |
| --- | --- | --- | --- |
| `ticker` | string | 是 | 股票代码。 |
| `force_initialize` | boolean | 否 | 是否强制重新初始化文档。默认 `false`。 |
| `reason` | string | 否 | 操作原因，写入审计。 |

Response schema：

```json
{
  "data": {
    "operation": "start",
    "status": "accepted",
    "ticker": "MU",
    "ticker_state": {
      "status": "running",
      "health": "normal"
    },
    "audit_id": "audit_abc"
  }
}
```

状态枚举：

- `operation`：`start`
- `status`：`accepted | already_running | blocked | failed`

示例 JSON：

```json
{
  "data": {
    "operation": "start",
    "status": "accepted",
    "ticker": "MU",
    "ticker_state": {
      "status": "running",
      "health": "normal"
    },
    "audit_id": "audit_01"
  }
}
```

当前可能数据来源或实现位置：

- `src/doxagent/runtime_scheduler/api.py::DashboardStateAPI.start_ticker`
- `src/doxagent/runtime_scheduler/service.py::start_ticker`
- `src/doxagent/runtime_scheduler/repository.py::append_audit_event`

### 5.2 POST `/tickers/{ticker}/pause`

用途：暂停 ticker 调度。  
前端页面/组件：Overview ticker 卡片操作。  
实现状态：`partial`。已有 `pause_ticker`，缺正式 HTTP 和鉴权。

Request body：

```json
{
  "reason": "人工暂停"
}
```

Response schema：

```json
{
  "data": {
    "operation": "pause",
    "status": "accepted",
    "ticker": "MU",
    "ticker_state": {
      "status": "paused",
      "health": "normal"
    }
  }
}
```

字段含义：

| 字段 | 含义 |
| --- | --- |
| `reason` | 暂停原因，写入 scheduler audit。 |
| `ticker_state.status` | 暂停后应为 `paused`。 |

当前实现位置：

- `src/doxagent/runtime_scheduler/api.py::pause_ticker`
- `src/doxagent/runtime_scheduler/service.py::pause_ticker`

### 5.3 DELETE `/tickers/{ticker}`

用途：删除 ticker 监测任务。  
前端页面/组件：Overview ticker 卡片删除按钮，必须二次确认。  
实现状态：`partial`。当前 `runtime_scheduler.stop_ticker()` 可停止并禁用 bindings，`monitoring.delete_ticker_config()` 可删除 Message Bus bindings，但没有统一删除 ticker state 的 Dashboard API。

Query params：

| 参数 | 类型 | 必填 | 含义 |
| --- | --- | --- | --- |
| `delete_history` | boolean | 否 | 第一阶段默认 `false`，不删除历史审计和 runtime 记录。 |

Request body：

```json
{
  "reason": "不再监测该标的"
}
```

Response schema：

```json
{
  "data": {
    "operation": "delete",
    "status": "accepted",
    "ticker": "MU",
    "disabled_binding_count": 3,
    "deleted_binding_count": 3,
    "history_deleted": false
  }
}
```

字段含义：

| 字段 | 含义 |
| --- | --- |
| `disabled_binding_count` | 被禁用的 source binding 数。 |
| `deleted_binding_count` | 被删除的 Message Bus binding 数。 |
| `history_deleted` | 是否删除历史记录。第一阶段应为 `false`。 |

当前实现位置：

- 停止：`src/doxagent/runtime_scheduler/service.py::stop_ticker`
- 删除 Message Bus 配置：`src/doxagent/monitoring/service.py::delete_ticker_config`
- 现有 viewer：`src/doxagent/monitoring/viewer.py::delete_ticker`

### 5.4 POST `/tickers/{ticker}/restart`

用途：重启 ticker 监测。  
前端页面/组件：Overview ticker 卡片重启按钮。  
实现状态：`proposed`。可组合 `stop_ticker(keep bindings?)` 与 `start_ticker(force_initialize?)`，但当前没有原子重启操作。

Request body：

```json
{
  "force_initialize": false,
  "keep_bindings": true,
  "reason": "人工重启"
}
```

Response schema：

```json
{
  "data": {
    "operation": "restart",
    "status": "accepted",
    "ticker": "MU",
    "ticker_state": {
      "status": "running",
      "health": "normal"
    }
  }
}
```

字段含义：

| 字段 | 含义 |
| --- | --- |
| `force_initialize` | 是否重新初始化 Document 1/2/3。 |
| `keep_bindings` | 是否保留已有 Message Bus bindings。 |

当前可能数据来源：

- `src/doxagent/runtime_scheduler/service.py::stop_ticker`
- `src/doxagent/runtime_scheduler/service.py::start_ticker`

## 6. Document 1/2/3 当前版本与历史版本

### 6.1 GET `/tickers/{ticker}/documents/current`

用途：投研资料页与执行策略页加载当前 Document 1/2/3。  
前端页面/组件：`/ticker/:ticker/research`、`/ticker/:ticker/strategy`。  
实现状态：`partial`。当前可通过 `WorkflowDocumentProvider.latest()` 读取最新可用文档和 status，但缺前端卡片化 schema、中文 label、历史版本选择。

Query params：

| 参数 | 类型 | 必填 | 含义 |
| --- | --- | --- | --- |
| `types` | string | 否 | 逗号分隔：`document1,document2,document3`。默认全部。 |
| `include_raw` | boolean | 否 | 是否包含内部 raw document，默认 `false`。仅 dev 详情可开启。 |

Response schema：

```json
{
  "data": {
    "ticker": "MU",
    "document_run_id": "run_123",
    "documents": [
      {
        "document_type": "document1",
        "document_type_label": "Document 1：Global Research",
        "document_id": "doc_global_research_mu",
        "generated_at": "2026-06-30T10:00:00Z",
        "updated_at": "2026-06-30T10:10:00Z",
        "version_status": "current",
        "availability": "available",
        "cards": [
          {
            "card_id": "fundamental_report",
            "title": "基本面研究",
            "updated_at": "2026-06-30T10:10:00Z",
            "summary": "收入、毛利率与资本开支摘要。",
            "fields": [
              {
                "key": "text",
                "label": "详细内容",
                "value": "长文本内容"
              }
            ]
          }
        ]
      }
    ]
  }
}
```

字段含义：

| 字段 | 含义 |
| --- | --- |
| `document_type` | 前端稳定类型：`document1/document2/document3`。 |
| `document_id` | 当前 document UUID/id。 |
| `version_status` | `current | historical`。 |
| `availability` | 可用性。 |
| `cards` | 前端可直接渲染的卡片数组。 |
| `fields` | 展开后的 label/value 字段。 |

状态枚举：

- `document_type`：`document1 | document2 | document3`
- `version_status`：`current | historical`
- `availability`：`available | missing | stale | invalid`

示例 JSON：见上方 schema。

当前可能数据来源：

- `src/doxagent/runtime_scheduler/documents.py::WorkflowDocumentProvider.latest`
- `src/doxagent/models/documents.py::GlobalResearchDocument`
- `src/doxagent/models/documents.py::ExpectationUnitDocument`
- `src/doxagent/models/documents.py::KnownEventsDocument`
- `src/doxagent/models/documents.py::MonitoringPolicyDocument`
- `src/doxagent/debug_viewer/query.py::_global_research_view`
- `src/doxagent/debug_viewer/query.py::_expectation_view`

### 6.2 GET `/tickers/{ticker}/documents/{document_type}/versions`

用途：历史记录侧边栏。  
前端页面/组件：投研资料页、执行策略页左侧历史版本侧边栏。  
实现状态：`proposed`。当前 `WorkflowDocumentProvider.latest()` 会看最近 runs，但没有 Dashboard 级版本列表 schema。

Path params：

| 参数 | 类型 | 含义 |
| --- | --- | --- |
| `document_type` | string | `document1 | document2 | document3`。 |

Query params：

| 参数 | 类型 | 必填 | 含义 |
| --- | --- | --- | --- |
| `limit` | int | 否 | 默认 50。 |
| `cursor` | string | 否 | 分页游标。 |

Response schema：

```json
{
  "data": {
    "items": [
      {
        "version_id": "doc_global_research_mu",
        "document_id": "doc_global_research_mu",
        "document_run_id": "run_123",
        "generated_at": "2026-06-30T10:00:00Z",
        "updated_at": "2026-06-30T10:10:00Z",
        "version_status": "current",
        "availability": "available"
      }
    ],
    "page": {
      "limit": 50,
      "next_cursor": null,
      "has_more": false
    }
  }
}
```

当前可能数据来源：

- `BlackboardService.list_runs_by_ticker`
- `src/doxagent/runtime_scheduler/documents.py::_bundle_from_run`
- `src/doxagent/debug_viewer/query.py::list_runs`

### 6.3 GET `/tickers/{ticker}/documents/{document_type}/versions/{version_id}`

用途：查看历史 Document 版本内容。  
前端页面/组件：投研资料页、执行策略页历史版本内容切换。  
实现状态：`proposed`。底层 Blackboard 有历史 run/document，但缺稳定版本读取接口。

Response schema：

```json
{
  "data": {
    "ticker": "MU",
    "document_type": "document2",
    "document_id": "doc_expectation_mu_001",
    "version_id": "doc_expectation_mu_001",
    "document_run_id": "run_123",
    "version_status": "historical",
    "availability": "available",
    "cards": []
  }
}
```

字段含义同 6.1。

当前可能数据来源：

- `src/doxagent/blackboard/service.py`
- `src/doxagent/runtime_scheduler/documents.py`
- `src/doxagent/debug_viewer/query.py::load_bundle`

## 7. Known Events

### 7.1 GET `/tickers/{ticker}/known-events`

用途：执行策略页 Known Events 列表与筛选。  
前端页面/组件：`/ticker/:ticker/strategy` Known Events 小卡片列表。  
实现状态：`partial`。Document 3 Known Events 和 runtime Known Events patch log 已有，但缺 Dashboard 合并视图。

Query params：

| 参数 | 类型 | 必填 | 含义 |
| --- | --- | --- | --- |
| `expectation_id` | string | 否 | 按 expectation unit 筛选。 |
| `q` | string | 否 | 关键词搜索。 |
| `limit` | int | 否 | 默认 50。 |
| `cursor` | string | 否 | 分页游标。 |

Response schema：

```json
{
  "data": {
    "items": [
      {
        "event_id": "KE_001",
        "title": "HBM 订单扩张",
        "description": "客户订单继续增长。",
        "core_fact": "客户订单继续增长。",
        "expectation_id": "EU_001",
        "event_time": null,
        "event_window": "2026Q2",
        "last_updated_at": "2026-06-30T10:00:00Z",
        "source": {
          "kind": "document3",
          "document_id": "doc_known_events_mu"
        },
        "runtime_patch": null
      }
    ],
    "page": {
      "limit": 50,
      "next_cursor": null,
      "has_more": false
    }
  }
}
```

字段含义：

| 字段 | 含义 |
| --- | --- |
| `event_id` | Known Event 稳定 ID。 |
| `title` | 前端标题，可由 `core_fact` 截断生成。 |
| `description/core_fact` | 事件正文。 |
| `expectation_id` | 关联 expectation unit。 |
| `source.kind` | `document3 | runtime_patch`。 |
| `runtime_patch` | 若由 O3 更新，展示 patch 审计摘要。 |

当前可能数据来源：

- `src/doxagent/models/documents.py::KnownEventsDocument`
- `src/doxagent/persistent_runtime/repository.py::list_known_events`
- `src/doxagent/persistent_runtime/repository.py::list_known_events_patch_logs`

## 8. Monitoring Execution Policy

### 8.1 GET `/tickers/{ticker}/policies`

用途：执行策略页 policy 列表。  
前端页面/组件：`/ticker/:ticker/strategy` Monitoring Execution Policy 卡片。  
实现状态：`partial`。Document 3 policy 模型已存在，但缺 Dashboard 聚合/筛选 endpoint。

Query params：

| 参数 | 类型 | 必填 | 含义 |
| --- | --- | --- | --- |
| `action_type` | string | 否 | `DTC | EBA | NULL | Irrelevant`。 |
| `expectation_id` | string | 否 | 按 expectation unit 筛选。 |
| `q` | string | 否 | 关键词搜索。 |
| `limit` | int | 否 | 默认 50。 |
| `cursor` | string | 否 | 分页游标。 |

Response schema：

```json
{
  "data": {
    "items": [
      {
        "policy_id": "POLICY_001",
        "action_type": "DTC",
        "action_type_label": "Direct Trade Candidate",
        "title": "重大订单确认",
        "trigger_condition": "确认新增重大客户订单。",
        "expectation_id": "EU_001",
        "last_updated_at": "2026-06-30T10:00:00Z",
        "detail": {
          "scope": {},
          "trigger": {},
          "confirmation": {},
          "risk_guard": {},
          "action": {}
        }
      }
    ],
    "page": {
      "limit": 50,
      "next_cursor": null,
      "has_more": false
    }
  }
}
```

字段含义：

| 字段 | 含义 |
| --- | --- |
| `policy_id` | Policy 稳定 ID。 |
| `action_type` | 前端动作类型。 |
| `title` | 策略标题。 |
| `trigger_condition` | 完整触发条件摘要。 |
| `detail` | 展开后展示的结构化字段。 |

状态枚举：

- `action_type`：`DTC | EBA | NULL | Irrelevant`
- 内部映射：`direct_trade` 可映射为 `DTC`，`push_to_agent/escalate` 可映射为 `EBA`，未命中不一定有静态 policy，`NULL/Irrelevant` 更偏 runtime 判定结果。

当前可能数据来源：

- `src/doxagent/models/documents.py::MonitoringPolicyDocument`
- `src/doxagent/models/documents.py::MonitoringPolicyRule`
- `src/doxagent/runtime_scheduler/service.py::_runtime_context`

## 9. Message Bus 消息流与配置状态

### 9.1 GET `/tickers/{ticker}/message-bus/overview`

用途：消息总线页面顶部 KPI。  
前端页面/组件：`/ticker/:ticker/message-bus` KPI 区。  
实现状态：`partial`。Message Bus status snapshot 已有，启动时长和正文补全成功率需新增聚合。

Query params：

| 参数 | 类型 | 必填 | 含义 |
| --- | --- | --- | --- |
| `date` | string | 否 | 默认当前交易日。 |

Response schema：

```json
{
  "data": {
    "ticker": "MU",
    "uptime_seconds": 3600,
    "today_raw_message_count": 80,
    "today_event_count": 42,
    "media_enrichment_success_rate": 0.72,
    "healthy_channel_count": 5,
    "total_channel_count": 6,
    "last_error_message": null
  }
}
```

字段含义：

| 字段 | 含义 |
| --- | --- |
| `uptime_seconds` | 该 ticker runtime 或 viewer/service 启动时长。 |
| `today_raw_message_count` | 今日 raw message 数。 |
| `today_event_count` | 今日 event stream 数。 |
| `media_enrichment_success_rate` | 正文补全成功率。 |
| `healthy_channel_count` | 最近正常 source channel 数。 |

当前可能数据来源：

- `src/doxagent/monitoring/service.py::status_snapshot`
- `src/doxagent/monitoring/repository.py::recent_raw_messages`
- `src/doxagent/monitoring/repository.py::recent_events`
- `src/doxagent/monitoring/repository.py::list_media_enrichment_records`
- `src/doxagent/runtime_scheduler/schema.py::TickerRunState.started_at`

### 9.2 GET `/tickers/{ticker}/message-bus/messages`

用途：Live Message Stream 列表。  
前端页面/组件：`/ticker/:ticker/message-bus` 消息卡片、筛选、搜索、展开详情。  
实现状态：`partial`。`recent_standard_messages` 已有，处理状态需联动 runtime observations。

Query params：

| 参数 | 类型 | 必填 | 含义 |
| --- | --- | --- | --- |
| `source_id` | string | 否 | 来源筛选。 |
| `processing_status` | string | 否 | 处理状态筛选。 |
| `q` | string | 否 | 标题/正文关键词。 |
| `from` | string | 否 | 起始时间。 |
| `to` | string | 否 | 结束时间。 |
| `limit` | int | 否 | 默认 50。 |
| `cursor` | string | 否 | 分页游标。 |
| `sort` | string | 否 | 默认 `-collected_at`。 |

Response schema：

```json
{
  "data": {
    "items": [
      {
        "message_id": "std_001",
        "raw_message_id": "raw_001",
        "ticker": "MU",
        "source_id": "stocktwits_messages",
        "source_label": "Stocktwits",
        "source_type": "social",
        "collected_at": "2026-06-30T12:00:00Z",
        "published_at": "2026-06-30T11:59:00Z",
        "title": "MU discussion",
        "summary": "社媒摘要",
        "body": "完整正文",
        "url": "https://example.com/item",
        "processing_status": "routed_to_archive",
        "runtime_execution_id": "pre_001"
      }
    ],
    "page": {
      "limit": 50,
      "next_cursor": null,
      "has_more": false
    }
  }
}
```

字段含义：

| 字段 | 含义 |
| --- | --- |
| `message_id` | 标准消息 ID。 |
| `source_label` | 中文/友好展示名。 |
| `summary` | 前端摘要，可由 body 截断生成。 |
| `processing_status` | 当前 runtime 处理状态。 |
| `runtime_execution_id` | 若已进入 runtime，关联 execution。 |

当前可能数据来源：

- `src/doxagent/monitoring/schema.py::StandardMessage`
- `src/doxagent/monitoring/service.py::recent_messages`
- `src/doxagent/persistent_runtime/service.py::runtime_observations`

### 9.3 GET `/tickers/{ticker}/message-bus/config`

用途：Config 页面展示当前消息源配置和状态。  
前端页面/组件：消息总线右上角齿轮 Config 视图。  
实现状态：`partial`。`MonitoringBusService.get_ticker_config()` 已有较完整结构。

Response schema：

```json
{
  "data": {
    "ticker": "MU",
    "sources": [
      {
        "source_id": "stocktwits_messages",
        "display_name": "Stocktwits Messages API",
        "source_type": "social",
        "interface_type": "by_ticker",
        "enabled": true,
        "poll_interval_seconds": 300,
        "binding": {},
        "poll_state": {
          "status": "succeeded",
          "last_success_at": "2026-06-30T12:00:00Z",
          "last_error_message": null
        },
        "user_only_fields": [
          "poll_interval_seconds"
        ],
        "agent_mutable_fields": [
          "enabled"
        ]
      }
    ],
    "missing_source_ids": []
  }
}
```

当前实现位置：

- `src/doxagent/monitoring/service.py::get_ticker_config`
- `src/doxagent/monitoring/schema.py::TickerSourceBinding`
- `src/doxagent/monitoring/schema.py::PollState`

### 9.4 PATCH `/tickers/{ticker}/message-bus/config/{source_id}`

用途：更新单个 ticker/source binding。  
前端页面/组件：Config 视图配置表单。  
实现状态：`partial`。底层 service 已支持配置 binding，但 Dashboard 权限、前端 schema 和危险字段限制需新增。

Request body：

```json
{
  "enabled": true,
  "mode": "merge",
  "search_terms": [
    "MU earnings"
  ],
  "reason": "补充财报搜索词"
}
```

Response schema：

```json
{
  "data": {
    "ticker": "MU",
    "source_id": "tikhub_x_search",
    "binding": {},
    "config": {}
  }
}
```

字段规则：

- `poll_interval_seconds`、全局 source enable/disable、Stocktwits durable cadence 等 user-only 字段需要更高权限，不能由 agent 自动修改。
- 参数限制复用 `SOURCE_PARAMETER_SCHEMAS`。

当前实现位置：

- `src/doxagent/monitoring/service.py::configure_ticker_source`
- `src/doxagent/monitoring/schema.py::validate_parameters_for_source`

### 9.5 DELETE `/tickers/{ticker}/message-bus/config/{source_id}`

用途：删除单个 source binding。  
前端页面/组件：Config 视图 source 行删除。  
实现状态：`partial`。底层已有删除 binding，缺正式 Dashboard route。

Response schema：

```json
{
  "data": {
    "ticker": "MU",
    "source_id": "newswire_rss",
    "removed": true
  }
}
```

当前实现位置：

- `src/doxagent/monitoring/service.py::delete_ticker_source`
- `src/doxagent/monitoring/viewer.py::unbind`

## 10. Runtime Execution 节点状态、链路图、处理记录

### 10.1 GET `/tickers/{ticker}/runtime/overview`

用途：运行状态页顶部 KPI。  
前端页面/组件：`/ticker/:ticker/runtime` KPI 区。  
实现状态：`partial`。runtime observations 已有，节点级日内聚合和平均延迟需新增。

Query params：

| 参数 | 类型 | 必填 | 含义 |
| --- | --- | --- | --- |
| `date` | string | 否 | 默认当前交易日。 |

Response schema：

```json
{
  "data": {
    "ticker": "MU",
    "queue_message_count": 8,
    "w1_today_count": 40,
    "w1_avg_latency_ms": 1200,
    "w2_today_count": 40,
    "w2_avg_latency_ms": 1300,
    "o3_today_count": 3,
    "o3_avg_latency_ms": 48000,
    "dtc_today_count": 1,
    "eba_today_count": 2,
    "failed_task_count": 0,
    "avg_processing_latency_ms": 2300
  }
}
```

当前可能数据来源：

- `src/doxagent/runtime_scheduler/service.py::event_processing_status`
- `src/doxagent/persistent_runtime/service.py::runtime_observations`
- `src/doxagent/persistent_runtime/schema.py::RuntimeNodeTrace`

### 10.2 GET `/tickers/{ticker}/runtime/graph`

用途：运行链路图节点和边。  
前端页面/组件：Runtime Execution 链路图。  
实现状态：`proposed`。底层有 observations 和 route，但缺图形聚合 schema。

Response schema：

```json
{
  "data": {
    "nodes": [
      {
        "node_id": "message_bus",
        "label": "Message Bus",
        "status": "normal",
        "in_count": 42,
        "out_count": 42,
        "failed_count": 0
      },
      {
        "node_id": "w1",
        "label": "W1 新旧判定",
        "status": "normal",
        "in_count": 42,
        "out_count": 42,
        "failed_count": 0
      }
    ],
    "edges": [
      {
        "edge_id": "message_bus_to_w1",
        "from": "message_bus",
        "to": "w1",
        "label": "消息进入执行层",
        "count": 42
      }
    ]
  }
}
```

节点枚举：

```text
message_bus | w1 | w2 | o3 | trading_records | o1_a2 | ignored | exception_queue
```

当前可能数据来源：

- `RuntimeExecutionObservation.final_route`
- `RuntimeExecutionObservation.node_durations_ms`
- `RuntimeExecutionRecord.message_statuses`

### 10.3 GET `/tickers/{ticker}/runtime/nodes/{node_id}`

用途：点击节点后的右侧详情面板。  
前端页面/组件：Runtime Execution 节点详情 drawer。  
实现状态：`proposed`。底层记录存在，缺节点聚合视图。

Query params：

| 参数 | 类型 | 必填 | 含义 |
| --- | --- | --- | --- |
| `limit` | int | 否 | 最近处理记录数量。 |
| `cursor` | string | 否 | 分页游标。 |

Response schema：

```json
{
  "data": {
    "node": {
      "node_id": "w1",
      "label": "W1 新旧判定",
      "status": "normal",
      "last_processed_at": "2026-06-30T12:00:00Z",
      "today_count": 42,
      "today_failed_count": 0,
      "avg_latency_ms": 1200,
      "last_error": null
    },
    "recent_records": [
      {
        "execution_id": "pre_001",
        "source_message_id": "std_001",
        "status": "completed",
        "input_summary": "消息标题",
        "output_summary": "is_new=true, confidence=high",
        "duration_ms": 1200,
        "created_at": "2026-06-30T12:00:00Z"
      }
    ]
  }
}
```

当前可能数据来源：

- `src/doxagent/persistent_runtime/repository.py::list_executions`
- `src/doxagent/persistent_runtime/schema.py::RuntimeExecutionRecord`
- `src/doxagent/persistent_runtime/schema.py::RuntimeNodeTrace`

### 10.4 GET `/tickers/{ticker}/runtime/executions`

用途：最近处理记录列表。  
前端页面/组件：Runtime 节点详情、调试列表。  
实现状态：`partial`。底层 `recent_executions()` 已有，缺 HTTP、分页和前端摘要字段。

Query params：

| 参数 | 类型 | 必填 | 含义 |
| --- | --- | --- | --- |
| `route` | string | 否 | 最终路由筛选。 |
| `status` | string | 否 | 执行状态筛选。 |
| `source_type` | string | 否 | `media | social`。 |
| `limit` | int | 否 | 默认 50。 |
| `cursor` | string | 否 | 分页游标。 |

Response schema：

```json
{
  "data": {
    "items": [
      {
        "execution_id": "pre_001",
        "source_message_id": "std_001",
        "ticker": "MU",
        "source_type": "media",
        "final_route": "trading_record",
        "status": "completed",
        "message_statuses": [
          "received",
          "workers_completed",
          "routed_to_trading_records"
        ],
        "node_durations_ms": {
          "W1": 1200,
          "W2": 1300
        },
        "exception_types": [],
        "created_at": "2026-06-30T12:00:00Z"
      }
    ],
    "page": {
      "limit": 50,
      "next_cursor": null,
      "has_more": false
    }
  }
}
```

当前实现位置：

- `src/doxagent/persistent_runtime/service.py::recent_executions`
- `src/doxagent/persistent_runtime/service.py::runtime_observations`

### 10.5 GET `/tickers/{ticker}/runtime/executions/{execution_id}`

用途：单条处理记录详情。  
前端页面/组件：Runtime 记录详情展开。  
实现状态：`proposed`。底层按 source message id 幂等读取存在，但没有按 execution_id 的 Dashboard API。

Response schema：

```json
{
  "data": {
    "execution_id": "pre_001",
    "source_message": {},
    "route_decision": {},
    "w1_result": {},
    "w2_result": {},
    "a2_result": null,
    "o3_result": null,
    "node_traces": [],
    "exceptions": [],
    "created_at": "2026-06-30T12:00:00Z"
  }
}
```

当前可能数据来源：

- `src/doxagent/persistent_runtime/repository.py::list_executions`
- `src/doxagent/persistent_runtime/repository.py::execution_for_source`

## 11. 收益审计

### 11.1 GET `/tickers/{ticker}/audit/revenue`

用途：收益审计概览、趋势图、交易意图列表。  
前端页面/组件：`/ticker/:ticker/audit` 收益审计 tab。  
实现状态：`missing`。当前只有 trade intent/TradingRecord，没有审计价格、滑点、卖出价、收益计算和任务状态。

Query params：

| 参数 | 类型 | 必填 | 含义 |
| --- | --- | --- | --- |
| `date` | string | 否 | 默认当前交易日。 |
| `period` | string | 否 | `today | 7d | 30d`。 |

Response schema：

```json
{
  "data": {
    "ticker": "MU",
    "audit_date": "2026-06-30",
    "status": "not_started",
    "exit_rule": "close_minus_10min_full_exit",
    "kpis": {
      "today_trade_intent_count": 1,
      "audited_trade_count": 0,
      "today_pnl_usd": null,
      "today_return_pct": null,
      "win_rate": null
    },
    "trend": [
      {
        "date": "2026-06-30",
        "pnl_usd": null,
        "trade_intent_count": 1
      }
    ],
    "trade_intents": [
      {
        "record_id": "trd_001",
        "time": "2026-06-30T12:00:00Z",
        "ticker": "MU",
        "trigger_message_id": "std_001",
        "trigger_policy_id": "POLICY_001",
        "action": "long",
        "theoretical_entry_price": null,
        "estimated_entry_price": null,
        "exit_price": null,
        "slippage_pct": null,
        "pnl_usd": null,
        "status": "pending_audit"
      }
    ]
  }
}
```

字段含义：

| 字段 | 含义 |
| --- | --- |
| `status` | 审计任务状态。 |
| `exit_rule` | 当前收益审计退出策略。 |
| `theoretical_entry_price` | 交易意图生成时理论买入价。 |
| `estimated_entry_price` | 考虑滑点后的估算买入价。 |
| `exit_price` | 收盘前 10 分钟或配置规则对应卖出价。 |
| `pnl_usd` | 估算收益。 |

状态枚举：

```text
not_started | calculating | completed | failed
pending_audit | audited | skipped | failed
```

当前可能数据来源：

- 已有 trade intent：`src/doxagent/persistent_runtime/schema.py::TradingRecord`
- 价格工具候选：`src/doxagent/agents/market_trace/providers.py`、`src/doxagent/tools/providers/yfinance.py`、`src/doxagent/tools/providers/finnhub.py`
- 需新增：收益审计 service、持久化表、交易日调度任务。

### 11.2 POST `/tickers/{ticker}/audit/revenue/run`

用途：人工触发某日收益审计。  
前端页面/组件：收益审计手动刷新/重跑按钮。  
实现状态：`proposed`。

Request body：

```json
{
  "date": "2026-06-30",
  "force": false,
  "reason": "手动补跑"
}
```

Response schema：

```json
{
  "data": {
    "audit_run_id": "rev_audit_001",
    "ticker": "MU",
    "date": "2026-06-30",
    "status": "calculating"
  }
}
```

当前可能数据来源：无直接实现，需要新增。

## 12. 成本审计

### 12.1 GET `/tickers/{ticker}/audit/cost`

用途：成本审计 KPI、趋势图、占比图和明细入口。  
前端页面/组件：`/ticker/:ticker/audit` 成本审计 tab。  
实现状态：`missing`。当前有模型调用 audit/usage 痕迹，但缺统一成本聚合。

Query params：

| 参数 | 类型 | 必填 | 含义 |
| --- | --- | --- | --- |
| `date` | string | 否 | 默认当前日期。 |
| `period` | string | 否 | `today | 7d | 30d`。 |
| `group_by` | string | 否 | `node | model | ticker`。 |

Response schema：

```json
{
  "data": {
    "ticker": "MU",
    "period": "today",
    "status": "missing",
    "kpis": {
      "today_input_tokens": null,
      "today_output_tokens": null,
      "today_total_tokens": null,
      "today_total_cost_usd": null,
      "highest_cost_node": null,
      "retry_cost_usd": null
    },
    "trend": [],
    "breakdown": {
      "by_node": [],
      "by_model": []
    }
  }
}
```

字段含义：

| 字段 | 含义 |
| --- | --- |
| `today_input_tokens` | 今日 input tokens。 |
| `today_output_tokens` | 今日 output tokens。 |
| `today_total_cost_usd` | 今日估算总成本。 |
| `highest_cost_node` | 成本最高节点。 |
| `retry_cost_usd` | 异常重试成本。 |

状态枚举：

```text
missing | partial | completed | failed
```

当前可能数据来源：

- `src/doxagent/gateway/schema.py::ModelUsage`
- `src/doxagent/gateway/schema.py::ModelAuditSummary`
- `src/doxagent/agents/runtime/runner.py` 中 `model_audit`
- `src/doxagent/agents/runtime/react.py` 中 `model_audits`
- `src/doxagent/debug_viewer/query.py::build_agent_metrics_view`
- 需新增：模型价格表、usage 明细持久化、成本聚合 service。

### 12.2 GET `/tickers/{ticker}/audit/cost/details`

用途：成本明细表。  
前端页面/组件：成本审计明细表、筛选。  
实现状态：`missing`。

Query params：

| 参数 | 类型 | 必填 | 含义 |
| --- | --- | --- | --- |
| `node` | string | 否 | W1/W2/O3/O1 等节点。 |
| `model` | string | 否 | 模型名。 |
| `status` | string | 否 | `succeeded | failed | retried`。 |
| `from` | string | 否 | 起始时间。 |
| `to` | string | 否 | 结束时间。 |
| `limit` | int | 否 | 默认 50。 |
| `cursor` | string | 否 | 分页游标。 |

Response schema：

```json
{
  "data": {
    "items": [
      {
        "cost_record_id": "cost_001",
        "time": "2026-06-30T12:00:00Z",
        "ticker": "MU",
        "node": "W1",
        "model": "qwen-plus",
        "input_tokens": 1200,
        "output_tokens": 200,
        "total_tokens": 1400,
        "cost_usd": 0.0014,
        "is_retry": false,
        "status": "succeeded",
        "source_ref": {
          "execution_id": "pre_001"
        }
      }
    ],
    "page": {
      "limit": 50,
      "next_cursor": null,
      "has_more": false
    }
  }
}
```

当前可能数据来源：同 12.1，但需要新增持久化和聚合。

## 13. SSE Runtime Event Stream

### 13.1 GET `/events`

用途：SSE 实时追加关键事件。  
前端页面/组件：Overview ticker 状态、Message Bus Live Stream、Runtime Execution、收益/成本审计状态。  
实现状态：`proposed`。当前项目没有 SSE；Message Bus event stream 是持久化表，不是 HTTP SSE。

HTTP method：

```text
GET
```

Path：

```text
/events
```

Headers：

```http
Accept: text/event-stream
Authorization: Bearer <supabase_jwt>
```

Query params：

| 参数 | 类型 | 必填 | 含义 |
| --- | --- | --- | --- |
| `ticker` | string | 否 | 单 ticker 过滤。 |
| `event_types` | string | 否 | 逗号分隔事件类型。 |
| `last_event_id` | string | 否 | 断线恢复游标。也可使用 SSE `Last-Event-ID` header。 |

SSE event 格式：

```text
id: evt_1001
event: runtime.execution.updated
data: {"ticker":"MU","execution_id":"pre_001","status":"completed"}

```

Event data schema：

```json
{
  "event_id": "evt_1001",
  "event_type": "runtime.execution.updated",
  "ticker": "MU",
  "occurred_at": "2026-06-30T12:00:00Z",
  "payload": {
    "execution_id": "pre_001",
    "status": "completed"
  }
}
```

事件枚举：

| event_type | 用途 |
| --- | --- |
| `message_bus.message.created` | 新标准消息进入 Message Bus。 |
| `message_bus.poll.failed` | 来源轮询失败。 |
| `runtime.execution.started` | Runtime 处理开始。 |
| `runtime.execution.updated` | W1/W2/A2/O3 或 route 状态变化。 |
| `runtime.execution.failed` | Runtime 处理异常。 |
| `trade_intent.created` | 交易意图记录生成。 |
| `known_event.updated` | Known Events 被运行时更新。 |
| `ticker.state.changed` | ticker 状态变化。 |
| `audit.revenue.status_changed` | 收益审计状态变化。 |
| `audit.cost.status_changed` | 成本审计状态变化。 |

当前可能数据来源：

- `src/doxagent/monitoring/schema.py::EventStreamItem`
- `src/doxagent/persistent_runtime/schema.py::RuntimeExecutionRecord`
- `src/doxagent/runtime_scheduler/schema.py::RuntimeAuditEvent`
- 需新增：SSE broker/streamer、断线恢复、事件转换层。

## 14. 当前实现缺口与后续开发建议

### 14.1 基础框架与鉴权

| 页面/API 模块 | 缺口 | 建议 |
| --- | --- | --- |
| 全部 | 无正式 FastAPI Dashboard 服务 | 新增 `src/doxagent/dashboard_api/`，挂载 `/api/dashboard/v1`。 |
| 全部 | 无 Supabase dev 鉴权中间件 | 接入 DoxAtlas 同源/跨域 Supabase session 校验，后端校验 dev role。 |
| 全部 | 无统一错误响应、request_id、分页游标 | 增加 API middleware 和 response helper。 |
| 全部 | 无 SSE | 新增事件转换层，把 Message Bus event、runtime record、scheduler audit 转成 SSE。 |
| 部署 | `docker-compose.yml` 无 `doxagent-dashboard` 服务 | 增加独立 dashboard service，保留 `debug-viewer` 和 `monitoring-poller`。 |

### 14.2 Overview 与 ticker 控制

| API | 缺口 | 建议 |
| --- | --- | --- |
| `GET /overview` | 容器状态、Dashboard API 状态、今日消息数、今日 DTC、成本缺聚合 | 复用 `runtime_scheduler.overview()`，新增 `OverviewStateAssembler`。 |
| `GET /tickers` | 缺分页、筛选、今日卡片字段 | 在 scheduler state 上叠加 monitoring/runtime 日内聚合。 |
| `POST /tickers` | 无 HTTP route/auth | 直接挂载 `DashboardStateAPI.start_ticker()`。 |
| `DELETE /tickers/{ticker}` | stop 与删除 Message Bus config 分散 | 新增原子 delete operation，默认不删历史。 |
| `POST /tickers/{ticker}/restart` | 无原子重启 | 包装 stop/start，明确 `keep_bindings` 与 `force_initialize` 语义。 |

### 14.3 Document 与策略页面

| API | 缺口 | 建议 |
| --- | --- | --- |
| Document current | 当前只有可用性和 raw model，缺前端卡片 schema | 新增 `DocumentViewAssembler`，输出中文 label/card/field。 |
| Document versions | 无稳定历史版本 API | 基于 Blackboard runs 和 document ids 建版本索引。 |
| Known Events | Document3 与 runtime patch 未合并 | 合并 `KnownEventsDocument.events` 与 `persistent_known_events`。 |
| Policy | 缺 DTC/EBA 前端动作类型映射 | 在 API 层将 `direct_trade/push_to_agent` 映射为 `DTC/EBA`。 |

### 14.4 Message Bus

| API | 缺口 | 建议 |
| --- | --- | --- |
| overview | 今日 raw/event 统计、正文补全成功率缺聚合 | 在 `SQLiteMonitoringRepository` 增加 date-range count 查询。 |
| messages | 缺 keyword/search/cursor，缺 runtime processing status join | 对 standard messages 做分页查询，并按 `source_message_id` join observations。 |
| config | 底层已有，但需前端安全字段 | 复用 `get_ticker_config()`，过滤 secret 与 user-only 操作。 |
| config mutation | viewer 可做，但无正式权限 | 新增 dev-only mutation endpoint，保留 poll cadence 的 user-only 限制。 |

### 14.5 Runtime Execution

| API | 缺口 | 建议 |
| --- | --- | --- |
| runtime overview | 缺节点日内聚合 | 基于 `RuntimeExecutionRecord.node_traces` 聚合 count/latency/failure。 |
| graph | 缺链路图 schema | 新增固定节点和 route-derived edge 聚合。 |
| node detail | 缺 node 维度详情 | 过滤 node_traces 并生成 input/output summary。 |
| execution detail | 缺按 execution_id 查询 | repository 增加 `execution_for_id()` 或 API 层建立索引。 |

### 14.6 收益审计

| API | 缺口 | 建议 |
| --- | --- | --- |
| revenue overview | 缺审计任务、价格、滑点、收益计算 | 新增 `revenue_audit` 模块，只基于 trade intent 做纸面审计。 |
| revenue run | 缺交易日 18:00 后调度 | 在 scheduler 或独立 audit worker 中触发。 |
| trade intent detail | 现有 TradingRecord 无价格字段 | 不改 TradingRecord 语义，新增 audit result 表关联 record_id。 |

### 14.7 成本审计

| API | 缺口 | 建议 |
| --- | --- | --- |
| cost overview | usage 分散在 model audit payload 中 | 新增 `model_usage_events` 或 `cost_audit_records`。 |
| cost pricing | 无模型价格表 | 新增配置化价格表，按 provider/model 生效时间计算。 |
| cost details | 无统一明细 | 从 AgentRunner/ReAct/Gateway 统一写入 usage 明细，关联 ticker/node/task/execution。 |
| retry cost | retry_count 有 audit 字段，但未聚合 | 聚合 `ModelAuditSummary.retry_count` 和重复调用记录。 |

## 15. 推荐最小落地顺序

1. 新增 `dashboard_api` FastAPI 服务骨架、鉴权 middleware、通用响应/错误/pagination helper。
2. 先挂载 `runtime_scheduler` 已有 facade：`overview/tickers/start/pause/stop/detail`。
3. 做 `DocumentViewAssembler`，把 Document 1/2/3 转成稳定中文卡片 schema。
4. 做 Message Bus messages/config read API，再接 config mutation。
5. 做 Runtime graph/node/executions 聚合。
6. 做 SSE，先支持 `message_bus.message.created`、`runtime.execution.updated`、`ticker.state.changed`。
7. 最后补收益审计和成本审计，因为这两块当前数据模型缺口最大。
