# 五个医药 ticker 监测词应用与生产观察验收

## 配置结果

北京时间 2026-11-27 19:29:17，使用现有 `MonitoringTermsService` 将用户提供的五份 JSON 应用到生产。全部为 revision 1，完整反序列化后的规则与原文件逐项相等；未重写 L1、L2、Massive、相关性定义或 X 账号。保存了原始文件 SHA-256 和私有变更前备份。

| Ticker | 当前订阅数 | L1 概念 | 英文 L2 组 | Massive 组 | X 账号 |
|---|---:|---:|---:|---:|---|
| CGEM | 19 | 3 | 14 | 22 | cullinantx |
| RGNX | 18 | 3 | 14 | 20 | regenxbio |
| PRAX | 17 | 3 | 14 | 16 | praxismedicines |
| XENE | 18 | 1 | 9 | 9 | xenonpharma |
| SMMT | 20 | 2 | 10 | 14 | smmt_tx |

当前合计 **92 个实际订阅**，均已启用，五个 ticker 均 running。与上轮 97 个预配置相比，各 ticker 的 Stocktwits 订阅已被后续经用户批准的消息源维护删除；本轮不恢复退役源、不新增订阅。应用前源和绑定开关已开启，本轮实际解除的是“统一监测词尚不存在”的配置阻塞，没有改写 ticker 的激活时间、状态或运行游标。

Google News 已从“缺少第一搜索词”恢复为正式查询；X 流状态 CONNECTED，已包含上述五个账号。Massive 使用用户提供的独立正则，不调用决策模型。

ASH（CGEM，12 月 3–9 日）和 AES（XENE，12 月 7 日）的 America/New_York 日期窗口保留，本轮不会提前开启。其余 ticker 专属源归属、IR `endpoint_ids` 隔离和 Yahoo/Chrome 排除组合不变。

回执：[配置与原文件摘要](artifacts/biopharma-activation-20261127/configuration.json)、[后续语义复核](artifacts/biopharma-activation-20261127/terms-recheck.json)。

## 初轮生产观察

窗口：**19:29:17–19:41:42，共 12 分 25 秒**。观察使用现有 acquisition / live / stream 台账的有界只读查询，没有创建定时任务、常驻监控或平行处理链。

下表是该窗口的真实生产列表接收指标。共享行业源取一个 ticker 的镜像记录展示，避免把同一物理请求的多 ticker 分发误算为独立抓取。FDA/STAT/Akeso 的计数含各自多个入口。

| 来源/频道 | 完成的成功列表轮次 | 派发→列表平均秒 | p95 秒 | 最大秒 | 同入口成功接收最大间隔秒 |
|---|---:|---:|---:|---:|---:|
| FDA（三入口） | 112 | 1.647 | 3.921 | 5.265 | 22.903 |
| Endpoints | 37 | 1.333 | 3.213 | 4.350 | 22.645 |
| Fierce Biotech | 37 | 0.647 | 2.925 | 3.094 | 22.311 |
| STAT（两入口） | 75 | 1.822 | 4.093 | 5.134 | 23.136 |
| Taiho / CGEM | 37 | 2.306 | 4.902 | 5.285 | 22.172 |
| Akeso（两入口）/ SMMT | 75 | 1.121 | 3.472 | 4.912 | 22.701 |
| HKEX / SMMT | 37 | 3.167 | 5.458 | 6.367 | 24.140 |
| IR / CGEM | 37 | 1.228 | 3.073 | 4.256 | 22.374 |
| IR / RGNX | 38 | 1.395 | 3.159 | 4.548 | 22.200 |
| IR / PRAX | 38 | 1.763 | 3.741 | 5.164 | 22.167 |
| IR / XENE | 37 | 1.299 | 3.773 | 3.861 | 22.072 |
| IR / SMMT | 36（另一次超时） | 2.287 | 4.246 | 5.378 | 40.229 |

新增源的平均成功接收间隔约 20 秒，调度等待平均约 0.055–0.087 秒。SMMT IR 有一次 `TimeoutError`，下一轮恢复，形成约 40 秒的接收间隔；未见持续阻塞或牵连其他 ticker。该单次网络超时没有被改成成功，也没有通过扩大超时预算掩盖。

正文另用生产已安装适配器进行了只读真实网络抽样：18 个内部入口全部取得列表；15 个正文 FULL，另外 3 个为 Endpoints 和 STAT 订阅受限。此处没有把付费预览当全文，也没有新增 Chrome 身份。会议源的抽样是主动验收请求，不代表其日期窗口已被自动打开。

观察期间自然新发现的 16 篇 Newswire 文章均取得 FULL，对五个 ticker 形成 80 个正常相关性判断，均按用户正则判为 IRRELEVANT。它们不能计为医药行业源的相关消息成功发布样本。

证据：[12 分钟生产快照](artifacts/biopharma-activation-20261127/observation-prepatch.json)、[真实正文抽样](artifacts/biopharma-activation-20261127/body-canary.jsonl)。

## 实际发现并修复的问题

### FDA Calendar 将历史档案当作首次发现的新消息

原生 JSON 返回 201 条历年会议，原实现保留完整索引并在没有发布时间时使用 UNKNOWN_FIRST_SEEN。结果是，历史会议在首次接入时也能被接纳。本轮发现一条 **2026 年 7 月 29 日 Cellular, Tissue, and Gene Therapies Advisory Committee 会议**，在补齐监测词后被发布给 RGNX。

该记录大致时间为：15:00 首次发现、15:04 取得 FULL、19:31 入 Bus。其四个多小时的间隔主要包含缺词期间的配置等待及历史决策积压；没有官方发布时间，不能把这一间隔称为官方发布→发现延迟，也不能把它作为实时新消息验收样本。

修复分两处，复用同一规则：

1. 列表只保留尚未结束/刚结束的会议，以及官方 `changed` 显示最近两日有修改的通知；已结束且没有近期修改的历史会议不进入新消息链路。即使旧会议被近期修改或取消，仍可发现更新。
2. 本地发布前检查旧积压，拒绝归档会议，记录 `ADMISSION_REJECTED / ARCHIVED_FDA_CALENDAR`，防止旧队列绕过新列表范围。

会议日期只用于判断范围，官方修改时间只用于更新证据，都不冒充发布时间。缺少两者的通知不会被无依据删除。当前真实原生 201 条索引经范围检查后保留 1 条未结束会议；合法的“当前范围为空”也不会误报 HTTP 列表失败。

已经发布的那条历史记录保留原有审计历史，本轮没有删除已入 Bus 消息或操作交易/研判结果。该问题不能用较快的 HTTP 执行耗时解释为通过验收。

### 配置恢复遗漏尾批

应用监测词后，182 条旧记录仍处于 `CONFIG_INCOMPLETE / MASSIVE_TERMS_MISSING`。现有恢复 Owner 每次最多处理 2048 条，而调用方原先仅在配置版本变化时调用一次；达到上限后，剩余记录可能等待另一次用户修改。

先通过现有 `LiveRepository.refresh_decisions` 恢复这 182 条本地决策，复用已取得的正文，不重新下载、不直接发布。再修复调用方：如果处理满 2048 条，沿用每秒一次的配置检查节奏继续下一批，直到尾批完成。每次预算、前台新消息优先级和配置校验保持不变，没有增加全局扫描定时服务。

发布后五个 ticker 的 CONFIG_INCOMPLETE 和 PENDING 均已清空。恢复回执：[recovery.json](artifacts/biopharma-activation-20261127/recovery.json)。

## 测试与发布

- 74 项定向回归通过，覆盖医药来源、真实时间精度、首次发现、正文交接和跨源去重；其中医药专项 24 项也单独复核通过。两者包含重叠用例，不相加。
- 一个依赖已退役 `newswire_rss` 模板的旧用例被明确排除，该模板退役与本轮修复无关；没有宣称全仓测试通过。
- 回归新增：归档会议、近期修改的已结束会议、合法空日历、旧积压发布拦截，以及超过 2048 条后无需再次修改配置的分批恢复。
- 修正既有测试中的固定日历时间缓存以及退役 Stocktwits 后的组合数量；不恢复已删除的源来满足旧断言。
- 语法、Ruff E9/F 和 diff 检查通过。

运行代码提交：`7856e0d418c0adcfbf9bf63ba4163f335ec21251`，已推送 origin/main。只在 Collector 与 Bus 的当前不可变镜像上覆盖 `biopharma_parsers.py`、`live.py`，保留其他生产修复；通过既有加锁发布 Owner 发布。两个角色文件哈希全部匹配，重启计数为 0，Collector healthy。API、BODY、Initialization、O4、Runtime Scheduler、交易 Executor 均未因本轮切换重启。

发布镜像及哈希：[release.json](artifacts/biopharma-activation-20261127/release.json)。

## 补充观察与时效性边界

修复后补充窗口为 **19:46:00–19:51:36，5 分 36 秒**。所有本轮新增、处于启用窗口内的列表单元均有成功记录，无 FAILED；SMMT IR 也未再次出现超时。该窗口没有五个 ticker 的新 Bus 发布，另有自然新稿正常完成 FULL→正则筛除，TMX 一篇正文为 UNAVAILABLE，保留 NO_PUBLIC_BODY，没有用摘要冒充全文。

这段补充窗口包含重启后的波动：派发→接收平均约 7.34–10.06 秒、峰值约 17.76 秒；同入口成功接收间隔仍约 20 秒，最大约 23.84 秒。进一步拆分证明，早期耗时主要在真正发出 HTTP 之前：Fierce 12.410 秒等待、0.230 秒网络；FDA 17.235 秒等待、0.524 秒网络；CGEM IR 12.870 秒等待、0.653 秒网络。不能将这部分直接归因于网站列表索引延迟，也没有据此提高并发或改变网络出口。

到 19:52 左右，上述三个入口的最新抽样已回到约 0.006–0.777 秒的发出前等待、0.227–0.663 秒网络。具体占用哪个共享准入/执行准备资源，现有分段证据不能完全区分；该次波动已自行回落，不把它写成永久性能修复。

最后再次复核 **19:52:00–19:54:29，2 分 29 秒**：所有新增有效列表单元均成功，无 FAILED；平均派发→列表为 0.580–2.603 秒，最大单次 4.856 秒，同入口成功接收最大间隔 22.042 秒。短窗口复核确认恢复，不能代表全天尾延迟分布。

19:53 的最终配置复核再次确认：五份规则与用户文件一致、revision 1、92 个绑定启用、五个 ticker running，CONFIG_INCOMPLETE=0、PENDING=0；真实 FDA 索引当前范围为 1/201，两个美东日期窗口仍未提前打开。

证据：[补充观察](artifacts/biopharma-activation-20261127/observation-postpatch.json)、[最后稳定窗口](artifacts/biopharma-activation-20261127/observation-stable.json)、[HTTP 分段对照](artifacts/biopharma-activation-20261127/http-phases.json)、[最终配置核验](artifacts/biopharma-activation-20261127/final-configuration.json)。

**当前不能宣称真实发布→Bus 延迟已经达标。** 这段窗口没有足够的、具有精确官方发布时间且与五个 ticker 相关的自然新文章。列表轮次、HTTP 耗时、正文抽样、无错误和积压清空分别证明不同环节，不能替代真实时效验收。FDA 已发布旧会议样本不纳入实时成功率或延迟均值；DATE、RELATIVE、UNKNOWN_FIRST_SEEN 也不会混入 EXACT 的延迟统计。

## 保留的外部/既有来源限制

Benzinga 仍返回 401，IBKR 新闻数据源不可用；Google RSS 有零星 429，SMMT Reuters 有列表验证失败。以上来自既有通用源，开关仍启用且保留真实错误，没有伪造可用凭据、降级为成功或另建浏览器身份。Endpoints/STAT 订阅文章的全文仍需合法订阅能力，本轮保留 subscription_required。

后续如需验证长期时效，需要在自然新稿到来时，对照官方精确发布时间、第一次列表出现、采集接纳、BODY 排队/下载和 consumable_at 分段统计，分别报告首发新文章、配置积压释放和修订更新。
