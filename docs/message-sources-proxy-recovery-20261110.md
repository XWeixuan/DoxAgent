# 消息源集中失败：根因、修复与验收

2026-11-10，北京时间。依据本轮生产基线、同容器传输对照与自然业务回执；不是仅按错误名称归因。

## 根因与执行

### 1. 主故障是共享订阅额度耗尽，并非各站点同时发生 Challenge

故障时，Collector 中独立进程的 HTTPX/curl 对服务器直连和 server-direct 都返回 200，而 NL/DE 固定代理分别报 ConnectError / curl 35 TLS connect error。用户随后确认 Clash 订阅额度用尽。额度重置后，用相同容器、库、代理地址及原节点重测，两种库全部返回 200，实际出口仍为原 IP。无需向远端同步新订阅。

程序的失败机制有两层：

- HTTP、RSS 与行业适配器仍经固定代理进行真实请求，底层 TLS 失败被包装为 SSLError、RuntimeError、HTTP_RSS_BOTH_FAILED 或 publisher_endpoint_backoff。
- Site Access 的探测连续失败，把非直连出口置为 UNAVAILABLE，候选准入排除对应主/备身份。此时 access_combinations_unavailable / SiteAccessError 通常表示没有可准入出口，不等于已请求网站并遇到 Challenge。

五个代理槽位虽有多个 IP，但共享同一额度故障域。Browser Identity 隔离无法提供订阅供应商级冗余。既有出口探测连续失败后最多隔 900 秒重试，所以底层恢复后，投影状态未必瞬间转绿。这些保护在实际断网时有意义，本轮没有盲目移除。

已通过现有 Site Access 管理 API 实际 probe 六个固定出口（含 server-direct），全部 READY，失败次数归零；所有 generation 不变。没有手工伪造健康、重置 Cookie、换出口、重新建 Profile 或改变身份映射。后续只让自然采集覆盖旧错误，没有手工修改业务状态。

IBKR 的 SDK/Gateway 链路与这些固定代理不是同一机制：本轮最新自然轮询已恢复，但没有据此断言此前所有 RuntimeError 都由额度引起；未修改 Gateway 或交易组件。

### 2. 独立残留：AviationWeek 的 CDN 缓存周期性失效

额度恢复后，该源仍在成功与失败间切换。错误证据为 `public_listing_stale_cache_age:239.0`，RSS fallback 另有真实 403。只加 no-cache 头无法解决：同一主题、同一已批准代理的对照得到：

| 请求 | HTTP | Age | 缓存 | 解析文章 |
| --- | --- | --- | --- | --- |
| 原 URL + no-cache | 200 | 321 秒 | HIT | 25 |
| 增加既有刷新参数 | 200 | 0 秒 | MISS | 25 |

只在 `public_listing.py` 为五个既有 AviationWeek topic 开启 `fresh_query=True`，沿用框架的一分钟桶参数。120 秒新鲜度门槛、每轮请求数量、主题范围、RSS fallback 的失败表达保持；checkpoint 仍以原 recipe URL 为键。不是忽略旧缓存或提高新闻可接受年龄。

新增回归验证跨分钟仍延续 checkpoint/ETag 且同文章不重复召回，以及服务端仍返回 Age=321 时继续失败。相关 public-listing 整组 **79 passed**，Ruff 严重错误及 diff 检查通过。

## 生产发布

源码 `9f2ac400c29cd893b71ee2d5edd388b7577eef6f` 已推送 origin/main，使用现有标准精确 SHA 发布，仅重建 Collector。

- 完成时间：UTC 11-09 23:41:21 / 北京时间 11-10 07:41:21。
- Collector：`5279d4b57b96 → 1e15344dbc0a`，healthy，应用源码哈希与提交一致。
- Site Access、Chrome Supervisor、API、Message Bus、Enrichment 容器与启动时间均未变化；保留 Chrome 现场与 Profile。
- 普通 recheck 开关继续为 false；未新增普通复查、历史补发、模型或打码测试请求。

## 业务边界审查

没有调整正式或测试订阅、监测词、相关性、交易/研究调度、24 小时采集安排、现有目标间隔、30 分钟入场和闭市 sweep、正文订阅权限或免费文章规则。采集成功、正文完整和最终发布分别验收，不能互相替代。

额度故障期间已错过时效窗口的旧文章不会因本轮恢复被改成新消息重发；关闭的普通 recheck 也没有为回填历史而重新开启。正文回执只能证明对应文章结果，不代表全部历史缺口已补齐。

## 剩余问题与验收口径

Reuters 有独立于额度故障的主身份验证阻塞，恢复记录为 HUMAN/BACKOFF、`provider_result_still_blocked_manual_review`，原页 PRESENT；本轮保留现场，没有清状态或额外触发付费打码。备用仍有真实搜索成功，但诊断时一个身份正在执行、八个同身份排队，Site 全局只占 1/8 槽，存储 ready、无 stalled 操作。由此排除“全部站点共用的锁死”；当前 Reuters 单身份容量/恢复节奏与积压需求不匹配，queue timeout 不能称为 CAPTCHA 失败，也不能用继续重启或换 IP 解决。

自然窗口还会有少量 429、超时或未证明列表，不能用最终绿色状态把窗口内失败抹掉。本轮目标为恢复大部分源，不追加 Reuters 大重构或放宽新鲜度/并发/保护期来清零。主身份人工通过后应复核双活实际准入及 QUERY/TAIL 完整覆盖；如仍排队，再单独依据队列和实际执行耗时调优，不主动降低业务目标频率。

下面由最终验收快照追加整体数值、原失败源逐项对照及剩余失败。


## 最终自然业务验收

UTC 2026-11-09 23:41:22—23:46:25（北京时间 11-10 07:41:22—07:46:25），约 5 分钟；只查询既有持久回执，没有额外主动网站/Provider 探测。

- 基线 34 个持续失败 Source 中，33 个的全部当前 required 单元已成功；剩余 Reuters 部分失败。
- 当前 83 个有 required 单元的 Source 均有窗口内自然 CAPTURED；最新快照 82 个全部单元成功。分列为正式 76/77、测试 6/6；缺省 delivery_mode 按现有模型默认 production 解释。
- AviationWeek 五个入口合计 26 个有效物理采集 run，失败 0；其余共享 Source 的回执按 physical_run_id 去重，不把多 ticker 扇出当多次请求。
- Reuters 窗口内 95 个 CAPTURED、67 个 site_queue_timeout、8 个尚 DISPATCHED；末态 29 个 required 单元中 14 个成功、15 个排队失败。这里不是全 QUERY/TAIL 覆盖通过，也不代表已经稳定。
- BODY 窗口得到 FULL 7 条（generic 6、Investing 1）；另有 empty_extract 3、transport_timeout 1、Yahoo PARTIAL/transport_timeout 1，保留真实失败。额度恢复后的较早观察还见 Yahoo、HuggingNews、TrendForce、工商时报 FULL/SHORT_FULL；不宣称每个 Source 的全部文章正文已恢复。
- 其余窗口内短暂失败：Access Newswire、NSF、SSC、SpaceNews、Yahoo 各 1 个失败物理 run；最终当前单元均恢复。SpaceNews 是真实 429，其余需保留本轮瞬时网络/列表失败，不把最新绿色当作全窗零故障。

### 原 34 个失败 Source 逐项对照

下表“当前成功”是窗口末尾 required 单元状态；后两列是窗口内去重 run，不等于文章/网络请求/发布数量。

| Source | 当前成功单元 / 当前单元 | CAPTURED run | 失败 run | 剩余错误 |
| --- | --- | --- | --- | --- |
| access_newswire_massive | 10/10 | 9 | 1 | 无 |
| army_news_releases | 1/1 | 5 | 0 | 无 |
| army_news_rss | 1/1 | 5 | 0 | 无 |
| aviationweek_rocketlab | 1/1 | 5 | 0 | 无 |
| aviationweek_rss | 5/5 | 26 | 0 | 无 |
| barrons_ticker_news | 20/20 | 101 | 0 | 无 |
| breakingdefense_space | 1/1 | 5 | 0 | 无 |
| calcalist_search | 3/3 | 19 | 0 | 无 |
| crn_cloud_security_datacenter | 3/3 | 15 | 0 | 无 |
| ctee_semiconductor | 2/2 | 5 | 0 | 无 |
| darpa_news | 5/5 | 5 | 0 | 无 |
| data_center_frontier | 6/6 | 5 | 0 | 无 |
| dcd_energy_ticker | 2/2 | 10 | 0 | 无 |
| dcd_infrastructure_rss | 6/6 | 5 | 0 | 无 |
| digitimes_semiconductors | 5/5 | 5 | 0 | 无 |
| dod_releases | 2/2 | 5 | 0 | 无 |
| globenewswire_massive | 20/20 | 19 | 0 | 无 |
| huggingnews_rss | 5/5 | 5 | 0 | 无 |
| investing_ticker_news | 10/10 | 51 | 0 | 无 |
| investorshub_ticker_news | 10/10 | 49 | 0 | 无 |
| nro_press_releases | 1/1 | 5 | 0 | 无 |
| nsf_news | 1/1 | 4 | 1 | 无 |
| power_engineering_energy | 7/7 | 31 | 0 | 无 |
| power_magazine_energy | 9/9 | 46 | 0 | 无 |
| quantum_insider | 3/3 | 15 | 0 | 无 |
| quantum_zeitgeist | 1/1 | 5 | 0 | 无 |
| register_software_cloud_security | 3/3 | 15 | 0 | 无 |
| reuters_site_search | 14/29 | 95 | 67 | site_queue_timeout |
| sdxcentral_news | 5/5 | 5 | 0 | 无 |
| spacecom_launches | 1/1 | 5 | 0 | 无 |
| ssc_newsroom | 1/1 | 4 | 1 | 无 |
| thefly_ticker_news | 9/9 | 45 | 0 | 无 |
| wccftech_hardware | 15/15 | 15 | 0 | 无 |
| yahoo_finance_news | 30/30 | 286 | 1 | 无 |

### 资源与证据

生产尾样剩余约 32.9 GiB；Collector 约 317 MiB、Bus 164 MiB、Enrichment 322 MiB、Site Access 3.39 GiB、Supervisor 4.95 GiB，均在各自限制内，相关容器无 OOM/自动重启。CPU/IO 是短时样本，不外推为长期分位或将其他会话的资源修复归功本轮。未清理业务/历史数据、未新增常驻服务。

公开证据：`eval/message_sources_repair_20261110/acceptance-summary.json`、`release.json`、`egress-verification.json`、`aviation-comparison.json`、`listing-regression.xml`。较大原始审计 JSON 仅留本地 eval，不把全量历史/页面内容提交仓库。
