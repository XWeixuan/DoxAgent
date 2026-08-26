# O2 Event Library：当前 Prompt / Internal Skill 注入上下文

> 用途：将本项目当前 O2（Canonical Event Library Maintainer）的真实运行方式交给 ChatGPT Work，供后续讨论 Prompt / internal skill 重写与优化。
>
> 当前实现日期：2026-08-25。本文描述的是代码现状，不是未来设计。

## 1. 一句话架构

O2 是一个固定的 Codex SDK workflow node：`O2_MAINTAIN`。

同一个 O2 run 通常复用同一个 SDK thread；每个需要模型判断的阶段在该 thread 上发一个新的 `WorkerRunRequest` / turn。阶段之间的差异主要通过：

1. `task.json.stage`；
2. 阶段专用的 `skill.md`；
3. 当前阶段允许读取的 Frozen View / Known Index / Event Detail / 前序 attempt 工作路径；
4. 当前阶段的输出目录和输出要求。

初始化和增量的所有模型请求都使用：

```text
workflow_version = codex_event_library_v1
research_lane    = event_library
node             = O2_MAINTAIN
agent_role       = O2
allow_subagents  = false
max_subagents    = 0
```

生产路径是：

```text
src/doxagent/workflows/codex_event_library/remote_runner.py
```

较底层的单 attempt 骨架是：

```text
src/doxagent/workflows/codex_event_library/runner.py
src/doxagent/workflows/codex_event_library/context.py
```

## 2. 每一次 O2 request 的固定注入内容

每个 attempt 都会在以下位置准备输入：

```text
attempts/<attempt_id>/input/
```

固定注入六个文件，并要求 Agent 按 `task.json` 中的顺序读取：

| 文件 | 来源 | 作用 |
|---|---|---|
| `AGENTS.md` | `prompts/codex_v2/event_library/AGENTS.md` | O2 工作区总规则、读写边界、Package/Atomic 边界、Pending 规则 |
| `agent.md` | `prompts/codex_v2/event_library/agents/o2.md` | O2 角色职责、Occurrence→Fact 重建原则、稳定 ID 和 Reference View 规则 |
| `skill.md` | 当前阶段选择的 `prompts/codex_v2/event_library/skills/*.md` | 当前阶段的唯一专用工作说明 |
| `task.json` | 编排器生成 | 当前模式、stage、attempt、Delta 分配、前序工作路径、访问限制、失败信息 |
| `context.json` | Frozen View manifest | 当前冻结 Runtime / Delta / Known Index / Event Detail / Package / Review context 的路径和版本信息 |
| `output_schema.json` | `O2_RUN_RESULT_SCHEMA` | O2 turn 最终 JSON 返回结构 |

Worker 的外层 prompt 只做三件事：

```text
1. 告诉 Agent 这是哪个 O2 attempt；
2. 要求按 task.json 的顺序读取六个文件；
3. 要求只执行当前 stage，写 workspace 产物，并返回 output_schema.json 对应的一个 JSON 对象。
```

SDK 的 `base_instructions` 还会补充：

```text
只能在当前 run workspace 内工作；
先读取 attempt-local AGENTS.md 和 task.json；
只能使用已授权的 MCP；
禁止超过 max_subagents 的 subagent。
```

当前 O2 的 `allowed_data_tools()` 返回空集合。O2 主要依赖 Frozen View，不通过外部数据工具补充事实；搜索只在总规则允许且确实无法从 Frozen View 判定边界时作为例外手段使用。

## 3. Request、attempt、thread 的关系

这三个概念不是一回事：

```text
一个 O2 run
  ├─ 一个持久化 thread_id
  ├─ 多个阶段 request / SDK turn
  └─ 每个 request 一个 immutable attempt workspace
```

第一次 request 如果没有 `thread_id`，SDK 使用 `thread_start()`；SDK 返回的 thread ID 会保存到 maintenance run 状态。后续 request 带上该 ID，SDK 使用 `thread_resume()`。

失败后通常是：

```text
原 thread 不变
新增 o2-<stage>-retry-001 attempt
保留旧 attempt 的输入和工作产物
新 attempt 通过 prior_attempt_paths 读取前序结果
```

已完成的 phase 不会再次调用模型。新的 `run_id` 才代表新的 O2 run，通常也会创建新的 thread。

## 4. O2 turn 的统一返回 Schema

所有 O2 模型 turn 都使用同一个严格 JSON Schema：

```json
{
  "status": "BUNDLE_READY | PENDING | FAILED",
  "stage": "<EventLibraryRunStage> 或 null",
  "bundle_path": "<workspace-relative path> 或 null",
  "base_library_version": 0,
  "delta_coverage": {
    "total": 0,
    "resolved": 0,
    "pending": 0
  },
  "validation": "PASS | PARTIAL | FAIL | NOT_RUN"
}
```

约束：

- `resolved + pending == total`；
- 非最终 Bundle 阶段通常返回 `PENDING`；
- Global Reconciliation、Reference Review 或 Repair 成功生成 Bundle 时返回 `BUNDLE_READY`；
- `bundle_path` 是 workspace-relative 路径，不是绝对路径；
- O2 不直接返回完整 Event JSON 到 SDK response；完整 Event / Bundle 写入 workspace，SDK response 只返回小的状态对象。

Schema 来源：

```text
src/doxagent/workflows/codex_event_library/schema.py::O2RunResult
```

## 5. 初始化链路：哪些阶段会发 Codex request

初始化模式是：

```text
确定性 PREPARE
→ SURVEY request
→ LOCAL_RECONSTRUCTION request × N waves
→ GLOBAL_RECONCILIATION request
→ 确定性 Bundle validation
→ 必要时 BUNDLE_VALIDATE repair request
→ 确定性 Import / Publish
```

### 5.1 `PREPARE`：不发模型 request

程序负责：

- 从冻结 Runtime Snapshot 编译 DeltaBatch；
- 物化 Frozen View；
- 写入 Known Event Index、Pending Atomic、Runtime Package index、Reference Review candidates；
- 准备 attempt workspace；
- 确定 Package-aware waves（每 wave 最多 100 条，并受 token budget 限制）。

交付物是 Frozen View，不是 O2 模型产物。

### 5.2 `SURVEY`：一个 Codex request

Internal skill：

```text
skills/initialize-survey.md
```

职责：

- 阅读全部 Pending Delta Atomic；
- 阅读 Runtime Package / Delta 组织关系；
- 为每条 Delta 建立一个临时 occurrence key；
- 标出可能的跨 Package、跨 wave 碰撞；
- 标出日期、assertion state 或边界不确定性。

交付物：

```text
attempts/o2-survey/output/work/occurrence_ledger.md
attempts/o2-survey/output/work/delta_catalog.json
```

`occurrence_ledger.md` 是给人和后续 Agent 看的全局草图，例如：

```markdown
# Occurrence OCC-001

- time_anchor: 2026-08-23
- actor: ADI
- action_or_disclosure: 发布两项产品更新
- candidate_delta_ids: D1, D2, D7
- assertion_state_conflicts: none
- cross_package_duplicate_risk: medium
```

`delta_catalog.json` 是机器可读导航映射：

```json
{
  "D1": "OCC-001",
  "D2": "OCC-001",
  "D3": "OCC-002"
}
```

Survey 不得写正式 `revision_bundle/`，返回 `PENDING`。

### 5.3 `LOCAL_RECONSTRUCTION`：每个 wave 一个 Codex request

Internal skill：

```text
skills/initialize-wave.md
```

职责：

- 读取 Survey ledger/catalog；
- 读取当前分配到的 Delta 和 Package index；
- 按 occurrence anchor 重新分组，而不是机械按 Package 分组；
- 识别不同日期、不同披露阶段、不同 analyst report 是否必须拆开；
- 保留互补 Facts，只合并真正的语义重复；
- 为当前 wave 生成临时 Event / Fact ID；
- 对无法可靠判断的 Delta 给出 `KEEP_PENDING` 或 `DROP_INVALID` 建议。

交付物：

```text
attempts/o2-wave-<NNN>/output/work/drafts/T#.json
attempts/o2-wave-<NNN>/output/work/wave_index.json
```

`wave_index.json` 至少需要表达：

- 当前 wave 覆盖的 Delta；
- occurrence candidate key；
- draft 文件路径；
- unresolved Delta 的处理建议。

Wave 不得写正式 Bundle，也不得决定最终 Event 数量，返回 `PENDING`。

### 5.4 `GLOBAL_RECONCILIATION`：一个 Codex request

Internal skill：

```text
skills/initialize-reconcile.md
```

职责：

- 读取 Survey ledger；
- 读取每个 wave 的 `wave_index.json` 和 drafts；
- 重新对照完整 Pending Delta，而不是相信 wave coverage summary；
- 合并跨 wave 的同一 occurrence；
- 拆分 topic-like、跨日期、跨阶段或跨披露的错误合并；
- 确保每个 Delta 恰好被一个 Fact 消费，或恰好出现在一个 residual resolution；
- 生成完整 Event-per-file Revision Bundle；
- 将临时 `T#/TF#` ID 稳定化交给确定性 Importer。

交付物：

```text
attempts/o2-global-reconciliation/output/revision_bundle/
├─ manifest.json
├─ events/T1.json
├─ events/T2.json
├─ ...
├─ retirements.json
├─ residual_delta_resolutions.jsonl
└─ reference_review_decisions.jsonl（需要时）
```

返回 `BUNDLE_READY` 和 Bundle workspace-relative 路径。

### 5.5 `BUNDLE_VALIDATE`：通常不发模型 request，失败时才发 Repair request

程序先执行确定性读取和校验。如果结果是 `PASS` 或可发布的 `PARTIAL`，直接进入 Import / Publish。

只有无法发布的 Bundle-level error 才会创建：

```text
o2-repair-001
```

Internal skill：

```text
skills/revision-bundle.md
```

Repair request 会注入：

- 原 Bundle；
- Frozen View；
- 前一阶段输出路径；
- `task.json.previous_failure` 中的确定性错误列表。

Repair 只能复制一个修正后的完整 Bundle 到新的 attempt，不得发布、改 SQLite 或重写无关的有效 Event。

## 6. 增量链路：哪些阶段会发 Codex request

增量模式是：

```text
确定性 PREPARE_DELTA
→ READ_FULL_INDEX（确定性前置状态）
→ BUILD_CANDIDATE_MAP request
→ LOAD_EVENT_DETAILS（确定性前置状态）
→ RECONSTRUCT_AND_EDIT request
→ REFERENCE_REVIEW request
→ 确定性 Bundle validation
→ 必要时 BUNDLE_VALIDATE repair request
→ Import / Publish
```

### 6.1 `PREPARE_DELTA`：不发模型 request

程序生成新的 DeltaBatch 和 Frozen View，并保存：

- 完整 Known Event Index；
- Event Detail 目录；
- 本批 Pending Delta；
- Runtime Package index；
- Reference Review candidates。

### 6.2 `READ_FULL_INDEX`：不单独发模型 request

这是编排状态，要求下一步必须以完整 Known Event Index 为输入。

当前没有独立的 `read-index` SDK request；它是 `BUILD_CANDIDATE_MAP` 前的确定性阶段标记和访问约束。

### 6.3 `BUILD_CANDIDATE_MAP`：一个 Codex request

Internal skill：

```text
skills/incremental-index-map.md
```

职责：

- 完整读取 `known_event_index.md`；
- 为每条 Delta 找可能的已有 Event；
- 只能使用稳定 Event ID；
- 禁止读取 Event Detail；
- 不修改 Canonical Event。

交付物：

```text
attempts/o2-known-index-map/output/work/<candidate-map artifact>
```

当前 candidate map 的内容契约由 skill 文字规定，尚未有独立的 Pydantic/JSON Schema。后续 Prompt/skill 优化时，这是一个需要特别确认的边界。

### 6.4 `LOAD_EVENT_DETAILS`：不单独发模型 request

这是编排状态。程序依据 candidate map 准备完整 Event Detail，之后把可用的 Detail 路径交给 `RECONSTRUCT_AND_EDIT`。

### 6.5 `RECONSTRUCT_AND_EDIT`：一个 Codex request

Internal skill：

```text
skills/incremental-edit.md
```

职责：

- 读取 candidate map；
- 打开每个选中稳定 Event 的完整 Detail；
- 判断 Delta 是新增 Event、修改已有 Event、合并、拆分、重复或 Pending；
- 只写受影响 Event 的完整 revision；
- 不把未受影响 Event 放入 Bundle；
- 保持稳定 Event / Fact ID；
- 保持已有非空 `price_analysis`，新 Event 的 `price_analysis` 必须为空。

交付物：

```text
attempts/o2-incremental-edit/output/work/
```

以及后续 Reference Review 所需的完整 affected Event revision。

### 6.6 `REFERENCE_REVIEW`：一个 Codex request

Internal skill：

```text
skills/incremental-reference-review.md
```

职责：

- 判断受影响 Event 是否重要；
- 判断是否进入 Reference View；
- 对显式候选读取完整 Event Detail；
- 对隐式候选先看 review index，再决定是否打开 Detail；
- 应用冻结的 10/30/7 日规则；
- 记录 `TIME_UNRESOLVED` 等明确原因；
- 生成最终增量 Bundle。

交付物：

```text
attempts/o2-reference-review/output/revision_bundle/
```

Review-only 增量可以没有 Event revision，也不能凭空创建空的 V+1。

## 7. Final Revision Bundle 的 Schema

Bundle 使用 Event-per-file 结构。根目录 manifest 的核心结构是：

```json
{
  "contract_version": "event-library-foundation-v1",
  "run_id": "o2-...",
  "ticker": "ADI",
  "base_library_version": 0,
  "delta_batch_ids": ["delta:..."],
  "event_revisions": ["events/T1.json", "events/T2.json"]
}
```

每个 `events/T#.json` 或稳定 Event revision 的核心结构是：

```json
{
  "event_id": "T1",
  "ticker": "ADI",
  "title": "...",
  "event_type": "COMPANY_DISCLOSURE",
  "occurred_at": "2026-08-23",
  "occurrence_time_precision": "DAY",
  "status": "ACTIVE",
  "canonical_summary": "...",
  "known_event_summary": "...",
  "is_important": true,
  "include_in_reference_view": true,
  "related_event_ids": [],
  "supersedes_event_id": null,
  "derived_from_event_ids": [],
  "price_analysis": null,
  "facts": [
    {
      "fact_id": "TF1",
      "proposition": "...",
      "assertion_state": "ACTUAL",
      "subject_time": "2026-08-23",
      "consumes_delta_ids": ["D1", "D2"]
    }
  ]
}
```

Fact 必须是最小独立命题。`consumes_delta_ids` 是 Revision 阶段的 Delta 覆盖绑定；发布后会被去掉，Canonical Fact 不携带 Runtime / Source / reasoning 信息。

Residual 文件每行一个 Delta：

```json
{"delta_id":"D7","resolution":"KEEP_PENDING"}
```

或重复 Fact：

```json
{
  "delta_id": "D8",
  "resolution": "DUPLICATE_FACT",
  "target_event_id": "E3",
  "target_fact_id": "F9"
}
```

`DROP_INVALID` 和 `KEEP_PENDING` 不得携带 target；`DUPLICATE_FACT` 必须携带目标 Event/Fact。

Reference review 文件每行类似：

```json
{
  "event_id": "E3",
  "reviewed_at": "2026-08-24T00:00:00Z",
  "review_mode": "IMPLICIT",
  "candidate_reason": "PERIODIC_10D",
  "changed": false,
  "include_in_reference_view": true,
  "next_review_at": "2026-08-31T00:00:00Z",
  "note": null
}
```

## 8. 当前实现中“有严格 Schema”和“只有 skill 约定”的区别

当前严格验证的主要是：

- `WorkerRunRequest`；
- O2 turn 的 `O2RunResult`；
- Frozen View manifest；
- DeltaBatch / DeltaItem / RuntimePackageDelta；
- Revision Bundle manifest；
- Event / Fact Revision；
- residual resolution；
- reference review decision；
- 最终 Validator / Importer / Published View。

当前中间工作产物主要由 skill 约束，尚没有独立强类型 Schema 的包括：

- `occurrence_ledger.md`；
- `delta_catalog.json`；
- `wave_index.json`；
- incremental candidate map；
- drafts 下的中间 Event 文件。

这意味着后续 Prompt/internal skill 重写时，需要明确讨论：

1. 哪些中间产物需要提升为正式 Schema；
2. 哪些只保留为 Agent 工作笔记；
3. 哪些错误由 Agent 自己修复，哪些交给确定性 Validator 局部降级；
4. 是否继续让所有阶段共享一个 `O2RunResult`，还是为不同阶段增加更窄的输出 Schema。

## 9. 相关文件索引

### 编排和 SDK

- `src/doxagent/workflows/codex_event_library/remote_runner.py`
- `src/doxagent/workflows/codex_event_library/runner.py`
- `src/doxagent/workflows/codex_event_library/context.py`
- `src/doxagent/workflows/codex_event_library/schema.py`
- `src/doxagent/codex_worker/sdk_runtime.py`
- `src/doxagent/codex_worker/schema.py`

### 固定 Prompt 和 Internal Skill

- `prompts/codex_v2/event_library/AGENTS.md`
- `prompts/codex_v2/event_library/agents/o2.md`
- `prompts/codex_v2/event_library/skills/foundation.md`
- `prompts/codex_v2/event_library/skills/initialize-survey.md`
- `prompts/codex_v2/event_library/skills/initialize-wave.md`
- `prompts/codex_v2/event_library/skills/initialize-reconcile.md`
- `prompts/codex_v2/event_library/skills/incremental-index-map.md`
- `prompts/codex_v2/event_library/skills/incremental-edit.md`
- `prompts/codex_v2/event_library/skills/incremental-reference-review.md`
- `prompts/codex_v2/event_library/skills/revision-bundle.md`

### 最终契约和校验

- `src/doxagent/event_library/contracts.py`
- `src/doxagent/event_library/bundle_io.py`
- `src/doxagent/event_library/validator.py`
- `src/doxagent/event_library/importer.py`

## 10. 给 ChatGPT Work 的真实文件与 Runtime 证据补充

本节是对本文前面架构描述的实物补充。路径、数量和 SHA-256 来自 2026-08-25 对真实 ADI O2 run 的只读查询；测试 fixture 会单独标明，不能当成真实模型产物。

### 10.1 当前 Prompt / Internal Skill 完整目录

当前目录只有以下 10 个文件，没有隐藏的第二套 O2 Prompt：

```text
prompts/codex_v2/event_library/
├── AGENTS.md
├── agents/
│   └── o2.md
└── skills/
    ├── foundation.md
    ├── incremental-edit.md
    ├── incremental-index-map.md
    ├── incremental-reference-review.md
    ├── initialize-reconcile.md
    ├── initialize-survey.md
    ├── initialize-wave.md
    └── revision-bundle.md
```

文件大小和 SHA-256（用于 Work 讨论时确认版本）如下：

| 文件 | bytes | SHA-256 |
|---|---:|---|
| `AGENTS.md` | 1967 | `cca7c7f204dfaafcdc029e162c8859e887dfd4109bedbee97e4a9c0e911cfa58` |
| `agents/o2.md` | 1517 | `ac8487d0c401b15338878f14e8d4cb0c686537512f17f7bd53aece745718e807` |
| `skills/foundation.md` | 887 | `e8feb7f5f262fa8595483063ba495e6eddfe816f5916ad379c6bebeb56fb5cd3` |
| `skills/initialize-survey.md` | 999 | `50d527fdb891651d9773662b6603452b8a6a03364b4d3d69d8039e011c4edb40` |
| `skills/initialize-wave.md` | 1413 | `69477b46dc22784b220b3dc9681bcbaefdc24e86858e5650ddbe34313bb87464` |
| `skills/initialize-reconcile.md` | 1949 | `b04c580bc95a0cc56e145de8f3d9142e90754a2525d9680ba7c2e5dc6a1390bc` |
| `skills/incremental-index-map.md` | 578 | `0eed8d112eb2a66843f9c528f87f5eb4ba0b22f5de1aa819b96dc1bfa4f00da4` |
| `skills/incremental-edit.md` | 463 | `a62c91ec4aa8f9ff64b14ffad1b65a6e9e4e6e088a068db7f6cd1ff3fd9255af` |
| `skills/incremental-reference-review.md` | 1003 | `375557b8336fa2145fac9688c61663d8a227670ebf915cab2d62d040d2787447` |
| `skills/revision-bundle.md` | 640 | `5465e47f517edddb1b3bf1d4d639903e0e25d9cf35471064d255fa101e60e930` |

### 10.2 真实 ADI attempt 的身份和保存位置

真实 run 的固定身份：

```text
run_id:       o2-adi-fd2c58f18d0c6226
ticker:       ADI
mode:         INITIALIZE
as_of:        2026-08-24T00:00:00Z
thread_id:    01a0357d-285f-7f80-8304-0f4f23ece230
frozen_view:  fv-67fc755498de3e877454e5d3
delta_batch:  delta:15a91622809b6403f6823ed6
```

本地只读镜像根目录：

```text
C:\Users\WEIXUANXIE\Desktop\DoxAgent\.tmp\cdecr-step2-acceptance-20260824\o2-local\o2-adi-fd2c58f18d0c6226
```

本地镜像只保存 Frozen View 和下载后的最终 Bundle。完整的 `task.json`、`context.json`、audit 和中间工作文件保留在 Codex Worker 的远程 workspace 中；本次查询时该 workspace inventory 共 195 个文件。远程相对路径都以：

```text
attempts/<attempt_id>/...
```

开头。

第一次 `o2-survey` 在模型调用前被 SDK 拒绝，原因是旧的严格 response schema 缺少必需字段 `stage`；该 attempt 的 input/context 仍保留，token usage 为 0。随后创建的真实成功 attempt 是 `o2-survey-retry-002`，没有重新采集 CDECR，也没有创建新的 Runtime Snapshot。

### 10.3 真实成功 Survey 的 task/context 样本

真实文件路径：

```text
attempts/o2-survey-retry-002/input/task.json
attempts/o2-survey-retry-002/input/context.json
```

文件指纹：

```text
task.json    4705 bytes  sha256=5807d513b69fcfcbeff356301144be4549bf489e2c63c77cc2c7057d44d2f5c1
context.json  948 bytes  sha256=b2d90c605e0c6a58f9ddc7ac5440a25b7c018adfa3879a2fa127a21226ad34b6
```

下面是 `task.json` 的关键字段投影；为了避免把 346 个 ID 再复制一遍，投影用 `assigned_delta_count` 表示原文件中的 `assigned_delta_ids` 数组。原数组完整覆盖 `D1` 到 `D346`，不是抽样：

```json
{
  "workflow": "codex_event_library_v1",
  "mode": "INITIALIZE",
  "stage": "SURVEY",
  "attempt_id": "o2-survey-retry-002",
  "frozen_view_manifest": "context/event_library/fv-67fc755498de3e877454e5d3/manifest.json",
  "assigned_delta_count": 346,
  "prior_attempt_paths": [],
  "work_path": "attempts/o2-survey-retry-002/output/work",
  "output_bundle_path": "attempts/o2-survey-retry-002/output/revision_bundle",
  "required_input_order": [
    "AGENTS.md", "agent.md", "skill.md", "task.json", "context.json", "output_schema.json"
  ]
}
```

真实 `context.json` 全文如下：

```json
{
  "contract_version": "event-library-maintenance-v2",
  "frozen_view_id": "fv-67fc755498de3e877454e5d3",
  "run_id": "o2-adi-fd2c58f18d0c6226",
  "mode": "INITIALIZE",
  "ticker": "ADI",
  "as_of": "2026-08-24T00:00:00Z",
  "base_library_version": 0,
  "delta_batch_ids": ["delta:15a91622809b6403f6823ed6"],
  "published_event_count": 0,
  "pending_delta_count": 346,
  "known_event_index_path": "known_event_index.md",
  "event_details_path": "events",
  "pending_atomics_path": "delta/pending_atomics.json",
  "runtime_hints_path": "delta/runtime_hints.json",
  "runtime_packages_path": "delta/runtime_packages.json",
  "package_index_path": "delta/package_index.md",
  "reference_review_candidates_path": "review/reference_review_candidates.json",
  "upstream_context_manifest_path": null,
  "canonical_event_schema_path": "schemas/canonical_event.schema.json",
  "revision_bundle_schema_path": "schemas/revision_bundle.schema.json"
}
```

Survey 的真实输出：

```text
attempts/o2-survey-retry-002/output/work/occurrence_ledger.md
attempts/o2-survey-retry-002/output/work/delta_catalog.json
```

其中 `occurrence_ledger.md` 为 41,818 bytes，`delta_catalog.json` 为 21,705 bytes、包含 346 个 Delta 映射。实际 ledger 同时出现了：

```text
OCC_ADI_Q3_FY2026_EARNINGS_RELEASE_AND_GUIDANCE_2026-08-19
OCC_ADI_Q2_FY2026_EARNINGS_RELEASE_AND_GUIDANCE
KEEP_PENDING_R3_ADI_STRONG_GROWTH_SCREEN
KEEP_PENDING_R10_ADI_GROWTH_MOMENTUM_SCREEN
```

这说明 Survey 不是简单按 Package 合并：同一 Package 中仍会拆出多个 occurrence candidate，也会把没有可靠 occurrence boundary 的 Delta 留在 `KEEP_PENDING_*`。

### 10.4 真实 Wave 中间产物

四个真实 Wave 都在同一个 thread 上执行。每个 Wave 都有 `input/task.json`、`input/context.json`、`output/work/wave_index.json` 和 drafts：

| attempt | assigned Delta | draft Event 数 | pending Delta 数 | 主要工作文件 |
|---|---:|---:|---:|---|
| `o2-wave-001` | 97 | 10 | 28 | `output/work/wave_index.json`, `drafts/T1.json`…`T10.json` |
| `o2-wave-002` | 61 | 7 | 50 | `output/work/wave_index.json`, `drafts/T1.json`…`T7.json` |
| `o2-wave-003` | 100 | 11 | 8 | `output/work/wave_index.json`, `drafts/T1.json`…`T11.json` |
| `o2-wave-004` | 88 | 6 | 1 | `output/work/wave_index.json`, `drafts/T1.json`…`T6.json` |

真实 Wave-001 的 `wave_index.json` 指纹是 15,166 bytes、SHA-256 `12498b2c005638941538bcab8c0fb360f3a67bba2e7f983b0a749ed5c8c2f55e`。它的 occurrence candidate 示例包括：

```text
OCC_SMCI_Q4_FY2026_EARNINGS
OCC_ADI_Q2_FY2026_EARNINGS_RELEASE_AND_GUIDANCE
OCC_NXPI_Q3_FY2026_EARNINGS_CALL
KEEP_PENDING_R10_ADI_GROWTH_MOMENTUM_SCREEN
```

Wave draft 是临时 `T#/TF#` 身份，带 `consumes_delta_ids`，不是 Published Canonical Event/Fact。Wave 只提出 `DRAFT_REVIEW` 或 `KEEP_PENDING`，最终 Event 数量由 Global Reconciliation 决定。

### 10.5 真实 Global Reconciliation 产物

真实输入：

```text
attempts/o2-global-reconciliation/input/task.json
attempts/o2-global-reconciliation/input/context.json
```

其 `prior_attempt_paths` 明确指向：

```text
attempts/o2-survey-retry-002/output/work
attempts/o2-wave-001/output/work
attempts/o2-wave-002/output/work
attempts/o2-wave-003/output/work
attempts/o2-wave-004/output/work
```

最终 Bundle：

```text
attempts/o2-global-reconciliation/output/revision_bundle/
├── manifest.json
├── events/T1.json ... events/T28.json
├── residual_delta_resolutions.jsonl
└── retirements.json
```

真实 `manifest.json` 为 870 bytes、SHA-256 `6093cef49324835c997d6b3c9ed6d0bb0ecb0250580f71584220bc2062e1e2e9`，声明 28 个 Event revision、`base_library_version=0`，并引用同一个 Delta batch。

### 10.6 Candidate map / incremental edit 的真实边界

本次 ADI run 是 `mode=INITIALIZE`，因此真实 workspace 中**没有** `o2-known-index-map`、candidate map 或 `o2-incremental-edit` 输出；这不是漏文件，而是初始化阶段本来不执行增量阶段。真实初始化链路只有 Survey → Wave → Global Reconciliation。

增量阶段的 `task.json` / `context.json` 结构有 deterministic test fixture，但不能称为真实模型产物。例如：

```text
.tmp/pytest-event-library-view-v2b/test_incremental_o2_uses_index0/
└── remote/mu-incremental-v2/attempts/
    ├── o2-known-index-map/input/{AGENTS.md,agent.md,skill.md,task.json,context.json,output_schema.json}
    ├── o2-incremental-edit/input/{AGENTS.md,agent.md,skill.md,task.json,context.json,output_schema.json}
    └── o2-reference-review/input/{AGENTS.md,agent.md,skill.md,task.json,context.json,output_schema.json}
```

当前增量真实中间产物仍待下一次有可用模型额度的 `UPDATE` run；Work 讨论 Prompt 时不要把这个 fixture 的 final Bundle 当成真实增量质量证据。

### 10.7 O2 Codex Runtime 中 Web Search 的实际暴露方式

实际边界分三层：

1. `src/doxagent/workflows/codex_event_library/tool_policy.py::allowed_data_tools()` 对 O2 返回空集合。因此 O2 没有任何 Data MCP provider 工具，尤其没有 `tavily.*` 或 `anysearch.*` namespace。
2. `src/doxagent/codex_worker/sdk_runtime.py` 仍会启动基础 Data MCP 的 `guide` / `read` 工具，并可选启动 `source_capture.capture_source`；这些是本项目 MCP，不是 Web Search。
3. Native Web Search 是 Codex SDK 自带的工具面，不属于 `allowed_data_tools()`。Worker telemetry 将 SDK 的 `webSearch` item 记录为 `web_search`；因此 Prompt 可以约束它，但 O2 的 Data MCP allowlist 不能关闭它。

O2 的实际规则是 `prompts/codex_v2/event_library/AGENTS.md` 中的：只有 Frozen View 无法判定 occurrence boundary 或 propositions 冲突时才允许搜索；搜索结果不能写入 Source 字段，也不能替代 Frozen Delta。

这次真实 ADI run 的 `agent_loop.jsonl` 中没有 `webSearch` 事件，Survey/Wave/Reconciliation 的 `mcp_call_count` 为 0；所以本次真实结果没有使用 Web Search，也没有使用 Data MCP。Worker `/v1/capabilities` 只公开 SDK、workspace、Source Capture MCP 和 Data MCP 能力，不会把 native Web Search 列为 Data MCP tool。

对 Prompt/internal skill 重写最重要的结论是：应把 Web Search 写成“可用但默认禁用、仅用于边界冲突的例外能力”，而不是写成普通数据工具或事实来源。
