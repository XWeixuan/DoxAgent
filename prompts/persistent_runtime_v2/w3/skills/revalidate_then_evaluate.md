# W3 Mode 2 — REVALIDATE_THEN_EVALUATE

当前 Case 进入 W3，是因为 W1 的事实新旧判断、W2 的 Policy coverage 判断，或两者至少一项存在足以影响 Runtime 路由的歧义。本 Turn 先恢复可靠的 Case State；只有最终确认为 `NEW + no Policy`，才进入 uncovered-event Alpha evaluation。

```text
Stage A — Resolve the Case State
Novelty?
+ Policy Coverage?
→ OLD / NEW × Policy / no Policy

Stage B — Evaluate the Coverage Gap
仅在 NEW + no Policy 时
→ 读取并执行 skills/uncovered_new.md
```

## Working Inputs

Stage A 主要使用：

- `task.json`：当前消息、原 W1/W2 结果与 reason、原 reference IDs / policy IDs、时间及版本边界。
- `reference_view.md`：当前 version-pinned 现实中已经存在的 Event / Fact，是 Novelty 重判的比较面。
- `policy_set.json`：当前 version-pinned 的完整有效 Policy universe，是 Policy coverage 重判的依据。
- `output_schema.json`：统一 `W3CaseResult` 合同。

把原 W1/W2 结果视为已有 hypothesis 和检索线索：先理解其依据，再使用完整上下文形成当前最佳最终判断。D1/D2 在转入 `NEW + no Policy` 的 Stage B 后用于 Prior、Delta 与 Tradability 研究。

## 1. Establish the Core Fact Set

先从当前消息提取决定现实身份和 Runtime 路由的最小 Core Fact Set：

```text
Actor
+ Action / State
+ Object
+ Stage
+ Material Scope
+ Timing
+ Assertion Status
```

背景、评论和重复表述不改变 Case State。原消息不足以明确事实身份时，可使用聚焦 Web Search 澄清截至 `cutoff_at` 的事件细节，例如重复披露与新状态、MoU 与 binding contract、qualification 与 volume commitment、既有预测与新 Guidance。此处研究只服务于 Novelty 和 Policy coverage，不提前展开 Prior Expectation 或交易判断。

## 2. Re-adjudicate W1 — Factual Novelty

Novelty 判断的是现实 Core Facts 是否已被 Reference View 覆盖，而不是文章、来源或措辞是否首次出现。

对每项 Core Fact 建立 coverage comparison：

```text
Reference View 中哪个 E# 覆盖它？
已有事实覆盖到什么现实层级？
当前消息是否新增 Actor-State、Stage、Timing、Scale、Scope 或 Assertion change？
```

裁定标准：

```text
全部 Core Facts 已由已有 Event / Fact
以相同或更具体的现实语义覆盖
→ OLD

至少一个决定现实身份的 Core Fact 尚未覆盖
→ NEW
```

同一主题不等于相同现实；新的商业阶段、时间、规模、适用范围或正式程度可能构成 NEW。新来源、新措辞或对同一事实的重复说明不改变 Novelty。

最终为 `OLD` 时，`reference_ids` 至少包含一个 Reference View 中真实存在、足以支持覆盖判断的 `E#`。最终为 `NEW` 时，可以引用与已知 baseline 有关的真实 `E#`，但这些引用不替代对新增事实的说明。

## 3. Re-adjudicate W2 — Policy Coverage

先检查原 W2 的候选与 reason，再使用完整 PolicySet 判断是否存在遗漏或误命中的 Policy。

```text
match_scope
→ 召回可能相关的 Policy

activation_conditions / criterion
→ 当前消息是否使各 Boolean predicates 成立

calibration
→ 当前现实是否真正跨过 reference_state
   与 trigger_boundary
```

主题相关只产生 Candidate；当前消息满足 Activation Conditions 才形成 Policy hit。对于多条件 Policy，现行语义要求同一条消息同时满足全部 Conditions；只满足部分条件时，当前 Policy 尚未激活。

最终 `policy_ids` 只使用当前 PolicySet 中真实存在且已被消息触发的 ID。多个 Policy 均成立时，将最直接覆盖当前 Core Fact Set 的 Policy 排在第一。Policy 的既定交易方向由 Runtime 使用，W3 在此不重新决定方向。

## 4. Resolve the Final Case State

Stage A 必须收敛到以下一种状态：

| Final State | 业务含义 | 本 Turn 行为 |
| --- | --- | --- |
| `OLD + no Policy` | 已知现实，没有 Policy 触发 | Archive；不做 Expert Trade；无 Delta Candidate |
| `OLD + Policy` | 已知现实仍能触发现行 Policy | Archive + BADCASE；不做 Expert Trade；无 Delta Candidate |
| `NEW + Policy` | 新现实已由现行 Policy 覆盖 | 返回 Policy hit；不做 Expert Trade；提取 NEW facts |
| `NEW + no Policy` | 出现真实 Policy coverage gap | 在同一 Turn 进入 Stage B |

`OLD + Policy` 表示 Policy 的现实坐标可能与当前 baseline 不同步，Runtime 会据此形成 BADCASE 反馈。W3 只需交付正确的 Novelty 与 Policy 状态。

W3 不输出新的 confidence 或 escalation level；完整 Reference View 和 PolicySet 用于形成 best final state。

## 5. Stage B — Explicit Mode Transition

一旦 Stage A 收敛为 `NEW + no Policy`，把 Novelty 与 Policy coverage 视为本 Turn 已解决的前提。立即读取 `skills/uncovered_new.md`，从其 Case Frame 开始完成完整的 Observed Reality、Prior Expectation、Expectation Delta、Tradability 与 RuntimeFactCandidate 研究；继续使用同一消息、同一 context version 和同一个 `W3CaseResult`。

## 6. Final NEW Fact Extraction

每个最终 `NEW` Case 都至少需要一个 `RuntimeFactCandidate`，包括 `NEW + Policy`。从 Core Fact Set 提取尚未被 Reference View 覆盖的 atomic factual propositions，保留真实 assertion state、occurrence date、subject time 和 entities；Prior、Policy 判断、Expectation Delta 与交易推导不属于事实命题。

最终为 `OLD` 时，`delta_candidates` 为空。

## 7. Compile W3CaseResult

严格使用 `output_schema.json`：

```text
OLD
→ reference_ids 非空
→ delta_candidates = []
→ expert_trade.evaluated = false

NEW + Policy
→ policy_ids 非空
→ delta_candidates 非空
→ expert_trade.evaluated = false

NEW + no Policy
→ policy_ids = []
→ delta_candidates 非空
→ expert_trade.evaluated = true
→ TRADE / NO_TRADE 来自 uncovered_new.md
```

所有结果均精确复制当前 `w3_case_id`。`novelty.reason` 解释 factual coverage，`policy.reason` 解释 Condition coverage；E# 与 Policy ID 只能来自当前 version-pinned inputs。

## Completion Gate

```text
Core Fact Set 已明确
原 W1/W2 结果被作为 hypothesis 复核
每个 OLD core fact 都有 E# 覆盖
Policy hit 基于 Conditions，而不是主题相关
多条件按 same-message AND 判断
Case State 已收敛到四种之一
NEW + Policy 已产生 Delta Candidate
NEW + no Policy 已执行 uncovered_new.md
最终结果完全符合 output_schema.json
```
