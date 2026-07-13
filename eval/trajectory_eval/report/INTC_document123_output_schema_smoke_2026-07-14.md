# INTC Document1+2+3 Output Schema / ReAct Memory Smoke 报告

## 1. 最终结论

本轮结果分为两部分：

- **本次代码修复通过定向验收。** Document1/2 输出 schema、结构字段 Citation 防误删、ResearchSection runtime 归属回填、PostgreSQL objection upsert 均已完成；本地相关测试 `21 passed`，部署镜像内的精确回归测试也通过。
- **Document1+2+3 真实整体 smoke 未通过。** 最终重跑在 Document1 `BuildGlobalResearch` 被百炼 `400 Arrearage` 阻断，Document2/3 未启动。该错误来自所有已配置百炼候选账号的账户/计费状态，不能通过代码静默降级为成功。

上一轮 provider 尚可用的真实 run 已证明修复后的流程可以完成 Document1，并推进至 Document2 `ReviewExpectationFields`；它随后暴露了 PostgreSQL conflict key 错误，该错误已修复。最终重跑因 provider 先行失败，未能再次从真实链路走到该数据库写入点。

此外，本次轨迹审计确认一个与当前 Output Schema 修复独立、但会阻止 ReAct Memory 验收的高风险问题：**Fresh Observation 固定内容超过 hard budget 后，Full Compaction 无法缩小 Fresh payload，runtime 仍继续发送超预算请求。** 上一轮最大真实输入为 `547,293 tokens`，不能判定 128k 上下文约束通过。

## 2. 代码修复与防错边界

| 问题 | 修复 | 防错/非阻塞策略 |
| --- | --- | --- |
| `ResearchSection.author_agent` 被模型留空或伪造 | ReAct final-payload 边界以可信 `AgentResult.agent_name` 覆盖 | Document1 builder 再次回填；归属字段不再阻断有效正文，也不会接受模型伪造归属 |
| 裸 `O4` 被 Citation normalizer 当作引用并从结构字段删除 | annotation processor 对 agent/alias/ID/ref/enum 等结构字段在解析、删除前直接跳过 | 自然语言字段仍照常识别裸 Citation；结构字段保持原字节 |
| reviewer finding 缺少 `expectation_id` | `expectation_id` 改为非阻塞可选字段 | 缺失时不 fan-out 到全部 expectation，只保留可路由 finding |
| `market_evidence` repair 与 promotion 断层 | schema、adapter、transaction、promotion 统一支持 | 未进入 resolver 的本轮真实轨迹未覆盖，保留为自动测试证据 |
| `realized_facts` / `key_variables` 可为空 | candidate 与 repair 类型层均要求非空 | 在进入后续质量门槛前失败，不让空业务结果继续流转 |
| `reviewer_agents` 暴露给模型 | 从模型可见 ResearchSection contract、Prompt、Context 移除 | 如需归属信息只由 runtime 维护 |
| candidate 身份字段由模型填写 | `document_id/ticker/document_type/created_at` 由 runtime 注入 | 模型只生成业务正文 |
| Field Repair 决策重复 | 统一为逐项 `decisions` 单一模型决策来源 | transaction/runtime 计算总体结果及未解决项 |
| 旧 Document2 schemas 仍可选 | 三个旧 schema 标记 deprecated，并从主流程 registry/config 移除 | `Document2ResolutionPlan`、`ExpectationConstructionResult`、`ExpectationDetailResult` 不再被当前节点选择 |
| objection upsert 与远端主键不一致 | `ON CONFLICT (run_id, objection_id)` | 与远端实际复合主键一致，无需破坏性迁移 |

“做成非阻塞”只用于 runtime 可以可靠恢复的结构元数据，例如 author 归属。provider 无法生成研究内容、硬预算仍超限等情况继续显式 blocked，不伪造业务成功。

## 3. Commit、部署与测试

| Commit | 内容 |
| --- | --- |
| `444a39d` | Document1/2 Output Schema 整体优化 |
| `2824475` | ResearchSection author 归属 runtime 化 |
| `20551e0` | 结构字段裸 Citation 防误删与 Document1 回填 |
| `a76d121` | ResearchSection fallback 测试隔离 |
| `d429465` | objection upsert 对齐复合主键 |

远端 `/root/doxagent` 已 pull 至 `d429465`，DoxAgent/Dashboard 镜像已 build，`doxagent-dashboard` 与 `doxagent-runtime-scheduler` 健康；最终 smoke 使用该镜像。

验证结果：

- 本地：`tests/test_document12_output_schema_optimization.py` + `tests/test_evidence_ref_restructuring.py`，`21 passed`。
- 相关扩展测试：`21 passed, 5 skipped`；Ruff 与 diff check 通过。
- 部署镜像：结构字段 O4、ResearchSection 回填、复合 conflict key 精确回归测试通过。
- 此前全量测试：`308 passed, 44 skipped, 2 failed`；两个失败分别为缺失 `dev_plan/PHASE0_BASELINE.md` 和 Dashboard 旧测试期望 `None`、实际 `0.0`，与本次修改无关。

## 4. 真实 smoke 时间线

| 北京时间 | 容器/Run | 结果 |
| --- | --- | --- |
| 约 04:12 | `doxagent-smoke-intc-d12-20260714-0412` | `ResearchSection.author_agent` 空值暴露 |
| 约 04:44 | `doxagent-smoke-intc-d12-20260714-0444` | 再次失败；定位到结构字段 `O4` 被裸 Citation 删除 |
| 05:13:36–06:00:39 | `doxagent-smoke-intc-d12-20260714-0515` / `run_799e096d5f964e5eb1dbf4553058a47f` | Citation 根修后完成 D1、推进 D2；在 `ReviewExpectationFields` 写 objection 时遇到 PostgreSQL `42P10` |
| 06:26:12–06:26:36 | `doxagent-smoke-intc-d12-20260714-0626` / `run_fc293967919e4faab6783ac46d91e104` | DB 修复后重跑；所有 BuildGlobalResearch agent 的全部 provider 候选均失败，最终 blocked |
| 06:36:35 | 定时 cron 唤醒 | 单次检查终态；不是 heartbeat，未 sleep/循环 |
| 06:39:38 | 定时检查记录 | 确认 `exited|1`，未错误启动 D3；随后删除 cron |

监控过程曾出现两次独立监控异常：05:55 SSH hostname 解析失败、06:16 PowerShell 参数解析冲突；两次均未改变远端 smoke 状态。后续使用逐字 SSH 命令的定时唤醒成功确认终态。

## 5. Workflow 结果

### 5.1 最终 dbfix 重跑

| Document | 状态 | 已完成节点 | 阻塞点 |
| --- | --- | --- | --- |
| Document1 | blocked | `StartTickerInitialization` | `BuildGlobalResearch`：百炼 `400 Arrearage` |
| Document2 | not started | 无 | Document1 未完成 |
| Document3 | not started | 无 | 无有效 Document1+2 execution run id，按规则不启动 |

最终重跑的 LangSmith 轨迹包含 24 次 provider 请求：C1/C2/C3/O4 各 6 次，全部失败；主要错误为 `Arrearage`，C2/O4 各夹杂连接错误。时间集中在 `2026-07-13T22:26:21Z–22:26:27Z`。

### 5.2 上一轮可执行 run

`run_799e096d5f964e5eb1dbf4553058a47f` 完成：

1. `StartTickerInitialization`
2. `BuildGlobalResearch`
3. `ReviewGlobalResearch`
4. `GenerateExpectationConstruction`
5. `ReviewExpectationConstruction`
6. `ResolveExpectationConstruction`
7. `GenerateExpectationDetails`

在 `ReviewExpectationFields` 后写 objection 时失败。远端最新 checkpoint 为 `blocked / ReviewExpectationFields / completed_nodes=7`。该 run 是下面 ReAct/Workflow Memory 实际表现统计的主要样本。

## 6. LangSmith Agent Loop 与上下文指标

上一轮共 47 次 provider 请求，其中正常 Agent 请求 31 次、Full Compaction 请求 16 次。`LOOP#` 是 provider 尝试序号；实际 ReAct 轮次按 metadata `react_step` 统计。

| 节点 / Agent | 正常请求 | Compaction 请求 | 最大 Fresh chars | 最大 Retained chars | 最大 Passive chars | 最大真实 prompt tokens | 结果 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| GenerateExpectationConstruction / O1 | 2 | 2 | 1,493,686 | 2 | 2 | 544,198 | 成功生成 construction |
| ReviewExpectationConstruction / A1 | 2 | 2 | 1,493,686 | 2 | 2 | 538,708 | 成功完成 review |
| GenerateExpectationDetails / O1 | 9 | 8 | 1,494,840 | 2 | 2 | 547,293 | 三个 candidate task 成功 |
| ReviewExpectationFields / A1 | 2 | 0 | 2,323 | 2 | 2 | 15,412 | 成功 |
| ReviewExpectationFields / C1 | 7 | 4 | 1,537,651 | 2 | 2 | provider 未返回 token；最终失败 | 首轮一度成功，后续 Arrearage |
| ReviewExpectationFields / C3 | 7 | 0 | 167,352 | 2 | 2 | provider 未返回 token；最终失败 | 首轮一度成功，后续 Arrearage |
| ReviewExpectationFields / O4 | 2 | 0 | 301,277 | 2 | 2 | 147,599 | 成功 |

这里 `2 chars` 表示 JSON 空数组 `[]`。正常 Agent 请求中没有实际装载 Retained/Passive block；大量原文都以 Fresh Observations 直接注入。

## 7. ReAct Memory / Workflow Memory / Evidence Citation 表现

### 7.1 Retained 与 Passive Carryover

- 按 run_id 精确聚合的 15 条 Working Memory 记录中，persisted ReAct audit 累计记录 77 个 retained items，序列化体积 16,035 bytes。
- LangSmith 正常 Agent input 的 Retained Observation 最大仍为 0 block；这些 retain 多发生在任务完成 action 或 maintenance action 中，没有形成下一次正常 Agent 调用的可观测加载。
- persisted audit 累计记录 42 个 Passive candidate，预算上限 64,000 tokens；预算历史中 maintenance projection 一度计算出最多 23 个 passive block。
- 但所有正常 Agent input 的 Passive Carryover 都是 0 block。主要原因是 Fresh/其他输入已超过 96k ceiling，Passive 没有可用预算；部分引用还被显式 retained 或任务随即结束。

因此，本次轨迹证明了 Passive 不会继续挤压已经过大的正常输入，但**没有证明 Passive block 在真实下一轮被正确携带**。

### 7.2 Compaction 与 128k hard budget

PostgreSQL 的 bounded audit 聚合结果：

| 指标 | 数值 |
| --- | ---: |
| context budget records | 55 |
| Micro Maintenance | 8 |
| Full Compaction provider requests | 16 |
| Full Compaction applied | 14 |
| Full Compaction failed | 2 |
| over-hard budget records | 30 |
| 最大 runtime 估算 projected input | 443,663 tokens |
| 最大 provider 实际 input | 547,293 tokens |

根因不是“没有触发 compaction”，而是：

1. 普通非 SEC 工具的完整 Fresh Observation 一次注入约 149–154 万字符；
2. Micro/Full Compaction 只能整理 Synthesis、Agenda、Retained 等可维护状态，不能删除本轮必须消费的 Fresh payload；
3. Full Compaction 重试后仍 over-hard；
4. safe fallback 只能把 loaded retained observation 降为 index-only；当超限主体是 Fresh Observation 时无内容可卸载；
5. runtime 记录 warning 后仍返回到正常请求路径，最终把 538k–547k token 请求发给 provider。

这违反“DoxAgent 单次 ReAct 输入主动限制为 128k”的契约。应在后续修复中对单结果 >128k 的 provider 输出执行紧凑化/分页，或在发送前 hard-block；不能依赖 Full Compaction 处理不可压缩 Fresh payload。

### 7.3 Workflow Memory

- construction/details/review 节点实际注入的 Workflow Memory 为约 3,702–28,416 chars。
- 最大值出现在 O4 `ReviewExpectationFields`，为 28,416 chars；相较 301,277 chars Fresh 和 147,599 provider tokens，它不是本轮主要膨胀源。
- final dbfix 重跑停在 BuildGlobalResearch 初始 step，`workflow_memory={}`，符合尚无稳定上游文档时的预期。

### 7.4 Citation、retain schema 与事件时间

- 可解析输出中的 `retain_observations` 共观察到 40 个 `alias + note` item，`reason` 出现 0 次；无 reason 新 contract 生效。
- 标准 `tool_calls:[{"tool_name":"read_observation",...}]` 与旧快捷结构在真实输出中均为 0 次，因此本轮没有真实回读验收证据；自动测试已覆盖标准/批量回读与禁止快捷结构。
- Action 与 reasoning channel 均出现真实 Observation Citation；normalizer 可拆分并去重有效 alias。
- 结构字段 `author_agent="O4"`、`agent_definition.agent_name="O4"` 已由回归测试证明不会再被裸 Citation 删除。
- `occurred_at` 观察到 4 次，`published_at` 为 0；事件时间使用率仍偏低。
- structured/text 镜像在统计前按单个 provider message 处理，没有对镜像正文重复累计。

## 8. 最终节点配置矩阵

完整矩阵见 `dev_plan/ReAct_Node_Config_Matrix.md`。最终有效配置如下：

| Workflow | 节点 | max_steps | 状态 |
| --- | --- | ---: | --- |
| Document1 | BuildGlobalResearch | 10 | 从默认 5 提升 |
| Document1 | ReviewGlobalResearch | N/A | deterministic，未改 |
| Document1 | GenerateGlobalNarrativeReport | 10 | 从默认 5 提升 |
| Document2 | GenerateExpectationConstruction | 10 | 从默认 5 提升 |
| Document2 | ReviewExpectationConstruction | 5 | review 不变 |
| Document2 | ResolveExpectationConstruction | 10 | 从默认 5 提升 |
| Document2 | GenerateExpectationDetails | 10 | 从默认 5 提升 |
| Document2 | ReviewExpectationFields | 3 | 显式 review 配置不变 |
| Document2 | ResolveObjectionsAndDelegations | 1 | repair/resolve 显式配置不变 |
| Document2 | PromoteExpectationToBeliefState | N/A | deterministic，未改 |
| Document3 | 全部 ReAct 节点 | 5 | 不变 |
| 其他 workflow | 全部节点 | 原配置 | 不变 |

## 9. 验收判定与后续阻塞项

| 验收域 | 判定 | 说明 |
| --- | --- | --- |
| Document1/2 Output Schema 代码修复 | 通过 | 定向测试与部署镜像精确测试通过 |
| Citation 结构字段根修 | 通过 | parser 前跳过结构字段 + runtime 双层回填 |
| PostgreSQL objection upsert | 通过定向验证 | SQL 与远端复合主键一致；最终真实 run 被 provider 提前阻断 |
| 真实 Document1+2+3 E2E | 不通过 | provider `Arrearage`，D3 未启动 |
| ReAct 128k hard budget | 不通过 | 最大实际输入 547,293 tokens，compaction 后仍发送 |
| Passive Carryover 真实装载 | 未覆盖 | 正常 Agent input 均为 0 block |
| read_observation 真实回读 | 未覆盖 | 真实 action 未调用 |

恢复完整验收需要先解决百炼账号/计费状态，再重跑 INTC Document1+2+3。即使 provider 恢复，也应先修复 Fresh Observation hard-budget 失守，否则真实流程仍可能以超出 DoxAgent 产品契约的上下文运行。
