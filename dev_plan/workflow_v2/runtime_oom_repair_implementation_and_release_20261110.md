# Runtime OOM / IO 修复实施与发布记录

2026-11-10，依据同目录完整事故方案执行。模型、Prompt、Skill、LLM Schema、路由、交易规则、冻结历史窗口不变。

## 已实施

- W3 私有 Ready 绑定取消公共 ready 豁免，保留原准备期限与正常 90 秒阶段交接；终态未提交 Job 的 Case 通过内部幂等接口释放物理 Capsule，保留逻辑 Thread、工作区和业务证据。释放与 Job 接受在同一 SQLite writer 内仲裁，按 Case attempt 身份阻止迟到的首次提交，已受理 Job 不被误杀。
- 静态 payload、标签、历史和事件准备提前到领取私有 Thread 前。实时行情与模型开始时刻保持原阶段。
- 实时 W3 用短线程直接开始，等待模型让出线程；CLOSED/归档等只领取既有 8 路后台容量能承接的工作。实时过滤发生在 SQL LIMIT 前；公共预热在绑定实时 Case 后也能动态绕过后台 cold gate / handshake / launch ramp。保留同一个 Thread 的未知 owner 隔离。
- Future 续接只持小身份，结果先保存再按原提交逻辑执行；ACK 在业务 commit 后异步进行，复用持久 housekeeping 标记。失去 lease、停机和取消不扣业务重试次数。
- W3 使用现有持久 SSE 和游标续订；终态只取一次完整 Job。状态投影不解码大对象，条件版本消除通知丢失。终态落库即可唤醒，不等审计文件复制。
- GC 启动延后 15 分钟，周期从完成算起，不再由每次维护 finally 强制触发。先遍历小批候选目录，再按 root 索引读取 Case/任务小元数据；不枚举全部历史 Frame / 原生 Fact。后台压力或实时 W3 出现可让出，2 秒预算和 SQLite progress handler 中断 staging 工作；发布仍复核原引用、指纹和版本。未知旧元数据保留原资料。
- checkpoint / finish / fail 的大对象编解码移出 writer，原 lease fencing 与 receipt CAS 保留；无变化 stage/association 省去写事务；批量续租返回失权身份。
- 核验并整合生产已有的 journal / maintenance / coordinator / bounded_inputs / maintenance_recovery overlay，防止本轮发布或以后全量 build 丢失 O2 恢复与补充任务修复。

## 验证与发布范围

- 151 项相关回归通过；最终事故聚焦回归 46 项通过，含 40/200 个模型等待 Future、后台阻塞时实时旁路、动态启动提升、终态释放及迟到请求、同线程隔离、停机恢复、事务外编码与 GC 让出。报告：`eval/runtime_oom_repair_20261110/regression.xml`。
- 扩展扫描中另一个旧 W2R3 测试用固定历史 source，现已触发既有过期交易保护，期待 READY 而实际 RUNTIME_DELAY_EXCEEDED；保护逻辑未修改。旧 Scheduler main 单测 fake 缺少真实 coordinator / heartbeat 配置，也与本次改动无关。没有为让测试变绿放宽过期保护。
- 停服窗口预建 jobs_run_owner（4.89s）、w3_ready_run（0.43s）、runtime_gc_task_run（9.69s）、runtime_gc_task_root（0.14s）、runtime_gc_activation_root（0.09s）；8MB SQLite cache，排序落磁盘，未执行 VACUUM / 全库完整性扫描。
- 使用实际 Scheduler / Worker 镜像为基底，仅覆盖明确文件清单，逐文件校验旧基线与新文件 SHA256。生产其他 overlay、密钥与 Compose 环境保留。其余 5 个隔离角色恢复已登记 Compose。构建与业务恢复期间不执行镜像 prune。

## 发布后观察

待追加：实际提交、镜像、恢复时间、10 分钟主机/容器资源与 Case 进展。采样只读 rowid 小窗、active job 索引、cgroup 与 PSI，不执行历史大统计。本次 10 分钟观察不代表已经覆盖下一交易日真实波峰。
