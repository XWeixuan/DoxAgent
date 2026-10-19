# DoxAgent 生产 CPU 深入审查与优化方案

日期：2026-10-19，Asia/Shanghai。实施更新：2026-10-20 已按用户授权上线 CPU-01、02、03、05、06，CPU-04 按要求跳过；已完成 30 分钟以上稳定性复查，结果见[实施报告](production-cpu-optimization-execution-20261020.md)。以下保留原设计与证据。

本轮目标是减少无效 CPU 工作，保留新闻覆盖、初始化并发、交易响应和恢复能力。优先修复三个已经有运行证据的热点：初始化空闲争抢、Control 无变化同步、W3 历史线程维护的 JSON 关联扫描。随后做 Yahoo 读取顺序、传输维护读取、Bus 限流状态写入的局部优化。不建设全局资源调度系统，也不把正常采集流量一律降频。

方案编制阶段仅运行只读诊断与隔离的内存 SQL 实验；没有修改生产配置、创建生产索引、关闭网页、重启业务容器或删除历史数据。方案和诊断脚本写在本地仓库。已有其他任务的未提交修改不属于本轮。

## 1. 结论与证据边界

### 1.1 是实际竞争，但不能把所有 CPU 都归为浪费

服务器为 4 vCPU、16 GiB。最近约 168 小时的 1,008 个 sysstat 样本中，`100-idle` 平均约 88.75%，其中实际执行约 83.54%、iowait 约 5.21%、steal 为 0；约 44.15% 的时间段忙碌度达到 95%。10 月 19 日取证时 CPU 压力很高，运行队列明显超过 4。既存在应用计算竞争，也存在过去若干时段的 I/O 等待。

用户截图的云监控平均为 93.033%。截图窗口与 sysstat 窗口、聚合口径未完全对齐，因此不将数值差异解释成监控故障。历史没有完整的逐容器数据，当前容器份额不能外推为过去七天各组件的贡献。

90 秒 cgroup 差分的主要占用如下。这里的“整机占比”已经除以 4；Docker 的 100% 通常代表一个逻辑 CPU。

| 组件 | 使用核数 | 整机占比 | 当前判断 |
| --- | ---: | ---: | --- |
| Chrome Supervisor | 0.842 | 21.05% | 包含实际采集浏览器及子进程，不能全部视为可省 |
| Site Access | 0.643 | 16.07% | 含 Python、Managed Chrome、Node 等，需分开评估 |
| Runtime Scheduler | 0.605 | 15.11% | 其中 W3 maintenance 线程有明确低效 SQL |
| Message Bus | 0.376 | 9.41% | 业务采集与持久化均在运行，优先合并重复开库 |
| Read Projector | 0.277 | 6.93% | 当时仍受追赶/版本状态影响；之后瞬时值已显著降低 |
| Initialization | 0.230 | 5.75% | 当时无 RUNNING/QUEUED 初始化，空轮询证据明确 |
| Transport Maintenance | 0.164 | 4.10% | 每条候选重新读取 packet、开库存在优化空间 |
| Executor | 0.152 | 3.80% | 仍有 WAIT_QUOTE 任务，不能按空闲服务处理 |
| Control | 0.143 | 3.57% | 已完成状态仍持续重复协调 |

这些是同一个测量窗口中的份额。后续瞬时快照有波动，例如 Projector 约为 Docker 4.09%，不能继续把前一窗口的 27.7% 当成当前固定成本。main 已包含 10 月 19 日的 Read 索引、无变化写入和 tick 缓存优化，本方案不重复设计这些已完成的改动。

### 1.2 证据来源与版本

本地审查基线为 `6699d74c`；取证期间远端仓库观察到 `e49e264a`，各运行容器还存在不同发布批次。实施时应按当前 main 和各服务镜像身份对照，不用仓库 HEAD 代替运行版本。

本地证据目录：[eval/cpu_audit_20261019](../eval/cpu_audit_20261019/)。关键文件：

| 文件 | 支持的结论 |
| --- | --- |
| `history.json` | 主机历史执行、等待、队列情况 |
| `live.json`、`inventory.json` | 90 秒 cgroup 核时与服务身份 |
| `thread-cpu.json`、`profiles.json` | 活跃线程核时及其调用路径 |
| `database-cost.json`、`query-plan.json` | 连接成本、队列状态、旧 W3 执行计划 |
| `browser-inventory.json`、`final-boundary.json` | 页面所有权与随后自动退出的复核 |
| `deeper-plan-probe.json` | 真实库上旧 SQL、候选 CTE 改写的中断实验 |
| `plan-validation-probe-v2.json` | 内存 SQL 结构实验、半小时浏览器访问、身份生命周期 |
| `bounded-case-probe.json` | 生产只读的 64 Case 查询验证 |

`py-spy` 原始采样包含等待线程，栈样本比例不能当作 CPU 比例；本方案用内核线程核时确认热点。Site Access 的部分采样受 UID 权限限制，未扩大生产权限来完成采样。诊断中 SQL 中断预算只作用于独立只读连接，不会取消业务查询。

## 2. 实施顺序与范围

| 优先级 | 编号 | 工作 | 依赖与边界 |
| --- | --- | --- | --- |
| P0 | CPU-01 | 初始化单一取任务入口，保留 16 个执行槽 | 独立实施；不改 D2/D3、CDECR 并发与租约语义 |
| P0 | CPU-02 | Control 按变化同步，批量读协调元数据 | 独立实施；保留跨库补偿与控制生效时效 |
| P0 | CPU-03 | W3 历史发现按 Case 分批，回执索引点查 | 独立实施；仅维护元数据，不改研究资料复制与 Context |
| P1 | CPU-04 | Yahoo 先读已到达数据，再滚动/回退 | 复用现有 recipe；不降采集频率、不新增浏览器池 |
| P1 | CPU-05 | Transport 避免逐条开库和年轻 packet 的无效加载 | 复用现有维护任务；不改保留期限和恢复边界 |
| P1 | CPU-06 | Bus 请求起始限流状态在一个事务中更新 | 保留逐请求持久化及重启后的限流约束 |

CPU-01 至 CPU-03 优先完成并验收；CPU-04 至 CPU-06 随后实施，无须等待它们才释放 P0 的收益。它们不依赖清理历史数据、不依赖 W3 存储架构重做，也不依赖升级机器。

暂不纳入本轮必做范围：全局 SQLite 连接池、跨请求通用内容缓存、全局浏览器页面池、全面修改 Chromium 参数、降低所有源频率、限制所有容器 CPU、改造 Worker/W3 研究资料物化。后文列出具体触发条件，避免将低确定性的方向伪装成必做重构。

## 3. CPU-01 初始化空闲调度

### 根因

`ticker_initialization/cli.py::_run_parallel_loop` 为每个 worker 启动 `drive()`，无任务时每 0.25 秒再次调用 `run_once()`。生产配置为 16，因此理论上每秒可产生约 64 次空 claim；实际频率还受数据库和事件循环耗时影响。

`service.py::run_once` 同步调用 `repository.claim()`。即使没有任务，claim 仍进入 `_write()` 的 `BEGIN IMMEDIATE`，检查/创建控制镜像表并查可领取任务。16 个执行槽同时空闲时，仍反复开库和争抢写锁。

现场初始化记录为 15 个 SUCCEEDED、3 个 FAILED，无 RUNNING/QUEUED。初始化主线程仍使用约 0.230 核。空连接轻查询 CPU 均值约 3.67ms，64 × 3.67ms ≈ 0.235 核，与现场量级一致；这是机制交叉验证，不是对每次 claim 的精确计时。

### 选定实现

改为进程内一个取任务循环管理现有 worker 的空闲槽，直接复用 `run_claimed(lease)`：

1. 只有存在空闲 worker 时才尝试 claim；使用该 worker 原有 owner 和 lease_seconds。
2. 领取成功后立即创建执行 task，继续填充其他空闲槽，直至填满或队列为空。
3. 队列为空时，整个进程只保留一个 0.5 秒的领取等待。存在执行 task 时，同时等待其完成；释放槽后可立即继续填充，不额外等完整轮询周期。
4. 所有槽忙时只等完成事件，不继续查空队列。
5. 单次执行、结果打印、summary mirror、SIGTERM 及退出码保持原契约。取消时先取消并 await 当前执行 task 的 finally，再释放进程锁；不遗留已经领取但尚未交给执行 task 的租约。
6. `ExecutionDeferred` 仍沿用现有释放与恢复语义，避免 dispatcher 因 RUNNING 返回值立即高频重领同一任务。若需要局部等待，使用既有延期信息/空闲等待，不新增一套全局重试规则。

claim 仍是唯一的原子领取入口，保留 normal/repair lane、execution identity、CDECR 排除、token 和租约检查。把镜像表迁移移出高频 claim，放到现有启动迁移；不通过删掉完整性检查省 CPU。

### 为什么不只把并发从 16 降到 1

执行并发与领取并发不是一回事。降低执行并发会牺牲初始化吞吐；单一 dispatcher 只去掉无人工作时的重复查询。也不引入 Redis、跨进程通知或新的任务系统：0.5 秒单轮询已足够便宜。

### 验收

使用 fake adapter 验证 16 槽能同时工作、完成一个槽立即补位、空闲领取频率不随槽数倍增。覆盖无任务、单次执行、deferred、repair 隔离、lease lost 和 SIGTERM。复用现有初始化/CDECR 领取测试。

生产在没有初始化任务时，空 claim 目标不超过约 2 次/秒，CPU 目标低于 0.03 核；有任务时不以该 CPU 阈值约束业务执行。保持配置并发 16，单个 ticker 的 D2/D3 并发配置不变。

## 4. CPU-02 Control 按变化协调

### 根因

`v2_control/worker.py` 每秒 tick。`ControlService.tick()` 每次写 heartbeat，并对所有已初始化 ticker 调用 `reconcile_initializations()`。

成功分支每次都调用 `activate_candidate()`、重新读取 effective state、写初始化镜像、写 Bus 镜像、写 ACK。`activate_candidate()` 已有 activation 相同的逻辑短路，但短路发生在新的 Runtime 写事务内部；不能把它误报成每秒递增 revision。真正的重复成本是事务、开库、完整对象读取及下游镜像重写。

`mirror.apply()` 使用 `revision >= current_revision`，相同 payload 也会执行 UPDATE。现场 9 个 ticker 在初始化和 Bus 中的 epoch/revision 已一致，17 个控制操作均 SUCCEEDED，仍重复执行上述流程。Control 主线程实测约 0.131 核，整容器在另一个窗口约 0.143 核。

### 选定实现

修改 `v2_control/service.py`、`repository.py`、`mirror.py` 以及初始化仓库的窄读取方法：

1. 每 tick 用少量批量读取获得控制状态、初始化状态/epoch、`ticker_active_revision.revision_id`，以及两个消费者的镜像状态。复用同一次读取连接，避免为每个 ticker、每个属性新开连接。
2. 对 activation_id 已一致、analysis 已允许的成功 ticker，不再读取完整 activation artifacts，不再调用无变化的激活写事务。新的候选或状态转换才读取完整证据，并执行现有初始化归属验证。
3. 镜像与权威状态一致则跳过该消费者写事务；缺失、落后或内容不一致才同步。比较保留 epoch、revision 和实际 payload，不能仅凭“过去 ACK 过”永久跳过。
4. `mirror.apply()` 在原有单调 epoch/revision 条件之外增加“payload 确有变化”的更新条件；真正进入写事务时仍使用现有单调约束，防止读写间变化导致旧状态覆盖新状态。
5. ACK 仅缺失时写入。REMOVE、暂停、重新添加和初始化失败仍走现有业务分支，其节点终止等副作用不能因镜像相同而被一起跳过。
6. heartbeat 首次 tick 立即写，之后每 10 秒写一次。已核对 `api_v2/overview.py` 的新鲜度阈值为 30 秒；维持该阈值，不降低控制命令的每秒处理频率。

不新增跨库事务框架。权威控制状态继续在 Runtime；每秒核对实际镜像，继续承担中途崩溃后的补偿责任。初始化、Bus 任一镜像恢复为旧版本后仍能自动追平。

### 验收

稳定状态连续 tick 不产生镜像 UPDATE、激活事件或 revision 增长；只有 heartbeat 按 10 秒写入。分别模拟初始化镜像缺失、Bus 落后、写一个库后崩溃、ACK 缺失、新 activation、初始化失败、REMOVE 后旧调用继续等情况。

生产稳定 9 ticker 时，Control CPU 目标低于 0.05 核；命令进入处理和新 activation 被发现的时效保持约一个 tick。测试使用仓库事务/SQL trace 统计实际写入，不仅检查返回值。

## 5. CPU-03 W3 历史线程维护

### 根因与本轮新增验证

`persistent_runtime_v2/w3_execution.py::maintain()` 的 legacy 查询，将 Case JSON 中的 thread_id 与 `runtime_values` 中 Worker 回执 JSON 的 thread_id 关联，再排除已登记线程，最后 `DISTINCT ... LIMIT 20`。

执行计划以 worker_invocations namespace 为外层检索，再扫描 Case；LIMIT 限制返回结果，不限制扫描工作。现场 `runtime-maintain_2` 线程约使用 0.292 核，栈位于该 SELECT。数据规模约为 4,384 Case、7,005 invocation、2,050 线程；Case 与线程允许多对一。

本轮做了三层验证：

- 真实库上旧查询消耗约 502ms CPU 后被诊断连接主动中断，尚未完成。把未登记 Case 包成 CTE 再 JOIN 的简单改写同样超过 500ms，不能采用。
- 从真实库提取必要标量，在独立内存库实验：有 thread_id 的 Case 全部已有登记，缺失 Case 数为 0。旧关联即使加了表达式索引，规划器仍可能先扫回执，仍超过 500ms；仅“补个索引”不足以解决。
- 真实生产只读连接先按主键取 64 个 Case，再做登记存在性查询，64 条用了约 **9.87ms CPU、19.48ms wall time**，全部已登记，需要查回执的次数为 **0**。内存实验中的独立回执点查使用了 `SEARCH ... (<expr>=?)`。

后两种实验只验证查询结构，不代表整个 W3 流程能按相同比例加速。此次也未在生产创建索引。

### 选定实现：两段查询，零缺失时零回执读取

1. 在现有 housekeeping namespace 中增加独立 `legacy_case_cursor`。原有 normal thread cursor 保留，两个扫描不能共用游标。
2. 按 Case 主键读取最多 64 条原始 Case，**先 LIMIT 再按 thread/登记状态判断**；没有 thread 的行也推进扫描游标。这样全已登记、全无 thread 等情况下都不会为了找满候选而扫描全表。
3. 在同一短读事务中核对线程登记。已登记即跳过，不扫描 invocation。批内相同 thread_id 去重。
4. 仅对未登记的 thread 查询 Worker 回执。优先使用已有 stage 中明确的 worker_job_id；没有明确映射时使用下面的部分表达式索引做 thread 点查，不再 JOIN 全部 Case。

```sql
CREATE INDEX IF NOT EXISTS w3_legacy_invocation_thread
ON runtime_values(json_extract(payload, '$.job.thread_id'))
WHERE namespace = 'worker_invocations'
  AND json_extract(payload, '$.node') = 'persistent_runtime_w3';
```

5. 索引放在现有 W3 owner 的启动迁移内创建，不放到高频 maintain；查询条件与部分索引谓词一致。字段确认是 inline 标量，查询不经 row codec 展开整个 Context/native file。索引只增加必要检索元数据，不复制业务材料。
6. 继续通过 Worker 的 `register_legacy_thread(job_id)` 验证真实 job/request 归属、旧任务类型和 template 保护；不能凭 Case 的 thread_id 自行认领或删除线程。某条回执不可注册时可继续检查其他匹配回执，不能盲取第一条就判定无恢复责任。
7. 每轮最多扫描 64 个 Case、最多发起 20 个登记动作。若在一个 Case 的多个回执中达到动作上限，保留该 Case/回执的续扫位置，不能把 Case 游标推进到尚未处理的结果之后。失败沿用现有错误记录和重试机制；一轮扫描中有失败时，完成本轮后按原有失败周期重扫，避免游标推进后永久漏掉失败项。
8. 有剩余批次时沿用短间隔继续，扫到末尾重置 legacy 游标，平时沿用现有周期。随机 Case ID 在游标后方插入的问题由完整周期重扫覆盖；不把一次扫描完成标志当成永久不再发现旧线程的依据。
9. legacy 发现、正常线程到期处理、ACK 重试分别完成短步骤。legacy 发现异常仍记录错误，但不阻止其余两类维护。现有 active thread、owner Case、Effect、template、expiry、DELETING/DELETED 检查全部保留。

这里的续扫状态只复用现有 housekeeping 存储，不新建迁移服务、扫描队列或全局资源账本。

### 验收

重点测试：全已登记时零 invocation 查询；大量无 thread Case 时仍只扫 64 行；稀疏缺失项最终可达；相同 thread 多 Case；一个 thread 多回执；分页/动作上限边界；登记失败后可重试；新建 Case 在已扫主键范围内下一完整周期仍被发现；active/template/有 owner 的线程仍受保护。

对空缺失、稀疏缺失和较密缺失三种数据检查执行计划，回执点查必须使用 SEARCH，不接受外层全表关联。稳态无缺失 batch 的目标为低于 50ms CPU，现有千级数据一轮完整扫描 CPU 目标为数秒以内，不能持续占用约 0.29 核。RPC 等待单独计时，不伪装成 SQL CPU。

不改 Worker/W3 的研究资料复制、Context 物化、交易与 operations 执行，不通过删除 Cases/invocations 来缩小查询规模。

## 6. CPU-04 浏览器成本与 Yahoo 局部优化

### 已查清与仍不能断言的部分

浏览器高占用并非只有“遗留空闲页”一种解释。半小时访问记录显示：

| 身份/用途 | SUCCESS 次数 | 单次平均 network_ms |
| --- | ---: | ---: |
| GlobeNewswire search / CRAWLER | 365 | 2,314ms |
| Yahoo backup / CRAWLER | 157 | 9,437ms |
| InvestorsHub / CRAWLER | 131 | 10,360ms |
| InvestorsHub / BODY | 24 | 5,554ms |

上述时延包含等待，不等于 CPU 时间。GlobeNewswire 按 ticker、搜索词及分页采集；Yahoo 和 InvestorsHub 是 ticker 列表，不能因为请求多就改成只读一个全站 feed。也不能把 transport SUCCESS 等同于正文 FULL 或最终消息接纳。

前次疑似 CLOSED 但仍存在的两个原生页面后来已经自动消失，未证明永久页面泄漏。Registry 中一些很老的 LIVE/CLOSE_PENDING 记录也不能直接证明 Chrome 仍在运行，要核对相同 instance 与 CDP target。后续现场还出现了 2 个 WAITING_HUMAN challenge，因此“先前没有人工会话”不能作为之后关闭全部页面的依据。

已有 `WorkerRuntimeFacade.close_idle()`、native instance 停止逻辑和每分钟 maintenance。Yahoo/DigiTimes backup 的真实配置已经是 on_demand、120 秒 idle；InvestorsHub 是 always_on。增加另一套 TTL 扫描器不能解决持续业务请求下的 CPU。

### 本轮选定修改

修改 `message_bus_v2/yahoo_sources.py::capture_latest_news`：

1. 保留原生网络 listener、ticker 页面导航、状态码与目标 ticker 校验。
2. 页面到达现有 readiness 后，若网络数据已捕获立即使用；否则**先读 SSR hydration，必要时检查现有 DOM**，有结构有效的新闻数据就返回。
3. 只有数据确实未到达时才执行现有滚动和等待，然后保留 browser same-origin fetch 回退。当前代码在 SSR 检查之前最多滚动并等待约 1 秒，这部分在已有数据时没有业务价值，还会触发额外页面工作。
4. 空列表必须按既有可识别结构判定有效，不能将“解析失败”解释成“没有新闻”。保留发布时间、标题、URL、snippet_count 语义以及 challenge/error 分类。
5. 不增加常驻 anchor page、不改变 profile/代理/登录身份、不调整源轮询间隔。

收益主要发生在 SSR 已到达的请求。网络响应已经提前命中的请求可能无收益；因此不承诺所有 Yahoo 请求都减少 1 秒，更不将 157 秒直接换算成 CPU 核时。

### 验收与浏览器后续触发条件

用现有 Yahoo fixtures 测试 native response、SSR、有/无 DOM、需滚动、same-origin fallback、空列表、非目标 ticker、403/challenge；SSR 已有效时不再调用滚动或 fallback。正常生产窗口对比每次成功轮询 CPU 核时、p50/p95 时延、入库新消息和覆盖失败率。

下列方向保留为后续条件项，不先行扩大本轮：

- **GlobeNewswire 搜索端点 browser fetch**：确实是 HTML partial 端点，可以研究复用同一浏览器身份的同源 fetch，避免完整页面导航。须用实际查询/分页证明状态码、challenge、内容和时间覆盖一致，并测得每次成功采集 CPU 明显下降后再实现。不是改用无身份 HTTP，也不改为少查词。
- **重复 BODY 抓取**：24 次 InvestorsHub BODY SUCCESS 仅对应 3 个 operation key；key 是 URL hash，并不等于 3 个 job。要先区分同 URL 的不同策略、expand、身份代次、业务时间窗口与不同任务。只有证明确属同一输入的重复抓取才合并，正文质量检查仍逐任务执行。
- **操作内重复解析**：`_cache_key()`、resolve、identity 查询可考虑复用同一操作中的已读快照；Site Registry 已有长期连接，不能错误地再给它补“连接池”。等待 admission 或网络之后仍须遵守现有 generation 检查。
- **Chromium 后台参数/资源屏蔽**：目前仅发现 Managed 参数存在，未证明移除不会影响采集。若后续测试，按单站 recipe 验证，不全局阻断脚本、图片、iframe 或降低后台调度。

`SiteAccessService` 的现有缓存包含 request_id 和请求指纹，承担同一 RPC 重试去重。不能直接删掉 key 中 request_id，让跨任务请求误用旧响应、旧 provenance 或错误归属；通用内容缓存不纳入本轮。

## 7. CPU-05 Transport 维护读取

### 根因

`v2_read/source_lifecycle.py::expire()` 每个候选都调用 `source.event(seq, decode=False)`，后者另开连接读取 packet；之后才检查年龄。年轻 outbox 行已有 recorded_at，仍被反复加载。旧 archive/pack 还可能重复读同一 pack。游标走完后重置，年轻记录会在后续周期再次经过。

维护容器实测约 0.164 核。`transport_archive.main()` 已有 expiry 的时间/批次限制，但 native refs reconcile、inventory、collect 不在该 expiry 时间限制内。不能将“已有预算”误读成整个维护循环都有相同上限，也不能再添加另一个全局压力控制器。

### 选定修改

1. outbox 候选先用行内 recorded_at 判断年龄，未过期直接推进游标，无须加载 packet。遍历仍覆盖整个有界批次，不在第一条年轻记录处退出，因为时间可能乱序。
2. 把需要加载的 packet 读取改成使用同一个调用内的连接，或使用有界批量读取；不再按候选单独开连接。archive/pack 按本批次实际 digest 复用一次解压结果，批次结束释放。
3. 保持当前 ack floor、pin、exception、native artifact refs、延迟文件退出及事务一致性。真正要删除前仍执行现有引用检查，不能用缓存中的旧引用数代替。
4. 第一版保持现有保留期、sleep 和 native GC 周期，只改变重复读取。若其中某一步仍占主要 CPU，再根据各步骤计时调整现有循环的调度，不先增加多个周期参数。

修改位置：`source_lifecycle.py`、`outbox.py` 的内部 packet 读取接口，必要时 `transport_archive.py` 增加单轮聚合计时。借用连接须明确 owner，内部方法不能关闭调用者连接；不在事务外悬挂可变 packet cache。

### 验收

年轻、过期、乱序时间、exception pin、pack 共享 digest、native ref 和文件退出的结果与当前语义相同；同一批次开库次数为常数量级。复用 `tests/v2_backend/test_source_lifecycle.py` 等生命周期测试。以相同 scanned/expired 数量比较核时，不通过少执行清理获得表面 CPU 降幅。

## 8. CPU-06 Bus 限流状态的单事务更新

### 根因与选定修改

`message_bus_v2/scheduler.py::_limiter().on_request` 先 `get_scheduler_group_state`，再 `save_scheduler_group_state`；每次读取和写入分别开库。Bus fresh 轻查询约 6.41ms CPU，而同连接轻查询约 0.006ms，连接/首次 schema 工作的固定成本明显。

在仓库增加一个窄方法，例如 `record_scheduler_request_start(group, now)`：同一个事务读取已有状态，只更新 last_request_at、updated_at 后写回，保留其他 cooldown/失败/约束字段。`on_request` 调用该方法，继续在请求开始前等待持久化完成。

不把请求开始记录改成数秒后批量写入，也不只存在内存中；重启后 minimum gap 的恢复责任继续保留。与其他限流状态写入竞争时不能覆盖它们的最新字段。

### 验收

覆盖初次 group、已有 cooldown、连续请求、并发状态变化、重启后 gap。每次请求该步骤只开一个连接/事务，实际最小间隔不变。吞吐提升仅来自减少持久化开销，不来自绕开 request permit。

## 9. 连接复用的实现边界

fresh 与 warm 的轻查询差异证明固定开库成本存在，但不能推出“所有业务提速上千倍”。实际业务还包括 JSON、native file、模型、浏览器和网络。

本轮在 CPU-02/05/06 中通过批量方法或调用内借用连接解决热点，不整体改造 `RuntimeJournal.transaction()` 或所有仓库 API。保留写事务的 FULL/NORMAL 现有持久化设置，事务及时提交，不跨 LLM/网络 await 持锁。

P0/P1 后如果 Runtime 主线程或 Bus 仍有明确大量开库核时，再考虑已有执行线程内的有限连接复用；届时需单独测试 rollback、关闭、fork/线程归属。禁止把一个全局 SQLite 连接交给所有线程、把 check_same_thread=False 当作并发正确性方案，或引入新的池服务。

## 10. 预期收益与容量判断

| 项目 | 当前可归因量级 | 可期待的变化 | 不能据此承诺的效果 |
| --- | --- | --- | --- |
| CPU-01 | 空闲约 0.23 核 | 大部分空闲 claim 成本消失 | 初始化模型/业务执行更少 |
| CPU-02 | 稳定协调约 0.13 核 | 无变化写入基本消失，开库显著减少 | 控制服务完全不使用 CPU |
| CPU-03 | maintenance 线程约 0.29 核 | 全已登记时无回执扫描，维护变为短批次 | 整个 scheduler 的 0.60 核全部释放 |
| CPU-04 | Yahoo 高密度成功采集 | 已有数据时提前结束、少滚动 | 浏览器总成本全部可省 |
| CPU-05 | 整维护容器约 0.16 核 | 减少年轻数据加载、逐条开库 | 全部 GC 工作零成本 |
| CPU-06 | Bus 约 0.38 核的一部分 | 每次限流持久化减少一次独立开库 | Bus 所有采集/解析成本消失 |

前三项已定位热点合计约 **0.65 核**，它是优化上限参考，不是保证回收量。若消除其中约 70%–90%，规划估算可释放 **0.45–0.59 核**，相当于 4 核机器约 **11–15 个百分点**的计算容量；上线前后必须在同类负载下验证。后三项暂不给伪精确的整机降幅，避免重复计算浏览器和服务份额。

运行队列已有积压，新增可用 CPU 可能被有效业务立即使用。因而总利用率仍高不一定是失败；应看单位成功业务 CPU、排队时间和吞吐是否改善。若完成明确的无效工作优化后，正常业务仍长期接近四核且队列持续增长，就需要按源的单位业务成本继续优化，或评估增加 vCPU；不能靠暂停健康业务伪造余量，也不能承诺仅这几处修改一定把 CPU 降至 50%。

## 11. 实施、测试、发布与结束条件

### 11.1 开发和提交

按 CPU-01/02/03 独立提交；CPU-04/05/06 可在同一轮接续，但保留可定位的改动边界。每项重要代码修改追加 `changelog`，写清行为变化和验证。适配实施时最新 main，保留同时进行的业务改动。

只补实际行为测试，不为了覆盖率生成镜像实现的测试。核心是任务并发、写入次数、扫描上限、恢复和引用语义；无需启动付费模型、重新跑全套研究或发起大量生产抓取。已有 fixtures 与小规模仿真足以完成本地回归。

### 11.2 生产发布

复用当前部署脚本和版本身份机制，按变更构建并重建对应服务：

- CPU-01：Initialization；保护既有执行租约，沿用正常停止/恢复流程。
- CPU-02：Control。
- CPU-03：Scheduler 所在 Runtime owner；同时发布它使用的 W3 schema/query 实现。该改动不要求重启 Codex Worker。
- CPU-04：实际包含 `capture_latest_news` 的 Site Access/identity worker 运行镜像；只重建 Bus 而不更新 Site owner 不会使该 recipe 生效。
- CPU-05：Transport Maintenance。
- CPU-06：Message Bus。

先更新并验收 P0，再更新 P1。W3 部分索引由启动迁移显式创建，确认实际执行的是点查代码。正常服务身份和回滚机制复用现状，不创建新发布平台。不因本轮优化执行存量深度清理。

### 11.3 轻量生产验收

每个发布组开始/结束各取一个约 120 秒的 cgroup 差分窗口，保留相同口径的业务计数；在正常流量下留出至少 30 分钟运行再检查一次。不高频轮询长期业务任务，不新增守护服务。

记录以下数据即可：

1. 主机 CPU 执行/iowait、CPU PSI、run queue；容器 CPU 以核数归一化。
2. 初始化 claim 次数/成功领取数、Control 实际变化写入数、W3 scanned/missing/lookup/RPC 及本批次 CPU/耗时。
3. 各主要源成功轮询、新消息、覆盖错误、队列等待，Yahoo 按 capture_method 的时延；数据窗口与变更版本同时留档。
4. 原有生产能力、控制命令、投影新鲜度、交易/quote 待办持续正常。已有待处理业务不因 CPU 验收被暂停。

计数放入既有日志的低频聚合记录，避免为每次轻查询写诊断日志，也不新增监控数据库。上线后复用现有主机历史做跨日观察；不为本方案默认创建定时通知。

### 11.4 回滚与完成

某一改动导致漏处理、镜像不补偿、抓取覆盖退化、线程错误退出或维护语义改变，回滚该服务对应提交/镜像。新增 W3 索引是兼容附加项，回滚代码无需删索引；新增游标由旧版本忽略。维护前后不改变保留期限，不需要备份大体积缓存。

完成标准为：P0 三个无效工作机制已经消除；P1 三个局部改动已通过对应行为和正常流量验收；必要并发、控制补偿、线程所有权与采集覆盖保持。没有新证据时不自动扩展为连接池、浏览器池或全局资源治理重构。总 CPU 仍被有价值业务用满时，明确报告剩余业务容量压力，不能把“看起来低于某个百分比”作为唯一结束条件。
