# 存储源头减量开发回执

实施基线为 `b99d0dbea`，包含 `main` 已交付的 W3 非模型延时修复。开发位于 `codex/storage-source-reduction-20261118`。首次提交是 `b1aaddaa2`，后续开发为 `1b0494dae`。已完成项现已发布生产，现场另补充采集恢复索引修正 `62bdc9b047cd`，见 [发布回执](storage-source-reduction-release-20261118.md)。下表中的剩余开发与长期量化指标仍按实际状态保留。

| 工作包 | 本轮交付 | 待完成 |
|---|---|---|
| P0-01 D3 与 Maintenance | 取消逐轮过程视图和生产 `output/final` 重复输出，Pilot 按需生成。已消费、同族 Jobs 终态且通过 manifest 校验的 D3 使用同 inode 保留旧发布路径，退出派生视图。非 W3 已消费 Maintenance Job 退休重复请求和事件，保留业务结果与用量 | 工作区其他过程输入仍由原有快照和复用规则保护，SDK Session 大树扫描待收敛。生产新增长至少降 70% 待实测 |
| P0-02 Research 正文 | SQLite 发布入口支持 SHA 寻址压缩正文，兼容旧 inline 和已有外部存储。读取、adoption、W3 编译、备份以及评估导出支持新引用。历史转换命令每次处理有限页面 | 后续发布时统一启用 writer，实测压缩与写入规模，迁移后再审查旧副本 |
| P0-03 Acquisition | 相同 unit state/head 跳过重写，物理诊断只随一个逻辑结果保存。现有 `meta` 保存一小时的薄证据分页，重启续跑。真实在途周期阻止 seal，上一代有有效列表证明的中断周期保留 `CAPTURED`，单独记录运行中断。完成时核对终态水位，提交精确 cadence 后再退出原行 | 旧已归档小时已有的晚到原记录继续留到窗口结束。物理/逻辑字段的进一步归并和生产 50% 块写下降待实测 |
| P0-04 CDC | Research 与 Bus 的已 ACK 热回放改为 48h，保留 repair pin、有效消费者及源 epoch。压力下每源独立短批。Native ref 按集合差量维护。归档同时记录时间，旧 pack 的近龄时间随有限页读取回填 | 生产旧库索引安装、历史 pack 的 backfill 进展及其他字段级消费契约待生产验收 |
| P0-05 Native | 小对象单 gzip 成员保持旧 chunk 可读。`DELETING` 及异常隔离目录的递归删除在 writer 外，退出队列可续跑。Transport 归档统一走 pack，锁外准备，短事务复核坐标并登记引用 | 生产物理写入、inode 与持锁耗时待实测 |
| P1-01 Runtime | Reference 快照复用 Native SHA，结算只更新状态和时点。旧版 W3 citation scope 按不可变事件内容复用，保留逻辑形状。带 Journal 的新 Case 用 round key 指向既有冻结输入，内存调用继续留原字段。Runtime 常驻退出按已有终态 run 索引分页，保留显式历史扫描和选版压缩 | 历史 Event 分支根路径及已落盘的 Case round 副本需单独核对与迁移 |
| P1-02 Read | 新小正文可同文件系统共享校验过的压缩文件。日内 Reference 链只保存下次差分的基线，完整交付证据仍可查。复用现有 `serving_fields` 薄投影和短 view/stream 协议 | 历史重投影与物理空间回收待按生产实测安排。端到端时延尚无上线数据 |
| P1-03 W3 | 编译资源能从 canonical 正文重建。无需 trade、watch 或 pin 的已结算 Case 在两阶段 ACK 后退出在线 Context 义务，可用现有 extension 重新读取原始冻结快照 | 未回收共享 `.w3-contexts/snapshots` 实体和旧 canonical source 副本。SDK Session 全树枚举及 SDK logs 留存仍待解决 |
| P1-04 其他 owner | Observation 只物化 canonical blocks、alias、调用回执和交付。初始化预节点快照去除重复目录检查和原 Worker 可查的 `agent_loop.jsonl`，仍可恢复旧快照。沿用 CDECR 临时解包清理和 Initialization Repair 已终态干净工作树退出 | `.init` 还未换成仅保留必要输入的 manifest。Ops/验证临时库和 Source Maintenance 后续候选需继续验证 owner |
| P2-01 浏览器 | Registry 孤立快照最多每小时扫一次。新恢复有精确 inode 和恢复时点回执，只有当前已验证身份才退出对应的旧 profile 隔离目录。现有可再生缓存继续使用 owner 锁与 PID 检查 | 未给 legacy protected snapshot 猜测过期时间，也未清退旧格式隔离目录或宿主 legacy 路径 |
| P2-02 发布与备份 | 新正文纳入恢复校验。成功迁移备份保留当前与同一数据库集合的前一验证恢复点，重跑旧任务不会清退更晚备份。继续用现有发布 owner 和 Compose 日志限额 | 其他历史发布入口、已失效备份与无主产物需要分别核对，不把未完成备份自动认定为无价值 |

## 后续启用顺序

先发布兼容读端和被选用的回滚版本，再逐角色启用 `DOXAGENT_PUBLISHED_CONTENT_REFS=1` 和 `DOXAGENT_CONTENT_SINGLE_FILE=1`。第一个开关启用发布引用写入，并允许 Read 对小对象链接同一个压缩成员。第二个开关启用 Native 和 Read 内容池的小对象写格式。回滚读端必须认识已经写入的两类新文件。

旧 capture 库可在有迁移余量的维护窗口运行现有 Transport Maintenance CLI 的 `--install-expiry-index`。常驻退出进程会在索引缺失时沿用小页序列扫描。大规模索引创建不纳入本地开发验收。

历史正文转换使用 `v2_read.cli compact-published-bodies --source research=<路径> --limit 32 --after <游标>`。先读取 `--dry-run` 配置回显。实际转换前核对备份、写端版本和空间余量。每页使用返回游标推进，并重新检查 `conflicts` 清单。SQLite 释放页用于后续复用，文件大小以单独的存量操作记录。

## W3 交集

沿用基线交付的 Native 候选分页、写锁外准备和引用复核。本轮使用项目已有的 OS 互斥来序列化同一 Native 内容池的最终清理，避免两个清理过程重用同一隔离路径。Artifact `DELETING` 的已有 guard 继续保护新引用与 hold。处理过程中保留 W3 同一个已冻结的业务输入和原有结果恢复屏障。

## 本地验证

- Acquisition、新闻列表与生命周期回归 102 项通过。覆盖按 128 条分页、已取证但尚未完成的周期和 96 小时留存仿真。
- D3 发布兼容、历史 inline 转换、Read 同 inode 内容、备份恢复、CDC 与 Runtime 资源退出组合 25 项通过。
- W3 启动与 READY admission、Observation、Profile 快照组合 25 项通过。
- 单独重跑 Native 删除中断、新发布复活与小时归档两项关键边界，均通过。
- Site 较广组合有一项基线失败。`test_maintenance_inspection_waits_for_navigation_without_reload` 的 Page 测试替身缺少 `url`，在原始 `main` 复现。其余 70 项通过。生产写入量、真实业务延时与物理容量收益待发布后验收。
- 第二批跨组件回归 125 项通过、1 项跳过。后续 D3、Workspace、W3 兼容组合 75 项通过。包含薄物理证明与轮次引用的最后一组有 66 项通过。另有一项交易时点相关用例在 `b99d0dbea` 同样复现 `RUNTIME_DELAY_EXCEEDED`，未调整生产交易判定。上述组合有重叠；静态未定义引用检查通过。

## 生产与清理进度

以下为本页原始开发阶段的发布前巡检记录；当前发布状态以以上链接的生产回执为准。独立清理过程另见 `production-disk-recovery-20261118.md`。

北京时间 2026-11-18 10:43 的根盘巡检显示可用约 2.64 GiB，`df` 显示 100%。生产仍为 `b99d0dbea`。22 个容器仍运行，Read API `/readyz` 为 HTTP 200、`ok=true`，Worker 健康。以上为现行生产版本的巡检。新存储格式尚未启用，所有生产业务验收与净释放量仍待执行。

已有 backend、site、supervisor、guardian 和 repair-agent 稳定依赖镜像。发布脚本的 10 GiB 门槛适用于需重新构建的大依赖层。沿用缓存仍需核对应用构建、业务写入和回滚的实际余量。正式清理按交付要求排在发布验收之后。独立的临时余量处置尚未获得本轮确认。

正式清理候选优先看 Maintenance 已结算过程体与可校验重复文件、已无引用 Runtime 分支、已导入的 Ops/验证现场。对 Research、Read 和 Acquisition 分别核算实际文件删除与 SQLite 可复用空闲页。当前只读检查显示，发布目录下的 backups 合计约 1.59 GiB，不能将这一整组当成足够的发布余量。正式清理仍需要记录逐批实测和恢复点复核。
