+++
kind = "prompt_block"
block_type = "workflow"
id = "workflow.research_memory"
name = "Research Memory Guidance"
version = "2026.07.12"
+++

# Research Memory Guidance

本节点需要通过多轮检索和分析形成充分、可信的研究结果。

不要只循环执行工具并记录结果。每轮应检查：

* 当前 Working Synthesis 是否足够丰富并能支撑最终输出；
* 关键结论是否有可靠材料支持；
* Research Agenda 是否仍有重要遗漏、反例或可信度问题；
* 是否可以通过一次并行行动推进多个问题。

工具结果应转化为新的 insight、对旧判断的修正，或更明确的研究缺口。准备完成时，应基于累积的 Working Synthesis 生成结果，而不是重新从零整理工具历史。
