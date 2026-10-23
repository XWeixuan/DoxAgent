# 消息源轮询约束与分阶段耗时：两轮只读排查记录

日期：2026-10-24，北京时间。

本文汇总侧对话中两轮排查：第一轮为妨碍按配置周期采集的人工约束扫描；第二轮为浏览器执行、源请求许可、接纳与存储的机制和生产证据核对。仅记录事实、机制、证据边界与未确定项，不包含修复建议或实施方案。

## 1. 范围与证据时间

- 代码范围：`message_bus_v2`、`persistent_runtime_v2/bus_orchestration.py`、`site_strategy`、`crawler_plane`、`content_enrichment`、相关 settings、默认 manifests/seeds、NativeContent 和配置迁移工具。
- 生产读取：源/绑定配置、Site/Identity 有效策略、容器有效设置、Site Access 事件与诊断、Bus/BODY 慢操作日志、只读 SQL 执行计划、少量数据展开验证、主机资源指标。
- 第一轮扫描时，本地 HEAD 为 `778e9748`；读取的生产 Bus 镜像前缀为 `e9cbbd88865c`。本记录不据镜像前缀推断其完整 Git 提交。
- 第二轮扫描时，本地和生产 Bus/BODY 已为 `6a2083722a5cf27e7e4b32ac2a3ad5cb4dcf4680`，Bus 启动于北京时间 03:20:27，BODY 于 03:20:05。Site Access 仍为 `e8e4032eacad8c679f0578b5b1c37a7e3d502ab2`，启动于 01:03:00。
- 第二轮 Site 事件先读取 03:08:55–03:23:55 的 15 分钟窗口，又读取 03:20:28–03:25:44 的较短窗口。后文分别标明，不能视为同一批样本。
- 慢日志读取采用 `--since 10m`，但新 Bus 只运行了数分钟，实际覆盖从其启动到各次读取时刻。日志会继续增长，因此两次日志摘要样本数不同。
- 部署与其他开发可能在排查期间继续推进。本文是上述时间和版本的快照，不代表之后所有生产值保持不变。排查没有修改生产、配置、源代码或 Git 状态。

相关既有资料：

- `docs/message-source-polling-and-body-completion-optimization-plan-20261023.md`
- `docs/message-source-polling-and-body-completion-optimization-release-20261024.md`
- `eval/news-production-final-version-audit-20261024.json`
- `eval/news-production-release-summary-20261024.json`

本次新增实时读取的结果保留在侧对话工具输出中，未另行保存完整原始日志或复制数据库；下面的表格为当次输出的摘录。此限制不同于已有 eval 文件的证据范围。

## 2. 指标定义及解释边界

|指标|当前计算/记录范围|不包含或不能代表的内容|
|---|---|---|
|配置轮询周期|绑定下一次应到期的目标间隔；到期后仍需调度与资源准入|不是固定周期取得新列表的保证|
|单次轮询耗时 `last_latency_ms`|`_poll_impl` 中计时开始到构造最终状态更新时的耗时|计时开始前的初始 `get_poll_state` 等待、尚未派发的组容量等待，以及最终状态更新自身之后发生的等待，不完整包含在内|
|新 capture 间隔 `fresh_capture_interval_seconds`|同一 binding 保存的不同 `last_capture_at` 去重、排序，相邻作差；按来源汇总时按间隔样本数加权|不是文章发布间隔、不是逐请求完整网络日志；一轮多次采集只保留最后 capture 时会漏掉中间快照|
|capture 到首次接纳|第一批 capture 至第一批观察/正文任务提交完成|不等于每篇文章的抓到至发布；不包含后续完整 BODY 和最终发布链路|
|`acquisition_ms`|整个 adapter 执行时间|包含源许可等待及部分逐页接纳，不能理解为纯网络耗时|
|`source_wait_ms`|源 semaphore、启动节奏锁/限速和请求开始记录持久化的累计等待|不能全部解释为网站限流或主动 sleep|
|`site_wait_ms`|Site Access 实际尝试的准入等待汇总；复用 capture 的尝试被排除|不等于全部调度空档|
|`runtime_ms` / `network_ms`|Site attempt 的执行时间；可包含 Worker、页面、内容检查和内部恢复|不等于纯下载耗时；部分 RSS/非 Site 路径没有被这一计时覆盖|
|`accept_ms`|逐页和最终接纳调用累计时间|包含 StorageLane 等待及实际处理；部分发生于 acquisition 内部|

上述阶段有嵌套，不能直接相加或计算互斥的耗时占比。浏览器的 `global_wait_ms` 在前面的联合准入通过之后记录，成功样本中为零，不能证明全局 browser 没有竞争。

配置 60 秒、可观测 capture 间隔 180 秒，表明对应 binding 的有效刷新机会可能低于配置目标，但不能据此断言整个消息总线三分钟才采集一次。其他来源可独立采集，且现有 capture 指标不是全部请求的完整日志。

## 3. 第一轮：调度和源级限制

### 3.1 并发 2 的来源与实际作用范围

`scripts/ops/configure_news_latency.py` 明确把五个目标源组 `max_concurrency` 写成 2；`message_bus_v2/manifests.py` 也为这五个来源保留默认 2、其他一般来源默认 1。该数值来自优化方案的初始资源配置，不是 Chrome 只能开两页的技术上限。源组模型本身允许最大 100；Site/Identity 模型的并发字段上限为 8。

代码存在两种源级控制：

1. `BusOrchestration._inflight_groups` 及 standalone 调度的 `group_active` 限制整轮在途任务数。列表请求、解析、逐页接纳、最终接纳和状态处理期间，任务都可能继续占用名额。
2. `SchedulerGroupLimiter` 的 semaphore 和 pacing lock 限制每个实际请求，同时受最小启动间隔控制。

因此，请求级 permit 未覆盖解析/入库，不代表整轮级容量已经释放。计划文件曾明确区分请求 permit 的范围，实际仍保留整轮在途上限。

纯容量示例：九个 binding，每轮持续占槽 30 秒、目标周期 60 秒，需要平均 4.5 个名额；若为 40 秒则需要平均 6 个。固定并发 2 在该假设下无法持续兑现周期。该计算仅说明容量矛盾，不是各来源真实持槽时长的独立测量。

### 3.2 生产配置快照

下列 binding 数为第一轮从未 tombstone、绑定与 polling 启用配置聚合的结果，不等于该时刻每个 ticker 都处于实时市场采集状态。

|源组|binding 数|并发|请求最小间隔|
|---|---:|---:|---:|
|Yahoo|9|2|1s|
|Barron's|9|2|5s|
|InvestorsHub|9|2|3s|
|Investing|9|2|3s|
|GlobeNewswire search|9|2|0s|
|Reuters search|9|1|1s|
|IBKR news|9|1|1s|
|Benzinga|9|1|1s|
|Finnhub|9|1|1s|
|newswire_rss 共享组|11|1|1s|
|TrendForce news/press 共享组|10|1|3s|
|DIGITIMES semiconductors|5|1|5s|
|HuggingNews|5|1|3s|
|Tom's Hardware / The Elec / ETNews / DIGITIMES Taiwan 各自组|各 5|1|1s|
|GlobeNewswire semiconductors RSS|5|1|0s|
|Google News search RSS|2|1|1s|
|CTEE|2|1|3s|
|TikHub|1|1|1s|
|NBIS official actor RSS|1|1|1s|
|corporate_ir_press_releases|9|6|0s|

`newswire_rss` 组的六个源为 `newswire_rss`、`optical_systems_official_rss`、`aws_platform_marketplace_rss`、`enterprise_apps_official_rss`、`lakehouse_platform_official_rss`、`observability_vendors_official_rss`；均为 by_ticker。一个源的慢任务会占用其他这些源共享的整轮名额/请求容量。

TrendForce 两个源为 by_distribution。共享分发实时调度不完全使用 by_ticker 的整轮 `_inflight_groups` 分支，但请求仍经过同组 limiter；不能将所有组的整轮控制方式视为相同。

第一轮还读取到国防源组：`defense.aviationweek.com`、`defense.breakingdefense.com`、`defense.defensescoop.com`、`defense.airandspaceforces.com`、`defense.space.com`、`defense.spacenews.com`、`defense.ssc.spaceforce.mil`、`defense.nasa.gov`、`defense.war.gov`、`defense.investor.iridium.com`、`defense.calcalist.co.il`、`defense.unmannedairspace.info`、`defense.shephardmedia.com`、`defense.army.mil`，配置并发均为 2、请求间隔 0.5 秒；各组当次 binding 数为 1–4。

Yahoo 九个有效 binding 及 source 默认周期为 30 秒，其余重点源默认周期为 60 秒。用户界面的源默认周期与具体 binding 有效值需要区分。

### 3.3 其他调度行为

- 同组有效并发取组内 SourceDefinition 的最小值，最小请求间隔取最大值。单独改变一个源不能保证组有效值改变。
- 同 binding 在途排除避免重入；旧轮未结束时不会开启下一轮，实时轮询外层可等待最长 600 秒。
- 源任务按到期时间等字段排序；历史 sweep 先于 realtime 分支安排，部分采集阶段与实时轮询共用绑定和源组资源。BODY 等待阶段已能释放部分 sweep 采集责任，但采集阶段仍竞争。
- Bus 循环休眠有效值为 1 秒；每个周期需要先准备名单、读取状态、读取日历/暂停等。循环粒度不等于端到端每秒都能完成派发。
- 初始/配置变更时有组内错峰；`_next_due` 推进目标时间，不是简单完成后固定 sleep 一个完整周期；没有为每个错过的周期无限追加任务。
- 实时采集仍受交易语义日/日历模式和 ticker pause 控制。第一轮读取的启用 binding 未配置额外 `active_windows`。
- `tolerance_ratio` 和 `alert_after_seconds` 等不是实际抢占资源或保证到期执行的机制。容量告警主要比较请求需求频率与最小请求间隔，未完整纳入整轮持槽时长和下游容量。

## 4. 第一轮：Site、Identity、Worker 与资源控制

### 4.1 有效预算和启动间隔

生产 settings 在 Site、Bus、BODY 容器内读取结果一致：

|设置|值|
|---|---:|
|全局 browser pages|4|
|browser max processes 设置|8|
|BODY browser 常态上限|2|
|BODY 空闲借槽|开启|
|BODY Hub 总并发|8|
|BODY 普通 retry delay|30s|
|BODY worker loop sleep|0.25s|
|Bus worker loop sleep|1s|

全局联合准入同时判断全局、Site/scope、Identity 的容量和启动时钟。CRAWLER 可以使用全部四个 browser 槽；BODY 正常最多两个，无 CRAWLER waiter 时可借第三个；每身份 BODY 固定最多一个。已经运行的 BODY 不会因新 CRAWLER 到来而被抢占取消。

正常新 BODY 等待年龄达到 30 秒可提升到普通 CRAWLER 同级；旧文 recheck 不使用该提升。该规则使列表优先并非所有情况下的严格绝对优先。

|Site|并发|最小启动间隔|配置的 CRAWLER/BODY queue timeout|
|---|---:|---:|---|
|Yahoo / Barron's / InvestorsHub / Investing|2|3000ms|均 3000ms|
|GlobeNewswire|2|0ms|均 3000ms|
|Reuters / DIGITIMES|1|3000ms|10000 / 30000ms|

执行代码又将 queue cap 限制为不超过 3 秒，因此后两者较大的配置值不代表实际一定会等待 10/30 秒。

重点有效身份中：Yahoo 主备、Dow Jones 主备、InvestorsHub 主备、Investing backup、`public-markets-nl` 为并发 2、间隔 3000ms；Globe 两个 managed 身份为并发 2、间隔 0；Reuters backup 和 DIGITIMES 相关有效身份为并发 1、间隔 3000ms。

主备身份按当前路由与能力证明切换，不是自动相加的业务负载均衡池。维护、冷启动/激活、认证/订阅能力、会话代际证明、probation 和 profile writer 排他都可能影响可选身份。probation 默认 300 秒、导航间隔 10000ms；运行状态检查含“尚无自然成功证明”的分支，不能仅按时间经过认定已恢复正常节奏。

### 4.2 Worker 与浏览器生命周期

- Manager 和子 Worker 的 `OperationGate` 均按 Identity 配置限制 execute 读并发；配置/维护等 writer 互斥且有 writer 等待优先，可能暂时阻止新 execute。
- 启动锁、IPC 写锁和页创建锁存在，但并非都跨整条命令持有；不能把见到任意 Lock 等同于所有业务访问串行。
- Identity controller 总上限默认 24；managed 同类上限 8，external 同类上限 16。Supervisor 默认最多 16 个 Chrome 实例。未取得当前已触发这些上限的证据。
- 每身份 worker runtime 将自身 managed process 参数设为 1，用于一个身份自己的进程边界；不表示整个系统只有一个 Chrome。
- 浏览器 manager、底层 runtime、worker facade 仍有页面容量控制。联合准入通过的业务调用存在跳过重复获取部分 global slot 的路径，不能把每一个 semaphore 都当作额外独立等待的证据。
- 临时业务页面创建与关闭有 TargetOwner 所有权登记、CDP 控制、Playwright 页面识别等工作。身份启动/重新连接可能包含 Supervisor ensure、controller claim、CDP attach。
- 人工维护和恢复使用 profile 独占及独立 recovery 容量。独占的业务目的为保持同一身份的状态完整性，其存在本身不能证明是无意义限制。
- `max_context_pages` 字段和部分 managed idle-only 回收路径仍存在，未取得其在本轮触发并造成延迟的证据。

### 4.3 默认生成及迁移

除单次生产配置工具外，源 manifests 继续生成固定 1/2 并发。Site seeds 对 Reuters、WSJ、Seeking Alpha、MarketWatch、DIGITIMES 等默认单槽；还存在针对旧配置 `max_concurrency=2,min_interval_ms=500` 降回 `1,3000` 的条件迁移。该分支条件不等于当前五个重点源配置已被降回。

## 5. 第一轮：来源内部与延期机制

|路径|发现的机制|
|---|---|
|Yahoo transport|同一 `rate_scope` 的锁跨整个 HTTP 请求；RSS 共用 RSS scope。session `max_clients=1`。不同 scope 另共用全局启动时钟，默认 gap 1.5s 加 0–0.5s jitter|
|Yahoo 浏览器补齐|内部以距上次 browser capture 至少 60 秒判断是否执行，独立于 source/binding 更短周期；RSS 先接纳后，浏览器补齐仍处于同一整轮任务内|
|Yahoo 回退/冷却|429 circuit 按 300/900/1800s 升级并结合 Retry-After；部分消失/不兼容端点冷却 600s；浏览器/API 相关路径还存在 300/600s 的重试时点|
|Globe head 刷新|每个 query head 内部硬编码至少 60s；每片最多 2 请求或 15s；跨 query/尾页持久续跑，每四个成功请求安排一次尾页|
|Globe HTTP 熔断|适配器级 `_http_retry_at` 可设为 300s；一个失败可影响该实例其他 ticker 对 HTTP 路径的选择|
|Reuters|query 顺序执行，query 内 page 也顺序执行；单条慢 query 占用该 binding 轮次|
|SharedFeed HTTP|部分路径顺序尝试出口或只读连接重试，总预算 30s；请求 permit 期间包含这些尝试|
|Site 准入延期|队列 cap 不超过 3s；普通 queue timeout 有约 1s 后重试的分支。未取得资源的轮次不进入成功轮询均值|
|切换/备用激活|`scope_activation_in_progress`、fallback activation 等分支固定约 60s 后再试|
|其他恢复/不可用结果|存在 60s、120s 重试下限；不同原因不能统一视为网站明确要求的限流|
|Site 风控冷却|风险失败按 60/300/900s 升级，参考 Retry-After；HTTP 与 browser 部分状态分开记录|
|IR|每 endpoint 独立状态；异常后按配置周期指数退避，上限 300s，并参考 429 Retry-After|

Site 级正常访问、网站实际 Retry-After、程序选取的风控退避、纯容量延期、身份未恢复属于不同情况。当前若干等待值属于程序策略，不能仅凭等待存在认定网站只允许这一节奏。

## 6. 第一轮：BODY、分发及其他路径

- BODY 与 CRAWLER 共用部分 Site/Identity/browser 资源；BODY Hub 为最多 8 个任务，实际 browser 常态 2、借 3、每身份 BODY 1。
- BODY 首次实际获取预算 180s，另有首次未开始的 pending 上限约 10 分钟；缓存/在途复用有短延期，恢复延期和真实网络重试有各自时点。它们是边界/回退机制，不是正常耗时目标。
- 旧正文到期核对默认约 30 分钟，仍使用公共任务和存储系统。
- 域名 HTTP controller 也有并发和 pacing：Yahoo/Fool 等为 2，Seeking Alpha/Stocktwits/TheStreet 等为 1，默认 4；各自带 min_gap 和 jitter。这层主要作用于正文公开 HTTP，而不是所有列表请求。
- 共享分发 Worker 默认并发 2，每批 claim 最多 16 条，按文章分组后等待该批处理；主要影响后续分发与发布。
- IR HTTP 独立池总 6、每 host 2、host 启动间隔 500ms；IR browser fallback 2、BODY reader 4。
- 新国防来源 HTTP 池总 4、每 host 2、间隔 500ms。其传输实现可能随其他开发发布变化，本文数值为第一轮读取范围。
- Crawler Plane 子进程默认 4，模型允许 4–8；每个进程逐个执行 job，等待网络时仍占该进程，提交队列可能出现进程内等待。**第一轮生产没有启用 `crawler:` 自定义适配器，五个重点来源走内建或 `site:auto`，因此不能把该池列为它们当前已证实的瓶颈。**
- 发布模式虽支持 buffered，代码默认 buffer max_wait 300s，但第一轮 172 个启用 binding 全为 `immediate`，没有证据表明它们当前在等待该缓冲。
- ARTICLE_ONCE、正文质量/身份/权益门禁和 admission 仍生效。当前接纳提交任务不是最终 Stream 发布；原文更新也不等于再次触发下游。

## 7. 第二轮：浏览器实际耗时及对应机制

### 7.1 较短一致窗口：03:20:28–03:25:44

以下来自 Site `ACCESS_RESULT` 中有完整浏览器阶段计时的 CRAWLER 样本。单位秒。Investing、Barron's 该批有计时者为成功记录；InvestorsHub 成功样本只有 3 条。页面 acquire、navigation 包含于 browser runtime，余项为同批均值差额，不是独立测得的阶段。

|来源|样本|browser runtime|page acquire|navigation|其余未细分差额|worker roundtrip|worker 内等待|
|---|---:|---:|---:|---:|---:|---:|---:|
|Barron's|8|8.578|0.639|1.044|6.895|9.030|0.110|
|Investing|8|10.690|0.618|1.138|8.934|11.330|约 0|
|InvestorsHub|3|25.340|3.263|6.962|15.115|26.891|约 0|
|Globe browser 子样本|1|1.069|0.130|0.477|0.462|1.106|约 0|

Globe 同窗 27 条带 attempt 计时的 CRAWLER 平均 attempt 约 0.710s，其中仅 1 条有 browser runtime；不能将全部 27 条当浏览器样本。上述成功调用 `global_wait_ms` 为 0，发生于联合准入之后。

同窗 CRAWLER 结果：

|来源|结果构成|
|---|---|
|Yahoo browser 补充|6 次 `site_queue_timeout`、7 次 `access_budget_exhausted`，均为 BUDGET_DEFERRED；不代表 RSS 全部失败|
|Barron's|8 SUCCESS、11 `site_queue_timeout`|
|Investing|8 SUCCESS、9 `site_queue_timeout`|
|InvestorsHub|3 SUCCESS、3 `site_queue_timeout`、3 `access_budget_exhausted`、2 SERVICE_UNAVAILABLE / `browser_cdp_attach_failed`|
|Globe|27 SUCCESS、1 `access_budget_exhausted`、1 `site_queue_timeout`|

稍后读取的 Site 诊断显示：无 stalled；联合队列 1 个 waiter；四个活动 browser 名额分别为 InvestorsHub backup 2、Investing backup 1、Dow Jones main 1。活动调用在等待 Worker IPC 返回，若干 age 约 15–21s。该快照证明当时容量满及执行未结束，不能仅据父进程 await 栈判定子 Worker 的具体慢步骤。

### 7.2 导航之后的实际代码路径

**Investing**：先取得临时页、启动导航门禁、激活该 target；goto 对该站只等待 commit。随后 `capture_document` 通过原生 CDP 获取 document/outerHTML，解析 HTML、判断访问状态、列表或正文完整性；每次可间隔 0.5s。无挑战但不完整文档的正常观察边界约 started+12s；观察到挑战时，grace 默认 25s，并受剩余 budget 控制。之后还可能读取原始完整 response、确认 ticker 列表。它不是只有一次 page.goto。

同身份并发时每个 Investing 调用都会激活自己的 target，代码中仍有“single-concurrency”的历史注释；是否因此使另一页后台节流并增加本次延迟，未取得独立实测证明。

**Barron's**：导航 commit 之后，`capture_barrons_ticker_news` 在页面内 `Promise.all` 请求 Barrons 和 BarronsDowJonesNetwork 两个 feed，等待两者完成；每条 fetch 的 AbortController 上限 8s，整个 capture 默认最多 12s。返回前还会读取并检查 native HTML。导航约 1s 不包含后续 feed 请求。现有记录没有每个 feed 独立耗时。

**InvestorsHub**：commit 后等待列表 `.quote-news-item` 可见，最大 15s；BODY 的一些 selector 使用 attached 而非 visible。成功后仍需 page.content、检查和页面清理。本窗存在真实 CDP attach 失败。External runtime 冷连接含 Supervisor ensure、controller claim、Playwright `connect_over_cdp(timeout=20000)`；Worker runtime 对部分 runtime 故障还允许一次 controller 重建尝试。失败不能统一计为网站响应慢。

**Reuters/错误文档路径**：部分 401/403 等待 DOMContentLoaded 上限 8s，再观察原生成功导航响应上限 8s；正常 Reuters 还有内容/搜索结果证据等待。未证明这些等待发生于本窗每条成功来源。

**公共生命周期**：创建临时 target、所有权登记、门禁启动、HTML 读取与检查、gate.close、lease exit、关闭 target/页面，都包含于总执行。页面 acquire 在成功样本约 0.6–3.3s，Worker 内等读锁多数很小。页面关闭在当前数据中没有独立持久计时，不能将余项全部划给内容等待。

`_attempt` 中的 `prior_retry_ms` 在以上成功样本接近 0，不能把其平均十几秒主要归因于多次 attempt 重试。其他失败/恢复请求存在不同路径。

### 7.3 较长窗口的交叉读取

03:08:55–03:23:55 共读取 1101 条事件，其中 1050 条 ACCESS_RESULT。该窗 Investing CRAWLER 的 29 条带阶段样本 browser runtime 均值约 14.091s、navigation 1.505s、page acquire 1.259s；Barron's 25 条约 9.884s、1.570s、0.717s。该窗包含较短窗口前的运行，不用于证明新 Bus 版本的独立收益。

同窗 BODY 方面，Investing 161 条 ACCESS_RESULT 中 147 次 BUDGET_DEFERRED，Barron's 141 条中 121 次；BODY 本身也在资源竞争中多次延期。Site queue 等待、已取得许可后的 Worker global_wait 及实际 browser runtime 是不同计时位置。

## 8. 第二轮：源请求许可的跨层阻塞

`SchedulerGroupLimiter.permit` 的实际顺序：

1. 取得源 semaphore；
2. 取得 `_spacing_lock`；
3. 等 `_next_request_at`；
4. 更新内存启动时钟；
5. **仍持有 semaphore 与 spacing lock，await `_on_request`**；
6. `_on_request` 经同一个 Bus StorageLane 调用 `record_scheduler_request_start`，读取并写入 `scheduler_group_states`；
7. 完成后释放 spacing lock，再 yield 给远端请求。

因此，源许可计时混入了应用存储队列等待。该数据库事务自身很短，但前面的长接纳会让它排队数秒。在 await 期间，同组后续请求不能通过 spacing lock，有空闲网络/页面也不代表可以发请求。

第二次慢日志摘要中，`persist` 有 265 个样本：执行均值约 27ms、排队均值约 3164ms。先前一次读取为 178 个样本，执行均值 27.2ms、排队均值 3122.8ms，排队最大 21756ms。两次为不同读取时刻的累计样本，不是重复计数后的合并均值。

## 9. 第二轮：接纳处理的工作与占用范围

普通最终接纳通过一次 `service.storage.call(_accept_poll_result, ...)` 运行整批同步处理。逐页接纳则一次调用 `accept_observations`，整页放在 `atomic_intake` 中。共享分发通过 `ingest` 和其他登记操作运行。

每批/每篇可包含：

- 外层 `resolve_first_seen_many` 批量 UPSERT first-seen；每篇 `enqueue_enrichment` 再调用 `resolve_first_seen`。
- admission；在相应条件下查询已发布文章；每篇入队也检查 known article。
- `ArticleBodyResults.schedule/observe`、内容更新 hint 比较、正文结果复用、必要时 legacy body 读取。
- `observe_input` 查询观察/URL alias、检查已发布收据和输入兼容性；观察 UPSERT 更新 last_seen、seen_count 及 data_json。
- BODY job 查重；已在途 job 更新 last_seen/updated；新任务入队；部分旧文设置检查状态。
- 模型构造、canonical JSON、hash、NativeContent 编解码及状态写入。
- 共享分发对文章和各 ticker roster 登记 article、delivery、observation、正文结果引用及任务。

正常最终接纳按篇进入事务，存在多次事务成本；逐页接纳将整页包含在事务内，存在较长单次持有。两者都可能在一个 StorageLane 工作单元内持续占用通道。缓存复用/没有新网络下载，不等于接纳路径没有查询、序列化或写入。

第二次慢日志中的主要执行者：

|操作|样本数|执行均值|执行最大值|排队均值|
|---|---:|---:|---:|---:|
|`_accept_poll_result`|82|1237ms|13682ms|3995ms|
|`ingest`|74|752ms|4234ms|5338ms|
|`accept_observations`|24|367ms|1485ms|4462ms|

这些只统计触发 `elapsed>=1s OR wait>=1s` 慢日志的操作，不是所有调用的无偏均值。现有慢日志没有每次操作的 source/ticker、输入条数和内部 SQL/codec 子阶段，不能把某次 13.682s 精确归给某一个源或某一条 SQL。

## 10. 第二轮：存储通道、查询与编码

### 10.1 应用层存储队列

`GlobalPollScheduler.storage_call` 直接使用 `service.storage.call`。同一 Bus 服务事件循环中的相关同步工作共享 StorageLane 的一个 asyncio lock。获取锁后通过线程执行整个工作单元，执行完才释放；读、写、Python 解析/模型构造/序列化可能一起包含在工作单元中。

因此，长接纳期间，其他来源短状态读取、源请求开始记录、轮询准备、IR 提交和共享分发事务都可能等待。BODY 独立进程拥有自己的应用锁，不是所有进程共用同一个 asyncio lock，但仍共享 Bus SQLite writer 和主机资源。

第二次慢日志中的短操作：

|操作|样本数|实际执行均值|应用锁排队均值|
|---|---:|---:|---:|
|get_poll_state|199|15ms|3336ms|
|save_poll_state|120|38ms|3795ms|
|_limiter|76|34ms|3039ms|
|_prepare_poll_cycle|10|71ms|7385ms|
|persist（请求开始记录）|265|27ms|3164ms|
|attach_target|116|26ms|3752ms|
|_record_distribution_poll|73|93ms|5712ms|

前一次较早读取的 get_poll_state 排队最大 19337ms、save_poll_state 最大 20878ms。这些等待发生在运行同步操作之前，不是对应 SQL 自身执行了十几秒。

BODY 进程慢操作样本明显少于 Bus：第二次摘要中 prune_site_strategy_outbox 三次执行均值约 1347ms、claim 三次约 754ms、finalize 三次约 501ms。该事实不等于 BODY 没有资源竞争，也不证明所有 Bus 等待均由 BODY 造成。

### 10.2 SQLite 与关键执行计划

Bus 普通连接：WAL、synchronous NORMAL、busy_timeout 30000ms；事务使用 BEGIN IMMEDIATE。Site busy_timeout 5000ms。跨进程 writer 竞争、事务内文件工作与应用队列等待是不同成本，当前慢日志不能完整拆分。

只读 EXPLAIN 核对结果：

|查询|生产计划|
|---|---|
|ticker＋URL 旧正文查找|使用 `raw_article_url`；ORDER BY 有临时 B-tree|
|ticker/source/binding/identity 已发布原文定位|使用 `idx_mbv2_raw_identity` 等关联索引；实际 known 查询含 standard/stream 收据校验|
|正文 final canonical URL/scope/language|使用 `idx_body_results_verified_url`|
|observation→Raw→standard→stream 收据关联|使用 `observations_input`、Raw 主键、standard raw 唯一索引和 stream member covering index；排序有临时 B-tree|

本轮这些被检查的关键查询没有显示全表扫描；这不能推广为所有业务 SQL 都无问题，也不能据索引使用本身认定某个大 alias 集合没有成本。

### 10.3 NativeContent 与只读小样本

RawMessage/EnrichmentJob 保存可能通过 NativeContent：JSON 序列化、hash，长子对象外置；新文件涉及 gzip、文件和目录 fsync。读取文件包含解压、hash 校验和模型验证。该工作可能在数据库事务和 StorageLane 工作单元内进行；本轮没有实际写入计时。

从最新有 canonical Raw 的八条观察读取关联正文，SQL SELECT 约 0.11–4.24ms，展开/验证约 1.15–8.68ms；单条展开大小约 11–52KB。该样本未包含 Yahoo 的大 HTML，不能据此排除大 payload 成本。

另读取最新 400 条观察的大小字段：其中 37 条 Yahoo 观察 raw_payload 外置大小均值约 133094 字节、最大 834866 字节；多数其他样本没有整段 raw_payload 外置。此为大小证据，不是这些载荷 CPU/写盘耗时的测量。

## 11. 第二轮：主机资源共同影响

当次读取主机为 4 CPU。CPU pressure：avg10 81.75%、avg60 83.96%、avg300 80.68%；这些是任务等待 CPU 的压力时间，不是 CPU 使用率。

`vmstat 1 3` 中两个即时区间 CPU idle 均为 0、user/system 合计约 100%，运行队列约 19–27；即时 iowait 约 0。IO pressure avg60 约 5.40%、memory pressure avg60 some 0.31%、full 0.07%。不能从短采样推断永久没有磁盘/内存压力。

随后一次 Docker 快照：

|角色|CPU（100%=约一核）|内存|PIDs|
|---|---:|---|---:|
|Chrome Supervisor|132.90%|3.837GiB / 8GiB|1545|
|Site Access|76.09%|4.762GiB / 6GiB|939|
|Message Bus|75.47%|230.6MiB / 15GiB|17|
|Projector|43.12%|286.8MiB / 15GiB|2|
|Executor|19.29%|18.63MiB / 2GiB|3|
|BODY|15.28%|134.2MiB / 15GiB|8|
|Scheduler|12.20%|366.1MiB / 15GiB|25|

Site、Supervisor、Bus、BODY 的 HostConfig NanoCpus/CpuQuota 均为 0，没有在本次读取中发现额外固定 CPU quota。Site 内存上限 6GiB、Supervisor 8GiB、Bus/BODY 15GiB。容器内存上限相加不代表宿主机有对应可用物理内存，PID 数也不等于业务页数。

这些证据表明该时段存在明显主机 CPU 争用。它同时影响浏览器脚本、CDP/Worker 通信、HTML 检查、接纳序列化与投影。当前不能仅凭十几秒 browser runtime 认定远端下载慢，也不能把应用队列慢全部归因于磁盘。

## 12. 已观察到的跨层关系与未确定项

已经由代码与运行证据支持的关系：

1. 整批接纳/共享 ingest 的长工作单元占用 StorageLane；其他短操作排队数秒。
2. 调度准备与初始状态读取也排队，使“到期→实际派发”变长；部分等待未完整进入后台单次轮询耗时。
3. 请求许可在持有源 semaphore/spacing lock 时等同一存储通道，存储拥堵反向阻止下一次网络请求。
4. 整轮任务仍占源组名额，接纳和存储延迟继续传导至其他 ticker。
5. 浏览器总量/局部预算满会造成独立延期；成功样本的执行均值没有包含所有延期轮次。
6. 导航后的内容观察、页面内 feed 获取、控制连接及页面生命周期具有实际成本；主机 CPU 争用同时放大多个环节。

尚不能从本轮证据精确得出的内容：

- 每个来源完整周期差额分别有多少来自等待派发、延期重试、源许可、浏览器、接纳及最终状态保存。
- 浏览器余项中 DOM 等待、feed 请求、内容检查、gate/页面关闭各自的毫秒数。
- 某次接纳的 SQL writer 等待、实际 SQL、模型/JSON、压缩/hash、fsync 各占多少，以及与源、输入条数的对应关系。
- 全部来源真实新文章 capture→BODY→Stream→UI 的逐篇端到端分布。
- 仅凭上述短窗口能否推断稳定高峰容量、连续交易日正文完整率或完整覆盖。

本文中的分阶段均值不能相加当作端到端总时间，也没有据这些记录宣称配置周期或正文目标已经达成。

## 13. 代码定位索引

|主题|文件|
|---|---|
|源组整轮名额与 sweep/realtime|`src/doxagent/persistent_runtime_v2/bus_orchestration.py`|
|请求 limiter、调度名单、轮询计时|`src/doxagent/message_bus_v2/scheduler.py`|
|配置工具、默认源配置|`scripts/ops/configure_news_latency.py`、`src/doxagent/message_bus_v2/manifests.py`|
|联合预算与优先级|`src/doxagent/site_strategy/budget.py`|
|Site 准入、延期、attempt、诊断|`src/doxagent/site_strategy/service.py`|
|Identity 并发、维护 writer、IPC|`src/doxagent/site_strategy/operation_gate.py`、`worker_manager.py`、`identity_worker.py`|
|浏览器执行、临时页与控制连接|`src/doxagent/site_strategy/runtime.py`、`worker_runtime.py`、`external_runtime.py`|
|Investing/Barron's 内容路径|`src/doxagent/site_strategy/investing_document.py`、`src/doxagent/message_bus_v2/barrons_sources.py`|
|Yahoo transport/补齐|`src/doxagent/message_bus_v2/yahoo_transport.py`、`news_adapters.py`、`yahoo_sources.py`|
|Globe 时间片与固定 head 周期|`src/doxagent/message_bus_v2/market_sources.py`|
|接纳、任务登记与结果复用|`src/doxagent/message_bus_v2/service.py`、`repository.py`、`body_results.py`|
|共享分发|`src/doxagent/message_bus_v2/distribution.py`、`distribution_repository.py`|
|存储队列和文件编码|`src/doxagent/message_bus_v2/storage_lane.py`、`src/doxagent/v2_read/native_content.py`、`content_files.py`|
|通用 BODY 节流|`src/doxagent/content_enrichment/service.py`、`extractor.py`、`src/doxagent/monitoring/media_enrichment.py`|
|条件适用的 Crawler 子进程池|`src/doxagent/crawler_plane/runtime.py`、`worker_runtime.py`|
|有效值、默认值、模型边界|`src/doxagent/settings.py`、`src/doxagent/site_strategy/schema.py`、`seeds.py`|
