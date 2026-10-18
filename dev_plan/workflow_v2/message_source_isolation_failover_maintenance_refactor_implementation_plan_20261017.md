# 消息源隔离、有效主备与人工维护重构实施方案

日期：2026-10-17（Asia/Shanghai）。方案基线 MAIN：`372fedcd840013111ebde44b464c19e5dd110072`。本轮只复审代码、只读取证和制定方案，**未实施应用修改、迁移、清理、发布或生产重启**。

本方案承接 [架构根因审计](message_source_recurrence_architecture_audit_20261017.md) 的P0/P1/P2；用户本轮补充优先。保留页面所有权、跨连接收尾、独立控制Worker、错误分类、真实能力验证，替换原先为节省进程而共享无关身份及名义多路fallback的设计。

## 1. 实施目标与已冻结取舍

1. 每个独立消息源入口独立主Identity/Profile/Chrome及控制Worker，不按ticker创建；原则上每源只有一个同类备用。
2. Challenge是持久待办Task，不是一个无限占用业务lease的登录会话。待办不占业务页面额度，不阻塞其他源维护或本源合格备用接替。
3. 保留Managed/External双轨；同一源的主备必须同轨、同批准浏览器版本和必要环境。不得把Managed、直连、未认证Profile伪装成原代理External的等价备用。
4. 继续Tkinter桌面工具、受限root桥接、原xRDP/VNC路径；优化成可快速响应、可恢复、可明确结束的维护工具，不另建Web管理后台。
5. 有界的页面、控制进程、缓存、诊断、快照及发布资源生命周期纳入实现；不靠周期重建全部Chrome、清Cookie、堆超时或增加Coding Agent维持运行。
6. 保留60秒目标轮询、实时30分钟准入、原闭市sweep、ArticleHub/总线去重、最终publisher正文规则、Taiwan免费限制、现有正则/Jev合同。未完成的真实登录不能被计划或测试替代。

### 必须明确的能力边界

- 原Challenge页可以跨External控制Worker/Node重启保留；**不能保证跨Chrome进程崩溃、宿主机重启仍保有同一个活页面**。HTML、截图、Profile快照不能恢复网站挑战的内存现场。丢页必须进入明确TARGET_LOST流程，而不是伪称原页恢复。
- 备用停机时Cookie会保留，但账号可能到期、网站可能重新challenge。首次冷启动/登录/业务验收及激活时canary都必需；不能保证从此永不需人工。
- 进程、Profile和维护生命周期隔离，不等于网络供应商、宿主机、磁盘、同一容器cgroup故障完全独立。本轮不引入每源一套Docker编排平台；容器级OOM仍是共享基础设施风险，应以正常容量和有界工作集控制，不能宣称强内核级隔离已完成。

## 2. 本轮新增审查结论与生产约束

### 2.1 当前实现的确切缺口

| 范围 | 当前位置/行为 | 本轮确定做法 |
| --- | --- | --- |
| 身份路由 | `SiteResolver.resolve`按域名返回Site组合；`AccessRequest`无来源scope；`SiteManagedBrowser`只传URL | 增加受治理source scope，贯穿采集/正文/redirect；不只是修改组合名单 |
| 共享身份 | public-markets承载Investing+iHub，digitimes-de又被Reuters使用，Dow Jones三站共享 | 原Profile仅由一个新scope继承，其他源新建独立Profile；不复制Cookie |
| Challenge | `runtime._retain_challenge`保存业务lease；Profile challenge字段混人工状态；`_RawBrowserCDP.close`关闭owned targets | 独立Task+页面所有权转移；关闭连接与关闭页面拆分 |
| 控制恢复 | 所有External共一个Playwright/Node；Managed驱动死会结束整个Site Access owner | 每个活跃Identity自己的控制Worker；API/Registry进程不再持有浏览器驱动 |
| 冷启动 | Supervisor默认max_instances=6，CLI没有对应参数；仅在内存中存实例，不支持跨进程接管现有Chrome | 显式容量参数；最小实例manifest/接管；不把重启Supervisor当无损操作 |
| 桌面列表 | `site-login-admin._inventory`先4个GET，再每Site一个GET；每次都是Docker exec+Python；全程持有file flock | 一个只读inventory快照GET；短锁仅保护本机Viewer会话，不包网络任务 |
| Working | UI全局busy禁用刷新、取消及验证；线程意外异常没有finally；后台recover也占busy | 操作ID+阶段轮询；后台状态不锁按钮；线程异常必达完成回调 |
| Verify收尾 | 普通登录验证失败后仍调用_close_token；公开2xx直接public_access_ready；Challenge严格原URL验证可能不适应登录跳转 | 分离challenge检查、登录/正文能力检查和解除门控；失败保留页，成功再幂等结束 |
| 认证代际 | 每次verify推进共享session_revision | 只读验证不改变凭据代际；显式登录提交/退出/restore才变更 |
| 快照 | 每个Profile快照打包完整Registry及网页cache；hash整包read_bytes；14天内快照数无上限 | 流式hash、缓存白名单排除、Registry快照按维护批次复用、每身份最多2个常规成功快照 |
| 诊断留存 | `prune_diagnostics`每小时每表最多1000行，且与预热/probe串在一个loop | 独立有界清理，削减重复事件、补清理索引和实际积压量 |
| 构建增长 | Dockerfile.v2发布ARG/ENV位于大依赖安装前 | 后移发布身份，验证不同commit共用依赖层；精确收尾旧发布资源 |

本轮新增离线探针：`eval/message_source_refactor_plan_20261017/code_review_probes.py`。不用真实Tk窗口/网站，已复现：后台operation抛意外异常后busy一直为true且没有完成回调；5个Site一次inventory产生9次串行容器请求。它们是确定性机制，不是全部现场延时的耗时分摊。

### 2.2 最新只读采样

UTC 10/17 15:10（北京时间23:10）附近：

- 主机约15.24GiB物理内存，MemAvailable约5.09GiB，2GiB swap几乎用尽；不能把可用内存当无限并发预算。
- Site Access约1.27/6GiB；Chrome Supervisor约2.68/5GiB，CPU采样约一核；Bus仍运行正式main-6a3e19ba，Site仍main-c9e321d9。
- 根盘可用约4.46GiB。此时不适合直接全套重新构建；本轮没有据历史清理授权执行删除。
- Registry约104.9万access_events、1.6万body_outcomes；108个Identity revision、105个Site revision。没有全表JSON扫描或逐文件容量重测。
- 当前工作树有Jev、初始化、SDK等并行未提交工作；只应在实施时冻结所需文件，不整树混合发布。

取证：`eval/message_source_refactor_plan_20261017/{site,bus,host}.json`与`read_only_snapshot.py`。这些只说明本次截面，不覆盖整日可用率。

## 3. 隔离单位：Source Access Scope，而不是ticker或整个域名

### 3.1 三层职责保持清楚

```text
SourceDefinition / ticker binding：采什么、何时采、采集分发模式
SiteStrategy：域名归属、采集/正文适配、授权及安全规则
SourceAccessScope：这个独立入口的主/备Identity、选路与访问健康
    → Identity自己的控制Worker → 对应Managed或External Chrome
```

独立消息源按SourceDefinition解释：TrendForce News与Press、Globe Search与Semiconductors RSS分别隔离。不同ticker仍共享同一source scope；不会产生MU/INTC/BE三套浏览器。

已注册但只有正文需求的publisher使用`publisher:<site_id>` scope，按需启动。未知publisher按归一化最终host生成lazy scope；不预建所有浏览器。

### 3.2 正文路由，防止重构破坏最终publisher归属

- 请求带`origin_scope_id`，例如`source:barrons_ticker_news`。该值只能是Registry登记scope，不允许调用方指定任意Profile、CDP端口或代理。
- 请求URL的实际Site与该scope的owner_site一致，采集与正文使用同一主备对。
- Barron's抓到WSJ/MarketWatch等第三方文章时，转到`publisher:wsj/marketwatch`自己的主备；不能使用Barron's Cookie/Identity。
- RSS/API本身不需要Chrome时继续原HTTP路径。正文才使用该source对应的browser scope；Globe RSS显式browser feed fallback也必须使用RSS自己的scope，不能借Search身份。
- ArticleHub/共享文章继续只补一次正文。首个有效任务把origin scope/route revision写入已有job/observation metadata；后续发现同文不另建body job。首次路线后来不能用时仍由同scope备用接替；若需改由独立publisher scope，仅按最终站点治理显式重新解析，不在多个源身份间盲试。
- redirect每跳重做Site归属。只共享完全相同的源/查询/分页/时间窗口原始召回；不能为了省请求跨独立scope复用同一真实浏览器页面或登录状态。

### 3.3 具体接线

1. `site_strategy/schema.py::AccessRequest`新增可空`origin_scope_id`、`scope_revision`；AccessResult新增可空effective scope及route generation，旧字段保留。
2. `message_bus_v2/scheduler.py`在`_poll/_poll_distribution`建立任务局部`AccessRoutingContext`；`source_id`已有，无需重构SourceDefinition或每个ticker绑定。
3. `SiteStrategyCrawlerAdapter`及普通builtin adapter、Crawler Plane的browser/http路径均通过同一context传递；不能只覆盖`site:auto`。
4. `SiteAccessClient.execute`在request没有显式来源时读取ContextVar，按任务copy，不改变共享client对象属性。`SiteManagedBrowser`正常/Reuters/Yahoo入口统一覆盖；不得依赖解析operation_id猜来源。
5. `ContentEnrichmentHub._process`从job已有source+冻结metadata恢复context，`managed.py`的HTTP/browser及pipeline redirect继承。验证临时canary显式绑定scope，不污染业务任务。
6. `SiteResolver`继续只解析域名策略；新增`ScopeResolver`把策略与scope对组合，生成effective access view。健康runtime key使用`scope:<scope_id>`，`site_id`仍为最终站点，统计不混淆。
7. legacy无scope请求走该publisher显式默认scope；只用于旧接口过渡和正文未知来源，不在新采集链中长期依赖此默认。

## 4. 数据模型：沿原Registry加小表，不再建第二套总线

### 4.1 新表及必须字段

| 表 | 必须字段与约束 |
| --- | --- |
| `source_access_scopes` | scope_id PK、source_id可空、owner_site_id、revision、enabled、primary_identity_id、fallback_identity_id可空、verification_plan JSON；一源一个scope；primary/fallback不能同Identity |
| `challenge_tasks` | task_id PK、scope_id、identity_id、site_id、purpose、operation_id、original_url脱敏展示、original_target_id不可变、browser_instance_id、allocation_id、state、reason、opened/resolved_at、last_check、predecessor_task_id、superseded_by可空；每Identity最多一个当前未终结阻断任务 |
| `owned_browser_targets` | allocation_id PK、identity_id、browser_instance_id、worker_epoch、target_id可空、owner_kind、owner_ref、state、created/updated_at；唯一marker由allocation_id生成 |
| `maintenance_sessions` | session_id PK、kind=LOGIN/CHALLENGE、scope/identity/site/task_id、target/allocation引用、state、created/updated_at；单个人工Viewer占用，不能用全体challenge待办代替它 |
| `maintenance_operations` | operation_id PK、session/task引用、action、idempotency_key、state、stage、stage_started_at、deadline_at、结果摘要/error、updated_at；同session同时最多一个变更operation |

不新增scope_runtime表：复用`SiteRuntimeState`和CombinationHealth持久化，runtime_key按scope分开，active_combination_id只指这一对中的成员。新增scope revision沿原revision/CAS审计模式；已有Profile/Identity spec不需要全部重写。

Identity新增`owner_scope_id`（迁移期可空）。新建/启用配置必须保证：一个Identity/Profile只归一个scope；同一source主备不能同Profile/directory；Profile+出口不可暗换。正常业务不每次重跑全Registry校验，仅配置接纳与实际绑定时检查。

认证仍用`SiteIdentityAuth`，以实际`site_id + identity_id`取记录，不再把scope健康runtime_key误作认证站点键。修正其“观测版本”和“凭据epoch”语义，不复制主身份认证给备用；只读verify不推进session_revision。Profile的challenge字段保留一版读兼容，迁移后以Task表为唯一权威，禁止双写两套待办状态。

### 4.2 Target所有权状态

`owner_kind=BUSINESS / CHALLENGE / LOGIN`；`state=ALLOCATING / LIVE / CLOSE_PENDING / CLOSED / LOST`。

- 先由owner记录allocation intent，再发`Target.createTarget`，marker为唯一allocation_id；响应丢失通过该marker精确找回。
- 绑定target时记录browser instance，不能仅凭target_id跨Chrome实例认领。
- 发现Challenge后，先原子更新target owner和Task、隔离该Identity，再卸载该页导航instrumentation、交接确认、释放业务permit。
- 返回Task前不得存在“已从业务集合移除但待办没有落盘”的窗口。若落盘失败，保留已创建target和allocation状态，返回storage故障并重试本地交接，不重新访问网站。
- 关闭失败留CLOSE_PENDING；重连后只对本Identity、本browser instance、明确allocation的业务target重试，不能因连接释放就删除归属。
- CHALLENGE/LOGIN不属于业务cleanup集合；`detach_transport`不关闭它们。页面关闭只有业务lease结束或明确session/task动作执行。

既有`_owned_targets`改成对上述台账的连接内索引，不再是唯一所有权来源。事件/阶段只在真实转换时持久化，heartbeat不每秒写SQLite。

## 5. 独立控制Worker与双轨Runtime的具体改造

### 5.1 进程结构

- Site Access API进程：Registry、Site/scope解析、访问预算、候选/主备决策、Task/session/operation协调；不持有Playwright或Chrome Page。
- 每个活跃Identity一个Python控制Worker及一个Playwright Node。采用`asyncio.create_subprocess_exec`启动固定模块，Unix socketpair/受限本地IPC传结构化命令；不是Codex Worker、不调用模型、不另起采集管线。
- External Worker连接原Supervisor拥有的正式Chrome；Managed Worker启动自己scope的唯一persistent context。两轨的策略和recipe运行接口相同。
- Worker无业务库写连接；所有持久转换通过父进程确认。启动时只接收该Identity批准spec/egress/策略，不传管理员HTTP token，不接受任意shell、脚本或CDP地址。

为完整隔离，将Managed驱动也放进Identity Worker，避免其死亡结束整个Site Access owner。Managed的Chrome由Playwright管理，不能许诺其活页跨驱动死亡不丢；反复需长期人工challenge的源整对迁External，不混轨fallback。

### 5.2 代码切分，避免远程Page代理的大重写

1. 新增`browser_operations.py`：从`runtime._browser`提取完整导航、gate、站点recipe、DOM读取和统一结果归一化。保留现有parser/ref，不把每个locator调用变成IPC。
2. 新增`identity_worker.py`：Worker入口，执行完整BrowserOperation；直接复用Managed/External adapter和上述runner。
3. 新增`worker_manager.py`：按Identity单飞启动/恢复、IPC结果、deadline、PID/epoch；BrowserRuntimeManager变为选择Worker/管理业务lease的facade。
4. `_RawBrowserCDP/_DocumentNavigationGate`提取到`browser_protocol.py`或原模块继续导出兼容名，避免external_runtime↔runtime循环导入。先纯移动保回归，再进程化。
5. `SiteAccessRuntime._http`保留在owner；browser路径只派发请求。IdentityRuntime状态由owner在收到provenance后更新，Worker不再直接repository.save_*。

### 5.3 最小IPC合同

固定命令：`allocate、execute、inspect_target、adopt_target、verify、close_owned_target、detach、status`。必带operation_id、identity_id、epoch、browser_instance_id（已存在时）、remaining_budget、allocation/session引用。

- Worker只接受自己的Identity，旧epoch结果不更新新运行态；旧lease只释放自己的预算一次。
- Supervisor的逻辑controller_id保持该Identity稳定值，controller_epoch另行递增；不能把每次Worker随机PID当新owner_id，触发旧实现的controller_owner_mismatch。旧Worker确认退出/失联隔离后才通过CAS认领新epoch，严禁同时两个活Worker控制同一Profile。
- operation_id用于同一操作有界幂等；route generation纳入owner缓存key。主备切换/验证恢复后不能重读旧失败回执当新验证。
- result含RuntimeResponse、structured recipe result、provenance、target引用及明确错误，不传Page对象；成功recipe不再回传不需要的整页HTML。
- IPC长度沿当前response budget（默认8MB、上限20MB）加小量信封余量；单次超限明确失败，不无界reader/readline，不把大正文写进操作表。
- status/进度是独立轻量通道，不排在慢页面命令后。父进程维护本地最后阶段，Worker无响应时仍能返回“控制器无响应”，不能让UI状态查询跟着挂住。

### 5.4 取消和恢复

普通请求沿现有总预算，queue/cold start/页面操作共用，不能每阶段重置。预算结束后owner不等待Playwright无限取消确认：先向该Worker发取消、3秒有限收尾；仍无响应则替换该控制Worker/Node，保留External Chrome和人工target。主管等待使用可返回的IPC/进程状态和`asyncio.wait(..., timeout=...)`，不得重新用`wait_for`等待同一不可取消SDK任务；旧任务的收尾绑定旧epoch，不阻塞新操作。只处理登记的控制PID，不kill进程组误伤Chrome。

计划driver回收仍为单Identity idle-only，人工页存在不禁止其他Identity回收；External detach只断控制连接。Managed存在人工会话时不做计划驱动回收。

native Chrome连续两次目标命令不可控/attach失败时：先尝试一次独立控制Worker恢复；仍失败则隔离该Identity、走合格fallback。无活跃人工操作且无需保留的Challenge页时才SIGTERM/graceful drain同Profile单实例；超时保留PROFILE_BUSY/UNHEALTHY，不强杀/删锁。有人页需要保留则等待明确人工动作，不用恢复逻辑偷偷销毁。

健康READY必须来自该Identity近期真实控制能力；Browser.getVersion/HTTP端口仅liveness。API readyz不因某个Worker/网站失败把整个Site Access置死，返回逐scope degraded；Registry/IPC主管本身无法服务才503。

## 6. Challenge Task与常规登录维护完全分离

### 6.1 发现和接管

1. 真实interactive challenge立即识别；对站点自己的JS自动跳转最多保留现有8秒观察，不自动点击/提交验证码，不反复reload。
2. 传输错误沿原有限重试；429走Retry-After/cooldown，不一律创建人工Task。不能“重试三次验证码”后才承认人工阻断。
3. 形成Task并完成所有权交接后返回`challenge_task_id`。该Identity标记`CHALLENGE_BLOCKED`（由阻断引用表达，不把Profile auth改为失效）；scope仍可切备用。
4. 重复轮询返回同Task/阻断，不创建新页面、新Task或每次完整诊断。新到的BODY/CRAWLER可按能力走备用。
5. 原页不导航到新URL；需要验证能力时先就地检查，后在同Identity临时验证页运行真正recipe，原页一直保留到确认成功/用户明确放弃。

### 6.2 Task状态与人工操作

Task使用`WAITING_HUMAN → VERIFYING → RESOLVED`，未通过Challenge检查回WAITING_HUMAN；确认原页丢失进入`TARGET_LOST`。是否正在Viewer显示由session表达，不另造多套Task状态。重新打开丢页时，旧Task填superseded_by，新Task成为唯一当前阻断；旧记录只保留历史，不能让多个历史LOST任务一起阻塞当前身份。

- “打开原页”：精确instance+target，不创建普通登录页冒充现场。
- “返回列表/稍后处理”：关闭自己的Viewer连接，Task和Chrome页保留，释放人工单席位；本Identity仍阻断，备用不受影响。
- “验证Challenge已完成”：执行本节检查和第8节能力检查，失败保留原页、原因与下一步。
- “原页丢失，重新打开”：保留旧Task为TARGET_LOST，创建关联新Task/新页；明确它不是原挑战现场，不能抹掉历史。
- “明确放弃此页”：高级确认后关闭这个Task唯一页，保留审计和阻断；不因关闭页就把站点设READY。

每Identity最多一张自动保留的活Challenge页：第一次后停止该Identity新业务，因此不会每分钟积累标签。多个Identity可以有多个待办；人工仍一次处理一个，但它们不占业务页面额度/全局维护锁。

### 6.3 常规Login Session

常规登录不借用待办集合的全局锁，只排他占用选中Identity。其他源继续；如本源有已认证可用备用，可先切备用再维护主身份。

- 未完成/验证失败仍留窗口。显式Cancel普通登录才关闭自有登录页并结束session；不修改账号成功记录，不解除独立Challenge阻断。
- 登录过程中遇到Challenge：当前页原子转换为Challenge Task，session可以继续作为该Task的Viewer；不同时保留两个“权威会话”。
- GUI退出只是释放本次Viewer控制，任务/会话按原选择保留；没有活跃人工操作的遗留session可显式接续或关闭，不能静默强制完成。

## 7. 每源一个真正有效的Fallback

### 7.1 同类约束与能力证据

主备共用：runtime_kind、批准Chrome/Playwright版本、固定locale/timezone/窗口、安全与网络策略、同一crawler/body recipe合同及所需auth requirement。

独立：identity_id、profile_id/directory、控制Worker；代理型主备使用同国家/地区、实测不同出口IP，尽量不同上游故障域。不同egress_id/节点名但相同IP不算IP隔离。地区/版本不明不随便随机填。

备用默认`ON_DEMAND`，不常驻Chrome/Node；**冷启动不等于未初始化Profile**。付费源需操作员对备用独立登录、两篇当前订阅正文验收，再正常停止并冷启复验，才记录VERIFIED_COLD。不复制主身份Cookie。

能力证据按purpose保存：crawler_ready、body_ready及是否付费；内容只存URL摘要、contract/version、环境/egress指纹、验证时间和结果。某篇付费文章ENTITLEMENT_MISSING不全局抹掉其他公开能力，但该源明确要求订阅的验收不能由公开文章替代。

`fallback_identity_id`最多一个；`UNPROVISIONED/UNVERIFIED/HUMAN_REQUIRED`都不是可用备用。旧多组不删除Profile，退出active引用并标legacy，不让legacy顺序悄悄变成第三条路。

### 7.2 冷接替状态机

```text
主Identity故障/Challenge
  → CAS隔离主、保留原页/Task
  → 单飞启动唯一备用（正常取得writer lock）
  → 检查版本/固定出口/身份绑定
  → 真实同用途canary（有效列表/真空结果，或正文权限）
  → CAS激活备用 → 正式业务继续
```

- 有足够原request预算则同一请求内完成接替并重试只读操作；不足则独立激活operation继续冷启，原请求返回准确延期，后续原调度使用已就绪备用，不延长旧文章deadline或补发过期新闻。
- 不把主Identity的manual门控复制到备用；不要求先解决主挑战才能切备用。
- 备用也被Challenge时各自保留一个Task，scope进入人工阻断；无第三条自动试撞路线。
- 激活后保持sticky。修好主身份只标记其能力恢复，不自动抢回；明确“恢复主用”操作在无在途冲突时CAS切回。
- 备用canary总预算35秒（冷启/attach包含在激活总预算45秒内）；正式请求仍用原45/30/180秒合同。实测超出则明确冷启失败/容量不足，不靠逐级加timeout。
- 证据超过24小时标记待复验，不直接抛弃身份；激活始终canary。默认不每60秒唤醒备用；低频冷启保活检查可每天最多一次，部署初期默认关闭，以免为“检查备用”制造常驻或挑战。

### 7.3 哪些错误允许接替

| 错误 | 动作 |
| --- | --- |
| 明确Challenge/访问阻断 | 创建Task、隔离成员，立即推进唯一合格备用；不无限重试当前页 |
| egress不可用/连接失败 | 既有有限重试后切不同IP备用；不改原Profile绑定 |
| CDP/控制Worker失败 | 先单Identity控制恢复一次，再备用；不写成网站risk strike |
| 会员登录失效 | 主保留登录维护待办；备用必须已有该purpose真实认证证据 |
| 页面模板/正文抽取错误 | 不默认换Identity重复同样工作；保留DOM诊断，准确报CONTENT_ERROR |
| 单篇无订阅权益 | 准确记录该文章限制；只有显式配置备用有不同合法权益且验收过才用，不盲试 |
| 全局存储不可用 | 不通过换Identity绕开同一个DB问题；原窗口内延期 |
| 当前Identity人工维护/排队 | 维护可用备用接替；排队按容量处理，不累积风控/网络错误 |

### 7.4 直连主路的诚实边界

TrendForce等已稳定的Managed+DIRECT不为形式一致而强改成未经验证代理。可配置同Managed+DIRECT独立Profile备用，**只声称浏览器/Profile故障接替，不声称同IP能抵御IP封锁/直连网络故障**。

若要具备出口级容灾，必须取得第二个真实直连出口，或整对经业务验证后受控迁到同区代理。没有资源时标`network_redundancy=NONE`，不能把代理/External藏作另一模式备用。代理型高风控源则必须实测主备IP不同后才验收出口级接替。

## 8. 验证合同：成功登录、Challenge解除和业务可用分别表达

新增`verification.py`，复用现有recipe/parser和`ArticlePipeline/body_v2.2`质量逻辑；验证只读，不publish、不调用Jev、不改30分钟窗口。

维护验证必须通过该session专属Worker命令执行，不能回调普通`/access/execute`再申请本Identity的业务lease，否则会被自己的MAINTENANCE门控拒绝。正文先在验证页形成capture，再调用现有纯正文识别/质量判定（必要时从pipeline提取`validate_capture`公共函数）；不调用会再次联网/入Hub的完整`extract`，避免同Identity重入、递归RPC和重复正文任务。

### 8.1 返回结果必须分维度

```json
{
  "challenge": "CLEARED",
  "authentication": "VALID",
  "crawler": "READY",
  "body": "FULL",
  "health_applied": true,
  "session_closed": true,
  "active_role": "fallback"
}
```

各值允许NOT_TESTED/UNKNOWN，附安全reason、阶段和验证URL；不能用一个UNKNOWN把“已登录但验证文章404”“公开源无需账号”“模型解析失败”“关窗失败”混在一起。

### 8.2 具体规则

1. Challenge原页先就地检查拒绝标记、当前frame/DOM；无新response时不能把合成200当作业务验收。登录跳转到首页不直接判失败，也不自动宣称CLEARED：继续正式canary。
2. CRAWLER：使用该source实际entry/ticker/query和正式recipe；有效新闻卡片或明确真空结果算通过，200空壳/错误instrument/推荐区不算。无需把每ticker都塞进一次GUI点击，日常验证用选中/最近失败ticker，生产首轮验收另覆盖全部ticker/L1。
3. BODY：优先用触发文章；失效/404时从该source最近真实列表选一篇当前文章，或高级同域URL。复用最终publisher身份/正文质量与权益判定；文章页标签存在不等于FULL。
4. 验证临时页不改原Challenge现场；未完成Challenge验证时只关闭验证页。Challenge明确CLEARED的证据可以来自正式recipe成功，也可以来自该站已识别的正常账号/登录/文章/订阅墙页面（实际已离开WAF文档）；不能只靠“没匹配到captcha字符串”。确认CLEARED便可结束Challenge Task、释放其人工阻断，不要求付费正文必须同时FULL。
5. Challenge解除后如果body仍ENTITLEMENT_MISSING或EXTRACTION_ERROR，分别保留授权/内容能力阻断，明确提示“Challenge已完成，正文另有问题”；crawler真实通过才清crawler门控，不能让人为了parser/会员问题反复做验证码。登录/订阅认证仍独立判定，不从正常公开页面推断会员VALID。
6. 先持久化各维度证据、只清已证明解除的门控、结束对应Task/session，再可关闭原页。关页失败保留`cleanup_pending`，不能把已验证结果回滚成登录失败；常规登录未通过则不自动close。
7. 清门控与结果记录在同一owner短事务/CAS中完成；不把正常current health简单归零，不动兄弟scope、当前sticky角色或已完成文章。
8. public source可以crawler_ready而authentication=NOT_REQUIRED，不要求AuthState.VALID。付费源只通过crawler不能清body授权阻断。
9. 显式常规登录完成后才推进凭据epoch一次；只读重新验证、challenge cleared均不是“全Identity换会话”。登录/退出/restore确有影响时保留正确失效语义。

## 9. Site Login Maintenance：快速、准确、可结束

### 9.1 一个桌面程序，两个明确入口

- `Challenges`：source、主/备、真实出口、原页面是否存在、待办时间、主备业务状态；按钮Open Original / Verify Challenge / Later。
- `Login & Access`：独立source的主/备Identity、auth/crawler/body分别显示；Open Login / Verify Access / Close Login / Activate Primary。
- 顶部展示Viewer当前选中Identity、正在执行的operation/stage/耗时，不能只有Working。
- 继续English/ASCII界面避免已发生的中文字体方框；错误建议可用简短明确英文。密码/MFA仍只输入网站，不由UI存储/自动填。

### 9.2 异步操作和只读状态，不新增通用后台队列平台

新增轻量端点：

| API | 合同 |
| --- | --- |
| GET `/v1/maintenance/inventory` | 单次聚合读取的主管内存快照，含scope/主备/Task/当前session；不逐Identity live attach、不probe网站 |
| POST `/v1/maintenance/operations` | 固定action和session/task/scope引用，idempotency_key；提交后202返回operation_id，不等待Chrome/导航/verify |
| GET `/v1/maintenance/operations/{id}` | 只读当前state/stage/result；不能隐式recover、导航或重启Worker |
| POST `/v1/maintenance/operations/{id}:cancel` | 取消本次后台operation，不等于关闭原Challenge页/释放站点阻断 |
| GET `/v1/challenges` | 可分页待办与target状态，默认只当前未完结 |

actions固定为OPEN_LOGIN、OPEN_CHALLENGE、VERIFY_LOGIN、VERIFY_CHALLENGE、RELEASE_VIEWER、CLOSE_LOGIN、REOPEN_LOST_CHALLENGE、ACTIVATE_PRIMARY。高级放弃页需单独明确确认字段，不能以任意URL/path/command实现。

正常阶段：QUEUED、DRAINING_IDENTITY、STARTING_BROWSER、ATTACHING、VIEWER_READY、NAVIGATING、CHECKING_CHALLENGE、CHECKING_CRAWLER、CHECKING_BODY、APPLYING_HEALTH、CLEANUP、DONE/FAILED/CANCELLED。只持久化阶段转换，不每秒写elapsed。

- owner后台管理本表中操作，操作重启后根据allocation/Task状态恢复或明确INTERRUPTED；不是丢到无记录的asyncio.create_task后不管。
- 同一session变更操作串行，同一Identity禁止两个会话；只读inventory/status永不排在后台verify长锁后。
- inventory在配置/状态变化后更新内存快照，最长2秒缓存；SQLite忙时可返回最后快照及snapshot_at/stale原因。后台更新不阻塞GET事件循环，status来自主管内存阶段，不同步等待5秒数据库锁。进程刚启动且无快照则立即返回INITIALIZING，不假造空列表。
- 操作阶段deadline：常规open40秒，verify45秒，normal close10秒；接近预算时显示阶段和elapsed，不用75秒总超时遮盖所有问题。

### 9.3 桥接与GUI改造

`site-login-admin.py`：

1. list只一次inventory请求；command status只一次operation GET。去掉4+N遍历。
2. flock使用短期/非阻塞方式，只在读写本机session/operation引用时持有；不在锁中执行Docker、HTTP或等待RFB。CAS校验旧session再提交本机更新，避免两个窗口串号。
3. open/verify命令提交operation即返回；每个status Docker exec仍可保留，首版不新增宿主HTTP守护。需要优化时只复用同一status订阅，不能同时开多个轮询线程。
4. 根目录0600保存必要token，GUI只看到安全session/operation/task ID。sudo固定命令白名单不变；不把桌面用户加Docker组，不输出API token。
5. 原list/open/verify/close/recover CLI保留一版兼容包装，新的GUI使用明确task/login动作；recover变显式操作，不能每次刷新自动执行恢复。

`site-login-ui.py`：

1. 全部工作线程try/except/finally，所有异常都排回Tk完成回调，destroy后不再投递；Tk只在主线程更新。
2. 使用operation序号淘汰旧回调；readonly刷新单飞，不使用全局busy禁止所有操作。一个session的Verify执行期间仍可刷新、查看状态、取消operation。
3. 按1秒间隔查询活跃operation；异常退到3秒，再失败显示失联并保留引用。无活operation停止轮询；窗口隐藏5秒一次或手动刷新。不能每次refresh触发recover再抢Verify。
4. UI即刻显示动作和阶段；打开浏览器的导航失败不关闭Viewer。VNC失效仅提示Viewer未连，不能调用close销毁原Challenge页。
5. 切换Viewer必须关闭GUI自己创建的旧viewer并新连目标；不按“找到任意vncviewer PID”认领其他窗口。bridge返回session generation，UI核对后再显示已连接。
6. Verify失败原页/登录窗口均保留；Verify成功明确显示已持久化/门控已解除/当前仍走备用。cleanup失败单独按钮Retry Close，不重新要求登录。

### 9.4 可测体验目标

健康正常负载下：按钮反馈<100ms；list/status单次p95≤2秒；提交operation p95≤1秒；已有Chrome Viewer可见p95≤5秒；冷启可见目标≤20秒，40秒内给出明确成败；verify45秒内有结果或明确超时阶段。

这些是实施验收目标，不是本轮已测指标。网站登录/MFA/Challenge人工耗时不纳入20秒；网络坏也必须能看到窗口和准确阶段。

## 10. Supervisor、资源效率与容量

### 10.1 初始资源配置

| 配置 | 当前 | 本次实施默认 |
| --- | --- | --- |
| Chrome Supervisor memory/swap总上限 | 5/6GiB | 8/9GiB；不是预留8GiB，观察真实工作集 |
| Supervisor max_instances | 硬编码6且CLI未接 | CLI/env可配，默认16，支持真实备用并发冷启 |
| Supervisor pids_limit | 1536 | 2304，实测不足再调，不以OOM杀renderer替代容量评估 |
| Site Access memory/swap | 6/7GiB | 先保留；包含独立控制Worker/Managed Chrome，测量后再调 |
| Managed活跃process容量 | 默认4 | source隔离后8，按需启动/idle LRU；不预热所有publisher |
| 全局真实业务页面并发 | 4 | 初期保持4；不同Identity公平，不把人工页算在内；基准证明CPU余量后可配置提升 |
| 热主Identity | 多个always_on/12h idle | 高频高风控主常驻；低频publisher按需；备用不常驻 |

不能仅因MemAvailable5GiB就同时启动所有primary+backup。先清明确孤儿业务target/失败控制进程，正常工作集测量后再增加scope。两张Challenge+两张正式页同时存在属于正常容量需求，不能靠销毁人工页省内存。

### 10.2 不改变浏览器身份的效率优化

- 精准关闭已完成BUSINESS/VERIFY页、失败popup和CLOSE_PENDING；保留当前人工页。不按about:blank批删，不按年龄重启所有Chrome。
- Node与Worker随Identity idle一起退出；未激活备用不预热。已启动但不再active、无人页/任务的备用，正常idle120秒关闭；主身份恢复之前正在承载业务的备用不算idle。
- Managed/低频publisher idle10分钟即可关闭，保留Profile；高风控主不继承这个短idle。无业务需求的Seeking Alpha等正文Identity改ON_DEMAND，不因为曾创建就永久常驻。
- 禁止默认生成trace/video/download/整页日志；成功Reuters等只回传结构化rows；正文只传一次所需内容，不把相同HTML同时塞进事件、operation表和缓存。
- 新/已安排正常重启的主备统一使用256MiB原生HTTP disk-cache容量（`--disk-cache-size=268435456`），保持缓存开启；纳入同一Identity环境指纹，不为立即生效重启当前人工Challenge页。Code/GPU cache仍按停机白名单整理，不声称该参数约束全部Profile存储。
- 保留缓存、Service Worker和正常资源加载，不拦广告/脚本、不随机UA/指纹。External `no_defaults=True`受控验收后采用，保留原页网络安全门控；不把它宣称WAF根因修复。
- 导航安全gate只覆盖所属业务target及受治理popup，人工Challenge接管后卸载；公网/跨站边界不能为了“原生”直接取消。
- Browser/Playwright版本继续锁定；本轮不顺带升级Chrome153到其他major，升级单列快照和验收。

### 10.3 Supervisor自身可恢复

增加`--max-instances`等CLI wiring；为每External实例保存最小运行manifest（identity/profile、spec/egress指纹、PID/start-time、instance/generation、CDP/display/VNC、controller epoch）。manifest不含Cookie/token，受限目录原子更新。

Supervisor进程崩溃但Chrome仍存活时，重启先按PID start-time、实际命令/Profile、CDP元数据核对并接管；Profile flock不能仅因为父进程死就当无人writer，接管与外部writer检测必须先完成。新建前发现现存Chrome而无法认领则PROFILE_BUSY，不能清Singleton后并行启动第二writer。

本轮首次部署如更换Supervisor容器，会导致namespace改变/容器内Chrome退出，**不是无损滚更**。应先持久化Task/manifest，明确操作员当前无正在交互会话，逐Identitygraceful stop再切容器，并将受影响Task标记TARGET_LOST；保留Profile，按新Viewer重开。以后的Site Access/控制Worker部署不再要求重启Supervisor。

如果必须保持此刻原Challenge现场，就先发布P0/GUI，暂缓Supervisor容器替换，待处理完再切；不得一边承诺原页完整保留一边执行全Supervisor重建。

## 11. 存储和发布资源的长期治理

### 11.1 不让新Task/operation复制旧的百万行问题

| 数据 | 默认留存/处理 |
| --- | --- |
| 未解决Task/人工页 | 不按TTL自动删除；每Identity一个活Task限制增长；明确LOST/RESOLVED后才进入终态留存 |
| 已完成maintenance operation | 完整安全阶段摘要7天，Task关键结果30天；重复status不写历史 |
| owned_targets | LIVE/CLOSE_PENDING保留；CLOSED/LOST记录7天，必要归属摘要随Task保留30天 |
| 成功Access事件 | 完整事件7天；按source/Identity/小时汇总90天 |
| 错误/状态转换/切换 | 脱敏关键事件30天；同Task/原因重复阻断只更新计数/last_seen，不每poll再写完整事件 |
| body_outcomes | 保持现有90天诊断；不删除新闻正文/总线消息；按job幂等 |
| 有界DOM诊断 | 每次新型失败≤64KiB文本形状/marker；不存密码、隐藏表单值、完整挑战query；同指纹24小时只保留一次；默认7天/全局512MiB上限 |
| Worker stdout/stderr | 接父进程已有轮转日志，不为每次Worker启动新建永久大日志文件 |

新增`retention.py`，独立于预热/站点probe执行，每10分钟短事务批删（每批≤2000行、每轮≤2秒，积压则后续继续）；使用时间/状态索引。记录oldest eligible、pending rows、deleted rows，证明清理能追上写入，而不只报告job已运行。

新时间统一UTC保存；旧ISO偏移规范化为时间戳/UTC后再比较，不能直接拿不同时区字符串比较。索引迁移在受控升级执行，不在每个GET/worker构造时建索引。

SQLite DELETE只增加可复用页，不承诺df下降。日常复用freelist并正常checkpoint；现有库若无incremental auto-vacuum，不直接调用就宣称物理回收。首次大量旧诊断整理的物理压缩单列短维护，保留当前Registry/Profile一致性，不周期复制整个大库。[SQLite说明](https://www.sqlite.org/pragma.html#pragma_incremental_vacuum)

### 11.2 Profile、缓存和快照

1. Profile真实Cookies/Local State、Local/Session Storage、IndexedDB、Service Worker状态是身份数据，不按普通缓存清理；UNKNOWN或未认证不是可删除依据。
2. 仅对已停机、writer确认释放、无人工页的Identity维护可重建HTTP/Code/GPU cache及Crash/BrowserMetrics白名单；默认该类分配超过512MiB且已idle时处理到正常范围，不每次poll清缓存，不为清缓存停正在使用的高风险主身份。
3. 快照排除这些可再生目录和Singleton；Registry一致备份按同次升级/维护批次只做一份，Profile包引用其hash，不每个Profile重复打包0.7GiB Registry。
4. hash/压缩流式进行，禁止archive.read_bytes将大包整块进内存。后台operation执行，UI返回进度；构建失败只退役本次未完成包，不能留下半包冒充可恢复。
5. 每Profile最多2个常规成功升级快照；当前升级/显式回滚pin例外且可解释。停用的legacyProfile不自动删除，先退出引用、保留目录清单，再由明确清理操作处理，不把旧登录身份当废缓存。
6. 不为每次driver恢复、Verify、fallback切换创建Profile快照；只有浏览器major升级、Profile恢复/破坏性迁移等确需恢复边界的操作才创建。

对长期不关闭的Chrome，另保留极小的BrowserMetrics/Crash诊断白名单收尾：仅清本Identity专用诊断目录中已关闭、无fd/mmap使用的旧PMA/崩溃文件；至少保留最近48小时、每Identity正常上限128MiB，活跃文件不强行截断。不能因为它位于Profile内就永久保留，也不能扩成在线删除Cookie、HTTP cache或网页业务存储。字段/目录版本不识别时只报未管理，不按整个Profile目录递归清理。

### 11.3 发布不再按commit重复生成整套依赖

- Dockerfile.v2中DOXAGENT_BUILD_COMMIT/SOURCE_SHA的ARG声明、ENV/LABEL移到稳定apt/uv/Playwright层之后；不仅移动LABEL。依赖/SDK版本锁和非root导入smoke保留。[Docker缓存语义](https://docs.docker.com/reference/dockerfile/#impact-on-build-caching)
- 连续两个仅源码不同commit构建，apt/uv/Playwright DiffID必须复用；记录镜像source hash，不能牺牲可追溯性换缓存。
- 正式发布器验收后只退役自己产生且无运行/动态/回滚引用的中间镜像/上下文；保留当前和最近一个可用回滚，不执行全局prune，不自动删别的任务产物。
- 不扩展到研究Worker、SDK sessions、业务Read/Bus历史的大规模治理。本轮只处理消息源访问体系自己的增长和发布反馈链，保留既有总线幂等/GC修复。
- 当前约4.46GiB余量不足以安全完整重建；实施前重新列出低价值候选及引用，按该实施轮授权处理，或使用远端已有稳定依赖层/隔离构建机。不把当前小余量下强行build当“激进”。

## 12. 首批逐源迁移表

表中旧ID保留代表保留原Profile，不代表仍允许多个scope引用。新备用默认需独立验收，不预标READY。

| scope | 主Identity处理 | 唯一备用 | Runtime / 迁移边界 |
| --- | --- | --- | --- |
| source:barrons_ticker_news | 原dowjones-main归Barron's独占 | 原dowjones-backup，去除其他Site引用后独占 | External；保留现有Profile/待办；主备订阅逐个验收 |
| publisher:wsj / publisher:marketwatch | 各自新Identity/Profile | 各自一个新备用 | External；不能继续共享dowjones-main或复制Barron's认证 |
| source:reuters_site_search | 原digitimes-de-1归Reuters独占，保留ID作为历史名 | 新reuters-backup | External；从DIGITIMES引用移除，名称不再决定归属 |
| source:digitimes_semiconductors | 原digitimes-nl-1归英文源独占 | 新digitimes-en-backup | External；原de身份不能同时作Reuters主/DIGI备；旧direct不作活动fallback |
| source:investing_ticker_news | 原public-markets-nl归Investing独占 | 新investing-backup | External；原Challenge保留，iHub退出引用 |
| source:investorshub_ticker_news | 新investorshub-main | 新investorshub-backup | External主备同轨；不为省进程借Investing，后续整对Managed迁移需另有同路径证据 |
| source:yahoo_finance_news | 原yahoo-main独占 | 原yahoo-backup仅此源使用 | External；NCP/RSS仍独立HTTP，不伪称browser已恢复 |
| source:trendforce_news | 原trendforce-1归News独占 | 新trendforce-news-backup | Managed+DIRECT；备用仅runtime/Profile容灾，出口边界按7.4明确 |
| source:trendforce_press_releases | 新trendforce-press-main | 新trendforce-press-backup | Managed+DIRECT；不得再与News共Profile |
| source:globenewswire_search | 原globenewswire-1归Search独占 | 新globe-search-backup | Managed；保留已验证主路线，备用同类 |
| source:globenewswire_semiconductors_rss | 新globe-rss-main，HTTP403后备和正文用此scope | 新globe-rss-backup | Managed；HTTP采集本身不额外启动Chrome |
| source:digitimes_tw_rss | 原该入口实际免费正文Profile独占 | 唯一同轨同环境备用 | Managed；仅免费，付费不publish、不触发无意义正文/会员待办 |
| source:huggingnews_rss / tomshardware / thelec / etnews / ctee | 各自独立scope，已有独占Profile保留 | 每源最多一同类备用 | HTTP/RSS保持独立，Chrome仅正文需要时启动；禁用CTEE不因迁移自动订阅/启用 |
| publisher:seeking_alpha及其他正文站点 | 原专属Identity保留或首次需要时创建 | 最多一个同类备用 | 无活跃采集的改按需；不为所有未知域预建两个浏览器 |

代理选择使用实际国家/IP而非us/jp/nl历史槽位名。现有DE1/DE2可作为同区不同IP候选，但同供应商相关性仍标注；SSRDOG/Flower订阅不先假设可刷新或跨供应商独立。落地配置需记录批准节点与真实canary，不把规划ID当已登录资源。

## 13. 数据迁移与版本兼容

### 13.1 一次性迁移脚本

新增`scripts/ops/migrate_source_access_scopes.py`，默认preview，`--apply --expected-revision`才执行；输出每个scope主备归属、旧引用、新建Profile清单、待人工事项及原target是否可读。脚本不登录、不清Cookie、不切活跃出口、不删除Profile。

迁移顺序：

1. SQLite Backup API做一份Registry一致备份，记录当前Identity/instance/Task及配置revision；不为每个scope再复制全库。
2. 导入现有Profile challenge字段为Task；原target存在则准确认领；无法确认/已消失则TARGET_LOST，不自动新建冒充现场。
3. 创建scope和新Identity spec，原共享Profile只给表中一个owner；原站点组合/健康逐成员迁移，真实manual/auth证据保留。
4. 旧pending body任务保留原deadline、publisher、去重ID；首次由新reader领取时补scope，不把老文章重新送实时流。不对大Bus库做整表JSON改写。
5. 原scope切换由CAS完成；legacy组合退出active用途，不硬删资源。脚本重复运行必须全部keep或明确revision冲突，不能重置人工认证。

### 13.2 严格schema消费者的发布顺序

- 所有新字段先可空、默认不发送。先更新Site Access、Supervisor协议和全部client schema读取能力，保留旧接口兼容包装。旧客户端仍extra_forbid时，服务端v1响应必须显式排除新增字段（包含null）；新client以协议能力声明接受v2字段，不能以“字段optional”误以为旧reader一定兼容。
- Supervisor wire同样做版本能力检查：旧Supervisor未更新时owner_scope_id等新Identity元数据留在owner，不发入旧严格spec；新控制命令只有确认服务端支持才调用。不能让P0的GUI发布因一个新增null字段把六个既有Chrome判成不兼容。
- 新GUI使用operation端点；旧bridge只能作兼容包装，不能继续走失败即close的旧语义。
- Message Bus、Enrichment、必要Crawler Plane客户端全部具备scope传递后，才启用source scope路由；未具备时维持旧路径，不把有明确source的请求静默错配到publisher默认身份。
- 运行schema migration在受控升级一次执行；UI只读GET和新Worker启动不能BEGIN IMMEDIATE做schema迁移。
- 旧Site健康诊断API可显示legacy默认scope并标注deprecated；新库存统一按scope，不能让多个入口的active role互相覆盖。

## 14. 按文件和依赖可直接执行的开发包

| 包 | 主要文件 | 完成条件 |
| --- | --- | --- |
| A：scope/Task/target模型 | `site_strategy/{schema,repository,resolver}.py`；新`scope.py、targets.py、maintenance.py` | 唯一归属、revision、原子交接、幂等迁移及旧字段兼容 |
| B：统一BrowserOperation | `runtime.py、external_runtime.py、browser_runtime.py`；新`browser_operations.py、browser_protocol.py` | 所有recipe正常/异常/延迟挑战必经统一归一化；不丢原status/DOM证据 |
| C：独立控制Worker | 新`identity_worker.py、worker_manager.py`；`factory.py、service.py` | 两轨按Identity独立；取消无界时仅重建对应控制；跨连接target回收 |
| D：主备与能力验证 | `health.py、service.py、api.py、client.py`；新`verification.py` | 一个主/一个备、冷接替、sticky、真实能力门控、精确错误 |
| E：来源上下文 | `client.py、message_bus_v2/{scheduler,adapters,industry_sources,news_adapters,market_sources}.py`；`crawler_plane/runtime.py`；`content_enrichment/{service,managed,pipeline}.py` | 所有实际采集路径和正文/redirect完整传scope；原ArticleHub/源配置不重构 |
| F：维护体验 | `deploy/site-login-{ui,admin}.py、site-login-vnc-relay.py`；`api.py、maintenance.py` | 单次inventory、operation状态、两个入口、失败保留页、单Viewer真实切换 |
| G：Supervisor/容量 | `chrome_supervisor.py、supervisor_client.py、settings.py`；entrypoint/Compose | max_instances接通、实例manifest接管、同Profile排他、8GiB配置、无全局重启依赖 |
| H：存储/构建 | `profile_storage.py、repository.py`；新`retention.py`；`Dockerfile.v2、部署发布入口` | 诊断清理跟得上写入、快照不重复Registry/缓存、流式hash、构建层复用 |
| I：迁移/回归/运行手册 | `scripts/ops/migrate_source_access_scopes.py`，新tests及operations文档、`changelog` | 每源迁移收据、冷备用验收、人工待办和回滚明确 |

开发顺序：**A → B → C/D → E/F → G/H → I**。G的manifest/容量接口可先在隔离环境实现；生产Supervisor切换必须遵守第10.3节。每包重要实现追加changelog，不包含当前并行Jev/初始化/SDK工作。

## 15. 关键回归与真实验收

### 15.1 必须新增的组合测试

1. 业务target→Challenge Task后Manager/Worker业务permit都释放；两轨其他source仍可获取页面。
2. External Worker/Node真实终止重建后，原Challenge target、Profile和Chrome instance保留；底层detach不close人工页。
3. Worker在createTarget响应丢失前后分别崩溃：依据allocation marker找回；跨epoch关闭失败记录不丢；不会关闭无归属blank。
4. 三个source待人工时仍能打开第四源普通Login；切换Viewer不切其他Profile；Later不清Task、不解除主路阻断。
5. primary challenge→冷启fallback→正式列表/正文通过，主原页仍在；备用也失败时无第三路线，真实阻断可解释。
6. 主备同IP、不同runtime、未认证、旧版本/环境变更的配置与接替结论准确；纯内容错误不盲切身份。
7. public200空壳、真空列表、错误ticker、订阅预览、完整短文、404验证文章和MFA中间页分别判定；Challenge已解除但正文缺权限/模板错误时Task可结束，正文能力不伪装READY；门控与证据一次提交。
8. 已登录但verify超时：原页保留；retry使用新真实canary而非缓存旧失败；成功但close失败只显示cleanup_pending。
9. UI后台异常必解除busy，迟到回调不覆盖新操作，后台status不抢Verify/Cancel；inventory调用数恒为一次，不随Site数增加。
10. 来源隔离：两个TrendForce入口、Globe Search/RSS不同Profile；ticker不增Identity；Barron's→WSJ转独立publisher；body dedup不被scope新增破坏。
11. 独立控制器挂死/Managed Node死亡不退出Site Access owner、不重置其他source；全局预算正常释放，父进程status仍响应。
12. Supervisor仅进程重启可接管live Chrome；整容器退出丢页准确TARGET_LOST；无法确认writer时不清锁、不启动第二Chrome。
13. 重复同Task失败不线性增加事件/DOM/快照；按窗口清理能追上模拟写入；SQLite空闲页与物理释放不混报。
14. Profile快照恢复Cookie/必要存储，排除缓存；同批多个Profile仅一个Registry备份；超过2个常规快照可回收且protected不误删。
15. 仅源码不同的两镜像复用依赖DiffID；非root导入、正式Compose及client严格schema兼容通过。

沿用旧`test_site_strategy*、test_service_busy_lifecycle_repair、test_message_source_recurring_repair、test_site_login*`及正文/市场源/总线去重回归；原审计的机制探针改成修复后断言。不能只mock掉`cdp.close`便宣称原页得到保护。

真实集成使用隔离Chrome/临时Profile和本地测试页面，覆盖被故意阻断的SDK取消；生产不注入故障、不发布测试文章、不自动过验证码。是否需要某个真实付费账号由人工维护结果决定。

### 15.2 逐源验收，不挑全绿截面

- 每个browser采集入口覆盖当前启用ticker/L1；共享distribution一次采集和订阅roster分开核对。
- 每源至少10个自然目标周期，区分真实I/O、延期、待人工、有效空结果；正文两篇最终publisher，付费需FULL/SHORT_FULL和权限证据。
- 每个被声明为可用备用的身份，必须完成停主业务（不清主Cookie）、冷启动备用、真实能力验证、自然接替三个环节；未登录备用清楚列未就绪，不为交付全绿放宽判定。
- UI warm/cold/status/verify真实p95及失败阶段留痕；无法人工完成的网站只验工具链，不冒称业务恢复。
- 24–48小时覆盖一次closed sweep及下一实时窗口；再记录7天的target、Worker/Chrome数量、内存、事件/快照/缓存/镜像净增长。期限未到写待观察，不以同步交付等待7天为必要阻塞。

## 16. 发布顺序、回滚及交付边界

### 16.1 生产顺序

1. 冻结本次精确提交、schema和现有Profile/Task清单；先恢复足够构建施工余量，保留正式恢复集。不要对当前4.46GiB余量盲build。
2. 先发布向后兼容A/B、Task导入及新GUI/operation端点，让人工恢复先可用；旧worker客户端仍可请求旧字段。
3. 在隔离环境完成独立Worker、Supervisor接管/容量和所有权测试；需要整Supervisor容器更新时，先明确协调当前活页影响，保留可处理的原Task，不能隐含执行。
4. 窄发布C/D/E及新clients，逐source迁scope；先TrendForce/Globe等可确定验证源，再高风控源；只切对应scope，不全局重置健康。
5. 按第12节登记唯一主备并完成实际登录/冷接替；legacy多路退出用途引用。高风险原Profile保留在选定主身份。
6. 上线H留存与构建修复，检查自然增长；不借本轮去清整个Message Bus/研究库。
7. 正式提交/推送main后按对应服务镜像发布；每次记录Git blob、image digest、scope/Registry revision、前后instance/target和当前人工待办。不上未归并源码overlay，不启用Source Maintenance Codex Worker。

### 16.2 回滚

- schema采取添加式迁移，保留旧字段；回滚应用不能用旧Registry备份覆盖切换后登录/Task事实。
- 已迁移scope回滚要保留“一Profile一个owner”；不能为回滚重新把public-markets/dowjones挂回多个独立源。必要时停掉新源路由并保持明确不可用，而不是并发使用相同Profile。
- 受新Task保护的页面，旧代码可能误关；在存在活新Task时不能直接回旧Site Access。先保留target并停新业务，使用兼容版owner/GUI继续处理；运行回滚最低版本需在发布收据中指定。
- 只回滚受影响服务及配置revision，不回滚业务库、Cookie、消息成功时间或历史发布。Chrome major版本不在本轮变化，不把Profile拷回旧实验卷。

### 16.3 本次重构明确不做

不重写SourceDefinition/监测词/Jev/消息总线，不用模型自动处理网站挑战，不换指纹或自动输入凭据，不把RSS/API强制迁Browser，不无限加Chrome/延长timeout，不建立新的集群/总线/完整后台，不删除业务历史或无依据旧身份，不承诺网站永不风控。

## 17. 完成定义

软件完成不是“所有网站绿色”，而是：每源有独立可解释的身份和控制生命周期；原Challenge现场在可保留的边界内可靠留存，丢失有准确恢复路径；一个待办不阻断其他维护/合格备用；冷备用经过同类真实能力验收；Verify可快速反馈、失败不毁现场、成功能正确恢复门控；资源不会随重复故障和发布次数无界增长。

生产完成还必须列明：哪些主/备真正验收、哪些需要人工登录/Challenge、哪些仍缺合法订阅/独立出口，以及尚未到期的长期观察。**本轮交付仅为这份可执行方案及复审证据，不是上述重构已实施的声明。**

参考：上轮53份文档的冻结索引、当前代码、此次只读快照及UI探针。Playwright CDP默认context/`no_defaults`语义以[官方接口](https://playwright.dev/python/docs/api/class-browsertype#browser-type-connect-over-cdp)为准；Profile单writer以[Chromium资料](https://chromium.googlesource.com/chromium/src/+/HEAD/docs/user_data_dir.md)为准。没有把这些资料当作目标站风控原因的证明。

技能应用：project-discovery用于核对当前MAIN、脏树和生产镜像差异，因此采用分包/受控迁移而非整树发布；web-scraping用于要求实际列表/正文有效性，不采用stealth、随机身份或自动挑战处理。
