# 服务器磁盘修复执行记录（2026-10-13）

> 主要实施完成：P1/P2 已窄发布，P3 已完成历史回收和 Bus/Read 物理切换，P4 已重新审计、精简并部署。按用户“差不多得了，主要任务完成了就行”的最新指示收尾，不扩展性能治理或等待长期观察。Read 尚有待追平积压，一周自然增长验收尚未完成；未安装此前拟议的定时任务。

## 执行边界

按 `disk-rootcause-and-repair-plan-20261013.md` 授权执行。保留当前活跃 MU/INTC/BE/RKLB 研究、RKLB D3、Guardian 动态镜像/模板、交易/Policy/研究/新闻正文和四条真实 runtime gap。脏工作区中的其他修改未整体发布或提交。API/Site Access 的同期修复镜像继续使用原版本。

## P0：施工余量与恢复集

- D 盘补齐 10/12 的 Bus 2,590,263,328 bytes 和 Read 4,680,360,216 bytes gzip；分别验证 SHA-256 `31e0a47c5c63eaa2050da7d1c46d24db8dab7e97df7807cd0d143cee9b7b425a`、`27e124367d861df23ddf5ee702e8a2e0564f325d69bee5dc6d30459ae6b204e7`。
- 核对已有 research/initialization/runtime/business-files/event-config 恢复文件；退役已验证冗余的服务器压缩副本。business-files 服务器副本在本轮前已不存在，未据此重复宣称本轮回收量。
- 按精确 image ID、现有容器和动态依赖检查，成功退役 31 个旧镜像；4 个多标签冲突镜像未强制删除。共享层导致实际收益约 0.8 GiB，不能将镜像显示大小相加作为回收量。
- D 盘是用户指定的冷存储位置，当前容量有限；本轮恢复集和归档均有哈希凭据。

## P1：停止重复分发写入

- REALTIME 使用稳定文章/内容版本、binding、terms、source/admission 和 classifier 语义组成业务键，重复轮询只更新 last_seen/次数。保留 legacy 成功/有效待处理 ID，CLOSED_SWEEP 仍按自己的窗口/运行身份处理。
- 完整 source/roster/binding/admission 采用不可变快照引用；重复 observation membership 集合去重，保留迟到 attach 语义。
- 过期且没有有效目标的文章不再创建无意义正文任务；真实正文/日期/词表修改及有效补扫仍产生必要处理。
- 线上阶段验收：1,491 个新业务键承接 29,037 次观察，单键最大重复 29 次；后续截至 UTC 22:12 已为 1,515 个键承接 90,315 次观察，单键最大 119 次，已发布数正常推进到 1,325。重复轮询未再次按观察次数生成完整 delivery。此处是短期实测，不代替一周容量验收。

## P2：受约束的技术历史与压缩归档

- poll_states 纯调度时间变化不再反复 capture；成功/错误/状态/计数等变化继续 capture，保留 freshness。
- Read GC 纳入正常生产装配；在四条 runtime provenance gap 下，仅允许无 Bus gap 的过期 Bus poll 技术版本回收，业务历史与聚合仍受保护。视图/cursor 及 REMOVE pre-image 保留窗口不变。
- GC 使用既有索引、短事务与有界扫描，不在线构建全表大索引；输出真实回收数、floor、保护原因及错误。SQLite contention/时间预算中断会回滚重试，未伪报全量成功。
- 源运输归档受消费 ACK、完整异机恢复 checkpoint 和精确 repair pin 约束，压缩分包可由 read/event/coordinates 按原 seq 回读，备份包含分包。Runtime pin 1240145 继续阻止相关回收。
- 已从 D 盘真实 gzip 流验证压缩/解压 SHA 和 SQLite schema/source epoch/head，提取 Bus head 2259296、Runtime head 1801607 的带恢复文件定位 checkpoint；没有伪造四条 gap 的 provenance。
- 现场曾出现一批 Runtime 分包超过 16 MiB；已改为按实际字节自动缩小批次，单条超预算仍保留原件并显式报错。上线后 Bus/Runtime 归档均继续推进。

## P3：历史整理与物理回收（已完成切换）

10,888,166 条超过 24h 的 REALTIME ADMISSION_REJECTED 完整记录已导出到 D 盘：

`D:/DoxAgentPilot/backups/storage-archive/realtime-rejects-20261012T193459-56169bea.jsonl.gz`

压缩大小 677,700,647 bytes，SHA-256 `56a3b22fbd1506628df24344a5d199492bdf40fc0df32c81ce404c9cda51ae31`；逻辑 SHA-256 `749f6901744799d38ba996c94a2258c7259524b775092239435ca6f2277d56e9`。异机校验 ACK 已上传；回收逐行核对现值与归档原值，变更记录保留。未纳入 sweep、已发布、待处理、文章或决策原件。

Bus 使用受控 VACUUM INTO shadow，关闭句柄后核对各表计数、业务/运输摘要、epoch/head，再同文件系统原子替换。Read 使用流式 shadow：保留 generation、public seq、checkpoint、gaps、业务历史及 token；只略去安全窗口外 poll 历史和已有读取路径不使用的 changes 重复 payload，按现有 NativeContent 格式外置压缩大字段。复制期间暂停 projector/GC，API 继续查询，最终短停 API 同步新增 token 后切换。

| 项目 | 切换前 | 切换后 | 验证 |
| --- | ---: | ---: | --- |
| Bus 主库文件 | 21,367,558,144 bytes（19.90 GiB） | 6,333,575,168 bytes（5.90 GiB） | 全库 integrity_check、各表计数及完整业务/运输摘要一致，epoch/head 不变 |
| Read 主库文件 | 31,672,459,264 bytes（29.50 GiB） | 24,614,690,816 bytes（22.93 GiB） | 所有复制表有序摘要、重建索引、quick_check；原 public seq/generation/checkpoints/token 保留 |

Read 仅略去 1,465,545 条已过保留窗口的旧 Bus poll 版本；大字段转换涉及 objects 3,801 行、object_current 365 行、business_revisions 14 行。通过真实 API 镜像读取切换前 public seq 4400496：96 类共 288 个对象结果相同，Fact SQL 身份 145,761/145,761 保留，四条 gap 的 source/event/reason 不变，healthz/readyz 返回 200。79 个 view 和 41 个 cursor 在最终切换前同步。验收后确认无旧句柄，直接退役两库旧物理文件。

Read 构建/索引期间 projector/GC 共暂停约 65.9 分钟；API 在复制阶段继续查询，仅最终同步 token 时短停。Docker 记录 API 停止至再次启动约 0.487 秒，该数字不包含 HTTP 就绪耗时，不能声称完整对外中断仅此时长。业务源与活跃研究继续写入。切换后根盘可用曾达约 39.65 GiB；最终 UTC 22:23 根盘可用 41,546,547,200 bytes（38.69 GiB），含后续正常写入及 Guardian Docker CLI 依赖层。

### 执行中发现的问题

第一版历史拒绝导出 JOIN+ORDER BY 触发大型 SQLite 临时排序，瞬时消耗约 10 GiB，使根盘短暂满。立即终止本轮自己的导出进程，临时空间释放。随后改成强制 rowid 范围扫描和千行批次，并验证成功完成全部导出。没有因此删除业务历史或停止活跃研究。该事件必须计入本轮施工复盘，不能只展示最后 df。

首次 Bus shadow 完整性检查后，WAL checkpoint 在同一连接上的收尾出现 `database table is locked`，切换未发生，finally 自动恢复相关服务。修正为关闭校验连接、使用独立新连接完成 checkpoint，已在服务器 Python/SQLite 上实测通过；旧 shadow 因落后于恢复后的写入被安全退役，按新基线重新构建。Read 流式 shadow 也在实际生产镜像中通过小库 smoke test。

## 验证

P1–P3 针对性测试最终 31 passed，涵盖分发幂等/真实修订/terms/sweep、poll CDC、GC gap/token、异机归档 ACK、checkpoint 哈希、transport 原坐标和备份回读、超大分包缩批、Read shadow 身份与 token 保留及争用恢复。附加验证 shadow 的生成列复制和 AUTOINCREMENT 高水位保留。

P4 针对性回归 37 passed、1 deselected；被排除的既有测试将 NOW 固定在 10/5，却以实际 10/13 updated_at 建立 Incident，导致“30 天前”条件不成立。以一致的当前时钟单独复核该测试通过，未修改该无关生产逻辑。Policy 查找优化另有 7 passed；最终 Compose YAML 解析及关键修改文件编译通过。真实 Linux SQLite 压缩备份恢复和 Guardian 实际创建日志轮转容器均通过。

证据目录：`eval/disk_repair_20261013/`。自然业务/发布/备份至少一周的增长验收仍待完成，不能据本次采样承诺有限磁盘永不满。

## P4

已按 [精简 P4 审计](disk-p4-retention-audit-20261013.md) 执行：

- 正常迁移入口按含 WAL 的 SQLite 实际页数估算备份及 immutable 文件；成功后压缩、逐流 SHA 校验，只退役同入口、同原库集合的旧成功回滚集。失败/不受该入口管理的副本保留；压缩在业务 writer lock 释放后执行。同版本重启沿用快速路径。未为验收再执行一次全量生产迁移。
- 新受管初始化/消息源修复容器统一 json-file 20m × 3。Guardian 的真实创建 smoke 暴露基线镜像没有 Docker CLI，已在规范专用 Dockerfile 补齐已有 source-maintenance 使用的 Docker 27.5 CLI/插件，真实创建、inspect、退出、删除 smoke 通过。未重建活跃研究 Worker，现有 Worker 参数随正常重建生效；未启用不活跃的 source-maintenance profile。
- Migration/Transport 使用 Read R9；Guardian 使用专用 R2。命令、环境、挂载、网络保持原配置，动态 agent 镜像不变。现有 storage_report 补齐实际备份入口。按精确镜像及动态依赖收尾本轮 7 个中间镜像，实际仅约 1.4 MB 共享层净收益，未夸大成 GiB。
- 不新增 SDK 内部清理器、全局 prune、CI 平台、浏览器缓存清理重启、任务磁盘配额或 Codex heartbeat。正式 adoptions 和活跃工作区保留。

### 收尾时的投影积压与验证边界

Read 暂停积累了源事件，恢复后的正常消费继续推进，但尚未追平。现场一次 cProfile 定位到既有 Policy-hit 投影每个 Policy 反复加载整套明细（262 次，约 8.5 秒）；已改为每条 Case 只读取一次并保留原先首次匹配语义，7 项针对性回归通过，仅 Projector 窄发布 R10。复测另见既有 Event Library reference 投影单条约 13.6 秒，按用户最新收尾指示不扩展修复。

UTC 22:23:20：Bus checkpoint 2,278,692 / head 2,291,816（积压 13,124），Runtime checkpoint 1,805,038 / head 1,806,401（积压 1,363）。两者在推进，数据保留，不能声称完整追平或查询完全实时。Projector 已恢复正常后台运行，无留置的一次性 writer；四条旧 provenance gap 与精确 repair pin 仍保留。一周自然业务/发布/备份增长验收和完整恢复演练未完成。

### 最新低价值副本直接退役授权

按用户后续明确指示，低价值副本不再另做备份。核对新 D 盘正式恢复集、旧维修已完成、精确文件路径、无进程句柄及无容器命令/挂载依赖后，直接删除 9 月至 10/6 的旧 Bus/初始化/Runtime/Read 维修快照；合计 9890166609 bytes（约 9.21 GiB）。未删除正式业务原件。为保障正在进行的 Read 索引施工余量，提前执行了这一已审计清单。小型旧验收收据保留。证据：eval/disk_repair_20261013/obsolete-snapshot-retirement.json。
