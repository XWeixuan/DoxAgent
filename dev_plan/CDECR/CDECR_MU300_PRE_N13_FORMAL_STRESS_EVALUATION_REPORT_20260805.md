# CDECR 300 篇 MU 新闻中断态正式压力测试评估

> 评估日期：2026-08-05  
> 冻结运行：`D:\DoxAgent_CDECR_Stress_20260804_MU300_R1`  
> 口径：用户要求中断后冻结的 **N12 + Wave C / N13 前状态**  
> 重要限制：本报告不是完整端到端验收；N13 Decide、后置修复、finalize 与幂等复跑均未完成。

## 1. 结论

1. **中断操作安全，冻结数据可评估。** 仅终止指定 MU300 进程树；SQLite `integrity_check` 与 `quick_check` 均为 `ok`，300 篇单文档全部成功，冻结库可重复读取。
2. **完整工作流没有成功完成。** 单文档成功率是 300/300（100%），但 bulk epoch 与 cross-document run 仍为 `RUNNING/N13_DECIDE`；不能把本轮宣称为 300/300 端到端成功。
3. **N13 超长耗时不正常，而且尚未发生 N13 模型成本。** N13 pair-plan 之后约 11 小时 32 分钟都消耗在本地全量 pair 扫描；`package_pair_evaluations=0`，没有 N13 LLM Decide。当前实现对 2,467 个 touched Package 近似扫描约 608 万对，候选上限在全量扫描之后才生效，并在 pair 内反复读取 SQLite。600 pair 实测均值 16.27ms，完整串行扫描预计约 27–28 小时。
4. **本轮生产调用 Token 很高。** 12,527 次模型调用消耗 Input 19,804,124、Output 13,504,733，总计 33,308,857 Token；平均每篇约 111,030 Token。Atomic、Judge、Grounder 合计占总 Token 56.00%。
5. **Mention Recall 明确不达标。** 对 2,495 条 Gold，严格 Recall 51.86%，Partial-aware Recall 74.91%，都低于 85% 目标。1201 个严格漏召中，`OUTPUT_PARTIAL` 占 60.45%，`DREAMER_MISSING` 占 35.55%。
6. **Mention Precision 的现有 Gold 口径存在重大缺陷，不能把 36.23% 当作业务真值。** 1,703 个“FP”中至少 1,103 个只是“真实但 Gold 未收录的其他事件”。例如 D46 是市场综述，系统抽出 Bund yield、Galaxy Digital、Empire survey 等真实事件，但 Gold 只保留 1 条。Gold manifest 声称覆盖所有 material explicit events，与实际标注不一致。
7. **Field 本体质量明显好于端到端分数。** 端到端 total 仅 67.61%，但在已匹配 Mention 上 total 为 93.48%；predicate 95.95%、metric 94.76%、fiscal period 82.61% 达门槛，participant 94.35% 略低于 96%。主要损失来自上游 Mention 未召回，而不是 Field 节点整体失效。
8. **Atomic 聚类确认严重失衡。** 在 Partial-aware 的 1,815 条可对齐 Mention 上，Pair P/R/F1 为 35.48% / 23.67% / 28.39%；76 个 Gold Atomic 被拆分，excess components 137。最大实际 Atomic 有 26 条 Mention，却对应 13 个 Gold Atomic。
9. **Package 冻结态同样不可接受，但 Gold Package 边界仍需二次校准。** Partial-aware end-to-end Pair P/R/F1 为 7.71% / 42.98% / 13.08%；91 个 Gold Package 被拆分。最大实际 Package 含 151 个 Atomic，对应 54 个 Gold Package。与此同时，Gold 将同一披露父事件内的若干子指标拆成不同 Package，故绝对 Package 指标偏保守；151-Atomic 巨簇和大量 singleton 并存则不依赖该争议，属于确定问题。
10. **不应从中断点恢复当前 N13 实现。** 在修复 pair-plan 的候选生成复杂度和 checkpoint 之前，继续执行只会再消耗约 10–20 小时本地扫描，且不能修复已经在 N12/Wave C 形成的 151-Atomic 巨簇。

## 2. 测试集与冻结完整性

| 项目 | 结果 |
| --- | ---: |
| 原始候选 | 3,600 |
| reader accepted | 1,000 |
| eligible | 985 |
| 最终冻结文档 | 300 |
| 来源数 | 25 |
| 唯一 document fingerprint | 300 |
| 正文字符 | min 1,059 / p50 3,305 / p95 8,102 / max 45,210 |
| Gold Mention / Atomic / Package | 2,495 / 2,173 / 1,739 |
| Gold 文档覆盖 | 300/300 |

两条 150k/205k 字符的页面拼接污染记录在开跑前已替换；测试集中不是 summary-only 数据。

中断证据：

- 启动：2026-08-04 19:11:02 +08:00；
- 中断：2026-08-05 16:20:22 +08:00；
- 精确终止 PID：169552、171396、168816、171372；残留 PID 为 0；
- 原库中断时 SHA256：`01ae372c...`；
- 评估冻结副本：`pre_n13_frozen.sqlite3`，SHA256：`e58f2e8...`；
- SQLite 完整性：`integrity_check=ok`、`quick_check=ok`。

## 3. 成功率与局部失败

| 层级 | 成功 | 失败/未完成 | 判断 |
| --- | ---: | ---: | --- |
| 单文档 | 300/300 | 0 | 100% |
| Field bulk task | 8,043/8,043 | 0 | 100% |
| N9 | 3,571/3,572 | 1 | 99.97% |
| N9 late | 1/1 | 0 | 100% |
| N12 A | 3,129/3,129 | 0 | 100% |
| N12 B | 3,096/3,129 | 33 | 98.95% |
| N12 C | 1/1 | 0 | 100% |
| 全部 bulk task | 17,841/17,875 | 34 | 99.81% |
| N13 Decide | 0 | 未开始模型请求 | 未完成 |
| Bulk finalize | 0/1 | 用户中断 | 未完成 |

34 个失败均为局部 `UNJUDGEABLE_FAILED`，没有扩大为文档失败：

- N9：1 条 Mention task；
- N12 B：33 条 Atomic task；其中 19 条最终处于 singleton Package，说明局部降级仍产生了可观察的碎片化代价；
- 生产 `model_calls` 有 692 次失败尝试（5.52%），但绝大部分由既有 retry/repair 吸收。最大项是 Field 的 477 次 `provider_invalid_request_error`；数据库未保留 provider 原始响应文本，无法再做更细的服务端错误分类。

## 4. 墙钟耗时

总墙钟约 21 小时 09 分钟。

| 阶段 | 时间区间 | 墙钟 | 总墙钟占比 |
| --- | --- | ---: | ---: |
| 启动、单文档收尾与 manifest | 19:11–19:22 | 约 11m | 0.9% |
| Field plan/execute/overlay | 19:22–次日 01:11 | 约 5h49m | 27.5% |
| Atomic plan/N9/late/partition | 01:11–02:45 | 约 1h34m | 7.4% |
| N12 + Wave C + Package partition | 02:45–04:47 | 约 2h02m | 9.6% |
| N13 本地 pair scan | 04:47–16:20 | 约 11h32m | 54.5% |

### N13 根因

- touched Package：2,467；
- `FULL_BOUNDED_PAIR_PLAN`，候选 cap=6、batch_size=2；
- 当前实现先做 touched × active 的全量信号计算，再保留 top candidates；
- 近似候选比较：2,467² ≈ 608 万；
- `seen_pairs` 在选出 top candidates 后才登记，无法在主扫描中有效消除对称 pair；
- 每个 pair 重复物化 Atomic/Mention/Field、source/parent boundary；
- 阶段内部没有可恢复 checkpoint。

因此这不是 provider 并发不足，也不是 N13 LLM 慢；是候选召回顺序和本地 I/O 复杂度错误。

## 5. 生产 Token 与模型耗时

| 指标 | 数值 |
| --- | ---: |
| 模型调用 | 12,527 |
| Input Token | 19,804,124 |
| Output Token | 13,504,733 |
| Total Token | 33,308,857 |
| 累计 model latency | 102,896,826ms（约 28.58h，并发累计值） |
| 平均每篇 Total Token | 约 111,030 |

主要节点：

| 节点 | Calls | Input | Output | Total Token 占比 | model latency 占比 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Atomic coreference | 904 | 4,121,826 | 3,610,587 | 23.21% | 28.28% |
| Judge | 317 | 2,526,042 | 3,514,396 | 18.13% | 21.55% |
| Grounder | 295 | 1,599,389 | 3,283,013 | 14.66% | 18.78% |
| Field coreference | 3,973 | 4,127,724 | 271,914 | 13.21% | 8.14% |
| Package assignment | 79 | 1,782,847 | 737,989 | 7.57% | 5.82% |
| Grounder item repair | 327 | 1,689,133 | 433,651 | 6.37% | 2.68% |
| Atomic escalation | 85 | 402,390 | 537,121 | 2.82% | 4.26% |
| Dreamer | 301 | 501,750 | 354,715 | 2.57% | 1.70% |
| 其他 | 6,343 | 3,053,023 | 761,347 | 11.45% | 8.79% |

M1/M2/M3/M4 分布：

| Tier | Calls | Input | Output | Failed |
| --- | ---: | ---: | ---: | ---: |
| M1 | 6,010 | 1,982,521 | 0 | 0 |
| M2 | 5,279 | 9,267,073 | 4,408,544 | 552 |
| M3 | 895 | 5,870,569 | 5,437,865 | 125 |
| M4 | 343 | 2,683,961 | 3,658,324 | 15 |

N13 本轮 Input/Output Token 均为 0；其问题是 11.5 小时本地 planner 空转。

Mention/Field Gold 复判另行消耗 Input 4,098,093、Output 5,645,377，仅属于本报告评估成本，未混入上述生产 Token。

## 6. Evidence

| 指标 | 结果 | 门槛 | 判断 |
| --- | ---: | ---: | --- |
| Evidence records | 3,740 | — | — |
| VERIFIED | 3,731 | — | — |
| exact/source-equivalent | 99.76% | ≥99.5% | 通过 |
| TEXT_NOT_FOUND | 9 | — | 局部保留 |
| Evidence 异常造成文档失败 | 0 | 0 | 通过 |

9 条 `TEXT_NOT_FOUND` 没有触发整文失败，符合非阻塞设计。

## 7. Mention

### 7.1 直接 Gold 对齐

| 指标 | 数值 |
| --- | ---: |
| Gold | 2,495 |
| 输出 Mention | 3,572 |
| Strict TP | 1,294 |
| Partial | 575 |
| Gold 严格 FN | 1,201 |
| 标为 FP | 1,703 |
| Strict P/R/F1 | 36.23% / 51.86% / 42.66% |
| Partial-aware coverage | 74.91% |
| Partial-aware observed precision | 52.32% |
| strict TP=0 的文档 | 50/300 |
| partial-aware TP=0 的文档 | 24/300 |

### 7.2 Recall 根因

| first failure | 数量 | FN 占比 |
| --- | ---: | ---: |
| OUTPUT_PARTIAL | 726 | 60.45% |
| DREAMER_MISSING | 427 | 35.55% |
| JUDGE_REJECTED_OR_MERGED | 46 | 3.83% |
| GROUNDER_MISSING_OR_INVALID | 1 | 0.08% |
| UNKNOWN | 1 | 0.08% |

结论：优先级应是“复合/不完整 Mention 的原子边界修复”与 Dreamer 定向漏召；不应继续无差别放大输出量，因为当前输出已经是 Gold 的 1.43 倍。

### 7.3 Precision Gold 缺口

当前 36.23% 不能作为业务 Precision：

- 1,703 个 FP 中，至少 1,103 个理由明确是 `different event / not in Gold / unrelated`，并非无事实依据；
- 只有约 180 个理由明确包含 background、unsupported、valuation、opinion 等真实精度问题；
- D46：Gold 1、输出 48，其中 47 个被判 FP，但 Bund yield、Galaxy Digital 股价、Empire survey 都是来源中的真实事件；
- D54：Gold 1、输出 40，Gold 未覆盖同文档其他公司与市场事实；
- D98：Gold 0、输出 23，实际是整篇非 MU 中心事件被 Gold 全部排除。

因此本轮可确认 **Recall 不达标**；Precision 只能给出受 Gold 完整性影响的 observed 值，不能据此回滚 Mention 模型。必须先明确 CDECR 是“全文开放世界抽取”还是“只抽 MU 相关事实”，再补 Gold 或加目标过滤。

## 8. Field

| Field | 端到端 Accuracy | 已匹配 Mention 条件 Accuracy | 目标 | 判断 |
| --- | ---: | ---: | ---: | --- |
| predicate | 70.26% | 95.95% | ≥90% | 条件通过 |
| participant | 69.54% | 94.35% | ≥96% | 略低 |
| metric | 69.82% | 94.76% | ≥90% | 条件通过 |
| fiscal period | 53.68% | 82.61% | ≥80% | 条件通过 |
| total | 67.61% | 93.48% | ≥91% | 条件通过 |

已匹配 Mention 的主要错误：missing/mismatched fiscal period、missing participant、predicate mismatch、missing metric。Field 本体不是当前总质量的首要瓶颈；先修 Mention Recall 的收益更大。

## 9. Atomic / Identity

评分仅使用 Mention evaluator 已对齐到 Gold 的记录，并解析当前 Atomic head；因此是 **Mention 条件下的 pair 指标**。

| 口径 | 对齐 Mention | Pair P | Pair R | F1 | fragmented Gold | excess components | missed links |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Strict-only | 1,285 | 36.29% | 23.26% | 28.35% | 48 | 89 | 297 |
| Partial-aware | 1,815 | 35.48% | 23.67% | 28.39% | 76 | 137 | 516 |

结构状态：

- 当前 Atomic：3,134；
- singleton：2,884（92.02%）；
- multi-Mention：250；
- 最大 cluster：26 Mention。

典型确认问题：

- 最大 Atomic `atomic:ec19...` 的 canonical proposition 是“Micron stock rose 744% over the past 52 weeks”，26 条 Mention 中可对齐的 13 条分别属于 13 个 Gold Atomic；不同来源日期对应不同滚动窗口，被错误合并。
- 同时存在严重过拆：Gold Atomic 的 Partial-aware fragmented groups=76，若干 10–15 member Gold Atomic 被拆成 6–11 个组件。

这说明 N9/Identity 不是单向“过于保守”，而是时间窗口/occurrence 边界误并与候选召回不足同时存在。

## 10. Package / 碎片化

### 10.1 冻结结构

| 指标 | 数值 |
| --- | ---: |
| active Package | 2,467 |
| singleton Package | 2,205（89.38%） |
| multi-Atomic Package | 262 |
| 最大 Package | 151 Atomic |
| N13 Apply redirect | 0（N13 未运行） |

### 10.2 Gold pair 指标

| 口径 | Pair P | Pair R | F1 | fragmented Gold | excess components | singleton components | missed links |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Strict end-to-end | 7.14% | 43.67% | 12.28% | 58 | 104 | 135 | 832 |
| Partial-aware end-to-end | 7.71% | 42.98% | 13.08% | 91 | 163 | 213 | 1,861 |
| Strict conditional on Gold Atomic | 9.14% | 28.46% | 13.84% | — | — | — | 533 |
| Partial-aware conditional on Gold Atomic | 9.61% | 30.10% | 14.57% | — | — | — | 1,147 |

最大实际 Package `package:a48f...`：

- 151 个 Atomic；
- 186 条 Partial-aware 对齐 Mention；
- 横跨 54 个 Gold Package；
- family 为 `EARNINGS_DISCLOSURE`；
- `anchor_conflict=true`，同时累积 30 个 anchor ID；
- summary 混合 Micron FYQ3 实绩、FYQ4 guidance 与其他披露。

这证明错误大簇在 N12/Wave C 已形成，不能归咎于尚未运行的 N13。

### 10.3 Gold Package 边界警告

Gold Package 虽经过 797 个 package review task，但仍发现与当前业务定义冲突的样例：D01 同一 Micron Q3 guidance 父披露中的 revenue、gross margin、gross-margin change 被分到不同 Gold Package。若 Package 定义是“承载多个 Atomic 的父 occurrence/artifact”，这会系统性压低 Package Precision。

因此：

- 151-Atomic 巨簇、89.38% singleton、跨 54 个 Gold Package，属于确定异常；
- 上表绝对 P/R 暂作为保守诊断值；
- 在作为正式发布门槛前，应对高频 earnings/report 父事件做一次 Gold Package 边界复核。

## 11. 是否继续、是否可用当前结果

### 可以使用

- 300 篇单文档输出、Mention、Field、Atomic 与 N12/Wave C Package 冻结态；
- Evidence 成功率；
- Token、模型 latency、局部失败与 N13 planner 性能诊断；
- Gold target Recall、Field 条件准确率、已对齐 Gold 内的 Atomic/Package pair bad case。

### 不可以宣称

- 300/300 完整端到端成功；
- N13 质量、N13 Token 或 N13 Apply 效果；
- finalize/idempotency 通过；
- Mention 业务 Precision=36.23%；
- 未复核 Package Gold 前的 Package 指标是最终发布真值。

## 12. 下一步建议

1. **先修 N13 planner，禁止恢复当前扫描。** 候选索引必须先按 issuer/artifact/anchor/family/period/session 等分桶，再做 top-K；在 DTO、SQLite 深读之前剪枝，对称 pair 在生成时去重，并按固定 chunk 持久 checkpoint。
2. **先确定 Mention 抽取范围。** 若是全文开放世界抽取，补齐 roundup/多公司文章 Gold；若只服务 MU，则把 target relevance 变成明确业务契约与前置过滤，而不是在评估时把真实事件算 FP。
3. **Mention Recall 做窄修。** 优先处理 `OUTPUT_PARTIAL` 的 compound/fragment 边界和 427 条 Dreamer missing；禁止以继续增大候选量换 Recall。
4. **Field 暂不整体重构。** 仅针对 participant 缺实体/角色与 fiscal period 缺失做局部修正。
5. **Atomic 同时治理误并与过拆。** 滚动时间窗口、报告日期、market session、analyst report occurrence 必须进入 identity；召回侧再检查同一 metric/period/issuer 的 candidate coverage。
6. **N12/Wave C 先处理 anchor conflict 大簇。** `anchor_conflict=true` 且存在多个不兼容 anchor 时不得继续凭宽 family/issuer 合并；同时避免退回全 singleton，应按 canonical parent occurrence 分组件。

## 13. 评估产物

- 冻结库：`D:\DoxAgent_CDECR_Stress_20260804_MU300_R1\pre_n13_frozen.sqlite3`
- 中断记录：`D:\DoxAgent_CDECR_Stress_20260804_MU300_R1\interruption.json`
- Mention 评估：`D:\DoxAgent_CDECR_Stress_20260804_MU300_R1\mention_gold_eval.json`
- Field 评估：`D:\DoxAgent_CDECR_Stress_20260804_MU300_R1\field_gold_eval.json`
- Strict hierarchy mapping/metrics：`hierarchy_mapping_strict.json`、`hierarchy_metrics_strict.json`
- Partial-aware hierarchy mapping/metrics：`hierarchy_mapping_partial_aware.json`、`hierarchy_metrics_partial_aware.json`
- Gold 人类可读层级：`D:\DoxAgent_CDECR_Stress_20260804_MU300_R1\gold\gold_human_readable.md`
