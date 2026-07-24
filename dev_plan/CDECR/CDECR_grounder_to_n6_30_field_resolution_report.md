# CDECR 30 篇 Grounder→N6 字段归一真实验收报告

## 1. 实验边界

- 输入：既有 30 篇已审核、非聚合形新闻的冻结 Grounder 输出，共 227 条 Event Mention。
- 路径：Grounder 结果物化 → N5 Mention Finalization → N5.5 KB Linking / Field
  Coreference → N6 Identity Compiler。
- Judge：明确跳过；隔离 Registry 为 30/30 文档写入 `EVALUATION_JUDGE_SKIPPED`，Judge
  模型调用为 0。
- 不执行 N7–N11，不创建 Atomic Event 或 Event Package。
- 所有新闻全文、字段上下文、完整逐项审查和 SQLite 只保存在 Git 忽略的
  `.tmp/cdecr/field_resolution_30_20260723/`。

字段解析经过 4 个逻辑 pass 后收敛；最后一轮 field-links hash 无变化。工作流真实调用共
337 次，全部成功：M1 `text-embedding-v4` 146 次，M2 `deepseek-v4-flash` 191 次。另用
M4 `qwen3.7-max` 做 928 条字段的辅助审查；首次审查在第 39 批连续两次返回语义不完整
JSON，严格校验中止，启用逐批 checkpoint 后完整重跑。Registry 最终记录 95 次审查调用，
其中 93 次成功、2 次严格校验失败。所有结构化模型均使用 JSON Mode，reasoning effort 为
`none`。

## 2. 结果摘要

字段总数 928，其中 754 个进入 N5.5 路由，174 个未路由；696 个获得 link，58 个已路由
字段保持 unresolved。单纯 link coverage 为 92.31%，但它明显高估真实质量。

逐字段 M4 辅助判定后，对 105 条存在确定性口径矛盾的判定做了一致性修订：不同数值的
同一 metric 仍是同一指标概念；null link 不能判为 incorrect link；fiscal period 不得脱离
Grounder surface 擅自改写年份。最终 928 条均有记录，5 条 AMBIGUOUS 不进入
precision/recall 分母。该结果是 agent/M4-assisted audit，不是独立人工金标准。

| 指标 | Precision | Recall | 结论 |
|---|---:|---:|---|
| Overall field resolution | 76.27% | 73.60% | 不达生产预期 |
| KB Linking | 96.81% | 96.53% | 基础对象 KB 有效 |
| Field Coreference（field） | 61.27% | 57.30% | 明显不达标 |
| Field Coreference（pairwise） | 89.34% | 68.20% | 误并相对少，漏并和错误 canonical 较多 |

计数口径：923 条非 AMBIGUOUS 字段中，691 条已有 link，527 条被判为正确；716 条应解析。
KB actual/expected 为 345/346；provisional Field Coreference actual/expected 为 346/370。

## 3. 按字段审查

| Namespace | Precision | Recall | 主要结论 |
|---|---:|---:|---|
| participant.company | 100.00% | 100.00% | 188/188，ticker/别名 KB Linking 稳定 |
| participant.institution | 100.00% | 100.00% | 7/7 |
| participant.person | 100.00% | 100.00% | 7/7 |
| place | 100.00% | 100.00% | 17/17；样本量小 |
| concept.rating | 100.00% | 100.00% | 5/5；样本量小 |
| metric | 67.41% | 78.65% | 相关但不同指标被并入大簇 |
| concept.predicate | 58.82% | 64.68% | 通用/具体 action 串簇最严重 |
| fiscal_period | 100.00% | 33.85% | 已链接项准确，但仅解析 22/65 个应解析项 |
| participant.unknown | — | 0.00% | 79 项中 30 项实际是可解析命名对象，路由前即漏掉 |
| unrouted.open_attribute | — | 0.00% | 95 项中 4 项应进入对象解析但未路由 |

主要混杂簇：

- 34-member revenue metric 簇混入 revenue、EPS、segment revenue、committed value。
- 16-member gross-margin 簇混入 actual、consensus 与 guidance metric。
- 14-member forecast/guidance predicate 簇混入 analyst forecast、issuer guidance 和 raise target。
- price target、free cash flow、index level、agreement、capacity 等簇均出现语义相关但身份不同的
  对象被合并。
- `expect` 簇把 `expect_capex`、`expect_margin`、`expect_improvement` 等不同 predicate
  压成同一 provisional root。

这说明 Field Coreference 的主要问题不是 namespace 跨类污染，而是同一 namespace 内用
Embedding/LLM 把“语义相关”误当成“同一 canonical object”；同时早期 participant type
router 对 Anthropic、IDC、Counterpoint Research、Samsung Electronics、ETF/指数等对象召回
不足。

## 4. N6 评估

- Schema complete：223/227（98.24%）；4 条因 `predicate.normalized` unresolved 而无 Identity。
- 但按 N6 实际消费的 predicate、principal participants、location、reference period 逐项核查，
  core-field clean 只有 98/227（43.17%）。
- 再要求 principal participant 非空，strict usable 只有 75/227（33.04%）；50 条无可用
  principal（包含 4 条无 Identity）。
- 既有 Grounder 人工报告还确认 101 条带 event_start 的 Mention 中有 46 条执行日期泄漏。
  本实验按要求跳过 Judge，N5.5/N6 不会也不应修复这类上游事实错误。

因此，`223/227 identity_profile != null` 只能说明 Schema 可构造，不能解释为 N6 身份正确率。
当前结果不应继续进入 N7/N8 的 Atomic recall/cannot-link。

## 5. 修复优先级

1. 将 participant router 从“KB exact 命中后才定型”改为 typed multi-recall，再让 KB/FC 在类型内
   决策；补齐 company/institution/instrument/index 的 alias 与类型先验。
2. predicate 和 metric 禁止仅凭 Embedding 近邻直接形成可复用 root；增加 canonical granularity
   规则与反例，区分 actual/consensus/guidance、value/change、generic/specific action。
3. Field Coreference 决策应允许 `NEW` 保守分裂，并在大簇增长时执行 impurity guard；同 root
   出现多个互斥 attribute/use 或 predicate head 时转 HOLD，而不是继续吸附。
4. 完成 issuer-aware fiscal period surface 解析；`fiscal third-quarter`、`FY2026-Q4` 等应以
   issuer + published_at + 财期目录确定，无法确定时保持 unresolved 并阻止 typed Identity。
5. N6 OPEN Identity 至少要求一个可信 principal participant；核心链接缺失或被审计为风险时，
   不得以空 participant list 伪装为 complete。
6. 在上述修复前恢复 Judge；本 30 篇 Grounder 报告仅 118/227 strict PASS，跳过 Judge 会把
   时间、原子性、eventhood 和 assertion 错误直接传入 canonical identity。

## 6. 产物与验证

- Runtime JSON SHA-256：
  `5a6243a8897ea2f95ae676eb49f10b8461d3ffd2d01313ea93eaf71ef40d360a`
- 原始 M4 审查 SHA-256：
  `fc9414a0fc54994d79dcfe13509719553dc5f894746a06e3398c30f24698aded`
- 一致性修订 JSON SHA-256：
  `c88febd2af123dd4814c1412081eaf59487215f9492aa712b87e5a9df94b00cd`
- 完整 928 字段 + 227 Identity Markdown SHA-256：
  `248fd90416d64fcdfce085baf6e467e7c1f2641c12412be08047b810908d59e5`
- CDECR pytest：156 passed, 3 skipped。
- scoped ruff：通过。
- scoped mypy：通过。
- `git diff --check`：通过（仅现有 Windows LF/CRLF 提示）。

