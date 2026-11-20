# Bus → Persistent Runtime V2 入站延时：根因与精益修复方案

日期：2026-11-21，北京时间。范围止于 Bus 已产生 Stream 记录之后的消费、准入登记和最初任务交接；本轮只勘察、只读探测和设计，没有修改业务代码、数据库或生产配置，没有发布、重启、重放消息或调用模型。

## 1. 结论先行

**201.22秒的IONQ与157.10秒的BE不是两次持续数分钟的SQL阻塞，而是前轮发布流程造成的消费者停止窗口，加上新Scheduler启动后的恢复准入时间。** 两次消息都在旧Scheduler已经进入发布停机流程之后产生，新Scheduler启动后分别7.65秒、6.78秒登记。Bus继续发布，消费循环已经停止；进程在优雅收尾期间显示Running，也不代表还在读取Stream。

**正常运行的约5.6秒P95则是另一个真实问题。** 当前所有ticker由单一主循环顺序处理，每轮先完成全部activation检查，再逐ticker读取、重做Control检查、reconcile、逐条多事务登记和同步领取、提交Bus游标、再次完整读取待消费内容以统计数量、写状态，最后额外睡1秒。前一个ticker的登记、领取和状态更新会推迟后一个ticker下一次读取。后台Runtime恢复已隔离，但这条入站链本身仍是串行的。

本轮确认三类可直接减少的工作：

1. 每轮重复连接和同义读取；只读对照已经证明已有连接作用域能够显著降低这部分成本。
2. 一条普通消息分开写入准入计划、Control准入、Task、高水位；生产已捕捉到连续数次writer等待。
3. 消费线程在游标提交前同步扫描、领取任务；`_tasks_lock`还覆盖整个SQLite claim等待，存在可复现的等待传播。

不建议新增消费者服务、分库、Redis、全局消息队列、连接池或新的调度状态机。先减少轮次工作和事务次数，再调整既有轮询节奏。不能只提前写`admitted_at`，把等待转移到准入之后。

## 2. 勘察基线、数据完整性与时间语义

### 2.1 当前代码确实在生产运行

- 本地/远端checkout：`4bf7762aed7da29efefa8d60f39c14b4f288a410`。
- 生产Scheduler：`a295d9584f4eb9d93e36b13c3f59e9d3099523e5`，UTC 11月19日17:31:38.272720启动，未再次重启。
- 实际容器内的`runtime_scheduler/v2.py`、`service.py`、`coordinator.py`、`journal.py`、Bus `repository.py`、activation `consumers.py`、Control `repository.py`七文件，均与上述生产提交逐字节SHA256匹配。相关入站代码与当前checkout没有差异。
- 数据库实测：Bus、Runtime、Initialization为WAL；**Scheduler状态库为DELETE journal**。这不是此前已修复的Read Serving WAL问题，不能混为一谈。

### 2.2 固定48小时队列

观察截止UTC **11月20日16:02:24.750209**，窗口起点11月18日16:02:24.750209，即北京时间11月19日00:02:24至11月21日00:02:24。

从最近8,192条CASE任务提取元数据，最旧任务已早于窗口。所有窗口内任务都保留；通过Bus的`(ticker, stream_offset)`唯一索引读取原Stream时间，补齐46条因source被NativeContent外置、不能直接`json_extract`的记录，没有把它们当缺失后丢弃。

| 口径 | 数量 | P50 | P95 | P99 | 最大 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 全部已登记入站CASE任务 | 2,742 | 2.011s | 5.423s | 8.537s | 201.223s |
| 其中已形成Case | 2,406 | 2.027s | **5.614s** | 8.716s | 201.223s |
| 已登记但未形成Case | 336 | 1.899s | 4.472s | 6.665s | 9.261s |

分位采用nearest-rank；不与其他窗口的插值分位混写。已形成Case的5.614秒，与用户5.63秒观察基本一致。全部任务中169条超过5秒，14条超过10秒，9条超过30秒。

336条未形成Case的任务已终态失败，不能算消费成功：有界gap复核为332条`FileNotFoundError`、4条`RuntimeInputUnavailable`。前者与历史Case创建依赖事故时间窗重叠，参见`docs/runtime-case-admission-lineage-repair-20261119.md`；本轮没有重放或扩展修复这些任务。这里只保留其准入延时，避免幸存者偏差。

这是一份**已登记队列**，不是所有已发布消息的完整集合。另一次快照中10个ticker的游标均追平；它证明当时没有残留未消费Stream，不能证明之前没有停顿，也不能代替持续覆盖或排除已经被业务准入规则跳过的消息。

### 2.3 `created_at`不是另一个独立的落库时钟

当前顺序为：`ControlRepository.admit()`返回 → 捕获`at` → 计算mode/sweep归属 → `put_task()`提交。`inputs.admitted_at=at`，因此它早于真正Task提交返回；`PersistentRuntimeV2Service.execute_message()`又把传入的`admitted_at`作为Case的`created_at`。本窗口2,406个Case两者全部相等。

因此用户定位的大方向正确，但两列不是两个独立计时点，不能据此声称已精确测出“Bus提交返回→Task提交返回”。`message_bus_event_time`来自Stream的`published_at`；live发布路径在已获得Bus writer后、事务末段生成该时间，仍略早于实际commit。不能把这段微小未观测间隙填成零，也不能把它反向解释成两分钟的模型耗时。

准入后冻结确实较快：2,406例`inputs_ready-admitted_at` P50/P95为**0.130/0.380秒**，最大4.022秒。它不属于本次分钟级长尾的主因，但后续验收仍需保留，防止把前置等待移到这里。

## 3. 两个极端样本已经闭合

以下均为北京时间11月19日；原始审计回执保存UTC。

| 边界 | IONQ `case_5380fde325964c318055bde4e0efa6cb` | BE `case_16962a44d4274ba6917f26e3d067712f` |
| --- | --- | --- |
| 发布操作启动 | 07:26:19.505，`runtime-critical-release.py e259765d9 ... v2-scheduler` | 07:34:30.120，`runtime-critical-worker-release.py 307db28d2` |
| Bus Stream时间 | 07:27:20.614900 | 07:34:57.915235 |
| 新Scheduler启动 | 07:30:34.182781，`e259765d9` | 07:37:28.235663，`307db28d2` |
| Control内准入时间 | 07:30:41.829262 | 07:37:35.003913 |
| `inputs.admitted_at / created_at` | 07:30:41.837673 | 07:37:35.013086 |
| `inputs_ready` | 07:30:42.093934 | 07:37:35.333493 |
| 总差值 | **201.222773s** | **157.097851s** |
| Bus时间→新容器启动 | 193.567881s | 150.320428s |
| 新容器启动→准入 | 7.654892s | 6.777423s |
| 准入→inputs_ready | 0.256261s | 0.320407s |

### 3.1 当时的发布脚本为什么会形成这个窗口

`eval/runtime_critical_chain_release_20261119/release.py`取得正式release锁后，**首先**执行`docker stop --time 1200 Scheduler`，然后才按顺序发布Projector、API、Executor、Scheduler。主循环收到SIGTERM立即停止；`PersistentRuntimeV2Service.close()`和`RuntimeCoordinator.close()`等待已经提交的executor、W3线程及Effect收尾。等待模型结果和其他组件替换期间，Bus仍工作，但没有该消费者。

`worker_release.py`则顺序停止其他派发者和Scheduler，再等待Worker在途Job排空、做idle fencing、逐组件替换。BE就在这个窗口发布。

证据不仅是时间相关：journalctl保留了两次发布命令；当时存档的`preflight-post-e.json`和`verify-final.json`保留了实际新容器StartedAt；发布脚本源代码明确把停止消费放在前面。IONQ在发布期间07:28的Docker快照仍显示旧Scheduler Running，恰好说明**Running与仍在消费不能等同**，不是反证。

没有逐毫秒的旧容器SIGTERM回执，不能把release命令启动时间写成精确停止完成时间；但分钟级空档与恢复时间的因果链已经闭合，无须再假设存在一笔201秒的SQLite事务。

### 3.2 其余长尾及归因边界

另外5条33.01–48.51秒消息都在UTC 11月19日17:31:44–48集中登记，对应当前`a295d9584`发布：实际Scheduler17:31:38启动，发布命令17:29:51、回执17:31:48均可核对。这也是发布恢复，不是五次独立的provider限流。

两条SNOW为49.84/37.15秒，集中17:02:53登记；服务器同一时段保留了`runtime-facts-overlay-20261120`发布材料，`compose-before`为17:01:58、`deployed`为17:02:57，实际包含Scheduler镜像替换。**强相关于该次发布，但没有保留该旧容器精确StartedAt，故不冒充与前两例相同精度的逐段闭合。**

即使只作为敏感性分析扣除这9条超过30秒记录，已形成Case子集P95仍约5.460秒。**发布解释了极端值，解释不了正常P95。** 正式验收不得删掉这9条。

## 4. 正常入站关键链：必要屏障与额外工作

代码入口：`runtime_scheduler/v2.py:163`、`service.py:159/232`、`coordinator.py:165/235/329/357`。

```text
主循环 run_due_once
  ├─启动/复用后台既有任务恢复线程（不是这里等待W1/W2/W3）
  ├─全部ticker的activation / proof / Control / policy-head检查
  └─按ticker排序串行 tick_ticker
      ├─Scheduler状态、Control再次读取
      ├─Bus offset读取 + pending_stream整页物化
      ├─coordinator.reconcile（日界/sweep安排、calendar读取）
      └─逐Stream：冻结分组计划 → Control准入 → put_task
          → dispatch_realtime扫描/同步claim → inbox_highwater
          → Bus cursor commit
      ├─再次pending_stream，完整物化只为了len()
      └─每轮upsert ticker状态，再get_state确认
  → 本轮所有工作之后固定再睡1秒
```

普通REALTIME消息的事务清单：

| 库/环节 | 当前工作 | 必须保留什么 | 可消除或合并什么 |
| --- | --- | --- | --- |
| Initialization | active revision、Bus ACK、Runtime ACK、周期heartbeat | 当前有效revision与真实安装/授权证明 | 同轮逐函数重连、重复ACK查询；正常proof命中不重新加载业务材料 |
| Scheduler | 多次get_state；每ticker每轮状态UPDATE，再SELECT | 状态/健康变化、真实计数及消费时间 | 无变化持久化、重复对象编码和回读 |
| Bus读取 | offset SELECT、Stream范围SELECT、每item成员join、Native文件解码 | 顺序、成员完整性、原offset身份 | 空ticker仍重复连接；统计数量时二次解码正文 |
| Runtime计划 | `value.stream_admission_plans`独立写全文 | buffered成员/分组冻结、重试不改变原case组合 | 与Task重复的完整source；可与新Task一次注册 |
| Runtime准入 | `ControlRepository.admit`独立writer | 最新analysis/removal/mode及原trade_eligible | 与Task分开的writer；必要检查不能移到陈旧缓存 |
| Runtime Task | `put_task`独立writer；Native编码在事务外 | 原任务ID、原source与首次准入时间不变 | 前后的重复Task读取、单独计划事务 |
| Runtime claim | `_tasks_lock`内进入`journal.claim`写事务、租约/并发计数 | 原任务租约、token、容量与恢复 | 数据库等待覆盖内存锁；每新消息扫描所有active任务 |
| Runtime高水位 | 每item独立`inbox_highwater`写 | 表示已持久登记的Stream边界 | 并入完整plan/Task登记，不重复写相同值 |
| Bus cursor | 每item一次Bus短写 | Runtime已持久登记后才能推进 | 不需要等待业务执行/模型；可按同ticker已登记连续前缀提交 |

临时库直接运行现有方法验证：首次`accept_stream`确实3个Runtime写事务；随后高水位再1次，claim再1次；Bus cursor另1次。Scheduler状态还有1次/轮，不能把这些全算作一笔“准入写入”。重放同一item没有重新写计划/Task，现有幂等能力必须保留。

## 5. 正常慢点的生产证据

### 5.1 消费检查间隔本来就不是1秒

UTC 11月20日16:04:34–16:06:04，用一条只读连接每250ms读10行状态，360次有限采样，无写入、无错误。状态记录的tick起点表明：

- 同ticker已观测检查间隔的中位数约3.14秒，P95约4.31秒，个别9.04秒。
- 这些间隔重复描述同一全局轮次，不是250个独立全局样本。BE有25个完整轮次，P50/P95约3.15/3.84秒；窗口末端突发拉长其他ticker间隔，最后完整轮次尚未全部落在观测窗口中。
- 这是机制实测，不是稳定运行SLO估计。它已足以证明“CLI interval=1所以最多等1秒”不成立。

现有`stop.wait(args.interval)`是**工作量之外的固定休眠**，不是deadline。十个ticker、activation、登记、claim及状态写入的总时长都会叠加到下一次检查前。

### 5.2 不需要复杂业务，也会反复承担连接/schema成本

生产容器内另起有限只读进程，所有SQLite连接强制`mode=ro/query_only`，只执行明确的读取方法，不运行真实tick、不写ACK。两轮10个ticker的组件校准共262次新连接，其中含额外“一条真实Stream物化”探测；生产空轮按当前调用结构约需140次新连接，具体随分支变化。

三组只读对照，均使用现有代码：

| 10 ticker工作 | 当前读法墙钟 | 复用/同轮批读墙钟 |
| --- | --- | --- |
| 30次同义Control读取 → 同轮读10个 | 380 / 405 / 295ms | 11.8 / 1.0 / 1.2ms |
| 40个Bus连接 → 已有`connection_scope`下10个连接 | 479 / 355 / 361ms | 118 / 89 / 80ms |

包含连接、首次查询、schema与对象处理，不只算`sqlite3.connect()`。不能把微基准的差额直接加到全Case预测收益，也不能把3次试验叫P95；但重复工作和精简方向已经有证据，不必新建连接池。

### 5.3 正常突发出现真实writer等待

UTC 11月20日10:57:15–29，BE/CEG/COHR/INTC/MU/NBIS依次登记。第一条NBIS约9.285秒，后两条9.724/10.068秒。相应生产日志：

| 操作 | 总耗时 | `wait_ms` | hold / commit |
| --- | ---: | ---: | ---: |
| `value.stream_admission_plans`，10:57:24.800返回 | 1,415ms | 1,388ms | 9 / 18ms |
| 入站`Control.admit`，10:57:25.173返回 | 374ms | 353ms | 7 / 13ms |
| `put_task`，10:57:25.430返回 | 255ms | 238ms | 7 / 11ms |
| 后一笔入站`Control.admit`，10:57:27.012返回 | 442ms | 415ms | 12 / 15ms |
| `task.claim`，10:57:29.309返回 | 1,916ms | 1,767ms | 131 / 17ms |
| `inbox_highwater`，10:57:29.851返回 | 504ms | 451ms | 21 / 32ms |

其中25.173返回的入站Control与第一条NBIS的`admitted_at=25.172940`相邻，27.012与第三条NBIS相邻。它们是对该消费窗口的直接证据，而非引用无关慢SQL。claim和高水位发生在准入时间之后，**不能反加到同一消息准入之前**；其同步执行会拖慢后续消息/下一轮。

`wait_ms`覆盖连接/PRAGMA、进程内gate及SQLite BEGIN之前的等待，**不能直接等同跨进程SQLite锁等待或某一个持锁者**。现有日志没有给入站操作关联stream/Task identity和gate持有者，1,388ms仍不能精确分配给这几类。应减少重复准入次数，同时补轻量分段，不能凭此单独指责Projector、GC或线程池。

### 5.4 两个确定的同步放大点

1. `coordinator.reconcile()`仍在Stream已读出、尚未登记之前同步执行；后台`coordinator._tick()`也会reconcile。日界正确性有价值，**每个空轮都前置重复calendar/history读取没有等同价值**。不能直接删除后台恢复；当前并不存在两套完整`coordinator.tick`都在main执行的情况。
2. `_submit_task()`取得`_tasks_lock`后才进入可能等待writer的`journal.claim()`。本地用阻塞claim运行原方法，证实其他线程无法取得该内存锁。这是确定的代码机制，尚不能声称它独自解释了某个16.75秒Case。

另外，UTC11月20日09:10的BE有16.751秒准入长尾，同窗口有多笔claim等待。缺少当时入站分段时钟，无法把16.751秒逐项闭合；不将未知部分填作SQLite或资源不足。Scheduler库DELETE模式及单条大消息解码放大目前也只是潜在放大项，没有已量化的独立贡献。

## 6. 推荐实施方案：先减少工作，再缩短轮次

### 批次A：一轮只读取必要事实，空轮不制造写入

修改`runtime_scheduler/v2.py:_run_admitted_once`、`service.py:run_due_once/tick_ticker`、`ticker_initialization/consumers.py:admit_runtime_revisions`及相应repository读取接口。

1. 将当前revision/ACK、Scheduler状态、Control、policy authorization标量按**同一轮**批读，复用既有`_runtime_admission_proofs`。同轮向`tick_ticker`传递已读事实，删除outer/tick等价读取；不新增跨轮授权缓存。新Task的真正准入仍重读最新Control。
2. Bus沿用已有`connection_scope()`，仅包住同一线程、同步短工作单元；读取仍autocommit。不得把连接/读事务带入Future、模型或其他线程，不跨网络持有事务。
3. 对空ticker，使用索引化head/cursor轻量检查，确有待消费时才物化正文；计数用`SELECT 1 ... LIMIT event_limit`的数量，不再二次物化整页。保持当前有上限的显示语义，不用`head-cursor`假设offset绝对无洞。
4. 状态/计数/健康没有变化时，不每ticker每轮重写整个state并回读。复用现有15秒健康节奏；需要持久heartbeat的字段按既有健康周期批量短写。消费数量、错误、模式、真实消费时间等变化立即保存，确认前端不会把减少空轮写入误判为停机。
5. 把日界reconcile从每个item准入的硬前置移开：同语义日且schedule边界已对齐时直接使用其既有结果；首次启动/换日仍执行一次必要安排，再消费。不能把旧schedule当新日归属；保留sweep补建与后台恢复。

预期：直接删除约0.5–0.8秒/轮的重复读取成本，减少空轮写入；是微基准支持的量级估计，仍以自然队列验证。优先于提高频率，避免把低效轮次重复四倍。

### 批次B：一次性注册Stream计划、准入和Task

修改`RuntimeCoordinator.accept_stream/accept`、`RuntimeJournal`及`ControlRepository.admit`的事务内复用入口。

1. writer外完成成员过滤、分组、Source编译、NativeContent编码及准备必要skip记录。不得在writer里解码正文、压缩或fsync文件。
2. 新增**局部的**`register_stream_admission`入口，以一笔Runtime短事务完成：重核已有plan/Task → 最新Control准入 → 插入全部新Task及原source → 完整登记回执 → 单调高水位。复用现有表和身份，不新建状态机。
3. 新回执只记录版本、stream身份、分组Task ID和必要跳过依据，完整source只冻在Task一次。新plan和全部Task同事务提交，回执即表示登记完成，不再保存第二份全文。旧数组形态的plan、已存在Task和部分旧plan继续走兼容恢复，不覆盖首次admitted_at、原分组或原trade资格。
4. Control增加`admit_in_transaction`内部助手，原公共`admit`包装保持契约；ticker暂停/移除、mode边界、origin身份仍当场校验。既有任务重放不重新授权、不修改原输入。
5. Runtime提交成功后再提交Bus cursor；可以按同ticker已经完整登记的**连续前缀**合并cursor短写。两库不做跨库原子提交：利用原任务/回执幂等，崩溃后从旧cursor重放；失败item及未登记后缀绝不跨越。
6. 游标提交不等待claim或业务完成。提交后仍立即派发新Task，后续崩溃由现有durable pending恢复。这里减少的是SQL/扫描工作，不是将模型派发放到新队列等待。

目标是普通Stream登记从4次Runtime准入相关writer进入降为1次；**claim、租约及之后业务事务不冒充被删除**。buffered多组仍整体冻结，CLOSED_SWEEP的真实owner/window和supplemental归属保持不变；复杂首次边界安排可保留其独立业务事务。

### 批次C：解除领取等待传播，并改为deadline轮询

1. `accept_stream`返回刚登记的Task身份，实时派发直接针对这些ID；不每条新消息都扫描全部active任务。既有容量释放回调和周期恢复负责补位，不改变每ticker Case/模型并发。
2. `_submit_task`中不持`_tasks_lock`等待SQLite：短锁预查 → 原子`journal.claim` → 短锁重核关闭状态并注册Future。租约本身裁决并发claim；若成功claim后恰逢shutdown/submit失败，按原owner/token有条件defer。没有成功claim不得执行；不可无条件清理别人的lease。无需新增一套reservation表。
3. 上述减量生效后，将现有main loop改为单调时钟deadline：一轮完成后只等待本周期剩余时间，不再“完整工作量+固定睡1秒”。初始目标周期250ms；工作超过周期时不补睡、不堆积下一轮、不新增ticker线程池。`--once`及停止信号语义保留。
4. 串行时仍保留每ticker原有顺序、事件页上限和单消费者。只有实测显示大页仍长期阻挡其他ticker，才在原轮次内做有界轮转；本批不预先增加队列/构建者/模型调用并发。

### 批次D：修正发布造成的消费空窗

这是两条最大样本的直接修复方向；不能只修正常轮询。

- 构建、源文件核验、磁盘清理及与Scheduler无关的组件发布，全部在消费者保持工作时完成，不能先停止Scheduler再串行做这些事情。
- Scheduler/Worker确需替换时，沿用**较新发布流程已有**的“Scheduler仍运行时观察排空，再进入短切换窗口”，不恢复本轮找到的旧`stop --timeout 1200`前置流程。最新Elapsed发布已采用这一方向，属于应统一保留的改进，不重复宣称新开发。
- 把旧临时wrapper的行为收敛进既有正式release owner的角色检查：Scheduler应最后才退出，临界窗口内不夹带无关health等待、retire/GC或其他角色构建；记录停止消费、旧owner收尾、容器启动、首个成功poll和首Task提交时钟。已受理Job、原请求身份和paid receipt仍受原fencing保护。
- `assert_scheduler_idle`与实际停止之间存在新任务到达竞态，必须作为发布测试而非靠“缩短docker stop到15秒”处理。若在途重新出现，应退出切换、继续服务或完成原任务后重试，不能强杀付费调用。现有Worker idle fencing保留。
- **单进程同步停机替换本身存在真实空窗**。本最小方案移除分钟级可避免停顿，但不承诺所有发布零中断；全部发布样本继续进入延时统计。如果仍要求在持续有模型调用时无缝替换Scheduler，需要单独验证现有lease/fencing能否支持一次性的原owner恢复交接，再决定改shutdown边界；不在本轮直接增加第二个常驻消费者，也不靠提前登记Case掩盖后续业务等待。

### P1：Scheduler库与剩余锁归因，按证据处理

先通过A/B减少其写入。补充Scheduler库独立的connect/BEGIN/commit计时；若仍明确受到跨进程reader/writer阻塞，再在正式启动/迁移时设置并验证WAL，审查API、文件恢复及备份使用SQLite backup而非只复制主文件。不在每次GET/tick重设PRAGMA，也不把当前DELETE模式直接宣布为已证实主因。

本轮不再扩大到W1/W2/W3模型、Prompt/Skill、交易资格、Fact Library全库、Transport Maintenance重构或此前已经完成的Future优化。

## 7. 必要观测与正确性测试

观测沿用现有timing设施，不新增逐阶段业务UPDATE。内存记录少量monotonic span，每item/每轮聚合一次；慢于1秒才记录详细分段，常态只保留计数/汇总。仅记ticker、offset、Task/stream身份、版本和时钟，不记消息正文、模型输入和密钥。

必须区分：global-round等待、activation、Bus读取/解码、reconcile、plan准备、Runtime gate、SQLite BEGIN、SQL hold/commit、task claim、`_tasks_lock`等待、Bus cursor提交、状态写入与循环休眠。持锁者补进程/线程/operation的已有诊断信息即可，不另建锁协调表。

至少保留下列真实边界：原Bus时间、首次读到、Runtime登记commit返回、Bus cursor commit返回、claim、inputs_ready。原`admitted_at/Case.created_at`业务语义保留，新真实计时不能由它们反推。

必要测试：

1. immediate、buffered部分成员过滤、多组不同sweep owner、空计划、补充sweep、语义日切换；新compact回执与旧数组/旧Task兼容。
2. Runtime提交前失败游标不动；提交后cursor前崩溃原Task不变；cursor后dispatch前崩溃可恢复；连续前缀中间失败不跳过后缀。
3. 准入期间暂停/移除/模式切换，Control origin和trade_eligible保持原规则；重复消费不能新建Case、刷新首次时间或重新授予资格。
4. 两个dispatcher抢同Task、claim后shutdown、pool提交失败、Future瞬时完成、容量释放补位；不重复claim/付费调用，运行控制异常不折成业务失败。
5. source外置和大消息，pending计数不读取Native正文文件；空轮无业务UPDATE，状态变化及健康仍及时可见。
6. 在途Job不断到达的发布窗口，切换竞态必须退出或保留原工作，不能SIGKILL后假装已安全排空。

本轮未写实现测试去假装方案已落地；现有入站隔离、Native Stream与准入业务契约测试合计**31 passed**。临时库另验证了真实事务次数、重复消费幂等以及claim覆盖内存锁的机制。

## 8. 验收与收益边界

先发布A观察，再发布B/C观察；D作为正式发布流程约束同步落实。每批固定版本、保留连续队列，不同时改变模型或READY库存以混淆归因。

初始工程预算（待真实负载验证，非本轮已达结果）：空轮10 ticker必要查询P95≤100ms；一条普通Stream读取/解码≤100ms；新Runtime原子登记≤150ms；Bus cursor短提交≤50ms。正常Bus→真实登记返回P95目标≤1秒；所有内部锁/退避/领取等待仍计入，不能靠改计时点达标。

至少持续一个完整交易日并覆盖开盘突发；同时列出：

- 全部已发布待消费数量、消费/准入速率、最老pending年龄与cursor差；所有已登记任务的P50/P95/P99/max。
- 已形成Case、失败无Case、跳过、在途及缺时钟分别计数，不仅统计成功Case。
- 准入前与准入后inputs_ready分别计时，再核对原完整Runtime非SDK指标；既有Case子集inputs_ready P95约0.380秒不得因本轮优化明显回退。
- 发布窗口单列归因，但全队列仍保留，不用删除启动20分钟的方法粉饰。
- CPU/写入/内存不得因250ms轮询而持续上升；若连接/空写没有先减少，不启用更高频率。

微基准支持减少轮次中的固定读取成本，事务合并支持减少反复竞争次数；**现有证据不足以承诺每个慢Case恰好减少几秒或正式P95必然达标**。16.751秒Case及writer持有者的未知部分，必须由上述首轮轻量分段验证，不继续凭经验归因。

## 9. 证据目录与复核范围

`eval/runtime_ingress_audit_20261121/`包含固定队列元数据、Bus索引补齐、完整统计、发布journal与历史回执摘要、七文件哈希、90秒消费轮次、只读组件/作用域对照、选定慢窗口事务日志、临时库机制及31项现有契约回归。

关键文件：`metadata.json`、`enriched.json`、`cohort-statistics.json`、`cohort-rows.json`、`details.json`、`release-receipts.json`、`release-extra.json`、`version-and-extra-releases.json`、`cycles.json`、`cycle-summary.json`、`read-profile.json`、`scope-profile.json`、`logs.json`、`mechanism.json`、`gaps.json`、`current-contract-tests.xml`。原始消息正文、模型隐式思维链、凭证及私有Compose环境未导出。

最终判断：**分钟级样本由发布消费空窗主导，常态数秒延时由串行轮次内的重复工作和同步writer/claim交接放大；两者应分别修，但必须用同一端到端口径共同验收。**
