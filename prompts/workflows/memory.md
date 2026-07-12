+++
kind = "prompt_block"
block_type = "workflow"
id = "workflow.memory"
name = "ReAct Task Memory"
version = "2026.07.12"
applicable_agents = ["O1", "O2", "O3", "O4", "A1", "A2", "C1", "C2", "C3"]
+++

# Task Memory

你在同一 `AgentTask` 的多个 ReAct loop 中共享 Task Memory。它保存当前有效的研究结论、未解决问题、后续计划和需要继续使用的工具材料，帮助后续 loop 延续前面的工作。

每轮只更新本轮新增、修正或失效的内容。

## Working Synthesis

Working Synthesis 是你当前对任务的核心认知状态，保存当前仍然成立、可被最终输出直接利用的 insight。

每个 Synthesis block 应写明：

* 明确的分析结论；
* 形成该结论的主要推理；
* 对结论有实质影响的边界或不确定性。

使用：

* `ADD`：新增独立 insight；
* `REVISE Sx`：完整替换现有 Sx，写出修改后的完整内容；
* `DROP Sx`：该 insight 已失效或被取代。

结论依赖工具材料时，使用 `【cite:O#】` 标明对应 Observation。

## Research Agenda

Research Agenda 保存完成当前 task 前仍需解决的重要问题，包括影响结果完整性、可信度或分析深度的研究缺口。

一个 Plan 或一组并行工具可以同时推进多个 Agenda。

使用：

* `OPEN`：新增重要问题；
* `REVISE Qx`：完整替换现有 Qx；
* `RESOLVE Qx`：已有足够依据回答；
* `MERGE Qx Qy`：合并重复问题；
* `DEFER Qx`：当前无法解决，保留为不确定性。

问题解决后，将有价值的答案写入 Working Synthesis。

## Observations

Observation 是工具返回内容经过 Harness 划分后形成的可读取材料块，每个块使用 `O#` alias 标识。

Fresh Observations 是上一轮工具刚返回、等待本轮处理的材料。当前展示的内容只代表已加载的 Observation Block，不一定是完整 ToolResult。需要具体内容时，使用 `read_observation` 读取对应 alias 或其子 alias。

只能使用当前输入中提供的 `O#`，不得自行编造 alias。

## Retained Observations

Retained Observations 保存本轮你阅读到的observations中，任何对任务而言有价值的、后续 loop 推理或最终写作仍需直接使用的 Observation。

每个保留项包含：

* `alias`：对应的 `O#`；
* `note`：该材料客观包含什么；
* `reason`：后续为什么仍需使用。

## Plan

Plan 表示下一阶段准备采取的完整行动方案，应服务于当前 Agenda 和输出目标，并可同时包含多个行动或并行工具调用。

`plan_update` 会完整替换当前 Plan。更新时写出下一阶段仍需执行的完整计划；计划不变时省略。

## Reasoning Summary

`reasoning_summary` 简洁说明本轮为什么做出这些判断和行动，包括：

* 新 Observation 对当前判断的影响；
* Synthesis 或 Agenda 的变化；
* 当前 Plan、工具调用或完成任务的原因。

它用于连接本轮行动逻辑。

## 每轮处理

每轮结合当前 Working Synthesis、Research Agenda、Retained Observations、Plan、最近 Reasoning Summary、Fresh Observations 和 warnings：

1. 处理新材料和已有 warning；
2. 更新本轮形成或修正的 insight；
3. 更新已解决或新增的重要问题；
4. retain 后续仍需使用的材料；
5. 更新必要的 Plan；
6. 决定继续调用工具、委托或提交最终结果。

没有实质变化时省略对应 memory update。能在一次 loop 中推进多个问题时，不要机械拆分。

Full Compaction 仅在 runtime 明确注入专用 maintenance prompt 时执行。
