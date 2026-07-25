# CDECR 运行可靠性与 Token 协议优化完成方案

> 状态：已确认实施范围  
> 日期：2026-07-25  
> 基线：Phase 0 `dd6c580`、Phase 1/2 safe-shadow `57633b9`  
> 依据：`CDECR_RUNTIME_TOKEN_AUDIT_OPTIMIZATION_DELIVERY.md` 的真实 30 篇运行结果及后续逐项根因排查

## 1. 目标与范围

本方案只完成已经评估通过的第一批和第二批项目。第三批暂不实施。

本轮目标按优先级排列：

1. 先消除当前完成率的主要非业务失败：Grounder/Judge 的时间和 Evidence 校验失败、N9 非法
   merge target，以及 Wire shadow 审计 ID 冲突。
2. 再实施不增加模型注意力负担的 Wire DTO 和输出协议精简，并对 N9、N12、N13 分节点做固定
   上游 A/B。
3. 保留完整 reason 审计，不用更大复杂 Batch 换吞吐，不实施 N5.5 Snapshot 批量 Decide。
4. 每项降级只能删除无法可靠落地的辅助信息，不能伪造 Evidence、伪造 CREATE_NEW 或放松
   Atomic/Package 业务边界。

已经完成且本轮只做回归、不重复改造的项目：

- 分层 Scheduler、文档级并发、Dreamer Block 有界并发；
- 精确重复文档代表规划；
- Embedding 合批和安全同层 fan-out；
- JSON 紧凑序列化；
- provider-facing Schema 只删除生成器 `title`；
- Mention、Atomic、Package lineage、trace 与基本阶段遥测；
- N9/N12/N13 候选协议的 shadow 统计。

## 2. 不变约束

### 2.1 Prompt、Schema 与 reason

- `description` 全部保留。
- Schema 中的 `default`、`examples` 不删除。
- 已删除的生成器 `title` 保持现状。
- reason 协议不改；正常通过也继续保留模型决策原因。
- 只允许本方案第 4.1、5.2、5.3、5.5 节列出的短 Prompt 修改，其他 Prompt 不动。

### 2.2 业务边界

- Event time 不得把 `published_at` 或财务报告期当作事件发生时间。
- Evidence 最终必须映射到原文精确 Source Span；不得用模糊文本直接持久化。
- N9 只有 `SAME_EVENT` 可以合并，`UNCERTAIN` 不能被降级成 MERGE。
- N12/N13 不得把反应、后果或外部关联事件并入原始 BOUNDED Package。
- 跨文档 LLM 判断可以并行，Registry 领域状态仍按稳定顺序单 Writer 应用。

### 2.3 Batch

- Grounder、Judge、N9、N12、N13 的复杂判断 Batch 上限保持当前值。
- 不因本方案进一步下调现有 Batch。
- 第二批优化收益来自更短、更自包含的单项表示和定向 Repair，不来自扩大 Batch。

## 3. 执行顺序与 Git 边界

```text
实施前检查
  ↓
第一批：可靠性兜底、错误归因、审计冲突修复
  ↓
离线测试 + 固定样本回归 + 30 篇完整运行
  ↓
Git Commit A（第一批独立提交，第二批开始前必须完成）
  ↓
第二批：低注意力负担 Wire DTO、派生输出删除、定向 Repair
  ↓
逐节点固定上游 A/B
  ↓
30 篇完整运行 + 与第一批结果比较
  ↓
Git Commit B（第二批实现、测试和报告）
```

建议提交信息：

- Commit A：`fix(cdecr): harden grounding validation and audit identity`
- Commit B：`perf(cdecr): ship validated task-local wire protocols`

Commit A 的意义不是普通 checkpoint，而是强制隔离“可靠性提升”和“协议压缩”的因果。第二批
任一节点未通过 A/B 时，只回退该节点，不回退第一批。

## 4. 第一批：可靠性与审计修复

### 4.1 时间语义归一

#### 根因

`validate_event_time_semantics` 要求：当 `event_start`、`event_end` 都为空时，
`precision` 必须为 `UNKNOWN`。Grounder/Judge Prompt 只要求 unsupported bound 置空，却没有
告诉模型空边界与 precision 的联动规则。该严格校验在 Phase 0 前已经存在；Phase 0 并发重构
不是根因。

#### 改法

在 Grounder 输出 Adapter 和 Judge 变更应用后增加同一个确定性归一函数：

```text
if event_start is null and event_end is null:
    precision = UNKNOWN
```

要求：

- 归一发生在 Pydantic 结构解析后、业务语义校验前；
- 记录原 precision、归一后 precision 和短原因码 `NO_EVENT_BOUNDS`；
- 不改 `reference_period_id`；
- `event_end < event_start` 继续硬失败，不自动交换；
- 有任一事件边界时不自动修改 precision。

#### Prompt 修改

只在 `grounder.md` 和 `judge.md` 的时间规则后各增加同一句：

> If both event_start and event_end are null, set precision to UNKNOWN.

不加入枚举说明、时间推理手册或重复 Schema 内容。

### 4.2 Evidence 分层校验与保守降级

#### 根因

模型面对的 Evidence 是 `segment_id + text`，程序负责计算字符位置。真实失败主要来自：

- 模型给精确原文外包了一层引号；
- 同一短语在一个 Segment 中出现多次；
- 短 Attribute 文本是另一处长文本的前缀；
- Grounder 只校验 Segment 是否存在，严格 exact/unique 校验推迟到 Judge，导致错误归因为
  Judge 失败。

#### 改法

新增统一的 `EvidenceReconciler`，在 Grounder 边界先完成定位，Judge 对修改后的 Evidence
再次复用。按以下固定顺序处理：

1. 在声明的 Segment 内 exact unique；
2. 仅剥离一层成对包裹引号后 exact unique；
3. 对 Unicode 引号、dash、HTML entity 和空白做等价匹配，但最终反向映射为原文精确子串；
4. 声明 Segment 错误、但全文所有可见 Segment 中只有一个精确命中时，纠正 Segment；
5. 声明 Segment 内多命中时，使用该 Grounder Draft 的 Dreamer candidate anchor 与命中区间
   overlap 选唯一位置；
6. Judge 修改后的 Evidence 无法落地、而 Judge 输入中的原 Evidence 有效时，只回退本次
   Evidence 修改，不回退 Judge 的其他字段修改。

每次非第 1 类定位都写短审计：

```json
{
  "resolution": "STRIP_WRAPPING_QUOTES | NORMALIZED_EQUIVALENT | SEGMENT_CORRECTED | ANCHOR_DISAMBIGUATED | JUDGE_EVIDENCE_REVERTED",
  "draft_id": "...",
  "segment_id_before": "...",
  "segment_id_after": "...",
  "source_candidate_ids": ["..."],
  "source_span_hash": "..."
}
```

审计不复制全文、完整 Segment 或完整 Prompt。

#### 可降级与不可降级

可降级：

- 单个 Open Attribute Evidence 无法唯一定位：删除该 Attribute，记录
  `AUX_ATTRIBUTE_EVIDENCE_DROPPED`；
- 多条主 Evidence 中一条失败：若仍至少有一条有效主 Evidence，删除失败项并留痕；
- `MERGE_AS_ATTRIBUTE` 的 Attribute Evidence 无效：不应用该 Attribute，保留主 Mention。

不可降级：

- Mention 唯一主 Evidence 无法定位；
- SPLIT 后任一 child 没有有效主 Evidence；
- Evidence 指向未暴露文本、跨文档文本或无法恢复为原文精确 Span。

不可降级项进入只包含失败 Draft、相关 Segment 和 candidate anchor 的定向 Repair；Repair
仍失败则该 Draft/Stage 明确失败，禁止伪造 Span。

Evidence Prompt 当前已经要求“exact text from an available document segment”，本项不修改
Grounder/Judge 的 Evidence Prompt。

### 4.3 N9 coverage 与非法 merge target

#### 根因

第二轮的 `ValueError: merge target must be an input candidate` 发生在：

- 模型已经覆盖当前 Mention 的 candidate assessments；
- 但输出的 `merge_target_event_id` 不属于当前请求允许候选；
- coverage validator 在模型 Repair 边界内，完整 action/target 语义校验却在其后，因此错误
  以裸 `ValueError` 逃逸。

这不是 Phase 0 引入的并发错误，也不是已确认的 candidate assessment 数量错误。当前审计只
保留“set mismatch”总类，无法直接区分漏项、多报、重复和非法 target。

#### 改法

第一批不改变 N9 模型输出协议，只修可靠性：

1. coverage 审计记录 `expected`、`returned`、`missing`、`extra`、`duplicates`、
   `invalid_target`，全部使用请求内 `m#`/`a#`；
2. 把以下完整语义校验移入 `models.typed(... validator=...)` 的 initial/repair 边界：
   - Mention coverage；
   - candidate assessment coverage；
   - candidate assessment 唯一；
   - MERGE target 属于当前 Mention 的候选；
   - MERGE target 被评为 `SAME_EVENT`；
   - CREATE_NEW target 为空；
3. 只允许表示级安全归一：
   - 不在输入中的 extra ID 可剔除并留痕；
   - 完全相同的重复 assessment 可去重并留痕；
   - MERGE target 非法，但恰好只有一个候选被评为 `SAME_EVENT` 时，可将 target 修正为该
     candidate 并留痕；
4. 漏项、冲突重复、零个或多个 `SAME_EVENT` 下的非法 target，进入只包含失败 Mention 和
   完整候选集的定向 Repair；
5. Repair 仍失败时抛出带路径和短 ID diff 的 `CrossDocumentPipelineError`，不得让裸
   `ValueError` 逃逸。

### 4.4 修复 `ImmutableRecordConflict`

#### 根因

`record_wire_shadow` 的 immutable audit ID 目前只包含：

```text
run_id + stage + batch_index
```

正常 N12 Package Assignment 与 reaction-member boundary repair 都可能再次以
`stage=package_assignment, batch_index=0` 写入不同 payload，于是两个不同调用被错误识别为
同一 immutable record。

#### 改法

为 shadow/model-call 审计引入稳定的 invocation context：

```text
audit identity =
run_id + stage + operation + trigger + batch_index + attempt
```

最低要求：

- `operation` 区分 normal assignment、boundary reassessment、reaction-member repair；
- `trigger` 使用稳定短枚举，不使用自由文本；
- `attempt` 区分 initial、repair、escalation；
- 相同逻辑调用重跑生成相同 ID 和相同 payload；
- 不把 payload hash 当作区分调用的主键，避免用新 ID 掩盖真正的幂等冲突；
- `subject_id` 和 payload 同步保存 invocation context，便于查询。

对已有 safe-shadow 数据不做破坏性迁移；新协议提升 audit identity/version，新运行写新格式。

### 4.5 第一批审计补齐

在不增加模型 payload 的前提下补充：

- Grounder：time normalization、Evidence resolution/drop 的输入/输出引用；
- Judge：Evidence change applied/reverted、最终 Mention ID；
- N9：model call ID、validation diff、repair/escalation cause、最终 assignment ID；
- N12/N13：normal/reassessment/repair invocation context；
- 所有降级记录原对象 hash、结果对象 hash、短原因码，不保存重复业务全文。

### 4.6 第一批验证与提交门槛

离线测试至少覆盖：

- null/null time 的 precision 归一，以及有界时间不被误改；
- `event_end < event_start` 仍失败；
- 包裹引号、Unicode 等价、错误 Segment、重复短语 + candidate anchor；
- 辅助 Attribute 删除和唯一主 Evidence 硬失败；
- Judge Evidence 回退不覆盖其他合法 changes；
- N9 missing/extra/duplicate/invalid target 的分型与 Repair；
- normal package assignment 与 reaction repair 同 batch index 不再冲突；
- 相同 invocation 重放仍满足 immutable 幂等。

真实验证使用同一冻结 30 篇数据集，从头到尾运行并单独保存 Registry、报告和 hash。第一批
提交前必须满足：

- `time without event bounds` 最终失败数为 0；
- 所有成功 Mention 的 Evidence Source Span 有效率为 100%；
- 不再因可恢复的辅助 Evidence 丢失整篇文档；
- N9 无裸 `ValueError`；
- `ImmutableRecordConflict` 为 0；
- 成功跨文档路径幂等复跑新增模型调用为 0；
- 不新增 false merge、reaction-in-bounded-package 等业务边界违规。

达到门槛后完成 Git Commit A；未达到则停在第一批排查，不开始第二批。

## 5. 第二批：低注意力负担的协议与 Token 优化

第二批采用“任务内自包含、长 ID 不入模、模型只做不可推导判断”的原则。禁止恢复失败过的
顶层 `refs`、`atoms`、`packages` 字典和 `r#` 多层引用。

### 5.1 Wire DTO 总体结构

每个节点使用独立 DTO，不修改持久化模型：

```text
Persistence objects
  → node-specific task-local DTO
  → model output DTO
  → full semantic validation
  → orchestrator-side ID map restore
  → existing domain/persistence objects
```

模型只看到一个任务内的 `m#`、`a#`、`e#` 或 `p#`。完整 canonical ID 和持久化对象保存在
编排层 map，例如：

```text
(request_id, m1, a1) → full Mention / Atomic objects
```

这个 map 不放入 Prompt，不要求模型理解，不成为持久化合同。

### 5.2 N9：任务内候选 + 只输出 assessments

#### 输入

每个 task 自包含：

```json
{
  "mention": {"id": "m1", "proposition": "...", "identity": {}, "time": {}},
  "candidates": [
    {"id": "a1", "proposition": "...", "identity": {}, "time": {}, "retrieval": {}}
  ]
}
```

- candidate 直接内联，不经过 `atoms[a1]` 再引用；
- 删除模型不需要的持久化版本、完整 canonical ID、重复 mention ID 列表；
- 保留 event identity、claim conflict 判断、时间、证据摘要和 hard dimensions；
- recall/similarity 只作为辅助信号，保留三位小数。

#### 输出

Wire Output 只保留：

```json
{
  "mention_id": "m1",
  "candidate_assessments": [
    {
      "candidate_event_id": "a1",
      "relation": "SAME_EVENT",
      "claim_conflict": false,
      "identity_differences": []
    }
  ]
}
```

编排层推导：

- 没有 `SAME_EVENT` 或存在 `UNCERTAIN`：`CREATE_NEW`；
- 恰好一个 `SAME_EVENT`：`MERGE` 到该 candidate；
- 多个 `SAME_EVENT`：按现行业务优先级选 target：
  identity 完整度 → persisted 非 provisional → trusted identity evidence → recall score；
- `RELATED_NOT_SAME` 推导 `related_candidate_event_ids`；
- 除最终 target 外的 `SAME_EVENT` 推导 `possible_duplicate_atomic_ids`。

持久化 `AtomicAssignmentRecord` 的 action、target、派生 ID 列表和 reason 仍完整保留。

#### Prompt 修改

在 `atomic_coreference.md` 增加：

> For each task, return one assessment for every listed candidate ID and no others.

将要求模型返回 action/target 的输出说明替换为：

> The program derives MERGE or CREATE_NEW and the merge target from your assessments; do not return action or target fields.

其他 Atomic identity、claim conflict、relation 和 target priority 业务规则保留。

### 5.3 N12：任务内 Package 候选 + 删除完整排名

#### 输入

每个 Event task 内联精简后的 Event、Seed 和候选 Package：

```json
{
  "event": {"id": "e1", "proposition": "...", "identity": {}, "time": {}},
  "seed": {},
  "candidates": [
    {"id": "p1", "kind": "BOUNDED", "anchors": [], "representative_members": []}
  ]
}
```

同一个 Package 在不同 task 中允许重复出现，以换取模型不做跨 task join。只有当实测重复成本
高且该表示先通过质量门槛后，才讨论进一步优化；本轮不做顶层 Package 字典。

#### 输出

- 每候选 assessment 和 reason 保留；
- `ranked_member_package_ids` 删除；
- 0 个 MEMBER：selected target 和 selection reason 为空；
- 1 个 MEMBER：编排层直接推导 selected target，模型无需重复输出；
- 多个 MEMBER：模型仍输出一个 selected target 和 selection reason；
- selected target 必须属于 MEMBER assessments。

#### Prompt 修改

将：

> Multiple candidates may be classified as MEMBER. Rank all MEMBER candidates and select exactly one canonical target.

替换为：

> Multiple candidates may be MEMBER. If more than one is MEMBER, select one canonical target; the program handles the single-MEMBER case.

将：

> Give one concise reason for every candidate assessment and a concise selection reason when a target is selected.

替换为：

> Give one concise reason for every candidate assessment, and a concise selection reason only when choosing among multiple MEMBER candidates.

reason 字段本身不删除、不缩短为 reason code。

### 5.4 N13：Pair-inline，不做图级字典

N13 每个判断项直接携带 left/right 两个 Package decision view：

```json
{
  "pair_id": "k1",
  "left": {"id": "p1", "kind": "...", "anchors": [], "members": []},
  "right": {"id": "p2", "kind": "...", "anchors": [], "members": []}
}
```

- 不增加 `packages` 顶层表；
- 不增加 `r#` canonical ID；
- 每个 pair 可独立理解、验证和 Repair；
- 保留现有 SAME_PACKAGE / RELATED / DIFFERENT、边界与 reason 语义；
- 先通过 Pair 级 A/B，再决定是否替换当前模型输入。

本项不修改 N13 Prompt；只做等价字段裁剪和任务内短 ID。

### 5.5 Grounder `issue_flags` 有界化

自由文本 `issue_flags` 改为有界 `issue_codes`，只进入 Grounder Batch Audit，不进入最终
Mention payload。初始枚举只覆盖编排层和当前模型能稳定识别的几类：

```text
INSUFFICIENT_EVENT_EVIDENCE
AMBIGUOUS_EVENT_BOUNDARY
AMBIGUOUS_EVENT_TIME
AMBIGUOUS_PARTICIPANT
COMPOSITE_CANDIDATE
OTHER
```

正常无问题时返回空列表。现有审计不得丢失；`OTHER` 必须配现有短 note 字段或保留原始
issue 文本的受限审计分支，避免不可解释。

Prompt 只把末句：

> Produce only Event Mention drafts and document-level issue flags.

替换为：

> Produce only Event Mention drafts and document-level issue_codes from the supplied enum.

如果 provider JSON Schema 已足够可靠地注入枚举、且固定 Grounder A/B 表明无需 Prompt 修改，
则优先不改这句话。

### 5.6 定向 Repair

按节点最小失败单元 Repair：

- Grounder/Judge：失败 Draft + 必要 Segment/candidate anchor；
- N9：失败 Mention task + 其完整候选；
- N12：失败 Event task + 其完整候选；
- N13：失败 Pair；
- 已通过 Item 保存为临时 validated result，不随失败 Item 重发。

Repair capsule 只包含：

```json
{
  "task_id": "m2",
  "allowed_ids": ["a1", "a2"],
  "invalid_output": {},
  "errors": [{"path": "...", "code": "..."}],
  "task": {}
}
```

要求：

- 错误路径和 allowed ID 必须来自程序校验；
- 不把业务判断降级成默认 CREATE_NEW/NOT_RELATED；
- Repair 后重跑完整节点语义 validator；
- initial、repair、escalation 分别审计 token、latency 和成功率。

### 5.7 选择性省略空值与默认值

不改 Schema 的 `default/examples`。仅在 node-specific input Adapter 中，对白名单字段使用
`exclude_none` 或省略可无歧义恢复的空集合。

可先试：

- 仅作诊断且为空的 retrieval signal；
- 可由候选数量推导的空辅助列表；
- 未触发的 optional conflict detail。

禁止省略：

- time precision；
- assertion state；
- Package kind / membership relation；
- Evidence；
- 任何 null 与 unknown 具有不同业务语义的字段；
- 模型需要据此判断 hard cannot-link 的字段。

每个 DTO 必须有 round-trip test：`domain → wire → restored domain` 后业务字段等价。

### 5.8 N5.5 Read/Decide/Apply 边界整理

本轮只做轻量拆分：

1. Read：冻结当前 Mention、catalog/link version 和候选；
2. Decide：保持当前逐项/既有 Batch 和顺序，不做 Snapshot 批量 LLM Decide；
3. Apply：按稳定 key 单 Writer 写入，并在写入前检查输入 version 未变化。

目的仅是缩短 Registry 写锁范围、明确 decision/apply lineage 和安全恢复边界。它不是 N5.5
批量化，也不改变 Field Coreference 业务 Prompt。

### 5.9 第二批分节点 A/B 与启用顺序

不得一次性同时打开 N9/N12/N13 新协议。顺序：

1. N9 legacy 与 task-local assessment-only；
2. N12 legacy 与 task-local no-ranking；
3. N13 legacy 与 pair-inline；
4. Grounder issue_codes；
5. 定向 Repair 与选择性空值省略。

每项使用完全相同的固定上游对象、候选集和模型配置。至少记录：

- payload bytes、input/output token；
- first-pass Schema valid；
- candidate coverage；
- repair rate 和 repair token；
- P50/P95 latency；
- relation/action/target 一致率；
- false merge、false package membership、reaction boundary；
- reason 是否完整留存。

单节点启用门槛：

- first-pass structured success 不低于 legacy；
- final success 不低于 legacy；
- candidate coverage 100%；
- 无非法/未知短 ID；
- 业务决定完全一致，或差异经人工复核确认不劣；
- initial + repair 总 token 下降，而非仅首轮 payload 下降。

未通过的节点保持 legacy input，并保留 shadow 数据；不阻塞其他节点独立验收。

### 5.10 第二批 Token 收益预期

以下比例是对应节点估计，不能相加为全流程比例：

| 项目 | 预计收益 | 主要风险 |
|---|---:|---|
| N9 task-local 字段裁剪 | Input 8%–20% | 裁掉 identity hard dimension |
| N9 删除 action/target/派生 ID | Output 15%–30% | 多 SAME target 推导顺序漂移 |
| N12 task-local 字段裁剪 | Input 10%–25% | Package representative context 不足 |
| N12 删除完整 ranked list | Output 8%–18% | 多 MEMBER 选择信息不足 |
| N13 pair-inline 字段裁剪 | Input 15%–35% | Pair view 过短造成 false merge |
| Grounder issue_codes | Output 1%–4% | 稀有问题被 OTHER 吞并 |
| 选择性空值省略 | 相关节点 Input 3%–10% | null/unknown 语义混淆 |
| 定向 Repair | Repair Input 35%–65% | Repair 缺少必要上下文 |

全流程实际收益以 30 篇完整运行中 initial + repair + escalation 总 token 为准。

### 5.11 第二批完整运行与提交门槛

所有通过单节点 A/B 的协议按顺序启用后，再跑同一冻结 30 篇完整流程，并与第一批运行比较。
第二批提交前必须满足：

- 文档和跨文档成功数不低于第一批；
- Grounder/Judge time、Evidence 已修复问题不反弹；
- N9/N12/N13 coverage 100%，无裸 `ValueError`；
- `ImmutableRecordConflict` 为 0；
- Mention/Atomic/Package Schema 与 Source Span 有效率保持 100%；
- false merge、reaction boundary 等业务违规不高于第一批；
- 成功路径幂等复跑新增模型调用为 0；
- 总 token、单位成功文档 token 和至少一个目标节点 token 明确下降；
- 任一未通过节点已经独立回退到 legacy，而不是带病提交。

完成比较报告、机器可读指标、artifact hash 和 changelog 后提交 Git Commit B。

## 6. 版本、开关与回滚

为每个第二批节点使用独立协议版本和开关：

```text
legacy
shadow
canary
on
```

建议独立键：

- `CDECR_N9_WIRE_PROTOCOL`
- `CDECR_N12_WIRE_PROTOCOL`
- `CDECR_N13_WIRE_PROTOCOL`
- `CDECR_GROUNDER_ISSUE_PROTOCOL`
- `CDECR_TARGETED_REPAIR`

Processing Key 必须包含实际影响模型输入/输出或决策结果的协议版本。回滚只切换协议，不删除
已有 Registry、Decision Audit 或 Model Call 记录。

第一批确定性 time/evidence 修复不依赖第二批 Wire 开关；如果其业务门槛未通过，应回退对应
normalizer/reconciler 版本，而不是通过关闭审计掩盖问题。

## 7. 预期修改位置

主要实现文件：

- `src/cdecr/single_document_contracts.py`
- `src/cdecr/single_document.py`
- `src/cdecr/preprocessing.py`
- `src/cdecr/cross_document_contracts.py`
- `src/cdecr/cross_document.py`
- `src/cdecr/wire.py`
- `src/cdecr/prompts/v1/grounder.md`
- `src/cdecr/prompts/v1/judge.md`
- `src/cdecr/prompts/v1/atomic_coreference.md`
- `src/cdecr/prompts/v1/package_assignment.md`

主要回归文件：

- `tests/cdecr/test_single_document_contracts.py`
- `tests/cdecr/test_single_document.py`
- `tests/cdecr/test_preprocessing.py`
- `tests/cdecr/test_cross_document_contracts.py`
- `tests/cdecr/test_cross_document.py`
- `tests/cdecr/test_wire.py`
- `tests/cdecr/test_registry_v3.py`

评估与报告：

- 复用现有冻结 30 篇 manifest 和运行脚本；
- 新增节点固定输入 A/B 制品，避免用两次随机全流程输出直接判断 Wire 等价；
- 第一批、第二批分别保存 Registry、JSON 报告、SHA256；
- 最终报告必须区分实测 token、shadow 估算和未启用项目。

## 8. 第三批：本轮明确不做

以下项目不进入本轮实现或提交：

- 顶层 `refs` / `atoms` / `packages` 字典；
- 模型可见的 `r#` canonical ID interning；
- N13 graph-wide Package dictionary；
- N5.5 Snapshot 批量 LLM Decide；
- Judge/Atomic/Package reason code 化或正常 reason 缩减；
- 动态 M2/M3 模型路由；
- 复杂 LLM 节点 Batch 扩大；
- Provider Prompt Cache 收益声明；
- Title Embedding 与 Dreamer 的投机重叠；
- 跨文档 Registry 多 Writer 或全局图重构。

第三批只有在第一、第二批均通过真实业务门槛且出现新的明确瓶颈后，才重新评估。

## 9. 最终交付物

完成本方案应产生：

1. 第一批实现与测试；
2. 第一批独立 Git commit；
3. 第一批 30 篇完整运行制品；
4. N9/N12/N13/Grounder 的固定上游逐节点 A/B；
5. 第二批通过门槛的实现与测试；
6. 第二批 30 篇完整运行制品；
7. 两批效果、成本、失败、边界和幂等性对比报告；
8. 第二批 Git commit；
9. changelog 记录；
10. 未通过项目的明确回退状态，不以 shadow 节省估算冒充已上线收益。
