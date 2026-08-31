# O3 INITIALIZE Internal Skills 重构方案

## 一、重构目标

当前 `initialize.md` 同时承担：

```text
Gap/Path 展开
→ 现实状态补充
→ Calibration
→ Activation Boundary
→ Condition 设计
→ Policy 表达
→ Canonicalization
```

其中真正的问题不是任务步骤太多，而是 **Trigger Research 与 Policy Compilation 的目标相反**：

```text
Trigger Calibration
→ 发散研究现实
→ 找到最早、最小充分的交易变化

Policy Compilation
→ 收敛表达
→ 把已经确定的变化写成 W2 可判断的 Policy
```

当前 Skill 没有把二者充分区分，因此 Agent 容易直接把 D2 `recognition_criteria` 当作 Activation Boundary，再通过增加 shipment、收入、margin、market response 等后续事实获得“可靠性”。

重构后拆成两个 Skill：

```text
initialize_trigger_calibration.md
→ 从 Gap / Tradable Path 研究出真实可交易 Trigger

initialize_policy_compile.md
→ 从已校准 Trigger 编译最终 Policy drafts
```

两者共享 `foundation.md` 的业务语义。

---

# 二、首先需要同步升级 Foundation 的共享定义

两个 Skill 不应分别重新解释 Condition。核心 ontology 应统一放在 `foundation.md`。

## Activation Condition

建议正式定义为：

> **Activation Condition 是相对于当前 `reference_state`，一个仍面向未来、现实中具有合理披露可能的最小充分事实命题。该事实一旦由一条自然消息确认，本身已经足以使目标 ticker 的相关 expectation 产生具有明确方向和交易意义的边际修订，并达到预先可定义的交易判断边界；它不需要证明 thesis 已完整兑现，也不要求排除正常的剩余不确定性。后续判定节点无需补充其他关键事实即可判断其是否成立。**

一个合格 Condition 同时满足六项属性：

### Marginal

相对于当前 baseline，它是新的变化。

### Trade-sufficient

该变化本身已经对 D2 expectation 形成足够明确、可执行的方向性预期差，使 O3 能够预先规定直接交易判断，而不需要等待 thesis 的后续经营结果充分兑现。

这里判断的是：

> **信息是否已经跨过可交易的边际修订边界。**

不是：

> 单一信息是否已经足以证明整个投资 thesis 正确。

允许 Policy 在仍存在合理不确定性的情况下成立。

### Minimal

删除其中任何一个事实后，就不再足以支持同样的直接交易判断。

### Actor-specific

能够明确现实中哪个主体或对象，从什么状态变化到什么状态。

### Disclosure-realistic

这个现实变化本身具有合理发生概率，而且存在正常的信息生产与公开路径。

### W2-judgeable

后续简单 LLM 只依赖消息与 Policy，即可稳定判断 Condition 是否成立。

这六项应该成为两个新 Skill 共同使用的质量标准。

---

# 三、同时增加几个必要共享概念

## 1. Trigger-bearing Actor

真正承载未来状态变化的主体或对象。

可以是：

* 公司；
  -客户；
  -供应商；
  -竞争者；
  -监管机构；
  -某产品；
  -工厂；
  -合同；
  -行业指标。

O3 研究的不是抽象“行业可能怎么样”，而是：

> **当前哪个现实主体的下一项什么变化能够成为消息级 Trigger。**

---

## 2. Realistic Disclosure Trigger

不仅要求某些事实理论上可以出现在同一篇文章，还要求：

> 基于当前具体主体的现实状态，这个现实事件本身具有合理发生可能，并且存在一个自然的信息发布者和消息类型会披露它。

因此：

```text
多个独立核心客户同时削减采购
```

即使理论上能够被某篇综合报道描述，也不应直接视为合理 Trigger。

此时应该研究：

```text
Customer A 当前状态
Customer B 当前状态
Customer C 当前状态
```

再寻找：

```text
A 首次削减
B 在已有削减基础上进一步削减
```

等现实上更可能独立发生的 Trigger。

---

## 3. Direct Trading Sufficiency

Direct Trading Sufficiency 不是要求：

> 单一消息足以证明整个 thesis、排除主要不确定性或保证最终经营兑现。

它回答的是：

> **在 D2 已经完成的研究和当前 reference state 之上，这项新信息是否已经使某个关键 expectation 的概率、时点、规模、实现路径或风险发生足够明确的方向性修订，以至于可以预先规定一个直接交易判断？**

因此，Condition 可以在：

```text
后续 shipment 未知
revenue 尚未兑现
margin 尚未确认
```

时成立。

只要这一信息本身已经跨过了 D3 所研究出的交易边界。

合理的剩余不确定性属于事件驱动交易本身，不应因为它存在就自动继续增加确认条件。

---

# 四、Skill A：`initialize_trigger_calibration.md`

## 核心职责

这个 Skill 只解决：

> **从 D2 Potential Gap / Tradable Path 出发，研究出当前现实中最早、最小充分、消息可实现且可被 W2 判断的 Candidate Trading Trigger。**

它是 INITIALIZE 中研究强度最高的部分。

它不负责最终：

* `title`
* `match_scope`
* `activation_summary`
* Policy canonical wording

这些留给下一 Skill。

---

# 五、Trigger Calibration 的工作流程

## Step 1 — 恢复工作状态

沿用现有 checkpoint 模式。

读取：

```text
task
D2
Reference View
Previous Policy Set
worklist
Calibration checkpoint
wave state
已有工作产物
```

继续当前未完成 Shell。

---

## Step 2 — 理解完整 Shell

继续保持：

> 一个 Shell = 一个认知 wave。

先理解：

```text
core_question
boundary_rule
Units
State
Realization Factors
Potential Gaps
```

目的是知道每个 Gap 在怎样的 expectation system 中产生。

---

## Step 3 — 展开 Tradable Path Surface

继续保留现有 Worklist Gate。

每个 Gap 先明确：

```text
现实对象是谁
→ 当前状态
→ 可能的新状态
→ expected_revision
→ ticker transmission
→ LONG / SHORT
```

Worklist 在任何 Trigger Research 前完整落盘。

但 `d2_boundary_sufficient` 的标准需要提高。

---

# 六、重新定义 `d2_boundary_sufficient`

D2 只有在已经能够直接提供一个：

```text
仍面向未来
+
最小充分
+
actor granularity 合理
+
现实消息有合理披露可能
+
W2 可直接判断
```

的 Trigger Boundary 时，才为 `true`。

尤其明确：

> **D2 `recognition_criteria` 是寻找交易边界的重要研究依据，但不是默认 Activation Condition。**

当前 D2 recognition criteria 很多是为了完整确认 Gap，天然会包含多个阶段和后续兑现，因此其“完整”不意味着无需 Calibration。

---

# 七、Step 4 — 建立 Trigger Surface，而不是直接研究一个抽象 Gap

对于每条 Tradable Path，首先识别：

```text
哪些具体 actors / objects
可能承载下一项 expectation-changing event？
```

例如 D2：

> 多个核心客户削减采购。

O3 首先研究：

```text
Customer A 当前是否仍执行 commitment
Customer B 是否已经开始削减
Customer C 当前采购状态
```

而不是直接把：

```text
多个核心客户削减
```

写成 Trigger。

这一阶段真正需要回答：

> **在当前现实中，哪个具体主体的哪一个下一步变化最有机会首先构成新的预期差？**

---

# 八、Step 5 — 研究 Actor-specific Current State

这一阶段重新定义 Calibration Research 的重点。

原 Calibration 更多研究 baseline、stage、timeline、数据区间。现在应扩展为：

```text
trigger-bearing actor 是谁？
↓
当前已经发生到哪里？
↓
哪些变化已经进入 baseline？
↓
下一项现实状态有哪些合理候选？
```

研究信息优先级仍然保持：

```text
D2
→ Reference View
→ targeted Web Search
→ 必要时 Data MCP
```

但 Web Search 在这个节点中可以比旧 INITIALIZE 更积极，因为：

> actor-level current reality 与 disclosure pattern 本身就是 Trigger Calibration 的核心研究对象。

---

# 九、Step 6 — Candidate Trigger 的核心测试

每一个 Candidate Trigger 必须依次通过以下思考。

## 6.1 Marginality Test

问：

> 这个状态相对于最新确认现实，真的是新的 expectation information 吗？

已经发生或进入 baseline 的状态吸收到 `reference_state`。

---

## 6.2 Counterfactual Trade Test

假设：

> 这条消息现在真实出现，而 shipment、revenue、margin 等后续兑现仍然未知。

这里不是问：

> “仅凭一条消息是否已经足够安全、确定到让我确信 thesis 会兑现？”

而是问：

> **按照 D2 已经完成的研究，这项信息是否已经把相关 expectation 推过一个可以预先规定直接交易判断的边际边界？**

重点观察它是否已经显著改变：

```text
发生概率
实现时点
潜在规模
经济实现路径
关键风险
```

中的一个或多个核心维度。

如果这一变化本身已经构成有方向、有经济意义的预期差：

> 可以继续构造 Trigger，即使后续兑现仍存在正常不确定性。

如果它本身只是一条普通进展，必须依赖额外关键事实才能产生明确 expectation revision：

> 再寻找真正承担交易边界的变化。

这个 Test 的目标是防止两个相反错误：

```text
过早：
普通信息也被当成直接交易信号

过晚：
为了追求确定性，一直等到 shipment / revenue / margin 全部兑现
```

---

## 6.3 Minimality / Deletion Test

如果候选 Trigger 实际是：

```text
A + B + C
```

逐项删除：

```text
没有 C，
A+B 是否已经足以跨过当前交易边界？
```

如果是，删除 C。

继续直到：

> 再删除任何必要事实都会让这个 Trigger 退回普通进展，无法形成同样明确的直接交易判断。

这里判断的是：

> **最小交易充分性。**

不是：

> 对 thesis 最终兑现的最小证明集合。

---

# 十、Step 7 — Actor Granularity Test

对于 D2 中出现的：

```text
多个客户
多个 OEM
多个供应商
多个平台
```

等集合表述，不直接把集合主体作为 Candidate Trigger。

O3 应进一步研究：

```text
哪些具体 actors 当前真正 relevant？
各 actor 当前已经处在哪个状态？
哪个 actor 的下一项独立变化已经足以形成预期差？
```

然后按现实中能够独立发生、独立披露的 Trigger-bearing actor 构造 Candidate Trigger。

例如：

```text
Customer A 首次削减 commitment
```

与：

```text
Customer B 在已有削减基础上进一步削减
```

应分别评估。

D2 的 aggregate Gap 可以对应多个 actor-specific Candidate Triggers；O3 的目标是把抽象 future space 转换成现实消息流中真正可能出现的事件单位。

---

# 十一、Step 8 — Disclosure Plausibility Test

这是新 Skill 最重要的一层之一。

对于 Candidate Trigger，明确回答：

```text
谁最可能发布？
什么类型的消息？
这个发布者是否掌握必要事实？
这些事实是否通常在同一时点披露？
这种现实事件本身是否具有合理发生可能？
```

典型 disclosure routes：

```text
company press release
customer announcement
SEC / filing
regulatory decision
supplier announcement
credible sourced report
```

这里评估的是：

> **真实信息生产过程。**

而不是“理论上能否把若干事实写进同一句话”。

如果无法找到一个与当前具体 actor、状态变化和消息来源相匹配的现实披露路径：

> 继续调整 Trigger Granularity 或重新寻找 Candidate Trigger。

---

# 十二、Step 9 — W2 Judgeability / Comparator Test

最后从下游简单模型视角检查：

> 如果 W2 只有这条未来消息和 Runtime Policy，它是否可以直接判断 TRUE/FALSE？

特别检查：

### 程度词

```text
明显
重大
大幅
广泛
持续
高位
健康
实质
```

若保留，必须有显式 comparator。

例如：

```text
相对于当前约 86% 的 margin guidance baseline
```

而不能让 W2 自己建立比较基准。

### 历史比较

如果 Condition 依赖：

```text
持续下降
进一步扩大
相对此前
```

则未来消息自身或 Policy 必须提供清晰 comparator；不能假设 W2 拥有未注入的历史序列。

---

# 十三、Trigger Calibration 的过程 Checkpoint

仅靠现在五字段 `calibration_log` 已不足以强制 Agent 完成上述推理。

从 Skill 语义上，Stage A 必须形成一个结构化的 **Trigger Calibration checkpoint**。

至少需要记录：

```text
path_id

trigger-bearing actor / object

current reference state

candidate trigger

expectation delta / trade sufficiency basis

minimality conclusion

disclosure route / plausibility

W2 judgeability / comparator basis

resolution status
```

这不是最终 Policy schema。

它的价值是：

> **第二个节点看到的是已经研究好的 Trigger，而不是一句模糊 finding。**

具体是升级现有 `calibration_log` 还是新增过程 artifact，可以在代码设计阶段决定；Skill 层需要先把这一语义合同固定下来。

---

# 十四、Skill A 的完成标准

一个 Path 只有在以下情况之一成立时才完成 Trigger Calibration：

### TRIGGER_READY

至少存在一个 Candidate Trigger，并且通过：

```text
Marginality
Direct Trading Sufficiency
Minimality
Actor Granularity
Disclosure Plausibility
W2 Judgeability
```

### UNRESOLVED

经过针对性研究，仍无法找到现实中可披露且足够形成直接交易判断的 Future Trigger。

特别强调：

> `UNRESOLVED` 比制造一个极晚、极难发生的 Trigger 更好。

但同时：

> **合理的剩余不确定性本身不是 `UNRESOLVED` 的理由。**

只要一个现实变化已经足以形成明确、可预先定义的边际交易判断，就可以进入 Trigger-ready 状态，而无需等待完整 thesis confirmation。

Stage A 的优化目标不是：

```text
尽可能让所有 Path COMPILED
```

而是：

> **为所有可以形成真实 Direct Trading Policy 的 Path 找到可信、足够早且现实可披露的 Trigger。**

---

# 十五、Skill A 的核心思维偏好

应持续引导 Agent：

### 从“如何完整证明 Gap”切换到“哪项信息第一次足以交易”

这是最高原则。

### 从抽象行业事件切换到具体 actor current state

先研究：

> 谁现在在哪？

再研究：

> 谁下一步发生什么？

### 优先现实消息事件，而不是理论状态组合

Policy 最终运行在真实新闻流，不运行在理想化研究报告里。

### 接受事件驱动交易中的合理剩余不确定性

Trigger 的目标是制造足够明确的预期差，不是提前获得最终经营结果的确定证明。

### 不为了确定性把 Trigger 向后推到 realization completion

后续 shipment / revenue / margin / share 往往用于验证 thesis，而不是都应该进入交易 Trigger。

---

# 十六、Skill B：`initialize_policy_compile.md`

## 核心职责

这个 Skill 的输入已经不是原始 Potential Gap，而是：

> **Stage A 已经研究完成的 Candidate Trading Triggers。**

它回答：

> **怎样把这些 Trigger 准确压缩成 supplied Policy schema，使下游 W2 能低自由度判断。**

这个节点的默认工作方式应是：

```text
使用 Trigger Calibration 结论
→ 规范化表达
→ 写 Policy
```

而不是重新从 D2 recognition criteria 推导 Trigger。

---

# 十七、Skill B 的第一条原则：Trigger Calibration 是主要触发语义来源

D2 仍然用于：

* provenance；
* direction transmission；
  -必要的上下文核对。

但：

> **Condition 的现实主体、reference state 和 trigger boundary 以 Stage A 的 Trigger Calibration 为主要输入。**

这样可以避免第二个节点再次“觉得 D2 recognition criteria 更完整”，然后把上一节点的研究成果覆盖掉。

---

# 十八、Policy Compilation 流程

## Step 1 — 读取 Trigger-ready Paths

先重建：

```text
Path
→ calibrated trigger(s)
→ decision
→ D2 provenance
```

只有 Trigger-ready Path 进入正常 drafting。

---

# 十九、Step 2 — Atomic Decomposition

在写 `criterion` 前，先拆解 Candidate Trigger：

> 它实际上包含几个彼此独立、可以分别 TRUE/FALSE 判断的现实事实？

这里必须重新定义：

> **Single Condition 不等于一条长句。**

如果：

```text
A
AND B
AND C
```

是三个独立事实，

即使 Agent 能把它们写进一个 `criterion` 字符串，也仍然是 hidden conjunction。

---

# 二十、Single Condition 与 Multi-condition 的新口径

## Single Condition

表示：

> **一个完整、单义的世界状态命题。**

它可以包含该状态本身不可分割的信息，例如：

```text
Customer A 正式把 binding production commitment
从 X 下调至 Y。
```

这里 X/Y 属于一个合同修改事实。

---

## Multi-condition

当同一自然消息必须同时证明多个**独立可判定事实**，且它们都经过 Minimality Test 证明不可缺少时，显式拆成多个 Conditions。

例如：

```text
C1 = 新规则正式生效
C2 = 同一公告明确把目标产品纳入适用范围
```

如果这两个独立事实确实天然出现在同一 regulatory decision 中，可以使用多条件 Policy。

这比把 C1+C2 藏进一个 criterion 更透明。

多条件仍遵守当前：

> **同一条消息全部满足的 AND 语义。**

---

# 二十一、Step 3 — 构造 Calibration 四字段

对于每个 Condition：

## `reference_state`

必须尽可能是**直接比较锚**：

> trigger-bearing actor / object 当前具体处于什么状态。

避免写成：

> 行业需求仍强，环境复杂……

---

## `trigger_boundary`

只表达：

> 相对于当前 state，哪一步变化构成新的交易边界。

---

## `qualifying_evidence`

只回答：

> 消息中出现什么证据，W2 可以认定 trigger boundary 已经发生。

它不是追加更多经济必要条件的地方。

---

## `criterion`

只表达：

> **W2 最终判断的世界状态。**

---

# 二十二、Criterion 的必要语义要素

不规定固定句式或语法结构。

O3 可以根据不同事件类型自由组织表达，但一个可用的 `criterion` 必须让 W2 清楚识别以下必要语义：

### 1. Trigger-bearing actor / object

谁或什么发生了变化。

### 2. Future state change

相对于 `reference_state`，具体发生了什么新的状态跃迁。

### 3. Necessary scope

如果同一动作只在特定产品、客户、地区、合同、时间窗口等范围内才具有当前交易含义，应明确该适用范围。

### 4. Comparator

凡涉及：

```text
增加 / 减少
提前 / 延后
进一步
明显 / 重大 / 大幅
高位 / 低位
持续变化
```

等相对或程度判断时，需要让 W2 知道比较锚是什么。

Comparator 可以来自：

```text
当前 reference_state
最新正式 guidance
当前 commitment
当前 timeline
当前明确数值
可比历史区间
```

Criterion 不要求按固定顺序出现这些语素，也不要求每条 Policy 都机械包含四类文本；只要求其实际判断所依赖的必要语义在表达中不存在缺口。

---

# 二十三、程度词规则

以下表达若无 comparator：

```text
明显
重大
大幅
广泛
持续
高位
健康
实质
```

应视为尚未完成 Calibration。

优先级：

```text
明确业务状态
>
与当前 reference_state 比较
>
与最新正式 guidance / commitment / timeline 比较
>
与可比历史范围比较
>
开放式程度判断
```

如果不能完全消除程度词，至少让 W2 明确知道：

> **和什么比较，以及这个程度词具体修饰什么现实变化。**

---

# 二十四、Realization Leakage Test

逐条检查：

> Criterion 是否把“为什么这个 Trigger 对 ticker 重要”的后续兑现重新写回触发条件？

典型：

```text
客户采用
+
Micron shipment增长
+
收入改善
+
利润改善
```

如果 Customer Adoption 本身已经在 Stage A 被证明 Direct-Trade Sufficient：

> 后面这些 realization evidence 不再进入 criterion。

D2 expected revision 与 `decision` 已经解释了为什么它重要。

---

# 二十五、Disclosure Consistency Test

Policy Compile 不重新研究“谁会发”，但必须检查：

> 最终 criterion / qualifying_evidence 是否仍符合 Stage A 确认的 disclosure route。

如果 Stage A 判断：

```text
客户公告可以自然确认 A
```

Compile 不能偷偷把：

```text
Micron shipment
industry inventory
```

再加进去。

发现这种问题时，应先修正 Trigger Calibration 结论，再继续 drafting，而不是通过扩大 Condition 解决。

---

# 二十六、`match_scope` 仍然最后写

这一设计继续保留。

顺序：

```text
Trigger
→ Condition
→ Calibration
→ decision
→ title
→ activation_summary
→ match_scope
```

避免从：

> “哪些新闻和这个主题有关？”

反向推导 Activation Condition。

---

# 二十七、Canonicalization 的新口径

当前 canonicalization 不能只比较 abstract economic theme。

应该比较：

```text
trigger-bearing actor / object
+
state transition
+
Activation Boundary
+
decision
```

如果两个具体 actors 能够：

-独立发生；
-独立披露；
-各自独立产生足够 expectation delta；

则保持不同 Policies。

例如：

```text
Customer A 首次削减 commitment
```

与：

```text
Customer B 进一步削减 commitment
```

即使都来自同一 D2 Gap、同一 SHORT transmission，也应保持独立。

只有底层现实 Trigger 本身、经济边界和方向实质相同时才合并。

---

# 二十八、Skill B 的 Progressive Checkpoint

Stage B 继续使用当前 Policy progressive write 模式。

每个 Trigger 编译完成后：

```text
写 Policy draft
→ 更新对应 Path mapping
→ 再处理下一 Trigger / Path
```

但完成状态不能只意味着：

> schema valid。

还应该意味着：

```text
Condition 没有 hidden conjunction
Actor granularity 与 Stage A 一致
Comparator 完整
Criterion / evidence 分工明确
没有 realization leakage
符合 disclosure route
W2 可判断
```

---

# 二十九、Skill B 的完成标准

一个 Trigger 只有满足以下条件才可形成 Policy Draft：

1. Stage A 已标记 Trigger-ready；
2. 每个 Condition 表达独立可判定状态；
3. 没有把多个通常跨主体/跨时点事实隐藏进一个 Condition；
4. `reference_state` 是直接比较锚；
5. 所有相对/程度判断存在 comparator；
6. `criterion` 表达清楚完成判断所需的主体、状态变化及必要 scope/comparator；
7. `qualifying_evidence` 只承担事实确认标准；
8. 不把 downstream realization 当成 Trigger；
9. Condition 与现实 disclosure route 相符；
10. 后续 W2 不需要额外研究或隐藏上下文即可判断。

完成所有 Trigger 后再关闭当前 Shell wave，并与后续 Final Global Pass 接续。

---

# 三十、两个 Skill 之间最重要的职责边界

可以压缩成：

```text
Skill A
研究：
“什么消息值得交易？”

Skill B
编译：
“怎样让简单模型准确判断这条消息？”
```

更具体：

```text
Trigger Calibration
负责：
actor
current state
expectation delta
trade sufficiency
minimality
disclosure realism
judgeability

Policy Compile
负责：
condition decomposition
criterion
calibration fields
comparator
language normalization
match_scope
canonicalization
draft
```

两者不要重复承担对方的核心工作。

---

# 三十一、现行 `initialize.md` 内容如何迁移

| 当前内容                                       | 新归属                         |
| ------------------------------------------ | --------------------------- |
| 恢复 workspace                               | 两个 Skill 各保留适合自己的恢复逻辑       |
| Shell 认知 Wave                              | Trigger Calibration         |
| Worklist Gate                              | Trigger Calibration         |
| D2 sufficiency                             | Trigger Calibration，定义升级    |
| Reference View / Web / Data MCP            | Trigger Calibration         |
| 当前 Calibration Research                    | Trigger Calibration，整体重写    |
| earliest reliable boundary                 | Trigger Calibration         |
| Actor / Minimality / Disclosure / W2 Tests | **新增至 Trigger Calibration** |
| Single/Multi-condition                     | Policy Compile，整体重写         |
| Calibration 四字段                            | Policy Compile              |
| criterion 表达                               | Policy Compile              |
| title / summary / match_scope              | Policy Compile              |
| Progressive Policy Drafting                | Policy Compile              |
| Local Canonicalization                     | Policy Compile              |
| Wave completion                            | Policy Compile              |

---

# 三十二、整个新 INITIALIZE 的认知流程

最终希望 Agent 形成的不是：

```text
Gap
→ Recognition Criteria
→ Policy
```

而是：

```text
D2 Gap
↓
Tradable Path
↓
当前具体 trigger-bearing actors 是谁
↓
各 actor 当前在哪里
↓
谁的哪一个下一变化最早制造预期差
↓
Counterfactual Trade Test
↓
Deletion Test
↓
Actor Granularity Test
↓
Disclosure Plausibility Test
↓
W2 Judgeability Test
↓
TRIGGER READY
────────────────────
↓
Atomic Decomposition
↓
Single / Multi-condition
↓
reference_state
trigger_boundary
qualifying_evidence
criterion
↓
Comparator / language normalization
↓
Realization Leakage Test
↓
Disclosure Consistency Test
↓
title / activation_summary / match_scope
↓
Canonicalization
↓
POLICY DRAFT
```

这条链条才真正体现 O3 的独立价值：

> **D2 研究“什么未来变化会改变 expectation”；O3 首先研究“其中哪个现实消息状态已经足以形成直接交易判断”，然后才把它编译成 Policy。**

如果这两个新 Skill 按这个职责边界来写，能够直接针对 MU Pilot 暴露出的四个核心失败：

> **Single Condition 作弊、Recognition Criteria 复制、Synthetic Message、模糊且低可判定的 Criterion。**
