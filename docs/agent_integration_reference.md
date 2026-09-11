# Message Bus v2 / Crawler Plane：O4 Agent 集成操作手册

> 当前实现基线：2026-09-11。本文以仓库中已经落地的代码、持久化 schema 和本轮测试结果为事实源，不以早期设计文档补全不存在的能力；测试未覆盖处按代码明确标出接口边界，不宣称生产验收。

## 1. 先读结论：O4 应如何操作

O4 对这两个组件的标准操作面是：

1. 用 `monitoring.*` 和 `crawler_plane.*` tools 做 Agent 操作；人工操作使用 `/api/dashboard/v1/...`，二者最终进入同一 application service。
2. 只在 Crawler Plane 返回的 `working_path` 内编写 crawler 代码和 certification fixtures。不要直接修改 SQLite、`releases/`、全局 `cassettes/` 或 `artifacts/`。
3. 新 crawler 的顺序是：查询/复用 → `create_version` → 写 working copy → `live_probe` → 整理 replay/temporal/synthetic fixtures → `certify` → `promote` → `register_source` → ticker binding。
4. 修复已有 crawler 的顺序是：告警 → execution/lineage/artifact/cassette → 必要时 `add_regression` → 从 active version 创建下一版 → 修复 → certification → promote。已有 `crawler:<crawler_id>` source 会自动使用新 active version，除非参数 schema 或 source 配置也改变，否则不必重新注册 source。
5. 配置更新必须采用“先读、合并、再写”。尤其是 `monitoring.update_ticker_config`：更新已有 binding 时，如果没有传 `source_parameters`，当前实现会把参数替换成 `{}`，而不是保留原值。
6. `DOXAGENT_MESSAGE_BUS_V2_ENABLED` 默认关闭。关闭时正式 polling 和 Runtime v2 消费不运行，普通 `monitoring.*` tool 会失败；Crawler Plane 自身仍可被独立构造和开发，但这不代表 source 已投入正式监测。

## 2. 入口、存储与开关

### 2.1 运行开关和路径

| 配置 | 默认值 | Docker 正式路径/含义 |
| --- | --- | --- |
| `DOXAGENT_MESSAGE_BUS_V2_ENABLED` | `false` | 正式启用 v2 polling 与 Runtime v2 stream 消费 |
| `DOXAGENT_MESSAGE_BUS_V2_SQLITE_PATH` | `.tmp/message_bus_v2.sqlite3` | compose 中为 `/app/.tmp/message_bus_v2.sqlite3` |
| `DOXAGENT_MESSAGE_BUS_V2_WORKER_SLEEP_SECONDS` | `1` | worker 主循环空转间隔 |
| `DOXAGENT_MESSAGE_BUS_V2_ADAPTER_ROOT` | `.tmp/message_bus_v2_adapters` | `file:` API adapter 的受管根目录 |
| `DOXAGENT_MESSAGE_BUS_V2_CONTENT_ENRICHMENT_ENABLED` | `true` | 旧版兼容总开关；关闭时 Message Bus 不写补全队列 |
| `DOXAGENT_CONTENT_ENRICHMENT_ENABLED` | `true` | 全局正文补全中台开关 |
| `DOXAGENT_CONTENT_ENRICHMENT_MAX_CONCURRENCY` | `8` | 所有 ticker/source 共用的最大网络并行数 |
| `DOXAGENT_CONTENT_ENRICHMENT_RETRY_DEADLINE_SECONDS` | `180` | 首次入队起算的一次 retry 绝对截止时间 |
| `DOXAGENT_CONTENT_ENRICHMENT_RETRY_DELAY_SECONDS` | `30` | 无 `Retry-After` 时的 retry 等待时间 |
| `DOXAGENT_CONTENT_ENRICHMENT_WORKER_SLEEP_SECONDS` | `0.25` | 中台队列空闲轮询间隔 |
| `DOXAGENT_CRAWLER_PLANE_ROOT` | `.tmp/crawler-plane` | compose 中为 `/var/lib/doxagent/workspaces/crawler-plane` |
| `DOXAGENT_CRAWLER_PLANE_SQLITE_PATH` | `.tmp/crawler-plane/crawler_plane.sqlite3` | compose 中位于同一 crawler-plane 根目录 |
| `DOXAGENT_CRAWLER_PLANE_WORKER_PROCESSES` | `4` | 只接受 `4..8` |
| `DOXAGENT_CRAWLER_PLANE_EXECUTION_TIMEOUT_SECONDS` | `120` | 单 execution 超时 |
| `DOXAGENT_CRAWLER_PLANE_MAX_RESPONSE_BYTES` | `10_000_000` | 单次 HTTP body 或 rendered DOM 上限 |

实现：[`settings.py`](../src/doxagent/settings.py)、[`.env.example`](../.env.example)、[`docker-compose.yml`](../docker-compose.yml)。

正文补全由独立的 `v2-content-enrichment` 服务执行。Message Bus poll 只把消息写入同一 SQLite
持久队列，不等待目标站点；中台每批最多 claim 8 条，并在进程生命周期内共用域名 limiter。
业务写入顺序为 `queue → enrichment → identity/content_hash → Raw dedupe → Standard/stream`。
所有 source 默认补全，`SourceDefinition.content_enrichment_mode=skip` 用于 Stocktwits、TikHub 等
正文与短消息本体没有区分的来源。补全失败保持原 body，原 body 为空时保持 summary，再为空则保存空串；
失败只影响该消息，不阻塞 poll 或其他任务。

### 2.2 三种调用入口

- O4 tools：由 `default_real_tool_registry()` 注册。请求和返回统一为 `ToolRequest` / `ToolResult`。
- 人工 HTTP API：前缀为 `/api/dashboard/v1`，受 Dashboard auth 保护。成功返回 `{"data": ..., "meta": {"request_id", "generated_at", "source"}}`。
- Python application service：`MessageBusV2Service` 与 `CrawlerPlaneService`。适合内部编排、诊断当前 tools/API 尚未暴露的数据，不应绕过 service 直接写 repository。

Tool 的统一请求/返回外壳：

```json
{
  "request": {
    "tool_name": "monitoring.get_ticker_config",
    "ticker": "MU",
    "agent_name": "O4",
    "input": {"ticker": "MU"},
    "metadata": {}
  },
  "result": {
    "tool_name": "monitoring.get_ticker_config",
    "status": "succeeded",
    "output": {},
    "output_summary": "...",
    "error": null
  }
}
```

失败时 `status="FAILED"`，`error={code,message,retryable,details}`。当前两个 tool provider 会把大多数异常包装成非 retryable 的 `monitoring_tool_failed` 或 `crawler_plane_tool_failed`；Agent 仍应根据 `details.provider_error` 和 message 判断是输入错误、状态冲突还是执行故障。

实现：[`tools/schema.py`](../src/doxagent/tools/schema.py)、[`tools/factory.py`](../src/doxagent/tools/factory.py)、[`tools/providers/monitoring.py`](../src/doxagent/tools/providers/monitoring.py)、[`tools/providers/crawler_plane.py`](../src/doxagent/tools/providers/crawler_plane.py)。

## 3. Message Bus 控制面

### 3.1 当前初始 source registry

新 v2 DB bootstrap 会注册以下六个 source。注册不等于某 ticker 已启用；是否轮询只由 ticker binding 决定。

| `source_id` | `kind` / `adapter_ref` | ticker 参数 schema | 能力 |
| --- | --- | --- | --- |
| `benzinga_news` | `api` / `builtin:benzinga_news` | 可选 `search_terms: string[]`，最多 3 项 | Benzinga news；不传词时按 ticker |
| `finnhub_company_news` | `api` / `builtin:finnhub_company_news` | 空 object | 按 ticker 获取公司新闻 |
| `stocktwits_messages` | `api` / `builtin:stocktwits_messages` | 空 object | 按 ticker 获取 Stocktwits 消息 |
| `tikhub_x_search` | `api` / `builtin:tikhub_x_search` | 必填 `search_terms: string[]`，1..3 项 | X 搜索 |
| `tikhub_x_user_posts` | `api` / `builtin:tikhub_x_user_posts` | 必填 `usernames: string[]`，1..2 项 | X 用户帖子 |
| `newswire_rss` | `api` / `builtin:newswire_rss` | 必填 `rss_urls: string[]`，1..3 项 | RSS 拉取 |

初始 `default` profile **只有** `benzinga_news` 和 `finnhub_company_news`，两者工作日纽约时间 07:00–18:00、60 秒轮询、10% tolerance、持续失败 1800 秒后产生 binding poll alert。Crawler registry 初始为空；测试 fixture 不会自动建立 working copy。

`adapter_ref` 的真实形式有三种：`builtin:<source_id>`、`crawler:<crawler_id>`、`file:<relative.py>:<factory>`。`file:` 路径必须位于 `DOXAGENT_MESSAGE_BUS_V2_ADAPTER_ROOT`，factory 返回实现 `async poll(PollContext)` 的对象，并按 `(adapter_ref, source_version)` 缓存。Source 注册阶段只校验 `kind=crawler` 必须配 `crawler:`、API source 不能配 `crawler:`；builtin/file 是否真实存在要到 poll resolve 时才会发现。因此新采集网页优先使用可 certification 的 Crawler Plane，不要把注册成功当成 adapter 可执行证明。

实现：[`manifests.py`](../src/doxagent/message_bus_v2/manifests.py)、[`adapters.py`](../src/doxagent/message_bus_v2/adapters.py)。

### 3.2 核心配置模型

`SourceDefinition` 是全局 source 定义：

```json
{
  "source_id": "example_ir",
  "display_name": "Example IR",
  "kind": "crawler",
  "adapter_ref": "crawler:company_ir",
  "parameter_schema": {"type": "object", "properties": {}, "additionalProperties": false},
  "default_parameters": {},
  "default_polling_config": {
    "enabled": true,
    "target_interval_seconds": 300,
    "tolerance_ratio": 0.1,
    "alert_after_seconds": 1800,
    "active_windows": []
  },
  "default_streaming_config": {
    "publication_mode": "immediate",
    "buffer": {"max_items": 20, "max_wait_seconds": 300, "max_compiled_body_chars": 120000}
  },
  "scheduler_group": "example_ir",
  "scheduler_constraints": {"minimum_request_gap_seconds": 1.0, "max_concurrency": 1},
  "enabled": true,
  "version": 1,
  "updated_by": "agent",
  "updated_reason": "..."
}
```

`DefaultMonitoringProfile.entries[]` 每项为 `{source_id, source_parameters, polling, streaming}`。它只是新 ticker 首次 `start_ticker()` 时的模板快照。

`TickerSourceBinding` 是实际生效配置：

```json
{
  "binding_id": "MU:example_ir",
  "ticker": "MU",
  "source_id": "example_ir",
  "source_parameters": {},
  "polling": {"enabled": true, "target_interval_seconds": 300, "tolerance_ratio": 0.1, "alert_after_seconds": 1800, "active_windows": []},
  "streaming": {"publication_mode": "immediate", "buffer": {"max_items": 20, "max_wait_seconds": 300, "max_compiled_body_chars": 120000}},
  "enabled": true,
  "version": 1,
  "source_version": 1,
  "tombstoned_at": null
}
```

`PollingConfig.active_windows[]` 为 `{timezone, weekdays, start_time, end_time}`；weekday 为 `0=Monday` 到 `6=Sunday`。跨午夜窗口归属于开始日。`StreamingConfig.publication_mode` 为 `immediate|buffered`。

参数 schema 不是完整 JSON Schema 实现。当前校验支持：object root、`required`、`additionalProperties=false`、primitive type、array/items、enum、string 长度和 array 项数；不要依赖 `$ref`、组合关键字或嵌套 object 的递归校验。

实现：[`message_bus_v2/schema.py`](../src/doxagent/message_bus_v2/schema.py)。

### 3.3 配置优先级与启用条件

创建 binding 时的优先级：

1. 显式传入的 `source_parameters` / `polling` / `streaming`；
2. 未传时使用 `SourceDefinition.default_*`；
3. `start_ticker()` 从 profile 创建 binding 时，profile entry 是显式值，因此优先于 source defaults。

创建后不再动态继承：

- 更新 default profile 只影响未来首次启动的 ticker；已有 binding 不变。
- 更新 source defaults 不会覆盖已有 binding。
- 更新 source 会增加 `SourceDefinition.version`，校验所有存量 binding，并把其 `source_version` 推进到新版本；如新 schema 不兼容，必须在同一次 `update_source` 中提交 `binding_patches`。
- ticker 被重复 `start_ticker()` 时只恢复 `RUNNING`，不会重新按最新 profile 物化 binding。

一个 binding 真正进入 polling，必须同时满足：ticker state 为 `running`、source `enabled=true`、binding `enabled=true`、`binding.polling.enabled=true`、当前时间处于 active window。注册 source 本身不会创建 binding；创建 binding 也不会替未启动 ticker 创建 ticker state。

同一 `scheduler_group` 的 provider safety constraints 共享。当前有效约束取该组所有 source 的最大 `minimum_request_gap_seconds` 和最小 `max_concurrency`；即使某 source 当前 disabled，仍会参与该组约束计算。每次 poll 对 source 与 binding 做快照，更新从下一次 poll 生效。

`tolerance_ratio` 当前会进入 binding 和 scheduler schedule signature，但尚未参与 due-window 或迟延判定；实际 dispatch cadence 由 `target_interval_seconds`、group limiter、active window 和 adapter 的 `optional_next_poll_hint` 决定。不要把 tolerance 配置当成已实现的 SLA 容差执行器。

### 3.4 O4 monitoring tools

从 Policy/Monitoring Plan 落地时，按以下顺序做：先从 plan 提取每个 ticker 所需的内容类型、目标来源、查询词/账号/URL、cadence、active window、immediate/buffered；再用 `GET /message-bus/sources`（或 `repository.list_sources()`）逐项比对 `kind`、`parameter_schema`、adapter 与 scheduler constraints；然后用 `monitoring.get_ticker_config` 读取 ticker 当前 materialized binding 和 poll state。只有确认现有 source 能力不够时才进入 Crawler Plane。已有 ticker 用 binding 更新；default profile 只在确实希望未来新 ticker 默认继承时单独更新，不能用它替代当前 ticker 的 binding 修改。

| Tool | 必要/主要 input | output |
| --- | --- | --- |
| `monitoring.get_ticker_config` | `ticker` | `{ticker,ticker_state,bindings[],poll_states[]}` |
| `monitoring.update_ticker_config` | `ticker,source_id`；可选 `enabled,source_parameters,polling,streaming,reason` | `{binding}` |
| `monitoring.list_status` | 可选 `ticker` | `{counts,ticker_states[],poll_states[],alerts[]}`；counts 始终全局，ticker 只过滤 state，alerts 仍是全局 active OperationalAlert |
| `monitoring.recent_events` | `ticker`，可选 `limit` | `{stream_items[]}`，从 offset 0 正序读取，不是严格意义的“最新倒序” |
| `monitoring.register_source` | 完整 `SourceDefinition` 字段 | 注册后的 SourceDefinition |
| `monitoring.update_source` | `source_id,patch`，可选 `binding_patches,reason` | 新 source revision |
| `monitoring.hard_delete_source` | `source_id`，可选 `reason` | `{source_id,deleted_binding_count,flushed_stream_item_ids,historical_messages_preserved}` |
| `monitoring.get_default_profile` | 可选 `profile_id`，默认 `default` | profile |
| `monitoring.update_default_profile` | `profile_id,entries`，可选 `reason` | 新 profile revision |

当前 tools 的重要缺口：没有 `monitoring.list_sources`、source revision/rollback、binding delete、OperationalAlert resolve、acquisition failure 查询。要枚举 source 及其 schema，使用下述 HTTP API 或 application service。`monitoring.list_status` 的 descriptor 文案比真实返回更宽，真实返回不包含 source definitions、raw message 或 acquisition failures。

安全更新已有 binding：

```text
1. monitoring.get_ticker_config {"ticker":"MU"}
2. 找到 MU:example_ir，复制完整 source_parameters
3. 合并计划要求
4. monitoring.update_ticker_config
```

```json
{
  "ticker": "MU",
  "source_id": "example_ir",
  "source_parameters": {
    "listing_url": "https://ir.example.com/news",
    "source_name": "Example IR"
  },
  "polling": {
    "enabled": true,
    "target_interval_seconds": 300,
    "tolerance_ratio": 0.1,
    "alert_after_seconds": 1800,
    "active_windows": []
  },
  "reason": "Apply Monitoring Plan revision plan-2026-09-02"
}
```

不要只传 `polling`：当前 O4 tool 会把缺失的 `source_parameters` 变成 `{}`。该 fallback 也只识别历史的 `keywords/usernames/search_terms/rss_urls` 四个顶层键；crawler 的 `listing_url` 等自定义键即使被 schema 允许，放在 input 顶层也不会进入 binding。无论新建还是更新，始终传完整嵌套 `source_parameters`；不要期待该 tool 自动采用 source `default_parameters`。Dashboard PATCH 不存在同样的保留问题，它先保留已有参数，再合并 payload 中所有 schema property。

原子升级 source schema 的 `binding_patches` 以 binding id 为 key：

```json
{
  "source_id": "example_ir",
  "patch": {"parameter_schema": {"type":"object","properties":{"listing_url":{"type":"string"},"source_name":{"type":"string"}},"required":["listing_url","source_name"],"additionalProperties":false}},
  "binding_patches": {
    "MU:example_ir": {"source_parameters":{"listing_url":"https://ir.example.com/news","source_name":"Example IR"}}
  },
  "reason": "Align bindings with crawler v2 schema"
}
```

### 3.5 HTTP API 与 application service

Message Bus HTTP 控制面：

```text
GET    /api/dashboard/v1/tickers/{ticker}/message-bus/config
PATCH  /api/dashboard/v1/tickers/{ticker}/message-bus/config/{source_id}
DELETE /api/dashboard/v1/tickers/{ticker}/message-bus/config/{source_id}
GET    /api/dashboard/v1/message-bus/sources
POST   /api/dashboard/v1/message-bus/sources
PATCH  /api/dashboard/v1/message-bus/sources/{source_id}
GET    /api/dashboard/v1/message-bus/sources/{source_id}/revisions
POST   /api/dashboard/v1/message-bus/sources/{source_id}/rollback
DELETE /api/dashboard/v1/message-bus/sources/{source_id}
GET    /api/dashboard/v1/message-bus/default-profiles/{profile_id}
PUT    /api/dashboard/v1/message-bus/default-profiles/{profile_id}
GET    /api/dashboard/v1/message-bus/default-profiles/{profile_id}/revisions
POST   /api/dashboard/v1/message-bus/default-profiles/{profile_id}/rollback
GET    /api/dashboard/v1/tickers/{ticker}/message-bus/overview
GET    /api/dashboard/v1/tickers/{ticker}/message-bus/messages
GET    /api/dashboard/v1/tickers/{ticker}/message-bus/messages/{message_id}
```

枚举 source/schema：

```bash
curl -H "Authorization: Bearer $DASHBOARD_TOKEN" \
  https://HOST/api/dashboard/v1/message-bus/sources
```

Python service 的写入口为：

```python
repository, bus = build_message_bus_v2_service(settings)
bus.register_source(source)
bus.update_source(source_id, patch, actor=UpdateActor.AGENT,
                  reason=reason, binding_patches=binding_patches)
bus.save_default_profile(profile)
bus.configure_binding(ticker=..., source_id=..., actor=UpdateActor.AGENT, ...)
bus.update_binding(binding_id, patch, actor=UpdateActor.AGENT, reason=...)
bus.delete_binding(binding_id, actor=UpdateActor.AGENT, reason=...)
bus.hard_delete_source(source_id, actor=UpdateActor.AGENT, reason=...)
```

只读诊断可用 `repository.list_sources()`、`list_bindings()`、`list_poll_states()`、`list_failures()`、`list_alerts()`、`list_raw()`、`list_standard()`、`read_stream()`、`list_audit()`。它们当前不是完整的 O4 tool API；不要把直接 repository 写入当成受支持操作。

实现：[`service.py`](../src/doxagent/message_bus_v2/service.py)、[`repository.py`](../src/doxagent/message_bus_v2/repository.py)、[`dashboard_api/real_router.py`](../src/doxagent/dashboard_api/real_router.py)、[`dashboard_api/real_service.py`](../src/doxagent/dashboard_api/real_service.py)。

## 4. Message Bus 数据面与 Runtime 契约

### 4.1 ingest、bootstrap、去重和发布

- 每个 ticker/source 独立去重。identity 优先级为 `external_id` → `source_item_key` → canonical URL hash。
- 同 identity + 同 content hash 是 duplicate；同 identity + 新 content hash 是 revision。
- binding 第一次成功 poll 是 bootstrap：消息写入 raw/baseline，但不发布 stream item。此后才发布新消息。
- source disable、binding disable/delete 只阻止未来 polling；已发布 stream 不撤销。binding delete 会先强制 flush buffer，再 tombstone。
- source hard delete 会恢复 pending raw、flush buffer，并删除 source/profile/binding 等控制记录；raw、standard、stream、revision 与 audit 历史保留。
- crawler checkpoint 只存在 Crawler Plane。Crawler source 的 Message Bus `PollState.checkpoint` 始终写回 `{}`。

`PollState` 是当前可查询的 polling 健康快照：`status` (`never_polled|succeeded|failed|disabled`)、due/dispatch/attempt/success/failure 时间、`failure_since`、错误、consecutive failures、latency 和累计 collected/published 数。

### 4.2 immediate 与 buffered

`immediate` 一条 StandardMessage 对应一个 StreamItem。`buffered` 按 `max_items`、`max_wait_seconds` 和 `max_compiled_body_chars` flush；单条超长消息不会截断，会原样发布并产生 `compiled_message_oversized` warning。

一个 buffered StreamItem 在 Runtime v2 中仍是一个 `RuntimeCase`。编译 body 按发布时间、member index 排序，每条使用：

```text
[MESSAGE N]
source: ...
published_at: ...
title: ...              # 有 title 才出现
body:
...
url: ...
```

Runtime envelope 的 `source_message_id/source_id/binding_id/url/published_at` 取 batch 最新 member；buffered snapshot 的 title 为 null，body 为上述完整编译文本。多样元数据不会被提升为 Runtime 的确定性审计字段。

### 4.3 Runtime 消费

- 固定 consumer id 由 Runtime Scheduler 使用；consumer offset 按 ticker 持久化。
- ticker 切换到 `trading` 时先把 Runtime cursor seek 到当前 stream tail，因此不会重放此前 monitoring-only 历史。
- Runtime 每个 StreamItem 执行一次；case 达到 `ADJUDICATED|COMPLETED|PENDING_W3` 后才 commit offset。异常时不 commit，事件保留待重试。
- v2 路径没有 `source_type` social gate，也没有 source allow/deny policy；ticker 是否消费某数据源，只取决于该 ticker 是否存在并启用 binding。

实现：[`compiler.py`](../src/doxagent/message_bus_v2/compiler.py)、[`persistent_runtime_v2/schema.py`](../src/doxagent/persistent_runtime_v2/schema.py)、[`runtime_scheduler/service.py`](../src/doxagent/runtime_scheduler/service.py)。

## 5. Crawler package 与执行契约

### 5.1 真实目录和 registry

正式根目录结构：

```text
/var/lib/doxagent/workspaces/crawler-plane/
├── crawler_plane.sqlite3
├── working/<crawler_id>/vN/
│   ├── crawler.py
│   └── tests/
│       ├── cases.json
│       └── *.json
├── releases/<crawler_id>/vN/
├── cassettes/<crawler_id>/<cassette_id>.json
└── artifacts/<execution_id>/<basename>
```

没有 manifest 文件、package upload 或 dependency install。版本元数据存于 SQLite：

```json
{
  "crawler_id": "example_ir",
  "version": 1,
  "entrypoint": "crawler.py:crawl",
  "parameter_schema": {"type":"object"},
  "checkpoint_schema_version": 1
}
```

`create_version` 创建 `WORKING` 目录。可用 `base_version` 从已有 working/release 复制；版本必须从 1 开始并严格等于 `latest_version + 1`。`promote` 是 move，不是 copy：working 目录消失，release 目录变成只读。

Crawler Plane SQLite 持久化 package/version、每 crawler+binding checkpoint、execution、service-owned item retry、artifact metadata、cassette metadata、certification、regression 和 alert/policy。不要直接修改这些表。Retry 唯一键为 `crawler_id + binding_id + item_key`；下一次正常 production poll 会把 due items 作为 `ctx.retry_items` 注入当前 ACTIVE version。

实现：[`crawler_plane/assets.py`](../src/doxagent/crawler_plane/assets.py)、[`crawler_plane/repository.py`](../src/doxagent/crawler_plane/repository.py)、[`crawler_plane/schema.py`](../src/doxagent/crawler_plane/schema.py)。

### 5.2 crawler.py 接口

入口格式必须是 `relative/path.py:function`，不得用 `..` 逃出 package。函数可同步或异步，接收一个 `CrawlerContext`：

```python
ctx.ticker: str
ctx.parameters: dict
ctx.checkpoint: dict
ctx.retry_items: list[dict]
await ctx.http.get(url, params={}, headers={})
await ctx.browser.get(url)
await ctx.artifacts.save(name, content, kind="crawler")
```

目前 parent broker 只提供 HTTP GET、Chromium page GET 和 artifact save。返回可以是：

- `CrawlerRunOutput`；
- 可被其校验的 dict；
- observation list（此时 `next_checkpoint` 默认使用未修改的 `ctx.checkpoint`）。

标准输出：

```python
from doxagent.crawler_plane.schema import CrawlerObservation, CrawlerRunOutput
from doxagent.crawler_plane.worker_runtime import CrawlerContext

async def crawl(ctx: CrawlerContext) -> CrawlerRunOutput:
    response = await ctx.http.get(str(ctx.parameters["listing_url"]))
    response.raise_for_status()
    raw_ref = await ctx.artifacts.save("listing.html", response.text, kind="raw")
    return CrawlerRunOutput(
        observations=[CrawlerObservation(
            external_id="stable-provider-id",
            title="Title",
            body="Full body",
            source=str(ctx.parameters["source_name"]),
            url=response.url,
            published_at="2026-09-02T00:00:00Z",
            metadata={},
            raw_artifact_ref=raw_ref,
        )],
        next_checkpoint={"seen_ids": ["stable-provider-id"]},
        diagnostics={"listing_count": 1},
    )
```

`CrawlerObservation` 要求非空 body/source、绝对 HTTP(S) URL、带 timezone 的 `published_at`。建议提供稳定 `external_id`，否则 Message Bus bridge 会以 URL 形成身份。

可复用确定性 helper：`html_text`、`article_text`、`parse_datetime`、`canonical_url`、`json_value`、`xml_root`、`sha256`、`unseen_ids`、`advance_seen_ids`，见 [`toolkit.py`](../src/doxagent/crawler_plane/toolkit.py)。仅供测试说明的完整 fixture 见 [`company_ir_reference/crawler.py`](../tests/fixtures/crawler_packages/company_ir_reference/crawler.py)；它不会进入生产 registry，也不能作为真实来源能力复用。

实际安全边界：子进程是受信 O4 代码执行环境，不是 OS sandbox。框架提供父进程网络 broker，但当前没有从操作系统层阻止 crawler 自己 import socket/http client 或访问文件。Agent 必须只使用 `ctx.http` / `ctx.browser`，不要把“parent-owned capability”误解为强隔离。

### 5.3 O4 Crawler Plane tools

| Tool | input | output |
| --- | --- | --- |
| `crawler_plane.list` | `{}` | `{crawlers:[CrawlerPackage]}` |
| `crawler_plane.get` | `crawler_id` | `{crawler,versions[]}` |
| `crawler_plane.create_version` | `crawler_id,version`；可选 `base_version,entrypoint,parameter_schema,checkpoint_schema_version` | CrawlerVersion，含 `working_path` |
| `crawler_plane.certify` | `crawler_id,version` | CertificationResult |
| `crawler_plane.promote` | `crawler_id,version`；可选 `checkpoint_action` | CrawlerVersion |
| `crawler_plane.rollback` | `crawler_id,version` | CrawlerVersion |
| `crawler_plane.execute` | 完整 `CrawlerExecutionRequest` | CrawlerExecutionResult |
| `crawler_plane.live_probe` | `crawler_id,version,ticker,parameters`；可选 `baseline_cassette_ref` | CrawlerExecutionResult |
| `crawler_plane.get_execution` | `execution_id` | `{execution,artifacts[]}` |
| `crawler_plane.get_cassette` | `cassette_id` 或 `cassette_ref` | `{cassette}` |
| `crawler_plane.list_alerts` | 可选 `crawler_id,open_only` | `{alerts[]}` |
| `crawler_plane.update_alert_policy` | 完整 CrawlerAlertPolicy | policy |
| `crawler_plane.resolve_alert` | `alert_id` | resolved alert |
| `crawler_plane.list_retries` | 可选 `crawler_id,binding_id,status,limit` | `{retries:[CrawlerRetryItem]}` |
| `crawler_plane.resolve_retry` | `retry_id` | resolved retry item |
| `crawler_plane.reactivate_retry` | `retry_id` | reset PENDING retry item |
| `crawler_plane.register_source` | 完整 CrawlerSourceRegistration | SourceDefinition |
| `crawler_plane.add_regression` | `execution_id` | RegressionCase |

几个不可从 descriptor 猜测的真实字段：

- `crawler_plane.execute` 使用 `source_parameters`，不是 `parameters`。它缺省生成 `poll_run_id` 和从 ToolRequest 补 ticker，但 `binding_id`、`source_id` 仍必填。
- `crawler_plane.live_probe` 使用 `parameters`，且 O4 tool 当前要求显式传 object，包括空 `{}`。
- `crawler_plane.execute` 的 `commit_checkpoint` 默认是 `true`。开发复现时必须传 `false`，或使用独立 probe/cert binding id，避免污染正式 checkpoint。
- `checkpoint_override` 只能和 `commit_checkpoint=false` 一起使用。
- 显式 `version` 可以执行 WORKING/CERTIFIED/旧 release；不传 version 才是 production 语义，只允许 active release，并校验 release digest。

`CrawlerExecutionRequest`：

```json
{
  "crawler_id": "example_ir",
  "ticker": "MU",
  "binding_id": "debug:example_ir",
  "source_id": "debug.example_ir",
  "source_parameters": {"listing_url":"https://ir.example.com/news","source_name":"Example IR"},
  "poll_run_id": "manual-repro-001",
  "network_mode": "REPLAY",
  "cassette_ref": "cassette_...",
  "version": 2,
  "commit_checkpoint": false,
  "checkpoint_override": {"seen_ids": ["old"]},
  "preserve_response_bodies": false
}
```

`CrawlerExecutionResult` 返回并持久化：`execution_id,poll_run_id,crawler_id,crawler_version,source_id,binding_id,ticker,source_parameters,status` (`RUNNING|SUCCEEDED|PARTIAL|FAILED|TIMED_OUT`)、`crawler_content_digest`、`observations[]`、`item_failures[]`、`completed_retry_keys[]`、`retry_keys[]`、`diagnostics`、`artifact_refs[]`、`cassette_ref`、`checkpoint_before/checkpoint_after`、开始/结束时间、latency/request_count/response_bytes、error code/message 和 `message_bus_telemetry`。Tool 返回该 model；`get_execution` 额外附带已解析的 artifact metadata。

### 5.4 HTTP API

Crawler Plane application service 的受支持入口为：`list_crawlers()`、`get_crawler()`、`create_version()`、`get_version()`、`certify_version()`、`get_certification_result()`、`promote_version()`、`rollback_version()`、`execute()`、`live_probe()`、`get_execution()`、`get_execution_artifacts()`、`get_alert_policy()`、`update_alert_policy()`、`list_alerts()`、`get_alert()`、`resolve_alert()`、`list_retries()`、`resolve_retry()`、`reactivate_retry()`、`register_crawler_source()`、`add_failure_to_regression()`。内部诊断还可只读调用 repository 的 `list_executions()`、`get_cassette()`、`get_checkpoint()` 和 `list_regressions()`；不要用 repository 执行状态修改。

```text
GET  /api/dashboard/v1/crawler-plane/crawlers
GET  /api/dashboard/v1/crawler-plane/crawlers/{crawler_id}
POST /api/dashboard/v1/crawler-plane/crawlers/{crawler_id}/versions
GET  /api/dashboard/v1/crawler-plane/crawlers/{crawler_id}/versions/{version}
POST /api/dashboard/v1/crawler-plane/crawlers/{crawler_id}/versions/{version}/certify
POST /api/dashboard/v1/crawler-plane/crawlers/{crawler_id}/versions/{version}/promote
POST /api/dashboard/v1/crawler-plane/crawlers/{crawler_id}/versions/{version}/rollback
GET  /api/dashboard/v1/crawler-plane/certifications/{run_id}
POST /api/dashboard/v1/crawler-plane/executions
POST /api/dashboard/v1/crawler-plane/live-probes
GET  /api/dashboard/v1/crawler-plane/executions/{execution_id}
GET  /api/dashboard/v1/crawler-plane/executions/{execution_id}/artifacts
GET  /api/dashboard/v1/crawler-plane/alert-policies/{policy_key}
PUT  /api/dashboard/v1/crawler-plane/alert-policies/{policy_key}
GET  /api/dashboard/v1/crawler-plane/alerts?crawler_id=...&open_only=true
GET  /api/dashboard/v1/crawler-plane/alerts/{alert_id}
POST /api/dashboard/v1/crawler-plane/alerts/{alert_id}/resolve
GET  /api/dashboard/v1/crawler-plane/retries?crawler_id=...&binding_id=...&status=...&limit=...
POST /api/dashboard/v1/crawler-plane/retries/{retry_id}/resolve
POST /api/dashboard/v1/crawler-plane/retries/{retry_id}/reactivate
POST /api/dashboard/v1/crawler-plane/alerts/{alert_id}/resolve
POST /api/dashboard/v1/crawler-plane/sources
POST /api/dashboard/v1/crawler-plane/executions/{execution_id}/regressions
```

相较 O4 tools，HTTP API 额外提供 certification result、单条 alert、alert policy 的 GET；但仍没有 execution list、cassette GET 或 checkpoint GET endpoint。

### 5.5 CLI

Crawler console entrypoint 是 `doxagent-crawler-plane`：

```bash
uv run doxagent-crawler-plane list
uv run doxagent-crawler-plane get example_ir 1
uv run doxagent-crawler-plane create-version example_ir 2 --base-version 1 \
  --entrypoint crawler.py:crawl \
  --parameter-schema '{"type":"object"}' \
  --checkpoint-schema-version 1
uv run doxagent-crawler-plane certify example_ir 2
uv run doxagent-crawler-plane promote example_ir 2
uv run doxagent-crawler-plane rollback example_ir 1
uv run doxagent-crawler-plane run example_ir MU example_ir MU:example_ir \
  --version 2 --parameters '{"listing_url":"https://ir.example.com/news"}'
```

CLI `run` 固定 `commit_checkpoint=false`，但不支持 REPLAY/cassette；`promote`/`rollback` 也没有 `checkpoint_action` 参数。遇到 checkpoint schema 变化应使用 O4 promote tool、HTTP promote 或 application service。HTTP/tool 的 rollback 当前同样未暴露 `checkpoint_action=reset`；不兼容 rollback 只能调用 service。

Message Bus worker：

```bash
uv run python -m doxagent.message_bus_v2.cli status
uv run python -m doxagent.message_bus_v2.cli run-once
uv run python -m doxagent.message_bus_v2.cli run-worker
```

## 6. working → certification → promote → register → bind

### 6.1 新 crawler 的最小完整流程

1. 查询复用：`crawler_plane.list`、`crawler_plane.get`；再用 `GET /message-bus/sources` 检查是否已经存在满足能力和 schema 的 source。
2. 创建版本：首次必须 `version=1`；复用已有 crawler 时创建 `latest+1` 并传 `base_version=active_version`。
3. 只写返回的 `working_path`，至少提供 entrypoint 和 `tests/cases.json`。
4. `crawler_plane.live_probe` 做真实连接。检查 `status`、`observations`、`diagnostics.live_probe`、`cassette_ref` 和 `artifact_refs`。`baseline_cassette_match` 只比较 transport/method/request URL/status class 的 shape；false 不会自动使 probe 失败。
5. 把可靠 cassette 复制到 working `tests/`，或在 cases 中引用持久化 cassette id。为了 package 可移植，优先保存为 working 内相对路径。
6. 准备 replay、temporal、synthetic 三类 case。对已知历史失败先 `add_regression`。
7. `crawler_plane.certify`；只有 `overall=PASS` 才进入 `CERTIFIED`。
8. certification 后不要再改 working。`crawler_plane.promote` 会重算 digest，任何变化都会拒绝。
9. `crawler_plane.register_source`。这要求 crawler 已有 active release。
10. 读取 source definition/schema，再用 `monitoring.update_ticker_config` 建 binding。若 ticker 尚未启动，等待 Runtime Scheduler 的 `start_ticker`，或由内部 service 明确启动；仅建 binding 不会使未启动 ticker polling。

创建与上线示例：

```jsonc
// crawler_plane.create_version
{
  "crawler_id":"example_ir",
  "version":1,
  "entrypoint":"crawler.py:crawl",
  "parameter_schema":{
    "type":"object",
    "properties":{
      "listing_url":{"type":"string","minLength":1},
      "source_name":{"type":"string","minLength":1}
    },
    "required":["listing_url","source_name"],
    "additionalProperties":false
  },
  "checkpoint_schema_version":1
}
```

```jsonc
// crawler_plane.live_probe
{
  "crawler_id":"example_ir",
  "version":1,
  "ticker":"MU",
  "parameters":{"listing_url":"https://ir.example.com/news","source_name":"Example IR"}
}
```

```jsonc
// crawler_plane.register_source
{
  "source_id":"example_ir",
  "display_name":"Example IR",
  "crawler_id":"example_ir",
  "parameter_schema":{
    "type":"object",
    "properties":{"listing_url":{"type":"string"},"source_name":{"type":"string"}},
    "required":["listing_url","source_name"],
    "additionalProperties":false
  },
  "default_parameters":{},
  "default_polling_config":{"target_interval_seconds":300,"alert_after_seconds":1800},
  "default_streaming_config":{"publication_mode":"immediate"},
  "scheduler_group":"example_ir",
  "scheduler_constraints":{"minimum_request_gap_seconds":2,"max_concurrency":1}
}
```

Crawler version 的 `parameter_schema` 与 Message Bus source 的 `parameter_schema` 当前没有自动相等校验。Agent 必须保持二者一致；新 active crawler 若改变参数 schema，promote 不会自动更新 SourceDefinition 或 bindings。

### 6.2 certification fixture 与结果

`tests/cases.json` 是 `CertificationCase[]`：

```json
[
  {"case_id":"replay","kind":"replay","live_derived":true,"cassette_refs":["$live_probe"],"parameters":{},"initial_checkpoint":{},"expected_status":"SUCCEEDED","expected_external_ids":["A"],"expected_checkpoint":{"seen_ids":["A"]},"observation_assertions":[{"external_id":"A","url_prefix":"https://example.test/","body_min_length":20}]},
  {"case_id":"temporal","kind":"temporal","cassette_refs":["tests/t0.json","tests/t1.json"],"parameters":{},"initial_checkpoint":{},"expected_external_ids":["B"]},
  {"case_id":"synthetic","kind":"synthetic","cassette_refs":["tests/synthetic.json"],"parameters":{},"initial_checkpoint":{"seen_ids":["A"]},"expected_external_ids":["SYNTHETIC_D"]}
]
```

CertificationResult：

```json
{
  "certification_run_id":"cert_...",
  "crawler_id":"example_ir",
  "crawler_version":1,
  "content_digest":"sha256...",
  "overall":"PASS",
  "checks":[
    {"check":"contract","status":"PASS","test_case":null,"expected":{},"actual":{},"cassette_ref":null,"artifact_ref":null,"diagnostic":null}
  ],
  "regression_count":0,
  "started_at":"...",
  "finished_at":"..."
}
```

七项真实检查：

| check | 实际行为 |
| --- | --- |
| `contract` | 要求版本处于 WORKING/CERTIFIED、有 working path、entrypoint 存在，并具备 live-derived replay、temporal、synthetic、malformed、duplicate/revision 及 partial/failure cases |
| `replay` | 至少一个 replay case；逐 case replay，按 observation 顺序精确比较 `external_id or url` |
| `temporal_replay` | case 必须恰有 T0/T1；T1 输出等于 expected；用推进后 checkpoint 再跑 T1 必须零输出 |
| `synthetic_increment` | 至少一个 synthetic case；行为与 replay 对比相同 |
| `package_failures` | 校验 partial/failure、malformed、duplicate/revision 的 status、IDs、item/retry keys、checkpoint 和 observation assertions |
| `determinism` | 选择第一个 replay 或 synthetic，用相同 cassette/checkpoint 跑两次，observations 和 checkpoint 必须完全一致 |
| `failure_replay` | 逐个运行已加入该 crawler 的 regression；所有历史失败必须在候选版成功。没有 regression 时为 `NOT_APPLICABLE`，`regression_count=0` |

Certification 不运行 package 自带 pytest，不访问真实站点。Temporal 的 T0 observations 不与 expected 对比；expected 对应 T1 增量。Promotion 额外要求成功且有 observation 的 live probe digest、certification digest 与待发布 release digest 完全一致。

实现：[`certification.py`](../src/doxagent/crawler_plane/certification.py)、测试 fixture：[`cases.json`](../tests/fixtures/crawler_packages/company_ir_reference/tests/cases.json)。

## 7. Cassette、Raw Artifact 与 Failure Artifact

### 7.1 Network Cassette

每个 exchange 保存：sequence、`http|browser`、method、request URL/headers、status、response URL/headers、可选 response body/ref、observed_at。

- 普通 RECORD 成功 execution 会保存 cassette，但默认移除 `response_body`，所以它保留 lineage/shape，不能保证可用于内容 replay。
- `live_probe` 与 PARTIAL execution 保存完整 response body。
- FAILED/TIMED_OUT 总是保存带当前已录 exchanges 和 body 的 cassette。
- REPLAY 按顺序消费 exchange；不足时报 `network cassette exhausted`，transport/URL 不匹配时报 `cassette mismatch`。
- `cassette_ref` 可以是 DB cassette id，也可以是 package 内相对 JSON path。全局文件路径为 `cassettes/<crawler_id>/<cassette_id>.json`。

O4 使用 `crawler_plane.get_cassette` 按 id/ref 查询；当前 HTTP API 没有 cassette GET。内部代码也可只读调用 `crawler_plane.repository.get_cassette(id)`。不要修改受管 cassette 文件。

安全注意：当前 cassette 不做 header/body secret redaction。request/response headers 会持久化，failure/live probe 还会持久化 body。不要在 crawler 源码、parameters、headers 或 artifacts 中写长期密钥；当前实现也没有专用 secret injection/redaction 接口。

### 7.2 Raw Artifact

Crawler 显式调用 `await ctx.artifacts.save(name, content, kind=...)`。返回 `artifact_id`；文件原子写入 `artifacts/<execution_id>/<basename>`，repository 保存 `{artifact_id,execution_id,kind,path,sha256,size_bytes,created_at}`。若 observation 的 `raw_artifact_ref` 指向该 id，Message Bus 会把它带入 raw message metadata，但不会校验引用存在。

查询：`crawler_plane.get_execution` 同时返回 execution 和 artifacts；HTTP 也有独立 artifacts endpoint。读取内容使用 artifact 的受管绝对 `path`，先核对 `sha256`，不要修改。

### 7.3 Failure Artifact

FAILED/TIMED_OUT execution 自动生成 `kind="failure_bundle"` 的 `failure_bundle.json`，包括开始时的 execution snapshot、source parameters、checkpoint_before、cassette id、error code/message。该 bundle 和完整 failure cassette 是复现首选。

只有“失败且有 cassette”的 execution 可以 `crawler_plane.add_regression`。Regression 固化 `{crawler_id,source_execution_id,cassette_ref,checkpoint,parameters}`，之后每次 certification 都必须通过 failure replay。

实现：[`assets.py`](../src/doxagent/crawler_plane/assets.py)、[`runtime.py`](../src/doxagent/crawler_plane/runtime.py)、[`service.py`](../src/doxagent/crawler_plane/service.py)。

## 8. Message Bus ↔ Crawler Plane lineage

正式 crawler poll 的链路是：

```text
TickerSourceBinding
  → GlobalPollScheduler 生成 poll_run_id
  → AdapterRegistry 解析 crawler:<crawler_id>
  → CrawlerExecutionRequest(poll_run_id, source_id, binding_id, active version)
  → CrawlerExecutionResult(execution_id, version, checkpoint, cassette/artifacts)
  → PollResult(acquisition_metadata)
  → PollExecutionResult(poll_run_id, crawler_execution_id, counts/errors)
  → RawMessage.metadata(crawler_id, crawler_version, crawler_execution_id,
                        poll_run_id, raw_artifact_ref)
```

成功或失败后，scheduler 都把 `PollExecutionResult` 写入对应 crawler execution 的 `message_bus_telemetry`。这使 `crawler_plane.get_execution(execution_id)` 成为 crawler poll 的主要跨面诊断入口。

当前限制：Message Bus 不单独持久化 PollExecutionResult。对于 API source，没有 execution id 时只能从 `PollState`、`AcquisitionFailure`、OperationalAlert、raw/standard/stream 诊断；`AcquisitionFailure` 目前仅 repository 可查。对于 crawler source，可从 alert 的 `execution_id` 或 RawMessage metadata 进入完整 lineage。

## 9. 告警、排障、复现与确认

### 9.1 Message Bus poll health / OperationalAlert

当前可能出现：

- `source_poll_failure`：单 binding 从首次失败持续超过该 binding 的 `alert_after_seconds`。
- `source_poll_failure_aggregate`：同 scheduler group 近 5 分钟内至少 3 个 binding 为 failed。
- `scheduler_capacity_insufficient`：目标 polling demand 超过 scheduler group 的安全 request capacity。
- acquisition/materialization error code：按 binding + error code + raw hash 去重。
- `compiled_message_oversized`：单消息编译后超过 buffer body limit，但仍已发布。

读取顺序：

1. `monitoring.list_status {ticker}`：active alerts、PollState、ticker state。
2. `monitoring.get_ticker_config`：确认 binding 参数和 gate。
3. `monitoring.recent_events`、Dashboard messages/detail：确认是否已 raw/standard/publish。
4. crawler source 若有 execution id，转到 Crawler Plane；API source 则内部查询 `list_failures()` 与 raw `processing_status/error`。

Message Bus OperationalAlert 当前没有人工/O4 resolve 或 acknowledge 接口。成功 poll 会自动清除 binding/aggregate failure alert；capacity 恢复会自动清除 capacity alert。不要通过直接改 DB “确认”。

### 9.2 Crawler alert 类型和 policy

Policy key：`<crawler_id>:<source_id-or-*>:<alert_type>`。模型：

```json
{
  "crawler_id":"example_ir",
  "source_id":null,
  "alert_type":"crawler_discovery_anomaly",
  "enabled":true,
  "threshold":null,
  "window":3
}
```

| alert type | 默认 | 触发/恢复 |
| --- | --- | --- |
| `crawler_execution_failure` | enabled, window 1 | 任意 FAILED/TIMED_OUT 即 open；成功 execution 自动 resolve；当前实现不使用 window 聚合 |
| `crawler_discovery_anomaly` | enabled, window 3 | 同 crawler+binding 最近 window 次成功 execution 都零 observations；恢复有输出后 resolve |
| `crawler_content_drift` | disabled, threshold 100 | 任一 body 长度小于 threshold；全部达标后 resolve |
| `crawler_transport_anomaly` | enabled, window 1 | diagnostics 中存在 HTTP status >=400；无 transport failure 后 resolve；当前实现不使用 window 聚合 |

Crawler-specific policy 与 crawler-wide (`source_id=null`) 同类并存时，当前 repository 排序使 specific policy 后写入有效映射，因此 specific 覆盖 global。更新 policy 不会回算历史，下一 execution 才重新 evaluate。

CrawlerAlert：`{alert_id,alert_key,crawler_id,source_id,binding_id,execution_id,alert_type,status,message,metadata,first_seen_at,last_seen_at,repeat_count,resolved_at}`。相同 key 会 reopen/累加 repeat count。

处理告警的标准步骤：

1. `crawler_plane.list_alerts {"open_only":true}`，保存 alert id、execution id、binding/source。
2. `crawler_plane.get_execution`，检查 status/error、version、parameters、checkpoint before/after、request/bytes、diagnostics、cassette、artifacts、`message_bus_telemetry`。
3. 读取 failure bundle、raw artifacts、完整 cassette；核对 hash。
4. 对稳定可复现的失败执行 `crawler_plane.add_regression`。
5. `crawler_plane.get` 找 active/latest；`create_version(latest+1, base_version=active)`。
6. 用 REPLAY + `checkpoint_override` + `commit_checkpoint=false` 复现；修复后 live probe。
7. certification → promote。若改变 source parameter schema，同步 source 并原子迁移 bindings。
8. 让新 active version 对受影响 binding 成功执行；健康评估通常会自动 resolve。
9. 若告警条件已经人工确认不适用，使用 `crawler_plane.resolve_alert`；下一次仍失败会 reopen。当前没有独立 ACK/snooze 状态，只有 OPEN/RESOLVED。

实现：[`health.py`](../src/doxagent/crawler_plane/health.py)。

## 10. 常见失败与恢复

| 错误/现象 | 原因 | 正确恢复 |
| --- | --- | --- |
| `DOXAGENT_MESSAGE_BUS_V2_ENABLED is false` | v2 未正式启用 | 不要回退 v1；在部署配置中明确启用 v2 后重启相关进程 |
| `source already exists` | 重复用 generic register | 用 `monitoring.update_source`；crawler source 可用 `crawler_plane.register_source`，它对已有 source 走 update |
| missing/unsupported source parameter | binding 不符合 SourceDefinition schema | 先 GET source schema，再完整重提 parameters |
| 只改 polling 却出现 `{}` 参数 | O4 ticker update 的当前替换语义 | 从 `get_ticker_config` 取回完整参数并重写 |
| `source schema is incompatible with active binding(s)` | source schema 升级会破坏存量 binding | 同一个 update 提供按 binding id 的 `binding_patches` |
| crawler source/ref kind mismatch | `kind=crawler` 未用 `crawler:`，或 API source 错用 crawler ref | 修正 SourceDefinition；crawler 注册优先用 `crawler_plane.register_source` |
| first/next version error | 版本不是 1 或不是 latest+1 | `crawler_plane.get` 读取 latest 后创建唯一下一版 |
| `entrypoint file not found` / contract fail | working package 不完整 | 修复 working path 与 `tests/cases.json`，重新 certify |
| certification 缺 replay/temporal/synthetic | 三类 fixture 不齐 | 按真实 case schema补齐；查看 check diagnostic |
| `working copy changed after certification` | PASS 后改过任意 package 文件 | 重新 certify，再 promote |
| checkpoint schema incompatible | 新旧 `checkpoint_schema_version` 不同 | promote 时显式 `checkpoint_action=reset`，接受清除该 crawler 全部 binding checkpoint；当前无迁移器 |
| `crawler has no ACTIVE release` | 生产执行/注册早于 promote | certification PASS 后 promote |
| immutable release digest mismatch | release 被外部改写 | 停止使用该 release；从可信 base 创建、certify、promote 新版，不手改 release |
| `checkpoint_override requires commit_checkpoint=false` | 调试请求会污染状态 | 显式 false |
| `network cassette exhausted/mismatch` | crawler 网络调用顺序/URL 已改变或 fixture 不完整 | 更新代码以保持契约，或用新 live probe 录制并审查 fixture |
| `crawler response exceeds...` / rendered DOM exceeds | 超过全局 body 上限 | 缩小请求/页面；不要在 crawler 内绕过限制 |
| `execution_timeout` | worker 超过全局 timeout | 用 failure bundle/replay 优化；timeout 会重启承载该 job 的 worker |
| Playwright/binary 错误 | 本地未安装 browser；容器镜像已安装 Chromium | 在正确镜像运行或执行受管浏览器安装；不要把 HTTP replay PASS 当 browser live PASS |
| failed execution 无法 add regression | execution 成功或无 cassette | 只把 FAILED/TIMED_OUT 且有 cassette 的 execution 加入 regression |
| source registered/bound 但不 poll | ticker 未 running、source/binding/poll disabled、active window 外、v2 flag off | 按四层 gate 和 PollState 定位，不重复注册 |
| 手动 resolve 后告警重开 | 根因仍存在 | 先修复并跑健康 execution，再 resolve/确认 |

## 11. 当前未实现或不能直接修改的边界

- v1 DB 历史不会迁移；正式 v2 不双 poll、不双写。
- O4 tools 不能枚举 SourceDefinition；需 HTTP/application service。
- HTTP API 没有 cassette GET；tools/API 没有 checkpoint GET、execution list、Message Bus AcquisitionFailure 查询、Message Bus OperationalAlert resolve。
- O4 tool 没有 certification-result-by-id 或 get-alert-policy；HTTP/application service 有。
- 没有 crawler package manifest、bundle upload、requirements 安装、依赖锁定或 per-package virtualenv。
- 没有 checkpoint migration；只有保持 schema version 或全 crawler reset。
- 没有 crawler alert ACK/snooze，只有 OPEN/RESOLVED。
- 没有 cassette secret redaction，且成功生产 cassette 默认不保留 response body。
- 没有强 OS sandbox；crawler code 被视为受信代码。
- live probe 与直接 execution 默认不经过 Message Bus scheduler group limiter；正式 Message Bus crawler poll 才把 `request_permit` 传给 parent network session。
- source registration 不自动加入 default profile、不自动绑定 ticker；promote 也不自动同步 source parameter schema。
- service 自动生成 id、时间戳、版本 revision、digest、release path、cassette/artifact metadata、poll/stream offset、checkpoint 与 alert repeat count；Agent 不应伪造或直接改写这些持久化字段。

## 12. 实现与测试核对索引

核心实现：

- Message Bus contracts / service / persistence / scheduler / adapters：[`schema.py`](../src/doxagent/message_bus_v2/schema.py)、[`service.py`](../src/doxagent/message_bus_v2/service.py)、[`repository.py`](../src/doxagent/message_bus_v2/repository.py)、[`scheduler.py`](../src/doxagent/message_bus_v2/scheduler.py)、[`adapters.py`](../src/doxagent/message_bus_v2/adapters.py)
- Runtime envelope 与消费：[`persistent_runtime_v2/schema.py`](../src/doxagent/persistent_runtime_v2/schema.py)、[`runtime_scheduler/service.py`](../src/doxagent/runtime_scheduler/service.py)
- Crawler contracts / lifecycle / worker / network / certification / health：[`crawler_plane/schema.py`](../src/doxagent/crawler_plane/schema.py)、[`crawler_plane/service.py`](../src/doxagent/crawler_plane/service.py)、[`crawler_plane/worker_runtime.py`](../src/doxagent/crawler_plane/worker_runtime.py)、[`crawler_plane/runtime.py`](../src/doxagent/crawler_plane/runtime.py)、[`crawler_plane/certification.py`](../src/doxagent/crawler_plane/certification.py)、[`crawler_plane/health.py`](../src/doxagent/crawler_plane/health.py)
- Agent tools / human API / CLI：[`tools/providers/monitoring.py`](../src/doxagent/tools/providers/monitoring.py)、[`tools/providers/crawler_plane.py`](../src/doxagent/tools/providers/crawler_plane.py)、[`dashboard_api/real_router.py`](../src/doxagent/dashboard_api/real_router.py)、[`dashboard_api/real_service.py`](../src/doxagent/dashboard_api/real_service.py)、[`crawler_plane/cli.py`](../src/doxagent/crawler_plane/cli.py)、[`message_bus_v2/cli.py`](../src/doxagent/message_bus_v2/cli.py)

当前直接相关测试：

- [`tests/test_message_bus_v2.py`](../tests/test_message_bus_v2.py)：bootstrap、dedupe/revision、buffer、cursor、恢复、hard delete、alerts、scheduler、dynamic/builtin adapters、O4 tools、Runtime v2 handoff、Dashboard v2 contract。
- [`tests/test_crawler_plane.py`](../tests/test_crawler_plane.py)：reference certification、8 个并行 replay execution、move-only promote、lineage/checkpoint ownership、failure cassette/bundle、O4 tools、human API。
- [`tests/test_dashboard_real_message_bus_api.py`](../tests/test_dashboard_real_message_bus_api.py)：Dashboard Message Bus overview/config mutation/error contract。
- [`tests/test_phase25_runtime_scheduler.py`](../tests/test_phase25_runtime_scheduler.py)：无 legacy social gate、进入 trading 时 seek tail、不重放旧 stream。

本轮文档编写前实际运行：

```text
uv run pytest tests/test_message_bus_v2.py tests/test_crawler_plane.py tests/test_dashboard_real_message_bus_api.py -q
21 passed

uv run pytest \
  tests/test_message_bus_v2.py::test_scheduler_runtime_v2_handoff_skips_history_then_commits_new_stream \
  tests/test_phase25_runtime_scheduler.py::test_trading_has_no_legacy_social_source_gate \
  tests/test_phase25_runtime_scheduler.py::test_switch_to_paper_trading_does_not_replay_existing_pending_events -q
3 passed
```

这些测试使用固定响应、MockTransport、replay cassette 或本地 application service，也不是对上文每个 HTTP endpoint 的穷举验收。当前没有在本轮执行真实 provider、真实网站、真实凭据、真实 Playwright Chromium 或生产部署验收；Agent 在首次正式启用新 crawler 前仍必须执行 live probe，并把结果作为独立证据判断。
