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
|spacenews_rss|RKLB|by_distribution；原 RSS，固定美国出口 + 标准库 TLS 读取列表和正文|
|ssc_newsroom|RKLB|by_ticker；原 Newsroom HTML，美国 HTTP 出口|
|nasa_2026_releases|RKLB|by_distribution；官方同年 press-release API|
|dod_contracts|RKLB、ONDS|共享每日合同 RSS，完整正文后分别判断相关性|
|dod_releases|RKLB、ONDS|by_distribution；用户 RSS 所指向的官方 HTTPS ArticleCS ContentType=9，德国 HTTP 出口|
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

### 候选镜像验证后的修正

- 首轮候选实际运行中，Breaking Defense 和 Calcalist 被通用解析器选中侧栏短文，分别误报 169/271 字符 SHORT_FULL。已明确选择主正文 `post-single__content` / `ArticleBodyComponent`，缺失主正文时失败，不回退到侧栏；新增冲突样本回归。
- Army 的“查看原图”图片链接此前被泛用门禁误判为原文外跳，现明确排除图片资源链接，保留真正原文跳转门禁。
- DoD Releases 旧 URL 301 到 HTTP ArticleCS **ContentType=9**；采用同一官方终点的 HTTPS 版本并增大至 50 条，未放开 HTTP 降级或任意主机跳转。
- SpaceNews RSS 的 Video/Podcast 项显式标记媒体，介绍文字不能算完整采访逐字稿；没有 transcript 时保留 unsupported_media。首轮快速重复探测触发一次 429，不能算稳定性通过；后续以合理间隔重测。
- 生产读取发现 RKLB、ONDS 原本没有统一词表，因而配置工具也支持仅在缺失时创建初始词表；已有词表不覆盖。可审阅的完整规则为 `scripts/ops/defense-monitoring-terms-20261024.json`，RKLB L1 为 Rocket Lab，ONDS 为 Ondas / Airobotics / American Robotics。L2 监测公司、已核实业务公司及带发射上下文的产品名称，不将泛行业词单独设为正则命中。公司/产品依据 [Rocket Lab 官网](https://rocketlabcorp.com/)、[Ondas 业务公司](https://www.ondas.com/our-business-units) 及 [Ondas 官方披露](https://ir.ondas.com/press-releases/detail/282/ondas-announces-10-million-strategic-investment-in-world)。统一词表也供原有已订阅搜索来源使用，没有改动其绑定参数或开关。

修正后的相关完整回归 **139 项通过**；补充视频完整性与 RSS 摘要清理后，消息源专项 **32 项通过**。配置和质量改动继续使用既有 Owner、准入和审计路径。

### 接纳成本与限流

候选实测表明，NASA/Army/Shephard 等返回 200 时会重复带回数十条原记录。最终适配器为每条记录保存内容指纹，变化立即接纳，未变化记录五分钟强制重送一次以保留重试和复查；请求仍按 60 秒目标执行，新消息不等待五分钟。强制重送时不发送条件校验器，closed sweep 始终完整处理返回记录。NASA 采用正文文本 hash 作为 provider version，纯 modified 时间变化不触发正文更新。新增相关接纳/分发回归 **44 项通过**，最终源专项 **34 项通过**。

HTTP 429 保存真实失败，并按 Retry-After（至少 60 秒）延期，不紧循环或切浏览器尝试。正文阶段也把 Retry-After 交给原有有限重试预算。SpaceNews 的视频/播客与付费墙等边界在候选结果中单列，不能混入普通新闻全文通过率。

### SpaceNews 路径补充

后续受限探测验证：固定美国/德国出口的标准库 TLS 均能读取 RSS，而不是使用受拦截的 Chrome TLS；选择固定美国出口用于正式列表和正文。普通 DARPA 新闻样本返回 HTTP 200、FULL、3540 字符；此前 1051 字符样本是视频简介，不计入普通新闻正文通过证明。原标准库传输增加可选 approved proxy 参数，旧 IR 默认直连行为保留，相关 IR/消息源回归 **39 项通过**。
