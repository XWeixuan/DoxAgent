# Step 2 Acceptance Snapshot

快照日期：2026-06-26

目标：确认 `Document1ContextPack` 不是只在代码层接上，而是已经能作为 Document2 construction/detail/review 的输入底座。

## 验收命令

```powershell
uv run pytest tests/test_initialization_characterization.py::test_document1_context_freezes_o1_global_research_context_shape tests/test_initialization_characterization.py::test_document2_detail_and_review_contexts_prefer_document1_context_pack tests/test_initialization_characterization.py::test_document1_context_pack_keeps_old_background_out_of_fresh_catalysts -q
```

结果：`3 passed`。

## 快照证据

同一轮本地快照输出：

```json
{
  "global_doc_json_chars": 2633,
  "injected_context_pack_json_chars": 2766,
  "o1_has_document1_context_pack": true,
  "o1_section_has_text": false,
  "pack_catalysts": 0,
  "pack_evidence_refs": 4,
  "pack_key_variables": 0,
  "pack_recent_company_facts": 1,
  "pack_recent_drivers": 3,
  "status": "running"
}
```

## 判定

| 验收点 | 当前证据 | 结论 |
| --- | --- | --- |
| `Document1ContextPack` 可从现有 `GlobalResearchDocument` 生成 | `pack_recent_company_facts=1`, `pack_recent_drivers=3`, `pack_evidence_refs=4` | 通过 |
| Document2 construction 可直接读取 pack | O1 construction task 中 `o1_has_document1_context_pack=true` | 通过 |
| Document2 detail/review 优先携带 pack | `test_document2_detail_and_review_contexts_prefer_document1_context_pack` 覆盖 detail 与四类 review agent | 通过 |
| Document2 不直接消费完整 GlobalResearch 长文本 | O1 construction `global_research_context.sections.fundamental_report` 中 `text` 不存在 | 通过 |
| 旧事实不会被标为 fresh catalyst | `test_document1_context_pack_keeps_old_background_out_of_fresh_catalysts` 覆盖 `2023 background` 样例 | 通过 |
| token/JSON 下降 | 当前 mock fixture 的 GlobalResearch 文本过短，`injected_context_pack_json_chars` 略高于完整 document payload | 未作为本快照通过条件 |

## Caveat

这个快照证明的是“Document2 已经以 pack 作为输入底座，并且不再直接吃完整 section text”。它不是真实长文本 token benchmark。当前 synthetic fixture 的 `GlobalResearchDocument` 本身很短，因此 JSON 字符数不能代表真实 eval 文档的 token 下降幅度。

Step 3 可以继续；若后续真实 eval 显示 pack 仍偏大，应在 Document1ContextPack 内继续压缩 evidence digest 和派生 claim，而不是回退到完整 GlobalResearch 长文本。
