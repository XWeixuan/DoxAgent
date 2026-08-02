# CDECR 30 篇真实验收：Post-Optimization 对比报告（2026-07-31）

## 1. 结论

本轮不能判定为通过。

- 单文档阶段 30/30 成功，较上一轮 29/30 改善为 100%。
- 跨文档阶段 29/30 成功，最后一篇在 Package reaction repair 写入 `WIRE_PAYLOAD_SHADOW` 审计时发生 `ImmutableRecordConflict`；端到端成功率仍为 96.67%。
- Mention 严格 Recall 从 57.09% 升至 65.67%，但严格 Precision 从 76.88% 降至 69.29%。召回有所恢复，但代价是更多碎片、残缺字段和非 Gold 输出，未达到 Precision>90%、Recall>85%，也违反“不能为提召回显著牺牲准确率”的约束。
- Atomic/Identity 的高置信 Gold 投影 Pair Precision 约 71.37%，较 52.58% 明显改善；但 18-member revenue、12-member EPS、12-member guidance 和 12-member SCA 簇仍有污染。N9 MERGE Precision 约 74.07%，接近但未达到 75% 门槛。
- Package Pair Recall 通过 anchor 聚合显著恢复，但产生一个 51-Atomic 的 Micron earnings Package；高置信投影 Precision 约 85.8%，仍低于 90%。本轮是“用更强聚合换回 Recall”，尚未达到平衡最优。
- N13 输入 Token 从 563,764 降至 87,131，下降 84.55%，目标达成；但 N13 本轮 43 个 pair 全部判 `DIFFERENT_PACKAGE`，没有产生一个正确新增 join，因此 87,131 Token/0 join，单位有效 join 成本不可定义。
- 总输入 Token 反而从 2,610,616 增至 3,276,708（+25.51%）。主要原因不是 N13，而是 N12/`package_assignment` 输入从 821,126 增至 1,607,339（+95.75%），占本轮输入 49.05%。
- 总墙钟时间从 6,035.21 秒增至 6,589.26 秒（+9.18%）；没有实现整体提速。

因此，本轮优化的判断是：N13 降本、单文档韧性、N9 粒度降级修复有效；Mention 准确率、Package 超大包和 N12 成本回归不可接受，不应按整轮“通过”处理，也不应整体回滚。

## 2. 测试范围与可复现产物

- 代码基线：`fd41811 feat(cdecr): checkpoint acceptance remediation baseline` 之后的当前工作区优化实现。
- 测试集：与上一轮相同的固定 30 篇 manifest。
- 当前 Registry：`.tmp/cdecr/acceptance_20260731_postopt_r3/cdecr_30_postopt_r3.sqlite3`
- 当前机器报告：`.tmp/cdecr/acceptance_20260731_postopt_r3/cdecr_30_postopt_r3_report.json`
- 上一轮 Registry：`.tmp/cdecr/acceptance_20260730/cdecr_30_20260730.sqlite3`
- 上一轮机器报告：`.tmp/cdecr/acceptance_20260730/cdecr_30_20260730_report.json`
- 上一轮质量摘要：`dev_plan/CDECR/experiments/cdecr_30_real_acceptance_20260730_summary.json`
- Gold：复用 `.tmp/cdecr/resilience/mention_review_01_10.json`、`mention_review_11_20.json`、`mention_review_21_30.json`、`atomic_review_all.json`、`package_review_all.json`；没有重新标注 Gold，也没有调用 Codex 子 Agent。

质量指标口径：Mention 采用与上一轮一致的 source-centered、one-to-one 严格 Gold；PARTIAL 不计 TP。Atomic/Package 因当前 Mention 集和 Atomic 边界已经变化，采用主 Agent逐簇回放得到的“高置信 Gold 投影”；模糊边界不伪装成精确人工 Gold，因此相应指标标为 provisional。

## 3. 运行完整性与失败影响

| 指标 | 上一轮 | 本轮 | 变化 |
| --- | ---: | ---: | ---: |
| 单文档成功 | 29/30（96.67%） | 30/30（100%） | +3.33pp |
| 跨文档成功 | 29/29（100%） | 29/30（96.67%） | -3.33pp |
| 端到端成功 | 29/30（96.67%） | 29/30（96.67%） | 不变 |
| Mention | 199 | 254 | +55 |
| 报告口径 Atomic | 131 | 145 | +14 |
| Package | 61 | 68 | +7 |
| 模型调用 | 786 | 942 | +156（+19.85%） |
| 首轮墙钟 | 6,027.71s | 6,579.66s | +9.16% |
| 含幂等复验总墙钟 | 6,035.21s | 6,589.26s | +9.18% |

幂等复验是干净的：`rerun_model_call_delta=0`，Mention/Atomic/Package delta 均为 0，成功文档和成功事件均被复用。此前测试 harness 的执行模式错误没有在本轮复现。

唯一跨文档失败：

- 文档：D27，`doxatlas:raw_media:d9404d58-cd88-4ccc-a9d2-ec4004629dbc`。
- run：`99126f5d-4869-4267-9ead-47bb53697f20`。
- 路径：`process → _correct_packages_v13 → _repair_reaction_members_v13 → _joint_package_decisions → process_batch → record_wire_shadow → append_decision_audit`。
- 根因：同一 run/stage/operation/batch/attempt 的 `WIRE_PAYLOAD_SHADOW` audit ID 被第二次 reaction-member repair 复用，但 payload 已变化；immutable audit 检测到同 ID 异 payload，抛出 `ImmutableRecordConflict`。
- 这不是模型内容错误，也不是 provider 网络错误；是编排层审计 ID 缺少 invocation/payload instance 区分。

影响范围不是“整库都坏了”，但也不能忽略：

- D27 的 9 个 Mention、9 个 Atomic assignment 和 9 个 Package assignment 已经写入；失败发生在后置 Package 修复审计阶段。
- Registry 当前有 146 个 Atomic head，机器报告只计 145 个有效 Atomic，说明失败 run 存在部分持久化状态。
- 其他 29 个跨文档 run 不受该异常影响。
- 当前质量回放使用 Registry 最终可见状态，并明确包含 D27 的部分应用结果；它适合暴露质量问题，但不能当作“30/30 原子提交成功”的生产验收证明。

另外有 2 次 `field_coreference` 模型调用失败：1 次 `provider_arrearage`、1 次 `invalid_structured_output`。二者均由 item-local fallback 吸收，没有扩大为文档失败。

## 4. Token、调用与时长

### 4.1 总量对比

| 指标 | 上一轮 | 本轮 | 环比 |
| --- | ---: | ---: | ---: |
| Input Token | 2,610,616 | 3,276,708 | +25.51% |
| Output Token | 443,211 | 500,726 | +12.97% |
| Total Token | 3,053,827 | 3,777,434 | +23.69% |
| 模型调用 | 786 | 942 | +19.85% |
| 模型 aggregate latency | 6,890,079ms | 7,884,073ms | +14.43% |
| Provider queue wait | 26,981ms | 122,396ms | +353.64% |

`aggregate latency` 是所有调用延迟之和，存在并发，不能与墙钟直接相加；“时长占比”以下按 aggregate model latency 计算。

### 4.2 本轮逐节点占比

| 节点/stage | 调用 | Input | Input占比 | Output | Output占比 | aggregate latency | 时长占比 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| package_assignment（N12） | 36 | 1,607,339 | 49.05% | 113,538 | 22.67% | 2,018,055ms | 25.60% |
| atomic_coreference（N9） | 94 | 695,689 | 21.23% | 187,539 | 37.45% | 2,195,196ms | 27.84% |
| field_coreference | 313 | 310,532 | 9.48% | 5,094 | 1.02% | 333,722ms | 4.23% |
| judge | 30 | 158,513 | 4.84% | 20,175 | 4.03% | 308,202ms | 3.91% |
| grounder | 30 | 123,849 | 3.78% | 111,907 | 22.35% | 1,977,376ms | 25.08% |
| grounder_item_repair | 27 | 98,868 | 3.02% | 15,923 | 3.18% | 289,332ms | 3.67% |
| package_merge（N13） | 4 | 87,131 | 2.66% | 2,651 | 0.53% | 48,708ms | 0.62% |
| atomic_identity_embedding | 38 | 54,144 | 1.65% | 0 | 0 | 23,627ms | 0.30% |
| package_embedding_m1 | 33 | 47,012 | 1.43% | 0 | 0 | 18,740ms | 0.24% |
| dreamer | 30 | 32,239 | 0.98% | 35,116 | 7.01% | 411,416ms | 5.22% |
| grounder_missing_recovery | 5 | 17,132 | 0.52% | 3,437 | 0.69% | 62,193ms | 0.79% |
| atomic_coreference_escalation | 2 | 15,695 | 0.48% | 5,033 | 1.01% | 89,933ms | 1.14% |
| atomic_recall_m1 | 38 | 8,922 | 0.27% | 0 | 0 | 18,055ms | 0.23% |
| judge_coverage_recovery | 2 | 7,086 | 0.22% | 178 | 0.04% | 5,009ms | 0.06% |
| field_coreference_recall | 229 | 7,007 | 0.21% | 0 | 0 | 69,420ms | 0.88% |
| judge_item_repair | 1 | 4,509 | 0.14% | 135 | 0.03% | 3,229ms | 0.04% |
| title_embedding | 30 | 1,041 | 0.03% | 0 | 0 | 11,860ms | 0.15% |

### 4.3 关键节点环比与根因

| 节点 | 上一轮 Input | 本轮 Input | 变化 | 判断 |
| --- | ---: | ---: | ---: | --- |
| N13 package_merge | 563,764 | 87,131 | -84.55% | pair 去重/波次/禁用弱 SAME 有效 |
| N12 package_assignment | 821,126 | 1,607,339 | +95.75% | 新的首要成本回归 |
| N9 atomic_coreference | 504,437 | 695,689 | +37.91% | Mention +27.64%、任务数和更细 assessment 同时增加 |
| field_coreference | 188,444 | 310,532 | +64.79% | Mention/字段项增多，调用 208→313 |
| grounder_item_repair | 65,221 | 98,868 | +51.59% | repair 仍为 27 次，且单次输入包含业务错误信息 |

N12 单次平均输入从约 21,609 增至 44,648，最大单次 97,564；调用数反而从 38 降至 36。因此根因不是“调用更多”，而是 anchor 集合、成员/候选 Package profile 和增长中的大包被重复带入请求。51-Atomic 主包形成后，后续请求 payload 被明显放大。下一轮真正的效能重点应从 N13 转向 N12 profile 增量化/截断，而不是继续压 N13。

N13 本轮 43 个 pair evaluation 全部为 `DIFFERENT_PACKAGE`，4 次模型调用没有新增 join。按“每个正确新增 join 的 Token 成本”口径：正确新增 join=0，成本不可定义；只能报告浪费上界为 87,131 Input + 2,651 Output Token。

## 5. Mention / Grounder / Judge

### 5.1 严格 Gold 指标

| 指标 | 上一轮 | 本轮 | 环比 | 目标 | 结论 |
| --- | ---: | ---: | ---: | ---: | --- |
| Gold | 268 | 268 | 0 | — | 同一 Gold |
| 输出 Mention | 199 | 254 | +55 | — | 输出显著增加 |
| TP | 153 | 176 | +23 | — | 有真实召回收益 |
| FP/非严格输出 | 46 | 78 | +32 | — | 增幅大于 TP |
| FN | 115 | 92 | -23 | — | 遗漏减少 |
| Precision | 76.88% | 69.29% | -7.59pp | >90% | 失败 |
| Recall | 57.09% | 65.67% | +8.58pp | >85% | 失败 |
| F1 | 65.52% | 67.39% | +1.87pp | — | 小幅提升但不平衡 |

本轮不是“Mention 整体变差”：TP 和 Recall 确实上升；问题是每新增 23 个 TP，同时新增了 32 个非严格输出。主要代价来自把同一 Gold 拆成多个残缺 Mention、漏 qualifier/benchmark/period，以及把分析背景或细节单独提升成 Mention。

逐文档严格计数：

| 文档 | Gold | Output | TP | FP/非严格 | FN |
| --- | ---: | ---: | ---: | ---: | ---: |
| D01 | 11 | 9 | 9 | 0 | 2 |
| D02 | 9 | 7 | 4 | 3 | 5 |
| D03 | 5 | 5 | 5 | 0 | 0 |
| D04 | 4 | 5 | 2 | 3 | 2 |
| D05 | 6 | 6 | 4 | 2 | 2 |
| D06 | 11 | 8 | 7 | 1 | 4 |
| D07 | 13 | 10 | 9 | 1 | 4 |
| D08 | 11 | 11 | 7 | 4 | 4 |
| D09 | 10 | 12 | 2 | 10 | 8 |
| D10 | 12 | 7 | 7 | 0 | 5 |
| D11 | 18 | 18 | 10 | 8 | 8 |
| D12 | 10 | 9 | 5 | 4 | 5 |
| D13 | 5 | 8 | 5 | 3 | 0 |
| D14 | 16 | 14 | 14 | 0 | 2 |
| D15 | 12 | 10 | 10 | 0 | 2 |
| D16 | 19 | 19 | 12 | 7 | 7 |
| D17 | 10 | 13 | 8 | 5 | 2 |
| D18 | 2 | 1 | 1 | 0 | 1 |
| D19 | 7 | 7 | 7 | 0 | 0 |
| D20 | 1 | 2 | 0 | 2 | 1 |
| D21 | 4 | 2 | 1 | 1 | 3 |
| D22 | 7 | 5 | 1 | 4 | 6 |
| D23 | 7 | 10 | 4 | 6 | 3 |
| D24 | 5 | 3 | 3 | 0 | 2 |
| D25 | 5 | 6 | 4 | 2 | 1 |
| D26 | 9 | 10 | 8 | 2 | 1 |
| D27 | 10 | 9 | 7 | 2 | 3 |
| D28 | 12 | 13 | 6 | 7 | 6 |
| D29 | 14 | 12 | 11 | 1 | 3 |
| D30 | 3 | 3 | 3 | 0 | 0 |

### 5.2 Candidate coverage 与 repair

- Dream candidate：362。
- 最终 used：282；rejected：69；明确处置 351/362=96.96%。
- 初始 missing：11；补处置恢复 4，仍有 7 个 `FAILED_TECHNICAL`，因此 candidate coverage 未达到 100%。
- 5 次 missing-recovery 请求中 3 次仍因 `semantic_validation_failed_without_batch_repair` 失败；失败集中于 D15、D20、D26 对应 source。
- Grounder item repair：27 次；22 次得到合法结果，5 次仍非法，合法恢复率 81.48%。较上一轮 26/26 零恢复显著改善，但尚未闭环。
- Judge targeted coverage recovery：2 次，覆盖 3 个 missing draft；未再扩大为整文档失败。
- Judge item repair：1 次；最终保留 item-local fallback。仅有 1 条 `GENERIC_UMBRELLA_DUPLICATE` 语义校验记录，没有 whole-document repair。

### 5.3 主要 Mention bad case 模式

1. 同一 Gold 被拆成残片：D20 将“Mizuho 维持 Outperform 并上调目标价”拆成两个 Mention，严格 TP=0；D08、D09、D12、D28 的 actual/guidance 多指标事实也被拆散。
2. qualifier 丢失：D11 多条 revenue/EPS/margin/capex 输出保留主数值，却丢 consensus、YoY、prior-year 或构成项，导致 18 个输出只有 10 个严格 TP。
3. period/assertion 错位：D23 将 Q3 revenue/EPS 写成 fourth quarter；这既损害 Mention，也会污染 Identity sidecar。
4. 非 Gold 背景被提升：D04 两条股票历史涨幅、D13 三条泛 physical-AI 陈述、D16 两条 BofA 估值/回购评论、D17 analyst consensus 与泛 AI 陈述。
5. compound 未守恒：D04 将 Micron-Anthropic partnership/supply 与 Series-H investment 合在一个 Mention；D27 又把 revenue growth 与 adjusted margin 合为一个 Mention。

结论：`exactly once` 和补处置机制提高了候选覆盖，但模型通过“拆细”和“保留更多背景”满足覆盖，缺少同一 Gold 的信息守恒/最小业务事实边界。不能仅继续强化覆盖 prompt；否则 Precision 还会下降。

## 6. Evidence

| 指标 | 上一轮 | 本轮 | 门槛 | 结论 |
| --- | ---: | ---: | ---: | --- |
| Evidence records | 337 | 271 | — | Mention 结构变化导致口径变化 |
| VERIFIED/exact-or-source-equivalent | 333/337（98.81%） | 267/271（98.52%） | ≥99.5% | 失败，-0.29pp |
| TEXT_NOT_FOUND | 4 | 4 | 0为理想 | 数量不变 |
| semantic Evidence repair LLM | 0 | 0 | 0 | 通过 |
| Evidence 异常导致文档失败 | 1 | 0 | 0 | 通过，韧性修复有效 |

4 条 `TEXT_NOT_FOUND`：D28 SCA 跨句合成 evidence、D19 S&P 500 改写、D24 tight-beyond-2027 两段带引号改写。它们都是生成文本与原文不完全等值的问题；deterministic materialize 没有扩大为文档失败，但 exact rate 未提升。

## 7. Field

本轮复用了 Mention/Atomic/Package Gold，但上一轮 Field Gold 是按旧 Mention 字段 occurrence 建立的；本轮 254 个 Mention 全部换了 ID，且大量 Gold 被拆分/重组。直接把旧 Field 分母机械套入会把“字段移动到另一个残片”误报成正确或错误。因此本轮不能诚实地给出与上一轮同等强度的全量 Field Precision/Recall；以下给出可复现运行指标和已确认错误，不伪造精确率。

- canonical field links：1,021；`INTERNAL_COREFERENCE=763`、`EXTERNAL_LINKING=219`、`UNRESOLVED_CANONICALIZED=39`。UNRESOLVED 从上一轮 24 增至 39，不能宣称“未靠 UNRESOLVED 达标”。
- `FIELD_CANDIDATE_SNAPSHOT=612`，其中 selected_id 为空 317；candidate recall@8 不能从 selected 结果自证，当前 Registry 没有独立 field Gold snapshot，故不宣称达到 98%。
- 已确认旧错误仍复现至少 3 类：`Tech shares → Bio-Techne/INSTRUMENT_TECH`、`Mizuho → Mizuho Financial/COMPANY_MFG`、`commitment_value → XBRL TotalCommitmentFairValue`。
- 已确认修复：`trading_volume → TRADING_VOLUME`；`Magnificent Seven` 被保留为 asset，`Stoxx 600` 被保留为 instrument，未再误作普通 company/product。
- `FIELD_CANDIDATE_BLOCKED=11`，其中 share-price/index、margin value、close-price predicate 等窄 blocker 在运行；没有造成文档级阻塞。

因此 Field 的结论是“部分老 alias 修复有效，但全量门槛未获认证，且至少 3 个旧 bad case 仍复现”。下一轮若要正式验收 Field 门槛，必须把同一 Gold 的字段 occurrence 映射到当前 Mention，而不能继续用 runtime link 数代替准确率。

## 8. Atomic / N7 / N9 / Identity

### 8.1 Gold 投影指标

| 指标 | 上一轮 | 本轮 provisional | 目标 | 结论 |
| --- | ---: | ---: | ---: | --- |
| Pair Precision | 52.58% | 329/461=71.37% | >70% | 刚好通过 |
| Judgeable Pair Recall | 85.71% | 329/(329+22)=93.73% | >80% | 高置信子集通过 |
| 最大 Atomic cluster | 12 | 18 | 不形成 supercluster | 33-member 未复现，但环比恶化 |
| N9 MERGE Precision | 61.82% | 80/108=74.07% | >75% | 差0.93pp |
| whole-task invalid fallback | 45 tasks | 0 | 0 | 通过 |
| singleton absorption | 0 | 1，Gold 正确 | 受限启用 | 通过 |
| hard-conflict violations | 158 | 378 | 应减少 | 明显回归 |

Pair Recall 的 22 个 FN 是高置信、可复现的 same-fact 跨簇漏并；模糊表达粒度未强行计入，因此 93.73% 是 judgeable projection，不应冒充新一轮独立人工 Gold 的绝对值。即使按更保守边界扩大 FN，本轮 Recall 仍明显好于上一轮；核心问题已从“过拆主导”转为“过并和局部过拆并存”。

N9 粒度降级修复有效：254 个 assignment 中 `MERGE=108`、`CREATE_NEW=146`；没有 `N9_INVALID_TASK_CREATE_NEW` 整任务降级。96 个 N9 validation audit 均保留在 batch/assessment 路径，未将单 candidate 格式错误扩大成整个 task 失败。

### 8.2 主要 cluster bad case

1. revenue cluster：18 Mention，约 16 条属于同一 Micron fiscal-Q3 revenue 事实，但混入 $11.3B 的不同季度/年度事实和泛“earnings exceeded expectations” umbrella；仍是最大 FP 来源。
2. EPS cluster：12 Mention，主要 11 条是同一 $25.11 actual EPS，混入 $20.63 analyst estimate。D23 把 actual 错写成 fourth quarter，说明上游 period 错位会绕过 Identity 语义边界。
3. Q4 revenue-guidance cluster：12 Mention，主簇是 $50B/current-quarter revenue guidance，但混入 Q4 net-income>$40B；metric boundary 仍有漏网。
4. SCA cluster：12 Mention，把签署协议、volume share、长期 visibility、contract duration 和 future revenue share 放在一起；共同 container 仍在替代最小 Atomic 身份。
5. Apple price cluster：price raise、具体产品涨价与 “iPhone prices unchanged” 被并在一起。Object 和 action polarity 仍未形成稳定边界。
6. analyst action cluster：Mizuho target、Mizuho rating 与 Citi target 混在同一 Atomic；institution/source report 边界仍不稳定。
7. 残余过拆：adjusted gross margin 被拆为 3 个 Atomic；同一 no-line-of-sight supply statement 被拆为 2 个；同一 after-hours Micron reaction 被拆为多个 Atomic。

### 8.3 Sidecar/validator 观察

- `ATOMIC_MERGE_INVARIANT=109`：103 `ALLOW`，6 `SEMANTIC_REVIEW`，后者全部由 `MARKET_MEASURE` 触发。
- 配置中声明的 `ASSERTION_STATE/COMPLETE_REFERENT/METRIC/PRIMARY_METRIC_FAMILY` 在最终 invariant audit 中没有一次形成 `triggered_rules`；本轮改善主要来自模型判断和输入结构，而不是 hard rule 实际拦截。
- `ATOMIC_HARD_CANNOT_LINK_OBSERVED=3,625` 仍为 shadow；机器报告在 30,273 个评估 pair 中记录 378 个最终同簇冲突。该数不能直接等于业务 FP，但从 158 增到 378，说明大簇增长重新放大了结构冲突。
- 已知 false split 中，adjusted-margin、no-line-of-sight、after-hours reaction 的正确候选都已进入 N7 top-5，但 N9 仍判 `RELATED/CREATE_NEW`；这类问题不是 N7 missing candidate，而是 axis/period/assertion 编译或 N9 语义决策问题。
- SK Hynix listing plan 的正确 listing candidate 未进入 top-8，属于仍存在的 N7 recall 漏洞。

结论：assessment-level fallback 和受限 singleton absorption 应保留；不能回滚。Sidecar hard rules 不能宣称已被本轮真实验收，因为配置存在不等于实际触发。下一步应先解释“为什么四条已启用规则在 109 个 merge invariant 中零触发”，而不是继续增加规则数量。

## 9. Package / N12 / N13

### 9.1 Gold 投影

| 指标 | 上一轮 | 本轮 provisional | 目标 | 结论 |
| --- | ---: | ---: | ---: | --- |
| Package Pair Precision | 80.43% | 约1198/1396=85.82% | >90% | 改善但失败 |
| Package Pair Recall | 38.95% | 约1198/1212=98.84% | >80% | 通过，但由强聚合驱动 |
| 多成员 Package | — | 14 | — | 主要集中于两个大包 |
| 最大 Package | — | 51 Atomic | 不应形成污染超大包 | 失败 |
| N13 SAME | 上一轮有弱 SAME apply | 0/43 | 禁用弱 SAME | 符合设计 |
| reaction→earnings | 1个严格 family defect | 机器边界1条 | 0 | 未完全通过 |

该 Recall 是高置信 parent-cluster 投影，主要由 Micron fiscal-Q3 earnings 的大量 Atomic 被收进同一 Package 贡献。它不能单独证明质量提高：当一个超大包吞入外部分析、泛供需陈述或不同市场时点时，Recall 会天然上升，Precision 则下降。

### 9.2 Anchor 留存与继承

- 254 个 Mention 中 101 个 `local_package_hint` 非空，覆盖 39.76%。
- 68 个 Package 中仅 9 个有 `package_anchor_ids`（13.24%），仅 8 个有 `primary_anchor_id`（11.76%）。
- `anchor_artifact_id` 仍为 0/68。
- 最大 earnings Package 聚合了 12 个不兼容 anchor，`anchor_conflict=true`、`primary_anchor_id=null`，包含 51 Atomic。
- 这说明“保留完整 anchor 集合和 conflict”审计闭环已经工作，但 canonical anchor 归一并没有把同一 Q3 earnings 的不同 raw hint 收敛成一个稳定 parent identity；下游只能靠集合相交/主题语义做强聚合。

### 9.3 典型 Package bad case

1. 51-Atomic earnings Package：多数成员确属 Micron Q3 earnings，但混入外部 Jake Behan assessment、泛 AI-memory demand 陈述等边界可疑成员；同时 anchor_ids=12，导致 N12 payload 和判断难度膨胀。
2. 15-Atomic market Package：混合 after-hours、premarket、regular-session、Tuesday historical drop、YTD/one-year return、market cap、valuation 和 earnings growth。它不是一个稳定的最小 parent occurrence。
3. SK Hynix 市场 Package：同包包含 Thursday rise、Tuesday fall 和 2.8M price level，session/time 边界不足。
4. S&P、Nasdaq、Dow 两成员包：将 premarket/intraday/close 的不同观测合包，重复暴露 market-session 边界。
5. 正向样例：SK Hynix listing 的 plan/proceeds/listing 相关 Atomics 进入同一 parent；Wedbush stance+rating、Qualcomm Meta customer+custom-chip deals 的 package 边界基本合理。

### 9.4 N13 判断

N13 的降本是真实的，但本轮不应继续把精力集中在 N13：4 次请求、43 个 pair 全部为 `DIFFERENT_PACKAGE`，aggregate latency 仅占 0.62%。Package 质量和成本的主战场已经前移到 N11 canonical anchor 和 N12 assignment；尤其是大包 profile 被反复完整发送的问题。

## 10. 验收矩阵

| 维度 | 门槛/预期 | 本轮 | 判定 |
| --- | --- | --- | --- |
| 端到端成功 | 30/30 | 29/30 | 失败 |
| Mention Precision | >90% | 69.29% | 失败 |
| Mention Recall | >85% | 65.67% | 失败 |
| Candidate coverage | 100% | 96.96% | 失败 |
| Evidence exact/source-equivalent | ≥99.5% | 98.52% | 失败 |
| Evidence semantic repair | 0 | 0 | 通过 |
| Evidence 异常不致文档失败 | 必须 | 0文档失败 | 通过 |
| Field 门槛 | predicate/participant/metric/period/total | 无可辩护全量新映射 | 未认证 |
| Atomic Pair Precision | >70% | 71.37% provisional | 通过 |
| Atomic Pair Recall | >80% | 93.73% provisional | 通过 |
| N9 MERGE Precision | >75% | 74.07% provisional | 失败 |
| N9 conditional MERGE Recall | >85% | 高置信机会显著改善，但无完整新 mapping | 未认证 |
| N9 whole-task fallback | 0 | 0 | 通过 |
| Package Pair Precision | >90% | 85.82% provisional | 失败 |
| Package Pair Recall | >80% | 98.84% provisional | 通过但需结合超大包解释 |
| N13 Input 降幅 | ≥60% | -84.55% | 通过 |
| N13 Token share | 15%-30%机会值 | Input占2.66% | 超额降本 |
| 总 Input Token | 应下降 | +25.51% | 失败 |
| 总运行时长 | 应下降 | +9.18% | 失败 |

## 11. 最终判断与建议边界

本轮不建议整体回滚：N13 降本、单文档 30/30、Grounder item repair 81.48% 恢复、Judge targeted coverage、N9 assessment-level fallback 和 singleton absorption 都有独立收益。

但也不能接受当前实现直接进入生产默认路径，原因按优先级为：

1. 修复 `WIRE_PAYLOAD_SHADOW` immutable audit ID 冲突，并保证失败 run 的 Package 修复具备原子性或可恢复性；否则 30/30 无法成立。
2. Mention 覆盖机制必须增加“信息守恒和同一 Gold 不碎片化”的约束/后处理；不能继续单纯加大 missing recovery。
3. N11 应把 `Q3 earnings release/report/call` 等同一 parent 收敛为一个 canonical anchor，而不是让 N12 携带 12-anchor conflict 的 51-member profile。
4. N12 输入应改为稳定摘要+增量 diff/代表成员，设置业务安全的 payload 上限；这是下一轮 Token 和时长最大的优化点。
5. 对四条声称启用的 Sidecar rule 做实际触发审计；零触发必须先解释。不要新增更多 hard rule。
6. Field 应补一次当前 Mention occurrence→旧 Gold field 的 deterministic remap，才能正式判断各字段门槛；现有 runtime link 数不足以证明准确率。

本报告仅评估，不包含上述修复实施，也未执行任何回滚。
