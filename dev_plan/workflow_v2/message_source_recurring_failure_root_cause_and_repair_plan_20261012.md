# 消息源反复失败：根因诊断与修复方案

日期：2026-10-12。生产取证约 11:22–11:34 UTC，即北京时间 19:22–19:34。

本轮交付诊断与实施方案，**未执行生产修复、配置修改、清理、服务重启或人工维护会话**。文中的“修复”均为下一步待实施项，不是完成声明。

## 1. 结论：这次不是上次满盘问题原样复发

当前问题分为四层，不能统一归为网站风控或随机网络波动：

1. **本次集中故障的直接共因是两个荷兰固定出口同时失效。** `nl-standard-2:18082` 与 `us-standard-5:18081` 均连续探测失败 106 次、状态 UNAVAILABLE；两轮独立 HTTPS 对照也均连接超时。Reuters、InvestorsHub、DIGITIMES Taiwan 的成功记录集中停在 07:17–07:20 UTC；DIGITIMES 英文切到已有德国组合后仍正常。不是整个 Site Access、所有 Chrome 或所有代理都坏了。
2. **故障被配置和候选治理放大。** InvestorsHub 虽登记多个组合，但 crawler/body override 实际只允许荷兰主组合；Reuters 的德国组合被真实 challenge 的人工门控排除；Barron's 有未认证备用；这些都不是可立即接替的有效备用。监测到节点不可用不等于已经有恢复路径。
3. **部分“反复修复”其实是上轮未完成业务验收的持续失败。** Investing 上次真实轮询成功仍为 10/06；Barron's 大部分 ticker 仍停在 10/08。DIGITIMES 英文列表正常，但近 48 小时记录仍有 19 次 subscription_required。必须分别验收采集、全文权限与分发，不能用容器 healthy 或一个首页 200 宣布全恢复。
4. **还有可确定改进的内部放大机制。** Runtime SQLite 的 BUSY/locked 异常会逃出消息总线主循环；同步 30 秒等锁会阻塞网络事件循环。Yahoo 同时存在 browser recipe 404 和后备 HTTP 全局代理失效，外层只留下最后一条传输错误。External 控制层也有未收尾任务异常和过于敏感的健康恢复路径，但它们不是本次所有来源同时失败的已证实直接原因。

优先顺序：**恢复真实失效的出口与 Taiwan feed → 建立有效候选和分层错误 → 隔离 Runtime 锁异常 → 有界修复控制层 → 完成必须人工参与的站点验收。** 不先重启所有服务、不重建身份、不再统一拉长 timeout。

## 2. 证据与当前生产基线

### 2.1 可复核证据

本轮证据目录：`eval/message_source_diagnosis_20261012/`，说明见其中 README。

| 文件 | 用途 |
| --- | --- |
| bus.json | 各 ticker 真实 PollState、最后成功时间、连续失败与错误 |
| site.json | Registry 策略、用途 override、组合健康、访问诊断 |
| details-site.json | 最近真实访问、Identity 绑定、时区归一化近 48 小时正文结果 |
| network-confirm.json | 按实际 listener 映射的出口和 Feed 对照 |
| details-bus.json | 实际 feed_proxy_url、非终态 runs 与结果 outbox |
| host.json / storage.json | 主机资源、脱敏代理配置、锁异常与业务库大小 |
| yahoo.json | Yahoo 正式采集 recipe 的连续 http_404 |

所有时间比较以原始 ISO 时间和 UTC 归一化为准。PowerShell 展示可能自动转换为北京时间；不要把展示中的 +08:00 当作另一轮事故。各文件采样不是原子快照，计数会自然变化。

历史对照已复核：

- `docs/message-source-reliability-repair-20261001.md`：跨 Identity 队头阻塞、取消泄漏、清理期限与调度热路径。
- `docs/message-source-recurring-repair-20261004.md`：External Node 驱动失效、自恢复、查询健康契约、公共身份拆分。
- `docs/reuters-investing-repair-20261006.md`：缺失恢复 probe、列表/正文探测混淆、人工验证未解除健康门控、真实 challenge。
- `docs/message-source-storage-recovery-20261009.md`：SQLITE_FULL、三服务窄发布及未恢复项。

### 2.2 运行版本与排除项

当前仍在运行：

| 服务 | 镜像 | 观察 |
| --- | --- | --- |
| Site Access | doxagent-site-access:source-storage-20261009-r1 | 自 10/09 发布后未重启；readyz 正常 |
| Message Bus | doxagent-v2:source-storage-bus-20261009-r1 | RestartCount=5，最近启动 10/12 09:56:31 |
| Content Enrichment | doxagent-v2:source-storage-enrich-20261009-r1 | RestartCount=1，最近启动 09:42 |
| Chrome Supervisor | doxagent-chrome-supervisor:server | 运行两周，六个正式实例仍为原 instance_id/PID/generation |

Site Access `service.py`、`storage.py` 的运行文件 SHA256 与 10/09 overlay 记录一致；`egress.py`、`runtime.py`、`external_runtime.py` 与当前本地对应文件一致。**没有证据证明本次是存储补丁被全量发布覆盖。**

但本地和生产的 `persistent_runtime_v2/bus_orchestration.py`、`journal.py`、`message_bus_v2/scheduler.py` hash 不同；生产仍包含 10/06 sweep-priority overlay。修复不能直接拿本地整个文件覆盖生产，必须以实际运行文件和现有补丁谱系为基线做符号级合并。

主机状态：

- 根分区约 177 GiB，剩余约 **14.4 GiB**，占用 92%，不是 0；inode 使用 23%。Site Access storage=warning、unavailable=false，最近事务仍提交成功。
- 上次清理后约 35 GiB 的可用空间已明显下降。当前 read 库约 31.12 GB、Bus 20.33 GB、Runtime 4.91 GB；保留专项备份约 4.08 GB。未完整拆解其余空间增量，不把全部增长归咎于消息库或镜像。
- 主机可用内存约 2.3 GB，2 GB swap 几乎耗尽；Supervisor 约 4.13/5 GiB，Site Access 约 1.87/6 GiB，仍有多个隔离研究 Worker 并行运行。
- Control 等旁侧服务反复退出，现场有 Runtime database is locked 栈；部分容器显示退出码 137。**137 本身不能证明 OOM。** 本轮未取得对应内核 OOM 记录，Bus/Site Access 的 cgroup oom_kill=0，不将内存紧张直接写成已证实 OOM 根因。
- Site Access driver READY、epoch=41，采样活动页面 1、无全局队列等待、无超预算挂起。六个 Chrome 未丢失；不是“Chrome 全部死亡”。

## 3. 逐源判断

| 来源 | 当前实况 | 直接问题 / 恢复边界 |
| --- | --- | --- |
| Reuters Site Search | 四 ticker 最后轮询成功 07:16:47–07:17:32；真实 Managed 访问到 07:20:13 仍成功；随后 resources_unavailable | 荷兰主路失效，其他路因 challenge 人工门控不可用；不是所有请求又被 Reuters 拒绝 |
| InvestorsHub ticker news | 四 ticker 最后成功 07:17:53–07:19:42；最后真实访问 07:20:12 返回 200 | 唯一获 override 放行的公共荷兰身份出口失效；其余登记组合不进入生产候选 |
| Barron's ticker / Other Dow Jones | INTC 06:11:27 有新轮询成功；MU/BE/RKLB 上次成功仍为 10/08；07:19:47 主路真实 403，之后 resources_unavailable | **真实网站 403 在前、出口故障在后**；不能认为换通节点就必然消除拒绝，也不能说所有 ticker 已恢复过 |
| Investing ticker news | 四 ticker 上次成功仍在 10/06 14:33–14:36，累计失败约 5,530 次 | public-markets-nl 的 challenge 人工门控一直未解除，最后探测 10/09 仍 403；今天另叠加荷兰出口故障 |
| Yahoo Finance | 四 ticker 最后成功约 07:17–07:19；外层报 Yahoo browser transport request failed | 当前 browser recipe 连续 CONTENT_ERROR/http_404；随后 NCP/RSS 使用全局 7893，Clash 日志确认该路径上游拨号超时。不能仅修一个分支 |
| DIGITIMES Taiwan RSS | 三 ticker 最后成功均 07:18:01，随后 ConnectTimeout | 生产入口写死 feed_proxy_url=18082；同一 RSS 直连与德国 18083 都返回 200/XML，入口并非全网不可用 |
| GlobeNewswire Semiconductors RSS | 三 ticker 自 10/09 13:40 后持续 403 | 当前采集无 feed_proxy_url；直连、德国出口均 AkamaiGHost/Access Denied，属于独立 feed 访问拒绝，不是同一个 NL 故障 |
| DIGITIMES English More News | 三 ticker 当前正常；德国组合持续返回列表 200 | 采集可用，但近 48 小时正文 19 次 subscription_required、9 次 resources_unavailable；列表 fallback 成功不等于付费正文 fallback 可用 |
| TrendForce News / Press | 当前三 ticker 正常 | 本轮近 48 小时 trendforce 正文 FULL 7 次，没有证据要求再改 parser/出口 |
| HuggingNews、GlobeNewswire Search | 当前轮询正常 | 近 48 小时分别 FULL 45 / 17 次；不能因为历史故障再次重启或改路由 |
| BE IR RSS | 无成功记录，累计失败约 21,070 次 | 原 feed 持续 403，是老问题，不包装成本轮新故障 |

正文统计是 Site Access 已上报的 outcome 样本，不是去重文章的全量成功率；没有 outcome 不等于成功或完全没有抓取。

## 4. 共性机制与根因分级

### 4.1 已确认：固定出口故障域集中，且只有检测没有接替

Registry 映射：

| egress_id | 固定端口 | 实际节点 | 当前结果 |
| --- | ---: | --- | --- |
| us-standard-5 | 18081 | 荷兰标准 IEPL 2 | UNAVAILABLE；两轮 ipify ConnectTimeout |
| nl-standard-2 | 18082 | 荷兰标准 IEPL 1 | UNAVAILABLE；两轮 ipify ConnectTimeout |
| de-standard-1 | 18083 | 德国标准 IEPL 1 | READY；实测 IP 205.198.126.113 |
| jp-standard-6 | 18080 | 德国标准 IEPL 2 | READY；实测 IP 205.198.126.114 |
| server-direct | 18084 | DIRECT | READY；实测 IP 43.163.67.97 |

两个荷兰节点此前观测为同一出口 `104.28.219.140`，配置上还共享订阅接入服务器名、不同端口。它们不构成独立故障域。**尚未证明具体故障是订阅上游端口、链路、服务端还是出口网络；已证明的是固定转发路径不可用，而非单一网站拒绝。**

`egress.py::probe_egress` 会在连续失败后撤下节点；`service.py::_execute_uncached` 随后在访问网页前拒绝该组合。这部分故障检测是生效的。缺口在于 Registry 主备未形成真实可用路径，以及入口配置仍绑定死出口。

错误还会失去精度：profile 缺失、egress 不可用、materialize 失败最终可能都归并为 resources_unavailable；完全没有健康候选会统一变成 ACCESS_RATE_LIMIT。它们不代表相同的底层故障。

### 4.2 已确认：配置中的“备用”与可接替的备用不一致

- InvestorsHub revision 12：登记四组合，crawler/body override 仅 `investorshub-external-nl`。
- Investing revision 13：也只允许 `investing-external-nl`，且 manual_attention_required=true、risk_strikes=3；cooldown 时间早已过去，不表示人工门控应自行解除。
- Reuters revision 9：德国 External 已登记，但 manual_attention_required=true；原德国 Managed 和直连路也有人工门控。存在组合名称不代表可切换。
- Barron's 的备份 Profile 是独立 Cookie 身份，不能因主 Profile 曾登录就把它视作已认证。还受 Supervisor 六实例容量约束。
- DIGITIMES 列表无登录要求，可以走德国备用；正文要求认证，不一定能走同一路，所以出现列表正常而正文不可用。

这不同于 10/06 已修掉的“缺 probe_url 永久 cooldown”缺陷。Investing 当前有正确 probe_url，仍未恢复的主要原因是需要人工处理的 challenge，而非继续缺 probe。

### 4.3 已确认：Yahoo 前后备的错误和代理归属断开

当前原 owner 的 `builtin:yahoo_latest_news@1` 连续返回 CONTENT_ERROR/http_404；生产最终 PollState 却仅报告后备传输错误。

`YahooFinanceNewsAdapter` 仍按浏览器 → NCP → RSS 尝试，HTTP transport 使用 `crawler_egress_proxy_url=http://doxagent-egress-clash:7893`，不是 Chrome 的 18080。Clash 日志明确看到 finance.yahoo.com / feeds.finance.yahoo.com 在该全局路径反复上游拨号超时。容器没有 HTTP_PROXY/HTTPS_PROXY 环境变量，不是意外环境变量劫持。

**404 发生在导航文档还是页面内 latestNews/NCP 响应，现存聚合记录不足以区分。** 当前不能直接断言 Yahoo 删除了 `/latest-news/`，更不能盲目改 URL 就算修好。需要保留浏览器分支中的实际 response URL、phase 与 status。

原架构边界必须保留：Site Strategy 治理浏览器采集和正文，不接管 Yahoo NCP/RSS 业务策略。HTTP 后备需要自己的明确出口配置与诊断，但不因此变成 Browser Identity 任务。

### 4.4 已确认：共享 Runtime Journal 锁异常会扩大为 Bus 进程级故障

生产 Bus 日志有两条明确栈：

1. `BusOrchestration.run_once → journal.claim → BEGIN IMMEDIATE → database is locked`。
2. 处理已完成任务异常时，`journal.gap → BEGIN IMMEDIATE → database is locked`；错误记录自身又失败。

生产 `GlobalPollScheduler.run_forever` 直接 `await self.run_once()`，没有 BUSY/LOCKED 的外层隔离。异常可以逃出 CLI，交给 Docker 重启；RestartCount=5 与这一放大机制相符，但没有完整关联每次退出的时间/代码，不能把全部五次重启都确认为它造成。

`RuntimeJournal.transaction` 同步等待锁 30 秒。Bus 与网络任务共享事件循环，等待期间会延后 RPC、IBKR 回调、Jev 与任务收尾，制造看似网站 ReadTimeout 的次生症状。WAL 允许读写并行，但仍只有一个 writer，并不消除写竞争，见 [SQLite 官方 WAL 并发说明](https://www.sqlite.org/wal.html#concurrency)。

**持续持有写锁的具体进程/事务尚未被本轮证据锁定。** Control 也报同库锁错误，提示共享故障域；不能无证据指定某个研究 Worker 为元凶。必须补持锁/事务耗时证据，不能只吞异常或无限延长 busy_timeout。

### 4.5 已确认的控制层缺口；与本次源故障的因果仍有限

当前 `runtime.py` / `external_runtime.py` 的运行 hash 已核对：

- `_RawBrowserCDP._track` 完成回调只 discard task，不观察异常；现场反复 `Task exception was never retrieved / _attached / TimeoutError`。
- `_attached` 对无 policy 的自动附加 page 执行 `Runtime.runIfWaitingForDebugger` 位于异常处理之外。若命令超时，任务退出，没有明确恢复/告警闭环。由于配置了 waitForDebuggerOnStart，确有 target 未正确继续的风险，协议语义见 [CDP Target.setAutoAttach](https://chromedevtools.github.io/devtools-protocol/tot/Target/#method-setAutoAttach)。**未实测证明现场某个业务 target 正停在该状态。**
- `maintain_driver` 对一次 5 秒控制探测异常直接判 FAILED，并恢复整条 External 控制驱动；慢响应与驱动死亡没有充分区分。epoch=41、两次 driver stop failed 证明控制代际变化/收尾异常值得治理，但不足以证明新的 Node heap OOM。

本次采样 driver READY、独立 Managed 与德国 External 均能完成任务。因此优先修真实出口；控制层改进作为有单元复现的后续窄修复，不拿它解释所有 403。

### 4.6 放大风险：容量、历史非终态和模型失败

- 约 14.4 GiB 剩余空间低于当前 20 GiB warning，也低于上次窄构建使用的 20 GiB 预检余量。先解决空间/扩容，不能直接沿用 build.sh 强行构建。
- distribution_runs 约 14,858 PENDING、37 RUNNING，包含 9 月留下的行；其中既有过期现场也有当前活动行，不能整体删或整体重放。实时下一 slot 不自动完成旧 run。
- outbox 采样只有 CONFIRMED 6,095，**没有证据支持通过删除 outbox 解决此次源故障**。
- Jev 日志有 HTTP 402 和大量 timeout；正则仍可独立分发。它不造成 Reuters 访问拒绝，但会拖累共享分发与掩盖“采集成功、分发未完成”的区别。

## 5. 可执行修复方案

### P0-A：先恢复出口/Feed，不碰现有身份数据

1. 重新按 Registry 读取端口/节点/真实出口，禁止按 us/jp 名字猜国家。对现有订阅候选做有界连通性与目标入口验证，明确候选来自 SSRDOG 还是 Flower_SS；当前证据不能证明某一订阅未配置，也不能仅凭订阅名保证质量。
2. 固定 listener 保持固定身份绑定，不改成每请求自动挑最快节点。优先恢复同地区、经实测可用的 NL 节点；确需换国家时明确记录影响哪些 Identity/Site，并通过原维护流程复验。
3. 对 18081/18082 的节点替换采用**一次明确的维护切换**：暂停相关 Identity 新业务、等待自有任务结束、保留原 config 与节点记录、校验 Mihomo 配置后应用，关闭该出口的旧代理连接以免新旧混流，再核验出口。Profile/Cookie 不复制不删除。只变固定节点映射时不升级 Chrome；若实现确需更新绑定 generation，必须先适配 Supervisor/attach fencing，不能再次触发 9/30 的代际失配。
4. `DIGITIMES Taiwan` 可先把生产入口从已失效 18082 改为已验证 18083，或在同轮 XML 解析验收后选择直连。**优先既有德国固定路**，与仓库默认一致；不同时改变免费内容规则和正文身份。
5. 恢复后让正常 60 秒轮询自行取得新成功；不得手改失败计数、last_success_at 或批量回填过期新闻。若目标站出现新 challenge，明确转人工，不不断换 IP 试撞。

相关位置：`site_strategy/egress.py`、固定 listener 渲染/部署入口、生产 ProxyEgress 配置、`message_bus_v2/manifests.py` 与对应 SourceDefinition CAS 更新。

### P0-B：消除“有 fallback 名字但没有 fallback”的假象

1. 增加只读有效候选视图，复用现有解析/健康方法，不另造一套选路规则。逐组合展示 enabled、purpose override、Identity/Profile 存在、egress health、认证要求/状态、manual/cooldown、运行或冷启动容量，以及不合格原因。
2. Site Access 返回组合跳过证据：`egress_unavailable`、`profile_missing`、`identity_disabled`、`auth_required`、`manual_attention`、`purpose_cooldown`。保留原 disposition 枚举和兼容字段，补结构化明细，不给严格旧 PollState 塞不兼容字段。
3. 无候选时根据真实阻塞原因归类：出口问题不变成 RATE_LIMIT，人工 challenge 不变成泛化 EGRESS_UNAVAILABLE；混合原因保留各条证据，不强行选一个误导性的根因。
4. 为明确基础设施撤下的出口返回有界 retry_not_before，Bus 按既有延期契约处理，保留最近真实成功与原窗口；恢复探测独立运行。**60 秒目标 polling 配置不改**，但不把本地门控次数伪装成真实网站请求数。
5. InvestorsHub：先以已登记 Managed 组合做同入口/正文 canary，通过才加入 crawler/body override；不为有备用而放开全部未验证组合。Investing 仍保留人工门控。Barron's 备用认证与 Supervisor 容量明确列为未就绪，不能误报自动接替。
6. Egress 探测增加 last_probe_attempt/next_probe_at 和状态变化事件，记录阶段/异常类型/耗时，不记录秘密。当前失败后 observed_at 不更新，循环会反复探测死出口；改为每出口独立有限恢复节奏及至多小并发，避免串行探测拖延全部站点维护。禁止以切全局 fallback 组代替 Identity 维护。

相关位置：`site_strategy/service.py::_execute_uncached/_run_due_probes`、`health.py`、`schema.py` 的兼容诊断字段、`api.py`、`message_bus_v2/news_adapters.py` / `industry_sources.py` / `scheduler.py`。补回归验证 mixed blockers、零真实 attempt、延期不假成功、单出口失败不阻塞其他出口恢复。

### P0-C：Runtime 锁故障隔离，并查明持锁来源

1. 发布前先取得真实数据库路径、共享使用者和持锁时间线。加入低成本事务耗时/等待指标：进程、操作名、开始/结束、耗时、primary SQLite code；不记录 SQL 参数、文章全文、账号或交易 payload。超过阈值才输出告警。
2. Bus 外层只拦截 SQLite BUSY/LOCKED：短退避、下一轮继续，不能终止整个 worker；FULL/IOERR/CORRUPT 保持明确故障，不 blanket catch 吞掉全部异常。
3. 已完成 `_inflight` 任务在 finally 从集合移除；写 gap 失败不能导致下一轮反复消费相同异常。诊断失败保留脱敏进程日志，不覆盖原业务异常。未成功 claim 的 sweep 不改为成功、不更换 cutoff 或 lease generation。
4. 对 Bus 使用的 RuntimeJournal 增加可选短 busy timeout，默认保持其他调用方兼容；Bus 写操作通过有界异步重试执行。确需线程移交时让**完整短事务**在线程内创建/关闭连接，不在线程之间传递连接，也不在事务中执行网络请求。不得把整个 `run_once` 扔到线程而引入共享状态竞态。
5. 持锁证据确定后，只缩短真正的长事务、把文件/网络处理移出事务或补对应索引。暂不迁移数据库、不重构全部业务 Worker、不牺牲交易持久化级别来提高吞吐。
6. 测试：真实第二 SQLite 连接持有 BEGIN IMMEDIATE；验证 Bus 不退出、事件循环仍响应、另一独立消息源继续采集，释放锁后同一 durable sweep 正常恢复；gap 写入再失败也不产生重启循环。

相关位置：生产基线 `persistent_runtime_v2/bus_orchestration.py`、`message_bus_v2/scheduler.py`、`factory.py`、`persistent_runtime_v2/journal.py`。保持既有 sweep-priority、一次日历查询、lease fencing 和去重补丁。旁侧 Control 的故障单列交接；不借本轮自动重启或全量替换它。

### P1-A：Yahoo 两条失败链分别修，不扩大 Site Strategy 边界

1. browser recipe 错误必须带 phase=`navigation/native_network/hydration/page_js_fetch`、实际 URL（脱敏 query）与 status；外层 acquisition diagnostics 保留浏览器、NCP、RSS 三段结果，最终错误不覆盖前面的 404。
2. 通过当前 owner 对实际 ticker 入口和页面内部请求做有限观察，确定 404 来源后修 recipe；若当前站点确有新入口/响应格式，按真实响应适配，不臆测 URL。仍先 observation，不加资源拦截/stealth。
3. NCP/RSS 改用明确、已验证的 HTTP 固定出口配置，不再依赖当前失效的全局 7893 自动组；沿用独立 transport 的节奏/endpoint circuit。它们不继承 Chrome Cookie、不纳入网站浏览器登录治理。Browser Identity 的 egress 保持自身绑定。
4. 验收浏览器采集和后备采集分别能完成；日志能解释实际成功的是哪一层。最终 publisher 正文仍按最终域名治理，不按 Yahoo 来源强行路由。

相关位置：`message_bus_v2/yahoo_sources.py`、`news_adapters.py`、`yahoo_transport.py`、对应配置与构造 wiring。不改优先级和 30 分钟窗口。

### P1-B：RSS 入口级访问策略，不重构 Browser Registry

1. `SharedFeedAdapter` 保留现有单次重试/总 deadline；新增小型入口级 HTTP 路由配置：稳定 primary 与至多一个**验证过的** fallback，以 egress ID 或受限直连表示，不接受任意外部代理地址。默认兼容旧 feed_proxy_url。
2. 只有连接/读取故障、明确不可用的 primary 才允许执行已批准 fallback；403 不盲目转多个出口，也不把 HTML 拒绝页喂给 XML parser 后伪装空成功。记录使用的路由、HTTP status、XML 有效性、条目数。
3. Taiwan 先恢复既有德国路，验证免费文章正常进入、付费文章仍被原规则拒收/不做无意义补全。
4. GlobeNewswire RSS 独立处理：核对官方生成的当前 feed URL；如果 URL 有效但普通 HTTP 被拒绝，有限验证现有 Managed Globe identity 是否能读取**同一 feed**。只有真实 XML/条目与正文验收通过才采用浏览器 feed fallback。不拿 GlobeSearch 的 200 代替 RSS 验收，不换成范围不同的综合 feed。仍不能访问则明确留为站点访问限制，提供人工/官方入口交接。
5. BE IR RSS 同理，不能凭猜测替换 URL 或默默关掉订阅。

相关位置：`industry_sources.py::SharedFeedAdapter`、`manifests.py`、SourceDefinition schema/参数校验。此处是采集入口的 HTTP 能力，不把所有 RSS/API 重构成 Site Strategy。

### P1-C：控制层有界收尾，保留真实 Chrome 连续性

1. `_track` 的 done callback 观察异常，有限聚合告警，避免无人收尾的任务；close 时取消/等待自有 tasks 并释放 pending futures。
2. `_attached` 的非受治理 target 也必须有明确的恢复/失败路径。受治理 popup 在完成域名门控后才能继续，初始化失败保持安全处理；非受治理 target 的 resume 超时要记录 target/session 并有界重试或判为 attachment 异常，不能静默停在 debugger。不可为消除异常关闭所有人工页面。
3. 健康探测区分 Node 真死亡/连接断开与单次超时。真死亡立即单飞恢复；存活但单次控制超时先标记退化，短期再次确认，持续失败才替换控制驱动。计划轮换继续 idle-only，人工维护继续保护，不停正式 Chrome。
4. 验证一次慢控制响应不重置全部身份；连续失败能恢复；旧 lease 仅释放一次；其他 Identity 不受长期队头阻塞；浏览器 instance/PID/profile 不变。

相关位置：`site_strategy/runtime.py::_RawBrowserCDP`、`external_runtime.py::maintain_driver/_detach_driver`。禁止新增第二个生产 CDP 控制器做长期并行控制，不重写双轨 Runtime。

### P2：容量与过期任务，停止靠人工抢救获得短暂健康

1. 当前 14.4 GiB 不足以继续按 20 GiB 余量构建。优先扩容/明确迁出可恢复历史镜像或备份；任何删除单独列精确对象、保留回滚能力并取得执行授权。本方案不授权删除业务库、Chrome、WAL/SHM 或尚有使用者的镜像。
2. 容量告警沿用现有运维出口；区分进程 healthy、服务能力 degraded、业务源 unavailable。不要因某个出口死就让所有 HTTP/API readiness 一起失败，也不能 healthy 就隐藏源不可用。
3. 旧 distribution run 按原 realtime/sweep 上下文判定是否已不可能继续：仅无有效 claim、没有未完成有效 delivery、且原窗口已过期的 run进入明确 EXPIRED/ABANDONED 终态，保留原因和审计。有效 sweep 保留原 cutoff，禁止批量重放。增加必要的状态/时间索引，避免健康采样全表扫描大 JSON。
4. Jev 对 402 记录 provider_unavailable，保留当前每条有限尝试及正则正常发布；增加短期 provider circuit 防止同一额度故障持续占用实验分发能力，恢复半开探测。不得自动充值、换 provider、扩大无关消息分发或把未知直接判相关。

## 6. 人工维护与逐源恢复顺序

先恢复出口和错误证据，再由 xRDP 原“消息源登录维护”处理，避免人在死代理上反复输入账号：

1. **InvestorsHub、Taiwan RSS**：优先用可确定的网络/配置修复恢复，分别验收列表与免费正文；无需预先要求用户登录。
2. **Reuters**：先复测恢复后的 Managed 主路；另由用户维护 `digitimes-de-1` 的 Reuters 行，验证实际搜索，不是 DIGITIMES 行或 Reuters 首页。德国备用目前确有 challenge。
3. **Barron's**：`dowjones-main` 出口恢复后人工确认当前拒绝页、登录及最新订阅文章。主路本就有真实 403；不要先归零风险。备用只有实际认证和容量允许后才成为可用 fallback。
4. **Investing**：维护 `public-markets-nl` 的 Investing 行，实际 ticker news 列表验证成功后解除该 Site 的人工门控；共享 Identity 不意味着同步解除 InvestorsHub/其他 Site 的健康状态。
5. **DIGITIMES English**：维护有订阅账号的原 Profile，验证两篇当前付费正文。明确德国组合是否具备订阅权限；它当前只证明列表可用。不能放宽付费墙识别来宣布成功。
6. **Yahoo、Globe RSS、BE IR**：分别按前述分支/官方入口验收，不承诺人工登录必然能恢复 HTTP API/RSS。

仍由人完成 MFA/CAPTCHA；不保存密码、不复制 Cookie、不做自动 challenge、TLS MITM、随机 UA 或频繁轮换身份。

## 7. 发布与验收标准

### 发布边界

- 本轮方案不启用 Source Maintenance Codex Worker；业务 codex-worker 镜像名包含 maintenance 不代表该功能已开启。
- 先做可回滚的入口/节点配置修复，再发布对应最少服务的代码；容量不足先停构建，不从脏本地全仓直接 build/push。
- 逐文件记录原镜像 digest、原文件 hash、补丁符号、配置 CAS revision 和回滚方式；正式发布必须包含既有 9/30、10/01、10/04、10/06、10/09 修复或明确回滚，不用旧 overlay 覆盖新版业务代码。
- 预计仅 Site Access / Message Bus 需要代码窄更新；只有实证涉及正文延期实现才更新 Enrichment。默认不重启 Supervisor、Chrome、Scheduler、Clash 容器或研究/交易服务。固定节点配置维护若需 Mihomo reload，单列影响与收据。

### 技术验收

1. 故障固定出口能识别/恢复，其他出口维护不被其阻塞；服务返回具体 unavailable 原因而非一律 RATE_LIMIT。
2. 已登记但 override 排除、人工关注、未认证、容量不足的候选可明确解释，不被声称为可用备用。
3. 真实 Runtime 锁竞争下 Bus 不退出、网络事件循环可服务；原 lease/cutoff/去重不变，释放锁后继续。
4. 只读 RPC 重试、cleanup deadline、driver recovery、profile single-writer、30 分钟实时准入和闭市 sweep 回归均保留。
5. 控制层异常被收尾，单次慢探测不重置全部 Runtime；真断连可恢复且正式 Chrome 实例不变。

### 业务验收

- 四个 ticker 的 by-ticker/by-search 分别验收；共享 distribution 以一次真实采集、对应订阅 roster 和正文/分发结果验收，不能把三份 PollState 当三次独立抓取。
- 每个需要恢复的源至少 10 个自然目标轮询周期，保留真实 attempt、成功/部分成功/延期/拒绝及最后成功时间。零条但有效结果可算采集成功，不冒充产生新闻。
- 对有当前新文章的来源验收至少两篇最终 publisher 正文；付费源必须全文而非预览，第三方来源按最终域名走对应策略。旧文章可作只读诊断，不能发布到实时流。
- 节点/登录维护完成后观察 24–48 小时并覆盖下一次闭市 sweep；尚未经历的窗口明确未验收，不能以本轮短采样承诺永久稳定。
- 对首次拒绝到恢复间的缺口单列，不绕开时效性规则补发历史新闻。

## 8. 最终判断与证据限制

这轮可以确定：**荷兰固定出口故障是集中停采的主要新共因，缺乏有效接替和精确错误契约使它演变成多源长时间不可用；Runtime 写锁异常又能把局部等待升级为进程重启。** 同时存在未完成人工维护的旧 challenge、付费权限和独立 RSS 拒绝，不能用一个公共修复将它们全部宣称解决。

尚不能确定：荷兰上游故障的运营商内部原因、Runtime 长事务的具体持锁进程、Yahoo 404 的精确响应阶段、每次 137/驱动 epoch 变化的完整因果链、网站不公开的风控打分规则。上述点已经分别给出下一步取证与修复边界，不能用猜测替代。

本轮使用 project-discovery 技能核对本地脏树、工具链和部署差异，因此明确保留符号级生产基线合并边界；没有借诊断任务执行生产重构或全量发布。
