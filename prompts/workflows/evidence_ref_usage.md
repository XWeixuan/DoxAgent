+++
kind = "prompt_block"
block_type = "workflow"
id = "workflow.observation-annotations"
name = "Observation Annotations"
version = "2026.07.12"
applicable_agents = ["O1", "O2", "O3", "O4", "A1", "A2", "C1", "C2", "C3"]
+++

# Observation Annotations

当前 `AgentTask` 中的原始材料使用 `O1`、`O2` 等 alias。

## Citation

当正文中的具体事实、数字或原文结论直接来自某个 Observation 时，在对应句末标注：

```text
【cite:O1】
```

多个 Observation 共同支持时分别标注：

```text
【cite:O1】【cite:O3】
```

要求：

* 只使用当前上下文真实存在的 `O#`，不得编造。
* Citation 必须紧跟其实际支持的句子。
* 不要用 Citation 为自己的推测或延伸判断背书。

在内部 `reasoning` channel 中，只要正在依据某个 Observation 的具体内容进行判断、思考或推断，也应标注对应 alias。

## Event Time

对具有明确时间的事件型陈述，在不编造的前提下尽可能标注：

```text
【occurred_at:<事件发生时间点或周期>】
【published_at:<信息公开时间点>】
```

例如：

```text
公司公布2026年第二季度收入增长12%。【occurred_at:2026-Q2】【published_at:2026-07-10】【cite:O1】
```

时间只使用当前输入中明确存在的信息。不得把抓取时间或当前时间当作事件时间；无法确定时省略，可只标注一种时间。

Citation、`occurred_at` 和 `published_at` 相互独立，均放在对应句子的标点之后。标签属于非阻塞标注，不得影响任务完成或输出结构。
