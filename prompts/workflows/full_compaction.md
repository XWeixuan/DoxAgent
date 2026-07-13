+++
kind = "prompt_block"
block_type = "workflow"
id = "workflow.full_compaction"
name = "Full Compaction"
version = "2026.07.12"
manual_only = true
+++

# Full Compaction

你是当前 AgentTask 的同一个主 Agent。本次请求只执行当前任务内部的 Memory State 压缩维护，不调用工具，不输出最终业务结果，也不改写任何原始 Observation。

目标是在保留任务认知连续性、关键 insight 和证据可恢复性的前提下，充分减少后续 Active Context，使 runtime 重新计算后的实际输入低于 Full Compaction 阈值。优先处理体积大、重复、已经完成作用或当前不再关键的内容。

执行规则：

- 合并重复或高度重叠的 Working Synthesis；`REVISE Sx` 必须给出替换后的完整内容，已经被取代的 block 可以 `DROP`。
- 合并重复的 Research Agenda；已经解决的问题可以关闭，当前非关键且无法继续推进的问题可以 `DEFER`。
- 审查 Retained Observations：
  - `KEEP_LOADED`：下一轮或最终输出仍必须直接看到原文；
  - `INDEX_ONLY`：保留 alias、note 和 reason，原文可在需要时重新读取；
  - `DROP`：内容重复、失效或已无后续用途。
- 仅在执行路径确实需要变化时更新 Plan；`plan_update` 必须是替换后的完整计划。
- 尚未首次处理的 Fresh Observations 必须受到保护。

只返回 `maintenance_action_schema` 要求的 JSON。`compaction_reasoning_summary` 应简要说明主要上下文占用来源、采取的压缩动作，以及压缩后为何仍能继续完成当前任务。
