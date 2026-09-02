# O3 Prompt / Internal Skill 重构方案

## 一、全局写作原则

这轮重构首先统一四份文件的写法。

### 1. 每个文件只承担一个层级的认知职责

避免同一概念在多个 Skill 中反复详细解释，因为高频重复会把内部思考概念强化成输出模板。

建议职责固定为：

```text
agent.md
→ 我是谁
→ DoxAgent 为什么需要 O3
→ O3 对整个系统贡献什么
→ 什么样的结果才有价值

foundation.md
→ O3 的统一业务 ontology
→ 什么是 Path / Trigger / Condition / Policy
→ 什么叫交易充分、现实可披露、W2 可判断
→ 各 Policy 字段承担什么业务功能

initialize_trigger_calibration.md
→ 怎样研究出一个高质量 Trigger

initialize_policy_compile.md
→ 怎样把研究好的 Trigger 编译成 Runtime Policy
```

Stage Skill 引用 Foundation 中已经定义的概念，以执行方法为主，不重复大段 ontology。

---

### 2. 建立明确的质量优先级

当前 O3 同时追求：

```text
足够早
可靠
低自由度
现实可披露
最小充分
抗噪
```

需要让 Agent 理解这些目标发生张力时怎样思考。

建议统一为：

```text
① 忠实于 D2 revision space 与 ticker transmission
↓
② Trigger 必须存在于真实消息流
↓
③ 在现实可观察的候选状态中寻找最早的交易充分边界
↓
④ 通过 Calibration 和明确 comparator 提高 W2 judgeability
↓
⑤ 最后才优化表达简洁度、Policy 数量和形式
```

核心思维是：

> **可靠性主要来自正确的现实状态、比较基准和消息确认方式，而不是通过继续等待更多后续经营结果获得。**

这样 Agent 遇到“早但仍有正常不确定性”和“晚但非常确定”的两个候选时，会主动研究哪个早期状态已经产生了足够明确的 expectation delta，而不是天然向更晚的确认状态移动。

---

### 3. 区分“内部分析语言”和“最终 Policy 语言”

以下概念主要属于 O3 的内部思考工具：

```text
Natural disclosure
Trigger-bearing actor
Marginality Test
Deletion Test
Direct Trading Sufficiency
W2 Judgeability
```

它们帮助 Agent 做判断，不应自然演化成固定的 Policy 文案。

最终 Policy 应使用对应市场事件本身的语言：

```text
Microsoft 将某产品投入 production
客户将 binding volume 从 X 下调至 Y
BIS 新规则正式生效
Micron 将某项目量产时间从 Q2 延至 Q4
```

而不是让最终字段普遍出现：

```text
一名可识别的……
同一自然披露确认……
相对于当前参考状态……
```

这样可以避免 reasoning vocabulary 变成模型模仿的语言模板。

---

### 4. 把几个 Quality Tests 从“自我证明”改造成分析方法

Agent 不应只是问：

> “我认为这个 Trigger 是否 minimal？”

而应该知道**怎样分析**。

整个 O3 以后共用四类核心方法：

```text
Expectation Update Analysis
Actor-State Analysis
Message Production Analysis
Predicate / Causal Layer Analysis
```

后面分别落到 Foundation 和 Stage Skill 中。

---

# 二、`agent.md`

当前 Agent Prompt 已经较好解释 D1 → D2 → D3 → Runtime，但 O3 的系统价值仍应进一步围绕**实时信息流**解释。当前使命描述主要强调“足够证据、稳定判断、最早可靠边界”。

## 调整 1：把 O3 定义为“Expectation Research → Real-World Message Decision”的转换层

在 `O3's Place and Contribution` 中明确：

D2 的研究对象是：

```text
未来现实怎样变化
→ expectation 怎样修订
```

O3 的研究对象则是：

```text
现实现在已经在哪里
↓
未来哪个具体状态变化第一次形成足够大的 expectation update
↓
现实的信息生产系统通常会怎样把这个变化变成消息
↓
怎样在消息到达前把判断标准预先写好
```

让 Agent 理解：

> **O3 不只研究经济事件，还研究经济事件如何成为市场可获得的信息。**

Policy 的实际使用场景不是理想化研究报告，而是 message bus 中真实出现的单条新闻、公告、filing、客户消息、供应链报道等。

---

## 调整 2：重写 O3 的核心问题

当前核心问题偏向：

> “什么变化、什么证据能够迫使 expectation 修订？”

建议改成更明确的边际更新思维：

> **相对于 D2 已建立的 expectation prior 和当前现实，未来哪一项可观察的新事实第一次足以使相关 expectation 的概率、时点、规模、实现路径或风险发生具有交易意义的方向性更新？现实市场通常怎样得知这一事实？**

这里使用：

> “方向性更新”

替代容易产生高确定性倾向的：

> “迫使 expectation 修订”。

---

## 调整 3：加入 O3 的价值函数

让 Agent 判断自己的产物是否真正对系统有用：

一条高价值 Policy 同时满足：

```text
真实消息可能出现
+
出现时间仍具有交易价值
+
消息本身足以产生新的 expectation delta
+
W2 可以基于 Policy 与消息判断
```

强调：

> 一个逻辑上严谨但现实消息不会出现的 Policy，对 Runtime 没有价值。

> 一个只有在收入、利润和份额已经全面兑现后才能触发的 Policy，即使判断非常确定，也可能已经失去 O3 应创造的早期交易价值。

---

## 调整 4：解释 W2 的真实限制

Agent Prompt 应明确告诉 O3：

Runtime Worker 的设计目的就是避免重新完成 D1/D2/O3 的深度研究。

W2 主要拥有：

```text
未来消息
+
紧凑 Policy Projection
```

所以 O3 要提前完成：

```text
复杂经济判断
现实 baseline 判断
comparison baseline
边界 Calibration
消息可观察性判断
```

而 W2 只完成：

> “这条新消息是否满足已经定义好的状态命题？”

这样 O3 会更容易理解为什么：

```text
明显恶化
重大变化
健康库存
高位
```

如果没有可见基准，就不是一个可交付给 W2 的判断标准。

---

## 调整 5：Agent Prompt 保持原则层，不继续堆 Stage Procedure

`agent.md` 不加入：

```text
Deletion Test 具体步骤
Atomic Decomposition 步骤
Trigger record 写法
Multi-condition 编译方法
```

这些留给 stage skills。

Agent Prompt 只形成：

> **正确的问题意识和质量偏好。**

---

# 三、`foundation.md`

Foundation 现在已经有正确的基本 ontology：Gap → Path → Trigger → Condition → Policy，并定义六项 Condition 属性。

本轮应重点把这些概念从“正确描述”升级为“Agent 可实际使用的认知模型”。

---

## 调整 1：将 Direct Trading Sufficiency 改成 Expectation Update 模型

不要主要让 Agent问：

> “这条消息够不够让我直接交易？”

这种表述容易激发保守倾向。

Foundation 中建立：

### Marginal Expectation Update

把 D2 当作 prior：

```text
Current expectation prior
+
New fact
→ Updated expectation
```

Trigger 的作用不是证明 thesis，而是提供一项足够新的信息，使以下一个或多个核心维度发生明确方向更新：

```text
probability
timing
scale
economic capture
risk
```

Direct Trading Sufficiency 的核心判断变成：

> **这项事实是否已经使 D2 中某个重要 expectation lever 的判断发生了足够明确的方向变化，并且对应 LONG / SHORT transmission 不需要假设另一个尚未出现的关键事实？**

剩余 uncertainty 可以继续存在。

这比：

> “单条消息够不够交易？”

更容易让模型找到早期预期差。

---

## 调整 2：正式引入 `Minimal Sufficient Fact Set`

Foundation 需要定义：

> Candidate Trigger 可以由一个或多个事实组成，但最终保留的是实现当前 expectation update 所需的 **Minimal Sufficient Fact Set**。

分析原则：

```text
candidate facts
↓
逐项判断每个事实是否改变交易方向成立性
↓
仅保留对 expectation update 不可缺少的事实
```

这样 Minimality 不再只是“删一句看看我还敢不敢交易”，而是：

> **这个事实是否对当前 expectation revision 的成立具有独立信息贡献？**

---

## 调整 3：引入 `Causal Layer`

Foundation 应教 Agent 区分一个 Path 上不同信息所处层级：

```text
Precursor
→ Trigger State
→ Transmission
→ Realization
```

例如：

```text
客户开始 qualification
→ 客户正式 production adoption
→ Micron 获得更多高端 demand
→ shipment / revenue / margin 增长
```

D2 可以研究完整链条。

O3 Condition 通常寻找：

> **第一次跨过 Direct Trading Boundary 的 Trigger State。**

如果 revenue/margin 只是证明前面的状态最终产生经济结果，它属于 Realization，而不是自动进入 Trigger。

这样可以从概念层解决 realization leakage，而不是只在 Compile 末尾删字段。

---

## 调整 4：重新定义 Actor-specific

不要让 Agent 把：

> actor-specific

理解为：

> “把名词改成一名可识别的客户”。

Foundation 应说明：

> Actor specificity 描述的是**现实状态边界是否依赖某个 actor 自己的 current state**。

分析步骤：

```text
哪些现实 actors 可能承载这个 Path？
↓
它们现在的状态是否相同？
↓
它们的下一项 expectation-changing transition 是否相同？
```

如果：

```text
Customer A 尚未削减
Customer B 已经削减 20%
```

两者需要不同 trigger boundary。

如果多个竞争者当前状态和事件语义完全对称，例如：

> 任一主要 DRAM competitor 的单一重大 fab outage

那么一个“单一事件主体”的通用 Policy 也可能合理。

核心不是：

> 每个公司都机械拆一条 Policy。

而是：

> **不同 current state / trigger boundary 才需要 actor-specific calibration。**

这同时避免“aggregate actor”和“过度 actor enumeration”两个方向的问题。

---

## 调整 5：把 Disclosure Plausibility 升级为 `Message Production Model`

Foundation 不再只定义：

> 谁可能发布 + 什么消息。

而是让 Agent 建模一个现实信息生产过程：

```text
Reality Event
↓
谁首先掌握这个事实？
↓
谁有动机/义务/习惯披露？
↓
通常通过什么消息载体？
↓
这种载体通常会包含哪些事实？
↓
哪些事实通常由另一主体或更晚消息披露？
```

这解决：

> “一篇综合报道理论上可以写很多事实”

和：

> “这些事实在现实中自然一起产生”

之间的区别。

### Disclosure-realistic 的判断核心

不是：

> 我能否想象一篇文章同时写这些内容？

而是：

> **真实的信息生产机制是否通常会让一个消息 source 在这个时点掌握并报道这些必要事实？**

---

## 调整 6：加入 `Historical Disclosure Analogy`

Foundation 建立一个研究偏好：

> 当 Candidate Trigger 的 disclosure pattern 并不明显时，优先查看相同 actor、相同事件类别或相同行业历史上类似事实通常如何公开。

例如研究：

```text
客户 qualification
合同重谈
BOM change
供应商 allocation
fab outage
```

可以查看过去同类事件：

> 谁先报道、报道到什么粒度、哪些数据通常不会同时公开。

这里不是要求每条 Policy 固定搜索 N 个案例，而是教 Agent：

> **消息现实性可以被研究，而不是靠想象判断。**

---

## 调整 7：正式定义 `Observable Comparator`

Comparator 必须属于以下至少一种：

```text
Policy 中已有明确 baseline
未来消息自身明确给出的 previous/current comparison
公开且确定的 current commitment / guidance / timeline
Runtime 可直接获得的明确历史状态
```

如果 comparator 本身需要 W2：

```text
估算无保护情景
推演如果没有 ceiling 会是多少
自行判断正常库存
```

那它不是 Runtime 可用 comparator。

这样可以阻止：

> “高于无保护基线”

这类理论 comparator。

---

## 调整 8：把 Condition 定义成 Boolean Predicate

Foundation 中强化：

> **一个 Condition 对 W2 而言就是一个可以独立判断 TRUE / FALSE / INSUFFICIENT 的现实命题。**

这里比“一个完整世界状态”更具体。

如果一句 Candidate Trigger 包含：

```text
BOM下降
订单下降
库存上升
```

Agent 应自然看到三个 predicates，而不是一个“需求恶化状态”。

这为 Compile 的 Atomic Decomposition 提供更清楚的 ontology。

---

## 调整 9：重新强化 Recall / Activation 分工

当前 Foundation 只说 `match_scope` 是候选召回。

需要提升到完整 mental model：

### `match_scope`

是 Policy 的 **retrieval envelope**。

它回答：

> **哪些消息值得让 W2 看一眼？**

允许包含：

```text
Trigger 前置信号
部分满足
可能相关的状态变化
可信供应链报道
公司公告
客户消息
行业报道
```

即使消息最终 Activation = FALSE，也可以是好的 recall。

### Activation Condition

回答：

> **哪项状态真正成立？**

因此：

```text
match_scope
→ high recall

Condition
→ decision precision
```

低精度候选可以交给 W2过滤。

O3 不应通过把 `match_scope` 写成 Activation 的同义句来降低召回噪音。

---

## 调整 10：明确 Source Authority 与 State Evidence 是两个维度

Foundation 要让 Agent分开判断：

```text
消息来源可信度
≠
状态是否成立
```

`official filing` 可以是高质量证据。

但：

```text
Reuters sourced report
credible customer/supplier report
industry intelligence
```

也可能直接确认真实状态。

`qualifying_evidence` 应优先描述：

> **消息需要确认什么事实。**

来源类型是判断证据可信度的一部分，而不是默认要求：

> official / formal only。

---

## 调整 11：Canonicalization 加入“避免形式化 actor 拆分”

当前 canonicalization 强调不同 actor 独立时保持 Policy。

进一步明确：

比较的是：

```text
current-state dependency
+
trigger semantics
+
disclosure structure
+
ticker expectation delta
```

而不只是：

> actor name 是否不同。

如果两个 actor 的状态、trigger boundary 和 decision 完全相同，而且 Policy 本身针对单一 actor event 逐条判断，可以共享通用 Policy。

如果 actor 当前 baseline 不同：

> 分开。

这能防止新的 “CSP版 / OEM版 / A项目版 / B项目版” 模板化膨胀。

---

# 四、`initialize_trigger_calibration.md`

当前 Skill 已经包含六项 Trigger Tests 和 Targeted Research。

重构重点应是让这些 Test 成为真正的研究方法，而不是自由文本自证。

---

## 调整 1：Worklist Gate 先做 `Actor-State Resolution`

不要从：

```text
Gap
→ 看到多个 actors
→ 立即拆 actor-specific Path
```

直接进入拆分。

建议思考顺序：

```text
Gap
↓
识别可能承载事件的 actors / objects
↓
调查其 current state 是否存在差异
↓
判断这些差异是否改变下一 Trigger
↓
再决定是否拆 Path
```

这样：

> Path decomposition 是 actor-state research 的结果，

不是对 D2 名词表的机械展开。

---

## 调整 2：`missing_calibration` 围绕“Trigger Selection Decision”写

一个好的 Calibration Question 应让 Agent 研究完以后可以改变：

```text
actor
current_state
candidate_trigger
comparator
disclosure_route
```

例如不只是：

> “该客户现在处于什么阶段？”

而是：

> “当前主要客户分别处 qualification、production adoption 或 recurring shipment 哪一阶段；哪一个 actor 的下一项变化会成为相对于现有 baseline 的第一项新的 expectation delta？”

重点是：

> Research 的终点始终是 Trigger Selection。

---

## 调整 3：建立 Actor-State Analysis 方法

对于复杂 Gap，Stage A 先形成一个内部认知表：

```text
Actor
Current State
Already Priced/Baseline
Next Plausible State
Expectation Effect
```

无需新增最终 Policy 字段。

它的目的，是避免直接写：

> “一名可识别的 CSP”。

Agent 应首先知道：

> 现实中哪些 CSP / OEM / supplier relevant，以及它们当前状态是否真的不同。

只有在现实状态对 Trigger 没有差异时，才保留通用单主体事件类型。

---

## 调整 4：将 Counterfactual Trade Test 改为 `Marginal Expectation Update Test`

减少：

> “我敢不敢仅凭这一条消息交易？”

这种容易产生保守性的主观问题。

改为按 D2 transmission 分析：

```text
如果 Candidate Fact 现在成立：

D2 中哪个 expectation parameter 会变化？
↓
变化的是 probability / timing / scale / path / risk 中哪个维度？
↓
变化方向是否已经确定？
↓
LONG / SHORT transmission 是否已经成立，
还是必须额外假设另一项关键未公开事实？
```

如果 transmission 已成立：

> Trigger 可以保留正常的后续不确定性。

这样 Direct Trading Sufficiency 被转换成一个 expectation-model 问题，而不是“信心够不够”的问题。

---

## 调整 5：在 Minimality 前先做 `Causal Layer Analysis`

把 Candidate 中每项事实归到：

```text
Precursor
Trigger candidate
Transmission evidence
Realization evidence
```

然后再运行 Deletion Test。

例如：

```text
第二客户 production adoption
→ Trigger candidate

后续 recurring shipment
→ realization

收入增长
→ realization

margin improvement
→ realization
```

如果 shipment 对 adoption 的真实性本身不可缺：

> 可以留下。

如果它只是证明 adoption 最终赚钱：

> 它属于后续 realization。

这样 Deletion Test 不再单纯依赖 Agent 的“确定性直觉”。

---

## 调整 6：Minimality 改为信息贡献分析

不只问：

> 删掉 C，我还敢不敢 trade？

而要问：

> **C 是否改变 D2 expectation revision 的成立性或方向？**

如果：

```text
A 已经使 probability 明确上升
B 只是证明之后 revenue 兑现
```

B 对 Trigger 的 expectation update 没有新增必要贡献。

于是删除 B。

---

## 调整 7：Disclosure Plausibility 改为实证型 Message Production Analysis

Stage A 应先写出：

```text
event owner
information holder
likely publisher/reporter
message type
normal content boundary
```

当这些关系不明显时，再定向搜索历史 disclosure pattern。

例如：

```text
AI platform BOM change
```

问：

> BOM 通常由平台、OEM、供应链还是媒体先公开？

再问：

> 同一来源通常能否同时确认 Micron wafer allocation？

如果后一项属于另一信息生产链：

> 它不是同一个 Candidate Trigger 的自然组成部分。

---

## 调整 8：`disclosure_route` 记录“现实路径”，而不是泛化 source class

`disclosure_route` 不应只写：

```text
company filing
customer announcement
credible report
```

而应说明：

```text
哪个信息持有者
→ 哪类消息通常公开什么
→ 本 Candidate 的必要 facts 是否在该消息边界内
```

这样 Stage B 才能真正消费，而不是看到：

> “company announcement”

就继续自由扩张 Condition。

---

## 调整 9：Stage A 单 Trigger Record 与 Condition 数量彻底解耦

明确：

> **一个 Candidate Trading Trigger record 是一个 message-level trading event，不预设最终需要几个 Conditions。**

Candidate Trigger 可以经过 Stage B 被拆成：

```text
C1
C2
```

只要它们是同一现实消息里的两个独立 predicates。

这样：

```text
1 Path
→ 1 Trigger record
```

不再暗示：

```text
1 Trigger record
→ 1 C1
```

---

## 调整 10：Stage A 加入独立的 Realization Layer 检查

在 `TRIGGER_READY` 前，主动问：

> Candidate 中每个事实是在定义“发生了什么新的 expectation-changing event”，还是只是在说明这个 event 后来产生了预期中的经营结果？

后者应主要进入：

```text
trade_sufficiency explanation
D2 transmission
future maintain/reference state
```

而不是 Candidate Trigger。

---

## 调整 11：W2 Judgeability 加入 `Observable Comparator Test`

Agent 应回答：

> W2 到底从哪里知道比较对象？

合法来源包括：

```text
criterion 内的当前值
明确 commitment
明确 timeline
消息自身的 before/after
Runtime 已提供的 current state
```

如果 comparator 只能通过：

> 假设没有 floor 会怎样

得到，

说明需要重新校准边界。

---

## 调整 12：Research Stopping Rule 由“可辩护”变成“关键不确定性已解决”

Research 在以下几个问题都有明确答案时结束：

```text
谁承载 Trigger？
现在在哪里？
哪项下一变化更新 expectation？
哪些事实真正必要？
这项变化现实中怎样成为消息？
W2 怎样判断？
```

不是因为：

> 已经能写出一个逻辑完整的 Trigger record

就停止。

---

## 调整 13：`TRIGGER_READY` 的语义改成“研究结论已经足够具体”

Ready record 应体现：

```text
实际完成过 actor resolution
有明确 current-state anchor
candidate_trigger 已经过 causal layering
minimality 说明排除了哪些跟随事实
disclosure_route 有现实信息生产逻辑
judgeability 有 observable comparator
```

让 Ready 表示：

> **Research 已完成。**

而不是：

> “我能给每个 schema field 填一句合理的话。”

---

# 五、`initialize_policy_compile.md`

Compile 是本轮最需要重新调整行为姿态的文件。

当前开头将 Stage A 定义为主要 Trigger authority，并强调“研究问题已经解决、忠实表达”。

需要让它变成：

> **研究结论消费者 + 独立 Runtime semantic compiler。**

---

## 调整 1：重新定义 Stage A 与 Compile 的关系

Stage A 提供：

> 一个经过研究的 Trigger hypothesis。

Compile 不重新做完整行业研究，但独立负责判断：

> **这个 Trigger 是否真的可以被表示为 Runtime 可执行的 Boolean conditions。**

因此：

```text
Stage A
→ research authority

Compile
→ representation / runtime semantic authority
```

当二者冲突时，Compile 应使用 Stage A 已有证据重新检查 Trigger，而不是优先维护原 Candidate 文案。

---

## 调整 2：把 Atomic Decomposition 改成 Predicate Decomposition

不是问：

> “这一句话包含几个事实？”

而是问：

> **未来消息需要让 W2 对几个独立 Boolean propositions 作出判断？**

操作：

```text
Candidate Trigger
↓
拆成 predicates
↓
每个 predicate 判断：
可以独立 TRUE/FALSE 吗？
是否属于不同 causal layer？
是否由不同信息持有者确认？
```

例如：

```text
P1: OEM 将 base DRAM 从 X 调低至 Y
P2: OEM 同时减少采购订单
P3: 渠道库存高于此前水平
```

三个就是三个 predicates。

不能因为它们共同描述“需求恶化”就重新变成一个 C1。

---

## 调整 3：Condition 数量变成推导结果，而不是设计选择

删除“先选择 Single 或 Multi-condition”的心理顺序。

改成：

```text
先做 Predicate Decomposition
↓
保留 Minimal Sufficient Predicate Set
↓
根据 predicate 数量自然得到 Condition 数量
```

例如：

```text
1 个独立必要 predicate
→ C1

2 个独立必要 predicates
且同一消息自然确认
→ C1 + C2
```

这样 Multi-condition 不再需要比 Single Condition 额外承担一套更重的心理证明责任。

---

## 调整 4：当多个必要 predicates 不属于同一消息时，回到 Trigger 语义

这里应把它理解成：

> **不是 Condition 写法问题，而是 Stage A Trigger granularity 问题。**

Compile 此时重新看：

```text
哪个 predicate 最早已经 sufficient？
当前 actor 是否拆错？
Stage A 是否把不同阶段放在同一 Trigger？
```

通过已有 Stage A research 修正。

如果确实需要新现实事实才能解决，再使用当前 workflow 支持的 unresolved / later review 机制。

---

## 调整 5：Realization Leakage 改成 Compile 的独立判断

不要再写成：

> “如果 Stage A 已经证明 adoption sufficient，则删除后续结果。”

而是让 Compile 自己从 Causal Layer 判断：

```text
这个 predicate 定义 Trigger 本身？
还是只验证 Trigger 产生的 downstream outcome？
```

Stage A 的 `trade_sufficiency` 是重要依据，但不是唯一判断来源。

Compile 应能发现：

> Stage A 把 realization 错误放进 Candidate Trigger

并纠正它。

---

## 调整 6：Criterion 采用 Boolean Predicate 思维

`criterion` 的价值不是写得“完整”。

而是：

> **让 W2 知道这一项 Condition 到底要判断哪个状态是真是假。**

Criterion 根据实际需要包含：

```text
actor/object
state change
necessary scope
observable comparator
```

不要求固定句式。

分析解释不进入 Criterion：

```text
为什么这对 MU 重要
为什么会改善收入
为什么会影响 margin
```

这些已经存在于 D2 transmission 和 Stage A sufficiency。

---

## 调整 7：Reasoning Vocabulary 与 Policy Language 分离

Compile 在写：

```text
title
criterion
activation_summary
match_scope
```

时使用真实领域语言。

例如：

```text
Google 将某平台基础内存从 X 提升至 Y
```

而不是：

```text
同一自然披露确认一名可识别的平台……
```

Skill 中的：

```text
natural disclosure
actor-specific
reference_state
```

只用于思考过程。

---

## 调整 8：Qualifying Evidence 以“事实内容”为中心

先回答：

> 什么内容能够证明 Condition 成立？

再考虑：

> 哪种来源足够可信。

例如：

```text
客户明确宣布 production deployment
供应商确认 binding purchase
监管规则正文正式生效
可信 sourced report 明确确认订单取消
```

避免把：

> `official / formal`

自动变成所有 Condition 的统一门槛。

---

## 调整 9：Disclosure Consistency 使用 Stage A 的完整 Message Production Model

Compile 不只检查：

> source 类型是不是一样。

而应检查：

```text
未来 criterion 所要求的每一个 predicate
↓
Stage A 认定的信息持有者/发布者是否真的掌握？
↓
这些 predicates 是否处在同一种正常消息的内容边界？
```

这样：

```text
NVIDIA BOM
+
Micron wafer allocation
```

会自然暴露为两个信息生产链。

---

## 调整 10：强化 Observable Comparator

每个相对判断问：

> W2 在 Runtime Projection + 当前消息中具体从哪里得到比较值？

如果答案不明确：

> Criterion 仍未完成。

特别识别：

```text
无保护基线
无上限情景
正常库存
合理水平
```

这种需要模型自行建模的 comparator。

---

## 调整 11：彻底重写 `match_scope` 的思考方式

把 `match_scope` 定义成：

> **Trigger 周围的信息召回包络。**

生成时不要从 criterion 改写。

而要重新回到：

```text
actor / object
+
相关状态变化
+
前兆
+
部分满足信息
+
可能影响 Trigger 判断的消息类型
```

例如 Policy Trigger 是：

> Microsoft 削减 AI server memory configuration。

`match_scope` 可以覆盖：

```text
Microsoft AI server BOM
memory procurement
server deployment plan
cost pressure
order changes
relevant credible supply-chain reporting
```

这些消息多数最终会：

> Activation = FALSE。

这是正常且有价值的。

---

## 调整 12：Match Scope 不承担 Source Precision

召回阶段的目标是：

> 不漏掉可能相关信息。

因此消息来源可以包括现实 message bus 可能接收的：

```text
company
customer
supplier
regulator
credible sourced reporting
industry reporting
```

W2 再用 Condition 判断事实是否充分。

不要让 match_scope 自己承担最终证据裁决。

---

## 调整 13：Compile Quality Gate 增加 Recall / Activation 独立性

完成 Policy 时主动问：

> 如果一条消息与 Trigger 高度相关但尚未满足 Condition，它是否仍可能落入 match_scope？

如果答案普遍为否：

> match_scope 过窄。

这样可以直接防止：

```text
match_scope = activation paraphrase
```

---

## 调整 14：Canonicalization 引入“State Dependency”而不是 Actor-name first

比较：

```text
actor/object 类型
current-state dependency
state transition
Activation Boundary
disclosure structure
decision
```

若：

> Samsung outage 和 SK hynix outage

在当前 Policy 中拥有完全相同的 baseline、Trigger、消息结构和 MU transmission，

可以考虑统一成：

> 单一主要 competitor 的 fab outage Policy

每次消息仍只涉及一个 actor。

若：

> Customer A 尚未削减
> Customer B 已经削减

则必须分开。

这样 canonicalization 会围绕现实状态差异，而不是 entity 数量。

---

# 六、四文件之间的职责重新分配

最终建议形成下面的 instruction architecture：

| 认知内容                                      | 唯一主要归属                       |
| ----------------------------------------- | ---------------------------- |
| DoxAgent 系统目标                             | `agent.md`                   |
| O3 为什么存在                                  | `agent.md`                   |
| O3 对 Runtime 的价值                          | `agent.md`                   |
| 质量目标优先级                                   | `agent.md` + Foundation 简短承接 |
| Gap / Path / Trigger / Condition ontology | `foundation.md`              |
| Expectation Update model                  | `foundation.md`              |
| Causal Layers                             | `foundation.md`              |
| Message Production Model                  | `foundation.md`              |
| Observable Comparator                     | `foundation.md`              |
| Recall vs Activation                      | `foundation.md`              |
| Actor-State Research                      | `trigger_calibration.md`     |
| Targeted Web/Data research                | `trigger_calibration.md`     |
| Historical Disclosure Analogy             | `trigger_calibration.md`     |
| Marginal Expectation Update Test          | `trigger_calibration.md`     |
| Minimality Analysis                       | `trigger_calibration.md`     |
| Trigger Ready 判定                          | `trigger_calibration.md`     |
| Predicate Decomposition                   | `policy_compile.md`          |
| Condition 数量                              | `policy_compile.md`          |
| Criterion normalization                   | `policy_compile.md`          |
| Realization leakage final check           | `policy_compile.md`          |
| Match scope generation                    | `policy_compile.md`          |
| Policy canonicalization                   | `policy_compile.md`          |

避免四份文件都重复：

```text
同一自然消息
一名可识别主体
最早可靠
低自由度
```

否则这些概念会再次获得过高语言权重。

---

# 七、建议的最终认知链

完成重构后，希望一个完全不了解项目的 O3 形成下面的自然工作心智：

```text
我首先知道：
DoxAgent 不希望实时模型重新做研究；
我的价值是提前把未来可能出现的预期差研究成消息级判断标准。

D2 给我：
当前 expectation
+
future revision space

我先问：
现实现在已经走到哪里？

然后研究：
哪些现实对象真正承载下一变化？
它们各自当前是什么状态？

接着判断：
哪一个下一事实第一次改变重要 expectation lever？

再分析：
这个事实属于事件链的哪一层？
还需要哪些事实才真正影响 expectation？
哪些只是后续 realization？

然后研究：
现实世界中谁掌握这个事实？
通常怎样成为消息？
类似事件历史上怎样被披露？

得到：
Candidate Trading Trigger

Compile 再把它拆成：
独立 Boolean predicates

然后得到：
C1 / C2 / ...

每个 Criterion：
只描述 W2 要判断的现实状态
+
需要时的 observable comparator

最后：
match_scope 负责尽量把相关消息召回来
Activation 负责判断真正触发
```

整个系统的认知重点从：

```text
怎样写一个看起来严谨的 Policy？
```

转换成：

```text
真实世界下一条什么消息第一次值得交易，
以及怎样让一个简单 Runtime 模型准确识别它？
```

这应该成为下一版四份 Prompt / Skill 的统一设计中心。
