# 2026-11-14 大块资源清退与膨胀机制记录

本轮依据 `root-disk-value-review-and-cleanup-plan-20261114.md` 执行。目标是恢复可持续运行的生产余量，重点处理大块低价值过程数据，不为几百 MiB 的边角缓存投入过多时间。

操作证据：`artifacts/audits/disk_cleanup_execution_20261114/`。以下收益区分实际释放、候选与尚未处理；不把清理临时效果当作长期治理已经完成。

## 1. 执行与收益

初始根盘：总容量 400,937,631,744 B；已用 390,466,011,136 B；可用 9,491,308,544 B，即约 8.84 GiB。

北京时间 02:25:44 验收：已用 310,383,546,368 B，可用 89,573,773,312 B，约 **83.42 GiB**，`df` 使用率由 98% 降至 **78%**。相对本轮初始，**根盘净释放约 74.58 GiB**。此数包含并行批次及正常业务写入，以整盘 `df` 差额为准；不是把各路径登记字节机械相加。

| 批次 | 操作 | 结果 |
|---|---|---|
| A1 | 宿主机 Reuters `BrowserMetrics/*.pma` | 已删除 2,096 个超过 48h、没有打开或映射的遥测文件；释放 8,793,235,456 B，约 8.19 GiB；身份目录其他内容未删除 |
| A2/A3 | 正式退役的 Maintenance Workspace 过程镜像、发布正文副本 | 已完成，约 13.87 GiB。只有既有退休 fence、无活跃同族 Jobs、最终发布文件可读且哈希吻合的对象进入 |
| A4 | Read 空闲页物理压缩 | 34,748,481,536 B 原分配变为 13,488,500,736 B，切换时实际减少约 **19.80 GiB**；原库和临时库均已退出 |
| B1 | 初始化快照与共享内容池 | 共退出 **1,638 份**已完成任务的预节点副本；其中漏管旧快照 1,036 份直接释放 **12.46 GiB**；另清理无链接共享内容。没有将同一 inode 的多份路径重复计数 |
| B2 | 补办已结算 Maintenance Workspace 退役 | **770 个工作区**；原有收集器登记删除路径 allocated 合计约 **8.38 GiB**。这是路径登记量，部分文件可能共享块，净收益统一看整盘 df |
| B3 | 新退役工作区再次消除过程镜像和发布副本 | **467 个工作区**，额外确认释放约 **3.02 GiB**，与 B2 的输入/重复 output 集合不同 |
| B4 | Research 已消费、低价值传输重放收据 | 清退 **24,448 条**；形成约 **4.97 GiB 库内空闲页**，供后续写入复用；没有算成操作系统释放空间 |
| B5 | Acquisition 过期原始周期与物理请求 | 清退 **337,903 条周期、186,880 条物理请求**；产生约 **0.52 GiB 库内空闲页**。补齐现有代码已定义、现场缺失的 `captures_run(run_id,status)` 索引 |
| B6 | Bus 已 ACK 的旧 CDC 重放前缀 | 按原 7d 规则清退 **767,500 条**；形成约 **1.48 GiB 库内空闲页**，没有删除标准消息、正文及当前来源配置 |

发布副本保留原路径及 manifest：在同一文件系统内以硬链接共享不可变正文，不删除独特最终产物。过程镜像退役保留哈希与小型退出收据，不额外复制完整备份。

第一轮 A1—A4 的独立物理对象测量合计约 **41.86 GiB**，超过原方案约 40.13 GiB 候选估计，主要因为 Read 重建还消除了部分页内碎片。不要再把该小计叠加到 73.83 GiB 整盘净释放上。

Read 最终持久作业的维护窗口为北京时间 **01:38:44—02:10:01**；此前一次压缩受聊天中断影响退出，没有完成切换，原库及服务已自动恢复，造成了额外维护时间。第二次改为服务器独立 systemd 作业，日志不依赖聊天连接。最终 36 张表的行数、schema、generation、公开进度和源 checkpoint 一致，`quick_check=ok`，随后原路径切换并重启 Read API/Projector/Maintenance。Worker 和交易执行容器未因本轮清理重启。

切换后 API `/readyz` 返回 200，查询池全部 READY；API、Web 与原有带健康检查的生产容器正常。10 个 Ticker 的读取投影存在，Bus/Runtime checkpoint 已继续推进；`gaps` 仍是切换前的 17,206，未因本轮新增。这里只证明切换与基本读取恢复，不声称已经完成前端性能优化。

## 2. 已确认的数据产生与积压机制

### 2.1 Maintenance Workspace / `runtime-maintain-*`

产生链：每次维护运行写入完整 context、`artifacts/document3/v21_state.json`；发布时写 artifacts/releases、output/final；Workspace.publish 再 copyfile 到 published。一次维护可以持有同一正文多份及完整过程状态。

退出缺口：原 GC 主要清 context、attempts input 与 output/final；完成标记没有覆盖过程镜像和 artifacts/published 正文重复物化。相同 owner revision 的 complete 标记使后续扫描跳过，遗漏变成长期留存。部分家庭以 `*-o3` 作为自身 owner，仅该完整 run_id 有退休 fence，不能错误要求另一个不属于原生命周期的根 ID 也必须存在 fence。

本轮取舍：已有正式退役且结果保留的任务，放弃完整过程视图和原地续跑便利；最终正文及引用路径继续可用。还发现一批已在 11 月 12 日完成、D3 有 settlement、Runtime MAINTENANCE 已 SUCCEEDED 的运行，工作区仍未退役。本轮按明确候选调用现有 collector，沿用引用保护、任务重验和退休 fence，集中处理 385 个同族运行对应的 770 个工作区，再处理原 collector 未覆盖的镜像/发布副本。

线上确有独立 resource_maintenance 模块在运行，最近仍产生成功退出回执，不能归因于“维护进程完全没启动”。更准确的问题是全局证据/路径扫描成本、候选游标和限量推进造成积压，加上完成定义遗漏大文件。后续应把镜像与正文副本纳入同一“退出完成”定义，补扫旧完成记录，并测量实际退出吞吐。

代码：`codex_worker/workspace_retention.py`、`workspace_store.py:306`、`workflows/codex_document3/orchestrator_v21.py`。

### 2.2 初始化 `.init` 与 `.init-contents`

每个节点执行前冻结整个工作区，为 internal rerun 保存 pre-node snapshot。初始化工作区快照使用不可变内容池及硬链接，已减少同内容的重复物理块；但源工作区随节点推进不断增加输入、产物和过程文件，新版本仍会产生新块。

退出保护包含非终态、manual_resume、当前生产采纳、主账本上一成功版本、正在内部重跑使用的快照。RUNNING/manual_resume 若在一次性 dispatcher 退出或版本替换后未结算，会阻塞整份快照退出。各 Ops 账本需要和当前 Runtime 的生产采纳关联，不能只依赖自己本地的 active head。

**本轮新发现的主漏洞是历史 owner 回填缺失。** 初看 owner 文件中有 605 份 LIVE 快照，进一步枚举实际目录发现另有 **1,070 个没有 owner 文件的目录**。现有 collector 只遍历 `.init-owners/*.json`，这些目录从未进入候选选择。

使用 `sha256(workspace_run_id + ':' + execution_id)[:32]` 对目录名进行确定性反查：306 个候选工作区、13,998 条历史执行，成功认领 **1,067/1,070**，不是按年代猜测归属。大部分属于早已 SUCCEEDED 的 ONDS、CEG、COHR、NBIS、MU、INTC、BE、RKLB、SNOW 初始化；它们是旧节点的完整恢复拷贝，不是当前生产结果。

本轮先退出 538 份登记历史、保留每源最新一份；复核价值与共享块后，继续退出 64 份最新预节点副本。对应四次初始化/研究刷新均已完成、无 manual-resume 和活跃同族 Jobs，也没有正在使用的 internal rerun pin；最终研究产物及采纳数据独立保留，故不再为旧节点原地重跑保存完整副本。其后为可确定归属且成功完成的旧目录回填 owner，正式退出 1,036 份漏管快照。

退出使用既有 RETIRED 标记与 tombstone rename，后续不能按旧身份重新 fork。共享池只清没有快照/产物硬链接的缓存项；不存在活跃初始化物化时，再清理老于 24h 的孤立缓存。不是对生产 Native CAS 按年龄直接删除。

最终 `.init` 剩 37 个目录，包含旧失败/人工恢复及本轮未完全验明的少量对象；本轮没有将它们认定为永久不可清理。已经取得大块收益后，不再为少量复杂遗留继续扩大操作面。

代码：`codex_worker/workspace_store.py:snapshot/_copy_atomic`、`snapshot_retention.py:protection/collect`。

### 2.3 Research

Research 同时保存完整发布文档、D3 运行状态、原生大内容及采纳资产；Workspace 又保存文件形式的发布正文和过程镜像。上轮已量化 published_documents 16,121 条、约 9.53 GiB JSON 逻辑字节；D3 运行状态约 1.04 GiB，说明之前的状态瘦身有效，但不等于全部过程副本已退出。

需要保留当前采纳、独特研究成果、有效 Case/交易引用的准确证据；低价值的是重复正文、旧过程调试状态、未采纳且已无复用价值的历史物化。不得把 9.53 GiB 逻辑字节和工作区物理清理重复计入收益，也不得直接删除 repository 正在读取的唯一正文。

本轮补充了主库组成，均为逻辑字节，不等同物理回收量：

| 数据 | 数量 | 逻辑体积 | 价值判断 |
|---|---:|---:|---|
| `v2_source_receipts` | 38,927 | 约 7.96 GiB | 已投影后的传输重放副本，不能和当前研究正文等值保护 |
| D3 staged payload | 3,137 | 约 2.96 GiB | 已编译 PolicySet 等版本化业务结果，按有效引用保留 |
| D3 staged receipt | 3,137 | 约 3.07 GiB | 与发布/交接读取有关；有重复物化嫌疑，但不能当作上一行 CDC receipt 直接清空 |
| D3 tasks | 4,716 | 约 0.165 GiB | 收益相对小，优先保留结果/状态，不为其单独重构 |
| 来源 captured_text | 2,547 | 约 19.7 MiB | 成本很小、证据价值高，保留 |

Research 传输收据最老时间原本恰在 7d 附近，说明既有 7d 退出并非完全无效，**但留 7 天完整正文重放代价很高**。本轮对已 ACK、无 pin 且可从当前业务表恢复的收据，复用原 expire 逻辑，仅将这次候选窗口设为 48h；保留真实当前时间、消费进度、例外 pin 与 SOURCE_SNAPSHOT_REQUIRED 边界，清退 24,448 条，约 4.97 GiB 空页可复用。

这是有界批次退出，不宣称所有余留都已收敛到精确 48h；后台默认策略仍为 7d，后续会有回长风险。持久策略应在后续正式修复中统一。本轮没有为了将这约 5 GiB 立刻返还根盘而再暂停 Worker、复制并切换整个 Research 主库；已恢复足够根盘余量时，这种额外维护成本不划算。

代码：`codex_runtime/repository.py:save_published_document/get_published_document`、`workflows/codex_document3/state_v21.py:retire`、`v2_read/source_native_gc.py`。

### 2.4 Runtime / Event Library 运行分支

每次维护创建运行分支并携带 Event Library 历史、delta/CDC 等过程数据。Case、activation、reference delivery 和历史任务可继续引用准确版本；引用了一个版本不等于需要永久保存整条分支的全部过程。

已有退出逻辑：成功分支 48h、失败分支 7d；无引用退出整个分支，有引用压缩至必要历史版本。上轮现场有 297 项 pending，重压缩因 active realtime 连续推迟。现有引用重验和锁有价值，但“只在全局清闲时清理”会在常态业务下形成永久饥饿。

本轮不改变活跃 W3 的输入物化架构；旧过程 owner、终态分支和过时恢复责任仍可独立退出。后续应让已有维护周期保证有界推进，不引入新的全局管理框架。

### 2.5 Bus / Acquisition

采集每轮记录 schedule、logical cycle、physical requests 和 capture，正文/业务证据另有保存位置。高频轮询下，即使单条不大，每日也会持续产生大量账本行和请求明细。

现有规则已经是原始明细 48h、小时摘要 7d，问题不在缺少天数配置。`_retire_hour` 在 0.5 秒读预算内定位候选、加载一整个 binding/unit/hour 的 samples、schedule、physical JSON 并聚合，再删除最多 512 条；入口长扫或整小时展开超时会使这轮归档无法提交。physical 清理又受尚存 cycles/captures 引用保护，前面的归档阻塞会延伸成后面的物理明细积压。

上轮 retention 记录 hours interrupted，unit_hours 当时为空；captures 的 ACCEPTED payload 已是薄 `{}`，继续压缩这类 payload 没有显著收益。应处理实际积压的 cycles/physical 明细和过期保护，不为此再设计一套全局存储服务。

本轮进一步确认：physical_cycles 约 **586 万条**；7d 以前仍有约 **218 万条 cycle、126 万条 physical**。现有代码包含 `captures_run(run_id,status)` 索引，现场数据库却没有；而 physical 退出恰要逐个检查 pending capture，缺索引会放大探测成本。本轮补齐此既有索引，保持业务调度和并发不变。

对已经超过小时摘要保留期的终态旧 cycle，直接退出原始过程，无需再制造已无保留要求的小时归档；SCHEDULE、DISPATCHED 保留。physical 仅在不存在 logical cycle 引用且无未接受 capture 时退出。最终删除 337,903/186,880 条，产生库内空闲，仍有历史积压未全部走完。没有因清理再做一遍 18 GiB 全库复制并中断采集服务。

Bus CDC 还存在单独的遍历效率问题：无全局 pin，当前 Read 已消费到约 1,143 万，最老 outbox 却仍在约 953 万（10 月 31 日）；退出 cursor 位于约 1,136 万的年轻尾部。小批扫描遇到未到龄记录就结束当轮，但 cursor 仍要慢慢绕回旧前缀。**没有 pin 不等于自动 GC 能及时追上增长。** 本轮重置的是退出扫描 cursor，不是消费者 checkpoint、源 head 或 replay floor，并按原 7d 规则定向补扫旧前缀；120 秒有界补扫退出 767,500 条，约 1.48 GiB 可复用空闲页；旧前缀从 10 月 31 日推进到 11 月 6 日，仍有积压，未将阶段结果当成整个 Bus 清空。证据为 `remote-evidence/b-bus-replay.log`。

代码：`message_bus_v2/collection_plane/maintenance.py`、`store.py`、`observability.py`。

### 2.6 Read

历史 GC 的 SQLite DELETE 释放的是库内页；如果没有物理压缩，主文件仍占用原分配空间。上轮 32.36 GiB 主库里约 18.06 GiB 已是 freelist，不能再把这些空页当业务历史。

旧 `maintenance.compact` 包含迁移/内容外置，并要求源库 3 倍空间，不适合只回收空闲页。此次采用冻结 Read 写入、纯 VACUUM INTO、验证元数据/行数与文件一致性后切换；保留原库至验证通过，再退出。原生内容目录沿用原位置，不复制整份内容库。

压缩还提供了一条后续设计基线：当时 `object_current` **457 万行**、`object_history` 仅 **4,383 行**；另有 entity_watermarks 708 万、contribution_watermarks 492 万、definition_ref 112 万。剩余规模不能继续全部归咎于历史版本表。

当前行按 kind 聚集在 fact 约 58.3 万、policy 48.6 万、policy_detail 43.1 万、policy_admission/catalog 各 42.6 万、policy_change 33 万。应进一步核实版本/分支身份下的重复投影、同一业务对象的多种物化和实体水位退出。这里记录了量化现象，不把相近数量直接认定为可任意删除的重复业务事实；后续应从真实前端读取与精确历史引用设计最小存储形态。

### 2.7 宿主机浏览器与 SDK

Reuters Chrome 在独立宿主机 profile 中写 BrowserMetrics，Site 的 browser pool GC 没覆盖这个确切路径；遥测每个约 4 MiB，本次积压 2,096 个，形成 8.19 GiB。必须补覆盖路径，而不是误删登录身份。

SDK 长期保留 debug/info 日志、完整工具对话、rollouts 及 thread_history 派生副本。已有会话退出又依赖终态 owner 和全局空闲；W3 豁免使低价值过程对话长期在线。最终结果、当前父链/模板与旧完整调试重放应分开保留。

## 3. 后续应优先修的少量责任

1. 快照 owner 的历史回填和“目录存在但 owner 缺失”发现；成功任务的预节点恢复责任应独立于当前业务采纳终止。
2. Workspace 完成定义覆盖镜像、重复发布和过程输入；已完成标记也要能补扫遗漏内容，候选扫描吞吐要追得上创建量。
3. Acquisition 定位具体 hours 超时点，按有界范围推进；补齐既有 schema 的缺失索引。不要依赖不断延长全局读写超时。
4. Source CDC 优先扫描确已过期的前缀、处理年轻尾部绕行；评估将 Research 重放持久收窄到 48h，保留 snapshot 恢复与真正的例外 pin。
5. Read 采用纯压缩回收大块空页，独立审查当前业务投影与水位表的规模；不为治理空间引入新的全局资源平台。

本轮没有重写应用架构或重建镜像。除补齐既有 Acquisition 索引外，主要是按现有 owner/消费/引用机制进行集中退出。普通重复副本和缓存没有另做大备份，必要成果、业务账本和浏览器身份仍保留。

达到约 83.4 GiB 可用后，停止继续追逐零散缓存、少量难确认旧快照及需要停 Worker 才能兑现的数据库压缩。SDK 会话、旧恢复包、Runtime 分支仍可进一步治理，**不是判定它们不可清理**；本轮按收益与操作成本取舍结束。空间恢复不代表长期增长机制已经全部修复，上述责任应成为后续优化的直接依据。
