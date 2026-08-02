# CDECR Bulk Epoch V3 Stage Graph 30 篇真实 A/B 验收报告

> 日期：2026-08-03
> 实现基线：`ba5f166`；主体实现：`7a0a7f8`；验收期间补充 SQLite 单写事务门
> 语料：固定 `grounder_quality_30_manifest.json` 30 篇，同源、同顺序、同 fingerprint
> Gold：复用原 268 条 Mention Gold 与 2026-07-28 Package 人工边界；没有重新标注
> 对照：`CDECR_BULK_EPOCH_30_REAL_AB_ACCEPTANCE_REPORT_20260802.md` 及其正式 R2 Registry
> 正式 Registry：`D:\DoxAgent_CDECR_Acceptance_20260803_StageGraph_V3_R3\cdecr_30_stage_graph_v3.sqlite3`

## 0. 结论先行

本轮 **工程可靠性与效能通过，但端到端质量不通过，因此不能把整套修改无条件判定为生产质量已达标**。

1. 技术运行成功：单文档 30/30、跨文档 30/30，stderr 为空；199 Atomic、116 Package 均完成 finalize。幂等复跑的模型调用、Mention、Atomic、Package 增量均为 0。
2. 墙钟从 12,275,833 ms（3:24:35）降至 2,726,440 ms（45:26），下降 **77.79%**；方案的主要效能目标真实成立。
3. Input Token 从 3,772,039 降至 1,582,413，下降 **58.05%**；总 Token 从 6,433,077 降至 3,757,142，下降 **41.60%**。
4. N12 Input 从 1,751,219 降至 161,861（**-90.76%**）；N13 Input 从 708,000 降至 225,061（**-68.21%**）。这是本轮最明确的降本收益。
5. Evidence 275/275 VERIFIED，且异常未造成文档失败；该层通过。
6. Mention Precision 72.43%→73.71%，但 Recall 73.51%→69.03%；Field total 81.64%→76.14%；均未达标且召回/字段质量回归。
7. N9 candidate coverage 达 100%，但 MERGE Precision 100%→92.00%，conditional MERGE Recall 82.30%→69.70%；N9 质量回归明显。
8. Package 在上一轮 R2 的低点基础上显著恢复：发布口径 P/R 82.57%/32.95%→本轮 98.94%/65.18%。但本轮覆盖 96 个 Atomic、上一轮仅 83 个，不能直接把这组变化全部归因于改进。将上一轮 Package 重投影到本轮相同 96-Atomic 集合后，上一轮 P/R 为 90.68%/76.64%，本轮为 98.94%/65.18%：Precision +8.25pp，Recall **-11.46pp**。
9. 同口径碎片化也未恢复：fragmented Gold groups 3→8、excess components 5→16、missed links 267→398。Micron FQ3 earnings 从上一轮重投影的 3 个组件变为 7 个组件；虽优于 R2 发布结果的 13 个组件，但仍明显过拆。
10. 回滚判断：保留 Stage Graph、异步 lane、全局 Field/Atomic 计划、task/artifact/幂等、N12/N13 字典降本与 Registry 单写事务门；不应整体回滚。需要优先修正 N9 global snapshot 的过保守合并与 N12 Wave B 召回/Apply，再复验质量。当前结果不支持把质量相关 reducer 直接视为最终完成态。

## 1. 执行范围与异常熔断记录

### 1.1 正式实现

- BULK_EPOCH 仅保留 `BULK_EPOCH_V3_STAGE_GRAPH`，旧 component/finalizer 与 N12/N13 shadow/canary 产品路径已删除。
- 物理并发 M1/M2/M3/M4 为 32/48/48/16；Field 32，N9/N12/N13 各 24，repair 8。
- M2/M3/M4 使用 DeepSeek 官方 `deepseek-v4-flash` strict structured request；模型 HTTP 为原生 async。
- Field、Atomic、Package、N13 依次使用冻结快照，N12 使用 Wave A/Wave B 与单一 reducer，N13 完整执行 bounded pair plan。
- 产物和任务状态以 content-addressed artifact、task ledger 和 finalized epoch 留存。

### 1.2 两次未纳入指标的失败启动

1. `StageGraph_V3`：启动即失败，0 次模型调用。原因是本机 `.env` 仍为 `CDECR_N12_WIRE_PROTOCOL=shadow`，与新代码强制 `on` 冲突。只修正本机开关后使用新目录重跑。
2. `StageGraph_V3_R2`：单文档 30/30、586 次调用后，在 Field 并发写入发生 29 个 `OperationalError`、4 个 `IntegrityError`，跨文档 0/30。根因是语义 Apply 虽有 BulkWriter，但 Field link、audit、task ledger 仍由多 worker 直接竞争 SQLite 单写事务。
3. 修复：Registry 增加进程内、短事务级可重入单写门；LLM 请求与 CPU 准备不在锁内。新增 16 worker/64 transaction 回归。R3 Field 436/436 成功，未再出现 SQLite 错误。

失败的两个目录完整保留为诊断证据，但所有正式指标只来自全新 R3 Registry。

## 2. 成功率、可靠性与局部失败

| 指标 | 上一轮 R2 | 本轮 R3 | 结论 |
| --- | ---: | ---: | --- |
| 单文档成功 | 30/30 | 30/30 | 持平 |
| 跨文档成功 | 30/30 | 30/30 | 持平 |
| stderr | 0 | 0 | 通过 |
| 最终 Mention | 272 | 251 | -21，召回风险 |
| 最终 Atomic | 178 | 199 | +21，过拆风险 |
| 最终 Package | 101 | 116 | +15，碎片化风险 |
| 幂等复跑模型调用增量 | 0 | 0 | 通过 |
| 幂等复跑 Mention/Atomic/Package 增量 | 0/0/0 | 0/0/0 | 通过 |

局部模型格式失败共 14 次：Grounder 6、Grounder missing recovery 3、Judge 2、N9 core 1、N9 escalation 2。另有 7 个 N12 Wave B assessment 在业务校验后仍不可判，被局部降级为 `N12_UNJUDGEABLE_FAILED_SINGLETON`。这些问题均未扩大为文档或 epoch 失败。

7 个 N12 局部失败包括 4 个市场收盘/涨跌事实、HBM4 qualification、Micron-Anthropic 投资子事实和 Micron 近 16% 股价变化。当前 ledger 只保留统一错误码，缺少具体字段路径，因此可以确认“模型返回通过 transport、业务 assessment 非法”，但不能从现有审计再区分是 candidate coverage、relation 还是 selected ID 违规。这是诊断可观测性的一个有限缺口。

## 3. 墙钟、Token 与节点占比

### 3.1 总量 A/B

| 指标 | 上一轮 R2 | 本轮 R3 | 变化 |
| --- | ---: | ---: | ---: |
| 首轮墙钟 | 12,262,145 ms | 2,725,633 ms | **-77.77%** |
| 含幂等复跑总墙钟 | 12,275,833 ms | 2,726,440 ms | **-77.79%** |
| 模型调用 | 992 | 816 | -17.74% |
| Input Token | 3,772,039 | 1,582,413 | **-58.05%** |
| Output Token | 2,661,038 | 2,174,729 | -18.28% |
| 总 Token | 6,433,077 | 3,757,142 | **-41.60%** |
| aggregate model latency | 22,315,413 ms | 16,869,771 ms | -24.40% |

评估器的 M4 调用不计入上述 workflow Token。Output 包含 DeepSeek thinking token；aggregate latency 是并发请求耗时之和，不等于墙钟。

### 3.2 真实 stage 墙钟占比

| 阶段 | 墙钟 | 占首轮墙钟 |
| --- | ---: | ---: |
| 单文档 N1-N5 | 545,605 ms | 20.02% |
| Field | 619,712 ms | 22.74% |
| Atomic / N9 | 654,855 ms | 24.03% |
| Package / N12 | 377,692 ms | 13.86% |
| N13 | 527,607 ms | 19.36% |

旧 R2 的单文档/跨文档墙钟约为 1,108,289/11,153,856 ms；本轮分别下降 50.77% 和 80.45%。跨文档长尾从 3 小时以上压缩到约 36 分钟，是 Stage Graph 的核心收益。

### 3.3 节点 Token 与 aggregate latency 占比

| 节点 | calls | Input | Input 占比 | Output | Output 占比 | aggregate latency 占比 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| N9 core | 64 | 277,504 | 17.54% | 315,925 | 14.53% | 14.24% |
| N5.5 Field | 247 | 272,375 | 17.21% | 85,746 | 3.94% | 4.92% |
| N13 Package merge | 37 | 225,061 | 14.22% | 353,418 | 16.25% | 17.10% |
| Judge | 27 | 199,453 | 12.60% | 186,477 | 8.57% | 9.57% |
| N12 Package assignment | 9 | 161,861 | 10.23% | 223,134 | 10.26% | 9.34% |
| Grounder | 30 | 143,455 | 9.07% | 606,850 | 27.90% | 26.10% |
| N9 escalation | 14 | 64,884 | 4.10% | 193,967 | 8.92% | 8.65% |
| Grounder item repair | 12 | 53,612 | 3.39% | 54,049 | 2.49% | 2.62% |
| Grounder missing recovery | 6 | 28,199 | 1.78% | 106,943 | 4.92% | 4.52% |
| Dreamer | 30 | 38,092 | 2.41% | 37,797 | 1.74% | 1.35% |
| 其余 embedding/recall/repair | 340 | 117,017 | 7.39% | 10,423 | 0.48% | 1.61% |

关键环比：

- N12 Input 1,751,219→161,861，下降 **90.76%**；37 calls→9 calls。
- N13 Input 708,000→225,061，下降 **68.21%**；N13 Input 占比 18.77%→14.22%。
- N9 core+escalation+repair Input 370,778→357,415，仅下降 3.60%；N9 字典收益被 escalation 14 calls、3 次 repair 部分抵消。
- Field core+recall Input 341,039→289,511，下降 15.11%。
- Grounder Output 仍占总 Output 27.90%，是最大 output/thinking 消耗；这是模型生成长度问题，不是 bulk reducer 的主要瓶颈。

Async executor 在跨文档阶段记录 374 次调用，M2/M3 实际最大同时活跃均为 24，failed call 为 3；证明并发已真正发生，并非只增加 worker 配置。当前 telemetry 的 `queue_wait_ms=3` 是汇总字段，不足以证明完整 p95；节点 model_calls 的 queue_wait p50/p95 均为 0，说明 provider lane 没有形成显著排队。

## 4. 质量评估

### 4.1 Mention

| 指标 | 上一轮 R2 | 本轮 R3 | 变化 | 目标 | 结论 |
| --- | ---: | ---: | ---: | ---: | --- |
| Precision | 72.43% | **73.71%** | +1.28pp | >90% | 未达标 |
| Recall | **73.51%** | 69.03% | **-4.48pp** | >85% | 未达标且回归 |
| F1 | **72.96%** | 71.29% | -1.67pp | — | 回归 |
| strict TP | 197 | 185 | -12 | — | 回归 |
| partial | 54 | 41 | -13 | — | 数量下降但未转化为 TP |
| FP | 21 | 25 | +4 | — | 回归 |
| FN | 71 | 83 | +12 | — | 主要问题 |

本轮输出 251 Mention 对 268 Gold，较上一轮少 21 个 Mention。由于单文档 Prompt 与业务逻辑并未由本次 Stage Graph 改动，下降不能直接归因于跨文档并发；但真实生产 A/B 仍必须把模型波动造成的损失计入结果，不能因“Prompt 未改”而忽略。Grounder 6 次 invalid JSON、missing recovery 3/6 再次失败、Judge 2 次 invalid JSON 与 FN 增长方向一致，是本轮首要单文档质量风险。

### 4.2 Evidence

| 指标 | 上一轮 R2 | 本轮 R3 | 门槛 | 结论 |
| --- | ---: | ---: | ---: | --- |
| Evidence records | 293 | 275 | — | 随 Mention 减少 |
| VERIFIED | 292 | 275 | — | 全部通过 |
| exact/source-equivalent | 99.66% | **100.00%** | ≥99.5% | 通过 |
| semantic Evidence repair LLM | 0 | 0 | 0 | 通过 |
| Evidence 异常导致文档失败 | 0 | 0 | 0 | 通过 |

### 4.3 Field

| Field | 上一轮 R2 | 本轮 R3 | 变化 | 门槛 | 结论 |
| --- | ---: | ---: | ---: | ---: | --- |
| predicate | 80.60% | 75.37% | -5.22pp | ≥90% | 未达标 |
| participant | 82.46% | 77.24% | -5.22pp | ≥96% | 未达标 |
| metric | 78.35% | 71.94% | -6.41pp | ≥90% | 未达标 |
| fiscal period | 90.79% | 85.19% | -5.59pp | ≥80% | 通过但下降 |
| total | 81.64% | 76.14% | **-5.50pp** | ≥91% | 未达标 |

Field runtime link 为 1,102 条：646 internal coreference、255 external linking、201 unresolved canonicalized。上一轮为 1,159 条（724/243/192）。本轮 external linking 增加 12、UNRESOLVED 增加 9，不能用扩大 UNRESOLVED 解释下降；主要变化来自上游 Mention 数减少和 Field 内容本身错误。并行事务冲突已修复，436 个 Field task 全部成功，因此这不是执行失败导致的数据缺失。

### 4.4 N7/N9 与 Atomic

| 指标 | 上一轮 R2 | 本轮 R3 | 变化 | 目标 | 结论 |
| --- | ---: | ---: | ---: | ---: | --- |
| N9 task | 272 | 251 | -21 | — | 随 Mention 减少 |
| candidate coverage | 99.63% | **100.00%** | +0.37pp | 100% | 通过 |
| judgeable SAME opportunities | 113 | 66 | -47 | — | 样本结构变化大 |
| MERGE Precision | **100.00%** | 92.00% | -8.00pp | >75% | 达目标但回归 |
| conditional MERGE Recall | **82.30%** | 69.70% | **-12.60pp** | >85% | 未达标 |
| CREATE_NEW accuracy | 88.83% | 90.05% | +1.22pp | — | 改善 |
| final Atomic count | 178 | 199 | +21 | — | 过拆信号 |
| largest Atomic cluster | 16 | 7 | -9 | — | 无 supercluster，但拆分加重 |

50 次 MERGE 中独立复核确认 46 次正确、4 次错误；66 个存在 SAME opportunity 的 task 只合并 46 个，漏合 20 个。全局冻结 snapshot 与保守 reducer 成功消除了 candidate coverage 缺口和超大簇，但把主要风险从误并转为过拆。72 个 final hard-conflict pair violations 来自少数错误簇的组合放大，不能与“4 个 assignment-time 错误 MERGE”直接等同；仍说明错误 merge 一旦进入 cluster 会产生明显污染半径。

### 4.5 Package / N12 / N13

Package 评估只覆盖本轮 96/199 个高置信跨 run Atomic 对齐（48.24%）；上一轮发布报告覆盖 83/178（46.63%）。必须同时报告发布口径和同一 96-Atomic 重投影口径。

| 指标 | 上一轮 R2 发布值（83） | 上一轮重投影（本轮 96） | 本轮 R3（96） | 相对同口径变化 | 目标 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Pair Precision | 82.57% | 90.68% | **98.94%** | +8.25pp | >90% |
| Pair Recall | 32.95% | **76.64%** | 65.18% | **-11.46pp** | >80% |
| Pair F1 | 47.10% | **83.07%** | 78.59% | -4.49pp | — |
| TP pairs | 199 | 876 | 745 | -131 | — |
| FP pairs | 42 | 90 | 8 | -82 | — |
| FN pairs | 405 | 267 | 398 | +131 | — |

本轮 Package Precision 超过目标且错误合并非常少；Recall 未达标，且相对同口径上一轮下降。N13 在 444 个 pair decision 中产生 61 SAME、374 DIFFERENT、9 UNCERTAIN，最终只 Apply 1 个 merge plan，另有 46 个弱边界 SAME 未 Apply。该边界保护是高 Precision 的重要来源，也是 Recall 长尾的直接来源之一。

| 碎片化指标 | 上一轮重投影（96） | 本轮 R3（96） | 变化 |
| --- | ---: | ---: | ---: |
| multi-event Gold groups | 9 | 9 | 持平 |
| fragmented Gold groups | 3 | 8 | +5 |
| excess components | 5 | 16 | +11 |
| singleton components in multi-event Gold | 4 | 20 | +16 |
| missed pair links | 267 | 398 | +131 |

主要 bad case：

- `G_MICRON_FQ3_2026_EARNINGS`：48 个对齐 Atomic，从上一轮重投影的 `42+3+3` 三组件变为 `39+2+2+2+1+1+1` 七组件，missed links 261→384。
- `G_QUALCOMM_DATA_CENTER_STRATEGY`：4 个 Atomic 从 `2+1+1` 变为四个 singleton，missed links 5→6。
- 其余新增碎片集中在小型 2-3 Atomic Gold group；这说明 Wave B 不只在 giant component 上过保守。

需要注意：`recorded_original_n12` 显示 0 recall，是 evaluator 不认识 Stage Graph Wave B 的 provisional package IDs，不能据此判定 N12 模型完全失效。最终 Package 指标和落库 assignment 才是有效业务结果。这个 evaluator 兼容缺口应后续修正，但不影响 current actual partition 的 P/R 与碎片化计算。

## 5. 保留、修正与回滚必要性判断

| 修改项 | 真实收益/风险 | 判断 |
| --- | --- | --- |
| one-shot Stage Graph 与固定 barrier | 墙钟 -77.79%，30/30 技术成功 | **保留** |
| native async + 32/48/48/16 lane | M2/M3 跨文档最大活跃均达 24，无 provider 压力失败 | **保留** |
| Registry 短事务单写门 | 修复 R2 29 OperationalError + 4 IntegrityError；R3 Field 436/436 | **必须保留** |
| artifact/task ledger/finalized 幂等 | 复跑 calls/Mention/Atomic/Package 增量全 0 | **保留** |
| Field 全局语义 key 去重 | Field Input -15.11%，执行无缺失 | 保留编排；质量下降来自上游/语义结果，另行优化 |
| provisional Atomic 全局 snapshot | coverage 100%、无大簇；但 N9 Recall -12.60pp、Atomic +21 | **需修正，不整体回滚**：放宽安全 SAME 收敛/late convergence |
| N12 字典与 9-call Wave A/B | Input -90.76%，Package Precision 98.94%；Recall 65.18%、碎片加重 | **保留字典降本，修正 Wave B reducer/召回** |
| N13 full bounded pair plan | Input -68.21%、Precision 高；444 tasks、只 Apply 1 merge、46 weak SAME 未 Apply | 保留 coverage；优化 high-yield candidate 与受限 safe apply |
| 非法 item 局部隔离 | 7 个 N12 + 14 个模型格式失败未扩大为文档失败 | **保留** |
| 整套 V3 一键回滚 | 会丢失数量级效能收益，且不能直接解决单文档模型波动 | **不建议** |

下一轮最小修正优先级：

1. N9：在不降低 hard boundary 的前提下，为同冻结 snapshot 中的高置信 SAME 设计一次 bounded late convergence；只处理 singleton/已验证 SAME，不做 cluster↔cluster 传递闭包。
2. N12：补全 Wave B provisional ID 的 evaluator 映射；对同 canonical anchor、同 family/period 且无 NOT_RELATED 的碎片增加一次受限 reducer 收敛，重点验证 Micron earnings 与 Qualcomm strategy，不针对具体 ID 写规则。
3. N13：分析 46 个 weak SAME 未 Apply 的 Gold 命中率，只有在 precision 下界足够高时窄放宽；不能用全量 SAME 自动 Apply 换 Recall。
4. Mention：优先排查 6 次 Grounder invalid JSON、3 次 missing recovery 再失败和 2 次 Judge invalid JSON。它们与本轮 FN +12 同方向，在下一次跨文档复验前先做单文档 fixed-input 重放。
5. 审计：N12 `UNJUDGEABLE_FAILED` 增加短错误分类/字段路径，仅写编排审计，不扩大 LLM payload。

## 6. Prompt 与业务逻辑对齐复核

本次没有改变 Mention、Field、Atomic、Package 的领域定义，也没有重写 reason 协议。新增 Prompt 只说明批量任务独立、ID 必须恰好返回一次，以及 N12/N13 读取 immutable snapshot；这些语句与强制 dictionary DTO、冻结快照和 task-local repair 一致。

模型从 Prompt + Schema 可以知道：每个 task 独立判断、不得省略或合并 task、N12 不得假设前序 task 已改变 Package、N13 pair 不得依赖同批其他 pair。没有把 artifact/task ledger 等编排审计字段加入模型 payload。R3 仍出现 invalid JSON，但它们是模型输出合规问题，不是新业务字段没有 Prompt 定义；现有 item-level repair/隔离避免了阻塞。

## 7. 验证与可复核产物

代码验证：

- `uv run pytest tests/cdecr/test_bulk_epoch_v3.py -q`：10 passed。
- `uv run ruff check src/cdecr tests/cdecr`：通过。
- `uv run mypy src/cdecr`：50 source files 通过。
- 最终全量 `uv run pytest tests/cdecr -q`：288 passed、3 skipped（5:27）。
- N9 独立评估产物已用 Python 标准 JSON 解析器验证有效。

真实验收产物：

- Registry：`D:\DoxAgent_CDECR_Acceptance_20260803_StageGraph_V3_R3\cdecr_30_stage_graph_v3.sqlite3`
- workflow report：`D:\DoxAgent_CDECR_Acceptance_20260803_StageGraph_V3_R3\cdecr_30_stage_graph_v3_report.json`
- diagnostics：`D:\DoxAgent_CDECR_Acceptance_20260803_StageGraph_V3_R3\diagnostics.json`
- Mention Gold：`D:\DoxAgent_CDECR_Acceptance_20260803_StageGraph_V3_R3\mention_gold_eval.json`
- Field Gold：`D:\DoxAgent_CDECR_Acceptance_20260803_StageGraph_V3_R3\field_gold_eval.json`
- N9 review：`D:\DoxAgent_CDECR_Acceptance_20260803_StageGraph_V3_R3\n9_gold_eval.json`
- Package Gold：`D:\DoxAgent_CDECR_Acceptance_20260803_StageGraph_V3_R3\package_gold_eval.json`

限制：Package 指标只覆盖 96/199 个高置信 Atomic 对齐，不能外推为全部 Atomic 的人工 Gold；N9 是 assignment-time 独立 M4 review，不等同于最终全局 Atomic membership Gold；模型 A/B 存在随机波动。因此本报告对效能收益有强因果证据，对质量变化给出真实观测和结构相关性判断，但不把所有质量变化都归因于 Stage Graph。
