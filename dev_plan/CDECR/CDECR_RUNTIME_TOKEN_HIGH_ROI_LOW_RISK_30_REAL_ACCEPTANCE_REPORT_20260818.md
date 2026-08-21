# CDECR Runtime Token 高收益优化：30篇真实验收报告

## 1. 结论

本轮使用固定30篇语料、全新 Registry 和百炼真实模型完成了完整工作流与 Gold 评估。正式结果为30/30文档成功、30/30事件成功，但总体 Gate 为 **FAIL**。

三个问题的直接结论：

1. **Grounder PRIMARY“无损规范化”不得进入 Apply，也不需要删除 shadow。** 本轮29条shadow记录中20条符合静态窄规则，理论上可避免20次单条repair；但当前审计没有持久化“原draft与repair后结果”的字段级对照，且4个repair任务仍产生partial结果，无法证明≥99.5%一致率和0语义漂移。正确动作是继续shadow并补齐对照证据，而不是启用或删除规则。
2. **Token与钟墙收益不符合预期。** 可审计总Token为1,737,351，较基线1,718,846增加1.08%；首轮钟墙25.95分钟，较17.72分钟增加46.47%。更严重的是96次Field逐项repair没有写入`model_calls`，所以1.737M只是下界，真实Token成本更高。
3. **当前不可启动MU300。** Field planned batching存在串行准备和repair审计/降级问题；Package Recall仅32.12%；N9 conditional Merge Recall降至75.79%；恢复过程中还暴露了28篇派生状态无法安全扩为30篇的`DERIVED_STATE_REBUILD_REQUIRED`问题。

本轮不执行任何自动回滚或业务修改。

## 2. 固定输入与产物

- Snapshot：`.tmp/cdecr/baselines/us_mu_2026-06-25.jsonl`
- Manifest：`.tmp/cdecr/parent_v2_fixed_30_manifest.json`
- 正式 Registry：`.tmp/cdecr/mu30_runtime_token_optimization_20260818_r2.sqlite3`
- Runtime report：`.tmp/cdecr/mu30_runtime_token_optimization_20260818_r2_report.json`
- Mention Gold：`.tmp/cdecr/mu30_runtime_token_optimization_20260818_r2_mention_gold_eval.json`
- Field Gold：`.tmp/cdecr/mu30_runtime_token_optimization_20260818_r2_field_gold_eval.json`
- N9 Gold：`.tmp/cdecr/mu30_runtime_token_optimization_20260818_r2_n9_gold_eval.json`
- Package Gold：`.tmp/cdecr/mu30_runtime_token_optimization_20260818_r2_package_gold_eval.json`
- 对比基线：`.tmp/cdecr/mu30_selective_recovery_20260817_r1.sqlite3`及对应报告

正式R2使用全新 Registry；幂等复验model call、Mention、Atomic、Package增量均为0。

第一次运行曾因1次`provider_arrearage`和1次`provider_datainspectionfailed`只完成28篇。补齐2篇后，旧Registry因派生输入从28变30而在Atomic前以`DERIVED_STATE_REBUILD_REQUIRED`停止。该失败库被保留为证据，没有作为正式结果；正式R2从全新Registry运行。

## 3. 完整性与可靠性

| 指标 | 基线 | 本轮 | 判断 |
| --- | ---: | ---: | --- |
| 文档成功 | 30/30 | 30/30 | 通过 |
| Event成功 | 30/30 | 30/30 | 通过 |
| Mention schema / Evidence span | 100% / 100% | 100% / 100% | 通过 |
| Mention | 214 | 297 | 输出规模显著变化 |
| Active Atomic | 122 | 221 | 明显更碎 |
| Package | 28 | 44 | 明显更碎 |
| 记录到的失败模型调用 | 1 | 4 | 回归但均局部化 |
| `invalid_json` | 0 | 2（Judge） | 局部恢复，仍需观察 |
| 幂等二跑 | 0 delta | 0 delta | 通过 |

本轮4条已记录失败为：Dreamer `provider_datainspectionfailed` 1次、Field `provider_arrearage` 1次、Judge `invalid_json` 2次。Dreamer/Judge均局部恢复；Field的一次provider失败使8个Field逻辑任务失败，但没有扩大成文档或epoch失败。

## 4. PRIMARY shadow判定

### 4.1 本轮证据

- `GROUNDER_PRIMARY_NORMALIZATION_SHADOW`：29条；
- 静态安全候选：20条（68.97%）；
- 非安全候选：9条，均因quantity数量不为1；
- Grounder非法draft：27条，其中25个含`PRIMARY_QUANTITY_COUNT`；
- Grounder item repair：27次，记录Token 110,041；
- 20条安全候选按平均repair成本估算，潜在可省约81.5k Token，但这只是机械估算；
- 4个repair任务产出partial结果；有7条安全shadow候选落在其中2个run内，现有审计无法逐draft确认最终去向。

### 4.2 为什么现在不能启用

当前shadow只记录quantity数、PRIMARY数、静态安全判定和`would_avoid_repair`，没有记录：

- repair最终是保留该quantity、删除quantity还是改变metric/value/period；
- proposed PRIMARY结果与repair结果的字段级diff；
- Evidence、metric、fiscal period是否发生漂移；
- 额外开放世界样本的一致率。

因此本轮只能证明“20条满足窄静态前提”，不能证明计划要求的“字段级一致率≥99.5%且0语义漂移”。

### 4.3 决策

**不启用Apply；保留shadow。** shadow当前不改变业务结果，回滚它没有质量收益；下一步应为shadow增加短小的编排层post-repair comparison，再用固定30篇加开放世界样本判定。已启用的shape normalizer本轮仅命中1条，安全删除冗余Evidence char字段并避免1次repair，可继续保留。

## 5. 效能与Token

### 5.1 总体A/B

| 指标 | 基线 | 本轮可审计值 | 环比 | 门槛 | 结论 |
| --- | ---: | ---: | ---: | ---: | --- |
| 首轮钟墙 | 1,063,198 ms / 17.72 min | 1,557,222 ms / 25.95 min | +46.47% | ≤18.6 min | 失败 |
| 模型记录 | 556 | 493 | -11.33% | — | 表面下降 |
| Input Token | 1,325,375 | 1,348,176 | +1.72% | — | 回归 |
| Output Token | 393,471 | 389,175 | -1.09% | — | 小幅改善 |
| Total Token | 1,718,846 | 1,737,351 | +1.08% | ≤1.42M | 失败 |
| 累计已记录模型延迟 | 4,147,314 ms | 3,712,935 ms | -10.47% | — | 改善但未转化为钟墙 |

上述本轮Token是**下界**：Field的96次真实`field_coreference_item_repair`物理请求出现在executor telemetry中，但没有写入`model_calls`，其Token没有进入`call_budget`。

Gold评估的额外模型调用不计入生产工作流：Mention 156,065 Token、Field 147,927 Token、N9 259,098 Token，合计563,090 Token；Package评估为本地计算。

测试执行过程中保留的第一次28篇失败/补齐库另记录1,440,954 Token；它包含一次28篇全局epoch和一次终止于Atomic前的30篇Field重建，不能并入正式R2 A/B，但属于本次真实发生的恢复开销。该库同样存在未记账的Field item repair，因此本次测试活动的实际Provider消耗高于“正式R2 1.737M + 失败库1.441M + Gold评估0.563M”。

### 5.2 各节点族

| 节点族 | 基线Token | 本轮已记录Token | 环比 | 本轮占比 |
| --- | ---: | ---: | ---: | ---: |
| N9 / Atomic | 451,070 | 518,314 | +14.91% | 29.83% |
| Grounder及恢复 | 386,551 | 374,012 | -3.24% | 21.53% |
| Parent | 310,323 | 324,934 | +4.71% | 18.70% |
| Judge及恢复 | 233,486 | 263,537 | +12.87% | 15.17% |
| Field（不含96次漏记repair） | 148,423 | 122,770 | -17.28% | 7.07% |
| Relevance | 84,486 | 51,716 | -38.79% | 2.98% |
| Dreamer | 67,609 | 65,039 | -3.80% | 3.74% |
| Package V3 | 35,857 | 15,988 | -55.41% | 0.92% |

局部有效项：

- Relevance reasoning Token=0，且总Token下降38.79%；
- Parent Repartition调用=0；
- Parent main input/call从约8,917降至6,139，Compact Wire单请求输入下降约31%；
- N9 overlap packing保持batch=3和exact coverage，card重复减少35.24%；按N9评估task归一后，core+escalation Token/task约下降20%；
- Package V3模型累计延迟从352.3秒降至75.7秒。

但这些局部收益被Field回归和更大的上游输出规模抵消。

### 5.3 Field是钟墙回归的确定根因

| 阶段 | 基线 | 本轮 | 变化 |
| --- | ---: | ---: | ---: |
| Field | 342.16 s | 1,032.16 s | +201.66% |
| N9 main | 101.68 s | 82.62 s | -18.74% |
| N9 late | 22.45 s | 9.86 s | -56.06% |
| Parent + Package | 503.48 s | 298.15 s | -40.78% |
| Bulk总计 | 947.67 s | 1,413.23 s | +49.13% |

新Field telemetry显示：

- planned prepare：859.63秒；
- LLM batch decide：19.09秒；
- Apply：59.83秒；
- 37个主批次，batch平均7.78、P50=8、P95=12；
- 513个planned item；
- 96次逐项repair；
- 43项repair后仍fallback为UNRESOLVED。

代码根因清晰：planned路径在`canonical_field_resolution.py`中用普通`for group in pending_groups`串行执行`_prepare_field_groups`；旧路径则通过`ThreadPoolExecutor`并发执行每个group。也就是说，LLM请求被批处理了，但原本并行的候选准备/recall路径被改成串行，859.63秒直接落在关键路径上。

此外，Field主批次的部分item输出不能通过adapter，触发96次单条repair；其中43次repair再次返回非法结果并被确定性降级为UNRESOLVED。repair请求虽然实际发生，却未写入`model_calls`，同时缺少初次invalid item的结构化错误分布，因此当前既低估成本，也不足以精确定位模型究竟违反了哪一条wire约束。

### 5.4 方案效能Gate

| Gate | 本轮 | 结论 |
| --- | ---: | --- |
| 总Token ≤1.42M | ≥1.737M | 失败 |
| 钟墙 ≤18.6 min | 25.95 min | 失败 |
| Parent Repartition=0 | 0 | 通过 |
| Relevance reasoning=0 | 0 | 通过 |
| Field主调用≤30 | 37；实际另有96 repair | 失败 |
| Field平均batch≥5 | 7.78 | 表面通过，但repair抵消收益 |
| Grounder repair Token≤110k | 110,041 | 极窄失败 |
| 失败请求Token占比≤1% | 已记录0.94% | 通过，但总分母漏记Field repair |

## 6. 质量A/B

### 6.1 Mention

| 指标 | 基线 | 本轮 | 变化 | 目标 |
| --- | ---: | ---: | ---: | ---: |
| Precision | 66.82% | 60.27% | -6.55pp | >90% |
| Recall | 53.36% | 66.79% | +13.43pp | >85% |
| F1 | 59.34% | 63.36% | +4.02pp | — |
| Gold / Output | 268 / 214 | 268 / 297 | +83 output | — |

Recall明显改善，但以Precision显著下降为代价，未满足“不能为提Recall牺牲Precision”的全局约束。该变化发生在真实模型波动和上游输出规模变化下，不能单独归因于本轮runtime优化；但结果本身不具发布资格。

### 6.2 Field

| Field | 基线 | 本轮 | 变化 | 门槛 |
| --- | ---: | ---: | ---: | ---: |
| predicate | 86.05% | 83.67% | -2.38pp | 90% |
| participant | 87.79% | 81.05% | -6.74pp | 96% |
| metric | 78.74% | 78.87% | +0.13pp | 90% |
| fiscal period | 72.29% | 77.55% | +5.26pp | 80% |
| total | 82.85% | 80.91% | -1.94pp | 91% |

Field total下降超过方案允许的1pp；participant回归尤其明显。43项UNRESOLVED fallback和8个provider失败task是直接风险源，但由于缺少item级错误/结果闭环，不能把全部下降精确归因于这51项。

### 6.3 N9 / Atomic

| 指标 | 基线 | 本轮 | 变化 |
| --- | ---: | ---: | ---: |
| Candidate coverage | 100% | 99.66% | -0.34pp |
| Merge Precision | 91.30% | 96.00% | +4.70pp |
| Conditional Merge Recall | 87.50% | 75.79% | -11.71pp |
| CREATE_NEW accuracy | 90.16% | 89.64% | -0.52pp |
| Active Atomic | 122 | 221 | +99 |
| singleton Atomic | 92/122=75.41% | 192/221=86.88% | +11.47pp |
| 最大Atomic | 30 | 15 | 改善 |
| hard-cannot-link violation | 407/21,021=1.94% | 160/41,345=0.39% | 明显改善 |
| reaction-in-earnings | 4/46=8.70% | 7/68=10.29% | 回归 |

本轮显著减少了最大污染簇和hard conflict，但代价是过度拆分：N9 conditional recall下降11.71pp，Atomic singleton升至86.88%。N9 packing的exact coverage成立，质量变化主要不是候选漏传，而是模型判定与输入规模波动；无论归因如何，该结果不满足进入压测的质量门槛。

### 6.4 Package

本轮高置信对齐85/221=38.46%，只代表可判子集；与基线50/122的对齐集合不同，因此环比仅作方向性证据。

| 指标 | 基线 | 本轮 | 判断 |
| --- | ---: | ---: | --- |
| Pair Precision | 97.79% | 89.80% | 跌破90% |
| Pair Recall | 75.32% | 32.12% | 严重失败 |
| Pair F1 | 85.10% | 47.31% | 严重回归 |
| Package | 28 | 44 | 更碎 |
| singleton Package | 10/28=35.71% | 14/44=31.82% | 比例表面改善 |
| 最大Package | 52 | 49 | 略降 |
| fragmented Gold groups | 2/4 | 6/8 | 回归 |
| Micron earnings components | 5 `[27,1,1,1,1]` | 9 `[24,6,4,3,2,2,1,1,1]` | 严重回归 |
| Micron earnings missed links | 114 | 644 | 严重回归 |

本轮的主要Package问题不是singleton比例，而是多个非singleton子簇之间没有收敛，同时仍存在SCA、memory supply、stock reaction等跨边界误合。Package Recall 32.12%单独即可否决MU300。

## 7. 必须修复的问题与MU300判断

### P0-1：修复Field planned prepare串行化

在保持“先计划、后批量Decide、稳定Apply”的前提下，把每个Field semantic group的纯准备阶段恢复为有界并发；不得并发写Registry。应先并行得到immutable prepared plan，再统一排序、批量LLM Decide、串行/稳定Apply。必须用冻结Field plan验证结果hash不因完成顺序变化。

### P0-2：修复Field item repair合同与成本审计

1. 为每个initial invalid item记录有限的validator error code、task ID和namespace；
2. 查明为什么288个送模item触发96次repair，以及为什么43次repair仍因`ValueError`降级；
3. repair使用单item本地ID合同，不能沿用导致模型/validator错位的batch位置ID；
4. 96次repair必须全部进入`model_calls/call_budget`；
5. provider级批失败继续只影响该批，不得扩成文档失败，但不可把批失败伪装成普通UNRESOLVED成功。

### P0-3：修复派生状态扩容恢复

28篇epoch已FINALIZED后补齐2篇，当前会在Field完成后以`DERIVED_STATE_REBUILD_REQUIRED`停止，无法安全从Atomic继续。这会使MU300的局部失败恢复退化为全新Registry重跑。进入压测前必须明确支持“固定manifest内失败文档补齐后，从Field/Atomic安全重建派生层”，或在首次全局epoch前阻止不完整文档集合固化；不能让旧28篇Package继续冒充30篇结果。

### P0-4：质量门槛

- Package Recall 32.12%必须恢复；
- N9 conditional Merge Recall 75.79%必须至少回到既有门槛；
- Field total与participant不得继续被fallback拉低；
- Mention Precision不能以6.55pp下降换取Recall提升。

### P1：PRIMARY shadow证据闭环

增加本地post-repair comparison，仅记录字段diff和一致/漂移代码，不增加LLM payload。达到固定30篇加开放世界样本≥99.5%一致、0 Evidence/metric/period漂移后，才讨论启用。

## 8. 最终发布判断

当前版本不能启动MU300。原因不是“指标略低”，而是同时存在：

- 一个确定性的Field串行钟墙回归；
- 96次未计费审计的repair与43次UNRESOLVED降级；
- 不可靠的总Token统计；
- 补齐失败文档后无法安全重建派生层；
- Package Recall、N9 Recall、Field质量均未过门槛。

建议修复顺序为：Field并发准备与repair合同/审计 → 30篇冻结Field A/B → 完整30篇质量复验 → 派生恢复演练 → 再决定是否进入MU300。PRIMARY规则继续shadow，不进入Apply，也不需要回滚shadow实现。
