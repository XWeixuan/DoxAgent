# DoxAgent V2 Ticker 初始化守护与自动修复：代码勘察事实包

> 勘察日期：2026-09-20  
> 仓库：`C:\Users\WEIXUANXIE\Desktop\DoxAgent`  
> 基线提交：`d7d1c24c`（`main`，与 `origin/main` 同一提交）  
> 文档性质：当前实现事实与约束记录；**不是开发计划，不包含实施阶段、任务拆分、排期或技术选型结论**。

## 1. 勘察边界与证据口径

本事实包针对需求基线中的以下问题核对当前代码：

- ticker initialization 的真实入口、DAG、Worker 与 Adapter 调用链；
- durable run / node / attempt / receipt / event / lease / resume 语义；
- 正式 `FAILED` 的形成条件和失败信息保存位置；
- 主初始化 Worker 的领取、并发和执行权约束；
- V2 API、Control、Read Projection 与前端人工恢复链路；
- 现有 Codex Worker 的 thread/workspace/隔离能力；
- 当前 Docker 镜像、Compose 服务和持久卷边界；
- Guardian、Repair Incident、Git worktree、Repair Runtime 是否已经存在。

本轮只检查本地代码、Git 历史、已有文档和本地定向测试；未连接远端服务器，未检查远端 Compose 实例、远端数据库或正在运行的真实 initialization，也未调用真实模型/provider。

仓库在勘察开始前已有一个与本轮无关的未提交文件：`rklb_real_test_issue.md`。本轮未修改该文件。

## 2. 结论摘要

1. 当前 ticker initialization 已经是一条持久化的 V2 DAG，不是一次性脚本。它具备 SQLite 真相源、run/node/attempt/event、节点 receipt、动态内部节点、租约与 fencing、两次自动尝试预算、崩溃后 reconcile、正式失败后的同 ID resume，以及人工 API/CLI 恢复入口。
2. 正式 `FAILED` 后，原 run 的 `initialization_id`、成功节点结果、历史 attempts 和子工作流持久化状态都保留；`resume()` 只把选中的失败节点开启新 generation，并重新把同一 run 放回队列。现有测试明确验证已成功节点不会重跑。
3. 当前实现中**没有** Ticker Initialization Guardian、Repair Incident、自动修复状态机、Git repair branch/worktree 管理、Repair 次数上限、`HUMAN_REQUIRED` 状态或一次性 Repair Container 生命周期管理。
4. 当前 Codex Worker 支持创建/续接 Codex thread，也有持久化 run workspace 和受控文件写入；但它是研究工作流执行器，输入模型是 research node/agent role/output schema，工作区不是 DoxAgent Git checkout，没有现成的代码修复 incident 契约。
5. 当前生产镜像是不可变应用镜像：Docker build 明确排除 `.git`，只复制应用源码/提示词/部分契约；Compose 只挂载 `/data` 持久卷，没有正式宿主仓库挂载，也没有 Docker socket。因此现有服务容器内没有可直接创建 Git worktree、构建修复镜像或启动 sibling Repair Container 的已接线能力。
6. 当前“同一 initialization 的独占执行”依赖 `ticker_operations` 的 ticker 唯一占位、递增 token lease 和 fencing。正式失败时该占位被删除；`resume()` 会重新插入占位。可是恢复后，主 Worker 会立即把它视为普通 `QUEUED` 工作，当前 run/operation schema 没有 `execution_lane`、repair owner 或指定 Worker 字段。
7. `InitializationRepository.claim()` 虽然有可选 `permit(initialization_id)` 参数，但现有 `InitializationWorker.run_once()` 没有传入 permit；CLI 也没有“只执行指定 initialization”的 worker 参数。生产 CLI Worker还持有整个初始化数据库目录上的进程级 `WriterLock`，同一共享卷上的第二个 CLI initialization worker 会因锁冲突退出。
8. 因此，需求基线所依赖的“原 initialization 可精确恢复”已经成立；但“失败自动发现 → 隔离代码修复 → 修复代码专属执行权 → 临时容器 resume → 同 incident 复用 thread/worktree”整条外围控制链目前不存在。

## 3. 当前整体调用链

### 3.1 V2 页面发起初始化或人工恢复

```text
Overview 前端
  -> Operations.submit()
  -> POST /api/doxagent/v2/tickers/{ticker}/.../resume
  -> API ControlRepository.submit(RESUME_INITIALIZATION)
  -> v2-control Worker 轮询 pending operation
  -> ControlService.step()
  -> InitializationRepository.resume(original initialization_id)
  -> ticker_operations 重新插入同一 run
  -> 主 v2-initialization Worker claim
  -> InitializationWorker._drive()
  -> 失败节点的新 generation 继续执行
```

证据：

- 前端仅在 `FAILED && manual_resume_allowed` 时显示“重试失败步骤”，提交 `RESUME_INITIALIZATION`：`frontend/v2/src/pages/overview/initialization.tsx:59-85`。
- 前端恢复请求使用原 `initialization_id`，带 control ETag 和 idempotency key：`frontend/v2/src/core/operations.ts:160-205`。
- API 端点只接受空 body，并创建 Control operation：`src/doxagent/api_v2/app.py:441-453`。
- Control 层验证目标 initialization 必须等于 ticker state 当前记录的 ID，且当前必须是 failed：`src/doxagent/v2_control/repository.py:196-258`。
- `ControlService.step()` 最终调用 `InitializationRepository.resume()`，然后把操作结算为 `INITIALIZATION_RESUMED`：`src/doxagent/v2_control/service.py:121-133`、`src/doxagent/v2_control/repository.py:377-387`。

### 3.2 正常初始化提交与 Worker 执行

```text
START / FORCE_INITIALIZE
  -> v2-control ControlService
  -> InitializationRepository.submit(default_plan)
  -> initialization_runs / initialization_nodes / ticker_operations
  -> v2-initialization service
  -> CLI _run_parallel_loop (默认 16 个 async InitializationWorker)
  -> InitializationRepository.claim()
  -> InitializationWorker._drive()
  -> adapter_factory(node)
       d1/cdecr/o2/d2/d3 -> ResearchInitializationAdapter
       o4.*              -> O4InitializationAdapter
       activation/bus/runtime -> ActivationAdapter
  -> 每个 node 先 reconcile，再决定 execute
  -> complete/fail 持久化
  -> 全部完成后 finish(SUCCEEDED)，预算耗尽后 finish(FAILED)
```

证据：

- V2 Control 创建 default plan 并提交 run：`src/doxagent/v2_control/service.py:137-193`。
- CLI 启动多个 async worker，默认并发来自 `DOXAGENT_TICKER_INITIALIZATION_CONCURRENCY`，代码默认值为 16：`src/doxagent/ticker_initialization/cli.py:67-83`、`src/doxagent/settings.py:47-49`。
- 每个 worker 从 `claim()` 获得 lease，然后由 heartbeat 续租：`src/doxagent/ticker_initialization/service.py:50-92`。
- 每个节点先调用 adapter `reconcile()`；没有可认领结果时才 `execute()`：`src/doxagent/ticker_initialization/service.py:136-184`。
- Adapter 分派在 `src/doxagent/ticker_initialization/catalog.py:44-54`。

## 4. 当前冻结 DAG

`default_plan()` 当前包含 12 个父级节点：

| 顺序关系 | 节点 | block | 依赖 |
|---|---|---|---|
| 并行起点 | `d1` | D1 | 无 |
| 并行起点 | `cdecr` | CDECR | 无 |
| 汇合 | `o2` | O2 | `d1`, `cdecr` |
| 下游 | `d2` | D2 | `d1`, `o2` |
| 下游 | `d3` | D3 | `d2`, `o2` |
| O4 | `o4.configure` | O4 | `d3` |
| O4 | `o4.deliver` | O4 | `o4.configure` |
| O4 | `o4.register` | REGISTER | `o4.deliver` |
| 激活准备 | `activation.prepare` | ACTIVATION | `d1`, `o2`, `d2`, `d3`, `o4.register` |
| 激活提交 | `activation.commit` | ACTIVATION | `activation.prepare` |
| Bus ACK | `bus.ready` | BUS_START | `activation.prepare`, `activation.commit` |
| Runtime ACK | `runtime.ready` | RUNTIME_START | `activation.prepare`, `bus.ready` |

定义位置：`src/doxagent/ticker_initialization/catalog.py:14-41`。

父级节点并不是全部恢复粒度。`substeps.durable()` 与 `checkpointed_json()` 会按真实 shell/wave/turn 动态注册 `managed_by=<parent>` 的内部节点；内部节点同样进入 `initialization_nodes` 和 `initialization_attempts`。证据：`src/doxagent/ticker_initialization/substeps.py:86-129`、`src/doxagent/ticker_initialization/substeps.py:189-302`。

## 5. 持久化模型与真相源

### 5.1 初始化控制库

`InitializationRepository` 使用本地 SQLite，启用 WAL、`synchronous=FULL`、30 秒 busy timeout，并以 `BEGIN IMMEDIATE` 包围写事务：`src/doxagent/ticker_initialization/repository.py:33-104`。

主要表：

| 表 | 当前用途 |
|---|---|
| `initialization_runs` | run 的 ticker、状态、phase、cutoff、control epoch、error、state_seq |
| `ticker_operations` | 每个 ticker 唯一 active run、owner、递增 token、lease_until |
| `initialization_nodes` | 当前 generation 的 node 状态、输入、receipt、result、error |
| `initialization_attempts` | 按 execution_id 保存历史 attempt；唯一键为 run/node/generation/ordinal |
| `initialization_events` | append-only 状态事件 |
| `initialization_outbox` | 面向摘要同步/投影的 coalesced 状态 |
| `activation_revisions` | 不可变 activation revision |
| `ticker_active_revision` | ticker 当前激活 revision 指针 |
| `activation_worker_ack` | Bus/Runtime 对 revision 的真实 ACK 与 heartbeat |

建表事实位于 `src/doxagent/ticker_initialization/repository.py:41-83`。

当前没有 repair incident、repair attempt、worktree、repair branch、repair runtime/container 或 human escalation 专用表。

### 5.2 Run 与 Node 状态

- Run 只有 `QUEUED / RUNNING / SUCCEEDED / FAILED` 四种状态：`src/doxagent/ticker_initialization/schema.py:27-32`。
- Node 当前使用字符串状态 `PENDING / RUNNING / SUCCEEDED / FAILED`，并保存 `generation`、`ordinal`、`execution_id`、`execution_version`、`receipt`、`result` 和 `error`：`src/doxagent/ticker_initialization/schema.py:75-87`。
- Repair/Human 状态不在当前枚举或 record 中。
- `PARTIAL`、`DEGRADED`、`NOOP` 等通过 `NodeResult.quality_annotations` 保存，不是父 workflow 状态：`src/doxagent/ticker_initialization/schema.py:41-45`。

### 5.3 Attempt 与代码版本

- 数据库约束每个 node generation 最多 ordinal 1、2 两次 attempt：`src/doxagent/ticker_initialization/repository.py:54-60`。
- `begin()` 在 dispatch 前递增 ordinal、生成 execution_id、保存 execution version 并冻结 dependency results：`src/doxagent/ticker_initialization/repository.py:423-459`。
- execution version 包含 workflow/contract、`DOXAGENT_BUILD_COMMIT`、全 Python 源码 hash 和 prompt/skill hash：`src/doxagent/ticker_initialization/provenance.py:9-28`。
- 当前记录的是**实际领取该 attempt 的 Worker 镜像版本**；run 本身没有“仅允许某个代码 revision 执行”的字段。

## 6. 正式失败的形成与失败信息

### 6.1 Node 失败

Adapter 异常会被父 Worker 捕获，写入 node receipt 的 `failure`，并把 node 结算为 `FAILED`：`src/doxagent/ticker_initialization/service.py:185-191`。

`failure_details()` 当前保存：

- `code`；
- `manual_resume_required`（只有 `WORKER_INFRA_RECOVERY_EXHAUSTED` 在这一层显式为 true）；
- `scope`；
- `retryable`；
- 最多 3000 UTF-8 字节的 `summary`。

证据：`src/doxagent/codex_runtime/recovery.py:9-35`。

此外，node 自身的 `error` 保存为最多 4000 字符的 `ExceptionType: message`。Codex worker invocation/job 信息会进入 receipt 的 `worker_invocations`，包括 job、ticker、node、model/provider、child run ID、initialization ID 和 ordinal：`src/doxagent/ticker_initialization/substeps.py:40-57`。

### 6.2 自动尝试预算与 run `FAILED`

- 普通父节点失败后可以使用同 generation 的第二次 attempt。
- 父 `_drive()` 只在失败节点 `ordinal >= 2` 或其 failure receipt 明确要求人工恢复、且没有仍在恢复的父节点时，将 run 正式结束为 `FAILED`：`src/doxagent/ticker_initialization/service.py:93-126`。
- `finish(error=...)` 会拒绝带未结算父节点的释放，将遗留内部 RUNNING 节点标记失败，设置 run `FAILED`、保存 run error、设置 `manual_resume_required=true`，并删除 `ticker_operations` active 占位：`src/doxagent/ticker_initialization/repository.py:585-618`。
- 因而本需求 V1 所说的“正式 FAILED、原执行已结束”在当前模型中有明确持久化边界：run 是 `FAILED` 且对应 `ticker_operations` 已删除。

### 6.3 当前失败对外可见性

- CLI `status` 输出 run、current/failed nodes、attempt count、active revision 和 node 摘要：`src/doxagent/ticker_initialization/cli.py:193-218`。
- CLI `inspect-node` 可读取完整 NodeRecord（包括 inputs/receipt/result/error）：`src/doxagent/ticker_initialization/cli.py:219-224`。
- Read Projection 将内部 block 汇总为六个公共阶段，并保留所有 `failed_node_keys`：`src/doxagent/v2_read/initialization.py:14-115`。
- V2 页面展示的是通用 `INITIALIZATION_FAILED` failure DTO，不直接展示 node receipt 的原始异常和 Codex job 上下文：`src/doxagent/v2_read/initialization.py:99-115`。
- coalesced summary outbox 明确排除 run error 原文，只同步 `has_error`、最多 10 个 failed node key、诊断数等最小摘要：`src/doxagent/ticker_initialization/repository.py:289-360`。

## 7. Resume、reconcile 与断点复用

### 7.1 原 initialization 的 resume

`InitializationRepository.resume()` 的现有合同：

- 只接受 run `FAILED`；
- `OPERATOR_STOPPED` 不可恢复；
- 同 ticker 不能已经有 active `ticker_operations`；
- 默认选中全部 failed nodes，也可由 CLI 指定一个 failed node；
- 失败的 managed child 会把其 `managed_by` parent 一并放回待执行；
- 选中节点 `generation += 1`、`ordinal = 0`、`execution_id = None`、状态回到 `PENDING`；
- run 回到 `QUEUED`，清除 run error/manual flag；
- 重新插入同一 ticker、同一 run ID 的 `ticker_operations`；
- 写入 `manual.resume` 事件。

证据：`src/doxagent/ticker_initialization/repository.py:620-696`。

历史 attempts 不删除；测试验证历史序列可以是 `(generation, ordinal) = (1,1), (1,2), (2,1)`：`tests/test_ticker_initialization_control.py:74-91`。

### 7.2 成功节点不重跑

- `_drive()` 把 `SUCCEEDED` 父节点加入 complete 集合，只调度依赖已完成且自身未完成的节点：`src/doxagent/ticker_initialization/service.py:93-129`。
- 每个默认 DAG 节点的 fault matrix 测试都验证：失败两次 → 原 ID 精确 resume → 失败节点再执行一次；resume 前已经完成的所有节点调用次数不变：`tests/test_ticker_initialization_fault_matrix.py:16-48`。
- startup integration 测试验证 `runtime.ready` 失败后恢复时 `activation.prepare` 的完整 NodeRecord 不变：`tests/test_ticker_initialization_startup_integration.py:141-170`。

### 7.3 崩溃与 committed side effect reconcile

- 每个 Adapter 在 execute 前先得到 reconcile 机会。
- Research adapter 有 `child_run_id` receipt 时重新进入现有 orchestrator，由子 orchestrator恢复冻结 checkpoint；不是创建新的 child identity：`src/doxagent/ticker_initialization/research_adapter.py:68-99`。
- O4 adapter 能按 initialization ID + execution ID 找回已提交 request；`PENDING/RUNNING` 继续 process，`SUCCEEDED/DEGRADED` 认领既有结果：`src/doxagent/ticker_initialization/o4_adapter.py:60-75`、`199-211`。
- Activation adapter 的动作本身按 activation revision/ACK 幂等核对：`src/doxagent/ticker_initialization/activation_adapter.py:22-114`。
- crash fault matrix 验证 side effect 已提交但父 receipt 未落下时，下一 Worker reconcile 后不重新 dispatch：`tests/test_ticker_initialization_fault_matrix.py:51-77`。
- Codex Worker job 以 idempotency key 去重；终态 job 在 manager 重启后直接返回，不重新执行：`tests/test_ticker_initialization_worker_jobs.py:27-60`。

### 7.4 “checkpoint”在当前代码中的实际组成

当前没有单独名为 `initialization_checkpoints` 的总表。恢复信息分散但均持久化在：

- `initialization_nodes.receipt` 中的 parent/child execution receipt；
- `initialization_attempts` 的历史 NodeRecord；
- Codex runtime SQLite 中的 child workflow bundle/checkpoint/attempt/thread；
- `/data/workspaces` 下的 Codex run workspace；
- CDECR job/state/registry 与 Event Library；
- O4 repository request/plan/delivery checkpoint；
- immutable activation revision 与真实 consumer ACK。

所以需求文档中的“复用 checkpoint”在当前实现中不是读取一个单表对象，而是由父 node receipt 指向多个已有子系统的 durable state，再由各 Adapter `reconcile()`。

## 8. 执行权、并发与 fencing

### 8.1 已有保护

- `ticker_operations.ticker` 是主键，`run_id` 唯一；提交和 resume 都在同一写事务内检查/插入，因此同 ticker 同时只能有一个 active initialization operation：`src/doxagent/ticker_initialization/repository.py:45-49`、`106-191`、`631-692`。
- `claim()` 只领取 lease 过期且 run 为 `QUEUED/RUNNING` 的工作，owner 每次领取获得递增 token：`src/doxagent/ticker_initialization/repository.py:363-392`。
- 所有 node begin/receipt/settle/finish 都通过 `_fence()` 校验 owner、token 和 lease 未过期；旧 Worker 延迟回写会收到 `LeaseLost`：`src/doxagent/ticker_initialization/repository.py:394-421`。
- 心跳每 `lease_seconds / 3` 续租；默认 lease 60 秒：`src/doxagent/ticker_initialization/service.py:56-65`、`88-91`。

### 8.2 当前没有 Repair Runtime 专属领取权

- `claim()` 有一个可选 `permit(initialization_id)` 过滤器，但 `InitializationWorker.run_once()` 固定调用 `claim(owner, lease_seconds=...)`，没有传 permit：`src/doxagent/ticker_initialization/repository.py:363-384`、`src/doxagent/ticker_initialization/service.py:67-71`。
- CLI `worker` 只有 `--once`，没有 `--initialization-id`、owner lane 或 repair-only 参数：`src/doxagent/ticker_initialization/cli.py:17-64`。
- `resume()` 重新入队后，主 Worker 与任何另一个直接使用 `InitializationWorker` 的进程面对的是同一普通队列；谁先 claim 谁获得执行权。
- 当前 lease/fencing 能防止两个 owner**同时提交**，但没有机制保证修复后的指定 runtime 一定先于主 Worker领取。

### 8.3 进程级 WriterLock

生产 CLI `_worker()` 在进入循环前获取 `repo.path.parent / "initialization-worker"` 的 OS 文件锁：`src/doxagent/ticker_initialization/cli.py:67-74`。

`WriterLock` 使用非阻塞 `flock`/Windows byte lock；第二个进程无法获取时抛出 `EXECUTOR_ALREADY_RUNNING`：`src/doxagent/trade_execution/worker.py:58-99`。

在生产 Compose 中所有 backend 服务共享 `v2-data:/data`，主初始化 Worker 的数据库是 `/data/initialization/control.sqlite3`。因此另一个挂载同一卷、直接运行现有 CLI `worker` 的容器会与常驻 `v2-initialization` 使用同一进程锁路径。当前没有 repair-specific worker entrypoint 绕过全局队列同时保留目标级 fencing。

## 9. 各业务节点的当前恢复落点

### 9.1 D1 / CDECR / O2 / D2 / D3

`ResearchInitializationAdapter` 使用固定 child ID：`{initialization_id}-{node_key}`，先将其写入父 receipt，再调用既有 orchestrator：`src/doxagent/ticker_initialization/research_adapter.py:75-99`。

- D1：`CodexGlobalResearchOrchestrator.run()`，只接受 published handoff：`research_adapter.py:128-160`。
- CDECR：`TickerCDECRPipelineCoordinator.prepare_runtime_through_delta()`，可使用 prebuilt bundle，并保存 job/registry/snapshot/delta 引用：`research_adapter.py:162-254`。
- O2：复用完成的 CDECR frozen snapshot/delta，通过 `run_o2_with_upstream_context(..., resume_finalized_only=True)` 继续：`research_adapter.py:255-328`。
- D2：使用 D1 和 Event Library 固定版本，`reuse_published_partial=True`：`research_adapter.py:330-378`。
- D3：先查 reserved policy；不存在时才调用 initialize，使用 D2 run 和 Event Library version：`research_adapter.py:380-404`。

### 9.2 O4

O4 被拆为 configure、deliver、register 三个父节点；request 与 parent execution ID 关联，能重附已有请求。Deliver 的未完成 Source Need 在预算耗尽时会结算为 `REPLAN_REQUIRED`、请求降级为 `DEGRADED`，而不是把整个 DAG无条件硬失败：`src/doxagent/ticker_initialization/o4_adapter.py:77-177`。

Register 要求候选 Message Bus 中至少一个 enabled、未 tombstone 且 source enabled 的 binding；否则是真实失败：`o4_adapter.py:294-334`。

### 9.3 Activation / Bus / Runtime

- `activation.prepare` 汇总五类 artifact ref，验证 Event Library Index、D3 Projection、D1/D2 正文 hash 与 W3 readiness，然后 stage immutable revision：`src/doxagent/ticker_initialization/activation_adapter.py:25-83`。
- `activation.commit` 切换 active revision：`activation_adapter.py:84-88`。
- `bus.ready` / `runtime.ready` 等待真实 consumer ACK；默认 180 秒，超时失败并记录 consumer admission error：`activation_adapter.py:89-114`。
- 首次初始化在激活后启动失败仍保持 `FAILED` 供精确 resume；替换已有 revision 失败时补偿逻辑只尝试 CAS rollback，不重跑上游：`activation_adapter.py:179-184`、`repository.py:1021-1043`。

## 10. 现有 Codex Worker 的能力边界

### 10.1 已存在的可复用基础事实

- `WorkerRunRequest` 支持可选 `thread_id`，job record 保存 thread/turn ID：`src/doxagent/codex_worker/schema.py:43-67`、`102-123`。
- 有 thread ID 时调用 SDK `thread_resume()`，否则 `thread_start()`：`src/doxagent/codex_worker/sdk_runtime.py:357-390`。
- Worker workspace 是 run-scoped store，包含 `context / attempts / artifacts / audit / published`；context/published 有不可变边界：`src/doxagent/codex_worker/workspace_store.py:29-52`。
- 支持 workspace snapshot/fork，但这是**业务 run 文件目录复制**，不是 `git worktree`，也不携带 Git branch/commit：`workspace_store.py:54-101`。
- Codex turn 在当前 run workspace 中执行，approval mode 为 deny-all；生产配置的外层 Docker 隔离开启时 SDK sandbox 为 full access，但该 full access 仍位于容器边界内：`src/doxagent/codex_worker/sdk_runtime.py:165-219`、`357-400`。

### 10.2 当前契约不等于 Repair Thread

当前 WorkerRunRequest 强制包含：

- research workflow version/lane；
- ticker；
- `CodexResearchNode`；
- `CodexResearchAgentRole`；
- attempt ID；
- prompt；
- output JSON schema。

它没有 repair incident ID、Git repository/worktree path、base commit、patch/branch、allowed code paths、test command、repair verdict 或 merge-review handoff 字段。当前持久 thread 主要用于 D1/D2/D3/O4 业务工作流连续性，不是代码修复控制面。

## 11. Docker 与代码隔离现状

### 11.1 生产服务

`docker-compose.v2-production.yml` 当前有常驻：`v2-control`、`codex-worker`、`v2-initialization`、Message Bus、O4、Scheduler、Projector、Delivery 等服务。`v2-initialization` 直接运行：

```text
python -m doxagent.ticker_initialization.cli worker
```

证据：`docker-compose.v2-production.yml:62-124`。

所有 backend 服务继承同一 immutable image，并只挂载 `v2-data:/data`：`docker-compose.v2-production.yml:46-54`。

### 11.2 镜像内不是 Git checkout

- `.dockerignore` 第一项排除 `.git/`：`.dockerignore:1`。
- `Dockerfile.v2` 安装 `git`，但只 `COPY` pyproject/lock/README、`src`、prompts 和 API contract；没有复制仓库元数据：`Dockerfile.v2:1-27`。
- Compose 没有把宿主正式 DoxAgent repository 挂进任何 backend 服务。
- Compose 没有 `/var/run/docker.sock` 挂载，也没有 privileged/cap_add 配置。

因此，当前容器虽然有 git 可执行文件，却没有现成 Git repository 可建立 worktree；也没有从容器内管理宿主 Docker daemon 的既有通道。

### 11.3 当前没有一次性 Repair Container 定义

仓库中没有 repair/guardian Compose service、profile、Dockerfile、container launcher 或 cleanup ledger。现有 `docker-compose.ticker-init.yml` 是完整初始化 worker overlay，会同时给 Message Bus、Scheduler、Codex Worker、O4 Worker注入初始化环境；它不是“只执行一个目标 initialization 的一次性 executor”：`docker-compose.ticker-init.yml:1-52`。

## 12. V2 读模型与人工操作约束

- `ControlService.reconcile_initializations()` 观察 run `FAILED` 后，只把 V2 ticker control 标记为 `initialization_failed=true`；它不会创建修复任务：`src/doxagent/v2_control/service.py:245-271`。
- projected `manual_resume_allowed` 要求 run `FAILED`、`manual_resume_required=true` 且错误不是 `OPERATOR_STOPPED`：`src/doxagent/v2_read/initialization.py:99-115`。
- API resume 是异步 Control operation，具备 actor-scoped idempotency、ETag 前置条件和单 ticker operation-in-progress 防重：`src/doxagent/api_v2/app.py:384-405`、`src/doxagent/v2_control/repository.py:196-258`。
- API resume 不接受 node 选择或 reason；ControlService 使用固定 reason `V2 explicit resume`，并默认恢复所有 failed nodes。只有 CLI 可通过 `--node` 精确指定一个失败节点：`src/doxagent/ticker_initialization/cli.py:30-38`、`178-183`。
- 当前系统没有自动 repair 开关、ticker allowlist、incident pause/cancel、repair status API 或前端 repair 状态显示。

## 13. 需求基线逐项对照

| 需求基线能力 | 当前代码事实 | 判定 |
|---|---|---|
| durable initialization state | SQLite run/node/attempt/event/operation/revision 完整存在 | 已存在 |
| checkpoint / failure information | 父 receipt + attempts + 子系统 checkpoint/workspace；失败 code/scope/retryable/summary 存在 | 已存在，但分布式保存 |
| 正式 FAILED 后再接手 | `finish(error)` 设 FAILED 并删除 active operation | 已存在明确边界 |
| 原 initialization ID resume | `resume()` 原地 FAILED→QUEUED，不创建新 run | 已存在 |
| 已成功节点不重跑 | scheduler 只选择未完成节点；fault matrix 有覆盖 | 已存在 |
| 同 ticker 双执行防护 | ticker 唯一占位 + lease token + fencing | 已存在基础保护 |
| 指定 Repair Runtime 独占 resume | run 无 lane/owner；主 Worker会领取普通 QUEUED；CLI 无 target selector | 不存在 |
| 自动发现 FAILED | Control reconcile 只投影 failed flag；无 Guardian incident creator | 不存在 |
| Repair Incident 持久状态 | 无 schema/table/model/API | 不存在 |
| 两轮自动修复后 HUMAN_REQUIRED | 无 repair counter 和该状态 | 不存在 |
| 独立 Git worktree/branch | 当前只有业务 workspace snapshot/fork；生产镜像无 `.git` | 不存在 |
| Codex Repair Thread | Codex thread resume 基础存在；无 repair request/context/verdict 契约 | 部分基础存在，repair 能力不存在 |
| 同 incident 复用 thread/worktree | thread ID可复用；没有 incident/worktree 关联 | 不存在完整链路 |
| 必要相关验证后才能 resume | 现有初始化无 repair validation ledger/gate | 不存在 |
| 一次性 Repair Container | 无 service/launcher/cleanup 记录 | 不存在 |
| Repair Container 只执行目标 ID | CLI worker 不支持 target ID，claim permit 未接入 Worker | 不存在 |
| Repair Container 不启动 Bus/W1/W2/Monitoring | 没有 Repair Container；现有生产服务分离，但 overlay 不是 repair executor | 不适用/不存在 |
| Repair Container 结束后删除 | 无 container lifecycle owner | 不存在 |
| 修复结果保留独立 branch、不 merge main | 无自动 Git 控制面 | 不存在 |
| 正常 initialization worker 继续其他 ticker | 当前常驻 Worker可并发处理多个 ticker | 已存在 |

## 14. 已确认的硬约束与不变量

以下是当前代码已经依赖的约束；本节只记录事实，不提出改造方式：

1. SQLite 是单主机、本地持久卷真相源；现有代码不把它当跨主机分布式锁。
2. 所有初始化状态写入必须持有有效 lease token；过期/被替换 owner 不能结算 node 或 finish run。
3. 同 ticker active operation 必须唯一；resume 前必须不存在该 ticker 的另一 active operation。
4. 每个 generation 的自动执行预算是两次；只有显式 resume 才打开新 generation。
5. 已成功父节点和内部 durable node 默认复用；恢复先 reconcile stable receipt/artifact/job，再决定是否 dispatch。
6. Activation revision 是不可变引用集合；Bus/Runtime readiness 依赖真实 ACK，不能由父流程伪造。
7. 当前主 Worker 的进程级 OS lock 覆盖整个 initialization worker，不是单 ticker lock。
8. 当前生产 Codex Worker只能看到其 `/data/workspaces` 业务工作区；正式 Git 仓库不在容器内。
9. `FAILED` 投影与初始化库真相之间经过 projector；页面不是 Guardian 可直接依赖的高频控制账本。
10. run 的 `error`、node 的 `error/receipt`、Codex child job/thread、artifact/workspace 分属不同存储；完整 repair context 不能仅从公共 InitializationProgress DTO 获得。

## 15. 定向验证结果

执行命令：

```powershell
.\.venv\Scripts\python.exe -m pytest -q `
  tests/test_ticker_initialization_fault_matrix.py `
  tests/test_ticker_initialization_control.py `
  tests/test_ticker_initialization_worker_jobs.py `
  tests/test_ticker_initialization_startup_integration.py `
  tests/v2_backend/test_initialization_projection.py
```

结果：`44 passed, 3 warnings in 29.35s`。

该组合直接覆盖：

- 默认 DAG 每个父节点两次失败后的原 ID 精确 resume；
- resume 后成功上游不重跑；
- 预算跨 repository restart 保留；
- crashed attempt/committed side effect reconcile；
- Codex Worker job 幂等与终态重启恢复；
- activation/runtime startup failure 的精确恢复；
- Read Projection 保留多 failed node 与 manual resume flag。

三个 warning 均来自第三方依赖的 deprecation/experimental 提示。本轮未运行全量测试、真实模型、真实 provider、远端 Docker 或十几个小时的真实 ticker initialization，因此这些结果不能作为 Guardian/Repair 能力已存在或生产已验收的证据。

## 16. 相关文件索引

### 初始化核心

- `src/doxagent/ticker_initialization/schema.py`
- `src/doxagent/ticker_initialization/repository.py`
- `src/doxagent/ticker_initialization/service.py`
- `src/doxagent/ticker_initialization/cli.py`
- `src/doxagent/ticker_initialization/catalog.py`
- `src/doxagent/ticker_initialization/substeps.py`
- `src/doxagent/ticker_initialization/provenance.py`

### 业务 Adapter

- `src/doxagent/ticker_initialization/research_adapter.py`
- `src/doxagent/ticker_initialization/o4_adapter.py`
- `src/doxagent/ticker_initialization/activation_adapter.py`
- `src/doxagent/ticker_initialization/internal_adapter.py`
- `src/doxagent/ticker_initialization/cdecr_process.py`
- `src/doxagent/ticker_initialization/cdecr_tasks.py`

### V2 Control / API / Projection / Frontend

- `src/doxagent/v2_control/repository.py`
- `src/doxagent/v2_control/service.py`
- `src/doxagent/v2_control/worker.py`
- `src/doxagent/v2_control/mirror.py`
- `src/doxagent/api_v2/app.py`
- `src/doxagent/api_v2/overview.py`
- `src/doxagent/v2_read/initialization.py`
- `frontend/v2/src/core/operations.ts`
- `frontend/v2/src/pages/overview/initialization.tsx`

### Codex Worker 与部署

- `src/doxagent/codex_worker/schema.py`
- `src/doxagent/codex_worker/sdk_runtime.py`
- `src/doxagent/codex_worker/jobs.py`
- `src/doxagent/codex_worker/workspace_store.py`
- `Dockerfile.v2`
- `Dockerfile.codex-worker`
- `docker-compose.v2-production.yml`
- `docker-compose.ticker-init.yml`
- `.dockerignore`

### 直接相关测试

- `tests/test_ticker_initialization_fault_matrix.py`
- `tests/test_ticker_initialization_control.py`
- `tests/test_ticker_initialization_worker_jobs.py`
- `tests/test_ticker_initialization_startup_integration.py`
- `tests/test_ticker_initialization_substep_recovery.py`
- `tests/test_ticker_initialization_o4_adapter.py`
- `tests/v2_backend/test_initialization_projection.py`

## 17. 本轮未形成的产物

按照本轮要求，本事实包没有包含：

- 开发计划或阶段拆分；
- 新组件设计；
- 数据库 schema 草案；
- API/状态机设计草案；
- Worktree/Container/Codex 调度技术选型；
- 测试开发计划；
- 远端部署或真实初始化操作。
