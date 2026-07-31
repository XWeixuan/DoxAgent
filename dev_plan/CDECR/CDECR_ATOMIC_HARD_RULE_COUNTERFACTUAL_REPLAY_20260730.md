# CDECR Atomic Hard Rule 反事实回放报告（2026-07-30）

## 1. 结论

本次测试没有重新调用 LLM，只回放本轮固定的 199 条 Mention、N7/N9 结果、Sidecar/Invariant 审计、最终 Atomic，以及上一轮 30 篇 Gold。

结论是：

1. **目前没有任何一条规则可以直接全量升级为 hard rule。**
2. `COMPLETE_REFERENT` 是唯一表现出明确净收益的规则：Gold 可判范围内阻止 10 个错误 pair、误杀 0 个正确 pair，Pair Precision 预计由 56.45% 提升至 61.40%，Pair Recall 不变。但它只有 3 次实际 MERGE 触发，其中一条缺少稳定 Gold，因此只建议继续 shadow，并在补判后做窄场景 canary。
3. `MARKET_MEASURE` 不应启用：它阻止 2 个错误 pair，却误杀 3 个正确的 Micron after-hours pair，Pair Recall 下降 1.68 个百分点。
4. `ASSERTION_STATE` 不应作为当前宽口径 hard rule：虽然阻止 25 个错误 pair，但同时误杀 7 个正确 pair，包括同一 SCA 主事实和同一 FY2026 capex plan。
5. `PRIMARY_METRIC_FAMILY`、`ISSUER`、`MARKET_SESSION`、`ANALYST_INSTITUTION`、`ACCOUNTING_BASIS` 对本轮实际 MERGE 均为 **0 次触发**。它们不是“通过”，而是本轮没有形成有效覆盖。
6. 候选替代回放没有改变上述主规则结论：这些规则命中的 task 中不存在可用的第二个、不触发同规则的 N9 `SAME_EVENT` 候选。

因此，本轮可采取的最合理动作不是批量启用 Sidecar hard rules，而是：

- 保持所有规则 shadow；
- 优先补齐 `COMPLETE_REFERENT` 的未判 Gold，并仅考虑“主体完整且明确不同”的窄启用；
- 修正 `MARKET_MEASURE` 的语义：不能因数值表达不同就拆分同一市场变动；
- 提升五条零触发规则的 Sidecar 轴覆盖后再回放，而不是以本轮零误杀作为上线依据。

## 2. 输入与控制变量

固定输入：

- 本轮 Registry：`.tmp/cdecr/acceptance_20260730/cdecr_30_20260730.sqlite3`
- 本轮运行报告：`.tmp/cdecr/acceptance_20260730/cdecr_30_20260730_report.json`
- 上一轮 Atomic Gold：`.tmp/cdecr/resilience/atomic_review_all.json`
- 上一轮 Mention Gold：`.tmp/cdecr/resilience/mention_review_01_10.json`、`11_20.json`、`21_30.json`

控制变量：

- 199 条 Mention 全部固定；
- 131 条原始 `CREATE_NEW` 和 68 条原始 `MERGE` 固定为回放起点；
- 45 条 `N9_INVALID_TASK_CREATE_NEW` 固定为本轮原结果，不把其 fragmentation 归因于规则；
- 每次只启用一条规则；
- 未被该规则命中的 assignment 完全保持原行为；
- 没有重新执行 N7、N9 或任何模型请求。

Gold 对齐采用同 source message 的 Mention Embedding 一对一匹配，阈值为 cosine ≥ 0.90。199 条本轮 Mention 中有 148 条可与上一轮 Gold 稳定对齐，最低相似度为 0.9039。Pair P/R 的反事实变化只在这 148 条稳定子集上计分；无法稳定对齐的 pair 单列为不可判，不用于宣称规则有效。

## 3. 原始回放校验

按 assignment 原始顺序回放后：

| 指标 | Registry 最终结果 | 原始回放 |
| --- | ---: | ---: |
| Atomic 数 | 131 | 131 |
| 最大 Atomic | 12 | 12 |
| singleton Atomic | 102 | 102 |
| Gold 稳定子集 TP / FP / FN | 70 / 54 / 109 | 70 / 54 / 109 |
| Pair Precision | 56.45% | 56.45% |
| Pair Recall | 39.11% | 39.11% |
| Pair F1 | 46.20% | 46.20% |
| hard-conflict violation | 158 | 158 |

原始分区与 Registry 最终分区一致，说明以下差异来自规则回放，而不是回放器改变了既有业务路径。

## 4. 逐规则 Gold 结果

下表的“触发”是被规则阻断的 N9 MERGE assignment 数；“阻止 FP / 误杀 TP”是该 assignment 与当时目标 cluster 展开后，在稳定 Gold 子集上实际改变的 pair 数。一次 cluster MERGE 可能对应多个 pair，因此 pair 数可以大于触发数。

| Rule | 触发 MERGE | 阻止 FP pair | 误杀 TP pair | Pair 阻断精度 | Pair P 变化 | Pair R 变化 | 结论 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| `PRIMARY_METRIC_FAMILY` | 0 | 0 | 0 | 无覆盖 | 0 | 0 | 不可判，保持 shadow |
| `ASSERTION_STATE` | 8 | 25 | 7 | 78.13% | +12.03pp | -3.91pp | 不可 hard enable |
| `ISSUER` | 0 | 0 | 0 | 无覆盖 | 0 | 0 | 不可判，保持 shadow |
| `MARKET_SESSION` | 0 | 0 | 0 | 无覆盖 | 0 | 0 | 不可判，保持 shadow |
| `MARKET_MEASURE` | 5 | 2 | 3 | 40.00% | -0.15pp | -1.68pp | 明确不可启用 |
| `ANALYST_INSTITUTION` | 0 | 0 | 0 | 无覆盖 | 0 | 0 | 不可判，保持 shadow |
| `ACCOUNTING_BASIS` | 0 | 0 | 0 | 无覆盖 | 0 | 0 | 不可判，保持 shadow |
| `COMPLETE_REFERENT` | 3 | 10 | 0 | 100%（可判 pair） | +4.95pp | 0 | 补判后可做窄 canary |

`COMPLETE_REFERENT` 的 3 次触发中，有 2 次形成了 10 个稳定 Gold pair，全部是应阻断的错误合并；另 1 次涉及未稳定对齐的 provisional target，不能计入 100% 有效证据。因此它尚未满足“所有触发均可判、零误杀”的 hard-rule 上线条件。

作为兼容性审计，还回放了旧版 shadow conflict：

| Legacy Rule | 触发 MERGE | 阻止 FP pair | 误杀 TP pair | Pair 阻断精度 | 结论 |
| --- | ---: | ---: | ---: | ---: | --- |
| `METRIC` | 3 | 9 | 2 | 81.82% | 会拆散正确的 84.9% adjusted/unspecified GM |
| `EVENT_TIME` | 7 | 7 | 17 | 29.17% | 大量拆散同一 Q3 revenue、after-hours、capital return |
| `NORMALIZED_PREDICATE` | 10 | 24 | 21 | 53.33% | 大量拆散正确 SCA 主事实 |

这些 legacy 规则都不适合作为宽口径 hard rule。尤其是 `EVENT_TIME`：时间表达差异不能等价为事件不同；它将 Pair Recall 从 39.11% 降至 29.61%。

## 5. 两种 cluster 回放

| Rule | 策略 | Atomic 数 | 最大 cluster | singleton | hard-conflict violation |
| --- | --- | ---: | ---: | ---: | ---: |
| Baseline | 原结果 | 131 | 12 | 102 | 158 |
| `ASSERTION_STATE` | CREATE_NEW / 候选替代 | 139 | 10 | 111 | 109 |
| `MARKET_MEASURE` | CREATE_NEW / 候选替代 | 136 | 12 | 110 | 150 |
| `COMPLETE_REFERENT` | CREATE_NEW / 候选替代 | 134 | 11 | 106 | 146 |
| Legacy `METRIC` | CREATE_NEW / 候选替代 | 134 | 12 | 105 | 149 |
| Legacy `EVENT_TIME` | CREATE_NEW | 138 | 12 | 110 | 133 |
| Legacy `EVENT_TIME` | 候选替代 | 137 | 12 | 108 | 133 |
| Legacy `NORMALIZED_PREDICATE` | CREATE_NEW | 141 | 10 | 112 | 97 |
| Legacy `NORMALIZED_PREDICATE` | 候选替代 | 140 | 10 | 111 | 97 |

对主 Sidecar 规则，候选替代版与保守版相同：被阻断 task 没有第二个可用且不触发同规则的 N9 `SAME_EVENT` 候选。Legacy `EVENT_TIME` 和 `NORMALIZED_PREDICATE` 各只有 1 个 assignment 找到替代候选，仍不足以挽回其 Recall 损失。

不能只看 violation 数下降。`NORMALIZED_PREDICATE` 将 violation 从 158 降至 97，但同时误杀 21 个正确 pair，Pair Recall 跌至约 27.37%。这正是“结构审计变好、业务质量反而变差”的典型反例。

## 6. 重点事实组

- **Q3 revenue / EPS 25.11**：`PRIMARY_METRIC_FAMILY` 零触发，因此没有修复 Q3 revenue、EPS、capex 混合风险。Legacy `METRIC` 能拆出 capex和 compound gross-margin 错误，但同时误杀 2 个正确 84.9% gross-margin pair，不能整体启用。
- **Micron after-hours**：`MARKET_MEASURE` 把 “15%”“more than 15%”“surges to new highs” 当成不同 measure，误拆同一次 after-hours price move 的 3 个 Gold TP pair。这条规则当前方向错误。
- **SCA**：`COMPLETE_REFERENT` 正确排除 Sandisk 与 Micron SCA，并拆开 Roundhill/Defiance ETF，最大 cluster 从 12 降至 11；`ASSERTION_STATE` 和 `NORMALIZED_PREDICATE` 虽能清除部分污染，却同时拆散同一 signed-SCA 主事实。
- **GAAP / adjusted margin**：`ACCOUNTING_BASIS` 本轮零触发，未形成有效验收覆盖。Legacy `METRIC` 对 adjusted 与未显式写 basis、但 Gold 视为同一事实的 84.9% margin 发生误杀。
- **market session / analyst institution / issuer**：规则零触发不表示这些边界没有问题，只表示当前 Sidecar 没有在本轮实际 MERGE 上形成可测试信号。

## 7. 启用门槛判断

| Rule | 是否达到 99.5% | 是否零误杀 | 最终建议 |
| --- | --- | --- | --- |
| `COMPLETE_REFERENT` | 可判 pair 为 100%，但 1 次触发未判 | 是（已判范围） | 保持 shadow；补判后仅对完整、明确不同主体做窄 canary |
| `MARKET_MEASURE` | 否，40.00% | 否 | 不启用；先改为“measure 类型冲突”，禁止仅凭数值表达差异阻断 |
| `ASSERTION_STATE` | 否，78.13% | 否 | 不启用；只保留明确 ACTUAL vs GUIDANCE 且核心事实相同可比的窄规则研究 |
| `PRIMARY_METRIC_FAMILY` | 无覆盖 | 无法判断 | 保持 shadow；先修复 Sidecar primary metric 覆盖 |
| `ISSUER` | 无覆盖 | 无法判断 | 保持 shadow；补充跨 issuer 候选测试 |
| `MARKET_SESSION` | 无覆盖 | 无法判断 | 保持 shadow；补充同标的跨 session Gold |
| `ANALYST_INSTITUTION` | 无覆盖 | 无法判断 | 保持 shadow；补充跨机构研报 Gold |
| `ACCOUNTING_BASIS` | 无覆盖 | 无法判断 | 保持 shadow；补充 GAAP/adjusted 明确对照 Gold |

最终判断：**本次回放支持继续开发 Sidecar，但不支持把现有规则集合整体启用。唯一值得进入下一步的是经过进一步收窄和补判的 `COMPLETE_REFERENT`；其余规则要么已证明会伤 Recall，要么尚无覆盖证据。**

## 8. Sidecar 下一阶段优化方案

本节基于后续业务决策更新，作为实施依据；第 1–7 节保留原始反事实测试结论，不据此限制本节明确放宽的启用范围。

### 8.1 决策范围

根据后续业务决策，下一阶段允许启用：

- `ASSERTION_STATE`
- `COMPLETE_REFERENT`
- 旧版 shadow conflict 中、仅来自高可信 Compiled Identity 的 `METRIC`
- `PRIMARY_METRIC_FAMILY`

继续保持 shadow：

- `MARKET_MEASURE`
- `MARKET_SESSION`
- `ISSUER`
- `ANALYST_INSTITUTION`
- `ACCOUNTING_BASIS`
- 旧版 `EVENT_TIME`、`NORMALIZED_PREDICATE` 及其余 conflict

这里需要明确一个技术边界：反事实测试显示，**不加限制地启用旧版 `ASSERTION_STATE` 会误杀正确合并**，因为当前实现把任意已知 assertion state 不同都视为冲突，包括 `EXPECTED` vs `PLANNED`、`ONGOING` vs `ACTUAL` 等可能描述同一事实或同一持续状态的组合。因此本方案将“启用 `ASSERTION_STATE`”解释为启用经过兼容矩阵收窄的版本，而不是恢复旧版全枚举不等即阻断的逻辑。

### 8.2 问题 A：高可信 Metric 没有进入 FACET

实际发生路径如下：

1. Field Resolution 已把 Q3 revenue、EPS、capex 的 PRIMARY Quantity 分别解析为 `REVENUE`、`EPS`、`CAPEX`。
2. `IdentityCompiler.compile()` 会生成正确的 `primary_metric_id`，但 Sidecar 在使用该 discriminant 之前已经由 `compile_atomic_identity_sidecar()` 编译完成。
3. 这三条 Mention 没有形成 `FinancialMetricIdentityProfile`，而是落入 `OpenIdentityProfile / GENERIC_OPEN`。
4. `_open_facets()` 只为 `MARKET_MOVEMENT`、`ACTION_ARTIFACT`、`OUTLOOK_STATE` 返回 FACET；`GENERIC_OPEN` 即使存在 PRIMARY Quantity 也直接返回空列表。
5. 三条 Mention 因而只有相同的 REFERENT 与 OCCURRENCE，Sidecar signature 无法表达 metric 身份差异；N9 只能把 metric 差异看成 claim difference。

这不是 Prompt 未提醒模型。现有 Prompt 已明确写出 revenue、EPS、CAPEX 是不同 Atomic facts。真正缺口是输入合同没有把已经解析正确的 metric 放进 identity FACET。

#### 修复设计

调整 `IdentityCompiler.compile()` 的内部顺序：

1. 先计算 `_primary_metric_discriminant()`；
2. 再将高可信 `primary_metric_id` 传入 `compile_atomic_identity_sidecar()`；
3. `_open_facets()` 对 `GENERIC_OPEN` 增加：

```text
metric:<canonical metric family>
```

core ontology external ID 或已归并到该 external root 的稳定 canonical metric 均可进入该 FACET。没有可解析 root 的 PRIMARY metric 时继续保持空 FACET，不用 raw metric、provisional ID 或 supporting quantity 猜测身份。

预期结果：

```text
Q3 revenue -> FACET [metric:revenue]
Q3 EPS     -> FACET [metric:eps]
Q3 capex   -> FACET [metric:capex]
```

三者仍可因同公司、同财季、同披露动作进入 N7 候选池，但 N9 会收到明确的 FACET conflict，且 `METRIC` / `PRIMARY_METRIC_FAMILY` Apply guard 会阻止错误合并。

#### 同时启用 `METRIC` 与 `PRIMARY_METRIC_FAMILY`

两条规则同时启用，但仍保留各自语义：

- `PRIMARY_METRIC_FAMILY`：先阻断 revenue、EPS、capex、gross margin 等明确不同的 metric family；
- `METRIC`：在 family 相同但 canonical metric root 仍不同的情况下继续区分，例如 total revenue 与 product revenue。

同一 candidate 同时命中两条规则时只执行一次 lock，审计中保留两个 rule code，不重复增加 blocked 计数。accounting basis 和 comparison basis 仍由各自字段处理，不通过 metric-family 规则间接扩大。

#### 同类高确信字段排查

对本轮 199 条 Mention 的 Field Links 与 Sidecar 编译路径复核后，除 PRIMARY metric 外还存在以下情况：

- 75 条 PRIMARY metric link 已解析到 external metric root，其中 49 条为 `EXTERNAL_LINKING`、26 条为稳定 internal coreference；
- 159 条 company link、19 条 place link 已解析到 external root；
- 15 条 product/asset/technology link 已形成稳定 internal canonical root；
- 12 条 fiscal-period link 和 73 条 predicate external link 已被现有 Open profile/Sidecar 路径使用。

| 字段类型 | 当前状态 | 本轮处理 | 风险判断 |
| --- | --- | --- | --- |
| Fiscal period | 已由 `reference_period_id` 进入 OCCURRENCE | 保持现状 | 低风险 |
| Canonical predicate/action | 已进入 OCCURRENCE，但经过 action-family 归并 | 保持现状 | 继续 shadow，避免谓词粒度误杀 |
| Principal company | Open profile 通常已从 participant 进入 REFERENT，但 schema issuer 或高可信 company discriminant 可能未补入 | 用 compiled company IDs 补齐 REFERENT | 低风险；本轮不单独启用 ISSUER hard rule |
| Canonical place/location | 已存在于 OpenIdentityProfile，当前未进入 Sidecar | 加入 OCCURRENCE | 中低风险；只作为 N9 轴和 shadow warning |
| Canonical product/facility/project/asset/technology/program | Field Registry 已解析，当前未进入 Sidecar | 加入 FACET | 中等风险；只使用 resolved canonical root，不 hard enforce |
| Artifact/report ID | 仅在适配的 analyst/action-artifact 事件中可靠 | 在对应 adapter 下加入 OCCURRENCE | 低覆盖；只 shadow |
| Rating、lifecycle stage、accounting/comparison basis 的 GENERIC_OPEN 扩展 | 本轮覆盖不足或可能属于 claim 状态 | 暂不新增 | 误分风险高于当前收益 |

补充字段遵循两个约束：

1. 只把已经存在的 canonical root 投影进 Sidecar，不增加新的 LLM 字段或推断节点；
2. location/object/artifact 首轮只改善 N9 可见性，不加入 enforce allowlist。模型明确返回 `CONFLICT` 时仍受 relation 一致性约束，但 deterministic shadow warning 可被模型基于证据覆盖。

### 8.3 问题 B：axis 与 relation 自相矛盾

当前三层校验均有同一缺口：

- Schema 只保证 axis 唯一；
- task validator 只禁止 deterministic `CONFLICT` 被模型改成 `AMBIGUOUS`；
- action validator 只保证 MERGE target 的 relation 是 `SAME_EVENT`。

因此以下输出仍能通过：

```text
OCCURRENCE = CONFLICT
FACET = CONFLICT
relation = SAME_EVENT
action = MERGE
```

S&P 500 的 +0.6% 与 -0.01% bad case 正是沿此路径进入 Apply。

不能直接把所有 `canonical_conflict_axes` 变成 hard constraint。当前该字段同时包含 `MARKET_MEASURE`、`MARKET_SESSION` 等 shadow 规则；若对所有 deterministic conflict 强制 `CONFLICT -> 非 SAME_EVENT`，就等于绕过规则 allowlist，把尚未获得数据支持的规则全部间接启用。

#### 修复设计

N9 输入将冲突轴拆成两类：

- `canonical_conflict_axes`：所有 deterministic shadow warning，模型可根据原文确认或覆盖；
- `enforced_conflict_axes`：仅由本轮 allowlist 中的 `ASSERTION_STATE`、`COMPLETE_REFERENT`、`METRIC`、`PRIMARY_METRIC_FAMILY` 产生。

一致性处理采用任务内确定性规范化，而不是重新请求整批 LLM：

1. 任一模型返回的 axis 为 `CONFLICT` 时，该 candidate 的 `relation=SAME_EVENT` 非法；
2. 将该 candidate 规范化为 `RELATED_NOT_SAME`，保留原 axis 和差异原因；
3. 若它是 merge target，则优先选择同 task 内另一个合法 `SAME_EVENT` candidate；
4. 没有替代 candidate 时，仅将当前 Mention 降级为 `CREATE_NEW`；
5. `enforced_conflict_axes` 必须在输出中对应 `CONFLICT`；模型返回 `MATCH/AMBIGUOUS` 时仍由 Apply guard 阻断；
6. 其他 shadow conflict 被模型覆盖为 `MATCH` 时不阻断，但写审计，供下一轮 Gold 回放。

这样可以阻止 S&P 式的“模型自己说冲突却仍合并”，同时不会因为新增 validator 而触发整批 repair、整篇文档失败，或提前启用 `MARKET_MEASURE`。

### 8.4 四条规则的实际启用边界

#### `METRIC`

满足以下条件时阻断：

- incoming 存在唯一 PRIMARY Quantity；
- namespace 为 `METRIC`；
- Field Link 为 `EXTERNAL_LINKING`，或稳定 canonical root 已归并到 core ontology external ID；
- 双方 primary metric root 均可解析，且不同；
- provisional、unresolved 和仅有 supporting quantity 的 metric 不参与 hard rule。

candidate 已是混合 cluster 时，只要存在与 incoming 相同的 primary metric root，就允许合入该 metric 分支并写 `MIXED_CANDIDATE_METRIC` 审计；若 candidate 的所有已知 primary metric roots 都与 incoming 不同，则阻断。

#### `PRIMARY_METRIC_FAMILY`

双方存在可解析的 PRIMARY metric family 且 family 不同时直接阻断。该规则允许使用 core external root 或已稳定归并到该 root 的 canonical metric；不要求 Field Link method 必须是 `EXTERNAL_LINKING`。

以下属于明确 family conflict：

```text
revenue / EPS / capex / gross_margin / free_cash_flow / profit
```

family 相同但具体 metric root 不同时交由 `METRIC`；family 缺失或仍为 unresolved 时不阻断。

#### `COMPLETE_REFERENT`

双方各自至少存在一个可解析、非 unresolved 的 principal referent，且 canonical referent 集合完全不相交时阻断。referent 可以来自 external link，也可以来自已稳定归并的 internal coreference root，不再要求两侧都具备完整 schema projection。

candidate 中只要仍存在与 incoming 相同的 principal referent，就不因其他污染 referent 锁死；同时写 mixed-referent 审计。

#### `ASSERTION_STATE`

不采用当前“任意已知枚举不等即冲突”的逻辑，但将边界扩展为 realized 与 prospective 两组：

```text
realized:    ACTUAL, ONGOING
prospective: PLANNED, EXPECTED, HYPOTHETICAL
```

两组之间互为 hard conflict；组内不因 assertion 单项阻断。以下继续交给 N9：

```text
任何一侧 UNKNOWN
RUMORED / DENIED 与其他状态
```

candidate 若包含多个 assertion state，只要存在与 incoming 同组或相同的 state 就不依赖 assertion 单项锁死；所有可判 state 都落入对立组时才阻断。

### 8.5 Prompt 修改

Metric 问题不需要新增 Prompt。现有 High-Risk Boundaries 已明确说明 revenue、EPS、CAPEX 等不同指标是不同 Atomic fact；继续增加同义说明只会增加 payload，不能弥补缺失的 FACET。

只修改 `atomic_coreference.md` 中当前关于 `canonical_conflict_axes` 的一句话。

删除：

```text
Every axis listed in `canonical_conflict_axes` must be CONFLICT, not AMBIGUOUS.
```

替换为以下两句，除此之外不修改 N9 Prompt：

```text
`canonical_conflict_axes` are deterministic warnings; use the evidence to confirm or override them.
Every axis in `enforced_conflict_axes` must be `CONFLICT`, and any candidate with a `CONFLICT` axis must not be `SAME_EVENT`.
```

这两句分别表达：

- shadow 规则仍允许模型基于证据覆盖，避免被间接 hard-enable；
- 已启用规则和模型自身的 axis 结论必须与 relation 一致。

### 8.6 编排与审计改动

1. 增加显式 `atomic_enforced_rules` allowlist，默认只包含：

   ```text
   ASSERTION_STATE,COMPLETE_REFERENT,METRIC,PRIMARY_METRIC_FAMILY
   ```

   不再通过一个全局 `enforce` 开关恢复所有 legacy conflict。空 allowlist 可立即回退到全 shadow。

2. Apply 时对所有 N9 `SAME_EVENT` candidates 逐一评估 allowlist：

   - selected target 被锁时，尝试同 task 内下一个未锁的 `SAME_EVENT`；
   - 全部被锁才对当前 Mention `CREATE_NEW`；
   - 不重跑 N9，不阻塞同批其他 Mention。

3. 新增或扩充审计字段：

   ```text
   enforced_rules
   shadow_rules
   enforced_conflict_axes
   model_axis_relation_normalized
   original_relation
   final_relation
   alternate_target_used
   final_action
   ```

4. 对 shadow deterministic conflict 被模型改判为 `MATCH` 的情况写：

   ```text
   ATOMIC_AXIS_DETERMINISTIC_OVERRIDE
   ```

   记录 rule、axis、模型 relation 和证据摘要，不进入 LLM payload。

5. 对 `CONFLICT + SAME_EVENT` 的任务内规范化写：

   ```text
   ATOMIC_AXIS_RELATION_CONSISTENCY_NORMALIZED
   ```

   保留原输出，确保后续能区分模型错误、Sidecar 错误和 Apply guard 介入。

6. 升级并写入 processing key：

   - Atomic Identity Sidecar compiler version；
   - Identity Compiler version；
   - Atomic Merge Invariant policy version；
   - Cross-document Prompt version；
   - enforced-rule allowlist hash。

### 8.7 实施顺序

#### Phase 1：输入合同与一致性修复，规则仍保持 shadow

- 调整 IdentityCompiler 编译顺序；
- 将高可信 PRIMARY metric 注入 `GENERIC_OPEN.FACET`；
- 用 compiled principal company 补齐 `GENERIC_OPEN.REFERENT`；
- 将 canonical location 投影到 OCCURRENCE，将 canonical object/product/asset 投影到 FACET；
- 仅在 analyst/action-artifact adapter 下投影 canonical artifact/report ID；
- 增加 `enforced_conflict_axes` 输入字段；
- 增加 axis/relation 任务内规范化及审计；
- 修改上述两句 Prompt；
- 完成单元和离线回放测试。

先保持全 shadow，是为了分别验证“Sidecar 信号是否正确”和“启用规则后的业务影响”，避免两个变量同时变化后无法归因。

#### Phase 2：只启用四条 allowlist 规则

- 启用 realized/prospective 分组后的 `ASSERTION_STATE`；
- 启用 `COMPLETE_REFERENT`；
- 启用 compiled `METRIC`；
- 启用 `PRIMARY_METRIC_FAMILY`；
- 其余规则继续 shadow；
- 先用固定运行记录做组合反事实回放，再决定是否进行真实模型验收。

### 8.8 必须覆盖的测试

#### Sidecar/Identity

- GENERIC_OPEN revenue、EPS、capex 分别生成 `metric:revenue/eps/capex` FACET；
- 三者 signature 不再相同，FACET 比较为 `CONFLICT`；
- 缺失、supporting、LLM LINK、provisional metric 不进入 FACET；
- 相同高可信 metric 仍为 `MATCH`。
- schema issuer/compiled company 可补齐 REFERENT，但不会覆盖已有 participant；
- canonical location 进入 OCCURRENCE，canonical object/product/asset 进入 FACET；
- rating、lifecycle stage 和无 resolved root 的 open attribute 不进入 Sidecar；
- location/object/artifact conflict 只产生 shadow warning，不进入 enforced axes。

#### N9 一致性

- `FACET=CONFLICT + SAME_EVENT` 被任务内改为非 SAME；
- `OCCURRENCE=CONFLICT + SAME_EVENT` 被任务内改为非 SAME；
- 当前 merge target 失效时优先选择另一个合法 SAME candidate；
- 没有替代时只对当前 Mention `CREATE_NEW`；
- 不触发整批 repair，不造成文档失败；
- shadow `MARKET_MEASURE` 被模型明确改判为 `MATCH` 时只审计、不被规则层硬阻断。

#### 四条规则

- revenue、EPS、capex 在同 issuer/period 下不得互并；
- `PRIMARY_METRIC_FAMILY` 与 `METRIC` 同时命中时只执行一次 lock；
- Sandisk/Micron、Roundhill/Defiance 等完整不同 referent 不得互并；
- realized 与 prospective assertion 互相阻断；
- `EXPECTED` vs `PLANNED` 和 `ACTUAL` vs `ONGOING` 不被 assertion 单项阻断；
- candidate 含兼容 metric/referent/state 时，不因污染成员而误锁全部候选。

#### 固定 30 篇回放门槛

- 原始 199 条 task、45 条既有 N9 fallback 保持为固定控制变量；
- candidate coverage 仍为 100%；
- `CONFLICT + SAME_EVENT` 最终进入 Apply 的数量为 0；
- Q3 revenue / EPS / capex 已知误合并为 0；
- `COMPLETE_REFERENT` 已判 pair 继续保持 TP blocked=0；
- 非 allowlist 规则的 enforced count 必须为 0；
- 新增 document/case failure 为 0；
- 单独报告四条规则各自及组合后的 Pair P/R、singleton 增量和每个被阻断 TP。

### 8.9 回滚条件

出现任一情况即清空对应 rule 的 allowlist、退回 shadow，不回滚 Metric FACET 和一致性审计：

- `METRIC` 阻断一个经 Gold 确认的相同 primary metric；
- `PRIMARY_METRIC_FAMILY` 阻断一个经 Gold 确认的相同 metric family；
- `COMPLETE_REFERENT` 阻断一个经 Gold 确认的同 referent；
- 分组后的 `ASSERTION_STATE` 仍误杀正确 merge；
- 一致性修复造成整批 N9 repair 或文档失败；
- 任一未获准规则出现 enforced count；
- combined replay 的 Recall 损失超过四条规则单独回放可解释的上界。

最终实施原则：**Sidecar 高确信字段补齐和 relation 一致性属于输入合同/正确性修复，应保留；四条 hard rule 必须通过显式 allowlist 独立启停，不能再使用一个全局开关把所有 shadow conflict 一起恢复。**
