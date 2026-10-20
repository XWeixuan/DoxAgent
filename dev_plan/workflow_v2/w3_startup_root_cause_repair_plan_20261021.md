# W3 启动超时复发：根因与第三轮修复实施方案

日期：2026-10-21，北京时间。基线：本地 `bfe1a86b81b0d2624e1b2221bb0fb4a0c840d1c4`；本轮只读核对生产、执行本地故障注入与隔离导入实验，**没有修改生产实现、重启、重放、派发模型或调用券商**。

依据：10/16《确定性耗时与 Fork 恢复实现记录》、10/19《编排层重构实施与验收记录》、10/21《W3 准备/冷启动失败专项交接》。本文替代其他方案中针对本次 W3 启动链路的预算、补池与恢复设计；不接管其 SQLite、W1/W2 输出或停机修复工作。

## 1. 本轮结论和明确取舍

**这次不是“同一个 Fork RPC 又变慢”，而是旧修复解决了结果接回与热路径，却没有把昂贵冷启动、资源压力、补池、取消和恢复真正作为一条生命周期治理。**

已复核的失败链是：

> CPU/I/O 竞争下冷 Python/SDK 启动接近或超过 30 秒 → boot 尚未完成就被终止 → 没派发过 Fork，也进入 thread/list 恢复 → 慢查询超时关闭共享客户端 → 其他恢复请求可能受牵连 → 多层重试与补池继续制造启动成本 → 部分 Case 在模型 turn 前终态失败。

这条链中，boot 超时、恢复查询和终态失败有生产 Case 证据；共享关闭的传播、压力回收后仍补建、取消后内部任务继续等缺陷已在本地对当前实现确定性复现。**还不能证明每个生产 stdout closed 都由共享关闭造成，也不能把全部 30 秒拆成已测的 Python 导入或 SDK 初始化。**修复要消除已证明的放大机制，同时让下一次异常能够留下分段根因。

本轮采用六项必要改动：

1. 移除 W3 启动中仍残留的 O4 整套服务隐式导入。
2. 统一冷进程启动许可与优先级；热驻留续跑不排在冷启动后面；压力下停止投机补池，取消“回收后立即重建”。
3. 启动阶段可观测、预算分段；不再用同一个 30 秒等待覆盖全部 boot 后再笼统重试。
4. 依据“Fork 是否可能派发”恢复原 operation，boot 失败跳过 SDK 全库查询；修复取消后内部执行失去归属。
5. 生命周期辅助 RPC 单客户端、单在途、有限等待；隔离关闭与错误，正常 Fork/模型 turn 不经过这把控制锁。
6. 保住交易→运营交接的短期驻留；按原身份恢复，运营失败不能重发交易。

保留当前 Capsule/Thread 隔离、READY 2 Luna + 1 Sol、一个 SDK 热壳、现有模型及业务并发。**不改成所有 Case 共用一个业务 Thread，不新建调度服务/数据库/通用租约框架，不靠扩大池子或全面提高并发解决。**

## 2. 为什么前两轮修复后仍会失败

| 轮次 | 确实解决了什么 | 未覆盖的条件 | 本次表现 |
|---|---|---|---|
| 10/16 | 原 operation/child 接回，删除无界全局生命周期锁；Python Capsule 预热；资产和确定性路径缩短 | Python 对象准备不等于真实 SDK 已启动；隔离性能测试部分模拟 SDK、Template 预先就绪 | 热路径改进，昂贵初始化仍可能进入 Case；4.225 秒不是生产 SLO |
| 10/19 | 等真实 SDK initialize 才 READY；全局预 Fork 库存；常驻 Research MCP；热 Fork/Resume 及丢响应接回 | 正常隔离环境的毫秒级 Fork，不等于资源紧张时能维持库存；补池/回收没有统一压力判断；共享 helper 失败隔离与取消所有权测试不足 | Fork 已通常较快，故障前移到 boot；恢复路径又将 boot 故障放大 |
| 后续“同绑定一次恢复” | 本次 BE 的 `ready_recovery_count=1` 证明已经执行 | 只扩大同一恢复路径的次数，未区分没发 Fork 的 boot 失败与不确定 Fork；多层计数相乘 | 同 slot 又启动/查询多轮后仍失败；TransportClosed 类型还可能不进入额外恢复 |

前两轮不是没有部署。实际容器构建提交为 `160199b7…`，与 checkout SHA 不同；本轮对 **capsules、capsule_child、thread_lifecycle、w3_ready、jobs、sdk_runtime、app、resource_safety 八个模块**按统一换行计算内容哈希，均与当前本地实现一致。不能以构建标签不同误判遗漏上线。

## 3. 本次复发的证据闭环

### 3.1 规模与时间

主快照为 UTC 10/20 16:27，北京时间 10/21 00:27；开盘取 UTC 10/20 13:30。开盘后新建 92 个 Case 中，15 个 W3 PREPARE 终态失败；另有一个盘前创建、盘后失败，合计 **16 个 W3 准备失败**。16 个均 `attempt_count=0`，该次交易模型任务尚未提交。另有一个 ONDS 交易已提交、运营启动失败，必须单列。

本轮重新对 `timings.json` 按 identity + plane + started_at 去重，保留同 trace 最长快照，得到：

| 边界 | 结果 | 中位数 | p95 | 最大 |
|---|---|---:|---:|---:|
| `sdk.thread_fork` | 62 成功 | 1.92s | 6.51s | 7.29s |
| `capsule.spawn` 成功 | 50 | 22.91s | 31.20s | 32.66s |
| `capsule.spawn` 错误 | 106 Timeout + 29 Cancelled | 31.69s | 37.56s | 63.52s |

分位数采用中位数 / nearest-rank p95，故与交接文档部分分位值略有差异；样本数与错误数一致。成功 Fork 有存活者偏差，不能承诺 Fork 永不超时，但**本轮大量失败明确在 Fork 之前**。

### 3.2 三个代表 Case

**BE `case_9fce33d229f9411689988c41b5fbe42f`**：同 slot `823dc088…`，13:30:50、13:31:57、13:33:39 的 spawn 分别约 31.10/30.67/33.57 秒超时；中间夹入多次 `thread/list`、10 秒取消及 client close；同绑定额外恢复已用一次，operation 最终 recovery_count=3。说明“再给原 slot 一次机会”并没有改变失败路径。

**INTC `case_a0db40d6c34040ee88ed893be4ca5723`**：首次 spawn 约 37.56 秒超时；之后两轮恢复的 `thread/list` 分别遇到 TransportClosed。最后显示 Codex stdout closed，但这个已能定位到**恢复辅助客户端的查询链**，不是证明本 Case 已进入 Fork 并在 Fork 内崩溃。bubblewrap fallback 文字仍不能证明退出原因。

**ONDS `case_59fc7f7248b84709bfaa0af665214b99`**：NO_TRADE 已于 13:36:25 完成；运营 job 随后 startup_wait 约 6.2 秒、spawn 约 39.5 秒超时，后续启动仍在约 30–32 秒失败。其 slot 已 USED、原 Fork 已 COMPLETED。这里需要原 Thread 的运营续跑，不需要也不允许再进行一轮交易。

### 3.3 新补证：现有“安全 NORMAL”看不到 CPU/I/O 启动压力

本轮 UTC 16:48:37（北京时间 00:48:37）只读刷新：CPU PSI some avg10 **85.41%**，load **18.34**，但安全文件为 **NORMAL**。安全控制器实际依据内存、内存 PSI、swap 等指标，不含 CPU/I/O 启动压力。

这解释了为什么“已有安全治理”仍允许在 CPU 已高度拥挤时继续补池。存活 Capsule Python 进程的累计 CPU 运行约 4–5 秒、累计调度等待可达 10–22 秒，与共享 CPU 竞争相符；这些是进程寿命累计，**不是失败 boot 的精确阶段耗时**。

历史 CPU 0 idle、I/O wait 及浏览器占用沿用交接证据。当前快照不能替代开盘时的逐进程历史，也没有证据把本轮归为 OOM kill。

## 4. 进一步定位出的代码根因

### 4.1 W3 仍在为 O4 全量服务付冷导入成本

`sdk_runtime.py` 顶层导入 `codex_monitoring_o4.capability`；该包 `__init__.py` 又立即导入 orchestrator/service，连带加载 Message Bus、Site Strategy、Crawler、CDECR 等契约。W3 v5 只使用只读 Research MCP，根本不需要这些 O4 运营能力。

本地六个全新 Python 进程的 import-only 对照：当前导入 1.61–1.71 秒；仅在私有内存模块中去掉该顶层 O4 import 后为 0.70–0.72 秒，O4 service 和 Message Bus schema 均不再加载。源码未修改、未构造 SDK、未初始化或发模型任务。

**这证明有可去除的依赖成本，不证明生产能直接少 0.95 秒或全部 30 秒来自 O4。**生产繁忙时重复导入、页读取和调度等待会放大这些工作，必须先移除确定不需要的部分。

### 4.2 补池、冷启动和回收使用了不一致的治理边界

- `jobs._dispatch_loop` 非 NORMAL 时 `reap_idle(all_idle=True)`，包括 READY 和交接中的闲置 Capsule。
- `W3ReadySlots.fill()` 不检查 safety；后台每 30 秒执行、claim/configure 也可执行 fill，继续补回被回收的空库存。
- 正常 `warm()` 会在压力下停止，但 READY fill 是另一条路径，不受这个判断保护。
- 后台 prepare 只判断原 request 的 `source_case_id is None`，而 READY request 本来就为 null；之后绑定了 Case 也仍执行 `while jobs._starting` 让行。
- 现有 startup semaphore 同时包住冷启动和热续跑。几个冷启动/准备占满后，已经有健康原 Thread 的运营也要等待。
- provider readiness probe 还可能另建并销毁一个 Capsule，没有统一进入同一冷启动许可。

本地对当前方法复现：压力下 READY 被关闭，随后的 fill 将其置 RETIRING 并立即产生新的 BUILDING。**已证明这是可发生的反馈回路；生产 12 个 resident reaped 记录不足以证明每一个都由这个回路造成。**

### 4.3 当前状态无法区分“根本没发 Fork”与“Fork 结果未知”

`_prepare_operation_observed` 在获得启动信号量、boot 之前就写 `FORKING`；所有重入只因 operation 已存在就执行恢复查询。即使前次只是在等 boot，恢复仍会先做 state-only list，再做完整 list。

本地注入“运行时在真正 Fork 前就报 boot timeout”，第二次准备仍调用 **两次 `thread/list`**。生产 BE/INTC 正好有 boot 超时后进入慢 list 的记录。其完整列表后备可能涉及更多历史读取，不能用来恢复一个从未派发的 Fork。

### 4.4 取消仍有所有权缺口

prepare 创建 `running=task(execute)` 后，先 shield 等 10 秒、再直接等 55 秒；外层捕获 BaseException 更新状态，却没有在所有取消路径里停止并收束内部 task，也不重新抛出 CancelledError。

本地取消处于前 10 秒 shield 内的外层 task：外层正常结束，内部仍能稍后完成“Fork”，持久状态仍是 CREATING。后续 GET/prepare 可能再启动恢复，形成并行旧 owner 与新 owner。**这是确定的协议缺陷，不代表本次 16 个 Case 已逐一证实发生重复 Fork。**

### 4.5 helper 复用降低了正常初始化，却重新引入了故障耦合

`AppServerThreads.request()` 多个并发 RPC 共用一个 client；任一 BaseException，包括取消，会关闭这个 client。初始化锁只保护创建，不保护在途请求与关闭。正常并发测试能通过，并不证明失败隔离。

本地同 client 上，一个 list 在等响应，另一个请求超时并 close，前者随即 ConnectionError。今天三个 stdout closed 中尚缺 client/process ID，不能把这个复现冒充其唯一根因，但必须消除这条传播路径。

## 5. 最终实现设计

### 5.1 工作包 A：精简真实 boot，并留下失败前的阶段证据

修改 `codex_worker/sdk_runtime.py`：

1. 顶层删除 O4 capability 的三个运行时 import；`_o4_capabilities` 初始为 null，仅 `request.o4_operations_enabled` 分支内导入和构造。保存构造时的 capability secret 私有引用，不能错误改用另一份 settings secret。
2. O4 首次使用仍按原工具列表、签名与权限执行；不改 O4 包的对外 API，不修改第三方 SDK/generated models。
3. Data MCP 的 registry/contract 仍保持原按需方式；新 W3 路径不为了初始化去加载 O4、Message Bus、全量 tool factory。
4. 增加 fresh-process 模块集合断言：W3 runtime import/construct 后没有 O4 service、Message Bus server/factory 等非必要服务；真实 O4 请求回归仍能生成原 capability。

修改 `capsule_child.py` / `capsules.py`：

1. 在导入 Worker schema 前发出/落盘首个 boot marker，记录 schema import、SDK/runtime import、runtime construct、SDK initialize 的 start/end。
2. 协议显式区分 `boot_progress`、`boot_ready(state=SDK_READY)`、`error`。父进程循环处理进度，只有收到 SDK_READY 才返回可用，不能把第一条 JSON 当作 boot 成功。
3. 失败时保留最后阶段、capsule_id、pid/start_ticks、累计 wall/CPU、少量进度时间、returncode/信号、是否由父进程主动结束及原因。helper 另带 client_generation/process_id/RPC ID。
4. 复用现有 capsule owner/边界 receipt：每次 boot 一个小型当前快照，阶段变化覆盖写，终态摘要合并到 job 或 operation。无每秒全量日志、无 raw stderr/prompt/env 留存；不能因诊断写入失败让业务失败。
5. 父进程超时前后均抓取自身已知 child 的 `/proc/<pid>/stat/schedstat`，不为每个阶段扫描整机进程；保留现有随机身份+start_ticks 的清理校验。

### 5.2 工作包 B：只给真正的冷初始化排队，所有路径共用同一个小闸门

新增小型进程内 `codex_worker/startup_admission.py`，或同等规模直接放 CapsuleRuntime；**它只管理进程初始化，不管理模型执行，不另建持久任务系统。**

首版行为：

- Worker 全局最多 **1 个物理冷启动**在途，覆盖 Capsule boot、热壳补充、首次 lifecycle helper 初始化、需要初始化的人工 provider probe。
- SDK 已热的原 Thread 续跑、同 Case operations、健康热壳上的 Fork/Resume，不等这个冷启动闸门。
- 保留现有 3 个 SDK handshake 的并发能力，计入的只是实际 Fork/Resume/start handshake，**不得由“等待冷启动许可”的任务先占住它**。
- 优先级：已绑定 Case/原 operations 恢复 > 未绑定 READY/热壳补充 > 维护/可延后探测；同优先级 FIFO。
- claim 将既有准备 waiter 提升为业务优先级，依据实时 slot 绑定，不修改原 canonical request，也不新建一个并行准备。
- 运行中的 boot 不为来了更高优任务而杀掉重建；完成后优先交给既定 owner。

具体改法：

1. `CapsuleRuntime._take/start/_prepare` 内先原子预约健康且仍存活的目标 Capsule；匹配到热驻留或未绑定热壳就直接推进。
2. 只有必须 launch 时取得 cold permit；拿到 permit 后重查一次是否已有可用热壳，防止排队期间库存已补好却仍启动新进程。
3. 现有 `jobs._run_observed`、lifecycle execute 不再用 `_startup` 包住整个 `runtime.start/prepare`。对正式 CapsuleRuntime，将 handshake 限流下沉到取得 capsule 以后；测试/embedded 的其他 runtime 继续有兼容 fallback。不要只在旧锁内部再套一个 cold semaphore，否则热任务仍被三个冷 waiter 堵住。
4. 删除 `while jobs._starting` 的全局空转让行；让统一优先队列决定，而非某个无关 job 的集合是否为空。
5. `_schedule` 的启动名额只计当前实际 cold/handshake，不能因已预约的热启动被另一个冷等待计数挡住；Thread 的单活互斥保持。
6. 取消排队只移除自身 waiter，不关闭其他 Capsule；队列重建依靠现有 durable job/operation，不新增数据库队列表。

### 5.3 工作包 C：补池与回收服从同一资源判断

仍复用 `SafetyStateReader` 的内存硬保护；另在 Worker 每秒最多读取一次 CPU/I/O PSI，形成 **startup_pressure**，仅控制投机启动，不能把 CPU 紧张直接转成全局 CRITICAL 杀任务。

首版门槛：CPU some avg10 ≥60% 或 I/O some avg10 ≥20%，暂停未绑定补池/热壳启动；连续 10 秒低于 CPU 40%、I/O 10% 后恢复。指标不可读时沿既有 fail-open，但全局 cold=1 仍有效。门槛是资源调节默认值，不是交易资格。

| 状态 | 投机补池 | 已有 READY / 交接驻留 | 业务启动 |
|---|---|---|---|
| NORMAL 且无 startup_pressure | 按现有 Luna 2/Sol 1 + 热壳 1，冷启动串行 | 保持健康库存 | 热路径优先，冷路径进入统一许可 |
| CPU/I/O startup_pressure | 不启动新的投机进程 | 不因 CPU 紧张主动杀健康库存 | 已绑定任务仍可启动，物理 cold=1 |
| 内存 PRESSURE | 停补池 | 先回收普通无依赖 idle、热壳和超额库存；健康两阶段交接暂保留；最小 READY 可保留各 profile 1 份 | 沿现有实时优先策略，不能扩大并发 |
| 内存 CRITICAL | 停补池 | 允许回收全部可安全回收的闲置进程，包括最小库存/交接；不否认后续将需要恢复 | 保留原硬安全行为，状态明确为资源等待 |

实施边界：

1. `fill/warm/configure/claim/maintenance` 都调用同一 admission 决策，不能再有绕过 pressure 的补建入口。
2. PRESSURE 不再每 250ms 执行无差别 `all_idle=True`；按上述顺序回收选定进程，CRITICAL 才可全回收。READY 被迫回收应立即更新状态，并在压力退出前不重建。
3. 未绑定某 profile 同类临时初始化连续失败，沿用 scope 状态加 30 秒冷却，下一次 60 秒；任一成功后清零。配置/鉴权错误等待配置变化，不每次 fill 再创建新失败 slot。
4. 按需服务已绑定 Case 的恢复不被“投机补池冷却”封住，但仍受总期限/实际启动次数约束。
5. 回收和 SDK delete 放受控后台队列执行；dispatch loop 只预约回收，不同步逐个等 close 后才调度业务。保持 process identity 校验和 quarantine，不能用“不等清理”放任旧 owner 与新 owner 并发。
6. provider readiness 读取最近验证或借用闲置未绑定热壳，借完归还；业务繁忙/无空壳时返回 deferred/provider_verified=false。常规健康检查不为证明健康创建并销毁新进程。

主机 CPU 资源配合：在现有 Compose 给 codex-worker `cpu_shares: 2048`，chrome-supervisor `256`、site-access `512`，其他不变；这是相对权重而非 CPU 保底或硬配额。只作为已证实竞争环境下的最小调度配合，不改抓取/浏览器业务逻辑，也不将其当作容量充分的证明。若相同自然负载下仍持续 CPU PSI 高、冷启动无法稳定完成，需要独立治理浏览器后台负载或扩容；不能再以无限重试和继续加大超时替代资源不足的事实。

### 5.4 工作包 D：准备协议按真实副作用边界恢复

继续使用 `w3_thread_operations`、`w3_ready_slots`、`.w3-fork-receipt.json`。**保留原 operation_key / parent / run / cwd / request / cutoff / 已确认 child。**

新增最少字段：

| 对象 | 字段 | 用途 |
|---|---|---|
| operation | `owner_token`、已有 generation、`capsule_id` | 一个物理执行 owner，迟到回调不能覆盖新 owner |
| operation | `fork_dispatched_at`、`startup_attempts`、`next_retry_at`、`last_error` | 区分 boot 与可能 Fork；仅实际启动计次数 |
| slot / 旧入口 operation | `prepare_deadline_at` | 已绑定业务首次准备的固定总期限；重试/轮询/重启不刷新 |
| job/operation receipt | boot 阶段、run command 是否派发、已知 thread/turn、清理结果 | 支持 typed error 与安全恢复，不从空 ID 猜副作用不存在 |

步骤保持现有 status，细化 step：

`ACCEPTED → WAIT_BOOT_SLOT → BOOTING → SDK_READY → FORK_DISPATCHED → CHILD_KNOWN → MCP_READY → READY`

或在原 child 已知时：`WAIT_BOOT_SLOT/RESIDENT_FOUND → RESUMING_CHILD → MCP_READY → READY`。恢复为 `RECONCILING`；资源暂缓为 `WAIT_RESOURCE`。不能在获得许可和 boot 前就标 FORKING。

**Fork 派发边界：**父进程收到 SDK_READY 后、发送 prepare_fork command **之前**先持久记录 `fork_dispatched_at` 与 owner。宁可保守记录“可能发过”，不能发出后才试图补记录。boot 超时发生于此之前，有确定的无 Fork 副作用证据。

按以下表恢复：

| 已持久化事实 | 下一步 |
|---|---|
| boot 未 ready，Fork 从未派发 | 清理本 owner 后按资源许可重启；**不调用 thread/list** |
| child ID 或匹配原 run/parent 的 Fork receipt 已在 | 直接用原 child；驻留失去时 Resume，不能再 Fork |
| Fork 可能派发、无 child | 先收束原 owner，按 receipt→精确 cwd/parent 的 state-only 分页查询；仍无法确认时最多一次有界完整后备查询 |
| 恢复查询超时/transport closed | 保持原 operation 的 RECONCILING，记录下次时点；不把查不出来当成零结果，不新 slot/Fork |
| 确定零 child，原 owner 已结束 | 在总预算和实际启动次数内，才允许同 operation 再派发 |
| 多 child、原 owner 无法收束 | 明确身份歧义/quarantine，停止自动重建 |

原准备没有新字段时不能假定“没派发过”，按 UNKNOWN/可能派发恢复。找到 thread 不等于 SDK/MCP 驻留已 READY；保留现有 Resume 和同 Capsule tools/list 确认。

**取消修复：**

1. preparations 必须持有实际 execute 的 owner task，不能只跟踪一个 shield wrapper。10 秒只作慢提示，不能改变 owner 或另开任务。
2. HTTP 客户端不再等待不取消后台 operation；GET 纯读，重复 POST/claim 只接回原 owner。
3. Worker 真停机/主动取消：取消并 await 实际 execute；清理自己拥有的 Capsule，保存最后阶段和可能副作用，重新抛出 CancelledError。
4. 所有状态回写带 owner_token 条件；在确认旧 owner 已结束前不允许新的物理执行。进程重启继续依赖现有 Worker 目录排他锁和原进程清理，不另造租约系统。

### 5.5 工作包 E：一种恢复预算，取消多层计数相乘

首版采用下列值。它们是**异常上限，不是正常时效目标**：

| 项目 | 首版 | 约束 |
|---|---:|---|
| 已绑定 Case 总准备期限 | 180s | 从首次业务绑定/旧入口首次 prepare 接受起，兼顾原业务允许时间；固定持久化，不能靠轮询刷新 |
| 单次 boot 硬上限 | 90s | 包含 Python + SDK initialize；30s 只记 slow；失败前保留阶段证据 |
| 单次 Fork/Resume/MCP 准备 | 最多 45s | 服从总期限剩余量；Fork 已发后按不确定副作用恢复 |
| 单个 helper RPC | 10s | 等许可/排队与 RPC 分开；超时后不放大为其他在途请求失败 |
| owner 清理 | 沿现有有限清理上限，至多约 15s | 预留于总预算内；无法收束即 quarantine |
| 物理 boot 尝试 | 最多 2 次 / 同一业务准备 | 只有实际开始创建进程才加一；排队、GET、恢复查询不加 |

未绑定空库存不以“入队时间 + 180s”占用未来 Case 的业务额度：排队可暂缓，只允许一个活动 build；实际启动一次有 90/45 秒分段上限，失败后结束该空 build、scope 冷却。Case 绑定时才设置一次 `prepare_deadline_at` 并提升同一 build；如果空 build 已消耗物理启动，仍计入该次准备，不能把绑定当作免费重置。已 READY 库存长期健康可保留，不因业务准备期限过期被误退役。

总期限与每一步上限取最小，**不是 180+90+45+15 秒相加**。job 冷 Resume 还服从现有 runtime.start 最多 120 秒的外层上限；不能设置内部 135 秒再被外层 120 秒意外取消。模型 turn 的 timeout 不变。

删除“lifecycle 三次 + READY 再一次”的独立重置：Worker 根据阶段、副作用、剩余时间和这两个实际 boot 尝试作唯一恢复决策。`slot.error` 只表示本次准备最终失败；暂时故障放 `last_error/next_retry_at`，claim 返回 pending，不提前抛 409。

客户端对 typed retryable prepare 状态统一映射 `w3_prepare_pending`；继续走现有不消耗 Effect 业务失败次数的分支。鉴权、配置、身份冲突和 quarantine 明确终止；TransportClosed 不仅因异常类型就永久拒绝，也不因“可重试”就绕过原身份重新创建。

`CODEX_START_TIMEOUT` 继续保留外部兼容标签，增加结构化 substage。仅 boot 失败、确定尚未发送 run command 的 job 可以按原 job/phase 自动启动恢复；**thread_id/turn_id 为空本身不是模型没执行的证明**。run 已可能派发时，先查原结果/turn 身份，不能直接再开一个 turn。

### 5.6 工作包 F：helper 故障隔离和两阶段驻留

**生命周期 helper：**

1. 保留常驻 client，但控制 RPC 同时只执行一个；锁只覆盖 helper 的 initialize/request/必要关闭，不覆盖 Capsule Fork、模型执行、所有 Case 的热启动。
2. 业务恢复查询优先维护 delete；等待按所属 operation 剩余期限有界，维护超时延期，不长期占着控制队列。
3. 未取得执行权时取消，只取消该 waiter。RPC 执行中发生真实 transport fatal/timeout，才将这一 generation 标不可用并清理；下一请求必须等旧 client 收束后建新 client。
4. SDK 正常业务错误，例如 thread not found、参数错误，不关闭健康 transport。不要继续 `except BaseException: close(shared_client)`。
5. 初始化也进入统一 cold permit；helper 记录独立 owner 身份、PID/start_ticks、generation、退出码，按实际拥有的进程树清理，不只凭 `_sync._proc` kill 一个未知残留。
6. 不回到 10/16 前的无界全局锁。正常热 Fork 不走 helper，这个序列化只限制少量控制操作；禁止在锁内扫目录、删 Workspace 或等待模型。

**交易→运营：**

1. 成功 trade_decision 的 Capsule 释放为短期 handoff lease，默认 90 秒，或到原阶段允许结束点更早者。operations 立即继承原 Thread、同一 SDK/MCP；正常 idle 30 秒回收规则不覆盖这段交接。
2. PRESSURE 优先保这段有明确后续工作的驻留；CRITICAL 仍可回收，记录原因。不能为保护 90 秒租约违反内存硬安全。
3. operations 结束释放 lease，之后沿普通 idle 策略；失败重试/真正终态也必须释放，不无限延长。
4. 原 resident 已被收回时，用统一热壳/冷许可 Resume 原 Thread，仅恢复 operations job。交易结果、意图 ID、Policy 消费及成交状态都不能被改写。
5. `can_start_warm` 与实际预约都校验 process 存活、healthy、非 busy；只看布尔缓存不足以宣称 resident 可用。

## 6. 文件级实施清单与顺序

| 顺序 | 文件 | 必须落地的修改 |
|---|---|---|
| 1 | `codex_worker/sdk_runtime.py` | O4 capability 延迟到实际 O4 分支；启动阶段回调；Fork/Resume 子阶段剩余预算；原 child 优先 |
| 2 | `codex_worker/capsule_child.py`、`capsules.py` | boot_progress/严格 SDK_READY；失败前证据/退出码；原子热预约；cold/handshake 分离；handoff lease |
| 3 | `codex_worker/startup_admission.py`、`jobs.py` | cold=1、业务优先、排队不占 handshake；startup_pressure 缓存；回收不阻塞 dispatch；probe 不额外造冷进程；typed substage |
| 4 | `codex_worker/w3_ready.py` | 实时绑定提升优先级、共同压力决策、scope 冷却、唯一恢复预算、pending/final error 分离、去掉特殊一次计数重置 |
| 5 | `codex_worker/thread_lifecycle.py` | 派发标记/owner、按阶段恢复、无 Fork 不 list、取消 await 实际 task；helper 单在途及错误分类 |
| 6 | `codex_runtime/client.py`、`persistent_runtime_v2/w3.py` / `w3_execution.py` | typed pending 与原 binding 接回；handoff/phase 状态；既有 stage/result/原截止不变 |
| 7 | `settings.py`、`.env.example`、`docker-compose.v2-production.yml`（核对 server override） | 少量启动预算配置与 codex-worker/v2-chrome-supervisor/v2-site-access 的 CPU shares；不改业务模型或 Case/W1/W2 并发 |
| 8 | 对应 tests、`changelog`、实施记录 | 下节验收；记录本轮覆盖与残余，不把计划或模拟结果当生产成功 |

数据库仅在已有表加字段；迁移幂等、老行 UNKNOWN 恢复。不要为这轮更换 operation_key、重写 canonical request 或建立第二套任务状态表。

## 7. 必须新增的验收，避免第三次只测健康热路径

### 7.1 本轮已完成的定位实验

`eval/w3_startup_root_cause_20261021/reproduction.json` 已复现：

- 一个 RPC 失败关闭 client，另一个请求受牵连。
- PRESSURE 回收 READY 后仍补建 BUILDING。
- 原空准备已服务业务，但仍受无关 `_starting` 让行。
- 前 10 秒 shield 内取消外层，内部继续、durable 状态不一致。
- 注入纯 boot timeout，恢复仍做两次 list。

这是**当前问题的证明，不是修复通过**。导入对照也是私有内存实验，不计作生产性能验收。

### 7.2 实现后自动测试矩阵

| 场景 | 通过条件 |
|---|---|
| 35–45 秒、持续进展的 boot | 超过旧 30 秒仍能就绪；同 attempt，不重建；完整阶段记录 |
| boot 真失败/进程退出 | 有最后阶段、退出码/父杀原因；未发 Fork 时零 list、零模型 turn |
| 20 个 Case 同时到达，库存仅 2+1 | 每 Case 唯一 slot/operation/child；物理 cold 最大 1，热任务可先推进；没有每次 claim 再补一排不受限启动 |
| 已绑定 BUILDING 与空库存同时排队 | 绑定 waiter 提升，后台不抢其许可；不改 canonical request |
| 三个冷 waiter 与一个健康 operations | 热 operations 不等待冷 boot 或其 permit；SDK handshake 仍有实际有限并发 |
| CPU/I/O 高、内存 NORMAL | 停投机补池，保持健康 READY；已绑定业务有界推进，无“状态 NORMAL 所以照常造进程” |
| PRESSURE→NORMAL 反复切换、CRITICAL | 无回收/补建风暴；硬内存保护仍有效；一段故障只计实际物理启动 |
| 队列等待 70 秒、后续 boot 正常 | 队列不被错误计为 boot timeout；总期限不因 GET 重置 |
| 取消发生于排队、boot、Fork 已发、收到 child 后 | 旧 execute 与新 owner 不重叠；未知 Fork 不重发；迟到状态写被 owner_token 拒绝 |
| 一个 helper RPC timeout，另一个排队；正常 not found | 后者不被共享 close 误伤；not found 不销毁 client；维护不堵正常热 Fork |
| Worker 重启 / response 丢失 | 原 receipt/child 接回，必要时只 Resume；旧 UNKNOWN 状态不猜成未派发 |
| ONDS 型已提交交易、operations 启动失败 | 只恢复原运营阶段，交易提交次数始终 1 |
| 120 秒 job 外层 / 180 秒准备总期限 | 所有子预算受剩余时间限制；到期一次清理/终态，无无限 pending |
| 新旧 HTTP/embedded 回测 | 新字段向后兼容；回测仍无 Web/Broker，且不触发真实模型 |

沿用现有 W3、Worker、资源、生命周期回归；将本轮五个缺陷复现转为失败前/修复后测试，不能删掉注入条件来让测试通过。

测试落点：新增 `test_w3_startup_admission.py`、`test_w3_boot_protocol.py`；扩展现有 `test_w3_ready_v5.py`、`test_w3_deterministic_repair.py`、`test_w3_writer_handoff.py`、`test_w3_boundary_timing.py`。现有 `test_concurrent_lifecycle_call_cannot_use_client_being_closed` 实际只覆盖两个 RPC 都成功的情况，必须增加一个 RPC 在途、另一个超时/取消的负向用例；原“并发正常请求不阻塞”测试要改为区分 helper 控制串行与独立 Capsule Fork 不受影响，不能为了沿用旧断言恢复共享关闭缺陷。

### 7.3 真实能力与生产验收

先在远端独立目录、私有 CODEX_HOME 做真实 SDK **零模型 turn** 的 initialize/Fork/Resume：包含冷缓存、并发启动、跨 30 秒延迟、kill 自有 owner、丢响应；不得使用生产 Case 或生产 SDK 进程作故障注入。端到端模型测试仍按另行批准的隔离语料执行，生产验收只看自然流量。

上线验收至少覆盖一个真实开盘高负载窗口，不能拿闭市空载或预先准备好 Template 的夹具代替：

- 新 boot 前进度、退出原因覆盖率 100%；每个失败可归到 queue/boot/import/SDK-init/Fork/MCP/Resume/reconcile。
- 无确定未发 Fork 的 prepare 触发 list；无因为一个 helper 请求取消而关闭另一个在途请求；无相同 operation 的 owner 重叠。
- 无 scope 在压力期持续 BUILDING→RETIRING→立即 BUILDING，冷启动并发始终 ≤1；CRITICAL 后缺池如实计冷回退。
- READY / 已有 resident 的正常启动 p95 <2 秒作为内部目标；Effect→commit 扣除全部 SDK 交易 turn 区间并集后的非 SDK **p95 <5 秒**仍是业务验收，不排除失败、冷回退、空池等待。
- 准备终态失败目标为 0；若自然负载下仍出现失败或持续空池，按明确阶段继续查容量/SDK，而非再增加一层重试。样本数、失败数与 observation 窗口完整报告，不能以“小样本没失败”宣称已证明可靠性。

## 8. 上线、兼容与存量 Case

1. 先部署兼容字段与 Worker，再更新 Scheduler/client；新协议与旧 receipt 均可读。检查实际源文件 hash、active template/profile、READY 数、cold permit、startup_pressure，再确认接流量。
2. 既有模板、研究 context_ref、原 Case 与原 child 继续使用；本轮不是业务合同/模型/Prompt 重构，无需全部重建模板。
3. 保留新旧诊断窗口和每次失败，不做生产 kill/压力注入。CPU shares 随本次部署显式记录，不能未记录地把环境改善全部归功于代码。
4. 针对原 16 个失败 Case 先出恢复 manifest：原 Case/Effect/slot/operation/child/结果/业务截止/owner 清理状态。已确认过期的交易不能为了验收恢复而延长有效期；未过期才交由原业务受理逻辑判断。
5. ONDS 单独恢复原 operations，不重跑 W1/W2、不再提交交易。已有最终结果/意图的 Case 先接回结果，禁止以缺阶段回执为由新建模型任务。
6. 恢复先单条观察原身份与副作用次数，再处理名单；真正已退役且无任何可能存活副作用的空准备，才按原恢复工具的明确映射规则处理，不能因 claim 报错就自动换 slot。
7. 回滚保留新增列及原证据，不回滚或删除业务记录。旧版本不得与新 owner 同时写同一 Worker root；排他锁和停机收束必须保持。

## 9. 预期改善与仍然存在的边界

本方案最有把握去掉的是：W3 无关的导入、投机启动与回收相互抵消、boot 失败后的无效全库恢复、客户端故障传播、取消后失去所有权，以及交易后本可直接续跑却又冷启动的成本。这些有代码、Case 和确定性复现支撑。

仍不能承诺四核主机在任意浏览器/磁盘负载下都能 <5 秒，或断言尚未观测的 SDK 初始化挂起已被排除。新增 boot 证据就是为把这些剩余因素分清。**90 秒 boot 上限只消除“健康进程刚要就绪就被 30 秒杀掉”的错误阈值；真正的正常路径提速依靠减工作、维持热库存、资源优先级和避免恢复放大，而不是把超时数字继续调大。**
