# INTC Document1+2+3 ReAct / Workflow Memory Smoke Test 报告

## 1. 结论

本次真实 smoke **失败**，且相对 2026-07-13 同类运行发生前移回归：流程只完成 `StartTickerInitialization`，在 Document1 的 `BuildGlobalResearch` 被阻塞；Document2、Document3 因缺少合法的 Document1 前置产物而未启动。

直接阻塞原因是某个 `ResearchSection` 最终 payload 返回了 `reviewer_agents: ["", ""]`，无法通过 `list[AgentName]` 校验。除此之外，本次验证还发现一个独立的高风险问题：O4 第二轮真实 provider input 达到 **146,782 tokens**，超过 128,000 hard budget **18,782 tokens（14.7%）**，但没有触发 Micro Maintenance 或 Full Compaction。

因此，本轮不能判定整套 ReAct Memory 重构通过验收。

## 2. 部署与运行范围

| 项目 | 结果 |
| --- | --- |
| 远端 | `doxagent-hk:/root/doxagent` |
| 分支 / Commit | `main` / `102e1e5` |
| Pull | `Already up to date` |
| Build | `dashboard` 与包含 `eval/` 的 DoxAgent 镜像构建成功 |
| 服务 | `dashboard`、`runtime-scheduler`、`revenue-auditor` 已重建；前两者健康 |
| Ticker | `INTC` |
| Tool mode | 真实 provider，`DOXAGENT_RUN_REAL_API_TESTS=1` |
| Smoke container | `doxagent-smoke-intc-20260714-0212`，退出码 `1` |
| source run | `run_c0b301944a744b058d5699c15b8f3d5c` |
| execution run | 未生成 |
| 开始 | `2026-07-13T18:15:12.692434Z`（北京时间 2026-07-14 02:15:12） |
| 结束 | `2026-07-13T18:29:43Z`（北京时间 2026-07-14 02:29:43） |
| 监测方式 | Codex 每 10 分钟定时唤醒；未使用 heartbeat；终态确认后已删除定时任务 |
| LangSmith | stdio LangSmith MCP，project `DoxAgent` |

## 3. Workflow 结果

| Document | 状态 | 已完成节点 | 阻塞点 / 说明 |
| --- | --- | --- | --- |
| Document1 | failed / blocked | `StartTickerInitialization` | `BuildGlobalResearch` 的 `ResearchSection` schema 校验失败 |
| Document2 | not started | 无 | Document1 未产出稳定 `GlobalResearchDocument` |
| Document3 | not started | 无 | 上游前置条件未满足 |

终端错误：

```text
Agent final payload failed schema validation: ResearchSection:
reviewer_agents.0: input_value=''
reviewer_agents.1: input_value=''
```

## 4. LangSmith 节点与 Agent Loop 审计

本次只有 `BuildGlobalResearch` 进入模型阶段。MCP 共定位到 12 次 Bailian/Qwen provider 调用。LangSmith 名称中的 `LOOP#` 是 provider 尝试序号，可能包含重试/回退；实际 ReAct 步数以 metadata 的 `react_step` 为准。

| Agent | 实际 ReAct 调用 | provider input tokens（逐次） | output tokens 合计 | 主要行为 / 最终状态 |
| --- | ---: | --- | ---: | --- |
| C1 | 5 | 6,072；6,071；52,847；17,074；40,596 | 11,410 | 出现两个 task id；后一个 task 完成 4 步并输出带 citation 的完整结果 |
| C2 | 3 | 4,342；5,885；5,999 | 2,822 | 调用 FRED/BLS/BEA/FOMC/Polymarket/TwelveData；未见最终 payload |
| C3 | 2 | 4,561；56,343 | 3,803 | 第二步继续发起 Tavily 调用；未见最终 payload |
| O4 | 2 | 4,846；**146,782** | 4,301 | 第二步生成完整结果，但输入越过 128k hard budget |
| **合计** | **12** | **351,418** | **22,336** | 总 token 373,754 |

可核验的事件时间片：C1 后一任务 step 1 为 `18:20:36Z–18:20:50Z`，step 2 为 `18:20:53Z–18:23:24Z`，step 3 为 `18:27:21Z–18:27:53Z`，step 4 为 `18:27:53Z–18:29:20Z`。整个 smoke 在 `18:29:43Z` 以 schema failure 退出。

所有可见请求均正确注入：

- `workflow.memory`
- `workflow.research_memory`
- `workflow.observation-annotations`
- `max_steps=10`
- 标准 citation 示例 `【cite:O1】`
- 标准 `read_observation` tool-call schema

但本次真实轨迹未实际调用 `read_observation`，因此“精确回读”路径未获得本轮运行证据。

## 5. ReAct Memory 指标

| 指标 | 实测结果 | 判定 |
| --- | ---: | --- |
| Micro Maintenance | 0 次 | O4 已超过 micro/full 阈值却未触发，不通过 |
| Full Compaction | 0 次 | O4 146,782 input tokens 时仍未触发，不通过 |
| Passive Carryover blocks | 0 | 预算边界未被压力测试 |
| Provider reasoning citation | 0 | 本轮 reasoning channel 未形成 passive 来源 |
| C1 显式 retained / obtained 后实际进入下轮 | 34 blocks，约 20,370 JSON chars | 在 C1 step 3、4 中稳定携带 |
| O4 最终声明 retained | 7 blocks | 完成后无下一轮，未形成可观察 carryover |
| C1 后一任务 action citation | 87 个标签（未按 alias 去重） | 标准格式工作 |
| C3 action citation | 13 个标签 | 标准格式工作 |
| O4 action citation | 22 个标签 | 标准格式工作 |
| 全部可见 action citation | 122 个标签 | 无裸 alias / 多括号变体验证样本 |

### 5.1 上下文预算异常

O4 第二步 Fresh Observations 约 **355,281 chars**，最终 provider 统计为 **146,782 input tokens**。这说明当前运行时的字符估算与 Qwen 实际 tokenizer 差异过大，且 hard-budget 判定没有使用最终 provider token 数形成保护闭环。Fresh Observation 可以绕过 115,200 Micro 阈值和 128,000 Full 阈值。

这不是“模型支持更大 context”可以掩盖的问题：当前产品契约明确将单次 ReAct 输入限制为 128k。

### 5.2 Passive Carryover

所有可见输入中的 passive blocks 均为 0。原因是 provider reasoning channel 中没有可归一化的 observation citation；action citation 虽存在，但对应任务多数在该轮完成，或下一轮由显式 retained observations 提供上下文。因此本轮只能证明“没有错误携带”，不能证明 64k/96k 动态预算与完整 block 装载逻辑在真实压力下正确。

## 6. Workflow Memory 表现

`BuildGlobalResearch` 的 `workflow_memory` 实际内容为 `{}`（2 chars）。这是初始化早期没有上游稳定文档时的预期表现。Prompt 分层注入正确，但本轮没有推进到 Document2/3，无法验证跨节点/跨文档的 workflow memory 投影和消费质量。

远端 working-memory 聚合（按 run_id 精确过滤）：

| content_type | 条数 | payload JSON 总字符数 |
| --- | ---: | ---: |
| `global_research_agent_result` | 4 | 2,175,959 |
| `agent_result_schema_failed` | 2 | 710,311 |

合计 6 条、约 **2.89 MB**。这是持久化 audit 体积，不等于每轮 LLM 输入，但说明失败结果仍包含较大 payload，后续应继续确认 audit 是否需要保留完整正文。

## 7. Evidence Ref 与事件时间标记

- 可见模型输出统一使用 `【cite:O#】`，没有观察到旧快捷 `{"read_observation": {...}}`。
- action 中共扫描到 122 个 citation 标签；structured/text 镜像未在本报告中重复计数。
- 本轮没有裸 `O1`、方括号变体或多 alias 单标签的真实输出样本，因此兼容 normalizer 只能由自动测试覆盖，不能由本次 smoke 证明。
- 可见输出中 `occurred_at` 和 `published_at` 事件标记均为 0；说明 Agent 并未实际使用新事件时间标注能力。
- 远端数据库仅存在 `working_memory_entries`；`raw_tool_results`、`observation_blocks`、`citation_annotations`、`time_annotations` 对应迁移未落地。因而 citation/time annotation 的持久层计数和精确 alias 去重无法复核。

## 8. 根因分析

### 8.1 直接阻塞根因

某个 `ResearchSection` 最终 payload 将 `reviewer_agents` 写成两个空字符串，违反 `ResearchSection.reviewer_agents: list[AgentName]`。workflow 在 `Document1BuilderMixin._research_section_from_result()` 的 schema validation 阶段正确拒绝了该 payload，没有把非法文档写入 Blackboard。

本次 LangSmith MCP 按 source run 定位到的 12 个 LLM 输出中，两个可见完整 final payload（C1、O4）均为 `reviewer_agents: []`；C2/C3 没有可见 final payload。代码中的 max-steps fallback 也固定输出空列表。因此，产生 `["", ""]` 的具体模型调用/内部重试没有出现在可检索 trace 中，属于**可观测性缺口**，不能在缺少证据时归因给某一 Agent。

### 8.2 独立高风险根因

上下文预算以近似字符估算做维护决策，没有在发送前用与 provider 足够一致的 tokenizer 或保守上界复核。Fresh Observations 大 payload 使 O4 实际输入达到 146,782 tokens，而 compaction 计数仍为 0。

### 8.3 部署完整性问题

镜像/代码已更新，但远端数据库没有 ReAct observation annotation migration。运行时只能把大结果写入 working memory，无法提供 observation/citation/time 的持久审计证据。这会直接削弱故障复盘与指标验收。

## 9. 最终判定与建议修复顺序

**判定：不通过。** 本轮验证不是网络或限额导致的偶发失败，而是 schema 合约失败；同时存在 hard context budget 未被执行和 annotation migration 缺失。

建议按以下顺序处理后重跑：

1. 在 final payload 接收边界规范化 `reviewer_agents`：丢弃空字符串，并补齐对应 trace/event，使 schema-failed payload 可追溯到 task/provider attempt。
2. 修复预算计算：发送前以 provider tokenizer 或保守 token 上界执行 115,200/128,000 阈值；Fresh Observations 不得绕过 maintenance。
3. 在远端应用 observation/citation/time migration，并用有界聚合确认表与索引存在。
4. 重新运行 INTC Document1+2+3；必须推进至 Document3，且真实覆盖 `read_observation`、Passive Carryover 和至少一次受控的 maintenance 场景，方可完成验收。
