# 新消息源与正文补全恢复记录 · 2026-09-28

验收环境：新加坡 `doxagent-sg`，`/home/ubuntu/doxagent`；时间截至北京时间 06:13 左右。本文中的成功均来自生产实际请求、持久化结果或真实管理 API，不以 HTTP 200 单独代替正文成功。

## 结论

| 消息源 | 采集验收 | 正文验收 / 剩余边界 |
| --- | --- | --- |
| TrendForce News | 实际列表 8 条、零采集失败；闭市窗口覆盖 COMPLETE；BE/INTC/MU 轮询成功 | 样本 FULL，3296 字符 |
| TrendForce Press Releases | 实际列表 5 条、零采集失败；闭市窗口覆盖 COMPLETE；BE/INTC/MU 轮询成功 | 样本 FULL，2295 字符 |
| Reuters Site Search | BE/INTC/MU/RKLB 正常搜索，原冻结闭市窗口复测 COMPLETE；旧错误清除 | 实际 Reuters 文章 FULL，4739 字符 |
| Google News Search RSS | BE/INTC 实际抓取成功、覆盖 COMPLETE，旧错误清除 | 原 118 条失败记录通过正常队列重试，94 FULL + 2 SHORT_FULL，即 96/118（81.4%） |
| DIGITIMES Semiconductors More News | 荷兰 External Chrome 实际列表 11 条、零采集失败；BE/INTC/MU 的 SiteAccessError 已消除 | 订阅正文仍待人工登录。单页闭市历史覆盖仍诚实标 PARTIAL，而非承诺完整回溯 |
| DIGITIMES Taiwan RSS | 入口换到荷兰出口后真实返回 40 条，三个 ticker 成功、零连续失败、闭市覆盖 COMPLETE | 缓存 40 篇：10 篇有正文；其余 30 篇经新策略逐篇重试，均明确 login_required。公开样本新策略抽出 FULL 1325 字符 |
| Barron's ticker news and Other Dow Jones | 代理链路恢复，但主身份验证仍遭 403 / challenge；四个 ticker 的鉴权失败未伪装成成功 | 需要人工完成 Barron's challenge / 登录，再验证订阅正文；其他 Dow Jones 发布者独立验证授权 |

正文样本：

- Reuters：`https://www.reuters.com/legal/transactional/softbanks-growing-bets-ai-semiconductor-assets-2026-09-24/`。
- Google 召回发布者：`https://manilashaker.com/msi-offers-level-up-your-ber-months-campaign-with-up-to-php-46000-off-select-laptops/`，FULL 1918 字符。
- DIGITIMES 台湾公开文章：`https://www.digitimes.com.tw/tech/dt/n/shwnws.asp?id=0000769567_EQC46CLT7QXBGK6EIKN7Y`。
- DIGITIMES 台湾会员验证文章：`https://www.digitimes.com.tw/tech/dt/n/shwnws.asp?id=0000769530_FJQ6EWDK21NANN1N332D5`。

## 定位与修复

### 1. 共享抓取失败后热循环

TrendForce、DIGITIMES 的失败计数已累计到万级。共享 distribution 的实时分派未遵守各绑定已持久化的 `next_dispatch_at`，失败/延期后反复立即派发，额外增加站点压力。

修复基础 Scheduler 和 Persistent Runtime 两条 dispatch 路径；失败与 Site Access 延期均保存下一次允许执行时间。保持消息源目标轮询 60 秒，不改变交易时段与闭市 sweep 窗口。

### 2. 浏览器池持有失效浏览器

Site Access 驱动和显示服务存活，但部分 Managed Chrome 已退出，缓存仍引用死连接。现在检查实际浏览器和原始 CDP 连接；失效 Managed Runtime 安全关闭并释放 single-writer lock 后，用同一 Profile 重启；External Runtime 仅重新 attach，不关闭长期 Chrome。取消任务也归还并发额度；不确定的关闭不会提前放开 Profile 写锁。

### 3. 出口故障与按站点分配

远端原配置确实来自 Flower_SS，并非仅 SSRDOG。原美国/日本节点探测失败；对 Flower 与 SSRDOG 的相关区域节点做对照，并导入 39 个 SSRDOG 节点供后续使用。本轮没有选择未通过探测的 SSRDOG 节点。

最终四个出口槽位的实际探测均为 200：

| 稳定槽位 | 当前实际节点 | 观测出口 IP |
| --- | --- | --- |
| `us-standard-5` / 18081 | Flower 荷兰标准 IEPL 专线 2 | 104.28.219.140 |
| `jp-standard-6` / 18080 | Flower 德国标准 IEPL 专线 2 | 205.198.126.114 |
| `nl-standard-2` / 18082 | Flower 荷兰标准 IEPL 专线 1 | 104.28.219.140 |
| `de-standard-1` / 18083 | Flower 德国标准 IEPL 专线 1 | 205.198.126.113 |

保留槽位 ID 与 Profile 绑定，更新实际 node_ref、指纹、固定时区及探测状态。需要加载环境变化的 External 身份通过正常 drain / start 完成，Profile 不删除。桌面地区优先显示节点真实国家，不能再把稳定槽位名字当成实际国家。荷兰两个节点本次观测到相同出口 IP，不宣称它们是独立 IP fallback。

DIGITIMES 英文与繁中分别使用自己的荷兰身份，保持 Cookie 隔离。德国 1 出口通用链路仍可达，但 DIGITIMES 的特定页面/Feed 出现 CloudFront 403；因此该 Feed 独立改为 18082，并通过版本化 Source 更新同步绑定。DIGITIMES 英文当前主组合是 `digitimes-nl-1`；直连组合禁用但原 Profile 保留。

Clash 配置、manifest 在 `/opt/doxagent-egress-clash/`，修改前备份后缀 `.before-20260928`，root-only；临时订阅导入文件已删除。直接访问与代理访问 Cloudflare 的证书 SHA256 一致，所测试代理链未观察到 TLS MITM；没有重构网络链或植入证书。

### 4. Google 正文在排队期间耗尽预算

原 118 条 Google 记录均出现首轮 `deadline_exceeded`，没有实际网络尝试；大量闭市正文任务排队即消耗了整个有限抓取预算。不是仅因 Google RSS 包装链接无法解析：实际解码已能得到发布者 URL。

闭市 / 共享文章任务的有限网络及重试预算现在从首次领取起算，后续重试、租约恢复不续期；实时消息期限不变。通过普通 EnrichmentJob / Hub 恢复原输入，没有直接改写正文结果或篡改发布时间。

剩余 22 条本轮真实结果：

| 正文结果原因 | 数量 |
| --- | --- |
| access_combinations_unavailable | 8 |
| risk_combinations_exhausted | 7 |
| subscription_required | 4 |
| publisher_identity_mismatch | 1 |
| deadline_exceeded | 1 |
| unsupported_media | 1 |

这些包含第三方订阅文章、挑战、不可用发布者及无文字稿视频，不能用摘要冒充正文。Google 宽召回也包含体育等无关 “Intel” 新闻；本轮未擅自修改监测词定义。历史失败元数据在未形成内容修订的重复记录上可能仍保留旧原因，本轮新尝试以 Site Access `body_outcomes` 为准。

### 5. 闭市成功未清除旧前端错误

原代码只对实时采集保存成功 PollState，闭市 Reuters / Google 成功后，前端仍读取旧失败。现在所有模式更新真实采集健康、错误与计数；闭市保持实时 checkpoint、bootstrap、调度游标不动。恢复旧共享 run 时，失败投影使用本次实际 attempt 时间，不沿用旧创建时间。

### 6. DIGITIMES 正文页面适配与维护窗口

繁中付费文章在 `#newsText` 内嵌 `form#Login` 和密码字段，通用抽取未识别，把会员服务文字混入候选，随后错误进入 reader fallback。新增 `builtin:digitimes_tw@1`：按真实正文容器取内容，文章内会员表单判 login_required；站外导航登录表单不误伤公开文章。公开短篇可正常判 SHORT_FULL。繁中采用 optional auth 的持久化浏览器轨道，单并发 / 3 秒间隔，既不封锁公开文章，也不把认证内容送第三方 reader。

同时修复通用 Pipeline 的 `browser_required` 路径：配置为 browser-only 且 optional auth 时，也必须实际进入浏览器，而不是直接结束。英文加入真实 `#content` 容器和明确 paid subscription 提示识别；会员验证不再仅得到 unknown / ok。桌面“无订阅正文权限”提示不再无依据地声称“账号有效”。

维护桥接以前先要求 VNC 可达，再启动 / 选择浏览器；未运行的目标导致永远不能打开维护。现在先 open 并选中 Runtime，再等待 RFB；失败安全关闭，或保留可恢复的会话。故意将 VNC 目标置为失效端口后，DIGITIMES 仍能自动恢复打开。

## 生产恢复与回归

- 代码已 commit / push / 远端 fast-forward 拉取；核心修复提交 `25e88cbe`、`5637686c`、`d5af7b89`，正文适配提交 `ffaf36b9`、`ac711063`、`03fd9b44`、`7c2f8645`，桌面提示提交 `3aba71c5`。
- 重建并更新 Message Bus、Content Enrichment、Site Access；桌面 root 桥接和 GUI 安装并编译通过。Chrome Supervisor 连续运行，未重建；其它 Scheduler、交易、初始化等服务未因本任务重启。
- 生产 Scheduler / Service / Repository / Pipeline 文件 SHA256 与本地修复文件一致。Site Access 的正文适配来自 `03fd9b44` 构建，之后 Pipeline 调用侧修复在 Content Enrichment 镜像生效。
- 原 9 个 TrendForce / DIGITIMES 英文失败 SOURCE_SWEEP 子任务，按原冻结 9/26 06:00 UTC → 9/27 06:00 UTC 窗口恢复为 SUCCEEDED；不重启终态父 sweep，不修改交易或分析工作流。
- Google 118 条与 DIGITIMES 台湾缺失正文通过正常队列重试；最终正文队列为空。旧内容的 distribution delivery / admission 未改动，不能把已过时新闻重新伪装成当日新增。
- 综合 14 个回归文件：253 passed、1 skipped；之后桌面提示增补回归：9 passed。覆盖调度节奏、断连 / 取消、锁释放、闭市健康与预算、正文身份、会员墙、公开短篇、browser-only 轨道及 VNC 启动顺序。
- VNC 仍只监听宿主机 `127.0.0.1:5900`；桌面用户不属于 docker 组；无未关闭的维护会话。重要操作已追加本地 `changelog`。既存 Overview、交易审计、磁盘清理等未提交改动保留，未混入本任务提交。

## 你醒来后需要做的操作

重新打开 xRDP 桌面的 **Site Login Maintenance（消息源登录维护）**，依次选择：

| 网站 | 推荐主身份 / Profile | 操作 |
| --- | --- | --- |
| Barron's | `dowjones-main` | 完成人工 challenge / 登录，点击登录完成并验证；目前验证仍为 403，未认定已登录 |
| DIGITIMES English | `digitimes-nl-1` | 在英文站登录拥有订阅的账号，再验证；当前只观察到付费墙，未证明账号有效或订阅存在 |
| DIGITIMES Taiwan | `digitimes-tw-nl-1` | 在繁中会员文章页面的会员框登录，再验证；当前明确 REAUTH_REQUIRED |

不用输入 SSH、Docker、代理地址或令牌。英文身份实测 open 3.34 秒；繁中新建 Managed 身份首次 open 6.21 秒，随后正常可访问。所有测试维护都已 verify / close，没有替你输入账号密码或处理 challenge。

Barron's 和 WSJ / MarketWatch 虽共用 Dow Jones Identity，授权状态仍分别记录；若需要这些站点的订阅正文，应分别选择网站验证同一主身份，不推断兄弟站已授权。备用身份仍独立，不把主身份登录状态复制到备用。会员登录完成前，本次不能承诺 Barron's 抓取和 DIGITIMES 会员正文已全部恢复。
