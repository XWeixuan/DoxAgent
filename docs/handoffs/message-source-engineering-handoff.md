# DoxAgent 消息源工程交接：架构、判断方法与长期维护经验

> 编写日期：2026-10-20（北京时间）。本次代码核对基线：`main@9f893864014c77bae810666f13067d441457d5d4`。
>
> 面向下一位长期负责消息源开发、适配、正文补全、访问治理和故障修复的 Codex。本文不要求读取旧对话。它交接的不只是代码位置，而是如何区分故障机制、保护生产身份、验证真实恢复，以及哪些已经反复踩过的坑不能再踩。

交付复核时并行工作已将 HEAD 推进到 `b630141e`（Overview Policy hits）。已检查该增量，未修改本文审查的消息源/正文/Site Access 核心实现；没有为保持旧基线撤销其他会话改动。

## 0. 阅读方式、事实边界与接手优先级

### 0.1 本文的事实分层

全文按以下层次表述，不能相互替代：

| 标记 | 含义 | 可以得出的结论 |
| --- | --- | --- |
| **代码事实** | 已在上述提交核对到实现、模型、配置或测试 | 机制存在；不自动证明生产启用或业务通过 |
| **有日期的生产证据** | 仓库内故障报告、脱敏回执和验收记录中的实际结果 | 仅证明指定版本、身份、时间窗和验收范围 |
| **确定要求** | 用户已明确的业务边界和工程决策 | 后续设计应遵循；实现缺口应显式报告 |
| **待核实/待验证** | 没有足够新证据，或者观察窗口不足 | 不能写成“已经解决”“长期稳定” |
| **历史/已替代** | 曾采用或讨论过，已被后续实现修正 | 用来解释演变，不可直接当当前操作手册 |

本轮只做本地代码、文档和已有记录调查，**没有重新连接生产、请求网站、维护登录、修改业务代码或部署**。下文最新生产结论来自已有 2026-10-20 验收；不是本轮实时巡检。动态配置、账号、代理 IP、任务和容器状态接手时必须重读。

代码核对时已跟踪文件无未提交改动，但有大量未跟踪的 `eval/` 等工作产物；不能简称整个工作区“完全干净”。本文之后的提交当然可能改变上述基线。

### 0.2 先记住这十二件事

1. **消息源不是网站，也不是 Profile。** SourceDefinition 管采集入口；SiteStrategy 管发布者网页策略；SourceAccessScope 管来源/发布者的身份所有权与主备；BrowserIdentity 管浏览器资源。混淆它们曾反复导致跨站串用身份和错误维护。
2. **`SiteAccessError`、`search_query_failed`、`ReadTimeout` 是调查入口，不是根因。** 先问这次到底有没有新的网站请求。旧人工阻塞、预算延期、内部 HTTP 中断、驱动死亡都能被包成相似错误。
3. **用户“我打开 Chrome 没看到 Challenge”是有效证据。** 应核对确切原页、实例、访问时间与治理状态；不能反复回答“还是网站风控”。页面已自动通过但人工阻塞仍粘住，确实发生过。
4. **HTTP 200、能开登录页、账号显示已登录、Verify 返回一次成功，都不足以证明消息源恢复。** 必须分别验证列表合同、正文质量、状态收尾和自然轮询。
5. **普通登录与 Challenge 是两条维护路径。** 普通 Open Login 不应被旧 `challenge_url` 劫持；原始 Challenge 页不能由新建首页代替；释放 Viewer 不等于解除 Challenge。
6. **控制 Worker、Chrome 进程、持久化 Profile 和网站认证不是同一生命周期。** 修复控制驱动通常不应该重启 Chrome，更不能删除 Profile。
7. **主备必须真的能接替。** 同 Runtime/浏览器环境、独立身份、实际不同出口、正确认证、真实业务 canary；配置里写着备用不等于可用。
8. **不同采集来源原则上隔离 Chrome 身份。** 同一来源的不同 ticker 共享；不能再为了节省少量内存，把 Reuters、DIGITIMES、Investing 等重新合并进一个身份。
9. **行业源共享采集，但只给订阅 ticker 判定和分发。** 正则与模型为 OR；模型不能否决过松正则。实验模型不可用不应拖死正则命中。
10. **轮询时间、发布时间准入、闭市 sweep 是三个概念。** 用户明确要求全天轮询，不得自行加美股盘中限制；也不能用抓取时间把旧新闻变成新新闻。
11. **多源同时失败先查共享执行路径。** 代理、命名空间、事件循环同步 SQLite、磁盘、投影延迟都曾是真正共因；RSS 失败不证明要给 RSS 加 Chrome。
12. **代码、配置、镜像、运行状态必须对齐。** 曾有有效临时补丁在 main 重建后消失。发布记录只记录漂移，不会替你把补丁合回 main。

### 0.3 接手第一小时应该产出什么

不是立刻点击所有 Verify 或重启全部容器，而是形成一张小的事实表：

- 本地/origin/生产 Git SHA；各相关服务实际镜像与源码指纹；当前 canonical release。
- 用户本轮授权范围：只查、修复、发布、监测、清理，分别是什么。
- 当前失败 source/binding 的最后真实成功、最近实际访问、最近调度、延期、人工任务、前端投影水位。
- source → scope → 当前/主/备 identity → Profile → egress → native instance 的真实映射。
- 哪些是新故障，哪些是旧记录，哪些只是页面投影滞后。

没有证据的格子写“待核实”，不要用旧对话印象补齐。最后一节提供导航和可用检查命令。

## 1. 负责的业务领域与系统边界

### 1.1 业务目标

DoxAgent 是围绕预期差、事实事件和策略研究的股票研究/交易系统。消息源工程负责持续发现信息、保留真实发布语义、取得可消费正文、可靠地送入对应 ticker 的消息流，使下游有机会及时处理事实；它不是直接决定买卖的交易信号生成器。

简化后的业务关系：

```text
外部 API / RSS / 搜索页 / ticker 列表 / 行业栏目
    → 统一 Message Bus 采集、准入、身份识别与去重
    → 正文补全；行业共享文章的相关性分发
    → ticker-local stream
    → W1 新颖性 / W2 Policy 满足 / W3 进一步评估等下游流程
    → 具备完整约束后的交易意图与执行
```

“与 MU 相关”不等于“新事实”，更不等于“命中某 Policy”或“应该交易”。不要为降低下游噪音而擅自在消息采集层增加语义交易筛选，也不要把文章多次重复发布当成下游去重的责任。

### 1.2 全局、行业、ticker 与发布者

- **全局默认来源**：默认监测模板可为新 ticker 建立标准 binding，例如 Barron's、InvestorsHub、GlobeNewswire Search、Investing 等；实际仍以 `manifests.py` 和持久化配置为准。
- **行业共享来源**：TrendForce、DIGITIMES、韩文 RSS 等通常 `by_distribution`，入口统一抓取/补正文，再对实际订阅 ticker 分别判断。没有订阅时不应为每个 ticker 创建无用爬虫。
- **ticker 专用配置**：binding 管启停、参数、轮询/流式行为；监测词是另一份绑定 ticker 的版本化配置，供所有适用入口使用。
- **最终发布者**：Yahoo、Google、HuggingNews 召回的文章可能来自第三方；正文应按最终 URL 发布者解析策略和身份，不能借用入口网站的认证。
- **官方 IR/定制 RSS**：可以通过通用 RSS 等配置接入，并非每一个都必须新增 SiteStrategy 或 Chrome。

“全局默认开启”不等于所有现有 ticker 自动继承此后全部新默认项。binding 是物化配置，历史绑定与新默认可能不同；要分别审计默认模板、source、每个 binding、必要的 access scope。

### 1.3 所有权与职责分层

| 层次 | 主要入口 | 负责什么 | 不负责什么 |
| --- | --- | --- | --- |
| Message Bus 配置/Owner | `message_bus_v2/schema.py`、`repository.py`、`service.py` | source、binding、raw、poll 状态、ticker stream、去重、正文任务和分发 | 不在业务进程直接持有所有网站浏览器 |
| 统一词表/分发 | `monitoring_terms.py`、`search_plan.py`、`distribution.py` | L1/L2/定义 revision、共享文章、每 ticker decision/delivery | 不给未订阅 ticker 广播 |
| Content Enrichment | `content_enrichment/` | 正文作业租约、抽取策略、质量、最终发布者跳转、结果投递 | 不用登录页冒充全文 |
| Site Access | `site_strategy/service.py`、`runtime.py`、`scope.py` | Registry、访问预算、身份选择、业务结果、维护和 Challenge 治理 | 不替换消息源配置，不接管普通模型/数据库/API 流量 |
| Identity Control Worker | `worker_manager.py`、`identity_worker.py` | 每身份的 Playwright 控制、有限恢复与 IPC | 不拥有跨来源的任意浏览器，也不自行写完整 Registry |
| Chrome Supervisor | `chrome_supervisor.py`、`supervisor_state.py` | 正常方式启动 External Chrome、native 实例、锁和持久化清单 | 不根据 HTTP wrapper 错误清空 Cookie |
| 人工维护桌面 | `deploy/site-login-*.py` | xRDP GUI、受限 root 桥接、Viewer、异步维护操作 | 不存网站密码，不自动填 MFA，不授予桌面 Docker 权限 |
| 调度与业务窗口 | `scheduler.py`、`persistent_runtime_v2/bus_orchestration.py` | 持续采集、每 binding/共享源 inflight、实时/sweep 上下文 | 不等同于交易所盘中时段 |
| 前端读取 | `v2_read/`、`api_v2/bus.py`、projector | 消息与状态投影、视图一致性/水位 | 不一定实时等于 Owner DB，必须看 lag |
| 维护 Coding Worker | `source_maintenance/` | 未来独立诊断、窄补丁和可信控制器应用 | **默认禁用、未上线**；不是现行业务恢复必需品 |

当前主线应在这些边界内演进，不要为修单个源重新搭第二套总线、爬虫平台或完整管理后台。

Site Strategy 原意是小型、可持久化、人工维护的策略 Registry：**并非每个域名都需要独有 parser/策略**，未注册专项用 generic；Registry 参数可以替换维护，复杂代码通过版本化 builtin 发布。后来自动 provision 的 publisher scope/Identity 是访问资源隔离，不等于已经为该网站开发了专用正文适配，更不等于新增了一个默认采集消息源。

## 2. 当前消息采集、正文与分发的真实实现

### 2.1 三种模式是采集入口属性，不是网站类型

`SourceDefinition.acquisition_mode`：

| 模式 | 语义与例子 | 容易犯的错误 |
| --- | --- | --- |
| `by_ticker` | 上游已经给出 ticker 新闻，如 Benzinga、Yahoo、Barron's、Investing | 再套行业相关性过滤，或猜 ticker 页面 slug |
| `by_search` | 按统一 L1 计划搜索，Reuters、Google News Search RSS、GlobeNewswire Search | 把多个概念强制 AND 拼接，过窄召回；只改一个旧 binding 搜索词 |
| `by_distribution` | 单入口全量增量抓取并补正文，再给订阅 ticker 判定，如 TrendForce/行业 RSS | 每 ticker 抓同一 feed；把所有返回文章直接广播 |

`SourceKind` 当前只有 `API` / `CRAWLER`，RSS 常归到 API。不要把 kind 与 acquisition mode 混为一谈。`SourceDefinition` 还带 `entry_url`、`content_language`、`site_id`、adapter、search/distribution policy、默认参数和调度；`TickerSourceBinding` 是具体 ticker 的物化绑定。

源码种子是新记录默认值，不是生产 Registry 的完整镜像。幂等 bootstrap 不应覆盖人工 revision；已有 binding 的人工 URL 也不应被新模板无差别覆盖。

### 2.2 统一监测配置

当前入口：`monitoring_terms.py`、`search_plan.py`、`relevance.py`，以及 V2 配置页/监测词 API。

- 每个 ticker 一次提交完整 L1、L2、相关/不相关自然语言定义，携带 `expected_revision`（API 使用 ETag/If-Match 等）。不是多个互相脱节的小配置。
- **L1 最多三个概念**，每个概念拥有不同语言表达；不是每种语言三个后总共九个独立概念。概念表达不塞自定义 OR 语法，使用方式由 search policy/renderer 决定。
- Reuters、GlobeNewswire 为 separate 查询；Google 为单页 OR 计划。不能把 L1 用法硬编码成一个通用连接字符串。
- **L2** 支持分组 `any/all/none`、字段 title/summary/body/all、literal/regex。`none` 只否决本组，不是跨所有语言/所有组的全局黑名单。
- 语言绑定到实际入口 `content_language`，可使用站点默认。提交需覆盖当前要求的语言和英文；目前相关行业配置涉及 `en`、`ko`、`zh-Hant`。不要因为站点域名相同就丢掉英文/繁中入口差异。
- distribution 将入口语言和英文规则作并集；NFC/空白归一化，正则有有限执行预算（当前 20ms），无效/超时不能拖死 Worker。
- 新 revision 作用于后续计划/目标；已冻结 sweep 与已经生成的分发目标保留其 revision。修改词表不是历史消息自动重放命令。
- `terms apply` 不创建来源订阅。词表、source 开关和 binding 必须分别确认。

历史 MU 移除 L1 的泛化词 AI，是宽召回应受限的具体要求，不应把 AI 又作为方便的统一搜索词补回。正式词表属于业务配置，示例 YAML 不能替代用户批准的配置。

### 2.3 正则 + Decision Model：名字仍叫 Jev，供应商已变

**当前代码事实**：`jev.py` 实际为 `BailianDecisionClient`，兼容别名 `JevClient`；使用百炼 `decision-model-preview` 的 System One API，不再调用历史 OpenRouter。关键配置：

- `DASHSCOPE_API_KEY`；独立 `DOXAGENT_MESSAGE_BUS_DECISION_URL`。
- 兼容总开关 `DOXAGENT_MESSAGE_BUS_JEV_ENABLED` 与每来源 `distribution_policy.jev_enabled`。
- 默认端点在代码/运维文档中定义；生产 Compose 的总开关默认仍为 false。**源码支持、来源开关、运行时总开关、有效 key/业务空间、真实返回，是五个不同核对点。** 本轮未重读生产环境，不能仅凭“之前让启用全部能力”断言当前模型已在工作。

每篇文章可成为一个 state，每 ticker 一个独立 `noul` 问题；批请求最多 16 ticker，但答案分别记账。定义组装进 `criteria.true/false`，当前阈值 0.5。长正文分段；当前每请求 15 秒、文章总预算 60 秒、最多两轮，供应商配额/永久错误有区分处理。

真正的分发规则：

```text
只取订阅该入口且当前可投递的 ticker
    → 先按目标冻结窗口准入
    → 正则 与 模型并行
    → regex_hit OR model_score >= 0.5
    → 再查时效/绑定/暂停/去重
    → ticker stream
```

正则命中不用等模型；双命中只投一次。模型是实验增益：正则未命中、模型重试耗尽，则按不相关终结并保留原因，不无限挂起，更不能故障时放行所有文章。

**查低相关 badcase 的正确方法**：读 `distribution_decisions` 与 delivery 的 terms revision、regex/model 结果、命中组、模型错误和最终结果，再在同版本词表重放单篇。早期“鱼雷/盗版黑胶”这类文章进入行业流，必须查出到底是宽词正则还是模型放行，不能仅以模型开启与否解释。OR 语义下，模型再准确也救不了过松正则；修改模型 definition 不会否决 regex 命中。反过来，也不能未取证就认定都是正则。

### 2.4 轮询、实时性与 sweep：不能用一个“30min”覆盖全部语义

**确定要求**：除 TikHub X User Posts 的专门配置外，常规来源目标间隔为 60 秒；用户明确纠正过“应该 24 小时轮询”，不得自行限制成 09:30–16:00。间隔是调度目标，不保证每个 ticker 在精确第 60 秒完成；串行身份与队列会影响完成延迟，但积压必须可见。

**代码现状**：

- `PollingConfig` 默认 60 秒，`active_windows` 默认空；核对既有 binding 时不能只看默认值。
- 生产 orchestration 持有 per-binding / shared-source inflight 并逐个收尾，避免让一个长请求阻塞整个全局轮次。
- 交易日/semantic day 与 CLOSED_SWEEP 仍存在。semantic day 默认以纽约 02:00 划分，处理 DST；不是“当前是否在美股盘中”。
- `bus_orchestration.py` 的 realtime mode 还参考 semantic day 是否为交易所 session；因此“交易日全天资格”与“周末也逐 60 秒 7×24”不能混称。若未来需求是后者，应明确核实并修改，不可宣称本版本已经证明此语义。
- `admission.py` 对 **EXACT** 实时消息执行 1800 秒窗口，并拒绝明显未来时间；CLOSED_SWEEP 使用原冻结窗口。
- **DATE / UNKNOWN_FIRST_SEEN 当前走纽约当地今日/昨日等日期级准入**，不是伪造精确时间后套 30 分钟，也不是滚动 48 小时。此分支与 EXACT/sweep 的行为不同，应按代码测试，不要在报告中笼统声称所有来源严格 30 分钟。

Google 的真实 `pubDate` 不得重写成抓取时间；`dateModified`、网页“几小时前”、搜索命中时间也不能随意当原始发布时间。没有新消息可能是正确的 EXPIRED_PUBLICATION/无新闻，而不是轮询失败。历史停机期间的覆盖缺口需要单独评估，恢复当前轮询不等于补齐缺失历史。

### 2.5 增量身份、去重和入流

`RawMessageInput` 的 external/source item key、URL、发布时间依据、identity evidence 是适配合同，不是可随便填充的占位字段。

- `deduplication.py` 当前是保守的 ticker-local article identity / business-version 证据，不是语义相似度过滤器。
- 只剥离明确 tracking 参数；DIGITIMES Taiwan 的 `shwnws.asp`、The Elec 的文章查询参数携带文章身份，不能统一删 query。
- Google/Yahoo 包装链接需要保留来源与最终链接证据；只有已验证 alias 才能作硬映射。
- 失败补全可能把 summary 复制到 body，代码会保留其实际证据类型；不能因此宣称 FULL 或拿摘要 hash 做跨提供商强去重。
- 新正文版本、供应商版本、文章更新与完全重复要区分；标题相似不是充分同文证据。
- checkpoint、coverage/PARTIAL、admission 拒绝、raw、body、decision、delivery、stream offset 是不同检查点。不能只看最终数量，也不能强行推进 checkpoint 掩盖分页失败。

### 2.6 正文补全早已不是只有通用抽取

现有 `content_enrichment/pipeline.py` 按 resolve 后的 publisher/strategy 选择正文策略、访问顺序、认证要求，保留 generic fallback；专项策略在 `content_enrichment/strategies/`。Registry 可配简单 XPath/等待/访问顺序，复杂 recipe 仍是版本化 builtin 代码。不要重复开发一套“支持按站点策略”的新系统。

- 默认可尝试公开 HTTP、browser、reader 等，站点可改序；必须登录的站点优先真实身份浏览器，避免先制造无意义公开失败。
- 最终发布者跳转有界、有循环保护；跨域重新 resolve，而非沿用入口账号。
- FULL/SHORT_FULL、登录页、订阅墙、Challenge、动态未就绪、部分正文分开；短正文是否完整依网站质量策略，不应全局用同一字符阈值判死刑。
- API 原生全文（例如 IBKR/Benzinga）可以走原生路径；RSS 摘要不当然等于全文。
- `ContentEnrichmentHub` 的 claim/renew/finalize 和 outcome outbox 是可靠交接边界。Owner 不可用、结果未确认与文章未抓取是不同问题，不能清 outbox “消除积压”。
- 作业冻结 `body_v2.1` / `body_v2.2` 等执行版本；只改 enabled flag 不会神奇迁移旧任务。回滚需考虑 pending job 的消费者兼容性。
- **DIGITIMES Taiwan 明确只要免费文章**：列表/元数据能识别付费就不补正文、不 publish；不确定的后续通过完整公开正文门槛阻止付费 teaser 入流。当前来源有 `require_complete_body=True` 等门槛，不能套一般“失败后标题摘要降级可投递”绕开它。

## 3. Browser Identity、主备与人工维护

### 3.1 三层资源与两种 Runtime

```text
source:<source_id> 或 publisher:<site_id> 的 SourceAccessScope
    ├─ primary_identity_id
    └─ 最多一个 fallback_identity_id（冷备）
        → BrowserIdentitySpec
            → runtime_kind / browser_release / Profile / Egress
            → locale / timezone / window / residency / concurrency / rhythm
        → Identity Control Worker
        → Managed Playwright，或 Supervisor-owned External Chrome + CDP
```

- **Managed Playwright**：Playwright 直接启动确定版本浏览器，成本较低，仍是新站点优先试验模式。不能因几个站点高风控就删除这条轨。
- **External Chrome**：系统普通方式启动正式 Chrome 153，长期真实持久化 Profile，Playwright CDP attach 控制。Supervisor 拥有 native Chrome，Worker 只拥有控制连接。
- Profile 长期独立持久化；environment 与 Egress 配置稳定，不主动 UA/Client Hints 随机化、不加 stealth、不把 viewport/语言/时区做成互相矛盾的拼贴。
- 当前 schema 支持固定 locale/timezone/window、默认 12 小时 idle、常驻/按需；强风控身份单并发和秒级间隔。具体值是配置，不是全站永久同一个时区或无限常驻要求。
- Playwright 与浏览器版本受控升级；浏览器大版本升级前保留可恢复 Profile 快照。当前 Site/Supervisor entrypoint 先完成必要的目录/secret 权限准备，再降权为 UID/GID 10001；Managed 启用 `chromium_sandbox=True`。Dockerfile 的 `USER root` 不能单独证明实际浏览器运行在 root。不要为方便默认退回 root/no-sandbox，也不要无必要重建浏览器原生 TLS/网络行为。

2026-09-22 Barron's 控制实验的重要结论是：同 Docker 环境下，普通正式 Chrome 能完成登录，Playwright 直接启动正式 Chrome 失败，普通启动 + CDP 控制又成功，登录还能持久化。它支持“需要 External 模式”的工程选择，**没有证明某一个具体启动参数是唯一根因，更没有证明 CDP 从此免疫风控**。2026-10-20 Reuters 普通 native 页仍有真实 Challenge，就是反例。

### 3.2 从“共享身份节省资源”到“按来源隔离”

早期双轨方案允许多个 Site 共享 Identity，Dow Jones 共享登录、低频站点合并节省 Chrome。这是**历史设计**。随着控制驱动死亡、维护独占、跨站手工任务和冷启动互相影响，2026-10-17 重构明确把故障隔离优先于少量内存节省。

当前 `SourceAccessScope` + `owner_scope_id` 建模来源/发布者所有权，同一 source 的多个 ticker 共享身份，不同独立 source 不再隐式共享故障和维护生命周期。即使域名相同，TrendForce News 与 Press Releases 也应保持来源路由上下文，不因 body resolve 丢掉区分。

最终 publisher 的 scope 选择在 `scope.py`：同站 origin source scope → 明确登记的 `default_publisher` → 已有 publisher scope → 必要时隔离 provision。错误地让早期自动创建的空 publisher 身份抢过显式正文身份，曾导致 Google/Yahoo 到付费文章普遍失败。

域名 `domains` 决定发布者归属，`support_hosts` 允许登录/API/CDN 等依赖，不应借共享 CDN 抢占其他发布者。规则需覆盖实际子域名和跳转；跨 publisher 后重新 resolve。相同认证供应商或同一集团域名不意味着可以越过来源所有权，IBD 排除等内容边界仍须保留。

旧资源名可以保留以继承 Cookie，例如 Reuters 的 `digitimes-de-1`、Investing 的 `public-markets-nl`、Barron's 的 `dowjones-main`。**名字不是当前所有权证明。** 查 scope、identity、Profile 和 native instance；不要看旧名就搬 Cookie、改站点或强制重建。

### 3.3 页面所有权是恢复能力的基础

关键模型在 `governance_models.py`：

- `OwnedBrowserTarget`：allocation、identity、browser instance、worker epoch、target、BUSINESS/CHALLENGE/LOGIN、状态。
- 分配先落可恢复意图，用 `about:blank#doxagent-<allocation>` marker，再建立精确 target 引用；不是“创建完页再试着登记”。
- BUSINESS 页面成功/失败/取消后按精确所有权收尾，支持跨 CDP 连接 reconcile；不能只依赖某个 Python Page 对象的 `finally close()`。
- Challenge handoff 将原页交给 Task，释放业务并发/预算占用；原页仍受保护，不可当孤儿清理。
- Worker epoch 与 native browser instance 不同。控制连接重启不能把旧人类页顺手关闭，native Chrome 已换实例也不能凭相同 URL 认领旧 Task。

防止遗留页泄漏也不能升级为“看到不认识的 Chrome 标签全部关闭”。仅清自己的分配记录/marker 和匹配实例的目标；人类页、旧 Challenge 有独立处置规则。

### 3.4 Challenge Task、维护会话与 Viewer 必须分开

| 对象 | 主要状态 | 状态含义 |
| --- | --- | --- |
| `ChallengeTask` | WAITING_HUMAN / VERIFYING / RESOLVED / TARGET_LOST | 网站原页与待处理业务阻塞；不是 GUI 连接 |
| `MaintenanceSession` | OPEN / RELEASED / CLOSED；kind LOGIN / CHALLENGE | 当前人工维护关系；OPEN 占全局 Viewer，RELEASED 不占 Viewer |
| `MaintenanceOperation` | QUEUED / RUNNING / DONE / FAILED / CANCELLED / INTERRUPTED | 异步命令执行与 stage/deadline，不等同于网站认证 |
| Profile/Identity operational state | AVAILABLE / MAINTENANCE 等 | 资源是否可用于业务，与 auth_state 分离 |
| Auth/capability | VALID、REAUTH_REQUIRED、ENTITLEMENT_MISSING、CRAWLER READY、BODY FULL 等 | 某个用途的实际证据，不能互相代替 |

**普通登录**：应创建正常登录页；不因为 Profile 还留着历史 Challenge 兼容投影，就去恢复旧原页。CLOSED 会话不可复活，已确认 LOST 的旧 LOGIN 应正常结束占位再新建；存在性不明/连接超时不能冒充“已丢失”。

**Challenge**：Open Original 指向精确原页；TARGET_LOST 明示原现场不在，Reopen 是有 predecessor 的新页面/任务，不是假装原页恢复。Later/Release Viewer 保留原页和待办，只释放全局 Viewer，让其他身份能维护。

**Verify**：先识别原页当前真实状态，再做所需 crawler/body canary，最后通过统一健康事务和精确清理收尾。原页不再 Challenge、认证有效、列表可抓、付费正文可读是独立维度。不能因为登录成功直接写 VALID/READY 清库，也不能因为 BODY 超时反称“仍有验证码”。

维护冷启动曾包含 imports、Worker 启动、CDP attach 和无界 cleanup，超出 GUI/API deadline；现在原页可走有限原生 CDP 检查，业务 canary 用独立拥有的页，保留整体预算和 cleanup_pending。下一次看见 Working，先查 operation 的 stage 和阻塞时间，不要再盲目全局加超时。

### 3.5 有效 Fallback 的确切含义

`failover.py` 实现冷启动 canary、按 scope CAS/sticky 激活，当前 `VERIFIED_COLD` 能力有 fingerprint 与 24 小时新鲜度要求。fallback 的核心访问属性需与主身份等效：Runtime、release、环境、认证需求一致；独立 Profile/Chrome、实际不同出口，同区域优先。不同代理节点名称可能仍出同一个 IP，不能当故障隔离。

付费站还需要当前 `session_revision` 的有效认证与两篇不同真实订阅正文样本，随后冷启动接替验证。两篇应在同一 LOGIN 会话取得；每次新开登录推进凭据 epoch，不能拿前一个 epoch 的首篇加到新会话里。fingerprint 变化要使旧证明失效，不可复制 READY。

Profile/Identity 对 Egress 的绑定应遵守 Owner 的不可变约束；变更按受控创建/迁移流程操作，不能暗改 endpoint 把旧身份伪装成另一出口。长期自动 selector 漂移也会破坏“同一浏览器身份”的假设。SSRDOG/Flower_SS 两个订阅是用户给过的候选线索，后者历史上被认为质量较好，不是可写死的健康结论；实际订阅覆盖、listener、IP、区域、目标可用性都需当场验证。

主身份失败时可调度真正可用备用；Challenge 待办本身不应阻止另一个身份接替。备用也失败后，不要静默来回撞主身份；sticky 状态、失败原因和重新验证应清晰。**若备用未登录或能力未验证，应明确报缺口，而不是为满足“有备份”放宽条件。**

目前已有真实公开源冷备接替的历史验收，但并不意味着全部付费备用都准备好。Barron's `dowjones-backup` 最近记录仍无订阅证明，Reuters 主身份仍有人类待办；详见第 5、9 节。

### 3.6 xRDP 桌面、权限与完整 Viewer 链路

桌面工具是 Python Tkinter，不是另一套网站管理后台。源码：`deploy/site-login-ui.py`、`site-login-admin.py`、`site-login-vnc-relay.py`、`site-login-clipboard-bridge.py`，由 `install-site-login-desktop.sh` 安装。

- GUI 安装为 `/usr/local/bin/doxagent-site-login-ui`；root 桥接 `/usr/local/sbin/doxagent-site-login-admin`；桌面账号 `doxagent-desktop`。
- root 桥接固定操作和受校验 ID，内部读取 token；`/run/doxagent-site-login/session.json` 等 root 限权状态不向桌面泄露秘密。
- 桌面用户不加入 Docker 组，不保存网站密码；用户在网站本身手工登录/MFA。不能让用户为正常维护接触 SSH、Docker、管理 token、VNC 地址。
- 不把生产 Profile 改到 xRDP `DISPLAY=:10`；保留独立浏览器显示与 VNC。中文字体不可用时英文 UI 是可接受的简化，不为此重构桌面。
- 当前 GUI 默认按正式注册 source/publisher scope 隐藏 automatic publishers，保留真实 Challenge/会话；高级开关可看全部。ChartMill/Fool/TheStreet 等不应靠永无止境黑名单处理，也不能从 Registry 删除来美化列表。
- Viewer 只监听宿主机 loopback。验收要核对 relay 的 identity/session generation、目标实例及 RFB，不是“5900 有端口”就通过。焦点激活失败不应把健康 Viewer 判成没运行。

**特殊部署依赖**：Site Access 为 loopback CDP/VNC 共享 Supervisor 网络命名空间。仅重建 Supervisor 会让 Site 留在旧 namespace；曾出现宿主机能 DNS、Site 容器不能 DNS，随后所有新维护页/Viewer 失效。`network_binding.py` 与宿主机 `site-access-network-guard.py` 检查真实 namespace inode/Unix socket 健康，30 秒 timer 有界重绑固定服务。`depends_on.restart:true` 单独不够。

这个确定性网络守护不是维护 Codex Worker；不能因后者禁用就一并关闭它。Supervisor 必须升级时协调 Site 重建；持久化 Profile 能保留，但内存活页不能跨原生 Chrome 重启保留，提前记录 TARGET_LOST 和用户授权边界。

### 3.7 新增 Challenge 现场留存，不等于自动解题

`challenge_evidence.py` 保存首次与确认阶段的原生 viewport PNG、脱敏结构化证据与 provider/interaction/confidence。Cloudflare、DataDome、HUMAN/PX、hCaptcha、reCAPTCHA 等按实际活动信号识别；普通登录弹窗、CF 静态资源脚本或广告不构成证据。不确定写 UNKNOWN，不猜题型。

- 关联 identity + native instance + target + Task；先前丢页任务可能没有截图，显示 NOT_RECORDED，不能回填成“当时证据”。
- 目录/文件受限，当前保留 14 天、总量 256MiB、单图 4MiB，截图预算 2 秒；失败只记元数据，不能让截图失败阻塞业务 handoff。
- 管理 API `/v1/challenge-evidence`、`/v1/challenges/{id}/evidence` 和对应 first/confirmed；GUI Challenges/View Evidence，经受限桥接读取。
- 截图可能含个人信息，即使文本已脱敏也不是公共素材；不要把原始账号界面、Cookie、token 或整篇订阅正文提交 Git。

**没有实现打码平台/自动解 Challenge。** 当前能力是准确保存现场，支持之后选择平台或人工判断；自动 solver、自动登录仍是另一个需要授权和评估的开发项目。

## 4. 长期实践形成的判断方式与开发原则

### 4.1 用户真正重视的不是“报错消失”

长期反复提出的要求可以归纳为：覆盖充分、实时、不依赖频繁人工修补；尽量自动恢复，但不伪造恢复；调查要穿过 wrapper 和第一条拒绝码，解释“为什么修完还会再坏”；方案可以有力度，但不要大规模重写或堆砌没有实际保护价值的门禁。

这意味着：

- 对“按方案执行”，先建立文件/合同/测试的落地清单，不得把尚未实施项写成已完成。
- 对“诊断”，先取证，不自动扩大为生产操作；对“修复并上线”，完成相关部署和实际验证，不只交本地 diff。
- 对“不能保守”，主动解决已证实机制，自己决定不重要细节；**不是**获得删除、所有容器重启、账号操作、换出口或旁路安全边界的永久授权。
- 用户睡觉无法回复时，尽量完成安全、在范围内的调查与修复；真的缺新权限就保留证据和阻塞，不能把未经授权的扩张包装成自主性。
- 用户明确排除 IBKR/BE IR/维护 Worker 等子项时，遵守当轮边界。曾被用户删除的内容（如 Silicon Analysts Message Bus）不应顺手“恢复”。

### 4.2 把一次故障拆成四个层次

例：`SiteAccessError → human_challenge_required`。

1. **表层**：Bus 暴露了这个错误。
2. **直接原因**：Site Access 当前拒绝请求，因为身份有人工阻塞。
3. **触发来源**：某次原页确有 Challenge，或者误分类、旧任务重开、维护未收尾。
4. **持续失败机制**：页面自动通过后没有业务证明/状态提交；验证依赖慢 recipe；cleanup 超时使 Profile 留在 MAINTENANCE；下一轮又只读旧阻塞，根本没请求网站。

只解决第 2 层（清 flag、换 IP、重启）可能短暂恢复，却无法防止第 4 层再次制造持久故障。反之，不能因内因存在就否认独立的真实风控：同一事故可以同时包含页面拒绝、驱动恢复问题和旧状态。

### 4.3 保留健康路径，修复最小充分机制

“窄修复”不是只加一行 catch。它可以跨 parser、维护验证、生命周期和部署触及同一条因果链，但应明确为什么每项都必要，并保留其他源的成熟路径。

有效例子：Barron's 改为同源原生新闻 feed + 精确时间，修掉卡片异步等待机制；Investing 为已证实的被动 CF 检查做有界观察和精确原页恢复。无效泛化：所有站点统一等待 60 秒、全局放宽正文门槛、所有异常都换 IP、所有任务都清人工阻塞。

**健康状态并发不能为少见失败被无谓串行化。** 来源之间隔离，但同一高风控身份内需要访问节奏；这两者不矛盾。一个身份排队不要变成全 Bus 等待。

### 4.4 不再重复的错误及替代做法

| 禁忌/旧误区 | 错误机制与后果 | 应用什么替代判断 |
| --- | --- | --- |
| 看到 401/403 就统一叫 Challenge | 接口鉴权、首页风控、普通登录和订阅权限不同；误导人工 | 保存 URL/purpose/status/实际 DOM/活动信号，未分类就诚实 UNKNOWN |
| 把 seen_count/last_check 当发生频率 | 重复读取旧阻塞也更新计数，夸大复发 | 独立 Task/访问 operation 去重，区分 predecessor/reopen |
| 清旧 Task/写 VALID 来恢复 | 删除症状不证明能力，可能泄露原页或带错 epoch | 真实 canary + 正常原子健康提交 + 精确释放 |
| 普通 Open Login 恢复任意旧会话 | CLOSED/失效 RELEASED 被复活，制造 MAINTENANCE | 分 LOGIN/CHALLENGE；核对存活 target/instance，不复活历史 CLOSED |
| 业务 finally 无条件 close 页 | 把人工 Challenge 原页一起关掉 | ownership handoff，人工页由维护生命周期收尾 |
| 更换控制 Worker 等于重启 Chrome | 不必要地丢页面与会话连续性 | per-identity control epoch 与 native instance 分离 |
| 依据 profile 目录还在证明实例没变 | Cookie 存在不等于 native 活页/进程连续 | 比较 Supervisor native instance、PID/starttime 和目标清单 |
| 检测到 SingletonLock 就删除 | 可能仍有真实 single writer，导致损坏 | 查询锁拥有者、进程、挂载和容器，正常 drain/锁恢复 |
| 代理名不同就是独立出口 | 两节点可能同 IP/同供应商故障域 | 实测 IP/区域/目标访问；观测 IP 变动与配置 generation 分开 |
| 把观测 IP 轮换当配置代际升级 | 长驻 Chrome 被 Supervisor 拒绝复用，已有身份突然全部不可用 | 只有实际节点配置变化推进配置代际；记录 IP 观测但不误轮换 |
| 付费备用没登录也允许切换 | 列表也许能抓，正文持续失败 | 按用途验能力、session epoch、双文章和冷启动 |
| 取消 await 就认为线程停止 | `to_thread`/IBAPI reader 可能继续写 DB/开连接 | 跟踪 inflight、等待实际退出再关 adapter/释放租约 |
| HTTP retry 覆盖任意 POST | 副作用重复执行、维护状态竞态 | 只对已证明幂等的只读操作有限重试，保留 operation/request ID |
| 同步 SQLite 小查询可以任意放 async loop | 累计阻塞网络导致假 ConnectTimeout；时间足够也执行不到 | StorageLane/offload、事务批处理、量化 event-loop 与 DB 时间 |
| “前端旧了”就刷新/重放全部 | 真正是 projector lag 或读视图，可能重复业务 | 对比 owner head/checkpoint/API view，再修投影性能/一致性 |
| 强制把过期/无日期卡片当 now | 旧闻变新消息，污染实时策略 | 保留时间 basis，精确来源拒绝无依据时间 |
| 为正文失败降低全局 FULL 标准 | teaser、登录提示、搜索页进入消息流 | 站点质量合同和有效公开/订阅文章 canary |
| 生产源码临时覆盖不归并 | 下次标准 main build 丢修复，无法重现 | patch 进 Git/main；部署 manifest + 文件 hash；核对实际镜像 |
| 统一重启所有服务“确保生效” | 丢原页、断 Viewer、错误 namespace、重建无关身份 | 按改动消费者选择组件；Supervisor 改动单独协调 |
| 用一次绿灯/数分钟窗口声称稳定 | 没覆盖冷启动、维护、竞争、真实新文章 | 分级验收与明确观察时长/操作数/缺口 |
| 清日志/业务表制造干净状态 | 丢诊断、outbox、恢复责任和覆盖缺口 | owner 有界 retention，原始失败/差异保留可追溯摘要 |

### 4.5 以“发生机制”而非补丁数量评价根因修复

反复失败并不总意味着前一个修复是假的。必须区分：

1. 一个确定 bug 已修好，后来另一层暴露；
2. 原补丁没有随发布进入当前镜像；
3. 原机制确实未解决，换出口/重启仅改变初始条件；
4. 新的一次真实网站/代理变化；
5. 其实从未完成业务验收，只是过去汇报过度。

把历史修复前后、下一次失败的源码版本/配置/实例/Task 串成时间线，再判断属于哪类。不要为了维护之前结论而把后续故障都归咎于“随机网络”；也不要因为再次报同 wrapper 就推翻所有前序改动。

## 5. 消息源地图、特殊合同与最新证据

### 5.1 如何读这张表

下表是接手导航，不是实时健康大屏。“最新证据”仅引用可访问记录；没有新实测的项目明确待核实。实际 runtime/出口应以 scope 与 Registry 为准，不凭种子、旧资源名或过去 IP 作决定。

| Source ID / 范围 | 采集与正文特殊性 | 已知证据/接手注意 |
| --- | --- | --- |
| `benzinga_news`、`finnhub_company_news` | by_ticker API；认证 key；原生内容能省正文请求 | API 依赖/配置错误先查生产镜像与 provider；本轮未做实时验收 |
| `ibkr_news` | IB Gateway、IBAPI，既有原生正文路径 | 曾因生产镜像缺 ibapi RuntimeError；也受 gateway 登录/reader 生命周期影响，不能因“官方”排除依赖与关闭竞态 |
| `stocktwits_messages`、TikHub X 系列 | 社交/API；部分明确 SKIP enrichment | User Posts 保留特定轮询配置；不要给所有社交消息硬套网页正文 |
| `newswire_rss` / 官方 IR | 通用 RSS，可配已批准 HTTP routes | 配置 URL 与 ticker 关系独立；BE 官方 IR 曾被当轮明确排除，不应无关扩张 |
| `yahoo_finance_news` | by_ticker；网页优先，NCP/RSS 兼容 fallback；最终正文按 publisher | 10/19 已修 SSR 卡片、文章 JSON-LD 时间，MU/INTC/BE 列表与 FULL 有实测；旧文档的 `/xhr/ncp` 不再是唯一有效路径 |
| `reuters_site_search` | by_search separate L1；强风控；External；搜索和 BODY 分开证明 | 10/20 主备均出现过真实交互 Challenge；人工通过 backup 后约 85 分钟观察无复发，不能宣称永久恢复；见下节 |
| Google News Search RSS | by_search OR；Google wrapper 解码后按最终 publisher 补正文 | XML 成功与 publisher FULL 不同；`google_news.py` 的旧编码/现代签名 batchexecute 都可能变；pubDate 不能被补全覆盖 |
| `barrons_ticker_news` | by_ticker，Barron's + Other Dow Jones，排除 IBD；本源认证要求严格；第三方正文另归发布者 | 10/20 已用原生双 channel 数据代替脆弱 UI 等待；20 分钟自然多 ticker 证据；冷备用未完成订阅证明 |
| `investorshub_ticker_news` | by_ticker，官方页面 locator、列表/文章时间；可链接 ADVFN | 新短 URL 不再含旧 slug，必须传显式 ticker，不能从路径猜；新 ticker 需交易所/locator |
| `investing_ticker_news` | by_ticker，免费文章与 Pro/登录弹窗分开；Cloudflare 可自动通过 | 最新已修有界被动等待 + 原页自动恢复；9f893864 仅约 8 分钟自然验收，需继续验证，见 5.4 |
| `globenewswire_search` | by_search separate，分页合同和完整性 | 用户要求保持 60s，不偷偷降级或改频率；路径/访问政策变化应如实说明，不因“私人项目”宣称不存在风险 |
| `globenewswire_semiconductors_rss` | by_distribution；官方行业 feed；存在受控 browser fallback | 与 Search 同站不等于同一来源；优先 feed 路径，不为所有 RSS 创建常驻 Chrome |
| `trendforce_news` | by_distribution，news 入口；英文 | 历史 ReadError 有内部 Site HTTP 瞬断，ReadTimeout 又有 loop/排队共因；不要全部当站点慢 |
| `trendforce_press_releases` | by_distribution，presscenter，入口/时间/正文不同 | 与 News 同站仍保留 source scope，调度组可能共享；不要被 body resolve 合并成同身份 |
| `digitimes_semiconductors` | 英文 More News，列表公开与订阅全文分开 | 慢提取、订阅墙、维护 Viewer 都发生过；不能以 teaser FULL 冒充；最新账号/冷备需重读 |
| `huggingnews_rss` | Atom，by_distribution；文章可为外部发布者 | 网络 route 与低相关分发分别排查；名称不是全站 Hugging Face 正文；此前是相关性噪音重点 |
| `tomshardware_rss` | 官方 RSS，by_distribution；原文/转载/广告噪音 | 原始 feed 与正文、相关性分别验收，非所有硬件/科技都相关半导体 ticker |
| `thelec_semiconductors_rss` | 韩文官方 RSS，文章 ID 可在 query | 保护 article query 和韩文标题/词表；英文规则并集不替代韩文 |
| `etnews_rss` | 韩文 RSS，现行 HTTPS；短文专项正文策略 | 短但完整可以 SHORT_FULL；不能全局提高阈值误杀 |
| `ctee_semiconductor` | 繁中栏目 crawler，by_distribution；分页有限 | 种子默认关闭；8fc6a4c4 生产为 NBIS/COHR 请求显式启用 source + scope；页面 CF 曾留现场；最新自然发布待核实 |
| `digitimes_tw_rss` | 繁中官方 RSS；只免费，不订会员；公开完整正文要求 | 无新发布可能因付费/时效正确拒绝，不等于 feed 断；HTTP feed route 与正文身份分开查 |
| WSJ/MarketWatch/Seeking Alpha 等 publisher | 可由其他入口发现；注册正文/认证策略，不必都有独立采集 source | 不继承 Barron's Profile 的登录证明；自动 publisher 与正式注册需区别 |

半导体/AI 硬件行业当前十个入口为：`trendforce_news`、`trendforce_press_releases`、`digitimes_semiconductors`、`huggingnews_rss`、`tomshardware_rss`、`thelec_semiconductors_rss`、`etnews_rss`、`ctee_semiconductor`、`digitimes_tw_rss`、`globenewswire_semiconductors_rss`。

2026-10-20 `8fc6a4c4` 的订阅工作为 NBIS、COHR 增加这些既有入口共 20 个 binding，复用已有多语言词表/共享采集；不是新造十套 adapter。`scripts/ops/subscribe_semiconductor_sources.py` 是幂等 owner-service 操作入口，**apply 是变更，不是只读检查**。MU/INTC/BE 是早期主要订阅对象；不能推定所有九 ticker 行业订阅完全相同。

### 5.2 Reuters：必须保留“不稳定已证实”的诚实结论

详见 [Reuters 持续搜索失败诊断](../reuters-persistent-search-failure-diagnosis-20261020.md)。这个案例至少含三条不同因果链：

1. 很多 `search_query_failed` 是旧人工阻塞，attempts 为空，没有新网站访问。原页此时可以正常，用户“没有看到验证码”并不矛盾。
2. 冷验证 imports/Worker/attach、cleanup 拖到 deadline，能力已证明却没释放 MAINTENANCE，恢复流程自己制造停顿。通过有限原生原页读和收尾预算修复。
3. 在这些内因修复后，主身份又产生真实新交互 Challenge；backup 业务成功后约 29 秒也再次产生新 Task。普通 Chrome/native 前台对照仍被挑战，不应继续只怪 Playwright 启动特征。

随后用户人工通过 `reuters-backup` 的旧原页，恢复基线为 **2026-10-20 02:13:54 CST**。只读频率记录截至 **03:38:47**：

- 约 84.9 分钟未发现新的独立 Challenge Task 或实际 ACCESS_CHALLENGE 风险回执；去重业务操作成功 **423 次（CRAWLER 415、BODY 8）**。
- 期间有部署间歇，因此墙钟窗口不等于连续业务暴露；这些是操作回执，不是全部 HTTP 子请求或新闻篇数。
- 主 `digitimes-de-1` 00:14:09 创建的历史 Task 仍 WAITING_HUMAN，不计为复发。
- 九 ticker 中 ONDS 当时是预算延期（并有旧累计失败值）；不能写成九个全部恢复或新增 591 次 Challenge。
- 11 次 queue timeout、4 次 budget exhausted 单列，不混入风控频率。

本地补充证据：`eval/reuters_challenge_frequency_20261020/followup-0338.md`、`frequency.py`。这些 eval 文件可能未进 Git，克隆后不保证存在；核心结论已在本文保留。后续统计必须读取完整时间窗，不能用最近 100 条事件估计频率。未复发只能给出观察下界，不算长期平均频率。

自动掉号登录尚未实现；当时没有可用 `credential_ref`，而且登录无法解决所有交互 Challenge。若未来做，先证明失败确为 REAUTH_REQUIRED、提供安全凭据途径、保留 MFA/Challenge 边界，不能把它当万能恢复脚本。

### 5.3 Barron's：从“原页正常但一直失败”找到 recipe/恢复耦合

详见 [Barron's 持续 ticker 失败诊断](../barrons-persistent-ticker-failure-diagnosis-20261020.md)。用户认为账号没掉、维护未见 Challenge，调查确实发现：旧原始页已是正常首页，旧人工会话/阻塞仍在；Verify 的 crawler recipe 自己超时，而不是又被网站挑战。

旧 recipe 等 `News_index`、滚动、双标签链接变化、`. ET` 日期稳定，网站真实新闻数据已来但 UI 日期仍是 placeholder；25–35 秒内仍不能完成。改为 **同源、同已打开 Chrome 页面内的浏览器原生 fetch**，读取 `/market-data/api/news` 的 Barrons 与 BarronsDowJonesNetwork 两 channel，按 `timestampUtc.value` 解析精确时间。

这里的关键不是“绕过浏览器用 Python 请求隐藏 API”：保持原 Cookie/Egress/TLS/浏览器网络行为，两 feed 都要满足合同；拒绝、无效 JSON、缺一边不能吞掉冒充成功；IBD 仍排除。两个真实空 feed 可以成功空轮询，壳页或缺数据不行。

没有看见 NCP 操作也不是“没执行爬虫”的通用证据：`/xhr/ncp` 是 Yahoo 的特定历史路径，旧 Barron's 本来做 DOM/滚动/标签，新 Barron's 用自己的新闻接口。每个源按实际 recipe、attempts、Worker 与原生网络响应判断，不能套用另一站的操作痕迹。

继续验收又暴露：冷 attach 遍历关闭中的旧 Playwright Page；BODY canary 错选历史 `/login`；普通登录复活旧 RELEASED/CLOSED 会话；全页事件等待被广告拖慢。都在保持身份/出口的前提下分别修复，而不是再换节点。

最后 03:11:35–03:31:42 的 20 分钟只读窗口有 174 次实际 CRAWLER 成功、无新风险，九 ticker 每个至少 17–19 个独立轮询时点；两轮正常 Open/Verify/自动收尾、真实订阅 FULL 单独通过。**没有符合时效的新文章**，所以没完成自然“新 raw→新 FULL→新入流”实例；不能用分别通过的列表和旧文正文冒充它。`dowjones-backup` 仍需单独登录和订阅/冷备证明。

### 5.4 Investing：最新修复不是“把广告当验证码”的简单误判

初期报告在 [Investing 持续失败诊断](../investing-persistent-failure-diagnosis-20261020.md)；该报告内 8 秒等待等早期值，已被后续 `ff572967`、`9f893864` 修正，以当前代码与 changelog 为准。

最新复发证据：一个 ONDS 原页已 Verify 成功，51 秒后 RKLB 顶层 bootstrap 403、`cf-mitigated: challenge`、Just a moment 和活动 CF 证据形成新 Task。之后同一个 RKLB 原始 Target 四次原生读取均有正常十条新闻，Task 却仍 WAITING_HUMAN。说明它不是单纯误认登录弹窗，也不是必须人工做题，而是**会自行通过的浏览器检查 + 系统过早升级为永久人工阻塞**。

当前窄修复：

- 原生文档观察上限 30 秒，已观测 Challenge 的被动 grace 25 秒，均在原请求总预算内；普通无 Challenge 的不完整文档仍保持 12 秒上限，不能全站无条件多等。
- 同主 frame 的真实后续 2xx 才可替换 bootstrap 403；不能因 DOM 好看伪造 HTTP 200。
- 独立 `ChallengeInspector.recover_passive_investing` 每轮只检查既有原页：限定 Investing + CRAWLER + 已知 CF 证据、同 URL/Target/instance、真实 ticker heading 与正式 parser；有 OPEN 人工会话不抢占。不是 reload、solver 或额外站点请求。
- 健康事务校验 Task/目标未变，提交 Task/crawler 证明后释放 reservation、关闭确切已解决页。不顺便虚构 auth/BODY READY，不推广到 Reuters/付费 BODY Task。
- 原生响应体可在当前已完成的同 URL 2xx navigation 上作最多 3 秒 observation，解决 DOM 被广告重绘成半壳；仍要正确 ticker/title/parser。不是 Python 额外抓一次。
- Worker 退休后排队命令向旧 closed pipe 写入的竞态也已修；这不是网站风控。
- 人工验证证明发生问题的原 ticker，不再固定只验 INTC；外置 Chrome 原页不要求重建旧 Playwright human Page 才能进入业务验证。

最新已有生产回执：Site Access 为 `9f893864`，Supervisor/native instance、Profile、出口保持；约 **06:09:41–06:17 的 8 分钟**有 29 个去重 CRAWLER 成功结果、九 ticker 状态绿、无新 Task；相关最终回归 108 passed/1 skipped。**这是短窗证据，不是长期恢复保证。** 本地证据位于 `eval/investing_recurrence_20261020/`，可能未进 Git。fallback 历史 BODY/TARGET_LOST 不在这次自动清除范围，仍需独立维护/证明。

## 6. 新消息源开发与适配：如何形成可维护的实现

### 6.1 调查先于选技术

开始前回答这些问题，而不是先复制一个 Playwright 模板：

1. 这是新的采集入口、已有站点的新栏目，还是只新增 ticker 订阅？是否有官方 API/feed？
2. 入口按 ticker、search、distribution 哪种语义？同站是否存在英文/繁中等不同入口语言？
3. 新闻何时出现在入口？分页/滚动/“More News”如何增量更新？有真实发布时间、更新版号与 stable article ID 吗？
4. 新闻数据在 SSR、客户端 DOM、页面自身原生 JSON 请求还是官方 API？广告/完整 load 是否与业务数据无关？
5. 列表、免费正文、付费正文是否各有登录要求？登录弹窗是阻断还是可忽略提示？
6. 最终正文是否第三方？能否给出正确 publisher/domain 与原始/解析链接？
7. 不同出口/模式在**完整目标行为**上是否可用？付费源至少实际完成登录并读取文章，不以登录页出现作为对照成功。
8. 当前 robots/条款/账号/限额/内容使用边界如何？用户过去要求 60s 和不自动降级，不等于未来可宣称任何法律/平台风险为零；存在冲突应直接指出并给可行边界。

记录一次可重复的列表、article、日期、空列表、拒绝/登录页样本；fixture 脱敏，不永久保存不必要整篇付费正文。与 source 相关的语言、locator 和解析合同应显式配置，不靠自由推断。

### 6.2 选择通道的顺序，不是僵硬等级

- API/官方 feed 若满足覆盖和时间合同，通常最简单；不必为了“全部走统一治理”加 Chrome。
- 静态 HTML/JSON 公开 HTTP 若稳定、权限允许且满足数据合同，可以用轻量路径。
- 已在浏览器里加载且需要真实 Cookie/同源行为的内容，可观察原生响应或在合法已打开页内使用现有同源接口；优先 observation，不为监听所有资源套 `page.route`。
- 爬虫按用户明确要求先试 Managed；完整业务验证失败、对照说明启动环境/身份连续性必要，再用普通正式 Chrome + CDP。不要只比较首页 200。
- fallback 可以是 API/RSS 业务路径（Yahoo），也可以是访问身份主备；两者不同，报成功时应报告到底哪一路成功。
- 不引入 TLS MITM/HTTP 重建；现有 Clash 链路若能端到端转发只验证，不为“升级反爬”重构。原生 Chrome 自然生成身份头，避免自相矛盾 override。

### 6.3 实现位置与完成顺序

1. 先写 parser/adapter 的输入输出合同和 fixtures：article key、最终 URL、title、summary、language、published time basis、metadata、pagination/empty/partial。
2. 加 `SourceDefinition`/必要默认模板；行业模式增加入口语言、policy，不复制每 ticker 进程。
3. 网页型入口按需登记 SiteStrategy、域名/support hosts、body/crawler refs 和 source scope；通用策略能满足的别造无用专项。
4. 建立来源隔离主身份、同等属性冷备；复用有明确唯一所有权的历史 Profile 可避免丢 Cookie，不能把其数据拷到多个并发 Profile。
5. 对 ticker locator 建可信映射/验证入口。Investing 的 NBIS 历史 slug 是 `yandex-nv`、RKLB 是 `vector-acquisition`；自动拼 ticker 很容易指错页。
6. 做真实列表、真实正文和同源跳转；有认证则维护真实主身份。备用不满足要求就明确未完成，不能假装 ready。
7. 用 owner 服务提交配置/绑定/词表并核对 revision，不直接改 sqlite JSON。
8. 接入调度/状态/前端，测试无新闻的成功空轮询、失败不推进 checkpoint、typed deferred 不伪装成功、页/锁收尾。
9. 自然观察后再给完成结论；重要实现追加 `changelog`，留下短报告、版本和验收缺口。

### 6.4 不可省略的适配验收维度

| 维度 | 最低应该证明什么 |
| --- | --- |
| 列表合同 | 正确 ticker/查询/栏目、两个必要 channel、分页、稳定 ID、真实时间；假 200/空壳应拒绝 |
| 正文 | 至少真实代表性 FULL/SHORT_FULL；登录/付费/公开分别分类，最终 publisher 正确 |
| 认证持久化 | 需要时完整登录；受控重连/重启后 Profile 仍有效；不同主备 Cookie 独立 |
| 并发/故障隔离 | 同源多 ticker 工作，不拖死其他来源；BUSINESS 页取消/超时仍收尾 |
| 主备 | 冷启动、实际不同出口、所需能力、真实激活和后续自然使用；不是 config list |
| 入流 | 在有合时效新文章时贯通 raw→正文→分发/去重→ticker stream；没有样本则明确缺口 |
| 相关性 | 只订阅 ticker、词表 revision、regex/model 轨迹，典型正负样本 |
| 状态 | poll/provenance/task/operation/前端水位可信，旧失败会在自然轮询后更新 |
| 恢复 | transient、明确 Challenge、TARGET_LOST、维护取消/重开、Worker cold/retire、内部服务短断 |
| 持续性 | 多自然轮次、冷路径、维护后恢复；24–48h/更长窗口和资源趋势另报告，不提前打勾 |

## 7. 故障调查与修复操作方法

### 7.1 第一步：重建事故，不急着再发请求

对单个 badcase 先保存一份脱敏事实链：

```text
ticker/source/binding、UTC 时间与展示时区
→ raw/article/query plan/admission context/terms revision
→ poll status/error/attempts/checkpoint
→ Site operation/request、scope/generation、选中 identity/egress
→ native instance/worker epoch/owned target
→ 实际网络/页面分类与最终 publisher
→ 当前 Task/Session/Operation/capability
→ outcome/delivery/stream/read-view
```

不必每次取整个数据库。选能区分假设的最小时间窗，前后至少覆盖最后成功、首次失败、一次修复和下一次失败；原始状态先冻结，后续快照分文件，不覆盖唯一基线。复杂事故应把“用户做了什么”和“系统随后做了什么”分别列出。

维护/Verify/真实 probe 会创建页面、请求网站和改健康状态，**不是只读诊断**。若本轮是只读观察，优先查 SQLite `mode=ro`、现有日志、Supervisor/CDP 元数据；不要借“监测”额外测十个 URL，改变被测对象。

### 7.2 第二步：按故障域分层，而不是按 wrapper 归因

| 观测 | 优先核对 | 能证伪什么/下一步 |
| --- | --- | --- |
| 多 ticker 同源同时失败 | scope 人工阻塞、单身份队列/进程、共用 locator、真实最后访问 | 不应把九 binding 当九次独立网站 Challenge |
| 多个不同 crawler + RSS 同时失败 | event loop、SQLite/WAL、磁盘、DNS、代理、共享 HTTP client、发布变更 | 独立脚本成功而 daemon 超时，优先应用内部执行问题 |
| `human_challenge_required` 且 attempts 空 | Task 原始时间/前后继、manual flag、维护占用 | 当前无新网站证据；不能反复要求重做验证码 |
| `identity_in_maintenance` | OPEN/RELEASED/CLOSED 与 profile 投影、cleanup_pending、operation stage | 认证不一定掉；可能成功证明后未释放或复活旧 LOGIN |
| `SiteAccessError`/ReadTimeout | 外部耗时与 Bus→Site 内部 HTTP 时间、server request ID、队列预算 | Site 已成功但内部响应读断，可做有限幂等重试；不应先换代理 |
| `search_query_failed` | 各 L1 query 的 attempts、partial/coverage、预算、真实页面内容 | 全查询无成功不能刷新 succeeded；错误可能在适配/恢复而非网站 |
| 200 但 parser 无数据 | ticker heading、栏目合同、原生响应、DOM hydration、日期 placeholder | 检查内容是否已在网络响应但 UI 未完成；别等 networkidle |
| 列表通过、BODY 失败 | 最终 publisher、策略、登录/订阅、正文候选、body URL 选择 | 不能拿登录 URL/列表页验证全文，也不应把 BODY 失败算出口故障 |
| `RuntimeError`/closed pipe/Node fatal | Python/Node PID、epoch、V8 heap、worker 实际活性 | native Chrome 可能正常，先控制 Worker 有界恢复 |
| 主机 DNS 正常、Site 容器失败 | Site/Supervisor namespace、容器 resolver/network、Unix socket health | 修部署绑定机制，不反复 retry DNS 或只重启一次 |
| 轮询正常但没有新流 | 时效拒绝、免费策略、去重、body job、decision/delivery、outbox、ticker pause | “无新消息”未必故障，须给出具体终点与原因 |
| Owner 新、前端旧 | Bus/runtime head、projector checkpoint、API view generation/token | 不回滚正常采集；定位投影积压或旧 view |
| SQLITE_FULL/readonly/locked | 文件实际挂载、空闲空间/inode、WAL、写锁/DDL、容器用户 | 访问成功后落库也会失败；不要当成站点风控 |

同时记录“不支持”的推测。例如广告弹窗仅遮住一角、真正文章正常，并不支持 Challenge；反之，顶层 403+活动 CF frame 支持真实检查，即使后来消失。证据应容纳状态随时间变化。

### 7.3 对比实验怎样才有解释力

固定 source/query、同 Profile、同 Egress、同 Chrome release、相近时间，一次只改变一个重要维度：Managed vs 普通启动；普通启动 vs CDP attach；DOM vs 同页原生响应；原页读 vs 冷 Worker 重连；daemon vs 同容器独立脚本。

必须记录：实验是否实际完成登录/正文，是否新建页，是否人工触发，原生实例是否相同，控制连接是否冷启动，以及请求是否影响后续风控。两个实验先后成功/失败只能支持条件性结论；共享 WAF 历史和时间漂移可能是混杂因素。

不主动输入账号密码/点击验证码/复制 Cookie；用户人工动作单独标记。诊断临时页由创建它的实验关闭，不能关闭原始 Challenge 页。对生产旧文章的真实 canary 不应发布到实时消息流。

### 7.4 突然停止采集：一条实际可用的调查路径

1. 对比 Owner 与 UI：先知道是否真的停止，不浪费时间修投影延迟造成的假故障。
2. 看 source/binding/polling/ticker 状态与 inflight：禁用、暂停、错日、排队、sweep、延迟不是网站拒绝。
3. 看最后成功→首次失败的版本/配置/egress/worker/instance 是否变化，查最新部署是否包含原修复。
4. 有真实请求才看 status/DOM/原生数据；无请求先查治理阻塞/预算/生命周期。
5. 对内容异常做 parser fixture；对连接/运行异常做可重复的故障注入；对网站拒绝保原页和证据，确认是否还有真正可用 fallback。
6. 实施针对因果机制的改动，测试不伤正常页和其他来源；按授权窄部署。
7. 验证状态收尾与多次自然轮询，再检查新的文章链路。缺新文章就写缺口，不伪造新闻。

### 7.5 重复失败：必须增加的调查

- 查历史报告和 `git log`，画出修复前→短时恢复→下一次失败的 **版本/配置/身份/任务变化图**。
- 比较这次实际出错层与上次；相同 wrapper 不代表同根因。
- 查是否重建镜像丢补丁、配置保留旧 revision、credential/session epoch 改变、备用过期、浏览器资源泄漏、队列随时间增长、磁盘重新填满。
- 找能稳定制造该机制的最小测试：例如 worker 退休后排队写 pipe、native target 先关闭但 PageClosed 未到、CLOSED session 被选中、被动 Challenge 超过旧 grace 后自行通过、同步 DB 让网络 callback 无法调度。
- 对长时回归要求状态转换和资源趋势，不只重复一条 happy path。无法重现的供应商故障标为外部证据限制，不强行写一个“根治”补丁。

### 7.6 多源同时异常：先查共性，否则会越修越乱

2026-10-19 的代表案例：同容器独立 Python 对 Site readyz、TrendForce、The Elec 很快成功，daemon 却报数十秒网络超时。15 秒 py-spy 中 299 个样本有 291 个（约 97.3%）落在同步 SQLite 路径。这证明至少该 Bus 进程的共因不是所有站点同时变慢。

修复不是放弃时效/去重，而是：StorageLane 把同步存储操作移出网络事件循环，first-seen/admission 批事务，ticker pause 每轮只读一次，终态 sweep 不重复装载，租约取消等待实际同步提交结束再释放。后续相关代码不能把小的 repository 调用不断搬回 async loop，重新制造同类问题。

这项采样针对当时 Bus，不应泛化成“所有正文服务已证明 97% 都是 DB”。不同进程分别量化。若要上 profiler，应按授权和有限窗口，避免高频采样本身干扰高负载生产。

### 7.7 认证/Challenge/维护故障：不要再让用户来回盲试

先回答四个问题：

1. 当前动作是 LOGIN 还是 CHALLENGE？选中的 Session/Task/Target 是否真属于此 source/identity？
2. 用户看到的页面与程序失败的页面是否同一 native instance/target？新开的首页正常无法否定旧列表 challenge，也不能用旧失效页阻止正常登录。
3. Verify 卡在原页识别、crawler、body、commit 还是 cleanup？哪个实际能力已证明，哪个未测/失败？
4. Viewer 被谁占用、释放后是否仍留 Task、业务 permits 是否释放？DNS/namespace/relay/RFB/target focus 哪一段真失败？

保留明确结果，例如“Challenge 已消失，crawler parser 超时，BODY 未测试，会话未收尾”，而不是“验证失败请重试”。普通登录、人工 Task、fallback 各有状态，应报告真正需要人做的那一件事和对应身份。无法打开 Chrome 的情况，先把维护路径自身修通，不能要求用户去解决看不见的验证码。

### 7.8 前端时间严重滞后的调查

用户曾发现消息源状态、左侧消息流停在 9 点，而运行状态页 W1/W2 仍有新记录；根因是读模型投影，不是源不轮询。

检查 Owner DB 最新时间/seq → Bus 运输 head → projector checkpoint → read generation/current → API view token/as-of → 前端展示。前端日期还要确认 UTC/CST/ET。若所有字段在 Owner 也旧，才回头查采集。

修复采用投影的有界批处理、每 tick/domain 连接复用等，不共享跨 HTTP 请求的危险 SQLite connection，不跳过事件可见性/refs/checkpoint 来追水位。存储重构还有 alias/generation、15 分钟 view/30 分钟 tail 等语义，不能硬改旧 token 无限有效。真实 PROVENANCE_UNVERIFIED 保留，不做“全绿”造假。

### 7.9 “修复完成”的证据阶梯

每次报告明确处在哪一级：

1. **代码层**：可重现故障测试、parser fixtures、边界/并发/取消回归、Ruff/diff。
2. **部署层**：确切 SHA/镜像/运行文件 hash、必要环境开关、namespace/健康，无意外身份变化。
3. **业务能力层**：真实列表、正文、认证/订阅/主备分别通过；旧 Task/session 正常收尾。
4. **自然运行层**：停止主动干预后多个 ticker、多轮自然访问；失败/延期分别计数，状态与 UI 对齐。
5. **长时层**：覆盖冷启动、维护后、代理波动、重连、资源增长等；24–48h 或更长按实际范围报告。
6. **覆盖/质量层**：新文章端到端与漏抓/晚抓、相关性正负样本，不能从访问成功率直接推导。

一次 API probe 是第 3 层的一部分，不是第 5 层。数十次轮询仍可能没有一篇新时效文章；有新文章但 BODY 失败也不能因 poll succeeded 宣称全部可用。重叠测试套件的数量不要相加成虚假“全套通过”。

## 8. 内存、磁盘、发布与持久化数据保护

### 8.1 资源是可用性的组成部分，不是事后清理任务

Chrome 内存、Node V8 heap、tab 数、队列、SQLite WAL、镜像、日志和 Profile cache 会互相影响。曾有 Chrome 仍活、控制 Node heap fatal；也有网页成功后 Registry 写 SQLITE_FULL，最终表现为 Site API 500 和正文 outbox 未确认。

先量化资源 owner：每 identity native/control 进程、BUSINESS/人工 targets 数、内存上限/OOM/restart、DB/WAL/磁盘/inode、构建缓存/镜像引用。不要仅凭宿主机“还有内存”推断容器没有压力，或看到 DB 大就直接 VACUUM。

当前主要治理：

- 每身份独立控制进程，强风控主身份常驻、冷备按需；有界退役/恢复而非不断留 orphan driver/tab。
- 业务页按 owner 回收，Challenge/LOGIN 原页保留；同 profile single-writer 和 graceful shutdown。
- `retention.py` 小批、有限时间处理：成功访问明细约 7 天并有小时汇总；风险事件/已解任务等各有不同保留期，未解决任务不被同一规则删除；统计物理回收为 false，删除行不等于磁盘立刻缩小。
- `resource_gc.py` 只处理可再生成 cache/dump 等，尊重 Profile 锁、真实进程/FD 和目录边界；不删 Cookies、账户、Service Worker 业务状态。不要因反爬而禁用 cache/Service Worker。
- 存储重构 `S1` 已进一步调整 cache 目标 512→256MiB、PMA/dump 48h/128MiB、普通快照两份等；这些不全写在同一个函数，不能见 `resource_gc.py` 的 512MiB 触发阈值就断言全系统目标仍旧。实际 owner 配置和执行回执一起看。
- Challenge 截图独立限额；其他 distribution/native/read 资源也有自身 owner/retention。跨模块数据不可当消息源缓存一起删。

用户过去授权过激进清理某些低价值缓存/日志，但不是下一轮所有路径的永久删除授权。当前调查任务不要删除。需要清理时先确定精确目标、挂载、真实引用、打开文件与恢复集；不能 `docker system prune -a`、删 Profile、操作 containerd 内部，或把 `/home/ubuntu/.cache` 一概视为下载缓存——里面曾有代码/验收备份。

### 8.2 发布漂移是实际故障源

版本应记录至少四层：

1. Git 主线/修复提交；
2. 各组件实际镜像与运行源码指纹；
3. Registry/source/binding/terms revision、runtime flags；
4. native Chrome/Worker/Task/session/capability 当前状态。

本地 main = origin main = 生产 checkout，不保证生产每个容器已构建相同内容；反过来，各组件不同 SHA 可以是刻意窄发布，不必强行全部同一 SHA。重要的是每个组件包含其所需改动，继承的旧修复没有被覆盖。

`scripts/ops/storage_release.py` 是当前统一构建/发布/资源收尾入口，使用 `/var/lib/doxagent/releases` 的 per-component current/previous、release lock 和精确 manifest。状态可能含 Compose 环境秘密，**不要直接 cat 整个 state.json/compose.current.json 给工具或文档**；只选必要的非秘密字段。稳定 dependency key 避免每次源码修改叠加巨大 app 祖先层。

早期 `Dockerfile.*-repair` 和一次性 overlay 仍可作为历史复现材料，不是未来默认发布入口。不能绕过 canonical release 随意执行原始 compose，丢掉现有镜像覆盖、secrets、网络/资源配置。若要改发布流程，先了解其 rollback/引用保护。

### 8.3 根据代码消费者选择重建，而非机械“全部 build”

| 改动范围 | 通常关注的组件 | 必须额外核对 |
| --- | --- | --- |
| source adapter/parser/manifest/scheduler/distribution | `v2-message-bus` | source/binding 物化配置是否需迁移、自然轮询/分发 |
| 正文抽取/质量/hub | `v2-content-enrichment`，有时 Bus | 执行版本/pending jobs/结果 outbox |
| Site runtime/维护/治理 | `v2-site-access` | Worker/原生页连续性、状态收尾、用户当前维护 |
| native Chrome/Supervisor/共享网络 | `v2-chrome-supervisor` + 协调 Site | 活页丢失授权、Profile 保留、namespace guard/Viewer |
| 桌面 GUI/bridge | 主机安装路径 | root 权限、重新打开 GUI 即加载；不必因此重启 Chrome |
| 前端/Read/API/projector | 对应 web/API/projector | view/generation/checkpoint，不重启身份来修读模型 |
| 依赖/共同基础模块 | 逐消费者确定 | 不能仅因 Dockerfile COPY 整个 src 就称所有容器必须重启 |

这张表是起点，不是固定依赖图。跨文件 import/功能消费者才是最后依据。不要拿上次“全部 main push/build”授权覆盖本轮只读任务。发布前保留真实 current/previous 回滚路径、容量和磁盘约束；历史方案的 20GiB 与当前构建守护阈值不是同一个永久常量，以本轮批准方案和当前发布器为准。

## 9. 关键历史案例与决策演变

这里选择影响判断方式的案例，不复述全部修复流水。早期报告保留的是当时事实，后续修复可能替代其中的操作建议。

| 阶段/案例 | 起初的解释或做法 | 更深层证据与演变 | 应继承的原则 |
| --- | --- | --- | --- |
| 9/21 统一治理 | body/crawler 独立代理与浏览器，通用提取为主 | 同网站用途应统一身份；Registry 管网页策略，不能替换整个源配置 | 拆清采集配置与访问治理，最终发布者路由 |
| 9/22 Barron's 控制实验 | 登录页正常就可能算通过；尝试修 UA/环境 | 完整登录才暴露 PW 直接启动失败；普通正式 Chrome + CDP 可完成并持久化 | 对照完整业务行为；保留双轨，不做随意 stealth |
| 9/24 行业分发 | 全量正文再 regex/model；旧文章也排 classifier | 大量过期文章占无效分类队列；必须入库/挂目标时及发布前准入 | 时间语义贯穿链路，不能靠最终拒收挽救无限积压 |
| 9/28 TrendForce ReadError | 像网站网络不稳 | Site 已成功，Bus 读内部 HTTP 瞬断 | 区分内部传输与外部访问；只读有限重试保 request ID |
| 9/30 Barron's identity 失效 | 看起来是要换节点/重新登录 | 出口观测 IP 变化误增 generation，长期 Chrome 被拒绝复用 | 观测与配置代际分开，不能破坏已有身份连续性 |
| 10/1–4 多源超时/Node 崩溃 | 共享驱动恢复/轮换、短时换出口 | Chrome 活但 Node fatal，人工页又阻止整个驱动退役；共享控制资源互相传染 | control/native 生命周期分开，最终每 identity 独立 Worker |
| 10/9 磁盘归零 | 网站访问/正文报错 | 网页成功后写库失败、outbox 积压；构建/日志/状态反复增长 | 修产生机制和 retention，清理不是长期架构 |
| 10/12–15 部署与恢复 | 换到可用出口、修 target 创建、临时 overlay | 缺跨连接收尾；临时补丁未归 main；旧版 build 又复发 | ownership 持久化、发布谱系与代码回流同样重要 |
| 10/17 架构审计 | 一次一次按站点表层修 | 53 份历史文档聚合，发现原页丢失、持有 permits、共享 Identity、无效备份等机制 | 从失败→修复→再失败审查，不只问第一条 HTTP code |
| 10/18–19 隔离重构上线 | 新架构就应该整体恢复 | native cleanup/Supervisor 尚未全上线、namespace 残留、Viewer 误探测、GUI会话竞态 | 部署状态也是系统状态；逐链实测，不以“方案完成”代替 |
| 10/19 登录顺序误诊 | 误把 Reuters→Barron's 的报错归成一个 Viewer 冲突 | 用户明确先 LOGIN 失败，再试 Challenge，再回 LOGIN；第一条其实旧 Challenge 劫持普通登录 | 尊重用户时间线；一个事故可以含多个后续错误，不倒因为果 |
| 10/19 crawler+RSS 共性失败 | 大量网络/站点出错 | daemon loop 被同步 SQLite 占住，独立脚本快；另有 locator 缺失 | 用对照剥离共享执行问题，不给每个站点加 retry |
| 10/19 前端晚一小时 | 似乎没有新 poll/message | Owner/Runtime 新，projector 旧；自动 publisher GUI 也漏筛 | 数据读取的真实性属于维护目标；别修错采集层 |
| 10/20 Reuters | 修冷连接/维护后就应永久恢复 | 真正新 Challenge 多次复发，native 普通页也失败 | 修复内因不等于解决供应商风控，观察下界不能算频率 |
| 10/20 Barron's | 又是账号/Challenge | 原生新闻 feed 已到、UI 等待超时、BODY URL 错、旧 LOGIN 复活 | 用数据合同替代广告/UI稳定；维护本身要能完成 |
| 10/20 Investing | 登录弹窗误判，或人为解题不足 | 真实被动 CF 检查会自行通过，早升级人工 gate 后不恢复 | 短暂自动检查有界观察；精确原页证明后正常恢复，非全局清 flag |

10/17 审计将历史分类为：确定 bug 修复后不同层再坏、补丁被覆盖、真实新拒绝、未业务验收却过度汇报。该分类仍值得使用。原 P0/P1/P2 方向中的页面所有权、跨连接收尾、独立控制 Worker、故障分类、真实业务验收继续有效；跨站共享 Identity 节省资源等与后续用户补充冲突的旧设计不再优先。

## 10. 已实现、未完成、待验证：接手工作清单

### 10.1 代码已经具备，不要再从零设计

- 三种 acquisition mode、统一多语言监测词、search plan、共享正文/每 ticker decision 与 delivery。
- 正则 + 百炼 Decision Model 实验轨，沿用 Jev 字段/开关；不是仍在 OpenRouter 的旧实现。
- 站点专项/通用正文策略、最终 publisher 解析、结果 outbox、准入/去重/执行版本。
- 双 Browser Runtime、来源/发布者 scope 隔离、独立 Identity Worker、native Supervisor、single-writer、owned target。
- Challenge 与 LOGIN 分开、异步 operation、Viewer release/切换、TARGET_LOST、统一验证提交与有限 cleanup。
- 冷备能力 fingerprint、认证 epoch/双文章、真实 canary/sticky activation；**实现存在不等于每个备用已经配置完成**。
- namespace 健康与确定性网络 guard、Viewer RFB 路径、正式来源 GUI catalog 筛选。
- bounded retention/resource owner、统一 release/current+previous；采集/分发/正文存储 offload 与批事务。
- Challenge first/confirmed 截图和结构化证据；Investing 被动 CF 精确原页恢复。

### 10.2 优先核实/后续事项

| 优先级 | 项目 | 已知边界与建议接手动作 |
| --- | --- | --- |
| P0（核实，不盲改） | 当前真实故障与部署基线 | 本文不是实时巡检；读取现有状态先区分旧 gate/延期/新访问/投影，避免所有源再重启 |
| P1 | Investing 最新被动恢复长时效果 | 9f 只有约 8 分钟多 ticker 自然证据；看 Task 是否再次粘住、总预算/串行排队、原页恢复是否只处理合格情况 |
| P1 | Reuters Challenge 频率与容量 | 最新只读约 85 分钟无新增，主历史 Task 仍在；统计完整窗独立 Task/成功操作，队列延期单列；不要又许诺永久恢复 |
| P1 | 真实付费备用可用性 | Barron's backup 未获订阅/冷备证明；Investing fallback 历史 BODY/TARGET_LOST 仍独立；其他付费源逐个查当前 epoch/canary |
| P1 | 新文章端到端自然验收 | Barron's 最近观察窗无新准入文章；列表/旧文 FULL 通过不填补新入流证据缺口；行业订阅/CTEE同样按实际样本核实 |
| P1/P2 | 模型实验轨生产状态与质量 | 读布尔开关/有效 key 存在性、端点/业务空间和真实 decision 回执；绝不输出 key；正负样本区分 regex/model 放行 |
| P2 | 全天轮询与非交易日语义 | 无 intraday active_windows 已确认；semantic calendar 仍控制 realtime。若要严格7×24逐60s，需需求/代码对照，不擅自新增盘中门禁 |
| P2 | DATE/UNKNOWN 的日期级准入 | 当前不是所有消息统一30min；确认与最新业务预期是否一致，不能文档替系统做决定 |
| P2 | 长时资源曲线/容量 | 每身份独立增加资源但应保隔离；观察24–48h/7天增长、target/driver/DB/WAL、冷备峰值；不靠合并不相关身份省资源 |
| P2 | Google 包装解码等外部非公开合同 | 代码含现代签名/batchexecute，外部可变；查失败阶段与最终publisher，不误路由空身份，也不假造pubDate |
| P2 | 一个月反复事故的回归覆盖 | 重点状态机、取消/冷启动/重发布/备用认证过期，别只有parser happy path；保持历史失败样本 |
| 暂缓/明确禁用 | 维护 Codex Worker | 仅本地实现/默认关闭，不能顺手上线；见10.3 |
| 未实现/另立任务 | solver/自动账号重登 | 截图识别不等于解题能力；先证明需求/平台支持/认证边界，不从本交接推导授权 |

优先级表示工程价值，不等于允许下一位 Codex 未经新用户任务直接执行。不能把旧计划上每个 TODO 都永久认作未完成：例如 10/18 尚未发布的 Supervisor 收尾，已被 10/19 的后继回执覆盖。

### 10.3 维护 Codex Worker 的真实状态及最大局限

实现位于 `source_maintenance/`，操作文档 [维护能力与局限性](../message-source-maintenance-implementation-and-limitations-20261005.md)，独立 Compose `deploy/docker-compose.source-maintenance.yml`。默认 `enabled=false`、`mode=shadow`；**不上线、不部署、暂不启用是用户明确要求**。

它不是 O1/O2/O3/O4 研究 agent，也不是 ticker 初始化 Guardian，更不是 namespace 网络守护。实现包括：持续失败健康信号与共享事故归并、独立库/租约/预算、脱敏证据、独立 Codex SDK thread/候选 worktree、窄符号 patch 策略、冻结基线测试、受限 Site 操作、可信 Controller 配置 CAS/窄镜像应用、canary/比较式 rollback、发布 manifest。

Worker 无业务 DB/Profile/站点秘密/Docker/SSH 权限，可信控制器才有窄动作能力。离线定向测试存在，**真实 SDK、Docker apply、长期无人值守未完成生产验收**；10/04 方案早于来源隔离与 canonical release，未来启用前必须重新检查适配，不能直接启动旧模板。

最大未闭环问题是**代码回流**：

```text
main 基线 → 独立补丁分支 A → Controller 应用到正式常驻服务
         → 后续补丁 B 基于实际已部署链
         → 本地 main 仍可能没有 A/B
         → 普通 main 重建可能把有效修复覆盖
```

部署 manifest/`deployment-head.json` 能记录谱系、防止一部分盲覆盖，**不自动 push/建 PR/merge main，也不自动同步本地**。ticker 初始化额外分支可在独立任务容器中完成一次性工作；常驻消息源修复则要影响正式长期服务，两者不能类推。

未来启用应补齐：代码修复应用后形成可审查分支/PR 回流、明确 APPLIED_NOT_RETURNED 状态；未经授权不自动 merge；正式发布必须包含或明确回滚已生效补丁。驱动重连/既有组合切换/Registry 参数调整与 parser/adapter 源码修改分别记账，配置审计不能代替 Git 归并。当前不要为了消除这项局限擅自把 Worker 改成自动 merge main。

## 11. 代码库导航、状态入口与命令

以下路径相对仓库根目录。文件可能继续拆分，先 `rg` 找调用者，不要因为本文写了旧行号就跳过当前代码。此处不固定行号，避免一个补丁使导航整体过期。

### 11.1 核心路径地图

| 入口 | 查什么 |
| --- | --- |
| [Message Bus schema](../../src/doxagent/message_bus_v2/schema.py)、[manifests](../../src/doxagent/message_bus_v2/manifests.py) | source/binding/poll/raw/stream 模型、默认注册与参数/频率 |
| [factory](../../src/doxagent/message_bus_v2/factory.py)、[compiler](../../src/doxagent/message_bus_v2/compiler.py)、[adapters](../../src/doxagent/message_bus_v2/adapters.py) | 实际 adapter 注册/构造，API 与 site:auto，依赖/配置消费 |
| [service](../../src/doxagent/message_bus_v2/service.py)、[repository](../../src/doxagent/message_bus_v2/repository.py) | 采集接纳、持久化、配置版本、raw/body/poll/stream 状态 |
| [scheduler](../../src/doxagent/message_bus_v2/scheduler.py)、[orchestration](../../src/doxagent/persistent_runtime_v2/bus_orchestration.py) | 调度/活跃窗口、inflight、全局共享采集、语义日/sweep |
| [admission](../../src/doxagent/message_bus_v2/admission.py)、[semantic_clock](../../src/doxagent/semantic_clock.py)、[calendar](../../src/doxagent/persistent_runtime_v2/calendar.py) | 精确/日期级时间、纽约02:00边界、交易日，不要混淆盘中 |
| [monitoring_terms](../../src/doxagent/message_bus_v2/monitoring_terms.py)、[search_plan](../../src/doxagent/message_bus_v2/search_plan.py)、[relevance](../../src/doxagent/message_bus_v2/relevance.py) | revision/语言/L1渲染/L2组语义与正则预算 |
| [distribution](../../src/doxagent/message_bus_v2/distribution.py)、[distribution_repository](../../src/doxagent/message_bus_v2/distribution_repository.py)、[jev](../../src/doxagent/message_bus_v2/jev.py) | 共享文章状态、每ticker目标、OR判定、当前百炼provider、重试/预算 |
| [deduplication](../../src/doxagent/message_bus_v2/deduplication.py)、[source_identity](../../src/doxagent/message_bus_v2/source_identity.py)、[storage_lane](../../src/doxagent/message_bus_v2/storage_lane.py) | 保守同文/版本证据、发布者展示、async/同步存储边界 |
| [news_adapters](../../src/doxagent/message_bus_v2/news_adapters.py)、[industry_sources](../../src/doxagent/message_bus_v2/industry_sources.py)、[market_sources](../../src/doxagent/message_bus_v2/market_sources.py) | Reuters/搜索、行业HTML/feed parser、iHub/Investing/Globe recipe |
| [barrons_sources](../../src/doxagent/message_bus_v2/barrons_sources.py)、[google_news](../../src/doxagent/message_bus_v2/google_news.py)、[yahoo_sources](../../src/doxagent/message_bus_v2/yahoo_sources.py)、[ibkr_news](../../src/doxagent/message_bus_v2/ibkr_news.py) | 最新专项原生数据合同、wrapper解码、Yahoo路径、IB reader |
| [enrichment service](../../src/doxagent/content_enrichment/service.py)、[extractor](../../src/doxagent/content_enrichment/extractor.py)、[pipeline](../../src/doxagent/content_enrichment/pipeline.py) | claim/renew/finalize，版本，访问/跳转级联 |
| [strategies catalog](../../src/doxagent/content_enrichment/strategies/catalog.py)、[adapters](../../src/doxagent/content_enrichment/strategies/adapters.py)、[quality](../../src/doxagent/content_enrichment/quality.py) | 正文专项/通用策略，FULL/SHORT_FULL/墙页判定 |
| [Site schema](../../src/doxagent/site_strategy/schema.py)、[governance models](../../src/doxagent/site_strategy/governance_models.py) | identity/environment/egress/auth 与 scope/target/Task/session/operation |
| [Site repository](../../src/doxagent/site_strategy/repository.py)、[governance repository](../../src/doxagent/site_strategy/governance_repository.py)、[scope](../../src/doxagent/site_strategy/scope.py) | Registry事实、CAS/能力提交、最终发布者/原始来源路由 |
| [Site service](../../src/doxagent/site_strategy/service.py)、[runtime](../../src/doxagent/site_strategy/runtime.py)、[budget](../../src/doxagent/site_strategy/budget.py)、[failover](../../src/doxagent/site_strategy/failover.py) | 访问执行、排队/并发、分类、fallback候选/证明/激活 |
| [worker_manager](../../src/doxagent/site_strategy/worker_manager.py)、[identity_worker](../../src/doxagent/site_strategy/identity_worker.py)、[worker_runtime](../../src/doxagent/site_strategy/worker_runtime.py) | 每identity控制进程、IPC、epoch、cold/recovery/retire |
| [managed_runtime](../../src/doxagent/site_strategy/managed_runtime.py)、[external_runtime](../../src/doxagent/site_strategy/external_runtime.py)、[chrome_supervisor](../../src/doxagent/site_strategy/chrome_supervisor.py) | 两轨接口、CDP/native所有权、进程/锁/实例 |
| [maintenance](../../src/doxagent/site_strategy/maintenance.py)、[maintenance_document](../../src/doxagent/site_strategy/maintenance_document.py)、[verification](../../src/doxagent/site_strategy/verification.py) | LOGIN/Challenge/Viewer/Verify业务证明与收尾 |
| [challenge_inspector](../../src/doxagent/site_strategy/challenge_inspector.py)、[challenge_evidence](../../src/doxagent/site_strategy/challenge_evidence.py)、[investing_document](../../src/doxagent/site_strategy/investing_document.py) | 原页存在性/被动恢复、截图类型证据、有界原生文档观察 |
| [targets](../../src/doxagent/site_strategy/targets.py)、[profile_storage](../../src/doxagent/site_strategy/profile_storage.py)、[retention](../../src/doxagent/site_strategy/retention.py)、[resource_gc](../../src/doxagent/site_strategy/resource_gc.py) | 跨连接页收尾、快照、限额、真实进程/锁保护 |
| [network_binding](../../src/doxagent/site_strategy/network_binding.py)、[network guard](../../deploy/site-access-network-guard.py) | Site/Supervisor真实namespace与幂等部署修复 |
| [V2 bus API](../../src/doxagent/api_v2/bus.py)、[source_snapshot](../../src/doxagent/v2_read/source_snapshot.py)、[v2_read](../../src/doxagent/v2_read/) | 状态builder/读快照/投影水位，前端假滞后调查 |
| [settings](../../src/doxagent/settings.py)、[production compose](../../docker-compose.v2-production.yml)、[server overlay](../../deploy/docker-compose.server.yml) | 实际环境变量、服务依赖/挂载/namespace、资源默认值 |
| [release](../../scripts/ops/storage_release.py)、[desktop install](../../deploy/install-site-login-desktop.sh)、[subscription helper](../../scripts/ops/subscribe_semiconductor_sources.py) | 当前发布入口、受限主机工具、既有来源订阅；均需分清读/写命令 |

另外 `crawler_plane/service.py` 是独立能力边界；不能把它与 Site Access 的全部网站访问逻辑或行业分发混称成“爬虫模块”。`source_maintenance/` 则是另一个默认关闭框架。API/Read/Frontend 的监测词编辑已实现，没必要重复造 CLI 之外的配置后台。

### 11.2 状态与数据库入口

**容器内默认路径**（先核对当前有效 Compose 与实际 mounts，宿主机路径不可照抄）：

| 数据 | 默认路径/入口 | 重要内容 |
| --- | --- | --- |
| Bus | `/data/bus/bus.sqlite3` | `source_definitions`、`ticker_source_bindings`、`poll_states`、`raw_messages`、`content_enrichment_jobs`、`site_strategy_result_outbox`、stream、terms/distribution表 |
| Site Registry | `/site-data/registry/site_strategy.sqlite3` | strategy/identity/egress/profile、site runtime/auth/events/outcomes，以及scope/target/Task/session/operation |
| 持久浏览器身份 | `/site-data/profiles` | single-writer Profile，不能当cache；凭据根 `/site-data/credentials` 是秘密 |
| Challenge证据 | `/site-data/challenge-evidence` | 私有PNG与索引/结构化证据，限额/权限保护 |
| Supervisor 控制 | `/run/doxagent-chrome/supervisor.sock` 等共享控制挂载 | native实例、CDP endpoint/生命周期；不要打印完整敏感配置 |
| Runtime/Read | `/data/runtime/runtime.sqlite3`、`/data/read/v2.sqlite3` | 下游处理与投影，Read可能alias至当前generation |
| 发布 | `/var/lib/doxagent/releases` | canonical manifest、current/previous、capacity/收尾；原始state可能含秘密 |
| 桌面桥接会话 | `/run/doxagent-site-login` | root session/lock；不向桌面展示token |

查询表结构时先读当前 schema 或 `sqlite_master`，不要假定文档列出所有表/字段。不要对正在 WAL 运行的库使用 `immutable=1` 假装完整快照；文件复制也不等于 SQLite 一致性备份。

**重要陷阱**：`build_site_strategy_service()`、`build_message_bus_v2_runtime()`、部分 repository 构造器会执行 schema/bootstrap/种子。即使你接下来只调用 list，也不保证整个诊断只读。严格只读时使用 Owner 的只读 API 或 sqlite URI `mode=ro`，不要启动一份第二个 Owner 去“读取生产”。

### 11.3 首次接手的只读命令示例

本地 PowerShell（仓库根）：

```powershell
Get-Location
git status --short
git branch --show-current
git rev-parse HEAD
git log -10 --oneline
git diff --stat
rg -n 'investing|reuters|barrons|TODO|待验证' docs dev_plan/workflow_v2
rg -n 'SiteAccessError|search_query_failed|manual_attention_required' src/doxagent
uv run --no-sync python -m doxagent.site_strategy.cli --help
uv run --no-sync python -m doxagent.message_bus_v2.cli --help
```

`--no-sync` 仅使用既有环境，环境未准备好先核对 `pyproject.toml`/`uv.lock`；不为写交接或查看帮助擅自更新依赖。帮助安全不代表所有 CLI 子命令只读：Bus `run-once` 是真实业务采集，terms 构造 repository 也可能建表。

生产历史入口为 SSH alias `doxagent-sg`、仓库 `/home/ubuntu/doxagent`、Compose project `doxagent-v2`；接手先验证，不把 IP/容器 ID 当永久常量。下列是只选非秘密字段的示例，不是本轮已执行：

```powershell
ssh doxagent-sg "date -u"
ssh doxagent-sg "sudo git -c safe.directory=/home/ubuntu/doxagent -C /home/ubuntu/doxagent rev-parse HEAD"
ssh doxagent-sg "sudo docker ps --format '{{.Names}} {{.Image}} {{.Status}}'"
ssh doxagent-sg "df -h /"
ssh doxagent-sg "sudo systemctl status doxagent-site-access-network-guard.timer --no-pager"
ssh doxagent-sg "cd /home/ubuntu/doxagent && sudo python3 scripts/ops/storage_release.py storage-status"
```

上例最后一条输出发布器的脱敏 storage-status，而不是原始 release state。容器 restart/OOM/image 可用 `docker inspect --format` 选字段；不要完整输出 `Config.Env`。日志用 `--since`/`--tail` 有界读取，先检查脱敏；凭据文件存在性可以查权限/是否存在，不能 cat 值。

旧对话曾明文提交过供应商 key。本文不复制任何值；若尚未轮换，应通过正常秘密管理另行处理，不把旧对话中的值当新环境配置来源，也不在本次文档任务顺手修改生产秘密。

只读 SQLite 示例（把占位路径替换为**由实际容器 Mounts 核实的宿主机库路径**；原样不能执行）：

```powershell
@'
import json, sqlite3, sys
from pathlib import Path
db = Path(sys.argv[1]).resolve(strict=True)
con = sqlite3.connect(db.as_uri() + '?mode=ro', uri=True, timeout=2)
try:
    con.execute('PRAGMA query_only=ON')
    names = con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    print(json.dumps([row[0] for row in names]))
finally:
    con.close()
'@ | ssh doxagent-sg "sudo python3 - /VERIFIED/HOST/PATH/site_strategy.sqlite3"
```

频率监测已有 `eval/reuters_challenge_frequency_20261020/frequency.py` 可参考完整窗口与 operation 去重；使用前读脚本和当前 schema/时区/基线，不照抄恢复时间。若用户只许监测，该脚本之外不要调用 Verify、probe 或网站。

### 11.4 配置和维护命令：存在，但有写入/网站副作用

以下命令已核对 CLI 解析器；它们是后续授权任务的操作导航，不是当前任务执行清单。管理 token 用现有受限运行环境/secret 注入，**不要把值写入命令、报告或仓库**。

```text
python -m doxagent.message_bus_v2.cli terms show --ticker MU
python -m doxagent.message_bus_v2.cli terms history --ticker MU
python -m doxagent.message_bus_v2.cli terms preview --ticker MU
python -m doxagent.message_bus_v2.cli terms validate --file <approved.yaml>
python -m doxagent.message_bus_v2.cli terms apply --file <approved.yaml> --actor <actor>
python -m doxagent.message_bus_v2.cli terms test --ticker MU --source <source_id> --article <fixture.json>
python -m doxagent.message_bus_v2.cli distribution status --source <source_id>
python -m doxagent.message_bus_v2.cli distribution decisions --ticker MU --limit 50

python -m doxagent.site_strategy.cli site list
python -m doxagent.site_strategy.cli identity list
python -m doxagent.site_strategy.cli profile status --site-id reuters
python -m doxagent.site_strategy.cli events --site-id reuters --limit 100
python -m doxagent.site_strategy.cli site export reuters
```

读取本地 Bus CLI 仍需注意上节 repository 初始化副作用。`terms test` 仅本地单篇规则 dry-run，不自动访问网站、不重放入流。

`site apply`、identity apply、egress probe、combination probe/activate、profile login/verify、migration apply 都是写入或真实访问。旧 profile login CLI 是兼容入口，当前人工恢复优先正式 GUI/异步 maintenance operation；不要为操作员重新暴露 login_token 手工流程。

发布器支持 `build/deploy/release/prepare-inputs/reconcile-resources/bootstrap/storage-status`；**除 storage-status 外不能当只读诊断命令**，`reconcile-resources` 也能回收资源。build/release 需精确 commit/组件，按当前 `--help` 和 canonical manifest 操作；不在交接里硬编码下一次发布 SHA 或给无条件 restart 指令。

### 11.5 测试导航与使用方式

项目为 Python 3.11–3.13、pytest/pytest-asyncio、Ruff；版本以 `pyproject.toml`/`uv.lock` 为准。当前 Playwright 锁 `1.63.0`，SDK锁 `0.159.3`；Chrome 构建在 server overlay 指定正式 `153.0.8010.52-1`。升级前按现行依赖/浏览器发布流程，而非 `pip install -U`。

| 领域 | 有价值的测试入口 |
| --- | --- |
| 模式/词表/分发/模型 | `tests/test_message_bus_monitoring_terms.py`、`test_message_bus_search_plan.py`、`test_message_bus_distribution.py`、`test_message_bus_decision_model.py`、`test_message_bus_jev.py` |
| 解析/时间/去重/HTTP路线 | `test_message_bus_industry_sources.py`、`test_message_bus_market_sources.py`、`test_barrons_native_news_capture.py`、`test_message_admission.py`、`test_message_bus_v2_deduplication.py`、`test_message_bus_rss_routes.py` |
| 正文 | `test_content_enrichment_pipeline.py`、`test_content_enrichment_native.py`、`test_content_enrichment_hub.py`、`test_content_enrichment_publishers.py` |
| runtime/隔离/所有权 | `test_site_strategy_dual_runtime.py`、`test_identity_control_workers.py`、`test_source_access_governance.py`、`test_source_isolation_migration.py`、`test_site_strategy_lifecycle.py` |
| 维护/原页/被动恢复 | `test_site_login_desktop.py`、`test_site_maintenance_recovery.py`、`test_reuters_maintenance_cold_inspection.py`、`test_investing_recovery.py`、`test_investing_passive_recovery.py`、`test_challenge_evidence.py` |
| 资源/存储/投影 | `test_message_bus_storage_lane.py`、`test_distribution_storage_retention.py`、`test_storage_release.py`、`tests/v2_backend/test_projection_freshness.py` |
| 禁用维护Worker | `test_source_maintenance.py`、`test_source_maintenance_controller.py`、`test_source_maintenance_lifecycle.py`、`test_source_maintenance_refactor.py` |

例如只改纯 parser 时可以先运行：

```powershell
uv run --no-sync python -m pytest tests/test_barrons_native_news_capture.py tests/test_message_admission.py -q
uv run --no-sync ruff check src/doxagent/message_bus_v2/barrons_sources.py
git diff --check
```

修改共享生命周期应扩大到 worker/维护/原页/预算/正文/真实repository回归，不可只跑 parser。先读 fixture 和网络边界，避免所谓离线回归不小心触发真实 provider/网站。已知 Windows 符号链接权限可能导致一个明确条件 skip；历史有基线失败或旧fixture未适配时，应标明具体项、是否基线复现和影响，不删除断言制造全绿。

测试是机制证明，真实生产多轮/付费正文/冷备依然另验；本交接编写没有重新跑上述业务套件，不借用历史通过数冒充本轮测试结果。

## 12. 历史文档索引与证据优先级

### 12.1 遇到文档冲突时如何裁决

业务要求以用户最新明确决定为准；当前实现以当前代码为准；是否生产生效以实际部署与对应业务回执为准。这三者可能暂时不一致，要报告差异，不是用其中一个覆盖另外两个。

同一问题按**后继实际验收**覆盖早期未部署状态。例如 10/18 Supervisor 最终细化仅 BUILT_NOT_DEPLOYED，不能在读到 10/19 已协调上线后仍称“尚未部署”。同样，10/20 Investing 初版 8 秒 grace 不能覆盖当前 25 秒实现；旧 Jev/OpenRouter 文档不能覆盖百炼 client；旧多站共享身份方案不能推翻来源隔离决定。

### 12.2 推荐阅读的长期材料

**先读当前工程手册，再按问题追历史**，不必一次通读几十份报告：

- [统一采集/分发操作](../message-bus-acquisition-distribution-operations.md)、[监测词编写](../message-bus-monitoring-terms-authoring.md)。其中早期 300s/CTEE 默认关闭/语言首批描述有历史性，以当前 manifest 与 Registry 为准。
- [消息管线与站点治理指南](../../dev_plan/workflow_v2/message_pipeline_site_strategy_governance_guide.md)、[Site 运维手册](../site-strategy-operations.md)。后者含早期一站一Profile、headless切换、Yahoo NCP 等旧操作说明；不能整篇当成当前维护UI流程。
- [10/17 反复故障架构审计](../../dev_plan/workflow_v2/message_source_recurrence_architecture_audit_20261017.md)：最有价值的历史机制总览、E1/E2/假设分层。
- [10/17 隔离、有效主备与维护实施方案](../../dev_plan/workflow_v2/message_source_isolation_failover_maintenance_refactor_implementation_plan_20261017.md)、[实现进度](../../dev_plan/workflow_v2/message_source_refactor_implementation_progress_20261017.md)、[10/18发布验收](../../dev_plan/workflow_v2/message_source_refactor_release_and_acceptance_20261018.md)：当前重构的设计理由及当时缺口。
- [维护/Yahoo/namespace收尾](../site-login-maintenance-and-yahoo-repair-20261019.md)、[独立会话与验证修复](../site-login-independent-sessions-and-verification-repair-20261019.md)：普通登录/Challenge边界、Viewer及原页验证。
- [crawler/RSS共同loop饥饿](../message-source-common-loop-starvation-repair-20261019.md)、[Read新鲜度与维护列表过滤](../message-bus-read-freshness-and-maintenance-filter-repair-20261019.md)：为什么网络错误和UI时间不能按字面归因。
- [Reuters诊断](../reuters-persistent-search-failure-diagnosis-20261020.md)、[Barron's诊断](../barrons-persistent-ticker-failure-diagnosis-20261020.md)、[Investing诊断](../investing-persistent-failure-diagnosis-20261020.md)：最新三条深层故障链，注意本文记录的后继修正。
- [存储架构方案](../doxagent-storage-architecture-refactor-plan-20261018.md)、[执行回执](../doxagent-storage-refactor-execution-20261018.md)、[main归并复核](../storage-main-merge-review-20261019.md)：当前资源owner、发布器、Read与版本/数据保护。
- [维护Worker方案](../../dev_plan/workflow_v2/message_source_maintenance_codex_worker_feasibility_and_implementation_plan_20261004.md)、[实现与局限](../message-source-maintenance-implementation-and-limitations-20261005.md)：未来再启用时的边界，当前禁用。

**基础设计与控制实验**：

- [9/21代码勘察](../../dev_plan/workflow_v2/body_crawler_policy_governance_code_survey_20260921.md)、[原始需求](../../dev_plan/workflow_v2/site_strategy_governance.md)、[初期治理实施](../../dev_plan/workflow_v2/site_strategy_governance_implementation_plan_20260921.md)。用来理解为什么只管网页访问，不重构全部源配置。
- [9/22浏览器修复方案](../../dev_plan/workflow_v2/site_access_browser_repair_implementation_plan_20260922.md)、[普通Chrome实验](../../dev_plan/workflow_v2/barrons_official_chrome_control_experiment_20260922.md)、[Playwright启动正式Chrome实验](../../dev_plan/workflow_v2/barrons_playwright_official_chrome_control_experiment_20260922.md)、[CDP实验](../../dev_plan/workflow_v2/barrons_cdp_attach_control_experiment_20260922.md)。保留完整登录证据，别退回“能开页就成功”。
- [双轨Runtime/Identity方案](../../dev_plan/workflow_v2/site_access_dual_runtime_identity_implementation_plan_20260922.md)、[双轨验收](../../dev_plan/workflow_v2/site_access_dual_runtime_identity_remote_acceptance_20260922.md)。共享Identity部分已被后续隔离方案修正，其余两轨/接口原则继续有效。
- [采集/分发实施](../../dev_plan/workflow_v2/message_bus_acquisition_distribution_implementation_plan_20260923.md)、[行业来源上线](../../dev_plan/workflow_v2/industry_news_sources_rollout_20260924.md)。不要把当时模型供应商/节点/订阅数当当前状态。

**复发与部署历史**：

- [10/04根因方案](../../dev_plan/workflow_v2/message_source_recurring_failure_root_cause_and_repair_plan_20261004.md)、[10/09方案](../../dev_plan/workflow_v2/message_source_recurring_failure_root_cause_and_repair_plan_20261009.md)、[10/12方案](../../dev_plan/workflow_v2/message_source_recurring_failure_root_cause_and_repair_plan_20261012.md)、[10/13方案](../../dev_plan/workflow_v2/message_sources_and_login_maintenance_root_cause_and_repair_plan_20261013.md)。按机制回溯，不把每份当最新待执行清单。
- [9/28恢复](../message-source-recovery-20260928.md)、[10/01可靠性修复](../message-source-reliability-repair-20261001.md)、[10/04执行](../message-source-recurring-repair-20261004.md)、[Reuters/Investing 10/06](../reuters-investing-repair-20261006.md)、[10/09存储恢复](../message-source-storage-recovery-20261009.md)、[10/12执行](../message-source-recurring-recovery-20261012.md)、[10/15 main重部署修复](../message-source-main-redeployment-repair-20261015.md)、[分支归并回执](../site-access-branch-convergence-receipt-20261015.md)。用于判断前次补丁是否实际被继承。

10/17 已有一次历史材料汇总：`eval/message_source_recurrence_20261017/historical_document_index.md`、`document_manifest.json` 及 `history/`，当时收集 53 份可定位 Markdown，并保留路径/hash。这个“全部”指当时能够找到的持久化相关材料，**不包括未保存/已删除/不可访问的对话产物**，也不包括之后新增的所有文档。eval/ 可能未入 Git；原件优先使用上述 docs/dev_plan 路径。

本文历史经验来自可访问旧对话、仓库报告、现有代码及脱敏回执，不声称恢复了每次会话的完整工具轨迹。无法确定某个先前假设最终是否被证明时，保留证据缺口，而不是补一个漂亮结论。

## 13. 给接手 Codex 的工作建议与交接充分性复核

### 13.1 你应该继承的是判断能力，不是旧补丁的路径依赖

把这个系统理解成**长期运行的有状态采集与恢复系统**，而不只是几个网页 parser。真实浏览器、认证、页面、业务能力、队列、磁盘、配置和部署都携带状态；错误往往发生在所有权转换和恢复边界，而不是第一条导航。

先向用户准确复述“哪一层已经知道、哪一层还不知道”，再提出有区分力的实验。用户重复质疑“我没看见 Challenge”“为什么又失败”时，不要把它当需要解释常识；那往往是在提示你的模型漏掉了状态/页面/版本维度。用时间线和原始证据回应，不用更强的断言维护旧结论。

新的架构/更多重试并不天然更可靠。优先让已经存在的边界兑现：源隔离不串用、原页有主人、控制连接可退役、维护可完成、备用有真实证明、读取状态不过时、发布不丢补丁、资源增长有 owner。若它们已经满足，再考虑新能力；不要每次从头换一个爬虫框架。

保护稳定功能不等于不敢改。一个有明确机制和回归证据的跨层窄改动，比反复清状态/换 IP 更大胆，也更可维护。反之，未经证实的全局并发降低、无限等待、原页 reload、Cookie 复制和全量重启，通常是在放大未知。

最后：每次结束都告诉用户**实际恢复了什么、用了哪种证据、还有什么没证明、真正需要他人工做哪一件事**。不要只说“测试通过”，也不要把清单里每个未知都变成阻塞他的问题；能从代码和当前事实决定的细节自己决定。

### 13.2 本次交接充分性复核

交付前按新 Codex 的视角检查了以下事项：

- [x] 不依赖旧对话即可理解业务位置、采集模式、三类配置/身份所有权、正文和分发链。
- [x] 区分当前源码、带时间的生产证据、禁用能力、过时方案、假设与待核实状态。
- [x] 保留用户反复纠正的全天轮询、真实登录/正文验收、非保守但不扩权、隔离与有效主备等要求。
- [x] 解释复发机制，而不是把 401/Challenge 发生时间当根因；含共性基础设施、恢复和发布链案例。
- [x] 给出适配、单源/多源/反复失败、维护、前端失真和持续验收的操作路径及反例。
- [x] 标出最新 Reuters/Barron's/Investing 的成功窗口、未完成证明与主备/人工缺口，没有宣称长期稳定。
- [x] 明确自动维护 Worker 仍默认禁用、代码回流未闭环；截图识别不等于 solver。
- [x] 核对主要代码/测试/配置/文档路径及 CLI 解析器；只读与写入命令分开，避免泄露秘密。
- [x] 本轮只新增交接文档与文档变更留痕，不改业务、工程原则、生产或持久化数据。

**仍然存在的交接边界**：生产实时状态没有本轮刷新；部分 eval 原始证据不保证新克隆存在；没有重新执行业务回归/长时验收；历史未持久化内容无法穷尽。它们不是本文遗漏的成功，而是下一轮应根据具体任务补足的证据。
