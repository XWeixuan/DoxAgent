# CDECR Mention-only 五轮调优报告（2026-08-01）

## 最终结论

五轮均完成固定 30 篇 Mention-only 真实调用，30/30 文档成功。最终保留第 3 轮代码，而不是最后
一轮：第 3 轮 P/R/F1 为 **82.67% / 69.40% / 75.46%**，是五轮综合最优；仍未达到计划的
Precision >85%、Recall >80%，因此本阶段质量门判定为**未通过但较执行前当前版显著恢复**。

与计划引用的三轮历史指标比较：相对执行前当前版 69.29%/65.67%，Precision +13.38pp、Recall
+3.73pp；相对历史最佳第一轮 85.33%/71.64%，仍低 2.66pp/2.24pp。没有启动完整 N5.5–N13
验收。

## 五轮结果

| 轮次 | 唯一假设 | Mention | TP/PARTIAL/FP/FN | Precision | Recall | F1 | 结论 |
| --- | --- | ---: | --- | ---: | ---: | ---: | --- |
| R1 | 完整路径重构初版 | 220 | 180/32/8/88 | 81.82% | 67.16% | 73.77% | 保留韧性，继续修正 |
| R2 | 收紧背景提升、改善完整性 | 225 | 182/27/16/86 | 80.89% | 67.91% | 73.83% | Recall微升，FP增加 |
| R3 | 平衡 candidate recall 与一事一条 | 225 | 186/25/14/82 | 82.67% | 69.40% | 75.46% | **最终选择** |
| R4 | 全量 rejected disposition review | 268 | 201/35/32/67 | 75.00% | 75.00% | 75.00% | Recall换Precision，回滚 |
| R5 | 窄化 rejected review | 253 | 193/38/22/75 | 76.28% | 72.01% | 74.09% | 仍劣于R3，回滚 |

R4 的 review 派生 24 条输出中仅 12 TP、3 PARTIAL、9 FP；R5 窄化后 14 条中 8 TP、2 PARTIAL、
4 FP。两轮都证明 rejected 二次语义复审会把缺失候选与背景提升混在一起，不能以当前形态进入
正常路径。最终代码已删除该实验分支并恢复 R3。

## 成本与时长

| 轮次 | Pipeline Input | Pipeline Output | wall time | Gold评估 Input/Output |
| --- | ---: | ---: | ---: | ---: |
| R1 | 435,809 | 186,832 | 992,906ms | 102,900 / 27,551 |
| R2 | 442,322 | 177,015 | 1,015,397ms | 104,522 / 29,075 |
| R3 | 472,778 | 169,008 | 972,080ms | 108,190 / 30,164 |
| R4 | 532,647 | 193,610 | 1,094,951ms | 114,180 / 33,079 |
| R5 | 474,005 | 181,716 | 1,029,367ms | 110,835 / 30,924 |

R3 节点分布：

| 节点 | calls | Input占比 | Output占比 | aggregate latency占比 |
| --- | ---: | ---: | ---: | ---: |
| Dreamer | 30 | 7.13% | 15.79% | 11.60% |
| Grounder | 30 | 25.90% | 61.86% | 64.58% |
| Grounder item repair | 30 | 23.93% | 11.28% | 12.08% |
| Grounder missing recovery | 2 | 1.39% | 0.62% | 0.67% |
| Judge | 30 | 33.36% | 9.55% | 9.47% |
| Judge coverage recovery | 9 | 7.11% | 0.86% | 1.08% |
| Judge item repair | 1 | 0.96% | 0.05% | 0.10% |
| title embedding | 30 | 0.22% | 0% | 0.42% |

模型 aggregate latency 大于 wall time 是节点内部并发所致。Grounder 正常请求与 item repair 合计
占 49.83% Input、75.86% aggregate latency，是下一阶段最明确的成本瓶颈；本轮以质量优先，没有
为了降本取消 item-local 容错。

## Bad case 分布与根因

R3 的 82 个 FN/partial 首次归因：Dreamer missing/low coverage 30，输出 partial 或 Judge loss 39，
Grounder rejected 9，Grounder technical loss 4。Gold 逐条首失统计中，DREAMER_MISSING 53、
OUTPUT_PARTIAL 21、JUDGE_REJECTED_OR_MERGED 8；二者统计单位不同，不能相加。

主要分布性问题：

1. **Dreamer仍漏掉次要但可验证事实**：ETF launch、历史区间回报、数据中心分部增长、作者/分析师
   明确建议等仍不稳定。追加 title/paragraph checklist 有正收益，但继续无差别扩召会重演 R4/R5 FP。
2. **完整性与当前 Gold 原子边界存在冲突**：旧 Gold 把 EPS+margin、SCA+sold-out capacity、
   listing+proceeds 等复合为一条；当前业务合同要求不同 PRIMARY metric/action 保持分离。于是同一
   业务上正确的拆分会被旧 Gold 计作 PARTIAL/fragment。当前数字是保守下界，不能针对旧 Gold
   盲目重新合并。
3. **Grounder repair 仍然昂贵**：R3 每篇都出现 `grounder_item_repair` 调用，虽然局部保留机制已
   避免整篇失败，但正常输出与 Schema/语义契约仍不够稳定。
4. **Judge coverage recovery 的有效性不足**：9 次请求对应 14 个 degraded audit，能避免静默遗漏，
   但没有把主要 FN 分布消除；继续叠加 recovery 会增加注意力竞争和成本。
5. **背景提升/重复**：R3 仍有 UNKNOWN_GOLD_ID 10、duplicate-after-match 4、unsupported 4；
   R4/R5 的 rejected review 将该问题显著放大，证明 precision 风险不是偶然单例。

## 保留与回滚

保留：

- 单 draft 非法只 repair 该 item；repair 后仍非法时保留同批其他合法 draft。
- missing recovery 只保留合法 drafts/rejections，非法局部不再拖垮整批。
- 只有整篇最初零 candidate 时才做一次 Dreamer recovery，避免与正常路径重复扩召。
- Grounder 的 candidate disposition、Judge coverage 与 Mention derivation 审计。
- 不同 action/subject/PRIMARY metric/time 不因共同 report/plan 被合并；split 时字段只随所属事件。

回滚：

- R4 全量 rejected review 和 R5 窄化 review 全部移除。
- 不保留为了 review 新增的 Prompt/Schema；最终 Prompt version 回到 R3 的
  `single-document-prompts-v16`。

## 本轮最终 Prompt 变更全文

Dreamer 新增/替换：

> Before returning, check the title and each paragraph for omitted attributed forecasts or ratings, transactions, explicit plans or commitments, price or volume moves, measurable state changes, and named-subject supply, demand or capacity claims. Require a specific supported assertion. Do not promote generic context; a historical or explanatory statement remains a candidate when it makes a specific named-subject, measurable, attributed claim material to the document.

Grounder 新增：

> A supplied candidate with a named subject and an explicit quantity, forecast, commitment, constraint, or measurable ongoing state is not BACKGROUND merely because it appears in explanatory context; reject it only when it lacks an independently truth-evaluable proposition.

> Keep supplied candidates separate when they carry different actions, subjects, PRIMARY metrics, or times, even inside the same report or plan; merge them only when they are semantically duplicate.

> Default to one complete draft per candidate. Use multiple drafts only when the evidence explicitly supports multiple independently meaningful events; never turn a qualifier, benchmark, bound, trigger, or context fragment into a draft. When splitting is necessary, preserve every supported field exactly once with the event it qualifies.

> Give every supplied candidate exactly one disposition: USED or REJECTED. USED means it appears in source_candidate_ids of one or more non-duplicate atomic drafts; REJECTED means it appears once in rejected_candidates and in no draft. A candidate may support multiple drafts only under the Atomicity rule above. Before returning, verify that USED and REJECTED are disjoint and cover every supplied candidate.

> Keep explicit comparison/baseline and reporting period with the metric they qualify. Preserve concrete products, assets, instruments, and counterparties as core participants when they are part of event identity. Keep explicit source_claim, report or artifact names and other non-participant objects in evidence-backed open_attributes, and metrics or counts in quantities.

> For each evidence location, copy one contiguous verbatim span from its declared segment; never paraphrase or join non-contiguous text.

Grounder 的 `local_package_hint` 段落使用既定短协议，未为本轮 bad case 增加领域枚举：

> Provide local_package_hint when the evidence identifies a parent occurrence or matter containing this Mention. Reuse the same short, distinguishing anchor for Mentions under the same parent. Do not use an entity, ticker, broad topic, article, Package ID, or the Mention itself as the anchor. Leave it null ONLY when no parent boundary is supported. relation_to_anchor points from the Mention to the parent.

Judge 新增：

> An explicit named-source forecast, commitment, constraint, or measurable ongoing state remains truth-evaluable without a calendar bound; do not reject it merely as explanatory context.

> Do not use this when it changes the core action, subject, PRIMARY metric, assertion, or time.

> For SPLIT, preserve every supported event and attach each period, benchmark/range, source, object, and action polarity only to the replacement it qualifies. Do not copy all fields to every replacement or leave a supported event behind. Each replacement must remain an independently meaningful event; keep qualifier, benchmark, bound, trigger, and context fragments with the event they modify.

> A shared report, plan, article, or topic does not make distinct objects, actions/polarities, PRIMARY metrics, Assertion States, or event times/sessions one Mention.

## 验收判断

本轮没有通过 85%/80% 目标，因此不能宣称 Mention 优化完成。可交付的是：相比执行前当前版恢复了
大部分 Precision，五轮中选出了可解释的最优版本，证明 rejected review 路径应删除，并将剩余
问题收敛到 Dreamer 分布性漏召、Grounder item repair 稳定性、旧 Gold 与新业务原子边界不一致
三类。下一次继续调优前，应先修订 Source-occurrence Gold，而不是在旧复合 Gold 上继续加 Prompt。

