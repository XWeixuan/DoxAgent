# DoxAgent Crawler Plane 开发方案

## —— Message Bus v2 的 Crawler Execution Infrastructure

## 1. 定位与目标

Crawler Plane 是 DoxAgent 消息管线中位于 **Message Bus v2 与 O4 crawler authoring workflow 之间**的爬虫基础设施中台。

它的职责不是决定：

* 哪个 ticker 什么时候 polling；
* polling interval 是多少；
* 哪条消息是否已经对某 ticker 发布；
* RawMessage 如何去重成为 StandardMessage；
* StandardMessage 如何进入 Stream。

这些继续由 Message Bus v2 负责。

Crawler Plane 专注于：

> **crawler 代码资产的开发支持、版本管理、稳定执行、crawler-specific runtime state、确定性测试、Network Cassette / Raw Artifact、运行诊断与 crawler-specific alert。**

整体边界为：

```text
                    O4
                     │
          develop / test / repair
                     │
                     ▼
              Crawler Plane
                     │
          CrawlerObservation[]
                     │
                     ▼
         CrawlerSourceAdapter
                     │
                     ▼
                 RawMessage
                     │
                     ▼
              Message Bus v2
                     │
                     ▼
            StandardMessage
                     │
                     ▼
               Ticker Stream
```

Crawler Plane 因此可以理解为：

> **Message Bus crawler source 的统一 execution backend。**

而不是 Message Bus 内部第二套 acquisition system。

---

# 2. 三层职责边界

必须长期保持下面三类“真相”只有一个 owner。

| 领域                                                                    | Owner         |
| --------------------------------------------------------------------- | ------------- |
| Polling 时间窗、目标 cadence、跨 ticker 错峰、provider spacing、poll health       | Message Bus   |
| Crawler code、版本、执行、checkpoint、cassette、测试、crawler diagnostics         | Crawler Plane |
| ticker 消息 identity、最终 dedupe、revision、StandardMessage、Stream delivery | Message Bus   |

Crawler Plane 可以提供增量发现和 crawler-side dedupe helper，但其语义只是：

> **提高 crawler discovery 效率。**

例如：

```text
这个 IR document_id 上次已经处理
→ 本次不用重新 fetch detail
```

而不是：

```text
MU 是否已经正式接收过这条消息
```

后者始终由 Message Bus 判断。

这样避免产生：

```text
Crawler state truth
        VS
Message Bus message truth
```

两套互相冲突的增量状态。

当前 v1 Stocktwits 已经形成过 acquisition truth 与 bus truth 分散在不同 repository 的特例，Crawler Plane 不应重新复制这种结构。

---

# 3. Crawler Plane 的六个核心服务

Crawler Plane 第一版建议由六个逻辑服务组成：

```text
Crawler Registry
Crawler Execution Service
Crawler Toolkit
Crawler Certification Service
Artifact / Cassette Service
Crawler Health & Alert Service
```

整体结构：

```text
┌────────────────────────────────────────────┐
│               CRAWLER PLANE                │
│                                            │
│  Crawler Registry                          │
│      packages / versions / active release  │
│                                            │
│  Execution Service                         │
│      runtime / checkpoint / telemetry      │
│                                            │
│  Optional Toolkit                          │
│      HTML / date / URL / incremental       │
│                                            │
│  Certification Service                     │
│      replay / temporal / synthetic         │
│                                            │
│  Artifact & Cassette Service               │
│      cassette / failure artifact           │
│                                            │
│  Health & Alert Service                    │
│      crawler-specific diagnostics          │
└────────────────────────────────────────────┘
```

六者都是 infrastructure service。

Certification 和 Alert 都不是为了审核或限制 O4，而是给 O4 提供：

> **可重复、可调用、能够直接用于开发和修复的反馈能力。**

---

# 4. Message Bus 与 Crawler Plane 的接口

## 4.1 SourceAdapter 继续属于 Message Bus

Message Bus v2 保持统一入口：

```text
SourceAdapter
    ↓
RawMessage
```

API Source：

```text
ProviderSourceAdapter
    ↓
RawMessage
```

Crawler Source：

```text
CrawlerSourceAdapter
    ↓
Crawler Plane execute()
    ↓
CrawlerObservation
    ↓
RawMessage
```

因此 Message Bus 只需要一个通用：

```text
CrawlerSourceAdapter
```

而不是：

```text
MicronIRAdapter
NvidiaIRAdapter
WhiteHouseAdapter
...
```

以后增加第 100 个 crawler，也不需要修改 Message Bus core。

---

# 5. CrawlerObservation Contract

Crawler Plane 不直接生成 Message Bus `RawMessage`。

因为 RawMessage 中包含：

```text
ticker
binding_id
source_id
raw_message_id
collected_at
schema_version
```

这些属于 Message Bus lineage。

Crawler Plane 输出一个更窄的：

```text
CrawlerObservation
```

建议业务 contract 为：

```text
title             optional
body              required
source            required
url               required
published_at      required

external_id       optional
metadata          optional
raw_artifact_ref  optional
```

它与 Message Bus v2 已经定义的五个核心业务字段保持一致：

```text
title
body
source
url
published_at
```

然后：

```text
CrawlerObservation
+
PollContext
        ↓
CrawlerSourceAdapter
        ↓
RawMessage
```

Message Bus 再补充自己的：

```text
ticker
binding_id
source_id
collected_at
raw_message_id
```

等基础设施信息。

---

# 6. Execution Request / Result Contract

Message Bus 发起一次 crawler polling 时，本质上只是向 Crawler Plane 发出：

```text
execute crawler once
```

建议：

```text
CrawlerExecutionRequest

crawler_id
ticker
binding_id
source_parameters
poll_run_id
```

Crawler Plane 自己加载：

```text
active crawler version
crawler checkpoint
runtime configuration
```

然后返回：

```text
CrawlerExecutionResult

execution_id
crawler_id
crawler_version
status

observations[]
diagnostics
artifact_refs[]
```

Message Bus 不需要知道 crawler 内部：

```text
调用了多少网页
用了 Browser 还是 HTTP
翻了几页
用了哪个 selector
checkpoint 怎么更新
```

---

# 7. Crawler Plane 不拥有 Scheduler

Crawler Plane 不保存：

```text
poll_interval
active_window
target_due_at
next_dispatch_at
跨 ticker slot
provider global pacing schedule
```

这些全部属于 Message Bus Global Poll Scheduler。

调用关系永远是：

```text
Message Bus:
现在执行一次 micron_ir

        ↓

Crawler Plane:
执行一次
返回结果

        ↓

Message Bus:
决定下一次什么时候执行
```

Crawler Plane 允许在一次 execution 内做合理的 transport-level retry：

```text
connection reset
→ retry
```

但它不建立：

```text
5 分钟后重新运行 crawler
```

这种第二调度系统。

---

# 8. Crawler-specific Checkpoint

Crawler Plane 可以保存 crawler 自己需要的运行状态，例如：

```text
pagination cursor
last listing page
last_seen_document_id
source-specific cursor
document listing fingerprint
```

Checkpoint 推荐按：

```text
crawler_id + binding_id
```

持久化。

基本行为：

```text
Execution starts
    ↓
load previous checkpoint
    ↓
crawler runs
    ↓
successful completion
    ↓
commit new checkpoint
```

如果 crawler execution 整体失败：

```text
previous checkpoint remains authoritative
```

避免失败执行把 crawler 推进到未知位置。

Checkpoint 的作用是：

> 提升 acquisition 效率和维持 crawler 内部连续性。

而不是 Message Bus 的最终 message watermark。

---

# 9. Crawler Package

Crawler Plane 管理的基本资产不是单独的：

```text
crawler.py
```

而是：

> **Crawler Package**

推荐概念目录：

```text
crawlers/
    micron_ir/
        manifest.*
        crawler.*
        tests/
        fixtures/
        cassettes/
```

Package 至少描述：

```text
crawler_id
entrypoint
crawler contract version

source registration metadata
parameter schema

network/runtime capabilities

checkpoint schema/version

test assets
```

这里不包含：

```text
poll_interval
active_windows
stream publication mode
```

这些仍属于 Message Bus。

---

# 10. Working Copy 与 Active Release

O4 workspace 和 Crawler Plane crawler 文件无需人为制造物理隔离。

O4 可以直接在：

```text
crawlers/micron_ir/
```

工作。

但必须区分：

```text
WORKING VERSION
```

和：

```text
ACTIVE RELEASE
```

例如：

```text
micron_ir

v12 ACTIVE
v13 WORKING
```

O4 修改 v13 时：

```text
production execution
→ 仍然运行 v12
```

Promotion 后：

```text
active_version → v13
```

关键不是创建两个 workspace，而是：

> **ACTIVE version 必须是 immutable release。**

实现上可以通过：

```text
version_id
package content hash
immutable snapshot
```

固定。

项目现有 Event Library/O2 已经采用过类似的有效工程模式：Agent 在 workspace 中工作，由确定性 runner 负责校验、导入并推进 Published Revision；working state 与运行中的 published state 不混为一体。Crawler Plane 可以借鉴这种“工作资产与运行资产分离”的工程结构，但这里的目的只是 crawler 稳定运行，而不是审批 Agent。

---

# 11. Crawler Registry

Registry 负责：

```text
crawler identity
available versions
version digest
creation/update metadata
certification result ref
active version
superseded versions
```

Crawler 生命周期首版可以保持简单：

```text
WORKING
   ↓
CERTIFIED
   ↓
ACTIVE
   ↓
SUPERSEDED
```

`CERTIFIED` 仅表示：

> 这个版本成功完成指定 Certification Service 测试。

它与：

```text
ACTIVE
```

是两个不同状态。

这样 O4 可以反复：

```text
edit
→ certify
→ edit
→ certify
```

而不会影响生产。

---

# 12. Crawler Execution Service

所有生产 crawler 统一通过：

```text
CrawlerExecutionService.execute()
```

运行。

执行流程：

```text
ExecutionRequest
      ↓
resolve ACTIVE version
      ↓
load checkpoint
      ↓
create execution context
      ↓
execute crawler
      ↓
capture telemetry
      ↓
capture artifact when required
      ↓
commit checkpoint on success
      ↓
return CrawlerObservation[]
```

每次执行生成：

```text
execution_id
```

并与 Message Bus：

```text
poll_run_id
```

关联。

最终能够追踪：

```text
StandardMessage
      ↓
RawMessage
      ↓
poll_run_id
      ↓
crawler execution_id
      ↓
crawler version
      ↓
network cassette
      ↓
raw artifact
```

这是整个 crawler provenance 的闭环。

---

# 13. Execution Context

Crawler 不直接依赖整个 Crawler Plane internals。

Execution Service 向 crawler 注入：

```text
CrawlerContext
```

概念上包含：

```text
ticker
parameters
checkpoint

http
browser

tools
artifact access
```

Crawler 只依赖这个稳定 contract。

这样后续 Crawler Plane 更换：

```text
HTTP library
browser implementation
artifact store
```

不会要求重写全部 crawler。

---

# 14. Optional Crawler Toolkit

Crawler Plane 提供通用工具箱，但不规定 crawler 必须使用。

建议首版提供：

```text
HTML text extraction / cleaning
article/main extraction

date parsing / normalization

canonical URL normalization

JSON / XML helper

hash helper

listing diff helper

checkpoint helper

incremental discovery helper
```

核心原则：

> **标准化 crawler 的输入输出与运行生命周期，不标准化 crawler 内部算法。**

所以允许：

```text
crawler A
→ 使用 toolkit
```

也允许：

```text
crawler B
→ BeautifulSoup 自己处理
```

只要最终返回合法 `CrawlerObservation[]`。

这避免 Crawler Plane 演变成一个过于僵硬的万能 crawler framework。

---

# 15. Network Runtime

为了让：

```text
Network Cassette
Temporal Replay
Synthetic Increment
failure artifact
```

真正可重复，建议 crawler 默认通过：

```text
ctx.http
ctx.browser
```

访问网络。

例如：

```text
ctx.http.get()
```

由 Plane 自动记录：

```text
request URL
method
response status
relevant headers
redirect
response body
timestamp
```

Browser runtime 可以按需保存：

```text
final rendered DOM
```

这不是为了限制 O4，而是让系统天然拥有：

```text
record
replay
diagnose
```

能力。

特殊 crawler 仍可以使用自定义 transport，但可能无法获得完整 automatic cassette support。

---

# 16. Network Cassette Service

Cassette 表示：

> 一次 crawler execution 所观察到的网络世界。

例如：

```text
request 1
→ IR listing HTML

request 2
→ release HTML

request 3
→ attachment JSON
```

保存成一个可 replay execution environment。

支持至少两种模式：

```text
RECORD
REPLAY
```

生产：

```text
ctx.http
→ live network
```

测试：

```text
ctx.http
→ cassette responses
```

crawler code 不改变。

---

# 17. Raw Artifact Service

Raw Artifact 是 crawler execution 的关键原始证据。

不仅包括 HTML，还可以是：

```text
HTML
JSON
XML
PDF
error response body
redirect page
rendered DOM
```

首版建议：

### Certification execution

保存测试需要的 cassette / fixture。

### Production success

保存必要 metadata；response body 保存策略可配置，避免无限增长。

### Production failure

自动保存：

```text
network cassette
relevant raw responses
execution log
crawler version
parameters
checkpoint snapshot
```

形成：

```text
Failure Artifact Bundle
```

供后续 O4 直接 replay。

---

# 18. Regression Corpus

生产失败 artifact 应能够被加入：

```text
Regression Corpus
```

因此生命周期可以形成：

```text
Production failure
      ↓
Failure Artifact
      ↓
O4 repair
      ↓
Replay failure
      ↓
fix
      ↓
add failure case to regression corpus
      ↓
future Certification always replays it
```

结果是：

> crawler 每经历一次真实故障，后续版本的确定性测试都会更强。

---

# 19. Crawler Certification Service

Certification 是 Crawler Plane 提供给 O4 的统一测试服务：

```text
certify(crawler_version)
```

它不是另一个 Agent，也不是 crawler 审核委员会。

它只是：

> **一次性调用多个确定性测试并形成标准结果的开发服务。**

建议首版至少执行：

```text
Contract Validation
Replay Test
Temporal Replay
Synthetic Increment
Determinism Test
Failure Replay
```

输出：

```text
CertificationResult
```

例如：

```text
crawler_version = v13

contract             PASS
replay               PASS
temporal_replay      PASS
synthetic_increment  FAIL
determinism          PASS
failure_cases        PASS

overall = FAIL
```

每个 failure 包含：

```text
test_case
expected
actual
cassette_ref
artifact_ref
diagnostic
```

方便 O4 直接进入下一次修改。

---

# 20. Contract Validation

检查：

```text
manifest 是否合法
entrypoint 是否可加载
parameter schema 是否合法
execution 是否返回 CrawlerObservation
必填字段是否存在
```

其中：

```text
body
source
url
published_at
```

必须满足 crawler output contract。

---

# 21. Replay Test

使用固定 cassette：

```text
Cassette
   ↓
crawler
   ↓
CrawlerObservation
```

对结果执行 deterministic assertion。

它验证：

> 在已知真实输入下 crawler 仍能产生稳定正确结果。

---

# 22. Temporal Replay

Temporal Replay 验证 crawler 的时间推进和增量发现能力。

例如：

```text
T0

A
B
C
```

建立 checkpoint。

随后：

```text
T1

A
B
C
D
```

使用 T0 checkpoint 执行。

期待：

```text
D 被发现
```

再次执行 T1：

```text
不得再次把 D 当 crawler-side 新发现
```

这里测试的是：

> crawler discovery state。

不是 Message Bus ticker delivery dedupe。

---

# 23. Synthetic Increment

当没有真实未来样本时，通过修改 cassette 构造：

```text
当前：
A
B
C
```

变成：

```text
A
B
C
SYNTHETIC_D
```

要求 crawler 能发现 D。

Synthetic Increment 应作用在 crawler 真正依赖的数据层：

```text
HTML listing
JSON response
XML
XHR response
```

而不是假设所有 crawler 都是 DOM selector crawler。

---

# 24. Determinism Test

相同：

```text
crawler version
parameters
checkpoint
cassette
```

重复运行，应产生相同：

```text
CrawlerObservation
checkpoint transition
```

避免 crawler 内部随机行为影响增量结果。

---

# 25. Failure Replay

对于生产中保存的：

```text
Failure Artifact Bundle
```

Certification Service 可以直接 replay。

一个 repair version 至少应该能够证明：

```text
原始 failure 已无法复现
```

而且历史 regression cases 仍然通过。

---

# 26. Live Probe

确定性 Certification 与真实网络验证应分开。

可以额外提供：

```text
live_probe(crawler_version)
```

用于：

```text
真实请求
真实网站
不进入生产 Message Bus
```

检查：

```text
能否连接
能否产生 observation
当前网站是否与 cassette 仍基本一致
```

但它不是 deterministic Certification 的组成部分。

这样不会因为网站瞬时网络问题导致 Certification 本身不可重复。

---

# 27. Crawler Health Service

Crawler Plane 的健康服务同时观察两个方向：

```text
Crawler execution telemetry
+
Message Bus operational/materialization telemetry
```

因为不同错误只有在不同层才能正确观察。

例如：

| 异常                         | 最合适观察层                 |
| -------------------------- | ---------------------- |
| crawler exception          | Crawler Plane          |
| HTTP 403 / timeout         | Crawler Plane          |
| selector 返回 0              | Crawler Plane          |
| 长时间 poll failure           | Message Bus            |
| 最终正文异常短                    | Message Bus processing |
| source 有请求但长期无 observation | 联合判断                   |
| observation 数量突然暴增         | 联合判断                   |

因此 Message Bus 与 Crawler Plane 应通过：

```text
poll_run_id
execution_id
source_id
binding_id
```

建立 telemetry correlation。

---

# 28. Message Bus Alert 与 Crawler Alert 分工

Message Bus 继续生成通用：

```text
polling_unhealthy
scheduler_capacity
provider unavailable
```

等 Operational Alert。

Crawler Plane 进一步提供：

```text
crawler_execution_failure
crawler_discovery_anomaly
crawler_content_drift
crawler_transport_anomaly
```

例如：

```text
Message Bus:
micron_ir 30 min 未成功 poll

Crawler Plane:
过去 7 次 execution 全部 HTTP 200
但 parser 都返回 0 observations
```

Crawler Plane 因此可以产生更加 actionable 的：

```text
crawler_discovery_anomaly
```

以后 O4 被唤醒时，直接获得真正有用的故障上下文。

---

# 29. Alert Policy

Crawler Alert Service 必须支持 crawler/source-specific 配置。

例如：

```text
execution_failure:
    enabled = true

content_too_short:
    enabled = true
    threshold = ...

content_too_long:
    enabled = false

discovery_empty:
    enabled = true
    window = ...
```

O4 后续可以：

```text
读取
修改
启用
停用
```

某类告警。

因此 Alert Service 是：

> **agent-operable diagnostics service**

而不是固定审查规则。

---

# 30. Content Drift

正文异常不应该使用全系统唯一的：

```text
body < 600
```

规则。

不同 crawler 可以配置不同 baseline。

第一版可采用：

```text
source-specific min/max
```

以后可以扩展为：

```text
historical body length distribution
historical observation count
structure fingerprint
```

例如：

```text
过去正文通常 5k–10k chars

突然连续：
40
55
38 chars
```

才真正构成：

```text
content drift
```

这个能力属于 diagnostics，不影响 Message Bus RawMessage contract 本身。

---

# 31. Source Registration

O4 完成 crawler package 开发后，交付入口仍是 Crawler Plane。

流程：

```text
Crawler Package
      ↓
Certification
      ↓
Crawler Release
      ↓
Crawler Plane registration service
      ↓
Message Bus Source Registry
```

Crawler Plane 向 Message Bus 提供：

```text
CrawlerSourceRegistration
```

概念上包含：

```text
source_id
display_name
kind = crawler

crawler_id

source parameter schema
default source parameters

recommended scheduler group / constraints
default streaming configuration if defined
```

Message Bus Source Registry 仍然是 source 注册状态的最终 owner。

Crawler Plane 只是负责完成 crawler source 的交付。

---

# 32. Active Version 与 Message Bus 解耦

Message Bus SourceDefinition 推荐只引用：

```text
crawler_id
```

而不是永久固定：

```text
crawler_version
```

生产执行时：

```text
Crawler Plane
→ resolve active version
```

这样：

```text
v12 → v13
```

升级不需要修改 Message Bus binding。

但每次 execution 必须记录：

```text
crawler_version
```

RawMessage metadata 也可以保留：

```text
crawler_execution_id
crawler_version
```

保证历史消息仍然可以追溯到当时真正运行的 crawler。

---

# 33. Rollback

Registry 必须支持：

```text
active v13
↓
发现问题
↓
activate v12
```

Rollback 只切换：

```text
active_version
```

不回滚：

```text
Message Bus stream
already produced messages
consumer offset
```

Crawler-specific checkpoint 是否兼容，需要由 crawler version manifest 声明 checkpoint schema version。

如果版本无法读取旧 checkpoint，可以执行明确的 checkpoint migration / reset，而不是静默误读。

---

# 34. Persistence

Crawler Plane 建议使用独立 SQLite store。

例如：

```text
crawler_plane.sqlite3
```

核心表可以围绕：

```text
crawler_packages
crawler_versions
crawler_active_versions

crawler_checkpoints

crawler_executions
crawler_execution_artifacts

network_cassettes

certification_runs
certification_results

crawler_alert_policies
crawler_alerts
```

Workspace 中保存代码资产和较大的：

```text
cassette
raw artifact
fixture
```

SQLite 保存：

```text
metadata
identity
state
references
```

避免将大量 HTML/PDF body 直接重复写入运行状态表。

---

# 35. Crawler Plane Application API

第一版建议为 O4 / CLI / Dashboard 提供稳定 service API：

```text
list_crawlers
get_crawler

create_version
get_version

certify_version
get_certification_result

promote_version
rollback_version

execute
live_probe

get_execution
get_execution_artifacts

get_alert_policy
update_alert_policy

list_alerts
get_alert
resolve_alert

register_crawler_source
```

O4 后续通过这些正式 service contract 操作 Plane。

而不是直接写：

```text
active_version database row
```

或者自己修改 Message Bus registry。

---

# 36. 推荐代码结构

逻辑上可以整理为：

```text
src/doxagent/crawler_plane/

    schema.py

    registry/
    execution/
    runtime/
    toolkit/

    certification/
    cassette/
    artifacts/

    health/
    alerts/

    repository/
    service.py
    api.py
```

Crawler package 可以位于：

```text
crawlers/
```

或现有 Codex/O4 workspace 可写区域。

物理路径可以和 O4 authoring workspace 重合。

关键约束只是：

```text
working file
≠
immutable active release
```

---

# 37. 开发阶段

## Phase 1 — Cross-plane Contracts

首先冻结：

```text
CrawlerObservation
CrawlerExecutionRequest
CrawlerExecutionResult

CrawlerManifest
CrawlerVersion

CrawlerCheckpoint

CrawlerSourceRegistration
```

同时完成 Message Bus：

```text
CrawlerSourceAdapter
```

contract。

验收目标：

```text
fake crawler
→ Crawler Plane
→ CrawlerSourceAdapter
→ RawMessage
```

端到端成功。

---

## Phase 2 — Registry & Versioning

实现：

```text
Crawler Registry
working version
immutable release
active version
promotion
rollback
```

重点测试：

```text
修改 working version
不会影响 active execution

promote
下一 execution 使用新版本

rollback
恢复旧版本
```

---

## Phase 3 — Execution Runtime

实现：

```text
Execution Service
CrawlerContext
HTTP runtime
Browser runtime
checkpoint
execution telemetry
```

重点保证：

```text
一条 execute request
只产生一次独立 execution

成功才推进 checkpoint

失败完整记录 execution result
```

---

## Phase 4 — Toolkit

迁移/实现：

```text
HTML extraction
date normalization
canonical URL
JSON/XML helpers
incremental helpers
```

保持 optional。

不要求已有 crawler 必须使用。

---

## Phase 5 — Cassette & Artifact

实现：

```text
record mode
replay mode

failure raw artifact
failure bundle

execution → artifact lineage
```

测试：

```text
live recording
→ replay
→ 相同 crawler output
```

---

## Phase 6 — Certification Service

依次实现：

```text
contract validation
replay
temporal replay
synthetic increment
determinism
failure regression
```

对外只提供统一：

```text
certify(version)
```

入口。

---

## Phase 7 — Health & Alert

接入：

```text
Crawler Plane execution telemetry
Message Bus poll/materialization telemetry
```

实现：

```text
alert policy
OPEN alert
update existing alert
RESOLVED alert
```

支持：

```text
disable alert type
change threshold
```

---

## Phase 8 — Reference Crawlers

首批建议只开发两类代表性 reference crawler：

```text
Company IR crawler
Government policy crawler
```

因为两者正好覆盖不同复杂度：

```text
HTML listing + detail page
发布时间
增量 publication

政府页面
document revision
附件
结构变化
```

用这两个 reference source 验证 Crawler Plane abstraction 是否真的足够通用。

---

# 38. 第一版验收标准

Crawler Plane 第一版完成应满足：

### Asset

Crawler 可以：

```text
创建
编辑
版本化
certify
promote
rollback
```

### Execution

Message Bus 可以：

```text
通过统一 CrawlerSourceAdapter
执行任意 ACTIVE crawler
```

无需为新 crawler 新写 adapter。

### Stability

Working crawler 修改：

```text
不会影响 ACTIVE crawler。
```

进程重启：

```text
active version
checkpoint
execution history
alert state
```

均可恢复。

### Testability

O4 可以一次调用：

```text
certify(crawler_version)
```

获得完整 deterministic result。

### Reproducibility

生产 failure 可以：

```text
保存 cassette/raw artifact
→ 后续 replay
```

### Diagnostics

Crawler Plane 可以从 execution + Message Bus telemetry 识别 crawler-specific 异常并形成 persistent alert。

Alert policy 可查询、配置和停用。

### Integration

成功 crawler execution：

```text
CrawlerObservation
→ CrawlerSourceAdapter
→ RawMessage
```

之后完全进入 Message Bus v2 原有 pipeline。

---

# 39. 第一版明确非目标

Crawler Plane 第一版不负责：

```text
跨 ticker polling scheduling
active trading windows
poll interval

Message Bus final dedupe
message revision identity
Raw → Standard normalization
stream publication
consumer cursor

source relevance evaluation
ticker 应该监控哪些网站

O4 reasoning / Prompt
O4 repair orchestration

semantic content validation
event detection
trading judgment
```

这些边界越稳定，Crawler Plane 后续越容易扩展。

---

# 40. 最终架构

完整消息管线的前两层最终变成：

```text
                O4 Authoring / Repair
                         │
                         ▼
               ┌─────────────────┐
               │  Crawler Plane  │
               │                 │
               │ Registry        │
               │ Execution       │
               │ Toolkit         │
               │ Certification   │
               │ Cassette        │
               │ Health/Alerts   │
               └────────┬────────┘
                        │
                CrawlerObservation
                        │
                        ▼
               CrawlerSourceAdapter
                        │
                        ▼
                    RawMessage
                        │
                        ▼
               ┌─────────────────┐
               │ Message Bus v2  │
               │                 │
               │ Global Scheduler│
               │ Source Registry │
               │ Dedupe/Revision │
               │ Materialization │
               │ StandardMessage │
               │ Durable Stream  │
               └─────────────────┘
```

最终最重要的原则可以压缩成两句话：

> **Message Bus 管消息，Crawler Plane 管 crawler。**

以及：

> **Crawler Plane 对 crawler 内部实现保持开放，但把 crawler 的资产生命周期、执行环境、I/O contract、可重复测试和故障诊断标准化。**

这样第三层 O4 才能真正成为一个稳定的 crawler engineering agent：它面对的是一套已经准备好的开发和运行平台，而不是每新增一个网站就同时重新解决调度、网络、状态、测试、部署、告警和消息接入。
