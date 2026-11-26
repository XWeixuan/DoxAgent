# Runtime 非 SDK 延时：剩余关键链根因与下一阶段修复方案

日期：2026-11-18。范围：生产只读实测、当前代码审查、离线机制测试；**本轮未修改业务实现、配置、Prompt、Schema，未提交、发布或重启服务**。

## 1. 结论与优先顺序

上一轮修复有实际收益，但没有覆盖“预热结果是否直接可用”和“其他进程占据业务 writer”两类问题。当前并非所有 Case 都持续慢，也不是双任务耗时重复相加造成的假象。

本轮最值得先改的四件事是：

1. **消除 READY→Trade 的确定性配置错配。** 私有准备默认 `max_subagents=2`，实际 Trade 明确传 `0`，最终生成的 `agents.max_threads` 分别是 `2/1`。即使两边均禁用子 Agent，resident 指纹仍不相等，每个新 Trade 都会再执行 `thread_resume`。离线使用实际 Runtime 适配器复现；对齐准备参数后额外 Resume 消失。抽查的 6 个生产 Trade Job 均有这次 Resume。
2. **清理 Runtime writer 的后台占用，而非继续调整 W3 写入重试。** 两个锁采样窗口均实际捕获 `v2-transport-maintenance` 持有 Runtime writer。该服务的 Native inventory、引用回填及部分清理仍在写事务内进行目录遍历、JSON 处理、manifest 读取或文件操作。上一轮修的是 Worker 派生物 GC，不能认为其他数据库中的 GC 也已解决。
3. **去掉 Worker 不使用的远端插件市场工作。** 文件级 eBPF 两次捕获到 Native Codex 写入约 **77.86MB** 的 `cache/remote_plugin_catalog/.tmp…`。这不是 Worker 业务 WAL。当前锁定的上游源码存在启动任务强制刷新目录并完整序列化写盘的路径。应在确认 W3 工具边界后关闭 W3 专用 client 的插件市场发现；不能只关闭推荐插件，也不能通过删缓存处理。
4. **缩短成功结果保存链，先处理已测出的回执与分支保存成本。** CEG Operations 成功结果已到 Scheduler 后，仍花 **2.576 秒** 才保存回执；MU Trade 最后一个 owner 花 **3.799 秒**，其中存在明确的 SQLite 重试和另一个约 **1.430 秒** 未闭合调度间隙。真正业务提交仅约 **146ms**。继续优化 Future 唤醒本身，无法消除这些时间。

READY 的主要问题是**突发需求遇到构建速度波动**，本轮没有找到“资源充足却长期漏补货”的证据。先修上面四项、测低成本冷路径，再决定是否增加库存或后台构建者；不直接把 4 调大，也不新增常驻通用池。

### 最新自然样本

统一按上一轮口径：并行模型区间取并集；W1R3 辅助推理不掩盖 W3 内部准备；Native Fork、SDK 初始化、控制 RPC、内部队列、锁等待及重试均保留在内部耗时中。

| 样本 | Trade P50 / P95 | Operations P50 / P95 | W3 整体 P50 / P95 | 完整 Runtime P50 / P95 |
|---|---:|---:|---:|---:|
| 上轮跟进的 8 个完整 Case | 7.48 / 21.65s | 3.06 / 7.56s | 13.28 / 24.63s | 15.72 / 26.37s |
| 本轮扩大至 34 个完整 Case | **4.51 / 19.98s** | **1.95 / 5.87s** | **6.12 / 21.50s** | **8.42 / 24.81s** |

本轮队列纳入 35 个 Case，34 个已完整闭合，1 个尚未完成，不按零计入。完整样本中 **11/34 超过 10 秒**。采样窗口是 Scheduler 本次发布后至 **2026-11-17 19:32:54 UTC**；额外资源诊断延续至 19:47 UTC 后。

**常态中位数改善，不等于 P95 达标。34 例仍不足以证明稳定生产 P95，未完成样本也存在右删失偏差。** 本报告不宣称已达到目标。

## 2. 实际版本、证据与测量边界

- Worker：`d227e7629826d065201df476705ce3d87dfb6bc2`，18:29:59 UTC 启动。
- Scheduler：`1f961d5a4993ccf8dd2b33b00515e98a01323823`，18:45:01 UTC 启动，包含 Task 容量释放后的即时续派修补。
- Projector、Control、Maintenance、API 为 `d227e7629`；观察前后六服务无 restart/OOM。
- Transport Maintenance 镜像标签为 `486931723…`，早于上述发布；**实际 `source_native_gc.py`、`source_lifecycle.py` 源码在统一换行符后与本地当前文件 SHA256 相同**。不能仅凭旧标签断言它漏部署了不同实现。
- 本地审查基线 main 为 `d2777c32a`；后续其他会话的 Site Strategy 工作区变更未纳入本轮分析，也未修改或覆盖。

生产采集只做短索引查询、标量元数据、180 秒 `/proc` 差分、60/30 秒锁采样，以及有 PID/cgroup 过滤、15–50 秒自动结束的文件写入探针。没有发起真实模型 turn，没有触碰交易或业务数据。离线测试仅在本地临时目录执行。

限制：

1. 早期 CEG 15 秒 Boot 发生时，没有保存逐线程调度、缺页和文件级等待记录；本轮不能把后来观测到的原因硬套到当时。
2. Kernel `task_delayacct=0`；`blkio_ticks=0` **不表示没有 I/O 等待**。
3. `schedstat` 是每线程累计值；多线程等待可重叠，不能把所有线程之和当作关键链墙钟。
4. `/proc/<pid>/io` 包括已等待回收子进程的 I/O，父子进程不能相加，也不能把父进程增量全部当成它自身的写入。[Linux man-pages](https://man7.org/linux/man-pages/man5/proc_pid_io.5.html)
5. 部分日志只保留前 48 个详细 span，但 totals 和 milestones 完整；未被 span 覆盖的间隙保留为未知。

## 3. READY：并非长期补不上，而是突发覆盖不足与构建波动

### 3.1 完整库存时间线

从 Worker 代际起点分页读取槽位，读到早于本次启动的记录后停止。当前代际 **114 个槽位**，全部属于一个实际使用的 scope：

| 状态 / 领取 | 数量 |
|---|---:|
| READY | 4 |
| USED | 108 |
| BOUND | 2 |
| POOL_HIT / POOL_MISS | 71 / 39 |
| 本代际失败、RETIRING、resident reaped | 0 |

因此这批库存可以由 `created_at → ready_at → bound_at` 重建；不存在需要凭空推测的当代际回收缺口。READY 可供认领区间为 `[ready_at, bound_at)`；先绑定后构建完成的 MISS 不算公共库存。

排除 Worker 重启初期、只看当前 Scheduler 发布后的 **2,871.82 秒**：

- 70 次领取：**58 HIT、12 MISS，命中率 82.86%**。
- 4 份可领取库存维持 **2,589.29 秒，占 90.16%**。
- 零库存共 **55.43 秒，占 1.93%**，最长一次 **26.73 秒**。
- 实际领取及完成均约 **1.46 份/分钟**；这是含库存满时闲置的观察吞吐，不能当成构建者最大能力。
- 根据未绑定 BUILDING 区间，后台构建者至少有约 **280.23 秒、9.76%** 时间在补货。绑定后继续为实时 Case 工作不再算空闲库存构建；直接创建后极快绑定的分类存在 100ms 边界。
- 36 次库存未满、完成后继续补货的间隔：P50 **17ms**、P95 **52ms**、最大 **64ms**。

这组数据不支持“补货普遍等待 30 秒维护轮次”或“单构建者一直满负荷”。即时补货已经生效。后台主循环仍串行执行其他维护，但 `_schedule_fill()` 在其之前，构建完成也会即时触发；不能再次把它描述成之前的串行前置缺陷。

### 3.2 为什么短时间缺货造成很多 MISS

发布后观测到的最大领取突发：**1 秒 4 份、5 秒 8 份、10 秒 10 份、30 秒 12 份**。双任务每个 Case 消耗两份库存。

同期被 HIT 认领的已完成槽位，原始构建时长 P50/P95 为 **3.51/8.76 秒**；包含一份构建 **45.68 秒**、随后仍被成功 HIT 的槽位。12 个 MISS 槽位原始构建时长 P50 **8.52 秒**、最大 **18.27 秒**。构建慢会同时影响当次冷 Case 和后续库存恢复。

当前 `_fill()` 仅允许一份**尚未绑定的 BUILDING**，直到完整构建结束才补下一份。它约束的是物化、Boot、Fork、MCP 和发布整条链，不只是物理进程启动。已经绑定的实时准备可以并行执行，不受这个后台库存限制；这一保障必须保留。

同一份存货在需求平静时能保持很久，在数秒突发内却会被耗尽。因此“90% 时间满库存”和“17% 领取 MISS”可以同时成立。

### 3.3 库存与构建者敏感性回放

用本次 70 个实际领取时刻，假设初始库存已满、构建时间固定、构建间不竞争 CPU/I/O，做离线供需回放：

| 初始/目标库存 | 后台构建者 | 每份 3.51s 时的 MISS | 每份 8.76s 时的 MISS |
|---|---:|---:|---:|
| 4 | 1 | 10 | 13 |
| 4 | 2 | 8 | 12 |
| 6 | 1 | 5 | 6 |
| 6 | 2 | 4 | 6 |
| 8 | 1 | 1 | 2 |

这是理想化敏感性分析，**不是生产反事实预测，也没有证明应该保留 8 份**。它说明：只把后台构建者翻倍，对这种集中突发收益有限；消除冷路径浪费比先扩大构建并发更合理。

上一轮 4 份空闲私有子树实测约 **1.34GiB PSS**。若按相同配置粗估，6 份可能增加约 **0.67GiB**，8 份约 **1.34GiB**；这不是峰值保证，还需计入双任务活跃树和工具进程。两份构建流水线也会短时多占一份构建中的子树和 CPU。

**建议先保持 4/单构建者；修掉准备与结果链的浪费后，用新构建耗时回放，再决定 4→6 或有限流水线。MISS 不可人为排队等待库存。**

## 4. 冷准备：确认的缺陷、正常成本与未闭合间隙

### 4.1 READY 指纹错配：已证实、可最小修复

相关代码：

- `codex_worker/schema.py`：`WorkerRunRequest.max_subagents` 默认 `2`。
- `codex_worker/thread_lifecycle.py:_prepare_operation_observed`：构造准备请求未显式设置该字段。
- `codex_worker/w3_ready.py:build` 的恢复 Resume 构造同样使用默认值。
- `persistent_runtime_v2/w3.py`：实际 Trade/Operations 请求明确 `allow_subagents=False,max_subagents=0`。
- `codex_worker/sdk_runtime.py:_start`：总是生成 `agents.max_threads=max(1,max_subagents)`，并把整个 `sdk_config` 纳入 resident scope。

离线调用真实适配器、替换为无网络 Fake SDK，结果如下：

| 准备值 / 实际 Trade 值 | 配置差异 | SDK 调用 |
|---|---|---|
| 默认 2 / 0 | `agents.max_threads: 2 → 1` | Fork 后再次 Resume |
| 显式 0 / 0 | 无差异 | 只有 Fork，复用已准备 Thread handle |

该测试使用相同 run/cwd、模型、MCP 与 v6 版本，不通过删除 scope 校验绕过不一致。

生产抽取 MU、CEG 三个 Case 的 6 个 Trade Job 均记录 `sdk.thread_resume`，示例耗时约 **34–178ms**；另有正常阶段切换的 Operations Resume 达 **1.506 秒**。两者不能混为一谈。

**最小修改：**仅在 W3 私有准备和同身份恢复请求上显式设置 `allow_subagents=False,max_subagents=0`。不要改所有工作流的默认值，也不要忽略 scope 中的权限或配置差异。存量旧 resident 允许正常一次 Resume，不伪造它已经兼容。

Trade→Operations 会从 fast 切到 default，仍可能需要 Resume 以应用配置；不可为消掉 RPC 而让 Operations 继续 fast，或改动模型/推理参数。对剩余 Resume 应记录合法配置差异，再处理无效变化。

### 4.2 本轮实时捕获的完整准备

19:23:28–19:23:48 UTC 的 6 次完整操作，关联了槽位、operation、Capsule、Boot、Native Fork 与进程采样：

| 槽位后缀 | operation 总时长 | Boot 至 SDK_READY | Native Fork |
|---|---:|---:|---:|
| `…cd8917` | 3.398s | 1.553s | 1.427s |
| `…873b1` | 3.714s | 2.299s | 0.801s |
| `…771bf8` | 5.846s | 2.482s | 1.334s |
| `…ce241e` | 3.093s | 1.494s | 1.074s |
| `…37c19f` | 3.291s | 1.691s | 0.752s |
| `…5f81ec` | 3.449s | 2.223s | 0.778s |

这些列是包含关系与子区间，不能把 operation 再加到 Boot/Fork 上。Boot 前后的余量包含 admission、进程调度、协议与落库；其中 5.846s 的实例存在明显的 Boot 之外准备等待。

同窗 Python Boot CPU 主要约 **1.37–1.58 秒**，采到的主要缺页很少；Native 在准备窗口还执行约 **1.5–2.1 CPU 秒**，Python/Native 可重叠。Native 多线程 runnable 等待累计约 **2.2–4.8 线程秒**，证明调度竞争确实存在，但不是可直接相加的墙钟分解。

这些事实表明：

- Native Fork 并非固定 3–5 秒；当前相同版本能完成约 0.75–1.43 秒的 Fork。
- Boot 至 SDK_READY 也非固定 15 秒；高尾部仍需解释，不能将历史慢值视为业务必要成本。
- 不能仅凭 Python CPU≈1.8 秒就说剩余时间全是 SQLite：Native CPU、线程调度、文件页和控制 RPC 都必须分别看。

### 4.3 旧慢例能闭合到哪里

CEG `case_b580264cf8f0430289ae419420b7f330` 两个 MISS：

| 项目 | 分支一 | 分支二 |
|---|---:|---:|
| 绑定→最终槽位 READY | 31.981s | 32.963s |
| operation 创建→FORK_DISPATCHED | 18.883s | 16.917s |
| Boot wall / Python CPU | 15.120 / 1.822s | 14.295 / 1.776s |
| Fork 标记→operation READY | 11.706s | 15.183s |
| operation READY→槽位 READY | 0.787s | 0.901s |

分支二 operation 比绑定早约 38ms，是先创建后被认领，不能把该差额算成负等待或数据错。

这例发生在 18:37 UTC，早于 18:45 的 Scheduler 补修，但 Worker 已是本轮版本。现有记录足以确认大段真实内部耗时，却没有当时的 Native 进程与完整控制 span，**无法进一步把 11.7–15.2 秒严格拆为 Native 执行、父进程回包和数据库等待**。本轮未伪造闭合，也不把后来的插件写入直接归因给这例。

### 4.4 已删除与仍存在的轮询

- BOOTING 微阶段回调确实直接返回，不再同步落 operation；不能再次提“删掉 BOOTING checkpoint”。
- operation READY 与 registry 最终登记已经在同一事务。
- Case 领取已优先 join 现有构建 task，不再重复 100ms 领取/绑定写入。
- **仍存在两类 250ms 检查**：生命周期监视器读取槽位以识别后台准备被实时 Case 绑定；`W3ReadySlots.build` 等待 preparation 时周期读 operation。6 个实测操作各有约 13–31 次 `io.read`，部分来自这些检查。

后者可以在本地 preparation task 存在时直接等待它，结束后读一次最终状态；恢复时无本地 task 才查询原 operation。前者承担绑定后的期限与优先级升级，不能直接删除；优先用现有内存 owner 的绑定通知替代重复读，再保留恢复查询。不新增持久化中间状态。

当前正常样本这部分 SQL/排队多数是几十至数百毫秒，**不是已证实的十几秒主因**。

## 5. 成功结果消费与 Operations：长尾主要不在模型或 Future 通知

### 5.1 MU：完整 Runtime 26.374 秒如何组成

Case：`case_6976cf85ab864bb894637022ee1b292b`。

| 不重叠区间 / 业务边界 | 非 SDK 耗时 |
|---|---:|
| Case 创建→W3 路由，扣 W1/W2 关键模型区间 | 1.748s |
| W3 路由创建 18:51:33.248→首个 Trade SDK 发起 18:51:50.559 | 17.311s |
| 两个 Trade 模型区间并集之后→Trade owner 真正返回 | 4.335s |
| Trade owner 返回→Operations owner 返回，扣 Operations SDK | 2.980s |
| **完整 Runtime 非 SDK** | **26.374s** |

Trade 自身是 **21.646s**。按独立阶段定义计算的 Operations 是 **3.213s**；它从交易已生效时开始，与 Trade 尾部重叠约 **232ms**。所以不能直接把 `21.646 + 3.213` 再加到整体上。

交易前的两个 ready claim 各约 **12.008/11.173s**，大量重叠，只按关键链计一次。它们不是 W1/W2 模型慢的替代名称。

Trade 最后 owner：`bf8ac6cb98904f6b8a52871faed0a228`，总 **3.799s**：

- 分支保存经历 3 次失败写入尝试，然后重试成功；`io.write` 总执行约 **1.016s**、排队累计约 **562ms**，重试 sleep span 共约 **93ms**。这些有嵌套关系，不再与内部 journal span相加。
- 第三次写入 callback 结束相对位置约 **1.039s**，下一次 retry-delay span 到 **2.469s** 才开始，中间 **1.430s 未归因**。不能全部记成数据库 hold，也不能记成供应商等待。
- 真正 `_commit_trade.commit` callback 约 **146ms**。
- 之后 route projection 约 **77ms**、Effect 保存约 **103ms**、控制任务收尾约 **49ms**。

另一 MU `case_0ecb18f82bb24060ac2dc0f95c6eef4f` 更清楚地区分了争用：最后 owner **1.944s**，分支保存 7 次 SQLite 失败重试，`journal.gate` 累计仅 **1.5ms**。争用发生在数据库准入，而非单纯被本进程 gate 卡住。正常业务提交仍约 **109ms**。

### 5.2 CEG：Operations 7.563 秒并非“运营模型慢”

Case：`case_d377f04f4f9544bebd5b17c52e1373bf`，两分支均 POOL_HIT，仍有 Operations 长尾。

| 区间 | 耗时 |
|---|---:|
| Trade 生效→Operations SDK 发起 | 3.377s |
| Operations SDK 区间 | 7.158s，单独扣除 |
| SDK 完成→Operations owner 返回 | 4.186s |
| **Operations 非 SDK** | **7.563s** |

前段 3.377s：

- Trade 生效→Worker Job 创建：**1.356s**，含 Operations Effect 准备、读取、输入及派发。
- Job 创建→开始：**292ms**。
- Job 开始→SDK 发起：**1.730s**；其中原生 `thread_resume` **1.506s**，`turn_start` **117ms**。这是同一 Case 的既有 Capsule，不是又 Fork 了一条新 Case 线程。

后段 4.186s：

- SDK 完成→Job `finished_at`：约 **87ms**。
- `finished_at`→Scheduler `worker.result_received`：约 **100ms**。
- Scheduler 已收到结果→`worker.receipt_saved`：**2.576s**。
- 解析校验约 **189ms**。
- 最后 Operations owner约 **1.229s**，其中 Case 读取约 **176ms**、保存阶段结果约 **63ms**、`case.operations` 约 **432ms**、Effect 保存约 **392ms**、控制收尾约 **52ms**。

毫秒四舍五入和边界之间的小间隙保留在总区间内。上述以现有 milestone 划界，不把 owner、case.operations 和其子事务重复累计。

**这例的 SSE/结果传输不是 2.6 秒主因；2.576 秒是在收到成功结果后发生的回执持久化链。** 其中 terminal receipt 编码仅约 52ms；有两次 SQLite 重试，但详细 span 被 48 条日志截断，不能把所有剩余时间都判为 SQL 锁。

### 5.3 下一阶段结果链的最小方向

1. 优先移除外部维护进程的长写事务；同进程优先队列不能抢占其他进程已经持有的 writer。
2. `run_trade_pair` 已 gather 到本轮结果，当前 owner逐个 `save`。两份都有效时，将预编码的两份分支结果、input refs 和 ACK intent 一次短事务保存；混合成功/失败时仍先保存有效结果，再传播失败。保留结果不可变、原 Job 身份和租约检查。
3. 在同一个 owner 内复用已读阶段/请求的必要标量，减少重复 `stage/get(worker_requests)` 和不必要的完整 Case 解码。跨 Future 或跨 owner 后仍按原 fence 重新取得权威状态；不能缓存旧 Case 覆盖并发 W1R3 更新。
4. v6 Operations 的 WATCH/PIN、阶段提交、Case/route 和线程所有权释放已合并；**不再提重复实施同一合并**。下一步只处理回执保存、owner 前后重复读取及 Effect/control 收尾的可合并投影。
5. Worker 正常成功路径还会先更新 execution timing，再写 CLEANING，再写最终状态；每次 `_update` 都伴随 Job 持久化、镜像及事件。对已有可靠成功 receipt、只释放 busy 标记而保留 resident 的路径，可准备一个终态快照，保留必要 release 后一次发布。真实终止/失败/隔离路径仍保留清理状态和资源确认。
6. 必须先补齐 `write_result` 的提交→W3 transport loop 开始、writer callback 开始/结束→协程恢复几个轻量时钟，定位 MU 的 1.430s 和 CEG 回执保存里的空档。复用现有 trace，不新增数据库流水或长期高频 profiler。未拿到这些证据前，不以扩大线程池或增加恢复队列作为修复。

## 6. 两个此前未闭合的资源来源

### 6.1 Native 远端插件目录：大写入已证实，历史慢例因果仍待验证

三分钟采样中 Worker cgroup 写入约 **1.11GB**；主 Python `/proc/io` 增量约 **1.13GiB**，后者包含回收子进程，不能作为主 Python 独立写盘量。

随后按 cgroup、文件名采样：

- 19:35:24–19:35:50 UTC，`remote_plugin_catalog/.tmprrUThG` 写入 **77,858,335 字节**。
- 19:36:55–19:37:46 UTC，Native PID `888350` 的 `tokio-rt-worker` 向 `cache/remote_plugin_catalog/.tmpgR4sFs` 再写 **77,858,335 字节**。
- 该目录对应的正式 JSON 文件约 75MiB，mtime 持续更新；同窗也有 `state_5.sqlite-wal`、`logs_2.sqlite-wal`、`thread_history_1.sqlite-wal` 写入，但不能把它们与目录写入混为一项。
- 同一 25 秒窗口主 Python 的 Worker DB/WAL 写请求各约 **3.6MB**，与一次插件目录写入的量级明显不同。

eBPF 这里记录的是 VFS 写请求字节，不等同于设备最终物理写入；实际设备量以 cgroup I/O 为准。两窗口没有证明每个 Case 必然下载一次，不能直接外推全天固定 GB 数。

锁定的 Native 上游提交 `01fc69f4026735edfdf6789820549727a4867b11` 中：

- `features.plugins`、`features.remote_plugin` 默认开启；只关 `recommended_plugins` 不够。
- 插件启动任务存在 `RemoteCatalogCacheRefreshMode::Force` 路径。
- cache writer 全量 pretty JSON 序列化并原子写文件；3 小时缓存 TTL 无法阻止明确的 Force 刷新。

证据为该锁定提交的[插件启动管理器](https://github.com/openai/codex/blob/01fc69f4026735edfdf6789820549727a4867b11/codex-rs/core-plugins/src/manager.rs)、[目录缓存写入](https://github.com/openai/codex/blob/01fc69f4026735edfdf6789820549727a4867b11/codex-rs/core-plugins/src/remote/catalog_cache.rs)及[特性默认值](https://github.com/openai/codex/blob/01fc69f4026735edfdf6789820549727a4867b11/codex-rs/features/src/lib.rs)，本地保存了原始源码。

推荐方向：**让 W3 专用 SDK client 从启动前就禁用未使用的插件市场功能**，保留本地 AGENTS/唯一 Skill、显式 W3 Research MCP、Web Search 和其他既有工具能力。不能只在 thread 配置层晚关闭；如果通用 Capsule 初始化时还不知道用途，应传递一个最小 W3 profile 标识，避免波及 D2/D3。

优先使用现有 `CodexConfig.config_overrides`，不先打 Native 补丁。先验证全部必要工具、模板继承、真实 Research MCP 工具列表和离线回放均不变；若 Native 仍存在必要插件依赖，才在该锁定源码内对非交互 Worker 使用已有 stale-only 路径，禁止盲目禁用实际依赖。**准备 profile 错配→额外 Resume→更多插件刷新是一条需要受控 A/B 验证的放大链，尚未量出其全部墙钟收益。**

### 6.2 Runtime writer：已捕获到其他进程占有

按 `/proc/locks` 匹配数据库 `-shm` inode，只计包含字节 120 的 WRITE 锁，不把读锁或 checkpoint 锁当作 writer。该偏移的含义来自 [SQLite WAL 文件格式](https://www.sqlite.org/walformat.html)。

| 窗口 | 样本点 | 捕获 Runtime writer 的归属 |
|---|---:|---|
| 19:27:12–19:28:12 UTC | 234 | Transport Maintenance 7、Scheduler 6、Projector 2、Executor 1 |
| 19:47:25–19:47:55 UTC | 117 | Transport Maintenance 8、Scheduler 1 |

这是离散采样的正证据，不是每笔事务的完整持续时间，也不是全天占比。采样与 MU/CEG 早期慢例不同时，不能证明具体哪一笔维护挡住了它们。

Transport Maintenance 的近期日志每轮总耗时约 **1.84–2.98 秒**，跨四个 source；Runtime 每轮扫描 64 份传输记录、登记 64 份 Native inventory，回填已 ready，过期/删除数量多为 0。**整轮耗时不等于 Runtime 一笔 writer hold**，但确有在“没有实际删除”时仍获取 writer 的工作。

当前真实路径：

- `source_native_gc.reconcile`：BEGIN 后枚举 schema、读 payload、解析/替换引用，默认 `.3s` 预算在事务内使用，单行/单次读取仍可跨越预算。
- `source_native_gc.inventory`：BEGIN 后遍历并排序目录、读 manifest、stat，再登记 artifact；`limit=64` 只约束成功处理数量，不约束排序前的目录规模。
- `source_native_gc.collect` 和 `source_lifecycle.expire`：仍保留部分文件访问/递归删除于事务中。

**最小修复边界：**优先 Runtime source 的实际在用路径，先短读取得候选与身份，在 writer 外解析、检查 manifest 和准备引用；短事务内重新验证当前所有者/版本、写引用和推进现有 cursor。文件删除复用现有 quarantine/exit 记录与原子 rename，递归删除放到提交后。发生并发变化时保留候选重查，不能错误推进回填完成标志或漏掉新引用。

保留 Snapshot/CDC pin、artifact_ref、retention hold 和 quarantine 可恢复语义，不停掉整个维护服务、不取消文件清理，也不延长业务 SQLite timeout。阶段耗时尚需小范围采样才能确认 inventory/reconcile/collect 各自份额；**现有证据已足以要求把文件/大解析移出 writer，但不足以承诺它独自消除全部结果长尾。**

## 7. Worker 生命周期与其他修复的定向核验

### 7.1 Worker DB 连接关闭成本

60 秒锁采样中 Worker `-shm` 有 **226/234** 个采样点不存在，其余仅少量出现；`JobStore.connect()` 每次新建连接，调用后 closing。该观察与频繁成为最后连接后关闭 WAL 的行为相符，不代表数据丢失。

本地临时 SQLite、同一个约 5MB 数据库、100 次小 UPDATE：

| 方式 | 总耗时 | 写后 shm 消失次数 |
|---|---:|---:|
| 每次开关连接、无持有者 | 1.111s | 100 |
| 保留一个空闲、不持读事务的连接 | 0.045s | 0 |

这是 Windows 离线机制测试，不是生产加速比；不能宣称生产也快 24 倍。建议作为 P1 小实验：复用现有 JobStore/写入 owner 生命周期保留一个无读事务的连接，减少最后连接关闭的隐式 checkpoint/churn。保留 FULL 与原始持久化边界，确认 WAL 自动 checkpoint 和关闭清理有效，不新增连接池或常驻后台线程。若生产 A/B 无实际收益则不纳入。

### 7.2 派生物 GC 回填仍可能重建冷资产

上一轮已把 Worker GC 的主要文件扫描/删除移出事务；该优化确实在生产代码中。但旧上下文水位回填调用 `protect_components()`，它不仅写 retained_until，还会主动 `build()` 任何 COLD 资产。

离线真实 Store 测试：一个仍受上下文引用的 COLD pack，仅执行 `gc_derivatives(apply=True)`，便触发了 **一次 rebuild**。给 rebuild 注入 300ms 的模拟耗时后，GC 用 **325ms**，超过声明的 250ms 时间预算，说明预算检查无法约束单次 build。

生产有 35,682 份上下文，其中所选时刻约 **11,426** 份仍未过期；3,929 个 READY 资产中 **3,420** 个水位仍为空。它说明旧资产回填尚未全部收敛；**没有证明 COLD rebuild 当时恰好挡住 MU**。

修复应把“回填保留水位”和“为实际消费恢复冷资产”分开：前者只聚合一页 manifest 引用、在短事务取各资产最大 retained_until，绝不编译 COLD；真正 bind/read 再按既有不可变 source 恢复。继续保护 NULL 直到游标完整完成。复用原 cursor 与字段，不新增回填表或全库搬运。

### 7.3 旧线程删除异常

当前读取到一个旧代际 `RETIRING` 槽位，错误是 `-32600 thread not loaded`。它创建于 18:25，非当前代际 114 份新库存；Worker 当前 cleanup closing/quarantined 均为 0，库存实际有 4 份，没有证据证明它造成当前长期库存泄漏。

代码上的问题是：通用 lifecycle helper 并不必然加载待删除 Thread，数据库 registry 证明身份不等于该 app-server 已加载它；直接调用 delete 会失败。10 秒 RPC timeout 同样不等于 Thread 不存在。

处理方向：保留原 Thread/run/parent 验证及 active Job 保护；针对“已持久化但未加载”的同一 Thread，采用该版本 SDK 支持的同身份恢复后删除方式，控制/清理专用 profile 不启动模型、不加载无关市场。不要把 `-32600` 视为成功删除，不新建替代 Thread，不因超时清空登记。仍复用现有低优先级 helper 和 retire cursor，不增加新清理队列。先验证该版本 delete 所需协议，再修改。

其潜在影响是重复 helper/RPC 和维护周期拖长，不能未经证实归为 15 秒 Boot 主因。

### 7.4 上轮修复的实际有效性

| 项目 | 本轮验证 | 处理结论 |
|---|---|---|
| Task 容量释放后立即续派 | 35 个 case_task：领取等待 P50 **29ms**、P95 **149ms**、最大 **496ms** | 没再观察到原 53 秒等待；不重写调度器 |
| Task 失败/延期恢复 | completion callback 会续派已到期 PENDING；未到 due_at 或需恢复 RUNNING 仍靠既有恢复 tick | 是剩余风险边界，不冒称所有恢复已即时；补窄场景测试/到期计时，发现实际慢例再改 |
| 14/7 日索引、Bridge | 35 个 Case：daily_index 最大 **46ms**，bridge 最大 **133ms**，frozen fact content 最大 **245ms**；生产有 66 份 prepared index、41 份 bridge 标记 | 当前不是秒级尾部主因；不重新设计 Fact Library |
| 公共 Template | 仅一个实际 Ready scope、正常补货连续；公共准备已脱离 ticker 全量输入 | 未发现重复建立多模型库存的问题 |
| READY 历史查询 | 当前代际可完整还原；未见当代际错误回收、失败冷却 | 不新增注册表/缓存 |
| SourceOutbox 技术 touch 去重 | 实际生产 trigger 保留业务差异比较，排除纯 updated_at | 不再次实施；它不消除其他 Native/ref 维护的 writer 占用 |
| v6 Operations 原子提交 | 当前仍为既有合并事务 | 保留，不重复拆合 |
| Worker 事件通知 | 当前终态 SQL 保存后先通知，再做镜像/事件；CEG 已收到结果前的延迟约 100ms | 不再把所有运营尾部归给 SSE；但终态前中转写入仍可精简 |

Task callback 对已完成、失败返回、容量释放的正常路径均会运行；仍必须验证取消、租约恢复和 due_at 刚跨界的情况。不能用无差别立即重跑来替代原有退避，也不能让到期恢复等待无关维护长轮次。

## 8. 推荐实施批次、收益与约束

各项收益作用在同一关键链或共享资源上，**不可把每项最大收益直接相加，也不承诺改完某一项就必达 P95≤10 秒**。

| 顺序 | 最小改动 | 预期收益依据 | 资源代价 / 约束 |
|---|---|---|---|
| P0-1 | W3 prepare/recovery 显式统一子 Agent 参数；保持 scope 真实比较 | 已复现可直接去掉首次 Trade 的额外 Resume；样本控制 RPC 常为 34–178ms，慢尾及间接市场工作收益待测 | 无新增常驻资源；不改全局默认 |
| P0-2 | W3 专用 client 启动前关闭未使用插件市场；验证保留全部必要工具 | 已证实每次命中的目录写入约 77.86MB；减少 CPU、网络与 I/O，Boot/Fork/结果链墙钟收益需 A/B | 原则上减资源；不得影响其他工作流或本地 Skill |
| P0-3 | Runtime source Native inventory/ref 维护移出长 writer；保留短事务 CAS 与 pin | 已捕获该进程 writer；MU 另一慢例明确有 7 次数据库准入重试、合计约 1 秒级 BEGIN 成本 | 不新增服务；不停止维护；不把磁盘安全换成速度 |
| P0-4 | 两分支有效结果批量短保存；减少 owner 内重复读取；成功 Job 合并非必要中转快照 | 减少独立 writer 准入与镜像/事件；正常可能省数十至数百毫秒，争用尾部收益待测 | 不删除 paid receipt、fence、结果不可变或 ACK 保护 |
| P0-5 | 对 1.430s 调度空档、2.576s terminal receipt 增加最小边界时钟后修实因 | 这两项最多覆盖对应未知/消费区间，不能预先许诺全部消失 | 无 DB 观测流水、无新常驻采样器；先探测再改 |
| P1-1 | preparation task 本地等待结束后一次读状态；保留绑定升级机制 | 去掉重复 operation/槽位查询与最多一个轮询尾差；当前实测收益偏毫秒到数百毫秒 | 使用现有 task/通知；恢复仍查原身份 |
| P1-2 | GC 水位回填只写元数据，不恢复 COLD；批内去重 | 消除已复现的非消费编译和超预算；具体生产关键链收益未量出 | 内存至多一页；NULL 保护与引用安全保留 |
| P1-3 | Worker 空闲连接生命周期小实验；修正未加载 Thread 的退役 RPC | 减少连接关闭 churn、重复清理；需要生产小窗量化 | 不建连接池、不延长 timeout、不误删 Thread |
| P1-4 | 用修复后真实服务时间重做供需评估 | 判断保持 4、改 6 或双构建流水线的边际收益 | 先测；不得限制实时 Case 或等待补货 |

### 具体实现顺序与文件边界

**第一批：消除已证实的无效工作。**

1. 修改 `thread_lifecycle.py` 和 `w3_ready.py` 两处 W3 request 构造；补同 profile 零额外 Resume、真实权限/profile 不同必须 Resume、旧准备恢复仍保持身份的测试。
2. `sdk_runtime.py`、`capsule_child.py`/Capsule 初始化仅增加必要的 W3 专用启动配置传递；公共模板与实际 Case 使用一致配置。先做无模型初始化/工具列表测试，再有限自然 Case 验证。非 W3 client 保持原行为。
3. `source_native_gc.py` 优先拆 inventory 文件读取与 SQL；随后处理 reconcile 的解析/读文件、collect/expire 中实际文件操作。尽量复用现有 metadata/cursor/quarantine，避免新的生命周期状态机。对 mutable source 必须在写入引用前检查所读身份仍有效；不能以吞掉并发变化来完成回填。
4. 把新观测点合到现有 trace，先查 terminal receipt writer/loop 时间与 1.430s 间隙。根据测量再决定是否还需改变执行器；不先扩容。

**第二批：结果链的局部减法。**

5. `w3.py` 的 pair consume 与 `w3_execution.py` 的分支保存增加现有事务内批保存入口；保留混合失败情况下先存成功分支、恢复不重跑成功 Job。
6. `jobs.py` 仅对确实保持 resident 的正常成功路径合并 execution-timing/CLEANING/terminal 的中间投影，保留原 completion receipt、资源 release 与最终状态原子发布。失败、cleanup unknown 和 quarantine 不走此捷径。
7. 同 owner 内减少重复 Case/Stage/worker request 读取；不跨阶段缓存业务可变状态。Effect 完成、业务 stage commit 和 ACK 所有权的必要边界不因“减少事务数”被删除。

**第三批：只有测到收益再纳入。**

8. 元数据 GC 回填、Worker keeper、旧 Thread 正确删除；分别确认 writer/IO 收益。
9. 以新服务时间回放突发，再做库存 4/6 或有限双构建试验。若冷准备已能稳定控制在约 3 秒，宁可允许低比例的快速 MISS，也不为追求 100% HIT 保留大池。

本轮不推荐跨 Case 复用已使用物理 Capsule：清空工具能力、旧 Thread handle、延迟 ACK 和清理 owner 的正确性验证范围明显大于上述最小修改。也不推荐 prefork Python、增加 Redis、新 executor 层或额外常驻通用 SDK 池。若前两批后仍失败，再基于残留时钟评估，不能把这种大改当作当前问题的默认答案。

## 9. 验收与剩余未知

### 9.1 目标预算

以下是下一轮设计预算，不是本轮实测承诺；端到端按每 Case 的实际不重叠关键链验收，不把各阶段 P95相加。

| 环节 | 参考常态 / P95 预算 |
|---|---|
| 到达→领取 | 约几十 ms / ≤0.3s；恢复例外仍计入 |
| 前置冻结与 W1/W2 非模型部分合计 | 约 0.5–1s / ≤1.5–2s |
| W3 Trade 非模型 | HIT 常态尽量 ≤1.5s；所有冷热自然样本 P95 争取 ≤5s |
| Operations 非模型 | 常态尽量 ≤1s；P95 争取 ≤2s |
| 完整 Runtime 非模型 | **P95≤10s**；保留所有内部等待 |

低库存冷准备仍需作为完整 Case 参与硬目标；不能只让 HIT 达标。单份冷准备 3–5 秒的现成路径说明有进一步收敛空间，但本轮尚未证明高峰时也能保持。

### 9.2 必须过的业务与恢复验证

- 同 profile READY→Trade 不再 Resume；Trade fast、Operations default、模型和 effort 与原方案一致。
- 新 Case 物理 Thread 独立，A/B 不互相继承历史；同 Case 的 Operations、失败重试与恢复保留原身份。
- 不同 profile、权限、MCP、版本确实变化时不得复用错误 scope。
- 同时成功、单分支失败、两分支失败、保存后 owner 丢失、提交前/后重启：成功结果不可变、不可重发已受理 Job、不得重复交易或 WATCH/PIN。
- Native refs 回填遇到并发更新、文件进入 quarantine 后新增引用、旧 packet pin、只读 snapshot 尚存时，不能误删或误标 backfill ready。
- Provider pause、429 及恢复只限制真实新请求，成功结果消费继续前进；未知派发仍只查询原 Job，网络异常不得当 404。
- 同 ticker 第 7 个待处理任务在前一任务释放后立即续派；失败、取消、恢复和 due_at 跨界也分别验证，所有内部等待保留计时。

### 9.3 生产性能与资源验收

1. 分批上线后，各取包含突发和 MISS 的连续自然窗口，**至少 100 个完整实时 Case**；同时报告纳入总数、未完成数、失败数和计时缺口。更长的高峰窗口用于确认稳定性，不用“最好的100例”。
2. 同时报 Runtime、Trade、Operations 的 P50/P95/max；统一统计到真实 owner 返回，不在业务 effective_at 截断。
3. READY 报实际完整库存、领取 HIT/MISS、连续空库存时段、构建耗时、完成→再补货间隔和每分钟突发需求。后台 builder 工作与实时已绑定准备分开统计。
4. 已知不使用的插件目录大写入应消失；保留 Native 状态库等必要写入。按 cgroup 与文件归因，不再叠加父子 `/proc/io`。
5. Runtime source 维护事务不得包含目录排序、manifest/pack 读取、全量 JSON 编码或递归删除；writer 等待、BEGIN、hold、commit 分开报。少量原子 rename 的耗时仍计入。
6. 资源不能通过增加常驻进程或压低实时并发换取改善。记录 Worker 子树 PSS、活跃/空闲数量、CPU/IO PSI 与实际 swap-in/out 增量。库存试验只改变后台供给，不给实时 W3 加并发门槛。

### 9.4 明确未解决的问题

- 历史 15 秒 Boot 的 Python/Native 调度、缺页/I/O 与父进程处理各占多少，仍无法从事后记录严格恢复；需要下一次自然慢例的同窗证据。
- Plugin 市场写入已经文件级确认，但减少它后能缩短多少 Boot/Fork/结果消费，需 A/B，不将 77.86MB 换算为虚构固定秒数。
- Transport Maintenance 确实持有 writer，但每个内部函数的实际 hold 与当时 MU 重试的直接重叠尚未捕获。
- MU 1.430 秒、CEG 收到结果后保存回执的未覆盖部分，需要 scheduler transport loop/回调交接时钟；Worker 的历史 max lag 约 1.951 秒不能当成同一进程、同一事件的解释。
- 观察期间 MemAvailable 约 13.2–13.8GiB，Guardian 为 NORMAL、无新增 OOM；CPU runnable 竞争仍存在，startup_pressure 可为 true。资源有余量不等于不存在局部等待。
- 根盘可用空间由约 5.83GiB 降至 4.92GiB，仍需后续单独做实际目录增长归因。本轮未清理业务数据，亦未用磁盘余量代替具体慢链分析。

## 10. 证据索引

证据目录：`eval/runtime_nonsdk_deep_attribution_20261118/`。

| 文件 | 用途 |
|---|---|
| `inventory.json`、`inventory-end.json`、`analysis.json` | 槽位/operation、库存区间与逐 Case 关联 |
| `cohort-end.json`、`logs-end.json`、`acceptance-end.json` | 35 例纳入、34 例闭合及原始 span/milestone |
| `receipt-consumption.json` | 102 个阶段结果的接收→回执保存时钟 |
| `observation.json`、`observe.py` | 180 秒进程、线程调度、缺页、cgroup、Boot 与完整 Fork trace |
| `locks.json`、`locks-native.json` | Runtime/Worker/Native 数据库锁的两个独立窗口 |
| `cgroup-writes.txt`、`tmp-writes.txt`、对应 `.bt` | 有过滤、有期限的文件级写入归因 |
| `maintenance.json` | Transport Maintenance 生产版本、源码 hash、实际轮次输出 |
| `ready-scope-test.json`、`ready_scope_test.py` | READY 配置错配与去掉额外 Resume 的无模型复现 |
| `mechanism-tests.json`、`mechanism_tests.py` | GC 回填恢复 COLD、SQLite 最后连接关闭行为的离线测试 |
| `builder-utilization.txt`、`supply-sensitivity.json` | 补货间隔、供需敏感性回放及假设 |
| `health-start.json`、`health-end.json` | 有效服务版本、重启、资源保护与可用内存/磁盘 |
| `upstream/` | 与当前 Native 构建锁定提交一致的插件源码，非 latest 分支推断 |

上一轮发布数据与早期 CEG 原记录保留在 `eval/runtime_global_latency_release_20261118/`；本报告没有覆盖或改写原始验收结论。
