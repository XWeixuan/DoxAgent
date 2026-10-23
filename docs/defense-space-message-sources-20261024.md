# RKLB / ONDS 国防航天消息源实施记录

日期：2026-10-24。范围：用户列出的 18 个外部源、20 个栏目入口，RKLB 12 条订阅、ONDS 8 条订阅；不加入其他 ticker 默认模板。

## 实现

- 统一 `builtin:defense_public@1` 适配器，源仍分别展示。Aviation Week、DefenseScoop、Army、DoD 的不同栏目按主机共用 scheduler group 和 HTTP 限速；每站最多两请求，全局四请求，0.5 秒启动间隔，HTTP 超时 8 秒、单请求队列/执行预算 12 秒、正文响应上限 4MiB。
- RSS / JSON / HTML 全部用 HTTP，无新增 Chrome、身份、常驻页面或浏览器 context。不同端点通过独立任务和 checkpoint 抓取；一个 feed 失败不会丢弃健康兄弟结果。全失败报错，部分失败保存 acquisition failure 与 PARTIAL 覆盖状态。
- 每个 URL 独立保存 ETag、Last-Modified、解析成功基线和无日期文章的首次发现时间；304 需要有效基线。共享分发跨分钟 run 从已有 ticker poll state 继承同版本 checkpoint，闭市 sweep 不继承实时 HTTP 校验器。
- BODY 在独立补全 worker 中执行，不在列表轮询内等待。复用现有正文结果表和下载去重，不按 ticker 重复下载。公开正文遵守原 FULL/SHORT_FULL 质量门禁，摘要、搜索片段与付费壳不能冒充全文；不尝试 Reader 绕过订阅。
- NASA 使用同年 press-release 官方 WordPress API，显式限制 2026-01-01 至 2027-01-01；保留精确发布时间和官方完整 content 字段，完整性通过后免详情页请求。该源按用户要求固定 2026 年，跨年需更新年份。
- Calcalist 使用其页面声明的 Google CSE 公开 HTTP 接口，动态读取短期 token（缓存 15 分钟，不是用户凭据）、按日期排序；统一 L1 分查询，交替刷新头页并继续尾页。只接受 Calcalist 文章 URL 和其发布元数据。它受 Google 索引时延约束，不等于站点直发实时流。
- 为 ONDS 保留既有词表并补充希伯来语公司别名，新增 Hebrew 标题身份 token；可选源语言要求按订阅 ticker 生效，不让 Calcalist 迫使无关 ticker 增加希伯来语配置。
- Shephard RSS 缺发布时间时保持稳定 UNKNOWN_FIRST_SEEN；正文提供可验证精确发布时间后再校正，旧文章仍接受最终发布时间准入检查，不能用每轮抓取时间更新为新新闻。

## 入口与模式

所有源目标轮询间隔 60 秒，全天窗口；这是调度目标，不是已验收 SLA。

|源 ID|订阅|模式/路径|
|---|---|---|
|aviationweek_rocketlab|RKLB|by_ticker；Rocket Lab 专题 HTML|
|aviationweek_rss|RKLB|by_distribution；原 RSS|
|breakingdefense_space|RKLB|by_distribution；原 Space RSS，已验证德国 HTTP 出口|
|defensescoop_space|RKLB|by_distribution；原 Space RSS|
|airspaceforces_space|RKLB|by_distribution；原 Space RSS|
|spacecom_launches|RKLB|by_distribution；原 Launches & Spacecraft RSS，美国 HTTP 出口|
|spacenews_rss|RKLB|by_distribution；原 RSS，标准库 TLS 读取列表和正文|
|ssc_newsroom|RKLB|by_ticker；原 Newsroom HTML，美国 HTTP 出口|
|nasa_2026_releases|RKLB|by_distribution；官方同年 press-release API|
|dod_contracts|RKLB、ONDS|共享每日合同 RSS，完整正文后分别判断相关性|
|dod_releases|RKLB、ONDS|by_distribution；用户指定 RSS，德国 HTTP 出口|
|iridium_press_releases|RKLB|by_ticker；Iridium 原 Press Releases HTML|
|calcalist_search|ONDS|by_search；原页面 CSE，希伯来语|
|defensescoop_drones|ONDS|by_distribution；Drones 与 Counter-drone 两个 RSS|
|unmannedairspace_counteruas|ONDS|by_distribution；Systems/Policies 与 Tenders 两个 RSS|
|shephard_uncrewed|ONDS|by_distribution；原 UV Online RSS|
|army_news_releases|ONDS|by_distribution；原 Press Releases HTML，美国 HTTP 出口|
|army_news_rss|ONDS|by_distribution；原 Army RSS，美国 HTTP 出口|

**DoD 合同模式修正**：指定入口返回按日汇总的全军合同，并没有 ticker 参数，标题/摘要一般也不含承包商。实现用共享采集/补正文，再按两个 ticker 已有监测词分发，持久 acquisition_mode 为 by_distribution；避免将所有合同广播到两只 ticker，或仅筛标题而漏掉正文中的合同。SSC 与 Iridium 按用户指定的 by_ticker 订阅整份列表。

## 探测发现

原始输入 URL 保存在 `eval/defense_sources_20261024/probe.py`。生产实测需要区分入口和正文：

- 大多数 RSS/HTML 可直接读取。SSC/Army 在服务器直接 DNS 路径失败，通过已有美国 HTTP 出口成功；未关闭 TLS 验证。
- Breaking Defense 的 direct/美国路径和临时 headless 样本均被拦截；德国 HTTP 路径同时取得有效 RSS 和正文。DoD 原 Releases XML 也在德国 HTTP 出口成功。曾试探的 ArticleCS ContentType=1 是 News Stories，**没有用它冒充 News Releases**。
- SpaceNews RSS direct 可读；Chrome TLS 正文被拦截，标准库 TLS 的同一文章返回正常 HTML，因而选择固定标准库传输，无浏览器回退。
- Calcalist 原页面只是 CSE 搜索壳，不能把 HTTP 200 当作有结果。临时浏览器用于观察公开搜索请求，随后纯 HTTP token + 搜索请求已在生产 direct/美国/德国三个路径分别取得真实结果；正式采集不启动浏览器。详情页使用已验证德国 HTTP 路径。
- Aviation Week 的两个付费样本均明确 subscription_required。列表覆盖保留，但没有用户可用付费身份/权益时无法承诺这些文章 FULL；不会伪造成功或创建空身份反复尝试。
- 普通文章样本包括 DefenseScoop、Air & Space Forces、space.com、SSC、NASA、Iridium、Unmanned Airspace、Shephard，已通过真实正文质量检查。Army RSS 也会返回外部报道摘要类条目，应保留 source_summary_only，不能把它们当作完整官方文章。

## 配置、测试与发布

操作入口：`scripts/ops/configure_defense_sources.py --bus <bus.sqlite3> --receipt <path>` 默认只读；加 `--apply` 后由现有 Bus Owner 在一个事务内注册来源、保留旧词表并添加希伯来语、创建/启用对应订阅。重复 apply 不重复创建或提高无变化配置的 revision。

本地相关测试 135 项通过，覆盖真实精简 RSS/列表合同、端点失败隔离、304 基线、无日期首次发现稳定、共享跨 run checkpoint、闭市 sweep 隔离、希伯来语身份、20 条订阅幂等、官方 API 正文复用和付费墙拒绝，以及现有 BODY/分发回归。初版 NASA 测试把去重后的 SHORT_FULL 错预期为 FULL，已修正测试口径并全部重跑通过。

生产候选验证与实际启用回执见下方追加记录。没有以本地测试或一次 HTTP 200 宣称长期稳定；尚未出现的新文章和付费正文能力分别列出边界。
