# COHR 初始化阻塞与 Guardian 首轮失败排查

核查时间：2026-10-17 18:33–18:35（北京时间）。远端：`doxagent-sg`。
本轮完成本地代码阅读、生产只读取证和故障复盘；未改生产代码、认证文件、业务账本或运行状态。

## 结论

COHR 当前是持久化 `FAILED / D3`，不是仍有初始化进程推进的 RUNNING。
原始触发是 S0003 的 Data MCP 启动握手超时，Worker 的两次基础设施恢复均未救回。
D3 并发聚合提前抛错，研究适配器关闭共享 HTTP client，造成其余 Shell 的结果无法正常收回。
三个兄弟 Worker job 后来成功，但初始化节点仍失败。

自动修复没有成功：Repair Agent 独立登录凭据陈旧，三次 turn 均发生 routing discovery 401，
Guardian 将 incident 置为 HUMAN_REQUIRED；没有产生候选代码、报告、镜像或修复 Executor。
所以只记一次 repair round；后续既不会自动推进终态 incident，也不会给同一 initialization 新建 incident。

## 运行链及守护边界

1. V2 Control 持久化操作，初始化 Worker 从 `ticker_operations` 领取带 fencing 的租约；
   `InitializationService.run_claimed` 并行驱动节点和心跳，成功结果逐节点落盘。
2. 研究适配器调用 D1、CDECR/O2、D2、D3；研究请求通过 HTTP 发给 Codex Worker。
   WorkerJobManager 管理队列、job 快照、执行和回执；CapsuleRuntime 管理 SDK 子进程与所属进程树，
   清理失败时隔离执行槽。MCP 初始化失败进入基础设施恢复，不直接当作业务研究失败重跑。
   初次执行后最多两次基础设施恢复，等待分别为 30/120 秒。
3. 初始化成功后才提交 activation，并通过 bus.ready/runtime.ready 转入持久化 Runtime。
   Runtime Coordinator、任务/effect 租约和 worker 回执负责之后的消息处理与维护。
4. Initialization Guardian 是另一层控制器，只接管持久化 FAILED 的初始化，
   不接管 RUNNING。独立工作树、独立 Codex Home 的 Agent 产出候选，经过验证/构建、
   Control 排队与 repair Executor 真实续跑后才算完成一轮业务修复。
   每个具体失败节点最多三轮；同轮 Agent 的基础设施重试不增加业务轮数。

本地已阅读上述入口及相关 schema/repository；生产 Guardian 的 guardian.py 和 repository.py
SHA-256 与本地一致；生产 D3 的关键 gather 和 adapter finally 已直接读取确认。
不同服务使用不同镜像，未以远端 checkout HEAD 代替所有服务运行版本。

## 身份与部署

- initialization：`init-cohr-7ba3295fc06b4093a3251b8bf024d3ab`
- incident：`repair-c814d8dc0f6a405c989b4e49f8dea1a4`
- round：`round-d19c47e22abb494bac60d0e7e184e0b3`
- 初始化镜像：`doxagent-v2:main-28053cde`
- Guardian 镜像：`doxagent-initialization-guardian:main-618fd9c5`，核查时仍运行。
- Worker 镜像：`doxagent-v2:main-3c30365a`
- 初始化 state_seq=884，manual_resume_required=true；COHR 无活动 ticker operation。

## 时间线（均为 2026-10-17 北京时间）

| 时间 | 事实 |
|---|---|
| 06:02:56 | 创建 COHR 初始化，cutoff 固定于此轮输入 |
| 08:32:13 | D2 成功结算；包含 PARTIAL/降级质量注释，不能等同完整研究质量通过 |
| 08:32:17 | D3 开始，后续并发四个 Shell Discovery |
| 08:38:00 | S0003 Worker job 基础设施恢复耗尽，最终错误为 required Data MCP 握手约 20 秒超时 |
| 08:38:02–04 | S0003、S0004、S0002 以及父 D3 依次失败结算 |
| 08:38:10 | 初始化落盘 FAILED，错误 retry budget exhausted: d3 |
| 08:38:36 | Guardian 创建 incident |
| 08:38:39 | 创建第 1 轮，四个 Discovery 节点预算各消耗 1 |
| 08:39:22 | S0001 Worker job 最终 succeeded，初始化节点未成功结算 |
| 08:39:29 | Repair Agent 第一次 turn 出现 routing discovery 401 |
| 08:40:06 | S0002 Worker job 最终 succeeded |
| 08:40:46 | Repair Agent 第二次 turn 出现相同 401 |
| 08:41:26 | S0004 Worker job 最终 succeeded |
| 08:41:57 | Repair Agent 第三次 turn 出现相同 401 |
| 08:42:07 | Guardian 设置 HUMAN_REQUIRED / RECORD，last_error=REPAIR_AGENT_INFRA_RETRIES_EXHAUSTED:1 |

## 原始阻塞的因果链

S0003 job `fac614d1cfbfdc05e068e7f1238aeb2c7831f5dc7f0b82758944613a7ba5de73`
最终为 `WORKER_INFRA_RECOVERY_EXHAUSTED`，infra_recovery_count=2，尚未创建 thread/turn。
错误明确来自 `START_THREAD` 中 required MCP `data` 的启动握手，不是 1800 秒模型研究超时。
sdk_runtime.py 对普通 D3 Discovery 的 Data MCP 启动预算为 20 秒。

`Document3OrchestratorV21.initialize()` 的 Shell Discovery 使用默认 `asyncio.gather`。
一个 coroutine 抛异常后 gather 会提前向外抛出，但兄弟 coroutine 继续运行。
`ResearchInitializationAdapter.execute()` 的 finally 随即 `await worker.aclose()`。
这与 S0002/S0004 的 `Cannot send a request, as the client has been closed` 及其后续 Worker 成功时间吻合。
S0001 留下 `parent stopped before internal stage settlement`。

初始化账本与实际 Worker 状态对照：

| Shell | 初始化节点 | Worker job 最终状态 | infra_recovery_count |
|---|---|---|---|
| S0001 | FAILED，parent stopped | succeeded | 1 |
| S0002 | FAILED，client closed | succeeded | 2 |
| S0003 | FAILED，MCP recovery exhausted | failed | 2 |
| S0004 | FAILED，client closed | succeeded | 2 |

三个成功 job 的 cold_start_ms 分别为 37992、20559、29980。这是 capsule 整体冷启动耗时，
**不是** Data MCP 独立握手测量，不能据此断言 MCP 自身耗时或直接认定 CPU/内存压力。
现有证据确认了 20 秒握手失败及聚合收尾缺陷；未保存足够的 D3 MCP 分阶段启动诊断，
无法进一步确定 S0003 为什么三次均未在该预算内完成。不能仅把超时调大就声称根因修复。

## 第一次非例行维护做了什么

Guardian 创建独立分支/工作树，生成 failure-context.json，复制独立认证模板，
启动并恢复同一个 Repair Agent thread，共三个 turn。
三个 turn 的 401 已从持久会话逐条确认，并非只根据最后一次 agent.log 推断。

独立模板及 incident 认证文件 last_refresh 均为 2026-09-25T23:25:22Z，
access token 的到期时间为 2026-10-05T23:25:21Z；核查时二者内容 hash 相同，未更新。
生产 Worker 的认证已于 10 月 15 日刷新，access token 至 10 月 25 日有效；
它和 Repair Agent 不共享认证文件，所以正常 Worker 能运行不能证明 Repair Agent 可用。
已过期的独立凭据与三次实际 401 构成认证通道故障证据；尚未做重新登录/刷新验证，
不额外断言 refresh token 的失效原因。取证仅保存时间等元数据，不输出凭据内容。

该轮 repair_commit、image_id、control_operation_id、executor_container、result 全为空，
tests 为空，无 agent-report.json；独立工作树 git status --short 为空。
不存在成功修复或真实续跑的证据。

## 为什么没有第二次

`Guardian._collect_agent()` 将非零退出记入当前 round 的 agent_infra_retries；达到 3
便将 incident 设置为 HUMAN_REQUIRED / RECORD。`tick()` 仅推进 ACTIVE incident。
`RepairRepository.failed_candidates()` 排除已有 incident 的 initialization，故下一次扫描
也不会为 COHR 重新开事件。不是每节点三轮预算耗尽：四个节点目前各仅消耗一轮。

Overview 指标按唯一 repair round 计数，不按 Agent turn、重试次数或修复成功次数计数，
因此显示 1 与持久事实一致。另有可观测性缺陷：incident 已 HUMAN_REQUIRED，
round.status 却还保留 AGENT_RUNNING；不能把该字段理解为仍有 Agent 在运行。
该轮没有 issue entry；agent.log 每次覆盖，仅保留末次退出，历史错误要从会话取回。

## 后续修复的具体范围（本轮未实施）

1. D3 并发阶段在传出失败前等待所有已启动兄弟结算，保留成功结果及原始异常，
   再让适配器关闭 client；检查 Discovery、owner queue 等相同聚合点。
   回归应覆盖一项基础设施失败、兄弟延迟成功，验证不会 client closed、不会丢成功回执。
2. 为 D3 MCP 冷启动补足分阶段诊断，结合实际握手验证预算；不要放宽 required/隔离契约。
3. 更新独立 Repair Agent 的有效登录来源并验证实际 turn；保留其独立 CODEX_HOME 与权限边界。
   仅更换公共模板不会覆盖已存在的 incident auth.json，需处理该 incident 的独立副本。
4. 为现有 HUMAN_REQUIRED 事件提供有审计的同轮恢复，保留 thread/turn 及节点预算；
   不通过重开初始化或清空预算来掩盖故障。认证失败重试不应伪装成新业务修复轮。
5. 恢复 COHR 时先对账三个已成功 Worker job，保留原初始化身份、cutoff、成功节点和输入；
   真实续跑验证应覆盖 S0003、D3 后续、O4、activation 和 runtime.ready。
6. Agent 基础设施重试耗尽时同步 round 的终态并落 issue 记录，避免 AGENT_RUNNING 的误导。

## 证据文件

- `eval/cohr_initialization_audit_20261017/evidence.json`：初始化/修复账本、Worker job 摘要、源码 hash。
- `agent.log`：最后一次 Repair Agent 退出 traceback。
- `agent_auth_errors.jsonl`：同一持久 thread 内三次 401 的时间与会话文件名。
- `auth_metadata.json`：独立模板、incident、生产 Worker 认证到期时间，无 token 内容。
- `worker_recovery_events.json`：四个 job 的恢复排队及最终失败事件。
- `collect.py`：使用 SQLite mode=ro 的主要证据采集器。

核查方式为生产 SQLite 只读查询、日志/工作树状态和源码读取。未执行业务修复、
重新登录、人工 resume、容器重启或删除操作；因此本轮没有生产修复成功的验收结论。
