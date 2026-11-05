# RSS 迁移后的入口调整与缓存复核（2026-11-04）

本次按新的栏目范围调整四个源，并重新检查此前已切换的 16 个源、22 个内部入口。不是用 HTTP 200 作为实时性验收：保留请求方式、响应缓存头、实际解析条目、前端列表路径及反例。前次两轮记录仍见 [原迁移报告](rss-to-http-listing-migration-20261104.md)，本文覆盖本次后续调整。

## 1. 四个指定源

### Aviation Week

仍为一个外部消息源 `aviationweek_rss`，RKLB 原订阅不变，内部五个独立 FEED 单元：

| 栏目 | 实际轮询入口 |
|---|---|
| Launch Vehicles & Propulsion | `https://aviationweek.com/taxonomy/term/186836` |
| Satellites | `https://aviationweek.com/taxonomy/term/186841` |
| Budget, Policy & Regulation | `https://aviationweek.com/taxonomy/term/186846` |
| Operations & Safety | `https://aviationweek.com/taxonomy/term/186851` |
| Commercial Space | `https://aviationweek.com/taxonomy/term/456` |

从 Space 页的五个 `/space/...` 入口开始检查，发现其中 Launch/Budget 等页面的标题虽然正确，内嵌 Drupal View 却配置成 Commercial Space 的 `456/all/all/10`，不能当作五个独立栏目。改用同名 taxonomy 的完整列表，各 25 条，有原生精确发布时间，校验 H1 栏目名称并只解析 main 内的新闻行。该列表首屏直接由服务端返回，不依赖 search 的 JS 空壳，也不需要执行 Drupal 分页 AJAX。

五个 taxonomy 均做了相隔约 12 分钟的两组请求，及真实 adapter 首次/重复轮询。样本为 Varnish MISS、Cloudflare DYNAMIC、Age=0，Last-Modified 随页面生成更新；声明的一年 max-age 本身没有被当作即时性证明。每个 adapter 返回 25 条，重复轮询均为 0 条变化。

内部键在多个栏目共享同一旧 RSS 时加入栏目 URL，避免五个栏目互相覆盖为一个单元。组合结果按规范 URL 去重；独立调度的跨栏目重复稿继续由现有 Bus 的文章身份和去重处理。真实跨标签样本通过“首次 inserted、第二次 duplicate、同一 raw 记录”的入库测试。各栏目独立调度、检查点和失败状态，不建新 Chrome/Profile。

这次明确将监测范围改为用户指定的五个太空栏目；不再把“是否覆盖原全站 11 类 RSS”当成切换门槛。运行中 HTTP 失效仍可 RSS 保底并显示失败；如果整个轻量方案不能正常工作，可把该源设回正常 RSS。

### U.S. Army / Data Center Dynamics

`army_news_rss`、`dcd_infrastructure_rss` 设置 `discovery_mode=rss`、kind=api，直接执行原 RSS 实现。不会先探测旧 HTTP 候选，也不会再人为保留 `HTTP_LISTING_FAILED_RSS_FALLBACK` 错误。RSS 请求本身失败时仍如实失败。

### Tom’s Hardware

按要求实测 `/news/`，补充 news 列表解析能力，不再使用 archive 候选。普通地址得到 Age=809 秒，添加刷新参数后重定向丢弃参数；改用无尾斜杠的 canonical URL 保留参数后，Age 仍为 942 秒，三次响应正文哈希相同。响应同时声明 `no-store,private`，证实不能只看 Cache-Control。跟随页面引用检查 ArticleRiver 脚本，所见脚本是曝光统计，不是替代首屏列表的更新接口。

因此本次将 `tomshardware_rss` 整体恢复为正常 RSS。news parser 与样本保留供后续使用，生产 RSS 模式不再为该候选消耗 HTTP 请求。

## 2. 其余 16 源的复核

HTTP 缓存年龄与文章年龄是两回事。旧稿列表长期未变化可以合法存在；不能因 Last-Modified 较旧就判定缓存失效，也不能因 Date 当前、查询参数变化或返回 200 就宣称上游内容必定实时。静态 JS/CSS 的长缓存也不能套用新闻列表的判定。

| 源 | 复核证据与当前处理 |
|---|---|
| Data Center Frontier | 首页声明 600 秒及较长 stale 窗口；继续采用该前端对应的 `getContentStream` GraphQL POST，而非首页编排。API 为 DYNAMIC、当前 Date，实际解析 53 条；adapter 重复为 0 条变化。 |
| DoD Contracts | 页面原生 SSR ArticleCS 属性含合同内容类型 400、标题、链接、发布时间。普通 GET 与 no-cache 请求均 `no-cache,private`、当前 Date；解析 10 条。不等浏览器 hydration。 |
| NSF | 普通 GET 命中 CloudFront/Drupal 缓存，Age=8033 秒；相同公开列表的只读 POST 返回 CloudFront MISS、`must-revalidate,no-cache,private`。继续 POST，解析 9 条。 |
| World Nuclear News | 首屏列表直接在 HTML；响应 `no-cache,no-store`、当前 Date，解析 19 条。核查其第一方 site.js，发现的 AJAX 用于 Cookie 同意，不是更新新闻列表。 |
| Canary Media | 当前分钟 URL Age=2 秒；原入口与当前入口均实际解析新闻卡片。声明一年 s-maxage，不能因此认定一年延迟；继续分钟键并运行年龄检查。检查第一方 main bundle，首屏新闻无需另外的列表 XHR。解析 10 条。 |
| CRN 云/安全/数据中心 | 重新跟随前端 scripts/shared/index-worker，确认网页 Worker 实际请求三张 `query-index.json` sheet。三张各 50 条。Cloud/security 最近 RSS 各 10 条均在 API 中。见下方专门限制。 |
| TechNews 半导体/AI | 普通地址 Age=8236/62635 秒，当前分钟 URL 为 CloudFront MISS 或 Age=17 秒。保留经验证的分钟键，分别解析 15 条；半导体/AI 栏目不替换成全站首页。 |
| DoD Releases | ArticleCS 内容类型 9 的服务端列表；GET 为 `no-cache,private`、当前 Date，解析 10 条。保留现有固定出口。 |
| Utility Dive | 普通首页 Age=487 秒，当前分钟 URL Age=51 秒，说明 `Cache-Control:no-cache` 单独不够。继续分钟键与原 urllib 传输，解析全部匹配新闻标题区的 31 条。 |
| ETNews | 电子栏目 `id1=06` 首屏 `news_list` 在 HTML 中；前端未发现替代首屏的列表请求。两次响应 Date 更新，解析 15 条；不虚构隐藏缓存已被彻底排除。 |
| Quantum Zeitgeist | 固定 US 的 WordPress 公共 API 为 `no-cache,must-revalidate,max-age=0,no-store,private`，server-timing 显式 BYPASS。两页各 100 条，日期均当前；adapter 合并 200 条、重复 0 条。另试站内 WP API 返回 403，未替换为该入口。 |
| HuggingNews | Svelte 同页 `__data.json` 为 `private,no-store`、DYNAMIC；首页也声明 no-store。解引用结构化故事数据，解析 113 条，不通过事件近似时间推导发稿时间。 |
| Quantum Computing Report | 普通新闻页 Age=6406 秒；当前分钟 URL Cloudflare MISS、Last-Modified/Date 当前，解析 25 条。保留分钟键，不用裸新闻页。 |
| The Elec | 新发现原 GET 明确 `x-proxy-cache:STALE`；任意刷新参数、重排参数、普通 POST 均可能失去栏目筛选。改为向 `/news/articleList.html` **POST form**：`sc_section_code=S1N2&view_type=sm`，响应正确半导体栏目且 `no-cache,no-store,must-revalidate`，解析 20 条、重复 0 条。新增错误栏目拒绝测试。 |
| DefenseScoop 无人机 | 两标签分钟 URL CloudFront Age=33/2 秒，虽然声明 max-age=300，仍需检查实际年龄。首屏服务端列表各 10 条，不合并成全站源。 |
| 企业官方 SAP/Salesforce/Workday | SAP 现有 WP API 分钟 URL 为 MISS、当前 Date；Salesforce WP API 声明 max-age=60、CDN MISS；Workday 分钟 HTML Age=17 秒。分别解析 48/50/10 条；保留按 binding 选择 feed 的能力及原正文链。 |

**CRN 的保留限制：** 该接口有多层 CDN：Cloudflare HIT、Fastly 多级 MISS/HIT，`Cache-Control=max-age=7200`、`CDN-Cache-Control=max-age=172800`；没有可用 Age，Date 当前也不足以证明最内层对象刚生成。实验中的任意刷新参数和 no-store 请求没有改变返回索引，不能宣传它们已经突破所有缓存；因此移除没有证实必要性的刷新参数，直接用网站自己的索引请求。

CRN 使用的 AEM 索引模型在发布页面时更新索引，平台支持内容发布时的缓存失效，参见 [AEM 索引说明](https://www.aem.live/developer/indexing) 与 [缓存和失效说明](https://www.aem.live/docs/faq)。这些平台文档不能证明 CRN 自身的每次推送都正常。当前最新 RSS 对照无缺口，故保留该真实前端 API；本次没有自然新稿可验证 CRN 的失效回调时延，**其后端索引/多层清缓存仍是时效待验收项**，不是已证明每分钟绝对更新的源。

## 3. 轻量运行时检查

- 对所有 HTTP 列表检查实际 Age 与 Date 的可见年龄，默认超过 120 秒即失败 RSS 保底；不是依照文章发布时间判定。200、304 都检查，Quantum 两个分段分别检查。
- 拒绝 HTTP Warning 110/111，以及明确 `STALE`/`UPDATING` 的代理缓存状态；不把这些响应计作新鲜列表成功。
- 缓存证据记录可见年龄、依据（Age/Date/未知）、原始缓存头，并绑定实际物理请求回执。Date-only 仅为可见响应证据，不能证明隐藏的服务端缓存不存在。
- 未改网站时钟、文章时间、监测频率或 BODY 策略。继续同出口一次短重试、12 秒主路径总预算、403/429 不重试；没有加入逐轮多路径探测、额外常驻服务或浏览器。
- The Elec 只给现有 TechnologyHttp 增加可选 form body，沿用 curl 池、预算、出口和 4 MiB 上限，没有另建 HTTP 客户端。

最终配置目标为 **17 个 HTTP 外部源、27 个 HTTP 内部入口，另 3 个正常 RSS 源**。54 个启用订阅集合保持不变；Aviation 内部增加四个单元不增加外部源。实现、样本、缓存检查与配置幂等均有专项测试。

## 4. 验证与生产结果

探测只读外部公开网站与生产 SQLite，没有把历史样本或合成稿注入生产消息总线。安全证据在 `eval/rss_listing_recheck_20261104/`：

- `cache-probes.json`：逐次请求、缓存头、正文哈希、前端脚本引用；静态资源与列表分开解读。
- `parsed-listings.json`：16 源、22 个旧入口的真实响应解析结果；Quantum 此文件只列第一段 100 条，完整 200 条证明在 adapter canary 中。
- `preproduction-canaries.json`：实际新 adapter 的解析与重复轮询、物理请求证明。
- `regression.xml`：257 项相关回归通过；最终 `listing-tests.xml` 的 66 项专项通过。两批有重叠，不相加计算独立测试数。

生产已发布 **`c7697c88357261799994a7925561e9508c31c690`**，镜像 **`sha256:084340718b26781513f7dd81c7891f13288afd4a77aff3a190e7016ba9bc880e`**，只更新 Collector/Message Bus。运行容器镜像、build commit 和四项实际源文件哈希一致，重启次数 0、OOM=false；BODY、API、Site Access、Chrome/Supervisor 等其他组件版本未变化，资源回收 timer 已恢复 active。

观察窗口为北京时间 **2026-11-04 06:15:17.548—06:19:02.849**，225.30 秒。按物理周期去除 ticker 扇出：共 115 个周期，113 CAPTURED、1 FAILED、1 DISPATCHED；终态口径 **113/114（99.12%）**，在途不计成功、不计失败，也没有删除失败样本。

- Aviation 五单元 **19/19 成功**；平均执行 0.826 秒，P95 1.657 秒，due→实际请求开始均值 0.083 秒。生产缓存样本 Age 0—59 秒，并非所有请求都 MISS。保留单一外部源。
- Army/DCD/Toms **3/3、4/4、4/4 成功**，物理请求分别只到 `www.army.mil/rss/static/0.xml`、`www.datacenterdynamics.com/en/rss/`、`www.tomshardware.com/feeds.xml`。没有先抓 HTML，也没有 `rss_fallback` 角色或强制失败。
- The Elec **4/4 成功**；平均执行 0.721 秒，四次均为当前 Date 和 `no-cache,no-store,must-revalidate`，无原来的 STALE。
- 唯一失败为 **06:16:39 Workday**：主请求与同出口重试均未在 12 秒总预算内取得完整响应，RSS 保底 200；失败周期持续保留，下一轮恢复。SAP/Salesforce 同源兄弟单元无失败。未为一次已自行恢复的传输故障扩大超时预算或增加额外回退链。
- 27 个 HTTP 内部入口完成的成功请求中，共持久绑定 **106 份缓存证据**（含 Quantum 双段）；其余为正常 RSS、一次失败尝试及在途请求。该数是请求回执数，不是文章数。
- 17 HTTP / 3 RSS 的实际配置与预期一致；只更新三个 RSS 回退源，配置重跑全部 unchanged。原 54 个启用订阅、绑定业务参数、监测词和正文契约核对不变。**06:19:36 API 读回 54/54 succeeded、无当前错误**；此前 53/54 的 Workday 失败读回也单独保留。
- Collector 绝对采样约 261—279 MiB、25 PID，Bus 132—141 MiB；组件还处理其他来源，不能将服务总量或 CPU 快照当作本次资源增量。没有新增浏览器、身份或服务。

安全发布证据在 `production/`：`release.json`、`configuration.json`、`idempotent.json`、`observation-final.json`、`deployment-verification.json`、`api-final.json`、`source-hashes.json`、`workday-timeout.json`。未导出带 Compose 环境或完整生产配置的私有快照。

本观察窗口新增来源关联为 0，故只验收采集健康、范围、缓存证据与故障隔离；不能由此宣称新稿发稿到发现已达目标。CRN 的多层失效回调时效限制仍按第 2 节保留。
