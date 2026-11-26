# 医药、生物科技与医疗创新消息源接入与验收（2026-11-26）

## 交付范围与状态

新增 9 个外部消息源、13 个独立列表入口，现有公司 IR / 新闻稿聚合源追加 5 个 ticker 专属入口。复用现有 Publisher HTTP、Site Access 路由、Collector、正文补全、ARTICLE_ONCE 去重和发布链路，没有新增服务、数据库、Chrome 进程或浏览器身份。新列表和 IR 入口均为 20 秒目标周期；同源入口分别调度，同一站点共用请求并发与间隔限制。

代码版本：`638b246315447b7e0a7a5f4dd4aff656101e0ddd`。生产发布与订阅结果见下方机器回执。

五个 ticker 尚未进入初始化流程，本轮只完成源发布和真实订阅预配置，不虚构统一监测词、不启动下游。正常初始化完成后，由既有流程启用实际监测。启用时间窗口和用户暂停状态仍然生效。

## 新增消息源及实际抓取方式

| 外部源 | 订阅对象 | 列表实现与筛选 | 正文及验收 |
|---|---|---|---|
| FDA Release | 五个 ticker | Approvals、CBER 官方表格 HTTP POST；Calendar 官方 JSON GET，三个独立入口 | HTML 及官方完整 PDF；三个抽样 FULL |
| Endpoints | 五个 ticker | `/news/` HTTP POST，解析文章卡片及相对发布时间 | 抽样为订阅文章，保留 UNAVAILABLE / subscription_required |
| Fierce Biotech | 五个 ticker | 官方前端使用的 fronts API；按返回 UUID 读取原生 JSON，限定 biotech、已发布、排除赞助文章 | 原生 JSON 完整 body，校验 UUID、路径及访问标记；抽样 FULL |
| STAT | 五个 ticker | biotech、clinical-trials 两入口 HTTP POST，外部一个源 | 两个抽样均为订阅文章，不降级伪装完整正文 |
| HKEXnews / AKESO 09926 | 仅 SMMT | 官方标题查询 POST，stockId 1000035461；Announcements and Notices；本地再校验 09926 和类别 | 完整 PDF 抽取，抽样 FULL |
| Akeso Press Releases | 仅 SMMT | Akeso 官方新闻 HTTP POST + Akeso PR Newswire 发布记录 GET，独立入口、统一源及去重 | 两个抽样 FULL |
| Taiho Oncology Press Releases | 仅 CGEM | 官方 news HTTP POST | 抽样 FULL |
| ASH / Cullinan | 仅 CGEM | 官方 ASP.NET 表单检索 Cullinan；只缓存表单初始化状态，不缓存结果列表 | 保留 paper 的 `pid`，核验原生表单 action，提取完整 ABSTRACT；抽样 FULL |
| AES Scientific Abstract Search | 仅 XENE | 官方页面嵌入的 FUSE 搜索 API，使用 ticker 冻结的统一 L1 查询计划，读取所有结果页 | 使用原生摘要内容；抽样 FULL；无统一词时不编造生产检索词 |

每个专用源的名称、分类卡片简介均注明专属 ticker；STAT、Akeso、FDA 没有按入口碎片化为多个外部源。没有为上述来源新增 Chrome 路线。Endpoints 和 STAT 的付费内容限制是保留的业务限制，不计为已取得完整正文。

### 公司 IR 聚合源

仍使用 `corporate_ir_press_releases`，只追加以下 5 个频道，合计 21 个内部频道，原有频道不替换。

| 频道 | 所属 ticker | HTTP 实现 | 抽样 BODY |
|---|---|---|---|
| xene.press | XENE | Xenon NIR 列表 POST | FULL |
| rgnx.press | RGNX | REGENXBIO NIR 列表 POST | FULL |
| prax.press | PRAX | Praxis NIR 列表 POST；从标题节点读取标题而非 “Read Full Release” | FULL |
| cgem.press | CGEM | Cullinan NIR 列表 POST | FULL |
| smmt.press | SMMT | 官方 Q4 PressRelease JSON API，固定复用现有 us-standard-5 路由 | FULL |

Summit 直接出口出现 429，已使用已有固定路由取得列表及完整正文，没有逐请求轮换网络。每个绑定只选择自己的 `endpoint_ids`，不读取其他 ticker 的 IR 频道。

## 订阅规则

预配置 97 个真实、启用的 TickerSourceBinding：XENE 19、RGNX 19、PRAX 18、CGEM 20、SMMT 21。

共同包含四个行业源及 14 个现有 GLOBAL 非 Chrome 来源（含 IR 聚合）：ACCESS、Benzinga、Business Wire、公司 IR、Finnhub、GlobeNewswire、Google News Search RSS、IBKR、PR Newswire、SEC EDGAR、Stocktwits、The Fly、TMX Newsfile、X 官方账号。

仅 RGNX、SMMT 增加 Reuters。Yahoo、Bloomberg、Barron's、Investing、InvestorsHub 不进入这五个 ticker 的批准组合。专用源只绑定对应 ticker。初始化默认绑定逻辑也遵守同一组合，避免初始化重新加回被排除的来源。

未启动 ticker 的 SYSTEM 预配置可先建立 Massive 绑定；正式运行或用户配置仍执行现有 massive_l2 词校验。既有通用来源的凭据、订阅或人工验证阻塞没有因为绑定而自动解决，本轮未修改这些来源的访问策略。

X 官方账号首次词配置的缺省值来自各公司官方网站链接：`XenonPharma`、`REGENXBIO`、`praxismedicines`、`CullinanTx`、`SMMT_TX`。只在首次配置时补默认值，后续用户清空不会被重新覆盖。本轮没有为未初始化 ticker 建立实时 X 规则。

## 可复用的美东启用日期

可选配置位于现有 `default_parameters.activation_window`，包含 `start_date`、`end_date`、`timezone`。日期首尾均包含，时区使用 `America/New_York`，自动处理夏令时。复用源级配置，兼容仍运行旧 SourceDefinition 解码器的服务，不引入定时任务。

- ASH：2026-12-03 00:00 至 2026   -12-10 00:00 美东时间之前。
- AES：2026-12-07 00:00 至 2026-12-08 00:00 美东时间之前。
- 当前只有这两个源设置日期，其他来源未启用该限制。
- Collector、原生调度和状态卡片共同识别 `WINDOW_CLOSED`；日期窗口内允许周末会议监测，但不越过 ticker 未启动、停止或用户暂停状态。

**日期事实提示：** ASH 官网显示本届会议为 12 月 12–15 日，与用户给出的 12 月 3–9 日窗口不同。本轮保留用户指定窗口，未自行修改。需要监测会议期间内容时，应另行调整窗口。

## 时间与内容质量

主要按官方发布时间记录新文章；只有日精度时保留 DATE，相对时间保留 RELATIVE。Endpoints 使用已有相对时间保守边界算法，避免把估算时间当精确时间。

FDA Calendar 的会议日期不是发布时间；AES 展示的报告/会议日期也不是发布时间。缺少官方发布时间时使用 UNKNOWN_FIRST_SEEN，原生修改日期单独记录为更新证据。相对时钟变化不会让同一卡片反复触发正文补全；ASH `pid` 在 URL、raw_url、external_id、source_item_key 中均保留，避免不同摘要被误合并。

本轮未引入 Age > 30 秒的失败判断。Age、Cache-Control 只作诊断。优先选择原生列表 API、官方表单或经实际返回验证的 POST，但这些措施本身不证明实时性。

**真实 publish→首次发现→Message Bus 延迟尚未验收。** 五个 ticker 尚无正式统一词与运行状态，本轮没有自然新文章进入它们的 Bus。HTTP 200、列表耗时、静态样本 BODY 成功不替代该业务验收。初始化完成并有实际新文章后，应从现有采集/正文/发布台账分别统计发现、排队、正文及发布耗时，并对比官方前端最新内容；未知发布时间的样本单列，不能混入精确发布延迟均值。

## 测试及真实网络验收

- 定向回归：**242 passed、1 skipped、2 deselected**。覆盖日期窗口、专属绑定、初始化规则、原生解析、正文、IR、搜索工作单元及现有金融源回归。
- 两个未选入的旧 Reuters“三查询”断言在未修改基线同样失败，当前生产查询策略为一条；未借本轮更改既有策略。
- 新模块语法、Ruff E9/F、git diff 检查通过。
- 真实 HTTP 复测：**18/18 内部入口返回并解析出列表**；每入口抽样一条正文：**15 FULL、3 需订阅**。这是抽样快照，不是长期完整率或实时性 SLA。
- HKEX 实际验证查询代码/类别及 PDF；ASH 实际验证原生搜索和 pid；Fierce 实际验证官方 JS 对应 API 与原生全文。
- 只读验收脚本不入 Bus、不调用决策模型、不启动 Chrome；诊断中的 Xenon 查询仅为测试输入，不写入正式统一监测词。

真实抓取快照：[http-body-canary.jsonl](artifacts/biopharma-sources-20261126/http-body-canary.jsonl)。

## 生产发布与回执

沿用既有不可变镜像覆盖层和加锁发布 Owner，发布 API、Collector、Bus、Content Enrichment、Initialization、O4 六个角色。每角色复制完整 Message Bus 包及 API Bus 模块，逐文件哈希核验。其他业务代码保留在各自当前镜像中；Web、交易和 Site Access 不在发布范围。

发布前发现 Initialization 的实际镜像与旧台账不同，但现有 Compose、命令、环境均与正在运行的 `coin-survey-validation-20261125` 镜像一致。先备份台账，登记该已存在镜像为实际基线，再在其上构建；没有恢复旧镜像。初始化进度相关 repository/substeps 文件在发布后再次核对。

镜像为增量覆盖层：源码版本以镜像 revision、发布 manifest 和逐文件哈希为准，基础镜像继承的 BUILD_COMMIT 环境变量不作为此次完整镜像源码版本证明。

2026-11-26 北京时间 06:42–06:43 完成六个角色的生产切换，06:44 完成配置及核验：

- 六个角色均运行，重启计数均为 0；有独立健康检查的 API、Collector 均 healthy。
- 每角色 108 个覆盖文件哈希全部匹配上述源码版本；原初始化 repository/substeps 两个文件的哈希与发布前完全相同。
- 97 个实际绑定全部启用；5 个 ticker 仍为 NOT_STARTED、统一监测词 revision 0。没有新增初始化任务或候选。
- 仅 ASH/AES 设置美东日期窗口，当前均未到启用日期；IR 总频道数为 21，新增 5 个均为 20 秒且 owner 隔离。
- 已发布镜像再次完成 18/18 入口复测，仍为 15 FULL、3 订阅受限。该复测主动检查窗口外会议源，不代表窗口外自动调度已开启。
- 配置完成后的有界台账采样已看到现有 COIN 的新 Bus 发布；本轮五个新 ticker 没有被意外启动。发布后日志检查没有新源配置解码异常，另见一条既有 Google News URL 正文解析失败，未扩展本轮范围修改通用来源。

回执：

- [实际配置写入](artifacts/biopharma-sources-20261126/configuration.json)
- [订阅、日期与 IR 归属核验](artifacts/biopharma-sources-20261126/verification.json)
- [六角色镜像、文件哈希与初始化修复保留](artifacts/biopharma-sources-20261126/release-verification.json)
- [已发布代码 HTTP/BODY 复测](artifacts/biopharma-sources-20261126/postdeploy-http-body-canary.jsonl)

私有原始网页、认证配置和发布环境没有加入仓库。

## 运维入口

沿用现有运行环境执行：

```text
scripts/ops/biopharma_release.py build|deploy --commit <精确版本>
scripts/ops/configure_biopharma_sources.py --apply --receipt <私有回执路径>
scripts/ops/verify_biopharma_sources.py
scripts/ops/accept_biopharma_sources.py [--source <源或IR频道>] [--list-only]
```

配置脚本通过现有 Owner Service 写入，保存私有配置备份，必要时同步初始化候选；不会创建监测运行状态或启动 ticker。发布 Owner 保留各角色上一镜像和 Compose 配置以供回退；回退前应检查已写入的新源及窗口的消费者兼容性，不删除既有业务消息。
