# W3 启动与持久化运行鲁棒性审查及优化方案

日期：2026-10-26，北京时间。审查基线：main `3602b0fa`；其中生产 Worker 修复版本为 `d5f43fb2`。本轮只读追溯生产、检查代码并在独立测试进程中注入故障，**未修改或部署生产代码，未创建模型任务、重放 Case 或调用券商**。

## 1. 审查结论

**仍有高优问题，值得继续修复。上一轮修复了真实的许可释放漏洞，但尚未覆盖同一机制的全部取消路径。** 本轮发现的风险不是泛泛的“再加重试/监控”：其中三组会造成长期不推进的问题已经被当前代码稳定复现。

| 编号 | 优先级 | 发现 | 证据 | 影响范围 |
| --- | --- | --- | --- | --- |
| R1 | P1，首先处理 | Condition 内层任务在重复取消时遗留锁；另有事件流持锁 yield，受慢客户端牵连 | Windows 3.11.14、生产同版 Linux 3.11.13 的独立进程复现取消漏洞；事件流真实心跳复现持锁阻塞 | cold/helper 准入及全局 job 通知，可能阻塞后续请求与启动 |
| R2 | P1 | 关闭命令无发送期限；取消可绕过物理收束；批量回收异常后留下无人持有的 busy 资源 | 真实 `Capsule.close/_take/reap_idle` 代码配合假管道、假进程/资源复现 | 可持续占住 cold；或使一批 Capsule 永久不能领取、回收 |
| R3 | P1 | dispatcher 因一次异常退出后不会自行重启，`start()` 也不替换已结束 task | 注入一次恢复读取异常后，恢复只执行 1 次；再次 start 仍 dispatcher=false | Worker 调度、恢复及补池推进，重启前可能持续失效 |
| R4 | P2，随本轮收尾 | 无主 operation 和未注册 child 无法退役；固定取前 4 条可使后续资源饥饿 | 生产确有悬挂记录；“4 条待确认＋第 5 条可清理”连续 3 轮不推进的本地复现 | 清理与磁盘生命周期；不是当前所有交易 Case 的入口死锁 |

建议实施 **R1→R2→R3**，然后以小范围 R4 收束遗留状态。无需另造调度系统、分布式锁或通用恢复框架。

线上当前没有再次观察到全局硬阻塞。19:30:58 的只读快照中，18:40:37 发布后共有 51 条新 Case：45 COMPLETED、4 PENDING_W3、2 RUNNING，无失败；34 条交易 stage 与 34 条后处理 stage 已 COMMITTED。PENDING/RUNNING 是当时未完成样本，不算成功，也不据此宣称长期可靠性已验收。

## 2. 对上次事故的再次追溯

### 2.1 许可为什么引入，上一轮到底修了什么

`039d6775`（10 月 21 日）引入 `StartupAdmission/PriorityGate`，将 Capsule、预热和首次 helper 初始化纳入同一个 cold=1，避免大量并行冷启动。已有驻留 SDK 不需要重新领取 cold；SDK handshake 仍单独受原 Semaphore 控制；运行中的模型 turn 不持有 cold。这些职责划分合理，应保留。

旧实现退出时先 `await condition.acquire()`，之后才 `active=False`。通知锁争用期间再次取消，便可能只结束任务而不清占用。上轮 `d5f43fb2` 改为先同步归还许可，再异步通知，解决了这条路径。

本轮重跑旧/新代码的同一复现：旧代码 owner 已结束但 active=true、后续请求被卡；当前代码 active=false、后续请求通过。**不能因此回滚上轮修复。**

### 2.2 新追溯说明：事故不能仅归因于一个布尔标记

再次读取最初爆发窗口，而非只看事故后半段的 TimeoutError：UTC 10 月 25 日 23:40—23:50（北京时间 10 月 26 日 07:40—07:50）创建了 71 个准备 operation，最终 15 完成、56 失败：

| 失败操作最后错误 | 数量 |
| --- | ---: |
| `TimeoutError` | 44 |
| `Lock is not acquired.` | 10 |
| `cannot wait on un-acquired lock` | 1 |
| `CancelledError` | 1 |

其中 `w3-slot-fork:f56da936bbf241dd981f0bdc6e02d5fe` 在 07:47:13 写入 `Lock is not acquired.`。另一个正式 job `144991582a5cd517a57ebfaed5a75f546f8935b411e39382bcd6dea5b760dc0e` 在 07:47:26 以 `START_THREAD: cannot wait on un-acquired lock` 结束。之后还出现了启动预算耗尽的 job。

这些是**事故中的真实锁状态错误**，不只是本次合成测试中的假设。上轮事后快照的 `cold_active=true`、无 Capsule、后续准备零物理启动，能够确认共享准入失去活性；但旧版本没有 owner 和取消栈，无法唯一恢复“最初由哪一个任务、哪一次取消触发”。

因此准确结论应修正为：**原事故涉及共享准入的取消/锁不变量破坏；已修复其中的释放标记泄漏，但等待路径仍有可复现的独立硬阻塞缺口。** 不把本轮发现的第二条路径直接宣称为已经证明的唯一首因。

### 2.3 R1：当前代码仍能把 Condition 永久卡住

位置：`src/doxagent/codex_worker/startup_admission.py:77`。

```python
await asyncio.wait_for(self.condition.wait(), 0.25)
```

Python 3.11 的 `wait_for` 会把 `Condition.wait()` 包装成独立 task。Condition.wait 在取消后必须重新取得锁，才能将锁交还调用者继续执行。第一次取消时，外层正在等待这个内层任务收束；第二次取消若打断该等待，外层可能已经退出锁上下文，而内层仍会重新取得锁，随后结束却无人释放。锁还可能被错误释放，从而出现“不持锁就 wait/release”的异常。

合法的取消来源包括准备总期限、单次启动/轮询的嵌套超时、资源干预、显式取消及 Worker 退出；不能假定任务一生只收到一次 cancel。无需假设人工连续操作才能发生。

本轮复现不手动持有 Condition，不运行 SDK：18 个并发申请，对 9 个等待者连续取消两次，然后释放正常 owner。申请自己的等待期限为 50ms：

| 检查 | 当前实现 | 仅改变等待方式的候选 |
| --- | ---: | ---: |
| 300ms 后外层任务已结束 | 10/18 | 18/18 |
| 仍挂起的外层任务 | 8 | 0 |
| Condition 锁仍被占用 | true | false |
| cold active / owner_alive | false / false | false / false |
| 剩余准入队列项 | 7 | 0 |
| 新请求设置 30ms 等待期限，100ms 内是否结束 | 否 | 是 |
| 最大同时 owner 数 | 1 | 1 |

新请求的超时之所以也无效，是因为目前超时只包住进入 Condition 后的循环，初次获取 Condition 锁本身没有被它覆盖。Linux 独立进程复现结果与本地一致。

这说明仅观察 `cold_active` 或 owner 存活状态不够；本次现有监控可能将另一种死锁显示为空闲。也不能通过定时把 active 清零修复，因为卡住的是另一把锁。

### 2.4 R1 的另一处放大器：事件流把慢客户端带进全局状态锁

`jobs.py:579` 也使用相同的 `wait_for(Condition.wait())`，需要一起修复重复取消。除此之外，events 在心跳超时后，仍在 `async with self._condition` 里 yield heartbeat。生成器暂停后锁不会释放，只有消费者继续迭代或显式关闭它才退出锁上下文。

`app.py` 的 SSE 生成器拿到事件后要向网络发送；慢客户端/背压发生在这个边界，就可能让全局 `_emit` 等锁。submit 和 job 的 STARTING/RUNNING/收尾都调用 `_emit`，因而一个观测消费者可能影响无关任务，而不仅是自己的事件展示。

独立测试直接等待当前代码真实的 15s 心跳，再暂停消费者：Condition 保持锁定，另一份通知无法完成；关闭该事件流后通知立即完成。未替换生产事件生成器，也没有发起真实网络请求。此问题不需要重复取消即可触发。

## 3. 完整机制审查：阻塞能否收束

| 环节 | 当前有用的保障 | 本轮判断 |
| --- | --- | --- |
| cold 准入 | 动态业务优先级、单 owner、等待预算、释放前同步清标记 | 保留；补 R1，不增加 cold 并发 |
| SDK 启动与 Fork | SDK_READY、90s boot、45s handshake、绑定准备总期限、物理启动次数、owner fencing | 保留；这些时间限制不能自动给异常清理兜底 |
| 失败后的关闭 | 先发 close、再等子进程、再 TERM/KILL | R2：发送 close 的 drain 不受 5s wait 限制；后续强杀可能根本走不到 |
| 批量回收 | 锁内预留资源，再锁外执行关闭 | R2：预留整批后第一条异常，整批 busy 无人撤销；下一轮只找非 busy，不能自救 |
| job 收尾 | 先清理再发布终态；未知物理 owner 时保留身份，防止重复执行 | 方向正确；重复取消或持久化失败可能跳过收尾，需补有限且可接管的终结路径 |
| 事件流与状态通知 | 持久事件＋Condition 唤醒，SSE 断线可续读 | R1：等待方式有重复取消缺口，心跳持锁 yield 还会把消费者背压传播到无关 job |
| dispatcher | 与具体 job task 分离；资源保护与启动恢复前置 | R3：主循环无异常监督，结束后的 task 仍被当作“已经 start” |
| Effect/阶段恢复 | 原身份、阶段落库、防止重交交易；有租约/fencing 与明确终态 | 保留；不要以重跑交易 turn 解决基础设施状态问题 |
| 补池失败 | 冷却、压力迟滞、业务绑定提升优先级 | 保留；不因候选复现而改资源阈值、扩大并发或取消内存 CRITICAL |
| helper | 单在途 RPC、排队有限、超时、致命错误才关闭 | 共用 R1；清理与永久 quarantine 的解除纳入 R2/R4，禁止排队取消误关他人客户端 |
| 退役 | 不删除活跃 job、保护 Template、保留 Case 证据 | 保留；补 R4 的无主行收束与扫描公平性 |
| 行情/MCP | 行情异步、有请求隔离；MCP 读取有容量/完整性边界 | 未发现需本轮重构的独立高优问题；不触碰业务判断或底层券商接口 |

### R2 的两种已复现故障

**关闭发送卡住。** 使用真实 Capsule.close 与 Runtime._take，仅把启动和进程信号换为假实现：启动报错后进入关闭，假 stdin.drain 永不返回。后续 cold 申请超时，`cold_active=true/owner_alive=true`；强制终止函数一次也没执行。再次取消可以让 cold 释放，但仍没有执行强制终止——不能把这种“旗标解锁、物理收束没做”当成恢复成功。

`Capsule.close` 的 `await send(close)` 在 `wait_for(process.wait(), 5)` 之前；业务的 asyncio.timeout 通常只取消一次，而异常处理继续 await close，并不会自动产生第二个独立清理期限。helper 关闭与 job 最终收尾也需要统一遵守这一原则。

**回收预留泄漏。** 三个空闲资源被 reap_idle 一次性置 busy；第一个 close 注入一次异常，剩余两个从未尝试关闭，三个都留在 pool 中 busy=true，没有 active job owner。第二次 reap 返回 0，无法修复自己遗留的状态。该复现不限于慢进程：一次普通关闭异常就足够。

### R3 为什么会持续失效

`WorkerJobManager._dispatch_loop` 在启动恢复和正常循环中都可能因读取、SQLite 或维护异常退出；`start()` 只检查 `_pump is None`，不检查已结束的 task。一次合成的恢复读取错误后，调用 start 两次仍只有一次恢复尝试，`_processes_recovered=false`、`dispatcher_running=false`。

启动恢复失败时所有 job 都不能调度；若正常循环中退出，新 submit 的即时 `_schedule` 有时仍可推进一波，但队列、后续重试、预热与回收不再有可靠的定时推进，不能以“偶尔还有新 job 启动”判断没有问题。`/healthz` 目前恒定成功，也不能代表 dispatcher 有活性。

### R4：生产已存在的退役缺口

只读库存中，退役候选包括一条 10 月 19 日的旧 UNKNOWN/CREATING operation，以及两条重启取消后仍 CREATING、确定未派发 Fork 的空库存操作。它们没有继续执行的当前 task，却会被 retire 的 `if operation.status == CREATING: continue` 永久跳过。

另外 slot `058f5b825c704630a0feeea8fdb09f5b` 的 operation 已 FAILED，但确实保存了 child `01a17575-ea0c-70d2-b39f-c7cd79228c25`；该 child 不在 `w3_thread_registry` 中，也没有对应 job。直接调用当前 delete 会因未注册而拒绝，因而这条 ABANDONED 无法退役。不能将“注册表中没有”误读为物理 child 没生成。

当前候选共 4 条，**尚不能宣称线上后续可清理行已经被第 5 条饥饿**。但 retire 固定 LIMIT 4 且没有游标，本地加入第 5 条完全可清理记录后，连续三轮仍一次都不处理，已证明未来形成清理阻塞的路径。

## 4. 优化实施方案

### A. 修复等待者取消安全，保留现有准入语义

修改 `startup_admission.py`，覆盖 cold 与 helper 共用的 PriorityGate；同步排查 `jobs.events` 中同形式的 `wait_for(self._condition.wait())`。

1. 用当前任务内的超时包住直接等待，移除 Condition.wait 的隐式子 task：

   ```python
   try:
       async with asyncio.timeout(0.25):
           await self.condition.wait()
   except TimeoutError:
       pass
   ```

2. 将总准入等待期限覆盖初次获取 Condition 及等待循环；该期限只约束进入许可前的等待，不变成模型/实际启动执行期限。
3. 保留上轮“同步释放 owner 后才 await 通知”的逻辑；等待者只能移除自己的队列项，绝不能释放活 owner 的许可。
4. 保留优先级、FIFO 顺序、绑定时提优、压力谓词和 cold=1。不要另造优先队列服务，也不以定时清空 active/Condition 的方式抢锁。
5. `jobs.events` 也使用同任务内的超时；心跳只在锁内设一个 timeout 标记，退出锁上下文后才 yield。其他事件与网络发送同样不能在持锁时进行；不改 SSE 序号/续读合同，不新增消息中间件。

候选验证只实现第 1 点，在内存中替换方法：Windows 与 Linux 的 18 并发复现通过；50 轮共 900 个并发申请，没有残留、异常或 owner 重叠；26 项相关原回归通过。尚未替代正式实现与完整验收。

### B. 将资源关闭做成有限、可重复等待的收尾

主要修改 `capsules.py`、`thread_lifecycle.py`、`process_tree.py`、`jobs.py`；复用已有 identity、process tree 与 15s 清理预留。

1. 每个 Capsule/helper owner 仅创建一份 cleanup task，所有 close/release/reap 调用复用它。关闭开始就进入 CLOSING，停止被当作 READY 或可领取资源；不要让多次 close 并行写同一协议通道。
2. `send(close)` 与优雅退出共享短预算，建议最多 1s；超时、断管道或协议错误均继续 TERM/KILL。复用现有最多 5s TERM＋5s KILL 的身份校验逻辑，总收尾控制在原 15s 预留内，不新增一串可逐层重置的 timeout。
3. 调用者取消只能取消业务等待意图，不能丢弃 cleanup task。已有 `blocking()` 的取消等待思路可参考，但只用于这份**本身有终止预算**的资源收尾；不要对无界任务简单 shield 后永远等下去。重复取消后仍应完成物理 owner 核验，再传播 CancelledError。
4. 批量 reap/release 的预留也要有 owner。按资源逐条 try/finally：已结束则移出 pool；本条未确认结束则标记本条 quarantine；还没执行关闭的其他预留必须复原。一次异常不能把整批资源永久 busy，也不能让整批后续收尾都被跳过。复用现有 reaper task 即可。
5. job 最终收尾与 Capsule 回收分开核对：业务结果若已写 result receipt，保留它；收尾/诊断落盘失败不能重跑已成功交易 turn。清理正常时原子释放调度身份；物理 owner 未确认退出时保留该 identity 的隔离记录，不把整个 Worker 的常态工作都标成同一个失败。
6. 无法结束的 owner 保留 identity/PID/start_ticks、cleanup 状态与错误，不能伪报 CLOSED 或直接遗忘。若它仍处于未收束的物理冷启动，不能为了可用性允许第二个冷启动绕过 cold=1；进入明确的基础设施异常处理。使用现有 Worker 监督/发布收束路径处理不可恢复进程，不根据普通启动慢、压力或单个业务失败重启整个 Worker。

边界：不承诺解决内核不可中断 I/O 等宿主机故障；要求其从“无限 await、服务还显示健康”变成有限收尾、可定位且可操作的异常。不得以无界 shield、增加启动次数或清零业务截止时间冒充鲁棒性。

### C. 给现有 dispatcher 加小型监督，不重建调度系统

修改 `jobs.py` 和 `app.py`。

1. `start()` 识别 None 与已结束的 pump；读取旧异常后才能重建，`_closed=true` 不重建。所有操作在同一事件循环中完成，防止多个调用同时建两个 pump。
2. pump 的启动恢复与单轮调度采用异常边界：瞬时读取/SQLite/维护失败记录错误并按 1/2/5/10s 上限退避，随后继续推进。只重试失败的基础设施阶段，不取消已有健康 job，不重新提交模型、不重建 operation 身份。
3. `_processes_recovered` 仅在旧 owner 确认收束后置 true。确认仍活着的旧进程是隔离状态；不能 catch-all 后放行业务。临时 I/O 恢复后应自行再次核验，不能永久停在一个已结束 task。
4. 普通 GC/观测维护错误与核心调度分离：继续使用现有后台 task/重试位置，不能由可选维护的异常杀死 pump。记录 last_error 与 last_progress；日志和诊断库也不可写时，保留可读取的内存错误并限频记录，不产生数据库失败的自激重试。
5. 保留 `/healthz` 的存活职责，但明确不可用的 dispatcher 要在现有 readiness/resources 中返回异常及原因；正式发布验收也要看这些字段。不要以 READY=0、正常 PSI 暂停或队列非空直接触发整进程重启。Docker 的 unhealthy 本身不保证重启，不能把它当恢复实现。

### D. 收束无主记录与公平退役

修改 `w3_ready.py`、`thread_lifecycle.py`，复用现有 prepare_assets/retire 周期，不增加新的常驻服务。

1. 区分 operation 的真实 task 是否仍存活、generation、已知 child 与派发状态，而不单看 CREATING 字符串。
2. **确定未派发＋无执行 owner 的未绑定空库存**：结束该库存操作并退役；不新 Fork、不提交模型。绑定业务仍按原预算与既有恢复规则处理。
3. **已知 child 但缺注册**：凭原 operation 的 parent/run/cwd 与持久 receipt 核对并补齐原 child 的生命周期归属，再交给现有 delete；不得给未知路径开任意删除接口，不得为方便清理把该 child 分配给新 Case。
4. **已派发但 child 不确定／旧 NULL UNKNOWN**：只做原身份的有界核验，查询失败保留不确定性，不能当作没创建。无必要重新 Fork；失败记录可进入待核验隔离并按退避再检查。没有新证据就不无限重置 full-query 次数或擦除旧身份。
5. 小批退役改为有游标/公平扫描；仍限制每轮实际删除数量，遇到待核验行继续检查后面的可退役记录。共享 helper 的 quarantine 也按 owner 核验退出后解除；进程仍活着时不另建覆盖 owner。
6. 存量清理先输出 dry-run 清单，仅处理已能证明确切归属和终止状态的资源；原 Case 决策、意图和审计记录不重跑、不删除。本轮产出的悬挂清单仅是证据，未执行清理。

## 5. 最小观测补充与验收

不新增常开全量事件探针。复用 `/v1/resources`、现有 operation/slot/owner 回执，增加少量标量：

- cold 与 helper gate：active owner、Condition locked、队列数与最老等待时长。
- dispatcher：running、最后成功推进时点、恢复阶段、最近异常及退避到期时间。
- 收尾：CLOSING/quarantine 数、最老年龄、原 identity、最后收尾阶段；不要保存原始工具内容或 stderr。
- 退役：可处理、待确认、无 owner CREATING 的数量及最老时间。

实现时只对**已无活 owner/cleanup task 却持续占用、dispatcher 意外停止、实际清理预算耗尽**进行告警与恢复；不因为模型 turn 长或普通资源压力就强行释放许可/重启服务。

### 必须新增的故障回归

| 场景 | 必须证明 |
| --- | --- |
| 18 并发、等待者连续两次/多次取消，穿插正常释放和超时 | 所有任务收束，无 Condition 子任务残留；新申请仍受期限约束；最多 1 个 owner |
| 等待初始锁、进入许可前后、释放通知时取消 | 本人队列/owner 收束，不误释放他人的许可 |
| 心跳到达后暂停消费者、慢发送、断线及重复取消 | 事件流不持全局通知锁；无关 job 的 submit、启动与收尾不受其阻塞 |
| 20 并发且压力谓词暂停/恢复、绑定时提优 | 原优先级与公平性保持；正常驻留 turn 不受 cold 排队牵连 |
| stdin 不读、close 断管道、子进程不优雅退出 | 在清理预算内走到实际 TERM/KILL，确认退出才报告完成 |
| close、reap、Worker shutdown 重叠并重复取消 | 同一 owner 只执行一份 cleanup；其他资源无无人持有的 busy；无误杀其他进程 |
| 第一份回收失败，后两份健康 | 后两份仍能回收或恢复可领取状态，故障只隔离到对应资源 |
| 一次恢复读取失败；一次正常调度/可选维护失败 | 不靠新 HTTP 请求，pump 自动再次推进；不重复运行已有 job |
| dispatcher 无法恢复、旧 owner 确实活着 | readiness 明确不可用，不伪成功；不同时产生两个 dispatcher/冷启动 |
| 前 4 条待确认、第 5 条可清理；child 已知但 registry 缺失 | 第 5 条可推进；原 child 经归属核验退役；未知 child 不重复 Fork、不误删 |
| 交易结果已落库但清理/后处理失败 | 恢复只接回缺失阶段，不重新产生交易意图 |

顺序：先在本地故障矩阵完成 R1—R3，再在私有进程/临时目录验证真实进程协议；这些验证无需模型 turn。正式上线另行授权后，检查 source/image、dispatcher、gate、READY、自然 Case 交易与后处理落库；观察全部失败与 pending，不以一条成功或空窗口零失败代替可靠性验收。生产不注入取消/kill 故障。

### 本轮已完成验证

- 当前基线相关 8 个测试文件：**77 passed**，覆盖准入、boot、生命周期恢复、ready、writer handoff、资源预算、行情和资源生命周期；1 条既存依赖弃用 warning。
- 当前实现 R1 的单次取消对照通过，重复取消复现失败；旧释放漏洞可复现，当前释放修复对照通过。
- R1 内存候选：**26 passed**；额外 50×18=900 个并发申请无异常、无残留、无 owner 重叠。Linux 同版独立测试同样通过。候选测试有 pytest 提前导入 warning，不影响断言，不记成生产代码已修复。
- R2/R3/R4 的失败复现使用当前生产函数，仅替换外部 I/O 或临时状态；不据此宣称生产正在发生所有这些失败。

## 6. 范围与证据

本次不建议改 Prompt/Skill、模型/effort、交易策略、冷启动并发、内存熔断阈值、既有交易有效期或正常 Fork 身份协议；也不开发历史失败 Case 的批量重跑。优先解决异常后失去活性、资源无法收束、后台 owner 消失的问题。

源码定位：`startup_admission.py:35/77`、`capsules.py:174/260/424/452`、`thread_lifecycle.py:134/285/793`、`jobs.py:176/210/390/473`、`w3_ready.py:169/382`、`app.py:98/366`。路径均在 `src/doxagent/codex_worker/`。

证据目录：`eval/w3_robustness_audit_20261026/`。

- `incident-origin.json`：首个故障窗口 71 个 operation、锁错误 job、缺注册 child 核对。
- `production-inventory.json` / `production-runtime.json`：发布版本、源码 SHA、自然流量与悬挂状态、真实资源状态。
- `gate-linux-current-double.json` / `gate-linux-candidate-double.json`：Linux 当前/候选及新请求超时对照。
- `legacy-release.json` / `current-release.json`：上轮修复前后对照。
- `event-stream-backpressure.json`、`cleanup-drain.json`、`reaper-reservations.json`、`dead-dispatcher.json`、`retirement-head-of-line.json`：其他失败复现。
- `candidate-stress-900.json`、`baseline-tests.xml`、`candidate-tests.xml`：压力与回归结果。
- `gate_cancellation.py`、`fault_injection.py`、`candidate_regression.py`：可重跑的私有故障与候选验证；生产只读脚本单独命名 `_remote.py` / `production_inventory.py`。

故障脚本在检测到合成锁死后只退出它自己的解释器，避免审查脚本也无限挂起。远端复现仅创建该命令的私有 Python 对象，不操作运行中 Worker 的锁或进程；没有用生产 Case 做实验。本轮未实施上述 A—D，也没有自动改变生产运行状态。
