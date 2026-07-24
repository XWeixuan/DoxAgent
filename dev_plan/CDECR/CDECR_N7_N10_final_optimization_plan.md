# CDECR N7–N10 最终优化方案

## 一、目标与边界

本轮修改集中在以下方面：

1. 暂时冻结 Hard Cannot-Link，使正常 workflow 不再受 N8 拦截；
2. 全局删除 HOLD 及相关持久化结构，所有合法对象必须得到明确归属；
3. 将 N9 从独立 Pair 分类器重构为“单 Mention、多候选联合裁决器”；
4. 修复 Atomic Embedding 生命周期错误；
5. 将 Canonical Field Identity 以统一来源等级真正贯通 N6、N7、N8 和 N9；
6. 修复 Field Link 或 N6 Identity 更新后错误复用旧 Atomic Assignment 的增量重处理问题；
7. 当前不建设完整的在线 N10 校正系统，保留正常 Mention-to-Atomic 更新方式，仅将独立的 `_correct_atomic()` 自动 Atomic-to-Atomic 校正冻结为审计模式，并以可重建派生状态作为测试阶段的低成本替代。

本轮对 N10 的统一口径是：

- 保留当前正常 Mention-to-Atomic 的 Atomic Profile 更新方式；
- 不增加保守合并或成员驱动的共识 Profile 重算；
- 不调整当前正常 MERGE 对成员、时间和 OPEN Identity 的更新逻辑；
- 不实现成员移除、在线拆簇、冻结、redirect 回滚或自动语义级 Atomic-to-Atomic 合并；
- 仅停止当前 `_correct_atomic()` 的自动修改和 redirect 行为，改为只检测、只审计；
- 使用独立可重置 SQLite Registry 和 Derived State Rebuild 处理测试阶段的错误聚类。

需要保留一项风险说明：在 N8 不拦截、N10 正常 MERGE 更新方式不调整的测试阶段，错误 MERGE 可能污染 Atomic Profile。因此真实测试应使用可重置的独立 SQLite Registry，避免实验结果污染后续基线。待 N8 重新设计或恢复后，再评估 N10 是否需要建设完整的成员共识、移除和拆簇能力。

优化后的路径为：

```text
N5.5 Canonical Field Resolution
→ N6 Identity Compiler
→ N7 候选召回
→ N8 Shadow 冲突观测，不排序、不拦截
→ N9 多候选联合裁决
→ MERGE / CREATE_NEW
→ N10 按当前正常逻辑更新 Mention-to-Atomic
→ Atomic 重复候选只审计，不自动 redirect
```

除本方案明确列出的修复外，原有候选预算、完整独立 Pair Decision 表、全局图聚类等问题仍属于 P1，可以后置。

## 二、冻结 Hard Cannot-Link

### 2.1 运行模式

引入配置：

```text
CDECR_ATOMIC_HARD_CANNOT_LINK_MODE=enforce|shadow|off
```

当前测试及正常 workflow 使用：

```text
shadow
```

| 模式 | 计算冲突 | 写审计 | 影响候选排序 | 阻止进入 N9 |
|---|---:|---:|---:|---:|
| `enforce` | 是 | 是 | 是 | 是 |
| `shadow` | 是 | 是 | 否 | 否 |
| `off` | 否 | 否 | 否 | 否 |

保留 `enforce` 是为了后续 N8 修订完成后可以重新启用，不需要再次改动 workflow 结构。

### 2.2 候选构建

在 `_atomic_candidates()` 中继续调用 `hard_cannot_link()`，但在 `shadow` 模式下：

- 计算结果仅保存到局部 `observed_conflicts`；
- 写入 `DecisionAuditRecord`；
- `AtomicCandidate.hard_conflicts` 固定为空；
- 不把观测到的冲突发送给 N9。

建议审计载荷：

```json
{
  "candidate_event_id": "atomic:...",
  "observed_conflicts": [
    "ASSERTION_STATE",
    "EVENT_TIME"
  ],
  "mode": "shadow",
  "enforced": false
}
```

### 2.3 取消“无冲突候选优先”

当前排序：

```python
(
    bool(item.hard_conflicts),
    -item.recall_score,
    item.event.event_id,
)
```

`shadow` 模式调整为：

```python
(
    -item.recall_score,
    item.event.event_id,
)
```

否则 N8 即使不直接拦截，仍可能通过 Top K 排序间接排除真正候选。

### 2.4 取消两处候选过滤

以下两个阶段都必须使用完整候选集：

- `_atomic_decisions()` 生成 N9 输入时；
- `_apply_atomic()` 执行最终分配时。

删除等价逻辑：

```python
if not candidate.hard_conflicts
```

在 `shadow` 模式下，不再存在“全部候选被 Hard Cannot-Link 阻断”的业务分支。

### 2.5 指标调整

原有：

```text
atomic_hard_blocked
```

改为：

```text
atomic_hard_conflict_observed
```

该指标只表示“旧规则原本可能阻断多少候选”，不能再解释为实际拦截数量。

### 2.6 幂等与版本

processing key 增加：

```json
{
  "hard_cannot_link_mode": "shadow",
  "atomic_assignment_policy_version": "v2",
  "hold_policy": "removed"
}
```

并同步递增：

```text
ENGINE_VERSION
PROMPT_VERSION
```

防止复用旧 N8/N9 逻辑产生的已完成结果。

### 2.7 暂缓重构 Hard/Soft/Claim 三层规则

当前暂不重构 Hard/Soft/Claim 三层规则。

原先确认的风险仍然成立：OPEN Profile 中以下差异目前都可能被旧规则当成 Hard Conflict：

- `normalized_predicate`；
- Assertion State；
- 时间不重叠；
- Lifecycle Stage；
- 原始参与者或地点集合。

这些字段会受到措辞、时间解析和报道状态演化影响，不能证明旧规则正确。已有“60,794 个 Hard Cannot-Link、0 violation”的记录只能证明没有违反当前代码规则，不能证明规则本身正确。

由于 N8 已进入 `shadow`：

- 继续计算；
- 继续审计；
- 不参与排序；
- 不过滤候选；
- 不影响 MERGE。

因此这些过严规则当前不会继续制造实际漏合并。后续重新启用 `enforce` 前，再将其拆成真正 Hard、Soft Conflict 和 Claim Conflict。

## 三、调整 identity_conflicts

### 3.1 取消自动否决

删除当前选择逻辑中的：

```python
and not item.identity_conflicts
```

即：

```text
SAME_EVENT + identity differences
```

仍然可以执行：

```text
MERGE
```

### 3.2 调整字段语义

将模型输出中的：

```text
identity_conflicts
```

重命名为：

```text
identity_differences
```

新语义是：

> 模型识别出的身份字段差异，用于解释和审计，但不自动决定最终动作。

`claim_conflict` 同样只作为审计信息，不阻止 MERGE。

最终动作由 N9 对全部候选联合比较后的结果决定。

N10 暂时仍可把这些差异写入现有 conflict flags，但它们不再参与本轮分配决策。

## 四、全局删除 HOLD

由于当前仍处于测试阶段，旧结果没有兼容价值，本轮彻底删除 HOLD，而不是仅停止产生新的 Atomic HOLD。

### 4.1 删除枚举

从 `AtomicAction` 删除：

```text
HOLD
```

从 `PackageAction` 删除：

```text
HOLD
```

删除：

```text
HoldKind
HoldStatus
```

### 4.2 删除领域契约

删除：

```text
HoldRecord
```

从 `CrossDocumentResult` 删除：

```text
hold_ids
```

简化 `AtomicAssignmentRecord` 校验为：

```text
所有 Atomic Assignment 都必须具有 resulting_event_id
```

简化 `PackageAssignmentRecord` 校验为：

```text
所有 Package Assignment 都必须具有 resulting_package_id
```

### 4.3 删除持久化结构

删除：

- `hold_queue` 表；
- `save_hold()`；
- `list_open_holds()`；
- Registry Port 中对应方法；
- Result Export 中的 Open Hold 输出；
- CLI 中的 `hold_ids`；
- 所有 Hold Registry 测试。

SQLite Schema 升级时直接删除 `hold_queue`。不迁移旧 Hold，因为旧测试结果明确不再保留。

### 4.4 Atomic Assignment 新语义

| 场景 | 最终处理 |
|---|---|
| 没有候选 | `CREATE_NEW` |
| 全部 `UNRELATED` | `CREATE_NEW` |
| 唯一 `SAME_EVENT` | `MERGE` |
| 多个 `SAME_EVENT` | 选择一个目标 `MERGE`，其余记录为疑似重复 Atomic |
| 一个或多个 `RELATED_NOT_SAME` | `CREATE_NEW`，候选仅留痕 |
| 存在 `UNCERTAIN` | 升级 M3；仍不确定则 `CREATE_NEW` |
| N6 Identity 无法编译 | Run 失败，不进入 N9 |
| provisional 目标不存在 | Run 失败，视为编排不变量错误 |
| 模型调用或 Schema 修复持续失败 | Run 失败并允许重试 |

原则是：

```text
语义不确定 → CREATE_NEW + 审计
技术失败 → FAILED + 重试
```

技术失败不能伪装成 `CREATE_NEW`，否则供应商故障可能批量制造单例 Atomic。

### 4.5 多个 SAME_EVENT

当多个候选均被判断为 SAME_EVENT：

1. 按目标选择规则确定一个 `merge_target_event_id`；
2. Mention 合并到该目标；
3. 其他 SAME 候选记录到：

```json
{
  "possible_duplicate_atomic_ids": [
    "atomic:b",
    "atomic:c"
  ]
}
```

本轮不立即合并这些 Atomic，留给后续 Atomic-to-Atomic 校正。

### 4.6 Related 事件

只要没有 SAME_EVENT，即使存在一个或多个 RELATED_NOT_SAME，Mention 都创建新 Atomic。

相关候选写入：

```json
{
  "related_candidate_event_ids": [
    "atomic:a",
    "atomic:b"
  ]
}
```

N9 不再创建正式 `RELATED_TO`，正式关系由后续 Relation Gate 决定。

### 4.7 Package 相关 HOLD 的替换

全局删除 HOLD 后，Package 分支同步调整：

- Package Assignment 不确定：`CREATE_NEW_PACKAGE`；
- Package Merge 不确定：保持两个 Package 分离，仅写 Decision Audit；
- Package 过度扩张：写异常审计，不创建 Hold；
- Package 技术失败：Run 失败。

## 五、重构 N9 输入输出

### 5.1 节点职责

N9 从：

```text
逐 Pair 独立分类
→ 编排层根据分类结果推导动作
```

改成：

```text
单 Mention + 全部候选联合比较
→ 模型直接输出 MERGE / CREATE_NEW
```

每个 Mention 独立裁决，但模型必须同时看到该 Mention 的全部候选，避免多个 SAME、多个 Related 被割裂处理。

### 5.2 输入内容

N9 必须显式接收 N6 编译结果，而不是只接收原始 EventMention：

```json
{
  "tasks": [
    {
      "incoming": {
        "mention_id": "mention:...",
        "canonical_proposition": "...",
        "event_family": "...",
        "assertion_state": "...",
        "identity_profile": {},
        "time": {},
        "claim_values": [],
        "source_claim": "...",
        "evidence_excerpts": ["..."],
        "canonical_participants_by_role": {
          "SUBJECT": ["COMPANY:MU"],
          "COUNTERPARTY": ["COMPANY:..."]
        },
        "canonical_locations": [],
        "canonical_named_objects": []
      },
      "candidates": [
        {
          "event_id": "atomic:...",
          "canonical_proposition": "...",
          "event_family": "...",
          "identity_profile": {},
          "time": {},
          "representative_source_claims": ["..."],
          "canonical_participants_by_role": {
            "SUBJECT": ["COMPANY:MU"],
            "COUNTERPARTY": ["COMPANY:..."]
          },
          "canonical_locations": [],
          "canonical_named_objects": [],
          "recall_routes": ["..."],
          "recall_score": 0.91,
          "is_provisional": false
        }
      ]
    }
  ]
}
```

不注入 N8 shadow conflicts。

角色化 Canonical Identity 视图从 EventMention、当前 CanonicalFieldLinks 和 Representative Mentions 动态构造，不要求本轮扩展 AtomicEvent 持久化 Schema。

### 5.3 输出契约

```json
{
  "decisions": [
    {
      "mention_id": "mention:...",
      "action": "MERGE",
      "merge_target_event_id": "atomic:...",
      "candidate_assessments": [
        {
          "candidate_event_id": "atomic:...",
          "relation": "SAME_EVENT",
          "claim_conflict": false,
          "identity_differences": []
        }
      ],
      "related_candidate_event_ids": [],
      "possible_duplicate_atomic_ids": []
    }
  ]
}
```

强制校验：

- 每个 Mention 恰好一个最终 Decision；
- 每个候选恰好一个 Assessment；
- `action` 只允许 `MERGE | CREATE_NEW`；
- MERGE 目标必须来自输入候选；
- MERGE 目标必须被评估为 SAME_EVENT；
- CREATE_NEW 时目标必须为 `null`；
- UNCERTAIN 不能成为 MERGE 目标；
- 多个 SAME 时必须选一个目标，其余进入 `possible_duplicate_atomic_ids`；
- Related 候选进入 `related_candidate_event_ids`。

### 5.4 N9 决策审计

本轮可以不增加独立 `atomic_pair_decisions` 表。

但新版 N9 的完整以下内容必须整体写入现有 `DecisionAuditRecord`，不能只保存最终 Assignment：

```text
candidate_assessments
related_candidate_event_ids
possible_duplicate_atomic_ids
identity_differences
claim_conflict
```

独立追加式 `atomic_pair_decisions` 表后续再做。

## 六、最终英文 Prompt

```text
You are the **CDECR N9 Atomic Event Assignment Adjudicator**.

## Business Context

CDECR extracts **Event Mentions** from individual news documents and clusters mentions that refer to the same real-world atomic event.

An **Event Mention** is an evidence-supported description or claim about an event within a single document.

An **Atomic Event** is a cross-document cluster representing one specific real-world event occurrence. Event Mentions from multiple sources may belong to the same Atomic Event even when they use different wording, use aliases, or contain conflicting claim values.

## Your Task

For each incoming Event Mention, jointly compare it with all provided candidate Atomic Events. You must choose exactly one of the following business actions:

1. **MERGE**

   The Event Mention refers to the same real-world atomic event as one of the candidate events.

2. **CREATE_NEW**

   None of the candidate events is supported by sufficient evidence to be identified as the same event, or significant semantic uncertainty remains after all candidates have been compared.

## Core Relations

For every candidate event, you must assign exactly one of the following relations:

- **SAME_EVENT**

  The Event Mention and the candidate describe the same specific real-world event occurrence.

- **RELATED_NOT_SAME**

  They are related causally, temporally, organizationally, or narratively, but represent different event occurrences or different milestones in the lifecycle of the same broader event.

- **UNRELATED**

  They have no material relationship at the event level.

- **UNCERTAIN**

  Select this only when the provided evidence is entirely insufficient to make a determination. **UNCERTAIN must never be used as the basis for MERGE under any circumstances.**

## Distinguishing Event Identity from Claims

**Canonical identity fields** describe which specific event occurrence is being referenced.

**Claim fields** describe what a source states about that event.

Different claim values do not automatically constitute different events. When such differences occur, set:

`claim_conflict=true`

However, if the underlying event identity is the same, you must still select:

`SAME_EVENT`

Identity differences are diagnostic observations, not automatic rejection conditions. You must use the provided context to determine whether each difference is sufficient to establish that the two items represent different events.

You must not identify candidates as SAME_EVENT solely because they:

- are semantically similar;
- involve the same company;
- belong to the same event type or event family;
- have a high retrieval score.

## Identity Assessment Guidelines

### OPEN Events

For **OPEN** events, compare:

- the normalized predicate;
- the normalized core participants;
- counterparties;
- explicitly named objects or assets;
- the event time or period;
- the event occurrence actually described by the evidence.

You must determine whether the sources are merely describing the occurrence of the same scheduled event under different reporting states. Unless the evidence shows that the two descriptions actually refer to the same milestone, they must be classified as:

`RELATED_NOT_SAME`

## Target Selection When Multiple Candidates Are the Same Event

Choose the merge target according to the following priority order:

1. the candidate with the most complete and best-matching canonical identity;
2. a persisted, non-provisional Atomic Event;
3. the candidate supported by the strongest trusted identity-resolution evidence;
4. the candidate with stronger retrieval evidence.

## Output Rules

- When selecting MERGE, `merge_target_event_id` must reference a candidate assessed as SAME_EVENT.
- When selecting CREATE_NEW, `merge_target_event_id` must be `null`.
- A candidate assessed as UNCERTAIN cannot be selected as the merge target.
- `identity_differences` and `claim_conflict` are audit information and do not automatically override or change the final action.
- The returned content must strictly match the provided JSON Schema.
```

## 七、模型路由

建议保留 M2 为默认模型，但修改升级条件：

以下任一条件满足时升级 M3：

- M2 输出一个或多个 `UNCERTAIN`；
- M2 判断多个候选为 SAME_EVENT；
- 最终动作与 Candidate Assessments 不一致；
- MERGE 目标没有明显优于其他 SAME 候选；
- 第一次输出未通过业务 Schema 校验。

不再仅因为“候选数达到 4”就自动升级 M3。候选数量本身不代表判断困难程度。

处理链：

```text
M2 联合裁决
→ 业务校验
→ 存在语义歧义时 M3 重裁
→ 仍不确定则 CREATE_NEW
```

结构化输出或供应商调用持续失败不适用 CREATE_NEW，直接令当前 Run 失败。

## 八、P0：修复 Atomic Embedding 生命周期

### 8.1 当前实现缺陷

当前 Mention Embedding 使用 Mention 的 `canonical_proposition` 计算向量和哈希，该部分逻辑正确。

错误发生在 Atomic 更新阶段：

- 代码取得新加入 Mention 的向量；
- 保存为 `owner_kind="atomic_event"`；
- `owner_id` 使用 Atomic ID；
- `input_hash` 却由 Atomic 的 `canonical_proposition` 计算。

Registry 对相同以下逻辑身份保留第一次向量：

```text
owner_kind + owner_id + model + input_hash
```

因此会产生以下错误：

1. 向量和 `input_hash` 描述的不是同一段输入；
2. Atomic Proposition 永远是首个 Mention 的措辞；
3. 后续 Mention 合并通常无法更新 Atomic 向量；
4. Atomic Embedding 长期锁定为首个 Mention 的向量；
5. `_correct_atomic()` 合并两个 Atomic 后没有重建 Embedding；
6. existing-assignment 恢复分支在写 Embedding 前就结束，可能永久缺失 Atomic Embedding；
7. N7 对同一事件不同措辞的 Embedding 召回不稳定。

### 8.2 独立 Atomic Identity Text

新增唯一的 Atomic Embedding 输入构造函数：

```python
atomic_identity_text(event, representative_mentions)
```

输入至少包含：

```json
{
  "event_version": 3,
  "event_family": "TRANSACTION_CAPITAL",
  "identity_profile": {},
  "time": {},
  "assertion_state": "ACTUAL",
  "representative_propositions": [
    "...",
    "..."
  ]
}
```

要求：

- 使用稳定字段顺序和确定性序列化；
- Representative Mention 最多保留现有的三条；
- Representative Proposition 使用确定性顺序；
- `input_hash` 必须是该文本精确 UTF-8 内容的 SHA-256；
- M1 必须对同一文本生成向量；
- 向量、`input_hash`、Atomic Profile 和 Atomic Version 必须来自同一个输入；
- 禁止再把 Mention Vector 直接保存为 Atomic Vector；
- Atomic 的 `canonical_proposition` 可以继续保留现有语义，但不再作为 Atomic Embedding 的唯一输入。

### 8.3 集中同步 Atomic Embedding

增加统一同步入口：

```python
_sync_atomic_embeddings(events, models)
```

调用位置：

1. N7 前：检查活跃 Atomic 的最新 Embedding 是否存在、是否匹配预期 Identity Hash；
2. `_apply_atomic()` 完成后：同步所有新建或更新的 Atomic；
3. existing-assignment 恢复时：如果 Embedding 缺失或哈希不一致，必须补建；
4. Atomic Profile 或 Representative Mentions 更新后：确定性重建；
5. 未来重新启用 redirect 时：只重建最终 Target，Redirect Source 不再参与召回。

当前测试规模较小，可以接受 Atomic 每次版本更新后重新调用一次 M1。后续如果需要降低成本，再把 `event_version` 从语义文本中移到 Embedding 元数据。

### 8.4 读取与幂等

N7 读取 Atomic Embedding 时必须：

- 根据当前 Atomic 重新计算预期 Identity Hash；
- 只接受 `input_hash` 与预期值一致的最新 Embedding；
- 忽略缺失、过期或属于 Redirect Source 的 Embedding；
- 对缺失或过期对象批量调用 M1；
- 使用每批最多 10 条的现有 M1 限制；
- 相同 Identity Text 重跑时不增加模型调用和 Embedding 记录。

### 8.5 测试要求

必须覆盖：

- 新建 Atomic 的向量与 Identity Text 一致；
- 合并不同措辞 Mention 后向量更新；
- 同一 Identity Text 幂等，不新增调用；
- existing-assignment 恢复时补齐缺失向量；
- Atomic Profile 更新后旧向量不再被读取；
- Representative Mentions 变化后向量更新；
- Redirect Source Embedding 不参与召回；
- `_correct_atomic()` 审计模式不产生错误 Embedding；
- 不再存在“Atomic Hash 对应 A 文本、向量来自 B 文本”的情况。

## 九、P0：将 Field Coreference 真正贯通 Atomic Identity

### 9.1 对旧结论的修正

“Participant 完全没有进入 N7”已经不完全准确。

当前 N6 会读取 Canonical Field Links，并把 Participant 编译到：

```text
principal_participant_ids
```

N7 再通过 `core_entity_ids_from_profile()` 将这些 ID 用于 `CORE_ENTITY` 召回。

因此 Participant Canonical ID 已经部分进入 N7，但身份来源等级、角色信息、Place 和部分 Object namespace 仍未完整贯通。

### 9.2 Canonical ID 丢失身份来源等级

Identity Compiler 当前返回：

```python
entry.external_id or entry.id
```

进入 `IdentityProfile` 后，只剩下一个字符串，无法再判断它属于：

- 可信 KB External ID；
- 内部 Canonical Registry ID；
- provisional Field Coreference ID；
- N5 legacy entity ID；
- 原始文本。

后续 N8 会把不同 provisional ID 当成真实身份冲突，因为它只能看到两个不相同的字符串。

### 9.3 Participant 与 Location 判定标准不一致

Participant 当前存在以下问题：

- 不同 provisional IDs 可能直接产生 `CORE_SUBJECT`；
- 即使 Field Coreference 已证明两者指向相同可信 External ID，当前补丁也不会统一解除已有冲突；
- Subject 和 Counterparty 只有双侧可信 External ID 不同时才会追加冲突，但原有 Profile 冲突仍可能保留。

Location 当前存在相反问题：

- 只要任意一侧存在 Field Entry；
- 并且不存在“双侧 External ID 明确冲突”；
- 就会删除旧 `LOCATION_ASSET` 冲突；
- 单侧已知、另一侧未知也可能被过早当作不冲突。

因此现在同时存在：

```text
Participant：未知可能被错误判为不同
Location：单侧已知可能被错误判为相同
```

### 9.4 Field Recall Namespace 不一致

跨文档层允许以下六类 Object Namespace：

- Facility；
- Project；
- Product；
- Asset；
- Technology；
- Program。

但 Registry 的 Atomic Recall Index 当前只索引前四类。

因此：

- Technology 和 Program 可以被 N7 请求；
- 但 Registry 没有把它们写入 Atomic Field Recall Index；
- 对这两类对象的 `FIELD_ID` 召回不能正常命中。

Place 同样没有进入 `FIELD_ID` 召回。

### 9.5 OPEN Identity 丢失参与者角色

N6 当前会把：

- ACTOR；
- SUBJECT；
- TARGET；
- COUNTERPARTY；
- AUTHORITY；

全部放入同一个：

```text
principal_participant_ids
```

这会丢失角色信息。

已经确认的 N9 Prompt 明确要求比较：

- 核心参与者；
- 交易对手。

如果只把现有 Atomic Identity Profile 发送给 N9，模型无法可靠区分：

```text
A 收购 B
B 收购 A
```

因为两者可能都被压平成：

```text
{A, B}
```

### 9.6 运行时分级身份视图

本轮不需要修改不可变 EventMention，也不需要新增持久化领域表。增加运行时对象：

```python
ResolvedIdentityEvidence
```

至少包含：

```text
field_path
role
namespace
external_id
canonical_registry_id
legacy_entity_id
normalized_surface
trust_level
```

身份优先级固定为：

```text
可信 External ID
> 相同 Canonical Registry Root
> N5 Entity ID
> 规范化原始文本
```

比较结果必须为三值：

```text
SAME
DIFFERENT
UNKNOWN
```

判定规则：

| 左侧 | 右侧 | 结果 |
|---|---|---|
| 相同 External ID | 相同 External ID | `SAME` |
| 不同可信 External ID | 不同可信 External ID | `DIFFERENT` |
| 相同 Canonical Root | 相同 Canonical Root | `SAME` |
| 不同 provisional Root | 不同 provisional Root | `UNKNOWN` |
| External ID | provisional ID | `UNKNOWN` |
| 相同 N5 Entity ID | 相同 N5 Entity ID | 弱 `SAME` |
| 不同 N5 Entity ID | 不同 N5 Entity ID | `UNKNOWN` |
| 相同规范化原始文本 | 相同规范化原始文本 | 弱 `SAME` |
| 不同规范化原始文本 | 不同规范化原始文本 | `UNKNOWN` |
| 单侧未解析 | 任意 | `UNKNOWN` |

只有以下结果在未来恢复 N8 `enforce` 后才可以成为 Hard Cannot-Link：

```text
DIFFERENT + 双侧可信身份
```

不同 provisional IDs、单侧未知、不同 legacy ID 或不同原始文本不能直接证明不同。

### 9.7 N7 召回调整

- Participant Company/Institution/Person/Authority/Instrument 继续通过 Canonical Profile 进入 `CORE_ENTITY`；
- Place 与全部 Object Namespace 进入 `FIELD_ID`；
- Registry 和 CrossDocument 使用同一份 Atomic Field Namespace 常量；
- Atomic Recall Index 优先写入 Canonical IDs；
- N5 legacy IDs 不与 Canonical IDs 混在同一可信召回通道中，可保留为低权重 legacy route；
- Field Redirect 后必须回填受影响 Atomic Recall Index；
- Field Link 更新后必须刷新相关 Atomic Recall Index；
- Place、Technology 和 Program 必须有真实召回测试。

### 9.8 N8 身份观测调整

即使当前 N8 使用 `shadow`，其观测逻辑也应改用统一的三值身份判定：

- `SAME`：可以记录正向身份证据；
- `DIFFERENT`：记录可信冲突；
- `UNKNOWN`：保持中性，不记录为相同或不同。

这些结果当前只写审计，不影响排序、N9 输入或最终动作。

### 9.9 N9 角色化身份视图

为每个 Mention 和 Candidate Atomic 动态构造：

```json
{
  "canonical_participants_by_role": {
    "SUBJECT": ["COMPANY:MU"],
    "COUNTERPARTY": ["COMPANY:..."]
  },
  "canonical_locations": [],
  "canonical_named_objects": []
}
```

该视图从以下内容构造：

- 不可变 EventMention；
- 当前 CanonicalFieldLinks；
- Registry Redirect 后的 Root Entry；
- Atomic Representative Mentions；
- N6 Compiled Identity。

不要求本轮扩展 AtomicEvent 持久化 Schema。

### 9.10 测试要求

必须覆盖：

- provisional ID 差异返回 `UNKNOWN`；
- 不同可信 External ID 返回 `DIFFERENT`；
- 相同 External ID 返回 `SAME`；
- 单侧 Location Link 不证明 `SAME`；
- 不同 provisional Location 不产生可信冲突；
- 相同 Participant External ID 不产生冲突；
- Place、Technology、Program 能通过 `FIELD_ID` 召回；
- Participant Company/Institution/Person 能通过 `CORE_ENTITY` 召回；
- Field Redirect 后 Recall Index 更新；
- Subject 与 Counterparty 不被压平成不可区分集合；
- `A 收购 B` 与 `B 收购 A` 的角色化输入不同；
- N8 shadow 身份观测不影响 N9。

## 十、P0：修复 Identity 更新后错误复用旧 Atomic Assignment

### 10.1 当前错误行为

如果某个 Mention 已经属于一个 Atomic，N7 当前会直接跳过候选召回。

随后 `_apply_atomic()` 进入 existing-assignment 恢复分支。

如果当前 N6 Identity 与旧 Atomic Identity 不同，现有逻辑会执行等价行为：

```python
recovered.identity_profile = current_mention_profile
```

然后：

- 不执行 N9；
- 不重新判断该 Mention 是否还属于这个 Atomic；
- 强制保留旧成员关系；
- 用当前单条 Mention 的新 Profile 覆盖整个 Atomic 的 Profile；
- 可能把新 Field Resolution 结果错误地扩散到整个 Atomic；
- existing-assignment 恢复路径还可能永久缺少 Atomic Embedding。

### 10.2 为什么必须当前修复

Field Coreference 本身是增量更新的：

- provisional Entry 可升级为 External ID；
- Registry Entry 可以 redirect；
- Field Link 可以更新；
- Catalog、Prompt 或 Resolver 版本可以变化；
- 新的 KB 对象或别名可能让之前 unresolved 的字段得到解析。

这些变化理应触发：

```text
N6 重新编译
→ N7 重新召回
→ N9 重新分配
```

但当前实际行为是：

```text
N6 重新编译
→ 发现旧 Assignment
→ 强制沿用旧 Atomic
→ 覆盖 Atomic Profile
```

因此即使 Field Coreference 修正了身份，Atomic Membership 仍无法随之修正。

### 10.3 Field Resolution 前的错误幂等短路

`process()` 当前在执行 Field Resolution 之前，会根据已有 Field Links 计算 processing key，并尝试直接返回旧结果。

如果某字段上次 unresolved，后来 Registry 中出现新的可用对象，因为该 Mention 仍然没有 Link，当前 processing key 可能保持不变，于是 Field Resolution 不会重新运行。

因此必须删除 Field Resolution 之前的 completed-result 快捷返回。

新的顺序固定为：

```text
执行 N5.5 Field Resolution
→ 读取最新 Links 和 Redirect Roots
→ 执行 N6 Identity Compiler
→ 计算最终 processing key
→ 检查 completed result
→ 进入 N7
```

### 10.4 Assignment 绑定版本

`AtomicAssignmentRecord` 增加：

```text
identity_processing_key
assignment_policy_version
```

existing-assignment 只在以下条件全部满足时允许恢复：

```text
相同 Mention
+ 相同 identity_processing_key
+ 相同 assignment_policy_version
+ 属于同一次失败重试或幂等恢复
```

恢复时仍必须检查和补建 Atomic Embedding。

### 10.5 Identity 变化时的当前处理

Identity Processing Key 发生变化时：

- 禁止覆盖现有 Atomic Profile；
- 禁止静默沿用旧成员关系；
- 禁止直接把 Mention 添加到第二个活跃 Atomic；
- 禁止在没有 Detach 能力时原地迁移；
- 当前阶段返回明确错误：

```text
DERIVED_STATE_REBUILD_REQUIRED
```

随后使用 Derived State Rebuild 从不可变 Mentions 和当前 Field Links 重新运行。

在当前测试阶段，这比立即实现在线 Mention Detach、Cluster Split、Membership 迁移和回滚更安全，也更便宜。

### 10.6 测试要求

必须覆盖：

- 相同 Identity Processing Key 的失败重试可以恢复；
- Assignment Policy Version 相同才允许恢复；
- Field Link Root 变化后不恢复旧 Assignment；
- provisional Entry 升级为 External ID 后触发重处理；
- Mention Identity 变化不会覆盖整个 Atomic Profile；
- Identity 变化时返回 `DERIVED_STATE_REBUILD_REQUIRED`；
- Mention 不会同时出现在两个活跃 Atomic；
- Field Resolution 必须在最终 processing key 检查前运行；
- existing-assignment 恢复时补齐缺失 Atomic Embedding；
- Derived State Rebuild 后可以成功重新分配。

## 十一、P0：N10 采用简化替代方案

### 11.1 当前校正能力

当前 `_correct_atomic()` 只在以下两项完全一致时合并 Atomic：

```text
Identity Profile JSON 完全一致
Canonical Proposition 忽略大小写后完全一致
```

因此它无法处理：

- 同一事件的不同措辞；
- 单例碎片合并；
- Profile 被错误成员污染；
- 一个 Atomic 中混入两个事件；
- Assertion、时间或参与者被首个 Mention 锁定；
- 需要移除成员或拆簇的情况。

OPEN Profile 继续采用当前集合并集，仍存在链式扩张风险：

```text
A/B 共享一个主体后合并
→ B/C 再共享一个主体
→ Profile 变成 A+B+C
→ 后续更容易吸入相关但不同的事件
```

本轮不大幅修改正常 Mention-to-Atomic 更新方式，测试报告必须明确保留该风险。

### 11.2 当前 Atomic Redirect 的额外风险

当前 Atomic redirect 只执行：

1. 将 Source Mention 复制到 Target；
2. 保存 `source_event_id → target_event_id`；
3. 从活跃列表中移除 Source。

但没有同步迁移：

- Source 的 Package Membership；
- Source 的 Atomic External Relations；
- Source 的 Embedding；
- 其他对 Source Event ID 的引用。

因此可能出现：

```text
Atomic 已 redirect
但 Package Membership 和 Relation 仍指向旧 Atomic ID
```

此外，`_correct_atomic()` 使用静态 `all_current` 和外层快照循环。三个以上完全相同 Atomic 同时出现时，同一个 Source 可能在一轮处理中被尝试 redirect 到不同 Target：

- 某个 Target Version 可能已经写入；
- 随后的 Immutable Redirect 冲突导致 Run 失败；
- 中间写入无法整体回滚；
- 派生图可能进入不一致状态。

### 11.3 当前阶段不建设完整在线校正

本轮不实现：

- 成员移除；
- 在线拆簇；
- 成员驱动的共识 Profile 重算；
- Atomic 冻结状态；
- redirect 回滚；
- 自动 Atomic-to-Atomic 语义合并；
- Source/Target 全图引用迁移；
- 错误 MERGE 的在线撤销。

正常 Mention-to-Atomic 的 N10 更新逻辑保持不变：

- 继续追加 Mention ID 和 Source Claim；
- 继续使用现有 Representative Mention 逻辑；
- 继续使用现有时间合并逻辑；
- 继续使用现有 OPEN Profile 更新逻辑；
- 继续递增 Atomic Version。

### 11.4 `_correct_atomic()` 改为 Audit-Only

将 `_correct_atomic()` 改成：

```text
只检测
→ 只写审计
→ 不修改 Atomic
→ 不创建 Redirect
```

审计载荷：

```json
{
  "decision_type": "ATOMIC_DUPLICATE_CANDIDATE",
  "source_event_id": "atomic:a",
  "target_event_id": "atomic:b",
  "reason": "EXACT_PROFILE_AND_PROPOSITION_MATCH"
}
```

N9 发现多个 SAME_EVENT 时同样：

- Mention 合并到一个目标；
- 其他候选写入 `possible_duplicate_atomic_ids`；
- 本轮不自动合并这些 Atomic。

### 11.5 Derived State Rebuild

当前阶段使用派生状态重建替代在线回滚：

```text
保留不可变 SourceMessage
保留不可变 EventMention
保留 Canonical Field Registry/Links
清空 Atomic/Package 派生状态
按新版本重新运行 N6–N10
```

提供独立测试工具：

```text
python -m cdecr registry rebuild-derived
```

只重建：

- Atomic Events；
- Atomic Assignments；
- Atomic Embeddings；
- Packages；
- Memberships；
- Cross-document Relations；
- Cross-document Run 派生结果。

需要删除或重建的派生索引包括：

- Atomic Recall Index；
- Atomic Recall Entity Index；
- Atomic Recall Field Index；
- Package Recall Index；
- 当前 Cross-document Result 快照。

不得删除：

- SourceMessage；
- EventMention；
- Evidence；
- Canonical Field Registry Entry；
- Canonical Field Link；
- Field Redirect；
- Field Resolution Decision Audit；
- 单文档处理结果。

重建必须：

- 使用当前 Catalog、Field Links、Resolver Version、Identity Compiler Version、Engine Version 和 Prompt Version；
- 使用新的 Atomic Identity Text 生成 Embedding；
- 保持输入不变时结果幂等；
- 在独立可重置 Registry 上执行真实测试；
- 不把旧 HOLD 或旧跨文档 Assignment 迁移到新派生状态。

### 11.6 测试要求

必须覆盖：

- `_correct_atomic()` 不修改 Atomic；
- `_correct_atomic()` 不创建 Redirect；
- 完全相同 Atomic 只生成 Duplicate Candidate Audit；
- 三个以上重复 Atomic 不触发 Immutable Redirect 冲突；
- Package Membership 不再因自动 Atomic Redirect 变成悬空引用；
- Atomic External Relations 不再因自动 Redirect 指向非活跃 Source；
- Derived State Rebuild 保留 Source、Mention 和 Field Links；
- Derived State Rebuild 删除并重建 Atomic、Embedding、Package 和 Relation；
- 同一输入连续重建结果幂等；
- 重建后不存在旧 HOLD 和旧 Assignment；
- 测试报告明确标记 N10 正常 Profile 更新逻辑仍然沿用当前策略。

## 十二、其他问题的当前处理结论

### 12.1 候选排序和截断

除取消“无 Hard Conflict 优先”外，本轮不调整：

- 活跃 Atomic 10,000 上限；
- 每路最近 20 个；
- 最终 Top 5；
- Route Score 公式；
- Embedding Recall Threshold；
- `SCHEMA_IDENTITY` 的当前语义；
- Broad Event Family Route。

当前已知问题仍然保留：

- 活跃 Atomic 超过 10,000 后可能无法进入候选池；
- 单一路由且没有有效向量时可能同分为 0.5；
- 同分后按 Event ID 排序可能造成任意截断；
- Route Score 只看路线数量，不看路线可靠程度；
- Broad Route 可能挤掉精确候选。

先完成 Atomic Embedding 生命周期和 Canonical Identity 贯通，再重新测量 Recall@K，之后再决定是否实施分层候选预算。

### 12.2 N9 Pair Classifier

该问题由本方案既定 N9 重构处理：

- 改为多候选联合裁决；
- 显式注入 N6 Identity；
- 注入角色化 Canonical Identity 视图；
- 直接输出 MERGE/CREATE_NEW；
- 多个 SAME 确定一个目标；
- Related 不提前落正式关系；
- 不再根据唯一 Pair 分类结果直接推导动作。

本轮不进一步建设：

- 全局图分配；
- SAME_EVENT 传递性校验；
- Atomic-to-Atomic 全局聚类；
- 跨批次联合优化。

### 12.3 完整 Pair Audit

本轮不增加独立 `atomic_pair_decisions` 表。

但完整 N9 Decision 必须写入现有 Decision Audit，使系统至少能够回答：

- 哪些候选进入了 N9；
- 每个候选被判定为什么关系；
- 哪个候选被选中；
- 哪些候选被视为 Related；
- 哪些候选被视为疑似重复 Atomic；
- 是否存在 Claim Conflict；
- 是否存在 Identity Differences。

后续再根据真实测试需求决定是否增加独立追加式表。

## 十三、统一实施步骤

### 第一步：契约、版本与持久化清理

- 全局删除 HOLD 枚举和领域对象；
- 从 `AtomicAction` 和 `PackageAction` 删除 `HOLD`；
- 删除 `HoldKind`、`HoldStatus` 和 `HoldRecord`；
- 从 `CrossDocumentResult` 删除 `hold_ids`；
- 删除 `hold_queue`、Registry Port 和 Registry 实现；
- 删除 Result Export 和 CLI 中的 Hold 输出；
- 修改 Atomic/Package Assignment 的结果约束；
- SQLite Schema 升级时直接删除 `hold_queue`；
- 不迁移旧 Hold、旧 Assignment 或旧跨文档结果；
- 递增 SQLite Schema、Engine 和 Prompt 版本；
- processing key 加入 N8 Mode、Assignment Policy Version 和 Hold Policy。

### 第二步：Atomic Embedding 生命周期修复

- 建立唯一的 `atomic_identity_text()`；
- 统一 Atomic Identity Text、Vector 和 Input Hash；
- 禁止把 Mention Vector 保存为 Atomic Vector；
- 增加 `_sync_atomic_embeddings()`；
- 在 N7 前检查和补齐活跃 Atomic Embedding；
- 在 Atomic 新建、更新和恢复后同步 Embedding；
- 过期 Hash 不再参与召回；
- Redirect Source Embedding 不参与召回；
- 补齐 existing-assignment 恢复路径。

### 第三步：Canonical Field Identity 贯通

- 增加运行时 `ResolvedIdentityEvidence`；
- 实现 `SAME / DIFFERENT / UNKNOWN` 三值比较；
- 统一 External ID、Canonical Root、Legacy ID 和原始文本的来源等级；
- 不同 provisional IDs 保持中性；
- 单侧未知保持中性；
- 对齐 CrossDocument 与 Registry 的 Atomic Field Namespace；
- Place、Technology、Program 进入 `FIELD_ID` 召回；
- Participant Canonical ID 继续进入 `CORE_ENTITY`；
- Field Redirect 和 Link 更新后刷新 Recall Index；
- 为 N9 构造角色化 Participant、Location 和 Named Object 视图。

### 第四步：增量重处理安全

- 删除 Field Resolution 前的 completed-result 快捷返回；
- 固定为 N5.5、N6 完成后再计算最终 processing key；
- `AtomicAssignmentRecord` 增加 `identity_processing_key`；
- `AtomicAssignmentRecord` 增加 `assignment_policy_version`；
- 只允许完全相同 Identity 和 Policy 的失败恢复；
- Identity 变化时禁止覆盖整个 Atomic Profile；
- Identity 变化时返回 `DERIVED_STATE_REBUILD_REQUIRED`；
- 禁止 Mention 同时属于两个活跃 Atomic；
- 增加 Derived State Rebuild 路径。

### 第五步：N8 Shadow 化

- 增加 `enforce|shadow|off` 配置；
- 当前测试和正常 workflow 使用 `shadow`；
- 冲突只计算和审计；
- `AtomicCandidate.hard_conflicts` 在 shadow 模式固定为空；
- 不把 Shadow Conflict 发送给 N9；
- 取消无冲突候选优先；
- 取消 N9 和分配阶段的 Hard Conflict 过滤；
- 删除“全部候选被 Hard Block”的业务分支；
- 指标改为 `atomic_hard_conflict_observed`；
- 身份冲突观测使用三值 Canonical Identity 比较。

### 第六步：N9 联合裁决重构

- 从逐 Pair 分类改为单 Mention、多候选联合裁决；
- 显式注入 N6 Identity；
- 注入角色化 Canonical Identity 视图；
- 使用本方案确认的英文 Prompt；
- 使用新的 JSON Output Schema；
- `identity_conflicts` 重命名为 `identity_differences`；
- `identity_differences` 不再否决 MERGE；
- `claim_conflict` 不再否决 MERGE；
- 最终动作只允许 MERGE/CREATE_NEW；
- 实现多个 SAME、Related 和 UNCERTAIN 的确定性落地；
- N9 不再提前创建正式外部关系；
- 完整 Candidate Assessments 写入 Decision Audit。

### 第七步：模型路由调整

- M2 作为默认 N9 模型；
- 不再因为候选数量达到 4 就自动升级 M3；
- M2 输出 UNCERTAIN 时升级 M3；
- 多个 SAME 时升级 M3；
- Action 与 Assessments 不一致时升级 M3；
- MERGE Target 不明显时升级 M3；
- 第一次业务 Schema 校验失败时升级或修复；
- M3 仍语义不确定时 CREATE_NEW；
- 结构化输出或供应商持续失败时 Run FAILED。

### 第八步：冻结自动 Atomic 校正

- 保持正常 Mention-to-Atomic N10 更新方式不变；
- `_correct_atomic()` 改为 Audit-Only；
- 不自动修改两个 Atomic；
- 不创建 Atomic Redirect；
- 多个 SAME 只写 `possible_duplicate_atomic_ids`；
- 完全相同对象只写 `ATOMIC_DUPLICATE_CANDIDATE`；
- 不实施在线成员移除、拆簇或回滚。

### 第九步：Package 与下游收口

- Package Assignment 不确定时 CREATE_NEW_PACKAGE；
- Package Merge 不确定时保持分离并审计；
- Package 过度扩张只写异常审计；
- Package 技术失败令 Run 失败；
- N9 Related 只保存候选审计；
- 正式 `RELATED_TO` 由后续 Relation Gate 决定；
- Result Export 删除 Open Hold；
- Result Export 继续报告未聚类对象或失败对象，但不以 HOLD 表示。

### 第十步：Derived State Rebuild

- 增加 `python -m cdecr registry rebuild-derived`；
- 保留 SourceMessage、EventMention 和 Field Resolution 状态；
- 清理 Atomic、Assignment、Embedding、Package、Membership、Relation 和 Cross-document Run 派生结果；
- 使用当前版本从 N6 开始重建；
- 重建后校验不存在旧 HOLD、旧 Assignment 和旧 Embedding；
- 相同输入重建结果保持幂等。

### 第十一步：真实语料与工程验收

- 使用独立可重置 Registry；
- 重放现有真实测试集；
- 对 N8 原本会阻断的候选单独统计；
- 检查 Shadow Conflict 下的 MERGE 正确性；
- 检查 Canonical Field Identity 的 SAME/DIFFERENT/UNKNOWN；
- 检查 Atomic Embedding 更新和召回；
- 检查多个 SAME 和多个 Related；
- 检查 Derived State Rebuild；
- 执行 focused tests；
- 执行完整非真实 pytest；
- 执行 scoped ruff；
- 执行 strict mypy；
- 执行 wheel build；
- 执行 CLI smoke；
- 输出真实测试审计报告。

## 十四、完整验收标准

### 14.1 HOLD 删除

- 代码和契约中不存在 `HOLD` Action；
- 不存在 `HoldKind`；
- 不存在 `HoldStatus`；
- 不存在 `HoldRecord`；
- 不存在 Hold Registry Port；
- 不存在 `hold_queue`；
- `CrossDocumentResult` 不包含 `hold_ids`；
- Atomic Assignment 全部具有 `resulting_event_id`；
- Package Assignment 全部具有 `resulting_package_id`；
- Package 不确定性不再产生 HOLD；
- 技术失败不会被降级成 CREATE_NEW。

### 14.2 N8 Shadow

- N8 Shadow Conflict 继续计算并写审计；
- Shadow Conflict 不影响候选排序；
- Shadow Conflict 不影响 Top K；
- Shadow Conflict 不进入 N9 输入；
- Shadow Conflict 不过滤候选；
- Shadow Conflict 不阻止 MERGE；
- `atomic_hard_conflict_observed` 只表示旧规则观测数量；
- `enforce` 模式仍可供后续恢复；
- processing key 包含当前 Mode。

### 14.3 N9 联合裁决

- 每个成功处理且具有合法 N6 Identity 的 Mention 都有 Atomic 归属；
- 每个 Mention 恰好一个 Decision；
- 每个候选恰好一个 Assessment；
- Action 只允许 MERGE/CREATE_NEW；
- MERGE Target 必须来自 SAME_EVENT 候选；
- CREATE_NEW 时 Target 为 `null`；
- UNCERTAIN 不能成为 MERGE Target；
- 存在 `identity_differences` 的 SAME_EVENT 可以 MERGE；
- `claim_conflict` 不阻止 SAME_EVENT；
- 多个 SAME 不产生悬空 Mention；
- 多个 SAME 选择一个目标并记录其他疑似重复 Atomic；
- 多个 Related 只创建一个新 Atomic；
- Related 不在 N9 创建正式 `RELATED_TO`；
- UNCERTAIN 经 M3 后仍不确定时创建新 Atomic；
- 完整 Candidate Assessments 写入 Decision Audit。

### 14.4 Atomic Embedding

- Atomic Identity Text 为唯一 Embedding 输入；
- Vector 与 Input Hash 对应完全相同的文本；
- Atomic Version、Profile 和 Identity Text 一致；
- 不再保存 Mention Vector 作为 Atomic Vector；
- 新建 Atomic 具有有效 Embedding；
- 合并新措辞 Mention 后 Embedding 更新；
- Representative Mentions 变化后 Embedding 更新；
- existing-assignment 恢复时补建缺失 Embedding；
- 过期 Hash 不参与召回；
- Redirect Source Embedding 不参与召回；
- 相同 Identity Text 重跑新增 M1 调用为 0。

### 14.5 Canonical Field Identity

- 相同 External ID 判定为 `SAME`；
- 不同可信 External ID 判定为 `DIFFERENT`；
- 相同 Canonical Root 判定为 `SAME`；
- 不同 provisional Root 判定为 `UNKNOWN`；
- External ID 与 provisional ID 判定为 `UNKNOWN`；
- 单侧未知保持 `UNKNOWN`；
- 不同 Legacy Entity ID 不构成可信冲突；
- 单侧 Location Link 不证明相同；
- 相同 Participant External ID 不产生冲突；
- Place、Technology、Program 能通过 `FIELD_ID` 召回；
- Participant Company/Institution/Person 能通过 `CORE_ENTITY` 召回；
- Field Redirect 后 Recall Index 更新；
- Subject 与 Counterparty 保留角色；
- N9 可以区分 `A 收购 B` 和 `B 收购 A`。

### 14.6 增量重处理

- Field Resolution 在最终 completed-result 检查前执行；
- processing key 使用最新 Field Links 和 Redirect Roots；
- Assignment 保存 Identity Processing Key；
- Assignment 保存 Policy Version；
- 相同 Identity 和 Policy 的失败重试可以恢复；
- Identity 变化时不复用旧 Assignment；
- Identity 变化时不覆盖整个 Atomic Profile；
- Identity 变化时返回 `DERIVED_STATE_REBUILD_REQUIRED`；
- Mention 不会同时属于两个活跃 Atomic；
- existing-assignment 恢复能够补齐 Embedding；
- 同一 processing key 重跑保持幂等。

### 14.7 N10 简化替代

- 正常 Mention-to-Atomic Profile 更新方式保持不变；
- `_correct_atomic()` 不修改 Atomic；
- `_correct_atomic()` 不创建 Redirect；
- 完全相同 Atomic 只写 Duplicate Candidate Audit；
- 三个以上重复 Atomic 不触发 Immutable Redirect 冲突；
- Package Membership 不因自动 Atomic Redirect 变成悬空引用；
- Atomic External Relation 不因自动 Redirect 指向非活跃 Source；
- 本轮不声称具备在线成员移除、拆簇或回滚能力；
- 测试报告明确标记 OPEN Profile 集合并集和链式扩张风险。

### 14.8 Derived State Rebuild

- Rebuild 保留 SourceMessage；
- Rebuild 保留 EventMention；
- Rebuild 保留 Evidence；
- Rebuild 保留 Canonical Field Registry/Links/Redirects；
- Rebuild 删除并重建 Atomic Events；
- Rebuild 删除并重建 Atomic Assignments；
- Rebuild 删除并重建 Atomic Embeddings；
- Rebuild 删除并重建 Packages、Memberships 和 Relations；
- Rebuild 不迁移旧 HOLD；
- Rebuild 不迁移旧跨文档 Assignment；
- 相同输入连续重建结果幂等。

### 14.9 工程验证

- CDECR focused tests 通过；
- 完整非真实 pytest 通过；
- scoped ruff 通过；
- strict mypy 通过；
- wheel build 通过；
- CLI smoke 通过；
- SQLite Schema 迁移和重建测试通过；
- 真实测试使用独立可重置 Registry；
- 真实测试报告区分工程正确性与语义质量；
- 测试报告明确标记 N8 为 Shadow；
- 测试报告明确标记 N10 正常 Profile 更新沿用当前策略；
- 测试报告不把 Shadow 观测数量解释为正确 Cannot-Link 数量。

## 十五、明确延期项

以下内容本轮不实施：

- N8 真正 Hard、Soft Conflict、Claim Conflict 的完整三层规则；
- 活跃 Atomic 超过 10,000 的召回扩展；
- 每路 20 和 Top 5 的分层候选预算重构；
- Route Reliability 加权；
- SQLite 向量索引或外部向量数据库；
- 全局或局部图聚类；
- SAME_EVENT 传递性校验；
- 独立 `atomic_pair_decisions` 持久化表；
- 在线 Atomic 成员移除；
- 在线 Atomic 拆簇；
- 成员驱动的共识 Profile 重算；
- Atomic 冻结状态；
- Atomic Redirect 全图迁移；
- Atomic Redirect 回滚；
- 自动 Atomic-to-Atomic 语义合并。

这些延期项应在本轮真实测试完成、Atomic Recall@K 和 N9 MERGE 精度得到重新评估后再决定优先级。
