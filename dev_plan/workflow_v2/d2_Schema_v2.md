# Expectation Shell / Unit 最终 Schema v2

## 一、对象关系

```text
ExpectationShell
└── ExpectationUnit
    ├── ExpectationState
    │   ├── StateParameter
    │   └── StateValue
    ├── RealizationFactor
    └── PotentialGap
```

运行时由 Codex Agent 直接读取相关 Expectation Unit，并根据新事件更新 State Value、Realization Factor 的 `current_status`，重新判断 Potential Gap 是否仍成立或是否已被事件命中。本 Schema 不再为 Expectation Update、Gap Activation 设计独立动作对象。

---

## 二、全局命名规则

本 Schema 中的 `shell_id`、`expectation_id`、`parameter_id`、`state_value_id`、`factor_id`、`gap_id` 均使用**模型生成的自然语言语义名称**，而不是 `param_xxx`、`factor_xxx` 一类机器编码。

目的：

- Agent 直接读取 ID 即能理解对象含义；
- 避免对象只有机器 ID、没有实际名称；
- 便于不同 Agent 在 Blackboard 中引用同一个对象。

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

如底层数据库需要 UUID，可作为 Agent 不可见的内部主键维护，不进入本 Schema。

---

# 三、顶层 Document2

```json
{
  "shells": [
    {
      "shell_id": "AI存储周期与Micron盈利兑现",
      "core_question": "AI存储需求、行业供给约束与Micron竞争兑现，能否持续支撑其盈利增长？",
      "boundary_rule": "本Shell只覆盖AI存储需求经行业供需、Micron业务份额与产品结构向收入、利润和现金流兑现的主要传导系统。若某候选Unit拥有基本独立的终端价值结果、主要事件体系和预期状态，且不依赖这一核心传导背景即可单独研究和更新，则应拆分至其他Shell。",
      "units": []
    }
  ]
}
```

---

# 四、Expectation Shell

## Schema

```json
{
  "shell_id": "string",
  "core_question": "string",
  "boundary_rule": "string",
  "units": ["ExpectationUnit"]
}
```

## 字段评估

| 字段 | 作用 | 必要性 | 生产能力 |
| --- | --- | --- | --- |
| `shell_id` | Shell 的自然语言稳定名称，供 Unit 归属、继承与跨节点引用 | 必须 | O1 生成 |
| `core_question` | 约束 C1/C2/C3/C4 围绕同一个终端投资问题贡献 Detail | 必须 | O1 能形成 |
| `boundary_rule` | 同时说明为什么这些 Units 应共享上下文，以及什么情况必须与本 Shell 分开 | 必须 | O1 提出，Reviewer 审查 |
| `units` | 承载该 Shell 下的 Expectation Units | 必须 | Workflow 组装 |

## 不进入核心 Schema

- Shell 总体方向；
- Shell Market View；
- Shell Horizon；
- Context refs；
- 正面或负面事件；
- 交易 Bias；
- 共享变量列表。

这些内容要么由 Units 推导，要么属于 Workflow Memory 或展示层。

---

# 五、Expectation Unit

## Schema

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

## 字段评估

| 字段 | 作用 | 必要性 | 生产能力 |
| --- | --- | --- | --- |
| `expectation_id` | Unit 的自然语言稳定名称，作为 State、Factor、Gap 的共同父对象 | 必须 | O1 生成 |
| `proposition` | 定义市场实际讨论、修正和交易的中层预期命题 | 必须 | O1 形成，结构审查 |
| `horizon` | 限定预期成立和验证的主要业务时间窗口 | 必须 | O1 与领域 Agent 能给出 |
| `state` | 管理当前已经能够稳定定义、观测和持续更新的预期状态 | 必须 | C1/C2/C3/C4 分工贡献 |
| `realization_factors` | 管理 State 明确指标之外的重要兑现条件、阻断点和修正因素 | 必须 | 领域 Agent 能提出 |
| `potential_gaps` | 保存基于当前完整情境进一步发散得到的未来单点可能性 | 必须 | 领域 Agent 发散，O1 统合 |

Unit 不保存单独的 `market_baseline` 或 `model_expectation`。市场基准应体现在 State Values 中真实存在的管理层、卖方、产业链和市场隐含预期上，而不是由 Agent 再写一段综合判断。

---

# 六、State Parameter

## 定义

State Parameter 表示：

> 在当前 Expectation Unit 中，一个已经能够被稳定定义，并能够由不同来源持续提供新 State Value 的预期状态变量。

Parameter 只负责定义“系统在观察什么”，不承担其在某个 Unit 中的传导位置、重要性或当前状态。

## Schema

```json
{
  "parameter_id": "string",
  "definition": "string",
  "value_type": "NUMBER | RANGE | TIME | STAGE | DIRECTION | EVIDENCE"
}
```

## 示例

```json
{
  "parameter_id": "Micron Vera Rubin HBM4订单份额",
  "definition": "Micron在Vera Rubin首轮HBM4订单分配中的实际或预期份额",
  "value_type": "RANGE"
}
```

```json
{
  "parameter_id": "HBM供需重新平衡时间",
  "definition": "HBM市场从明显供不应求恢复至供需基本平衡的预期时间",
  "value_type": "TIME"
}
```

## 字段评估

| 字段 | 作用 | 必要性 | 生产能力 |
| --- | --- | --- | --- |
| `parameter_id` | Parameter 的自然语言稳定名称，使不同来源和时间的 State Value 指向同一变量 | 必须 | 领域 Agent 提出，O1 归一 |
| `definition` | 限定指标口径，避免相似变量被错误合并 | 必须 | 领域 Agent 能形成 |
| `value_type` | 约束 State Value 的主要表达方式，便于比较和更新 | 必须 | Agent 能稳定判断 |

## 明确删除

- `transmission_layer`：它描述 Parameter 相对于某个 Unit 的使用位置，不是 Parameter 自身的稳定属性；当前下游也不需要依靠它执行不同判断。
- `unit`：数值单位放入具体 State Value，不需要绑定在 Parameter 上。
- `ordered_states`：不是所有阶段或软状态都能被压缩成统一线性顺序，强制填写会损失语义。
- `importance`、`confidence`、`owner_agent`、`update_frequency`：均不进入核心 Schema。

---

# 七、State Value

## 定义

State Value 表示：

> 某一来源角色，在特定时间范围和时间点，对一个 State Parameter 给出的当前有效状态。

同一 Parameter 可以同时存在实际值、管理层预期、卖方预期、产业链预期和市场隐含值。

## Schema

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

`previous_value` 保存同一 `parameter_id + source_role + 可比 time_scope` 下最近一个可比较前值。没有可靠前值时填写 `null`。

模型派生计算不新增单独的 `source_role`。如确实需要模型计算值，应保留计算依据，并在上层 Agent 输出中说明其为派生结果。

## Value 类型

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

`STAGE` 不要求使用全局固定枚举，也不要求预先定义 `ordered_states`。只有来源本身存在明确、稳定的业务阶段时才使用。

### DIRECTION

```json
{
  "direction": "IMPROVING"
}
```

可使用：

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

建议：

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

EVIDENCE 只用于已经能够稳定定义为 State Parameter 的证据状态；更复杂、必须依靠上下文语义表达的软兑现因素，应放入 Realization Factor。

## 示例

```json
{
  "state_value_id": "卖方预期：Micron Vera Rubin HBM4订单份额｜首轮订单分配",
  "parameter_id": "Micron Vera Rubin HBM4订单份额",
  "source_role": "SELL_SIDE",
  "value": {
    "lower": 5.0,
    "upper": 10.0,
    "unit": "PERCENTAGE"
  },
  "previous_value": {
    "lower": 8.0,
    "upper": 12.0,
    "unit": "PERCENTAGE"
  },
  "time_scope": "Vera Rubin首轮订单分配",
  "as_of": "2026-07-06",
  "citation": [
    "对应卖方预期数据的系统 citation"
  ],
  "validity_state": "CURRENT"
}
```

## 字段评估

| 字段 | 作用 | 必要性 | 生产能力 |
| --- | --- | --- | --- |
| `state_value_id` | State Value 的自然语言名称，便于 Gap、Agent 和历史版本直接引用 | 必须 | 模型生成 |
| `parameter_id` | 指向其描述的稳定 Parameter | 必须 | Workflow 关联 |
| `source_role` | 区分实际、管理层、卖方、产业链和市场隐含状态 | 必须 | 来源信息足够 |
| `value` | 保存当前状态 | 必须 | 硬数据直接填；可稳定表达的软状态按类型填写 |
| `previous_value` | 提供可直接比较的前值，帮助 Agent 判断本次预期是否发生上修、下修或阶段变化 | 条件必需 | 由历史同口径 State Value 提供 |
| `time_scope` | 确定该 Value 对应的财期、产品周期或预测期 | 必须 | 来源通常可提供 |
| `as_of` | 确定当前状态的时间截点 | 必须 | 来源时间可提供 |
| `citation` | 给出该 Value 的直接依据，避免无来源生成 | 必须 | 当前研究和引用能力支持 |
| `validity_state` | 区分当前值、被替代值、争议值和撤回值 | 必须 | Workflow / Agent 可维护 |

模型可见的当前视图默认优先注入 `CURRENT` 与必要的 `DISPUTED` 值；历史状态由 Blackboard 继承和版本能力保留。

---

# 八、Realization Factor

## 定义

Realization Factor 表示：

> State 明确参数之外，在当前情境下能够通过研究和推理识别，并会显著影响 Expectation Unit 最终兑现、失败、时间、强度、受益对象或转化效率的关键因素。

Realization Factor 处理的是重要、可观察，但不适合被强行压缩成稳定 Parameter/Value 的开放世界因素。

## Schema

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

## 示例

```json
{
  "factor_id": "Micron获得Vera Rubin正式客户验证",
  "condition": "Micron产品是否真正通过Vera Rubin平台所需的正式客户验证",
  "structural_role": "REQUIRED",
  "current_status": "Micron已公开HBM4送样与量产进展，但目前缺乏NVIDIA或Micron对Vera Rubin正式资格的明确确认，公开报道与部分产业链信息之间仍存在冲突。",
  "impact": "通过验证只确认进入供应体系，不自动证明Micron能够获得有意义的订单份额；若正式未通过，则订单份额预期缺少基本兑现前提。",
  "citation": [
    "支持当前factor判断及current_status的主要系统citation"
  ],
  "observability": {
    "match_condition": "NVIDIA、Micron或高可信独立来源明确确认Vera Rubin验证或供应资格结果。"
  }
}
```

## 字段评估

| 字段 | 作用 | 必要性 | 生产能力 |
| --- | --- | --- | --- |
| `factor_id` | Factor 的自然语言稳定名称，使不同 Agent 和未来事件持续指向同一因素 | 必须 | 模型生成 |
| `condition` | 明确该 Factor 研究的现实条件是什么 | 必须 | 领域 Agent 能形成 |
| `structural_role` | 区分必要条件、阻断点和结果修正因素 | 必须 | 领域 Agent 能判断 |
| `current_status` | 用受约束自然语言表达该 Factor 在当前情境下真实发展到什么程度 | 必须 | 领域 Agent 基于已有研究能够形成 |
| `impact` | 说明该因素变化会怎样影响 Unit，并限制过度外推 | 必须 | 领域 Agent 能解释 |
| `citation` | 给出 Factor 判断及当前状态的主要依据；不再拆分多套审计字段 | 必须 | D1、事件库及研究引用可支持 |
| `observability` | 告诉下游未来什么信息可以进一步确认、否定或改变这个 Factor | 必须 | Agent 与事件库能力可完成 |

## 生成约束

- `current_status` 使用简洁自然语言，不套 `UNRESOLVED / PARTIAL / CONFIRMED` 等固定枚举。
- `current_status` 只描述当前现实状态，不写未来预测、股价意义或交易建议。
- 如果某个 Factor 已经能够长期稳定压缩成数值、区间、时间、阶段或方向，应优先考虑转为 State Parameter，而不是继续留在 Realization Factor。
- 不保留独立 `state_evidence_refs`，避免过度审计分散 Agent 对研究本身的注意力。

---

# 九、Potential Gap

## 定义

Potential Gap 表示：

> 针对某个 Expectation Unit，基于当前 State、Realization Factors 和整体研究情境进一步发散推演出的一个未来单点可能发生事项，以及如果该事项真实发生，当前预期可能产生的有交易意义的修正。

Potential Gap 是事前形成的**未来认知变化地图**。它不是某个 State 或 Factor 的机械未来版本，也不要求强绑定 Parameter、State Slot 或 Factor。

## Schema

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

## 示例一：基于明确 State 与 Factor 推演

```json
{
  "gap_id": "主要AI客户首次锁定2028年HBM供给",
  "possible_occurrence": "主要AI客户首次正式签署覆盖2028年的HBM长期供货或预付采购协议。",
  "derivation": "当前长期锁单已经提高2026—2027年的需求可见度，而新增HBM供给建设和良率爬坡仍需要较长时间。如果客户锁单期限进一步延伸至2028年，将形成比当前公开需求可见度更远的单点验证。",
  "citation": [
    "当前客户长期锁单、HBM供需与扩产研究对应的主要系统citation"
  ],
  "expected_revision": "将明显增强HBM紧缺持续时间向2028年延伸的判断，并可能迫使市场重新评估2027年以后供需正常化的时间；但该事件本身不直接证明Micron能够获得更高公司份额。",
  "recognition_criteria": null
}
```

## 示例二：开放世界发散

```json
{
  "gap_id": "下一代AI加速器显著降低单位算力HBM需求",
  "possible_occurrence": "下一代主流AI加速器架构或系统方案出现明确技术变化，使单位算力所需HBM容量或带宽需求显著低于当前路线图假设。",
  "derivation": "当前长期HBM需求判断高度依赖AI算力增长继续转化为单位系统更高的HBM需求。如果技术架构改变这一关系，即使云厂商Capex维持增长，也可能使既有HBM需求斜率失效；这一可能性并不需要当前已有对应State Parameter才能成立。",
  "citation": [
    "当前HBM需求驱动与AI系统架构研究对应的主要系统citation"
  ],
  "expected_revision": "需要下修AI Capex向HBM需求的传导强度，并重新评估长期供给紧张的持续时间；不应机械等同于AI Capex本身下修。",
  "recognition_criteria": "需要来自主流芯片厂商正式技术路线、产品规格或多个高可信独立技术来源的明确证据，普通架构讨论或未经验证的推测不足以命中。"
}
```

## 字段评估

| 字段 | 作用 | 必要性 | 生产能力 |
| --- | --- | --- | --- |
| `gap_id` | Gap 的自然语言名称，直接表达这一个未来可能性的核心事项 | 必须 | 模型生成 |
| `possible_occurrence` | 明确未来单点可能发生什么，使 Agent 能判断现实事件是否真正命中 | 必须 | 领域 Agent 能发散，O1 统合 |
| `derivation` | 解释为什么在当前情境下值得提前考虑这一 possibility，防止无依据脑补 | 必须 | Agent 基于完整研究上下文能够形成 |
| `citation` | 给出支撑该推演出发点的主要研究依据，但不将 Gap 结构性绑定到某个 State/Factor | 必须 | 当前研究引用能力支持 |
| `expected_revision` | 说明若 occurrence 发生，当前 Unit 哪一部分判断可能如何被修正，以及不能过度外推到哪里 | 必须 | Agent 有能力做受约束语义推理 |
| `recognition_criteria` | 当 occurrence 含有“显著”“有意义”“放缓”等模糊边界时，说明什么证据才算真正发生 | 条件字段 | 领域 Agent 可给出 |

## 生成约束

1. Potential Gap 必须是一个可被未来消息识别的**单点 possible occurrence**，不能是泛泛的“风险上升”或多件事拼接的长因果故事。
2. 不允许按“一个 State Parameter 对应一个 Gap”或“一个 Realization Factor 对应一个 Gap”的模板机械生成。
3. Gap 可以受 State、Factor、历史事件、D1 研究、行业机制和跨因素推理启发，也可以来自在完整当前情境基础上的开放世界发散。
4. `citation` 只负责约束推演必须有现实研究基础，不代表 Gap 与某个 State/Factor 存在固定结构绑定。
5. `expected_revision` 只描述预期可能怎样被修正，不提前定义具体 State/Factor 的数据库更新操作。
6. 一个可能性的反向结果如果本身具有独立交易意义，应作为另一个 Potential Gap 展开，而不是隐藏在 `counter_condition` 中。
7. 不在 Potential Gap 中保存 `StateSlotRef`、`target_type`、`ExpectedUpdateSpec`、`market_anchor_slots`、`invalidation_rules`、`gap_dimension` 或专门的 Activation Schema。

---

# 十、核心验证规则

## Shell

1. Shell 必须能够说清楚为什么其中 Units 需要共享完整研究上下文。
2. `boundary_rule` 必须同时给出本 Shell 的核心传导边界与拆分原则。
3. 不得只因“同一公司”或“同一行业”而将无关 Units 放入同一 Shell。

## Unit

1. Proposition 必须是单一的中层市场预期命题。
2. 必须有明确 Horizon。
3. 应拥有能够描述当前预期状态的 State，或存在合理理由说明某部分只能由 Realization Factor 表达。
4. 一个 Unit 的失败不能自动等于整个 Shell 失败。

## State Parameter

1. 必须是稳定存在、能够持续被不同来源提供新 State Value 的状态变量。
2. 必须可能影响所属 Unit 的判断。
3. 仅作为背景展示的数据不得进入。
4. 不因软预期难以表达而强行制造 Parameter；无法稳定参数化的重要因素进入 Realization Factor。

## State Value

1. 必须有 `citation`。
2. 新值若存在可比较的同口径历史值，应填写 `previous_value`。
3. `previous_value` 必须与当前值在 Parameter、来源角色和主要时间口径上可比，不得为了填字段强行比较不同口径数据。
4. 无可靠状态时不生成虚假 State Value。
5. 不允许用整体行情替代某个具体 Parameter 的 MARKET_IMPLIED 状态。

## Realization Factor

1. 必须显著影响 Unit。
2. 必须属于当前 State 无法充分表达、但仍可通过研究识别的重要兑现因素。
3. 必须有清晰的 `current_status`。
4. `current_status` 必须保留实际语义，不使用僵化枚举替代复杂现实状态。
5. 必须说明 `impact` 边界，避免局部事件被外推成整个 Unit 已兑现或失效。
6. 必须具有未来可观察接口。
7. 不得保存泛泛经营常识。

## Potential Gap

1. 必须描述一个具体、单点、未来 possible occurrence。
2. 必须说明该 occurrence 为什么值得从当前情境进一步推演。
3. 必须有现实研究 `citation`，但不得要求必须直接引用 State 或 Factor。
4. 必须明确 occurrence 若发生，预期可能怎样修正。
5. 不得机械地从现有 State/Factor 一一改写产生 Gap。
6. 应主动覆盖当前结构之外但具有合理研究基础的未来可能性，避免 Gap Set 被既有参数边界锁死。
7. `recognition_criteria` 仅在 occurrence 的判断边界本身模糊时使用。

---

# 十一、最终核心字段清单

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
- recognition_criteria（条件字段）
```

本版本不再定义专门的 `ExpectationUpdate`、`GapActivation`、`StateSlotRef`、`GapRule` 或 `ExpectedUpdateSpec`。运行时由 Codex Agent 直接基于最新 Blackboard、事件和上述对象完成更新、判断和工具调用。
