# DoxAgent V2 Persistent Runtime 重构开发方案

## 1. 开发目标与边界

本轮不对旧 V1 Persistent Runtime 进行改造，而是新建一套独立的 **V2 Real-time Adjudication Runtime**。

V2 Persistent Runtime 位于：

```text
O2 Event Library
+
O3 Monitoring Execution Policy
        ↓
Persistent Runtime
```

其中：

* **W1**：新旧 / factual novelty 判定节点，使用 Response API；
* **W2**：Policy 命中判定节点，使用 Response API；
* **W3**：值班专家节点，使用 **Codex SDK Agent**；
* 本轮开发 W1、W2 及其相关编排；
* W3 本轮只实现路由和 Case 持久化接口，不实现 W3 Codex SDK Agent 本体。

V1 与 V2 保持业务语义、workflow、接口和运行状态隔离，不复用旧 W1/W2/O3 Runtime 语义。

当前 V2 已经具备：

```text
D3 Canonical PolicySet
D3 RuntimePolicyProjection

O2 KnownEventIndexSnapshot
O2 EventDetailSnapshot
O2 DeltaBatch / DeltaItem
```

因此本轮主要补齐的是：

```text
Message
↓
W1 / W2 realtime adjudication
↓
Router
↓
Trade / Archive / W3 / Delta / Badcase

以及

Runtime
↓
O2 daily incremental
↓
O3 daily maintenance
```

---

# 2. 实时总编排

一条新消息进入 Message Bus 后，创建一个 V2 Runtime Case。

```text
                       Message M
                          │
                 create RuntimeCase
                          │
             ┌────────────┴────────────┐
             │                         │
             ↓                         ↓
            W1                        W2
       Event Novelty              Policy Match
             │                         │
       fixed R1→R2                R1→optional R2
             │                         │
             └────────────┬────────────┘
                          ↓
                  deterministic Router
                          │
          ┌───────────────┼───────────────┐
          ↓               ↓               ↓
       ARCHIVE           TRADE            W3
                          │
                          └── trade record

W1 NEW / ADD_TO_DELTA
   └── asynchronous W1 R3
       └── RuntimeFactCandidate[]
```

W1 与 W2 从第一轮开始并行。

Router 等待：

```text
W1 Round 2 最终结果
+
W2 最终结果
```

W1 Round 3 不阻塞实时判定。

---

# 3. SourceMessageSnapshot

每个 Runtime Case 启动时构造不可变的 `SourceMessageSnapshot`。

该对象**基本沿用当前 Message Bus 向旧 Persistent Runtime 注入消息时已经存在的消息字段和结构**，V2 只在编排层将该输入冻结为本 Case 的 immutable snapshot。

不为 V2 Runtime 额外增加以下无实际判定价值的字段：

```text
url
published_at
collected_at
sha256
source
source_message_id
```

具体消息业务字段继续以当前 Message Bus 已有合同为基准，不在本轮重新设计一套新的 Source Message Schema。

同一个 W1/W2 Response conversation 全程使用同一份 `SourceMessageSnapshot`。

---

# 4. Runtime Version Pin

W1 / W2 会话在启动时固定其读取的 Published 上游版本。

版本管理使用**版本号**，不使用 hash pin。

## W1

固定：

```text
event_library_version
provisional_snapshot_version
```

## W2

固定：

```text
policy_set_version
runtime_projection_version
```

如果 Case 运行过程中 O2 或 O3 发布新版本：

```text
当前 Case
→ 继续使用启动时固定版本

下一条 Message
→ 使用最新 Published version
```

避免：

```text
Round 1 使用 v42
Round 2 使用 v43
```

导致同一判定过程读取不同业务状态。

---

# 5. W1 总体设计

W1 负责判断：

> 当前消息的**核心事实**相对于当前已知现实是否存在新的 factual information。

W1 不再允许仅根据 `KnownEventIndex` 直接判断 NEW / OLD。

固定采用：

```text
Round 1
Event Candidate Recall

↓ 固定进入

Round 2
Fact Coverage / Novelty Adjudication

↓ NEW 或需要 ADD_TO_DELTA 时

Round 3
Atomic Fact Extraction
```

Round 1 与 Round 2 都属于 Hot Path。

Round 3 在最终新旧结果已经产生后运行，不计入实时判定 SLA。

---

# 6. W1 Round 1 输入

输入：

```text
SourceMessageSnapshot

KnownEventIndexSnapshot

Today's Provisional Event Index
```

当前 O2 `KnownEventIndex` 是完整但紧凑的 Published Event 存在性视图，真正完整的 Event Facts 需要通过 Event Detail 展开。

因此 Round 1 唯一职责是：

> 找出判断当前消息新旧时最值得进一步读取 Detail 的 Event IDs。

不进行 NEW / OLD 判定。

---

# 7. W1 Round 1 思考逻辑

模型首先识别当前消息对应的现实身份：

```text
主体
动作 / 状态变化
对象
产品 / 项目 / 客户
阶段
时间
关键数值
```

然后从：

```text
Published Known Events
+
Today's Provisional Events
```

寻找最可能属于：

```text
同一真实 occurrence
或
同一现实过程的直接延续
```

的候选。

候选排序关注：

```text
真实 occurrence 对应概率
>
主题或文本相似度
```

例如：

```text
同样讨论 HBM
```

本身不构成一个好的候选理由。

没有合理候选时：

```json
{
  "event_ids": []
}
```

仍然正常进入 Round 2。

---

# 8. W1 Round 1 Output

Schema 保持极简：

```json
{
  "event_ids": ["E17", "E31"]
}
```

Prompt 中明确：

```text
最多请求 5 个 Event Detail，
并按照最值得展开判断的顺序排列。
```

配置语义：

```text
W1_MAX_EVENT_DETAIL_REQUESTS = 5
```

这是 Prompt 层的行为约束，**不进行阻塞性 Schema / Validator 校验**。

即使模型偶发返回超过 5 个，也不因此使整个 Case 失败；编排器按正常容错策略处理。

Round 1 不输出：

```text
NEW / OLD
confidence
reason
```

防止模型提前进入 novelty judgement。

---

# 9. 当日 Provisional E# 机制

O2 Published Event Library 是稳定的历史事实层。

但 O2 不会对每一条实时新消息立即执行 Incremental，因此需要建立：

> **Intraday Provisional Reality Memory**

假设当前 Published Event Library 最大 Event ID 为：

```text
E184
```

当天第一个 provisional atomic fact：

```text
E185
```

之后依次：

```text
E186
E187
...
```

这些 E#：

* 只在当前交易日有效；
* 只服务当日 W1 判断；
* 不进入 O2 Canonical Event Library；
* 不要求与 O2 日终最终生成的 Canonical Event ID 保持一致。

---

# 10. Provisional ID 分配

Provisional E# 由确定性程序生成，不由 LLM 生成。

起点：

```text
n = 当前 Published Event Library 最大 Event numeric ID
```

而不是 Reference View 最后一条 Event ID，因为 Reference View 并不保证包含完整 Event Library。

当天：

```text
first provisional = E(n+1)
second provisional = E(n+2)
...
```

分配器按：

```text
ticker + trading_date
```

维护单调序列，并保证并发情况下 ID 不碰撞。

---

# 11. Today's Provisional Index

W1 Round 1 输入中明确区分：

```text
PUBLISHED KNOWN EVENTS
E1 | ...
E2 | ...
...

TODAY'S PROVISIONAL FACTS
E185 | ...
E186 | ...
```

尽管两者都使用 E#，其业务身份不同：

```text
Published Event
=
O2 Canonical Reality

Provisional Event
=
当日尚未经过 O2 canonicalization 的临时事实
```

但对 W1 novelty 判定而言，两者都代表：

> 当前已经知道的现实。

---

# 12. W1 Round 2 Detail 组装

根据 Round 1 返回的：

```text
event_ids[]
```

编排器分别加载：

### Published Event

读取固定 `event_library_version` 的：

```text
EventDetailSnapshot
```

### Provisional Event

读取：

```text
Today's Provisional Fact Detail
```

然后组成统一的 Runtime Detail 上下文。

建议 Runtime Detail envelope 至少携带：

```text
event_library_version
requested_event_ids[]
canonical_events[]
provisional_events[]
missing_event_ids[]
```

不修改底层 CanonicalEvent Schema。

如果某个请求 ID 不存在，应显式进入：

```text
missing_event_ids
```

而不是静默丢弃。

---

# 13. W1 Round 2

Round 2 是唯一 NEW / OLD 判定轮。

核心问题：

> 当前消息的**全部核心事实**是否已经被当前已知事实覆盖？

判定标准：

```text
消息全部核心事实均已覆盖
→ OLD

至少存在一个核心事实尚未被覆盖
→ NEW
```

这里强调的是：

> **核心事实**

而不是文章中任意附带信息。

背景资料、无关补充、作者评论、普通转述、非目标事实的新信息，并不会因为“是新的”而自动把整条消息判成 NEW。

---

# 14. W1 Round 2 思考顺序

模型按以下顺序判断：

```text
1. 当前消息有哪些核心事实？

2. Candidate Events 是否与消息属于
   同一现实 occurrence / process？

3. Event Detail 中已有的 active Facts
   分别覆盖了哪些核心事实？

4. 哪些只是措辞变化、重复报道或转述？

5. 是否存在尚未覆盖的核心事实？

6. 全部覆盖 → OLD
   存在核心新事实 → NEW

7. 最后执行 confidence ambiguity test
```

事实覆盖采用语义等价，而不是文本等价。

例如：

```text
Known:
Nvidia 已完成 Micron HBM4 qualification

Message:
Micron HBM4 已获得 Nvidia qualification
```

属于覆盖。

而：

```text
Known:
qualification testing underway

Message:
qualification completed
```

属于新的核心事实。

---

# 15. W1 Round 2 Output

```json
{
  "result": "NEW",
  "confidence": "normal",
  "reference_ids": [],
  "reason": "消息新增确认客户已完成量产资格，现有 Event 仅记录测试阶段。"
}
```

Schema：

```text
result:
  NEW | OLD

confidence:
  normal | low

reference_ids:
  E#[]
  maxItems = 3

reason:
  简短的一句判定依据
```

`OLD` 时应返回最直接对应的 reference ID。

该 ID 可以是：

```text
Published E17
```

也可以是：

```text
当天 Provisional E185
```

第二轮 `reference_ids` 保持：

```text
maxItems = 3
```

作为正式 Structured Output 约束。

---

# 16. Confidence 的统一语义

W1 / W2 的：

```text
confidence
```

只有：

```text
normal
low
```

它不表达：

> 模型主观上有多自信。

也不表达：

> 当前信息是不是“足够生产级充分”。

统一定义为：

> **当前是否存在会改变最终 verdict 的真实二义竞争结果。**

默认：

```text
normal
```

只有同时满足以下三个条件时使用：

```text
low
```

```text
1. 当前存在一个决定最终结果的关键歧义；

2. 两个互相冲突的最终结果
   都得到当前可用信息的直接支持；

3. 根据当前规则和上下文
   无法消除该歧义。
```

因此：

```text
没有更多证据
≠ low

条件没有满足
≠ low

普通信息不完整
≠ low

不是 100% 确定
≠ low
```

如果输出 `low`：

> `reason` 必须明确指出那个会导致两个不同 verdict 的具体歧义。

说不出该歧义，则应使用 `normal`。

---

# 17. W1 Round 3

如果：

```text
W1 Round 2 = NEW
```

无论：

```text
confidence = normal
```

还是：

```text
confidence = low
```

都进入事实抽取轮。

此外，根据最终 Router 结果，某些需要：

```text
ADD_TO_DELTA
```

的模糊 Case 也可触发 Round 3。

Round 3 已经不参与：

```text
NEW / OLD
```

判断。

唯一任务：

> 把当前消息中需要进入日内事实记忆的 atomic factual assertions 抽取出来。

---

# 18. W1 Round 3 两种工作模式

## `NEW_CAPTURE`

用于：

```text
W1 = NEW
```

只抽取：

> 尚未被当前 Known Reality 覆盖的全部核心新 atomic facts。

## `AMBIGUOUS_CAPTURE`

用于 Router 判定：

```text
ADD_TO_DELTA
```

但 W1 本身没有确认 NEW 的边缘 Case。

此时不要求模型再次断言这些 Facts 一定是新事实，而只是：

> 将消息中具体、独立的 atomic factual assertions 压缩出来，交给日终 O2 canonicalization 去重。

两种模式共享同一 Output Schema。

---

# 19. W1 Round 3 思考方向

模型关注：

```text
哪些是新的核心事实？
哪些只是旧背景？
哪些只是评论、推断或重复表述？

一个 Fact 是否能够独立成立？

是否把多个现实动作错误合并成一个 proposition？

发生时间与 subject period 是否被正确区分？
```

例如消息：

```text
旧：
Micron 已向客户送样

新：
客户完成 qualification
客户开始 volume shipment
```

只抽：

```text
客户完成 qualification
客户开始 volume shipment
```

---

# 20. RuntimeFactCandidate

W1 不直接生成正式 O2 `DeltaItem`。

因为当前正式 DeltaItem 包含：

```text
runtime_atomic_id
runtime_atomic_version
runtime_signature
delta_id
runtime hint/package identity
```

这些身份属于 Runtime / CDECR / O2 的确定性基础设施，而不是单消息 LLM 能够可靠生成的语义。

新增轻量：

```text
RuntimeFactCandidate
```

最小形态：

```json
{
  "proposition": "Micron确认该客户已进入重复量产采购。",
  "assertion_state": "ACTUAL",
  "subject_time": null,
  "occurrence_date": "2026-08-29",
  "entities": ["Micron"]
}
```

Round 3：

```json
{
  "candidates": [
    {...},
    {...}
  ]
}
```

模型不生成：

```text
E#
F#
delta_id
runtime_atomic_id
```

---

# 21. RuntimeFactCandidate → Provisional Memory

每个 Candidate 持久化以后：

```text
deterministic E# allocation
↓
加入 Today's Provisional Index
```

例如：

```text
candidate 1 → E185
candidate 2 → E186
```

之后当天新到达的 W1 会立即看到：

```text
E185
E186
```

因此同日另一篇文章重复这些事实时，不必等待 O2 日终更新。

---

# 22. W2 总体设计

W2 上游是 O3 Published PolicySet。

一条 Message 到达时，W2 与 W1 并行启动。

W2 负责：

> 根据当前 RuntimePolicyProjection 判断这条消息是否真正命中一个或多个 Direct Trading Policy。

系统业务语义是：

> **最终只执行一个 Policy。**

现实中允许多个 Policy 同时命中，因此 W2 可以输出多个 Policy ID，但必须排序。

只有：

```text
policy_ids[0]
```

具有执行资格。

---

# 23. RuntimePolicyProjection 压缩改造

现有 Projection 需要进一步压缩。

W2 Round 1 不再接收：

```text
title
decision
condition_id
```

Runtime Projection 中每个 Policy 只保留：

```text
policy_id
match_scope
criterion
activation_summary
```

建议逻辑形态：

```json
{
  "schema_version": "document3.runtime_projection.v2",
  "ticker": "MU",
  "policy_set_version": 12,
  "policies": [
    {
      "policy_id": "pol_001",
      "match_scope": "客户 qualification、adoption、commercial shipment 相关消息",
      "criterion": [
        "客户完成量产资格",
        "客户进入重复商业采购"
      ],
      "activation_summary": "客户进入符合条件的重复规模采购时触发。"
    }
  ]
}
```

一个 Policy 只出现一次。

如果 Policy 有多条 Activation Conditions：

```text
criterion
```

字段中直接并列多个 criterion。

W2 收到的简易 Projection Schema 说明中必须明确：

> **同一 Policy 的 `criterion[]` 为并列的全部当前触发条件，默认采用 AND 语义；当前消息必须满足其中全部 criterion 才算该 Policy 命中。**

不再依赖：

```text
condition_id
```

进行 Runtime 聚合。

---

# 24. 为什么 Projection 不携带 decision

`decision` 已存在于 Canonical PolicySet。

W2 的职责只是：

> 返回哪个 Policy 被命中。

因此无需让模型重复读取或者输出 LONG / SHORT。

如果最终：

```text
policy_ids[0] = pol_017
```

后续程序从固定 `policy_set_version` 的 Canonical PolicySet 确定性读取：

```text
pol_017.decision
```

即可。

这样继续减少 W2 Round 1 Input。

---

# 25. W2 Round 1 输入

```text
SourceMessageSnapshot
+
RuntimePolicyProjection v2
```

Projection 只用于快速判定，不注入：

```text
Calibration
source_refs
Document2
完整 PolicySet
```

---

# 26. W2 Round 1 思考逻辑

固定认知链：

```text
Message facts
↓
match_scope
↓
Candidate Policies
↓
criterion satisfaction
↓
True Policy Hits
↓
Ranking
```

---

# 27. `match_scope` 与 `criterion`

这是 W2 Prompt 最重要的语义区别。

```text
match_scope
=
什么消息值得考虑这条 Policy
```

```text
criterion
=
什么事实真正触发这条 Policy
```

因此：

```text
相关
≠
命中
```

例如 Message 属于某 Policy 的 `match_scope`，但没有达到 criterion：

```text
NO HIT
confidence = normal
```

不能因为“高度相关”就返回 Policy ID。

---

# 28. Multi-condition Policy

如果：

```json
"criterion": [
  "C1对应的判断命题",
  "C2对应的判断命题"
]
```

业务语义固定为：

```text
C1 AND C2
```

当前版本暂不处理：

```text
上午消息满足 C1
+
下午消息满足 C2
```

这种同日多消息逐项满足的边缘场景。

因此当前 W2 判断：

> 一条消息必须足以满足该 Policy 当前 Projection 中列出的全部 criterion。

如果某 Condition 在前一交易日已经成为现实：

> O3 MAINTAIN 会在新的 PolicySet / Projection 中移除它。

---

# 29. W2 Round 1 Output

```json
{
  "policy_ids": ["pol_017", "pol_042"],
  "confidence": "normal",
  "reason": "消息直接满足 pol_017 的全部现行触发条件。"
}
```

Schema：

```text
policy_ids[]
confidence
reason
```

其中：

```text
[]
=
NO POLICY HIT
```

建议：

```text
policy_ids maxItems = 3
```

如果多个 Policy 真正命中：

```text
数组顺序
=
Policy ranking
```

只有第一条执行。

---

# 30. W2 Policy Ranking

只对真正满足全部 criterion 的 Policies 排序。

排序标准：

```text
消息事实与 criterion 的直接对应程度
↓
触发语义匹配的精确程度
↓
所需额外推断的多少
```

不重新研究：

```text
哪条 thesis 更重要
哪个方向影响更大
哪条 Policy 应该得到更高仓位
```

这些属于 O3 和后续交易系统。

---

# 31. W2 Confidence

使用与 W1 完全一致的 ambiguity definition：

```text
normal
=
当前存在明显占优的 verdict

low
=
存在两个相反 verdict，
且两者都有直接证据支持，
当前规则无法消解
```

例如 Policy 要求：

```text
重复商业采购
```

消息只说：

```text
完成 qualification
```

正确结果：

```text
policy_ids = []
confidence = normal
```

因为这不是歧义。

只是没有满足触发条件。

---

# 32. W2 Round 2 Gate

只有：

```text
policy_ids 非空
AND
confidence = low
```

时增加一个临时轮次。

第一轮 Prompt 中不告诉 W2：

> low 后会有第二轮。

避免模型把 `low` 当作索取更多 Context 的机制。

---

# 33. Policy Detail Provider

当前 D3 Canonical PolicySet 已拥有完整：

```text
source_refs
activation_conditions
calibration.reference_state
calibration.trigger_boundary
calibration.qualifying_evidence
```

但 Runtime 缺少按 `policy_id` 读取 Detail 的独立边界。

本轮增加：

```text
get_policy_details(
    ticker,
    policy_set_version,
    policy_ids
)
```

返回：

```text
PolicyDetailSnapshot

ticker
policy_set_version
requested_policy_ids
policies[]
missing_policy_ids[]
```

其中：

```text
policies[]
```

直接使用 Canonical Policy Schema。

不建立第二套 Full Policy 数据结构。

---

# 34. W2 Round 2

输入：

```text
same SourceMessageSnapshot
+
same Response conversation
+
selected Policy Details
```

只加载：

> Round 1 已经返回的 Candidate Policy Detail。

不注入整个 PolicySet。

Round 2 重点使用：

```text
criterion
reference_state
trigger_boundary
qualifying_evidence
```

判断：

> 当前现实究竟处于 trigger boundary 之前还是之后。

---

# 35. W2 Round 2 思考顺序

```text
criterion
↓
reference_state
↓
trigger_boundary
↓
qualifying_evidence
↓
current message facts
↓
final Policy hit ranking
```

Round 2 不重新研究：

```text
D2 thesis
长期投资价值
Policy 是否值得存在
```

它只解决：

> Round 1 无法确定的 trigger-boundary ambiguity。

---

# 36. W2 Round 2 Output

继续使用同一个 Schema：

```json
{
  "policy_ids": ["pol_017"],
  "confidence": "normal",
  "reason": "完整Calibration确认该消息已经跨过重复商业采购的触发边界。"
}
```

第二轮结束后：

```text
无论 normal / low
均不进入第三轮
```

如果仍然 `low`：

> 由 Router 决定是否进入 W3。

---

# 37. W1 / W2 Prompt 体系

Prompt 分成五个独立认知任务：

| Prompt | 任务                                       |
| ------ | ---------------------------------------- |
| W1 R1  | Event Candidate Recall                   |
| W1 R2  | Fact Coverage / Novelty                  |
| W1 R3  | Atomic Fact Extraction                   |
| W2 R1  | Runtime Policy Match                     |
| W2 R2  | Policy Calibration Boundary Adjudication |

这些不是一个通用 Prompt 的不同参数。

每轮都根据自己的任务优化思考方式。

---

# 38. Prompt 写作结构

每个 Prompt 都使用：

```text
Role
↓
当前轮次的判断对象
↓
核心判定语义
↓
思考顺序
↓
必要的 ambiguity rule
↓
最小 Output Contract
```

Prompt 不要求输出 Chain-of-Thought。

要求模型：

> 按规定的判断顺序完成内部推理，只返回结构化 Verdict 和简短 Reason。

避免大量无实际失败模式依据的：

```text
Do not
Never
Cannot
```

约束。

---

# 39. W1 Round 1 Prompt 重点

只写：

```text
如何识别现实 occurrence
如何找同 occurrence / direct continuation
哪些维度用于 recall
如何排序 Event
最多请求 5 个 Event IDs
本轮只做 Detail Recall
```

不详细讨论 NEW / OLD。

---

# 40. W1 Round 2 Prompt 重点

重点定义：

```text
什么是核心 Fact
什么叫 Fact 已覆盖
什么是同 Event 的新核心进展
哪些只是背景 / 评论 / 重复
全部核心 Facts old → OLD
存在核心 Fact new → NEW
confidence ambiguity test
```

这是 W1 最核心的判定 Prompt。

---

# 41. W1 Round 3 Prompt 重点

关注：

```text
只抽需要进入 Delta 的 Facts
atomic proposition 如何拆
完整覆盖核心新增事实
不要重新复制已知 Fact
occurrence time / subject time
```

这是 Extraction Prompt，不再讨论 NEW / OLD。

---

# 42. W2 Round 1 Prompt 重点

重点：

```text
match_scope 与 criterion 的区别

criterion[] 并列时全部需要满足

相关但未满足 → no hit

缺少 trigger fact → normal no hit

多个真实 hit 如何排序

confidence ambiguity test
```

Projection Schema 的 Prompt 说明必须明确：

```text
一个 Policy 只有一条记录；
criterion 为数组；
多个 criterion 是 AND；
没有 condition_id。
```

---

# 43. W2 Round 2 Prompt 重点

重点：

```text
利用 Calibration 消除上一轮歧义

reference_state
→ 当前基线

trigger_boundary
→ 必须跨过的位置

qualifying_evidence
→ 如何确认已经跨过

只解决 Policy boundary
```

不重新进行 investment research。

---

# 44. Structured Outputs

W1 / W2 各轮都使用 Strict Structured Output。

不让模型额外产生：

```text
analysis
confidence_score
notes
recommendation
```

业务输出保持最小化。

---

# 45. Router

Router 完全由确定性程序执行。

输入：

```text
W1 final result
W1 final confidence
W2 final policy_ids
W2 final confidence
```

其中：

```text
policy_hit
=
len(policy_ids) > 0
```

Router 使用 W2 最终轮结果。

路由矩阵固定为：

| W1  | W1 low | Policy hit | W2 low | Primary result    |
| --- | -----: | ---------: | -----: | ----------------- |
| NEW |      是 |          是 |      是 | W3                |
| NEW |      否 |          是 |      是 | W3                |
| NEW |      是 |          否 |      是 | W3                |
| NEW |      否 |          否 |      是 | W3                |
| NEW |      是 |          是 |      否 | W3                |
| NEW |      否 |          是 |      否 | TRADE             |
| NEW |      是 |          否 |      否 | ADD_TO_DELTA      |
| NEW |      否 |          否 |      否 | ADD_TO_DELTA      |
| OLD |      是 |          是 |      是 | W3                |
| OLD |      否 |          是 |      是 | ARCHIVE + BADCASE |
| OLD |      是 |          否 |      是 | W3                |
| OLD |      否 |          否 |      是 | ARCHIVE           |
| OLD |      是 |          是 |      否 | W3                |
| OLD |      否 |          是 |      否 | ARCHIVE + BADCASE |
| OLD |      是 |          否 |      否 | ADD_TO_DELTA      |
| OLD |      否 |          否 |      否 | ARCHIVE           |

---

# 46. Primary Route 与 Side Effects

Router 的最后结果不能被实现成一个互斥的单一业务结果。

例如：

```text
NEW
+
W2 normal Policy hit
→ TRADE
```

同时仍然要：

```text
W1 R3
→ RuntimeFactCandidate
```

因此 Case 逻辑分成：

```text
primary_route
```

与：

```text
side_effects
```

Side effects 可包括：

```text
archive_message
emit_delta
create_trade_record
mark_badcase
route_to_w3
```

例如：

```text
TRADE
+
emit_delta
+
create_trade_record
```

---

# 47. Trade Record

当 Router 进入：

```text
TRADE
```

记录：

```text
trade_record_id
ticker
完整消息内容
executed_policy_id
candidate_policy_ids
policy_set_version
W1 result
W2 result
created_at
```

其中：

```text
executed_policy_id
=
policy_ids[0]
```

交易方向从固定 `policy_set_version` 的 Canonical PolicySet 根据 `executed_policy_id` 确定性取得。

Trade Record 必须保留命中 Policy 的完整文章内容，以供次日 O3 MAINTAIN 使用。

---

# 48. BADCASE

BADCASE 定义：

```text
W1 = OLD
W1 confidence = normal
W2 policy hit
```

表示：

> 一个已经属于已知现实的消息仍然能够命中当前 Active Policy。

正常情况下 O3 每日会同步最新现实，因此这种情况理论上不应存在。

它反映：

```text
Policy baseline stale
或
O3 daily maintenance 存在遗漏
```

BADCASE：

```text
直接 ARCHIVE
不进入 O2 Delta
```

同时保存：

```text
消息正文
matched known Event IDs
hit policy IDs
event_library_version
policy_set_version
W1 reason
W2 reason
```

并在日终反馈给 O3。

---

# 49. W3

W3 是：

> **基于 Codex SDK 的值班专家 Agent 节点。**

它用于处理 W1/W2 快速低自由度判定器无法可靠解决的疑难和模糊 Case。

未来 W3 将能够读取：

```text
当前 D1
当前 D2
当前 D3
Source Message
W1 partial result
W2 partial result
```

进行更完整的综合研判。

本轮：

```text
不开发 W3 Agent
```

只实现：

```text
route_to_w3
+
W3 case payload
+
PENDING_W3 状态
```

---

# 50. W3 Route Case

建议至少保存：

```text
case_id
ticker
完整消息内容
W1 final result
W2 final result
event_library_version
policy_set_version
route_reason
created_at
```

未来 W3 通过：

```text
ticker
+
当前 Published artifact references
```

自行加载 D1/D2/D3。

本轮不提前复制大型研究文档进入 W3 Case。

---

# 51. 技术失败与 Confidence 分离

例如：

```text
Response API timeout
Structured Output validation failure
Event Detail read failure
Policy Detail missing
Version unavailable
```

不属于：

```text
confidence = low
```

基础设施错误使用独立技术状态：

```text
PENDING_RETRY
UNAVAILABLE
FAILED
```

`low` 只表达业务判定中的真实二义性。

---

# 52. 实时 SLA

目标：

> W1 Round 1 → Round 2 与 W2 最终结果，在 1 分钟内完成。

W1 / W2 并行：

```text
W1 R1 ─────┐
           ├─ parallel
W2 R1 ─────┘
```

之后：

```text
W1 R2
必跑

W2 R2
仅少量 low + policy candidate Case
```

普通 Case：

```text
2 次串行 W1 LLM call
+
1 次 W2 LLM call
```

W1 R3 在 Router 已经获得 W1 final verdict 后运行，不计入 Hot Path SLA。

---

# 53. Output 延迟控制

W1 / W2 模型输出都保持极小。

### W1 R1

```json
{
  "event_ids": ["E17"]
}
```

### W1 R2

```json
{
  "result": "OLD",
  "confidence": "normal",
  "reference_ids": ["E17"],
  "reason": "..."
}
```

### W2

```json
{
  "policy_ids": ["pol_17"],
  "confidence": "normal",
  "reason": "..."
}
```

因此 Output token 不是主要性能瓶颈。

主要性能对象是：

```text
Input size
model inference
Detail assembly
network latency
```

---

# 54. 日终 DeltaBatch

交易周期结束后：

```text
Today's RuntimeFactCandidates
↓
deterministic dedup
↓
DeltaBatch Adapter
↓
O2 PENDING DeltaBatch
```

W1 不生成正式 Delta identity。

Adapter 负责生成 O2 当前合同要求的：

```text
delta_id
runtime_atomic_id
runtime_atomic_version
runtime_signature
source_snapshot_id
source_epoch_id
base_library_version
source references
occurrence date candidates
```

---

# 55. Runtime Delta Identity

不要冒充：

```text
cdecr-atomic-xxx
```

应使用明确属于新 Runtime 入口的 namespace。

例如概念上：

```text
runtime-v2-msg:{message_identity}:{candidate_index}
```

具体 Message identity 沿用现有 Message Bus / Runtime 已有业务标识，不要求向 LLM 或 SourceMessageSnapshot 新增 `source_message_id`。

`runtime_signature` 由程序根据 canonical candidate payload 确定性生成。

如果现有 O2 validator 对 CDECR identity 有硬编码限制：

> 扩展正式 Runtime Delta namespace。

不伪造 CDECR identity。

---

# 56. Occurrence Time Adapter

如果 W1 Fact 明确知道事实发生日期：

```text
source_kind
=
RUNTIME_CONFIRMED_OCCURRENCE
```

如果只有 Runtime 已有消息时间信息可作为来源时间候选：

```text
使用现有 Message Bus 对应的时间信息进行 adapter
```

不要求为 SourceMessageSnapshot 新增 `published_at` 字段。

尤其不能把：

```text
FY2027
下一季度
未来一年
```

这样的：

```text
subject_time
```

作为：

```text
Event occurrence date
```

---

# 57. O2 日终闭环

```text
Runtime DeltaBatch
        ↓
O2 Incremental
        ↓
Revision Bundle
        ↓
Publish Event Library vN+1
        ↓
New KnownEventIndex
```

下一交易日：

```text
W1
→ 使用新的 KnownEventIndex version
```

当天 Provisional E# 全部失效。

它们不会直接成为：

```text
Canonical E#
```

---

# 58. Reference View Delta

当前 O2 已经分别提供：

```text
Known Event Index
→ W1 使用

Reference View
→ D2 / D3 使用
```

本轮需要补充：

```text
ReferenceViewDeltaSnapshot
```

每次 O2 Incremental Publish 时同步生成。

正文格式继续沿用当前：

```text
Reference View
```

但只包含：

> 本轮 DeltaBatch 经 O2 canonicalization 后，对 Reference View 新增或改变的内容。

建议 Envelope：

```text
contract_version
ticker
from_library_version
to_library_version
reference_view_delta
removed_event_ids[]
```

版本管理只使用：

```text
from / to version
```

不增加 SHA-256。

`removed_event_ids[]` 用于表达：

> 某个此前存在的 Reference Event 被撤回、替代或不再进入 Reference View。

---

# 59. O3 Daily Maintenance Feed

日终 O2 Incremental 完成以后组装：

```text
O3MaintenanceFeed
```

包含：

```text
Reference View Delta

Today's Trade Records

Today's BADCASE Records
```

然后：

```text
Reference View Delta
        │
Trade ──┤
BADCASE ┤
        ↓
O3_MAINTAIN
        ↓
New PolicySet
        ↓
New RuntimePolicyProjection
```

不重新注入完整 D2。

如果当天：

```text
DeltaBatch = empty
```

但存在：

```text
Trade Record
或
BADCASE
```

仍然允许：

```text
empty Reference View Delta
+
Runtime Feedback
→
O3_MAINTAIN
```

---

# 60. Response API 多轮原则

W1 与 W2 各自使用独立 Response conversation。

```text
W1:
R1 → R2 → optional R3

W2:
R1 → optional R2
```

后续轮次继续同一 conversation。

每一轮重新注入：

```text
Core Instructions
+
Round-specific Instructions
```

不依赖上一轮 Prompt instruction 自动存在。

Transport 层保存 Response conversation metadata，但这些字段不进入：

```text
Event
Fact
Policy
Delta
```

等业务对象。

---

# 61. 开发实施顺序

## Phase 1 — Contracts

先冻结：

```text
SourceMessageSnapshot wrapper

W1Round1Result
W1NoveltyResult
W1FactExtractionResult

RuntimeFactCandidate

W2PolicyResult

RuntimeCase

PolicyDetailSnapshot

RuntimeEventDetailEnvelope
```

---

## Phase 2 — W1

实现完整链路：

```text
KnownEventIndex
+
Provisional Index
↓
W1 R1

↓ fixed

Detail Assembly
↓
W1 R2

↓ NEW / ADD_TO_DELTA

W1 R3
↓
RuntimeFactCandidate
```

先独立完成 W1 测试。

---

## Phase 3 — Provisional Reality Layer

实现：

```text
Daily E# allocator
Today's Provisional Index
Provisional Event Detail
Daily expiry
```

接入 W1 Round 1 / Round 2。

---

## Phase 4 — RuntimePolicyProjection v2

将当前 Projection 进一步压缩为：

```text
policy_id
match_scope
criterion[]
activation_summary
```

删除：

```text
title
decision
condition_id
```

一个 Policy 一条 Projection record。

多个 criterion：

```text
并列在 criterion[]
默认 AND
```

---

## Phase 5 — W2

实现：

```text
RuntimeProjection v2
↓
W2 R1

↓ policy_ids != [] AND low

Policy Detail
↓
W2 R2
```

---

## Phase 6 — Parallel Orchestrator

实现：

```text
Message
↓
W1 / W2 parallel
↓
wait W1 R2 + W2 final
↓
Router
```

W1 R3 独立于 Hot Path 结果完成。

---

## Phase 7 — Router

按照固定矩阵完成：

```text
ARCHIVE
TRADE
ADD_TO_DELTA
W3
BADCASE
```

及对应 side effects。

---

## Phase 8 — W3 Route

实现：

```text
W3RouteCase
PENDING_W3
```

不实现 W3 Codex SDK Agent。

---

## Phase 9 — Daily Delta Adapter

```text
RuntimeFactCandidates
↓
Deterministic Adapter
↓
O2 DeltaBatch
```

接入 O2 Existing Incremental Workflow。

---

## Phase 10 — Reference View Delta

扩展 O2 Published output：

```text
Published Event Library
KnownEventIndex
Reference View
Reference View Delta
```

---

## Phase 11 — O3 Maintenance Feed

日终组装：

```text
Reference View Delta
+
Trade Records
+
BADCASE
↓
O3_MAINTAIN
```

接入现有 D3 Maintenance workflow。

---

# 62. W1 Eval 重点

测试集重点覆盖：

```text
完全重复报道

同 Event 同 Fact 的改写

同 Event 新核心 Fact

同主题不同 occurrence

文章存在大量旧背景但核心事实为新

文章存在不相关的新内容，
但全部核心事实已知

Published Event duplicate

当天 Provisional Event duplicate

真正存在 NEW / OLD 二义竞争的 Case
```

尤其关注：

```text
W1 R1 candidate Event recall
```

因为 R1 漏掉正确 Event Detail 会直接影响 R2 的 novelty 判断。

---

# 63. W2 Eval 重点

覆盖：

```text
match_scope 相关但 criterion 不满足

明确 Policy hit

Multi-condition 全部满足

Multi-condition 缺少一个 criterion

多个 Policy 真命中后的 ranking

Projection 信息不足但 Calibration 可以消除歧义

完整 Calibration 后仍然存在真实二义结果

无 Policy 相关
```

---

# 64. Confidence Eval

专门建立：

```text
normal vs low
```

测试集。

目标不是让模型：

> 尽可能高 confidence。

而是确认：

```text
普通不确定性
→ normal

没有达到 trigger
→ normal

真正存在两个直接证据支持的冲突 verdict
→ low
```

重点监控：

```text
false-low rate
```

防止保守退避。

---

# 65. 结构验收

必须满足：

```text
V1 / V2 workflow 完全隔离

W1 固定 R1 → R2

W1 R1 不产生 NEW / OLD

W1 R2 才能产生 novelty verdict

W2 Projection v2 不包含
title / decision / condition_id

W2 多 criterion 使用 AND

Response conversation 不跨业务版本

Provisional E# 当日唯一

Provisional E# 不进入 O2 Canonical identity

DeltaBatch 通过 O2 现有 validator

Reference View Delta 可追踪 from/to version
```

---

# 66. 效果验收

主要指标：

```text
W1 R1 Event candidate recall

W1 final NEW / OLD accuracy

W1 atomic fact extraction recall

W2 Policy hit precision

W2 Top-1 Policy accuracy

W1 low rate
W2 low rate

false-low rate

W3 route rate

BADCASE rate
```

这些指标比单纯 overall accuracy 更容易暴露具体哪个环节出现问题。

---

# 67. 性能验收

Hot Path：

```text
W1 R1
→ W1 R2

并行

W2 R1
→ optional W2 R2
```

目标：

```text
p95 < 60 seconds
```

W1 R3 单独计时，不进入 Hot Path SLA。

同时记录：

```text
W1 Event Detail request count

W2 R2 trigger rate

Input token

Output token

LLM latency

Detail assembly latency
```

---

# 68. 最终 V2 Runtime 结构

实时：

```text
                     REALTIME

        Published O2              Published O3
     Known Event Memory           Policy Memory
             │                         │
             ↓                         ↓
            W1                        W2
      Event / Fact novelty       Policy adjudication
             │                         │
             └──────────┬──────────────┘
                        ↓
                Deterministic Router
                        │
        Archive / Trade / W3 / BADCASE
                        │
              NEW / retained facts
                        ↓
              Intraday Provisional
                   Reality Memory
```

日终：

```text
                    DAILY CLOSE

RuntimeFactCandidates
        ↓
DeltaBatch Adapter
        ↓
O2 Incremental
        ├── New KnownEventIndex
        └── Reference View Delta
                       │
Trade Records ─────────┤
BADCASE ───────────────┤
                       ↓
                  O3_MAINTAIN
                       ↓
                  New PolicySet
                       ↓
              RuntimePolicyProjection v2
```

---

# 69. 最终设计原则

这套 V2 Persistent Runtime 的核心不是让实时 LLM 临场重新研究市场。

而是：

> **O2 提前维护事实记忆，O3 提前维护交易规则；W1 和 W2 在实时消息到来时分别应用这两套已经整理好的知识，对“事实是否新”和“Policy 是否命中”进行低自由度判定。**

W1 通过固定：

```text
Index Recall
→
Detail Fact Comparison
```

避免只根据紧凑索引误判事实覆盖。

W2 通过：

```text
Compressed Policy Projection
→
optional Full Calibration Detail
```

在绝大多数消息上保持低输入、低延迟，只对真实 trigger ambiguity 扩大上下文。

当低成本判定无法可靠处理 Case 时：

> 路由到未来的 **W3 Codex SDK Expert Agent**。

与此同时：

```text
Runtime 新事实
→ O2

Runtime Trade / BADCASE
→ O3
```

分别形成：

```text
Reality Memory Feedback Loop
```

和：

```text
Policy Maintenance Feedback Loop
```

最终使 O2、O3、Persistent Runtime 构成一套持续滚动、自我更新但实时判定保持简单的 V2 事件驱动交易执行系统。
