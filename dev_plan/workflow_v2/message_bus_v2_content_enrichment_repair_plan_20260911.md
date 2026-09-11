# DoxAgent V2 全局正文补全中台修复方案

日期：2026-09-11  
状态：已实施（本地代码与定向验证完成，未部署远端）  
范围：Message Bus V2 的正文补全接入、全局并发/域名限流、一次性短时重试、字段兼容与必要测试  
不在范围：正文语义摘要、相关性判断、事件识别、Crawler 开发、额外审计体系、全局工作流重构

实施记录：队列 schema/claim/lease 与 Raw finalization 同库实现于
`message_bus_v2.repository`，避免再建一层 repository 抽象；worker loop 合并在中台 `cli.py`，
全局 limiter 复用 `monitoring.media_enrichment.DomainFetchController`。该文件布局与下文最初清单略有
收敛，但运行边界、持久化顺序、并发、retry、字段和非阻塞语义不变。定向测试覆盖 8 并发与
溢出排队、poll 只入队、429 单次 retry、180 秒过期、body/summary fallback、黑名单无网络请求、
publisher/domain 兼容字段；Compose 合并配置已通过。未执行远端部署或真实网站抓取验收。

## 1. 结论

本轮将正文补全从 `message_bus_v2` 内逐消息创建的临时 materializer，重构为一个由所有 ticker/binding 共用的全局 `Content Enrichment Hub`：

- 所有 Message Bus producer 将待处理消息写入同一持久队列；
- 单一正文补全 worker pool 全局最多同时执行 8 个任务；
- 所有任务共用同一组按目标域名划分的 limiter；
- 超过 8 个的任务留在队列中，不占用协程、不丢弃；
- 默认所有 source 都经过中台，短消息 source 通过 SourceDefinition 黑名单明确跳过；
- 可重试网络失败最多追加一次 retry，首次进入队列后 3 分钟为绝对截止时间；
- 补全成功才覆盖正文；失败保留原正文或 summary fallback；
- `publisher_name` 与 `resolved_domain` 分开保存，原有 `source` 字段继续兼容下游；
- 不新增审计事件，不把正文失败升级为 Message Bus 或 workflow 的全局阻塞。

## 2. 必须明确的约束修订

### 2.1 用户指定链路与原 V2 设计文档存在冲突

现有设计文档 `message_bus_v2.md` 明确写的是：

```text
Raw durable intake
  → Identity / Dedupe
  → Validation / Enrichment
  → StandardMessage
```

本轮指定的是：

```text
enrichment
  → identity/hash
  → Raw persistence/dedupe
```

如果直接按后一顺序在内存中执行，进程在 enrichment 期间退出时消息仍未落盘，当前已经确认的消息丢失问题会继续存在。因此，本方案采用以下可落地解释：

```text
轻量、耐久 Enrichment Intake Queue
  → enrichment
  → identity/hash
  → 业务 Raw persistence/dedupe
  → StandardMessage / Stream
```

其中 queue row 只是工作状态，不是业务 RawMessage，也不是审计记录。这样既保持指定的业务链路顺序，又保证网络请求前已有可恢复落点。

### 2.2 “5 个 ticker、5 个 Message Bus”的实际模型

当前 V2 架构是一个 Message Bus worker 管理多个 ticker/source binding，并不是每个 ticker 一个 Message Bus。方案以真实架构为基准：

- 多个 ticker、source、binding 共用一个 Enrichment Hub；
- 即使未来运行多个 Message Bus producer 进程，它们也只负责写同一个 queue；
- 正式部署只运行一个 enrichment worker service，其内部 worker pool 并发为 8，因此并发和域名限流是全局的，而不是单进程各算一份。

## 3. 当前问题与修复目标映射

| 当前问题 | 修复目标 |
| --- | --- |
| 每条消息单独调用 `enrich_media_records([record])`，每次创建新的域名 limiter | 建立进程生命周期内唯一的 `DomainLimiterRegistry` |
| 同一 poll 内逐条串行补全 | 全局 worker pool 并发 8，余量进入持久队列 |
| 正文补全发生在 Raw 之前且没有 intake 落点 | enrichment 前先写 durable queue |
| duplicate 在补全后才发现，重复访问目标站点 | queue 层使用 transport idempotency key 合并同一待处理输入；业务 identity/hash 仍在 enrichment 后计算 |
| `RawMessageInput.body` 必填非空，真正无正文消息进不了补全 | body/summary 均可空，统一做正文优先、summary fallback |
| 只认 `metadata.provider` 白名单 | 默认 enrich，按 SourceDefinition/source_id 黑名单跳过 |
| transient failure 没有正式重试队列 | 仅一次 retry，绝对 deadline 不超过 180 秒 |
| 成功后把 `source` 覆盖成域名 | 保存 `publisher_name` 与 `resolved_domain`，`source` 保持 publisher 兼容值 |
| materializer 异常会丢消息且 PollState 仍可能显示 succeeded | 异常转换为 item-local fallback，queue 最终化后继续处理其他任务 |
| bootstrap 历史也同步抓全文并阻塞 poll | poll 只入队；bootstrap 是否需要补全由明确策略决定，不阻塞采集循环 |

## 4. 目标架构

```text
                         ┌─────────────────────────┐
Ticker A bindings ───────┤                         │
Ticker B bindings ───────┤  Enrichment Intake     │
Ticker C bindings ───────┤  Queue (SQLite)         │
Ticker D bindings ───────┤                         │
Ticker E bindings ───────┤                         │
                         └───────────┬─────────────┘
                                     │ claim <= 8
                                     ▼
                         ┌─────────────────────────┐
                         │ Content Enrichment Hub  │
                         │ global semaphore = 8    │
                         │ shared domain limiters  │
                         └───────────┬─────────────┘
                                     │
                 ┌───────────────────┴───────────────────┐
                 │ success / blacklist / final fallback │
                 ▼                                       ▼
       identity + content hash                 one retry if transient
                 │                              and deadline permits
                 ▼                                       │
       Raw persistence / dedupe  ◄───────────────────────┘
                 │
                 ▼
       StandardMessage / Stream / Runtime V2
```

### 4.1 模块边界

新增独立包：

```text
src/doxagent/content_enrichment/
├── schema.py       # Job、policy、result、failure classification
├── repository.py   # queue enqueue/claim/requeue/finalize
├── limiter.py      # 全局 semaphore 与按域名 limiter
├── extractor.py    # 从 monitoring.media_enrichment 迁移的通用提取能力
├── service.py      # 处理单 job、fallback、retry 决策
├── worker.py       # 最多 8 个并发任务的持续循环
└── cli.py          # run-worker / run-once / status
```

Message Bus 只负责：

1. 把 adapter 产出的消息做宽松 normalization；
2. 连同 ticker、source、binding、poll snapshot 写入 queue；
3. 由 enrichment worker 回调 Message Bus finalization service；
4. 在补全完成后计算正式 identity/content hash、持久化 Raw、去重并发布。

Content Enrichment Hub 不负责：

- ticker polling cadence；
- Message Bus 业务 dedupe/revision；
- Runtime 路由；
- 相关性或事件判断；
- 新增审计或告警门禁。

## 5. Queue 设计

### 5.1 存储位置

在现有 Message Bus SQLite 中新增 `content_enrichment_jobs`。所有 producer 和唯一 enrichment worker 使用同一个 `/data/bus/bus.sqlite3`，不再引入第二个业务数据库，也避免跨库提交。

建议字段：

```text
job_id
intake_key                 # queue 级幂等键，不是业务 identity_key
poll_run_id
ticker
source_id
binding_id
source_definition_version
bootstrap_suppressed
input_json                 # RawMessageInput normalized snapshot
streaming_config_json
status                     # QUEUED | RUNNING | RETRY_WAIT
attempt_count              # 0/1/2，最多两次实际网络尝试
created_at
not_before
deadline_at                # created_at + 180 seconds
lease_until
last_error_code
last_error_message
```

不保留 DONE/FAILED 历史队列行：最终正文状态已经进入 Raw/Standard metadata 后立即删除 queue row。该表是运行队列，不是新增审计。

### 5.2 Queue 级幂等

业务 identity/hash 按要求仍在 enrichment 后计算。为了避免多个 producer 对同一上游输入同时发起补全，queue 仅使用 transport idempotency key：

```text
sha256(ticker + source_id + binding_id + external_id/source_item_key/canonical_input_url)
```

同一个 active queue job 再次入队时只刷新 `last_seen_at`（可放入 `input_json` 的运行字段），不创建第二个网络任务。任务完成并删除后，未来再次出现的消息仍可重新进入业务 dedupe/revision 流程。

### 5.3 Claim 与崩溃恢复

- worker 每轮原子 claim 最多 8 条 `QUEUED/RETRY_WAIT` 且 `not_before <= now` 的任务；
- claim 后设为 `RUNNING` 并写短 lease；
- 进程退出留下的过期 lease 在下一轮回到 `QUEUED`；
- 如果恢复时已超过 `deadline_at`，不再发起 retry，直接用 fallback 最终化；
- Raw finalization 与 queue 删除使用同一 SQLite transaction；
- 即使 transaction 前发生重复执行，Message Bus 最终业务 dedupe 仍保证不会重复发布。

这里只做有限恢复，不引入 leader election、分布式事务或 exactly-once broker。

## 6. 正文与 summary 规范化

### 6.1 RawMessageInput 调整

将输入改为：

```python
class RawMessageInput:
    title: str | None = None
    body: str | None = None
    summary: str | None = None
    publisher_name: str | None = None
    source: str | None = None          # 兼容旧 adapter
    url: str
    ...
```

正文候选只做 normalization，不新增阻塞校验：

```text
original_body = nonblank(body)
fallback_body = original_body or nonblank(summary) or ""
publisher_name = nonblank(publisher_name) or nonblank(source) or SourceDefinition.display_name
```

规则：

1. 原始正文存在时优先保留；
2. 正文不存在时使用 summary；
3. 两者都没有时允许以空字符串进入 enrichment；
4. enrichment 成功才覆盖 `body`；
5. enrichment 失败、过期或黑名单跳过时使用 `fallback_body`；
6. title 和最终 body 同时为空时，仍保存 Raw，但不发布无业务内容的 StandardMessage；该条标记为 item-local `content_unavailable`，不能阻塞其他消息或整个 poll。

### 6.2 Adapter 映射

- Benzinga：API full body → `body`，短描述 → `summary`（若接口有独立字段）；
- Finnhub：`summary` → `summary`，不要伪装成完整 `body`；
- RSS：`content:encoded`/完整正文 → `body`，`description` → `summary`；
- TikHub/Stocktwits：短消息本身 → `body`，source policy 设为 skip；
- Crawler Plane：Observation.body → `body`；未显式 skip 时默认走 enrichment。

Raw provider payload 始终原样保留。

## 7. 默认全量补全与黑名单

不再读取 `metadata.provider` 决定是否补全。在 `SourceDefinition` 增加向后兼容的可选字段：

```python
content_enrichment_mode: Literal["enrich", "skip"] = "enrich"
```

默认策略：

| Source | 默认值 | 原因 |
| --- | --- | --- |
| `benzinga_news` | enrich | 新闻正文可能需要补全 |
| `finnhub_company_news` | enrich | provider 通常只给 summary/redirect URL |
| `newswire_rss` | enrich | description 经常不是全文 |
| crawler sources | enrich | 默认统一经过中台，可由正式 source 配置覆盖 |
| `stocktwits_messages` | skip | 短消息本身就是正文，没有 body/summary 区分 |
| `tikhub_x_search` | skip | 帖子全文即消息正文 |
| `tikhub_x_user_posts` | skip | 帖子全文即消息正文 |

SourceDefinition 更新只影响后续入队消息；已经在 queue 中的 job 使用入队时冻结的 policy snapshot，不做运行中追改。

## 8. 全局并发与域名限流

### 8.1 全局并发

- `DOXAGENT_CONTENT_ENRICHMENT_MAX_CONCURRENCY=8`；
- schema 固定允许值 `1..8`，正式环境设置 8；
- worker 每批最多 claim 8 条并用 `asyncio.gather`/TaskGroup 并行；
- 超过 8 条的任务继续留在 SQLite queue；
- Message Bus poll 只执行快速 enqueue，不等待网络抓取。

### 8.2 全局域名 limiter

`DomainLimiterRegistry` 在 enrichment worker 生命周期内只创建一次，所有 ticker/source/job 共用：

```text
global semaphore: 8
domain semaphore: publisher-specific/default concurrency
domain next_allowed_at: minimum request gap + jitter
```

域名 key 使用最终目标文章域名：

- Finnhub redirect 请求先受 `finnhub.io` limiter；
- 解析出目标 URL 后，正文请求受目标 publisher 域名 limiter；
- Jina fallback 仍占用原 publisher 域名配额，同时受单独的 `r.jina.ai` 配额；
- limiter 必须覆盖 direct、redirect 和 reader 请求，不能只覆盖 adapter poll 请求。

保留当前 publisher profile 作为初始值，但 controller 不再在每条消息调用时重建。

## 9. 一次性 retry 规则

### 9.1 可重试失败

只对明显的短期 transport/风控波动重试：

```text
timeout
connection_error / connection_reset
DNS 临时错误
HTTP 408 / 425 / 429
HTTP 500 / 502 / 503 / 504
HTTP 403 且响应明确呈现临时 challenge/captcha/rate-control 特征
```

以下失败不重试：

```text
missing/invalid URL
HTTP 401 / 404
稳定权限拒绝型 HTTP 403（非临时 challenge）
unsupported_media
empty_extract
source_summary_only
poison_or_navigation_extract
incomplete_extract
```

403 必须先按响应特征分类：临时 challenge/风控页允许一次 retry；稳定权限拒绝不重试。如果以后需要 publisher-specific 策略，应通过新 extractor 版本解决，而不是放宽无限重试。

### 9.2 时间约束

- 一个逻辑 job 最多执行两次网络尝试：initial + one retry；
- `deadline_at = created_at + 180 seconds`；
- 429 优先读取 `Retry-After`，但 `not_before` 绝不超过 deadline；
- 未提供 Retry-After 时使用短退避，例如 30 秒加小幅 jitter；
- worker 取到 retry job 时如果已经超过 deadline，直接作废 retry 并使用 fallback；
- retry 等待不占 worker slot、不 sleep 阻塞主循环；
- 其他任务、域名和 ticker 始终继续处理。

“作废”只表示不再进行网络请求，不删除消息：消息仍按原正文/summary fallback 进入后续业务链路。

## 10. publisher_name、resolved_domain 与下游兼容

### 10.1 字段语义

在 RawMessage 与 StandardMessage 增加可选、向后兼容字段：

```text
publisher_name    # Yahoo、Reuters、Benzinga 等业务信源名称
resolved_domain   # finance.yahoo.com、cnbc.com 等最终抓取域名
```

兼容规则：

```text
source = publisher_name
publisher_name = publisher_name or legacy source or SourceDefinition.display_name
resolved_domain = host(final_url) if available else host(input_url)
```

正文补全成功时：

- `body` 使用抽取出的全文；
- `url` 使用最终文章 URL；
- `publisher_name` 保持原 publisher，不再改成域名；
- `resolved_domain` 单独记录最终域名；
- 原始 URL 继续保存在 metadata/raw payload 中。

### 10.2 下游适配范围

保持已有 `source` 字段，因此以下消费者不会因本轮立刻破坏：

- Message Bus compiler；
- `MaterializedStreamMember`；
- `SourceMessageEnvelope` / Runtime V2；
- V2 projector 和 Message detail API；
- Dashboard 当前 source 展示。

同时逐层透传新增字段：

1. `RawMessage`、`StandardMessage`；
2. `MaterializedStreamMember`；
3. compiler 格式继续使用 publisher，可选附加 `resolved_domain`，但不塞入 LLM body；
4. projector/read store 原样保存新增字段；
5. API DTO 以 optional 字段暴露，旧数据返回 `NOT_RECORDED`/null，不回填猜测值；
6. frontend 若暂不展示，不影响接口解析。

不增加新的 schema_version；新增字段全部有默认值，旧 SQLite `data_json` 可以继续反序列化。

## 11. Message Bus 运行语义调整

### 11.1 accept_poll_result

调整为：

1. 保存 adapter 自身的 acquisition failures；
2. 对每条有效消息快速 enqueue；
3. 更新 acquisition 侧 PollState；
4. 立即返回，不等待正文网络请求；
5. `PollExecutionResult` 增加 additive `queued_count`，保留旧字段避免消费者解析失败。

PollState 的 `succeeded` 只表示 provider poll 成功，不再暗示正文补全成功。正文状态从 Raw/Standard metadata 和 queue status 汇总读取。

### 11.2 enrichment finalization

worker 完成 job 后调用新的内部入口：

```python
MessageBusV2Service.finalize_enriched_message(job, result)
```

该入口负责：

1. 应用成功正文或 fallback；
2. 写 `publisher_name`、`resolved_domain`、`media_enrichment`；
3. 计算 identity/content hash；
4. Raw persistence/dedupe/revision；
5. bootstrap baseline 或 Standard/Stream；
6. 更新该 `poll_run_id` 的异步 materialization 计数；
7. 删除 queue row。

单条失败只能形成该条的 fallback，不得抛出并中止整批。

### 11.3 duplicate 与 retry

- queue 层只避免相同 active input 并发补全；
- enrichment 后正式业务 duplicate 仍由 Message Bus identity/content hash 决定；
- retry 属于同一个 job/attempt_id，不创建新的业务 revision；
- 同一逻辑 materialization 最终只保留一份 completion metadata；
- 不为 retry 增加第二条审计记录。

## 12. 审计、校验与非阻塞边界

### 12.1 不新增审计

- 不新增 audit entity type；
- 不新增 retry audit；
- 不新增 domain limiter audit；
- 保留现有 `body_completion` 兼容输出，一个 logical job 最多对应一条；
- 具体网络 attempts 放在最终 `media_enrichment.attempts[]` metadata；
- queue row 是可删除的运行状态，不作为历史证据长期保存。

### 12.2 不新增全局阻塞

以下情况都必须 item-local 结束并继续其他任务：

- body 和 summary 均为空；
- URL 无法解析；
- direct/reader 失败；
- 429 retry 超过 3 分钟；
- extractor 返回短文或空内容；
- queue job 异常；
- 单域名持续失败。

只有 Message Bus SQLite 本身不可写、schema 无法打开或 worker 无法启动这类全局基础设施错误，才允许进程级失败并依靠容器 restart 恢复。本轮不增加内容质量硬门禁。

## 13. 配置与部署

新增配置：

```text
DOXAGENT_CONTENT_ENRICHMENT_ENABLED=true
DOXAGENT_CONTENT_ENRICHMENT_MAX_CONCURRENCY=8
DOXAGENT_CONTENT_ENRICHMENT_RETRY_DEADLINE_SECONDS=180
DOXAGENT_CONTENT_ENRICHMENT_WORKER_SLEEP_SECONDS=0.25
```

移除正文处理对以下旧配置/行为的依赖：

```text
message.metadata.provider allowlist
每条调用创建独立 DomainFetchController
Message Bus poll 内 await 完整网络抓取
```

Compose 增加单一服务：

```yaml
v2-content-enrichment:
  command: [python, -m, doxagent.content_enrichment.cli, run-worker]
  # 与 v2-message-bus 共享 /data/bus/bus.sqlite3
  restart: unless-stopped
```

`v2-message-bus` 保留采集、入队、finalization contract，但不再执行正文网络请求。正式部署禁止同时启动多个 enrichment worker replica；全局最大并行 8 由这一进程保证。

## 14. 代码修改清单

### 新增

- `src/doxagent/content_enrichment/schema.py`
- `src/doxagent/content_enrichment/repository.py`
- `src/doxagent/content_enrichment/limiter.py`
- `src/doxagent/content_enrichment/extractor.py`
- `src/doxagent/content_enrichment/service.py`
- `src/doxagent/content_enrichment/worker.py`
- `src/doxagent/content_enrichment/cli.py`

### 修改

- `src/doxagent/message_bus_v2/schema.py`
  - body/summary normalization；
  - publisher_name/resolved_domain；
  - SourceDefinition enrichment mode；
  - additive queued_count。
- `src/doxagent/message_bus_v2/adapters.py`
  - 明确 body 与 summary 映射；不再拒绝 missing body。
- `src/doxagent/message_bus_v2/service.py`
  - poll 路径改 enqueue；新增 finalization 入口。
- `src/doxagent/message_bus_v2/repository.py`
  - queue schema、claim、lease、finalize transaction。
- `src/doxagent/message_bus_v2/factory.py`
  - 删除 poll 内 ArticleContentMaterializer 注入。
- `src/doxagent/message_bus_v2/content.py`
  - 退化为兼容 shim 后删除，或直接转发到新中台 extractor；不得继续持有独立 limiter。
- `src/doxagent/message_bus_v2/manifests.py`
  - TikHub/Stocktwits 标记 skip，其余默认 enrich。
- `src/doxagent/message_bus_v2/compiler.py`
  - 保持 source=publisher 兼容，不把 resolved domain 混成信源名称。
- `src/doxagent/monitoring/media_enrichment.py`
  - 可复用提取逻辑迁移到中台；V1 只通过兼容 import 调用，避免复制两套实现。
- `src/doxagent/v2_read/projectors.py`、相关 DTO/schema
  - additive 透传 publisher_name/resolved_domain。
- `src/doxagent/settings.py`、`.env.example`
  - 新增中台配置。
- `docker-compose.v2-production.yml`、`deploy/docker-compose.server.yml`
  - 增加唯一 enrichment worker 及资源限制。
- `docs/agent_integration_reference.md`
  - 更新真实运行顺序、状态语义和运维命令。
- `dev_plan/workflow_v2/message_bus_v2.md`
  - 在用户确认后同步修订与本轮指定链路冲突的旧设计段落。

重要代码修改完成后，按仓库要求追加 `changelog`。

## 15. 实施阶段

### Phase 1：合同与 queue 骨架

- 调整 RawMessageInput/SourceDefinition additive schema；
- 建 queue table/repository；
- 实现 enqueue、claim、lease recovery；
- 保持旧 Message Bus 流程暂时可切回。

完成条件：空 body 可以入队，旧数据可反序列化，超过 8 条不会丢失。

### Phase 2：全局 Hub 与并发 limiter

- 迁移通用 extractor；
- 建立唯一全局 worker pool；
- domain limiter 覆盖 redirect/direct/reader；
- Message Bus poll 改为 enqueue 后立即返回。

完成条件：五个 ticker 同域名请求总并发受同一个 limiter 约束，poll 不等待正文抓取。

### Phase 3：fallback、retry 与 finalization

- 实现 body → summary → empty fallback；
- 实现 transient 分类、一次 retry、180 秒 deadline；
- 补全后执行 identity/hash/Raw dedupe；
- 单条异常全部隔离。

完成条件：429 只重试一次，过期不再请求，失败消息仍能按 fallback 继续。

### Phase 4：publisher/domain 与下游兼容

- 新增并透传两个字段；
- `source` 保持 publisher 兼容；
- 校验 Runtime、projector、API、frontend parser。

完成条件：旧数据、旧客户端继续可读；新数据不再把域名冒充 publisher。

### Phase 5：部署接线与定向验收

- 增加 enrichment worker Compose service；
- 本地固定 corpus 验证；
- 单 ticker Paper 环境小流量验证；
- 再扩展到 5 ticker 并发观测。

完成条件：服务重启可恢复队列，最大并行 8，不发生 poll 全局阻塞。

## 16. 最小必要测试

只运行与本修复直接相关的测试，不扩展无关回归。

### 16.1 输入与 fallback

- body 有值、summary 有值：优先 body；
- body 空、summary 有值：使用 summary；
- body/summary 均空：仍进入 enrichment；
- enrichment 成功：覆盖 fallback；
- enrichment 失败：保持 fallback；
- title/body 最终均空：只隔离该 item，不阻塞批次。

### 16.2 全局并发与排队

- 一次提交 20 条，实际并发峰值必须为 8；
- 其余 12 条保持 QUEUED，随后被处理；
- 5 个 ticker 同时提交同域名任务，按域名观察到的并发/间隔仍只有一套；
- 不同域名可在全局 8 上限内并行。

### 16.3 retry

- 429 → 等待 → 成功，只发生 2 次请求；
- timeout → retry 仍失败，只发生 2 次请求并 fallback；
- Retry-After 超过 deadline，不再进行第二次请求；
- job 排队至超过 deadline 后，不执行 retry；
- 临时 challenge 型 403 只 retry 一次，稳定拒绝型 403、404、empty_extract 不重试。

### 16.4 顺序与耐久性

- 网络请求开始前 queue row 已提交；
- enrichment 完成前不存在业务 Raw；
- enrichment 完成后才计算最终 content hash；
- worker 在 fetch 中退出，重启后恢复 job；
- worker 在 Raw finalization 后、queue delete 前退出，不重复发布。

### 16.5 兼容

- 旧 Raw/Standard JSON 缺少新字段仍可读取；
- compiler 和 Runtime envelope 继续读取 `source`；
- API 新字段为 optional，不改变现有必填字段；
- projector 能处理空 body，但不会把不存在的正文宣称为可用全文；
- 不新增 audit entity，retry 不产生额外审计行。

### 16.6 建议执行命令

```powershell
uv run pytest --offline -q tests/test_content_enrichment_hub.py
uv run pytest --offline -q tests/test_message_bus_v2.py -k "enrichment or queue or dedupe or bootstrap"
uv run pytest --offline -q tests/v2_backend -k "message or bus"
uv run ruff check src/doxagent/content_enrichment src/doxagent/message_bus_v2 tests/test_content_enrichment_hub.py
uv run mypy src/doxagent/content_enrichment src/doxagent/message_bus_v2
```

## 17. 验收标准

必须同时满足：

1. 5 个 ticker 的消息进入同一 queue 和同一个 limiter registry；
2. 全局正文网络任务峰值不超过 8；
3. 第 9 条及以后排队，不丢失、不占并发 slot；
4. 同一域名跨 ticker 的请求遵守统一 concurrency/min-gap；
5. poll acquisition 不等待正文抓取完成；
6. body/summary 均空的消息可以进入中台；
7. 成功覆盖正文，失败保留 body/summary fallback；
8. transient failure 最多 retry 一次，且首次入队后 180 秒到期；
9. permanent failure 不重试；
10. 默认 enrich，TikHub/Stocktwits 通过 source policy skip；
11. `publisher_name` 不被域名覆盖，`resolved_domain` 独立可读；
12. worker 重启后 queue 可恢复；
13. 单条失败不改变其他消息、ticker 或 Message Bus 的运行；
14. 不新增审计体系，不新增内容质量全局硬阻塞；
15. 现有 Runtime、projector、API 和 frontend parser 保持兼容。

## 18. 明确不采用的设计

- 不在每个 Message Bus/ticker 内各建一个 semaphore；
- 不继续在 poll coroutine 中同步等待正文抓取；
- 不用 `metadata.provider` 白名单；
- 不对 403、空提取、摘要过短无限重试；
- 不增加 Kafka/Redis、分布式锁或 leader election；
- 不增加正文质量审批门、人工确认门或全局暂停；
- 不把 queue 状态复制成一套新的永久审计；
- 不因单条正文失败把 PollState、ticker 或 workflow 整体置为失败。

## 19. 实施时必须同步的设计决定

用户已经明确选择以下业务顺序，本方案按该决定执行：

```text
durable queue intake
  → enrichment
  → identity/hash
  → Raw persistence/dedupe
```

这不是原 `message_bus_v2.md` 中的 Raw-first 顺序。开始实施后应以本方案为新决定源，并同步修订旧设计文档，避免代码、测试和文档继续互相冲突。
