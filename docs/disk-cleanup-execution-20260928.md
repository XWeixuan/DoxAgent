# 新加坡磁盘清理执行记录（2026-09-28）

## 已完成清理

依据 `docs/disk-cleanup-plan-20260928.md` 和用户批准执行。目标主机 `VM-0-15-ubuntu`，SSH 别名 `doxagent-sg`，没有重启 Docker 或业务服务。

- 删除 42 个白名单旧镜像记录、4 个停止的测试/迁移容器、未使用 BuildKit 缓存。
- 删除 16 个旧整目录快照、O2 专项目录中三个旧主库、历史 Message Freshness 验证目录、两份旧 Bus 专项副本及一份 before-* 副本。
- 保留 9 月 20 日迁移快照、9 月 24 日 Bus 专项快照和约 27 MiB Event Library 原始证据。
- 17 个运行容器 ID、镜像 ID 和运行状态前后不变。10 个数据卷名称全部保留。
- Guardian 的 Created 修复模板和零容器引用的修复代理镜像仍可用；浏览器 Profile 不删。

实际清理释放 **81.147953 GiB**，可用空间从约 9.065 GiB 增至 **90.213051 GiB**；首轮 `df -h /` 为 177 GiB 总量、约 80 GiB 已用、约 91 GiB 可用、47%。新增当前备份后，最终验收根盘已用 **97.315662 GiB**、可用 **71.938751 GiB**，`df -h /` 显示 177G 总量、98G 已用、72G 可用、**58%**。这与清理释放量不同：新备份重新占用了部分空间。

旧快照已永久删除，不能在服务器上找回相应历史回滚点；旧镜像/构建缓存需重新构建或从已有仓库获取。当前业务数据和保留的恢复点未删除。

远端审计日志：`/var/lib/doxagent/ops/cleanup-20260927T183919Z/{before.json,plan.json,events.jsonl,after.json}`，共 70 条操作事件。本机副本位于 `.tmp/ops-cleanup-20260928/`。日志不包含凭据或完整环境变量。

## 业务验收

- `/healthz` 正常。
- `/readyz` 正常：Read 2/2、Stream 1/1、Control 1/1 查询进程池均 READY。
- 公网 `/overview` 返回 HTTP 200。
- 已登录 Chrome 页面执行“刷新 Overview”，刷新完成，无 STORE_UNAVAILABLE，BE、INTC、MU、RKLB 四个 ticker 均在列表中、状态为运行中。
- 此验收不是对消息源覆盖、交易收益或其他既有业务警示的全面重新审计。

## 当前备份

生成与验证已完成，验证记录完成时间为 2026-09-28 03:11:02（北京时间）。本机 C 盘空间不足以接收整份备份，因此当前恢复集保存在服务器数据卷上，尚不构成异地灾备。

备份使用当前 API 镜像运行独立 `--rm` 容器，网络禁用、CPU 0.5、内存 768 MiB、较低 I/O 权重；没有构建新镜像或重启业务容器。

Read 全库离线校验阶段观测到随机读取瓶颈，服务器当时可用内存约 10 GiB；仅将本轮临时容器内存上限调至 2 GiB、禁用该容器 swap，CPU 0.5 和 I/O 权重不变。没有修改任何业务容器的资源配置。

第一次临时备份在 Runtime 持续写入时出现增量快照重复重读，仅停止该备份容器，删除本轮尚无 manifest/verification 的不完整临时目录。随后为备份进程的只读源连接固定事务快照；不改线上库结构、运行配置或业务写入。

当前恢复集路径：`/data/backups/20260927T184853Z-verified-current`（时间戳为 UTC），宿主机路径为 `/var/lib/docker/volumes/doxagent-v2_v2-data/_data/backups/20260927T184853Z-verified-current`，`du -sh` 显示 19G。13 个数据库的 SHA256 和 SQLite quick_check、33,692 个文件校验全部通过，结果保存在该目录的 `verification.json`。各数据库为独立只读事务快照，不是跨库同一时刻的原子快照。

临时备份容器已正常完成并自动删除，没有遗留新镜像。最终再次核对原有 17 个运行容器 ID、镜像 ID、运行状态和全部 10 个数据卷不变，API healthz/readyz 正常；未重启业务服务。

独立读取保存的数据库已确认：

- Read 中当前 ticker 为 BE、INTC、MU、RKLB，没有 unresolved gap。
- 保存的 MU 初始化记录包含 `init-mu-1f3e9130ae304b01a7fdf2991a35e028`，状态 SUCCEEDED、phase VERIFY_READY；另有一次 SUCCEEDED 的 MU 记录。
- Read checkpoint 与四个保存源库的 head 完全一致：research 2397、initialization 102782、bus 729950、runtime 1096684。
- 不把上述校验称为整站启动/完整灾备演练，仍保留此前两个过渡恢复点。

## 根盘 15 GiB 告警定时任务已移除

本对话曾创建 heartbeat 告警，ID `15-gib`，每 15 分钟检查一次。用户明确表示这不是期望模式并要求移除，已通过应用工具删除，返回 `deleteStatus=deleted`。当前没有保留这个定时任务，也没有擅自创建替代告警。

被移除任务的原设计使用 `/` 的 `os.statvfs` 中 `f_bavail * f_frsize`，低于 `15 * 1024**3` 字节时告警；不自动清理或重启。这是历史记录，不表示任务仍在运行。

原方案采用应用自动化能力，依赖本机开机、Codex 应用和 SSH/Clash 路径可用，不是腾讯云侧独立告警。用户未选定其他告警渠道，本轮不部署其他方案。

初次阈值手工探测正常，备份运行期间测得根盘可用约 86.51 GiB；阈值条件为 false。尚未通过实际填满磁盘测试告警，避免制造生产故障。

## 记忆与验证

用户指定规则已写入授权记忆更新注记：`C:/Users/WEIXUANXIE/.codex/memories/extensions/ad_hoc/notes/20260928-023800-doxagent-temporary-image-cleanup.md`。

规则：每次调试/部署结束登记并删除无运行或动态依赖的临时镜像；核对实际镜像 ID 与动态模板引用，不误删业务卷/Profile。

新增运维脚本及测试 Ruff 通过，防误删定向测试 6 passed。现有不相关工作树改动保留；本轮没有提交/push，也没有部署业务代码。
