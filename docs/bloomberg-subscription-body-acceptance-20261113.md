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

开发回归和生产集成结果将在发布后补记。上述三个实际页面样本证明授权可见正文读取可行，但尚不能单凭它们宣称跨日稳定性或总体成功率目标达成。
