# Document2｜Expectation Shell / Unit 重构方案与 Schema

> **上游来源冻结说明（2026-08-20）：** 本文尚未实施 D2 runtime 重构。后续适配时必须
> 显式区分 Global Research handoff（C1/C3/C5 + C4 结构化快照）与独立 Market
> Situation handoff（C2/O4），不得把历史 `codex_d1_v2` 的 O4-A/O4-B 名称或混合 bundle
> 静默映射为新来源。

## 一、目标与整体结构

Document2 的目标不是生成一份完整的“市场预期报告”，而是建立一套能够被后续事件持续读取、修正和用于预期差判断的有状态认知结构。

核心对象关系：

```text
ExpectationShell
└── ExpectationUnit
    ├── ExpectationState
    │   ├── StateParameter
    │   └── StateValue
    ├── RealizationFactor
    └── PotentialGap
```

三类核心内容分别解决不同问题：

* **Expectation State**：当前已经能够稳定定义、观察和持续更新的预期状态；
* **Realization Factor**：当前研究能够识别，但不适合被强行参数化的兑现条件、阻断点和修正因素；
* **Potential Gap**：在前两者和完整研究情境基础上，进一步向未来发散得到的、可能迫使当前预期发生显著修正的单点 possible occurrence。

因此：

```text
State / Factor
= 当前情境

Potential Gap
= 未来认知变化地图
```

运行时由 Codex Agent 直接读取最新 Blackboard 与事件，根据实际情况修改 State Value、Realization Factor `current_status`，判断是否命中已有 Potential Gap，以及是否形成具有交易意义的实际预期差。

不再为 Expectation Update、Gap Activation 单独设计动作 Schema。

---

# 二、全局命名规则

以下 ID 均使用模型生成的**自然语言语义名称**：

* `shell_id`
* `expectation_id`
* `parameter_id`
* `state_value_id`
* `factor_id`
* `gap_id`

例如：

```text
shell_id:
AI存储周期与Micron盈利兑现

expectation_id:
HBM供给紧张持续至2027年以后

parameter_id:
HBM供需重新平衡时间

state_value_id:
卖方预期：HBM供需重新平衡时间｜2026Q3

factor_id:
客户长期锁单持续性

gap_id:
主要AI客户首次锁定2028年HBM供给
```

这样 Agent 可以直接通过对象名称理解语义并跨节点引用。

如底层数据库需要 UUID，可作为 Agent 不可见的内部主键维护，不进入 Document2 Schema。

---

# 三、Expectation Shell

## 1. 定位

Expectation Shell 是：

> 一组必须共享完整研究上下文，但在运行时仍能被分别研究和更新的 Expectation Units 的边界。

Shell 主要解决一个执行问题：

* Unit 必须足够细，才能被具体事件独立修改；
* Agent 的研究任务又不能被切得过碎，否则会失去完整业务与因果上下文。

因此：

> **Shell 决定研究上下文粒度，Unit 决定预期认知粒度。**

Shell 本身不是交易对象，也不承担预期方向。

---

## 2. Shell 边界

`boundary_rule` 需要同时回答：

1. 为什么这些 Units 属于同一个主要经济和价值传导系统，因此必须共享上下文；
2. 一个候选 Unit 满足什么条件时，应与当前 Shell 分开。

例如：

```text
AI Capex
→ 存储需求
→ 行业供需与价格
→ Micron份额和产品结构
→ 收入、利润和现金流
```

围绕这条主要价值传导系统形成的多个 Units 可以共享同一个 Shell。

但如果某个候选 Unit 拥有：

* 基本独立的终端价值结果；
* 基本独立的事件体系；
* 基本独立的预期状态；
* 不依赖当前 Shell 核心背景即可独立研究；

则应拆分至其他 Shell。

---

## 3. Schema

```json
{
  "shell_id": "string",
  "core_question": "string",
  "boundary_rule": "string",
  "units": ["ExpectationUnit"]
}
```

### 字段作用

| 字段              | 作用                             | 必要性 | 生产者           |
| --------------- | ------------------------------ | --- | ------------- |
| `shell_id`      | Shell 的自然语言稳定名称                | 必须  | O1            |
| `core_question` | 约束 C1/C2/C3/C4 围绕同一个终端投资问题贡献研究 | 必须  | O1            |
| `boundary_rule` | 定义共享上下文原因及 Shell 拆分标准          | 必须  | O1 + Reviewer |
| `units`         | 承载 Shell 内 Expectation Units   | 必须  | Workflow      |

Shell 不保存：

* 总体 bullish / bearish；
* Market View；
* Horizon；
* Potential Gap；
* 具体事件；
* Context refs；
* priced-in 判断；
* 交易 Bias。

---

# 四、Expectation Unit

## 1. 定位

Expectation Unit 是：

> 市场会反复讨论、修正和交易的中层预期命题。

例如：

* AI Capex 未来几个季度继续扩张；
* HBM 供给紧张持续到 2027 年以后；
* Micron 能获得有意义的 HBM4 订单份额；
* HBM 收入增长能够转化为持续利润和现金流改善。

Unit 不应过宽：

> AI 存储超级周期。

也不应只是单个底层财务指标：

> FY27 EPS = 150。

合格的 Unit 应能够：

* 承载一组相关 State；
* 承载相关兑现机制；
* 被若干未来事件独立修正；
* 即使失败，也不必自动推翻整个 Shell。

---

## 2. Schema

```json
{
  "expectation_id": "string",
  "proposition": "string",
  "horizon": "string",
  "state": {
    "parameters": ["StateParameter"],
    "values": ["StateValue"]
  },
  "realization_factors": ["RealizationFactor"],
  "potential_gaps": ["PotentialGap"]
}
```

### 字段作用

| 字段                    | 作用                    | 必要性 | 生产者           |
| --------------------- | --------------------- | --- | ------------- |
| `expectation_id`      | Unit 的自然语言稳定名称        | 必须  | O1            |
| `proposition`         | 定义市场实际讨论和交易的中层命题      | 必须  | O1 + Reviewer |
| `horizon`             | 限定预期主要成立和验证时间范围       | 必须  | O1 + 领域 Agent |
| `state`               | 保存当前可稳定维护的预期状态        | 必须  | C1/C2/C3/C4   |
| `realization_factors` | 保存 State 无法充分表达的兑现机制  | 必须  | 领域 Agent      |
| `potential_gaps`      | 保存未来可能迫使当前预期修正的单点事件地图 | 必须  | 领域 Agent + O1 |

Unit 不单独保存 `market_baseline` 或 `model_expectation`。

市场当前预期应尽可能通过真实 State Values 表达，而不是由 Agent 再写一段综合“市场认为如何”的文本。

---

# 五、Expectation State

Expectation State 负责维护：

> 当前已经能够稳定定义、稳定观察，并能够持续被未来信息更新的预期状态。

它不是 Market View，也不是长篇 current status。

结构为：

```text
State Parameter
    ↓
State Value
```

---

# 六、State Parameter

## 1. 定位

State Parameter 表示：

> 一个稳定存在、值得持续观察，并能够在未来由不同来源不断提供新 State Value 的状态变量。

例如：

* HBM 供需重新平衡时间；
* Micron Vera Rubin HBM4 订单份额；
* FY27 EPS 一致预期；
* AI Hyperscaler Capex 增长趋势；
* 当前客户订单覆盖时间。

Parameter 只回答：

> **系统在观察什么？**

它不负责描述：

* 当前值；
* 当前证据；
* 在某个 Unit 中属于哪一层；
* 对 Unit 是利好还是利空；
* 重要性和置信度。

---

## 2. Parameter 准入标准

一个对象适合作为 Parameter，需要基本满足：

1. 在没有任何具体新事件时，它仍然是稳定存在的状态变量；
2. 不同时间或来源能够持续给它提供新的 Value；
3. 新旧 Value 可以在合理口径下比较或替换；
4. 它的变化会影响至少一个 Expectation Unit。

无法稳定参数化、必须依赖较复杂情境语义才能表达的重要因素，应进入 Realization Factor。

---

## 3. Schema

```json
{
  "parameter_id": "string",
  "definition": "string",
  "value_type": "NUMBER | RANGE | TIME | STAGE | DIRECTION | EVIDENCE"
}
```

### 字段作用

| 字段             | 作用                  | 必要性 |
| -------------- | ------------------- | --- |
| `parameter_id` | 参数的自然语言稳定名称         | 必须  |
| `definition`   | 限定参数口径，避免相似变量误合并    | 必须  |
| `value_type`   | 约束 Value 的主要表达和比较方式 | 必须  |

不保留：

* `transmission_layer`
* `unit`
* `ordered_states`
* `importance`
* `confidence`
* `owner_agent`
* `update_frequency`

`transmission_layer` 描述的是 Parameter 相对于某个 Unit 的使用关系，并非 Parameter 自身的稳定属性；目前也没有必要为了分类而额外结构化。

---

# 七、State Value

## 1. 定位

State Value 表示：

> 某一个来源角色，在某个时间范围和时间点，对 State Parameter 给出的当前有效状态。

同一个 Parameter 可以同时存在：

* ACTUAL；
* MANAGEMENT；
* SELL_SIDE；
* INDUSTRY_CHAIN；
* MARKET_IMPLIED。

例如：

```text
Parameter:
HBM供需重新平衡时间

MANAGEMENT:
2027年以后

SELL_SIDE:
2028Q2

INDUSTRY_CHAIN:
2027H2
```

这比把五类预期分别压缩成五段综合文字更适合持续维护和事件更新。

---

## 2. Schema

```json
{
  "state_value_id": "string",
  "parameter_id": "string",
  "source_role": "ACTUAL | MANAGEMENT | SELL_SIDE | INDUSTRY_CHAIN | MARKET_IMPLIED",
  "value": {},
  "previous_value": {},
  "time_scope": "string",
  "as_of": "ISO-8601 date or datetime",
  "citation": ["string"],
  "validity_state": "CURRENT | SUPERSEDED | DISPUTED | RETRACTED"
}
```

`previous_value` 仅在存在同 Parameter、同 source role 且时间口径可比较的前值时填写，否则为 `null`。

---

## 3. Value 类型

### NUMBER

```json
{
  "number": 52.0,
  "unit": "USD billion"
}
```

### RANGE

```json
{
  "lower": 10.0,
  "upper": 18.0,
  "unit": "PERCENTAGE"
}
```

### TIME

```json
{
  "point": "2027-Q4",
  "precision": "QUARTER"
}
```

或：

```json
{
  "start": "2027-Q1",
  "end": "2028-Q2",
  "precision": "QUARTER"
}
```

### STAGE

```json
{
  "stage": "客户验证中"
}
```

STAGE 不要求使用全局固定枚举，也不预先维护 `ordered_states`。

只有现实业务本身存在明确阶段时才使用。

### DIRECTION

```json
{
  "direction": "IMPROVING"
}
```

建议使用：

```text
IMPROVING
STABLE
WEAKENING
```

### EVIDENCE

```json
{
  "stance": "SUPPORTING",
  "strength": "MODERATE"
}
```

可使用：

```text
stance:
SUPPORTING
OPPOSING
MIXED

strength:
WEAK
MODERATE
STRONG
```

EVIDENCE 只适用于已经能够稳定定义成 Parameter 的证据状态。

更复杂、必须保留上下文语义的软预期因素，应进入 Realization Factor。

---

## 4. Previous Value

`previous_value` 用于直接观察：

> 同一个来源的预期本身是否正在发生变化。

例如：

```text
SELL_SIDE FY27 EPS

Previous:
120

Current:
150
```

或者：

```text
SELL_SIDE HBM供需重新平衡时间

Previous:
2026H2

Current:
2027H2
```

它是预期差系统非常重要的动态信息。

但不得为了填写 Previous Value 强行比较不同时间范围或不同口径的数据。

---

## 5. Citation

State Value 必须存在 citation。

原因是 State 是系统最重要的当前锚点之一，不能退化为 Agent 自由判断。

Citation 只需要提供主要直接依据，不需要额外构建复杂审计层。

---

# 八、Expectation Realization Model

Expectation State 只能覆盖已经可以稳定定义和维护的变量。

但真实市场中，大量能够显著修改预期的因素并不适合 Parameter 化。

例如火箭商业化：

State 可以保存：

* 发射次数；
* Backlog；
* 管理层发射计划；
* Sell-side 收入预期。

但以下问题很难合理压缩成标准数值：

* 核心技术是否真正通过现实飞行验证；
* 重复发射可靠性是否成立；
* 监管路径是否正在出现异常；
* 客户是否愿意由测试采购进入规模采购。

因此需要 Realization Model。

---

# 九、Realization Factor

## 1. 定位

Realization Factor 表示：

> 当前 State 无法充分表达，但根据现有研究能够识别，并会显著影响 Unit 兑现、失败、时间、强度、受益对象或转化效率的现实因素。

它是**现实机制对象**，不是未来事件预测。

---

## 2. State 与 Factor 的边界

### State Parameter

适用于：

> 能够稳定定义、持续更新、不同来源可以持续提供 Value 的变量。

### Realization Factor

适用于：

> 很重要且可以研究和观察，但当前状态必须保留具体上下文语义，无法可靠压缩成统一 Parameter Value 的因素。

如果某个 Factor 后续已经能够稳定表示成：

* 数值；
* 区间；
* 时间；
* 阶段；
* 方向；

则应考虑迁移为 State Parameter。

---

## 3. Schema

```json
{
  "factor_id": "string",
  "condition": "string",
  "structural_role": "REQUIRED | BLOCKER | MODIFIER",
  "current_status": "string",
  "impact": "string",
  "citation": ["string"],
  "observability": {
    "match_condition": "string"
  }
}
```

---

## 4. 字段说明

### `factor_id`

Factor 的自然语言稳定名称。

### `condition`

明确该 Factor 实际研究的现实条件。

例如：

> Micron 是否真正通过 Vera Rubin 平台所需的正式客户验证。

### `structural_role`

* `REQUIRED`：兑现该 Unit 需要满足的重要条件；
* `BLOCKER`：出现后会显著阻断兑现；
* `MODIFIER`：主要改变兑现时间、强度、受益对象或转化效率。

它用于理解 Factor 在现实机制中的作用，而不是表达 bullish / bearish。

### `current_status`

用简洁、受约束的自然语言说明：

> 截至现在，这个 Factor 所描述的现实条件实际发展到了什么程度。

例如：

> Micron 已公开 HBM4 送样及量产进展，但目前缺乏 NVIDIA 或 Micron 对 Vera Rubin 正式资格的明确确认，公开报道与部分产业链信息之间仍存在冲突。

不能将其强压成：

> PARTIAL / UNRESOLVED / CONFIRMED

等固定枚举，因为这些状态无法表达真实业务语义。

`current_status` 不写：

* 未来预测；
* 股价影响；
* 交易建议；
* 主观概率。

### `impact`

说明：

> Factor 为什么影响当前 Unit，以及这种影响的合理边界。

例如：

> 客户认证只证明进入供应体系，不自动证明最终订单份额。

该字段用于阻止 Agent 对局部事件进行过度外推。

### `citation`

给出 Factor 判断与当前状态的主要研究依据。

不额外拆分 definition evidence 与 state evidence，避免过度审计分散研究 Agent 注意力。

### `observability`

说明：

> 未来什么信息能够进一步确认、否定或修改该 Factor。

Realization Factor 必须具有未来可观察性，否则没有必要进入事件驱动系统。

---

# 十、Realization Factor 与 Potential Gap 的边界

这是两个完全不同的对象。

## Realization Factor

回答：

> **现实世界中，什么因素决定这个预期能不能这样实现？**

例如：

> 下一代火箭是否完成端到端飞行验证。

即使市场已经完全知道这个因素的重要性，它仍然客观存在。

---

## Potential Gap

回答：

> **未来如果发生某个具体事情，我们现在这套预期可能需要怎样重新形成？**

例如：

> 下一次任务首次完整完成全部关键飞行目标。

如果发生，可能：

> 明显提前市场对商业化时间和技术兑现能力的判断。

因此：

```text
Realization Factor
= 持续存在的现实机制

Potential Gap
= 未来单点 occurrence 及其潜在认知修正
```

一个 Factor 可以产生多个不同 Potential Gaps；一个 Potential Gap 也可以同时受到多个 State、Factor 和其他研究信息启发。

不存在一一对应关系。

---

# 十一、Potential Expectation Gap Set

## 1. 定位

Potential Gap 表示：

> 针对某个 Expectation Unit，在充分理解当前 State、Realization Factors、已有事件和研究结论之后，进一步向未来进行受约束发散得到的一个**未来单点 possible occurrence**，以及如果该事项真实发生，当前预期可能产生的有交易意义的修正。

因此：

> **Potential Gap Set 是一张未来认知变化地图。**

它不是：

* 当前公开 State 之间的静态差异；
* State 的下一个预测值；
* Factor 的机械未来版本；
* 数据库 Update 指令。

---

# 十二、Potential Gap 如何产生

State 与 Factor 是 Gap Discovery 的重要出发点，但不能成为边界。

禁止：

```text
一个 State
→ 自动写一个 Gap

一个 Factor
→ 自动写一个 Gap
```

Gap Discovery 应读取完整 Unit 情境：

* Expectation State；
* Realization Factors；
* Document1 研究；
* 当前 Event Registry；
* 历史事件；
* 行业机制；
* 技术与产品规律；
* 多个 State / Factor 的组合关系。

然后问：

> **未来有哪些具体单点事件，一旦发生，会迫使我们实质修改当前这个 Unit？**

发散可以包含：

* 当前趋势进一步延伸；
* 当前趋势突然反转；
* 某个关键兑现条件首次真正被验证；
* 某个核心阻断因素发生；
* 兑现时间提前或延迟；
* 受益对象重新分配；
* 上游逻辑成立但向下传导失败；
* 当前 State/Factor 尚未完整表达的新变量出现。

这些是推理方向，不需要固化为 Gap 枚举。

---

# 十三、Potential Gap 必须是单点 occurrence

Gap 必须具体到运行时能够判断：

> **这件事情究竟发生了没有？**

不合格：

> AI 需求可能变差。

合格：

> 核心 Hyperscaler 正式下调下一年度 AI 基础设施 Capex 指引。

不合格：

> HBM 供给提前释放并导致价格下跌。

应拆为：

1. 竞争者新增 HBM 产能提前进入规模量产；
2. HBM 合约价格首次出现持续性下跌。

这两个事件虽然可能相关，但预期意义并不相同。

---

# 十四、Potential Gap Schema

```json
{
  "gap_id": "string",
  "possible_occurrence": "string",
  "derivation": "string",
  "citation": ["string"],
  "expected_revision": "string",
  "recognition_criteria": "string | null"
}
```

---

## 1. `gap_id`

直接用自然语言表达这一未来 possibility 的核心内容。

例如：

> 主要AI客户首次锁定2028年HBM供给

---

## 2. `possible_occurrence`

明确：

> **未来到底可能发生什么？**

要求：

* 单点；
* 可观察；
* 可识别；
* 不把多个不同事件揉成一个故事。

---

## 3. `derivation`

解释：

> **为什么基于当前情境，我们会提前想到并监测这一 possibility？**

它不是解释“事件发生后的影响”，而是说明该未来可能性如何从现有研究状态进一步推导出来。

这个字段用于限制开放世界推理，避免 Gap Discovery 退化成无约束脑补。

---

## 4. `citation`

提供支持该推演出发点的主要现实研究依据。

Citation 可以来自：

* State；
* Factor；
* Event；
* Document1；
* 行业研究；
* 技术研究。

但它不意味着 Potential Gap 与任何 Parameter 或 Factor 形成结构性强绑定。

---

## 5. `expected_revision`

回答：

> **如果 possible occurrence 真正发生，当前 Expectation Unit 可能需要怎样被修正？**

例如：

> 将 HBM 紧缺持续时间进一步向后延长。

或者：

> 明显降低 Micron 获得实质 HBM4 份额的可信程度。

或者：

> 提前商业化时间判断，但不足以直接确认未来收入规模。

Expected Revision 应同时限制合理外推边界。

---

## 6. `recognition_criteria`

条件字段。

只有 Possible Occurrence 包含：

* 显著；
* 大规模；
* 有意义；
* 放缓；
* 超预期；

等语义模糊边界时使用。

例如：

> AI Capex 显著放缓。

则需要说明什么程度的信息才算真正命中。

如果 occurrence 本身已经明确：

> FAA 正式拒绝商业运营许可。

则不需要 Recognition Criteria。

---

# 十五、Potential Gap 不保存反向条件

如果一个反向 possible occurrence 本身具有独立预期修正和交易意义，应作为另一个 Gap。

例如：

### Gap A

> Micron 正式获得高于当前预期的订单份额。

### Gap B

> Micron 获得资格但实际订单份额极低。

### Gap C

> Micron 被正式排除。

### Gap D

> Micron 获得订单，但量产良率导致交付延期。

这样兑现路径与失败路径能够获得相同研究深度，而不是把所有负面可能性压进一个 `counter_condition`。

---

# 十六、Potential Gap 准入标准

一个 Potential Gap 至少应满足：

1. 是一个具体、单点的 future occurrence；
2. 发生后会实质改变当前 Unit；
3. 有合理的现实研究依据；
4. 可以被未来消息或事件识别；
5. Expected Revision 可以清楚说明；
6. 具有潜在交易意义。

Gap 数量不与 Parameter 或 Factor 数量绑定。

其数量取决于：

> 当前情境下真正值得事前准备的高价值未来 possibilities。

---

# 十七、运行时如何使用 Document2

当前运行框架使用 Codex SDK，因此不设计额外的 `ExpectationUpdate`、`GapActivation`、`StateSlotRef` 或 `ExpectedUpdateSpec`。

新事件出现后，Agent 直接读取：

* 最新 Expectation Unit；
* State Values；
* Realization Factors 及 current_status；
* Potential Gap Set；
* Event；
* 当前市场状态。

然后判断：

```text
Event
↓
是否修改某个稳定 State？
↓
需要新建或替换哪些 State Value？

同时：
↓
是否改变某个 Realization Factor 的 current_status？

同时：
↓
是否命中某个事前 Potential Gap？

↓
如果命中：
当前预期实际发生了什么修正？

↓
市场是否已经同步吸收该修正？

↓
Trading Evaluation
```

---

# 十八、硬事件与软事件

## 硬事件

例如：

* 财报；
* 正式指引；
* 合同金额；
* 订单份额；
* 官方供应资格；
* 产能数据。

通常能够直接更新 State Value。

例如：

```text
Management Revenue Guidance
Previous → Current

Sell-side EPS
Previous → Current
```

随后 Agent 重新判断相关 Potential Gaps。

---

## 软事件

例如：

* 火箭试飞；
* 技术验证；
* 供应链消息；
* 客户非量化表述；
* 监管进展。

可能完全不改变财务 State，但会改写 Factor：

```text
Before:
完整端到端飞行能力尚未验证

After:
首次完成完整端到端任务，核心技术链获得实证；
重复执行能力仍待验证
```

然后判断是否命中相关 Potential Gap。

---

# 十九、市场定价

Expectation State 可以包含具体 MARKET_IMPLIED State Value，但只有在能够形成可靠、具体锚点时才维护。

不维护：

> “这个 Unit 已经 priced in 70%”

这类不可可靠获得的抽象数值。

真正的交易问题发生在事件出现之后：

> **事件造成的新增预期修正，有多少已经被当前市场认知和价格同步吸收？**

Codex Agent 可以结合：

* 事件前的市场预期；
* Sell-side 是否已经同步；
* DoxAtlas Narrative 是否早已传播；
* 是否只是旧消息正式确认；
* 事件前后的价格和估值；
* 期权和相对表现；

进行判断。

事后价格表现主要用于审计和校准，而不是交易触发前提。

---

# 二十、领域 Agent 在 Document2 的职责

C1/C2/C3/C4 不需要分别生成完整 Detail。

它们在保留各自 Document1 完整研究上下文的基础上，对同一个 Shell 提供领域贡献。

## C1

主要贡献：

* 公司实际数据；
* 管理层指引；
* 财务 State；
* 公司业务兑现 Factors；
* 公司级 Future Possibilities。

## C2

主要贡献：

* 宏观 State；
* 利率、政策、Capex 环境；
* 宏观兑现和阻断 Factors；
* 宏观 Future Possibilities。

## C3

主要贡献：

* 产业链 State；
* 供需；
* 竞争；
* 份额；
* 客户与供应商；
* 行业 Realization Factors；
* 产业链 Future Possibilities。

## C4

主要贡献：

* MARKET_IMPLIED 状态；
* 估值；
* 市场关注焦点；
* 信息吸收与事件后的市场反馈。

O1 最终负责：

* 统一 Parameter；
* 处理重复与冲突；
* 整理 Realization Factors；
* 对 Potential Gaps 去重、拆分和收敛；
* 保证 Future Possibility Map 不退化成 State/Factor 的机械镜像。

---

# 二十一、核心验证规则

## Expectation Shell

1. 必须能够说清楚为什么其中 Units 需要共享完整研究上下文。
2. `boundary_rule` 必须同时定义核心传导边界与拆分原则。
3. 不得只因属于同一公司或行业而强行归入同一 Shell。

## Expectation Unit

1. Proposition 必须是单一中层市场预期命题。
2. 必须有明确 Horizon。
3. 应具有能够描述当前情况的 State 和/或 Realization Factors。
4. 一个 Unit 的失败不能自动等于整个 Shell 失败。

## State Parameter

1. 必须是稳定存在、能够持续接受新 Value 的状态变量。
2. 必须对所属 Unit 有实际判断意义。
3. 仅作为背景展示的数据不得进入。
4. 不因软预期难以表达而强行制造 Parameter。

## State Value

1. 必须有 citation。
2. 存在可比较历史值时填写 `previous_value`。
3. 前值必须同口径可比。
4. 无可靠状态时不生成虚假 Value。
5. 不允许以整体股价行情替代具体 MARKET_IMPLIED 状态。

## Realization Factor

1. 必须显著影响 Unit。
2. 必须是 State 无法充分表达的重要现实机制。
3. 必须维护清晰 `current_status`。
4. `current_status` 使用自然语言保留真实语义，不硬套枚举。
5. 必须说明 `impact` 边界。
6. 必须具有未来可观察性。
7. 不保存泛泛经营常识。

## Potential Gap

1. 必须描述具体、单点、未来 possible occurrence。
2. 必须说明为什么当前情境使其值得提前研究。
3. 必须有现实研究 citation。
4. 必须说明 occurrence 发生后的 Expected Revision。
5. 不允许按 State/Factor 一一机械生成。
6. 必须允许发现当前 State/Factor 尚未覆盖、但有合理依据的开放世界 possibility。
7. `recognition_criteria` 只在 occurrence 本身具有模糊判断边界时使用。

---

# 二十二、最终 Schema

```text
ExpectationShell
- shell_id
- core_question
- boundary_rule
- units

ExpectationUnit
- expectation_id
- proposition
- horizon
- state
- realization_factors
- potential_gaps

StateParameter
- parameter_id
- definition
- value_type

StateValue
- state_value_id
- parameter_id
- source_role
- value
- previous_value
- time_scope
- as_of
- citation
- validity_state

RealizationFactor
- factor_id
- condition
- structural_role
- current_status
- impact
- citation
- observability

PotentialGap
- gap_id
- possible_occurrence
- derivation
- citation
- expected_revision
- recognition_criteria
```

---

# 二十三、最终核心模型

```text
Expectation Shell
确定哪些预期必须共享研究上下文

        ↓

Expectation Unit
定义市场真正反复交易的中层命题

        ↓

Expectation State
当前能够稳定参数化和持续维护的预期状态

        +

Realization Model
当前可以研究和描述、
但不能可靠参数化的现实兑现机制

        ↓

Potential Gap Set
在完整当前情境基础上进一步向未来发散，
形成可能迫使预期重新形成的单点事件地图

        ↓

Future Event

        ↓

Codex Agent
判断 State 是否变化
判断 Factor current_status 是否变化
判断是否命中已有 Potential Gap
判断是否出现新的 Future Possibility
判断实际预期修正及市场吸收程度

        ↓

Trading Evaluation
```

整个 Document2 最终服务的不是：

> “当前这个股票总体 bullish 还是 bearish？”

而是：

> **当前市场正在交易哪些中层预期；这些预期现在具体处在什么状态；现实中哪些机制决定其兑现；未来哪些具体事件可能迫使这些预期发生显著修正。**
