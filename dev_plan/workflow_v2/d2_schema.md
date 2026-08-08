# Expectation Shell / Unit 最终 Schema v1

## 一、对象关系

```text
ExpectationShell
└── ExpectationUnit
    ├── StateParameter
    │   └── StateValue
    ├── RealizationFactor
    └── PotentialGap

Event
└── ExpectationUpdate
    ├── 创建或替换 StateValue
    └── 更新 RealizationFactor

PotentialGap + ExpectationUpdate
└── GapActivation
    ├── GapDelta
    └── MarketAbsorption
```

核心更新流程：

```text
Event
→ ExpectationUpdate
→ State / Factor 更新
→ Potential Gap 重新评估
→ Gap Activation
→ 计算预期差及剩余未定价部分
```

---

# 二、顶层 Document2

```json
{
  "shells": [
    {
      "shell_id": "shell_mu_ai_memory",
      "core_question": "AI存储需求、行业供给约束与Micron竞争兑现，能否持续支撑其盈利增长？",
      "boundary_rule": {
        "shared_system": "本Shell覆盖AI存储需求向Micron业务份额、收入、利润和现金流兑现的主要传导系统。",
        "separation_test": "若候选Unit拥有独立的终端价值结果、主要事件体系和状态参数，且不依赖本Shell的核心传导背景，则应拆分为其他Shell。"
      },
      "units": []
    }
  ]
}
```

---

# 三、Expectation Shell

## Schema

```json
{
  "shell_id": "string",
  "core_question": "string",
  "boundary_rule": {
    "shared_system": "string",
    "separation_test": "string"
  },
  "units": ["ExpectationUnit"]
}
```

## 字段评估

| 字段                | 作用                          | 必要性 | 生产能力               |
| ----------------- | --------------------------- | --- | ------------------ |
| `shell_id`        | 稳定标识 Shell，支持继承、拆分和更新       | 必须  | 系统生成               |
| `core_question`   | 约束 C1/C2/C3/C4 围绕同一终端投资问题工作 | 必须  | O1 能够形成            |
| `shared_system`   | 说明 Units 为什么必须共享研究上下文       | 必须  | O1 提出，Reviewer 审查  |
| `separation_test` | 给出 Shell 拆分标准，防止范围无限扩大      | 必须  | O1 与 Reviewer 能够完成 |
| `units`           | 承载该 Shell 下的 Units          | 必须  | Workflow 组装        |

## 不进入核心 Schema

* Shell 总体方向；
* Shell Market View；
* Shell Horizon；
* Context refs；
* 正面或负面事件；
* 交易 Bias；
* 共享变量列表。

这些信息要么由 Units 推导，要么属于 Workflow Memory 或展示层。

---

# 四、Expectation Unit

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

| 字段                    | 作用                           | 必要性 | 生产能力                  |
| --------------------- | ---------------------------- | --- | --------------------- |
| `expectation_id`      | State、Factor、Gap 和历史更新的稳定父对象 | 必须  | 系统生成                  |
| `proposition`         | 定义市场实际交易和修正的中层命题             | 必须  | O1 形成，结构审查            |
| `horizon`             | 判断事件的相关性、提前或延迟及 Gap 是否过期     | 必须  | O1 与领域 Agent 能够给出业务周期 |
| `state`               | 保存当前明确、可维护的预期参数              | 必须  | C1/C2/C3/C4 分工贡献      |
| `realization_factors` | 覆盖明确指标以外的兑现条件和阻断因素           | 必须  | 领域 Agent 能够提出         |
| `potential_gaps`      | 保存未来事件可能造成的条件性预期修正           | 必须  | 领域 Agent 提议，O1 统合     |

Unit 不保存单独的 `market_baseline` 或 `model_expectation`。市场基准由 State Values 中的 `SELL_SIDE`、`INDUSTRY_CHAIN` 和 `MARKET_IMPLIED` 状态表达。

---

# 五、State Parameter

## 定义

State Parameter 表示：

> 该 Unit 中需要持续维护和被事件更新的一个明确预期参数。

## Schema

```json
{
  "parameter_id": "string",
  "definition": "string",
  "transmission_layer": "MACRO | INDUSTRY_CHAIN | BUSINESS | FINANCIAL | MARKET_PRICING",
  "value_type": "NUMBER | RANGE | TIME | STAGE | DIRECTION | EVIDENCE",
  "unit": "string | null",
  "ordered_states": ["string"]
}
```

## 条件约束

* `NUMBER`、`RANGE`：`unit` 必填；
* `STAGE`：`ordered_states` 必填；
* 其他类型：不填写无关字段。

## 示例

```json
{
  "parameter_id": "param_mu_hbm4_share",
  "definition": "Micron在Vera Rubin首轮HBM4订单中的实际或预期份额",
  "transmission_layer": "BUSINESS",
  "value_type": "RANGE",
  "unit": "PERCENTAGE",
  "ordered_states": []
}
```

```json
{
  "parameter_id": "param_mu_hbm4_qualification",
  "definition": "Micron在Vera Rubin HBM4供应体系中的资格和量产阶段",
  "transmission_layer": "BUSINESS",
  "value_type": "STAGE",
  "unit": null,
  "ordered_states": [
    "NOT_STARTED",
    "SAMPLING",
    "VALIDATING",
    "QUALIFIED",
    "RAMPING",
    "MASS_PRODUCTION"
  ]
}
```

## 字段评估

| 字段                   | 作用                 | 必要性  | 生产能力            |
| -------------------- | ------------------ | ---- | --------------- |
| `parameter_id`       | 让不同来源和时间的值指向同一参数   | 必须   | 系统/O1 归一        |
| `definition`         | 限定参数口径，防止相似变量误合并   | 必须   | 领域 Agent 能够形成   |
| `transmission_layer` | 服务 Agent 路由和传导位置识别 | 必须   | Agent 能稳定判断主要层级 |
| `value_type`         | 决定值如何保存、比较和更新      | 必须   | Agent 能稳定判断     |
| `unit`               | 统一数值口径             | 条件必需 | 数据和领域 Agent 可提供 |
| `ordered_states`     | 让阶段推进或倒退可以被结构化比较   | 条件必需 | 领域 Agent 可定义    |

不保留 `importance`、`confidence`、`owner_agent`、`update_frequency`。

---

# 六、State Value

## 定义

State Value 表示：

> 某一来源角色，在特定时间范围内对某个参数给出的当前有效状态。

同一 Parameter 可以同时拥有实际值、管理层预期、卖方预期、产业链预期和市场隐含值。

## Schema

```json
{
  "state_value_id": "string",
  "parameter_id": "string",
  "source_role": "ACTUAL | MANAGEMENT | SELL_SIDE | INDUSTRY_CHAIN | MARKET_IMPLIED",
  "value": {},
  "time_scope": "string",
  "as_of": "ISO-8601 date or datetime",
  "basis_refs": ["ObjectRef"],
  "validity_state": "CURRENT | SUPERSEDED | DISPUTED | RETRACTED"
}
```

模型派生值使用其输入数据对应的现有 `source_role`，不新增来源枚举。

## Value 类型

### NUMBER

```json
{
  "number": 52.0
}
```

### RANGE

```json
{
  "lower": 10.0,
  "upper": 18.0
}
```

`lower` 和 `upper` 必须同时存在；无可靠状态时不生成 StateValue。

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
  "stage": "QUALIFIED"
}
```

### DIRECTION

```json
{
  "direction": "IMPROVING"
}
```

允许值：

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

允许：

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

## ObjectRef

```json
{
  "object_type": "EVENT | METRIC | EVIDENCE | FILING | ANALYST_ESTIMATE | STATE_VALUE | REALIZATION_FACTOR",
  "object_id": "string"
}
```

统一 ObjectRef 解析仓库后续开发，本版本只记录该待办。

## 示例

```json
{
  "state_value_id": "sv_mu_hbm4_share_sellside_q3",
  "parameter_id": "param_mu_hbm4_share",
  "source_role": "SELL_SIDE",
  "value": {
    "lower": 5.0,
    "upper": 10.0
  },
  "time_scope": "Vera Rubin首轮订单分配",
  "as_of": "2026-07-06",
  "basis_refs": [
    {
      "object_type": "ANALYST_ESTIMATE",
      "object_id": "estimate_mu_hbm4_share_001"
    }
  ],
  "validity_state": "CURRENT"
}
```

## 字段评估

| 字段               | 作用                     | 必要性 | 生产能力                   |
| ---------------- | ---------------------- | --- | ---------------------- |
| `state_value_id` | 支持 Gap 锚点、历史更新和审计      | 必须  | 系统生成                   |
| `parameter_id`   | 指向明确参数                 | 必须  | 系统关联                   |
| `source_role`    | 区分现实、管理层、卖方、产业链和市场隐含状态 | 必须  | 来源信息足够                 |
| `value`          | 保存可比较、可被事件更新的状态        | 必须  | 硬数据直接填；软预期使用方向、阶段或证据   |
| `time_scope`     | 区分季度、产品周期和长期预测         | 必须  | 来源通常能够提供               |
| `as_of`          | 判断状态是否过时并完成增量更新        | 必须  | 来源时间可提供                |
| `basis_refs`     | 防止 Agent 无依据生成状态       | 必须  | 当前 Event/Evidence 能力支持 |
| `validity_state` | 防止旧传闻或旧预测继续被当作当前状态     | 必须  | Curator/O1 可更新         |

模型可见视图默认只注入 `CURRENT` 和 `DISPUTED` 值；`SUPERSEDED`、`RETRACTED` 保留在审计和历史层。

---

# 七、Realization Factor

## 定义

Realization Factor 表示：

> State 明确指标之外，仍会显著影响 Unit 兑现、失败、时间、强度或受益对象的关键条件。

## Schema

```json
{
  "factor_id": "string",
  "condition": "string",
  "structural_role": "REQUIRED | BLOCKER | MODIFIER",
  "impact": "string",
  "basis_refs": ["ObjectRef"],
  "observability": {
    "match_condition": "string"
  },
  "factor_state": "UNRESOLVED | SUPPORTED | CONTRADICTED | CONFIRMED | REFUTED"
}
```

## 示例

```json
{
  "factor_id": "factor_mu_hbm4_customer_qualification",
  "condition": "Micron产品通过Vera Rubin平台的正式客户验证",
  "structural_role": "REQUIRED",
  "impact": "通过验证只确认进入供应体系，不自动证明Micron能够获得有意义的订单份额。",
  "basis_refs": [
    {
      "object_type": "EVENT",
      "object_id": "cluster_vera_rubin_qualification"
    }
  ],
  "observability": {
    "match_condition": "NVIDIA、Micron或高可信供应链来源明确确认Micron的验证结果。"
  },
  "factor_state": "UNRESOLVED"
}
```

## 字段评估

| 字段                | 作用                | 必要性 | 生产能力                 |
| ----------------- | ----------------- | --- | -------------------- |
| `factor_id`       | 让事件和 Gap 持续引用同一因素 | 必须  | 系统生成                 |
| `condition`       | 定义可观察的兑现条件或阻断点    | 必须  | 领域 Agent 能形成         |
| `structural_role` | 区分必要条件、阻断点和修正因素   | 必须  | 领域 Agent 能判断         |
| `impact`          | 限制因素的影响边界，防止过度外推  | 必须  | 领域 Agent 能解释         |
| `basis_refs`      | 防止凭空生成路径故事        | 必须  | D1/Event/Evidence 支持 |
| `observability`   | 让事件监测知道如何识别该因素变化  | 必须  | 事件库和 Agent 可完成       |
| `factor_state`    | 给下游提供可直接读取的当前状态   | 必须  | 由事件更新物化              |

不保留主观概率、materiality 分数、长篇 current status 或预计日期。时间若重要，应独立成为 State Parameter。

---

# 八、Potential Gap

## 定义

Potential Gap 表示：

> 基于当前 State 和 Realization Factors 预先定义的条件性预期修正：若某类未来事件造成指定状态变化，该变化可能超出事件前市场锚点。

Potential Gap 不是当前公开数据之间的静态差异。

## Schema

```json
{
  "gap_id": "string",
  "basis_refs": ["ObjectRef"],
  "activation_rules": ["GapRule"],
  "market_anchor_slots": ["StateSlotRef"],
  "invalidation_rules": ["GapRule"],
  "lifecycle": "OPEN | CONSUMED | INVALIDATED | EXPIRED"
}
```

## StateSlotRef

```json
{
  "parameter_id": "string",
  "source_role": "ACTUAL | MANAGEMENT | SELL_SIDE | INDUSTRY_CHAIN | MARKET_IMPLIED",
  "time_scope": "string"
}
```

State Slot 指向：

> 某参数、某来源角色、某时间范围下，在事件发生前最后有效的 State Value。

使用 Slot 而不是固定 State Value ID，可以避免卖方或市场锚点更新后 Gap 继续引用过期值。

## GapRule

```json
{
  "event_condition": "string",
  "update_match": "ANY | ALL",
  "expected_updates": ["ExpectedUpdateSpec"]
}
```

## ExpectedUpdateSpec

```json
{
  "target_type": "STATE_SLOT | REALIZATION_FACTOR",
  "target": {},
  "operation": "SET_TO | ABOVE | BELOW | EXTEND_TO | SHORTEN_TO | ADVANCE_TO | DELAY_TO | SUPPORT | CONTRADICT | CONFIRM | REFUTE",
  "reference_refs": ["ObjectRef"],
  "target_value": {}
}
```

规则：

* `STATE_SLOT`：`target` 使用 `StateSlotRef`；
* `REALIZATION_FACTOR`：`target` 使用 `{ "factor_id": "..." }`；
* `reference_refs` 和 `target_value` 按实际场景选择；
* 不要求两者同时存在。

## 示例

```json
{
  "gap_id": "gap_mu_hbm4_share_upside",
  "basis_refs": [
    {
      "object_type": "STATE_VALUE",
      "object_id": "sv_mu_hbm4_share_sellside_q3"
    },
    {
      "object_type": "EVENT",
      "object_id": "factor_mu_hbm4_customer_qualification"
    }
  ],
  "activation_rules": [
    {
      "event_condition": "官方或高可信来源确认Micron获得显著高于事件前卖方预期区间的Vera Rubin订单份额。",
      "update_match": "ALL",
      "expected_updates": [
        {
          "target_type": "STATE_SLOT",
          "target": {
            "parameter_id": "param_mu_hbm4_share",
            "source_role": "ACTUAL",
            "time_scope": "Vera Rubin首轮订单分配"
          },
          "operation": "ABOVE",
          "reference_refs": [],
          "target_value": {}
        }
      ]
    }
  ],
  "market_anchor_slots": [
    {
      "parameter_id": "param_mu_hbm4_share",
      "source_role": "SELL_SIDE",
      "time_scope": "Vera Rubin首轮订单分配"
    },
    {
      "parameter_id": "param_mu_hbm4_share",
      "source_role": "MARKET_IMPLIED",
      "time_scope": "Vera Rubin首轮订单分配"
    }
  ],
  "invalidation_rules": [
    {
      "event_condition": "官方确认Micron未进入供应体系，或实际订单份额低于事件前卖方区间。",
      "update_match": "ANY",
      "expected_updates": [
        {
          "target_type": "REALIZATION_FACTOR",
          "target": {
            "factor_id": "factor_mu_hbm4_customer_qualification"
          },
          "operation": "REFUTE",
          "reference_refs": [],
          "target_value": {}
        }
      ]
    }
  ],
  "lifecycle": "OPEN"
}
```

## 字段评估

| 字段                    | 作用                             | 必要性  | 生产能力              |
| --------------------- | ------------------------------ | ---- | ----------------- |
| `gap_id`              | 支持持续监测、激活和历史审计                 | 必须   | 系统生成              |
| `basis_refs`          | 证明 Gap 来自当前 State 或兑现研究，而非凭空叙事 | 必须   | O1 能选择依据          |
| `activation_rules`    | 将未来事件转换为明确的状态修正条件              | 必须   | 领域 Agent 提议，O1 规范 |
| `market_anchor_slots` | 定位事件前市场已定价的参考状态                | 条件必需 | C4/O4 与卖方数据可贡献    |
| `invalidation_rules`  | 提供反向证伪条件，抑制单向叙事                | 必须   | 领域 Agent 能提出      |
| `lifecycle`           | 管理 Gap 的持续运行状态                 | 必须   | Runtime 更新        |

若 `market_anchor_slots` 为空，Gap 可以监测和 Escalate，但不得直接进入 DTC。

---

# 九、Expectation Update

## 定义

Expectation Update 是事件转化为结构化预期修正的记录。它同时承担 Event → Signal → State/Factor 的桥梁作用，不再额外建立 Signal 对象。

## Schema

```json
{
  "update_id": "string",
  "event_id": "string",
  "target_type": "STATE_PARAMETER | REALIZATION_FACTOR",
  "target_id": "string",
  "update_mode": "STATE_REVISION | FACTOR_EVIDENCE",
  "payload": {}
}
```

## State Revision Payload

```json
{
  "new_state_value_id": "string",
  "superseded_state_value_ids": ["string"]
}
```

## Factor Evidence Payload

```json
{
  "effect": "SUPPORT | CONTRADICT | CONFIRM | REFUTE",
  "strength": "WEAK | MODERATE | STRONG",
  "resulting_factor_state": "UNRESOLVED | SUPPORTED | CONTRADICTED | CONFIRMED | REFUTED"
}
```

## 示例

```json
{
  "update_id": "update_evt_nvidia_mu_qualification",
  "event_id": "evt_nvidia_mu_qualification",
  "target_type": "STATE_PARAMETER",
  "target_id": "param_mu_hbm4_qualification",
  "update_mode": "STATE_REVISION",
  "payload": {
    "new_state_value_id": "sv_mu_hbm4_qualification_actual",
    "superseded_state_value_ids": []
  }
}
```

一个事件可以产生多条 Expectation Updates，并更新多个 Units。

---

# 十、Gap Activation

## 定义

Gap Activation 表示：

> 事件已造成实际 State/Factor 更新，系统据此计算事件前市场锚点与更新后预期之间的差距，并判断其中多少尚未被市场吸收。

## Schema

```json
{
  "activation_id": "string",
  "gap_id": "string",
  "trigger_event_ids": ["string"],
  "update_ids": ["string"],
  "result": "ACTIVATED | NOT_ACTIVATED | INVALIDATED",
  "gap_delta": ["GapDeltaItem"],
  "market_absorption": {
    "state": "UNKNOWN | NOT_PRICED | PARTIALLY_PRICED | LARGELY_PRICED | OVER_PRICED",
    "anchor_snapshot_refs": ["ObjectRef"],
    "residual_delta": ["GapDeltaItem"],
    "basis_refs": ["ObjectRef"]
  },
  "as_of": "ISO-8601 datetime"
}
```

## GapDeltaItem

```json
{
  "target_type": "STATE_SLOT | REALIZATION_FACTOR",
  "target": {},
  "before": {},
  "after": {},
  "delta": {}
}
```

## Delta 示例

### 数值差

```json
{
  "kind": "NUMBER",
  "amount": 4.0,
  "unit": "PERCENTAGE_POINT"
}
```

### 区间差

```json
{
  "kind": "RANGE",
  "lower_delta": 5.0,
  "upper_delta": 10.0,
  "unit": "PERCENTAGE_POINT"
}
```

### 时间差

```json
{
  "kind": "TIME",
  "direction": "EXTENDED",
  "amount": 4,
  "unit": "QUARTER"
}
```

### 阶段差

```json
{
  "kind": "STAGE",
  "from": "VALIDATING",
  "to": "QUALIFIED"
}
```

### 证据状态差

```json
{
  "kind": "EVIDENCE",
  "from": "CONTESTED",
  "to": "OFFICIALLY_CONFIRMED"
}
```

## 示例

```json
{
  "activation_id": "activation_mu_hbm4_share_001",
  "gap_id": "gap_mu_hbm4_share_upside",
  "trigger_event_ids": [
    "evt_official_order_allocation"
  ],
  "update_ids": [
    "update_evt_order_share_actual"
  ],
  "result": "ACTIVATED",
  "gap_delta": [
    {
      "target_type": "STATE_SLOT",
      "target": {
        "parameter_id": "param_mu_hbm4_share",
        "source_role": "ACTUAL",
        "time_scope": "Vera Rubin首轮订单分配"
      },
      "before": {
        "lower": 5.0,
        "upper": 10.0
      },
      "after": {
        "lower": 18.0,
        "upper": 20.0
      },
      "delta": {
        "kind": "RANGE",
        "lower_delta": 8.0,
        "upper_delta": 15.0,
        "unit": "PERCENTAGE_POINT"
      }
    }
  ],
  "market_absorption": {
    "state": "PARTIALLY_PRICED",
    "anchor_snapshot_refs": [
      {
        "object_type": "STATE_VALUE",
        "object_id": "sv_mu_hbm4_share_sellside_pre_event"
      }
    ],
    "residual_delta": [
      {
        "target_type": "STATE_SLOT",
        "target": {
          "parameter_id": "param_mu_hbm4_share",
          "source_role": "ACTUAL",
          "time_scope": "Vera Rubin首轮订单分配"
        },
        "before": {
          "lower": 12.0,
          "upper": 15.0
        },
        "after": {
          "lower": 18.0,
          "upper": 20.0
        },
        "delta": {
          "kind": "RANGE",
          "lower_delta": 3.0,
          "upper_delta": 8.0,
          "unit": "PERCENTAGE_POINT"
        }
      }
    ],
    "basis_refs": [
      {
        "object_type": "METRIC",
        "object_id": "mu_pre_event_market_snapshot"
      },
      {
        "object_type": "ANALYST_ESTIMATE",
        "object_id": "mu_sellside_revision_snapshot"
      }
    ]
  },
  "as_of": "2026-09-10T14:30:00Z"
}
```

---

# 十一、核心验证规则

## Shell

1. 必须包含两个及以上具有共享上下文的候选 Units，否则不需要 Shell。
2. `separation_test` 必须能够真实排除至少一类相邻预期。
3. 不得按“同一公司”或“同一行业”作为唯一分组理由。

## Unit

1. Proposition 必须是单一中层命题。
2. 必须有明确 Horizon。
3. 至少拥有一个 State Parameter。
4. 至少拥有一个可监测的 Potential Gap 或 Realization Factor。
5. 一个 Unit 的失败不能自动等于整个 Shell 失败。

## State Parameter

1. 必须可能被未来事件更新。
2. 必须能参与 Potential Gap 的形成或激活。
3. 仅作为背景展示的数据不得进入。

## State Value

1. 必须有 `basis_refs`。
2. 无可靠状态时不生成，不使用自由文本 UNKNOWN 占位。
3. 新硬事实出现后，旧状态必须被标记为 `SUPERSEDED`、`DISPUTED` 或 `RETRACTED`。
4. 不允许用整体行情替代某个具体参数的市场隐含状态。

## Realization Factor

1. 必须显著影响 Unit。
2. 必须是当前 State 无法充分表达的因素。
3. 必须具有可观察事件接口。
4. 必须有明确影响边界。
5. 不得保存泛泛经营常识。

## Potential Gap

1. 必须引用 State 或 Factor 依据。
2. 必须包含可监测的 Activation Rule。
3. 必须明确事件预期修改哪个 Slot 或 Factor。
4. 必须有 Invalidation Rule。
5. 没有 Market Anchor 时不得直接进入 DTC。
6. 每个 Unit 建议只保留 1—4 个 OPEN Gaps。

## Gap Activation

1. 必须有真实 Event 和 Expectation Updates。
2. 必须生成结构化 `gap_delta`。
3. 必须区分总 Gap 与尚未定价的 `residual_delta`。
4. `UNKNOWN` 的 Market Absorption 不允许被下游 Agent解释为未定价。
5. 事后价格反应只用于吸收评估和审计，不是激活 Gap 的前置条件。

---

# 十二、数量限制

每个 Unit 的初始化建议限制为：

```text
State Parameters：3—8 个
每个 Parameter 每个 source_role 每个 time_scope：最多 1 个 CURRENT Value
Realization Factors：3—6 个
OPEN Potential Gaps：1—4 个
```

超出限制时优先删除：

* 普通背景指标；
* 无法被事件更新的参数；
* 无法观察的 Factors；
* 没有明确 expected update 的 Gaps；
* 彼此重复的 Gap 触发条件。

---

# 十三、最终核心字段清单

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
- transmission_layer
- value_type
- unit（条件字段）
- ordered_states（条件字段）

StateValue
- state_value_id
- parameter_id
- source_role
- value
- time_scope
- as_of
- basis_refs
- validity_state

RealizationFactor
- factor_id
- condition
- structural_role
- impact
- basis_refs
- observability
- factor_state

PotentialGap
- gap_id
- basis_refs
- activation_rules
- market_anchor_slots
- invalidation_rules
- lifecycle

ExpectationUpdate
- update_id
- event_id
- target_type
- target_id
- update_mode
- payload

GapActivation
- activation_id
- gap_id
- trigger_event_ids
- update_ids
- result
- gap_delta
- market_absorption
- as_of
```

这套 Schema 的最终业务输出不是“该事件利好或利空”，而是：

> 哪个事件将哪个预期状态从什么位置修改到什么位置，相对事件前市场锚点形成了多大差距，其中还有多少尚未被市场吸收。
