# CEG / ONDS 磁盘写满阻塞恢复

2026-10-18 18:09 北京时间用户要求探测并修复。18:09:42 发现两者表面 RUNNING / D3，
实际租约已过期约 28 分钟，初始化、Control、Guardian、projector 均 exited。
Docker 报 ENOSPC，初始化日志报 SQLite disk I/O error，OOMKilled=false。
根分区 177G / 100%，用户可用空间为零；不是正常模型长任务，也不是用户主动停机。

## 空间与恢复操作

没有删除业务数据库、研究产物、原生内容、会话或恢复点，也未执行 Docker prune。
先对五份已关闭的本会话离线 SQLite 备份做全零块稀疏化，前后大小与 SHA-256 一致，
但仅释放 15,630,336 字节，不足以恢复服务。
然后将同五份备份转为 gzip 表示；逐份核验解压字节数与 SHA-256 和原文件一致，
fsync 压缩副本及 .storage.json 恢复说明后才退役未压缩表示。

五个恢复点均保留：

- `/var/lib/doxagent/cohr-recovery-20261017/before/workspaces-worker-jobs.sqlite3.gz`
- `/var/lib/doxagent/cohr-recovery-20261017/before/initialization-control.sqlite3.gz`
- `/var/lib/doxagent/cohr-lease-recovery-20261018/initialization-before.sqlite3.gz`
- `/var/lib/doxagent/cohr-lease-recovery-20261018-r2/initialization-before.sqlite3.gz`
- `/var/lib/doxagent/cohr-recovery-20261017/before/research-research.sqlite3.gz`

邻接原文件名的 `.storage.json` 保存原始 SHA-256、大小、时间和恢复命令。
合计由约 3.50GB 逻辑备份转为约 1.01GB gzip，释放约 2.49GB。

ext4 原预留 1,906,093 个 4096B 块，约 7.81GB，因此释放后非 root 可用仍为零。
将 `/dev/vda3` 预留改为 1%（470,476 块），保留约 1.93GB root 应急空间。
该文件系统配置可逆：`tune2fs -r 1906093 /dev/vda3` 恢复原值；不影响文件内容。
调整后非 root 可用约 794MB。

18:15:40–41 仅启动原 Control、初始化、Guardian、projector 容器，保持镜像和配置。
由原 checkpoint 自动恢复，CEG / ONDS 取得 token=4 的新租约；未 submit、resume、
reinitialize、清空预算或改写研究 cutoff。

## CEG 丢失结算回执恢复

Worker job `6f8cb08be886c302a9d89713e9d9841df48fbc105c897bc8d28e41af510b6682`
仍 running，但原 turn 已在 17:49:55 完成，磁盘满时未保存 worker-results 和 job 结算。
没有重启 Worker 或取消其他正常任务。
核对原 session 的 terminal turn_id、原 job thread/turn、原 frozen request schema；
最终 assistant 输出通过 schema 校验（completion SHA-256 见验收 JSON）。
会话中有 1 条历史损坏 JSONL 记录，保留未改动；本次仅使用完整有效的最终输出及完成事件。
通过原 result_receipt.commit / JobStore.save / job.result_recovered 事件恢复该 job 的成功回执，
没有补造模型输出、重跑已完成 turn 或伪造 token 用量。

## 验收与限制

18:21:40：原 ONDS run `init-onds-d225e626c3ae49bd866082e7d696e7ae` RUNNING / D3，
state_seq 1413，成功节点 595（故障探测时 593）；CEG 原 run
`init-ceg-d0d787cd5f6846e7925032e0f51e0f99` RUNNING / D3，state_seq 2298，
成功节点 1078（探测时 1074）。原 run ID / research cutoff 不变，当前无 FAILED 节点。
CEG 四个 Build、ONDS 一个 Integration Worker 均 RUNNING，18:21:34–40 有实际模型活动。
两者租约已跨多个周期续期，四个受影响服务均正常，Initialization source 投影已追平，
前端均仍正确显示 INITIALIZING。Runtime source 仍有约一万条积压，检查点继续推进。
CEG 尚在 D3，PAPER_TRADING 切换待原初始化完成，原授权 heartbeat 待办不变。

磁盘长期容量问题没有根治：18:23 检查非 root 可用仅约 303MB（物理空闲约 2.25GB，
其余为保留空间），Read SQLite 约 31.31GB 且 freelist_count=0。
BuildKit 仅约 37MB，不能靠清理 BuildKit 解决此次容量问题。
后续需要扩容或经独立核验/授权的旧镜像与存储生命周期治理；不能把本次服务恢复宣称为容量根治。

主工作区 `eval/ticker_disk_recovery_20261018/` 保存稀疏化、压缩、文件系统调整、
Worker 恢复脚本与验收记录；远端 Worker 恢复前快照及审计保存在
`/data/initialization/operator-audits/ticker-disk-recovery-20261018/`。
