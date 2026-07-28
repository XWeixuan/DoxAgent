# CDECR Mention / Evidence / Field / Identity / KB 审慎优化方案

日期：2026-07-28  
依据：`CDECR_MENTION_EVIDENCE_FIELD_IDENTITY_DEEP_DIAGNOSTIC_20260728.md`  
性质：实施前方案；本文件不代表相关修改已经完成  
范围：Mention、Evidence、Canonical Field Resolution、Identity Compilation，以及它们实际使用的 v1/v2 Knowledge Base  
不在本轮范围：N7-N13 的 Atomic/Package LLM 决策逻辑全面重写

> 复杂度复核说明：本文保留完整的问题空间和备选设计，但不再作为默认实施清单。经复杂度复核后的正式实施范围，以 `CDECR_MENTION_EVIDENCE_FIELD_IDENTITY_KB_COMPLEXITY_REVIEWED_PLAN_20260728.md` 为准；其中已删除额外 residual LLM 节点、通用 KB overlay、Participant 新类型字段和显式 tri-state Identity schema 等高复杂度设计。

## 1. 方案目标

本方案不是把诊断报告中的 17 条建议机械展开，而是处理三个相互牵制的目标：

1. 修复错误合并，但不能把同一业务事实的多个表达、同一指标的上下界或同一交易的非独立条款拆成碎片；
2. 提升 Mention 与 Field 召回，但不能把泛泛背景、无归属观点和词面相似 KB 对象当成有效事实；
3. 提高 Field 和 Identity 精度，但不能通过大量 `UNRESOLVED` 或 mention-scoped 唯一键虚假获得高精度。

因此，每项修改都必须回答：

- 它修复的是哪个已观察到的首错；
- 它在哪一层执行；
- 它是否改变 LLM payload；
- 它可能造成什么反向错误；
- 用什么护栏和 paired A/B 指标判断是否放行；
- 失败时回滚哪一层，而不是回滚整个方案。

## 2. 总体判断

### 2.1 不建议采用的三种极端修法

#### 极端 A：把所有复合句都拆开

这会把以下合法结构错误碎片化：

- 同一 metric 的上下界、中点和容差；
- 同一指标的本期值、同比变化、市场预期；
- 同一 action 的多个互补 Evidence；
- 同一协议的期限、规模、定价机制等非独立条款。

真正应拆的是**能够独立改变 Atomic identity 的核心动作、核心主体、核心指标、Assertion State 或主要时间边界**，不是“句子里出现两个动词或两个数字”就拆。

#### 极端 B：要求每个 Dreamer candidate 都生成 Mention

357 个 candidate 中 106 个没有进入 Grounder draft，说明 Grounder 召回路径不可审计；但这不等于 106 个都应保留。它们混有：

- 真正漏掉的可验证 measurable/forecast fact；
- 有归属的 analyst view；
- 泛行业背景；
- 重复表述；
- 仅用于解释其他事件的上下文。

正确修复是要求每个 candidate 有处置，并对高价值拒绝进行受控复核，而不是 candidate 到 Mention 的 100% 转化。

#### 极端 C：把所有不确定 Field 都设为 mention-scoped 唯一值

这能迅速减少错误合并，却会把同一事实在不同文档中的正常复述全部打散。`UNRESOLVED` 应表示“不知道是否相同”，不能自动等价为“必然不同”。

### 2.2 建议的目标结构

```text
Source
  -> Dreamer high-recall candidates
  -> Grounder drafts + candidate disposition ledger
  -> deterministic consistency review triggers
  -> Judge correction
  -> exact Evidence materialization
  -> typed Field routing
  -> tiered KB candidate retrieval
  -> Field decision + alias quarantine
  -> tri-state Identity dimensions
  -> active high-confidence hard cannot-link
```

这里的关键不是增加一个重型 LLM 节点，而是把当前已经存在但没有闭环的信号接起来：

- candidate 是否被处理；
- Mention 中哪些 quantity 是核心值、比较值或边界；
- Field link 是高可信、低可信还是 unresolved；
- Identity 维度是 KNOWN、ABSENT 还是 UNRESOLVED；
- 哪些差异足够可靠，可以阻止自动同一；
- 哪些差异只够进入 LLM 判别，不能直接拆开。

## 3. 先修评估契约，否则优化会互相打架

### 3.1 统一 Mention atomicity Gold

冻结验收中存在一个直接冲突：

- Mention Gold 有时把同一期 revenue 与 EPS guidance 视为一条；
- Grounder/Judge Prompt 要求不同核心 metric 拆分；
- Atomic Gold 又要求不同 metric 不得自动同一。

实施前必须固定以下规则：

1. 同一 issuer、period、assertion、action 下，不同**核心 metric**各自形成 Mention；
2. 同一核心 metric 的 low/high/midpoint/tolerance 不拆；
3. consensus、prior-period、YoY/QoQ comparison 不单独形成 Mention，除非来源把“预期/比较本身”作为独立分析师行动；
4. artifact-level 动作只有在 artifact 本身是业务身份时才独立保留，例如“发布 10-Q”“签署协议”；
5. 当具体 metric Mention 已覆盖所有实质披露时，不再保留无独立 artifact identity 的 “reported earnings” 空壳；
6. 同一交易框架内的非独立条款留作 attribute；独立的 investment、supply commitment、partnership formation 等核心动作拆分后可在 Package 层关联。

这套规则既防过合并，也防“每个数字一条 Mention”。

### 3.2 统一 eventhood Gold

将候选分成三档：

| 档位 | 定义 | 例子 | 预期处置 |
| --- | --- | --- | --- |
| E1 独立事件/状态 | 有主体、可证伪命题，且有 action、metric、明确方向或计划 | unit sales expected to grow high teens | 应保留 |
| E2 有归属判断 | 明确 analyst/management claimant，且对标的有 rating、forecast、supply/demand 判断 | UBS expects DRAM constrained to 2028 | 通常保留为 ANALYST_ACTION/GUIDANCE_EXPECTATION |
| E3 背景/泛观点 | 无明确 claimant、时间、对象边界或可复核命题 | AI is changing the industry | 丢弃 |

“opinion”不能继续作为一刀切排除词。真正边界应是“是否有可审计归属和可独立判断的 proposition”。

### 3.3 明确主指标与比较量

建议在评估数据中标记：

- `PRIMARY`：决定 Mention/Atomic 核心身份；
- `COMPARISON`：consensus、prior period、YoY/QoQ 对照；
- `BOUND`：low/high/tolerance；
- `SUPPORTING`：规模、commitment 等不决定该 Mention 主 metric 的数量。

Identity 只直接使用 `PRIMARY`；其他角色用于语义完整性和下游属性，不用于把同一核心事实拆开。

## 4. Mention 优化方案

## 4.1 M1：Grounder candidate disposition ledger

### 修改

在 Grounder 输出中增加一个编排用途的紧凑数组，不改变 Judge reason：

```json
{
  "candidate_dispositions": [
    {"id": "c1", "code": "USED", "draft_ids": ["d1"]},
    {"id": "c2", "code": "DUPLICATE_OF", "draft_ids": ["d1"]},
    {"id": "c3", "code": "BACKGROUND", "draft_ids": []},
    {"id": "c4", "code": "NOT_INDEPENDENT", "draft_ids": ["d2"]}
  ]
}
```

建议 code 固定为：

- `USED`
- `DUPLICATE_OF`
- `NOT_INDEPENDENT`
- `BACKGROUND`
- `UNSUPPORTED`
- `OUT_OF_SCOPE`
- `REVIEW_NEEDED`

DTO validator 要求每个短 candidate ID 恰好出现一次；`USED/DUPLICATE_OF/NOT_INDEPENDENT` 必须引用 draft；其余不得引用不存在的 draft。

### 为什么不直接要求全部转成 draft

Disposition 解决的是审计和漏处理，不改变 eventhood 本身。一个被明确标为 BACKGROUND 的 candidate 仍可丢弃；真正异常的是 candidate 完全没有记录。

### 潜在负面影响

- 输出 token 小幅上升；
- 模型可能用 disposition 代替认真产出 draft；
- batch 中 candidate 越多，覆盖表可能增加漏 ID/错 ID。

### 护栏

- 使用短 ID 和短 code，不要求新增长 reasoning；
- disposition validator 失败时，只对缺失 ID 做局部结构修复，不重跑已合法 draft；
- `REVIEW_NEEDED` 比例设告警上限，避免模型把所有难题推给后续；
- 比较修改前后“最终 Mention 数量”和“candidate 处置分布”，不能只看 coverage=100%。

### 验收

- candidate disposition coverage = 100%；
- 已确认 Grounder 静默漏项的 fixed bad cases 全部进入 USED 或 REVIEW_NEEDED；
- BACKGROUND/OUT_OF_SCOPE 抽样 precision 不低于 95%；
- Mention precision 下降不得超过 1.5 个百分点。

## 4.2 M2：对高价值拒绝做受控 residual review

### 修改

不对全部 rejected candidate 再跑一轮。只将满足下列任一条件的 candidate 加入同一文档的短 residual review：

- 含明确 claimant + forecast/rating/price target；
- 含 metric/value/direction + 主体；
- 含 explicit plan/commit/sign/raise/cut/launch 等业务动作；
- 含供需、产能、销量等可验证 ongoing state，并明确对象和方向；
- disposition=`REVIEW_NEEDED`。

Residual review 只回答：

- `RESTORE_AS_DRAFT`
- `KEEP_REJECTED`

并保留短 reason code。它不允许改写其他 draft。

### 潜在负面影响

- 召回提升可能带入 analyst chatter 和行业背景；
- 增加少量 LLM 调用；
- 同一候选可能与已生成 draft 重复。

### 护栏

- residual 输入必须带现有 draft 的短摘要，用于 duplicate 检查；
- generic opinion、无 claimant、无可证伪 proposition 不进入 residual；
- 恢复项仍经过 Judge；
- 单文档 residual 数设上限，但上限由“符合规则的候选数”决定，不降低原 Grounder batch。

### 验收

- 已知 `unit sales high teens`、有归属 analyst forecast、量化 earnings/valuation 漏项应恢复；
- 恢复 Mention 的人工 precision ≥ 85%；
- 原先正确丢弃的背景项回流率 ≤ 5%。

## 4.3 M3：Dreamer 只补覆盖清单，不扩 candidate 上限

当前 30 篇 candidate 最大 23，没有触及 24，因此不建议先提高 cap。

在 Dreamer Prompt 增加一条很短的内部覆盖检查：

> Before returning, check for omitted attributed forecasts/ratings, explicit plans or commitments, measurable state changes, and supply/demand/capacity/volume claims with a named subject.

不要求输出 checklist，避免 payload 增加。

### 潜在负面影响

- 候选更接近上限；
- 可能增加次要行业事实；
- candidate 重复率上升。

### 护栏

- 保留 target ticker materiality 优先级；
- 只覆盖有主体、可判断 proposition；
- 按 Dreamer candidate precision 和 Grounder rejection 分布共同验收，不能只看 candidate recall。

## 4.4 M4：Atomicity consistency guard

### 修改

在 Grounder 后、Judge 前增加确定性“复核触发器”，而不是自动拆分器。触发条件：

1. 两个以上独立核心 action；
2. 两个以上核心 subject 且行为/方向不同；
3. 同一 Mention 同时有 ACTUAL result 与 EXPECTED guidance；
4. 两个以上不同 PRIMARY metric；
5. open attribute 含 action-bearing 结构，如 investment/sign/acquire/supply commitment；
6. 同一 predicate 下出现相反方向或相反交易角色。

被触发的 draft 在 Judge 输入中携带短 `review_flags`，例如：

- `MULTI_CORE_ACTION`
- `MULTI_PRIMARY_METRIC`
- `MIXED_ASSERTION`
- `OPPOSING_DIRECTION`

### 不应触发拆分的情形

- 同一 metric 的上下界、容差、中点；
- 同一交易的金额、期限、地域、非独立条款；
- 同一主体和 action 下的多个 Evidence；
- comparison/consensus/prior 值；
- 一个动作的原因与修饰语，除非原因本身是独立事件。

### 为什么不直接自动拆

词面多动词不必然代表多事件。“announced it had signed”可能只是一个 sign event；“entered partnership including investment and supply agreement”则可能包含多个独立 action。前者可由 Judge结合 Evidence 判断，纯规则无法可靠拆。

### 潜在负面影响

- Judge 可能在看到 flag 后过度拆分；
- 多 metric 的复杂披露可能产生大量 Mention；
- action keyword 可能误命中普通 attribute。

### 护栏

- flag 是复核提示，不是结论；
- Judge Prompt 同时加入“不要拆同 metric 的 bounds/comparisons/terms”；
- split 后做反向 recomposition check：若两个新 Mention 的主 action、subject、PRIMARY metric、assertion、time 完全相同，则判为疑似碎片；
- 对 split 数量增加设 per-document 告警，不设硬低上限。

## 4.5 M5：窄化 umbrella suppression

只在以下条件全部满足时删除 generic umbrella：

1. umbrella 与具体 Mention 来自同一 draft/split lineage；
2. umbrella 没有独立 artifact ID、action、time 或 claimant；
3. 具体 Mention 已覆盖 umbrella 的全部实质 Evidence；
4. 删除不会丢失“发布/提交/签署”本身的独立业务动作。

这样能删除 `reported earnings` 空壳，但不会删除真正的 filing、earnings release 或 agreement execution。

## 4.6 M6：QuantityDraft 增加最小角色信息

### 建议 DTO

```json
{
  "metric_id": "revenue",
  "value": 41.5,
  "unit": "billion USD",
  "raw_text": "$41.5 billion",
  "role": "PRIMARY",
  "basis": null
}
```

`role`：

- `PRIMARY`
- `COMPARISON`
- `BOUND`
- `SUPPORTING`

`basis` 只在必要时使用短枚举：

- `YOY`
- `QOQ`
- `CONSENSUS`
- `PRIOR_PERIOD`
- `LOW`
- `HIGH`
- `TOLERANCE`
- `OTHER`

不建议增加自由文本 reasoning 或复杂 quantity graph。

### 编排规则

- comparison/consensus/prior-period 值必须优先进入 `quantities`；
- `open_attributes` 中若发现 currency/percent/count 且 key 属于 estimate/prior/change/range，应触发 quantity placement review；
- Field resolver 只对 PRIMARY quantity 的 metric 做 Identity hard dimension；其他 role 的 metric link用于语义和审计。

### 潜在负面影响

- JSON 体积增加；
- 模型可能误把 comparison 作为新的 PRIMARY；
- 多 PRIMARY 会触发拆分，可能碎片化。

### 护栏

- 同一 Mention 默认恰好一个 PRIMARY metric；多个 PRIMARY 必须经过 atomicity review；
- BOUND 必须与一个 PRIMARY metric 同 base measure；
- comparison 缺 basis 时可以保留 UNKNOWN，不强迫猜测；
- evaluator 同时检查“信息是否保存”和“字段位置是否正确”。

## 4.7 M7：Assertion State 兼容性使用“语义模式”，不盲目强改

需要区分两种 proposition：

| Proposition | event_family/predicate | assertion |
| --- | --- | --- |
| 公司实际发布了 guidance | disclosure/issue_guidance | ACTUAL |
| 被 guidance 的未来 revenue/EPS 将达到某值 | GUIDANCE_EXPECTATION/guide_metric | EXPECTED |

兼容性 guard：

- `GUIDANCE_EXPECTATION + guide_metric + ACTUAL` 触发局部 Judge correction；
- `likely/expected/forecast` 有明确 claimant 时优先 EXPECTED；
- HYPOTHETICAL 仅用于条件命题、场景、反事实或没有形成预测判断的可能性；
- `could` 不能单独决定 HYPOTHETICAL，必须看 claimant 是否在表达 forecast。

### 潜在负面影响

- 可能把“guidance issuance”错改成 EXPECTED；
- EXPECTED 与 PLANNED 边界可能漂移。

### 护栏

- 先判断 proposition 模式，再校验 state；
- guard 不直接覆盖字段，而是生成 `ASSERTION_INCOMPATIBLE` review；
- 建立最少 20 个 ACTUAL issuance / EXPECTED underlying / PLANNED action / HYPOTHETICAL scenario 对照集。

## 4.8 M8：Time 与 forecast horizon 分开

短期不扩复杂 EventTime。建议：

- 明确 weekday + published_at 可以确定性解析日期的条件；
- `after-hours/pre-market/regular session` 进入短 session field/attribute；
- `beyond 2027`、`through 2030` 等 forecast horizon 不再强行进入 fiscal period；
- forecast horizon 可先用受控 attribute key：`forecast_horizon`，其 value 保留原文，identity 只在双方都明确且冲突时作为 cannot-link。

潜在风险是 horizon 过度进入 Identity 导致同一长期 forecast 被不同措辞拆开，因此短期只用于 hard conflict，不用于自动 same-identity。

## 5. Evidence 优化方案

## 5.1 E1：Attribute localization 使用 Main Evidence anchor

`reconcile_evidence_text()` 已支持 `candidate_anchors`，当前 materializer 未传。修改为：

1. 先定位 Main Evidence；
2. 对同一 Mention 的 attribute Evidence，优先选择与 Main span 重叠或距离最近的同文本 occurrence；
3. 仍有多个同距候选时标记 AMBIGUOUS，不猜。

### 风险

- attribute Evidence 可能合法来自另一个句子；
- 最近 occurrence 不一定语义对应。

### 护栏

- overlap 优先于 distance；
- attribute 自带 segment 时只在同 segment 消歧；
- 没有合理 anchor 时保持 AMBIGUOUS，不强行 VERIFIED；
- 保留 raw text 和所有 candidate offsets 到审计，不写入 LLM payload。

## 5.2 E2：终端 exact-substring validator 与局部回退

对 Dreamer/Grounder/Judge 的 Evidence 采用分级处理：

1. exact span；
2. exact text 在同 segment 唯一出现；
3. 仅尾部标点差异且 source 中存在无标点版本；
4. normalized whitespace-equivalent；
5. ambiguous；
6. not found。

允许第 2-4 级做确定性 locator 修正，但 persisted Evidence text 必须回写为 source 原文，不保留模型合成标点。

不得做：

- 把 `Sandisk's archrival` 改写为 `Micron` 后当 exact Evidence；
- 合并 ellipsis 两段为一个 quote；
- 用语义相似文本替换原文。

### 风险

- normalized repair 可能掩盖模型持续不遵循 exact quote；
- 标点处理可能跨句。

### 护栏

- 每次回退记录 `first_divergence_stage` 和 `repair_kind`；
- evaluator 同时报告 raw model exact rate 与 final persisted exact rate；
- 只允许 whitespace/terminal punctuation，不允许词替换。

## 5.3 E3：审计状态分型

将当前笼统的 `TEXT_NOT_FOUND` 拆为：

- `EXACT`
- `SOURCE_EQUIVALENT`
- `AMBIGUOUS`
- `NOT_FOUND`
- `INVALID_MODEL_QUOTE`

这只是审计与统计变化，不删除 Evidence，不增加 LLM reasoning。

## 6. Field Resolution 优化方案

## 6.1 F1：raw side 与 candidate side 使用同一 hard dimensions

当前 candidate 有 heuristic hard dimensions，raw input 没有同构字段。建议在编排层从 raw label + local context 解析：

### Metric

- `base_measure`
- `scope`
- `transformation`
- `instrument_or_business_object`
- `accounting_basis_explicit`

### Predicate

- `action`
- `object_class`
- `direction`
- `lifecycle`

### Participant

- `type_hint`
- `generic_or_collective`
- `ticker_context`
- `claimant_context`

### 使用原则

- raw 与 candidate 明确 hard conflict 时不进入 LLM candidate；
- raw 某维度 UNKNOWN 时，不判 conflict；
- hard dimension 不用于“相似即自动 LINK”，只用于排除不可能项和候选排序。

### 风险

- heuristic parser 错误会提前删掉正确 candidate，直接伤害 recall。

### 护栏

- hard prune 只允许高精度词法规则，例如 explicit `growth` vs value、`index` vs company metric、`stock` vs product；
- 低置信维度只降权不删除；
- 每个被 prune candidate 进入审计；
- fixed corpus 要计算 candidate recall@8，不能只看最终 Field accuracy。

## 6.2 F2：Exact/unique KB 命中不再天然可信

当前 `deterministic_match()` 会优先显式 ID、exact canonical name，否则唯一候选；这对受控 ontology 合理，对开放实体 alias 风险很高。

建议把 deterministic link 分三级：

| 级别 | 条件 | 动作 |
| --- | --- | --- |
| T1 trusted | 显式 canonical ID、source ticker 与公司 ticker 一致、受控 core ontology exact | 可直接 LINK |
| T2 contextual exact | alias exact，但存在跨目录/同目录 collision 或短词 | 进入候选判别 |
| T3 weak unique | 仅因为 KB 当前只返回一个结果 | 不自动 LINK，按普通候选处理 |

### 风险

- 取消 unique auto-link 会增加 LLM 调用和 UNRESOLVED；
- 对知名公司全名可能不必要地变慢。

### 护栏

- 公司全名 + ticker/source hint 仍属 T1；
- core metric/predicate 受控 exact 仍属 T1；
- 只把 collision、短 alias、generic-like 表面降为 T2；
- 对比 deterministic precision 与 LLM call 增量。

## 6.3 F3：Metric 两级候选池

### 当前事实

- Metric 共 9,257 条；
- 141 条非 `US_GAAP_`/`XBRL_`；
- 8,443 条 US-GAAP；
- 673 条 XBRL custom；
- 构建脚本显式补齐的核心集合只有 15 个；
- 目录内有 47 个 normalized alias collision；
- `adjusted EPS` 同时属于 `ADJUSTED_EPS` 和 `EPS_NON_GAAP`；
- `share price` 同时属于 core 与 US-GAAP 表项；
- runtime metric query 会移除 `adjusted/non-GAAP/GAAP/consensus/guidance` 等词，使 basis 信息从检索 query 中消失。

### 修改

#### Tier A：Business Core

从现有 141 条中清理出稳定业务 metric ontology，覆盖：

- 财务结果；
- guidance；
- 市场价格/成交量；
- analyst target/rating quantity；
- 运营 KPI；
- 供需/产能/销量；
- 交易/协议规模。

#### Tier B：Reporting Tag Fallback

US-GAAP/XBRL 只在以下条件进入：

- raw value 明确是 taxonomy tag；
- source 是 filing；
- core metric 无覆盖；
- local context 需要非常具体的会计项目。

普通新闻里的 `trading_volume`、`deal_count` 不应和整个 XBRL 表同权。

### Metric ontology 清理规则

1. 会计 basis 从 metric identity 中剥离：`adjusted/non-GAAP/GAAP` 优先进入 accounting basis；
2. transformation 保留：revenue、revenue growth、margin、yield 是不同 metric；
3. material business scope 保留：company revenue 与 data-center revenue 不同；
4. 删除或 redirect 纯重复 canonical，例如 `ADJUSTED_EPS` 与 `EPS_NON_GAAP` 只能保留一个主 canonical；
5. tag-specific 项保留 source tag crosswalk，但不作为普通新闻首层候选；
6. 新增 `TRADING_VOLUME`、`CLOSING_PRICE/SESSION_PRICE`、`DEAL_COUNT` 等本次已证实缺口；
7. `guaranteed_revenue` 应根据语义决定是 commitment/backlog/contracted revenue，不能自动链接 adjusted revenue。

### 反向风险

- 过度 collapse GAAP/non-GAAP 会错误合并；
- core pool 太小会降低长尾 filing recall；
-新增 metric 过多又会恢复候选噪声。

### 护栏

- raw 明示 basis 而 basis resolver 未成功时，不允许静默链接到无 basis identity；
- Tier B fallback 保留，不删除底层源知识；
- core 新增需至少有两个真实 corpus example 或明确业务定义；
- 每个 core metric 有正例、近邻反例和 hard dimensions 测试。

## 6.4 F4：Predicate ontology 与 Grounder factorization 对齐

### 当前问题

Grounder 要求 action-only，但 raw predicate 仍产生 `trade_stock`、`change_index` 等混合 token；KB string recall 又把它们拉向 `split stock`、`change pricing` 等近词。

### 修改

1. 建立受控 action vocabulary：
   - report
   - guide
   - announce
   - sign
   - complete
   - plan
   - expect
   - trade/move
   - close
   - raise/lower target
   - upgrade/downgrade
   - invest
   - supply/commit
2. object/direction 由 context/hard dimensions表达，不把错误 object 强塞进最相似 predicate；
3. exact 受控 action 可直接 LINK；
4. 没有同 action/object/direction 候选时优先 NEW/UNRESOLVED，禁止低分“最像就 LINK”；
5. 对 `rose to` 与 `closed at` 建立 lexical incompatibility。

### 反向风险

- action 过粗会让 report revenue/report EPS 都变成 report；
- action 过细会导致近义表达碎片化。

### 护栏

- metric/object 不靠 predicate 区分，而由 Field/Identity 其他维度补齐；
- 同义 action 可 canonicalize，生命周期和方向不能丢；
- predicate identity 的 positive/negative pair 单独验收。

## 6.5 F5：Alias 写入改为 quarantine + provenance

当前错误 LINK 会把 raw value 写入 canonical aliases，可能让一次错误变成后续 exact link。

### 修改

Alias 分三类：

- `SOURCE_ALIAS`：KB 构建时来源明确；
- `CONFIRMED_RUNTIME_ALIAS`：多次独立高可信 LINK 或人工确认；
- `OBSERVED_SURFACE`：单次 LLM LINK，仅供审计和 embedding，不参与 deterministic exact。

单次 LLM LINK 只写 `OBSERVED_SURFACE`。满足以下任一条件才晋升：

- 两个独立文档、相同 canonical、无 hard conflict；
- trusted ticker/full-name rule；
- 人工/离线 gold 确认。

### 风险

- alias 学习速度下降；
- 重复 LLM 调用增加。

### 护栏

- observed surface 仍可作为低权候选信号；
- 晋升规则可离线批处理；
- 每个 alias 保留 first_seen run/mention/decision/provenance；
- 支持撤销单个污染 alias，而不是清空 Registry。

## 6.6 F6：Participant type 先于 entity link

### DTO 最小修改

给 `ParticipantDraft` 增加可空短枚举：

- `COMPANY`
- `INSTITUTION`
- `PERSON`
- `INSTRUMENT`
- `GENERIC_COLLECTIVE`
- `BUSINESS_UNIT`
- `PRODUCT_OR_TECHNOLOGY`
- `UNKNOWN`

这不是 canonical ID，不要求模型知道 KB。

### 路由优先级

1. explicit type_hint；
2. claimant/participant role + local context；
3. source ticker/full company name；
4. 多 catalog exact；
5. 多 catalog fuzzy；
6. generic collective -> UNRESOLVED typed collective，不创建 named entity。

### 已知 case

- UBS 在 analyst claim 中优先 INSTITUTION；
- Stoxx 600 是 INSTRUMENT/INDEX；
- institutions、analysts、management 是 GENERIC_COLLECTIVE；
- `Micron management` 应保留为 COMPANY_MU 的 management collective，不链接 Carson Management；
- business unit 不是 company；
- Nasdaq listing venue 不是 PLACE。

### 反向风险

- Grounder type_hint 错误会把正确 candidate 排除；
- 同一名字确实可能兼具 company/institution/instrument facet；
- generic collective 处理过严会丢失真正命名机构。

### 护栏

- type_hint 与强 ticker/exact evidence 冲突时进入 participant.unknown，而不是硬锁；
- generic 判定需要 exact词表或明确 collective morphology；
- Named entity 与 generic phrase 分别建测试；
- participant type accuracy 和 entity accuracy 分开统计。

## 6.7 F7：Fiscal issuer 改为 mention-local

### 修改

issuer 选择顺序：

1. 当前 Mention 的 SUBJECT/ACTOR company link；
2. 当前 Mention 的明确 business unit owner company；
3. 当前 Mention 的 schema issuer（若未来启用）；
4. source ticker hint；
5. 文档其他 Mention 只能作为低可信 fallback，不能直接 first-match。

并记录：

```json
{
  "issuer_id": "COMPANY_MU",
  "source": "MENTION_SUBJECT",
  "field_path": "participants[0]",
  "fallback_used": false
}
```

### 时间类型路由

- explicit FY/Q -> fiscal period；
- rolling window -> relative window；
- forecast horizon -> horizon；
- calendar date/quarter -> calendar period；
- 无法确定类型时不强行 fiscal。

### 风险

- Mention participant 本身链接错时会传染 period；
- source ticker fallback 对同行/市场 Mention不适用；
- 取消文档共享 issuer 会降低省略主语的 period recall。

### 护栏

- participant link 必须达到 trusted tier 才作为 issuer；
- source ticker 只用于 proposition 主体可解析为 target company/其业务单元的情形；
- 省略主语可使用相邻 Evidence coreference，但记录 fallback；
- actual vs synthetic period 分开。

## 6.8 F8：Location 与 Attribute 路由窄化

### Location

- `locations[]` 只接受地理地点；
- exchange/index/listing venue 在上游就路由为 institution/instrument；
- place candidate 增加 country/admin context，不能只暴露 name/alias；
- 短地名 exact hit在无国家/地区上下文时不自动 LINK。

### Attribute

- 从 `lifecycle_stage` alias 删除过宽的 `status`；
- `status=record levels` 路由 qualitative magnitude，不是 lifecycle；
- `market` 不能默认 PLACE，应区分 end market、exchange、geography；
- customer/supplier/counterparty 的 target type不能全部固定为 COMPANY 或 LITERAL，应结合 participant type。

### 风险

- attribute route变窄会增加未解析字段；
- `status` 在真实 lifecycle case 中仍有价值。

### 护栏

- key + value 联合路由，不只看 key；
- unknown attribute 保留原值，不因未解析而删除；
- attribute identity 只使用明确 HARD 且高精度的 route。

## 7. Identity 优化方案

## 7.1 I1：OPEN Identity 引入 tri-state hard dimensions

当前 OPEN profile只使用：

- predicate；
- principal participants；
- event time/reference period；
- location/assets；
- assertion state。

建议新增：

- `primary_metric_ids`
- `accounting_basis`
- `comparison_basis`（只在决定核心命题时）
- `trading_session`
- `analyst_institution_id`
- `forecast_horizon`

每个维度不是简单 nullable，而是：

- `KNOWN(value)`
- `ABSENT`
- `UNRESOLVED(raw_fingerprint)`

### 语义

- 两边 KNOWN 且值冲突：可以 hard cannot-link；
- 一边 KNOWN、一边 ABSENT：不能自动当 same，也不能直接判 different；
- 两边 UNRESOLVED：不能因都 unresolved 而认为相同；
- raw fingerprint 只有在非常明确的 lexical conflict 时可阻止合并，不作为自动相同依据。

## 7.2 I2：Metric 不要求集合完全相等，先实施 disjoint cannot-link

直接把整个 metric set 拼进 identity equality 会导致：

- `{revenue}` 与 `{revenue, EPS}` 永远不同；
- 一个 Mention 漏掉 metric 时形成碎片；
- comparison metric 误入集合后拆散本来相同的事实。

更平衡的第一步：

1. 仅取 PRIMARY metric；
2. 双方都有高可信 metric 且集合不相交 -> hard cannot-link；
3. 集合相交但不相等 -> 交给 N9 判断，不自动 same；
4. 任一方缺失/低可信 -> 不使用 metric 做 hard 决策；
5. 多 PRIMARY Mention 先回到 Mention atomicity review。

这能阻止 revenue 与 EPS/gross margin 的超大误合并，又不会因为单边字段遗漏直接拆散。

## 7.3 I3：只让高可信 Field link进入 hard Identity

建议 trusted 条件：

- curated core KB T1 exact；
- source ticker/full-name contextual exact；
- Field LLM LINK 且 raw/candidate hard dimensions 无冲突；
- confirmed runtime alias；
- issuer-scoped actual fiscal period。

以下只作为 soft：

- 单次 observed alias；
- fuzzy-only candidate；
- synthetic future fiscal period；
- generic participant；
- unresolved provisional。

### 风险

- trusted 门槛过高会让 Identity仍然过粗；
- trusted 门槛过低会把 Field 错误变成 false cannot-link。

### 护栏

- 每个 Identity dimension 保存 source field_path、registry ID、link method、trust tier；
- 每种 link method 单独计算人工 precision；
- 只有实测 precision 达到阈值的维度才从 shadow 升为 active。

## 7.4 I4：Hard cannot-link 分维度逐步放行

当前 664 个 violation 是 shadow。不能一次性把所有 shadow rule 激活。

建议顺序：

1. 不同高可信 PRIMARY metric；
2. ACTUAL vs EXPECTED/PLANNED；
3. 不同高可信 issuer；
4. pre-market vs after-hours/regular session 的同日 market move；
5. 明确不同 fiscal period；
6. predicate action/lifecycle hard conflict。

每一类独立 A/B；若 recall 回退超阈值，只回退该维度。

## 7.5 I5：Cluster circuit breaker 只做安全阀

对超过 12 个成员或预计 pair 数超过 66 的 Identity bucket：

- 不自动拆；
- 强制输出 collision audit；
- 检查 metric、period、assertion、session 分布；
- 若存在已启用 hard conflict，则先分区；
- 剩余部分继续进入正常 N9。

它防止 33-member supercluster 无声扩散，但不以“cluster 大”本身作为不同身份的证据。

## 8. KB 全面现状审计

## 8.1 当前验证结果

本轮对当前工作树 `src/cdecr/catalogs/v2` 重新执行了只读 validator：

- `valid=true`
- errors = 0
- catalog hash：`82470113a3e7f4c49e1e7d31ff41673f122a94e4843a80aa46f9988b2bdb3c78`

| Catalog | Records | 单目录 normalized alias collision |
| --- | ---: | ---: |
| companies | 8,335 | 139 |
| institutions | 28,865 | 371 |
| persons | 99,594 | 16,548 |
| instruments | 40,354 | 3,096 |
| places | 302,720 | 62,887 |
| named_objects | 102,243 | 5,849 |
| concepts | 263 | 14 |
| metrics | 9,257 | 47 |
| fiscal_periods | 259,943 | 504 |
| units | 1,664 | 298 |
| artifacts | 248,044 | 1,880 |
| attributes | 77 | 0 |

结构合法不代表适合当前业务解析。Validator 当前主要验证：

- schema；
- ID 唯一；
-单目录 alias collision；
- reference integrity；
- enum/date。

它没有判断：

- 跨 catalog alias collision；
- alias 是否过于 generic；
- core/runtime tier；
-概念是否重复；
- source provenance/confidence；
- 业务 hard dimensions 是否完整；
- 一个 exact alias 是否足以支持 deterministic LINK。

本轮额外统计，companies/institutions/persons/instruments/named_objects 五类之间存在 **12,816 个 normalized surface 跨目录碰撞**。这不是 12,816 个错误，但说明“单目录合法”远不足以支持跨类型自动链接。

## 8.2 KB 与当前业务需求的不匹配

### K1：源知识覆盖库与运行时候选库没有分层

当前大目录同时承担：

- source archive；
- exact lookup；
- fuzzy candidate recall；
- deterministic link依据。

这让“尽量全”与“候选必须纯”发生冲突。KB 构建目标是广覆盖，但 Field 的目标是高精度判别，两者不应使用同一 active view。

### K2：Metric taxonomy 同时编码 metric、basis 与 reporting tag

例子：

- `ADJUSTED_EPS`
- `EPS_NON_GAAP`
- `EPS_GAAP`
- `EPS`

其中一部分区别应属于 accounting/share basis，而不是四个并列 metric。runtime query 又会移除 adjusted/non-GAAP/GAAP，导致这些对象更容易同候选出现。

### K3：Participant catalog 是多来源 facet，不是统一实体图

`UBS` 同时命中：

- 两个 company ID；
-一个 institution ID；
-一个 instrument ID。

这在业务上部分合理：上市公司、研究机构 facet、证券不是同一个使用角色。但缺少 parent organization/crosswalk 和 context policy，就会变成错误路由。

其他真实碰撞：

- Micron company vs named facility；
- Sandisk company vs named facilities；
- Western Digital company vs facility；
- Nasdaq company vs institution；
- Dow Jones Newswires vs Dow Jones index。

### K4：Person/Place 依赖上下文，但 KB schema没有足够上下文

- Person 有 16,548 个 alias collision 和 19,507 个 duplicate normalized names；
- Place 有 62,887 个 alias collision；
- candidate payload 对 Place 没有 country/admin metadata；
- Person 同名时 org 信息没有成为 hard candidate dimension。

因此这些目录不适合“表面唯一即确定性链接”。

### K5：Named Object 结构被 facility 主导

102,243 个 named objects 中：

- FACILITY 81,990；
- ASSET 8,000；
- PRODUCT 7,195；
- TECHNOLOGY 3,275；
- PROGRAM 1,727；
- PROJECT 56。

当前 participant fuzzy route 会同时扫 named_objects，而业务新闻中 generic product/technology/business unit 很常见。没有 owner/kind强约束时，facility 词面会污染 participant 路由。

### K6：Fiscal Period 的 synthetic future date 缺少 provenance

当前 fiscal period 包含按 364 天平移生成的未来财年。它提高 coverage，但：

- 53 周财年可能不准确；
- 公司财历可能变化；
- synthetic 与 actual filing-derived 记录在 schema 中没有明确可信度区别。

作为 candidate 可以，作为 hard identity必须区分。

### K7：Unit v2 更完整，但运行时 quantity normalization 仍读 v1

- v2 有 TRILLION；
- v1 没有；
- `mention_finalization.py` 与 `normalization.py` 仍读 v1；
- 由此出现 `$1.2 trillion -> 1.2 USD`。

同时 v2 units 有 1,664 条，其中 1,478 条为 UNIT，包含大量 XBRL source-specific 形式，且有 298 个 collision。运行时数量解析其实只需要一个较小、稳定的 active unit view。

### K8：Artifact catalog 种类与业务新闻不匹配

当前 248,044 条 artifact 只有：

- SEC_FILING 156,100；
- synthetic EARNINGS_RELEASE 91,944。

Schema 虽预留 PRESS_RELEASE、ANALYST_REPORT、AGREEMENT、REPORT，但当前没有实际覆盖。对新闻工作流而言：

- 静态 KB 不可能预先覆盖全部 analyst report 和 agreement；
- synthetic earnings release 不能等价于有来源证明的真实 release；
- 更适合从 source URL/accession/date 动态建立 artifact identity。

### K9：Attribute route 仅按 key，过宽 alias 会误路由

`status` 是 lifecycle_stage alias，导致 `record levels` 被路由到 lifecycle。`market` 默认 PLACE 也会混淆 geographic market、end market、exchange 和 instrument。

## 9. KB 重构方案

## 9.1 采用“Source Catalog + Runtime Resolution View”

不建议直接删除底层大目录。建议：

```text
Source Catalog
  保存可复现的 SEC/FASB/GeoNames/EPA/Wikidata 数据
        |
        v
Resolution Overlay
  redirects / tier / provenance / ambiguity / hard dimensions
        |
        v
Runtime Active View
  按 namespace、context、tier 提供小而纯的候选
```

这样允许对 KB 做大范围治理，同时：

- 不丢失长尾；
- 可以回滚 overlay；
- 可以分别更新 source 与 runtime policy；
- 不需要把 12 个大 JSON 全部人工重写。

## 9.2 建议增加的 overlay 字段

不必立刻破坏 12 catalog 原 schema，可以先新建可生成的 overlay：

```json
{
  "external_id": "ADJUSTED_EPS",
  "status": "REDIRECT",
  "redirect_to": "EPS",
  "tier": "CORE",
  "provenance": "CURATED",
  "ambiguity": "ACCOUNTING_BASIS_ENCODED",
  "hard_dimensions": {
    "base_measure": "earnings per share",
    "transformation": "value"
  }
}
```

字段：

- `status`: ACTIVE / FALLBACK / REDIRECT / DEPRECATED
- `tier`: CORE / DOMAIN / SOURCE_TAG
- `provenance`
- `redirect_to`
- `ambiguity_class`
- `hard_dimensions`
- `context_requirements`

## 9.3 Catalog 逐类治理

### Metrics

- 大范围清理 core 141 条；
- 合并/redirect basis 重复项；
-补齐 trading volume、session price、deal count、commitment/backlog 等实际新闻指标；
- US-GAAP/XBRL 降到 filing fallback；
- 新增 positive/negative examples。

### Concepts

- Predicate 与 guidance_action/analyst_action 允许概念复用，但 lookup 必须继续按 kind；
- 清理跨 lifecycle/direction 的 alias；
- action vocabulary 与 Grounder factorization 对齐；
- `close`、`trade/move`、`price target` 建立硬区别。

### Companies / Institutions / Instruments

- 建 organization facet crosswalk；
- ticker 只在 ticker context 中是高可信；
- 短 ticker/缩写默认 collision-sensitive；
- 公司、研究机构、证券不直接 collapse 为一个 ID；
- source claimant 场景使用 institution facet。

### Persons

- org_id、title/role、source date进入 candidate hard dimensions；
- 单名、同名或缺 org 的 person 不 deterministic LINK；
- 卖方 analyst 长尾优先动态 provisional + org scope，不虚假链接 SEC officer。

### Places

- candidate 增加 country/admin；
- same-name place 必须有地理上下文；
- exchange/index 从 location route 移除；
- 不需要删除 GeoNames 大库，但 active recall先按 source locale/context过滤。

### Named Objects

- owner_id 和 kind作为硬筛选；
- business unit单独建受控类型或 owner-scoped provisional；
- generic industry/product class 不链接到随机 named object；
- participant strong product route 必须按 kind过滤，不能把 facility candidate 放入 product namespace。

### Fiscal Periods

-标记 ACTUAL / DERIVED；
- derived future date只作 candidate，不直接成为 hard date truth；
- issuer-local parser优先；
- calendar/rolling/horizon 分流。

### Units

- active runtime view只保留常用 currencies、scale、ratio 和业务 unit；
- XBRL unit留作 filing fallback；
- v1/v2 quantity normalizer统一到同一 active unit source；
- multiplier 与 currency/unit 分开解析，覆盖 `trillion USD`。

### Artifacts

-静态 KB保留 filing；
- synthetic earnings release标记 SYNTHETIC，不声称真实 artifact；
-新闻稿、分析师报告、协议使用 source-derived dynamic artifact identity；
- accession/URL/date/owner为主要 identity，不依赖标题 fuzzy match。

### Attributes

- key + value联合 routing；
-删除过宽 alias；
-明确 HARD/SOFT/CLAIM 不等于是否进入 Identity；
-只有高可信 HARD route可参与 cannot-link。

## 9.4 Validator 扩展

新增：

1. cross-catalog collision report；
2. generic/short alias severity；
3. redirect cycle/target 校验；
4. active tier duplicate identity；
5. hard dimension completeness；
6. source-tag 是否误入 core；
7. synthetic provenance；
8. runtime active view大小；
9. business regression query set：
   - trading_volume
   - stock_change
   - closing_price
   - guaranteed_revenue
   - deal_count
   - revenue_growth
   - UBS
   - IDC
   - Micron management
   - Stoxx 600
   - Nasdaq

Collision 不要求全部清零。每个 collision 应被分类：

- ALLOWED_FACET
- CONTEXT_REQUIRED
- REDIRECT
- INVALID_ALIAS

## 10. 分阶段实施顺序

## Phase A：评估契约与只读审计

1. 修正 Mention atomicity/eventhood Gold；
2. 增加 candidate disposition audit schema；
3. 增加 Evidence 状态分型；
4. 增加 Field link trust/provenance 和 cross-catalog KB audit；
5. 固化本次 30 篇和所有 named bad cases。

本阶段不改变最终业务输出。

## Phase B：低风险确定性修复

1. Attribute Evidence anchor；
2. terminal punctuation/source-equivalent locator；
3. v1/v2 unit normalization统一和 TRILLION；
4. mention-local fiscal issuer；
5. `status`/Nasdaq 等明显错误 route；
6. alias quarantine。

这些修改应先独立回归，因为它们不需要扩大 LLM 判断。

## Phase C：KB runtime view 与 Field resolver

1. Metric core/fallback 两级候选；
2. metric duplicate redirects；
3. raw hard dimensions；
4. exact trust tiers；
5. participant type/routing；
6. Place/Named Object context；
7. validator扩展。

这一阶段允许大范围 KB overlay 修改，但不直接删除 source catalogs。

## Phase D：Mention 语义协议

1. Grounder disposition；
2. residual review；
3. atomicity review flags；
4. Quantity role/basis；
5. assertion compatibility；
6. umbrella suppression；
7. 窄 Prompt 修订。

不建议在此阶段同时提高复杂节点 batch。

## Phase E：Identity active boundaries

1. tri-state dimensions；
2. high-trust PRIMARY metric disjoint cannot-link；
3. assertion/issuer/session/period 逐类激活；
4. cluster circuit breaker；
5. N9 paired regression。

只有 Field trust tier通过精度门槛后，相关 dimension 才能成为 active hard boundary。

## 11. 验收与回滚标准

## 11.1 Mention

基线：

- P/R/F1 = 85.33% / 71.64% / 77.89%

阶段门槛：

- Recall 至少提升 5 个百分点；
- Precision 下降不超过 1.5 个百分点；
- known compound bad cases 修复率 ≥ 80%；
-新增 fragmentation bad cases ≤ 2 个；
- candidate disposition coverage = 100%；
- residual restored item precision ≥ 85%。

## 11.2 Evidence

- final persisted exact/source-equivalent ≥ 99.5%；
- invalid model quote必须仍被单独统计，不能被 repair 指标掩盖；
- ambiguous attribute evidence显著下降；
- semantic repair LLM调用仍为 0；
- Evidence异常不得扩大成 Mention/文档失败。

## 11.3 Field

建议阶段目标，不把它们伪装成已达到：

| Field | 当前 | 第一阶段目标 |
| --- | ---: | ---: |
| predicate | 83.56% | ≥ 90% |
| participant | 92.72% | ≥ 96% |
| metric | 81.07% | ≥ 90% |
| fiscal period | 50.00% | ≥ 80% |
| total | 83.29% | ≥ 91% |

同时要求：

- candidate recall@8 ≥ 98%；
- T1 deterministic link precision ≥ 99%；
-错误 alias 自动晋升 = 0；
- NEW/UNRESOLVED 不得靠大幅上升换取 accuracy；
- alias-group 复制错误与 primary 错误分别统计。

## 11.4 Identity

基线：

- pair precision 29.80%；
- judgeable recall 92.50%；
- 33-member cluster贡献 459 个 FP。

第一阶段 release gate：

- pair precision ≥ 60%；
- judgeable recall ≥ 90.5%；
- 33-member bad cluster不再跨不同高可信 PRIMARY metric；
- hard conflict violation减少 ≥ 80%；
- false split按 metric/period/assertion/session 分维度报告；
- 不能通过把所有 unresolved变成唯一 identity 达标。

长期目标可再提高到 pair precision ≥ 75%，但不建议本轮一次设成硬门槛。

## 11.5 KB

- validator structural errors = 0；
-所有 active cross-catalog collision均有分类；
- core metric正例 candidate recall@3 ≥ 98%；
- source-tag candidate在普通 news query首层出现率接近 0；
- participant type accuracy ≥ 97%；
- runtime active unit collision = 0；
- actual/derived fiscal period可区分；
- catalog/overlay hash均进入审计。

## 11.6 A/B 方法

必须使用 fixed input：

1. Frozen model input per node；
2. baseline 与 candidate 使用相同 upstream payload；
3. 每项修改单独开关；
4. 逐 item 比较，不以 aggregate token/latency替代语义验收；
5.真实模型随机性单独报告；
6. 30 篇之外增加 focused negative set：
   - same metric bounds 不得拆；
   -不同 metric 必须拆；
   - same transaction terms 不得碎；
   - independent investment/supply/partnership必须拆；
   - known candidate不得漏；
   - generic background不得回流；
   - high-confidence metric conflict不得合并；
   - missing field不得自动判 different。

## 12. 修改项风险总表

| 修改 | 主要收益 | 最大反向风险 | 核心护栏 |
| --- | --- | --- | --- |
| Candidate disposition | 找到 Grounder 静默漏项 | 误把全 candidate 当 Mention | disposition 不等于保留 |
| Residual review | 提高召回 | 背景/观点回流 | 仅高价值候选 + Judge |
| Atomicity flag | 修复复合 Mention | 过度碎片化 | flag 不自动 split + recomposition check |
| Quantity role | 保存 comparison | comparison 变核心 identity | 仅 PRIMARY 进入 hard dimension |
| Assertion guard | 修 guidance/forecast | issuance 被错改 | 先判 proposition 模式 |
| Evidence fallback | 提高 exact locator | 掩盖模型改写 | 只许空白/尾标点 |
| Raw hard dimensions | 减少错误候选 | prune 正确 candidate | UNKNOWN 不 prune + recall@8 |
| Metric tier | 降 XBRL 噪声 | 长尾 recall 下降 | filing/context fallback |
| Exact trust tier | 减少错误 auto-link | LLM/UNRESOLVED 增加 | full name+ticker/core exact 保留 T1 |
| Alias quarantine | 防污染扩散 | 学习变慢 | observed surface 低权保留 |
| Participant type | 改善 namespace | type hint 错锁 | conflict -> participant.unknown |
| Mention-local issuer | 修 fiscal period | 省略主语 recall 下降 | source ticker/邻近 coref fallback |
| Metric in Identity | 降过合并 | 同事实碎片化 | 先做 disjoint cannot-link |
| Active hard rules | 阻断 supercluster | Field 错误被放大 | 仅 high-trust、逐维度放行 |
| KB overlay | 大范围清理候选 | schema/维护复杂度 | source catalog不删、overlay可回滚 |

## 13. 建议的首个实施包

若下一轮开始写代码，建议首包只包含：

1. evaluator atomicity/eventhood口径固化；
2. Evidence anchor + terminal punctuation exact修复；
3. v2 unit active view与 TRILLION；
4. alias quarantine/provenance；
5. mention-local fiscal issuer；
6. KB validator的 cross-catalog collision与 active tier能力；
7. Metric core/fallback retrieval shadow。

原因不是这些最“保守”，而是它们能先建立可信 Field/KB 输入和评估基线。随后实施 Mention disposition/Quantity schema 与 Identity hard boundaries时，才能知道改善来自哪里，也能在出现碎片化或 precision 回退时只撤销具体规则。

第二个实施包再做：

1. Grounder disposition与 residual review；
2. atomicity flags；
3. Quantity role；
4. participant type；
5. Metric/predicate overlay正式启用。

第三个实施包做：

1. tri-state OPEN Identity；
2. high-trust metric/issuer/assertion/session cannot-link；
3. cluster circuit breaker；
4.完整 30 篇 paired acceptance。

## 14. 最终结论

这轮不能把问题简化成“Prompt 不够强”或“KB 不够大”：

- Mention 的关键缺陷是候选处置不可审计、边界冲突缺少确定性复核；
- Evidence 的问题主要是 exact locator 编排，没有必要增加语义 LLM；
- Field 的问题是 raw/candidate 维度不对称、exact link过度可信、alias可自污染；
- Identity 的问题是忽略正确 metric，又把 UNKNOWN 当成足够相同；
- KB 的问题不是数据量不足，而是 source coverage 与 runtime resolution 没有分层。

最平衡但不保守的方向是：

1. 对 KB 运行时视图做较大重构，保留 source catalogs、重做 active candidate policy；
2. 对 Mention 使用“复核触发器”而不是自动拆分器；
3. 对召回使用 candidate disposition + 高价值 residual，而不是全部恢复；
4. 对 Identity 先启用高可信 disjoint cannot-link，而不是把所有字段拼成严格相等键；
5. 每项修复都以 precision、recall、fragmentation、supercluster 和 unresolved rate 的联合门槛验收。
