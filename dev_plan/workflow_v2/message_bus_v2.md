# DoxAgent Message Bus v2 开发方案

## 1. 重构目标

Message Bus v2 采用整体重写，不以修改现有 `doxagent.monitoring` v1 为实现路径。

v1 保留作为参考实现和迁移来源。当前 v1 已经具备 Raw persistence、StandardMessage、exact dedupe、正文补全、durable stream、poll state 等有价值能力，但这些能力与 Runtime Scheduler、单一 `consumed` cursor、media/social 类型、Stocktwits 特例以及可变 event payload 形成了较强耦合。

v2 的目标是建立一个独立的：

> **ticker-scoped、source-agnostic、durable source message infrastructure**

其完整职责为：

```text
Source Registry
      +
Default Monitoring Profile
      +
Ticker Source Binding
      +
Global Poll Scheduler
      +
SourceAdapter
      ↓
RawMessage
      ↓
Exact Identity / Dedupe
      ↓
Content Validation / Enrichment
      ↓
Immutable StandardMessage
      ↓
Immediate / Buffered Publication
      ↓
Ticker-local Durable Stream
      ↓
Consumer Cursor
```

Message Bus 负责“把外部 source 的增量信息稳定转换为可消费消息”。

Runtime 负责“如何理解和处理消息”。

二者以 StreamItem contract 为边界。

---

# 2. Message Bus v2 的内部结构

建议逻辑上分成五个子系统：

```text
message_bus_v2/

    control/
        Source Registry
        Default Profile
        Ticker Binding
        Configuration Service

    acquisition/
        SourceAdapter Contract
        Global Poll Scheduler
        Poll Runtime State

    processing/
        Raw Intake
        Identity / Dedupe
        Content Validator
        Content Enricher
        Standardizer

    stream/
        Immediate Publisher
        Buffered Publisher
        Durable Stream
        Consumer Cursor

    operations/
        Poll Health
        Alert Engine
        Status / Metrics
```

这五个模块属于同一个 Message Bus。

Crawler Plane 后续只是提供一种新的 `SourceAdapter` 实现，不进入这些内部职责。

---

# 3. Source Registry

## 3.1 Source 是全局对象

任何数据源在被 ticker 使用之前必须完成注册。

Source 只区分：

```text
api
crawler
```

删除：

```text
media
social

by_ticker
by_parameter
```

当前 v1 的 `media/social + by_ticker/by_parameter` 本质上是旧 collector 架构形成的分类。

v2 不再通过类别推断消息应该怎样处理。

一个 Source Definition 至少表达：

```text
source_id
display_name
kind                    api | crawler
adapter_ref

parameter_schema
default_parameters

default_polling_config
default_streaming_config

scheduler_group
scheduler_constraints
```

其中：

### `source_id`

Message Bus 内部稳定 identity。

例如：

```text
benzinga_news
finnhub_company_news
stocktwits_messages
micron_ir
sec_company_filings
```

### `display_name`

当原始消息本身没有业务 `source` 时，用作 StandardMessage `source` 的 fallback。

### `parameter_schema`

完全由该 source 定义。

Message Bus 不理解参数业务含义。

例如：

```text
Benzinga:
    tickers
    channels
    page_size

X Search:
    query
    usernames

Crawler:
    section_url
    document_filter
```

Message Bus 只负责：

```text
schema validation
storage
query
update
传递给 SourceAdapter
```

因此以后 Agent/API 可以统一执行：

```text
get_source_schema(source_id)
get_ticker_source_config(ticker, source_id)
update_ticker_source_parameters(...)
```

而不需要 Message Bus 为每个 provider 写专有配置接口。

---

# 4. Default Monitoring Profile

Message Bus Core 本身不硬编码任何 provider。

实际部署时维护一个：

```text
Default Monitoring Profile
```

例如：

```text
benzinga_news
finnhub_company_news
stocktwits_messages
...
```

并携带默认：

```text
source_parameters
polling_config
streaming_config
```

创建新 ticker：

```text
start_monitoring("MU")
```

执行：

```text
Default Profile
      ↓
materialize
      ↓
MU Ticker Source Bindings
```

此后 MU 的配置独立存在。

所以 global default 是：

> 新 ticker 的模板。

而不是：

> 所有 ticker 永久动态继承的父配置。

以后修改 Default Profile 不自动修改已经运行中的 ticker。

---

# 5. Ticker Source Binding

Message Bus 的业务基本粒度为：

```text
ticker × source
```

例如：

```text
MU × benzinga_news
MU × stocktwits_messages
MU × micron_ir

NVDA × benzinga_news
NVDA × nvidia_ir
```

每个 binding 保存三类配置：

```text
source_parameters
polling
streaming
```

## source_parameters

Source 自定义。

## polling

Message Bus 通用。

初版建议：

```text
enabled
target_interval
active_windows
```

## streaming

Message Bus 通用：

```text
mode = immediate | buffered

buffer:
    max_items
    max_wait
```

配置更新时间和 live ingestion boundary 分开保存。

v1 目前把 `binding.updated_at` 同时当配置修改时间和 live watermark，因此只修改配置也可能改变历史过滤边界。

v2 不再继承这个行为。

---

# 6. Polling 时间窗

Polling Scheduler 属于 Message Bus，但：

> **Polling 不意味着全天候运行。**

每个 binding 的 PollingConfig 可以定义 generic：

```text
active_windows
```

例如概念上：

```text
timezone = America/New_York

Monday:
    07:00 - 18:00

Tuesday-Friday:
    07:30 - 18:00
```

这里没有：

```text
pre_market
market_hours
after_hours
```

这样的业务枚举。

Message Bus 只理解：

> 当前时间是否位于允许 polling 的时间窗口。

因此以后业务重新定义：

```text
07:00 → 06:30
18:00 → 20:00
```

只是配置变化，不修改 Scheduler 代码。

某些 source 也可以：

```text
24/7
```

另一些付费 source：

```text
只在指定窗口
```

Ticker 和 source 可以拥有不同 polling windows。

窗口关闭时：

```text
binding = enabled
但 scheduler = inactive
```

不会发送请求。

窗口重新开启时也不会重置 Source identity / ingestion baseline。

换句话说：

```text
暂停 polling
≠
重新 bootstrap
```

否则休市期间产生的公告会被错误认为是历史数据。

---

# 7. Global Poll Scheduler

这是 v2 最重要的基础设施之一。

v1 当前逐 binding polling，并且 due 基本由：

```text
last_attempt + poll_interval
```

决定；多个 poller 同时运行还会造成重复 provider 请求。

v2 不再使用：

```text
每个 ticker 自己的 interval timer
```

而是使用一个：

> **Global Poll Scheduler**

统一查看全部 ticker 的全部 active binding。

---

# 8. target interval 不是固定 clock

假设：

```text
MU Benzinga = 60s
NVDA Benzinga = 60s
AMD Benzinga = 60s
...
```

`60s` 的含义应该是：

> 这个 binding 长期平均希望约每 60 秒 poll 一次。

而不是：

```text
ticker 启动时间 + 60n 秒
```

Scheduler 可以在允许容差内动态移动实际请求时间。

默认可以采用：

```text
target interval = 60s
tolerance = ±10%
```

即合理 polling gap：

```text
54s ~ 66s
```

具体 `60s` 本身仍然是配置，可以修改成：

```text
30s
90s
300s
...
```

---

# 9. 跨 ticker 的统一错峰

如果有 10 个 ticker 都使用同一个 Benzinga Source：

```text
MU
NVDA
AMD
AVGO
TSLA
...
```

而目标 interval 都约 60 秒，

Scheduler 不应：

```text
12:00:00
MU
NVDA
AMD
AVGO
TSLA
...
一起 request
```

而应尝试形成：

```text
12:00:00 MU
12:00:06 NVDA
12:00:12 AMD
12:00:18 AVGO
...
12:00:54 XXX
12:01:00 MU
```

也就是：

> 每个 ticker 仍约 60 秒一次，但 provider 接收到的是稳定分散的请求流。

新 ticker 加入时：

```text
不是立即创建一个永久 phase
```

而是：

```text
Scheduler 将它插入现有 scheduling cycle
→ 重新调整后续 slot
```

因此系统始终保留重新平衡能力。

---

# 10. Scheduler Group

不同 Source 可能实际上共享同一个 provider 限流。

例如未来：

```text
tikhub_x_search
tikhub_x_user_posts
```

虽然是两个 Source，但可能共享 TikHub 的 API quota。

因此 Source Registry 中需要一个基础设施属性：

```text
scheduler_group
```

默认：

```text
scheduler_group = source_id
```

但可以显式让多个 source 共用：

```text
scheduler_group = tikhub
```

Crawler Source 后续也可以：

```text
scheduler_group = ir.micron.com
```

从而让 Message Bus 对同一个 provider/domain 统一错峰。

---

# 11. Scheduler Constraints

Source registration 可以声明 provider 层面的运行约束，例如：

```text
minimum_request_gap
max_concurrency
```

这些不是 ticker 的业务参数。

它们表达：

> SourceAdapter 的安全运行边界。

Scheduler 的任务是同时满足：

```text
binding target interval
active window
scheduler-group spacing
max concurrency
```

如果全部 binding 的需求已经超过 provider 容量，例如：

```text
20 ticker
×
60 sec interval

但 provider 只能 12 req/min
```

Scheduler 不应该假装自己还能满足所有 target。

此时：

```text
effective interval
```

会不可避免地扩大，同时产生：

```text
scheduler_capacity alert
```

而不是静默高频撞 provider。

---

# 12. 推荐的 Scheduler 核心算法

每个 active binding 维护：

```text
target_due_at
next_dispatch_at
```

逻辑：

```text
1. 找到当前 active window 中 enabled bindings

2. 根据 target_interval 计算下一轮 target_due

3. 为 scheduler_group 收集所有 due candidates

4. 在允许 tolerance 内为它们分配 request slot

5. 满足 minimum_request_gap / concurrency

6. 到达 slot 后 dispatch SourceAdapter.poll()

7. 下一 target_due 基于理论 schedule 推进
   而不是直接使用 request 完成时间 + interval
```

这样避免：

```text
一次请求慢 4 秒
→ 下一轮整体再延后 4 秒
→ 长期持续 drift
```

如果 Scheduler 本身宕机 10 分钟：

```text
不会补执行十次 missed poll
```

恢复后：

```text
执行一次当前 poll
→ 重新加入当前 schedule
```

这与 source message 的 historical catch-up 是两个问题。

---

# 13. SourceAdapter Contract

所有数据源最终必须满足同一个 contract。

概念上：

```python
class SourceAdapter(Protocol):

    async def poll(
        self,
        context: PollContext,
    ) -> list[RawMessageInput]:
        ...
```

`PollContext` 至少提供：

```text
ticker
source_id
source_parameters
checkpoint
```

Adapter 只负责：

```text
调用外部 source
解析 provider response
产生 RawMessageInput
更新必要 checkpoint
```

它不负责：

```text
Message Bus dedupe
StandardMessage ID
stream
consumer
```

因此：

```text
API adapter
crawler adapter
```

对于 Message Bus 完全等价。

---

# 14. RawMessage v2

按照你最新定义，业务内容字段固定为：

```text
title           optional
body            required
source          required
url             required
published_at    required
```

这里建议明确：

## title

可以为空。

## body

不可为空。

Adapter 获取消息时：

```text
provider 有正文
→ body = provider body

只有 summary
→ body = summary
```

之后 Content Enrichment 如果成功获取完整正文：

```text
StandardMessage.body = full body
```

如果没有成功获得有效全文：

```text
StandardMessage.body = RawMessage.body
```

也就是 summary fallback。

因此 StandardMessage 永远有可读 body。

## source

是业务意义上的消息来源。

例如：

```text
source_id = benzinga_news
source = Reuters
```

如果 Benzinga 返回 source=Reuters，就保留 Reuters。

如果 provider 没有提供：

```text
source = Source Registry.display_name
```

例如：

```text
source = Micron Investor Relations
```

`source` 和 `source_id` 是两个完全不同的概念：

```text
source
→ 下游业务信息

source_id
→ Message Bus infrastructure lineage
```

## url

不可为空。

Adapter 必须提供 canonical source URL / permalink。

## published_at

不可为空。

它代表：

> Source 声称该消息发布的时间。

不使用：

```text
collected_at
```

偷偷替代 published_at。

如果 adapter 无法得到发布时间，则该 RawMessage 不满足 contract，应进入 acquisition/validation failure，而不是产生时间语义错误的 StandardMessage。

这点尤其重要，因为当前 v2 Runtime 对 source time 的判断已经依赖 published time。

---

# 15. RawMessage 的基础设施字段

在五个业务字段之外，Message Bus 增加运行所需 lineage：

```text
raw_message_id
schema_version

ticker
source_id
binding_id

external_id          optional
source_item_key

raw_payload
raw_hash

collected_at
```

其中：

### external_id

provider 原生 ID。

例如：

```text
Benzinga article id
Stocktwits message id
IR document id
```

### source_item_key

Message Bus 用来判断：

> 现实中是不是同一个 source item。

优先：

```text
external_id
```

否则：

```text
canonical url
```

因为 v2 已要求 URL 必须存在，因此不再需要把 raw payload hash 当作 item identity。

Raw hash 只表示：

> 本次 provider payload 是否发生变化。

---

# 16. Dedupe 模型

v1 当前 dedupe 是：

```text
source-global
```

所以同一个 provider item 被 MU 先写入后，NVDA 可能无法形成自己的 Event。

v2 改成两个 identity：

```text
Source Item Identity
source_id + source_item_key
```

以及：

```text
Ticker Message Identity
ticker + source_id + source_item_key + content_hash
```

因此：

```text
同一 Reuters 新闻
```

可以同时：

```text
MU → Stream
NVDA → Stream
AMD → Stream
```

而 MU 自己重复 poll 到同一条内容不会重复 stream。

---

# 17. Revision Detection

同一个：

```text
source_item_key
```

再次出现：

### content_hash 相同

```text
duplicate
→ no new StandardMessage
→ no stream
```

### content_hash 不同

```text
revision
→ create new StandardMessage
→ publish new StreamItem
```

`content_hash` 应基于最终标准化后的业务内容，而不是整个 raw payload。

否则例如：

```text
like_count
view_count
tracking metadata
```

变化也会错误产生 update。

建议 hash：

```text
normalized title
normalized body
normalized source
canonical url
published_at
```

从而对真正的内容变化敏感。

---

# 18. Content Materialization Pipeline

完整流程：

```text
SourceAdapter
      ↓
RawMessageInput
      ↓
Contract Validation
      ↓
Durable Enrichment Intake Queue
      ↓
Basic normalization
      ↓
body → summary → 空串 fallback
      ↓
全局 Content Enrichment Hub（并发 8、统一域名限流）
      ↓
成功覆盖正文；失败保留 fallback；瞬态错误最多 retry 一次
      ↓
final normalization
      ↓
source_item identity / identity_key / content_hash
      ↓
Raw persistence / exact dedupe / revision detection
      ↓
Immutable StandardMessage
```

v1 的正文补全实现已经包含 direct fetch、JSON-LD、trafilatura 和 HTML fallback，这部分非常适合作为 v2 generic Content Enricher 的实现来源。

但 v2 不再：

```text
先 publish
再 enrichment
再修改 event payload
```

因为当前 v1 的这种行为导致 event stream 并非真正 immutable。

v2 改成：

> enrichment 完成后才形成最终 StandardMessage。

---

# 19. StandardMessage v2

业务字段仍然只有：

```text
title           optional
body            required
source          required
publisher_name  optional（明确发布方）
resolved_domain optional（最终 URL 域名）
url             required
published_at    required
```

基础设施字段建议：

```text
standard_message_id
schema_version

raw_message_id
ticker
source_id
binding_id
source_item_key

content_hash

collected_at
standardized_at
```

StandardMessage 一旦建立：

> immutable。

任何正文 enrichment 都发生在它建立之前。

如果未来现实 source item 被修改：

```text
不是修改旧 StandardMessage
```

而是：

```text
生成新的 StandardMessage revision
```

---

# 20. Raw persistence 和 publication transaction

RawMessage 应优先持久化。

因此：

```text
RawMessage 存在
但 processing 尚未完成
```

是允许存在的状态。

这是 acquisition truth。

真正不能出现的是：

```text
StandardMessage 已经产生
但不知道它到底有没有成功 publish
```

所以 StandardMessage publication 必须事务化。

Immediate 模式：

```text
BEGIN

insert StandardMessage
insert StreamItem
insert StreamItemMember

COMMIT
```

Buffered 模式：

```text
BEGIN

insert StandardMessage
insert BufferEntry

COMMIT
```

这样进程 crash 后不会出现 v1 当前那种：

```text
standard 已写
event 未写
```

无法知道是否应该重放的问题。v1 当前 raw → standard → event 分成独立 transaction，确实存在这种 partial state。

---

# 21. Immediate / Buffered Publication

这里正式命名为：

```text
publication_mode
```

而不是程序意义上的 sync / async。

## Immediate

```text
StandardMessage
      ↓
1 StreamItem
```

一个 StreamItem 包含一个 StandardMessage。

适合：

```text
IR
government policy
news
filing
```

等低频但单条重要的信息。

## Buffered

```text
StandardMessage A
StandardMessage B
StandardMessage C
...
      ↓
Persistent Buffer
      ↓
trigger
      ↓
1 StreamItem
```

trigger：

```text
count >= max_items
OR
oldest_wait >= max_wait
```

例如：

```text
10 messages
OR
30 min
```

达到任意一个即可 flush。

---

# 22. Buffer 粒度

Buffer 应为：

```text
ticker + binding
```

即：

```text
MU + Stocktwits
```

和：

```text
MU + X Search
```

是两个 buffer。

不会混合成：

```text
MU social batch
```

因为 v2 已经不存在 social 这个类型。

Buffered StandardMessage 在进入 buffer 时已经持久化。

因此即使：

```text
收到 7 条
→ 进程 crash
→ restart
```

7 条仍然存在。

时间 trigger 同样从原始 oldest buffered time 继续计算。

---

# 23. StreamItem

StandardMessage 是：

> source message。

StreamItem 是：

> Message Bus 对某 ticker 发布的一次 delivery unit。

建议：

```text
stream_item_id
schema_version

ticker
stream_offset
streamed_at

member_count
```

成员关系单独保存：

```text
stream_item_id
standard_message_id
member_order
```

因此：

Immediate：

```text
StreamItem 101
members = [SM1]
```

Buffered：

```text
StreamItem 102
members = [SM2 ... SM11]
```

避免像 v1 一样：

```text
StandardMessage full JSON
再次复制进 event payload
```

从而大幅减少正文重复存储。

消费者读取 StreamItem 时，Message Bus service 可以一次性 materialize 成：

```text
StreamItem
+
StandardMessages[]
```

给下游。

---

# 24. Ticker-local Stream

v1 的 `stream_offset` 是整个 SQLite 的全局 AUTOINCREMENT，只表示全库 append 顺序。

v2 的 stream 语义应为：

```text
MU
1
2
3
4

NVDA
1
2
3
4
```

即：

> ticker-local monotonic offset。

Transport ordering 使用：

```text
stream_offset
```

业务时间使用：

```text
published_at
```

两者不混用。

晚到消息完全可以：

```text
offset = 105
published_at = 09:58

offset = 104
published_at = 10:05
```

消费者 cursor 始终按照 offset。

事件分析可以按照 published_at。

---

# 25. Consumer Cursor

彻底删除：

```text
StreamItem.consumed = bool
```

v1 的单一 consumed 位实际上只是 Persistent Runtime 的消费状态，并不能支持真正独立的 consumer。

v2 使用：

```text
consumer_id
ticker
committed_offset
```

例如：

```text
persistent_runtime_v2
MU
1532
```

Message Bus 提供：

```text
read_after(
    consumer_id,
    ticker,
    limit
)
```

消费者 durable acceptance 后：

```text
commit_offset(...)
```

第一版采用：

> at-least-once delivery + downstream idempotency。

不需要第一阶段直接实现 Kafka 式 consumer group / partition rebalance。

当前 workflow 中 CDECR 并不应该变成第二个竞争 Message Bus consumer；当前设计仍是 Runtime 完成消息新旧判断，再向 CDECR 提供已经确认的新消息。

因此初始 production consumer 可以只有：

```text
persistent_runtime_v2
```

但底层 contract 不再假设永远只有一个消费者。

---

# 26. Poll Runtime State

每个 ticker/source binding 维护独立运行状态。

建议至少：

```text
last_attempt_at
last_success_at
last_failure_at

failure_since
consecutive_failures

last_error

target_due_at
next_dispatch_at

checkpoint

last_latency
```

这里：

```text
PollingConfig
```

表达用户/Agent 希望系统怎么运行。

```text
PollRuntimeState
```

表达现实中实际运行成什么样。

两者彻底分离。

---

# 27. Poll Health → Alert Engine

Poll health 不只是 Dashboard 信息。

它需要成为：

> operational detection mechanism。

例如：

```text
MU micron_ir
连续失败
```

第一次：

```text
failure_since = 10:00
```

之后继续失败。

到：

```text
10:30
```

如果配置：

```text
alert_after = 30m
```

则生成：

```text
polling_unhealthy
```

Operational Alert。

初期 Alert 至少需要：

```text
alert_id
alert_type

source_id
binding_id / affected ticker

opened_at
last_observed_at
status
details
```

同一个故障不应该每 poll 一次生成新 alert。

而是：

```text
OPEN
↓
持续更新 last_observed
↓
source 恢复成功
↓
RESOLVED
```

以后 O4 可以监听：

```text
OPEN operational alert
```

并启动 repair workflow。

Message Bus v2 当前只需要做到：

```text
detect
persist
query
resolve
```

暂时不实现 O4 wake-up。

---

# 28. Alert 去风暴

因为 Scheduler 是跨 ticker 的，一个 provider 故障时可能出现：

```text
MU Benzinga failed
NVDA Benzinga failed
AMD Benzinga failed
...
```

不应该因此生成几十个完全相同的告警。

Alert Engine 应能够识别：

```text
同 source / scheduler_group
短时间大量 bindings 同类失败
```

并形成：

```text
source-level operational alert
```

其中记录：

```text
affected_bindings / affected_tickers
```

单 ticker 独立异常仍可以形成 binding-level alert。

这样后续 O4 收到的故障信号会干净很多。

---

# 29. Bootstrap

新 ticker 第一次启动时：

```text
Default Profile
↓
Ticker Bindings
↓
Source bootstrap
```

第一次 provider poll 可能返回大量历史数据。

Message Bus 要区分：

```text
bootstrap observation
```

和：

```text
live increment
```

初始历史内容用于建立：

```text
source_item baseline
checkpoint
```

默认不进入 live stream。

以后真正新增或 revision：

```text
才 publish
```

这保留了 v1 很重要的：

> 新 source 启用时不把 provider 历史窗口整体灌入实时 stream

这一性质。

但实现方式不再依赖 `binding.updated_at`。

---

# 30. 暂停与恢复

需要明确三个不同动作：

```text
polling window closed
binding disabled
ticker monitoring stopped
```

它们都不是：

```text
delete history
```

也不自动改变：

```text
source_item baseline
consumer offset
```

重新开启时继续现有状态。

只有明确执行：

```text
reset/bootstrap
```

才重新建立 ingestion baseline。

这能避免 v1 当前通过 binding 删除/更新时间影响 live view 的隐式行为。

---

# 31. Persistence Schema

第一版仍可以使用 SQLite，不需要因为叫 Message Bus 就立即引入 Kafka/Redis。

核心逻辑正确性比 broker 技术更重要。

建议使用新的独立 DB，例如：

```text
message_bus_v2.sqlite3
```

并启用：

```text
WAL
busy_timeout
transactional writes
```

核心表：

| 表                                 | 责任                                   |
| --------------------------------- | ------------------------------------ |
| `sources`                         | 全局 Source Registry                   |
| `monitoring_profiles`             | 默认 ticker profile                    |
| `ticker_source_bindings`          | ticker-specific desired config       |
| `poll_states`                     | polling runtime state                |
| `operational_alerts`              | source/binding health alert          |
| `raw_messages`                    | 原始 source observation                |
| `standard_messages`               | immutable standardized messages      |
| `buffer_entries`                  | buffered publication pending members |
| `stream_items` / `stream_members` | ticker durable stream                |
| `consumer_offsets`                | per-consumer ticker cursor           |

这里没有：

```text
media tables
social tables
Stocktwits special DB
```

所有 source 走相同基础设施。

---

# 32. Source / Config API

虽然 O4 尚未开发，但 Message Bus v2 从第一天就应该提供稳定的 machine-facing service contract。

至少覆盖：

```text
list_sources
get_source

get_default_profile

start_ticker
get_ticker_config

get_ticker_source_binding
update_ticker_source_parameters
update_ticker_polling_config
update_ticker_streaming_config

enable_ticker_source
disable_ticker_source

get_poll_status
list_operational_alerts

read_stream
commit_consumer_offset
```

Dashboard、普通 API、未来 Agent tool 都调用同一个 application service。

不直接读内部 SQLite table。

---

# 33. v1 可复用内容

这次不是重构 v1 package，而是：

```text
新建 v2
+
提取可复用能力
```

优先复用：

### 正文补全

当前：

```text
media_enrichment.py
```

中的：

```text
direct fetch
JSON-LD articleBody
trafilatura
HTML fallback
content hash
domain pacing
```

可以抽取成新的 generic content enrichment utility。

### provider normalizer

现有 Benzinga/Finnhub/Stocktwits/TikHub/RSS 字段识别逻辑可以迁移到相应 v2 adapter。

### exact identity tests

保留 provider ID / canonical URL 优先的思路，但修改为 ticker-aware identity。

### historical flood tests

保留行为要求，替换 watermark 实现。

### persistence restart tests

继续要求：

```text
restart
→ stream intact
→ consumer offset intact
→ buffered messages intact
→ poll state intact
```

---

# 34. v1 明确不迁移的语义

以下直接视为 legacy：

```text
media/social

by_ticker/by_parameter

hard-coded source catalog

binding.updated_at watermark

source-global first-ticker-wins dedupe

mutable event payload

global stream offset as primary ticker cursor

single consumed bool

RuntimeScheduler owning source polling

Stocktwits second polling state universe

raw/standard/event independent publication transactions
```

这些恰好也是当前 v1 审计里已经暴露出的主要正确性和运维问题。

---

# 35. 推荐开发顺序

## Phase 1 — Contracts

先冻结：

```text
SourceDefinition
TickerSourceBinding

PollingConfig
StreamingConfig

RawMessage
StandardMessage
StreamItem

PollRuntimeState
ConsumerOffset
OperationalAlert

SourceAdapter
```

这是最重要的一步。

Schema 没稳定之前先不写 scheduler。

---

## Phase 2 — Repository / Control Plane

实现：

```text
SQLite v2 schema

Source Registry

Default Monitoring Profile

Ticker Source Binding

Config Service

consumer offsets
```

验收：

```text
create ticker
→ default profile materialized

update source parameters
→ schema validation

restart
→ config intact
```

---

## Phase 3 — Global Poll Scheduler

单独开发并压测 Scheduler。

先用 fake adapter，不接真实 provider。

测试：

```text
1 ticker

10 tickers

50 tickers

多个 scheduler_group

动态新增 ticker

动态删除 ticker

修改 60s → 30s

active window open/close

provider min spacing

scheduler capacity insufficient

process restart
```

核心 acceptance：

```text
每个 binding 实际平均 cadence
≈ configured target

允许误差
≤ configured tolerance

同 scheduler_group requests
平滑分散

新 ticker 加入后
能够重新 phase

不存在 request burst
```

这部分建议作为 Message Bus v2 最独立的一组测试。

---

## Phase 4 — Enrichment → Raw → Standard Pipeline

实现：

```text
durable enrichment intake queue
body/summary fallback
global content enrichment
source_item identity / content_hash
Raw persistence / ticker dedupe / revision detection

StandardMessage finalization
```

迁移现有正文 extraction 能力。

重点测试：

```text
title null allowed

body/summary 均可为空，最终 Raw body 为字符串

source fallback

url required

published_at required

summary fallback

正文 enrichment 覆盖 body

duplicate

same URL changed content

same message for multiple tickers
```

---

## Phase 5 — Durable Stream

实现：

```text
Immediate publisher

Buffered publisher

ticker-local offset

StreamItem membership

consumer cursor
```

重点测试：

```text
immediate = 1 message/item

buffer count trigger

buffer time trigger

restart with partially filled buffer

ticker isolation

consumer restart

multiple consumer cursors
```

---

## Phase 6 — Poll Health / Alerts

实现：

```text
failure_since
consecutive_failures

long-failure threshold

OPEN alert

deduplicated repeated failure

automatic RESOLVED

scheduler capacity alert
```

这一步完成以后，未来 O4 才拥有可靠的 machine-readable repair trigger。

---

## Phase 7 — Legacy Source Migration

依次把当前数据源改成 v2 SourceAdapter：

```text
Benzinga
Finnhub
Stocktwits
TikHub X
RSS
```

Stocktwits 不再保留第二套特殊 Message Bus。

如果 Stocktwits 自己仍需要：

```text
checkpoint
hot polling
```

则这些应该被表达为 adapter/source-specific acquisition state，而不是独立的 bus architecture。

---

## Phase 8 — Runtime v2 Handoff

最后才连接：

```text
Message Bus v2 StreamItem
        ↓
Runtime-side Adapter
        ↓
Persistent Runtime v2
```

Message Bus 不 import Runtime schema。

Runtime adapter 负责：

```text
StreamItem
+
StandardMessage[]
        ↓
Runtime SourceMessage input
```

然后把当前：

```text
Message Bus → Scheduler → Runtime v2
```

缺少的真实端到端 regression test 补上。当前代码虽然已经接通这条路径，但现有测试并没有把真实 EventStreamItem、Scheduler 和 Runtime v2 Case 放进同一个专门的端到端测试。

---

# 36. 第一版明确的非目标

为了避免 Message Bus v2 再次膨胀，第一版不需要解决：

```text
语义去重
消息重要性
相关性判断
事件识别
新旧现实事件判断

Kafka cluster
distributed partition rebalance
exactly-once broker semantics

Crawler creation
Crawler certification
Crawler repair

O4 source research
O4 agent orchestration
```

Message Bus v2 第一版完成的标准就是：

> **任意已注册 SourceAdapter 都能在正确的 ticker、正确的时间窗口、正确的全局调度节奏下稳定获取消息；消息经过统一 Raw → Standard materialization 后，以 immediate 或 buffered 方式进入 durable ticker stream；重复消息不重复触发，内容更新可识别，重启不丢数据，消费者可独立恢复，长期 source failure 可以形成机器可读告警。**

---

# 37. 最终系统边界

最终 Message Bus v2 应该稳定在这一层：

```text
                    EXTERNAL SOURCES

             API                    Crawler
              │                        │
              └──────── SourceAdapter ─┘
                           │
                           ▼
                 Global Poll Scheduler
                           │
                           ▼
                       RawMessage
                           │
                    durable intake
                           │
                           ▼
                 Identity / Dedupe
                           │
                           ▼
              Validation / Enrichment
                           │
                           ▼
                Immutable StandardMessage
                           │
                  ┌────────┴────────┐
                  ▼                 ▼
             Immediate          Buffered
                  │                 │
                  │          Persistent Buffer
                  │                 │
                  └────────┬────────┘
                           ▼
                   Ticker Stream
                           │
                           ▼
                    Consumer Cursor
                           │
                           ▼
                  Runtime-side Adapter
                           │
                           ▼
                      DoxAgent v2


       ┌─────────────────────────────────────┐
       │         Message Bus Control         │
       │                                     │
       │ Source Registry                     │
       │ Default Monitoring Profile          │
       │ Ticker Bindings                     │
       │ Polling Windows                     │
       │ Global Scheduling                   │
       │ Poll State / Health                 │
       │ Operational Alerts                  │
       └─────────────────────────────────────┘
```

这版方案里，我认为真正需要优先冻结的不是具体 Python class，而是 **八个核心契约**：

```text
SourceDefinition
TickerSourceBinding
PollingConfig
RawMessage
StandardMessage
StreamItem
PollRuntimeState
SourceAdapter
```

只要这八个边界先定准，后面的 SQLite schema、Scheduler implementation、现有 provider migration、Crawler Plane 和 O4 都会比较自然；如果八个 contract 尚未稳定就先写 implementation，很容易再次出现 v1 那种“一个后来加入的小功能改变了整个 Bus 语义”的情况。
