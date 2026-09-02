# Message Bus v1 当前实现细节与 Workflow v2 依赖契约

> 状态：基于 2026-09-01 当前工作树的代码审计。
>
> 本文中的“Message Bus v1”是历史架构称呼，不是代码内已有的正式版本号。当前
> `doxagent.monitoring` 的模型、数据库表和事件均没有 `contract_version` 字段；本文把这套
> 从 Workflow v1 延续下来、并已叠加 Scheduler、正文补全、Stocktwits durable polling 和
> Persistent Runtime v2 adapter 的实现统称为 v1。
>
> 本文是实现剖面和依赖契约，不是新版重构方案。现有操作手册仍见
> `docs/monitoring-message-bus.md`。

## 1. 结论先行

当前 Message Bus 不是 broker、队列集群或通用 pub/sub 中间件，而是一个以本地 SQLite
为权威状态的、面向单机 ticker runtime 的“采集与可消费消息日志”。它同时承担六类职责：

1. 保存 source catalog 和 ticker/source binding；
2. 按 polling cadence 主动拉取外部消息；
3. 保存 provider 原始 payload；
4. 将异构 payload 投影为统一 `StandardMessage`；
5. 生成带全局单调 `stream_offset` 的 `EventStreamItem`；
6. 通过单一 `consumed` 布尔值把事件交给 Persistent Runtime。

它还附带正文补全、poll health、CLI、agent tools、Monitoring Viewer、Dashboard API 和
Stocktwits 专用 durable acquisition adapter。因此它已经同时包含 data plane、control plane、
consumer cursor 和 observability，而不是一个边界清晰的消息管线组件。

Workflow v2 当前并未定义自己的 Source Message。实际直接依赖链是：

```text
MonitoringConfigDocument
        │
        ▼
UnifiedRuntimeSchedulerService ──配置──> Message Bus bindings
        │
        ├──按交易时段轮询 source
        │
        ▼
FetchedExternalMessage
        │
        ▼
RawExternalMessage ──> StandardMessage ──> EventStreamItem
                                              │
                                    可选正文补全会改写 payload
                                              │
                                              ▼
                                     pending_events(consumed=0)
                                              │
                             paper_trading + 合法交易时段
                                              │
                                              ▼
                                SourceMessageEnvelope.from_event
                                              │
                                              ▼
                                  PersistentRuntimeV2Service
                                              │
                                   达到裁决状态后才 ack
                                              │
                                              ▼
                                  mark_event_consumed(event_id)
```

因此，v1 的事件 payload、`standard_message_id`、三层时间、单 cursor 消费语义和正文补全
时机已经成为 v2 的事实 ABI。重构 Message Bus 时不能只替换 collectors 或数据库；至少需要
同时迁移 Scheduler adapter、Persistent Runtime v1/v2、Dashboard、agent tools 和历史回放。

## 2. 代码版图与责任分布

### 2.1 核心包

| 文件 | 当前责任 |
| --- | --- |
| `src/doxagent/monitoring/schema.py` | source、binding、raw、standard、event、poll state 的 Pydantic 合同；默认 source catalog；去重键和参数校验 |
| `src/doxagent/monitoring/collectors.py` | Benzinga、Finnhub、Stocktwits legacy、TikHub X、RSS 的同步 HTTP/RSS collector |
| `src/doxagent/monitoring/normalizer.py` | provider payload 到 `StandardMessage` 的纯字段投影与基础 HTML 清理 |
| `src/doxagent/monitoring/repository.py` | In-memory 与 SQLite repository；六张主表；live-view watermark；pending/consumed |
| `src/doxagent/monitoring/service.py` | 配置、due 计算、poll、ingest、正文补全、事件读取/确认的应用服务 |
| `src/doxagent/monitoring/media_enrichment.py` | 不完整 media 正文的抓取、提取、质量判定、fallback 和回写 |
| `src/doxagent/monitoring/stocktwits_durable.py` | Stocktwits 专用 checkpoint/crawl/hot-mode 系统到通用 bus 的桥 |
| `src/doxagent/monitoring/cli.py` | 初始化、配置、轮询、正文补全和 legacy 常驻 poller |
| `src/doxagent/monitoring/viewer.py` | 本地/SSH-backed Monitoring Control Plane |

### 2.2 直接邻接模块

| 模块 | 对 Message Bus 的依赖 |
| --- | --- |
| `runtime_scheduler` | 正式轮询入口、交易时段裁决、Monitoring Config 应用、事件消费与 ack |
| `persistent_runtime` | v1 consumer；从 `EventStreamItem` 宽松构造 `RuntimeSourceMessage` |
| `persistent_runtime_v2` | v2 consumer；严格验证完整 `StandardMessage` payload，再拆分 Snapshot/Envelope |
| `tools/providers/monitoring.py` | 四个 agent-facing tools |
| `dashboard_api/real_service.py` | overview、messages、detail、config、runtime graph 数据 |
| `cdecr_integration` | 明确不直接消费主 bus；目前没有从 bus 自动生成 production `RuntimeNovelMessageBatch` 的 adapter |

## 3. 原始设计目标与边界

最初文档把 v1 定义为 infrastructure-only：采集、raw persistence、standardization、基础
idempotency、durable event stream、agent-readable configuration 和用户可观测性。它明确不负责：

- 交易触发；
- 事件重要性排序；
- 语义去重；
- 低质量内容过滤；
- 新旧事件判定。

这些边界在代码上仍大体成立：Message Bus 不调用 LLM，不做 W1/W2/O3 决策，所有新消息只要
通过 exact dedupe 和 binding watermark 就会生成事件。后来增加的正文补全是一种 message
materialization，并未升级为相关性或业务质量 gate。

这套设计保留下来的通用需求值得作为新版锚点：

- source 接入与下游业务判断解耦；
- 原始数据和标准消息双层保留；
- provider 差异在 normalizer 前终止；
- 一个 source 可以按 ticker 或按参数配置；
- 配置权、轮询成本权和 agent 策略权分离；
- exact duplicate 不重复触发下游；
- 新 binding 不把 provider 历史窗口整体灌入实时流；
- 事件可持久化、可排序、进程重启后仍可消费；
- 失败不应被伪装成“没有新消息”；
- 用户和 agent 都能读取状态，但高成本 cadence 由用户控制。

## 4. Control Plane：source、binding 与权限

### 4.1 两个正交维度

每个 `MonitoringSourceConfig` 同时声明：

- `source_type`: `media | social`，表达内容性质；
- `interface_type`: `by_ticker | by_parameter`，表达 API 查询形态。

这两个维度不互相推导。例如 Benzinga 是 `media + by_ticker`，Newswire RSS 是
`media + by_parameter`，Stocktwits 是 `social + by_ticker`，TikHub X Search 是
`social + by_parameter`。

当前 source catalog 是代码内封闭枚举和默认列表：

| source_id | 类型 | 接口 | 默认周期 | binding 参数 |
| --- | --- | --- | ---: | --- |
| `benzinga_news` | media | by_ticker | 60s | `search_terms` 最多 3 个，仅作 ticker 无结果时 topics fallback |
| `finnhub_company_news` | media | by_ticker | 60s | 无，ticker only |
| `stocktwits_messages` | social | by_ticker | 300s | 通用 binding 无参数；durable cadence 等由专用 state 管理 |
| `tikhub_x_search` | social | by_parameter | 600s | `search_terms` 最多 3 个 |
| `tikhub_x_user_posts` | social | by_parameter | 600s | `usernames` 最多 2 个 |
| `newswire_rss` | media | by_parameter | 600s | `rss_urls` 最多 3 个 |

模型统一采用 `extra="forbid"`，但 `MonitoringParameters` 为兼容历史仍声明了
`keywords`、`source_filters` 和 `extra`。当前所有 source schema 都不允许这些字段实际非空，
repository 会 fail closed。

### 4.2 binding 是配置，也是 live watermark

`TickerSourceBinding` 的确定性 ID 为：

```text
{TICKER_UPPER}:{source_id_lower}
```

它保存 enabled、source-specific parameters、创建/更新时间、更新 actor 和 reason。`merge=True`
时列表参数去重合并；`replace` 才整体替换。

`binding.updated_at` 同时被用作 live-stream watermark：provider publish time 早于该时间的消息会
计入 `historical_skipped_count`，但不会写 raw、standard 或 event。没有 provider publish time 的
消息被当作 live。

这个复用是 v1 的重要构造逻辑，也是一项耦合：任何 binding 更新都会推进 watermark；配置修改
时间和“开始接收实时数据的时间”目前不是两个独立概念。

### 4.3 权限分层

- 全局 source enable/disable：user only；
- source polling interval：user only，且不得低于 30 秒；
- ticker/source binding 的 enabled 和合法查询参数：service 接受 user/agent/system；
- Stocktwits target/hot cadence、page size、crawl pages、hot threshold、bootstrap policy、mode：
  user-side 专用接口；
- agent tool 显式拒绝 `poll_interval_seconds`。

当前注册了四个工具：

- `monitoring.get_ticker_config`
- `monitoring.update_ticker_config`
- `monitoring.list_status`
- `monitoring.recent_events`

但当前 O2 agent allowlist 只授予读取类工具，不授予 `monitoring.update_ticker_config`。因此“工具已
注册”和“某个 agent 当前可调用”是两个不同事实。

## 5. Data Plane：从 provider 到事件

### 5.1 Collector 输出边界

所有 collector 最终输出 `FetchedExternalMessage`：

```text
source_id
binding_id
ticker
source_type
interface_type
raw_payload
provider_message_id?
source_url?
source_published_at?
metadata{}
```

collector 只负责请求构造、provider response 解包和最早一层时间/ID/URL 识别。它不生成 bus ID，
不决定 exact duplicate，也不写库。

常规 collector 是同步、逐 binding 执行的。TikHub Search 会逐 search term 请求；只要至少一个
term 成功就返回已获取结果，全部失败才抛错。Benzinga 在 ticker 查询为空时才使用最多三个
`search_terms` 做 topics fallback。

### 5.2 三层消息模型

#### RawExternalMessage

Raw 层增加：

- UUID 风格 `raw_message_id`；
- `dedupe_key`；
- canonical raw payload SHA-256；
- `collected_at`；
- duplicate 观测计数。

raw payload 原样保存，标准化不会破坏原始 HTML 或 provider 特有字段。

#### StandardMessage

Standard 层把 provider 差异压缩为：

```text
standard_message_id, raw_message_id
source_id, binding_id, ticker
source_type, interface_type
title, body, url, author, username
symbols[], keywords[]
published_at, collected_at, normalized_at
provider_message_id, metadata{}
```

`normalize_message()` 按 `EndpointKind` 分派。Benzinga body 会做 HTML text extraction；Finnhub
使用 headline/summary；Stocktwits 提取 user、sentiment、likes/replies；TikHub 兼容多层 legacy
结构；RSS 支持 RSS/Atom 的常见字段。

如果 provider 没给 symbols，normalizer 回填当前 binding ticker。`published_at` 优先使用
normalizer 识别结果，再回退 raw 的 `source_published_at`。

#### EventStreamItem

每个首次插入的 StandardMessage 对应一个事件：

```text
event_id             随机 bus ID
stream_offset        SQLite AUTOINCREMENT，全库单调
standard_message_id  唯一
event_type           固定 monitoring.message.created
event_time           append_event 时的 UTC 时间
ticker
source_id
payload              StandardMessage 的完整 JSON
consumed             单一布尔值，默认 false
```

事件按 `stream_offset ASC` 被消费，按 `DESC` 展示。它不是按 `published_at` 排序的业务时间流。

### 5.3 exact dedupe

去重键的优先级是：

```text
{source_id}:provider_id:{provider_message_id}
{source_id}:url:{source_url}
{source_id}:payload:{canonical_raw_payload_sha256}
```

命中 duplicate 时只增加 raw row 的 `duplicate_seen_count` 和 `last_seen_at`，不再创建 standard
或 event。

这个规则是 source-global，不包含 ticker 或 binding。其实际语义不是“同一 ticker 内去重”，而是
“同一 source 的同一 provider item 全局只归属第一次写入它的 binding/ticker”。一条同时属于多个
ticker 的新闻如果以相同 provider ID 返回，后续 ticker 不会得到自己的 StandardMessage/Event。
这是新版设计必须显式决定保留还是修复的契约，而不能无意继承。

### 5.4 ingest 顺序

`MonitoringBusService.ingest_fetched()` 对每条 fetched message 顺序执行：

```text
binding watermark check
  -> save_raw_message
  -> duplicate? stop this item
  -> normalize_message
  -> save_standard_message
  -> append_event
```

批次结果记录 collected、historical skipped、raw inserted、duplicate、standardized 和 event 数量。

raw、standard、event 分别由独立 repository 方法和独立 SQLite transaction 写入，不是一个原子
transaction。进程在中间失败时，允许出现“有 raw 无 standard”或“有 standard 无 event”的部分
状态；当前没有 repair/re-drive outbox 自动补齐这些中间态。

## 6. Polling 与调度

### 6.1 lower-level due 规则

普通 source 的 due 条件基于 `PollState.last_attempt_at + poll_interval`，不是 last success。失败后也
要等完整 interval 才再次 due。没有 poll state 的 enabled binding 立即 due。

`poll_binding()` 的顺序为：

1. 写 `record_poll_attempt`；
2. 检查 source/binding enabled；
3. 调 collector 或 Stocktwits durable adapter；
4. ingest；
5. 写 success/failure 和 latency；
6. provider/ingest 异常写 failure 后继续向调用方抛出。

`poll_due_once()` 逐 binding 串行执行，并把单 binding 异常转换成失败的
`IngestBatchResult`；正式 Scheduler 则逐 binding 调 `poll_binding()` 并独立记录 audit。

### 6.2 正式入口与 legacy 入口

正式生产入口是：

```text
python -m doxagent.runtime_scheduler.cli run-loop
```

`python -m doxagent.monitoring.cli poll-forever` 只应作为低层/debug poller。两者同时轮询同一
ticker/source 会绕过统一交易时段规则并产生重复外部请求；exact dedupe 只能降低重复事件，不能
消除 API 成本、poll state 竞争和 side effects。

Scheduler 的时段规则（America/New_York）是：

- 工作日周一 07:00–08:00、其他工作日 07:30–08:00：`pre_market_digest`；
- 工作日 08:00–18:00：`formal_monitoring`；
- 其他时间：`off_hours_low_frequency`，当前只轮询 `stocktwits_messages`。

### 6.3 Stocktwits 特例

Stocktwits 默认不走普通 interval collector，而走独立 durable polling 子系统：

- 独立 repository 保存 ticker checkpoint、crawl run、coverage/gap、next due 和 hot mode；
- 新 ticker 按 `stagger_slots=10` 错峰；
- normal/hot/paused mode 有各自 cadence；
- crawl 产出的新消息再桥接为 `FetchedExternalMessage`，进入相同 raw/standard/event 管线；
- crawl coverage、checkpoint、rate limit、run ID 等被压缩写入 Message Bus `PollState.metadata`；
- acquisition truth 与 bus truth 因而分布在两个 SQLite store 中。

## 7. 正文补全不是旁路，而会改写事件

Scheduler 在一次 poll 产生 media event 后，会在读取 pending events 前自动调用
`enrich_recent_media()`。默认每次最多 5 条、并发 2，只选择 body 不完整的 media message。

正文质量的主要门槛是：

- complete-like：至少 800 字符且至少 4 句；
- 可接受的改善：至少 600 字符、至少 4 句、且长度至少是已有正文两倍；
- title-only、HTML/entity、过短、截断/paywall marker 均视为不完整。

提取链包括 direct fetch、JSON-LD `articleBody`、trafilatura、HTML article/main/body fallback，
以及部分域名上的 Jina Reader fallback；同时记录 domain pacing、HTTP、latency、method、failure
reason 和 content SHA-256。

成功或失败都会更新 StandardMessage 的 `metadata.media_enrichment`。成功时还更新 body、url、
author。repository 随后把更新后的完整 StandardMessage JSON 直接写回已有
`monitoring_event_stream.payload_json`。

所以当前 event stream 不是 immutable append-only log：

- `event_id`、`stream_offset`、`event_time` 不变；
- event payload 在 append 后仍可能变化；
- Persistent Runtime 实际看到的是“消费时的最新 materialized payload”，不一定是事件创建时的
  payload；
- 已 consumed 事件也可能被手工 enrichment 再次改写。

这与 v2 文档所说的“Case 启动时冻结 immutable snapshot”并不矛盾，但冻结点是在 Runtime
adapter，而不是 Message Bus publication。新版必须明确需要 immutable event、可版本化 message
materialization，还是二者都需要。

## 8. 持久化结构与 live view

默认 store 为 `.tmp/monitoring_message_bus.sqlite3`，核心六表为：

| 表 | 主键/唯一约束 | 作用 |
| --- | --- | --- |
| `monitoring_sources` | `source_id` | 全局 source catalog |
| `monitoring_bindings` | `binding_id`；`(ticker, source_id)` unique | ticker/source 配置和 watermark |
| `monitoring_raw_messages` | `raw_message_id`；`dedupe_key` unique | 原始 payload 与 exact duplicate audit |
| `monitoring_standard_messages` | `standard_message_id`；`raw_message_id` unique | 标准消息和正文补全结果 |
| `monitoring_event_stream` | `stream_offset` autoincrement；event/standard ID unique | 可消费事件和全局单 cursor |
| `monitoring_poll_states` | `binding_id` | 累计与最近一次 poll health |

`recent_standard_messages`、`recent_events`、`pending_events` 都不是无条件读历史表，而是 join
当前 binding，并要求：

```text
binding 仍存在
AND (published_at is null OR published_at >= binding.updated_at)
```

因此：

- 删除 binding 只删除 binding 和 poll state；raw/standard/event audit row 保留；
- 删除后这些 row 不再进入 live view 或 pending view；
- 重建 binding 后，旧 row 是否重新可见取决于 publish time 与新 `updated_at`；
- raw history 查询不使用 live watermark，仍能看到历史 audit。

当前没有 retention、partition、archive、vacuum policy 或 message body size limit。SQLite 连接按
repository 操作创建，未配置显式 WAL/busy timeout；并发模型仍是单机、低并发假设。

## 9. 消费语义

### 9.1 当前 cursor

`pending_events()` 等价于读取 `consumed = 0` 的 live events，按 `stream_offset ASC` 返回。
`mark_event_consumed(event_id)` 只是把布尔值设为 1。

它没有：

- consumer name / consumer group；
- claim/lease/visibility timeout；
- compare-and-set owner；
- ack reason / ack time / consumer attempt；
- per-consumer offset；
- nack / retry schedule；
- dead-letter queue；
- poison event 隔离；
- 一个事件被多个独立下游各消费一次的能力。

因此 `consumed` 的真实含义是“主 Persistent Runtime 不再需要看到此事件”，不是通用消息已被
所有订阅者消费。

### 9.2 近似 delivery guarantee

当前链路具有部分 at-least-once 特征，但不是完整 broker guarantee：

- Runtime 成功/裁决后才 ack，异常时事件保持 pending；
- Persistent Runtime v2 以 `standard_message_id` 查询已有 Case，提供业务幂等基础；
- 如果 Case 已到允许状态但 ack 前进程终止，下次执行会返回已有 Case，Scheduler 随后可 ack；
- 如果 Case 已持久化为 FAILED，下一次 `execute_message()` 也直接返回同一 Case，Scheduler 会因
  状态不在允许集合而再次失败；目前没有 bus-level poison isolation；
- 多 Scheduler 实例可以同时读取同一 pending event，因为没有 claim；
- 一个事件失败会跳出当前 ticker 的 runtime loop，后续 offset 留待下一 tick。

## 10. Workflow v2 对 Message Bus 的具体依赖

### 10.1 启动配置依赖

`UnifiedRuntimeSchedulerService.start_ticker()` 当前仍使用 legacy `DocumentBundle`，并要求完整文档
集合 usable。启动步骤固定展示为：Document1 → Document2 → Document3 → Message Bus → Runtime。

Message Bus 步骤读取 `MonitoringConfigDocument.monitoring_items[].tool_input`，将以下内容映射为
binding：

```text
source_id
enabled
mode = merge | replace
reason
source-specific allowed list fields
```

Scheduler 会丢弃 source schema 不允许的参数；单 item 失败只记 warning 并继续。若存在
MonitoringConfig 但最终一个 binding 都没有成功应用，ticker startup 被 blocked。

这个应用过程不是配置集事务：

- 每个 item 独立 upsert；
- 前面成功、后面失败时不会回滚；
- 新配置中缺失的旧 binding 不会自动删除；
- 默认 `merge` 会把旧参数继续保留；
- `applied_config_version` 只进入 Scheduler 状态/audit，不作为 Message Bus row 的 version pin。

当前 Codex v2 D3 的核心产物是 Runtime Policy Projection；Message Bus 配置生产仍依赖历史
`MonitoringConfigDocument` 合同。也就是说，v2 已替换 Runtime 判断层，但还没有重建完整的监测
配置编译边界。

### 10.2 运行模式依赖

Scheduler 有两个可用 monitor mode：

- `message_monitoring`：继续 poll 和入 bus，但不消费 pending event；
- `paper_trading`：仅在 pre-market digest / formal monitoring 时消费 Runtime events。

从 `message_monitoring` 切到 `paper_trading` 时会记录 `paper_trading_enabled_at`，只处理
`event_time >= enabled_at` 的事件。更早的 media event 不会被消费，也不会自动 ack，仍会留在
pending count 中。这是“禁止历史 replay”的当前实现，而不是独立 cursor 起点。

### 10.3 Event → v2 SourceMessageEnvelope 精确映射

v2 adapter 执行：

```python
payload = StandardMessage.model_validate(event.payload)
SourceMessageEnvelope.from_standard_message(
    payload,
    message_bus_event_time=event.event_time,
)
```

映射如下：

| v2 字段 | Message Bus 来源 | 语义 |
| --- | --- | --- |
| `source_message_id` | `StandardMessage.standard_message_id` | Case 幂等业务键，不是 provider/raw/event ID |
| `raw_message_id` | payload | raw lineage |
| `source_id` | payload | provider/source adapter identity |
| `binding_id` | payload | ticker/source config lineage |
| `url` | payload | 运维/审计字段，不给 W1/W2 snapshot |
| `published_at` | payload | source time candidate |
| `collected_at` | payload | bus collection time |
| `normalized_at` | payload | materialization time |
| `message_bus_event_time` | `EventStreamItem.event_time` | bus append time；Revenue Audit 等也依赖该时间 |
| `provider_message_id` | payload | provider lineage |
| `metadata` | payload | normalizer、provider、正文补全等扩展审计 |
| `snapshot.ticker` | payload | W1/W2 可见业务字段 |
| `snapshot.source_type` | payload | media/social |
| `snapshot.interface_type` | payload | by_ticker/by_parameter 字符串 |
| `snapshot.title/body/author/username/symbols/keywords` | payload | W1/W2 实际输入 |

`SourceMessageSnapshot` 要求 title 或 body 至少一个非空。v1 StandardMessage 自身没有同等 validator，
所以 bus 能接受、保存并发布 title/body 都为空的事件，但 v2 adapter 会拒绝它。

v2 模型同样使用 `extra="forbid"`。由于 adapter 先把 event payload 严格验证成完整
`StandardMessage`，未来给 event payload 直接增加未经 StandardMessage versioning 接受的新顶层字段
会令 v2 消费失败。

### 10.4 时间契约

v2 `occurrence_source_time` 的确定性优先级是：

```text
published_at
  -> message_bus_event_time
  -> collected_at
```

它被用于确定 America/New_York trading date，并在 W3/O2 occurrence adapter 中作为 cutoff/source
time 候选。因此 Message Bus 对 provider publish time 的解析质量会跨层影响 Runtime Case 归属日、
Delta 和后续日终 feed。

需要区分三种时间：

- `published_at`：provider 声称的发布时间；
- `collected_at`：本系统接收 raw 的时间；
- `event_time`：标准消息被 append 为 bus event 的时间。

正文补全不会修改这三个时间，也不会新增 payload revision time；它只更新 StandardMessage body、
url、author、metadata。

### 10.5 ack 契约

v2 enabled 时，Scheduler 对每个 pending event：

1. `SourceMessageEnvelope.from_event(event)`；
2. `PersistentRuntimeV2Service.execute_message()`；
3. Case 必须达到 `ADJUDICATED | COMPLETED | PENDING_W3`；
4. 才调用 `mark_event_consumed(event_id)`。

`PENDING_W3` 已足以 ack Message Bus；后续 W3/effect 失败不会把原事件重新设为 pending。此时恢复由
Persistent Runtime v2 自己的 Case/effect journal 承担，而不是由 Message Bus 重投。

v2 开关关闭时，同一 Scheduler 改走 Persistent Runtime v1。开关开启后事件只走 v2，没有按事件
自动 fallback 到 v1。

### 10.6 social 契约

当前 Scheduler 在进入 Runtime 前执行 social exclusion：

- v2 service 存在且 `social_enabled=true` 时允许 social；
- 其他情况下，source_id 属于 Stocktwits/TikHub X、payload 声明 `source_type=social`，或 source
  catalog 表明 social，都会被排除；
- 被排除事件会直接 `mark_event_consumed`，但不会增加 Runtime processed/consumed counters；
- audit reason 为 `social_sources_temporarily_message_bus_only`。

所以默认 `DOXAGENT_PERSISTENT_RUNTIME_V2_SOCIAL_ENABLED=0` 不是“social 留在队列等待以后处理”，
而是“Scheduler 在 paper-trading 消费轮次中确认丢弃其 Runtime 资格”。这项语义在重构时必须显式
确认。

### 10.7 v2 的其他上游不是 Message Bus 的责任

Message Bus 只提供 source message。v2 Case 启动还要求：

- Published Known Event Index；
- Published Runtime Policy Projection；
- provisional snapshot version；
- 对应 event/policy version pin。

这些缺失时 `execute_message()` fail closed，bus event 保持 pending。CDECR/Event Library 不应建立
第二个 bus consumer 或竞争 `consumed`；当前 production code 也没有从 Message Bus pending rows
直接构造 CDECR `RuntimeNovelMessageBatch`。

## 11. 当前测试所证明的层级

现有测试分为三个层级：

- `tests/test_phase21_monitoring_message_bus.py`：覆盖 source 维度、raw/standard/event、exact
  duplicate、watermark、binding 删除后的 live view、SQLite 重启、正文补全、Stocktwits bridge、
  参数权限和 collector request；
- `tests/test_phase25_runtime_scheduler.py`：覆盖正式 Scheduler 的时段、message-monitoring/paper-trading、
  social 排除、禁止历史 replay、runtime failure 保持 pending、SQLite 重启和重复 tick；这些测试
  主要实例化 Persistent Runtime v1；
- `tests/test_persistent_runtime_v2.py`：覆盖 v2 adapter 对应的手工 SourceMessageEnvelope、Case 幂等、
  W1/W2/Router/effects/daily close，但不是通过真实 Message Bus + Scheduler 注入。

当前没有一个聚焦测试把 `UnifiedRuntimeSchedulerService(runtime_v2_service=...)`、真实
`EventStreamItem.payload`、v2 Case 和 bus ack 放在同一个测试中。因此“Message Bus → Scheduler →
Runtime v2”的接口在代码上已接通，但其跨模块契约没有独立端到端回归锁定。

## 12. 作为新版锚点应保留的性质

下面这些是 v1 已表达且仍有业务价值的通用性质，不等于要求保留现有实现方式：

1. **Raw fidelity**：原始 provider payload 可审计，不因 normalizer 或正文补全丢失。
2. **Canonical projection**：下游不直接适配每个 provider。
3. **Deterministic identity**：exact duplicate 不重复创建业务触发。
4. **Explicit source lineage**：source、binding、provider ID、raw ID、standard ID 都可追踪。
5. **Time separation**：source published、collected、event append 不混为一个时间。
6. **Historical flood protection**：新启用 source 不把查询窗口全部视作实时消息。
7. **Durable ordering**：重启后仍有稳定顺序和未处理记录。
8. **Fail-visible health**：poll failure、latency、coverage 和最近一次结果可观察。
9. **Authority separation**：agent 可表达监测策略，但不能任意提高付费 API cadence。
10. **Runtime decoupling**：采集不直接执行交易；业务判定由 Runtime 完成。
11. **Recoverable handoff**：Runtime 在完成自己的 durable acceptance 前，bus 不提前 ack。
12. **Human/agent observability**：同一配置和数据面可由 CLI、工具和 UI 查看。

## 13. 重构讨论前必须正面处理的现有问题

以下不是新版方案，只是从当前 contract 推导出的不可回避问题。

### 13.1 P0：契约与正确性

1. **event payload 可变**：append-only ID 外壳内保存的是可被 enrichment 改写的 materialized
   message，v2 输入不能按 event publication 重放。
2. **单一 consumed 位**：无法同时支持 Persistent Runtime、CDECR、审计、replay 等独立 consumer；
   social exclusion 也与正常 ack 共用同一位。
3. **没有 claim/lease**：多 scheduler 或重入时可并发处理同一 event。
4. **ingest 非原子**：raw → standard → event 中间失败没有自动修复。
5. **source-global dedupe 丢 ticker fan-out**：同一 provider item 只能归属首次写入 ticker。
6. **payload 无版本**：v2 严格依赖 `StandardMessage` 当前 shape，schema evolution 没有协商机制。
7. **poison event 会阻塞顺序批次**：失败 event 保持 pending，当前 tick 在异常处终止；FAILED v2
   Case 又会被幂等返回，缺少隔离/人工 resolution 状态。

### 13.2 P1：语义与运维

1. binding `updated_at` 同时承担配置更新时间和 ingestion watermark。
2. Monitoring Config 是逐 item merge/upsert，不是带版本的期望配置集；旧 binding 会漂移残留。
3. 禁止历史 replay 通过过滤 `event_time` 实现，更早事件仍永久占 pending count。
4. 默认 social-disabled 会 ack-and-drop Runtime 资格，难以日后 replay。
5. due 使用 last attempt，失败恢复 cadence 无独立 backoff/priority。
6. 常规 collector 和 binding 在一个 scheduler tick 中串行执行，慢 source 会增加整 ticker latency。
7. Stocktwits acquisition 与主 bus 分库、分状态机，统一恢复和一致性需要跨 repository 理解。
8. SQLite 没有 retention/partition/size limit；full raw、full body、full event payload 重复存储。
9. 事件全局 offset 只表达落库顺序，不表达 ticker 内 source time 顺序或 late arrival。
10. V2 feature flag 默认关闭；代码接通不等于任意部署已启用，部署状态需要另行核验。

## 14. 后续方案讨论需要先回答的接口问题

1. 新 Message Bus 是只服务一个 Runtime consumer，还是需要正式 consumer groups？
2. “消息 identity”和“ticker delivery identity”是否拆分，以支持一条消息 fan-out 多 ticker？
3. 原始消息、标准消息、正文 revision 和 runtime delivery 是否应成为四个独立对象？
4. Runtime Case 冻结的是首次标准化版本，还是达到正文质量门槛后的某个 revision？
5. schema/version migration 是原地升级 payload，还是保留 versioned envelope + adapter？
6. historical suppression 应由 source cursor、binding activation epoch 还是业务 window 表达？
7. social-disabled 是 defer、archive、独立 consumer，还是明确 terminal discard？
8. Message Bus ack 应等待 hot-path adjudication、全部 effects，还是只等待 durable Case acceptance？
9. poison event、部分 ingest、失败 enrichment 和 provider gap 分别由谁恢复？
10. Monitoring Config 是 patch/merge，还是 Document3 产出的 versioned desired state？
11. CDECR 增量输入由 Runtime 产出 novel batch，还是成为独立 bus consumer？现有边界支持前者。
12. Dashboard/agent tools 读取 operational projection，还是直接依赖内部 bus tables/models？

在这些问题明确前直接替换数据库或引入 Kafka/Redis，并不能解决当前真正的语义耦合；新版首先需要
冻结 message、delivery、revision、consumer receipt 和 runtime acceptance 五类 identity/状态边界。

## 15. 主要证据入口

- v1 原始说明：`docs/monitoring-message-bus.md`
- 核心合同：`src/doxagent/monitoring/schema.py`
- ingest/ack facade：`src/doxagent/monitoring/service.py`
- SQLite/live view：`src/doxagent/monitoring/repository.py`
- provider 投影：`src/doxagent/monitoring/normalizer.py`
- 正文 revision：`src/doxagent/monitoring/media_enrichment.py`
- Stocktwits bridge：`src/doxagent/monitoring/stocktwits_durable.py`
- 正式运行编排：`src/doxagent/runtime_scheduler/service.py`
- v1 Runtime adapter：`src/doxagent/persistent_runtime/schema.py`
- v2 Runtime adapter：`src/doxagent/persistent_runtime_v2/schema.py`
- v2 Case lifecycle：`src/doxagent/persistent_runtime_v2/service.py`
- v2 方案原文：`dev_plan/workflow_v2/presistent_runtime_v2.md`
- agent tools：`src/doxagent/tools/providers/monitoring.py`
- 当前测试：`tests/test_phase21_monitoring_message_bus.py`、
  `tests/test_phase25_runtime_scheduler.py`、`tests/test_persistent_runtime_v2.py`

## 16. 本次审计验证

执行：

```powershell
uv run pytest -p no:cacheprovider `
  tests/test_phase21_monitoring_message_bus.py `
  tests/test_phase25_runtime_scheduler.py `
  tests/test_persistent_runtime_v2.py -q
```

结果：`76 passed, 4 warnings`。pytest 退出码为 0。测试完成后的 LangSmith multipart trace
上传因月度用量限制返回 HTTP 429；该告警发生在本地断言全部通过之后，不改变上述测试结论，也不
构成真实 provider、真实部署或 Bus→Scheduler→V2 专门端到端验收。
