# Document 2 预期单元评估契约

## 目标

Document 2 eval 的核心目标是持续提升 Blackboard 中 Document 2，即 `expectation_unit` 的生成质量。
debug、跑通 workflow、定位 blocker 都是必要手段，但不能替代最终质量目标：产出的预期单元必须更可投资、
更可审计、更能支撑后续 KnownEvents、MonitoringConfig 和 MonitoringPolicy。

Document 2 从已经稳定的 Document 1 Global Research 出发，生成可审计、可复核、可稳定化的
`expectation_unit` 文档。一个有用的 Document 2 eval 循环不仅要看 workflow 是否跑通或 O1 是否输出文本，还要判断：

- expectation shell 是否形成清晰、差异化、方向明确的投资预期；
- detail patches 是否包含具体的已兑现事实、价格反应、关键变量和事件监控方向；
- A1/C1/C3/O4 字段复核是否真实施压，并把问题转化为可追踪 objections 或 delegations；
- O1 resolver 是否接受、部分接受、拒绝或保留异议，并用 revised patches 解释修改；
- promotion 是否把合格 pending patches 写入稳定 `expectation_unit`，且没有绕过 evidence、review 和 blocker 检查。
- baseline 到 retest 的变化是否真正改善了预期单元质量，而不是只把失败位置后移、把 blocker 静默吞掉，或让低质量 patch 勉强 promotion。

## 适用范围

本规范适用于通过 `eval/run_document2_expectation_units_smoke.py` 从 Document 1-only 运行继续执行的真实
Postgres 可见 eval。脚本必须复用正常 `BlackboardInitializationWorkflow`，不能直接调用 O1 helper 或绕过 workflow 节点。

可评估的停止点如下：

| 停止点 | 可判断内容 | 不可宣称内容 |
| --- | --- | --- |
| `GenerateExpectationDetails` | expectation detail pending patches 是否产生，字段是否足够进入 review | 不能宣称稳定 `expectation_unit` 已形成 |
| `ReviewExpectationFields` | 字段复核是否覆盖事实、证据、价格反应、变量和监控方向 | 不能宣称 objections 已被解决 |
| `ResolveObjectionsAndDelegations` | resolver 是否处理 blockers、修订 patches、关闭或保留风险 | 不能宣称 patches 已进入稳定 belief state |
| `PromoteExpectationToBeliefState` | Document 2 是否成为稳定 `expectation_unit` | 不能替代完整初始化最终通过结论 |

## 防错规则

1. 不能把 pending patches 计为稳定 `expectation_unit`。
2. 不能因为完整 workflow 的后置文档尚未生成，就把 Document 2 本身判为失败。
3. 不能把“未 promotion”笼统描述为“没有生成”。必须说明阻塞发生在 detail、review、resolver 还是 promotion。
4. baseline 和 retest 必须使用同一版 hard gates 和 rubrics。
5. 如果 LangSmith trace、Brief State JSON 或 git 状态缺失，本轮只能作为诊断记录，不能作为优化成功证据。
6. 如果 source run 不是 Document 1-only 状态，本轮 document2 eval 无效。

## 每次评估必需输入

- source run 的 `run_id`、ticker、git 状态和 Brief State 摘要。
- source checkpoint 的 `completed_nodes`、`next_node`、`stable_document_types`、pending patch 数、未解决 objections 和 blocking delegations。
- Document 2 execution run 的 `run_id`，以及 clone 或 in-place 模式。
- 真实执行命令、环境变量和 `--stop-after` 参数。
- `eval/export_brief_state.py` 导出的 Brief State JSON 路径。
- 同一 `run_id` 的 LangSmith trace 或 MCP 查询笔记。
- `document2_hard_gates.yaml` 的逐项结果。
- `document2_rubrics.yaml` 的逐项评分和理由。
- 【重点】本轮问题归类、优化假设、修改文件、复测结果和接受或拒绝结论。

## 建议执行入口

真实 smoke/eval 不能在本地直接运行。下面的本地命令只用于说明脚本入口和参数形态；正式 baseline/retest
必须在云服务器上的 Docker 环境中执行，避免本地环境、网络、缓存或凭据差异污染结论。

```powershell
$env:DOXAGENT_RUN_REAL_API_TESTS='1'
$env:DOXAGENT_STORAGE_MODE='postgres'
uv run python eval\run_document2_expectation_units_smoke.py <source_run_id> --mode clone --stop-after PromoteExpectationToBeliefState --export-brief-state
```

诊断阶段可将 `--stop-after` 改为 `GenerateExpectationDetails`、`ReviewExpectationFields` 或
`ResolveObjectionsAndDelegations`。默认优先使用 `--mode clone`，避免污染原始 Document 1 source run。

## 云端执行与部署链路

所有真实 Document 2 smoke/eval 必须通过 Windows SSH alias `doxagent-hk` 连接云服务器执行。该 alias 指向
部署在云端的 DoxAgent 环境；远端项目路径是 `/root/doxagent`。PowerShell 中远端 Linux 命令使用单引号包裹：

```powershell
ssh doxagent-hk 'whoami && hostname'
```

一轮 eval 的标准执行链路如下：

1. 本地确认修改范围和 git 状态，只提交本轮需要测试的代码、prompt、schema 或 eval 文档。
2. 本地执行 `git push origin main`，或推送当前评估分支。
3. 云端拉取并构建：

```powershell
ssh doxagent-hk 'cd /root/doxagent && git pull --ff-only && docker compose build debug-viewer && docker compose up -d debug-viewer'
```

4. 如果云端缺少真实 API、Postgres 或 LangSmith 配置，通过 SSH/SCP 更新 `.env`，不要把 `.env` 提交进 git：

```powershell
scp .env doxagent-hk:/root/doxagent/.env
```

5. 在云端容器里执行 Document 2 smoke/eval。示例：

```powershell
ssh doxagent-hk 'cd /root/doxagent && docker compose run --rm -e DOXAGENT_RUN_REAL_API_TESTS=1 -e DOXAGENT_STORAGE_MODE=postgres debug-viewer python eval/run_document2_expectation_units_smoke.py <source_run_id> --mode clone --stop-after PromoteExpectationToBeliefState --export-brief-state'
```

6. 用同一 `execution_run_id` 查询云端 Postgres 可见的 Brief State、LangSmith trace 和 hard-validator 结果。
7. 将 baseline/retest 记录追加到 `document2_eval_records.md`，并把重要代码修改同步追加到仓库根目录 `changelog`。
8. 不要在 `/root/doxagent` 之外执行宽泛的 `docker compose down`；保持 DoxAgent 与 DoxAtlas compose 项目隔离。

## Brief State 审查重点

审查 Brief State JSON 时至少检查：

- `workflow_state`、latest checkpoint status、`completed_nodes`、`next_node` 和 error。
- source run 是否只有稳定 `global_research`，且没有既有 stable `expectation_unit`。
- Document 2 execution run 中的 pending patches、stable documents、Working Memory、Commit Log。
- `expectation_units` 或 pending expectation patches 的字段完整性。
- `blockers.open_objections` 和 `blockers.blocking_delegations`。
- hard validators 中的 evidence、tool-boundary、commit-log 一致性结果。
- 必须审查Brief State中`document2_rubrics`相关项的表现与生成质量。

## LangSmith 审查重点

Document 2 过程评分不能只靠 Brief State。必须检查同一 `run_id` 的这些节点：

- `GenerateExpectationConstruction` 和 `ReviewExpectationConstruction`。
- `ResolveExpectationConstruction`，如果 construction review 产生 objections。
- `GenerateExpectationDetails` 的每个 expectation shell fan-out。
- `ReviewExpectationFields` 中 A1/C1/C3/O4 的字段复核 loops。
- `ResolveObjectionsAndDelegations` 的 resolver loops、accepted revisions 和 residual risks。
- `PromoteExpectationToBeliefState` 的 promotion 结果和 blocker 原因。
- 所有被 final payload 引用的 tool calls、source ids、evidence refs 和失败重试记录。

## 评估 SOP

1. 本地记录 git 状态，只暂存和提交本轮评估需要的变更；如果存在无关 dirty-tree，必须在记录中说明。
2. 选择一个 Document 1-only source run，并验证 source gate。
3. 本地 `git push`，云端 `git pull --ff-only`，然后在 `/root/doxagent` 执行 `docker compose build debug-viewer`。
4. 在云端 Docker 容器中运行 Document 2 smoke/eval，记录 SSH 命令、容器命令、环境变量和 `--stop-after`。
5. 捕获 execution `run_id` 和脚本输出。
6. 导出或定位 execution run 的 Brief State JSON。
7. 查询同一 `run_id` 的 LangSmith trace。
8. 按 `document2_hard_gates.yaml` 逐项判断。
9. 对每个 failed 或 partial hard gate 填写 root-cause matrix，明确 `failure_kind=direct|derivative|quality_residual`、blocking node、root cause、是否被本轮 modification 覆盖，以及 retest expectation。
10. 按 `document2_rubrics.yaml` 逐项评分，即使硬门槛失败也可用于诊断。
11. 把 baseline 追加到 `document2_eval_records.md`。
12. 聚类失败类别，写出以质量提升为中心的可验证优化假设。
13. 在修改代码、prompt、schema 或 workflow 前记录 baseline commit 或 dirty-tree 摘要。
14. 执行范围最小的修改，并追加 `changelog`。
15. 再次 `git push`、云端 `git pull --ff-only`、`docker compose build debug-viewer`，用同一停止点和同一评分标准在云端复测。
16. 追加 retest 结果，比较 hard gate、rubric delta 和 Document 2 质量变化。
17. 只有当目标质量问题改善且没有新增不可接受硬门槛失败时，才接受修改。

## 失败类别

记录时优先使用以下类别，并在必要时加短 subtype：

- `source_state_invalid`：source run 不是 Document 1-only 状态。
- `workflow_completion`：Document 2 路径未到达目标停止点、checkpoint 或 resume 异常。
- `construction_quality`：expectation shell 重复、方向不清、与 ticker 或 Document 1 脱节。
- `detail_contract`：O1 detail 输出无法解析、字段为空、placeholder、summary-only 或 schema 不合格。
- `evidence_integrity`：source refs 缺失、粒度过粗、无法水合、与工具调用不一致。
- `price_in_reasoning`：把旧新闻当催化剂、价格反应缺失、narrative-only 支撑价格判断。
- `field_review_pressure`：A1/C1/C3/O4 review 缺失、过弱、未提出必要 blockers。
- `objection_resolution`：resolver 超时、重复 objections 未合并、accepted revision 不落地。
- `promotion_blocker`：pending patches 未通过 `can_promote_target` 或仍有 blockers。
- `context_management`：输入上下文过长、重复、低价值，导致 detail/review/resolver 质量下降或超时。
- `memory_continuity`：多轮 loop 中丢失关键事实、source id、objection 决策、patch revision 或变量状态。
- `tool_trajectory`：工具越权、未执行却被引用、失败重试不可见。
- `traceability`：Brief State、LangSmith、Commit Log 或 Working Memory 无法互相复原。
- `optimization_readiness`：问题描述太泛，无法转成下一轮可测修改。

## 接受规则

一次 Document 2 优化只能在以下条件同时满足时被接受：

- baseline 和 retest 使用同一停止点、hard gates 和 rubrics；
- retest 没有新增不可接受硬门槛失败；
- 目标失败类别有 Brief State 和 LangSmith 双证据支持的改善；
- pending patch、objection、delegation、promotion 状态被准确区分；
- 所有回归被明确列出，并说明为什么可接受或不可接受；
- `document2_eval_records.md` 中包含 source run、execution run、Brief State JSON 和 LangSmith 查询笔记。
