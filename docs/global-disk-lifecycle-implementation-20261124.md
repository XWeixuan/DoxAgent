# 全局磁盘生命周期治理实施回执

依据：[完整方案 v3](global-disk-lifecycle-repair-plan-20261123-v3.md)。本文件区分代码、部署、即时业务验证和连续交易日验收，不能把清理收益或单测当作长期收敛。

## 1. 交付边界

- O3 持续关闭，Control `maintenance_control.enabled=0, revision=1`；没有以生产试跑、迁移或验收为由重新开启。
- O2 正常更新 Event，并继续引用既有正式 D3；Event-only activation 不重放 D3 的 accepted rearm，不修改有效消费代际。
- 改动保留近期 Runtime/Bus 延时修复以及最新 main 的可信归属、FAILED EXIT 闩锁和摘要补齐逻辑。合并基线为 `9bde19577`。
- 主要产品源码：`f15d9191aa10f378ef06ea8a7788c3825f258b16`。`3ea3117bd` 增加验证并消除方案中的统计留存、SDK 窗口文字冲突；最终 `66d53819646d5c54a97e9ebc3ec690b99f9e5243` 修正实际 Transport 入口，见第 10 节。
- 隔离开发目录 `DoxAgent-storage-20261123`，分支 `codex/storage-lifecycle-20261123`。主工作区已有其他会话的业务修改，未覆盖或清理。

## 2. 已实现的主要增长源修复

| 包 | 实际改变 | 收益口径 |
| --- | --- | --- |
| M0 | O3 创建/消费关闭；保留每 ticker 的小型待维护义务；O2-only activation、分阶段 ACK、授权复用与迟到 O3 采纳栅栏 | 截断新 O3 工作区、Context、feed、staged 发布过程增长；没有把旧 O3 存量视为已经释放 |
| E1 | O3 关闭时 O2 直接使用生效 Event 仓库，不再逐 run clone SQLite；相同实质内容不新增 revision，关系按变化更新 | 新无变化分支为零，真正变化仍有正确身份、时间与业务引用 |
| Event 历史 | 原 revision/state/区间复用；第一获知时间和小型 publication clocks 脱离完整祖先库；已采纳 Batch 丢弃重复正文，保留映射与重放身份 | 保护当前、精确活跃引用，以及七天内最多三个实际版本；不承诺全部 lineage 或退出历史的恢复能力 |
| D3 | 每 Policy 复用已有 Native CAS；formal/staged 共享；历史 projection 按需重建，当前热投影保留 | 减少大正文双写与跨版本重复；实际新写量和旧页回收分开验收 |
| D3 历史 | 既有版本退出队列；当前正式/Control/活跃 Case 和缺真实冻结证据的具体旧交易版本被保护 | 无引用历史退出并明确 `HISTORY_RETIRED`，不因为祖先关系永久保留 |
| Workspace | D3 导航改为引用；不重复导出大型状态正文；typed/终态 owner 的输入目录可局部 detach 后分批 unlink | 自动 24h 窗口及精确 Worker 提交 fence；不为字节一致而保留重复展示文件 |
| Runtime | `frozen_inputs` 按不可变字段复用 Native 内容，两个 Case 保存入口均覆盖；Read 只展开实际投影字段 | 真实生产 Case 的隔离 12 次状态变更回放，Native 新增 6,441,062→535,481 bytes，减少 **91.69%**；编码时间 1.233→0.573s。不是日净增长证明 |
| Read | 同 D3 版本不重复 repaint Policy；business fact 每步 16 revisions/128 rows/1MiB，子游标续跑；精确撤销 tombstone | 去掉 Event-only activation 的大规模重复投影，保持实际 KPI/Case 与删除语义 |
| Runtime 查询 | 不展开 O2 supplement 的完整 parent/Case/effects；恢复只读小页成功元数据和实际失败 ID；Selection 先筛 SQLite header | 保留并发和业务准入语义，避免因磁盘治理倒退启动时延 |
| T1 | 过期时间不再等同 seq 游标；游标前年轻记录到龄后可重访；保留真实 ACK、exception pin、epoch 和恢复租约 | 已 ACK 的过期行不再永久滞留；无索引也有有界轮转路径 |
| A1 | 成功薄证据 2h、异常薄证据 72h、48h 去重；错误正文/headers 不全量落盘；先封存小时统计，再退出明细 | 保留当前七天 `unit_hours` 消费者；没有消费者的 90 天日报未新增 |
| S1 | Native DB sink 默认 WARN，诊断/feedback 约 2KiB 摘要；按主键窗口有界退出，取消逐分区 SUM/window | 限制新日志工作集；不会把旧日志库约 4GiB 文件立即变成 512MiB |
| 本地 SDK root | 新建、无子线程、无外部复用且全 owner 终态+ACK 的 root，登记精确退出证明，调用原生 per-thread 删除 | 真实 SDK 不打开无关 rollout，避免每次退出遍历全局会话；W3 线程业务生命周期不改 |

## 3. 性能边界落实

Runtime owner 退出、Event 迁移和旧 Case 证据核验由已有独立 Transport Maintenance 进程执行，不在 Scheduler 的 Python 解释器/GIL 中执行。新旧数据库 ready 后跳过重复 DDL；后台 Runtime writer 的 SQLite busy 设置 10ms，忙锁退出。10ms 是配置的锁等待上限，不是操作系统调度后所有墙钟时延的保证；一次手动存量批次观察到 23–26ms 的忙锁返回，未持有业务 writer 继续做文件工作。

物理删除按实际目录/文件条目计数，每步至多 256 条目/约 50ms，不能一个根目录算一项后整树 `rmtree`。大输入区在 Worker 提交栅栏提交后锁外 detach，再续删。硬链接只在最后一个链接移除时计预计物理收益。

旧 Case 迁移以主键小页和真实 trade 主键关联，普通非交易历史不展开大型 Context。单次 Case 正文最多读取 256KiB，超大旧 Native 对象保留精确版本 pin，不为检查而装载几十 MiB；处理前缀持久化，完成后不重复扫描。只保护真正缺证据的具体旧版，不保护所有历史 PolicySet。

空队列/无变化不写空回执。Read、Native、过程和版本退出均有忙锁退避与页边界；不依赖“全局完全空闲”。没有新增全局协调器、全库定时扫描、每秒清理查询或 W3 启动前置治理 RPC。

## 4. 固定 Native SDK 构建与真实验收

- upstream `01fc69f4026735edfdf6789820549727a4867b11`，Codex `0.159.3`。
- 原 W3 timing patch SHA256 保持 `3ea16e781030712fd678f5d7208b1ea76bffe740babb5a24fb9ed484c711b6db`。
- storage patch SHA256 `23e4f5c65247340f810717de5c8fd6eb59c28d00df492c0da047e7aa3a64cc03`。
- 最终二进制 SHA256 `f352d9aed80d99375078fc8fdb6ebee1b8142e573d869f703135e49e74f6919c`。
- companion 保持既有固定 wheel 的 `fe64bd08c7844ca1fca91d8cb825e62012a03a05ae8040c635bbb61e5be0b4c5`。

本机构建完成后，在 `--network none` 下用 loopback synthetic Responses SSE 验证 initialize→thread/start→turn/start→完成→thread/read/delete。未调用真实模型、未产生评估费用。删除后 rollout 不存在，四个 thread-history 表目标线程行均为零；即使 `RUST_LOG=trace`，DB 只出现 WARN/ERROR。inotify 观察无关合法历史 rollout：旧实现对照失败，最终二进制打开数为 **0**。证明本地 root 快路径实际避免全树扫描，而不只是 Python mock 通过。

Native 仅在本机构建，服务器只安装最终已核验产物。安装采用目标文件系统内 staging 和原子替换，未在生产编译 Rust。运行容器通过正式镜像替换获得产物，没有原地修改产品代码。

## 5. 发布记录与兼容边界

按 B0/B1→读端及维护角色→Runtime/Worker 写端顺序分批发布，O3 始终关闭。发布使用 `/var/lib/doxagent/releases` 的既有锁、精确 git archive、组件清单、current/previous 镜像登记和资源退役能力。

2026-11-24 01:53–01:56，`f15d9191a` 的 API、Projector、Read Maintenance、Transport Maintenance、Control、Initialization、CDECR Executor、O4、Collector 已更新到镜像 `sha256:cb062d7b8a887e3f8a22e37453144d4408d60222ae1e4b1bca2516d53672bee1`。Worker/Scheduler 的最终切换和共享格式开关结果在本节末追加，不能把准备好的镜像当作已运行。

首轮 Read 发布暴露真实缺陷：生产 CLI 路径不会自动执行新增 Read DDL，导致 Projector 因缺 `serving_fact_exits` 表重启。已执行兼容迁移恢复，并修复发布 owner：以后替换 Read 角色前先在实际 active DB 安装 DDL。没有以“测试通过”掩盖这次发布问题。

Acquisition 新 success-time partial index 在 Collector 单独停止的发布边界安装；Bus、BODY、Runtime 未因此停止。Research/Runtime 的大 outbox 时间索引未放进启动或日常 tick：现有有界 seq 重访已经能推进，当前先复用该路径，不在繁忙 writer 上为索引全表施工。

Worker 切换门禁拒绝中断 queued/running 模型。为捕获空窗，只临时延后未开始的 Maintenance，原 due、输入和身份保留；已有 O2 和 W3 模型继续执行，W3 Case 准入不变。临时栅栏有墙钟失效时间，最终必须显式恢复真实 due 并删除临时表/触发器。

共享 D3 写入由 `/data/research/.d3-shared-policy-ready` 控制。全部实际读写角色及选定回滚镜像兼容后才能打开；O3 开关与此无关，不通过重开 O3 验证新格式。

## 6. 存量关闭验证与空间口径

1. MU 小批次释放约 25.55MB，保留五项正式发布成果；只作为边界试验。
2. 当前正式 D3 家族批次退掉 14,985 条文件/目录项，确认最后链接释放约 176,541,696 bytes；本轮 `df` 净变化 8,167,473,152→8,370,688,000 bytes，还包含同期业务写入/退出，二者不相加。
3. 首个旧 D3 家族页退掉 18,311 项，预计释放 195,981,312 bytes，`df` 净增约 190.66MB，零错误。精确 scope、引用、typed publication 和终态 Worker 逐家族判断；未扫描所有 Case 或复制全量备份。
4. 首次 Runtime 旧 owner 批扫描 192 条历史任务候选，约 40 个完成过程退出；扫描数不等于成功登记数。其主/O2 Workspace 多数已在以前退掉，不能因为“40 个 owner 完成”就宣称释放几十 GiB。一个缺旧 Runtime Worker receipt 的历史重试请求暂不能完成，记录后保留，不扩展成新的高优先级架构项目；同页其他 owner 继续推进。

旧 Read 路径 `/data/read/v2.sqlite3` 是 active serving-v4 库的符号链接，**不是另一份可删的 22GiB 旧库**。本轮未误算、未删除。Runtime/Research freelist 的增长是可复用空页，不等同根盘可用空间增长。

当前仍保存的独立 Event SQLite 祖先分支、旧 D3 已发布展示文件和来源不明历史 SDK root，不能混称已全退。新增分支/正文生产已从源头截断；旧资产在完成具体引用与第一获知证据转换后按 owner 继续退出，不以永久完整 lineage 为保护理由。

## 7. SQLite 物理回收决策

| 库 | 本次决策 | 理由 |
| --- | --- | --- |
| Runtime | 复用已释放页，不在线改 auto_vacuum、不在热 writer 跑全库重建 | 约 37.37GiB 物理文件已产生约 2–3GiB freelist；目前写负载仍活跃，源头减量与有界退出先上线 |
| Research | 复用约 3.63GiB 空页，D3 退出/转换继续后再做一次明确维护窗口决策 | 当前约 30.29GiB 文件；不能在共享格式转换和业务读取同时进行无收益的自动缩库 |
| Read | 保持现有 active 库，不为本轮另复制整个代际 | 同 D3 重复投影停止后观察页复用；已有贡献表存在真实晚到/更正消费者，未机械删除 |
| Acquisition | 成功/异常边界、小时封存和页复用验证；不新增周期 VACUUM | 对应 96h/额外日复用测试，明细退出不会被年轻 failure 阻塞 |
| SDK | 沿实际 SDK 自身维护，不改活跃私库模式、不手删 WAL | 工作集收敛与物理归还是不同验收；当前约 4GiB 日志库不冒充即时 512MiB |

本轮没有证据表明对全部库启用 INCREMENTAL 能抵消新增写入/锁成本，因此未增加持续负载。需要归还大额持久空页时，单库明确估算临时余量、停写范围和恢复责任，再执行一次 compact；不会在 Runtime 忙时用 nice 掩盖 writer 长锁。

## 8. 验证结果与已知基线

以下套件相互重叠，不累计成总通过数：

- 最终 source/owner/Read/退出/发布边界：49 passed，31.16s。
- 最新 main 交易保护合并后定向套件：68 passed。
- Acquisition 当前边界与去阻塞：23 passed，73.91s；另有 96h cohort、下一日空页复用场景。
- 旧交易精确证据及退出保护：22 passed，6.18s；D3 共享/历史独立复验 5 passed。
- 实际 Control 仓库集成入口及 Event/D3/引用边界补验：9 passed，5.06s。
- 原生 SDK 正负对照见第 4 节；Runtime 真实 Case 字节回放见第 2 节。

运行测试明确设置 isolated tree 的 `PYTHONPATH=src`，避免 venv editable import 意外跑主工作区旧源码。

全量测试存在与本包无关的固定日期/已过交易时间 fixture 失效、NOOP/旧错误码期待和 D2 payload 旧期待。消息入口旧日期测试已在未修改 main 源码上复现相同失败。未为磁盘交付改动交易日期或放松业务 guard，也未声称全仓所有测试全绿。

## 9. 即时与长期验收

即时验收已取得 O3=0、O2 自然完成/activation、十个 ticker 的 D3 版本不变、真实 W3 成功、Read 四源推进、修复后关键角色无新循环重启、Native 实际配置及 hash、自动退出推进、临时栅栏移除的证据，明细见下节。授权不重放由定向回归覆盖；单次自然运行不能代替全链路时延和长期容量验收。

最终收敛仍按方案的连续五个正常交易日观察实际物理净增、bytes/run、freelist 复用、退出吞吐及 W3/Bus 时延。当前部署完成或一轮释放空间不能证明该项通过。复用既有 release capacity 与业务回执，不新建高频轮询、模型评估或临时监控框架。

## 10. 发布收尾补记

### 10.1 最终切换

2026-11-24 02:32:10–02:33:32（北京时间）完成 Worker/Scheduler 受控切换，最终均为 `f15d9191a` 的 `cb062d7b…72bee1` 镜像。等待自然空窗期间，未开始 Maintenance 的 due 临时延后；窗口结束已恢复 75 条仍保持待执行的任务，另两条已自然开始，不强行改写其状态。相关临时表/触发器已删除。

核验发现 `w3-ready-*` 可以已经绑定真实 Case 并处于 `post_decision_operations`，不能按名称当作可丢预热。首次切换被该证据阻止，未取消该 Case。实际切换时只剩两项 O2 维护 Job，通过现有 DELETE API 记录为真实 `cancelled`，持久输入、Task 和重试语义保留；没有伪造成功或 ACK，没有中止真实 W3。Scheduler 正常停机，约 4.6s 完成已接受调用退出；新 Scheduler 首次消费在 02:33:19，整个发布窗口约 82s。

Worker 实际配置选择 `/opt/doxagent/codex-runtime/codex`，容器内 SHA256 与第 4 节最终二进制完全一致，原 timing patch 未变化。

### 10.2 共享 D3 真实 canary 与回滚

全部读写角色兼容后启用 `.d3-shared-policy-ready`，O3 仍为 0。生产当前 D3 应通过 Control 指定的版本读取，不能用旧 formal 表的 `is_current` 代替 activation。MU 当前正式引用为 v572，实际 staged typed release 转换前后模型数据完全相等：

- 语义 SHA256：`d65556f6bd410d128125de25841dec35e58bd8f706fe456d5b46a6fc5c043110`。
- 版本行 JSON 1,703,660→28,551 bytes；正文转入共享 CAS，因此不能把行缩小 98% 宣称为整盘即时释放 98%。
- 该次 Research writer（包含等待及提交）0.124s；是一次实际迁移测量，不宣称达到 10ms 墙钟。未占用 Runtime writer，版本、内容、发布 receipt 不变。
- 选定已兼容的 `5c030feeb` / `8a4f0b46…b0f72` 镜像，在 `--network none`、生产数据只读挂载下，成功读取同一共享 v572 并得到相同语义 SHA。回滚不是直接退到不认识共享格式的 B1/旧 Worker。

Worker、Scheduler 和 Transport 的 rollback inventory 已指向兼容版本；这是已验证的恢复 reader，未伪称这些角色以前实际运行过该镜像。紧急回滚可能暂时减少新的退出能力，业务读取仍应正确。

### 10.3 真实退出入口缺陷与修复

活跃引用迁移完成后，生产首次进入 Event 历史路径，暴露 `InitializationRepository` 没有 `_read()` 的 AttributeError，影响 Transport 循环到达 D3 退出。只暂停该后台角色，保留 Runtime/Bus/Worker 服务；改用其实际 `_connection()` API，增加真实仓库集成回归，9 项定向测试通过。`66d538196` 已推 origin/main、同步服务器，并只构建/替换 Transport Maintenance；没有扩大到其他业务重构。

修复后日志实际显示：Runtime 终态 owner `done=true`、零错误；Event 第一获知证据逐 ticker 迁移；D3 自动退出已退 BE v6（非当前、无必要精确 pin），并完成一个旧 D3 owner 的过程退出。不是仅看后台进程存活。

三次有界低优先级迁移推进后，旧 Case 证据审查和 condition highwater 均已完成。遇到 Runtime busy 时让出并保留游标；最后批次 259 页、零错误。完成后不再重复扫描全部旧 Case。

当前十个 ticker 的 formal/staged 精确版本 Event header 均核对相同；保留当前 D3 所需 Event 版本。未为假设的不一致再扩大实现。

### 10.4 生产业务与空间快照

2026-11-24 02:48 自然回执：IONQ O2 activation 已在最终发布后完成，`SKIPPED_BY_CONTROL`，D3 仍为 v655，复用生效 Event 仓库；其他 ticker 的 D3 版本与切换前相同。新 Worker 已有至少两个真实 Case 的五个成功阶段 Job 样本；这是有 Case 绑定的业务成功，不能拿无主预热冒充。没有额外发起付费模型测试。

Read 四源 head/checkpoint 相等且 `checked_at` 持续前进、error 为 null。修复后 Worker、Scheduler、Projector、Transport 的新容器 restart count 均为 0。历史 Read gaps 仍保留，不把未知与历史问题伪装成已修复。旧业务 fixture 与长期时延边界仍按第 8–9 节说明。

发布 owner 已实际清退旧 B1/Worker/W3 临时镜像及确认不再使用的本次 SDK 上传文件；保留当前、兼容回滚和真实动态引用，不清 volume/profile。最终根盘可用约 **9.0GiB**。这是实际余量，**不是完成全部大规模存量退役或达到 50GiB 的证明**；SQLite 空闲页、文件估算和镜像共享层不重复相加。

### 10.5 未结束的长期收尾

本轮核心源头写入修复和生产即时验证已落地，后续继续由已部署 owner 推进 Event 第一获知迁移、旧分支/版本、D3 过程与 Native 的有界退出。独立旧 Event 分支、历史派生文件及无明确 owner 的旧 SDK root 尚未全部清退；大规模存量退出和必要单库物理缩容不能标作已经完成。

连续五个正常交易日的容量/时延验收尚未通过时间条件。尤其不能以 9GiB 余量承诺长期容量安全，也不能把隔离回放的 91.69% 减量直接当成整盘净增长改善。优先追踪 M0/E1、D3、Workspace、Runtime frozen 写入和它们的退出吞吐；不再因边际收益很小的缓存或新发现的次要机制延长本轮开发。
