# CDECR Mention / Evidence / Field / Identity / KB 最终实施方案

日期：2026-07-28
状态：正式实施依据
适用范围：冻结 30 篇验收暴露的 Mention、Evidence、Field Resolution、Identity 与 KB 问题
不包含：N7-N13 Atomic/Package LLM 逻辑全面重构、N5.5 Snapshot LLM Decide、额外 residual LLM 节点

## 1. 实施目标

完成以下业务修复：

1. Grounder 不再静默丢弃 Dreamer candidate；
2. 提升 attributed forecast、measurable state、明确计划和供需/产能事实的 Mention 召回；
3. 修复不同动作、主体、核心指标和 Assertion State 被合为一个 Mention；
4. 保留同一指标的上下界、比较值和交易非独立条款，避免过度碎片化；
5. Evidence 保持原文 exact，单项定位异常不扩大为 Mention 或文档失败；
6. 降低 Metric、Predicate、Participant、Fiscal Period 的错误链接；
7. 阻止错误 Field link通过 runtime alias 自强化；
8. 修复 OPEN Identity 忽略核心 metric 导致的跨指标 Atomic 误合并；
9. 调整 KB 运行时候选策略，不重构 12 类 catalog 的通用 Schema。

正常工作流保持：

```text
Dreamer -> Grounder -> Judge -> Materialize -> Field Resolution -> Identity/N9
```

不新增正常流程 LLM 节点。现有 Judge repair 仅在结构或语义校验失败时触发。

## 2. 评估口径先行

### 2.1 Mention atomicity

在验收脚本和人工 Gold 中统一以下规则：

1. 不同核心 action 拆分；
2. 不同核心 subject 且行为不同，拆分；
3. ACTUAL result 与 future guidance 拆分；
4. 不同 PRIMARY metric 拆分；
5. 同一 metric 的 low/high/midpoint/tolerance 不拆；
6. 同一 metric 的 consensus、prior-period、YoY/QoQ comparison 不拆；
7. 同一协议的期限、金额和定价条款不因字段多而拆；
8. 独立的 partnership、supply commitment、investment、agreement execution 分别形成 Mention，再由后续 Package 建立关系；
9. 具体 metric Mention 已覆盖全部实质披露时，不保留没有独立 artifact/action identity 的空壳 `reported earnings` Mention；
10. filing、earnings release、agreement signing 等 artifact/action 本身有独立业务身份时可以保留。

### 2.2 Eventhood

保留：

- 有明确主体和可证伪 proposition 的 occurrence/state；
- 有明确 claimant 的 forecast、rating、price target、供需判断；
- 有 metric/value/direction 的 measurable state；
- 明确 plan、expectation、commitment、rumor、denial；
- 明确 ongoing supply/demand/capacity state。

丢弃：

- 无 claimant 的泛观点；
- 没有主体、时间、对象边界或可复核内容的背景；
- 纯标题、提问、关注度和文章 framing；
- 只重复其他 candidate 且没有新增事实的表述。

## 3. Mention 修改

## 3.1 Grounder rejection ledger

### DTO

在 `src/cdecr/single_document_contracts.py` 中增加：

```python
class RejectedCandidateDraft(StrictModel):
    id: Annotated[str, Field(pattern=r"^c[1-9][0-9]*$")]
    code: Literal[
        "BACKGROUND",
        "NOT_INDEPENDENT",
        "UNSUPPORTED",
        "OUT_OF_SCOPE",
    ]
```

`GrounderModelOutput` 增加：

```python
rejected_candidates: list[RejectedCandidateDraft]
```

不要给已进入 draft 的 candidate 重复输出 disposition。

### 验证

在 Grounder adapter 中验证：

```text
used = union(drafts[*].source_candidate_ids)
rejected = set(rejected_candidates[*].id)
used ∩ rejected = empty
used ∪ rejected = all input candidate IDs
```

每个 candidate 必须恰好处置一次。

若只缺 rejection 条目，走现有 Grounder 结构 repair；已合法 draft 不丢弃、不重跑整篇文档。

### 审计

在现有 Grounder audit 中增加：

- candidate_count；
- used_candidate_count；
- rejected_candidate_count；
- rejected code分布；
- missing/duplicate candidate IDs；
- repair是否触发。

不新增 reasoning 字段。

## 3.2 Dreamer Prompt

在现有 Prompt 增加一条短规则：

> Before returning, check for omitted attributed forecasts or ratings, explicit plans or commitments, measurable state changes, and named-subject supply, demand, capacity or volume claims.

保持：

- candidate cap = 24；
- exact Evidence要求；
- target ticker materiality优先级；
- 不输出 checklist。

## 3.3 Grounder Prompt

增加以下规则：

1. 每个 candidate 必须进入 draft 或 `rejected_candidates`；
2. 有明确 claimant、对象和可判断 proposition 的 analyst/management forecast不是 generic opinion；
3. comparison、consensus、prior-period、range和tolerance数值进入 `quantities`，不能仅放在 `open_attributes`；
4. 每个 Mention恰好一个 PRIMARY quantity；同一 metric的 comparison/bound不拆；
5. 不同 PRIMARY metric、不同核心 action、不同核心主体或实际结果与未来 guidance必须拆；
6. EXPECTED用于已经形成的 forecast/likely expectation；HYPOTHETICAL用于条件、反事实或尚未形成判断的 scenario；
7. `rose to/current at` 不得改写为 `closed at`。

不要改写原 Prompt 其他业务规则。

## 3.4 Quantity role

### DTO 与持久化模型

在 `QuantityDraft` 和 `Quantity` 增加：

```python
class QuantityRole(StrEnum):
    PRIMARY = "PRIMARY"
    COMPARISON = "COMPARISON"
    BOUND = "BOUND"
    SUPPORTING = "SUPPORTING"
```

字段：

```python
role: QuantityRole
```

不增加 quantity reasoning、relation graph 或新的 basis自由文本字段。

### 规则

- 每个 metric-bearing Mention恰好一个 PRIMARY；
- 同一 metric的上下界、容差使用 BOUND；
- consensus、prior-period、YoY/QoQ对照使用 COMPARISON；
- commitment、deposit、agreement count 等非主 metric 数值使用 SUPPORTING；
- 只有 PRIMARY metric参与 Identity hard discrimination；
- COMPARISON、BOUND、SUPPORTING 全部保留 Evidence 和 Field link，但不直接拆 Atomic。

### 兼容

旧 Mention 没有 role 时，迁移/读取适配按以下顺序：

1. 第一个 quantity 临时标为 PRIMARY；
2. 其余标为 SUPPORTING；
3. 只用于兼容旧数据，不用于重新验收；
4. 新运行必须显式输出 role。

## 3.5 Judge Prompt

保留现有 reason协议，包括正常 ACCEPT reason。

增加三条短规则：

1. 不同 PRIMARY metric必须拆；同一 metric的 bounds/comparisons不得拆；
2. split出所有具体 metric后，不保留没有独立 artifact/action identity 的 generic umbrella disclosure；
3. EXPECTED与HYPOTHETICAL按 forecast judgment和conditional scenario区分。

不增加 review_flags 到正常 Judge输入。

## 3.6 Judge 后语义校验

在现有 Judge command validation 后增加统一 semantic validator，检查：

1. 一个 Mention有多个 PRIMARY quantity；
2. `GUIDANCE_EXPECTATION + guide_metric + ACTUAL`；
3. 同一 Mention包含明显相反方向的核心 action；
4. 同一 Mention包含不同主体的相反动作；
5. Judge SPLIT 后生成完全相同的 subject + predicate + PRIMARY metric + assertion + time，疑似过度拆分；
6. generic umbrella 与同 lineage 的具体 metric Mention重复。

处理：

- 1-5 作为 validation error，复用现有 Judge repair；
- 6 首轮进入 Judge repair，不另建 suppression节点；
- repair仍不合法时，按 item-scoped resilience保留原合法 Grounder item并记录 degradation；
- 不得使单个 Judge item失败扩大为整篇失败。

所有 error code集中定义：

- `MULTIPLE_PRIMARY_METRICS`
- `GUIDANCE_ASSERTION_CONFLICT`
- `OPPOSING_CORE_ACTIONS`
- `OPPOSING_SUBJECT_ACTIONS`
- `LIKELY_FRAGMENTATION`
- `GENERIC_UMBRELLA_DUPLICATE`

## 3.7 Assertion 与时间

### Assertion

- guidance underlying proposition使用 EXPECTED；
- guidance issuance action可使用 ACTUAL，但 event_family/predicate必须表达 issuance；
- 明确 forecast/likely proposition不得为 ACTUAL；
- HYPOTHETICAL只用于 conditional/counterfactual/scenario。

### Time

- published_at只作为相对日期 anchor；
- 明确 weekday且上下文唯一时，允许确定性解析为日期并记录 parser audit；
- after-hours、pre-market、regular session保存在受控 `trading_session` open attribute；
- forecast horizon继续使用 `open_attributes.forecast_horizon`；
- rolling window、forecast horizon不得送入 fiscal-period resolver；
- 不新增 first-class forecast horizon Schema。

## 4. Evidence 修改

## 4.1 Main Evidence exact validation

在 Dreamer、Grounder、Judge 每个模型输出边界执行相同 locator规则：

1. exact offset + exact text；
2. 同 segment唯一 exact text；
3. 仅尾部标点不同；
4. 仅 whitespace不同；
5. ambiguous；
6. not found/invalid quote。

第 2-4 项允许确定性修正 locator，但持久化 text必须回写 source原文。

禁止：

- entity/coreference改写；
- synonym替换；
- ellipsis拼接；
- 语义 Evidence repair LLM。

## 4.2 Attribute Evidence anchor

修改 `_materialize_mention.locate()`：

1. 先定位 Main Evidence；
2. Attribute Evidence重复时，将 Main Evidence span传给 `candidate_anchors`；
3. 优先同 segment重叠；
4. 其次选择距离最近且唯一的 occurrence；
5. 仍歧义则保持 ambiguous，不猜测。

## 4.3 Evidence 审计

保留当前 persisted status。

通过 `error_code` 和 audit字段区分：

- exact；
- source-equivalent punctuation；
- source-equivalent whitespace；
- ambiguous；
- not found；
- invalid model quote。

新增：

- first divergence stage；
- repair kind；
- original model text hash；
- final source text hash。

不得增加 LLM payload。

## 5. Field Resolution 修改

## 5.1 安全 deterministic match

在 `canonical_field_resolution.py` 增加统一函数：

```python
is_safe_deterministic_match(
    raw_value,
    match,
    *,
    catalog,
    source,
    cross_catalog_collision,
) -> tuple[bool, reason_code]
```

只允许以下情况直接 LINK：

- explicit canonical external ID；
- source ticker与company ticker一致；
- 完整 canonical name exact，且不存在同目录或跨目录 collision；
- 受控 core metric/predicate exact；
- issuer-scoped fiscal period exact。

以下情况进入现有 candidate resolver：

- alias exact；
- 短 ticker/缩写；
- 当前 KB 只返回一个结果但不是 trusted name；
- 跨 catalog同表面；
- participant type不明确。

reason code：

- `EXPLICIT_ID`
- `TICKER_CONTEXT`
- `UNAMBIGUOUS_CANONICAL_NAME`
- `CORE_ONTOLOGY_EXACT`
- `ISSUER_SCOPED_PERIOD`
- `CANDIDATE_REQUIRED`

## 5.2 Metric 两级候选

### Core

默认 core：

```text
ID 不以 US_GAAP_ 或 XBRL_ 开头
```

普通 NEWS/SOCIAL/FLASH/ANNOUNCEMENT 先只检索 core。

### Fallback

以下情况再检索 US-GAAP/XBRL：

- SourceType=FILING；
- raw value含 taxonomy/tag格式；
- core无候选；
- local context明确指向具体会计科目。

### Resolution policy

新增小型文件：

`src/cdecr/catalogs/v2/resolution_policy.json`

只包含：

```json
{
  "metric_redirects": {},
  "blocked_exact_aliases": {},
  "participant_route_overrides": {}
}
```

用途：

- 合并已确认重复 metric canonical；
- 阻止过宽/错误 exact alias；
- 处理少量稳定、高价值的跨 catalog route，例如 UBS/Nasdaq。

不在该文件中加入通用 status、tier、provenance、hard-dimension框架。

### Metric 修正

至少处理：

- `ADJUSTED_EPS` 与 `EPS_NON_GAAP` 重复；
- `ADJUSTED_NET_INCOME` 与 `NET_INCOME_NON_GAAP` 重复；
- core 与 US-GAAP 的 `share price/store count/loss ratio` 重复；
- 新增/确认 `TRADING_VOLUME`；
- 新增/确认 `CLOSING_PRICE` 或 session price；
- 新增/确认 `DEAL_COUNT`；
- 区分 `REVENUE`、`REVENUE_GROWTH`、`ORGANIC_REVENUE_GROWTH`、`CONSTANT_CURRENCY_REVENUE_GROWTH`；
- `guaranteed_revenue` 不得链接 `ADJUSTED_REVENUE`，优先 commitment/backlog/contracted value或 NEW。

每个 redirect必须：

- 无环；
- target存在；
- 保留旧 ID到新 ID的兼容；
- 有正例与近邻反例测试。

## 5.3 Candidate blockers

候选召回后、LLM前应用高精度 blocker。Blocker只删除明确冲突，不自动 LINK。

### Metric

- growth vs value；
- margin vs value/yield；
- yield vs value/margin；
- share/stock price vs index level；
- index level vs company share price；
- organic/constant-currency vs unspecified growth；
- count对象不同；
- segment scope vs company scope。

### Predicate

- trade/move vs split stock；
- close vs cut price；
- index movement vs change pricing；
- announce vs sign/complete；
- report vs guide；
- price vs price target。

每个被 blocker删除的 candidate写入现有 Field audit：

- candidate ID；
- blocker code；
- raw value；
- local context hash。

## 5.4 Predicate KB

补齐实际缺失的 core predicate：

- trade/move；
- close；
- report metric；
- guide metric；
- announce plan；
- sign agreement；
- complete agreement；
- invest；
- supply/commit；
- raise/lower price target；
- upgrade/downgrade。

不要一次性重构完整 predicate ontology。

raw predicate无正确 candidate时允许 NEW/UNRESOLVED，不得选择低分近词填充。

## 5.5 Runtime alias

修改 `FieldCoreferenceResolver`：

- LLM `LINK` 成功后不再把 `raw_value` 写入 canonical aliases；
- trusted external exact link可以使用 KB原始 aliases；
- NEW canonical可保留自身 canonical_text；
- raw value、candidate和decision继续保存在 `FIELD_COREFERENCE` audit。

删除或绕过 LLM LINK 路径中的：

```python
self._add_aliases(entry, [value.raw_value], ...)
```

不新增 alias promotion状态机。

## 5.6 Participant routing

复用现有 `participant.unknown`，不新增 Participant模型字段。

修改 `_participant_route()`：

1. generic collective不做 named entity LINK：
   - analysts；
   - institutions；
   - foreign investors；
   - individual investors；
   - management；
   - customers；
   - suppliers；
   - shareholders；
2. strong regex命中后，如该表面存在跨 catalog collision，不得直接锁 namespace；
3. analyst/source-claim语境中的机构优先 institution candidate；
4. index/composite/average/ETF/fund优先 instrument；
5. `Micron management` 解析为 company-scoped management collective，不链接其他 Management机构；
6. business unit使用 owner-scoped unresolved/object candidate，不创建随机 company；
7. named_objects必须按现有 kind和owner_id筛选；
8. `Nasdaq` 在 exchange/listing语境使用 institution，在 index语境使用 instrument，不进入 PLACE。

在 Field audit中记录：

- attempted catalogs；
- exact collision count；
- selected route；
- route reason；
- generic collective标志。

## 5.7 Fiscal period

将 `_issuer_id(source, mentions)` 改为 mention-local：

```text
当前 Mention SUBJECT/ACTOR company
  -> 当前 Mention business-unit owner company
  -> source ticker
  -> unresolved
```

不得直接使用文档第一个 company participant。

只将以下表达送 fiscal resolver：

- FY/year；
- fiscal Q1-Q4；
- first/second/third/fourth fiscal quarter；
- 明确 reporting quarter且 issuer可确定。

以下不送 fiscal resolver：

- past three quarters；
- most recent quarter但 issuer/财历不明确；
- beyond 2027；
- through 2030；
- rolling 90 days。

future derived fiscal candidate视为低可信：

- 可用于候选；
- 不得单独成为 Identity hard conflict依据；
- audit标记 `derived_candidate=true`。

## 5.8 Location 与 Attribute

修改：

- `locations[]` 只走 PLACE；
- 发现 Nasdaq/index/exchange/listing venue时，在 Judge semantic validation中要求改到 participant/open attribute；
- 从 attributes catalog移除 `status` 作为 lifecycle_stage alias；
- `status=record levels` 保留 literal；
- `market` 不作为 Identity hard field；
- `forecast_horizon` 不路由 fiscal period；
- `trading_session` 使用受控 literal枚举。

## 6. Units 与数量归一化

统一 `mention_finalization.py` 和 `normalization.py` 的 active scale来源。

active scale至少包含：

- THOUSAND；
- MILLION；
- BILLION；
- TRILLION。

active ratio至少包含：

- PERCENT；
- PERCENTAGE_POINT；
- BASIS_POINT；
- MULTIPLE。

active currency继续保留当前常用 currency映射。

解析：

```text
1.2 trillion USD -> value=1_200_000_000_000, unit=USD
```

不要为普通新闻加载全部 1,664 条 v2 Unit。

XBRL unit只在 FILING fallback中使用。

## 7. Identity 与 N9

## 7.1 PRIMARY metric discriminant

不修改 public OPEN Identity Schema。

从现有 Field links读取：

```text
quantities[i].role == PRIMARY
quantities[i].metric_id 已链接到 core metric
link满足 high-trust规则
```

生成内部/audit discriminant：

```json
{
  "primary_metric_id": "REVENUE",
  "field_path": "quantities[0].metric_id",
  "trust_reason": "CORE_ONTOLOGY_EXACT"
}
```

## 7.2 High-trust Field link

第一阶段 high trust：

- core metric safe exact；
- contextual company ticker/full canonical name；
- issuer-scoped actual fiscal period；
- 明确 assertion enum。

第一阶段不将以下设为 high trust：

- fuzzy link；
- LLM candidate LINK；
- runtime learned alias；
- provisional/unresolved；
- derived future fiscal period。

每个 discriminant保留 source field_path和 trust reason到现有 Identity/hard-conflict audit。

## 7.3 Active hard cannot-link

第一批直接激活：

1. 双方都有不同的高可信 PRIMARY metric；
2. ACTUAL 与 EXPECTED/PLANNED 明确冲突；
3. 双方都有不同的高可信 principal company issuer。

第二批在 shadow precision达到 98% 后激活：

4. 同日 pre-market、regular、after-hours session冲突；
5. 明确不同 actual fiscal period；
6. report/guide、announce/sign/complete等 predicate hard conflict。

规则：

- 一方缺失或 unresolved时不做 hard decision；
- COMPARISON/BOUND/SUPPORTING metric不触发；
- hard conflict只能阻止 merge，不能自动证明两个 Mention相同；
- candidate retrieval可以继续，N9不得覆盖 active hard cannot-link。

## 7.4 Cluster 监控

继续记录：

- 最大 Atomic candidate cluster size；
- top cluster predicted pair数；
- hard conflict violation数；
- top cluster FP贡献。

不根据 cluster size自动拆分或新增 circuit-breaker节点。

## 8. KB validator

扩展 `scripts/cdecr_kb/validate_catalogs.py`：

1. 继续执行现有 schema/reference校验；
2.生成 companies/institutions/persons/instruments/named_objects 跨 catalog normalized collision统计；
3.验证 `resolution_policy.json`：
   - redirect target存在；
   - redirect无环；
   - blocked alias规范化后非空；
   - override指向合法 catalog/ID；
4.运行固定 regression queries：
   - trading_volume；
   - stock_change；
   - closing_price；
   - guaranteed_revenue；
   - deal_count；
   - revenue_growth；
   - UBS；
   - IDC；
   - Micron management；
   - Stoxx 600；
   - Nasdaq。

Validator只报告 collision，不要求全部消除或人工分类。

## 9. 审计要求

复用现有 Registry audit，不新增表。

必须能够还原：

```text
Source
  -> Dreamer candidate
  -> Grounder draft or rejection
  -> Judge action/repair
  -> Mention
  -> Evidence locator/repair
  -> Field candidates/blockers/decision
  -> PRIMARY metric discriminant
  -> hard cannot-link
```

新增 audit code集中定义，不允许散落自由字符串。

必须记录：

- Grounder rejected candidate code；
- Judge semantic validation code；
- Evidence repair kind；
- deterministic match reason；
- candidate blocker code；
- participant route reason；
- issuer selection reason；
- primary metric trust reason；
- hard conflict type。

reason协议不变，不删除正常通过原因。

## 10. 测试

## 10.1 Focused unit tests

### Mention

- 所有 candidate恰好 used或rejected；
- attributed forecast被保留；
- generic opinion被拒绝；
- partnership/supply/investment拆分；
- 不同 investor主体和相反流向拆分；
- revenue与EPS guidance拆分；
- 同一 metric bounds不拆；
- umbrella不重复保留；
- guidance assertion正确；
- EXPECTED/HYPOTHETICAL正确。

### Quantity

- PRIMARY唯一；
- comparison/bound/supporting持久化；
- comparison不进入 hard identity；
- 旧数据兼容适配。

### Evidence

- exact；
- terminal punctuation；
- whitespace；
- ambiguous repeated phrase + Main anchor；
- coreference rewrite拒绝；
- ellipsis quote拒绝；
- 单项失败不扩大。

### Field

- safe deterministic match；
- cross-catalog collision进入 candidate；
- metric core/fallback；
- redirect；
- blocker；
- LLM LINK不写 alias；
- participant generic/UBS/Stoxx/Nasdaq；
- mention-local fiscal issuer；
- horizon不走 fiscal。

### Identity

- 不同 PRIMARY metric cannot-link；
- comparison metric不触发；
- missing/unresolved不触发；
- assertion冲突；
- issuer冲突；
- N9不能覆盖 active hard conflict。

### Units

- thousand/million/billion/trillion；
- percent/percentage point/basis point/multiple；
- `1.2 trillion USD` scale。

## 10.2 Fixed-input node A/B

分别冻结：

- Dreamer input；
- Grounder input；
- Judge input；
- Field input/candidates；
- Identity input。

每项修改独立 A/B，逐 item比较，不用随机全流程 aggregate替代因果判断。

## 10.3 30 篇完整验收

重新执行 30 篇 end-to-end，并计算：

### Mention

- P/R/F1；
- Dreamer candidate recall；
- Grounder rejected code precision；
- compound rate；
- fragmentation rate；
- multi-PRIMARY violation；
- assertion violation。

### Evidence

- model raw exact rate；
- final exact/source-equivalent rate；
- ambiguous/not-found；
- repair kind；
- semantic Evidence LLM调用数必须为 0。

### Field

- predicate/participant/metric/fiscal accuracy；
- candidate recall@8；
- safe deterministic precision；
- core/fallback命中；
- blocker误删；
- NEW/UNRESOLVED；
- alias写入次数必须符合新规则。

### Identity

- pair precision/recall/F1；
- high-trust conflict precision；
- hard conflict violation；
- 最大 cluster；
- top cluster FP贡献；
- false split分类。

## 11. 放行门槛

### Mention

- Recall至少提升 5 个百分点；
- Precision下降不超过 1.5 个百分点；
- known compound bad cases修复率 ≥ 80%；
- 新增 fragmentation bad cases ≤ 2；
- candidate coverage = 100%。

### Evidence

- final exact/source-equivalent ≥ 99.5%；
- semantic Evidence repair LLM = 0；
- Evidence异常不造成文档失败。

### Field

| Field | 门槛 |
| --- | ---: |
| predicate | ≥ 90% |
| participant | ≥ 96% |
| metric | ≥ 90% |
| fiscal period | ≥ 80% |
| total | ≥ 91% |

并要求：

- candidate recall@8 ≥ 98%；
- safe deterministic precision ≥ 99%；
- 错误 runtime alias新增 = 0；
- 不能靠显著增加 UNRESOLVED达标。

### Identity

- pair precision ≥ 60%；
- judgeable recall ≥ 90.5%；
- 33-member cross-metric supercluster不再形成；
- hard conflict violation减少 ≥ 80%；
- 不得通过 mention-scoped唯一 identity制造高精度。

### 工程

- 正常流程 LLM节点数量不变；
- 正常流程调用次数不增加；
- 无新持久化表；
- KB 12 catalog通用 Schema不变；
- reason协议不变；
- item-scoped resilience保持；
- focused tests、CDECR suite、ruff、mypy、catalog validator、`git diff --check`通过。

## 12. 实施顺序与提交

### Commit 1：评估与协议基础

- Gold口径；
- Grounder rejection ledger；
- Quantity role；
- 对应 DTO/兼容/tests；
- changelog。

### Commit 2：Evidence 与确定性修复

- Evidence anchor；
- source-equivalent locator；
- TRILLION/active scale；
- mention-local issuer；
- status/Nasdaq route；
- tests/changelog。

### Commit 3：Field 与 KB

- resolution policy；
- core/fallback；
- redirects；
- blockers；
- safe deterministic match；
- participant routing；
- 停止 LLM alias回写；
- validator/tests/changelog。

### Commit 4：Mention semantic validation

- Prompt窄修改；
- Judge semantic validator；
- assertion/umbrella/atomicity repair；
- tests/changelog。

### Commit 5：Identity hard boundaries

- PRIMARY metric discriminant；
- high-trust规则；
- 第一批 active hard cannot-link；
- shadow metrics；
- tests/changelog。

### Commit 6：验收报告

- fixed-input node A/B；
- 30 篇完整验收；
- Mention/Evidence/Field/Identity指标；
- bad-case逐项变化；
- 复杂度与调用数量核对；
- 最终结论/changelog。

每个提交独立可回滚，不混入无关修改。
