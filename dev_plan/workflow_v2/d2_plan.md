# Expectation Shell / Unit 重构方案

本方案沿用原方案已经确定的核心结构：一个 Expectation Shell 内包含多个 Expectation Units，每个 Unit 由 Unit Definition、Expectation State、Expectation Realization Model 和 Potential Expectation Gap Set 四部分构成。

本轮重点修正三个问题：

1. Expectation State 不再通过大量固定分类字段描述，而是围绕少量稳定 State Parameter 与持续更新的 State Value 管理；
2. Realization Factor 不再只是静态“兑现条件”，而需要维护当前情境下的真实语义状态；
3. Potential Gap 不再被理解为 State 或 Factor 的机械派生项，而是一张基于当前完整研究情境进一步发散形成的**未来单点可能性地图**。

---

# 一、系统整体结构

```text
Expectation Shell
有边界的共享研究上下文

        ↓

Expectation Unit
市场实际讨论和交易的中层预期命题

        ↓

┌───────────────────────────────┐
│                               │
Expectation State        Expectation Realization Model
当前可稳定维护的状态       当前无法稳定参数化的兑现机制
│                               │
└──────────────┬────────────────┘
               ↓
      Potential Gap Set
受当前情境约束的未来单点可能性地图

               ↓

          Future Event
               ↓
Codex Agent 基于最新 Blackboard 判断：
- 是否修改 State Value
- 是否改写 Realization Factor current_status
- 是否命中某个 Potential Gap
- 是否产生有交易意义的新预期差
               ↓
        Trading Evaluation
```

这里最重要的区分是：

> **State 与 Factor 描述“现在”；Potential Gap 描述“未来可能发生什么”。**

State 和 Factor 是当前研究能够形成的认知状态；Gap 则是在此基础上继续向未来进行受约束发散。

---

# 二、Expectation Shell

## 1. Shell 的定位

Expectation Shell 是：

> **一组必须共享完整研究上下文，但在运行时仍能够独立更新的 Expectation Units 的边界。**

Shell 不是一个宽泛投资主题，也不是最终交易对象。

它存在的主要原因，是解决两个相反的问题：

* Unit 必须足够细，才能被具体事件独立更新；
* Agent 研究任务又不能被拆得过细，否则会失去完整业务和因果上下文。

因此：

> **Shell 决定研究上下文粒度，Unit 决定预期认知粒度。**

---

## 2. Shell 的边界如何划分

Shell 的核心不是简单描述“包含什么、不包含什么”，而是定义：

> **为什么这些 Units 必须放在同一研究上下文中，以及在什么情况下一个 Unit 应当与它们分开。**

例如 MU：

### Shell

> AI 存储周期与 Micron 盈利兑现

它可以覆盖：

* AI Capex；
* HBM/DRAM 供需；
* Micron HBM4 竞争兑现；
* 产品组合；
* 收入和利润率；
* 盈利持续性。

这些 Units 共享主要经济系统：

```text
AI Capex
→ 存储需求
→ 行业供需与价格
→ Micron份额和产品结构
→ 收入、利润和现金流
```

但如果出现另一个候选 Unit：

> 美国出口管制将永久改变 Micron 中国业务价值。

如果它拥有：

* 基本独立的事件体系；
* 基本独立的状态指标；
* 独立政策传导逻辑；
* 不依赖 AI 存储核心背景即可完整研究；

那么应考虑拆为其他 Shell。

因此 Shell 的 `boundary_rule` 应同时表达：

1. 什么共同经济系统把这些 Units 连接起来；
2. 什么条件意味着一个候选 Unit 已经足够独立，应拆出去。

---

## 3. Shell 不承担什么

Shell 不保存：

* 综合 bullish / bearish；
* Market View；
* Potential Gap；
* State Value；
* 具体事件；
* priced-in 判断；
* 交易方向。

这些都属于更细的 Unit 或运行时判断。

---

# 三、Expectation Unit

Expectation Unit 是：

> **市场会反复讨论、修正和交易的中层预期命题。**

例如：

* AI Capex 未来几个季度继续扩张；
* HBM 供给紧张持续到 2027 年以后；
* Micron 能够获得有意义的 HBM4 订单份额；
* HBM 收入增长能够转化为持续利润和现金流改善。

Unit 不应该是：

> AI 存储超级周期。

这太宽。

也不应该只是：

> FY27 EPS = 150。

这太底层。

Unit 应处在两者中间：

> **能够承载一整套预期状态和兑现研究，又能够被一组相关未来事件独立修正。**

---

# 四、第一板块：Unit Definition

Unit Definition 只负责确定：

> **系统长期管理的是哪一个预期对象。**

主要包括：

* 中层 Proposition；
* 主要 Horizon；
* 与相邻 Unit 的实际区别。

例如：

> 在 Vera Rubin 产品周期内，Micron 能取得足以形成实质财务贡献的 HBM4 订单份额。

这句话已经限定：

* 主体：Micron；
* 对象：Vera Rubin HBM4；
* 结果：实质订单份额；
* 经济意义：足以形成财务贡献；
* 时间：Vera Rubin 产品周期。

Unit Definition 不负责：

* 当前市场究竟相信多少；
* 模型认为最终会是多少；
* 未来哪些事件会发生；
* 是否已经 priced in。

这些由其他板块承担。

---

# 五、第二板块：Expectation State

## 1. State 的定位

Expectation State 是整个 Unit 中最重要的当前状态层。

它负责保存：

> **当前已经能够被稳定定义、稳定观测，并能够持续被未来信息更新的预期状态。**

它不是一段 Market View，也不是一篇当前状态总结。

State 由两种对象构成：

```text
State Parameter
    ↓
State Value
```

---

# 六、State Parameter

State Parameter 表示：

> **一个稳定存在、值得长期持续观察的状态变量。**

例如：

* HBM 供需重新平衡时间；
* Micron Vera Rubin HBM4 订单份额；
* FY27 EPS 一致预期；
* AI hyperscaler Capex 增长趋势；
* 当前 HBM 合约价格；
* 客户订单覆盖时间。

Parameter 只负责回答：

> **我们到底在持续观察什么？**

---

## 1. Parameter 应保持尽可能无上下文

Parameter 本身不需要保存：

* transmission layer；
* 对 Unit 的影响方向；
* importance；
* confidence；
* 当前值；
* ordered states。

例如：

> `HBM供需重新平衡时间`

本身就是一个稳定变量。

它对于不同 Unit 可能承担不同作用：

* 对“HBM 紧缺持续时间”是核心状态；
* 对“Micron 盈利持续性”是上游驱动；
* 对“传统 DRAM 定价”又可能是行业背景。

因此，不应把某个固定的 `transmission_layer` 写成 Parameter 的永久属性。

---

## 2. Parameter 与 Unit 不需要形成僵硬所有权

Parameter 可以被多个 Unit 共同使用。

例如：

> AI Capex 增长趋势

可能同时服务于：

* GPU 需求预期；
* HBM 需求预期；
* 数据中心电力需求预期。

所以逻辑上是：

```text
Parameter
   ↗     ↖
Unit A   Unit B
```

Document2 展示时可以把相关 Parameter 放在 Unit 内，但不能因此把 Parameter 理解成只能属于一个 Unit 的私有变量。

---

## 3. Parameter 的准入标准

一个东西适合作为 Parameter，需要基本满足：

1. 即使没有任何新事件，它仍然是一个稳定存在的状态变量；
2. 不同时间和来源能够持续提供新的 Value；
3. 新旧 Value 能够在大致相同口径下更新、替换或比较；
4. 它的变化会对至少一个 Expectation Unit 有实际意义。

如果不能满足这些条件，就不应该为了 Schema 完整而强行 Parameter 化。

---

# 七、State Value

State Value 表示：

> **某一个来源角色，在某一个时间范围和时间点，对 State Parameter 给出的具体当前状态。**

例如 Parameter：

> HBM 供需重新平衡时间

可以同时存在：

### MANAGEMENT

> Micron 管理层：供不应求持续至 2027 年以后。

### SELL_SIDE

> UBS：预计 2028Q2 恢复供需平衡。

### INDUSTRY_CHAIN

> 某产业链预测：2027H2 开始明显缓解。

### MARKET_IMPLIED

只有在确实存在足够依据时，才维护市场定价所隐含的状态。

---

## 1. State Value 可以采用不同表达

不是所有 Value 都应该数值化。

可以是：

### 数值

> FY27 EPS = 150。

### 区间

> Micron HBM4 订单份额 10%–15%。

### 时间

> HBM 供需平衡预计在 2027H2。

### 阶段

> 当前处于客户验证阶段。

### 方向

> AI Capex 仍处于上修状态。

### 证据状态

> 当前公开信息对某个状态存在 MIXED evidence。

关键原则是：

> **表达方式服务真实信息，不为了统一 Schema 强迫现实世界进入僵硬枚举。**

尤其是 `STAGE`，不要求提前建立一个全局 `ordered_states`。

如果某项业务天然存在稳定流程，可以直接用业务语义表达阶段。

---

## 2. State Value 的来源结构

预期来源仍然保持五种主要角色：

* ACTUAL；
* MANAGEMENT；
* SELL_SIDE；
* INDUSTRY_CHAIN；
* MARKET_IMPLIED。

这些来源不是五个独立 State 板块，而是：

> **同一个 Parameter 上可以存在的不同 State Values。**

这样才能真正比较：

> 同一个变量，不同市场参与者目前分别在预期什么。

---

## 3. Previous Value 的作用

每个 State Value 如果存在可靠同口径前值，应同时保存 Previous Value。

目的不是单纯记录历史，而是让 Agent 能直接识别：

> **这个来源的预期本身是否正在发生变化。**

例如：

```text
SELL_SIDE FY27 EPS

Previous:
$120

Current:
$150
```

这种变化本身就是重要预期信息。

同样：

```text
SELL_SIDE HBM供需平衡时间

Previous:
2026H2

Current:
2027H2
```

说明卖方正在明显延长景气预期。

但 Previous Value 只能在：

* 相同 Parameter；
* 相同来源角色；
* 可比较时间口径；

下使用。

不能为了填字段比较不同口径数据。

---

## 4. State Value 必须有 citation

State Value 是整个预期差系统最接近“硬锚点”的部分。

因此必须能够回答：

> **这个状态到底来自哪里？**

这里使用 citation 即可，不需要设计过重的 ObjectRef 审计体系。

系统目标不是建立一套证据数据库，而是约束 Agent：

> 不得凭空产生 State Value。

---

# 八、State 的更新原则

硬事件可以直接修改 State。

例如财报：

* Actual Revenue 更新；
* Actual Margin 更新；
* Management Guidance 更新；
* Previous Value 留作比较；
* Sell-side Value 暂时不变，直到卖方更新模型。

于是系统能够自然看到：

```text
实际值已经变了
管理层已经变了
卖方还没有变
市场隐含可能也还没有变
```

这才是预期差系统真正需要的状态基础。

---

# 九、第三板块：Expectation Realization Model

## 1. 为什么需要 Realization Model

Expectation State 只能覆盖：

> **已经能够稳定定义和持续管理的预期变量。**

但现实中的大量重大事件并不能合理压缩成 State Parameter。

例如火箭商业化：

State 可以包括：

* 发射次数；
* Backlog；
* 管理层预计发射节奏；
* 卖方收入预测。

但真正决定预期能否兑现的还有：

* 核心技术是否完成真实飞行验证；
* 重复发射可靠性是否成立；
* 监管批准是否顺利；
* 制造能力能否支撑发射节奏；
* 客户是否愿意从试验订单转成规模合同。

如果把这些全部硬 Parameter 化，会大量制造：

> 技术可信度 = MEDIUM
> 商业化能力 = HIGH

这种不可审计、伪结构化的状态。

因此需要 Realization Model。

---

# 十、Realization Factor

Realization Factor 是：

> **当前 State 无法充分表达，但根据研究能够识别，并会显著影响 Unit 最终兑现、失败、时间、强度、受益对象或转化效率的现实因素。**

它是现实机制对象。

---

## 1. Factor 与 State 的核心边界

### State Parameter

适合那些：

> 能稳定定义、持续更新、不同来源可以给出 Value 的变量。

### Realization Factor

适合那些：

> 很重要，也可以持续研究和观察，但当前状态必须保留具体情境语义，无法可靠压缩成一个标准 Parameter Value。

例如：

### State

> Micron HBM4 订单份额。

### Factor

> Micron 是否真正通过 Vera Rubin 所需要的客户技术验证。

---

## 2. Factor 需要维护 current_status

Factor 不能只写：

> 客户认证非常重要。

它必须同时保存：

> **这个因素目前现实发展到了什么状态。**

例如：

### Factor

> Micron 获得 Vera Rubin 正式客户验证。

### Current Status

> Micron 已公开 HBM4 送样及量产进展，但目前缺少 NVIDIA 或 Micron 对 Vera Rubin 正式资格的明确确认，公开媒体与部分产业链来源之间仍存在直接冲突。

这里不能被强压成：

> PARTIAL

因为真正有交易价值的恰恰是这些细微语义。

---

## 3. current_status 的边界

`current_status` 只回答：

> **截至现在，这个 Factor 所描述的现实条件发展到什么程度？**

不回答：

* 未来最可能怎样；
* 对股价是利好还是利空；
* 是否值得交易；
* 概率是多少。

它是一段受严格任务边界限制的自然语言状态，而不是自由分析文本。

---

## 4. Factor 的 Structural Role

Factor 可以大致承担：

### REQUIRED

预期兑现必须满足的重要条件。

### BLOCKER

一旦出现，会显著阻断预期兑现。

### MODIFIER

不会直接决定成立或失败，但会改变：

* 兑现强度；
* 时间；
* 受益对象；
* 转化效率。

这个分类的价值是：

> 帮助 Agent 理解一个 Factor 的现实作用边界。

而不是对 Factor 进行 bullish / bearish 分类。

---

## 5. Impact

Factor 还必须解释：

> **为什么它影响这个 Unit，以及它不能被外推到哪里。**

例如：

> 通过客户认证只能确认 Micron 获得准入资格，并不能证明其一定获得高订单份额。

这个字段非常重要，因为它限制事件发生后模型产生过度推理。

---

## 6. Observability

Realization Factor 不是抽象哲学概念。

它必须至少存在未来可以观察的事件接口。

例如：

> NVIDIA、Micron 或多个高可信独立来源明确确认供应资格结果。

或者：

> 下一次飞行是否完成完整任务链验证。

如果一个 Factor 无法通过任何未来事件观察，就没有必要进入事件驱动系统。

---

# 十一、Realization Factor 与 Potential Gap 的边界

这是当前方案中非常重要的概念边界。

## Realization Factor 描述现实机制

例如：

> 火箭是否完成端到端飞行验证。

即使市场完全知道它重要，这个因素依然客观存在。

因此它是 Factor。

## Potential Gap 描述未来认知变化

例如：

> 下一次火箭首次完整完成端到端任务。

这是一项未来 possible occurrence。

如果发生：

> 市场可能明显提前商业化时间并降低技术兑现折价。

因此这是 Potential Gap。

最简单的判断方式：

> **把“市场当前怎么看”和“未来会发生什么”全部拿掉以后，这个对象是否仍然客观影响 Unit？**

如果仍然成立：

> Factor。

如果失去意义：

> 更可能是 Gap。

---

# 十二、第四板块：Potential Expectation Gap Set

## 1. Gap 的重新定义

Potential Gap 不再定义为：

> 当前不同 State 之间已经存在的差异。

也不再定义为：

> State/Factor 的下一步机械变化。

它应当定义为：

> **针对某个 Expectation Unit，在充分理解当前 State、Realization Factors、已有事件和研究结论以后，进一步向未来进行受约束发散，得到的一个“未来单点 possible occurrence”，以及如果它发生，当前预期可能产生的有交易意义的修正。**

因此：

> **Potential Gap Set 是未来认知变化地图。**

---

# 十三、为什么称为“对预期的预期”

Expectation State 回答：

> 现在市场、管理层、卖方、产业链等分别在什么位置。

Realization Model 回答：

> 现实中什么决定这个 Unit 最终怎样兑现。

Potential Gap 回答：

> **接下来世界如果发生某件具体事情，我们现在这套预期可能怎样被迫修改？**

因此它实际上是在描述：

```text
当前预期
+
未来单点 occurrence
→
可能的新预期
```

而：

```text
可能的新预期
vs
事件发生时仍然存在的市场预期
```

才可能形成真正可交易的预期差。

---

# 十四、Potential Gap 不是 State/Factor 的机械派生

这是生成 Gap 时最重要的原则。

禁止这种生成方式：

```text
State A
→ 写一个 Gap A

State B
→ 写一个 Gap B

Factor C
→ 写一个 Gap C
```

这样只会生成模板化的未来版本。

State 和 Factor 只是：

> **当前现实认知的出发点和约束。**

Gap Discovery 还必须利用：

* Document1 研究；
* 当前 Event Registry；
* 历史事件；
* 行业机制；
* 产品和技术规律；
* 竞争行为；
* 多个 Factor 的组合；
* 多个 State 的组合；
* 当前结构未明确覆盖的开放世界变量。

---

# 十五、Potential Gap 的生成方式

针对整个 Unit，不应该问：

> 每个 State 下一步可能怎么变？

而应该问：

> **未来有哪些具体单点事件，一旦发生，会迫使我们实质性地重新判断这个 Unit？**

可以从以下角度进行发散，但这些只是推理视角，不作为 Schema 枚举。

### 当前趋势进一步延伸

例如：

> 主要客户首次开始锁定 2028 年 HBM 供给。

### 当前趋势突然反转

例如：

> 主要客户取消长期 HBM 采购协议。

### 关键兑现条件第一次被真正验证

例如：

> 下一次火箭任务首次完整完成所有关键飞行目标。

### 核心阻断因素发生

例如：

> Micron 被正式排除在 Vera Rubin HBM4 供应体系之外。

### 时间显著变化

例如：

> Samsung / SK Hynix 新产能提前一年进入规模生产。

### 受益对象重新分配

例如：

> HBM 景气继续，但新增订单大部分集中于竞争对手。

### 上游逻辑成立但传导失败

例如：

> HBM 收入继续增长，但良率成本与 Capex 导致自由现金流明显恶化。

### 当前模型尚未充分表达的新变量出现

例如：

> 下一代 AI 加速器架构显著降低单位算力所需 HBM。

这是 Potential Gap Set 相比 Realization Model 更进一步的地方：

> **它必须允许有限度突破当前已经定义出来的 State 和 Factors。**

---

# 十六、Potential Gap 必须是单点 possible occurrence

Gap 必须足够具体，使运行时能够判断：

> **这件事情真实发生了吗？**

不合格：

> AI 需求可能不如预期。

合格：

> Meta、Microsoft、Google 中至少一家正式下调下一年度 AI 基础设施 Capex 指引。

不合格：

> HBM供给可能提前释放并造成价格下跌。

应拆为：

1. Samsung/SK Hynix 新增 HBM 产能提前进入规模量产；
2. HBM 合约价格首次出现持续性环比下降。

这两个 occurrence 的意义并不完全相同。

---

# 十七、Potential Gap 要表达什么

一个 Gap 只需要讲明几个核心问题。

## 1. Possible Occurrence

未来到底可能发生什么。

这是 Gap 的主体。

---

## 2. Derivation

为什么基于当前情境，我们认为这一可能性值得提前监测。

它不是解释未来影响，而是解释：

> **这个 possible occurrence 是怎么从当前研究状态中被合理推演出来的。**

这样能够避免无限开放世界脑补。

---

## 3. Citation

Gap 必须有当前现实研究基础。

但 citation 不意味着：

> Gap 必须绑定某个具体 Parameter 或 Factor。

它只是证明：

> 该推演不是凭空故事。

依据可以来自：

* State；
* Factor；
* Event；
* Document1；
* 行业研究；
* 技术研究；
* 其他可信研究材料。

---

## 4. Expected Revision

如果 occurrence 真的发生：

> **当前 Expectation Unit 的判断可能需要怎样改变？**

例如：

> 将 HBM 紧缺持续时间进一步向后延长。

或者：

> 显著降低 Micron 能获得实质 HBM4 份额的可信程度。

或者：

> 明显提前商业化兑现时间，但不直接等同于收入预测上修。

Expected Revision 应同时说明：

> 能合理推到哪里，不能过度推到哪里。

---

## 5. Recognition Criteria

这是条件字段。

只有 occurrence 自身包含模糊概念时才需要。

例如：

> “AI Capex 显著放缓”

需要进一步定义：

> 什么样的信息才算真正发生。

而：

> FAA 正式拒绝运营许可

已经很明确，就不需要 Recognition Criteria。

---

# 十八、Potential Gap 不再维护反向条件

如果一个反向未来结果本身具有独立交易意义：

> 它应该成为另一个 Potential Gap。

例如对于 Micron：

### Gap A

> Micron 正式取得显著高于当前预期的订单份额。

### Gap B

> Micron 获得资格但实际份额极低。

### Gap C

> Micron 被正式排除。

### Gap D

> Micron 获得订单但良率不足导致交付延期。

这些都是不同的 future occurrences，应分别研究。

把 B/C/D 全部藏在 A 的 `counter_condition` 中，会系统性降低失败路径研究深度。

---

# 十九、Potential Gap 的数量

Gap 数量不应与 State Parameter 或 Factor 数量绑定。

一个 Unit 有：

* 5 个 State Parameters；
* 4 个 Realization Factors；

并不意味着应有 9 个 Gap。

Gap 数量取决于：

> **当前情境下能够识别出的、真正会迫使该 Unit 被重新判断的高价值单点 future occurrences。**

需要避免两种极端：

### 太少

只围绕当前几个 State/Factor 机械生成，开放世界覆盖不足。

### 太多

无限想象所有尾部可能性，导致监测和判断系统失去重点。

因此 Potential Gap 应满足：

1. 是具体单点 occurrence；
2. 发生后会实质改变 Unit；
3. 有现实研究基础；
4. 可以通过未来消息观察；
5. 具有潜在交易意义。

---

# 二十、事件进入系统后如何使用这些对象

当前已经使用 Codex SDK，因此没有必要为 `Expectation Update` 或 `Gap Activation` 再设计专门 Schema。

运行时 Agent 直接读取：

* 当前 Unit；
* State Values；
* Realization Factors 及 current_status；
* Potential Gaps；
* 新 Event；
* 当前市场信息。

然后判断：

```text
Event
↓
它是否改变某个稳定 State？
↓
是否需要创建/替换 State Value？

同时：
↓
它是否改变某个 Realization Factor 的 current_status？

同时：
↓
它是否命中了事前定义的 Potential Gap？

↓
如果命中：
这次事件造成的真实预期修正是什么？

↓
该修正相对于当前市场认知是否仍然具有未定价部分？

↓
Trading Evaluation
```

不要求初始化阶段提前预测事件发生后一定修改哪个数据库字段。

---

# 二十一、硬事件

硬事件通常能够直接改变 State。

例如财报：

```text
Revenue Actual
旧值 → 新值

Management Guidance
旧值 → 新值
```

State Value 中 Previous Value 可以直接显示变化。

随后 Agent 判断：

* 哪些原 Potential Gaps 已被命中；
* 哪些已经失效；
* 是否需要发现新的 Potential Gap。

---

# 二十二、软事件

软事件可能完全不修改任何数值 State。

例如：

> 火箭完成第一次完整任务验证。

它可以直接改写 Realization Factor：

### Before

> 完整任务链尚未经过一次真实成功验证。

### After

> 首次完成端到端任务，核心飞行能力获得实证；重复执行能力仍未验证。

这可能同时命中一个 Potential Gap：

> 下一次任务首次完整成功。

然后进入预期差判断。

---

# 二十三、State 与 Factor 都可能在事件后发生变化

有些事件会同时更新两者。

例如：

> NVIDIA 正式确认 Micron 获得供应资格，并披露 20% 首批订单份额。

可能同时：

### 更新 Factor

> 客户资格从“存在公开冲突”变成“官方确认”。

### 更新 State

> Micron 订单份额获得新的实际/产业链 Value。

### 命中 Potential Gap

> Micron 获得明显高于当前卖方主要估计区间的订单份额。

因此这些对象不是互斥的。

---

# 二十四、市场定价

Expectation State 中可以存在 MARKET_IMPLIED State Value，但必须建立在能够比较具体状态的前提下。

不能简单维护：

> “该 Unit 已 priced in 70%。”

这种数字并不可可靠获得。

真正有意义的问题是在新事件发生后：

> **这个事件所产生的新增预期修正，市场当前是否已经同步吸收？**

Codex Agent 可以结合：

* 事件前市场认知；
* Sell-side 是否已经同步；
* DoxAtlas Narrative 是否已广泛传播；
* 事件是否只是旧消息正式确认；
* 事件前后价格；
* 估值；
* 期权；
* 相对表现；

进行判断。

事后价格反应主要用于验证和校准，而不能作为交易判断的前置条件。

---

# 二十五、完整示例一：HBM 紧缺持续时间

## Expectation Unit

> HBM 供给紧张将持续到 2027 年以后。

---

## Expectation State

### Parameter：HBM供需重新平衡时间

Values：

* MANAGEMENT：2027 年以后；
* SELL_SIDE：2027H2 / 2028Q2 等不同预测；
* INDUSTRY_CHAIN：根据客户锁单和产能情况持续更新。

### Parameter：客户订单覆盖时间

Values：

* 实际合同覆盖时间；
* 管理层披露；
* 产业链观察。

### Parameter：竞争者新增产能预计释放时间

Values：

* Samsung；
* SK Hynix；
* 产业链预测。

---

## Realization Factors

### 客户长期锁单持续性

Current Status：

> 主要客户已经出现多年锁单，但当前可确认的覆盖范围主要集中于既有周期，尚未观察到广泛覆盖 2028 年的明确长期锁单。

### 新产能良率爬坡

Current Status：

> 竞争者已经公布大规模扩产，但新增产能实际设备安装、验证和良率爬坡时间仍存在较大不确定性。

### HBM 与传统 DRAM 产能重新分配

Current Status：

> HBM 优先配置正在挤压部分传统 DRAM 供给，但未来竞争者是否将部分产能重新切回普通 DRAM 尚不明确。

---

## Potential Gaps

### Gap 1：主要AI客户首次锁定2028年HBM供给

Possible Occurrence：

> 核心 AI 客户首次正式签署覆盖 2028 年的 HBM 长期供应或预付协议。

Expected Revision：

> 将进一步延长需求可见度，并显著强化紧缺持续到 2027 年以后的判断。

---

### Gap 2：竞争者新增HBM产能提前进入规模量产

Possible Occurrence：

> Samsung 或 SK Hynix 新增产能比当前主要公开时间表提前数个季度进入规模生产。

Expected Revision：

> 当前供需重新平衡时间可能明显提前，削弱长期紧缺预期。

---

### Gap 3：下一代AI加速器显著降低单位算力HBM需求

Possible Occurrence：

> 主流下一代 AI 加速器出现明确架构变化，使单位算力的 HBM 容量或带宽需求低于当前路线图。

Expected Revision：

> 需要重新评估 AI Capex 向 HBM 需求增长的传导强度，即使 AI Capex 本身没有下降。

这一 Gap 并不是现有某个 State/Factor 的机械改写，而是基于整个当前技术与需求逻辑继续向开放世界进行发散。

---

# 二十六、完整示例二：商业火箭兑现

## Expectation Unit

> 公司能够在未来产品周期内实现稳定商业发射，并兑现现有订单和收入增长。

---

## Expectation State

可以包含：

* 已完成发射次数；
* 管理层计划发射次数；
* Sell-side 收入预测；
* 当前 backlog；
* 商业化时间预期。

---

## Realization Factors

### 完整飞行任务链验证

Current Status：

> 已完成部分核心飞行能力验证，但完整端到端任务尚未得到一次真实成功验证。

### 重复发射可靠性

Current Status：

> 当前缺乏足够连续成功任务，无法确认系统已经具备稳定商业运行所要求的重复可靠性。

### 监管许可

Current Status：

> 主要许可流程仍在推进，目前没有出现实质性否决，但后续审批仍可能影响商业化时间。

---

## Potential Gaps

### Gap 1：下一次任务首次完整完成所有关键飞行目标

Expected Revision：

> 明显提高商业化路径可信度并可能提前市场对稳定商业发射时间的判断，但不能直接等价为收入预测上修。

---

### Gap 2：下一次任务出现新的关键系统性故障

Expected Revision：

> 可能重新打开技术路径风险，并推迟商业化时间判断。

---

### Gap 3：重大商业客户在完整验证前提前签署规模合同

Expected Revision：

> 客户对技术路径和商业化能力的外部验证明显增强，可能提高现有 backlog 的质量判断，即使当前发射数据未发生变化。

---

# 二十七、Document2 的最终结构

每个 Shell：

```text
Expectation Shell

└── Expectation Unit

    ├── Unit Definition

    ├── Expectation State
    │   ├── State Parameter
    │   └── State Value

    ├── Expectation Realization Model
    │   └── Realization Factor
    │       └── current_status

    └── Potential Expectation Gap Set
        └── Future single-point possible occurrences
```

---

# 二十八、C1/C2/C3/C4 的职责

各 Agent 不需要各自填写完整 Expectation Detail。

它们在保留自己 D1 完整领域上下文的基础上，对同一个 Shell 提供对应贡献。

## C1

重点提供：

* 公司实际数据；
* 财务参数；
* 管理层预期；
* 财务兑现 Factors；
* 公司级 Potential Gaps。

## C2

重点提供：

* 宏观 State；
* Capex、利率、政策环境；
* 宏观兑现与阻断 Factors；
* 宏观未来 possibilities。

## C3

重点提供：

* 产业链 State；
* 竞争；
* 价格；
* 份额；
* 客户和供应商；
* 行业 Realization Factors；
* 产业链 Potential Gaps。

## C4

重点提供：

* MARKET_IMPLIED 状态；
* 市场关注焦点；
* 定价；
* 价格吸收；
* 事件发生后的市场反馈。

---

# 二十九、Potential Gap Discovery 的特殊要求

Potential Gap 是四个板块中最需要发散推理的部分。

因此它不能只是要求领域 Agent：

> “根据你刚才输出的 State 和 Factor 再生成 Gaps。”

而应该要求：

1. 先理解整个 Unit 当前情境；
2. 使用 State 和 Factor 作为主要现实约束；
3. 重新查看完整领域研究；
4. 主动考虑兑现、失败、延迟、提前、重新分配和传导失败；
5. 寻找目前 State/Factor 尚未完全覆盖的高影响 future occurrences；
6. 只保留具体、可识别、有现实依据、能实质改变 Unit 的单点 possibility。

O1 最终负责：

* 去重；
* 合并；
* 拆分复合事件；
* 删除低价值尾部想象；
* 保证不同方向得到合理覆盖；
* 防止 Gap Set 退化为现有 State/Factor 的机械镜像。

---

# 三十、最终核心逻辑

```text
Expectation Shell
确定哪些预期必须共享完整研究上下文

        ↓

Expectation Unit
定义市场真正反复交易的中层命题

        ↓

Expectation State
维护已经能够稳定参数化的当前预期

        +

Realization Model
维护无法稳定参数化但现实中决定预期兑现的关键机制

        ↓

Potential Gap Set
在当前完整情境基础上进一步向未来发散，
形成可能迫使当前预期发生修正的单点事件地图

        ↓

Future Event

        ↓

Codex Agent 基于最新 Blackboard 判断：
State 是否变化？
Factor current_status 是否变化？
是否命中已有 Potential Gap？
是否出现此前未覆盖的新 possibility？
实际预期修正有多大？
市场是否已经吸收？

        ↓

Trading Evaluation
```

最终，三者的边界可以压缩成：

> **Expectation State = 当前能够稳定表达的预期状态。**

> **Realization Factor = 当前能够研究和描述、但不适合稳定参数化的现实兑现机制。**

> **Potential Gap = 基于前两者和完整研究情境，对未来可能迫使当前预期发生显著修正的单点 occurrence 所做的事前地图。**

这三层共同作用，才能让下游 Agent 的事件交易判断建立在已有状态、现实机制和事前推演的基础上，而不是在新闻发生后重新依赖模型自由发挥。
