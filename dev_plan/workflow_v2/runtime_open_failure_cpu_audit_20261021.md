# 美东 2026-10-20 开盘后 Runtime 失败与资源争用排查

排查日：北京时间 2026-10-21。只读取生产数据库、日志、运行源码与资源记录；未修改生产配置、重启、重放 Case 或调用付费模型。

## 1. 结论

失败不能全部归因于 CPU。当前同时存在三类问题：

1. **W3 本地线程准备/启动故障为主，资源争用与其高度相关。** 直接证据落在 Capsule 的 30 秒 boot-ready 等待及生命周期恢复 RPC，而不是模型研判。成功 Fork RPC 本身很快。
2. **百炼 W1R2/W2R2 的 120 秒请求超时。** 本地资源可能放大墙钟耗时，但现有回执没有首字节、连接阶段、上游排队时间，无法进一步确认是供应商长尾、网络阻塞还是本机调度影响，不能直接称为 CPU 导致。
3. **结构化/语义输出错误独立存在。** OLD 缺少 reference_ids、事实归属不一致、返回召回候选外的 Policy，均有具体输出证据，不能用 CPU 紧张解释。

W3 专项交接另见 `w3_startup_failure_handoff_20261021.md`。

## 2. 时间与计数

服务器时钟核对时为 UTC 2026-10-20 16:26，亦即美东 10 月 20 日 12:26、北京时间 10 月 21 日 00:26。因此“本交易日开盘”取 **UTC 10 月 20 日 13:30 / 美东 09:30 / 北京时间 21:30**。

主数据库快照约为 UTC 16:27:24（美东 12:27）。各表顺序读取，生产继续运行，不能将稍后 Worker 快照的状态与主快照强行拼成同一时刻的业务总量。

开盘后创建的 **92 个 Case**：71 COMPLETED、19 FAILED、1 UNAVAILABLE、1 PENDING_W3。终态失败合计 **20 个，21.7%**。

| 直接失败类别 | Case 数 | 位置 | CPU 关系判断 |
|---|---:|---|---|
| READY preparation / TimeoutError | 12 | W3 trade_decision/PREPARE | 同时有严重 CPU 争用、boot 超时，支持高度相关；并非已证明每个都只由 CPU 导致 |
| READY preparation / Codex stdout closed | 3 | W3 trade_decision/PREPARE | TransportClosed；真实进程退出原因未完整保留，另有共享客户端关闭可能放大错误 |
| provider_timeout | 3 | W1R2 | 120 秒百炼请求超时，来源需继续分离测量 |
| structured_output_validation_failed | 1 | W1R2 | 明确的 OLD 引用字段缺失，与 CPU 无直接关系 |
| provider_request_failed / APIConnectionError | 1 | W1R2 | 连接错误；现有脱敏诊断不足以区分 DNS/TLS/代理等具体原因 |

还有 **1 个盘前创建、开盘后才失败的 W3 Case**：`case_c9378c7d671c41c5b0a3ada8f2262806`，UTC 13:22:33 创建、13:31:37 失败，同为 w3_ready_prepare_failed。若按“开盘后发生终态失败”统计，应是 **21 个**，其中 W3 16 个、W1 5 个。

另有 **1 个 COMPLETED Case 的后置 operations 失败**：`case_59fc7f7248b84709bfaa0af665214b99`（ONDS），START_THREAD timeout。其交易判定已完成，不能加进交易判定失败数。

## 3. W1/W2：失败 attempt 与最终失败分开

以下只统计上述开盘后创建的 92 个 Case；成功耗时是本轮 provider 调用回执耗时，不是 Case 的端到端处理耗时。

| 节点 | 成功 attempt | 失败 attempt | 成功 p50 / p95 / max（秒） |
|---|---:|---:|---|
| W1R1 | 92 | 0 | 5.01 / 9.86 / 13.53 |
| W1R2 | 87 | 26 | 27.77 / 80.47 / 106.00 |
| W2R1 | 92 | 0 | 5.03 / 10.96 / 15.12 |
| W2R2 | 23 | 5 | 43.43 / 84.89 / 92.51 |
| W2R3 | 6 | 0 | 13.63 / 20.35 / 25.01 |

31 次失败 attempt 中，22 次在同 Case、同轮有更高 attempt 成功回执。W2R2 的 4 次 timeout 和 1 次候选外 Policy 错误全部后来成功；本样本 **没有 W2 终态失败 Case**。

运行配置由实际 turn 回执确认：W1R1/W2R1 为 OpenRouter Haiku 5.5 high；W1R2/W2R2 为百炼 qwen3.8-flash medium、120 秒；W2R3 为同一百炼模型 low、120 秒。不同节点的差异不能视为同模型/同供应商的对照实验，但足以反驳“整机所有请求都同等变慢”。

### W1R2 的具体错误

- 19 次 provider_timeout，W2R2 另有 4 次；基本聚集在 120 秒左右。记录中 timeout_seconds=120.0，确实是现行请求阈值，不是页面误显示。个别回执达到 124.9 秒，与本地调度/异常收束额外耗时相容，不能据此反推出供应商实际推理耗时。
- `case_cb20d7409a024de5afb5043476168a6f`（BE）：两次输出都在 reason 中引用 E121/E120/E119，却没有输出结构化 reference_ids；result=OLD，故验证失败。Schema 的 OLD 引用要求合理，不建议放开 OLD 无引用，也不能从自然语言 reason 自动猜结构化身份。
- 其他可重试输出错误包括 Fact attribution 不属于 reference_ids。修复方向应针对有效结构化字段和具体错误回灌，不扩大 Event 读取范围、不改变 NEW/OLD 业务语义。
- `case_72748f0cdfa2495884de156677b4b723`（MU）：15.5 秒后 APIConnectionError，直接 UNAVAILABLE。`transport.py::_safe_transport_error` 未单独处理连接异常，最后映射为不可重试的 provider_request_failed。这是一个可独立修复的可靠性缺口：按明确异常类型识别瞬时连接故障，复用既有有界重试，并保留脱敏的底层异常类型/原因；认证、余额与业务错误继续不可重试。

## 4. 服务器资源事实

机器为 **4 核、约 15 GiB 可见内存**。当前约 20 秒增量采样：

| 容器 | 占整机 CPU | 说明 |
|---|---:|---|
| chrome-supervisor | 36.1% | 约 1.45 核；当前最大消费者 |
| site-access | 15.5% | 包含浏览器及服务活动 |
| scheduler | 15.2% | 约 0.61 核 |
| message-bus | 9.2% | 约 0.37 核 |
| projector | 5.3% | 约 0.21 核 |
| codex-worker | 2.9% | 当前采样值较低，不能据此否认此前冷启动受抢占 |

这只是当前区间归因，不能拿当前百分比冒充开盘每一分钟的历史容器占比。相关容器没有 CPU quota 节流；CPU 压力来自共享主机竞争。当前 load 约 25，CPU PSI some avg60 约 90%。

sysstat 的历史十分钟采样提供了直接的同时段证据：

- UTC 13:40—14:00（美东 09:40—10:00）：idle 约 0.01%，runq 25—39，I/O wait 基本为零，主要是 CPU 执行资源不足。
- UTC 14:50—15:20：I/O wait 约 38%—41%，load 一度 71；vda 平均队列约 104—118、await 约 21—26 ms，读取约 135—173 MiB/s。此时不能把高 load 全部解释为 CPU 计算。
- UTC 15:40 的十分钟区间 I/O wait 47.23%、vda await 27.88 ms、平均队列 125.7；15:50—16:00 又转回 CPU 几乎打满。
- 内存可用量在部分区间降到约 1.2—1.8 GiB；有 swap 活动，但 I/O 高峰的换页量较小，不能将大量磁盘读取全部归因于 swap。
- 检索该窗口 kernel journal 未发现 OOM kill、内核 I/O error、segfault 等匹配记录；这不等于所有用户态进程退出原因都已查明。

历史没有足够的逐进程 I/O 记录，**尚不能确认磁盘读取峰值具体由构建、浏览器、数据库还是其他作业贡献**。当前容器占比不能填补这一证据缺口。

## 5. 版本与处置建议

Scheduler 和 Worker 均于 UTC 13:21 启动，构建提交 `160199b7a2009ad59c2b80b5d5941e9a96d66383`，restart_count=0；当前远端 checkout/main 为 `bfe1a86b81b0d2624e1b2221bb0fb4a0c840d1c4`。对实际读取的 14 个相关模块逐一比对，运行容器内容与当前本地 main 相同，不能仅凭构建提交不同认定这轮是遗漏部署。

建议优先处理：

1. 将 W3 专项证据交给负责线程准备的会话，针对 boot-ready 超时、恢复查询和共享客户端故障传播处理，避免只提高 Fork RPC 超时。
2. 给业务启动留出 CPU/I/O 资源；先约束非关键浏览器背景活动及冷启动/补池并发，避免靠继续提高并发抢占更多资源。若维持当前全量负载，扩容也是合理选择，不能承诺代码优化必然令 4 核机器足够。
3. 百炼连接异常改为类型化、有界重试；为超时补充最小连接/首字节诊断，再决定是否改模型、推理强度或请求阈值。本轮没有重跑模型或擅自改配置。
4. 针对 W1R2 实际缺字段 badcase 修复输出/纠错契约，保持原引用合法性约束。

这次样本未出现上一轮的 SQLite database is locked、service closed 状态脱节、账户 usage_limit_exceeded、429 或 W2R3 reason 校验终态失败；仅说明本窗口未观察到，不代表永久消失。

## 6. 本地证据

目录：`eval/runtime_open_audit_20261021/`。

- `snapshot.json`：Case、turn、Effect、W3 stage、task 的主快照。
- `failed_cases.csv`：开盘后创建的终态失败，以及 COMPLETED/operations 失败清单；盘前跨入案例另列于正文。
- `worker.json`、`worker_ops.json`、`timings.json`：READY slot、operation、Worker job、运行源码及细分启动计时。
- `cpu.json`：当前约 20 秒 CPU 增量、容器与进程归属。
- `history-raw.txt`、`memory-io.txt`：sysstat 历史 CPU、队列、内存、换页、磁盘记录。
- `inventory.json`：容器构建提交、启动时间、重启次数。

Boundary timing 有 progress/final 多份文件；本报告按 identity + plane + started_at 去重、保留 elapsed_ms 最大记录后统计，不能直接用 JSON 文件数量当 attempt 数。
