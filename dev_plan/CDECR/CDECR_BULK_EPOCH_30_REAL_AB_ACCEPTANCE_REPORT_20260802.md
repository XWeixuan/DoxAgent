# CDECR Bulk Epoch 30 篇真实 A/B 验收报告

> 日期：2026-08-02
> 正式运行 commit：`395526b`
> 语料：固定 30 篇 `grounder_quality_30_manifest.json`，与上一轮逐项同源、同顺序、同 fingerprint
> Gold：复用 268 条 Mention Gold、2026-07-28 Package Gold；没有重新标注
> 模型：正式 workflow 沿用 DeepSeek V4 Flash thinking；质量复核将本轮与上一轮统一改由 DeepSeek V4 Flash M4 重评
> 本轮正式 Registry：`D:\DoxAgent_CDECR_Acceptance_20260802_Bulk_R2\cdecr_30_bulk_epoch_r2.sqlite3`

## 0. 结论先行

本轮不是整体通过，也不应整体回滚。

1. **运行可靠性通过**：30/30 单文档成功、30/30 跨文档成功、独立 N13 finalize 成功、stderr 为空；上一轮跨文档仅 28/30。幂等复跑的模型调用、Mention、Atomic、Package 增量均为 0。
2. **墙钟有实质改善但未达到方案观察目标**：总墙钟从 4:21:57 降至 3:24:35，下降 21.90%；低于方案期望的 30%–50%。固定语料形成 11 个组件，但分布为 `20 + 1×10`；10 个 singleton 早期并行完成后，剩余 20 篇因真实 N9 candidate 竞争而稳定串行。
3. **总 Token 略降但 Input 上升**：Input 3,495,773→3,772,039（+7.90%）；Output 3,066,910→2,661,038（-13.23%）；总 Token 6,562,683→6,433,077（-1.97%）。N9 降本明显，但完整 N13 coverage 和 N12 碎片化把 Input 收益吃掉。
4. **Mention 与 Field 小幅改善，但仍不达目标**：Mention P/R 由 70.44%/72.01% 提升到 72.43%/73.51%；Field total 由 77.53% 提升到 81.64%。二者均未达到既定门槛。
5. **N9 更准但更保守**：MERGE Precision 96.55%→100.00%，conditional MERGE Recall 85.71%→82.30%；没有评估到的错误 MERGE，但 CREATE_NEW 过多。
6. **Package 出现不可接受回归**：同一 83-Atomic 高置信 Gold 子集上，Pair P/R 从 87.99%/78.81% 降至 82.57%/32.95%；fragmented Gold groups 2→5，excess components 3→16，missed links 128→405。Micron FQ3 earnings 父组从 3 个组件裂成 13 个。
7. **回滚判断**：P0 epoch/checkpoint、全 touched N13、scheduler 8/24/24/12、provider start gate 和 Atomic component 可保留；**Package/N12 的非全局稳定 Apply 必须判定为强回滚候选**。本轮按要求只评估，没有执行回滚。

## 1. 代码与执行范围

### 1.1 已落地能力

- 新增持久化 `bulk_epochs` / `bulk_epoch_items`，记录 manifest、stage、component、snapshot、结果与失败范围。
- evaluation 首轮与幂等复跑均走真实 `BULK_EPOCH`，不再用逐文档 legacy loop 假装 bulk。
- 固定 manifest 与 fingerprint，稳定 epoch ID，已 finalized epoch 不覆盖旧结果、不重复 N13。
- 依赖组件间并行、组件内稳定顺序；局部失败不扩大为整批失败。
- N13 从所有成功文档累积完整 touched Package，并在独立 synthetic run 中 finalize，不依赖最后一篇文档。
- document/M1/M2/M3/M4 并发为 `8/8/24/24/12`；N9/N12/N13 worker 为 `12/10/12`；structured request start gate 为约 1 秒。
- provider 429/timeout/reset 只压低对应 lane，并在稳定成功后渐进恢复。
- Prompt、业务 Wire DTO、reason 协议、复杂 LLM batch 大小和 N12 shadow 配置均未改变。

### 1.2 实现期纠错与无效运行隔离

首次真实 partial run 在单文档 30/30 后形成 `29 + 1` 两个组件。根因是 scheduler graph 错把同 principal/family 和泛 `source_claim` 当成依赖，违反“不能仅因同 ticker/公司形成巨型 component”的方案约束。该运行在仅完成 2 个跨文档 run 时被主动终止，Registry 完整保留为诊断证据，但**未进入任何正式质量、耗时或 Token 指标**。

修正后：

- 删除 generic source-claim、同公司/日期窗口等宽边；
- Atomic 图只使用 exact identity 与 candidate-like metric/period/lexical 边；
- Package anchor/window 不再反向阻塞 N9，而只在 N12 writer 前获取排序锁；
- 正式 R2 实际形成 11 个组件，分布为 `20 + 1×10`。

### 1.3 离线验证

- Ruff：通过。
- mypy：通过。
- bulk 定向回归：5/5 通过。
- 全量 CDECR：281 passed、3 skipped、1 failed。
- 唯一失败是本轮开始前已存在的配置/测试矛盾：runtime 默认 `atomic_hard_cannot_link_mode=enforce`，旧测试仍断言 `shadow`；未为绿测擅自回滚业务配置。
- 首次全量 pytest 因 C 盘仅余约 0.61 GB 触发 SQLite `database or disk is full`；改用 D 盘临时目录后连锁失败全部消失。该环境故障未计入业务回归。

## 2. 正式运行成功率与失败排查

| 指标 | 上一轮 | 本轮 | 变化 |
| --- | ---: | ---: | ---: |
| 单文档成功 | 30/30 | 30/30 | 持平 |
| 跨文档成功 | 28/30 | 30/30 | +6.67pp |
| 正式文档失败 | 0 | 0 | 持平 |
| 跨文档失败 | 2 | 0 | 修复 |
| 独立 N13 finalize | 不完整覆盖 | 成功覆盖全 touched set | 修复 |
| stderr | — | 0 bytes | 通过 |
| 幂等模型调用增量 | 0 | 0 | 持平 |
| 幂等 Mention/Atomic/Package 增量 | 0/0/0 | 0/0/0 | 持平 |

正式 R2 没有 document/case 失败。模型层发生 7 次局部非法 JSON：Grounder 4、N9 2、Judge 1；均由 item/task 级 repair 或保守降级吸收，没有扩大为文档失败。另有 1 条 Evidence `TEXT_NOT_FOUND`，同样未造成文档失败。

## 3. 墙钟、Token 与节点占比

### 3.1 总量 A/B

| 指标 | 上一轮 | 本轮 | 环比 |
| --- | ---: | ---: | ---: |
| 总墙钟 | 15,717,505 ms（4:21:57） | 12,275,833 ms（3:24:35） | **-21.90%** |
| 首轮墙钟 | 15,707,654 ms | 12,262,145 ms | -21.94% |
| 模型调用 | 973 | 992 | +1.95% |
| Input Token | 3,495,773 | 3,772,039 | +7.90% |
| Output Token | 3,066,910 | 2,661,038 | -13.23% |
| 总 Token | 6,562,683 | 6,433,077 | **-1.97%** |
| aggregate model latency | 23,539,029 ms | 22,315,413 ms | -5.20% |

本轮 output 包括 DeepSeek thinking token。aggregate model latency 是各并发请求 latency 的求和，不等于墙钟；节点“运行时长占比”只能解释模型资源消耗，不能相加为串行时间。

### 3.2 真实墙钟分解

| 编排阶段 | 本轮墙钟 | 占首轮墙钟 |
| --- | ---: | ---: |
| 单文档 30 篇并发 barrier | 1,108,289 ms（18:28） | 9.04% |
| bulk components（N5.5-N12） | 10,025,423 ms（2:47:05） | 81.76% |
| N13 全 touched finalize | 1,127,078 ms（18:47） | 9.19% |
| component planning | 313 ms | <0.01% |

组件调度收益主要发生在开始阶段的 10 个 singleton；之后 20 篇真实依赖组件成为长尾。该语料是单主体、同日、同指标事实密集集，不能代表数百篇多 issuer 历史新闻的最大吞吐，但足以暴露 giant component 的真实退化边界。

### 3.3 本轮逐节点 Token 与 aggregate latency 占比

| 节点 | calls | Input | Input占比 | Output | Output占比 | aggregate latency占比 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| N12 package_assignment | 37 | 1,751,219 | **46.43%** | 797,614 | 29.97% | **28.54%** |
| N13 package_merge | 33 | 708,000 | **18.77%** | 229,253 | 8.62% | 8.66% |
| N5.5 field_coreference | 308 | 332,779 | 8.82% | 108,146 | 4.06% | 4.92% |
| N9 atomic_coreference | 79 | 331,184 | 8.78% | 465,011 | 17.47% | 16.50% |
| Judge | 30 | 214,326 | 5.68% | 186,321 | 7.00% | 8.07% |
| Grounder | 30 | 142,265 | 3.77% | 593,861 | 22.32% | 21.90% |
| N9 escalation | 7 | 31,706 | 0.84% | 143,382 | 5.39% | 5.35% |
| Grounder item repair | 11 | 48,530 | 1.29% | 40,567 | 1.52% | 1.72% |
| Grounder missing recovery | 4 | 17,831 | 0.47% | 48,860 | 1.84% | 1.90% |
| Dreamer | 30 | 38,092 | 1.01% | 38,435 | 1.44% | 1.12% |
| 其余 embedding/repair | 423 | 156,107 | 4.14% | 9,588 | 0.36% | 1.32% |

### 3.4 关键节点环比归因

| 节点 | 上一轮 Input | 本轮 Input | 环比 | 判断 |
| --- | ---: | ---: | ---: | --- |
| N9 coreference | 802,951 | 331,184 | **-58.76%** | dictionary/deterministic filter 降本有效 |
| N12 assignment | 1,624,477 | 1,751,219 | +7.80% | 仍是最大输入瓶颈，且质量严重回归 |
| N13 merge | 95,270 | 708,000 | **+643.15%** | 上轮仅 4 call 且 coverage 不完整；本轮 33 call 覆盖全 touched set |
| Field | 353,447 | 332,779 | -5.85% | 小幅下降，但 308 calls 仍形成密集组件长尾 |
| Grounder | 151,573 | 142,265 | -6.14% | Input 略降，output/thinking 仍大 |

N13 的 Token 上升不能简单视为优化失败：上一轮低 Token 来自 coverage 缺口，本轮修复了正确性。但在 83-event Gold 子集上，N13 只让 N12 recorded partition 的 TP pair 从 195 增到 199、FP 保持 42，即 708,000 Input Token 对应 4 个可观察新增正确 pair，约 **177,000 Input Token / 新增正确 pair**。这说明全量 finalize 应保留，但 candidate wave/filter 必须继续优化。

## 4. 质量评估

质量复核调用不计入上面的正式 workflow Token。上一轮原报告使用 Qwen M4，但本轮评估时 DashScope 账户返回明确 `provider_arrearage`。为避免跨 judge 比较，本报告修复了 evaluator 的 provider factory，并用同一 DeepSeek V4 Flash M4 对上一轮与本轮重新评分；Prompt、Schema、Gold 和评分公式未改。

### 4.1 Mention

| 指标 | 上一轮同口径 | 本轮 | 环比 | 目标 | 结论 |
| --- | ---: | ---: | ---: | ---: | --- |
| Precision | 70.44% | **72.43%** | +1.99pp | >90% | 未达标 |
| Recall | 72.01% | **73.51%** | +1.49pp | >85% | 未达标 |
| F1 | 71.22% | **72.96%** | +1.75pp | — | 小幅改善 |
| strict TP | 193 | 197 | +4 | — | 改善 |
| partial | 53 | 54 | +1 | — | 基本持平 |
| FP | 28 | 21 | -7 | — | 改善 |
| FN | 75 | 71 | -4 | — | 改善 |

本轮 272 个输出 Mention 对 268 Gold。改善主要来自 FP 减少，missing/partial 仍是主要瓶颈；bulk 编排没有改变 Prompt，因此该小幅变化包含真实模型波动，不能全部归因于编排。

### 4.2 Evidence

| 指标 | 上一轮 | 本轮 | 门槛 | 结论 |
| --- | ---: | ---: | ---: | --- |
| Evidence records | 300 | 293 | — | 随 Mention 数变化 |
| VERIFIED | 300 | 292 | — | 1 条异常 |
| exact/source-equivalent | 100.00% | **99.66%** | ≥99.5% | 通过 |
| semantic Evidence repair LLM | 0 | 0 | 0 | 通过 |
| Evidence 异常导致文档失败 | 0 | 0 | 0 | 通过 |

唯一异常：`bf5258ec-37f8-408b-ace2-8b016d16f725` 中 “The AI memory shortage could last beyond 2028.” 的一条 Evidence 被标记 `TEXT_NOT_FOUND`；该异常被局部保留，没有扩大为 Mention/文档失败。

### 4.3 Field

| Field | 上一轮同口径 | 本轮 | 环比 | 门槛 | 结论 |
| --- | ---: | ---: | ---: | ---: | --- |
| predicate | 75.75% | **80.60%** | +4.85pp | ≥90% | 未达标 |
| participant | 78.36% | **82.46%** | +4.10pp | ≥96% | 未达标 |
| metric | 72.54% | **78.35%** | +5.81pp | ≥90% | 未达标 |
| fiscal period | **94.44%** | 90.79% | -3.65pp | ≥80% | 通过但下降 |
| total | 77.53% | **81.64%** | +4.11pp | ≥91% | 未达标 |

本轮 runtime 产生 1,159 条 Field links：724 internal coreference、243 external linking、192 unresolved canonicalized；上一轮为 1,184（727/256/201）。不能把较少 UNRESOLVED 当成达标依据，本轮 total 仍显著低于 91%。

### 4.4 N7/N9 与 Atomic

| 指标 | 上一轮同口径 | 本轮 | 环比 | 目标 | 结论 |
| --- | ---: | ---: | ---: | ---: | --- |
| N9 task | 256 | 272 | +16 | — | Mention 数/成功文档变化 |
| candidate coverage | 98.83% | **99.63%** | +0.80pp | 100% | 接近但未满 |
| judgeable SAME opportunities | 98 | 113 | +15 | — | coverage 增加 |
| MERGE Precision | 96.55% | **100.00%** | +3.45pp | >75% | 通过 |
| conditional MERGE Recall | **85.71%** | 82.30% | -3.41pp | >85% | 未达标 |
| CREATE_NEW accuracy | **91.72%** | 88.83% | -2.89pp | — | 下降 |
| final Atomic count | 168 | 178 | +10 | — | 过拆信号 |
| largest Atomic cluster | 18 | 16 | -2 | — | 无 supercluster 放大 |

本轮 93 个 MERGE 全部被独立 M4 判为正确，说明 precision 边界有效；但 113 个有 SAME opportunity 的任务中漏合 20 个，过度 CREATE_NEW 仍然明显。由于没有覆盖本轮随机 Atomic ID 的全量人工 membership Gold，本报告不伪造最终 Atomic 全局 Pair P/R；以 N9 assignment-time pair review 作为可复现节点指标。

### 4.5 Package / N12 / N13

高置信 Atomic 对齐覆盖 83/178（46.63%），因此下表代表可迁移 Gold 子集，不冒充全量 178 Atomic。

| 指标 | 上一轮 actual Package | 本轮 actual Package | 环比 | 目标 | 结论 |
| --- | ---: | ---: | ---: | ---: | --- |
| Pair Precision | **87.99%** | 82.57% | -5.41pp | >90% | 未达标且下降 |
| Pair Recall | **78.81%** | 32.95% | **-45.86pp** | >80% | 严重回归 |
| Pair F1 | **83.14%** | 47.10% | -36.04pp | — | 严重回归 |
| fragmented Gold groups | 2 | 5 | +3 | 越低越好 | 回归 |
| excess components | 3 | 16 | +13 | 越低越好 | 回归 |
| singleton components in multi-event Gold | 3 | 18 | +15 | 越低越好 | 回归 |
| missed pair links | 128 | 405 | +277 | 越低越好 | 回归 |

最主要 bad case 是 `G_MICRON_FQ3_2026_EARNINGS`：35 个对齐 Atomic 在上一轮被分为 3 个 Package，本轮分为 13 个，单组贡献 401 个 missed pair links。另新增/复现了 memory supplier reallocation、两类 package boundary/singleton group、Qualcomm strategy 等小组碎片。

N12 recorded partition 本身 P/R 为 82.28%/32.28%，N13 后为 82.57%/32.95%；N13 只恢复 4 个 TP pair，没有增加 FP pair。根因首先在 N12 候选/Apply 时序，而不是 N13 把正确 Package 再拆开。

## 5. 根因判断与回滚必要性

### 5.1 为什么效率提升但 Package 质量下降

本轮没有修改 Mention、Field、N9、N12 或 N13 的 Prompt。Mention 与 Field 的小幅波动因此不能直接归因于编排重构。Package 的大幅回归则具有明确的结构性相关性：

1. N9 在组件内获得稳定 Atomic 视图，MERGE Precision 提升至 100%，说明 Atomic 组件并行没有引入明显误合并。
2. N12 在多个组件推进时读取并 Apply 当时可见的 Package 状态；同一父事件的 Atomic 可能在不同时间看到不同候选集合，从而各自 `CREATE_NEW`。
3. 当前按 anchor/window 设置的局部 writer lock 只能防止完全相同锁键同时写入，无法保证同一语义父事件获得统一、稳定的候选视图。
4. N13 已覆盖全部 touched Package，但其保守 SAME Apply 边界只实际执行 3 个 merge plan；37 个弱边界 SAME 未 Apply，无法承担修复 N12 大规模碎片的职责。

因此，根因不是并发数 `8/24/24/12` 本身，也不是 provider 承压失败，而是 **N12 的全局 Package 状态依赖被错误地按局部组件切开**。继续增加局部锁或让 N13 激进兜底都会增加复杂度及误合并风险，不是合适修复方向。

### 5.2 失败与异常范围

- 正式 R2：文档阶段 30/30、跨文档阶段 30/30 成功，没有整篇或整 case 失败。
- 局部格式异常：Grounder 4 次、Atomic coreference 2 次、Judge 1 次非法 JSON，均由既有局部 repair/fallback 吸收，未扩大失败范围。
- Evidence：1 条 `TEXT_NOT_FOUND`，仅影响该 Evidence 的 VERIFIED 状态。
- provider：正式运行没有发现并发限流、超时风暴或 provider 错误。
- 预运行曾发现 component graph 退化为 `29+1`；原因是把宽泛 principal/family/source-claim 当作依赖边。该次只完成 2 个跨文档任务，已停止、隔离且未进入任何指标；修正后正式 R2 为 `20 + 10×1`。

### 5.3 回滚必要性矩阵

| 修改项 | 效果证据 | 回滚必要性 | 建议 |
| --- | --- | --- | --- |
| epoch manifest、checkpoint、失败隔离、幂等复用 | 30/30 成功；重跑增量 calls/tokens/产物均为 0 | 不回滚 | 保留 |
| M1/M2/M3/M4 = 8/24/24/12 与 1 秒启动门 | 无 provider 压力错误；总墙钟 -21.90% | 不回滚 | 保留，并继续由 tier lane 动态退避 |
| Atomic dependency component 并行 | N9 MERGE P=100%；Input -58.76% | 暂不回滚 | 保留；针对漏合做窄范围 late-edge convergence，不扩大 hard join |
| N12 按组件交错读取/Apply Package | Package R -45.86pp；碎片 excess +13 | **必须回滚/替换** | 恢复全局稳定 Package 快照与单一有序 Apply；N9 仍可并行 |
| N12 局部 anchor/window writer locks | 未阻止同父事件被拆成 13 个 Package | 应移除或降为实现细节 | 不再把它当正确性边界 |
| N13 全 touched finalize | 修复上一轮 coverage 缺口；无新增 FP | 不回滚 | 保留完整 coverage |
| N13 当前请求波次 | Input +643.15%，约 177k Input/新增正确 pair | 需要优化但不回滚 coverage | 去重 profile/candidate、缩短字典、按高收益边界分波 |
| evaluator 使用正式 provider factory | 同模型重评使 A/B 可比 | 不回滚 | 保留 |

这里的“必须回滚/替换”是验收结论，不代表本轮已经执行代码回滚；依照任务要求，本轮只评估。

## 6. 总结性判断

本轮重构在工程可靠性和吞吐上取得了真实收益：固定 30 篇全部成功，墙钟由 4:21:57 降至 3:24:35，总 Token 下降 1.97%，N9 Input 下降 58.76%，并发配置也没有触发 provider 压力问题。Mention、Field 与 N9 precision 均改善，但 Mention/Field 仍未达到业务门槛，N9 conditional recall 还差 2.70pp。

然而，Package 是 CDECR 最终交付层级，当前 Pair Recall 只有 32.95%，且碎片化显著恶化。故本轮不能整体判定为可生效：**应保留 epoch、幂等、并发 lane、N9 组件并行及完整 N13 finalize；应在下一轮优先撤销 N12 的组件间交错 Apply，改成“并行准备、全局稳定候选视图、单一有序 Apply”后再做 30 篇复验。** 在该修正通过前，不建议把 BULK_EPOCH 作为批量历史新闻的默认生产路径。

## 7. 可复核产物

- 正式 registry：`D:\DoxAgent_CDECR_Acceptance_20260802_Bulk_R2\cdecr_30_bulk_epoch_r2.sqlite3`
- workflow 汇总：`D:\DoxAgent_CDECR_Acceptance_20260802_Bulk_R2\cdecr_30_bulk_epoch_r2_report.json`
- 运行诊断：`D:\DoxAgent_CDECR_Acceptance_20260802_Bulk_R2\diagnostics.json`
- Package Gold 评估：`D:\DoxAgent_CDECR_Acceptance_20260802_Bulk_R2\package_gold_eval.json`
- Mention/N9/Field 同口径评估：同目录 DeepSeek evaluator 输出文件
- 未纳入指标的中止预运行：`D:\DoxAgent_CDECR_Acceptance_20260802_Bulk`

限制说明：Package 指标来自 83/178 个高置信对齐 Atomic；N9 指标来自 assignment-time Gold review。报告明确保留该覆盖边界，没有将其外推为全量人工 Gold。
