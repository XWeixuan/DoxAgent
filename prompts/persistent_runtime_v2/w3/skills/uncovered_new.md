# W3 Mode 1 — UNCOVERED_NEW

进入本方法时，当前 Case State 已经确认为 `NEW + no Policy`。把这一状态作为研究起点，本 Turn 集中判断：该新现实相对于事件发生前的合理预期，是否形成对目标 ticker 可立即行动的 expectation gap；同时为最终 NEW reality 提取 atomic facts。

```text
Observed Reality
→ Affected Expectation Variable
→ Pre-event Prior Expectation
→ Expectation Delta
→ Surprise / Materiality / Transmission
→ TRADE or NO_TRADE
→ RuntimeFactCandidate
```

## Working Inputs

先读取 `task.json` 和 `output_schema.json`，再结合四份业务上下文：

- `document1.md` 提供公司基本面、行业与价值链、市场预期及业务传导的研究基础；相关研究面共同使用，没有固定主次。
- `document2.json` 提供结构化的 expectation State、Realization Factors 与 Potential Gaps，用于定位消息影响的具体 expectation variable 和已有 baseline。
- `reference_view.md` 提供 D1/D2 之后、截至本 Case 版本已经进入现实的 Event / Fact。
- `policy_set.json` 展示系统已经预见和校准的现实边界。Mode 1 的空 Policy 结论已经成立；使用 PolicySet 理解已有 baseline 和本次变化为何属于 coverage gap。

Published D1/D2 是可信研究起点。若 D2 为 `PARTIAL` 或缺少当前变量，结合 D1、Reference View 与事件前公开资料恢复 baseline；内容缺失本身不代表市场没有相关预期。

## 1. Frame the Case

在扩展研究前先明确五个问题：

```text
发生了什么？
主要改变哪个 expectation variable？
事件前需要恢复什么 baseline？
它可能通过什么路径影响目标 ticker？
当前哪项缺失信息可能改变最终判断？
```

先定位 Affected Expectation Variable，例如 commercialization timing、effective supply、qualification probability、addressable market、cost / margin path 或 value-capture allocation。后续文件阅读和 Web Search 都围绕该变量收敛。

## 2. Clarify Observed Reality — Search A

先把消息还原为最小现实状态：

```text
Actor
+ Action / State Change
+ Object
+ Commercial or Operational Stage
+ Scale
+ Timing
+ Assertion Status
+ Evidence Strength
```

区分已经发生的经营状态与 Guidance、Plan、Forecast、Rumor 或 Scheduled state。消息若宣布未来计划，Observed Reality 是该计划已经被宣布，而不是计划内容已经兑现；这一差异同时影响交易判断和 `assertion_state`。

原消息不足以确定事件身份时，使用 Search A 补齐会改变判断的细节，例如 binding contract 与 MoU、qualification 与 volume production、适用范围、规模、生效时间及发布主体。Search A 可以使用截至 `cutoff_at` 可获得的信息。当现实状态、范围、时间和 assertion status 已足够清楚时，进入 Prior reconstruction。

## 3. Reconstruct Pre-event Prior Expectation

Prior 应恢复消息出现前一刻最合理的 expectation baseline：

```text
D2 structured baseline
+ D1 underlying research
+ Reference View recent reality
+ Fresh pre-event public evidence
```

先从 D2 找到相关 Unit、State、Factors 和 Gaps，再用 D1 解释公司、行业、价值链和市场为何形成该状态；随后用 Reference View 判断现实是否已经继续推进。Reference View 未记录某项信息，不足以单独证明公开市场不知道它。

前三层仍不足以建立可比较 baseline 时，使用 Search B 查找严格早于 `event_boundary_at` 的公开信息。事件后的报道可以帮助澄清新现实，但不用于反向证明事件前市场已知。

证据冲突时，优先形成最贴近事件发生前一刻的 baseline，综合判断：

```text
与受影响变量的直接程度
+ 发布时间与事件的距离
+ 是否已经公开并可能被市场吸收
+ 表述的具体程度
+ 来源是否真正掌握该状态
```

较新的事件前事实可以推进较早的 D1/D2 baseline。最终 `prior_expectation` 应简短、具体、可与 New Reality 比较；已有证据不支持精确概率时，用清楚的阶段、时间、规模或确定性表述。

## 4. Derive the Expectation Delta

使用以下内部对照：

```text
BEFORE — 没有当前消息时会继续相信什么
NOW    — 新现实明确建立了什么
CHANGED— 哪个 expectation lever 必须修订
```

检查 Direction、Timing、Scale、Probability / Certainty、Commercial Stage、Economic Capture、Beneficiary 和 Transmission Path。`expectation_delta` 表达的是被迫发生的预期修订，而不是把新闻改写成“利好”或“利空”。事实为 NEW 仍可能完全符合 baseline，因此 NEW 与 NO_TRADE 可以同时成立。

## 5. Decide Tradability and Direction

围绕三个相连的判断形成 best judgment：

- **Surprise**：New Reality 相对 pre-event baseline 产生了多大的非预期变化。
- **Materiality**：该变化是否影响目标 ticker 的重要业务、盈利、竞争或价值获取路径；结合规模、时间幅度、持续性和经济暴露判断。
- **Transmission**：能否建立清楚的 `Observed Reality → Expectation Variable → Company / Industry Economics → Target Ticker` 因果桥。

交易方向从目标 ticker 的 value capture 推导，而不是从消息主体的正负情绪推导。考虑主要抵消因素，但普通后续不确定性本身不否定已经形成的 expectation gap。

当新现实形成非琐碎 Surprise、改变重要 expectation variable，并能够在当前信息下建立足够清楚的 ticker 方向性传导时，给出 `TRADE` 和 `LONG / SHORT`。完整 thesis、最终财务兑现和精确 valuation 不是前提。

当变化基本符合 baseline、Delta 太小、经济影响不 material，或仍缺少决定 ticker 方向的关键现实连接时，给出具体的 `NO_TRADE` 理由。

## 6. Research Stop Rule

每次补充研究都服务于一个可能改变 Prior、Delta 或 Trade 的具体问题。以下四项已经足以形成明确判断时结束研究：

```text
现实发生了什么
事件前预期是什么
新现实改变了什么
变化如何传导到目标 ticker
```

目标是完成时效敏感的 Case judgment，而不是扩展为完整公司研究。

## 7. Compile W3CaseResult

严格使用 `output_schema.json`：

- `w3_case_id` 精确复制当前 Task。
- `novelty.result` 保持 `NEW`；`reason` 概括新的核心现实。`reference_ids` 只引用 Reference View 中真实存在且与 baseline 有关的 `E#`。
- `policy.policy_ids` 保持空数组；`reason` 简要说明当前 uncovered coverage state。
- `expert_trade.evaluated = true`。`prior_expectation` 写事件前 baseline，`expectation_delta` 写被迫修订的预期，`reason` 压缩说明 Surprise、Materiality 和 Transmission。
- `trade = true` 时填写 `LONG` 或 `SHORT`；`trade = false` 时方向为空。

`RuntimeFactCandidate` 与 Expectation Delta 是不同对象：前者是送入 Event Library 的新增 atomic factual proposition，后者是投资分析中的预期变化。每个 Mode 1 Case 至少输出一个 candidate，包括 `NO_TRADE` Case。

Candidate 只表达当前 NEW reality，不吸收 Prior、背景资料或因果推导。按命题实际状态填写 `assertion_state`；能够确认事实发生日期时填写 `occurrence_date`，`subject_time` 保留命题讨论的报告期、计划期或未来期间，`entities` 保留实际涉及的实体。用最少的独立命题覆盖新现实，最多 12 条。

## Completion Gate

```text
Reality 已明确
Affected Expectation 已定位
Prior 是事件前 baseline
Delta 是 Prior 与 New Reality 的差
Direction 来自目标 ticker transmission
NEW 与 NO_TRADE 已作为独立判断
RuntimeFactCandidate 与 Expectation Delta 已分离
最终结果完全符合 output_schema.json
```
