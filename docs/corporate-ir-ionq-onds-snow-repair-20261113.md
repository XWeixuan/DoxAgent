# 公司 IR：IONQ 接入、ONDS / SNOW 修复与并行渠道检查

日期：2026-11-13，北京时间。代码提交 `1f108da39`，生产构建 `77ae9cfc237af52ef5ff0d86df1ffa0776db5eba`。

## 已交付

继续使用唯一外部源 `corporate_ir_press_releases`（公司 IR / 新闻稿聚合），生产版本由 4 升至 5，内部端点由 12 增至 13，已绑定 ticker 由 9 增至 10。新增 `ionq.press`，自动进入原有发现、去重、正文质量检查及消息发布链路；没有新增浏览器身份、服务、队列或数据表。

保留所有原有 binding 的完整轮询设置：BE 30 秒，其他原有 ticker 20 秒。IONQ 使用当前多数 IR ticker 的 20 秒。外部源默认值仍为 30 秒，实际调度以既有 binding 覆盖值为准。

## IONQ：HTTP 列表与正文

目标为 [IonQ 官方新闻页](https://www.ionq.com/news)。网页包含两个独立 Webflow CMS 集合：公司自己的新闻稿，以及第三方媒体报道。实现仅提取明确标记 `IonQ Press Release` 的公司新闻稿，不混入第三方报道；使用隐藏的机器日期和公司集合自身的分页链接。

生产入口为官网原生第一页 `https://www.ionq.com/news?251779c9_page=1`。第一页实测 12 篇，下一页 `?251779c9_page=2`；正文限定在 `.body-rich-text`，排除推荐、订阅等外围内容。真实样本取得 `FULL`，6,359 字符。官网只提供发布日期，因此保留 `DATE / day`，不会生成虚假的精确发布时间。

**缓存限制仍需明确保留：** 裸 `/news` 曾返回 Age 约 82,321 秒；原生分页请求曾 MISS，后续也可返回较大的 Age（最终正文验收时 76,872 秒）。`no-cache` 和测试参数未证实有效，POST 返回 405，因此未加入无效的缓存绕过重试。Age 高并不单独证明漏掉新公告：网页最近一篇为 11 月 10 日，页面 Last-Modified 为 11 月 12 日，本轮没有新发布事件可作首次发现对照。**列表、正文和调度功能验收通过；真实“发布→首次发现”的时效性尚未验收，不能把 20 秒轮询或 HTTP 请求耗时当作发现延迟。**

## ONDS：连接阶段失败与单 RSS 依赖

查询保留的 11 月 9 日以来采集记录，主要失败为 `ConnectTimeout` 和 `TimeoutError`，少量 `ConnectError`。近期样本失败在 HTTP transport、尚未验证列表，admission 等待为 0；证据不足以进一步归因为 DNS、TCP 或 TLS 中的某一个环节。近期 RSS 消息的正文已有 `FULL`，不是 RSS 内容格式普遍失效。

保留 [官方 RSS](https://ir.ondas.com/rss)，新增 [官方 HTTP 新闻列表](https://ir.ondas.com/press-releases)，使用既有 `http_rss_race` 并行机制：两边独立请求、检查点和错误，快的一边直接提交，不等待另一边。HTTP 列表约 29 KB / 10 篇，RSS 约 558 KB / 20 篇；保留并集以维持覆盖。两条路径均实测无 Age，服务器声明 no-store/no-cache。RSS 内已有的完整正文继续复用。

官网同一文章的两个实际链接不同：RSS 为 `/news/detail/341/<完整slug>`，HTML 为 `/press-releases/detail/341/<较短slug>`。按官方数字 detail ID 统一文章身份，显式 generic URL 不参与。上线时通过现有 `live_origins` 机制，为 5 个历史 owner 补 canonical alias；保留原记录、已有全文和投递历史，避免迁移触发重复下载或发布。回归覆盖旧 owner 被 HTTP/RSS 两种链接再次发现的情况。

这项修复消除了单一 RSS 请求失败导致整轮无结果的依赖；两个通道仍共享同站网络，不宣称能消除全部站点连接故障。

## SNOW：官方网页 403，改用前端实际数据接口

原 HTML 入口的失败样本为 HTTP 403，并有 Cloudflare `cf-mitigated: challenge` / “Just a moment...” 证据；生产直连的多个 HTTP 客户端及一个既有固定出口均复现。普通浏览器可正常显示官网，不能据此将问题简单归因为订阅权限或单一客户端。

从 [官网新闻页](https://www.snowflake.com/en/news/press-releases/) 的公开 AEM 页面模型及前端脚本，确认其真实列表接口：

```text
https://publish-p57963-e462109.adobeaemcloud.com/content/snowflake-site/global/en/news/press-releases/_jcr_content/root/responsivegrid/container/container/flexible_column_cont/flexible_column_content_container_1/filterable_resources.filter.json?offset=0&pageSize=12&orderBy=customPublicationDate&sortOrder=desc
```

生产网络 HTTP 200，约 13.4 KB，12 篇 / 总数 275；offset=12 返回另一组文章。响应 `Cache-Control: private`，无 Age。无需 Cookie、账号、Chrome 或新出口；没有使用页面内无关的 API key。日期仍保持 day 精度。

正文读取同一官方公开发布服务中的文章 `.model.json`，核对 canonical 与目标文章相同，只整合真实主标题和正文富文本组件，保留条款、数字、列表和表格，排除侧栏、页眉页脚及配置数据。真实最新样本取得 `FULL`，5,232 字符。采集诊断记录 `ir_public_aem_model` 和实际来源 URL，最终新闻链接仍为 `www.snowflake.com` 原文章。

本轮未用新增文章验证 AEM 相对官网的首次出现差值，因此这里只确认实际前端数据契约、分页、正文及持续轮询恢复，不声称已经测得新公告发现延迟。

## 全部 IR 通道检查

| Ticker | 内部端点 | 生效列表通道 | 周期 |
|---|---|---|---|
| INTC | intc.press | HTTP + RSS 并行，原来已启用 | 20 秒 |
| MU | mu.ir、mu.newsroom | IR API + RSS 并行；newsroom HTTP | 20 秒 |
| BE | be.press | IR API + RSS 并行，原来已启用 | 30 秒 |
| COHR | cohr.corporate、cohr.financial | 两个独立 HTTP 端点 | 20 秒 |
| NBIS | nbis.press | HTTP | 20 秒 |
| CEG | ceg.press | HTTP JSON | 20 秒 |
| RKLB | rklb.ir、rklb.updates | IR HTTP + RSS 并行；updates HTTP | 20 秒 |
| SNOW | snow.press | 官方前端 AEM JSON | 20 秒 |
| ONDS | onds.press | 本次新增 HTTP + RSS 并行 | 20 秒 |
| IONQ | ionq.press | 本次新增 HTTP | 20 秒 |

配置与默认种子均已补齐，不再有 RSS-only 的 IR 端点。`method=rss` 在五个并行端点中保留其原正文配方与 feed 身份，实际发现方式由 `http_rss_race=true` 控制，不是仍然 RSS 优先。已有 BE/MU RSS 通道存在 429/backoff，HTTP 通道正常；独立错误继续保留，不把单通道失败隐去，也不为此阻塞有效 HTTP 发现。

## 测试、发布与验收证据

- 124 项相关测试通过：IR 列表/正文/传输、五个 HTTP/RSS 并行配方、跨源文章 owner、正文结果、配置幂等与频率保留。Ruff E9/F 通过。
- 生产发布 Message Bus、Collector、Content Enrichment，并同步 API 的新端点类型定义，沿用正式镜像发布和资源回收流程；没有变更前端代码、Site Access 或 Chrome。四个组件均运行、restart count 均为 0；API 容器实际加载构建 `77ae9cfc237a`，成功解析 source v5 的全部 13 个端点并读取到已启用的 IONQ binding。
- 配置升级在原有 owner 事务中执行，已保存私有回滚快照 `/data/bus/ops/ir-upgrade-20261113/before.private.json`；公开激活回执随 observation 保存。配置 source v5、IONQ binding 已启用；三个真实正文检查均为 FULL。样本只读验收，没有将历史样本人工发布到 Bus。
- 刚切换 source version 时，COHR financial 和 NBIS 各记录一次 `COLLECTION_SOURCE_CONFIGURATION_CHANGED`；随后恢复。报告保留此发布窗口事件，不把它当作正常站点故障。
- 连续观察结果、两个通道的实际成功/错误及原始周期计数见以下回执。`CAPTURED` 表示采集轮次完成，包含无新消息/未变化的正常轮次，不代表新增新闻数。
- 首次约 166 秒观察，13/13 个内部端点当前均为 succeeded；BE 5 轮、MU IR 6 轮、其余 8–9 轮完成采集，IONQ / ONDS / SNOW 分别完成 8 轮。五个并行端点均有 HTTP、RSS 独立检查点；不把 BE/MU 的 RSS 429/backoff 误写为两边都成功。

证据：

- [真实列表与正文验收](../eval/corporate-ir-upgrade-20261113/production-acceptance.jsonl)
- [生产配置、连续轮询与双通道回执](../eval/corporate-ir-upgrade-20261113/production-observation.json)
- [API 构建与端点类型验收](../eval/corporate-ir-upgrade-20261113/production-runtime-api.json)
- [历史故障计数](../eval/corporate-ir-upgrade-20261113/historical-summary.json)
- [可重复执行的配置升级](../scripts/ops/upgrade_corporate_ir_20261113.py)

未完成的业务验收仅为真实新公告的发布到发现延迟及跨日稳定性；本次短观察不能替代它们。
