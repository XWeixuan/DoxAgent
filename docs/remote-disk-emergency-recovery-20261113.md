# 生产根盘满盘紧急恢复记录（2026-11-13事故）

现场取样：2026-11-14 00:06:58（北京时间）。本轮仅做应急空间回收和原容器恢复，没有发布新镜像、修改业务策略或清退业务主库。

## 结果

起点根分区可用0字节、已用400,916,602,880字节，inode使用42%。最终可用**9.70GiB**，已用389,537,689,600字节，按同一文件系统已用差额净减少**10.60GiB**。删除对象分配量、净已用差额和可用空间是不同口径，不能相加；ext4保留块以及期间正常写入会影响差额。

Read Maintenance原本因写GC回执NoSpace退出，Docker重启又因resolv.conf空间不足失败；CDECR Executor原本因满盘退出，重启挂载目录创建失败。两者在腾出空间后按原容器启动。最终Read Maintenance运行、CDECR和Site Access健康；API/Web/Worker/Collector/Chrome等常驻服务均在运行。旧一次性刷新容器和历史迁移容器没有被重新启动。本轮没有停止或重启Worker、Scheduler、交易Executor/Delivery或Chrome。

## 已执行清理

| 批次 | 对象与操作 | 对象量 | 保护边界 |
| --- | --- | --- | --- |
| 1 | journalctl --vacuum-size=512M，只退出归档journal | 约296.5MiB | 当前活动日志和当前事故日志保留；512M不是包含活动日志的总量保证 |
| 2 | 对当前Clash容器经inspect确认的json.log原文件truncate | 1.13GiB | 不重启代理，不修改配置或连接身份；没有清业务容器故障日志 |
| 3 | 8份过时Site操作数据库副本 | 8.67GiB | 删除前无打开FD、无精确文件bind挂载；保留两份最新完整快照并只读确认schema及核心表可读，没有声称完成灾备恢复演练 |
| 4 | 364个闲置profile的788个白名单缓存目录 | 0.89GiB | 排除11个观测到的活跃profile，逐profile共享flock；关键身份文件前后哈希零变化，未动Cookies/Local State/Storage/Session等 |
| 检查 | 24小时前未使用BuildKit缓存清理 | 0B | 普通Docker镜像没有全局prune，未删除volume或有效回滚镜像 |

删除的旧副本：

- `/var/lib/doxagent/releases/backups/reuters-solver-latency-20261112T222713Z.sqlite3`
- `/var/lib/doxagent/releases/backups/reuters-solver-latency-20261112T220615Z.sqlite3`
- `/var/lib/doxagent/releases/backups/reuters-solver-latency-20261112T214823Z.sqlite3`
- `/var/lib/doxagent/releases/backups/reuters-dual-active-20261106T214141Z.sqlite3`
- `/var/lib/doxagent/challenge-recovery-20261021/before/site_strategy.sqlite3`
- `/var/lib/doxagent/datadome-20261022/registry-before.sqlite3`
- `/var/lib/doxagent/site-maintenance-recovery-20261019/registry.before.sqlite3`
- `/var/lib/doxagent/devicecheck-repair-20261022/registry-before.sqlite3`

保留的较新Site现场快照：`reuters-applied-forensics-20261112T232317Z.sqlite3`和`reuters-applied-forensics-20261112T231838Z.sqlite3`，各约1.59GiB。普通缓存没有额外制作备份。

缓存白名单仅为Cache、Code Cache、GPUCache、GrShaderCache、ShaderCache、DawnGraphiteCache、DawnWebGPUCache，位置限定于profile本身或Default直接子目录；未把Service Worker/Local Storage或整个profile判为缓存。

## 剩余项目与回收判断

| 项目 | 本轮核实规模 | 判断与下一步 |
| --- | --- | --- |
| 活动Read库内部空闲页 | **18.16GiB**；主库32.36GiB | 最明确的大档潜力：现有GC已将部分数据变成库内空闲页，尚未还给OS。需估算临时输出/WAL峰值、协调写入再压缩；当前约10GiB余量不能直接贸然全库VACUUM。不能删除当前Read库或在线截断WAL |
| Codex调试日志库logs_2.sqlite | **5.44GiB**，日志跨度覆盖11月初至当前 | 普通诊断日志，适合按期限裁剪并物理压缩，保留本次事故窗口。该5.44GiB是候选池，不等于全可回收；活跃SQLite不能按普通文件truncate/rm |
| 活跃profile的上述白名单缓存 | **3.37GiB** | 仍可再生；逐身份正常关闭浏览器、锁定并校验身份后可清。未为这部分立即打断在途浏览器任务 |
| Codex Sessions / thread_history | 分别约10.03/2.96GiB | 会话与其派生索引需按Job、模板、父子和恢复引用退出；不能整体清空。日志库与会话权威记录分开治理 |
| Runtime / workspace过程副本 | runtime_artifacts已完整测得14.78GiB；Runtime和workspaces总量扫描50秒未完成 | 需按终态/引用核验继续退出，不能将15GiB全部视为垃圾或把未完成扫描写成0。本轮未扩大到暂缓的W3 Context架构修改 |
| Production Validation | 合计1.19GiB，其中adoptions约0.88GiB | 含已采纳资产，不能按测试目录名整体删除；可再生输入只是一部分，低于以上大项优先级 |
| 最新两份Site快照、Site快照目录 | 两份约3.18GiB；另snapshot目录较早容量记录约2.74GiB | 可审计恢复集合和identity head引用后合并退旧；不是本轮已确认可全删的空间，不重复计收益 |
| 镜像/BuildKit | 本轮BuildKit回收0B；docker system df 45秒超时 | 镜像独占可回收量未完成核定，不按镜像展示大小相加，不以此解释本轮约55GiB复涨 |

## 初步机制与边界

11月11日06:54曾实测可用54.5GiB；本次再次满盘，说明长期容量目标尚未达成。当前确认新Site调试副本和长期代理日志仍绕过了有效退出边界，Read逻辑GC释放的空页也不会自动成为根盘可用空间。Codex会话/日志和Runtime过程资产仍占较大规模。此次没有完成全部新增占用的归因，不能仅凭这次删除清单解释全部复涨。

Read最新GC回执取样：`{"at": "2026-11-13T16:03:47.263578+00:00", "deleted": {"changes": 0, "commit_retention": 0, "commits": 0, "content_search": 0, "contributions_history": 0, "cursors": 0, "definitions": 0, "metric_buckets_history": 0, "object_history": 0, "poll_current": 0, "views": 0}, "elapsed_seconds": 2.003265884006396, "errors": [{"error": "interrupted", "stage": "search"}], "next_stage": "search", "retention_floor": 13396219}`。恢复空间、容器存活、全链路业务完成和长期容量稳定分别验收。**本轮解除0空间的急性故障，约10GiB仍属于偏紧的临时余量**；下一档应优先做Read物理回收、SDK诊断日志留存及Runtime/Acquisition/workspace实际退出复盘。

证据：`artifacts/audits/emergency_disk_20261113/final-state.json`、`cleanup-summary.json`。原脏工作区与其他会话的代码修改保留，未混入发布或提交。

## 逐容器inspect补充恢复

容器列表的概要状态不足以证明运行；逐一inspect后，又识别出Control、Message Bus、Content Enrichment、Initialization、Guardian的Running=false/Pid=0及满盘启动失败错误，已按原不可变容器ID启动成功。Projector在跟进核验时已自行恢复，未重复启动。以最终final-state.json中的Running、Pid和Error为准，所有当前常驻DoxAgent容器均运行，已配置健康检查的关键服务无unhealthy；旧一次性迁移/刷新容器不纳入“必须running”。没有发布镜像或重启健康Worker/交易组件。
