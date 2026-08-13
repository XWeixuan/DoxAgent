# CDECR Parent Occurrence Package V2.0R 实施与真实 Package-only 验收报告

日期：2026-08-13  
范围：完整落地 `CDECR_PARENT_OCCURRENCE_PACKAGE_V2_0R_BADCASE_DRIVEN_SEMANTIC_ROLLBACK_PLAN_20260813.md`；按方案先做两份冻结 Atomic 的真实 Package-only Gate，不执行 MU300。

## 1. 最终结论

V2.0R 的代码、Prompt、版本、配置、幂等与回归测试已落地。两份冻结 Atomic 快照均实际调用当前百炼 DashScope 的 `deepseek-v4-flash-0731` 完成 Parent Induction、两轮 Resolution、局部 admission repair 和 Package Apply；没有切回 DeepSeek 官方 provider。

但是，两份 Package-only 结果均未通过质量 Gate，因此严格按照方案第 11.3 节，没有启动30篇全流程，也没有执行 MU300或任何自动回滚。

| 冻结快照 | Pair P | Pair R | Pair F1 | Package | singleton | 最大包 | 判定 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| V2.1 R3（192 Atomic） | 72.93% | 42.36% | 53.59% | 49 | 20/49，40.82% | 50 | FAIL |
| 正式 V2.0（281 Atomic） | 77.55% | 38.78% | 51.70% | 73 | 23/73，31.51% | 56 | FAIL |
| 门槛 | ≥90% | ≥65% | ≥75% | — | ≤45% | 不得形成污染大簇 | — |

V2.0R 确实恢复了合并能力和候选覆盖，但当前恢复幅度越过了安全平衡点：碎片化有所下降，Precision、F1和大簇纯度却明显不合格。失败主因不是 candidate coverage 或 Apply 丢失，而是：

1. Induction 已在部分 proposal 内混入不同父发生；
2. Resolution 对“同 issuer / 同 coarse role / 同报告上下文”的语义合并仍过宽；
3. proposal-level admission guard 只覆盖少量明确边界，不能阻止同 coarse role 内的 SCA、供需、估值、市场时段和背景事实互相吸收。

因此，当前版本属于“工程实现完成，但质量发布不通过”。继续跑完整30篇只会重复消耗上游 token，不能改变已失败的 Package Gate。

## 2. 实施完成情况

### 2.1 Parent cue 与窄 guard

- participant surface、普通公司文本、普通 fiscal period 不再自动提升为 trusted artifact/identity。
- schema projection 的 issuer/institution 也经过 canonical 过滤，不再绕过可信值过滤。
- analyst report 只在存在独立报告证据时成立，不再把所有 `ANALYST_ACTION` 一概视作独立报告。
- `proposal_merge_guard` 只对明确 role、trusted artifact、canonical analyst institution、market date/session 边界进入局部 REVIEW。
- issuer、family、object、metric、source 的普通差异不直接产生 hard negative，也不触发全局拆分。

### 2.2 宽召回与两轮 Resolution

- 已接入 artifact、event overlap、source、institution、counterparty、market、issuer+family、issuer+period、period+family、object+family、metric+period 和 semantic routes。
- 每条 route 先按实际 score 排序再截断；默认配额为 structured 16、semantic 24、total K 32。
- zero-neighbor fallback 为结构化 issuer/period top4 加全局 semantic top4。
- token-budget split 使用 weighted microcomponent；被切断的弱边进入 R2 ledger。
- R1/R2 不继承模型不同组产生的 hard negative；常规 Resolution 固定为两轮。

### 2.3 局部修复、失败语义与 finalize

- 新 union 跨越明确 guard 时，使用相同 Resolution Schema 做一次局部 admission repair。
- repair 失败或仍不合法时恢复 pre-wave proposal，不扩大为整批/整篇失败，也不退化为全 singleton。
- active final Atomic split、Atomic-level reconcile、oversized review 均已移除。
- Apply 只投影冻结 partition，不重新做语义决策。
- finalized partition cache、checkpoint hash 均纳入 contract、Prompt、Schema、compiler 和 candidate policy 版本，避免误复用 V2.1 旧结果。
- Package Apply 审计 reason 已改为 `PARENT_OCCURRENCE_V2_0R_FROZEN_PARTITION`。

### 2.4 Provider 与幂等

- M2/M3/M4 effective provider：`dashscope`。
- effective model：`deepseek-v4-flash-0731`。
- 两份最终结果幂等复验均为 0 新模型调用，partition hash 稳定。
- V2 快照 6 次局部 structured/JSON失败均被局部修复或保守保留，281条 membership 完整，没有扩大为文档失败。

## 3. V2.1 R3 冻结 Atomic 验收

### 3.1 准召与碎片化

Gold 高置信对齐 90/192，coverage 46.88%。

| 指标 | 结果 | Gate |
| --- | ---: | --- |
| TP / FP / FN | 388 / 144 / 528 | — |
| Pair Precision | 72.93% | FAIL |
| Pair Recall | 42.36% | FAIL |
| Pair F1 | 53.59% | FAIL |
| Package singleton | 20/49，40.82% | PASS |
| Gold 可判 singleton 漏合 | 2/11，18.18% | PASS |
| fragmented Gold groups | 4/7 | FAIL |
| excess components | 9 | FAIL |
| singleton components | 8 | FAIL |
| missed pair links | 528 | FAIL |

相对方案记录的 V2.1 R3 Recall 18.96%，本轮提高约23.40pp，仍未达到“至少 +30pp”，且绝对 Recall 远低于65%。

Micron earnings 仍拆为6个组件 `[27, 6, 6, 2, 1, 1]`，其中521条 missed links，占全部 FN 的98.67%，未达到 components ≤4。

### 3.2 过合并 bad cases

- 最大50-member Micron earnings包：Gold可判部分约27条正确、3条异类；混入 SCA forecast、Wall Street expectation 等独立父事实，贡献主要 FP。
- 32-member 供需/SCA包：混入 market theme、AI背景、供需、定价与SCA条款，父边界不纯。
- 16-member earnings-call包：混入 Optimus / physical-AI 等通用背景。
- Wedbush、BofA、Bernstein 的独立 analyst reports 被合成7-member包。
- Apple intraday -0.56% 与 close -6.12% 被同包。
- Tuesday selloff 与 Thursday recovery 已分开，且 Tuesday 同一 global episode 的跨 issuer 事实得以同包；这说明开放跨 issuer 合并本身有效，但不足以抵消其他误并。

### 3.3 候选与效能

- 无模型 candidate replay：111/115 lineage 有合格邻居，coverage 96.52%，达到≥95%。
- 976 eligible/selected edges，0 truncation；Apply 为192/192 membership。
- 因此主要失败在 Induction/Resolution，不是 candidate 或 Apply。
- 模型调用：59次；input 382,967；output 27,893；total 410,860，token门槛通过。
- 该结果由停电/中断后的同库 checkpoint 续跑完成，报告中的 `first_wall_ms=35` 只是 finalized partition 读取与 Apply 段，不能冒充 fresh clean wall；本轮不据此宣称墙钟 Gate 通过。
- failure_count=0，幂等复验0新调用。

## 4. 正式 V2.0 冻结 Atomic 验收

### 4.1 准召与碎片化

Gold 高置信对齐 136/281，coverage 48.40%。

| 指标 | 结果 | Gate |
| --- | ---: | --- |
| TP / FP / FN | 532 / 154 / 840 | — |
| Pair Precision | 77.55% | FAIL |
| Pair Recall | 38.78% | FAIL |
| Pair F1 | 51.70% | FAIL |
| Package / Atomic | 73/281，25.98% | 形态目标通过 |
| Package singleton | 23/73，31.51% | PASS |
| Gold 可判 singleton 漏合 | 4/12，33.33% | FAIL |
| fragmented Gold groups | 7/13 | FAIL |
| excess components | 18 | FAIL |
| singleton components | 13 | FAIL |
| missed pair links | 840 | FAIL |

Micron earnings 52条被拆成11个组件 `[32, 4, 3, 3, 3, 2, 1, 1, 1, 1, 1]`，814条 missed links，远未达到 components ≤4。

### 4.2 大簇纯度与系统性错误

- 56-member Micron earnings主簇：Gold可判部分污染约2/34=5.88%，主簇自身纯度可接受，但外围仍被拆成10个组件。
- 19-member market episode：Gold可判污染6/9=66.67%，混合 SK Hynix、Samsung、KOSPI、资金流和不同市场发生。
- 11-member Micron stock簇：Gold可判污染4/5=80%，混合不同 session、价格、market-cap 与 valuation。
- 9-member Apple market簇：Gold可判污染5/7=71.43%。
- 非singleton误成员保守下界32/258=12.40%，超过≤10%的安全门槛。

旧79-member簇没有原样复现，但这不等于风险消失：污染被重排成56/19/11/9等多个簇，仍属于系统性 supercluster/边界污染。

### 4.3 候选、成本与可靠性

- R1 lineage coverage 93.94%；R1∪R2累计 coverage 96.21%，达到本方案≥95%门槛。
- R1/R2 selected edges 1,164/644，0 truncation；常规 Resolution 两轮。
- clean wall 548,900ms，即9分08.9秒，达到≤15分钟。
- 88 calls；input 637,129；output 47,974；total 685,103。
- input和total分别超过500k/550k，效能 Gate失败。
- 显式repair后缀占12.75%；若按业务口径把 admission/reconcile请求一并计入，为15.95%，超过15%门槛。报告采用更严格口径判失败。
- 幂等复验54ms、0新调用、partition hash稳定。

## 5. Gate 汇总

| Gate | R3 snapshot | V2 snapshot | 总判定 |
| --- | --- | --- | --- |
| Precision ≥90% | FAIL | FAIL | FAIL |
| Recall ≥65% 且R3提升≥30pp | FAIL | FAIL | FAIL |
| F1 ≥75% | FAIL | FAIL | FAIL |
| Package singleton ≤45% | PASS | PASS | PASS |
| 可判 singleton 漏合 ≤25% | PASS | FAIL | FAIL |
| Micron earnings components ≤4 | 6，FAIL | 11，FAIL | FAIL |
| reaction/SCA/report/background intruder=0 | FAIL | FAIL | FAIL |
| final Atomic split=0 | PASS | PASS | PASS |
| lineage coverage ≥95% | 96.52%，PASS | 96.21%，PASS | PASS |
| Parent input ≤500k | PASS | FAIL | FAIL |
| Parent total ≤550k | PASS | FAIL | FAIL |
| repair / total ≤15% | PASS | 严格口径15.95%，FAIL | FAIL |
| clean wall ≤15min | 不可严格判定 | 9.15min，PASS | 未全通过 |
| 幂等0新调用 | PASS | PASS | PASS |

结论：Package-only Gate整体失败。没有启动真实30篇全流程；没有启动MU300；没有自动回滚。

## 6. 独立 Agent 复核

三项独立只读复核已经完成：

1. R3质量复核确认候选覆盖和Apply完整，质量损失集中于 Induction mixed proposal 与 Resolution 过宽；50/32/16大簇均存在可读污染。
2. V2质量/效能复核确认形态和墙钟改善，但非singleton误成员、大簇纯度、准召、token均未过门槛。
3. 实现复核确认此前8项缺口已闭环，active path 未残留 hard-negative、final split、oversized review；当前无P0实现问题。

尚有两项非阻塞P1风险：

- `fallback_issuer_time` 当前实际是“同 issuer 或同 period”的结构池 top4，不是真正的 issuer + event-time window；会增加候选与误并暴露面。
- global semantic top4 在大量零邻居 proposal 时会逐一计算全局 cosine，理论上可能退化为 P²；30篇不是阻塞，但不能据此宣称 MU300 近线性。

这两项不是本轮 Gate失败的唯一或主要原因，不能通过单纯收紧 fallback 来修复当前 supercluster。

## 7. 回归验证

- Ruff：通过。
- strict mypy：通过。
- Parent专项：26 passed。
- CDECR全量：282 passed，3 skipped。
- diff whitespace check：通过。

## 8. 留存产物

- R3最终 Registry：`.tmp/cdecr/parent_v20r_package_only_r3_r2_20260813.sqlite3`
- R3 run report：`.tmp/cdecr/parent_v20r_package_only_r3_r2_20260813_report.json`
- R3 Gold eval：`.tmp/cdecr/parent_v20r_package_only_r3_r2_20260813_package_gold_eval.json`
- V2最终 Registry：`.tmp/cdecr/parent_v20r_package_only_v2_r2_20260813.sqlite3`
- V2 run report：`.tmp/cdecr/parent_v20r_package_only_v2_r2_20260813_report.json`
- V2 Gold eval：`.tmp/cdecr/parent_v20r_package_only_v2_r2_20260813_package_gold_eval.json`
- 可复现 runner：`scripts/cdecr_run_parent_package_only.py`

## 9. 后续判断

当前不应发布 V2.0R，也不应直接恢复 V2.1 的全局 final split。下一轮若继续优化，应优先处理“同 coarse role 内的父边界”，重点约束：

- earnings container 对 SCA、估值、通用AI背景和长期供需的吸收；
- 共同市场日期对不同 instrument/session/market occurrence 的吸收；
- 独立 analyst institution/report 之间的误并。

任何新边界仍应先在这两份冻结 Package-only 快照上同时证明 Precision≥90%、Recall≥65%、F1≥75%，再进入完整30篇全流程。
