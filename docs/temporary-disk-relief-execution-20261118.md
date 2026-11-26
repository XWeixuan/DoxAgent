# 2026-11-18 临时磁盘余量恢复回执

范围：按用户最新指令，仅做约 10 GB 的临时清理。未实施全局存储优化方案，正式存量清理留待方案落地后进行。

## 实际效果

北京时间约 05:00—05:10，以根盘 `df -B1 /` 可用空间变化计：

| 指标 | 结果 |
|---|---:|
| 清理前可用 | 2,051,317,760 B，约 1.91 GiB |
| 验收时可用 | 12,001,538,048 B，约 11.18 GiB |
| 净释放 | **9,950,220,288 B，约 9.95 GB / 9.27 GiB** |
| df 使用率 | 100% → 97% |

已达到约 10 GB 的临时释放目标，停止继续清理。期间业务与其他会话发布仍可产生写入，以下文件登记量不作为额外净收益相加。

## 清理对象

1. **已正式退役的 Maintenance 工作区重复内容。** 退出记录覆盖 1183 个工作区：删除 `artifacts/document3/v21_state.json` 过程视图，登记约 3.50 GiB；5910 个发布文件别名经 manifest 大小、SHA256 校验后，以硬链接共享原正式产物，登记约 3.27 GiB。保留所有正式产物访问路径与 manifest。操作条件包括已存在的 retirement fence、complete/publication 回执、无 active 同族 Jobs；变更前重新核验 Jobs。
2. **三份旧的发布/调试数据库快照，共约 2.94 GiB。** 删除：
   - `/home/ubuntu/deploy_backups/challenge-solver-repair-20261023-prepublish/registry.sqlite3`
   - `/home/ubuntu/deploy_backups/challenge-solver-repair-20261023-pre/registry.sqlite3`
   - `/var/lib/doxagent/releases/backups/reuters-applied-forensics-20261112T231838Z.sqlite3`

保留较新的完整恢复快照 `/var/lib/doxagent/releases/backups/reuters-applied-forensics-20261112T232317Z.sqlite3`，核实可读取身份相关表；当前身份库与浏览器 profile 没有清理。删除前核验旧文件不被当前容器 mount/env、release state 引用，且无打开 fd/mmap，记录 inode、大小和 SHA256；没有为这些旧副本额外制造备份。

只探测了浏览器缓存，没有清理：非活跃 profile 中超过 48h 的候选约 0.27 GiB，收益较小，未继续投入。主业务数据库没有删行、VACUUM 或迁移；没有清理 Runtime 分支、W3 正式资产、Sessions、Research 正文和仍有效恢复点；没有因本轮操作重启服务。

## 验收与执行说明

- 22 个原有业务/基础设施容器在运行，末次清单未见 unhealthy。
- Read API `/readyz` 返回 HTTP 200，`ok=true`；read/stream/control 查询池均 READY。
- 较新 Reuters 完整恢复快照仍在。
- 首个临时脚本对 SQLite 只读连接使用了仅提交/回滚、不关闭连接的上下文，触及文件句柄上限后提前退出；已改为显式关闭连接，从已有退役回执继续，续批无错误。该问题仅发生在临时清理进程，没有修改产品代码或数据库记录。
- 本次恢复的是短期余量，不代表持续膨胀已解决，也不宣称已完成长期业务验收。

证据与逐项操作记录：[`artifacts/audits/temp_disk_relief_20261118/`](../artifacts/audits/temp_disk_relief_20261118/)，主要包括 `probe.json`、`cleanup.jsonl`、`cleanup-resume.jsonl`、`old-backup-cleanup.json`、`verification.json`、`summary.json`。首批退出前完整总回执未输出，最终统计依据服务器逐工作区退出回执重新汇总；整盘净收益以 df 为准。
