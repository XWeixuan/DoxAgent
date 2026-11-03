# RSS → 轻量网页发现迁移与验收（2026-11-04）

本轮审查了生产中的 20 个外部源、26 个内部入口、54 个启用订阅。16 个源找到了可用 HTTP/JSON 发现路径；另 4 个源保留可执行的解析/探测能力及 RSS 保底，但不把未通过覆盖或及时性验收的路径标成成功。第一轮生产观察已完成，下面保留完整统计和第二轮修复范围。

## 1. 逐源入口与处理结果

外部 source_id、栏目归属与订阅保留；内部以原 RSS URL 的哈希作为 FEED 单元，不把不同栏目重新拆成独立外部源。表中为新入口；原 RSS URL 在代码与测试 manifest 中逐一保留。

| source_id | 实际网页/接口 | 获取方式 | 开发阶段结论 |
|---|---|---|---|
| `data_center_frontier` | `https://scorpia.graphql.aspire-ebm.com` | GraphQL POST | 轻量入口与栏目对齐通过 |
| `dod_contracts` | `https://www.war.gov/News/Contracts/` | GET HTML | 轻量入口与栏目对齐通过 |
| `nsf_news` | `https://www.nsf.gov/news` | POST HTML | 轻量入口与栏目对齐通过 |
| `army_news_rss` | `https://www.army.mil/news` | GET HTML | Worldwide覆盖不足，RSS保底 |
| `world_nuclear_news` | `https://www.world-nuclear-news.org/` | GET HTML | 轻量入口与栏目对齐通过 |
| `canary_media` | `https://www.canarymedia.com/articles` | GET HTML | 轻量入口与栏目对齐通过 |
| `technews_hardware` | `https://technews.tw/category/semiconductor/`<br>`https://technews.tw/category/ai/` | GET HTML | 轻量入口与栏目对齐通过 |
| `crn_cloud_security_datacenter` | `https://www.crn.com/query-index.json?offset=0&limit=50&sheet=cloud`<br>`https://www.crn.com/query-index.json?offset=0&limit=50&sheet=security`<br>`https://www.crn.com/query-index.json?offset=0&limit=50&sheet=data-center` | GET JSON | 轻量入口与栏目对齐通过 |
| `dod_releases` | `https://www.war.gov/News/Releases/` | GET HTML | 轻量入口与栏目对齐通过 |
| `aviationweek_rss` | `https://aviationweek.com/search` | GET HTML | 完整列表运行时待评估 |
| `utility_dive` | `https://www.utilitydive.com/` | GET HTML | 轻量入口与栏目对齐通过 |
| `etnews_rss` | `https://www.etnews.com/news/section.html?id1=06` | GET HTML | 轻量入口与栏目对齐通过 |
| `quantum_zeitgeist` | `https://public-api.wordpress.com/rest/v1.1/sites/quantumzeitgeist.com/posts/?number=100&fields=ID,URL,title,date,excerpt,modified` | GET JSON | 轻量入口与栏目对齐通过 |
| `huggingnews_rss` | `https://huggingnews.com/__data.json` | GET JSON | 轻量入口与栏目对齐通过 |
| `quantum_computing_report` | `https://quantumcomputingreport.com/news/` | GET HTML | 轻量入口与栏目对齐通过 |
| `tomshardware_rss` | `https://www.tomshardware.com/archive` | GET HTML | 列表长缓存，RSS保底 |
| `dcd_infrastructure_rss` | `https://www.datacenterdynamics.com/en/news/?page=1` | GET HTML | 全栏目覆盖及缓存/搜索风控未通过 |
| `thelec_semiconductors_rss` | `https://www.thelec.kr/news/articleList.html?sc_section_code=S1N2&view_type=sm` | GET HTML | 轻量入口与栏目对齐通过 |
| `defensescoop_drones` | `https://defensescoop.com/tag/drones/`<br>`https://defensescoop.com/tag/counter-drone/` | GET HTML | 轻量入口与栏目对齐通过 |
| `enterprise_apps_official_rss` | `https://news.sap.com/wp-json/wp/v2/posts?per_page=50&_fields=id,link,title,date_gmt,modified_gmt,excerpt`<br>`https://www.salesforce.com/news/wp-json/wp/v2/posts?per_page=50&_fields=id,link,title,date_gmt,modified_gmt,excerpt`<br>`https://newsroom.workday.com/press-releases` | GET HTML/GET JSON | 轻量入口与栏目对齐通过 |

## 2. 实现与资源边界

- 复用现有 TechnologyHttp / PublisherClients、固定出口、主机与角色预算。没有新增 Chrome、Playwright/NCP、Profile、常驻页面、服务或数据库表。Collector 抓取与 BODY 原有资源隔离保持不变。
- `ListingAdapter` 只包装实时发现；source_id、原 adapter_ref、site_id、语言、By Ticker/Distribution、正文 reader、正文 route/身份、正文完整性检查均保留。已完成正文的文章继续通过同一 canonical article_key 复用；ARTICLE_ONCE 未改。
- CRN 三个栏目、TechNews 两个栏目、DefenseScoop 两个标签，以及 SAP/Salesforce/Workday 三个内部入口分单元执行。一个入口超时不会占住兄弟入口的整轮锁。
- 列表请求保持原订阅周期，未用拉长周期掩盖失败。支持 ETag/Last-Modified；只在原入口实测有效时加入按分钟变化的刷新参数。JSON 只请求列表所需字段，响应沿用 4 MiB 上限。
- 成功列表保存最多 1024 个 URL 的标题/摘要/时间签名；内容未变化不重复递交 BODY。未知发稿时间不参与轮询签名，也不冒充精确发布时间。
- 本轮只替换新消息实时发现；有冻结 window_cutoff 的闭市补扫仍调用原 RSS/恢复逻辑，保留既有历史覆盖方式。
- 配置通过 `scripts/ops/configure_rss_listing_migration.py` 原子应用，新增 `discovery_mode=http_listing`。保留源的所有旧参数及 binding 的业务内容，推进源版本与引用版本；重复执行无变更。未重新配置监测词、自动开始 ticker 或改变订阅集合。

## 3. RSS 保底的状态语义

HTTP 拒绝、超时、空壳/结构变化、覆盖未通过，或指定源返回明显过旧的 Age 时，仍可从原 RSS 接纳新闻。返回 `HTTP_LISTING_FAILED_RSS_FALLBACK`，原始原因在 `last_error_message` 和消息的 `listing_error` 中保留。

保底请求以 `rss_fallback` 角色写入物理请求证据，不能获得新网页列表的有效证明、不能刷新成功时钟。采集 cycles、unit、binding UI 都显示失败；历史成功记录不能把此次回退“漂白”为 partial/succeeded。旧共享调度路径也保留该语义。RSS 保底新闻不因此被丢弃。

已知栏目缺口不反复请求同一个无效外壳，只以明确失败状态继续 RSS；Toms 的 HTTP 候选遇到超过 300 秒的缓存 Age 即回退。300 秒是本轮针对已证实长缓存候选的验收上限，不是把文章发布时间当作列表更新时间。

## 4. 重要实测与待统一评估项

### 可直接落地的轻量路径

- DCF 首页有约 600 秒 CDN 缓存，且只是版面编排；追查其实际前端 GraphQL 文档后，改用公开 `getContentStream` 固定小查询。约 27 KB 响应已返回缓存首页尚未出现的新 Podcast；保留 Article、News、Podcast、Video、Whitepaper 类型的真实路径，过滤目录联系人等非新闻对象。
- NSF GET `/news` 实测命中长达约 6 小时的 CDN 缓存；查询参数未绕开，`/news/releases` 出现 AWS WAF 202。相同公开只读列表的 POST + `X-Requested-With: XMLHttpRequest` 多次返回 `must-revalidate, no-cache, private` 新响应，采用该方式；没有提交表单或写入站点。
- CRN HTML 是 JS 外壳；沿网页 category template → shared.js → index-worker.js，找到网页本身调用的 `query-index.json?sheet=cloud/security/data-center`。保留三个栏目，不用全站首页替代。
- HuggingNews 采用同一 Svelte 页面的 `__data.json`，解析引用表而不执行 JS；使用 story 的 publishedAt，不用 eventTimeApprox 冒充该源的发稿时间。
- Quantum Zeitgeist 首页 429；官方 WordPress 公共 API 无浏览器即可读，首轮 50 条列表约 27 KB；第二轮改为固定 US HTTP 出口、两个 100 条分段（约 124 KB），实测 200 个不同文章 ID、最近一天 102 条 RSS 样本零缺口，保留原 RSS 的 200 条头部范围。两段和短重试共用 12 秒预算，不下载正文；冻结补扫保留旧 RSS 能力。固定出口是在独立探测后选择的配置，不是遇到 429 自动切换。
- TechNews AI 栏目裸 URL 曾返回约 15 小时缓存；按分钟变化的查询参数实测返回新响应。Canary、QCR、Utility Dive、SAP、Workday 的刷新方式也分别验证，不对所有站点盲目套用。
- ETNews 原 `06.xml` 的实际主题是“电子”，对齐 `/news/section.html?id1=06`；The Elec 对齐 S1N2 半导体栏目，保留 `idxno` 区分文章。韩国/台湾显示时间按当地时区解析；仅有日期的条目标 DATE；Utility Dive 无列表精确时间的条目标 UNKNOWN_FIRST_SEEN，交由原 BODY 链补准。

### 未通过切换验收的四项

| 来源 | 已确认的问题 | 本轮生产处理 | 浏览器评估边界 |
|---|---|---|---|
| Aviation Week | 原 RSS 为 100 条跨频道内容；首页/频道页为精选，不能等价替换。完整 search 返回需要运行时的空壳；已追查前端资源，尚未验证可直接使用的完整列表 HTTP 接口。 | 显式 coverage_review，RSS 保底，失败状态；避免每分钟重抓空壳。 | 可评估已有 Chrome/NCP 访问完整列表；尚未证明“有浏览器就能解决”，不新建身份。 |
| Army | Worldwide HTML 325 条区域编排稿，最新 RSS 19 条样本中有 14 条不在该页；JS 只切换已有 DOM，并不补齐全部稿件。 | 保留已开发 HTML parser，但因覆盖不等价继续 RSS，明确失败。 | 浏览器渲染同一 DOM不能修复编辑覆盖差异；应先找完整公开索引，不建议盲建 Chrome。 |
| DCD | `/en/news/` 缓存实测约 48 分钟且普通刷新参数无效；原 RSS 20 条还含 analysis/opinions/whitepapers，新闻页缺 3 条。公开 search 候选返回 Cloudflare 403，常见 Wagtail API 404，列表只读 POST 403。 | 保留新闻页 parser，完整覆盖未通过，RSS 保底失败；不把缺栏目视为成功。 | 可评估完整搜索/多栏目浏览器入口，但还须解决列表缓存，不能仅看 HTTP 200。 |
| Tom’s Hardware | `/archive` 是全类型索引且有当前 RSS 样本覆盖，但缓存 Age 实测约 55–110 分钟；刷新参数无效；显式年月档案仍约 15 分钟，POST 405。`/news` 仅新闻，不能代替全部 RSS 内容。 | 已实现全类型 archive parser，过旧缓存回退 RSS 并显示失败。 | 当前主要是上游缓存，启动浏览器未必改善；应以新稿同时对照验证更鲜活入口后再切换。 |

## 5. 验证记录

- 26 份真实入口样本与对应 RSS 样本裁剪为测试 fixtures，约 280 KiB。逐个审查/调试，每次至多处理两个外部源。
- 相关既有链路回归：269 项通过（含早期 39 项新测试）；后续最终发现/状态/旧共享调度专项：50 项通过（42 项新增测试与 8 项共享分发测试，和前一批有重叠，不能相加称 319 个独立测试）。
- 单独验证了规范化语言后的 article_key 与 RSS 一致、The Elec 查询 ID、精确/日期/未知时间语义、304 基线、未变化列表不重复递交、真实变化重新递交、取消/停用不触发保底、慢入口不阻塞兄弟、RSS 有消息但 UI 与审计仍失败。
- 开发探测通过单独进程运行，不向生产总线注入历史/合成消息，不伪造发布时间，不更改交易日历。

## 6. 生产发布与十分钟观察

第一轮生产 commit 为 `4b6e7398345618cab5be3581b549a672fc6fccff`，镜像 `sha256:c425c60f8735eaa725f30c5c59df1fa23ea0166f8050d4f46b4ae9f22a87a053`。仅更新 Collector、Message Bus、API；BODY 和其他组件保持不变。

观察窗口为北京时间 **2026-11-04 04:32:21.871—04:42:22.332**，600.46 秒。26 个内部入口各完成 10 次真实物理周期，共 260 次，不按 ticker 扇出重复计数。

- 16 个已切换源、22 个内部入口：217/220 次网页列表成功（98.64%）；DoD Releases、Utility Dive 各一次连接/响应失败后恢复，Quantum Zeitgeist 一次 429 后恢复。429 当轮 API 与 RSS 均被拒绝，如实失败，不重试拒绝响应。
- 4 个已知例外各 10 次 RSS 保底周期，40 次均明确显示失败；没有被 RSS 的可用性“漂白”为网页成功。全 20 源原始口径为 217/260 次成功，两种口径分别保留。
- 所有源的订阅集合及绑定业务参数、监测词、非目标源配置、其他组件版本核对一致。20 个源模式均为 http_listing，54 个启用绑定保留；配置重跑全部 unchanged。
- Collector 采样约 332—369 MiB、25 个 PID；API、Bus 和 BODY 亦记录绝对采样值，不能把其他来源并行负载算成本轮增量。没有新增浏览器进程/身份。
- 切换对齐产生的 38 个来源关联中，36 个关联已有/取得 FULL，2 个 UNAVAILABLE；包含旧稿的首次来源对齐，不能称为 38 篇真实新稿，更不能据此计算真实新文章正文成功率。

第二轮针对首轮故障与窗口覆盖补齐：同一出口最多重试一次，间隔 0.2 秒，两次共用 12 秒主路径预算，403/429 不重试、不轮换出口。另把 terminal_error_message 留入持久周期证据，便于恢复后仍能解释历史失败。另保留企业源按 binding 选择的内部 feed；JSON 提供的 modified/lastModified 纳入材料更新证据，使正文发生更新而标题未改时仍能重新验证。第二轮相关 68 项测试通过（含 47 项本轮专项）；两个分段均绑定真实解析证明，任一段失败保留失败 RSS 保底。第二轮仅更新 Collector/Bus，源配置重跑应无变化，BODY/API/浏览器保持第一轮状态。完成后再观察 10 分钟；本轮不进行第三次发布循环。

十分钟没有自然新稿时，只验收采集健康与资源，不能据此宣称发稿到发现的端到端延迟已经降低。

回退操作沿用源版本回滚，或把指定源 `discovery_mode` 改为 `rss` 以完全恢复旧发现实现；正文链路与已提交消息不回滚。
