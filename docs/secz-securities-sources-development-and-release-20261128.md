# SECZ / COIN 行业消息源实施与发布验收（2026-11-28）

## 交付范围

新增五个外部消息源，同源栏目独立采集、统一去重和管理，均使用现有 HTTP Listing / BODY、Publisher Budget、20 秒调度、统一监测词和标准消息入库链路。没有新增浏览器、身份、数据库或常驻服务。

| 外部源 | 模式 | 订阅 | 实际采集入口与筛选 |
|---|---|---|---|
| SEC Crypto/Newsroom | By Ticker | SECZ、COIN | 官方 Crypto@SEC 和 Press Releases 表格；同一行动的规则、Fact Sheet 合并为附件；重复 FAQ 保留最新日期 |
| Markets Media | By Distribution | SECZ、COIN | Exchanges、From The Markets 两个原始栏目，HTML 只读 POST；仅栏目 teaser，排除全站置顶 hero |
| SIFMA | By Ticker | SECZ、COIN | 官方 WordPress GraphQL POST，原生 letters / press-releases 分类各 40 条 |
| ICE / DTCC Press Releases | By Ticker | 仅 SECZ | ICE Q4 API；DTCC 官方前端 Coveo PressReleaseSearchModuleBlock 对应搜索，各 40 条，只收 Press Releases |
| tZERO News | By Ticker | 仅 SECZ | 官网 Sanity live API，published perspective，仅 news 且排除 drafts，各 40 条 |
| 企业 IR 聚合新增 secz.press | By Ticker | 仅 SECZ | Securitize Q4 JSON，40 条；绑定只选择自身 endpoint，20 秒 |

两个专用源的卡片简介明确标注 SECZ 专用。新增卡片维护 HTTP 列表、HTTP 正文与行业分类。Securitize 加入既有企业 IR 聚合，没有单独新建源。

SECZ 另订阅全部已启用 GLOBAL 源，以及用户指定的 The Block、CoinDesk、Ledger Insights、Unchained；不追加其他既有行业源。COIN 只新增上表前三项，既有绑定、监测词与状态保持不变。SECZ 初始化/激活由原流程负责，本轮不生成监测词、不强行启动。

## 入口勘察与正文处理

- SEC 官方表格可直接 HTTP 读取，响应 no-cache/no-store；Crypto 的日期精度是 DATE，Newsroom 的 time datetime 保留原生精度。重复 URL 不依赖页面排序。部分 no-action 页面只有简短回复，必须读取 incoming PDF，关联经济文件已完整合并，不以摘要替代。
- Markets Media 的 WordPress Exchanges REST 候选返回 2020 年旧文，与当前栏目 2026 年列表不一致，已排除。正常栏目 GET 与只读 POST 最新卡片一致；选择 POST，保留栏目日期为 DATE。不能由这次一致快照推断未来发布时效。
- SIFMA 的 Next 页面和原生 REST 存在缓存策略，且直连出口可能返回 Challenge HTML。原生 REST 与 GraphQL 的首批条目对照一致；采用固定现有 us-standard-5 出口的官方 GraphQL POST，以原生 taxonomy 查询，dateGmt 保留 EXACT。Letters 正文包括完整 PDF，而非只读取介绍段落。
- ICE / Securitize 直连返回 429，同一原生 Q4 列表在现有 us-standard-5 出口可用；BODY 同样采用已验证出口。Q4 原生美东发布时间转 UTC，保留 EXACT。
- DTCC 的 Next 初始 HTML 只有首条 hero。根据前端组件定位实际 Coveo 新闻稿列表，所有抽样结果均为 Press Releases。公开 scoped client key 从官方公开前端启动时读取，内存缓存一小时，不写入源码或回执。组件或范围变更显式失败，不静默扩大栏目。news_date 是日历日期，标为 DATE；搜索索引自身发布延迟尚待自然新稿验证。
- tZERO /news 会跳到混合 Content Hub；只查询 news，排除 Media、Insights、Learn、podcast 和草稿。BODY 读取公开 CMS 原文并校验 ID、slug、类型；保留段落、标题、表格及图片说明，未知数据块降为 PARTIAL，不降低完整性标准。

## 抓取与正文抽样

真实远端只读 canary 使用生产 Adapter、解析器与 BODY Reader；无 Bus 写入、无模型调用。九个行业入口加一个 IR 入口共十项均取得非空列表，正文抽样均为 FULL。

| 入口 | 列表条数 | 正文字符数 | 结果 |
|---|---:|---:|---|
| SEC Crypto | 39 | 53571 | FULL，含关联 PDF |
| SEC Press | 25 | 1282 | FULL |
| Markets Exchanges | 9 | 4510 | FULL |
| Markets From The Markets | 9 | 3093 | FULL |
| SIFMA Letters | 40 | 10386 | FULL，含 PDF |
| SIFMA Press | 40 | 5723 | FULL |
| ICE | 40 | 3172 | FULL |
| DTCC | 40 | 4523 | FULL |
| tZERO | 40 | 12961 | FULL，公开 CMS |
| Securitize IR | 40 | 8419 | FULL |

回执：[发布前 canary](artifacts/secz-sources-20261128/pre-release-canary.jsonl)。这些是完整性抽样，不代表长期成功率。

## 测试、发布与验收状态

新增 13 项测试与固定路由回归 14 项通过；Ruff E9/F 与 diff 检查通过。较大回归中的历史 newswire_rss 用例依赖已退役源，单独排除；旧调度测试还存在 Yahoo SUPPLEMENT 10/20 秒和 recheck claim 预期失配，相关代码本轮未修改，未把这些历史断言算作通过。最终直接相关回归及生产回执在下方补齐。

发布采用实际运行镜像上的 12 文件有限覆盖，范围仅 API、Collector、Message Bus、BODY、O4；不重启正在运行的 Initialization、Chrome、Scheduler 或交易服务。已只读对比全部将覆盖的既有模块：生产相对 origin/main 的三个差异（Quantum Zeitgeist 固定日本出口两处、SBC RSS 卡片）全部保留，其他文件一致。

## 时效性边界

验收核心仍是官方 publish → 首次发现 → Bus，而非 HTTP 200、请求耗时或 Cache Age。本轮不引入 Age 超阈值即失败的生产机制，不盲目加随机参数。采集端没有本地列表缓存；配置缓存仅 DTCC 公开前端 client key。

目前多数最新稿件为 11 月 24/25 日或更早，且 SECZ 尚在初始化，没有可用于统计的自然新稿发布到 Bus 样本。因此真实发现延迟和长期稳定性尚未通过业务验收；DATE 来源也不能伪造精确秒级延迟。既有全局源的订阅或凭据阻塞不在本轮修复范围。
