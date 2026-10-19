# Investing.com 持续失败根因与修复验收

日期：2026-10-20。下文时间均为北京时间，原始 JSON 保留 UTC 时间及偏移。

Investing.com 的持续失败不是“没有登录”，也不能用一个长期显示的 `SiteAccessError` 推断网站一直在拒绝请求。实证发现相互放大的失败链：历史 Challenge 门控没有随普通登录恢复；维护验证把列表当正文并在恢复人工页的 Playwright 句柄时耗尽预算；短暂的浏览器自动验证被立即变成长期人工阻断；控制 Worker 替换竞争导致向旧管道写入；未完成 DOM 导致正常 HTTP 200 的列表契约失败。登录弹窗本身没有被本轮检测器误判为 Challenge。

## 故障时间线

| 时间 | 记录和行为 | 对后续失败的影响 |
| --- | --- | --- |
| 10 月 6 日 | 缺省 probe URL、PROBE 错等正文 selector 等旧缺陷修复，曾验收四 ticker 和两篇正文 | 当次恢复不覆盖后来出现的故障机制 |
| 10 月 15 日 19:46:42 | 主身份 `public-markets-nl` 的 BODY 留下 403 / ACCESS_CHALLENGE 记录 | 主身份随后处于旧 COOLDOWN / manual attention；不是每天数千次新网站拒绝 |
| 10 月 18 日 14:01:20 至 21 | 备用列表成功后，BODY 留下 ACCESS_CHALLENGE | 备用也不能接替；两条访问路径均被旧治理状态阻断 |
| 10 月 20 日 03:48:53 | 用户 LOGIN Verify 为 CLEARED / CRAWLER CONTENT_ERROR / BODY NOT_TESTED，crawler_reason=TimeoutError | 没有取得恢复业务所需的证明，普通登录也没有解除旧 TARGET_LOST 任务 |
| 03:55 基线 | 九 ticker 均为 manual_attention_required；完整最近一天 access_events 查询没有新业务访问事件 | 当前大多数失败发生在本地准入前，不能当作新的验证码或出口失效证据 |
| 03:57 至 04:07 对照 | 原生原页有正常 10 条新闻；一次验证 READY 但 BODY NOT_TESTED，另一次采集验证超时 | 页面正常与验证管线可完成，是两件不同的事 |
| 04:15 至 04:16 对照 | 独立运行同一原页读取函数约 3.12 秒，分类约 0.47 秒；生产 Worker 日志只有 maintenance_recovery begin / status，没有 attached；正文结果 TimeoutError | 冷恢复人工页句柄会阻塞验证，正文尚未开始，不是正文被网站拒绝 |
| 04:20:28 | 一次新 LOGIN 验证原页确实被分类为 Challenge，建立关联任务 | 不能把这一帧忽略或伪装成功 |
| 随后的同 Target 复读 | 没有输入凭据、点击验证码、刷新、切换出口，同一 Target 已是正常 Intel 新闻列表 | 已复现可自行结束的临时检查；立即永久暂停身份会把短暂状态放大为长故障 |
| 04:22:16 | 独立业务页正文 FULL、采集 READY、会话正常关闭；04:22:48 主身份通过真实 canary 激活 | 解除的是验证成功的身份，不是手工刷数据库健康状态 |

历史文档对应 [10 月 6 日修复](reuters-investing-repair-20261006.md)、[10 月 15 日重部署修复](message-source-main-redeployment-repair-20261015.md)、[10 月 19 日共性阻塞修复](message-source-common-loop-starvation-repair-20261019.md)。这些修复分别解决过缺探测、共享 driver、文本边界、调度问题，不构成本轮登录 / Challenge 生命周期正确的证明。

## 已查实的机制和修复

### 旧任务与新登录脱节

旧 `commit_verification` 只允许同一 Challenge session / Target / Chrome instance 解除对应任务。新 LOGIN 的 task_id 为空，因此即使新页面正常，旧任务仍可保留 manual_attention。旧原页已经 TARGET_LOST 时，这形成无法完成的恢复链。前端只显示 `SiteAccessError`，掩盖了拒绝发生在网站访问之前。

现在，只有确认 TARGET_LOST 的同 Site、同 Scope、同 Identity，且新 LOGIN 的业务证明来自当前同一原生实例，才允许解除旧阻断。原任务为 BODY 时必须取得 FULL / SHORT_FULL，只有列表 READY 不够；原任务为 CRAWLER 时要求实际列表契约。仍存活的原始 Challenge、其他来源的任务、失败或未测试的能力都不能被新登录越过。缺少恢复证明时返回未结束任务 ID，保留 LOGIN 会话，不再因公开列表 READY 就自动关闭并误导用户认为已恢复。

### 列表被当作正文

Investing 新闻列表自身使用 `<article>` 渲染每张卡片。通用正文分类因此把真实列表认作 article，重复执行通用正文抽取。另一个配置错位是：采集 canary 为 Micron 列表，`auth.verification_url` 和 `body_url` 却为 Intel 列表。只排除与 canary 完全相同的 URL，仍会把另一个 ticker 的列表用于正文验证。

现在通过 Investing 域名、`/equities/*-news` 路径和真实新闻卡片共同识别列表；保留标题、URL、发布时间的实际 parser 契约，不把普通登录弹窗或 Cloudflare 的日常脚本引用当作验证码。所有 Investing ticker 列表都从正文验证候选中排除，正文改用真实新闻文章；公开来源优先普通文章而不是 Pro 推荐。生产 Scope 以 CAS 从 revision 1 更新到 2，将 canary 对齐已登记的 Intel 维护入口，并移除列表型 body_url。

### 人工页句柄恢复耗尽正文验证预算

External Chrome 人工原页的身份和所有权存在于持久化记录及原生 Target，不依赖某个 Python Playwright Page 对象。旧 Verify 仍会先重建该对象，再创建另一张正文页。实例或 Worker 变化后，这条恢复链可能耗尽整次验证预算，形成“页面可读、Verify 不可完成、Worker 再变冷”的循环。

Investing External 的 Verify 现在先原生读取并校验精确人工原页，再通过原有业务 Worker / 页面容量 / 截止时间创建独立正文目标；返回的 Chrome instance 必须匹配，不重建人工页句柄。原页恰为配置 canary 时直接验证这张活页的数据契约，不再重复打开列表。真正的正文检查仍然是独立业务访问，不拿原页状态或 HTTP 200 代替 FULL 证明。

### 临时自动检查被放大成长期人工暂停

04:20 的实验已经证明原页可在无人操作时自行从 Challenge 进入正常列表。此前的“一帧检查即保留人工任务并冻结身份”不能区分临时浏览器检查和持续人工验证码，后台页又可能降低检查推进速度。这个机制能够解释人工维护时经常见不到程序所报的 Challenge；不能反过来声称历史每一个 403 都是假验证码。

Investing 仍使用正式 Chrome、原 Profile、原代理和原生网络，只将已有单并发业务页置为活动页，并通过 DOM 协议有限观察。首次遇到 Challenge 时最多被动观察 8 秒，不点击、不解码、不反复 reload。只有观察到同一主 frame 的真实 2xx 导航响应，才用它替换最初的 403；其他 frame 的 200、仅有正常 DOM、或后台资源成功都不算。持续阻断保留精确原页，继续进入人工待办。

原生 DOM 读取与页面导航竞争时可发生节点失效，现在只重读 DOM，不增加网站请求。原生 CDP 帧容量对齐已有有界 Worker 帧，避免大于旧 2 MiB 元数据上限的正常 HTML 断开控制连接；最终响应仍受既有 body 字节上限约束。

## 验收口径

### 全天轮询与内部延迟，不是收盘停采

本轮曾仅凭最新轮询时间接近北京时间 04:30 就推断为闭市暂停，该推断错误，已撤回。生产 SQLite 的 9 个 Investing 绑定均为 `enabled=true`、`target_interval_seconds=60`、`active_windows=[]`；04:30 之后也持续出现新的自然 `poll_` 回执。因此应保持全天轮询，不通过增加交易时段窗口解释或隐藏延迟。采集调度、30 分钟新闻准入、闭市 sweep 是不同概念，不能混用。

04:35–04:38 的 Bus 日志实际存在 5 秒 SQLite BUSY 等待，以及 13–32 秒 `_accept_poll_result`，会使展示的 last_attempt/last_success 落后于实际时间；单身份排队和浏览器控制恢复进一步增加延迟。这是实测延迟证据，并不等于已经完整定位和修复全局 Bus 存储性能，本轮不扩大为该模块重构。

持续验收又捕获独立内部竞争：`command()` 先 `ensure()`，再等待该 Worker 的 operation_lock；等待期间预热或上一条超时操作可能 retire 这个 Worker。拿到旧锁后仍向关闭的 uvloop stdin 写入，出现 `RuntimeError: ... WriteUnixTransport closed=True`，两次网站重试仍复用旧句柄。现在取得锁后重新检查 Worker 是否仍为该 Identity 的当前句柄、进程/读通道/写通道是否存活；过期则在原截止时间内重新解析控制所有权，未执行的网站请求不会被重放。真实断管转换成既有有界控制恢复能识别的类型，不吞掉无关 RuntimeError；不杀原生 Chrome。

04:47:06 COHR 的真实失败回包为正常标题、同域 HTTP 200，但原生 DOM 只有 103,882 字符，没有 h1/h2、新闻链接、卡片或时间；因此是未完成文档，不是账号或已观察到的 Challenge。04:47:43 同路径运行方式下 ONDS 返回 969,528 字符、10 张具有精确时间的正常卡片。针对这个差异增加有限修复试验：仅在非 Challenge 的不完整 CRAWLER DOM 上，读取同一原生导航 Response 已收到的 body，最多 3 秒且包含在原总预算中；同 URL 真实 2xx、正确 ticker 标题、正式列表 parser 全部通过才能接纳，不另发 HTTP、不更换启动模式。仍失败保留原分类，不伪造成功。API 语义参考 [Playwright Response.body](https://playwright.dev/python/docs/api/class-response#response-body)；实际是否改善需以后续回执为准，不能从接口语义推断网站已恢复。

验收同时核对自然 `poll_` 操作、各 ticker 的最新轮询、独立正文管线、Challenge 原页及运行实例；不以一次 200 或一次 Verify 为交付。窗口内去重成功回执是访问次数，不是新新闻篇数。排队延期、发布期间连接失败、提取错误与实际 ACCESS_CHALLENGE 分开统计。

实际正文管线在 04:27:39 和 04:28:22 返回两篇不同文章 FULL，分别为 3,463 和 2,714 字符；使用随机独立操作 ID，不复用旧访问缓存，也没有发布测试文章。该样本证明正文能力，不改变现有 30 分钟准入和闭市 sweep 规则。

04:32:52、04:33:21 在后续镜像重新执行同一正式正文管线，两篇分别 FULL 3,941 / 2,993 字符。04:30:58 后第一段完整观察曾取得至少 15 次去重自然成功、没有新风险事件，但随后仍见队列/预算延期、COHR/ONDS 的 `crawler_contract_failed` 和控制管道竞争，故该段不是“全部 ticker 已稳定恢复”的验收。后续镜像和实际剩余故障应单列，不能只保留绿色样本。

备用 Identity 的普通 LOGIN 打开成功，但 Verify 在 CHECKING_CHALLENGE 阶段 ReadTimeout，不能认证为可用 fallback；已通过 owner API 执行 RELEASE_VIEWER，返回 `released=true,page_preserved=true`，避免诊断会话占住全局 Viewer。未输入账号、切换生产到备用或强行清除它的历史待办。

收尾又通过 CLOSE_LOGIN 正常结束本轮创建的备用诊断 LOGIN，返回 `session_closed=true`，同时解除该会话的 Profile 维护锁；未删除其 Cookie 或历史 Challenge 待办。主身份正常运行不依赖该备用被认证成功，备用仍需单独完成真实验收，不能标成已可接替。

回归：前序业务/维护集合 190 passed，Runtime 集合 32 passed / 1 平台跳过；控制竞争补丁及维护相关最新集合 52 passed，包含确定性“排队时旧 Worker 退出”与关闭管道测试。未声称全仓测试通过；扩大集合中的历史 `test_source_redeployment_recovery.py` 仍有旧 API/fixture 不匹配失败，未借本轮改写这些无关夹具。

### 最终镜像多轮验收（04:50:33—04:57:28）

- 最终 Site Access 镜像 `sha256:d09877df2c78f33d1f393916d62da9de1986df9e29af21377a23cfb663a57180`，代码 `4514ddaa6afb8af07ad4479c43ffd8c233d1a466`；之后只同步诊断文档和收据，不再重启服务。
- 此窗口去重成功 37 次，其中实际自然 `poll_` 成功 32 次、独立采集诊断 3 次、正文成功 2 次；该窗口没有失败结果或新风险事件。此前镜像的失败没有删除，不能与此窗口合并后声称全程无错误。
- 9 个 ticker 最终全部 `succeeded`、错误为空，轮询状态已跨多个自然轮次刷新；COHR、ONDS 单独复验均解析出正确 ticker 的 10 条精确时间新闻。
- 04:56:14 / 04:56:29 当前运行实例的真实正文管线再次 FULL，3,941 / 2,714 字符。测试文章未发布；自然采集窗口没有符合时效准入的新文章，故发布数为零不是“已发布新消息”的验收。
- 当前实例为 `f45a50c4368b4ca78e45f3f9972fa4b1`。查全站事件而非只按 Investing site_id 查询，发现 04:43:40 旧 `8ad0...` 已被既有 `NATIVE_GRACEFUL_DRAIN` 恢复逻辑停止（`stopped=true`）；legacy Profile.site_id 为 investorshub，事件挂在该旧标签下。这个元数据标签不表示两个采集源当前共享身份。不能声称主身份激活后进程从未变化；本次最后正文证明属于新实例。Supervisor 容器、Profile 配置及目录未重建。
- 最新最终代码回归 141 passed / 1 平台跳过；包含已完成响应试验的相关组合 78 passed。不同集合重叠，不相加冒充独立测试总数。

这是多 ticker、多轮自然抓取和独立正文的有限实证，不是全天或长期平均可用率。采集恢复不等于所有请求均严格 60 秒完成：04:54 快照端到端耗时约 52–74 秒，仍存在排队/存储压力，应作为独立性能问题保留，不能归咎于闭市或再次指派用户解不存在的验证码。本轮未调整交易日日历、新闻准入或 sweep 配置；上述“全天”核实的是实际绑定没有日内停采窗口，未以一次夜间验收宣称周末调度也已重新设计。

## 发布范围和保留项

只窄更新 Site Access；没有重启 Chrome Supervisor、Message Bus、Content Enrichment、Scheduler 或业务 Agent，没有更换代理、复制 / 清空 / 重建 Profile，也没有输入网站密码。历史阻断导致未活动的原生实例曾按既有 idle 规则退出，因此早期诊断中的 Chrome instance 有变化；不把“Supervisor 容器没重启”偷换成“本轮每个 Chrome 进程从未变化”。激活为主身份后的实例连续性另以最终运行收据核对。

当前四核主机曾观测到约 28 的 load average，Site Access 接近一个 CPU、Supervisor 约 0.9 CPU。它会放大启动和页面预算不足，但不因此扩大到业务 Agent 或主机调度重构。公开采集不要求账号 VALID；UNKNOWN 不等于未登录，也不能拿公开文章 FULL 声称付费权益已验证。

代码涉及 `content_enrichment/quality.py`、`site_strategy/verification.py`、`governance_repository.py`、`service.py`、`worker_runtime.py`、`runtime.py`、`investing_document.py`、`maintenance.py` 及 canary seed。测试、窄发布和脱敏收据位于 `eval/investing_root_cause_20261020/`。回滚使用 canonical release 的 previous Site Access 镜像与本轮逐次 release.json，不覆盖仍在运行的 Profile 或既有业务配置。
