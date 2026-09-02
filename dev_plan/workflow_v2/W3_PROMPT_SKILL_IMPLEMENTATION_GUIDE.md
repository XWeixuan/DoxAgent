# W3 Agent Prompt / Internal Skill 实现细节指南

本文只补充当前代码实现约束，供重写 W3 Agent Prompt 与两个 Internal Skill 时使用。
业务目标和编排原理仍以 `presist_runtime_w3.md` 为准。

## 1. 当前文件与执行形态

- Agent Prompt：`prompts/persistent_runtime_v2/w3/agent.md`
- Mode 1 Skill：`prompts/persistent_runtime_v2/w3/skills/uncovered_new.md`
- Mode 2 Skill：`prompts/persistent_runtime_v2/w3/skills/revalidate_then_evaluate.md`
- 每个 ticker 有一个持久 Main Thread；主线程忙时才创建一次性 Fallback Thread。
- 一个 Case 必须在一个 Codex turn 内完成，不允许 subagent。
- W3 可使用 SDK Web Search，但没有 Data MCP；不要在 Prompt 中要求调用 Data MCP 工具。
- 不设置搜索次数上限或全局 W3 并发上限；搜索何时停止由 Skill 的业务判断约束。

持久 Main Thread 只提供角色和近期处理连续性，不是事实数据库。每次必须以当前 Case 的
显式文件为权威，禁止沿用上一 Case 的消息或结论。

## 2. 每个 Case 实际可读输入

当前 Case 位于不可变目录：

```text
cases/<w3_case_id>/task.json
cases/<w3_case_id>/context/document1.md
cases/<w3_case_id>/context/document2.json
cases/<w3_case_id>/context/policy_set.json
cases/<w3_case_id>/context/reference_view.md
cases/<w3_case_id>/context/output_schema.json
```

`task.json` 包含：Mode、ticker、消息正文及 metadata、原 W1/W2 最终输出与 reason、
初始路由原因、`cutoff_at`、`event_boundary_at`、版本 pin 和 D2 publication state。
动态 Task 也会内联到本次 user prompt，用于避免 persistent thread 读取旧 Case。

必须读取当前完整 D1、D2、PolicySet 和 Reference View。PolicySet 与 Reference View
只按版本号固定，不存在需要模型校验的哈希。

## 3. 两种 Mode 的硬边界

### Mode 1 — `UNCOVERED_NEW`

热路径已以正常置信度确认 `NEW + no Policy`。W3 必须继承这两个结论：

- `novelty.result` 必须为 `NEW`；
- `policy.policy_ids` 必须为空；
- 只执行 prior expectation / expectation delta / direct trade 判断与 Fact extraction；
- 即使阅读完整 PolicySet，也不能在 Mode 1 中重新改判为已有 Policy 命中。

### Mode 2 — `REVALIDATE_THEN_EVALUATE`

W1 或 W2 至少一项低置信度。W3 在同一 turn 中先重判 NEW/OLD 与 Policy coverage：

- `OLD + no Policy`：归档；
- `OLD + Policy`：归档并记录 BADCASE；
- `NEW + Policy`：执行最匹配 Policy 的既有方向，不做 expert trade evaluation；
- `NEW + no Policy`：继续完成与 Mode 1 相同的专家交易判断。

Policy 命中必须满足完整 activation conditions / calibration，不能只因主题相似而命中；
多个 Policy 时最匹配者排第一，运行时只执行第一项的既有方向。

## 4. Strict Output 必须满足的关系

统一输出 `W3CaseResult`，不要输出 Markdown、解释性前后缀或 confidence：

```text
w3_case_id
novelty { result, reference_ids, reason }
policy { policy_ids, reason }
expert_trade { evaluated, trade, direction, prior_expectation,
               expectation_delta, reason }
delta_candidates[]
```

硬约束：

- `w3_case_id` 必须与当前 Task 完全一致；不要复用上一 Case ID 或结论。
- `OLD` 必须引用至少一个当前 Reference View 中真实存在的 `E#`，且必须：
  `delta_candidates=[]`、`expert_trade.evaluated=false`。
- 每个最终 `NEW` 都必须输出至少一个 `RuntimeFactCandidate`，包括 `NEW + Policy`。
- `NEW + Policy`：`expert_trade.evaluated=false`，不得改写 Policy 方向。
- `NEW + no Policy`：`expert_trade.evaluated=true`；必须填写
  `prior_expectation` 和 `expectation_delta`。
- `trade=true` 时 direction 只能为 `LONG` 或 `SHORT`；`trade=false` 时 direction 必须为空。
- `RuntimeFactCandidate` 字段只有：`proposition`、`assertion_state`、可选
  `subject_time`、可选精确 `occurrence_date`、`entities[]`；最多 12 条。
- Policy ID 必须来自当前完整 PolicySet；Reference ID 必须来自当前 Reference View。

JSON Schema 无法完整表达上述跨字段约束，运行时还会用 Pydantic fail-closed 校验，
因此 Prompt/Skill 必须明确写出这些关系。

## 5. Research 与交易判断边界

- Search A 用于确认“实际发生了什么”，证据可使用到 `cutoff_at`。
- Search B 用于重建事件前预期，只能使用严格早于 `event_boundary_at` 的证据。
- 不得使用事件后 hindsight 重构 prior expectation。
- Prior expectation 需要联合读取 D1、D2、Reference View，不能只看市场隐含数据。
- 直接交易要求同时成立：surprise、materiality、对目标 ticker 的 direct transmission。
- 普通不确定性不是自动 NO_TRADE 理由，但证据不足、偏差不重要或传导不直接可以 NO_TRADE。
- W3 不输出 confidence，也不负责正式 Event/Fact canonicalization；它只输出
  RuntimeFactCandidate，后续由 O2 处理。

## 6. 下游闭环提示

- 所有最终 `NEW` 的 FactCandidate 都进入 Runtime Delta。
- `NEW + no Policy` 无论 TRADE/NO_TRADE，都会形成 W3 Coverage Gap，供 Daily Close
  送入 O3 MAINTAIN；因此不要为了“避免创建新 Policy”而错误命中现有 Policy。
- `NEW + no Policy + TRADE` 由 W3 直接生成交易记录；`NEW + Policy` 使用 PolicySet
  中已有方向；Prompt 不需要输出最终 route 或 side effects。
