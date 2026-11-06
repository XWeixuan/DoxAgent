# RSS HTTP + RSS 并行赛马开发与生产验收（2026-11-07）

结果：清单中 14 项已接入赛马（10 个普通源、4 个 IR 内部端点），5 项保留 RSS，原因逐项列出。最终运行版本 `db08791f` 已发布，完整 10 分钟内 360 次物理轮询全部成功，未出现双通道失败。

本轮保持外部消息源、ticker 订阅、频率、时间准入、分发及 ARTICLE_ONCE 发布语义。没有增加 Chrome、浏览器身份或常驻网页。

## 实现范围

已接入 10 个外部普通源、4 个企业 IR 内部端点，共 30 个 HTTP 入口。企业 IR 仍使用唯一的 `corporate_ir_press_releases` 外部身份。

| 原消息源/内部端点 | HTTP 入口及实际范围 | 结果 |
|---|---|---|
| unmannedairspace_counteruas | counter-uas-systems-and-policies、counter-uas-systems-tenders 两个栏目 HTML | 并行赛马 |
| register_software_cloud_security | 原 software、off-prem/off_prem、security 三个查询的官方 JSON API，限定字段，单次约 11 KB | 并行赛马 |
| nbis_official_actor_rss | **ClickHouse 博客** HTML、**Meta 新闻** WordPress API；不是 Nebius IR | 并行赛马 |
| airspaceforces_space | space 分类 ID=8 的 WordPress API | 并行赛马 |
| siliconangle_software | big-data=1505426、cloud=603、security=1507578 的 WordPress API | 并行赛马 |
| wccftech_hardware | topic/ai、topic/industry、department/hardware 三个栏目 HTML，读取 data-time | 并行赛马 |
| siliconangle_ai | ai 分类 ID=369 的 WordPress API | 并行赛马 |
| ans_nuclear_newswire | /news/ HTML，读取文章卡片 data-epoch | 并行赛马 |
| quantum_insider | daily/business=21、daily/national=9291、capital-markets=9288 的 WordPress API | 并行赛马 |
| power_magazine_energy | nuclear、data-centers、legal-regulatory、safety、distributed-generation、energy-storage-2、hydrogen 七个原栏目 HTML；保持 CEG 4 栏目、BE 5 栏目配置 | 并行赛马 |
| INTC / intc.press | /news-events/press-releases HTML，原页面时间按纽约时区解析 | 企业 IR 内部并行赛马 |
| MU / mu.ir | 当前 /news/default.aspx 前端调用的 Q4 GetPressReleaseList JSON API | 企业 IR 内部并行赛马 |
| BE / be.press | 当前 /press-releases/ 前端调用的 Q4 GetPressReleaseList JSON API | 企业 IR 内部并行赛马 |
| RKLB / rklb.ir | /news HTML，日期仅日精度；保留原 RSS 的秒级时间及原文 | 企业 IR 内部并行赛马 |

## 保留 RSS 的五项

这些入口均已做实际 HTTP 探测，不把 HTTP 200 当作实时性和业务接入成功。

| 来源 | 实测限制和取舍 |
|---|---|
| spacenews_rss | 首页与 WordPress API 的 Direct/既有美国出口均出现浏览器挑战 403。保留 RSS，不引入浏览器；其 RSS 当前也曾返回 403，这属于原有可达性问题。 |
| venturebeat_software | 原 orchestration/data/security 栏目返回 200，但 HTML 命中约两小时以上缓存；POST 遇 Vercel 挑战，轻量 API 未验证成功。保留 RSS。 |
| lakehouse_platform_official_rss | Databricks /blog 及 page=0 均命中约两小时以上缓存；原 Fabric **社区更新板块**的网页与公开 REST API 返回 Cloudflare 挑战。未擅自替换成另一个 Fabric 官方博客。 |
| aws_platform_marketplace_rss | AWS、awsmarketplace、IONQ 的 quantum-computing 三个原栏目均已探测。HTML 与 RSS 最新内容对应，304 条件请求可用，但 HTML 首次每页约 1.48 MB；RSS 使用 HTML 没有提供映射的 opaque GUID，直接混用会改变已有文章身份。原平均延迟仅约 0.31 分钟，本轮不为它改造身份规则；三个既有订阅均保留。 |
| ONDS / onds.press | /press-releases 可 HTTP 访问，但 HTML 的 /press-releases/detail/ID/短slug 与 RSS 的 /news/detail/ID/完整slug 不同。页面 canonical 仍是前者，不能直接视为同一已存文章身份。原 RSS 自带完整正文且零延迟统计样本，本轮保留 RSS，避免重复正文任务及发布身份迁移；没有假装已启用赛马。 |

## 执行机制

- 原 FEED / IR 工作单元内并发运行 HTTP 和 RSS，各自保存验证器、游标、差量和重试状态。
- 快通道验证成功后立即通过 `on_page` 持久化捕获，**不等待慢通道才接纳**。慢通道继续补足独有文章，不取消它来换表面耗时下降。
- 单通道失败仅记入分通道诊断；至少一条通道有真实、通过解析校验的网络响应即成功。两条均失败才记录 `HTTP_RSS_BOTH_FAILED`，UI 与周期状态均显示失败。
- HTTP 上限 15 秒、RSS 上限 30 秒，沿用现有 HTTP 总预算及站点连接池。自适应源并发最低 2，上限沿用配置；调度需求计入双通道实际成本。
- HTTP 继续检查 CDN 响应 age/date/stale 状态；验证过的入口使用分钟查询键绕开静态列表缓存，支持 ETag / Last-Modified / 304。API 限定列表字段，不下载文章正文。
- 日期精度不造假。HTTP 初次发现遵循原时间准入，仅额外允许有明确近期 publisher 修改时间的文章更新；不把切换传输方式当作批量重抓旧文章的理由。
- HTTP、RSS 共享文章下载/发布身份，分别记列表变化基线；HTTP 与 RSS 摘要、更新时间字段的表现差异不会反复抢占同一正文任务。保留原 RSS 基线及真实更新重新取得正文的能力。
- 企业 IR 的非目标端点、MU newsroom、RKLB updates 等原工作单元保持原配置；历史恢复仍走原恢复流程。

## 正文兼容性

正文解析、清洗、完整性判定与分发规则沿用现有实现。企业 IR HTTP 结果冻结原 endpoint recipe，保持 owner ticker、语言及文章身份约定。

MU 的 RSS 原样提供 HTTP permalink，而网页/API 提供 HTTPS：消息身份继续沿用原 HTTP permalink，仅网络正文请求升级 HTTPS，防止同篇文章拆成两项。

实际探测发现 MU、BE 的原正文 Direct/urllib 访问返回 403，而既有美国出口的 curl HTTP 返回 200。因此仅这两个端点配置 `body_http_route=us-standard-5`，复用现有轻量 HTTP 客户端并明确进入 BODY 预算；其原 RSS 仍使用原来的 Direct/urllib 请求。无新增浏览器，无额外多阶段重试。

## 测试与发布前证据

- 30 个实际列表入口的快照解析通过；30 个入口在生产网络只读 canary 中，HTTP、RSS 两条通道均通过真实 transport + 列表解析校验。
- canary 未向消息总线接纳文章、未改生产配置。HTTP 端耗时约 0.27–3.95 秒，RSS 约 0.13–3.94 秒；这只是请求/解析成本，**不是新文章发现延迟改善的测量**。
- INTC、MU、BE、RKLB 四个真实正文样本均为 FULL；约 53,807 / 3,516 / 33,439 / 4,742 字符。ONDS 原文对照样本也为 FULL。
- 318 项发现、调度、正文、IR、UI 投影相关回归测试通过。随后包含赛马摘要差异、明确更新、跨源发布去重的 74 项补充回归通过；生产形态的绑定必填 RSS 参数迁移回归另通过 1 项。
- 真实页面证据、HTTP 缓存头、解析快照、canary 结果保存在 `eval/rss_http_race_20261107`；生产诊断副本位于 `/data/diagnostics/rss-http-race-20261107/`。

生产发布与完整观察已完成，见下方最终验收。

发布运行代码版本 `d0ef9a34`，2026-11-06 21:25 UTC 完成四个组件发布。启用配置后已核验 11 个外部源、28 个绑定的订阅、轮询、正文及监测词语义均无意外变化。迁移脚本针对生产中“仅 binding 配置必填 rss_urls”的形态补充兼容，采用已审核的原 feed 作为默认值；全部绑定原参数保留。

## 首轮观察后的修正

首轮 21:28 UTC 起的持续观察未出现双通道失败，但 The Register 的非唯一查询键随后命中旧 CloudFront 响应，缓存检查正确拒绝该 HTTP 列表，RSS 持续接替。增加分钟缓存键后，两次探测均为 MISS、Age=0、当前 Date，约 11 KB。未放宽缓存时效阈值。

同时修复赛马包装器的审计缺陷：只有未失败且通过解析的通道才能获得 `list_validated`，失败的 HTTP 200/304 不会因为传输完成而变成有效列表证明。新增回归覆盖此场景。完成修正后重新发布并重启完整 10 分钟观察窗口。

## 最终生产验收

- 最终代码：`db08791f`；Collector、Message Bus、正文补全 Worker、API 四个组件均运行该版本，运行状态正常、重启计数均为 0。
- 发布完成：2026-11-06 21:36:48 UTC（北京时间 11 月 7 日 05:36:48）。
- 连续观察窗口：**2026-11-07 05:37:30–05:47:30 北京时间，完整 10 分钟**。
- 360 次物理轮询全部 `CAPTURED`，HTTP 360/360 通过校验；RSS 336/360，通过 HTTP 接替的 24 次失败均来自企业 IR 的间歇性 429。没有双通道失败、没有源级阻塞，The Register 缓存问题未复现。
- 有效列表最大返回间隔：普通源 61.572 秒、企业 IR 30.717 秒，符合保留的 60/30 秒周期。
- API 实际状态投影中，28 个相关 ticker 绑定均为 succeeded；生产配置复查及幂等复跑通过，订阅、轮询窗口、正文规则、监测词和监测状态没有意外变化。

以下均按物理轮询去重，不将 shared distribution 的 ticker 扇出重复计数。耗时单位为秒；“首列表”从调度发起到最先通过校验的列表响应接收，“整轮”包含另一条通道完成和本轮处理。它们是执行成本，不是 publish-to-capture 的长期延迟分布。POWER 的公共栏目仍按原 CEG/BE 分组调度，因此 7 个不同 URL 对应 9 个 ticker 工作单元。

| 消息源 | 物理轮询 | HTTP 有效 | RSS 有效 | 首列表均值 / P95 | 整轮均值 / P95 | 最大列表间隔 |
|---|---:|---:|---:|---:|---:|---:|
| unmannedairspace_counteruas | 20 | 20 | 20 | 0.593 / 0.745 | 0.959 / 1.462 | 60.553 |
| register_software_cloud_security | 30 | 30 | 30 | 0.421 / 0.659 | 0.931 / 1.326 | 60.367 |
| nbis_official_actor_rss | 20 | 20 | 20 | 0.556 / 0.911 | 1.452 / 2.069 | 60.458 |
| airspaceforces_space | 10 | 10 | 10 | 0.480 / 0.595 | 0.669 / 2.029 | 60.197 |
| siliconangle_software | 30 | 30 | 30 | 0.371 / 0.615 | 1.012 / 1.470 | 60.258 |
| wccftech_hardware | 30 | 30 | 30 | 0.350 / 0.766 | 0.886 / 1.614 | 60.976 |
| siliconangle_ai | 10 | 10 | 10 | 0.380 / 0.556 | 0.886 / 1.461 | 60.252 |
| ans_nuclear_newswire | 10 | 10 | 10 | 1.082 / 1.316 | 1.711 / 2.309 | 60.292 |
| quantum_insider | 30 | 30 | 30 | 0.798 / 1.137 | 1.150 / 1.892 | 61.572 |
| power_magazine_energy | 90 | 90 | 90 | 0.891 / 1.409 | 1.552 / 2.178 | 60.886 |
| corporate_ir_press_releases | 80 | 80 | 56 | 0.251 / 0.662 | 0.396 / 0.932 | 30.717 |

观察窗口内出现 1 篇 The Register 自然新文章：官方发布时间 21:39:23 UTC，HTTP 首次发现并接纳于 21:39:39.350 UTC，延迟 **16.35 秒**；HTTP/RSS 产生的两次捕获均被接纳到同一文章，最终只有 **1 次正文下载、1 次 FULL、input_revision=1**，没有被两种列表的摘要差异反复补全。后续按原分发规则执行。这是单篇业务样本，不能据此承诺全部来源的长期延迟改善幅度。

资源采样：整个 Collector（含原有其他消息源）为 373.9–568.2 MiB，结束时 462.5 MiB；正文 Worker 148.9–230.6 MiB，Message Bus 144.2–156 MiB，API 约 474.5 MiB。采样不是隔离的增量基准；没有新增浏览器进程或身份。

测试汇总：318 项初始相关回归通过；正文复用/跨源去重补充回归 74 项通过；修正后采集与状态回归 147 项通过；合入并行维护后 50 项通过；最终仅测试/脚本整理后赛马回归 49 项通过。这些批次存在重叠，不累加为独立测试数。

验收证据：`eval/rss_http_race_20261107/observation-final.json`、`acceptance-extra.json`、`live-sample.json`、`ui-projection.json`、`configuration-check-final.json`、`runtime-final-end.json`、`release-receipts.json`。首轮发现问题后已完成修复与第二次完整观察，无需再次发布。
