# CDECR 固定 30 篇真实验收与上一轮对比报告

评估日期：2026-07-30
被测版本：`e7ad7d4+working-tree`

## 1. 结论

本轮不通过整体业务验收，但也不建议整体回滚。

可以保留的改动：

- N13 pair 削减、exact profile-pair 去重及窄的 exact-period DIFFERENT guard；
- N13 输入 Token、调用数和延迟的大幅下降；
- Field predicate/metric/fiscal-period 映射改进；
- 对极端 Atomic supercluster 的抑制。

必须阻断发布并修复或局部回滚的部分：

- 1 篇文档因本地 Dreamer repair 序列化 `TypeError` 失败；
- Mention P/R/F1 全面回落，candidate disposition coverage 仅 87.96%；
- 26 次 Grounder 单条 repair 全部再次校验失败，没有恢复任何非法 draft；
- Identity precision/recall、Field candidate recall@8 和 participant accuracy 未达门槛；
- Atomic 从极端过合并转为“过拆为主、少数高危复合过合并”；
- 45/199个 N9 task 因单项校验问题整体 fallback 为 CREATE_NEW；
- Package Recall 从约 73.5% 降至 38.95%，Micron earnings 严重碎片化；
- N13 仍把 reaction Atomic 合入 disclosure/earnings Package，语义口径至少2包/6 Atomic/12 Mention。

建议采用选择性处置：

1. 保留 N13 降本编排、profile 去重和 exact-period guard；mixed guard改为split/review；
2. 暂停 mixed、reaction↔earnings、跨 analyst-source、弱 anchor 的 N13 SAME 自动 Apply；
3. 修复 Dreamer repair 的错误对象序列化和 Grounder item repair 的零恢复问题；
4. 修复后用同一 30 篇重新验收，不应直接接受当前结果。

## 2. 运行范围与产物

固定语料：

- Snapshot：`.tmp/cdecr/grounder_quality_v5/live_all.jsonl`
- Manifest：`dev_plan/CDECR/experiments/grounder_quality_30_manifest.json`
- 文档数：30

本轮产物：

- Registry：`.tmp/cdecr/acceptance_20260730/cdecr_30_20260730.sqlite3`
- Runtime report：`.tmp/cdecr/acceptance_20260730/cdecr_30_20260730_report.json`
- Cluster export：`.tmp/cdecr/acceptance_20260730/cdecr_30_20260730_clusters.json`
- 机器摘要：`dev_plan/CDECR/experiments/cdecr_30_real_acceptance_20260730_summary.json`
- 人类可读层级结果：`dev_plan/CDECR/CDECR_30_CORPUS_HIERARCHY_RESULTS_20260730.md`

关键 SHA-256：

- Registry：`1312E08D891BC0BDE0E29317E0554D76971B3543DE0E86546B2DFF3EF6A74F0E`
- Runtime report：`E011D49CCAABB9B48B4118F954ADD402C7A36F00D9FE5BA4DD55C5C8763E72BD`
- Cluster export：`DE282DCF2140E2ED4E594C107D21BF8DE2C2EE80EFF2CBC207B06530033625D1`

上一轮基线：

- Registry：`.tmp/cdecr/resilience/resilience_30_20260728.sqlite3`
- 报告：`dev_plan/CDECR/CDECR_ITEM_SCOPED_RESILIENCE_30_ACCEPTANCE_20260728.md`
- 机器摘要：`dev_plan/CDECR/experiments/cdecr_item_resilience_30_20260728_summary.json`

## 3. 运行成功率与失败根因

| 指标 | 上一轮 | 本轮 | 变化 |
| --- | ---: | ---: | ---: |
| 单文档成功 | 30/30，100% | 29/30，96.67% | -3.33pp |
| 成功文档的跨文档成功 | 30/30，100% | 29/29，100% | 持平 |
| Mention | 225 | 199 | -26 |
| Atomic | 95 | 131 | +36 |
| Package | 65 | 61 | -4 |
| 模型调用 | 940 | 786 | -16.38% |
| 首轮墙钟 | 5,397.721 秒 | 6,027.709 秒 | +11.67% |
| 重启验证后总墙钟 | 5,408.298 秒 | 6,035.206 秒 | +11.59% |
| 重启新增模型调用/实体 | 0 | 0 | 通过 |

唯一失败文档：

- Source row：`b24ea6b3-557e-4053-9cae-c8e419455b39`
- Message：`doxatlas:raw_media:b24ea6b3-557e-4053-9cae-c8e419455b39`
- 标题：`SK Hynix Unveils $29 Billion US Listing as Shares Soar Over 800%`
- 旧 Gold：9 条；本轮输出：0；9 条全部计 FN。

失败轨迹：

1. `title_embedding` 成功；
2. `dreamer` 模型调用成功；
3. Dreamer 输出包含 `end_char` 缺失、误写 `end_color`，以及两个 Evidence locator value error；
4. 进入 `_invoke_typed.repair_and_validate`；
5. `single_document.py:1267` 对 Pydantic `validation_error` 执行 `json.dumps`；
6. Pydantic error context 内含不可 JSON 序列化的异常对象，触发本地 `TypeError`；
7. Grounder 尚未调用，整篇文档失败。

因此该失败不是模型供应商、欠费、超时或并发问题，而是 repair 审计对象未先转为 JSON-safe payload。它同时意味着“Evidence/schema 异常不得扩大为文档失败”的门槛未闭环。

## 4. Token、Payload 与总时长

| 指标 | 上一轮 | 本轮 | 变化 |
| --- | ---: | ---: | ---: |
| 输入 Token | 4,403,264 | 2,610,616 | -40.72% |
| 输出 Token | 443,843 | 443,211 | -0.14% |
| 总 Token | 4,847,107 | 3,053,827 | -36.99% |
| Request payload bytes | 12,781,656 | 6,946,718 | -45.65% |
| 每篇入选文档总 Token | 161,570 | 101,794 | -36.99% |
| 每篇成功文档总 Token | 161,570 | 105,304 | -34.82% |
| 首轮墙钟 | 89分58秒 | 100分28秒 | +10分30秒 |

结论：Token 和 payload 显著下降，但墙钟时间反而上升。效能优化只完成了“成本下降”，没有完成“运行更快”。新瓶颈已从 N13 转移到 N12、N9 和 Grounder；随机模型延迟及后半程候选图增长抵消了 N13 的节时收益。

## 5. 各节点 Token 与运行时占比

下表“运行时占比”采用各节点模型调用 `latency_ms` 的聚合占比。由于并行调用会重叠，它用于定位模型等待成本，不等同于对墙钟时间的可加和分摊。

| 节点/Stage | 调用 | 输入 | 输出 | 总 Token | 总Token占比 | 聚合模型时长 | 时长占比 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| package_assignment（N12） | 38 | 821,126 | 94,282 | 915,408 | 29.98% | 1,668.112s | 24.21% |
| atomic_coreference（N9） | 75 | 504,437 | 135,894 | 640,331 | 20.97% | 1,442.235s | 20.93% |
| package_merge（N13） | 47 | 563,764 | 24,954 | 588,718 | 19.28% | 464.217s | 6.74% |
| grounder | 29 | 119,121 | 98,303 | 217,424 | 7.12% | 1,730.528s | 25.12% |
| field_coreference | 208 | 188,444 | 3,310 | 191,754 | 6.28% | 214.533s | 3.11% |
| judge | 29 | 140,769 | 17,243 | 158,012 | 5.17% | 245.469s | 3.56% |
| grounder_item_repair | 26 | 65,221 | 12,423 | 77,644 | 2.54% | 226.238s | 3.28% |
| dreamer | 30 | 32,239 | 39,455 | 71,694 | 2.35% | 485.044s | 7.04% |
| atomic_coreference_escalation | 7 | 48,873 | 15,168 | 64,041 | 2.10% | 267.993s | 3.89% |
| package_embedding_m1 | 57 | 49,872 | 0 | 49,872 | 1.63% | 24.475s | 0.36% |
| atomic_identity_embedding | 33 | 41,463 | 0 | 41,463 | 1.36% | 19.595s | 0.28% |
| judge_repair | 3 | 16,341 | 2,179 | 18,520 | 0.61% | 28.167s | 0.41% |
| atomic_recall_m1 | 33 | 7,397 | 0 | 7,397 | 0.24% | 17.237s | 0.25% |
| package_merge_embedding_m1 | 12 | 6,059 | 0 | 6,059 | 0.20% | 4.708s | 0.07% |
| field_coreference_recall | 129 | 4,449 | 0 | 4,449 | 0.15% | 39.846s | 0.58% |
| title_embedding | 30 | 1,041 | 0 | 1,041 | 0.03% | 11.682s | 0.17% |

关键变化：

- N13 input：2,535,383 → 563,764，-77.76%；
- N13 calls：162 → 47，-70.99%；
- N13 aggregate latency：2,154.329s → 464.217s，-78.45%；
- N12 input：715,487 → 821,126，+14.76%，已成为第一大 Token 节点；
- N9 主调用 input 仅下降1.83%，但 aggregate latency 上升66.81%；
- Grounder 主调用 aggregate latency 上升14.78%，另新增226.238s的无效 item repair；
- 总输出 Token 几乎没有下降，说明主要收益来自输入上下文和候选对减少，而非模型输出收缩。

## 6. N13 专项成本审计

| 指标 | 上一轮 | 本轮 | 变化/结论 |
| --- | ---: | ---: | --- |
| Pair decisions | 1,768 | 523 | -70.42% |
| 进入 LLM 的 pair | 未单列 | 355 | 168条被 deterministic DIFFERENT 截断 |
| Calls | 162 | 47 | -70.99% |
| Input Token | 2,535,383 | 563,764 | -77.76%，通过≥60%门槛 |
| Output Token | 117,989 | 24,954 | -78.85% |
| 总 Token | 2,653,372 | 588,718 | -77.81% |
| 总Token占比 | 54.74% | 19.28% | 进入15%–30%预期 |
| Exact unordered profile-pair重复 | 未知 | 0 | 通过 |

剩余成本原因：

- Exact profile-pair 去重有效，重复为0；
- 但 package profile 演化后，相同 package-ID pair 被重新评估235次，涉及77组，最多17次；
- 单个 LLM pair 的输入反而从约1,434增至约1,588 Token，+10.74%；
- payload/item 约增加14.25%；
- 因此77.76%的降本主要来自 pair pruning，而不是剩余请求的单pair压缩。

正确新增 join 成本：

- 36个 SAME decision，27个实际 Apply；
- 旧 Gold 可严格确认9个正确 Apply、1个错误 Apply，17个不可判；
- 严格已确认口径：62,640 input Token/正确 join，65,413 total Token/正确 join；
- 若17个不可判全部正确，理论下界为20,880 input、21,804 total Token/正确 join；
- Registry 只保留 batch-level Token，不能把每个 batch 的 Token 精确拆到单条 join，因此应报告该区间，不能伪造单条精确成本。

## 7. Gold 可复用性与评估方法

上一轮 Gold 的业务定义可以复用：

- Mention Gold 是按来源先建立的268条独立语义命题；
- Atomic Gold 使用“最小同一事实”边界；
- Package Gold 区分 bounded disclosure、episode、reaction 和 analyst-source。

但旧 Gold 的机器表示并非完全跨运行可复用：

- 新旧 Mention ID 交集为0；
- 本轮 Mention 从225降至199，且1篇文档失败；
- 旧 Atomic/Package 审计主要保存旧 ID 上的分区和聚合结果，没有保存一份可直接套到随机新 Mention 的完整 membership Gold。

本轮没有重新发明 Gold，而是由 Codex 子 Agent 按相同来源与旧 Gold 命题做逐文档语义重映射：

- Mention：全199条逐文档 strict one-output↔one-Gold；
- Field：逐 unique raw→canonical 映射审查；
- Identity/Atomic：高置信 embedding 对齐子集与全量多成员簇人工规则复核；
- Package：105/131个可判 Atomic 投影到旧 Package Gold，并提供 exact-only 敏感性结果。

因此 Mention 数字具有最完整的可比性；Field、Identity、Atomic、Package 指标均明确标记 provisional。方向性结论稳定，但不应伪装成新建完整独立 Gold 后的无误差数字。

## 8. Mention 质量

| 指标 | 上一轮 | 本轮 provisional strict | 变化 |
| --- | ---: | ---: | ---: |
| Gold | 268 | 268 | 持平 |
| Output | 225 | 199 | -26 |
| TP | 192 | 153 | -39 |
| FP | 33 | 46 | +13 |
| FN | 76 | 115 | +39 |
| Precision | 85.33% | 76.88% | -8.45pp |
| Recall | 71.64% | 57.09% | -14.55pp |
| F1 | 77.89% | 65.52% | -12.37pp |

门槛结论：

- Recall 至少+5pp：失败，实际-14.55pp；
- Precision下降不超过1.5pp：失败，实际-8.45pp；
- known compound bad cases修复率≥80%：失败；
- 新增 fragmentation≤2：失败，至少3组；
- candidate coverage=100%：失败，87.96%。

旧3个 compound/non-atomic bad case：

1. D03 revenue + data-center revenue：仍合并在同一 Mention，未修复；
2. D04 partnership/supply/investment：结构上不再 compound，但遗漏 investment 等 Gold；
3. D12 三类投资者资金流：成功拆为3条独立 Mention，完整修复。

因此：

- 只看结构不再 compound：2/3，66.7%；
- 要求拆分且不损失 Gold：1/3，33.3%。

新增 fragmentation 至少3组：

- D08：同一 Gold 的 Q3 net income + adjusted EPS 被拆成两个不完整 Mention；
- D12：同一 Gold 的 Q3 revenue + adjusted EPS 被拆成两条；
- D12：同一 Gold 的 SK Hynix 涨幅 + static VI 触发被拆成两条。

另有3组反方向的新 compound：

- D26 Apple -5% 与 -$200B 市值合并；
- D27 Micron revenue quadrupling 与约85% adjusted margin合并；
- D29 DRAM至2028与NAND至2027的供给约束合并。

Candidate disposition：

- candidate_count=324；
- used=239，rejected=46；
- 完整 disposition=285；
- missing=39；
- coverage=285/324=87.96%；
- 16/29个成功文档批次触发 individual repair。

26次 `grounder_item_repair` 模型请求本身均返回成功，但26次全部在 `mention` 字段再次触发 `value_error`，最终全部降级丢弃。其成本为65,221 input、12,423 output、226.238秒聚合模型时长，恢复数为0。这是本轮 Mention recall 回落和额外延迟的直接工程原因之一。

即使不做逐条语义复核，仅按每篇 `min(output_count, gold_count)` 计算，本轮 Recall 理论上限也只有194/268=72.39%，相对旧轮最多+0.75pp，仍不可能达到+5pp门槛。

### 8.1 Grounder disposition 的完整分布

按成功文档逐篇展开，格式为 `candidate / used / rejected / missing`：

| 文档 | 分布 | 文档 | 分布 | 文档 | 分布 |
| --- | ---: | --- | ---: | --- | ---: |
| D01 | 10 / 7 / 1 / 2 | D11 | 22 / 17 / 2 / 3 | D21 | 8 / 3 / 3 / 2 |
| D03 | 11 / 8 / 3 / 0 | D12 | 17 / 14 / 3 / 0 | D22 | 8 / 8 / 0 / 0 |
| D04 | 8 / 7 / 1 / 0 | D13 | 7 / 7 / 0 / 0 | D23 | 11 / 5 / 5 / 1 |
| D05 | 10 / 10 / 0 / 0 | D14 | 20 / 13 / 6 / 1 | D24 | 12 / 5 / 1 / 6 |
| D06 | 17 / 11 / 4 / 2 | D15 | 13 / 12 / 0 / 1 | D25 | 7 / 7 / 0 / 0 |
| D07 | 11 / 11 / 0 / 0 | D16 | 19 / 14 / 2 / 3 | D26 | 9 / 4 / 2 / 3 |
| D08 | 15 / 10 / 3 / 2 | D17 | 12 / 9 / 1 / 2 | D27 | 11 / 9 / 2 / 0 |
| D09 | 11 / 7 / 1 / 3 | D18 | 3 / 3 / 0 / 0 | D28 | 14 / 11 / 0 / 3 |
| D10 | 11 / 9 / 0 / 2 | D19 | 7 / 7 / 0 / 0 | D29 | 13 / 8 / 2 / 3 |
| D20 | 2 / 2 / 0 / 0 | D30 | 5 / 1 / 4 / 0 | — | — |

46个明确 rejected 的原因分布为：`BACKGROUND=35`、`NOT_INDEPENDENT=7`、`UNSUPPORTED=3`、`OUT_OF_SCOPE=1`。这些条目有可审计处置，不属于“静默丢失”。真正的协议缺口是以下39个 missing candidate。

### 8.2 39个 missing candidate 的字段级 bad case

下表列出 Registry 中全部 missing candidate；“字段/问题”表示它本应形成 Mention 时最先丢失或失真的业务信息。一个 candidate 可能对应多个 Gold，少数 candidate 又是已有输出的重复或非 Gold，因此不能把39机械等同于39个 FN。

| 文档 | candidate | 内容摘要 | 字段/问题 |
| --- | --- | --- | --- |
| D01 | c7 | Counterpoint估算iPhone组件成本约+$200、全线涨价$150–$200 | `source+cost metric+price range` 未落 Mention |
| D01 | c10 | Apple盘中-0.56%且宣布MacBook/iPad涨价 | market move与product action compound 未安全拆分 |
| D06 | c1 | Q3 revenue同比+346%至41.46B | `metric trend+quantity+period` 未落 Mention |
| D06 | c3 | RSI升至60中段、接近超买 | `technical metric+state` 未落 Mention |
| D08 | c11 | 新工厂到2028年前不会增加显著产出 | `capacity+horizon` 未落 Mention |
| D08 | c13 | 本季度adjusted EPS约31 | `guidance metric+period` 未落 Mention |
| D09 | c4 | cleanroom转向DRAM，进一步限制NAND供给增长 | `causal action+supply state` 未落 Mention |
| D09 | c7 | revenue同比+346% | `metric trend` 未落 Mention |
| D09 | c9 | 季度股息$0.15、7月21日支付/7月6日登记 | `dividend+two dates` 未落 Mention |
| D10 | c3 | AI客户需求激增，可能处于增长早期 | `demand state+assessment` 未落 Mention |
| D10 | c10 | 公司称AI革命仍在early innings | `source claim+outlook` 未落 Mention |
| D11 | c6 | GAAP gross margin 84.6% vs 37.7% | `metric+basis+comparison` 未落 Mention |
| D11 | c13 | Qualcomm称已确保产能并对forecast有信心 | `participant+capacity action+outlook` 未落 Mention |
| D11 | c22 | 合同可执行性、未建产能及hyperscaler capex集中风险 | 多项 analyst assessment，缺明确 rejected disposition |
| D14 | c5 | Micron周二下跌13% | `market move+date` 未落 Mention |
| D15 | c7 | 当季operating margin 80.4% | `metric+period` 未落 Mention |
| D16 | c3 | Wedbush维持对Micron的bullish stance | `analyst source+rating/stance action` 未落 Mention |
| D16 | c6 | gross margin 84.9% vs consensus 81.7% | `metric+benchmark` 未落 Mention |
| D16 | c19 | 上调2026 capex并预示2027显著增支 | `metric action+two fiscal periods` 未落 Mention |
| D17 | c4 | Sandisk将在8月24日报告业绩 | `scheduled artifact+date` 未落 Mention |
| D17 | c9 | Micron operating margin超过80% | `metric+bound` 未落 Mention |
| D21 | c6 | WDC/SNDK/STX常规时段交易量活跃 | `participants+trading volume+session`，可能应rejected但无处置 |
| D21 | c7 | Micron业绩显示memory upcycle强劲 | `assessment/state`，可能应rejected但无处置 |
| D23 | c6 | SCA与HBM ramp提供超越典型周期的可见性 | `artifact+capacity action+outlook` 未落 Mention |
| D24 | c1 | Apple多产品涨价、iPhone价格不变 | `product actions+object boundary` 未安全拆分 |
| D24 | c6 | AI从根本上重塑memory行业 | `causal/state` 未落 Mention |
| D24 | c7 | memory已成战略资产且AI系统依赖memory | `state/dependency` 未落 Mention |
| D24 | c9 | DRAM/NAND需求收紧供给并强化价格/长期协议 | `causal chain+commercial outcome` 未落 Mention |
| D24 | c10 | 客户通过战略协议commit约22B | `artifact+commitment quantity` 未落 Mention |
| D24 | c11 | AI memory短缺可能扩散至普通消费者 | `impact/outlook` 未落 Mention |
| D26 | c1 | record revenue、84.9% margin及下季86% guidance | 多metric、跨actual/expected compound 未安全拆分 |
| D26 | c5 | Apple股价跌回May breakout、275–280区间重新受关注 | `technical state+price range` 未落 Mention |
| D26 | c9 | 收于275–280上下对应successful test或bull trap | 条件式technical outlook，缺明确rejected disposition |
| D28 | c2 | Q4 gross margin约86%、adjusted EPS 31±1 | `guidance+two metrics` 未落 Mention |
| D28 | c9 | 16份SCA覆盖约20% DRAM及三分之一NAND | `artifact count+share quantities` 未落 Mention |
| D28 | c14 | SCA增强财务表现的durability/predictability | `artifact impact+outlook` 未落 Mention |
| D29 | c1 | EPS 25.11、beat consensus 4.72 | `metric+benchmark` 未落 Mention |
| D29 | c2 | revenue 41.5B、beat estimates 6.4B | `metric+benchmark` 未落 Mention |
| D29 | c9 | UBS称NAND至少到2027年底仍受限 | `source claim+supply state+horizon` 未落 Mention |

这些条目集中在复合 metric/quantity、guidance 的 assertion state/period、benchmark/range qualifier、market session、source assessment 与跨 participant/artifact 的 action。D21两条、D11 c22、D26 c9等可能最终应 rejected，而不是形成 Mention；但当前问题正是没有任何最终 disposition，不能事后把它们默认为 FN或背景。其余大多数与 Gold 事实或其必要限定信息直接相关。

### 8.3 为什么单条 repair 仍然无效

26次 repair 的共同审计信息只有 `loc=["mention"]` 与 `type="value_error"`；repair prompt 只要求“修正 schema 所需字段”。真正触发失败的是 Pydantic 根级业务约束，而该约束既不在 JSON Schema 中，也没有通过安全错误码或短 `msg` 传给模型。站在无项目上下文的模型视角，它看不到究竟是 PRIMARY 唯一性、assertion 组合还是其他语义约束非法，只能盲修，所以出现26/26再次非法。

归因判断：

- **上一轮问题复现**：compound 拆分后丢 qualifier/benchmark/period，说明旧问题没有被修复；
- **修复实现失效**：单 draft、并行 repair 的架构无需回滚，但错误协议缺信息，导致新增成本而恢复数为0；
- **直接修复**：只给非法单 draft 增加短业务错误码/安全 `msg`，并为同类连续零恢复设置熔断；不能改回整篇串行 repair；
- **审计缺口**：missing candidate 没有持久化最终 rejected reason，D09/D10等条目无法从现有 Registry 还原最后一个具体业务校验字段。

## 9. Evidence 质量

| 指标 | 上一轮 | 本轮 | 门槛 |
| --- | ---: | ---: | --- |
| Final Evidence | 352 | 337 | — |
| VERIFIED/exact/source-equivalent | 336/352，95.45% | 333/337，98.81% | ≥99.5%，失败 |
| TEXT_NOT_FOUND | 16 | 4 | 改善 |
| Semantic Evidence repair LLM | 0 | 0 | =0，通过 |
| Evidence/schema异常导致文档失败 | 0 | 1 | 必须为0，失败 |

本轮 repair_kind：

- EXACT：328；
- ANCHOR_DISAMBIGUATED：3；
- SOURCE_EQUIVALENT_WHITESPACE：2；
- TEXT_NOT_FOUND：4。

Evidence 定位质量明显改善，但仍差0.69pp未达99.5%。更重要的是，Dreamer Evidence/schema validation error 进入 repair 后触发本地序列化错误，最终扩大为整篇失败，所以韧性门槛失败。

### 9.1 全部4条 Evidence bad case

| 文档 | Evidence 摘要 | 失败阶段 | 根因与归因 |
| --- | --- | --- | --- |
| D19 | S&P 500 +0.6%至7,401.17 | MATERIALIZE / `TEXT_NOT_FOUND` | 源 HTML 文本与生成文本不完全一致；属于上一轮 exact locator 残留模式 |
| D25 | Citi认为更健康的 NAND 基本面构成支持 | MATERIALIZE / `TEXT_NOT_FOUND` | 模型生成了语义概括而非源文逐字片段；本轮新 bad case |
| D25 | Citi提高财务预测 | MATERIALIZE / `TEXT_NOT_FOUND` | 同上，概括替代原句；本轮新 bad case |
| D28 | Micron宣布16份 SCA，覆盖20% DRAM及约三分之一 NAND | MATERIALIZE / `TEXT_NOT_FOUND` | 多处源信息被合成改写，deterministic alignment无法映射；上一轮 compound/evidence paraphrase问题的残留形态 |

与上一轮16条 `TEXT_NOT_FOUND` 相比，本轮降至4条，说明 exact/anchor/source-equivalent 修复有效，不能回滚。剩余问题应在生成侧要求 Evidence 保持原文 span，或在 materialize 前做不调用 LLM 的候选 span 对齐；不应恢复 semantic Evidence repair LLM。

文档失败另有独立根因：D02 的 Dreamer 输出缺 `end_char`、多出 `end_color`，两个 locator 触发 validation error；代码随后把 `errors()` 中 `ctx` 携带的 exception 对象直接交给 `json.dumps`，本地抛出 `TypeError`。模型调用已经返回，真正扩大为整篇失败的是 repair 错误序列化，而非 Evidence 内容本身不可恢复。该问题是**本轮韧性修复引入的实现缺陷**，可直接做 JSON-safe 序列化并保持 block/item 级降级，无需回滚 repair 架构。

## 10. Field 质量

本轮核心字段口径：

- predicate：199；
- participant：239；
- metric：214；
- fiscal period：13；
- core total：665；
- 可比 total 再加 location 20、open attributes 10，并排除新增 `local_package_hint.anchor` 10，共695。

| Field | 上一轮 | 本轮 provisional strict | 门槛 | 结论 |
| --- | ---: | ---: | ---: | --- |
| predicate | 83.56% | 194/199，97.49% | ≥90% | 通过 |
| participant | 92.72% | 228/239，95.40% | ≥96% | 失败，差0.60pp |
| metric | 81.07% | 207/214，96.73% | ≥90% | 通过 |
| fiscal period | 50.00% | 13/13，100% | ≥80% | 通过，但coverage显著变小 |
| comparable total | 83.29% | 670/695，96.40% | ≥91% | 通过 |

补充门槛：

| 指标 | 本轮 | 门槛 | 结论 |
| --- | ---: | ---: | --- |
| candidate recall@8 proxy | 51.81% | ≥98% | 失败 |
| safe deterministic precision | 106/106，100% | ≥99% | 通过 |
| runtime alias/错误映射 | 7 | 新增=0 | 失败 |
| UNRESOLVED | 24（旧52） | 不得显著增加 | 通过，没有靠增加UNRESOLVED达标 |

明确的7条 runtime alias/错误映射包括：

- Tech shares → Bio-Techne stock；
- Mizuho → Mizuho Financial company；
- committed_capital → US-GAAP InvestmentCompanyCommittedCapital；
- commitment_value → XBRL TotalCommitmentFairValue；
- secure_supply → constrain_supply；
- trade_volume → trade/move；
- disclose_status → report_metric。

UNRESOLVED 从52降至24，且逐条看主要为安全不链接/新建，因此没有“靠扩大 UNRESOLVED 提高表面精度”。但 HBM adoption、physical AI、16 customer agreements、Stoxx 600 等也暴露了上游 namespace 或 Mention extraction 污染。

Field 提升不能全部归因于 resolver：本轮少26条 Mention、1篇失败，period字段从70降至13，样本组成变化显著。尤其 fiscal period 100%必须与 coverage 大幅缩小一起解释。

### 10.1 全部25个 strict Field deviation

| Field | 数量 | 具体 bad case | 判断 |
| --- | ---: | --- | --- |
| predicate | 2 | `report_metric_trend → PREDICATE_REPORT_METRIC`（2次） | 丢失 trend 语义；确定错误 |
| predicate | 1 | `disclose_status → PREDICATE_REPORT_METRIC` | sold-out/status 被压成一般 report；确定错误 |
| predicate | 1 | `secure_supply → constrain_supply` 类 provisional predicate | action 方向/语义偏移；确定错误 |
| predicate | 1 | `trade_volume → PREDICATE_TRADE_MOVE` | volume 行为被压成价格 move；确定错误 |
| participant | 1 | Tech shares → `INSTRUMENT_TECH`（Bio-Techne） | ticker/普通词误消歧；硬错误 |
| participant | 1 | Mizuho → `COMPANY_MFG`（Mizuho Financial） | 研究机构/来源误作上市公司；硬错误 |
| participant | 1 | Benzinga Edge Stock Rankings → unresolved participant | 本应为 source/artifact，namespace 上游错误 |
| participant | 1 | core data center unit → unresolved participant | 本应为 business unit |
| participant | 1 | HBM adoption → unresolved participant | 本应为 concept/cause，不是 actor |
| participant | 1 | development of physical AI → unresolved participant | 本应为 concept/object，不是 actor |
| participant | 1 | average L2+ vehicle → NEW instrument | 泛产品类别误作唯一 instrument |
| participant | 1 | communication services stocks → NEW product | sector/instrument collective 类型错误 |
| participant | 1 | Stoxx 600 → unresolved participant | 应解析为 index/instrument |
| participant | 1 | 16 customer agreements → unresolved participant | agreement count 应进入 quantity/artifact，不是 participant |
| participant | 1 | Magnificent Seven → NEW unknown | 应为 basket/instrument collective |
| metric | 1 | `committed_capital → US_GAAP_INVESTMENTCOMPANYCOMMITTEDCAPITAL` | XBRL 词面误链；硬错误 |
| metric | 1 | `commitment_value → XBRL_CUSTOM_TOTALCOMMITMENTFAIRVALUE` | XBRL 词面误链；硬错误 |
| metric | 1 | `revenue_estimate → REVENUE` | 严格 Gold 差异；estimate 语义可能由 QuantityRole 承载 |
| metric | 1 | `revenue_guidance → REVENUE` | 严格 Gold 差异；guidance 语义可能由 role/state 承载 |
| metric | 1 | `revenue_consensus → REVENUE` | 严格 Gold 差异；consensus 语义可能由 benchmark role 承载 |
| metric | 1 | `prior_year_gaap_gross_margin → GROSS_MARGIN` | 严格 Gold 差异；basis/period 被移到属性后未必是硬错 |
| metric | 1 | `average_trading_volume → TRADING_VOLUME` | 严格 Gold 差异；average qualifier 可能由 role 承载 |
| open attribute | 2 | `Outperform → RATING_BUY`（2次） | provider label 被规范化；是否错误取决于业务是否要求保留原评级档位 |

这里必须区分“确定 runtime 错链”和“新数据模型下的 strict Gold 不一致”。前15条 predicate/participant问题及两个 XBRL metric 链接会改变业务对象或动作，属于确定错误；后5个 base metric 折叠可能是 QuantityRole 设计的预期行为，若 role/state/basis 完整则不应按同等严重度计错。当前 Gold 仍期待独立 metric ID，因此96.73%是 strict 指标，不是已经完成新 contract 口径校准的最终指标。

### 10.2 Field bad case 根因分布与同比

| 根因 | bad case | 上轮关系 | 修复判断 |
| --- | ---: | --- | --- |
| 普通词/ticker/机构名误消歧 | 2 | 上一轮 runtime alias问题复现 | 收紧 lexical alias，加入 namespace/context gate；直接修复 |
| 上游 participant namespace 污染 | 9 | 本轮集中暴露的新问题 | 在 Mention 生成/规范化入口把 source、artifact、concept、business unit、collective 与 actor 分流 |
| predicate 语义压缩 | 5 | 上轮 canonical collision 残留 | 保留 trend/status/volume/action direction 特征后再解析 |
| XBRL 词面近似误链 | 2 | 上轮 metric collision 残留 | 对外部 taxonomy 链接增加 domain/type 一致性；直接修复 |
| base metric + QuantityRole 口径变化 | 5 | 上一轮修复可能引入的评估口径变化 | 先核 role/state/basis 完整性，不能据此整体回滚 QuantityRole |
| provider rating 归一 | 2 | 新口径歧义 | 明确“保留 provider label”或“统一推荐等级”的业务合同 |

`safe deterministic=106/106` 说明确定性路径本身可保留；错误主要集中在开放世界消歧与上游 namespace。`candidate recall@8 proxy=51.81%` 则说明很多正确 canonical 根本没有进入候选集，不能仅靠调整最终 resolver prompt 解决。需要优先修 candidate generation/namespace，再评估 resolver。

## 11. Identity 质量

| 指标 | 上一轮 | 本轮 provisional | 门槛 | 结论 |
| --- | ---: | ---: | ---: | --- |
| Pair precision | 29.80% | 约52.58% | ≥60% | 失败 |
| Judgeable recall | 92.50% | 约85.71% | ≥90.5% | 失败 |
| 最大 cluster | 33 | 12 | 不再形成33-member | 通过 |
| Hard-conflict violations | 664 | 158 | 减少≥80% | -76.20%，失败 |
| Mention-scoped唯一identity造精度 | 无 | 未发现 | 禁止 | 通过 |

本轮 cluster size：

- singleton：102；
- size2：14；
- size3：8；
- size4：4；
- size7/10/12：各1。

最大簇从33降至12，说明 sidecar/边界控制确实抑制了极端 supercluster。但 precision 仍低于60%，recall 从92.5%降至约85.7%，表现为明显保守化。

主要误并：

- size12 agreement 簇混入 Sandisk agreement、HBM sold-out、财务可预测性等；
- size10 Q3 revenue 簇混入 EPS、capex、revenue growth；
- size7 guidance 簇混入 EPS guidance 和泛 AI demand；
- Apple price 与 Apple stock 的不同时间/数值；
- S&P相反方向；
- IDC不同预测；
- Defiance 与 Roundhill 不同 ETF。

Hard-conflict 绝对数下降506，但相对降幅76.2%，未达到80%。当前 guard/invariant 仍以 shadow 为主，`enforced_rules=0`；8次 `shadow_would_lock` 没有实际阻止坏并。

### 11.1 Identity sidecar 与硬边界实际生效情况

199条 `IDENTITY_COMPILED` 的 adapter 分布为：

- `GENERIC_OPEN=91`；
- `MARKET_MOVEMENT=48`；
- `ACTION_ARTIFACT=35`；
- `OUTLOOK_STATE=25`。

除1条缺 referent 外，适用 adapter 的 referent/occurrence 基本有值；91条缺 facet 全部来自 `GENERIC_OPEN`，属于该 adapter 的非适用轴，不应误报为 schema 缺失。说明 sidecar 编译并没有系统性漏字段。

问题发生在“编译后是否用于阻止错误合并”：

| 审计项 | 数量 | 实际效果 |
| --- | ---: | --- |
| `ATOMIC_HARD_CANNOT_LINK_OBSERVED` 候选比较 | 2,661 | 全部 `enforced=false` |
| assertion-state conflict | 1,524 | 只记录，不阻止 |
| normalized-predicate conflict | 979 | 只记录，不阻止 |
| core-subject conflict | 397 | 只记录，不阻止 |
| metric conflict | 374 | 只记录，不阻止 |
| event-time conflict | 290 | 只记录，不阻止 |
| issuer conflict | 2 | 只记录，不阻止 |
| `ATOMIC_MERGE_INVARIANT` | 76 | 68 ALLOW，8 SEMANTIC_REVIEW；`enforced_rules=[]` |
| supercluster guard | 15 | 8次 `shadow_would_lock=true`，实际 lock=0 |

因此“33-member变成12-member”只能证明新特征、候选/决策分布总体更保守，不能证明 hard cannot-link 已经修复了 bad merge。上一轮计划明确采取 shadow-first，本轮看到的是**修复尚未进入 enforcement**，而不是规则已 enforcement 但无效。也不应因本轮结果差就一次性开启全部 hard rule；正确做法是按 Gold 验证后逐条窄启用 metric/assertion/issuer 等高精度规则。

### 11.2 Identity bad cluster 的字段级分布

| cluster size | 主要混入内容 | 冲突字段 | 根因分类 |
| ---: | --- | --- | --- |
| 12 | SCA签署/条款/commitment/sold-out/可预测性，且混入 Sandisk/Micron | issuer、counterparty、artifact、facet | 旧 agreement supercluster 复现；shadow guard未生效 |
| 10 | Q3 revenue、EPS、capex、revenue growth | primary metric、facet | 旧跨 metric supercluster 复现 |
| 7 | revenue guidance、EPS guidance、泛 AI demand | metric、referent/facet | 旧 guidance supercluster 复现 |
| 4 | Apple涨价、iPhone维持不变、相关 statement | action、object、occurrence | 旧 action/object边界残留 |
| 4 | 2028供给改善、HBM紧缺、2027后供给约束 | state、horizon、object | 本轮新 compound/identity问题 |
| 3 | Apple盘中-0.56%与收盘-5%/-6.12% | session、time、value | 旧跨 session问题复现 |
| 3 | GAAP/adjusted margin及 revenue+margin compound | basis、primary metric | 旧 gross-margin问题复现 |
| 3 | FY27与FY26 capex | fiscal period | 本轮新 period误并 |
| 3 | 泛Q3 beat与 net income | facet/primary metric | 旧跨 metric问题复现 |
| 2 | Needham target与泛 analyst changes | source/report identity | 本轮新 source边界问题 |
| 2 | Defiance与Roundhill不同 ETF | institution/instrument | 新开放世界实体错误 |
| 2 | 11.5%与14%不同市场时点 | occurrence/value | 旧时点边界复现 |
| 2 | KOSPI开盘+5%与收盘/全天+5.4% | session | 旧跨 session问题复现 |
| 2 | S&P +0.6%与-0.01% | direction/session/time | 旧相反方向问题复现 |
| 2 | IDC ASP +12%与 RAM 12GB | primary metric/object | 本轮新数字词面误并 |
| 2 | Wedbush target/rating与泛 bullish stance | action facet/source | 本轮新 analyst-report边界问题 |

这16组是29个多成员 cluster 中可明确判断为错误或高风险的组；其余组在当前 claim-conflict 语义下可接受或证据不足，报告不把“多成员”本身当作错误。

## 12. Atomic、N7 与 N9

由于新旧 Mention ID 零重叠，严格同比使用148/199个同文档 embedding 相似度≥0.9的高置信对齐 Mention；全量29个多成员簇另做规则复核。

高置信稳定子集：

| 指标 | 上一轮全量 Gold | 本轮稳定子集 provisional |
| --- | ---: | ---: |
| TP/FP/FN | 400/556/120 | 70/54/109 |
| Pair Precision | 41.84% | 56.45% |
| Pair Recall | 76.92% | 39.11% |
| Pair F1 | 54.20% | 46.20% |

全量多成员簇规则复核得到约96 TP/98 FP，Pair Precision约49.48%。两个口径都表明：precision有所改善，但 recall 和 F1 显著下降，不能判定为整体 Atomic 质量提升。

节点指标：

| 指标 | 上一轮 provisional | 本轮 provisional | 变化 |
| --- | ---: | ---: | ---: |
| N7 Recall@8 | 108/110，98.18% | 61/61，100% | +1.82pp |
| N9 MERGE Precision | 72.31% | 61.82% | -10.49pp |
| N9 conditional MERGE Recall | 87.04% | 55.74% | -31.30pp |

N7 全量非 Gold 指标：

- 199条审计；
- 194/199至少有1个候选，97.49%；
- 161/199至少有8个候选，80.90%；
- candidate pool min/median/max/mean=0/23/56/21.60；
- 稳定 Gold 子集 Recall@8=100%。

这说明主要漏召不在 N7，而在 N9：大量真实同事实被 CREATE_NEW，同时复合 Mention 和相近财务 metric 仍会错误 MERGE。

旧5个最大坏簇全部缩小，但严格完全修复为0/5：

- 33-member财务跨metric → 新size10财务跨metric；
- 17-member agreement → 新size12 agreement污染簇；
- 15-member guidance → 新size7 guidance混合簇；
- 跨session股价仍有碎片与误并；
- GAAP/adjusted gross margin仍有混并。

按旧20种 `REQUIRES_SPLIT` pattern 复核，19种可评，仅5种完全修复，严格修复率26.3%。最大33-member supercluster消失是正向结果，但质量形态变成“明显过拆 + 少数高危复合过并”。

新增/加重的 fragmentation：

- Q3 revenue约11条分散至一个8-member子群和3个 singleton，估算同事实 pair recall约50.9%；
- EPS $25.11约6条分4个 Atomic，pair recall约20%；
- 同一次 after-hours 涨幅至少分成两个3-member簇和一个 compound singleton；
- FY27 capex 分散于一个簇和两个 singleton；
- signed-SCA 一部分在size12污染簇，另一部分成为 `SCA+sold-out capacity` compound singleton。

### 12.1 N7、Judge 与 N9 的逐节点归因

**N7：不是本轮主要漏召根因。** 稳定 Gold 子集 `Recall@8=61/61`，说明只要同事实候选存在，正确 Atomic 已进入前8。更重要的是，本轮业务路径仍使用原有 ranking；新的 v2 ranker 只写入 `ATOMIC_N7_RANKER_SHADOW`。因此不能把本轮业务改善归功于 v2，也不能把 N9 的错误归咎于 v2。N7的风险是全量候选池均值21.60、最大56仍偏大，但这属于成本与判别难度，不是当前 conditional recall 暴跌的直接原因。

**Judge：语义 repair 有检测但没有形成纠正。**

| 文档 | 校验 | repair结果 | 最终后果 | 归因 |
| --- | --- | --- | --- | --- |
| D03 | `GUIDANCE_ASSERTION_CONFLICT` | repair仍非法 | fallback保留 Grounder item，guidance仍标为 ACTUAL | 上轮已知问题复现；repair协议/提示未闭环 |
| D16 | guidance conflict | 成功捕获 | 未扩大为文档失败 | 正向案例 |
| D27 | `GENERIC_UMBRELLA_DUPLICATE` | repair仍非法 | fallback保留 revenue quadrupling+margin compound | 本轮新 compound未被纠正 |
| D05/D09/D14 | duplicate coverage | 各出现重复 draft | 依靠fallback保留合法 item | 覆盖/去重不稳定 |
| D03 | missing d4 | coverage缺口 | 单 draft未进入最终输出 | 已知漏召残留 |
| D10 | missing d2/d4/d5/d6/d7/d8 | 大面积coverage缺口 | 该文档召回上限下降 | 本轮新严重缺口 |

fallback策略实现了“不因单条错误失败整篇”，应保留；但它把未纠正的 compound/assertion错误继续送入 N7/N9，因此是质量降级而非质量修复。

**N9：45个整任务 fallback 是本轮 Atomic fragmentation 的最强直接原因。**

- 199个 assignment task 中，45个触发 `ATOMIC_N9_VALIDATION` 后整体降级为 `N9_INVALID_TASK_CREATE_NEW`；
- 占全部 task 的22.61%，占194个有候选 task 的23.20%；
- 同时存在88次 `NON_APPLICABLE_OR_DUPLICATE_AXES_DROPPED` normalization；
- 最终 action 为 `CREATE_NEW=131`、`MERGE=68`；
- 其中 `N9_RELATED_CREATE_NEW=73`、`N9_CREATE_NEW=53`、`NO_ELIGIBLE=5`。

上一轮只有1个 partial-coverage task，本轮45个 whole-task fallback与 conditional MERGE Recall 从87.04%降至55.74%方向一致。当前 validator 只要某个 candidate assessment 的 axis/target 不合法，就把整个 task（包括其他合法 SAME decision）改成 CREATE_NEW。该行为是**上一轮 tri-axis/校验修复引入的回归**，不是模型自然波动可以解释的量级。

现有审计只记录 fallback 结果，没有保存具体 exception code及非法 candidate/axis，所以无法把45个任务进一步逐条还原成 referent、occurrence或facet哪一轴失败；这本身是需要补齐的审计缺口。

修复判断：**不整体回滚 tri-axis DTO，也不回滚 N7。** 直接将校验/repair粒度缩至非法 assessment：

1. 先归一化多余、重复和 non-applicable axis；
2. 单个 assessment非法时只 repair/丢弃该项，保留同 task 内合法 SAME/DIFFERENT；
3. 只有 target/coverage 整体不可用时才 whole-task CREATE_NEW；
4. 审计落 `error_code + candidate_id + axis`，不落长 reasoning；
5. 对已在 shadow 中验证高精度的 metric/assertion/issuer conflict逐条窄启用，不能一次性全开。

### 12.2 Atomic fragmentation 的字段级 bad case

| 事实组 | 当前分布 | 主要断裂字段 | 三维归因 |
| --- | --- | --- | --- |
| Q3 revenue | 约11条 Mention：一个8-member子群+3 singleton | metric相同但 benchmark/source表述不同 | 旧问题复现，被N9 whole-task CREATE_NEW显著加重 |
| EPS 25.11 | 约6条 Mention分4个 Atomic，pair recall约20% | EPS metric、beat benchmark | 本轮新/加重，主要关联N9 fallback |
| after-hours reaction | 至少两个3-member簇+一个compound singleton | session、price/value、compound market-cap | 旧跨session问题复现，compound拆分仍不完整 |
| FY27 capex | 一个cluster+两个 singleton | period、estimate/guidance role | 本轮新，可能与QuantityRole/严格atomicity边界有关 |
| signed SCA | size12污染簇+一个SCA/sold-out compound singleton | artifact/counterparty/facet | 旧agreement问题复现，shadow guard未阻止 |
| gross margin | 按GAAP/adjusted/value碎裂，同时局部误并 | basis、period、value | 旧问题复现；边界和归并规则同时不稳定 |

这里同时存在“过并”和“过拆”，不能用全局调高或调低 MERGE 阈值解决。应修的是 task fallback 粒度与特定业务边界，而不是整体回滚 Identity sidecar。

## 13. Package、N12 与 N13 质量

旧 Package Gold 投影到当前105/131个可判 Atomic：

| 指标 | 上一轮修正后基线 | 本轮 provisional | 变化 |
| --- | ---: | ---: | ---: |
| TP/FP/FN | 303/28/约109 | 333/81/522 | 分母不同 |
| Pair Precision | 91.54% | 80.43% | -11.11pp |
| Pair Recall | 约73.54% | 38.95% | -34.59pp |
| Pair F1 | 约81.56% | 52.48% | -29.08pp |

Exact-only 80 Atomic 敏感性结果为 P=84.05%、R=38.77%、F1=53.06%，下降方向不变。

约497/522个 Package FN 来自 Micron earnings fragmentation：投影后的41个 earnings Atomic 被拆成约23+12+3+2+1的多个 Package。Package quality 明显低于修正后基线，未通过“Precision/Recall不得下降”的门槛。

N13 conditional 严格可判119/523条：

| 指标 | 上一轮相关子集 | 本轮 |
| --- | ---: | ---: |
| TP/FP/FN/TN | 17/9/9/未列 | 15/1/10/93 |
| Precision | 65.38% | 93.75% |
| Recall | 65.38% | 60.00% |
| F1 | 65.38% | 73.17% |

N13 conditional precision提高，但 recall下降；两轮抽样覆盖不同，不能视作完全同口径 A/B。

Reaction→Earnings：

- 当前1个 Package defect；
- 涉及3个 reaction Atomic、8个 Mention；
- 三个 Atomic 均由 `N13_SAME_PACKAGE_COMPONENT` 移入 earnings Package；
- 另有一个 family 被标为 `COMPANY_DISCLOSURE` 的 Package 混入3个 MARKET_MOVEMENT，未被严格 earnings rule 计数。

因此 reaction→earnings=0 的门槛失败，且根因明确位于 N13 Apply，不是 N12 初始 assignment。

N13门槛汇总：

| 门槛 | 结果 |
| --- | --- |
| deterministic SAME 扩展 Gold FP=0 | 没有启用 deterministic SAME；vacuous通过 |
| reaction→earnings violation=0 | 失败：1 Package/3 Atomic/8 Mention |
| Package Precision不低于基线 | 失败 |
| Package Recall不低于基线 | 失败 |
| 相同 exact profile pair不重复调用 | 通过：0 |
| N13 input至少降低60% | 通过：-77.76% |
| 正确新增join Token成本 | 已报告区间 |

### 13.1 N11 anchor：计划中的父事件锚点没有真正落地

- 61个最终 Package 中，59个 `package_anchor_ids` 为空，1个只有1个，1个有3个；
- 199条 Mention 中仅10条有 `local_package_hint`；
- 这10条又多为 `earnings report disclosure`、`latest quarterly report`、`Micron's blowout quarter` 等泛文本提示，并非稳定 artifact/period canonical anchor。

上一轮计划已明确旧227条 Mention 全部缺 hint，并要求 N11 通过 source/artifact/KB fallback补 parent anchor。本轮 `59/61` 空 anchor 说明该已知问题**仍然复现**：hint字段加入了，但从 Mention hint 到 canonical parent anchor 的 materialize/聚合没有闭环。修复无效的原因不是 hint prompt 太短，而是编排层没有把弱文本提示与 source、artifact、period 编译成可供 N12/N13 使用的稳定键。

不建议扩写 prompt或强迫每条 Mention生成 hint。应在编排层优先组合已存在的 source fingerprint、artifact identity、fiscal/reference period；只有这些都缺失时才保留短 local hint 作为弱特征。

### 13.2 N12：初始 assignment 不是主要失败点，但上游 anchor 缺失放大 CREATE_NEW

- 192次 assignment：`ADD_TO_PACKAGE=114`、`CREATE_NEW_PACKAGE=78`；
- CREATE_NEW 原因：`NO_MEMBER_CREATE_NEW_PACKAGE=71`、`NO_PACKAGE_CANDIDATE=7`；
- 11次 normalization 包含 `MEMBER_RANKING_REBUILT`、`SELECTED_MEMBER_ALIGNED`，未出现节点级失败。

N12正常完成，但在 parent anchor几乎为空时只能依赖 member相似性；71次“无成员则新建”是预期安全行为，却为后续 Micron earnings 大量碎片提供了初始条件。应保留 N12 的非阻塞行为，不应通过激进降低 CREATE_NEW阈值强行合包；先补 anchor，再复测 ranking。

### 13.3 N13：成本优化成功，但三个质量控制点存在直接缺陷

523个 pair decision 中有168个 deterministic DIFFERENT：

- `MIXED_PACKAGE_REQUIRES_SPLIT_OR_REVIEW=153`；
- `DIFFERENT_EXACT_PERIOD_GUARD=15`。

period guard是窄、可解释规则，应保留。`MIXED_PACKAGE_REQUIRES_SPLIT_OR_REVIEW` 在 package已被污染时过于宽泛：它直接阻止语义比较，也阻止后续纠正性重组，导致 earnings碎片继续分裂。不能整体回滚 deterministic guard；应把 mixed package送入 split/review lane，基于清理后的 core profile比较，而不是一律 DIFFERENT。

第二个缺陷是边界修正不具单调性：

- boundary gate最终有6个 FROZEN package；
- 38条 gate审计中29条为 FROZEN；
- gate确实识别出 market/analyst reaction，并请求 `REMOVE_MEMBER/REBUILD/FREEZE`；
- 但两个 boundary-split package 随后又被 N13 SAME plan 合回原 package。

也就是说“先拆错成员、后又重并”，修复动作没有产生 run内 cannot-remerge/tombstone。这是上一轮 boundary repair引入后暴露的**编排回归**。直接修复是在 boundary split时记录 pair-level cannot-remerge（包含原因和 profile version）；只有 profile发生实质变化才允许重评，不需要回滚整个 boundary gate。

第三个缺陷是业务门槛的统计存在 family loophole。严格按 `EARNINGS` family只统计到1个 defect package、3个 reaction Atomic、8个 Mention；另一个 family被标成 `COMPANY_DISCLOSURE` 的 package也混入3个 `MARKET_MOVEMENT` Atomic。按与family无关的语义口径，实际至少是：

- **2个 defect package**；
- **6个 reaction Atomic**；
- **12个 Mention**。

因此原报告中的“1/3/8”是严格 family 指标，不是完整业务风险。后续验收必须使用“成员语义×父包语义”检查，不能只按 package family label过滤。

### 13.4 多成员 Package 的 bad case 分布

| Package形态 | size | 判断 | 冲突字段/根因 |
| --- | ---: | --- | --- |
| Micron earnings主包 | 27 | 基本同一父事件，但仍缺大量兄弟包 | parent artifact/period anchor缺失 |
| `COMPANY_DISCLOSURE`大包 | 18 | 错含3个 market reaction Atomic | package family标签绕过reaction边界；N13 Apply误并 |
| market disclosure包 | 6 | 混合多个市场时段/measure | session、metric、occurrence边界 |
| `EARNINGS`包 | 6 | 错含3个 reaction Atomic | 已确认 N13 SAME Apply缺陷 |
| Sandisk/Citi report | 3 | 大体合理 | 同 analyst artifact/source |
| analyst包 | 3 | 混合 Wedbush、Mizuho及泛估值 | analyst source/report identity |
| SK Hynix opening episode | 3 | 大体合理 | 同市场时段/对象 |
| analyst包 | 3 | 混合 Benzinga、BofA、Needham | analyst source/report identity |
| BofA包 | 2 | 合理 | 同 analyst artifact |
| UBS包 | 2 | 可能合理，证据不足 | source相同但report identity需确认 |
| capital return包 | 2 | 合理 | 同父事件 |
| cloud capacity/price包 | 2 | 错把capacity与price quadrupling合并 | primary metric/facet |
| Sandisk forecast/report包 | 2 | 大体合理 | 同 scheduled report |
| Nasdaq包 | 2 | 错合0.7%与premarket 2.15% | session/value |
| Qualcomm financials | 2 | 大体合理 | 同 disclosure artifact |
| foreign flows | 2 | 合理 | 同市场episode |
| Dow包 | 2 | 错合premarket 0.3%与全天0.5% | session/value |

Package FN 的497/522来自 Micron earnings被投影为约 `23+12+3+2+1` 的多个包，问题高度集中，不是61个包均匀低质。相反，FP主要集中在 reaction、cross-analyst、cross-session及 metric/facet混合。修复应分别处理“共同父锚点缺失导致过拆”和“边界不持久导致过并”，不能用同一个全局阈值。

### 13.5 N13重复调用与成本的质量解释

本轮 exact profile pair重复调用为0，达到去重目标；但相同 package-ID pair在 profile演化后重评235次。它们不是技术意义上的重复 hash，因此不违反当前 exact去重指标，却说明 package反复演化/拆并造成额外请求。后续应单独审计“profile发生了什么实质变化”，只有新增强证据或成员集合变化达到阈值才重评。

N13 token大幅下降是真实收益，但较高 conditional precision建立在更保守的 DIFFERENT与较低 recall上，不能只用 token/precision宣布成功。应保留 profile字典化、字段精简、exact profile dedupe及波次执行；局部调整 mixed guard、boundary cannot-remerge和弱anchor SAME Apply。

## 14. 总验收矩阵

| 层级 | 门槛 | 本轮 | 结论 |
| --- | --- | --- | --- |
| Runtime | 30篇成功 | 29/30 | 失败 |
| Mention | Recall至少+5pp | -14.55pp | 失败 |
| Mention | Precision下降≤1.5pp | -8.45pp | 失败 |
| Mention | compound完整修复≥80% | 33.3% | 失败 |
| Mention | 新fragmentation≤2 | 至少3 | 失败 |
| Mention | candidate coverage=100% | 87.96% | 失败 |
| Evidence | exact/source-equivalent≥99.5% | 98.81% | 失败 |
| Evidence | semantic repair LLM=0 | 0 | 通过 |
| Evidence | 异常不造成文档失败 | 造成1篇失败 | 失败 |
| Field | predicate≥90% | 97.49% | 通过 |
| Field | participant≥96% | 95.40% | 失败 |
| Field | metric≥90% | 96.73% | 通过 |
| Field | fiscal period≥80% | 100% | 通过但coverage缩小 |
| Field | total≥91% | 96.40% | 通过 |
| Field | candidate recall@8≥98% | 51.81% proxy | 失败 |
| Field | deterministic precision≥99% | 100% provisional | 通过 |
| Field | runtime alias新增=0 | 7 | 失败 |
| Field | 不靠增加UNRESOLVED | 52→24 | 通过 |
| Identity | pair precision≥60% | 52.58% | 失败 |
| Identity | judgeable recall≥90.5% | 85.71% | 失败 |
| Identity | 33-member簇消失 | 最大12 | 通过 |
| Identity | hard conflict减少≥80% | -76.20% | 失败 |
| N13 | input减少≥60% | -77.76% | 通过 |
| N13 | total share约15%–30% | 19.28% | 通过 |
| N13 | reaction→earnings=0 | 严格family口径1包；语义口径至少2包/6 Atomic/12 Mention | 失败 |
| Package | P/R不低于基线 | P/R均下降 | 失败 |

## 15. 回滚与保留判断

### 15.1 不应整体回滚

以下优化有明确、可独立验证的收益：

- N13 pair减少70.42%；
- N13 input减少77.76%；
- exact profile pair重复=0；
- deterministic DIFFERENT 在进入LLM前截断168 pair，其中15个 exact-period guard可保留，153个 mixed guard需改造；
- N13 aggregate latency减少78.45%；
- 极端33-member Atomic supercluster消失；
- Field predicate、metric与period映射显著改善。

整体回滚会丢失已验证的降本和部分精度收益。

### 15.2 必须局部回滚或禁用

在修复并复验前，应禁用以下 N13 SAME 自动 Apply：

- reaction ↔ earnings；
- mixed family；
- 跨 analyst-source；
- 缺 artifact/period/parent anchor 的弱 anchor pair；
- profile发生变化后反复重评、但没有新增强证据的 package-ID pair。

这些 pair 可保留候选与审计，但暂时输出 DIFFERENT/REVIEW_REQUIRED，不应自动合包。

### 15.3 P0修复

1. Dreamer repair JSON-safe error：
   - Pydantic errors 进入 repair prompt 前只保留 `loc/type/msg/code`；
   - 对 `ctx` 中的异常对象转字符串或删除；
   - 增加带 `value_error` context 的回归测试；
   - 确保单 block repair 失败降级为空 block，不扩大为文档失败。

2. Grounder item repair 零恢复：
   - 复核26个 `mention.value_error` 的共同触发条件；
   - repair prompt/schema 应返回真正可通过语义校验的单 draft；
   - 首个同类失败后不要继续无差别消耗26次请求；
   - 以“恢复合法 draft 数/repair数”作为节点门槛，不能只看模型请求成功。

3. Mention recall：
   - disposition missing 39必须降至0或有明确 rejected reason；
   - 修复 qualifier/benchmark 丢失；
   - compound拆分不得以丢失其余Gold为代价；
   - 同一Gold的残缺 fragments不得冒充 atomicity提升。

4. N9：
   - 保留 N7 recall；
   - 将45个 whole-task validation fallback改成非法 assessment 级处理，保留同 task 合法决定；
   - 审计落 `error_code/candidate_id/axis`，再针对同事实的跨文档重复降低无依据 CREATE_NEW；
   - 对财务 metric、assertion、period 和 session 的强边界从 shadow推进到可审计的窄 enforce；
   - 不应一次性启用所有 hard-conflict规则，避免假阳性硬阻塞。

5. Package：
   - 对 Micron earnings 的23+12+3+2+1碎片建立 parent artifact/period anchor；
   - reaction/earnings边界先 deterministic fail-closed；
   - boundary split生成run内 cannot-remerge，避免同轮被 N13 SAME重新合回；
   - N13 SAME Apply 必须记录使用的强 anchor，不接受仅主题/实体相似。

## 16. 初步处置判断

本轮优化实现了真实的 N13 降本，但没有实现端到端质量提升，且总运行时长上升。Mention、Identity recall、Atomic/Package fragmentation 和文档韧性均出现不可接受的问题。

最终处置为：

- **效能优化：部分接受。**
- **业务语义质量：不接受。**
- **整轮代码：不整体回滚。**
- **N13弱边界自动Apply：局部回滚/禁用。**
- **Dreamer/Grounder repair：P0修复后必须重跑同一30篇。**

本报告是评估结论，不包含任何修复代码实施。

## 17. 三维相关性归因总表

本节以本轮 Registry/trace、上一轮验收结果、上一轮 Mention/Evidence/Field/Identity 最终实施计划、Atomic/N7/N9方案和 Package/N10–N13方案交叉核对。详细实例已列在8.2、9.1、10.1、11.2、12.2和13.4，本节不重复每条文本，只给出归因闭环。

### 17.1 第一类：上一轮存在、本轮复现

| 节点/问题 | 上一轮修复动作 | 本轮复现证据 | 为什么修复无效 | 结论 |
| --- | --- | --- | --- | --- |
| Grounder compound拆分后漏信息 | 引入atomicity、单条repair、disposition审计 | 39 missing；D03仍compound；D04虽拆分但漏investment；D08/D12新增碎片 | repair看不到根级业务错误；拆分门槛只约束结构，没有同时约束Gold信息守恒 | 保留单条并行repair，补错误协议和信息守恒检查 |
| Guidance assertion冲突 | Judge语义校验+一次repair+合法draft fallback | D03 repair后仍冲突，最终保留ACTUAL | repair输入没有让模型明确理解业务非法组合；fallback只保运行不中断 | 直接修repair协议；保留fallback |
| Evidence exact定位 | exact/anchor/source-equivalent materialize，不用semantic LLM | `TEXT_NOT_FOUND` 16→4，D19仍复现 | 确定性对齐覆盖扩大但无法处理源HTML差异/跨句合成 | 修复有效但未完全；保留并补deterministic span候选 |
| Participant/runtime alias | namespace+registry resolver优化 | Tech、Mizuho仍错误；另有9类participant namespace污染 | 开放世界词面消歧和上游类型错误未被候选层解决 | 不回滚resolver；先修namespace和candidate generation |
| Metric/predicate collision | canonical mapping和QuantityRole优化 | 2个XBRL错链、5个predicate语义压缩 | 词面相似仍压过domain/type；trend/status/action direction未进强特征 | 窄修候选与domain gate |
| Agreement supercluster | sidecar、hard cannot-link shadow、supercluster guard | 17-member缩为12-member但仍混issuer/artifact/facet | 关键guard全部shadow，未实际阻止 | 不是“规则失效”，是“尚未enforce”；按Gold窄启用 |
| Financial cross-metric supercluster | tri-axis identity、metric conflict shadow | 33-member缩为size10，仍混revenue/EPS/capex | metric conflict记录374次但`enforced=false` | 窄启用高精度metric边界 |
| Guidance supercluster | outlook adapter、assertion/metric sidecar | 15-member缩为size7，仍混revenue/EPS/AI demand | 同上，且上游Mention仍有compound/assertion错误 | 上游修复+窄边界，不能只调N9阈值 |
| Cross-session market move | occurrence/session特征与shadow guard | Apple、KOSPI、S&P、Nasdaq、Dow仍跨session/方向混合 | occurrence字段存在但没有形成稳定cannot-link；部分Mention本身compound | 窄启用session/direction规则 |
| GAAP/adjusted margin | basis/role侧车 | 仍有误并与碎裂并存 | basis进入特征不稳定，N9又有whole-task fallback | 修basis contract与N9粒度 |
| Package parent anchor | 增加`local_package_hint`并规划source/artifact/period fallback | 59/61 Package无anchor；Micron earnings碎成23+12+3+2+1 | 只新增弱hint字段，未materialize为canonical parent key | 编排层补anchor compiler，不扩prompt |
| Reaction→earnings | boundary gate、split/rebuild/freeze | 语义口径至少2包/6 Atomic/12 Mention | gate能发现但拆后可被N13 SAME重并；family过滤还漏计 | 加cannot-remerge并改语义口径 |

这一类的总体判断是：大部分修复方向没有错，但处于“只记录、不执行”“字段已生成、未编译成下游键”或“单条repair拿不到业务错误”三种未闭环状态。整体回滚不能解决，应该把现有机制闭环。

### 17.2 第二类：上一轮修复引入或明显放大的问题

| 节点/问题 | 因果证据 | 直接原因 | 能否直接修 | 是否需回滚 |
| --- | --- | --- | --- | --- |
| D02整篇失败 | 模型已返回；进入新 block repair 后本地 `json.dumps` 抛 TypeError | Pydantic `errors().ctx` 内exception对象不可JSON序列化 | 能：只保留JSON-safe `loc/type/msg/code`，block失败降级为空 | 不回滚repair架构 |
| Grounder repair 26/26零恢复 | 26次均是同一 `mention.value_error`，模型请求成功但输出仍非法 | 新单条repair协议没有传根级业务错误 | 能：短错误码、安全msg、同类零收益熔断 | 不回滚单条/并行repair |
| N9 whole-task CREATE_NEW | 45/199 task触发，上一轮仅1个partial coverage；conditional recall下降31.30pp | tri-axis validator把单项非法扩大成整个task fallback | 能：assessment级normalize/repair/drop，保留合法决定 | 不回滚tri-axis DTO；在修复前可临时禁用whole-task fallback |
| QuantityRole strict Gold偏差 | 5个base metric与旧Gold不同 | estimate/guidance/consensus/basis/average移出metric ID | 先核role/state/basis是否完整，再更新Gold或修字段 | 证据不足，禁止整体回滚 |
| 更严格atomicity造成新碎片 | D08、D12、FY27 capex等同Gold被拆成残缺Mention/Atomic | 结构拆分没有信息守恒/重组条件 | 能：以Gold语义单元和qualifier守恒约束拆分 | 不回滚atomicity目标 |
| mixed package deterministic DIFFERENT过宽 | 153/168 deterministic DIFFERENT来自mixed guard，Package recall显著下降 | 污染包直接判DIFFERENT，无法进入纠错比较 | 能：改split/review lane，比较清理后core profile | 仅局部回滚该宽规则的自动DIFFERENT |
| boundary split后重并 | gate已REMOVE/REBUILD/FREEZE，随后相同边界又被SAME Apply | 缺run内cannot-remerge/tombstone | 能：持久化边界原因和profile version | 不回滚boundary gate；临时禁用相关SAME Apply |
| profile演化重复评估 | exact hash重复=0，但package-ID pair重评235次 | 任何profile变化都可触发重评，缺“实质变化”门槛 | 能：审计diff，仅强证据/成员实变重评 | 不回滚exact dedupe |

第二类中只有两项建议“局部回滚/临时禁用”：过宽的 `MIXED_PACKAGE_REQUIRES_SPLIT_OR_REVIEW → DIFFERENT` 自动结论，以及缺强anchor/违反boundary的 N13 SAME自动Apply。其余均是局部实现缺陷，整体回滚会丢失 N13降本、Evidence定位和sidecar审计收益。

### 17.3 第三类：本轮新暴露的问题

| 层级 | 新问题 | 具体 bad case/分布 | 根因位置 | 建议 |
| --- | --- | --- | --- | --- |
| Mention | 新compound | D26 price loss+market cap；D27 revenue growth+margin；D29 DRAM/NAND不同horizon | Dreamer/Grounder atomicity与Judge coverage | 增加信息守恒的单draft校验，不增加整篇阻塞 |
| Mention | 新fragmentation | D08两组、D12两组及FY27 capex等 | 拆分策略+N9 fallback | 同一Gold的残缺片段不能算atomicity成功 |
| Judge | 大面积coverage缺口 | D10缺d2/d4/d5/d6/d7/d8 | Judge task coverage/去重 | 记录每个draft最终处置；只repair缺失/非法项 |
| Evidence | 新paraphrase | D25两条 | 生成span不忠实源文 | deterministic span对齐或生成侧短约束，不启semantic LLM |
| Field | participant开放世界类型污染 | source/artifact/concept/business unit/product class/sector/index/agreement count/basket共9类 | Mention namespace与candidate generation | 先做类型分流，再交resolver |
| Identity | 新对象/来源边界 | Defiance vs Roundhill、IDC ASP vs RAM、Needham/Wedbush report边界 | referent/facet/source identity | 添加窄、可解释的对象/source边界 |
| Atomic | EPS/Capex等严重碎裂 | EPS 6条分4 Atomic；FY27 capex分3处 | N9 whole-task fallback与role/basis特征 | assessment级修复；审计具体axis |
| Package | N11 anchor闭环失败被量化 | 59/61零anchor | hint→canonical anchor materialize | 编排层修复 |
| Package | family标签绕过业务检查 | `COMPANY_DISCLOSURE`包混3个MARKET_MOVEMENT | boundary metric实现 | 改为成员语义×父包语义 |
| Audit | 无法还原某些逐条根因 | 39 missing缺最终reason；45 N9 fallback缺exception/axis | decision audit payload | 增短code/id/axis，不加长reasoning |

这些“新问题”中有一部分并非代码本轮才产生，而是以前的评估没有按字段/语义口径观测到。报告将其归为“本轮首次暴露”，不把时间相关性伪装成已证明的代码因果。

## 18. 修复优先级、回滚判断与审计边界

### 18.1 P0：先恢复运行韧性和召回，再谈阈值

1. 修 D02 repair错误序列化，确保 Evidence/schema单项异常不扩大成文档失败；
2. Grounder repair传短业务错误码并增加零收益熔断，使39个 missing全部得到 used/rejected/failed-repair disposition；
3. N9从 whole-task fallback改为 assessment级处理，同时补 `candidate_id/axis/error_code` 审计；
4. boundary split生成run内 cannot-remerge，临时禁止 reaction、cross-analyst、弱anchor pair的自动SAME Apply。

这四项都是直接、局部修复，不要求整体重构，也不应改变正常合法项的主业务路径。

### 18.2 P1：修业务边界，不做全局阈值摆动

1. 以 source fingerprint、artifact identity、reference/fiscal period 编译 N11 parent anchor；
2. 从 Gold验证过的 metric、assertion、issuer、session规则中逐条窄enforce；
3. 把 participant 的 source/artifact/concept/business unit/collective 类型在resolver前分流；
4. mixed package进入split/review lane，而不是一律deterministic DIFFERENT；
5. 对 QuantityRole的5个strict metric deviation核验 role/state/basis 后再决定修实现还是更新Gold。

### 18.3 明确不建议的动作

- 不整体回滚 N10–N13优化：input -77.76%、exact profile dedupe和波次执行是已验证收益；
- 不整体回滚 tri-axis/sidecar：问题在validator fallback粒度和shadow未enforce；
- 不提高全局 MERGE 或 CREATE_NEW阈值：当前过并、过拆同时存在；
- 不恢复整篇串行repair，也不增加semantic Evidence repair LLM；
- 不靠扩写长prompt补parent anchor，anchor应主要在编排层形成；
- 不一次性开启全部hard conflict，否则可能把当前过拆进一步放大。

### 18.4 本报告可以与不可以证明什么

本报告完成了现有产物允许的最细粒度归因：列出全部39个 missing candidate、全部4条 Evidence violation、全部25个 strict Field deviation、全部明确错误/高风险多成员 Atomic cluster和多成员 Package，并把它们映射到上一轮问题、上一轮修复和本轮新暴露三类。

但以下两类无法从现有审计可靠下钻，不能伪造确定结论：

- 39个 missing 可以从 `dream_candidates` 恢复候选内容，但没有保存最终被丢draft的业务 `reason/code`，因此只能定位其业务字段，不能还原触发repair二次失败的具体根级约束；
- 45个 N9 fallback没有保存异常 candidate与axis，能确定whole-task扩大失败，却不能逐条断言是 referent、occurrence还是facet非法。

这两个审计字段只需增加短枚举与ID，对token payload影响接近零，却是下一轮判断修复是否有效所必需的留痕。

综合判断仍为：**本轮不能通过业务质量验收；不整体回滚；先做P0局部修复，再用同一30篇、同一Gold、同一字段级 Registry重跑。**
