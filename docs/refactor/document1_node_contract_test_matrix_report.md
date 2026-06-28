# Document1 node contract test matrix report

日期：2026-06-29

本报告记录 Document1 fixture-driven node contract matrix 的新增覆盖。测试只使用 mock `AgentRunner` 与 fixture payload，不调用真实 LLM，不调用真实外部工具。

## 前置文件确认

| 文件 | 当前状态 | 备注 |
|---|---|---|
| `dev_plan/WORKFLOW_REVISION_PLAN.md` | 找到 | 当前工作区中为 untracked 文件；PowerShell 读取时出现编码噪声，本轮以代码和测试为准。 |
| `tests/test_document2_node_contract_matrix.py` | 找到 | 作为 Document2 matrix 参考。 |
| `docs/refactor/document2_node_contract_test_matrix_report.md` | 找到 | 当前工作区中为 untracked 文件；PowerShell 读取时出现编码噪声。 |
| Document1/2 workflow 实现 | 找到 | 重点检查 `document1/*`、`document2/legacy_*`、`initialization/*`。 |
| smoke/eval 入口 | 找到 | 既有 `eval/run_document2_expectation_units_smoke.py`，本轮新增 `eval/run_document1_document2_smoke.py`。 |

## 新增测试文件

- `tests/test_document1_node_contract_matrix.py`

## 测试命令

```powershell
$env:TMP='C:\Users\WEIXUANXIE\Desktop\DoxAgent\.tmp-uv\tmp'; $env:TEMP='C:\Users\WEIXUANXIE\Desktop\DoxAgent\.tmp-uv\tmp'; $env:UV_CACHE_DIR='C:\Users\WEIXUANXIE\Desktop\DoxAgent\.tmp-uv\cache'; $env:UV_PYTHON_INSTALL_DIR='C:\Users\WEIXUANXIE\Desktop\DoxAgent\.tmp-uv\python'; uv run pytest -p no:cacheprovider -q tests/test_document1_node_contract_matrix.py tests/test_document2_node_contract_matrix.py tests/test_document2_expectation_units_smoke_script.py
```

结果：`102 passed, 3 warnings in 11.62s`

静态检查：

```powershell
uv run ruff check eval/run_document1_document2_smoke.py src/doxagent/workflows/document1/builder.py tests/test_document1_node_contract_matrix.py
uv run python -m py_compile eval/run_document1_document2_smoke.py
```

结果：通过。

## Case matrix

| node / case | fixture 类型 | expected behavior | actual behavior | 分类 |
|---|---|---|---|---|
| `BuildGlobalResearch` canonical ResearchSection | canonical | accepted，提交 stable `GlobalResearchDocument` | accepted，进入 `ReviewGlobalResearch` | expected |
| `BuildGlobalResearch` tool-call-only section | recoverable imperfect output | workflow recovery 替换工具残片，不提交工具调用文本 | accepted，section text/summary 被 fallback recovery 替换 | expected |
| `BuildGlobalResearch` section `evidence_refs=[]` but result has evidence | recoverable imperfect output | 从 `AgentResult.evidence_refs` 补回 section evidence | accepted，section evidence 被补齐 | expected |
| `BuildGlobalResearch` section 和 result 都无 evidence | questionable recovery | 当前 workflow 生成 `agent_output` evidence fallback | accepted，但会弱化 evidence gap 可见性 | questionable |
| `BuildGlobalResearch` malformed ResearchSection payload | contract violation | schema/contract failure | blocked | expected |
| `BuildGlobalResearch` failed AgentResult | contract violation/runtime failure | blocked | blocked | expected |
| `BuildGlobalResearch` proposed patch leak | contract violation | blocked，不允许 Document1 agents 越权提交 patch | 本轮修复后 blocked | bug fixed |
| `ReviewGlobalResearch` no-op boundary | canonical no-op | 不新增 commit，不改 GlobalResearch | accepted，进入 `GenerateExpectationConstruction` | expected |
| `Document1ContextPack` canonical | canonical | 生成 compact pack，包含 evidence、recent facts、compaction metadata | accepted | expected |
| `Document1ContextPack` old/background text | freshness boundary | stale fact 进入 `stale_background_facts`，不进入 fresh catalyst | accepted | expected |
| `Document1ContextPack` missing section evidence | evidence boundary | 不编造 evidence，显式产生 known gap | accepted | expected |
| `Document1 -> Document2` construction handoff | orchestration boundary | O1 construction 获得 `document1_context_pack` 和 compact sections | accepted | expected |
| `Document1 -> Document2` detail handoff | orchestration boundary | O1 detail 获得 `document1_context_pack`，不消费 full GlobalResearch text | accepted | expected |
| `Document1 -> Document2` review handoff | orchestration boundary | reviewers 获得 role-scoped compact context | accepted | expected |
| `GenerateGlobalNarrativeReport` tool fragment | recoverable imperfect output | recovery 替换工具残片，更新 `market_narrative_report` | accepted | expected |

## 根因判断

### Fixed bug

`BuildGlobalResearch` 已由 workflow 负责组装 `GlobalResearchDocument`，各 Document1 agents 应只返回 `ResearchSection`。原实现会忽略 agent 附带的 `proposed_patches`，导致 contract violation 无声通过。
本轮在 `src/doxagent/workflows/document1/builder.py` 增加窄 guard：如果 `BuildGlobalResearch` agent result 含 `proposed_patches`，立即 fail closed。

为什么不违反 harness：

- 未放宽 Pydantic schema validation。
- 未引入 normalizer 兼容。
- 未改 Document2 hard gates / promotion / resolver。
- 未让 Document1 承担 Document2 quality gate。
- 只是拒绝越权 patch leak，收紧合同边界。

### Questionable behavior

当某个 Document1 section 和 result 都没有 evidence 时，`_ensure_global_research_section_content()` 仍会生成 `agent_output` fallback evidence，使 `GlobalResearchDocument` 能提交。这个行为有历史兼容价值，但从 Document1 evidence gap 可见性看偏弱。
本轮不修它，因为这会改变 Document1 patch acceptance 语义，可能影响真实 smoke 的 Document1 成功率。建议后续单独改为：保留最小 audit evidence，同时在 `Document1ContextPack.known_gaps` 或 GlobalResearch metadata 中显式记录 `section_missing_external_evidence`。

## 是否需要 runtime 修复

本轮已做一个窄 runtime 修复：

- `src/doxagent/workflows/document1/builder.py`
  - 拒绝 `BuildGlobalResearch` agents 的 `proposed_patches` leak。

暂不修：

- `agent_output` evidence fallback 的 gap 表达不够显式。
- `Document1ContextPack` freshness 仍是文本启发式，不是严格日期窗口裁剪。

## 后续建议

1. 给 Document1 evidence gap 增加 typed gap/audit，而不是只靠 fallback evidence。
2. 将 `Document1ContextPack` freshness 从文本 marker 逐步升级为 evidence timestamp aware。
3. 保持 Document1 matrix 和 Document2 matrix 分离，避免两个生产线的 contract case 混杂。
