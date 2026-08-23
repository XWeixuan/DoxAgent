# Document2 Expectation Research Workflow v2

## 一、目标与边界

本方案从 **Shell Construction 已经完成并冻结 Shell / Unit Seed** 之后开始，不重复定义 Shell 如何产生。

输入是一组已经完成结构审查的 Expectation Shell。每个 Shell 已至少包含：

```text
shell_id
core_question
boundary_rule

units:
- expectation_id
- proposition
- horizon
```

Document2 Research Workflow 的职责是，在这些稳定的 Shell / Unit 边界内进一步完成：

```text
Expectation State
+
Realization Factors
+
Potential Gaps
```

最终形成完整 Document2。

Document2 本身不是一份新的综合研究报告，而是一套能够被后续事件持续读取和修改的**有状态预期认知结构**。当前 Schema 已经明确区分：

* State：当前能够稳定定义、观察和持续维护的预期状态；
* Realization Factor：当前研究能够识别、但不适合强行参数化的现实兑现机制；
* Potential Gap：基于完整当前情境向未来进一步发散得到的单点 possible occurrence。

因此整个 Research Workflow 的核心不是：

> 根据 D1 给每个 Schema 字段找一段文字填进去。

而是：

> **由一个拥有完整研究能力的 O1，在 Shell 边界内建立一套可以真正支持后续事件驱动预期更新的认知模型。**

---

# 二、基本执行单元：一个 Shell，一个 O1

Shell Construction 完成后，Runtime 为每一个 Shell 创建独立 Workspace，并启动一个唯一的 O1 Thread。

```text
Shell A
→ Workspace A
→ O1 Thread A

Shell B
→ Workspace B
→ O1 Thread B

Shell C
→ Workspace C
→ O1 Thread C
```

不同 Shell 可以并行执行。

同一个 Shell 内的所有 Expectation Units 始终由同一个 O1 Thread 负责。

原因是 Shell 本身已经被定义为：

> 一组必须共享完整研究上下文、但内部 Unit 可以独立更新的预期边界。

如果再把每个 Unit 分配给不同 Thread，会重新破坏 Shell Construction 已经建立的上下文耦合边界。

因此 O1 的定位是：

> **Shell Research Owner**

而不是某一个 Unit 的填表 Agent。

---

# 三、O1 的角色定位

O1 是一个完整的研究型 Agent。

它需要同时具备：

* 阅读完整 Global Research / Document1 的能力；
* 阅读相关数据库和已发布结构化信息的能力；
* 调用 Data MCP 大部分允许数据源的能力；
* Web Search；
* 阅读 Future Nodes、Event Library 等辅助上下文；
* 对新的研究问题主动检索、比较和推理；
* 直接维护当前 Shell 的 Document2 对象。

O1 的研究目标是：

> **把已经确定的 Expectation Units 研究到足以建立高质量 State、Realization Model 和 Potential Gap Set。**

它不是：

> 把 D1 的研究内容重新做一遍。

也不是：

> 对 D1 逐项进行事实审计。

---

# 四、Document1 是 Trusted Research Prior

O1 对已经发布的 Document1 应采用：

> **默认置信、按需扩展。**

而不是：

> 默认怀疑、逐项重新核验。

当前 Global Research 已经通过 C1、C3、C5 等研究节点形成明确发布产物和 citation boundary；D2 应消费这些已发布研究，而不是把上游研究重新执行一遍。

同时必须继续区分 Global Research handoff 与独立 Market Situation handoff，不把不同 lane 的历史角色和数据静默混合。

---

## O1 可以继续研究的主要情况

### 1. D1 明确存在 Unknown / Conflict

例如：

> Micron Vera Rubin HBM4 资格存在冲突。

O1 为了建立 State 或 Factor，可以进一步研究。

---

### 2. Expectation Detail 要求的粒度明显高于 D1

例如 D1 只得出：

> HBM4 竞争激烈。

但某个 Unit 是：

> Micron 能否获得足以形成实质财务贡献的 Vera Rubin HBM4 订单份额。

O1 就需要进一步研究：

* 客户资格；
* 订单分配；
* 当前卖方份额假设；
* 竞争者量产；
* Micron交付能力。

这是 **Research Extension**，不是 D1 Audit。

---

### 3. 为建立 State / Factor / Gap 缺少必要信息

例如已有 D1 足以证明行业短缺，但没有：

> 当前卖方预计什么时候恢复供需平衡。

如果这个 Value 对 Unit 很重要，O1 可以补充研究。

---

### 4. 研究过程中自然发现新的冲突或更晚信息

此时 O1 可以继续调查该具体问题。

---

## 不应该发生的行为

O1 不应形成：

```text
D1说收入是多少
→ Data MCP重新查一次

D1说毛利率是多少
→ 再查一次

D1说行业供给紧张
→ 再逐个来源核一次

D1引用了管理层
→ 再找原文确认一次
```

这样的逐项审计流程。

O1 的 Tool 使用必须由：

> **当前 Expectation Research 中实际存在的信息需要**

驱动，而不是由：

> “D1 的每句话都重新验证”

驱动。

---

# 五、Shell Seed 的修改权限

O1 可以对 Shell Construction 已经形成的部分字段进行**语义精化**。

例如：

* `core_question`
* `boundary_rule`
* `proposition`
* `horizon`

研究深入以后，原始表述可能过宽、时间范围可能不够准确，因此允许调整。

例如：

```text
原：
Micron能否获得有意义的HBM4份额

研究后：
Micron能否在Vera Rubin首轮量产与订单分配周期内获得足以形成实质财务贡献的HBM4订单份额
```

属于合理 refinement。

但 O1 不拥有重新设计 Shell Structure 的权限。

原则是：

```text
允许：
semantic refinement

不允许：
semantic restructuring
```

因此 O1 不应自行：

* 新增或删除 Unit；
* Merge / Split Unit；
* 将 Unit 改成另一个问题；
* 将 Shell 改为另一套价值传导系统；
* 大幅改变 `core_question` 的投资问题。

如果研究真正暴露出结构性错误，应形成一个简短的 Structure Exception，交回 O0 处理，而不是由 O1 静默修改。

---

# 六、为什么 Research Workflow 应拆成多个 Turns

完整 Document2 中的三个核心板块具有完全不同的认知任务。

```text
Expectation State
= 当前稳定认知的收敛

Realization Model
= 现实兑现机制的分析

Potential Gap
= 对未来认知变化的受约束发散
```

当前设计也明确要求 State Parameter 是稳定、可持续获得新 Value 的变量；Realization Factor 则用于那些无法可靠参数化、但现实中显著影响兑现的机制。

如果在一次 Request 中连续完成所有对象，很容易形成：

```text
刚生成 Parameter A
→ 马上生成 Gap A

刚生成 Factor B
→ 马上生成 Gap B
```

而当前 Potential Gap 的设计恰恰明确反对 State / Factor 与 Gap 的一一机械生成。

因此首版建议：

> **一个持续 O1 Thread，三个正式 Research Turns，加一个轻量 Finalization Turn。**

---

# 七、整体 Research Workflow

```text
Final Shell Seed
        ↓
Create Shell Workspace
        ↓
Create Unique O1 Thread
        ↓
Research Turn 1
Expectation State Research
        ↓
Checkpoint / Snapshot
        ↓
Research Turn 2
Realization Model Research
        ↓
Checkpoint / Snapshot
        ↓
Research Turn 3
Potential Gap Discovery
        ↓
Checkpoint / Snapshot
        ↓
Finalization Turn
        ↓
Published Shell
```

三个 Research Turns 是不同的 cognitive phase，但不是彼此完全封闭的流水线。

后面的研究可以发现前面遗漏的问题，因此允许**局部回补**。

例如：

```text
Turn 2发现某个Factor其实已经可以稳定参数化
→ 回补State Parameter / Value

Turn 3发现某个Gap缺少关键现实机制
→ 回补Realization Factor

Turn 3发现某个重要当前市场锚点缺失
→ 补充对应State
```

但不重新生成前一阶段的全部内容。

---

# 八、Research Turn 1：Expectation State Research

## 目标

回答：

> **对于这个 Shell 内的每个 Unit，截至当前时间，我们究竟能够稳定维护哪些预期状态？这些状态现在分别处于什么位置？**

这是整个 Workflow 中最偏**收敛**的一步。

---

## 核心 Cognitive Mode

> **Convergent Anchoring / State Normalization**

关键词是：

* 稳定；
* 明确；
* 可维护；
* 可比较；
* 可更新；
* 有现实来源。

O1 此时应克制开放世界发散。

主要任务不是：

> 还有什么可能影响这个 Unit？

而是：

> **现在有哪些东西已经足够明确，可以成为长期维护的 State Parameter？**

---

# 九、Turn 1 的具体研究范围

O1 按整个 Shell 统一研究，而不是机械地逐 Unit 完全独立工作。

对于每个 Unit：

### 1. 理解 Proposition 与 Horizon

确认：

* Unit 实际在判断什么；
* 时间窗口是什么；
* 与同 Shell 相邻 Unit 的边界是什么。

必要时进行轻微 semantic refinement。

---

### 2. 识别真正稳定的 State Parameters

Parameter 需要满足当前 Schema 的核心标准：

* 即使没有新事件也稳定存在；
* 不同时间或来源可以持续给出 Value；
* 新旧 Value 可以合理比较；
* 变化会实际影响 Unit。

O1 不应因为某项信息重要就把它 Parameter 化。

---

### 3. 建立当前 State Values

根据真实可获得信息建立：

* ACTUAL；
* MANAGEMENT；
* SELL_SIDE；
* INDUSTRY_CHAIN；
* MARKET_IMPLIED。

这些来源不是填表 checklist。

某 Parameter 如果没有可靠 MARKET_IMPLIED Value：

> 不生成。

如果没有可靠 Sell-side Value：

> 不生成。

不要求每个 Parameter 五类来源齐全。

---

### 4. 获取 Previous Value

存在：

* 同 Parameter；
* 同 source role；
* 可比 time scope；

的可靠历史 Value 时填写 `previous_value`。

Previous Value 的意义是判断：

> **预期本身是否已经发生了变化。**

当前 Schema 也明确要求只有同口径可比较历史值才使用 Previous Value。

---

### 5. 进行必要 Research Extension

如果 D1 已经足够：

> 直接使用。

如果缺少一个对 State 必不可少的具体信息：

> 调用 Data MCP / Web Search 补足。

此时的 Tool 使用应围绕：

> “我要完成这个 State，需要什么？”

而不是：

> “我要检查 D1 有没有错。”

---

# 十、Turn 1 的完成标准

Turn 1 不以：

> “所有能想到的数据都收集完”

为完成标准。

而以：

> **当前 Unit 已经拥有足够稳定的 State，使后续 Agent 能理解主要预期当前处于什么位置。**

为完成标准。

具体要求：

1. 每个保留的 Parameter 都满足稳定、可持续更新的条件；
2. Parameter 之间不存在明显重复或粒度混乱；
3. 当前存在的 State Value 都有 citation；
4. 有可比较历史值时合理填写 Previous Value；
5. 不为了 Schema 完整制造假 Value；
6. 不强迫每种 source role 都出现；
7. 重要但无法稳定 Parameter 化的问题被留给 Realization Research；
8. 剩余信息缺口已经不会阻止下一阶段理解 Unit 当前状态。

此时即完成。

---

# 十一、Research Turn 2：Realization Model Research

## 目标

回答：

> **在这些当前 State 之外，现实世界中还有什么机制决定这个 Unit 能不能兑现、什么时候兑现、兑现到谁、以及兑现强度如何？**

这是整个 Workflow 最偏**现实机制分析**的一步。

---

## 核心 Cognitive Mode

> **Mechanism Decomposition / Constraint Reasoning**

O1 此时不是继续找更多指标。

它开始研究：

* 必要条件；
* 执行约束；
* 阻断因素；
* 技术与运营里程碑；
* 外部依赖；
* 受益分配；
* 转化效率。

重点是：

> **现实为什么会这样发展。**

而不是：

> 市场现在给它多少概率。

---

# 十二、Turn 2 的具体研究范围

对于每个 Unit，O1 基于：

```text
Proposition
+
当前 State
+
Document1
+
必要的新研究
```

研究关键 Realization Factors。

需要从现实业务角度考虑：

### 预期兑现需要哪些重要条件成立？

例如 HBM4 份额：

* 客户资格；
* 量产能力；
* 良率；
* 订单；
* 交付能力。

---

### 预期可能被什么现实机制阻断？

例如：

* 客户验证失败；
* 平台延期；
* 竞争者提前锁定份额；
* 制造问题；
* 监管决定。

---

### 哪些因素不直接决定成败，但改变结果？

例如：

* 同样获得订单，但实际份额不同；
* 收入兑现但 FCF 转化很差；
* 行业景气成立但主要受益对象发生变化。

---

# 十三、Factor 与 State 的再判断

Turn 2 是第一次允许 O1 系统性回头审视 State。

如果研究过程中发现：

> 某个所谓 Factor 实际已经能够稳定表示成数值、区间、时间、阶段或方向；

则应考虑转为 State Parameter。

反过来，如果 Turn 1 某个 Parameter 实际无法被稳定维护，只能依靠复杂上下文才能解释：

> 应重新考虑是否更适合作为 Factor。

当前设计本身就把这种边界定义为：

> State = 稳定可参数化；Factor = 重要但必须保留现实语义。

---

# 十四、Factor 的研究要求

每个最终保留的 Factor 应能够形成完整：

```text
factor_id
condition
structural_role
current_status
impact
citation
observability
```

其中最重要的是三个部分。

### Current Status

不是：

> PARTIAL / CONFIRMED

而是：

> 截至现在，这个现实条件实际发展到什么程度。

需要保留具体业务语义。

---

### Impact

必须说明：

> 这个 Factor 能合理影响 Unit 到什么程度，又不能外推到哪里。

例如：

> 客户认证证明进入供应体系，但不证明一定获得高订单份额。

---

### Observability

必须存在现实未来信息接口。

如果一个 Factor 无法通过任何未来信息：

* 验证；
* 反驳；
* 更新；

则它通常不适合进入事件驱动系统。

---

# 十五、Turn 2 的完成标准

Turn 2 完成并不意味着：

> 列出了所有可能影响公司的因素。

而是：

> **已经形成一套足以解释该 Unit 主要兑现路径、主要失败路径和关键结果修正机制的 Realization Model。**

具体要求：

1. Factor 与 State 的边界基本清楚；
2. 保留的 Factor 都对 Unit 有实质影响；
3. `current_status` 能真实描述当前现实状态；
4. Factor 有 citation；
5. Factor 有明确影响边界；
6. Factor 具有未来 observability；
7. 不保留泛泛经营常识；
8. 不为了覆盖 `REQUIRED / BLOCKER / MODIFIER` 而机械各生成一个；
9. 继续增加新 Factor 已主要产生重复、低影响或无法观察的内容。

满足这些条件即可停止。

---

# 十六、Research Turn 3：Potential Gap Discovery

## 目标

回答：

> **基于我们现在完整理解的 State、Realization Factors 和研究背景，未来具体发生哪些单点事情，会迫使当前这些 Expectation Units 被实质重新判断？**

这是整个 Workflow 中最偏**未来发散**的一步。

---

## 核心 Cognitive Mode

> **Constrained Divergence → Future Occurrence Synthesis**

前两个 Turns 的目标是：

> 把当前世界理解清楚。

Turn 3 的目标是：

> **在这个现实基础上主动寻找未来认知断点。**

所以它不能继续采用：

> 一项一项填 Schema

的思维方式。

---

# 十七、Turn 3 应从整个 Unit 出发，而不是遍历对象

O1 不应：

```text
Parameter 1
→ 想一个 Gap

Parameter 2
→ 想一个 Gap

Factor 1
→ 想一个 Gap

Factor 2
→ 想一个 Gap
```

当前 Potential Gap 设计已经明确要求：

> State 和 Factor 是重要出发点，但不能成为 Gap Discovery 的边界。

正确的问题应该是：

> **如果未来世界发生什么具体事情，我会被迫重新判断这个 Unit？**

O1 应同时利用：

* State；
* Factors；
* D1；
* 自己补充的 Research；
* Future Nodes；
* Event Library；
* 行业机制；
* 技术路线；
* 产品规律；
* 竞争关系；
* 多因素组合。

---

# 十八、Turn 3 的内部思考过程

Turn 3 可以在同一个 Request 内经历两个认知阶段。

## Phase A：Divergence

先尽量发现具有现实基础的高价值 future possibilities。

可以考虑：

* 当前趋势继续走得更远；
* 当前趋势突然反转；
* 一个关键条件第一次被真实验证；
* 一个重大 blocker 出现；
* 时间大幅提前或延后；
* 受益者重新分配；
* 上游逻辑成功但下游传导失败；
* 当前 State / Factor 没有显式覆盖的新变量出现。

这些只是**发散角度**，不是 Gap 类型或 Schema 枚举。

---

## Phase B：Synthesis

然后统一收敛：

* 删除低影响 possibility；
* 删除纯猜想；
* 合并真正重复的 occurrence；
* 拆开复合事件；
* 检查不同兑现和失败路径是否存在明显认知盲区；
* 形成最终 Potential Gap Set。

---

# 十九、每个 Gap 的研究标准

最终 Potential Gap 必须回答：

### Possible Occurrence

未来具体可能发生什么。

必须能够让未来 Runtime 判断：

> 发生 / 没发生。

---

### Derivation

为什么当前研究状态使这个 future possibility 值得提前考虑。

不是解释影响，而是证明：

> 这不是无依据脑补。

---

### Citation

支持推演出发点的现实研究依据。

Citation 可以来自：

* State；
* Factor；
* D1；
* Event；
* 技术或行业研究。

但不把 Gap 强绑定到某个 State / Factor。

---

### Expected Revision

如果 occurrence 发生：

> 当前 Unit 会怎样被重新判断？

同时说明合理外推边界。

---

### Recognition Criteria

只有 occurrence 本身存在：

* 显著；
* 有意义；
* 大规模；
* 放缓；
* 超预期；

等模糊判断时才使用。

当前 Gap Schema 与准入标准已经明确要求其必须是具体、单点、可观察、能够实质改变 Unit 的 future occurrence。

---

# 二十、Turn 3 的完成标准

Potential Gap 不设固定数量。

不存在：

```text
每个Unit至少4个Gap
每个Factor对应一个Gap
必须两个正面两个负面
```

这种要求。

Turn 3 完成的标准是：

1. 每个 Gap 都是单点、未来、可观察 occurrence；
2. 每个 Gap 发生后都足以实质修改当前 Unit；
3. 每个 Gap 都有现实研究基础；
4. Derivation 与 Expected Revision 清楚区分；
5. 复合事件已经合理拆分；
6. 没有把当前已发生事实写成 Potential Gap；
7. 没有机械复制 State / Factor；
8. 没有明显只研究兑现路径而完全忽略重要失败或替代路径；
9. 继续发散主要只能产生低影响、重复、不可观察或高度投机的 possibility。

达到这一状态即可停止。

---

# 二十一、Finalization Turn

Finalization 不是新的 Research Turn。

它的 Cognitive Mode 是：

> **Curation / Consistency / Schema Closure**

此时默认不再大规模搜索新信息。

---

## Finalization 的主要任务

### 1. Object Boundary Check

检查：

```text
State
vs
Factor
vs
Gap
```

有没有对象放错层级。

---

### 2. Duplicate Check

检查：

* 重复 Parameter；
* 重复 Factor；
* 重复 Gap；
* 相邻 Unit 是否存在不合理重复。

---

### 3. Semantic Consistency

检查：

* Proposition；
* Horizon；
* State；
* Factor；
* Gap；

是否仍然围绕同一个 Unit。

---

### 4. Citation Closure

检查：

* State Value citation；
* Factor citation；
* Gap citation；

是否完整。

---

### 5. Previous Value Consistency

确认 previous/current 真正可比。

---

### 6. Shell Seed Refinement

如研究后需要，对：

* `core_question`
* `boundary_rule`
* `proposition`
* `horizon`

做最后轻微措辞精化。

不得借此重新设计 Shell Structure。

---

# 二十二、Finalization 完成标准

最终 Shell 必须完全满足当前 Document2 Schema：

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
```

以及其下完整：

* StateParameter；
* StateValue；
* RealizationFactor；
* PotentialGap。

同时：

* ID 使用自然语言语义名称；
* 不存在 Schema placeholder；
* 不为了完整度生成无依据对象；
* 不存在未处理的明显语义冲突；
* 不存在未经允许的 Shell / Unit 结构变化。

满足后 Shell 进入 Published 状态。

---

# 二十三、各 Turn 的 Cognitive Mode 汇总

| 阶段                   | 核心问题           | Cognitive Mode          | 错误偏好                   |
| -------------------- | -------------- | ----------------------- | ---------------------- |
| State Research       | 现在能够稳定维护什么？    | Convergent Anchoring    | 宁可少而可靠，不制造假状态          |
| Realization Research | 现实中什么决定它兑现？    | Mechanism Decomposition | 宁可保留复杂真实语义，不强行参数化      |
| Gap Discovery        | 未来什么会迫使我们重新判断？ | Constrained Divergence  | 宁可探索不同高价值可能性，不机械映射现有对象 |
| Finalization         | 整套认知结构是否闭合？    | Curation / Consistency  | 删除重复与错位，不再自由扩张         |

三个 Research Turns 并不是 Schema 填写顺序，而是：

```text
Current Expectation
↓
Reality Mechanism
↓
Future Expectation Revision
```

这一认知顺序正好对应 Document2 当前的核心模型。

---

# 二十四、Research Stopping Rule

O1 的研究目标不是：

> 把 Shell 涉及的全部行业知识研究完。

停止研究的总体标准是：

> **继续增加研究已经不会实质改善 State、Realization Factor 或 Potential Gap 的质量。**

例如：

已经足以形成高质量 HBM 供需状态，就没有必要为了“完整”继续研究十年前 DRAM 周期。

已经能够解释一个 Factor 的 condition、current_status、impact 和 observability，就无需把相关技术领域写成完整技术报告。

已经形成足够有差异性的 Potential Gap Set，就无需无限搜索尾部 scenario。

Document2 的研究深度由：

> **对象质量**

决定，而不是：

> **报告篇幅**

决定。

---

# 二十五、Workspace 与业务状态载体

每个 Shell Workspace 使用一个 Canonical Working Object：

```text
shell.json
```

它从 Shell Construction 完成时就已经存在。

初始：

```text
Shell
├── core_question
├── boundary_rule
└── units
    ├── proposition
    ├── horizon
    ├── state: empty
    ├── realization_factors: empty
    └── potential_gaps: empty
```

后续三个 Turns 始终修改同一个业务对象。

---

# 二十六、每个 Turn 的持久化

每个成功 Turn 完成后：

```text
Working shell.json
↓
Schema / stage validation
↓
Immutable Attempt Snapshot
↓
Checkpoint
↓
Next Turn
```

因此逻辑上存在：

```text
Shell Seed
↓
State-complete Snapshot
↓
Realization-complete Snapshot
↓
Gap-complete Snapshot
↓
Published Snapshot
```

但 Workspace 中无需人为维护：

```text
shell_v1.json
shell_v2.json
shell_final_final.json
```

历史由 Runtime Artifact / Attempt / Checkpoint 层负责。

---

# 二十七、Intermediate Validation

中间阶段不要求完整 Document2 Schema 全部完成。

使用同一个 Canonical Schema，但采用不同完成度要求。

### Turn 1 后

必须合法：

```text
Shell / Unit Definition
State
```

允许：

```text
realization_factors = []
potential_gaps = []
```

### Turn 2 后

必须合法：

```text
Shell / Unit Definition
State
Realization Factors
```

允许：

```text
potential_gaps = []
```

### Turn 3 后

完整对象全部形成。

这样避免为了中间 Schema Validation 强迫 Agent 提前生成还没有真正研究的对象。

---

# 二十九、最终 Document2 的持久化结构

所有 Shell 完成后：

```text
Shell A final JSON
Shell B final JSON
Shell C final JSON
        ↓
Deterministic Assembly
        ↓
document2.json
        ↓
Optional Renderer
        ↓
document2.md
```

其中：

> **JSON 是业务状态 Source of Truth。**

Markdown 只作为：

> 人类阅读和调试视图。

因为 Runtime 后续真正需要持续更新的是：

* State Value；
* Factor current_status；
* Potential Gap；

而不是一篇 Markdown 报告。

---

# 三十二、并行、失败与恢复

不同 Shell：

> 可以并行。

同一个 Shell 内：

> Research Turns 顺序执行。

例如：

```text
S1: State → Factor → Gap → Finalize
S2: State → Factor → Gap → Finalize
S3: State → Factor → Gap → Finalize
```

一个 Shell 失败：

> 不影响其他 Shell。

每个成功 Turn 都有 Snapshot，因此失败重试只需要从最近成功阶段继续。

唯一 O1 Thread 在正常路径中持续复用，以保持完整 Shell Research Context。

如果具体 attempt 失败到必须 fresh thread 重试，则通过：

* canonical shell.json；
* 已发布 D1；
* research artifacts；
* checkpoint；

恢复，而不把隐含 thread memory 当成唯一状态。

---

# 三十三、最终 Workflow

```text
O0 Shell Construction
        ↓
Final Shell Seeds
        ↓
─────────────────────────────────

For Each Shell

Create Independent Workspace
        ↓
Create Unique O1 Thread
        ↓
Load:
- Final Shell Seed
- Global Research / Document1
- allowed published context
- Future Nodes
- Event Library
- Data MCP
- Web Search
        ↓
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Research Turn 1
EXPECTATION STATE
Convergent Anchoring
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        ↓
State Snapshot
        ↓
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Research Turn 2
REALIZATION MODEL
Mechanism Decomposition
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        ↓
Realization Snapshot
        ↓
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Research Turn 3
POTENTIAL GAP DISCOVERY
Constrained Divergence
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        ↓
Gap Snapshot
        ↓
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Finalization
Curation / Consistency
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        ↓
Published Shell JSON

─────────────────────────────────
        ↓
Deterministic Assembly
        ↓
Document2 JSON
        ↓
Human-readable View
```
