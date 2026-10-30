# Yahoo Finance 实时采集优化与验收

日期：2026-10-31，北京时间。现状依据为 `yahoo-finance-source-current-state-20261031.md`，本文件记录本轮实际测试、实现及验收边界。

## 核心结论

轮询执行快，并不代表拿到了及时、完整的列表。本轮确认的限制包括：页面首屏只有约 12 条；没有消费现用 Content Gateway GraphQL；过滤了 `/m/UUID/…` 链接；缺精确时间的卡片必须先补时间才能输出。历史十分钟延迟不能全部归因于某一个限制。

GraphQL 可独立于浏览器运行。采用 Yahoo 自身前端使用的公开 Content Gateway：`https://nexus-finance-prod.media.yahoo.com/`，真实 GraphQL 查询 `GetQSPLeafNewsStream`，列表 `finance-US-en-US-ticker-all`，按 ticker 请求。仅发送 `queryRef` 会报 `MISSING_QUERY_STRING`，不能当作有效接口。

生产直连返回 429；已有固定 Yahoo HTTP 代理出口可返回 200。实现沿用这个出口，不切换 Clash 节点、不借用或修改 Chrome 的 cookie、Profile、身份配置。

## 对照测试

证据位于 `eval/yahoo-realtime-20261031/`。所有外网 canary 均只读，不向生产补写历史文章或伪造发布时间。

### 生产出口十 ticker 快照

10 个 ticker 均取得 GraphQL 50 条、HTML 12 条及有效 RSS。该快照 GraphQL 平均执行 1.30 秒；另一次使用**最终开发查询**和已安装 YahooTransport 的 canary，10/10 成功，各返回 50 条。

| ticker | GraphQL | HTML 首屏 | RSS | RSS UUID 同时在 GraphQL 中 |
| --- | ---: | ---: | ---: | ---: |
| INTC | 50 | 12 | 14 | 14 |
| MU | 50 | 12 | 18 | 17 |
| COHR | 50 | 12 | 19 | 19 |
| NBIS | 50 | 12 | 18 | 18 |
| BE | 50 | 12 | 19 | 18 |
| CEG | 50 | 12 | 17 | 17 |
| SNOW | 50 | 12 | 17 | 17 |
| RKLB | 50 | 12 | 20 | 20 |
| ONDS | 50 | 12 | 19 | 19 |
| IONQ | 50 | 12 | 20 | 20 |

179/181 个 RSS UUID 在 GraphQL 首 50 条中；两个 RSS 独有项证明不能取消原路径。这个静态快照中，没有观察到“30 分钟内新稿仅在 GraphQL、尚未进入 RSS”的样本。**接口耗时、列表数量与覆盖重合度，不能替代首次发现/接纳延迟改善的证据。**

### 真实浏览器与同源 fetch

使用本地一次性 Playwright 浏览器对照，不连接或变更生产 Profile。INTC、MU 导航首屏与同一时刻同源 fetch 的 HTML 都是相同的 12 条；MU 滚动后变成 72 条，出现原先没有解析的 60 条。INTC 在本次有界滚动内仍为 12 条。

实际浏览器网络使用 `FinancePolarisTickerListNews`，响应为 `lightyearList.main.edges[].node.asset`，分页为游标。旧实现只监听 `/xhr/ncp?queryRef=latestNews`，不会消费这个真实响应。现已支持并严格检查对应 ticker，仍保留原 NCP/DOM 恢复兼容。

这些样本证明首屏与动态列表存在覆盖差异；尚不能据此断言所有历史延迟都来自 SSR 缓存。普通 HTML 实测 `private, no-cache, max-age=0`；GraphQL 列表声明约 60 秒缓存，RSS 约 300 秒。GraphQL 加 `no-cache/no-store` 的三次短探测未取得可证明更及时的列表，因此不引入缓存破坏参数或随机请求。

### 相对时间的关键语义

真实 INTC 样本 UUID `99440181-9289-4bde-9418-a2eb5783a09e`：页面显示刚更新，接口 `pubDate=2026-10-30T13:44:49Z`，`displayTime=2026-10-30T20:33:18Z`。两者相差约 6 小时 48 分钟。页面“几分钟前”有时是更新时刻，不能认定为首次发布时间，更不能用它证明新稿延迟已降低。

## 已实现

1. 同一外部 Yahoo 源保留三个独立 HEAD：RSS `HEAD`、浏览器 `SUPPLEMENT_HEAD`、HTTP `GRAPHQL_HEAD`。每个 ticker 沿用原周期，失败、退避、执行池与相位独立，不等待另一路结果。RSS 保留兜底覆盖；不是每个 GraphQL 请求失败后再串行启动 RSS。
2. GraphQL 每轮获取 50 条，省略图片、广告、推荐模型及正文。200 响应中的 GraphQL errors、坏结构不会计为空列表成功。没有新的浏览器、Profile、持久页面、服务或 DB 表。
3. GraphQL 复用现有 Yahoo 专用线程，增加一个容量为 1 的 HTTP 会话/预算；与 RSS 的容量为 1 的实际预算隔离。避免 RSS 的网络等待占住新接口；沿用现有 HTTP 总预算、429 退避和固定代理。
4. 已运行 ticker 新增 GraphQL 路径时继承现有 HEAD 的 bootstrap 状态。不会把第一轮真实新稿错误地作为初始化历史压掉；接纳仍检查首发时间的新鲜度。
5. 页面 HTML 和 DOM 同时接受安全的 Yahoo `/m/UUID/…` 卡片、原生 `<time datetime>` 与 `.publishing` 相对时间。相对时间新稿 `max_probes=0` 即可输出，不入 `TAIL:YAHOO_TIME` 等待正文访问。
6. `RELATIVE` 保存时间区间、原始标签、观测时间和粒度。“2m ago”按 2–3 分钟区间的最早发布时间保守检查 30 分钟门槛。SSR 时间以响应 Date 为参考，避免旧缓存相对标签反复变“新”。无法解释的标签保留补查；明确过期标签不投机下载。
7. 历史 CLOSED_SWEEP 不接受 RELATIVE 作为精确区间证明；浏览器补扫禁用相对时间放行。正文成功后用 publisher 的 `datePublished` 等事实升级 EXACT，保留列表估算证据；如果真实首发已经过期，仍按原规则拒绝首次接纳。
8. `TAIL:YAHOO_TIME` 保留为未知时间的辅助任务，不再是刷新覆盖的 required 单元。文章元数据任务不会因没有列表请求而被误判 `UNPROVEN_LISTING`，也不会续期 HEAD 列表新鲜度。
9. 估算时间及每轮年龄标签不进入业务指纹；下游 business publication time 不把 RELATIVE 当精确时间。消息界面明确显示“发布（估算）”，补齐 EXACT 后恢复普通标识。
10. GraphQL 规范 URL、RSS wrapper、页面卡片依据 Yahoo 原生 UUID 在 live 正文工作建立前合并，包含两路同时 prepare、随后分别提交的竞态。各原始 URL 的更新 hint 仍独立，避免 RSS/GraphQL 不同摘要在每轮互相覆盖并反复抓正文。保留 ARTICLE_ONCE 和真实内容修订语义。

## 验证

- 专项覆盖：独立请求/退避、RSS 挂起时 GraphQL 不等待、真实两种 GraphQL 结构、原生浏览器 ticker 过滤、相对时间与缓存年龄、wrapper、正文 EXACT 升级、去重、并行首次录入、bootstrap 继承。
- 已有 live BODY/alias/Read 回归的定向集合：12 passed；前端 TypeScript 检查和 foundation 11 项通过。
- 1262/1559 两种桌面宽度实际浏览器检查了估算标识，截图已检查，无横向溢出。此为合成 UI 契约场景，不冒充自然新闻验收。
- 原始较大回归 153 passed、1 failed。失败为 `test_prepare_failure_does_not_claim_or_mutate_pending_task`，涉及 RuntimeJournal 的 claim/prepare 顺序；从未修改的 `d4ae2324` HEAD 导出源码后同样失败。本轮不改变该运行时机制。
- 本地 C 盘空间不足曾导致后续测试建库失败；已将本轮基线副本转移至 D 盘，并在 D 盘独立临时目录重新执行相关回归。环境失败不计作功能通过。
- 最终 D 盘复测：**177 passed, 1 deselected**（上述已证实的 Runtime 旧失败）；最终精确时间/页面专项 **30 passed**。回执为 `verified-regression.xml`、`exact-final.xml`。未把先前中断的扩大测试算成通过。

## 发布与自然消息时延

功能提交 **412d5cfad2890877ed5b04dcb56f7d67b927109d** 已推送 `origin/main` 并同步生产仓库。通过标准 `storage_release.py` 的提交归档构建、组件发布、运行检查与资源登记部署，未把本地其他会话未提交的 W3/回测修改带入镜像。

北京 05:01–05:03 依次切换 API、Projector、Site Access、Bus、BODY、Collector、Web；Collector 于 **05:03:26** 启动。七组件运行、重启计数均为 0，带健康检查的组件 healthy；六份关键模块的运行文件 SHA-256 与提交一致。Chrome Supervisor、身份、Profile、代理选择和 ticker 绑定/周期配置均未作变更。原九 ticker 仍为 30 秒，IONQ 沿用原 60 秒。

`release-receipt.json` 记录实际镜像与代码校验。最终窗口 **05:03:26–05:11:05** 的自然轮询（包括失败）为：

| 路径 | 完成轮次及结果 | 执行均值 | 成功轮询间隔均值（含 IONQ） |
| --- | --- | ---: | ---: |
| GraphQL | 142 CAPTURED / 1 FAILED | 0.63 秒 | 31.59 秒 |
| RSS | 146 CAPTURED | 0.53 秒 | 31.48 秒 |
| 页面 | 145 CAPTURED | 1.02 秒 | 31.27 秒 |

唯一 GraphQL 失败为 BE 的 `RequestError`，21:04:27Z 发起，约 1 秒失败；下一正常轮次恢复，未拖住其他 ticker、RSS 或页面。它导致 BE 成功间隔约 60 秒，不能从统计中剔除。这里尚无更底层网络错误码证据，不把它武断写成 TLS、限流或接口 schema 故障。窗口结束全部十 ticker 三个 HEAD 均 succeeded。

一次部署后进程资源快照：Collector 263 MiB、Bus 166 MiB、BODY 183 MiB、Site Access 2.46 GiB，均为整个组件，不是 Yahoo 增量，也不是峰值或长期均值。标准资源回收因当时 CPU 压力暂缓，登记无错误；新三份业务镜像均被运行组件引用，未清理业务内容、卷或 Profile。

该窗口**没有发布于切换后、可核实精确首发时间的真实新稿样本**，因此不能宣称“发布时间至首次发现/接纳已从约 9 分钟降到 30 秒”，也不能把历史更新稿重放作验收。已确认的验收结论是：真实第三路径上线并持续取到有效列表、精确时间补查不再阻塞新鲜相对时间卡片、三路去重且独立运行。真实新稿端到端降幅仍缺样本。

`verify_production.py` 只读核对首次 transport/listing 观测、capture 持久化、live 接纳、FULL/SHORT_FULL、Stream 发布时间。注意 ACCEPTED capture 的 payload 会被正常清空；需要沿原 input_ref/原生内容及 pipeline_trace 回溯，不能把 `{}` 当作当轮没采集到文章。生产快照为 `production-verification.json`、最终 `natural-final.json`，只比较真实首发新稿；更新稿、部署前已出现的文章和估算时间另列。
