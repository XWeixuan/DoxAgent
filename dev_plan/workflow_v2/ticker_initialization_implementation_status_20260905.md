# Ticker initialization V2 实施记录

日期：2026-09-05

依据：`ticker_initialization_orchestration_plan_20260905.md`。初始化开发阶段只使用本地隔离数据库与 fake model/worker；不访问真实网站、不调用真实模型、不部署。后续已获明确授权，于 2026-09-07 应用并核验远端 Supabase 摘要迁移；这不构成真实 ticker 或模型验收。

## 当前交付状态（取代下方历史批次的待办描述）

第一部分 ticker 初始化的生产接线、控制入口、人工恢复和本地验证已落地。第二部分持续运行编排重构、前端/公共 API、代码热替换、真实验收与部署均不在本次交付范围。下方原有批次记录保留作为开发过程留痕，其“尚未完成/暂停”不是当前待办。

| 开发项 | 当前实现与验证 |
|---|---|
| 默认生产编排 | CLI 默认 12 阶段，D1 与 CDECR 并行，O2 等新 D1，D2→D3→O4 CONFIGURE/全部 DELIVER→注册→激活→Bus/Runtime 接纳；PARTIAL/DEGRADED 不驱动流程 |
| 持久化控制与恢复 | SQLite WAL/FULL、排他 operation、fencing、冻结输入与执行摘要、动态内部节点、每代一次失败重试、精确人工 resume、父子预算不相乘 |
| 研究工作流接线 | D1/D2/D3/O2 真实 runner 的内部 durable 边界；D2 并行成果立即 checkpoint；D3 workspace 校验后结算并发布 candidate；cutoff 不随发布时间漂移 |
| CDECR 断点 | 非阻塞历史分页缓存与可用源隔离、staging 去重恢复、租约约束子进程、原生任务/epoch 认领、Bulk Task 统一预算、有效空库/NOOP 交接 |
| 人工修改 | resume、rerun-node、rerun-block、adopt-artifact、invalidate、activate、replace-artifact、rollback、reinitialize；不自动失效后继或更改活跃版本 |
| 内部 agent 节点独立重跑 | 签名不可变 PRE-NODE workspace 快照、类型化调用冻结、安全 fork，新 workspace 只执行选中 turn，输出候选；真实 D3 Compile 的离线测试确认无 Calibration/Review 重跑、原目录不变 |
| O4 与启动 | 候选配置隔离、禁止初始化 REPAIR/早启 Bus、跨库幂等安装、revision CAS、消费者真实循环 ACK、独立心跳、替换失败回滚后精确恢复 |
| Runtime 交接 | 默认 V2/TRADING；首次 cursor 只初始化一次；新 Case 读取一组 active refs，旧 Case 保留原 refs；W3 只做 readiness |
| 本地/云端 | 完整成果/日志/快照仅本地；14 字段上限的低频合并摘要 RPC、云故障不阻断；RLS/最小权限迁移及本地 PGlite 检查 |
| 运维 | opt-in compose overlay、共享持久卷配置、SIGTERM/资源关闭、明确状态机与完整人工操作手册 |

使用说明见 [`ticker_initialization_operations.md`](ticker_initialization_operations.md)。内部 agent turn 的主动重跑要求本次新增的 PRE-NODE 快照；历史原生 CDECR/抓取单元通过 checkpoint 精确恢复失败，主动重建使用 CDECR 板块入口，不伪造旧运行缺失的快照。

### 最终验证

- 全部 ticker initialization 测试与关联 D1/D2/D3/O2/O4、Event Library、CDECR、Message Bus、Runtime/Scheduler 组件回归统一使用 `--offline` 防护：**265 passed，3 个依赖包警告，206.32 秒**（同一次完整定向回归，不累加历史批次）。
- 默认 12 阶段故障矩阵覆盖 24 种逐阶段失败/崩溃恢复情景；生产组件另覆盖内部 Compile 快照重跑与真实 SQLite 启动握手、替换回退、原 revision 恢复。
- 本地 PGlite 验证迁移可重放、摘要乱序不回退、权限隔离及合法状态；不是远端 Supabase 验收。Compose 仅静态解析。
- 修改范围 Ruff、22 个源文件目标 mypy、`git diff --check` 通过。
- 未运行真实 ticker/模型验收或部署。远端摘要迁移已应用并核验；离线证据不冒充生产发布批准。

## 历史开发批次（仅留痕）

## 已落地的第一批代码

- 新增 `src/doxagent/ticker_initialization/`：SQLite 控制库、节点/attempt 模型、带 fencing 的租约、并行依赖调度、动态节点展开、节点级一次自动失败重试、人工 resume、局部 rerun、不可变 revision/CAS 原语、摘要 outbox 与异步 sink 接口。
- 成功节点立即持久化；崩溃恢复先 reconcile；预算跨进程重启保留，第二次失败只允许人工恢复。原 Worker 失去租约后无法提交。
- 单节点/板块 rerun 创建新任务，冻结不在重跑范围内的依赖，保留原任务及其 attempt 历史，不自动执行后继节点。
- 提供 `doxagent-ticker-init` CLI。当前 submit **必须传入显式 node plan**，worker **必须传入 adapter factory**。这是控制层开发入口，尚未注册完整生产初始化适配器，不能当作已经接通的生产入口使用。
- D2 新增请求级 `reuse_published_partial`；初始化 pinned runner 开启，既有独立 PARTIAL shell 修复行为保持可用。
- D3 initialize 新增 `enqueue_o4` 控制权参数，供父编排接管；默认保持独立工作流行为。
- O4 request 新增持久化 `initialization_id`，传递至 DELIVER 和 continuation；此上下文禁止 REPAIR，且成功、降级、异常退出均不自动启动总线。
- Message Bus 首次 runtime tail offset 与 initialization marker 同事务保存。已有 consumer offset 被视为旧版本部分提交的回执，不再次 seek-to-tail。

## 离线使用方式

```text
uv run doxagent-ticker-init --help
uv run doxagent-ticker-init --database <isolated-control.db> submit --ticker MU --research-cutoff-at 2026-09-05T00:00:00+00:00 --plan <explicit-node-plan.json>
uv run doxagent-ticker-init --database <isolated-control.db> status --initialization-id <id>
uv run doxagent-ticker-init --database <isolated-control.db> resume --initialization-id <id> --node <failed-node-key> --reason <reason>
uv run doxagent-ticker-init --database <isolated-control.db> rerun-node --initialization-id <id> --node <node-key> --reason <reason>
```

适配器约定：`reconcile(context)` 认领已完成成果，或等待既有 job；确无成果返回 None。`execute(context)` 执行一次逻辑尝试；通过 `context.checkpoint()` 及时记录 job/thread/checkpoint 引用。返回 NodeResult 表示产物可用，quality_annotations 不驱动 DAG。输出正文和输入只写本地；云端 sink 仅接收字段白名单摘要。

## 第二批：配置隔离、dispatch 恢复与运行输入接线

- 新增候选监测配置库：仅复制配置，不复制 raw/stream/offset；默认 binding 在不启动 ticker 的情况下生成。安装保留消息/游标/其他 ticker，binding 历史版本不覆盖；安装回执与 ticker 配置同事务，补偿回退不会覆盖更新的安装。此模块尚待父级 REGISTER/Activation 节点接线，不能独立作为完整激活事务使用。
- O4 capability、WorkerRunRequest、SDK、MCP 贯通签名 initialization_id；候选上下文改变必须重新打开 MCP 会话，不能在同一服务器线程切换配置库。初始化 workspace/thread 和 O4 SQLite 按初始化 ID 隔离，生产 O4 factory 增加候选构造参数。
- CONFIGURE 完成后仅持久化并入队 DELIVER，父级可分别跟踪两节点；既有 checkpoint 不被重置。普通 O4 Worker 不获取或恢复初始化所属请求；初始化 DELIVER 异常没有有效 settlement 时返回 FAILED，不再伪装 DEGRADED 成功。
- Worker 新增可选幂等 dispatch identity 与请求 hash：同 identity 相同输入重附原 job，不同输入报冲突；O4 在 HTTP 前冻结完整 dispatch。初始化客户端取消等待不取消远端幂等 job；Worker 本身重启的 orphan job 仍按既有规则记 FAILED，不假装恢复了已经消失的 SDK 进程。
- D2 并行 candidate/review 成功后立即保存 checkpoint，新增取消父进程后不重复调用已成功 C1 candidate 的测试。研究 cutoff 不再被 D1 published_at 向后抬升；显式固定版本的 O2 Reference View 不因编译发布时间晚于 cutoff 而丢弃，未固定版本的旧入口保留时间过滤。
- Runtime 新增一次读取 active revision 后解析整组输入的本地 loader，并将 activation/D1/D2 引用持久化到 Case。W3 优先读取 Case 的显式 D1/D2 引用，不强迫 D1 与 D2 历史 lineage 绑定；未初始化管理的旧入口保留兼容行为。
- 设置 `DOXAGENT_TICKER_INITIALIZATION_CONTROL_PATH` 后，Runtime factory 使用本地文档/Policy 仓库，不在热路径回读远端全文。初始化产物仍须由后续生产适配器完整留存本地，设置该参数不是迁移或预热功能。
- Runtime Scheduler 新增 `admit_activation`，接纳已加载的同版本 Index/Projection，保留一次性 cursor，并绕过 legacy DocumentProvider 与 weekly 自动重初始化。这个入口不代替真实 Scheduler/Bus Worker 的 heartbeat/ACK，启动握手仍未完成。

## 尚未完成，不应宣称端到端可用

### 第四批进行中：内部执行账本与研究桥接

- D1/D2/D3 的真实 turn runner 已接入内部节点账本；O2 phase/wave 已抽出相同 durable 边界。默认独立工作流不启用该账本；只有父初始化执行上下文启用。每个内部节点最多两次逻辑尝试，父容器继续遍历不重置内部预算。
- D3 小结果先落 receipt，Stage-A/Compile/Review 产物检查完成后才结算内部成功；语义失败不会一直复用失败前的小结果。新增取消点与多阶段分别失败的离线测试。
- O4 configure/deliver/register 的跨初始化单节点 rerun 可以导入原候选配置、Plan、checkpoint 和终态 settlement，冻结外部依赖且不自动运行后继；原候选和 live ticker 不被导入动作启动。
- 新增 research_adapter 生产桥接代码，使用本地 SQLite 和显式产物引用，尚未注册为完整默认生产 CLI。D3 builder 允许调用方持有并关闭 Worker client；初始化大于 2 MB 的发布正文留在本地，不要求上传 Supabase。
- CDECR 原同步执行会阻塞父事件循环。新增可注入 executor 与租约约束子进程：父取消终止子进程，孤儿子进程检查 fencing，OS registry 锁防止旧新执行重叠。离线测试仅调用 fake runner/模拟子进程，不启动真实 CDECR。
- SQLite D3 增加按 run 持久化的版本号预留和不修改 current 的 candidate publication；普通发布跳过已预留版本，避免候选与普通发布撞号。research adapter 使用 candidate 模式，发布已提交但父结果未提交时可认领候选。候选/并发版本预留及 D3 既有流程 27 项离线测试通过。
- 扩大组件回归得到 138 passed / 1 failed；失败来自旧测试 20 ms 租约在 SQLite 初始化耗时下提前过期，并非生产逻辑错误。已把这两个故障注入测试改为可控时钟，随后控制层/内部节点恢复/CDECR 子进程/O4 定向 29 项通过。批次有重叠，不累加为全量结果。
- 此批仍在开发：默认 DAG/生产入口、activation 跨库补偿与实际 Worker ACK、完整人工 CLI、Supabase 摘要/迁移、部署和全链测试仍需完成。D1/D2/D3 动态内部节点的独立 rerun 也不能据此视为已支持；当前明确拒绝未注册的内部节点独立 rerun，避免过滤掉子节点后错误宣称任务成功，失败子节点的 resume 已支持。

### 第三批：解除离线验证阻塞，接通 O4 父节点

用户已批准先隔离旧测试后继续开发，本轮已解除上一批暂停：

- 旧 Scheduler 测试的三处 runtime 构造改为显式本地仓库和四个离线 Worker，不再从生产 settings 延迟构造 A2/O3；保留对真实 Scheduler/Repository/路由代码的验证。
- 增加 pytest `--offline` 模式：禁止真实 HTTP transport、外部 socket/DNS 和生产 Codex SDK start。网络异常即使被业务代码吞掉，也在 fixture teardown 记录为测试失败；不输出地址、凭证或 payload。Windows asyncio 自用 loopback socket pair 保留，MockTransport/fake worker 不受影响。此模式不是 OS 级沙箱，不用于执行不可信测试脚本。
- 新增 `ticker_initialization/o4_adapter.py` 生产 O4 父级适配器：`o4.configure → o4.deliver → o4.register`。CONFIGURE 从 `policy_set_path` / `document2_path` 的本地发布产物读取输入，持久化 child request 和 Plan 引用；DELIVER 执行一次逻辑 turn，父级持有跨重启的一次失败重试预算，续作继承既有 checkpoint。
- CONFIGURE/DELIVER dispatch 使用 execution identity；子成果已提交、父级结果未提交时恢复原 request，不新开模型任务。人工 resume 仅重试失败的 DELIVER，已成功 CONFIGURE 不执行；DEGRADED 的已结算成果正常前进。
- 补齐 O4 settlement 的终态校验：PENDING/IN_PROGRESS 以及重复 Source Need 不能伪装成终态结算。候选注册只检查存在可用已注册组合，不启动 REPAIR，不重跑生命周期探针，不安装至 live bus，不提前 polling。真正的配置生效仍由后续 Activation/Bus 接管完成。
- 可用适配器 factory 为 `doxagent.ticker_initialization.o4_adapter:adapter_factory`，仅负责上述三个节点，不是全 ticker 初始化 factory。普通单节点/板块跨初始化 rerun 的候选导入仍待接线；当前测试覆盖同 initialization 的 resume。
- 在显式 `--offline` 下，整组组件回归通过 176 项；随后新增候选注册并强化 SDK/DNS 阻断后，O4 父节点 + 旧 Scheduler 定向回归 24 项通过（与前组有重叠，不累加）。当前修改 Ruff、11 个源文件目标 mypy、git diff --check 通过。未执行真实 ticker 验收、远端迁移或部署。

### 本轮验证与暂停原因

以下为上一批历史记录；暂停现已解除，不代表当前仍在等待授权。

- D2/O4/控制层定向回归曾通过 60 项；候选配置与幂等 job 新测试通过 10 项。后续增加的版本读取、Scheduler 接纳、D2 并行取消恢复也进行了定向验证。上述批次存在修改时点差异，不合计为最终全量通过数。
- 修改源代码 Ruff、21 个源文件目标 mypy、CLI help、git diff --check 通过。
- 最后扩大的组件回归包含旧 `tests/test_phase25_runtime_scheduler.py`，执行中发现其 `_scheduler()` 只注入 Heuristic W1/W2，`PersistentRuntimeExecutionService.from_settings()` 仍默认创建 LazyAgentRunnerA2Worker/LazyAgentRunnerO3Worker。因此不能保证该测试组为纯离线，已中止整个回归进程，未取得最终通过结果；不能确认中止前是否产生外部调用。
- 按用户异常熔断要求暂停进一步开发。继续前需先将旧测试的 A2/O3 及外部依赖完全隔离，再补齐本轮最终回归。没有执行专门的真实 ticker 验收、远端迁移或部署；不能将此说明理解为已证明上述旧测试零外部调用。

本批验证记录：控制层及 O4/D2/D3/Message Bus 组件回归 98 passed；新增重跑、动态节点、云端离线与 CLI 测试后，控制层及 D3 enqueue 定向验证 18 passed（与前一组有重叠，不累加计数）。修改范围 Ruff 通过，10 个源文件目标 mypy 通过，CLI help 与 git diff --check 通过。未执行真实验收。

1. D1/CDECR/O2/D2/D3 生产适配器及实际 node catalog 全量接线；统一原有内部重试预算，避免父子重试相乘；CDECR 全量重初始化身份隔离。
2. D1 可用交接物失败判定、D3 candidate publication 与激活拆分；D2 cutoff 与并行 checkpoint 已补齐，仍需全链故障矩阵。
3. O4 CONFIGURE/DELIVER/候选注册父级适配器及同 initialization 的重试/恢复已接通；仍需接入完整默认 DAG、补齐跨初始化 rerun 的候选导入，并与 activation 统一提交衔接，尚未具备可上线的全 ticker 重初始化链路。
4. Bus 按 Activation Revision 接纳、启动 ACK/heartbeat、跨库 durable activation operation 与自动补偿编排。Runtime/W3 的同版本输入读取及 Scheduler 接纳已接线，配置库局部回退不是完整跨库原子激活。
5. adopt/replace/invalidate/activate/rollback 的完整人工 CLI；当前只有 resume/rerun/reinitialize 控制任务入口。
6. Supabase 具体摘要 sink 和迁移、低频定时 flush、Worker compose 部署与资源关闭集成；当前 outbox/sink 接口不访问远端。
7. 真实生产适配器 + fake worker 的完整故障矩阵与最终运行手册。真实验收按用户要求不执行。

后续继续开发应完成第 1 项的内部节点接线与统一预算，再将本批 O4/runtime 入口接入父级；不得以粗粒度板块重试代替内部节点恢复。沿用现有控制层，不重复创建另一套父编排。现有测试不证明全链生产初始化、原子激活或完整 P1–P6 已完成。
