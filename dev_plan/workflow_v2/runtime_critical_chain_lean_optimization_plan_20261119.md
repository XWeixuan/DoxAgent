# Runtime 关键链深度优化：根因复查与精益实施方案

2026-11-19，北京时间。本轮只进行代码审查、生产只读观测与一次性机制实验，没有修改应用代码、Prompt/Skill、生产参数或服务状态，没有发布。

## 1. 推荐结论

采用 **原库内一次性注册＋减少非必要 writer 工作＋基础私有 READY 6 份** 的组合。先减少争锁入口和锁内工作，再扩两份库存；保持单后台补货者和实时 MISS 直接启动。

本轮不拆分 W3 业务数据库，也不为 W3 增加另一套 writer 队列。物理分库确实能隔离部分写入，但会破坏目前租约、Case、共识结果、交易输出与后续 Effect 的同库原子边界；仅拆 Python mutex 则不能隔离 SQLite writer。当前尚有足够明确、成本更低的减法可做。

推荐先发四类实质优化：

1. main/A/B、两条 Case Thread 及必要冻结值在一次短事务中注册，正常路径的注册写事务从5次收敛为1次；相关快照写入一起收敛，保留恢复锚点。
2. Projector ACK 改为正常推进只提交 checkpoint 与已准备的 pin 差量；取消热路径 DDL。expire 只查询并重核本页 pins。
3. READY 目标、补货和普通 PRESSURE 保护统一为6；不增加后台构建并发，不让 Case 等补货。
4. 原子合并“可从已保存 A/B 重算的共识代表结果”与 Trade 业务提交；复用 Operations 已有的 route projection 原子保存模式，减少 Trade 收尾的独立事务。

并行补齐一个仍未闭合的区间：Worker SDK 完成后至 Scheduler 接收结果的长尾。已经证明它实际存在，但尚未证明全部属于 SSE、SQLite 或某个执行器；按第7节先补现有链路的短时钟，再选择对应修复，不先发明通知机制。

## 2. 证据、版本和计时边界

生产 Scheduler/Worker 仍为 **7db63aacde026c286a108400d6f905900c1c121c**，本地及生产 checkout 为 bc44251c6，后两笔为验收资料更新。Projector、Read 等未涉及上一轮应用源码变更的角色仍为1b0494dae。不能把不同角色的 revision 不同直接视作发布遗漏。

主要证据：上一轮冻结105例及父/子准备 trace；本轮注册、writer隔离、pin查询及WAL机制实验；生产当前库存PSS、cgroup预算、近时段32例、原O3 Thread的工具记录、6例历史FAILED状态及两段有界锁采样。

当前补测窗口为 **11月19日05:15–06:08北京时间**，即11月18日21:15–22:08 UTC。这是盘后低并发窗口，不能替代开盘负载。连接器指标标注STALE，先用于确认状态与选例；时效分析依赖原数据库标量与时间回执。

沿用完整Runtime口径：持久准入→Operations最终owner返回，扣除关键模型区间并集，内部工具、准备、锁、重试、调度、SDK初始化和控制RPC计回；A/B并行不相加，W1R3辅助模型不抵扣W3内部工作。下面的分段分位数不得相加冒充端到端P95。

| 窗口 | 样本与完整性 | 完整Runtime P50/P95 |
| --- | --- | ---: |
| 上轮连续验收 | 105例全部完整 | 10.12 / 20.69s |
| 上轮两支均READY HIT | 75例 | 8.92 / 16.61s |
| 上轮至少一支MISS | 30例 | 14.85 / 25.02s |
| 本轮盘后补测 | 32例W3完成，31例全部时钟可核验 | 31例为5.95 / 11.68s |

本轮另1例 `case_497648df1f824872912282da2a269ea5` 缺前置turn模型时钟，保留为未知，不能将其未知模型时间当零。按现存时钟保守计算约20.24秒，不作为精确内部耗时。31例的Trade为2.80/8.60s、Operations为1.75/2.84s。低峰更快但仍超标，不能据小窗口宣称稳定达标。

## 3. 真正需要处理的关键链

### 3.1 “38次begin”并不等于38次写事务

原慢MU初始owner共38次 `journal.begin`，包含读取及失败尝试。其 gate 33次包含反复领取；不能再把33次当33个不同业务写入。

本轮在临时库调用当前正常注册API序列，30个Case共 **540次读事务、150次写事务**：每例18读、5写，涵盖main/A/B begin、重复stage/bound/template读取与两次create_case。一次性注册成本原型为30次写事务。该原型尚未包含生产全部冻结与fencing分支，证明的是可收敛的事务结构，不是生产耗时预测；本地毫秒数据不能按比例外推数秒锁等待。

正常v6初始owner还含近期成交快照、global input冻结和可选时间原点等，因此生产能看到约8次写入尝试。优先减少真实写锁入口及重复读取，而不是按38次写事务设计分库。

### 3.2 双HIT也有独立长尾

从上轮105例重新按互斥关键区间拆分：

| Trade区间 | 双HIT 75例 P50/P95 | 有MISS 30例 P50/P95 |
| --- | ---: | ---: |
| route Effect创建→首owner | 0.63/1.57s | 0.79/2.88s |
| 首owner执行 | 1.21/3.26s | 1.63/6.41s |
| 首owner结束→第一个SDK派发 | 0.98/2.40s | 3.66/12.67s |
| 最后SDK结束→最终owner开始 | 0.22/2.38s | 0.26/1.04s |
| 最终owner执行 | 1.00/1.80s | 1.02/2.35s |

双HIT不存在物理冷准备，却仍有领取、串行注册、绑定/材料/Job派发和结果交接开销。因此READY 6份必须配合事务与交接减法，不能独立承担目标。

代表性慢例：

- **BE `case_a6d1f30f482b464f82ba9bbaf516f818`，双HIT**：完整Runtime17.32s；首owner2.69s，gate累计1.10s；首owner→首SDK1.34s；末SDK→末owner4.73s；最终owner0.97s。另有0.14s本地研究工具，计回内部。
- **INTC `case_c3f15cd7bc694ec0ab10d466525424b0`，双HIT**：完整17.21s；前置内部5.35s，route→owner1.57s，首owner2.73s，派发前2.28s。单改结果保存或库存都覆盖不了这些区间。
- **COHR `case_7ef392b2965841eba42bb7789b1d40e6`，双HIT**：完整16.63s，前置内部6.86s、Operations5.28s；首owner仅1.18s、gate不到1ms。不是所有慢Case都有同一个瓶颈。
- **MU `case_d09c3b6908904cee91e6c0d736adf13c`，双MISS**：完整29.82s；首owner6.41s，其中gate累计3.18s、退避0.84s；两个READY等待重叠约9.5s。begin、gate、退避为嵌套关系，不能重复相加。

### 3.3 writer责任已能区分，尚不能逐笔归因全部长尾

上轮业务活跃窗口捕到Runtime writer来自Scheduler、Projector ACK和Executor；本轮低峰另捕到Transport Maintenance，非阻塞栈在 `source_lifecycle.expire` 路径。采样次数不是持锁时间比例；栈与内核锁之间有短时间差，不将整个函数墙钟当作写锁持有时间。

`journal.gate` 是同进程mutex；`sqlite_begin` 是跨连接/跨进程writer准入；短写owner的 `queued_ms` 又是第三个区间。前者慢不能直接归因Projector，后者慢也不能靠再加Python优先级解决。主owner及队列回调仍缺逐笔对应，应以慢事务的实际持有callback补证，禁止笼统写成“SQLite慢”。

## 4. writer隔离与事务合并的取舍

| 方向 | 实际能力 | 成本和结论 |
| --- | --- | --- |
| 同一库给W3另设mutex/线程队列 | 可改变进程内排队；无法取得并行SQLite writer | 临时库中另一独立连接仍立即SQLITE_BUSY。会把gate等待搬到BEGIN，**不采用** |
| W3 stage/Thread/Effect物理分库 | 可分离部分元数据写入 | 租约检查、共识、Case、交易输出、Operations Effect跨库；引入恢复协调与迁移，**本轮不采用** |
| 将心跳/诊断迁至独立库 | 这些数据通常可最终一致 | 需改Read/API消费者，当前频率低；先合并小写入和去无变化操作，暂不分库 |
| 原库一次注册＋writer外编译 | 减少多次writer准入及重复读；保留现有原子边界 | **首选**，只改已证明冗余的正常路径 |
| Projector/维护在writer外准备、只提交差量 | 有效减少其他写入者占用同库writer的时间 | **与上一项组合采用**，不是另建协调层 |

SQLite WAL下 `ATTACH` 只能保证各文件自身原子提交，不能保证崩溃时多个文件作为整体同时提交。因此不能用“仍是一次COMMIT”掩盖物理分库代价。[SQLite ATTACH说明](https://www.sqlite.org/lang_attach.html)

必要的同库边界保留：Effect租约/fencing与业务写；A/B已付费结果不可变；交易意图/Case/Operations创建；引用登记与内容退出重核。可以删除的边界：重复注册、可重算共识的独立中间提交、无变化控制票据、ACK内重复迁移和全量Python集合物化。

## 5. 初始owner的最小原子注册设计

涉及 `persistent_runtime_v2/w3_execution.py` 的 `trade/_trade_pair/_thread`，`w3_threads.py`，以及 `w3_v6.freeze_recent_trade`、时间原点辅助函数。

### 5.1 新正常入口

增加窄用途 `register_trade_bundle`，不建立通用事务框架：

1. 一次读取main/A/B和现有绑定、必要冻结值；获取不可变profile/compatibility及已READY公共Template。已有的Material Future/D1/D2缓存继续使用。
2. 缺失的近期成交快照、global input、refs及编码在writer外准备；writer内不读正文、不做history扫描、哈希、RPC或等Future。
3. 一次短事务校验当前Effect lease/fence；重核Template身份、兼容性和可用状态；创建缺失main/A/B stage、两份独立Case record、父线程/operation key及stage refs；用INSERT IF ABSENT冻结所需值。
4. 返回本次权威stage/record快照，正常owner不再接着调用多次 `stage()/bound()/template()/create_case()`。跨Future恢复重新读权威状态，同owner内复用，不跨owner缓存业务状态。
5. 两个分支分别领取READY和派发。**不得为了再省一次绑定事务，强制已READY的A等待仍冷准备的B。**

实测注册子链5写→1写；近期快照/global input等适合并入该必要写入，具体总数以真实owner trace核对。原型中的18次读可以大幅收敛，但生产仍需少量权威读，不能承诺“完全零读”。

### 5.2 不能遗漏的恢复边界

- main已有COMMITTED：恢复原结果，不再准备或模型调用。
- main/A/B部分已存在、已有一支paid结果、Record已绑定：保留原ID、accepted_at、Job和结果；仅补缺失项。
- main冻结值已存在：使用数据库原值，不用本轮重算值覆盖，尤其防止更新后的成交/全局状态污染旧Case。
- Template/材料尚未就绪：保留首次main接受时间和必要冻结锚点；已有材料Future完成即续接，不重新引入固定延期。不能为满足“一次事务”让首次接受时间推迟至材料完成。
- 时间原点使用首次真实执行的UTC墙钟，不能改用可冻结的journal业务时钟。将既有 `w3_time_awareness` 行的INSERT IF ABSENT并入注册；历史缺失值不伪造为事件发生时间。
- 不用完整Case保存来实现注册，避免覆盖并行W1R3；保持Trade→Operations同一选中Thread，A/B及跨Case物理Thread均独立。

这里允许少数冷恢复路径保留额外事务，目标是正常高频路径高效，不能为了少一次写牺牲恢复正确性。

## 6. 共享writer的针对性减负

### 6.1 Projector ACK：按单owner提交差量

位置：`v2_read/projector.py`、`outbox.py`、`source_lifecycle.py`。

现状正常ACK在BEGIN后执行7条DDL并加载该consumer全部pins。当前Runtime consumer有17,207个pins。上轮只读加载耗时21–72ms，不能单独解释数秒，但它是确定的可移出持锁区工作。Projector已经有signature/60秒节流，不能再声称“无变化仍每tick必定ACK”。

推荐实现：

1. 把生命周期表初始化留在正式source migration及拥有写权限的组件启动准备；正常ACK、retire和GC不重复DDL。只读SourceOutbox构造/查询不得因此自动迁移。
2. 利用已有 `_acknowledgements` 和Read路径对应的单Projector排他锁。首次/重启在writer外读一次当前consumer pins与登记元数据；正常推进用已确认成功的集合计算增删差量，checkpoint变化但repairs不变时不再读整集合。
3. pins新增/修复成功等成员变化使相应source的repairs快照失效；仅attempts或next_attempt_at变化不重建集合。正常ingest成功且未改gap成员，不再每tick读取17,207个gaps和排序。失效只在ACK成功后清除。
4. ACK短事务内验证预期consumer登记元数据、source身份及合法owner，插入新增pin、删除已解除pin、推进checkpoint/heartbeat，原子提交。发现外部变化或登记身份不符时保守退出、下次在writer外重读；不继续套用旧差量。
5. 排他条件有实际依据：`cli.py` 和Read maintenance共用 `.projector` WriterLock；snapshot使用独立 `snapshot:*` consumer。旧完整集合API继续服务snapshot/兼容调用。不能把“本进程有缓存”本身当并发保障；若未来增加同consumer的独立写者，该路径必须回到可靠核对。
6. pin变化与checkpoint必须同事务，不可先推进checkpoint后异步补pin。不删除历史gap、不伪造ACK、不改变SourceOutbox保留规则。

不增加ACK表、持久hash、队列或新状态机。若发现现有owner约束不足以安全使用差量，应先保留完整核对，将其放在writer外并重核已有登记身份；不要直接凭checkpoint相等判断pin集合相等。

### 6.2 expire只重核候选页

位置：`source_lifecycle.expire`。它目前准备前及writer内各读取全局pin集合。本轮17,207行临时库实验，整集合读取约51,627 VM步；按32个候选seq索引查约533步。数字是机制实验，不是生产时延预测。

- 首先按现有cursor/time索引选出小页，收集本页seq；只查询这些seq是否被任一consumer pin住，使用现有seq索引及 `json_each` 或有限参数。
- 异常包解析、hash/编码及文件准备在writer外完成。
- 短事务内重核epoch、floor、页cursor、原行身份及**本页pins**。准备后新出现的pin而没有对应异常包时，保守跳过并留待重查，不越过该候选推进退出游标。
- pins被解除时可以多保留一次已准备内容，但不能提前删除仍被引用的内容；现有hold、snapshot lease、引用重核和detach/claim保护保留。
- 空页且无退出/游标变化则不进入无意义写事务。页间释放writer，复用现有15秒维护循环和资源reserve；不新增调度服务。

`source_native_gc` 的schema缓存、实际目录检查预算和已登记文件不重复verify已上线，继续使用。collect/retire剩余热路径DDL同样移到组件准备。必须保留受引用保护的最小原子rename/状态变化，禁止把“全部文件操作移出锁”解释为允许先失去引用保护再删文件。

### 6.3 Executor、Control及Effect

- `trade_execution/worker.scheduler_health` 每10秒分别写health和heartbeat：合并为一次小事务。它是已确认写入者，但不能把这一低频动作说成主要数秒根因。
- `ControlRepository.dispatch` 当前即使ticket已存在也进入writer。现有ticket＋当前epoch/removal_epoch/analysis_allowed在同一只读快照均有效时可以只读复用；缺失或变化仍走原事务。更优的W3正常新派发路径是在 `ReceiptWorker._admit` 保存冻结request/generation的必要事务中完成ticket校验/插入，避免单独一笔Control写。
- 成功Job/已付费receipt消费继续绕过Provider Pause；accepted、unknown、确定404三态保持，不能因减少检查而重复派发。
- Effect领取已经按pending/running索引预探测，payload解码在writer外；`wake_effects/_pump_effects`已有即时续接，不重写。优先缩短其竞争的writer，不增加新的优先队列。
- Effect最终完成与控制收尾涉及仍有效的lease，不能把Effect提前改COMPLETED后再让旧owner执行需fence的写入。没有实测收益前保留该边界，不扩成全Runtime Effect重构。

## 7. 结果交接及Operations：已证实与待闭合分开

### 7.1 BE的4.73秒不是最终owner本身

该BE分支最后SDK完成14:02:03.774756 UTC，Worker Job.finished为03.828258，Scheduler `worker.result_received` 为07.649193，回执保存08.498248，owner开始08.500312。拆分为：

- SDK结束→Job.finished字段：约54ms。
- Job.finished字段→Scheduler结果接收：约3.82s。
- 接收→回执保存：约0.85s，包含短写等待、一次失败重试及编码。
- 回执保存→Future完成/owner启动：约2ms，已有即时续接在该处有效。

Job.finished/updated时间在保存前构造，**不是数据库COMMIT返回时钟**。本轮取回Worker原trace发现其在 `_run` 返回时结束，而真正 `_settle_execution → _update` 在随后执行；因此“Worker trace里没看到慢写”不能排除settlement慢写。旧events已按ACK退出，不能把空events当未产生过通知。

待闭合的3.82秒可能含Worker终态保存、通知/SSE、默认I/O执行器、HTTP获取和客户端恢复；当前不能将其全部写成已证实SSE网络延时或SQLite锁。

下一批最小观测与修复决策：

1. 把现有Worker trace作用域覆盖settlement；标记终态写入提交返回、notify、后续audit镜像，记录相应write_io队列及callback。
2. `jobs.events`标记终态查询开始/结束和stream结束，client标记SSE返回、final GET开始/接收、解析结束；全部现有trace/慢样本，不写新业务状态、不记录正文。
3. 若终态SQL慢，按具体持有者缩减Worker writer工作；若提交已快而SSE/GET慢，消除实际重复读取、无价值事件页或执行器占用。当前“SQL提交后先notify再audit”的正确路径保留，不重复实现。
4. 不增加250ms轮询兜底、放宽超时、复制完整终态到新缓存或新建通知服务；断流仍按原Job GET恢复。

### 7.2 删除可重算共识的中间事务

位置：`w3_execution._commit_pair/_commit_trade`。现状已批保存A/B，随后仍独立持久化派生main代表结果，再开启Trade业务提交事务。

推荐将main共识结果/代表绑定更新并入现有Trade业务事务：A/B付费结果先按原机制保存，聚合在writer外计算，事务内校验A/B Job/result未变、原main结果如存在须一致，再写main绑定、交易输出、Case、Operations Effect与commit_stage。

共识可由不可变A/B确定性重算，不需要额外“结果已派生”的恢复屏障。旧RESULT_SAVED读取兼容，缺一支时不能提交；技术错误不是NO_TRADE。synthetic NO_TRADE仍保留来源与正确Operations线程，不能伪造代表Job。

Trade的 `_sync_route_case` 仍在业务提交后独立执行，可复用Operations已经采用的 route_payload 预编码＋同事务保存。保留旧COMMITTED恢复的补投影能力，但新正常路径少一次独立读写。Operations已有多事务合并、批结果保存及fast/default切换不重做。

这是正常每Case额外减少约两次写锁入口的方向，实际秒级收益取决于竞争，不用本地SQL耗时直接承诺。generic Effect收尾与ACK必要意图保留，实际资源释放继续异步执行。

## 8. READY 6份：资源、供给和冷路径一起验收

### 8.1 实际成本与收益范围

当前4份真实READY子树PSS为165,134、175,296、173,144、166,913 KiB，每份约161–171MiB；每份为Python＋Native两进程。增两份预计增加 **约0.31–0.34GiB PSS（均值约0.32GiB）、4个进程**，还需验收实际MCP/fd及高峰增量。

实测Worker memory.current约4.02GiB，硬上限20GiB，宿主可用约13.7GiB；当前无READY环境变量覆盖。该时点有余量，但不能用静态余量保证所有高峰安全。普通PRESSURE保6，真实严重内存压力继续允许保护性回收；不能在OOM边缘强行保库存。

对上轮210次实际领取时点做单后台构建者的供给敏感性重放，实时MISS仍直派，BUILDING被Case认领后补替代库存：

| 固定单份服务时间，空库存起步 | 目标4模拟MISS | 目标6模拟MISS |
| --- | ---: | ---: |
| 2.61s，实测后台构建P50附近 | 43/210 | 23/210 |
| 4.62s，实测后台构建P95附近 | 75/210 | 48/210 |

真实为56/210；模拟未计CPU竞争、实际补货唤醒间隙和配置恢复，不能当生产命中预测。它支持“多两份可覆盖部分突发”，也证明构建变慢时6仍缺货。不能据模拟推算Runtime P95达标或省下多少确定秒数。

### 8.2 具体配置边界

在 `settings.py` 将Luna基础目标设6，并在正式发布配置中显式核验 `DOXAGENT_W3_READY_LUNA=6`；`W3ReadySlots.target/configure/fill` 和 `CapsuleRuntime.reap_idle` 的保护全部使用同一目标。只服务实际active Luna scope，不给未使用Sol配置额外建库存。

保留一个未绑定后台构建者；BOUND构建不占补货名额，认领BUILDING继续升级原operation，不重启物理准备。目标变化不改Template/Case身份。实时MISS不等补货、不受后台库存锁限流。首次发布/Worker重启通过已有public preparation尽早恢复当前活跃配置；不能等第一条Case才开始配置，也不能要求第一条Case等6份全就绪。

单变量先4→6，不同时改builder数量和压力阈值。现在基础补货本就不走CPU/IO投机阈值；已有ordinary-pressure保护逻辑保持，不能再宣称需要重复修一次“CPU过高必然禁基础补货”。

### 8.3 冷准备及READY之后的减法

上一轮最慢MU两支Boot wall5.17/5.56s、Python CPU约1.66s；Native Fork2.04/1.49s，Fork checkpoint仅65–73ms。子进程控制完成至父回包还有约0.7s，不能再把数秒全部归给checkpoint。

本轮在生产容器低优先级运行一次**仅导入、不启动SDK、不调用模型**的独立Python：schema CPU约0.30s、SDK模块CPU约1.12s，总约1.42s；最大模块为 `openai_codex.generated.v2_all`。该进程nice19且启用importtime，其墙钟不能当实时冷启动基准。它解释了每个新Python进程有真实协议模型构造成本，Boot wall与Python CPU之差仍需子树调度/I/O时钟，不能统称负载。

- 优先减少冷启动次数、后台无效I/O和重复准备，不改SDK生成协议Schema，不引入forkserver/新常驻daemon。
- 可后置评估将Worker依赖的少量枚举/常量从庞大的codex_runtime schema分离，并由旧模块原样re-export；只有确认import收益、类型身份及JSON Schema字节保持不变才做。它不是第一批达标前置条件。
- Slot READY→SDK仍包括Runtime绑定、Case overlay、冻结request/generation、Control及HTTP派发。绑定是跨Worker恢复的现有权威屏障，本轮保留每分支独立短写；首先通过注册、ACK及Control合并减少其竞争，不能把绑定直接删掉或等A/B一起绑定。
- 父子回包约0.7秒补 `fork receipt fsync/发送/父端收到` 时钟；证实后减少重复保存或不必要交接，保留首次Fork身份回执。不能仅延长RPC预算。

## 9. Read WAL与后台确定性减负

### 9.1 WAL完整影响面

当前四库实测均WAL，上一轮14:04恢复的Read模式仍有效。本轮不把生产切回DELETE做性能实验。

`ReadStore.migrate()` 的已有v4提前return确实绕过WAL设置；另一个关键遗漏是 **正式projector/API启动并不普遍调用migrate**，只修该函数不足以保证恢复后的正式运行。

推荐窄用途一次性 `ensure_wal`：已是WAL时只读确认；转换在显式migration、Read正式启动准备和serving文件激活/恢复时执行，位于业务事务之外并检查返回值确为wal。普通connect/每请求不执行转换。

当前API、Projector、Read Maintenance及其他Read消费者统一由正式owner在需要转换时协调：停止旧Read消费者、确认使用者退出、转换并校验、再恢复；W3 Worker/Scheduler不为Read模式转换停机。API运行时只使用已经验证的serving，不在读请求偷偷尝试DDL或切模式。

局部实验确认：活动DELETE读事务会令WAL转换返回database locked；释放后转换成功；已WAL时在另一writer运行期间只读检查仍可返回wal。SQLite backup实验目标保留WAL，因此不能声称所有备份一定重置模式。现有serving_generation/shadow_compact末尾已设WAL，继续验证返回值；VACUUM INTO或其他恢复产物都在激活前实测模式，不作假设。[WAL持久性说明](https://www.sqlite.org/wal.html)、[journal_mode约束](https://www.sqlite.org/pragma.html#pragma_journal_mode)

验收用真实投影events/s、writer wait/hold/commit、checkpoint lag及读API分位；当前Read已WAL，所以永久启动修复主要防回退，不能冒领本轮一次新P95收益。

### 9.2 O3无界搜索已证实重复发生

从上一轮问题Job及最近5个Document3 MAINTAIN原Thread各有界读取工具记录，共6份，3份出现 `find .. -name AGENTS.md -print`，其中最近5份有2份。该样本不是随机总体，不能把比例外推为全Agent发生率。原AGENTS已经在cwd，旧问题进程曾持续处于D状态；原Job最终succeeded。

对应实际路径是 **Document3 v2.1** 的 `runner_v21.py`，不是Event Library O2。宿主已经放入AGENTS、common、maintain、task与schemas；技术prompt目前仍使用“assets目录下的common/当前阶段”这样的间接定位，原Thread也反复枚举assets。

最小修改：在现有技术bootstrap prompt明确当前run根、`./AGENTS.md`、完整task/common/当前phase资产/schema路径；直接使用已有read_mapping，禁止为发现指令文件搜索共享父目录。只改技术导航，不改业务研判方法、W3 Prompt/Skill或输出Schema。新请求版本化冻结，已受理旧请求不重写prompt/sha、不重发。

同类文件驱动runner若仍给模糊目录提示，统一提供已知文件路径；必要资料按明确mapping读取。不要写通用shell命令拦截器，不禁止合法的当前run内有界枚举。W3插件市场关闭已有效，不重复实现；非W3 Native插件刷新等尚缺完整工具影响面证据，暂不全局关闭，也不因此扩大第一批范围。

### 9.3 生命周期、日志与历史失败

- Time Awareness只对预期BUSY/LOCKED降为无堆栈debug，其他SQLite故障继续可见；新路径把原点并入注册后避免独立抢锁。不新增日志计数表或持久诊断队列。
- `retire_consumer`取消重复DDL。若大量pin删除确有持锁长尾，按现有退出owner分小页，最后才移除consumer保护；没有实测贡献不重构整套退休机制。
- 最新只读复核仍是11:38 UTC发布窗口6例FAILED，5例MATERIALIZE、1例CONTEXT；stage均未绑定Job、未发现Trade record。该证据不足以证明远端从未受理。历史恢复需按原operation/workspace/idempotency查询Job三态，先核对交易终态；不自动把旧新闻作为新实时Case重跑交易。本轮未改这些状态。
- Fact Bridge、材料Future、D1/D2复用、结果批保存、GC不复建COLD等已验证项不重做。Source库存、文件保护和已付费结果不能因“生命周期简化”被提前释放。

## 10. 最小实施批次与验收

| 顺序 | 最小改动与代码位置 | 主要收益 | 放行与归因要求 |
| --- | --- | --- | --- |
| A | 原子注册：w3_execution/w3_threads，快照准备与原点辅助 | P50/P95；少4个注册写锁入口及重复读 | 注入partial A/B、lease丢失、材料Future和并行W1R3；保持模型/库存4不变，独立观察 |
| B1 | Projector ACK差量与gap成员失效；outbox/source_lifecycle正常路径无DDL | 高峰P95与长期资源 | 原17,207 pins不变、gap增删/重启/ACK失败/consumer变化，确认writer持有真实缩短 |
| B2 | expire候选pins、空页短路、维护初始化 | writer长尾及规模放大 | 竞态新增pin、全保护、epoch/cursor变更、异常包与引用退出，按实际检查量验收 |
| C | O3已知路径bootstrap；Read一次性WAL准备；可选日志降噪 | 背景I/O、防回退 | 不替换在途prompt，验证各Read消费者及恢复；不要把WAL已恢复的运行收益重复计算 |
| D | 仅READY4→6，settings/正式配置/统一保护 | 主要改善P95 | 一个后台builder；同版本连续曲线、PSS/CPU/I/O/Swap实测，保留MISS直派 |
| E | 派生共识与Trade同事务、Trade route projection同事务；Control票据并入必要admit | P50及结果竞争P95 | paid分支、synthetic冲突、旧RESULT_SAVED、提交失败恢复、停机/removal/probe三态 |
| 贯穿A–E | settlement/SSE/GET及父子回包少量时钟；根据真实慢span定向修复 | 覆盖尚未闭合长尾 | 只保留内容无关标量，不新建状态层；计时本身不算性能修复 |

B1/B2分别发布，不把Projector、维护、库存和共识全放进一次大改。C的Read修正和O3导航也可单独发布核验。若直接证据显示某个结果交接区间为最大项，可提前其针对性修复，但不要带入未验证猜测。

必要正确性测试至少覆盖：

- 正常双任务、单任务与旧契约；重复注册、已有一支结果、两支结果未提交；原Thread/Job、冻结值和accepted_at保持；真实墙钟与业务时钟区分。
- 新owner抢占、过期fence、材料完成竞态、进程重启；已受理/未知/确定404不能混淆，成功receipt消费不被Provider Pause阻止。
- ACK/gap成员增删、source epoch/consumer变化、snapshot独立consumer；新pin竞争、异常包覆盖、页游标和引用复活。
- READY目标6、普通压力保护6、严重内存保护、scope变化、动态绑定、库存耗尽；实时不受后台构建者限制。
- 共识与交易原子回滚，Case/Operations Effect不半提交；技术缺分支不变NO_TRADE，恢复不再次调用已付费模型。
- v4 DELETE库启动、活跃读者阻碍转换、已WAL无变化、文件恢复/alias切换、只读API不做转换；O3旧请求冻结与新导航准确性。

## 11. 性能预算、资源与结束条件

以下是工程预算，不是拿当前慢值当合理标准：

| 工作 | 初始P50/P95预算 |
| --- | ---: |
| route创建→首owner可执行 | ≤0.05/0.25s |
| 缓存材料正常初始注册owner | ≤0.15/0.50s |
| READY HIT→SDK派发，含绑定/overlay/request准入 | ≤0.25/0.75s |
| Worker终态提交→Scheduler收到 | ≤0.05/0.25s |
| Trade最终owner | ≤0.20/0.75s |
| Operations本地准备＋结果收尾 | ≤0.75/1.50s |
| 普通必要短事务writer持有 | 先以P95≤20ms定位，超标查具体工作 |

冷准备仍全部计入，不能用HIT组替代全量。先把冷准备压回可解释的数秒，并结合6份库存降低长尾暴露；最终以端到端而非子预算验收。

每个有实质行为变化的批次记录提交、镜像、实际配置及源文件身份。最终至少100个连续完整实时Case，覆盖真实开盘负载，保留全部准入名单、失败/在途/未知时钟、HIT/MISS和全部冷例；完整Runtime非SDK **P95≤10秒**，P50进一步降低，Trade/Operations分列。不同分位数不相加，不以排除启动20分钟或排除冷例通过。

READY6放行除性能外，还应满足：普通压力仍实际保有6份；没有新增实时等待机制；稳态额外PSS在实测约0.34GiB量级且不持续增长；补货吞吐改善而非构建更慢；OOM/restart/quarantine为0；CPU/I/O PSI、swap-in/out与物理写入无持续恶化。若回退先恢复库存4，不能降低实时并发。

验收未通过就交付实际慢链、未闭合区间与下一项最小修复，不追加未经证实的队列、分库或驻留服务。当前已有证据足以先执行A、B和6份候选；不足以保证只完成其中一项就能从20.69秒直接到10秒。

## 12. 本轮证据目录

`eval/runtime_critical_chain_design_20261119/`：

- `chain-summary.json`：105例分组关键链、双HIT慢例、新窗口31例与1例未知。
- `mechanisms.py / mechanism-results.json`：当前注册事务数、原子成本原型、同库/分库writer、候选pin查询与WAL并发机制；临时库测试，无生产业务写。
- `ready_replay.py / ready-replay.json`：实际210次领取的4/6供给敏感性，非生产性能预测。
- `pool-memory.json / ready-budget.json / health.json / inventory.json`：真实PSS、目标配置、资源和库存；字段不含认证配置。
- `be-worker-traces.json / be-terminal-events.json`：BE结果交接、未覆盖settlement及已退出events的证据。
- `production-guards-and-writers.json`：当前四库WAL、6例历史失败与有界writer采样；首次采样角色映射不完整的数据没有当作精确归因。
- `background-tools.json / background-searches.json`：6份原Thread的有界工具抽样；自定义exec工具与函数调用分别解析，不把未匹配解析器的空列表当没有搜索。
- `python-import-profile.json`：一次低优先级纯导入测量，未启动SDK或调用模型。

没有全库扫描、生产WAL切换、强制预热、模型重放、Job重派发、服务重启或业务状态修复。已有他人changelog修改保持原样。
