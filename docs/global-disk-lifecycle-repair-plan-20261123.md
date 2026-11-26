# DoxAgent 全局磁盘占用与数据生命周期修复方案

日期为 2026-11-23。本文件供后续开发、迁移、发布和持续验收使用。

## 1. 总体决策与审查基线

采用五项相互配合的调整。

1. 先关闭 Runtime 自动 O3 Maintenance 的创建和消费入口，停止积压继续放大。当前 O2→O3→activation 是一个复合任务，暂停该入口会同时暂停这批任务中的 O2；Case、W1/W2、W3、消息接收、交易与已有正式版本继续运行。暂停期间用小型待处理标记保留业务义务。
2. Event 和 D3 当前版本正常服务，历史保存对象级实质变化、必要元数据和恢复索引。同一内容保存一次。低频历史恢复允许耗时，历史 SQLite 分支、整套 PolicySet 和全量 projection 不继续按运行次数累积。
3. O3 输入在家族内冻结一次。导航读取原文件或 JSON 路径，取消把原文件拆成多层副本的默认行为。发布后由终态 owner 登记局部退出，后台只执行已确定的删除工作。
4. 修复传输过期游标和生产迁移缺口，缩短高频成功诊断的落盘生命周期。SQLite 采用必要的一次迁移与后续小批物理回收，停止把不断产生的空页永久留给根盘。
5. 正常业务上线、历史迁移和连续交易日容量验收分开交付。验收采用实际物理占用和写入成本，不能以清理行数或一时余量代替。

### 1.1 本轮实际核查范围

- 本地 HEAD 为 `434711945`，对应 11-21 Runtime/Bus ingress 发布回执；本地已知 `origin/main` 为 `2843e8403`。两者相差四个提交，新增内容为 MCP 延后读取及其生产恢复回执，受审存储模块无产品代码差异。本轮读取这些提交内容，未切分支、合并或覆盖文件。
- 工作区另有 W3 离线回放、评估和 changelog 修改。已查看 `w3.py` 差异，它涉及回放 Skill 装配。后续实施必须在当时最新 main 上适配，禁止把这些工作顺带回滚。
- 深读 `persistent_runtime_v2/coordinator.py`、`maintenance.py`、`journal.py`、Event Repository、D3 runner/publication/repository、Read formal/lifecycle/schema/GC、Acquisition store/maintenance、Transport/Native GC、Worker 资源与会话退出、SDK 本地构建入口。
- 生产容量、队列和表数量引用 11-23 已落盘调查与此前执行记录。本轮没有重新探测生产、调整开关、清理、迁移或发布。所列当前生产占用均指该调查窗口；O3 的实时队列数量、等待时间和阻塞占比仍需实施前补取。

历史与现场依据见 [11-23 问题发现](root-disk-regrowth-findings-20261123.md)、[11-18 源头减量方案](doxagent-global-storage-source-reduction-plan-20261118.md)、[开发交付与剩余项](storage-source-reduction-development-20261118.md)、[11-11 生命周期方案](doxagent-storage-lifecycle-optimization-plan-20261111.md)、[11-14 清理和生成机制](disk-cleanup-and-growth-mechanisms-20261114.md)、[11-21 ingress 方案及实现基线](../dev_plan/workflow_v2/runtime_bus_ingress_latency_root_cause_and_lean_repair_plan_20261121.md)。

### 1.2 按问题量级排序

| 工作包 | 已证实的规模或增长 | 优先级与决策 |
| --- | --- | --- |
| M0/M1，Maintenance 入口和工作区 | 工作区约 100.15 GiB；校正上次清退后基线约增长 30.95 GiB。新 Maintenance 出生队列在两个交易日分别留盘 9.50、11.65 GiB | P0。先暂停，再改变触发粒度、物化与终态责任 |
| E1，Event 分支 | 新分支约 7.91 GiB，两个高负载日约 2.5 GiB/日；173 个样本中 159 个未发布更高版本 | P0。取消逐 run 克隆；实质变化写增量 |
| T1，传输退出 | Research 无 pin 的过龄已 ACK 记录 66,345 条；Runtime 按自身 7d 规则有 270,200 条落在游标前 | P0。修回退重访和正式索引迁移，释放 Native 的无价值引用 |
| D1，D3 版本 | 正式和 staged 两表约 23.94 GiB，约占 Research 主库八成；主库约增 4.09 GiB | P1。单份当前内容、对象差异历史，取消历史 projection |
| R1，Read/Runtime | Read 目录约增 4.27 GiB；policy 四类 current 合计超过 360 万行；Runtime Native 约增 2.04 GiB | P1。改消费者契约和生产端载荷，保留热路径必需索引 |
| A1/P1，Acquisition 与物理回收 | 11-23 缩库物理差约 11 GiB；逻辑删除已有效，空页仍占根盘 | P1，与 T1 一起开发。高频成功证据变薄，自动回收物理页 |
| S1/O1，SDK、浏览器、Ops 等 | SDK logs 约 3.8 GiB；会话、缓存和发布有增量但量级较低 | P2。完成局部退出和诊断限额，避免抢占主任务 |

表中对象有包含关系，收益不能直接求和。30.95 GiB 为清退回执校正估计；新目录出生队列量与同目录净增不是两个独立收益。历史高水位缩库属于存量回收。

## 2. 历史修复复盘形成的设计基线

| 已做的事情 | 本轮判断 | 本轮必须补上的责任 |
| --- | --- | --- |
| 10-13 文章稳定业务键去重、拒绝分发退出，后续稳定依赖构建与发布退役 | 有效，应保留。11-23 BuildKit 仅约 0.09 GiB，Bus 主文件基本横盘 | 不沿用“镜像就是主因”的过时判断；继续统一现有 release owner |
| 10-18 Read V4、共享 definitions、短 MVCC，初始整目录约 3.11 GiB | 降低了当时体积；随后版本化 current、目录和贡献仍持续增多 | 把当前活跃服务、历史证据和衍生检索关系拆清楚，按内容变化写入 |
| 11-11 Worker 低频自动退出 | 后来因全局引用扫描影响 W3，在 `c5d9759d94` 撤掉 `collect_inputs()` 常驻调用 | 用家族/版本的局部退出替代。恢复旧全库扫描会重新引入延时问题 |
| 11-14、11-18 多轮人工去副本、清空目录和缩库 | 净释放真实，但相同生成路径继续存在 | 删除文件时退空目录；取消多份输入、发布副本和逐 run Event 库 |
| 11-18 gzip 正文引用、staged receipt 瘦身 | 有效。新发布记录已为 gzip 引用，新 staged receipt 不再保存完整 projection | 进一步取消 staged/正式策略双写，以及 Workspace 与正式版本的双重权威 |
| 11-18 传输 48h | Bus 有过期索引，现场达到窗口；Research/Runtime 缺索引，回退单向游标失效 | 索引迁移进入发布交付，回退可重访；逐源验 oldest eligible age |
| Acquisition 小页退出与小时摘要 | 逻辑记录明显减少，实际有收益 | 将成功明细从源头收窄，旧空页必须有物理收敛路径 |
| Runtime 线程隔离、READY、短状态读取、入站合并写、锁外 Native 准备与 ACK 差量 | 近期性能修复必须保留 | 新治理走组件后台；真实任务启动不等待归档、压缩、查全局引用或恢复历史 |

此前失败的整体机制是高频创建全量副本、退出成本高而被暂停、低预算扫描漏过到龄对象、逻辑退出未兑现物理余量，以及未完成跨交易日的增长验收。多加一个定时 GC 不能覆盖这些原因。

## 3. 最小必要的数据与恢复职责

| 对象 | 正常运行保存什么 | 历史与过程如何处理 |
| --- | --- | --- |
| Event/Fact | 当前对象、状态、成员、关系、Reference 所需字段；当前激活版本与 W1 日基线 | 一次基线，加真正变化对象的内容哈希和状态/关系变化。保留首次获知时间，旧整库按需重建 |
| D3 | 当前正式 PolicySet、当前执行 projection、轻量版本与采纳记录 | 政策对象/条件变化与集合关系变化；旧完整 projection 可复算，过程 Workspace 可退 |
| Case/交易/Policy 消费 | 决策、订单/成交、真实时间、精确输入身份、消费代次、必要引用 | 不可替代的业务事实长期保留。冗长工具输出、重复输入和运行日志按 owner 退出 |
| Read | 页面当前读模型、当前 KPI 需要的查询索引、30min 协议前像与实际有效 token | 小型历史目录和事实指标保留；旧大详情从业务历史恢复，恢复产物短暂缓存 |
| CDC/Native | 消费者尚需的记录、短期重放、精确异常 pin | 到龄且 ACK 后退出。当前态重建 Read 替代无限传输史 |
| Acquisition | 在途/待接收 payload、调度 head、薄进度证明 | 小时统计、有限异常样本；成功逐次过程证据快速合并退出 |
| SDK/浏览器/验证 | 活跃会话与浏览器身份、当前恢复状态 | 已消费会话的工具流水、日志、缓存与完成的临时库有明确退出边界 |

历史恢复材料仍会占空间，只保存独有变化及小元数据。当前存储预算优先给正常业务；历史无法恢复时明确报错并保留未知 provenance。D 盘档案可用于低频人工恢复，不能成为生产当前读、W1 或 W3 的依赖。

## 4. M0，立即可交付的 O3 Maintenance 暂停开关

### 4.1 开关位置与语义

在 `initialization/control.sqlite3` 增加单行 `maintenance_control`，字段为 `id=1、enabled、revision、updated_at、reason`，对外名称为 `o3_maintenance_enabled`。该库正是正式 `activate_runtime_bundle()` 的事务所在库。复用控制 CLI 与只读状态展示，不另建配置服务。首批发布显式设置为关闭。开关控制 Runtime 自动 Maintenance 家族，初始化 D3 和人工独立研究按自己的入口执行，人工明确创建的 Maintenance 也经过相同 gate。

当前 `MAINTENANCE` 任务包含 O2、O3、Reference 交接和 activation，首批暂停整个复合任务。若只屏蔽 O3 末端，仍会生成 Event 库并把旧策略与新 Reference 留在半成品状态。已有正式 Event/D3 组合继续服务。

开关检查必须覆盖以下路径，不能只在 `_tick` 跳过一次派发。

| 入口 | 实施点 | 关闭后的行为 |
| --- | --- | --- |
| 日切与重启补账 | `coordinator.reconcile()` / `_schedule()` | `schedule.last_boundary` 照常推进；将未处理业务义务压到小型 pending 标记，禁止以后补建每一天的空壳任务 |
| Sweep 完成 | `_sweep()` 创建 `maintain:<sweep>` | Sweep/Case 原处理完成；登记该批的输入身份与待维护范围，停止创建 Maintenance |
| 补偿/补充维护 | `_schedule_supplements()` / `_schedule_case_supplement()` | 保留 `dirty_case`，不逐 Case/效果终态创建新模型任务；禁止“暂停即删除 dirty” |
| 失败恢复 | `maintenance_recovery.recover_failed()` / 人工 resume | 未批准恢复的 Maintenance 不被自动复活，其他 SWEEP 恢复继续正常 |
| Runtime claim 与 Worker dispatch | `journal.claim()` 的 Maintenance 路径、`maintenance.py` 每阶段、Worker 精确 node/phase | 已存在 queued Maintenance 也不能绕过开关启动。对确切 O3 家族检查，不能泛停 background lane |
| 正式采纳 | `activate_runtime_bundle()` 所在控制事务 | 在最终采纳的同一个控制库事务检查开关 revision 和 base。暂停后旧 Worker 回执不能迟到激活 |

最终 gate 在正式采纳的同库事务检查。Scheduler 每轮复用一次小标量读取，Worker 仅在明确 Maintenance 的提交/启动边界读取，不给普通 W3 增加配置查询。修改开关后协调器立即进入暂停处理，并等待具体 owner drain 回执确认；不能宣称跨库 queued/running 状态瞬时原子变化。只读 UI 同时展示请求时间、有效 revision、仍在退出的 job 数与阻塞原因。

### 4.2 已有任务和运行中的任务

1. 分页列出 `runtime_tasks.kind=MAINTENANCE`，沿 `worker_requests`、`worker_invocations` 与确切 run/job/thread ID 关联 Worker。记录 scope、phase、age、base activation、Worker queued/running、已接受输出及是否已正式采纳。一次只读汇总，不扫描工具对话正文。
2. PENDING 与可明确关联的 queued Jobs 原地挂起。Runtime 复用 `HELD` 和专用 `pause_reason=O3_MAINTENANCE_DISABLED`，保留原 ID、cutoff、generation 和已成功阶段。Worker 通过确切 job 的取消/阻止派发处理 queued 状态，记录“暂停取消”；恢复时只为确已取消的 dispatch 使用新代次。
3. RUNNING 任务在阶段边界协作停止。尚在模型中的 O3 通过现有 job cancel/turn interrupt 请求停止，确认 active turn 与清理结果后才确认暂停完成。已返回成功结果先收据落盘，再暂停正式采纳。确切调用尚无法确认时保留原身份，不重派、不清其目录；UI 明确仍在 drain。
4. 不通过终止 Scheduler/Worker 进程完成暂停。lease/token 复核阻止迟到副作用，失败预算不因管理暂停消耗。单个隔离失败只保留该家族待处理，不把全服务永久设忙。
5. `_waiting_final_maintenance()` 目前让 Selection 等待约四小时。暂停时显式记录 `maintenance_skipped_by_control`，已完成 Sweep 后 Selection 使用当前有效正式组合继续。新策略更新时效被暂停，不能把它显示为 MAINTENANCE 成功；W3 的 Policy 授权、消费代次与 watch 仍按原逻辑校验。

发布验收必须证明 paused 后新任务、新 Worker O3 调用和新 Maintenance activation 都为零，实时消息/Case/W3 继续推进。中断模型、迟到成功、Scheduler 重启、开关竞态分别做恢复测试。

## 5. M1，重新开放后的维护粒度、输入与退出

### 5.1 调度改为按有效待处理集合合并

当前 DAILY、每个 Sweep、每个迟到 Case/效果变化和失败恢复都会进入同一复合维护链。`_schedule_case_supplement()` 使用 `[owner,case_id,current_effect_versions]` 形成新 ID，变化后可以再建一个；Runtime 后台执行器上限 32、Worker 公平队列与等待 Case/父任务会共同影响耗时。现场“任务未及时执行”尚不能全部归因于某一个队列，实施前按创建→可执行→claim→Worker queued→模型开始→回执消费→采纳逐段统计。

采用现有 `dirty_case`、pending 业务记录与任务表，按 **ticker + base activation** 最多一个在途维护。尚未 claim 的变更只更新一个 pending 标记，保存最早待处理时点、最新 cutoff 和确定的记录/效果代次。claim 时冻结本批，后到变更进入下一批；不为每个迟到 Case 立刻开 O3。

首发恢复设置为每个 ticker 在既有日切/收盘维护窗口合并一批，迟到补充最多一小时一批，真实新增业务记录才触发。该频率是为实现后台更新而新增的产品取舍，需在实施回执写清“正式策略更新可能延后一小时”；不改变实时 Case 和交易消费。当前没有充分业务证据要求按每个 Case 立即维护，因此默认采用该值。

只有当输入内容签名相同且没有新 consumption、trade、badcase、coverage gap、有效 Reference 变化或到期 review 时才 NOOP。不要把待处理的真实业务记录当缓存丢掉。重复或过期 Task 先转为可查询的 superseded/retired 收据，只有继任任务真的覆盖原记录和 effect 代次后才用现有 `compensated_by` 确认补偿。已冻结 Task 的 membership/cutoff 不原地改写。

Maintenance 使用一个独立的单并发执行通道，避免和现有 `_background` 内 W3 公共资产、补货及资源退出互相排队。复用现有执行器和 Worker lane 识别，给这类维护单独并发上限 1；实时和其他业务的既有并发不调低。Worker 只在维护新 Job 启动边界让路，不反复重启已经完成的模型工作。晚间/持续繁忙时由有界维护份额推进，不依赖全局 Job 数归零。暂停期间不适用该份额，仍保持停止。

### 5.2 O3 家族只保存一份输入

| 当前生产点 | 目标改动 |
| --- | --- |
| 父 workspace 冻结 base PolicySet、Document2、Reference，子 workspace 再 `write_text` | 冻结家族 manifest，一组已验证不可变文件。子目录按相同 inode 链接必要文件；所有后续修改采用原子替换，禁止原 inode 内改写 |
| `materials_v21.attach_material_indexes()` 调用 `context_index.build_index()` | 为源文件记录路径/SHA、JSON Pointer 或字节区间。工具按需读取；默认取消 assets、records、整文 pages、章节 pages 多层完整物化 |
| task identity/generation 产生导航副本 | 每个实际不同物料生成一次轻索引，task 只保存引用。重试同一冻结输入复用 |
| runner `_commit()` 同一结果写 snapshot 和普通输出 | snapshot 作为已验收的不可变内容，普通输出为同 inode 或引用。外部工具需可写路径时先独立文件，不共享可写文件 |
| `_publish()` 与 `LocalWorkspaceStore.publish()` 写 artifacts 再 copyfile 到 published | 正式发布落一次不可变文件；已有两个路径用安全硬链接兼容，后续由版本读取接口导出。禁止再默认输出 `output/final` 或整 run 的视图 JSON |

单个输入仍大不代表无价值。模型实际需要的正文完整可读，导航工具分页/指针结果与原文件逐项一致。每个家族写入预算由“独有输入 + 新结果 + 小索引”解释，不再乘以父/子/attempt/发布路径数。

### 5.3 终态 owner 自动退出

在现有 Worker/Runtime 库中各增加或复用一个小型本地退出队列，键为实际 run 或 thread，保存 `not_before、state、last_error、retry_at`。Worker 以 `run_id` 索引其家族与终态计数，Runtime 以任务/版本的实际 owner 索引退出。队列候选使用 `(state,retry_at,not_before)` 索引，状态为 `PENDING→DELETING→DONE`，失败回到有限重试。Job 终态登记、下游消费 ACK 能并入原事务就并入，不多加逐文件事务。不建立跨模块总账。

- Worker 终态登记本族 job 数和结果引用，Runtime 下游消费成功后发精确 run ACK。索引查同族 live Jobs、当前 dispatch、显式 repair hold 和发布 manifest，不遍历全库 evidence。
- 普通成功且已消费，输入与导航在 24h 后退出；失败诊断留 72h，已取消且无独有成果留 24h。显式可恢复故障保留实际 checkpoint 所需文件，并设下一次复核时间。特殊业务 hold 有原因和 owner，不能因为过去出现过 run ID 就永久保护全族。
- 先在短事务中禁止同一退休家族再提交新 Job、登记可执行文件清单，再在锁外按目录执行删除，最后落薄收据。重启从队列继续；已经消失的文件按幂等处理。文件 I/O 失败时只阻塞该 run。
- 同时退出空祖先目录和死的索引产物。成功标记写在最终收尾完成后，不能在只删普通文件后把整 run 标成永远无需再访。
- 正式成果保存版本键、SHA、来源、模型/用量、最终响应或结果引用和必要时间。已消费的请求/事件流水与整串工具输出随后退出。

历史 backlog 只做一次可续的按 Job sequence/run 前缀分批登记；新任务直接投递 owner 事件。常驻循环启动后立即恢复 pending 队列，无六小时盲等。每批至少推进一个有限工作单元，持续活跃 W3 不影响退已确认终态的其他家族。

## 6. E1，Event Library 实质变化版本与按需历史

### 6.1 现有能力与必须改变的点

`EventLibraryRepository` 已有 Event/Fact revisions、状态和关系有效区间。`publish_bundle()` 在空 Event revision/retirement 时保持原版本；Fact payload 已比较相同内容。仍有两处放大。

1. `branch_library()` 为每个 run 复制所选版本的 canonical 内容、状态和索引。即使模型最后无变化，复制已经发生。
2. `_write_event_revisions()` 对提交的每个 Event 无条件新建 Event revision，并先关闭再重建整个成员集合和关系。候选内容相同也会触发这一组写入；Fact 的相等比较不能阻止它。

### 6.2 选定方案

保留现有 Event Repository 与发布校验器，改为 **读取固定 base → 内存/小文件保存候选变化 → 校验 → 写一次稀疏变化 → 采纳**。复合运行不再创建全量候选 SQLite。O2 initializer/service 增加“生成并验证 Bundle，延后 apply”的入口，O3 需要的候选 Reference 由 base 事件集合叠加受影响 Event/Fact/成员变化得到，使用既有 compiler 保持输出格式。仅触及实际变更对象，候选文件只保存 Bundle、base 身份和校验结果。

每个 lineage 使用稳定 library identity，版本在该 lineage 内递增。**旧 root 相同数字的 version 或 Event/Fact 编号不自动等同**。迁移用 `(old_root,ticker,version)` 到新版本的精确映射，小型 manifest 保留 lineage 与 provenance；不能只按 ticker/version 合并不同旧分支。

新数据结构沿用原表组织，必要时加少量列/本地历史表。

| 内容 | 保存方式 |
| --- | --- |
| 当前 Event、Fact | 一份当前内容，已有稳定身份；内容字段按确定 JSON 格式计算 digest |
| 内容变化 | 记录 object ID、版本、previous digest、new digest、准确 publication 时间；digest 指向已有压缩内容池。同一 digest 只保存一次，before 可指向已有旧内容 |
| 状态、成员、关联 | 只写 ADD/REMOVE/状态变化；成员顺序有业务语义时单独保存变化，不整组关闭重建 |
| 复查与无内容变化 | 必要 review 状态/下一次复查时间单独更新；无变化批次保留小收据，不新建内容版本 |
| 版本恢复 | 初始基线只做一次；此后按对象变化记录和元数据恢复指定版本。JSON 重放按对象替换，不引入通用 JSON Patch 引擎 |
| 旧大载荷 | 压缩的独有变化作为历史材料；小版本/变化索引留在组件库。旧整库索引、候选底稿和无独有内容的分支在验证后退出 |

“实质变化”不能只比较标题。Event 的解释、时间锚点/精度、状态/redirect、Reference 可见性、Fact 内容、关系和引用证据都可能影响业务。只排除 run ID、生成时点等纯运行字段；这些仍写在小收据中。有新引用而正文不变时只增引用关系，不复制整 Event。W1 Fact 获知时间沿用当前 `fact_admissions()` 的内容判定，不能用本轮归档时间改写首次发布时间。

无内容变化仍可能有 Delta resolution、dedup mapping 或 review schedule 要结算，这些小型业务状态照常提交，并保留原批次幂等收据；不能因版本不升就让相同原子事实下次反复进入 O2。真正未解决的 atomics 继续明确 pending，不能为了退出而强行置 RESOLVED。

候选阶段的临时 Event/Fact ID 先在冻结 base 下确定映射，持久化小候选 manifest。提交时同时核对 base 和 ID counter；若并发人工发布占用了编号，则重新验证映射及依赖它的 O3 候选，不能提交前后更换 ID 却沿用旧 O3 结果。每 ticker 单在途减少该冲突，但不代替最终 CAS。

### 6.3 当前与历史消费者

- 当前激活 Event、当日 W1 冻结基线、正在运行 Case、有效 watch/恢复 owner 所需版本提前准备为热数据。activation 完成前准备好这些材料，W3 启动不承担冷恢复。
- 用小型 Fact 内容 admission 记录保存首次版本、published_at 及原有未知状态，代替 `_admission_history()` 沿旧分支链追索。原 `known_index_entries()`、Fact bridge 及 provisional 编号上界逐项对照，尤其回退、合并和事实重新出现。
- Reference delivery 保存 before/after 版本和变化对象 digest、小型提交视图身份。完整前后渲染仅当前基线有需要时缓存；历史查询由版本重建，避免每次交接再保存一份完整 events+rendered。
- 低频历史页面/导出复用已有 deferred query 返回 202 和查询结果路径。临时恢复 SQLite 最多保留一个非活跃恢复任务的缓存，完成/24h 无引用后退出；多个请求复用相同恢复键。当前业务查询不调用冷恢复器。

### 6.4 跨库采纳与故障恢复

Event、Research、Control 分属不同 SQLite，不能设计成一笔虚构的全局事务。先持久化验证过的候选变化与 D3 结果，再在 Control 的单事务内比较 base activation 和 gate revision，切换正式组合。候选记录在切换前标为 prepared，不作为默认 Published head 暴露。切换后逐组件确认本地 current head，过程可幂等恢复。默认 reader 以正式 activation 的明确版本读取，避免先发布 Event 后 O3 失败造成半套可见。

base 已变化则不覆盖新正式组合，将候选交回新的合并维护；已接受模型结果可复用的部分按已有校验重用。提交失败的 prepared 结果在确定无激活引用后退出，不永久保留全量候选。该规则也应用于初始化和人工正式发布调用点。

### 6.5 迁移与收益验收

按实际活跃 lineage 先转换当前、W1 日基线和热 Case 依赖，再分批历史。保留 version 身份、inactive/redirect、成员、Native birth/provenance、首次获知时间和 Reference 编译结果。每个候选 old version 恢复后摘要一致才退出对应整库；已有 D 盘 archive 通过 locator 继续恢复。旧格式只由迁移读取器接收，新业务写入统一新路径，迁移完成退出旧分支跟随逻辑。

三方向比较后的决定如下。

| 方向 | 收益与代价 | 决策 |
| --- | --- | --- |
| 保持逐 run 整库，只提高压缩/GC 频率 | 改动小，复制与索引 I/O 每轮继续发生，退出仍争用 | 不采用为最终方案 |
| 通用多层 overlay SQL/版本文件系统 | 可以 COW，但引入跨层查询与长期兼容成本 | 不采用 |
| 现有对象 revisions + 延后 apply + 稀疏历史内容 | 需要调整 O2/O3 交接与 reader，变化面明确，热查询仍直接读当前 | 采用 |

验收同内容 Bundle 重试/复查不新增正文块和成员版本，无变化运行不新建 Event SQLite。对同一冻结负载，目标是 Event 分支新增物理量下降至少 90%，真正变化量单独记账；173 个样本的比例仅作方向证据，不当作预报收益。

## 7. D1，D3 当前单份与历史对象差异

### 7.1 决策与最小结构

不再把“版本存在”实现成“每个版本一份完整策略集”。复用 Research 库和既有压缩内容池，保留以下职责。

- 一份当前正式完整 PolicySet 和当前执行 projection，供 W3/Runtime 正常读。
- 一份小型版本头，含 ticker、version、base、schema、发布/采纳时间、run、整体摘要以及编译器 revision。相同业务内容可以有交付身份，内容不复制。
- 初始策略基线，以及按 Policy ID 的 ADD/CHANGE/REMOVE。变更行指向旧/新对象 digest，集合元数据、顺序、coverage 和 policy origins 的真实变化单独记录。同一政策只改一个条件时先采用“该政策对象替换”；进一步逐条件拆分需由测量证明收益，首版不做复杂字段差分。
- staged 只保存候选版本/候选变化引用、校验、manifest 和提交状态。正式化只更新同一候选的采纳关系，不复制整个 PolicySet。

当前 `save_staged_v3()`、`SQLiteDocument3PolicyRepository.publish()`、`publication_v21.finalize_candidate_v21()` 与后续 maintain 对 `document3.json` 的读取统一走“按版本读策略”的 Repository 接口。迁移期旧版 full payload 可读；新写只走新结构。不能一边保留新结构，一边继续双写旧 full JSON。

### 7.2 projection、Workspace 与回放

- 当前 projection 在发布准备阶段计算、校验并缓存一次。旧 projection 不再全量持久化；历史只保存 schema、compiler revision 和结果摘要。升级编译器时仍要具备旧语义的确定重建入口，不假定任何未来算法都得到同一结果。优先将已有 projection schema 的确定转换函数固化为受测的格式版本；确实不能复算的少量旧投影，作为单项例外保留必要结果，不冻结整库。
- 活跃 Case/watch 使用其真实已冻结策略/条件和 guard，不因 current 切换而按新 projection 解释旧决定。必要旧版本在正式切换前准备热缓存，完成最后引用后转冷；旧历史恢复不进入 W3 请求链。
- Workspace 的 document3/runtime_projection/Markdown 为发布导出视图。需要的模型输入按版本 materialize 到当次运行，完成后退出；历史导出按需生成。唯一研究文本且无法重建的正文留一份。staged 与正式、Workspace 与 gzip 池不得各自承担同一数据的长期权威责任。
- 版本号、policy ID、condition ID/revision、`policy_activation_revision`、usage generation、已消费方向、watch、policy origins 的语义独立于内容 digest。正文相同也不能合并两次有不同消费授权的激活。
- 区分确定 JSON 的内容摘要与旧文件字节 SHA。对仍被原 manifest 读取的历史文件，记录其序列化格式并验证重建字节 SHA；无法确定复现的少数文件保留其唯一压缩字节，直至迁移引用到明确的新版本接口。不能把“解析后对象相同”冒充旧签名文件仍可恢复。

### 7.3 迁移步骤与验证

1. 按 ticker 的当前正式版本和热引用建立对象基线；新建局部版本目录索引。
2. 分页按旧实际 lineage/version 转换历史，只写发生变化的政策及关系。旧 root/run 缺数据时逐项标记，不生成假历史。
3. 用新读取接口恢复旧 PolicySet，对确定 JSON 内容与摘要比较；current projection、`get_version`、`runtime_projection`、D3 NOOP、手工 adoption、W3 Frozen Policy、Case 详情和方向消费全部对照。
4. 确认一页迁移与读取验证完成后退这一页重复大列、Workspace 双份和旧 projection 引用。旧库页的物理收益在 P1 包记录，不能与 gzip/硬链接收益重复计算。

同样策略集重复交付的新增字节应接近小版本头和关系收据。目标是在同一维护 cohort 下 D3 历史写入量下降至少 80%；23.94 GiB 存量中可回收比例由各页迁移后实际分配核算。

## 8. R1，Read 投影与 Runtime 持久化精简

### 8.1 Read 按消费者保留索引

Read 已是 V4 current/history，definitions 共享已存在，`changes.before/after` 当前为 NULL，旧 Poll current 现场为零。继续针对这些已有效措施反复重建没有主要收益。

| 结构 | 消费者与目标形态 | 实施要求 |
| --- | --- | --- |
| `policy` 多壳摘要 | D2 shell 下的策略列表 | 一份摘要 + 轻量 `(policy_revision,shell_id)` 关系。取消每 shell 一份完整摘要；原分页、排序、筛选改 JOIN/批读，维持结果形状 |
| `policy_detail` | 当前策略详情、Case 证据 | 同一策略定义一份，当前详情引用它。历史版本按版本恢复；活跃 Case 必需证据提前准备，小引用常驻 |
| `policy_catalog` / `policy_admission` | 当前/退役策略列表、首次激活时间、KPI | catalog 一份必要摘要和状态；admission 作为小型不可替代事实保留。当前 SQL 所需字段适度内联，取消重复大 summary |
| `definitions` / `definition_ref` | 精确对象内容与引用 | 不再为同一版本在多种全 payload 投影里重复写 ref。用实际拥有者集合差量更新，旧引用退出后保留队列负责物理删除 |
| `contributions_current` | Policy 命中、消息、使用量等 KPI | 只留持续修订/去重必需的业务贡献。已终态历史 fold 到原 metric buckets 并保存结算水位；真实迟到更正用小修正凭证，不能损失可更正语义 |
| `metric_buckets_current` | 日期与维度指标 | 小型有业务价值的聚合可长留；过细无消费者维度停止生产。不再为每次 heartbeat 写同值 bucket |
| `watermark/checkpoints/source_health` | 源追平和活性观测 | checkpoint 仅前进时写，checked_at 按既有分钟级合并；保持 source event time、巡检时间、head/checkpoint 和错误各自真实。禁止把旧等值水位解释为在线 |
| MVCC、views/cursors、changes | 一致分页与 REMOVE | 30min 协议和实际 token 生命周期继续有界。瘦身前像保留必要删除/更新信息，GC 分阶段提交和覆盖索引仍保留 |

API 端点核对包括 `api_v2/libraries.py`、`policy_queries.py`、`case_detail.py`、`case_evidence.py`、`v2_read/formal.py`、`lifecycle.py`、`policy_hits.py` 与前端 D2/Policy 页面。历史列表留下小目录可搜索，打开历史大详情可走 deferred 恢复。Case 常用结论与交易证据仍直接返回；低频完整历史上下文可以等待。

索引按实际 SQL 查找与 `EXPLAIN QUERY PLAN` 决策。保留 identity、current_page/parent、Case 定向的 partial index 和有效 GC 到龄索引。取消多壳 payload 后同步去掉仅服务旧布局的索引。不能仅因“索引多”删除索引而使 W3/Case 页面转成全表扫描。

存量 Read 采用现有 serving generation 构建/切换机制生成精简代际，增量跟至明确源水位，再同步 token/切换 alias。保留无法修复的历史 gaps 和 public API 所需 generation/seq 语义；按现有协议决定 token 能否续用，不能静默错页。旧代际只留一个经验证可回滚点，满足切换验收后退役。

### 8.2 Runtime 只写真正的恢复屏障和业务状态

先保留 11-21 已完成的 `register_stream_admission()`、inbox 与 Task 合并提交、持锁外准备、同线程短读连接、当前 owner 结果消费与可靠 ACK。不要为新治理重新拆成多笔“先准备、再登记、再更新水位”。

逐 namespace 执行以下改变。

- `reference_baselines/reference_deliveries/reference_day_chain` 改存版本/digest、变化 ID 和必要最后基线。完整 rendered/events 在发布计算中生成一次，结算不再成为永久快照。
- `worker_requests/worker_receipts/worker_invocations` 在下游 ACK 后只留 invocation、实际 job/模型/用量、最终结果引用和错误。重试前的请求全文、工具流与阶段 progress 不和业务证据共寿命。
- `round_inputs` 与 Case frozen inputs 的相同内容只保留一个权威身份；已有 journal round key 机制继续使用。源码内直接消费者逐个改为按明确 key 读取，不把“内存仍需完整对象”误解为“每张表再持久化一次”。
- `stream_admission_plans` 在确认对应 Task 持久注册、Bus 连续前缀 ACK、幂等身份仍可恢复后退掉重复 source，保留小 registration receipt。Task/Case 状态与交易租约仍需真实落盘。
- `policy_direction_usage`、授权/消费迁移、订单/成交/持仓、有效 watch、message pin 与真正唯一的业务事实长期保留。它们的存在不保护同 run 的所有工具对话和输入目录。
- Producer CDC 按实际 Read 与其他消费者选择字段，进一步删除无消费者的纯恢复/技术 namespace 捕获。变更时必须同时核对 `outbox.migrate()` triggers、`serving_fields`、source snapshot 重建和 ACK/pin，不仅在 reader 跳过大包。维持已过滤的 lease/updated_at 心跳。

Runtime 的 37 GiB 主文件、Native 和 Event 分支分别度量。内容池按 hash 去重已存在，下一步是减少反复编码的整个容器对象和持有它的多余 owner。移除 owner 时精准释放其 refs，`DELETING` guard、在用 reader 与快照租约保持正确。

## 9. T1/A1/P1，传输、采集证据和物理页退出

### 9.1 T1，过期算法与上线迁移

保留源 epoch、真实消费者最小 ACK、精确 exception pin、恢复快照租约和 `SOURCE_SNAPSHOT_REQUIRED`。Research/Bus 热重放 48h，Runtime 首发沿现有 7d 规则先修正确性；Runtime 按当前态完整恢复验过后改为 48h，exception pin 独立保留其准确 packet。该缩窗是明确产品选择，不能混称已有行为。

修改 `source_lifecycle.expire()`。

1. 优先按时间索引取 `recorded_at<cutoff AND seq<=ACK` 的有限页。所有生产源正式执行 `migrate_expiry_index()`，核对 SQL 定义和实际计划；发布回执记录每个库的结果。常驻启动只验 schema，不在实时启动中建大索引。
2. 回退路径维护轮转扫描 cursor。无候选时复位到起点，年轻记录只前进本轮位置，不增加 `expired_floor`；扫到 ACK 上界后开启下一轮。单调 ACK 的情况下也必须定期重访曾年轻的前缀。timestamp 无序、indexed 查询预算中断、`hot_only` 和 archive pack 路径使用同样的到龄规则。
3. “扫描位置”和“实际退出/replay floor”分别更新，只在对应小批退出提交后推进已删除边界。有例外的 packet 由 exception receipt 保存，不能虚报整个历史完整，也不能为一个缺口永久保护普通前缀。
4. 删除 receipt 后 Native refs 由现有 trigger/owner 更新进入可续退出，文件 I/O 留在 writer 外。归档包按实际最年轻记录时点判到龄，孤立包也纳入局部队列。

回归必须覆盖“先扫到年轻行→推进到尾→时间经过→仍能退出”、长期 ACK 不变、无序时间、多个 consumer/pin、索引查询中断、缺索引和压力模式。以固定冻结数据库加时钟推进复现 66,345/270,200 这类遗漏，禁止只测已过龄前缀。

### 9.2 A1，采集成功记录从源头汇总

`AcquisitionStore._physical_proof()` 当前已过滤部分成功字段；`capture_page()` 和 finish 仍重复更新 physical JSON，逻辑周期按 binding 保存。`observability.py` 仍读 48h 明细，因此仅提前删除会破坏诊断与 coverage。

选定以下首发规则，正常采集频率、网页实际请求与原业务消息接收不变。

| 数据 | 新落盘与退出规则 |
| --- | --- |
| 在途/未接受 capture | 保留完整必要 payload、原目标与幂等键，直到 Bus 真正接受；不得以 summary 代替待投递数据 |
| 同一 physical run | 只保存一份薄 request proof，logical bindings 引用它。相同结果跳过 UPDATE；capture/finish 只更新变化的终态字段 |
| 成功且已接受 | 薄逐次成功证据保留 2h，小时归档 seal 后退出。前端 48h coverage/history 使用小时时段统计，最近两小时用 thin detail。明确不再支持超过 2h 的所有成功请求逐条诊断 |
| 异常与中断 | 错误代码/transport/status/时间/必要小片段保留 72h。按 source+unit+错误类每小时最多 3 份完整异常样本，其余计数；未解决事故可明确 pin |
| 统计 | unit_hours 保留当前 7d 明细，旧小时滚入日汇总保留 90d。expected/fresh/late/missing 的真实计算与 schedule 变更按已有分页摘要维护，业务按需扩展统计窗口 |
| 外部请求细节 | 重复 header/body、工具导航、无用途的完整 timing tree 默认不写；真实成功/失败、VALIDATED_TRANSPORT_LIST_PROGRESS 与时点证据必须能追溯 |

先改观测 API/小时聚合，验整小时、新旧混合窗口和 late recovered 的结果，再缩明细留存。聚合采用现有可续 staging 和每页短事务，禁止为了 seal 一小时再全取当小时所有 JSON 或以近似统计替代已定义 coverage。SCHEDULE 的调度恢复、上一代真实 CAPTURED 与未接收数据独立处理。

### 9.3 P1，物理空间回收

优先让短寿命数据占用达到稳定上限，再归还存量空页。选用 SQLite 现有 `auto_vacuum=INCREMENTAL` 配合受控 `incremental_vacuum`，不为本轮迁移到新数据库产品或按天拆几十个库。

- 新建 Acquisition 及新 Read 代际在建表前设 INCREMENTAL，WAL 与耐久性设置沿用业务要求。
- 旧 `auto_vacuum=0` 不能靠改一个 PRAGMA 原地获得回收能力。Acquisition 进行一次必要转换；Runtime/Research/Bus 在大列退出后，与已有 compact/shadow 迁移合并，仅转换一次。具体构建新目标时先设 INCREMENTAL，再建 schema 并流式复制原数据，复制完成后安装捕获/引用 trigger，避免凭空生成一轮 CDC。保留原主键、生成列规则、sqlite_sequence、epoch/head/consumer 等；使用原 snapshot/shadow 取一致数据，切换前验证。未经实测不能把现有 `VACUUM INTO` 命令直接当作该模式转换。目标库切换前确认仍为 INCREMENTAL、WAL，避免上次缩库回到 DELETE 的问题。
- 回收动作由各组件现有后台执行。首发每次请求不超过 256 页、busy_timeout 约 50ms，轮次最多约 200ms；一次 PRAGMA 未必严格耗时恒定，须实测锁时，必要时减少页数或到独立连接中断。先命中“至少 256 MiB freelist 且 free ratio>10%”，再小批归还。低于阈值允许给后续写入复用，不为几十 MB 扫库。
- Acquisition 每分钟、其他大库每五分钟检查便宜 PRAGMA，最大一个维护动作，持续忙时仍尝试有限页并可退避。忙锁立即退出，不使用 30 秒等待压住 writer；不得无限等所有 W3/Jobs 空闲。Native unlink、hash 校验和 gzip 均不在该 writer 中。
- SQLite checkpoint 首选 PASSIVE，持续有长读时报告 reader/时长；不要高频 TRUNCATE，不能删除 `-wal/-shm` 文件腾空间。checkpoint 与 freelist 回收分别度量，INCREMENTAL 不承诺回收页内碎片。
- 旧大库转换/索引施工需要候选实际占用、临时排序、WAL 与正常业务写入余量。按现有 release owner 固定版本和相关写者操作，短停仅在确有依赖时使用。禁止一边生产写入一边复制旧库后直接替换，也不能在余量不足时先创建大 shadow。优先由 M0 止增、M1/E1 退无价值副本取得施工余量。

若受控回收本身造成锁时/I/O 明显回退，先减少批量并修长读。只有独立压测证明 pointer-map 成本不合适的库才考虑专门设计分区，不能把未来分区工程设为其他修复的前置条件。

## 10. S1/O1，SDK 与其他过程资源

### 10.1 SDK 日志与会话

SDK 日志、可恢复会话和最终业务证据是三个用途。已采到的 SDK 源码 `log_db::default_filter()` 默认 TRACE，app-server 的 DB layer 使用自己的 filter；只设置 Python WARNING 或 `RUST_LOG=warn` 不足以证明 SQLite 日志减少。该源码为历史取证，实施时按 `deploy/codex-patches/prepare_build.py` 锁定的实际 upstream SHA 重新核验。

1. 优先在已有 SDK 本地补丁入口调整 DB log sink。DB 默认为 WARN，加允许列的 turn 开始/结束、连接错误、usage、重试等小字段；关闭 request/response 全文、工具正文和每个流 chunk 的诊断。保持真实 model timer 与 Usage，异常需要完整追踪时只为确切 thread 开诊断窗口，结束后恢复。
2. 普通诊断默认 7d，DB 物理预算 512 MiB，超限先退最旧低等级记录。真实故障摘要可留 30d；完整故障样本以单次事故短窗保留。日志退出走 SDK 自身批写/维护事务，不能对活跃 SDK 私库直接按猜测 SQL `DELETE`。
3. 模型运行中、Trade→Operations 同线程延续、未知结果和有效 repair hold 的恢复流水保留。已成功且被业务消费的会话，最终响应/usage/身份已在 Worker 和业务源落盘后，按前述 24h 过程窗口退出。失败流程 72h，必要 checkpoint 单独 hold。
4. 替换 `TerminalSessions.owned()/listing()/groups()` 对全库 Job 和全部 SDK 会话的扫描。创建/子线程通知时记录局部 parent/fork/session 关系，终态入队后只读相关 group，复用已有 `thread/read`、`thread/delete`、DELETING fence。已知 W3 分支继续由 `w3_thread_registry` owner 处理，不能用非 W3 退出器猜它的完成状态。
5. 移除六小时启动等待和全局 `quiet(jobs)` 前置。短会话删除任务使用现有低优先级管理通道，一次一族；新模型请求优先，删除已终态 thread 不触碰活跃共享 parent。SDK thread/delete 是否也退出 history projection 要用实际版本验证；若没有，补 SDK owner 内按已删除 thread 退出 `thread_items` 等，禁止外部手删 DB 文件。

### 10.2 其余 owner

| 组件 | 具体退出责任 |
| --- | --- |
| W3 context/source/assets | `w3-assets`、`.w3-contexts`、packs 是可再生资料与必要冻结输入的混合。以现有 thread/Case 双 ACK、watch、pin 与 repair 边界为准退派生内容；当前准备链和 READY 补货不增加扫描。新方案不把这项小增量当第一收益 |
| Initialization / `.init` | 已有不可变共享与 owner。新快照只保恢复必需文件清单和变更，终态 owner 退出；旧 `.init` 约 0.09 GiB，优先补新增责任，不专项追小存量 |
| Observation / CDECR | 保留 canonical blocks、结果/引用和必要调用回执；临时解包、渲染、长工具流结束即退出，唯一采纳内容留下单份 |
| Research 未采纳候选 | 成功发布且被采纳与未采纳分开。无 live job/repair 且长期未采纳的候选，先留小目录和结果摘要，再退出可再生准备体；独有研究成果继续保留压缩内容。首次实现以 30d 未再访问为候选，不自动删除已引用研究 |
| Browser | profile/Cookies/Local Storage/凭据属于身份；缓存、BrowserMetrics 和旧诊断由实际 profile owner 在闲置实例轮换/释放时收尾。恢复快照保留当前有效身份的上一份已验证恢复点，成功接替后退出更旧等价包，不全量复制整个 profile 作为每次保险 |
| Production Validation / 测试 / Ops | 建立该运行自己的 owner/end receipt，结束后 24h 退全量副本，保留小验收结果。用户采纳的唯一测试产物仍按真实引用保存。取消重跑每次默认生成整库取证 |
| Docker / 发布 / 备份 | 继续用 `storage_release.py` 稳定依赖、成功后退临时镜像/缓存。全量恢复点只保当前和前一套已验证集合；内容池按变化备份，不能每次发布再复制整个数据盘。Git/镜像回滚与数据格式读兼容一起核对 |

对每类资源检查“创建有入口、结算有 ACK、退出有调度、失败有下一次重试”。同一个 hold 不能因 parent/同 session 的历史关系无限扩散；实际共享且仍在用的线程和文件按准确引用保留。

## 11. 开发、迁移与发布批次

### 11.1 执行顺序

| 批次 | 交付 | 前置与可独立完成条件 |
| --- | --- | --- |
| B0 | M0 控制开关、精确暂停、状态与 Selection 依赖处理 | 固定最新 main，核对实时版本与 owner；先交付止增。暂停与恢复测试通过即可独立发布 |
| B1 | T1 cursor/index、M1 局部退出与导航输入减量 | M0 关闭期间开发和清积压。旧大扫描不接回，Runtime/W3 热路径验收 |
| B2 | E1 延后 apply、唯一当前与稀疏历史、Fact admission 元数据 | B1 提供退出通道；先转换 live lineage。新事件与新 D3 候选采纳协议共同验证 |
| B3 | D1 策略对象历史、Read/Runtime 载荷与查询重构 | 统一版本接口后改上下游，不在中途长期保留新旧全量双写 |
| B4 | A1 观测/聚合先改、明细缩窗；P1 一次转换与物理回收 | 小库/冻结现场压测通过，有足够候选余量才迁大库。T1 可先独立上线 |
| B5 | S1/O1、指标防回退、有限恢复试跑 | 不阻塞主要增长源治理。M0 仅在维护减量与调度保护验收后重新打开 |

正式业务读取兼容先上，单一新写者随后启用，再分批转换旧数据；不长期维持两套完整写入。不要求实现全局资源服务或统一跨库事务。所有重要产品修改追加 changelog，保留此方案 ID、行为改变与测试结果。

### 11.2 存量转换与清退属于同一交付的收尾

实施前刷新各根 allocated、SQLite freelist、活跃 owner 与实际镜像。对照 11-23 数据生成可执行候选，不硬套旧清理列表。正式清退顺序为终态过程目录 → 无独有内容的 Event 库 → 已转换的 D3 双份/旧投影 → 已 ACK 运输与无引用 Native → 旧 Read 代际 → 必要旧库缩容 → SDK/临时产物。

每页或一组实际目录取得内容/引用验证后即可清退，避免先制造一整套 300 GiB 备份。普通缓存、确认相同的副本不额外备份；真正不可替代的历史变化有压缩恢复材料和可验证 locator。当前正式组合、热 Case/交易/watch 资料在切换前已齐备。恢复包和迁移临时库各留明确 owner，验收后退役，避免新治理再制造冗余。

旧 Event 分支 basename/整数版号不作为唯一标识，D3 的 Policy/condition/activation ID 不重编。Read 的历史 gap、未知 provenance 和真实 source 时点原样保留。采集 PENDING、SDK unknown turn 和 incomplete import 逐 owner 处理，不能为达标伪造 ACK。

### 11.3 回滚与中断

- 每个新格式先给正式和选定回滚镜像装兼容读能力。新写格式启用后回滚到该兼容版本；不能指望旧镜像读取已被删掉的 full payload。
- M0 可随时再次关闭，已完成业务结果仍保存，恢复只释放该开关挂起的任务。
- 归档/迁移在记录持久完成和校验前不移除唯一源。原子发布文件后发生 DB 提交失败，留下的无引用候选由本地 owner 重试/退出；不是恢复全局一致性的借口。
- 格式升级、索引安装、大库切换和轻量日常退出使用现有各自的锁，文件工作不持有 Runtime/Worker writer。一个 owner 失败不得阻止其他 source 和目录继续退出。

## 12. 收益预算、观测与长期验收

### 12.1 预期收益的正确口径

| 改动 | 持续减量目标 | 一次性空间 |
| --- | --- | --- |
| 暂停 Maintenance | 关闭期间自动维护新任务/模型调用/候选写入归零；真实业务 pending 标记小幅增长 | 自身不回收既有 100 GiB 工作区 |
| M1 恢复后 | 同等输入/完成量的 bytes/run 至少降 80%，终态过程数据在窗口后平台化 | 既有父子和已结算过程树逐组实测，不能承诺 100 GiB 全可删 |
| E1 | 无变化 run 零候选库，整体候选物理新增目标降 90% | 可退旧分支按恢复验证页计；不可与 Runtime 根差额相加 |
| D1 | 同 cohort 历史策略写入目标降 80%，无重复全策略与历史 projection | 两大表 23.94 GiB 是治理范围，非保证回收量 |
| Read/Runtime | 去多壳 payload、重复容器和无消费者 CDC；新物理增量、写字节及引用数量明显下降 | 精简代际和大列退出后才核算实际页/文件收益 |
| T1/A1/P1 | 过龄已 ACK 正文及时退出，短寿命明细占用收敛，freelist 持续归还 | 11-23 的约 11 GiB Acquisition 差已实现，不能再次计入未来收益 |

整体首轮验收目标为修复全部启用后，**连续五个正常交易日**，可再生/技术资源滚动五日净增长不大于 1 GiB，整盘平均净增长相对最近可用业务量基线至少下降 80%，暂设日均不高于 2 GiB 作为容量告警线。2 GiB/日只表示阶段性显著改善，有限根盘仍不可持续无限增长；必须把剩余增量归到真实独有业务，并计算容量跑道。小型事实可长期留，大正文按活跃引用/访问价值决定在线与历史压缩位置，需求变化时调整，不能用这条数字掩盖剩余无价值增长。

暂停期间通过五日指标不算 Maintenance 修复完成。最终五日窗口必须包含已恢复的正常 Maintenance；真实维护完成量、有效变更量与过去负载对齐。另连续观察超过最长自动保留窗口，确保过期后占用平台化，不能只挑低负载周末。

### 12.2 轻量指标与资源预算

复用 release capacity 与各 owner receipt，小时汇总，保留 30d；按任务事件直接累计小计数，五分钟输出一份最新状态。根盘 `df` 和 SQLite `page_count/freelist` 单独读。大型树扫描按既有目录/创建清单逐页 stat，跨轮续跑；硬链接按 inode 计一次。全树巡检只作低频校准，不放启动、每轮 tick 或定时高频 `du`。

每个 owner 记录 produced/retired bytes、pending eligible 数与 oldest age、最后成功退出、忙锁/错误、重试时点。SQL 页数与文件 allocated 分开，压缩逻辑 bytes、硬链接路径 bytes、真实物理块不相加。历史不完整样本标出实际时点，超时不能沿用旧值再标成当前。

轻量退出首发每批 SQL writer 持锁目标 P95≤50ms，文件压缩/扫描/恢复全部锁外；超预算就减少批量，事件吞吐通过源头减量保持可追。队列按 `retry_at` 公平轮转，坏对象不挡后面的正常退出。永久繁忙压测中每个到期 owner 都必须有已提交进度；有任务在运行不等于全局冻结治理。

现有 `background_allowed()` 当前始终返回 True，不能作为实际隔离证据。Runtime `resource_gc` 仍会在实时 W3 活跃时让路，首发只取消已经确认终态小对象的全局让路，大型历史恢复单独串行、低优先级限速。避免为了追清退吞吐突然放开高并发 VACUUM 或压缩。

### 12.3 必要验证矩阵

| 场景 | 通过条件 |
| --- | --- |
| 暂停/重启/晚到回执 | 无新维护任务、O3 模型调用和正式采纳；已运行准确退出，Selection 和 Case/W3 无等待链扩散 |
| 补偿合并 | 同一 ticker 在途维护不超过 1；迟到结果和 effect 新代次仍被下一批消费，失败不制造无限新任务 |
| Event/D3 无变化 | 无新内容块和完整版本；真正变更只影响对应对象，业务 version/activation/消费代次正确 |
| 历史恢复 | 覆盖旧 lineage 分叉、曾退役恢复、无变化链、Fact 首次获知、membership/redirect、旧 D3 schema 与导出，内容和时间对照一致 |
| API 与 Read | 当前列表/分页/KPI/Case Detail 正确，短 token 与 REMOVE 语义正确；冷历史明确 deferred，恢复后结果一致 |
| CDC/Native | 缺索引、年轻行到龄、ACK 停止/回退、pin、snapshot 租约、原生文件删除中断全部可续；源坐标/gap 不伪造 |
| Acquisition | 采集频率、接收幂等、真实 PENDING、coverage、late recovery、跨小时与重启调度正确；两小时后成功明细退出且统计可查 |
| SQLite | 自动回收长期有实际 df 收益，WAL 正常、无频繁大锁、无反复停采；原始业务摘要与恢复语义一致 |
| SDK | 日志量下降、真实计时/Usage/错误保留；活跃 turn/watch 同线程不中断；已删 thread 的多个本地投影均兑现退出 |
| Runtime 延时 | 在可比冻结负载与自然突发下，W3 启动及 Runtime 非模型 P95 不劣化超过 5% 或 200ms（二者取宽），新增维护 writer 等待纳入 trace；未达则定位新持锁/物化步骤 |

每个工作包配对本地回归与必要的冻结生产规模回放，随后按正常业务入口验收。完成短测试后即可进入下一包；最终以自然交易日签收。实施回执要给逐包代码 SHA、实际角色镜像、开关、迁移进度、物理收益、业务与性能验收、未完成项。M0 再次开启和五日结束是两个明确的验收节点。

## 13. 本轮设计选择与实施前补证

本方案已按“当前高效，历史可慢”选择 Event/D3 的稀疏变化历史、短期过程数据、合并 O3 更新和 deferred 历史详情。默认参数包括 O3 迟到一小时合并、成功过程 24h、失败 72h、采集成功薄证据 2h、SDK 日志 512 MiB/7d。它们是首发设计值，实施时在配置与变更回执中明确，并用真实业务验证；不是从现有产品合同推导出的硬约束。

实施前仍需补取四项小范围证据，不妨碍先开发暂停和确定性缺陷修复。

1. 当前 Maintenance 按 scope/phase 的队列数、最大年龄、claim 与模型等待、父任务阻塞与恢复率，核清实际 backlog 对应的输入是否已被继任任务覆盖。
2. Event/Policy 的真实变化对象比例及 Reference/W1 热引用集合，精确到 lineage。历史异常不得被内容去重隐藏。
3. Read current/索引和 Runtime namespace 的物理页分摊，以及 SDK 实际构建的日志 filter/history 退出能力。重查询应在冻结样本上做，避免在线扫描多 GiB 再影响业务。
4. 上线时真实根盘余量和大库 pointer-map/索引转换成本。11-23 19:13 的 14.77 GB 可用是历史采样，不代表未来施工容量。

所有设计以少写独有变化、在既有业务 owner 内结算和有界执行为落点。需要恢复的历史保存足够的真实变化和身份，不再以保存每次运行的完整物理副本承担恢复责任。

## 附录 A. 实施文件与最小回归入口

以下为已核查的主要落点。新增测试只覆盖本方案引入的确定性缺陷与恢复边界，复用已有测试集和 fixture。

| 工作包 | 主要文件 | 首要回归入口 |
| --- | --- | --- |
| M0 | [Runtime 调度](../src/doxagent/persistent_runtime_v2/coordinator.py)、[阶段执行](../src/doxagent/persistent_runtime_v2/maintenance.py)、[失败恢复](../src/doxagent/persistent_runtime_v2/maintenance_recovery.py)、[正式采纳](../src/doxagent/ticker_initialization/repository.py)、[Worker 派发](../src/doxagent/codex_worker/jobs.py) | 新增暂停竞态/Selection 不阻塞用例，覆盖真实 claim、cancel、采纳事务 |
| M1 | [物化与快照](../src/doxagent/workflows/codex_document3/runner_v21.py)、[导航](../src/doxagent/workflows/codex_document3/materials_v21.py)、[原导航文件生成](../src/doxagent/codex_runtime/context_index.py)、[Workspace](../src/doxagent/codex_worker/workspace_store.py)、[退出](../src/doxagent/codex_worker/workspace_retention.py)、[后台入口](../src/doxagent/codex_worker/resource_maintenance.py) | [D3 Maintenance](../tests/test_codex_document3_v21_maintenance.py)、[Runtime 退出](../tests/test_runtime_resource_exit.py)；增加 inode、导航完整性和中断收尾 |
| E1 | [Event Repository](../src/doxagent/event_library/repository.py)、[旧分支入口](../src/doxagent/persistent_runtime_v2/event_branch.py)、[W1 日基线](../src/doxagent/persistent_runtime_v2/daily_index.py)、[Reference 交接](../src/doxagent/persistent_runtime_v2/reference_capture.py) | [增量消费](../tests/test_event_library_incremental_consumers.py)、[时间语义](../tests/test_event_library_time_reference_repair.py)、[视图合同](../tests/test_event_library_view_contracts.py) |
| D1 | [staged 状态](../src/doxagent/workflows/codex_document3/state_v21.py)、[正式存储](../src/doxagent/workflows/codex_document3/repository.py)、[候选交接](../src/doxagent/workflows/codex_document3/publication_v21.py)、[维护编排](../src/doxagent/workflows/codex_document3/orchestrator_v21.py) | [发布合同](../tests/test_document3_v21_publication.py)、[维护](../tests/test_codex_document3_v21_maintenance.py)，增加稀疏历史恢复与消费代次 |
| R1 | [formal](../src/doxagent/v2_read/formal.py)、[lifecycle](../src/doxagent/v2_read/lifecycle.py)、[Read Store](../src/doxagent/v2_read/repository.py)、[Case Evidence](../src/doxagent/api_v2/case_evidence.py)、[Policy SQL](../src/doxagent/api_v2/policy_queries.py)、[DeferredQueries](../src/doxagent/api_v2/background_queries.py)、[入站事务](../src/doxagent/persistent_runtime_v2/journal.py) | [Policy 查询](../tests/v2_backend/test_policy_queries.py)，加旧 token、历史恢复和热 Case 大样本比较 |
| T1 | [source_lifecycle](../src/doxagent/v2_read/source_lifecycle.py)、[transport 主循环](../src/doxagent/v2_read/transport_archive.py)、[producer 捕获](../src/doxagent/v2_read/outbox.py)、[Native 退出](../src/doxagent/v2_read/source_native_gc.py) | [传输生命周期](../tests/v2_backend/test_source_lifecycle.py)，增加年轻前缀重访、unordered time、pin 和持续忙场景 |
| A1/P1 | [Acquisition Store](../src/doxagent/message_bus_v2/collection_plane/store.py)、[小时归档](../src/doxagent/message_bus_v2/collection_plane/maintenance.py)、[实际观测消费者](../src/doxagent/message_bus_v2/collection_plane/observability.py)、[Read 新代际](../src/doxagent/v2_read/serving_generation.py) | 固定时钟覆盖 2h/48h 跨界、late recovery、INCREMENTAL 模式切换和真实 allocated 趋势 |
| S1/O1 | [会话退出](../src/doxagent/codex_worker/session_retention.py)、[W3 线程 owner](../src/doxagent/codex_worker/thread_lifecycle.py)、[SDK 固定构建](../deploy/codex-patches/prepare_build.py)、[发布/容量 owner](../scripts/ops/storage_release.py) | [会话退出](../tests/test_terminal_session_retention.py)、[生命周期](../tests/test_storage_lifecycle_20261111.py)、[发布](../tests/test_storage_release.py)，增加永久有实时 Job 的退出进度验证 |
