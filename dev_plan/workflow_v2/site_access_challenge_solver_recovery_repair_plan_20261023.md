# Challenge Resolve 与 DataDome Solver：诊断及可执行修复方案

日期：2026-10-23；状态：方案，尚未实施。范围：Site Access、恢复/接替、2Captcha 结果交接及维护入口；不重写消息总线或浏览器架构。

## 1. 结论与证据边界

目前不是“2Captcha 完全没有接通”，也不是“剩下的问题只是网站风控”。供应商异步提交、同出口代理、Cookie 应用以及至少一次真实采集恢复已经发生；但存在可从当前代码确认的内部缺陷，使“已验证恢复”不一定转化为可用路由，“已打码”也缺少可追踪的业务结局。

优先修复四件事：**双向、按能力选择实际可用身份；统一恢复预算并保证探测闭合；补全打码后原页→业务→路由闭环；恢复后的有界流量平滑与复发约束。** 同时收敛分类与页面现场一致性、桌面维护反馈和发布/存储治理。

必须明确：不能承诺这些修复会让 Reuters 永久放行。多次恢复后快速再挑战是真实现象，但现有记录不足以区分出口信誉、请求行为、页面状态迁移与其他外部风控因素，更不能据此承诺“多买几次验证码就能稳定”。

### 1.1 使用的记录

- 用户指定：[00:30 验收记录](../../eval/device_check_deadlock_20261022/heartbeat-20261022T0030Z.md)。其中新恢复的是 **备用**，不是主身份第二笔付费任务；备用恢复后约1分51秒再次挑战。
- 后续[02:30](../../eval/device_check_deadlock_20261022/heartbeat-20261022T0230Z.md)：主身份02:01:54 UTC 搜索200/20条/READY，活动路由却仍是受阻备用，九ticker未恢复。
- [12:38](../../eval/device_check_deadlock_20261022/heartbeat-20261022T1238Z.md)：备用恢复后4次采集、2条普通FULL，约1分54秒复发。
- [18:44](../../eval/device_check_deadlock_20261022/heartbeat-20261022T1844Z.md)：备用又恢复2次采集，约1分15秒复发；第5笔APPLIED，未证明之后业务恢复。
- [死锁修复实施记录](../../docs/device-check-deadlock-repair-20261022.md)、[DataDome接入记录](../../docs/datadome-2captcha-implementation-20261022.md)。其中初稿“无付费任务”和早期版本信息是历史时间点，不能当作当前状态。

所有时间对比均按UTC；北京时间加8小时。seen_count、分类变更、旧Task更名、同一事件探测均不是新增Challenge。

### 1.2 本次代码与只读复核

- 本地 main/本地记录的 origin/main：`3d0ae94c`；未fetch、未提交。工作区有其他会话的changelog和评估产物，只新增本方案，不覆盖。
- `6dbdb4e9..3d0ae94c` 的 `src/doxagent/site_strategy` 与 `message_bus_v2/reuters_sources.py` 无代码差异。这些内部问题仍存在于当前代码，不是根据旧日志猜测。
- 生产只读观察：2026-10-23 11:24:40 UTC（北京时间19:24），四个相关容器标签均为`3d0ae94c`；Supervisor已于10-22 20:19:33 UTC再次重建。旧心跳里的“Supervisor未重建”不再成立。仅核实标签/启动记录，不冒充逐文件哈希验收。
- 当时Reuters活动身份仍为`reuters-backup`；主`digitimes-de-1`旧恢复仍RESOLVED。备用新事件BACKOFF/HUMAN、82次尝试、最后完成探测04:53:46 UTC，说明“不断调度但实际探测回执停滞”又出现。
- 当时累计6笔付费任务均APPLIED，已记录费用$0.00870；第6笔发生在原24小时观察窗口之后，不能混入原窗口费用。未访问provider API，未读取凭证/Cookie，未触发任何新闻网站请求或恢复动作。
- 原窗口 **[2026-10-21 20:23:20Z, 2026-10-22 20:23:20Z)** 用只读SQLite上下界重新聚合：InvestorsHub 5397个CRAWLER SUCCESS、305个BODY SUCCESS、117个FULL、31个SHORT_FULL；Reuters 24个CRAWLER SUCCESS、2个BODY SUCCESS及2个FULL。均为回执，不是独立文章数或ticker完整轮次。
- 原窗口Reuters：PROBE_STARTED170、PROBE_FINISHED133；不直接把差37全部认定为超时，但它明确暴露终态记录不完备。5个新RECOVERY_STARTED、5个RESOLVED记录不等于5次持续恢复。
- 本次离线执行`tests/test_datadome_solver.py`、`tests/test_browser_recovery.py`：**78 passed /71.60s**。现有测试通过，但没有覆盖下列关键复合链路；不是生产成功验收。
- 本次脱敏补证：[只读审查摘要](../../eval/challenge_solver_audit_20261023/summary.json)，区分固定24小时窗口和10-23追加观察；不含凭证、Cookie或验证码参数。

## 2. 问题分层：什么已经查实，什么还不能定因

| 优先级 | 问题与代码锚点 | 结论 |
| --- | --- | --- |
| P0 | `service.py:1601–1606`活动备用时过滤掉主身份；`failover.py:43–61`只调度fallback且活动fallback直接返回；`recovery.py:855–869`恢复只清门禁不接替路由 | **确定性接替缺陷**。02:01主恢复却九ticker持续受阻有直接代码解释。正常stickiness不应升级为活动身份失效后永不切换。 |
| P0 | `recovery.py:465`外层90秒，先做solver/原页观察，再排空最多20秒，随后仍给probe固定75000ms；`worker_runtime.py:172–196`又含独立席位/Worker等待 | **预算不一致**。所有子阶段都可能合法耗时，却超过外层期限；不是简单“把90改180”能根治。 |
| P0 | PROBE_STARTED在`recovery.py:583`写入，FINISHED只在正常返回/验证后`713`写入；异常`757–769`仅defer | **探测终态缺失**。本地控制/排队/超时与网站拒绝混淆，last_real_probe_at不更新不能判断到底没访问还是访问无回执。 |
| P0 | `datadome_solver.py:144–149`遇APPLIED直接退出solver；费用只在`browser.apply`之后保存；`api.py:180`只展示status/taskId/cost | **付费结果无业务结局**。数小时后恢复仍挂APPLIED，既不能证明及时有效，也无法快速区分写入成功、网站接受、持续可用。 |
| P1 | `datadome_browser.py:217–247`与`challenge_detection.py:assess`各自推断；Native采用`frame_html or html`，读取失败也可能根据URL推为SOLVABLE；native frame observer未完整验证实际可见阻断 | **证据契约不一致**。需统一采样，不再靠旧iframe.src、页面标题、父DOM有正文或“vendor资产存在”决定。 |
| P1 | `service.py:1423–1448`cache key含完整request/request_id，现有single-flight不是相同业务查询的跨ticker合并；正常JointBudget有节奏，恢复探测直接走Worker独立容量 | **恢复路径缺少统一节奏约束**，恢复后多个ticker/L1/BODY迅速到期有可能形成访问集中。它是合理机制假说，尚不是已证实快速复发根因。 |
| P1 | `retention.py`7天删除solver_jobs不区分是否仍关联未结束恢复；browser_recoveries按identity覆盖旧事件；费用缺失场景不能完整追溯 | **长期幂等/审计边界有缺口**，特别是事件超过7天后不能因删除作业记录重新购买同一旧事件。 |
| P1 | 多次整体发布重建Supervisor、原页真实丢失；后续新页PRESENT不能代表原页连续保留 | **发布污染验收与人工现场**。Cookie持久化不等于活页持久化；不能通过数据库“标LIVE”修复物理丢页。 |

### 2.1 必须保留的正确能力

保留双轨Runtime、独立Browser Identity/持久Profile、实际Chrome环境、同Identity单写、OPEN人工会话优先、精确target/instance/allocation所有权、备用真实业务验证、配置/出口generation/同IP校验、Cookie域与实际publisher可见性校验、每事件至多一次购买、提交不确定不重买、全局24小时预算、后台周期性恢复以及各能力独立提交证明。

不重新实现Squid/Clash，不迁移Profile，不伪造UA/Client Hints、不加stealth/randomization、不修改验证码参数、不将`t=bv`硬改成`t=fe`，不创建新后台服务、通用工作流引擎或管理前端。

### 2.2 外部机制边界

[DataDome Device Check官方说明](https://docs.datadome.co/docs/device-check)：该验证无需人工交互，可能放行、拒绝或追加验证码。因此Device Check不能直接当滑块，也不能无期限等待。

[2Captcha DataDome接口](https://2captcha.com/api-docs/datadome-slider-captcha)：需要真实captcha URL、浏览器UA及代理；`t=bv`是IP拒绝，不能作为正常可解滑块付费。[getTaskResult](https://2captcha.com/api-docs/get-task-result)的ready表示供应商完成，不是publisher或业务已恢复；其`ip`是任务提交请求IP，不应误用为浏览器代理出口的验证值。保留现有实际代理出口预检，不拿该字段代替它。

## 3. 统一成功语义，但不堆状态机

继续使用BrowserRecovery现有phase和ChallengeTask状态。新增少量正交字段，不把整个业务切成十几种全局状态。

1. **验证页面结局**：NONE/AUTO_CHECK/INTERACTIVE/ACCESS_BLOCK/UNKNOWN，附观察完整度和原因。UNKNOWN不是失败也不是放行。
2. **业务能力**：每个scope×identity×purpose的READY/FULL/SHORT_FULL证明，绑定当前配置、实例、认证session与出口。CRAWLER通过不能认证BODY；没有登录不影响本来公开的采集能力。
3. **路由与运行稳定性**：路由激活是独立CAS动作；成功激活后仍需自然请求证明。短暂恢复单列`probation`，不修改真实SUCCESS回执，也不把一次canary等同于长期健康。
4. **solver**：旧`status`保留，APPLIED继续只表示Cookie交接完成；新增`verification_state`和相关时间/结局，避免破坏当前API消费者。

“清除Challenge”与“账号有效”“全文权限”“已接替”“持续稳定”在管理API/GUI分别显示。允许显示“Challenge已解除；CRAWLER可用；BODY需重新登录”，而不是总显示Verify失败或全绿。

## 4. P0-A：修复双向接替和候选解释

修改`failover.py`、`service.py`、`health.py`、`governance_repository.py`，沿用每scope的主+一个备用，不加第三身份。

### 4.1 核心规则

- 活动身份对本次purpose真正可用时保持sticky，不因主身份恢复就强制抢回路由。
- 活动身份对本次purpose受阻/不可用时，检查**另一身份**，不管它名为primary还是fallback。主恢复后可以接替坏备用。
- 删除`service.py`的“活动是fallback就永久只保留fallback”的过滤；把“可用活动优先”纳入统一选择函数。
- `schedule(scope,purpose, candidate_id=None)`默认解析另一成员；`activate`支持明确candidate_id并校验必须属于scope。保留显式ACTIVATE_PRIMARY/FALLBACK管理操作兼容。
- 无可用候选才返回RECOVERY_PENDING，并精确指向对应身份/Task/下次调度；不能先遇到一个受阻成员就掩盖另一个可用成员。

### 4.2 准入与激活

新增/提取纯判定函数`candidate_status(scope,identity_id,purpose)`，供执行、effective_candidates、自动接替和GUI共同使用，避免解释与执行再分叉。

必须检查：成员启用、Profile可用、无OPEN单写占用、该purpose不被Recovery阻断、真实出口可用、当前认证要求、能力proof与当前配置/实例/session一致。禁止只看member.state=READY。

已有当前实例的暖证明（例如恢复Coordinator刚提交的READY）可作为**一次自然业务准入**依据，无需先停Chrome再冷启动重验；旧实例/过期证明不能直接激活。冷备用仍保留冷启动实测语义，但不要无条件停止已经有有效暖证明的Runtime。付费BODY首次准入保留既有订阅证明约束；不要给公开CRAWLER附加订阅正文要求。

恢复commit后投递/触发一次既有`FailoverCoordinator.reconcile(scope,purpose)`；仅活动能力受阻才接替，实际成功后通过health.activate的generation CAS提交。并发变更重新评估一次，不循环抢路由。

当前只有一个活动route指针：不为此次需求引入按purpose双路由。若活动CRAWLER可用但BODY需另一成员，BODY在该请求内使用另一已验证成员，不因单条BODY抢走健康采集路由；确需全局接替时记录触发purpose。两边均失败则保留后台恢复/人工，不假造可用备用。

### 4.3 验收

确定性回放：活动backup受阻、primary刚恢复、无新网站请求时能给出primary有效候选；下次自然poll不再被坏backup永久截断。反例覆盖：活动backup健康不回切、primary旧实例proof拒绝、BODY未授权仍阻断、OPEN Viewer不被抢占、CAS过期不覆盖新route、effective_candidates与实际选择完全一致。

## 5. P0-B：预算、控制席位和探测回执闭合

修改`recovery.py`、`worker_runtime.py`和必要的`worker_manager.py`，保留现有2个Recovery任务槽、1个独立浏览器恢复席位；不增加常驻Worker。

### 5.1 统一截止时间

- 每次Recovery tick继续最多90秒；在开始时计算单一monotonic deadline。所有等待、读取、排空、Worker ensure、导航/解析、交接、收尾均扣减同一剩余预算。
- 预留10秒给ownership ACK、持久回执和释放；实际probe预算=`min(75s,deadline-now-10s)`。不足10秒则不发网站请求，明确PROBE_DEFERRED/BUDGET，不开始一条注定被外层截断的probe。
- 排空20秒只是上限，实际使用remaining；原页观察最多10秒。solver tick与长probe不在同一个tick内叠加；supplier poll不占Profile控制权，仅应用结果时短暂取得单写。
- 独立恢复席位等待设短上限（默认5秒并受总预算约束），拿不到就defer；控制任务不应拿着Profile控制锁等很久才能获得全局席位。统一固定获取顺序，避免手工控制/自动恢复互相锁住。
- 不能通过放大90秒掩盖缺陷，不能把预算不足、controller通信失败或排队失败标成网站Challenge。

### 5.2 每次尝试都有闭合结果

在PROBE_STARTED时持久`probe_id`（recovery_id+generation+sequence）、stage和开始时间。终态统一在try/except/finally输出一次PROBE_FINISHED，结果明确：BUSINESS_READY、STILL_BLOCKED、QUEUE_DEFERRED、CONTROL_DEFERRED、RUNTIME_UNAVAILABLE、TIMEOUT、CANCELLED、SUPERSEDED、PARSER_ERROR。

记录`request_sent`为true/false/unknown，queue_ms/control_ms/site_ms/total_ms、安全reason_code、保留现场结果；禁止记录凭证、Cookie或原始验证码URL参数。没有导航回执就写unknown，不凭PROBE_STARTED推断已请求网站。Worker协议可选增加`navigation_started`/stage回执；无需重做IPC协议。

`last_probe_attempt_at`与`last_real_probe_at`分开，后者只在有实际网站回执时更新。进程异常中断后，过期probe由下次claim标INTERRUPTED，不伪造网页结果。所有finish用probe_id/generation去重。

把外层只记TimeoutError的路径拆出当前stage/错误类型；浏览器控制通道需要退役重连时只替换该Identity的控制Worker，External Chrome进程、Profile和人工原页不能被顺带重建。

### 5.3 回归

用虚拟时钟/可控Future测试：原页读10秒+排空20秒+probe尝试75秒时，实际probe获得的是剩余预算而非固定75；队列饥饿、Worker.ensure卡住、ACK丢失、超时、取消均有终态且释放所有席位；90秒tick不会持有超过100秒lease。活动Viewer被阻止后台改页，但其他Identity/HTTP继续运行。

## 6. P0-C：把solver接到原页和业务结局，而非停在APPLIED

修改`datadome_solver.py`、`datadome_browser.py`、`recovery.py`、`api.py`；作业仍用现有SQLite表JSON，不新增独立服务。

### 6.1 字段与持久顺序

新增可选字段：`provider_ready_at`、`provider_cost`、`applied_at`、`verification_state`、`verification_reason`、`original_cleared_at`、`business_verified_at`、`business_probe_id`、`verified_purposes`、`route_activated_at`、`natural_success_at`、`recurrence_at`。

`verification_state`取PENDING/ORIGINAL_CLEARED/BUSINESS_VERIFIED/STILL_BLOCKED/INDETERMINATE/SUPERSEDED，不能与旧status混用。旧APPLIED没有关联证明默认INDETERMINATE，不补造“成功”。

1. getTaskResult获得ready后，**先持久provider_ready_at及实际费用，再尝试写Cookie**。应用失败或场景已换也保留已付费用；费用未知保持null，不能当0。
2. 已有taskId的结果查询与浏览器应用解耦：原页丢失/变更也可在既有有界期限内收集该task的最终收费结果，但不允许应用到新页/新出口；不再购买替代task。supplier poll不需持有Profile单写锁。
3. provider solution仅在内存短暂使用，不把Cookie写进job/event/Git。应用后仍验证实际publisher URL可见；配置/egress/instance/target/lease fence继续保留。
4. Network.setCookie成功但刷新/回执失败，要记“应用结果不确定”，而不是丢掉收费信息。下次仅可对同task、同scene/绑定做有界确认；不默认反复setCookie/reload或重新购买。

### 6.2 应用后的有限确认

- APPLIED记录verification_state=PENDING，主动调度一次短tick，而不是立即进入人工10分钟probe周期。
- 先观察**同一精确原页**让网站自行完成跳转/验证；以5/15/30/60/120秒的有界观察点推进，每tick做一次读取，不在一个tick内sleep两分钟。此期间不制造新captcha页、不重复刷新，不抢OPEN人工页。
- 原页已具备可信listing/body合约且当前overlay消失：按实际能力提交proof；缺少完整业务证据时，再调度最多一次独立真实业务probe（用受影响query/文章，不用首页替代）。不能为了证明“干净”又抢先打开多个页面。
- 120秒仍有真实验证/拒绝则verification_state=STILL_BLOCKED；原页/网络/DOM不确定则INDETERMINATE。保留Task及原页，恢复既有10分钟后台probe和实际可用另一身份接替，不能永久停用消息源。
- 后续自然或恢复probe成功，记录late recovery及时间。超过即时确认窗口的成功只能叫“该事件后来恢复”，不能自动标为该paid result即时成功。provider结果、Cookie应用、原页通关、业务验证、自然轮询恢复分别展示。
- 恢复事件结束/被替换时更新对应job验证结局，不让旧APPLIED永远无结论；不能将同Identity另一新事件的SUCCESS错关联到旧job。

### 6.3 RETRY与拒绝边界

保留每事件一次正常刷新重生成；持久记录刷新时间/结果，不因重启或新Task名再次刷新。真正加载的`t=fe`+RETRY可在这一刷新后重新观察是否可解；明确`t=bv`/ACCESS_BLOCK不直接购买。Device Check不以SliderTask盲投。旧paid scene变成不同验证码，原paid结果不能写给新scene；保留人工和fallback，不靠篡改URL解决。

## 7. P1-A：统一验证采样、页面存在性与分类

修改`verification_frames.py`、`challenge_detection.py`、`datadome_browser.py`、`maintenance_document.py`。

- 提取一个共用`observe_verification_document`，返回精确parent target、真实loaded frame URL、frame_read_state、main/frame文案结构信号、可见性/遮挡证据、presence_state与assessment。自动恢复、人工Verify和solver只解释这一个采样结果。
- 精确DOM owner→frameId→OOPIF方法保留。不扫描其他tab，不把旧iframe.src当当前导航，不因父页面有搜索结果就忽略已加载的全屏阻断。
- 对需要付费的SOLVABLE判断：真实允许域的`/captcha/`、受支持t、实际加载、可见阻断与可读/可信验证信号一致。无法读取子页保持UNKNOWN，不使用`frame_html or parent_html`回填后假装已读过滑块。隐藏/零尺寸vendor资产不构成阻断；可见性读不到也不能假装无阻断。
- presence与challenge分开：`PRESENT_BUT_UNREADABLE`不能写TARGET_LOST；只有Supervisor实例确认变化/该target确实缺失才LOST。GUI使用presence/check时间，不让Recovery.UNKNOWN和Task.LIVE造成“原页可靠留存”的误导。
- 保留AUTO约3–5分钟有限等待，明确拒绝立即人工复核；UNKNOWN长期无法观测也有REVIEW出口。REVIEW是技术/人工检查，不声称人类能解自动设备验证。
- 稳定人工复核原因，不在同页每次采样都HUMAN↔AUTO反复跳变/重复写CLASSIFICATION_CHANGED。只有有意义的kind/原因/loaded scene变化才记新分类事件；Task不会因旧父DOM重新隐藏。
- 普通登录页/登录提示/广告不当Challenge；真正full-screen验证不能因有正文DOM而放行。补同一HTML在人工/自动/solver的三方一致性正反例。

## 8. P1-B：短暂恢复后的平滑接入与复发治理

这部分解决可控的集中请求和重复付费，不以“限速一定能治好风控”为前提。**正常source仍24小时、60秒调度**；不能借此改300秒poll或漏掉ticker/L1。

### 8.1 5分钟恢复观察期

在现有scope/identity runtime JSON增加`recovered_at`、`probation_until`、`recent_relapses`（最多8条）及`last_recovery_id`，跨Site Access重启保持。

Reuters恢复后默认5分钟单并发、Identity实际导航最短间隔10秒；到期且有自然成功再回原配置3秒。保持调度60秒，对排队/窗口未完成输出明确延期，不能伪造SUCCESS或将L1标done。其他站点默认不提高间隔，按其真实复发证据启用。

恢复probe、canary、人工Verify的独立业务页和自然采集/BODY都使用统一Identity节奏；Profile单写与HTTP隔离不改变。原页纯DOM观察/provider轮询不计导航次数。只做轻量共享时间门禁，禁止持有JointBudget业务席位后再等第二套互锁队列。若恢复控制已排空同Identity业务，可使用同一节奏器的control入口，不能绕过间隔。

恢复时不补发一串过期tick；同ticker按当前窗口继续。按ticker×query公平排队、保留未完成checkpoint，日志明确capacity deferred而不是Challenge。该临时节奏可能使多个L1完整轮次超过60秒，必须真实展示延迟，不许承诺所有查询60秒内完成。

### 8.2 精确同查询合并

当前request_id级cache保留不动。仅为只读CRAWLER recipe增加业务键single-flight/短缓存：scope、purpose、recipe版本、规范query/offset、scope/策略revision、identity及配置/实例/session/egress generation、当前route generation。不同query/分页/身份不得合并；不缓存Challenge/超时为成功。

共享结果TTL15秒、内存有界（复用现有32MiB总预算），用于相同词跨ticker或重叠调度的重复请求，不替代60秒轮询。返回给每个调用者复制其request_id/operation_id与独立消费信息，附`served_from_shared_capture`/`capture_id`；只记录一次物理ACCESS_RESULT，其余为cache/coalesced消费回执，验收不能把复用次数当新的成功网站访问。正文仍按现有正文队列/去重，不把全站内容塞进新缓存系统。

### 8.3 快速复发与付费上限

- 真正恢复后5分钟内新Challenge记fast_relapse，关联previous_recovery_id和原scene证据；不同incident ID仍保留，不靠合并Task掩盖频率。
- 同Identity10分钟内最多2次付费创建尝试（含提交结果不确定的预留），现有全局滚动24小时20次仍保留；超限仅禁止新付费，不阻断后台probe、另一身份接替或人工。
- 连续2次fast_relapse后观察期延至15分钟（间隔仍10秒），提示身份/出口需要检查。此限制跨事件和重启生效；不能每次Recovery换ID就重新获得无限额度。
- 不自动换IP、不重建Profile。后续若证据指向IP拒绝，走已有受控egress更换；切换前核实另一身份真实能力，切换后重做solver绑定/同出口校验，不让旧paid结果跨出口应用。

## 9. P1-C：人工维护与自动恢复收敛到同一结局

修改`maintenance.py`、`service.verify_scope_session`、`api.py`、`deploy/site-login-ui.py`/受限桥接。

- Verify结果清楚分列：Challenge已清/仍交互/访问被拒绝/证据不足；CRAWLER；BODY；登录；当前route是否真正可用；原页是否存在及检查时间；solver阶段/费用/即时或延后恢复。
- Challenge已清、公开采集READY但BODY需要重新登录时，结束Challenge维护与释放Viewer，登录走独立Open Login；不要要求订阅全文成功才能承认所有Challenge已清。真实BODY仍不能发布不完整付费内容。
- 普通Open Login不跳转到旧Challenge恢复；关闭Viewer/Later保留Task和原页，不等于关闭事件。
- 手工Verify与自动恢复共用finish_recovery/candidate reconcile；用户不能遇到“报告CLEARED但永久卡人工门禁”，也不允许一次登录成功跨身份清另一个Task。
- 操作创建后立即展示operation_id/stage，已有阶段超时明确结束并展示安全原因；不新增前端框架。自动观察窗口不让GUI持续Working数分钟。提供“仅重新检查/释放Viewer/另开登录”既有动作的清晰入口，不把重试按钮实现为无条件刷新验证码。

## 10. P1-D：审计保留与发布边界

### 10.1 有界存储、不能删掉幂等依据

`retention.py`调整solver保留条件：关联未结束Recovery或保留中的人工Task、PENDING/SUBMITTING/应用结果未确定作业不能按7天直接删。至少保留该事件“已创建/可能已收费”tombstone直到事件结束后7天；历史终态默认30天（脱敏metadata），原始solution不持久。

不靠给活跃作业刷updated_at续命；按生命周期与关联条件判断。已有过期旧记录缺失时，不为同一历史事件自动补买。

browser_recoveries仍每Identity一条当前投影。历史恢复概要从事件/solver聚合，记录recovery_id、身份/作用域、开闭时间、原因、结局、真实业务/路由时间；可增加一张小型`recovery_incident_summaries`表（仅在现有事件保留无法支持30天查询时采用），不复制HTML或新增事件平台。

现有targets CLOSED/LOST 7天保留与分批GC先核实运行指标。窗口内历史target增长不是活页泄漏，不能激进删除Profile/Cookie；观察GC backlog、到期行与物理SQLite/WAL尺寸。活跃targets、Task、会话及solver仍引用的行受保护。截图继续复用first/confirmed，只在新scene/有意义状态变化捕获，设置总量/字节上限；不能每15秒截图。

### 10.2 发布

1. 此次后续实现优先只构建/重建Site Access。`worker_runtime`/`identity_worker`在Site Access镜像内，不能因修改这些文件就顺带重建Supervisor。
2. 只在API返回字段/UI展示改动时原位安装桌面GUI/桥接；无需重装xRDP。Message Bus如无实际消费逻辑修改不重建。代理配置无需变则不重建captcha-proxy/Clash。
3. 发布前保存运行容器manifest、精确源码hash、当前instance/target/Task及证据清单、只读SQLite在线备份；有OPEN人工维护先协调，不静默抢占。
4. 不更新Supervisor镜像标签以凑“所有服务同commit”；多组件不同已验收版本允许存在。若未来确需Supervisor升级，另行明确现场损失边界；Cookie可留、活页不能跨重建，丢页必须TARGET_LOST而非伪恢复。
5. Site Access新版本启动只做兼容字段补全，不重建Identity/重置计费额度/重新购买历史任务。不把历史APPLIED全部改FAILED后触发重买。
6. 单服务回滚前保留新字段/事件/费用台账；回滚镜像不回滚业务SQLite/Profile。发布前后验收按实际组件版本、启动时间分段，原观察截止不随发布重置。

## 11. 开发顺序与具体交付

| 步骤 | 文件/组件 | 必须交付 |
| --- | --- | --- |
|1|`recovery.py`,`worker_runtime.py`,`worker_manager.py`,`governance_models.py`|共享deadline、probe_id、阶段终态/过期中断对账；先消除不可观测尝试。|
|2|`failover.py`,`service.py`,`health.py`,`governance_repository.py`|双向候选/统一解释、健康sticky、坏活动接替、当前purpose暖证明准入与CAS。|
|3|`datadome_solver.py`,`datadome_browser.py`,`api.py`|ready先记账、应用后有限观察、业务结局关联、结果不确定不重买。|
|4|`verification_frames.py`,`challenge_detection.py`,`maintenance_document.py`|统一采样契约、OOPIF可见阻断、UNKNOWN/LOST分离及分类稳定。|
|5|`budget.py`,`service.py`,`recovery.py`,`schema.py`|恢复观察期与统一导航节奏、精确查询single-flight、独立物理/复用回执。|
|6|`datadome_solver.py`,`governance_repository.py`,`retention.py`|跨事件快速复发计数/付费上限、活跃幂等记录不误删、有界审计。|
|7|`maintenance.py`,`api.py`,桌面GUI/桥接|分项Verify结果、恢复/接替阶段、可完成的人工入口；英文UI保持现有可显示字体。|
|8|测试/部署脚本与changelog|完整回归、最小发布manifest、逐文件hash与真实业务验收报告。|

步骤1–4是稳定性核心；5–7同轮完成但不以部署更多基础设施替代。配置在现有ChallengePolicy/访问配置中小幅扩展；默认对旧记录兼容，Reuters启用恢复观察期/solver功能，其他来源保留现有正常速率。

## 12. 测试矩阵与真实验收

### 12.1 必须新增的回归

1. 坏备用活动+主READY暖证明→主可接替；正常备用不抢回；三种purpose独立；有效候选解释与执行一致。
2. 同一source九ticker只有坏Identity受阻，另一Identity/其他网站/HTTP不受阻；Recovery返回不提前终止另一成员选择。
3. 外层deadline与排空/容量/IPC子预算一致；任意异常/cancel/重启后probe有唯一终态；未发网站请求不计网站失败。
4. provider ready已收费但Cookie失败/页面变更/进程中断仍留账；已有taskId不重买；提交不确定保留预留。
5. Cookie正确作用域及publisher可见性保持；APPLIED后自动原页观察，有界恢复/拒绝/超时/丢页；不能把对方Identity成功归给该job。
6. 原页消除overlay但无业务证明，不伪FULL；父文档有完整新闻但有可见全屏验证仍阻断；隐藏vendor资产正常；空子DOM/旧iframe.src不假SOLVABLE。
7. RETRY仅刷新一次、重启不重置；t=bv/Device Check不付费盲投；OPEN Viewer期间不写Cookie/不刷新/不导航。
8. 原页确定丢失与读超时分别处理；旧原页Task和新probe页引用不可混用；普通登录不清/抢Challenge。
9. 恢复后九ticker排队不突发、不饿死；重复query合并但不同ticker消费和checkpoint独立；缓存命中不扩增物理成功数；60秒/24小时调度不变。
10. fast_relapse跨事件、进程重启仍计数；局部付费上限不能冻结免费probe/人工；7天GC不会删除活跃事件的已购依据。
11. Verify已清但登录掉号只转登录维护；CRAWLER恢复而BODY失败可继续采集；高资源或无剩余预算明示延期。
12. Site Access单服务重建保留Supervisor container/instance/原页，兼容旧job/Task/协议；旧镜像回滚不重买不丢账。

测试入口优先扩展既有`test_browser_recovery.py`、`test_datadome_solver.py`、`test_source_maintenance_refactor.py`、`test_challenge_detection.py`、`test_challenge_evidence.py`，必要新增`test_recovery_route_reconciliation.py`与`test_solver_business_outcomes.py`。时间用可控clock/Future，不在测试里真实睡90秒、不调用provider或新闻站。

### 12.2 真实生产验收，不能恢复一次就交付

先完成确定性回放和本地集成，再经授权窄发布。没有自然可处理验证码时不制造付费场景，也不为了验收长期守候。

分开验收：

- **内部确定性闭环**：对应复合badcase逐条验证，不允许有“primary已证明READY而坏backup唯一可选”；本地失败的每次probe都有技术终态；每笔费用与应用/业务结局可追踪。
- **一次自然solver链**：真实现成滑块→提交→ready/费用→同scene/同出口Cookie可见→原页结局→真实listing/body→自然poll；如果仍失败，交付的是明确失败原因和可继续人工/备用的路径，不能报告打码业务成功。
- **稳定性**：连续24小时自然记录，至少跨3个完整ticker调度轮次；按query/ticker检查覆盖、延期和正文，而非只合计ACCESS_SUCCESS。Reuters是否达到可用单列，不用InvestorsHub高成功数掩盖。
- **复发**：逐事件列恢复至复发时长、此前真实访问数、query/目的、原/新target与provider作业；只有足够有效暴露时间才估频率。37秒、1–2分钟样本不能外推平均间隔。
- **资源**：记录每小时内存/CPU分位、活动targets、历史行/GC、截图/WAL/盘余量；不能用单次CPU高推断Device Check失败。

受控比较顺序：先保持当前Identity/出口/Chrome版本，只比较修复前后路由与预算闭合；再在已自然恢复的会话比较正常与恢复观察期节奏。没有重复且可比的场景，不宣称因果。若仍快速复发且内部闭环通过，再做一次经授权的同区域不同既有出口对照；不同时换Profile/出口/Runtime导致不可归因。若`t=bv`明确拒绝则直接走受控出口处置，不等待CPU或打码重试解决。

最终报告必须分为“内部代码修复通过”“solver链路结果”“实际消息源可用性”“尚需人工/外部调整”。当前资料足以支持修复内部确定缺陷，但不足以承诺Reuters恢复观察期后不会再被网站识别。

## 13. 本轮交付边界

本轮仅完成记录重建、现代码审查、只读生产补证、78项离线回归和本执行方案。未修改运行代码/配置，未部署/重启，未切出口，未调用打码provider或新闻站，未操作Cookie/登录/验证码，未进行清理或Git提交。后续实施必须追加changelog，保留其他会话改动，并按上述阶段留下可核实回执。
