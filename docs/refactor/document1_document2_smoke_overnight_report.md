# Document1 + Document2 smoke overnight report

日期：2026-06-29

## Harness 边界

- ticker：`SNDK`、`NVDA`
- 每个 ticker 3 轮，总计最多 6 轮。
- 成功、失败、阻塞、超时都计入轮次。
- 每轮从新的 Document1 run 开始，再 clone Document1-only state 执行 Document2。
- stop target：`PromoteExpectationToBeliefState`
- 不进入 Document3。
- 不削弱 schema validation，不恢复 marker tombstone，不写 ticker-specific sanitizer，不放宽 hard gates，不让 promotion 修改 candidate。

## 本轮入口

新增入口：

```powershell
uv run python eval/run_document1_document2_smoke.py --ticker <TICKER> --round-label <LABEL> --stop-after PromoteExpectationToBeliefState
```

远端容器内执行时使用：

```bash
docker compose run --rm -e DOXAGENT_RUN_REAL_API_TESTS=1 -e DOXAGENT_STORAGE_MODE=postgres debug-viewer python eval/run_document1_document2_smoke.py --ticker <TICKER> --round-label <LABEL> --stop-after PromoteExpectationToBeliefState
```

## 轮次记录

| # | ticker | label | source_run_id | execution_run_id | status | reached node | expectation_unit_count | unresolved objections | blocking delegations | root cause / notes |
|---|---|---|---|---|---|---|---:|---:|---:|---|
| 1 | SNDK | pending |  |  |  |  |  |  |  |  |
| 2 | SNDK | pending |  |  |  |  |  |  |  |  |
| 3 | SNDK | pending |  |  |  |  |  |  |  |  |
| 4 | NVDA | pending |  |  |  |  |  |  |  |  |
| 5 | NVDA | pending |  |  |  |  |  |  |  |  |
| 6 | NVDA | pending |  |  |  |  |  |  |  |  |

## 修复记录

### Document1 proposed patch leak guard

- blocker 所在节点：`BuildGlobalResearch`
- 失败类型：contract violation silently ignored
- root cause：Document1 agents 应只产 `ResearchSection`，但 workflow 没有拒绝 `proposed_patches`
- 最小修复：在 `Document1BuilderMixin` 接受 section 前 fail closed
- harness 合规性：收紧合同，不放宽 schema，不改 Document2，不改 promotion
- targeted tests：`tests/test_document1_node_contract_matrix.py`、`tests/test_document2_node_contract_matrix.py`

## 当前 targeted tests

```text
tests/test_document1_node_contract_matrix.py tests/test_document2_node_contract_matrix.py tests/test_document2_expectation_units_smoke_script.py
102 passed, 3 warnings in 11.62s
```

## 汇总

待 6 轮真实 smoke 完成后更新。
