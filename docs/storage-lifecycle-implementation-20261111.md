# 资源生命周期优化实施记录

依据 [执行方案](doxagent-storage-lifecycle-optimization-plan-20261111.md)，2026-11-11。独立工作树 `D:/DoxStorageLifecycle20261111`，分支 `codex/storage-lifecycle-20261111`，以实际生产代码 b4f18aca 为起点，再合并最新 main 业务更新。原始 C 盘脏工作区没有被覆盖。

## 实现与边界

| 包 | 实际实现 | 保留边界 |
| --- | --- | --- |
| R1/R2 | 强制既有时间覆盖索引；剩余预算传递、独立阶段提交、持久游标、GC receipt；Acquisition 存在时停止旧 poll 双重投影，48h 技术头通过常规 ingest 关闭 | 有效 view/cursor 的 MVCC 前像、业务定义、精确 gap 保留；未因索引数量而盲删索引 |
| A1 | 小时摘要与物理退出分阶段提交；先取有限物理候选再查引用；游标绕过大量受保护前缀；每轮至多 128 unit-hour/2s，512 logical candidates/小时 | 原有精确 cadence 算法、SCHEDULE/DISPATCHED、PENDING captures 保护；异常超大单小时可能触发预算，应通过 receipt 诊断，未改成有损近似摘要 |
| D1 | 大 prepared/base/final/handoff 字段复用现有 Native CAS，减少反复 JSON 写入；发布结算独立于 PARTIAL 质量；结算退役后由 staged 权威成果恢复结果视图 | 保留发布版本、accepted-rearm/coverage/身份及审计；退休 run 禁止从已删过程输入续跑；不新建资产平台 |
| D2 | Worker 独立 5min owner；每轮 job 候选页256，最多退出32 workspace；已交接维护输入24h，generation retirement fence；大文件退出在短事务外 | 保留真实恢复 hold、source/adoption/citation 指向的产物、唯一输出；已校验相同 canonical output 才作为重复副本退出；W3完整上下文复制仍暂缓 |
| T1 | root 候选/引用范围索引、pending 续批；廉价过程退出不等待 W3 空闲，重压缩可让步；已发布维护 frame24h 后退出重复内容，保留 base revision/冻结选择/哈希 | Case/Effect/交易、活跃W3、被引用 Event 版本保留；原任务不可原地复活 |
| Native | 显式 owner exit 进入既有队列；继续全 owner 对账、snapshot lease、其他 artifact_ref/hold 与5min quarantine | 不把“退一个owner”当“删除共享内容”；老无主内容仍有7d orphan grace |
| Runtime CDC | producer 不再为 runtime_tasks 捕获 inputs/receipt，前端仍有状态进度，源任务保有原始上下文 | 修正了此前仅 reader 跳过、producer 仍写重复载荷的缺口；历史传输按既有 ACK/pin 退出 |
| I1 | 新 snapshot 写 initialization_id；主库与既有ops刷新控制库联合归属，跨库恢复保护；有限页和游标记录 unknown | 当前、必要前驱、rerun pin、真实 manual-resume hold 保留；不擅自取消旧失败恢复责任 |
| S1 | W3 registry DELETED 的 fork 可退出；拆开已退休 fork 的模板继承边与真实 subagent 父子边；SDK全树维护6h且空闲运行 | 活跃模板/分支与真实子任务链保留，不把SDK全树查询放入热派发 |
| B1/B2 | 操作备份 helper 同次复用一致 SQLite 快照，验证新恢复点后退旧；Site owner 每10min 轮转16 profile，7d旧白名单缓存退出 | helper不等于完整Native/profile灾备；手工任意cp不自动纳管；浏览器身份和活动/人工恢复 profile 保留 |
| 发布/容量 | 复用现有release owner，精确纳管遗留镜像tag；容量目录分组独立轮转，超时保留上次完整值；worker/acquisition/read receipt 汇总 | 不全局prune、不删volume；现役/回滚/其他引用镜像保留；40/20GiB风险提示不自动停业务 |

## 验证

复用既有 Python 环境，所有新增测试临时数据位于 D 盘。`eval/storage_lifecycle_20261111/` 含 JUnit 证据：regression95、pipeline101、release-cases14、source-final70、native-final30、core-final86、freeze13，以及merged-final46、latest-main89、ledgers-merged31均通过；各组重叠，不能相加为独立测试总数。覆盖百万 Read 账本、10万保护物理记录、阶段失败已提交结果、MVCC前像、共享Native引用、D3回滚、已结算PARTIAL工作区、浏览器身份、Runtime冻结选择和producer薄回执。

旧 `test_bus_snapshot_rebuilds_message_search_from_thin_body_reference` 的 inline正文断言在干净 main ace2b960 同样失败；仅将测试适配既有 `@body`/content_search 合同并验证分块后的实际检索，没有借机改产品搜索。C盘最初临时目录不足的一项测试已转D盘完整重跑通过。Ruff F/E9 与 diff whitespace 检查在发布前执行。

## 发布与回滚

使用 `scripts/ops/storage_lifecycle_release_20261111.py`：逐角色校验当前镜像与源码，再以各角色实际镜像为底构建 COPY 层，保留已有业务补丁、环境和依赖。先构建；排空已接受模型任务后替换Worker，不取消模型任务，不改并发。通过现有 release health/promote/state 管理现役及前驱组合。源码 commit 与实际角色 hash 见最终生产回执。

D3 表示升级期间保留 `.d3-native-inputs-disabled`，待所有reader/writer上线后激活；旧镜像回退前须排空D3 writers，再在新镜像执行：

```sh
python -m doxagent.workflows.codex_document3.state_v21 --inline-for-rollback /data/research/research.sqlite3
```

该命令开启禁写新格式门并逐行恢复 inline，之后才能恢复旧镜像；已经退休的重复输入不复活，权威成果继续保留。此要求也写入宿主 release state。

## 生产执行与验收（11月11日）

本轮已完成 R1/R2、A1、D1/D2、T1、I1/S1/B1/B2及发布/容量规则的代码实装与对应角色发布；最后的I1多控制库归属修复已于06:46独立发布。**这不是整个方案的长期验收全部通过**：Read/Acquisition 仍在追平历史积压，3份独立Document2快照及未放弃恢复责任仍有未关闭项，24h/72h/7d自然窗口和完整前端因果性能验收尚未完成。

### 生产版本与发布边界

- 核心共享修复 `79caa5cc`，Read吞吐/父子owner收尾 `c6949151`，宿主生命周期与容量规则 `5fc0f60e`。代码以精确提交构建，10个目标角色的所有选定文件与各角色冻结提交逐项SHA-256比对，无不一致。
- 实际发布角色：Scheduler、Initialization、O4、CDECR Executor、Codex Worker、Read Maintenance、Transport Maintenance、Projector、Message Collector、Site Access。吞吐收尾仅替换Worker/Read Maintenance，其他角色保留已验收镜像。并行W3发布后Worker基线由3ef52d更新为43233a，逐文件核对本轮选定文件无漂移，保留其全部新业务补丁；同步合并main be469c53。取消路径候选4a07b15c构建后经复核撤回，未替换43233a。宿主脚本独立按Git原始字节安装并校验，避免Windows换行造成哈希歧义。
- 复用当前各角色镜像依赖层，不恢复旧整树覆盖。合并并保留 main 的 W3 原Job恢复/ordinary429/Ready、准入隔离、D3 frozen WAIT_WORKER、微信MCP等已存在业务能力。自然业务已重新提交模型Job，未改变模型并发、提示词、交易规则。
- Worker替换前暂停模型生产者、等待已接受任务自然结束；没有取消在途模型工作。生产者与Guardian均恢复。API/Web、交易Executor/Delivery及Chrome Supervisor未因发布替换镜像。
- 首次Site镜像因构建上下文权限过严，非root用户读取源码失败，健康门禁回退该角色。后续COPY显式0644，并在镜像原用户下编译验证后重新发布成功。此窗口Site出现暂时不可用，不能把发布窗口算业务连续性能验收。

### 实际回收与退出证据

| 对象 | 实测结果 | 口径 |
| --- | --- | --- |
| Bus单库物理压缩 | 主库28,021,735,424→13,358,657,536字节，约26.10→12.44GiB；业务主键摘要、CDC epoch/head/retained_floor一致，quick_check=ok，checkpoint=[0,0,0] | 主库减少约13.66GiB；没有删新的业务行 |
| 同一压缩操作窗口根盘df | 可用46,650,757,120→62,285,328,384字节，净增约14.56GiB | 含同时自然写入/缓存/日志变化，不能与上行相加 |
| 最终容量取样 | 06:54:48根盘约54.5GiB可用、使用86%；正在自然写入 | 不是70GiB目标已经达成；此前40.105GiB应急清理属于上一阶段，不计本轮收益 |
| Read真实GC | 收尾镜像74轮中68轮有已提交进度；object_history退出47,653行、contributions_history47,652、metric_buckets_history47,652、changes47,776、commits47,276 | 取样期间的提交计数，非物理GiB；6轮零提交和剩余预算中断仍如实保留 |
| Acquisition | 分阶段提交无错误；一次receipt退出33 unit-hour、512 physical候选；最老eligible细账从审计的10/30 18:00推进至10/30 22:00 UTC | 更老的SCHEDULE合法前驱仍在；不能拿全表min(dispatch_at)当退出水位。旧于7d的摘要会按原规则立即退出，unit_hours暂为0不等于未归档 |
| D3定向存量结算 | 8个独立已发布run，过程JSON合计76,267,943→5,101,358字节，约缩减93.3%；16个父/子目录回执退出2256个文件，退出前分配量88,678,400字节 | 同一D3父/子receipt含相同retirement证明，按8个唯一run去重；JSON瘦身形成可复用页，不冒充df收益 |
| 普通workspace追平 | 另4轮候选扫描完成，无错误，退出578个文件、退出前分配量5,234,688字节 | 保留具体引用文件、唯一输出/SQLite证据与W3目录 |
| Runtime | 自然owner已提交多份frame瘦身、process结算和有引用分支压缩；保留base revision/冻结选择/精确版本哈希 | 有些极小分支转换后反而略大，未报为回收收益；未删除仍有效版本来制造目标 |
| Session | 17个eligible，清退16个终态线程，无错误；退出文件原分配量105,046,016字节，保护13144个会话 | 约100MiB，不与df重复相加；活跃模板、真实父子恢复链保持 |
| 镜像 | 现有release owner清退16个无运行/回滚/模板/Compose引用的本次临时tag，无错误 | 标签包含共享层，不以展示大小求和；不做volume或全Docker prune |

Read仍约32.36GiB。旧技术current退休会先生成有界MVCC前像，等待协议窗口后再退出，因此短时object_history总数可能上升；**已恢复提交不等于过期backlog已收敛**。本轮大规模eligible精确COUNT在4s探针预算内未完成，不编造下降比例。水位查询和候选查询已在生产确认使用覆盖索引；后续按已有owner receipt与自然窗口核对新增/退出速率。没有再次全量重建Read或提前删除有效pin。

Bus采用SQLite自身原子VACUUM，避免不一致cp和在线文件替换。操作窗口02:45:51–03:04:10（约18m19s），其中VACUUM约630s，其余用于停止/对账/完整性检查及恢复。仅暂停该库5个采集/传输owner；交易执行/EXIT、API和独立浏览器服务继续运行。期间采集与投影有滞后，必须从自然稳定窗口中排除。未同时压缩第二个大库，也未给可再生Read再造全量备份。

### 新增发现并已修复的持续退出缺口

1. Read旧poll头按表达式全局排序仍超预算，改用既有(kind,id,ticker)索引先取候选、持久游标，再按业务时间退出；MVCC子批限16，避免吞掉其他阶段预算。已提交阶段不因尾部deadline耗尽而一律降低吞吐。
2. D3发布run、无直接Job的主工作区、分片工作区，以及Runtime O2/O3原本使用不同ID。现在按真实父流程结算；短writer内复核兄弟Job与初始化人工恢复责任，发布家族退役fence，锁外退重复文件。主run的JSON也随同结算，不能只清子目录而漏掉prepared大副本。
3. Runtime前端reader虽早已跳过inputs/receipt，producer还会捕获它们。现已升级runtime_tasks捕获触发器：状态仍投影，大过程载荷保留于原业务owner。
4. 显式Native owner退出使用现有队列，但继续校验其他引用、快照lease及5min隔离窗口，避免等待普通无主put的7d宽限，也不会误删共享输入。
5. 宿主CPU PSI avg60现场约56.86–59.46，高于旧10阈值，原host owner会长期整轮跳过。移除这条全局早退，沿用每小时Nice=10/IO优先级7；仍保护实际引用和正在构建的资源，不增加高频扫描。容量现在识别活动Read alias，并保留超时及跨日的last-complete分组样本。45组每轮2组轮转，单轮不假装全部扫完。
6. journald显式4GiB上限、8GiB空闲保护及14d诊断窗口，沿用systemd日志机制；Docker原有轮转继续。没有给规模很小、可能含唯一采纳结果的旧测试树再加全局按天删除器。普通pytest临时数据按其测试owner保留机制退出，唯一采纳/审计输出保留。

### 仍未关闭的事项与明确保留

- **I1补充审查纠正了主库单一视角。** BE/INTC/IONQ/COHR独立刷新各有`/data/ops/*/control.sqlite3`，共享同一Worker数据卷。审计取样的完整只读清单849份，846份可唯一对应5个实际控制库；余下3份都属于`d2ws-652706d70fc0dbd8253b4bd894251b8a`，没有可靠initialization owner，继续保留。此前主库单查的293/417/123分组不能再解释为真实owner遗失，已被多库证据替代。新补丁按execution优先、显式owner/最长前缀补足，保存具体控制库；当前Runtime、主生产头/必要前驱、跨库rerun和真实manual-resume保持保护。独立ops本地成功头不会永久保护过期过程快照。`ledgers-merged.xml`31项回归通过，生产新代码进程内只读预演4.74s、10个候选/22保护/0未知/0错误（仅当前页，不能外推全量）；最终187d79f3候选已在用户要求优先发布后，于06:46上线；以下最终回执替代此前待发布状态。
- 未发布且没有明确放弃恢复责任的失败run继续原恢复窗口；没有将所有FAILED自动按72h删掉。不能把Job cancelled直接当作D3业务弃权：Worker close也会写cancelled。收尾候选4a07b15c因此撤回且未上线；无权威交接的D3 prepared/唯一草稿仍保留，明确放弃原现场恢复的owner事件尚未自动补齐，普通FAILED不隐含放弃恢复。
- 原计划的24h、72h、7d自然业务验收，以及固定窗口Overview/消息/Research/Policy/Event/Case的完整前后p50/p95因果对照未完成。不能把磁盘释放换算成前端加速，也不能用本地测试替代长期吞吐收敛。
- published_documents/staged/PolicySet唯一定义与精确历史版本继续保留。未把5.04GiB发布文档全部视为垃圾；不新增资产平台，也未触动明确暂缓的W3完整研究资料/Context重复物化。

### 回执位置

- 生产 `/var/lib/doxagent/releases/storage-lifecycle-79caa5cc00b2/`、`storage-lifecycle-c6949151fc9a/` 的build/release回执；private manifest包含生产配置，留在宿主0600，不提交。
- 单库压缩、host reconcile、Session和定向workspace操作记录位于同一release根；定向helper均为有限进程，已退出，不留新的cron/模型worker。
- 本地 `D:/DoxStorageLifecycle20261111/eval/storage_lifecycle_20261111/` 保留JUnit、源码哈希、原始只读/操作回执与初始化owner清单。公开摘要和选定源码hash随分支提交；原始大数据与测试临时目录不混入提交。

本轮没有宣称“以后磁盘完全不增长”或“全部历史已清空”。已上线的各owner负责继续追平和维持退出；唯一业务内容、必要历史及暂缓W3的成本仍需在自然业务窗口中核算。无需为等待观察另建Codex轮询任务。

## 并行发布协调

用户告知另一Codex会话正在发布、稍后恢复。本会话停止继续切换/重启容器与暂停生产者；当前窗口内仅做只读核验和无容器切换的候选构建；窗口结束后再争取Worker自然空闲窗口。已终止本会话未获安全窗口的额外Worker候选部署。远端工作副本changelog有其他未提交修改，未覆盖；生产以各角色实际镜像与冻结源码hash为准。

### 05:00补充状态

已合并main至a0a249d9，保留W3 Luna Fast及并行业务更新。精确文件候选源码cfe7b649，以当前Worker实际镜像14121db49ff6为底，仅覆盖initialization_owners/snapshot_retention/session_retention三个文件，候选7172bb5619e5。当前Worker仍有5个在途Job，未替换。采集角色aff392a3e876的Acquisition maintenance/store与当前提交逐字节一致，明确不为它额外切换容器；构建时产生的同源码Collector候选不发布，由既有48h候选生命周期退出。

最新main业务合并（含W3 JSON解码、Luna供应商配置）的交叉收尾检查：`merged-closeout.xml`为67通过、2跳过，未发模型请求；与此前各组重叠，不合计为独立总数。

### 发布窗口解除后的最终Worker发布

W3会话已通知窗口结束（Scheduler62d6be3311df、Worker14121db49ff6，主机工具/main47117b71）。新增d08df6dd操作门禁：复用既有W3 Effect结果消费空闲检查，并在确认Job空闲后，以短SQLite writer admission fence覆盖停止旧Worker的步骤；新Job必须先持久化再派发模型，因此堵住检查与停止之间的新受理竞态。不修改运行时准入策略/业务并发。Job繁忙、Effect繁忙、并发提交及锁释放等合计10项发布边界回归通过。

最终候选从最新实际Worker与当前配置重新构建，仅三份I1源码，保留Scheduler和其全部更新。有限运维进程只暂停已枚举六个非W3生产者，保留现有模型和实时Scheduler；长模型按1800秒间隔等一批自然结束，最后最多5分钟选择W3消费完毕的空闲窗口。失败或超时均恢复原来在运行且未被其他发布替换的生产者；不留daemon/cron，不取消Job。实际执行结果将在完成后补充。

### 首个有限发布窗口的实际结果与角色边界修正

05:44:57–06:20:07，d08候选未发布，六个生产者均恢复。原3个D3 Job全部SUCCEEDED，无取消；窗口末次仍有1个W3 Job，未找到同时满足Job与Effect空闲的时刻。回执没有单独证明Effect是唯一阻塞，不能把整次未发布都归因于Effect门禁。

继续审查明确：Scheduler停止会切断结果消费者，因此保留其Effect空闲检查；单独Worker停止时Scheduler继续运行，成功Job/请求/receipt都在持久卷。既有HttpCodexWorkerClient对传输/5xx按原Job重试，未知lookup由W3PreparationError回到PENDING_RETRY且不换generation。故187d79f3将Effect门禁限定于实际包含Scheduler的发布，Worker仍必须全部Job空闲，并以短SQLite writer fence阻止检查后新模型受理。新增503/500/连接中断的成功结果恢复测试，无POST重发；发布/Provider恢复组合32项通过。未修改任何产品业务恢复、模型并发或重试参数。

06:34:01启动第二个有限窗口，PID3608905，候选64c3564fce85，当前仅暂停原六个非W3生产者；约07:04检查已有模型自然结束。此进程在界面权限切换/命令中断后仍存活，恢复工作时已核对并未重复启动。Scheduler62d6be3311df和原Worker14121db49ff6持续运行，最终结果待回填。

### 最终优先发布：06:45:58–06:46:54

用户明确改为“现在中断模型任务优先发布”。先向此前等待进程PID3608905发送SIGTERM，等待其finally恢复六个生产者并退出，再由唯一新进程接管。为避免再受理模型，暂时停止同一Scheduler容器及六个非W3生产者；检查队列时已经为空，取消API实际调用0次，未据此宣称先前所有时刻都没有模型任务。随后由既有canonical release锁、短Job提交写锁、健康门禁和promote完成Worker发布。未增加永久force模式、运行时队列或模型限制。

最终Worker镜像`64c3564fce85b3f24d1b44330b3aa5cceaa7a7a7ed919aec2ef2cdf7ffc98f56`，三份I1源码来自`187d79f3896d926d4b750a6aafb58a4456f6e53b`；基底为此前`14121db49ff6`，保留W3 Fast、Luna/Provider和其他业务补丁。Scheduler仅短停/恢复，镜像仍为`62d6be3311df`、原容器ID不变。七个暂停角色均按原不可变ID恢复，Worker健康检查通过；没有覆盖浏览器身份、发布成果、金融状态或恢复输入。

生产回执：`/var/lib/doxagent/releases/storage-lifecycle-priority-worker-20261111/window.json`；canonical源码/镜像/health回执：`storage-lifecycle-187d79f3896d/`。此次约56秒发布及此前有限生产者暂停窗口均不计入自然稳定性/业务性能验收。所有等待/发布helper均为有限进程，不留后台daemon；生产常规退出仍由原组件和hourly release owner执行。

### 最终只读验收与剩余观察边界

北京时间06:54:48，三份Worker源码SHA-256均与187d79f3的构建回执一致；所有本轮相关常驻角色在运行，Worker/Collector/Site/CDECR健康检查通过。Scheduler和其余临时暂停的常驻生产者均已恢复。IONQ/COHR两个一次性刷新容器恢复启动后分别于06:46:58/59以exit0退出，无OOM、无错误，不为制造“全为running”而再启动已退出的一次性命令；其业务run状态不等于整条初始化已完成。

- 新Worker首轮自然owner（2026-11-10T22:51:36.313149+00:00 UTC）已读取全部5个控制库，实际退出15份过期快照、保护17份，当前候选页unknown=0、errors=[]，另回收11个无引用内容。当前页0未知不能代替全量审计，历史3份独立Document2归属仍保留。Workspaces同一轮有10份结算回执、无错误；不与此前定向样本重复累计收益。
- Acquisition最新回执归档12个unit-hour，候选小时2026-11-02T08:00:00+00:00，各退出阶段errors=[]；相较审计10/30已继续推进。按正常owner滚动，不额外重跑全库。
- Read最新已提交退出：`{"changes": 2000, "commit_retention": 892, "commits": 2000, "content_search": 0, "contributions_history": 0, "cursors": 0, "definitions": 0, "metric_buckets_history": 2000, "object_history": 1500, "poll_current": 96, "views": 0}`；尾部错误/预算中断如实为`[{"error": "interrupted", "stage": "poll_current"}]`，下一阶段`poll_current`。提交与游标可见不等于全部过期积压已清零。
- 最终可用54.5GiB，约70GiB运维目标仍未达成；不能将逻辑行数、快照数量、CAS条数加成物理GiB。宿主release timer为active，脚本SHA仍为`8db638ac724f6e4984bbb3b8cd75ad19534c813ad05f00e4231f27ba8d36c3ab`；未留下本轮发布/等待进程或新增cron。普通未发布候选由既有48h候选生命周期退出，无需全Docker prune。

开发和生产发布收尾已完成；24h/72h/7d自然业务观察、完整前端前后因果性能验收，以及没有业务放弃事件的恢复责任仍单列，不宣称长期治理已经全部验收。W3完整研究/Context存储架构继续明确暂缓。后续不为等待长窗口而继续扩张开发或建立高频Codex轮询。
