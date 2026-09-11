# O2 语义权威接入与 Wave Runtime Context 修复方案

> 日期：2026-09-11  
> 状态：待实施  
> 范围：DoxAgent V2 Canonical Event Library 的 O2 Bundle 接入、发布门禁、repair 触发和初始化 wave 输入投影  
> 性质：信任边界调整，不是单条 validator 规则修补

## 1. 背景与问题陈述

MU 初始化中，O2 原始最终 Bundle 已形成：

- 23 个 Event；
- 296 个 Fact；
- 399 个 Resolved Delta；
- 597 个 Pending Delta。

后续确定性 validator 对 Date Resolution Ledger 产生 216 个
`DATE_CANDIDATE_PRIORITY_VIOLATION`。12 个直接受影响 Event 被判错，随后关系依赖传播又隔离
6 个 Event，最终共隔离 18 个 Event，并把它们消费的 393 个 Delta 强制改为 Pending。发布结果因此退化为：

- 5 个 Event；
- 5 个 Fact；
- 6 个 Resolved Delta；
- 990 个 Pending Delta。

这不是 O2 未完成工作，而是 O2 已作出 Agent 语义判断后，又被确定性程序进行第二次语义裁决。
`DATE_CANDIDATE_PRIORITY_VIOLATION` 只是最先暴露的症状；当前代码中还存在日期、Reference
View、关系图、Fact 所有权、标题、Fact 数量和 UNKNOWN 日期比例等其他二次语义门禁。

本方案确立以下核心原则：

> O2 是 Event/Fact/时间/关系/重要性/Reference View/Delta disposition 的最终语义权威。
> 确定性代码只负责宽容接入、Frozen 身份与并发保护、可表示性归一化、原子落库和可观测性，
> 不得再次判断 O2 的业务结论是否正确。

## 2. 目标与非目标

### 2.1 目标

1. 移除所有会因非严重问题而拒绝、隔离、删除或改写 O2 Event/Fact 的确定性语义校验。
2. 禁止单条 Fact、ledger row 或关系问题向整个 Event、关联 Event 和 Delta coverage 传播。
3. Bundle 中局部不可表示的内容只在最小范围内归一化或跳过；其余 O2 内容继续发布。
4. 只有完全不可读取、Frozen 身份不可信、base 已过期或 SQLite 事务无法提交时硬阻断。
5. 每个初始化 wave 预生成只读 `wave_runtime_context.json`，直接呈现 Package → Atomic 层级。
6. 本次 MU 原始 O2 Bundle 在不重新调用模型的情况下恢复为 O2 原始语义结果。

### 2.2 非目标

- 不在本方案内重新判断 23 个 Event 的业务质量。
- 不用确定性规则替 O2选择 occurrence date、Event 边界、关系、importance 或 Reference View。
- 不在 wave context 中为没有 Runtime Package 的 Delta 伪造 Package。
- 不改变 CDECR Runtime Package 的业务生成逻辑；这里只优化其向 O2 的投影。
- 不修改 Published 历史版本；修复只作用于后续 Bundle 接入和显式重放。

## 3. 当前实现中的二次裁决面

### 3.1 Strict contract 提前拒绝

`src/doxagent/event_library/contracts.py` 中的 `StrictModel(extra="forbid")`、字段枚举和
model validator 会在正式 validator 运行前拒绝以下情况：

- Event/Fact ID 或引用格式不符合严格表达式；
- Event 没有 Fact；
- Event 内 Fact ID 重复；
- Event 自引用；
- `consumes_delta_ids` 重复；
- Date Ledger 状态和字段组合不满足严格关系；
- Reference basis 和 include flag 不一致；
- Bundle 中 Event、retirement 或 ledger identity 重复。

这些约束适合作为 Published 数据模型，但不适合直接充当 O2 原始产物的第一层 parser。

### 3.2 Tolerant loader 仍以整 Event 为隔离单位

`src/doxagent/event_library/bundle_io.py::RevisionBundleIO.load_tolerant()` 当前会：

- 以 `CanonicalEventRevision.model_validate()` 解析整个 Event 文件；
- 任一字段失败即隔离整个 Event；
- 从原始文本提取 D# 并强制 Pending；
- 重复 Event ID 时删除所有同 ID Event；
- ledger/residual/review 行不合法时隔离整行。

逐行容错是正确方向，但 Event 解析粒度仍过大，且后续 validator 会继续扩大影响范围。

### 3.3 Validator 进行语义重判和关系传播

`src/doxagent/event_library/validator.py` 当前包含：

- Event/Fact/retirement/relationship 语义检查；
- Delta coverage 冲突后从 Fact 中删除 `consumes_delta_ids`；
- Date Resolution Ledger 优先级、日期一致性、未来日期和精度检查；
- Reference Review 与 Event revision、ledger 的一致性检查；
- `compile_bundle_semantic_report()` 的标题、Fact 数量、UNKNOWN 日期比例等阈值；
- lifecycle/derivation/retirement relation cycle 检查；
- ERROR → Fact/Event → 关联 Event 的迭代隔离和强制 Pending。

这是本次 23 Event 退化为 5 Event 的直接机制。

### 3.4 Repair loop 将程序判断重新交给模型

`src/doxagent/workflows/codex_event_library/remote_runner.py::_validate_with_repairs()` 当前在
`outcome.publishable == false` 时最多运行两次 O2 repair。由于 publishable 同时受语义 validator
控制，确定性程序实际可以要求 Agent 推翻其已经完成的语义判断。

### 3.5 Repository 假定 coverage 已被严格校验

`src/doxagent/event_library/repository.py::_write_delta_dispositions()` 使用
`dispositions[delta_id]` 直接索引每个 Delta。只要漏一条 disposition，整个 SQLite 事务就会因
`KeyError` 回滚。这使 coverage normalization 不能简单删除，必须改造成独立于 Event/Fact
内容的机械性 mapping 归一化。

## 4. 新信任边界

### 4.1 O2 语义权威字段

以下内容以 O2 最终 Bundle 为准，确定性程序不得重判：

- Event occurrence identity、Event/Fact 边界和聚合粒度；
- `event_type` 的业务选择；
- `occurred_at`、`occurrence_time_precision`；
- `fact_occurred_at`、Fact 日期精度和 `subject_time`；
- `assertion_state`；
- Event title、summary 和 Fact proposition；
- `related_event_ids`、`derived_from_event_ids`、`supersedes_event_id`；
- `status` 和 retirement 的业务意图；
- `is_important`、`include_in_reference_view`、`reference_view_basis` 和 note；
- Delta 的 Fact consumption、duplicate、drop 和 pending 判断。

### 4.2 确定性程序所有权

程序仅拥有：

- Frozen task、Bundle、Delta batch 和 Published head 的身份核对；
- workspace 路径和不可变输入保护；
- 临时 ID 的稳定 ID 分配；
- protected/code-owned 字段保护，例如 `price_analysis`；
- O2 wire 到 Published schema 的机械归一化；
- Delta mapping 的完备写入；
- review 的运行时调度字段，如 `reviewed_at`、`next_review_at`；
- SQLite Working transaction 和 Published head 原子切换；
- diagnostics、normalization log 和 metrics。

### 4.3 唯一硬阻断条件

| 硬阻断 | 原因 | 后续动作 |
| --- | --- | --- |
| Bundle manifest 缺失或完全无法解析 | 无法建立产物身份 | 允许一次 artifact repair |
| manifest 引用越出 Bundle/workspace 根目录 | 安全边界被破坏 | 立即失败，不进入模型语义 repair |
| run/ticker/base/delta batch 身份与 Frozen Task 不一致 | 可能写入错误 ticker 或错误输入 | 立即失败 |
| Delta batch 不存在或批间 D# 歧义 | 无法建立唯一 mapping | 立即失败 |
| Published head 与 base version 不一致 | 并发写入/过期基线 | 不 repair 语义；重新冻结或显式重启 |
| 同一 run 已发布不同内容 | 幂等身份冲突 | 立即失败 |
| 所有 Event、residual 和可识别 Delta 内容均不可读取 | O2 业务产物 100% 不可用 | 允许一次 artifact repair |
| SQLite 原子事务无法提交 | Published 状态不能保证一致 | 回滚 Working transaction |

除此以外不得整 Bundle 失败。

## 5. 目标接入流程

```text
O2 raw Revision Bundle
  → strict manifest identity check
  → tolerant per-file/per-row wire decode
  → field-level representation normalization
  → independent Delta disposition normalization
  → CanonicalRevisionBundle for persistence
  → SQLite Working transaction
  → Published head switch
  → non-blocking diagnostics export
```

原来的“deterministic semantic validation”阶段取消。为兼容现有 API，可以暂时保留
`BundleValidationOutcome` 名称，但其含义调整为“import preparation outcome”。

建议兼容期状态：

- `PASS`：可完整接入；diagnostic 不影响状态。
- `PARTIAL`：至少一个原始 record 完全不可表示而被最小范围跳过，或 mapping 被机械性补 Pending。
- `FAIL`：只对应第 4.3 节硬阻断。

## 6. 逐文件实施方案

### 6.1 `contracts.py`：拆分 O2 Wire 与 Published Contract

新增宽容 wire DTO，建议放入新文件
`src/doxagent/event_library/o2_wire.py`，避免放宽 Published consumer contract：

- `O2RevisionBundleWire`
- `O2EventRevisionWire`
- `O2FactRevisionWire`
- `O2ResidualResolutionWire`
- `O2DateLedgerWire`
- `O2ReferenceDecisionWire`

Wire DTO 允许额外字段、可选字段和原始字符串枚举。转换到 Canonical model 时再做机械归一化。

不得再通过修改 `CanonicalEvent`/`CanonicalFact` 为全部可空来迁就 O2，否则会把宽容输入契约
泄漏给所有 Published consumer。

### 6.2 `bundle_io.py`：字段级恢复而非 Event 级校验

将 `load_tolerant()` 改造成：

1. manifest 仍严格校验身份和路径安全。
2. Event 文件先解析为普通 JSON object/Wire DTO。
3. 对字段逐项归一化，再构造 `CanonicalEventRevision`。
4. 只有 Event 文件完全不可解析，或没有任何可恢复 title/summary/proposition 时，才跳过该 Event。
5. 重复临时 ID 不删除全部 Event，而是按 manifest 顺序稳定重编号。
6. 重复稳定 ID 的冲突 revision 保留第一条可表示 revision，其余写入 rejected-record audit；不得影响其他 Event。
7. ledger/review 行继续逐行读取，但任何坏行都只影响该 sidecar。
8. 输出 `raw_bundle_hash`、`normalization_actions`、`rejected_records` 和 `recovered_delta_ids`。

建议新增只读审计文件：

```text
artifacts/event_library/import_diagnostics/<bundle-hash>.json
```

它不得成为发布门禁。

### 6.3 `validator.py`：改为 ingestibility normalizer

删除发布路径中的以下调用及其 ERROR 行为：

- `_validate_time_contract()`；
- `_validate_reference_contract()`；
- `_semantic_checks()`；
- `_relation_cycle()`；
- `validate()` 内的 bad-item relation closure 和迭代 quarantine。

保留或重写为纯接入检查：

- Bundle/Frozen identity；
- batch existence；
- stale base；
- prior publication idempotency；
- Delta mapping normalization；
- protected field normalization；
- Canonical persistence representability。

所有历史语义 issue code 可以暂时继续输出到 diagnostics，severity 统一改为
`OBSERVATION` 或兼容性的 `WARNING`，但不得：

- 令 `publishable=false`；
- 删除 Event/Fact；
- 删除 Fact 的 Delta consumption；
- 触发 relation closure；
- 触发 repair。

### 6.4 `quality.py`：只保留离线质量观测

`compile_bundle_semantic_report()` 可以保留用于人工回顾和指标，但必须从 importer/runner 发布路径
移除。建议：

- 将 `release_gate_passed` 废弃或固定为兼容字段；
- 所有 issue 改为 observation；
- `DUPLICATE_EVENT_TITLE`、`EXCESSIVE_EVENT_FACT_COUNT`、
  `UNKNOWN_OCCURRENCE_RATIO_HIGH` 等不再映射到 validator ERROR；
- quality report 失败本身也不得回滚已发布版本。

### 6.5 `importer.py`：导入 normalized target

`RevisionBundleImporter` 改为：

1. 调用 ingestibility normalizer；
2. 仅当 outcome 为硬 `FAIL` 时拒绝；
3. 发布 normalized target；
4. 将原始 O2 Bundle hash 和 normalization log 与 publication 绑定；
5. diagnostics 写失败不得回滚已提交的 Canonical transaction，但要写运维告警。

### 6.6 `repository.py`：让 Delta mapping 自身 fail-open

在 `_write_delta_dispositions()` 内再次提供事务内兜底：

```python
resolution, target_event, target_fact = dispositions.get(
    delta_id,
    (DeltaResolution.KEEP_PENDING.value, None, None),
)
```

但不能只做这一行；还应先建立独立 mapping plan：

- 一个 Delta 只有一个有效 Fact placement：写 `PUBLISHED_FACT`；
- 多个 Fact placement 或 Fact/residual 冲突：Event/Fact 都保留，mapping 写 `KEEP_PENDING`；
- 无 placement：写 `KEEP_PENDING`；
- `DUPLICATE_FACT` target 不可解析：mapping 写 `KEEP_PENDING`；
- unknown D#：不写 DB mapping，只记 diagnostic；
- 原始 O2 disposition 保留在 source bundle/audit 中。

禁止为解决 mapping 冲突而从 Canonical Fact 中删除 proposition、删除 Event 或传播隔离。

### 6.7 `remote_runner.py`：取消语义 repair

修改 `_validate_with_repairs()`：

- ingestible 或 partial-ingestible：直接返回；
- diagnostic/observation：直接返回；
- 单个 Event/row 不可表示：归一化后直接返回；
- 只有 Bundle 完全缺失、manifest 完全不可读取或全部正式内容不可读取时，才运行一次
  artifact repair；
- identity mismatch、stale base、path escape 不运行 O2 repair。

将 `BUNDLE_VALIDATE` stage 的含义改为 `BUNDLE_PREPARE_IMPORT`；如暂不迁移 enum，可保留旧 wire
值但更新注释、日志和 dashboard 文案。

### 6.8 `runner.py`：本地/远端行为对齐

本地 `EventLibraryAgentRunner.validate_and_promote()` 必须调用同一 ingestibility normalizer，避免：

- 本地仍严格拒绝、远端宽容；
- 本地测试误以为语义 validator 仍是 release gate；
- 同一个 Bundle 在两套 runner 得到不同 publishability。

### 6.9 Prompt/skill：移除“deterministic semantic validation follows”

更新：

- `prompts/codex_v2/event_library/AGENTS.md`
- `skills/foundation.md`
- `skills/initialize-wave.md`
- `skills/initialize-reconcile.md`
- `skills/incremental-edit.md`
- `skills/incremental-reference-review.md`
- `skills/revision-bundle.md`

要求：

- O2 仍必须认真完成日期、Reference 和关系判断；
- ledger 仍可作为 Agent 判断审计，但不再描述为程序发布门禁；
- Global Reconciliation 明确为最后一次业务语义裁决；
- `revision-bundle.md` 只处理完全缺失/不可解析的 artifact，不接受日期优先级等语义错误作为 repair scope；
- Completion 中移除“Deterministic validation follows this stage”，改为“Deterministic identity and import preparation follows”。

## 7. 机械归一化策略

以下策略必须固定、可测试、可审计，且不得依赖新的模型调用。

| O2 wire 问题 | 归一化结果 | 是否影响其他 Event |
| --- | --- | --- |
| Event ticker 与 task ticker 不同 | 使用 Frozen task ticker，记录 diagnostic | 否 |
| 未知 event type | `OTHER_CORPORATE_EVENT` | 否 |
| 未知 assertion state | `UNKNOWN` | 否 |
| title 缺失 | 依次使用 canonical summary、首个 Fact proposition | 否 |
| summary 缺失 | 使用 title；不新增外部事实 | 否 |
| occurred_at 缺失 | 优先取最早的合法 `YYYY-MM-DD` Fact 时间；若无 DAY，则取最早可比较的非空 Fact 时间；Fact 全无时间时保留 `null` + `UNKNOWN` precision | 否 |
| importance/include 缺失 | Event 保留在 Full Library；缺失值使用显式兼容默认并记录 diagnostic | 否 |
| consumes 列表重复 | 稳定去重 | 否 |
| 新 Event 带 price_analysis | 强制置 null | 否 |
| 旧 Event 改写 price_analysis | 恢复 Published base 值 | 否 |
| 临时 ID 冲突 | 稳定重编号，并更新同 Bundle 引用 | 否 |
| dangling related/derived relation | Event 保留；关系可保留为 observation 或只丢该边 | 否 |
| relation cycle | 不拒绝、不删除；consumer traversal 必须有 visited guard | 否 |
| 无效 retirement redirect | 不执行该 retirement，source 保持原状态 | 否 |
| ledger/review row 不合法 | 只丢该 sidecar row | 否 |
| Delta disposition 冲突 | Event/Fact 保留；该 D# mapping Pending | 否 |
| 单 Event 完全无可恢复内容 | 只跳过该 Event，可识别 D# Pending | 否 |

“importance/include 缺失”的默认仅用于保持 Full Library 可用，不代表程序作出了 O2 的业务判断；
diagnostics 必须明确标记字段缺失，便于后续人工或 Agent 增量修订。

## 8. Date 与 Reference Ledger 的新定位

### 8.1 Date Resolution Ledger

保留用途：

- 记录 O2 使用过的候选、选择和解释；
- 支持人工审计和未来模型增量判断；
- 统计上游 date candidate 投影质量。

禁止用途：

- 用候选优先级覆盖 O2 selected date；
- 因 row 缺失或不一致隔离 Event/Fact；
- 因未来日期、DAY 精度或 SUBJECT_TIME 规则改写 Delta disposition。

216 个 `DATE_CANDIDATE_PRIORITY_VIOLATION` 可改名为
`DATE_CANDIDATE_SELECTION_DIFFERENCE`，作为 observation 输出。

### 8.2 Reference View Decision Ledger

Event edit 场景：

- Event revision 中的 `is_important/include_in_reference_view` 为发布权威；
- ledger/review row 仅提供 basis、note 和审计信息；
- 不一致时不得隔离 Event。

Review-only 场景：

- O2 review decision 是语义权威；
- 如果 flags 改变而 O2 未输出完整 Event revision，程序从 Published Event 复制完整 revision，
  只应用 O2 明确选择的 flags；
- `reviewed_at/next_review_at/review_mode/candidate_reason/changed` 可由 frozen clock 和调度策略计算，
  但不得改写 O2 的业务判断。

## 9. `wave_runtime_context.json` 设计

### 9.1 目标

Worker 不再自行跨三个文件连接：

```text
pending_atomics.json
runtime_packages.json
package_index.md
```

Runner 在 wave 划分完成后，为每个 wave 生成一份已连接、只读、不可变的上下文。

### 9.2 路径和身份

```text
attempts/o2-wave-001/input/wave_runtime_context.json
```

在 `task.json` 增加：

```json
{
  "wave_runtime_context_path": "attempts/o2-wave-001/input/wave_runtime_context.json",
  "wave_runtime_context_sha256": "..."
}
```

该文件属于 attempt input，不写入 Frozen View。理由是 wave 划分由 runner 的 size/token 配置决定，
不应改变 Frozen View 的事实身份。

### 9.3 v1 契约

```json
{
  "contract_version": "o2-wave-runtime-context-v1",
  "frozen_view_id": "fv-...",
  "delta_batch_ids": ["..."],
  "wave_id": "o2-wave-001",
  "wave_index": 1,
  "wave_count": 16,
  "assigned_delta_ids": ["D382", "D596"],
  "planner": {
    "max_delta_count": 100,
    "estimated_token_budget": 18000
  },
  "package_groups": [
    {
      "runtime_hint_id": "R5",
      "title": "...",
      "runtime_package_version": 1,
      "full_member_delta_ids": ["D382", "D596", "D630"],
      "wave_member_delta_ids": ["D382", "D596"],
      "out_of_wave_members": [
        {"delta_id": "D630", "wave_id": "o2-wave-002"}
      ],
      "time_anchors": [],
      "subject_time_anchors": [],
      "entity_anchors": [],
      "source_message_ids": [],
      "occurrence_date_candidates": [],
      "atomics": [
        {
          "delta_id": "D382",
          "proposition": "...",
          "raw_time": null,
          "subject_time": null,
          "occurrence_date_candidates": [],
          "assertion_state": "ACTUAL",
          "entities": [],
          "runtime_hint_ids": ["R5"],
          "primary_runtime_hint_id": "R5",
          "secondary_runtime_hint_ids": [],
          "target_suggestion_ids": []
        }
      ]
    }
  ],
  "ungrouped_atomics": [],
  "coverage": {
    "assigned_delta_count": 2,
    "package_grouped_delta_count": 2,
    "ungrouped_delta_count": 0,
    "split_package_count": 1
  }
}
```

### 9.4 成员规则

1. 每个 assigned D# 在 `package_groups[].atomics` 或 `ungrouped_atomics` 中恰好出现一次。
2. 一个 Delta 属于多个 Package 时，只嵌套于 planner 选定的 primary Package。
3. Atomic 同时保留完整 `runtime_hint_ids` 和 `secondary_runtime_hint_ids`。
4. Package block 保留完整成员 ID，但只嵌入本 wave Atomic 正文。
5. Package 被 size/token budget 拆分时，列出 `out_of_wave_members` 及目标 wave。
6. 没有 Package 的 Delta 放入 `ungrouped_atomics`，不得生成虚假 R#。
7. JSON 内顺序必须稳定，以保证 resume hash 和 fixture 可复现。

### 9.5 Planner 调整

将 `_plan_waves(batch)` 从多次重复计算改为每个 run 只计算一次，输出 `WavePlan`：

```python
class WavePlan:
    wave_id: str
    delta_ids: list[str]
    estimated_tokens: int
    primary_package_ids: list[str]
    split_package_ids: list[str]
```

同时生成：

```python
delta_to_wave: dict[str, str]
primary_package_by_delta: dict[str, str | None]
```

Package 保持同 wave 的优先级高于普通排序，但仍遵守硬 size/token budget。单个 Package 超预算时允许拆分，
依靠 context 的 full member 和 cross-wave location 保留层级。

### 9.6 Attempt seeding

修改 `build_attempt_assets()` 接受可选 `wave_runtime_context`：

- LOCAL_RECONSTRUCTION stage 必须提供；
- 其他 stage 不生成；
- 文件作为 input asset 与 `task.json` 一起做 immutable resume 比较；
- `content_input_order` 的五个控制文件保持不变；
- `AGENTS.md` 和 `initialize-wave.md` 明确要求控制文件后立即读取
  `wave_runtime_context_path`。

这样不会把业务 payload 混入控制文件序列，也不会重复扩大启动 prompt。

### 9.7 当前 MU 数据的预期表现

历史本次输入中只有 12/996 Delta 带 Runtime Package membership；因此：

- 12 条会在 `package_groups` 中直接呈现 Package → Atomic；
- 984 条会明确进入各 wave 的 `ungrouped_atomics`；
- `package_coverage` 只作观测，不阻断 O2；
- 后续 CDECR Package 闭合修复产生的完整 membership 会自然得到更高覆盖，无需升级 wave schema。

## 10. 测试计划

只运行与本次变更直接相关的测试，不扩展到无关全量回归。

### 10.1 Ingestibility 单元测试

1. `DATE_CANDIDATE_PRIORITY_VIOLATION` 不改变 Event/Fact/Delta。
2. Date ledger 缺失、冲突、未来日期、Fact SAME 或精度差异均可发布。
3. Reference ledger/review mismatch 不删除 Event。
4. 重复标题、Fact >25、UNKNOWN 日期比例高不阻断。
5. relation cycle 不隔离 Event；consumer traversal 有 visited guard。
6. dangling optional relation 不影响 Event publication。
7. 单个坏 ledger row 只产生 diagnostic。
8. 单个 Event 完全不可解析只影响该 Event，不传播到相关 Event。
9. temp ID 冲突可稳定重编号。
10. price_analysis 保护不删除 Event。

### 10.2 Delta mapping 测试

1. 漏 disposition 自动 Pending。
2. 同一 D# 被两个 Fact 消费：两个 Event/Fact 都保留，mapping Pending。
3. Fact consumption 与 residual 冲突：内容保留，mapping Pending。
4. invalid duplicate target 转 Pending。
5. unknown D# 不进入 DB mapping。
6. repository 即使收到漏项也不会 `KeyError` 回滚。

### 10.3 Wave context 测试

1. assigned Delta 在 context 中恰好一次。
2. Package 双向 membership 完整。
3. 多 Package Atomic 只有一个 primary 嵌套位置。
4. 同 Package 在预算允许时保持同 wave。
5. 超预算 Package 的 cross-wave member location 正确。
6. ungrouped Delta 全部存在且不伪造 Package。
7. 同一输入多次生成 byte-identical JSON 和 SHA-256。
8. resume 时 context 不一致触发 immutable input mismatch。

### 10.4 MU 回放验收

使用 repair 前原始 Bundle 和 Frozen Delta：

| 指标 | 期望 |
| --- | ---: |
| Event | 23 |
| Fact | 296 |
| Resolved Delta | 399 |
| Pending Delta | 597 |
| Date selection observations | 216，可记录但不改写 |
| Validator 隔离 Event | 0 |
| 额外强制 Pending Delta | 0 |
| O2 semantic repair turn | 0 |

另外保留四个负例：manifest 完全损坏、Frozen identity mismatch、stale base、SQLite transaction
failure，确认它们仍硬失败且不会伪装为成功发布。

## 11. 分阶段落地

### Phase A：Fixture 与行为锁定

- 冻结 MU repair 前 Bundle、Frozen manifest 和预期 23/296/399/597 指标。
- 为现有 validator 行为增加 characterization test，证明当前会产生 18 Event 隔离。
- 新测试先以目标行为失败，避免无证据删除代码。

### Phase B：Wire/Normalizer 改造

- 增加宽容 O2 wire DTO。
- 重写 tolerant loader。
- 删除 semantic/date/reference/graph release gate。
- 引入 normalization diagnostics。

### Phase C：Importer/Repository 改造

- Delta mapping 与 Canonical Event/Fact publication 解耦。
- 增加 repository 事务内 Pending 兜底。
- review-only decision 生成最小完整 revision。
- 验证原子事务和 idempotency。

### Phase D：Runner 与 Prompt 改造

- 禁用 semantic repair。
- 本地/远端 runner 对齐。
- 更新 O2 prompt 对最终语义权威的描述。

### Phase E：Wave Runtime Context

- 引入稳定 `WavePlan`。
- 生成、hash、seed `wave_runtime_context.json`。
- 更新 wave skill 读取路径。
- 运行 package/split/resume 聚焦测试。

### Phase F：Shadow 与发布

1. 在不写 Published head 的 shadow 模式重放 MU 原始 Bundle。
2. 对比原始 O2 Bundle、normalized target 和预期指标。
3. 确认只发生机械归一化，没有 Event/Fact 语义删除。
4. 本地发布到临时 SQLite，导出 Event Library 并验证消费者读取。
5. 经人工确认后再部署远端；部署本身不自动重跑 MU 初始化。

## 12. 回滚策略

- 代码回滚不修改已发布历史版本。
- 新 importer 继续保存 raw bundle hash，允许从原始 O2 产物重新归一化。
- Wave context 是 attempt input，只影响尚未运行的 attempt；已存在 attempt 不覆盖。
- 若新 importer 出现 SQLite 写入问题，Working transaction 回滚，Published head 保持不变。
- 不允许回滚到“语义 validator 删除 Event 后继续发布”的行为；紧急降级应改为保持上一 Published head，
  而不是再次二次裁决 O2。

## 13. 完成定义

以下全部满足才算修复完成：

- 发布路径不存在确定性日期、Reference、关系或质量语义门禁。
- 不存在 error relation closure 或 Event 级连隔离。
- O2 repair 只由 100% artifact 不可用触发。
- 局部格式问题可归一化或最小隔离，其他 Event/Fact 保留。
- Delta mapping 漏项/冲突不会删除 Canonical 内容或回滚整批。
- MU 原始 Bundle 回放得到 23 Event、296 Fact、399 Resolved、597 Pending。
- 每个初始化 wave 都有 immutable `wave_runtime_context.json`。
- Worker 可在单文件中直接读取已有 Package → Atomic 层级及跨-wave位置。
- Frozen identity、stale base、路径安全和 SQLite transaction 硬门禁仍有效。
- 聚焦测试、Ruff 和涉及文件的静态类型检查通过。
- `changelog` 记录实现范围、验证范围以及是否完成远端/真实模型验收。
