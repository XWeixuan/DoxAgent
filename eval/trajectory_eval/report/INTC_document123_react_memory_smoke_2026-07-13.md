# INTC Document1+2+3 ReAct Memory 整体 Smoke Test 报告

## 1. 结论

- 测试结论：**失败 / 阻塞**。
- Document1 已完成并形成稳定 `global_research`；Document2 完成 construction、review、resolve，但阻塞在 `GenerateExpectationDetails`；Document3 因缺少稳定 expectation units，按业务前置条件未启动。
- 失败不是上下文溢出、Micro Maintenance 或 Full Compaction 导致。持久化审计中的最高 projected input 为 29,647 tokens，LangSmith 最高单次 provider input 为 43,389 tokens，均明显低于 115,200 / 128,000 阈值；本次 compaction 次数为 0。
- 直接根因是三个并行 expectation-detail 子任务都在最后一个允许的 ReAct step 继续请求 `read_observation`，没有返回 `ExpectationDetailCandidateResult`。其中两个子任务还输出了不符合当前 ReAct action 契约的快捷结构 `{"read_observation": ...}`。
- 新 memory prompt 分层与 128k budget 配置已在真实请求中生效；Passive Carryover 选择逻辑被触发过，但本次没有任何 passive block 实际进入下一轮；所有已持久化任务的显式 retained observation 均为 0。
- 新 evidence citation prompt 已真实注入，但实际使用高度不均衡：O4 产生并成功解析 46 个 citation tags，其他已持久化 agent 结果均为 0；provider reasoning channel 中没有观察到规范的 `【cite:O#】`。

## 2. 测试范围与运行信息

| 项目 | 结果 |
| --- | --- |
| 远端 | `doxagent-hk:/root/doxagent` |
| 分支 | `main` |
| 拉取前 HEAD | `1adb2a8` |
| 拉取后 HEAD | `76837bd1329ac404c5c47da848c7802846a62362` |
| Ticker | `INTC` |
| Tool mode | `real` |
| Document1 source run | `run_0acec8ad417d455996f974b0fac945cd` |
| Document2 execution run | `run_6a84997017314663bf1ff5b4a4f98e2e` |
| 开始时间 | 2026-07-13 18:43:25 +08:00 |
| 结束时间 | 2026-07-13 18:51:35 +08:00 |
| 监测方式 | 一次真实的 10 分钟定时唤醒；未使用 heartbeat |
| 最终状态 | `blocked`，`next_node=GenerateExpectationDetails` |

远端最新 compose 已没有 `debug-viewer` service，因此实际使用 `runtime-scheduler` 镜像执行 smoke。该差异不影响 workflow 执行，但说明旧的部署命令已过期。

## 3. 业务流程结果

| 节点 | 状态 | LangSmith / 持久化观察 |
| --- | --- | --- |
| StartTickerInitialization | completed | 无模型 loop |
| BuildGlobalResearch | completed | C1/C2/C3/O4 共 18 次模型调用；C1/C2/C3 到达 max steps 后走保守恢复输出，O4 正常完成 |
| ReviewGlobalResearch | completed | 未发现独立模型 loop，按当前流程使用已有结果继续 |
| GenerateExpectationConstruction | completed | O1 共 3 loops：调用 narrative report、读取 3 个 observation、生成 3 个 expectation shells |
| ReviewExpectationConstruction | completed | A1 共 4 loops，完成 review |
| ResolveExpectationConstruction | completed | 未发现独立模型 loop，按当前流程完成确定性 resolve |
| GenerateExpectationDetails | blocked | 3 个并行子任务各执行 5 steps，均未返回 final payload |
| Document3 | not started | expectation unit count 为 0，不满足 Document3 前置条件 |

最终 checkpoint：

- completed nodes：`StartTickerInitialization`、`BuildGlobalResearch`、`ReviewGlobalResearch`、`GenerateExpectationConstruction`、`ReviewExpectationConstruction`、`ResolveExpectationConstruction`
- stable document types：仅 `global_research`
- working memory entries：7
- commit log：1
- pending patches：0
- expectation unit count：0
- error：`GenerateExpectationDetails agent result failed: ReAct step 未返回 final payload、工具调用或委托。`

## 4. LangSmith 逐节点 Agent Loop

### 4.1 BuildGlobalResearch

| Agent | Loops | Provider input tokens 范围 | 主要输出行为 | 结果 |
| --- | ---: | ---: | --- | --- |
| C1 | 5 | 5,907–25,494 | Tavily、SEC、Alpha；后续 `read_observation` | max-steps conservative recovery |
| C2 | 5 | 4,178–43,389 | FRED、BLS、BEA、FOMC、Polymarket、TwelveData；大量 `read_observation` | max-steps conservative recovery |
| C3 | 5 | 4,392–25,909 | peers、SEC、Tavily；部分 loop 无进展 | max-steps conservative recovery |
| O4 | 3 | 4,673–25,206 | TwelveData；最终正文使用 O1/O10/O19 citation | 正常完成 |

这里的主要问题不是 memory 超限，而是 C1/C2/C3 没有在 5 steps 内完成正式 final payload，workflow 依赖 recovery 生成稳定段落。该恢复使 Document1 能继续，但研究质量低于正常完成路径。

### 4.2 GenerateExpectationConstruction

- 3 loops，provider input 为 8,057 / 14,784 / 21,229 tokens。
- Loop1 调用 `doxa_get_narrative_report`。
- Loop2 读取 3 个 Observation。
- Loop3 生成 3 个 expectation shells：`exp_intc_001`、`exp_intc_002`、`exp_intc_003`。

### 4.3 ReviewExpectationConstruction

- 4 loops，provider input 范围 7,846–21,002 tokens。
- 中间读取 7 个 Observation，最终完成 construction review。
- 作为 review 节点，请求只加载 `memory.md`，没有加载 `research_memory.md`，符合分层要求。

### 4.4 GenerateExpectationDetails

三个子任务并行执行，各自使用独立 task memory：

| Expectation | Task ID | Steps | Provider input tokens 范围 | 最后一步模型输出 |
| --- | --- | ---: | ---: | --- |
| `exp_intc_001` | `task_5a301dc8f0f443fb8c17cdbd605586b5` | 5 | 9,634–16,394 | `{"read_observation":{"alias":"O19",...}}` |
| `exp_intc_002` | `task_46e2093eeb594166947e834a8b5fb575` | 5 | 9,678–16,544 | 合法 ReAct wrapper，但仍调用 `read_observation(O739)`，`is_complete=false` |
| `exp_intc_003` | `task_b9ddbcaf578e486a9acdad9df384561d` | 5 | 9,741–16,616 | `{"read_observation":{"alias":"O738"}}` |

前两条快捷结构不包含 `tool_calls` 数组，因此不符合当前 ReAct action schema。第三条虽然结构合法，但已经处于最后一个允许 step，没有后续 loop 可以读取材料并生成 candidate。三条任务都没有产生 `final_payload`。

## 5. Token 与 Memory 指标

LangSmith 累计模型使用：

| Run | 模型调用数 | 累计 input tokens | 累计 output tokens | 累计 total tokens |
| --- | ---: | ---: | ---: | ---: |
| Document1 source run | 18 | 235,618 | 18,178 | 253,796 |
| Document2 execution run | 22 | 287,733 | 16,783 | 304,516 |
| 合计 | 40 | 523,351 | 34,961 | 558,312 |

累计 tokens 是所有请求之和，不代表任一时刻的上下文窗口长度。

持久化 `react_audit` 指标：

| Agent / Node | Loops | Max projected input | Max active context | `read_observation` | 显式 obtained | Passive 实际装载 | Micro | Full |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| C1 / BuildGlobalResearch | 5 | 21,377 | 14,727 | 6 | 0 obs / 0 chars | 0 blocks | 0 | 0 |
| C2 / BuildGlobalResearch | 5 | 29,647 | 23,177 | 30 | 0 obs / 0 chars | 0 blocks | 0 | 0 |
| C3 / BuildGlobalResearch | 5 | 21,267 | 16,396 | 0 | 0 obs / 0 chars | 0 blocks | 0 | 0 |
| O4 / BuildGlobalResearch | 3 | 16,468 | 11,428 | 0 | 0 obs / 0 chars | 0 blocks | 0 | 0 |
| O1 / GenerateExpectationConstruction | 3 | 15,862 | 9,252 | 3 | 0 obs / 0 chars | 0 blocks | 0 | 0 |
| A1 / ReviewExpectationConstruction | 4 | 16,017 | 9,213 | 7 | 0 obs / 0 chars | 0 blocks | 0 | 0 |

补充说明：

- 所有 budget report 均为 `mode=normal`，配置值为 128,000 hard budget、115,200 Micro threshold、128,000 Full threshold，没有额外 output/safety reserve。
- Passive budget 在已持久化 loop 中均为 64,000 tokens，但因为没有合格引用进入下一轮，实际 loaded block count 始终为 0。
- O4 最终 action 从引用中选择了 `O1,O19,O10` 作为 Passive candidates，但该 action 同时完成任务，没有下一轮，因此这 3 个 block 没有实际装载。
- `GenerateExpectationDetails` 最后一步曾尝试 retain `O728,O729`，但任务随后失败，未形成已持久化 retained context；因此实际 obtained context 仍为 0。
- reasoning channel 已作为独立字段返回，但本次 reasoning 中没有规范 `【cite:O#】`，无法在真实 smoke 中验证“action 引用优先于 reasoning 引用”的竞争排序。

## 6. Workflow Memory 与 Prompt 分层

真实请求中的 Prompt Block 如下：

- C1/C2/C3/O4 BuildGlobalResearch：`workflow.memory` + `workflow.research_memory`
- O1 GenerateExpectationConstruction：`workflow.memory` + `workflow.research_memory`
- O1 GenerateExpectationDetails：LangSmith system input 中存在 `workflow.memory` + `workflow.research_memory`
- A1 ReviewExpectationConstruction：仅 `workflow.memory`，不含 `workflow.research_memory`
- 所有已观察 ReAct 请求均加载 `workflow.observation-annotations`

Workflow Memory 实际注入大小：

| Node | workflow_memory_chars | assembly estimated_tokens |
| --- | ---: | ---: |
| BuildGlobalResearch | 2 | 442–475 |
| GenerateExpectationConstruction | 4,666 | 1,330 |
| ReviewExpectationConstruction | 3,242 | 1,318 |

说明：BuildGlobalResearch 尚无上游稳定文档，因此 workflow memory 为空对象；后续节点正确接收到 global research 或 expectation shell 控制字段。

## 7. Evidence Ref / Citation 实际表现

- `prompts/workflows/evidence_ref_usage.md` 注册为 `workflow.observation-annotations`，真实 system prompt 已包含其规则；Assembler 中没有重复硬编码规则。
- O4 最终结果产生 46 个 citation tags，46 个全部解析成功，invalid alias 为 0。
- C1、C2、C3、O1 construction、A1 review 的已持久化最终结果 citation tag 均为 0。
- O4 的 Passive candidates 来自 action 引用；reasoning channel 没有有效 citation alias。
- 导出的稳定 `global_research` 投影中没有内联 `cite:O#` 或 `evidence_ref` 字段；由于本次 export 明确标记 annotations 需单独查询，且 hard validators 未运行，不能据此断言 annotation 丢失，但本次 smoke 也没有完整证明“稳定文档可回溯 citation annotation”的验收链路。

结论：新 citation 模块的加载、解析和合法 alias 解析已经在 O4 路径得到真实证明，但 agent 使用率不足，且稳定文档级回溯仍缺一段真实验收证据。

## 8. 根因分析

直接根因：

1. `GenerateExpectationDetails` 每个 task 的 `max_steps=5`。
2. 每个 task 先调用一次 `doxa_get_narrative_report`，返回大量结构化 Observation 目录。
3. Agent 随后需要多次定位和读取具体 narrative block。
4. 到最后一步仍停留在读取材料阶段，没有生成完整 candidate。
5. 两个 task 使用了旧式/快捷 `read_observation` JSON，而不是当前 `tool_calls` action 契约，最终被 runtime 判定为“未返回 final payload、工具调用或委托”。

这是“Tool 结果可读取，但 Agent 在固定步数内不能完成从目录定位到最终写作”的可用性问题，不是 ReAct Memory 的容量问题。

建议下一轮修复顺序：

1. 让 expectation-detail task 的首次 narrative result 直接暴露目标 narrative 的紧凑正文或稳定目标 alias，减少目录探测。
2. 在 runtime action normalization 中明确决定是否兼容 `{"read_observation": ...}`；若不兼容，应在 prompt/schema 中更强约束并给出一次格式纠错机会。
3. 为 final step 预留完成语义：最后一步不得再发读取请求，或在达到 step 上限前提前触发“基于已有材料完成并记录 unknowns”。
4. 增加一条真实回归：三个并行 expectation details 均必须产出 candidate，随后才能启动 Document3。
5. 单独补一条会实际跨轮加载 Passive Carryover 的真实用例，以及一条触发 Full Compaction 的压力用例；本次 smoke 未覆盖这两个分支。

## 9. 未覆盖与环境异常

- Document3 未启动，因此 Document3 的 memory prompt 分层、evidence annotation 和 compaction 表现未验证。
- 本次没有触发 Micro / Full Compaction，因此 `full_compaction.md` 的真实 provider 请求未被运行时覆盖；源码路径存在不等于本次 smoke 已验收。
- 远端 smoke 完成后，SSH 经全局代理再次卡在 `banner exchange`，目标显示为 `UNKNOWN:65535`。按异常熔断停止重复 SSH；随后通过两个精确 `run_id` 的 Postgres 只读查询补抓 brief state，没有轮询、没有全表扫描。
- hard validators 在该导出中为 `not_run`，不能宣称本次通过完整 hard-validator 验收。

## 10. 最终判定

本轮真实 smoke 证明了新版 128k budget、Prompt 分层、独立 reasoning channel、Observation readback 和 citation 解析已进入真实业务请求；但完整 Document1+2+3 链路未通过，主要阻塞点为 `GenerateExpectationDetails` 在有限 steps 内无法从大型 narrative Observation 目录完成最终 candidate。应先修复该 action/finalization 路径，再重跑相同 INTC Document1+2+3 smoke。
