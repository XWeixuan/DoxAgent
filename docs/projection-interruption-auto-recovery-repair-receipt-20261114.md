# 投影中断自动恢复修复回执（2026-11-14）

## 已实施

- 00:05 启动现有 Projector，从原 checkpoint 续跑；未改 generation、未全量重建。
- 发布应用提交 `9fedb4ea9b16fddafb4e9cf40133fd309c47dafb`，镜像 `sha256:75753457c2789d2db0d87340d0947859bbc2cf63de359ac17556d06cac838a77`。仅覆盖 cli/projector/recovery 三个文件，沿用现有发布、镜像与回退登记机制。新容器于 00:09:15 启动。
- FULL/ENOSPC、BUSY/LOCKED 在 tick 退出事务后按 1/2/5/10/30 秒重试，不再记成业务 gap；其他事件读取/写入基础设施异常不推进失败事件。持久 IOERR/CORRUPT 不盲目重试或清库。
- repair 的业务退避只在业务失败时累计；同 attempts 按事件升序处理，优先恢复较早依赖。保持单 writer、现有幂等与 entity watermark。
- 部署每分钟宿主机检查，只恢复正式发布清单中、因 ENOSPC 启动失败的 Projector；2 GiB 最低空间门槛、5 分钟失败冷却、互斥、镜像校验，绝不拉起 executor 或其他角色。应用自身退避与宿主启动恢复相互补充。
- 应用修复已合入远端 main（集成提交 `1618cb735`）；另一个会话的提交保留，未覆盖用户原工作区脏文件。源码工作树：`C:/DoxProjectionRecovery`。

## 数据恢复核验

Read generation 始终为 `75a54e2449a84065a0aee86dac09e353`。

本次 `4624585 / 4624587 / 4624591 / 4624593` 均有真实 `:repair:1` 提交；后续依赖 turn `4624595` 同样有修复提交。五个坐标均已无 gap，未直接删除 gap 或改写 checkpoint/ACK。启动约40秒时 Runtime 坐标距离从462降至11；至00:07初始化/研究追平、Runtime/Bus恢复跟随新增事件。

剩余 runtime gap：`ValueError=17,140`、`PROVENANCE_UNVERIFIED=66`，合计17,206。其中17,204条是此前历史问题；新增2条为费用事件 `4624987/4624988`，缺少对应 `te_fills`。已查证源库对应成交记录也不存在，不能由投影伪造成交。本次中断四条 OperationalError gap 已全部解除，不能把剩余缺口说成“全部投影完整”。

## 测试与生产验证

- 最终相关测试集58项通过，含源生命周期、freshness、health、执行投影及恢复事务语义。
- 额外初始化投影测试存在1项既存失败：`test_progress_reads_used_fields_without_hydrating_worker_receipts[True]`，在修改前工作区也复现；本轮未修改该初始化代码，未声称全仓测试通过。
- 已在实际生产 Linux 镜像的隔离临时库验证 FULL 不跳过坐标、不会产生存储型 gap，且 CLI 在同一进程按1/2秒重试。未向生产磁盘注入满盘。
- API `/healthz` 返回200；恢复 timer启用，首次和后续service执行成功；单次内存峰值18,186,240 bytes，CPU约0.084秒。容器挂载失败的恢复判断通过测试，未再次人为填满生产盘来演练。

## 运维与回退

宿主路径：`/var/lib/doxagent/projector-recovery/projector_recovery.py`。

维护前执行 `sudo touch /var/lib/doxagent/projector-recovery/paused`，再停止Projector；维护结束删除该marker恢复检查。永久禁用则删除同目录 `enabled` 或 disable timer。当前 guard 的2GiB为紧急恢复下限，不替代另一会话负责的长期容量治理；如写入预算增加，应提高门槛。

单元：`doxagent-projector-recovery.timer`、`doxagent-projector-recovery.service`。只检查 running/exited 和容器启动错误，不强杀 running 但缓慢的进程；健康程度仍需结合四源进度、最老pending和gap。恢复等待时不向满库写健康状态。

应用基线镜像 `sha256:090c503631399d46c49ae7604fe47f48390109417f6ef8b68689fddb138f6963`，回退登记在 `/var/lib/doxagent/releases/projection-performance-9fedb4ea9b16-v2-projector/`。回退沿用组件发布工具的已登记版本，保留Read库、generation及坐标，不恢复旧数据库文件。回退旧程序也会撤回FULL原地重试能力。

原始证据位于 `eval/store_unavailable_diskfull_audit_20261113/`，包括 `before-repair.json`、`after-start.json`、`deploy-result.txt`、`verification.txt`、`linux-smoke.txt`、`check-source-fill.json` 和发布后观察文件。

## 停止时验收结果

按用户“全部追平就先停下”的指示，于00:29结束观察；未再实施额外修改。计划30分钟窗口实际完成00:10:28–00:29:31共19分03秒、39个样本，结束的是本轮采样任务，生产Projector及恢复timer继续运行。

最后四源 **head=checkpoint=ACK**：runtime `4631342`、bus `11418296`、initialization `158760`、research `61487`。四源均无正常消费积压。整个窗口容器持续running、零重启、无OOM，gap总数稳定17,206。

| 源 | 实际Read提交样本 | P95秒 | P99秒 | 最大秒 |
|---|---:|---:|---:|---:|
| runtime | 2913 | 13.523 | 20.205 | 22.663 |
| bus | 2213 | 2.746 | 3.788 | 5.579 |
| initialization | 2 | 9.074 | 9.074 | 9.074 |
| research | 0 | 无样本 | 无样本 | 无样本 |

时延采用源事件时间至Read commit_retention.projected_at，排除repair提交；不包含客户端网络/渲染时间，也不以无变更源伪造零延迟。Runtime在00:15–00:18突发负载时出现秒级长尾，随后自行追平；窗口内采样最老pending最大19.23秒，最大坐标距离143，未再出现小时级持续积压。

**未全部验收的项目**：Runtime P95≤10秒仍未达标；Research有效变更样本不足；持续容量余量及同负载CPU/RSS/写入≤10%增幅未证明。宿主IO full avg60最高6.66%，超过5%目标。上述性能余项保留，按用户指示本轮先停，不为通过指标另开并发或扩大改造。

停止前Docker瞬时Projector CPU17.40%、内存83.98MiB；容器主PID为supervisor，因此不使用主PID的低CPU/RSS作为Projector资源结论。宿主恢复检查内存峰值约23MiB、单次CPU约0.1秒，满足轻量目标；最终根盘可用10,438,336,512 bytes，磁盘容量长期验收由另一会话负责。

原始观察文件为 `acceptance-19min.jsonl`。采样进程按用户指示发送SIGINT退出，其KeyboardInterrupt仅属于观察器，不是生产Projector退出。汇总：`acceptance-summary.json`；最终状态：`user-stop-final.json`。
