# DoxAgent 根盘复满问题发现

2026-11-23，北京时间。对象为新加坡生产服务器根分区 `/dev/vda3`，总容量约 373.40 GiB。以下的 GiB 采用 2³⁰ 字节，GB 采用 10⁹ 字节。目录采用 `du` 的分配块，SQLite 表采用 `dbstat` 的分配页，JSON 内容量与这些物理量分别核算。

## 1. 紧急恢复结果

18:38 接手时，根盘可用 **2,123,247,616 B（2.12 GB）**。清理后 `df -B1 /` 实测可用 **15,906,271,232 B（15.91 GB、14.81 GiB）**，净增 **13,783,023,616 B（13.78 GB、12.84 GiB）**，超过本轮目标。

| 动作 | 核实结果 | 业务边界 |
| --- | ---: | --- |
| Acquisition 缩库 | 旧文件分配 14,075,170,816 B，新文件 2,266,492,928 B，差 **11,808,677,888 B** | 复用原数据库内空页，核对 schema、关键表计数和 `quick_check`。恢复 WAL，确认 Collector 健康、收到新的 `ACCEPTED`、PENDING 为 0 后退出旧库。停 Collector 和 Bus 至重启约 4 分半，未核算停顿窗口内各源实时覆盖。 |
| Worker 已退役空输入目录 | **257,820 个**，**1,056,251,904 B** | 复用家族锁、退休标记与活跃 Job 边界，只对空目录执行 `rmdir`。本轮再次查找符合原严格条件的 D3 发布副本，新增命中 0。 |
| Event Library 历史分支 | **176 个**，**693,923,840 B** | 仅对已核实版本归档，核对当前 activation/Case/任务和打开句柄。D 盘四个压缩包在远端退出前回读校验 SHA，恢复时可按包内 manifest 找原路径和精确版本。 |
| 浏览器可再生缓存 | **607,608,832 B** | 仅对拿到 owner 锁且无浏览器进程占用的白名单缓存目录操作，身份数据保留。 |

分项记录对象变化，根盘净增以 `df` 为准。档案在 `D:\DoxAgent-Cold\20261123-emergency`，四包共 **176,270,480 B**。**19:13:21 最终复验**根盘可用 **14,772,981,760 B（14.77 GB）**，仍比接手时净增 **12.65 GB**。22 个生产和基础容器运行，API 两个探针均返回 200，Collector 健康，有 19:13:20 的新接受记录，PENDING 为 0，Read 四路 checkpoint 均与对应源的抽样 head 一致。[Acquisition 物理切换](../artifacts/audits/root_disk_regrowth_20261123/acquisition-host-stream.jsonl) · [切换后业务探针](../artifacts/audits/root_disk_regrowth_20261123/dox-1123-acquisition-verified.jsonl) · [最终复验](../artifacts/audits/root_disk_regrowth_20261123/final-probe.txt) · [空目录](../artifacts/audits/root_disk_regrowth_20261123/dox-1123-empty-dirs.jsonl) · [浏览器缓存](../artifacts/audits/root_disk_regrowth_20261123/dox-1123-site-cache.jsonl) · [D 盘 SHA 清单](../artifacts/audits/root_disk_regrowth_20261123/archive-local-hashes.json)

## 2. 上一轮清到 62.84 GiB 后，余量怎样耗尽

11 月 18 日 21:33:48 实测已用 **332,479,913,984 B**、可用 **62.84 GiB**。到 11 月 23 日 18:38:09，实测已用 **397,834,072,064 B**。约 4.88 个自然日净增长 **60.87 GiB**，折合约 **12.48 GiB/自然日**。其间已有发布清退和人工腾挪，实际产生量高于净值，交易日与周末增速也不同。根盘本轮并非首次跌到临界点。

| 现场时点 | 根盘可用 | 含义 |
| --- | ---: | --- |
| 11-18 21:33 | 62.84 GiB | 上次正式清理后复验 |
| 11-18 23:03 | 55.18 GiB | 约 90 分钟减少 7.66 GiB |
| 11-19 23:09 | 23.48 GiB | 再少 31.70 GiB，白天 `containerd` 曾到 25.62 GiB |
| 11-20 23:18 | 12.46 GiB | 再少 11.02 GiB |
| 11-21 23:26 | 0 GiB | 主机记录可用为零 |
| 11-22 23:32 | 0.33 GiB | 周末仍处于临界状态 |
| 11-23 18:38 | 1.98 GiB | 人工先腾出约 2 GB 后的本轮起点 |

历史小时采样的目录子项有各自的实际测量时间。`workspaces`、`runtime`、`read`、`v21-production-validation` 反复触发单目录 25 秒超时；Bus 目录甚至一直沿用 11 月 13 日结果。数据库主文件 `stat` 与 `df` 可以进行同口径比较，缺失的历史目录值只能用上次深清对象回执、树占用和本轮新对象出生队列交叉定位。下面列出的出生队列是当前仍留盘的新增对象，不能与相同目录的净增长相加。资料见 [11 月 18 日最终验收](../artifacts/audits/root_disk_50g_execution_20261118/final-acceptance.json)、[容量时间序列](../artifacts/audits/root_disk_regrowth_20261123/dox-1123-focus-capacity.jsonl)、[容量脚本](../scripts/ops/storage_release.py#L108)。

## 3. 主要增量与实际写入者

本轮清理后按完整树对账，生产 `/data` **307.19 GiB**，其他根盘路径约 **50.78 GiB**，合计 **357.97 GiB**，临近 `df` 的实际已用 **358.61 GiB**，两者差约 **0.63 GiB**。两次树扫与 `df` 时间不同，个别业务临时路径在扫描中正常退役。`/data` 内独立大组为 Workspaces **100.15**、Runtime **90.03**、Research **41.94**、Read **26.29**、Codex Home **21.19**、Bus **19.64**、Initialization **3.03**，单位均为 GiB。Bus 数字已经扣除本轮 Acquisition 物理缩库，不能用于清前横比。独立 site 卷 **20.96 GiB**，相较上次预清理 **+1.18 GiB**，主要在 profiles，snapshots 仍 **2.74 GiB**；身份和可再生缓存必须分开。[生产数据树](../artifacts/audits/root_disk_regrowth_20261123/dox-1123-data-du.tsv) · [其余根分区](../artifacts/audits/root_disk_regrowth_20261123/dox-1123-outside-du.tsv) · [Site 独立卷](../artifacts/audits/root_disk_regrowth_20261123/dox-1123-site-du.tsv)

| 优先级 | 对象及写入者 | 本轮证实的增长 | 业务价值判断 |
| --- | --- | --- | --- |
| 第一 | Maintenance Workspace，`v2-maintenance` / Scheduler 编排，`codex-worker` 落盘 | `workspaces` 实测 **100.15 GiB**，约占根盘已用的 **28%**。由上次清退回执校正的名义基线 **69.19 GiB**，差约 **30.95 GiB**，相当于本轮复满净增的一半。新建 Maintenance 目录当前仍占 **34.95 GiB**，其中 11-19、11-20、11-21 的出生队列分别 **9.50、11.65、7.66 GiB**。 | 运行中的可验证输入以及承诺恢复的产物有用。把同一输入、索引、任务过程、发布副本长期分开存，与业务版本保留没有必然关系。 |
| 第二 | Runtime Event Library 分支、Native，主要由 Runtime Maintenance / Scheduler 写入 | 本轮仍在线、出生于 11-18 清理之后的 **1,807 个** Event SQLite 文件，分配约 **7.91 GiB**。11-19、11-20、11-21 分别 **2.53、2.49、1.73 GiB**。`runtime_artifacts` 较上轮退分支后的名义基线约 **+7.80 GiB**，计回此次归档则约 **+8.44 GiB**。`native-files` 另增 **2.04 GiB**，Runtime 主库体积基本停在 37.37 GiB。 | 当前已发布事实、事件和需要重放的精确版本有用。每个 run 的 SQLite 容器、索引、相同底稿和无变化分支不应隐含长期保留义务。 |
| 第三 | Research/D3，Maintenance 发布，Worker、投影与 Transport 共同消费 | Research 主库从 **25.90 → 29.99 GiB，+4.09 GiB**，约 **0.84 GiB/自然日**。`research/native-files` 对上轮预清理清册约 **+1.82 GiB**。正式 gzip 池当前约 **6.97 GiB**，其中有上轮历史迁移，不能把全量算作本轮新增。 | 采纳研究存一份，历史引用保留版本与内容哈希。暂存与正式库的全量策略内容、大而可再投影的镜像和工作区发布镜像要分别审查。 |
| 第四 | Read，`v2-projector` 写，`v2-api` 读 | Read 目录约 **22.02 → 26.29 GiB，+4.27 GiB**。自动取样的主库在 11-18 夜 **18.69 GiB**，11-19 **19.92**，11-20 **21.09**，11-21 **21.75**，本轮约 **22.00**。主库空闲页约为零。 | Read 保留有消费者的可服务状态与有限 MVCC 窗口。持续保留的技术 current、未共享的语义定义和衍生索引需要用端点实际读路径证明。 |
| 次要 | SDK 目录、`containerd`、Bus 等 | `codex-home` 约 **21.19 GiB**，相对 11-18 预清理 +2.36 GiB，若计入当晚已退 0.43 GiB，会高于此数。`containerd` 约 **17.03 GiB**，相对预清理清册 +3.06 GiB，曾短时达到 25.62。Bus 主库维持 **13.32 GiB**，可复用页从约 1.07 升到 2.52 GiB。 | SDK 细粒度诊断、发布峰值值得继续压，排在每天多个 GiB 的业务工作树之后。 |

### 3.1 Maintenance Workspace 的源头与退出缺口

`workspaces` 上次预清理为 77.90 GiB，按定量回执扣除当晚 7.89 GiB 空目录和 0.82 GiB D3 重复发布块，得到上述 69.19 GiB 名义基线。上次采样至验收之间仍有业务写入、另有一批无独立总回执的释放，本轮扫描也跨过空目录清理。因此 30.95 GiB 是校正估计，预清理同口径目录差为 **+22.25 GiB**。独立的出生统计中，1,482 个新 O3 子目录占 **21.14 GiB**，1,482 个 O3 父目录占 **13.67 GiB**，O2 新建约 **0.15 GiB**。相比之下 3,282 个仍在线的新 W3 Ready 目录合计约 **0.61 GiB**。`.init` 约 **0.09 GiB**，生产验证目录约 **1.19 GiB**，两者与 11-18 预清理清册相同。[全量出生分组及日期占用](../artifacts/audits/root_disk_regrowth_20261123/dox-1123-fs-cohorts.jsonl)

两棵目录对应同一 D3 家族。`v2-maintenance` 把上轮 PolicySet、覆盖与当前材料冻结供模型使用。`runner_v21.py` 在任务准备时构造 task identity、导航和物料页，按工作区物化。接受任务结果时还将相同内容写入 snapshot 与普通输出路径。重试需要稳定输入与已验收结果，但可以用已冻结文件引用或单份快照实现。`orchestrator_v21.py` 进一步写 release 文件，`workspace_store.py` 用 `copyfile` 写到正式 `published`。路径不同本身不产生第二份独立内容的业务价值。[任务物化与快照](../src/doxagent/workflows/codex_document3/runner_v21.py#L225) · [发布物料](../src/doxagent/workflows/codex_document3/orchestrator_v21.py#L1311) · [Workspace 发布副本](../src/doxagent/codex_worker/workspace_store.py#L304)

对 20 个新建 O3 家族抽样，20 个子目录都有成功 Job，20 个都没有 Worker `retained-assets` 退出标记。20 个子目录普通文件按分配块计约 **252 MiB** 的 `context`，远大于其 `attempts`、`output` 与 `audit`。父目录的 `context` 另约 **75 MiB**，`artifacts` 与 `published` 各约 **58 MiB**。仅对不小于 100 KB 的 Context/Attempt 文件做 SHA 和 inode 检查，每个家族都有独立重复块，中位约 **2.45 MiB**，合计约 **53.9 MiB**。内容包括 `shared/document2.json`、`shared/event_library.md`、`shared/base_coverage.json`，部分 `event_library.md` 还出现第三份作导航物料。`context_index` 中的记录/资产、已交付后的其余过程输入是另一块开销，不能用已证实的最小重复量替它们估计回收比例。[终态样本及 SHA 对照](../artifacts/audits/root_disk_regrowth_20261123/dox-1123-workspace-samples.jsonl)

发现了明确的自动退出回退。11 月 11 日把 `collect_inputs()` 接入 Worker 常驻低频任务。11 月 17 日 `c5d9759d94` 为避免跨 run 全局扫描拖慢 W3，移除了常驻调用，保留显式维护入口。随后发布的恢复改动没有重新接上这个入口。生产 Worker 实际安装文件仍是该逻辑：每五分钟醒来检查，只有 SDK 会话条件分支，没有 Workspace 或 `.init` 清退调用。取样时 `resource_retention` 最后回执为 **11-17 17:49**，之后已停止更新。Runtime 自己仍尝试退休有精确终态凭证的 D3，但只覆盖其能关联的 run 和限定路径。Workspace 文件收集器即使手工执行，也会跨全库解码引用、按记录数扫并只对文件 `unlink()`，完成后同一 `owner_revision` 直接跳过，原有空祖先目录就会持续留盘。上次人工释放 **7.89 GiB** 空目录，这次又收回 **0.98 GiB**，是这个残留边界的实测代价。不能把旧全局扫描直接接回实时 Worker。[Worker 常驻入口](../src/doxagent/codex_worker/resource_maintenance.py#L29) · [工作区候选、退休 fence 与收尾](../src/doxagent/codex_worker/workspace_retention.py#L98)

### 3.2 Runtime 的 Event 分支把历史版本固化为每次 Run 的整库

`branch_library()` 先建一份候选库，再从底稿复制当前有效的 canonical Fact/Event、成员、状态、版本及待发布批次；代码选择了基准版本而非整段无关尾史，但每个分支仍复制了大块相同底稿及索引。Maintenance 用候选根构建 Reference 结果，正式 activation 继续存下这个根和精确版本。后续投递、Case pin 及 W1 的首次发布时间确有消费者。GC 还对曾推进发布版本的分支保留整根以支持 W1；对其他有引用候选，压缩遇实时压力会让路。本轮 owner 回执观察到 29 个待重试项、零整根退休、零分支压缩以及 `active realtime; deferred heavy compaction`。这是**入口继续消耗多个 GiB/日，退出在高压力下产不出同等结果**的机制。Reference SHA 复用和收据瘦身有价值，但不会覆盖 SQLite 分支里的底稿和索引。[创建分支](../src/doxagent/persistent_runtime_v2/event_branch.py#L14) · [候选根与引用](../src/doxagent/persistent_runtime_v2/maintenance.py#L167) · [保护规则](../src/doxagent/persistent_runtime_v2/resource_gc.py#L59) · [实时让路](../src/doxagent/persistent_runtime_v2/resource_gc.py#L460)

从五个日期的出生队列分层抽取 173 个分支。按 `.branch.json` 的基准版本逐库核验，**159 个**没有发布更高版本，**13 个**发布了更高版本，**1 个**无法核实。无升级说明这批候选没有发布独有的新版本，仍可能被历史 activation 或 Case 引用，不能直接按样本判定可删。建议先用读端对照证明复用原 `(ticker, version)` 仍能支持获知时间和可引用的历史成员版，再把无变化 run 引用改向现有版本。有变化的分支只保留新增事实、变化和必要恢复内容。按 O2/O3 完成量与新分支 GiB 比较，确认入口减量以后再安排已有分支选择性收缩。[分支逐日抽样](../artifacts/audits/root_disk_regrowth_20261123/dox-1123-fs-cohorts.jsonl) · [Runtime owner 回执](../artifacts/audits/root_disk_regrowth_20261123/dox-1123-runtime-owner.json)

### 3.3 Research 中，正文减量成功，策略集仍重复持久化

Research 的完整 `dbstat` 测得 `codex_document3_policy_sets` **12.64 GiB**、`codex_document3_v21_staged_sets` **11.30 GiB**、`v2_source_receipts` **3.63 GiB**、`codex_document3_v21_runs` **1.95 GiB**、`codex_document3_v21_tasks` **0.26 GiB**，`codex_runtime_records` 含索引仅 **0.12 GiB**。前两张表占主库约八成。新一批 1,480 个 staged 行全部能按 ticker/version 联到正式表，采样时均已成为历史版。`s.payload` 和正式 `policy_set_json` 各约 **1.29 GiB**，正式 `runtime_projection_json` 另 **1.32 GiB**。另抽取新旧各 16 个版本，解析 JSON 后逐结构比较，32/32 的两张表 PolicySet 相同。同 cohort 的 staged receipt 只有约 **13.88 MiB**。这证明取消新 receipt 中的全投影确实生效，当前主库新增仍主要来自正式与暂存双写以及正式投影全量物化。`published_documents` 自上次清理后的 **7,400** 条均为 `gzip-v1`，记录 JSON 约 **2.52 MiB**，其引用正文声明总逻辑量约 **4.06 GiB**。不能用这些正文的逻辑量估计实际压缩池回收。[Research 表分配与记录格式](../artifacts/audits/root_disk_regrowth_20261123/dox-1123-sql-serving.jsonl) · [暂存与正式表校对](../artifacts/audits/root_disk_regrowth_20261123/dox-1123-focus-research2.jsonl)

`save_staged_v3()` 写完整策略与交接收据，供校验及显式指定上一版使用。正式表再存原始 PolicySet 和 `_project(policy_set)` 的完整结果。D3 后续 maintain 当前还会打开同一旧版的 Workspace `document3.json` 和 coverage，分别校验内容与哈希。保留版本身份和实际旧版内容有必要，由单份按版本/SHA 的策略体提供。staged row 可只保留交接和版本，引导现有读取接口解析；runtime projection 首先核对哪些字段和计算真有热读价值，当前版可以维护，老版可用相同算法验证重建后按需加载。不要批量删除现有文件直到 manifest 读路径和旧版本回放已过验证。[暂存接口](../src/doxagent/workflows/codex_document3/state_v21.py#L389) · [正式版本双写](../src/doxagent/workflows/codex_document3/repository.py#L483) · [维护输入对历史文件的依赖](../src/doxagent/workflows/codex_document3/orchestrator_v21.py#L1458)

### 3.4 Read 要核对 current 消费契约，单加快 GC 不够

当前使用 V4 活跃代际，Projector 运行 `e6452a8e`。Read 的薄投影已把运行 tasks 限为 ID/ticker/kind/status。近期 `changes` 样本的 before/after 都为 NULL，近期 `object_history` 大头是 `graph_member` 约 12.7 MiB，其余历史样本也在 MiB 级。因此不能拿本轮 **4.27 GiB** 目录增量全部解释成 `changes` 重复 payload 或整个短历史堆积。当前在线 GC 抽样仍出现 2 秒预算内对 `object_history` 的 `interrupted`，且有已提交的删除进度；应同时跟踪历史退出速率和真正还在扩张的 current、定义和索引。主库零 freelist 说明本轮此时没有现成的几个 GiB 文件压缩收益。Read 的近期 current 变动尾部，`policy_detail`、`policy`、`policy_catalog` 分别贡献约 **38.6、35.8、21.4 MiB 原始 JSON**，也有消息与使用记录。这是当前写向哪些对象的证据，与多日 `dbstat` 分解的物理差异口径不同。服务历史端点、流的 REMOVE 机制、活跃 cursor 和旧代技术对象各要校验实际读依赖。[Read 薄字段](../src/doxagent/v2_read/serving_fields.py#L31) · [Read GC](../src/doxagent/v2_read/serving_gc.py#L18) · [在线采样](../artifacts/audits/root_disk_regrowth_20261123/dox-1123-focus-read.jsonl)

在线逐类索引核对得到 `policy` **1,109,341**、`policy_detail` **855,206**、`policy_catalog` **849,900**、`policy_admission` **849,900**。`contributions_current` **2,361,482** 行，`definition_ref` **1,721,087** 行，`object_history` 实测 **36,155** 行。旧 `native:poll_runs`、`native:poll_captures` 两类 current 均为 **0**，此前专门治理的旧 Poll 镜像并不是本轮元凶。代码每个正式策略修订会生成 detail，再按其关联的 Document2 shell 数重复生成 `policy` 摘要，另随生命周期生成 catalog 和首次 admission。定义池的 SHA 去重已经存在，仍要核对各历史行是否需要长期配齐所有衍生索引。Case 回证明确读 `policy_detail`，Policy KPI/列表明确读 catalog，D2 壳下列表应保持原查询结果；可测试用关系键连接一份摘要，精确历史详情继续按版本读。Read 与 Runtime 全库 `dbstat` 在生产观测窗口未能及时取得，结束了长读探针，Read 各类别的物理 GiB 增量暂不作精确分摊。[当前类别及 GC 收据](../artifacts/audits/root_disk_regrowth_20261123/dox-1123-quick-read.jsonl) · [策略多壳投影](../src/doxagent/v2_read/formal.py#L344) · [Case Detail 消费](../src/doxagent/api_v2/case_detail.py#L144)

### 3.5 Acquisition、Bus、SDK 与发布的优先级

- Acquisition 的逻辑退出有实质成效。同一组表在 11-18 到本轮压缩前，`cycles` **6,895,451 → 597,727**，`physical_cycles` **6,261,171 → 2,726,170**，`captures` **7,090 → 517**，`unit_hours` **13,893 → 52,766**。然而约 **10.74 GiB** 退出成了库内空页，`auto_vacuum=0` 的主文件仍被根盘计费。本轮缩库后，`physical_cycles` 连同索引仍 **1.46 GiB**，`cycles` **0.59 GiB**，`unit_hours` **0.073 GiB**。设计应允许物理高水位随退出而收敛，在低盘位前处置，避免再次依赖临界点上的停采 `VACUUM INTO`。每小时摘要是否足以替代全量物理请求细节，由实际诊断调用决定。[逻辑退出](../src/doxagent/message_bus_v2/collection_plane/maintenance.py#L123) · [缩库后表页](../artifacts/audits/root_disk_regrowth_20261123/dox-1123-sql-bus.jsonl)
- Bus 主库物理量几乎横盘，且可复用页增长。表页采样仍见历史 `poll_captures` **3.03 GiB**、`distribution_runs` **1.19 GiB**、`message_content_revisions` **0.91 GiB**。前两类要复核历史端点及调查窗口，标准消息和独有正文有业务价值。近几日 Native 仍在写，但同时有退出；Bus Native 实际目录较上次预清理略降，隔离树约 **0.81 GiB** 单独看。以前把 ACK 后传输记录与正文一起永久在线保护的办法不适用；按真实消费/replay/pin 保留恢复路径。
- SDK 多数新增迹象来自 `logs_2.sqlite`，本轮约 **3.77–3.80 GiB**，高于 11-18 现场约 **2.32 GiB** 的报告量。分配页几乎全在 `logs` 表（含索引约 **3.79 GiB**）。`thread_history_1.sqlite` 约 **3.98 GiB**，其中 `thread_items` 含索引约 **3.86 GiB**，与可恢复的真实会话有不同用途。会话退役开机等待 6 小时，再以没有全局排队或运行 Job 为前提，久忙时可能一直顺延。先定位可丢弃的调试日志和大型字段，产品会话/终态证据应依 owner 退出。专门目录 `.w3-contexts` 近期盘量变化约 **0.02 GiB**，`.control` 约 **0.17 GiB**、`.w3-case-archives` 约 **0.28 GiB**，这些路径没有证据与 Maintenance 同优先级。[Bus、Acquisition 及 SDK 表证据](../artifacts/audits/root_disk_regrowth_20261123/dox-1123-sql-bus.jsonl)
- BuildKit 当前约 **0.09 GiB**，11-23 18:37 的发布 reconcile 仍回收约 **0.51 GB**。`containerd` 几次发布时曾从约 14–15 GiB 峰值到 25.62 GiB，随后回到约 17 GiB，发布会提前用掉安全余量，自动退役实际在运行。宿主 `/var/log` 约 **0.38 GiB**，`/tmp` 约 **20 MB**，检查未发现大于 64 MiB 的已删除仍打开文件。只继续清构建缓存无法抵消业务目录增长。

### 3.6 传输 48 小时策略只在有索引的生产库真正到龄退出

`source_lifecycle.expire()` 的生产代码对 Research、Bus 按 48 小时，其余源按 7 天。正常安装 `v2_source_outbox_expiry` 后以记录时间和 ACK 选过期行。旧库没有该索引、索引查询中断或走 `hot_only` 时，回退分支改为 `seq > cursor AND seq <= ACK`，扫描到年轻行也会推进 cursor。之后遇到空页会提前返回，代码没有把游标回到仍未退出的旧前缀。同一条在第一次访问时太新的记录，到了过期时间可能已经永久落在 cursor 后面。短窗口配置对这种路径无效，后台仍能打印成功扫描却持续找不到可退出旧数据。生产实例已经核实该路径缺口如下。[已安装镜像对应的过期逻辑](../src/doxagent/v2_read/source_lifecycle.py#L229) · [索引、ACK、游标与龄段证据](../artifacts/audits/root_disk_regrowth_20261123/dox-1123-transport-final.jsonl)

| 数据源 | 现场状态 | 结论 |
| --- | --- | --- |
| Research | 缺时间索引，cursor 和 ACK 均在 **141,586**，旧记录最早 **11-17 15:44**，无 legacy/exception pin。固定对照北京时间 11-23 19:00，cursor 之后无法再扫到的过龄且已 ACK 记录 **66,345** 条。 | 48h 在当前库没有兑现。`v2_source_receipts` 全表约 **3.63 GiB**，可退比例需做物理页/外置引用分解。 |
| Runtime | 缺时间索引，最老热记录 **11-12 05:23**，落在游标之前且按自身 7d 规则已过龄、已 ACK 的 **270,200** 条。当前有 **17,216** 条精确例外 pin。 | 真正的例外按坐标保留，可普通退出的前缀不应因例外一起延长。Runtime Native 的近龄内容与 `v2_source_receipts` 引用高度相关，单一对象可被多条 receipt 重复引用，引用边字节不能相加成磁盘收益。 |
| Bus | 有时间索引，最老热记录 **11-21 19:25**，同一窗口已有 ACK 且过龄的记录在游标之前为 **0**。 | 现场观察与 48h 预期一致。 |

已 ACK 传输数据的核心用途是短期重放，不应按 canonical 业务数据留存。优先用现有正式迁移方式配置 Research 与 Runtime 的过期查询。回退本身也需具备到龄后重访的能力，避免下一个老库、低余量或争用窗口复发。不要仅重置一次 cursor 然后视为结束。退出后验 oldest eligible age、ACK/例外 pins、可从当前态重建 Read，以及 Native 的物理块回收。[Runtime Native 引用证据](../artifacts/audits/root_disk_regrowth_20261123/dox-1123-recent-owners.jsonl)

## 4. 过去哪些修复有效，哪些没有达到目的

| 轮次 | 当时实际做到了什么 | 为什么这次仍会复满 |
| --- | --- | --- |
| 10 月 13 日 数据与发布规则 | 回收旧传输/技术投影，Bus 与 Read 受控重建缩库。Read 主库约 29.50 → 22.93 GiB，精确版本与 token 验过。 | 这是存量收益。范围仍围绕当时的 Poll/CDC 和发布卫生，未限制 Maintenance 新输入、Event 分支及 D3 策略集。长期自然写入验收未完成。[执行回执](disk-repair-execution-20261013.md) |
| 10 月 18 日 Read 重构 | V4 上线实际 Read 整目录约 3.11 GiB，合并内容、短历史和窄投影具有真实即时收益。初始化快照共享不变内容。 | 到 11-23 活跃 Read 又占约 26.29 GiB。代际压缩和最初正确性没覆盖随后不断进入的 current 及更多衍生投影。退出速率也受预算影响。[执行回执](doxagent-storage-refactor-execution-20261018.md) |
| 11 月 11 日 生命周期 | Read 恢复真实 GC 进度，Bus 重建约 26.10 → 12.44 GiB。Worker 低频退出、Acquisition owner、Runtime 传输字段瘦身和发布轮转都补了职责。 | Worker 自动退出后来因真实延时问题被撤出，仍缺跨 run 全局查找的低成本替代。Read 当时已经保留“过期队列未收敛”的验收边界。Bus 主库后续横盘，属于应保留的改善。[生命周期回执](storage-lifecycle-implementation-20261111.md) |
| 11 月 14 日 人工集中退出 | 根盘净释放 **74.58 GiB**。Read 压缩一次释放 **19.80 GiB**，旧初始化快照至少 **12.46 GiB**，Reuters 遥测 **8.19 GiB**，大量 Maintenance 存量镜像与历史副本退出。 | 能证明当时的文件低价值、可以移除，不能证明新 run 不再生成它们。已经识别到 Workspace、D3 staged/receipt 与历史投影，后续修复仍保留大部分写入契约。[机制与回执](disk-cleanup-and-growth-mechanisms-20261114.md) |
| 11 月 18 日 源头减量发布 | `gzip-v1` 发布引用进入新生产行，staged receipt 已瘦。Acquisition 旧细节进入小时汇总并逐批退出。Runtime 内容小对象、传输 48h 策略及发布清退具备新能力。完结时根盘有 62.84 GiB，来自随后单独清退。 | **减掉的是部分副本，最大写入链仍续写。** 自动工作树清退未恢复，D3 父/子输入仍独立物化，Event 仍先全量建分支，Read current 无证明的稳定上限，Research/Runtime 传输回退错过旧前缀，Acquisition 未归还空页。原设计写明的生产工作区新增长降 70%、采集块写下降 50% 等指标还没有在连续交易日实测通过。复发证实发布整包没有解决根盘持续填满，已经有正向证据的单项仍应保留。[开发与待验项](storage-source-reduction-development-20261118.md) · [发布验收边界](storage-source-reduction-release-20261118.md) · [上次深清](root-disk-50gib-cleanup-execution-20261118.md) |

## 5. 下一轮只应优先解决有量级的增长

1. **先做 Maintenance 源头和终态输入退出。** 把同一业务输入在一个家族内冻结一次，父子、重试与导航复用已校验内容。当前仍要用旧路径的正式发布文件可继续暴露兼容路径，实际只保存一份不可变字节。把已被下游消费且同族终态的输入退休责任接到现有 owner，引用检查只查候选 run、家族和已发布 manifest，清文件时顺手退出空祖先。不要重新引入 Worker 主循环全库扫描。以同等 O3 完成量计算 bytes/run、GiB/交易日，优先把每天多个 GiB 的工作区降下来。
2. **治理 Event 先复制再退役。** 先对无变化组合证明沿用同一个 `(ticker, version)` 后的 Case、Reference、W1 结果一致。确有变化再按同一个版本协议保存新增事实、成员变化和必要恢复材料。验证通过前保留现有 archive 恢复路线。用新建分支数、平均块量、真实发布数量和历史精确版本回放验收。
3. **修传输回退，同时做 Research 和 Read 的大表。** 补过期索引和回退重访，各库用实际 ACK 与保留规则验证。Research 统一暂存/正式对同一版本的读取与引用，再测试旧版 runtime projection 能否从唯一策略体复算。Read 以 current 类别和实际 API/修复消费者列出必要字段，删无消费者的物化；GC 保持有界、可续，用户可用窗口与旧业务回放分别验。Research 主库与 Read 整目录的上述比较合计增量约 **8.36 GiB**，收益仍高于重复扫几十项百 MB 的缓存。
4. **将 Acquisition 的物理回收做成计划动作。** 本次 ~11 GiB 是历史高水位的物理回收，不纳入未来平均可减少 11 GiB 的承诺。对主要短寿命明细比较原位增量释放与有容量前提、可恢复的维护窗口，保持现有 WAL 并发与 capture 接收契约，等到自然小时归档后同时量 `pages_in_use`、`free_pages` 与实际 `df`。
5. **修复观察盲区，作为验收项。** 使用已有容量记录，按根盘 `df`、各 SQLite 已分配/可复用页、按日期出生 cohort、owner 退役物理量计账。把大型目录改为小批持续游标扫描，明确每个数据点是完整、进行中还是上次成功。主要窗口至少覆盖 5 个有自然负载的交易日，使用同样的 O3 完成量和投影水位进行前后比较；同时确认业务吞吐、W3 延时和历史读取可用。发布成功、单次 `df` 回升、已删除多少行分别报告，均不能独自作为容量修复验收。

### 仍需业务研判的保留决策

| 决策 | 建议默认口径 | 若需要更长完整留存 |
| --- | --- | --- |
| 结算后是否要逐文件重现 O3 Agent 的整个任务导航、attempt input 与中间版本 | 保留最终结果、模型/用量、冻结输入 manifest 和真实发布内容；普通完成任务退出派生目录。 | 规定能实际用到的失败调查窗口和人工 pin；旧数据按需重建，避免把 owner 永久标活。 |
| 每次 Maintenance 的完整 Reference 与 Event 在线浏览是否有产品承诺 | 当前版持续在线，实际 Case/交易引用保留精确版本身份、业务内容与证据。 | 明确哪些历史请求要实时访问，哪些允许从归档在有限时间内恢复。W1 获知时点逻辑由同一版本、时间证据维持。 |
| D3 历史是否要在线展示每个已发布候选的完整 PolicySet 与其投影 | 保留精确历史版本可恢复能力；同一个版本的 staged 和正式阶段读同一份内容。 | 指定在线范围与老版回放时限，再决定持久的压缩资源与历史投影缓存。 |
| Acquisition 的逐次 HTTP/物理请求证据保多久、SDK trace 可用于哪些运维调查 | 保留当前证据、失败样本、节奏/成功率的小时摘要和调试短窗。 | 只为明确来源事故、合规或复盘标记保留整段，避免把所有高频成功记录作为长期恢复责任。 |

本轮做了紧急生产回收和只读业务审计。没有进行产品代码改造或新版本发布。所有后续动作先以正常业务持续写入的物理增量减少量作为验收。
