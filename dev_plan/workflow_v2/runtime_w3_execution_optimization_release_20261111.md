# W3 执行架构优化：生产发布与一次性积压恢复

日期：2026-11-11。正文时刻为北京时间 CST，证据 JSON 使用 UTC。

## 1. 当前结论

修复已经进入 origin/main 并发布生产，Scheduler / Worker 正在运行。原 R1 发布和其他服务镜像保留。用户追加授权的一次性历史恢复正在执行；00:17:43 又按用户明确要求上调为 **16 个历史 Case 并行**，新实时 Case 不受这份历史名单的恢复槽位限制。

**发布已完成；累计非 SDK P95≤5 秒尚未通过，不能称为整轮性能验收完成。** 固定新 Case cohort 的 42 例中，仅 3 例具有工具所需的完整 DB 收尾证据，39 例尚在运行或缺少完整 trace；这 3 例累计内部耗时分别为 7.50、11.92、20.92 秒。已有超过底线的直接反例，样本和证据覆盖也不足以代表整体 P95。

## 2. 实际发布范围

| 对象 | 已运行镜像 / 实现 |
| --- | --- |
| Scheduler | `sha256:59eb5011ef5e680e8a896619d684035979e4f2d9a89d181e9d4ed1d67db7d010` |
| Worker | `sha256:0f57319caca17e3ed3b899b809b82125d04432fa9f32fcf4e21565f734f3f8ac` |
| 主修复 | `e697595e`，Provider/结果消费、原 Job 恢复、Future/结果写入、Ready 领取 |
| 一次性恢复 | `4245daea`，临时恢复脚本；Worker 重启保留显式 future next_retry_at |
| 临时脚本资源清理 | `07c61b2d`，明确关闭短连接，防止读连接/缓存等待 GC 累积 |

通过现有 canonical `storage_release` 链、实际接受镜像上的精确文件 overlay 发布，没有全量重建其他角色，也没有同步镜像清理或全库历史扫描。Scheduler 的 8 个替换文件、Worker 的 Ready 与 jobs 两个文件已核对实际 SHA256；canonical state 与运行镜像一致。原脏开发工作区未清理，远端 changelog 的 7,384 字节私有尾部完整保留。

只读统计工具的修正和本文属于后续 Git 更新，不需要再次重建业务镜像。

## 3. 首次直接发布发生了什么

23:44:45 Worker 更新，23:45:34 Scheduler 启动。旧错误 Provider pause 被清除，原 pending deadline 开始恢复。

23:45:50–23:48 的限定现场观察中：

- Worker 实测最高 59 个 running Job，queued 最高 122 个；这些峰值不是同一采样时刻。
- Worker cgroup 内存约达 12.91GiB、PID/线程约 2,002；可用 RAM 从约 16GiB 快速降至最低约 4.13GiB。
- 内存压力及换页开始增加，Worker 健康检查超时；当时没有宿主机 OOM 或业务容器 OOM。
- 已立即停止这两个容器，恢复约 18.4GiB 可用 RAM。ExitCode137 来自此次 5 秒安全停止后的强制退出，不是 OOMKilled。

这次直接观测确认了之前提出的发布风险：解除错误等待后，大量历史阶段同时进入准备和模型执行，物理资源快速增加。它不证明所有 I/O 都来自 SQLite，也不能据此把某个线程池当成唯一根因。未通过延长 pause、调大 Swap、给新实时 Case 加门槛或恢复旧错误串行锁来掩盖问题。

## 4. 用户授权的一次性恢复

用户明确选择：**允许历史积压一次性限速，新实时直派**。

以 23:48:50 为 cutoff，在停止状态固定名单，包含 163 个历史 Case、163 个未完 W3 Effect、144 个已有 active Job。之后进入的新实时 Case 不加入名单。名单文件：

`/var/lib/doxagent/releases/w3-backlog-recovery-20261110.json`

具体采用现有字段完成，未新增业务队列/数据库表或永久执行通道：

1. 对名单内原 Effect 暂存控制字段，用原 `available_at` 暂停其调度；只释放已死亡 owner 的运行控制，不修改 Case 业务判断、预算、冻结输入或历史模型结果。
2. 对已有 Job 只设置原 `next_retry_at`，不改 request、request_sha256、Job ID 或模型参数。Worker 重启时保留更晚的显式 next_retry_at，原基础设施恢复/结果文件收养逻辑继续执行。
3. 临时脚本最初每次放行 2 个历史 Case，00:17:43 按用户要求改为 16；原 Job 与 Effect 都按原身份恢复。Case 的两阶段继续由原业务编排推进。
4. 原业务完成或失败收尾后释放一个恢复位置；这表示“已收尾”，不等于每个历史 Case 都成功交易或成功完成。
5. 原快照成员和控制备份不可变；capacity 是用户可明确调整的一次性操作参数。小型 progress 文件记录 active/done/released。先保存放行所有权，再改控制字段；崩溃后幂等恢复，不重放已提交交易。
6. 两个 Case 未完成时不反复写调度字段、也不重写原大快照；只做短索引读。名单处理完毕，脚本自动退出。

临时 OS unit：`doxagent-w3-backlog-20261110.service`。它只有约 12MiB cgroup 内存和极低 CPU 用量，不参与新实时 Case 调度。

截至 00:14:46，名单已收尾 9/163 个，2 个正在恢复。自然生产同时观测到 14 个名单外 Job 运行，证明它们没有受 2 个历史位置限制。144 个原 Job 的 request_sha256 逐一核对未变。

00:17:43 用户要求升至 16 并行。已只修改原 manifest.capacity 并重启临时脚本，原名单、progress、Job 和两个业务容器未重建；调整前已收尾 11 个。操作回执位于 `/var/lib/doxagent/releases/w3-backlog-capacity-16-20261111.json`。下节 11 分钟观察是最初 2 并行窗口，不能冒充 16 并行的验收。

追加的 16 并行观察：00:18:35–00:21:35，共 13 次、180 秒采样，可用内存最低 8.51GiB、结束 11.26GiB，Worker 峰值 7.98GiB；业务容器无 OOM/自动重启、kernel OOM 增量 0，结束时内存 full PSI avg10=0。00:21:06 时已收尾 17 个、16 个同时推进。该短窗不代替持续负载验收。

若临时脚本异常退出，未放行的历史项保持等待，新实时 Case 不被它拦截。恢复时重用同一 manifest/progress，禁止重建名单或把全部 deadline 批量改成 now：

```bash
sudo systemd-run --unit=doxagent-w3-backlog-20261110 --collect \
  --property=MemoryMax=128M --property=CPUWeight=10 \
  /usr/bin/python3 /home/ubuntu/doxagent/scripts/ops/w3_backlog_recovery_20261110.py run \
  --manifest /var/lib/doxagent/releases/w3-backlog-recovery-20261110.json
```

该命令只在原 unit 已停止/不存在时用于恢复，不重复启动两个控制进程。

## 5. 临时脚本的资源缺陷与修正

第一版临时脚本误将 `with sqlite3.Connection` 当作关闭连接。该上下文只提交/回滚；短轮询积累的连接/缓存没有及时释放，00:05:31 触及其自身 128MiB MemoryMax，发生一次 **unit 范围的 OOM kill**，不能将整个窗口写成“kernel OOM 为零”。该过程没有导致两个业务容器 OOM 或宿主机失去响应。

已在 `07c61b2d` 改为 finally 显式 close，并补充重复空闲迭代所有连接均关闭、无重复 progress/control 写入的回归；观察探针也补齐 close。没有扩大该脚本的内存限额。从同一 progress 恢复后，00:06:55 起持续运行，实测约 12MiB cgroup 内存、约 21MiB RSS、VmSwap0、3 个 FD，未再次触及限额。

## 6. 受控恢复后的 11 分钟资源观察

窗口：00:00:22.603–00:11:22.603，共 34 次采样。

| 指标 | 实测 |
| --- | --- |
| 可用 RAM 最低 / 结束 | 9.27 / 10.35GiB |
| Worker cgroup 峰值 / 结束 | 7.49 / 5.82GiB |
| Scheduler cgroup 峰值 | 1.51GiB |
| 业务容器 OOM / 自动重启 | 0 / 0 |
| kernel OOM kill 增量 | 1，仅上述旧临时脚本自身限额事件 |
| 全机 swap-out 增量 | 386.91MiB；包含旧临时脚本的资源问题，不宣称没有换页 |
| 结束时 memory / IO full PSI avg10 | 0.07 / 0.10 |
| Provider pause | 观察到恢复后为 null，没有旧普通 429 幽灵探测 |

这是恢复窗口验收，不能代替下个开盘峰值或一小时持续负载验收。没有新增交易回放或真实模型 canary，只观察自然生产与原积压续接。

## 7. 性能验收仍未通过，证据没有被抹平

修正只读工具的两个实际问题：Effect.created_at 在 payload_json 内；初始 Future 让出时的 trace 不是最终 Effect 收尾 trace。现在必须有覆盖业务 commit / Effect.updated_at 的最终 trace，且每个阶段 Job 都有 SDK 区间覆盖，才算完整样本。缺最终 trace 的 Case 明确计 incomplete，不允许以初始两秒 trace 误报为完整 Case 两秒。

固定 00:00–00:11:22 创建的自然 Case cohort，共 42 个进入 W3；采样时可完整计算 3 个、其余 39 个未完成或缺日志。完整子集的累计非 SDK 时长为 7.50–20.92 秒，子集 P95=20.92 秒。**不能将此子集当成整体 P95；但它已提供未达到 5 秒底线的反例。**

已保存进一步定位所需的分段，而非把超时归咎于 Provider：

- `case_1a1c0d6c6cbc45dca1852edb6c84c71e`：Job 创建至 SDK 派发约 4.69 秒，两阶段累计非 SDK 约 11.92 秒。
- `case_110829fc37084f4cb2fdb47ddf10b06e`：operations 的 Worker finished 至业务 commit 约 2.80 秒，两阶段累计约 7.50 秒。
- `case_fbc43d3c06a1459c868b78e0a094370b`：存在局部真实限流等待，工具已单独扣除，累计内部时间仍约 20.92 秒；不能将整个 Case 都排除为 Provider 样本。

剩余验收需要继续定位 Job 启动/准备、Effect 交接与结果提交的分段成本，并在原文件 trace fallback 中有界补足缺失证据；不能为了日志完整强迫诊断落库抢占业务 writer，也不能降低验收口径或延长 SQL timeout。未在本次发布期间叠加新的持久调度架构来掩盖差距。

## 8. 验证与证据

- 原主修复整合 main 后 158 项回归通过。
- 一次性恢复及 Worker/实时并发相关 35 项回归通过。
- 连接释放和严格 trace 完整性修正的定向回归通过。
- 实际运行 10 个源文件 hash、canonical 镜像、Worker dispatcher RUNNING / ready / processes_recovered=true / 无待结算均已核验。
- 停机前遗留一例 W1/W2 `provider_dispatch_unknown`，继续保留原“未知受理不能重派”保护，未将它擅自改成未受理重跑。

证据目录：`eval/w3_execution_optimization_20261110/`，包括原无约束恢复采样、受控恢复采样、资源摘要、源文件核验、最终严格时效统计和剩余长尾分段。原 manifest/progress 含运行控制备份，保存在远端 private release 路径，不作为源码批量覆盖生产数据。
