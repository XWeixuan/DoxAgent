# DoxAgent V2 Ticker Initialization Guardian：执行性开发方案

> 日期：2026-09-20；代码核对基线：`main@d7d1c24c`。本文件是待实施方案，不表示功能已经实现或上线。  
> 输入：用户粘贴需求、同目录 `ticker_initialization_guardian_fact_pack_20260920.md`、本轮问答及补充要求。冲突时以本轮用户答复为准。  
> 本轮只交付方案和 handoff，不修改业务代码、不操作远端、不启动真实初始化。

## 1. 定案与边界

- Guardian 只接管**正式 FAILED** 的原 initialization；不扫描 RUNNING 时长、不主动杀死疑似卡住的任务。
- 一个 initialization 对应一个持久 Incident、一个 Repair Thread、一个 worktree/branch；后续新故障复用它们。每轮执行使用新临时容器。
- 原 initialization ID、cutoff、monitor mode、已成功节点、有效产物与 checkpoint 保留；不用 submit、reinitialize 或 rerun 创建替代初始化。
- 修复范围包括初始化编排、其调用的业务工作流，以及本方案定义的提示词补充修复。**不修生产 Codex Worker、MCP 或其他常驻服务本体。**临时执行器始终调用原生产 Codex Worker。
- Repair Thread 是服务器后台持久 Codex 会话，不是桌面任务。固定 `model=gpt-5.6-sol`、`effort=medium`；部署预检验证可用性，不悄悄降级或换模型。
- **每个具体持久节点最多三轮修复**，不是整个 initialization 三轮，也不是模型最多交互三次。A 越过后 B 失败，B 从第一轮开始。
- “尽全力恢复、尽可能少改编排、覆盖已知同源下游缺陷”写进 Repair Agent 提示词；不做修改行数、文件数、局部补丁大小或难度门禁。
- 自动修复不修改正式源码、不重启正式服务、不 merge/push main。修复分支保留，日后人工审阅合并。
- 只加必要的接管、持久化、隔离和日志能力；不新增通用调度器、资源池、全局并发控制、ticker admission 限流或第二套生产环境。

### 1.1 必须明确的现实边界

“不得因困难放弃”不等于绕过业务正确性、伪造产物或无限重试。困难、大改动、第一次测试失败都不是转人工理由；三轮同节点仍未越过、缺少必要外部权限、根因确实在明确排除的服务内、恢复需要破坏冻结业务输入，才是具体可审计的人工处理原因。

“main 内自动更新 Git 跟踪的日志”与“自动修复不改 main”不能同时严格成立。本方案采用**集中持久日志 + main 工作目录阅读入口**实现同等阅读效果：不用找分支，读取一个文件即可；但该文件不会自动进入 main 的提交历史，GitHub main 页面也不会自动出现最新记录。需要版本化时由人工导出、提交，不新增文档机器人分支/自动合并流程。

## 2. 结构：一个常驻 Guardian，两个按需命令

```text
生产 initialization：正式 FAILED
  -> Guardian：登记 Incident、收集证据、维护分支/轮次
  -> 临时 Repair Agent job：后台 Codex 会话，代码诊断/修复/本地测试
  -> Guardian：独立验证、提交修复分支、构建不可变修复镜像
  -> 现有 V2 Control：带内部 repair 路由恢复同一个 ID
  -> 临时 Repair Executor：只领取这个 ID，运行现有 InitializationWorker
       -> 原生产 Codex Worker、原共享状态、原 Bus/Runtime ACK
  -> Guardian：核对节点与 run 真相、更新集中日志、清理临时容器
```

Guardian 使用稳定发布镜像，作为新增单实例服务运行；挂载原 `/data`、独立 repair 持久目录和 Docker socket。它负责 Docker 操作，不让模型获得 Docker socket。该 socket 是宿主机高权限入口，只有可信控制器持有；不将命令拼接字符串或模型生成的 Docker 参数直接交给宿主执行。

Repair Agent job 与 Repair Executor 是两种一次性进程，不是两套生产环境：前者只编辑测试代码，后者才访问生产状态、执行原 DAG。用两个隔离运行边界，避免给会写代码的模型生产数据库和 Docker 权限。两者都不启动 Bus、Runtime、O4 worker、W1/W2 或新的 Codex Worker。

默认持久根目录 `/var/lib/doxagent/initialization-repair`（部署参数可改），Guardian 与子容器使用相同绝对挂载路径，以保证 worktree 的 `.git` 指向有效。目录：

- `repository.git/`：独立 bare clone；不使用正式工作树的 Git common dir。
- `incidents/<incident_id>/worktree/`：分支 `codex/init-repair/<incident_id>`，整个 Incident 复用。
- `incidents/<incident_id>/codex-home/`：独立持久 Codex 会话目录，不复用生产 Worker 的 CODEX_HOME。
- `incidents/<incident_id>/rounds/<round_id>/`：脱敏上下文、补丁、测试输出、镜像信息、容器日志、模型报告。
- `init-issue.md`：所有 Incident 的唯一汇总阅读文件。

部署时显式设置认证来源和服务 UID；不把 token 写入上下文、Git、集中日志或 docker 命令输出。Agent 只挂载本 Incident 可写代码/会话/报告目录及必要只读 Git 元数据，不挂载 `/data`、正式仓库或生产环境文件。关闭无关 MCP，不继承生产 Worker 的 MCP 配置。Agent 网络仅用于已配置模型服务和确有必要的依赖获取，不获得生产业务服务凭据。

## 3. 现有实现决定的修改点

| 当前事实 | 本次具体处理 |
| --- | --- |
| `finish()` 进入 FAILED 后删除 `ticker_operations`；`resume()` 原子重置节点并重建占位 | 保留语义，在同一恢复事务内写入 repair 路由，不能恢复后再补标记 |
| `claim()` 全队列查询且 LIMIT 1 后才调用 permit | 在 SQL WHERE 中按 lane 过滤；不要用 permit 回调绕过第一条记录 |
| CLI worker 有全库 `WriterLock` | 新增独立 `repair-execute` 命令，只取目标锁和目标 lease，不运行普通 worker 命令 |
| V2 Control 更新 epoch、镜像状态及 failed 标记 | 自动恢复走原 Control saga，不能 Guardian 直接 resume 后让 Control 自己猜 |
| attempt ordinal 限制为每 generation 两次 | 完全保留；三轮 repair 是外层新预算，不改 ordinal CHECK |
| managed 子节点具有稳定 key；成功节点可 checkpoint 复用 | 以最深层失败 key 计数，父节点汇总失败不重复计费 |
| DurableWorker 先冻结 request，再用 execution ID 幂等 dispatch | 只在新请求首次冻结前注入 repair 提示词；既有 request 永不改写 |
| 研究 workspace context 是冻结内容，部分 runner 每次准备时写入本地 prompt assets | 不靠覆盖旧 context 实现本次提示词修复，使用请求级补充层 |
| 生产镜像没有 `.git`，code revision 可能为 unversioned | 增加 build commit/源码 hash 的可验证发布元数据，再据此创建 worktree |

详细当前调用链见事实包。本方案新增符号/文件均为待实现接口，不是假定现有 API。

## 4. 数据模型和原子性

### 4.1 在现有 initialization SQLite 中新增四张小表

由现有 schema 初始化/迁移路径执行幂等迁移；使用同一数据库，避免 Incident 接管与 run 恢复跨库竞争。不引入新数据库服务。

1. `initialization_repair_incidents`：`id` 主键；`initialization_id UNIQUE`；ticker；状态 `ACTIVE/SUCCEEDED/HUMAN_REQUIRED/CANCELLED`；phase；当前 round；创建/更新时间；源镜像 ID/提交/hash；worktree/branch/thread_id；最近错误和控制 epoch；payload 保存可扩展非索引字段。
2. `initialization_repair_rounds`：`id` 主键；incident_id；顺序号 `seq`（UNIQUE incident_id,seq，仅审计，不是预算）；目标节点与其本轮 ordinal；观测到的 failed state_seq；phase；thread turn IDs；控制 operation ID；修复 commit/image ID；agent/executor 名称及 ID；测试摘要、退出码、结果。
3. `initialization_repair_node_budgets`：主键 `(incident_id,node_key)`；`rounds_started`；首次失败证据；最近失败证据；`crossed_at/state_seq`。generation、ordinal、异常 hash 不进入预算身份。
4. `initialization_repair_issue_entries`：`entry_id` 主键；incident_id、round_id、kind、created_at、content。模型报告与客观执行结果分别保存；使用唯一事件键实现重投幂等。此表是集中 MD 的恢复来源，不另建日志系统。

`ticker_operations` 增加 `execution_lane TEXT NOT NULL DEFAULT 'normal'`、nullable `repair_incident_id/repair_round_id`。老记录默认为 normal，旧正常提交不受影响。为 lane/lease 查询和 Incident 状态扫描加普通索引即可。

Incident 的 phase 使用 `CONTEXT/CODING/VERIFY/QUEUE/EXECUTE/RECORD`，基础设施临时失败保留原 phase 并记录 `retry_at/last_error`，不再叠加一整套工作流状态机。

### 4.2 必须在单个 initialization DB 写事务内完成的操作

- `open_incident(run_id, expected_state_seq)`：复查 FAILED、没有任何该 ticker operation、非 OPERATOR_STOPPED、没有旧 Incident；插入唯一 Incident。Control 是否仍允许操作还需恢复前再查，不把早期快照当永久许可。
- `start_round(incident_id, failed_state_seq, targets)`：复查 run 与当前 round、目标节点事实；锁定/更新各节点预算并插入一轮。重复扫描或同一请求重放返回旧 round，不加次数。
- `resume_for_repair(run_id, incident_id, round_id, control_epoch, control_operation_id)`：复用抽出的 `_resume_in_transaction()`，一并重置原 FAILED 节点/managed parent、保留 activation rollback 特例、插入 lane=repair 的 operation、写事件。校验 Incident ACTIVE、round 已验证、镜像已记录、ID/epoch 匹配。
- `claim_repair(run_id,incident_id,round_id,owner)`：只匹配这三个 ID、lane=repair、允许的 Control epoch、合法状态和已过期/未领 lease，原子分配 token。复用当前 heartbeat/fence。

`finish()` 不改变：终态仍删除 operation；独立 Incident/round 保留。普通 `claim()` SQL 必须 `execution_lane='normal'`，因此不会抢走 repair，也不会被队首 repair 阻塞。

普通 `resume()` 发现本 run 有 ACTIVE/HUMAN_REQUIRED Incident 时返回 `REPAIR_OWNS_INITIALIZATION`，不偷偷切回原版代码；控制操作提交阶段也做相同友好预检，最终以 initialization DB 事务为准。修复与人工提交的竞争通过既有 operation idempotency、epoch 和最终事务复查解决，冲突者明确失败而不是覆盖胜者。

## 5. 每个具体节点三轮：精确定义

### 5.1 节点身份与目标选择

预算键为 `(initialization_id, NodeRecord.key)`。优先选择正式 FAILED 的最深 managed 子节点；例如两个 O3 shell 的 durable key 不同，分别计数。父节点只是子错误向上传播时，不再为父节点扣一次预算。没有失败子节点证据时，父节点自身就是本次具体节点。

不同 generation、同节点换一种异常、同一错误换措辞，都不重置预算。不得改 key、创建替代 run 或拆分虚假节点来获得次数。若首次只有父错误、后续才补全原故障对应子节点证据，将原计数迁移到明确子 key，并留下 alias/证据，不以“发现子节点”为由清零。若确实是已越过原位置后出现另一个持久节点的失败，新节点独立计数。

一次正式 FAILED 可以有多个并行叶子失败：同一 repair round 同时修它们，每个目标各用一次机会；顺序号 seq 可以相同，但节点 ordinal 可以不同。任一仍未越过的目标已用满三轮，则该 Incident 转 HUMAN_REQUIRED，记录所有节点情况；不通过只挑其他目标继续绕过已耗尽节点。

### 5.2 一轮开始和结束

- 前置证据/认证准备失败不扣轮数。第一次实际派发本轮代码修复任务前，持久化 round 和目标预算；该 round 内继续会话、修测试、修格式、重连都不重复扣费。
- 一轮包含：诊断与代码修改 → 必要本地验证 → 至多一次新的业务 resume/execution。测试不过就继续本轮修复，不因为测试失败提前判定用完一轮或放弃。
- 业务执行中原有每 generation 两次 attempt 仍有效，均属于这同一 repair round。不能在同一 round 中无限调用 resume。
- 容器创建网络失败、构建工具临时失败、SDK 连接中断只重试当前技术步骤，不能假装发生了另一次节点修复。进程重启也不能重复扣次数。
- 执行后，原目标 key 到 SUCCEEDED，才记录已越过；只变 RUNNING/PENDING、模型声称通过或父节点日志前进都不算。可在仍执行下游时记录原目标越过，但不会并行启动新修复。
- 正式 FAILED 后，仍失败的旧目标进入其下一轮；新失败目标从 1 开始。原目标累计第三轮仍未越过，转 HUMAN_REQUIRED。
- 预算在同一 Incident 内累计，已越过节点若以后被实际重新失败，不因换异常清零；不擅自给人工恢复重置预算。

示例：`A(1) -> A(2)通过 -> B(1)通过 -> C(1/2/3)仍失败 -> HUMAN_REQUIRED`。虽然 Incident seq 已到 6，也不构成“总共三轮用尽”。普通大节点 D3 的另一个 shell 失败不是上一个 shell 的第二轮。

## 6. Guardian 控制循环与恢复

扫描间隔默认 30 秒，可配置；扫描仅是发现延迟，不是 hang timeout。Guardian 用 repair 目录专用 OS 锁避免同实例重复控制，不复用初始化全库 executor 锁。每个 ACTIVE Incident 按持久 phase 推进；按需任务独立等待，不用一个长 await 阻塞其他 Incident；不新增可配置资源并发池。

处理顺序：

1. 读取正式 FAILED 候选，过滤停止/移除、已有其他同 ticker run/operation、失效输入及已有终态 Incident。启动前已存在的历史 FAILED 默认不自动接管：保存首次启用 watermark；显式 `adopt --initialization-id` 可纳入旧任务，避免上线时批量复活历史失败。
2. 原子创建或加载 Incident。基于实际部署镜像对应 commit 创建独立 worktree；有修复轮次时直接复用原 worktree，不跟随最新 main/rebase。
3. 收集当前 run、失败叶子/parent、相关 attempts/receipt、被冻结的输入引用与 hash、代码 provenance、有限关联日志、上一轮差异/验证结果。保留足够路径供按需读取，不把十几小时历史全部塞入 prompt。
4. 创建/恢复同一 Codex thread，提交当前 round 的故障包；完成代码修复、测试及报告。
5. Guardian 检查测试命令退出状态、Git diff、报告字段和 frozen-input 约束，亲自运行回归，不信任一句“测试已过”。保存分支 commit（失败候选也保留），构建唯一镜像 tag 并记录 image ID。
6. 走第 7 节 V2 Control 恢复。目标 executor 只在 operation 已结算、repair lane 和 round 均匹配后启动。
7. 等待目标容器和数据库；原 run SUCCEEDED 才结束整个 Incident；正式 FAILED 才安排下一轮或转人工。及时记录已越过节点与新的失败证据。
8. 在 finally 中收集 inspect/日志/退出码后删除本轮 agent/executor 容器；持久化 worktree、thread、commit、报告、执行史不删除。

### 6.1 崩溃/重放规则

- Docker 名称固定 `doxagent-repair-agent-<round_id>` / `doxagent-repair-exec-<round_id>`，labels 包含 incident/run/round/role。先记录 launch intent，再 create；重启后 inspect 名称与 labels，不创建第二个同 round 实例。
- 容器仍存活：观察原容器，不能重发 turn、resume 或启动另一个 executor。容器已退出：先收集已有结果，再进行幂等入账和删除。不使用 `--rm` 导致退出证据先消失。
- SDK start/turn 响应丢失：通过该 Incident 的持久会话目录、独有 cwd/名称、thread read 与 round 标记确认已有 thread/turn，能确认即复用；状态不确定时暂停重复派发并记录，不无依据新建第二个修复会话。
- Executor 退出而 run 仍 RUNNING：记录 `EXECUTOR_EXITED_WITH_NONTERMINAL_RUN` 并转 HUMAN_REQUIRED；**不主动改成 FAILED、不杀生产 Worker job、不自动再 resume**。这是本期不处理 RUNNING hang 的边界，不是“困难放弃”。保留 repair 所属信息，主 worker 也不能捡走它。
- 控制/数据库暂不可达时先保留证据和重试时间，不猜业务状态；恢复后对账。权限、磁盘等明确持续外部障碍要在问题日志暴露并转人工，不用三次模型空转消耗节点预算。
- 服务停止只停止 Guardian 发起新动作，不批量终止正在执行的修复容器。显式取消只对非 RUNNING 的 Incident 生效；运行中按现有控制语义处理，不新增强杀能力。

## 7. 接入现有 V2 Control，处理人工操作竞争

不新增公共 HTTP 修复 API。Guardian 调用内部 `ControlRepository.submit_repair_resume(...)`，仍生成现有 `RESUME_INITIALIZATION` operation，actor 固定 `system:initialization-guardian`，幂等键 `repair:<incident_id>:<round_id>`，携带当前 expected revision。内部 body 附加 `repair_incident_id/repair_round_id`；公共 resume endpoint 仍只收原空 body，不允许客户端伪造内部路由。

`ControlService.step()` 保留 `_mirrors()`、epoch 校验及 settle；仅在内部 repair operation 分支调用 `resume_for_repair()`，其他调用原 resume。Control 对 `initialization_failed` 的现有同步先完成，Guardian 才提交；“标记尚未同步”重试观察，不改写 Control state。数据库间没有全局事务，沿用既有可重放 saga：同一 operation 再 step 不新增 generation、budget、lane 或执行实例。

恢复前再次检查当前 control initialization ID、epoch、removed/permission/pending operation，避免人类 REMOVE/RESTART 后 Guardian 把旧任务复活。操作被新 epoch supersede 时，中止尚未开始的自动恢复并结算为 CANCELLED；已在执行时由既有 mirror/fencing 路径失去权限，不绕过它。

增加 `repair status/inspect` 和 `repair release --incident-id --reason` 运维命令。release 仅允许没有活 lease/容器且 run 为 FAILED：将 Incident CANCELLED，保留预算/分支/日志，再允许正常人工 resume。明确警告：正常 resume 使用生产原版代码，不含隔离修复；不能把 release 当默认自动降级方式。

无需本期新做前端修复控制面。现有 API 对 ACTIVE/HUMAN_REQUIRED 给明确错误码和 incident ID；CLI 与集中日志可查看详细进度。`HUMAN_REQUIRED` 是 Incident 状态，不新增 run.status，避免破坏既有四态协议。

## 8. Repair Executor：复用 Worker，不复制执行算法

在现有 initialization CLI 新增 `repair-execute --initialization-id --incident-id --round-id`。三个参数必填，没有“自动找下一个”的 fallback。启动时验证修复 image/build SHA 与 round 一致、生产 Worker URL 与部署指纹一致、共享数据库与目标身份一致。

取得 `<initialization_db_dir>/repair-<initialization_id>` OS 锁，再执行 `claim_repair()`。将 `InitializationWorker._run_lease()` 提取/开放成明确的 `run_claimed(lease)` 接口，普通 worker 仍原调用；修复命令只调用一次。复用 `_drive`、heartbeat、reconcile、adapter_factory、attempt 分配和 ACK，不能实现第二套 DAG loop。

临时容器从实际生产 `v2-initialization` 容器 inspect 得到白名单配置：共享数据卷、网络、Worker URL、数据库/工件路径、CDECR 配置、安全状态只读挂载、必要 provider 设置、host aliases、执行 UID。不要硬编码 Compose 生成的卷名、只读取 `.env` 或直接复制全部敏感配置到日志。使用 Docker create 参数数组/API，不运行 compose up。

严格覆盖 image、entrypoint/command、容器名、labels、restart=no；不发布端口，不挂 Docker socket/正式仓库，不继承生产 worker 启动命令。保留既有安全设置，不新加本期专属资源策略。生产当前 `PREBUILT_REQUIRED` 不能擅自改为 LOCAL_ONLY；只有配置本来允许的 CDECR 路径才可执行。未完成下游仍使用原生产 Bus/Runtime 的真实 ACK，不能伪造 readiness。

除运行代码外，executor 是原生产初始化的一个合法执行者，会通过现有仓储写共享状态；这是必要授权，不等于允许 Repair Agent 直接 SQL 修库。自动补丁不得引入破坏性 schema migration；需要改变现有持久契约的修复必须先证明向后兼容，否则记录具体冲突交人工协调。

## 9. 后台 Repair Thread、提示词与验证

### 9.1 SDK 接线

新增专用 `initialization_repair/agent.py` 使用已安装 `openai_codex.AsyncCodex`，不复用研究 Worker 的 HTTP job 类型冒充代码代理，也不修改 Worker/MCP 实现。

- 首次 `thread_start(cwd=worktree, model='gpt-5.6-sol', ephemeral=False, sandbox=Sandbox.workspace_write, approval_mode=ApprovalMode.deny_all, developer_instructions=...)`；立即保存 thread ID。
- 后续 `thread_resume(thread_id, cwd=同一路径, model=同一模型, sandbox=同一隔离边界, approval_mode=...)`。
- 每个模型 turn 显式 `effort=ReasoningEffort('medium')`，保存 turn ID；设置报告 JSON schema，流式保存事件和最终报告。
- Agent job 退出不删除 CODEX_HOME；下轮新容器恢复同一 thread。模型可在一轮内进行多次修复/测试，不限制成一条最终回复。
- SDK 可用接口已对照本地安装包 `api.py` 的 AsyncCodex/AsyncThread 签名。持久会话的 start/resume/read 生命周期另核对了 [OpenAI 官方 App Server 文档](https://learn.chatgpt.com/docs/app-server?translationFallback=zh-Hans)。部署必须锁定 SDK/CLI 版本并真实做一次 start→退出→resume 冒烟，不能仅凭接口签名宣称生产可用。

Repair Agent 镜像安装锁定依赖及测试工具；测试在当前 worktree 环境运行，不意外 import 稳定镜像内旧源码。Guardian 的独立验证 job 使用同一候选源码 hash。最终 executor 镜像使用候选 commit 的 `Dockerfile.v2` 既有构建路径，tag 唯一、不覆盖正式生产 tag；Docker build 上下文不包含认证、日志和生产数据。

### 9.2 必须写入开发者提示词的行为约束

新增 `prompts/initialization_repair/agent.md`，核心原文应表达：

> 第一优先级是尽全力修复问题，使原 ticker initialization 在保持业务正确性和既有持久化契约的前提下继续运行。不得因为问题困难、修改面大、需要多轮调试或首次验证失败而放弃、要求人工代修或宣称不可修复。在能够正确恢复的方案中，尽可能选择对初始化编排改动较小的方式；这不是文件数、行数或只修当前节点的限制。若已确认同一错误逻辑很可能使后续节点失败，应一并修复相关调用路径并补回归。不要做无关重构。不得降低质量门槛、伪造产物/成功状态、篡改冻结业务输入、绕过 activation/ACK 或更换 initialization 身份以获得表面成功。确实需要排除范围内的组件修复、外部权限或不可兼容的数据改动时，给出已验证证据和已尝试路径。

以上“改动尽可能小”“困难不能放弃”“同源下游一并修”只以提示词和评审证据落实。Guardian 不解析修改量、不按难度打分、不设置编排文件修改上限；模型输出“太复杂”不作为终态枚举，反馈原 thread 继续本轮。明确的生产访问边界、三轮预算与冻结输入完整性仍由程序保证，两者不是同一种约束。

上下文中日志、抓取文本、历史模型输出都标注为不可信证据，不能覆盖开发者指令。报告包含 `root_cause/evidence/changed_files/tests/downstream_implications/remaining_items`；根因未证实则明确写假设，不能编造确定性。

### 9.3 本次已启动 initialization 的提示词修复

不把“修改 prompts 文件并重新 build”当作必然生效：旧 workspace 的 `context/...`、输入 manifest 和已经冻结的 worker_request 可能仍在使用原内容；直接覆盖会破坏不可变约束。

V1 明确支持**请求级 repair supplement**：在 repair 分支新增 `prompts/initialization_repair/overrides/<incident_id>.json`，条目为 `node_key`、完整 `supplement` 文本、修复原因，精确匹配稳定 node key；同源下游明确列出 key，不靠模糊 ticker/正则广播。对于尚未展开但 key 可确定的 shell/artifact 可预声明；动态 key 尚未知时先由代码修复或下次故障明确补充，不改变 DAG 身份。

在 `substeps.DurableWorker.run()` 中，当且仅当 `step != None`、当前为已登记 repair round、且该 execution 尚无 frozen worker_request 时，调用新增 `repair_prompts.apply(request, node_key, context)`，把补充说明追加到 `request.prompt` 并一次性冻结。记录 supplement hash、repair commit、incident/round、原 prompt hash。补充层只修正任务执行指令，不覆盖业务快照、输出 schema、工具权限或质量阈值。

同一执行的 reconcile 必须直接使用已冻结 request，包括原 supplement，不重读变化后的文件；新 generation 才可能得到新版本。已成功节点不重新执行。该内容在调用端嵌入请求，生产 Worker 无需加载修复镜像或修改其本地 prompts/MCP。

已核实 D1 `workflows/codex_document1/node_runner.py`、O2 `workflows/codex_event_library/remote_runner.py`、D2/D3 各 `runner.py` 均包装 DurableWorker，使用上述共同入口，不逐个复制注入实现。

O4 使用另一条已有冻结路径：`workflows/codex_monitoring_o4/runner.py` 在发送前调用 `repository.freeze_worker_dispatch()`，按 request ID 保存并返回第一次请求。为其 runner 增加可选 `initialization_prompt_transform` 回调，通过 `build_monitoring_o4_runtime()` 从 `O4InitializationAdapter._build(context)` 注入；回调绑定父节点 key、execution ID 和当前 Incident/round，调用同一 supplement helper。在 freeze_worker_dispatch **之前**变换候选请求；旧 request ID 必须仍返回仓储中原请求，新 request ID 才能冻结补充内容。常规常驻 O4 没有该回调，不受影响。实际冻结后将请求/supplement hash 写入初始化 receipt 的审计字段，不用候选 hash 冒充实际 dispatch hash。

因此 V1 覆盖 D1/O2/D2/D3 的 durable 请求与初始化 O4 configure/deliver 请求。`o4.register`、activation、ready 等纯程序/生产消费者节点没有研究 prompt，不伪造提示词入口；CDECR 使用其已有子流程和配置，不承诺修改生产 Worker 内部提示词生效。新发现不在这些路径内的调用只能在已有 durable 请求边界接入同一 helper，不能修改生产 Worker 兜底。

本次 incident 中若仅为提示词问题，优先 supplement，不改同 workspace 的原 prompt assets，避免 prepare 阶段重写 immutable context 失败。基础模板的长期修订可在人工合并时单独整理；补丁同时需要改基础模板时，必须证明当前恢复不会尝试覆盖旧 context，并补相应恢复测试。不能静默忽略 immutable-write 错误。

### 9.4 验证标准

Repair Agent 补一个能够复现根因的测试，并覆盖已发现的同源下游路径；执行失败节点相关测试与 initialization 恢复契约测试。Guardian 重跑测试并核对候选 source hash；shell 命令不能仅来自未经验证的模型报告。默认验证命令集由仓库配置维护，允许按涉及模块增加测试，不按 diff 大小拒绝修复。

本地验证成功只代表候选可投放。节点真实 SUCCEEDED 才代表原故障越过；原 run SUCCEEDED 且现有 activation/readiness 校验成立才代表整个 Incident 成功。不添加“全节点重新跑一次”验收，不把已成功上游当回归耗材。

## 10. 一个 init-issue.md：集中事实账本和 main 阅读入口

所有 Repair Worker 报告均写入同一逻辑问题账本。为避免多个模型同时编辑大 Markdown 文件造成覆盖，Agent 提交结构化报告给 Guardian，由唯一日志写入器存入 issue_entries，再按确定顺序渲染整个 `init-issue.md`。这是代理落笔机制，不是要求用户去各分支收集报告。

每轮修复完成、投放前即写记录，真实执行后补充“已越过/仍失败/新故障”；Incident 成功或转人工时再追加结论。每个条目至少包含：

- ticker、initialization/incident ID、节点 key、本节点第几轮、时间；
- 问题表现、根因、证据位置，明确事实与待证假设；
- 已做修复，逐文件列路径及作用；关联 commit、thread/turn、image ID；
- 实际运行的验证、结果、原节点是否越过、新失败位置；
- 后续仍需修复项；无则写“无已知待修项”，不能省略字段或把 TODO 写成已完成。

模型报告不得用后续执行结果尚未知时的“已解决”替代事实。Agent 崩溃尚未提交报告时，控制器先记“报告未完成”，恢复同一 thread 补写；不可凭空生成根因。分支里的测试/业务代码按既有要求追加 `changelog`，但不在每个分支各维护一份 authoritative init-issue.md。

渲染使用临时文件+fsync+原子 replace；失败时保留旧 MD，数据库记录不丢，下次重渲染。entry_id 幂等保证不重复记账；缺失文件可从表重建。时间/顺序以持久事件为准，先全局索引，再按 Incident 连续展示各轮，便于一次阅读整个初始化修复史。

main 阅读入口一次性部署：在正式 main 根目录创建被忽略的 `init-issue.md` 符号链接，指向集中持久文件；开发提交中只加入对应 `.gitignore` 规则和运维说明，不提交生产绝对路径链接。正式源码/索引/HEAD 不被每轮改动；此入口属于一次安装的运维文件，不声称正式目录连一个目录项也完全不变。权限设置为普通代码阅读账号只读、Guardian 独占写。

Windows 本地 main 无法自动读取服务器 Linux 链接。新增 `repair issues export --output <path>` 导出同一个完整 MD；人工同步后在本地 main 阅读，被 ignore 不造成修复源码混入 main。若希望日志进入 main Git 历史，人工把导出放到跟踪路径并正常提交。这是本期清晰边界，不隐含 SSH 同步服务或自动 push。

## 11. 文件级实施清单与顺序

### 第一组：先锁定恢复和归属契约

- 修改 `src/doxagent/ticker_initialization/repository.py`：lane 列迁移、SQL claim 过滤、事务内 resume helper、`resume_for_repair/claim_repair`、普通 resume 的 Incident 冲突检查。
- 修改 `src/doxagent/ticker_initialization/service.py`：公开 `run_claimed(lease)`，不改 DAG drive/retry 语义。
- 修改 `src/doxagent/ticker_initialization/cli.py`：增加 repair-execute 的三 ID、专属锁、退出码/非终态错误；普通 worker 保持原锁。
- 新增 `src/doxagent/initialization_repair/{__init__,schema,repository}.py`：四表、CAS、轮次和预算，使用现有 initialization DB 事务能力，禁止私自另开连接嵌套写事务。
- 修改 `src/doxagent/v2_control/{repository,service}.py`：内部 resume 入口/私有路由、操作重放、epoch/人工冲突处理。
- 验收：两个 executor/主 worker 并发争用只能一个成功；修复排队不阻塞正常 ticker；原成功节点调用次数不变。

### 第二组：跑通外围闭环

- 新增 `initialization_repair/{guardian,context,git_workspace,containers,agent,cli}.py`：分别负责轮询推进、脱敏证据、独立 worktree、Docker 生命周期、SDK 会话、运维命令。保持这些职责为薄模块，不建立插件框架。
- `context.py` 只输出当前故障相关事实；敏感配置只传给运行环境，报告保存 hash/指纹。
- `git_workspace.py` 创建独立 clone、同 Incident 分支、每轮候选 commit；验证提交源来自实际部署 revision。未认证 unversioned 时不猜 main HEAD，先补发布元数据或人工确认源 hash。
- `containers.py` 只接受可信结构化启动配置和已校验 ID；记录 intent/labels、inspect 重用、日志收集、finally 清理。测试使用 fake Docker runner。
- `agent.py` 负责 SDK start/resume/turn/report，不通过生产 research Worker 调用代码修复任务；生产 Worker 留给业务执行。
- `cli.py` 提供 `guardian/status/inspect/adopt/release/issues export`；状态返回 node budgets、round、commit、thread、容器/错误信息。
- 验收：A 修复成功后 B 新失败，使用同 worktree/thread、新容器、B 第 1 轮；Guardian 任意阶段重启不重复 dispatch/resume。

### 第三组：提示词和集中账本

- 新增 `initialization_repair/repair_prompts.py`，修改 `ticker_initialization/substeps.py` 的首次冻结前分支。
- 新增 `prompts/initialization_repair/agent.md`、override schema/示例；修改 `ticker_initialization/o4_adapter.py` 及 `workflows/codex_monitoring_o4/{service,runner}.py` 注入初始化专属 O4 transform，不改 `codex_worker/*`，不改变常驻 O4 的默认调用。
- 新增 `initialization_repair/issues.py`：幂等记录、完整 MD 渲染/导出；issue 模型写入和客观运行结果明确分层。
- 修改 `.gitignore` 忽略根级运行日志入口和本地 repair 工件，不忽略待审阅的业务补丁。
- 验收：旧 execution 请求字节不变，新 generation supplement 生效，原 immutable context 不变；多个 Incident 的报告能在一个文件读到且不重不漏。

### 第四组：部署与可执行验收

- 修改 `Dockerfile.v2`：显式 build commit 参数、运行环境与 OCI revision label，并保持源码/提示词 hash 可核对。
- 新增 `deploy/Dockerfile.initialization-repair-agent`：锁定 Codex CLI/SDK、测试依赖，独立 CODEX_HOME；不内置认证。
- 新增 `deploy/docker-compose.initialization-repair.yml`：只新增 Guardian 服务与持久挂载/socket 配置；Agent/Executor 按需 create，不定义为常驻 compose 服务。
- 修改 `src/doxagent/settings.py` 与 `pyproject.toml`（如需 console entrypoint）：最少参数为 enable、scan interval、repair root、生产初始化容器标识/Compose label、agent image；模型/effort 与每节点三轮有明确默认，不引入 Incident 总预算参数。
- 新增运行手册：部署前备份 DB；迁移一次；发布带 lane 支持的 control/initialization 镜像；创建集中日志阅读入口；检查 source/image 对应、模型认证、thread 持久化、共享卷/网络/生产 Worker URL；最后开启 Guardian。
- 初次上线允许正常发布升级涉及的服务，**不是**每次 Repair 都重启生产。禁止在仍有不识别 lane 的旧初始化 worker 时启用 Guardian。
- 分组完成重要代码修改后追加仓库 `changelog`，记录语义变化与验证；本方案文档本身不冒充已经完成的变更。

## 12. 测试矩阵与完成门槛

新增测试文件建议：`tests/test_initialization_repair_{repository,budget,control,guardian,executor,prompts,issues}.py`；名字可按测试目录惯例细分，不省略下列行为。

| 类别 | 必须证明 |
| --- | --- |
| 触发 | FAILED 自动发现；RUNNING 长时间不变不触发；OPERATOR_STOPPED/removed/历史未 adopt 不接管 |
| 原子路由 | resume 与 lane 同事务；主 claim 永不领取 repair；正常任务不被队首 repair 饿死 |
| 并发 | 两个目标 executor 只有一个 lease；过期 token 不能写；Guardian 重放不能生成第二轮/第二个容器 |
| 恢复 | 原 ID、cutoff、成功上游结果/次数、checkpoint 保留；原 attempt history 可查，新 generation 仍最多两次 |
| 预算 | A 三轮失败转人工；A→B→C 每个可有三轮；换异常不清零；不同 shell 独立；parent 不重复扣；并行失败分别计费 |
| 控制 | 内部 resume 重放只增加一次 generation；控制标记滞后等待；REMOVE/epoch supersede 不复活；普通人工 resume 不能夺取 ACTIVE Incident |
| 崩溃 | launch 后落库前、SDK turn 响应丢失、验证后、resume 后、容器退出后各位置重启可对账；RUNNING+executor退出不自动失败重跑 |
| 提示词 | frozen request 幂等不变；新请求带 supplement hash；成功节点不重跑；immutable context 不覆盖；生产 Worker URL 固定 |
| 隔离 | Agent 看不到生产 DB/socket/main；executor 只起目标命令；不启动第二套 Worker/Bus/Runtime；main HEAD/索引/跟踪文件不变 |
| 验证/日志 | 模型成功声明不等于验收；独立测试失败继续本轮；每轮逐文件记修复和 TODO；跨 Incident 集中渲染、重复事件去重、重建一致 |
| 生命周期 | 成功、FAILED、启动错误、进程异常均保留退出证据并移除对应临时容器；worktree/thread/分支保留 |

至少运行现有 `test_ticker_initialization_operations.py`、`test_ticker_initialization_substeps.py`、`test_ticker_initialization_substep_recovery.py`、`test_ticker_initialization_control.py`、`test_ticker_initialization_history.py`、`test_ticker_initialization_fault_matrix.py`、`test_ticker_initialization_activation.py`、`test_ticker_initialization_cdecr_prebuilt.py`、`test_ticker_initialization_worker_snapshots.py` 及相关 V2 backend control/projection 测试；实施时以 `rg --files tests` 核实名单，不将文件名猜测当已执行结果。

先 fake SDK/Docker/Worker 跑确定性故障矩阵，再在隔离测试 DB+真实容器中验证挂载、锁、镜像源码与恢复；最后选择明确授权的一个真实失败 initialization 验证生产 Worker 联通和原节点越过。不得为验收故意破坏正在正常运行的生产任务。

最终交付必须包含：代码/迁移/提示词/部署配置、实际测试结果、一次完整 A→修复→继续的证据、A→B 的预算证据、main 不变及集中日志证据。没有真实运行验证时明确标注“实现与模拟验收完成，生产业务验收待进行”，不能宣称已经自动修复成功。

## 13. 本期明确不补的能力

不做 RUNNING hang 诊断/中断、生产 Worker/MCP 热修、生产常驻服务热替换、复杂资源治理、自动合并 main、自动整理 PR、自动跨机器同步日志、补丁批量推广到其他 ticker、重建整条 DAG。修复成果只在该 Incident 的专属代码上继续生效；其他 ticker 使用原生产版本，后续是否推广由正常人工发布流程决定。

对已确认同源下游问题，必须积极修复相关调用链；但不借此扩成全仓重构或改变业务验收门槛。“不保守”体现在完整打通自动修复到真实续跑，“不过度设计”体现在复用既有执行/控制/数据库语义，只补接管所必需的外围能力。
