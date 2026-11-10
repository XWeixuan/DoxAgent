# DoxAgent 全局存储生命周期审查与优化执行方案

日期：2026-11-11，北京时间。生产主机 `doxagent-sg`；本地仓库 `C:\Users\WEIXUANXIE\Desktop\DoxAgent`，远端 `/home/ubuntu/doxagent`。

本报告对应两阶段任务的第二阶段：**审查、历史复盘和设计，不实施大规模改造。** 第一阶段低价值清理已完成，文件系统净释放约 40.105 GiB，详见[独立执行回执](storage-recovery-execution-20261111.md)。以下修复、迁移和追加清理均是待执行方案，不得理解为已上线。

## 1. 决策与设计基线

这次膨胀的主因是业务过程数据与维护账本没有可靠退出，当前证据不支持继续以构建缓存为主要治理对象。优先执行四条链：

1. **修 Read 的确定性 GC 查询/预算缺陷，再退出不需要在线保留的技术 current 数据。** 保留 V4 单份 current、短协议历史和业务版本引用的架构，不再重做一套 Read。
2. **由 D3 与 Maintenance 的业务 owner 结算重复输入。** 已发布成果、必要引用、审计身份继续存在；大块 prepared/base/files 和多份等价过程副本在完成交接后退出。不能把 `PARTIAL/STAGED` 一律视为需要永久恢复的未完成任务。
3. **让 Acquisition、Runtime 的退出真正推进。** 有限的输出条数不代表有限扫描；小时间预算也不能成为永远做不完、跳过后假装遍历完成的原因。
4. **在既有 producer/owner 内补齐备份、profile 缓存、初始化快照和 Session 的责任，并用真实积压/退出结果验收。** 不建设全局资源管理平台，不新增一套调度/消息系统，不再靠 Agent 每次记得手工清理。

不降低现有业务并发、不删除唯一生产事实、不靠长期暂停维护或业务来伪造平稳。W3 完整研究资料/Context 反复复制仍单独列为后续议题，不是本轮其他修复的前置条件。真正有价值的唯一历史会随业务增长，目标是消除无价值放大，并让剩余增长可以解释和预算；有限磁盘不可能无限容纳永久增长的数据。

## 2. 历史修复复盘：本次方案的设计输入

### 2.1 已执行案例，而非只读旧方案

下表以执行回执、当前代码与现场证据交叉判断。历史数值都是当时窗口，不能冒充当前占用；部署完成、本地测试通过和长期容量达标分开记账。

| 时期与证据 | 当时措施与结果 | 当前判断 | 本轮必须吸收的经验 |
| --- | --- | --- | --- |
| 9/28，[清理执行记录](disk-cleanup-execution-20260928.md) | 42 镜像、停止测试容器、旧快照、BuildKit；净释放约 81.15 GiB。随后同盘制作新备份，又消耗约 19 GB | 释放真实有效，但没有改变生产数据生成；同盘新备份立即吃回部分余量 | 缓存、可验证重复副本直接退出；有恢复责任的副本按集合管理，不能每次救火都再留一份全量 |
| 10/9，[消息源空间恢复](message-source-storage-recovery-20261009.md) | 删除两个旧全量备份，恢复约 34.9 GiB；区分 FULL/IOERR/BUSY，存储故障不消耗内容失败预算 | 故障隔离有价值；一次性腾挪没有解决上游增长。删除旧恢复点也确实减少了对应历史恢复能力 | 保留基础设施错误语义；不能把“运行恢复”写成“存储治理结束” |
| 10/13，[根因](disk-rootcause-and-repair-plan-20261013.md)、[执行](disk-repair-execution-20261013.md) | 同一文章按 run 重复 admission，约 2287 文章膨胀为 378 万 observation、1134 万 delivery；改稳定业务键。过滤高频 poll CDC、启用 GC、精准 gap 范围；退出约 1088 万拒绝分发；Bus 19.90→5.90、Read 29.50→22.93 GiB | 上游幂等是应保留的根本修复；逐行对账/压缩收益真实。当前不是已证实同一个 admission bug 再现 | 以业务身份去重；过程运行 ID 不能制造新业务。保留真正 Fact。全库大 JOIN/排序曾耗尽临时盘，应采用有界键序扫描 |
| 10/13，[P4 审计](disk-p4-retention-audit-20261013.md) | producer 日志轮转、受管 migration backup 只留一套验证集合；明确不管理手工副本、不扩展 Session/profile 治理 | 克制边界合理；但后来多轮手工 Site/发布副本落在边界外，没有 owner | 不是恢复无差别全局 prune，而是给这些实际新增生产者补 owner 和事件结束退出 |
| 10/15–17，[复涨与清理](remote-disk-regrowth-and-cleanup-20261017.md) | 两天前释放 46.48 GiB，约 49 小时后又消耗约 50.53 GiB；动态提交身份破坏依赖层缓存、BuildKit 与镜像、Context 重复物化均有实证 | 当时确有镜像/依赖放大；当前 BuildKit 很小，不能沿用旧诊断 | 每次按真实构成定位；相同“满盘”症状有不同主导机制 |
| 10/18–19，[架构方案](doxagent-storage-architecture-refactor-plan-20261018.md)、[实施回执](doxagent-storage-refactor-execution-20261018.md) | Read V4 压到约 3.11 GiB，取消多份 current、共享定义、消除 Case×Policy 负集合；稳定依赖层与发布退出；Event 分支、Native 引用、非 W3 Session/workspace、初始化不可变内容、缓存和 backup owner | **核心设计曾工作**，不是全部“无效优化”。旧深度清理部分由用户暂停，W3 完整物化明确暂缓；未证明七天增长闭环。今天 Read 达 34.81 GiB，需修持续退出和新增对象，不应推倒 V4 | 将“初始代际很小”与“后续 GC 可持续”分开验收；未执行的遗留清理不能称回归；新生产路径必须继承退出责任 |
| 10/19、10/21，CPU 执行与 P0 回执 | 去重复轮询/聚合、连接复用和稳定 activation 快路；保持业务与并发 | 有助 CPU/IO，不能替 D3 快照和数据库历史定义退出 | 不用减少并发掩盖存储设计；避免为治理存储新增高频全库扫描 |
| 10/24，提交 `9c479777`；[发布回执](message-source-polling-and-body-completion-optimization-release-20261024.md) | GC 连续中断时 batch 不再降到无效值；SQL 使用既有身份索引、正文结果复用；发布承认长窗口未验收 | batch 修复保留，但未覆盖这次“水位查询先耗完 100ms”的根因。缩小删除批次不会使固定前置扫描变快 | 回归必须包含大账本、真实执行计划和最终提交产出，不能只测 batch 下限 |
| 10/27–29，[Bus 精益实施](message-bus-lean-implementation-20261028.md) | Acquisition 独立、48h 原始证明/7d 小时摘要，薄实时链路、ACK/去重；558 项组合回归等通过，24/72h 未完成 | ACCEPTED capture 已压成空载荷，证明部分退出有效；但当前 cycles 835.9 万、仅一个小时摘要，细账未闭环 | 96h 合成保留期测试不能代表百万行下退出吞吐；摘要/清理自身必须按真实生产率验收 |
| 10/29，[全局精益实施](host-resource-lean-implementation-20261029.md) | Serving 展开前过滤无消费者字段、减少旧历史扫描、冷热 browser 生命周期、owner 归属快照；90min 及短样本，24/72h 开放 | 保留薄投影和故障域改进；当前 Read 历史、冷 profile 总量、Maintenance 新路径仍可积累 | 有界工作量必须覆盖所有候选并可续批；性能短检与长期增长分别关闭 |
| 11/6，[Event Library 语义审查](event-library-compaction-recon-20261106.md)、[业务评估](event-library-compaction-production-review-20261106.md) | 区分 Reference 排除、失效退役、有效但退出当前工作集；要求保留真实历史与身份 | Canonical 事件有效性与运行分支/过程副本退出是两件事；不能拿 Event 数作为磁盘可删率 | 不用年龄或标题初筛批量删除事实；优先清过程副本，不用删除有效事实来补基础设施退出失败 |
| 11/10–11，[现场审计](remote-disk-regrowth-audit-20261110.md)、[本次清理](storage-recovery-execution-20261111.md) | Read 638 周期零删除；D3 14.25 GiB run payload；Maintenance 45.90 GiB；低价值清理净释放 40.105 GiB | 主要是数据 owner/退出与操作副本问题；镜像不再占主因 | 先恢复余量，再修生产和退出两端；不能把清理 40 GiB 当作根因已解决 |

CPU 历史补充文件：`production-cpu-optimization-execution-20261020.md`、`production-cpu-p0-implementation-20261021.md`。它们不是存储长期验收证明。10/29 观察协议也明确记录重查询超时、真实业务目标未达与停服/版本切换边界，不能把缺测窗口算稳定运行。

### 2.2 为什么多轮正确修复后仍然涨盘

- **被修的是不同放大源。** 九月旧备份、十月分发幂等、十月依赖层重复都是真实问题；它们消失后，新 Acquisition 账本、D3 Maintenance 和失效的 Read GC 成为主要增量。
- **很多动作只改变存量。** 备份/镜像删除和初始 Read 新代际会立即变小，但不会自动约束下一次 run、快照、版本或 profile 缓存。
- **创建责任大于退出责任。** D3 的 prepared/final/handoff、工作区文件、发布文件、staged payload 都可独立写成功，业务交接结束却没有统一结算它们的过程副本。手工备份没有 owner；历史初始化归属未知就永远留存。
- **“有界”被错误地当作“可持续”。** Read 100ms 固定前置查询中断；Acquisition `LIMIT` 在过滤后，仍扫数百万行；Runtime 两秒预算可能在引用核验阶段耗完。没有完成事务，就没有释放。
- **保护范围缺少业务终点。** 历史记录出现过 run/thread 路径，不代表仍需要完整在线工作目录；失败任务可恢复也不代表永远承诺原现场恢复。相反，活跃交易和精确业务版本引用不能按年龄强退。
- **验收闭环不足。** 多份回执诚实保留 24/72h、七天未验收；后来日容量扫描又在预算用尽时漏掉 workspaces/Runtime。638 次零删除和最老账本长期不动，没有成为必须处理的失败。

本轮不恢复高成本常驻全量扫描，不把所有历史搬到另一目录冒充解决，不再引入“压力一高就永久停止维护”的硬门禁。

## 3. 当前空间、增长与证据边界

### 3.1 主要物理占用

以下为 11/10 晚间审计快照，来自实际 `du` 和 SQLite 页信息；第一阶段已删除的旧副本/缓存单独注明。百分比以约 373.4 GiB 根文件系统容量为分母，不是对目录互相求和后的份额。

| 不重叠大类/主数据卷内子类 | 清理前 GiB | 根盘比例 | 解释 |
| --- | ---: | ---: | --- |
| Docker 主数据卷整体 | 268.14 | 71.8% | 下列六项是其中子集，不能再加到整体上 |
| ├ workspaces | 81.99 | 22.0% | Maintenance 45.90、`.init` 18.46，另有 W3/初始化/研究工作目录 |
| ├ Runtime | 58.16 | 15.6% | 主库 16.75、native-files 23.14、artifacts 8.69、旧升级副本 8.27；最后一项已删 |
| ├ Bus（含 Acquisition） | 41.60 | 11.1% | Bus 主库 26.10、Acquisition 12.84、native-files 2.60 |
| ├ Read | 34.81 | 9.3% | 主库 31.99，其余内容/协议资产；不是新的完整权威业务库 |
| ├ Research | 29.88 | 8.0% | 主库 29.32，其中 D3 run JSON 14.25 是主要对象 |
| └ codex-home | 13.11 | 3.5% | Session 文件约 6.03；不是目录内每个文件都能删除 |
| Site 数据卷 | 28.09 | 7.5% | profiles 21.23；普通缓存池约 10.52，已清冷 profile 6.65 |
| 宿主 `/var/lib/doxagent` | 31.23 | 8.4% | 含旧运维副本；本次已从中删除 25.22 |
| `/home` | 12.12 | 3.2% | 工作 checkout、工具、测试等，非主要净增长归因 |
| containerd | 12.36 | 3.3% | 镜像共享层，不能再把每个 image 展示大小叠加 |
| `/var/log` | 4.76 | 1.3% | journal 约 4.01，其余日志 |

BuildKit 约 90 MiB；48 个 image ID 中 42 个受保护，剩余六个旧 `doxagent:o2-auto-*` 的 RootFS 层均被受保护镜像共享，**不能宣称能再回收其展示大小之和**。deleted-open 文件仅约 40 MiB（清理前），inode 使用约 39%；不是本次主因。

### 3.2 增长事实

| 日样本 | 根盘已用 GiB | Research DB | Read DB | Runtime DB |
| --- | ---: | ---: | ---: | ---: |
| 10/26 | 157.78 | 6.18 | 7.53 | 5.38 |
| 10/31 | 221.17 | 6.18 | 21.83 | 8.01 |
| 11/3 | 253.00 | 10.95 | 22.80 | 9.65 |
| 11/7 | 293.06 | 11.78 | 27.15 | 10.83 |
| 11/8 | 327.70 | 15.46 | 28.15 | 11.93 |
| 11/9 | 330.66 | 18.10 | 28.87 | 12.05 |
| 11/10 凌晨 | 333.64 | 20.26 | 29.77 | 13.42 |
| 11/10 晚间 | 约 366.7 | 29.32 | 31.99 | 16.75 |

约十五天净增 209 GiB；11/10 凌晨到晚间约 33 GiB，Research 约 +9、Runtime 主库约 +3.3、Read 主库约 +2.2 GiB，其他包括工作目录与原生内容。取样时刻不同，不能把不同窗口当成严格可加的当日归因。按最近这种增长速度，40 GiB 余量只是短期缓冲，不能等待数周再修。

容量历史的目录明细反复 `incomplete: daily scan time budget reached`，尤其漏掉后面的 workspaces/Runtime；根盘 `df` 仍可信。新方案须修明细覆盖，但不能每天再启动一套不受控递归扫描。

## 4. 最小必要存储模型与保护原则

### 4.1 数据职责

| 类型 | 真正责任与保留策略 | 退出依据 |
| --- | --- | --- |
| 唯一生产业务事实 | 当前与必要历史 Policy/Event/Fact、发布研究成果、来源身份、Case 决策、交易/持仓/成交/资金账本；重要且小的身份证据长期保留 | 不做通用 TTL；按业务去重/版本治理，删除或归档唯一内容前有可验证恢复能力 |
| 发布定义与版本恢复 | 保留被当前 activation、Case/Effect、accepted-rearm、历史证据明确引用的精确版本；可复用已有不可变内容/定义存储 | 无实际引用且恢复义务结束才退役；不用“所有旧版本全部在线”代替精确版本保护 |
| 可恢复过程输入 | D3 prepared/base/files、模型尝试输入、维护工作目录、冻结快照 | 业务交接确认且不再允许原任务续跑，过短恢复窗口后由创建 owner 清退；保留哈希、版本、输入来源与最终结果 |
| 读协议历史 | view/cursor/SSE 所需旧版本、REMOVE 前像、短期 changes/commits | 沿用 view 15min、tail 30min 及有效 pin，不等同业务历史保留期 |
| 采集/运行证明 | 原始轮次、网络证明、失败分母、ACK/投影/交付 | 原始 48h、摘要 7d；未 ACK/未交付责任精确保护，长期只留必要的小证明，不留整份过程 payload |
| 可再生缓存与操作副本 | 普通 browser cache、无引用构建资源、同次操作重复快照、测试中间产物 | 优先退出；不为普通缓存制作备份；身份状态、有效回滚组合不属于缓存 |

流程原则：**创建者负责声明权威结果、结算恢复责任、退役输入及派生副本。** 引用有效性以实际使用合同为准，不能让审计回执里出现过一个路径就永久保护整棵目录。权威数据和身份不因“现在没任务”而失去价值。

### 4.2 统一规则，局部实现

复用既有 SQLite 状态、retained-assets/retirement receipt、版本引用、CAS 和发布 manifest。每个 owner 至少记录本轮候选/完成/保留原因、最老可退出对象、游标、最近成功时间、错误和实际释放量。指标可以加在已有 receipt 中，不新建全局资源目录服务。

恢复窗口默认建议：成功交接的普通维护输入 24h；已明确放弃原现场恢复的失败/取消过程 72h；仍在真实续跑/人工处理中的任务不按此直接删。24h/72h 是**过程副本默认值**，不是业务成果 TTL。业务 owner 必须区分“仍承诺恢复”和“已有权威成果、可新建任务重算”，不能通过不断更新同一个旧失败对象把过期输入续命。

提交前重核实际活跃 owner/代际，短事务发布退休标记或 rename，递归删除放到锁外；失败可重试，不能先标 complete 再丢掉未完成列表。已退休任务若再次执行，应从权威输入新建 run，不能悄悄复活已删工作现场。未知归属是待对账错误，不是永久合理保留，也不是直接可删。

## 5. Read：具体故障与职责收缩（最高优先级）

### 5.1 已证实机制

生产 `v2_read/maintenance.py::run_gc` 外层预算 2s，调用 V4 `serving_gc.collect()` 未传内层预算，后者默认 0.1s。前置查询：

```sql
SELECT min(seq) FROM commit_retention WHERE projected_at >= ?;
```

生产计划为 `SEARCH commit_retention`，没有用时间范围索引。在相同生产库、相同 100ms 限制下，原查询约 100.4ms 后 interrupted；指定已有覆盖索引 `commit_retention_at(projected_at,seq)` 后约 13.6ms wall / 1.18ms CPU 完成。638 个近期 GC 周期全部 interrupted、零删除；object_history 4,836,750 条，其中按当时 30min 水位可过期 4,831,221 条。前置成本与删除 batch 大小无关，因此 10/24 的 batch 下限修复并未解决此缺陷。

部署的 `background_allowed()` 返回 True，本次不是 PSI 硬门槛。V4 已采用 scoped protocol，66 个 provenance gap、约 17,092 个 ValueError gap 不等于全库 history 冻结；不能删 gap 或伪造修复状态来放行 GC。

### 5.2 业务需要什么

前端主要需要当前摘要、分页消息与正文引用、精确发布版本、Case 决策/证据、Policy 命中、Event 定义和有限的变更协议。旧研究/交易历史需可读，不意味着必须保留每次技术轮询的 Read current 行。

代码 `api_v2/news_timeliness.py::poll_cadence` 已优先走 Acquisition observability，旧 `native:poll_runs/poll_captures/poll_schedule` 是兼容 fallback。生产 Read 样本仍含大量此类技术对象，部分 valid_from 远早于最新水位。**已证明存在旧技术对象，尚未完成其全量按 kind 字节统计；不得把抽样比例外推为精确可删 GiB。**

`serving_fields.py` 对 CONTROL 内 poll 记录直接返回完整值，不能认为薄投影已自动覆盖所有技术数据。另一方面 V4 的 objects 是 current/history 兼容 view，不能重新宣称仍有一张全量 objects 物理副本。

### 5.3 实施包 R1：先恢复 GC 进度

改动边界：`v2_read/serving_gc.py`、`v2_read/maintenance.py`，必要时针对现有 `serving_schema.py` 的索引迁移，不重建新 GC 系统。

1. 强制/改写为使用既有时间覆盖索引的范围查询，以实际 EXPLAIN 为准；不要依赖 `ANALYZE` 恰好改变计划，先不新增同义索引。
2. 把本轮剩余预算显式传入 collect，预留短提交时间；预算耗尽保留最后已提交游标/进度。floor 仅在成功事务后作为已完成进度；调整后的批次可小步提交，不能因后续搜索清理慢而回滚前面所有表。
3. 保留有效 view/cursor pin、边界 REMOVE 前像、重放幂等、definition/artifact 引用和 scoped gap 语义。不得把缩短业务历史作为修复前提。
4. 前置读取中断与 DELETE 中断分别记 receipt；batch 调节只对删除成本生效。积压追平用同一 owner 连续多批、小写锁运行，不能另开一个全库 DELETE 进程。
5. 建立百万级账本 fixture/VM 步数与 query-plan 回归、有效 pin 和过期 cursor 边界、故障后下一轮推进测试；先用影子查询证明范围有界，再生产看提交删除量与最老过期年龄。

验收：正常业务下连续多个自然周期成功提交，过期 backlog 持续下降；积压阶段退出速率高于新增历史速率，追平后过期年龄约束在协议窗口加合理维护滞后内。不要只看 maintenance running、错误消失或 retained_floor 数字前移。

### 5.4 实施包 R2：退出技术 current，缩短写入链路

1. 列实际 API/诊断消费者合同。对于已切 Acquisition 的轮询数据，停止新代际重复投影旧原始技术账本；fallback 兼容仅服务尚未完成切换的环境，不能永久双存。先验证生产观测路径与完整分母一致，再退出过期旧技术 current。
2. 需要短窗在线的技术对象使用业务时间和 owner 退出：原始轮询证明 48h，跨窗 schedule 保留正确前驱，长期诊断读 7d 摘要。没有消费者的数据不再物化。源删除/退休必须能使 Read current 同步退休；仅删 history 不会删除永远不更新的 current。
3. 初次遗留退出采用 kind+时间+稳定键游标，限批 tombstone/关闭对象；不得基于 rowid 大小判年龄，也不得误删业务 Fact/Case。现存 view 的历史可见性需先保留到 pin 失效。
4. 盘点 current/history 的同义索引。`history_expiry` 与历史兼容索引可能重复，须比对字段、谓词、实际 SQL/EXPLAIN 后删除确实无用的重复项。业务时间/身份查询所需索引保留；不按名字或“超过 30 个”直接砍。
5. 性能验收固定同一数据窗口测 Overview、消息列表、Research、Policy/Event/Case 展开；记录 p50/p95、SQL 时间、读字节/CPU、响应大小。history 大会增加写入/缓存/索引成本，但本轮没有做前端因果对照，**不保证 GC 后页面同比例加速**。

方向比较：只修 GC 最小且立即必要，但 current 技术数据仍可增长；R1+R2 能同时控制历史与无用头部，推荐。再次全量重建 Read 只能临时变小且代价大，不作为常规方案；物理压缩仅在逻辑治理后做一次。

## 6. Research/D3 与 Maintenance：由业务结算释放过程副本

### 6.1 具体组成与不必要放大

Research 29.32 GiB 主库中：1673 条 `codex_document3_v21_runs` payload 合计 **14.25 GiB**；8648 条 published_documents payload **5.04 GiB**；16287 source receipt **3.38 GiB**；1633 staged set payload **1.55 GiB**。local source 元数据、citation manifest 分别仅约 2.69MB、1.73MB；普通 artifact/attempt/bundle 记录不是这个库的主要大头。payload 字节与 B-tree 文件尺寸口径不同，不能相加后称物理可释放量。

实样 run `runtime-maintain-c649aade9b8d502095a9731b-o3` JSON 约 14.84MiB：prepared 9.35MiB、handoff 1.65MiB、base/final 各约 1.58MiB，另有重复 coverage。`StateV21.update` 每次读整份 JSON、merge、整份序列化重写；prepared 又含 files/base，发布时写 artifacts、output/final、published release，handoff 含 projection，staged set 再存整份定义。它们不是全都独立业务成果。

该 run 和旧样本均可为 `PARTIAL/STAGED`。源码 `_publish` 已写版本、校验发布文件、保存 staged，再更新 phase=STAGED；status 表达质量/缺失项，不等于发布交接是否结束。删除条件必须联查 staged receipt、正式 publish/activation/consumer 接纳及仍在途任务，不能只筛 COMPLETE。

### 6.2 实施包 D1：定义 D3 结算与瘦身合同

边界：`workflows/codex_document3/state_v21.py`、`orchestrator_v21.py`、正式消费/发布交接处及 `research_adoption.py`；不要覆盖工作区里另一项 Runtime/OOM 修复。

1. 用现有 staged/publish/activation receipt 和 run 身份明确 `resume_allowed` 与 `settled_at`，将质量 status 与资源终态分开。不必另建跨系统状态机；给既有 run 增加小型 settlement/retirement 元数据即可。
2. active/可续跑 run 保留所需 prepared；成功交接并超过默认 24h、无在途消费者的 run，把 prepared.files/base、重复 base/final、可由已验证发布内容还原的 handoff projection 移除或转为**既有不可变内容引用**。长期保留 run identity、输入版本/哈希、commit、accepted_rearm、coverage/缺失结论、结果定位、关键任务与审计摘要。
3. staged 未被消费不自动等于可删。明确转为已放弃的非金融维护责任后，保留小型失败/候选回执，默认 72h 后退出大过程副本；若仍承诺 same-run 恢复，保留准确输入并暴露 hold 原因。不能用年龄强制结束真实发布/交易责任。
4. `run()`/重放/导出/采纳消费者识别 `compacted` 格式，必要定义从 canonical staged/published 版本加载；不能把缺少 prepared 当成从头再次创建同一 run。已退休且要重研时创建新 run。
5. 运行中增量改善：prepared.files 尽量引用已存在的冻结输入/发布内容，同一 run 不在 JSON 里多次内嵌相同大定义；将经常变化的小状态与大不可变 prepared 分开保存/引用。只用现成 Native/immutable store，不造新对象存储服务。
6. staged/published/PolicySet 中先保留一份可完整恢复的权威定义及精确版本映射，再去重派生 projection/markdown 等可再生副本。published_documents 的内容是否唯一须按 artifact ID、版本、哈希核对，不能把 5.04 GiB 全判垃圾。

验收：结算后同一版本 Policy/coverage/accepted-rearm 哈希与消费结果不变；活跃 run 可恢复；退休 run 不复活；采纳、历史精确版本读取通过。正常结算样本的 run JSON 目标至少缩减 70%（作为工程验收目标，非已测收益）；按实际字段和保留义务报告未达原因。新维护周期不再永久增加一份约 15MiB 的过程 JSON。

选型：仅把保留期从 7d 改 2d 仍会在高频维护下放大；完整新资产平台迁移成本高。推荐“既有状态结算 + 现有不可变内容复用 + 终态瘦身”，先终态、后运行中大字段拆分，二者可分包上线。

### 6.3 实施包 D2：工作目录与 D3 同一责任结算

Maintenance 4289 个目录约 45.90 GiB，其中 <2d 23.83、2–7d 9.12、7–14d 6.16、>14d 6.79 GiB。约 **32.95 GiB 在七天内**，只清老目录并不能控制增长。超过七天的 12.95 GiB 也只是年龄候选池，不是已核准删除量。

`codex_worker/workspace_retention.py::collect` 依赖 jobs 状态/完成时间、business_holds、published bundle；成功 7d/失败 14d；主要清 context 与 attempts/*/input，保护 task/context/manifest 等命名文件和输出，显式排除 W3。每轮读取大量全局 evidence，并在候选上反复解析；完整 retained-assets marker 之后也可能跳过后来新增内容。最新已读 11/10 08:01 receipt 的 workspace runs=[]，不能据此认定所有 45.90 GiB 都有必要。

执行：

1. 由 run 的正式 settlement 触发 workspace 退出，jobs 只做在途互斥/恢复证据，不能要求所有历史子 job 永远满足同一终态才释放已结算部分。把 maintenance 父 run、`-o3` 子 run、真实发布/采纳产物归到准确 owner。
2. 输出/来源证据按 artifact/source/citation 的**具体文件**保留；run_id 只是定位，不自动保护整棵输入。复用现有不可变内容，迁移引用后保留原路径的必要可读合同，不能把 adoption/import 引用悬空。
3. 成功交接 24h、已放弃原现场恢复的失败/取消 72h 后，退出未引用的大 context/attempt 输入；task.json/manifest 等保留小型协议字段，不能仅因文件名叫 context.json 就永久保留完整大输入。唯一来源原文、有引用输出不按此窗口删。
4. 旧 marker 绑定输入/引用代际或明确禁止结算后继续写同一目录；重跑新 attempt 有自己的退出进度，不以一次 complete 永远跳过。
5. 使用现有 owner 映射做批量引用快照/按 run 查询，避免每个候选反复全量解析 evidence。准备在锁外，提交时核验业务代际和 active job，递归删除锁外。没有必要新建全局引用图。
6. 回归覆盖已发布 COMPLETE/PARTIAL、未发布失败、子 run 失败但父交接成功、跨 run citation/adoption、重试时新输入、并发提交、删除中断续跑。

收益：降低每次维护固化完整资料的常驻天数与副本数；不减少维护频率、不牺牲生成结果。先给旧目录产出 `retain/retire/reason/bytes` 清单，再按相同 owner 合同退出，避免开发完成后仍靠人工找目录。

## 7. Acquisition：48h/7d 规则为何没有闭环

### 7.1 新增实证

11/11 00:07 的只读生产探针：cycles **8,359,082**、physical_cycles **4,054,136**、units 864；只有 **1 条** unit_hours，位于 10/30 18:00 UTC。最早待退出非 SCHEDULE 仍为 10/30 18:00 的 IONQ HEAD，单个该小时仅 60 条，读取约 0.37ms；不是每个小时本身都有海量条目。ACCEPTED capture 载荷已压成 `[]`，早先样本约 5229 条仅 10458 字节，证明结果载荷压缩在工作；不可借此宣称明细账本也已退出。

`collection_plane/maintenance.py::maintain` 在一个总流程中准备 archive/cycles/captures/physical/inputs/schedule/checkpoint，最后才统一写事务。问题查询：

```sql
SELECT run_id FROM physical_cycles p
WHERE finished_at < ? AND NOT EXISTS (
  SELECT 1 FROM cycles c
  WHERE c.run_id >= p.run_id || ':' AND c.run_id < p.run_id || ';'
)
ORDER BY finished_at LIMIT 512;
```

虽然两边都有可用索引，LIMIT 约束的是**找到 512 个无引用结果**，不是检查 512 个候选。现场原查询分别 2s、12s 均未完成，12s CPU 约 11.92s；先按 finished_at 取 512 个候选再查引用，**6.85ms** 完成，507 个仍有引用、5 个无引用。这足以证明大多数被保护时，原查询的扫描成本不受 512 限制；不能把 12 秒/7 毫秒比值声称为全系统收益。

首个小时读取快、全局物理孤儿过滤慢，且物理过滤在统一提交之前，具有拖住 archive 和 cycles 退出的明确机制。日志无 retirement exception 并不证明健康：未结束的慢调用不会产生异常。由于本轮没有让原始重查询无限运行、没有生产写实验，**不声称已经测得每轮总耗时或这一条解释全部停滞**；实现时还要用分阶段耗时确认其余阻塞。

第二个独立问题：现代码每 30s 最多处理一个 binding/unit/hour，理论最多 2880 unit-hour/日；864 单元若全日活跃可产生 20736 unit-hour/日。864 不等于实际全天活跃分母，但这个设计没有证明能追上当前生产。不能只修慢 SQL 而继续保持不可持续的退出吞吐。

### 7.2 实施包 A1

改动边界：`collection_plane/maintenance.py`、`store.py`、`worker.py`，以及原有 cadence/observability 测试。

1. 所有冷物理回收先从既有时间索引按 `(finished_at,run_id)` 取有上限候选，再核验引用；保存扫描游标，遇到全受保护页也向前推进，完成一轮后可再次扫描。也可在逻辑 cycles 删除时将对应 physical ID 加入 owner 本地退出队列，避免全库重复找孤儿；优先复用现有状态，二者选最小实现，不双建两套机制。
2. 删除前在短 writer 事务中重核引用；活跃/尚未接纳的 capture、current units 的 checkpoint 保留。窗口 cutoff 固定为本批开始时间，不能扫描途中漂移误伤在途。
3. 把“冷小时摘要并退出对应细账”作为可单独提交的业务批。物理 orphan、input、checkpoint 的慢阶段不回滚已完成的小时退出；异常/超时有阶段、候选数、提交数、游标和最近成功时间。
4. 每轮处理多个已完整冷却的 unit/hour，以剩余时间和候选条数双边界循环，预算内完成就继续。按实测新增 unit-hour 率设吞吐，积压期至少达到新增的 2 倍；例如每 30s 处理 16 个完整小时可达 46080/日，仅是初始容量估算，最终以生产测量调节，不引入业务限流。
5. 同一冷小时先完整读取必要 schedule 前驱/变化、有效网络证明，按原 cadence 语义生成摘要并校验后，再删对应原始行。不能对半小时数据先写“完整”摘要，也不能把批次均值/p95 简单相加。异常超大小时使用可续读状态/分块准备，保留完成标记；普通小时沿用现成 cadence，不为了 GC 重写统计语义。
6. schedule 留跨窗口前驱与必要配置变化，旧小时摘要仅保留 7d。保留失败/延期/未执行分母；PENDING capture 不因超过48h直接删。`first_seen` 是业务去重身份，不能当普通 input 缓存一起删。
7. schema/index 先查已有索引与实际 EXPLAIN。checkpoint 表达式关联本次仍扫描（约 467ms），先限定候选/改写为正确可用的引用查询，不堆同义索引。capture 查询的排序也需按实际 backlog 验证，但当前不作为主阻塞扩大改造。

回归与验收：百万 protected physical + 少量 orphan 的扫描 VM 步数有界；共享物理请求、多 binding、24h 重启、archive 冲突、schedule 边界、失败分母、未接纳 capture、并发写/删除均覆盖。生产最老 eligible hour 前移、unit_hours 持续新增，完整提交的退出率追上新增；raw 最老时间约束在48h加维护滞后内，七天后摘要稳定滚动。进程健康和 ACCEPTED 空载荷不能替代上述验收。

## 8. Runtime：让引用保护和退出进度同时成立

主目录 58.16 GiB 中 8.27 GiB 旧副本已删；仍有主库 16.75、native-files 23.14、runtime_artifacts 8.69 GiB。Case payload 本身约 0.297GiB/25264 行，不能把 Runtime 十几 GiB 简单归因于“Case 太多”。具体 namespace/receipt 聚合部分探针超时，不能编造完整可删率。

当前已部署 `persistent_runtime_v2/resource_gc.py` 使用 `_gc_owner`/frame 的 run/root/version 映射及相关索引，已经有选择性引用与短事务发布退休，不应再新建一套相同 owner 表。Coordinator 15min 调度，实时 W3 active 时不启动/让行；collect 内仅 2s，最新旧现场 receipt 为 interrupted、无退役/压缩。游标在开始某目录时即写到 report，若引用准备耗尽预算会跳到后续目录；需要区分“检查到”与“完成”。

11/11 00:10 再从正式 Scheduler 取源码，resource_gc/coordinator 与本次基线相同。Worker/Scheduler 已由另一项任务恢复，本地有 Runtime/OOM 大量未提交变更；本文不把这些本地文件当成已上线修复。

实施包 T1：

1. 复用现有 owner 字段/索引做小字段、分 root/version 的查询；引用准备不展开全部 Native payload。与正在进行的 Runtime/OOM 修复合并语义审查，保留其 lazy decode、交易、故障恢复能力，不能覆盖。
2. 分开 scan cursor 与完成/待重试状态：中断的 root 仍可续做，不能当作完成；受保护对象推进扫描并记录原因。重活阶段拆为可持久化的小步，不要求整个分支压缩都塞进2s；重命名发布仍短锁。
3. 将短小准备/提交与较大文件压缩分开。实时 W3 期间可暂停重 IO，但不能永久阻止廉价的终态结算/标记和所有维护；用单个低优先级 owner、短写锁及有界批次取得最低进度，不给业务减并发。
4. Case/Effect/activation 保留的精确 Event Library 版本按既有 `compact_one` 能力保留；无引用整分支才退役。有引用分支可压成必要版本集合，不能因有一个历史 Case 就保护该运行的全部过程记录。
5. Runtime native-files 按实际 source-row/请求/receipt owner 释放。结算的非 W3 worker request、round input、重复 transport receipt 瘦身为结果引用/哈希/必要错误摘要；引用解除和 CAS 文件回收分步可重试。金融账本、未知提交、EXIT 义务、有效幂等及历史决策依据保持完整。不得直接 `rm native-files` 或统一删除所有终态任务。
6. 以已退休 run、当前活跃 Case、历史精确版本、失败恢复、并发新引用和中断压缩测试；生产按最老可退出 root 年龄、retired/compacted 字节和 native ref backlog 验收，不用扫过目录数冒充完成。

W3 完整研究/Context 复制的结构改造独立为可选 W 轨，不修改它也能完成 R/D/A/T。其空间代价纳入容量解释，但不成为本轮实现依赖。

### 8.1 Source transport 与 Native：保留已经有效的部分

`v2_read/source_lifecycle.py` 已有7d、consumer ACK、精确exception pin、snapshot importer 24h lease和逐表cursor；不能把所有源回执归为永久垃圾，也不应恢复“一个repair pin保护整段历史”的旧做法。当前transport日志中Bus native_exit有真实quarantine/delete，说明Native退出并非全局坏掉；Research/Runtime多轮expired=0需要结合年龄、ACK和pin判断，单独的0不构成故障。

Research的3.38GiB `v2_source_receipts` 是已核实的大副本池，但本轮尚未证明全部超期。实施D/T存量清理时，额外核对最老receipt时间、各consumer checkpoint/代际和exception pin：当前生产reader保留，旧代际由其generation owner明确退役；ack后过期源包由原expire退出，唯一业务事实/版本不删。若保留期内receipt因完整D3/Runtime过程payload反复写入而很大，优先在D1/T1源头瘦身，不另造第二套归档账本。

## 9. 其余 owner 的最小补齐

| 实施包 | 现有证据/缺口 | 执行动作与边界 | 验收 |
| --- | --- | --- | --- |
| I1 初始化快照 | `.init`18.46GiB；receipt protected293、unknown307、retired0；`snapshot_retention.py` 依 snapshot→execution→initialization 映射，manual/nonterminal 保留 | 一次遗留映射对账，按 manifest/execution/activation 确定 owner；新快照创建时写完归属。保留当前、一个必要前驱、实际 rerun pin；旧失败需由初始化 owner 结算是否放弃原现场恢复。unknown 不盲删、不永久无声保护。复用 `.init-contents` 去重/私有 fork | 未知量有可解释清单并收敛；过期成功/已结算失败有实际退出；当前/前驱真实 fork 恢复通过 |
| S1 Session | codex-home13.11GiB，sessions6.03；原 TerminalSessions 扩展 parent/fork 闭包，排除 W3；W3 registry 已有大量 DELETED，但历史 jobs 仍可能扩大保护 | 非 W3 沿用 owner 原退出；区分仍可 resume 的活跃根/模板与历史引用。已由 W3 registry 正式 DELETED 的会话，可由现有 owner 对账退出其无引用文件，不改变 W3 Context 架构。先统计 closure 保留原因；保留父子仍有实际恢复关系，不能仅按文件 mtime 删除 | active/template/resume 可用；已删除 registry 与文件残留一致；一次亲缘关系不再无期限保护所有历史后代 |
| B1 操作备份 | coherent migration owner 有边界，手工 Site/发布前副本绕开；本轮删掉25.22GiB这些副本 | producer 记录 operation/变更集/保护库/验证结果/结束时刻。一次变更前做一份一致快照而非每次试验重复复制；完成且新恢复点验证后，自动退旧同类集合。旧 incident 重复副本纳入一次迁移；现存唯一有效恢复点先保留。schema不兼容回滚需保留配套镜像/配置 | 同一操作重复运行不新增 N 份全量；失败新备份不替换旧有效集合；恢复点可读/代表性恢复通过 |
| B2 冷 profile 缓存 | 本次362profile安全释放6.65GiB；原 `site_strategy/resource_gc.py` 512→256MiB 单profile阈值不能控制聚合长尾，0.25s/10000文件可能反复前缀 | 原身份owner在实际关闭/退休后对白名单缓存退出；冷profile按最后实际使用时间定期清理（建议7d冷却），热profile沿用现有限额。扫描保存profile/文件游标，不能每轮从Cache前缀开始；profile锁+实际进程保护。Cookies/Local State/Storage/Session继续保留，不新增身份级停机 | cold缓存合计受控，扫描完整轮转；热身份不受影响，核心身份哈希一致；普通缓存不做备份 |
| P1 发布/镜像 | 稳定依赖层与release timer已有成效；六旧image仅共享层；`doxagent-`前缀可能漏`doxagent:`手工tag | 保留当前/一个有效前驱/运行容器/依赖/Compose/template精确引用；把正式临时tag纳入既有manifest/受管命名，不扩大到全Docker。BuildKit沿用现有预算，不新建prune cron。无唯一层的tag只清管理噪声，不报空间收益 | 小代码发布不重建依赖层；连续多次发布后唯一层基线稳定；有效回退组合与运行容器不受影响 |
| L1 日志/测试 | `/var/log`4.76GiB、journal4.01；测试根仅数GiB，可能有唯一采纳成果 | 复用日志producer轮转和既有journal配置，确认保留价值后限总量；测试完成后保留摘要/已采纳不可变输出，其余由测试owner退出。旧研究目录按真实 adoption/source refs，不按“研究资产”永久保护 | 无重复日志副本；错误诊断窗口保留；已采纳产物可读，测试中间目录有终点 |

真正需要长期留存但无需在线的大型唯一历史，应在独立可用目标具备后分层，引用带校验和和恢复定位。用户历史上允许 D 盘，并不代表本轮已验证其可用容量或备份完成；不把“将来归档”作为本轮清除重复输入/缓存的前置条件。同盘重命名为 archive 不减少占用，也不是独立故障域备份。

## 10. 存量深度清理：未来执行清单与收益

本阶段不追加业务数据清理。后续清理与上述 owner 修复共用规则，**不得直接复制一个全局 `find -mtime -delete`**。候选先轻量生成清单和引用理由，大对象按业务意义核验；普通缓存和已验证重复副本不额外备份。

| 对象 | 已确认规模/候选 | 后续退出动作 | 实际可回收量与风险 |
| --- | --- | --- | --- |
| 第一阶段三批 | 文件分配量40.138GiB | 已完成，不再次执行 | 文件系统净增40.105GiB；不能重复计入未来收益 |
| Bus 主库 freelist | **13.503GiB** 内部可复用页；Init另0.461 | 逻辑数据与ACK保护核对后，由各DB owner一次物理压缩/受控切换 | 已核实最确定的下一档物理潜力，约13.5+0.46GiB上界；不是DELETE还会再释放13.5。需输出文件/WAL/写入余量，非零风险 |
| Read 过期 history | 4,831,221 条；Read主库31.99GiB | R1追平，再R2技术current退出，最后一次压缩 | 行数已确认；GiB尚未逐表核定，不能声称31.99全可删。协议pin/REMOVE前像/精确版本有风险 |
| D3 run JSON | **14.25GiB payload** | D1先生成settlement清单，退休后批量瘦身，验证恢复/采纳合同，再压缩 | 业务结算且重复字段占优，空间显著；若样本均达70%是约10GiB逻辑情景，**不是承诺或物理测量**。未结算run扣除 |
| Maintenance workspace | 45.90GiB，其中>7d **12.95GiB**，<7d32.95 | D2精确输出/来源保护、交接恢复窗口、输入退出 | 老目录池及近期已结算输入均值得积极处理，但全目录不是可删输入；按清单文件实际blocks计算，不能许诺全删 |
| `.init` | 18.46GiB | I1映射对账、旧恢复责任结算、无引用内容回收 | 未核定；canonical/privatefork/共用内容必须区分。不要同时把快照目录逻辑大小与content池重复计入 |
| Runtime artifacts/native | 8.69+23.14GiB物理文件池 | T1按精确版本压缩、终态receipt/输入解除引用后回收 | 未核定；与D3/workspace有业务联系但物理文件不同，只有确实各自退出才计各自收益。金融与有效版本不得受损 |
| Research published/staged/receipts | payload5.04/1.55/3.38GiB | 保留唯一发布定义，去重等价物化；已ACK过期transport退出，保留小provenance | 不把全部判为低价值。可能与D3字段复用、同DB压缩收益重叠，应按同一库净文件差分结算 |
| Acquisition | 12.84GiB原快照，00:07已约13.01GiB页数 | A1先恢复摘要/细账退出，再物理压缩 | 很多过期但仍有当前48h和7d摘要责任；可回收GiB待逐表/窗口核定，不宣称整库12.84 |
| 剩余profile缓存 | 原池剩约3.9GiB | 等正常关闭/冷却由B2处理，不为清缓存打断业务 | 随业务重建、非永久收益；第一阶段6.65不重复算 |
| Session/旧测试/日志/镜像 | Session6.03GiB池、测试数GiB、日志4.76GiB；旧image候选无唯一层 | 分别按S1/L1/P1，有价值日志/唯一已采纳结果保留 | 低于主路径优先级；无引用image当前预期物理收益接近0，不报展示大小 |

**收益口径：** 只有已完成40.105GiB及下一档约13.5GiB freelist有较坚实的物理依据；其余是明确规模的候选池，存在较大优化空间但不能给虚假的总计。按数据库分别测“逻辑payload减少→freelist增长→压缩后文件变小”；按文件池测唯一inode的allocated blocks和最终df差分。整个DB文件压缩收益不能再加到该DB各表的逻辑字节收益上。

### 10.1 物理回收顺序

1. 修退出先于大规模删现行库；在正常写入中分批追平，保存每批已提交结果。
2. 优先评估 Bus freelist 的一次压缩，它不需要先删除新的业务行，可给后续Read/Research操作更多余量。选择现有 owner 的可验证 compact/rebuild/switch 路径；不同时对多个大库 VACUUM。
3. 估算旧库+新输出+WAL/临时文件+预计期间新增写入的峰值；仅检查“空余大于DB大小”不充分。必要时协调对应writer短暂切换/追平窗口，不停止全部生产。
4. SQLite物理文件复制必须一致；有WAL时不能直接cp主库。新库结构检查、业务key/精确版本/代表性读取对账、写入追平后原子切换；回退文件有明确到期而不是永久残留。
5. 对Read尤其核验alias代际、checkpoint、cursor/view合同；不复活已经退出的旧代际。不可逆业务内容退出前具备实际恢复点；重建型Read不为每次GC另备全库。

## 11. 执行顺序、依赖与可交付粒度

| 顺序 | 包 | 前置与提交范围 | 交付/生产验收 |
| --- | --- | --- | --- |
| 0，已完成 | 应急低价值回收 | 三批独立回执；无架构依赖 | 净增40.105GiB，身份/生产健康核验 |
| P0-1 | R1 Read GC | 仅Read两个主文件及必要回归；单独可发布 | 范围索引、预算贯通、连续真实提交和过期积压下降 |
| P0-2 | A1 Acquisition | 不依赖D3/Runtime；先 bounded candidate+阶段提交，再吞吐追平 | 百万保护行成本有界；48h细账/7d摘要真实滚动 |
| P0-3 | D1终态 + D2 owner结算 | 同一业务交接合同；先兼容reader/retired格式，再启动退出 | 新结算run不永久留完整JSON/context；活跃恢复和已采纳成果完整 |
| P0-4 | T1 Runtime进度 | 与当前OOM补丁逐项适配，复用现有owner字段/索引 | 不干扰在途W3/交易；有引用分支压缩、无引用退出、native回收有进度 |
| P1-1 | R2技术current、D1运行中大字段引用 | R1稳定；Acquisition观测路径已核验；D1终态reader兼容 | 不永久双存技术轮次，不重复重写prepared；业务读契约一致 |
| P1-2 | I1/S1/B1/B2 | 各自owner独立，不等待W3大改 | 旧unknown收敛、Session退出、备份不倍增、冷缓存完整轮转 |
| P1-3 | 存量迁移与一次物理回收 | 按第10节；每个库/文件池独立批次 | 净空间、业务引用、恢复合同三类回执；不将逻辑删除当df收益 |
| 常规收尾 | P1发布/L1日志测试、可观测性 | 在现有发布/容量服务补齐 | 版本/构建基线不倒退；明细完整覆盖、owner零进度可见 |
| 独立后续 | W3完整研究/Context复制 | 本轮不以此为依赖；另行业务/性能评估 | 只记录代价与方向，不夹带改变W3业务输入合同 |

开发时先读取最新main、生产镜像与本地dirty差异，不整体覆盖文件。每个包有单独变更摘要、必要回归、生产代码/配置版本和退出前后证据；重要代码修改按仓库要求追加 `changelog`。按依赖仅重建需要的角色，保留有效当前/前驱组合，不把文档改动当重建理由。

## 12. 容量与持续验收：避免下一次只看到满盘

复用现有容量记录和owner receipt，添加少量结果指标：

- 根盘free、净增；Read过期行/最老过期时间/已提交退出；Acquisition最老冷小时/新增与已归档unit-hour；D3已结算未瘦身字节；workspace/init未知和hold原因；Runtime待退出root/native引用；backup集合数、cold缓存合计。
- 每个owner的最近成功时间、游标、候选与完成数、阻塞阶段。只读成本昂贵的精确COUNT不每轮算，优先索引限定的最老对象、有限样本及生产者增量计数。
- 日目录扫描分组保存独立进度：workspaces、Runtime、Read等轮转，不在统一deadline后永久漏掉后半目录。部分完成明确标incomplete，不能写0或覆盖上次完整数。缓存上次完整结果并标时效。
- 告警接现有运维渠道/状态输出，不建立Codex高频heartbeat。free<40GiB或预计不足72h写入余量为容量风险；free<20GiB为紧急处理。owner连续多轮有可退出积压却零提交，或最老eligible时间持续前移失败，应先报告具体owner，再看总盘。阈值用于治理动作，不自动停业务或删权威数据。

建议将70GiB左右可用空间作为首轮逻辑治理/物理回收后的运维目标，**不是本次已达成果或保证**。当前约45GiB不能视为长期安全平台；运行余量还要容纳一次必要发布和单库压缩峰值。

验收分三层：

1. **机制**：大规模保护对象仍有界；故障/重启可续做；保留边界正确；新生成数据确有退出合同。不要写镜像实现逻辑的低价值测试，重点测生产暴露的规模、提交和引用边界。
2. **自然业务**：发布后24h覆盖正常运行及高峰，72h验证所有主要owner持续推进；七天看完整48h/7d窗口与备份/发布轮转。按低频采样取证，勿高频全库扫描。停服、版本切换、缺测必须单列，不能算完整负载稳定。
3. **单位业务成本**：分别记录每维护周期新增唯一研究成果字节、过程输入峰值和结算后常驻量；每采集unit-hour明细/摘要字节；每发布新增唯一层。cache/history/过程副本追平后应进入有界平台；剩余增长能对应唯一内容和必要历史。业务量变大可以增长，不能用“总盘七天完全不涨”作为不合理验收。

停止条件：R/D/A/T各owner已证明实际退出、必要业务恢复合同通过、容量分布可解释，并完成对应自然窗口后关闭该包。未完成观察写明确边界，不以“做过大量测试”提前宣称长期治理完成；也不因等待七天观察而继续无关扩张开发。

## 13. 证据索引与未确认事项

本次结论以已读取的生产源码、只读SQLite查询、文件分配量与历史执行回执为依据。没有在第二阶段调用会初始化/迁移数据库的owner构造器或执行清理dry-run；SQLite仅RO连接，查询设置进度预算，昂贵聚合超时保留缺口。进度handler不能硬中断所有内核IO/COUNT优化路径，因此后续探针仍需外层墙钟边界，不能把SQL预算当绝对运行上限。

主要证据目录：

- `eval/disk_regrowth_20261110/`：inventory/capacity历史、du、数据库页数与freelist、payload_sizes、mechanisms（Read计划对照）、gc_and_samples、owners、deployed/small_queries、image_candidates、final_checks。
- `eval/storage_recovery_and_plan_20261110/`：三批执行回执；`acquisition_probe.json`（阶段查询/计划、835.9万cycles）；`acquisition_bounded.json`（原查询12s中断与512候选6.85ms对照）；`final_state.json`（00:10磁盘/运行容器/实际Runtime源码）。
- 远端 `/var/lib/doxagent/releases/capacity/`：历史根盘/DB样本；目录明细不完整已在本报告明确。

尚未声称完成：Read每kind全量物理页归因、Runtime每namespace/Native文件的全部owner闭合、全部Maintenance目录的可删文件清单、Acquisition原慢调用最终耗时、前端慢与历史膨胀的因果实验、唯一生产历史的完整灾备恢复演练。这些缺口不妨碍先实施已经硬证据定位的R1/A1/D1-D2，也不应被用来把全部大对象笼统划成不可清理。

本轮交付是可执行设计及已授权低价值清理回执；未修改产品代码、部署配置或业务数据保留规则。
