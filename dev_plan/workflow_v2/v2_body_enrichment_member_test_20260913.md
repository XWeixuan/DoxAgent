# Seeking Alpha 登录后补充验证（2026-09-13）

用户完成个人 Chrome 的 Google 登录后，使用浏览器界面的可访问性树检查两篇历史失败文章。英文原文通过页面“显示原文”入口取得，其 URL 带 `force_locale=en`；未修改账号语言设置。

| Article ID | 标题 | 界面观察结果 |
| --- | --- | --- |
| 4942766 | Semiconductor ETFs: The Good, The Bad, And The Ugly | 可见从 Thesis 到 To Sum It Up 的正文及最后结论，之后才是作者介绍和披露 |
| 4916537 | TSMC: The Buying Opportunity Is Finally Flashing (Rating Upgrade) | 可见完整分析至最终 Rating，之后是投资群推广、作者介绍和披露 |

这证明当前账号在个人 Chrome 对这两篇文章具有全文访问权限，不代表已确认具体订阅档位。未保存账号、投资组合、cookie 或完整页面私人信息。

## 本轮修复与验证

- Seeking Alpha 的正文展开检测限定在选定正文容器内，避免作者简介的 Show more 或 Read more 导致全文被拒绝；正文内同名控件仍会阻止误报全文。
- Seeking Alpha 正文遇到明确的 More on my IG service 标题时结束，排除本次观察到的作者投资群推广。
- 针对观察到的边界构造 HTML 测试，覆盖正文外按钮/链接、正文内展开控件和推广尾部。正文管线测试 28 passed，相关 Ruff 检查通过。

## 验收边界

这是登录后界面访问验证和构造 HTML 的代码回归验证。本轮未取得真实登录 DOM 快照送入后台 extractor，因此不宣称真实 DOM 选择器、后台端到端补全或完整正文哈希对照已通过。个人 Chrome 登录态与 PublisherBrowser 独立持久化 profile 不共用；后台 profile 的首次登录、重启复用及失效恢复仍待实测。没有部署、生产数据库写入或开启实验开关。

上一轮 1000 条回放数据保持原样，本轮修复后未重跑全量，其统计属于之前的代码版本。中文自动翻译与英文输入标题的身份匹配尚未验收；本次通过网站提供的原文入口进行检查。
