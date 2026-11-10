# Runtime OOM / IO 修复实施与发布记录

2026-11-10，依据同目录完整事故方案执行。模型、Prompt、Skill、LLM Schema、路由、交易规则、冻结历史窗口不变。

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

待追加：实际提交、镜像、恢复时间、10 分钟主机/容器资源与 Case 进展。采样只读 rowid 小窗、active job 索引、cgroup 与 PSI，不执行历史大统计。本次 10 分钟观察不代表已经覆盖下一交易日真实波峰。


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
