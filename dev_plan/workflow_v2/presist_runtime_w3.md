# DoxAgent V2 W3 值班专家 Agent 开发方案

## 1. W3 的定位

W3 是 Persistent Runtime 中的 **Codex SDK 值班交易专家 Agent**。

W1/W2 解决的是：

```text
W1
已知现实中，这条消息是不是新的？

W2
现行 PolicySet 中，是否已经存在规则覆盖这条消息？
```

W3 解决的是：

```text
一个新的或疑似新的现实已经出现
+
现行 Policy 没有覆盖或疑似没有覆盖

→ 这个例外现实是否形成可交易的信息差？
```

因此 W3 的核心不是：

```text
low confidence 二层审核
```

而是：

> **处理 PolicySet 之外的新现实。**

`low` 只是部分 Case 进入 W3 时需要先修复 W1/W2 判定的原因。

---

# 2. W3 的上下游关系

完整实时关系：

```text
Message
   │
   ├───────────────┐
   ↓               ↓
  W1              W2
Novelty         Policy
   │               │
   └───────┬───────┘
           ↓
     Runtime Router
           │
           ├── Normal Route
           │
           └── W3 Route
                  ↓
           W3 Codex SDK Agent
                  ↓
        ┌─────────┴─────────┐
        ↓                   ↓
   Existing Route       NEW + NO POLICY
                            ↓
                      Expert Evaluation
                            ↓
                     TRADE / NO_TRADE
```

W3 最终产生的新事实仍进入 Runtime Delta → O2 闭环。

W3 产生的 direct trade 以及 W3 发现的 Policy coverage gap，则作为 Runtime feedback 提供给后续 O3 MAINTAIN。

---

# 3. W3 Route 的业务范围

W3 主要承接两类 Case。

## Mode 1 — `UNCOVERED_NEW`

典型输入：

```text
W1:
NEW
normal

W2:
no policy hit
normal
```

这是最重要、最标准的 W3 Case：

> **已经确认是新现实，同时已经确认当前 PolicySet 没有覆盖。**

W3 不重新执行 W1/W2。

直接进入：

```text
事件澄清
→ Prior Expectation
→ Expectation Delta
→ Tradability
```

---

## Mode 2 — `REVALIDATE_THEN_EVALUATE`

适用于：

> 当前 Case 有可能最终属于 `NEW + NO POLICY`，但 W1 或 W2 的判断存在真实歧义。

例如：

```text
NEW / low
+
no policy / low
```

或者其他满足当前 W3 路由逻辑、仍然存在：

```text
NEW
+
NO POLICY
```

可能性的 Case。

Mode 2 先重新判定 W1/W2。

如果修正后已经可以落入普通 Runtime 路径，则结束 W3 研究任务。

如果修正后仍然是：

```text
NEW
+
NO POLICY
```

则**在同一个 W3 request/turn 中直接继续 Mode 1 的后半段**。

不重新启动第二个 W3 Node。

---

# 4. W3 执行模型

W3 采用：

```text
single ticker
+
one persistent main thread
+
one case = one Codex turn/request
```

即：

```text
MU
→ 固定 W3 主 Thread

Case A
→ turn 1

Case B
→ turn 2

Case C
→ turn 3
```

其目的不是把 Thread 当数据库，而是让同一个 ticker 的值班专家保持持续的角色、上下文认知和近期处理经验。

当前 V2 通用 Codex thread identity 仍然带 `run_id`，本质上属于 run-scoped thread；因此为了实现真正的 ticker-level W3 固定 Thread，本轮需要对 W3 的 thread binding/lifecycle 做专门的架构调整，而不是机械复用现有每 run 新 thread 的模式。

具体 thread persistence 如何落到当前 repository/schema，由 Codex 根据现有 runtime infrastructure 实现，本方案不提前规定数据库字段。

---

# 5. 主 Thread 与 Fallback Thread

W3 **不采用 FIFO 等待主 Thread** 的并发模式。

正常：

```text
Message A
↓
Main W3 Thread
```

如果 Main W3 正在处理 A，此时 Message B 也进入 W3：

```text
Message A
↓
Main W3 Thread

Message B
↓
Fallback Thread 1
```

Fallback Thread：

* 使用与 Main W3 完全相同的 Agent Prompt；
* 使用对应 Mode 的相同 Internal Skill；
* 接收相同方式准备的 workspace 上下文；
* 只服务当前这一条 Case；
* Case 完成以后即结束；
* 不替代 Main W3；
* 不成为下一条消息的默认 Thread。

Main W3 完成 A 后：

```text
Message C
↓
仍然回到 Main W3 Thread
```

极端情况下：

```text
Main W3 → A
Fallback 1 → B
Fallback 2 → C
Fallback 3 → D
```

允许根据并发 Case 数量创建多个一次性 fallback thread。

因此业务原则是：

> **Main Thread 优先；只有主 Thread 正忙时，当前 Case 才获得一次性 Fallback Thread。**

不为了保持 thread history 而让时效性敏感的新消息等待前一条研究完成。

---

# 6. Fallback Thread 上下文一致性

Fallback 并不是低质量降级路径。

其差异仅在：

```text
没有 Main Thread 的历史 conversation state
```

它必须获得与主 Thread 当前 Case 相同的显式上下文基础，包括：

```text
当前 Published D1
当前 Published D2
当前完整 PolicySet
当前 Reference View
当前 Case task context
Agent Prompt
对应 Mode Internal Skill
```

其中：

```text
Reference View
```

作为 W3 对当前 Event / Fact 现实状态的完整参考上下文；

```text
完整 PolicySet
```

作为 W3 对当前全部 Policy 及其 Calibration 的完整参考上下文。

W3 不采用 W1/W2 那种：

```text
Index / Projection
→ selected Detail
```

的分层上下文控制方式。

因此：

> W3 的关键业务判断不能依赖“Main Thread 恰好记得某件事”才能正确执行。

Thread continuity 是增强。

Published artifacts 与当前 Case context 才是正式输入。

---

# 7. Thread 与 Published Artifact 冲突

本轮不为 W3 单独设计新的：

```text
thread memory
vs
artifact version
```

冲突解决机制。

继续沿用当前 V2 Codex 架构的通用原则和现有处理方式。

即：

> Thread 负责连续执行，Published artifact / 显式输入负责业务事实边界。

后续如果要整体重构 V2 Agent 的 persistent-thread memory / artifact version precedence，再对 D1/D2/O2/O3/W3 一并设计，不在 W3 单点创造新的规则。

---

# 8. 上下文注入方式

W3 沿用当前 V2 Codex Agent 通用执行方式。

W3 同样保持：

```text
Agent-level:
W3 Agent Prompt

Node-level:
Mode-specific Internal Skill

Current Case:
Task Prompt / User Prompt
```

不为 W3 创造独立的 Prompt 体系。

---

# 9. Agent Prompt 与 Internal Skill 分工

## W3 Agent Prompt

跨所有 Case、所有 Mode 保持稳定。

只定义：

```text
W3是谁
W3为什么存在
W3负责判断什么
什么叫可交易信息差
它与W1/W2/O3的关系
它应该采取怎样的交易判断倾向
最终需要完成什么
```

不放具体 Mode 工作流。

---

## Mode 1 Internal Skill

负责：

```text
新且无 Policy 的消息
→ 如何快速研究
→ 如何恢复 Prior Expectation
→ 如何计算 Expectation Delta
→ 如何作 TRADE / NO_TRADE 判断
```

---

## Mode 2 Internal Skill

前半部分增加：

```text
W1/W2 re-adjudication
```

一旦得到：

```text
NEW + NO POLICY
```

其后半部分与 Mode 1 保持完全相同的交易评价框架。

两个 Skill 不应形成两套不同的：

```text
surprise
materiality
transmission
trade
```

判断标准。

Mode 差异只存在于前半段。

---

# 10. Case 上下文放入 Task Prompt

每条 W3 Case 的动态信息统一组装进入该次：

```text
task prompt / user prompt
```

Task Prompt 可以包含当前 Case 实际需要的：

```text
Mode
消息正文及已有 metadata
W1 最终输出
W1 reason
W1 reference_ids
W2 最终输出
W2 reason
W2 policy_ids
必要的 Runtime route 信息
```

与此同时，W3 执行环境直接提供：

```text
当前 Published D1
当前 Published D2
当前完整 PolicySet
当前 Reference View
```

其中完整 PolicySet 已包含当前 Policy 的完整 Activation Conditions 与 Calibration；Reference View 则作为当前 Event / Fact 现实状态的完整参考。

这样 Main Thread 连续处理不同 Case 时：

> 每次新 turn 的主要变化就是新的 Case Task Prompt。

也符合 W3 的使用特点：

```text
长期 Agent 身份基本不变
长期研究 artifact 基本稳定
真正频繁变化的是 Case 本身
```

---

# 11. Workspace

Workspace 继续使用当前 V2 Codex SDK workflow 的通用架构。

本方案只提出业务需求：

W3 执行时能够读取当前版本的：

```text
Document 1
Document 2
完整 PolicySet
Reference View
```

即可。

Mode 2 重新核验 W1/W2 时，直接使用：

```text
Reference View
+
完整 PolicySet
```

作为当前事实与 Policy 的完整判断上下文，无需再按照 W1/W2 的模式逐级请求 Event Detail 或 Policy Detail。

---

# 12. Mode 2 — 第一阶段：修复 W1/W2 判定

Mode 2 首先只解决：

```text
这条消息到底是不是 NEW？
到底有没有现行 Policy 覆盖？
```

这一阶段不做交易研究。

---

## 12.1 优先复核 W1/W2 原判断

Runtime 已经把：

```text
W1 result
W1 reasoning
W1 reference_ids

W2 result
W2 reasoning
W2 policy_ids
```

交给 W3。

所以首先判断：

```text
当前消息
vs
W1 原新旧判断

当前消息
vs
W2 原 Policy 判断
```

W3 同时已经拥有：

```text
Reference View
+
完整 PolicySet
```

因此能够直接检查：

> 当前 Reference View 中是否已经存在覆盖消息核心事实的 Event / Fact；

以及：

> 当前完整 PolicySet 中是否存在真正满足 Activation Standard 的 Policy。

---

# 13. Mode 2 — W1 重新校验

W3 使用：

```text
当前消息
+
W1 原结果和 reason
+
W1 reference_ids
+
Reference View
```

重新判断：

```text
NEW
or
OLD
```

核心仍然沿用 W1 的 factual novelty 语义：

```text
消息全部核心事实已经被当前现实覆盖
→ OLD

存在尚未被覆盖的核心事实
→ NEW
```

W3 可以利用 Reference View 中的完整近期 Event / Fact 上下文完成这一判断。

---

# 14. Mode 2 — W2 重新校验

W3 使用：

```text
当前消息
+
W2 原结果和 reason
+
W2 policy_ids
+
完整 PolicySet
```

重新判断：

```text
policy hit
or
no policy hit
```

W3 需要同时理解：

```text
match_scope
activation_conditions
criterion
reference_state
trigger_boundary
qualifying_evidence
```

因此完整 PolicySet 已经提供足够的判定上下文。

W3 与 W2 一样：

```text
match_scope
→ Candidate

criterion / calibration
→ Hit
```

不因为消息与 Policy 主题相关就判断命中。

---

# 15. W3 Re-adjudication 不输出 Confidence

W3 接管以后不再输出：

```text
low
normal
```

W3 的任务就是：

> 在允许读取更多上下文、使用 Codex Agent 能力的情况下给出当前最佳判断。

所以 Mode 2 最终得到：

```text
NEW | OLD
reference_ids[]

policy_ids[]
```

以及简短判定依据。

如果仍存在残余不确定性：

> W3 仍选择当前证据下最合理的最终结果，而不是重新创造一个新的 escalation level。

---

# 16. Mode 2 修正后的路由

W3 得到修正结果以后：

### OLD + no Policy

```text
ARCHIVE
```

### OLD + Policy hit

```text
ARCHIVE
+
BADCASE
```

### NEW + Policy hit

```text
进入现有 Policy 执行路径
```

交易方向由 Canonical PolicySet 决定。

W3 不重新决定：

```text
LONG / SHORT
```

### NEW + no Policy

不退出 W3。

直接在**同一个 request/turn**继续 Mode 1。

---

# 17. Mode 1 的核心任务

Mode 1 解决：

> 一个已经确认的新现实，在现有 PolicySet 没有覆盖的情况下，是否形成即时可交易的信息优势？

核心认知链：

```text
Observed Reality
        ↓
Prior Expectation
        ↓
Expectation Delta
        ↓
Materiality / Transmission
        ↓
TRADE / NO_TRADE
```

这应该成为 W3 Skill 的核心结构。

---

# 18. Step 1 — Clarify Observed Reality

先理解：

> **现实到底发生了什么？**

使用原消息以及有限 Web Search 补充必要 Detail。

重点确认：

```text
主体
动作
产品 / 项目
客户
规模
商业阶段
时间
确定性
官方确认程度
关键数值
```

Web Search 的目标是：

> 补齐当前 Event 本身。

如果原消息已经足够：

> 直接进入后续判断。

---

# 19. Web Search 分成两个目的

W3 必须区分：

## Search A — Event Detail

回答：

> 这条消息到底发生了什么？

---

## Search B — Pre-event Expectation

回答：

> **在这条消息出现之前，市场已经知道什么？**

这一区分非常重要。

否则事件发生以后产生的大量新报道会把：

```text
post-event information
```

污染成：

```text
prior expectation
```

W3 在搜索市场原有预期时，应明显偏向：

```text
消息发生前已经公开的资料
此前时间表
此前传闻
此前行业判断
此前管理层/客户表态
此前市场共识
```

而不是拿事件公布后的报道来证明：

> “市场原本就知道。”

---

# 20. Step 2 — Reconstruct Prior Expectation

这是 W3 Mode 1 最重要的一步。

W3 需要建立：

> **消息出现前，关于当前现实变量的合理 expectation baseline 是什么？**

证据来源：

```text
Document 1 全部相关研究板块
+
Document 2
+
Reference View
+
必要的 pre-event Web Search
```

---

# 21. Document 1 的使用方式

D1 的各个相关组成需要**同等作为 Prior Expectation 的研究基础**。

当前 Global Research 中：

* C1 研究公司基本面、近期变化、管理层/卖方口径、核心驱动和业务→财务传导；
* C3 研究行业、价值链、外部主体、商业化里程碑与产业驱动；
* C5 研究市场隐含预期。

W3 应根据当前消息涉及的问题，同时使用其中所有真正相关的研究。

例如一条供应链消息：

```text
C1
→ 对公司财务与业务意味着什么

C3
→ 行业与价值链里的现实状态是什么

C5
→ 市场当前已经反映了多少
```

三者是互补关系。

不存在默认优先级。

---

# 22. Document 2 的使用方式

D2 是 W3 恢复当前 expectation state 的重要结构化依据。

当前 D2 的 Unit 已经包含：

```text
State
Realization Factors
Potential Gaps
```

其中 State 表示当前变量和基线，Realization Factors 表示传导路径，Potential Gaps 表示哪些未来变化会迫使 expectation 修订。

W3 不需要重新执行 D2。

它只需要找到：

> **与当前新消息真正相关的 expectation variables。**

---

# 23. Reference View 的使用方式

Reference View 用于恢复：

> D1/D2 之后、截至当前消息之前，现实又发生了哪些值得纳入 baseline 的近期变化。

它同时提供 W3 对当前 Event / Fact 状态的完整现实参考，因此既用于 Mode 2 的新旧重判，也用于 Mode 1 的 Prior Expectation 重建。

它主要帮助 W3 防止：

```text
D2 baseline
已经被最近几天现实推进
但新消息其实只是合理延续
```

这种误判。

---

# 24. Prior Expectation 的目标不是精确概率

W3 不需要输出：

```text
市场原概率 42%
事件后变为 71%
```

除非已有可靠数据明确支持。

真正需要的是类似：

```text
当前市场仍主要把该客户视为 qualification 阶段，
量产时点集中在2027年之后；

或

当前市场已经普遍预期这项监管放松，
正式公告只是确认已知路径。
```

也就是：

> **足够支撑 Surprise 判断的 baseline。**

---

# 25. Step 3 — Expectation Delta

建立 Prior Expectation 后比较：

```text
New Reality
-
Prior Expectation
```

重点判断变化发生在哪个维度：

```text
方向
规模
时间
确定性
商业阶段
现实实现概率
价值获取主体
财务/产业传导
```

例如：

```text
Prior:
2027 才可能进入规模生产

New:
2026 Q4 已获得大批量商业采购

Delta:
时间显著提前
+
商业确定性明显提升
```

不需要重新写一个完整 thesis。

---

# 26. Step 4 — Tradability

W3 最终交易判断聚焦：

```text
Surprise
×
Materiality
×
Transmission Directness
```

## Surprise

这条消息是否明显超出此前合理 expectation？

## Materiality

被改变的变量对目标 ticker 是否足够重要？

## Transmission Directness

是否存在清晰的：

```text
Event
→ Expectation
→ Company / Industry / Value Capture
→ Ticker
```

传导？

如果三个条件足够明确：

> W3 应当敢于给出 TRADE。

---

# 27. NO_TRADE 的正确来源

W3 的 NO_TRADE 应主要来自：

```text
消息基本符合已有预期

消息虽然新，但经济重要性不足

事件方向无法形成明确 ticker transmission

信息主要是可能性/猜测，而不是足够形成现实修订的事实

消息虽然重要，但无法判断对目标 ticker 的方向
```

这能压制 Codex SDK 通用研究 Agent 常见的保守退避。

---

# 28. W3 的职责边界

Agent Prompt 应明确告诉 W3：

> W3 的任务是评估当前新信息是否形成具有方向性的可交易信息优势。

重点关注：

```text
Surprise
Materiality
Transmission
Direction
```

W3 最终需要对这些维度形成明确判断，并给出：

```text
TRADE / NO_TRADE
```

若 TRADE：

```text
LONG / SHORT
```

---

# 29. Research Depth

W3 是时效敏感的 Runtime Agent。

因此研究原则是：

> **足够形成明确判断即可停止。**

优先回答：

```text
事件是什么？
此前预期是什么？
哪里发生偏差？
这个偏差重要吗？
方向是什么？
```

如果已经足以 TRADE / NO_TRADE：

> 停止继续搜索。

---

# 30. W3 Delta

如果 W3 最终确认：

```text
NEW
```

且当前 Case 需要进入 Event Library 增量闭环，则由 W3 自己输出：

```text
RuntimeFactCandidate[]
```

Schema 与 W1 Round 3 使用的 Candidate Schema 完全一致。

O2 继续负责后续正式的 Event / Fact canonicalization 与增量维护。

---

# 31. 为什么 W3 自己写 Delta

如果 Mode 2 发现：

```text
W1 原判断有误
```

那么继续让 W1 根据被 W3 推翻的上下文生成 Delta 容易产生语义不一致。

因此：

> **W3 一旦成为最终 novelty authority，就由 W3 自己完成同一 Case 的 atomic Fact extraction。**

这样：

```text
最终 NEW 判断
+
新增 Facts
```

来自同一个 Agent 上下文。

---

# 32. W3 输出原则

W3 不输出 Confidence。

建议使用一个统一 Case Result，而不是 Mode 1 / Mode 2 两套完全不同 Schema。

概念结构：

```json
{
  "novelty": {
    "result": "NEW",
    "reference_ids": [],
    "reason": "..."
  },

  "policy": {
    "policy_ids": [],
    "reason": "..."
  },

  "expert_trade": {
    "evaluated": true,
    "trade": true,
    "direction": "LONG",
    "prior_expectation": "...",
    "expectation_delta": "...",
    "reason": "..."
  },

  "delta_candidates": []
}
```

最终字段名可结合当前项目 Schema 风格确定，但业务语义建议保持以上四块。

---

# 33. `novelty`

Mode 2：

> 由 W3 重新判定。

Mode 1：

> 继承已经明确的：

```text
NEW
```

但最终 Output 仍回显，便于统一编排。

---

# 34. `policy`

输出：

```text
policy_ids[]
```

如果 W3 Mode 2 发现现有 Policy 实际已经覆盖：

```text
["pol_xxx"]
```

若无：

```text
[]
```

多个 Policy 时继续使用：

> 最匹配的排在第一。

---

# 35. `expert_trade`

只有最终：

```text
NEW
+
NO POLICY
```

时：

```text
evaluated = true
```

并产生：

```text
trade = true | false
direction = LONG | SHORT | null
prior_expectation
expectation_delta
reason
```

其中：

### `prior_expectation`

用极短文本说明：

> 消息之前市场大致相信什么。

### `expectation_delta`

说明：

> 新消息相对此前 expectation 改变了什么。

这两个字段既提供最小审计，也能强迫 W3 真正执行：

```text
Prior
→ Delta
```

而不是直接看到一条“利好新闻”就判断 LONG。

---

# 36. Existing Policy 命中时不运行 Expert Trade

如果 Mode 2 修正结果：

```text
NEW
+
policy_ids != []
```

则：

```text
expert_trade.evaluated = false
```

后续：

> 按现有 Policy 执行。

W3 不重新改变 O3 已经决定的 LONG / SHORT。

---

# 37. Delta Candidates

最终：

```text
NEW
```

并需要进入日终 O2 增量的 Case：

```text
delta_candidates[]
```

使用 W1 Round 3 已冻结的相同业务 Schema。

最终：

```text
OLD
```

则为空。

---

# 38. W3 Direct Trade

W3 会首次引入：

> **没有现行 Policy ID 的交易判断。**

因此 Runtime Trade Record 必须能够区分：

```text
decision_origin = POLICY
```

和：

```text
decision_origin = W3
```

概念关系：

```text
POLICY TRADE
→ policy_id exists

W3 DIRECT TRADE
→ policy_id absent
→ w3_case reference exists
```

具体数据库字段和迁移方式由实现时结合当前 TradeRecord 设计处理，本方案不强制规定底层表结构。

---

# 39. W3 Direct Trade 与 O3 MAINTAIN

W3 Direct Trade 是 O3 最重要的一类维护输入：

> 现实中出现了一条值得交易的信息，但 PolicySet 没有提前覆盖。

因此日终：

```text
W3 Trade Record
+
Reference View Delta
+
其他 Trade / BADCASE
↓
O3 MAINTAIN
```

O3 可以据此判断：

```text
是否只是一次特殊事件

还是暴露了新的持续监测路径

是否需要修改已有 Policy

是否需要建立新的 Policy
```

W3 只提供 Case 和判断。

最终 Policy 仍由 O3 编译。

---

# 40. Agent Prompt 写作方向

W3 Agent Prompt 应保持较短。

主要包含：

### 身份

> W3 是 V2 Persistent Runtime 的值班交易专家。

### 核心职责

> 处理新的、现行 Policy 未覆盖或疑似未覆盖的现实。

### 决策目标

> 判断信息相对当前 expectation 是否形成有方向、足够重要、能够传导至目标 ticker 的交易机会。

### 判断倾向

> 对信息优势做积极而明确的判断，不因为普通市场风险退回 NO_TRADE。

### 输出

> 必须完成当前 Case 最终判断，不输出 confidence。

具体流程放 Internal Skill。

---

# 41. Mode 1 Internal Skill 的核心内容

按顺序指导：

```text
1. 理解原消息
2. 必要 Web Search 补齐 Event Detail
3. 读取 D1 各相关板块
4. 读取 D2 相关 Unit / State / Factors
5. 读取 Reference View
6. 必要时搜索消息前的公开市场预期
7. 重建 Prior Expectation
8. 比较 New Reality
9. 得到 Expectation Delta
10. 评估 Surprise / Materiality / Transmission
11. TRADE / NO_TRADE
12. 如需，生成 RuntimeFactCandidate[]
```

Skill 应强调：

> 搜索和文件阅读应围绕当前 Case 收敛，而不是扩大成完整研究项目。

---

# 42. Mode 2 Internal Skill

前半段：

```text
1. 读取 W1/W2 原判断及理由
2. 使用 Reference View 重新核验消息的新旧
3. 使用完整 PolicySet 重新核验 Policy 是否命中
4. 得出最终 NEW / OLD
5. 得出最终 Policy hit / no hit
```

随后：

```text
if NEW + NO POLICY:
    继续执行与 Mode 1 完全相同的后半段
else:
    返回相应普通 Runtime 结果
```

这里必须在同一 Agent turn 中完成。

---

# 43. Task Prompt

每个 Case 的 Task Prompt 只负责把这次事件说清楚。

建议包含：

```text
Current Mode

Current Message

W1 Result
W1 Reason
W1 Reference IDs

W2 Result
W2 Reason
W2 Policy IDs

Runtime 期望 W3 完成的当前任务
```

W3 同时通过当前 V2 Agent 上下文机制获得：

```text
当前 Published D1
当前 Published D2
当前完整 PolicySet
当前 Reference View
```

这样无需为 W3 重复设计 W1/W2 的按 ID Detail 请求流程。

---

# 44. W3 初始开发边界

本轮实现：

```text
W3 route eligibility

Mode 1 / Mode 2 dispatch

ticker-level Main W3 Thread

concurrency fallback threads

W3 case task assembly

Agent Prompt

Mode 1 Internal Skill

Mode 2 Internal Skill

W3 structured result

Mode 2 → normal route integration

W3 direct TRADE / NO_TRADE

W3 RuntimeFactCandidate generation

W3 trade feedback → O3 MAINTAIN
```

---

# 45. 开发阶段

## Phase 1 — W3 Case Contract

先冻结：

```text
Mode
W1/W2 adjudication input
message input
最终 W3 result
```

保证 Mode 1 / Mode 2 使用统一 Case 基础。

---

## Phase 2 — W3 Thread Lifecycle

实现：

```text
ticker → persistent Main W3 Thread
```

并使：

```text
case
→ main thread turn
```

跨多个 Case 继续。

同时保持当前 V2 Published artifact / thread 的一般事实边界。

---

## Phase 3 — Fallback Thread

增加：

```text
main busy
→ ephemeral fallback
```

并保证：

```text
fallback context
=
main case explicit context
```

Fallback 完成即结束。

后续 Case 仍优先 Main。

---

## Phase 4 — Mode 2

先实现 W1/W2 repair：

```text
Current Message
+
Reference View
+
完整 PolicySet
+
W1/W2 原判断
↓
final novelty / policy verdict
```

并接回现有 Router。

这是最容易独立验证的一部分。

---

## Phase 5 — Mode 1 Research

实现：

```text
Event clarification
↓
Prior Expectation reconstruction
↓
Expectation Delta
↓
Tradability
```

重点保证：

> D1 各相关研究部分被共同参考，而不是只阅读市场隐含预期。

---

## Phase 6 — W3 Trade + Delta

支持：

```text
NEW + NO POLICY
→ W3 TRADE / NO_TRADE
```

以及：

```text
NEW
→ RuntimeFactCandidate[]
```

接入现有日终 DeltaBatch 入口。

---

## Phase 7 — Feedback Integration

保证：

```text
W3 direct trade
+
W3发现的Policy coverage gap
```

能进入当前 O3 MAINTAIN 所使用的 Runtime feedback。

---

# 46. Pilot / Eval

W3 不能只测试：

> W1/W2 故意判错的简单 Case。

重点应该是两类真实难题。

### Mode 2

测试：

```text
W1选错旧Event

W1漏掉真正旧Event

W2选错相似Policy

W2漏掉真正Policy

NEW/OLD genuinely ambiguous

Policy boundary genuinely ambiguous
```

目标：

> W3 能基于完整 Reference View 与完整 PolicySet，把 Case 拉回正确 Normal Route。

---

### Mode 1

测试：

```text
明显新且正向超预期

明显新且负向超预期

新但完全符合已有预期

新且重要，但无法形成ticker方向

新闻标题很大但经济意义弱

D1/D2原预期已经很高，因此表面利好没有surprise

此前市场预期弱，但一个普通事件实际显著提前realization
```

这部分才决定 W3 有没有真正的 event-driven alpha judgment 能力。

---

# 47. 重点评估指标

Mode 2：

```text
W1 correction accuracy
W2 correction accuracy
Normal-route recovery accuracy
false W3 continuation rate
```

Mode 1：

```text
TRADE / NO_TRADE precision

LONG / SHORT direction accuracy

Prior Expectation reconstruction quality

Expectation Delta quality

无意义保守 NO_TRADE 比例

过度交易比例

平均 Web Search 数量

Case latency
```

另外重点观察：

```text
W3 direct trade rate
```

如果几乎为 0：

> Agent 可能过度保守。

如果非常高：

> Agent 可能没有真正执行 Prior Expectation / Surprise 判断。

不存在预设的理想百分比，但分布本身非常值得监控。

---

# 48. 最终形态

完整 Runtime 最终形成两层：

```text
                     FAST NORMAL PLANE

               Message
                  ↓
              W1 + W2
                  ↓
       Event Memory / Policy Memory
                  ↓
        Trade / Archive / Badcase
```

以及：

```text
                    W3 EXCEPTION PLANE

          new / probably new reality
                     +
         no / probably no Policy coverage
                     ↓
              W3 Codex SDK Agent
                     │
          ┌──────────┴──────────┐
          ↓                     ↓
       Mode 2                  Mode 1
   Repair W1 / W2      Uncovered-New Research
          │                     │
     Normal Route       Observed Reality
          │                     ↓
          │             Prior Expectation
          │                     ↓
          │             Expectation Delta
          │                     ↓
          │          Surprise / Materiality /
          │               Transmission
          │                     ↓
          │             TRADE / NO_TRADE
          └──────────┬──────────┘
                     ↓
              Runtime Delta
                     ↓
                     O2
                     
Trade / Policy Gap
        ↓
       O3
```

W3 因此不是现行 Policy 系统的替代品，而是它的**动态安全阀和新 Alpha 捕获层**：

> O3 负责把能够提前想到的未来状态编译成快速 Policy；W3 负责现实真正超出这个预编译空间时，快速恢复有限研究能力，从当前 D1/D2、完整 PolicySet 与 Reference View 重建 prior expectation，判断这一新现实究竟只是“新消息”，还是一个值得立即交易的 expectation gap。

这样正常消息继续走低成本 W1/W2，而真正值得花费 Codex Agent 推理预算的只有 **Policy 没有提前覆盖的现实变化**。
