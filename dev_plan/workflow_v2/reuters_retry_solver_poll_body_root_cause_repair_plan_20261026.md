# Reuters RETRY、打码复发、采集与正文恢复：根因诊断与可执行修复方案

日期：2026-10-26。性质：**诊断与待执行方案；本轮没有修复、发布、重启、切换出口、登录或购买验证码。**

## 1. 结论与边界

Reuters 不是单一的“账号掉号”或“又遇到验证码”问题，而是四条相互放大的链路：

1. **RETRY 的实际恢复动作没有实现。** 当前所谓 regeneration 是刷新 Reuters 整个原页，不是点击 DataDome 子页面的 RETRY；刷新不等价于用户观察到的正确动作。刷新后仍是 RETRY 就进入 WAITING_SLIDER，后续主要靠免费探测或人工。
2. **通过一次搜索，被当作一次恢复，但经常马上复发。** 不是所有 BUSINESS_VERIFIED 都代表第三方解码有效，更不代表恢复后的全部查询、正文或持续访问可用。当前代码仍会把数小时后的人工/自然恢复关联到此前 APPLIED 的作业；它虽写了 late_recovery，结果字段仍容易被误用。
3. **恢复控制与业务负荷本身有问题。** 存在大量没有确认网站回执的运行时失败、排队延期；Reuters 搜索仍反复创建业务页和完整导航。现有查询需求与单身份节奏有明确容量矛盾，不能靠继续加超时让其消失。
4. **正文、认证和 Challenge 是不同能力，当前配置及验收仍未完全分开。** auto_login 实际关闭，验证文章仍有搜索页历史配置；搜索可用不能证明账号有效，账号显示 VALID 也不能证明当前文章有权益。

本轮不能承诺“改完永不触发 DataDome”。可修复的是：RETRY 不再需要人工兜底才能进入滑块、正常自动验证不死等、供应商结果不冒充业务恢复、局部控制故障不污染站点风控判断、主备与正文能独立恢复、失败和真实采集周期如实可见。若在这些内部缺陷消除后，两条出口仍在低负载真实访问中迅速被拒绝，则是需要更换访问条件/取得正式内容渠道的外部边界，不能无限追加恢复代码。

## 2. 本轮证据与当前基线

### 2.1 采用的历史记录

- 用户指定的 `eval/challenge_solver_repair_20261023/heartbeat-20261023T2345Z.md` 及同目录前序小时记录。
- `docs/reuters-persistent-search-failure-diagnosis-20261020.md`：已在 10-20 留下同类 RETRY 截图，以及同 Chrome/Profile/出口的原生访问对比。
- `docs/device-check-deadlock-repair-20261022.md`、`docs/datadome-2captcha-implementation-20261022.md`、`docs/challenge-solver-recovery-repair-20261023.md`。
- 10-24 轮询/正文优化发布、实际周期发布及整机升级恢复记录；10-26 消息管线修复、生产恢复和发布核对记录。
- 当前 main 的实际实现，以及远端 Registry/Bus SQLite 全时间窗记录、当前运行组件、既有截图和有界日志摘录。

本地没有找到 10-23 23:45 之后的同目录小时验收 Markdown，因此**没有虚构后续每小时观察**；该时点之后的历史由本轮直接查询持久化记录补齐。不能把跨版本、跨重启的这几天称为同版本连续运行验收。

### 2.2 本轮原始证据

目录：`eval/reuters_diagnosis_20261026/`。

| 文件 | 用途 |
| --- | --- |
| `audit.py`、`history.json` | 全窗口事件、作业、恢复、正文回执及当前状态；没有访问新闻网站/provider |
| `details.py`、`details.json` | 当前 Registry 配置、认证投影、调度账、故障分型、代码哈希及资源压力 |
| `forensics.py`、`forensics.json` | 出口已有测量、登录尝试账、已有截图清单、有界日志匹配；不读取凭据/Cookie |
| `demand.py`、`demand.json` | 活跃监测词产生的确定性请求需求；不输出监测词内容 |
| `summarize.py` | 离线重算事件和付费时间链 |
| `screenshots/` | 从生产既有现场导出的两张 PNG；不是本轮重新访问网站生成的图片 |

主历史窗口为 **[2026-10-23 23:45:37Z, 2026-10-26 12:47:00.286845Z)**，约 61 小时 1 分钟。配置/后读当前状态有自己的读取时间，不当作该窗口终点的原子快照。12:50 的补充聚合也单独记录，不与前窗混称同一时点。

注意证据使用方法：初版 JSON 中 `hourly`/`poll_daily` 部分日期标签曾直接截取带时区的原始字符串；本轮结论不以这些标签作 UTC 日分组。应对 `timeline.at` 解析时区后重算；脚本现已改为显式 UTC/SQLite 时间转换。没有覆盖原导出掩盖这一限制。SQLite 当前投影不是历史状态机的完整快照。

### 2.3 当前版本与部署状态

北京时间约 20:47 的核实结果：

| 组件 | 运行源码版本 | 本实例启动 UTC |
| --- | --- | --- |
| Site Access | `46a9d7dd` | 10-26 09:37:46 |
| Chrome Supervisor | `f2f0bcd6` | 10-25 13:31:18 |
| Message Bus、Content Enrichment | `4c05c00f` | 10-26 11:47 左右 |

本地、远端仓库 main 当时均为 `62136352`。镜像不是每个文档提交都重新构建，但运行中的 `datadome_browser.py`、`datadome_solver.py`、`recovery.py`、`governance_repository.py` 四个核心文件与本地 main SHA-256 一致。因此本轮指出的核心缺口不是“运行的仍是 10-23 老代码”这一种解释。

这四个组件当前实例重启计数均为 0；这不抹去此前旧实例的重启。Site Access 当前共享的网络命名空间指向当前 Supervisor，现有出口遥测也为 READY；本轮未发现足以直接归因为旧 namespace 的新证据。

### 2.4 人工解决后的真实状态

- 主身份 `digitimes-de-1` 的本轮事件在 **12:34:00Z / 北京时间 20:34** 由 `manual_verification` 解除。
- 备用 `reuters-backup` 在 **12:42:13Z / 20:42** 由原页业务观察解除；该次供应商任务此前明确 UNSOLVABLE，不能把后续恢复算它的解码成功。
- 12:47 的九 ticker 当前投影都有新的 succeeded、无当前错误；它们的真实轮次与单词覆盖仍需查 unit/请求账，不能只看 binding 总状态。
- 12:47 前已有 **6 条 Reuters FULL 正文回执**；12:50 的补充窗口为 **7 条 FULL**，另有 1 条 `subscription_required`、3 条历史 `transport_timeout`。这些是回执数，未证明都是互不重复的新文章。
- 因而当前不是“Reuters 根本不能取正文”，也不是本轮已证明长期稳定。不能把人工处理后的改善归功于尚未执行的修复。

## 3. 错误—修复—再次失败的机制

### 3.1 从上次验收到现在，并非一直没有自动恢复

全历史窗口内：

- 234 个 CRAWLER `ACCESS_RESULT=SUCCESS`，108 个 BODY `ACCESS_RESULT=SUCCESS`。
- 603 个 probe started、603 个 finished，无窗口内缺失终态：302 STILL_BLOCKED、153 RUNTIME_UNAVAILABLE、112 QUEUE_DEFERRED、18 TIMEOUT、17 BUSINESS_READY、1 CANCELLED。
- 26 条 RECOVERY_STARTED 事件、10 条 regeneration 事件、77 条分类变化。**不把这些数字直接等同于 26 次/77 次独立新验证码**：分类抖动、旧任务更新、同原页复查必须去重。
- 19 笔窗口内新付费任务：15 APPLIED、4 UNSOLVABLE；15 笔已知费用合计 **$0.02175**，4 笔费用未知，不当作 0。另有未购买的 PREPARING/WAITING_SLIDER 作业。
- 15 个 APPLIED 中，7 个在确认窗口取得业务证明，8 个为 late_recovery；有 **12 个**在取得业务证明后约 **9.37–67.72 秒**内再次登记阻断，另有约 261.86 秒复发样本。复发账仍有未覆盖分支，因此这些不是全部实际复发。

这些记录证明后台探测和打码链条确实执行过；真正的问题是**短暂接通后重新访问又失败，以及部分恢复/现场准备路径没有完成**，不是简单再加一个“定时重试”。

### 3.2 代表性时间链（UTC）

| 时间 | 真实过程 | 可推出什么 |
| --- | --- | --- |
| 10-24 00:24:01 → 00:24:21 → 00:24:36 | paid Cookie 应用 → 搜索证明 → 约 15 秒后复发 | 已能提交并应用，持续性失败并未解决 |
| 10-24 00:27:47 → 00:29:05 → 03:46:21 | RETRY 整页刷新 → WAITING_SLIDER → 数小时后免费恢复 | regeneration 没有实现用户实际需要的按钮动作 |
| 10-25 14:45:34 → 14:45:41 → 14:45:52 | 应用 → 搜索通过 → 约 11 秒后新阻断 | 仅完成一轮 canary 远远不够 |
| 10-25 14:46:30 → 14:46:36 → 14:46:45 | 第二笔应用 → 搜索通过 → 约 9 秒后再阻断 | 主备/打码预算不足以自动解决高复发条件 |
| 10-25 19:24 → 10-26 00:09 → 00:10 | 应用后仍受阻约 4 小时 45 分 → 免费恢复 → 约 46 秒后再阻断 | 不能用晚到的 BUSINESS_VERIFIED 证明供应商当时解码有效 |
| 10-26 09:22:08 → 12:34:00 | 主身份应用 → 用户人工 Verify 后解除 | 必须保留人工/自然/solver 的恢复归属，不能统一归为打码恢复 |
| 10-26 11:24 后 → 12:42:13 | 备用任务 UNSOLVABLE → 后续原页恢复 | 第三方失败与后续恢复是两个事实 |

10-20 已存在相同 RETRY 画面；这不是最后一次部署才产生的新问题。此前“只刷新一次、不过度计费”的保护保住了预算，却没有实现 RETRY→滑块这一必要前置能力。

### 3.3 搜索成功不代表全链路恢复

用户指定的 23:45 验收曾有 7 个 BODY ACCESS_RESULT SUCCESS，却没有 FULL；本轮又看到历史窗口 108 个 BODY SUCCESS，但完整正文回执远少于它。这两个账不能直接相除得到正文成功率：访问回执、canary、HTTP/DOM 可访问、缓存/结果复用、正式正文结果和 outbox 入账不是同一分母。

应继续保留现有 FULL/SHORT_FULL 质量门槛，补齐因果关联和新文章 cohort，而不是把 BODY 的 HTTP/Access SUCCESS 改名为“正文成功”。

## 4. 根因分层：确定问题与待验证因素

### R1：RETRY 动作错误，且会终止主动场景准备【已确认】

位置：`site_strategy/datadome_browser.py::challenge_state/NativeChallenge.reload`，`datadome_solver.py::DataDomeSolver.tick`。

现状：RETRY、IP_BANNED、UNAVAILABLE、INTERSTITIAL 共用一次整页 reload；REGENERATED 超过 60 秒后转 WAITING_SLIDER。WAITING_SLIDER 可以继续观察并在自然变成 SOLVABLE 后提交，但**不会点击 RETRY**。已有 APPLIED/FAILED 作业则在 tick 顶部直接退出，不再做场景准备。

因此至少有两条卡点：首次 RETRY 没有正确打开滑块；已购买后滑块退回 RETRY，也没有独立、免费、可幂等执行的准备阶段。不能用“已经有重试逻辑”否认这个缺口。

### R2：题型与“可以购买”的判断仍过粗【已确认代码缺口，实际误判占比未证明】

- `challenge_state()` 在整个子页文本中搜 retry；只去除 script/style/显式 hidden，不计算内部按钮的真实可见性。隐藏的重试文案也可能改变判断。
- `/captcha/?t=fe` 的可读页面，在没有 retry 文本时就可能是 SOLVABLE；额外 assessment 主要证明交互验证码存在，不保证滑块图片、拼图或控件已经加载。
- 外层 OOPIF 的 loaded/visible/readable 检查应保留，但它不是内部 widget readiness。
- 当前最新两个可导出的 confirmed PNG 经本轮目视检查均是**白屏**；主身份另一条最新记录没有可导出的 PNG。三条父 DOM 摘要分别可能显示搜索内容或仅 reuters.com，不能据此否认遮罩内验证码，也不能靠这些图片确认具体题型。
- `EvidenceStore.capture()` 在 confirmed 已标 CAPTURED 后通常停止更新；白屏也会标 CAPTURED，后续真正出现 RETRY/滑块的画面可能完全没有留下。

当前保存能力是“文件存在”，还不是“类型现场可用”。这也是过去题型解释不断变化的重要诊断盲区。

### R3：恢复/复发账与业务恢复条件不够精确【已确认】

- `mark_solver_outcome()` 对同事件的 APPLIED 作业，会把后来人工/免费恢复也更新成 BUSINESS_VERIFIED，只通过 reason 区分 late_recovery。缺少独立的实际恢复方式字段，统计上易把相关性当因果。
- `_record_recovered_window()` 对相同 recovery_id 直接返回；仍有另一个 purpose/人工任务未完成时，同事件内能力恢复—再受阻可能不重新登记观察期与复发。
- `begin_recovery()` 为保留人工现场，有当前 Challenge Task 时会合并事件；现场保留是正确的，但不应因此丢失 capability 的新阻断序列。
- 当前已有 5 分钟/10 秒观察期、快速复发加长观察期，**不再重新开发一套同名机制**；需要修的是适用范围、记账分支及恢复后实际请求分配。
- 原页通常只对应某一个查询/offset，常为 Micron 或零结果；它能证明这一请求正常，不代表接下来 Intel、Nebius 等不同查询、分页和正文全部正常。

### R4：真实请求需求与现行节奏有硬冲突【已确认】

12:59 的词表只读核查：8 个已有监测配置的 ticker 合计 **22 个互不相同英文表达式**、模式 separate、周期均为 60 秒；CEG 的 legacy 查询不包含在这 22 个中。旧 `PollState.units` 还留有历史键，不能直接把其总数当当前有效查询数。

主/备均单身份单并发，普通导航间隔 3 秒；源 Scheduler 还有单并发/1 秒间隔约束。**即使不算 CEG、翻页、正文和执行耗时，22 次导航的稳态循环下界也已经是 66 秒；观察期则至少 220 秒。** 现有 single-flight/15 秒缓存对这些不同查询没有合并作用。

把 60 秒配置解释成每个 ticker 全部 L1 都会在每分钟完成，是无法兑现的。盲目加并发或缩短风控身份间隔，会增加恢复后突发访问；只扩大 RPC/queue timeout 则把不足容量变成长等待。此处需做请求工作量治理和真实周期表达，不是单纯网络修复。

### R5：运行时、出口检查和资源压力是独立失败来源【部分已确认，不能全归为 DataDome】

- 窗口 153 个 RUNTIME_UNAVAILABLE、112 个 QUEUE_DEFERRED 没有确认网站成功回执。不能把它们算新挑战。
- solver unavailable 中有 66 个 `PROXY_EGRESS_NOT_VERIFIED`；另有 ConnectTimeout 14、OperationalError 16、TimeoutError/WriteTimeout 各 1。现有原因没有区分出口证明过期、真实 IP 不同、代理入口不可达等，不能倒推出 66 次 IP 被封。
- 当前两出口既有测量均 READY、无连续失败，分别为 `205.198.126.113/.114`；它们是历史记录所指的同地区相邻 IP。逻辑名 `jp-standard-6` 不代表实际在日本。独立 IP 不等于独立信誉网络，未重新核验 ASN，不编造信誉结论。
- 备用 lifecycle 为 on_demand/idle 120 秒，主为 on_demand/43200 秒；Worker facade 还有活动身份最多 600 秒、非活动 120 秒的收回限制。只调 Registry 的 idle_seconds 不足以实现长期连续性。
- 当前 8 核/16G，根盘约 128GiB 可用，已不是磁盘归零。一个资源样本 available 约 2.8GiB、swap 约 1.9GiB；CPU PSI some avg60 约 17.79%，memory PSI full avg60 约 11.99%。这支持“存在计算/内存压力”，但不能证明某次 RETRY 就由资源不足造成。
- 当前 BODY 实例没有重启，但有界日志仍匹配一次 database is locked；旧心跳 `_renew` 异常透过 finally 和 `task.result()` 使整个 BODY Worker 退出的代码路径仍在。新 storage lane 已减少竞争，不等于这个失败隔离缺口自动消失。

### R6：认证与正文恢复仍有配置债务【已确认】

- Reuters AuthPolicy 为 optional/public_access，auto_login=false；本轮没有启用它，登录尝试账为空。
- 当前 `auth.verification_url` 和 scope `body_url` 仍指向搜索页。`body_verification_url()` 已排除同一 listing，动态选文章代码也已存在；不能再声称搜索页是有效文章 canary，但静态配置和冷 BODY 验收入口仍会造成歧义。
- AUTH VALID 投影的 verified_at 仍来自较早时点；Challenge cleared 只是 last_check_reason，不能当“刚刚再次证明了账号有效”。
- 本轮明确一条 subscription_required：应进入账户/权益/站点解析核查，不把它变成新的 CAPTCHA，也不能靠打码解决真实订阅缺失。

### 未证明的外部原因

IP 信誉、DataDome 会话评分、不同查询风险、解码 cookie 与后续环境的连续性、正文权益和 CPU/renderer 停顿均可能影响复发。但目前没有同条件对照证明其中某一项是全部复发的唯一原因。此前同一受限身份的普通原生新页也被拦，只能说明“问题不限于某次 page.goto”，不能证明历史启动/控制完全无关。

官方资料用于确定边界而非猜测私有风控规则：Device Check 可以自动放行、拒绝或升级验证码，不是等久一定通过。[DataDome Device Check](https://docs.datadome.co/docs/device-check)。2Captcha 的 SliderTask 要求真实 captchaUrl、浏览器 UA 和代理；文档明确 t=fe 与 t=bv 不同，后者不是应继续付费求解的题目。[2Captcha DataDome API](https://2captcha.com/api-docs/datadome-slider-captcha)。RETRY 按钮的具体 DOM 不能从供应商 API 文档臆造，须以实际子页回归样本适配。

## 5. 修复目标和不做的内容

保留现有 Registry、双轨 Runtime、Identity/主备、独立控制 Worker、Task/Target 所有权、单写锁、capture/正文去重和安全代理。**不新建验证码平台、不重写消息总线、不加入 stealth/随机指纹、不复制登录 Cookie、不自动轮换几十个节点、不用 Reader 的摘录冒充 FULL。**

方案默认只改变 Reuters 的策略与专属动作，共性错误隔离/诊断修复才适用于其他来源。生产依旧是 60 秒配置、24 小时可调度；不偷偷改成 300 秒或休市停用。网站正在被明确拒绝时允许受控 backoff，必须展示为真实延期，不制造成功。

## 6. P0-A：把 RETRY 做成真正的、独立的场景准备步骤

### 6.1 复用现有观察链，增加细化状态

修改 `verification_frames.py`、`datadome_browser.py`，增加一个内部 `DataDomeScene` 返回结构，至少包含：

`state / loaded_frame_id / frame_read_state / visible / retry_control / widget_ready / scene_signature / observed_at`。

状态固定为：`AUTO_RUNNING`、`RETRY_READY`、`SLIDER_LOADING`、`SLIDER_READY`、`IP_BANNED`、`DENIED`、`UNREADABLE`、`CLEAR`。不把全部 provider 状态塞进新的全局治理枚举。

- 先核对原页 identity、instance、target、allocation、owner、当前 lease；保持现有 fencing。
- 从 DOM owner 精确寻找已加载的 DataDome frame/OOPIF，禁止扫描或接管其他验证码页。
- 在该子 frame 中判断 RETRY 控件**实际可见、未 disabled、有有效尺寸**；隐藏模板/脚本文字不能算按钮。
- `SLIDER_READY` 要同时有可见交互结构与加载完成的题面资源。字段/选择器集中在一个 DataDome adapter 中；缺少证据输出 LOADING/UNREADABLE，不从 URL 或“没有 retry”直接推定有解。
- t=bv、明确拒绝立即输出 BANNED/DENIED；不刷新购买、不改写 t 参数。Device Check 使用现有有界等待与人工复核出口，不调用 SliderTask。

### 6.2 实现 `NativeChallenge.prepare_retry()`，不再用 reload 代替

1. 只有 `RETRY_READY` 才能执行。自动动作前再次检查维护会话；用户有 OPEN 会话时只观察，不动其页面。
2. 持久化 `retry_action_id`、scene_signature、instance/target、attempt_no、started_at，**先记意图再操作**。
3. 使用已绑定子 frame/OOPIF 的正常可见按钮交互；不修改页面脚本、不伪造网络请求或 CAPTCHA 答案。按钮适配以真实样本确定，不在主文档盲搜所有“retry”。
4. 同一现场先点击一次；跨 tick 在 5/15/30 秒读取同一现场，出现真实 SLIDER_READY 才进入购买。累计 60 秒没有转化则输出明确失败并保留原页；同现场至多再允许一次有证据的重试，累计两次即停止自动点按钮。
5. 页面/scene 换代、结果不确定、进程重启，都不能把旧动作重新当成尚未执行。先观察；不确定时不盲目重复。
6. 整页刷新只保留给“无可读 widget、原生自动文档确实停滞”的独立一次补救，不用于 RETRY/IP_BANNED，不与点击互相循环。

### 6.3 准备动作与已购买作业解耦

在现有 recovery JSON 增加一个小的 `scene_preparation` 字段即可，不新增常驻服务或通用 DAG。

- 免费场景准备可在同事件有 FAILED/APPLIED 时运行，避免顶部 terminal-return 让 RETRY 永远无人处理。
- 已有 PENDING paid task 时不点击、不 reload、不更换其 scene；先收集结果。场景变化按既有规则 SUPERSEDED。
- 同事件已付费失败/应用后，准备成功**不自动再买一笔**；继续原页/免费业务恢复或人工/备用。保持一次事件付费幂等、全局与身份限额。
- 清理旧无 taskId 的 PREPARING/REGENERATED/WAITING_SLIDER：事件结束时标终结或 superseded，保留诊断，不继续呈现 PENDING，不删除付费墓碑。

## 7. P0-B：修正恢复归属、快速复发和 paid 熔断

修改 `datadome_solver.py`、`governance_repository.py`、`recovery.py`、`maintenance.py`、`failover.py`。

### 7.1 分开两种结果

- `solver_verification`：`NOT_APPLIED / PENDING / VERIFIED_IN_WINDOW / STILL_BLOCKED / INDETERMINATE / SUPERSEDED`。
- `business_recovery`：实际恢复时点、purpose、proof_id、`recovered_by=manual / background_probe / original_observation / solver_confirmation`。

可以复用原 verification_state 兼容输出，但内部不能再把任意晚恢复改写为“本次付费已验证有效”。晚恢复保留作业关联与 `late_recovery_at`，不能覆盖早先确认窗失败；人工 Verify 明确写 manual。即便在确认窗口，最多证明“应用后该现场恢复”，没有对照不宣称 cookie 是唯一原因。

费用、provider ready、Cookie applied、原页解除、真实搜索、正式 FULL、route activated、natural request 和 recurrence 继续分列。route/人工路径补齐已有真实回执，不用当前时间回填历史激活或复发。

### 7.2 让 capability 恢复序列独立于保留原页的事件

在现有 recovery/runtime JSON 为 CRAWLER/BODY 分别登记 `capability_epoch / recovered_at / last_success_at / blocked_again_at / probation_until`。每次“已恢复能力再次受阻”只递增一次序列；人工作业和原页仍可留在旧 Task，不强行创建/抢走新原页。

修掉 `_record_recovered_window()` 只按 recovery_id 去重的分支。复发判定采用上一次该 purpose 的真实恢复证明，不采用 Task seen_count、UNKNOWN→INTERACTIVE 抖动或运行时异常。CRAWLER 恢复后 BODY 仍失败，不把 CRAWLER 重新锁回未恢复；反之也不能让搜索证明自动放行付费正文。

### 7.3 防止“解开—突发访问—立刻再买”的循环

- 保留现有 5 分钟/10 秒观察期，不扩大所有网站降速。恢复后先用正常生产的 3 个不同有效 query 单元提供自然证明，不额外并行发相同 canary；剩余查询公平推进，不立即补跑积压轮次。
- 任一真实复发立即终止本次恢复观察并请求真实可用备用；没有可用备用时保持明确阻断，免费恢复继续。
- 同 Identity 15 分钟内两次“业务恢复后 120 秒内又被真实 WAF 拒绝”，对 paid solver 冷却 30 分钟；已有全局 20/24h、身份 2/10min 上限继续保留。冷却只停止付费，不禁止人工、免费探测或已有 paid result 收账。
- 免费网络探测保持当前人工作业约 10 分钟一次；原页轻观察不算额外网站访问。冷却结束也不能因为旧按钮还在就自动造新付费事件。
- 首轮以明确帧/HTTP/WAF 回执证实复发，队列超时、CDP 失败、权益缺失不参与此 paid 熔断。

## 8. P0-C：补齐可用现场与分阶段诊断

### 8.1 截图从“有 PNG”升级为“可判读”

修改 `challenge_evidence.py`，不引入视觉大模型/OCR 服务。

- 保留不可变 first；另保留 `latest_actionable` 和 `after_action` 两个小槽。发生 AUTO→RETRY→SLIDER、paid 后仍受阻等关键转换才采样，不按每个 ticker/每分钟截图。
- 为截图标 `READABLE / LOW_DETAIL / BLANK_SUSPECTED / FAILED`。可用现有图像库做降采样像素分布检查；小文件大小只作提示，不能直接判白屏。没有合适依赖时先保留 LOW_DETAIL 并允许重采，禁止把它报告为可准确识别的现场。
- CAPTURED 但 BLANK/LOW_DETAIL 不阻止下一次**已读到真实可见 widget 后**的有界重采；最多两次短等待，不 reload/goto、不夺取其他用户的 Viewer。
- 同一个 metadata 绑定 assessment_at、screenshot_at、scene_signature、frame_read_state；图片与 DOM 不是同一次现场则明确标为不一致。
- 每 Task 最多保留 first + 最新可操作 + 一张动作后场景；复用既有 14 天/256MiB 与活跃保护，不无限累计子阶段。

验收必须打开图片目视检查，文件存在、PNG 头正确不算截图可用。当前白屏样本作为必须通过的坏例。

### 8.2 细化控制与代理原因，不再把它们叫 Challenge

- `PROBE_FINISHED` 保存 queue/控制 attach/网站首响应/DOM/解析/收尾各阶段、error_type、受限 reason_code；保留 request_sent 三态。
- `egress_matches()` 输出 `BINDING_MISMATCH / OBSERVATION_STALE / LOCAL_PROXY_UNREACHABLE / IP_MISMATCH / MATCH`，不是一个 False。使用已有半小时出口维护；购买前缺新证明时做一次有界、合并的验证，不在每个 5 秒 tick 重复测 IP。
- 对主链与供应商入口同一 Identity 的出口证明绑定 observed IP、generation、测量时间；不记录认证代理 URL。失败只阻止本次购买，不把主业务永久判坏；发现真实 IP 变更才使旧场景结果失效。
- 对 153 个历史 RUNTIME_UNAVAILABLE 不追填猜测类型。上线后的新回执必须能明确区分 attach failure、资源拒绝、SQLite busy、页面不可读与真实 401/403。

## 9. P1-A：降低 Reuters 的访问与控制成本，不牺牲查询覆盖

### 9.1 先解决工作量模型，而不是偷偷把间隔改大

修改 `news_adapters.py::ReutersSiteSearchAdapter`、`scheduler.py` 中 Reuters query 单元、`service.py`/`budget.py` 的既有准入衔接。

1. 保留 60 秒目标及 24 小时启用；不修改监测词、不删 L1、不默认把 OR 当作 Reuters 已支持。
2. 将 Reuters 每个 query 的首页作为发现单元；第二页及后续历史覆盖转为独立 tail/checkpoint，只在未覆盖窗口仍有必要时继续。复用已有 QUERY/TAIL 模式，不创建新调度系统。保留完整列表 capture、逐页检查点、失败/延期与时效规则。
3. 同一 query+offset+语言+身份/实例/会话+scope/版本在同一观察周期只发一次物理请求；复用已有 single-flight/capture 账。不同查询不共享内容，不把旧 capture 的时间改成现在；缓存不能替代下一周期真实采集。
4. 过期目标周期不逐个补发。每 unit 合并为一次最新到期工作，公平按 oldest-due 前进；历史 tail 另计。不得为了 catch up 在 challenge 解除后立即突发导航。
5. 规划器计算当前实际需求、最小物理循环和 BODY 余量，明确显示超额需求与实际周期。任何延期都不计为成功；binding 的“本轮完整”必须引用本轮全部必需 query 的成功/明确零结果证明。
6. 支持经过实验验证的站点 OR 模式作为**降低请求数的首选可选项**：用冻结样本比较 separate 与 OR 的正文文章集合、词义、分页、排序和重复运行，只有证明宽召回不损失且实际语法有效才由 Registry/CAS 启用。现有通用 QueryPlan 已支持 OR，不另造查询语言。验证失败则保持 separate。

**明确取舍：** 在 OR 未验证、至少 22 个 distinct query、3 秒单身份导航不变的情况下，不承诺所有词 60 秒内一轮。默认优先稳定、完整覆盖及真实时点，展示实际有界循环；这不是暗改 300 秒，也不把处理一次单词标为整个 ticker 新鲜。若必须强制每个词每 60 秒完成，须另行选择经实测可承受的节奏或增加并行身份容量；本方案不擅自把唯一 fallback 改成第二条常驻分片，不用放宽风控环境伪造这一目标。

### 9.2 减少每次请求的页面和驱动重建

修改 `runtime.py`、`fetch_anchors.py`、`external_runtime.py`、`worker_runtime.py` 的现有机制，限定 Reuters：

- 每个已活动身份保留最多 1 个可复用的普通搜索业务页；不同 query 仍正常导航并按真实 recipe/DOM 合约验证，不使用另一 query 的 DOM 回答。
- Reuters 页面**不套用 frozen idle scripts**。现有冻结逻辑目前不覆盖 Reuters，因此不能把它当当前故障根因；新增复用时必须显式排除，避免暂停 DataDome/会话 JS。
- 发现阻断后将该页按既有两阶段 ownership ACK 转交 CHALLENGE/RECOVERY，立即从业务复用池摘除；后续业务不得借到原页或关闭它。
- 人工 OPEN/维护与恢复保留当前 single writer。业务页复用不会导致不同 ticker 同时操作同一页；取消只清理该命令的临时页。
- 冷控制 attach 失败先检查当前 Supervisor/实例/原生 CDP 本地可达性；若原 Chrome 活着且有人工原页，只重建控制 transport/Worker 一次，不停止 Chrome、不清 Profile、不造 TARGET_LOST。
- 将 ensure、connect_over_cdp、页分配、导航和读取纳入同一剩余预算；不给每段一份新的完整超时。内部失败返回类型，下一次正常机会再试，不形成同轮两次完整冷 attach。
- 只在自然流量证明确有持续 CDP attach 瓶颈时，为 Reuters 搜索 recipe 复用现有 raw CDP DOM 读取与 `reuters_listing_rows()` 进行等价实现；不是首轮就重写整个 Runtime，也不把纯 CDP 当永久免风控技术。

### 9.3 活动身份连续、备用按需，但不两分钟反复停启

- 主身份及正在承载路由的备用保留真实 Chrome 连续性；按 active route 决定，而不是只看它是否原始 primary。
- 备用空闲 120 秒改为至少 15 分钟；真实未使用备用可冷回收。同步调整 Worker facade 的 120/600 秒 clamp，不能只改 Registry。
- 活动 Reuters 浏览器不因控制 Worker idle/4 小时驱动轮换而停 Chrome；原页、维护会话、活动命令和恢复现场继续 pin。
- scope 的主备仍只有现有两个 External Chrome；对备用 CRAWLER 与 BODY 分别记录真实可用证明。允许公搜备用先接替，不因缺付费文章证明阻断其公开采集。
- 当前两出口是相邻 IP，后续若低负载仍同步快速复发，可受控为**备用**选择另一条已实测可用、同地区且不同网络的既有节点；一次迁移、保存 Profile 快照、绑定版本和实验记录，不自动轮换，不新建一堆身份。已有网页成功不等于稳定，必须完成同样的多轮查询及正文验收。

## 10. P1-B：正文/认证独立闭环与 BODY 故障隔离

### 10.1 清理验证配置，沿用已存在的真实文章发现

- 将 Reuters scope/auth 中“BODY 验证地址=搜索页”的历史值移除；CRAWLER canary 仍可为搜索页。
- BODY canary 优先采用最近实际发现且归属 Reuters 的文章、已有正式 FULL 的同身份可复用捕获；没有文章时沿用已实现的 discovery，返回 `ARTICLE_SAMPLE_PENDING`，不编造通过，也不因此关闭公开搜索。
- 文章访问使用现有 `builtin:reuters@1`、ArticleBody、质量与标题对应检查。正文为全文才能 FULL/SHORT_FULL；subscription_required、login_required、extractor_error、challenge、transport/control deferred 分开。
- 将实际新文章 cohort 的 first_seen、attempt、capture、body_result、NativeContent/outbox 和最终质量关联；同篇多 ticker 只计一次正文抓取。恢复积压与新消息正文分开排队，后者优先，避免登录完成后立刻回补大量旧正文触发再封。

### 10.2 启用有界掉号恢复，但只对已证明的掉号

当前函数已实现，不重新写密码脚本：

- 开发先通过 credential store 的**可用性状态**确认私有引用，不输出账号密码，不从聊天/测试夹具硬编码；缺少配置就显示待配置，不能阻断公共采集。
- 仅在真实 Reuters 登录墙/会话检查证明 `REAUTH_REQUIRED` 后，启用 existing `auth.auto_login` 和受限正常表单流程。DataDome、RETRY、MFA、真实订阅不足都不得触发重输密码。
- 保留账号共享的 30 分钟一次尝试上限、域名/form action 校验及 MFA/Challenge 停止规则。
- 自动登录触发不只挂在 `RecoveryCoordinator._commit()`：正常 BODY 的确定登录墙也应建立 AUTH 恢复上下文，由同一控制 Worker 执行，不要求先发生 CAPTCHA 事件。
- “表单提交了/Sign in 消失”仅 SUBMITTED；后续账号证据及同身份真实文章证明后才能更新 AUTH VALID/BODY READY。登录失败不改健康搜索为坏，不无限自动尝试。

### 10.3 修补心跳写锁导致全 Worker 退出的窄缺口

修改 `content_enrichment/service.py::_renew/_process_guarded/run`：

- 对可识别 SQLite BUSY/LOCKED，在现有 storage lane 内做最多 2 次短退避；未确认 lease 续成功且接近失效时停止该 job 的提交，按租约机制等待重派，不能放任 stale worker 写入。
- `finally await heartbeat` 不得把已处理的 transient/storage/lease-lost 异常升级为整个服务退出。每 job 的基础设施失败留终态/延期收据，兄弟任务继续。
- `task.result()` 区分已处理的 job 局部失败与真正进程级错误。库损坏、schema 不匹配等仍显式报故障，不用 blanket except 吞掉所有异常。
- 保留 ARTICLE_ONCE、claim token、租约 generation 和现有严格身份检查；不因“减少重启”取消完整性保护。

## 11. 接线、数据与兼容迁移

不新增数据库引擎。scene preparation、capability epoch、恢复方式和 per-job solver outcome 优先加到既有 JSON 模型，默认值兼容旧记录；仅确有高频查询需要时再加窄索引。

迁移命令默认 dry-run，通过 Owner/CAS 执行：

1. 备份并导出 Reuters 当前 identity/scope/auth 策略版本和活跃 Task/Target 清单。
2. 标记无付费旧准备作业终结、保留 paid 墓碑和所有原页；旧 APPLIED 没有证据就保留 INDETERMINATE，不回填假的 solver 成功或复发次数。
3. 移除伪 BODY 搜索验证地址；调整仅 Reuters 的 lifecycle/准备策略；auto_login 仅在私有引用可用时单独启用。
4. 计算当前 query 集合，退役旧词表 unit 的调度资格但保留其历史；不能删除新词或重新从零抓全部历史。
5. 新内部字段默认不出现在旧严格 API 响应；若桌面要展示，遵循现有协商 header/协议能力，先更新消费者再输出字段。必须复测 execute 与 resolve 嵌套模型两条路径，避免再次出现 extra=forbid。

## 12. 开发顺序与文件粒度

| 顺序 | 项目 | 主要文件 | 完成条件 |
| --- | --- | --- | --- |
| 1 | 冻结坏例、补真实状态回执 | 本目录 eval、tests | RETRY、白屏、隐藏 retry、晚恢复、快速复发、掉号、写锁可确定性重放 |
| 2 | 细化 scene + 精确 RETRY 动作 | `verification_frames.py`、`datadome_browser.py`、`datadome_solver.py`、`governance_models.py` | 点击正确子页按钮后真实就绪；重启不重复操作/购买 |
| 3 | 归属、能力 epoch、paid 熔断 | `datadome_solver.py`、`governance_repository.py`、`recovery.py`、`maintenance.py`、`failover.py` | 人工/自然恢复不冒充 solver；保留原页仍能记新复发 |
| 4 | 现场质量和阶段诊断 | `challenge_evidence.py`、`recovery.py`、`external_runtime.py`、`worker_runtime.py` | 可判读图片；控制/网站/代理错误可区分 |
| 5 | Reuters 请求预算、首页/tail、业务页复用 | `news_adapters.py`、`scheduler.py`、`runtime.py`、`fetch_anchors.py`、`budget.py` | 不突发追赶，不漏 L1/分页，真实周期可见 |
| 6 | 正文、认证、心跳隔离 | `verification.py`、`service.py`、`recovery.py`、`content_enrichment/service.py` | 搜索、登录和全文分别恢复，单 job 写锁不杀整个服务 |
| 7 | 配置迁移、桌面结果与发布 | 既有 CLI/API、`deploy/site-login-ui.py`、生产发布工具 | 每阶段回执完整；不重建无关 Chrome/Profile |

每一项重要改动追加 changelog，不混入其他会话未提交内容。不要先只上线 RETRY 点击就交付“Reuters 稳定”，至少 2–6 项需共同完成后做真实验收。

## 13. 必须补的测试

### 13.1 离线/本地复合回归

- OOPIF RETRY：唯一可见按钮可操作；隐藏 retry 不匹配；frame 迁移/实例换代/人工 OPEN 后不操作。
- 点击→加载→SLIDER_READY→一次购买；点击后仍 RETRY；无 DOM；t=bv；两次失败退出；进程在点击前后各阶段中断不循环。
- 已有 APPLIED/FAILED 时免费准备不被跳过，但绝不自动二次购买；PENDING 期间不改场景；费用未知为 null。
- 空 `/captcha/?t=fe`、题面图片未完成、不可读子页不得购买；真实 slider 可购买。
- first/confirmed 白屏后能保留新的可判读 RETRY/slider；原 first 不覆盖、截图次数和容量有界。
- 人工、免费、solver 确认窗、late recovery 分别记账；未解决 BODY/人工作业时 CRAWLER 复发仍增加 epoch，但保留原页。
- 两次快速复发只冷却 paid；人工、免费 probe 和实际 fallback 仍可工作；冷却到期不从旧场景重买。
- 22 query、主/备单并发、BODY 等待、翻页、取消/超时组合：不丢 query、不无限排队、不把单词成功标为完整 ticker 轮次。
- 复用页转交 Challenge 后绝不进入 anchor 池；新 query 不能读旧 query 结果；Reuters 不 freeze；仅控制 Worker 重接不关 Chrome。
- 登录墙触发一次有界登录；验证码/MFA/订阅缺失不登录；登录失败不阻断公开搜索；真实文章才证明 BODY。
- SQLite 写锁发生在心跳、正文提交、结果 outbox 各阶段：只延期相应 job、不写 stale 结果、兄弟任务继续；不可恢复存储错误仍告警。
- 老严格客户端 execute/resolve 与新桌面协商兼容；所有权/租约/幂等/ARTICLE_ONCE 回归不退化。

本轮只运行现有 `test_datadome_solver.py` + `test_solver_recovery_repair.py`，**70 passed / 65.86 秒**。这证明当前已有保护通过，不证明本方案新需求已实现；现有 mock 的 RETRY 测试只断言 reload 一次，并未覆盖真实按钮路径。

### 13.2 一次受控真实矩阵，不重复制造验证

只使用自然发生或当前已有现场：

1. 现场尚在 RETRY 时，记录动作前可判读图与 frame 状态；执行一次 prepare，证明进入真实 slider，再允许一笔 paid task。
2. 记录 provider ready/费用、Cookie 应用、原页通过、正常搜索、下一次自然 query、正式 FULL 和后续复发。若没有可解现场，分支明确未覆盖，不主动刷出验证码。
3. 对主、备分别完成当前真实查询（含一个明确零结果）、不同 query 与真实文章；没有备用 BODY 权益就只登记其公开搜索能力，不伪造全能力备用。
4. 对 OR 做小样本与 separate 对照，只有召回语义验证后启用；不以一次返回 200 或某次零结果宣布支持。
5. 一个变量一轮：先修动作/控制/负荷，再按需对备用出口做受控对照。不能同时改出口、Profile、UA、账号、并发后声称找到唯一根因。

## 14. 发布、回滚与长期验收

### 14.1 发布范围

- 主要是 Site Access、Message Bus、Content Enrichment，以及英文维护 GUI 的必要更新；共用镜像按标准 release 工具选择正式 role，使用精确 main 提交构建。
- **默认不重建 Chrome Supervisor、Clash、captcha-proxy，不重建 Profile/身份、不清 Cookie/原页。** 本方案不需要新开放端口或更换打码服务。
- 发布前保留精确 Task/Target/instance 清单、Registry 在线备份、运行镜像和配置版本。发布后先核对精确原页与 namespace，再看业务回执。
- 回滚只回相应镜像与 Reuters 配置版本，新增字段兼容保留；不能恢复旧整库覆盖上线后的新闻/付费账，也不能回滚 paid task 墓碑重新购买。
- 不仅改远端文件。修复进 main、origin main，再用标准发布台账落地，避免再次出现临时补丁被整机 main 发布覆盖。

### 14.2 24–48 小时验收口径

至少覆盖一个正常消息活跃时段，逐 ticker/query 列：计划机会、真实网站请求、capture、接纳、发布和正文；保留失败、延期、逾期轮次，不只统计成功样本。

- 内部质量：无新 Viewer/Target 所有权错误、无重复付费/自动重放点击、无无限 WAITING_SLIDER/AUTO；结果不确定在 3–5 分钟内进入明确人工/后台恢复出口；控制异常不能变成新 Challenge。
- 原页：真实 Challenge 有可操作原页或明确 TARGET_LOST；有可判读现场或明确截图不可用，不能再白屏却汇报题型已确认。
- 恢复：已有原页/免费探测证明业务恢复后，一个正常调度周期内解除相应 purpose 阻断；人工 Verify 后不继续残留旧治理闸门。
- 稳定性：先完成九 ticker 当前全部有效 query 连续三轮；再报告 24–48 小时自然业务成功率、真实复发间隔、每次恢复后成功访问数、人工次数、不可用时长和 paid 费用。一次 canary 或后台 probe 不算持续恢复。
- 周期：60 秒配置继续保留；分别报告每 query、每 ticker 完整循环及正文等待的 p50/p95/max。超出实际容量的周期明确记为 unmet，不把 soft target 当达成。OR 未验证前不得宣称 22+ 查询每分钟全部完成。
- 正文：对成熟的新文章 cohort 统计真实 FULL/SHORT_FULL 比例，目标 ≥80%；另列登录/权益、Challenge、控制/资源延期和抽取失败，未完成任务仍在分母。公共内容和真正付费权限不足分别展示，不能通过过滤失败样本提高比例。
- 资源：记录 CPU/memory PSI、进程/物理页、截图/targets/WAL 增长和服务重启；不拿单次资源快照当长期曲线，也不把代码造成的写锁崩溃归给随机网络。

若低负荷、连续浏览器身份和正确 RETRY/打码闭环全部成立后，仍复现 10–60 秒级同步再封，则本轮业务稳定性验收不通过，记录准确的出口/实例/负荷/场景关系后进入有限的访问条件对照；不能以继续自动买码、换一轮节点后成功一次作为交付。技术内部修复可单独确认，外部持续可用性必须实事求是说明。

## 15. 本轮实际完成与未做

已完成：历史全窗与当前状态排查、核心代码哈希核对、RETRY/恢复/采集/正文/资源机制审查、已有截图目视核查、70 项基线回归，以及本执行方案。

未做：按钮操作、重新访问 Reuters、请求 provider、购买、登录、切出口、业务配置或源码修复、部署/重启、删除、Git 提交。没有修改其他会话已有工作；后续实施需按本方案另行执行。
