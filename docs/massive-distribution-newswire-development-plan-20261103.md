# Massive Distribution 全局 Newswire 测试源开发方案

日期：2026-11-03（北京时间）。范围：调研、HTTP/RSS 实测及可执行开发方案；本轮未接入产品、未修改生产配置、未创建真实 Ticker 订阅。

## 1. 结论与边界

新增 `AcquisitionMode.MASSIVE_DISTRIBUTION = "massive_distribution"`，作为第四种采集/分发类型。它仍使用现有 Collector → CaptureBridge → LiveQueue → BODY → 正则 → 标准化链路，不增加服务、消息队列、浏览器身份、独立测试数据库或另一套处理引擎。

第一批保留五个外部源：Business Wire、PR Newswire、GlobeNewswire、ACCESS Newswire、TMX Newsfile。同平台的候选 feed/栏目是内部采集端点，独立 checkpoint、失败退避和轮询状态，共享平台 HTTP 调度；不能按每个 RSS 地址再拆外部源，更不能按 10 个 Ticker 重复抓取。

用户已确认测试目标：**INTC、MU、BE、COHR、NBIS、CEG、RKLB、SNOW、ONDS、IONQ**。只读取其现有 L2 正则监测词，不调用 Decision Model，不修改其词库，不建立真实订阅。正则中的 `literal`、`regex`、`any/all/none`、字段范围及语言回退继续使用现有实现。

需要明确三项实测限制：

1. **公开 RSS 不等于完整平台数据。** PR Newswire 和 GlobeNewswire 当前都是 20 条滚动窗口，Newsfile 是 25 条；批量同刻发布可能在两次抓取间挤出新闻。30 秒轮询不能数学上保证全量覆盖。
2. **RSS 成功不等于正文成功。** Business Wire 全量 RSS 可读，但正文 HTTP、现有两个出口及 Reader 的样本均未取得完整正文；Newsfile HTTP 正文受 WAF 限制，本地 Reader 样本可 FULL。
3. **ACCESS 暂未找到经验证的全平台企业新闻稿 RSS。** 可读的 `/feed/rss2` 明确是公司博客；常见 RSS 路径直连被挑战，现有出口实际返回 404。不能拿博客或其他 Newswire 的部分转载冒充 ACCESS 全量源。

因此，方案可以实现五个平台的注册、测试配置和共用链路，但**不能把五家都标记为“RSS 与正文接入成功”**。ACCESS 的 RSS 选择、Business Wire 的完整正文取得能力是明确的来源级待解决项，不阻塞其他来源开发和积累数据。

## 2. 调研方法及证据

### 2.1 本轮做了什么

- Web search 查找入口，再以官方 RSS 目录、官方说明及实际 XML 校验；第三方搜索结果只作发现线索，不作为覆盖率证明。
- 本地 HTTP 探测主要窗口：北京时间 **20:48–20:57**；包含首页/目录、feed、正文、条件请求及 Reader。完整时间以 JSON 的 UTC 时间戳为准。
- 从生产 Message Bus 容器进行只读网络探测；测试默认 HTTP 路由以及现有 `us-standard-5`、`tr-standard-1` 出口。没有切换代理选择器、修改身份或启动浏览器。
- 用当前 `content_enrichment.quality.inspect_html / inspect_reader / choose_candidate` 检查取得的样本，不以“HTTP 200”“长度足够”替代 FULL 判断。
- 只读核验了 10 个 Ticker 的当前词库版本和运行状态。

代码审查基线：本地 HEAD `86f3d34ea9784fcce0a5b638834133cbc573b898`。生产网络探测不能当作生产代码与该 HEAD 一致的证明。

证据目录：[eval/massive-newswire-research-20261103](../eval/massive-newswire-research-20261103/)。主要文件：

| 文件 | 内容 |
|---|---|
| `initial/results.json`、`candidates/results.json`、`official/results.json` | RSS 目录、候选响应、官方分类复核及原始 XML 文件引用 |
| `feed-summary.json` | 24 次 feed 观察、20 个地址的条数、时间跨度、大小及缓存字段；包括被否决候选，不是 20 个有效全量源 |
| `production-network.json` | 生产默认路由、10 个 Ticker 状态和词库版本 |
| `production-routes.json`、`access-index.json` | 现有 HTTP 出口对照；ACCESS 新闻室声明的 feed 链接 |
| `body-quality.json`、`economic-body-quality.json`、`reader-quality.json` | 现有正文检查器的样本结果 |
| `conditional.json` | BW、PRN 条件请求 304 结果 |
| `probe.py`、`remote_probe.py`、`route_probe.py` 等 | 有界、可复跑的只读探测脚本，不是新测试处理系统 |

### 2.2 可以和不可以推断的结论

本轮证明的是候选端点的短时可用性、feed 实际结构、正文样本及路由差异。没有连续观察完整交易日，不能给出可靠日均量、P95 发现延迟、长期成功率或“平台覆盖率 100%”。BW 的约一周窗口可计算最近 24 小时的窗口内条数；其他短 feed 不能按 20/25 条直接外推日量。

## 3. 五家 RSS 候选及选择建议

### 3.1 Business Wire

官方说明区分 RSS 标题流、全文 Atom 和其他授权交付方式，并支持按行业/主题定制；全文 Atom 不能假定存在无需开通的公共 URL。[官方 feed 说明](https://www.businesswire.com/help/feed-options)

| 候选 | URL | 实测 | 定位 |
|---|---|---|---|
| BW-A：全站 feed | `https://feed.businesswire.com/rss/home/?rss=G1QFDERJXkJcFVJYWQ==` | 200，4,078 条，约 4.81 MB，跨度约 7 天，含多语言版本 | **全局测试的首选** |
| BW-B：Technology | `https://feed.businesswire.com/rss/home/?rss=G1QFDERJXkJeEFpQWg==` | 200，345 条，频道名称明确为 Technology | 低流量备选/对照，不能单独满足全平台目标 |
| BW-C：官方全文 feed | 由平台提供的全文 Atom/NX 等地址 | 本轮未取得可测试的公共端点 | 如果公开网页长期阻断，这是减少 BODY 成本的候选；不作为已可用接口 |

BW-A 首轮最新 12:40 UTC、最旧 10 月 27 日 13:45 UTC；后续最新推进至 12:45。首轮窗口最近 24 小时有 940 条，包含语言版本，**不是 940 个独立经济事件，也不是平稳日均量**。同一发布时间最多见到 73 条。

feed 的 description 中位数约 499 字符，没有 `content:encoded`；不能用作完整正文。GUID 含语言后缀，不能直接去掉语言后缀后合并。URL 的 `feedref` 应去掉，保留原始 URL 作溯源。

缓存实测 `s-maxage=300`，带 ETag/Last-Modified。即时条件请求返回 **304、0 字节正文**。建议 30 秒条件轮询，不加随机 cache-buster。上游可能几分钟才更新，不能把本机 30 秒调度宣传成 30 秒发布发现。

生产 feed 默认路由 200、约 0.125 秒。正文：本地两个样本 403；生产默认样本超时；现有 US/TR 出口样本仍 403；Reader 返回的 200 是“Page Unavailable”，检查器没有接受为正文。

**推荐 BW-A。** BW-B 保留在候选清单，不默认多抓一份同域行业流。正文暂以“发现可用、完整正文未通过”标记，后续只对少量独立样本调试现有 BODY 访问策略，不能对每条新稿堆叠无效浏览器回退。

### 3.2 PR Newswire

官方 RSS 目录同时提供 All News Releases、General Business、Business Technology、Energy 等分类；平台另提供全文交付服务，公开 RSS 并非全文源。[官方 RSS 目录](https://www.prnewswire.com/rss/)

| 候选 | URL | 实测/取舍 |
|---|---|---|
| PRN-A：All News Releases | `https://www.prnewswire.com/rss/news-releases-list.rss` | 200，20 条，约 39–41 KB，覆盖最广，混入公共事务等类型 |
| PRN-A 的旧地址 | `https://www.prnewswire.com/rss/all-news-releases-from-PR-newswire-news.rss` | 实际重定向到 PRN-A；不能当作独立兜底 |
| PRN-B：General Business | `https://www.prnewswire.com/rss/general-business-latest-news/general-business-latest-news-list.rss` | 200，20 条，企业业务导向更强，但不保证涵盖全平台所有企业稿 |
| PRN-C1：Business Technology | `https://www.prnewswire.com/rss/business-technology-latest-news/business-technology-latest-news-list.rss` | 200，20 条；可作分类补漏 |
| PRN-C2：Financial Services & Investing | `https://www.prnewswire.com/rss/financial-services-latest-news/financial-services-latest-news-list.rss` | 200，20 条；含融资/证券，也含金融业自身稿件和律所营销 |

可供选择的组合：

- **覆盖优先：PRN-A 主流 + PRN-B 独立补漏。** 这是建议的初始测试组合；统计 B 相对 A 的新增独立稿数，再决定是否保留 B。
- **噪声优先：PRN-B 主流 + 目标行业分类集合。** 能减少部分非企业主题，但必须接受并测量跨行业漏抓，不能称为全站替代。
- 只用 PRN-A 最省请求，但 20 条窗口对停机恢复和同刻突发最脆弱。

首轮 A 的 20 条覆盖 12:12–12:38 UTC；`?pageSize=100` 实测仍 20 条，不能当作扩窗方法。缓存 max-age=60，If-Modified-Since 实测 304。

正文 HTTP 样本共 6 个均经当前检查器判为 FULL：2 个律所稿，另有并购、交易量、财报、商业地产稿。财报样本 HTML 含 7 个 table，提取约 28,033 字符；这只证明该样本可提取，开发验收仍需逐表核对关键数值和列关系。

生产默认路由 feed 200、约 0.354 秒。先直接 HTTP，不需要新 Chrome 身份。

### 3.3 GlobeNewswire

官方目录明确区分 Public Companies、主题及行业流。[官方 RSS 目录](https://www.globenewswire.com/rss/list)

| 候选 | URL | 实测/取舍 |
|---|---|---|
| GN-A：无分类全站流 | `https://www.globenewswire.com/RssFeed` | 200，20 条，约 38 KB，范围最广，含监管披露/基金等内容 |
| GN-A 的另一域名 | `https://rss.globenewswire.com/RssFeed` | 可读，样本内容一致；是同一上游流，不能算独立覆盖保证 |
| GN-B：Public Companies | `https://www.globenewswire.com/RssFeed/orgclass/1/feedTitle/GlobeNewswire%20-%20News%20about%20Public%20Companies` | 200，20 条；上市公司更集中，但会排除私营公司相关稿件 |
| GN-C：Press Releases 分类 | `https://www.globenewswire.com/RssFeed/subjectcode/72-Press%20Releases/feedTitle/GlobeNewswire%20-%20Press%20Releases` | 200，20 条；本轮跨度 10:00–12:30 UTC，明显不是全站所有稿件 |

**不要靠 feedTitle 判断订阅范围。** 官方 Press Releases 分类编号是 **72**。本轮排除了一个可返回 XML、标题也能显示“Press Releases”的误配路径 `subjectcode/1-Press Releases`：编号 1 在官方目录是 Advisory。URL 的描述文字及 feedTitle 不会证明筛选语义正确。

建议测试 **GN-A 主流 + GN-B 补漏**；GN-C 保留为用户可选的较窄组合，不强行认定“Press Releases”名称更符合需求就覆盖更完整。全站样本含私营/上市企业和披露，Public Companies 无法覆盖私营对手、供应商、客户。

全站 20 条曾有 15 条处于同一发布时间，存在短窗口突发风险。`?pageSize=100` 仍 20 条。缓存约 60 秒。

**生产路由必须显式配置。** 默认路由 RSS 403；现有 US/TR HTTP 出口均返回 200。两条出口的正文样本都经现有检查器得到 FULL，未启浏览器。建议先以 TR 主、US 备实测上线，沿用已有两个出口的 `transport_routes` 约束，总预算内失败才回退，不同时重复打两条出口。

本地两个不同稿件正文均 FULL，约 7,101 / 7,426 字符。无需把既有 GlobeNewswire Search 浏览器路径搬到全站 RSS 轮询上。

### 3.4 ACCESS Newswire

官方企业新闻室与 ACCESS 自身博客/自身 IR 新闻室必须区分。[企业新闻室](https://www.accessnewswire.com/newsroom)、[ACCESS 自身公司新闻室](https://newsroom.accessnewswire.com/press-releases)

| 路径 | 本轮结果 | 结论 |
|---|---|---|
| `/users/rss`、`/rss`、`/rss.xml` | 默认路由 403 challenge；US 出口对应常见路径为 404 | 没有验证出企业新闻稿 RSS，不能以默认 403 推断“只是换出口即可” |
| `/feed/rss2` | 200，144 条，频道 `ACCESS Newswire Blog`；最新 2026-07-09 | **排除：不是 wire 企业稿流** |
| 新闻室声明的 `accessnw.multiscreensite.com/feed/rss2` / `feed/atom` | 新闻室 HTML 的 alternate 链接 | 指向网站博客系统，不能据此认为是全站发布流 |
| `newswire.com/newsroom/rss` | 本轮默认路由也被 challenge | 即使可读，也不是已证明等价于 ACCESS 的完整镜像；不替换来源身份 |
| `/newsroom` HTML | 生产 US 出口 200，约 169 KB、2.576 秒 | 可作为**无公开 RSS 时的轻量 HTTP 候选**，尚未完成正文/分页验收 |

RSS 优先的可执行处理：注册 ACCESS 外部测试源，但 RSS endpoint 不填虚假地址，状态 `FEED_UNAVAILABLE`，没有有效端点就不生成周期性失败抓取任务。其余四家正常运行。以后取得官方全平台 RSS/全文 feed，直接补同一外部源的内部端点。

如果希望第一轮五家均能积累新闻数据，建议选择 **ACCESS 新闻室 HTTP 备选**：复用现有轻量 HTML 列表解析和 BODY，使用已验证的 US HTTP 出口；同一外部源的 acquisition_mode 仍是 Massive Distribution，但 endpoint 协议明确写 HTTP，不能在报告中冒充 RSS。开发阶段须补齐列表选择器、发布时间、下一页、正文各至少两个样本，才启用采集。

本方案**保留“等待官方 RSS”和“批准 HTTP 备选”两个选择**，不在证据不足时替用户确定一个虚假的 RSS 方案，也不自动引入高成本浏览器挑战循环。

### 3.5 TMX Newsfile

官方 Newsroom 说明公开全站流是最近 25 条，行业流最近 10 条，可另行请求全文 feed。[官方 Newsroom](https://www.newsfilecorp.com/news)

| 候选 | URL | 实测/取舍 |
|---|---|---|
| NF-A：全平台最新 25 条 | `https://feeds.newsfilecorp.com/global/Last25Stories` | 200、25 条、约 33 KB，**全局测试首选** |
| NF-B：Technology | `https://feeds.newsfilecorp.com/industry/technology` | 200、10 条、约 14 KB，只能行业补漏 |
| NF-C：官方定制全文 feed | 由 Newsfile 提供 | 官方说明可申请，本轮没有取得具体全文端点 |

公开 feed 的标题直接声明 Truncated，summary 不能当 FULL。首轮 25 条跨度约 45 分钟，同一时间最多 11 条；`?limit=100` 仍 25 条。猜测的 `Last100Stories` 虽返回 HTTP 200，但不是 RSS，已排除。

生产默认路由 RSS 200、约 0.942 秒。网页正文默认及两个出口的样本为 AWS WAF 202/空响应/挑战页，**202 不能当正文成功**。

本地一个相同文章 Reader 样本经当前 `inspect_reader` 判为 FULL、约 6,390 字符、耗时约 0.741 秒；已校验标题及 URL Source。生产 Reader 路由和批量稳定性未验收，不能从一个样本承诺长期成功率。

建议 NF-A。正文先 HTTP，遇已知 WAF 202 直接转现有 Reader，跳过无效 browser 尝试；同域明确受阻期间复用现有访问冷却，不对每篇重走已证实失败的多个出口。Reader 仍须身份及完整性检查，不能因 Reader HTTP 200 就判 FULL。

## 4. 候选选择及默认开发输入

以下是开发建议，不是本轮已经应用的生产配置：

| 外部 source_id | 主端点 | 默认补漏端点 | 待确认/不默认启用 |
|---|---|---|---|
| `businesswire_massive` | BW-A | 无 | BW-B 行业对照；全文授权端点 |
| `prnewswire_massive` | PRN-A | PRN-B | Technology/Financial/其他行业分类集合 |
| `globenewswire_massive` | GN-A | GN-B | GN-C 或其他行业流 |
| `access_newswire_massive` | 未验证出有效 RSS | 无 | 等待 RSS，或采用明确标识的新闻室 HTTP 备选 |
| `tmx_newsfile_massive` | NF-A | 无 | 行业补漏、官方全文 feed |

有效 RSS 初始组合共 **6 个内部端点、4 个平台**；若 ACCESS HTTP 方案获选且验收通过，则为 7 个采集端点、5 个平台。用户若选择其他候选，仅改变该源的 endpoint 配置，不增加外部源或处理链路。

## 5. 企业稿、噪声与语言范围

全站 Newswire 的“企业稿”并不等于“只有上市公司重大公告”。私营公司产品、客户合同、律所公告、市场研究推广、企业地区项目都可能混在一起。按 `上市 ticker 必须存在`、地区标签或标题含 lawsuit 就硬排除，会漏私营竞争对手、地方电力合同、重要诉讼等。

第一版只确定性剔除：官网导航/博客/非发布页、挑战页、损坏 RSS 项、确认是普通编辑新闻而非发布稿的内容。明确非企业的纯公共事务稿可以依赖可靠发布者/类别证据排除，并保留原因；缺少证据的保留，由目标 Ticker 正则筛选。不要构造一个额外语义分类器，不建复杂发行人知识库。

律所营销、市场报告、基金净值等先单独统计噪声样本及匹配贡献；在用户选择更严格范围前，不做全平台一刀切黑名单。保留可靠的原始 category、issuer、exchange/ticker 信息，缺失就为空，不从地名或摘要猜发行人。

全站流可能混合语言。按单条 URL/元数据的可靠语言证据保存语言，沿用该语言 L2 + English 回退；不能因 channel 声明 en 就把所有文章标成英文。不增加机器翻译或模型费用。报告须显示各语言稿量、目标词库语言缺口；“采到了非英文稿”不代表“英文词库覆盖了其行业语义”。

## 6. 当前架构与必须修改的位置

### 6.1 实际生产路径

当前实时路径以 `collection_plane` 和 `LiveQueue` 为主：

```text
source_definitions / compiled Configuration
  → Collector：共享平台端点、HTTP、解析、轮询 checkpoint
  → acquisition.sqlite3 captures：现有耐久捕获
  → CaptureBridge：现有 at-least-once 接纳
  → live_articles / live_origins：文章去重、BODY 所有权、修订
  → ContentEnrichmentHub / ArticlePipeline：正文补全与质量检查
  → live_deliveries：10 个目标 Ticker 的正则结果
  → prepare_raw_message + _standardize：正常 Raw/Standard 内容准备
  → 最终提交分支：production 原提交 / test 测试发布记录
```

`distribution.py / distribution_repository.py` 仍有兼容路径，但不能再另建一套 distribution run 作为新源实时主引擎。旧路径只作必要的第四类型兼容，不复制实时工作。

### 6.2 代码审查发现

| 位置 | 当前事实及修改要求 |
|---|---|
| `schema.py::AcquisitionMode` | 当前三类；加第四枚举。它是 acquisition_mode，不是新增一种 HTTP/爬虫 SourceKind |
| `collection_plane/worker.py`、`scheduler.py` | 多处严格比较 BY_DISTRIBUTION，影响按源共享、roster、work unit；需统一识别两种分发模式 |
| `collection_plane/store.py`、`bridge.py` | 有字符串 `by_distribution` 判断，涉及 checkpoint 和 bootstrap，不能只改 enum |
| `live.py::LiveDeliveryWorker.process` | 非 BY_DISTRIBUTION 当前可能直接发布；遗漏新枚举会绕过正则，是必须测试的风险 |
| `live_repository.py::prepare_input` | `direct` 根据类型决定；新类型必须与共享分发一致 |
| `industry_sources.py::SharedFeedAdapter` | 响应正文上限 2,000,000 bytes；BW-A 已超限 |
| `publisher_http.py::PublisherSession` | 默认 curl 响应缓冲上限 4 MiB；BW-A 同样超限，不能只改 adapter 的 2 MB 判断 |
| `body_results.py`、`live_repository.py` | canonical URL 可跨源复用；`live_deliveries` 主键只有 `(article_key,ticker)`，仅末端加 dry_run 不足以隔离 |
| `live_repository.py::prepare_publication/publish` | 正常 Raw/Standard 准备与正式表写入/stream 发布的切分点 |
| `monitoring_terms.py`、`service.py` | 词库语言需求、绑定校验也有类型枚举判断；测试目标不能伪装成真实订阅 |
| API wire / `source-settings.tsx` / 监测词页面 | 类型 schema、标签及消费者展示须支持第四类；测试状态不得混进真实订阅数和正式消息统计 |

新增一个语义清楚的共享分发判断函数，例如 `is_shared_distribution(mode)`，用于共享采集/参数规则。Decision Model 资格仍由明确策略控制；本轮 Massive 一律 regex-only，不能因为 helper 扩展误继承模型调用。

## 7. 最小数据及配置变更

### 7.1 源配置

在现有 `SourceDefinition` 加 `delivery_mode: "production" | "test" = "production"`。在其 `DistributionPolicy` 增加 `test_tickers: list[str]`；初始五源固定上述 10 个目标，`jev_enabled=false`，`require_complete_body=true`。已有源不变。

端点配置存现有 source 参数体系，内容包括 `endpoint_id / url / format / routes / enabled`。可放在一个小型 `newswire_sources.py` catalog + adapter 中，底层复用现有 RSS 解析、HTTP 客户端和共享上下文。无需为五个平台各写一套 worker。

测试目标由编译配置生成**内存中的 TickerSourceBinding 兼容对象**，传给现有 Collector/Live 调用，不写 `ticker_source_bindings`，也不加入 DefaultMonitoringProfile。binding_id 仍使用现有格式，测试 work unit 带明确作用域；配置读取统一用一个 resolver，普通绑定仍走原 repository，测试绑定从源配置构造。

`ConfigurationLoader` 继续按 source/terms revision 刷新；测试目标变动随源版本刷新。词库缺失时该目标记 `CONFIG_INCOMPLETE`，其他目标和平台照常处理。10 个目标的词库存在不需要开初始化流程。

本轮只读版本快照：BE=3、CEG=2、COHR=2、INTC=4、IONQ=2、MU=6、NBIS=2、ONDS=4、RKLB=3、SNOW=2。开发上线应重新读取，不把这些版本写死。

### 7.2 作用域隔离：必须前置标记，最终阶段才分流

“仅最终入总线隔离”指处理算法不分叉，**不代表前面可以共用无作用域的去重主键**。否则同一篇文章已由普通源发给 INTC 时，测试任务可能命中同一个 live delivery，污染正文所有权、决策或正式发布状态。

- 捕获创建时冻结 `delivery_mode`，传递到 capture payload、generation/input_key、live article、origin alias 和发布准备结果；不得在执行末端只读取可变的当前源开关来决定去向。
- article/origin/发布去重键增加统一处理作用域；所有 test 源可共用一个测试作用域，以复用测试内相同 URL 的正文。production 默认值保持原键，避免迁移重抓旧文章。
- 对 `live_articles` 加默认 production 的 scope 标量列及必要索引，用于 claim 优先级与指标过滤；复用现有 live tables，不建镜像 live tables。
- BODY cache 可以只读复用 production 中已完成且语言/权限/版本兼容的 FULL 结果；test 的等待、claim、失败、修订及写回必须是测试作用域，不能覆盖 production 的活动 BODY owner。
- `_merge`、`reserve_resolved`、canonical alias 和正文结果复用都必须保持作用域。不要通过伪造浏览器 entitlement/access_scope 达到隔离；处理作用域与访问权限是不同维度。
- 测试转正式时只接受新一代 production capture；旧测试积压永远进测试 sink，不批量补发。没有本轮授权，不开启正式模式。

### 7.3 最终测试发布记录：只加一个小型结果表

在现有 Bus SQLite 增加 `test_publications`，它是**最终提交目的地**，不是第二套处理系统。字段建议：

```text
publication_id, article_key, ticker, source_id,
content_revision, terms_revision, source_version,
decision_digest, body_outcome, prepared_at, published_at,
payload_ref
UNIQUE(article_key, ticker, content_revision, terms_revision)
```

`payload_ref` 使用现有 NativeContent / artifact 引用，保存同一正常 builder 生成的 Raw/Standard 内容、来源证据和测试发布回执，不把十份长正文复制到 SQLite。Body 仍存一份；各 Ticker 的分发结果引用它。

把 `prepare_publication` 中的发布对象准备复用成共同步骤；正式去重查询和测试去重查询按 scope 选择，随后 test 分支原子写结果并把 `live_deliveries.state` 置 `TEST_PUBLISHED`。未变化的重跑不新增记录，实质修订保存版本及更新状态；沿用 ARTICLE_ONCE，不再次触发已消费的下游。

**test 分支不写**正式 `raw_messages`、`standard_messages`、`stream_items`、`stream_members`、正式发布别名/版本、消费游标或下游 outbox；不分配正式 stream offset，不发 `record_consumable` 通知。它仍可以写现有采集、BODY、决策、审计及 artifact 内部台账，这不等于进入正式消息流。

正常业务页、消息数、BODY 成功率和 P0 延迟指标默认只计算 production；测试统计从相同台账按 scope 查询。测试输出独立标为“测试发布”，不能标成已被下游接纳。

## 8. 轮询、覆盖与资源方案

### 8.1 采集不等 BODY

- 初始内部端点目标 **30 秒**，源级统一调度；每平台并发 HTTP 1、端点错峰。五平台之间不互相等待。
- 同域 endpoint 独立 validators、内容 hash、checkpoint、最后成功时间和失败退避；某补漏流失败不暂停主流。
- 全局测试建议 24×7 运行，以源 enabled 控制。仅在 test 模式的配置编译/门禁中实现，不修改其他源交易日规则、不使用全局 override；测试目标的真实订阅开关不承担测试调度职责。
- 一轮 feed 请求预算 12 秒，至多使用一个已配置备用出口，所有尝试共享该预算。403/429/无效 XML 记真实失败及退避，304 是列表验证成功，但不是新消息。
- 收到有效条目即交现有 capture/bridge，接下来继续下一轮；BODY、正则和标准化都不占用该 feed 的采集锁。

### 8.2 大 feed 与重复处理

BW 使用**源/请求级** 16 MiB 解压后上限，改通 HTTP 层及 RSS 层的真实限制；其余源保持现有限制。不要全局放大所有网站的响应缓冲。

优先条件请求，200 相同 hash 不重复解析；变化时有界解析 XML，逐项释放节点，解析工作放到现有 prepare/线程阶段，避免阻塞采集 event loop。只把新条目/实质变化 delta 入 capture，不把 4,000 条每 30 秒重新落盘并扇出十份。

初次启动不为约一周的历史 BW 条目做 BODY。沿用新鲜度规则处理窗口内候选，并将启动样本标为 bootstrap；过期条目只参与基线计数/覆盖证据。首屏初始化数量不混进稳态日量和发现延迟。

去重顺序：平台 ID + 语言/规范 URL → 已知正文与版本复用 → 实质更新才重新补全 → Ticker 级发布去重。`feedref`、轮询时间、抓取顺序、摘要的装饰性变化不应制造更新；也不能只比较标题忽略官方更正、更新标记或重要正文变更。

跨平台同一稿保留各平台观察记录，按现有可靠正文指纹识别重复；不使用标题近似把不同融资或订单强行合并。与已存在的 production 新闻做只读重复标记，计算测试源的增量价值，不修改正式消息，也不能因此把本测试源的采集量抹掉。

### 8.3 BODY 和正则

**每篇独立文章只补全文一次，再对 10 个 Ticker 跑现有正则。** 不能为每个 Ticker 访问同一正文。

摘要不含关键词不能判无关：关键产品、竞争对手、合同对象经常只在正文里。第一版不做“标题/摘要未命中就不下载正文”的不可逆前置过滤；完整正文失败时保存 `UNRESOLVED_BODY`，不把正则阴性算成已证实无关。

正文策略：PRN 直接 HTTP；GN 使用已验证出口 HTTP；NF 对已识别的 202/WAF 快速转现有 Reader；BW 只有通过样本验证的 HTTP/Reader 能力才进入批量尝试。测试任务默认**不启动 Chrome**；不为全站海量稿件创建 Profile 或持续挑战重试。若后续确有必要使用已有身份，只针对小量待验证样本，另行给出成本和成功证据后再决定，不作为首版吞吐依赖。

使用当前完整性、标题/规范 URL 身份检查。保留正文中的价格、数量、对手方、合同期限、融资条款、表格及单位，去导航和重复模板，不引入摘要模型。RSS summary/错误页永远不升级为 FULL；Reader 的 Published Time 不自动覆盖发行人的原始发布时间。

源策略 `require_complete_body=true`：只有 FULL/SHORT_FULL + 正则命中才 `TEST_PUBLISHED`。BODY 失败仍保存发现、候选匹配和失败原因，用于评价这家源的实际有效率，而不是隐藏在无关稿中。

### 8.4 与生产公平共用资源

- 测试 feed 网络并发最多 2，BODY 在现有 worker 中测试 in-flight 最多 1，包含在现有总额度里，不扩大总 Chrome/HTTP 并发。
- 生产就绪任务优先；没有生产待处理时测试持续推进。测试任务每次网络尝试有短预算，不能长时间占用所有总槽；不能仅在独立 semaphore 上限 1、却让大量测试任务先抢走总槽排队。
- 同平台受阻复用现有域冷却/重试机制；一次失败后不能让其数百篇稿逐篇重新试相同的失败浏览器/出口链。
- 批量标准化/正则按现有小批工作和时间片执行，避免一个大源的 10×N 分发阻塞新到 production 消息。
- 测试积压不能静默丢弃或冒充已处理。以发现量、BODY 等待年龄和已处理量分别报告；若持续积压，直接报告所需成本与实际能力，不用减覆盖来制造漂亮指标。

资源目标：不增加常驻进程、Chrome 或定时全表扫描。新增内存以一个 BW 有界解析批和少量并发正文为主；**新增稳态 RSS 内存目标 <100 MiB** 是待压测目标，不是本轮测量结果。

BW 不支持缓存时，4.81 MB × 120 轮/小时约 **577 MB/小时** 的应用层返回量；304 已证明可用，实际预算必须按 200/304 比例和压缩传输量统计，不能忽视带宽。首版不保存每次完整 feed；仅保留必要基线、变化 delta 和诊断样本。

## 9. 测试数据与评价方式

直接扩展现有源详情读接口/CLI，提供 source + time-window 的分页 JSON/CSV 导出；UI 只增加类型标签、测试状态及结果入口，第一版不建设新仪表盘、实时 WebSocket 或长期在线分析服务。

| 维度 | 必须留存/计算 |
|---|---|
| 请求与轮询 | endpoint、路由、HTTP 状态、耗时、304、响应大小、调度偏差、真实完成间隔 |
| 发现时效 | `publisher_published_at → first_seen_at` 的 P50/P95/max；排除初始化旧稿并单列语言版本、修订稿、未知/估算时间 |
| 本地链路时效 | first_seen → capture commit → BODY ready → regex → TEST_PUBLISHED；与来源本身缓存延迟分开 |
| 内容完整度 | 独立新文章总数、BODY 尝试数、FULL/SHORT_FULL、PARTIAL、失败、未尝试及等待；同时给已完成尝试成功率和全发现口径有效率 |
| 正则相关度 | article×ticker 的命中、阴性、词库版本、命中规则/字段、BODY 不完整而无法确定；Decision Model 请求数必须为 0 |
| 真实相关性 | 人工抽样复核正则阳性及阴性，特别检查只在正文出现的关系；正则命中率不是准确率，不从未标注数据编造 precision/recall |
| 消息量 | feed item 观察量、去重后文章数、语言版本、经济事件近似量分别列；不把 30 秒重复读取计为新消息 |
| 重复 | 同 endpoint 重复、同平台多 feed 重叠、跨平台可靠重复、与现有 production 新闻重叠；保留来源贡献 |
| 企业稿噪声 | 抽样统计纯公共事务、律所营销、市场报告、基金披露等，不把它们自动当成无关或漏抓 |
| 覆盖 | feed 窗口跨度、同刻批量数、每轮新增数、无重叠滚动风险、与同时间官方列表/归档抽样对照的缺口 |
| 资源 | 测试 HTTP/BODY 并发、总请求数、Reader 请求数、CPU/内存增量、Native/SQLite 增量、生产任务等待变化 |

覆盖分成三项：平台稿件是否出现在公开 feed；出现后本系统是否采到；采到后是否取得完整正文并正确正则分发。后两项做得好，不能证明第一项完整。

对照验证复用已开发 adapter 的低频官方列表抓取或人工固定时点样本，不新建另一条常驻抓取服务。短 feed 发现窗口无交集时记录 `WINDOW_OVERFLOW_RISK`；没有交集不是已证实漏抓，存在交集也不能证明期间没有整批被挤出。

首轮建议连续 **3 个自然日并覆盖至少 2 个完整美股交易日**，包括盘前整点高峰；周末不能替代交易日验证。每日人工抽样每源约 20 条阳性/阴性/正文失败，样本不足如实给出。开发调试和最终验证窗口分开，避免修改正则后再把同批样本当独立验收。没有本轮授权不自动调词。

保留策略复用现有维护器：测试详情及结果默认 14 天；汇总在需要评估时离线导出。到期小批清理 terminal 测试记录并释放 artifact_ref，保留仍在处理任务；正确接入已有 GC，不另建清理 daemon，也不在运行中 VACUUM。报告中明确统计留存窗口。

## 10. 可直接执行的开发顺序

### 步骤 A：先落隔离和第四类型（优先）

1. 修改 `schema.py`：第四枚举、delivery_mode、test_tickers，默认行为全部兼容。
2. 加共享类型 helper，审查 `rg 'BY_DISTRIBUTION|by_distribution'` 命中：service、scheduler、worker、store、bridge、live、live_repository、monitoring_terms、manifest，以及 API wire/前端标签。
3. 编译测试目标兼容对象，增加统一绑定 resolver；禁止这些对象写入真实 bindings/default profile。保留正常源控制路径。
4. 冻结 capture 的目标模式；给 live/body/alias/delivery 的相关键接入同一个作用域规则，修改合并与缓存命中路径，避免只隔离最终 stream。
5. 加一个 `test_publications` 结果表，复用 Raw/Standard 准备，将 test 的最终 commit 放在所有正式业务写入和 outbox 之前。补齐测试 terminal 状态及重启幂等。
6. 先用合成源跑通同一 URL 同时进入生产和测试，验证正式链路不受影响，再接公网大源。

### 步骤 B：RSS 及来源接入

1. 新增 `newswire_sources.py` 的五源 catalog 和共享 RSS adapter；复用现有解析器/HTTP transport，按 endpoint 拆物理 work unit。
2. 完成 BW 单请求 16 MiB 上限、增量/条件处理，不改其他源默认上限。
3. 按第 4 节候选组合配置有效端点；ACCESS 不虚构 RSS，根据选择保持待配置或补新闻室 HTTP 解析。
4. 给 GN 配已验证 HTTP 路由；对其余站使用实测可用的最低成本路径。
5. 验证 GUID/规范 URL、语言、时间精度、同刻发布、非法 XML、HTML 200/202、缓存 304，不把异常当空列表成功。
6. BODY 复用现有正文管线；NF 加/配置 WAF → Reader 的短路径，BW 单点调试，不增加批量 Chrome 负载。

### 步骤 C：资源公平与可观测

1. 在现有 Collector/BODY claim 上落实 test 并发和 production 优先，配合现有总容量，不建独立调度服务。
2. 新增 scope 过滤和来源级指标，正式 UI 的消息数、订阅数、BODY/P0 指标不混入测试。
3. 添加同源测试详情/导出 CLI 或读接口，结果使用现有 Native 引用，不做正文多份存储。
4. 接现有维护器完成 14 天测试记录/引用回收；失败、等待和未配置状态可见。

### 步骤 D：测试后启动测试运行

1. 离线回归和真实只读样本通过后，使用正常发布流程部署相关角色；本轮方案交付本身不执行部署。
2. 写入五个源定义及测试目标，不调用 `configure_binding` 给 10 个 Ticker 创建真实订阅。独立读回所有真实 bindings、default profile 和 monitoring terms，确认无变更。
3. 小流量开启 PRN/GN/NF，确认最终 sink 零泄漏与生产资源稳定，再开 BW 大流；ACCESS 按真实能力启用，不能为满足“5/5”而使用错误 feed。
4. 采集第 9 节数据，输出来源×候选端点×Ticker 的评估；失败项继续针对实际样本调试。没有用户正式启用指令，不晋升 production。

## 11. 测试与验收清单

应增加真正覆盖边界的测试，复用现有 `test_message_bus_distribution.py`、collection plane、live、BODY 和 dedup 测试；不写只镜像枚举赋值的测试。

| 测试 | 通过条件 |
|---|---|
| 第四类型全链路 | Massive 共用一轮物理采集，不被当成 BY_TICKER，不跳过正则；模型调用计数 0 |
| 最终隔离 | 命中稿完成 BODY、清洗、Raw/Standard 准备及 TEST_PUBLISHED；正式 raw/standard/stream/消费游标/outbox 与发布别名无新增/变更 |
| 同 URL 同 Ticker 双模式 | 同时已有 production 及 test，先后顺序互换、并发和重启均不覆盖/抑制正式 delivery；生产只发布一次 |
| 模式变更在途 | 测试 capture 排队时修改源模式，旧任务仍进 test；正式新一代正常执行，不回放测试积压 |
| 内容和目标去重 | 10 个目标只一次 BODY；多 feed 同稿不重抓/重发；实质更新有修订；追踪参数/相对时间变化不触发更新 |
| 绑定与词库 | 真实 bindings/default profile/词库内容不变；词库缺失只影响对应目标，配置刷新可见；命中结果保留使用版本 |
| 大 RSS | 真实约 4.81 MB BW fixture 可完整解析，无 2 MB/4 MiB 截断；其他源默认限制不变；304 不重复正文任务 |
| 来源失败隔离 | 一端点超时、403/429、ACCESS 未配置，不影响同源其他端点和其他平台 |
| 正文质量 | PRN/GN 样本、NF Reader 样本保留经济事实和表格；BW 错误页、NF 202、RSS 截断、Reader 错误页不会 FULL |
| 新鲜度 | 原始时间/精度保留；旧稿、初始化、新稿、重要更新分别统计；不把抓取时间当发布日期 |
| 高峰负载 | 用窗口中批量同刻发布的 fixture 压测；test HTTP≤2、BODY≤1，生产可继续发现和接纳，记录平均/尾延迟变化 |
| 留存与 GC | terminal 到期记录和引用正确回收，不删除在途/production 内容、不产生悬空 Native 引用 |

运行验收分三层，不能混报：

1. **链路验收**：第四类型、regex-only、轻量测试发布、隔离及幂等全部通过。这是上线测试能力的必要条件。
2. **来源验收**：逐平台给 RSS/列表发现、完整正文、路由和覆盖证据。ACCESS 无有效 RSS 和 BW 正文受阻必须单列，不能用其他源成功率掩盖。
3. **业务评估**：足够观察期后比较真实相关度、增量覆盖、时效和资源成本，用户再决定候选组合及正式启用。正则结果不是人工真值，feed 窗口覆盖不是全平台覆盖。

## 12. 本轮尚需用户选择的来源选项

- PRN：建议 All + General Business 同源双端点测试；也保留 General Business + 行业集合的较窄方案。
- GN：建议全站 + Public Companies；Press Releases(72) 保留为窄流候选，不能替代全站。
- ACCESS：等待真实官方 RSS，或采用已经验证可访问、但仍需解析/正文验收的新闻室 HTTP 备选。
- BW：全量 RSS 推荐明确，完整正文能力未解决；是否后续取得官方全文 feed，取决于公开 BODY 调试结果与实际成本。

上述选择只影响源端点，不要求另起测试架构。本方案的隔离、正文复用、零模型和最终发布实现可先独立开发。
