# CDECR Mention / Evidence / Field / Identity / KB 低复杂度修订方案

日期：2026-07-28  
依据：

- `CDECR_MENTION_EVIDENCE_FIELD_IDENTITY_DEEP_DIAGNOSTIC_20260728.md`
- `CDECR_MENTION_EVIDENCE_FIELD_IDENTITY_KB_OPTIMIZATION_PLAN_20260728.md`

性质：对上一版完整方案进行系统复杂度复核后的正式实施建议  
目标：修复关键质量问题，但不显著增加模型协议、Prompt、工作流环节和长期维护分支

## 1. 结论

上一版方案的问题诊断和风险识别仍然成立，但如果全部实施，会给系统注入偏高复杂度，主要集中在：

1. 增加 residual review LLM 环节；
2. 同时给 Quantity、Participant、Identity 增加多组新字段；
3. 为 KB 建立通用 Source Catalog / Overlay / Runtime View 三层框架；
4. 为 OPEN Identity 引入显式 tri-state 数据模型；
5. 为大 cluster 增加新的运行时 circuit-breaker 分支；
6. 多个 validator、review flag、fallback 和二次修复路径叠加。

这些设计各自有合理性，但同时落地会产生：

- 协议之间的新依赖；
- 更多模型兼容问题；
- 更多“只有某种组合才执行”的特殊路径；
- 更复杂的审计解释；
- 更高的回归测试矩阵；
- 后续开发者难以判断错误究竟来自模型、Validator、KB policy 还是 fallback。

本轮修订后的原则是：

> 只增加能够消除已证实系统性错误、且无法通过现有机制完成的最小能力。其余能力优先复用现有 Grounder、Judge repair、Field resolver、Registry audit 和 N9 hard-cannot-link，不创建新的正常流程节点。

## 2. 复杂度预算

修订后的实施方案必须满足以下预算：

| 维度 | 预算 |
| --- | --- |
| 新增正常流程 LLM 节点 | 0 |
| 新增正常流程 LLM 调用 | 0；只有现有 repair 路径在校验失败时可触发 |
| 新增模型输出 Schema 字段 | 最多 2 处：Grounder rejection ledger、Quantity role |
| 新增持久化表 | 0 |
| 修改 KB 12 catalog 通用 schema | 0 |
| 新增通用状态机/策略引擎 | 0 |
| Prompt 扩充 | 每个现有 Prompt 只增加必要的短规则，不增加长解释章节 |
| 新增特殊运行分支 | 只允许 evidence alignment、semantic validation、candidate filtering、hard conflict 四类通用机制 |
| 正常通过路径 payload | 只允许小幅增加，不能加入长 reasoning |

如果某项修改突破以上预算，必须证明它解决的是高频、系统性且无法用现有机制修复的问题，否则延期。

## 3. 复杂度评估方法

每个修改项从四方面打分：

- **实现复杂度**：代码、Schema、迁移和测试量；
- **运行复杂度**：新增节点、重试、分支和状态；
- **认知复杂度**：后续开发者理解和排障成本；
- **收益确定性**：是否直接对应冻结 30 篇中的系统性 bad case。

决策：

- `保留`：收益高，复杂度低；
- `简化`：问题真实，但上一版方案过重；
- `仅审计`：先观测，不改变业务输出；
- `延期`：收益尚不足以覆盖复杂度；
- `删除`：有更简单且效果接近的方案。

## 4. 修改项逐项复杂度复核

## 4.1 Mention

### M1 Grounder candidate disposition ledger

上一版：每个 candidate 输出 disposition、draft_ids 和多种状态。

复杂度问题：

- 重复表达 `source_candidate_ids` 已有的 used relation；
- 每个 candidate 都增加对象，输出体积和 Schema 校验负担偏大；
- `DUPLICATE_OF/NOT_INDEPENDENT` 与 draft mapping 容易形成第二套 lineage。

决策：**简化保留**。

改为只输出被丢弃 candidate：

```json
{
  "rejected_candidates": [
    {"id": "c3", "code": "BACKGROUND"},
    {"id": "c7", "code": "NOT_INDEPENDENT"}
  ]
}
```

只保留四个 code：

- `BACKGROUND`
- `NOT_INDEPENDENT`
- `UNSUPPORTED`
- `OUT_OF_SCOPE`

编排层通过：

```text
used candidate IDs = drafts[*].source_candidate_ids
rejected candidate IDs = rejected_candidates[*].id
```

验证两者：

- 无交集；
- 并集等于输入 candidate IDs；
- 每个 ID 恰好处置一次。

收益：

- 能直接定位 106 个静默遗漏；
- 不复制 draft lineage；
- 每个被使用 candidate 不新增输出 token。

复杂度：

- 一个 Grounder 输出字段；
- 一个小 DTO；
- 一个 coverage validator；
- 无新增节点、无新增 reasoning。

最终判断：必要且成本合理。

### M2 受控 residual review

上一版：对高价值 rejected candidate 增加第二次 LLM review。

复杂度问题：

- 新增流程环节；
- 新增正常运行 LLM 调用；
- 需要 duplicate 检查、恢复逻辑、Judge 衔接和独立审计；
- 容易演变为 Grounder 后的第二个 Grounder。

收益不确定性：

- 30 篇确有 Grounder 漏项；
- 但增加 rejection ledger 和更明确 eventhood 规则后，残余漏项规模未知；
- 当前没有证据证明必须新增节点才能解决。

决策：**删除默认实施**。

替代方案：

1. Grounder 必须明确拒绝 candidate；
2. Prompt 用两三条短规则澄清 attributed forecast、measurable state；
3. evaluator 对错误 rejection 单独统计；
4. 只有完成一轮新验收后，若仍有大量高价值错误 rejection，再单独评估 residual 节点。

最终判断：当前不新增 residual LLM。

### M3 Dreamer coverage checklist

上一版：增加一条内部覆盖检查，不输出 checklist。

决策：**保留**。

复杂度：

- Prompt 增加一条短句；
- 无 Schema、节点、持久化变化。

护栏：

- 不提高 24 candidate cap；
- 不增加输出字段；
- 只有 attributed forecast、明确计划、量化 state 和供需/产能方向进入检查。

### M4 Atomicity consistency guard

上一版：增加 review_flags 并传给 Judge。

复杂度问题：

- 新增 Judge 输入字段；
- 形成 model-facing 第二套 atomicity协议；
- flag 可能给模型造成 anchoring；
- 后续需维护 flag生成与 Prompt解释的一致性。

决策：**简化保留，但不增加 review_flags**。

改为扩展现有 Judge 输出 validator：

1. Judge 已经审查所有 Grounder draft；
2. Judge 返回后，确定性检查以下高置信冲突：
   - ACTUAL 与 EXPECTED/PLANNED 混合；
   - 两个相反方向的核心 action；
   - 两个不同主体执行相反动作；
   - 两个不同核心 metric 被判为一个 Mention；
3. 发现冲突时，沿用现有 Judge repair 机制，把具体 validation error 返回；
4. 不新建节点、不在正常 Judge 请求中加入 flags。

不做：

- 不根据动词数自动拆分；
- 不根据 quantity 数自动拆分；
- 不增加一套通用 action parser 状态机。

复杂度：

- 一个 post-Judge semantic validator；
- 复用已有 repair；
- 正常通过路径无新增 payload。

### M5 Umbrella suppression

上一版：满足四个条件时自动删除 generic umbrella。

复杂度问题：

- 需要 split lineage、Evidence 覆盖和 artifact identity 联合判断；
- 规则只对应当前一个明确 bad case；
- 自动删除存在不可逆的信息损失风险。

决策：**先仅审计，不自动删除**。

实施：

- 若同一 split lineage 中同时存在 generic `report earnings` 与具体 metric Mention，记录 `POSSIBLE_UMBRELLA_DUPLICATE`；
- 由验收统计衡量频率；
- 当前不改变输出。

如果后续多轮均证明高频，再把它加入 Judge 的一条短规则，而不是建立独立 suppression 模块。

### M6 QuantityDraft role/basis

上一版：给每个 Quantity 增加 `role` 和 `basis`。

复杂度问题：

- 两个字段进入 Grounder、Judge、持久化和 evaluator；
- basis 又与现有 `comparison_basis` concept、open attributes 发生重叠；
- 需要处理 role 与 basis 的组合合法性。

决策：**只保留一个 `role` 字段，删除 `basis` 字段**。

```json
{
  "metric_id": "revenue",
  "value": 41.5,
  "unit": "billion USD",
  "raw_text": "$41.5 billion",
  "role": "PRIMARY"
}
```

枚举：

- `PRIMARY`
- `COMPARISON`
- `BOUND`
- `SUPPORTING`

为什么这个字段值得保留：

- 当前 14 个 quantity 问题中一半是字段位置问题；
- Identity 必须知道哪个 metric 是核心身份；
- 没有 role 时，只能依赖数组顺序或复杂启发式，长期维护成本反而更高；
- 一个短枚举比增加 quantity graph 或恢复 schema projection 简单。

约束：

- 默认恰好一个 PRIMARY；
- 多 PRIMARY 触发已有 Judge repair；
- 同一 metric 的上下界使用 BOUND，不拆 Mention；
- COMPARISON/SUPPORTING 不进入 hard identity。

### M7 Assertion compatibility

上一版：先判 proposition 模式，再校验 state。

决策：**简化保留**。

只实现两个高置信校验：

1. `GUIDANCE_EXPECTATION + guide_metric + ACTUAL` 非法；
2. 明确 forecast/likely/expected proposition 不应是 ACTUAL。

不建立通用 assertion 状态机。

处理：

- 复用 Judge repair；
- Prompt 只补 EXPECTED 与 HYPOTHETICAL 的两句定义；
- guidance issuance 与 underlying guidance 的差异用一个正反例说明。

### M8 Forecast horizon

上一版：考虑 first-class horizon 或受控 attribute。

决策：**不增加新 Schema 字段**。

实施：

- 继续使用 `open_attributes.forecast_horizon`；
- Field resolver 不把它送入 fiscal period；
- Identity 第一阶段不使用 horizon；
- 只保留原文和审计。

理由：

- 当前 horizon 主要是 representation 问题；
- 加入 Identity 很容易制造长期 forecast 碎片；
- 现有 attribute 足以避免信息丢失。

## 4.2 Evidence

### E1 Main Evidence anchor 消歧

决策：**保留**。

复杂度：

- 只修改现有 locator 的参数；
- 无 Schema、Prompt、节点变化；
- 直接修复 7/7 attribute ambiguity cases。

### E2 Exact/source-equivalent locator

决策：**保留但缩小范围**。

只允许：

- exact text；
- 同 segment 唯一 exact occurrence；
- 尾部标点差异；
- whitespace normalization。

不允许：

- coreference rewrite；
- ellipsis quote；
- synonym replacement；
- semantic LLM repair。

复杂度：

- 一个通用 locator 函数；
- 不增加特殊 per-case 规则。

### E3 Evidence 状态分型

上一版增加五种状态。

复杂度评估：

- 如果修改 persisted enum，会影响存储、读取、报告和兼容；
- 当前 `error_code` 已能区分 ambiguous/not-found 的一部分原因。

决策：**不修改持久化 status enum**。

实施：

- 保留当前 status；
- 在 evaluator/audit 聚合时按 `error_code` 分组；
- 只补缺失的 `repair_kind/first_divergence_stage` 审计字段。

收益接近，协议复杂度显著更低。

## 4.3 Field Resolution

### F1 通用 raw hard dimensions

上一版：为 Metric、Predicate、Participant 建同构 hard-dimension 对象。

复杂度问题：

- 引入新的内部 mini-ontology；
- parser、candidate、Prompt 和审计都要理解相同结构；
- hard dimensions 规则会快速膨胀。

决策：**删除通用框架，改为少量 candidate blockers**。

第一阶段只实现已证实、高精度的 blocker：

#### Metric

- `growth` 不链接 value；
- `margin` 不链接 value/yield；
- `yield` 不链接 value/margin；
- 明确 `stock/share price` 不链接 index level；
- 明确 `index level` 不链接 company share price；
- `organic/constant-currency` 不得从普通 revenue growth 推断；
- count object 不同不能只因 `count` 相同链接。

#### Predicate

- trade/move 不链接 stock split；
- close 不链接 cut；
- index movement 不链接 pricing action；
- announce 不链接 sign/complete。

这些规则只删除明确冲突 candidate，不自动 LINK。

不建立：

- 通用 action/object/direction parser；
- 通用 base_measure 推导框架；
- 低置信 hard prune。

### F2 Exact/unique KB trust tier

上一版：T1/T2/T3 三层并可能进入新 metadata。

决策：**简化为一个函数，不增加新的数据模型**。

`is_safe_deterministic_match(...)` 只对以下返回 true：

- 显式 canonical external ID；
- source ticker 与 company ticker 一致；
-完整 canonical name exact，且没有跨目录 collision；
-受控 core metric/predicate exact。

其余 alias exact 或“当前只有一个结果”统一进入已有 candidate resolver。

审计只记录短 reason：

- `EXPLICIT_ID`
- `TICKER_CONTEXT`
- `UNAMBIGUOUS_NAME`
- `CORE_ONTOLOGY`
- `CANDIDATE_REQUIRED`

不持久化 T1/T2/T3 类型。

### F3 Metric 两级候选池

问题和收益明确，但上一版通用 tier/overlay 过重。

决策：**保留两级检索，简化 KB 表达**。

实现：

1. 直接按现有 ID 前缀区分：
   - core：非 `US_GAAP_`、非 `XBRL_`；
   - fallback：`US_GAAP_`、`XBRL_`；
2. 普通 news 先只查 core；
3. filing、显式 taxonomy tag 或 core 无候选时再查 fallback；
4. 增加一个小型 `metric_redirects.json` 处理已确认重复：
   - adjusted EPS / non-GAAP EPS；
   - adjusted net income / non-GAAP net income；
   - core 与 US-GAAP 的明显重复表面；
5. 不给所有 9,257 条增加 tier/status/provenance 字段。

复杂度：

- 一个 prefix policy；
- 一个小 redirect map；
- 一个 fallback 条件；
- 不改原 catalog schema。

### F4 Predicate ontology

上一版：建立完整 action vocabulary 并重新对齐 object/direction。

复杂度问题：

- 可能演化为新的 predicate parser；
- 与 Grounder prompt、concept KB、Identity均强耦合。

决策：**只补当前已证实缺失的 core predicates 和 blocker**。

实施：

- 保留现有 factorization；
- 补 `trade/move`、`close` 等实际缺口；
- 对无正确 candidate 的 raw predicate允许 NEW/UNRESOLVED；
- 不尝试一次性重构整个 predicate ontology。

### F5 Alias quarantine/provenance

上一版：SOURCE/CONFIRMED/OBSERVED 三类 alias 和晋升机制。

复杂度问题：

- 需要 alias 新状态、first_seen、promotion 和撤销；
- 新增长期维护流程。

决策：**采用更简单方案：LLM LINK 不再回写 deterministic aliases**。

实施：

- KB source aliases 保持不变；
- external trusted exact link可使用原 KB aliases；
- LLM 选择 candidate 后，只创建 Field link，不调用 `_add_aliases(raw_value)`；
- raw value、decision 和 canonical ID 已在现有 `FIELD_COREFERENCE` audit 中留存；
- 未来如确需学习 alias，通过离线 KB 构建显式加入。

收益：

- 直接阻断错误自强化；
- 不增加新表、新状态和 promotion job。

代价：

- 相同新 alias 后续仍可能调用 LLM。

这个代价小于在线错误 alias 污染的维护成本。

### F6 Participant type_hint

上一版：给 `ParticipantDraft` 增加 type_hint。

复杂度问题：

- 每个 Participant 增加模型字段；
- Grounder 要承担实体类型分类；
- type_hint 与 Field router冲突时需要新仲裁逻辑；
- Judge 也必须能修 type。

决策：**删除 DTO 修改，修现有 router**。

实施：

1. 扩充 generic collective：
   - institutions
   - foreign investors
   - individual investors
   - management
   - analysts
   - customers/suppliers 等；
2. 强 regex 不再在有跨 catalog collision 时直接锁 namespace；
3. analyst/source_claim 语境中的 UBS 等进入现有 `participant.unknown` 多类型候选；
4. Stoxx/index/composite优先 instrument；
5. business unit 使用 owner-scoped unresolved，不链接随机 company/object；
6. `Micron management` 先解析 Micron，再保留 management collective。

复用已有：

- participant.unknown；
- multi-catalog exact/fuzzy recall；
- Field LLM；
- existing reason/audit。

### F7 Mention-local fiscal issuer

决策：**保留**。

这是修改一个现有 issuer selection 函数，不增加 schema、prompt或节点。

简化优先级：

1. 当前 Mention SUBJECT/ACTOR company；
2. source ticker；
3. 无可靠 issuer则 unresolved。

不增加：

- 邻近 Mention复杂 coreference；
- issuer confidence graph；
- 新时间类型 schema。

rolling window/horizon只是不送 fiscal resolver。

### F8 Location / Attribute

上一版包含通用 key+value route框架。

决策：**仅修已证实问题，不做通用重构**。

第一阶段：

- Nasdaq/index/exchange 不进入 PLACE；
- 从 lifecycle alias 中移除过宽 `status`；
- `record levels` 保留 literal attribute；
- `market` 暂不成为 Identity hard field。

Persons/Places/Named Objects 的大规模 context schema治理延期，除非后续验收显示它们成为主要错误源。

## 4.4 Identity

### I1 显式 tri-state Identity schema

上一版：KNOWN/ABSENT/UNRESOLVED 三态进入 OPEN Identity。

复杂度问题：

- 修改核心 Identity contract；
-所有比较、序列化、hash、N9 输入和测试都要理解三态；
-可能和 Field unresolved canonical重复表达；
-维护成本高。

决策：**删除显式 tri-state schema**。

用更简单的决策语义替代：

- 双方都有高可信 canonical 且冲突 -> cannot-link；
- 任一方缺失或 unresolved -> 不做 hard 决策；
- 缺失不等于相同，也不等于不同。

这一语义可以直接在现有 hard-cannot-link函数中实现，不改变 Identity DTO。

### I2 Metric disjoint cannot-link

决策：**保留**。

输入：

- 只读取 `Quantity.role=PRIMARY`；
- 只读取高可信 core metric Field link；
-双方都存在且 canonical metric不同，才 cannot-link。

不做：

- 不把整个 metric set拼进 OPEN Identity equality；
- 不因一方 missing而分开；
- 不让 COMPARISON/BOUND/SUPPORTING参与 hard conflict。

这能修复 33-member cross-metric supercluster，同时将碎片化风险控制在最低。

### I3 Field trust tier

上一版：完整 trust tier持久化到 Identity provenance。

决策：**简化为内部布尔判断 + audit reason**。

`is_high_trust_field_link(...)`：

- core ontology safe exact -> true；
- contextual company ticker/full name -> true；
- Field LLM link -> 第一阶段 false；
- fuzzy/unresolved/provisional -> false。

审计记录为什么某个 conflict 被启用或跳过，但不修改 public Identity schema。

后续若实测 Field LLM 某 namespace precision ≥ 98%，再把该 namespace加入 high trust。

### I4 Hard cannot-link 分维度激活

上一版列出六类。

决策：**第一轮只激活两类**：

1. 不同高可信 PRIMARY metric；
2. 明确 ACTUAL vs EXPECTED/PLANNED。

issuer、session、period、predicate继续 shadow，原因是当前 Field/Time准确率不足，直接激活更容易制造 false split。

### I5 Cluster circuit breaker

上一版：大 cluster触发检查和分区。

复杂度问题：

- 新增运行时分支；
- cluster size不是业务语义；
- hard conflicts启用后应自然缩小 cluster；
- circuit breaker容易变成另一套聚类器。

决策：**删除行为逻辑，只保留监控指标**。

记录：

-最大 cluster size；
- top cluster FP贡献；
- hard conflict violation concentration。

不因 cluster 大小改变业务输出。

## 4.5 KB

### K1 通用 Source/Overlay/Runtime 三层框架

上一版：建立带 status/tier/provenance/redirect/hard_dimensions 的通用 overlay。

复杂度问题：

- 形成第二套 KB schema；
-构建器、validator、loader和候选器全部增加一层；
-每种 catalog都要维护 overlay语义；
-当前错误主要集中在 metric、participant routing、units，没必要让 12 catalog一起承担重构。

决策：**删除通用 overlay框架**。

### K2 最小 runtime policy

替代为三个很小、明确的机制：

1. Metric core/fallback按现有 ID prefix区分；
2. `metric_redirects.json` 只存已确认重复 canonical；
3. cross-catalog collision由 validator生成报告，router在 exact link时查询 collision set。

不增加所有对象的 tier/status/provenance字段。

### K3 Participant organization facet graph

上一版：为 company/institution/instrument 建 parent organization/crosswalk。

复杂度很高，且当前主要错误可以通过 collision-aware routing解决。

决策：**延期**。

UBS 等保留多个 facet，由 context选择，不新建 `ORG_UBS` 等父实体。

### K4 Person / Place / Named Object 全面扩 schema

上一版建议给 Place增加 country/admin、Person增加 org/title/date、Named Object强 owner/kind。

其中部分长期合理，但会改变 catalog schema和候选 payload。

决策：

- 当前只在检索时使用已有 `org_id/owner_id/kind`；
- Place 全面 metadata扩展延期；
- Person 同名不 deterministic link；
- Named Object强制使用现有 kind/owner filter；
-不修改 12 catalog schema。

### K5 Fiscal provenance

上一版建议 ACTUAL/DERIVED 字段。

决策：**不改 catalog schema**。

通过 ID/date生成路径和一个 runtime helper判断 derived candidate；derived 只作 soft candidate，不作 hard identity。

如果无法可靠判断，则所有 future fiscal periods第一阶段都按 low trust处理。

### K6 Units

决策：**保留并简化**。

- 将 v1 normalizer补齐 TRILLION，或让现有 normalizer读取 v2 中 4 个 SCALE；
- 不让 quantity normalization加载整个 1,664 unit catalog；
- active scale/currency/ratio使用小型确定性表；
- XBRL units继续留在 v2，但不进入普通新闻数量解析。

### K7 Artifacts

上一版提出 dynamic artifact identity。

当前四部分验收没有显示 artifact是主要错误源。

决策：**延期，不在本轮修改**。

### K8 Attributes

决策：**只做 alias cleanup**：

- 移除 `status -> lifecycle_stage`；
-修 Nasdaq/exchange routing；
-其他 attribute体系不重构。

### K9 Validator

上一版新增九类 validator。

决策：**只新增三项**：

1. cross-catalog normalized collision report；
2. metric redirect有效性与无环；
3. core/fallback候选 regression queries。

不要求 12,816 个 collision全部人工分类。

## 5. 修订后的系统变化

## 5.1 模型 Schema

只增加：

1. Grounder `rejected_candidates[{id, code}]`；
2. Quantity `role`。

明确不增加：

- residual review schema；
- Grounder review flags；
- Participant type_hint；
- Quantity basis；
- first-class forecast horizon；
- tri-state Identity；
- KB overlay fields。

## 5.2 Prompt

只做以下窄修改：

### Dreamer

- 一条 attributed forecast/measurable state coverage提醒。

### Grounder

- 说明 rejection ledger；
- comparison/bound应进入 quantities；
-每个 Mention恰好一个 PRIMARY quantity；
- EXPECTED/HYPOTHETICAL 两句边界。

### Judge

- 不增加 review flag说明；
- 增加同 metric bounds/comparison不得拆；
-增加 generic umbrella审计提醒，但第一阶段不自动删除；
-保留原 reason协议。

总原则：

- 不重写现有 Prompt；
-不新增长示例集；
- focused examples进入测试，不全部塞进生产 Prompt。

## 5.3 工作流

正常路径保持：

```text
Dreamer -> Grounder -> Judge -> Materialize -> Field -> Identity/N9
```

不增加：

- Residual review；
- Snapshot Decide；
-新 Evidence LLM；
-新 KB LLM；
-cluster repair节点。

只在现有边界增加：

- Grounder coverage validation；
- Judge semantic validation，失败复用现有 repair；
- Field candidate filtering；
- N9 hard conflict。

## 5.4 持久化与审计

不新增表。

复用：

-现有 model-call audit；
- `FIELD_COREFERENCE`；
- Evidence location audit；
- hard cannot-link audit；
- Mention derivation。

新增内容只作为现有 audit payload中的短字段：

- rejected candidate code；
- semantic validation error code；
- deterministic match reason；
- hard conflict reason。

## 6. 修订后的实施项

## Phase 0：评估口径与审计

1. 统一 multi-metric、bounds、comparison、umbrella Gold；
2. 增加 Grounder rejection ledger和 coverage validator；
3. Evidence按现有 error_code重新聚合；
4.增加 KB cross-catalog collision只读报告；
5. 固化 bad-case regression。

复杂度：低。  
业务行为变化：除 Grounder显式拒绝外基本无。

## Phase 1：确定性修复

1. Attribute Evidence使用 Main anchor；
2. Evidence尾标点/空白 source-equivalent对齐；
3. TRILLION和 active scale解析；
4. mention-local fiscal issuer；
5.停止 LLM link回写 deterministic alias；
6.修 `status` 和 Nasdaq route。

复杂度：低。  
新增模型字段：0。  
新增节点：0。

## Phase 2：最小 Mention 协议

1. Quantity增加唯一 `role` 字段；
2. Grounder/Judge做窄 Prompt修改；
3. Judge后增加少量高置信 semantic validator；
4. umbrella只审计；
5.不增加 residual review。

复杂度：中低。  
新增模型字段：1。  
新增节点：0。

## Phase 3：Field / KB / Identity

1. Metric core/fallback；
2. 小型 metric redirects；
3. 少量 metric/predicate candidate blocker；
4. collision-aware deterministic exact；
5.复用 participant.unknown修 routing；
6.激活高可信 PRIMARY metric和 assertion hard conflict；
7.其余 cannot-link继续 shadow。

复杂度：中。  
KB catalog schema变化：0。  
Identity public schema变化：0。  
新增节点：0。

## 7. 明确延期项

以下不是永久否定，而是当前收益不足以覆盖复杂度：

- residual review LLM；
- Participant type_hint；
- Quantity basis字段；
- first-class forecast horizon；
-显式 tri-state Identity；
-通用 KB overlay；
- organization facet graph；
- Place/Person全面 schema扩展；
- dynamic artifact identity；
- cluster circuit breaker；
- issuer/session/period/predicate全部 active hard conflict。

只有出现以下证据才重新开启：

1. 完成简化方案后仍有稳定、高频错误；
2. 错误无法通过现有节点/validator修复；
3. fixed-input A/B证明收益；
4.能给出迁移、回滚和维护责任边界。

## 8. 复杂度与收益总表

| 修改项 | 原方案复杂度 | 修订决策 | 修订后复杂度 | 预期收益 |
| --- | --- | --- | --- | --- |
| Candidate disposition | 中 | 只列 rejected | 低 | 高 |
| Residual review | 高 | 删除 | 0 | 待验证 |
| Dreamer checklist | 低 | 保留 | 低 | 中 |
| Atomicity flags | 中 | 改 post-Judge validator | 低 | 高 |
| Umbrella suppression | 中 | 只审计 | 低 | 低到中 |
| Quantity role+basis | 中高 | 只留 role | 中低 | 高 |
| Assertion模式 | 中 | 只留两条高置信校验 | 低 | 中高 |
| Forecast horizon | 中 | 继续 attribute | 低 | 中 |
| Evidence anchor | 低 | 保留 | 低 | 高 |
| Evidence状态扩展 | 中 | evaluator按 error_code统计 | 低 | 中 |
| 通用 hard dimensions | 高 | 少量 blockers | 低 | 高 |
| Exact trust tier模型 | 中 | 一个 safe-match函数 | 低 | 高 |
| Metric tier/overlay | 高 | ID prefix + redirect文件 | 低 | 高 |
| Predicate ontology重构 | 高 | focused补缺 | 低 | 中 |
| Alias quarantine状态机 | 高 | 禁止 LLM alias回写 | 低 | 高 |
| Participant type_hint | 中高 | 修现有 router | 低 | 高 |
| Mention-local issuer | 低 | 保留 | 低 | 高 |
| Location/Attribute重构 | 中 | focused修复 | 低 | 中 |
| tri-state Identity | 高 | 删除 | 0 | 可由简单规则替代 |
| Metric cannot-link | 中 | 保留高可信单一规则 | 低 | 很高 |
| 六类 hard conflict | 中高 | 首轮只启用两类 | 低 | 高 |
| Cluster circuit breaker | 中 | 删除行为，仅监控 | 低 | 低 |
| 通用 KB overlay | 很高 | 删除 | 0 | 当前不必要 |
| Org facet graph | 很高 | 延期 | 0 | 当前可替代 |
| Validator九项 | 中高 | 缩为三项 | 低 | 中高 |

## 9. 维护性约束

实施时还应遵守：

1. 所有新 validator error code集中定义，不散落字符串；
2. 所有 candidate blocker集中在一个 policy模块；
3. Metric redirect只有一个数据源；
4. normalizer只使用一套 active scale定义；
5.不能为单个 mention ID写特殊规则；
6.不能把 frozen 30篇中的文章文本写进生产逻辑；
7.新审计字段必须是编排生成，不要求 LLM输出 reasoning；
8.每项行为修改有独立 feature flag或可独立回滚提交；
9.测试按“防合并”和“防碎片”成对出现；
10.如果一个规则需要三个以上下游模块理解，优先重新设计为局部 validator。

## 10. 验收方式

沿用上一版的 precision/recall联合验收，但增加复杂度验收：

### 业务质量

- Mention recall提升至少 5 个百分点；
- Mention precision下降不超过 1.5 个百分点；
- known compound bad cases修复率 ≥ 80%；
-新增 fragmentation bad cases ≤ 2；
- Field total ≥ 91%；
- Identity pair precision第一阶段 ≥ 60%；
- judgeable recall ≥ 90.5%。

### 复杂度

-正常路径 LLM节点数量不变；
-正常路径 LLM调用次数不增加；
-模型输出只新增 rejection ledger和 Quantity role；
- KB 12 catalog schema不变；
- Identity public schema不变；
-无新持久化表；
-无新在线 alias晋升任务；
-无按 cluster size改变输出的特殊路径；
- Prompt新增内容保持短小，可逐句对应已观察 bad case。

### 回滚

- Evidence、unit、issuer、alias、metric retrieval、Mention schema、hard conflict分别提交；
-某一项未通过只回滚该项；
-不得用“整包回滚”掩盖具体失败来源。

## 11. 最终建议

本轮真正值得承担少量复杂度的只有两项模型协议变化：

1. Grounder rejection ledger：解决 candidate 静默消失和召回不可审计；
2. Quantity role：解决主指标、comparison、bound混淆，并为防止 cross-metric误合并提供最小必要信息。

其他主要修复都可以复用现有系统：

- Judge repair承接 atomicity/assertion验证；
-现有 locator承接 Evidence修复；
-现有 Field resolver承接 collision-aware候选；
-现有 participant.unknown承接类型歧义；
-现有 hard-cannot-link承接高可信 metric/assertion冲突；
-现有 Registry audit承接 provenance。

因此，修订方案不是“为了简单而少修”，而是删掉了收益尚未证明的第二套机制。落地后系统仍保持原来的主工作流、节点数量、KB schema和 Identity schema，同时能够处理当前最主要的召回不可审计、Evidence定位、Field候选污染和 cross-metric错误合并。

