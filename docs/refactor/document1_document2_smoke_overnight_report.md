# Document1 + Document2 MU smoke report

日期：2026-06-29

## Harness 边界

- ticker：`MU`
- 本轮用户更新后只跑 `MU` 3 轮。
- 成功、失败、阻塞、超时都计入轮次。
- 每轮从新的 Document1 run 开始，再 clone Document1-only state 执行 Document2。
- stop target：`PromoteExpectationToBeliefState`
- 不进入 Document3。
- 不削弱 schema validation，不恢复 marker tombstone，不写 ticker-specific sanitizer，不放宽 hard gates，不让 promotion 修改 candidate。
- 原计划中的 `SNDK` / `NVDA` 不再继续执行；原因是无 DoxAtlas 数据的 ticker 对当前 Document1+2 smoke 验证价值有限。

## 本轮入口

新增入口：

```powershell
uv run python eval/run_document1_document2_smoke.py --ticker <TICKER> --round-label <LABEL> --stop-after PromoteExpectationToBeliefState
```

远端容器内执行时使用：

```bash
docker compose run --rm -e DOXAGENT_RUN_REAL_API_TESTS=1 -e DOXAGENT_STORAGE_MODE=postgres runtime-scheduler python eval/run_document1_document2_smoke.py --ticker <TICKER> --round-label <LABEL> --stop-after PromoteExpectationToBeliefState
```

## 轮次记录

| # | ticker | label | source_run_id | execution_run_id | status | reached node | expectation_unit_count | unresolved objections | blocking delegations | root cause / notes |
|---|---|---|---|---|---|---|---:|---:|---:|---|
| 1 | MU | MU-1 | `run_51b155e18fe742bbbab93251469ead64` | `run_9a2fa07279dc43b9a9530b3d6d2f5f9b` | timeout | `ResolveObjectionsAndDelegations` | 0 | 47 | 0 | External harness timed out at 40 minutes and the remote container was stopped. Workflow itself had completed Document1, construction, detail generation, and field review; it was entering resolver with 47 open blocking objections. |
| 2 | MU | MU-2 | `run_e5197579c7f543f895992fdf7b048134` | `run_4e7034ea82224b73916caa732feda599` | blocked | `ResolveObjectionsAndDelegations` | 0 | 31 | 0 | Workflow returned `ResolveObjectionsAndDelegations/O1 exceeded workflow timeout 240 seconds.` Document1 context pack was present; 3 pending patches existed but none were promoted. |
| 3 | MU | MU-3 | `run_39dca0a7589249918fc6c7484c1ba6b7` | `run_a7a5e014958642eca2fc17476bfc4567` | blocked | `ResolveObjectionsAndDelegations` | 0 | 35 | 0 | Workflow returned `Document2FieldRepairResultOutput` schema validation errors because O1 emitted structured object values in `evidence_requests`, while the current contract requires `list[str]`. |

## 计划变更前记录

| ticker | label | source_run_id | execution_run_id | status | notes |
|---|---|---|---|---|---|
| SNDK | SNDK-1 | `run_d089267bf7f741ab8133255ac4904231` | `run_47204284a30e4f0eb293f707a0105a3d` | blocked | Document1 completed and `document1_context_pack` was present. Document2 construction blocked before shells because `doxa_get_narrative_report` prefetch failed with `Narrative research run not found`. |
| SNDK | SNDK-2 |  |  | terminated | User changed scope to MU-only after this round had started. The remote smoke container was stopped before completion and is not counted in the new MU three-round matrix. |

## 修复记录

### Document1 proposed patch leak guard

- blocker 所在节点：`BuildGlobalResearch`
- 失败类型：contract violation silently ignored
- root cause：Document1 agents 应只产 `ResearchSection`，但 workflow 没有拒绝 `proposed_patches`
- 最小修复：在 `Document1BuilderMixin` 接受 section 前 fail closed
- harness 合规性：收紧合同，不放宽 schema，不改 Document2，不改 promotion
- targeted tests：`tests/test_document1_node_contract_matrix.py`、`tests/test_document2_node_contract_matrix.py`

### Document2 narrative prefetch gap handling

- blocker 所在节点：`GenerateExpectationConstruction`
- 失败类型：tool/runtime prefetch 与 gap policy 冲突
- root cause：Document2 O1 context 要求 `doxa_get_narrative_report`，同时声明 unavailable 时应记录 DoxAtlas narrative gap 后继续；orchestration 却在 prefetch 失败时无条件 hard fail，导致真实 ticker 没有 DoxAtlas narrative run 时无法进入 construction review / resolver。
- 最小修复：失败预取写入 `tool_prefetch_failed` working memory；只有当 O1 payload 已在 `unknowns` 或 `rationale` 显式记录 DoxAtlas narrative gap 时才继续，否则仍按合同阻塞。
- harness 合规性：不削弱 Pydantic schema validation，不新增 normalizer/adapter，不造 evidence，不放宽 promotion；该修复只让既有 gap policy 生效。
- targeted tests：`tests/test_phase15_o1_a1_a2_realization.py -k "prefetch or construction_prefetch_gap"`、`tests/test_document1_node_contract_matrix.py`、`tests/test_document2_node_contract_matrix.py`、`tests/test_document2_expectation_units_smoke_script.py`

## 当前 targeted tests

```text
uv run pytest -q tests/test_phase15_o1_a1_a2_realization.py -k "prefetch or construction_prefetch_gap"
2 passed, 17 deselected, 3 warnings in 1.31s

uv run pytest -p no:cacheprovider -q tests/test_document1_node_contract_matrix.py tests/test_document2_node_contract_matrix.py
99 passed, 3 warnings in 11.11s

uv run pytest -p no:cacheprovider -q tests/test_document2_expectation_units_smoke_script.py
3 passed, 3 warnings in 1.27s
```

## 汇总

### MU 三轮结论

- 成功：0 / 3
- 到达 `PromoteExpectationToBeliefState`：0 / 3
- Document1 关键路径：3 / 3 均完成 `BuildGlobalResearch`，且 `document1_context_pack_present=true`。
- Document2 进度：3 / 3 均完成 construction、detail generation、field review；阻塞集中在 `ResolveObjectionsAndDelegations`。
- stable `expectation_unit`：0 / 3。
- unresolved blocking objections：`MU-1=47`，`MU-2=31`，`MU-3=35`。
- blocking delegations：3 轮均为 0。
- LangSmith 429：三轮均出现 tracing quota 噪声，但不影响 workflow 判断。

### Root cause 分类

1. Resolver fan-in / runtime budget：`MU-1` 和 `MU-2` 都说明 field review 后仍有 31-47 个 open blocking objections。`MU-2` 已经写入 5 个 successful `objection_resolution_result` 和 5 个 `document2_transaction_audit`，第 6 个 resolver O1 task 超过 240 秒 workflow timeout。
2. Resolver output contract gap：`MU-3` 第一轮 resolver O1 输出的 `evidence_requests` 是结构化 object，而当前 `Document2FieldRepairResultOutput.evidence_requests` 是 `list[str]`，Pydantic 正确拒绝。这个不是 schema 应放宽的问题，而是 prompt/output-contract 示例与模型约束需要进一步收紧或显式演进。
3. External harness timeout：`MU-1` 的 40 分钟超时是本地等待窗口过短导致的外部中断，不是 workflow 自行返回的 contract failure；它仍提供了有效证据：Document1/Document2 前半段可跑通，resolver 前积压 47 个 blocker。

### 后续建议

- 先针对 resolver 做局部鲁棒性修复，而不是改 Document1：三轮都证明 Document1 context pack 能进入 Document2 并支撑到 field review。
- 为 `Document2FieldRepairResultOutput.evidence_requests` 增补 node-contract matrix：object/list/dict/partial output 必须按当前 schema fail，并在 prompt 中明确只能返回字符串请求。
- 将 resolver repair task 进一步分层或引入 resumable task cursor：当前 31-47 个 blocker 仍能把单个 O1 task 推到 240 秒超时。
- 保留 deterministic revalidation gate，不要为了通过 smoke 放宽 schema、promotion 或 evidence gate。
