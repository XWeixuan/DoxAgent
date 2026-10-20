# W3 准备/冷启动失败专项交接（北京时间 2026-10-21）

## 交接目的与结论

用户要求把与此前“旧 W3 Fork 准备超时”同类的现象交给另一 Codex 会话处理。本轮只读排查，没有实施此链路的修改、重启或 Case 重放。

**归于同一线程准备/启动问题域，但不能等同为同一个 RPC 太慢。** 本交易日 Case 使用 `w3-entry-v5` READY 流程；其底层仍经过 Capsule + SDK + Fork/Resume + 生命周期恢复。最强证据指向 **Capsule boot-ready 30 秒超时** 和恢复 `thread/list` 10 秒超时。已经进入 SDK Fork 的成功调用通常很快。

建议另一会话直接从本文与本地保存证据继续，不要先重跑大批模型或盲目调大 Fork RPC 超时。

## 1. 生产环境与时间范围

- SSH：`doxagent-sg`；代码目录 `/home/ubuntu/doxagent`。
- Worker：`doxagent-v2-codex-worker-1`；Scheduler：`doxagent-v2-v2-scheduler-1`。
- 两容器运行镜像 `sha256:8a72916bedc0c9210e784d341d24ed9632c588be90cbff5f80c16e8fb91192ef`，构建提交 `160199b7a2009ad59c2b80b5d5941e9a96d66383`。
- Worker UTC 13:21:26、Scheduler 13:21:50 启动，restart_count=0。
- 排查时本地/远端 main 为 `bfe1a86b81b0d2624e1b2221bb0fb4a0c840d1c4`。实际容器中的 thread_lifecycle.py、capsules.py、capsule_child.py、sdk_runtime.py、jobs.py、w3_ready.py 等共 14 个相关模块与该 main 内容一致。
- 主 Case 快照窗口：UTC 2026-10-20 13:30—约 16:27（美东 09:30—12:27）；补充启动计时读取到约 UTC 16:30。

数据库只读：Worker `/data/workspaces/worker-jobs.sqlite3`；Runtime `/data/runtime/runtime.sqlite3`。需用 SQLite mode=ro / query_only，Runtime payload 用 NativeContent 解码。不要整份打印容器 environment 或私有配置。

## 2. 失败规模和业务边界

- 开盘后新建 92 个 Case，15 个 W3 trade_decision/PREPARE 终态失败：12 TimeoutError、3 TransportClosed/stdout closed。都没有正式进入这一次 trade_decision 模型调用，W3 attempt_count=0，stage 的 worker_job_id 为空。
- 加上盘前创建、开盘后失败的 `case_c9378c7d671c41c5b0a3ada8f2262806`，本窗口 W3 准备终态失败共 16 个。
- 另 1 个 ONDS Case 已 COMPLETED，但后置 operations 最终因 START_THREAD timeout 失败；不能误认成交易判定失败，也不能通过重跑 trade_decision 恢复。
- Worker 还存在失败后重试成功的启动 job；不能把 job 失败数直接加到 Case 失败数。

## 3. 代表 Case 与持久化身份

### A. BE：反复 boot 超时后同 slot 一次恢复仍失败

- Case：`case_9fce33d229f9411689988c41b5fbe42f`。
- UTC 13:30:27 创建，13:35:01 FAILED。
- slot：`823dc08828f347be883313e3adf582c8`；run_id=`w3-ready-823dc08828f347be883313e3adf582c8`。
- operation_key=`w3-slot-fork:<完整 slot id>`；ready_recovery_count=1，证明上一轮“同绑定一次恢复”已执行，不能误报为完全未启用恢复。
- 去重后的准备尝试中：13:30:50 capsule.spawn 约 31.1 秒 TimeoutError；13:31:57 又约 30.7 秒；13:33:39 又约 33.6 秒。恢复过程夹有 thread/list 约 10 秒取消、TransportClosed 和 client_close。
- 原业务 W1/W2 已成功，路由 NEW_UNRELATED → W3。失败不是 NEW/OLD/Policy 路由门槛问题。

### B. INTC：同一准备链最终 TransportClosed

- Case：`case_a0db40d6c34040ee88ed893be4ca5723`。
- UTC 13:35:26 创建，13:37:18 FAILED。
- error：`w3_ready_prepare_failed`，`Codex process closed stdout`，stderr_tail 只见 bubblewrap fallback 提示。
- 另两例同类：`case_f5f86456e2ff4286ba515c034a38920a`、`case_10fa250b789d4cf48559c3f8925b1ece`。
- 这三例在 slot 的最后一次恢复走到 TransportClosed，部分没有进入额外 READY 一次恢复，因为当前仅针对 Timeout/Cancelled 空准备判定可恢复。是否扩大恢复类型必须先确认原操作/进程身份与退出语义，不能创建新 slot 掩盖同一操作。

### C. ONDS：交易判定成功，operations 冷启动失败

- Case：`case_59fc7f7248b84709bfaa0af665214b99`。
- UTC 13:33:49 创建；13:36:25 已完成 NO_TRADE 判定；后置 operations 最终失败。
- 相关 run_id：`w3-ready-3b9268730184437aa5a204b59b5ee923`。
- Worker job `0c6b37465b5fb1339023d74f74b89b2fd82380f1cf96e7853285378d3a35fe97`：startup_wait 约 6.2 秒，capsule.spawn 约 39.5 秒 TimeoutError；worker_wall_time 约 59.4 秒。
- 后续同 run 的 operations 启动 job `ae5fb99dd7d6b491859dc086a77a9f711922b1eafa1e38eae7c16943e7f2c4ff`、`87b4cfc3fa42d1697aa70f6d8c9614ab8b38128d2593ac20d5c41d24ad7ee1f4` 仍在 capsule.spawn 约 30—32 秒失败。
- 另有相同模式 trade_decision job `b3abf3fe2a33c67f81db929d17bea37145917142e1ca2b9d2bafc18046768a54`，UTC 15:44:19，spawn 约 35 秒超时，但不能不查最终 Case 就把该 attempt 计成最终业务失败。

## 4. 边界计时：故障在 Fork 之前

对 Worker `boundary-timing` 文件按 identity + plane + started_at 去重，保留最长记录。约 UTC 13:30—16:30 的取证集合：

| span | 样本/结果 | 耗时 |
|---|---|---|
| sdk.thread_fork | 62 个记录，均成功 | p50 1.91 秒，p95 6.02 秒，max 7.29 秒 |
| capsule.spawn | 185 条 span | 50 成功、106 TimeoutError、29 CancelledError |
| capsule.spawn 成功 | 50 | p50 22.68 秒，p95 30.94 秒，max 32.66 秒 |
| capsule.spawn 有错误 | 135，含取消 | p50 31.69 秒，p95 37.27 秒，max 63.52 秒 |

这些是准备尝试与启动 span，不是唯一 Case 数；成功 Fork 集合有存活者偏差，不能证明任何未来 Fork 都不会慢。但它清楚显示，本轮很多失败在收到 boot_ready 之前，尚未执行 Fork。

9 个可直接读取到失败 job 回执的启动样本全部为 CODEX_START_TIMEOUT，细分 timing 都有 capsule.spawn TimeoutError，实际 worker wall 约 38—65 秒。**START_THREAD 标签不是“必定等满 120 秒”，内部 30 秒超时同样被 jobs.py 捕获映射到这一标签。**

准备恢复 RPC 的去重 span 中，thread/list 出现 59 次 CancelledError、12 次 TransportClosedError；thread/delete 也有少量。它们与 boot 冷启动错误应分开归因。

## 5. 代码落点与当前预算

以下行号按已读取的运行源码核实；后续修改后需重新定位。

1. `src/doxagent/codex_worker/capsules.py:81`：`await asyncio.wait_for(self.receive(), 30)`，等待 boot_ready。
2. `capsule_child.py:21` 起：先导入 schema/sdk、构造 OpenAICodexRuntime，再在第 38 行 `await runtime.initialize()`；完成后才发送 boot_ready。因此当前 30 秒同时覆盖 Python 导入、对象构造和 SDK app-server 初始化。
3. `capsules.py:249`：准备 Fork/Resume 后等 capsule 响应 60 秒。与上面的 boot 30 秒不是同一计时段。
4. `jobs.py:591`：startup semaphore acquire；第 596 行 runtime.start 外层 `min(120, request.timeout_seconds)`。本次这些失败没撞外层 120 秒，先撞内部 boot 阈值。
5. `thread_lifecycle.py:260,263`：prepare execute task 有 shield 10 秒 + 再 55 秒；execute 内包括恢复查询、startup 等待、boot 与准备。整体准备预算和内部阶段预算仍有叠加/相互取消关系。
6. `thread_lifecycle.py:29,35,51`：生命周期客户端初始化锁/外层 15 秒、SDK 初始化 10 秒、单 RPC 10 秒。
7. `AppServerThreads.request()` 复用 `self.client`；一个 RPC 失败后会关闭这个共享 client。并发中的其他请求可能因此收到 TransportClosed。**代码存在故障传播路径，但目前缺少具体 client/process id 的全链跟踪，不能把三个 stdout closed 都定性为此原因。**
8. `w3_ready.py` 会在发现 resident 被资源回收时把 READY 标记 RETIRING。主 Worker 快照中有 12 个 `resident reaped` 退休 slot，另有失败/超时退休。应核实回收策略与冷启动/补池是否形成资源消耗循环；不能直接关闭全部回收。

## 6. CPU/I/O 与其他假设

4 核主机：UTC 13:40—14:00 几乎 0% idle、运行队列 25—39；之后部分时段 I/O wait 38%—47%，vda 队列上百。当前 CPU PSI some 约 90%。成功冷启动已逼近 30 秒阈值，说明资源争用是有直接时间相关证据的主要放大因素。

不过，还缺少失败 capsule 进程自己的 import/SDK 分段收束证据：boot_ready 发不出来时，现有进程内 timing 没有落盘。**不能精确分解为多少秒 Python 导入、多少秒 app-server 初始化，也不能排除特定 SDK 进程挂起。**

stderr_tail 的 `could not find bubblewrap ... will use the bundled bubblewrap in the meantime` 是 fallback 信息，不是退出码证据。该窗口 kernel journal 未检索到 OOM kill/segfault；仍需检查用户态退出码、信号和共享客户端关闭的关联。

## 7. 建议接手顺序与边界

1. 先复核本地取证，明确 boot 30 秒、准备 65 秒、RPC 10 秒和业务 runtime.start 120 秒分别在什么阶段生效；不要只提升 thread/fork timeout。
2. 在失败前可留下最小 boot 阶段标记与子进程退出码；只存阶段/时间/身份/脱敏错误，不存 prompt、密钥或完整 SDK stderr。
3. 在业务启动可正常推进的前提下协调补池、冷启动与回收，避免连续失败→销毁→立即重建造成更大 CPU/I/O 压力。是否调预算、复用预启动 SDK 或减少无效冷启动，结合现有隔离/身份设计选择；不要为了加速让不同 Case 共用业务 thread。
4. lifecycle RPC 的关闭行为需要故障隔离；保持原 operation/cwd/receipt 先 reconcile，不能无证据新建另一个 Fork。
5. operations 恢复只能恢复原后置阶段，不能重发已提交交易判定或过期交易意图。
6. 本轮百炼 timeout 与 W1 引用字段错误由总体报告单列，不要混入 Fork 修复扩大范围。

## 8. 可直接使用的证据文件

绝对目录：`C:/Users/WEIXUANXIE/Desktop/DoxAgent/eval/runtime_open_audit_20261021/`。

- `snapshot.json`：Case、W3 stage、turn 与路由；`worker.json`：完整 slot id、case 绑定、recovery_count、error。
- `worker_ops.json`：w3_thread_operations 的持久化状态与生命周期源码。
- `timings.json`：启动 job 的 resource_receipt/telemetry、boundary trace、实际 jobs/capsules/capsule_child 源码。
- `cpu.json`、`history-raw.txt`、`memory-io.txt`：资源事实；`inventory.json`：当前运行版本。
- `failed_cases.csv`：总体失败清单。

原始 timing 有进度与最终快照重复；必须采用本文去重口径。生产持续运行，上述文件是本轮固定证据，不代表接手时的最新状态。
