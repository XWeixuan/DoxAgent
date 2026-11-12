# Bloomberg 订阅正文接入与验收（2026-11-13）

## 本轮边界

列表继续使用现有 Latest HTTP 与 Artemis HTTP 快照并行发现；不引入 RSS、Sitemap、SSE 或浏览器列表轮询。Bloomberg 继续为测试源，不新增正式订阅或下游消息。本轮复用 `bloomberg-latest-lab-1` 普通持久化 Chrome、原 Profile 和固定出口，只开发订阅正文读取。

## 已确认的机制问题

1. `SharedContentExtractor` 对所有 `_massive` 源强制走公共正文 pipeline，导致已登录身份没有用于 Bloomberg；pipeline 内部还有第二处 `_massive` 公共访问顺序覆盖。两处均为 Bloomberg 单独纠正，其余 Newswire 不变。
2. 原 Registry 为 generic 正文策略，未建立 Bloomberg 的可见正文完整性证据。Bloomberg 的订阅段落本身带 `paywall` 类；不能把该类当作墙删除，也不能直接把源 HTML 中隐藏的付费文本当作完整正文。
3. 历史 lab scope 和自动创建的 generic publisher scope 并存。必须显式把原 lab scope 作为该站默认 publisher，继续使用原授权身份，不能让新任务误用匿名 generic Profile。

## 实现

- 为 Bloomberg 登记 `builtin:bloomberg@1`，BODY 仅允许 browser，并使用已有 CDP 默认持久化 context。保留 external runtime 已有的 `no_defaults=True`，不修改全站 Chrome 启动参数。
- 导航等待 document commit，再等待文章 BODY 实际完成订阅加载；不等待广告造成的 DOMContentLoaded/networkidle。正文与完整性状态连续两次相同才输出，最多等待 15 秒且受任务剩余预算约束。
- 在当前已授权页面内读取 `article[data-story-id] .body-content`。检查段落实际可见性、祖先隐藏/裁剪、付费段落与真实访问墙。只复制可见正文，不解锁、不取消隐藏、不读取账户状态或导出 Cookie。
- 形成最小文章快照，携带 canonical、标题、精确 datePublished/dateModified、story ID 与可见完整性标识；保留段落、链接、表格、列表，排除整页导航和推荐内容。
- 原始 HTML、隐藏订阅段落、预览、canonical 不符、付费墙或不支持模板不能成为 FULL。已验证完整的短付费文章允许 SHORT_FULL。
- `render_required` 表示模板/加载证据不足，不等同于失去订阅权限；只有实际观察到访问墙或隐藏付费段落才报 `subscription_required`。Bloomberg 的机器人标题和 HUMAN 节点组合明确标成 Challenge，不伪装成空正文。
- 正文复用沿用已有 publisher entitlement scope，将 Bloomberg 从 public 改为 `scope:publisher:bloomberg.com`，避免以前公共链路取得的预览/未验证 FULL 被当作订阅全文复用。不批量重抓历史，不以历史标签冒充新版本验收。
- Challenge 沿用现有证据和人工维护流程；不增加无效公共 HTTP/Reader 回退、验证绕过或反复重试。不新增服务、常驻页面或浏览器身份。
- 实测服务重启后首篇完整访问需要 23.885 秒，超过普通文章的 20 秒总预算。因此仅 Bloomberg 采用 35 秒总预算、30 秒 browser 阶段预算；并发仍为 1，不修改其他源预算或列表调度。这避免冷启动时已获得全文却被外层提前取消。

## 真实订阅观察

用户完成官方订阅登录后，复用原 Chrome、新建临时文章 tab，并在读取后关闭该 tab。2026-11-12 20:44–20:45 UTC：

| 样本 | HTTP | 可见正文段落 | 其中订阅段落 | 正文字符数 | 导航至稳定正文 |
|---|---:|---:|---:|---:|---:|
| EU to Hit Russia With More Banking and Energy Sanctions | 200 | 7/7 | 5 | 1195 | 6.88s |
| Anthropic IPO Is ‘Jenga Block’ Holding Up Next Leg of AI Trade | 200 | 16/16 | 14 | 5124 | 4.10s |
| Microsoft Vows to Pay Its Own Way to Power Data Centers | 200 | 7/7 | 5 | 1606 | 3.23s |

三个样本均无 Challenge。首篇与登录后首次结构检查的正文 SHA256 一致。这里的耗时是 BODY 获取耗时，不是新闻发布至发现延迟；本轮不以它宣称列表时效改善。

## 反爬结论与证据边界

确认之前的 403/挑战与本次订阅权限缺失是不同机制。当前相同 Profile、固定出口、普通 Chrome 的 CDP 自动化读取成功，足以否定“只要使用 CDP 就必然被拦截”。不能据此断言过去的每次拦截都由匿名、自动化特征或频率单独造成，也没有理由更换指纹、账号或 IP。

生产 external runtime 已使用 `no_defaults=True`；过去部分临时探测未使用，属于实验环境不一致，而非已证明的 HUMAN 触发原因。保留原 context、让页面正常执行资源和会话脚本，避免每篇创建匿名 context；访问按单身份并发 1 执行。既有出口 ID 的名称不是地理位置证据：本轮 Owner 实测该固定出口为 DE，未更换出口。

官方/社区核对：

- [Playwright connect_over_cdp](https://playwright.dev/python/docs/api/class-browsertype#browser-type-connect-over-cdp)：`no_defaults` 避免修改既有 context 的浏览器默认行为；不是反检测开关。
- [HUMAN cookies/storage](https://docs.humansecurity.com/applications/use-of-cookies-web-storage)：会话依赖 Cookie、local/session storage；因此应保留正常登录上下文，而非只复制请求 Cookie。文档所列期限不能直接当作 Bloomberg 的实际配置。
- [Playwright issue 42882](https://github.com/microsoft/playwright/issues/42882)：既存睡眠 tab 可影响整个 CDP attach；本轮未复现，不引入另一套 CDP 框架。

## 验收状态

### 生产正文链路

首次窄发布后，三个指定样本通过真实 `SharedContentExtractor → ArticlePipeline → Site Access → Chrome Worker`，不是仅通过独立 DOM 脚本：FULL 正文分别 1195/5077/1606 字符，7/16/7 段。EU 和 Microsoft 正文散列与可见页面初测完全一致；Anthropic 清洗后 5077 字符，去除页面附属内容，保留 16 个正文段落。

2026-11-12 21:05–21:06 UTC，发布后重复验收：

| 样本 | 结果 | 字符/段落 | 总耗时 |
|---|---|---:|---:|
| EU sanctions | FULL | 1195 / 7 | 23.885s |
| Anthropic Jenga Block | FULL | 5077 / 16 | 10.110s |
| Microsoft data centers | FULL | 1606 / 7 | 7.003s |
| Kalshi $65 billion funding | FULL | 1958 / 9 | 6.618s |

四篇均 HTTP 200、`browser_bloomberg_visible_dom`、原订阅 identity，并补得官方精确发布时间。Kalshi 在旧公共链路只取得预览并判为 subscription_required；新链路实际取得 9 段全文。首篇超出普通 20 秒总预算的问题已单独修复，不能以直接 pipeline 成功掩盖 Hub 预算不足。

### 自然消息与测试隔离

后台自然流经新链路的五篇不同文章已完成 FULL 入库：ETF（1733 字符）、Ghana（1980）、permitting reform（8653）、copper（6568）、Anthropic six-times oversubscribed（3877）。这与四篇定点样本不重复，共九篇不同文章实际取得可见完整正文。

观察窗口包含一条发布期间的 `connecterror`，保留失败台账；未将它归因于 HUMAN，也未篡改为成功。旧 Kalshi PARTIAL 历史结果未批量改写。旧版全部历史 FULL/SHORT_FULL 总量不能作为本轮成功率分母，需区分是否带有 `bloomberg_visible_dom` 证据。

21:06:17 UTC 只读检查：Bloomberg source v3、delivery_mode=test、两个原有 HTTP 入口保持不变；正式 raw_messages=0、standard_messages=0、ticker_source_bindings=0。测试发布从 870 增至 874，继续停留在原有轻量隔离出口。

四条新增测试发布均为 FULL，目标为 CEG×2、NBIS、MU；未变成正式订阅或下游消息。这里只验证处理与隔离，不重新评价这些正则命中的投资相关性。

### 最终发布

- Site Access 和正文路由/质量修改已通过既有组件发布工具上线，保留同一个 Supervisor、Profile、出口与登录会话；合并并行主线变更，未覆盖其他消息源修复。
- 最后只补发 Content Enrichment 的预算修正，运行构建提交为 `eb010079c14f58a05e3c5465ba0271293bce1f38`。生产检查确认加载 35 秒 LiveQueue 预算，在真实 35 秒外层 deadline 内以单次 browser 路径取得 FULL，耗时 9.463 秒。
- 测试证据：[生产 pipeline](../eval/bloomberg-subscriber-body-20261113/final-pipeline-receipt.jsonl)、[自然任务及隔离台账](../eval/bloomberg-subscriber-body-20261113/production-receipt.jsonl)、[最终预算 smoke](../eval/bloomberg-subscriber-body-20261113/budget-smoke.json)。这些文件不含付费全文、账号、Cookie 或密码。

### 回归和验收边界

- 正文 pipeline、publisher、native、双 runtime、维护恢复回归：132 通过、1 跳过。
- Massive Distribution、跨源去重、正文复用回归：57 通过、1 跳过；后续增加的预算/模板用例单独回归。
- 合入并行 Barron's 修改后的 Bloomberg/Barron's 回归：26 通过。
- 实际 Chrome 的可见/隐藏/裁剪/位于屏幕下方四种 DOM 对照均通过；隐藏、裁剪内容不能完整化，位于页面下方的可滚动内容不会被误删。SHORT_FULL 有回归验证，本轮未取得实际付费短稿样本。
- Ruff E9/F 与 diff 检查通过。代码已写入 changelog 并合入 origin/main。无账号密码、Cookie、付费全文进入仓库；证据只记录结构、字符数、散列和时间。

结论：订阅正文接入和真实生产链路验收通过；原登录会话跨多次服务更新持续可用，本轮未再观察到 Challenge。**跨日成功率和长期挑战复发率尚未验收，不能把九篇样本当成稳定 80% 以上的统计证明。** 本轮没有调整列表入口，也不宣称解决此前的列表发布→发现延迟。
