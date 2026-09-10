# Workflow V2：4 核 8GB 资源治理审查与修复方案

日期：2026-09-11。状态：**修复提案，尚未实施**。

审查基线：本地 `main` / `1ac9e618`，包含当前未提交的服务器部署 overlay；未连接服务器、未读取凭证、未运行真实任务。事故数据来自用户提供的排查报告，不是本轮重新采集。当前目录存在其他工作产生的修改，本轮仅新增本方案。

## 1. 结论与推荐配置

本地实现与事故描述相符：重研究任务可以无全局上限地进入同一 Worker；Codex thread、MCP、历史结果及文件处理没有统一资源生命周期；生产容器没有资源硬边界。**这不是只把 D2 的 4 改成 2 就能解决的问题。**

推荐常态是 **全局 2 个 Codex 执行槽、D2 2 个并行 shell、Worker 3.5 GiB 硬上限、Worker 不使用 swap、会话进程可独立回收**。保留 D1 的 C1/C3 并行和整个 DAG 的依赖并行，不改为全串行，不减少研究步骤，不降低模型/effort，不缩短正常研究时限。

资源准入是“等待可执行容量”，不是产物质量 gate。容量等待不判失败、不消耗业务重试，也不新增 PARTIAL/DEGRADED 工作流状态。

关键边界：

- 4 核不等于可同时跑 4 个 Codex 会话，限制因素是整个进程树的内存，不是 API 请求数。
- 77 万累计输入 token 不等于 77 万 token 同时驻留；不能据此推导单进程 RSS 或认定 SDK 内存泄漏。
- 本地一个 `OpenAICodexRuntime` 持有共享 `AsyncCodex`，不能假定目前每个 job 都有一个独立 App Server。多个 thread 可共用同一 App Server，却各自带来会话/MCP 开销。
- “基础设施错误不扣业务预算”必须同时有独立、有上限的恢复预算，否则会制造持续启动、失败、重试的资源风暴。
- 下列数值是面向本机规格的**首发工程配置**，不是已经完成真实容量验收的结论。

## 2. 当前本地风险与证据

路径以仓库根目录为基准；行号是本次审查定位，实施时以函数名为准。

| 优先级 | 证据 | 风险与处理方向 |
|---|---|---|
| P0 | `codex_worker/jobs.py`：`submit()` 直接 `create_task(_run(...))` | 无共享准入；限制必须覆盖初始化、O4、W3 等所有调用者，不只 D2 |
| P0 | `workflows/codex_document2/orchestrator.py:85,109,193,478,578` | shell 默认 4；O0 candidate/review 的 gather 在 shell semaphore 外；只改 shell 参数漏掉 O0 |
| P0 | `ticker_initialization/research_adapter.py:336` | 初始化创建 D2 时没有传 shell 并发参数；须接 settings，不能只增加一个无效环境变量 |
| P0 | `codex_worker/jobs.py`：`cancel()` / `_run()` | interrupt 没有独立超时；cancel 提前发布终态，没有等 task/后代退出；finally 只移除 handle，无法证明资源已释放 |
| P0 | `codex_worker/sdk_runtime.py:92,268` | 单共享 SDK client；无运行槽隔离、线程淘汰、进程树清理和应用 shutdown 关闭协议 |
| P0 | `docker-compose.v2-production.yml`、`deploy/docker-compose.server.yml` | 实际生产入口没有 mem_limit/memswap_limit/cpus/pids_limit；旧 ticker-init overlay 已不是当前部署主入口 |
| P1 | `codex_worker/jobs.py`：`_jobs/_tasks/_events`、`_recover_snapshots()` | 无终态缓存淘汰；job 含 telemetry，事件又保存完整 job，查询 deep copy；重启扫描并载入全部历史 job，历史越多越重 |
| P1 | `codex_worker/app.py:74,90,294`、`workspace_store.py:54,96,144` | async 路由中同步 copytree/遍历/压缩；export 用 BytesIO 保存整个 ZIP；可同时产生事件循环阻塞、内存峰值和磁盘读放大 |
| P1 | `ticker_initialization/substeps.py`：异常分支；`codex_runtime/recovery.py:24` | transient 目前仍扣同一个 ordinal，额外只 sleep 1 秒；排队、基础设施故障与业务执行预算尚未分离 |
| P1 | `codex_runtime/client.py`：`run()` | HTTP 失联转 WorkerUnavailable；服务端 job 可能仍在运行。必须先认领同一个 job，不能凭网络异常换 execution_id 再开一份 |
| P1 | `settings.py:687`、`persistent_runtime_v2/service.py` 的 W3 lease | W3 上限被类型固定为 Literal[5]，不是可设为 2 的普通配置；该 ticker lease 限制也不等于全局 Worker 执行上限 |
| P1 | `workflows/codex_document1/node_runner.py:241`、`sdk_runtime.py:134` | D1 部分节点允许内置 subagent；当前主要依赖 feature 与文本数量要求，顶层 job 上限不能自动覆盖嵌套 agent |

已有值得保留的行为：最新 D2 gather 已使用 `return_exceptions=True` 并分别保存成功成果；CDECR、D1、O2 的成功 checkpoint 不因 D2 失败而清空；Bus scheduler 已有源级 semaphore；服务器 overlay 已要求 CDECR 使用 `PREBUILT_REQUIRED`。不重写这些设计，也不把所有线程池/HTTP 请求统一压成 1。

事故中 “client has been closed” 与基础设施故障一致，但本地最新提交已修过并行 D2 失败结算；不能未经复现就断言此错误必然由某个 close 调用造成。部署镜像与本地工作区需要在后续发布时分别校验。

## 3. 全局准入：两个执行槽，不是两个协程

### 3.1 建议配置

以下均为**待新增或接线的配置**，不是当前设置环境变量就会生效。

| 设置 | 4c8g 默认值 | 含义 |
|---|---:|---|
| Worker execution capacity | 2 | 全服务共享，覆盖 STARTING、RUNNING、CLEANING；资源未收完不返还槽 |
| D2 max_shell_concurrency | 2 | 两条 shell 链并行，各 shell 内 state→realization→gaps→finalization 不变 |
| D2 O0 turn concurrency | 2 | candidate/review 使用同一个 D2 turn 限制，不各自获得两份额度 |
| 同 thread 活跃 turn | 1 | `(CODEX_HOME, thread_id)` 排他；新建阶段先锁 workspace/逻辑会话身份 |
| 服务器 active initialization | 1 | 服务器级重初始化活动上限；不同 ticker 请求可以持久排队，现有同 ticker 重复请求仍拒绝 |
| W3 ticker concurrency | 2 | 改 Literal[5] 为有界 int；仍受全局 Worker 两槽限制，不是另加两槽 |
| 启动握手并发 | 1 | 仅限制 session/MCP 冷启动阶段，不串行执行整个 turn |
| Worker 待执行请求 | 64 | 完整输入落盘、内存仅保存 ID；超过上限返回可识别的 capacity busy + Retry-After |
| 同时 snapshot/export | 1 | 独立 I/O 队列；不能持有 Codex 槽等待另一个需要 Codex 槽的操作 |

D1/D2/W3 均通过同一 Worker admission。保留单实例 Worker（一个 ASGI 进程）并加数据目录 owner lock；不要用 `uvicorn --workers 2` 或两个容器，各自拿一个 Semaphore(2) 后误称全局为 2。本期不引入 Redis/Celery/Kubernetes。

### 3.2 请求持久化与恢复

1. POST 先写 durable job + 完整请求 + 请求 hash + FIFO 序号，返回 queued；重复幂等请求返回原 job。
2. dispatcher 只将真正取得容量的请求送入 executor；排队不得启动 SDK、Data MCP，也不得预先生成完整 workspace 快照。
3. 获取槽后先写 `started_at` / owner generation，再启动 session。执行时限从实际启动计，不把排队算进 1800 秒研究预算。
4. Worker 重启：queued 请求从原序号恢复，不再像当前实现一样统统变成 WORKER_RESTARTED；running 先认领落盘成果/原进程事实，不盲重发。
5. HTTP polling 超时只进入 transport reconnect：按原 idempotency/job 查询；服务端仍在执行时不扣业务重试、不创建新 job。
6. business attempt 与 execution reservation 分离：兼容现有 execution_id 审计，但 queued 记录不能消耗已执行次数；不能为此抹掉旧 ordinal 或篡改历史失败。

排队保持现有 workflow RUNNING/节点待执行语义，附 `wait_reason=RESOURCE_CAPACITY`、queued_at、queue_position、next_check_at。不是新增业务终态。延迟告警不自动把排队变 FAILED。租约在排队期间照常续写。

采用工作保持型调度：初始化独占时可以用满两槽；Runtime 有可执行请求时，下一个释放槽优先给 Runtime，并用老化防止后台饥饿（例如连续 3 次 Runtime 准入后让一个已等待的研究 turn 入场）。不空置一个“永久 Runtime 专用槽”，不抢占健康研究 turn。

这不保证已被两个长研究 turn 占满时的 Runtime 秒级响应。若未来要求严格实时 W3 SLA，应做独立 Worker/扩容，而不是在 8GB 上临时多开第三个重会话。

### 3.3 Codex 内置子代理

推荐服务器配置默认使用跨节点两路并行，关闭**额外嵌套** subagent；这只作用于 4c8g 执行策略，不修改研究内容、通用默认值或历史 thread。D2/O2/D3/O4/W3 本来就多数禁用，主要补 D1 漏洞。

保留受控开启方式：单个 root 最多 1 个 subagent，该 root **提前占用两份全局额度**，另一槽不再发 root job；禁止 root 先占 1 槽后等待子代理槽导致死锁。root、子代理及其 MCP 全部纳入同一 execution capsule 清理。空闲 capsule 必须退出，才能把额度借给内置子代理。

数量约束必须是 SDK/CLI 配置，不靠 prompt。官方当前文档将 `agents.max_threads` 作为 spawned-agent 并发设置的旧别名，且不包含 primary；本地锁定 `openai-codex==0.144.4`，实施时用该版本的配置/行为测试确认生效，不直接套用新版本键名。无需为本次修复先升级 SDK。[OpenAI 配置参考](https://developers.openai.com/codex/config-reference/)

## 4. 会话与进程：资源回收后才释放槽

### 4.1 两个轻量隔离 execution capsule

将当前共享单个 AsyncCodex 改为最多两个受 supervisor 管理的执行 capsule。每个 capsule 拥有独立 SDK client/App Server 及后代，默认只承载一个业务 thread。采用本地子进程和 PID/start-time/PGID 归属，不需要逐 job 启动 Docker，也不挂 Docker socket 给 Worker。

- 同一 shell/thread 的连续 turn 可复用 capsule，避免每个请求冷启动；保留最多 30 秒的空闲亲和，但有其它可执行任务时立即让位。
- 换业务 thread 前回收旧 capsule，再 `thread_resume` 读取持久 thread；不能让已完成的多个 thread/MCP 不断堆在一个 App Server 内。
- persisted thread 可以很多；限制的是 resident/active thread，不删除历史，不 archive/delete 研究线程，不破坏 D3/O2 同线程连续性。
- 默认最多两个 resident 业务 thread；空闲 capsule 也计内存。受控 subagent 模式合计仍只有两份 thread 额度。
- readiness 使用已有空闲 client 或一次短生命周期 probe；不能因为定期 healthcheck 创建第三个常驻 App Server。`/healthz` 只反映轻量控制面存活；队列满不是 unhealthy。

本地 SDK async 层实际使用 `asyncio.to_thread` 调用同步 transport；取消 Python await 不足以保证同步调用和 App Server 退出。本地 SDK close 终止直接 App Server 进程，也不是经验证的整个后代树清理。因此需要应用自己的 ownership/supervision，而非只在 finally 加一行 close。

官方 App Server 的 `thread/unsubscribe` 是解除订阅、随后延迟卸载；不能当立即释放内存的保证。`turn/interrupt` 与后台终端清理也不是同一操作。方案不依赖一个假设存在的立即 `thread/unload` RPC。[OpenAI App Server](https://developers.openai.com/codex/app-server/)

### 4.2 超时与取消协议

保留现有业务 timeout：通常 1800 秒，O4 等仍可使用其专门上限；不统一改成 5 分钟，也不按累计 token 达到某值直接杀任务。

1. 到时进入 `CLEANING` 执行子状态，立刻冻结新 turn；先保存已收到的 thread_id/turn_id 和证据路径。thread_start 成功即落 receipt，不等 handle.run 完成。
2. interrupt 最多等 5 秒；再等 turn 终态/合规 shutdown 10 秒。
3. 未退出则只对该 capsule 已登记的进程组 TERM，再等 5 秒后 KILL；回收 waitpid/确认后代归属清空。清理总目标不超过约 25 秒。
4. PID 需结合 start time，避免误杀 PID 复用；处理 subprocess 再建 session 的逃逸后代。能安全委派 cgroup 时用 job 子 cgroup 强化，否则用受限 Linux supervisor/subreaper + 后代跟踪；不得靠 `pkill python`、关闭整个共享 client 或杀其它健康 job 解决。
5. 控制 supervisor 独立于 SDK 事件循环。即使 sync SDK 调用卡住，外层仍能回收 capsule。长时间 D-state 暂时无法被 SIGKILL 回收时，**不谎报已释放槽、不新开替身**，隔离该槽并报警。
6. 成果已经完整落盘但最终响应丢失，优先现有 artifact/checksum/checkpoint 认领；不能因为基础设施分类无条件重做研究。

取消排队任务直接撤销 reservation，不启动进程。取消执行中任务先停止执行并回收，再发布 settled 终态；业务结果已成功时，单纯清理告警不反向作废成果，但该槽保持隔离直到清理结束。

## 5. 4c8g 容器预算

按事故实际约 **7.5 GiB 可用物理内存**计算，不按名义 8 GiB 全部分配。下表是建议的首发硬上限，不是预分配。

| 服务 | mem_limit | cpus 上限 | pids_limit |
|---|---:|---:|---:|
| codex-worker（含两个 capsule/MCP） | 3584m | 2.75 | 512 |
| v2-initialization（服务器仅 prebuilt CDECR） | 640m | 0.75 | 128 |
| v2-api | 384m | 0.75 | 128 |
| v2-scheduler | 512m | 1.00 | 128 |
| v2-message-bus | 256m | 0.50 | 96 |
| v2-o4 | 256m | 0.50 | 96 |
| v2-control | 192m | 0.50 | 64 |
| v2-projector | 256m | 0.50 | 64 |
| v2-delivery | 128m | 0.25 | 64 |
| v2-executor | 192m | 0.50 | 96 |
| v2-web | 64m | 0.25 | 64 |
| v2-migrate（非同时常驻） | 512m | 1.00 | 96 |

11 个常驻容器内存上限合计 **6464 MiB ≈ 6.31 GiB**，约余 1.19 GiB 给宿主机、Docker、SSH/Nginx、Gateway/桌面与额外缓存。Gateway/桌面实占未在本轮验证：上线前必须列入预算；若它们与宿主机超过剩余空间，先关闭不需常驻的桌面会话或将其移出本机，不能再把 Worker 放到 5–6 GiB。

轻量服务的数值需用当前生产镜像分别做无模型启动/空闲采样，目标留出其正常峰值约 30% 裕量；若 Python 导入峰值已超过某项，应在**同一总预算**中调配，而不是让它上线后反复 OOM。容器硬上限不是对单个 Codex turn 的 1.5 GiB 均分限制：一个较大 turn 可以借用第二槽空闲时的内存。

实现位置：在 `deploy/docker-compose.server.yml` 的 4c8g 服务覆盖中写可执行的 service-level 字段；基础生产 Compose 保留可配置入口。各常驻容器 `memswap_limit = mem_limit`，避免业务容器用满主机 swap；主机现有 2 GiB swap 保留供系统兜底，不执行全机 swapoff，也不通过加 swap 解决本次问题。保留 OOM killer，不设置 oom_kill_disable。Docker 的 memswap 是内存与 swap 的合计值，不能误设为“额外 swap 数量”。[Docker 资源限制](https://docs.docker.com/engine/containers/resource_constraints/)

CPU caps 可相加超过 4，因为不是专用核预留；Worker 的 2.75 核只是限制其 CPU 饥饿影响，不证明控制面获得了硬保留。无需把每个 Python 服务限制成 0.1 核。`pids_limit` 也包含线程，不能按“两个 job”设为 16。保留当前 init:true、日志轮转和 stop_grace_period。[Compose 服务参数](https://docs.docker.com/reference/compose-file/services/)

本配置继续使用 `PREBUILT_REQUIRED`，不把 CDECR 本地大批推理偷偷塞进 640m 的初始化容器；未来要在同机运行 CDECR，需单列执行池和预算，不从本次 Codex 两槽规则推导其安全容量。

## 6. 轻量压力反馈：只控制入场，极端时局部回收

每 5 秒采样自身 cgroup `memory.current/events/swap.current`、PID、进程树 RSS，以及可用的主机 MemAvailable、memory/io PSI。主机指标需确认采样命名空间；没有可信主机 PSI 时使用已验证的 cgroup 指标加静态上限，不因一个可选指标缺失让整个 workflow 停摆。

| 档位 | 初始阈值（待容量测试微调） | 行为 |
|---|---|---|
| 常态 | 不满足下面条件 | 容量允许即开满两槽 |
| 暂停新入场 | Worker ≥2.8 GiB；或主机可用 <768 MiB；或 memory PSI full avg10 ≥5% 持续 10 秒 | 健康在途继续；优先回收空闲 capsule；排队无失败/无预算消耗 |
| 恢复入场 | Worker <2.4 GiB、主机可用 >1 GiB、memory full <1%，可获取的指标连续稳定 30 秒 | 自动恢复最多两槽，不需人工点恢复 |
| 极端压力 | Worker ≥3.25 GiB 且仍上涨/存在持续 stall；或主机可用 <384 MiB 且持续 memory stall | 先回收空闲资源；仍无改善才中断一个可恢复、低优先级且占用最大的 capsule，按基础设施恢复处理 |

单纯“内存使用率 80%”不触发杀任务；内存统计要区分 anon、可回收文件缓存及增长趋势。CPU 高、短暂 I/O 高、仅有历史 swap 使用量都不直接判业务失败。PSI 的 full 表示非空闲任务同时受阻，可以帮助区分正常繁忙和抖动；阈值是本方案选择，不是内核推荐默认值。[Linux PSI](https://www.kernel.org/doc/html/latest/accounting/psi.html)

不额外设置过低 memory.high，让内核持续强制回收后重演卡顿。硬上限、入场阈值、异常回收各司其职；`memory.max` 本身也不保证不会短暂 reclaim，应用必须提前反馈。[cgroup v2 内存控制](https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html)

防止压力恢复无限循环：同一 job 最多允许两次基础设施重新执行；同一 Worker 10 分钟内连续 3 次压力中断则进入服务级冷却，停止新 admission、保留状态并告警，不自动反复重启容器。

## 7. 基础设施恢复与业务预算分离

保持业务“初次 + 一次失败重试”的已确认约定。增加持久化 `infra_recovery_count`、`failure_class`、`next_retry_at`、`worker_generation` 和实际 dispatch 记录，不能简单把 ordinal 减回去。

| 情形 | 处理 |
|---|---|
| queued / capacity busy /压力等待 | 未开始执行，不消耗任何执行重试；正常排队 |
| HTTP 临时失联，但原 job 存活 | 原身份重连查询；不算一次重新执行 |
| Worker 重启、可证明的 cgroup OOM/压力回收、已确认的 transport/MCP 连接故障 | 先认领成果和确认旧执行停止，再用独立 infra 预算恢复同一个逻辑节点 |
| schema/业务语义校验失败、程序逻辑错误、正常资源下反复无有效成果 | 原业务预算，不因带有 timeout/client 字样自动豁免 |
| auth 失效、模型不可用、缺少工具权限、长期固定配置错误 | 不自动无限重试；明确基础设施原因并转人工恢复 |

每节点基础设施重新执行默认最多 **2 次**，退避 **30 秒、120 秒**；可用性持续未恢复 30 分钟则保留成果并终止为 FAILED + manual_resume_required，原因是基础设施预算/时限耗尽，而不是研究质量失败。冷却等待本身不重复花钱调用模型。

必须以结构化证据分类：Worker generation 变化、退出码、cgroup OOM 增量、cleanup reason、具体 MCP transport 事件。不能把所有 CODEX_TURN_TIMEOUT 或 RuntimeError 都视为无责基础设施错误。资源健康但 1800 秒仍未完成的 turn，先走成果恢复，再按执行失败处理。

这些语义同时接入 Worker、HTTP client、内部 durable decorator 与父容器恢复；不能只改 Worker 错误码，父层仍旧两次扣完。对旧 MU 已失败记录不自动追溯恢复预算，后续人工 resume 创建新的恢复代次，历史保持不变。

O4 注册/交付、activation、Runtime 输出等有副作用的操作仍必须按原稳定身份查询目标事实；“infra retry”不是允许重放未知外部副作用。已有交易意图与执行器幂等关系不改，不能因为恢复研究会话重新下发交易请求。

## 8. 内存驻留、MCP 与磁盘读放大

### 8.1 Worker 内存有界

- `_tasks` 完成后移除；终态 `_jobs` 改按数量与字节双限的 LRU（建议 128 个且合计 32 MiB），完整结果在磁盘。
- `_events` 改有界内存 tail，完整事件按序落 JSONL；状态事件只放 job 摘要，不重复嵌入完整 telemetry/final_response。重连从持久事件续读，不能重用 sequence=0 破坏游标。
- 终态 job 查询不默认携带全部 loop event；保留业务需要的 final_response，telemetry 用引用或显式详情接口，并同步调整调用者。
- 重启从轻量持久索引加载 active/queued job；历史按需读取，不一次把所有 `audit/jobs/*.json` 连同 telemetry 放入内存。迁移索引可分批建立，保留旧 JSON 权威记录。
- SDK `_run.py` 本身也收集 items；应用结果流应逐条投影/落盘，内存保留最终回答与有界统计。只优化 Manager 缓存不能声称已经限制 SDK 在途 items。

### 8.2 I/O 与 MCP

- snapshot/fork/hash/ZIP 移入受控线程或独立 I/O 执行器，避免直接阻塞 ASGI；同一 workspace 的破坏性变更仍保留排他。
- export 改磁盘临时文件或受限 spool + chunked response，不用整个 ZIP 的 BytesIO；客户端断开后关闭并清理该临时文件，不删成果。
- 本期保留完整、不可变 PRE-NODE 快照与 SHA 校验，只限制同时复制数。绝不拿可写 hardlink 替代真实快照；文件系统支持时才可用安全 reflink。内容寻址增量快照可后续优化，不把它变成当前发布前提。
- Data MCP 按 execution capsule 合计最多 4 个在途外部调用，昂贵 browser/PDF 抽取最多 1 个；两槽总量自然不超过 8/2。同 capsule 下多个 MCP 进程需共享本地预算，不能各设 Semaphore(4) 后相乘。控制/取消/readiness 不排在研究工具队列后。
- 返回大正文用现有 observation/文件引用和分页；不通过截断必要证据降低研究质量。不本轮统一削减所有非 Codex HTTP 并发；Bus 保留已有源级限制。
- 增加 1 个低优先级 snapshot/export 通道，不先加低磁盘 B/s 硬限速，因为数据库/fsync 共用磁盘，粗暴限速可能伤害 checkpoint 和恢复。

## 9. 实施拆分与验证标准

三个开发批次可以独立提交，但 **A+B 合并验证后才恢复真实 MU**；只打容器上限然后让旧无限并发 Worker 继续跑，不是完成修复。

### A：先截断资源峰值

1. settings 与服务器 overlay 接线：两槽、D2=2、W3 可配置、容器资源字段。
2. durable admission queue、单实例所有权、同 thread 排他、启动/执行/排队三种计时。
3. slot 独立执行 capsule、超时清理上限、thread/turn 早期 receipt、shutdown 协议。
4. 稳定全局配额，防内置 subagent 绕过；不依赖每个调用方自觉限流。

### B：恢复与持续运行

1. HTTP 失联重附原 job；queued 重启恢复；infra 预算独立且有界。
2. 压力暂停/自动恢复、局部异常回收、健康接口与研究执行隔离。
3. job/events/telemetry 有界；snapshot/export 离开事件循环，磁盘流式输出。
4. 记录每个 job 的 PID/PGID、thread、node、排队时间、开始时间、资源峰值、退出原因、cleanup 耗时。每 5 秒资源采样，日志轮转 7 天/总大小上限，不记录凭证或完整工具输入。

### C：容量校准与发布（需后续明确执行授权）

先做**不调用模型的真实子进程负载测试**，再做受控真实业务验收；本次仅列标准，没有运行：

- 20 个 fake job 突发提交，始终最多 2 个执行槽；不同 workflow 也不能绕过。排队期间零 SDK start、零业务预算消耗。
- 两个有持续内存占用的测试子进程并行，逐步靠近阈值；控制 API、status/cancel 与心跳仍能响应；压力解除后自动恢复两槽。
- 模拟不可响应的 interrupt、START_THREAD 卡死、MCP 超时及子孙进程逃逸，确认约 25 秒清理路径、资源回收后才放行下一 job；D-state 情景不得虚假释放槽。
- queued/running/replied-but-not-settled 三类重启测试；同 idempotency key 无重复执行；已成功 D2 shell/turn 不重跑。
- 连续至少 100 个短 turn，并重复查询历史 job/导出 workspace：空闲时 resident session 不随历史 job 数增长；进程数、Worker RSS 和 FD 回落到稳定平台，无单调增长趋势。
- 模拟纯网络故障不会多开第二份同节点任务；infra 恢复最多 2 次，业务失败仍最多一次重试；未知异常不能进入无限豁免。
- A/B 吞吐比较：资源健康时两槽均能被有效利用；不存在固定长冷却、每节点 readiness 全审查、常态单线程等退化。无需承诺相对“故障前 4 并发”一定快多少，事故状态不是有效吞吐基准。
- 用无秘密输出的 Compose 静态解析，并在后续服务器上核对实际 Memory/MemorySwap/NanoCpus/PidsLimit；不能仅以 YAML 存在证明生效。
- 真实 MU 首先检查部署镜像与配置、原执行确已停止及 checkpoint 完整，再人工恢复失败 D2 节点；保留 D1/CDECR/O2、已完成 shell。不得用整 ticker reinitialize 验收恢复。
- 实测控制 API p95 目标 <1 秒、健康检查无连续超时、Worker swap 增量 0、主机无持续 memory full stall；真实单 turn 若仍超过 3.5 GiB，应分析会话/工具 payload 或移出本机，不能假装“两并发”保证任意大任务能跑。

## 10. 不采用的极端做法

不全 workflow 串行；不砍 shell 数、模型质量或研究步骤；不大幅缩短 turn timeout；不把 CPU 利用率高等同故障；不对每节点增加全局质量/readiness gate；不扩大 swap；不关闭 OOM killer；不借资源异常无限自动重试；不删 thread/checkpoint 换内存；不在修复中重构消息总线和全部持久化调度。

**推荐落地口径：常态双并行、全局统一计费容量、可回收会话、低开销压力反馈、明确宿主机余量、独立且有限的基础设施恢复。**
