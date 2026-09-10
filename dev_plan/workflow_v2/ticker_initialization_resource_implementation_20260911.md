# Workflow V2：4 核 8GB 资源治理实施记录

## 交付范围

本轮落实本地代码与服务器 overlay，不部署、不连接服务器、不恢复 MU、不执行真实 Codex/模型或负载验收。按用户最新要求，只保留直接相关的必要测试。不能用本地测试通过推断服务器上的 cgroup 已生效，也不能保证任何单个超大研究任务都能在 3.5 GiB 内完成。

常态维持 **两路执行**，不减少 shell 数、研究步骤、模型质量或原有 turn 时间预算。容量不足是排队，不是新的质量 gate；PARTIAL/DEGRADED 的既有非阻塞编排口径不变。

## 已实现

| 范围 | 实施内容 |
| --- | --- |
| 全局准入 | 单 Worker 进程及目录 OS 锁；SQLite 持久请求/状态/事件；默认 2 个槽、64 个等待 job；STARTING/RUNNING/CLEANING 都占槽；相同 thread 排他；Runtime 优先但最多连续 3 个后让出研究机会 |
| 节点并发 | D2 shell 和 O0 各最多 2 路；W3 ticker 并发变为可配置，服务器设为 2；初始化 Worker 目录锁防止多进程并行消费 |
| 内置 agent | 服务器默认关闭；可选 1 个子 agent 时主任务预占 2 槽，SDK 同时写入 feature、max_threads、max_depth，不只靠提示词 |
| 执行隔离 | 独立 SDK capsule，按 thread 复用；换线程时受两实例上限约束回收旧空闲 capsule；空闲 30 秒回收，不删除 thread 历史；readiness 也计入资源容量，满载时不额外启动 SDK |
| 进程清理 | interrupt 最多 5 秒，优雅关闭最多 10 秒，再 TERM/KILL 各 5 秒；Linux 按随机 capsule 身份、PID start time、私有进程组回收，检查脱离进程组但保留所属身份的后代；未确认清理则隔离槽，不能放行重叠重试 |
| 崩溃恢复 | 请求在确认接收前持久化；thread/turn 尽早回写；子进程将完成结果 fsync/原子提交后再通知父进程；重启先清旧执行再调度，并认领已提交结果；queued 不因重启消耗重试 |
| 故障预算 | 进程退出、确认资源压力、启动期 MCP 握手不可用使用独立 2 次基础设施恢复，等待 30/120 秒；耗尽要求人工恢复，不继续透支业务重试；普通研究失败/无压力证据的普通超时不被普遍豁免 |
| 断线重附 | POST 重试保持同一 idempotency key；已知 job 只重新 GET；429 为容量等待；连续失联 30 分钟结束自动等待并要求人工核对同一 job，不生成新执行 |
| 压力治理 | 每 5 秒采样 cgroup v2/宿主机内存、swap、PSI；2.8 GiB/宿主机可用不足 768 MiB/持续 full stall 暂停新准入；低于 2.4 GiB、可用超过 1 GiB、full<1 持续 30 秒后恢复；严重压力先清空闲，再回收低优先级且较大的一个任务；10 分钟内 3 次压力中断进入 10 分钟冷却，记录持久化 |
| 内存与审计 | 不保留全量历史 job Python 缓存；事件分页续读；SDK 使用流式消费，不收集完整 items；内存只留最近 256 条紧凑事件、64 条失败、5 条慢步骤及最终回答，完整紧凑 loop 记录到磁盘 |
| MCP | 同 capsule 的 MCP 进程通过 OS 文件锁共享 4 个外部调用槽、1 个昂贵抽取槽；PDF 按响应内容识别后限流；工具取消不提前释放仍运行的阻塞线程；控制/观察读取绕过外部工具等待 |
| 文件 I/O | snapshot/fork/hash/publish/ZIP 走单路后台 I/O；普通文件请求也避免在事件循环等待 workspace 锁；取消必须等实际 I/O 完成才放槽；ZIP 使用磁盘临时文件和分块返回，保留完整不可变 PRE-NODE 快照 |

## 服务器配置

实际生产组合为 `docker-compose.v2-production.yml` 与 `deploy/docker-compose.server.yml`。不要只应用旧 ticker-init overlay。

| 服务 | 内存上限 MiB | CPU 上限 | PID 上限 |
| --- | ---: | ---: | ---: |
| codex-worker | 3584 | 2.75 | 512 |
| v2-initialization | 640 | 0.75 | 128 |
| v2-api | 384 | 0.75 | 128 |
| v2-scheduler | 512 | 1 | 128 |
| v2-message-bus | 256 | 0.5 | 96 |
| v2-o4 | 256 | 0.5 | 96 |
| v2-control | 192 | 0.5 | 64 |
| v2-projector | 256 | 0.5 | 64 |
| v2-delivery | 128 | 0.25 | 64 |
| v2-executor | 192 | 0.5 | 96 |
| v2-web | 64 | 0.25 | 64 |

常驻上限合计 6464 MiB。migration 另为 512 MiB/1 CPU/96 PID，依赖关系要求迁移完成再启动常驻服务。每个服务 `memswap_limit == mem_limit`，限制容器 swap，不执行宿主机 swapoff。CPU 是竞争时的上限，不是每个服务的专属预留，因此不按 CPU 上限简单求和理解为超卖错误。

环境变量：

- `DOXAGENT_CODEX_WORKER_CAPACITY=2`
- `DOXAGENT_CODEX_WORKER_QUEUE_LIMIT=64`
- `DOXAGENT_CODEX_WORKER_SUBAGENTS=0`
- `DOXAGENT_CODEX_WORKER_PRESSURE_ENABLED=true`
- `DOXAGENT_CODEX_D2_MAX_CONCURRENCY=2`
- `DOXAGENT_PERSISTENT_RUNTIME_V2_W3_MAX_TICKER_CONCURRENCY=2`

## 运维可见性与存储

认证后的 `GET /v1/resources` 返回执行容量、占槽、排队数、压力、冷却、dispatcher 是否运行及 generation。`GET /v1/jobs/{id}` 增加执行阶段、等待原因、基础设施恢复次数、身份/排队/清理信息。

Worker 根目录新增 `worker-jobs.sqlite3`、`worker-results/`、`capsules/` 以及容量有界的 `resource-samples.jsonl`。原 workspace `audit/jobs/*.json` 保留为兼容审计副本；升级时流式导入旧记录，旧 active JSON 没有可重放请求时要求人工恢复，不虚构请求。**新的 SQLite 是调度权威源**，不能单独删掉索引然后把旧 JSON 当作完整新队列恢复。

与草案的具体化差异：没有引入 128 项 LRU，而是直接按需读索引；持久事件使用 SQLite 序列分页，不再维护第二份内存完整事件列表。资源日志采用总量约 16 MiB 的轮转，未承诺固定保留 7 天；完整业务审计不随资源日志轮转。原始 SDK stderr 不复制到无限增长日志，结构化失败仍进入 job receipt。

PRE-NODE 快照仍在节点调用冻结时执行，而不是移到模型执行之后；它使用独立单路 I/O 通道，可能早于 Codex job 入队。这保留独立重跑的精确快照语义，不能把“排队不启动 SDK”扩张为“整个初始化等待期间完全不发生磁盘 I/O”。

## 必要验证与未执行边界

本轮最终必要测试组：`test_worker_resource_budget.py`、`test_ticker_initialization_worker_jobs.py`、`test_ticker_initialization_worker_snapshots.py`，合计 **18 passed**；目标代码 Ruff 和 Linux 目标类型检查另行核验。早期 D2 回归中与现有 fresh-attempt MCP 行为不符的旧 thread 断言已同步，不为通过测试恢复旧 capability 复用。

直接相关离线用例覆盖 20 个 fake job/双槽、同 thread 排他、满队列、子 agent 加权、取消清理/隔离、基础设施预算、queued 重启与结果认领、压力滞回、I/O 取消、MCP 锁、SDK 流式有界 tail、readiness 不启动第三 SDK、capsule thread 复用、HTTP 重附、父 workflow 人工恢复及服务器预算合计。

未执行 Linux 真实进程树/D-state 故障注入、100 turn RSS/FD 曲线、4 核 8GB 容器负载、真实 MCP/Codex 子 agent 启动、控制 API p95 或真实 MU 验收；这些属于原方案 C 的部署/容量校准，不以 mock 测试替代。Windows 的本地测试也不能证明 Linux cgroup 与进程树回收已经线上验证。

后续部署需要明确核对实际 Memory/MemorySwap/NanoCpus/PidsLimit、镜像版本、单 Worker 所有权和旧执行停止状态；之后仅人工恢复原失败节点，不能默认整 ticker 重跑，也不改写历史 MU 已消耗预算。
