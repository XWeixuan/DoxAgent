# 消息总线采集模式、监测配置与共享分发实施方案

日期：2026-09-23。状态：基于用户已冻结决策的可执行方案，尚未实施业务代码。

前置需求：[需求沟通稿](message_bus_acquisition_distribution_requirements_20260923.md)。本文取代其中待确认项，以本文为开发依据。代码勘察基于本地 `b3d50e8d` 及当前未提交工作区；实施时保留现有 Silicon Analysts、O4、模型配置等并行修改。

## 1. 冻结决策与交付边界

1. 首批新增且仅新增 `ctee_semiconductor`：工商时报繁中半导体栏目，入口 `https://www.ctee.com.tw/industry/semi`，爬虫，by distribution。Reuters Site Search、Google News Search RSS 迁移为 by search。Benzinga、Yahoo 等保持 by ticker。重点交付通用模块，不批量接入新媒体。
2. 每 ticker 最多三个 L1 概念，每个概念有各语言表达；不是每语言独立扩展三个不同概念。
3. distribution 只评估订阅该入口的 ticker。
4. 正文补全完成既有重试后，可以用有效标题/摘要降级判定及分发。
5. 正则与 Jev 并行，任一命中即可投递。Jev 是实验性增强，首次失败后仅一次重试，仍失败则该轨按“不相关”终结。保留失败原因，不无限等待、不增加兜底大模型。
6. 严格维持现有 REALTIME/CLOSED_SWEEP 窗口及日期精度规则，不新增 24 小时宽限，不改变下游 Runtime 的时效口径。
7. Jev provider 固定 OpenRouter。用户提供的 key 已写入 Git 忽略的本地 `.env` 的 `OPENROUTER_API_KEY`；文档、示例、changelog 和提交中不得包含该值。本轮没有同步远端或调用付费接口。

本次交付包括 schema/持久化迁移、监测配置提交程序、search 执行计划、distribution 共享采集/补全/分类/投递、工商时报适配、测试及操作文档。O4 本轮只获得稳定可调用的提交接口和样例，不修改其自主产词流程。不新增管理后台、消息 broker、向量检索、翻译模型或另一个网站策略治理系统。

## 2. 当前代码约束与架构选择

当前 `TickerSourceBinding` 的身份是 ticker+source，`PollContext` 强制包含 ticker/binding；`scheduler.py` 与生产 `persistent_runtime_v2/bus_orchestration.py` 都存在逐 binding 调度入口。仅修改 scheduler 的独立 worker 路径不足以覆盖生产运行。

`ContentEnrichmentHub` 已共享进程/并发，但 `EnrichmentJob` 和 intake key 仍含 binding。`repository.py` 的逻辑文章、别名和投递去重以 ticker 为边界。这一边界应保留：MU 已收到不能阻止 NVDA 收到。

采用“共享内容处理 + ticker 独立投递”扩展：为 distribution 增加入口任务、共享文章版本和分发记录；复用已有数据库、ContentEnrichmentHub、Site Access、ticker 标准化和 stream。by ticker/search 继续走现行链路，不为本次目标重写其全局去重。

```text
by ticker ────────────────────────→ 现有补全 → ticker 内去重/准入/发布
by search → L1 QueryPlan ─────────→ 现有补全 → ticker 内去重/准入/发布
by distribution → 入口共享采集 → 共享文章/补全 → 每 ticker 的 L2 与 Jev
                                                ↓ 任一相关
                                      现有 ticker 内去重/准入/发布
```

同一 source definition 表示一个可配置采集入口；不同栏目/语言需要独立 source_id，可复用 adapter。第一版不再创建与 source 一一对应的 Feed Registry。实际共享任务数取决于入口和窗口，不取决于订阅 ticker 数。

## 3. 配置契约

### 3.1 SourceDefinition 的新增字段

在 `message_bus_v2/schema.py` 增加以下类型，SourceKind 保持原义：

| 字段 | 语义与默认 |
| --- | --- |
| acquisition_mode | `by_ticker / by_search / by_distribution`；旧 JSON 缺失时兼容原执行路径 |
| entry_url | 可选，实际栏目/feed/搜索入口；工商时报固定栏目 URL |
| content_language | 可选 BCP47 内容语言；首批 Reuters/Google 为 en，工商时报为 zh-Hant |
| site_id | 可选默认元数据引用，不意味着所有 API/RSS 都必须注册 Site Strategy |
| search_policy | search 专用：`separate / or`、query 语法 renderer ref、分页上限参数 |
| distribution_policy | distribution 专用：Jev 是否启用、分类配置默认值；不放 ticker 关键词 |

source 的 default_parameters/default_polling_config 作为 distribution 入口级有效参数与调度配置；binding 只表示订阅和 ticker 的 streaming 设置。distribution binding 禁止对入口 URL、栏目、语言、抓取参数单独覆盖，已有 binding.polling.enabled 只决定该订阅是否参与；间隔不能导致重复入口任务。CLI 查询同时显示“入口采集间隔”和“订阅启用状态”。

非此次范围的账号帖子等来源暂保留兼容执行，不将其强制声明为上游 ticker 聚合。后续需要迁移时单独选择模式。

### 3.2 语言默认值

Site Strategy 的站点定义增加可选 `default_content_language`（空值兼容）；Message Bus 配置入口时可继承并持久化解析后的语言。优先级：source 显式语言 > 已登记站点默认。没有 Site Strategy 的 API/RSS 直接配置语言。

这不是 BrowserIdentity.environment.locale；语言改变不修改浏览器、Cookie 或出口。站点默认值更新通过显式配置解析/刷新生效，不在每篇新闻请求中引入额外 Site Access 管理查询。

提交时要求覆盖全部已启用 by search/distribution 入口的有效语言，至少包含 en；首批是 en、zh-Hant。未知语言入口在启用前必须补齐。增加一种语言不使现有语言失效：缺词的 ticker 在该新语言入口显示 CONFIG_INCOMPLETE，其他入口继续工作。

### 3.3 TickerMonitoringTerms

新增 `monitoring_terms.py` 管理模型、校验与提交服务；配置历史存入 Bus SQLite。

```yaml
ticker: MU
expected_revision: 0
l1_concepts:
  - concept_id: company
    expressions: {en: Micron, zh-Hant: 美光}
  - concept_id: memory
    expressions: {en: memory, zh-Hant: 記憶體}
  - concept_id: hbm
    expressions: {en: HBM, zh-Hant: HBM}
l2:
  en:
    groups:
      - id: direct
        any: [{literal: Micron}, {literal: DRAM}, {literal: HBM}, {literal: NAND}]
      - id: competitor_memory
        all:
          - {literal: Samsung}
          - {regex: '(?i)\b(DRAM|HBM|NAND|memory)\b'}
  zh-Hant:
    groups:
      - id: memory_supply
        any: [{literal: 美光}, {literal: 記憶體}, {literal: 三星}]
definition:
  relevant: 美光及記憶體供需、價格、技術、資本支出、競爭者產能和影響該產業的政策。
  irrelevant: 與記憶體業務無關的手機評測、家電行銷和純股價榜單。
```

示例说明结构，不作为未经人工确认的生产 MU 词表；例如繁中单独“三星”偏宽，正式提交可改成组合组。

校验：1–3 个唯一 concept_id，每个所需语言一个非空表达；不接受表达内自带 OR 管道或任意高级查询来规避限额。短语可含空格，由 renderer 转义。L2 支持 group 的 any/all/none，组间 OR，语言间 OR；none 只否决所在规则组，不否决其他组或 Jev。至少存在正向条件，不允许仅 none 构成全匹配。匹配字段默认 title+summary+body，可选 title/summary/body。

L2 不设三词式业务配额，但限制配置文件总大小并采用支持超时的正则执行（新增并锁定 `regex` 为直接依赖），每规则默认 20ms；规则失败只影响该规则并记录诊断。文本 NFC 规范化、空白归一；literal 明确区分大小写与英文 whole-word，中文/韩文不强制 `\b`。全文只使用正文识别后的可用文本。

revision 用乐观并发控制，提交一份配置原子生效，历史不可变；future O4 与人工使用同一服务。提交后返回有效 revision、语言覆盖、受影响来源与 query 预览。旧版本不会被同名文件覆写抹去。

## 4. 提交与运维接口

扩展 `message_bus_v2/cli.py` 为子命令，保留既有 run-worker/run-once/status：

```bash
python -m doxagent.message_bus_v2.cli terms validate --file monitoring/MU.yaml
python -m doxagent.message_bus_v2.cli terms apply --file monitoring/MU.yaml --actor user
python -m doxagent.message_bus_v2.cli terms show --ticker MU
python -m doxagent.message_bus_v2.cli terms history --ticker MU
python -m doxagent.message_bus_v2.cli terms preview --ticker MU
python -m doxagent.message_bus_v2.cli distribution status --source ctee_semiconductor
python -m doxagent.message_bus_v2.cli distribution decisions --ticker MU --limit 50
```

`terms apply` 只提交监测定义，不偷偷创建 source binding。订阅继续使用现有 configure_binding；提供批量 source/ticker 配置的薄封装即可。未来 O4 tool 调用 `MonitoringTermsService.apply`，不得让 agent 直接写数据库或逐源复制词表。

新 revision 在下一采集批次生效，批次内冻结。旧 revision 正在处理的任务继续以旧版本完成；取消订阅/停用 ticker 则在投递时再检查，不能因旧快照向已取消目标投递。默认不回放历史；本轮提供有限窗口 dry-run 规则测试，不提供自动历史补投/撤回。

## 5. by search 实现

新增 `search_plan.py`：根据 ticker terms revision、入口语言与 search_policy 输出 QueryPlan，包含 concept_id、实际 query、query_key、terms_revision。Reuters 采用 separate，Google News 采用 or。一个计划最多三个 query；OR 计划只有一个。L2/Jev 不应用于这两类入口的召回结果。

修改 `news_adapters.py`：Reuters `_query` 改为可执行多个子查询；Google 从 QueryPlan 读取词与语言/地区参数，保留 domain 限定与窗口条件。`max_pages` 明确定义为每子 query 上限，执行计划显示最坏请求量；所有请求仍受 source limiter 与 Site Access Identity limiter 约束。

每个 query 有独立分页游标、覆盖状态及错误；operation_id 包含 query_key，不能让多个词复用同一请求身份。成功 query 的结果按提供方 ID/已确认 URL 合并，失败 query 不抹去已成功结果。DEFERRED 保存游标并下次续跑，不推进失败分页。

新 terms revision 改变 query_key，旧游标不能套用新词。CLOSED_SWEEP 冻结自己的 QueryPlan，直到该 sweep 完成。统一计划只改变召回输入，不声称 Reuters/Google 已覆盖全部历史：窗口太宽、搜索限额、截断或失败均如实为 PARTIAL/UNKNOWN。

迁移优先级：存在统一 terms 时必须使用 terms；没有时暂使用旧 binding 显式参数/现有 Reuters 公司名逻辑，并输出 LEGACY_TERMS 标记。提供迁移预览收集既有词；超过三个概念或缺语言时不截断、不自动猜翻译，也不阻断旧服务，列出待人工提交项。新建 search binding 要求完整 terms。完成现有 ticker 提交后再关闭各自兼容路径。

## 6. distribution 持久化与任务所有权

继续使用当前 Bus SQLite。新增表由 `distribution_repository.py` 在相同连接/事务管理器上维护，避免扩大已有 repository.py；字段 JSON 保存快照，关键状态/唯一键/调度时间单独列。

| 表 | 最小字段与唯一性 |
| --- | --- |
| ticker_monitoring_terms | ticker 主键、current_revision、updated_at |
| ticker_monitoring_term_revisions | (ticker,revision) 主键、config_json、hash、actor、created_at |
| distribution_runs | run_id、source_id/version、mode、window_start/cutoff、checkpoint、roster_json、terms revisions、coverage、status、lease；唯一 work_key |
| distribution_articles | article_id、article_key、input_version、original_message、content_version、enriched_message、body_state、first_seen/last_seen；唯一 (article_key,input_version) |
| distribution_observations | (run_id,article_id) 唯一、provider identity、observed_at；保留跨窗口/入口的发现证据 |
| distribution_decisions | decision_id、article/content_version、ticker、terms_revision、regex_result、jev_result/attempts/retry_at/error、final_result、claim token；唯一文章内容版本×ticker×terms revision×classifier版本 |
| distribution_deliveries | delivery_id、run_id、article_id、ticker/binding snapshot、AdmissionContext、decision_id、状态/重试/lease/结果；每 run×article×ticker 唯一 |

决策可被不同窗口引用；delivery 持有各自准入上下文，不能把上一次实时“不允许发布”误认为本次 sweep 也不允许。最后一层仍由现有 ticker 逻辑消息去重保证跨 run/跨来源不重复发布。

article_key 首选提供方稳定 ID+source；有明确 article URL 能力时使用规范 URL，保留会选择文章的查询参数；不能仅标题相同就合并。第一版保证 distribution 内同文章同版本复用，且跨类别投递去重；不承诺把全部 by ticker/search 历史正文改造成跨 ticker 缓存。正文版本由清洗文本/质量信息决定，last_seen 或采集时间变化不生成新版本。提供方无更新证据时正常轮询不反复补全。

claim 使用现有 lease+claim_token 模式，写结果前验证所有权。原始文章落盘、观察关系和补全入队事务完成后才推进 checkpoint；不得先保存 cursor 再异步扔文章。进程崩溃重放依唯一键恢复。

## 7. 共享调度与严格窗口衔接

### 7.1 实时

`scheduler.py` 新增入口级 dispatch 方法，`BusOrchestration.run_once` 先按现有日历和暂停策略筛选 eligible bindings，再按 distribution source 分组。普通两类继续逐 binding `_poll`；distribution 每源每次 due slot 只有一个 run。所有运行路径共用该分组与 claim，不能独立 worker 和生产编排器各抓一遍。

work_key 使用 source_id + source version + REALTIME + 持久化 due slot。采集 roster 冻结本次 eligible 的订阅 ticker 及 terms revision。没有有效订阅时不采集。工商时报默认入口间隔 300 秒、并发 1，仍遵循现有实时日历；不创建全天独立调度以绕过闭市逻辑。

精确发布时间超过现有 1800 秒仍不投递；DATE/UNKNOWN_FIRST_SEEN 继续调用现行 evaluate_admission，不能以入口时区替换现有业务日期规则。采集入口解析日期时使用 Asia/Taipei 转 UTC，并保留精度，不能编造秒级时间。

### 7.2 闭市 sweep

保留现有每 ticker 的 SOURCE_SWEEP task、sweep_id/source_task_id；这些是投递和下游消费归属。将其采集部分映射到共享 run：work_key 为 source_id + source version + CLOSED_SWEEP + 精确 window_start/cutoff。相同窗口共享一次抓取，不同窗口不错误合并。

`bus_orchestration.py::_source` 对 distribution 分支获取/续跑共享 run，订阅者即使晚到，也通过已持久化观察创建自己的 delivery，复用文章/补全结果。分页必须到窗口下界或确认源耗尽才 COMPLETE；达到页数/时间预算为 PARTIAL，不能伪称覆盖完整。

目前 SOURCE_SWEEP 只检查 `pending_enrichment_ids`。新增显式 `pending_distribution_delivery_ids` 与 receipt 字段，不把新任务 ID 塞进旧补全字段冒充完成。该 ticker 的所有相关 delivery 终结后才 flush_binding、冻结 stream_highwater 并完成 SOURCE_SWEEP。

完成状态包括 PUBLISHED、DEDUPLICATED、NOT_RELEVANT、ADMISSION_REJECTED、CANCELLED、FAILED。持久投递失败记 gap/PARTIAL 并终结，不能让 sweep 永久等下去。Jev 失败耗尽的 NOT_RELEVANT 是已冻结的实验性降级策略：单独统计 degradation，不把它算成采集窗口缺页，也不因它阻断 sweep。

共享文章没有全局 ticker 专属 AdmissionContext；只在每条 delivery 中使用真实目标的 context。实时与 sweep 均在分发前及 `accept_message` 时再次检查准入；分类耗时跨过实时窗口会被拒绝，这是保留既有语义的明确取舍。不会刷新 published_at 或伪造新的 first_seen。

## 8. 复用正文补全与可靠发布

在 `content_enrichment/schema.py` 为 EnrichmentJob 增加 owner_kind，旧 JSON 默认 ticker_binding；distribution_article 必须包含 article_id、source 和原始 message，binding 为空。通过校验强制两种所有权互斥，禁止虚拟 ticker/binding。

抽出 Hub 的正文处理公共函数，保持原生正文复用、body_v2.2、重试/180 秒预算、质量识别和 Site Access 回报。共享任务不执行 ticker 准入：入队前确认至少有一个有效窗口目标，投递时逐目标准入。`MediaEnrichmentRecord.ticker` 改为允许 None，仅用于共享抽取；所有真正发布消息仍要求真实 ticker。检索并修改日志/outcome 中 job.binding.ticker 的直接访问。

Hub `_finalize` 根据 owner_kind 路由：旧任务继续现行 accept_message；共享任务原子保存正文结果、创建/唤醒 decisions 与 deliveries、写 body outcome outbox 并结束补全任务。共享 body outcome 只回报一次，携带 article/source/identity provenance，不按订阅 ticker 数翻倍。

正文最终失败时使用提供方的有效 title/summary，不把验证码、登录页或拦截文案当文章。没有有效标题/摘要则标记 EMPTY_CONTENT，终结该次分类且不投递。降级数据含 body_quality 与 fallback_reason。首版不自动重抓已终结文章等待登录恢复；实际提供方修订或显式运维重试才产生新的内容处理机会。

分发调用内部 `accept_message` 的已补全路径，附带真实原始 input 与共享补全 provenance，不重新 enqueue。这个信任标志只由进程内部持久记录构造，外部 provider metadata 不能设置。

delivery 的发布/记录尽可能与 Bus 写入同一 SQLite 事务；若现有调用边界无法一次事务，则采用可重放顺序：先以既有 ticker 逻辑身份幂等发布，再确认 delivery，崩溃重试不得重复 stream。保留原始 source、文章域名、content hash、命中轨道和 terms revision。

## 9. 正则与 Jev 分类执行

新增 `distribution.py` 管理 durable decisions/deliveries；复用现有 Bus worker 生命周期，配置小型独立异步并发池（默认 2），不阻塞 poll 或 ContentEnrichmentHub。生产编排和独立 CLI 都启动同一组件，数据库 claim 防止重复。

正则轨使用入口语言+en 的 L2 组并集，先独立完成；同时排入 Jev 请求。正则命中立即允许投递，Jev 仍完成对照结果，绝不二次发布；该 ticker sweep 可在投递完成后结算，无需等待已不影响结果的模型对照。

Jev 请求使用 `httpx.AsyncClient` 直接调用 OpenRouter System One endpoint，不引入自动重试 SDK，也不混用现有生成式模型路由。参数：`model=typesafe/jev-1.13`、文章作为 state、questions 的 key 对应稳定 ticker ID，类型 Noul。definition 的相关/不相关范围组装成一个正向问题；程序处理时效与计数。按 keyed answer 解码，不能依返回顺序配 ticker。

默认每批最多 16 个 ticker、超时 15 秒、失败后 5 秒重试一次；计数按文章×ticker 判定持久化，而非整批共享计数。部分答案成功立即保存，仅缺失/无效的问题重试；HTTP 429 的 Retry-After 在现有任务时限内尊重，超出则按实验失败收敛。HTTP SDK 禁用隐式 retries，避免实际次数超过 2。鉴权/配置等确定性错误可直接终结且告警，不浪费第二次请求。

阈值默认 noul >= 0.5，模型版本、阈值和 prompt template 版本记录入 decision；阈值不另叠加 Choice confidence。正则不相关且 Jev 两次失败：final_result=NOT_RELEVANT，reason=JEV_RETRY_EXHAUSTED；这代表业务不分发的降级选择，不记为模型明确否定。Jev 被禁用/缺 key 时正则正常工作，reason=JEV_DISABLED/CONFIG_MISSING，错误日志绝不输出请求鉴权头。

文章输入先清洗，仅包含 title/summary/body/language/source；不加入 HTML、导航和无关历史。常规文章一份 state。超限文章按段落拆成有界子 state，覆盖完整文本；一个 ticker 在任一分段命中则相关。每个 article×ticker 的首次尝试包含所需分段，第二次只补失败段，不以分段创建无限重试机会。预检遵守 provider 实际输入限额；超出合理总预算时标记 JEV_INPUT_LIMIT，实验轨按不相关结束，正则仍扫完整正文。不静默截断并宣称读完全文。

预算默认每文章模型处理总期限 60 秒，且不能超出实际窗口任务剩余时间；期限到达已知正则命中照常，其他目标按实验降级收敛。批量对照和样本校准是运维能力，不允许 Jev 延迟主链无限排队。

## 10. 工商时报首批适配

新增 `message_bus_v2/ctee.py` 和 `builtin:ctee_semiconductor` adapter，提供入口级 `poll_shared(SharedPollContext)`，不强行复用要求 ticker 的 PollContext。SharedPollContext 包含 source、窗口、checkpoint、run_id、request_permit，不含 ticker。返回复用 PollResult 的新闻列表、failure、coverage 等字段。

使用现有 SiteAccessClient 获取栏目与文章页面，统一 HTTP/Browser 访问策略；Site Strategy 新增正式 ID `ctee`，publisher 精确域名为 `ctee.com.tw`、`www.ctee.com.tw`，支持域按现场请求证据登记。默认通用 Managed Runtime、低并发、固定出口组合；若直连可用先直连，不因订阅 ticker 数创建浏览器，不无证据默认 External。

栏目解析器输出真实文章 URL/ID、标题、摘要、published_at/时间精度、publisher 和源证据。第一步取得栏目 HTML/正常页面网络响应样本，确认真实分页与日期格式，再固化 fixture 和 parser；本次网页检索被 robots 限制，尚未证实其 DOM、分页端点或正文 selector。不得写入猜测 API、假 selector 或为了通过测试返回固定列表。

优先解析页面可见数据/内嵌结构化数据；只有正常页面明确使用的列表请求才适配为站点内部实现，仍经访问治理。正文优先通用提取，fixture 证明失败时才添加 `builtin:ctee@1` 定点规则。首次启用不全站历史回灌；bootstrap、窗口分页和首次发现使用原有业务语义。

只默认登记 source，不给全部 ticker 自动订阅。部署验收选择已有正式监测词与订阅的测试 ticker；没有配置时提供提交样例和预览，不擅自为用户确定生产投资相关定义。

## 11. 代码改动清单与实施顺序

| 阶段 | 文件/模块 | 必须交付 |
| --- | --- | --- |
| A | message_bus_v2/schema.py、monitoring_terms.py（新）、search_plan.py（新）、repository.py | 模式/语言/terms 契约、版本存储、提交预览；旧数据可读 |
| B | cli.py、news_adapters.py、manifests.py | 新子命令、Reuters separate/Google OR、子 query checkpoint 与部分成功 |
| C | distribution_repository.py、distribution.py（新）、scheduler.py、factory.py | 共享 run/article/decision/delivery、lease、入口级调度、worker 生命周期 |
| D | content_enrichment/schema.py、service.py、monitoring/media_enrichment.py | 两种任务 owner、共享补全与原子完成、outcome 不重复 |
| E | persistent_runtime_v2/bus_orchestration.py | 实时分组、共享 sweep run、每 ticker receipt 与终结等待 |
| F | relevance.py、jev.py（新）、settings.py、pyproject.toml/uv.lock | L2 匹配、OpenRouter 客户端、批量逐 ticker 结果、一次重试和降级 |
| G | ctee.py（新）、adapters.py、site_strategy/schema.py/seeds.py、按需正文策略 | 工商时报实际列表/正文 fixture、语言默认、站点访问与模式注册 |
| H | 测试、操作文档、.env.example、生产 Compose、changelog | 回归、迁移命令、密钥传入 worker、远端验收与回滚记录 |

实现顺序 A→B→C→D→E→F→G→H；C/D/E 完成后才允许 distribution 正式启用，不能出现列表已入库但 sweep 不等待投递的半成品生产路径。新文件使用上述职责划分，不再层层引入 framework/provider/plugin 抽象。

## 12. 迁移、部署与回滚

1. 备份当前 Bus SQLite（在线一致性备份）及 source/binding 配置；保留正在执行的补全和 sweep，不直接覆盖数据库。
2. 增量建表/字段，EnrichmentJob 旧 JSON 默认旧 owner。迁移脚本提供 dry-run/apply，重复执行幂等；不自动改写 ticker stream offset。
3. 标注已确认的 by ticker 来源、Reuters/Google by search；现有 search 参数作为 LEGACY_TERMS 可继续工作。工商时报 source 初始关闭，完成站点可用性与订阅配置后启用。
4. 提供待迁移词表清单；人工一次提交 ticker 的 en/zh-Hant 定义后，其两个 search 源和订阅的 distribution 源自动引用同 revision。不得自动加入备选 L1 概念填满三项。
5. `.env.example` 只增加空 OPENROUTER_API_KEY 和模型/开关/超时配置名；DoxAgentSettings 用 SecretStr 读取该 key。Compose 只传给实际运行 classifier 的 worker，日志与 status 遮蔽。本地已有 key 不代表远端已配置，部署时使用受限环境/secret 注入并验证存在性。
6. 按现有生产部署流程提交/推送/拉取/重建受影响 worker，先验证老业务，再为明确指定 ticker 开启工商时报订阅；验收写独立记录。
7. 回滚优先关闭 Jev，或关闭新 distribution source；其他来源不受影响。完整代码回滚前 drain 新 owner 补全与分发任务，不能让旧 Hub 读取新任务。数据库新增表保留，不删除文章、认证状态或已发布消息。

无需调整当前稳定 Browser Runtime 生命周期，也不为本轮重复修补无关镜像标签。

## 13. 必测用例与完成标准

| 范围 | 必测结果 |
| --- | --- |
| 配置 | 4 个 L1 概念拒绝；缺 zh-Hant 拒绝新提交；三个概念多语言有效；revision 冲突不覆盖；一次提交影响三种已关联入口 |
| search | Reuters 三 query/Google 一 OR；每 query 分页独立；一 query 失败仍交付其他消息；多词重复文章只进入一份有效 intake；旧配置兼容 |
| 共享采集 | 同入口 1/20 个订阅 ticker 网络抓取次数相同；同文章内容版本只补全一次；没有订阅不采集；暂停一个 ticker 不停其他目标 |
| 正文 | FULL 原文复用；失败一次既有重试后降级；challenge 页面不能分类；空内容终结；崩溃不丢完成结果 |
| 双轨 | 规则独中、Jev 独中、双中只投递一次、双否不投递；模型缺答案只补该 ticker；失败最多两次；耗尽按不相关且有失败原因 |
| 多语言 | en+zh-Hant 并集生效；韩语 fixture 验证结构可扩展；规则组局部 none 不否决另一轨；坏正则/超时隔离 |
| 幂等 | 两个 worker 抢同任务仅一个有效 writer；发布后崩溃重试不重复 stream；同一文章 Yahoo 已投 MU，工商时报观察不重复，但仍可投其他 ticker |
| 窗口 | 实时超 1800 秒拒绝；DATE/UNKNOWN 保持现行规则；闭市严格原窗口；不同 ticker context 不混用；sweep 等待本 ticker 分类/投递终结 |
| 覆盖 | 到达分页上限标 PARTIAL；同窗口共享一次 sweep；晚加入消费者复用结果；旧实时被拒不阻止合法 sweep 处理 |
| 恢复 | 取消订阅后不新投递；更新 terms 不混合旧新批次；重启恢复 lease；新 schema 可读旧任务；回滚先 drain |

新增 tests/test_message_bus_monitoring_terms.py、test_message_bus_search_plan.py、test_message_bus_distribution.py、test_message_bus_jev.py、test_message_bus_ctee.py，并扩展已有 enrichment 与 bus orchestration 测试。Mock 断言网络次数、时间窗口、故障恢复和实际状态转换；真实站点验证与模型样本测试单独 opt-in，不在单元测试默认联网。

本地先运行相关现有 Message Bus、dedup、enrichment、Site Strategy 和 orchestration 定向回归，再执行 Ruff/mypy/Compose config。远端证明：真实工商时报新文章进入共享池、完成或合理降级补全、按已提交规则投递、两个 search 源正确采用统一 L1；记录 Jev 实际请求数、时延与失败率，不以“HTTP 200”代替分类质量。

状态统计必须区分入口抓取、正文、规则命中、模型命中、实验失败降级、时效拒绝、去重和投递。首批少量人工标注样本用于判断 Jev 增益，不把模型优于正则设为核心功能上线前置条件。

## 14. 外部接口证据及待现场核验项

- Jev 支持一个 state、多独立 questions，Noul 为 0–1 判定值：[TypeSafe Introduction](https://docs.typesafe.ai/introduction)。
- OpenRouter System One 接入与模型版本说明：[OpenRouter Jev 接入说明](https://openrouter.ai/blog/insights/what-is-jev/)、[Jev 1.13 模型页](https://openrouter.ai/typesafe/jev-1.13)。前一轮已读取；本轮重新访问接入文章失败，因此实施阶段应先做一条最小授权请求验证当前 endpoint/model/response 契约，再开发完整批量适配。
- 模型可能受字面歧义、无关长文和对抗内容影响：[Jev 已知限制](https://docs.typesafe.ai/model-jaggedness/jev-1.13)。定义应具体，正文只能作为数据。
- [工商时报指定入口](https://www.ctee.com.tw/industry/semi) 本次检索工具受 robots 限制，尚未完成生产网络实测。开发阶段以真实列表、分页和正文样本确定 parser，不把此限制等同于生产浏览器被封禁。

这些现场核验是实施工作项，不需要再开一轮需求问答。若 provider 契约变化或站点只有付费可见内容，记录实际证据并隔离该适配；共享采集、词管理、双轨判定及窗口衔接仍可独立完成。
