# Persistent Runtime Response API 合同核验

> 目的：给网页版 ChatGPT 作为持久化运行模块重构的事实基线。
>
> 核验日期：2026-08-27。
>
> 本文只讨论 V2 D3/O3、V2 O2 Event Library，以及为“每条消息多轮 Response API”需要补齐的协议。V1 workflow 与 V2 完全隔离；V1 的 W1/W2/O3 接口将废弃，不作为本文的设计依据，也不应在新实现中复用其语义或代码。

## 0. 结论先行

当前 V2 代码已经具备：

- D3 Canonical PolicySet；
- 从 PolicySet 确定性生成的低负载 RuntimePolicyProjection；
- O2 Published Event Library 的 KnownEventIndexSnapshot；
- O2 按 Event ID 读取完整 EventDetailSnapshot；
- O2 接受的正式 DeltaBatch / DeltaItem 合同。

当前 V2 代码还缺少：

- 面向 W2 的按 policy_id 读取 Policy Detail 的独立 API/envelope；
- 面向 W2 第三轮的“消息级 Delta candidate”合同；
- candidate 到 O2 正式 DeltaBatch 的日终确定性 adapter；
- Response API session 的 version/hash pin、detail request、缺失 ID 和重放协议。

建议的目标边界：

~~~text
D3/O3 PolicySet
    └─> Runtime Policy Projection (W2 Round 1)
             └─> selected Policy Detail (W2 Round 2)

O2 Published Event Library
    └─> Known Event Index (W1 Round 1)
             └─> selected Event Detail (W1 Round 2)

W2 Round 3
    └─> message-level Delta candidate
             └─> daily deterministic adapter
                     └─> O2 DeltaBatch(PENDING)
~~~

W2 不直接写 O2 Canonical Event/Fact，不生成稳定 E#/F#，不把策略判断结果冒充 O2 Delta。O2 仍负责 occurrence identity、Fact 合并、revision、stable ID 和 KEEP_PENDING。

## 1. V2 权威组件和边界

### 1.1 D3/O3

权威代码：

- src/doxagent/workflows/codex_document3/schema.py
- src/doxagent/workflows/codex_document3/runtime_projection.py
- src/doxagent/workflows/codex_document3/repository.py
- dev_plan/workflow_v2/d3_plan.md

D3/O3 是低频、研究密集的 Policy 编译/维护层。Canonical source 是完整 PolicySet。Runtime 只应读取由该 Policy Set 确定性生成的紧凑 Projection；D2、完整 D3 Policy Set 和 Calibration 不应作为每条消息的默认大上下文。

### 1.2 O2 Event Library

权威代码：

- src/doxagent/event_library/provider.py
- src/doxagent/event_library/contracts.py
- src/doxagent/event_library/compiler.py
- src/doxagent/event_library/delta_compiler.py

O2 保留完整 Canonical Event/Fact 历史，并提供两个不同视图：

- Known Event Index：给 W1 做完整、紧凑的历史事件识别；
- Event Detail：按稳定 Event ID 展开完整 Event 和全部 active Fact。

Reference View 是给 D2/D3 现实基线使用的另一种视图，不应与 W1 Known Event Index 混称。

## 2. O3 → W2：Policy 索引和 Detail

### 2.1 Canonical source：PolicySet

PolicySet 当前定义于 src/doxagent/workflows/codex_document3/schema.py:124。完整 wire 形状：

~~~json
{
  "schema_version": "document3.v2",
  "ticker": "MU",
  "policy_set_version": 12,
  "publication_state": "COMPLETE",
  "document2_ref": {
    "run_id": "d2-run-...",
    "artifact_id": "d2-artifact-...",
    "sha256": "<64 lowercase hex>",
    "published_at": "2026-08-26T12:00:00Z",
    "publication_state": "COMPLETE"
  },
  "event_library_ref": {
    "contract_version": "reference-view-md-v4",
    "ticker": "MU",
    "version": 42,
    "sha256": "<64 lowercase hex>",
    "published_at": "2026-08-26T12:00:00Z"
  },
  "policies": [
    {
      "policy_id": "pol_001",
      "title": "客户进入重复规模采购",
      "source_refs": [
        {"shell_id": "S1", "expectation_id": "EU1", "gap_id": "G1"}
      ],
      "decision": "LONG",
      "match_scope": "客户 qualification、adoption、commercial shipment 相关消息",
      "activation_conditions": [
        {
          "condition_id": "C1",
          "criterion": "同一消息确认客户已经进入持续商业量产采购",
          "calibration": {
            "reference_state": "当前仍处于有限验证或试用阶段",
            "trigger_boundary": "进入持续商业量产采购",
            "qualifying_evidence": "公司或客户正式确认重复量产供货"
          }
        }
      ],
      "activation_summary": "同一消息确认客户进入持续商业量产采购时做多 MU"
    }
  ],
  "published_at": "2026-08-26T12:00:00Z"
}
~~~

单个 Policy 字段：

~~~text
policy_id
title
source_refs[]                 # shell_id / expectation_id / gap_id
decision                      # LONG | SHORT
match_scope
activation_conditions[]
activation_summary
~~~

单个 ActivationCondition 字段：

~~~text
condition_id
criterion
calibration.reference_state
calibration.trigger_boundary
calibration.qualifying_evidence
~~~

PolicySet 版本按 ticker 单调递增，并由 expected-base CAS 保护。event_library_ref 只记录 O3 初始化/维护时参考的 O2 Reference View 版本，不代表 Runtime 要同步读取 Event Library。

### 2.2 Round 1 当前已实现的索引：RuntimePolicyProjection

RuntimePolicyProjection 定义于 schema.py:277，由 runtime_projection.py:25 的 project_policy_set() 确定性生成：

~~~json
{
  "schema_version": "document3.runtime_projection.v1",
  "ticker": "MU",
  "policy_set_version": 12,
  "policy_set_published_at": "2026-08-26T12:00:00Z",
  "conditions": [
    {
      "policy_id": "pol_001",
      "title": "客户进入重复规模采购",
      "decision": "LONG",
      "match_scope": "客户 qualification、adoption、commercial shipment 相关消息",
      "condition_id": "C1",
      "criterion": "同一消息确认客户已经进入持续商业量产采购",
      "activation_summary": "同一消息确认客户进入持续商业量产采购时做多 MU"
    }
  ]
}
~~~

字段语义：

- schema_version 固定为 document3.runtime_projection.v1；
- ticker 是目标证券；
- policy_set_version 是 Projection 所属 Published Policy Set 版本；
- policy_set_published_at 是该版本发布时间；
- conditions[] 每个 Policy 的每个 Activation Condition 一行；一个 Policy 有多个 Condition 时 policy_id 重复是正常的；
- policy_id 是稳定 Policy 身份，W2 命中后应回显；
- decision 是 Policy 成立时的预定方向；
- match_scope 是候选召回范围，不等于触发条件；
- condition_id 是 Policy 内稳定 Condition 身份；
- criterion 是最终判断命题；
- activation_summary 是运行时快速理解的紧凑表达。

当前 Projection 不包含 calibration、source_refs、document2_ref、event_library_ref、publication_state，也不包含 Projection 自身的 sha256/size_bytes。若 Response API 需要 hash pin，应由 runtime session envelope 携带，不能假称它已经是 Projection 字段。

### 2.3 当前没有独立 Policy Detail API

V2 D3 repository 当前提供：

- get_current() / get_version()：读取整个 PolicySet；
- get_current_projection() / get_projection()：读取整个 Projection；
- list_version_metadata()：读取不含完整 Policy JSON 的历史元数据。

当前没有 get_policy(ticker, policy_set_version, policy_id)，也没有 PolicyDetailSnapshot。因此第二轮 Detail 只能由编排器从固定版本完整 PolicySet.policies[] 中筛选被选中的 Policy。完整 Policy Detail 的事实 schema 就是 2.1 的单个 Policy，其中必须保留完整 activation_conditions[].calibration 和 source_refs[]。

### 2.4 建议新增的 W2 Detail envelope

为让 Response API 第二轮成为明确、可校验的边界，建议新增（这不是当前 schema）：

~~~json
{
  "schema_version": "document3.policy_detail.v1",
  "ticker": "MU",
  "policy_set_version": 12,
  "index_sha256": "<Round 1 Projection hash>",
  "requested_policy_ids": ["pol_001"],
  "policies": [
    {
      "policy_id": "pol_001",
      "title": "客户进入重复规模采购",
      "source_refs": [
        {"shell_id": "S1", "expectation_id": "EU1", "gap_id": "G1"}
      ],
      "decision": "LONG",
      "match_scope": "客户 qualification、adoption、commercial shipment 相关消息",
      "activation_conditions": [
        {
          "condition_id": "C1",
          "criterion": "同一消息确认客户已经进入持续商业量产采购",
          "calibration": {
            "reference_state": "当前仍处于有限验证或试用阶段",
            "trigger_boundary": "进入持续商业量产采购",
            "qualifying_evidence": "公司或客户正式确认重复量产供货"
          }
        }
      ],
      "activation_summary": "同一消息确认客户进入持续商业量产采购时做多 MU"
    }
  ],
  "missing_policy_ids": []
}
~~~

建议约束：

1. policy_set_version 必须与 Round 1 相同；
2. index_sha256 绑定 Round 1 实际读取的 Projection；
3. requested_policy_ids 必须是稳定 policy_id，不能是数组下标或 title；
4. policies[] 只返回请求的完整 Policy；
5. missing_policy_ids 显式报告缺失 ID；缺失不能静默等同于“没有命中”；
6. Detail 读取失败时返回不可判定/可重试状态，不自动转成无关消息。

## 3. O2 Event Library → W1：Index 和 Detail

### 3.1 Round 1 当前已实现的索引：KnownEventIndexSnapshot

src/doxagent/event_library/provider.py:20 定义：

~~~json
{
  "contract_version": "known-event-index-v2",
  "ticker": "MU",
  "version": 42,
  "published_at": "2026-08-26T12:00:00Z",
  "known_event_index": "E13 | 2026-06-24 | Micron FY2026 Q3 earnings release | On Jun. 24, Micron reported ...\\nE14 | 2026-08-10 | Micron KeyBanc forum update\\n",
  "sha256": "<known_event_index UTF-8 bytes SHA-256>"
}
~~~

known_event_index 不是 JSON 数组，而是无表头 Markdown/text，每个 active Published Event 一行：

~~~text
event_id | occurred_at_or_range | title [| known_event_summary]
~~~

当前 compiler.py:169 的确定性规则：

- 固定前三列：event_id、显示用 occurred_at、title；
- 第四列 known_event_summary 在规范化后与 title 相同，或只多出末尾 event/occurrence 时省略；
- 行内换行折叠为空格，字段中的 | 转义为 \|；
- 明确带时区的时间转为 America/New_York 日期；无时区值不平移；
- 按发生时间倒序、event_id 升序确定性排序；
- event_type、status 不进入 Index；
- Index 不是 Top-K，必须包含全部 active Published Event；
- 初始化时 Published Library 为空时，known_event_index 可以是空字符串。

Known Event Index 是 W1 的完整历史存在性视图，不是候选检索结果。

### 3.2 Round 2 当前已实现的 Detail：EventDetailSnapshot

src/doxagent/event_library/provider.py:29 定义的 wrapper：

~~~json
{
  "ticker": "MU",
  "version": 42,
  "events": [
    {
      "event_id": "E13",
      "ticker": "MU",
      "title": "Micron FY2026 Q3 earnings release",
      "event_type": "EARNINGS_RELEASE",
      "occurred_at": "2026-06-24",
      "occurrence_time_precision": "DAY",
      "status": "ACTIVE",
      "canonical_summary": "Micron reported record FY2026 Q3 revenue and issued higher FY2026 Q4 guidance.",
      "known_event_summary": "On Jun. 24, Micron reported FY2026 Q3 revenue of $41.46B; ...",
      "is_important": true,
      "include_in_reference_view": true,
      "related_event_ids": ["E14"],
      "supersedes_event_id": null,
      "derived_from_event_ids": [],
      "facts": [
        {
          "fact_id": "F77",
          "proposition": "Micron reported FY2026 Q3 revenue of $41.46 billion.",
          "assertion_state": "ACTUAL",
          "subject_time": "FY2026-Q3",
          "fact_occurred_at": "2026-06-24",
          "fact_occurrence_time_precision": "DAY"
        }
      ],
      "price_analysis": null
    }
  ]
}
~~~

CanonicalEvent 完整字段：

~~~text
event_id
ticker
title
event_type
occurred_at
occurrence_time_precision
status
canonical_summary
known_event_summary
is_important
include_in_reference_view
related_event_ids
supersedes_event_id
derived_from_event_ids
facts[]
price_analysis
~~~

CanonicalFact 完整字段：

~~~text
fact_id
proposition
assertion_state
subject_time
fact_occurred_at
fact_occurrence_time_precision
~~~

Detail 必须携带该 Event 的完整 active Fact membership，不能只返回摘要或 Top-K Fact。Fact occurrence time 与 subject time 分开：公告当天是 fact_occurred_at，FY2026-Q3 是 subject_time。

当前 EventDetailSnapshot 没有 contract_version、published_at、sha256、requested_event_ids 或 missing_event_ids。provider 对不存在的 Event 直接过滤，可能出现“请求 E13、E99 只返回 E13、没有显式缺失”。新 Response API 建议补齐版本 pin、请求回显和缺失 ID。

### 3.3 O2 Index/Detail 与 Reference View 的区别

O2 的 reference_view() snapshot 字段为 contract_version、ticker、version、published_at、reference_view、sha256。它是给 D2/D3 当前现实理解的文本视图，不替代给 W1 使用的 Known Event Index。W1 Round 1 使用 KnownEventIndexSnapshot，Round 2 使用按 ID 的 EventDetailSnapshot。

## 4. W2 → O2：Delta 合同

### 4.1 当前 O2 正式输入：DeltaBatch / DeltaItem

当前 O2 待整理输入来自：

~~~text
FrozenRuntimeSnapshot → DeltaCompiler.compile() → DeltaBatch → O2 Frozen View/Revision Bundle
~~~

DeltaItem 定义于 contracts.py:712：

~~~json
{
  "delta_id": "D1",
  "runtime_atomic_id": "cdecr-atomic-001",
  "runtime_atomic_version": 1,
  "runtime_signature": "<canonical business payload hash>",
  "proposition": "Micron confirmed repeated commercial shipment to a Tier-1 customer.",
  "time": "2026-08-27",
  "subject_time": null,
  "occurrence_date_candidates": [
    {
      "candidate_date": "2026-08-27",
      "source_kind": "SOURCE_PUBLISHED_AT",
      "source_id": "source-message-001",
      "source_message_id": "source-message-001",
      "runtime_package_id": null,
      "evidence": null
    }
  ],
  "source_message_ids": ["source-message-001"],
  "assertion_state": "ACTUAL",
  "entities": ["Micron", "Tier-1 customer"],
  "runtime_hint_ids": ["R1"],
  "target_suggestion_ids": ["E13"]
}
~~~

字段语义：

- delta_id 当前 validator 只接受 D + 正整数（^D[1-9]\\d*$），是本 batch 内的短 ID；
- runtime_atomic_id、runtime_atomic_version、runtime_signature 是 CDECR Runtime Atomic 的幂等身份字段；
- proposition 是最小、独立可用的事实命题；
- time 是兼容的原始/主题时间字段，不能未经语义判断直接作为 Event occurrence；
- subject_time 保存命题所指期间；
- occurrence_date_candidates[] 提供可审计 occurrence 日期候选；
- source_message_ids 只保存消息引用，不复制完整 Source/Evidence 正文；
- assertion_state 是事实业务状态；
- entities 是 occurrence 候选匹配辅助；
- runtime_hint_ids 和 target_suggestion_ids 只是入口提示，O2 可以拒绝、合并或拆分。

OccurrenceDateCandidate 当前字段：

~~~text
candidate_date
source_kind
source_id
source_message_id?
runtime_package_id?
evidence?
~~~

source_kind 当前枚举：

~~~text
PROPOSITION_EVIDENCE
OFFICIAL_RELEASE_DATE
RUNTIME_CONFIRMED_OCCURRENCE
SOURCE_PUBLISHED_AT
FOCUSED_WEB_SEARCH
~~~

### 4.2 当前 DeltaBatch wire

DeltaBatch 定义于 contracts.py:842：

~~~json
{
  "contract_version": "event-library-maintenance-v3",
  "batch_id": "delta:20260827:MU:001",
  "ticker": "MU",
  "runtime_scope": "cdecr:US:MU",
  "source_snapshot_id": "runtime-snapshot:...",
  "source_epoch_id": "bulk-epoch:...",
  "base_library_version": 42,
  "status": "PENDING",
  "items": [
    {
      "delta_id": "D1",
      "runtime_atomic_id": "cdecr-atomic-001",
      "runtime_atomic_version": 1,
      "runtime_signature": "<hash>",
      "proposition": "Micron confirmed repeated commercial shipment to a Tier-1 customer.",
      "time": "2026-08-27",
      "subject_time": null,
      "occurrence_date_candidates": [],
      "source_message_ids": ["source-message-001"],
      "assertion_state": "ACTUAL",
      "entities": ["Micron", "Tier-1 customer"],
      "runtime_hint_ids": [],
      "target_suggestion_ids": []
    }
  ],
  "runtime_hints": [],
  "runtime_packages": [],
  "created_at": "2026-08-27T23:59:00Z"
}
~~~

DeltaBatch 顶层字段：

~~~text
contract_version
batch_id
ticker
runtime_scope
source_snapshot_id
source_epoch_id
base_library_version
status
items[]
runtime_hints[]
runtime_packages[]
created_at
~~~

Batch validator 还要求：

- batch 内 delta_id、Runtime Hint ID 唯一；
- Package 成员必须指向本 batch 已存在的 Delta；
- Delta↔Package membership 必须双向一致；
- O2 Revision Bundle 通过 delta_batch_ids[] 引用 batch；
- 每个 D# 最终必须被某个 Fact 吸收，或进入 DUPLICATE_FACT、KEEP_PENDING、DROP_INVALID。

### 4.3 为什么 W2 不能原样直接产生当前 DeltaItem

W2 第三轮天然没有：

- CDECR Runtime Atomic 的稳定 identity；
- runtime_atomic_version；
- CDECR 生成的 runtime_signature；
- 日级 source_snapshot_id/source_epoch_id；
- O2 当前 base_library_version；
- Runtime Package membership。

直接让模型填满这些字段会把 source message ID 冒充 CDECR Atomic ID，并让模型决定 batch/base-version/短 ID，破坏 O2 的确定性边界。当前 DeltaItem 的 message-independent identity 语义也会被改变。

因此“W2 输出和 O2 一样的 Delta”应解释为：W2 输出可映射到 O2 Delta 的消息级 candidate；日终系统再生成严格 DeltaBatch。如果业务坚持 W2 必须直接输出当前 DeltaItem，则必须冻结 adapter identity 规则：

- runtime_atomic_id 使用命名空间化、可重放的 message-derived ID；
- runtime_atomic_version 对同一 ID 的内容重写递增；
- runtime_signature 对 proposition/time/subject_time/date candidates/source IDs/assertion state/entities 做 canonical JSON SHA-256；
- delta_id 由 batch aggregator 分配，不由模型分配。

### 4.4 建议新增：RuntimeMessageDeltaCandidate

推荐的 W2 第三轮 response object（不是当前 schema）：

~~~json
{
  "schema_version": "runtime_message_delta_candidate.v1",
  "ticker": "MU",
  "source_message_id": "source-message-001",
  "emit_delta": true,
  "proposition": "Micron confirmed repeated commercial shipment to a Tier-1 customer.",
  "time": "2026-08-27",
  "subject_time": null,
  "occurrence_date_candidates": [
    {
      "candidate_date": "2026-08-27",
      "source_kind": "SOURCE_PUBLISHED_AT",
      "source_id": "source-message-001",
      "source_message_id": "source-message-001",
      "runtime_package_id": null,
      "evidence": null
    }
  ],
  "assertion_state": "ACTUAL",
  "entities": ["Micron", "Tier-1 customer"],
  "runtime_hint_ids": [],
  "target_suggestion_ids": [],
  "reasoning": "同一消息包含可进入 O2 事件库的具体事实。"
}
~~~

建议约束：

- emit_delta=false 时不携带可入库 proposition；
- 一个消息默认产生 0 或 1 个最小 proposition；若允许多个，改为 candidates[]，每个候选单独有时间、状态和来源；
- 不携带稳定 event_id/fact_id；
- target_suggestion_ids 只是建议；
- 不复制长 body、完整 evidence、模型 reasoning 或 Response API metadata；
- occurrence 不确定时保留日期候选，不能用 FY2027/下一季度等 subject period 伪装 occurrence date；
- source 通过 source_message_id 追溯。

### 4.5 日终 deterministic adapter

日终 aggregator 收集一个 ticker/交易日内的 candidates，再生成当前 O2 DeltaBatch：

1. 以 source_message_id + canonical candidate payload 去重，重复重放返回同一结果；
2. 分配本 batch 唯一 delta_id=D1,D2,...；
3. 固定 source_snapshot_id 和 source_epoch_id，代表本批消息快照/运行 epoch；
4. 读取创建时的 O2 Published head，写入 base_library_version；
5. 生成或复用 runtime identity/signature；
6. 映射 candidate → DeltaItem；
7. 只为确有分组语义的成员生成 RuntimeHint/RuntimePackageDelta；
8. 以 status=PENDING 保存不可变 DeltaBatch，再交给 O2 Frozen View/Revision Bundle 流程。

如果不接受 message-derived runtime identity，则不要强行复用当前 DeltaItem；应新增 MessageDeltaItem / O2 Delta v4，明确区分消息 Delta 与 CDECR Runtime Atomic Delta。

## 5. Response API 多轮时序

以下按“暂时只要求同一消息满足全部条件”冻结：一个会话从头到尾只能使用同一个 immutable source-message snapshot；不能用多条消息跨轮拼出一个 Policy condition。

~~~text
Message M
  │
  ├─ W1 Round 1
  │    输入：O2 Known Event Index(version/hash) + M
  │    输出：候选 Event IDs / detail request metadata
  │
  ├─ W1 Round 2（需要时）
  │    输入：同一 O2 version 的 selected Event Detail + M
  │    输出：最终 W1 novelty judgement
  │
  ├─ W2 Round 1
  │    输入：D3 Runtime Policy Projection(version/hash) + M
  │    输出：候选 Policy IDs / detail request metadata
  │
  ├─ W2 Round 2（需要时）
  │    输入：同一 D3 version 的 selected Policy Detail + M
  │    输出：最终 Policy match judgement
  │
  └─ W2 Round 3（满足 Delta gate 时）
       输入：同一 Response conversation 的前两轮状态 + M
       输出：RuntimeMessageDeltaCandidate

日终：
RuntimeMessageDeltaCandidate[]
  └─ deterministic adapter
       └─ DeltaBatch(PENDING)
            └─ O2
~~~

### 5.1 Round 1

- 会话初始化时固定 ticker、source_message_id、index kind、index version、index hash；
- 完整 Index 每个会话只注入一次；后续轮次只引用它；
- W1 Index 必须是完整 active Event Index，不是 Top-K；
- W2 Index 必须是完整 Projection，不是完整 PolicySet；
- message body、published_at、collected_at 等 source fields 在会话内保持 immutable；
- Index 读取失败、版本不存在或 hash 不一致要产生显式 UNAVAILABLE/retry 状态，不能静默当作空索引。

### 5.2 Round 2

- W1 只展开其选中的 Event IDs；
- W2 只展开其选中的 Policy IDs；
- Detail query 必须带 Round 1 version/hash；
- 返回应包含 requested_*_ids 和 missing_*_ids；
- Detail 不重复灌入完整 Index；
- 版本变化时结束当前会话并从新 Index 重新开始，不能把不同版本的 Index/Detail 拼接。

### 5.3 Round 3 的 Delta gate

D3 Policy match 与“事实是否新”是两件事。建议采用：

- W1 拥有 novelty：只有 W1 认为 material_update 或 new_event 且 is_new=true，才允许 W2 进入第三轮；
- W2 负责判断消息是否值得进入某个 Policy/运行路径，以及在第三轮把已确认事实压缩成 candidate；
- 是否所有 W2 类型都允许产生 O2 candidate，需要业务单独冻结；O2 事件库不一定只收 Direct Trade Candidate；
- W2 不重新定义 W1 的 novelty enum。

如果坚持 W2 自己拥有 novelty，则必须新增独立 W2DeltaDecision 或 emit_delta 合同；不能用 W2 type=NULL 推断“新事件”，因为 NULL 只表达“相关但没有 Policy 命中”。

### 5.4 日终与 O2 发布

- base_library_version 在 batch 创建时固定；
- O2 head 发生变化时使用确定性 CAS/重编译规则，不能静默覆盖；
- O2 负责 Event/Fact stable ID、同 occurrence 合并、Fact revision、Event retirement 和 KEEP_PENDING；
- O2 发布新版本后，下一条消息使用新 Index；正在进行的会话继续使用 pinned snapshot；
- response conversation ID、previous response ID、tool call ID、model call 和 reasoning 只保存在 runtime trace/attempt metadata，不进入 O2 Canonical Delta。

## 6. 建议冻结的对象组合

| 层 | 对象 | 当前状态 | 责任 |
| --- | --- | --- | --- |
| W1 Round 1 | KnownEventIndexSnapshot + session envelope | Index 当前存在；envelope 建议新增 | O2 完整已知事件索引 |
| W1 Round 2 | EventDetailSnapshot + CanonicalEvent[] | Detail 当前存在；建议补 hash/requested/missing | 选中事件的完整事实 |
| W2 Round 1 | RuntimePolicyProjection + session envelope | Projection 当前存在；envelope 建议新增 | D3 Policy 条件索引 |
| W2 Round 2 | PolicyDetailSnapshot | 当前没有，建议新增 | 选中 Policy 的 Calibration/source refs |
| W2 Round 3 | RuntimeMessageDeltaCandidate | 当前没有，建议新增 | 单消息事实 candidate |
| 日终 O2 | DeltaBatch/DeltaItem | 当前存在 | aggregator 赋予 batch/D#/runtime identity，O2 整理 |

建议的通用 session envelope：

~~~json
{
  "session_id": "rt-MU-source-message-001",
  "ticker": "MU",
  "source_message_id": "source-message-001",
  "index_kind": "O2_KNOWN_EVENT_INDEX",
  "index_version": 42,
  "index_sha256": "<sha256>",
  "index_loaded_at": "2026-08-27T09:00:00Z",
  "conversation_id": "response_..."
}
~~~

D3 session 将 index_kind 换成 D3_RUNTIME_POLICY_PROJECTION。Transport metadata 与业务对象分离；不要把 Response API conversation ID 写进 Policy、Event、Fact 或 Delta。

## 7. 给网页版 ChatGPT 的待决策问题

1. Novelty owner：W1 作为唯一事实新颖性 gate，还是新增 W2 自有 emit_delta/novelty 合同？
2. Delta 兼容策略：接受 message-derived runtime identity adapter，还是新增明确的 MessageDeltaItem/O2 Delta v4？
3. 一个消息的候选数量：默认 0/1，还是允许 1/N；若允许 N，如何保证每个 proposition 独立有来源和时间？
4. Policy Detail 内容：是否固定包含完整 Calibration 和 source_refs？建议包含，否则第二轮无法核对触发边界。
5. Event Detail 缺失处理：是否新增 missing_event_ids 并让缺失进入不可判定/重试，而不是静默过滤？
6. Version pin：进行中的会话是否继续使用 pinned snapshot？建议是；下一条消息再切换到新 head。
7. 日终 CAS：O2 head 在日内变化时，拆 batch、重编译还是留待下一批？必须有确定性规则。
8. Source evidence：candidate 只带 source_message_id，还是允许短 evidence 摘要？当前 O2 Delta 偏向只保存 ID，避免正文重复和 egress。
9. 缓存：D3 Projection/O2 Index 是否按 ticker/version 做 TTL/LRU；建议每会话读取一次，以 version/hash 失效，而不是每轮重新拉大 payload。
10. 失败降级：Index/Detail/Response API 失败时使用 UNAVAILABLE/PENDING 并可重放，不生成未经证实的 Canonical Delta。

## 8. 参考路径

- D3 Policy/Projection：src/doxagent/workflows/codex_document3/schema.py:96-284
- D3 Projection compiler/cache：src/doxagent/workflows/codex_document3/runtime_projection.py:25-128
- D3 versioned repository：src/doxagent/workflows/codex_document3/repository.py
- O2 Index/Detail provider：src/doxagent/event_library/provider.py:20-103
- O2 Event/Fact/Delta contracts：src/doxagent/event_library/contracts.py:201-356, 712-875
- O2 Known Event Index compiler：src/doxagent/event_library/compiler.py:169-220
- O2 Runtime Snapshot → Delta compiler：src/doxagent/event_library/delta_compiler.py:53-189
- D3 Runtime Projection 规划：dev_plan/workflow_v2/d3_plan.md:1295-1370
- O2 Index/Detail/Delta 规划：dev_plan/CDECR/CDECR_CANONICAL_EVENT_LIBRARY_INCREMENTAL_AGENT_INTEGRATION_PLAN_20260824.md:560-735


