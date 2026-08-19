# CDECR Runtime Token 与质量回归修复方案

日期：2026-08-19  
范围：Field planned Prepare、Field item repair、N9 batch packing、Parent Induction Prompt、Grounder PRIMARY shadow 留痕  
状态：可直接实施；本文不包含真实模型验收执行

## 1. 结论与修复边界

本轮按以下五项一次性落地，但每项保留独立开关、版本和验收口径：

1. 修复 Field epoch planned batching 将 Prepare 串行化的问题；
2. 统一 Field 初次输出与单 item repair 的 validator 合同，并完整记录 repair 的 Token、时延和错误类型；
3. 关闭 `CDECR_N9_OVERLAP_BATCH_PACKING`，恢复稳定顺序下每批 3 条 Mention；
4. 仅回滚 Parent Induction 新增的边界收紧句，不恢复 Repartition，不恢复旧 Parent Resolution；
5. PRIMARY 规则继续保持 shadow，只补齐“原非法 draft → 确定性建议 → 实际 repair 结果”的可审计对照，下一轮测试后再决定是否 Apply。

本方案不修改 Dreamer、Relevance、Grounder、Judge、Field、N9、Package V3 的业务 Prompt；唯一 Prompt 变更是删除 Parent Induction 中已确认需要回滚的新增语句。不修改模型、provider、thinking、并发总上限、N9 batch size、JSON Object 模式或现有 fail-open 边界。

## 2. 当前问题与目标

最近30篇验收中：

- Field wall 从 342.16 秒升至 1,032.16 秒，其中 planned Prepare 为 859.63 秒；
- Field 主批次外另发生96次单 item repair，但这些调用未写入 `model_calls`；
- 43个 repair 再次非法后降级为 `UNRESOLVED`；
- N9 conditional Merge Recall 从87.50%降至75.79%，已触发 overlap packing 的既定回滚条件；
- Parent Induction 的边界收紧与 Atomic 过拆叠加，Parent/Package组件进一步碎片化；
- PRIMARY shadow 只能说明“可能安全”，尚不能比较建议结果与真实 repair 结果。

修复目标不是增加新的复杂业务规则，而是恢复原有并行能力、消除输入上下文变化、恢复 Prompt 语义，并让已有 repair/shadow 行为可准确计量。

## 3. P0-1：Field planned Prepare 恢复有界并发

### 3.1 不能直接把 `for` 替换为线程池

当前 `_prepare_field_groups()` 内仍存在两类共享副作用：

- `_apply_candidate_blockers()` 会直接写 `FIELD_CANDIDATE_BLOCKED` 审计；
- `FieldCoreferenceService.prepare_decision()` 为 transient candidate 临时修改共享 `_candidate_dimensions`。

若直接并发调用，会让 Registry 写入顺序和共享候选维度出现竞态。必须先把 Prepare 改为真正的纯准备阶段。

### 3.2 纯 Prepare 合同

扩充 `_PreparedFieldGroup`，使每个任务返回不可变结果：

- task ID、source、occurrence、candidate snapshot；
- deterministic match 或 `PreparedFieldDecision`；
- task-local transient entry/dimensions；
- 延迟写入的 `DecisionAuditRecord` 列表；
- prepare error code 与 prepare duration。

具体调整：

1. `_apply_candidate_blockers()` 增加“返回 kept candidates + audit records”的纯函数路径；planned Prepare 不直接调用 Registry；
2. `_recall()`/candidate compiler 显式接收 task-local transient dimensions，不再通过修改共享 `_candidate_dimensions` 传值；
3. Prepare 阶段禁止创建 canonical field、写 field link、写 task状态和写decision audit；
4. Apply 阶段按 `task_id`、`mention_id`、`field_path` 稳定排序后，统一写 deferred audits 和业务结果。

legacy 路径保持兼容，不要求同步重构其调用表面。

### 3.3 并发编排

在 `CanonicalFieldResolutionService.resolve_epoch()` 的 planned 分支中：

1. 用现有 `max_workers` 构造有界 `ThreadPoolExecutor`；
2. 每个 pending semantic group 提交一个 Prepare task；
3. future完成顺序只影响内存收集，不影响任务ID、batch packing或Apply顺序；
4. 单group Prepare失败只将该task记为失败，不取消其他任务；
5. 全部Prepare完成后，按稳定task ID展平 `FieldDecisionPlanItem`；
6. Collect、batch Decide、逐task validator、稳定Apply保持现有先后关系。

不得让并发完成顺序影响：

- short ID；
- Field batch成员；
- canonical field ID；
- field link hash；
- task/checkpoint状态；
- audit ID或写入顺序。

### 3.4 Telemetry

在现有 Field planned telemetry 中补充：

- `prepare_group_count`；
- `prepare_worker_count`；
- `prepare_max_active_count`；
- `prepare_failed_group_count`；
- `prepare_p50_ms`、`prepare_p95_ms`、`prepare_max_ms`；
- `prepared_plan_hash`；
- `deferred_audit_count`。

`planned_ms`继续代表真实钟墙，不允许用累计worker时长替代。

## 4. P0-2：Field item repair validator 与完整成本审计

### 4.1 统一 validator 合同

新增一个初次batch与item repair共用的本地validator，例如：

```text
validate_field_wire_output(
  payload,
  expected_tasks,
  candidate_ids_by_task,
  namespace_by_task,
) -> valid_outputs + item_failures + root_failure
```

校验顺序固定为：

1. root必须为object，`decisions`必须为array；
2. 每个expected task恰好出现一次，不允许missing、duplicate或unknown task；
3. `decision`必须属于 `LINK/NEW/UNRESOLVED`；
4. `LINK`只能使用该task提供的candidate short ID；
5. `NEW/UNRESOLVED`不得携带canonical ID；
6. 只有 `participant.unknown + NEW` 可以返回允许的target namespace；
7. 输出适配回完整ID后，再通过现有Pydantic和业务validator。

为避免repair把原批次局部ID误解为新批次序号，repair请求使用独立的单item wire：

- 请求内task ID固定为 `t1`；
- 原始batch task ID只放在本地上下文和审计中，不要求模型回传；
- candidate short ID在repair请求内重新稳定编号；
- repair结果仍通过同一个validator，不再走另一套额外规则。

### 4.2 错误分类

validator必须返回短错误码，不把长异常文本作为主要统计维度：

- `ROOT_NOT_OBJECT`；
- `DECISIONS_NOT_ARRAY`；
- `TASK_MISSING`；
- `TASK_DUPLICATE`；
- `TASK_UNKNOWN`；
- `DECISION_UNKNOWN`；
- `LINK_CANDIDATE_UNKNOWN`；
- `CANONICAL_ID_FORBIDDEN`；
- `TARGET_NAMESPACE_REQUIRED`；
- `TARGET_NAMESPACE_FORBIDDEN`；
- `TARGET_NAMESPACE_UNKNOWN`；
- `PYDANTIC_VALIDATION_FAILED`；
- `BUSINESS_VALIDATION_FAILED`。

错误码应附带安全的field path或task-local short ID；不得把完整文章、完整prompt或provider key写入audit。

### 4.3 repair边界

- 只repair当前非法item，不repair整批，也不重发合法item；
- 每个非法item最多一次repair；
- repair可与其他非法item并行，但继续受全局 `CDECR_ITEM_REPAIR_ACTIVE_REQUESTS` 限制；
- repair非法时只降级该item；
- provider/transport/欠费错误保持task-local exception，不得伪造为 `UNRESOLVED`；
- 现有batch root/coverage非法时最多拆分一次的边界不变。

### 4.4 `model_calls`完整记账

每一次真实 `field_coreference_item_repair` 都必须写入一条且仅一条 `model_calls`：

- `stage=field_coreference_item_repair`；
- `prompt_version`、`schema_hash`、`input_hash`；
- provider/model/tier/transport/output mode；
- input/output/cached/reasoning Token；
- latency、queue wait、attempt、batch-local task ID；
- `original_validation_error_code`；
- `repair_validation_error_code`；
- provider error code与parse diagnostics；
- 最终状态：`VALID`、`INVALID`、`PROVIDER_FAILED`。

调用成功但本地validator失败时，Token仍必须记账，不能因为业务结果非法而丢失provider usage。若底层audited client已经记录调用，Field层只补充关联audit，不得重复写第二条model call。

同步新增item结果audit：

- `FIELD_ITEM_REPAIR_SUCCEEDED`；
- `FIELD_ITEM_REPAIR_INVALID`；
- `FIELD_ITEM_REPAIR_PROVIDER_FAILED`；
- `FIELD_BATCH_ITEM_FALLBACK`继续保留。

audit只保存错误码、输入/输出hash、candidate数量、最终action和model call ID，不保存完整payload。

### 4.5 统计闭环

Field telemetry新增：

- `item_invalid_count_by_code`；
- `item_repair_request_count`；
- `item_repair_success_count`；
- `item_repair_invalid_count`；
- `item_repair_provider_failure_count`；
- `item_repair_input_tokens`、`item_repair_output_tokens`；
- `item_fallback_count_by_reason`。

要求 `item_repair_request_count` 与 `model_calls(stage=field_coreference_item_repair)`严格一致。

## 5. P0-3：关闭 N9 overlap batch packing

只关闭packing，不修改N9业务合同：

1. `CDECRSettings.n9_overlap_batch_packing`默认值改为 `false`；
2. `.env.example`改为 `CDECR_N9_OVERLAP_BATCH_PACKING=false`；
3. 正式运行配置同步设为false；
4. active路径恢复按冻结Mention稳定顺序每3条切分；
5. 保留 `_pack_mentions_by_candidate_overlap()`、telemetry和开关，供后续离线实验，不删除实现；
6. processing key继续包含packing enabled/version，避免误复用旧checkpoint；
7. 不修改candidate snapshot、short ID、batch size、Prompt、Schema和Apply逻辑。

验收中必须看到：

- `n9_overlap_batch_packing_enabled=false`；
- Mention exact coverage=100%；
- candidate exact coverage=100%；
- batch size上限仍为3；
- Apply结果不依赖请求完成顺序。

## 6. P0-4：回滚 Parent Induction 边界收紧句

从 `src/cdecr/prompts/v1/parent_occurrence_induction.md` 删除且只删除以下整段：

> Keep clearly independent reports, market occurrences, agreements, transactions, reactions, and background context in separate parents. A field difference alone does not require a split unless the evidence supports a distinct real-world boundary.

其他Parent Prompt语句、Schema、compact wire、document-local Induction、V3输入输出合同全部保持不变。

明确不回滚：

- Parent Repartition关闭；
- 已移除的旧 Parent Resolution R1/R2；
- Package V3 global clustering；
- Parent compact Wire DTO。

Prompt正文已经进入Parent task input hash，因此旧task checkpoint不会匹配。另将 Bulk stage graph/Parent semantic contract版本提升一版，使最终Parent pool或Package stage artifact也不能误复用收紧Prompt下的结果。历史记录不删除。

## 7. P1：PRIMARY shadow补全结果对照留痕

### 7.1 仍然不Apply

`CDECR_GROUNDER_PRIMARY_NORMALIZATION`继续保持 `shadow`。本轮不增加 `apply`枚举，不跳过真实repair，不改变Grounder输出，也不修改Prompt或Schema。

### 7.2 shadow建议

对于满足现有窄规则的 `PRIMARY_QUANTITY_COUNT` 非法draft，在内存中产生一个“would-be normalized draft”，仅做以下建议：

- 在没有语义选择空间且恰有一个明确匹配primary metric的quantity时，将其标为PRIMARY；
- 其余字段逐字保持；
- 任何需要拆Mention、改变metric、period、assertion、evidence或participant的case均不是safe candidate。

不持久化完整draft，只计算：

- original draft hash；
- proposed draft hash；
- source candidate ID set hash；
- semantic projection hash。

### 7.3 与真实repair结果闭环

给 invalid draft、shadow记录和item repair建立稳定关联键：

```text
run_id + grounder_batch_index + invalid_draft_index + original_draft_hash
```

repair结束后新增 `GROUNDER_PRIMARY_NORMALIZATION_SHADOW_OUTCOME` audit，记录：

- `safe_candidate`；
- `repair_status`；
- `repair_output_count`；
- `lineage_exact_match`；
- `semantic_projection_exact_match`；
- `changed_field_codes`；
- `would_have_avoided_repair`；
- `outcome_code`。

`outcome_code`限定为：

- `EQUIVALENT_TO_REPAIR`；
- `CRITICAL_FIELD_DIFFERENCE`；
- `REPAIR_SPLIT_OR_MULTI_OUTPUT`；
- `REPAIR_DROPPED_OR_PARTIAL`；
- `REPAIR_FAILED`；
- `NOT_SAFE_CANDIDATE`。

比较至少覆盖：

- source candidate lineage；
- proposition；
- predicate/action/polarity；
- participant及role；
- metric、quantity value/unit/range/role；
- fiscal period、time/session；
- assertion state；
- Evidence identity/text span；
- local package hint与relation。

只保存changed field codes和hash，不增加LLM payload，也不保存模型长reasoning。

### 7.4 下一轮Apply决策口径

下一轮测试后才允许讨论Apply，并至少满足：

- safe candidate与真实repair语义投影一致率不低于99.5%；
- predicate、participant、metric、period、assertion、Evidence、lineage出现0次差异；
- 0个repair split/multi-output被shadow错误视为可确定修复；
- 0个新增discarded draft或candidate coverage下降；
- 至少同时报告可避免repair次数、Token和墙钟，不以机械估算代替实测。

样本不足时继续shadow，不因30篇中未观察到错误而直接启用。

## 8. 版本、恢复与幂等

一次性提升以下版本材料：

- Bulk stage graph version；
- Field planned batching protocol/version；
- Field item validator/repair protocol version；
- N9 packing effective setting进入processing key；
- Parent Prompt hash/semantic contract；
- Grounder PRIMARY shadow audit version。

恢复规则：

- 旧SUCCEEDED Field task只有input hash与新协议完全一致才可复用；
- 旧Field repair调用只保留历史审计，不补造Token；
- 旧packing结果不得在packing关闭后复用；
- 旧Parent Prompt产生的proposal/artifact不得冒充新结果；
- 幂等二跑必须0新模型请求、0新link、0新Atomic/Package membership。

不新增“整篇文档失败”或“整epoch失败”的校验。所有新增校验只定位到Field item、Grounder draft或对应checkpoint。

## 9. 修改文件清单

预计修改：

- `src/cdecr/canonical_field_resolution.py`
  - Prepare纯化、有界并发、稳定收集、telemetry；
- `src/cdecr/field_coreference.py`
  - 统一wire validator、单item repair合同、model call/audit闭环；
- `src/cdecr/field_coreference_contracts.py`
  - 必要时增加内部validation result/error code DTO，不扩大模型Schema；
- `src/cdecr/config.py`
  - N9 packing默认关闭；
- `.env.example`
  - N9 packing示例值改为false；
- `src/cdecr/cross_document.py`
  - 保留packing helper，仅确保active默认走sequential与telemetry正确；
- `src/cdecr/prompts/v1/parent_occurrence_induction.md`
  - 删除指定边界收紧段；
- `src/cdecr/single_document.py`
  - PRIMARY shadow proposal/repair outcome关联与字段级hash对照；
- `src/cdecr/bulk_epoch/engine.py`
  - stage graph/version与telemetry；
- 对应 `tests/cdecr/` focused tests；
- `changelog`。

## 10. 测试要求

### 10.1 Field确定性与并发

- 使用barrier/fake delay证明至少两个Prepare task真实重叠执行；
- 完成顺序正序/倒序时 `prepared_plan_hash`、Field link、canonical ID完全一致；
- Prepare期间Registry写方法为0调用，Apply后才按稳定顺序写入；
- 单group Prepare失败不影响其他group；
- planned与legacy使用冻结candidate plan时语义结果一致；
- idempotent resume不重复Prepare Apply或repair。

### 10.2 Field validator/repair

- 覆盖全部错误码；
- 初次输出和repair使用同一validator；
- repair请求只含一个task且本地wire ID为`t1`；
- 合法item不因同批非法item被重发；
- repair success/invalid/provider failure均写且只写一条model call；
- Token、error code、model call ID与decision audit可关联；
- provider failure不产生错误UNRESOLVED；
- 非provider非法repair只降级当前item。

### 10.3 N9与Parent

- 默认配置和`.env.example`均关闭packing；
- sequential batch成员与原稳定顺序一致，batch≤3；
- Parent Prompt不再包含被删除句；
- 其他Prompt内容逐字不变；
- Repartition保持0调用；
- compact Wire DTO仍启用；
- 旧Prompt checkpoint/artifact不可复用。

### 10.4 PRIMARY shadow

- shadow绝不改变返回draft；
- safe proposal与repair单输出可正确对齐；
- split、partial、failed repair产生对应outcome；
- critical field diff能够定位到短field code；
- audit不包含完整文章、完整prompt或长reasoning。

### 10.5 静态验证

```powershell
uv run pytest tests/cdecr/test_field_coreference.py -q
uv run pytest tests/cdecr/test_bulk_epoch_v3.py tests/cdecr/test_bulk_deterministic_runtime.py -q
uv run pytest tests/cdecr/test_single_document.py tests/cdecr/test_parent_occurrence_induction.py -q
uv run pytest tests/cdecr -q
uv run ruff check src/cdecr tests/cdecr
uv run mypy --strict src/cdecr
git diff --check
```

若仓库存在与本方案无关的既有失败，必须单列，不得通过扩大修改范围消除。

## 11. 下一轮真实30篇验收门槛

下一轮需使用相同30篇、相同provider/model/Prompt（除本方案明确删除的Parent句）、相同并发与全新Registry。

### 11.1 效能与审计

- 30/30文档成功；
- Field wall不高于360秒，且全流程wall不高于18.6分钟；
- Field Prepare不再占据串行关键路径；
- Field repair真实请求数与`model_calls`完全一致；
- repair Token 100%计入总Token；
- 总Token不高于1.42M；
- 幂等二跑0新模型调用。

### 11.2 质量

- Field total与关键participant指标不得低于恢复基线超过1pp；
- candidate异常不得通过增加UNRESOLVED掩盖；
- N9 Merge Precision下降不超过1.5pp，conditional Merge Recall至少恢复至既有门槛；
- Atomic singleton比例不再显著高于基线；
- Package Pair Precision/Recall、Micron earnings组件数和fragmented Gold groups不得继续恶化；
- Parent Repartition仍为0调用。

PRIMARY只输出shadow统计与字段级对照结论，不在本轮验收前后自动切换Apply。

## 12. 回滚矩阵

| 修改项 | 单独回滚条件 | 回滚动作 |
| --- | --- | --- |
| Field并发Prepare | link/hash非确定、出现并发写或wall仍增加>5% | 临时关闭planned batching；保留repair审计修复 |
| Field统一validator | 合法旧输出被系统性拒绝或fallback增加 | 回退validator适配层；保留model call完整记账 |
| N9 packing关闭 | 不设自动恢复条件 | 保持关闭；未来只做冻结snapshot A/B |
| Parent Prompt回滚 | Precision出现明确且不可接受的系统性下降 | 仅重新评估一条更温和边界句，不恢复整段或Repartition |
| PRIMARY shadow留痕 | 审计显著增大payload或影响业务输出 | 关闭outcome audit；PRIMARY仍不Apply |

## 13. 实施顺序

1. 先落地Field纯Prepare与并发安全改造；
2. 接通Field统一validator和repair model-call审计；
3. 关闭N9 packing并更新processing version；
4. 删除Parent指定Prompt句并更新stage/version；
5. 补齐PRIMARY shadow outcome关联；
6. 完成focused tests、全量CDECR tests、Ruff、strict mypy和diff check；
7. 另行启动全新30篇真实验收；本方案实施阶段不调用真实模型。

该顺序保证先恢复确定性运行能力，再移除已失败的语义变量，最后补审计；任何单项失败均可独立关闭，不需要整体回滚。
