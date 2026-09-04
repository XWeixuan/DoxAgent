# Persistent Runtime V2：W1/W2 当前实现上下文

> 用途：为后续重写 `core.md`、`w1_r1.md`、`w1_r2.md`、`w1_r3.md`、
> `w2_r1.md`、`w2_r2.md` 提供当前代码事实。本文描述的是现有实现，不是新的
> Prompt 设计建议。

## 1. 总体调用结构

每条 Message Bus 消息先被转换为 `SourceMessageEnvelope`。Runtime 随后读取：

- 当前 Published Known Event Index；
- 当前 Published Runtime Policy Projection；
- 当日 SQLite provisional facts；
- Event Library / PolicySet 的当前版本号。

W1 与 W2 在 hot path 中并行启动：

```text
                         ┌─ W1 R1 ─previous_response_id→ W1 R2
SourceMessageSnapshot ───┤
                         └─ W2 R1 ─conditional continuation→ W2 R2

W1 R2 ──若确定性路由产生 EMIT_DELTA──previous_response_id→ W1 R3（异步 effect）
```

当前分轮条件：

- W1 R1、W1 R2：每个正常 hot-path Case 都执行。
- W2 R1：每个正常 hot-path Case 都执行。
- W2 R2：仅当 W2 R1 同时满足 `policy_ids 非空 + confidence=low` 时执行。
- W1 R3：不是 hot-path 必经轮；仅在路由创建 `EMIT_DELTA` effect 后异步执行。
  当前正常快路径中主要是 `W1=NEW/normal + W2 policy hit/normal → TRADE`
  触发。进入 W3 的 Case 由 W3 自己产生 Delta，不执行 W1 R3。

## 2. Prompt 与 Responses 请求如何拼装

每轮的 model instructions 都是：

```text
<core.md 原文>

<当前 round prompt 原文>

# Exact Output Contract
<transport 自动追加的严格 JSON Schema 及固定输出要求>
```

也就是说，输出 Schema 不靠 `core.md` 或 round prompt 自己维护。Transport 会从
当前 Pydantic output model 动态生成 Schema，展开本地 `$ref`，为所有 object 加上
`additionalProperties=false`，然后同时放进：

- `instructions` 末尾的 Exact Output Contract；
- Bailian Responses API 的 `text.format.json_schema`，`strict=true`。

每轮业务输入则单独序列化为紧凑 JSON，放进 Responses API 的 `input`。API metadata
另外包含 `runtime=persistent_v2`、`case_id`、`lane`、`round`，但它不是业务 payload。

当前 transport 固定配置为：

```text
provider = bailian
model = qwen3.8-flash
reasoning_effort = medium
store = true
session cache = enabled（默认）
```

R2/R3 会使用 `previous_response_id` 续接同一 lane 的上一轮 Response。因此，后续轮
实际可见的上下文是“当前轮 JSON input + provider 保存的上一轮对话上下文”，不是只看
当前轮 payload。

## 3. 共用输入对象

### 3.1 `SourceMessageSnapshot`

W1/W2 不会直接看到完整 `SourceMessageEnvelope`，只看到：

```jsonc
{
  "ticker": "MU",          // string，trim 后转大写，非空
  "title": "...",         // string | null
  "body": "..."           // string | null
}
```

`title` 和 `body` 至少一个必须有非空内容。

以下 Envelope 字段不会放进 `source_message`：

```text
source_message_id, source_id, binding_id, url,
published_at, collected_at, normalized_at,
message_bus_event_time, stream_item_id, member_count
```

因此当前 W1/W2 Prompt 不能假设模型直接知道来源、URL、发布时间、采集时间或消息 ID。

### 3.2 `RuntimeVersionPin`

```jsonc
{
  "event_library_version": 7,        // integer >= 1
  "provisional_snapshot_version": 3, // integer >= 0
  "policy_set_version": 5,           // integer >= 1
  "runtime_projection_version": 5    // integer >= 1
}
```

Case 创建时：

- `event_library_version` 来自当前 Published Known Event Index；
- `provisional_snapshot_version` 来自当前 ticker + trading date 的 SQLite 状态；
- `policy_set_version` 来自 Runtime Policy Projection；
- 当前实现令 `runtime_projection_version = policy_set_version`。

Trading date 使用消息 `published_at` 转为 America/New_York 后的日期。

### 3.3 `RuntimeFactCandidate`

W1 R3 输出它，W1 R1/R2 也会在 provisional records 内看到它：

```jsonc
{
  "proposition": "...",          // string，1..4000
  "assertion_state": "ACTUAL",   // enum，见下方
  "subject_time": "FY2027 Q1",   // string <= 500 | null，默认 null
  "occurrence_date": "2026-09-02", // YYYY-MM-DD | null，默认 null
  "entities": ["Micron"]         // string[]，最多 32；去空白、去重，默认 []
}
```

`assertion_state` 当前完整枚举：

```text
ACTUAL, GUIDANCE, FORECAST, PLAN, RUMOR, DENIAL,
SCHEDULED, ONGOING, PLANNED, EXPECTED,
RUMORED, DENIED, HYPOTHETICAL, UNKNOWN
```

### 3.4 `ProvisionalFactDetail`

W1 当前注入的是完整持久化 DTO，而不是只注入 candidate：

```jsonc
{
  "provisional_event_id": "E186", // /^E[1-9][0-9]*$/
  "ticker": "MU",
  "trading_date": "2026-09-02",
  "source_message_id": "...",
  "candidate_index": 0,            // integer >= 0
  "candidate": { /* RuntimeFactCandidate */ },
  "runtime_signature": "<64 hex sha256>",
  "snapshot_version": 3,           // integer >= 1
  "created_at": "<datetime>"
}
```

这意味着，虽然原始 Source Message 的运维字段被隔离，intraday provisional context
目前仍会把 `source_message_id`、signature、snapshot version、created_at 等字段暴露给
W1。

## 4. W1 各轮输入

## 4.1 W1 R1 — Candidate Recall

当前 payload：

```jsonc
{
  "source_message": { /* SourceMessageSnapshot */ },
  "version_pin": { /* RuntimeVersionPin */ },
  "published_known_event_index": "<compiled text>",
  "today_provisional_facts": [ /* ProvisionalFactDetail[] */ ]
}
```

### `published_known_event_index` 的真实格式

它不是 JSON Event 数组，而是一段由 Event Library 编译的文本。每行一个 Published
Event，按 occurrence time 倒序排列：

```text
E42 | 2026-08-30 | HBM4 customer qualification completed | recognition-oriented summary
E41 | 2026-08-28 | Fiscal-quarter results released
```

行结构实际为：

```text
event_id | displayed occurrence date/time | title | optional known_event_summary
```

如果 `known_event_summary` 与 title 重复，最后一列会省略。R1 看不到完整 Canonical
Event/Facts，只能用这份 compact index 和当日 provisional records 做候选召回。

R1 返回 ID 后，编排会再截断到前 5 个。然后：

- 与当日 provisional E# 对上的 ID 从 Runtime SQLite 读取；
- 其他 ID 按固定 `event_library_version` 从 Published Event Library 读取；
- 任一请求 ID 缺失会作为 infrastructure unavailable 失败，不交给 R2 猜测。

## 4.2 W1 R2 — Fact Coverage / Novelty

当前 payload：

```jsonc
{
  "source_message": { /* SourceMessageSnapshot */ },
  "event_details": {
    "event_library_version": 7,     // integer >= 1
    "requested_event_ids": ["E42", "E186"],
    "canonical_events": [ /* CanonicalEvent[] */ ],
    "provisional_events": [ /* ProvisionalFactDetail[] */ ],
    "missing_event_ids": []
  }
}
```

同时设置：

```text
previous_response_id = W1 R1 response_id
```

`RuntimeEventDetailEnvelope` 中的 `missing_event_ids` 在调用前已被编排保证为空。

`canonical_events` 里的每个 `CanonicalEvent` 当前会完整序列化，主要 Schema 为：

```jsonc
{
  "event_id": "E42",
  "ticker": "MU",
  "title": "...",
  "event_type": "PRODUCT_MILESTONE",
  "occurred_at": "2026-08-30",
  "occurrence_time_precision": "DAY",
  "status": "ACTIVE",              // ACTIVE | SUPPRESSED | MERGED
  "canonical_summary": "...",
  "known_event_summary": "...",
  "is_important": true,
  "include_in_reference_view": true,
  "related_event_ids": [],
  "supersedes_event_id": null,
  "derived_from_event_ids": [],
  "facts": [
    {
      "fact_id": "F88",
      "proposition": "...",
      "assertion_state": "ACTUAL",
      "subject_time": "SAME",       // "SAME" | free-form string | null
      "fact_occurred_at": "2026-08-30",
      "fact_occurrence_time_precision": "DAY"
    }
  ],
  "price_analysis": null             // object | null；下游保留字段
}
```

`occurrence_time_precision` 枚举为：

```text
TIMESTAMP, DAY, MONTH, QUARTER, YEAR, INTERVAL, UNKNOWN
```

R2 之后还有本地语义 Gate：输出的每个 `reference_id` 必须属于本轮实际加载的
canonical/provisional Event IDs。

## 4.3 W1 R3 — Atomic Fact Extraction

当前 payload：

```jsonc
{
  "source_message": { /* SourceMessageSnapshot */ },
  "capture_mode": "NEW_CAPTURE",  // NEW_CAPTURE | AMBIGUOUS_CAPTURE
  "w1_final": {
    "result": "NEW",
    "confidence": "normal",
    "reference_ids": ["E42"],
    "reason": "..."
  }
}
```

同时设置：

```text
previous_response_id = W1 R2 response_id
```

`w1_final` 的类型是 `W1NoveltyResult | null`，但正常新建 effect 时会有值。
`capture_mode` 由编排确定：

- `w1_final.result == NEW` → `NEW_CAPTURE`；
- 否则 → `AMBIGUOUS_CAPTURE`。

当前 W3 路由会自己负责 exception-plane Delta，因此低置信 Case 不通过 W1 R3；
`AMBIGUOUS_CAPTURE` 主要保留给已有/兼容 effect，而不是当前正常 hot-path 的主分支。

## 5. W2 各轮输入

## 5.1 W2 R1 — Compact Policy Match

当前 payload：

```jsonc
{
  "source_message": { /* SourceMessageSnapshot */ },
  "version_pin": { /* RuntimeVersionPin */ },
  "runtime_policy_projection": {
    "schema_version": "document3.runtime_projection.v2",
    "ticker": "MU",
    "policy_set_version": 5,
    "policy_set_published_at": "<datetime>",
    "policies": [
      {
        "policy_id": "pol_xxx",
        "match_scope": "...",
        "criterion": ["condition A", "condition B"],
        "activation_summary": "..."
      }
    ]
  }
}
```

重要实现语义：projection 是“一条 Policy 一行”；同一 Policy 的 `criterion[]` 来自其
全部 activation conditions。当前 Prompt 将数组解释为同消息 AND，但 Schema 本身只
表达“非空、去重字符串数组”，AND 语义不由 validator 强制。

R1 后有本地语义 Gate：返回的每个 `policy_id` 必须存在于这份固定 projection。

## 5.2 W2 R2 — Full Policy Calibration

只有 R1 返回至少一个 ID 且 `confidence=low` 才读取完整 Policy 并调用 R2。

当前 payload：

```jsonc
{
  "source_message": { /* SourceMessageSnapshot */ },
  "policy_details": {
    "ticker": "MU",
    "policy_set_version": 5,
    "requested_policy_ids": ["pol_xxx"],
    "policies": [ /* Policy[] */ ],
    "missing_policy_ids": []
  }
}
```

同时设置：

```text
previous_response_id = W2 R1 response_id
```

完整 `Policy` Schema：

```jsonc
{
  "policy_id": "pol_xxx",
  "title": "...",
  "source_refs": [
    {
      "shell_id": "S1",
      "expectation_id": "U1",
      "gap_id": "G1"
    }
  ],
  "decision": "LONG",              // LONG | SHORT
  "match_scope": "...",
  "activation_conditions": [
    {
      "condition_id": "C1",
      "criterion": "...",
      "calibration": {
        "reference_state": "...",
        "trigger_boundary": "...",
        "qualifying_evidence": "..."
      }
    }
  ],
  "activation_summary": "..."
}
```

所以 W2 R2 会看到完整 Policy direction、D2 provenance IDs、每条 condition 及完整
calibration，而 R1 看不到这些字段。

调用 R2 前，任一请求 Policy 缺失都会作为 infrastructure unavailable 失败。
R2 后还有本地语义 Gate：最终 `policy_ids` 必须是 R1 所选 IDs 的子集，R2 不能新增
R1 未召回的 Policy。

## 6. 各轮输出 Schema

所有 output model 都是 `extra="forbid"`。Provider wire Schema 也会把所有 object 设为
`additionalProperties=false`。

### 6.1 W1 R1：`W1Round1Result`

```jsonc
{
  "event_ids": ["E42", "E186"]
}
```

约束：

- `event_ids` 默认 `[]`；
- 每个 ID trim + uppercase；
- 必须满足 `E` + 纯数字；
- 去重并保持首次出现顺序；
- Pydantic Schema 本身没有 `maxItems=5`，但 Prompt 要求最多 5，编排也会硬截断前 5。

### 6.2 W1 R2：`W1NoveltyResult`

```jsonc
{
  "result": "NEW",            // NEW | OLD，必填
  "confidence": "normal",     // normal | low，必填
  "reference_ids": ["E42"],   // E#[]，最多 3，默认 []
  "reason": "..."             // string，1..1000，必填
}
```

除字段约束外还有两个运行时约束：

- `result=OLD` 时 `reference_ids` 至少一个；
- 所有 `reference_ids` 必须属于本轮实际加载的 Event IDs。

### 6.3 W1 R3：`W1FactExtractionResult`

```jsonc
{
  "candidates": [
    {
      "proposition": "...",
      "assertion_state": "ACTUAL",
      "subject_time": null,
      "occurrence_date": "2026-09-02",
      "entities": ["Micron"]
    }
  ]
}
```

约束：

- `candidates` 必填，最少 1、最多 12；
- 每个 candidate 的 `proposition`、`assertion_state` 必填；
- `subject_time`、`occurrence_date`、`entities` 在 domain Schema 中有默认值；
- 不允许输出 E#/F#/Delta ID、signature 等身份字段，因为 output model 没有这些字段。

### 6.4 W2 R1 / W2 R2：`W2PolicyResult`

两轮使用完全相同的 output model：

```jsonc
{
  "policy_ids": ["pol_xxx"], // string[]，最多 3，默认 []
  "confidence": "normal",    // normal | low，必填
  "reason": "..."            // string，1..1000，必填
}
```

约束：

- `policy_ids` trim、去空字符串、去重并保持首次出现顺序；
- R1 IDs 必须来自 Runtime Policy Projection；
- R2 IDs 必须是 R1 requested IDs 的子集；
- Schema 不包含 decision/direction；实际交易方向在编排层按首个 Policy ID 回读
  version-pinned canonical PolicySet。

## 7. 重写 Prompt 时必须保持的实现边界

1. `core.md + round prompt` 是共同 instructions；Exact Output Contract 由 transport
   自动追加，不应在 Prompt 中维护另一套可能漂移的 JSON Schema。
2. W1/W2 没有 Web Search、Data MCP 或其他工具；只能用注入上下文。
3. R1 与 R2/R3 之间存在 Responses continuation。不能把后续 round 当成完全无状态的
   独立请求，也不能假设它只看当前 JSON。
4. W1 R1 只看 compact Published index，但会看到完整 intraday provisional DTO；W1 R2
   才看完整 selected CanonicalEvent/Facts。
5. W2 R1 只看 compact projection；W2 R2 才看 selected full Policy/Calibration。
6. W2 R2 不能召回新 Policy；它只能裁决 R1 已召回的候选。
7. `confidence=low` 会改变控制流：W2 可进入 R2，最终任一 W1/W2 low 会进入 W3。
8. W1/W2 output 的业务一致性除了 JSON Schema，还由本地 Pydantic validator 和
   orchestration subset Gate 二次校验；Prompt 不应描述与这些 Gate 冲突的输出行为。

## 8. 当前代码入口

- 输入/输出 DTO：`src/doxagent/persistent_runtime_v2/schema.py`
- W1/W2 payload 组装、continuation、retry：
  `src/doxagent/persistent_runtime_v2/service.py`
- Prompt 拼装：`src/doxagent/persistent_runtime_v2/prompts.py`
- Bailian Responses strict transport：`src/doxagent/persistent_runtime_v2/transport.py`
- Event Library 输入 adapter：`src/doxagent/persistent_runtime_v2/providers.py`
- Known Event Index 编译格式：`src/doxagent/event_library/compiler.py`
- Canonical Event/Fact Schema：`src/doxagent/event_library/contracts.py`
- Runtime Policy Projection / full Policy Schema：
  `src/doxagent/workflows/codex_document3/schema.py`
