# V2 正文修复部署与会员账号验收（2026-09-15）

## 部署与启用

按用户要求整体推送 main 并同步 doxagent-sg `/home/ubuntu/doxagent`。已部署 badf5ed2、a06bc52c、5c292bf4、3e3f28cf；其中 5c292bf4 同步已有 API 查询子进程交易日历预热修复，远端原脏文件先保留 stash，再快进更新。原始诊断响应、浏览器页面和凭据未提交。

生产启用正文 V2 管线与浏览器，CDP 连接现有 operator Chrome `http://172.17.0.1:19223`。配置 Reuters、Barron’s、WSJ、Seeking Alpha、MarketWatch 身份域名；私有环境文件修改前已备份。未复制本机密码/cookies，未关闭用户浏览器或重新启动主机。新任务才冻结 body_v2.1，旧历史任务未重放。

## 账号文章级测试

3e3f28cf 部署后的独立正文管线真实测试：8 篇中成功 5 篇。测试不写业务库，不触发发布。

| 站点 | 当前结果 | 验证边界 |
|---|---|---|
| Reuters | 2/2，3785 / 5212 字符 | ArticleBody div 正文，标题匹配，身份 VALID |
| WSJ | 2/2，7287 / 6969 字符 | 实际 paywall 正文段落，排除作者简介，身份 VALID |
| Seeking Alpha | 1/2，2064 字符 | 中文正文与同文章 ID 的原始标题匹配；另一篇实际出现 PerimeterX 人机验证 |
| Barron’s | 本轮最后复测 0/2 | 两篇实际返回 DataDome 人机验证 iframe。此前普通文章真实管线抓到 3347 字符；实时卡片的已取得正常响应解析为 595 字符 SHORT_FULL，不等于最新现场端到端成功 |
| MarketWatch | 未验收通过 | 正常页面 200，但目标文章正文区显示订阅墙；WSJ 登录不能直接证明 MarketWatch 有文章授权 |

用户已收到 Barron’s 和 SA 具体文章的正常人机验证提示。验证码未自动处理，不接受隐藏实质段落、SSR 摘要、作者简介或推荐文章充当全文。

## 重启期间未完成修复的推进

- Reuters 新 div 正文、WSJ 作者简介优先级、Barron’s 实时卡片、SA 新容器与本地化标题已修复；CDP 读取正常渲染页面，有界等待正文 hydration，计算实际可见性。
- 已配置身份的域名直接选择登录浏览器，减少必然失败的匿名请求；使用现有域名并发/节奏控制，浏览器 429 将 Retry-After 写入共享冷却。身份内容即使浏览器故障也不送第三方 reader。
- 明确识别活动 PerimeterX 遮罩及 DataDome CAPTCHA iframe，返回 challenge_required；与空正文、订阅墙和被动 WAF bootstrap 区分。
- 15 条免费代表样本复测成功 4 条：Yahoo 7566、Barchart 5059、Investment Monitor 2742、Fool 3720 字符。其余为 9 条 HTTP 403、1 条 HTTP 451、1 条空官方字幕 unsupported_media。TheStreet AMP、24/7 Wall St. 公开 API 的远端补充尝试仍拒绝，未添加无验证依据的新路径。
- 上一轮已验证的 IBKR/Benzinga 原生正文和 CNBC 字幕代码同步上线；不能将重叠代表样本相加推算 256 条 badcase 的恢复率。

## 验证和留存

最新正文 5 文件共 99 项定向测试通过，正文模块 Ruff/mypy 通过；已有 Yahoo 相邻 36 项及 API 预热 2 项定向测试通过。部署检查覆盖远端 commit、实际 server 镜像、各 backend 容器运行/OOM/restart、真实 API 健康和消息源多轮轮询。

启用后截至已检查轮询时刻没有新的 Raw 输入，队列为空，因此尚未取得自然新任务的 body_v2.1 发布验收。消息源 status=succeeded 仅证明轮询成功，不证明新闻完整性。

证据保留本地 `exports/body_delivery_20260915/`：`acceptance_summary.json`、`body-access-results-final/probe.jsonl`、`body-free-results/probe.jsonl` 及对应正文/响应；原始数据不提交。最终身份优先路径部署复测结果在交付补记中记录。
