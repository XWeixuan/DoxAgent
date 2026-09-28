# 消息管线、Message Bus 与 Site Strategy 架构速查

更新基线：2026-09-24，代码基线 `591eb8d2`（以当前代码和在线配置为准）

本文面向后续负责“新增消息源、监测词、共享分发、去重/发布、正文补全、修复新消息爬虫或维护浏览器访问”的 Codex。它描述当前已落地的代码结构，不将方案中的待办当成线上已启用功能；实时 source/binding、监测词、站点、出口与认证状态必须再查询各自 Registry 和运行记录。

## 1. 模块边界

消息管线至少有三种不同的治理对象，不能互相替代：

| 治理对象 | 基本单位 | 管什么，不管什么 |
| --- | --- | --- |
| Message Bus source / binding | 采集入口、`ticker + source` 订阅 | 采集方式/参数/调度、监测词、正文任务、准入、ticker 内去重、发布；不决定网站浏览器身份 |
| Site Strategy / Browser Identity | 网站/域名、浏览器身份 | 爬虫与正文的访问策略、Profile、出口、认证及浏览器生命周期；不决定哪些 ticker 订阅或消息源 fallback 优先级 |
| Runtime V2 | ticker、持久 stream 消费位置 | 接收已发布消息并创建/推进后续 Case；不承担上游采集和正文抓取 |

Site Strategy 是消息网站访问层。它统一治理：

- URL/域名属于哪个消息网站；
- 该网站的新消息爬虫策略与正文识别策略；
- 访问使用的 Browser Identity、Profile、Proxy Egress 和 Browser Runtime；
- 登录验证、访问节奏、cooldown、组合 fallback、诊断与统计。

它不治理 Message Bus 的来源优先级或非浏览器后备链。例如 Yahoo 的“crawler → NCP → RSS”仍由 Message Bus adapter 决定，只有 crawler 访问经过 Site Strategy。Yahoo 抓到的 Barron's 文章在正文补全时按最终文章域名归 Barron's，不继承 Yahoo 的策略。`SourceKind` 的 `api/crawler` 是技术获取方式，`AcquisitionMode` 的三类是采集与分发方式，二者正交；Google News RSS 也可以是 `by_search`。

## 2. 一张图理解当前实现

```text
SourceDefinition + TickerSourceBinding
                  + TickerMonitoringTerms（仅 search/distribution）
                          │
                          ▼
              调度/抓取（API、RSS、Crawler）
                  ├─ by_ticker / by_search ──→ ticker 绑定的补全任务 ─┐
                  └─ by_distribution ───────→ 共享 run/文章/补全 ────┤
                                             L2 正则 ∨ Jev → ticker delivery
                                                               │
            Site Access ← 需要网页的 crawler / 正文补全             ▼
               │                                ticker 准入 → Raw/Standard
URL → SiteResolver → Site Strategy                         → 即时/缓冲 Stream
               │                                              → Runtime V2 Case
          Access Combination
               │
         Browser Identity → persistent Profile + Proxy Egress
               │             + Managed Playwright / External Chrome + CDP
```

访问层上层只接收统一的 `AccessResult`；结果携带 `site_id`、策略 revision、combination、identity、egress、runtime kind/instance/generation 和出口 IP。正文、Crawler、健康与运维据此留痕，不需要知道 Chrome 是怎样启动的。API/RSS 入口不因属于 Message Bus 就自动经过 Site Access。

## 3. 核心资源与不变量

| 资源 | 职责 | 关键不变量 |
| --- | --- | --- |
| `SiteStrategySpec` | 一个网站的域名、正文、crawler、认证和访问组合 | 站点配置有 revision，CAS 更新，可回滚；非 generic 站点必须声明 publisher/API 域名 |
| `AccessCombination` | 某站点可选的访问组合及优先级 | 新模型引用 `identity_id`；可分别为 body/crawler 覆盖组合顺序 |
| `BrowserIdentitySpec` | 浏览器身份资源 | 唯一绑定一个 Profile、一个 Egress、一种 Runtime、固定 locale/timezone/window/lifecycle；多个 Site 可共享 |
| `BrowserProfile` | 持久 Cookie、Storage、Preferences | 单 writer；绑定 Egress 不可漂移；不得热拷正在写入的 Profile |
| `ProxyEgress` | Clash 固定 listener 和实际节点状态 | Profile/Identity 绑定稳定 egress ID；节点可在固定 slot 后替换，但不要偷偷换 ID/端口契约 |
| `SiteIdentityAuth` | 某 Site 在某 Identity 上的认证结果 | 认证状态是 `site_id + identity_id` 维度；共享 Chrome 不等于共享“已验证”结论 |
| `SiteRuntimeState` | Combination 健康、active、cooldown | HTTP 与 Browser 健康分轨，generation fencing 防止旧结果覆盖新状态 |

域名解析采用 exact/suffix 规则，最具体规则优先；冲突会被拒绝。`support_hosts` 只允许当前站点导航/加载受信辅助域名，不取得文章归属权。未注册域名进入按 host 隔离的 `generic` 策略和派生 Profile，不能与其他未知域名共享 Cookie。

Registry 持久化在 SQLite WAL 中，保存策略/Identity 全量 revision、head、Profile、Egress、逐站认证、运行态、事件和正文结果。应用策略与 Identity 都要求 `expected_revision`，不要在线直接修改 SQLite。

## 4. 双轨 Browser Runtime

### Managed Playwright

- Playwright 直接启动 CfT 153 persistent context。
- 适合低风控、访问稳定、无需特殊浏览器连续性的站点，也是默认轨道。
- Profile 仍长期持久化并固定 Egress/locale/timezone/window；不是每次请求创建匿名无状态浏览器。

### External Chrome + CDP

- Chrome Supervisor 以普通方式启动锁定版正式 Google Chrome 153；Playwright 只用 `connect_over_cdp` 接管默认 context。
- 不使用 Playwright launch 参数、stealth、UA/Client Hints override、资源拦截或 TLS MITM；Chrome 原生生成网络与浏览器身份。
- Supervisor 拥有 Chrome 生命周期、Xvfb、loopback VNC、Profile OS writer lock 和优雅 `SIGTERM`；Site Access 重启只断开/重连 CDP，不应杀死长期 Chrome。
- 每次业务任务新建独立 page；同一 Identity 统一限流、并发和生命周期。`always_on` 启动时预热，`on_demand` 可在空闲后回收。
- Chrome、Site Access 均以 UID 10001 运行并保留 sandbox。CDP 只在共享容器 network namespace 的 loopback；VNC 只发布宿主 `127.0.0.1:5900`。

选择原则：默认 Managed；只有控制实验已证明 Playwright 启动方式导致 challenge/login 失败，或明确需要长期真实 Chrome 连续性时，才使用 External。不要为普通站点批量迁移到 External。

当前首批映射：Barron's、WSJ、MarketWatch 共享 `dowjones-main/-backup`；Seeking Alpha 使用独立 main/backup；Yahoo 使用独立 main/backup；它们走 External。Reuters 和 generic 保持 Managed。实际 egress、启停和认证状态以在线 Registry 为准。

## 5. 正文补全链路

启用 Site Access 时，Message Bus 使用 `body_v2.2`：

1. `SharedContentExtractor` 根据文章 URL 调用 Site Access。
2. Resolver 按最终文章域名选择 Site Strategy，而不是按消息来源选择。
3. `body.access_order` 决定 `http_public`、`browser`、`reader` 的尝试顺序；需要登录的首批站点通常只走 browser。
4. Site Access 选择健康 Combination、校验认证、取得 Site+Identity 联合预算，再由对应 Runtime 返回 HTML。
5. `ArticlePipeline` 使用 Registry 的 body strategy ref 和参数识别正文，最后经过统一质量门；结果和完整访问 provenance 写入 trace/outcome。

现有正文策略既有通用抽取，也有站点适配。Registry 支持 `body_xpath`/`remove_xpath`；代码适配覆盖 Yahoo、Reuters、Barron's、WSJ、Seeking Alpha、MarketWatch、TheStreet、Finnhub redirect、CNBC、247wallst、Fool、Chartmill、Benzinga。新增策略 ref 必须在 catalog 注册并通过参数校验，不能从 Registry 注入任意代码或任意 JavaScript。

## 6. 新消息爬虫链路

- Message Bus 的 `site:auto` adapter 在每次 poll 时解析 listing URL，并读取当前 Site Strategy 的 `crawler.ref`。
- 小型、稳定、可信的内建 recipe 当前包括 Yahoo 页面和 Reuters 搜索；它们通过 `SiteManagedBrowser` 调用同一个 `/v1/access/execute`，因此自动继承 Identity、Egress、Runtime、节奏和 fallback。
- 复杂或需独立版本治理的爬虫使用 `crawler:<crawler_id>`，由 Crawler Plane 完成 package、certify、promote、checkpoint、retry 和 artifact 管理。
- Crawler 子进程不直接持有任意网络能力；HTTP/Browser 请求由父进程 broker。启用 Site Access 时，broker 将请求转成 `SitePurpose.CRAWLER`，并记录 cassette/provenance。

不要把“新增 Site Strategy crawler ref”误当成“新增 Message Bus source binding”。Crawler 策略、SourceDefinition、binding/schedule、消息归一化/发布是相邻但不同的配置层，验收时都要检查。

## 7. fallback、风控和认证

- 候选组合按 active、priority 排序；风险失败后在同一次请求中尝试下一个 Combination，不轮换同一 Profile 背后的临时匿名出口。
- Browser 与 HTTP_PUBLIC 分别维护 cooldown。429/challenge/block 的退避为 60/300/900 秒；Browser 连续两次 challenge/block 后标记需要人工关注，等待显式维护/清理。
- Egress 定时探测并保存 observed IP。已验证 READY 节点第一次完整探测失败仍保留最后成功状态，连续第二次失败才置为 `UNAVAILABLE`；真实访问失败仍由正常 fallback 处理。
- 认证要求可以按 body/crawler 覆盖。`required` 且该 `site + identity` 非 `VALID` 时不会发起业务抓取。
- Identity 的 session revision 变化会使旧认证观测失效；Dow Jones 三站即使共享 Profile，也必须分别验证，不能因 Barron's 已登录就把 WSJ/MarketWatch 标为有效。

人工登录统一使用 xRDP 桌面的 **Site Login Maintenance**。受限 root bridge 只允许 list/open/verify/close/recover，不把 Docker 权限、管理 token、登录 token或网站密码暴露给桌面用户；同一时间只允许维护一个 Profile/Identity。业务 lease 会先 drain，验证 URL 必须属于当前站点，完成后恢复业务使用。

## 8. Message Bus 消息源配置与生命周期

Bus SQLite 的 `SourceDefinition` 定义一个**采集入口**：`source_id`、`kind`、`adapter_ref`、`acquisition_mode`、入口 URL/内容语言、参数 schema/默认值、调度组/限流、默认 polling/streaming、正文补全模式及启停。它有版本和修订历史。`DefaultMonitoringProfile` 是新 ticker 可物化的默认 source 集合；`TickerSourceBinding` 才是具体 `ticker + source` 的订阅、参数、polling、streaming 和 source 版本。Ticker 自身还有 RUNNING/PAUSED/STOPPED 状态。**注册 source ≠ 启用 source ≠ 绑定 ticker ≠ 已经抓到/发布消息**。

- `MessageBusV2Service.bootstrap()` 只补登记缺失的内建 source 和默认 profile；对既有配置有受限迁移，不应把 seed 当作覆盖人工 Registry 的实时配置。source 更新会检查现有 binding 参数与新 schema 的兼容性；来源、profile、binding 的变更有版本/审计和相应回滚/停用入口。
- 默认 profile 目前包含 Benzinga、Finnhub、Yahoo Finance、IBKR News、Reuters Site Search；Google News Search RSS 是已登记的 source，但**不在默认 profile**。工商时报 `ctee_semiconductor` 已登记，默认 `enabled=false`，不自动绑定任何 ticker。不要把用户删除的 Silicon Analysts Message Bus 源重新补进来。
- 分发入口的 URL、语言、抓取参数和 300 秒默认间隔属于 source；binding 只决定订阅是否有效及本 ticker 的 stream 行为，不能为每个 ticker 复制栏目/抓取进程。新建 `by_search`/`by_distribution` binding 前要求该 ticker 已提交监测词；已有 search binding 可暂走 `LEGACY_TERMS`。
- adapter 可是内建 `builtin:*`、Site Strategy 的 `site:auto` recipe、或受 Crawler Plane 管理的 `crawler:<id>`。动态爬虫的 package/certify/promote 与 Bus source/binding 是两层治理；不能只注册 crawler 就假定 ticker 开始接收消息。Yahoo adapter 内部仍有页面 → NCP → RSS 后备链；搜索聚合结果的最终 publisher 可能与采集入口不同。

入口可配置 provider 过滤、参数校验、scheduler group 限流、失败记录和告警。Yahoo/Google 的隐藏 publisher/domain ingress policy 在入 Raw/补全队列前生效，不是全局去重规则。`PollState` 的采集成功只证明抓取阶段，不等于正文完成、准入通过、stream 发布或 Runtime 消费。

## 9. 三种采集与分发模式

| `acquisition_mode` | 采集键与召回 | 正文和投递 | 当前首批实例 |
| --- | --- | --- | --- |
| `by_ticker` | 已按 ticker 聚合的入口，逐 binding poll；部分历史 API/社交来源维持兼容默认值，不能据此推断其语义已逐一审定 | 逐 binding 补全、ticker 内准入/去重/发布 | Benzinga、Yahoo、Finnhub、IBKR 等维持原路径 |
| `by_search` | 每 ticker 的 L1 生成搜索计划，仍逐 binding poll；不是把搜索结果再用 L2 分类 | 继续原 ticker 绑定的补全/发布链 | Reuters Site Search：每概念单独 query；Google News Search RSS：一条 OR query |
| `by_distribution` | 同一 source 和窗口只建一次共享 run，列出入口全量文章，**只针对本入口有效订阅的 ticker** 建目标 | 共享文章/正文一次；每 ticker 独立 L2/Jev 判定、准入、去重和投递 | 繁中工商时报半导体栏目，默认关闭 |

独立 Bus Worker 和生产 `BusOrchestration` 都识别 distribution；生产调度仍由原有 ticker 交易日历、实时窗口和闭市 SOURCE_SWEEP 驱动，不另开全天采集进程。source limiter、poll checkpoint、失败/覆盖状态仍有意义。`by_search` 的多 query 游标/`query_key` 相互独立，成功 query 的结果可保留，失败不能冒充完整覆盖；闭市 sweep 冻结当次 QueryPlan。

## 10. 统一监测词：L1 搜索与 L2 分发

`TickerMonitoringTerms` 按 ticker 保存不可变 revision，由 `MonitoringTermsService.apply` 以 `expected_revision` 原子提交。**一次提交供该 ticker 已关联的全部 search/distribution 入口引用**；它不自动建立 source binding，也不回放历史。未来 O4 应调用相同服务，而不是逐源复制词表或直接改 SQLite。

- L1 为 1–3 个概念，每概念提供各所需语言表达。source 显式 `content_language` 优先；未写时可从 Site Strategy `default_content_language` 解析并持久化到 source。首批要覆盖 `en`、`zh-Hant`；语言属于采集入口/内容，不是 Browser Identity 的 locale。Reuters `separate` 最多三条独立查询（各自仍可分页），Google `or` 一条查询；有统一词表时优先使用，没有时既有 search binding 暂用旧参数/公司名逻辑并标记 `LEGACY_TERMS`，不能自动截词、翻译或猜投资概念。
- L2 只用于 distribution 内部判定，不限制为三个词。每语言有多组 `any/all/none` 的 literal/限时 regex；组间 OR，入口语言与英语取并集，`none` 只否决所在组。配置同时提交简短的“相关/不相关”定义供 Jev 使用；正文只作为数据，不让页面内容变成指令。
- 每个已附加目标/投递保存当时的 ticker 与 terms revision；同一闭市窗口晚到的订阅者可附加到既有共享 run，复用已发现文章。更新词表不重写旧目标的 revision。新增语言入口缺词时须显示配置不完整，不应暗中降级为错误语言搜索。可用 `terms validate/apply/show/history/preview/test` 校验、提交、审计和单文 dry-run；样例 YAML 不是已批准的生产监测定义。

Jev 是可关闭的**实验轨**：正则与 Jev 并行，任一相关即可分发、双中也只投一次。客户端经 OpenRouter Decisions API，以一篇文章为 state、最多 16 个 ticker 为独立 keyed questions；每 article×ticker 最多两轮，部分失败只补未得有效答案的问题。首次失败仅重试一次，仍失败且正则未命中则终结为 NOT_RELEVANT 并保存失败原因，不无限等待。启用须同时考虑全局开关/key 与 source 的 `distribution_policy.jev_enabled`；禁用时正则仍可用。现行模型/接口以 `jev.py` 为准，不照抄早期方案的 endpoint 假设。

## 11. 从抓取到正文、准入、去重、发布

1. **抓取和持久入队**：adapter 返回 `PollResult` 的文章、失败、checkpoint 与窗口覆盖。普通入口先执行隐藏来源过滤、时间准入和 first-seen 解析，再将 provider 原文以稳定 intake key 入持久 `content_enrichment_jobs`；短社交消息可按 source 配置跳过正文。首轮 bootstrap 可建立 baseline 并抑制旧消息发布，闭市 sweep 有单独窗口上下文。
2. **正文补全**：全局 `ContentEnrichmentHub` 按 lease/claim 处理任务，优先复用可靠的 provider 原生正文，否则按 `body_v2.2`/当前配置调用 `SharedContentExtractor` 与 Site Access；失败按现有预算重试，最终保留有效 title/summary 降级。`EnrichmentJob.owner_kind=ticker_binding` 走普通 `accept_message`；`distribution_article` 无虚构 ticker/binding，共享结果只保存一次，并以 outbox 回报一次正文结果。正文最终 URL 的 publisher 网站决定其 Site Strategy，不按 Yahoo/Google 等采集来源套用登录态。
3. **分发**：distribution 的 run/article/observation 在 Bus SQLite 中持久化，正文版本就绪后创建每目标 ticker 独立 decision/delivery；没有有效订阅不采集。决策和投递有 claim/lease，可在重启后续跑；投递时再次核查 binding/ticker 状态和目标自己的 `AdmissionContext`，然后回到普通 `accept_message`，**不再补全第二次**。正文缺失时只有有效标题/摘要才继续分类；无有效内容终结而不投递。
4. **严格准入**：`admission.py::evaluate_admission` 是共用门。EXACT 实时仅允许约 1800 秒内的消息，闭市要求 `window_start ≤ published_at < cutoff`；DATE/UNKNOWN_FIRST_SEEN 用既有纽约时间“今天与昨天”日期规则，不伪造秒级精度。入口、出队、Raw、发布和 Runtime 接收处各有相关校验/记录；抓取成功或 HTTP 200 不等于可发布。
5. **ticker 内去重**：`deduplication.py` 与 `repository.py` 用 provider 稳定 ID、可确认的文章 URL/重定向别名、原始输入指纹和高质量全文指纹，并检查日期、标题/数字、独立发布语境冲突。不能仅凭相同标题/搜索列表页或验证码内容强行合并。`message_observations` 保留重复发现及 first/last seen；匹配同一逻辑文章时**文章只发布一次**，后来正文补充/业务内容变化作为版本/证据留痕，不能假定每次修订再生成一个 Runtime Case。去重以 ticker 为边界：MU 的已发布结果不能阻止另一 ticker 合法收到同一文章。
6. **标准化和 stream**：有效 Raw 变为 Standard；`IMMEDIATE` 直接发布，`BUFFERED` 按 binding 大小/等待时间 flush，并按业务 owner 分组，避免一个编译消息混入两个 Case 归属。Standard、stream member、ticker 单调 offset 与 Raw 完成状态用事务及唯一键防重复。Runtime 以 `consumer_id + ticker` offset 读取，Coordinator 持久接收/校验后提交 Bus cursor，再推进 Case；不要把 Bus 已发布数、Runtime 已消费数和最终 Case 数混为一个指标。

闭市时每 ticker 仍有自己的 SOURCE_SWEEP task。distribution 可以按相同 source+精确窗口复用一次采集，但 task 要等本 ticker 的正文/decision/delivery 终结，再 flush、冻结 stream highwater；持久失败记录 gap/PARTIAL，不把“模型失败按不相关”误算成采集覆盖完整性问题。分页触顶/查询失败应如实标记 PARTIAL/UNKNOWN，不以首页 200 或固定条数证明历史覆盖。

## 12. 当前上线状态与快速诊断

2026-09-24 远端验收记录确认：Reuters 与 Google source 已迁移为 `by_search`；工商时报 `ctee_semiconductor` 为 `by_distribution`、`zh-Hant`、**disabled**，无默认 ticker 订阅/生产词表，因此尚没有该源真实分发验收。既有 BE/INTC/MU/RKLB 搜索 binding 仍有 `LEGACY_TERMS` 待人工提交。Jev 生产总开关为 false；不能把已接入代码等同于已验证模型增益。验收记录是一个时间点，不替代当前 Registry 查询。

故障排查按阶段查，避免把一种成功误作端到端成功：

| 阶段 | 首先核对 |
| --- | --- |
| 配置/调度 | source 是否 enabled、ticker 是否 RUNNING、binding 是否有效、词表语言/revision、实时与 sweep 日历、due/checkpoint |
| 获取 | adapter route、Site Access resolve/execute 或 API/RSS 出口、分页覆盖、实际 provider 身份/发布时间、失败/告警 |
| 正文 | `content_enrichment_jobs` owner/状态/重试、body outcome/最终 publisher 归属、challenge/订阅权限 |
| 分类/准入 | distribution run/article/decision/delivery、正则/Jev 轨道、终结原因、`message_admission_results` 与各 ticker 窗口 |
| 去重/发布/消费 | observation/逻辑文章、Raw→Standard→buffer/stream offset、Runtime consumer offset、Case/receipt/gap |

操作入口见 `docs/message-bus-acquisition-distribution-operations.md`：先 `migration preview`，监测词用 `terms validate/apply/...`，共享入口用 `distribution status/decisions`。source/binding 的启停通过现有 Message Bus 管理服务/API，而不是改 Site Strategy Registry。不要直接改生产 SQLite、复用人工登录 Profile 做探测，或因 Site Access 稳定就顺手重启它；完整代码回滚前须处理新的 `distribution_article` 与 delivery，旧 Hub 不能读取新 owner。

## 13. 新增或修复消息网站的最短路径

新增一个能向 ticker 发布的来源，需要依次确定 **source/采集模式 → adapter → ticker 监测词与 binding → 正文/访问策略 → 准入/去重/stream 验收**；下面是其中涉及网站访问的路径，不能只完成 Site Strategy 就宣称消息源接入完成。
纯 API/RSS 入口若没有网站爬虫和正文网页访问，可跳过不适用的 Site Strategy/Browser Identity 配置，但仍须完成 Bus source、binding、调度、准入、发布和消费验收。

1. **先确认归属**：列出 publisher/API 域名、support hosts、可能的跨站 redirect；正文按最终 publisher 域名归属。同时确定实际 feed/栏目语言及是 `by_ticker`、`by_search` 还是 `by_distribution`，不要按网站品牌固定分类。
2. **先验证通用能力**：用 generic/现有 Managed 组合测试公开页、正文和 listing；只有证据证明不足才新增适配。
3. **写 Site Strategy**：声明 domains、body ref/参数/access order、crawler ref、auth policy、组合优先级和访问节奏；先 `validate`，再带 `expected_revision` apply。
4. **选择 Identity**：环境、认证体系和出口确实相同时才复用；否则创建独立 Profile+Egress Identity。默认 Managed，External 需要控制实验依据。
5. **实现站点适配**：正文优先增加受限 XPath/可信 adapter；新消息简单场景加 builtin recipe，复杂场景走版本化 Crawler Plane。
6. **配置 Bus**：登记/更新 source 的 adapter、mode、语言、参数 schema 和入口级调度；需要监测词时先提交多语言 L1/L2/definition，再建立明确 ticker 的 binding。distribution 不默认订阅所有 ticker；保持 source 与 Site Strategy 的各自 revision/启停边界。
7. **分别验收**：出口实际 IP、主页、登录/challenge、订阅正文、正文抽取质量、crawler freshness/覆盖、fallback/cooldown、重启续存，以及 Bus 的 Raw/Standard/stream/Runtime 消费必须分开记录。HTTP 200 不能单独证明正文或消息源成功。
8. **上线与观察**：Registry、Bus SQLite 和 Profile 先备份；Profile 冷迁移必须停 writer；部署后检查 Site Access 事件与 `/v1/stats`、Bus source/binding/词表/分发状态、准入/去重/stream，不能用短测结果宣称长期成功率。

修复既有站点时，优先从 `/v1/resolve`、Identity live/runtime、Egress probe、Combination runtime、access events 和 body outcomes 重建事实链，避免直接“换节点”或改 stealth。尤其不要让自动探测打开、关闭或覆盖珍贵的人工登录 Profile。

## 14. 关键代码入口

Message Bus 与下游：

- 配置契约、source/binding/default profile、Raw/Standard/stream：`src/doxagent/message_bus_v2/schema.py`、`manifests.py`、`service.py`、`repository.py`
- adapter 解析和具体入口：`adapters.py`、`news_adapters.py`、`ctee.py`；隐藏来源过滤：`news_policy.py`
- 调度与生产闭市编排：`message_bus_v2/scheduler.py`、`persistent_runtime_v2/bus_orchestration.py`
- 监测词/搜索计划：`monitoring_terms.py`、`search_plan.py`；显式迁移：`migration.py`；操作命令：`cli.py`
- 共享入口持久化与判定：`distribution_repository.py`、`distribution.py`、`relevance.py`、`jev.py`
- 时间准入、逻辑文章去重、stream 编译：`admission.py`、`deduplication.py`、`compiler.py`
- 全局正文任务：`content_enrichment/schema.py`、`service.py`、`extractor.py`、`pipeline.py`
- 下游 Bus stream 消费：`runtime_scheduler/service.py`、`persistent_runtime_v2/coordinator.py`

Site Strategy 与浏览器访问：

- 数据模型与合同：`src/doxagent/site_strategy/schema.py`
- Registry 持久化：`src/doxagent/site_strategy/repository.py`
- 域名解析：`src/doxagent/site_strategy/resolver.py`
- 访问编排/认证/预算/fallback：`src/doxagent/site_strategy/service.py`
- 浏览器执行与导航安全：`src/doxagent/site_strategy/runtime.py`
- 双轨接口：`browser_runtime.py`、`managed_runtime.py`、`external_runtime.py`
- 正式 Chrome owner：`chrome_supervisor.py`
- 默认站点和出口：`seeds.py`；首批双轨迁移：`identity_rollout.py`
- Worker/Admin API：`api.py`；CLI：`cli.py`
- 正文入口：`content_enrichment/extractor.py`、`managed.py`、`pipeline.py`、`strategies/`
- Crawler 接入：`site_strategy/client.py`、`crawler_plane/runtime.py`、`message_bus_v2/adapters.py`
- 登录桌面：`deploy/site-login-ui.py`、`site-login-admin.py`
- 部署：`docker-compose.v2-production.yml`、`deploy/docker-compose.server.yml`

核心回归测试：`test_message_bus_v2.py`、`test_message_bus_v2_deduplication.py`、`test_message_bus_v2_news_sources.py`、`test_message_bus_monitoring_terms.py`、`test_message_bus_search_plan.py`、`test_message_bus_distribution.py`、`test_message_bus_jev.py`、`test_message_bus_ctee.py`、`test_message_bus_migration.py`、`test_site_strategy*.py`、`test_content_enrichment_pipeline.py`、`test_crawler_plane*_runtime.py`、`test_crawler_plane_contracts.py`、`test_site_login_desktop.py`。

## 15. 进一步阅读

- 需求与治理边界：`site_strategy_governance.md`
- 初版实施方案：`site_strategy_governance_implementation_plan_20260921.md`
- 双轨实施方案：`site_access_dual_runtime_identity_implementation_plan_20260922.md`
- Message Bus 分类/分发方案：`message_bus_acquisition_distribution_implementation_plan_20260923.md`
- Message Bus 运维步骤及示例：`docs/message-bus-acquisition-distribution-operations.md`、`docs/examples/monitoring-mu.example.yaml`
- 最近生产验收：`site_access_dual_runtime_identity_remote_acceptance_20260922.md`、`message_bus_acquisition_distribution_remote_acceptance_20260924.md`

上述方案文档解释“为什么”，本文和当前代码解释“现在是什么”；两者冲突时以当前代码、在线 Registry 和实测结果为准。
