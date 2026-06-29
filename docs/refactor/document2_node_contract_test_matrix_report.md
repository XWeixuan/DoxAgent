# Document2 node contract test matrix report

日期：2026-06-28

本报告记录 Document2 fixture-driven node contract matrix 的第二批覆盖以及本轮 runtime 边界修复结果。测试只使用 mock `AgentRunner` 和 fixture payload，不调用真实 LLM，不调用真实外部工具；本轮未运行真实 smoke，未修改 Document3。

## 审查建议合理性评估

| 建议 | 评估 | 本轮处理 |
|---|---|---|
| resolver 对 non-numeric deterministic blockers 做 post-resolution revalidation | 合理。numeric sanity 已停用，unknown price、missing evidence refs、empty facts/variables/events、placeholder 等仍需要防止被 O1 无 revision 关闭 | 已修：resolver 关闭 objection 前重跑 non-numeric deterministic findings；同一 finding key 仍存在则 retained blocker |
| promotion fail-closed，但不要恢复旧的完整 detail quality validator | 合理，但必须收窄。promotion 不是 review 节点，只做只读防线 | 已修：promotion 重跑现有 deterministic finding 生成器，发现隐藏 blocker 时写 rejected audit 并阻断 |
| `author_agent=SYSTEM` patch 不能走普通 registry，也不能把 SYSTEM 注册成全能 agent | 合理。扩大 SYSTEM 权限风险太大 | 已修：仅在 Document2 promotion submit 内给 SYSTEM 一个极窄权限，只能写 `expectation_unit` |
| finding lifecycle 对 missing source / unresolved source / resolved-but-unfixed source fail-closed | 合理，但 resolved source 需要区分 candidate 是否真的修复 | 已修：missing source 和 unresolved source 视为 active；resolved source 只有在当前 candidate 不再出现同一 deterministic finding key 时才 inactive |
| proposed_patches leak 用统一 helper 处理 | 合理 | 已修：construction review、construction resolver、detail generation、field review、objection resolver 统一拒绝 forbidden `proposed_patches` |
| detail candidate delegations 短期 reject | 合理。当前没有 detail delegation lifecycle，忽略比拒绝更危险 | 已修：`ExpectationDetailCandidateResult.delegations` 非空时阻断 |
| final_payload_adapter 收窄 normalization，不发明语义内容 | 合理，但不应完全禁用结构性 adapter | 已修：保留完整 candidate-like dict 的结构归一化；拒绝 list/multi/patch/path-map/proposed_patches/partial semantic candidate，不再补 `unknown` price reaction、默认 facts/variables/events |

## 测试命令

```powershell
$env:TMP='C:\Users\WEIXUANXIE\Desktop\DoxAgent\.tmp-uv\tmp'; $env:TEMP='C:\Users\WEIXUANXIE\Desktop\DoxAgent\.tmp-uv\tmp'; $env:UV_CACHE_DIR='C:\Users\WEIXUANXIE\Desktop\DoxAgent\.tmp-uv\cache'; $env:UV_PYTHON_INSTALL_DIR='C:\Users\WEIXUANXIE\Desktop\DoxAgent\.tmp-uv\python'; uv run pytest -p no:cacheprovider tests\test_document2_node_contract_matrix.py
```

结果：`81 passed, 3 warnings in 9.64s`。

```powershell
$env:TMP='C:\Users\WEIXUANXIE\Desktop\DoxAgent\.tmp-uv\tmp'; $env:TEMP='C:\Users\WEIXUANXIE\Desktop\DoxAgent\.tmp-uv\tmp'; $env:UV_CACHE_DIR='C:\Users\WEIXUANXIE\Desktop\DoxAgent\.tmp-uv\cache'; $env:UV_PYTHON_INSTALL_DIR='C:\Users\WEIXUANXIE\Desktop\DoxAgent\.tmp-uv\python'; uv run pytest -p no:cacheprovider tests\test_document2_canonical_contracts.py tests\test_document2_expectation_units_smoke_script.py
```

结果：`13 passed, 3 warnings in 1.34s`。

## 更新文件列表

- `tests/test_document2_node_contract_matrix.py`
- `src/doxagent/workflows/document2/legacy_pipeline.py`
- `src/doxagent/workflows/document2/legacy_quality.py`
- `src/doxagent/workflows/document2/legacy_promotion.py`
- `src/doxagent/workflows/document2/final_payload_adapter.py`
- `docs/refactor/document2_node_contract_test_matrix_report.md`
- `changelog`

## Node case matrix

| node / case | fixture 类型 | expected behavior | actual behavior | result |
|---|---|---|---|---|
| `GenerateExpectationConstruction` canonical / one-shell | canonical / recoverable | accepted | accepted，进入 construction review | expected |
| `GenerateExpectationConstruction` missing evidence / too many shells | contract violation | schema/contract failure | blocked | expected |
| `ReviewExpectationConstruction` blocking objection | recoverable | bridged objection | unresolved objection 创建 | expected |
| `ReviewExpectationConstruction` reviewer `proposed_patches` leak | contract violation | schema failure | blocked by unified proposed_patches guard | expected |
| `ResolveExpectationConstruction` fixes market_view / expectation_name / direction | canonical resolver output | transaction accepted | objection closed，进入 detail generation | expected |
| `ResolveExpectationConstruction` changed id / empty revision / unrelated objection | contract violation | transaction rejected | blocked | expected |
| `GenerateExpectationDetails` good candidate | canonical | accepted | pending `Document2Revision` + legacy pending patch produced | expected |
| `GenerateExpectationDetails` unknown price / missing evidence / generic trigger | recoverable imperfect | accepted for review, not hard fail | accepted，后续 review 产生 typed finding | expected |
| `GenerateExpectationDetails` changed id/name/direction | contract violation | schema failure | blocked | expected |
| `GenerateExpectationDetails` O1 `proposed_patches` leak | contract violation | schema failure | blocked by unified proposed_patches guard | expected |
| `GenerateExpectationDetails` candidate wrapper missing/malformed | contract violation | schema failure | blocked | expected |
| `GenerateExpectationDetails` detail delegations | contract violation for current workflow | explicit reject | blocked with delegation-specific contract error | expected |
| `ReviewExpectationFields` structured reviewer finding | recoverable | typed finding + bridged objection | finding stored and source objection created | expected |
| `ReviewExpectationFields` placeholder / unknown price / evidence gaps / empty fields / generic trigger | recoverable | deterministic typed finding + bridged objection | finding stored and source objection created | expected |
| `ReviewExpectationFields` numeric sanity | disabled | no numeric finding / no SYSTEM objection | not routed to resolver | expected |
| `ReviewExpectationFields` reviewer `proposed_patches` leak | contract violation | schema failure | blocked by unified proposed_patches guard | expected |
| `ResolveObjectionsAndDelegations` resolved without changed_paths/evidence_refs | contract violation | transaction rejected | blocked | expected |
| `ResolveObjectionsAndDelegations` accepted without revised_candidate | contract violation | transaction rejected | blocked | expected |
| `ResolveObjectionsAndDelegations` revised_candidate still has deterministic blocker | recoverable but unresolved | retained blocker | blocked | expected |
| `ResolveObjectionsAndDelegations` deferred blocker | recoverable but unresolved | retained blocker | blocked | expected |
| `ResolveObjectionsAndDelegations` changed identity | contract violation | schema failure | blocked | expected |
| `ResolveObjectionsAndDelegations` numeric sanity | disabled | no repair task | no resolver fan-in from numeric_sanity | expected |
| `ResolveObjectionsAndDelegations` non-numeric deterministic blocker still fails | recoverable but unresolved | retained blocker | blocked after deterministic revalidation | expected |
| `PromoteExpectationToBeliefState` no active blocker | canonical | promotion accepted | accepted，进入 `GenerateGlobalNarrativeReport` | expected |
| `PromoteExpectationToBeliefState` active blocking finding | recoverable but unresolved | promotion blocked | blocked | expected |
| `PromoteExpectationToBeliefState` unresolved objection | recoverable but unresolved | promotion blocked | blocked | expected |
| `PromoteExpectationToBeliefState` candidate differs from source patch | contract violation | rejected | rejected by read-only promotion boundary | expected |
| `PromoteExpectationToBeliefState` promotion mutation attempt | contract violation | rejected | blocked | expected |
| `PromoteExpectationToBeliefState` hidden unknown price / placeholder issue | orchestration invariant violation | promotion blocked + rejected audit | blocked; rejected promotion audit recorded | expected |

## Mini-flow matrix

### GenerateExpectationDetails -> ReviewExpectationFields

| case name | fixture 类型 | expected behavior | actual behavior | result |
|---|---|---|---|---|
| `MiniFlow_DetailToReview__unknown_price_reaction__typed_finding` | recoverable imperfect | typed finding + bridged objection | found `realized_facts[0].price_reaction` | expected |
| `MiniFlow_DetailToReview__missing_realized_fact_evidence_refs__typed_finding` | recoverable imperfect | typed finding + bridged objection | found `realized_facts[0].evidence_refs` | expected |
| `MiniFlow_DetailToReview__missing_key_variable_evidence_refs__typed_finding` | recoverable imperfect | typed finding + bridged objection | found `key_variables[0].evidence_refs` | expected |
| `MiniFlow_DetailToReview__empty_realized_facts__typed_finding` | recoverable imperfect | typed finding + bridged objection | found `realized_facts` | expected |
| `MiniFlow_DetailToReview__empty_key_variables__typed_finding` | recoverable imperfect | typed finding + bridged objection | found `key_variables` | expected |
| `MiniFlow_DetailToReview__empty_positive_events__typed_finding` | recoverable imperfect | typed finding + bridged objection | found `event_monitoring_direction.positive_events` | expected |
| `MiniFlow_DetailToReview__empty_negative_events__typed_finding` | recoverable imperfect | typed finding + bridged objection | found `event_monitoring_direction.negative_events` | expected |
| `MiniFlow_DetailToReview__generic_monitoring_trigger__typed_finding` | recoverable imperfect | typed finding + bridged objection | found generic positive trigger | expected |
| `MiniFlow_DetailToReview__placeholder_generic_text__typed_finding` | recoverable imperfect | typed finding + bridged objection | found `market_view.text` placeholder | expected |
| `MiniFlow_DetailToReview__numeric_sanity__disabled` | disabled | no numeric sanity finding / objection | no numeric sanity target | expected |

### ReviewExpectationFields -> ResolveObjectionsAndDelegations

| case name | fixture 类型 | expected behavior | actual behavior | result |
|---|---|---|---|---|
| unknown `price_reaction` | recoverable imperfect | retained blocker unless revised candidate fixes it | retained blocker | expected |
| missing realized_fact `evidence_refs` | recoverable imperfect | retained blocker unless evidence-backed revision fixes it | retained blocker | expected |
| missing key_variable `evidence_refs` | recoverable imperfect | retained blocker unless evidence-backed revision fixes it | retained blocker | expected |
| empty `realized_facts` | recoverable imperfect | retained blocker unless revision fixes it | retained blocker | expected |
| empty `key_variables` | recoverable imperfect | retained blocker unless revision fixes it | retained blocker | expected |
| empty positive/negative events | recoverable imperfect | retained blocker unless revision fixes it | retained blocker | expected |
| generic monitoring trigger | recoverable imperfect | retained blocker | retained blocker | expected |
| placeholder/generic text | recoverable imperfect | retained blocker unless revision fixes it | retained blocker | expected |
| numeric sanity | disabled | no resolver task | no resolver fan-in from numeric_sanity | expected |

### ResolveObjectionsAndDelegations -> PromoteExpectationToBeliefState

| case name | fixture 类型 | expected behavior | actual behavior | result |
|---|---|---|---|---|
| canonical resolver path | canonical | promotion accepted | accepted | expected |
| hidden unknown `price_reaction` after resolver boundary | orchestration invariant violation | promotion blocked + rejected audit | blocked | expected |
| hidden placeholder text after resolver boundary | orchestration invariant violation | promotion blocked + rejected audit | blocked | expected |

## final_payload_adapter boundary tests

| case name | fixture 类型 | expected behavior | actual behavior | result |
|---|---|---|---|---|
| list-wrapped `revised_candidate` | contract violation | schema failure | list preserved, `Document2ResolutionPlan` validation fails | expected |
| multi-candidate `revised_candidate` | contract violation | schema failure | list preserved, validation fails | expected |
| partial patch `changes` | contract violation | schema failure | extra-forbid rejects `changes` | expected |
| partial patch `path_map` | contract violation | schema failure | extra-forbid rejects `path_map` | expected |
| `proposed_patches` leak | contract violation | schema failure | extra-forbid rejects `proposed_patches` | expected |
| complete candidate-like revised_candidate dict | structural normalization | accepted | normalized without semantic invention | expected |
| partial candidate-like revised_candidate/detail payload | ambiguous semantic payload | schema failure | missing semantic fields are not invented | expected |

## Metadata sync and finding lifecycle

| case name | fixture 类型 | expected behavior | actual behavior | result |
|---|---|---|---|---|
| resolver revision sync | revised candidate fixes blocker | `pending_patches` and `document2_pending_revisions` stay aligned | aligned; transaction audit status accepted | expected |
| promotion audit after SYSTEM-authored revision patch | system transaction patch | promotion accepted through narrow Document2 system permission | accepted; promotion audit recorded | expected |
| source_objection_id unresolved | active finding | promotion blocked | blocked | expected |
| source_objection_id inactive and candidate fixed | superseded finding | promotion may proceed | accepted | expected |
| source_objection_id missing from Blackboard | inconsistent metadata | fail closed | promotion blocked | expected |
| resolved source but candidate still has same deterministic issue | inconsistent lifecycle | fail closed | active due current deterministic finding key | expected |

## 问题清单

### P0

本轮覆盖范围内的 P0 编排问题均已加 targeted runtime guard，并由 fixture matrix 覆盖：

1. resolver non-numeric deterministic blockers 过早关闭：已修，现 retained blocker。
2. promotion 首次发现 hidden quality issue 却 accepted：已修，现 blocked + rejected audit。
3. transaction-derived `author_agent=SYSTEM` patch 无法 promotion submit：已修，现仅 Document2 promotion 有极窄 SYSTEM 写权限。
4. missing `source_objection_id` finding 被当作 inactive：已修，现 fail closed。

### P1

1. 非 deterministic 的人工 reviewer finding 在 resolved/accepted 后，系统仍主要依赖 objection lifecycle，而不是强语义 supersede proof。当前矩阵覆盖了 deterministic fail-closed，后续可补“人工 finding superseded_by revision”显式模型。
2. `missing_market_evidence` 当前只验证 detail 可进入 review；还没有独立 deterministic market-evidence finding。若需要把 market evidence 纳入 promotion 前硬门，需要新增专门 finding 规则。

### P2

1. final payload adapter 仍接受完整 candidate-like dict 的结构归一化，这是有意保留的容错；后续如果真实 ReAct 输出仍漂移，可继续收窄 alias 列表。
2. promotion deterministic guard 现在复用 review 的 finding generator。若未来 review generator 变重，需要拆出更小的 promotion-safe checker。

## 已覆盖问题

- proposed_patches leak：construction review、construction resolver、detail generation、field review、objection resolver。
- deterministic revalidation：unknown price、missing evidence refs、empty facts/variables/events、generic monitoring trigger、placeholder/generic text；numeric sanity 已停用，不再进入 review/resolver/promotion blocker。
- final_payload_adapter：list/multi/partial patch/proposed_patches/complete candidate-like/partial candidate-like。
- detail delegations：非空 delegations 明确阻断。
- metadata sync：resolver revision、pending patch、transaction audit、promotion audit。
- finding lifecycle：unresolved source、inactive fixed source、missing source、resolved-but-unfixed deterministic source。

## 后续 runtime 修复建议

1. 先补人工 reviewer finding 的 supersede 语义：例如 `superseded_by_revision_id` 或 transaction audit 中的 finding-level close proof。
2. 再决定是否把 market evidence 缺失提升为 deterministic finding，而不是只靠 reviewer 或 prompt。
3. 暂缓更大范围 schema 改动；当前 targeted guards 已经覆盖本轮审查指出的 P0/P1 编排边界。

## 是否建议继续真实 smoke

本轮不建议立刻进入真实 smoke；用户已明确要求先不跑。建议先人工 review 这组 runtime diff，再在下一轮选择一个极小 `stop-after PromoteExpectationToBeliefState` 真实 smoke 作为最终验收。

## 2026-06-29 Review/Resolver Robustness Addendum

Scope: extend the fixture-driven Document2 node contract matrix for the newly
changed `ReviewExpectationFields` / `ResolveObjectionsAndDelegations` behavior.
No real LLM, external tools, or smoke tests were run.

Test command:

```powershell
$env:TMP='C:\Users\WEIXUANXIE\Desktop\DoxAgent\.tmp-uv\tmp'; $env:TEMP='C:\Users\WEIXUANXIE\Desktop\DoxAgent\.tmp-uv\tmp'; $env:UV_CACHE_DIR='C:\Users\WEIXUANXIE\Desktop\DoxAgent\.tmp-uv\cache'; $env:UV_PYTHON_INSTALL_DIR='C:\Users\WEIXUANXIE\Desktop\DoxAgent\.tmp-uv\python'; uv run pytest -p no:cacheprovider -q tests\test_document2_node_contract_matrix.py
```

Final result: `110 passed, 3 warnings in 18.93s`.

New matrix cases:

| node / case | fixture type | expected behavior | actual behavior | result |
|---|---|---|---|---|
| `ReviewExpectationFields` A1 `recommended_statement` without `evidence_refs` | recoverable / supplemental | accepted, bridged as blocking finding only when status requires revision | accepted; canonical finding keeps `recommended_statement` and empty supplemental refs | expected |
| `ReviewExpectationFields` supported supplemental `recommended_statement` | supplemental / non-blocking | accepted, no Blackboard objection | accepted; finding remains non-blocking and no unresolved objection is created | expected |
| `ReviewExpectationFields` finding `evidence_refs=["id_only"]` | contract violation | blocked by contract validation | blocked with `evidence_refs` contract error | expected after runtime guard |
| `ReviewExpectationFields` finding `recommended_statement` object | contract violation | blocked by contract validation | blocked with `recommended_statement` contract error | expected after runtime guard |
| `ReviewExpectationFields` top-level `changes` leak | contract violation | blocked; reviewers must not output patch-like edits | blocked with patch-like field error | expected after runtime guard |
| `ResolveObjectionsAndDelegations` legacy `obj_numeric_sanity_*` objection | disabled legacy blocker | no executable O1 repair task and no resolver final block | resolver advances to promotion without creating O1 task | expected after runtime guard |

Unexpected behavior found before the runtime guard:

1. Review ingestion converted non-string `recommended_statement` values to text
   via `str(...)`, so an object could become canonical review content.
2. Review ingestion dropped invalid `evidence_refs` items instead of preserving a
   schema failure.
3. ReAct review normalizers could trim top-level patch-like keys into a legal
   review payload.
4. Resolver task synthesis ignored numeric sanity objections, but the final
   unresolved-objection check still counted legacy `obj_numeric_sanity_*` entries.

Root cause:

The Pydantic output models were strict, but the workflow-side review ingestion
and ReAct review normalizers were more permissive than the current prompt
contract. The numeric-sanity disablement was applied to generation/task
synthesis/promotion paths, but not consistently to the resolver completion
predicate for legacy state.

Runtime fixes:

- `src/doxagent/workflows/document2/review.py`: reject review patch-like fields;
  reject non-string `recommended_statement`; reject `evidence_refs` values that
  are not `list[EvidenceRef object]`.
- `src/doxagent/agents/runtime/react.py`: keep review evidence refs optional,
  but preserve invalid explicit review field types so schema validation fails;
  do not normalize patch-like review payload keys away.
- `src/doxagent/workflows/document2/legacy_quality.py`: use actionable
  unresolved objections for resolver loops and final checks, excluding disabled
  numeric-sanity legacy objections without affecting other blockers.

Remaining risk:

This matrix verifies fixture-driven node contracts only. It does not prove live
LLM adherence or real tool-call quality. The next verification layer should be a
narrow Document2 or Document1+2 smoke only after reviewing these runtime guards.
