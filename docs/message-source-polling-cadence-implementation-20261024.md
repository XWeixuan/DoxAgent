# 实际采集周期与端到端时延优化：开发与验证回执

日期：2026-10-24。对应方案：[实际采集周期与端到端时延优化方案](message-source-polling-cadence-and-latency-optimization-plan-20261024.md)。

本轮交付调度、持久采集与接纳、请求准入、来源执行、正文竞争、Read/UI 和运维配置工具的联动改造。设计基线为 **8 核、1–20 个 ticker**；按实际启用的 binding、查询、频道、周期和执行成本计算需求，没有固定 9 个 ticker 的阈值。

本轮在本地开发分支验证，**没有同步 origin main、修改生产配置或发布服务**。生产升级后的 CPU、内存和网站身份并发能力尚未在本轮核验；下文的本地功能与浏览器测试不等于生产实际间隔、99% 周期兑现率或 80% 真实正文成功率已经验收。

## 1. 已实现的主链路

```text
固定相位的应执行时点
  → 按独立采集单元派发
  → 短范围的请求节奏 / Site / Identity / 全局浏览器准入
  → 实际传输与列表校验
  → 准备载荷（事务外）
  → capture 与获取 checkpoint 同事务持久化
  → 释放采集责任，允许下轮到期采集
  → 每片最多 16 篇的可恢复接纳
  → 既有正文复用 / BODY / ticker 分发
  → ARTICLE_ONCE 发布或更新原消息与修订记录
  → Read 投影与周期、端到端指标
```

没有引入 Redis、Kafka、额外消息平台或每 ticker 一个浏览器。继续使用 Bus SQLite、NativeContent、现有 Site Access 与身份所有权。

## 2. 按实施包核对

|实施包|落点|本轮行为|
|---|---|---|
|A 周期与请求账|`polling.py`、`poll_trace.py`、`cadence.py`、Site client/runtime、审计脚本|保存 schedule 变更、run、真实请求及 capture；算入失败、延期、取消、逾期和未派发周期，保留窗口两端空窗|
|B 短存储与复用|`storage_lane.py`、repository/service、正文结果、分发仓库|独立只读连接与快照；读取和准备各限 2；单 writer 公平队列；完整且未变输入走标量索引快路径|
|C 捕获与接纳拆分|`poll_captures`、`CaptureIntake`、scheduler、IR|载荷和获取进度原子提交；接纳 cursor、收据与业务登记原子推进；进程中断后按 owner token 重放|
|D 固定时点与请求节奏|scheduler、Bus orchestration、manifests、配置工具|取消整轮源组占槽；慢 ticker 不挡同组其他 ticker；首屏、补充频道、查询和尾页独立到期|
|E 来源执行成本|Yahoo、Globe、Barron's、Investing、InvestorsHub、fetch anchors|减少不必要的导航、逐篇时间探测、等待和浏览器级联；保留来源覆盖合同|
|F 容量与 BODY|联合预算、Identity Worker、settings/seeds、production compose|按启用需求与 EWMA 成本计算局部容量；硬件/身份上限约束；新 BODY 最少服务机会和两槽常规上限|
|G 分发与 UI|DistributionWorker、ContentEnrichmentHub、Read/outbox/projector、消息总线 UI|释放一个文章槽即可领取下一组；展示真实刷新间隔、周期兑现、缺轮、延期、失败和接纳积压|

### 2.1 调度和存储边界

- 到期时间从固定相位推进，错过多个周期只合并成下一次工作，不产生补发风暴。增减 ticker 不重新打散其他 binding 的已有相位；修改周期保留已消费 checkpoint。
- 一个采集单元最多一个在途工作。后台历史 sweep 使用单独的在途标识；首屏不等它完成。共享来源按物理采集的最短有效周期计算需求，不按订阅数重复请求。
- 请求 permit 只包住实际请求所需的限制范围；启动记录异步批量落盘，节奏等待不占并发名额，后续接纳不占网络许可。五个重点来源移除 Bus 层重复固定 gap，实际节奏由 host/Site/Identity 负责；同时迁移旧配置，避免 Barron's 5s、InvestorsHub 3s 的源组节奏残留。HTTP 注册客户端另有总在途 16、默认 host 4 的边界。
- `poll_captures` 保留原观察时间、source/binding 版本、bootstrap/sweep 上下文及冻结的共享订阅 roster。获取 cursor 和接纳 cursor 分离；旧 owner 不能推进新 owner 的接纳位置。
- source 或 binding 修改后，旧响应不能覆盖新周期、checkpoint、健康状态；旧代际响应也不能充当新代际的有效刷新。接纳时仍检查当前启用状态与订阅权限。共享文章重放会检查新增订阅，避免已取得的文章对新订阅遗漏。
- 写通道仍单 owner。每连续 8 个短控制写至少服务一个普通写；每片接纳最多 16 篇，片间让出写通道。超过 50ms 的片段输出软预算慢日志，不能将 50ms 当成已保证的生产 SQL 上限。
- 取消不能中断后台线程的事务：包括重复取消，都会等已开始的提交结束后才释放 writer 名额。只读连接有独立事务快照，不借用另一线程的 writer 连接。
- 原始列表大载荷在准备池编码、hash 和文件发布，随后提交引用；未变且已有完整正文的输入不展开旧正文、不重建 BODY 作业、不重写 Standard。实质字段、时间修正、更新提示、策略代际或 recheck 到期仍进入既有修订机制。

### 2.2 各来源的处理

|来源|采集单元与成本优化|保留的完整性约束|
|---|---|---|
|Yahoo|`HEAD` RSS、`SUPPLEMENT_HEAD` 浏览器分别继承 T；`TAIL:YAHOO_TIME` 独立处理持久的待核验文章 URL。首屏不再等待最多三篇文章日期请求；已验证日期回填复用。RSS 独立 4 并发、250ms 初始节奏，不再和需认证 API 共用串行会话|RSS 成功不掩盖浏览器补充路径缺失；只接纳有精确日期证明的文章。待核验 URL 离开首屏仍保留，日期核验使用原 bootstrap 发布语义。真实 429 按端点族退避，半开仅一次探测；404/405/410 的浏览器页面冷却按 ticker/单元隔离|
|GlobeNewswire|每个 search query 独立首屏与有界尾页；失败隔离到 binding/query；已验证 HTTP 列表优先|不因一个 query 或容量延期把整个来源改走浏览器；查询覆盖与历史续页 checkpoint 保留|
|Barron's|Barron's 与 Dow Jones 两个原生 feed 分别派发、捕获；同一物理身份的有所有权同源 fetch 页面可短时复用|两个必需频道分别计算兑现，聚合状态不能因其中一个成功就报完整；检查真实原生返回、频道、日期和明确空列表合同|
|Investing|列表执行在准确原生响应和当前 DOM 之间竞速；已有有效列表就取消另一观察任务；CRAWLER 列表不再等待候选文章正文检查|当前挑战页面优先于旧健康响应，不把历史 DOM 或页面壳当新列表；既有国家域和身份路由保留|
|InvestorsHub|列表等待可使用 attached，而非必须进入可见状态；减少无关的 focus|保留已验证身份和列表解析，无法完成的轮次仍延期/失败|
|企业 IR|保留每端点隔离；HTTP/RSS 验证与短提交后异步接纳；archive recovery 独立于新列表；binding 的 T 生效到各内部端点|304 必须有已消费基线；改周期保留 ETag、列表指纹和 bootstrap。历史页不能回写覆盖较新的首屏 ETag/checkpoint；仍是 7×24 采集|

Yahoo 日期核验属于发现新文章的必要后续工作，不因它命名为 TAIL 而随普通历史任务停掉。浏览器主列表仍可能有真实导航成本；本轮没有将未验证的同源请求假定为所有网站的默认替代路径。

### 2.3 浏览器、资源与正文

- 全局活动页面默认按 CPU 数计算，最高 8，显式环境配置优先。局部需求取 `ceil(请求速率 × 实际服务成本 / 0.75)`，受现有配置及已验证身份能力约束；名额按需借用，不按 20 个 ticker 预留 20 个页面。
- 取消会把旧 `2/500ms` 配置降回 `1/3000ms` 的种子迁移。配置工具支持 adaptive 源组、Site 上限 8 和显式的已验证身份上限；不会在运行时根据 ticker 数擅自突破身份上限。
- 新 BODY 常规最多 2 个 browser 槽，同身份最多 1 个 BODY；符合准入且等待 1s 的新 BODY 在没有 BODY 执行时取得服务机会。默认关闭无条件借用第三槽，旧文 recheck 不提升为首屏同级。
- 同身份多个业务页仍各有独立 gate/lease/TargetOwner；取消一个请求不能关闭兄弟页。fetch anchor 每个物理身份最多保留一个空闲业务页，空闲 90s、总寿命 300s，换代、出口变化、挑战、维护与关闭时失效。人工登录/恢复页不进入此池。
- CPU PSI some avg60 ≥10% 时暂停可选历史与旧文复查，不拉长实时首屏周期；接纳超过 3s 的积压也暂停这些工作，并报警。新文章接纳优先于普通历史，未处理输入保持持久状态。
- production compose 保存相对 CPU shares：Site/Supervisor/Bus 1536，BODY 1024，projector 512，维护 256。没有给消息链路增加硬 CPU quota，也未改变无关交易服务。
- 正文入口/获取前复用、真实完整性检查、各阶段回退原因、首次抓取预算及 ARTICLE_ONCE 语义继续生效。缓存命中不冒充网络下载成功，正文失败不因抓取优先级调整而从业务分母消失。

## 3. 指标与诊断数据

`poll_runs` 保存实际执行与终态；`poll_captures` 保存实际列表、观察/提交/首次与最终接纳时点；正文与 Stream 沿用现有作业、文章结果和发布审计，保留原 run ID 关联。

HTTPX 以 httpcore 的发送请求头事件记录启动，区别于等待连接前的 hook。浏览器记录导航/原生 feed/同源日期请求的实际发起与响应证据；旧 shared capture 的重复消费不会新增物理请求记录。端点日志移除用户信息和查询参数，只保留 host/path 及请求 hash。

周期口径分开记录：

1. `expected_cycles`：窗口内全部应执行周期；有效日历未知仍纳入，不能直接排除。
2. `settled_cycles`：已终结或已逾期、需要验收的周期。失败、取消、延期立即保留在分母。
3. `pending_cycles`：只有当前尚在启动/执行容许时间内、未终结的周期暂单列。超过启动宽限或一个 T 后不能继续当正常进行中。
4. `coverage=fresh/settled` 与 `all_cycle_coverage=fresh/expected` 同时提供，避免在当前轮尚未返回时稳定少算一个成功，也避免抹去全轮次账。
5. 新鲜列表要求真实传输证明和有效采集终态；保存过部分输入但整体失败的轮次不能通过 capture 数量伪装为成功。窗口前最后 capture、窗口结束仍未刷新的年龄都参与 gap。

UI 展示近 30 分钟真实刷新间隔、P95、周期兑现、缺轮、延期和失败；每源还显示采集年龄、实际间隔、请求启动 P95 提示、待覆盖 query 和待核验时间。正文 cohort 与待接纳候选/接纳异常分开显示，不能把尚未登记成文章的失败藏进漂亮的正文比例。

CDC 不携带 capture 私有载荷、claim/lease 或完整局部收据；lease 更新和运行中纯阶段更新不制造技术历史风暴。已完成诊断明细按 3 天做每批有界维护，保留每单元窗口前最后真实刷新证据、未完成/失败输入及刚重放完成的旧输入；业务消息、正文、修订和 Stream 不按此诊断期限删除。Read 的这些技术对象也不转存为永久 business_revisions。

`scripts/ops/audit_news_timeliness.py` 提供全轮次、子单元、物理请求成本、capture→接纳、积压和样本截断标志；旧数据库显式返回未埋点，不将旧 last_latency 拼成新周期验收。

## 4. 测试与调试记录

最终按唯一 testcase 取最近结果汇总：**298 通过、1 跳过、0 未解决失败**。跳过项是 Windows 当前账户不能创建符号链接的 Supervisor singleton 清理用例；本地 Chromium 并发与 anchor 用例实际执行通过。

|检查|结果与证据|
|---|---|
|主回归|295 项，初次 292 通过、2 个 IR 页面替身缺少 `on` 接口而失败、1 跳过；原始失败记录保留在 [完整 JUnit](../eval/news-cadence-complete-20261024.xml)|
|IR 替身修正复验|6 项通过，包括上述 2 项；[专项 JUnit](../eval/news-cadence-ir-browser-final-20261024.xml)|
|最终旧配置/节奏链路|81 项通过；[pacing JUnit](../eval/news-cadence-pacing-final-20261024.xml)|
|延期原因、接纳和正文链路|81 项通过；新增空列表/部分输入两类延期原因穿透测试；[reason JUnit](../eval/news-cadence-deferred-reason-final-20261024.xml)|
|Yahoo 来源及隔离|56 项通过；[来源 JUnit](../eval/news-cadence-yahoo-isolation-final-20261024.xml)；最后修改后的页面冷却隔离 2 项再次通过，[最终专项](../eval/news-cadence-yahoo-page-final-20261024.xml)|
|去重汇总|299 个唯一用例，298 通过、1 跳过；[验证汇总 JSON](../eval/news-cadence-validation-summary-20261024.json)。不是将多次重复运行的通过数相加|
|代码检查|新增核心模块与新测试完整 Ruff 通过；所有修改 Python 文件的 F/I 检查通过；[lint 结果](../eval/news-cadence-lint-20261024.txt)|
|前端|`pnpm --dir frontend/v2 build` 通过，含 schema 生成和 TypeScript 检查；1262/1559px Playwright 2 项通过并人工检查截图。构建保留既有大 chunk 提示|

回归期间还修正了三条会影响真实结果的链路：生产配置迁移未去掉 Bus 固定 gap；Yahoo 页面缺失使用全源冷却；延期原因经过空/部分接纳后未完整写入 run 账。修正后分别复验上表相关组，没有只调整超时或只追求后台耗时显示。

已经覆盖的关键故障场景：

- 20 ticker 中一个请求挂起、接纳受阻时，其余健康 ticker 仍能采集并开始下一轮；需求计算覆盖 1、9、12、20。
- 大列表分片重放、旧 claim token、共享文章后附加订阅、原观察时间、计数一致与单文章下载复用。
- source/binding 在旧请求执行期间修改：成功和失败两类晚返回均不能回滚新代际状态。
- FIFO 大批接纳不再挡只读；连续短写公平；重复取消不释放仍在提交的 writer。
- 304 没基线、明确空列表、挑战、原生响应与 DOM 竞速、失败列表不能算新鲜、旧缓存不增加实际请求。
- Yahoo 日期核验保留离开首屏的 URL、原始 bootstrap 语义及真实 429 的单探测恢复。
- 使用本地 Chromium 临时 profile 实测同 profile 2/4 页并行与单页取消；本地真实页面验证 Barron's/Yahoo 同源 anchor 复用及正确 TargetOwner 清理。这些测试不碰生产 profile，不证明远端网站在相同并发下的风控配额。
- 1262/1559px 的消息总线 UI 无横向溢出，新增源状态标签不挤成竖排。UI fixture 中的 84.0% 是合成数据，不是生产成功率。

## 5. 上线执行与生产验收边界

本节是后续发布步骤，不表示已经执行。

1. 确认远端实际已升级 8 核，记录可用内存、swap、CPU PSI、run queue、运行镜像及身份状态；内存仍按约 16GiB 假设核验。保留发布前同窗实际周期/正文样本。
2. 发布 Site Access/Worker 的新计时、联合预算和页面生命周期实现，保留 Supervisor 持有的 Chrome、profile、出口和恢复页；随后发布 Bus/BODY/API/projector/frontend。Bus 初始化增加表和列，再运行既有 CDC schema/backfill 流程，使新 run/capture 投影生效。
3. 通过 `configure_news_latency.py --bus <bus.db> --registry <site.db> --receipt <receipt.json>` 先生成只读配置差异；使用既有 Site owner API、admin token file 和 `--apply` 写入 CAS 配置。配置工具不会绕过维护/冷却身份，也不会直接改 Site 的 owner 数据表。
4. 全局浏览器先以显式环境上限 6 验证，再到 8。真实身份按 2→3→4 做有界业务 canary，检查各频道/列表 ID、日期、挑战、取消隔离和内存；通过后经 Owner 设置该身份能力上限。`--validated-identity-capacity` 只能填写已取得证据的能力，不把本地 Chromium 4 页成功当成所有远端身份都验证为 4。同一组身份的能力不同时，应分别写 Owner 配置，不能批量误提。
5. 配置变动/增减 ticker 后，检查同一代码自动计算的需求、周期和容量缺口；实际网站能力不足继续保留缺轮和告警。不能通过增加 profile、静默改慢 T、减少 query 或用缓存刷新隐藏缺口。
6. 用新审计工具按每个 binding/必需 head 对照窗口：due→实际请求、真实列表 gap、capture→接纳、正文就绪→Stream→Read；检查平均、P95/P99、最大停滞及完整分母。先看约 30 分钟自然运行及活跃交易时段样本，不以少量成功轮询宣布稳定达标。
7. 真实首次发现文章使用成熟 cohort 验收 FULL/SHORT_FULL ≥80%，分别列出网络、缓存、更新核查、pending、失败与未接纳候选。同时检查 CPU PSI、进程 RSS、浏览器 target/driver 数与单位来源成本；内存/网站能力成为瓶颈时按实测最大成本继续优化。

本轮已把生产验证所需的周期账、调试工具、配置差异和代码路径落地。**尚未获得的结论**是：8 核生产负载的具体延迟降幅、每个现有身份更高并发的可持续能力，以及真实新文章 cohort 的稳定 80% 成功率。它们需要在目标服务器和真实来源上取得数据，不能从离线并发用例推导。
