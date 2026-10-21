# DoxAgent 生产 CPU 复审与 4C → 8C 决策

日期：2026-10-21，Asia/Shanghai。范围：只读生产调查、代码审查、容量与收益判断。本轮未修改业务代码、发布、重启业务服务、清理生产数据或执行服务器升级。

## 1. 决策

**建议升级到 8C，安排在下一次维护窗口；同时实施下文三项优先级最高的小范围优化，不把大规模重构作为升级前置条件。** 按用户提供的成本条件，服务器总成本增加 50%。这是容量和响应稳定性决策，不是承诺任务耗时减半。

依据：

1. 最近完整约 24 小时，98.61% 的采样时段 CPU 忙碌度 ≥95%；平均实际执行 CPU 为 94.53%，另有 3.14% iowait。四核长期接近耗尽，不只是偶发峰值。
2. 排查结束前，移除本次诊断额外进程、停止采样 profiler 后，再独立采样约 60 秒，实际执行约 98.65%、iowait 0.17%。CPU PSI some 的区间差分约 80%，证明存在真实执行排队。不是仅凭云监控百分比作判断。
3. 业务目前仍能运行，Worker 没有普遍排长队，Read 也未持续落后；但采集和调度已有秒级额外耗时、真实数据库锁错误，且缺少吸收新业务和盘中峰值的容量。
4. 存在低侵入优化空间，规划量级约 **0.3–0.6 核**，相当于四核约 **7.5–15 个百分点**的容量；这是工程估算，尚未做上线前后同负载对照。收益值得做，但不能可靠保证四核长期宽裕。
5. 当前负载横跨独立 Python 进程、浏览器进程及多个能释放 GIL 的工作线程，增加核心能够被使用。不是一个只会占满单核的进程导致整机显示 99%。

**不建议继续以“先优化到不够再考虑扩容”为长期策略。** 可以先完成这些小改再进入同一维护窗口升级，但不值得为了省下 50% 的机器成本，继续把生产运行在几乎没有 CPU 余量的状态，也不值得为了维持四核而改变健康业务的并发和采集覆盖。

## 2. 数据口径与生产身份

主要证据目录：[eval/cpu_capacity_decision_20261021](../eval/cpu_capacity_decision_20261021/)。

- 历史：系统 sysstat；最新完整样本截至北京时间 18:10:01。所称“最近 24 小时”是约 2026-10-20 18:10 至 10-21 18:10，144 个约十分钟区间；首个样本结束时间为 18:20。
- 现场：cgroup `usage_usec` 差分，最后独立约 60 秒窗口见 `live_final.json`。**1 核 = 每秒消耗 1 CPU 秒**；Docker 单核 100% 不等于四核整机 100%。
- CPU 等候：`/proc/pressure/cpu` 与线程 `/proc/PID/task/TID/schedstat`；CPU 配额检查未发现目标服务的 CFS throttling。
- 业务：只读 SQLite 标量、近期任务状态、Site Access 元数据和生产已有日志。无新研究、交易、浏览器导航或模型请求。
- 当前远端仓库 HEAD 为 `2f9ddcce`，与本地 HEAD 相同；自上一轮 CPU 发布后又有完整业务发布，不能只看旧镜像版本推断今天的负载。
- 针对六个本轮关键审查文件，部署中 Scheduler 的源码 SHA-256 与本地源码统一 LF 后匹配：`consumers.py`、Scheduler `service.py`、`calendar.py`、`journal.py`、Bus `scheduler.py` 和 IR `http.py`。没有把本地尚未部署的逻辑误当成生产逻辑。

诊断限制：没有完整的近一天逐容器历史核时，也没有升级 8C 的实际 A/B。短窗口线程栈只用于寻找代码路径，不按样本百分比直接换算整个服务 CPU。GIL 栈采样不能覆盖释放 GIL 的 SQLite/TLS 全部开销。普通 stack 中线程池和 socket 等待也不等于 CPU 热点。

一次初始 Worker 目录枚举超时，残留的本次诊断进程已经按 PID、命令、父进程、启动年龄和容器身份核实并终止；相关中间窗口不用于最终组件核数。最终窗口在其退出后重新采集。另一条无合适索引的 Site 查询触发只读进度预算中止，随后改为按 rowid 限量读取。失败查询没有用于业务结论。

## 3. CPU 紧张程度与分布

### 3.1 历史和资源瓶颈

| 指标 | 实测 |
| --- | ---: |
| 最近约 24h 平均忙碌度，包含 iowait | 97.67% |
| 最近约 24h 实际执行 CPU | 94.53% |
| 最近约 24h iowait / steal | 3.14% / 0% |
| 最近约 24h 忙碌度 ≥95% 的时间比例 | 98.61% |
| 十分钟样本点 runnable 数中位 / P95 / 最大 | 21 / 35 / 49 |
| 一分钟 load 中位 / P95 | 17.86 / 29.57 |
| 最终 60 秒忙碌度 / iowait | 98.83% / 0.17% |
| 当前可用内存 | 约 5.6 GiB |
| 当前系统盘可用 | 约 51 GiB |

CPU 是当前主要资源瓶颈。采样时内存 PSI 很低；swap 约 2 GiB 基本用满，说明有换出历史，不能据此断言此刻正在内存抖动。历史部分区间有 I/O 等待，但最终窗口并非磁盘 I/O 饱和或宿主机 steal 在制造假象。内存峰值和 SQLite 串行写入仍需要分别看待。

PSI some 约 80% 表示该比例的墙钟时间中至少有任务因 CPU 不足等待，不表示每个请求慢 80%，也不能按 21 runnable / 4 核推导需要 21 核。

### 3.2 最终现场核数

| 组件 | CPU 核数 | 四核容量占比 |
| --- | ---: | ---: |
| Chrome Supervisor，含原生 Chrome | 0.693 | 17.3% |
| Site Access，含受管 Chromium、子进程与访问服务 | 0.647 | 16.2% |
| Runtime Scheduler | 0.623 | 15.6% |
| Message Bus | 0.590 | 14.7% |
| Codex Worker，含其业务子进程 | 0.588 | 14.7% |
| Read Projector | 0.235 | 5.9% |
| Trade Executor | 0.167 | 4.2% |
| Transport Maintenance | 0.090 | 2.2% |
| CDECR Executor | 0.072 | 1.8% |
| Control / Initialization | 0.030 / 0.009 | 0.8% / 0.2% |

表内未列全部小服务和宿主进程，合计不强行凑成四核。容器负载随业务变化，尤其 Worker；不能把某一分钟的值当成全天固定基线。

浏览器相关两个容器合计约 **1.34 核，占四核约 33.5%**。这包括实际采集、多种站点访问、解析和生命周期开销，不等于可删除的空耗。

## 4. 对业务的实际影响

### 4.1 仍能稳定完成的部分

- 最近约 24h 创建的 Runtime Case：570 COMPLETED、54 FAILED、3 UNAVAILABLE、1 RUNNING。创建时间队列与任务更新时间队列口径不同，不能将其与 Worker 或 runtime_tasks 混算成功率。
- 最近约两小时的 120 个 Worker 任务：119 succeeded、1 running；启动排队中位约 47ms、P95 161ms、最大 405ms。现场只有 1 个 Runtime CASE 在运行，没有积压的大量待调度 CASE。
- 这批已完成 Worker 任务的执行耗时中位约 64.1 秒，SDK 阶段中位约 64.0 秒；外部 Worker wall 减 SDK 耗时中位约 119ms、P95 352ms。**SDK 阶段包括模型、工具及本地相关工作**，不能把其中全部耗时都认定为远端模型，但可确认单纯启动排队不是当前研究慢的主因。
- Read 的 runtime、research、initialization、bus 四个源现场均 checkpoint=head，error 为空。
- Executor 仍有成交处理记录；24h 事件记录中 fill 为 165 条，不能将其直接解释成 165 笔不同交易。现场工作状态为 DONE 96、READY 2。

判断：**不是已经全面停摆或持续堆积业务任务；属于高水位运行、响应质量和峰值承载能力受损。** 没有证据支持“所有业务耗时都因 CPU 翻倍”，也没有证据支持“CPU 99% 对业务无影响”。

### 4.2 已经看到的影响和边界

1. **确有 CPU 排队。** 约 30 秒线程核算中，Scheduler 主线程获得约 12.7 CPU 秒，同时在内核 runnable 队列等待约 11.6 秒；Site 主线程约 3.7 CPU 秒、排队约 10.9 秒。它们已经有本可执行却拿不到核心的时段。
2. **Runtime 写路径已出现秒级拖延。** 最近 24h 范围的已有慢日志中，Scheduler 记录 4,613 次慢事务，耗时中位 462ms、P95 1,043ms；Executor 310 次，中位 372ms、P95 954ms。日志只记录 ≥250ms 或异常的事务，以上不是全部事务的延迟分布。
3. Scheduler 慢事务中约 74% 的累计墙钟时间位于连接/BEGIN 完成之前。`wait_ms` 从调用开库前开始计时，包含开库、CPU 调度和 SQLite 获取锁的时间，**不是纯锁时间，也不是可直接节省的 CPU 时间**。已有 `journal.set` SQL 会避免相同 payload 的实际 UPDATE，但仍会开库并申请写锁。
4. 最近 24h 的 durable gap 中有 3 条 `CALENDAR_UNAVAILABLE: database is locked`、4 条明确 `OperationalError: database is locked`；另有 2 条 OperationalError 只有类型名。锁错误是事实，但不能将其全部归因于 CPU，或认为加核会消灭单写者竞争。
5. Bus 记录 5,060 次 `bus_storage_slow`，被记录的操作耗时中位 1,364ms、P95 2,464ms。`ingest`、`_initialize_due_slots` 等在内，墙钟耗时也包含排队和其他等待，不能直接折算 CPU 核数。
6. 153 个 poll state 中 132 succeeded、21 failed；当前 binding 152 个 enabled、1 个 disabled，九个 ticker 均 running。最近轮次中，Yahoo / InvestorsHub 部分 ticker 的总轮次耗时约 110–140 秒；全体有 latency 的状态 P95 约 127 秒。它是不同来源最近一次轮次的横截面，不是 24h 全部请求 P95；轮次也包含排队、多关键词/页、重试、网络和回退。
7. 来源失败集中于 Reuters、Investing、CTEE 等访问/解析问题；Case gap 还包含 provider_timeout、provider_request_failed、structured_output_validation_failed。**不能将 54 个 FAILED Case 或 21 个失败来源笼统算作 CPU 导致。**

当前 API 慢日志没有足够完整的用户端成功请求分布，未测量实际浏览器端 RUM；不能承诺升级让前端快某个百分比。Read 追平说明没有持续投影积压，也不等于证明所有前端查询都快。

## 5. 上一轮修复是否失效

- Initialization 约 0.009 核、Control 约 0.030 核；Control 现场仍为每分钟镜像写事务 0。原先的空闲多槽轮询、稳定镜像重写没有明显反弹。
- Transport 约 0.09 核，正常工作；较早发布前样本约 0.17 核。任务组成不同，不把差值视为严格同负载因果结果。
- CPU-03 的 W3 发现现在能检查真实周期：日志中 869 个批次累计约 **48.5 线程 CPU 秒**，全部 receipt lookups=0、registration_attempts=0。即使粗略摊在不到一天的观察时间，平均也远低于 0.01 核；不再是原有历史大 JOIN 持续占用数十分之一核的形态。
- W3 当前存在一个 DELETING 线程查询 Worker status 返回 500，导致维护仍按失败周期小时级重试。它应独立修复，但**不是本轮持续四核饱和的主因**。48.5 秒只包含日志所在维护线程，不包含全部下游 RPC 工作，不能将它当作整个维护体系的总成本。
- CPU-06 本来只减少单次 group 请求状态事务的重复开库，并未覆盖每轮全部配置、poll state 和来源约束读取。因此 Bus 仍高 CPU 并不证明 CPU-06 没生效。

之前局部空耗下降后，整机仍长期满载；今天又包含后续业务发布。没有同负载逐容器历史，无法精确拆出“哪次业务发布新增了多少核”。正确结论是**局部修复有效，但未覆盖所有重复工作，也未消除总体容量压力**。

## 6. 不改变主要业务逻辑的优化方向

### 6.1 P0：稳定 activation 不重复准入；后台 tick 不构造完整展示详情

代码：`ticker_initialization/consumers.py::admit_runtime_revisions`；`runtime_scheduler/service.py::admit_activation`、`tick_ticker`、`detail`。

实际机制：每轮 `run_due_once` 遍历 active revisions。即使 Scheduler 已经是相同 revision，现有条件只会跳过部分停用状态，仍然执行 `scheduler.admit_activation`。后者重复加载固定版本 Event Index / Policy projection，执行 `admit_revision` 的 staged receipt、授权校验及事务，并更新 Scheduler 状态。每个 ticker tick 最后还返回 `detail()`，读取最近 50 条消息/流、配置、audit 等；生产主循环只需要驱动业务，不使用这份完整返回对象。

15 秒 GIL 样本中，Scheduler 的 115 个有效样本里，41 个位于准入链，22 个位于 detail 链。这是方向证据，不能解释为可节省 Scheduler 总核时的 55%。

实施边界：

- 复用已存在的 activation ID、控制 epoch / mode、运行状态和 ACK/授权 receipt 作为稳定准入判断；仅变化、首次加载、重启恢复或明确缺失时走完整准入。
- 保留每轮轻量控制检查。暂停、恢复、切换模式、新 revision、缺 ACK、未完成授权都必须生效；不能仅看 ticker 在内存 set 中就永远跳过。
- 新 Case 继续按自己的激活版本加载冻结输入；不要为了避免重复准入而缓存一个永久不变的业务快照。原有 Policy 消费、rearm、fencing 规则不改。
- 给后台 tick 增加不构造详情的内部路径；API/显式调用 detail 的行为保留。保留驱动计数、状态更新、事件消费和 coordinator.tick。

收益预算：**约 0.12–0.22 核**；优先级最高。验收应覆盖稳定多轮无重复全量加载，新版本/epoch/mode/缺 ACK 会恢复完整路径，以及后台 tick 与原路径的事件消费结果一致。

### 6.2 P0：交易日历真正使用缓存，稳定日程不重复申请写事务

代码：`persistent_runtime_v2/calendar.py::is_session`、`session_bounds`；`coordinator.py::reconcile`。

实际机制：`is_session` 读出 cached 后仍然计算并调用 `journal.set`；cache 主要在异常时兜底。每次还通过 `importlib.metadata.version` 查找同一个安装版本。`reconcile` 每轮保存相同 schedule。虽然 `journal.set` 已有 no-op UPDATE 条件，开库和 BEGIN IMMEDIATE 仍发生，导致低价值日历判断与真实 Case/交易抢写入机会。

实施边界：

- 包版本在 Calendar 实例内缓存；固定版本 session / bounds 使用已有持久化缓存。
- 每次先检查显式 calendar override；override 的开闭市和 open/close 时间优先。跨日、override 变更、库版本变更不能误用缓存；无需引入外部缓存服务。
- schedule 内容变化时才 set；不要全局改掉 Journal 的安全事务，也不降低 durability、吞掉 calendar 错误或拉长业务轮询。

收益预算：**约 0.04–0.10 核**，另可减少 Runtime 写锁竞争；后者的价值可能高于纯 CPU 节省，但没有足够数据承诺秒级延迟下降比例。

### 6.3 P0：Bus 在单轮/同一 storage 工作单元批量读取轻量状态

代码：`message_bus_v2/scheduler.py::_initialize_due_slots`、`_effective_constraints`、`_update_capacity_alerts`，以及 Corporate IR `_roster` 的重复读取。

实际机制：153 个 binding 的排期逐个 `get_poll_state`；每个 group 又查 sources；随后 due 判断再次查 poll state。Bus 当前一次全新只读连接加首条最小 schema 查询平均约 **6.57ms CPU**，Runtime 约 **8.52ms**；同一连接重复最小查询约 0.007ms。这是开库与 schema 初始化成本证据，不是“全部业务能快一千倍”。

以 153 次 Bus 点查为例，仅冷连接成本已约 1 CPU 秒/轮，尚未包含其他工作；真实轮次速率会被自身耗时限制，不能把它按一秒一轮直接算成整整一核节省。

实施边界：

- 同轮批量读 poll state / sources / group constraints，必要的子调用借用该 storage 线程的连接；每轮重新取快照。
- 不持有写事务跨 HTTP、浏览器、模型或 async await；不共享同一个 SQLite connection 到任意线程。
- 新配置版本、禁用 source/binding、限流约束、durable last_request_at 仍及时生效；保留 CPU-06 的“先持久化请求开始状态，再执行请求”。
- 不建立通用全局连接池，不把任意历史业务对象永久缓存；先消除这条热点的重复读取。

收益预算：**约 0.12–0.25 核**。应先完成本项，再评估剩余 Bus CPU 是否主要是实际采集/解析。

### 6.4 P1：IR urllib 复用 TLS 信任配置；Projector 批内借用连接

- `corporate_ir/http.py::_stdlib_read` 每次构造 opener，HTTPS 默认路径重复创建 SSLContext 和加载根证书。现场 20 次默认 SSLContext 创建耗费约 1.045 CPU 秒，即约 52ms/次。可在 PublicHttp 实例初始化一个固定默认信任 SSLContext，用 HTTPSHandler 注入；如复用 opener，限定线程所有权。保留证书验证、无代理、禁止自动重定向和现有超时/host 限额。收益取决于实际 urllib 请求量：每秒一次约 0.052 核；本次没有准确计数，不将该场景当成生产实测收益。预算 **0–0.04 核**。
- Projector 约 0.235 核，已有栈中重复 capture/head/coordinates 和业务事实同步的开库；可先在同源同批次借用读连接，保留当前捕获、回放保护和投影事务边界，预算 **0.03–0.06 核**。不要直接扩大后台间隔或改动 Read 一致性协议。

这两项排在三项 P0 后面。不要为了极低收益先改 Worker 全局 metadata 持久化或开发一个统一资源缓存框架。

### 6.5 条件项：浏览器重复整页加载，而非直接关来源或减频率

从最近限量读取的访问记录中取最后一小时：GlobeNewswire 成功访问 1,196 次，InvestorsHub 297 次、Yahoo 184 次、Barron's 372 次，另有 TrendForce、DigiTimes 等。这些是访问记录数，不等于独立新闻数或独立业务任务。

GlobeNewswire 的访问 network_ms 中位约 1.3 秒，Yahoo 约 13 秒、InvestorsHub 约 9.3 秒；与 poll 轮次 110–140 秒之间还有排队、多查询、重试等成本。单纯加核不会自动消除这些站点和 identity 的串行约束。

可研究让已验证可用的列表/分页在相同身份与路由中读取结构化内容，避免每次完整页面生命周期；失败继续现有 browser 路径。上线前必须回放对照 URL、时间、匹配和正文可消费性。**目前没有等价覆盖和 CPU A/B，不能把约 1.34 核浏览器负载算成可全部去掉。** 条件收益可先以约 0.1–0.3 核作为验证目标，不纳入升级决策的确定预算。

仍遵循此前 CPU-04 Yahoo 读取顺序不实施的边界；本次没有修改它，也不把其假定收益计入建议。W3 重复研究资料/Context 物化与研究读取序列化仍单列：本次 Worker GIL 样本可见该链路，但不将它作为本轮容量治理前置条件，更不擅自改业务语义。

## 7. 优化能否代替升级

以上 P0/P1 存在连接、状态读取等交叉收益，不能把所有上界直接相加。**建议按 0.3–0.6 核作为可规划的低侵入优化收益，0.6 核以上需要实测再认领。**

若暂按当前约 3.95 核执行需求、任务量固定且没有被压住的待执行需求计算：

| 路径 | 仅作敏感性分析的 CPU 水位 | 判断 |
| --- | --- | --- |
| 4C，保持现状 | 接近 99% | 已持续争抢 CPU |
| 4C，省 0.3–0.6 核 | 约 84%–91% | 有明显收益，峰值余量仍薄 |
| 8C，相同已观察执行量 | 约 49% | 仅是现有核时除以 8 的算术基线 |
| 8C，叠加 0.3–0.6 核优化 | 约 42%–46% | 同样不是升级后的监控预测 |

CPU 饱和时的核时只显示机器已经提供的服务量，**无法显示所有被排队压住的需求**。升级后任务能更快推进，可能完成更多工作并继续消耗 CPU，因此不能承诺实际监控降至 49% 或上述区间。4C 优化后的水位也可能高于表内固定工作量估算。

若希望四核有 25%–30% 余量，约需从目前执行量中减少 **0.95–1.15 核**，还未计潜在排队需求与新增业务。当前已确认可优先优化的路径尚不足以支撑这样的稳定降幅。继续强求四核，很可能要依赖未验证的浏览器重构，或降低采集/业务并发，收益风险不合算。

## 8. 为什么 8C 的额外成本值得

按当前机器成本 P、8C 成本 1.5P：核心数增加 100%，总成本增加 50%；单位核心成本降低 25%，理论 CPU 容量/成本提高约 33.3%。前提是 CPU 代际、单核性能和云实例可持续配额可比；本报告未查询或确认具体云规格/报价，采用用户给定成本条件。

可直接期待：独立服务和 Chromium 之间的 CPU 竞争减少；Scheduler、Executor、Site 主线程更容易及时得到执行；为盘中峰值和后续 ticker 增长留出空间。当前线程 schedstat 已证明这些路径有真实 runnable 等候，因此这笔支出有明确受益对象。

不能承诺：模型调用减半、网页网络时间减半、SQLite 写入变成多写者、来源访问错误消失、业务吞吐翻倍。单核/GIL、数据库锁、访问站点和 provider 限制仍在。

因此建议是 **8C + 三项 P0 精简**，而不是仅加核后停止修复重复工作，也不是继续大改整个系统来回避扩容成本。

## 9. 后续执行与验收边界

1. 三项 P0 各自窄改、针对真实行为做回归，重要代码变更追加 `changelog`；保留当前主分支业务逻辑，不改健康业务并发/频率。
2. 用同一套核时和业务计数做前后对照，记录每轮 ticker/binding 数、成功采集量和实际任务量。只有“每单位工作 CPU 时间下降”才认领优化收益；降低处理量不算优化。
3. 另修 W3 单个线程 status 500 的退出错误，优先定位 Worker 侧失败原因，不靠无限延后维护或放弃所有权检查；这不是扩容的阻塞条件。
4. 经用户后续授权后，在维护窗口升级 8C；本轮不执行升级。不要同时放大所有并发，否则无法比较效果且可能转为内存压力。
5. 升级后比较完整工作日与盘中窗口：实际执行核数、CPU PSI、慢事务计数/分布、Bus 轮次延迟、Worker 启动排队、Read head-checkpoint、吞吐和错误。核心目标是减少排队并维持业务覆盖，不是只要求某个 CPU 百分比。
6. 如果升级后实际消耗已明显超过 6 核或 PSI 仍持续很高，重新追踪具体热点；如果 CPU 压力已经缓解但锁等待/网页轮次仍慢，应针对串行写入和访问队列处理，不继续盲目加核。不要把该条件做成新的复杂自动阻塞系统。

## 10. 核验索引

- `history.json`、`summary.json`：系统历史、最后窗口、任务和慢日志汇总。
- `inventory.json`、`verify.json`：当前服务/仓库身份、审查源码哈希、W3 真实维护周期和错误。
- `live_final.json`：诊断额外进程退出后的独立最终 cgroup/进程采样。
- `threads.json`：关键进程线程实际执行时间与 runnable 等候。
- `profiles.json`、`gilprofiles.json`：普通栈与仅 GIL 栈，包含权限失败记录；没有将采样失败当成零负载。
- `business2.json`：只读 Case/Task、Worker、poll 和 Read 健康状态。
- `details.json`：已有慢日志原始数字与失败预算记录。
- `cost_and_site.json`、`cost_and_site2.json`：最小开库、TLS/包版本成本，以及限量 Site Access 元数据。
- `own_probe_stop.json`：本次额外诊断进程的退出凭据。

证据包含多种窗口和不同统计对象；不重复叠加核数、失败数量或性能收益。本轮产物是一份决策与执行建议，**没有声称已完成新增优化或解决持续高 CPU**。
