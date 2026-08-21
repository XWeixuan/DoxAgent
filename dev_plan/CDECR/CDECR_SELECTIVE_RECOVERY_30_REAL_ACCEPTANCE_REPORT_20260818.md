# CDECR 历史版本选择性恢复：30 篇真实验收报告

## 1. 结论

本轮已完整落地 `CDECR_HISTORICAL_VERSION_PERFORMANCE_QUALITY_SELECTIVE_RECOVERY_PLAN_20260817.md` 中的选择性恢复项，并使用与上一轮完全相同的固定 30 篇语料、全新 Registry 和百炼真实模型完成验收。

最终结论是：

- **运行效能恢复通过**：30/30 文档成功，首轮钟墙 17.72 分钟，较上一轮 87.49 分钟下降 79.75%，并略优于 2026-08-08 的 18.60 分钟性能基准。
- **JSON / failure resilience 恢复通过**：工作流 556 条模型记录中仅 1 条失败，为 Package Description `empty_response`；`invalid_json=0`，Bulk 327 个物理 attempt 全部一次成功，无 retry、backoff 或 provider pressure。
- **Judge 异常已消除**：Judge 主请求加 coverage recovery 的 Token 占比由 23.84% 降至 13.58%，累计模型延迟占比由 37.34% 降至 6.87%。
- **Mention 与 Field 只部分恢复**：Mention precision 和 F1 较上一轮改善，但 recall 继续下降；Field total accuracy 从 57.46% 回升至 82.85%，仍低于既定门槛。
- **N9 单任务评估通过，但最终 Atomic 业务质量不通过**：N9 Gold 显示 Merge Precision 91.30%、conditional Merge Recall 87.50%，但最终出现 30-Mention 复合 Atomic 和 407 个 hard-cannot-link pair violation。N9 评估器把 revenue、EPS、margin 等明显不同最小事实判成 SAME，不能作为最终发布证明。
- **Package Precision 通过、Recall 不通过**：高置信对齐子集 P/R/F1 为 97.79%/75.32%/85.10%，Micron earnings 从上一轮 2 个组件退化为 5 个组件；Description 还发生一次失败降级。

因此本轮总 Gate 为 **FAIL**。当前版本可以作为新的性能基准，但**不应进入 MU300 压测**，否则会放大已经确认的 Mention 漏召、Atomic 过合并和 Package 碎片化。

## 2. 冻结输入与产物

- Snapshot：`.tmp/cdecr/baselines/us_mu_2026-06-25.jsonl`
- Snapshot SHA256：`E3ACE54E34A951E04F7AD0852218616A56AEEBEEC73ECEF8F57F821DD0FF8290`
- Manifest：`.tmp/cdecr/parent_v2_fixed_30_manifest.json`
- Manifest SHA256：`8A4E4EAC8116B140AB5EF6C5F809AA2BE629742198A9559D8A8F143B70BAB3FE`
- Registry：`.tmp/cdecr/mu30_selective_recovery_20260817_r1.sqlite3`
- Runtime report：`.tmp/cdecr/mu30_selective_recovery_20260817_r1_report.json`
- Mention Gold：`.tmp/cdecr/mu30_selective_recovery_20260817_r1_mention_gold_eval.json`
- Field Gold：`.tmp/cdecr/mu30_selective_recovery_20260817_r1_field_gold_eval.json`
- N9 Gold：`.tmp/cdecr/mu30_selective_recovery_20260817_r1_n9_gold_eval.json`
- Package Gold：`.tmp/cdecr/mu30_selective_recovery_20260817_r1_package_gold_eval.json`

运行使用此前不存在的全新 Registry；二次幂等复验的 model call、Mention、Atomic、Package delta 均为 0。

## 3. 已落地修复

### 3.1 模型传输与 thinking 隔离

- 非 Package M2：百炼 `deepseek-v4-flash`，Chat Completions JSON Object，thinking 关闭。
- 非 Package M3/M4：百炼 `deepseek-v4-flash`，Responses JSON Object，thinking 关闭。
- Dreamer：独立 Responses JSON Object，thinking 关闭。
- Relevance：独立、无 `previous_response_id` 的 Responses JSON Object，thinking=low；主批失败时只对同批做一次无 continuation repair，仍失败则逐项 fail-open。
- Package V3：独立 `deepseek-v4-flash-0731` Responses 客户端；Initial Clustering=high，Description=low。
- JSON Schema strict 实现和开关保留，但 M2/M3/M4 默认全部关闭。

正式运行前四个真实最小探针均成功：

| Lane | 模型 | Transport | Output | Thinking |
| --- | --- | --- | --- | --- |
| M2 | deepseek-v4-flash | chat_json_object | json_object | none |
| M3 | deepseek-v4-flash | responses_json_object | json_object | none |
| M4 | deepseek-v4-flash | responses_json_object | json_object | none |
| Package V3 | deepseek-v4-flash-0731 | responses_json_object | json_object | none（client default；节点覆盖 high/low） |

### 3.2 并发、重试与审计

- provider target/hard/start-rate/burst 恢复为 `100/160/50/80`。
- M1 embedding 绕过 structured provider gate。
- Field、N9、Parent 使用 stage-local provider gate，单阶段 pressure 不再污染后续阶段。
- 只把真实 throttling 视为并发压力；auth、arrearage、普通 JSON 错误不再降低全局并发。
- 移除 tier limiter 与 provider gate 的重复缩容。
- 外层重试只覆盖 throttled/transient，最大 1 次；key rotation 仍由 adapter 负责。
- 新增物理 attempt、provider wait、backoff、错误类型、key fingerprint、transport、output mode 与 effective reasoning telemetry。

### 3.3 JSON 与 Relevance

- JSON Object 输出继续经过 Pydantic、ID coverage 与业务 validator。
- 只允许安全归一：单一完整 code fence 或文本中唯一 JSON object；多个候选 object 仍判非法。
- parse diagnostics 只保留长度、解析位置和前后缀 hash，不持久化整段非法 payload。
- Dreamer 仅增加一条 cap tie-breaker，未改输出 Schema。

## 4. 静态与回归验收

- CDECR 全量：`305 passed, 3 skipped`。
- 最终 telemetry 补丁聚焦回归：`60 passed`。
- Ruff：通过。
- strict mypy：62 个 source file 通过。
- `git diff --check`：通过，仅存在 Windows CRLF 提示。

Field batch 在冻结实测 DB 中缺少 model-call transport metadata；物理 attempt、配置和真实探针均证明其走 M2 Chat JSON Object。该纯 telemetry 缺口已在实测后补齐并通过聚焦测试，不改变本轮业务结果。

## 5. 完整性与可靠性

| 指标 | 上一轮 2026-08-17 | 本轮 | 判断 |
| --- | ---: | ---: | --- |
| 文档成功 | 28/30 | 30/30 | 通过 |
| Event 成功 | 28/30 | 30/30 | 通过 |
| Mention | 248 | 214 | 数量下降，需结合 Gold 判断 |
| Active Atomic | 189 | 122 | 压缩显著，但含过合并 |
| Package | 39 | 28 | 压缩显著，但 recall 回归 |
| Mention schema / Evidence span | 100% / 100% | 100% / 100% | 通过 |
| 模型失败 | 115/686 | 1/556 | 明显改善 |
| invalid_json | 112 | 0 | 通过 |
| Package Description | 成功 | 1 次 empty_response，31项 fallback | 降级但不阻塞 |
| 幂等复验 | 0 delta | 0 delta | 通过 |

Relevance Enforce 共处理 378 个 Dreamer candidate，保留 294、明确删除 84、fail-open=0；所有明确 IRRELEVANT 均未进入下游。

Package Description 的唯一失败被局部化：31 个 MCP 均保留 Initial Clustering 的 canonical 作为 compressed description，Registry 正常 FINALIZED，122/122 Atomic 均有 Package membership。它没有造成流程失败，但 Description 内容没有获得模型压缩增强。

## 6. 效能与成本

### 6.1 总体 A/B

| 指标 | 上一轮异常版本 | 本轮 | 环比 |
| --- | ---: | ---: | ---: |
| 首轮钟墙 | 5,249,381 ms / 87.49 min | 1,063,198 ms / 17.72 min | -79.75% |
| 模型调用 | 686 | 556 | -18.95% |
| Input Token | 1,517,458 | 1,325,375 | -12.66% |
| Output Token | 1,287,834 | 393,471 | -69.45% |
| Total Token | 2,805,292 | 1,718,846 | -38.73% |
| 累计模型延迟 | 14,515,430 ms | 4,147,314 ms | -71.43% |

本轮钟墙相对 2026-08-08 的 18.60 分钟性能基准约再下降 4.7%。不同版本输出规模不同，因此这是相同语料上的实际成本比较，不是严格隔离变量实验。

### 6.2 阶段钟墙

| 阶段 | 本轮实际钟墙 |
| --- | ---: |
| 单文档阶段（整体首轮减 Bulk） | 115.53 s |
| Field | 342.16 s |
| N9 main | 101.68 s |
| N9 late | 22.45 s |
| Parent + Package | 503.48 s |
| 其中 Package V3 stage | 352.98 s |

Package V3 Initial Clustering 单次模型 latency 为 310.95 秒，是当前最大的单请求关键路径；此前观察到的 proposal 写入后数分钟停顿实际是该请求，而非 Parent embedding 卡死。

### 6.3 主要节点 Token / 累计模型延迟占比

| 节点 | Calls | Input | Output | Total Token占比 | 累计模型延迟占比 |
| --- | ---: | ---: | ---: | ---: | ---: |
| atomic_coreference | 57 | 232,593 | 80,941 | 18.24% | 17.76% |
| grounder | 30 | 118,373 | 91,077 | 12.19% | 17.88% |
| judge + coverage | 38 | 203,128 | 30,358 | 13.58% | 6.87% |
| grounder_item_repair | 34 | 126,294 | 17,513 | 8.37% | 4.15% |
| field_coreference | 140 | 135,680 | 7,653 | 8.34% | 12.46% |
| parent_induction | 14 | 124,836 | 16,100 | 8.20% | 3.46% |
| parent_repartition | 16 | 106,572 | 10,472 | 6.81% | 2.47% |
| atomic escalation | 15 | 65,029 | 23,505 | 5.15% | 4.97% |
| relevance | 30 | 45,032 | 39,454 | 4.92% | 8.96% |
| dreamer | 30 | 33,082 | 34,527 | 3.93% | 7.46% |
| Package Initial | 1 | 2,988 | 32,869 | 2.09% | 7.50% |

Judge 主请求加 coverage recovery 从上一轮 668,848 Token / 5,420.21 秒，下降到 233,486 Token / 284.80 秒，即 Token -65.09%、累计模型延迟 -94.75%。Judge 不再是最高成本或最高时长节点。

### 6.4 并发与物理 attempt

Bulk executor：

- logical calls=327；physical attempts=327；retry=0；backoff=0ms；failed=0。
- provider max active=57；M2 max active=57，M3 max active=14。
- 所有 stage pressure_events=0，provider 最终 limit=160，没有再从100连续跌到4。
- queue wait 总计仅2ms。

当前实际并发没有达到硬上限，是受可同时就绪的任务数、Field batch聚合和单请求时长限制，不是 provider gate 人为压低。

## 7. Mention 质量

| 指标 | 上一轮 | 本轮 | 变化 | 目标 | 判断 |
| --- | ---: | ---: | ---: | ---: | --- |
| Precision | 60.08% | 66.82% | +6.74pp | >90% | 失败 |
| Recall | 55.60% | 53.36% | -2.24pp | >85% | 失败 |
| F1 | 57.75% | 59.34% | +1.59pp | — | 仍低 |
| Gold / Output | 268 / 248 | 268 / 214 | -34 output | — | 召回风险 |

本轮 FN 根因分布：

- `DREAMER_MISSING=86`，上一轮为76；
- `OUTPUT_PARTIAL=27`，上一轮为42，明显改善；
- `GROUNDER_MISSING_OR_INVALID=12`，上一轮没有该项；
- Judge 不再是 Mention 漏召主因。

判断：Dreamer cap tie-breaker 的业务收益没有被证明。它与 candidate 数从430降至378、Dreamer missing 增加10项同时发生；虽然不能仅凭一次模型运行证明因果，但该句具备较高回滚必要性。Grounder 还新增12个缺失/非法项，需要逐条看其 repair/fail-open分类。

## 8. Field 质量

| Field | 上一轮 | 本轮 | 既定门槛 | 判断 |
| --- | ---: | ---: | ---: | --- |
| predicate | 59.70% | 86.05% | 90% | 失败 |
| participant | 56.72% | 87.79% | 96% | 失败 |
| metric | 50.00% | 78.74% | 90% | 失败 |
| fiscal period | 72.00% | 72.29% | 80% | 失败 |
| total | 57.46% | 82.85% | 91% | 失败 |

Field 的 transport 与 thinking 修复显著有效，但当前 Field 评估只覆盖成功/部分匹配 Mention，分母由811降至554，不能把 +25.39pp 全部解释成 Field 模型自身改善。主要残留是 predicate mismatch、missing metric、wrong participant 和 fiscal period/time 缺失。

## 9. N9 / Atomic 质量

### 9.1 N9 评估器结果

- task=214；candidate coverage=100%。
- judgeable SAME opportunities=96。
- correct merge=84，incorrect merge=8。
- Merge Precision=91.30%。
- conditional Merge Recall=87.50%。
- CREATE_NEW accuracy=90.16%。

这些指标达到此前 N9 门槛，但与最终簇的确定性冲突，不能单独作为发布依据。

### 9.2 最终 Atomic 形态

| 指标 | 上一轮 | 本轮 |
| --- | ---: | ---: |
| Active Atomic | 189 | 122 |
| singleton | 160 / 189 = 84.66% | 92 / 122 = 75.41% |
| 最大 Atomic | 9 | 30 |
| hard-cannot-link violations | 98 / 28,717 | 407 / 21,021 |
| reaction-in-earnings violations | 3 / 47 | 4 / 46 |

最大 30-Mention Atomic 明确混合：

- Q3 revenue 41.46/41.5bn；
- EPS 25.11；
- adjusted/GAAP gross margin；
- operating margin 80.4%；
- data-center annualized revenue约100bn；
- 总括业绩超预期表述。

另有一个5-Mention Atomic 合并 Citi、Mizuho、Needham、Wedbush 四家机构的不同评级/目标行动；一个4-Mention Apple Atomic 同时混入产品成本表述、盘中跌0.56%和收盘跌逾5%。

从 assignment 账本看，30-Mention 簇不是传递闭包副作用，而是29次直接 `N9_MERGE / SAME_EVENT` 合入同一个 seed。N9 Gold evaluator 又把多数 revenue 与 EPS pair评为 SAME，说明当前评估判据本身把“同一财报容器”误当“同一最小事实”，其 91.30% Precision 存在系统性高估。

判断：**Atomic 是当前最高优先级业务阻塞项**。进入 MU300 前必须先修正 N9 Prompt/评估口径对 report container 与 minimal fact 的边界，并在 Apply 前真正执行已存在的 metric/accounting-basis/institution/polarity hard boundary；不能只改评估分数。

## 10. Package 质量与碎片化

### 10.1 Gold Pair 指标

本轮高置信对齐50/122=40.98%，只代表可判子集：

| 指标 | 上一轮 | 本轮 | 目标 | 判断 |
| --- | ---: | ---: | ---: | --- |
| Pair Precision | 94.96% | 97.79% | >90% | 通过 |
| Pair Recall | 87.55% | 75.32% | >80% | 失败 |
| Pair F1 | 91.11% | 85.10% | — | 回归 |

两轮对齐覆盖为78与50，不是严格同集 A/B，环比只可作方向性证据；本轮自身 Recall 低于门槛是明确事实。

### 10.2 碎片化与大簇

| 指标 | 上一轮 | 本轮 |
| --- | ---: | ---: |
| Package | 39 | 28 |
| singleton Package | 15/39=38.46% | 10/28=35.71% |
| 最大 Package | 82 | 52 |
| Gold fragmented groups | 4/7 | 2/4 |
| Micron earnings components | 2 `[45,3]` | 5 `[27,1,1,1,1]` |
| Micron earnings missed links | 135 | 114 |

Raw singleton比例和最大Package有所下降，但不能据此宣称整体更好：本轮上游 Atomic 已把大量不同事实压入一个30-Mention Atomic，使 Package 输入粒度被污染；同时 Micron earnings 的高置信对齐组件从2个退化为5个，Package Recall 明确下降。

V3 Initial Clustering 形成31个 MCP，其中3个因 cross-MCP Atomic unique-owner 后为空，最终投影28个 Package。主要大簇为 earnings 52、stock reaction 10、SCA 9。Package Description 全批失败后使用 canonical fallback，故本轮不具备 Description质量通过证据。

## 11. 各修复项是否符合预期

| 修复项 | 结果 |
| --- | --- |
| M2 Chat JSON Object、M3/M4 Responses JSON Object | 通过真实探针与运行验证 |
| 非 Package thinking关闭 | 通过；Judge异常消失 |
| Package模型隔离 | 通过；Initial仍high、Description仍low |
| strict默认关闭 | 通过 |
| provider并发恢复 | 通过；无缩到个位数、无pressure |
| stage-local gate / M1 bypass | 通过物理attempt审计 |
| 仅throttling触发pressure | 通过；0 pressure event |
| 外层retry收窄 | 通过；0无效retry/backoff |
| JSON安全归一 | 通过；invalid_json=0 |
| Relevance独立无continuation与同批repair | 通过；fail-open=0、未误删 |
| bounded parse/attempt telemetry | 通过；Field model-call metadata缺口已补丁修复 |
| Dreamer cap tie-breaker | 不符合质量预期，建议回滚评估 |
| Mention质量恢复 | 失败 |
| Field质量恢复 | 部分恢复，未达门槛 |
| Atomic质量恢复 | 失败，且出现严重过合并 |
| Package质量恢复 | Precision通过，Recall/Description失败 |

## 12. 严重问题与后续顺序

### P0：先修 Atomic，不进入 MU300

1. 纠正 N9 Prompt 与 Gold evaluator 对“同一报告容器”和“同一 minimal fact”的边界；revenue、EPS、margin、机构行动不能因同一财报上下文视为 SAME。
2. Apply 前启用双方均高置信时的 metric、accounting basis、analyst institution、market session/polarity hard boundary；只阻断单条candidate，不扩大为任务失败。
3. 用本轮30-Mention、跨机构 analyst、Apple价格/股价三个簇做冻结反事实回放，验证能拆错簇而不伤正确 revenue/EPS各自跨文档召回。

### P0：恢复 Mention recall

1. 对86个 `DREAMER_MISSING` 比对上一轮，单独评估新 cap tie-breaker；若新增漏召集中在cap命中样本，回滚该一句 Prompt。
2. 对12个 `GROUNDER_MISSING_OR_INVALID` 核对主批、item repair与missing recovery路径，确保局部失败不变成候选丢失。

### P1：Package Recall 与 Description

1. 在修复 Atomic 输入污染后再评估 Package；当前先调 Package 会掩盖上游错误。
2. 对 Micron earnings 5组件做冻结 V3 clustering重放，确认是PO粒度还是global clustering语义导致4个singleton组件。
3. 排查 Description `empty_response`；保留当前 canonical fallback，不升级为阻塞。

### P1：Field门槛

继续针对 metric、participant和fiscal period做badcase修复，但不得通过扩大复合 Mention 或放宽 identity merge 来抬高表面命中率。

## 13. 最终判断

本轮成功找回了正常性能和可靠性基线，证明上一轮钟墙翻倍、Judge爆量和 JSON invalid 多发，主要由错误 transport/thinking、全局双重并发缩容和过宽重试共同造成；这些问题现已被真实测试验证修复。

但质量并未随性能自动恢复。Mention recall仍低，Atomic出现比上一轮更严重的复合簇，Package Recall也回落。因此当前版本的正确定位是：

> **性能恢复版本，可作为后续质量修复基线；尚不是可进入 MU300 的发布候选。**

本轮未执行任何自动回滚。
