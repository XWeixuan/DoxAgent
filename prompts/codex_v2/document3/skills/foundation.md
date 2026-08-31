# O3 Shared Foundation

本文件定义 O3 INITIALIZE 生命周期、Final Global Pass 和 `O3_MAINTAIN` 共同使用的业务语义。当前 stage skill 决定本 Turn 如何执行；supplied schema 决定对象的精确字段。

## 1. 从 Potential Gap 到 Direct Trading Policy

D2 已经研究当前 expectation、Realization Factors，以及哪些未来现实变化可能迫使 expectation 修订。O3 将这组 revision space 转换为真实消息流可以调用的判断标准：

```text
Potential Gap
→ Tradable Path
→ Candidate Trading Trigger
→ Activation Condition
→ Direct Trading Policy
```

- **Potential Gap** 是 D2 对未来可能发生什么、为何重要、怎样修订 expectation 以及如何识别的研究对象。它提供研究来源，不直接等于交易条件。
- **Tradable Path** 是从 Gap 展开的一条经济含义完整的现实路径：一个主体或对象从当前状态转向新状态，并经 D2 `expected_revision` 对目标 ticker 形成单一 `LONG` 或 `SHORT` 方向。
- **Candidate Trading Trigger** 是 O3 研究出的下一项消息级现实变化：相对于当前状态，它第一次达到可预定直接交易判断的边界。
- **Direct Trading Policy** 把一个或多个语义相同的 Trigger 编译为消息召回范围、Activation Conditions 和预定方向；它不是实时交易执行。

一个 Gap 可以因不同主体、现实落点或方向形成多个 Paths；多个 Gaps 或 Units 也可以汇入同一 Policy。Policy surface 服从真实交易路径，不服从一 Gap 一 Policy 的机械映射。

## 2. Activation Condition

**Activation Condition 是相对于当前 `reference_state`，一个仍面向未来、现实中具有合理披露可能的最小充分事实命题。该事实一旦由一条自然消息确认，本身已经足以使目标 ticker 的相关 expectation 产生具有明确方向和交易意义的边际修订，并达到预先可定义的交易判断边界；它不需要证明 thesis 已完整兑现，也不要求排除正常的剩余不确定性。后续判定节点无需补充其他关键事实即可判断其是否成立。**

一个合格 Condition 同时具备六项属性：

- **Marginal**：相对于最新确认现实和当前 expectation baseline，它是新的变化。
- **Trade-sufficient**：该变化本身已经跨过可预定直接交易判断的边界，不需要等待完整经营兑现。
- **Minimal**：删除其中任何独立事实后，便不足以支持同样的直接交易判断。
- **Actor-specific**：能够辨认哪个现实主体或对象从什么状态变成什么状态。
- **Disclosure-realistic**：该事件具有合理发生可能，也存在掌握事实的自然发布者和正常消息类型。
- **W2-judgeable**：后续简单模型只依赖未来消息与 Policy，即可稳定判断条件是否成立。

这里的“充分”是对边际交易判断充分，不是对完整投资 thesis 的最终证明充分。

## 3. Trigger-bearing Actor 与现实披露

**Trigger-bearing Actor / Object** 是真正承载未来状态变化的主体或对象，可以是公司、客户、供应商、竞争者、监管机构、产品、工厂、合同或行业指标。O3 研究的不是抽象的“行业可能变化”，而是当前哪个对象的哪项下一变化能够成为消息级 Trigger。

**Realistic Disclosure Trigger** 同时要求：

```text
具体 actor / object
+ 基于当前状态合理可能发生的下一变化
+ 掌握该事实的自然发布者
+ 正常会披露该事实的消息类型
```

公司公告、客户声明、监管决定、正式 filing、供应商公告或可信来源报道都可能成为披露路径。综合报道理论上可以汇总多个事实，不代表这些事实会由同一自然消息同时产生；多个客户、多个平台或多个阶段应先按能够独立发生和披露的现实单位研究。

## 4. Direct Trading Sufficiency

Direct Trading Sufficiency 回答：

> 在 D2 已建立的研究和当前 `reference_state` 之上，这项新信息是否已经使关键 expectation 的概率、时点、规模、实现路径或风险发生足够明确的方向性修订，以至于能够预先规定直接交易判断？

它不要求单一消息足以证明完整 thesis、排除主要不确定性或保证最终经营兑现。Condition 可以在后续 shipment、revenue、margin 或市场份额仍未知时成立；这些剩余不确定性属于事件驱动交易本身。

边界过早会把普通进展当成 expectation-changing information；边界过晚会等到经济结果已经充分兑现。目标是最早的可靠边界：该状态本身已经足以形成有方向和交易意义的边际预期差，同时仍保留合理的早期交易价值。

## 5. 方向来自目标 ticker 的经济传导

`decision` 按以下链条确定：

```text
现实变化
→ D2 expected_revision
→ 目标 ticker 的净经营、风险或估值影响
→ LONG / SHORT
```

消息对其直接主体的正面或负面含义，不一定等于目标 ticker 的方向。一条 Tradable Path 保持单一现实含义和方向；相反方向通常意味着不同主体、状态跃迁或传导，应分别表达。

## 6. Recall、Activation 与 Calibration

Policy 各部分承担不同任务：

- `match_scope` 描述哪些消息值得进入候选召回；
- `activation_conditions` 描述哪些尚未成为现实的边界必须成立；
- `activation_summary` 紧凑表达何时成立及对应方向；
- Calibration 解释当前起点、触发边界和确认标准。

相关消息可以进入 `match_scope`，但只有跨过交易边界的事实才满足 Condition。每个 Condition 形成连续判断链：

```text
reference_state
当前已经成立什么
↓
trigger_boundary
还需要发生什么具有交易意义的变化
↓
qualifying_evidence
消息中什么事实足以确认已经跨过边界
↓
criterion
实时系统最终判断的自足命题
```

四者表达不同层次，不是同一句话的重复改写。Calibration 可以使用数值阈值，也可以使用阶段跃迁、合同或监管状态、商业采用、生产状态和相对时间等更符合现实机制的边界。

Runtime Round 1 使用紧凑 Projection 中的 `match_scope`、`criterion[]` 和 `activation_summary`；完整 Calibration 只在低置信度升级时用于边界裁决。因此，判断所必需的 actor、状态变化、适用范围和 comparator 应能从 `criterion` 本身识别，不能只藏在 Calibration 中。

## 7. Current State 与未来边界

D2 定义 expectation、经济传导和 revision space；Reference View 及必要的当前研究确定现实已经推进到哪里。已经发生、已经成为持续现实或已经进入 expectation baseline 的状态属于 `reference_state`；下一项足以产生边际修订的变化才属于 `trigger_boundary`。

若 D2 中的未来状态已被后续事实确认，将其吸收到新的 `reference_state`，再沿同一 Tradable Path 寻找下一项仍面向未来的边界。Reference View 是可用现实证据，不是完整世界状态；未记录某项变化不证明它尚未发生。

## 8. Single Condition 与 Multi-condition

**Single Condition 是一个完整、单义、可独立判断真假的世界状态命题，不是一条可以容纳任意 `A AND B AND C` 的长句。** 同一合同修改中的主体、原值、新值和必要适用范围可以共同描述一个不可分割的事实；资格通过、量产、重复 shipment 和收入确认则通常是不同阶段。

在写 Condition 前，将候选 Trigger 拆成可独立判定的事实：若某项能够单独发生、单独为假，或通常由不同主体、时点或消息披露，它就是独立事实，不能因写进同一字符串而变成一个 Condition。

只有当多个独立事实都通过 Minimality Test，且同一自然消息确实会同时确认全部事实时，才使用多条件 Policy。全部 `activation_conditions` 采用当前同一条消息同时满足的 `AND` 语义。彼此独立的 `A OR B` 属于不同 Paths 或 Policies。

如果没有单项事实足以交易，而必要事实又不可能由同一自然消息确认，应调整 actor 或 Trigger 粒度、寻找更合适的交易边界，或保留 `UNRESOLVED`，而不是制造 Synthetic Message。

## 9. W2 Judgeability 与 Comparator

`criterion` 应让 W2 识别必要的：

```text
actor / object
+ future state change
+ necessary scope
+ comparator（当判断依赖相对或程度变化时）
```

“明显、重大、大幅、广泛、持续、高位、健康、实质”等程度词若没有比较锚，不能形成低自由度判断。优先把它们翻译成可观察业务状态；无法完全消除时，至少明确与当前状态、正式 guidance、commitment、timeline、具体数值或有依据的历史区间相比发生了什么变化。数值阈值应来自研究和经济含义，而不是为了形式精确而任意设定。

## 10. Canonicalization、Identity 与 Provenance

Canonicalization 比较底层语义：

```text
trigger-bearing actor / object
+ state transition
+ Activation Boundary
+ decision
```

四者实质相同通常维护一条 Policy，合并 `source_refs`；具体 actors 能够独立发生、独立披露并各自形成足够 expectation delta 时，即使来自同一 Gap 和方向，也保持不同 Policies。同一主题不足以合并，措辞不同也不足以拆分。

每个 `source_refs` 必须精确对应 D2 中真实存在且支持该交易含义的 `shell_id + expectation_id + gap_id`。它表达 D2 provenance，不是外部研究 citation 字段。

Initialize draft 使用 temporary `policy_id`；确定性 assembly 负责分配或延续稳定 Policy/Condition 身份。Maintenance 在同一 Tradable Path 上推进现实起点或边界时保持既有身份；交易含义实质改变时建立独立 Policy。

所有自然语言字段以中文为主体，字段名、公司与产品名、行业缩写和原始计量单位可以保留英文。
