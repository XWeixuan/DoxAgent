# 根盘空间全景、保留价值复审与清理决策

日期：2026-11-14；对象：`doxagent-sg` 生产根分区。

本轮仅执行低优先级全盘扫描、只读数据库查询、文件元数据与抽样哈希核验，没有执行删除、数据库压缩、服务重启或代码修复。此方案供下一轮执行使用。

## 1. 决策

**应当清理，而且范围应明显超出构建缓存和浏览器缓存。** 最大问题是在线系统同时保存了大量任务过程、恢复镜像、发布副本和历史分支；其中一部分已经完成正式退役，却仍然占用独立物理块。“业务目录”“BUSINESS 类型”“有引用”“曾经可以恢复”均不能单独成为永久保留理由。

建议先完成约 **40.13 GiB 的有量化依据的候选批次**，再处理日志、旧会话、过期任务、历史分支和采集过程数据。40.13 GiB 是候选的物理占用与空闲页测量合计，不是已经释放量；其中发布副本尚需执行时全量哈希校验，Read 压缩结果也应以实际 `df` 为准。

应主动接受两个取舍：

- 已完成并被后续版本替代的任务，可以失去完整过程回放和原地续跑能力。保留结果、必要证据、版本关系及精简结算记录即可；需要重新研究时开新任务。
- 普通诊断、浏览器遥测、重复物化和已过期传输明细不值得购买长期在线磁盘。它们无需为删除再制造一份大备份。

需要继续保留的是当前可用业务结果、当前生产与明确回滚点、真实仍有履行责任的交易/持仓/订单记录、必要证据的准确版本、正在使用的身份状态及确实仍在推进的任务输入。这些保护应落在具体对象，不能扩张为保护整个历史目录。

## 2. 全盘统计与口径

### 2.1 容量与核对

- 根盘 `df` 总容量：400,937,631,744 B，即 **373.40 GiB**。用户所说的“380+GB”与 GiB 单位不同，不能直接混用。
- 全盘扫描结束时：已用 **362.89 GiB**，可用 **9.60 GiB**，`df` 显示 **98%**。
- `du -x -B1 -d8 /` 实际归集 **362.92 GiB**，与 `df` 已用仅差约 35.5 MiB，约 0.01%；没有几十 GiB 的无法解释缺口。
- 扫描时间为北京时间 **00:15—00:41**，生产持续写入；后续复核可用约 **9.39 GiB**。这是动态区间扫描，不是冻结瞬间。任务目录计数在不同采样时刻会有小幅差异。
- 扫描唯一错误是 Acquisition 的一个 WAL 在遍历期间消失，符合正常 checkpoint/文件轮换行为；根目录总量已获得。不能因退出码 1 将本次整体统计误判为失效。

下表比例以全盘 `du` 已归集的 362.92 GiB 为分母，全部行互斥。数据库内部逻辑字节、父目录与子目录、镜像展示大小不再额外相加。

| 互斥部分 | 物理 GiB | 占全盘已归集空间 |
|---|---:|---:|
| Workspaces 工作区 | 99.43 | 27.40% |
| Runtime 业务运行 | 65.21 | 17.97% |
| Read 在线读取 | 35.14 | 9.68% |
| Bus / Acquisition | 34.24 | 9.44% |
| Research 研究库 | 33.94 | 9.35% |
| Site 身份与浏览器卷 | 21.48 | 5.92% |
| Codex Home / Sessions | 19.25 | 5.30% |
| containerd 镜像与运行层 | 15.13 | 4.17% |
| 宿主机 /home | 12.22 | 3.37% |
| 宿主机发布及恢复目录 | 5.35 | 1.47% |
| DATA/ops | 5.20 | 1.43% |
| DATA/initialization | 3.03 | 0.83% |
| 其他主数据（验证、测试等） | 3.09 | 0.85% |
| 其他 Docker（日志、缓存、其他卷） | 0.68 | 0.19% |
| 系统、swap、系统日志等 | 9.53 | 2.63% |
| **合计** | **362.92** | **100%（四舍五入）** |

**Workspaces 与 Runtime 合计约 45.37%；再加 Read、Bus、Research，五项合计约 73.84%。** Docker BuildKit 只有约 **0.089 GiB**。本轮应把精力投入任务和数据生命周期，而不是反复清几乎为空的构建缓存。

主要路径简写：

```text
DATA = /var/lib/docker/volumes/doxagent-v2_v2-data/_data
SITE = /var/lib/docker/volumes/doxagent-v2_v2-site-strategy/_data
```

### 2.2 Workspaces 的组成

以下是全盘一次遍历内的物理归集，不是对每个目录单独 `du` 后相加。硬链接只计一次，`.init` 与 `.init-contents` 的块归属会受先遍历到哪个路径影响。

| 部分 | GiB | 判断 |
|---|---:|---|
| `runtime-maintain-*` | 56.37 | 最值得清理；完成任务仍留过程镜像与多份发布内容 |
| `.init` | 18.10 | 冻结初始化资料；应按当前生产/明确恢复任务退役旧 owner，不能整体豁免 |
| `.w3-packs` | 7.18 | 终态 Pack 与被保护任务的物化输入，应随 owner 退出 |
| `persistent-runtime-w3-*` | 3.32 | 活跃实例保留，已退休实例精简 |
| `.w3-sources` | 3.00 | 来源快照；先去掉过期任务引用，再使用现有回收路径 |
| `.w3-contexts` | 2.20 | 过程上下文，不属于需要永久在线保存的最终结果 |
| `init-*` | 1.84 | 与初始化状态/当前采纳版本一起决定退出 |
| `d2ws-*` | 1.40 | 已采纳结果存在时，旧输入/中间态可退出 |
| `.control` | 0.89 | 保留必要运行账本；历史恢复物化不应无期限保留 |
| `.init-contents` | 0.73 | 与 `.init` 存在共享块，禁止重复估计收益 |
| `w3-ready-*` | 0.61 | 失效、已使用、已放弃的池成员应退出 |
| `.w3-case-archives`、`w3-case-*`、`w3-template-*` | 1.26 | 留必要结果/当前模板，旧完整过程应有退出期限 |
| 其他目录、工作账本及文件 | 2.53 | 包括约 1.11 GiB Worker Jobs DB；不整体删除 |

## 3. 分项审查与清理决策

### 3.1 Maintenance Workspace：明确应该清理，优先级最高

后续元数据扫描共发现 7,337 个 `runtime-maintain-*` 目录，其中 5,287 个有 `audit/retained-assets.json` 的完成退役标记。

| 候选 | 全体占用 | 已完成退役子集 | 本轮判断 |
|---|---:|---:|---|
| `artifacts/document3/v21_state.json` | 10.11 GiB / 3,130 个 | **7.21 GiB** | 已退役子集清退完整过程镜像，保留小型结算摘要与最终文档 |
| `artifacts/releases` 与 `published` 等路径的独立发布副本 | 9.36 GiB / 15,735 对候选 | **6.67 GiB** | 同尺寸且有 manifest 哈希；执行时逐对验哈希，转换为同一份不可变内容的硬链接 |

这两行是不同文件集合，可相加；全体与已退役子集不能再相加。镜像文件本次核验为单硬链接；发布副本统计排除了已经共享同一 inode 的配对。全体候选约 19.48 GiB，先处理已有退出证明的 **13.88 GiB**；其余约 5.60 GiB 在 owner 正式结算/放弃后继续退出，不意味着可以立即全删。

现场样本 `runtime-maintain-8a8dc6c0f5cfcaa78b610923-o3` 已于 11 月 13 日凌晨北京时间完成退役，标记显示此前已清约 8.7 MiB，但目录仍占约 13.2 MiB，其中包括约 4.5 MiB 完整状态镜像和两套发布内容。该样本的 D3 恢复证明已设置 `resume_allowed=false`。继续为其保存完整过程视图，业务价值很低。

另外两个未完成退出的样本各占约 29—31 MiB，相同文档还同时存在于 `output/final`、`artifacts/document3/releases/<version>`、`published/<release>/...`。三个样本各抽验五份发布文件，均与 manifest 哈希相符。**抽样通过不是全体已验哈希**，下一轮采用逐对核验即可，不需要额外建立一套全局文件治理平台。

根因在现有清理边界：`workspace_retention.py` 主要清 `context`、`attempts/*/input` 及已验哈希的 `output/final`；`complete=true` 后按 owner revision 跳过，**不处理完整 `v21_state.json`，也不合并 `published` 与 `artifacts/releases`**。所以历史 GC 是确实有作用、但完成定义不完整。

发布链路也解释了副本来源：D3 orchestrator 写 artifacts 与 output，`LocalWorkspaceStore.publish` 再用 `shutil.copyfile` 生成 published。收据同时引用原 artifacts 路径和 published manifest，故不宜只删一个目录破坏路径。推荐保留两套必要路径、共享一份只读内容：确认终态、无活跃同族任务、文件不可变且哈希一致后，在同文件系统以临时硬链接原子替换副本。后续代码也只对不可变终态资源这样处理，禁止对仍会原地修改的文件做硬链接。

代价：旧任务完整调试界面/某些 Pilot 过程评估不能直接重放。**接受此代价**；最终产物、采纳关系和恢复责任已独立存在。不是声称所有过程都可以无损重建。

### 3.2 Read：应该压缩，当前至少 18.06 GiB 不是业务数据

Read 共 35.14 GiB；活动库位于 `read/serving-v4-20261018/v2.sqlite3`：

- 主库约 **32.36 GiB**；4 KiB 页，8,482,731 页。
- freelist 为 4,735,576 页，即 **18.065 GiB**，约占主库 **55.8%**。
- 已分配非 freelist 页约 **14.30 GiB**。这不是业务逻辑字节总量，也不是最终压缩输出的精确保证。
- 后续复核 WAL 只有约 8 KiB；不能把整个 Read 的体积归咎于 WAL。其余约 2.77 GiB 包含 native/content 文件与 quarantine 等，未全量核销，不计入首轮收益。

**决策：不删当前主库；做一次纯物理压缩，释放逻辑删除后遗留空闲页。** 这不需要再缩短业务历史期限，也不需要重建全部 Read 历史。

现有 `v2_read/maintenance.py:compact` 是旧的影子转换流程：要求空余达到源文件 3 倍，还包含 migrate、payload 改写与内容外置，不适合直接拿来做这次简单回收。32.36 GiB 主库按该门槛需要约 97 GiB 空间，反而让可回收资源一直无法落地。

精简执行方式：先释放文件系统候选，获得约 30 GiB 以上余量；安排 Read 范围维护窗口，停止所有该库写入者，checkpoint 并保持冻结；在相同目录用 SQLite `VACUUM INTO` 生成新文件，只改变物理布局，不迁移 schema/不改业务对象。比较源/目标的 integrity、关键表行数、公开 seq、各源 checkpoint、views/cursors/pins；关闭旧连接后原子切换同一路径并重新打开连接。源文件保留至切换验收完成，然后退出。若仍有未冻结写入，禁止直接切换一个旧快照覆盖当前进度。

新库预计约 14—15 GiB，另留 WAL、操作临时空间及业务增长余量；执行前以最新页数和实际临时空间方案复核。原库本身就是切换回退点，无需先复制一份 32 GiB 额外备份。不要把“产生 freelist”和“磁盘真正释放”混为一谈。

### 3.3 Runtime：应清理过度过程留存，不应整体保护 65 GiB

物理组成：

| 部分 | GiB | 决策 |
|---|---:|---|
| `native-files` | 23.85 | 先退役低价值过程 owner，再用原生引用 GC；不按文件年龄盲删 |
| `runtime.sqlite3` | 约 25.00，持续变化 | 精简旧过程/传输/失效任务载荷；保留财务与当前业务事实；清行后再压缩 |
| `runtime_artifacts` | 14.83 | 退出不再需要的运行分支；仍引用的只留准确必要版本 |
| timing、receipts、W3 assets 等 | 约 1.53 | 短期诊断和终态收据可精简，收益次于前三项 |

现场有约 3,587 个运行分支，3,585 个 Event Library 数据库，仅这些主数据库就约 **12.51 GiB**，已包含在 14.83 GiB 内。不能把每个历史分支都当作一个必须完整保留的独立研究成果。

任务结果说明应区别当前责任和旧状态：CASE 成功 27,951，失败 2,092；CASE PENDING 77 个最后更新时间仍在 10 月 25—28 日；Maintenance HELD 38 个最后更新时间是 11 月 7 日北京时间。全体 Worker 实际 running 仅 2 个。这并不直接证明旧任务可删，却足以否定“状态未终结就永远有现实工作”的默认假设。

建议保留订单、成交、分配、持仓、当前策略及其精确证据；失败/已过期的旧任务显式终结，给旧业务意图一个不再自动复活的终态。没有执行机会的旧 CASE 不应无限占用 W3/输入/引用保护。先收敛业务终态，再释放过程 refs，不能反向强删仍有真实履约责任的状态。

现有 GC 已支持成功分支 48h、失败分支 7d 后退出/保留必要版本，但现场 `last-resource-exit.json` 有 **297 项 pending**，最近一次 retired=0、compacted=0，记录多次 `active realtime; deferred heavy compaction`。**“有退出代码”不等于“有足够退出吞吐”。** 修复应优先让既有按 root 加锁、重新验引用、限定工作量的压缩每轮能推进一项，而不是要求全局业务完全空闲。保留锁和准确版本校验，移除无限期饥饿条件，不必新建资源调度平台。

原生内容以 SHA256 组织分块 gzip。业务数据的 CAS 引用中也可能装着旧 Worker 调用、上下文和传输前后态；先去掉这些低价值 owner 的保留责任，再走现有 owner-exit/reconcile/quarantine。`artifact.size` 是描述符口径，不是 23.85 GiB 文件池的物理占用；本方案不据其 SUM 虚报可回收量。

本轮几项 Runtime namespace 大体量 SQL 在预算内未完成，所以不宣称 25 GiB 主库具体有多少来自某个表，也不把整库列为可删。已成功量化的任务 inputs/receipt 仅百 MiB 级，不能解释整个主库；执行时应记录每类 owner 退出与物理池变化，而非继续把全部体积笼统称作必要账本。

### 3.4 Research：结果有价值，不代表每次发布与过程都等值

Research 33.94 GiB，其中主库约 30.97 GiB、native-files 2.41 GiB、adoptions 0.55 GiB。

- `published_documents` 16,121 条 JSON 共约 **9.53 GiB 逻辑字节**；SQLite 保存完整发布对象，工作区又有内容副本。
- D3 运行记录 3,184 条共约 **1.04 GiB 逻辑字节**；说明此前精简运行 payload 已产生效果，不能说历次优化全部无效。
- bundles、artifacts、attempts、threads 等已量化的小记录合计仅几十 MiB，保留其状态和谱系很划算。

**决策：保留当前采纳文档、交易/Case 准确版本、确有复用价值的独特历史结果；清理未采纳、无引用、已被同内容发布替代的正文与过程镜像。** 对无引用且没有差异价值的历史发布，保留版本、哈希、摘要和退出收据即可，不必永久在线保留正文。文件与 DB 都保留完整正文时，只保留一个可靠读取来源或共享不可变内容，不能直接删除被 API 读取的 `published_documents` 行。

先做确定的工作区重复消除，不立刻将所有发布文档迁到新存储系统。只有随后识别出确有大收益的重复正文，才改现有 repository 的读取来源并验证当前/历史读取。不能把 9.53 GiB 逻辑字节直接加到前文工作区物理收益，更不能把整个 30.97 GiB DB 当成可清理空间。当前主库没有显著 freelist，单纯 VACUUM 收益有限，须先减少实际保留数据。

### 3.5 Bus / Acquisition：过程账本应积极退出，归档失败应修

Bus 总体 34.24 GiB：Bus 主库约 13.28 GiB，Acquisition 主库约 **18.18 GiB**，native-files 约 2.72 GiB，其余很小。两个主库当前 freelist 均接近 0，不能指望无条件压缩立刻回收。

Acquisition 现场证据：

- `unit_hours` 为 0 行；最近 retention 报告 `hours` 阶段报 `interrupted`，运行约 5.44 秒。
- physical 扫描 512 条、删除 0；schedule 扫描/删除各 128 条。不是整个维护完全没跑，而是核心小时归档/部分退出没有形成吞吐。
- 最老 cycles 仍有 10 月 28 日 `SCHEDULE`；不能只因它标记待调度就永久保护，更不能只因老就直接删除。
- captures 的 ACCEPTED 7,693 条，payload 合计只有 15,386 B，已是薄记录。不要再次围绕 captures payload 做无效优化。

**决策：采集原始执行过程不值得保留半个月；必要正文、覆盖与故障统计单独保存。** 成功/最终失败的 cycle、physical request、重试过程沿用现有 48h 原始明细窗口、小时统计沿用 7d；长期只保留确有业务意义的来源/正文/采纳证据。现有代码已经规定这些窗口，问题在于退出推进不畅，不需要重新发明一套保留天数。正在发生的任务凭有效 lease/真实 owner 保护；过期 schedule、僵死 pending 显式结算，不用“过去创建过”保护。

代码已经按 binding/unit/hour 处理，但 `_retire_hour` 给整段读取仅 0.5 秒：先从时间序列寻找非 SCHEDULE/DISPATCHED 行，再整小时加载 samples、调度配置与 physical requests 并展开 JSON，最后才删最多 512 条。单次入口扫描或整小时展开过慢，就会反复无法提交。现场已经有 `cycles_unit_time`、`cycles_schedule` 等索引，不能不看执行计划就再加同类索引。

优先对超时点记录 SQL 阶段并检查执行计划：若入口长扫被保护状态，改用针对终态候选的索引/推进坐标；若整小时载荷超预算，按时间/主键有界累积摘要。只有这一小时的完整摘要形成后才退出对应原始行，避免分批统计重复或丢失。复用现有 meta、unit_hours 和事务，避免新建归档服务；也不应仅把 0.5 秒改成几十秒反复争抢生产资源。Bus 的 CDC/outbox/receipt 保留必须区分还需 ACK/replay 的窗口与已送达过程；若缩短 replay 窗口，确认既有 snapshot/full-rebuild 能接管过期消费者，不能直接删未被接收的唯一正文。

本轮大表总 payload 分组查询也超时，故 **18.18 GiB 是治理对象总规模，不是已经确认可释放量**。建议清理，但具体回收量须以归档推进、行退出与压缩后的实测计入。

### 3.6 Codex Home：会话和日志不是永久业务资产

共 19.25 GiB：Sessions 10.04 GiB、`logs_2.sqlite` 5.45 GiB、`thread_history_1.sqlite` 2.98 GiB、SDK state 0.38 GiB、cache 0.33 GiB 等。

**决策：清理旧诊断日志，主动退休旧会话，保留小型最终结果。**

- DEBUG/INFO 普通日志保留 48—72h；当前事故与必要错误摘要保留 7d。通过 SQLite 删除、checkpoint、必要时紧凑副本切换；不 truncate 活库文件。5.45 GiB 是对象总量，并非全部都是旧日志，不先承诺全部回收。
- 终态任务已结算、有最终产物的全量工具调用/对话流通常只保留 7d；无未完成业务责任的普通非交易任务可更短。活跃会话、当前模板和确有恢复需求的父链保留。去掉“所有 W3 天然免清”规则。
- `thread_history` 是派生投影，保留范围应跟随实际留存 rollouts；不能先删除仍需使用会话的唯一文本，或把 SDK 正在写入的数据库直接重建。
- 现场 Worker registry 已有大量 `DELETED` CASE，但 SDK state 仍有 5,155 条未 archived thread。两者不是严格一一关系，不能直接相减算垃圾数；足以要求核对终态 owner 到 SDK 删除的实际落地。

现有 session retention 主要在全局 Jobs 空闲时每 6h 才做 SDK 全量列表，且对 W3 大量豁免。在持续业务下容易形成无期限留存。应在现有 Worker 维护周期内分页退出已知终态线程，不让“永远等空闲”成为前提。删除全量历史会牺牲精确工具调试重放，这是可接受且应明说的成本，不伪称可无损恢复。

### 3.7 Site 与宿主机浏览器：明确拆开身份和可丢弃信息

Site 卷共 21.48 GiB：profiles 14.28、registry 3.97、snapshots 2.74、challenge evidence 0.26、backups 0.23 GiB。

身份注册记录 1,660，identity heads 985；注册行、实际目录、当前 enabled 身份不是同一个集合。不能按行数粗算每个 profile 都该保留，也不能删掉 enabled=false 的全部身份，因为有些承担明确恢复责任。

**决策：保留当前可用身份与确有需要的备用；退出已废弃、已替换、无正在进行挑战/维护工作的旧 profile 及其历史 snapshot。** 退出时保留小型身份谱系和当前可恢复凭据，HTTP cache、编译缓存和遥测无需备份。保护标记必须绑定具体恢复事件和到期条件，不能作为永久快照豁免。

现有 profile snapshot prune 保留普通快照 2 份，但 protected 快照不受该数量限制，registry 备份又可能被历史 manifest 持续引用。因此“被快照引用”并不自动证明每套备份仍有必要。应先解除过期恢复事件，再按 profile 保留当前加一个需要的回退点，退出过时快照及独占 registry backup。

**本轮新增确定性候选：**

```text
/home/doxagent-desktop/.config/doxagent/reuters-chrome/BrowserMetrics
```

共 **2,096 个 `.pma`，8.189 GiB**，全部超过 48h；本次 `/proc/*/fd` 与 maps 扫描未发现仍被打开或映射的文件。它们是遥测，不是 Cookie、Local State、登录数据库或业务正文，**建议列第一批清理，无需备份、无需删整个 Chrome profile**。执行前重新检查打开/映射状态即可。

宿主机 Reuters 浏览器由 `deploy/doxagent-reuters-chrome.service` 维护。当前 Site GC 按 browser pool 的 profile root 遍历，不能假定覆盖这个独立宿主机路径。下一轮复用同一诊断文件白名单，给这个确切路径补上 48h/128MiB 退出规则；不把全 `/home` 加进通用递归清理。可评估关闭不需要的遥测，但应验实际文件产生量，不把添加一个 Chrome flag 当作治理已经成功。

此前冷 profile 可再生缓存已清过，剩余活跃 profile 缓存约 3.37 GiB 是潜在对象。选择必要 profile 的浏览器空窗执行缓存白名单清理，不必销毁身份。该值来自此前紧急审计，未重复全量计入本轮首批 40.13 GiB。

### 3.8 初始化、Ops、测试、恢复备份：应做业务取舍，不整类保护

| 对象 | 当前规模 | 建议 |
|---|---:|---|
| Workspaces `.init` 及共享内容 | 约 18.82 GiB，含硬链接分摊 | 当前生产采纳版本和真实正在重跑版本保留；被新版本替代的冻结资料、无实际推进意图的旧 manual-resume owner 应退出 |
| DATA/ops | 5.20 GiB | IONQ 1.43、BE 0.96、INTC 0.86、COHR 0.76、RKLB 0.73、CEG 0.37 GiB 等；核对当前采纳版本后，退出成功/已替代 refresh 的重放包与输入副本 |
| DATA/initialization | 3.03 GiB | native-files 1.94、工作区 0.54、control DB 0.54 GiB；后者约 0.455 GiB 空闲页可回收，但收益次于大项 |
| Production Validation | 1.19 GiB | 保留报告、失败最小复现和真实采纳映射；不能把含约 0.88 GiB adoptions 的整个目录当临时垃圾 |
| MU/INTC/BE/RKLB 测试根 | 合计约 1.30 GiB | 验证不存在生产挂载/引用后退出完整沙盒，只留精简测试结论与独特 fixture；不为沙盒再做整份备份 |
| `/var/lib/doxagent` | 5.35 GiB | releases/backups 3.18 GiB、COHR recovery 0.86、initialization-repair 0.66 等；只留一套有恢复责任的事故回退，不保留每次修复尝试的完整数据库 |
| `/home/ubuntu/deploy_backups` | 1.35 GiB | 两个 10 月 23 日 challenge 修复包各 0.675 GiB；已经被后续验证发布替代，应删除重复整包，留小型操作记录 |
| `/home/ubuntu` 其他 | 约 2.29 GiB | 主 checkout 1.27、BE export 0.60 GiB 等；旧 export 若已回流权威库即退役，当前代码/部署工具保留 |

**取舍原则：** 有可读备份不等于必须一直保留；正式当前库正常、事故恢复完成并已被后续发布验证后，旧事故库通常只是低价值调试便利。应留一个确定责任的回退集合，退出其他 trial；不以“可能某天想查”继续保留每份全库。

手工重跑的 RUNNING 状态也可能在一次性容器 exit=0 后残留。不能只看容器退出就删，但也不能因为账本还写 RUNNING 就无限保护：对照最后推进时间、下游采纳、当前 activation 和实际 owner 意图，作出结算/放弃决定。需要再跑时新建任务，不承诺永远原地恢复。

### 3.9 镜像、构建与系统：清理，但投入应与收益成比例

- containerd 共 **15.13 GiB**：overlay snapshot 11.13、content 3.96、metadata 0.04 GiB；它包含运行镜像所需内容与展开层，不是全部构建垃圾。
- 当前镜像列表 82 行包含同 ID 多 tag 和共享层；不可把每行的 3.73 GB 加起来算出几百 GB 可回收量。
- Docker 自身除数据卷外不大，BuildKit 约 0.089 GiB，容器 JSON 日志约 0.19 GiB。这一轮不应围绕它们继续做大工程。
- 建议现有发布管理器保留当前运行镜像、每服务一个明确验证回滚版本、必要 Worker 模板镜像；停止的旧 refresh/迁移容器完成业务结算后删除，其独占旧镜像再退役。不要手动删除 `/var/lib/containerd` 文件。
- OS、swap、系统日志及其他合计约 9.53 GiB，仅占 2.63%；2 GiB swap、内核与当前系统软件应保留。常规旧包/旧 journal 有小收益，优先级低，禁止把 swap 作为“大块垃圾”。

## 4. 精简执行顺序与收益账本

### 4.1 第一轮：已量化的大块候选

| 批次 | 操作 | 候选 GiB | 必须满足的简短条件 | 明确接受的代价 |
|---|---|---:|---|---|
| A1 | Reuters 旧 `.pma` | 8.19 | 仍超过 48h、非打开/映射、路径在确切诊断目录内 | 丢失旧 Chrome 遥测 |
| A2 | 已退役维护工作区 `v21_state.json` | 7.21 | 退出证明、无活跃同族任务；最终产物可读；确认不是独特未采纳结果 | 放弃完整过程视图/原地续跑 |
| A3 | 已退役发布重复副本共享不可变内容 | 6.67 | 逐对 sha256=manifest，非同 inode，链接边界与只读性正确；保留必要路径 | 不损失正文；所有路径必须保持不可变 |
| A4 | Read 物理压缩 | 约 18.06 | A1—A3 先恢复余量；冻结写入、完整性/seq/checkpoint 验证后切换 | 一个计划内 Read 维护窗口 |
| **合计** | **互不重叠，尚未执行** | **约 40.13** | **候选量，不保证每个对象执行时仍符合条件** | |

A1—A3 先释放约 22.07 GiB，足以为 Read 新库留下合理操作空间。不要在仅约 9 GiB 可用时先做复制整库的压缩。A4 源文件到切换验收完成前仍占磁盘，不能在影子文件建好就提前报已释放 18 GiB。

每批只记录对象清单、退出原因、删除/替换前后 inode 与 allocated、`df` 前后差；不备份缓存或重复副本。生产在写入时净 `df` 变化可能小于对象释放量，应同时给出二者，不虚报。

### 4.2 第二轮：应继续推进的业务价值清退

按收益/修改量排序：

1. **SDK 日志与终态会话**：先日志，再已知退役线程及派生 history。对象总量约 18.46 GiB，不能全当可删；保留当前会话/事故窗口，不为完整老对话回放无限让步。
2. **剩余维护工作区 owner 结算**：全体镜像/副本候选中尚有约 5.60 GiB 未纳入第一轮，结算后清退；同时清旧 context，不再重复计算首轮 13.88 GiB。
3. **旧 refresh / 初始化 / 事故恢复包**：将当前采纳与有效恢复责任对齐后，积极结束过时 manual-resume、RUNNING 和保护事件；清其独占快照/输入/整库备份。
4. **Runtime 分支及 Native owner**：先消除 pending 压缩饥饿，再压缩仍需准确历史版本的分支、退出其余分支；紧接着释放过程 refs。不要直接手删 CAS 文件。
5. **Acquisition 归档阻塞与原始账本**：让小时归档真正成功，再退原始周期/物理请求及过期保护；删行后物理压缩，不把逻辑删行当成回收完成。
6. **Site 旧身份/快照**：清缓存之外，还应明确退役已替代身份和过时 protected snapshots；保留当前身份及一个有实际责任的回退。
7. **低价值无引用历史发布/测试根**：正文与重放材料退出，保留最终结果、谱系和必要 fixture。不是 Research、Validation 或 Agent 标签一律保留。
8. **镜像及小型系统垃圾**：随发布系统完成，非主要救盘措施。

上述治理对象中仍有数十 GiB 值得清退，但本轮没有逐项完成全量 owner-物理块核销，因此不编造一个“肯定再回收 70/100 GiB”的数字。**建议把累计释放 60—80 GiB 作为这轮执行目标**：首轮有约 40.13 GiB 候选支撑，其余通过明确的旧任务/会话/分支取舍争取；达到目标仍不能代替增长治理。若仍不足，应报告具体有价值的保留对象，而非默认保护所有历史。

### 4.3 最小安全边界

所有大项执行只需要三个边界，不需要层层新增审批和全量备份：

1. **当前 owner**：真实活跃任务/当前采纳/未完交易责任，与已经结算、被替代、明确放弃的任务分开。僵死状态先终结。
2. **内容与路径**：必要最终结果存在且可读；重复文件校验哈希；清理必须限定到已枚举根目录，防止符号链接越界。CAS 按引用退出，SQLite 按事务与受控切换操作。
3. **实际落地**：每批记录净空间变化和最小业务验证。无需对每个可再生缓存做一套恢复演练；身份与唯一生产结果才需对应验证。

## 5. 为避免再次长满，只补这些责任

本次不建议建立跨全系统的新资源管理框架。复用现有组件的退出入口，修正以下几处即可：

| 责任组件 | 最小修正 | 验收依据 |
|---|---|---|
| Workspace retention | 完成标记覆盖过程镜像与发布副本；旧完成标记允许一次补扫，不因 complete 永久跳过遗漏内容 | 新退役任务无完整镜像、正文不重复占物理块；旧批次有真实释放 |
| Worker/session maintenance | 按终态 owner 分页推进，不要求全局空闲；不再整体豁免 W3 | 已退休线程/Pack/ready slot 数与占用持续下降，当前任务正常 |
| Runtime resource GC | 每轮保证有界重压缩推进；保留现有引用重验与锁 | pending 最老年龄下降，终态分支物理体积收敛 |
| Acquisition retention | 小批归档与退出，消除 hours 超时；过期 lease 结算 | unit_hours 开始产生，原始窗口与物理 DB 体积进入稳定范围 |
| Read maintenance | 定期比较空闲页与库大小；例如空闲超过 4 GiB 且超过 25% 时安排纯压缩 | 同时报告 logical GC 与 physical reclaim，不再仅报删行 |
| Site + 宿主机 Chrome | 复用已有诊断白名单覆盖 Reuters 确切路径；protected snapshot 关联真实恢复事件 | BrowserMetrics 有 128 MiB/48h 边界，当前身份哈希/登录可用 |
| 各 refresh / recovery owner | 成功采纳、替代或放弃时关闭恢复责任并退出输入快照 | 不再新增永久 RUNNING/manual-resume；恢复包有明确负责人和退出点 |
| 现有部署工具 | 自动保留当前与明确回滚镜像，退出其余独占资源 | 发布后自行完成退役，且不删除在用模板/回滚 |

W3 活跃运行期间“完整研究资料重复物化”的大架构调整仍可独立排期，不作为以上清理与退出修复的前置条件。**终态 W3 资源退出与活跃 W3 输入架构重写是两件事**，前者不应继续被整体搁置。

容量运行目标建议保留至少约 50 GiB 可用，低于约 30 GiB 应定位哪个 owner 净增长；这些是告警与操作余量，不是到阈值就任意删除业务数据。持续治理验收应覆盖一个正常业务周，观察各 owner 新增与退出净额、pending 最老年龄、Read 空闲页和实际可用空间。正常发布、业务增长、维护回收分别记账，避免一次清出 40 GiB 后就宣布永久解决。

## 6. 证据、代码依据与未完成的量化

证据目录：`artifacts/audits/root_disk_value_review_20261114/`。

| 文件 | 用途 |
|---|---|
| `root-scan.jsonl`、`remote_root_scan.py` | 完整根盘一次遍历、30,314 条目录统计、df 与唯一瞬态错误 |
| `root-breakdown.json`、`workspace-prefix.json` | 互斥占比和 Workspace 前缀归集 |
| `db-inventory.json` | 各 SQLite 页数/空闲页、只读 schema 与索引 |
| `value-facts.json`、`logical-bytes.json` | owner/任务状态、Research 发布与 D3 逻辑字节、Acquisition retention |
| `workspace-mirrors.json`、`remote_workspace_mirrors.py` | 逐任务镜像/重复别名候选、退出标记、三组哈希样本 |
| `browser-metrics.json`、`last-metadata.json` | 宿主机遥测占用、fd/maps、文件年龄、Read 文件状态、容器及镜像 |
| `runtime-detail.json` | 运行分支/数据库数量、分支 DB 物理占用、297 项 pending 与推迟原因 |
| `deep-tables.json` | 有预算大查询的超时记录；未据超时查询推导比例 |

主要代码依据：

- `src/doxagent/codex_worker/workspace_retention.py`：退役资格、W3 豁免、候选扫描范围、完成标记。
- `src/doxagent/codex_worker/workspace_store.py:306`：发布时复制不可变 artifacts，生成独立 published 内容。
- `src/doxagent/codex_worker/session_retention.py`：线程保护与 SDK 空闲维护。
- `src/doxagent/workflows/codex_document3/orchestrator_v21.py`：工作区过程视图与发布文件的多次物化。
- `src/doxagent/codex_runtime/repository.py:827`：本地发布文档存储/读取。
- `src/doxagent/persistent_runtime_v2/resource_gc.py:267`：分支退出、引用重验与实时业务导致的重压缩推迟。
- `src/doxagent/message_bus_v2/collection_plane/maintenance.py:13`：0.5 秒小时归档、48h 明细、7d 小时摘要及各退出阶段。
- `src/doxagent/v2_read/maintenance.py:370`：现有影子转换与 3 倍源库空间门槛；不应原样套用于简单释放空闲页。
- `src/doxagent/v2_read/native_content.py`、`source_native_gc.py`：原生大载荷与 owner-exit GC。
- `src/doxagent/site_strategy/service.py:778`、`resource_gc.py:149`、`profile_storage.py`：托管 profile 遍历、诊断白名单、快照保留。
- `deploy/doxagent-reuters-chrome.service:13`：独立宿主机 Reuters profile。

明确的量化边界：没有对 Runtime 25 GiB 主库所有表完成物理页归属，也没有对全部 Native 引用、每个旧身份、每份历史发布完成逐对象价值结算；大表查询达到预算后中止，避免持续争抢生产 I/O。因此它们是建议继续清退的具体对象和决策规则，而不是已经算进收益的垃圾。首轮 40.13 GiB 不依赖这些未完成的大表比例推断。

本轮结论是：**必要业务可以保留，但完整旧过程、重复正文、遥测、长期恢复便利和永不过期的保护状态不应一起永久在线。** 先按上表落地约 40 GiB 大块候选，再让已有 owner 真正完成退出，远比再设计一个复杂的全局存储平台有效。
