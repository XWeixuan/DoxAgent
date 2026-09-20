# DoxAgent 4C16G 资源安全与弹性执行架构修订方案

日期：2026-09-16  
状态：Revised Architecture Proposal，仅供评审；本轮不实施代码、不修改生产配置、不部署远端。  
替代关系：本版本完全取代此前以固定 slot、reserved/shared/background 分区和 weighted capacity 为核心的方案。

## 1. 决策摘要

本轮重构只解决一个核心问题：现有 Resource Guardian 把资源保护变成了全局任务准入，导致资源健康时仍然长时间阻塞业务。新方案不再用另一套复杂调度平台替换它，而是尽可能删除错误控制层。

最终职责收敛为：

- **业务 Scheduler / Workflow**：管理依赖、幂等、deadline 和业务状态。
- **现有 durable queue**：保存待执行工作、吸收突发并支持崩溃恢复。
- **Elastic Execution Dispatcher**：机器健康时积极启动 runnable work；只控制启动速度和调度顺序，不维护全局固定并发槽。
- **Execution Capsule / 局部执行器**：真正运行 Codex、Browser 或本地重计算，并负责进程归属和清理。
- **Linux cgroup**：以 `MemoryHigh=14G`、`MemoryMax=15G` 提供最终物理边界。
- **Safety Controller**：只判断 `NORMAL / PRESSURE / CRITICAL`，真实压力出现时减少后台 fan-out；不审批单个 Job。

必须同时成立的原则：

1. 默认并行，而不是默认限制。
2. 根据真实资源压力收缩，而不是根据预测内存进行准入。
3. Realtime 获得调度优先权和压力下的让路权，不获得永久闲置的保留槽。
4. Codex、LLM、网络等待型工作允许高逻辑并发，不设全系统固定总 capacity。
5. 只有存在明确物理瓶颈的局部阶段，才允许使用简单 semaphore。
6. QUEUED Job 不启动线程、SDK、MCP、Browser page，也不持有父 Workflow 资源。
7. Safety Controller 故障不能成为业务停摆原因。
8. 任何新增机制如果删除后系统仍能安全运行，就不应加入第一版。

## 2. 业务目标与取舍

### 2.1 目标

1. 面向 4 核 16GB 服务器，支持 15–25 个 ticker 同时处于监测、交易、Realtime Case、Initialization 或 Maintenance 生命周期。
2. Message Bus、W1/W2/W3 和 Realtime Case 优先保留时间价值；后台任务不能制造小时级排队。
3. 在资源健康时，系统能够自然提升到 10、15、20 个甚至更多同时存在的 I/O/LLM execution，而不是永远受制于 4、6 或 8 的人为上限。
4. 高峰期允许内存和 CPU 保持高利用率。系统只在出现持续 PSI、swap thrashing、极低 MemAvailable、逼近 hard limit 或 OOM 信号时降载。
5. 即使发生真实压力，也先停止新的后台扩张，再回收 idle execution，最后才终止一个可恢复的后台任务。
6. 等待、重启和基础设施恢复不消耗业务 attempt，不改变业务 `PARTIAL/DEGRADED/UNRESOLVED` 语义。

### 2.2 明确取舍

- 本方案接受资源健康时较高的 RSS 和较多并发进程，以换取多 ticker 吞吐与时效性。
- 本方案不追求“内存永远离上限很远”；`MemoryHigh` 附近运行并不自动等于异常。
- 本方案不能保证 25 个 CDECR Bulk 或 25 个 Chromium 同时运行；它保证 25 个 ticker 的业务生命周期互不形成全局锁，真正重阶段只受其局部物理约束。
- 本方案接受 Safety Controller 在极端故障时不够精细，优先保证行为简单、可预测、可排障。

### 2.3 非目标

- 不引入 Kubernetes、Celery、Redis 或新的中央资源数据库。
- 不新建覆盖全部业务的统一 `execution_jobs` 平台；优先演进现有 Worker/Runtime/Initialization durable state。
- 不通过降低模型质量、删除研究阶段或缩短合理 timeout 换取资源。
- 不让所有网络请求、SQLite 操作和轻量协程竞争同一种全局许可。
- 不做未来内存预测、per-job MiB reservation、Docker peak 借用或动态 memory resize。

## 3. 从旧方案中继续删除的设计

以下内容不进入修订后的架构：

| 删除项 | 删除原因 | 修订后的处理 |
|---|---|---|
| Codex 总 weighted capacity = 6 | 将估算值固化成系统吞吐上限 | NORMAL 时持续消费 runnable jobs，不设总并发槽 |
| 2 realtime reserved / 3 shared / 1 background guaranteed | 固定分区可能闲置，也会制造新瓶颈 | 两级调度顺序；压力时后台让路 |
| root/subagent weight | 又形成预约账本和原子多槽申请 | subagent 属于已启动 execution 的内部行为，直接计入真实 footprint |
| Browser realtime/background 固定槽 | 用业务类别替代真实浏览器压力 | 仅在浏览器进程本地使用经实测需要的简单 semaphore |
| 全局 Execution Pool Manager | 新中央组件会成为新的单点 Gate | 各执行域保留自己的薄 dispatcher，共享最少状态定义 |
| 统一 Pool lease/跨池资源顺序协议 | 第一版复杂度高且易产生嵌套锁 | 一个阶段只执行一种重资源；阶段完成后落盘再推进 |
| 四级 GREEN/AMBER/RED/EMERGENCY | 状态过细、阈值与恢复规则过多 | 收敛为 NORMAL/PRESSURE/CRITICAL |
| ticker round-robin、8:1 dispatch 等复杂公平算法 | 需要长期调参与解释 | Realtime 排前；后台只用最长等待保护 |
| 2048 作为执行准入容量 | durable queue 不应因为人为总量制造资源等待 | 只设置存储安全高水位，不作为执行并发模型 |
| 每个资源都建 Pool | 为架构统一而增加无效层次 | 只有真实稀缺资源才有局部控制 |

同时删除旧 Guardian 中的：

- `WORK` 静态内存预约；
- `heavy`、`batch` 和跨 ticker/workflow 互斥；
- `priority waiter` 全局抢占；
- Guardian socket 的逐任务 `acquire/release/renew`；
- 父 Initialization/Maintenance/Projector 长期 reservation；
- Docker limit 未使用空间的“已承诺内存”计算；
- 动态 `docker update --memory`；
- 单次压力事件触发全局长时间 cooldown。

## 4. 保留的必要能力

删除资源准入不等于删除业务与执行安全：

- durable job、幂等 job ID、原子状态推进和重启恢复；
- 同一 Codex thread 只允许一个 active turn；这是上游会话一致性约束，不是资源 Gate；
- execution capsule 的进程树归属、interrupt/TERM/KILL 和清理确认；
- result receipt 原子提交与 Worker 重启后的结果认领；
- 单 ticker duplicate initialization、revision CAS 等业务一致性规则；
- Message Bus provider cadence、timeout、dedupe 与 source-local degradation；
- MCP 工具自身已有且经过验证的局部并发保护；
- service/capsule cgroup、PID 边界和 OOM 事件；
- 队列等待时间、实际执行时间和外部 provider 时间分开观测。

## 5. 简化后的顶层架构

```text
Business Scheduler / Workflow
  依赖、deadline、幂等、业务状态
                │
                ▼
Existing Durable Jobs
  crash recovery、burst absorption、ordering
                │
                ▼
Elastic Execution Dispatcher
  realtime-first、简单 aging、launch ramp
                │
                ▼
Execution Capsule / Local Executor
  Codex / Browser / CPU-heavy / ordinary I/O
                │
                ▼
Linux cgroup hard isolation
  MemoryHigh=14G / MemoryMax=15G

Safety Controller（旁路）
  真实压力 NORMAL / PRESSURE / CRITICAL
        └──────► 只影响新的 heavy launch fan-out
```

不存在一个中央组件判断“这个 ticker 或 workflow 有没有资格运行”。Dispatcher 只回答两个问题：

1. runnable jobs 中哪个先启动；
2. 当前是否应该继续扩大新的 heavy execution fan-out。

## 6. Durable Job：只保存状态，不代表等待许可

### 6.1 不新建统一中央队列表

第一版复用现有 Worker JobStore、Runtime Journal、Initialization Repository 和已有 outbox。只在确有缺口的存储中增加最少字段，不迁移为一个全局 `execution_jobs` 权威表。

最小必要信息：

- 稳定 `job_id/idempotency_key`；
- `realtime` 或 `background` 两级类别；
- `queued_at`、可选 `deadline_at/ready_at`；
- `QUEUED/RUNNING/CLEANING/SETTLED/CANCELLED`；
- 仅在真实启动后记录的 process/capsule owner；
- attempt、receipt、错误和清理结果。

不增加：

- resource reservation；
- slot、weight、shared/reserved partition；
- ticker/batch 资源互斥字段；
- 预计 RSS 或未来峰值；
- 等待期间的 execution lease。

### 6.2 QUEUED 的严格语义

QUEUED Job 只有 durable record，不得：

- 占用 Python/OS thread；
- 创建长期存活的 asyncio task 只为等待；
- 启动 Codex SDK、MCP 子进程或 Chromium page/context；
- 持有父 Workflow lease 或 heartbeat；
- 预约内存、PID 或未来 capsule；
- 开始计算业务 execution timeout。

真正 dispatch 后才创建 execution capsule，并从此时计算 attempt timeout。

### 6.3 Queue 的边界

Queue 用于崩溃恢复、突发吸收、排序与依赖解耦，不用于维持人为低并发。NORMAL 下 dispatcher 应持续消费 runnable work。

只保留面向磁盘/数据库安全的高水位保护：达到高水位时对产生最快的来源做明确 backpressure，既有 Job 不丢弃，也不记为业务失败。高水位根据实际记录体积与磁盘预算配置，不与服务器可同时执行多少任务绑定。

## 7. Elastic Execution Dispatcher

### 7.1 不是 Slot Allocator

Dispatcher 不维护 `capacity/reserved/shared/guaranteed/weight`。NORMAL 状态下不存在“当前已用 6 个，所以第 7 个必须等待”的判断。

其正常循环非常简单：

1. 从现有 durable store 获取 runnable realtime jobs；
2. 再获取 runnable background jobs；
3. 按 launch ramp 逐批启动；
4. 每批启动之间读取新的 Safety 快照；
5. execution 完成后清理归属并推进 durable 状态。

已启动的 I/O/LLM execution 不因冷启动节流长期占用任何“slot”。只要 Safety 仍为 NORMAL，后续批次继续启动，逻辑并发可自然增长。

### 7.2 只有两级调度

第一版只有：

- `realtime`：Message-driven W1/W2/W3、Realtime Case 和明确的实时恢复；
- `background`：Initialization、Maintenance、Sweep、Research、历史补全。

规则：

- 每轮先取 realtime，再取 background；
- NORMAL 下 realtime 有积压也不永久停止 background；
- 若最老 background 超过一个可配置等待阈值，下一启动批次至少包含一个 background；
- 不增加 urgent-background、数字 priority、ticker score、8:1 配额或固定保底槽；
- 同一 thread 排他在 dispatch 时单独检查，但不影响其他 thread/ticker。

后台公平只解决“不能永久饿死”，不追求完美公平算法。

### 7.3 Realtime 的优先权和让路权

Realtime 不拥有固定资源分区，而是拥有：

1. runnable 排序优先；
2. PRESSURE 时仍可继续按较慢 ramp 启动；
3. PRESSURE 时新的 background heavy fan-out 先暂停；
4. CRITICAL 回收时 background execution 优先成为候选；
5. Message Bus、Control、receipt 与 Runtime 轻量推进始终不进入 heavy launch gate。

资源健康时，Initialization/Maintenance 和 Realtime 可以充分并行；不会因为 realtime 的存在让后台固定只剩一个槽，也不会为未来可能到来的 realtime 长期空置容量。

### 7.4 Launch ramp 只控制瞬时启动冲击

需要控制的是 SDK/MCP/Chromium/本地大计算同时冷启动产生的瞬时峰值，而不是长期 execution 数量。

首轮 4C16G canary 可从下列**启动参数**开始：

- Codex/MCP：每个 launch wave 最多启动 3 个新 capsule；wave 间至少取得一次新的 Safety 采样；
- 同时进行的 Codex cold-start handshake 暂以 3 个起步；handshake 完成后立即释放启动许可；
- Browser：新建 context/page 使用独立较慢 ramp，初始每 wave 1 个；
- CPU-heavy：仍按第 8 节的局部限制启动。

这些数字不是总并发上限，也不是架构契约。升级后通过实测快速提高 wave 大小或缩短间隔；目标是在机器健康时验证并允许超过 6 个、达到 10–20+ 个 I/O/LLM execution 同时存在。

### 7.5 Dispatcher 故障语义

- dispatcher 重启后从既有 durable store 恢复，不重放已成功 receipt；
- dispatcher 不可用只影响该执行域，不阻断 Message Bus 或其它执行域；
- 不引入新的跨服务分布式锁或全局 leader；
- 同一执行域如已有单 writer 约束，继续沿用现有进程所有权机制。

## 8. 各执行域采用不同的最小控制

### 8.1 Codex / LLM / 网络等待型任务

- 使用 Elastic Dispatcher，无固定总并发。
- NORMAL 下持续扩大 runnable execution，直到队列消化或出现真实压力。
- 只限制 cold start 速率；已启动且大部分时间等待远端 I/O 的 turn 不占固定 slot。
- W1/W2/W3 排在 Initialization/Maintenance 之前，但后台可在 NORMAL 下同步推进。
- subagent 不申请额外 weight；其实际进程与内存由 capsule/cgroup 观测。

### 8.2 CPU-heavy

CDECR Bulk、大规模本地解析、embedding/reindex 等确实受 4 核约束，可以使用简单局部 semaphore。

首发原则：

- 本地持续 CPU-heavy 默认并行 1；
- 只限制具体函数/子进程，不锁住整个 Initialization 或 Maintenance；
- 网络等待、Codex 等待和 checkpoint 不持有该 semaphore；
- 只有 CPU 利用率、CPU PSI 和 realtime 延迟实测健康后才尝试并行 2；
- 该 semaphore 不跨资源类型、不跨业务阶段、不参与全局账本。

这里保留 1 的初值是 4 核真实物理约束，不推导为全系统只能并行一个重 workflow。

### 8.3 Browser / Chromium

Browser 可以有进程本地、仅保护 Chromium footprint 的简单 semaphore，但必须由实测决定，而不是建立 realtime/background 固定分区。

- Message Bus 和普通 HTTP/RSS/API fetch 不占 Browser semaphore；
- 只有实际 page/context 导航、渲染和复杂抽取受约束；
- Realtime browser job 排在 background browser job 前；
- PRESSURE 停止创建新的 background page/context，并回收 idle context；
- 已健康运行的 page 不因普通 PRESSURE 被强杀；
- semaphore 不跨 ticker/workflow，不与 Codex/CPU 锁嵌套。

4C16G canary 可以从最多 4 个活动 page/context 观察起步，但该值必须在 Chromium RSS 实测健康后上调或下调，不能演变成全局业务 Gate。

### 8.4 普通 I/O、SQLite、Projector

- 不新建全局 I/O Heavy Pool。
- 普通 point read/write、receipt fsync、Message Bus 落库照常运行。
- snapshot、ZIP、备份和大批投影继续使用已有局部 DiskBudget/批大小控制；只有实测 I/O 饱和时才调整。
- Projector 删除 Guardian reservation 与动态 peak lease，但不需要为此引入新的中央资源池。

### 8.5 Message Bus、Control、API、Delivery、Executor

- 不进入 heavy dispatcher 准入路径；
- Message Bus 即使正文 enrichment 延迟，也先完成消息 durable publication；
- Control、取消、health、receipt、幂等交付和只读状态在 PRESSURE/CRITICAL 下仍需可用；
- 各服务只受自身 cgroup/PID 边界与原有业务并发约束。

## 9. Safety Controller：三态异常保护器

### 9.1 职责边界

Safety Controller 每 2 秒左右读取 Linux/cgroup 真实指标并发布一个小型只读快照。它不读取业务数据库，不解析 ticker/workflow/job，不调用 Docker resize，不分配许可，也不提供逐任务 socket RPC。

观察项限定为：

- host `MemAvailable`；
- 顶层 DoxAgent cgroup `memory.current/high/max/swap.current/events`；
- memory PSI `some/full`；
- `pswpin/pswpout` 增量或等价 swap I/O rate；
- OOM/OOM-kill 事件；
- CPU PSI 仅供 CPU-heavy 局部控制参考。

`inactive_file`、anon/file/slab 可以记录用于排障，但不进入任务预算计算。

### 9.2 三个状态

| 状态 | 含义 | 行为 |
|---|---|---|
| NORMAL | 没有持续真实压力 | 不限制正常业务并发；realtime-first；background 正常 fan-out |
| PRESSURE | 持续回收/PSI/swap/低可用内存，或已进入 14G soft boundary 附近且伴随压力 | 暂停新的 background heavy launch；realtime 使用较慢 ramp 继续；回收 idle capsule/context；不杀健康运行任务 |
| CRITICAL | 已逼近 15G hard boundary、OOM 增长或宿主出现失控风险 | 暂停所有新的 heavy launch；保护 Message Bus/Control/Runtime；必要时终止一个最大、最低优先级、可恢复的 background execution |

### 9.3 4C16G 首发触发条件

阈值只作为 canary 初值，不形成复杂评分模型：

进入 PRESSURE，满足任一持续条件：

- DoxAgent `memory.current >= 14G`，并同时存在持续 memory PSI 或 swap I/O；
- Host `MemAvailable < 1G`，并同时存在持续 memory PSI 或 swap I/O；
- cgroup `memory.events high` 持续增长且 PSI 同步升高。

进入 CRITICAL，满足任一条件：

- DoxAgent `memory.current >= 14.75G` 持续数秒；
- OOM/OOM-kill 计数增长；
- Host `MemAvailable < 384MiB` 且存在 PSI 或 swap thrashing。

这套判断有意不把“内存利用率高”单独作为 PRESSURE。只有非常接近 15G hard boundary 时，current 本身才足以构成 CRITICAL 风险。

恢复只保留简单迟滞：

- CRITICAL 条件消失并持续健康 30 秒后退到 PRESSURE；
- PRESSURE 条件消失并持续健康 60 秒后回到 NORMAL。

不增加四级状态、分 Pool effective capacity、每 30 秒恢复一个 slot 或 10 分钟全局 cooldown。

### 9.4 CRITICAL 回收规则

- 一次只选择一个明确归属、可恢复、最低优先级的 background execution；
- 优先选择实际 RSS 最大者，而不是根据静态估算；
- 没有新的 OOM/持续恶化证据时不循环连续杀任务；
- 不终止 Message Bus、Control、receipt writer 或交易执行状态提交；
- 不优先终止 realtime turn；只有已经没有 background 候选且宿主仍在失控时，才由 cgroup/OOM 作为最终边界，第一版不设计复杂 realtime 抢占协议。

### 9.5 Controller 故障语义

- 快照过期或 Controller 退出不能返回 `GUARD_UNAVAILABLE`、不能阻断所有 Job；
- dispatcher 对过期快照告警并忽略软件降载状态，回到默认 NORMAL 行为；
- systemd 负责重启 Controller；14G/15G cgroup 边界始终有效；
- 选择 fail-open 是有意的业务取舍：宁可依靠 cgroup 最终保护，也不能因监控进程故障再次让全系统长期停摆。

## 10. 4C16G cgroup 与服务边界

### 10.1 顶层固定边界

| 配置 | 值 | 说明 |
|---|---:|---|
| DoxAgent `MemoryHigh` | **14 GiB** | soft boundary；由内核回收/节流，不是业务拒绝线 |
| DoxAgent `MemoryMax` | **15 GiB** | hard boundary；避免 DoxAgent 拖死整机 |
| DoxAgent `MemorySwapMax` | **1 GiB** | 仅作短时缓冲；持续 swap I/O 触发 PRESSURE |

这组边界按用户确认值执行。升级后必须确认 Gateway、operator Chromium、桌面进程和 Docker 是否位于预期 cgroup；不在父 cgroup 内的进程需要单独观测，不能在预算中假设它们为零。

### 10.2 服务 hard limit 初值

本节原来的固定服务上限会在父级仍有充足资源时制造局部 OOM，已经由
`service_cgroup_elastic_memory_cdecr_executor_4c16g_20260921.md` 全面取代。

新口径为：顶层 14G/15G/1G 是共享容量边界；Codex、API、Message Bus、Runtime、
Content Enrichment 和 Projector 等弹性执行域使用与父级等宽的 15g RAM / 16g
RAM+swap 边界；轻量稳定服务只保留足够宽松的故障熔断线。CDECR 使用独立常驻
executor，初始 6g RAM / 7g RAM+swap，Initialization controller 不再承载其重型
子进程、冻结快照和 Delta 编译。

这些 limit 不预分配资源，也不相加形成准入预算。所有子级额外 swap 共同受父级
1 GiB 上限约束。逐服务数值、进程归属、恢复协议和验收矩阵以 2026-09-21 方案为准。

不建议用严格 CPU quota 把 I/O 型服务压死。大多数服务使用 CPU weight/正常竞争；只有持续 CPU-heavy 子进程可设置约 2.5 核的局部上限，为 Message Bus、数据库和控制面保留调度机会。

## 11. 业务流程改造

### 11.1 Initialization

- 删除整个 `run_once()` 外层 Resource Guardian acquire/renew/release；
- 多个 ticker 的 controller 可以同时推进 ready node 和依赖状态；
- 等待 Codex、dependency、checkpoint 时只保留 durable 状态，不占长期线程或资源；
- Codex 子阶段进入 background durable job，由 Elastic Dispatcher 启动；
- CDECR 只在真正本地计算期间取得局部 CPU-heavy semaphore；
- 同 ticker duplicate/revision 规则继续由业务 Repository 保证。

### 11.2 Maintenance 与 Sweep

- 删除 MAINTENANCE/SWEEP 父任务的 512MiB reservation 和 Heavy Batch Mutex；
- MU Maintenance、RKLB Initialization、NVDA Maintenance 和其他 ticker workflow 默认并行；
- O2/O3/O4 的 Codex 子阶段标记 background，不创建跨 workflow 资源锁；
- Closed Sweep 的 Case 状态 durable 化，轻量阶段可并行推进；不再让一个 wave 的慢 Case 串行阻断其余 Case；
- 背景任务最长等待超过阈值后获得下一 launch wave 的一个启动机会，仅在 NORMAL 生效。

### 11.3 Realtime W1/W2/W3

- Realtime Case 入库后立即成为 active logical Case，不先申请内存或 slot；
- W1/W2/W3 各自提交 realtime durable job；dispatch 前不创建 SDK/MCP/capsule；
- W1→W2→W3 依赖按 Case 状态推进，不受其它 ticker 的维护/初始化 batch 影响；
- queue time、cold-start time、model/provider time 分别记录；
- 业务 timeout 从真实 execution start 计算，排队不消耗模型预算；
- Runtime 不使用一个 OS Thread 阻塞等待远端模型完成。

### 11.4 Message Bus 与正文

- polling、normalization、dedupe、cursor 和 durable publication 不进入 heavy launch gate；
- 正文/Browser enrichment 可排队为独立 Job，但消息先发布并进入 Runtime；
- enrichment 延迟使用现有 `PENDING/PARTIAL` 等语义，不阻断 W1/W2 的时间价值；
- PRESSURE/CRITICAL 只影响新的后台 Browser fan-out，不停止主消息循环。

### 11.5 Projector、Delivery、Executor

- Projector 删除 Guardian reservation 和 Docker dynamic peak；保留简单批大小/DiskBudget；
- Delivery、Executor 不进入资源池或 Safety 准入；
- CRITICAL 下仍允许 receipt、outbox、幂等交付和交易状态提交完成；
- 不为这些服务新建统一 I/O Pool。

## 12. 防死锁规则

不需要复杂全局资源顺序，只执行一条规则：

**一个 durable 阶段只做一种重资源工作；阶段完成并落盘后，父 Workflow 再提交下一阶段。**

因此禁止：

- 持有 Browser semaphore 等 Codex；
- 持有 CPU-heavy semaphore 等远端模型；
- 父 Workflow 预占未来 Codex/Browser/CPU 资源；
- QUEUED Job 持有 execution owner/heartbeat；
- 一个局部 semaphore 扩大为跨 ticker、跨 workflow 的锁。

## 13. 可观测性与 SLO

### 13.1 最小必要指标

不构建庞大的资源账本，只记录排障和校准需要的事实：

- queued/running/cleaning 数，按 realtime/background 分类；
- oldest queue wait 与 p50/p95/p99 dispatch delay；
- concurrent active executions 的实际值，不把它当 capacity；
- 每轮 launch 数、cold-start 耗时和 launch ramp 暂停原因；
- capsule/process RSS、peak RSS、PID/FD、cleanup time；
- Message Bus admission latency、W1/W2/W3 各段 queue/provider time；
- Safety state、触发指标、cgroup current/high/max/events、PSI 和 swap rate；
- CRITICAL 回收对象、实际 RSS、恢复结果。

不再暴露或依赖：

- reserved/shared/background slot 使用量；
- predicted memory、outstanding reservation、borrowed peak；
- batch conflict、global priority waiter；
- effective pool capacity。

### 13.2 业务 SLO

在 NORMAL、外部模型服务正常时：

- Message Bus 单条内部接入并落 durable inbox：p95 < 5 秒；
- Realtime Case 到 W1 dispatch：p95 < 15 秒，最大目标 < 60 秒；
- W1 完成到 W2 dispatch：p95 < 10 秒；
- W3 durable 后到 dispatch：p95 < 15 秒；
- Initialization/Maintenance 与 Realtime 可同时推进；
- 任何任务不得因软件资源 Gate 出现小时级等待；
- 25 ticker burst 中实际 Codex/LLM 并发应能在资源健康时超过旧 6 并发，逐步验证 10–20+。

外部 provider 限流或故障必须与本地 resource wait 分开统计，不能互相掩盖。

## 14. 实施顺序

### Phase 0：清理前一版未完成实现

当前工作区可能仍保留上一版固定 slot/四态 Safety 的中止改动。正式开发前先逐文件审计：

- 不把未完成代码视为既定实现；
- 删除或改写 fixed capacity、reserved/shared/background、weight 与四态 overlay；
- 保留其中已经正确完成的“移除父 reservation”“14G/15G cgroup”等独立部分，前提是重新验证；
- 在开始新实现前形成明确 diff，避免两套策略残留并存。

### Phase A：先建立物理边界和观测

1. 配置顶层 14G/15G/1G cgroup、弹性域等宽边界与宽松故障 hard limit。
2. Safety Controller 先以只读 shadow 方式发布 NORMAL/PRESSURE/CRITICAL。
3. 增加 queue delay、active execution、RSS/PSI/swap/OOM 遥测。
4. 验证 Controller 停止不会阻断任何业务。

退出条件：指标可信，cgroup 边界有效，软件保护器尚不参与任务准入。

### Phase B：删除旧 Guardian 和父 Workflow 资源控制

1. 删除 Initialization、Maintenance、Projector 的 resource acquire/renew/release。
2. 删除 Worker 的 Guardian admission、resource token 和 batch 推导。
3. 删除旧 Guardian socket、WORK/ALLOWED_WORK/heavy/waiter/reservation/dynamic resize。
4. 保留业务幂等、same-thread、capsule cleanup、receipt 和现有 durable store。

退出条件：不同 ticker/workflow 在 NORMAL 下不存在资源层互斥，也不存在 Guardian 不可用导致的拒绝。

### Phase C：Elastic Dispatcher 与无等待线程

1. 在各现有 durable store 上增加最小 realtime/background 排序字段。
2. Worker 使用 realtime-first + background aging + launch ramp；不实现 slot capacity。
3. Runtime/W3 改为 durable continuation，等待模型时不占 ThreadPool worker。
4. Initialization controller 允许多个 ticker 轻量推进，重阶段独立提交 Job。
5. Browser/CDECR 只在具体调用点保留局部 semaphore。

退出条件：100 个 Codex Job 不丢失，NORMAL 下并发可以持续增长并超过 6；QUEUED 不占执行资源。

### Phase D：启用压力降载并校准 4C16G

1. PRESSURE 先暂停新的 background heavy launch，realtime 减速但继续。
2. CRITICAL 暂停新 heavy launch，并验证单个 background 回收。
3. 运行 15、20、25 ticker 分级 canary，观察 RSS、PSI、swap、queue delay 和业务 SLO。
4. 优先调大 launch wave、缩短 wave 间隔，让健康机器充分使用资源；只有真实压力证据才回调。

退出条件：25 ticker 下 Message Bus/W1/W2/W3 满足 SLO；系统能在健康时超过 6 并发；真实压力能局部、可恢复地降载。

## 15. 文件级预计改造范围

本节只描述后续实施范围，本轮不修改这些代码：

| 范围 | 预计文件 |
|---|---|
| Codex elastic dispatch | `codex_worker/jobs.py`、`job_store.py`、`app.py`、`capsules.py` |
| Runtime continuation | `persistent_runtime_v2/coordinator.py`、W3 effect dispatch/repository |
| Initialization controller | `ticker_initialization/service.py`、`cli.py`、repository claim |
| 局部 Browser 控制 | `crawler_plane/runtime.py`、正文/browser 调用入口 |
| 局部 CPU-heavy 控制 | CDECR/Bulk 实际启动入口 |
| Safety Controller | 新 controller 与对应 systemd service |
| 删除旧资源客户端 | `src/doxagent/resource_budget.py` 与所有调用点 |
| 4C16G 部署 | `deploy/doxagent-app.slice`、`deploy/docker-compose.server.yml` |
| 验收 | Worker/Runtime/Initialization/Safety/25 ticker burst 测试 |

不建议为了共享几十行排序逻辑而新建大型 `execution_pool` 子系统。只有在两个以上执行域确实出现完全相同、稳定的代码后，再提取小型纯函数或 schema。

## 16. 必要验收矩阵

### 16.1 默认并行与时效

1. 25 ticker 同时产生至少 100 个 Codex Job；记录完整、无丢失、无等待线程。
2. NORMAL 下 active Codex executions 能超过 6，并逐步验证 10、15、20+；不存在固定总 capacity 阻挡。
3. 多个长 background turn 已运行时提交 W1/W2/W3，realtime 在下一 launch wave 优先启动。
4. realtime 持续流量下，background 最长等待保护能推进，但不占用固定保留槽。
5. RKLB Initialization、MU/NVDA Maintenance、Realtime Cases 同时推进，无 `HEAVY_BATCH_CONFLICT`、`PRIORITY_WAIT`、`RESOURCE_BUDGET_WAIT`。
6. 单 ticker 50 条突发不会通过全局锁阻断其余 24 ticker。

### 16.2 压力与故障

1. DoxAgent 使用超过旧 6.5G 时仍保持 NORMAL，不发生软件误阻断。
2. 高 page cache 或高 `memory.current` 但没有 PSI/swap 时，不因内存利用率单独进入 PRESSURE。
3. 注入持续 PSI/swap/低 MemAvailable，验证 NORMAL→PRESSURE；background 新 fan-out 停止，realtime 继续。
4. 逼近 15G 或 OOM 事件增长时进入 CRITICAL，只回收一个可恢复 background execution。
5. 已健康运行的 background 在普通 PRESSURE 下不被强杀。
6. Safety Controller 停止或快照过期不会停止 dispatch；cgroup hard boundary 仍有效。
7. 弹性域泄漏最终受顶层 app cgroup 限制；CDECR/Browser 等独立故障域受自身宽松上限限制。

### 16.3 恢复与一致性

1. QUEUED 不消耗业务 attempt；重启后保持幂等身份和顺序字段。
2. RUNNING 重启先检查 receipt 和旧进程归属，再决定恢复，不重叠执行。
3. CLEANING 未确认时不复用该 capsule；这属于进程安全，不是全局 slot。
4. 同 thread 始终只有一个 active turn，其它 thread/ticker 不受影响。
5. 父 Workflow 等待子 Job 时不持有执行资源，进程重启后可从 durable state 继续。
6. Controller/dispatcher/单执行域故障不扩散为全系统不可运行。

## 17. 发布、校准与回滚

- 先在本地完成，不同步远端；生产部署需用户另行确认。
- 新旧资源准入不得同时生效；切换 Elastic Dispatcher 前必须移除旧 Guardian admission。
- Safety Controller 先 shadow 观察，再启用 PRESSURE，最后单独验证 CRITICAL 回收。
- canary 按 15→20→25 ticker 递增，每级先观察业务 SLO，再观察资源；资源健康时优先提高并发，而不是停在保守初值。
- 回滚目标是上一个可运行版本，但不恢复 Heavy Batch Mutex、静态 reservation 或 Docker peak 计账。
- 部署前备份 Worker/Runtime/Initialization durable store；不删除成功成果、不重建 ticker、不重放已完成模型阶段。

## 18. 明确不再接受的设计

- “整个 DoxAgent 只能同时执行 N 个任务”的全局 slot 模型；
- realtime 永久保留槽、shared/background 固定分区或 weighted slot；
- ticker、workflow、batch 或 run_id 参与资源互斥；
- 每个任务预估 MiB 并与当前占用相加决定准入；
- Safety Controller 不可用时拒绝全部新任务；
- 把 Docker limit 未使用部分当作已占用或已承诺；
- QUEUED Job 预先创建线程、SDK、MCP、Browser page 或长期 lease；
- 为追求架构统一，让 Message Bus、SQLite、API、Codex、Browser、CDECR 使用同一许可系统；
- 用复杂公平评分、多个压力等级或长期 cooldown 解决第一版问题；
- 只提高内存阈值，却保留 Heavy Mutex、父预约或 Worker 固定低并发；
- 通过降低研究质量、关闭 OOM killer或无限扩大 swap 掩盖架构问题。

## 19. 最终交付口径

完成重构后，系统应呈现以下行为：

- **资源健康**：runnable work 尽可能并行，Realtime 先启动，Background 同时推进，并发随机器实际承载能力自然增长。
- **出现压力**：先停止新的 Background heavy fan-out，Realtime 减速但继续，已运行任务原则上不受影响。
- **逼近失控**：停止新 heavy launch，保护 Message Bus/Control/Runtime，必要时只回收一个可恢复 Background execution。
- **监控故障**：不形成新的全局阻塞，最终安全由 14G/15G cgroup 边界保证。
- **多 ticker**：15–25 个 ticker 可以同时处于活跃生命周期，不因 batch、workflow 或人为总 slot 相互串行。

本方案的成功标准不是“调度算法足够精巧”，而是删除错误 Gate 后，DoxAgent 能够在 4C16G 上积极使用资源、保持实时链路时效，并在真实异常出现时以最少状态和最小影响范围完成降载。
