# CDECR Mention / Evidence / Field / Identity 深度排查报告

日期：2026-07-28  
性质：冻结 30 篇真实验收结果的只读根因复盘  
范围：Mention、Evidence、Canonical Field Resolution、Identity Compilation  
不在本轮范围：N7-N10 Atomic 决策、N11-N13 Package 决策本身

## 1. 结论先行

原验收报告给出的总指标没有问题，但“为什么错”的归因需要明显细化。

本轮最重要的结论有六条：

1. **召回损失不能统称为 Dreamer omission。** 冻结运行共有 357 个 Dreamer candidate，其中 106 个没有进入任何 Grounder draft；Judge 只明确 REJECT 了 7 个 draft。至少在“PC/手机销量下降”“分析师观点”“估值”“需求继续超过供给”等若干 Gold 漏项上，Dreamer 已经召回，首个分歧点是 Grounder 静默过滤。当前 Grounder 输出协议不要求为每个 candidate 给出处置，因此无法区分“有意过滤”和“意外漏处理”。
2. **Mention 的主要问题不是幻觉，而是边界、字段位置和模态。** 225/225 Surface Proposition 都有来源支持；严格 Precision 下降主要由复合 Mention、低信息 umbrella Mention、比较量没有进入 `quantities`、assertion/time 语义错误造成。
3. **Evidence 的 16 条 `TEXT_NOT_FOUND` 不是同一种问题。** 9 条 Main Evidence 中，5 条最早在 Dreamer 已经失去 exact quote，4 条由 Grounder给原本精确的文本补句号而造成；7 条 Attribute Evidence 全部是“文本在来源中出现多次但未使用 Main Evidence anchor 消歧”。这些都不需要增加语义修复 LLM。
4. **Field 的 132 个错误 occurrence 不是 132 次独立 LLM 误判。** 其中 45 个是同一文档 alias group 对 primary 决策的复制；按冻结审计请求形态重建，51 个错误来自无候选集的确定性/唯一 KB LINK，33 个来自候选集内错误 LINK，33 个来自错误 UNRESOLVED，15 个来自错误 NEW。Prompt 修改最多只能直接影响其中一部分。
5. **Identity 是当前 Atomic 过合并的第一个系统性放大点。** 225/225 Mention 都以 OPEN profile 编译，`schema_projection` 全为空；虽然 206 个 quantity metric 中有 167 个 Field link 判断正确，但 OPEN Identity 完全不读取 `quantities[*].metric_id`。所以 Field 做对了也没有进入身份边界。
6. **存在一个跨模块契约冲突。** Grounder Prompt 明确要求 predicate 只保留动作，使用 `report_metric` 而不是 `report_revenue/report_eps`；这是合理的字段因子化。但 N6 OPEN Identity 又不纳入 metric，于是不同财务指标只剩相同的 predicate + issuer + period。这不是单个模型节点能修好的问题。

因此，本轮不建议先大改 Prompt。优先级应是：

- 先补 Grounder candidate 逐项 disposition 和 Mention/Identity 的确定性边界；
- 再清理 Field 候选与路由；
- 最后用少量、针对性的 Prompt 语句处理 close/current、assertion-state、umbrella Mention 等模型错误。

## 2. 证据与方法

### 2.1 冻结证据

- Registry：`.tmp/cdecr/resilience/resilience_30_20260728.sqlite3`
- Mention 复核：
  - `.tmp/cdecr/resilience/mention_review_01_10.json`
  - `.tmp/cdecr/resilience/mention_review_11_20.json`
  - `.tmp/cdecr/resilience/mention_review_21_30.json`
- Field/Identity 全量复核：`.tmp/cdecr/resilience/field_identity_audit_all.json`
- Cluster export：`.tmp/cdecr/resilience/resilience_30_20260728_clusters.json`
- 上游验收报告：`dev_plan/CDECR/CDECR_ITEM_SCOPED_RESILIENCE_30_ACCEPTANCE_20260728.md`

### 2.2 排查链路

每个代表性 bad case 都沿以下路径回溯：

`Source -> Dreamer candidate -> Grounder draft -> Judge decision -> Mention materialization -> Field request/candidates/result -> Identity profile`

### 2.3 归因类型

报告将错误区分为：

- 模型未遵循已有明确 Prompt；
- Prompt 语义缺口或相邻规则冲突；
- DTO/Schema 无法表达业务区别；
- 候选召回或 KB 污染；
- 确定性路由错误；
- 编排/验证器未拦截；
- 人工 Gold 与当前业务契约不一致。

### 2.4 限制

- 本报告只解释冻结运行，不把随机模型输出外推为长期稳定概率。
- Mention 三个分片的 Gold 口径并不完全一致，特别是“同一次 guidance 的不同 metric 是否应拆成不同 Mention”。凡遇到这类冲突，本报告同时指出 evaluator 与生产 Prompt 的差异，不强行把全部差异归为模型错误。
- 这里的 Field 错误数是 occurrence 数，不是独立决策数；alias group 会复制 primary 决策。

## 3. Mention 深度排查

### 3.1 现状指标

| 指标 | 结果 |
| --- | ---: |
| Gold Mention | 268 |
| 输出 Mention | 225 |
| 严格 TP / FP / FN | 192 / 33 / 76 |
| Precision / Recall / F1 | 85.33% / 71.64% / 77.89% |
| Surface Proposition 有来源支持 | 225/225 |
| Dreamer candidates | 357 |
| 进入至少一个 Grounder draft | 251 |
| 未进入任何 Grounder draft | 106 |
| Judge actions | 207 ACCEPT / 8 SPLIT / 7 REJECT / 3 MERGE_AS_ATTRIBUTE |
| 最终贡献到 Mention 的 candidates | 245 |

30 篇中 Dreamer candidate 数量为 2-23，平均 11.9，没有任何文档触及 24 条上限。因此本次召回损失**不是**由 `max 24` 硬截断直接造成。

### 3.2 召回损失的真实分层

#### A. 已确认的 Dreamer omission

前 10 篇人工复核明确标注：

- 19 个 clean `DREAMER_OMISSION`；
- 4 个 Gold 因两个复合输出未拆分而失去严格一对一匹配。

这部分可真实归因到 Dreamer 覆盖不足，常见于：

- 次级业务事实；
- 比较量；
- 市场动作；
- 管理层附带陈述。

Dreamer Prompt 中“prioritize claims material to the target ticker”会合理压低非目标主体的次级事实优先级，但本次 Gold 又覆盖了一部分相关市场、同行、分析师和供应链事实。这里存在业务覆盖边界需要统一的问题。

#### B. Grounder 静默过滤

357 个 candidate 中有 106 个没有对应任何 draft。Grounder DTO 只有：

- `drafts[]`
- 每个 draft 的 `source_candidate_ids[]`

没有：

- `rejected_candidate_ids`
- `candidate_dispositions`
- `reason_code`
- “每个 candidate 必须恰好被消费或拒绝”的覆盖校验

所以 candidate 不出现时，无法审计是模型认为它不是 event，还是模型漏处理。

冻结运行中可明确看到 Dreamer 已召回、但 Grounder 没有 draft 的 Gold/准 Gold 例子：

| 文档/候选 | Dreamer | Grounder | 根因判断 |
| --- | --- | --- | --- |
| Data-center unit sales expected to grow by high teens | 已召回 | 无 draft | Grounder 静默过滤；该事实有明确主体、方向和预期 |
| PC and smartphone unit volumes are falling | 已召回 | 有 draft，随后 Judge REJECT | Judge 认为是 generic market trend；与人工 Gold 冲突 |
| Ryan Lee 对 earnings beat 推动 AI equities 的判断 | 已召回 | 无 draft | 分析师判断被 eventhood 过滤 |
| Jake Behan 对需求、pricing power、visibility 的判断 | 已召回 | 无 draft | 分析师观点被过滤 |
| Cloud providers/AI developers 扩大 data-center capacity | 已召回 | 无 draft | 被视为背景，但 Gold 将其视为 ongoing state |
| Citi sees room for further Sandisk gains over 90 days | 已召回 | 无 draft | 分析师 forecast 被过滤 |
| Micron forward valuation near 19x | 已召回 | 无 draft | measurable state 被过滤 |
| Micron earnings +1,368% YoY | 已召回 | 无 draft | 明确量化状态被过滤 |
| Expert says Micron is a buying opportunity | 已召回 | 无 draft | opinion 被过滤 |
| AI memory demand continues to outpace supply | 已召回 | 无 draft | ongoing state 被过滤 |

这里既有真正的漏处理，也有 Prompt 与 Gold 对 eventhood 的不一致。

Grounder Prompt 第 5-9 行保留 measurable/ongoing state，却排除 opinions、general background。对于“分析师预测”“有归属的投资判断”“行业供需持续状态”，当前边界不够精确：

- 有归属、可复核、对标的有明确含义的 analyst view，业务上通常应作为 `ANALYST_ACTION` 或 EXPECTED/HYPOTHETICAL Mention；
- 没有主体、时间或可复核内容的泛泛评论才应丢弃。

#### C. Judge 过滤与 Gold 冲突

Judge 只 REJECT 了 7 个 draft，但其中至少有以下值得复核：

| Candidate | Judge reason | 诊断 |
| --- | --- | --- |
| Micron momentum 99th percentile | analytical opinion/rating，不是 time-bounded event | 与 Gold 将排名/趋势视为 measurable analyst signal 冲突 |
| Unit volumes falling in PC/smartphone | generic background，缺少 specific metric/time/source | 来源有明确方向和市场范围；是否必须保留需统一业务口径 |
| Stock jumped nearly 1,000% over the year | “over the year”期间不清晰 | 时间不精确不等于事件不存在；更合适的是保留 UNKNOWN/rolling period，而非整条 REJECT |
| Revenue growth accelerated again | revenue report 的 attribute | 可接受作为同一 revenue event 的比较属性，但必须真正并入目标 Mention，而不能只 REJECT |
| AI fundamentally reshaped memory industry | generic background/opinion | 若保留，应以 attributed analyst/management assertion 建模；若不保留，Gold 不应要求 |

### 3.3 Atomicity 错误

#### Case M-1：Anthropic partnership / supply / investment 被合成一个 Mention

最终 Mention：

`mention:...984175b9`

Dreamer 已分别产出：

- partnership；
- agreement scope；
- supply agreement；
- strategic investment。

Grounder 首次把它们合成：

> entered strategic partnership and supply agreement ..., including strategic investment

并把 investment 放进 `open_attributes.investment_detail`。Judge 随后 ACCEPT，reason 仍把 partnership 和 investment 当作一个独立 event。

根因：

- Grounder 模型没有遵守 Prompt 第 12-17 行“不同 actions 必须拆分”；
- Judge 没有遵守第 14-18 行 atomicity；
- `open_attributes` 允许装入动作性很强的内容，但没有确定性 guard 检查 `investment/sign/commit/acquire` 等第二动作；
- Judge reason 协议有保留，但 reason 的存在并没有保证判断正确。

这不是 Prompt 缺失；Prompt 已经明确。更有效的修复是编排层的 action-bearing attribute 检查与候选覆盖检查。

#### Case M-2：三类投资者的相反净流向被合成一个 Mention

`mention:...b4415e33`

同一 Mention 包含：

- individuals net bought；
- institutions net bought；
- foreign investors net sold；
- 三个不同主体；
- 两种相反 predicate；
- 另有 five-day aggregate 未保留。

Prompt 已明确要求按不同 subject/action 拆分，因此首错在 Grounder，Judge 未纠正。

#### Case M-3：同一 guidance 中 revenue 与 EPS 仍被合并

`mention:...c752f64f`

Grounder 将 Q4 revenue $50B 和 EPS $31 合成一个 Mention；Judge reason：

> two metrics for the same issuer and period; atomicity preserved

这直接违背 Judge Prompt “Split different metrics”。

但这里还暴露出 evaluator 不一致：

- Mention 分片 Gold 把这一组 guidance 当一个 Gold Mention；
- Grounder/Judge Prompt 要求不同 metric 拆分；
- Atomic Gold 又要求不同 metric 不得进入同一 Atomic。

从最终 `Package -> Atomic -> Mention` 业务目标看，应以“不同核心 metric 可形成不同 Atomic”为准，因此 Mention Gold 也应同步拆分，否则会奖励对下游有害的复合 Mention。

### 3.4 低信息 umbrella Mention

`mention:...6eb1d91d`：

> Micron Technology reported earnings.

来源 candidate 原本包含 earnings、revenue quadrupling、gross margin。Judge SPLIT 后保留：

- generic reported earnings；
- revenue；
- gross margin。

前者被人工判为 `LOW_INFORMATION_DUPLICATE`。

根因不是单纯模型粗心。Judge Prompt 将“disclosure”列为 event，却没有补充：

> 当同一 draft 已拆出所有具体披露内容时，不要再保留没有独立 artifact/action identity 的内容空壳 umbrella disclosure。

需要增加的是一个很窄的反冗余规则，而不是全面收紧 disclosure eventhood。

### 3.5 Quantity / comparison 字段错误

11-20 分片有 14 个 `MISSING_KEY_QUANTITY`。进一步检查发现：

- 7/14 至少有一个被指“遗漏”的数量实际上保存在 `open_attributes`；
- 另外 7/14 才是确实没有落在结构化输出中。

例子：

| Mention | `quantities` | `open_attributes` | 诊断 |
| --- | --- | --- | --- |
| Q3 revenue $41.456B | 主值 | 17.60% beat、345.72% YoY | 比较量存在，但字段位置错误；$9.3B prior-year value 真的缺失 |
| Revenue $41.5B | 主值 | 74% YoY、$35.9B estimate | 信息没有丢，但无法进入 metric/comparison identity |
| Gross margin 84.9% | 主值 | 81.7% consensus | 同上 |
| EPS $25.11 | 主值 | doubled QoQ、$20.86 estimate | 同上 |
| Q4 revenue $50B | 主值 | $43.6B estimate | 同上 |
| 16 SCAs | count | $100B commitment | commitment 被当 attribute 而不是 quantity |
| Remaining performance obligations | 无 | $100B | 主 metric/value 整体落在 attribute |

DTO 层的 `QuantityDraft` 只有 `metric_id/value/unit/raw_text`；`OpenAttributeDraft` 是自由 `key/value`。Prompt 只说 attribute 是“不独立构成 event 的 modifier”，没有说明：

- comparison value；
- consensus；
- prior-period value；
- range/bound；
- percentage change

应当仍进入 `quantities`，并用 comparison/basis 关联，而不是自由属性。

这会造成两个下游问题：

1. Field resolver 只对 `quantities[*].metric_id` 做 metric resolution，比较量进入 attribute 后失去 metric 类型；
2. OPEN Identity 又只读取极少数 attribute key，因此这些事实不参与身份边界。

### 3.6 Assertion State

#### Case M-4：Guidance 被标为 ACTUAL

`mention:...c752f64f`

- event_family：`GUIDANCE_EXPECTATION`
- predicate：`guide_metric`
- assertion_state：`ACTUAL`

Grounder 首次出错；Judge 明确说“Supported guidance event”，却仍返回 ACTUAL。

Prompt 已写明 reporting guidance 不会把 underlying proposition 变成 ACTUAL，因此这是双模型不遵循规则。编排层缺少兼容性校验：

- `GUIDANCE_EXPECTATION + guide_* + ACTUAL`

在当前业务定义下应触发修正或局部降级。

同时，canonical proposition 使用“Micron provided guidance”，可能让模型把“发布 guidance 的动作”理解为 ACTUAL，而 evaluator 关注的是“被 guidance 的未来指标”。建议明确区分：

- guidance issuance/disclosure event；
- guided underlying metric proposition。

#### Case M-5：`likely constrained` 被改成 HYPOTHETICAL

`mention:...c478f113` 与 `mention:...7fd1f3fb`

Grounder 是 ACTUAL，Judge 改成 HYPOTHETICAL。人工认为应为 EXPECTED。

这里的 Prompt 只列枚举，没有定义 EXPECTED 与 HYPOTHETICAL 的边界。对有明确 claimant 的“likely to be constrained until...”：

- 它是带概率的 forecast；
- 更接近 EXPECTED；
- HYPOTHETICAL 更适合条件命题、反事实或尚未形成判断的可能情景。

这是需要小幅补 Prompt/枚举描述的真实缺口。

#### Case M-6：Investor day 的计划与潜在内容共用一个 assertion

`mention:...874ec9a4`

- investor day in August：PLANNED；
- could bring updates：HYPOTHETICAL/EXPECTED；
- 最终 Mention 只有一个 PLANNED assertion；
- “可能更新的内容”作为无模态 `open_attribute` 保留。

这不是简单字段填错，而是 `OpenAttribute` 没有自己的 modality。保守处理可以保留一个 PLANNED investor-day Mention，但 attribute 应保留短 modality 标记，或者把潜在更新从 identity-bearing 字段中排除。

### 3.7 Time 错误与 evaluator 冲突

#### Unsupported close

Doc 19 有 3 个相似错误。以 `mention:...d0240bac` 为例：

来源：

> The Dow Jones index rose 0.5% to 52,107.28

Grounder：

> increased by 0.5% to close at 52,107.28

并将 predicate 设为 `trade_close`。Judge ACCEPT。

“rose to/current at”不等于“closed at”。当前 Prompt 没有明确 close/current/intraday 区分，属于可用一条窄规则修复的缺口。

#### After-hours 时间被 Judge 清空

`mention:...4cb8ba0e`

Grounder给出 2026-06-24 DAY；Judge以“未提供具体 after-hours timestamp”为由改为 null/UNKNOWN。人工标记 `TIME_NOT_NORMALIZED`。

按照当前 Prompt，“没有具体 bound 就保持 null”，Judge 是按契约执行的；人工 Gold 更倾向从文章语境推导 Wednesday。这里必须统一：

- 若允许从明确 weekday + published_at 解析日期，应在 Prompt 和确定性 parser 中明确；
- 若不允许，人工 Gold 不应把 UNKNOWN 算作错误。

#### Forecast horizon 不是 event time

`beyond calendar 2027` 被放进 `open_attributes.outlook_horizon`，Judge 删除 `reference_period_id=fiscal Q3`。这在当前 `EventTime` 设计下是合理的，因为：

- fiscal Q3 是声明发生的上下文；
- beyond 2027 是预测 horizon；
- 二者都不是具体 event bound。

因此部分 `TIME_NOT_NORMALIZED` 实际是“缺少 first-class forecast horizon”，不是模型时间解析错误。

### 3.8 数量 scale：`$1.2 trillion -> 1.2 USD`

`mention:...500f3c49`

- Dreamer 正确；
- Grounder 输出 `value=1.2, unit="trillion USD"`；
- Judge ACCEPT；
- 最终 normalizer 把 unit 变成 USD，却没有乘 1e12。

直接根因：

- `src/cdecr/catalogs/v1/units.json` 只有 thousand/million/billion；
- `mention_finalization.py` 和 `normalization.py` 仍读取 v1 units；
- v2 units 已经有 TRILLION，但没有进入该 normalization path。

这是确定性跨版本资源错误，不应主要靠 Prompt 修。

## 4. Evidence 深度排查

### 4.1 总体

| 类型 | VERIFIED | TEXT_NOT_FOUND | 合计 |
| --- | ---: | ---: | ---: |
| Main | 234 | 9 | 243 |
| Attribute | 102 | 7 | 109 |
| 总计 | 336 | 16 | 352 |

关键事实：

- 352/352 原始 Evidence 均持久化；
- 225/225 Mention 的 Surface Proposition 均有来源语义支持；
- 没有调用语义 Evidence repair LLM；
- 1 次 Dreamer 结构修复处理两个空 Evidence text，修复后没有空文本落库；
- 16 条定位失败没有扩大为 Mention 或文档失败。

### 4.2 9 条 Main Evidence 的具体成因

| Mention 后缀 | Evidence 形态 | 最早分歧点 | 结论 |
| --- | --- | --- | --- |
| `e2216444` | `SK Hynix ... tumbled more than 12%` | Dreamer locator 已非法；Grounder继续生成 ellipsis quote | 模型合成摘录，不是 exact quote |
| `76a166f7` | `Samsung ... tumbled more than 12%` | 同上 | 同上 |
| `86dc60a6` | `the tech-heavy Nasdaq ... were up 2.15% ...` | 同上 | 同上 |
| `8ff748f4` | `S&P 500 were up ... 0.75% ...` | 同上 | 同上 |
| `00b58800` | `Micron just reported GAAP profits...` | Dreamer 将 `Sandisk's archrival` 改写为 `Micron` | 语义 coreference rewrite，不是原文 quote |
| `c4ff23c5` | 原文以逗号继续，Evidence 加句号 | Grounder | 仅尾标点变化 |
| `d6c1c1b8` | 同上 | Grounder | 仅尾标点变化 |
| `b818645a` | 同上 | Grounder | 仅尾标点变化 |
| `41f64d1a` | 同上 | Grounder | 仅尾标点变化 |

这里可以明确分成：

- 5 条 first divergence 在 Dreamer；
- 4 条 first divergence 在 Grounder；
- Judge 对 9 条都没有纠正。

Dreamer Prompt、Grounder Prompt 和 Judge Prompt 都已要求 exact text。因此主因是：

- 模型未遵循；
- 为了 item-scoped resilience，Dreamer invalid locator 被保留并向后传递；
- Grounder/Judge 没有一个“Evidence 必须是来源 exact substring”的最终逐项 validator/局部回退。

### 4.3 7 条 Attribute Evidence 的具体成因

全部 7 条都不是文本不存在，而是同一短语在 segment 内出现多次：

| 类型 | 数量 | 示例 |
| --- | ---: | --- |
| `pre-market trade` 重复 | 4 | Micron、Dow、Nasdaq、S&P 500 |
| `Strategic Customer Agreements` 重复 | 1 | agreement_type |
| `plus or minus $1` 重复 | 1 | guidance_range |
| `record levels` 重复 | 1 | FCF status |

`reconcile_evidence_text()` 已支持 `candidate_anchors` 做重叠消歧，但 `_materialize_mention.locate()` 调用时没有传 Main Evidence anchors。对这 7 条，Main Evidence 本身已限定正确句子，使用 anchor 可以确定性解决，不需要增加 LLM payload。

### 4.4 审计缺口

- `DREAMER_EVIDENCE_RECONCILIATION` 记录了 invalid locator 数量和 dropped 数量，但 payload 没有 message_id；需要通过 run_id join 才能定位来源。
- Main/Attribute 的最终 `MENTION_EVIDENCE_LOCATION` 已有 status、error_code、hash，基本够用。
- 当前 status 将“文本重复歧义”也表示为 `TEXT_NOT_FOUND`，语义不够准确。至少应在 `error_code=evidence_text_ambiguous` 层单独统计。

## 5. Field Resolution 深度排查

### 5.1 指标

| 字段 | 正确/总数 | 正确率 |
| --- | ---: | ---: |
| predicate.normalized | 188/225 | 83.56% |
| participants | 242/261 | 92.72% |
| quantities.metric_id | 167/206 | 81.07% |
| locations | 19/20 | 95.00% |
| time.reference_period_id | 35/70 | 50.00% |
| open_attributes | 7/8 | 87.50% |
| 合计 | 658/790 | 83.29% |

错误类型：

| 错误 | 数量 |
| --- | ---: |
| METRIC_SEMANTIC_COLLISION | 39 |
| SEMANTIC_PREDICATE_COLLISION | 37 |
| FISCAL_PERIOD_SCOPE_OR_COVERAGE_ERROR | 35 |
| PARTICIPANT_NAMESPACE_OR_ENTITY_MISMATCH | 19 |
| LOCATION_NAMESPACE_MISMATCH | 1 |
| ATTRIBUTE_NAMESPACE_MISMATCH | 1 |

### 5.2 132 个错误的决策来源

根据每个错误 occurrence 对应的 `FIELD_COREFERENCE` 与 `DOCUMENT_FIELD_ALIAS_GROUP` 审计重建：

| 决策形态 | 错误 occurrence | 含义 |
| --- | ---: | --- |
| 无 candidate set 的 LINK | 51 | 确定性 unique/exact KB link 或其 alias-group 复制 |
| 有 candidate set 的 LINK | 33 | 候选内错误选择，主要是 Field LLM |
| UNRESOLVED | 33 | 应识别对象/period 却保守弃权 |
| NEW | 15 | 错建新的 canonical/type |
| 其中 alias-group 复制 | 45 | 不是新的独立判断 |

因此：

- 只改 Field Prompt 不会解决 51 个 deterministic link 错误；
- 35 个 period 错误也主要是 issuer/routing/coverage；
- 真正直接受候选判别 Prompt 影响的是 33 个 candidate LINK，再加少量 unknown routing 输出。

### 5.3 Metric：候选目录和 hard dimension 失真

运行时 v2 metrics 共 9,257 条，其中 9,116 条（98.48%）是 `US_GAAP_`/`XBRL_` 扩展项，核心业务指标只有约 141 条。

典型错误：

| raw metric | 候选/结果 | 具体问题 |
| --- | --- | --- |
| `trading_volume` | Trading Assets | 市场成交量被 XBRL balance-sheet asset 吸走 |
| `stock_change` | Share price | change 与 level/value 混淆 |
| `closing_price` | Index level | stock level 与 index level 淆 |
| `guaranteed_revenue` | Adjusted revenue | contract guarantee 与 accounting basis 淆 |
| `deal_count` | Store count | 同为 count，但对象完全不同 |
| `revenue_growth` | Constant-currency/Organic growth | 来源未说明 FX-neutral/organic transformation |

候选请求里有 candidate hard dimensions，但 raw input 自身没有经过同样的确定性维度解析。模型看到：

- `raw_value=revenue_growth`
- 一组 transformation=VALUE/GROWTH 的候选

却要仅凭上下文自己推导 raw 的 base measure/scope/transformation。错误候选还常把 query raw label 暴露在 alias 中，形成很强的词面诱导。

Field Prompt 已明确：

> value、growth、margin、yield、total、segment 必须区分。

所以这些错误不是 Prompt 没写，而是：

- 候选池被大规模 XBRL 项污染；
- raw side 没有 hard dimensions；
- 候选 alias 词面过强；
- 模型没有遵守已有 substitution test。

此外，错误 LINK 会通过 `_add_aliases()` 把 raw value 写入被选 canonical 的 aliases，后续同 raw 值可能变成更强的 deterministic/exact link，存在自强化污染风险。

### 5.4 Predicate：Prompt 因子化与 KB action 冲突

典型错误：

| raw predicate | 结果 | 丢失的业务区别 |
| --- | --- | --- |
| `disclose_uncertainty` | disclose incident | uncertainty/outlook 被改成 incident |
| `trade_stock` | split stock | 市场交易动作被改成 corporate action |
| `trade_higher` | raise price | stock movement 与公司定价动作混淆 |
| `change_index` | change pricing | index movement 与产品/商业定价混淆 |
| `close_price` | cut price | close 与 cut 混淆 |
| `announce_plan` | announce agreement | plan object 被改成 agreement |
| `expect_price_change` | expect action | price-change object 丢失 |

Predicate policy 已要求按 action/object/direction/modality 区分，但很多错误是：

- KB exact alias 本身把 raw predicate 挂到了错误 concept；
- 候选中正确的 NEW 选项没有显式“保持 raw action”对照；
- Grounder 产生了不稳定、开放式 predicate token；
- 后续 Field 被迫在噪声 concepts 中找近似项。

特别需要注意：

Grounder Prompt 第 20-25 行要求 predicate 只保留动作，这是正确的；但 `trade_stock`、`change_index` 仍同时把 object 塞在 token 中，说明模型输出并未稳定遵循 factorization。Field 不能可靠地从一个已经混合 action/object 的 raw token 恢复业务语义。

### 5.5 Participant：缺少显式类型导致先路由、后判断

`ParticipantDraft` 只有：

- `surface`
- `role`

没有：

- participant type；
- generic/collective 标志；
- instrument/company/institution/object 的显式分类。

Resolver 只能通过正则和多 catalog lookup 决定 namespace。典型错误：

| surface | 结果 | 应有方向 |
| --- | --- | --- |
| analysts | persistent unresolved company-like participant | generic participant，不应成为稳定实体 |
| data center, consumer, automotive customers | institution/company 路由 | generic collective |
| Stoxx 600 | unresolved | instrument/index |
| Memory and storage | product/company-like canonical | commodity/product class |
| core data center unit | company/object | business segment |
| Defiance | object.asset | institution/company |
| communication services stocks | product | sector/instrument collective |
| UBS | COMPANY_UBS | analyst institution/UBS Research |
| institutions | Osaic Institutions | generic word与实体名称词面碰撞 |
| IDC | Western Digital | alias/candidate collision |
| Micron management | Carson Management | “management”词面碰撞 |

Field Prompt 写了“Never link across namespaces”。问题是：一旦前置 routing 已进入错误 namespace，Prompt 反而会阻止模型看到正确对象。因此 participant 问题的第一根因是 Mention type 缺失和路由，不是 coreference Prompt。

### 5.6 Fiscal period：issuer 选择与时间类型混用

70 个 period 只有 35 个正确。

代码中的 `_issuer_id(source, mentions)` 会遍历整篇文档的所有 Mention，返回第一个可用 company participant，再把同一个 issuer hint 用到各 Mention 的 period 解析。风险：

- 文档包含多个公司；
- 第一个主体是 business unit 或错误 company canonical；
- 来源 ticker 是 MU，但具体 Mention 主体不是 MU；
- mention-local issuer 信息被 document-global first match 覆盖。

实际例子：

- `fiscal Q3 2026` 的 issuer hint 变成 `field:83ff...`，即 core data center/business unit 的 provisional field，而不是 `COMPANY_MU`；
- `third quarter`、`fourth quarter` 多次落为 issuer-scoped UNRESOLVED；
- 明确 Micron Q3/Q4 没有链接到 `COMPANY_MU_FY2026_Q3/Q4`。

另一个问题是：

- `past three quarters`
- `most recent quarter`
- `beyond calendar 2027`

不一定都是单一 fiscal period。把所有 `time.reference_period_id` 都送入 `fiscal_period` namespace，会把 rolling window、relative window、forecast horizon 与 issuer fiscal quarter 混为一类。部分 audit error 实际是 representation/routing 错误，而不是应该强行链接某个 Q3/Q4。

### 5.7 Location / Attribute

#### Nasdaq

`US Nasdaq` 被 Grounder 放进 `locations[]`。Resolver 对 locations 默认走 PLACE，最终 NEW place。

这是上游字段类型错误：

- Nasdaq 在 listing 语境中是 exchange/listing venue；
- 应是 institution/instrument attribute；
- `locations` DTO 没有 type，Field 只能按 place 处理。

#### `status=record levels`

Grounder生成：

- key=`status`
- value=`record levels`

v2 attributes catalog 把 `status` 作为 `lifecycle_stage` 的 alias，因此 Field 将它路由到 `concept.lifecycle_stage`。但“record levels”是 qualitative magnitude/comparison state，不是 proposed/announced/completed 等 lifecycle。

这属于 attribute routing catalog 过宽，不是 Field LLM 错判。

### 5.8 Field 审计能力

优点：

- `FIELD_COREFERENCE` 已持久化 raw input、local context、hints、候选、hard dimensions、结果和配置 hash；
- `DOCUMENT_FIELD_ALIAS_GROUP` 已保留 paths 与最终 canonical；
- 因此本轮可以重建大多数具体错误。

不足：

- alias group 的 45 个复制 occurrence 需要额外 join primary 才能还原；
- 错误 LINK 后 alias 被更新，但缺少“该 alias 首次由哪次决策引入”的专门 provenance；
- document-global issuer 选择结果只在 hints 中看到，没有单独记录 issuer selection path；
- participant routing 的所有被尝试 namespace/candidate rank 没有一个紧凑 routing audit。

这些都可以在编排层加短审计记录，不需要增加 LLM payload。

## 6. Identity 深度排查

### 6.1 指标

| 指标 | 结果 |
| --- | ---: |
| Profiles compiled | 225/225 |
| `missing_required_fields` | 0 |
| OPEN profiles | 225 |
| typed profiles | 0 |
| schema_projection null | 225 |
| predicted same-identity pairs | 956 |
| TP / FP | 285 / 671 |
| pair precision | 29.80% |
| high-confidence judgeable recall | 92.50% |

92.50% 只适用于 308 个可高置信恢复的真 pair，不是全量 recall。

### 6.2 `missing_required_fields=0` 的真实含义

对 OPEN Identity，编译器只把 `predicate.normalized` 当作硬 required field。

以下都可以缺失或不参与 missing：

- metric；
- accounting basis；
- comparison basis；
- quantity value；
- trading session；
- principal participant 为空；
- schema type；
- forecast horizon。

因此 `missing_required_fields=0` 只能说明“满足 OPEN profile 的最小代码要求”，不能说明“满足业务身份边界”。

### 6.3 schema_projection 为空不是偶发模型错误

`single_document.py` materialization 明确写入 `schema_projection=None`；测试也明确说明 model-facing schema projection 按当前用户批准契约 deferred。

所以：

- 225/225 OPEN 是当前设计的确定性结果；
- 不能把它说成模型漏填；
- 但 N6 typed branches 因此完全不可达。

在不恢复 model-facing projection 的前提下，N6 必须从已解析的普通 Mention fields 构造足够强的 OPEN identity。

### 6.4 正确 Field link 被 Identity 丢弃

两个实际 Mention：

1. `mention:...e519701`
   - Micron FY2026 Q3 revenue $41.5B
   - metric link：REVENUE
2. `mention:...6815f15b`
   - Micron FY2026 Q3 GAAP gross margin 84.6% + adjusted EPS $25.11
   - metric links：GROSS_MARGIN / EPS

它们编译出的 Identity 完全相同：

```json
{
  "schema_type": "OPEN",
  "normalized_predicate": "PREDICATE_REPORT_METRIC",
  "principal_participant_ids": ["COMPANY_MU"],
  "reference_period_id": "field:ea802...",
  "assertion_state": "ACTUAL",
  "location_or_asset_ids": []
}
```

这证明：

- Field metric 即使正确，也不参与 identity；
- Grounder predicate factorization 把两者都变成 `report_metric`；
- N9 接收到的边界已经不足。

### 6.5 33-member supercluster

`atomic:bb912...` 包含 33 个 Mention：

- revenue；
- EPS；
- gross margin；
- free cash flow；
- net income；
- segment revenue；
- revenue growth；
- generic earnings。

该 cluster 产生：

- 528 个 predicted-positive pairs；
- 459 个 false pairs；
- 单一 cluster 占全部 Identity FP 的 68.4%（459/671）。

这说明不是“很多零散小错误”，而是一个粗 OPEN identity 加上 N9 relatedness merge 被组合放大。

### 6.6 Field 错误对 Identity 的双向影响

#### 过合并

- predicate collision 把不同 action 归为同一 canonical；
- participant collision 把不同主体归为同一实体；
- metric 本身不进 Identity；
- shadow-only hard cannot-link 没有阻止最终 merge。

#### 碎片化

- 同一 Q3 EPS 有的链接 `COMPANY_MU_FY2026_Q3`，有的链接 issuer-scoped provisional period；
- 同一 forecast 有的 EXPECTED，有的 ACTUAL/HYPOTHETICAL；
- 同一 metric 有的链接正确，有的 NEW/UNRESOLVED。

所以 Identity 同时产生：

- 671 个 false-positive pairs；
- 至少 23 个高置信 false-negative pairs。

### 6.7 最小平衡方案

不恢复 schema projection、也不增加 N5.5 Snapshot LLM Decide 的前提下，建议：

1. OPEN Identity 纳入已存在的 canonical quantity metric IDs；
2. 对多个 metric 使用稳定排序的集合，必要时让多 metric Mention保持不可与单 metric Mention自动同一；
3. 纳入已解析 accounting/comparison basis；
4. 把 trading session、analyst institution、business action 等少量高精度字段作为 hard discriminants；
5. unresolved metric/period 不应被当作缺失后继续粗合并，可使用 mention-scoped conservative discriminator；
6. 保留现有 `IDENTITY_COMPILED` 审计，并增加每个 hard dimension 的来源 field_path。

这比重新引入一个批量 LLM projection 节点更轻，也符合当前“reason 不改、payload 不膨胀”的约束。

## 7. Prompt / DTO / 编排责任矩阵

| 问题 | Prompt | DTO/Schema | 编排/验证 | KB/候选 | 主责任 |
| --- | --- | --- | --- | --- | --- |
| Dreamer clean omission | 有“target material”优先级 | 无 coverage target | 无 Gold-time coverage | - | Dreamer/业务覆盖 |
| 106 candidates 未进 Grounder | eventhood 边界略宽泛 | 无 candidate disposition | 无逐项覆盖校验 | - | Grounder协议 |
| Anthropic 复合 Mention | 已明确要求拆分 | attribute 可装动作性内容 | 无 action guard | - | 模型+验证 |
| Generic earnings umbrella | disclosure 可作为 event | - | split 后无反冗余规则 | - | Judge规则缺口 |
| Guidance ACTUAL | 已明确不应 ACTUAL | 单 assertion | 无 family/predicate/state compatibility | - | 模型+验证 |
| EXPECTED/HYPOTHETICAL | 缺少边界定义 | 枚举无描述 | - | - | Prompt/Schema |
| Comparison 进 attribute | 不够明确 | Quantity 无 comparison relation | Field 只解析 quantity metric | - | DTO/Prompt |
| Unsupported close | 未明确 current/close | predicate 开放字符串 | 无 source lexical guard | concept 噪声 | Prompt+Field |
| Main Evidence 非 exact | 已明确 exact | 可表达 exact text | 最终不阻断、不局部回退 | - | 模型+验证 |
| Attribute Evidence 歧义 | - | - | 未传 Main anchor | - | 编排 |
| Metric collision | Prompt 已正确 | raw 无 hard dims | wrong LINK 写 alias | XBRL 噪声 | 候选/KB |
| Participant wrong type | 仅后置 namespace policy | Participant 无 type | heuristic routing 先决定 namespace | 多 catalog alias | DTO/路由 |
| Fiscal period 50% | Prompt 已正确 | rolling/horizon 仍用同一字段 | document-global issuer | period coverage | 编排/Schema |
| Identity 29.8% precision | - | projection deferred | OPEN 不读 metric | Field 错误放大 | Identity编译器 |

## 8. 修复优先级建议

### P0：直接影响最终 Atomic 边界

1. **OPEN Identity 纳入 canonical metric、basis、session 等已有 Field links。**
   - 预期：直接降低 33-member、15-member、11-member supercluster 的误合并。
   - 风险：可能增加碎片；必须先只纳入高精度字段，并用 30 篇 pair Gold 回归。
2. **Grounder candidate disposition + coverage validation。**
   - 每个 candidate 必须标记 `CONSUMED/REJECTED/MERGED`，reason 用短 code；
   - 不需要改正常通过 reason 协议，也不需要大 reasoning payload。
3. **Mention-level deterministic compatibility guards。**
   - `GUIDANCE_EXPECTATION/guide_* + ACTUAL`；
   - 多 core action；
   - 相反 predicate + 多主体；
   - action-bearing open attribute。
4. **修复 v1 quantity multiplier 路径的 TRILLION。**

### P1：提高 Field 准确率

5. **Metric 两级候选池。**
   - 先 141 个 core business metrics；
   - 无覆盖时再进入 XBRL fallback；
   - 不让 XBRL lexical candidate 与核心指标同权。
6. **为 raw metric/predicate 生成确定性 hard dimensions。**
   - 与候选使用同一维度比较；
   - value/change、stock/index、count object、organic/constant-currency 必须 hard match。
7. **Participant mention-local type/routing。**
   - 至少增加 `entity_type_hint` 或确定性 router audit；
   - generic collective 优先 UNRESOLVED，不创建 named canonical。
8. **Fiscal issuer 改为 mention-local。**
   - mention participant > source ticker fallback；
   - rolling window/forecast horizon 不强行走 fiscal-period identity。

### P2：Evidence 与审计

9. **Attribute localization 使用 Main Evidence anchor。**
10. **Grounder Evidence 尾标点做确定性 source-equivalent 对齐。**
11. **保留 raw Evidence，不增加语义 repair LLM；但把 exact failure first-divergence 记录到 candidate/draft。**
12. **Field alias provenance。**
    - 记录 raw alias 首次由哪个 LINK 引入；
    - 允许离线发现自强化污染。

### P3：窄 Prompt 修订

13. 定义 EXPECTED vs HYPOTHETICAL。
14. 明确 `rose to/current at` 不等于 `closed at`。
15. 明确 split 之后不得保留无独立 artifact identity 的 generic umbrella disclosure。
16. 明确 analyst forecast/rating 与 generic opinion/background 的 eventhood 边界。
17. 明确 comparison/consensus/prior-period 数值必须进入 quantity/comparison 结构，而不是自由 attribute。

## 9. 下一轮验收应增加的诊断指标

### Mention

- Dreamer candidate Gold recall；
- Grounder candidate disposition coverage = 100%；
- candidate -> draft -> Judge -> Mention 转化矩阵；
- compound-action rate；
- multi-metric Mention rate；
- assertion compatibility violations；
- key quantities in `open_attributes` count。

### Evidence

- Main exact / normalized-equivalent / ambiguous / not-found 分开统计；
- Dreamer first-divergence 与 Grounder first-divergence 分开；
- Attribute anchor-disambiguated count；
- 仍保持 semantic repair LLM = 0。

### Field

- 错误 occurrence 与独立 primary 决策分别统计；
- deterministic LINK、candidate LINK、NEW、UNRESOLVED 分开；
- core metric pool 与 XBRL fallback 命中率；
- wrong-link alias propagation count；
- mention-local issuer selection accuracy。

### Identity

- typed/open profile 分布；
- 每个 profile 使用的 hard dimensions；
- metric-bearing Mention 的 metric-in-identity coverage；
- identical profile 下的 Gold conflict rate；
- Atomic Pair P/R；
- top collision cluster 对 FP 的集中度。

## 10. 最终判断

Mention、Evidence、Field、Identity 四部分不是四个独立问题，而是一条连续传播链：

```text
Dreamer/Grounder 的遗漏或复合
  -> Judge 未纠正边界/模态
  -> comparison 等信息落入弱类型 attribute
  -> Field 候选或路由发生 collision/unresolved
  -> OPEN Identity 丢弃即使正确的 metric links
  -> N9 在过粗 identity 上把 relatedness 当 sameness
```

就本轮四部分而言，最值得先做的不是“再写一版更长 Prompt”，而是：

1. 让 Grounder 对每个 candidate 有可审计处置；
2. 让 Identity 使用已经解析好的业务区分字段；
3. 把 Field 的 deterministic routing/KB 污染与 LLM 误判分开治理；
4. 只对已证实的 Prompt 缺口做窄修订。

这样能在不引入重型 N5.5 Snapshot LLM Decide、不改变 reason 协议、不扩大复杂节点 batch 的前提下，优先修复最终 `Package -> Atomic -> Mention` 结构最关键的身份边界。
