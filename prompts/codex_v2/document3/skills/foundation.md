# O3 Shared Foundation

本文件定义 O3 INITIALIZE、Final Global Pass 和 `O3_MAINTAIN` 共用的业务 ontology。Agent prompt 说明 O3 的长期角色，当前 stage skill 决定本 Turn 的执行方法，supplied schemas 决定对象的精确字段。

## 1. 从 D2 Revision Space 到 Runtime Policy

D2 研究当前 expectation、Realization Factors，以及哪些未来现实变化可能使 expectation 修订。O3 将这组 revision space 转换为真实消息流可以调用的判断标准：

```text
Potential Gap
→ Tradable Path
→ Candidate Trading Trigger
→ Activation Condition
→ Direct Trading Policy
```

- **Potential Gap** 是 D2 对未来可能发生什么、为何重要、怎样修订 expectation 以及如何识别的研究对象。它提供研究来源，不直接等于 Condition。
- **Tradable Path** 是从 Gap 展开的一条经济含义完整的现实路径：一个主体或对象从当前状态转向新状态，并经 D2 `expected_revision` 对目标 ticker 形成单一 `LONG` 或 `SHORT` 方向。
- **Candidate Trading Trigger** 是 O3 研究出的下一项消息级现实事件：它包含相对于当前状态第一次产生交易意义 expectation update 的最小充分事实集合，不预设最终 Condition 数量。
- **Activation Condition** 是 W2 可以独立判断 `TRUE / FALSE / INSUFFICIENT` 的 Boolean predicate。
- **Direct Trading Policy** 将 Trigger 编译为 retrieval envelope、Activation Conditions 和预定方向；它不是实时交易执行。

一个 Gap 可以形成多个 Paths；多个 Gaps 或 Units 也可以汇入同一 Policy。Policy surface 服从真实状态路径，不服从一 Gap 一 Policy、一 Trigger record 一 Condition 或一主体名称一 Policy 的机械映射。

O3 的共同质量优先级是：

```text
忠实于 D2 revision space 与 ticker transmission
→ Trigger 存在于真实消息流
→ 在可观察状态中选择最早的交易充分边界
→ 通过 Calibration 与 comparator 提高 W2 judgeability
→ 最后优化表达、Condition 和 Policy 数量
```

## 2. Marginal Expectation Update

Trigger 的作用是更新 prior，不是证明完整 thesis：

```text
D2 expectation prior
+ 最新确认的 current reality
+ new fact
→ updated expectation
```

新事实可以更新一个或多个核心维度：

```text
probability
timing
scale
economic capture / realization path
risk
```

**Direct Trading Sufficiency** 的核心判断是：Candidate Trading Trigger 的完整 Minimal Sufficient Fact Set 是否已使 D2 中某个重要 expectation lever 发生足够明确、具有交易意义的方向更新，而且对应 `LONG / SHORT` transmission 不需要再假设集合之外另一项尚未出现的关键事实。

后续 shipment、revenue、margin、份额或最终结果仍可能不确定。只要当前事实或事实集合整体已经建立方向性 expectation delta，这些正常不确定性不会自动使 Trigger 不充分。可靠性主要来自正确的现实状态、经济传导、消息确认方式和比较基准，不来自继续等待完整兑现。

普通进展尚未改变重要 expectation lever，边界过早；等到经济结果全面确认才行动，边界可能过晚。目标是在现实可观察的候选状态中找到最早的交易充分边界。

## 3. Causal Layer 与 Minimal Sufficient Fact Set

同一 Path 上的信息处于不同 causal layers：

```text
Precursor
→ Trigger State
→ Transmission
→ Realization
```

- **Precursor** 提高事件发生的可能性或关注度，但尚未形成目标 expectation update；
- **Trigger State** 第一次跨过交易边界；
- **Transmission** 解释该状态如何改变目标 ticker 的 expectation；
- **Realization** 说明 Trigger 后来转化为 shipment、收入、利润、份额或其他经营结果。

D2 可以研究完整链条；O3 Condition 通常停在第一次跨过边界的 Trigger State。Transmission 用于证明方向，Realization 用于理解后续兑现，它们不因增加确定性就自动成为 Trigger 的必要事实。若某项 shipment 或经营结果本身定义该业务事件，而非仅证明后续结果，则可以是 Trigger，判断依据是它对当前 expectation update 的独立作用。

Candidate Trigger 可以包含一个或多个事实，最终只保留 **Minimal Sufficient Fact Set**：

```text
candidate facts
→ 判断每项事实是否改变 expectation revision 的成立性或方向
→ 仅保留不可缺少的事实
```

删除某项事实后，若同一个重要 expectation update 和 ticker transmission 仍成立，该事实只增加确认程度，不属于最小集合。Minimality 衡量信息贡献，不衡量模型在缺少该事实时是否仍对最终 thesis 足够安心。

## 4. Actor-State Dependency

**Trigger-bearing Actor / Object** 是真正承载状态变化的现实主体或对象，可以是公司、客户、供应商、竞争者、监管机构、产品、工厂、合同、产能或指标。

Actor specificity 描述的是 Trigger boundary 是否依赖某个 actor 自己的 current state，而不是是否把主体写成单数或“可识别”。判断顺序是：

```text
哪些现实 actors / objects 可能承载 Path？
→ 它们当前状态是否相同？
→ 下一项 expectation-changing transition 是否相同？
→ 消息生成路径与 ticker delta 是否相同？
```

若 Customer A 尚未削减采购、Customer B 已经削减 20%，二者需要不同 `reference_state` 和 trigger boundary。若多个竞争者的 current state、事件语义、消息结构和 ticker transmission 实质对称，例如未来任一主要竞争者发生一项独立 fab outage，则一个每次判断单一事件主体的通用 Policy 可能更准确。

因此，current state 或下一边界不同才需要 actor-specific calibration；主体名称不同本身不要求拆分。需要具体 actor 的边界不能用“一名可识别的客户”代替现实研究；状态真正对称时也无需机械枚举所有公司。

## 5. Message Production Model

O3 不只研究经济事件，也研究事件怎样成为市场可获得的信息。现实可披露性通过以下信息生产链判断：

```text
Reality Event
→ 谁首先掌握事实？
→ 谁有义务、动机或习惯披露？
→ 通常通过什么消息载体？
→ 该载体通常包含哪些事实？
→ 哪些事实通常由另一主体或更晚消息披露？
```

Disclosure-realistic 不等于可以想象一篇文章同时写下所有相关事实。关键是现实信息生产机制是否通常会让一个 source 在该时点掌握并发布 Candidate 所需的全部事实。平台 BOM、供应商 allocation、客户订单和目标公司的财务结果即使处于同一经济链，也可能属于不同 information holders、载体和时点；事后综合报道能够汇总，不证明它们会作为一条正常消息同步产生。

当 disclosure pattern 不明显时，优先查看相同 actor、相同事件类别或同行历史上类似事实如何公开：谁先报道、通过什么载体、披露到什么粒度、哪些数据通常不会同时出现。这是 **Historical Disclosure Analogy**；其作用是研究消息现实性，不要求固定案例数量。

### Source Authority 与 State Evidence

消息来源可信度与消息是否直接确认目标状态是两个维度。Official filing 可以是高质量证据；可信 sourced reporting、客户或供应商报告、监管消息和行业情报也可能直接确认状态。

`qualifying_evidence` 首先描述消息必须确认什么事实，再结合 Message Production Model 判断哪些来源能够可信地掌握或报道它。来源类型不是统一的 formal/official 门槛；`match_scope` 也不承担最终证据裁决。

## 6. Activation Condition 是 Boolean Predicate

一个 Condition 对 W2 而言，是一个可以独立判断 `TRUE / FALSE / INSUFFICIENT` 的现实命题。它根据实际需要包含：

```text
actor / object
+ state change
+ necessary scope
+ observable comparator
```

这里区分三个语义单位：

- **Candidate Trading Trigger** 是完整的 Minimal Sufficient Fact Set；Direct Trading Sufficiency 属于该集合整体。
- **Activation Condition** 是集合中的一个独立 Boolean predicate，可由 W2 判断 `TRUE / FALSE / INSUFFICIENT`；它本身不必独立形成充分的 expectation update。
- **Policy Activation** 在同一条消息满足全部必要 Conditions 后成立，此时完整 Candidate Trigger 才跨过 Direct Trading Boundary。

每个 Condition 应仍面向未来，基于真实 Actor-State，符合现实消息生产机制，并具有 Runtime 可见的真值边界。Minimality 判断该 predicate 是否是充分集合中不可缺的成员；expectation sufficiency 判断集合整体。

同一次不可分割的合同修改可以由主体、原值、新值和适用范围共同描述；qualification、production adoption、repeated shipment、收入和利润通常属于不同 predicates。若 Candidate 同时包含 BOM 下降、订单下降和库存上升，三项共同描述“需求恶化”不使它们变成一个 Boolean predicate。

Condition 数量是 Predicate Decomposition 与 Minimal Sufficient Fact Set 的结果，不是预先选择：

```text
1 个独立必要 predicate
→ 1 个 Condition

N 个独立必要 predicates
+ 同一种正常消息会同时确认全部 predicates
→ N 个 Conditions
```

全部 `activation_conditions` 使用当前同一条消息同时满足的 `AND` 语义。把 `A AND B AND C` 写进一个字符串仍是 hidden conjunction；彼此独立的 `A OR B` 属于不同 Paths 或 Policies。若多个必要 predicates 不会由同一正常消息确认，应重新校准 Trigger granularity，而不是制造 Synthetic Message。

## 7. Recall、Activation 与 Calibration

Policy 各部分承担不同任务：

### `match_scope`：retrieval envelope

它回答：哪些消息值得让 W2 看一眼？围绕 Trigger 的 actor/object、相关状态变化、precursor、部分满足信息和可能影响判断的消息类型，保持 high recall。公司、客户、供应商、监管消息、可信供应链或行业报道都可以进入召回；消息最终 `Activation = FALSE` 仍可能是有价值的 recall。

### `activation_conditions`：decision precision

它回答：哪项状态已经成立？Conditions 只保留跨过交易边界的 Minimal Sufficient Predicates，并由 W2 作真值判断。

因此：

```text
match_scope
→ high recall

activation_conditions
→ decision precision
```

`match_scope` 是 Trigger 周围的信息包络，不是 Criterion 或 `qualifying_evidence` 的改写。O3 通过 Conditions 过滤候选，不通过收窄 recall 来替代判断。

### Calibration

每个 Condition 形成独立可判定链；多条件 Policy 的 Direct Trading Boundary 由全部 Condition 链共同构成：

```text
reference_state
当前已经成立什么
↓
trigger_boundary
还需要发生什么状态变化使该 predicate 成立
↓
qualifying_evidence
消息中什么事实足以确认该 predicate 已成立
↓
criterion
W2 最终判断的自足 Boolean predicate
```

四者承担不同语义，不是同一句话的重复。`activation_summary` 紧凑概括哪些状态成立时对应何种方向。

Runtime Round 1 使用紧凑 Projection 中的 `match_scope`、`criterion[]` 和 `activation_summary`；完整 Calibration 只在需要升级时提供边界依据。因此，真值判断所需的 actor、状态变化、适用范围和 comparator 应能从 `criterion` 本身识别。

## 8. Observable Comparator 与 W2 Judgeability

Comparator 只有在 W2 能从 Runtime Projection 或当前消息取得时才可用。它至少来自以下一种：

- Policy/Criterion 中已有的明确 current baseline；
- 未来消息自身给出的 previous/current 或 before/after；
- 公开且确定的 current commitment、guidance 或 timeline；
- Runtime 可直接获得的明确历史状态。

若 comparator 需要 W2 估算“无保护情景”、推演“没有 ceiling 会是多少”或自行判断“正常库存”“合理水平”，它不是 observable comparator。

“明显、重大、大幅、广泛、持续、高位、健康、实质”等程度词应优先转换为可观察业务状态。无法完全消除时，Criterion 至少明确指标、当前基准和变化方向；数值阈值来自研究和经济含义，不为了形式精确而任意设定。

W2 judgeability 的目标是让简单 Runtime 模型判断已经研究好的 predicate 是否成立，而不是让它重新完成 baseline 估算、行业研究或经济传导推演。

## 9. Current State、时间与方向

D2 定义 expectation、经济传导和 revision space；Reference View 及必要的当前研究确定现实已经推进到哪里。已经发生、持续存在或进入 expectation baseline 的状态属于 `reference_state`；下一项足以产生 Marginal Expectation Update 的变化才属于 `trigger_boundary`。

若 D2 中的未来状态已被后续事实确认，将其吸收到新的 `reference_state`，再沿同一 Tradable Path 寻找下一项仍面向未来的边界。Reference View 是可用现实证据，不是完整世界状态；未记录某项变化不证明它尚未发生。

`decision` 按目标 ticker 的经济传导确定：

```text
现实变化
→ D2 expected_revision
→ 目标 ticker 的净经营、风险或估值影响
→ LONG / SHORT
```

消息对其直接主体的正负含义不一定等于目标 ticker 的方向。一条 Path 保持单一现实含义和方向；相反方向通常对应不同主体、状态跃迁或传导。

## 10. Canonicalization、Identity 与 Provenance

Canonicalization 比较底层状态依赖：

```text
actor / object 类型
+ current-state dependency
+ trigger semantics / state transition
+ disclosure structure
+ ticker expectation delta / decision
```

多个 actors 的 current state、trigger boundary、消息结构和 decision 实质相同，而且 Policy 每次判断一个单一主体事件时，可以共享通用 Policy。actor 当前 baseline 或下一 transition 不同，则保持独立。Canonicalization 不以 actor name 为先：同一主题不足以合并，名称或措辞不同也不足以拆分。

每个 `source_refs` 精确对应 D2 中真实存在且支持该交易含义的 `shell_id + expectation_id + gap_id`。它表达 D2 provenance，不是外部研究 citation 字段。

Initialize draft 使用 temporary `policy_id` 和 `condition_id`；确定性 assembly 负责分配或延续稳定身份。Maintenance 在同一 Tradable Path 上推进现实起点或边界时保持既有身份；底层交易含义改变时建立独立 Policy。

## 11. 内部分析语言与 Policy 语言

Marginal Expectation Update、Actor-State、Message Production、Minimality、Natural Disclosure 与 W2 Judgeability 是 O3 的分析工具，不是固定输出模板。

Policy 的 `title`、`criterion`、`activation_summary` 和 `match_scope` 使用事件本身的领域语言，例如具体主体将产品投入 production、binding volume 从 X 调整至 Y、监管规则正式生效。避免把“一名可识别的”“同一自然披露确认”“相对于参考状态”等内部测试措辞机械复制为业务条件。

所有自然语言字段以中文为主体；字段名、公司与产品名、行业缩写和原始计量单位可以保留英文。
