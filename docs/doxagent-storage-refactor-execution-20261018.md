# 生产存储重构执行记录

授权：严格实施 2026-10-18 完整方案；允许清理低价值可恢复资源。W3 Context 复制机制继续排除。

本记录随实施更新，尚未代表整项工作完成。实现分支 `codex/storage-lifecycle-v4`；基线 `160f352e`。

## 已完成的施工与校验

- 四套旧隔离 Pilot 的重复 Context 输入已退出远端：MU 2,287,439,872 / INTC 583,614,464 / BE 499,421,184 / RKLB 266,285,056 分配字节。保存 D 盘的只是去重唯一输入及恢复路径/SHA 清单，合计加密包约 160 MiB；未改变生产 W3 Context。
- 9 月旧 control predeploy 快照及空 WAL/SHM 已退出，463,708,160 分配字节；当前 10 月恢复集已核验。浏览器本次已有 owner GC 回收为 0，不重复计算旧收益。
- Read V4 隔离构建完成，主库 2,750,025,728 字节，单份当前对象＋短协议历史＋稀疏正命中＋7,585 个单份业务定义。825,071 条逐 Policy 观察收敛为 30 个实际命中 Case 集合。
- 全表核对 `business_revisions` 与原 `objects` 的同键同 payload：孤本 0。没有把不确定来源改写成可核实业务记录；33 条原 Runtime gap 继续保留 coverage/修复定位。
- 可达文件选择性校验链接 33,748 个内容对象；新代际完整恢复包共 69,358 个文件、2,864,611,686 逻辑字节。D 盘解密还原后逐文件 SHA 与 SQLite quick_check 通过，代表对象包含 Case 7,879、Fact 203,916、Policy 详情 33,096、执行 88、usage 40,607。
- D 盘恢复包：`D:/DoxAgentStorageExecution/read-serving-v4/serving-v4.tar.gz.enc`，SHA256 `a34e44c40b400fe0be34654c078a4e6520f8bb41e75bcdf3de69468765ca40cc`；同目录 private ACL 密钥和 verified.json。该包是 V4 业务读取基线，不冒称完整旧 V3 技术历史备份。权威源仍在线保留，后续变化通过 outbox 追赶。
- Read/发布器回归 158 项通过；前端类型检查与既有 63 项测试通过。保留生产额外初始化按字段读取修复。

## 尚未完成

- 新旧端点固定水位对照、Read 追赶及生产切换、旧代际退出。
- 稳定依赖真实构建、两次小发布依赖 diffID 对照、组件 current/previous 登记、自动收尾与 timer 启用。
- Event 最小分支与终态退役、源 transport 真正到期/精确 pin、源 native 引用收尾、Worker 终态 Session、旧研究/Validation/恢复资料后续清理。
- 生产性能与容量回执；七天自然增长观察尚未经过，不提前宣称通过。

完整原始执行证据位于主 checkout 的 `eval/storage_lifecycle_execution_20261018/` 以及 D 盘 `DoxAgentStorageExecution/`。
