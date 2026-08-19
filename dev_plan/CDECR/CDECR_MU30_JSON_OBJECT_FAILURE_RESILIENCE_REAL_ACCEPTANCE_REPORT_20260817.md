# CDECR JSON Object / Failure Resilience 30 篇真实验收报告

## 1. 结论

本轮使用固定30篇MU语料、全新Registry和百炼 `deepseek-v4-flash-0731` 完成了真实全流程运行。结果为 **FAIL，不得直接进入MU300压力测试**。

积极结果有两项：

1. Relevance 独立 JSON Object 请求和同批无 continuation repair 在真实运行中闭环：430条candidate中342条保留、88条明确剔除、fail-open=0；2个失败主批均由各一次局部repair恢复，没有重跑Dreamer。
2. Package V3 在Gold高置信对齐子集上达到 Pair P/R/F1 = 94.96%/87.55%/91.11%，Micron FQ3 earnings为2个组件（45+3），末端Package聚合本身明显改善。

但存在四个发布阻断项：

- 仅28/30文档成功，2篇在Grounder主请求遭遇百炼 `provider_datainspectionfailed` 后被扩大为整文档失败；
- N9 JSON Object主调用63次中59次 `invalid_json`，最终27/248 N9任务降级为 `N9_UNJUDGEABLE_FAILED_SINGLETON`；
- Mention严格P/R/F1仅60.08%/55.60%/57.75%，Field总准确率仅57.46%；
- 全流程墙钟87.51分钟，比上一轮45.2分钟增加93.60%，其中Parent/Package关键路径占45.40%，并出现约25分钟缺少物理尝试落账的长尾。

## 2. 冻结输入与产物

- Snapshot：`.tmp/cdecr/baselines/us_mu_2026-06-25.jsonl`
- Snapshot SHA256：`E3ACE54E34A951E04F7AD0852218616A56AEEBEEC73ECEF8F57F821DD0FF8290`
- Manifest：`.tmp/cdecr/parent_v2_fixed_30_manifest.json`
- Manifest SHA256：`8A4E4EAC8116B140AB5EF6C5F809AA2BE629742198A9559D8A8F143B70BAB3FE`
- Registry：`.tmp/cdecr/mu30_json_object_resilience_20260817_r1.sqlite3`
- Runtime report：`.tmp/cdecr/mu30_json_object_resilience_20260817_r1_report.json`
- Mention Gold：`.tmp/cdecr/mu30_json_object_resilience_20260817_r1_mention_gold_eval.json`
- Field Gold：`.tmp/cdecr/mu30_json_object_resilience_20260817_r1_field_gold_eval.json`
- Package Gold：`.tmp/cdecr/mu30_json_object_resilience_20260817_r1_package_gold_eval.json`

运行使用全新、此前不存在的Registry，未复用旧checkpoint。幂等复验新增model call/Mention/Atomic/Package均为0。

## 3. 完整性与失败

| 指标 | 结果 | 判断 |
| --- | ---: | --- |
| 选择文档 | 30 | 固定语料完整 |
| 成功文档/事件 | 28/30 | 不通过 |
| 文档成功率 | 93.33% | 不通过 |
| Mention | 248 | 仅来自28篇成功文档 |
| Active Atomic | 189 | 完整Apply |
| Package | 39 | V3 batch已FINALIZED |
| External relations | 41 | 已落库 |
| Mention schema / Evidence span合法率 | 100% / 100% | 仅覆盖成功输出 |

失败文档均在Grounder主请求发生百炼 `provider_datainspectionfailed`：

1. `SanDisk, Western Digital, Seagate Surge After Hours As Micron's Blowout Earnings Signal Memory Upcycle`
2. `Why Everyone Is Talking About Micron`

防扇出修复生效：provider错误没有扩散为逐candidate Grounder请求。但当前错误被直接提升成文档失败，仍损失两篇全部Mention和下游事件，failure resilience未闭环。

## 4. Relevance验收

| 指标 | 本轮 |
| --- | ---: |
| Relevance audit | 30/30 |
| 输入candidate | 430 |
| 保留 | 342 |
| 明确IRRELEVANT并剔除 | 88 |
| fail-open | 0 |
| 主请求成功 | 28 |
| 主请求失败后同批repair成功 | 2/2 |

结论：本轮优先修复目标已经真实通过。Relevance不再携带Dreamer `previous_response_id`，两个失败批次没有重跑Dreamer，repair输出完整覆盖原candidate ID，明确不相关记录没有进入后续流程。

## 5. 效能与成本

### 5.1 总体

| 指标 | 上轮strict失败验收 | 本轮JSON Object | 变化 |
| --- | ---: | ---: | ---: |
| 墙钟 | 45.20 min | 87.51 min | +93.60% |
| Model calls | 547 | 686 | +25.41% |
| Input token | 958,857 | 1,517,458 | +58.26% |
| Output token | 1,453,567 | 1,287,834 | -11.40% |
| Total token | 2,412,424 | 2,805,292 | +16.28% |
| 累计provider latency | 16,003,471 ms | 14,515,430 ms | -9.30% |

输出token下降说明关闭strict后没有继续放大上一轮的hidden-thinking输出异常；但N9 repair调用和输入重发使总token仍上升。累计provider latency下降而墙钟接近翻倍，说明本轮主要问题不是单请求耗时总和，而是失败后的波次等待、重试和关键路径串行长尾。

### 5.2 互斥墙钟阶段

| 阶段 | 墙钟 | 占比 |
| --- | ---: | ---: |
| 单文档 Dreamer/Relevance/Grounder/Judge | 1,658.18s / 27.64min | 31.58% |
| Field | 437.15s / 7.29min | 8.33% |
| N9/Atomic（含late） | 771.39s / 12.86min | 14.69% |
| Parent induction + Package V3 | 2,383.66s / 39.73min | 45.40% |
| 总计 | 5,250.54s / 87.51min | 100% |

Package V3自身约352.59s（initial clustering 301.28s、Description 50.79s），因此Package全阶段39.73分钟中的主要异常来自Parent及其不可观测等待，而不是V3本地Apply。

### 5.3 Token占比最高节点

| 节点 | Input | Output | Total占比 | 累计模型时长占比 |
| --- | ---: | ---: | ---: | ---: |
| Judge | 172,107 | 496,741 | 23.84% | 37.34% |
| Grounder | 115,129 | 235,579 | 12.50% | 17.99% |
| N9 main | 253,707 | 74,100 | 11.69% | 5.26% |
| N9 batch repair | 250,584 | 67,235 | 11.33% | 4.35% |
| Field | 193,168 | 8,796 | 7.20% | 3.52% |
| Parent induction | 114,350 | 62,349 | 6.30% | 4.66% |
| N9 item repair | 102,955 | 15,370 | 4.22% | 1.22% |
| N9 escalation | 51,440 | 65,066 | 4.15% | 4.72% |

N9 main + batch repair + item repair已消耗763,951 token，占全局27.23%；这还不含escalation。repair不再是低频异常路径，已经成为主成本节点。

### 5.4 请求失败

| 状态/错误 | 调用数 | Input | Output |
| --- | ---: | ---: | ---: |
| SUCCEEDED | 571 | 1,074,987 | 1,062,404 |
| FAILED | 115 | 442,471 | 225,430 |
| `invalid_json` | 112 | — | — |
| `provider_datainspectionfailed` | 3 | — | — |

失败调用占16.76%，且失败请求仍消耗约667,901 token。当前物理重试没有逐次写入model_calls；`async_executor.provider.completed=808`而业务模型记录为686次，说明仍有大量物理尝试仅存在聚合telemetry，无法逐次审计其等待时间和错误。

## 6. Mention与Field质量

Mention使用既有30篇固定Gold，包含失败文档的零输出损失。

| Mention指标 | 历史R5可接受基准 | 本轮 | 变化 |
| --- | ---: | ---: | ---: |
| Gold | 268 | 268 | 同口径 |
| 输出Mention | 229 | 248 | +19 |
| Strict TP | 183 | 149 | -34 |
| Partial | 36 | 66 | +30 |
| FP | 10 | 33 | +23 |
| FN | 85 | 119 | +34 |
| Precision | 79.91% | 60.08% | -19.83pp |
| Recall | 68.28% | 55.60% | -12.69pp |
| F1 | 73.64% | 57.75% | -15.89pp |

FN首次失败归因：Dreamer missing 76、output partial 42、Judge rejected/merged 1。主要问题不是Judge拒绝，而是候选漏召回和复合/残缺输出。错误类型中另有8个fragmented、8个compound split、5个incomplete；33个输出无法安全匹配Gold。

| Field | 历史R5 | 本轮 | 变化 | 既有门槛 |
| --- | ---: | ---: | ---: | ---: |
| predicate | 73.88% | 59.70% | -14.18pp | >=90% |
| participant | 73.03% | 56.72% | -16.31pp | >=96% |
| metric | 72.92% | 50.00% | -22.92pp | >=90% |
| fiscal period | 89.47% | 72.00% | -17.47pp | >=80% |
| total | 74.84% | 57.46% | -17.38pp | >=91% |

所有Field指标均回归，且上游Mention partial/FP会进一步污染Field评估集。当前不是可通过局部阈值微调解决的小幅波动。

## 7. N9/Atomic质量与碎片化

| 指标 | 本轮 |
| --- | ---: |
| N9任务 | 248 |
| 成功 | 221 |
| 失败降级 | 27 / 10.89% |
| MERGE | 56 |
| CREATE_NEW | 192 |
| `N9_RELATED_CREATE_NEW` | 156 |
| `N9_UNJUDGEABLE_FAILED_SINGLETON` | 27 |
| Active Atomic | 189 |
| Atomic singleton | 160 / 84.66% |
| 最大Atomic | 9 Mentions |
| size>=8 Atomic | 2 |

N9 main调用63次中59次非法JSON；59次batch repair仍有12次非法，35次item repair仍有25次非法。业务层最终保住了248条exactly-once assignment，但27条只能保守建singleton，且大量 `RELATED_NOT_SAME` 继续造成Atomic碎片化。

更严重的是最终Atomic仍有98个已知hard-cannot-link violation（28,717个可检查pair中的0.34%），说明部分错误合并穿过了N9/Apply边界；同时reaction进入earnings Package的已知违规为3条。系统同时存在过拆和误并，不适合仅通过放宽MERGE来提升召回。

## 8. Package质量与碎片化

Gold只高置信对齐78/189 Atomic（41.27%），以下结果必须作为子集指标解读。

| 指标 | 本轮 |
| --- | ---: |
| Pair Precision | 94.96% |
| Pair Recall | 87.55% |
| Pair F1 | 91.11% |
| TP / FP / FN | 999 / 53 / 142 |
| Package | 39 |
| Package singleton | 15 / 38.46% |
| 最大Package | 82 Atomic |
| size>=8 Package | 3 |
| Fragmented Gold groups | 4/7 |
| Singleton components in multi-event Gold | 6 |
| Missed pair links | 142 |

Micron FQ3 earnings在对齐子集上为2个组件（45+3），明显优于此前多组件碎片化；但82-member主Package错误吸收了“humanoid robots carry 10x memory”事实，贡献跨Gold FP。另有Sandisk investor day与Citi target、Micron after-hours涨幅与market-cap等错误父发生合并。末端聚类已经不是当前最大问题，但仍需逐包边界复核，且不能用41.27%覆盖的高分掩盖上游失败。

## 9. 严重问题与修复优先级

### P0-1：Grounder provider错误仍扩大为整文档失败

`provider_datainspectionfailed`已被正确识别并阻止item fan-out，但随后直接终止文档。需要增加文档级、可恢复的provider failover边界：优先切换健康fallback key/兼容模型或安全的请求重构；仍失败则记录 `FAILED_RETRYABLE`，不得把空Mention固化为成功，也不得让一次provider审查错误永久丢失整篇文档。

### P0-2：N9 Responses JSON Object输出合同不稳定

59/63主调用非法JSON是系统性失败，不是偶发repair。应先持久化限长原始响应形态和解析失败位置，区分Markdown包裹、截断、thinking污染、根级shape和ID coverage错误；然后修Responses文本提取/输出上限/Prompt合同。节点级真实probe必须以完整N9批次和ID coverage为准。未证明稳定前不能依靠逐项repair兜底，更不能启用strict。

### P0-3：Mention/Field质量严重回归

Dreamer missing 76和output partial 42是Mention FN主体；Field四类准确率全面下降。需要逐文档复核missing/partial/FP bad case，重点排查本轮JSON Object adapter、Relevance剔除的88条中是否有错误删除、Grounder/Judge合同及复合Mention切分。Relevance本身协议成功不等于88条业务判断全部正确。

### P0-4：Parent阶段不可观测长尾

Parent/Package占39.73分钟，V3自身仅约5.88分钟。实际运行中checkpoint长时间保持RUNNING，最终model_calls只记录约53–74秒的完成请求，无法解释约25分钟等待。必须给每个物理attempt记录attempt index、开始/结束、错误和backoff，并对Parent局部repartition设置更短的阶段预算；失败应立即保留原group继续，而不是让少量可疑group阻塞整个Package阶段。

### P1：Atomic hard-boundary最终复检

98个已知hard-cannot-link violation说明当前Apply后仍缺最终闭环。应对已知确定性冲突做非阻塞、局部成员切分或隔离，不得整任务回滚，也不得只留shadow审计。

## 10. MU300进入判定

**不允许进入MU300正式压力测试。** 最小放行条件：

1. 同一30篇达到30/30成功，provider错误不再永久丢文档；
2. N9主请求非法JSON显著降至低个位数比例，N9失败降级接近0；
3. Mention和Field至少恢复到历史R5附近，不能继续出现两位数百分点回归；
4. Parent/Package关键路径不再出现不可解释的20分钟级静默，30篇总墙钟恢复到可接受范围；
5. 已知Atomic hard-cannot-link violation显著收敛；
6. 再跑一次全新30篇并通过以上Gate后，才可启动MU300。

本轮不建议回滚Relevance独立请求或Package V3；应优先修复Grounder provider failover、N9 JSON Object合同、Mention/Field质量及Parent attempt telemetry/阶段预算。
