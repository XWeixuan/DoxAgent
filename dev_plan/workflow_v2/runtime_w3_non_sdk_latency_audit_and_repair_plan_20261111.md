# W3 非 SDK 延时专项排查与修复方案

日期：2026-11-11。正文时刻为北京时间；原始 JSON 使用 UTC。状态：前置代码收敛与发布已完成；本专项只排查、探测和制定方案，未实施下述性能改造。

## 1. 结论

**当前未达到非 SDK 内部耗时 P95≤5 秒的硬性底线。** 固定的 55 个新生产 Case，两阶段均已完成、账本可重建率 100%；Trade 内部耗时下界 P95 **13.96 秒**，Operations **7.38 秒**，整条 Case **21.05 秒**。这批样本没有 429 或跨 Job 重试，不能用 Provider 等待解释超标。

主要问题集中在：**进入 Job 之前的准备链、部分 Trade 的物理 Capsule 冷启动，以及结果提交与准备写入之间的竞争**。已经观察到可消除的公共准备重复写事务。Worker 领取后的实际排队、正常大小 JSON/Pydantic 解析和 SDK 结束后的 Worker 收尾，在本批样本中不是主要耗时来源。

更早恢复窗口的数分钟长尾还有另一类明确原因：普通 429 后程序固定退避 60 秒。这是内部计时，不能因为字段名叫 `provider_wait` 就扣除。本文同时保留这类样本，不用后面的正常窗口掩盖前面的长尾。

细分观测尚不能覆盖所有异步准备、接收和最终落盘边界，因此本文明确报告**账本下界和实测局部区间**，不把未知区间填成零，也不宣称已经精确测得全部纯推理耗时。

## 2. 已先完成的工作树审查、收敛与发布

已审查原 `codex/agent-readonly-mcp` 的 54 个应用、测试及操作代码候选路径：33 个已在 main；14 个经审查保留当前 main，其中 9 个 W3 文件是旧 `e93e2557` 的原样副本，不能覆盖近期修复；7 个有效增量路径已整合。另对非应用证据、操作原型和 SDK 快照做静态清单与语法检查，没有将这些历史材料当作生产程序全量发布。4 个尚未启用的 Claude 提示词原型保留，未替换生产提示词。

有效增量包括逐 ticker admission/ACK 异常隔离、健康恢复后的降级原因清理、维护健康摘要、D3 原冻结 WAIT_WORKER 续接，以及明确未派发情况下的存储失败恢复。没有重新引入旧 W3、普通 429 全局暂停或失败 Future 重准备逻辑。

- 代码提交并推送：`3d8bc755e13a56442a7df7d3e42c496d82b7a6ad`，已进入 `origin/main`；49 项定向回归通过。
- 经现有 canonical `storage_release` 流程发布 Scheduler、Initialization、CDECR Executor；Worker 无代码增量，保持原镜像，避免打断在途 W3。
- 已核对实际容器源码 SHA256、canonical 镜像与运行镜像一致，保留远端 changelog 私有尾部。原脏分支的 HEAD/index 和未发布原型没有强制重置；工作区应用源码已与 main 做实际字节核验。

| 角色 | 实际运行镜像 SHA256 前缀 | 发布后核验 |
| --- | --- | --- |
| Scheduler | `bd06b2ac54f8` | 本次替换及关键 W3 文件均匹配 main |
| Initialization | `650582d081ef` | 两个 D3 文件匹配 main |
| CDECR Executor | `bcb75c507ced` | 两个 D3 文件匹配 main |
| Worker | `0f57319caca1` | 保持已接受镜像；jobs/Ready 源码匹配 main |

证据：[收敛清单](../../eval/w3_nonsdk_audit_20261111/worktree-convergence-inventory.json)、[49 项回归](../../eval/w3_nonsdk_audit_20261111/convergence-regression.xml)、[版本核验](../../eval/w3_nonsdk_audit_20261111/production-version-verification.json)、[最终生产核验](../../eval/w3_nonsdk_audit_20261111/final-production-verification.json)。此前发布背景参见[上一轮发布报告](runtime_w3_execution_optimization_release_20261111.md)。

## 3. 取样与计时口径

固定 cohort：01:01:26–01:31:42 创建的 55 个新 W3 Case，01:44:54 按同一名单复查，Trade/Operations 各 55 个全部完成。此前快照中 Operations 尚未完成的 1 个已补齐，没有删掉慢 Case。历史恢复名单、旧版本/跨停机 Case 单列；不是仅挑“成功且快”的样本。55 例仍不足以代替下次开盘和全天验收。

以原 Case → Effect → phase → Job/request 身份串联 Runtime、Worker 账本、边界 trace、Ready 和线程操作记录。完成记录可能已清除 `owner_case_id`，实际通过 stage.thread_id 找回原线程，再按 Case attempt 前缀取全阶段 Job；不能仅查当前 owner，否则会漏掉已完成 Case。

当前计算如下：

1. Trade 起点为路由 Effect 创建；Operations 起点为 `max(operations Effect 创建, Trade committed_at)`。Operations Effect 对象在 Trade 提交前已构造，早于依赖完成的那段仍留在 Trade 和整条 Case 内，避免分阶段重复归因。
2. 终点先取业务 stage 和 Effect 的持久时间戳；从阶段墙钟中扣除**全部 Job 的 SDK Turn 区间并集**，包括失败尝试，不只取最后成功 Job。分段也按区间去重；不相加父子 span 或各段 P95。
3. 整条 Case 单独按首阶段到末阶段的时间包络，扣 SDK 区间并集；不把两个阶段 P95 相加。
4. SDK client 初始化、thread fork/resume、Capsule 导入、上下文/工具配置等发生在模型 Turn 派发前的操作，**均计内部**。SDK Turn 墙钟包含 SDK 工具循环及其自身网络行为，不等于已测得纯模型推理。Turn 内 DoxAgent 工具执行如需拆出，还必须用嵌套工具 span 补足，不能把整段叫 Provider 推理。
5. DoxAgent 内部排队、锁等待、重试、固定退避、发布恢复控制等待全部计内部。只有供应商原始回执可证明的限流期限/外部等待区间才能另列扣除；本次没有足够原始期限证据，额外扣除 **0 秒**，不表示现实中不存在限流。

**边界限制：** committed_at/Effect.updated_at 在 SQL 调用完成前生成，异步 trace 又存在缺口，所以这是内部耗时下界。不能用它做最终“通过”判定。实测某个 Operations 的最终 trace 比 Effect.updated_at 晚 **0.823 秒**，证明尾部确有漏计可能。前面更新中的 Operations 7.66 秒是从 Effect 对象创建计时；最终统一依赖可运行起点后为 7.38 秒，整条 Case 数值不变。

诊断开销：最新 450 条 Case 的标量导出耗时 3.35 秒，固定 55 例复查 0.50 秒；只读短连接、显式 close，没有复制/全扫约 15GB Runtime 数据库。选定 trace 收集约 0.91 秒；随机文件目录各检查上限 5,000 个元数据，截断明确记录。另用 inotify 监听新落地文件 180 秒，共 48 个 trace、约 48KB，未扫描目录历史、未增加业务数据库写入。少量导入/解析探针 nice=19，没有构造 SDK client、调用模型或重启业务服务。

## 4. 当前生产分段数据

下表单位为秒，使用最近秩 P95；**是完整账本 cohort 的保守区间，不是最终全链路精确时效验收**。

| 指标 | Trade，n=55 | Operations，n=55 |
| --- | ---: | ---: |
| 内部耗时下界 P50 | 5.16 | 2.92 |
| 内部耗时下界 P95 | **13.96** | **7.38** |
| 内部耗时下界最大值 | 17.92 | 10.51 |
| 超过 5 秒 | 28/55 | 7/55 |
| SDK Turn 墙钟 P50 / P95 | 62.76 / 154.68 | 90.24 / 374.31 |

整条 Case：内部下界 P50 **9.27 秒**、P95 **21.05 秒**、最大 **24.17 秒**，40/55 超过 5 秒。两阶段间的交接保留在整条 Case 内。

| 内部区间 | Trade P50 / P95 | Operations P50 / P95 | 可确认的含义 |
| --- | ---: | ---: | --- |
| 可运行起点 → 首个 Job.created | 3.256 / **10.407** | 1.596 / **4.139** | 调度、领取、同步准备、异步上下文/Ready/派发的合计，尚非纯准备 CPU |
| Job.created → Job.started | 0.067 / 0.167 | 0.051 / 0.128 | Worker 实际内部排队 |
| Job.started → SDK dispatched | 0.134 / **8.358** | 0.024 / 0.053 | 启动、Capsule 和 SDK 控制 RPC；不算模型推理 |
| SDK completed → Worker finished | 0.098 / 0.292 | 0.073 / 0.195 | Worker 结果持久化/结算，非纯清理函数 |
| Worker finished → stage.committed | 0.639 / **2.143** | 0.749 / **2.583** | SSE/接收、Future、回执编码/写入、解析及业务提交的合计 |
| stage.committed → Effect.updated | 0.142 / 0.645 | 0.143 / 0.739 | 路由镜像同步、释放/收尾等；另有落账后尾部未全覆盖 |

这些区间按单条 Case 计算后再求分位数，不能相加表内 P95。当前 Trade 的准备区间约占该阶段内部累计秒数的 64%；Operations 主要也是准备与结果提交。这个占比用于确定调查优先级，不把准备区间全部归为某一项函数。

## 5. 已超标 Case 的直接证据

| Case / 阶段 | 内部下界 | 准备 | Worker 排队 | Worker 启动 | SDK结束→Worker终态 | Worker终态→业务提交 | 提交后账本收尾 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| MU `case_f438f9c5998f4e3a98a5bbb46fa6dddd` Trade | 17.922 | 3.025 | 0.155 | **12.607** | 0.292 | 1.651 | 0.191 |
| COHR `case_dc48755e0334446eb95733ac52dfdb03` Trade | 14.376 | **10.407** | 0.061 | 0.161 | 0.748 | **2.810** | 0.188 |
| MU `case_1221cbd4d4074a0e96fa091d5d94be37` Trade | 13.959 | 3.826 | 0.129 | **8.358** | 0.101 | 1.246 | 0.299 |
| 同一 MU Case，Operations | 10.508 | **4.139** | 0.051 | 0.045 | 0.100 | **5.740** | 0.434 |
| `case_42271f13bfde42d6bff4703ec1d677c5` Operations | 7.381 | **5.992** | 0.081 | 0.027 | 0.236 | 0.929 | 0.116 |

### 5.1 冷启动是真实执行成本，不是 Worker 排队

第一条 MU Trade 的 `capsule.spawn` **7.124 秒**，其后 `sdk.thread_resume` **5.437 秒**，合计基本解释 12.607 秒启动区间。另一个 MU Trade 的 spawn **6.922 秒**、resume **1.389 秒**。对应子进程启动 CPU 约 2.97–2.99 秒，导入 Schema/SDK 模块的墙钟分别达 1.83/3.71 秒、1.02/4.70 秒。

第一条的 Ready 行写着 `POOL_HIT`，但该行创建/绑定于 **11 月 6 日**；当前 Job 明确 `boot_already_ready=false`，重新 spawn。它表示历史逻辑 slot 领取，不能证明当前物理 SDK 进程温热。不能根据这个标签排除冷启动。

镜像 `PYTHONDONTWRITEBYTECODE=1`，原 Dockerfile 的 uv sync 未预编译。当前容器导入探针确认 206 个已加载的应用依赖源码没有字节码。一次同配置的临时外部字节码对照：Schema+SDK 导入由 **3.03 秒/2.56 CPU秒** 降至 **1.92 秒/1.45 CPU秒**；375 个临时缓存文件约 7.97MB，测试后删除，未修改生产包。另一次未缓存探针为 4.43 秒/2.67 CPU秒。

这证明预编译有实际收益；它是低优先级单次探针，不是生产 P95 收益保证，也不能解释或消除全部 5.437 秒 thread_resume。

### 5.2 Ready 构建剩余等待在 Job 创建前，不能漏计

COHR Trade 的 Effect 创建于 01:02:52.163，slot 绑定于 01:02:54.727，READY 于 01:03:01.922，Job 创建于 01:03:02.570。10.407 秒准备区间可以直接拆为：**2.564 秒绑定前工作 + 7.196 秒等待绑定 slot 就绪 + 0.648 秒就绪后的准备/派发**。

该 slot 的构建早在 Case 进入 W3 前已经开始，不能把完整 37 秒构建时长都归给当前 Case。初始 Case trace 中上下文准备约 103ms、历史输入约 224ms、线程准备同步部分约 392ms；available→claim开始约 487ms，claim 批次约 431ms，领取后线程启动不足 1ms。该 Job 自身启动只有 161ms，长等待主要发生在 Job 之前。

### 5.3 Operations 收尾存在事务入口竞争，但不是整段都能归为 SQLite

上述 MU Operations，Worker finished→业务提交 **5.740 秒**。其续接 trace 从 01:03:06.784 到 01:03:12.637，11 次 `journal.begin` 合计 **3.302 秒**，覆盖业务提交和收尾；`case.operations` 为 4.441 秒。这里有直接的等待证据，但 11 次包含读和写，不能说成 11 次写锁，更不能把整段 5.740 秒全部归为锁等待。

同一时间窗，进程慢事务日志有 link 入口等待 2.959 秒、begin_stage 2.171 秒、ACK commit 1.984/2.291 秒等记录，多数事务实际 hold/commit 只有数至数十毫秒。日志没有 Case ID，不能把同窗所有告警逐条强行归给这个 Case；目前也不能分清其中多少是进程内 gate、SQLite BEGIN、调度切出或 fsync。

其 Effect.updated_at 为 01:03:11.813，而最终 trace 到 01:03:12.637，另有 **0.823 秒**尾部：按 final trace 可证明该阶段内部至少约 **11.33 秒**，比纯账本 10.51 秒更慢。需要把观测补在 SQL 真正返回之后。

### 5.4 早期分钟级长尾：429 与程序退避要分开

同一 W3 代码、00:00:22–00:59:29 前置收敛发布前窗口的 84 个完整 Case：Trade/Operations 下界 P95 分别 **230.98/207.24 秒**；各有 32/28 个阶段经历 429，合计 69/55 次失败 Job。这个窗口还包含历史恢复竞争，不与新 cohort 混成一个“正常 P95”，也不构成部署前后 A/B 因果证明。

例如 `case_03c8e943b36e4880805e6c1aee9f6a00` Trade 内部 **359.91 秒**，5 次 429 后均由 `worker_receipts.py` 生成 finished_at+60 秒期限：本地固定退避共 **300 秒**，失败终态到下次 Job.created 的累计间隔 **308.35 秒**。其中额外约 8.35 秒仍是内部交接/准备，其他约 51.56 秒在准备、启动及收尾区间。不能将 308.35 秒整体扣成 Provider 等待。

上一轮报告使用过 `provider_wait` 区间扣除；本报告纠正这一口径。真实供应商拒绝请求是已证实事件，**60 秒是应用决策，不是已证明的服务端强制等待期限**。

## 6. 根因与当前架构中的具体问题

| 结论强度 | 当前代码/机制 | 证据与判断 |
| --- | --- | --- |
| 已证实 | `capsule_child.py` 冷进程重复导入；镜像没有对应 pyc | 启动 trace、CPU 计时与无 SDK 调用的字节码对照一致；放大冷启动成本 |
| 已证实 | `w3_threads.template()` 即使 READY 仍 `INSERT OR IGNORE` 并申请写事务 | `coordinator.py` 每 ticker 约 30 秒公共准备，两种 profile 均走该路径；01:00–01:20 慢告警中 78 次，入口等待累计 **41.045 秒**，hold累计2.362秒、commit累计0.957秒。是多个并发调用之和，不能当作某个 Case 的墙钟 |
| 已证实 | 同一公共准备反复写 `w3_public_preparation`、`w3_entry_active` | 对应慢告警分别12/11次，入口等待3.272/4.482秒；状态未变时仍写，挤占同一 writer |
| 已证实 | `w3_execution._commit_operations()` 每条 delta 单独 `allocate_provisional`，然后 link/gap/main commit/route同步/release | 选定三例分别有4/3/3条delta。分配使用 repository 普通写路径和 `_lock`，不在现有结果优先提交单元中；优先级只保护部分结果消费链。额外事务数量明确，具体各自等待贡献尚未完整观测 |
| 已证实的结构风险 | `app.py` 所有 `DiskBudget.run` 共用 `Semaphore(1)` | 研究包、context bind、workspace materialize、ACK镜像、snapshot/inventory共用；不同 Case 可被无关工作串行阻塞。准备剩余5.99秒是否主要来自此处，目前没有 slot等待span，不能认定为已证实主因 |
| 已证实的交接问题 | `service._dispatch_effects_with_capacity()` 先 claim realtime，再 claim background，最后才启动 realtime | 后一次后台领取阻塞会传递给已领到的实时 Case。当前 claim 是短但非零区间；尚缺两次领取的独立计时 |
| 已证实的观测缺陷 | `effect_trace` 在 Future 让出时先保存，异步原 trace 完成后未统一终结保存；INFO日志未启用 | 缺失异步准备、回执、解析细分；同Effect/lease key可被续接覆盖。随机UUID平面文件无法按Case有界完整回收；不得以初始1秒trace代表整个阶段 |
| 已证实的观测缺陷 | Effect时间戳早于实际SQL返回；部分字段名字误导 | `effect_pump.queue_wait_ms` 在循环中沿用首次提交时间，后续圈数包含已执行时间；Worker startup trace有时到SDK结束才快照。均不能直接当实际排队；旧slot的POOL_HIT也不是物理warm命中 |

正常解析不是本批主因：三个实际冻结 Case 的 Native展开/重编码约30–78ms，JSON+RuntimeCase校验8–15ms；已保存输出 JSON/Pydantic 校验约0.03–0.12ms。这不覆盖原始文本修复、完整引用检查及高压时的执行器等待，后者仍需独立计时。当前压缩后的 invocation 行约数KB；没有证据支持再次把“巨大模型回执编码”当作主要根因。

已存在且应保留：原 Job 确认受理/明确404/派发未知区分；成功结果绕过 Provider Pause；租约/fencing/幂等；结果先持久化再业务提交；实时 Job 直接启动；Future 不重新准备/重调模型；Worker终态先SQL再立即唤醒SSE；Trade→Operations handoff保护；有界资源回收。`W3Execution` 已按 service 单例复用，**不是每个 Effect 都重新建 Registry/执行DDL**；`sync_event_links` 也已有版本watermark，不作为重复全扫问题处理。

## 7. 推荐实施顺序与边界

### P0：补齐真实边界，纠正验收工具

修改 `w3_timing.py`、`w3.py`、`worker_receipts.py`、`service.py`、`control_io.py` 和相应审计工具，但只增加标量观测：

- 用同一个 Case/phase/Job/Effect lease 身份记录：eligible、claim开始/结束、Case线程开始、context读取/编译、context bind、Ready claim/等待/物理spawn、Job受理/启动、SDK Turn起止、Worker终态、SSE接收、Future done/续接开始、回执编码/落库、parse/引用校验、save_result、business commit真正返回、Effect保存真正返回。
- SQL将 **本地gate等待、BEGIN等待、事务体、commit** 分开；`write_io/read_io` 将提交→实际执行和实际函数时间分开。共享 span 必须有父子关系；继承到 coroutine 的 trace 在异步准备/接收完成时也输出终结摘要，不能只保存第一次yield。
- 复用已有有界诊断通道，输出每阶段有限摘要，含schema版本、attempt和完成标志；不新增业务表、全量回执副本或每步SQL，不在事件循环/业务writer同步写诊断文件。日志启用仅限定时效摘要，不打开全局INFO。拥塞可丢诊断并计数，缺失则验收“不完整”，不能通过。
- 修正 pump 的每轮时间基准；物理warm按当前spawn/boot_already_ready和实际持有进程记录；区分SDK envelope与其嵌套本地工具。账本下界仍保留作交叉核验。

### P1：先去掉已证实的冷启动和无效写成本

1. **Worker镜像预编译**：`Dockerfile.v2` 的依赖安装增加编译步骤；应用和实际SDK依赖在最终源码overlay之后编译，避免仅修改Dockerfile却仍发布未带pyc的overlay镜像。应用缓存采用可校验源哈希的失效方式，保留运行期不写pyc的设置。发布前真实检查运行解释器所用cache路径与源文件匹配；不在首次Case中现场编译，也不更改模型/SDK参数。
2. **模板读快路径**：`W3ThreadRegistry.template()` 先以只读查询返回已READY模板；缺失/未READY才进入原写事务，再检查状态和原租约CAS。两种profile、兼容键、租约及失败恢复保持原规则。
3. **公共marker按变化写**：`prepare_public()` 对两个marker比较后再写；相同库版本沿用已有mapping watermark。保留30秒轻量configure/健康补池，以便Worker重启后恢复其内存配置；不能仅凭journal成功marker永久跳过公共准备。Worker已有后台fill和claim后refill继续保留，不另建预热服务或扩大常驻池。

以上是直接有证据的改造，先做、分别测收益；**仅做pyc不能承诺5秒达标**。

### P1：让结果提交全链真正使用现有短写机制

修改 `w3_execution._commit_operations()`、`repository.allocate_provisional()` 和 `w3_threads.py`，抽出可接收现有数据库连接的最小SQL helper：

- 在writer之外准备不可变candidate内容、归档/coverage数据及已冻结的已知最大E#；原result_saved持久化屏障不删除。
- 在原Operations业务事务内，一次完成幂等provisional分配/计数器推进、必要Event links、结果/终态记录及业务动作；生成ID后只拼小型元数据，禁止把大对象编码或Native文件发布搬回事务。
- 只合并同一阶段原本必须提交的本地SQL，不包含HTTP、SDK、市场数据或ACK请求；以原candidate identity和`w3_operations_applied`保证重入不重复分配。必要路由镜像和线程owner释放可使用同一已fence的提交单元，减少提交之后再次争锁；保留历史读取和恢复语义。
- Trade继续保证交易输出、stage提交、Case状态、Operations Effect创建同一原子提交，不延后交易资格判断。ACK保持提交之后异步且可恢复；补测是否与housekeeping重复ACK，再决定是否只保留一个已有触发入口，不先增加ACK状态机。

禁止用扩大busy_timeout、循环重试次数或退避掩盖等待；禁止把整段上下文准备装进高优先级writer。repository和journal的相关写入采用一致的现有gate接入顺序，避免持repository锁等待另一条writer路径形成串等。

### P2：精简已存在的准备交接，按测量实施

- **先启动已领取实时Effect，再领取后台Effect**：拆开service两次claim的计时和发布点；保留原租约、active注册、未启动项回退及8个后台位置。实时不加并行数上限，不新增线程池或队列。完成Future直接续接的现有路径保持。
- **拆除W3无关I/O的全局串行点**：在补齐DiskBudget slot等待后，对研究pack/materialize等已有按pack/run锁和原子发布的路径，使用现有 cancellation-safe `blocking`，取消额外全局Semaphore包装。context bind保留真正保护密钥初始化/唯一绑定的小临界区；不可直接删除研究Store所有锁。大snapshot、inventory和ACK镜像不能占用实时准备的同一全局位置。先验证当前执行器排队数据，不以简单扩大共享线程池作修复。
- **物理warm复用先走既有能力**：保留目标线程resident优先、健康unbound其次、无现成资源立即spawn；保留Trade→Operations handoff和cleanup保护。结合新增库存/启动事件定位为什么可用unbound不足，先修复错误失效/重复消耗，不直接增大池或延长常驻TTL。若仍需重用其他已结束Operations的健康client，只在确认无active/handoff、清理结束、scope/凭证可完整重绑定后，在现有reserve路径增加一个安全分支；不回收在途Case、不改变逻辑Thread/Job身份，不新增持久资源状态机。当前没有库存证据证明这一分支必需，暂不直接实施。
- **冷thread_resume待单独定位**：补其SDK本地控制RPC耗时、原线程rollout大小/读取、MCP配置启动和事件循环延迟。禁止删MCP隔离、强制新Thread或改Prompt来换速度；不把5.437秒resume算成推理，也不凭一次结果认定SDK故障。

### P2：限流重试不再使用错误计量豁免

`worker_receipts.py` 的60秒普通429退避必须持续计入内部；真实服务端Retry-After/reset只有在原始证据可用时才另列，不从自建deadline倒推。若固定60秒仍经常出现，SLO就是失败，不能靠排除这些Case通过验收。

后续应先采集原限流回执允许的重试时间及既有SDK重试情况；有服务端期限则按该期限复用原任务定时恢复，不叠加另一个固定60秒。没有原始期限时，不在本轮贸然把60秒改成密集探测或0退避：那会增加真实调用和资源竞争。保留一个已有重试责任方、消除重复退避后复测；若仍不能兼顾5秒与供应商限制，明确报告未达标及证据，不转移到全局Pause、伪造外部等待或重跑已成功Job。

## 8. 尚待验证，不提前定罪

1. 10.407/5.992秒准备区间内，context bind、materialize、短写队列及共享I/O位置各占多少；COHR已确认7.196秒Ready剩余构建等待，但其内部全部子段未齐。
2. 事务入口等待中本地gate与跨进程SQLite锁、CPU调度的比例；ACK是否重复提交、哪个writer持有较久。不能由同窗告警直接认定某个后台任务造成全部等待。
3. Worker finished→commit 的Future唤醒、SSE接收、回执落库、引用验证精确占比；SSE现有revision保护没有被证明失效，不重写其协议。
4. 冷resume慢的具体原因，以及是否有无需增加常驻资源的物理client复用机会。

01:34:41–01:37:41的3分钟资源观察：可用RAM最低10.68GiB，Worker峰值4.23GiB、Scheduler1.65GiB；两者平均约0.38/0.86 CPU核。全机CPU some PSI约35%–37%，确有调度竞争；内存full PSI末值0.06%、I/O full 0.32%，swap-out增量约17.90MiB，kernel OOM无增量。它不支持“服务器再次内存爆满导致一切变慢”，也不能反推01:02的全部等待来自CPU。没有证据就不做新的系统资源限流。

## 9. 实施验收

**时效是硬门槛，观测覆盖不足也不能通过。**

- 固定生产cohort，至少覆盖一个正常开盘高峰，建议连续1小时且每阶段≥100例；不足则全部列出并延续观察。Trade与Operations分别P95≤5秒，同时保留整条Case累计P95≤5秒的既有目标，不能两阶段各5秒却把累计放宽成10秒。
- 所有进入W3的Case均有去向：完成、失败、未完、缺证据分别计数；不只统计快的成功样本。未完Case中已经观测到的内部等待也要记录。每条完成样本必须有SDK完整尝试覆盖、真实业务commit/Effect保存返回边界以及可加总区间；SDK/真实Provider等待并集去重，内部队列/锁/退避不剔除。
- 分段排查预算：可运行→Job受理力争≤2秒，Worker排队≤0.2秒、启动≤0.8秒，SDK完成→业务提交≤1.5秒、必要收尾≤0.5秒；这些是定位预算，不是相加各段P95即可宣布通过。冷启动/Ready miss单列但不豁免总指标。
- 回归必须覆盖：READY读快路径与创建竞争、Worker重启重新configure、库/执行版本变化；并发多delta分配、提交回滚后原结果恢复、重复续接/租约丢失；Trade输出和Operations Effect原子性；已受理/404/未知Job防重派；Pause不拦成功结果；SSE终态唤醒；同线程handoff、MCP权限隔离及取消后物理资源归属。冻结输入、两阶段职责、模型/参数、Prompt/Skill、Schema、路由和交易规则逐项不变。
- 发布只改必要角色，canonical镜像及实际源码/pyc核验；至少10分钟资源观察并覆盖随后真实峰值。不得降低实时并行、不设新准入门槛、不扩大SQLite timeout、不新增Redis/永久调度层，也不以更高常驻RSS或交换压力换取局部速度。

## 10. 历史收尾及材料

截至01:44:02，授权的16并行历史名单 **163/163 Case为COMPLETED，两阶段均163/163 COMMITTED**，active=0，临时unit已inactive，未重建名单或重放交易。四个相关角色运行、OOMKilled=false、自动restart=0，实际关键源码匹配main；当时可用RAM约9.54GiB。这是本次收尾核验，不代替下一开盘性能/资源验收。

可复核材料集中在 `eval/w3_nonsdk_audit_20261111/`：[压缩证据与具体Case](../../eval/w3_nonsdk_audit_20261111/audit-evidence-summary.json)、[固定55例名单](../../eval/w3_nonsdk_audit_20261111/frozen-cohort-ids.json)、[冻结账本](../../eval/w3_nonsdk_audit_20261111/frozen-cohort-metadata.json)、[逐阶段计算](../../eval/w3_nonsdk_audit_20261111/frozen-cohort-ledger.json)、[选定trace与事务告警](../../eval/w3_nonsdk_audit_20261111/traces.json)、[新文件/资源观察](../../eval/w3_nonsdk_audit_20261111/live-observation.json)。只读采集、计算及探针脚本一并保留；未将消息正文、完整Prompt或密钥写入本报告。
