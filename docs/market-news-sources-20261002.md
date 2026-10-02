# 新市场消息源：实现与生产验收（2026-10-02）

## 范围与采集策略

| source_id | 模式 | 默认订阅 | 采集 / 正文 Runtime | 轮询 |
| --- | --- | --- | --- | --- |
| investorshub_ticker_news | by ticker | 全局默认；当前 MU / INTC / BE / RKLB | External Chrome + CDP | 60s |
| globenewswire_search | by search | 全局默认；当前 MU / INTC / BE / RKLB | Managed Playwright；正文允许 HTTP / Browser | 60s |
| investing_ticker_news | by ticker | 全局默认；当前 MU / INTC / BE / RKLB | External Chrome + CDP | 60s |
| globenewswire_semiconductors_rss | by distribution | MU / INTC / BE | 官方 RSS；正文归属 GlobeNewswire | 60s |

全局默认加入现有 default Profile，不替换其旧源；当前 RUNNING ticker 补齐缺失订阅，不恢复已删除、禁用或 tombstone 的绑定。行业 RSS 只分发给订阅该入口的 ticker，复用现行 L2 正则 + Jev、正文队列、实时 30 分钟与闭市 sweep 窗口。

按照本轮用户明确决策，GlobeNewswire 搜索不因 robots.txt 的 Disallow 被禁止、降级或改成慢轮询；不增加该网站的额外请求间隔。保留真实请求耗时、单 Profile 并发互斥和原有市场日历，不伪造请求成功。

## 实现边界

- 新增 `market_sources.py`，只解析实际 ticker 新闻卡片或搜索结果，不扫导航栏、推荐区或任意外站链接。iHub 卡片内的 `uk.advfn.com/market-news/article/...` 属于合法 ADVFN 内容，保留并进行最终发布域名正文提取。
- Investing.com 与 iHub 使用可覆盖的 `listing_url` / `ticker_pages`。首批四个 ticker 均配置真实入口；新增 ticker 的 Investing.com 订阅需提供正式 instrument URL，不能把示例 INTC 页面用于其他 ticker。iHub 可额外指定 NASDAQ / NYSE / AMEX。
- GlobeNewswire 从第一页开始，以已提交的 ticker L1 概念分别搜索；默认每个 query 最多三页，支持当前 QueryPlan 的 OR 模式、独立 checkpoint 和有限重试。页面无结果与 DOM 失配分别处理，不能把布局变化报告为正常空结果。
- 搜索和 RSS 是两个独立 SourceDefinition，但正文治理均归属 globenewswire，复用一次采集 + 去重 + 正文 + ticker 分发路径。
- iHub / Investing 原生浏览器正文路径不先发送另一个 HTTP 身份的探测请求，避免 HTTP 403 污染同一访问组合健康状态。新源专用页面等待实际新闻内容，而非广告资源拖延的 DOMContentLoaded；不拦截资源、不禁用缓存、不做身份伪装。
- iHub 列表日期和相对日期只标记 DATE。正文 JSON-LD 中匹配标题、含时区的 `datePublished` 可升级为 EXACT。Investing 列表更新时间由正文首次发布时间校正；最终仍经过原有实时窗口验证。不采用 `dateModified`，不把抓取时刻冒充发表时刻。
- 保留生产原有 `site_budget_deferred` 的有限重试补丁。其他站点的正文识别、登录治理及 Runtime 不变。

## Runtime 实测与选择

Managed Playwright 首先完成尝试。GlobeNewswire 正常；iHub / Investing 曾分别出现 HTTP 200，但后续同路线出现 403 或浏览器超时，不能据一次正常页面将它们判定为稳定。External Chrome 的既有 NL 出口身份 `digitimes-nl-1` 验证可读两站。

生产 Chrome Supervisor 已占用配置的六实例容量；本轮未为了新增站点不断增加常驻 Chrome。新增两站只增加到既有 Identity 的 Site 映射，不更改该 Identity 的 Profile、出口、环境或 DIGITIMES 的站点策略。共享 Identity 的并发与访问节奏由现行治理统一管理；每次采集使用隔离页面，不共享页面任务。没有复制 Cookie、自动输入密码、自动解 challenge 或绕过订阅正文。

临时创建的两个独立 External 测试 Identity 因容量限制停用并移除对应新站点的组合引用。一次配置引用未清理导致 Site Access 校验失败，已经清理并恢复；没有重建原有 Chrome / Profile。后续部署只更新 Message Bus、正文补全和 Site Access，Supervisor 不重启。

## 可重复的部署与验收

`deploy/Dockerfile.news-source-update` 从每个服务现行镜像分别派生，只覆盖本轮九个业务文件；不将并行开发中的 SDK、交易或前端修改带入这些服务。`deploy/docker-compose.market-news.yml` 指定三个已测试镜像，作为现有 production / server / source-repair overlay 的最后一层。

`eval/market_news_sources_20261002/enable.py` 分离权限：

1. Site Access 容器运行 `--apply --sites-only`：通过既有管理员 API、revision CAS，仅更新三个新站点正文策略、选定共享组合与 GlobeNewswire 额外间隔。
2. Message Bus 容器运行 `--apply --bindings-only`：版本化登记四个源、补齐 RUNNING ticker 默认绑定以及 MU / INTC / BE 行业 RSS 订阅。

管理员令牌不传入 Message Bus，脚本不打印秘密。重复执行不会复活 tombstone 或重复创建订阅。部署前后比较 Supervisor instance_id / pid，验证原有 Chrome 连续性。

`eval/market_news_sources_20261002/smoke.py` 为只读真实验收，返回实际采集数量、URL、日期、正文提取方法与长度，不将旧样本发布入生产总线。正文使用生产 `body_v2.2` 提取器而非独立 curl 成功来冒充验收。

### 已完成的开发阶段验收

- 定向回归 129 passed / 1 skipped，覆盖新源、默认订阅、查询隔离、正文发布时间与实时 admission、现行站点治理和双轨 Runtime。
- GlobeNewswire Search：micron 搜索抓到 10 条，真实新闻正文 6,557 字符。
- GlobeNewswire Semiconductors RSS：20 条，Applied Materials / Besi 文章正文 12,412 字符。
- Investing.com INTC：10 条；两篇真实文章正文 5,352 / 2,913 字符，External Chrome 浏览器 DOM 提取。验证列表更新时间与正文首次发布时间不同。
- InvestorsHub MU：8 条；原生 iHub Form 8-K 正文 13,831 字符；ADVFN 市场新闻另补专用选择器，生产最终复测见下文。

### 生产最终验收

本节在本轮实际部署、订阅确认和复测后补充；开发阶段的只读样本不代表已在实时消息流发布，也不等于长时间可用性承诺。
