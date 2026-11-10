# Runtime OOM / IO 修复实施与发布记录

2026-11-10，依据同目录完整事故方案执行。模型、Prompt、Skill、LLM Schema、路由、交易规则、冻结历史窗口不变。

**最终状态：修复已合入 origin/main 并发布，09:22:13—09:32:13 CST 完成全服务 10 分钟观察。129 项相关回归通过。监测组件无新 OOM/重启；263 个近期 Case 的限定检查无新增失败。实时 W3 41 次采样队列均为零，Worker 内存随积压消退回落。**最后一轮同期浏览器、消息源、交易与维护服务均运行，未采用停服后的资源回落冒充验收。完整统计和限制见文末。


## 已实施

- W3 私有 Ready 绑定取消公共 ready 豁免，保留原准备期限与正常 90 秒阶段交接；终态未提交 Job 的 Case 通过内部幂等接口释放物理 Capsule，保留逻辑 Thread、工作区和业务证据。释放与 Job 接受在同一 SQLite writer 内仲裁，按 Case attempt 身份阻止迟到的首次提交，已受理 Job 不被误杀。
- 静态 payload、标签、历史和事件准备提前到领取私有 Thread 前。实时行情与模型开始时刻保持原阶段。
- 实时 W3 用短线程直接开始，等待模型让出线程；CLOSED/归档等只领取既有 8 路后台容量能承接的工作。实时过滤发生在 SQL LIMIT 前；公共预热在绑定实时 Case 后也能动态绕过后台 cold gate / handshake / launch ramp。保留同一个 Thread 的未知 owner 隔离。
- Future 续接只持小身份，结果先保存再按原提交逻辑执行；ACK 在业务 commit 后异步进行，复用持久 housekeeping 标记。失去 lease、停机和取消不扣业务重试次数。
- W3 使用现有持久 SSE 和游标续订；终态只取一次完整 Job。状态投影不解码大对象，条件版本消除通知丢失。终态落库即可唤醒，不等审计文件复制。
- GC 启动延后 15 分钟，周期从完成算起，不再由每次维护 finally 强制触发。先遍历小批候选目录，再按 root 索引读取 Case/任务小元数据；不枚举全部历史 Frame / 原生 Fact。后台压力或实时 W3 出现可让出，2 秒预算和 SQLite progress handler 中断 staging 工作；发布仍复核原引用、指纹和版本。未知旧元数据保留原资料。
- checkpoint / finish / fail 的大对象编解码移出 writer，原 lease fencing 与 receipt CAS 保留；无变化 stage/association 省去写事务；批量续租返回失权身份。
- 核验并整合生产已有的 journal / maintenance / coordinator / bounded_inputs / maintenance_recovery overlay，防止本轮发布或以后全量 build 丢失 O2 恢复与补充任务修复。

## 验证与发布范围

- 151 项相关回归通过；最终事故聚焦回归 46 项通过，含 40/200 个模型等待 Future、后台阻塞时实时旁路、动态启动提升、终态释放及迟到请求、同线程隔离、停机恢复、事务外编码与 GC 让出。报告：`eval/runtime_oom_repair_20261110/regression.xml`。
- 扩展扫描中另一个旧 W2R3 测试用固定历史 source，现已触发既有过期交易保护，期待 READY 而实际 RUNTIME_DELAY_EXCEEDED；保护逻辑未修改。旧 Scheduler main 单测 fake 缺少真实 coordinator / heartbeat 配置，也与本次改动无关。没有为让测试变绿放宽过期保护。
- 停服窗口预建 jobs_run_owner（4.89s）、w3_ready_run（0.43s）、runtime_gc_task_run（9.69s）、runtime_gc_task_root（0.14s）、runtime_gc_activation_root（0.09s）；8MB SQLite cache，排序落磁盘，未执行 VACUUM / 全库完整性扫描。
- 使用实际 Scheduler / Worker 镜像为基底，仅覆盖明确文件清单，逐文件校验旧基线与新文件 SHA256。生产其他 overlay、密钥与 Compose 环境保留。其余 5 个隔离角色恢复已登记 Compose。构建与业务恢复期间不执行镜像 prune。

## 发布后观察

最终补丁于 09:22 CST 发布，完整服务的最终 600 秒采样结果见本文最后的验收表。采样只读 Case rowid 小窗、active job 索引、精确 Case/Job 主键、cgroup 与 PSI，不执行历史大统计。此前失败或停服窗口均在下文单独保留，不计为验收通过。


## 首轮观察发现的补充项

首轮资源已回落，未 OOM；但不能判为业务验收通过。约 80 个准备/续接任务仍在短写争用，W3 尚未推进到新的模型 Job。实际 /proc/locks 将 WAL writer 指向 Scheduler；py-spy 栈同时看到共享续租提交、其他 Effect repo 写入和大量 journal.begin 等待。Effect 的 claim/save/renew 原本绕过 journal 已有的数据库级 write_lock，形成同进程两套竞争入口；这四条短 SQL 路径现复用同一个现有锁，不扩大业务并发限制、不创建新队列，Native 编码仍在锁外。新增回归验证：心跳等待本地 writer 时尚未占用 SQLite writer。

另一个采样时刻 Executor 持有数据库 PENDING lock。已确认其 164 个执行 Job 全部 DONE、没有未完成提交，再单独重启 Executor；重启后 W3 等待仍存在，因此没有把 Executor 写成唯一根因。最终发布后重新计满 10 分钟观察，首轮低内存不能替代最终验收。


补齐结果路径：Worker terminal invocation/receipt 与 quota 收尾使用既有 priority=0，二阶段 parse-issue 回执同样提升优先级；没有变化的 W3 stage input_refs 不再开写事务。另发现 effect_trace 的原生诊断 INSERT 仍绕过业务 writer，现只在现有 writer 立即可用时记录 SQLite，否则沿用原文件 trace，不抢业务写锁。没有增加线程池、缓存或调度服务。最终资源观察及结果消费验收以最后版本重新计时为准。


结果等待链路专项：Http client 会 await queued/running observer；旧 observe_job 同步 journal.set 最多反复等锁 30 秒，多次进度事件可先于终态消费累积等待。这个字段只是 UI 进度，不是阶段结果。现复用原 write_lock 做立即可用检查，争用时跳过此次进度写入，不增加任务/队列；真正 Worker receipt、RESULT_SAVED、业务 commit 的持久化及 fencing 均保持。回归直接持有业务 writer 验证 observer 立即返回，空闲时仍正确保存 UI 状态。


SSE 配套检查：Httpx 默认共享 100 条活动连接，SSE 长连接会占用这份预算，从而可能把排队转移到 materialize / result RPC。已改 max_connections=None，仍只保留 20 条空闲 keepalive；不增加常驻进程或模型并发。新增真实本地 TCP 回归同时持有 120 条 SSE，再验证同一 client 的普通 RPC 立即可用。生产某条 WorkerUnavailable 的原始异常未留存，不据空错误字符串认定它一定是 PoolTimeout。


ACK 边界复核：ACK eligibility 标记改为在 commit_stage 与业务 COMMITTED 同一事务写入，RESULT_SAVED 不再提前创建标记，防止 housekeeping 抢先清理。回归覆盖 RESULT_SAVED、commit rollback、commit success 三种状态；正常阶段交接和保存结果的重试入口不变。


最后现场栈（07:25 CST 后）：prv2-w3-async 阻塞于 queue_market 的 Future done callback → journal.gap → transaction；这是辅助行情失败诊断同步等待 SQL，能够停住所有同 loop SSE、回执和 HTTP 工作。现将失败处理移入原协程，await 既有 write_io；失败诊断本身异常只用已有 diagnostic_warning。回归持有 writer 并模拟行情失败，验证同 loop 的独立协程在 0.5 秒内完成，释放 writer 后诊断仍写入。没有新增线程池、业务并发门槛或模型调用。

工作区 DiskBudget 单槽和 workspace 全局锁确有潜在后台相互等待，但本次两次 Worker 栈均未见这些操作持有者，不能将其写为此次 MATERIALIZE 超时的已证实根因；本轮没有未经归因全局移除文件一致性锁。


## 9b9e0516 发布验收失败及第二次隔离

07:31:29 CST 开始最后一轮观察后，原积压推进，但内存在约 5 分钟内再次增长：Worker 约 17GiB，主机最低可用约 1.6GiB、2GiB Swap 接近用满，I/O PSI full 也明显增长。07:37 左右紧急停止 Scheduler/Worker 并临时 restart=no，交易 Executor/Delivery 保留运行。停止后可用内存回到约 20GiB。**这轮不能记为通过；后半段停服后的低资源不计验收。**

定点元数据确认该窗口 64 个 Slot 发布 Ready，其中 56 个 run 无模型 Job；不是 64 个正常活跃模型。Worker claim 每次都排入同一个 slot lock、执行 reconcile 和写事务，并 await 公共 fill；Scheduler 的已准备绑定回执仍直接竞争普通 journal writer。已完成绑定的重试现改为 case/record 索引窄读、身份核验和原私有交接，不进入上述公共写路径。HTTP 首次准备等待已有 build task（分段 20 秒，断连不取消原 Fork），准备完成即回读同一 Slot，减少原 100ms claim 写轮询。公共补货保留在已有 build.finally 与后台 prepare_assets，不作为实时 claim 回执前置条件。CASE 身份与 Ready 发布同事务，终态 tombstone / 已受理 Job fencing 不变。Scheduler 小绑定回执复用现有 priority=0 write_io，并跳过相同字段 UPDATE。

这些是已定位等待路径的窄修复；其对生产资源回落的效果仍以再次发布的完整 10 分钟观察为准，不能仅以本地回归或新机制推断通过。


补货退出依赖复核：build.finally 的公共 fill 也改为复用现有 tasks、按 profile 去重的独立后台任务，不令首次 HTTP claim 的原 build 完成被公共补货卡住；claim 消耗公共 Ready 后仍即时触发补货，避免等 30 秒周期才预热。没有新的 registry、线程池或容量门槛。刻意阻塞 refill 的真实协程回归验证第一次已准备回执仍在 1 秒内返回。


## 短写优先级仍被进程锁抹平

第二轮资源观察已看到内存平台回落，但没有新模型 Job / 业务提交，不能作完整验收。现场仅提取锁地址与线程栈（不输出 locals 业务内容），核验现有 write_lock 被普通 save_effect / BEGIN 路径持有，35 个同类线程及 priority=0 绑定回执等待。既有 PriorityQueue 的优先级到线程 RLock 为止被抹平，竞争重试可不断越过短写 owner。现仅替换 write_lock 的内部互斥实现：保持同一数据库一个可重入互斥，既有 SHORT_WRITE owner 优先，同级 FIFO，逐个 Event 唤醒；不打断当前事务，不新建 writer、线程池、业务并发上限或额外持久状态。既有 SQLite busy timeout、rollback、fencing、async 外层 retry 保留。回归验证 20 个排队普通 writer 中短写 owner 先完成，以及重入、timeout 移除、best-effort observer 不占队。


同批减少实际标量读开销：RuntimeJournal 默认读复用当前线程的一条 SQLite 连接，读事务仍每次 BEGIN / commit（异常 rollback），没有跨调用快照。嵌套读取与指定 timeout 单独连接；写连接及所有业务事务不共用该连接。生产同一镜像的独立只读子进程、同一 miss key 20 次探针：原版 207.02ms，新版 8.70ms。该探针未写生产库、未替换运行进程模块。回归验证 20 次读只建一次连接，另一连接更新立刻可见、嵌套隔离和异常后无残留事务。74 项聚焦回归通过；扩展 49 通过、1 个 CLOSED recovery 旧夹具不进入子任务，在恢复原 RLock 后同样失败，未改业务日/恢复规则来令其通过。


## 最后一轮窗口仍失败：Worker 独立全历史回收入口遗漏

798da8b1 后实际 Job 与业务提交已恢复（窗口中段最近 250 个 Case 观察到 8 个决策提交，3 个抽查均在提交后完成 ACK），但后半段再次出现严重内存 / I/O 压力。Worker 持久状态有 71 个排队 W3 Job（trade 55、operations 16）及约 40 个运行 Job；不能因这些数量就认定内存全部属于活跃模型。新隔离完成时间：08:18:54 Worker、08:18:56 Scheduler、08:19:00 两个浏览器相关容器。四者均未被 Docker 标为 OOMKilled；后续还须核验内核全局 OOM 记录。此前 10 分钟采样因系统停顿实际延长至约 765 秒，最后状态不可记为通过。

本次现场浏览器 cgroup 同时占用：Site Access 3,628,269,568 bytes / 601 PIDs，Chrome supervisor 5,073,838,080 bytes / 1994 PIDs，约 8.10GiB。它是实际共存资源，不能说只是 Runtime 独占整机，也没有将浏览器认定为所有事故的唯一根因。因 Docker/SSH 响应变慢，已准备按精确模块与 cgroup 身份紧急终止 Runtime；执行时目标已停止，未额外 kill 进程。

进一步代码复核确认，前面 P0-6 只修了 Scheduler 资源 GC，遗漏 WorkerJobManager 启动 60 秒后自动触发 _collect_owned_resources：TerminalSessions.collect 在事件循环同步 owned/business_holds 全量读与 Native 解码；Workspace collector 在选 limit=4 之前枚举全体 Job 和研究证据，并为每个候选重复 paths_in；Snapshot collector 又全局计算保护集合。这些无范围上界的回收仍可能与恢复叠加；早先已抓到 Workspace collector 的活动线程，但没有事发前每 PID PSS/I/O 数据，不能编造本轮占用百分比或宣称它是唯一原因。

修复采用减法：实时 Worker 派发循环不再自动执行这三种全历史 sweep，保留原显式静默维护入口及所有删除前 fencing，不另造全局目录 / 反向引用平台。缺少候选范围引用元数据时保留旧输入和 session；该批旧派生数据的广域回收需静默维护窗口显式执行，自动的范围化版本不在本轮扩张实现。实时 Capsule 清理、W3 终态未用资源释放、ACK 压缩、候选范围的 Runtime resource_gc 均继续运行。回归以 overdue sweep + 正常 Job 验证热派发不启动全历史扫描，同时显式维护函数仍可调用；51 项通过。


冷启动读放大同步修复：Capsule.launch 和 lifecycle SDK initialize 原先为获取刚 spawn 的 PID start_ticks 调用 owned_processes(refresh=True)，从而每次启动都扫描 /proc 的全部进程环境并竞争全局 scanner lock。两处均改为只读取刚创建的精确 PID /proc/<pid>/stat；不动后代所有权扫描、随机执行身份和发送信号前的 start_ticks 核对。回归覆盖含括号的进程名、精确 PID 单文件读取及已退出进程。与 Worker 调度/Ready/清理共 52 项回归通过。

恢复顺序采用一次性的事故隔离步骤：先恢复已受理 Job 的 Worker，Scheduler 与浏览器保持暂停；没有新增模型并发门槛。08:28 时约 100 任务正在恢复、主机约 23GiB 可用；08:32 降至 9 个 Job、Worker 约 6.6GiB、主机约 14.5GiB 可用、无新的 Worker OOM。该数据有 Scheduler/浏览器暂停的条件，不能据此归因 GC 单项或作全服务验收。已受理任务自然结束后恢复其余角色，最终另记完整窗口。


## P0-2 顺序补齐：真正完成静态准备后才领取物理进程

全服务恢复窗口仍出现膨胀，说明仅把 history/labels/payload 推到 _thread 前并不充分：_run_phase_materialized 中 manifest 冻结、context bind、prompt/schema 构造还在实际 Ready claim 之后。现对生产已有的 batch workspace 路径延后 claim：_thread 只取得原逻辑 record；异步 phase 完成上述静态材料、原 context binding 和原冻结字节后，再 await 同一个 Ready claim / 原高优先级绑定回执，更新原 record 的真实 workspace，随后一次发布相对路径的小文件批次并提交原 Job。公共预热与原 Thread 复用仍在，legacy 非 batch 路径保持原行为。没有新增分片、reserve 状态、容量门槛、线程池或模型阶段。

回归分别覆盖 bind 失败时不 claim / 不派发模型、正常 bind→claim→materialize→model 顺序、第二次运行复用相同 manifest 和模型 receipt、不再次 claim / 调模型；实际 Task 的 run_id 指向原 Ready 返回的 workspace。Snapshot accepted_at、原冻结 manifest/token 与历史成员不重建，行情仍沿用已有早期 queue_market 与绑定机制。此次只更新 Scheduler，Worker 正在进行的模型不重启。


## 结果提交优先级补齐与最终验收口径

3376b07a 的完整服务窗口为 09:07:05—09:17:05 CST，41 次采样、600 秒，没有停止服务来降低后半段资源。主机可用内存最低 6.32GiB，结束 6.58GiB；I/O PSI full 窗口占比约 0.69%，无内核 OOM、容器 OOM 或重启；但 Swap 从约 1675MiB 增至 2048MiB，仍有资源压力。固定最近 250 个 Case 中，85 个决策和 29 个后续处理提交，未新增失败 Effect；22 个窗口内新建且已提交的 Worker Job，结束到提交中位 222.46 秒、最大 464.61 秒。**该窗口资源和业务进展恢复，但时效性不合格，不能算最终通过。**这些 Job 对应恢复积压，不能把“新建 Job”表述为“新鲜实时消息”。

随后不输出 locals 的现场 Python 栈确认：17 个结果保存、15 个 operations commit、3 个 trade commit 等仍以普通优先级争用进程内 write_lock；short-write owner 本身已空闲。之前只给异步绑定/receipt 设置优先级，漏掉同步完成结果的入口。0b011358 复用既有 SHORT_WRITE ContextVar，给 save_result、已保存结果续接、trade/operations 提交标记同一短写优先级。标记不持锁，网络与文件操作仍不占用 SQLite writer；不打断当前事务，不新增 owner/线程池/状态表/容量门槛。结果不可变、租约 fencing、提交后 ACK、原交易保护均保留。

新增真实多线程回归在普通 SQL 已排队时验证完整 RESULT_SAVED 写入先行、失败后恢复原上下文，且保存结果不提前创建 ACK。最终相关回归 **129 通过**，包括 Ready、续接、Worker 资源、边界时点、原 MCP 协议和离线回放。另一次扩展 W3 v4 旧用例检查为 43 通过、4 失败，四项均期待历史消息 READY 而实际被既有 RUNTIME_DELAY_EXCEEDED 保护拒绝；将本次优先级装饰器替换为 no-op 后，原 W3 v4 同样 14 通过、同四项失败。没有更改过期保护来绿化测试。

最终代码发布：Scheduler `0b011358937a05da65c775dc205d4dcfdc476e39`，镜像 `sha256:d80b752fe2c6bdaa8eb9480b0b417b3206b27d4f9aaa82b78f44072e4ec8d688`。Worker 已在此前发布 `05d6062222e1ee5c83233f07276a6a1d2fb582f4`（包含 baf4ecc5 修复），镜像 `sha256:9f6f40e8f4471b15a60c5e00caa924d1c245f77ca26d66871f46e3cac97c5f16`。最终补丁仅重建重启 Scheduler，没有中断 Worker 在途模型。逐文件 hash 与 canonical release state 已核对；其他会话的消息源/SiteAccess/Chrome 修复保留。

运行维护边界：本次没有新增 W3 人工并发上限、延长模型或 Case 超时，也没有重放历史失败交易或改写历史 Case。旧式 Session/Workspace/Snapshot **全历史自动回收已从热派发路径移除**，暂时只保留显式静默维护入口；即时 Capsule 退出、未用私有资源释放、提交后 ACK 以及候选范围 Runtime GC 仍运行。旧派生文件可能保留更久，后续需要维护窗口执行广域清理；不在本轮扩张为新引用索引平台。


## 最终 600 秒完整服务验收

时间：2026-11-10 **09:22:13—09:32:13 CST**（01:22:13—01:32:13 UTC），41 次采样、无采样错误。期间未发布、重启或暂停组件。结束时 22 个现有容器全部运行；已核对 Scheduler / Worker 实际文件 hash、实际镜像与 canonical release state 一致。源码提交后的最终报告提交不触发再次构建或重启。

| 指标 | 实测 |
| --- | --- |
| 主机可用内存 | 开始 10.25GiB，最低 5.99GiB，结束 11.48GiB |
| Worker 内存 / PID | 峰值 12.58GiB / 1737，结束 6.28GiB / 678 |
| Scheduler 内存 | 峰值及结束 1.53GiB |
| Worker / Scheduler 平均 CPU | 1.17 / 1.13 个核 |
| I/O PSI full | 窗口 stall 占比 0.82%，一分钟均值最高 1.02% |
| 主盘平均读 / 写 | 5.73 / 17.15MiB/s |
| Swap 存量 | 开始 2044.4MiB，结束 2046.1MiB，仍接近 2GiB 满额；未执行 swapoff |
| OOM / 重启 | 内核窗口查询无新 OOM；监测的 7 个角色 OOM kill / restart 增量均 0 |
| Worker 排队 | 实时 41 次采样均 0；后台开始 15，结束 0；结束运行中 Job 7 |
| Worker 共享 loop | over-250ms 次数增量 0；旧历史 max lag 不清零伪装成新低值 |
| 263 个近期 Case 提交 | trade_decision 27，post_decision_operations 39，共 66，66 个均已完成 ACK |
| 限定 cohort 新失败 | Case 0、Effect 0；未抹去历史失败 |
| 窗口内新建且消费的 Job | 46；模型结束→commit p50 1.51s，p95 9.32s，最大 19.16s |
| 防止只看成功提交的交叉核查 | 63 个窗口内结束的成功 Job，结束后逐一核对，无已结束但未提交结果 |
| 新绑定私有资源 | 17 个 Slot 全部已有对应 Job；未见新绑定后无 Job 的遗留 |

业务统计取观察开始与结束的最新 250 个 Case 的并集，共 263 个，逐 Case/Job 索引定点读取，不是全库全部历史统计。46 个新建 Job 大部分属于恢复积压；其中 2 个窗口内新建 Case 对应 4 个 Job，未将旧消息恢复错误表述为新实时流量。不同观察轮次恢复负载不同，前后时延改善不能解释为单项补丁的严格同负载基准。

该结果满足本次授权的发布后 10 分钟观察：资源在活跃任务回落时回收，模型完成结果能及时消费，实时工作没有容量闸门排队，业务持续进展。**尚未覆盖下一次真实开盘波峰，不能声称极端流量或所有未来故障均已解决。** Swap 存量仍高，浏览器同时占用约 10GiB 是真实共存成本；本轮没有扩大 Swap/PID 上限或压低 W3 并发来掩盖问题。

安全证据：`eval/runtime_oom_repair_20261110/observation-summary.json`、`business-end.json`、`completion-crosscheck.json`、`source-and-service-verification.json`、`final-release-regression.xml`。远端最终窗口目录：`/var/lib/doxagent/releases/runtime-oom-resultpriority-0b011358937a/`。失败/隔离窗口另存且不混用，全部记录见该目录 README。
