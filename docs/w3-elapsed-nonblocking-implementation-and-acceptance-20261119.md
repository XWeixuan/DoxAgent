> 2026-11-20 更新：已随 100/150 秒阈值变更发布生产，见[发布与清理记录](w3-elapsed-100-150-production-release-20261119.md)。下文保留原开发验收时间点的历史状态。

# W3 Elapsed：非阻塞修复实施与验收

2026-11-19，北京时间。依据[修订方案](../dev_plan/workflow_v2/w3_elapsed_reliability_root_cause_and_lean_repair_plan_20261119.md)完成开发、隔离测试及本地延时验收。**本次代码尚未部署生产**；上一轮生产 53 Case / 106 分支的观测属于原子登记旧版本，不能作为本次发布验收。

## 已实施

1. **正常 v6 复用冻结值。** 保留原有 `register_trade_bundle()` 业务事务，将返回的权威原点携入当前 Case，供两条分支使用；删除原来每分支一次 `read_io(time_context, ...)` 数据库查询。仅缺时间原点的历史入口，不再单为计时重新进入登记 writer。
2. **缺失或异常时使用内存兜底。** 原点 helper 不再做独立 INSERT。合法冻结值优先，其次为 Journal 实例内的有界缓存；兼容/恢复路径至多做一次 `timeout=0` 只读查询。若原点缺失、数据损坏、Case 不匹配或读失败，使用首次调用前捕获的真实 UTC，并共享给本 Case 后续分支。缓存最多 2,048 项，锁内没有 I/O。
3. **继续启动，不等待时间恢复。** 控制目录创建失败、控制文件写失败、缺时间元数据均不抛业务异常、不重试、不产生恢复 Effect。写失败尽力删除旧控制记录，避免误复用。异常通过已有有界异步诊断记录，避免同步日志写入阻塞启动。
4. **保持原生轻量路径。** 原生补丁和二进制未改变；继续一次读取、单调计时、每次研判请求前追加当前档位。未增加每轮数据库/文件查询、定时中断、协议或新状态机。旧 frozen request/hash、paid receipt、未知受理保护保持原样。

“进程内存兜底”是明确的降级状态：若没有既有持久证据，进程重启后无法恢复此前全部耗时；记录为可能缺少先前 Elapsed，仍继续 W3，不为恢复完整计时等待。控制通道彻底不可用时，允许该 Turn 无动态提醒，下一正常 Turn 自然恢复。高优需求按用户要求服从启动时效硬约束。

## 测试

**Python 统一定向回归：221 passed。** 覆盖原点真实时钟、门锁/SQLite writer 持续占用、冻结值零数据库 I/O、共享内存原点、坏值/读失败、仅缺原点不重新注册、目录/文件异常仍启动 SDK、缺元数据继续执行、旧 Job hash、v5/v6、双分支、Provider 恢复、线程生命周期、回放和 Worker SDK。

**原生 Harness：4 passed。** 使用现有 Linux patched binary、官方 SDK 和本地 mock 模型服务；检查真正发送到 mock 服务的请求内容，覆盖 120/180/240 秒提示、工具续轮、传输重试、Code Mode、Fork/Resume、压缩、Operations 排除，以及缺失/损坏/错误线程控制记录时继续执行、下一 Turn 恢复提醒。没有调用真实模型或重放生产交易。

锁竞争测试在另一个线程持续持有门锁、独立连接持续持有 SQLite `BEGIN IMMEDIATE` 时，验证原点调用在 250 ms 测试上界内返回，且持锁者尚未释放；缓存命中继续复用第一次值。数据库错误测试确认一次零等待读后直接内存兜底，不重试。

扩大回归发现一项原 HEAD 已失败的旧 fixture：消息为 8 月 29 日、回放时钟为 10 月 10 日，却要求 READY；同时默认模型已变化。已将该 fixture 的接收时间固定到自己的回放时钟，显式固定 Trade 测试模型，并保留 Operations 的独立 Luna 路由断言。未放宽生产 12 分钟门控，也未更改生产模型。

时间 helper、Worker 控制及对应时间测试的 Ruff 检查通过；修改过的旧大文件与旧 fixture 的既有 lint 问题数未增加。`git diff --check` 通过。

## 本地延时对照

同一临时数据库、同一合法原点、每轮两条分支，共 500 轮。基线使用当前 HEAD 的原 helper 加原来的 `read_io`；新路径直接使用冻结内存值。控制文件测试也各执行 500 次。

| 路径 | P50 ms | P95 ms | 最大 ms | 原点 SQL |
|---|---:|---:|---:|---|
| 原双分支 `read_io` 查询 | 0.2311 | 0.2793 | 12.4604 | 1,000 次读，0 次写 |
| 新双分支冻结值传递 | **0.0049** | **0.0053** | **0.0243** | **0 读、0 写** |
| 现有控制文件正常写入 | 0.5138 | 0.7015 | 2.7646 | 无 |
| 控制文件写失败并降级 | 0.3083 | 0.4276 | 1.2716 | 无 |

这些数值是本机该时间元数据路径的测量，不是整个 W3 或生产开盘 P95。实测未增加秒级启动依赖；源码上也未增加等待、退避或技术恢复。正常路径减少数据库查询及线程池切换，文件异常路径只做一次尝试和尽力清理。

## 证据及上线边界

- [Python JUnit](../artifacts/audits/w3_elapsed_reliability_20261119/python.xml)、[原生 JUnit](../artifacts/audits/w3_elapsed_reliability_20261119/native.xml)、[原生请求回执](../artifacts/audits/w3_elapsed_reliability_20261119/native_receipts)。
- [延时数据](../artifacts/audits/w3_elapsed_reliability_20261119/latency.json)、[对照脚本](../artifacts/audits/w3_elapsed_reliability_20261119/benchmark_nonblocking.py)。
- 核心文件：`persistent_runtime_v2/w3_time_awareness.py`、`w3.py`、`w3_execution.py`、Worker `time_awareness.py`。变更已追加至 `changelog`。

本次验收范围为代码、隔离原生请求及本地延时。生产发布后应单独核对 Scheduler/Worker 源码版本和自然 Case 的原生片段；不能以容器健康或本次历史窗口宣称生产新版本已经验收。现有单响应长生成仍不支持中途追加提示，此边界没有被隐藏或改造成定时取消器。
