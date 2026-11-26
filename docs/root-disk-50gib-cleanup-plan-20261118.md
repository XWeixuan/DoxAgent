# 生产根盘占用复审与 50 GiB 余量清理方案

对象为 `doxagent-sg` 根分区。本次做只读盘点与取舍设计。起始快照为北京时间 2026-11-18 19:57，代码基线为 `37baf88d1`，为刚上线的存储源头减量版本及采集启动索引修正。当前轮次未执行清理、迁移、停机或部署。

## 决策

**优先退役已经结算的过程副本，把历史 Event 分支搬出生产盘，再转换 Research 的旧正文并收缩数据库。** 这三项针对实际占比最大的存量。Acquisition 的空闲页另开维护窗兑现。Read 暂停整库压缩，Site 只处理可再生缓存和已取代的身份快照。Docker 资源继续交给已有发布回收器。

根盘总容量 **373.40 GiB**。起始可用 18.24 GiB，20:21 复测可用 `18,633,519,104 B`（**17.35 GiB**），`df` 使用率 96%，inode 使用 39%。达到 **50 GiB 可用**需要再净增 **32.65 GiB**。以释放 **37–38 GiB** 排批，给执行期间的业务新增留约 4–5 GiB。若要求**额外净回收 50 GiB**，该快照下验收线是 **67.35 GiB** 可用。

业务取舍明确为：已结算且已采纳的运行保留最终文档、SHA、关键证据、发布/采纳关系、用量及失败原因，放弃老任务原地续跑、工具级 replay 和完整过程镜像。旧 Event 分支的精确版本有恢复价值，历史分析可以接受离线恢复；仍被实时任务、活跃 Case 或常用历史查询直接读取的版本保留在线。已确认重复的内容和可再生缓存直接退役。按 `df -B1 /` 实测验收，候选占用、复用页、净回收分别记账。

## 根盘全景

分两段低优先级扫描根分区，主应用卷一次遍历，其余根盘目录另一段遍历。`du -x -B1` 统计分配块，同一段内硬链接只计一次。两段合计 **354.43 GiB**。20:21 的 `df` 已用 **355.14 GiB**，差 **0.71 GiB**（约 0.20%），包含并发写入、两段采样时间差和其他未归集占用。应用卷遍历中 Bus 的 SHM 文件轮换消失一次，各根目录总计已返回。比例分母是 354.43 GiB，各行互斥。

| 部分 | 占用 GiB | 占已归集 | 清理决策 |
|---|---:|---:|---|
| Runtime | 92.72 | 26.16% | 优先分层历史 Event，退出结算过程，Native 按引用清理 |
| Workspaces | 77.90 | 21.98% | 优先清已结算过程和重复发布文件，清冷派生件 |
| Research | 49.00 | 13.82% | 转换旧正文并物理缩库，保留正式正文和可恢复版本 |
| Bus / Acquisition | 35.89 | 10.13% | 清消费后过程历史，必要时兑现 Acquisition 空页 |
| Read | 22.02 | 6.21% | 继续正常退役，无值得重复缩库的空闲页 |
| Site 卷 | 19.78 | 5.58% | 清再生缓存、已退役实验和旧快照 |
| Codex Home | 18.83 | 5.31% | 终态会话分批退出，日志已缩过库 |
| containerd | 13.97 | 3.94% | 发布器按引用回收，保留在用和回滚 |
| `/var/lib/doxagent` | 3.77 | 1.06% | 核证过期恢复/取证大对象 |
| Initialization | 3.03 | 0.85% | 留正在生效的业务资产，退已结束恢复过程 |
| Ops | 2.79 | 0.79% | 正式导入后移除临时现场 |
| `/home` | 2.74 | 0.77% | 归档旧导出，保留实跑源码和 Native runtime |
| 其他主应用卷 | 2.86 | 0.81% | 清已交付验证/测试；保留小型活跃账本 |
| 系统、swap、日志与其他卷 | 9.13 | 2.58% | 无大项优先清理，BuildKit 仅约 0.09 GiB 已含在此行 |
| **核对合计** | **354.43** | **100%** | 四舍五入可能有尾差 |

**Runtime 与 Workspaces 合计 48.14%。** 加 Research、Bus 和 Read 后达 **78.30%**。Runtime 中主库 37.01 GiB、Native 28.71 GiB、运行目录 26.00 GiB。Event 主库 21.48 GiB 已含在运行目录中。Codex `sessions` 约 12.05 GiB，已含在 Codex Home。Workspaces 样本折算 Maintenance 全族约 58 GiB，实测工作区全卷 77.90 GiB。`.w3-sources` 3.68、`.w3-contexts` 2.32、`.w3-packs` 1.66、`.control` 1.11 GiB 均已含在上表。`.init` 与 `.init-contents` 本轮合计约 0.21 GiB，确认已无上轮十几 GiB 的规模。

与同日 04:22 的分布对照，Workspaces 约减少 10 GiB，而 Runtime 增约 5.3 GiB、Research 增约 3.4 GiB、Read 增约 1.3 GiB。期间既有清退也有发布与持续生产。这个对照用于确定下一批工作范围，新版日均减量需要另取稳定观测区间。

## 各部分的取舍

以下 `DATA` 为 `/var/lib/docker/volumes/doxagent-v2_v2-data/_data`。量化证据见本文末尾。

### 1. Maintenance 与初始化工作区

- **清 Maintenance。** 12,008 个 `runtime-maintain-*` 目录，9,612 个有 `complete` 标记。标记组剩约 **0.26 GiB** 单链接 `v21_state.json` 和 **1.23 GiB** 同尺寸、双路径、发布侧单链接的候选副本。另有 2,388 个未标记目录含 **2.99 GiB** 镜像和 **2.86 GiB** 发布侧副本。观察到 19,310 对已经同 inode。统计未把这些硬链接算进可回收空间。8 个目录落在活跃 job 家族，排除在候选之外。标记是筛选入口，执行时用 `workspace_retention.retire_d3_copies` 的实际结算、不可变性和哈希校验。对历史记录未达新入口条件的，按初始化/Runtime 成功结算、正式产物存活、无活跃同族/人工恢复义务补写退出证据。保持旧发布路径，校验后同文件系统硬链接。可再生的 `context`、`attempts/*/input`、`output/work` 和私有物化缓存，应随已结算 owner 退出。其收益单列，避免与上面的文件重叠。
- **停止 `.init` 全量清理。** 当前只看到 37 个实际快照目录和 2,376 个 owner 标记。上轮 `.init` 漏管存量已处理。逐个退掉超过有效恢复窗口、无待续跑义务的快照即可，按最后一个硬链接的物理块计收益。`.init-owners` 小收据可留。保留当前生效初始化版本的正式资产，历史成功 owner 的完整预节点镜像无需永久保留。
- **清冷 W3 派生件，保留历史决策输入。** Pack 及编译索引在已公开 SHA 可读取、已有新热版本且无冻结任务时继续由 owner 转 COLD。仅靠压缩包年龄无法区分行情原证和派生索引。已结算无 trade/watch/pin 且 ACK 完成的 Context 按现有退出账本处理。共享 Context/Source 物理删除需要全体反向引用，暂不给预算。保留 canonical 研究正文和被决策引用的历史行情字节。上轮已回收约 4 GiB 编译资产，不能直接照抄收益。

补抽样按目录名在两个组各取 64 项。标记组的 `context` 样本约 **73.2 MiB**，未标记组约 **543.8 MiB**，两组折算总盘候选规模约 **30.6 GiB**，含仍有用途或近期生成的文件，仅用于排序。标记组 64/64 的同族 Jobs 已终态超过一天，57/64 匹配 Runtime 成功。未标记组同族 Jobs 也均已终态，但只有 15/64 超过一天、11/64 同时匹配 Runtime 成功。优先复审标记组中仍保存的 `context_index/materials` 与 `context/event_library`。对有明确 canonical 版本的重复物化可积极退出，摘要、准确引用及独有材料继续保留。已有 `complete` 会跳过同 revision，历史补收走独立局部验收入口，不全局清空旧标记。上述样本量和 manifest 副本候选分别记录，样本外推不能直接加进收益账本。

### 2. Runtime 与 Event Library

Runtime 主库 **37.01 GiB**、freelist 接近零。库中包含 Case、持仓/订单的关联、历史阶段和大量过程输入及 transport。应按工作完成后的责任拆分。实时链路需要真实事实、冻结输入和可追溯的最终结果；已完成的临时 frame、全量调试请求和重复参考快照由现有终态压缩入口减掉。清退行后先复用页。Native 文件池按实际可达性处理，历史 `BUSINESS` 标签本身没有永久保留权，但解除引用前需指出哪个消费者退出了。近期跨内容池去重实查只发现约 9 MiB，停止对已压缩池做全盘同 SHA 排查。

Event 分支主库 census 为 **5,884 个、21.48 GiB**。仅匹配历史 activation 的 **5,668 个、20.73 GiB**，当前初始化指针匹配 **10 个、0.034 GiB**，未匹配 activation 的 206 个约 **0.72 GiB**。Case/Reference 全量汇总查询超时，随后改用生产 `referenced` 入口抽查 24 个历史根。24 个均有精确版本引用，且未命中当前 owner 保护，样本物理约 66.4 MiB。该结果支持优先研究冷档，但还需逐批核对在线历史消费者。`0.72 GiB` 也不能用无引用名义直接删除。以前的选版压缩实测没有收益，跳过老分支库原地逐库重压缩。

**决策为优先分层，主动放弃“所有历史分支实时在线”。** 先从既有 `resource_gc.referenced(..., only_root=...)` 的逐根检查开始，筛出终态、当前 activation/Control 均未引用、近 48 小时不活跃、无 pending/running/held/可恢复任务、无打开 FD、无活跃 W3 引用的分支。若没有任何历史消费者，由现有 Runtime owner 直接退役。仍有旧 activation/Case/Reference 精确版本约束时，逐个核对消费路径。已能用 Read 的物化证据满足历史呈现且只需偶发离线回放的，归档其准确分支；需要在线查的继续留。检查历史 Case、Reference、回放/恢复工具三个入口的代表请求。若在线入口需要冷分支，先保留批次，或把所用小版本证据转成既有的精确版本表示并验收。禁止用最新版本代替历史版本。

本地 D 盘实测余量约 **39.19 GiB**。分批流式打包至 D，根盘无需先生成压缩包。每批保留 `run_id → 原路径 → ticker/版本 → SHA256 → 归档文件` 的小型清单。归档时确保旧分支不变、数据库无未合并 WAL 或保留完整可恢复快照。每批在 D 试解、核对 SHA 和所需版本，验证能恢复到原路径，再按清单退掉服务器原件。使用已有 Runtime 退出锁，删除前重新查引用。归档只针对确有历史恢复义务的分支；无需替普通缓存、重复副本建立归档系统。

### 3. Research、Read、Bus 与 Acquisition

| 项目 | 决策与收益边界 |
|---|---|
| Research | 主库 **45.79 GiB**，目前 **2.52 GiB** 空闲页。27,183 条 `published_documents`，按 key 定距抽样 161 条都有 inline 正文。折算约 **17.2 GiB** 内联原文，样本按 4 KiB 分配对齐后 gzip 占原文 **35.4%**。不去重的样本折算约 **11.1 GiB** 潜在减量。已有 2.52 GiB 与转换后收益须在同一次实测中扣除 gzip 新文件及 WAL，且受业务页复用影响。保留可复现正式正文和发布标识，取消 SQLite 与文件间重复正文。 |
| Read | 活动主库 **18.30 GiB**，可复用页约 **0.02 GiB**，软链接只指向一个活动库。当前重复 VACUUM 退出预算。短视图、游标、投影中间版本及失效 Native 内容走已有 Read owner。单独挑选过期 serving generation，核对活跃 alias、view pin 和重建输入后才可清退。正式 Case/查询所需历史不按单一 TTL 截断。 |
| Bus | 主库 **13.32 GiB**，空闲页约 **0.60 GiB**。已 ACK 回放按新 48 小时边界、source epoch 和消费者/repair pin 退役。消息正文和投递证据保持可查。现阶段不为 0.60 GiB 单独重写 13.32 GiB 活跃库。 |
| Acquisition | 主库 **19.08 GiB**，实测 **4.28 GiB** 可复用页。旧明细在小时证据 seal 并核对 pending/终态后退休。物理缩库可兑现约 4 GiB 量级，优先作为最终补足。上轮 5 分钟窗口失败，本次新索引建成也花了约 5.5 分钟，安排更充足的单次窗口。保持采集水位、日历、覆盖证据和 ACCEPTED 记录。 |

Research 使用已发布的 `compact-published-bodies`。从启用新格式的应用容器执行 `python -m doxagent.v2_read.cli compact-published-bodies --source research=/data/research/research.sqlite3 --limit 32 --dry-run` 核配置。实际执行移除 `--dry-run`，保持单 owner，以上一页返回的 `cursor` 作为 `--after`，逐页核对 `conflicts`。161 个样本都有匹配的 immutable artifact 元数据，执行时再校验正文 SHA 和原 payload。先做一个可核算样本批次，观察新 gzip 实际块数、freelist 净变化、源回执和 Projector lag，抽验 D3 导出、W3 加载、Read 流以及备份恢复。结果足够再扩大。放弃已被采纳且无消费者的旧阶段过程体，保留结算证明。主库、staged set、CDC 和 Read 中的逻辑估算不叠加。转换后等源/消费者稳定，再决定整库缩库窗口。

缩库使用独立输出文件、明确写入暂停、核验后切换。先量 `page_count × page_size - freelist × page_size` 估计活页，再测压缩误差、WAL 和执行期增长预留。按完整样本折算，Research 转换先新增约 **6.1 GiB** gzip，理想活页从约 43.3 GiB 降到约 26 GiB。只有 A/B 实际先释放约 22 GiB 时，毛可用才约 40 GiB，扣新 gzip 后约 34 GiB，有机会容纳约 26 GiB 的独立新库。执行以实测活页和剩余量为准。根盘须能同时容纳原库及新库，或准备独立的可靠暂存盘。D 盘可以存经校验的冷档，当前未挂载为 SQLite 暂存文件系统。源库和 WAL/SHM 切换要按同一一致时点处理，核对 `quick_check`、schema、关键计数、队列与四路水位。验收后才退原库，保留一个可恢复点。Research 缩库预算一律扣除压缩对象新增占用，Acquisition 缩库仅计算其自身。两项分开执行。

### 4. Codex Home、浏览器和其他目录

| 部分 | 决策 |
|---|---|
| Codex Home | 旧 Session 完整工具对话价值随结算显著下降。有 final/用量/错误回执且无后续恢复、父子任务及进程使用的成功任务，按既有 SDK `thread/delete` 退出。已结算 Maintenance 过程按 24 小时，其他已成功 7 天，失败 14 天，只保留有继续排查价值的少量失败样本。`logs_2.sqlite` 2.32 GiB 已做过实缩，`thread_history_1.sqlite` 3.74 GiB，现有 freelist 约 0.064 GiB，不为它重复 VACUUM。按删除文件和缩库实际分开记账。 |
| Site | 实测 19.78 GiB，profiles 14.86、snapshots 2.74、registry 1.70、其余约 0.48。现有 `site_strategy.resource_gc` 先处理再生缓存和 BrowserMetrics。快照保留已验证当前恢复点，其余按 profile、inode、恢复健康回执取舍。明确不再使用的实验 profile 可整组退役，执行前核对当前路由、租约、Chrome PID、Registry 引用，仍有效的身份和最新恢复点保留。 |
| Ops、Validation 与测试 | 先核对正式导入收据及任务退出。保留可复跑的最小输入、导入产物和小结。已完成刷新的一整套临时控制库、Native 池和工作区，在无生产指向且唯一业务产物已正式归档后按环境整体退出。上轮已经删过孤立 Native，不能再计整池。 |
| 宿主恢复与发布档 | `/home/ubuntu` 约 2.38 GiB，`/var/lib/doxagent` 约 3.77 GiB。重点复核 1.59 GiB Reuters 取证 SQLite、0.60 GiB BE 导出、0.58 GiB COHR 旧 Research gzip。前者以故障复盘完成且无独有证据为退出条件，BE 只有正式结果和导出校验通过后处理，COHR 只在恢复谱系清楚后退役或迁 D。Guardian 的 `initialization-repair` 是现场 bind mount，核对 owner 后清旧运行，保留父目录和当前实例。 |
| Docker、系统和其他 | containerd 约 13.97 GiB，含实际镜像内容和已展开快照。仅用现有 release owner 核销非引用资源，保留实跑、兼容回滚、模板和稳定层。17 个角色刚发布且已自动回收，不把 containerd 大小作为清理收益。系统包、当前日志及 swap 的运行价值高。仅处理过期 journal 和临时导出，单项低于几百 MiB 的不作为达标主路径。 |

## 实施顺序与达标账本

| 阶段 | 预计服务器物理收益 | 进场条件 |
|---|---:|---|
| A 直接回收 | 目标 **5–8 GiB**，其中标记组有 **1.49 GiB** 量化候选，另外 **5.85 GiB** 待结算核证。站点、Ops、宿主和 Session 作为补足 | 先处理结算后的 D3 和宿主大块。对每批记录对象、保留证据、回收物理块。保留能释放数 GiB 的项，停止在小目录上反复扫描 |
| B Event 历史分层 | 目标 **15–20 GiB**，物理候选上界约 **21.48 GiB** | 用 50–100 个历史分支先验证恢复和实际读取。每批实际低于目标就更新预算，禁止用筛选总量代替实测收益。阶段 B 可在 C 前分批做，为数据库转换提供临时余量 |
| C Research 转换及物理缩库 | 目标 **10–13 GiB**，基于 **11.1 GiB** 正文样本净潜力和现有 **2.52 GiB** freelist | 小批转换确认压缩和实占下降方向。执行前按实际活页和空闲量核算新库所需空间，达不到先补独立暂存盘。活跃业务吞掉空闲页或源回放膨胀时更新预估 |
| D Acquisition 物理缩库 | 目标 **约 4 GiB** | 重新确认空闲页没有被近期写入消耗，维护窗允许实际时长。优先在其他步骤完成后判断是否仍值得做 |

按 A=6、B=16、C=11、D=4 规划，毛释放预算约 **37 GiB**，叠加末次约 17.35 GiB 达到 **54.35 GiB**，再扣实际同期增长。预算的关键是 Event 能退出至少约 **16 GiB** 或有独立补足来源。仅 A+C+D 的示例约 **21 GiB**，不足以达标。A、B、C、D 都是条件候选，当前证据不足以承诺无维护窗直接删除 32.65 GiB。

每阶段以最新 `df` 重新算差额。先做到 A 并试验 B，再决定扩展归档和 C 的维护窗。若 B 通过引用确认的可退量低于所需，就在 **Runtime 已结算过程和历史 Reference/Case 输入、可再生 W3 冷资产、已正式导入的 Ops 与 SDK 会话** 中用实际文件块补缺口。若多数历史 Event 确需在线，替代路线为按精确版本迁移历史引用或提供扩容暂存后做一次迁移，生产稳定优先于凑出估算值。

若采用 **净回收 50 GiB** 口径，37 GiB 中档预算还有 13 GiB 差额。四阶段上沿约 45 GiB，仍需至少 5 GiB 来自额外经核验的终态过程/历史资产下线。扫描并未核实这 5–13 GiB 的直接可删份额。不能用修改 ext4 保留块、删除有效备份、强制 VACUUM 零空页库或再算一遍历史收益顶缺口。

最终验收以距最后一批结束至少一次独立复测的 `df` 为准，记录可用、实际净增和剩余增长余量。核对 22 个生产及基础设施容器、API/Worker/Collector/Site 健康、采集新 ACK 和 PENDING、四路新水位及 source error。核对当前 activation、抽取老 Case/Research 读取和一份历史分支恢复。阶段间遇到新引用、挂起任务或 source error 时只暂停对应批次，其他低风险批次可以继续。

## 证据

- [主卷分层 DU](../artifacts/audits/root_disk_review_20261118/data-du.json) 与 [其余根盘](../artifacts/audits/root_disk_review_20261118/outside-data.json)。主卷文件保留尺寸前 120 项和各大项；表中的小目录合计通过父项差额取得。
- [SQLite 与 Maintenance 候选](../artifacts/audits/root_disk_review_20261118/owners.json)、[工作区体积样本](../artifacts/audits/root_disk_review_20261118/workspace-sample.json)、[同族状态样本](../artifacts/audits/root_disk_review_20261118/workspace-status.json)。
- [Research 正文及 Event census](../artifacts/audits/root_disk_review_20261118/content.json)、[Event 逐根 owner 样本](../artifacts/audits/root_disk_review_20261118/event-owners.json)。
- 上轮收益和失败边界见 [11 月 18 日清理回执](production-disk-recovery-20261118.md)、[源头减量方案](doxagent-global-storage-source-reduction-plan-20261118.md) 和 [上线回执](storage-source-reduction-release-20261118.md)。
