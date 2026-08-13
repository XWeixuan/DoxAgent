# CDECR Package Workflow V3：Global LLM Clustering + Rolling Registry 一次性重构方案

日期：2026-08-13  
目标版本：Package Workflow V3.0  
上游基线：当前 V2.0R 的 Mention、Atomic 与单文档 Parent Occurrence Proposal 生成结果  
修改边界：只替换跨文档 Package 聚合、Rolling Registry、Package 投影及其必要的持久化/模型传输；不修改 Mention、Field、Atomic、相关性过滤和单文档 Parent Occurrence 的业务语义。

---

## 0. 结论与实施口径

本轮不再继续修补 V2.0R 的候选图、embedding、R1/R2、microcomponent、prototype、hard/soft boundary 或 cluster-to-cluster judge，而是把跨文档 Package 聚合整体替换为一条极简主线：

```text
Mention
  ↓
Atomic
  ↓
现有单文档 Parent Occurrence Proposal
  ↓
Parent Occurrence Pool
  ↓
最多 200 occurrences / batch
  ↓
Global LLM Clustering（Response A）
DeepSeek-V4-Flash-0731 / reasoning=high
  ↓
Rolling Clustering Registry
  ↓
Affected MCP Description Compression（Response B）
  ↓
下一批最多 200 Parent Occurrences
  ↓
Final MCP Registry
  ↓
Occurrence ID → Atomic IDs
  ↓
Atomic Union + Deduplicate
  ↓
Final Packages
```

V3.0 的业务假设是：强模型在一次看到约200条 Parent Occurrence，并在后续批次看到完整压缩 Registry 时，比 pairwise/local graph 更能稳定找到“最具体、具有独立现实发生边界的共同父事件”。本轮首先验证该假设，不在 V3.0 内重新引入复杂的 Package maintenance。

### 0.1 必须完整保留的上游

- Mention、Field、Atomic 生成与已有审计不变。
- 相关性 Enforce Gate 不变；明确 `IRRELEVANT` 的 candidate 继续不得进入 Grounder/后续。
- 当前 `AtomicDocumentSlice → ParentInductionDocument → ParentInductionDecision → ParentProposalCard` 的单文档 Parent Occurrence 生成语义不变。
- `parent_occurrence_induction.md` 及其 Schema 不在本轮重写。
- 当前 Parent Proposal 的 `label` 直接作为 V3 的自然语言 `parent_occurrence`。
- Parent Proposal 内的 Atomic 映射、membership relation、document refs、external links 只在本地保留，不进入聚类 Prompt。

### 0.2 必须整体退出 active path 的 V2.0R 跨文档机制

- `CanonicalParentPrototype` 与 existing Package prototype matching；
- Package-level M1 embedding；
- structured/semantic route candidate graph；
- weighted microcomponent packing；
- R1/R2 set resolution；
- bridge ledger；
- proposal-level merge admission repair；
- `proposal_merge_guard` 在跨文档 Package 合并中的决策作用；
- final parent reducer 及 V2 frozen partition；
- V2.0R Package artifact、checkpoint 与 partition cache 的 active reuse。

这些逻辑可以在迁移期保留为只读历史代码或测试证据，但 V3.0 正式入口不得调用；V3验收通过后删除无入口代码，避免长期维护两套 Package 语义。

### 0.3 V3.0 明确不做

- 不新增 embedding centroid；
- 不新增 Package Prototype；
- 不新增 family-specific identity rule/阈值矩阵；
- 不新增 microcluster；
- 不新增 pairwise 或 cluster-to-cluster judge；
- 不新增 final hard split、oversized review 或 union-find；
- 不让模型输出 Atomic IDs、Package family、membership relation 或额外 reasoning；
- 不依赖 `previous_response_id` 保存状态；
- 不用 raw Package/Atomic 全量正文替代 Parent Occurrence；
- 不因一个 Package batch 失败而重跑 Mention、Field、Atomic 或整批文章。

---

## 1. 当前实现核对与必须解决的兼容点

### 1.1 当前真实主路径

当前代码中：

- `src/cdecr/parent_occurrence_contracts.py:14-160` 定义 Atomic slice、文档内 Induction 和 Parent Proposal；
- `src/cdecr/parent_occurrence.py:711` 将文档内决策编译为 `ParentProposalCard`；
- `src/cdecr/parent_occurrence.py:1976` 之后继续执行 prototype、embedding、R1/R2、admission repair 和 frozen partition；
- `src/cdecr/package_projection.py:112` 把 V2 partition 投影为 EventPackage；
- `src/cdecr/bulk_epoch/engine.py:796` 是批量 Package stage 入口；
- `src/cdecr/cross_document.py` 的增量路径也会对全部 current Atomic 重新运行 ParentOccurrenceService。

V3 的切点应位于 `_proposals(...)` 之后：其前保留，其后整体替换。

### 1.2 `occurrence_id` 不能直接沿用当前 request-local ref

当前 `proposal_ref=P1/P2/...` 只在单次请求内稳定；`proposal_id` 虽为 hash，但包含 label、family、boundary payload，模型措辞轻微变化可能产生新 ID。V3 Rolling Registry 需要跨 batch、跨断点、跨增量 run 稳定的 Occurrence ID。

因此新增本地稳定映射：

```text
occurrence_business_key = SHA256(
  registry_scope_id
  + document_fingerprint
  + sorted(event_ids)
  + scope
  + sorted(membership_by_event)
)

occurrence_business_key → PO-000001（本地事务分配、永久不变）
```

`label/parent_occurrence` 可以版本更新，但不参与稳定 ID；相同业务键重放时复用原 occurrence_id。

LLM 输入中的 `occurrence_id` 就是该短永久 ID，不再额外增加一套 wire ID。

### 1.3 当前 Parent Proposal 不是 Atomic 的天然严格分区

真实冻结结果显示：

- R3 快照：115个 Parent Proposal 覆盖192个 Atomic，其中29个 Atomic 出现在多个 Proposal，单个 Atomic 最多出现14次；
- 正式 V2 快照：132个 Parent Proposal 覆盖281个 Atomic，其中24个 Atomic 出现在多个 Proposal，单个 Atomic 最多出现12次。

因此“Occurrence → Atomic Union + Deduplicate”必须同时处理：

1. 同一个 MCP 内多个 Occurrence 重复包含同一 Atomic：直接 set union；
2. 同一 Atomic 被不同 MCP 的 Occurrence 同时引用：必须确定唯一 Package 归属。

V3 不新增第四个 LLM 节点。跨 MCP 冲突采用最小确定性策略：

```text
owner_score(MCP, Atomic)
  = 该 MCP 内引用该 Atomic 的 distinct occurrence 数量

选择 score 最大的 MCP；
并列时按 membership relation 支持数；
仍并列时按永久 mcp_id 排序。
```

全部冲突写入 `PACKAGE_V3_CROSS_MCP_ATOMIC_DEDUP` 审计，报告 `conflict_atomic_count`、候选 MCP、支持 occurrence 与最终 owner。不得通过自动合并 MCP 来解决，因为共享一个 Atomic 不足以证明两个父事件整体相同。

### 1.4 Rolling Schema 没有 split：这是 V3.0 的真实限制

Node 2 只允许：

- 新 Occurrence 加入已有 MCP；
- 创建新 MCP；
- 合并已有 MCP。

它不能把历史 Occurrence 从一个 MCP 移到另一个 MCP，也不能拆分错误 MCP。因此第一批或早期 Rolling batch 的误并在 V3.0 内不可逆。这不是实现遗漏，而是粘贴方案的最小 Schema 边界。

本轮严格保留该边界，不暗中增加 split/maintenance；但发布验收必须增加批次顺序敏感性测试。如果顺序变化反复制造不同污染簇，则说明此最小 Rolling contract 本身不成立，应停在 V3.0 验证阶段，而不是继续叠加规则。

### 1.5 Responses API 与 JSON Schema 兼容

当前 `ResponsesModelRequest` 只允许 `output_mode=json_object`，`_responses_kwargs` 会把 Schema 作为 Prompt 文本附加，不能直接表达粘贴方案要求的 `text.format.type=json_schema`。

此外，当前生产 provider 是百炼 DashScope，而不是 DeepSeek 官方端点。官方资料对 DeepSeek V4 Flash 的 Responses/structured-output支持口径并不完全一致：DeepSeek 官方当前主要公开 Chat Completions/Anthropic；百炼不同页面对 DeepSeek V4 structured output 的标注也存在差异。因此实施前必须对当前百炼 provider 做三次真实最小探针（Node1/2/3各一次）：

```text
provider = dashscope
model = deepseek-v4-flash-0731（或 provider 返回的等价固定 0731 snapshot）
Responses API
reasoning.effort = high
text.format.type = json_schema
```

只有探针同时通过 provider、JSON parse、Pydantic 与业务 coverage 校验，才进入真实 V3 测试。不得静默切回 DeepSeek 官方、不得静默关闭 thinking、不得静默改成普通文本输出。若 provider 不支持，则本轮实现状态为 `PACKAGE_V3_PROVIDER_CONTRACT_BLOCKED`，由用户另行决定是否允许 Chat Completions strict-tool fallback。

官方核对入口：

- DeepSeek V4 API/Thinking：<https://api-docs.deepseek.com/api/create-chat-completion>
- 百炼 DeepSeek V4：<https://help.aliyun.com/zh/model-studio/deepseek-api>
- 百炼结构化输出：<https://help.aliyun.com/zh/model-studio/qwen-structured-output>

---

## 2. V3 核心数据模型

### 2.1 模型不可见的 Parent Occurrence 本地记录

```python
class PackageParentOccurrenceV3(StrictModel):
    occurrence_id: str                 # PO-000001，永久 ID
    occurrence_business_key: str       # 幂等键
    source_proposal_id: str
    parent_occurrence: str             # 当前 ParentProposalCard.label
    atomic_event_ids: list[str]         # 当前代码中的 event_id，即业务 Atomic IDs
    membership_by_event: dict[str, MembershipRelation]
    document_refs: list[str]
    scope: ParentScope
    external_links: list[ParentExternalLinkProposal]
    source_payload_hash: str
```

模型只看到：

```json
{
  "occurrence_id": "PO-000001",
  "parent_occurrence": "Micron fiscal Q3 2026 earnings disclosure"
}
```

### 2.2 Rolling MCP 本地状态

```python
class RollingMCPStateV3(StrictModel):
    mcp_id: str                         # MCP-000001，本地永久分配
    canonical: str
    compressed_description: str
    occurrence_ids: list[str]
    status: Literal["ACTIVE", "MERGED"]
    redirect_to: str | None = None
    created_registry_version: int
    updated_registry_version: int
```

`count` 由 `len(occurrence_ids)` 派生，不单独成为模型字段。Registry 给 Node 2 时只输出：

```text
mcp_id + canonical + description
```

不输出 occurrence_ids、count、Atomic IDs、Package IDs 或额外 audit 字段。

### 2.3 冻结 Registry 与最终 Package Partition

```python
class FrozenRollingRegistryV3(StrictModel):
    registry_scope_id: str
    registry_version: int
    registry_hash: str
    status: Literal["FINALIZED"]
    mcps: list[RollingMCPStateV3]

class FrozenPackageClusterV3(StrictModel):
    mcp_id: str
    canonical: str
    compressed_description: str
    occurrence_ids: list[str]
    atomic_event_ids: list[str]
    membership_by_event: dict[str, MembershipRelation]
    document_refs: list[str]

class FrozenPackagePartitionV3(StrictModel):
    registry_scope_id: str
    registry_version: int
    registry_hash: str
    partition_hash: str
    groups: list[FrozenPackageClusterV3]
```

正式 Package ID 由本地稳定映射生成：

```text
package_id = stable_id("package-v3", registry_scope_id + mcp_id)
```

MCP canonical/description 是 Rolling 上下文与最终展示元数据，不是额外的 Package identity prototype，也不参与任何确定性自动合并。

---

## 3. 三个模型节点

三个节点统一使用：

```text
provider: 当前配置的 DashScope/百炼
API: Responses API
model: deepseek-v4-flash-0731
reasoning.effort: high
output: JSON Schema
previous_response_id: null
```

不修改全局 M2/M3/M4 thinking 档位；V3 Package 请求在 `ResponsesModelRequest` 中显式指定 `high`，避免影响其他节点。

### 3.1 Node 1：V1 Registry Clustering

#### System Prompt（原样使用）

```text
You are the global Package-level event clustering agent.

Cluster all Parent Occurrences by their Minimal Common Parent Event.

A Minimal Common Parent Event is the most specific common real-world event that has an independent occurrence boundary. Different facts, metrics, guidance, statements, or other aspects belong together when they are parts of that same event; separate real-world events remain separate.

Every occurrence_id must belong to exactly one MCP. Use a singleton MCP when no valid common parent event exists.

Return JSON only according to the required output schema.
```

#### Input

```json
{
  "parent_occurrences": [
    {
      "occurrence_id": "PO-000001",
      "parent_occurrence": "Micron fiscal Q3 2026 earnings disclosure"
    }
  ]
}
```

#### Output Schema（字段与粘贴方案一致）

```json
{
  "type": "object",
  "properties": {
    "clusters": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "mcp_id": {"type": "string"},
          "canonical": {"type": "string"},
          "occurrence_ids": {
            "type": "array",
            "items": {"type": "string"}
          }
        },
        "required": ["mcp_id", "canonical", "occurrence_ids"],
        "additionalProperties": false
      }
    }
  },
  "required": ["clusters"],
  "additionalProperties": false
}
```

Node 1 返回的 `mcp_id` 只作为本次 Response 内的临时 cluster ref。为保证断点恢复和 ID 冲突安全，本地按临时 mcp_id 排序后分配永久 `MCP-000001...`；这一转换不改变模型聚类结果，也不增加 Prompt 字段。

#### 业务校验

- 输入 occurrence_id 必须全部出现且恰好一次；
- 不得出现未知 occurrence_id；
- cluster 临时 mcp_id 必须唯一；
- canonical 非空；
- cluster 不得为空；
- 同一 cluster 内重复 occurrence_id 可确定性去重；跨 cluster 重复视为非法；
- 输出顺序不作为语义。

### 3.2 Node 2：Rolling Registry Clustering

#### System Prompt（原样使用）

```text
You are maintaining a Rolling Package Clustering Registry.

Assign the new Parent Occurrences to MCPs according to their Minimal Common Parent Event.

A Minimal Common Parent Event is the most specific common real-world event that has an independent occurrence boundary. Different facts, metrics, guidance, statements, or other aspects belong together when they are parts of that same event; separate real-world events remain separate.

Use an existing MCP when appropriate, create a new MCP when needed, and merge existing MCPs when the new evidence shows they represent the same Minimal Common Parent Event.

Every new occurrence_id must be assigned exactly once.

Return only the Registry changes as JSON according to the required output schema.
```

#### Input

```json
{
  "registry": [
    {
      "mcp_id": "MCP-000001",
      "canonical": "Micron FY2026 Q3 Earnings Release",
      "description": "Micron公布FY2026 Q3季度财务业绩，包括季度收入/利润表现、财报披露、电话会议及相关经营更新。"
    }
  ],
  "new_parent_occurrences": [
    {
      "occurrence_id": "PO-000201",
      "parent_occurrence": "Micron Q3 earnings call commentary on HBM demand"
    }
  ]
}
```

#### Output Schema（原样使用）

```json
{
  "type": "object",
  "properties": {
    "existing_assignments": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "mcp_id": {"type": "string"},
          "occurrence_ids": {
            "type": "array",
            "items": {"type": "string"}
          }
        },
        "required": ["mcp_id", "occurrence_ids"],
        "additionalProperties": false
      }
    },
    "new_mcps": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "canonical": {"type": "string"},
          "occurrence_ids": {
            "type": "array",
            "items": {"type": "string"}
          }
        },
        "required": ["canonical", "occurrence_ids"],
        "additionalProperties": false
      }
    },
    "merges": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "keep_mcp_id": {"type": "string"},
          "merge_mcp_ids": {
            "type": "array",
            "items": {"type": "string"}
          }
        },
        "required": ["keep_mcp_id", "merge_mcp_ids"],
        "additionalProperties": false
      }
    }
  },
  "required": ["existing_assignments", "new_mcps", "merges"],
  "additionalProperties": false
}
```

#### 本地 ID 与 Apply 规则

- `existing_assignments.mcp_id` 必须是当前 active MCP；
- `new_mcps` 的永久 mcp_id 由本地顺序分配；
- merges 的 keep/source 必须是当前 active MCP；
- 一个 merge source 本轮只能出现一次；
- keep 不得同时作为 merge source；
- 不允许 merge cycle 或同一 source 指向多个 keep；
- assignment 若指向本轮被 merge 的 source，本地规范化到最终 keep；
- 每个新 occurrence_id 必须在 existing assignments 或 new MCPs 中恰好出现一次；
- 不允许对历史 occurrence 做隐式 reassignment；
- canonical 只在创建 MCP 时生成；merge 后保留 keep MCP canonical，Response B 只刷新 description。

### 3.3 Node 3：Compressed Registry Description

#### System Prompt（原样使用）

```text
Compress the Parent Occurrences of each MCP into a single concise Registry description.

Reduce redundant phrasing, but do not merge multiple distinct real-world actions into a single generalized event. You must preserve the minimal distinguishing heterogeneous information required to separate member boundaries, including different entities, institutions, products, actions, numerical values, and time references. If the MCP itself contains clearly heterogeneous members, this heterogeneity must be explicitly reflected in the compressed description rather than being abstracted away into a higher-level unified theme.

Return JSON only according to the required output schema.
```

#### Input

```json
{
  "mcps": [
    {
      "mcp_id": "MCP-000004",
      "canonical": "Micron Strategic Customer Agreements",
      "parent_occurrences": [
        {
          "occurrence_id": "PO-000005",
          "parent_occurrence": "Micron's introduction of strategic customer agreements"
        },
        {
          "occurrence_id": "PO-000019",
          "parent_occurrence": "Micron's $22 billion customer commitments"
        }
      ]
    }
  ]
}
```

#### Output Schema（原样使用）

```json
{
  "type": "object",
  "properties": {
    "descriptions": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "mcp_id": {"type": "string"},
          "compressed_description": {"type": "string"}
        },
        "required": ["mcp_id", "compressed_description"],
        "additionalProperties": false
      }
    }
  },
  "required": ["descriptions"],
  "additionalProperties": false
}
```

#### 业务校验

- 每个 affected MCP 必须恰好返回一条 description；
- 不得返回非 affected MCP；
- description 非空；
- Node 3 必须从数据库重新读取该 MCP 的全部 Parent Occurrence 原文；
- 不允许只用上一版 description 增量改写；
- description 不作为新的 cluster decision，也不得修改 membership。

---

## 4. 两阶段 Response 的事务连接

### 4.1 第一批

```text
Batch 1（≤200 Parent Occurrences）
        ↓
Node 1 Response A
        ↓
完整 coverage/schema/semantic validator
        ↓
本地分配永久 MCP IDs
        ↓
保存 STAGED clustering changes
        ↓
按 staged membership 从 occurrence store 读取全部原文
        ↓
Node 3 Response B
        ↓
description validator
        ↓
单事务发布 Registry V1
```

### 4.2 后续批次

```text
Registry Vn 全部 active MCP 的：
mcp_id + canonical + description
+
Batch n+1（≤200 new occurrences）
        ↓
Node 2 Response A
        ↓
本地校验 assignments/new/merges
        ↓
保存 STAGED changes（不移动 current pointer）
        ↓
计算 affected MCPs
        ↓
重新读取 affected MCP 的全部 occurrence 原文
        ↓
Node 3 Response B
        ↓
单事务发布 Registry Vn+1
```

`affected MCPs` 严格包括：

- 本轮新建 MCP；
- 本轮新增 Occurrence 的已有 MCP；
- merge keep MCP；
- merge source 不再生成 description，只写 redirect。

Node A 完成后不能立即修改对外可见 Registry；它只生成 staged changes。Node B 全部成功后才在一个短 SQLite transaction 中更新 MCP heads、occurrence membership、redirect、registry version 和 current pointer。这样停电或 provider 错误不会留下半版 Registry。

### 4.3 Description packing

Node 3 不需要跨 MCP 推理，因此可以只做工程层 packing：

- 每个 MCP 的全部原始 Parent Occurrence 必须在同一个 description request 中；
- 多个 MCP 可按 token budget 分成若干请求并行；
- 不拆分单个 MCP 后再二次合并摘要；
- 所有 description task 完成后才发布 Registry version。

这不构成额外业务节点，只是 Node 3 的无损请求分包。

---

## 5. Batch 与 Registry 顺序

### 5.1 Bulk 历史新闻

Parent Occurrence Pool 使用稳定排序：

```text
published_at
→ document_fingerprint
→ occurrence_business_key
→ occurrence_id
```

每批最多200条。除非预计输入超出 context reserve，否则不下调 batch；绝不因 family、issuer 或文档边界切出语义 batch。

所有 batch 严格顺序执行，因为 Registry Vn+1 依赖 Vn。Node 1/2 不并行；Node 3 内不同 MCP 的无损 description pack 可以并行。不同 `registry_scope_id` 可以独立并行。

### 5.2 日常增量新闻

- 每个监测 universe 使用稳定 `registry_scope_id`；不能把不同 ticker/tenant/corpus 写入同一 Registry。
- 增量 invocation 将当前 pending Parent Occurrences 一次 drain，最多200条；不必等待凑满200。
- 同一 scope 只允许一个 Rolling writer；使用 current registry version 的 compare-and-swap 防止并发覆盖。
- 新 batch 开始前读取当前完整 active Registry。
- 失败时保留上一版 Final Packages，新 Atomic 标为 `PACKAGE_PENDING_RETRY`，不回退为伪 singleton。
- 重试只从失败 batch/checkpoint 继续，不重跑文档、Mention、Field、Atomic 或已完成 Registry batch。

### 5.3 完整 Registry 的 context 边界

本方案要求给 Node 2 发送完整压缩 Registry，不能偷偷改为 retrieval/top-K。调用前进行 token estimator，并预留系统 Prompt、200条新 Occurrence、最大合理输出和 reasoning 的安全空间。

若“完整 Registry + 至多200条新 Occurrence”超过 provider context：

- 可以减小本轮 new occurrence batch；
- 不允许截断 Registry；
- 若单独完整 Registry 已超限，返回 `PACKAGE_V3_REGISTRY_CONTEXT_EXHAUSTED`；
- current Registry 和 Package 保持不变，pending occurrence 可重试；
- 不在 V3.0 内引入检索式 Registry，因为那会改变“全局视野”核心假设。

---

## 6. 持久化设计

新增 V3 专用表，不复用 V2 partition/checkpoint 作为当前状态。

### 6.1 `package_parent_occurrences_v3`

```sql
registry_scope_id TEXT NOT NULL
occurrence_id TEXT NOT NULL
occurrence_business_key TEXT NOT NULL
source_proposal_id TEXT NOT NULL
parent_occurrence TEXT NOT NULL
payload_json TEXT NOT NULL
source_payload_hash TEXT NOT NULL
created_at TEXT NOT NULL
updated_at TEXT NOT NULL
PRIMARY KEY (registry_scope_id, occurrence_id)
UNIQUE (registry_scope_id, occurrence_business_key)
```

### 6.2 `package_registry_heads_v3`

每个 scope 一行：

```sql
registry_scope_id TEXT PRIMARY KEY
current_registry_version INTEGER NOT NULL
current_registry_hash TEXT NOT NULL
next_occurrence_sequence INTEGER NOT NULL
next_mcp_sequence INTEGER NOT NULL
status TEXT NOT NULL
updated_at TEXT NOT NULL
```

### 6.3 `package_mcp_heads_v3`

```sql
registry_scope_id TEXT NOT NULL
mcp_id TEXT NOT NULL
canonical TEXT NOT NULL
compressed_description TEXT NOT NULL
status TEXT NOT NULL CHECK(status IN ('ACTIVE','MERGED'))
redirect_to TEXT
created_registry_version INTEGER NOT NULL
updated_registry_version INTEGER NOT NULL
payload_json TEXT NOT NULL
PRIMARY KEY (registry_scope_id, mcp_id)
```

### 6.4 `package_mcp_occurrence_memberships_v3`

```sql
registry_scope_id TEXT NOT NULL
occurrence_id TEXT NOT NULL
mcp_id TEXT NOT NULL
assigned_registry_version INTEGER NOT NULL
updated_at TEXT NOT NULL
PRIMARY KEY (registry_scope_id, occurrence_id)
```

### 6.5 `package_registry_batches_v3`

```sql
registry_scope_id TEXT NOT NULL
batch_id TEXT NOT NULL
base_registry_version INTEGER NOT NULL
input_hash TEXT NOT NULL
status TEXT NOT NULL
response_a_payload_json TEXT
staged_changes_json TEXT
affected_mcp_ids_json TEXT
error_code TEXT
created_at TEXT NOT NULL
updated_at TEXT NOT NULL
PRIMARY KEY (registry_scope_id, batch_id)
```

状态：

```text
PENDING
→ CLUSTERING_SUCCEEDED
→ DESCRIPTIONS_PARTIAL / DESCRIPTIONS_SUCCEEDED
→ FINALIZED
或 FAILED_RETRYABLE
```

### 6.6 `package_registry_versions_v3`

```sql
registry_scope_id TEXT NOT NULL
registry_version INTEGER NOT NULL
registry_hash TEXT NOT NULL
source_batch_id TEXT NOT NULL
snapshot_json TEXT NOT NULL
created_at TEXT NOT NULL
PRIMARY KEY (registry_scope_id, registry_version)
UNIQUE (registry_scope_id, registry_hash)
```

每个 finalized version 保存完整 active MCP snapshot，便于幂等、回放、质量评估和人工审计。它不是给 LLM 维护的第二套状态；`package_registry_heads_v3.current_registry_version` 是唯一 current pointer。

### 6.7 现有通用审计继续复用

- 每次 Node1/2/3调用继续写 `model_calls`；
- 完整 Response A/B、normalized changes、coverage、merge、Atomic dedup 写 `decision_audits`；
- Bulk artifact 新增 `package_occurrence_pool_v3`、`package_registry_final_v3`、`package_partition_v3`；
- 不再写 `package_frozen_partition_v2` 或 `package_partition_v2` 作为 V3成功证据。

---

## 7. 校验、repair 与失败语义

### 7.1 允许确定性规范化的错误

- 数组顺序；
- 同一目标内重复 occurrence_id；
- merge source 顺序；
- 空白 canonical/description 的首尾空格；
- assignment 指向本轮 merge source 时重定向到 keep root。

### 7.2 需要一次同节点 repair 的错误

- missing occurrence；
- occurrence 跨不同 cluster/assignment 重复；
- unknown occurrence/MCP ID；
- merge cycle、多个 keep 或非法 source；
- affected description 缺失/重复/未知；
- JSON/Schema 非法。

repair 使用相同输出 Schema，只附加原请求、原非法输出与短错误列表；最多一次。Node1/2 repair 仍是当前 batch 级，因为全局 partition/change set 不能安全拆成 item repair。Node3 repair 只重试非法 description task，不重跑已成功 description，也不重跑 Node A。

### 7.3 repair 后仍失败

- 不把 missing occurrence 硬塞成 singleton；
- 不发布半版 Registry；
- 不修改 current MCP membership；
- batch 标为 `FAILED_RETRYABLE`；
- Bulk 初始构建返回 `PARTIAL_PACKAGE_REGISTRY`，但保留上游 Atomic；
- Incremental 保留上一版 Packages，新 Atomic 保持 pending；
- 余额/网络/schema修复后从该 batch 继续。

这是合理的 Package-stage阻断：非法 global change set 会使 Registry 唯一归属或下一轮完整上下文失效，属于下游无法安全继续的错误；它不会扩大为文章或 Atomic 失败。

### 7.4 Node 3 失败的特殊处理

Response A staged changes必须留存。Node 3 部分成功时：

- 保存已成功 description task；
- 仅重试缺失/非法 affected MCP；
- current Registry pointer 不前移；
- 不重复 Node 1/2 调用；
- 全部 affected descriptions 完成后一次性 finalize。

---

## 8. Final Registry → Package Projection

### 8.1 MCP 展开

对每个 active MCP：

1. 读取完整 occurrence_ids；
2. 读取每个 occurrence 的 atomic_event_ids；
3. MCP 内 set union；
4. 执行跨 MCP Atomic unique-owner dedup；
5. 汇总 membership relation、document refs 和外部关系；
6. 丢弃 dedup 后没有 Atomic 的空 Package projection，但保留 MCP Registry 状态和审计。

### 8.2 EventPackage 字段

不修改现有 EventPackage 持久化模型：

- `package_id`：由 scope+mcp_id 稳定生成；
- `canonical_title`：MCP canonical；
- `canonical_summary`：compressed description，仅作为展示摘要，不参与自动 identity；
- `package_family`：沿用当前 Atomic family 多数派确定性派生；
- `parent_scope`：沿用 member Parent Occurrence scope 多数派；
- `member_event_ids`：dedup 后 Atomic IDs；
- `supporting_proposal_ids`：member occurrences 的 source_proposal_id；
- `supporting_document_refs`：member occurrence document refs 并集；
- `anchor_entities/time_range/status`：沿用当前确定性投影；
- `partition_hash`：V3 frozen partition hash。

### 8.3 Membership relation

同一个 Atomic 在 winning MCP 的多个 Occurrence 中出现时：

- 对 relation 计数；
- 选择支持数最多的 relation；
- 并列时按固定 enum 顺序选择；
- 写入 dedup audit。

不要求 Node 1/2 输出 relation，不增加模型负担。

### 8.4 External relation

当前单文档 Parent Occurrence 的 external link 不进入聚类 Prompt，但在最终本地映射：

- target proposal → occurrence_id → final mcp_id → package_id；
- source Atomic 与 target Package相同则丢弃自环；
- 其余继续生成 `PackageExternalRelation`；
- relation 类型和支持文档保持原值。

### 8.5 Rolling merge 与 Package continuity

- new MCP 创建稳定新 Package ID；
- existing MCP 增加 occurrence 时复用 Package ID并生成新 Package version；
- MCP merge 保留 keep MCP 的 Package ID；
- merge source Package 写 redirect 到 keep Package；
- unaffected MCP 不新增 Package version。

---

## 9. V2.0R → V3.0 切换

### 9.1 不能直接把旧 V2 Package 当成 V3 Registry

V2.0R 已证明存在污染和碎片化；将旧 Package直接压缩成 MCP V1 会把旧错误固化为 Rolling 历史，而且 Node2没有split能力。因此 V3首次启动必须从当前 Parent Occurrence Pool重新聚类，不能从旧 EventPackage bootstrap。

### 9.2 Shadow build 与原子切换

1. 创建 V3 tables，不触碰现有 Package active state；
2. 从当前 active Atomic +现有 Parent Occurrence生成逻辑构建完整 Occurrence Pool；
3. 运行 V3 Rolling Registry 至 FINALIZED；
4. 生成 V3 frozen partition 和 Package-only质量报告；
5. 通过 Gate 后，在一个迁移事务中将 active Package membership切到V3；
6. V2 Package状态保留为版本化历史/验收 artifact，不作为 current Package；
7. 由于旧 Package可能一对多拆到多个V3 MCP，不能伪造单一 redirect；只有旧 Package全部 active成员落到同一V3 Package时才可写 redirect。

未通过 Gate 时，V3 Registry与测试结果保留在隔离 scope，current生产 Package不切换，也不自动回滚代码。

### 9.3 版本隔离

至少更新：

```text
BULK_STAGE_GRAPH_VERSION = cdecr-bulk-epoch-v10-package-global-registry-v3
PACKAGE_WORKFLOW_CONTRACT_VERSION = package-global-registry-v3-contract-1
PACKAGE_REGISTRY_POLICY_VERSION = package-global-registry-v3-rolling-1
PACKAGE_ASSIGNMENT_POLICY_VERSION = package-global-registry-v3
Prompt versions = package-v3-node1/node2/node3-v1
Artifact kinds = *_v3
```

所有 checkpoint/input hash 必须包含：Prompt全文、Schema、model ID、reasoning effort、provider、contract version、registry base hash、batch occurrence payload。V2 finalized partition 不得命中 V3 cache。

---

## 10. 代码落点

### 10.1 新增

- `src/cdecr/package_global_clustering.py`
  - `PackageWorkflowV3Service`
  - deterministic batch planner
  - Node1/Node2/Node3 request compiler
  - validation、staging、resume、finalize
- `src/cdecr/package_v3_contracts.py`
  - 三个模型 I/O DTO
  - Occurrence/MCP/Registry/frozen partition DTO
- `src/cdecr/prompts/v1/package_v3_initial_clustering.md`
- `src/cdecr/prompts/v1/package_v3_rolling_clustering.md`
- `src/cdecr/prompts/v1/package_v3_registry_description.md`
- `scripts/cdecr_run_package_v3_only.py`
  - 冻结 Parent Occurrence/Atomic 的真实 Package-only runner
  - 支持断点恢复、幂等复验、Registry导出、Gold eval

### 10.2 修改

- `src/cdecr/parent_occurrence.py`
  - 保留 snapshot、slice、Induction、现有 Parent Proposal编译；
  - 暴露 `build_parent_occurrence_pool(...)`；
  - 移除 V3 active path 对 prototype/embedding/R1/R2/admission 的调用。
- `src/cdecr/parent_occurrence_contracts.py`
  - 保留 Induction 与 Parent Proposal contracts；
  - V2 Resolution/Frozen contracts 迁移后退出 active imports。
- `src/cdecr/models.py`、`src/cdecr/ports.py`
  - 为 Responses request 增加真正 `json_schema` format；
  - 保持现有 json_object调用兼容；
  - 增加 package-specific audited typed Responses调用，不改变其他节点。
- `src/cdecr/package_projection.py`
  - 接收 `FrozenPackagePartitionV3`；
  - 使用 mcp_id 稳定 Package ID；
  - 只更新 affected Package；
  - V3 assignment reason。
- `src/cdecr/registry.py`、`src/cdecr/ports.py`
  - 新表、CAS current version、staged batch、description checkpoint、final snapshot。
- `src/cdecr/bulk_epoch/engine.py`
  - Package stage 改为 Pool → V3 Registry → V3 Apply；
  - 新 artifact/version；
  - 上游 Stage Graph不改。
- `src/cdecr/cross_document.py`
  - 增量 Package从“全量 current Atomic重跑V2”改为“新增 Parent Occurrence追加到指定 Rolling scope”；
  - scope内串行，跨scope可并行。
- `src/cdecr/config.py`、`.env.example`
  - 增加 V3 专用少量配置；删除/弃用V2 route/resolution配置。

### 10.3 配置

```text
CDECR_PACKAGE_V3_BATCH_SIZE=200
CDECR_PACKAGE_V3_MODEL=deepseek-v4-flash-0731
CDECR_PACKAGE_V3_REASONING_EFFORT=high
CDECR_PACKAGE_V3_DESCRIPTION_ACTIVE_REQUESTS=16
CDECR_PACKAGE_V3_CONTEXT_RESERVE_TOKENS=<实现时按provider实测固定>
CDECR_PACKAGE_V3_REGISTRY_SCOPE=<由业务调用方显式提供，不建议全局default>
```

不保留 structured/semantic route quota、resolution proposal cap、existing prototype cap、R1/R2并发和 reconcile 配置作为 V3 active配置。

---

## 11. Telemetry 与审计

### 11.1 每个 Registry batch

- base/final registry version/hash；
- new occurrence count；
- registry MCP count；
- request input/output/reasoning/cached token；
- Node1或Node2 wall/latency；
- new/existing assignment/merge count；
- affected MCP count；
- Node3 request count和wall；
- schema repair count；
- staged/resume/finalize状态；
- complete occurrence coverage；
- MCP size distribution；
- context token estimate与reserve。

### 11.2 最终 Package

- Package count、singleton count/ratio；
- MCP occurrence size与Atomic size分布；
- cross-MCP Atomic conflict count；
- dedup后空 MCP count；
- merge redirect count；
-超大 Package列表与成员；
- Package Pair P/R/F1；
- fragmented Gold groups、components、singleton components、missed links；
- false-merge groups与误成员比例；
- Micron earnings、SCA、market episode、analyst report、Apple session等已知bad cases。

### 11.3 不增加模型 reasoning 字段

审计依赖结构化 assignment/new/merge、Registry versions、description历史和本地映射。三个模型输出均不增加 reasoning，以免增加payload和模型任务复杂度。

---

## 12. 测试方案

### 12.1 Contract/Prompt

1. 三个 Prompt 与本方案逐字一致；
2. 三个 Schema `additionalProperties=false`；
3. Node1完整partition；
4. Node2每条new occurrence恰好一次；
5. Node3 affected MCP完整覆盖；
6. Atomic IDs/occurrence membership不进入Prompt；
7. Rolling Registry Prompt不含 occurrence_ids/count/atomic_ids；
8. reasoning固定high，`previous_response_id=None`；
9. real Responses json_schema探针三节点各一次。

### 12.2 Registry/幂等/恢复

1. 相同 occurrence business key复用ID；
2. V1临时mcp ref映射为本地永久ID；
3. new MCP并发分配无冲突；
4. merge无环、redirect root稳定；
5. 同scope CAS防并发覆盖；
6. Node A成功、Node B失败后只重试B；
7. 停电重启从staged batch继续；
8. finalized batch二跑0模型调用、hash稳定；
9. provider失败不生成伪singleton；
10. V2 checkpoint不能被V3误复用。

### 12.3 Projection

1. occurrence→Atomic union；
2. MCP内重复Atomic去重；
3. 跨MCP Atomic唯一owner与完整audit；
4. empty projection不产生空Package；
5. MCP merge保留keep Package ID并生成source redirect；
6. unaffected MCP不增版本；
7. membership relation稳定归一；
8. external link正确映射且删除自环；
9. 每个active Atomic最终恰好一个active Package membership；
10. EventPackage现有Schema不变。

### 12.4 Batch语义

1. 200只是工程切片，排序规则稳定；
2. 201条必须执行Node1+Node3，再执行Node2+Node3；
3. Node2收到完整active Registry；
4. registry context超限不得截断；
5. 不存在pairwise、candidate graph、embedding或prototype调用；
6. 早期MCP不能被Node2隐式split/reassign；
7. 不同batch顺序的质量漂移可被评估。

---

## 13. 真实验收顺序

### Gate 0：Provider/Schema

- 百炼 DashScope；
- DeepSeek-V4-Flash-0731；
- Responses API；
- reasoning high；
- JSON Schema严格输出；
- Node1/2/3各一次真实调用均通过。

任一失败则停止，不运行完整数据集，不静默降级。

### Gate A：冻结 Package-only

至少使用两份既有冻结上游：

1. V2.1 R3 的192 Atomic / 115 Parent Proposal；
2. 正式 V2 的281 Atomic / 132 Parent Proposal。

只运行 V3 Package，不重跑 Mention/Field/N9。比较当前V2.0R最终结果与V3：

| 指标 | 门槛 |
| --- | ---: |
| Pair Precision | ≥90% |
| Pair Recall | ≥65% |
| Pair F1 | ≥75% |
| Package singleton | ≤45% |
| Gold可判singleton漏合 | ≤25% |
| Micron earnings components | ≤4 |
| high-confidence SCA/report/background intruder | 0 |
| Tuesday selloff 与 Thursday recovery | 不得同包 |
| 同一 Tuesday episode 跨issuer事实 | 不得仅因issuer拆分 |
| 不同 analyst institution/report | 不得合并 |
| Apple intraday/close | 按独立occurrence边界处理 |
| 每个active Atomic唯一Package | 100% |
| V3幂等二跑 | 0新调用 |

两份都必须通过，不能只选择表现较好的一份。

### Gate B：真实30篇全流程

仅 Gate A通过后运行：

- 30/30文档成功；
- relevance Enforce且明确IRRELEVANT 0进入Grounder；
- Mention/Field/Atomic指标不得因Package改动下降；
- Package指标达到Gate A；
- 独立Agent逐层复核package-atomic-mention；
- 报告全部调用、token、reasoning token、wall和阶段占比；
- 不用恢复段wall冒充fresh全流程wall。

### Gate C：多批次与 Rolling 验证

V3的核心风险只在 >200 Occurrence 时暴露，因此30篇通过后必须用冻结MU300 Parent Occurrence Pool做Package-only，不先重跑MU300上游：

- 至少形成2个Rolling batch；
- chronological顺序与一个固定hash-rotated顺序各运行一次；
- Pair Precision均≥90%，Recall均≥65%，F1均≥75%；
- 两种顺序F1差≤3pp；
- Package count差≤10%；
- 不新增系统性supercluster；
- 早期错误MCP不可逆问题不得造成明显污染放大；
- 每轮完整Registry token可被provider容纳；
- evaluations不得退化为pairwise P²，因为V3不存在pair evaluation。

若顺序敏感性失败，应回到Rolling Schema讨论历史 reassignment/split，不得在V3.0上堆叠隐式修正规则。

### 效能门槛

对≤200条的30篇Parent Pool，正常路径应只有：

```text
1次 Node1 + 1个或少量Node3 description packs
```

建议门槛：

- Package M1 embedding调用=0；
- pairwise/R1/R2调用=0；
- Parent/Package input token相对V2.0R下降≥60%；
- Package total token相对V2.0R下降≥50%；
- Package-only clean wall≤5分钟；
- schema repair token≤Package总token的10%；
- 断点恢复不重复成功的Response A/B。

这些是发布Gate，不是通过下调质量换取的目标。

---

## 14. 一次性实施顺序

执行层一次性落地，步骤顺序如下：

1. 冻结现有V2.0R Package-only结果、Prompt hash、代码版本与测试基线；
2. 新增V3 contracts、Prompt和Responses json_schema传输能力；
3. 完成三个真实Schema probe；
4. 抽取现有Parent Occurrence Pool builder，不改Induction语义；
5. 新增V3 Registry表、稳定ID、batch state machine与CAS；
6. 实现Node1与完整coverage validator；
7. 实现Node2 assignment/new/merge validator；
8. 实现Node3 affected MCP原文重读与description packing；
9. 实现staged→final事务、断点恢复和幂等；
10. 实现Occurrence→Atomic dedup、external relation与V3 Package projection；
11. 同时切换bulk与incremental Package入口，确保只剩一条业务主线；
12. 更新版本、artifact、telemetry、CLI doctor和`.env.example`；
13. 完成静态/单元/集成/恢复测试；
14. 完成两份冻结Package-only Gate；
15. Gate通过后才跑真实30篇全流程；
16. 30篇通过后才做冻结MU300 Parent Pool的Rolling/order-sensitivity验证；
17. 无论结果如何不自动回滚，输出是否切换生产current Package的明确判断。

---

## 15. 最终判断

该方案严格采用“Global LLM Clustering + Rolling Registry”作为唯一跨 batch Package状态，把 V2.0R 最复杂、成本最高且质量仍不稳定的 candidate graph/R1/R2/maintenance链路整体移除。模型输入保持最小：首次只看 `occurrence_id + parent_occurrence`，后续只增加完整压缩 Registry；Atomic和持久化细节全部留在本地。

方案没有回避两个真实风险：Rolling contract无法拆分历史误并，以及当前 Parent Proposal对Atomic存在重叠。V3.0分别用顺序敏感性Gate和确定性Atomic唯一归属审计处理，而不是偷偷恢复复杂维护逻辑。

只有当强模型在两份冻结Package-only、真实30篇以及多batch顺序测试中同时证明高Precision、高Recall、低碎片化和低顺序漂移，V3.0才具备替代V2.0R的业务依据。否则应判定“极简Rolling假设未成立”，而不是继续用更多边界规则把它重新变成V2式系统。
