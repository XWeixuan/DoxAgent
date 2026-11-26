# Runtime 发布后延时回退：实施审查、生产证据与精益修复方案

2026-11-18，北京时间。范围是审查、只读生产观测、机制核验与方案设计。

**结论：存在真实回退，原修复也有未完成项。** 最新版本完整 Runtime 非 SDK P50/P95 为 **21.74s/69.11s**；去掉发布后前 20 分钟、另列 12:00 UTC 后连续窗口，仍为 **11.63s/24.32s**。原修复改善过盘后自然业务，但尚不能支撑当前负载下的端到端目标。证据支持“冷准备供给不足与物理成本波动＋Runtime 写入竞争＋多次阶段交接放大”的组合机制。尚无证据把回退归因于某个模型执行开发的主观行为。

## 1. 版本和对照边界

- 被审查提交为 `b99d0dbea20ad7e927f25b340ebe45176dfa72f9`，对应 changelog 原 3270 行。实际变动 19 文件，533 行新增、207 行删除。
- 当前生产 Worker/Scheduler 为 **`1b0494daeb9474c2f23de1e17100bd9ae8a50788`**，镜像 `sha256:b6bb06a3e717cd623c04f4c16622a88f7f778821d4802bd6a0e260ae699bb02d`。Worker 启动 11:39:33 UTC，Scheduler 11:40:35 UTC，均无 OOM、无自动重启。该版本继承 b99，又包含两轮存储源头减量改动。当前本地 HEAD 为 37baf88d1，另有用户／其他会话尚未提交的 changelog 修改。
- b99 首次验收为 07:06–09:07 北京时间、106 个完整 W3 Case，完整 Runtime P95 10.61s。当前观测发生在北京时间 19:40 后，流量、背景作业及运行版本均已变化。
- [后续存储发布回执](../../docs/storage-source-reduction-release-20261118.md)记录多角色重建、Scheduler 停机等待及 60 秒停止上限、数据库触发器切换及采集库索引安装。本轮不能将这些影响全部归到 b99，也不能因为后续改动存在就跳过原修复遗漏。

## 2. 原修复符合性审查

依据[原方案](runtime_nonsdk_root_mechanism_lean_repair_plan_20261118.md)逐项对照实际 diff 和当前代码。

| 要求 | 实施结果 | 需补充或修复 |
| --- | --- | --- |
| 从合格 waiter 选择冷启动队首，保持取消释放 | 已落地，新增队首资格回归 | 当前健康快照无 stranded owner，不能据此证明高峰从未缺货 |
| W3 专用 bootstrap，关闭三项插件市场能力，其他工作流隔离 | 已落地，Capsule profile 参与认领；control helper 同步关闭 | 缺上线前后 Native 写入的闭环验证；不能宣布所有 Native 写入已消除 |
| READY/恢复 max_subagents=0 | 已落地 | 无需重复开发 |
| v6 跳过 Policy/Reference 和 citation scope，旧契约继续读取 | 已落地，repository 与 D2 解析部分转 read_io | `_install_context` 的 compact/HTTP JSON、hash 等仍有同步工作；没有完成方案要求的集中离开共享 loop |
| 内容身份与 Case pin 分开复用 | 部分完成 | `research_pin_key` 和 v6 Provider cache key 仍纳入 Event/Policy 版本；v6 未设置 research_identity，未建立明确 D1/D2 内容复用身份。相同 D1/D2 随无关版本变化再次准备，再触发固定退避 |
| 双成功分支一笔短事务，混合结果先保留成功再传异常 | 已落地 | prepare_save 仍逐分支读取／编码；同 owner 的 stage/request 复用仍有限。恢复屏障继续保留 |
| Worker 成功保留 resident 时合并 timing/终态，跳过 CLEANING | 已落地 | 失败和资源释放路径保留原状态，不能再删 paid receipt |
| per-turn fast/default 避免仅 tier 不同的 Resume | 已落地传参 | 缺有效 per-turn tier 的独立生产证据、版本不支持时的明确行为测试。UI/rollout 未记录 tier，不能当作生效证明 |
| write_result 分段记录 submit、loop start、callback start/end、loop resume | **未完整落地** | 只增加一个包住整个 write_io 的 span 和开始时 queued_ms。callback 队列、实际 SQL、恢复仍未全程闭合。此前把整个 span 描述为结果交接会误归因 |
| Maintenance collect 有界候选 | 已落地 256 个裸候选分页 | 与建议 64/50ms 检查预算不完全一致，应按实测 writer hold 和吞吐调整 |
| reconcile schema_version 缓存、inventory 目录检查数量有界 | **未完成** | 每轮枚举 sqlite_master、每表 PRAGMA；inventory 仍 sorted(prefix.iterdir()) 后过滤，limit 只限制成功项，目录规模仍影响成本 |
| expire 准备移出 writer，最终删除保留安全保护或安全脱离 | b99 部分落地，后续存储改动进一步 detach | 不重复开发后续已完成部分，单独验证 race 和短写耗时 |
| GC 水位回填不 build COLD；空闲 WAL keeper | 已落地，后续改动继续保留 | 需要长期物理 I/O 收益验收，keeper 不代表其他进程/Native 连接成本消失 |
| 原身份 thread delete 幂等收敛 | 已加 not-loaded 后查询确认 | 需要晚到回包、RPC 超时原身份恢复用例 |
| 修复后供给时间线、4/6反事实比较、实际资源增量验证 | **未完成** | 上轮只得到库存快照与冷准备分位，缺连续空库存/后台构建者服务曲线和反事实，不能凭当前缺货直接扩池 |
| 分批小提交、针对新增风险回归、正式性能验收 | 未按原分批顺序实施，合并一个提交；150 项通过但仅新加一项队首测试、修改一项既有断言 | 已通过测试不覆盖全部新增约束，需补对新风险有区分力的回归和实际 canary |

原提交还改变了 `W3PreparedContext` 的内部可空字段，符合方案；不是发给模型的 Schema 变更。旧 owner 被替换时的测试改为期待 CancelledError，与现存 fencing 行为一致，仅凭此处不能认定削弱恢复保护。

另一个已证实遗漏是 `prepare_case` 发起后台研究材料准备后，仍抛 `w3_prepare_pending`，service 将 Effect 延后 5 秒。上轮已观测 5.11 秒等待，当前窗口又有 6 次 context.prepare 的 W3Error。它不属于模型耗时，不能继续保留为常规就绪交接。

## 3. 当前生产计时

元数据读取 2048 条最新 Case 标量，按精确 Case/Job key 读各阶段与 SDK 时钟；两容器回执分别约 25.5MB/8.8MB，均未触达 100MiB 预算。日志是当前容器的记录，旧容器被替换前的 owner 回执无法据此补齐。

| 组别 | W3 入组 | 完整 | Trade P50/P95 | Operations P50/P95 | 完整W3 P50/P95 | 完整Runtime P50/P95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 当前 Scheduler 稳定启动后，11:40:40–12:39:55 UTC | 119 | 116 | 14.49/39.22s | 2.48/23.65s | 17.14/50.93s | **21.74/69.11s** |
| 同队列中12:00 UTC以后连续子窗 | 57 | 54 | 5.74/19.63s | 2.18/6.12s | 8.74/21.31s | **11.63/24.32s** |

3 个在途 Case 保留未完成和 SDK 时钟缺失，不填零、不混入完整分位数。晚窗单列用于判断是否仅为重启集中恢复，正式全窗仍包含启动后慢例。并行 SDK 区间取并集，W1R3 辅助模型不抵扣 W3 内部耗时，本地工具计回，无 Provider Pause 记录。排队、退避、SQL、Native 初始化全部计入。

10:30 UTC 后共读取186个 W3 Case，其中6个 FAILED，均创建于11:38:01–11:38:58，错误是 Worker connection attempts failed；位于后续存储发布替换 Worker 的时段，早于正式新实例队列。本轮明确列出这6例，不将其计为119例内新增失败，也不因“发布期间”忽略恢复缺陷。

### 3.1 最慢 COHR 的端到端链

Case `case_b3587cf22d334b709cd03108a43a36fb`，非 SDK 总计 **82.56s**，完整 W3 **76.06s**。

- W3 前置内部6.49s。case_task span 21.16s包含模型等其他区间，不能把它整段加到内部时间。
- Trade首轮 owner 5.57s，journal.begin 合计4.42s，嵌套 gate 3.40s；之后第二轮 owner 7.45s，出现重复 stage/context 准备。两轮之间有6秒量级无模型等待。
- 两份 Worker READY 请求8.09s/8.82s。准备和业务写入共同延后实际 SDK 派发。
- Trade最终 owner **13.54s**，journal.begin 合计6.21s，gate4.52s，写入等待span5.89s；这些有嵌套，禁止相加。
- Operations前 owner **9.03s**，gate6.87s及内部重试1.79s；最终 owner2.34s。Operations 内部共 **26.07s**。

这些发生在模型前后，已足以排除“只是双分支统计口径膨胀”的解释。此例没有观察到本地研究工具开销。

### 3.2 12:00后仍有同类问题

INTC `case_02ca8ee0fc1c43ad92490dfd1582e11d` 总内部 **26.10s**，Trade19.81s，READY请求9.90s/11.82s，首轮owner3.56s。NBIS `case_c3bc6cf4179e4d2abed5b08a0699da0c` 总内部25.50s，两份READY请求9.14s/10.14s，最终Trade owner1.10s。后一例核心仍在准备侧，不能归咎于数秒结果消费。

## 4. 当前卡点和证据等级

### 4.1 已证实：真实供给缺口和冷准备波动

当前Worker启动后的270次已绑定领取，**185 MISS／85 HIT，缺货68.5%**。这是当前Worker全量绑定标量窗口，较119例窗口稍宽，不冒充同一分母。

185次MISS绑定到READY P50/P95/最大值 **7.77/18.89/35.26s**。273份operation的Boot P50/P95/最大值 **3.26/9.54/22.57s**，operation创建到Fork派发 **4.15/11.42/29.24s**，包含后台库存构建和绑定冷准备，不混作纯Native CPU。

上轮44次MISS P95约5.94s，本轮已明显变慢；提高命中率与降低缺货成本都需要做。实时Case继续直接准备。当前快照4 READY，只能说明当前已补齐，不能否定历史缺货。

### 4.2 已证实：Runtime writer竞争沿多个短阶段累计

慢例 gate/BEGIN/retry 已达秒级，包含 stage.begin、thread_prepare、共识提交、Effect保存和Operations前置。当前 writer是进程内 gate＋跨进程 SQLite；只优先一个写线程不能让其他进程让位，也不能消除一个Case多次进出writer的成本。

已抽取警告包含value.round_inputs、task.claim、repository.dispatch、w3.ack.commit。警告保留有1500条上限，频次有前段偏倚，不据此排名全窗writer来源。journal.reconcile_interrupted 的“hold”5.67s属于读事务，不能叫写锁持有5.67s。即使单次业务SQL很短，CPU调度和FULL提交也会拉长锁的墙钟。

**待补证的是具体跨进程持锁者。** 当前5轮低频线程栈看到Runtime后台维护读解压、大对象Pydantic、dispatch与claim争用；未在历史慢时点捕获唯一writer所有者，因此不写成已确定Transport Maintenance是主因。

### 4.3 已证实：结果写入与Future通知不能混算

472个结果写入span中：

| 区间 | P50 | P95 | 最大 |
| --- | ---: | ---: | ---: |
| 调用者submit到W3 loop开始，queued_ms | 0.36ms | **9.27ms** | 417.78ms |
| loop中整个write_io span墙钟 | 113.36ms | **747.26ms** | 4.43s |

后者包含writer排队、回调、重试、协程返回，不等于通知延时。COHR慢例完整span6秒量级但部分上下文中的span未带Case ID，因此不能用472个Case可归属样本的最大值宣称全窗最大只有4.43s。需要按Effect关联补证，保留原始span。

### 4.4 已证实：资源竞争还在，存在另行运行的压缩作业

12:41的15秒cgroup差分：Worker CPU6.79s、物理写163MB、读502MB；API CPU9.80s、写315MB、读330MB；Scheduler CPU15.61s。该差分无CPU quota节流。

另一个12秒全进程差分中，PID3258124 Python物理写324MiB、打开research.sqlite3；Worker主Python写109MiB；宿主PID3291825运行 **/tmp/dox-50g-compact-db.py**，打开acquisition.sqlite3及acquisition.50g-vacuum.sqlite3，物理写125MiB、读156MiB。这是另一个会话的实际在线压缩作业，本轮没有停止或修改。打开文件和进程级写量不能证明所有写都来自其中某个SQLite。

12:40快照I/O PSI full avg60约6.96%，CPU some avg60约30.15%；内存可用约13.8GiB，根盘约19.9GiB，内存保护NORMAL。可用内存充足仍可能有调度／文件页／I/O竞争。以上采样晚于最早11:41慢例，**不能反推该压缩作业在最早慢例时已经运行，更不能把它当唯一根因**。

## 5. 为什么前面修复没解决整体问题

原修复确实移除了无效插件工作和若干重复写，但没有解除全部准备交接，也没有证明高峰供给能力与资源成本。单份冷准备墙钟随竞争上升，库存被双任务突发消耗，更多Case进入冷路径，进一步争用CPU/I/O；与此同时Runtime仍多次短写、维护与ACK竞争同库writer，已成功结果也受影响。两条链叠加，导致准备、结果、Operations同时变慢。

后续多角色发布又带来集中恢复及背景存储工作。当前版本包含原修复，性能变化仍需结合版本和负载比较。现有证据不足以认定后续单个存储代码改动造成全部回退，也不足以认定b99的所有修改已经满足原方案。

## 6. 执行级修复方案

按下面顺序推进，每批保留生产版本和前后同口径窗口，不再把全部修改一次合并后只做健康验收。

### A. 先消除精确材料准备的固定等待

修改 `w3.py:prepare_case` 和 `service.py` 的特殊错误分支，复用现有_asset_preparations Future。把该Future作为现有Effect等待对象，完成后立即续接当前Effect；成功后重新读取原research_pin_key，完成状态保存及fencing检查。先登记等待者再安装callback，兼容已完成Future；shutdown后不再派发，重启沿用原Effect，跨进程通过原持久身份恢复。

现有 w3_pending_external 已支持Future唤醒，优先复用其等待与续接机制，区分“阶段结果可直接消费”和“材料完成后重进业务步骤”。不能直接调用其phase_result把材料当交易结果。仅无本地Future的恢复依赖既有持久恢复tick，不为正常完成保留5秒退避。

### B. 完成v6内容身份和共享loop减负

现有Case pin key继续作为冻结引用的权威索引，读取旧记录兼容。新增v6 discriminator的组件身份，仅由ticker、固定D1/D2版本／内容hash和编译契约组成。Case pin写回时使用该Case原始版本，禁止复用另一个Case的Event/Policy pin。Provider缓存复用D1/D2组件值，组装Case版本外壳；现有安装API继续幂等返回组件。

HTTP正文JSON、hash、Pydantic/compact离开共享loop，用现有read_io处理，网络发送仍在原async client。相同组件正在准备时复用已有Future，按完成/失败释放，避免留下无限增长的映射。不同Case的Token、私有Thread和WATCH/PIN仍独立。

### C. 解决短写累计与维护占用，先定位持锁者

在现有诊断trace补事务acquired→commit-return、提交到callback开始→callback返回→loop恢复；记录库类别、PID、operation、Case/Effect身份。保持低频慢事务采样，避免逐SQL记录正文。Worker SQLite、Runtime SQLite分别计量。完整区间用非重叠关键链对齐，不累加嵌套span。

根据实测持锁者修改。对同一owner内重复stage/request只读一次，编码在事务外完成。只合并属于同一fence和原子职责的无意义重复写；保留paid receipt、分支结果不可变、共识、交易提交、ACK intent与租约检查。ACK和无变化投影按既有后台维护有界页处理，缩小一次占用并在页间释放writer；成功Job持久结果优先可消费，ACK退出继续可靠补偿。

Transport schema描述按schema_version复用，inventory按实际检查目录数/时间预算推进cursor，目录枚举也有界；无变化不写。不能仅对“成功64项”设limit。原批量collect已修部分不重写。对后续存储detach的引用保护再验竞态，不为省锁删除保护。

### D. 降低单份准备成本，再决定4/6

补齐operation创建、admission、进程启动、SDK_READY、Fork发送／开始／完成／父进程接收、slot READY非重叠时钟；仅慢构建采子树CPU、缺页和I/O，保留原deadline/operation身份。

从现有slot/operation记录重建消耗与补货开始/完成，按秒记录eligible库存及后台构建者空闲原因。先观察B/C完成和背景压缩退出后的同负载结果，确认原插件关闭在实际物理client有效，无多余Resume。实测低负载冷准备应恢复数秒量级。

再以实际到达及后台unbound构建服务时间回放4/6，绑定冷构建不能当作后台补货服务时间。若增加2份库存能消掉主要长尾且内存/IO余量充足，做一次有限6库存试验并保留回退；实时MISS直接准备。有限第二构建流水线仅在单构建者吞吐确证不足且新增CPU/IO成本可接受后再试，不能同时扩大两项。

### E. 补上发布恢复和资源验收

6个连接失败Case需要逐例检查原Job查询及原Effect恢复。Worker切换时，网络断开不能推断原Job不存在，不消耗普通业务失败预算，保留原幂等ID查询。只有确定404才能考虑原规则新派发。先让Worker恢复可查询再恢复新派发，已成功结果继续消费，采用已有发布协调入口。

后台压缩／转换按现有资源协调器可观察地运行，记录开始结束、CPU/I/O和实时指标；不在本轮擅自终止其他会话任务。若负载影响已证实，优先可暂停的后台分页／yield，W3实时并发保持。

## 7. 必要回归与验收

- A：Future已完成/刚好完成、准备失败、租约更换、停机和重启、原pin不变，无重复模型调用。
- B：相同D1/D2不同Event/Policy版本只准备一份组件，各Case pin仍正确；旧key读取兼容；并发准备不会阻塞成功结果；旧v4/v5仍加载Policy/Reference。
- C：无变化大库、万条目录但仅少量有效项、并发新引用、quarantine复活、断点恢复；结果不可变和部分成功可恢复。精确验证writer外编码及实际检查数上界。
- D：真实turn验证fast/default、同profile不Resume、不同profile合法Resume，权限与工具不减；主动注入冷缺货和突发，不能只验满库存快照。
- E：发布断连、请求已受理但回包丢失、查询5xx、确定404，分别验证不重发与正常恢复。

最终以同版本至少100个连续完整实时Case＋真实开盘高峰验收，报告所有未完成/失败/缺时钟；完整Runtime非SDK P95≤10s，Trade/Operations单列，冷启动、锁等待、内部退避全部计入。当前119例窗口已明确失败；等待更多快Case不能改写本窗口结论。

工程目标为状态/通知毫秒级、短事务通常几十毫秒、冷准备常态数秒。指标达标同时要求没有显著物理写入/Swap/资源增长回退。writer持锁P95先以20ms为目标，偏离时定位持有者，不能扩大timeout。达到后再评估更低P50。

## 8. 证据与未闭合部分

证据目录 `eval/runtime_post_release_regression_audit_20261118/` 包含cohort、logs、inventory、acceptance与steady子窗、时钟汇总、非阻塞线程栈、cgroup和进程I/O差分。采样只读、nice降低优先级，没有修改生产配置、数据库业务状态或执行模型补跑。

尚未精确闭合历史慢例每秒的writer持有进程、Python/Native调度等待、每轮有效service tier。跨时间资源差分仅用于确认当前竞争者。上述未知项作为C/D的短期验证任务，不作为增加新基础设施的理由。

本地针对性复核执行 `test_w3_startup_admission.py`、`test_w3_v6.py`、`test_w3_nonsdk_optimization.py`，共41项通过，另有一条第三方DeprecationWarning。该结果验证现有回归行为，不证明表中未实现的约束已满足。本轮没有代码修改或发布，工作区原有changelog修改保留。
