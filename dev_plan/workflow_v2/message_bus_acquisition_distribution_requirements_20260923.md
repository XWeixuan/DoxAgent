# 消息总线采集分类、监测词与共享分发：开发需求方案

日期：2026-09-23。性质：需求理解与首轮沟通稿，尚非执行性开发方案。

本轮依据当前工作区代码、已有消息管线说明及官方网络资料形成；不修改业务实现、不操作生产状态、不执行付费模型请求。工作区基线为 `b3d50e8d`，另有未提交的 Silicon Analysts、消息源及 O4 等修改；下述现状以读取到的工作区为准，不等同于生产部署清单。

## 1. 目标与边界

在现有统一 Message Bus 内，明确消息源的三种采集与分发模式，引入 ticker 级监测配置和按入口共享的行业新闻采集链路。希望达到：维护一次 ticker 的多语言监测定义，即可供已关联的搜索型与分发型消息源使用；行业源只执行一份采集和正文补全，再独立判断应投递给哪些 ticker。

三种模式是消息源/采集入口属性，不是网站固定类型，也不是 HTTP、RSS、API、浏览器等访问方式的替代分类。同一个网站可以有 ticker 聚合入口、搜索入口和行业栏目入口。第一版建议一个注册入口指定一种模式，需要其他模式时另建入口并复用 adapter，而不把混合模式隐含在某个 ticker binding 内。

| 模式 | 上游采集范围 | 采集调度单位 | ticker 归属方式 | 监测配置用途 |
| --- | --- | --- | --- | --- |
| by ticker | 上游已按 ticker 聚合 | ticker × 入口 | 上游归属直接投递 | 不加本次相关性过滤 |
| by search | 指定搜索词召回的结果集合 | ticker × 入口，内部一个或多个 query | 结果归搜索发起 ticker | L1 宽召回词 |
| by distribution | 入口/栏目增量新闻全集 | 入口，所有订阅 ticker 共享 | 正则与 Jev 独立判断，命中任一轨则投递 | L2 规则与相关性 definition |

“无需筛选”指不增加 L2/Jev 相关性筛选；身份、去重、有效内容、已配置屏蔽规则和发布时间准入仍适用。“全量抓取”指所选入口覆盖的新文章，而非默认遍历整站历史。

Site Strategy 继续负责网站访问、Browser Identity、出口、正文适配与维护登录。采集模式、入口语言、监测词、共享调度、相关性与 ticker 分发归 Message Bus。API/RSS 不应为了使用本次能力而被迫注册浏览器策略。正文实际来源仍根据文章最终域名解析。

## 2. 当前实现及直接影响

| 当前证据 | 现状 | 对新需求的影响 |
| --- | --- | --- |
| `message_bus_v2/schema.py` 的 SourceDefinition / TickerSourceBinding | source 定义 adapter、参数与默认调度；binding 是 ticker+source；SourceKind 只有 API/CRAWLER | 新增采集模式应是独立维度，不能复用 SourceKind 表达 |
| `message_bus_v2/scheduler.py` 的 `_eligible_bindings` / `_poll` | 逐 ticker binding 调度，PollContext 要求 ticker 与 binding | distribution 必须支持入口级采集所有权，不能复制 N 个 ticker polling 来实现共享 |
| `message_bus_v2/service.py` 的 `enqueue_enrichment` | 先持久化补全任务；任务含 binding，intake key 含 binding_id、稳定输入指纹和重查窗口 | 当前共享 worker 不等于跨 ticker 共享文章任务 |
| `message_bus_v2/repository.py`、`deduplication.py` | Raw、逻辑消息、别名、观察记录及 stream 以 ticker 为边界去重/投递 | 需要增加分发前的共享文章与补全复用；不能因 MU 已接收而阻止其他 ticker 接收 |
| `content_enrichment/service.py` / `extractor.py` | 统一 Hub、原生正文识别、失败重试和 body_v2.2；完成后进入消息发布 | 复用现有补全能力，但补全输入/完成出口需能支持尚无目标 ticker 的文章 |
| `news_adapters.py` Reuters `_query` | 优先 binding company_short_name，其次公司知识库、Yahoo 名称查询、ticker 兜底 | 改为优先读取统一 L1；旧逻辑只作为兼容迁移来源 |
| 同文件 GoogleNewsSearchRssAdapter；`manifests.py` | 已接受显式 search_terms 并 OR 拼接；当前上限 10，语言区域固定英语/美国 | 并非所有 search 源都只有 MU→Micron 推导；需统一词管理、严格额度和语言/查询能力 |
| `admission.py` | 精确发布时间 REALTIME 超过 1800 秒会拒绝；DATE/UNKNOWN 有不同日期规则；另有 CLOSED_SWEEP | 低频共享采集与延迟分类不能直接套用而不讨论时效，否则会出现抓到但不能分发 |
| O4 runner、`tools/providers/monitoring.py` | 已有监测配置工作流和 binding 配置入口 | 预留同一提交能力给 O4，第一版由人工维护，不要求同时重写 O4 agent |

工作区已有 opt-in Silicon Analysts 情报源及通用 RSS、X 搜索、账号帖子等来源。因此三种模式本轮首先规范“新闻采集入口”，不应把无法证明由上游 ticker 聚合的账号帖子硬标成 by ticker。已有这类来源先兼容，待明确业务目标再迁移。

## 3. 监测词管理需求

每个 ticker 拥有一份持久化、可读取和版本化的监测配置，包括 L1、多语言 L2、相关/不相关 definition、启用状态与修改记录。提供简单的结构化文件提交程序；一次提交校验通过后整体生效，并输出版本、覆盖语言和受影响入口。未来 O4 调用同一能力，不另造一套词存储。

提交不会自动给 ticker 增加所有消息源订阅，也不会覆盖入口的登录、出口、调度、栏目等配置。正在执行的批次使用自身配置快照，后续任务使用新版本，避免同一批 query 或判定混用新旧词。配置变更默认不回放全部历史；保留显式有限窗口重判能力，已投递消息不撤回、不重复投递。

### L1：搜索型入口

L1 是最多三个宽召回概念，不是复杂正则或无限扩展查询。建议按三个跨语言对应的概念槽位维护：例如 Micron、memory、HBM 各有英语/韩语/繁中表达；一个具体入口只选择其语言对应的最多三个表达，不把所有语言拼成九个搜索词。此额度解释列入待确认问题。

查询使用方式由入口声明并适配：

- separate：每个词分别查询，合并结果并去重；某个 query 失败只记该 query 不完整，其他结果继续进入管线。
- OR：经验证支持 OR 的入口合并成一个查询；引号、括号及编码由 adapter 处理，不能假设所有网站支持同一种语法。

L1 不再分别写入每个 source binding。Google News 的地区/语言参数跟随入口配置，Reuters 多词默认采用 separate。普通搜索词不允许借分隔符偷偷扩展成无上限词表。MU 的英文公司名称使用 Micron，而非示例中的 mircon。

### L2：内部相关性规则

支持关键词、多组规则、组内 all/any、局部排除及正则表达式。默认各组 OR，任一组匹配即规则轨相关；允许标题、摘要和正文的明确字段范围，默认对清洗后的标题+摘要+正文判断，不匹配导航、相关推荐或验证码文字。

不设置类似 L1 的业务条数上限；仍需基本正则语法校验及执行时间/输入规模保护，单条规则异常不得拖住全局分发。保存命中规则及文本片段，便于人工调优。中文、韩文和英文采用适合各自文字的匹配方式，不将英文单词边界机械用于全部语言。

例如 Samsung 单独命中会召回手机、电视等大量无关新闻；MU 可使用 Samsung 与 DRAM/HBM/NAND 的组合条件。这是规则设计问题，Jev 的“不相关”结果不能否决已命中的正则，因为本需求明确采用 OR。

## 4. 语言归属

语言以实际 feed、栏目、API 查询入口或搜索入口为准，站点提供可选默认值；优先级为入口显式语言 > 站点默认语言。英语、韩语、繁中采用明确语言标识（如 en、ko、zh-Hant）。浏览器 locale 与内容语言分开：共享 en-US Browser Identity 可以访问繁中 feed，不因词表变更而重建浏览器。

需求按“现有启用的 search/distribution 入口语言集合，加英语”要求提交完整监测词版本。界面/提交预览先列出所需语言；未启用的试验入口不扩大强制范围。新增语言时列出受影响 ticker，已支持语言继续运行，缺词语言不静默伪装成已覆盖。

distribution 默认同时使用入口语言 L2 和英语 L2，任一命中即可。文章实际语言与入口不同应记录；有现成对应语言规则时可补充匹配，不把语言识别或自动翻译变成每篇文章的前置门槛。第一版不强制翻译全文；跨语言同一报道也不凭语义相似就强行合并身份。

definition 每个 ticker 维护一份明确表述即可，不要求机械翻译成所有语言；多语言词表仍由人工提交。Jev 的韩文/繁中实际准确率要通过样本验证，不能从支持文本输入推断与英文效果相同。

## 5. distribution 共享采集与分发

建议采用以下业务链路：

```text
启用的采集入口 → 共享增量采集/检查点 → 来源观察与文章身份去重
             → 共享正文补全/原生正文复用 → 固定文章内容版本
             → 对候选 ticker 分别启动 L2 与 Jev
             → 任一轨相关 → ticker 级准入与幂等发布 → 现有 Stream/消费者
```

新增共享文章状态，是为了在尚未决定 ticker 时保存和补全新闻，不使用虚构 ticker 承载采集结果。入口级保留游标、调度、错误与最新成功时间；ticker 级保留订阅、相关性决策和投递记录。某个 ticker 暂停或配置异常，不应使其他 ticker 的共享入口停采。

默认候选集合建议为显式订阅该入口且具备有效监测配置的运行中 ticker；支持一次为当前 ticker 集合批量订阅。不能先用 L2 命中筛掉 Jev 候选，否则模型轨无法召回规则漏掉的文章。

同一已确认文章/内容版本由多个订阅 ticker 共享一次补全。具备确定 URL/提供方文章 ID 证据时，允许复用其他入口取得的正文；无法确认同一文章时保留独立记录。复用需尊重内容版本和访问权限范围，不将不同权限下的截断正文等同全文。

投递时复用现有 ticker 内去重：若 Yahoo 已将同篇新闻交给 MU，distribution 应追加来源/判定证据，而非再发布一次；同时仍可投递给尚未收到它的其他 ticker。文章实质修订、正文恢复和规则版本变化分别记录，不能因为定时重抓或模型重试制造新消息。

共享文章与每个 ticker 的决策均需持久化。重启可续跑，某 ticker 投递失败只重试该目标。保留所有来源 first-seen、真实 published_at、补全完成和分发时间，不能通过重写发布时间规避时效准入。

## 6. Jev 判定需求与已验证的接口事实

TypeSafe 官方文档明确支持一个 state 与多个独立 questions，各问题对同一 state 独立评估，符合“一文章、多 ticker 独立判定”的设计。[TypeSafe Introduction](https://docs.typesafe.ai/introduction)

OpenRouter 官方说明给出 System One 专用路径 `https://openrouter.ai/api/v1/systemone`；建议锁定 `typesafe/jev-1.13` 而非 latest。该调用不按普通聊天补全文本处理。模型页当前标注 32K context；请求分批和长文处理需遵循实际限额。本轮仅核实文档，尚未验证账号权限、实测延迟与业务准确率。[OpenRouter Jev 接入说明](https://openrouter.ai/blog/insights/what-is-jev/)、[模型页](https://openrouter.ai/typesafe/jev-1.13)

建议每个 ticker 对应一个 Noul 问题，使用“本文是否符合该 ticker 的相关定义”这一正向问题；definition 包含相关范围、不相关范围和少量边界说明。Noul 返回 0–1 值，系统按可配置阈值判定；初始建议 0.5，经样本校准后调整。一个 HTTP 请求可以批量承载多个 ticker，但结果、重试、规则版本和投递归属始终按文章×ticker 独立保存。

state 包含清洗后的文章标题、摘要、正文、语言和必要来源上下文；程序拼装，不由生成模型改写成新的事实。超长文章显式分段/分批并记录覆盖，不能静默截尾后把模型未读到的内容判为不相关。批量上限属于后续接口验证细节，不在本稿臆定。

正则和 Jev 两轨都运行并保留结果：正则命中即可先投递，Jev 继续提供对照记录；Jev 命中同样立即投递；两轨都完成且不相关才是明确未命中。调用失败、缺失某 ticker 答案、正文不足等应是 UNKNOWN/PENDING，而非 NOT_RELEVANT。模型重试和限流不能阻塞共享采集、正文 worker 或已命中的目标。

TypeSafe 明确提示 Jev 可能过于字面化、受无关长文和对抗内容影响；因此 definition 应具体，文章作为数据，模型没有执行权。时效、订阅范围、ticker 身份和投递幂等由程序控制，不能交给模型判定。Noul 与 Choice 的置信数据不混用。[Jev 1.13 已知限制](https://docs.typesafe.ai/model-jaggedness/jev-1.13)

## 7. 默认建议与首批验证范围

默认采用：轻量持久化配置+提交程序；入口级共享采集；每 ticker 独立逻辑判定；双轨 OR；模型版本固定；配置版本审计；无全量历史回灌；无额外管理后台；复用现有 Site Access 与 Content Enrichment 能力。只为 distribution 新增必要共享状态，不以本轮为由重构全部既有 ticker 数据和消费者。

建议首批验证 by ticker 的 Benzinga/Yahoo 回归、by search 的 Reuters/Google News 迁移，以及 by distribution 的 TrendForce、Digitimes。TrendForce News 与 Press Center 是可区分的官方入口，不默认视为同一内容集合；Digitimes 有繁中站及英文站，实际入口和订阅权限需要确定。[TrendForce News](https://www.trendforce.com/news/)、[Press Center](https://www.trendforce.com/presscenter)、[Digitimes 繁中](https://www.digitimes.com.tw/)

本次网页检索访问 Digitimes 英文入口返回限制页；这只反映检索工具的访问环境，不能据此断言生产 Browser Identity 不可访问，更不能宣称已经完成站点适配。[本次英文入口返回页](https://www.digitimes.com/403.asp)

后续验收应证明：新增 N 个订阅 ticker 不带来 N 倍入口抓取/正文请求；一次词提交作用于全部相关入口；L1 不超过约定额度；多 query 可部分成功；多语言 L2 生效；正则独中、模型独中、双中、双否、超时和部分答案分别正确处置；共享文章只对每 ticker 发布一次；配置更新与进程重启可续跑。还需用英语/繁中/韩文人工标注样本比较两轨及 OR 后的召回与误报，分别展示抓取覆盖、正文成功、分类耗时、模型调用成本和投递延迟。

## 8. 一次性待确认的问题

1. **首批实际接入范围**：是否按 TrendForce 英文 News、Digitimes 繁中“半导体．零组件”栏目落地 distribution，并同步迁移 Reuters/Google News？建议先这两个 distribution 入口；韩语词表结构本轮支持，具体韩语入口待你指定。若希望包含 TrendForce Press Center、Digitimes 英文版或其他栏目，请一并明确。
2. **L1 三词额度**：建议最多三个跨语言对应的概念，每个语言各填最多三个表达，具体入口只执行其语言词表；是否符合你所说的严格最多三个？备选是各语言独立最多三个且概念不要求对齐。
3. **distribution 的目标范围**：建议仅评估显式订阅该入口的运行中 ticker，支持批量订阅；还是自动评估全部运行中且有监测配置的 ticker，不维护 source-ticker 订阅？
4. **正文补全失败**：建议完成现有有界重试后，用有效标题/摘要做降级双轨判定，命中仍投递并标记正文不足，恢复正文后补充判断；还是坚持只有完整正文才能分发？challenge/登录提示页面不作为有效内容。
5. **Jev 不可用时**：建议正则命中照常投递，正则未命中的文章保留 pending 并有界重试，最终失败可查询但不自动全投；是否接受这一降级取舍？
6. **行业源的晚发现消息**：建议共享入口全天按源频率增量采集，distribution 可单独接受最近 24 小时首次发现的新闻，并向下游保留 late 标记；这会涉及现有 30 分钟准入及消费者衔接。你希望采用这一范围，还是严格保持现有实时/闭市 sweep 窗口？监测词变更默认只影响新任务，不自动补投历史。

回答以上问题后，下一轮再明确数据契约、具体改动位置、迁移步骤和可执行验收清单。
