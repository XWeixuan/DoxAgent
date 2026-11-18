# 存储源头减量开发回执

实施基线为 `b99d0dbea`，包含本地 `main` 已交付的 W3 非模型延时修复。开发位于 `codex/storage-source-reduction-20261118`，原工作目录用于读取两份方案。以下为本地开发结果，生产部署和存量清理另行安排。

| 工作包 | 本轮交付 | 待完成 |
|---|---|---|
| P0-01 D3 与 Maintenance | 取消每轮 `v21_state.json` 镜像和 V21 `output/final` 重复输出。Pilot 按需构建视图，兼容历史文件。新 staged receipt 引用已校验的投影文件。Runtime 从同一已结算维护任务核对 D3 消费与 Jobs 状态，调用现有 D3 过程字段退休入口 | Workspace 的发布副本单份化，以及非 W3 ACK 后的过程文件与 Session 局部退出 |
| P0-02 Research 正文 | SQLite 发布入口支持 SHA 寻址压缩正文，兼容旧 inline 和已有外部存储。读取、adoption、W3 编译、备份以及评估导出支持新引用。历史转换命令每次处理有限页面 | 后续发布时统一启用 writer，实测压缩与写入规模，迁移后再审查旧副本 |
| P0-03 Acquisition | 省去相同 unit head/state 重写，物理请求诊断只随一个逻辑结果保存。按 128 条读取一小时的薄证明，以原 cadence 算法计算结果。保护已取证但在途的 cycle，归档提交后再退出原行，超出七天的终态明细直接退出 | 小时扫描的进程内进度在重启后会重读原始页。完成真正晚到且落在已归档小时的指标合并，以及物理证明与更多派生字段的归一化。50% 块写下降尚未实测 |
| P0-04 CDC | Research 与 Bus 已 ACK 热回放改为 48h，其他源保持现有窗口。年龄索引与有限回退，事务重查 source epoch、ACK 与 pin。压力条件下每源尝试一小批热包，使用零等待 writer。Native ref 按集合差量维护 | 旧 archive/pack 的独立到期元数据回填，以及其余投影字段审查 |
| P0-05 Native | 小对象支持单 gzip 成员，旧 chunk 格式继续可读。Source Native 常规最终删除采用现有 `DELETING` 状态，文件删除在 writer 外，退出记录可在中断后重试 | 对复活及历史异常分支中的残余锁内文件操作继续验证。没有把它们统一移到锁外 |
| P1-01 Runtime | Reference 的 before/after 与 baseline 大字段复用 Native SHA，同一快照不反复写正文。结算只更新状态和时点。原有 activation/branch 引用协议沿用 | 全量历史 Event 分支退役策略、W3 citation scope 与 round inputs 的引用收敛 |
| P1-02 Read | 新发布的合适大小正文在同一文件系统可共享已验证的压缩文件，保留原 API 内容身份与访问权限。大对象沿用可按范围读取的 chunk | 全投影减量及存量转换 |
| P1-03 W3 | 新编译资源支持从 canonical 研究文件重建，支持先发布 reader。保留现有启动、READY、模型调用及结果结算设计 | Context 的真实 obligation 终态退出、旧 source/pack 回收及 SDK Session 生命周期 |
| P1-04 其他 owner | 成功 Observation 直接落既有 canonical blocks、alias、调用回执和交付，省去无独立消费者的 raw transport 副本。保留去敏的异常诊断 | 初始化快照、CDECR 和修复/验证现场的统一结算 |
| P2-01 浏览器 | Profile 恢复点修剪沿用原规则，跨 profile 的孤立 Registry 快照扫描每小时最多一次 | 保护期限和成功恢复隔离目录退出 |
| P2-02 发布与备份 | 压缩发布正文进入已有备份校验和目录清单 | 统一其他发布入口、旧恢复点及日志退出规则 |

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
