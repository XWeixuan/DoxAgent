# 持久化 Case 分钟级延迟与 W3 超时：P0 排查报告与统一修复方案

日期：2026-10-28，北京时间。**本轮只读生产、检查代码并在临时目录复现机制；未修改生产配置、数据库、调度或交易逻辑，未重跑 Case、调用模型/券商、启停探针或发布容器。**

2026-10-28 方案复核：纳入消息总线侧四项审查意见。区分心跳与配置安装、当前 ACK 与旧任务恢复凭证、单次执行计时与阶段总预算、按库隔离的短写与业务阶段恢复。**撤销上一版新增的 795s 阶段硬期限及多次模型尝试共享 600s 的限制**；本次只修复执行与计时正确性，沿用已有准备/模型重试策略。具体边界和新增验收见 R0—R3、R6 及第 7 节。本次复核只修改方案和 changelog，没有修改实现或生产状态。

## 1. 结论与优先级

这不是单独的 W3 Fork 超时复发，也不是前端把毫秒算错。**系统将短控制事务、完整业务内容、网络任务的执行所有权和恢复调度耦合在一起；共享 SQLite 写阻塞会穿过线程/事件循环边界，继而影响续租、结果接回和新任务派发。展示层主要记录模型调用，未完整表达这些等待及恢复。**

必须同时处理以下问题，不能仅调大模型或 Fork 超时：

1. **长事务与写等待形成共享瓶颈。**事故窗口 Scheduler 有 7,901 条慢事务记录，其中 2,489 条 `SQLITE_BUSY`；成功写事务中出现一次约 161s 的总区间，其中等写锁仅约 0.3s，其余约 160.7s 在事务内。任何与其争用该 SQLite writer 的续租、阶段提交、round 输入持久化都可能一起延期。原先“确定性流程”并不等于“短事务”。
2. **恢复推进错误地依赖上游的实时健康证明。**Bus ACK 有效期 60s；Scheduler 先按新鲜 Bus ACK 构造 admitted ticker 集合，再执行整个 ticker tick。ACK 过期后，已接纳 Case 的租约回收、Effect 派发、W3 结果接回也一同失去入口。现场 9 个标的 Bus ACK 停在北京时间 00:18:40 左右，Runtime ACK 停在 00:20:12—14；01:46 读取时都已过期。容器仍运行、Worker 的 dispatcher 正常，不代表持久化业务在推进。
3. **异步调度器仍在直接执行同步数据库/文件工作，模型预算还由共享父 loop 晚开。**W3 共用一个 `prv2-w3-async` loop，阶段回调、输入/回执写入等直接调用同步 journal/repository；Worker loop 也有直接 SQLite 调用。Capsule child 发出 started 后已经运行 SDK，父进程却要先落盘 RUNNING 状态才开始 600s timeout。一个阻塞调用既能推迟计时开始，也能推迟其他 Case 的网络收包、回调与 timeout。把 `wait_for` 换成同任务 timeout 修复了取消不变量，却不能让同步 SQLite 调用变成可抢占操作。
4. **任务所有权跨边界不完整。**`_wait_v4(...).result(timeout=...)` 超时只结束调用者等待，不会自动取消或接管底层 coroutine；普通 `ThreadPoolExecutor.submit` 也不传播 `write_scope` 的 ContextVar。外层租约、热 lane、W3 coroutine、远端 durable job 并非同一个生命周期。过期后的重入会放大争用；不能假设一次上层退出意味着实际执行已停止。
5. **模型成功后的落盘不是独立恢复阶段。**W1/W2 的 provider 返回与 `append_turn` 之间仍可能因 SQLite 故障失去可复用的完成凭证；基础设施重试回到整个 lane/Case。日志能看到失败的热 lane，但 `runtime_v2_turns` 可能没有对应一次模型失败记录。W3 已有 durable job/receipt，仍须把接回、提交与业务重试分开。
6. **Case、阶段、模型调用的时间及状态语义不统一。**Case 时间可以从入队/接纳开始，模型 attempt 从实际请求开始；W3 attempt 又包含 Worker start/run/cleanup，既不是整个 W3 阶段，也不等于 SDK 本体。失败 SDK 遥测可能缺失，而最新成功阶段只保留最后一个 job。不能相加几个前端数字就得到总时长，更不能把缺失当作零秒。

**因果顺序也要区分：长事务/等待退化先发生，ACK 大面积失效在后；后者解释持续不恢复，不能倒过来解释此前全部分钟级延迟。**同样，01:46 的健康 Worker 不能反证 21:36 那次 turn 的执行情况。

**W3 644s 需单独纠正一个判断：Corning 第一次 turn 确实进行了持续的研究，并非后台凭空等待了全部 644s。**其 native 区间约 633.856s，有 14 次 response、14 个外层工具批次；其中也包含慢工具返回、Code Mode 续等和未分解的服务/生成/传输时间。600s 的预算最后却到约 633.9s 才中断，说明预算执行/交接也存在滞后。该 Case 的其他十几分钟则明确落在模型之前、阶段准备及结果接回等区间。

修复顺序推荐：**先恢复推进与短事务边界，再隔离事件循环和理顺执行/回执所有权，最后统一时延展示与验收。**不增加模型并发、不给相同任务另造身份、不用放宽超时掩盖等待。

## 2. 样本、版本与口径

### 2.1 时间范围

客户端日期已是 10 月 28 日；指定 Corning 事故发生在 **10 月 27 日美股交易日、北京时间 10 月 27 日晚间**，异常延续到 28 日凌晨。为避免只看跨日后的少量样本，本轮读取：

- 主 cohort：北京时间 **10/27 00:00 至 10/28 01:39 左右**创建的 915 个 Case，全部为 REALTIME。
- 状态：848 COMPLETED、37 FAILED、22 PENDING_W3、7 RUNNING、1 ADJUDICATED。失败和未完成没有按成功耗时填零。
- 3,382 条 W1/W2 turn、1,591 条 Effect、1,315 条 W3 stage。Worker 导出 1,463 条 job，其中按 stage job_id 和既有 `case_id[-24:]` attempt 身份可关联 1,292 条、647 个 Case。
- 深查 18 个代表 Case；读取 25 个 W3 native turn。另读 1 条初始化超时作边界对照，不计入 W3 统计。
- 事故日志窗口：UTC **14:28—16:23**，即北京时间 22:28—次日 00:23，来自当前 Scheduler 容器保存的完整窗口。较早 Corning 现场主要依赖 durable 记录、boundary timing 与 native rollout。
- 后续控制、资源和非阻塞栈快照读取至北京时间约 01:50；最后一次小字段状态复核为 **02:13:02**。这些是补充状态，不伪称与 cohort 同一原子快照。

最后复核时，9 个标的 Bus/Runtime ACK 仍停在上述旧时点；7 条 CASE task 为 RUNNING，275 条 CASE task 为 PENDING，另有 13 条 RUNNING、6 条 PENDING_RETRY Effect。这里 task 数是全局存量，包含旧任务，**不等于本轮 915 个 Case 的待重跑数**。最近新 Case 的创建时间仍为 UTC 16:07:37。这说明截至该快照，停止推进尚未自然恢复；报告不是修复完成声明。

### 2.2 版本核对

远端工作树 HEAD 在初查时为 `74c07266a2e9bd2fa63c9ac3502d20f386b53ef1`。组件存在分批发布：Scheduler 自 UTC 14:28 运行，Worker 自 16:52 运行，Bus/Collector/正文又有各自启动时点。

Worker 环境里的 `DOXAGENT_BUILD_COMMIT` 仍为旧 `d5f43fb…`，**不能拿这个字段断言旧代码仍在运行**。本轮导出实际源码并规范换行核对：Worker 的准入、Capsule、job、生命周期、ready、SDK 等关键文件与本地当前代码一致，包含上一轮鲁棒性实现；现场 Condition 无占用、cold 无 owner/等待者、无清理隔离、dispatcher 正常。

Scheduler 的大部分相关代码与本地一致，但其 `InitializationRepository` 尚无后加的 `initialize=False` 开关，`journal.is_sqlite_contention` 也缺少本地新增的 `WriteAdmissionBusy` 分类。这是实际组件漂移；不据此直接宣称它解释了全部早期延迟。实施应按组件源码/镜像及契约核对发布，不能只凭主机 HEAD 或陈旧 build 环境值验收。

**为什么此前启动修复没有覆盖这次问题：**前几轮修复主要解决 Fork 操作接回、ready-slot 预备、冷启动许可释放和不确定 child 的隔离，保护的是“能否安全启动、取消及恢复同一个物理任务”。本次已有 ready-slot 在约 19s 内成功、正式 job cold_start 仅 1.9s，却仍有数百秒的 job 前后等待；现场也没有残留 cold owner。那些修复应保留，但它们没有改变共享 writer 的事务范围、Scheduler 的 ACK 门控、父 loop 的阻塞与计时起点，也没有为 W1/W2 增加独立的付费结果回执。重复修改 Fork 超时或许可实现无法覆盖这些根因。

### 2.3 统计定义

| 指标 | 定义与限制 |
| --- | --- |
| Case 时延 | Case.created_at → decision_completed_at；不进入 W3 的已完成 Case 使用 completed_at。前者是交易决定提交，后续运营另列 |
| 第一次模型前等待 | Case.created_at → 最早已保存的 W1/W2 started_at；包含入队、准备、未记为模型 attempt 的基础设施失败和恢复，不能全部叫线程池排队 |
| W1/W2 请求区间并集 | 所有已保存 turn 的 started_at—finished_at 并集；并行 W1/W2 不重复相加。此区间也不保证全是服务端计算 |
| W1/W2 区间内未覆盖时间 | 最早请求到最后请求完成的大区间，减去上面的并集；定位 round 间/恢复/准备等待，不称纯空闲 |
| W3 阶段 | route_to_w3 Effect.created_at → trade_decision.committed_at |
| stage → 首个 job | 保留第一阶段 accepted_at，对齐该 Case 所有交易尝试中的第一个 job.created_at；不能只用最后成功 job |
| job 完成 → 提交 | 最后交易 job.finished_at → stage.committed_at，包含传输、接回、校验及业务写入 |
| W3 非 SDK | 逐 Case 用 W3 阶段减去全部交易尝试的有效 sdk_duration；任一尝试缺失则该值缺失。Corning 第一条失败 SDK 遥测为空，不能扣成 0 |

各行分母不同，分位数不能直接相加。比较分段是描述性对照，不是隔离模型、负载和部署版本后的因果实验。

## 3. 退化的规模与位置

以 UTC 12:00（北京时间 20:00）划分正常前段和明显退化段：

| 指标 | UTC 00:00—12:00 | UTC 12:00—16:00 |
| --- | ---: | ---: |
| Case 总数 | 302 | 303 |
| COMPLETED / FAILED / 尚未最终完成 | 302 / 0 / 0 | 253 / 33 / 17 |
| 已完成 Case >10 分钟 | 1 / 302 | **154 / 253（60.9%）** |
| 已完成 Case 时延 p50 / p95 | 117.07 / 247.89s | **762.56 / 2,393.99s** |
| 第一次模型前等待 p50 / p95，全部 Case | 4.94 / 19.11s | **71.68 / 1,983.97s** |
| 已记录 W1/W2 请求区间并集 p50 / p95 | 20.64 / 129.08s | **15.41 / 111.66s** |
| W1/W2 大区间内未被请求覆盖的时间 p50 / p95 | 0.31 / 7.70s | **6.65 / 693.21s** |
| 已提交 W3 阶段 p50 / p95 | 86.21 / 209.18s，n=239 | **468.47 / 1,198.47s，n=196** |
| stage → 首个交易 job p50 / p95 | 5.45 / 63.77s，n=239 | **210.81 / 638.71s，n=204** |
| 最后交易 job 完成 → 提交 p50 / p95 | 7.21 / 24.82s，n=239 | **71.03 / 411.25s，n=196** |

将有完整 SDK 时长的 W3 Case 严格配对，前段 n=239、事故段 n=191：

| 配对指标 | 前段 | 事故段 |
| --- | ---: | ---: |
| SDK p50 / p95 | 60.13 / 148.67s | 86.67 / 256.04s |
| W3 非 SDK p50 / p95 | 15.32 / 97.58s | **375.04 / 942.60s** |
| 非 SDK 占配对样本 W3 总秒数 | 29.4% | **80.3%** |

这才是本轮最重要的量化结论：**整体退化主要发生在模型调用之外，且发生在进入模型、round 间、W3 job 创建前、job 完成后多个边界。**SDK 也有变慢和真实长 turn，不能说模型完全没有问题，但不能用 Prompt 优化替代架构修复。

北京时间 10/28 00:00 后 cohort 只有 17 个新 Case：3 COMPLETED、1 FAILED、8 PENDING_W3、4 RUNNING、1 ADJUDICATED。不能把这个已经失去推进的窗口与正常完成窗口直接混算成功 p95。主 cohort 中的 30 个未最终完成对象均超过 10 分钟，作为开放等待保留。

## 4. 三类代表性 Case：时间到底去了哪里

### 4.1 Corning / Yahoo：29 分钟交易决定，第一次 turn 644.69s

Case：`case_5e9a10c4358e4fe38167166ca80037d6`，COHR。完整标题：Corning Earnings Might Be Just What Optical Networking Stocks Need Right Now。

以下用 UTC，换算北京时间加 8 小时：

| 边界 | 记录 | 本段说明 |
| --- | --- | --- |
| Bus 消息时间 | 13:11:40.512 | 到 Case 接纳还相隔 **666.63s**；这部分在 Case 29 分钟之外 |
| Case 接纳/created | 13:22:47.147 | 接纳时点并不代表已经发起 W1/W2 |
| 首次 W1/W2 started | 13:28:01.784 | Case 内先等 **314.64s** |
| 最后 W1/W2 完成 | 13:28:42.884 | 大区间 41.10s，已记录请求并集 34.51s |
| W3 Effect 创建 / stage 接受 | 13:28:43.547 / 13:28:49.967 | 之后仍不是模型开始 |
| bound ready-slot 创建 / READY | 13:33:26.017 / 13:33:45.316 | pool miss；这一份原 operation 约 **19.27s**，物理启动 1 次成功，不是单次 Fork 卡了八分钟 |
| 第一个交易 job 创建 | 13:36:52.202 | stage→首 job **482.24s**；slot READY 后仍等 **186.89s** |
| job.started / native.started | 13:36:58.707 / 13:36:59.739 | cold_start 1.90s，说明该 job 的长尾不是冷启动本身 |
| native final_answer / aborted | 13:47:33.224 / 13:47:33.593 | native 区间 **633.856s**，结尾 interrupted；不能把 final_answer 文本直接当完成回执 |
| 第一个 job.failed | 13:47:43.397 | 前端约 **644.69s** 是 job started→finished；包含约 10.29s cleanup。失败 telemetry.sdk_duration 为空 |
| 第二个交易 job | 13:48:47.073—13:49:56.094 | 同原 Case/thread 的新 dispatch generation；SDK **38.939s**，另有约 29.86s 启动，其中 thread_resume 约 23.99s |
| 交易决定提交 | 13:51:33.996 | 最后 job 完成后再等 **97.90s**。Case→决定共 **1,726.85s（28分46.85秒）** |
| 后续运营 | 13:54:12.028—14:03:43.020，14:03:47.608 提交 | 运营 SDK 568.045s；不混入上面的交易决定时延，但会造成用户感知的更晚最终收束 |

同标题还存在另外两条来源不同的 Case：IBKR 版本 `case_45ac150f80ed4524b7502dbde89199df` 和 Barron's 版本 `case_e93cb310625e44b183daae0d646f4b79`，分别约 366.02s、242.45s。它们不是这次 644s 的 job，不能按标题把三个 Case 合成一条恢复链，也不据标题重复就更改新旧判定规则。

**第一次 turn 实际做了什么。**消息只有摘要，Agent 需要获取 Corning 原始披露、比较既有光通信/AI 需求基线，判断对 COHR 是业务增量还是行业主题，再做行情完成态核验。native 记录有 14 次 response、14 个外层工具调用，返回约 177,708B；工具区间并集约 168.657s。包括 catalog 15.20s、D1/D2 批读与续等合计约 48.68s、多个 Web 批次，其中有 35.59s 和 43.05s 的慢返回。最后 market_view 返回后至 final_answer 约 120.40s，没有后续工具调用。

这些是外层观测区间：未分解时间包含模型生成、服务排队、传输、Code Mode/系统调度等，**不能叫纯推理**。实际 MCP 内部纯读文件耗时也不能从一个外层 15s 调用直接推出。该 turn 未发现 compaction，不能把本 Case 超时归因于上下文压缩。

600s 模型等待预算与 native 633.856s 的差距存在两条明确设计路径：child 在 started 后直接 `await handle.run()`，没有同等的本地运行期限；父进程先 `await _update(RUNNING)`，之后才开启 600s timeout。也就是说，SDK 已经开始，计时器却可能尚未启动；启动后还可能被父 loop 的同步工作延迟触发。目前没有这一次的 RUNNING 落盘结束、timer 实际起点和 event-loop-lag 记录，不能唯一分配这多出的 33.9s。第二次 38.939s 成功还复用了同一 Thread 的已有研究/上下文和缓存，不能用它证明第一次 633s 全是无效编排。

### 4.2 INTC Quantum：模型请求合计约 27s，Case 却达 66分43秒

Case：`case_99825ff33aa544da9a074aad937c9995`，标题 Intel Quantum Acquisition Might Not Be Enough to Make It a Powerhouse -- Barrons.com。

- 15:00:54.698 接纳；16:07:12.135 才出现第一条保存的模型 started，前段 **3,977.44s**。
- 最终四个 W1/W2 请求分别约 5.75、5.93、9.59、5.54s；16:07:38.278 完成归档。没有交易 W3，不能用“W3 研究复杂”解释。
- 不能把 3,977s 全部归因于没有线程：日志显示 15:35:45 已进入 W1/W2 lane，queue_wait 仅 **2/23ms**；两 lane 在约 39—41s 后失败，随后重入。这段基础设施失败没有形成一个对应的模型失败 attempt。

这说明“最终前端模型秒数很短”与“程序已经在处理却反复无法走到下一次模型/提交”可以同时成立。扩充热 lane 线程池不会消除 SQLite 写失败。

### 4.3 INTC TSMC CoWoS：成功 R1 与下一 round 之间相隔半小时

Case：`case_1c5dd90699974bb0b536f751532adf17`，标题 TSMC reportedly weighs 20% CoWoS price increase as Nvidia Blackwell and Rubin chips put pressure on packaging capacity。

15:00:43.714 接纳，15:38:58.682 发出并成功完成 W1 R1；下一条 W2 R1 到 **16:13:09.727**，W1 R2 到 **16:13:10.026**。全部已记录模型请求的并集只有约 **52.24s**，但首次请求至最后响应之间有 **2,046.95s** 未被这些请求覆盖；Case 共 **4,486.77s（74分46.77秒）**。

日志给出至少两轮 lane 基础设施失败，最终保存的 hot_path_latency 仅约 61.60s，对应后面一轮接回成功的热路径。**业务 Case 总等待、一个成功重入的 hot_path、单次付费请求，本来就是三个口径；现有界面缺少使它们闭合的恢复区间。**

## 5. 结构性机制与证据等级

### 5.1 共享 writer 被“准备工作”占住，争用升级为续租与恢复事故

确定的代码路径：

- `RuntimeJournal.claim()` 在 `BEGIN IMMEDIATE` 后调用 `prepare_inputs`、编码 inputs，再取整行 task。新消息冻结可能需要读其他库和文件，这些工作与所有 runtime writer 共享锁。
- journal 默认 row_factory 会自动展开 native-file 内容；事务内 `SELECT *` 可以隐含读文件和 JSON 解码。`fence()` 不需要任务正文，却仍取整行。续租控制不该依赖读取整份业务输入。
- 多处 `with journal.transaction()` 使用默认 `write=True`，实际只读也先争抢 writer。`w3_execution.trade()` 读 global_snapshot 后再写快照就是需要明确拆分的路径之一。
- 多种内容/状态共用 runtime.sqlite3。Case.frozen_inputs、round_inputs、worker request/receipt 的大内容虽已部分外置，普通 row/Case DTO 读写仍会触发展开、完整序列化、重新编码及校验。内容寻址避免重复文件，不会自动消除重复 CPU/I/O。
- `prepare_case_inputs`、`fence`、Case 输入与完整结果的读取路径，都可能持有应用级 `_lock`，或在 writer 已占用后继续做不必要的内容工作。

现场慢事务中**聚合等待毫秒数占聚合记录耗时约 90.9%**，不是墙钟的 90.9%，不能相加并发事务来算 Case 时延。与此同时，有长达 160s 的事务内区间，说明仅把 busy_timeout 从 30s 调小并不解决持锁源。

日志多数 operation 仍叫 `transaction`，无法把每一条 160s 区间唯一对应到具体调用点、进程中的内容读取或 commit 子阶段。本报告确认的是架构允许和现场发生长 writer 占用，**不虚构“唯一就是 claim.freeze 持锁了那 160s”**。本地已用真实 `RuntimeJournal.claim` 复现：冻结回调未结束时，另一短写被全局 writer 拒绝。

### 5.2 异步外壳没有隔离同步阻塞

Scheduler 的 W3 async loop 上存在同步 `_journal.get/set`、repository.save_case、request/receipt 记录等调用；`on_update=observe_job` 又同步保存整个 Case。这些操作发生在其他 coroutine 等待 Worker HTTP、准备或接回结果的同一个 loop 上。

Worker 的 `_dispatch_attempt`、`_schedule`、`_emit` 等仍直接执行 SQLite 元数据/队列读写。Research MCP 已把主要 store.call 放在线程中，但 market_view 分支仍有同步 view/证据文件工作；因此不能因为“MCP 读取已异步”就认为 Worker loop 已无阻塞源。

MCP 本身还存在可确认的读取放大：`context()` 对每次调用展开所有 snapshot_refs，`objects()` 加载全部 component；`catalog` 即使只返回元数据，也对每个 body 执行 compact 后丢弃。固定 16 个 component 的缓存还会被多 ticker、多冻结版本互相替换。私有检查调用真实 catalog 代码：返回 41B 的对象索引，仍先编码 1,000,011B 的业务正文。**这证明轻读路径做了不必要工作，不证明 Corning 的 15s 全部花在这一步。**应按请求定位内容，而不是扩大常驻缓存。

本地用临时 SQLite 复现同类模式：设 30ms 的 async timeout，loop 内同步等写锁，实际约 265ms 后才能抛超时。上一轮同任务 timeout 的正确性保留，问题是**它无法抢占一个正在执行的同步调用**。本轮不把所有未归因 SDK 秒数都强行归为这一机制。

### 5.3 活性证明、入站资格、已接纳任务的推进被绑成一个开关

`admit_runtime_revisions()` 先检查 Bus ACK 60s 新鲜度；`V2Scheduler._run_admitted_once()` 只 tick 返回的 admitted 集合。`RuntimeCoordinator.tick()` 才会 wake_effects、回收任务、准备公共资产及其他推进。因此上游暂时失活后，下面已经拥有冻结输入和远端 job 的任务也停止被接回。

现场 9 个业务控制仍 `analysis_allowed=true`，Bus ticker state 为 running，Worker dispatcher ready、无 cold/helper 锁住；Scheduler 非阻塞栈显示 main 在普通循环等待，热 lane 线程池大多空闲。它不是上一轮 cold permit 遗留，也不是整个 Scheduler 线程卡在一个 Fork 中。**空转与 stale ACK 条件吻合，且用原 admission 与 `_run_admitted_once` 函数已在内存对象中复现。**

Bus 仍每秒打印通用 `bus runtime lock contention`，但缺少异常类型/SQLite 扩展码/数据库/调用位置；当前日志不足以唯一定位最初使 Bus ACK 停止的那次失败。Bus 的 activation admission 在 run_once 的业务 StorageLane 里、心跳生命周期又依附本轮成功进入，进一步放大风险。修复必须保证 **上游 heartbeat 故障只体现来源健康与尚未验证的新版本能力，不能关闭已安装版本和已有合法任务的恢复泵**；并补齐 Bus 具体失败点，不能把“未知首条 SQL”说成已唯一确认。

### 5.4 执行所有权断层与重复恢复

| 边界 | 当前缺口 | 后果 |
| --- | --- | --- |
| task → hot ThreadPoolExecutor | 普通 submit 不带 ContextVar，也没有热 lane Future 的统一取消/接管 | 父任务可能已失去租约，子 lane 仍继续；不能假定其所有写入都受原 task fence 保护 |
| 同步 W3 wrapper → async coroutine | `_wait_v4` 的 Future.result 超时不会自动取消 coroutine，也无完整持久接管合同 | 调用方开始恢复时，旧 coroutine 可能仍做 HTTP/回执写入；只取消等待也无法证明远端模型未启动 |
| Effect 租约 → durable Worker job | effect 是 120s 租约，job 可运行 600s；续租争用时 stale owner 结束并不等于 job 结束 | 旧 owner 写入被拒、新 owner 接回同一 job；如果按普通失败再 mint 新 generation，会增加付费尝试和总预算 |
| provider 返回 → W1/W2 turn 落库 | 成功响应未形成独立完成凭证，后续存储失败可回到 Case/lane 重入 | 已成功子任务可能无法被后续重入复用；前端又看不到这段存储失败 |
| failed durable job → Runtime 重试 | `ReceiptWorker` 会在确认 failed 后生成新 dispatch；上层 Effect 还有自己的多次重试 | 分层恢复可能放大总时延；必须区分真实终态后的合法新模型尝试与仅等待/接回。没有阶段总上界属于现有策略，不能直接判作 bug 或在本轮静默新增硬期限 |

额外检查发现 Effect 续租/保存有分离维护的 status、updated_at、JSON lease_until。恢复领取主要按 `updated_at <= now-120s`，而不是所有地方都用同一个条件；异常类又混用 RuntimeError 与 LeaseLost。不是已经证明发生误交易，但这是同一所有权链条的薄弱点，应一起统一。

### 5.5 状态、日志与发布证据仍不足以揭示故障

- `runtime_v2_turns` 只保存已经进入/完成请求记录的尝试；输入冻结、dispatch admission 或落盘失败不一定形成 ModelAttempt。
- `hot_path_latency_ms` 是某轮执行函数的局部计时，恢复后可能是后一次区间；不等于接纳以来总时延。
- W3 stage 的 `worker_job_id` 是最新结果来源；历史尝试须通过 durable receipt/job 身份合并，不能覆盖第一次失败。
- Worker 失败遥测 sdk_duration 为空，usage=0 也不证明 Agent 没做事；Corning native 是直接反例。
- 部分应用日志时间与 Docker 接收时间相差几十秒；诊断输出本身可能受到全局 logging 锁、stdout 或 I/O 压力放大。不能把日志接收时间当实际动作开始时间。
- 宿主机初查 load 约 7.7—9.8，16GB 内存可用约 2.7GB，2GB swap 几乎用满；I/O PSI some 约 14%、full 约 9.8%。这是压力证据，**不是仅凭一个快照就把整晚超时归因于内存不足**。read 事务曾出现 225s，也需要区分查询扫描、外置内容和调度/日志影响。

## 6. 最终修复架构：保留系统，重划控制边界

不新增消息队列、通用工作流引擎或分布式事务平台；继续使用现有 Runtime journal、Effect、Worker durable job、共享 research assets 与既有维护循环。**不改 W1/W2/W3 业务判断、Prompt/Skill、模型路由、原冻结数据或交易授权。**

### R0：先让已接纳业务持续推进，并明确不可用状态

主要文件：`runtime_scheduler/v2.py`、`runtime_scheduler/service.py`、`ticker_initialization/consumers.py`、`ticker_initialization/configuration.py`、`ticker_initialization/repository.py`、`message_bus_v2/scheduler.py`、`message_bus_v2/repository.py`。

1. `run_due_once` 将三个集合分开：当前允许新接纳的 ticker、持久化已有任务/Effect/job 需推进的 ticker、已显式暂停/移除的 ticker。每轮先跑已有工作的恢复/接回，再执行新接纳；一次最多按现有批量额度推进，不把积压全拉入内存。
2. 首次接纳/切换 activation 需要该版本真实安装的证明；已接纳版本收到合法 Stream 后，不再因 Bus heartbeat 过期阻止分析。已有 Case 按其持久化 activation、冻结输入、control epoch 与原执行身份恢复，**不回查最新 ACK 是否仍指向该旧 revision**。切换新版本不能重冻旧输入或使旧 job 失去接回入口。显式暂停/移除仍通过现有控制合同决定是否可继续研究/发布；不能为了恢复绕过控制。
3. 在既有 coordinator 中提供 `tick_existing(ticker)`，只执行任务/Future 回收、Effect 唤醒、原 job 接回/提交、到期状态收束，不消费新 Stream、不重新冻结输入。单 ticker 错误不得阻断其余 ticker。
4. `activation_worker_ack` 的主键为 **`(ticker, worker)`**，新 revision 会覆盖旧 revision；它只保存该 worker 的最新 revision 接纳状态与心跳，不能充当历史接纳账本。不新增历史 ACK 表。新增 `current_revision_admitted(ticker, revision, worker)` 时，须同时匹配当前 active revision 与 ACK 行的 revision，但不加心跳 TTL，用于当前版本的新入站；`revision_live` 另查 60s TTL，用于健康诊断。旧 Case 的恢复直接使用第 2 项的持久凭证，不能依赖这两个查询；已有 Runtime activation/policy-applied 记录按原身份核验，不用全局最新 ACK 替代。当前版本变化后，旧 ACK 不能授权新版本。
5. **先拆职责，再脱离长业务 lane；不能将 `_admit_bus()` 整体丢进多路线程。**该函数目前会检查 head、安装/回滚候选配置、start_ticker，并写 ACK，必须拆成以下执行边界：
   - **轻量心跳：**由既有进程维护循环定时触发，使用小字段读写；只为本进程已验证安装且仍为当前 active revision/config head 的身份续心跳，按 revision 条件更新，不在心跳路径安装配置、启动 ticker、构造 CandidateConfiguration 或运行 DDL。进程重启先核验既有安装记录及 head，再恢复该身份的心跳。未安装的新 revision 只展示进程存活/待接纳，不伪造 ACK；旧身份的心跳不得覆盖新 ACK。心跳独立于业务 run_once 成败，写入经所属数据库短写通道。
   - **单一配置控制 owner：**复用既有 Bus 控制维护循环，对安装/回滚/start 统一协调并合并同 revision 的重复工作；不新增多路完整 admission。锁外读取候选、解析及编译变更；锁内只做少量写入及 head/control epoch/预期版本 CAS，所有写连接显式采用短锁等待。真正业务配置变化仍走此 owner，不让纯心跳反复触发安装。配置前后涉及不同数据库时，不制造跨库原子性假设：Bus 侧短事务校验其配置 head/control mirror，写既有安装回执；写 ACK 前重查当前 activation，发现已变更则保留待核验/按原协议协调，不为被取代 revision 确认接纳。
   - **初始化边界：**在线打开已有 live/candidate/control Repository 明确 `initialize=False`；缺少候选或 schema 时报告待准备，不在热循环创建空候选。当前 Bus Repository 在已有非空文件上通常已自动跳过初始化，但 CandidateConfiguration 未显式限定模式，且 install 仍有 `CREATE TABLE IF NOT EXISTS`。将安装所需表的幂等建表纳入显式启动迁移/候选准备，运行态心跳、安装和接纳不再隐式 DDL。真正的新候选仍允许在专门准备阶段初始化，不能全局禁用必要建库。
   - **通道与错误：**控制 owner 的准备不占 Bus 短 writer；实际 Bus 写入仍进入该数据库已有短写 owner，控制库 ACK 进入控制库自己的通道。不通过裸 `to_thread` 丢失 WRITE_ADMISSION 后恢复 30s busy_timeout；逐 ticker catch 包含 error sink 自身失败。记录 operation、DB 名称、sqlite_errorcode/sqlite_errorname 和原异常类型，内存保留 last_error，不输出凭据或消息正文。
6. Scheduler readiness 增加 last_successful_tick、existing_work_last_progress、admission_degraded_reason、oldest_due/oldest_unreconciled。容器存活仍是 liveness，不用一次 ACK 抖动触发整机重启。

**验收：**9 ticker Bus ACK 同时过期，有一个远端 job 刚成功时，该 job 仍能被原 Case 接回；同已安装版本的合法 Stream 仍可处理，切换到尚未真实 ACK 的新版本则被阻止。将 ACK 从 R1 覆盖为 R2 后，R1 Case 仍按原冻结输入接回，R2 入站按 R2 的真实安装证明判断。并发触发心跳/安装及安装中切换 active revision 时，只有一个配置控制 owner，不出现旧心跳覆盖新 ACK、过期配置被确认、30s 默认锁等待或热路径 DDL；缺候选时不创建空库。恢复泵无需新消息唤醒，暂停/移除约束不被破坏。

### R1：短事务是主修复，先拆持锁内容，再治理写等待

主要文件：`journal.py`、`repository.py`、`coordinator.py`、`message_content.py`、`w3_execution.py`、`w3_threads.py`、`worker_receipts.py`。

1. **claim 不在 writer 中冻结正文。**先用短事务领取原 task/owner/token；在锁外调用 prepare_inputs，按原消息身份和 revision 生成不可变冻结内容；最后用同一 owner/token 和预期 inputs hash 做 CAS 保存冻结引用。只有冻结发布成功才可发起模型。恢复复用原冻结引用；并发发布用 existing winner 校验，不刷新原 cutoff。
2. `fence/renew/claim` 只 SELECT 所需标量，禁止整行 DTO/自动 NativeContent 展开。新增 `task_control_row`/`effect_control_row` 明确列名，return 元数据后再在锁外加载业务正文。
3. 所有 journal.transaction 调用逐一标注稳定 operation 名。纯读明确 `write=False`；读内容/编译/序列化/生成文件移到事务外。真正提交时只做 version/owner/control 校验与小规模 INSERT/UPDATE/CAS。
4. 大内容继续用现有 NativeContent/研究资产版本，不设计新资产系统。给冻结 Case 输入、round 输入、Worker 请求/结果使用固定引用；hot state 更新不展开再重编码整个 Frozen Case。兼容现有完整 RuntimeCase 读取接口，按需组装，控制路径用瘦 DTO。
5. `observe_job` 改为更新原 job 关联及执行状态小行，不重复保存包含全部 frozen_inputs 的 Case。`worker_invocations/receipts` 保存 immutable job/attempt 引用与结果摘要，原完整凭证仍可读取。
6. **复用短写通道模式，不复用 Bus 的同一个队列。**通道按 `(进程, 规范化数据库路径)` 划分实例，Runtime、Bus、采集账本、Worker job 库及控制库互相隔离；同一库的控制续租/交易提交可优先，普通诊断可合并。`StorageLane` 当前只在一个实例内按事件循环分 lane，并不自动按数据库隔离，也不能宣称跨 loop 已单 writer。每个数据库在进程内由一个明确的 owner loop 管写，跨 loop 请求经安全投递到该 owner，不跨 loop 共享 asyncio 锁/信号量；允许共享辅助实现，不引入跨进程通用队列。**不是再加覆盖文件准备/网络调用的大锁。**多进程仍用 SQLite 短事务/CAS 协调，不承诺跨进程优先级。
7. 进入写事务的单次锁等待建议 50—100ms，回退在 writer 之外按原身份延后；不让几十个线程各阻塞 30s。**自动重试单位只能是尚未提交的短事务，不能是完整业务 callback。**只有 BEGIN 前拒绝，或已确认回滚且没有外部副作用的短写可重新执行；提交结果不明先按原幂等记录核验。冻结文件发布、provider 返回、Worker 派发或任何部分提交已经发生后，恢复相应阶段，不能重新执行整个冻结/请求/派发函数。保留关键业务提交原有耐久性，不通过将交易 fsync 降级来获得数字改善。
8. 将 CASE 外层和 `_case` 的嵌套 heartbeat 合并成同一 lease 一份；续租 SQL 不读正文。正常 20s 间隔和原租约先保持，先修事务再决定是否调整，不靠无限延长租约掩盖占锁。
9. 线程 offload 等待被取消不代表物理 SQL/文件发布结束；必须让原 work 完成并观察结果，之后才释放 writer/owner/写槽。沿用当前 StorageLane `_execute` 的 shield + 等待物理完成原则，覆盖重复取消；可取消尚未开始的排队项，不能提前放走在途线程。超过诊断阈值时暴露 stuck owner 并停止对其重叠派发，不强制清零 ownership 来制造可用假象。此规则也适用于 R0 控制写和 R2 offload。

**验收：**在正文冻结人为慢 2s、NativeContent 冷读慢 2s 时，另一 ticker 的续租及已就绪结果提交仍正常；事务持有时间不包含这 2s。突发 20 个 Case 时不产生周期性 30s writer 阻塞与续租丢失。同一 loop 的不同库不共用队列，同一库跨 loop 不出现双 owner；取消物理写等待后下一 writer 只能在原物理操作结束后进入。冻结发布/provider 成功/Worker 已派发后注入 BUSY，只重试相应提交，不重复外部动作；提交结果不明先核验。

### R2：W3 与 Worker loop 只做异步控制，不执行阻塞业务 I/O

主要文件：`persistent_runtime_v2/w3.py`、`worker_receipts.py`、`codex_runtime/client.py`、`codex_worker/jobs.py`、`app.py`、`mcp/w3_research_server.py`。

1. W3 loop 上所有同步 DB/文件路径按语义拆开：纯读取走已有有界 I/O executor；写入走 R1 短写通道。不能只把整个大函数包进 to_thread 后继续持锁。
2. Worker `_schedule`、metadata/save/event/ACK 等控制 SQL 使用其所属 job 数据库的短写 owner 或小批 offload，不与 Runtime/Bus 写队列共用；队列查询只读索引字段，避免每 250ms 在 loop 上展开历史 payload。状态未变化不重写 safety/queue 等相同内容。线程取消仍遵守 R1 第 9 项。
3. Research MCP 的 market_view 分支也将文件、snapshot 编码和 evidence 发布移出 loop；网络/券商工作仍由现有后台行情准备承担。catalog 直接读已有/预生成的元数据，不 compact body；已知 read 按 kind/id/shell 选择对应 component 和必要 snapshot，不展开无关 D1/D2/历史状态。继续沿用 component 版本/hash 和 Unit 业务名，补一个小的 metadata/kind 索引即可，不为 Factor 建新索引、不扩大常驻缓存；原完整研究内容及 sources/raw/分页能力全部保留。只读工具不得被行情准备的长调用占住。
4. 保留单 Uvicorn worker、cold=1、既有池规模与读容量；不要通过增加 app-server/热进程数复制资源解决事件循环阻塞。
5. loop heartbeat 采用一个廉价 monotonic 定时器，记录 lag 的当前/最大值和阈值计数。默认只留标量，lag>阈值才输出短事件；不常开全量工具正文探针。
6. **单次真实模型执行的期限由执行它的 Capsule child 就地执行。**在 sdk.turn_start 派发时记录单调起点，按本次已冻结 request.timeout_seconds 计算同一个 deadline 并包住 handle.run，不能等父进程写 RUNNING 后才开表；常见原始请求为 600s，合法恢复请求若已有其他配置则沿用，不能一律钳回 600s。父进程监督同一 deadline，加原有 15s 物理收尾，不另获一份模型执行时间。超时路径有界 interrupt，但必须区分“SDK 在期限内真实完成”和“结果尚未完成”：前者即使回执落盘/通知/接收晚到，也按真实完成事实接回，不能拿持久化或收包时间判模型超时；后者记录明确超时/中断。父子记录 dispatch、SDK completed/status、started 发送/接收、RUNNING 落盘结束、timeout_due/fired、receipt_written/received；完成事实关联原 job/attempt/thread/turn。父监督到期先核验原执行及完成事实，未明确收束时保持原 owner 的待核验状态，不能先将本地等待超时当成最终模型失败再派新 job。超时回执写入前先协调已完成结果，晚到的真实 completed receipt 仍走原结果接回；仅 final_answer 后 aborted 不算完成，缺乏完成时点证据则明确待核验，不能编造期限内完成。对同一在途执行，任何 HTTP/SQL/owner 恢复都不得重开计时。

**验收：**注入 2s 存储慢操作时，其他 job 的事件收取/HTTP 状态/轻量定时器不一起冻结；timeout 能及时触发，后续有限清理仍持有原物理 owner。

### R3：一次执行有一个可恢复 owner，超时不再制造隐形后台工作

主要文件：`w3.py`、`coordinator.py`、`service.py`、`fencing.py`、`repository.py`、`worker_receipts.py`、`codex_runtime/client.py`。

1. 用既有 case/phase/operation/dispatch generation 作为 owner，不新增第二套 workflow ID。`_wait_v4` 必须登记 Future 与原 stage 的关联；等待到期返回/记录明确 PENDING_EXTERNAL，后续只查询/接回原 Future 或 job。
2. 若尚未派发，取消该原 coroutine 并等待有限收尾；若派发结果不明，先按原 idempotency_key/job ID 查询。不允许一次 Future timeout 就创建新 job。取消等待不等于取消远端模型。
3. 热 lane submit 使用每次独立的 `copy_context().run` 传播 lease/control epoch；父任务结束时管理两份 Future，不因一边失败就遗忘另一边。已发出的 provider 请求由 attempt 回执持有，不能丢弃付费完成结果。
4. Effect claim/renew/save 统一使用一份 authoritative lease 条件。续租和状态提交采用 token CAS；`updated_at` 保持观察字段，不再与 lease_until 形成两套到期判断。统一抛 LeaseLost，由既有 catch 正常退出，避免旧 owner 反复尝试写 failed。
5. `ReceiptWorker` 的新 dispatch generation 只用于已证实终态且需要的模型/输出修复；SQL、RPC 等待和结果接回不是新模型重试理由。先按原 job 结果判断恢复阶段，再决定能否重试模型。
6. **修执行计时，不在本轮新增阶段总预算。**持久化/补齐原 job/attempt 的 dispatch/SDK start/completed、该次 timeout_seconds 与 deadline、实际恢复代数；同一在途 job 的 RPC/SQL/lease 接管继续使用原 deadline，不以恢复查询或重新等待为起点。准备超时仍按原 prepare operation 的既有配置与语义执行，不改成 Case 年龄；确认终态后是否可进行真实新模型尝试，沿用既有 recover_timeouts、输出修复和 attempt 规则，包括 `_recovery_timeout` 对合法恢复请求的现行处理。**不新增 accepted_at+795s 硬期限，不让多个合法模型尝试共享 600s，不因排队/回执年龄用尽一个新总额度而禁止原可恢复 Case。**总等待/累计模型时间可以记录和告警，但不能据此新增 BUDGET_EXHAUSTED 业务终态。若今后要设置阶段硬预算或改变超时重试政策，须作为明确的策略变更另行决定，不属于本轮默认修复。
7. SDK 已完成但调用方超时，使用原 durable completed receipt 恢复；仅 native final_answer、随后 aborted 的场景不能自动当成功交易。明确 FAILED、PENDING_EXTERNAL、RESULT_READY、COMMITTED 的区别。

**验收：**Future 到期、SQL 暂不可写、effect owner 被接管、Worker HTTP 响应丢失四种故障交错下，仍只有一份物理/付费在途执行；旧 owner 不能提交业务；同一执行的 deadline 和恢复代数不因接回重置。再覆盖：模型期限前完成、回执落盘/接收在期限后仍按原完成事实接回；SDK 未完成才按原本次期限处理；确认失败后的合法新尝试沿用既有配置。排队超过 795s 本身不构成新增禁止条件，不出现多次合法尝试共享 600s 的隐式策略。

### R4：W1/W2 补齐“provider 已完成、业务未提交”的接回能力

主要文件：`service.py::_run_round/_record_success_turn`、`journal.py`、`repository.py`；复用现有 runtime artifact/NativeContent。

1. 发请求前持久化 attempt 的 dispatch ID、case/lane/round/ordinal、冻结输入 hash、owner generation；已运行/已完成 attempt 必须可被查询。
2. provider 返回后，先将其不可变结果/usage/原 response ID 发布为小完成回执，再尝试 turn 索引与业务状态落库。使用现有原子文件和内容寻址，不另造消息系统；写完回执后 SQL 故障仅重试落库。
3. 回执和业务提交分开权限：付费结果事实可按原 attempt 保存，业务发布仍受当前 lease/control fence。新 owner 可以采用经身份核验的原回执；不能为了加 fence 而把已付费结果丢掉。
4. 对失联而无法确认是否已发出的请求，保留 UNKNOWN/待核验与原 request identity，不以租约到期直接认为“没有调用”。HTTP provider 若不支持幂等查询，无法承诺绝对零重复付费；明确边界，不能假装本地原子性覆盖远端 API。
5. 本轮不改变模型输出、round 结构或上下文语义。不要借此跳过 W2、减少事实或修改 Gold。

**验收：**provider 成功一次、turn 索引连续两次写失败后，恢复仍只有一次 provider 调用，最终按同一 attempt 展示；原输入/结果 hash 一致。

### R5：统一时间台账与可行动的读模型

主要文件：Runtime schema/journal/Effect、`w3_timing.py`、`v2_read/runtime.py`、`graph.py`、Case DTO 与 `frontend/v2/src/pages/runtime-case.tsx`。

1. 保留当前 invocation 用时，明确标为“调用用时”；增加 Case 接纳→决定、已等待、排队/准备/重试/提交、后续运营的独立说明。W1/W2 并行以区间并集或 DAG 关键路径展示，不再引导相加。
2. 记录 admitted、task_claimed、inputs_ready、lane_started、round_dispatch_started、provider_started/completed、receipt_persisted、stage_accepted、job_created、remote_turn_started/completed、result_received、decision_committed。优先扩展已有小元数据/trace，不保存每次工具正文。
3. 失败、取消、partial、重试按原 attempt 保留；stage 最新结果引用不能覆盖历史。unknown SDK 标为 unavailable/partial，不显示 0s；“无结果”不自动等于健康 PENDING。
4. 当 Case 总时间与已覆盖阶段不闭合时，显示“未归因等待 x 秒”与当前边界，不改写为模型推理。数据缺口不能用前后分位差假填。
5. 事务探针只记 operation、case/phase/owner、DB、begin_wait/hold/commit、busy code；给 claim.freeze、round.receipt、effect.renew、stage.commit 命名。超过阈值才限频输出，详细内容保留短期、可关闭。
6. 业务审计保持耐久；高频诊断日志使用有界非阻塞队列或已有异步诊断写入，队列满只计数/丢弃可选诊断，不能阻塞交易或静默丢业务结果。

### R6：发布与存量接回必须覆盖整个链条

1. 发布单元至少包含已修改的 Scheduler、Worker、Bus admission 相关组件；记录每个组件实际 source digest/image/SDK 版本。更正 build revision 注入，避免再用陈旧 `d5f43fb…` 标识新源码。
2. 先只读清单：Case/phase、task/effect owner、远端 job/turn、是否已提交决定、是否已有正式意图。已提交的永不重新研判/重复发布。
3. 原 job succeeded → 接回/提交；原 job running → 原身份等待/观察；确定未派发 → 原 task 继续；已 failed → 按原请求配置、既有恢复策略与实际失败阶段处理，不清空全体重试计数，不用新增阶段年龄预算截断存量。已真实完成但回执晚到的按完成时点核验，无法确认的保留显式原因。
4. 不做标题级或批量“重新生成 Case”。不为了清理 UI 将 RUNNING 全改 FAILED。现有 UNKNOWN child 与原 Fork 协议仍然保留。

## 7. 开发顺序、范围与验收门槛

| 批次 | 可直接执行的交付 | 必须同批覆盖 |
| --- | --- | --- |
| 1 | R0 恢复泵与 ACK 解耦、心跳/配置安装拆分、R1 claim/fence 的纯元数据与锁外冻结、稳定 operation 标签 | ACK 过期及 R1→R2 覆盖不停止旧任务；配置单 owner、运行态无 DDL；冻结慢不占 writer |
| 2 | R1 按库短写隔离及剩余长事务/重内容清洗、R2 loop I/O 隔离与单次 SDK 计时 | 批量 20 Case 的存储延迟注入；取消不提前释放物理 owner；已发生外部动作不重跑 callback |
| 3 | R3 原 Future/job 所有权及统一租约、R4 provider 完成回执 | SQL/网络/owner 丢失的组合故障；接回不新派模型；真实完成与回执时间分开，不新增阶段硬预算 |
| 4 | R5 DTO/前端时间闭合、R6 整链发布与存量接回 | 成功/失败/超时/partial/重试/运营分母均可复核；发布前后实际源码一致 |

批次只是开发组织，不是允许只发布一个局部补丁后宣布 P0 已解决。R0 可先恢复推进，完整验收需其余根因同时闭合。

建议生产验收门槛：

- 单条正常控制写事务 hold p95 <20ms、p99 <100ms；内容准备注入延迟不进入 hold。分开上报 begin_wait、hold、commit，验证不是仅缩短等待而持锁仍几分钟。
- 自然 W3 **Effect→stage→job→SDK→commit** 全链计时；非 SDK p95 回到 <5s 的目标，失败与第一次初始化单列且不删样本。先记录窗口/负载/所有尝试，不用 isolated mock 的数值冒充生产。
- 观测期间无“Bus stale 导致已有任务不推进”、无因单条同步 I/O 使全部 W3 loop 停顿、无同 Case 因接回失败产生重复在途模型任务。
- 单次 SDK 执行按本次冻结 timeout_seconds 准时监督，超出只允许已声明的有限收尾；同一在途执行不因接回重置预算。模型在期限内真实 completed、但回执写入或接收晚到，仍按原完成事实接回；对 final_answer+aborted 不伪造成功。确认终态后的合法模型恢复沿用已有政策，不设置 795s 阶段硬门槛或跨尝试共享 600s。
- 当前纯 SDK p95 仍可达约 256s，**仅架构修复不能保证总 p95 100s**。如果保留这一最终目标，需在架构恢复后再针对真实 Agent 业务步骤优化；本轮不偷改 Prompt/Skill 来制造达标。
- 用自然流量验证至少一个正常峰值时段及一次组件重连/接回；不在生产注入 kill/取消风暴，不用历史 Web 重放证明实时性能。

本轮不建议：增加全局线程/SDK 进程数、把 600s 改成更长、全量搬库/更换数据库、增加通用队列系统、取消冻结/幂等/交易 fence、把失败样本从统计移除。资源紧张时先减少重复展开、短写和不可选诊断，不扩大常驻缓存。

## 8. 已完成验证与尚缺的证据

本轮在本地私有对象/临时目录完成六个机制检查，全部通过预期故障断言：

1. 真实 RuntimeJournal.claim 在冻结回调期间持有 writer，其他短写报 BUSY。
2. 同步 SQLite 等锁使 30ms 的 async timeout 约 265ms 才生效。
3. 真实 `_wait_v4` 的等待期限压缩后，外层报 TimeoutError，原 coroutine 仍继续完成。
4. 原 admission + V2Scheduler 方法在 Bus ACK 过期时完全跳过 ticker tick，即使本地 analysis_allowed=true。
5. 原热 lane 使用的普通 ThreadPoolExecutor.submit 不传播 lease ContextVar。
6. 真实 ResearchMcp store 的 catalog 返回小索引前仍编码了约 1MB 无需返回的业务正文。

这些是机制验证，不是已经实现新方案或生产性能验收。没有写生产业务库，也没有触发真实模型。

仍需在实施中通过轻量标签补齐：每条长事务的具体 caller/holder、Corning 600s timer 迟到的精确 loop 阻塞点、外层 Code Mode 调用与 MCP 服务内部的独立时间、Bus 首次 ACK 停刷时抛出锁错误的确切 SQL。**目前足以确认结构性缺陷和量化主耗时，但不足以把每一秒都唯一归给一个函数。**修复方案要求覆盖这些边界，并在生产前后证明因果链收束，而不是把未知部分命名为“推理慢”或“服务器配置不够”。

## 9. 证据位置与复算

证据目录：`eval/runtime_p0_latency_20261028/`。

| 文件 | 用途 |
| --- | --- |
| `inventory.json`、`cohort.json` | 数据库 schema、Case/turn/Effect/stage/job、slot/operation、实际 Worker 源码 |
| `scheduler-state.json`、`bus-state.json` | 实际 Scheduler 源码、activation/ACK、控制状态、Bus state/config head |
| `final-snapshot.json` | 北京时间 02:13 的最终小字段状态复核，保留仍停滞的证据及全局任务口径 |
| `details.json` | 精读样本 boundary timing、native 元数据/工具与 token 时间线、当前 resources；不含隐藏思考文本 |
| `case_metrics.json`、`metrics.json` | 915 Case 逐条指标、分组及缺失标记；失败 SDK 未填 0 |
| `scheduler-incident.log`、`log-metrics.json` | 完整事故日志窗口和慢事务/热 lane 提取；warning 分母不是全部事务 |
| `*-stacks-1.txt`、`bus-profile.json` | 使用现有 py-spy 做非阻塞栈读取/5s 采样，未发送业务控制信号 |
| `reproduction.json`、`reproduce_local.py` | 六项私有机制复现及结果 |
| `analyze_local.py`、`*_remote.py` | 可重跑读取与复算脚本；remote 脚本均为只读入口 |

复算使用项目 Python 执行 `eval/runtime_p0_latency_20261028/analyze_local.py`；机制复现执行同目录 `reproduce_local.py`。跨组件取数有先后，原始 at 与覆盖范围保留，不把这些读数包装为单一全局原子快照。
