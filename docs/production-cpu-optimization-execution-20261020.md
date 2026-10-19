# 生产 CPU 优化实施与验收

2026-10-20，Asia/Shanghai。状态：CPU-01、02、03、05、06 已上线并完成 30 分钟以上的稳定性复查；CPU-04 按要求跳过。

## 实施范围与版本

按 [优化方案](production-cpu-optimization-plan-20261019.md) 实施 CPU-01、CPU-02、CPU-03、CPU-05、CPU-06。CPU-04 Yahoo 读取顺序按用户指示跳过，`yahoo_sources.py` 未因本轮改变。未扩展方案中有待证明收益的通用缓存、浏览器池、Chromium 参数或全局连接池改造。

| 项目 | 实际改动 | 提交 |
| --- | --- | --- |
| CPU-01 | 一个 dispatcher 领取，16 个执行槽独立运行；空闲与 deferred 合并等待；取消时等执行 task 清理后释放进程锁；镜像 DDL 移至启动 | `81febeb5` |
| CPU-02 | 批量读取标量状态；稳定 activation 不读完整 artifacts；镜像相同不写入，缺失仍补偿；失败状态只变更一次；heartbeat 每 10 秒持久化 | `3fa2bd44` |
| CPU-03 | 先取最多 64 个原始 Case，再查线程登记；缺失时优先 stage 回执并通过部分索引点查；20 次登记预算可在 Case 内续跑；保持 Worker 所有权验证 | `5f05bac2` |
| CPU-03 收尾 | 复用 housekeeping 状态标记本周期已完成的阶段，避免兄弟阶段续批导致重复扫描/RPC；周期失败保留到最终重试调度，进度原子提交 | `fc04c09a` |
| CPU-05 | 年轻 outbox 行不加载 packet；过期 packet 借用同一个连接；每批最多缓存一个解压 pack；输出扫描计数 | `7ebd3a1a` |
| CPU-06 | 请求起始时间与其他 group 字段在一个事务内读取和更新；时间仍在 storage executor 内取得，限流持久化仍先于请求执行 | `8988a02d` |

所有重要代码改动均已追加 `changelog`。修复分别提交并推送 origin/main，没有把其他任务未提交的文件一并提交。

生产发布复用 `scripts/ops/storage_release.py` 的构建、引用保护、回滚登记和资源退出。首次发布构建代码版本为 `6098235c`，随后仅为 CPU-03 收尾更新 Scheduler 到 `fc04c09a`。这两个版本均包含前述五项修复；期间的已提交业务更新随 main 保留。

| 服务 | 最终代码版本 |
| --- | --- |
| Initialization、Control、Transport Maintenance、Message Bus | `6098235c` |
| Runtime Scheduler | `fc04c09a` |

两次构建复用相同稳定依赖镜像 `572025e4f72c…`。首轮 backend 镜像为 `0a6f28472c65…`，Scheduler 收尾镜像为 `7ca96fecd726…`。切换前正常排空 Scheduler/Initialization，没有强制结束模型工作；Codex Worker、Executor 和前端不因本次 CPU 发布重建。

未执行额外存量深度清理、未变更保留期、未降低研究/初始化并发、未设置新的 CPU 限额。既有 Transport 生命周期服务继续按原规则运行；部署时既有 release owner 也照常退出已无引用的发布资源。

## 本地回归

三组正式结果分别为 76、68、26 项通过，组间存在重复；另一次 Control 专项 4 项通过，含两项补充测试。按测试标识扣重，共验证 **136 项不同测试**。修复文件的针对性 Ruff 检查通过。

行为验证覆盖：

- 16 槽并发、完成补位、空闲领取不随槽数放大、deferred 不立即重领、取消清理。
- 稳态 Control 无重复写入，缺失镜像/ACK 补偿，旧 epoch 不覆盖新状态，新 activation 仍验证完整归属，失败状态只写一次。
- W3 全已登记时零回执查询，原始 Case 批次上限，回执预算内续扫，优先明确 stage job，表达式索引点查，发现失败不阻塞 ACK；两个阶段续批不互相重跑；既有 active/template/expiry 保护。
- 年轻与过期数据交错时正确退出，批内借用连接，共享 pack 只解压一次，pin 后 packet 可恢复；原有生命周期回归。
- Bus 一次请求只开一个状态事务，保留其他 group 字段和重启后的请求间隔信息；原有 Bus/初始化恢复与故障矩阵回归。

测试使用 fixtures 和 fake adapter，没有为了验收重新提交研究或付费模型试验。正常生产业务在发布完成后继续运行。

## 即时生产验证

五个目标容器的实际文件哈希与对应构建源一致。Initialization 的配置仍为 16 槽，现场日志空闲时每分钟约 114–117 次 claim、成功领取 0 次；不再由 16 个槽独立空轮询。Control 稳态日志每分钟镜像写事务为 0，初始化与 Bus 的控制镜像均与权威状态一致。

按 cgroup 累计 CPU 核时差分，结果单位为“核”，不是未归一化的 Docker 百分比：

| 组件 | 发布前约 90 秒窗口 | 五项首轮发布后约 120 秒窗口 |
| --- | ---: | ---: |
| Initialization | 0.235 | 0.009 |
| Control | 0.156 | 0.029 |
| Transport Maintenance | 0.172 | 0.064 |
| Runtime Scheduler | 0.394 | 0.448 |
| Message Bus | 0.373 | 0.416 |

前后业务负载不是严格受控实验。初始化同为空闲、Control 同为稳定状态，且有 claim/实际写入计数支撑，降耗证据明确。Transport 的既有周期中，样本从约 4–8 秒缩短至约 2 秒，部分轮次处理更多过期 packet，未通过少执行维护降低占用。

Scheduler 和 Bus 容器总 CPU 没有在该窗口表现为下降，不能把它们所有负载视为修复收益。CPU-06 确认去掉一次独立开库并保持原子性，不能由此推导整个 Bus 下降同一比例。

### W3 的真实数据隔离验证

生产旧 housekeeping 在退出前已完成，`next_at` 指向下一周期；未为了验收提前触发线程删除等生命周期动作。因此即时容器 CPU 不能用于证明完整新周期的降幅。

改用已部署发现函数读取真实 Runtime 数据，复用隔离的只读连接，把游标/错误写入重定向到内存库，并禁止 Worker RPC。实际完成 **69 批、4,394 个 Case**，回执查找 **0 次**、登记 RPC **0 次**，累计约 **395ms CPU、3.22 秒 wall time**。真实库执行计划显示回执点查使用 `w3_legacy_invocation_thread` 的 SEARCH。该结果验证批次与查询结构，不包含生产每批新开连接和进度落盘的全部开销，也未声称已执行到期线程退出的真实业务周期。

### 业务读取与整体负载

即时验证中，research、initialization、bus、runtime 四个 Read 源的 checkpoint 都达到所报 head，error 均为空。Worker 正常出现并处理业务任务，发布未以暂停健康业务换取低 CPU。

整机在该窗口仍约 **99% 忙碌**。上述确定空耗已经下降，但释放的容量会被其他业务使用；本次不能宣称已经彻底消除整机高负载，也不把 W3 的原热点预估直接加入实测释放量。

## 最终 30 分钟复查

最终窗口为北京时间 **00:54:08–00:56:08**，距离最后一次发布结束约 **31.8 分钟**。等待期间未高频轮询，未执行额外扫描或改造。

| 检查 | 最终结果 |
| --- | --- |
| 五个目标容器 | 与即时验收为相同容器 ID，restart_count 均为 0 |
| 部署代码 | 五个容器全部修改文件的哈希匹配各自构建输入 |
| 初始化并发 | 16 槽；空闲每分钟约 115–117 次 claim，无实际领取 |
| Control | 稳态镜像写事务为 0；Bus、初始化镜像无差异；17 个控制操作均 SUCCEEDED |
| Read | 四源 checkpoint=head，error 均为空 |
| 正常采集 | Raw 数从收尾即时验证的 8,894 增至 8,906 |
| 历史失败/交易待办 | 初始化仍为原有 15 SUCCEEDED、3 FAILED；Executor 原有 DONE 85、FAILED 1、WAIT_QUOTE 3 未因发布改变 |

最终 120 秒窗口的核数：

| 组件 | 发布前 | 最终窗口 |
| --- | ---: | ---: |
| Initialization | 0.235 | **0.009** |
| Control | 0.156 | **0.029** |
| Transport Maintenance | 0.172 | **0.075** |
| Runtime Scheduler | 0.394 | 0.530 |
| Message Bus | 0.373 | 0.406 |

前三个组件合计从约 0.563 核降至约 0.112 核，相差约 **0.451 核**，相当于四核机器约 **11.3 个百分点**的容量。该差分是观测窗口结果，Transport 的任务组成仍有变化，不能充当严格同负载因果测量。

整机最终忙碌度约 **99.65%**，iowait 约 **0.14%**。主要剩余组件为 Chrome Supervisor 1.051 核、Site Access 0.665 核、Codex Worker 0.544 核、Scheduler 0.530 核、Message Bus 0.406 核。它们包含实际业务和其他开销，不能全部认定必要，也不能全部认定可以直接关闭。

本轮五项修复及其验收已经完成；没有以低 CPU 百分比替代业务正常运行的标准。**整机持续高负载仍然存在**，后续若继续优化，应针对上述剩余热点重新归因，不把本轮局部降耗表述成全机问题已彻底解决。CPU-04 以及方案中的条件研究项没有擅自实施。

## 留痕

本地诊断目录：[eval/cpu_audit_20261019](../eval/cpu_audit_20261019/)。主要凭据：

- `implementation-tests.xml`、`implementation-extra-tests.xml`、`w3-phase-regression.xml`。
- `implementation-before-inventory.json`、`implementation-before-cpu.json`。
- `build-release.json`、`release-p0.json`、`release-p1.json`、`followup-release.json`。
- `accept-p0.json`、`accept-all-immediate.json`、`accept-followup-immediate.json`。
- `w3-live-readonly-validation.json`，以及完成后生成的 `accept-final-30min.json`。

服务器发布回执位于 `/var/lib/doxagent/releases/cpu-6098235c7036-20261020/`。完整容器配置留在服务器受限文件中，报告和本地公开回执不复制凭据。
