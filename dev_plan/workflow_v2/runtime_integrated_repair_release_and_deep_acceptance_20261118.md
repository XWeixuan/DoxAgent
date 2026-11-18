# Runtime 全局精益修复：实施、发布、真实验收与剩余根因

2026-11-18。依据 `runtime_global_integrated_lean_repair_plan_20261118.md` 执行。代码提交为 **7db63aacde026c286a108400d6f905900c1c121c**，已推送 origin/main 并正式发布。

**最终连续入组105例、105例完整完成，完整 Runtime 非 SDK P50/P95 为10.12/20.69秒，验收未通过。** 本轮已消除材料完成后固定延期、v6 公共组件重复准备等明确缺陷，但仍有初始 owner 事务/调度、冷准备及背景资源干扰。排除启动后20分钟的42例仍为8.55/15.57秒，不能把问题归结为发布启动集中恢复。以下保留失败窗口及真实关键链，不宣布性能问题已解决。

## 1. 本轮实际落地

| 原方案项目 | 实际实现 | 验证与边界 |
| --- | --- | --- |
| 材料 Future 立即续接 | `w3_prepare_pending` 携带原准备 Future，复用 `_phase_waiters`、done callback 和原 Effect；完成后重新执行准备步骤 | 完成前后竞态、已有完成 Future、错误与原身份回归；不把材料值当模型结果 |
| 发布期基础设施故障分类 | 材料/上下文网络失败、408/429/5xx 原身份恢复，不消耗业务 attempt | 保留 UNKNOWN 与原 Job 身份；取消、租约与业务 Schema 错误不被吞掉 |
| v6 D1/D2 内容身份复用 | ticker、D1/D2 run/SHA、renderer、契约构成组件身份；共享组件与各 Case pin 外壳分离 | 同内容不同 Event/Policy 版本复用，各 Case 冻结版本仍独立；旧契约读取保留 |
| 元数据优先 | 新增内部 publication metadata 描述，W3 命中检查前不再必然解压正文 | 完整 PublishedDocument 原调用契约保持；缺失正文不能被伪造成有效空内容 |
| W3 loop 减负 | 正文读取校验、任务/schema JSON 和 HTTP JSON 编码利用现有 read_io；任务文本一次构造复用 | 不新增持久缓存服务；异步网络调用仍在原 loop |
| 成功共识提交减法 | 当前 owner 一次读取 main/A/B stage 标量，复用已得结果 | paid receipt、分支不可变性、fencing、交易提交原子边界保留 |
| Transport 无效工作减法 | schema_version 缓存表结构；目录实际检查量有界；已登记相同资产不重复全文 verify；无变化 cursor 不写 | 新对象仍校验；晚到小 key 后续轮次能看到；文件引用退出保护保留 |
| Native 引用触发器护栏 | UPDATE 身份和 JSON 均无变化时跳过差量扫描，并升级生产触发器 | 保留业务写与引用登记同事务 |
| 结果/启动时钟闭合 | submit/loop/callback/owner 时钟及 admission、Fork checkpoint/send、子进程接收/完成标记 | 复用既有 trace，不新增业务状态机 |
| per-turn tier 实证 | 实际 SDK 回执记录 turn 参数 | 自然 Case 确认 Luna max，Trade fast、Operations default；抽查同 resident 没有额外 thread_resume |

原有 PriorityGate、插件市场关闭、max_subagents 对齐、GC 水位不复建 COLD、Worker WAL keeper 等有效修复继续使用。本轮没有重做它们。库存仍为当前实际 Luna profile 的 4 份，实时 MISS 直接准备。

本轮相关回归分别运行 113 项、92 项；末轮重点组合 48 项通过，套件存在重叠，不将三者相加冒充独立测试数。另完成编译与 diff 检查。修正一个旧 Operations 测试的故障注入点：现行原子提交已不经过旧 `_sync_route_case`，改在提交后的 ACK 调度处注入，继续验证重试不重复模型与业务提交。

## 2. 发布和身份核验

按 `scripts/ops/storage_release.py` 正式构建及组件发布流程执行：

- 源码：7db63aacde026c286a108400d6f905900c1c121c。
- 镜像：`sha256:d55b4d6e2f0946dbf8e3946dfcf036f81bb53c4e6d4b580d5614172c4d401a87`。
- 构建源码摘要：`7da582b1fc759546d6a888f690997c68934380bba45d13ba933f2f4a6fe42e94`。
- Worker 于 13:53:11 UTC 启动，Transport Maintenance 于 13:53:35 启动，Scheduler 于 13:53:59 启动。发布回执完成时间 13:54:15 UTC，即北京时间 21:54。
- 三个变更组件使用上述镜像，核对 revision、真实文件摘要、Worker health 和服务状态。其他无相关源码改动的角色沿用先前镜像；不是所有容器都被重启。
- 最终对 Scheduler、Worker、Transport 各两份关键源码复核，六份均与7db63aac提交 blob 的 SHA256一致。使用git提交字节比较，避免Windows工作区CRLF与正式git archive字节差异造成误判。
- Native 二进制版本保持正式构建锁定版本；没有因为发布重新选择 Native 版本。

发布前另一个会话正在 Research 库压缩，等待其完成文件切换后才进入本轮发布。13:50:09 请求旧 Scheduler 退出；旧进程关闭仍继续 D3 维护，13:51:47 在确认没有实时 W3 运行后终止旧 Scheduler。原 Worker 上已受理的 5 个 D3 Job 全部于 13:53:02 前完成，再替换 Worker，没有中断在途 W3 模型调用。

生产触发器升级回执及正式发布回执保存在 `/var/lib/doxagent/releases/runtime-integrated-20261118/`。本地其他会话未提交的 changelog 内容保持原样，没有混入本次代码提交。

## 3. 真实验收口径与首批结果

从新 Scheduler 启动后新准入的 REALTIME Case 连续入组，完整 Runtime 从持久准入到 Operations 最终 owner 返回。按真实模型区间并集扣除，A/B 不相加；W1R3 辅助抽取不抵扣 W3 内部时间，本地工具时间计回。冷准备、排队、锁、内部退避及未知间隙均计入。

Case 状态 COMPLETED 可能先于 Operations 结束，不能仅按状态字段判定完整。原始资料、未完成、缺时钟和失败均保留；下面每行分位数只针对当时完整的样本。

| 采样 UTC | 入组 / 完整 | Trade P50/P95 | Operations P50/P95 | 完整 W3 P50/P95 | 完整 Runtime P50/P95 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 13:58:28 | 14 / 11 | 7.39/9.84s | 2.81/3.90s | 9.13/11.44s | 11.94/16.46s |
| 14:13:21 | 62 / 62 | 5.64/15.37s | 2.73/4.52s | 8.43/17.59s | 12.01/23.09s |
| 14:18:36 | 68 / 65 | 5.55/15.37s | 2.73/4.52s | 8.06/17.59s | 11.70/23.09s |
| **14:35:58，最终冻结** | **105 / 105** | **5.03/13.46s** | **2.46/4.20s** | **7.20/16.93s** | **10.12/20.69s** |

14:18:36 的3例当时在途，最终均完成；没有因慢而删掉。最终准入边界固定为13:53:59.565090–14:35:00 UTC，105例均COMPLETED且Trade/Operations提交、最终owner和模型时钟完整，未截断候选窗口。最大完整Runtime内部29.82秒。A/B共210个独立物理Thread，未见跨Case复用，Operations均沿用该Case选中Thread。

开盘14:30–14:35的8例完整Runtime P50/P95为9.11/14.12秒；仅用于开盘初段观察，8例不足以证明持续高峰分位。排除启动后20分钟仍有42例，P50/P95为8.55/15.57秒。二者单列，均不替代包含冷启动的主要验收，也不能将启动窗口删除后宣称通过。

## 4. 关键链闭合：最慢 MU 的 29.82 秒在哪里

Case：`case_d09c3b6908904cee91e6c0d736adf13c`，14:05:41.824786 UTC 准入，完整 Runtime 非 SDK **29.82 秒**，其中 W1/W2 前置内部 **2.88 秒**、完整 W3 **26.94 秒**。

Trade 自 route Effect 创建到最终 owner 返回的非 SDK **23.92 秒**，按互不重叠时间段拆分：

| 区间 | 内部耗时 | 已知细节 |
| --- | ---: | --- |
| Effect 创建→首 owner 启动 | 约 2.97s | 14:05:51.223→54.191；属于内部领取/调度等待 |
| 首 owner 执行 | 约 6.41s | 38 次 journal.begin（含读及重试），累计 begin 5.16s，进程内 gate 3.18s，内部退避 0.84s；这些数字嵌套，不能与 6.41 相加 |
| 首 owner 返回→第一个模型派发 | 约 12.94s | 其中两个 READY 请求各约 9.5s，剩余材料/绑定/Job 派发等约 3.4s；A/B 等待重叠，不相加 |
| 最后模型完成→最终 owner 启动 | 约 0.42s | 包含接收、保存与续接，不是外部推理 |
| 最终 owner | 约 1.18s | 含读取、共识保存、业务提交、Effect 与控制收尾 |

A/B 模型重叠区间单独扣除。Operations 内部约 3.36 秒，Trade 与 Operations 边界有约 0.34 秒重叠，因此不能把两阶段指标机械相加当完整 W3。

### 4.1 READY 内部再拆

两条分支均 POOL_MISS。slot bound→READY 分别约 9.20、9.22 秒：

| 分支 slot 前缀 | operation 创建→Fork dispatched | Boot wall / Python CPU | 子进程 Native Fork | Fork dispatched→operation 完成 |
| --- | ---: | ---: | ---: | ---: |
| c51aab08 | 5.77s | 5.17 / 1.66s | 2.04s | 3.11s |
| d75ba653 | 7.20s | 5.56 / 1.66s | 1.49s | 2.61s |

Native Fork 来自 `capsule_prepare` 的真实 `sdk.thread_fork` span。最后一列包含该 Fork 及回包/登记，不能再与 Native Fork 相加。分派前也包含 Boot，不能把两列相加。此证据证明冷准备仍是实际长尾，不能用“SDK 模型慢”解释；但 Boot wall 减 Python CPU 的差尚未完全分配到 Native 子进程 CPU、调度、缺页和控制通信，不能统称 I/O 等待。

父进程 `prepare` trace 进一步证实：c51 分支 acquire 5.71s，其中 spawn 5.67s，Fork checkpoint 73ms、prepare_reply 2.76s；d75 acquire 7.17s，其中 spawn 6.07s，checkpoint 65ms、reply 2.26s。子进程完成控制调用至父进程收到回包分别还约0.71、0.76秒，包含 fork receipt 文件保存、管道/执行器及调度，尚未唯一归因。小 checkpoint 不是此例数秒等待的主项。

d75 原为后台构建，admission记录等待1.067秒后被升级放行；其中约0.85秒发生在 Case 绑定之前，绑定后约0.22秒。不能把整个后台等待再次加到实时 Case 关键链。

### 4.2 首 owner 仍是独立问题

`trade()` 仍串行执行 main stage、快照冻结、A/B stage 和各自线程绑定；`_thread()` 重复读取 stage，两个分支各进入事务/读取链。本轮已减少的是结果消费阶段的重复 stage 读取，没有把初始注册链同步压缩完。

`journal.gate` 是 Scheduler 进程内数据库级 mutex 的等待，不等于 SQLite 跨进程 BEGIN 等待。该例 gate 总计 3.18 秒，而已记录 SQLite begin 合计约 0.19 秒；必须分别处理。高频事件做了采样，totals 完整但逐笔细节不完整，无法仅靠该 trace 指认每一次竞争者。

## 5. READY：库存恢复有效，突发时仍有供给缺口

截至 14:15 的标量重建：128 个本次 Worker 新 slot，其中 124 已绑定使用、4 READY；单一实际 Luna scope，没有为闲置 Sol profile 保留额外库存。124 次绑定中 **85 HIT / 39 MISS，MISS 31.5%**。

| 构建类型 | 数量 | P50/P95/最大 |
| --- | ---: | ---: |
| MISS bound→READY | 39 | 3.97/9.89/10.54s |
| 后台/预建 created→READY，含当前库存 | 89 | 2.74/4.65/10.58s |
| 全部 Boot wall | 128 | 1.78/5.17/7.22s |
| 全部 Boot Python CPU | 128 | 1.46/1.68/1.73s |

14:06 一分钟发生 12 次绑定，11 MISS、1 HIT。单张库存快照为 4 不能代表高峰命中率；本轮也不能因 14:15 库存恢复而否认刚刚的缺货。与上一窗口 68.5% MISS 相比，本批数字较低，但负载与窗口不同，不将全部差值归功于代码。

用该次库存记录能够对应上的完整 Case 分组：两支均 HIT 的41例，完整 Runtime P50/P95 **9.64/16.61s**；至少一支 MISS 的21例为 **16.46/25.02s**。另11例当时没有对应新库存记录，单列 unknown，不冒充 HIT。分组有负载选择差异，不能视为随机对照，但足以否定“全部长尾仅因冷启动，命中就一定达标”。

最终库存补齐后，105例均可关联：210次绑定，154 HIT、56 MISS，MISS **26.7%**。两支均HIT的75例，完整Runtime P50/P95 **8.92/16.61s**；至少一支MISS的30例为 **14.85/25.02s**。最终214份新slot中，56份MISS bound→READY P50/P95 **3.81/9.80s**；158份后台/预建 created→READY **2.61/4.62s**；Boot wall **1.71/4.16s**、Python CPU **1.44/1.66s**。该完整分组进一步支持同时修初始事务与冷准备，不能只扩库存。

进一步扩大库存前，要用真实 READY 服务时间和突发到达序列比较 4/6。后台 P50 已约 2.7 秒，突发可在短时间消耗多份；增加两份只能覆盖一部分突发，不会消除供给受扰或首 owner 写锁等待。保持 MISS 直派，不让真实 Case 等待后台补货。

## 6. 新捕获的资源与跨进程 writer 证据

### 6.1 Read 库意外处于 DELETE 模式

14:01 检测到 Runtime、Research、Worker 均 WAL，而实际 Read serving 库 `/data/read/serving-v4-20261018/v2.sqlite3` 为 DELETE。同期 12 秒进程差分：Projector 写约 **153.6 MiB**、Read Maintenance 写约 **77.1 MiB**，Maintenance 打开了 `v2.sqlite3-journal`。

14:04:06.105839 UTC 用短连接恢复该 Read 库 WAL；没有改业务行、执行 VACUUM 或重启服务。回执见 `read-wal-restore.json`。这是已执行的运行配置修正，不冒充已提交的永久启动修复。

14:05 的 15 秒 cgroup 窗口中，Maintenance 写约 0.14 MiB，但 Projector 仍写约 192.6 MiB。14:18 的另一 10 秒窗口，Projector 写约 32.1 MiB、Worker 主 Python 约 102.9 MiB。窗口业务不同，不能据此算 WAL 的因果改善百分比；它明确说明恢复 WAL **没有消除全部写入，更没有自动兑现延时目标**。

代码缺口：`ReadStore.migrate()` 对已有 version=4 在 `upgrade()` 后提前 return，绕过后面的 WAL 设置；由 `00ee814cd9`（10 月18日）引入。它能解释“模式漂移后没有被启动修正”，不能证明是谁、何时将生产库改为 DELETE。现有压缩/恢复脚本与 serving 文件切换仍应核验，不能把本次现象直接归罪于两轮存储减量。

### 6.2 Runtime writer 不只有 Scheduler/Transport

14:06:00–14:06:18，内核锁约 100ms 间隔采样，最多三份非阻塞 Python 栈：

- Runtime writer：Scheduler 38 次、Projector 8 次、Executor 1 次。
- Read writer：Projector 58 次；Worker 库 writer 24 次。

这些是采样命中次数，不能当作精确持锁时间比例。捕获 Projector 持 Runtime writer 时，调用链为 `ProjectionWorker._tick → acknowledge_exceptions → source_lifecycle.acknowledge → migrate`。Scheduler 同时存在 stage 注册、Case 保存和 gate 等待；Executor 有健康状态写入。

ACK 在 `BEGIN IMMEDIATE` 内执行七条 `CREATE ... IF NOT EXISTS`，再读全部现存 repair pins 作集合差分。Runtime 当前 Read consumer 有 **17,207** 个 pin。实现由 `f7ba4f874a`（10 月18日）引入，属于积累规模放大的旧路径，不是新差量触发器的直接证据。

额外只读测量“新连接＋读 pins＋集合构造”，5 次为 **21–72ms**，CPU 约 21–57ms。它证明有可移出 writer 的实际工作，但不足以单独解释数秒长尾。Projector 已有 ACK signature/60秒节流；不能错误声称“完全无变化仍每 tick 必定 ACK”。checkpoint 持续前进时仍会触发 ACK。保留全部 gap/pin 的保护语义，不能用丢弃历史异常换性能。

### 6.3 资源解释的边界

8 核服务器，14:05 的 15 秒各容器 CPU 累计约 107.5 核秒，相当于约 7.16 核持续工作；Worker 35.3、Scheduler 15.7、Site Access 13.5 核秒。此时没有容器 CPU throttling 的证据，存在全机 CPU 竞争的可能，但这个窗口早于最慢 MU 的 Boot，不能精确分配那几秒。

14:15 可用内存约 13.4 GiB、磁盘剩余约 58.7 GiB，未见本轮容器 OOM/重启；Swap 已使用约 2 GiB，几乎无空余。静态 Swap 使用量不等于本窗口持续换页，必须用 pswpin/pswpout 增量验证。资源保护照常运行，没有降低 W3 实时并发。

### 6.4 开盘抓到的非 W3 无界指令文件搜索

14:30:05–14:30:26，有限21次内核采样发现Worker内 `find .. -name AGENTS.md -print` 持续运行，16次处于D状态；CPU约2.96秒。其父链为bash→Native Codex→私有Python Capsule。核对唯一Capsule标记：`DOXAGENT_CAPSULE_W3=0`，cwd为 `runtime-maintain-c51bc966fa6e941ae28dba9a-o3-maintain`，对应原Job `61eae9c6d4f1f4bb43276a9bad68bfccc0df355f6e23b0a44ff3a70d555bc4c9`，14:27:39受理。这是O3 maintain业务工具，不是本轮诊断命令或W3 READY构建。

**AGENTS.md事实上已在该任务cwd存在**。父目录搜索会遍历共享workspaces，属于应消除的无效工作。此窗口系统IO PSI some/full avg10约12.70%/7.68%，存在真实资源竞争；不能将全部系统I/O或W3历史延时都分配给这一条find。未中断该已受理业务Job。

同一窗口观察到417页swap-in、0页swap-out（约1.63MiB读回）；不是“新一轮大量换出”的证据。内核schedstats未开启，不能把采样中的零runqueue计数当作不存在CPU调度等待。本轮没有为诊断改变该内核设置。

最终14:35资源复核：可用内存约13.43GiB、磁盘剩余约57.21GiB；Worker dispatcher RUNNING、processes_recovered=true、settlements_pending=0、无cleanup quarantine，实时无隐藏队列。三份已发布组件未OOM、restart=0；Read仍为WAL。

### 6.5 Traceback 的实际含义

Scheduler日志另捕获75次 `sqlite3.OperationalError: database is locked`，栈全部来自 `w3_time_awareness.time_context` 的timeout=0可选时间原点登记：68次gate未取得、7次SQLite BEGIN未取得。它们被明确捕获并返回None，不是75个失败Case，也不是75次长超时。Worker未捕获对应异常终结。这个诊断日志噪声应与本轮105例零终态失败分开，不能把“有Traceback”直接当业务失败。

## 7. 为什么本轮减法没有让端到端直接达标

1. **删除固定延期不能消除另一条写锁与冷启动链。** 材料 Future 已立即续接，真实 Case 中没有继续等待固定5秒的证据；但首 owner 数秒 gate、READY MISS 和派发前工作仍在。
2. **本轮公共内容复用主要减少重复工作，不是免除每 Case 两个物理子线程的启动。** 双任务突发下，4份库存在补货完成前仍可能耗尽，随后进入真实冷准备。
3. **结果尾部改善不能替代前置链改善。** 62例窗口的 Trade 最终 owner P50/P95 约1.02/2.02秒；Operations 最终 owner约0.64/1.33秒。它们仍可优化，但已不是最慢例唯一或最大开销。
4. **Runtime 共享 writer 的竞争面比单个维护组件更广。** ACK、Case/Effect、stage、控制状态都可能参与；SQL begin、进程内 gate、短写 owner 队列须分开测量和缩减。
5. **Read 库模式漂移是额外放大因素。** 运行修正有价值，但后续样本仍有长尾，不足以成为全部问题的单因解释。

单独对两份大 Case 进行读取/校验与编码探测：读取约30–72ms、编码约19–62ms。重复全文读取应减少，但不能以1–2MB输入大小就推断一次读取需要数秒，也不据此设计新的复杂薄 Case 层。

## 8. 剩余修改方向：按关键链收益排序

以下为本轮验收后新增证据支持的下一批设计，未将其写成已经上线的能力。

### P0：缩短初始 owner 注册链

在现有 `W3ThreadRegistry` 中为双任务提供一次原子注册入口，预计算不变 profile/compatibility，一次取得 main/A/B stage 和已有绑定；缺失项在同一短事务内创建，已有项复用。将当前 main、A、B 分别 begin、随后 create_case、重复 stage 读取收敛；已有 Thread 身份不改、不重新生成。

快照需要读取/计算的内容在 writer 外准备，事务内只核对原 fence、原 pin、已存在的冻结值及缺失身份。禁止将正文读取、history 扫描、哈希或 Worker RPC 拼入合并事务。重启、已有单分支结果、部分绑定、W1R3并行更新都必须回归。预期节省串行事务/重试和重复标量读取，秒级收益取决于实际 gate，不能承诺直接减掉该例全部6.41秒。

### P0：先减少冷准备成本，再决定库存

用已补齐的 admission/boot/command/Fork/回包时钟，在下一次慢 Boot 同窗采子树 CPU、schedstat runqueue、缺页与 I/O 增量，补足当前“wall明显大于Python CPU”的区间。确认实际 Native 配置、插件关闭、MCP初始化和线程恢复成本；必要能力保留。没有证据前不再增加新的启动 Gate。

把 operation 完成到 slot 发布、slot READY 到 Job SDK 派发的约1–3秒也作为独立减法目标。相同 owner 已持有的正确准备结果直接传递，保留必需绑定与恢复屏障，取消重复读取和无变化状态写入。之后按真实到达序列重放4/6，单变量验证命中、构建吞吐、PSS和CPU；不默认扩库存能治本。

### P0：针对实际共享 writer 持有者减法

保留数据库级短写和原子提交。围绕慢 gate 的实际持有者继续核对 callback，而不是再扩大 timeout/retry。优先初始注册批处理、无变化控制状态短路、ACK必要数据外移和成功结果链已有 stage/request 复用。避免为了统一 writer 新增跨进程队列或服务。

ACK 的七条 DDL 应在现有 source migration/startup 完成；正常 ACK 不重新做完整迁移。repair 集合准备放在 writer 外，事务内按同一 consumer 可靠核对后提交 checkpoint/pin 差量，保留崩溃和多 owner 防护。不能仅用进程内旧集合越过竞争核验；若安全重核仍需有界读，应保留它，先删确定无效工作。

### P1：补齐 Read WAL 与维护遗留边界

`ReadStore.migrate` 的现有 v4 分支同样保证 WAL；正式 serving 切换/恢复核验模式，失败要显式报告，不在每条业务请求设置 PRAGMA。用既有版本库从 DELETE 启动的回归验证。

`source_lifecycle.expire` 目前仍在准备前和 writer 内各读取全局 exception pin 集合。改为只查本页 seq 的 pin 身份，事务内重核本页，不扫描全部历史 pins；新 pin 竞争时继续保守跳过没有准备异常包的候选。文件最终 detach 的原子保护保持，但路径遍历/内容编译不应扩大 writer 持有范围。本轮没有把这些剩余点伪称已彻底完成。

### P1：结果消费继续做小范围收敛

保留分支 paid receipt、代表结果冻结与最终交易提交的必要边界。缩减同owner内重复 stage/record/full Case 解码、无变化投影和 Effect/control 的重复落库；跨 Future 恢复仍读取权威状态，防止覆盖并行W1R3。Operations fast→default 的实际 per-turn 参数已验证，不能用删除必要阶段配置切换换性能。

### P1：消除后台工具的实际无界工作

针对已证实O3指令文件搜索，使用已存在的当前run `AGENTS.md`/manifest确定路径，不再向共享父目录寻找。先核对任务启动指令及该工具调用的实际触发原因，再改最小路径；必要业务资料通过原有明确路径读取。不限制实时W3，不为此新增全局队列、守护进程或通用文件系统扫描服务。观察原Job后续正常终结，避免因修诊断而重发模型。

## 8.1 原方案尚未闭合的边界

| 项目 | 本轮结论 |
| --- | --- |
| Future固定5秒等待、v6内容身份、metadata及inventory重复verify | 已实施、回归并发布；自然调用链确认没有继续依赖固定材料退避 |
| 冷准备完整跨进程时钟 | 父/子Fork、checkpoint与Boot已闭合到上述实际数值；Boot内部Native子树CPU、调度和回包约0.7秒仍待精确分配 |
| 共享writer归因 | 已捕到Scheduler、Projector ACK和Executor；尚未唯一逐笔闭合最慢Case全部gate的持有callback |
| 正常初始stage/Thread事务减法 | 尚未批注册，实际数秒问题已列为下一批P0，不能称已完成全部生命周期减法 |
| Transport expire本页pins重核、ACK热路径DDL | 尚未精简；原方案的维护优化不能据inventory完成便整体打勾 |
| 库存4/6与双后台构建者试验 | 未扩池；完整HIT组也超标，先解决已证实工作与冷构建成本，再按原方案条件做单变量试验 |
| 原六个发布期历史FAILED Case | 未擅自重置/重跑；仍须逐例核对原Job、交易终态和历史恢复资格，与本轮新Case验收分开 |
| 全部关键链P95≤10秒 | 未通过，最终数据见第3节；下一轮不能只验子步骤 |

## 9. 后续验收要求

每一批只改变一个可辨认的关键链因素，保持连续入组名单，至少100完整Case、真实开盘高峰，失败/在途/缺时钟单列。完整 Runtime 非 SDK **P95≤10秒**，P50及Trade/Operations单列。观察冷准备、writer owner队列、gate/BEGIN/hold/commit、CPU/IO PSI、Swap增量、PSS和物理写入。

不以更改口径、移除冷启动 Case、降低实时并发、放宽超时或掩盖内部退避达标。若仍失败，交付本次完整失败名单和已闭合关键链，不能只留最好看的安静窗口。

## 10. 证据索引

本轮证据目录：`eval/runtime_integrated_release_20261118/`。含连续 cohort/logs 原始采集及压缩快照、各次 acceptance、inventory/supply、慢 Case trace、turn-parameters、writer-owners、process/cgroup I/O、Read模式修正、ACK只读成本和Case编码探测。采集使用只读连接、索引标量、低优先级短窗口；没有生产全库扫描或重放付费模型。

启动 trace 首次扫描10000项达到诊断上限，未将其空结果当无记录；第二页有界扫描完成目录剩余项并取得上述两个目标子线程 trace。逐条 journal 事件有采样和摘要截断，关键链按绝对时钟，totals 不当作互斥时间直接相加。
