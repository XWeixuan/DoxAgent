# DoxAgent 全局磁盘占用与数据生命周期修复方案（定向修订版）

日期为 2026-11-23。本文件供后续开发、迁移、发布和持续验收使用，完整承接[原方案](global-disk-lifecycle-repair-plan-20261123.md)。本版仅调整 O3 独立跳过、历史逆向恢复、派生产物留存和治理性能约束，保留其他合理工作包。本轮交付为完整设计文档。

## 1. 总体决策与审查基线

采用五项相互配合的调整。

1. 先关闭 O3 阶段的创建和消费，O2 沿原调度执行，并将新的 Event 与原正式 D3 组成一次明确的 activation。O2/O3 分开确认消费，暂停期间每 ticker 只留下小型 O3 待处理状态，重新开启合并处理。保持原执行器、Worker lane 和既有每 ticker 租约并发，不新增全局单并发或小时批次策略。
2. Event 和 D3 当前完整版本正常服务，复用既有 revision、base version 与内容去重，保存实际变化的必要旧值，支持从当前版逆向恢复历史。历史 SQLite 分支、全量 PolicySet/projection 和展示文件不按运行次数累积。
3. O3 输入在家族内冻结一次。导航读取原文件或 JSON 路径，取消把原文件拆成多层副本的默认行为。发布后由终态 owner 登记局部退出，后台只执行已确定的删除工作。
4. 修复传输过期游标和生产迁移缺口，缩短高频成功诊断的落盘生命周期。SQLite 先利用空页复用，按库验证物理回收净收益；INCREMENTAL 仅在证明收益的库启用，取消所有大库默认转换和定期主动缩库。
5. 正常业务上线、历史迁移和连续交易日容量验收分开交付。验收采用实际物理占用和写入成本，不能以清理行数或一时余量代替。

### 1.1 本轮实际核查范围

- 本地 HEAD 为 `434711945`，对应 11-21 Runtime/Bus ingress 发布回执；本地已知 `origin/main` 为 `2843e8403`。两者相差四个提交，新增内容为 MCP 延后读取及其生产恢复回执，受审存储模块无产品代码差异。本轮读取这些提交内容，未切分支、合并或覆盖文件。
- 工作区另有 W3 离线回放、评估和 changelog 修改。已查看 `w3.py` 差异，它涉及回放 Skill 装配。后续实施必须在当时最新 main 上适配，禁止把这些工作顺带回滚。
- 深读 `persistent_runtime_v2/coordinator.py`、`maintenance.py`、`journal.py`、Event Repository、D3 runner/publication/repository、Read formal/lifecycle/schema/GC、Acquisition store/maintenance、Transport/Native GC、Worker 资源与会话退出、SDK 本地构建入口。
- 本版补查 `_consume()`、`mark_daily_records_processed()`、`admit_revision()/apply_release_authorizations()`、`ActivatedRuntimeInputs.from_revision()`、Native ref reconcile/inventory、Read GC 和容量轮转。发现 O2 后直接跳 O3 仍调用旧 `_consume()` 会误确认 O3 输入，同 D3 随 Event 重激活还可能重放旧 rearm，必须纳入 M0。部署中的实际压力与延时因果仍按正式实施窗口验收。
- 生产容量、队列和表数量引用 11-23 已落盘调查与此前执行记录。本轮没有重新探测生产、调整开关、清理、迁移或发布。所列当前生产占用均指该调查窗口；O3 的实时队列数量、等待时间和阻塞占比仍需实施前补取。

历史与现场依据见 [11-23 问题发现](root-disk-regrowth-findings-20261123.md)、[11-18 源头减量方案](doxagent-global-storage-source-reduction-plan-20261118.md)、[开发交付与剩余项](storage-source-reduction-development-20261118.md)、[11-11 生命周期方案](doxagent-storage-lifecycle-optimization-plan-20261111.md)、[11-14 清理和生成机制](disk-cleanup-and-growth-mechanisms-20261114.md)、[11-21 ingress 方案及实现基线](../dev_plan/workflow_v2/runtime_bus_ingress_latency_root_cause_and_lean_repair_plan_20261121.md)。

### 1.2 按问题量级排序

| 工作包 | 已证实的规模或增长 | 优先级与决策 |
| --- | --- | --- |
| M0/M1，O3 阶段和工作区 | 工作区约 100.15 GiB；校正上次清退后基线约增长 30.95 GiB。新 Maintenance 出生队列在两个交易日分别留盘 9.50、11.65 GiB | P0。O3 独立跳过，O2 继续；收敛 O3 义务与重复物化 |
| E1，Event 分支 | 新分支约 7.91 GiB，两个高负载日约 2.5 GiB/日；173 个样本中 159 个未发布更高版本 | P0。取消逐 run 克隆；实质变化写增量 |
| T1，传输退出 | Research 无 pin 的过龄已 ACK 记录 66,345 条；Runtime 按自身 7d 规则有 270,200 条落在游标前 | P0。修回退重访和正式索引迁移，释放 Native 的无价值引用 |
| D1，D3 版本 | 正式和 staged 两表约 23.94 GiB，约占 Research 主库八成；主库约增 4.09 GiB | P1。单份当前内容、对象差异历史，取消历史 projection |
| R1，Read/Runtime | Read 目录约增 4.27 GiB；policy 四类 current 合计超过 360 万行；Runtime Native 约增 2.04 GiB | P1。改消费者契约和生产端载荷，保留热路径必需索引 |
| A1/P1，Acquisition 与物理回收 | 11-23 缩库物理差约 11 GiB；逻辑删除已有效，空页仍占根盘 | P1。先降低成功明细生产，再按库评估复用与物理回收成本 |
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
| Event/Fact | 当前完整对象、状态、成员、关系、Reference 所需字段；当前激活版本与 W1 日基线 | 保留真正变化对象的旧值引用和状态/关系变化，从当前版逆向恢复；保留首次获知时间 |
| D3 | 当前正式 PolicySet、当前执行 projection、轻量版本与采纳记录 | 政策对象/条件变化与集合关系变化；旧完整 projection 可复算，过程 Workspace 可退 |
| Case/交易/Policy 消费 | 决策、订单/成交、真实时间、精确输入身份、消费代次、必要引用 | 不可替代的业务事实长期保留。冗长工具输出、重复输入和运行日志按 owner 退出 |
| Read | 页面当前读模型、当前 KPI 需要的查询索引、30min 协议前像与实际有效 token | 小型历史目录和事实指标保留；旧大详情从业务历史恢复，恢复产物短暂缓存 |
| CDC/Native | 消费者尚需的记录、短期重放、精确异常 pin | 到龄且 ACK 后退出。当前态重建 Read 替代无限传输史 |
| Acquisition | 在途/待接收 payload、调度 head、薄进度证明 | 小时统计、有限异常样本；成功逐次过程证据快速合并退出 |
| SDK/浏览器/验证 | 活跃会话与浏览器身份、当前恢复状态 | 已消费会话的工具流水、日志、缓存与完成的临时库有明确退出边界 |

历史恢复材料仍会占空间，只保存独有变化及小元数据。当前存储预算优先给正常业务；历史无法恢复时明确报错并保留未知 provenance。D 盘档案可用于低频人工恢复，不能成为生产当前读、W1 或 W3 的依赖。

## 4. M0，O3 独立暂停，O2 正常发布

### 4.1 最小改动与开关落点

保留现有 `MAINTENANCE` Task、日切/Sweep/补充任务、租约与执行器。在 `maintenance.py` 的 O2 完成和 O3 准备之间增加分支，关闭时跳过 O3 的构造、输入准备、模型与发布；随后用 `event_ref=O2 新结果、policy_ref=base.artifacts.document3` 调用现有 `activate_runtime_bundle()`。O2 真实无变化时不创建等价 activation，但必须先提交实际 resolution/mapping/review 等小状态；只有内容、必要关系和 visibility 均无新正式变化才复用原引用，不能因 version 数值相同就丢掉新状态。暂停期间前置 assets/prompt 物化也只准备 O2 需要的内容，不能在分支前已经生成 Document3/Atlas 文件或完整 O3 feed。

在正式采纳所在的 `initialization/control.sqlite3` 增加单行 `maintenance_control(id=1,enabled,revision,updated_at,reason)`，对外名称 `o3_maintenance_enabled`。首发显式关闭。CLI 写入后通过现有控制更新链通知正在执行的 Maintenance 与 Worker；重启读一次，Maintenance 进入阶段边界时读一次。普通 Case/W3 不新增开关 SQL，不在每秒 tick 或每个 queued Job 上轮询控制库。

O3 关闭不限制 O2 的 claim、失败恢复、Worker 调用及 Event activation。对确切 O3 node/phase 阻止新增调用，不笼统停 background lane 或复合任务。独立初始化/人工研究沿自身入口工作；明确的 Runtime O3 重跑经过同一开关。控制 revision 在正式采纳事务中复核，属于本次 Task 的 O3 结果只有在允许且 base 未改变时才能采纳；O2-only 采纳不会被 O3 开关拒绝。

### 4.2 新 Event 与旧 D3 的版本与授权语义

`ActivatedRuntimeInputs.from_revision()` 已分别读取 activation 中的 Event 与 D3 版本，现有结构允许二者独立演进。实现必须保留以下语义。

1. 从同一有效 base 取 D1、D2 和正式 D3 引用，O2 仍按冻结输入运行、校验和提交。Control 的单事务比较这四类 base 引用，切换到新 Event 与原 D3；人工发布并发改变 base 时沿既有 CAS/rebase 处理，不覆盖新正式状态。
2. 原 D3 的 `event_library_ref` 是该策略制作时的依据，不能改成新 Event 或凭空增加 PolicySet version。新 activation 明确记录 `o3_outcome=SKIPPED_BY_CONTROL`、O2 版本与开关 revision，表示策略尚未吸收新信息。新 Case 使用这个明确组合，旧 Case 的 version pin 不改写。
3. 现有 `_admit()` 会按新 activation ID 调 `admit_revision()`，后者读取该 D3 的 `accepted_rearm`。O2-only 激活必须复用这份 D3 已完成的授权结果，写入与新 activation 对应的薄关联/授权 head，**不再次执行历史 rearm**。新的 D3 发布才可能执行新的已验收授权请求。沿现有 release/run/version 身份去重，不能只用新 activation ID 判断这是一次新 D3 授权。
4. `policy_direction_usage`、consumption_id、usage_generation、方向已消费状态与 watch 均不因 Event-only 激活重置。旧 Case 的输出继续遵守现有 activation/authorization fence；新 Case 的 guard 与新 head 一致。测试旧任务跨激活的接受/拒绝路径，不能以兼容为由关闭 fence。
5. Reference 当前可见状态随 O2 的已发布内容更新，W1 日冻结基线与 Fact 首次获知时间继续保留。若需要刷新准备资产，只复用现有 `prepare_library_visibility()` 和按变化失效机制，不每个 O2 结果重编全部 ticker 的 W3 资料。

短期仍使用旧 Event branch 实现时，O2 可能继续生成 Event 候选库，这属于 E1 待消除的成本；不能因此暂停 O2，也不能声称 M0 已消除了全部 Maintenance 写入。

### 4.3 O2/O3 分开确认消费与 Reference 基线

当前 `_consume()` 会一起将候选事实、trade、badcase 和 coverage gap 标为 PROCESSED。关闭 O3 时不能沿用该无条件整批确认。复用原记录表和小型 owner 状态，按消费者拆出确认函数。

| 输入或状态 | O2-only 完成时 | O3 真正完成时 |
| --- | --- | --- |
| 候选事实的 O2 解析/发布 | 仅确认 `resolved_candidates`，保留 unresolved；以现有 mapping/resolution 防重复 O2 | 若也是 O3 的 trade_candidates 输入，独立确认已覆盖的该代候选 |
| trade、badcase、coverage gap | 保留 O3 PENDING，不能置已处理 | 仅确认本批冻结的记录 ID 与版本/效果代次 |
| Policy consumption | 保留真实消费事实和待维护边界，不生成整包 feed 快照 | 推进该次 O3 已确认的消费边界，不重置真实方向消费 |
| Reference 差异 | 更新最新 O2 版本引用，记录 O3 尚需吸收 | 推进 `last_o3_ack_event_ref` 到本次实际输入版本 |
| Runtime Task | O2 已完成可以成功结算，receipt 明确 O3 skipped，不能虚报 O3 成功 | 另记 O3 成功/NOOP 与输入覆盖证明 |

为候选表加必要的 `o3_status/o3_generation` 标量或等价的已有 owner ACK，原候选 `daily_status` 继续表示 O2 处理；trade/badcase/gap 的原 daily_status 继续由 O3 确认。新增字段只针对该消费者缺口，不给全部 Case 引入通用生命周期表。记录创建和 O3 待处理变更并入原业务事务；新字段与 SQL 谓词安装明确 partial index。`bounded_inputs`、补充调度、退出与补偿读取同一消费者定义，避免一个 O3 未消费 trade 反复触发完整 O2。

迁移旧记录时依据真实成功 O3 receipt/范围补 ACK，不能仅因旧 candidate 的 daily_status=PROCESSED 就认定 O3 已消费；该值也可能来自提前发布或去重。未确定的遗留义务合入对应 ticker 的待复核范围，一次迁移游标分批确认，禁止给所有历史 Case 重新创建任务。

`maintenance_recovery._covered()` 和补偿 receipt 也按实际阶段验证，不能因候选已被 O2 标 PROCESSED 就宣布 O3 的 trade_candidates 已覆盖。O2-only 完成收据与真正 O3 成功/NOOP 明确区分。

每 ticker 的 O3 pending 保存少量字段，包括 generation、首次待处理时点、最新 Event 引用、上次 O3 ACK 的 Event 引用及 pending 标志。真实记录仍在原表；marker 不积攒 Case ID/完整 frame/逐日副本。已有标志且内容未变就不写。没有新 O3 输入时不触发检查。

暂停阶段不调用 `reference_capture.freeze_before()/prepare()` 为 O3 写完整 before/after events+rendered。重开时从上次 O3 确认的 Reference 语义到最新有效 Event 计算一次合并输入。若现有 D3 无可用 ACK 基线，以其实际 `event_library_ref` 及版本材料初始化一次，未知时记录真实缺口。暂停期间该基线只需必要的历史变化材料；在恢复准备阶段重建，不为每个 O2 run 保留完整基线。

### 4.4 已有 queued/running、失败与恢复

- 原 PENDING/失败 Task 的 O2 继续调度。曾被前一方案标成 O3 管理暂停的 HELD Task，仅解除这一管理原因，原有业务/修复 hold 保留。O2 已完成的 Task 不因 O3 关闭反复执行 O2。
- 精确关联既有 O3 Job：queued 的取消或止派发；running 的用现有 cancel/turn interrupt 协作中断，确认实际 turn 状态后收尾。已返回成功结果先收 receipt，关闭期间不采纳它。O2 Jobs 不被该操作取消。
- 中断或迟到成功的 O3 结果只有在重新开启、其冻结 base/输入仍有效时才可复用；否则合并到最新待处理义务，退旧派生目录。未知状态保留原 dispatch 身份待精确 reconciliation，不盲目重派，也不保留整个共享 session。
- `maintenance_recovery` 分清 O2 失败和 O3 管理跳过。O3 关闭不计模型失败、不创建恢复风暴；原 O2 错误仍按既有恢复处理。Task 的 phase outcome 参与重启恢复，O2-only activation 后 crash 只补原 ACK，不把已成功阶段再运行一遍。
- Selection 仍按原规则等待真实未完成的 Sweep/O2；O3 已明确 skipped/deferred 后不再额外等待四小时。它使用当前有效 Event+D3 组合和既有策略消费规则。不能因 O3 关闭提前跳过尚未完成的 O2。

暂停完成的判据是 O3 新 Job、工作区和结果采纳为零，已有 O3 已终态或明确列出不能确认的精确 Job；O2 与新 Event activation 持续推进。此时允许保留一行待维护状态，不允许积累 O3 的待执行 Task/冻结上下文。

## 5. M1，保留现有调度并减少 O3 输入与退出成本

### 5.1 暂停义务合并与恢复

保留 DAILY、Sweep、补充与失败恢复的既有调度语义和并发。`journal.claim()` 已按 ticker 限制 MAINTENANCE 同时租约为 1；本版复用这一约束。Runtime 后台执行器与 Worker lane 不新增专属通道、不设跨 ticker 全局上限，也不引入每小时一批或新的日切策略。争用专门优化留给后续有证据的任务，本轮以源头减量和不新增争用为边界。

关闭期间所有 O3-only 触发更新本 ticker 的 pending marker；复合 Task 只运行其 O2 部分。已结算的 O2-only 家族在补充调度判断中视为 O2 终态，O3 欠账不会占住原 Task，亦不会因 effect 变化反复为同一义务建 O3 Job。

开关重新开启时，利用现有一次控制变更唤醒和原 MAINTENANCE claim 路径，按 ticker 当前 pending generation 创建一个合并 catch-up Task。以原 Task inputs 的轻量 phase 标记表示 O3-only，复用既有 receipt/lease，不另建调度器。准备时从最新有效正式组合和原业务表分页冻结本批记录；不按暂停天数、旧任务或每个 Case 重放 O3。随后回到原正常维护入口。

catch-up 只确认实际覆盖的记录与 effect/generation。运行中新到结果保留在下一代 pending，不能以“已追到当前时间”清掉未读数据。重复唤醒由 ticker+generation 的既有任务身份幂等处理。已有同 ticker 维护在运行则保持 pending，待其原完成事件接续，不能每秒查整张任务表等空位。

暂停前留下的多个 O3-only Task，分页归并成 pending 状态并留小收据，已冻结 Task 不原地篡改 cutoff/membership。`compensated_by` 只在继任结果实际覆盖后确认。O2 的未解决原子事实及原业务失败不会被归并删除。

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

O2-only 家族按 O2 的真实发布/消费及在用 Event 引用判断退出，不等待一个永远不会出现的 O3 staged 行。O3 deferred 所需记录和 Reference 历史由业务源/版本旧值承担，不继续保护整个 O2/O3 frame、prompts 和 Workspace。相反，仍被当前 activation 或热 Case 引用的 Event 内容必须先经 E1 收敛才退出原目录。

历史 backlog 只做一次可续的按 Job sequence/run 前缀分批登记；新任务直接投递 owner 事件。常驻循环启动后立即恢复 pending 队列，无六小时盲等。每批至少推进一个有限工作单元，持续活跃 W3 不影响退已确认终态的其他家族。

## 6. E1，Event Library 实质变化版本与按需历史

### 6.1 现有能力与必须改变的点

`EventLibraryRepository` 已有 Event/Fact revisions、状态和关系有效区间。`publish_bundle()` 在空 Event revision/retirement 时保持原版本；Fact payload 已比较相同内容。仍有两处放大。

1. `branch_library()` 为每个 run 复制所选版本的 canonical 内容、状态和索引。即使模型最后无变化，复制已经发生。
2. `_write_event_revisions()` 对提交的每个 Event 无条件新建 Event revision，并先关闭再重建整个成员集合和关系。候选内容相同也会触发这一组写入；Fact 的相等比较不能阻止它。

### 6.2 选择当前完整状态加稀疏旧值，复用原 revisions

保留现有 Event Repository、校验器、`library_versions.base_version`、对象 revisions、状态/成员有效区间和压缩内容去重。当前完整版本正常读，历史按实际变化的旧值逆向恢复；不额外维护一份永久初始完整基线。

| 方向 | 持久化与恢复代价 | 决策 |
| --- | --- | --- |
| 初始完整基线加前向变化 | 恢复逻辑直观，但长期重复保留一份旧全集；当前态已经在盘上 | 保留旧格式迁移读取能力，新写不采用该双基线 |
| 当前完整状态加必要旧值 | 发布需记录被替换/移除对象的旧值；历史低频逆向恢复，热读保持当前结构 | 采用。直接复用原 revisions 与 base 关系 |
| 独立通用版本框架、复杂 JSON Patch 或 SQL 多层 overlay | 增加读写、索引和兼容责任，收益未经量化 | 不引入 |

O2 按固定 base 读取，生成并验证小型 Bundle，再 apply 稀疏变化，取消 `branch_library()` 为每个 run 建整库。O3 开启时需要的候选 Reference 用 base 加受影响对象在内存中预览，沿用现有 compiler 的输出语义；暂停时直接跳过这段 O3 预览和物化。只处理受影响 Event/Fact/关系，不为计算 hash 再序列化所有 ticker 或整库。

原表职责调整如下。

| 既有结构 | 具体改动 |
| --- | --- |
| `library_heads/library_versions` | 继续保存当前版本、parent/base、发布时间和真实批次；小元数据常驻，无变化不升内容版本 |
| Event/Fact revisions | 当前最新内容保持可直接读取。被替换的旧 revision 只留一次必要旧值，使用既有 Native/压缩引用表示大字段；同 digest 复用，避免再写一份 before+after 全包 |
| Event/Fact states | 仅状态/redirect 改变时写记录；历史恢复按版本取原状态，纯复查不复制正文 |
| memberships/relations | 对实际 ADD/REMOVE 维护原有效区间；不用全组关闭再重建。恢复旧集合取相应关系，不另存每版完整集合 |
| review schedule、resolution/mapping | 保存本次真正变化的小记录与原幂等身份，不因内容 NOOP 丢掉必要结算 |

恢复版本 V 时固定读取当前 head H，以已有 base 关系倒序应用 H→V 的变化。对被替换/删除对象恢复旧 revision；对 V 之后首次新增的对象撤销；恢复原成员、状态与关系。只重建请求 ticker/版本或指定对象。旧值缺失则明确报缺口，不能拿当前内容补成假历史。新内容已在当前完整状态，逆向链无需每版再长期存完整 after 文件；后续被替换时自然成为下一次变化所需的唯一旧值。

版本与 lineage 继续使用现有 source/base、稳定 Event/Fact ID。迁移只加精确 `(old_root,ticker,version)` 映射到已收敛 lineage 的小索引，不能按同整数版号合并不同分支；历史曾分叉时沿实际 parent 恢复，不强接成一条伪顺序。

“实质变化”覆盖解释、时间/精度、状态/redirect、Reference 可见性、Fact 内容、关系和证据。只有纯生成时点/run 等技术字段放小收据。正文相同而引用更新，保存引用变化即可。已有 Fact 内容相等比较继续复用，Event revision 和成员更新也做同样的局部差量。

候选阶段的临时 ID 映射冻结在小 manifest，提交时核对 base 与 counter。并发人工发布冲突时重验受影响 O2/O3 结果，不沿用映射已变化的旧 O3 输出；不持长写锁等模型返回。

### 6.3 Reference、Case、W1 与冷恢复

- 当前 activation、当日 W1 基线、活跃 Case/watch 必需输入为热数据或已冻结证据；在正常发布准备中增量更新，W3 首次请求不等待归档或重建。
- Fact 首次获知保持现有 `fact_admissions()` 的业务内容判定，保留 first version/published_at、身份和未知 provenance 等小事实。发布时对变化 Fact 增量更新，替代 W1 每次沿旧库链查历史；不按归档时间重新计算。
- O3 关闭期间 Reference 最新状态随 O2 正常推进；待维护基线是最后一次实际 O3 ACK 的语义版本，不能被每次 O2 覆盖。只保版本/必要旧值和变化关系，重启无需全量 Reference 快照。
- 开启后的 `reference_deliveries` 保存真实提交版本、必要变化与小收据，成功后推进 ACK。去掉 before/after 全集及 rendered 的长期持久化责任，保留当前差分必需的一份热基线即可。
- 历史恢复复用现有 deferred query，读请求才发起，按具体 ticker/version 合并相同请求。固定 head H 后，通过该版本的不可变 revision/内容引用分短页读取，当前 head 再前进也不改变本次恢复。只保请求对应的精确短期引用，避免长读事务钉住 WAL。恢复输出在独立临时库/文件，CPU、内存与 I/O 预算见 12.4。完成后仅保请求消费期缓存，owner/TTL 到期退；没有请求不预热全部历史。

### 6.4 跨库采纳与故障恢复

Event、Research、Control 分库，先持久化受验证的候选变化，再在 Control 短事务比较 base。O3 正常时采纳新 Event+D3；关闭时走 4.2 的新 Event+原 D3。候选在切换前不作为默认 Published head 暴露。若当前实现必须先写 Event 版本，普通消费者仍只按正式 activation 明确版本读，未采纳候选由精确 owner 恢复或退出。

切换后按原幂等路径补本地 head 和 ACK，不能恢复出新 Event 配错 D3 的默认状态。O2-only 已完成而 O3 deferred 的事实作为成功的阶段结果保存；重启不能调用旧整批 `_consume()`。初始化和人工发布的同类调用也核对 current pointer 与内容版本边界。

### 6.5 迁移与收益验收

从活跃当前版和热依赖先收敛原分支；遍历旧分支时按实际 lineage 保留真正变化的旧值和必要小元数据。已有前向 revisions 能正确恢复时直接转换其 payload 引用，不先展开每个完整历史版再两两比较。确需比较的 legacy 边仅处理该 parent/child，一次转完登记完成；不在常驻 GC 重做这项工作。

逐组核对当前视图、W1 首次获知、inactive/redirect、成员、Native identity 和历史语义，再退旧整库。D 盘 archive 继续按原 locator 读取，转换成同一组件的历史材料。不能为每个历史 Markdown/JSON 导出字节不同保留整库。

验收同内容 Bundle/复查不新增正文和成员版本，无变化 run 不建 Event SQLite；目标为可比负载下候选库新增物理量下降至少 90%。真正变化的旧值存储单独核算，样本中无升版比例不作为承诺收益。

## 7. D1，当前完整 PolicySet 加对象旧值恢复

### 7.1 单份当前与原版本表承接历史

采用与 Event 相同的取舍。保留原 Research、`codex_document3_policy_sets`、staged receipt、base version 和内容池；当前正式完整 PolicySet 与当前执行 projection 正常服务，不新建一套通用稀疏版本目录或初始全量基线。

- 在原版本行保存 base/version/schema/发布与采纳时间、来源和摘要，历史行的大 JSON 改为变化引用与必要旧元数据。当前行保持完整可读，Repository 按行格式恢复老版。
- D3 发布准备时对本次实际变更 Policy 做一次比较。保存被替换/删除 Policy 的旧对象引用，ADD/REMOVE、政策顺序、coverage、origins 和集合元数据只存真正变化；新版本整体摘要不变时不复制内容。首版按 Policy 对象替换，不做复杂逐字段 diff。
- 历史恢复固定读取当前 H，沿原 base 倒序撤销变更至 V。ADD 撤销、REMOVE 恢复旧值、CHANGE 取前值；恢复实际旧 schema/身份/时间。集合外小元数据也恢复，不能只比较政策正文。
- staged 保存本次候选及轻量收据。候选通过引用进入正式化，随后释放暂存整套 JSON；最终只保当前完整版本、独有变化旧值与真实结果引用。迁移期读取旧 full 行，新写不双写旧格式。

后续 maintain 通过 Repository 获取旧 PolicySet/coverage/origins，取消“版本可用必须打开旧 Workspace 文件”的前提。暂停重新开启只准备最新有效 D3 与合并 feed，不恢复所有旧维护 workspace。

### 7.2 projection、派生文件与精确业务证据

当前 projection 在发布校验阶段计算一次并缓存。历史从恢复的策略语义重算，需要旧 schema 的实际解释规则时复用已有转换函数和受测格式版本；不为每个编译器 revision 永久维护一个独立运行环境。真实已执行条件、授权、消费与交易证据作为小型业务事实精确保留，不能仅依赖未来重算。

活跃 Case/watch 继续使用已冻结策略/条件和 guard。正常发布准备必要热输入；历史重算不得进入 W3 启动或结果消费路径。同内容的 Policy 也不能混淆 condition revision、`policy_activation_revision`、usage generation、已消费方向及 origins。

派生 Markdown、格式化 JSON、runtime projection 展示、coverage 导出和导航文件按需重新生成，允许字段顺序、缩进、模板或文件 SHA 不同。必须保持的业务内容从 typed schema、身份、时间和证据校验；原导出 SHA 可作为历史审计元数据，不能继续承担未来读取的强制字节恢复契约。改 `finalize_candidate_v21()`、maintain 的旧 manifest 读法及相应导出消费者，用语义来源/版本引用替代旧路径字节等同校验；对迁移中的当前活跃任务先完成其已承诺的本次验收再退文件。

只有确实不可重建的原始研究证据、实际收到的业务原文或交易证据保留必要原内容。不能以“少数历史导出无法逐字节重建”为由保留完整旧展示产物，也不为此增加序列化兼容层、旧模板仓库或保底完整版本。

### 7.3 迁移与验证

1. 识别当前正式行与热引用，旧 staged/正式同一版本确定语义一致后统一来源。
2. 复用原版序/base，分页生成真实变化的旧值引用；每次只处理相邻必要对象，固定高水位，受 12.4 资源预算约束。不能每轮扫描所有 PolicySets。
3. 验 `get_version/get_projection`、D3 maintain/NOOP、adoption、W3 Frozen Policy、Case 和方向消费。历史结构化内容、身份、时间与业务效果一致；导出文件比较解析结果，不要求旧字节 SHA 相同。
4. 逐组退重复 staged/full 列、Workspace 策略正文、旧 projection 和展示文件。当前读取不等待历史转换完毕，旧单个异常明确列出，不能冻结全库迁移。

目标为同 cohort 历史 D3 写入下降至少 80%；两大表约 23.94 GiB 为治理范围，可回收量按实际分配核算，不能把小逻辑 delta 的估计当整盘收益。

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

### 9.3 P1，物理回收按库证明收益

默认先减少写入和退出无用记录，让已有空页复用。`freelist` 是已分配的可复用页，不能重复算作新增业务或必然无效占用；若其很快被正常业务复用，自动缩小再扩展反而增加 I/O。取消上一版所有大库默认 INCREMENTAL、Acquisition 每分钟及其他库每五分钟检查缩容的安排。

| 数据库 | 默认决策 | 升级为物理回收的前提 |
| --- | --- | --- |
| Acquisition | A1 减量后先观察现有分配高水位与页复用；维持当前 WAL/auto_vacuum | 留存缩窗后的长期空页明显、实际根盘有收益，再用冻结负载比较 INCREMENTAL 与保留复用。通过才在一次必要维护中转换 |
| Runtime | 保持既有写入/连接和耐久性设置。优先移除重复内容及无用传输 owner，拒绝周期性 vacuum | 大规模迁移结束仍有显著持久空页时，评估一次现有 shadow/compact；高频 writer 不默认承担 pointer-map 维护成本 |
| Research/Bus | 保留可复用页，优先修退出与源头写入 | 大字段退出后算一次真实可回收 GiB；已有维护窗口能证明收益才 compact。INCREMENTAL 单独实测，不能从 Acquisition 的结果外推 |
| Read | 优先借本来就要生成的精简代际收敛，避免为物理回收多做一遍全库复制 | 新代际模式依据其更新/删除比例与性能对照决定，不强制加 INCREMENTAL |
| SDK | 沿 SDK 自身模式和维护入口 | 禁止外部改活跃私库模式或手删 WAL；按其 owner 批量退出 |

INCREMENTAL 的小页参数不代表小锁时，也不能保证压到指定毫秒内。选型对照须测总设备写字节、WAL/checkpoint、CPU、实际 allocated/df、页复用率、writer 等待及 W3 启动/消费时延，至少跨完整删除→复用周期。未通过或收益小就不启用，先用现有空页。不能为一次空闲页读数触发昂贵模式迁移。

确实启用的库在业务删除批次中顺带标记“可能需回收”，以已有低频容量采样复核，不另加定时 PRAGMA 扫所有库。空闲页很快复用则清掉候选；真实大额持久可回收量才进入本库的一次后台尝试。共享进程写门与 SQLite writer 均非阻塞获取，失败立即释放、退避，不持第一道锁等第二道；一旦对实时链造成可归因等待，停止该自动动作，降低单步工作或回到人工维护窗口。不能为清退进度强占写锁。

旧 `auto_vacuum=0` 的模式转换需要真实重建成本，不能只改 PRAGMA。若已批准该库转换，将其并入必要 shadow 迁移，在新目标建表前设置模式，保留主键、生成列、sqlite_sequence、epoch/head/consumer 和原耐久性。复制时不触发一轮新 CDC；数据一致性、WAL 与模式均验证后切换。不能假定原 `VACUUM INTO` 命令自动实现模式转换。

沿用现有自动 checkpoint 与正确 WAL 生命周期。只有已有 backlog/reader 指标显示异常时才针对该库诊断；不额外常驻 PASSIVE/TRUNCATE 轮询。`-wal/-shm` 不是清理对象。一次 compact/index 施工先算候选、临时排序、WAL 与业务写入余量，先部署兼容读端，按受影响 writer 安排既有维护窗口；不以背景线程或 nice 掩盖长锁。

本包的通过条件是：短寿命数据量达到边界、物理占用可解释、需要归还的空间有低成本兑现方式。并非每个库必须自动缩小。低余量优先退出已验证文件副本和无用写入，不能在 Runtime 繁忙时启动大库重建救急。

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
| B0 | M0 O3 独立跳过、O2-only activation、分阶段 ACK、授权复用与 Selection | 固定最新 main，核对实时版本与 owner；O2 正常推进、暂停/重开/消费与授权测试通过即可独立发布 |
| B1 | T1 cursor/index、M1 局部退出与导航输入减量 | O3 关闭期间继续 O2；旧大扫描不接回，保持原调度，验 Runtime/W3 热路径 |
| B2 | E1 延后 apply、当前完整与稀疏旧值、Fact admission 元数据 | B1 提供退出通道；先转换 live lineage。O2-only 与正常 O2/O3 采纳都验证 |
| B3 | D1 策略对象历史、Read/Runtime 载荷与查询重构 | 统一版本接口后改上下游，不在中途长期保留新旧全量双写 |
| B4 | A1 观测/聚合先改、明细缩窗；P1 逐库回收决策 | 先比较复用、I/O 与锁成本，有明确收益和余量才转换或 compact。T1 可先独立上线 |
| B5 | S1/O1、轻量指标、性能防回退与有限恢复试跑 | 不阻塞主要增长源治理。O3 在输入减量、正常消费及资源验收通过后按原调度重新打开 |

正式业务读取兼容先上，单一新写者随后启用，再分批转换旧数据；不长期维持两套完整写入。不要求实现全局资源服务或统一跨库事务。所有重要产品修改追加 changelog，保留此方案 ID、行为改变与测试结果。

### 11.2 存量转换与清退属于同一交付的收尾

实施前刷新各根 allocated、SQLite freelist、活跃 owner 与实际镜像。对照 11-23 数据生成可执行候选，不硬套旧清理列表。正式清退顺序为终态过程目录 → 无独有内容的 Event 库 → 已转换的 D3 双份/旧投影 → 已 ACK 运输与无引用 Native → 旧 Read 代际 → 必要旧库缩容 → SDK/临时产物。

每页或一组实际目录取得内容/引用验证后即可清退，避免先制造一整套 300 GiB 备份。普通缓存、确认相同的副本不额外备份；真正不可替代的历史变化有压缩恢复材料和可验证 locator。当前正式组合、热 Case/交易/watch 资料在切换前已齐备。恢复包和迁移临时库各留明确 owner，验收后退役，避免新治理再制造冗余。

旧 Event 分支 basename/整数版号不作为唯一标识，D3 的 Policy/condition/activation ID 不重编。Read 的历史 gap、未知 provenance 和真实 source 时点原样保留。采集 PENDING、SDK unknown turn 和 incomplete import 逐 owner 处理，不能为达标伪造 ACK。

### 11.3 回滚与中断

- 每个新格式先给正式和选定回滚镜像装兼容读能力。新写格式启用后回滚到该兼容版本；不能指望旧镜像读取已被删掉的 full payload。
- M0 可随时再次关闭，O2 继续；重新开启只合并实际未消费的 O3 义务，不逐条恢复所有旧 Task/工作区。
- 归档/迁移在记录持久完成和校验前不移除唯一源。原子发布文件后发生 DB 提交失败，留下的无引用候选由本地 owner 重试/退出；不是恢复全局一致性的借口。
- 格式升级、索引安装、大库切换和轻量日常退出使用现有各自的锁，文件工作不持有 Runtime/Worker writer。一个 owner 失败不得阻止其他 source 和目录继续退出。

## 12. 收益预算、观测与长期验收

### 12.1 预期收益的正确口径

| 改动 | 持续减量目标 | 一次性空间 |
| --- | --- | --- |
| 仅暂停 O3 | O3 新 Job/工作区/全量 feed 写入归零；O2 正常运行，O3 欠账仅小 marker 与必要原记录 | 自身不回收既有 100 GiB 工作区，也不把 O2 的真实成本当成零 |
| M1 恢复后 | 同等输入/完成量的 bytes/run 至少降 80%，终态过程数据在窗口后平台化 | 既有父子和已结算过程树逐组实测，不能承诺 100 GiB 全可删 |
| E1 | 无变化 run 零候选库，整体候选物理新增目标降 90% | 可退旧分支按恢复验证页计；不可与 Runtime 根差额相加 |
| D1 | 同 cohort 历史策略写入目标降 80%，无重复全策略与历史 projection | 两大表 23.94 GiB 是治理范围，非保证回收量 |
| Read/Runtime | 去多壳 payload、重复容器和无消费者 CDC；新物理增量、写字节及引用数量明显下降 | 精简代际和大列退出后才核算实际页/文件收益 |
| T1/A1/P1 | 过龄已 ACK 正文及时退出，短寿命明细占用收敛；freelist 合理复用或按实测收益归还 | 11-23 的约 11 GiB Acquisition 差已实现，不能再次计入未来收益 |

整体首轮验收目标为修复全部启用后，**连续五个正常交易日**，可再生/技术资源滚动五日净增长不大于 1 GiB，整盘平均净增长相对最近可用业务量基线至少下降 80%，暂设日均不高于 2 GiB 作为容量告警线。2 GiB/日只表示阶段性显著改善，有限根盘仍不可持续无限增长；必须把剩余增量归到真实独有业务，并计算容量跑道。小型事实可长期留，大正文按活跃引用/访问价值决定在线与历史压缩位置，需求变化时调整，不能用这条数字掩盖剩余无价值增长。

O3 暂停期间的五日指标只验阶段性止增，O2 完成量必须正常。最终五日窗口包含按原调度恢复的 O3，按实际输入、O2/O3 完成量与有效变更对齐。另连续观察超过最长自动保留窗口，确保过期后占用平台化，不能只挑低负载周末。

### 12.2 事件登记、空队列休眠和轻量观测

终态、消费确认、版本替换、文件创建/退出和开关变更是治理触发源。候选登记优先并入原业务事务，单次只增小标量/版本引用，不记录全 frame、正文或每文件一笔事务。原事务已保存的 job/run/generation 直接复用；同值不更新。若进程在业务提交后、通知前中断，启动恢复一次持久候选即可，不能依赖易失通知保证安全。

本组件后台用已有事件/完成通知唤醒，候选按到期索引取有限页。内存保存本 owner 下一到期/重试时点，队列空时休眠到下一事件或已有低频维护 tick；跨进程遗漏通知通过启动恢复和原五分钟级维护回访补齐。禁止每秒查询待删对象、每轮扫全量 Case/Jobs 或持续写空回执。写入候选失败重试由原提交/ACK 负责，不给 W3 启动串入新的治理 RPC。

复用 release capacity 和 owner receipt。进程内累计 produced/retired bytes、成功/忙锁、最后推进和最老已见到期时点；随实际批次提交或已有小时容量回执合并输出。空轮不落一份全量状态。全局精确 COUNT/排行不是日常指标，pending 数按登记/结算差量；重启校准只走队列状态索引，昂贵统计仅调查时在冻结样本执行。

根盘 df、库文件 stat 在已有小时采样一次读取；页数/freelist 按被改变的库采样，不轮询所有库。文件 allocated 结合已知路径和 inode，实际 df 独立对账。停止大型目录反复 25 秒超时再从头 du；新资源沿创建清单计账，legacy 校准按目录游标跨轮推进，结果明确实际测量时点、完整性和共享 inode 口径。

### 12.3 必要验证与优先级

| 场景 | 通过条件 |
| --- | --- |
| O3 关闭、O2 正常 | O2 Job/必要 Event activation 正常推进，D3 引用不变，无新 O3 Job/工作区或 O3 采纳；原已运行 O3 有精确终态 |
| 分阶段消费 | 候选仅确认真实 O2 resolution，trade/badcase/gap/O3 候选仍待消费；O2 不因 O3 欠账空转，重开按最新有效状态处理一次 |
| Event-only 授权 | 重复及连续 O2 activation 不重放 accepted_rearm，不修改 consumption/usage generation，旧/新 Case guard 正确 |
| 重启/并发控制 | activation 后、ACK 前中断可幂等收尾；人工发布 CAS 冲突、迟到 O3 成功和开关切换不产生混合正式状态 |
| 调度保持 | 原 ticker 租约、执行器、Worker lane 与 W3 并发继续使用；无新增全局单并发或固定小时批次 |
| 逆向恢复 | 覆盖 ADD/REMOVE/CHANGE、退役恢复、分叉、旧值缺失、Fact 获知时间、D3 schema/顺序/授权。业务语义一致，导出字节可不同 |
| API/Read | 当前分页/KPI/Case 正确；短 token、REMOVE 与冷历史 deferred 正确；JOIN 和引用替代没有 N+1、全库排序或重复大 JSON 解码 |
| CDC/Native | 年轻记录到龄、ACK 不变、timestamp 无序、pin、索引缺失/中断、snapshot 租约及删除中断可续；不为测 oldest 每轮全表扫描 |
| Acquisition | 接收幂等、PENDING、coverage/late recovery 正确；时段聚合不重扫小时全量，异常样本有界 |
| 物理回收 | 按库证明净收益；不合适的库不开启，不新增周期性全库 PRAGMA/vacuum；已选动作的设备写入和锁时达标 |
| SDK/其他退出 | 成功业务证据保留，活跃线程与浏览器身份正确；终态局部退出无需全局 idle 或全树 listing |
| 资源与 Runtime 性能 | 同负载对照无可重复的 W3 启动/回执消费/推进恶化；既有性能指标继续验，背景进度不能抵消实时回退 |

采用固定 cohort/同一业务量、冷暖缓存分别可比的对照，记录 CPU 时间、峰值增量内存、块读写、事务次数及 writer wait/hold，避免用净磁盘变小掩盖更高 I/O。源头少写应降低总工作量；新治理动作本身单独记账。观测复用现有 trace/cgroup 小窗口和小时记录，不新增高频 profiler。

共享 SQLite 天然有瞬时互斥，nice/低优先级不能消除持锁造成的等待。通过优先合并原事务、最小改动、非阻塞尝试和推迟重操作控制成本。若出现可归因的新 W3 等待，先停该新增后台动作并缩减工作/事务，不能仅放宽 P95 阈值或要求实时业务让路。删除原 5%/200ms 的默认性能回退许可。

### 12.4 所有治理动作的性能与必要性审查

以下要求覆盖现有路径和新设计。执行额度限制的是实际读取/解码字节、文件数和锁内语句工作，单写一个 LIMIT/超时不表示整轮有界。实现逐项列出触发者、候选索引、单步字节、内存峰值、所需锁和退出条件；无需再建立全局调度/资源管理框架。

| 动作与已核对风险 | 调整后的触发、工作与锁边界 | 验证/停止条件 |
| --- | --- | --- |
| O3 gate 与待处理 | 开关变更通知、Maintenance 阶段边界；marker 随原真实输入变化更新，同值不写 | 普通 Case/W3 无额外开关 SQL；无每 tick 全队列查找 |
| O2/O3 ACK | 精确记录 ID/代次，原事务只更新对应标量；`_mark_daily_payloads` 若仅改 daily_status，避免解码、重新编码大 Native 载荷 | 对齐 SQL 标量与模型读取结果，确保必要 CDC 状态继续可见 |
| O3 物料与发布 | 已有冻结/发布过程内算一次 digest；导航指针只读实际请求页，硬链接只用于不可变文件 | 不为了省存储对每次 read 重算整文 hash；同 family 不重复索引 |
| 终态 Workspace 删除 | 终态与下游 ACK 入本地队列，按 run 精确查 live owner；短 fence 后锁外删目录/空祖先 | legacy `workspace_retention` 的全 bundles/evidence 解码不进入常驻循环；大树分步续，不一次全树 rglob 后再限量 |
| Session 退出 | 已知 thread family 的终态/registry 事件，精确 read/delete；局部 relation，不扫所有 Jobs/thread/list | 删除不占活跃调度锁，不等待全局 quiet；未知 group 单独复核 |
| Event 内容与历史 | 候选变化对象比较一次；原 revision/base 记录旧值，现有索引查精确对象 | 无整库 clone、全 lineage 反复展开、整 ticker hash；当前读不追历史链 |
| D3 差异/历史 projection | 正式发布对一个 ticker 的实际候选与 current 比一次；旧值去重、元数据小更新 | 历史 export 请求才恢复，发布不生成每个旧版的完整 projection |
| 冷恢复/迁移/压缩 | 明确请求或一次迁移游标触发；独立临时输出，源只读短页，CPU 重活在写锁外 | 不在后台自动预恢复全部历史；禁止长只读 snapshot 无限钉 WAL、全库加载或聚合大 JSON |
| Reference/W1 | 已发布变化更新 admission 小事实与局部缓存；O3 暂停只更新版本引用 | 不每次 O2 写 before/after 全文，不在 W3 冷启动沿全部分支追溯 |
| Read GC | 沿原分阶段短提交，先判该阶段有到期候选，token/floor 只有变化才持久化 | 当前 stage 无候选不争 writer；无变化阶段内存轮转，完成真实进度才合并 checkpoint；崩溃允许幂等复扫 |
| Read 定义与 KPI | 已有内容去重、关系 JOIN/批量 SQL；只对变更集合更新 refs/贡献 | 取消冗余摘要不能引入每页 N+1 或每激活全 catalog 扫描；必要查询索引保留 |
| Read→Runtime 小业务事实同步 | `sync_business_facts()` 已按 revision 增量复制，仍在 complete 时全取 policy_hit_set IDs 做集合比较；改为按该类真实变化/移除同步局部键 | 用已有变化坐标/精确 tombstone 退出，不每次 complete 构造全部 ID 的 frozenset。单一 seq 可能包含大批记录，补稳定主键子游标和字节上限，读完前不能越过该 seq；ACK 与删除义务必须一致 |
| Transport expiry | 时间索引/ACK 取到期小页；缺索引只轮转有限页并记已见最早未来到期，避免每轮重扫年轻前缀 | 已到 ACK 尾部且无新到龄/ACK/pin 变化时休眠；不为 expired=0 反复开 writer |
| Native reconcile | 迁移时一次带游标修旧 refs，触发器/owner 之后直接维护精确 refs | 原 `reconcile()` 对已完成 owner 仍可能开空写事务，改为 ready 后只受 schema/修复事件触发；不能常驻扫描全部表/大 JSON |
| Native inventory/collect | put 记录对象，最终 ref 释放登记 exit；只取到期 ID，锁内复核 refs/hold，锁外文件工作 | 原按 hash 循环 age scan/目录 inventory 降为一次 legacy 校准或确切 orphan 修复；旧 ref 修复屏障不能永久冻结新格式已知 owner 的退出 |
| Runtime resource_gc | 原终态 Task/consume 事件登记候选，复用 local queue；精确 root+活跃 owner 查询 | 避免每15min从头扫整个终态历史；`OR root IS NULL`、opaque 全保护和逐候选扫描全部 queued Jobs 只用于有限 legacy 修复，不能作为新路径前置 |
| Runtime 补偿/Selection | `acknowledge_compensations()` 目前会查历史成功补偿 Task 和全部失败集合；改用实际新完成/失败 owner 的局部输入。`_freeze_selection()` 按 ticker+PENDING+原 cutoff 索引选候选 | 不在每分钟恢复中反复反序列化旧 frame，不用 `journal.values('candidates')` 拉所有历史；仍保持原 Selection 时间和范围 |
| 打开文件/引用核验 | 现有 lease、已知 reader/version pin 与家族 owner 负责；只复核候选有关的实际句柄 | 不对每个候选枚举全盘文件再扫全 /proc；未知遗留对象列为单项调查，已确定 owner 可继续退出 |
| Acquisition seal/退出 | 原结果提交增量统计、到龄小时与运行中的确切 unit；异常样本按小键计数 | seal 不全读一小时 requests；候选读→原子确认→锁外退出，预算包括后续 small/physical/checkpoint 各阶段，不能只限 hours 循环 |
| SQLite checkpoint/回收/索引 | 9.3 的按库决策；索引随正式迁移一次安装，后台只校验已知版本 | 建索引和 vacuum 不能在 W3/Runtime 活跃启动链执行。空页复用有利时不开新动作 |
| SDK logging/退出 | producer 丢弃无用字段，原批写按量/终态维护；不逐 chunk 落第二份诊断 | Native 真实计时、Usage 保留；不靠日志 DELETE 速度抵消 TRACE 写入 |
| 浏览器/Ops/备份 | 真实身份接替、运行完成和发布成功时 owner 收尾 | 不每次巡检 hash 全 profile、重备全库或生成完整取证；无事件不检查 |
| 容量/进度/告警 | 复用原小时采样与现有事件累计，按最老已见候选/队列索引判断滞后 | 不为监控开发新的全树扫描、全库 COUNT、每秒状态事务；空转回执不落盘 |

新工作项在进入后台前已有候选，不通过全库“先发现是否有事”启动。每步先用小元数据筛选，确需正文再读取；跨页按字节/文件量和时间共同截止，不能只按行数读取几十 MiB 的单行。超大唯一业务对象用流式处理或留在一次明确维护中，普通退出继续推进。

首发资源额度是实现验收上限候选，不需要为它们新建配置平台：轻量一批先限 32 个候选/约 1 MiB 实际读取；正文处理每步尽量控制在 20ms CPU 内，常驻临时内存增量目标不超过 16 MiB。冷恢复/历史迁移可提高进程局部额度至约 64 MiB，但按页分步并允许因实时压力推迟，既有执行通道每 owner 不叠多个重任务。恢复很慢可以接受，不能自动升级额度抢资源。

新增独立共享库写批次目标是几毫秒，先按持锁 P95≤5ms、单次20ms审查；无法做到就合并原事务、拆小或取消该后台写法，而非直接沿用旧50ms许可。进程写门和 SQLite 锁均 try-acquire，获取失败释放并延后，不在 writer 或同步 W3 回调中 sleep/retry。后台新请求不得抢在已排队实时工作前反复重试；复用现有低优先级 write_io/门，不新建全局优先级协调器。

普通终态文件退出可以在系统持续繁忙时有限推进；需要长锁或全量 I/O 的维护允许推迟，禁止靠抢占 W3 保证“每分钟至少删一条”。长期滞后记录精确 owner 和受阻原因，优先减少源头写入、消除无效重复事务；必要的大库操作留明确维护窗口。最终以总资源成本下降和实时链无回退同时达标，不能只交付清理吞吐。

## 13. 本轮设计选择与实施前补证

本版选择 O3 独立跳过、O2-only 采纳、分消费者确认、原调度恢复、当前完整版加必要旧值的逆向历史，以及按请求生成派生展示。删除上一版的复合任务整体暂停、全局单并发、独立通道、每小时批次、永久初始完整基线、旧展示字节保留和默认 INCREMENTAL 转换安排。

成功过程 24h、失败 72h、采集成功薄证据 2h、SDK 日志 512 MiB/7d 继续作为原方案的首发设计值，实施回执明确并验证。12.4 的资源额度是对实际成本的验收候选，不要求新增一套配置平台。W3 时效优先级高于任何清理吞吐目标。

实施前仍需补取四项小范围证据，不妨碍先开发暂停和确定性缺陷修复。

1. 当前 Maintenance 按 scope/phase 的队列数、最大年龄与真实 O2/O3 消费覆盖。仅为正确跳过、迁移和合并取一次小范围证据；本轮不以此扩展成资源争用或并发重构项目。
2. Event/Policy 的真实变化对象比例及 Reference/W1 热引用集合，精确到 lineage。历史异常不得被内容去重隐藏。
3. Read current/索引和 Runtime namespace 的物理页分摊，以及 SDK 实际构建的日志 filter/history 退出能力。重查询应在冻结样本上做，避免在线扫描多 GiB 再影响业务。按 12.4 核对逐项真实读取量与锁成本，尤其业务事实同步的同 seq 大批量、补偿历史扫描和空写事务。
4. 上线时真实根盘余量、空页复用趋势、必要索引施工和各库回收方式的实际成本。11-23 19:13 的 14.77 GB 可用是历史采样，不代表未来施工容量。

所有设计以少写独有变化、在既有业务 owner 内结算和有界执行为落点。需要恢复的历史保存足够的真实变化和身份，不再以保存每次运行的完整物理副本承担恢复责任。

## 附录 A. 实施文件与最小回归入口

以下为已核查的主要落点。新增测试只覆盖本方案引入的确定性缺陷与恢复边界，复用已有测试集和 fixture。

| 工作包 | 主要文件 | 首要回归入口 |
| --- | --- | --- |
| M0 | [Runtime 调度](../src/doxagent/persistent_runtime_v2/coordinator.py)、[阶段执行](../src/doxagent/persistent_runtime_v2/maintenance.py)、[失败恢复](../src/doxagent/persistent_runtime_v2/maintenance_recovery.py)、[正式采纳](../src/doxagent/ticker_initialization/repository.py)、[实际组合读取](../src/doxagent/ticker_initialization/runtime_inputs.py)、[消费/授权](../src/doxagent/persistent_runtime_v2/policy_consumption.py)、[Worker 派发](../src/doxagent/codex_worker/jobs.py) | O2-only activation、ACK 分拆、rearm 不重放、开关竞态、Selection、重启与 O3 合并追平 |
| M1 | [物化与快照](../src/doxagent/workflows/codex_document3/runner_v21.py)、[导航](../src/doxagent/workflows/codex_document3/materials_v21.py)、[原导航文件生成](../src/doxagent/codex_runtime/context_index.py)、[Workspace](../src/doxagent/codex_worker/workspace_store.py)、[退出](../src/doxagent/codex_worker/workspace_retention.py)、[后台入口](../src/doxagent/codex_worker/resource_maintenance.py) | [D3 Maintenance](../tests/test_codex_document3_v21_maintenance.py)、[Runtime 退出](../tests/test_runtime_resource_exit.py)；增加 inode、导航完整性和中断收尾 |
| E1 | [Event Repository](../src/doxagent/event_library/repository.py)、[旧分支入口](../src/doxagent/persistent_runtime_v2/event_branch.py)、[W1 日基线](../src/doxagent/persistent_runtime_v2/daily_index.py)、[Reference 交接](../src/doxagent/persistent_runtime_v2/reference_capture.py) | [增量消费](../tests/test_event_library_incremental_consumers.py)、[时间语义](../tests/test_event_library_time_reference_repair.py)、[视图合同](../tests/test_event_library_view_contracts.py) |
| D1 | [staged 状态](../src/doxagent/workflows/codex_document3/state_v21.py)、[正式存储](../src/doxagent/workflows/codex_document3/repository.py)、[候选交接](../src/doxagent/workflows/codex_document3/publication_v21.py)、[维护编排](../src/doxagent/workflows/codex_document3/orchestrator_v21.py) | [发布合同](../tests/test_document3_v21_publication.py)、[维护](../tests/test_codex_document3_v21_maintenance.py)，增加稀疏历史恢复与消费代次 |
| R1 | [formal](../src/doxagent/v2_read/formal.py)、[lifecycle](../src/doxagent/v2_read/lifecycle.py)、[Read Store](../src/doxagent/v2_read/repository.py)、[Case Evidence](../src/doxagent/api_v2/case_evidence.py)、[Policy SQL](../src/doxagent/api_v2/policy_queries.py)、[DeferredQueries](../src/doxagent/api_v2/background_queries.py)、[入站事务](../src/doxagent/persistent_runtime_v2/journal.py)、[小业务事实同步](../src/doxagent/v2_read/source_lifecycle.py) | [Policy 查询](../tests/v2_backend/test_policy_queries.py)，加旧 token、历史恢复、同 seq 大批量同步与热 Case 大样本比较 |
| T1 | [source_lifecycle](../src/doxagent/v2_read/source_lifecycle.py)、[transport 主循环](../src/doxagent/v2_read/transport_archive.py)、[producer 捕获](../src/doxagent/v2_read/outbox.py)、[Native 退出](../src/doxagent/v2_read/source_native_gc.py) | [传输生命周期](../tests/v2_backend/test_source_lifecycle.py)，增加年轻前缀重访、unordered time、pin 和持续忙场景 |
| A1/P1 | [Acquisition Store](../src/doxagent/message_bus_v2/collection_plane/store.py)、[小时归档](../src/doxagent/message_bus_v2/collection_plane/maintenance.py)、[实际观测消费者](../src/doxagent/message_bus_v2/collection_plane/observability.py)、[Read 新代际](../src/doxagent/v2_read/serving_generation.py) | 2h/48h 跨界、late recovery、空页复用；仅对选定库做 INCREMENTAL 对照与模式切换验证 |
| S1/O1 | [会话退出](../src/doxagent/codex_worker/session_retention.py)、[W3 线程 owner](../src/doxagent/codex_worker/thread_lifecycle.py)、[SDK 固定构建](../deploy/codex-patches/prepare_build.py)、[发布/容量 owner](../scripts/ops/storage_release.py) | [会话退出](../tests/test_terminal_session_retention.py)、[生命周期](../tests/test_storage_lifecycle_20261111.py)、[发布](../tests/test_storage_release.py)，增加永久有实时 Job 的退出进度验证 |
