# Business Wire HTTP 重试与 Newsfile 保持 RSS（2026-11-10）

本轮遵循 HTTP 优先、必要时 Playwright 再 CDP 的顺序。**已找到可工作的低成本 HTTP 路径，因此未启动或部署浏览器。没有使用任何 sitemap 作为主源、兜底或平替。** 五源仍按既有 Massive Distribution 测试隔离运行，不增加正式订阅或向 Message Bus 下游发布。

## Business Wire

- 普通 `GET /newsroom` 仍403。只读 `POST /newsroom?page=N` 成功；增加网站 Next.js 页面导航采用的 `RSC: 1` 头，可直接取得 `text/x-component` 结构化服务端列表，约346KB，比完整HTML约626KB更小。
- 使用不带 `language` 的全语言列表，避免把原全语言RSS静默缩成英文。响应 `Cache-Control: max-age=0, no-cache, no-store`，没有Age、ETag、Last-Modified。**这些头不等于零发布/索引延迟**，另做新稿与原RSS对照。
- 前端 newsroom JavaScript 的筛选/分页实际用路由参数更新；可读数据在RSC的`news.items`中，包含官方`newsItemId/language/releaseDate/headline/teaser`。没有调用保存搜索、邮件设置或登录的Server Actions，也不需要维护构建版本相关的action hash。
- `page`已验证有效；limit/pageSize/rows=100均仍10条，未把它们当作扩窗手段。默认只请求第一页，有突发或恢复需要时按需翻页，最多8页、HTTP总预算12秒；页码未实际变化、页面结构/缓存异常会明确失败并保留原RSS兜底。
- URL按网站前端公开的slug算法生成，保留实际文章地址供BODY访问。实测RSS与前端会为同一公告生成不同slug（例如`AU2.1`与`AU%242.1`），因此只对`businesswire_massive`使用官方公告ID+语言作为文章身份，不能仅依赖尾斜杠标准化。RSS/HTTP只保留稳定标题为内容版本线索，将列表摘要保留在原始证据中，避免两路摘要格式差异制造重复BODY任务。正文抓取方式、质量判定、测试BODY预算不变。
- 原Google News sitemap仅做过诊断，存在数分钟缓存；用户补充要求后已明确排除，**生产代码没有加入任何sitemap路径**。已有Bloomberg自身的原sitemap配置不属于本轮修改范围。

生产网络内试跑新适配器：首轮3页、14条近30分钟候选、约2.683秒；重复轮1页、0增量、约0.520秒。该试跑尚不能证明长期稳定或所有新闻已被及时发现，周期性观察另记。

## Newsfile

将 `tmx_newsfile_massive` 明确配置为 `discovery_mode=rss`、kind=api，保留原官方RSS、30秒周期、10个测试Ticker、正文方式与隔离。正常RSS读取不再冒充“HTTP失败后的兜底”，不再生成恒定的HTTP失败状态。

初始化默认值与既有 `configure_newswire_http.py` 同步遵循这一选择，避免以后重跑配置脚本又把Newsfile改回受阻爬虫。其他源的配置保持不动。试跑RSS首轮8条、约1.145秒，重复轮0增量、约0.245秒。

## 验证与发布

相关回归覆盖真实BW RSC数据、全语言、精确时区、官方URL/原RSS文章键和BODY版本签名一致、请求头与只读POST、无效翻页失败且保留首屏、Newsfile不调用爬虫/浏览器、配置幂等及原隔离路径。

54项相关测试通过，1项依赖可选历史4078条样本的测试跳过；原有Yahoo字符串转义警告不属于本轮。相关修改通过Ruff E9/F检查。测试还覆盖旧FULL正文保留、两路不同slug不新增正文任务、标题实质更新触发既有owner的第二次输入修订。

### 新稿时效对照

在生产网络中于北京时间06:03:30—06:08:31进行11轮HTTP与11轮RSS对照，两路均没有请求错误。排除首屏已有文章，只统计观察期间新发布且后来首次出现的文章：

| 官方公告ID/语言 | 发布时间（UTC，11月9日） | HTTP首次出现（UTC） | 发布时间至首次观察 |
| --- | --- | --- | --- |
| 20261107593861/de | 22:04:00 | 22:04:31.637 | 31.637秒 |
| 20261109437926/en | 22:06:00 | 22:06:31.677 | 31.677秒 |
| 20261109661464/en | 22:07:00 | 22:07:31.659 | 31.659秒 |

3篇在观察结束时均未进入RSS；RSS直到22:08才返回部分22:00/22:01旧稿。RSS观察为右删失，不能编造“平均领先RSS多少秒”。上述约31.7秒是30秒采样下的首次观察时差，包含采样等待，并非网站内部索引耗时。样本少，不能据此宣称全天覆盖或长期P95达标；但已经验证列表确实新增，入口并非只返回快速、滞后的缓存页。

正式新路径运行后，排除冷启动补获，另有实际新稿记录：

| 来源/官方ID | 标题 | 发布至首次入账 |
| --- | --- | --- |
| BW / 20261109331701 | Square Nine Leads $206 Million Financing for Aline Accounting Partners | 54.499秒 |
| BW / 20261109555922 | California IBank Reaches Milestone, Connecting Small Businesses to More Than $5 Million in Private Lending | 54.490秒 |
| Newsfile / 323187 | Go Metals Announces Non-Brokered Private Placement | 16.585秒 |

两篇BW样本平均54.494秒，与上述独立接口对照的31.7秒属于不同观测窗口；不能挑选其中更低者作为稳定承诺。首次入账指现有live_articles测试链路接纳，并非正式Bus发布时间；这三篇后续正文均为`UNAVAILABLE/deadline_exceeded`，没有取得真实完整正文，不将其描述为端到端FULL成功。

### 生产部署与身份保留

- 代码已提交`origin/main`：初次路径变更`3cd9fb09`，身份修正`8c203e62`。北京时间06:30完成Collector、Message Bus、Content Enrichment三角色标准镜像发布；后者同步文章键计算，未改变正文下载方式。三角色均在运行、未OOM；发布无错误，旧镜像清理因资源保护延期。
- Newsfile现有配置更新为v3 RSS；BW保留v2 HTTP配置。均为30秒周期、测试模式、原10个Ticker。对其他三家Newswire的配置此次不做改动。
- 运维配置幂等执行，为4,007个既有公告身份写入现成canonical/origin别名，优先保留FULL/SHORT_FULL，否则保留最早owner。历史重复审计行保留，不把历史去重数量冒充新增收益。
- 发布后再次读取HTTP前三页30条与RSS 4,408条，共有28条，其中3条URL不同；28条文章键、内容版本签名全部一致，全部存在既有owner映射。读测不写入消息或制造新的BODY任务。
- 配置前后四项控制摘要完全一致：323个正式绑定、40个监测词版本、103个其他消息源配置、1个默认profile。五个测试源正式raw/standard行数、正式绑定数及模型决策数均为0。

### 验收边界

本轮修复新消息发现路径和两路文章身份；没有把列表成功当成正文成功。BW历史正文仍有大量`UNAVAILABLE`和限流/预算等待，现有FULL仅少量；继续沿用原正文方式与测试BODY限额，不因发现更快就宣称正文质量改善。首次切换补获的旧稿不计入稳态新稿延迟。分页上限80条、12秒是资源边界，突发超过此范围或上游结构变化仍可能导致明确失败并使用RSS兜底；兜底不能清除HTTP失败状态。Newsfile则是用户明确选择的正常RSS源，状态不应被标作HTTP失败。

### 最终版本连续生产观察

北京时间06:30:15—06:40:31，共616.954秒，按物理采集run去重，不把10个Ticker的回执算作10次请求：

| 来源 | 有效物理轮询 | 失败/降级 | 单轮平均 / P95 / 最大耗时 | 平均调度间隔 |
| --- | --- | --- | --- | --- |
| Business Wire HTTP RSC | 21/21 | 0 / 0 | 0.917 / 1.816 / 2.352秒 | 29.999秒 |
| Newsfile RSS | 21/21 | 0 / 0 | 0.570 / 1.324 / 1.842秒 | 30.011秒 |

该窗口BW每轮仅一页，无需新增Chrome进程或身份；突发翻页与冷启动恢复另由适配器试跑及回归覆盖。以上为短期稳定性验收，不等于长期可用性保证。正式订阅、监测词、其他消息源、profile控制摘要在结束时复核仍一致，测试隔离指标仍全部为0。

机器可读汇总：[验收JSON](businesswire-http-followup-and-newsfile-rss-20261110.acceptance.json)。原始请求、周期及发布日志保存在本轮隔离工作树`eval/businesswire-followup-20261110/`，未将数MB原始RSS或全量生产审计数据提交到仓库。
