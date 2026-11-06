# 五个 Newswire 的 HTTP 发现迁移与验收（2026-11-07）

本轮只改新消息发现，保留现有正文补全、10 ticker 正则、Massive Distribution 测试隔离和来源身份。测试消息仍不进入正式 Message Bus/Decision Model。没有新增浏览器、Chrome 身份、常驻进程或数据库表。

## 1. 实测结论与采用入口

测试在本地及新加坡生产 Collector 网络内执行，使用已有轻量 HTTP/curl 传输；curl 的 TLS impersonation 不会启动 Chrome。以下是抓取能力结论，不等同于新稿实时性已经达标。

| 来源 | 采用/处置 | 缓存、前端和限制 |
|---|---|---|
| PR Newswire | 只读 `POST /news-releases/news-releases-list/?page=1&pagesize=100` | 前端实际通过页面导航切换 page/pagesize，没有发现这个列表另用 JSON 接口。GET 声明 max-age=60，实测 Age 3–42 秒；7 组 GET/POST 对照中 POST 无 Age/Cache-Control/ETag，Last-Modified 接近响应时刻。不过观察时头部稿件相同，不能仅据此证明 POST 底层列表没有缓存。 |
| GlobeNewswire | 公开 `GET /en/Search/load/more?page=1&pageSize=50`，加独立并行原全站RSS | 沿用页面公开加载更多入口，最多 50 条（请求100仍50），大于原 RSS 20 条。生产直连轻量 curl 可用，不再为列表绕 TR 出口；保留原 RSS/BODY 路由。响应 max-age/s-maxage=60，无 Age 不代表无缓存。前端 JSON `/api/user/search/subscriptions` 未登录401；POST列表500，未采用。真实新稿对照已发现HTTP比RSS晚60秒的例子，因此不能单独用HTTP替换RSS。 |
| ACCESS Newswire | `GET /newsroom?page=1` | 原实现已经是 HTTP，并非 RSS。补分页与缓存审计。每页20；缓存 max-age=60，另允许 stale-while-revalidate=300、stale-if-error=600，曾观测 Age70、hit graced cached。额外参数400，旧 `/newsroom/api`401，POST未返回列表，均未采用。不虚构企业新闻 RSS，博客 feed 不可兜底。 |
| Business Wire | **HTTP受阻，待用户评估；保留原 RSS，状态失败** | newsroom（有无 language=en）、尾斜杠、旧 portal 列表，在直连和现有 US 出口轻量 curl 均403。普通HTTP亦未取得有效列表。没有足够证据支持部署一个可实时工作的HTTP入口；不再每30秒重复撞已确认的验证墙。原全语言RSS保留，不以英文栏目缩减覆盖。 |
| TMX Newsfile | **HTTP受阻，待用户评估；保留原 RSS，状态失败** | search/news、PHP与尾斜杠形式，GET/POST、直连及现有US出口均触发AWS WAF JavaScript挑战（202），不能当空列表成功。未实现验证脚本或引入浏览器。 |

后两家需要进一步评估现有身份浏览器或授权接口；本轮没有测试 Chrome，所以不声称 Chrome 一定能解决。它们没有完成 HTTP 替代，RSS 兜底不能算迁移成功。

原始入口来自用户指定页面：[PRN](https://www.prnewswire.com/news-releases/news-releases-list/)、[GN](https://www.globenewswire.com/en/Search)、[ACCESS](https://www.accessnewswire.com/newsroom)、[BW](https://www.businesswire.com/newsroom?language=en)、[Newsfile](https://www.newsfilecorp.com/search)。以上状态/缓存/授权结论来自本轮抓取，而非第三方文档推断。

## 2. 实现与边界

- 新增 `message_bus_v2/newswire_listing.py`，接入原 `NewswireAdapter`，原RSS解析与BODY均沿用。借鉴 `public_listing.py` 的只读POST、响应缓存检查、传输证据验证、失败RSS兜底设计；复用其缓存审计函数，检查Age与Date的实际响应年龄、Warning及明确STALE/UPDATING状态，不把no-cache请求头当作绕缓存成功。
- 参考现有 Data Center Frontier GraphQL、WordPress JSON、CRN query-index、NSF/The Elec只读POST路径；新站只有实际探测可用才采用，没有生造一套通用API框架。
- 每个外部源一个物理 `WIRE:listing`，GN另保留独立 `WIRE:all` RSS：五家原7个物理单元变为6个，10 ticker共享。GN两路径独立调度，不等另一路结果，30秒周期错相；统一URL去重，两路径摘要差异仅保留为审计字段，避免当正文更新重复下载。PRN原两个RSS与GN原Public Companies RSS仅用于HTTP失败兜底。站与站独立，原测试HTTP容量2、测试BODY容量1不增加。
- 逐卡解析、不按第一个旧时间或重复条目提前结束本页；PRN结合页面日历日期/跨日完整日期，GN和ACCESS使用Eastern时区。保留`publication_time_precision=minute`，正文流程仍可取得精确时间，不伪造秒精度。
- 默认首屏；突发/恢复时按需翻页，直到看到已知文章、整页均早于接纳窗口或尾页。真实对照发现PRN会混入旧稿，所以不能凭单个旧日期停止分页；跨页重复URL优先保留第一页内容。单次至多4页，HTTP总预算12秒；超界明确失败并保留已发现消息，RSS兜底总预算12秒。单次超界不会拖住其他平台。
- URL文章身份沿用，`seen`按标题/摘要稳定签名，最多1024条；304只有已有有效基线才成功。BODY复用和ARTICLE_ONCE发布语义不变。
- HTTP响应实际年龄超过120秒、陈旧警告、明确STALE/UPDATING、挑战/空壳、分页失败都不能算健康。120秒是拒绝明显旧响应的上限，**不是实时性SLA**；Age缺失、Age较低和Last-Modified较新都不能证明上游索引无延迟。
- RSS兜底保留消息，但物理周期、单元及UI都失败，不刷新HTTP成功时钟；无可用RSS的ACCESS同样明确失败。失败周期也保留本次`wire_observation`，避免显示上次成功的旧入口。BODY补全失败与列表发现失败继续分别统计。

## 3. 测试与生产发布

核心最终回归：116 passed / 1 skipped（可选4078条历史BW研究样本在隔离工作区未展开），另有原有Yahoo脚本转义警告。包含真实列表卡片、跨日时间、RSS/HTTP文章身份、稳定去重、304基线、分页第二页失败保留消息、缓存失败证据、取消/撤权不兜底、慢站不阻塞、配置幂等与Bloomberg/X不变。最后补齐GN RSS语言后，相关GN/Bloomberg套件40 passed。各套件有重叠，不将计数相加冒充独立用例总数。

补充Collector套件44 passed。随后旧稿混排修正24 passed；初段真实对照中，19:16 UTC的ROSEN/Millrose稿RSS于19:18:02首次可见、HTTP于19:19:02首次可见；19:17/19:18两稿两入口约同在19:20:02出现。这既否定“GET200就通过实时性验收”，也支持GN保留RSS独立并行。最终版本另做完整十分钟观察，不将这些早期样本混作最终窗口统计。

生产网络内独立进程试跑新适配器：PRN 7条、GN12条、ACCESS2条（当时30分钟窗口）；耗时约1.64/1.03/2.38秒。BW/NF仍能分别从RSS获得6/2条，**结果均有失败码**。这只证明当时能解析/保留候选，不是消息相关性或真实新稿延迟验收。

部署通过标准 `storage_release.py`，仅发布 Collector、Bus、API（后者更新UI状态映射）；正文服务不重启。`configure_newswire_http.py --apply`使用现有Service原子更新五个源的kind和discovery_mode，保留enabled、BODY参数、Ticker测试名单、terms、其他来源与正式绑定。脚本可重复执行。

## 4. 真实时效、对照结果与调试修正

**PRN：有真实改善证据，但不是所有稿件都能30秒内出现。** 初段UTC19:30发布的Crunch Fitness与Alphabet两稿，POST观测首次出现19:30:22.141，RSS19:31:21.219，普通GET19:32:21.134；POST分别早约59/119秒。生产在19:30:19.290采集、19:30:20.254接纳。19:40的Netcapital稿，POST19:40:17.135，GET19:40:46.328，RSS19:41:16.469。最后一轮又出现POST自身滞后超过一分钟的稿件，说明绕过页面缓存不等于消除发布/索引延迟。对照每30秒采样，HTTP返回延迟与列表首次可见延迟分别记录。

[Crunch Fitness样本](https://www.prnewswire.com/news-releases/crunch-fitness-franchisee-relocates-north-riverside-gym-into-an-all-new-crunch-3-0-club-302932334.html)、[Netcapital样本](https://www.prnewswire.com/news-releases/what-does-netcapital-incs-28-62-stock-drop-mean-for-investors-the-law-offices-of-howard-g-smith-outlines-securities-fraud-class-action-lead-plaintiff-deadline-302931652.html)。

**GN：公开列表不能证明比RSS更及时。** 早期ROSEN/Millrose稿RSS领先约60秒，另有同次采样均出现的稿件。因此采用独立双路发现，HTTP负责扩窗，RSS保留时效补充；不声称已消除上游分钟级发布/索引延迟。

**实际非英文坏例修复：** UTC19:41的一篇希伯来文O-I Glass稿，RSS链接百分号编码为`%D7...`，HTML为`%d7...`。此前只统一语言和摘要仍不够，两者会得到不同文章键，产生重复BODY。最后新增仅限GN域名的百分号编码大小写规范化，保留slug本身大小写及其他站URL；真实页面与RSS重抓已证明同一canonical URL、article key和material hint，复用原FULL正文（fetch_total仍1）。集成回归将HTTP/RSS/HTTP三次输入写入原Live链路，结果只有一个文章owner、input_revision仍1。历史诊断阶段的重复记录保留审计，不清库伪造成功。

03:33:51–03:43:51曾完整观察600秒，HTTP及GN并行RSS各20/20，但该窗口发现上述编码问题，**不能当作最终去重验收通过**。原数据保留在`eval/newswire-http-migration-20261107/`。最后修正提交`590919ed`并重新发布Collector后，再观察完整10分钟。

## 5. 最终生产验收

最终代码来自`origin/main`：主体`d2820f76`、分页`784af7c1`、GN并行`dbfd671d`、语言身份`51c43cf8`、百分号编码修正`590919ed`。同期其他HTTP覆盖发布已包含这些提交的相应祖先；最终Collector登记`590919ed`，Bus登记`45e0dcc5`，API登记`d2820f76`。三容器18个选定源码哈希均匹配各自登记提交；最新发现逻辑运行于Collector。正文服务仍为`942c3936`，起于UTC11月6日00:07:49，本轮未重启或修改。

最终连续窗口：**2026-11-07 03:50:45.743–04:00:45.743 北京时间**（UTC11月6日19:50:45.743–20:00:45.743）。窗口始于最后一版Collector已健康运行之后；独立对照进程也从该时刻采样，结束后退出，不新增常驻测试服务。

统计口径：按`acquisition.sqlite3`物理run去重，排除SCHEDULE和窗口右端未完成周期，10 ticker回执不算10次抓取。新稿延迟统计窗口内首次进入Live的文章URL，不要求声明的发布时间也在窗口内，避免剔除真正迟到的稿件。接纳指采集桥持久写入原Live处理，**不代表正则命中或下游发布**。分钟发布时间保留精度标识，秒数差值不是精确发布秒级SLA。

最终计数与缓存、完整性、隔离核对见下方验收结果及同目录JSON回执。

回退：通过原Service将五源`discovery_mode`设为`rss`，原端点均保留（ACCESS恢复原HTML）；无需回退BODY或测试隔离。

### 最终数值（600秒，修正后）

| 来源/任务 | 有效列表成功 | 明确失败周期 | 平均周期耗时 | 实际轮询间隔均值/最大 |
|---|---:|---:|---:|---:|
| PR Newswire HTTP | 20/20 | 0 | 1.265s | 29.998s / 30.096s |
| GlobeNewswire 并行RSS | 20/20 | 0 | 0.394s | 30.001s / 30.125s |
| GlobeNewswire HTTP | 20/20 | 0 | 0.287s | 29.996s / 30.113s |
| ACCESS HTTP | 20/20 | 0 | 0.462s | 29.996s / 30.322s |
| Business Wire RSS兜底（主爬虫受阻） | 0/20 | 20 | 2.640s | 30.005s / 30.170s |
| TMX Newsfile RSS兜底（主爬虫受阻） | 0/20 | 20 | 0.503s | 30.005s / 30.110s |

共120个物理周期：三个HTTP任务60/60成功，GN并行RSS20/20成功；BW/NF各20次保持失败状态，仍正常尝试RSS兜底。后40次不是40次新HTTP挑战请求，已确认的挑战入口在待评估期间不反复撞墙。

| 来源 | 窗口首次发现URL数 | 发布→首次采集均值/最大 | 采集→接纳均值/最大 | BODY结果 |
|---|---:|---:|---:|---|
| PR Newswire | 6 | 69.04s / 108.54s | 0.56s / 2.33s | FULL 4, PARTIAL 2 |
| GlobeNewswire | 2 | 143.18s / 167.42s | 0.23s / 0.30s | FULL 2 |
| ACCESS | 0 | 无新样本 | 无新样本 | 无新样本 |
| Business Wire | 3 | 316.88s / 417.30s | 0.12s / 0.14s | UNAVAILABLE 2, FETCH_DUE 1 |
| TMX Newsfile | 2 | 35.28s / 51.55s | 0.19s / 0.27s | FULL 2 |

这些是本次小样本，不是长期分位数或保证。最终窗口13个新文章owner无同URL重复；GN两篇是不同URL，编码问题的同篇重复未再出现。BW的FETCH_DUE受沿用的测试发布商冷却影响，UNAVAILABLE返回empty_extract；PRN两条PARTIAL返回publisher_loop。本轮没有修改这些BODY机制，结果为窗口结束后末次读取状态。没有把这些改成FULL，也没有为提速删掉正文质量检查。最终窗口正式Raw、Standard、真实订阅、Decision Model、五源测试发布数均0；测试发布0不能解释为没有抓到消息，捕获/接纳与正则命中分开统计。

### 缓存与真实时效结论

- **PRN：采用POST有收益，但未消除全部上游延迟。** 最终配对5篇，POST平均比RSS早47.409秒、比GET早35.209秒；4篇明显提前，1篇在5秒内基本同时。生产首次采集6篇平均69.036秒、最大108.541秒，接纳平均69.594秒。早期18–20秒的样本不能冒充最终全部样本。POST无Age且CF标记DYNAMIC、Last-Modified贴近当前响应，但仍可能遇到上游发布/索引延迟。
- **GN：HTTP扩窗，RSS保留时效补充。** 最终同篇配对1例，RSS又比HTTP早29.427秒。HTTP物理周期平均0.287秒不代表新闻实时：最终两篇发布→采集平均143.179秒。未验收其上游分钟级延迟已消除；两路径独立、不互等。窗口前后真实希伯来文样本身份一致，既有FULL owner的fetch_total仍1。
- **ACCESS：可稳定读列表，但缓存风险保留。** 独立对照Age平均52.81秒、最高115秒，允许grace/stale的缓存头仍在；最终窗口无新稿，不能验收发布→发现延迟改善。前一诊断窗口一篇约90秒，单独保留，不混入最终统计。未验证出可用的绕缓存API/POST，不用随机参数虚称解决。
- **BW/NF：未完成HTTP替代。** BW兜底3篇平均约317秒，且正文仍受阻，值得优先评估现有身份浏览器/获准数据接口；NF兜底2篇平均约35秒，引入Chrome的收益需要权衡。两者都没有被标成HTTP健康；若后续选择浏览器，须另做实测，不能假定Chrome必然可解。

### 隔离、版本与资源

四项静态控制哈希全部不变：正式订阅323条、监测词修订40条、其他103个源定义、默认策略1条。五源版本仍2、enabled且保持原10 ticker测试目标。源码18/18回读匹配；最终进程与窗口开始时相同，未重建或异常重启。

没有新增Chrome身份、浏览器进程、常驻测试服务或表，六个物理任务少于原七个，沿用原测试HTTP2/BODY1预算。观测进程已退出。末尾全进程快照：Collector479.2MiB、Bus158.2MiB、API497.6MiB、BODY353.7MiB；包含所有现有业务，不能归因为本轮的增量，CPU瞬时值也不作为新增源成本估计。

冻结回执：[最终验收JSON](newswire-http-discovery-acceptance-20261107.json)。原始请求/响应头、抓取与接纳记录、身份对照、生产读回和调试片段在`eval/newswire-http-migration-20261107/`；调试窗口与最终600秒窗口分开保留。
