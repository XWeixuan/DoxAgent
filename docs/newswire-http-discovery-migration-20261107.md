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

开发回归：114 passed / 1 skipped（可选4078条历史BW研究样本在隔离工作区未展开），另有原有Yahoo脚本转义警告。包含真实列表卡片、跨日时间、RSS/HTTP文章身份、稳定去重、304基线、分页第二页失败保留消息、缓存失败证据、取消/撤权不兜底、慢站不阻塞、配置幂等与Bloomberg/X不变。补充Collector回归及最终计数见后续验收记录。

补充Collector套件44 passed。随后旧稿混排修正24 passed；初段真实对照中，19:16 UTC的ROSEN/Millrose稿RSS于19:18:02首次可见、HTTP于19:19:02首次可见；19:17/19:18两稿两入口约同在19:20:02出现。这既否定“GET200就通过实时性验收”，也支持GN保留RSS独立并行。最终版本另做完整十分钟观察，不将这些早期样本混作最终窗口统计。

生产网络内独立进程试跑新适配器：PRN 7条、GN12条、ACCESS2条（当时30分钟窗口）；耗时约1.64/1.03/2.38秒。BW/NF仍能分别从RSS获得6/2条，**结果均有失败码**。这只证明当时能解析/保留候选，不是消息相关性或真实新稿延迟验收。

部署通过标准 `storage_release.py`，仅发布 Collector、Bus、API（后者更新UI状态映射）；正文服务不重启。`configure_newswire_http.py --apply`使用现有Service原子更新五个源的kind和discovery_mode，保留enabled、BODY参数、Ticker测试名单、terms、其他来源与正式绑定。脚本可重复执行。

## 4. 十分钟生产观察

待最终生产发布后填写实际完整窗口。必须分开报告：HTTP可用性、RSS兜底失败、真实新稿样本的发布→入口首次出现→生产首次采集/接纳，以及没有足够样本的未验证部分。不会把页面200、旧稿重新出现或10 ticker回执数冒充实时性改善。

可回退：同一Service将五源 `discovery_mode` 改为 `rss`；原RSS端点保留，ACCESS仍是原HTML解析。测试隔离和BODY无需回退。
