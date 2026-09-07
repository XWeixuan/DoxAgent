# DoxAgent Workflow V2：Ticker 初始化整体编排开发方案

日期：2026-09-05

状态：开发方案；尚未实施

范围：美股 ticker 初始化、节点级恢复、产物激活与 Message Bus / Runtime V2 接管

业务版本：Workflow V2。现有组件内部 `*_v1` schema/version 标识不因本方案机械改名。

## 1. 目标与冻结决策

提供一个持久化初始化入口，将现有各板块工作流接通。正常情况从入口自动执行到 Message Bus、W1/W2/W3 就绪；中断后恢复到未完成的内部节点；已经运行的 ticker 在新初始化失败时继续使用旧激活版本。

以下为用户已确认的需求，后续实现不得改变：

1. D1 与 CDECR 并行；O2 等待本轮 D1 和 CDECR，之后依次 D2、D3、O4 CONFIGURE、O4 DELIVER、统一注册、启动总线、启动 Runtime、验证就绪。
2. workflow 结果只有 `SUCCEEDED` / `FAILED`。PARTIAL、DEGRADED 是诊断，不驱动父编排分支，不触发额外质量重试。
3. 每个内部执行节点首次执行之外只有一次自动失败重试；预算耗尽后只能人工恢复。失败和重启不得重置预算。
4. 全过程节点级断点恢复；例如 D3 Policy Compile 失败，不重跑已成功的 Trigger Calibration、D2、D1。
5. 显式替换上游不自动失效、重跑或修复下游；单节点执行、板块执行、下游失效均为独立人工操作。
6. 使用 ticker 级 Activation Revision；长期实例不永久绑定某一个 run。运行中的 attempt/case 仍使用固定输入快照。
7. 保留人工全 workflow 重新初始化入口，默认不触发。
8. 初始化不包含 O4 REPAIR。必须等待 DELIVER 完成结算，统一注册入 Message Bus 后一同启动。
9. W3 仅 readiness 验证，不要求真实触发一个 W3 Case。
10. 允许丢弃 Message Bus 启动至 Runtime 首次消费启动之间的消息。
11. 完整初始化默认 `Runtime V2 / TRADING`；不由本功能新增券商执行或账户授权。
12. 只支持美股；本期不实现代码、skill、prompt 热更新，只记录执行版本并避免长期绑定设计。
13. 使用本地 SQLite + 远端 Supabase；高频和大 payload 不进入 Supabase 新增同步路径。
14. 同 ticker 只能有一个 active initialization；重复提交无效，不附着、不重建、不重置原任务。
15. 固定 research cutoff；允许为上述契约实施必要的节点边界改造。

## 2. 范围与后续运行编排接口

本期交付：CLI、后台初始化 Worker、持久化状态、子节点适配、恢复与人工操作入口、候选产物与激活、总线/Runtime 启动握手、Supabase 摘要同步、测试及运行手册。无需前端、HTTP API、分布式任务平台。

用户附件《02:00 ET 语义日边界与持久化运行编排》是后续第二部分的业务参考。本期预留 `semantic_day`、`activation_revision`、`runtime_started_at`、`maintenance_cycle_id` 接口。日界线采用 `America/New_York` 的本地 02:00；春季不存在该时刻时，取当天第一个不早于 02:00 的合法本地时刻。UTC 时间保存，ET 用于业务日计算。semantic day 不替代 research cutoff。

日常 O2/O3 Maintain、连续休市 polling、Weekend Sweep、Trade Candidate、03:45 W3 Sweep 和 04:00 交易恢复均由第二部分实施。本期启动就绪不以这些尚未实现的调度行为为成功条件。未来 scheduler 接管时应根据市场日历决定运行模式，避免初始化入口永久强制连续 polling。

## 3. 当前实现基线与改造落点

本轮源码核对以以下文件为依据；开发开始时再次核对工作树，保留无关修改。

| 当前实现 | 已有能力 | 本期必须补齐 |
|---|---|---|
| `src/doxagent/cdecr_integration/initialization_orchestrator.py` | D1/CDECR 并行，O2→D2→可选 D3，父状态落库 | 统一入口、内部节点恢复引用、O4/启动链；恢复不把进度退回上游 |
| `src/doxagent/cdecr_integration/coordinator.py` | ticker job、epoch、snapshot、O2 run 及部分恢复 | 暴露内部恢复单位和结果；固定 child identity；发布与回执对账 |
| `src/doxagent/workflows/codex_global_research/orchestrator.py` | D1 DAG、成功节点按输入恢复 | 无可用交接物才失败；防止父恢复重新执行已成功节点 |
| `src/doxagent/workflows/codex_document2/orchestrator.py` | 冻结输入、O0 阶段、O1 shell/turn checkpoint | published PARTIAL 在初始化恢复时直接复用；补齐并行节点完成即落库 |
| `src/doxagent/workflows/codex_document3/orchestrator.py` | Stage A / Compile / Review checkpoint，冻结输入 | 延迟激活、O4 触发所有权、发布边界崩溃恢复 |
| `src/doxagent/workflows/codex_monitoring_o4/orchestrator.py` | CONFIGURE、DELIVER checkpoint/continuation、注册能力 | 初始化模式取消异常/finally 自动启动；结算后统一配置激活 |
| `src/doxagent/runtime_scheduler/documents.py`、`service.py` | 老文档初始化路径、ticker 运行管理 | 新入口直接消费 V2 Activation Revision，禁止落回旧 Blackboard 初始化 |
| `src/doxagent/message_bus_v2/service.py` | ticker 启动、binding、runtime cursor | 配置 revision、禁采集候选、启动确认、首次 tail 游标幂等 |
| `src/doxagent/persistent_runtime_v2/service.py` | Case 级版本 pin、W1/W2/W3 | 从同一 active revision 解析依赖，保留 Case 快照 |
| `src/doxagent/codex_runtime/repository.py` | SQLite / Postgres / Hybrid repositories | 复用本地真相与远端轻量投影模式，禁止新增全文云端同步 |

现有两处行为必须显式调整：D2 published PARTIAL 不能因父级恢复被重新打开；O4 中断后不能提前启动初始化 ticker。D3 自动 enqueue O4 与父编排触发只能保留一个所有者，不能并发重复执行。

当前组件测试只能证明局部行为，不能作为新整体编排已验收的证据。先前恢复测试组合在收集阶段遇到模块 skip / no collectors，开发时应选取实际可收集的 V2 测试并明确隔离遗留测试。

## 4. 总体执行结构

```text
CLI submit
  → SQLite 接纳事务（ticker 唯一 active）
  → Initialization Worker 领取 / 续租
  → D1 内部 DAG ───────────┐
  → CDECR 初始化内部流程 ──┤ 两者交接物就绪
                           ↓
                          O2
                           ↓
                          D2
                           ↓
                          D3
                           ↓
                     O4 CONFIGURE
                           ↓
                     O4 DELIVER / NOOP
                           ↓
                  统一结算、注册、候选配置
                           ↓
                 预加载候选依赖与 readiness
                           ↓
                 提交 Activation Revision
                           ↓
                Message Bus → Runtime V2
                           ↓
                启动确认 → SUCCEEDED
```

新增 `src/doxagent/ticker_initialization/` 作为父编排模块。父 Worker 调度板块适配器；适配器继续调用原有内部 DAG，不复制节点业务算法。父库保存节点标识、结果引用和执行索引，子库保存完整 checkpoint/产物。通过稳定 ID 与启动对账连接，不引入跨库分布式事务。

初版是一台拥有持久卷的服务主机，可有不同 Docker Worker 共享本地卷。全局初始化并发默认 2、每 ticker 一个变更操作；参数可配置。SQLite 不承担跨主机协作，不把网络文件系统上的 SQLite 当分布式数据库。Supabase 不承担高频锁与租约。

## 5. 状态机与失败语义

### 5.1 四个相互独立的状态维度

| 维度 | 值 / 内容 | 规则 |
|---|---|---|
| Initialization lifecycle | QUEUED / RUNNING / SUCCEEDED / FAILED | 只有后二者是结果；人工 resume 可使 FAILED→QUEUED |
| 内部 node lifecycle | PENDING / RUNNING / SUCCEEDED / FAILED | 已成功节点默认永久复用；显式人工失效另记操作 |
| Attempt lifecycle | RUNNING / SUCCEEDED / FAILED | 崩溃未确认结果先 reconciliation，再确定失败，不直接重调 |
| ticker operational state | 尚未激活 / RUNNING / PAUSED / STOPPED | 独立于某次初始化成败；重初始化失败不能停止旧运行 |

`phase` 是进度视图：UPSTREAM、O2、D2、D3、O4_CONFIGURE、O4_DELIVER、REGISTER、ACTIVATE、START_BUS、START_RUNTIME、VERIFY_READY。并行内部节点列表独立展示。节点动态展开后进度分母可变，因此不承诺虚假线性百分比。

`quality_annotations` 保存原始 PARTIAL / DEGRADED / 警告、原始状态和原因。无独立 WARNING、BLOCKED、DEGRADED workflow 结果。重试等待仍为 RUNNING，附 `next_attempt_at`；预算耗尽为 FAILED，附 `manual_resume_required=true`。

父级失败时允许已经开始且相互独立的兄弟节点完成并持久化，不强制取消成功机会；不再启动依赖失败结果的节点。ticker active 占位直到这些执行停止/完成且 lease 安全释放后才释放，避免失败显示导致第二任务抢占。

### 5.2 最小可用交接物

| 板块 | 成功条件 | 不作为阻断的情况 |
|---|---|---|
| D1 | 下游可实际读取的 research handoff 与必要 C1/C3/C5 内容，或显式选用的已有可用版本 | 局部研究缺口、引用质量警告、非必要分支失败 |
| CDECR/O2 | 可读取的 Event Library / Reference View；NOOP 必须能解析既有版本或有效空库 | 无新增事实、部分记录隔离、局部质量问题 |
| D2 | published 且下游可加载的文档交接物 | PARTIAL、shell 缺口、未解决引用 |
| D3 | published PolicySet / RuntimeProjection 能被 Runtime 消费 | PARTIAL、部分 Path UNRESOLVED；合法空集合也不机械拒绝 |
| O4 | CONFIGURE 完成，DELIVER 所有项已结算，可用 Source 已注册并归入本轮配置 | 部分 Source Need 终态未交付，只要仍有可用监测能力 |
| 启动 | 本轮 revision 被总线和 Runtime 接纳，必要 Worker 与本地依赖就绪 | 无新新闻、W3 未被触发、Supabase 同步积压 |

适配器首先依据产物和契约决定成功，不能机械将异常或原始状态字符串视为失败。无需新增引用齐全、全部 Policy coverage、全部来源健康、全部 shell 成功等全局 gate。若已发布产物不可读、语义身份错误、没有任何可用输入或必需执行能力无法启动，才是真实失败。

沿用节点内既有本地结构修复与隔离；不得在父级再重复完整审查。必要兼容判断限于身份和可消费的数据结构，禁止硬编码某 run 必须搭配另一 run。

## 6. 节点级断点恢复与一次重试

### 6.1 恢复单位

| 板块 | 恢复粒度 |
|---|---|
| D1 | C4 pre-scan、C1、C3、C5、C4 enrichment，以及独立确定性 assemble/publish |
| CDECR | 历史获取/分页 checkpoint、staging、现有持久化 CDECR 执行单元、finalize、Delta 准备；禁止把整个 initialize 作为唯一重试单元 |
| O2 | 实际运行计划中的 survey、各 reconstruction wave、reconciliation、reference review、import/publish 等 phase |
| D2 | O0 各 candidate/review/synthesis/finalization；O1 各 shell 内的 research turn；assemble/publish |
| D3 | Trigger Calibration、Policy Compile、Final Review、publish |
| O4 | CONFIGURE；DELIVER 的 Source Need/candidate/stage checkpoint；最终 settlement/registration |
| 启动 | 激活提交、总线启动、Runtime 游标初始化、Runtime 接纳、readiness 确认 |

实现阶段必须列出由实际代码生成的 node catalog，不能用静态表覆盖动态 shell、wave、Source Need。节点键示例：`d2.o1.shell:<id>.gaps`。父级重试一个板块适配器时，该适配器必须先恢复节点计划，绝不能把调用整个板块解释为重跑整个板块。

### 6.2 执行协议

1. 本地事务记录 node、冻结输入引用、attempt、执行版本、request/execution ID、预算消耗意图，然后 dispatch。
2. 可查询的远端 Worker job ID、thread ID 及时回写；lease 使用递增 fencing token，过期 Worker 不得提交新状态。
3. 单个并行节点完成立即提交成果，不等 gather 全部返回才保存。完成与产物引用在子库内可原子写入时必须原子写入。
4. 父库落后于子库时，恢复先从子库认领成功结果，再补父级状态。父状态本身不是重新调用模型的理由。
5. 模型已写出可用产物但回执未提交：验证该节点的完成 manifest 并补记；成果不足时只重跑这个节点。
6. publish/register/start 等副作用使用稳定幂等键，先查目标事实，再补回执。超时不意味着副作用未发生。
7. 父 Worker 重启后扫描 QUEUED、过期 RUNNING；FAILED 不自动恢复。有效租约或仍存活的远端 job 不得重复 dispatch。

持久卷丢失与进程中断不同：本期恢复保证以 SQLite、产物目录和远端 workspace 仍可访问为前提。通过本地一致性备份恢复灾难损坏；Supabase 摘要不宣称能还原完整工作流。

### 6.3 预算所有权

每个 node 的一个执行 generation：`max_attempts=2`（首次 + 一次失败重试）。节点自身已有的模型失败重试、父重试不得相乘；初始化模式下由统一 NodeAttemptController 计数，适配器内部失败重试交给该控制器。HTTP 短连接重连、无副作用的读重试沿用传输层策略，不新建模型 attempt。

预算必须在重新 dispatch 前持久化。进程崩溃、重新领取任务不会恢复额度；如果原远端 job 仍可查询则继续等待，不消耗新 attempt。无法确认且需要重发模型请求时计一次重试。预算耗尽：标记失败，保存精确节点与继续所需信息，等待人工。

有意分批的 O2 wave / O4 progressive continuation 属于有进展的正常执行计划，不把它们全部挤入两个模型 turn；但错误诱发的重复执行必须计入节点失败预算。O4 已有 candidate 的业务探索/STALLED 规则沿用，不新增外层无限 continuation：预算以稳定 logical node 键计，不能靠新 request_id 逃逸。没有有效 checkpoint、没有业务进展的中断按失败处理。

控制库的 attempt 表示一次逻辑执行尝试，可包含多个正常 continuation turn；这些 turn 的 execution ID、顺序和 checkpoint 由子库记录并关联到同一 attempt。真正失败后的重发才进入第二个 attempt。不得把每个 SDK turn 都映射成父级 attempt，也不得把失败重发伪装成正常 continuation。

人工 `resume` 为失败节点开启新 generation（首次 + 一次重试），历史 generation 保留。只对选中的失败节点授权，不给整个 workflow 所有节点重新发额度。

### 6.4 输入恢复与重跑

普通恢复始终使用该节点原冻结输入；全局 active pointer 变化不影响它。节点执行版本记录代码 commit、workflow contract、model 配置摘要、prompt/skill digest，敏感值不记录。本期不提供热加载入口。

若操作员要求采用新上游，执行显式 `rerun-node` / `rerun-block`，创建新的 execution generation 和新的不可变产物版本。需要新子 run 时允许新子 run，但导入已完成前置节点的引用，不能因此重新调用其模型。旧产物与冻结输入不原地覆盖。

## 7. 产物版本与 Activation Revision

### 7.1 数据关系

`ArtifactRef` 记录 component、ticker、schema、version/artifact_id、source_run_id、hash、locator、published_at、research_cutoff_at。`source_run_id` 是 provenance，不是 ticker 的永久依赖。

`ActivationRevision` 保存 D1、Event Library / KnownEventIndex、D2、D3 PolicySet / RuntimeProjection、O4 configuration revision 的准确引用，以及 base_revision、created_by_operation、reason。`TickerActiveRevision` 在本地控制库中单行指向当前 revision，并用 compare-and-set 防止覆盖并发更新。

候选 artifact 可发布到不可变仓库，但初始化模式下不得直接改变运行系统消费的 active pointer。所有新 Runtime Case 在开始时一次读取 revision，随后固定输入；W3 的 D1/D2 也从同一 Case/revision 解析，不能另查“最新 D2”混入。

现有 `current` 字段保留给旧查询兼容时，明确其为 published head；新 Runtime 不得将它作为激活真相。增加 `publish_candidate` / `activate` 适配层，将现有发布与激活职责分开。

### 7.2 单节点替换不级联

手动生成的新 D1 可以与既有 D2/D3 共存。系统记录它们的 lineage 差异为诊断，不自动判定旧下游失效。操作员可以：

- 只 rerun 一个内部节点，保存候选产物。
- rerun 整个板块 workflow，保存候选产物。
- replace 某组件引用，形成基于当前 revision 的新 revision，其余引用保持不变。
- 显式指定下游节点集合并 invalidate；该操作本身不启动执行。
- 显式执行所选失效节点，或人工发起完整 reinitialize。

替换前只检查实际消费者所需身份与结构，不因 lineage 不同拒绝替换。接口/ID 已完全无法被既有消费者消费时，返回具体错误并保留旧 active revision；不自动修复下游。自动 reconcile 仅指崩溃后事实对账，绝不隐含下游重计算。

### 7.3 回退

运行 ticker 的候选初始化失败不改变旧 active revision。允许显式引用上一版可用组件继续本次初始化；不能静默把“新 D1 执行失败”改成“新 D1 成功”。记录 `reused_from_revision` 和原因。普通 resume 使用既有输入；需要选旧产物时用明确 adopt 操作。

保留上一 active revision 与全部运行中 attempt/case 引用的版本。初版不自动删除业务产物；后续清理必须 reference-aware。rollback 创建激活操作，回到既有 revision，不重新运行模型。

## 8. O4 完成、统一注册与启动

初始化由父编排拥有 O4 触发与总线启动权。D3 初始化发布使用 `enqueue_o4=false` 或等价执行上下文；独立 D3 更新的既有行为保持可用。父级持久化 O4 request 引用，所有 continuation 关联同一初始化与 logical DELIVER。

CONFIGURE 结束仅表示 Plan 已完成。没有新 crawler 时，DELIVER 为 `SUCCEEDED`，标注 NOOP；仍进入统一注册/配置提交。存在新 crawler 时，必须等所有 Source Need 都出现有效 terminal settlement，不能把 INTERRUPTED/PENDING 当成 DEGRADED 成功。

“DELIVER 完成”定义为整份 Plan 已结算，不要求每个 Source Need 都交付成功；这是与用户的 PARTIAL/DEGRADED 非阻塞原则一致的口径。`FAILED/REPLAN_REQUIRED/HUMAN_INTERVENTION_REQUIRED` 的 item 保存诊断，初始化不启动 REPAIR；仍有可用监测组合时继续。若所有可用能力都缺失，则真实失败。未结算 item 按恢复协议继续，失败预算耗尽后人工恢复。

初始化期间 Source/crawler 的全局注册可幂等保存，但 ticker binding 在候选 configuration revision 下不可被 polling 选中；若现有 MCP 操作只能写 active binding，本期需增加候选配置执行上下文。禁止通过直接绕过签名能力写库模拟交付。

统一注册阶段确认成功交付项已完成 working→probe→certification→ACTIVE→source registration→binding 的现有生命周期；复用这些证明，不额外重跑所有探针。默认源和已有源归入同一候选配置。只有 DELIVER 结算完成后才将候选配置一次性用于本 ticker。

O4 `except/finally` 里的 `_start_monitoring()` 在初始化上下文必须关闭；中断 continuation 不得提前启用配置。重初始化时旧配置继续 polling，新配置直到激活才替换，禁止 CONFIGURE 阶段污染旧运行。

## 9. 激活事务、启动握手与游标

SQLite 控制库、总线库、文档库不假装具有跨库事务。使用单一 active revision 加本地 durable activation operation/outbox：

1. REGISTER 成功后，预加载候选 D1/D2、Index、Projection 和配置；检查必要 Worker readiness。此时旧 revision 仍服务。
2. 将 activation operation 及全部目标引用写入控制库；一次本地事务 CAS 切换 active pointer 并写 activation outbox。
3. Bus 与 Runtime 基于相同 revision 处理通知，准备本地缓存并回写 revision ACK。每个新 poll/case 领取任务时读取 active revision；缓存按 revision 失效，不能只依赖旧 300 秒 TTL。
4. 首次初始化由 Bus 接纳 revision 后启动；Runtime 随后初始化消费游标并接纳 ticker。只有两侧 ACK 及 readiness 完成，父级才 SUCCEEDED。
5. 切换后 ACK 丢失或进程崩溃：恢复同一个 activation operation，查询已完成动作并补记，不能创建第二个 revision 或重复清游标。

运行中的旧 poll/case 允许在旧快照完成，新领取任务使用新 revision。原子性指“同一个新 Case 不混版本”，不是停止所有进程后物理同一微秒切换。

若第一次激活提交后关键启动失败，ticker 维持未就绪，初始化 FAILED，等待精确恢复。若替换已运行 ticker 的 revision 后必要能力不可用，则自动回滚 active pointer 至该 operation 的 base_revision，恢复旧配置并记录故障；这仅撤销未成功的激活，不失效或重跑下游节点。回滚也必须幂等，不能覆盖后续人工已生效的 revision。

### 9.1 消费起点

首次 Runtime 激活时，将 `consumer_id + ticker + initial_activation_operation_id` 对应的 tail offset 保存一次；早于该 offset 的总线启动间隙消息明确不处理。记录 `bus_started_at`、`runtime_cursor_initialized_at`、`initial_offset` 及可获得的 skipped_count。

恢复、Worker 重启、revision 更换、人工单节点重跑不得再次 seek-to-tail。已有 ticker 重新初始化继续原消费游标，不能借用户允许首次间隙丢弃而持续丢消息。bootstrap 历史沿用现有抑制规则。

### 9.2 Readiness

默认 heartbeat 每 15 秒写本地，60 秒新鲜窗口；启动握手每次默认最多等待 180 秒，可配置且与模型任务 timeout 分开。窗口耗尽按对应启动节点的一次重试预算处理，不重跑 D1–O4。

检查：Bus Worker heartbeat、至少一个可用启用 binding、ticker revision ACK、调度槽/bootstrap 状态可建立、Runtime Scheduler heartbeat、consumer offset 已初始化、Index/Projection 可加载、W1/W2 执行配置可用、W3 Worker 可接单及必要签名能力有效。无需触发真实 W3；无需等真实新闻出现；某个源暂时 poll 失败只诊断，不要求全部源首轮成功。

W3 readiness 使用现有 worker 能力/配置检查，禁止通过虚构金融事件触发生产 W3。运行时被第二部分有意暂停 polling 时，健康语义将是“已接纳且按计划待机”，不应误判为失败。

## 10. 本地与 Supabase 存储分工

### 10.1 本地是真相源

新增控制 SQLite 建议路径 `<persistent_root>/ticker_initialization/control.sqlite3`，产物继续使用现有各组件仓库与持久卷，不集中复制正文。WAL、busy timeout、短事务；提交窗口不跨 await 网络请求。关键状态采用足够的同步持久性，不依赖内存 flush。备份使用 SQLite backup API 或一致性快照，不只复制活跃 WAL 模式的主 db 文件。

| 数据 | 本地 | Supabase |
|---|---|---|
| initialization/node/attempt、预算、lease、fencing、事件日志 | 完整真相 | 仅低频任务/板块结果摘要 |
| D1/D2/D3、Event Library、Index、Projection、O4 Plan/Settlement 正文 | 现有仓库完整保留 | 本期新同步不上传正文 |
| 原始消息、Case、prompt、skill、trace、模型输出、checkpoint | 完整保留 | 不同步 |
| Activation Revision | 完整组件引用与激活记录 | 紧凑 manifest：ID、version、hash、时间、状态 |
| ticker 运行概况、最近失败节点、诊断计数 | 完整 | 低频汇总 |
| heartbeat、poll state、游标、锁、重试等待 | 高频本地 | 不逐条同步 |
| 人工操作审计 | 完整 actor/reason/目标/前后版本 | 精简操作记录，不带正文 |

Supabase 是异步状态投影，不能成为初始化下一步的前置条件。断网、限额、同步失败仅记录 `cloud_sync_lag`，本地执行照常进行。远端摘要不能作为机器迁移时的恢复 checkpoint。

### 10.2 建议本地表

| 表 | 主要字段 / 约束 |
|---|---|
| initialization_runs | initialization_id、ticker、workflow=V2、cutoff、status、phase、base_revision、timestamps、last_error、state_seq |
| ticker_operations | ticker 唯一 active 占位、operation_id、kind、owner、lease_until、fencing_token；覆盖 initialize/rerun/replace/rollback |
| initialization_nodes | run_id、node_key、parent_key、dependencies、child_run_id、generation、status、input/output refs、checkpoint locator；唯一 run/node/generation |
| initialization_attempts | attempt_id、node/generation、ordinal、execution_id、thread/job ref、input digest、execution version、status、error；ordinal 最多 2 |
| initialization_events | event_id、run_id、seq、node_key、type、bounded payload；顺序用于审计和状态投影 |
| activation_revisions | revision_id、ticker、base_revision、manifest refs、created_by、created_at |
| ticker_active_revision | ticker 主键、revision_id、state_seq |
| activation_operations | operation_id、target/base revision、step receipts、bus/runtime ACK、status |
| initialization_outbox | event_id、destination、aggregate_key、state_seq、payload summary、attempt/next_send/ack |

重复提交检查和占位写入必须同一事务。已 FAILED 且执行收敛后可人工 resume；resume 也先获取 ticker operation，不能与另一 active 初始化并发。`DUPLICATE_ACTIVE_INITIALIZATION` 返回已有 ID 供排查，但不把请求附着为成功。已 SUCCEEDED ticker 普通 submit 返回 `ALREADY_INITIALIZED`；完整重做必须调用 reinitialize。

### 10.3 Supabase 投影

新增或复用私有 schema 下的 `ticker_initialization_summary`、`ticker_activation_summary`、`ticker_operation_audit`。初版不暴露匿名或前端 Data API；按既有服务端连接方式写入。迁移按仓库已有迁移流程生成，部署前检查权限/RLS，不在本方案阶段执行远端迁移。

同步默认每 30 秒合并发送，每批最多 100 条；状态终结时立即尝试 flush，但本地结果不等远端 ACK。正常摘要单条目标 ≤8 KiB，超限截断诊断并保留本地引用；不是阻断业务的限制。按 `(aggregate_id,state_seq)` 防止乱序旧状态覆盖新状态，upsert 仅返回最少确认字段。上传并行 node 的细节仅汇总数量/当前键，不逐 token、逐日志同步。

发布 manifest 和人工操作低频同步；Runtime 不按 Case 从 Supabase 下载文档。已有 Hybrid / Postgres 路径不做全库搬迁，但必须检查本次接线是否重新引入远端全文读取；将新初始化产物预热到本地，消费者按 revision 从本地读取。云端无正文不妨碍本地主机重启恢复。

Supabase 官方 egress 文档说明数据库取回数据以及其他服务输出均会产生出站流量，并建议减少查询字段/频次、缓存和避免写入后返回整行。上述策略据此制定，不依赖套餐价格或额度假设：[Manage Egress usage](https://supabase.com/docs/guides/platform/manage-your-usage/egress)。本轮 changelog Markdown 地址未能被浏览器解析；本期仅制定存储边界，没有依赖新 SDK/数据库功能，实施迁移时需重新核对当前文档。

## 11. CLI 与人工操作合同

以下是拟新增命令，不代表当前已可执行；最终挂入现有 `doxagent` CLI。

```text
doxagent ticker-init submit --ticker MU --research-cutoff-at <UTC>
doxagent ticker-init status --initialization-id <id> [--json]
doxagent ticker-init inspect-node --initialization-id <id> --node <key>
doxagent ticker-init resume --initialization-id <id> [--node <failed-key>] --reason <text>
doxagent ticker-init rerun-node --ticker MU --node <key> --from <execution-ref> --reason <text>
doxagent ticker-init rerun-block --ticker MU --block <D1|CDECR|O2|D2|D3|O4> --reason <text>
doxagent ticker-init adopt-artifact --initialization-id <id> --component <name> --artifact <ref> --reason <text>
doxagent ticker-init activate --ticker MU --candidate <revision-id> --reason <text>
doxagent ticker-init replace-artifact --ticker MU --component <name> --artifact <ref> --reason <text>
doxagent ticker-init invalidate --execution <ref> --nodes <explicit-node-keys> --reason <text>
doxagent ticker-init reinitialize --ticker MU --research-cutoff-at <UTC> --reason <text>
doxagent ticker-init rollback --ticker MU --revision <id> --reason <text>
doxagent ticker-init worker
```

`rerun-node` 不自动跑 successor；若修改的只是内部产物，最终板块发布需操作员显式执行 finalize 或 `rerun-block`。`rerun-block` 仅运行该板块的新 generation，不自动推进其他板块。两者默认产出候选，`activate`/`replace-artifact` 才影响线上。

`adopt-artifact` 明确替代本次初始化某板块成果后，依赖链中尚未开始的下一板块可按正常初始化继续；已完成下游不自动清除。`invalidate` 只改变明确列出的节点，CLI 必须列出目标，不提供默认隐式 cascade。`reinitialize` 创建完整新初始化，保留旧运行，复用规则由新 operation 明确指定，不能偷偷退化为旧任务 resume。

CLI JSON 输出含 schema_version、initialization_id、status、phase、current_nodes、failed_node、attempt_count、manual_resume_required、active/candidate revision、quality_annotations、cloud_sync_lag。普通重复请求退出非零并给稳定错误码；read-only status 不改变任务。

## 12. 实施任务拆分

### P1：控制模型与本地执行骨架

- 新增 schema/repository/service/worker/cli，落实 ticker 唯一占位、lease/fencing、一次重试、事件/outbox、状态查询。
- 定义 Adapter 接口：`plan / inspect / execute_or_resume / reconcile / artifact_refs`。结果只能 SUCCEEDED/FAILED，执行中通过持久状态表达。
- 父库只保存恢复引用；与子库对账规则先用 stub 验证。定义实际内部 node catalog，核对 CDECR 的最小可恢复单位。

完成条件：重复请求无效；杀 Worker 后复用 identity/预算；失败只恢复精确 node。

### P2：D1 / CDECR / O2 / D2 / D3 接线

- 保留现有 DAG 和冻结输入，父级记录子 run / epoch / wave / shell；D1+CDECR 后 O2。
- 修正 D2 PARTIAL 恢复行为；检查并行成功节点即时落库；D3 compile 独立恢复。
- 拆分 candidate publish 与 active 选择；不重复节点内部质量检查。
- 统一 cutoff 语义：D1 发布晚于 cutoff 只影响 published_at，不扩大研究证据窗口。

完成条件：真实适配器+假模型完整跑到 D3；每个边界中断后成功节点模型调用数不增加。

### P3：O4 DELIVER 结算与配置候选

- 新增 initialization 执行上下文，D3 不再重复 enqueue；父级持有 O4 request/continuation。
- 禁止 initialization 的异常/finally 启动总线；终态 settlement 后统一注册。
- 候选 binding 不参与 polling；旧 ticker configuration 在重初始化期间保持服务。
- Source Need 进展恢复、预算、signed capability 生命周期接通；过期 capability 可按原授权范围重新签发，不扩大权限。

完成条件：DELIVER 中断时新 ticker 无 polling；重初始化时旧配置仍工作；已完成 item 不重复交付。

### P4：Activation / Message Bus / Runtime V2

- 实施候选预加载、active revision CAS、durable activation receipts、revision ACK。
- 新入口绕过旧 WorkflowDocumentProvider 自动初始化；Runtime 输入/W3 上下文统一按 revision。
- 首次 tail 初始化只一次，后续恢复/替换保留 offset；readiness 与 heartbeat 本地化。
- 新增 initialization Worker compose service，持久卷挂载齐全；应用 readiness 不只依赖 compose 启动顺序。

完成条件：无模型重跑完成启动恢复；旧 Case 固定版本、新 Case 使用新 revision；failed refresh 仍保持旧 ticker。

### P5：人工操作、Supabase 摘要与文档

- 完成 rerun-node/block、adopt/replace、显式 invalidate、reinitialize、rollback；操作审计与 ticker 互斥。
- outbox 异步汇总，远端低频 schema/migration；本地流量指标统计请求数、字节数、积压。
- 编写从 CLI 首次初始化、节点失败恢复、O4 续作、激活故障、云端离线到恢复的运行手册。
- 每次重要代码提交追加仓库 `changelog`。

完成条件：Supabase 完全不可用时仍能全链成功；手动替换不触发任何未指定下游节点。

### P6：整体故障注入与真实验收

- 完成下节矩阵；代码质量检查按项目现有 Ruff/mypy/pytest 规则执行。
- 先进行 stub/deterministic 全 DAG，再执行一个隔离美股 ticker 的真实完整初始化，记录模型成本与耗时。
- 真实验收使用已配置且获准的数据/provider 范围；不得把已有线上 ticker 当作破坏性恢复试验对象。
- 本方案不执行真实付费模型或远端部署；实施阶段的验收需明确标记通过层级，不以单元测试替代真实链路证明。

## 13. 必须通过的验收矩阵

| 场景 | 必须观察到的结果 |
|---|---|
| 正常全链 | D1/CDECR 并行；O2 等两者；O4 完成后启动；最终 revision 与 ACK 一致 |
| 每板块返回 PARTIAL/DEGRADED | 可用交接物存在时继续；无额外质量 gate / retry |
| D3 Compile 首次失败 | 只自动重试 Compile 一次；Stage A 模型调用次数不变 |
| 同节点第二次失败 | FAILED + manual_resume_required；Worker 重启不自动恢复预算 |
| 人工 resume | 同 initialization；已成功节点保留；仅失败节点新 generation |
| 任意并行节点完成后进程被杀 | 已完成 sibling 不重调；未完成节点恢复 |
| 模型完成、checkpoint 未提交 | 从可用产物认领成功，或仅该节点受预算约束重试 |
| 远端 job 仍执行时父进程重启 | 查询/重新附着原 job，不重复 dispatch |
| artifact 已发布、父状态未写 | 查 immutable identity，补回执，无重复 publication |
| O2 wave / D2 shell 恢复 | 只继续失败 phase/turn，保留成功 wave/shell |
| O4 DELIVER 部分进行中 | 新 ticker 不启动；重初始化旧 ticker 不受影响 |
| O4 全部结算但部分失败 | 有可用组合则继续，保留缺口；不调用 REPAIR |
| CONFIGURE/注册过程中崩溃 | 恢复相同 Plan，已注册项幂等，未激活配置不 polling |
| 指针提交后、ACK 前崩溃 | 同 operation 补 ACK；必要失败回滚旧 revision；无重跑模型 |
| Runtime offset 写入后崩溃 | 恢复原 offset，不再次 tail；首次间隙可丢弃有记录 |
| 上游手动替换 | 下游状态、调用次数保持不变；新 revision 只替换指定引用 |
| 单内部节点重跑 | 只选中节点执行，产物候选化，无隐式 successor |
| 两次并发 submit | 仅一次接纳，另一稳定返回 DUPLICATE_ACTIVE_INITIALIZATION |
| 已运行 ticker 全量重初始化失败 | 旧 revision/consumer/配置继续工作 |
| Supabase 断网/乱序同步 | 本地全链不阻断；恢复同步后摘要不回退；无大 payload |
| W3 无消息触发 | readiness 足以成功；不产生假 Case/Trade |
| cutoff 跨天或 ET DST 边界 | research cutoff 固定；semantic day 独立正确 |
| 旧 lease Worker 延迟回写 | fencing 拒绝旧 owner 提交；不覆盖新执行 |

故障注入至少覆盖 dispatch 前后、产物提交前后、父状态确认前后、publication/registration/activation/offset 写入前后。保存每个节点的模型调用计数及副作用幂等键，证明没有整链重跑。测试不能只验证 mock 最终返回 SUCCEEDED。

## 14. 完成定义与实现约束

完成时应具备：一条 CLI 从零启动 ticker；一个持久化后台 Worker；可查看内部节点级进度；任何节点失败后可精确恢复；O4 DELIVER 后统一启动；Runtime V2 接纳同一激活版本；人工替换与重跑不级联；Supabase 故障不影响本地运行；可验证的真实全链记录。

本方案采用现有组件、SQLite、稳定 ID、少量适配器、激活指针与 outbox 即可落地。无需引入通用 workflow 引擎或新一层全局质量审核。后续第二部分只需基于这里的 active revision、runtime admission 与持久状态接入时间编排。
