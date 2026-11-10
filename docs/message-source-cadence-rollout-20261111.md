# 消息源首轮提频与 legacy 下线验收（2026-11-11）

用户覆盖口径：Yahoo 维持 30 秒，IBKR 20 秒；删除 TikHub Search/User Posts 与 GlobeNewswire 半导体 RSS 并解绑全部 ticker。其余沿两轮评估的候选/例外档位执行。

## 已执行

- 首版 main `03f44d4c`，最终 Collector `c232f094`；两次标准 exact-SHA 仅发布 Collector。Site Access、Supervisor、API、Message Bus、Enrichment 容器未重建，Chrome/Profile/出口未调整，routine BODY recheck=false 保留。
- 修改 69 个 Source 的配置、235 个订阅间隔，并同步默认 Profile。没有重启/启用停用 ticker 或新增订阅。
- 健康 HTTP/RSS 20 秒；Benzinga 15、Finnhub 30、SEC 10、IBKR 20、Yahoo 30；Investing/InvestorsHub/SDxCentral 30。诊断例外保留独立档位，未将所有源无差别降频。
- IR 的 BE press / MU IR 保留 30 秒，其余已评估入口 20 秒；Workday 独立 30 秒、同组四个入口 20 秒。
- 最终 Collector 同时包含另一会话已合并 main 的 bounded storage maintenance 代码；首窗与最终窗分开，不能将所有指标变化归因于 IR 修正。
- 修正 sub-minute fresh-query bucket；不能轮询改成 20 秒、缓存键仍每分钟不变。保留现有缓存新鲜度校验。
- 删除 TikHub 两源与 GlobeNewswire 半导体 RSS：MU 的 TikHub User Posts 订阅 1 个；半导体 RSS 的 BE/COHR/INTC/MU/NBIS 订阅 5 个；Search 无订阅。默认 Profile 无引用。半导体 RSS Scope 已通过 Owner CAS 从 rev1 停用至 rev2。历史消息和 Profile 未删除；种子与订阅脚本不再重新创建 legacy 源。
- 回归：public-listing 与本轮新测试 83 passed；最终 Collection Plane 与本轮测试 26 passed（部分重叠）。生产配置 dry-run 再次执行无待变更项。配置回滚快照保存在服务器 root 私有 release 目录。

## 15 分钟自然观察

- 最终版本观察窗口：UTC 2026-11-10T19:54:30+00:00—2026-11-10T20:09:30+00:00 / 北京时间 2026-11-11 03:54:30—2026-11-11 04:09:30；覆盖 15.00 分钟。每约 5 分钟只读累计回执，不额外发新闻网站/Provider 探测。
- 首窗 03:31:30—03:46:30 已完整观察 15 分钟，12035 次成功/113 次失败，发现 MU IR 的旧 binding 覆盖导致 20 秒；已通过既有 unit_interval 修复 30 秒下限，最终窗口重新观察完整 15 分钟。首窗 JSON/报告保留，不当作最终版本验收。
- 对照窗口：UTC 19:14:43—19:29:43 / 北京时间 03:14:43—03:29:43。这是首版发布前对照，之后另有一轮观察与窄修复，不能将它冒充连续无变更 A/B 实验。对照的资源和 current-units 是读取时现状，仅周期历史用于前后比较。
- 观察到 11,198 次去重采集成功、253 次失败、0 次延期，截止时 224 次在途。在途不算失败；截止之后才终态完成的任务也按截止时在途处理。周期成功不等于新增文章数量；当前 required-unit 配置检查为读取时点的投影。
- 当前 348 个受调整的 required binding×unit，间隔与目标不匹配 0 个。共享源分发不重复计数；CAPTURED 是采集周期回执，不等同独立网页请求或新增文章。部分周期可先有有效页面，随后附带终态错误，因此 reason 数量不必等于纯 FAILED 数量。

| 范围 | 成功 | 失败/延期 | 实际成功间隔 P50 / P95（秒） |
|---|---:|---:|---:|
| benzinga_news | 399 | 19 | 16.454 / 52.828 |
| finnhub_company_news | 290 | 0 | 29.7 / 35.563 |
| ibkr_news | 445 | 0 | 20.013 / 25.412 |
| sec_edgar | 521 | 3 | 13.687 / 35.078 |
| yahoo_finance_news | 883 | 5 | 30.005 / 30.87 |
| investing_ticker_news | 294 | 1 | 30.006 / 30.844 |
| investorshub_ticker_news | 289 | 6 | 30.016 / 34.247 |
| sdxcentral_news | 27 | 2 | 30.088 / 60.141 |
| reuters_site_search | 643 | 14 | 60.031 / 73.904 |
| enterprise_apps_official_rss | 198 | 4 | 20.028 / 32.178 |
| barrons_ticker_news | 842 | 36 | 19.807 / 25.889 |
| globenewswire_search | 1214 | 0 | 19.997 / 20.967 |

## 仍存在的失败与边界

| 范围 | 发布前成功/失败 | 本窗成功/失败 | 原因 |
|---|---:|---:|---|
| army_news_releases | 15/0 | 42/2 | {"RuntimeError": 2} |
| army_news_rss | 15/0 | 43/1 | {"RuntimeError": 1} |
| aviationweek_rocketlab | 15/0 | 40/4 | {"UNPROVEN_LISTING": 3, "RuntimeError": 1} |
| aviationweek_rss | 75/0 | 72/1 | {"HTTP_LISTING_FAILED_RSS_FALLBACK": 1} |
| barrons_ticker_news | 299/0 | 842/36 | {"SiteAccessError": 17, "site_queue_timeout": 16, "UNPROVEN_LISTING": 3} |
| benzinga_news | 150/0 | 399/19 | {"TimeoutError": 21} |
| calcalist_search | 0/60 | 31/25 | {"RuntimeError": 25} |
| canary_media | 15/0 | 36/7 | {"UNPROVEN_LISTING": 6, "HTTP_LISTING_FAILED_RSS_FALLBACK": 1} |
| corporate_ir_press_releases|IR:mu.newsroom | 28/2 | 44/1 | {"TimeoutError": 1} |
| corporate_ir_press_releases|IR:nbis.press | 30/0 | 43/1 | {"TimeoutError": 1} |
| corporate_ir_press_releases|IR:onds.press | 30/0 | 41/4 | {"ConnectTimeout": 3, "TimeoutError": 1} |
| defensescoop_drones | 30/0 | 84/2 | {"HTTP_LISTING_FAILED_RSS_FALLBACK": 2} |
| defensescoop_space | 14/1 | 42/2 | {"UNPROVEN_LISTING": 2} |
| digitimes_tw_rss | 15/0 | 43/1 | {"TimeoutError": 1} |
| enterprise_apps_official_rss | 75/0 | 198/4 | {"HTTP_LISTING_FAILED_RSS_FALLBACK": 4} |
| investing_ticker_news | 149/0 | 294/1 | {"site_queue_timeout": 1} |
| investorshub_ticker_news | 149/1 | 289/6 | {"site_queue_timeout": 6} |
| nasa_2026_releases | 15/0 | 42/2 | {"RuntimeError": 2} |
| nbis_official_actor_rss | 30/0 | 84/1 | {"HTTP_RSS_BOTH_FAILED": 1} |
| nrc_event_notifications | 0/15 | 0/44 | {"publisher_endpoint_backoff": 44} |
| power_magazine_energy | 135/0 | 285/20 | {"HTTP_RSS_BOTH_FAILED": 20} |
| quantum_zeitgeist | 15/0 | 12/2 | {"HTTP_LISTING_FAILED_RSS_FALLBACK": 2} |
| reuters_site_search | 662/5 | 643/14 | {"search_query_failed": 4, "TimeoutError": 1, "identity_control_busy": 8, "site_queue_timeout": 1} |
| sdxcentral_news | 15/0 | 27/2 | {"publisher_endpoint_backoff": 2} |
| sec_edgar | 284/0 | 521/3 | {"TimeoutError": 3} |
| shephard_uncrewed | 15/0 | 40/4 | {"DEFENSE_ENDPOINT_FAILED": 2, "RuntimeError": 2} |
| spacenews_rss | 12/3 | 11/4 | {"publisher_http_429": 4} |
| ssc_newsroom | 15/0 | 43/1 | {"RuntimeError": 1} |
| technews_hardware | 29/1 | 68/14 | {"HTTP_LISTING_FAILED_RSS_FALLBACK": 14} |
| thefly_ticker_news | 132/2 | 247/17 | {"TimeoutError": 16, "SSLError": 1} |
| tomshardware_rss | 15/0 | 43/1 | {"TimeoutError": 1} |
| unmannedairspace_counteruas | 30/0 | 86/2 | {"HTTP_RSS_BOTH_FAILED": 2} |
| yahoo_finance_news | 842/10 | 883/5 | {"browser_coverage_pending": 2, "RuntimeError": 2, "RequestError": 1} |

- NRC Event Notifications 的 endpoint_backoff 在调整前已 15/15 失败，不能归因于本轮提频。SpaceNews 的 429、Calcalist 的间歇失败仍属于保留档位的既有问题。它们需要独立修复，不靠配置秒数制造“恢复”。
- Benzinga 的部分 45 秒 TimeoutError 没有对应请求事件；首窗抽查的 POWER 失败也无物理请求回执。正常响应网络耗时并未同步增长，需后续定位预发送准入/等待以及可能的追踪缺口，不能仅凭 TimeoutError 断言供应商慢或限流。Benzinga 15 秒配置生效，不代表实际所有 ticker 都稳定每 15 秒成功；SEC 10 秒也有周期长尾。
- InvestorsHub/Investing/Reuters 的控制占用或队列延期如实保留，不以最新一次成功抹掉窗口失败。Reuters 保留 60 秒，本窗口表现应按表中数据判断，不能沿用上一轮“只有 Backup”的旧快照。
- 新人工任务：[]；恢复事件：[]；新 Solver jobs：[]。不将任务更新或已有 job 状态变化算作新增购买。
- 网站维度自然 BODY 回执：{"FULL": 35, "UNAVAILABLE": 11, "PARTIAL": 8}。不等同所有新文章正文均完成，也没有额外 BODY canary。
- 资源：磁盘可用 54.40 GiB；acquisition.sqlite3 13.18 GiB。资源样本、WAL、PSI 与容器重启/OOM 状态详见 0/5/10/15 分钟 JSON。15 分钟不能证明长期增长可控或不再触发风控。
- 本轮完成配置落地与 15 分钟观察；没有继续无限续测或因个别失败扩大重构。后续优先处理内部排队/预发送可观测性及既有失败源，再决定是否进一步降低间隔。

完整逐源结果：`eval/polling_cadence_rollout_20261111/per-source-acceptance.csv`；汇总 `acceptance-summary.json`；生产 release/hash、配置 apply/idempotent、scope 停用与各时间点快照均在同目录。

最终结论：配置/解绑/运行版本验收通过，整批时效稳定性仅部分通过。实际成功间隔及长尾见 CSV，特别是 Benzinga 与 SEC 未稳定兑现 15s/10s；不能把配置值当作真实成功周期。观察已结束，不安排继续守候。
