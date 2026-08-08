# CDECR Thinking 强度下调：固定 30 篇真实 A/B 验收报告

> 日期：2026-08-04  
> 正式基线：`D:\DoxAgent_CDECR_Acceptance_20260804_FinalNarrow_R1`  
> 正式实验：`D:\DoxAgent_CDECR_Acceptance_20260804_ThinkingDownshift_R5`  
> Gold：沿用上一轮固定 30 篇 Gold，未重新标注；评估请求不计入工作流 Token/耗时。

## 1. 结论

本轮配置已经按要求改为 M2 关闭 thinking、M3=low、M4=high，其他模型、strict Schema、并发和业务 Prompt 保持不变。三档各选一个真实节点请求做 strict function probe，全部通过；M2 返回中无 reasoning token，M3/M4 均实际产生 reasoning token。

正式 R5 的 30 篇单文档和跨文档处理均成功，成功率为 **30/30（100%）**，幂等重跑增量为 0。总 Token 从 3,324,234 降至 2,428,274（**-26.95%**），累计模型延迟从 14,937,672 ms 降至 7,856,953 ms（**-47.40%**）；但真实端到端墙钟仅从 3,385,244 ms 降至 3,315,832 ms（**-2.05%**），说明当前瓶颈仍由串行关键路径、长尾请求和恢复请求决定，不能用累计模型延迟等比例推算实际提速。

质量没有全面通过：

- Mention Precision 明显改善（71.54% → 79.91%，+8.37pp），Recall 小幅下降（69.40% → 68.28%，-1.12pp）。
- Field 总准确率下降 2.71pp，predicate、participant 分别下降 4.48pp、4.12pp。
- N9 MERGE Precision 达到 100%，但 conditional MERGE Recall 从 89.87% 降至 84.00%（-5.87pp），呈现明显的保守化/过拆。
- 同集 Package Pair P/R/F1 从 89.47%/77.02%/82.78% 变为 95.22%/77.35%/85.36%，碎片化小幅改善；但这一结果同时受到本轮之前尚未提交的 Package 语义回撤影响，不能归因于 thinking 下调。
- M3 相关 Grounder 主请求的非法 JSON 从 1 次增至 8 次，并连带把 missing recovery 请求显著放大；这是本轮最明确的可靠性回归。

因此，不建议把三档下调整体视为“验收通过”。M2-off/M3-low 带来了显著成本收益，但当前全局使用尺度过激；尤其 M3-low 应优先恢复复杂 Grounder 节点的更高 thinking，M2-off 也应至少对 Dreamer/N9/Field 等质量敏感节点做分节点复核。M4-high 暂未暴露结构化失败，可保留观察，但 Judge 的单次输出成本反而上升，尚不能宣称它单独降本。

## 2. 测试完整性与控制变量

### 2.1 正式语料

R5 manifest 使用 Python 按 FinalNarrow R1 的 30 个 document ID、顺序和 fingerprint 重建，并在启动前通过生产 `load_step4_corpus` 校验：数量 30、顺序一致、fingerprint 全部一致。因此 R5 是本报告唯一纳入正式 A/B 的运行。

此前四次启动均不纳入指标：

- R1 误用了 24 行 Step2 manifest，实际跑成 24/24；有真实模型调用，但不是固定 30 篇。
- R2、R3 在 Registry/document ID 预检阶段 `KeyError`，未进入模型调用。
- R4 使用 PowerShell 字符串长度生成 manifest，被生产 loader 检出 Unicode 长度不一致，未进入模型调用。

这些失败没有污染 R5 的独立 Registry、manifest 或统计文件。

### 2.2 重要混杂因素

基线 FinalNarrow R1 之后、thinking 实验之前，工作区已按上一项用户要求完成两处 Package 语义回撤：关闭 N13 pair-local Apply，并局部恢复 Wave C R4/M0 父事件语义。此次没有回滚这些既有改动。

因此：

- Mention、Field、N9 位于 Package 之前，可主要用于判断 thinking 下调影响。
- Package、N13 Token 和全程耗时同时受到 thinking 与 Package 回撤影响，只能评价“当前组合方案”，不能做纯参数因果归因。
- R5 中 N13 判出 28 个 `SAME_PACKAGE`，但 28 条实际 redirect 全部是 `N12_WAVE_C_M0`，N13 Apply redirect=0，Apply 后 embedding=0，确认 N13 关闭确实生效。

## 3. 配置与真实 Schema Probe

| Tier | 上一轮 | 本轮 | 实现语义 |
| --- | --- | --- | --- |
| M2 | low | none | 发送 `thinking.type=disabled`，不发送 `reasoning_effort` |
| M3 | high | low | 发送 `thinking.type=enabled` + `reasoning_effort=low` |
| M4 | max | high | 发送 `thinking.type=enabled` + `reasoning_effort=high` |

真实 probe 文件：`C:\Users\WEIXUANXIE\Desktop\DoxAgent\.tmp\cdecr\thinking_tier_shift_20260804\schema_probe_m234.json`。三档 strict function Schema 均被服务端接受；M2 的 reasoning token 为空，M3/M4 非空。

## 4. 成功率、失败与 Evidence

| 指标 | 上一轮 | 本轮 | 变化 |
| --- | ---: | ---: | ---: |
| 单文档成功 | 30/30 | 30/30 | 持平 |
| 跨文档成功 | 30/30 | 30/30 | 持平 |
| 文档级失败 | 0 | 0 | 持平 |
| 幂等重跑增量 | 0 | 0 | 持平 |
| Evidence VERIFIED | 282/282 | 238/239 | 100% → 99.58% |

唯一 Evidence 异常是 `TEXT_NOT_FOUND`：模型把“Micron stock soared nearly 16%”与后半句财报描述拼接成一个非原文连续文本。该异常被局部隔离，没有造成文档失败。

模型调用层的失败虽未扩大为文档失败，但明显增加：

| 节点/错误 | 上一轮 | 本轮 |
| --- | ---: | ---: |
| Grounder `invalid_json` | 1 | 8 |
| Grounder missing recovery `invalid_json` | 1 | 5 |
| Grounder missing-item recovery `invalid_json` | 1 | 4 |
| Atomic coreference 非法输出 | 1 | 4 |
| Field coreference 非法结构 | 0 | 1 |
| Judge 非法 JSON | 4 | 0 |

审计同步显示 `GROUNDER_BATCH_DEGRADED` 1→8、`GROUNDER_DISPOSITION_DEGRADED` 2→9、`GROUNDER_MISSING_RECOVERY_FAILED` 1→5。根因不是 strict Schema 未被服务端接受，而是复杂 M3 任务在 low thinking 下更频繁地产生协议内无法解析/校验的内容，局部恢复机制虽保住了 30/30 成功率，却增加了补救调用并损伤质量稳定性。

## 5. Token 与耗时

### 5.1 总量

| 指标 | 上一轮 | 本轮 | 变化 |
| --- | ---: | ---: | ---: |
| 模型调用 | 780 | 792 | +12（+1.54%） |
| Input Token | 1,449,648 | 1,363,869 | -85,779（-5.92%） |
| Output Token | 1,874,586 | 1,064,405 | -810,181（-43.22%） |
| Total Token | 3,324,234 | 2,428,274 | -895,960（-26.95%） |
| 累计模型延迟 | 14,937,672 ms | 7,856,953 ms | -47.40% |
| 端到端墙钟 | 3,385,244 ms | 3,315,832 ms | -69,412 ms（-2.05%） |

`output_tokens` 包含 provider 计入输出的 thinking token；工作流 `model_calls` 表未单列保存每次 reasoning token，因此正式总量不能再拆出纯 reasoning 占比。真实 tier probe 只用于证明模式是否生效，不用于外推整轮 reasoning token。

### 5.2 主要节点占比

节点“耗时占比”按累计模型延迟计算；由于 BULK stage 存在并行，所有节点延迟不能相加解释成墙钟关键路径。表中只列本轮 Token 或累计延迟占比较高、以及变化具有诊断价值的节点。

| 节点 | 调用 | 本轮 Total Token | Token占比 | 环比 | 本轮模型延迟 | 延迟占比 | 环比 | 失败 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| N9 atomic coreference | 61 | 525,460 | 21.64% | -16.0% | 1,881,953 ms | 23.95% | -26.2% | 4 |
| Judge | 28 | 436,736 | 17.99% | +13.1% | 1,801,470 ms | 22.93% | +23.1% | 0 |
| Grounder | 30 | 343,545 | 14.15% | -48.5% | 1,366,713 ms | 17.39% | -63.9% | 8 |
| Field coreference | 240 | 283,982 | 11.69% | -13.8% | 342,544 ms | 4.36% | -56.0% | 1 |
| N9 escalation | 18 | 182,230 | 7.50% | -49.6% | 684,647 ms | 8.71% | -68.9% | 0 |
| N12 package assignment | 6 | 175,554 | 7.23% | -38.7% | 456,767 ms | 5.81% | -56.3% | 0 |
| Grounder item repair | 13 | 76,444 | 3.15% | -9.7% | 160,400 ms | 2.04% | -34.8% | 1 |
| Grounder missing recovery | 9 | 70,604 | 2.91% | +332.3% | 229,407 ms | 2.92% | +236.9% | 5 |
| Dreamer | 30 | 62,677 | 2.58% | -17.4% | 143,875 ms | 1.83% | -35.4% | 0 |
| Grounder missing-item recovery | 8 | 53,310 | 2.20% | +306.5% | 159,784 ms | 2.03% | +311.9% | 4 |
| N12 Wave C | 4 | 43,958 | 1.81% | -63.3% | 238,021 ms | 3.03% | -73.2% | 0 |
| N13 full Decide | 4 | 41,566 | 1.71% | -81.9% | 109,160 ms | 1.39% | -92.1% | 0 |

解读：

- 最大节省来自 M3 的长 reasoning 输出缩短，尤其 Grounder、N9 escalation、N12、N13。
- M3 Grounder 主请求虽然省下约 324k Token，但新增恢复链合计多消耗约 94k Token，并造成更多局部失败；净收益仍为正，可靠性代价却不可忽略。
- Judge 属 M4。由 max 降至 high 后，本轮调用少 2 次且失败从 4 降至 0，但 Token/累计延迟反而上升 13.1%/23.1%；说明该节点存在显著请求内容和模型随机性影响，不能仅凭本轮断言 M4-high 更省。
- N13 的 -81.9% Token 不全是 thinking 收益：本轮 N13 pair-local Apply 已关闭、候选对也减少，属于组合效果。
- 累计延迟下降 47.4% 而墙钟只下降 2.05%，表明后续效能优化应针对最长文档/最长批次的关键路径和 recovery fan-out，而不是继续全局压低 thinking。

## 6. 节点质量 A/B

### 6.1 Mention

| 指标 | 上一轮 | 本轮 | 变化 |
| --- | ---: | ---: | ---: |
| Gold | 268 | 268 | 0 |
| 输出 Mention | 260 | 229 | -31 |
| Strict TP | 186 | 183 | -3 |
| Partial | 51 | 36 | -15 |
| FP | 23 | 10 | -13 |
| FN | 82 | 85 | +3 |
| Precision | 71.54% | 79.91% | +8.37pp |
| Recall | 69.40% | 68.28% | -1.12pp |
| F1 | 70.45% | 73.64% | +3.19pp |

本轮不是全面变差，而是输出明显收缩：少输出 31 条换来 13 个 FP 的减少，但 strict TP 也少 3、FN 多 3。Precision 的改善是真实收益，Recall 未达到既定 85% 目标且略有回归。考虑 Dreamer 使用 M2-off、Grounder 使用 M3-low，无法从一次整链 A/B 精确拆分二者贡献；但 Grounder 失败/补恢复显著增加，是 Recall 风险的直接证据。

### 6.2 Field

| Field | 上一轮 | 本轮 | 变化 | 既定门槛 | 结果 |
| --- | ---: | ---: | ---: | ---: | --- |
| predicate | 78.36% | 73.88% | -4.48pp | ≥90% | 未通过 |
| participant | 77.15% | 73.03% | -4.12pp | ≥96% | 未通过 |
| metric | 73.37% | 72.92% | -0.45pp | ≥90% | 未通过 |
| fiscal period | 87.01% | 89.47% | +2.46pp | ≥80% | 通过 |
| total | 77.56% | 74.84% | -2.71pp | ≥91% | 未通过 |

Field evaluator 使用同一 Gold 定义。上游 Mention 集合变化会改变可评估 occurrence，但 predicate/participant 的 4pp 级下降仍足以判定质量风险；M2-off 对 Field coreference 的直接影响也不能排除。

### 6.3 N9 / Identity

| 指标 | 上一轮 | 本轮 | 变化 | 目标 | 结果 |
| --- | ---: | ---: | ---: | ---: | --- |
| Candidate coverage | 100.00% | 99.56% | -0.44pp | 100% | 未通过 |
| MERGE Precision | 98.61% | 100.00% | +1.39pp | >75% | 通过 |
| Conditional MERGE Recall | 89.87% | 84.00% | -5.87pp | >85% | 未通过（差1pp） |
| CREATE_NEW accuracy | 95.74% | 92.77% | -2.97pp | — | 回归 |

本轮 63 个正确 MERGE、0 个错误 MERGE；上一轮为 71/1。模型更谨慎，确实消除了这一可评估集中的错误合并，但少召回 8 个正确 merge。结果符合“thinking 下调导致语义裁量趋于保守”的风险模式，不适合仅以 100% Precision 判定成功。

### 6.4 Package 与碎片化

为避免上下游 Atomic 数量变化造成不公平比较，正式环比采用双向互选且 Gold 可判的 62 个共同 Atomic；另报告本轮发布口径，但不与不同覆盖集直接作差。

| 同集指标（62 Atomic） | 上一轮 | 本轮 | 变化 |
| --- | ---: | ---: | ---: |
| Pair Precision | 89.47% | 95.22% | +5.75pp |
| Pair Recall | 77.02% | 77.35% | +0.32pp |
| Pair F1 | 82.78% | 85.36% | +2.57pp |
| TP / FP / FN | 238 / 28 / 71 | 239 / 12 / 70 | FP -16，FN -1 |
| 多事件 Gold 组 | 5 | 5 | 持平 |
| 被碎片化 Gold 组 | 3 | 2 | -1 |
| Excess components | 5 | 4 | -1 |
| 多事件组内 singleton components | 7 | 5 | -2 |
| Missed pair links | 71 | 70 | -1 |

本轮发布口径的 85 个高置信对齐 Atomic 上，Package P/R/F1 为 **91.07%/75.83%/82.75%**。Precision 达到 ≥90% 门槛，但 Recall 未达到 ≥82% 或期望的 ≥80% 上沿目标。

碎片化只小幅改善：IDC 双事件组恢复聚合，但 Micron FQ3 earnings 仍是 4 个组件（22+1+1+1），产生 69 个 missed links，未达到“不超过 3 个组件”的目标；memory supplier reallocation 仍被拆成两个 singleton。旧的 KOSPI/Nikkei/Dow/Stoxx 五市场大簇没有形成。

仍存在的主要 false-parent merge 包括：

- Apple 产品涨价、IDC ASP 展望、Counterpoint 成本估计被合为同一父事件；
- Sandisk scheduled earnings 与 SCA；
- Qualcomm 数据中心战略与 Q2 handset revenue；
- Stoxx 600 与 SK Hynix/Samsung 个股表现；
- Alphabet 收盘与 Microsoft 收盘。

这说明恢复 Wave C 的 shared anchor/artifact 父事件语义改善了 Recall/碎片化，却仍允许部分共同来源或共同主题越过真实 parent boundary。该现象属于 Package 回撤组合效果，不能据此判断 M3-low 本身改善了 Package。

## 7. 参数保留与回滚必要性判断

本轮只评估，不自动回滚。建议如下：

| 配置 | 判断 | 依据 |
| --- | --- | --- |
| M2 = thinking off | **不建议按全局默认直接放量** | Mention Precision 提升但 Recall 下降；Field total -2.71pp；N9 Recall -5.87pp。应把 off 限定在机械、低语义风险任务，对 Dreamer/Field/N9 至少恢复 low 后做窄 A/B。 |
| M3 = low | **建议优先局部回升** | Grounder invalid JSON 1→8，missing recovery/repair fan-out 明显增加；虽节省大量 Token，但可靠性和 Recall 代价已经显现。复杂 Grounder 建议恢复 high；Wave C/N13 等 compact pair 任务可单独保留 low 再测。 |
| M4 = high | **可暂留观察，不宣称已降本** | Judge 失败 4→0，但 Token +13.1%、累计延迟 +23.1%；本轮没有证明恢复 max 会改善业务质量，也没有证明 high 更便宜。 |

全局结论是：**“一档整体下调”在成本上成功，在端到端时延上收益有限，在质量与结构化可靠性上未通过。** 最合理的后续不是整体回到旧配置，也不是原样全量保留，而是按节点复杂度拆分 effort：低风险 DTO/短分类保留低 thinking，Grounder、N9 等复杂且影响召回的节点恢复一档，之后仅重跑相应冻结节点 A/B 再决定是否启动完整 30 篇。

## 8. 可复现产物

正式运行目录：`D:\DoxAgent_CDECR_Acceptance_20260804_ThinkingDownshift_R5`

- `fixed_30_step4_manifest.json`：与基线一致的 30 篇 manifest。
- `cdecr_30_thinking_downshift.sqlite3`：独立 Registry。
- `cdecr_30_thinking_downshift_report.json`：正式运行报告与 stage metrics。
- `diagnostics.json`：Token、延迟、失败、Evidence、Atomic/Package 汇总。
- `mention_gold_eval.json`、`field_gold_eval.json`、`n9_gold_eval.json`、`package_gold_eval.json`：各节点 Gold 评估。
- `package_finalnarrow_mutual_atomic_comparison.json`：62 Atomic 同集 Package/碎片化比较。
- `thinking_downshift_comparison_summary.json`：本报告的机器可读比较总表。
- `logs/`：完整运行日志。

代码验证：`tests/cdecr/test_boundary_and_models.py` 21 项通过，Ruff 通过；真实 M2/M3/M4 strict Schema probe 三项通过。
