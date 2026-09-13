# Message Bus V2 四路新闻源可行性评估与实施方案

日期：2026-09-14  
评估基线：`main@44a955b718c2`，工作树在评估开始及完成前均无未提交改动  
范围：Yahoo Finance、IBKR TWS News、Reuters Site Search、Google News Search RSS 进入 Message Bus V2  
交付性质：深度调研、真实只读探针与开发方案；本轮不修改运行代码、不部署

## 1. 执行结论

四个来源可以共用现有 Message Bus V2 的 Raw、去重、正文补全、StandardMessage 与 ticker stream 链路，但不能用同一种采集实现：

| 来源 | 技术结论 | 正确接入形态 | 默认策略 |
| --- | --- | --- | --- |
| Yahoo Finance | 可行，`latestNews` NCP 与页面 News tab 最接近；Query1/2 搜索只适合降级 | 普通 60 秒 API adapter | 新 ticker 默认绑定并启用 |
| IBKR | 可行，但 292 是持续订阅，不是 60 秒短轮询；历史接口只适合断线补漏 | 常驻 TWS gateway + durable inbox + 60 秒 drain/reconcile adapter | 新 ticker 默认绑定并启用；TWS/权限缺失时只降级，不阻塞初始化 |
| Reuters | 浏览器页可行，直接 HTTP 当前为 401；必须用浏览器型 Crawler Plane，按 20 条分页 | 内置 CRAWLER-kind adapter 复用共享 browser runtime + 60 秒触发 | 新 ticker 默认绑定并启用；浏览器会话不可用时降级 |
| Google News RSS | 可行，但域名约束只是召回提示；Google 包装链接不能直接交给正文补全 | 普通 RSS adapter + 发布方域名硬过滤 + canonical URL resolver | 注册但默认不绑定；由 O4 使用 O2/D1 上下文生成参数后显式启用 |

最关键的架构修正有三项：

1. **不能每分钟重连 IBKR 292。** `reqMktData(..., "mdoff,292:<providers>", false, ...)` 是 streaming subscription；应常驻连接，60 秒只做订阅对账、健康检查和 inbox 排空。
2. **“由 O2 决定 Google 参数”不能解释为 O2 直接修改 Message Bus。** 当前 ticker 初始化先准备候选 Message Bus 配置，O4 才是监测源配置的授权节点。O2/D1 提供冻结的公司主体上下文，O4 负责把它编译成 Google `search_terms`/`domains` 并写入 candidate binding。
3. **四路都只接现有正文补全中台。** adapter 只负责发现、规范化、URL 解析和入口策略；不得在各 adapter 内另写正文抓取。IBKR 的 `reqNewsArticle` 只作为 IBKR 原生正文获取步骤，输出仍进入同一个 enrichment intake，而不是建立第二条补全管线。

## 2. 现有实现审计

### 2.1 可直接复用的边界

- `SourceAdapter` 已固定为 `poll(PollContext) -> PollResult`，支持 checkpoint、`COMPLETE/PARTIAL/UNKNOWN` 窗口覆盖与 item-local failure。
- `MessageBusV2Scheduler` 在每次 poll 前冻结 source/binding snapshot，并将 adapter 结果交给 `MessageBusV2Service.accept_poll_result()`。
- `accept_poll_result()` 已把默认 `ENRICH` 来源写入 durable `content_enrichment_jobs`；因此新增来源只要输出 `RawMessageInput` 即可进入统一正文补全。
- `initial_default_profile()` 当前默认间隔已经是 60 秒；ticker 初始化的 `CandidateConfiguration.prepare()` 会先 bootstrap，再 materialize 默认绑定。
- bootstrap 的首批历史数据可写入 Raw/enrichment intake，但按现有语义抑制首次 stream publication，适合“首次最多回看 24 小时、之后只发增量”。

### 2.2 必须改的接缝

- `AdapterRegistry` 目前只注册 Benzinga、Finnhub、Stocktwits、TikHub 与通用 RSS，没有本轮四个 adapter。
- `initial_sources()` 和 `initial_default_profile()` 尚无 Yahoo/IBKR/Reuters/Google。
- `MessageBusV2Service.bootstrap()` 里有 `{"benzinga_news", "finnhub_company_news"}` 的旧默认源集合，新增默认源时必须一起迁移，不能只改 manifest。
- 当前 RSS parser 没有保留 Google `<source url>`，也没有发布方域名硬过滤和 Google wrapper canonical URL 解析。
- 当前 adapter 协议是短生命周期 poll，不能承载 TWS socket 的常驻 reader loop。

## 3. 真实探针结果

探针时间为 2026-09-14（Asia/Shanghai），样本 ticker 为 `MU`；所有操作均为只读。

### 3.1 Yahoo Finance

浏览器中的 `https://finance.yahoo.com/quote/MU/latest-news/` 可正常显示 ticker News tab。当前 yfinance 源码也明确使用：

```text
POST https://finance.yahoo.com/xhr/ncp?queryRef=latestNews&serviceKey=ncp_fin
{"serviceConfig":{"snippetCount":N,"s":["MU"]}}
```

实测：

| 路径 | 请求数 | 返回数 | 本地过滤后 24h | 观察 |
| --- | ---: | ---: | ---: | --- |
| NCP `latestNews` | 10 | 10 | 6 | 与页面最新新闻一致性最好 |
| NCP `latestNews` | 100 | 100 | 6 | 可覆盖 24h，最早到 2026-09-08 |
| NCP `latestNews` | 250 | 200 | 6 | 实际有效上限 200 |
| Query1 `/v1/finance/search` | 100 | 10 | 6 | 硬限制约 10 条 |
| Query2 `/v1/finance/search` | 100 | 10 | 6 | 同样只回 10 条 |

NCP 返回的 article content 包含稳定 id、标题、摘要、`pubDate`、provider、`canonicalUrl`/`clickThroughUrl` 与 finance metadata。结论是：**NCP 为主，Query1/2 仅在 NCP 结构变化或失败时做降级探针；不能把 Query 搜索的 10 条称为完整覆盖。** 当前 yfinance 实现可作为结构变化的上游哨兵，而不是直接把 yfinance 对象塞进 Message Bus。

### 3.2 IBKR TWS News

本次实时检查 `127.0.0.1` 的 7496、7497、4001、4002 均未监听，因此无法在本轮重新建立 TWS 会话。最近一次真实只读验收（2026-09-13）使用 `ibapi 10.49.2`：

- `reqNewsProviders()` 返回 8 个 API provider：`BRFG`、`BRFUPDN`、`DJ-N`、`DJ-RT`、`DJ-RTA`、`DJ-RTE`、`DJ-RTG`、`DJNL`；
- MU 解析为 `conId=9939`；
- `reqHistoricalNews()` 单次返回最新 300 条且 `hasMore=true`，实际忽略请求时间边界；本地过滤七天得到 36 条唯一文章；
- 运行时 `NewsProvider` 字段为 `code/name`，error callback 需要兼容新版可选 `errorTime`。

IBKR 官方示例证明 ticker-specific News 需要 generic tick 292 和 provider code，例如 `mdoff,292:BRFG+DJNL`；无 API 新闻订阅会返回 invalid tick type。`tickNews` 给出 providerCode、articleId、headline 与时间戳，再用 `reqNewsArticle` 取正文。这里的“全部已订阅源”应定义为：

```text
reqNewsProviders()
  -> 取得 API 可见 providers
  -> 对目标合约发起一个合并后的 292 provider list
  -> 逐 provider 记录 entitlement rejection
  -> accepted providers 构成 effective_provider_set
```

不能把 TWS UI 中可见的栏目当作 API entitlement，也不能把 `reqHistoricalNews()` 返回 300 条当作指定窗口完整。

### 3.3 Reuters Site Search

同一个 URL 的两种访问形态差异明显：

- 直接 HTTP GET 当前返回 `401`、771 bytes、无文章结果；
- 用户现有 Chrome 会话可正常渲染，`micron` 显示 906 个结果，排序为 Newest，每页 20 条，`offset=0` 对应 1–20，并有 Next stories；页面第一批最新日期为 2026-09-11，因此当前 24h 窗口为 0 条。

这排除了纯 `httpx + BeautifulSoup` adapter。正确实现是复用 Crawler Plane 的 browser runtime：等待结果列表出现，读取 `date/title/url/category`，按 `offset += 20` 翻页。停止条件必须同时满足任一项：

- 当前页最旧的可解析搜索结果日期早于“前一天”；
- Next disabled/不存在；
- 结果签名重复；
- 达到安全页数上限（建议 10 页），此时 coverage=`PARTIAL`。

按补充需求，Reuters 不再解析文章页精确时间，也不执行严格滚动 24 小时过滤。搜索结果页只保留 `requested_at` 所在自然日和前一自然日的日期；为满足统一消息契约，`published_at` 使用该日期 UTC 12:00 的确定性占位，并显式写入 `publication_time_precision=day` 与原始搜索日期。正文仍统一交给 enrichment hub。

### 3.4 Google News Search RSS

实测 endpoint：

```text
https://news.google.com/rss/search?q=<query>&hl=en-US&gl=US&ceid=US:en
```

结果：

| query | 返回 | 24h 内 | 结论 |
| --- | ---: | ---: | --- |
| `micron when:1d` | 25 | 25 | RSS 可用 |
| `micron site:reuters.com when:1d` | 0 | 0 | domain query 可能无召回 |
| `micron (site:reuters.com OR site:bloomberg.com) when:1d` | 1 | 1 | OR 可用但不能保证完整/精确 |

feed 没有 ETag/Last-Modified，响应为 `no-cache, no-store`。每条 item 有 title、pubDate、Google wrapper link、description HTML 和 `<source url="publisher-origin">publisher name</source>`。现代 wrapper link 请求后仍停留在 `news.google.com/rss/articles/...`，返回的大段 HTML 里没有可直接抽取的 publisher URL，因此 **不能把 wrapper URL直接交给现有正文补全**。

继续用当前 RSS 第一条做真实解码：从 article page 取得签名、时间戳与 article id，再调用 Google 内部 `Fbv4je` batchexecute，成功得到：

```text
https://finance.yahoo.com/markets/stocks/articles/micron-technology-stock-soar-1-163500139.html
```

这证明 canonical URL 可以在入统一正文补全前恢复。验证使用了 `googlenewsdecoder 0.1.7`，但正式实现不建议直接引入该同步依赖栈；应把必要协议封装为项目内异步 resolver，复用现有 `httpx`，并用当前 payload 做 fixture。

必须增加一个独立的 `GoogleNewsCanonicalUrlResolver`，但它只做 URL 规范化，不做正文提取：

1. 优先从 feed payload/已支持的旧 token 格式解析；
2. 现代 token 从 article page 读取 `data-n-a-sg`、`data-n-a-ts`、`data-n-a-id`，再 POST `/_/DotsSplashUi/data/batchexecute`，固定 `rpcid=Fbv4je`；
3. 解析失败时仍允许 headline-only Raw 进入 enrichment fallback，但标记 `canonical_url_status=UNRESOLVED`，不得伪造发布方 URL；
4. 不把 Google wrapper 的大页当正文。

`site:` 只是召回提示，Google 官方也说明它不保证返回所有已索引 URL。因此 domains 必须执行两层约束：query 中拼 `site:` 以减少噪声，返回后再按 `<source url>` 的规范化 registrable domain 做硬 allowlist。

## 4. 最终数据契约

### 4.1 统一 24 小时窗口

每次 poll 使用：

```text
window_end   = context.requested_at
window_start = window_end - 24h
accept       = window_start <= published_at <= window_end + 5m_clock_skew
```

- provider 的 `when:1d`、query date、historical start/end 都只是请求优化；本地时间过滤是最终真值。
- 第一次 poll 拉 24h，写 Raw/enrichment intake，维持现有 bootstrap suppression。
- 后续 poll 仍可请求 24h 窗口，以 provider article id/canonical URL + published timestamp 做幂等；这比依赖不可靠的 provider cursor 更稳。
- 只有真实可继续的 provider cursor 才分页；不能通过扩大到 30 天假装分页。
- `COMPLETE` 仅在已越过窗口下界且没有 cap/重复页/解析异常时使用；否则 `PARTIAL` 或 `UNKNOWN`。

### 4.2 Source 参数

建议 source ids 与参数：

```json
{
  "yahoo_finance_news": {
    "snippet_count": 100,
    "query_ref": "latestNews"
  },
  "ibkr_tws_news": {
    "provider_mode": "all_api_entitled",
    "historical_catchup": true
  },
  "reuters_site_search": {
    "company_short_name": "Micron",
    "max_pages": 10
  },
  "google_news_search_rss": {
    "search_terms": ["Micron", "MU"],
    "domains": ["reuters.com", "bloomberg.com"],
    "locale": {"hl": "en-US", "gl": "US", "ceid": "US:en"}
  }
}
```

前三个参数都由平台从 ticker subject profile 自动生成/填充，O4 无需逐 ticker 手工配置。Google 要求 `search_terms` 至少 1 个，`domains` 可空；source 注册时没有默认 terms，且不进入默认 profile。

### 4.3 Ticker subject profile

不要从任意 O2 Event 文本猜公司简称。新增一个冻结的小型配置产物：

```json
{
  "ticker": "MU",
  "canonical_company_name": "Micron Technology, Inc.",
  "company_short_name": "Micron",
  "search_aliases": ["Micron", "MU"],
  "source": "ticker_initialization",
  "source_artifact_ids": ["..."],
  "version": 1
}
```

取值优先级：binding 显式 `company_short_name` > worker 内 Yahoo symbol lookup 的 `shortname/longname` 缓存 > ticker 原值降级。这样默认 binding 无需额外配置，同时保留 O2/O4 覆盖搜索词的能力；D1 `entity_relations` 不是稳定 alias registry，不直接全量变成监测词。

O2 完成后，O4 读取该 profile 与 O2/D1 冻结上下文，给 Google 生成显式参数。这样满足“由 O2 决定业务语境”，又不破坏 O4 对监测配置的授权边界。

### 4.4 Yahoo/Google 全局隐形域名与信源名黑名单

黑名单必须是平台级配置，初始为空，不出现在 source parameter schema、O2/O4 prompt、普通控制 API 或前端：

```text
DOXAGENT_MESSAGE_BUS_V2_HIDDEN_NEWS_DOMAINS=
DOXAGENT_MESSAGE_BUS_V2_HIDDEN_NEWS_PUBLISHERS=
```

实施时在 `accept_poll_result()` 前加入 `IngressDomainPolicy`：

```text
adapter PollResult
  -> normalize publisher domain / publisher name
  -> hidden domain and publisher blocklists (Yahoo + Google only)
  -> source binding allowlist (Google only)
  -> enqueue enrichment / Raw
```

域名匹配规则：lowercase、去尾点、去 `www.`，精确域名或子域后缀命中；信源名使用 trim + casefold 精确匹配。两份列表初始均为空，且只应用于 Yahoo 与 Google。命中项不进入 enrichment queue、Raw、Standard 或 stream，只在 poll telemetry 中增加分维度 blocked count 和 policy hash，不记录被拦截标题或正文。

## 5. 各 adapter 的详细行为

### 5.1 YahooFinanceNewsAdapter

主路径：NCP `latestNews`，`snippetCount=100`。200 条仅用于覆盖压力测试，不作为默认，避免每分钟重复传输过多历史。

映射：

- `source_message_id = content.id`；
- `title = content.title`；
- `summary/body = description || summary || title`；
- `published_at = pubDate`；
- `publisher_name = provider.displayName`；
- `url = canonicalUrl.url || clickThroughUrl.url`；
- metadata 保存 `query_ref`、Yahoo finance tickers、transport endpoint、schema fingerprint。

降级：NCP transport/schema 失败后，单次调用 Query1；Query1 失败再 Query2。Query fallback 最多 10 条，必须返回 `window_coverage=PARTIAL`，不能把 fallback success 报成完整。

### 5.2 IbkrNewsGateway + IbkrTwsNewsAdapter

复用现有 `IbkrTwsConfig` 的 loopback/port/client-id/timeout 安全边界和官方 `ibapi`，不要再写第二套 socket 配置。新增一个只读、进程常驻的 news session：

```text
TWS reader thread
  -> nextValidId / connection lifecycle
  -> reqNewsProviders
  -> resolve ticker contract
  -> reqMktData(contract, joined 292 providers)
  -> tickNews callback
  -> durable ibkr_news_inbox
```

inbox 最少字段：`provider_code, article_id, con_id, ticker, headline, published_at, extra_data, received_at, article_status, payload_json`，唯一键 `(provider_code, article_id, con_id)`。正文 `reqNewsArticle` 应受独立小并发限制；成功时保存 provider body，失败时保留 headline-only，不阻塞 drain。

60 秒 `IbkrTwsNewsAdapter.poll()` 只做：

- drain 当前 ticker 未 ack 且发布时间在 24h 内的 inbox；
- 输出 RawMessageInput 后原子 ack；
- 读取 gateway health/effective providers 写 acquisition metadata；
- 每分钟触发订阅 reconcile，而非断开重连。

断线恢复：指数退避重连；恢复后用 `reqHistoricalNews(conId, joinedProviders, start, end, 300)` 补洞并本地过滤。由于实测边界可能被忽略，`hasMore=true`、返回数达到 300 或最旧条目仍晚于窗口下界时 coverage=`PARTIAL`。历史接口没有可靠 cursor 时不循环伪分页。

若 TWS 未运行、ibapi 缺失或全部 provider 拒绝，记录 source-local DEGRADED/AcquisitionFailure；ticker 初始化、其他 source 与 Runtime 均继续。

### 5.3 ReutersSiteSearchAdapter

作为平台预置 crawler manifest，而不是每个 ticker 让 O4 重新生成脚本。参数只传 `company_short_name` 与 `max_pages`。Crawler Plane 执行：

1. 打开 `/site-search/?query=<encoded>&offset=0`；
2. 等待结果 heading 与列表；
3. 按语义 DOM 读取日期、标题、href、category；
4. 以搜索结果日期筛出今天和前一天，不再打开文章页解析精确时间；
5. 以日期 UTC 12:00 映射 `published_at`，同时标记 day precision；
6. 需要时 `offset += 20`；
7. 输出稳定的 provider id（优先 Reuters URL path hash）和 canonical Reuters URL。

直接 HTTP 401、challenge/WAF、空列表和“确实 24h 无结果”必须分开：

- 401/challenge/DOM contract 破裂：failure + coverage=`UNKNOWN`；
- 列表正常且第一条已经早于前一自然日：success、0 rows、coverage=`COMPLETE`；
- 翻到 cap 仍没越过下界：coverage=`PARTIAL`。

### 5.4 GoogleNewsSearchRssAdapter

query 编译：

```text
(term1 OR "multi word term2")
(site:a.com OR site:b.com)       # domains 非空时
when:1d                          # 请求优化，非真实性边界
```

注意严格转义双引号、括号和 operator 注入；terms/domains 只能由 schema-validated token 构造，不能接受完整任意 query string。

每次取一份 feed，无分页。用 item guid/link token 作为 transport id，pubDate 做本地 24h 过滤，`<source url>` 做 domain allowlist/hidden blocklist，再调用 canonical resolver。resolver 复用 adapter 的 `httpx.AsyncClient`，同一 poll 最多 4 个并发、单 URL 一次尝试；失败即 `UNRESOLVED`，不在 adapter 内重试风暴。默认无 terms 且不绑定，因此 O2/O4 未配置时不会发出空搜索或全网搜索。

## 6. 默认配置与失败隔离

`initial_default_profile()` 加入：

```text
yahoo_finance_news
ibkr_tws_news
reuters_site_search
```

三者 `target_interval_seconds=60`、tolerance 10%、连续会话、`content_enrichment_mode=ENRICH`。Google 只进入 `initial_sources()`。

这里的“默认启用”定义为：ticker candidate 中 binding `enabled=true`，安装后 scheduler 会尝试运行；不是要求外部依赖缺失时初始化失败。建议为来源增加统一 capability metadata/health，而不是修改 binding 业务含义：

- Yahoo：endpoint/schema 可用；
- IBKR：TWS connected、contract resolved、effective providers 非空；
- Reuters：browser runtime/session 可用；
- Google：binding 显式存在且 terms 非空。

capability 不满足时 adapter 返回 source-local failure 并按 scheduler backoff；不得 hard-block 初始化，也不得把 `HTTP 401`、challenge 或空结果误报 success。

## 7. 最小代码变更清单

按以下顺序做小步提交，不全局重写：

1. `src/doxagent/message_bus_v2/schema.py`
   - 仅补充必要 acquisition metadata/参数校验模型；不改变 StandardMessage contract。
2. `src/doxagent/message_bus_v2/manifests.py`
   - 注册四个 source；前三个进入 default profile；profile/source version 升级。
3. `src/doxagent/message_bus_v2/service.py`
   - 扩展旧 default-source migration 集合；在 enqueue 前增加全局 domain policy。
4. `src/doxagent/message_bus_v2/adapters.py`
   - 加 Yahoo 与 Google adapter；Reuters 使用内置 CRAWLER-kind adapter 并复用 Crawler Plane browser runtime，不走普通 HTTP。
5. `src/doxagent/message_bus_v2/google_news.py`
   - query compiler、RSS field preservation、canonical resolver、fixture contract。
6. `src/doxagent/message_bus_v2/ibkr_news.py`
   - 复用现有 TWS config，实现 news gateway、callbacks、subscription reconcile、historical catch-up。
7. `src/doxagent/message_bus_v2/repository.py`
   - 新增最小 `ibkr_news_inbox` 表与 lease/ack；不新增新闻长期归档表。
8. `src/doxagent/message_bus_v2/cli.py` 或现有 worker CLI
   - 增加 gateway 子命令/worker lifecycle；Compose 只新增一个常驻 news gateway service。
9. `src/doxagent/ticker_initialization/*` 与 O4 contract
   - 生成/pin ticker subject profile；O4 为 Google 写 candidate binding，O2 不直接写 Message Bus。
10. `src/doxagent/settings.py`、`.env.example`、Compose/runbook
    - TWS news client id、hidden blocklist、Reuters browser profile、source kill switches。
11. `tests/test_message_bus_v2.py` 及定向新测试文件
    - 只加本轮必要测试；真实 API 测试标记 `real_api`，不进入默认 CI。
12. `changelog`
    - 每个重要代码阶段完成后追加精简记录。

## 8. 实施阶段与验收门槛

### Phase A：契约、manifest 与入口策略

- 四个 source schema/profile migration；
- 24h local filter helper；
- hidden blocklist + Google domain allowlist；
- ticker subject profile/O4 ownership contract。

验收：已有 ticker binding 不被覆盖；新 ticker 仅自动得到前三路；Google 无参数时不存在 binding；黑名单命中项零 enrichment job、零 Raw。

### Phase B：Yahoo 与 Google

- Yahoo NCP 主路径和 Query1/2 partial fallback；
- Google RSS parse/query/domain/canonical resolver。

验收：fixture + MU real probe；重复两次 poll Raw/Standard 不增长；所有入站 published_at 都在 24h；Google source-domain allowlist 不能被 query 噪声绕过。

### Phase C：IBKR gateway

- 常驻 reader、provider discovery、292、article fetch、inbox、60 秒 drain、reconnect catch-up。

验收必须在 TWS 在线时执行：provider 列表、MU conId、至少一个 accepted provider、实时 tick 或明确“窗口内无 tick”的持续会话证据；人工断开/恢复后无重复、无缺口误报；`hasMore/cap` 正确产生 PARTIAL。

### Phase D：Reuters crawler

- browser-only discovery、offset 分页、DOM drift fixture、challenge 分类。

验收：Chrome/browser runtime 中 MU offset=0 解析结果；构造今天/前一天有结果 fixture 验证放行、再早一日验证拦截；构造无结果 ticker 验证 COMPLETE+0；直接 HTTP 401 不得走空成功。

### Phase E：候选初始化与端到端

- 用隔离 candidate 初始化 MU；
- 验证三路默认 binding + Google 缺省无 binding；
- O4 基于固定 subject profile 添加 Google 参数；
- 安装后跑两轮 60 秒 poll，检查 Raw/enrichment/Standard/stream/bootstrap。

必须报告的数字：每 source requested/returned/within_24h/invalid/blocked/queued/inserted/revision/suppressed、coverage、effective provider set、query mode、checkpoint、source health。HTTP 200、poll succeeded 或固定返回数都不等同完整覆盖。

## 9. 必要测试矩阵

只跑本轮相关测试：

- Yahoo：NCP 100、200 cap、ad filtering、canonical URL、Query fallback=`PARTIAL`、schema drift。
- IBKR：新版 error signature、`code/name` fields、provider join、duplicate tick、article failure fallback、disconnect/reconnect、300+hasMore、local window filter。
- Reuters：20 条页、next/offset、搜索结果日期的今天/前一天边界、日期不可解析、oldest-before-window、重复页、401/challenge、DOM drift、0 result。
- Google：term escaping、domain OR、hard allowlist、hidden blocklist、missing source URL、旧 token decode、`Fbv4je` 现代 token decode、resolver failure、24h filter、no terms no request。
- 共通：bootstrap suppression、两轮幂等、item-local failure 不阻塞其他来源、enrichment 仍只有一条队列、default profile migration 不覆盖既有 ticker。

建议命令形态：先按 test node 定向 pytest，再对改动文件跑 ruff/mypy；最后只做一个 MU candidate 端到端。不要启动全量 regression 或真实多 ticker 压测。

## 10. 风险与明确取舍

- NCP、Reuters 页面 DOM、Google RSS/canonical token 都是非稳定公开契约；每个 adapter 必须有 schema fingerprint、fixture、kill switch 和 source-local degradation。
- 非商业、24h 窗口、不长期囤积降低了运营和数据保留风险，但不自动改变各站点条款。按用户本轮决定，这不是开发阻断项；部署配置仍保留独立开关和限速，便于立即停用。
- Reuters 与 Google 的搜索结果是相关性召回，不是“所有新闻”的完整清单；不得把 `COMPLETE` 解释为互联网全覆盖，它只表示已完整处理 provider 当前暴露的窗口/页。
- IBKR provider 权限可能随账户、session 和 API entitlement 改变；每次连接都重新发现并记录 effective set，不把历史 provider 列表写死。
- Google canonical resolver 最脆弱。若其真实验收不稳定，允许先交付 headline-only 且 `UNRESOLVED`，不能为追求正文覆盖绕过统一 enrichment 或伪造 URL。

## 11. 最终建议

按 **Phase A → B → C → D → E** 实施。Yahoo/Google 可以先完成纯 adapter 与契约；IBKR 必须独立成常驻 gateway；Reuters 必须走 browser Crawler Plane。前三路作为默认 binding、Google 由 O4 显式配置，是与当前 ticker 初始化控制面最一致的落法。

如果只允许一次合并交付，发布门槛应是：四路代码均完成，但 IBKR/Reuters 真实运行环境未通过时保持 source-local DEGRADED，而不是阻塞整个 Message Bus 或 ticker 初始化。任何单条日期/URL/正文异常都应隔离为 item failure；只有 source contract 整体破裂才让该 source poll 失败。

## 12. 资料来源

- yfinance 当前 `get_news()` 实现（NCP `latestNews`）：[GitHub source](https://github.com/ranaroussi/yfinance/blob/main/yfinance/base.py#L2792-L2853)
- Yahoo Finance robots（`/xhr` 当前为 disallow）：[finance.yahoo.com/robots.txt](https://finance.yahoo.com/robots.txt)
- Yahoo 服务条款：[Yahoo Terms](https://legal.yahoo.com/ca/en/yahoo/terms/otos/index.html)
- IBKR TWS API News：provider entitlement、292、tickNews、reqNewsArticle 与 historical news：[官方文档](https://interactivebrokers.github.io/tws-api/news.html)
- Reuters Connect 的搜索/API 能力（作为稳定替代路径参考）：[Reuters Connect](https://reutersagency.com/content-delivery-platforms/reuters-connect/)
- Reuters robots（`/site-search/` 与默认 bot policy）：[reuters.com/robots.txt](https://www.reuters.com/robots.txt)
- Google `site:` 不保证列出全部索引结果：[Google Search Central](https://developers.google.com/search/docs/monitor-debug/search-operators/all-search-site)
- Google News wrapper 的现代解码协议与可参考实现：[google-news-url-decoder](https://github.com/SSujitX/google-news-url-decoder)
- Google News robots：[news.google.com/robots.txt](https://news.google.com/robots.txt)
- Google 服务条款：[Google Terms](https://policies.google.com/terms)
