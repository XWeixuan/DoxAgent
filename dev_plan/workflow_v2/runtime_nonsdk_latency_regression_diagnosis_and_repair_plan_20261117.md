# Runtime 非 SDK 延时回退：历时复盘、生产诊断与修复方案

2026-11-17。**本轮只排查、验证机制和制定方案；未改业务源码、配置、Prompt、Schema，未发布或重启生产服务。** 文内生产时间以北京时间为主，精确原始时间保留 UTC。断网导入对照和租约复现均为零模型调用。

## 1. 结论

**存在真实回退，不是双任务取并集的统计错误；也不能把页面的全部延时都归给 W3。**

1. 对 1,293 个模型时钟完整的已提交 Case，把页面 attempt 与原 Worker 两份 Trade SDK 区间逐条对齐，扣除并集的差异为 **0**；与已投影的页面 Case 数值最大差约 `7.2e-7` 秒。没有发现只扣选中分支、把另一分支推理时间误算成内部等待的问题。
2. 当日页面指标可复算的 69 例，均值 **41.65 秒**。其中相当部分发生在进入 W3 之前：CEG 一个 Case 在首次 W1/W2 调用前已等约五分钟，NBIS 一个 Case约四分半。页面统计起点包含这段真实时间。
3. **一项此前修复确实在正式发布路径中丢失：构建期字节码。** 优化加在 `Dockerfile.v2` 和 W3 专项 overlay 工具，但正式发布实际使用 `deploy/Dockerfile.source-release`，该路径没有执行编译，构建 context 白名单也没有携带编译脚本。当前 Worker 的 Schema、SDK 和 Runtime Schema 四个实查模块均无 pyc。不是源码回滚，而是构建入口绕过了优化。
4. **双任务引入了新的租约交接风险。** 单任务在当前 Effect continuation 内保存阶段结果；双任务把 A/B 阶段保存放进原异步 Future，继承最初 Effect 的写租约。生产发生续租失败和租约换代后，两份模型已成功返回，旧 Future 的写入仍可能被旧租约拒绝，新 owner 又消费同一个旧 Future，最终落到下一次过期领取，产生约两分钟额外等待。不得通过取消 fencing 或延长 lease 掩盖。
5. 当前还有两项已证实的读放大：模板快路径缺少部分索引所需的 `kind='TEMPLATE'` 条件；补充维护查询以 ticker 的历史 Case 索引为起点，且同步挡在新 Case 派发之前。线程按 Case 隔离、双任务使记录增长更快，放大了旧查询缺陷。
6. 15:00 后存在真实、严重的整机 I/O 压力，且续租失败、数分钟只读事务和排队与之重叠。**尚缺该时段按进程/容器的历史内存、I/O 归因，不能认定全部由双任务、某个维护任务或 SQLite 单独造成。** 本方案把具体代码缺陷与尚未证实的资源触发者分开处理。

优先恢复正式构建中的编译优化、修复模板查询及双任务结果所有权，再精简补充维护和后台保护集扫描；不回滚双任务，不降低实时并发，不增加 Redis、线程池、常驻缓存或新调度状态机。

## 2. 先统一口径与历史基线

### 2.1 页面指标与 W3 专项不是同一个指标

| 指标 | 起止与扣除 | 本轮用途 |
| --- | --- | --- |
| 页面 `non_model_mean_seconds` | Case.created_at → Trade 真正生效的 `decision_effective_at`，扣除 W1/W2/W1R3及两份 Trade 等非 Operations 模型区间的并集 | 原样复算用户看到的数值；不改成只统计 W3 |
| W3 Trade 内部 | route_to_w3 创建 → Trade 业务提交/真实 owner 收尾，扣所有 Trade 尝试 SDK 区间并集 | 诊断准备、Ready、结果交接与提交 |
| Operations 内部 | `max(Operations Effect创建, Trade提交)` → Operations及其Effect真实收尾，扣 Operations SDK区间 | 单独验收运营链路 |
| 完整 W3 Case 内部 | 首个 W3 Effect → Operations 最终收尾，扣全部 W3 SDK 区间并集 | 不能把两阶段各小于5秒当累计达标 |

SDK 区间是 SDK Turn 的包络，包含其工具循环，不是纯推理。既有本地研究工具时间应加回内部；本轮大样本主要用持久账本边界复算，故 W3 数字是**下界**，不是最终全覆盖 SLO。它们已经超标，补齐尾部/本地工具不会使其变快。两阶段重叠、W1R3与W3并行，不能相加各段 P95，也不能简单相加页面前段与W3段的均值。

本地退避、锁等待、心跳失败、租约过期恢复都计内部。没有原始供应商强制等待期限的证据，不额外扣 Provider 等待；区间缺失列为 unknown，绝不按0扣除。

### 2.2 历时机制复盘

| 阶段 | 当时问题与修法 | 本轮复核 |
| --- | --- | --- |
| 11/08—11/10 事故 | 绑定顺序/409、失败准备资源遗留、长事务编码、历史引用扫描和恢复突发形成资源反馈；消除重复准备、移出大对象编码、限制后台历史扫描，保留原Job恢复 | 不能再把故障简化成“服务器负载高”；目前仍发现另一路自动保护集扫描，见§6 |
| 11/09—11/11 Provider Pause/Future | 普通429全局暂停误挡成功消费；成功结果绕过新请求准入，原Job查询区分404与未知，Future完成直续接 | 这些主修复仍在，没有本轮证据表明普通429全局暂停回来了 |
| 11/11 执行链优化 | 编译真实导入图、模板读快路径、公共marker按变化写、短写优先级、先派实时Effect再领后台、消除Native JSON往返、分年度日历 | 多数源码仍在；**编译步骤没有进入正式 source-release 镜像；模板快路径的SQL一直没命中部分索引** |
| 11/11 最终固定cohort | 完整11例：Trade P95 **6.066s**，Ops **6.692s**，完整W3 **12.009s**；此前先完成子集曾接近5秒 | 历史报告明确未全面通过，不应把局部快窗当全天累计≤5秒的已验收基线 |
| 11/13 页面计时修复 | Serving曾跳过生效Effect；OpenRouter成功调用未记model_clock。修复源投影与起止记录，缺历史锚点保留unknown | 本轮页面与源账本能对齐，没有发现同类漏扣双分支 |
| 11/15—11/16 v6/双任务 | 每Case隔离Thread、Operations精简、统一模型、A/B并行；随后修复Native摘要写入和v6 Ready身份 | Thread隔离是业务边界，不能回滚为跨Case复用来换速度。新增异步双分支保存与旧Effect租约耦合，是本轮应修的差异 |

历史依据：[11/10实现](runtime_w3_execution_optimization_implementation_20261110.md)、[11/11诊断](runtime_w3_non_sdk_latency_audit_and_repair_plan_20261111.md)、[11/11最终验收](runtime_w3_latency_implementation_and_acceptance_20261111.md)、[页面计时修复](../../docs/non-model-timing-repair-20261113.md)、[双任务发布](../../docs/w3_v6_dual_main_release_20261116.md)。

## 3. 样本、生产版本与回退规模

固定候选取最近2,500条Case主键/标量元数据，最早为11/14 07:51北京时间；从中选取11/16 19:06修复上线后、REALTIME且进入W3的1,298例。采集起点为11/17 16:47:25；逐Case用索引读取，不长持全库快照。1,297例Trade已提交；4例模型区间不完整，另1例未完成，均保留。有效时钟1,293例：上一语义日1,224例、当前语义日69例。并非重新调用或回放这些Case。

当前语义日从**北京时间11/17 15:00（UTC07:00、美东02:00）**开始，不能称作美股开盘。指标会被这段异常的少量样本显著抬高。

### 3.1 统一口径结果，单位秒

| 窗口 | n | 页面非模型均值/P95 | W3 Trade内部均值/P95 | Ops内部均值/P95 | 完整W3内部均值/P95 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 上一语义日，上线后 | 1,224 | 13.15 / 39.42 | 8.79 / 26.16 | 3.05 / 7.81 | 11.80 / 32.63 |
| 当前语义日全部有效 | 69 | **41.65 / 271.90** | **15.31 / 48.03** | 5.39 / 12.77 | **20.55 / 52.08** |
| 当日15:00—15:25创建 | 16 | 123.75 / 316.71 | 40.25 / 161.48 | 9.65 / 36.54 | 49.70 / 174.14 |
| 当日15:30后创建 | 53 | 16.87 / 47.65 | 7.79 / 26.34 | 4.10 / 11.57 | 11.75 / 28.75 |

当前69例中，W3 Trade 46例超过5秒、Ops 23例超过5秒、完整W3 64例超过5秒；不是只有极端长尾造成平均值偏高。15:30后压力减弱，仍没有恢复到5秒硬线。

页面对应ticker均值：INTC **41.64s/22例**，NBIS **41.83s/14例**；CEG **181.58s/2例**、BE **170.61s/2例**。其他如COHR31.19s、MU24.76s。不同采集时刻新Case会改变分母，不能按ticker均值再平均冒充总体均值。

### 3.2 版本没有在异常发生前刚重启

Worker/Scheduler实际revision为`cbff3269b`，自11/16 19:05—19:06运行，自动restart=0、OOMKilled=false。Projector/API镜像为其他正式提交，但本次核验的计时及W3关键文件哈希相同。相关业务源码没有被并行会话覆盖回旧实现。生产配置仍为`dual_consensus`。

这说明：**已有约一天的冷启动成本回退，叠加当日15:00后的资源/续租异常；不能把所有增量都解释成刚切开关或重启。**

## 4. 已证实问题：原因、证据与边界

### 4.1 正式构建漏掉已完成的编译优化

实际链路：

`storage_release.build → backend-deps → deploy/Dockerfile.source-release:backend`。

`Dockerfile.v2`最终backend里的`compile_w3_imports.py --calendar`没有在这条链上执行；其专用W3 overlay发布工具会编译，正式source-release不会。构建context白名单也未包含该脚本。当前Schema/SDK/Runtime Schema/`openai_codex.async_client`均没有缓存文件。

断网、无生产挂载、单CPU/512MiB的一次性容器对照：相同376个导入源，原镜像导入 **2803ms墙钟/2612ms CPU**，临时checked-hash缓存后 **1505ms/1456ms**；零SDK初始化/模型调用。证明每个冷进程有可消除的重复编译成本，**不能据此宣称已解决全部Fork或5秒SLO**。

真实CEG `case_7254c65d048c42efaacddd9a6bb38cdc`：两分支均POOL_MISS，Capsule导入/初始化约4.40s、5.77s，CPU约2.06s；Slot从创建到Ready约11s。不是简单增大线程池可解决的问题。

### 4.2 模板读快路径没有使用已有索引

`W3ThreadRegistry.template()`的查询为：

```sql
SELECT * FROM runtime_v2_w3_threads WHERE template_key=? AND status='READY';
```

索引`w3_template_key`仅覆盖`kind='TEMPLATE'`，原SQL没有该条件。生产EXPLAIN是`SCAN runtime_v2_w3_threads`；加入kind后是`SEARCH ... USING INDEX w3_template_key`。只取主键的有界探针已达到10,000条上限，不能声称线程表只有10,000条。

15:14:13记录该只读事务耗时**420.197s**。这包含当时I/O/调度压力，不等于CPU扫描本身需要七分钟；但不必要扫描是已证实的放大路径。修复不需要新索引、缓存或降低公共准备频率。

### 4.3 双任务旧Future保存阶段结果，与新owner消费相冲突

单任务`_resume_result()`在当前Effect owner中读模型结果、`save_result`、业务提交。双任务`run_trade_pair()`却在原协程里`await write_io(...save...)`；`_resume_pair()`只`reader()`再聚合。Future继承原`write_scope`，并不自动获得新Effect租约。

BE `case_9f9acd13af064a67a42006f1a998b9ef`的精确时间（UTC）：

| 事件 | 时间 |
| --- | --- |
| Effect初始领取，token1 | 07:06:29 |
| SDK尚在运行时，重领token2 | 07:16:25 |
| B SDK结束/回执落库 | 07:16:34.567 / 07:16:35.084 |
| A SDK结束/回执落库 | 07:16:44.402 / 07:16:45.299 |
| 原Future完成并立即续接 | 07:16:45.575，handoff **0.012ms** |
| token3重新领取 | 07:18:53.799 |
| Trade真正生效 | 07:18:57.106 |

两份结果均成功，最终SDK结束至生效仍 **132.704s**。INTC `case_66c628e8f3bb400c97857613a6c4b39f`同类尾部 **128.451s**。这不是模型推理，也不是Future线程池排队；原Future已经立即回调，问题在租约和阶段保存/错误收尾。

日志同时存在Effect续租OperationalError、`_resume_pair`调用栈，以及保存失败状态时再次`LeaseLost`逃出线程的栈。当前异常分支内再次`save_effect()`的失败可能跳过正常收尾，留下RUNNING Effect等待过期，进一步放大尾部。

本地用真实`run_trade_pair()`、真实fencing/短写路径、零模型替身复现：协程未完成前把租约从1换为2，原Future保存被拒；相同两份结果在当前owner2中均可保存。**保持拒绝旧owner是正确的，要修的是结果消费位置与恢复交接。**

### 4.4 补充维护同步阻塞新派发，查询随历史增长

`RuntimeCoordinator._tick()`在遍历和派发CASE任务之前同步执行`_schedule_supplements(ticker)`。查询无分页，生产计划从`idx_runtime_v2_cases_maintenance (ticker=?)`扫描该ticker的历史Case，再逐行查dirty/owner；并非从少量dirty通知出发。

当日全局dirty通知有界读取为623条；当前代码仍会按每ticker历史范围扫描，再逐项读Case/Effect/维护任务。语义日切换使昨天的通知集中具备处理资格，增加查询和逐条事务。这是同步挡路及读写放大的确定代码事实，**尚不能把某个Case首次模型前的全部300秒精确归给该函数**。

15:14:45同函数只读事务耗时 **452.900s**；另有`task.renew`约211s、ACK提交等待28.7s。原始记录中的hold不等于写锁持有：该函数本身是只读，延时含真实读取、调度切出与I/O等待。

CEG `case_70c5fd5cbb774da7890a08f57cc148ca`：Case创建07:01:09，首次W1/W2调用约07:06:08，进入W3前扣模型后299.658s；W3本身内部16.732s。NBIS `case_2ea122f6420a41e39f52abf190933a30`进入W3前266.694s，W3内部仅6.685s。单改W3 Future不能消除这些页面高值。

## 5. 资源压力的事实与尚未闭合的因果链

主机sysstat记录：14:50/15:00可用RAM约11.8/11.9GiB，磁盘读取约2MiB/s、await约0.8ms；15:12:52可用约2.26GiB，load1 **534.92**、blocked **344**，I/O wait **31.98%**，vda平均读取约105MiB/s、队列112、await56.29ms；15:20读取约119MiB/s，15:30后明显回落。异常采样本身已延迟，不能把15:12:52当压力首次发生的精确时刻。

15:08—15:16有多次Effect续租失败。链条中已能确认的部分是：

> 严重内存/物理I/O竞争 → 数据库及控制读写变慢、续租失败 → owner换代 → 双任务原Future阶段写入失效 → 原结果没有立即由新owner完成提交 → 约120秒过期恢复。

**未闭合部分是压力最初由谁、以多少资源占比触发。** 当前资源快照Worker约9.9GiB、Scheduler约3.7GiB、两个浏览器角色合计约9GiB；它们不是15:12的历史PSS，不能倒推事故归因。Worker诊断自启动最大loop lag约386s，但无对应发生时间，不能把它直接贴到这个Case上。

双任务有真实放大效应：两次私有Fork、SDK和结果保存；非选中分支没有显式解除终态resident，Capsule普通prepared idle回收为300秒、Trade handoff保护为90秒。但不能将全部RSS或I/O都算到A/B。

另发现仍在每300秒自动执行的`resource_maintenance.collect_inputs()`：workspace删除候选已分页，但构建published/evidence/引用保护集仍扫描全历史；snapshot保护计算也有广域部分。`limit=32/16`限制删除数，不限制前置读取工作量。这与旧事故禁用全历史热回收的原则存在冲突，是应审查/缩窄的已证实实现风险；**没有该次调用的起止与进程I/O记录，不能认定它就是本次唯一触发者，也不能声称本次已证实全量正文展开。**

## 6. 推荐修复：按证据依次推进

### P0-A：让既有优化进入真实发布入口

文件：`deploy/Dockerfile.source-release`、`scripts/ops/storage_release.py`、`scripts/ops/compile_w3_imports.py`。

1. 正式archive whitelist加入编译脚本；source-release在最终源码COPY和editable安装之后执行`python scripts/ops/compile_w3_imports.py --calendar`。同时覆盖实际Worker SDK/Pydantic导入图；复用原脚本，不新建另一套编译器。
2. 不修改`PYTHONDONTWRITEBYTECODE=1`，不在首Case运行时补写缓存；保留checked-hash失效方式。
3. 正式候选及运行镜像都核验解释器真实路径、缓存存在和source hash。测试必须走`storage_release.build`真实context，不能仅测试Dockerfile.v2或专用overlay。
4. 在未发布的候选容器内重复零SDK冷导入对照；不要在生产容器现场编译来临时掩盖构建缺陷。

### P0-B：消除模板无关历史扫描

文件：`persistent_runtime_v2/w3_threads.py`。

所有通过template_key定位模板的读/写查询均明确加`kind='TEMPLATE'`，包括首次只读快路径和写事务内二次核验。沿用原唯一索引、兼容键、租约CAS；不新建索引或内存模板缓存。

回归使用大量CASE行及少量TEMPLATE行，EXPLAIN必须命中`w3_template_key`；验证缺失/初始化中/失败模板仍走原恢复。只测返回值不够，要防止再次退化成SCAN。

### P0-C：恢复“模型结果传输”和“当前业务owner提交”的分工

文件：`persistent_runtime_v2/w3.py`、`w3_execution.py`、`service.py`、`repository.py`。

1. 双任务Future负责原Job观察、回执持久化、解析和返回每分支的结果/Job；**不在捕获旧Effect lease的Future里写阶段及业务状态**。原`worker_receipts`已是可恢复屏障，不另造结果表。
2. 初始同步返回及`_resume_pair()`都进入同一个当前owner保存helper：对每份已有可消费回执幂等保存对应A/B stage，再按现有规则聚合、原子提交一个Trade/一个Operations。支持一份成功、一份失败时保留成功记录；重试读取原Job/回执，不重跑已成功分支。
3. 新owner不得盲用带旧owner副作用的closure。旧回执可以消费，旧写租约不能继承；不取消可能已受理的Job，不删除原Thread，不省略校验或fencing。
4. 修正`_process_claimed_effect_inner`异常分支的再次保存：失败写入/租约丢失应回到现有统一收尾与恢复路径，不能从异常处理器再次抛出未处理错误后留下RUNNING空壳。失去owner不能擅自修改别人的lease。
5. 续租仍复用原20秒周期、120秒lease及现有短写owner。将batch准备放writer外，续租提交走既有高优先级短事务，检查实际SQL执行时的当前时间；SQL busy按现有让出机制处理，避免普通writer的30秒等待/重试占用整个续租机会。不得延长lease、SQLite timeout或增加重试预算来达标。
6. 旧owner确认失效后，由当前调度恢复者读取持久结果立即续接；不能固定等下一次120秒过期，也不能跳过明确404/派发未知区分。

这是对双任务新增交接点的局部修正，保留单Case/单Trade Effect/一次业务提交/一次Operations的骨架，不建立新的分支调度框架。

### P1：让补充维护有界、离开实时派发前置链

文件：`coordinator.py`的`_tick/_schedule_supplements/_schedule_case_supplement`。

- CASE及实时Effect正常派发后再触发补充维护；耗时drain复用已有background executor，同ticker最多一个已有型Future，dirty_case仍是唯一持久通知，不新增任务队列或数据库状态机。
- 查询从`runtime_values(namespace='dirty_case',key)`有界页开始，再按Case主键核验ticker、语义日及owner。以实际EXPLAIN验证，不靠加LIMIT掩盖上游历史扫描；必要时用明确的dirty-first联接顺序。
- 每页建议32条，仅在短读事务中取通知和最小标量，随后退出事务再读冻结内容/处理。使用每ticker轻量seek cursor保证忙parent或无效通知不会长期占住第一页；重启丢游标无损，因为原通知和幂等键保留。
- 保留parent状态、primary maintenance、effect_versions、冻结frame、比较后删除通知的原规则；不合并或遗漏业务补充。不在数据库读事务内访问文件、解码大Native或遍历全Case。
- 同时缩窄自动workspace/snapshot回收的保护集：先定位候选owner/family，复用已有manifest和索引证明引用。无法在有界范围确认跨run保护的候选继续保留，把广域证明留给显式静默维护；不冒险删除，也不在实时流量中反复全历史展开。保留必要的轻量生命周期退出，不恢复全量热扫描。

### P1：减少双任务不必要的物理驻留

文件：`w3_execution.py`、现有ACK/清理链、Worker `capsules.py`。

Trade聚合提交后，非选中分支已无后续Operations用途；在现有异步收尾链中解除其resident的handoff并释放物理进程，**保留逻辑Thread和两份Job/原始结果**。Operations提交后同样退出选中resident。复用现有`release_thread`的busy检查和清理隔离，不调用删除对话历史的`delete_thread`来释放内存。

必须防迟到ACK、在途Job、同线程Ops已启动和cleanup未完成的竞态；失败释放交给原有清理重试，不阻塞Trade提交，不新增resident表或进程池上限。当前`release_unused_case`会对已有Job直接返回，不能误以为调用它就会释放已执行分支。

### P2：只对仍超标的Ready/首轮准备补齐定位后优化

CEG两个Slot在UTC08:05:19已发布Ready，而Scheduler领取约08:05:28才结束，存在约8–9秒未闭合交接；当前记录不能区分handoff、loop stall、HTTP回包与重新回读。先加复用trace的六个标量边界：ready SQL返回、私有handoff开始/结束、claim HTTP返回、Scheduler接收、materialize完成。不得直接认定是20秒timeout设计或slot排序错误。

W1/W2前置同样记录admitted、task真正领取/线程开始、输入快照读取、daily index冻结/事实桥接、首次请求发出。对于当日约267–300秒头部，目前只能确认发生在首次模型前；不能把冻结事实缓存、Case并发或某个pool当作已经证实的唯一原因。先修已证实同步前置扫描，再看残差。

若编译恢复及上述减法后仍有CPU导入成本：用既有无模型启动探针定位Pydantic模型构建/重导入图。先减不必要导入；仅在现有SDK已验证可完全卸载旧线程并重新绑定凭证的前提下，评估复用安全终态的**物理client**。不得继承别的Case对话，也不靠扩大Ready、warm或常驻池来达标。没有证据前不实施这条分支。

## 7. 验收与发布顺序

1. **先正式构建与查询计划修复，再结果owner及有界后台链。** 分提交、分候选；合并验收后定向发布Worker/Scheduler，若修复验收工具则同批版本冻结。不为本轮改Prompt、模型、参数、Schema、W1/W2路由或交易资格。
2. 补齐自动回归：双任务重叠/不重叠/重试区间并集；一个分支已成功时租约换代、旧Future迟到、当前owner立即消费、无重复模型与业务提交；事务busy发生在错误收尾；小Ready回执不等无关后台工作；dirty量/历史Case增长时查询仍只读有界候选；终态resident退出不损伤Ops/在途Job。
3. 修复现有`scripts/ops/w3_latency_audit.py`的`len(phases)!=2`假设：双任务有主Trade、A、B及Ops四行，不能全部判incomplete。A/B按Job ID计区间，主Trade代表记录不再重复计费/扣时；SDK重试全部覆盖。使用实际owner最终trace、提交真实返回及本地工具回加，不能只靠本轮账本下界通过。
4. 工程先验预算：索引模板/内存判断P95应在毫秒至几十毫秒；单次轻量读/短写宜≤100ms；调度/Future交接≤100ms；上下文与本地准备合计宜≤500ms；物理启动目标亚秒，不能把现状4–12秒认作合理预算。预算用于寻找必要工作和额外等待，**不相加分段P95冒充总P95**。
5. **Trade、Operations、完整W3 Case的非SDK内部耗时P95≤5秒均为硬线**；页面跨W1/W2的Case→Trade指标另列，当前需要一并降低，不能通过改起点美化。真实Provider拒绝单列，内部等待/重试/锁/lease恢复全计入。
6. 固定连续入组名单，至少100个自然Case，跟到全部终态/明确缺证据；覆盖语义日15:00前后及正常开盘、高并发时段。分单任务/双任务展示，仅作为自然模式样本或后续明确授权的对照，不为验收主动切模式或下单。未完成、失败、区间缺失全部列示。
7. 在既有有界观测中补Case对应的RSS/I/O来源：峰窗记录三四个主要容器的anon/file/IO差值、活跃/空闲/handoff Capsule数、自动GC开始/结束及扫描行数；低频标量即可，不读取进程env、全目录内容或每步写DB。这样才能闭合最初I/O触发者，而不是再次凭现时RSS倒推。
8. 发布保留原Job/Thread身份，明确已受理/404/未知。发布窗口本身的等待也计内部；不得降低实时并发、引入模型准入排队、扩大timeout/退避、增建Redis或常驻监控服务。未到5秒就报告未达标，不能只给容器健康或短窗先完成Case的结果。

## 8. 证据与本轮边界

证据目录：[eval/w3_nonsdk_regression_20261117](../../eval/w3_nonsdk_regression_20261117/)。关键文件：

- `inventory.json`：实际镜像、源码哈希、启动时间；`findings.json`：有效cohort、完整性与逐Case重算；`cohort.json.gz`：有界主键关联元数据，不含完整冻结Prompt或API key。
- `logs.json.gz` / `trace-summary.json` / `control.json` / `exceptions.json`：五个选定Case的边界、续租及错误收尾；不导出隐式思维链。
- `sar-before.json` / `sar.json`：事件前后整机实测；不能替代缺失的历史容器归因。
- `plans.json`：实际生产EXPLAIN；`pyc.json` / `import-ab.json`：缺失缓存及隔离对照；`lease-probe.json`：当前双任务保存位置的确定性复现；`ready.json`：Slot与物理启动元数据。

只读SQL均使用短连接/主键或现有索引限定范围，未做全库backup、checkpoint、完整性扫描或模型重跑。没有修改生产代码/状态，未对397条旧失败Case发起恢复交易。后续应按本方案得到实施授权后再修复、测试和发布。
