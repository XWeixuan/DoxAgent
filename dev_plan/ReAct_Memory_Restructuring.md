# DoxAgent Task 内 ReAct Memory 重构方案

## 一、重构范围

本次只重构**单个 AgentTask 内部、多个 ReAct loop 之间的记忆继承与上下文管理机制**。

暂不改动：

* Blackboard 长期业务状态；
* Document1 / Document2 / Document3 跨节点状态；
* agent 私有长期记忆；
* 跨 run 经验检索；
* 现有 `EvidenceRef` 业务契约；
* W1 / W2 等 single-shot runtime worker。

当前系统中，ReAct task 使用同一个 `Scratchpad` 保存 plan、task ledger、trajectory、tool result、compaction summary 等，再将这些内容重新组装进下一轮 prompt。

问题是，现有 `Scratchpad.entries` 同时承担了：

1. runtime 真实执行轨迹；
2. agent 下一轮可见上下文；
3. tool result 存储；
4. compaction 处理对象。

这导致审计历史、原始 observation 和 agent memory 混在同一个结构中。`microcompact()` 会修改旧 observation，full compaction 还会删除原始 tool/delegation entry。

本次重构的第一原则，就是把这些职责彻底拆开。

---

# 二、总体架构

重构后的单 task ReAct 系统分为四个层级：

```text
Runtime Audit Layer
└── Immutable Task Event Log

Observation Data Layer
├── Raw Tool Result Store
├── Observation Parser / Indexer
└── Observation Block Store

Agent Memory Layer
├── Working Synthesis
├── Research Agenda
├── Retained Observations
├── Plan
└── Recent Reasoning Summary

Agent Context Layer
└── Active Context View
```

四层之间的核心边界是：

> **Event Log 保存“实际发生了什么”；**
> **Observation Store 保存“工具实际返回了什么”；**
> **Agent Memory 保存“agent 当前认为哪些结论、问题和原始材料值得继续使用”；**
> **Active Context View 决定“下一轮模型此刻看到什么”。**

---

# 三、Immutable Task Event Log

## 1. 定位

Immutable Task Event Log 是给 ReAct runtime、审计、恢复和 eval 使用的**不可变执行事实日志**。

它不是 agent memory，也不是 prompt context。

它只追加，不修改，不删除。

## 2. 保存内容

每个 task 内发生的事件都写入 Event Log：

* task started；
* model action；
* `reasoning_summary`；
* `plan_update`；
* synthesis update；
* research update；
* retain observation request；
* tool request；
* 完整 ToolResult；
* skill result；
* delegation result；
* observation parsing result；
* pointer validation result；
* warning；
* memory update application result；
* full compaction request 和结果；
* pre-final challenge；
* final output；
* failure / timeout。

## 3. Agent 可见性

**默认不可见。**

Agent 不应该直接读取：

* 完整历史 action；
* 全部旧 reasoning summary；
* 所有 tool result；
* 被 DROP 的 synthesis 历史版本；
* 已 RESOLVE 的 agenda 历史；
* observation parser 内部信息；
* pointer hash；
* compaction 或 context selection 内部决策。

Agent 只能看到由 Active Context Assembler 从 Event Log、Observation Store 和 Agent Memory 中生成的当前视图。

## 4. Runtime 权限

只有 Harness / runtime 可以：

* 写入 Event Log；
* 读取完整 Event Log；
* 从 Event Log 恢复 Task Memory State；
* 执行审计和 eval；
* 定位某次 tool call；
* 重建 observation block。

Agent 不能：

* 改写历史事件；
* 删除历史事件；
* 伪造 tool result；
* 修改已发生的 tool call；
* 修改 pointer 对应原文。

## 5. 与现有 Scratchpad 的关系

现有 `Scratchpad.entries` 应改造成 append-only Event Log。

需要取消：

* `microcompact()` 对旧 entry 原文的覆盖；
* full compaction 对 tool/delegation entry 的删除。

Compaction 只能改变 Agent Memory 的当前 materialized state 和 Active Context View，不能改变 Event Log。

---

# 四、Observation Data Layer

Observation Data Layer 负责保存和定位工具实际返回的内容。

它也不是 agent memory。

## 1. Raw Tool Result Store

每次 tool call 返回后，完整 ToolResult 原样保存。

当前 `ToolResult` 已经包含：

* `output`
* `output_summary`
* `raw`
* `evidence_refs`
* `error`

不需要新增另一套 evidence 数据模型。

Raw Tool Result Store 的职责是：

* 保存完整原始结果；
* 支持 observation block 重建；
* 支持精确原文回看；
* 保留现有 `EvidenceRef`；
* 为 pointer 提供唯一真实来源。

## 2. Observation Parser / Indexer

ToolResult 返回后，Harness 根据结果形态，将其解析为一组可引用的 Observation Blocks。

这一步必须是：

* 确定性的；
* 低成本的；
* 可测试的；
* 不依赖额外无上下文 LLM 总结节点；
* 不判断当前业务上是否重要。

它只负责回答：

> ToolResult 中有哪些自然语义单元？
> 每个单元如何被稳定定位？

## 3. Observation Block Store

每个 Observation Block 在 runtime 内至少保存：

```text
block_id
tool_call_id
parent_block_id
locator
content
context_envelope
content_hash
block_type
evidence_ref_ids
metadata
```

这些字段是 runtime 内部实现，不要求 agent 输出，也不进入最终业务 schema。

---

# 五、原文指针的实现

## 1. 指针的基本原则

原文指针必须同时满足：

* 能精确恢复原文；
* 对 agent 可读；
* 对 runtime 稳定；
* 不因 prompt 截断或排序变化而改变；
* 能携带父级上下文；
* 能继续关联已有 `EvidenceRef`。

建议同时维护两类 ID。

### Runtime 内部 ID

```text
oblk_<hash>
```

用于数据库、校验和精确定位。

### Agent 可见 Ref

```text
obs_<tool_call_id>::<structural_locator>
```

例如：

```text
obs_tc12::/results/2
obs_tc13::section/MD&A/paragraphs/12-14
obs_tc14::/quarterlyReports/0
obs_tc15::rows/2026-06-20..2026-07-08
obs_tc16::narrative/N2/event/E3/proposition/P4
```

Agent 只使用可见 ref。

Harness 内部将其解析到稳定 block ID。

---

## 2. 结构优先切块

不能统一按固定字符数或 token 数切块。

正确顺序是：

1. 识别原始结构；
2. 按自然语义单元切块；
3. 只有自然单元过大时，才做二次切分；
4. 每个子块都保留父级上下文。

### JSON

按 JSON path 和自然对象切块：

```text
/quarterlyReports/0
/quarterlyReports/1
/results/2
/events/3
```

避免：

* 每个数字单独一个块；
* 整个几十万字符 JSON 一个块。

### 搜索结果

每条 result 是一级块：

```text
/results/0
/results/1
```

长正文再按段落建立子块：

```text
/results/1/paragraphs/0-3
/results/1/paragraphs/4-7
```

### Markdown / HTML / SEC / 财报正文

优先按：

* 标题层级；
* section；
* paragraph；
* list；
* table；

生成层级结构。

例如：

```text
section/Management Discussion
section/Management Discussion/Revenue
section/Management Discussion/Revenue/paragraphs/4-7
```

### 表格

按逻辑行组、期间或报告期切分，同时重复必要上下文：

* 表名；
* 行名；
* 列名；
* period；
* unit；
* currency。

不能给 agent 一个没有表头的纯数字块。

### OHLCV / 时间序列

使用范围指针，而不是文本块：

```text
rows/2026-06-20..2026-07-08
rows/2026-07-01..2026-07-03
```

Agent 如需精确分析，可重新读取指定窗口或调用确定性计算工具。

---

## 3. Context Envelope

每个 Observation Block 在展示时应附带确定性生成的简短上下文，只保留 agent 有参考价值的字段，移除无关 metadata。

例如：

```text
Tool: alpha.financial_statements
Period: 2026Q2
Section: Income Statement
Path: /quarterlyReports/0
Evidence: evidence_xxx
```

长文档：

```text
Document: META 10-Q 2026Q2
Section: Management Discussion > Revenue > Advertising
Paragraphs: 12-14
```

DoxAtlas：

```text
Run: run_xxx
Narrative: N2
Event: E3
Proposition: P4
```

Context Envelope 由 Harness 生成，不要求 agent 填写。

---

## 4. Agent 输出的 Observation Index

Agent 不能只返回 ref，还应为保留的 observation 建立轻量索引：

```json
{
  "retain_observations": [
    {
      "ref": "obs_tc13::section/MD&A/paragraphs/12-14",
      "note": "管理层对利润率改善来源的原文解释。",
      "reason": "用于支撑 Working Synthesis 中利润率改善主要来自产品组合和成本控制的判断。"
    }
  ]
}
```

这组 `ref + note + reason` 是 Observation Index。

三个字段职责：

* `ref`：机器可验证的原文位置；
* `note`：该原文客观讲了什么；
* `reason`：为什么后续推理或最终写作仍需要它。

`note` 和 `reason` 不是正式证据内容。

精确事实、数字、原文和引用仍以 ref 对应的 Observation Block 和原有 `EvidenceRef` 为准。

## 5. 软校验

* ref 有效、note/reason 缺失：仍可保留，记录 warning；
* ref 无效：拒绝该项，记录 warning；
* 单个 retain item 错误不得阻塞 task；
* agent 不得通过 note 改写原文；
* retained observation 只能引用当前 task 已存在的 block。

---

# 六、Tool Observation Policy

不能为每个工具分别发明一套 memory schema。应在 ToolDescriptor 或独立 Observation Policy Registry 中，为工具声明展示策略。

当前 ToolDescriptor 只有简单的 `compactable`，不足以表达真实结果类型。

建议三种策略。

## 1. Inline

适用于：

* 短 JSON；
* 少量配置；
* 小型指标结果；
* 已经高度聚合的结果。

Agent 下一轮直接看到完整结果。

## 2. Indexed

适用于：

* 长新闻；
* SEC filing；
* 财报正文；
* DoxAtlas narrative；
* 搜索结果；
* 长研究文档。

Agent 看到：

* observation outline；
* 初始召回块；
* 每块的 ref；
* `read_observation` 使用方式。

## 3. Recomputable

适用于：

* OHLCV；
* 大型时间序列；
* 大型财务表；
* peer dataset。

完整 raw 数据不长期进入 prompt。

Agent 可以：

* 查看当前相关视图；
* 读取指定范围；
* 调用确定性计算；
* 将结论写入 Working Synthesis；
* retain 精确范围。

---

# 七、真实 ToolResult Profiling

Observation Policy 不能由 Codex 凭工具名猜测。

应先基于真实 run 做 Tool Observation Profiling。

## 1. 样本来源

优先从 LangSmith 历史 traces 中读取真实成功 ToolResult。

每个工具尽量覆盖：

* 常规结果；
* 大结果；
* 空结果；
* partial result；
* 不同参数导致的不同 shape。

历史样本不足时，再进行受控调用。

## 2. 每个 Tool 的 Profile

记录：

```text
tool_name
真实 output shape
典型大小
极端大小
自然语义单元
原生 ID
必须保留的父级上下文
建议 pointer 形式
inline / indexed / recomputable
超大单元二次切分方式
evidence_ref 关联方式
```

## 3. 开发策略

先将工具映射到通用 adapter：

* JSON object；
* list / search result；
* text / markdown / HTML；
* table；
* time series；
* domain hierarchy。

只有通用 adapter 无法正确表达时，才增加 tool-specific override。

DoxAtlas 很可能需要专用 hierarchy policy，因为其 run / narrative / event / proposition / source ID 具有业务意义。

## 4. Fixture

真实 ToolResult 应保存为脱敏 fixture，用于测试：

* 指针稳定性；
* 1:1原文恢复；
* parent context；
* JSON path；
* 表格表头；
* 日期范围；
* pointer validation；
* 不同版本 payload 兼容。

---

# 八、Agent Memory Layer

Agent Memory Layer 是给 agent 使用的过程记忆。

它与 Event Log 的最大区别是：

> Event Log 保存全部历史；
> Agent Memory 只保存当前仍有研究价值的 materialized state。

Agent Memory 包含五部分。

---

## 1. Working Synthesis

保存当前仍然有效、最终报告可以直接利用的 insight。

回答：

> 到目前为止，我们已经形成了什么结论？

更新方式：

```text
ADD
REVISE S2
DROP S1
```

Harness 负责维护当前有效版本。

Agent 不需要每轮重写全部 synthesis。

### Agent 可见性

Agent 始终看到：

* 当前有效 synthesis blocks；
* block ID；
* 与其关联的 retained observation refs。

Agent 默认看不到：

* 被 DROP 的 block；
* REVISE 前的旧版本；
* update parser 内部历史。

这些历史只在 Event Log 中保留。

---

## 2. Research Agenda

保存会影响任务完整性、可信度或丰富度的未解决问题。

回答：

> 当前还不知道什么？

支持：

```text
OPEN
REVISE Q2
RESOLVE Q1
MERGE Q2 Q4
DEFER Q3
```

### Agent 可见性

Agent 看到：

* active question；
* deferred question；
* question ID；
* 当前简短状态。

Agent 默认看不到：

* 已 RESOLVE 的完整历史；
* 旧版本问题描述；
* 被 MERGE 掉的问题正文。

这些保存在 Event Log。

### 初始化边界

Workflow 提供 Research Frame，定义最低业务覆盖维度。

Agent 根据 Research Frame 和真实材料建立、调整具体 Agenda。

Research Frame 不能写死完整问题清单，避免僵化。

---

## 3. Retained Observations

Retained Observations 是 agent 在前序 loop 中主动选择、后续仍需继续使用的原始材料集合。

每条 Retained Observation 包含两部分：

### Observation Index

```text
ref
note
reason
```

用于说明：

* 原文在哪里；
* 原文大致讲什么；
* 为什么后续仍需要它。

### Loaded Original Block

由 Context Assembler 根据 ref 从 Observation Block Store 中加载的：

* 原始文本；
* 原始数值；
* 原始 JSON 对象；
* 表格片段；
* 时间序列区间；
* Context Envelope；
* 关联 `EvidenceRef`。

### Agent 可见性

在正常上下文状态下，Agent 始终看到：

* 每条 Retained Observation 的 Observation Index；
* Context Assembler 当前为该条目加载的完整原文块；
* 原文块的 Context Envelope；
* 与该原文关联的 evidence ref 信息。

只要某个原文块处于 `loaded` 状态，就必须完整展示给 agent，不能只展示 note 或截断后的摘要。

Agent 不会直接看到：

* Observation Store 中没有被 retain 的其他块；
* block 内部 hash；
* parser metadata；
* pointer validation 内部结果。

### 默认加载规则

正常情况下，所有 Retained Observations 都保持 `loaded`，即：

> retain 不只是保存索引，也意味着该原文块会持续进入后续 Active Context。

只有在触发 Full Compaction，并由 agent 明确判断某些原文暂时无需持续占用上下文时，才能将其降级为 `index_only`。

`index_only` 状态下：

* Observation Index 继续可见；
* 原文块暂时不进入 Active Context；
* Agent 可以通过 `read_observation` 重新加载；
* 原文和 pointer 不会被删除。

---

## 4. Plan

保留现有 plan 机制。

回答：

> 下一阶段具体做什么？

最新非空 `plan_update` 替换当前 plan。

Plan 可以同时推进多个 Agenda，不要求一题一轮。

Agent 始终看到当前 plan。

历史 plan 只保存在 Event Log。

---

## 5. Recent Reasoning Summary

保留现有 `reasoning_summary`。

回答：

> 为什么前面的 loop 进行了这些判断和行动？

Active Context 固定保留**最近两个已完成 loop**的 Reasoning Summary：

```text
reasoning_summary[-2]
reasoning_summary[-1]
```

如果当前 task 只完成了一轮，则只展示最近一轮。

更早的 Reasoning Summary：

* 仍完整保存在 Event Log；
* 默认不进入 Active Context；
* 不参与普通 loop 的上下文继承。

Reasoning Summary 不承担：

* 长期 insight；
* Research Agenda；
* Plan；
* 原始 observation。

长期有效内容应进入 Working Synthesis 或 Research Agenda，而不是依赖旧 reasoning history。

---

# 九、Active Context View

Active Context View 是下一轮模型真正消费的 prompt context。

它是临时投影，不是存储。

## 1. 组装顺序

```text
Task / Output Contract
Research Frame
Working Synthesis
Active / Deferred Research Agenda
Current Plan
Recent Reasoning Summary（最近两个 loop）
Retained Observations
Fresh Observations
Warnings
```

其中 Retained Observations 必须包含：

* Observation Index；
* 当前处于 loaded 状态的完整原文块；
* Context Envelope；
* evidence ref 信息。

## 2. Agent 始终可见

* 当前 task；
* output contract；
* Research Frame；
* 当前 Working Synthesis；
* active/deferred Agenda；
* 当前 Plan；
* 最近两个 loop 的 Reasoning Summary；
* 所有 Retained Observation Index；
* 所有当前 loaded 的 Retained Observation 原文块；
* 最新 Fresh Observations。

## 3. Agent 按需可见

通过 `read_observation`：

* observation outline；
* 指定 block；
* parent block；
* child block；
* JSON path；
* 时间范围；
* 某条 search result；
* DoxAtlas hierarchy item；
* 被 Full Compaction 降级为 `index_only` 的 retained observation。

## 4. Agent 默认不可见

* 完整 Event Log；
* 所有历史 tool result；
* 全部 raw payload；
* resolved agenda；
* dropped synthesis；
* 所有旧 plan；
* 两轮以前的 reasoning summary；
* parser 内部 metadata；
* block hash；
* compaction audit；
* context selection 内部评分。

---

# 十、Fresh Observation 生命周期

```text
ToolResult 返回
      ↓
写入 Event Log 与 Raw Store
      ↓
Parser 生成 Observation Blocks
      ↓
Policy 生成 Fresh Observation View
      ↓
下一轮 Agent 消化
      ↓
┌────────────────┬────────────────────┬─────────────────┐
│形成 insight     │保留重要原文         │无后续价值        │
│Synthesis Update│Retain Observation  │退出 Active View │
└────────────────┴────────────────────┴─────────────────┘
```

Fresh Observation 默认只强展示一轮。

在 agent 首次看到并处理该 Fresh Observation 之前：

* 不得因 micro compaction 被隐藏；
* 不得因 full compaction 被降级为 index only；
* 不得只保留 summary 替代原始内容。

Agent 处理后，未 retain 的内容：

* 不再自动进入下一轮 prompt；
* 但不会删除；
* 仍可通过 `read_observation` 重新读取。

---

# 十一、Agent Action Protocol

保留现有：

* `reasoning_summary`
* `plan_update`
* `tool_calls`
* `skill_calls`
* `delegations`
* `final_payload`
* `is_complete`
* `completion_reason`

新增三个可选过程更新：

```json
{
  "synthesis_update": [
    "ADD：……",
    "REVISE S2：……"
  ],
  "research_update": [
    "OPEN：……",
    "RESOLVE Q1：……"
  ],
  "retain_observations": [
    {
      "ref": "obs_tc13::section/MD&A/paragraphs/12-14",
      "note": "……",
      "reason": "……"
    }
  ]
}
```

规则：

* 不要求每轮全部输出；
* 只有实际变化时才更新；
* 全部软校验；
* memory update 失败不影响 tool call 和 final；
* Harness 应记录 update failure；
* final output schema 不包含这些过程字段。

Full Compaction 使用独立的 maintenance action，不与普通业务 action 混用。

---

# 十二、Pre-final Research Challenge

研究型 task 首次准备 `is_complete=true` 时，Harness 可以进行一次轻量 readiness check。

检查：

* Working Synthesis 是否足以支撑 output contract；
* Research Frame 是否得到覆盖；
* critical Agenda 是否已 RESOLVE、明确无发现或合理 DEFER；
* 是否存在大量 tool call 但几乎没有 synthesis；
* 是否存在 retained observation 但未被用于当前判断；
* 是否过早结束；
* 是否明显只做了 tool-result 复述；
* Known Events 候选异常少时，是否检查覆盖不足问题。

若不足，由同一个主 agent 进入最多一次 challenge loop。

Event Log 记录 challenge，但 challenge 本身不成为长期 memory。

---

# 十三、Compaction 的新边界与执行方式

## 1. 不参与 Compaction 的数据

以下内容永远不被 compaction 修改或删除：

* Immutable Task Event Log；
* Raw Tool Result；
* Observation Block Store；
* Observation Pointer；
* 原有 `EvidenceRef`；
* 已发生的 tool call 和 agent action。

Compaction 只处理：

* Agent Memory 当前 materialized state；
* Active Context View；
* Retained Observation 的加载状态。

---

## 2. Context Budget

每次准备发起模型请求前，Harness 计算当前可用输入预算：

```text
context_budget =
model_context_window
- reserved_output_tokens
- system_prompt_tokens
- tool_schema_tokens
- safety_reserve_tokens
```

其中：

* `reserved_output_tokens` 按当前 output schema 和节点配置预留；
* `safety_reserve_tokens` 用于避免 token 估算误差和临时 prompt 增长；
* 不允许直接以模型最大 context window 作为全部输入预算。

Active Context 的 projected token 数基于实际待发送 prompt 计算。

---

## 3. 正常状态

当：

```text
projected_context_tokens <= 75% * context_budget
```

不触发任何 compaction。

Active Context 正常包含：

* 全部 Working Synthesis；
* active/deferred Agenda；
  -当前 Plan；
* 最近两个 loop 的 Reasoning Summary；
* 所有处于 loaded 状态的 Retained Observations；
* 当前 Fresh Observations；
  -必要 warnings。

正常情况下不限制 Synthesis 字数，也不主动卸载 Retained Observation 原文。

---

## 4. Micro Context Maintenance

当：

```text
projected_context_tokens > 75% * context_budget
```

Harness 先执行确定性的 Micro Context Maintenance。

它不调用 LLM，也不修改 Agent Memory。

具体操作固定为：

1. Active Context 不包含完整 trajectory，只保留最近两个 loop 的 Reasoning Summary；
2. 只保留当前最新 Plan，不展示历史 plan；
3. 不展示已 RESOLVE 或被 MERGE 的 Agenda；
4. 不展示被 DROP 或已被 REVISE 替代的旧 Synthesis 版本；
5. 移除已经被 agent 消化、但未 retain 的旧 Fresh Observations；
6. Indexed 类型的新 Fresh Observation 只展示 outline 和初始 selected blocks；
7. 删除重复 warning，只保留最近五条不同 warning；
8. 不处理当前尚未被 agent 首次消费的 Fresh Observation；
9. 不卸载任何 Retained Observation 原文；
10. 不修改 Event Log、Raw Store 或 Observation Store。

Micro Context Maintenance 执行后，重新计算 projected token 数。

---

## 5. Full Compaction 触发条件

完成 Micro Context Maintenance 后，如果仍满足任一条件，则触发 Full Compaction：

```text
projected_context_tokens > 85% * context_budget
```

或：

```text
projected_context_tokens + 当前必要 Fresh Observation
> context_budget
```

或：

```text
当前 Active Context 已无法为一次正常业务 action
保留 reserved_output_tokens
```

Full Compaction 不是后台脚本整理，也不是无上下文的弱模型总结。

它是：

> **下发给同一个主 agent、在当前 AgentTask 和当前 ReAct loop 内执行的一次专用 memory maintenance 任务。**

---

## 6. Full Compaction 的执行流程

### 第一步：暂停当前普通业务 action

Harness 不立即让 agent继续调用工具或输出 final。

### 第二步：构造 Full Compaction Request

该请求只包含：

* 当前 Working Synthesis；
* active/deferred Research Agenda；
  -当前 Plan；
* 最近两个 loop 的 Reasoning Summary；
* 所有 Retained Observation Index；
* 当前 loaded 的 Retained Observation 原文块；
* 各部分 token / char 占用；
* 当前 compaction 原因；
* 当前 output contract 的剩余任务；
* 当前 Fresh Observation 的存在状态。

Full Compaction Request 不包含：

* 完整 Event Log；
* 所有历史 ToolResult；
* 已删除的旧 synthesis；
* resolved agenda 历史；
* parser 内部 metadata。

当前尚未被 agent 首次消费的 Fresh Observation属于受保护内容，Full Compaction 不得删除、摘要化或降级。

### 第三步：Agent 输出 Maintenance Action

Full Compaction 使用独立的轻量 action：

```json
{
  "compaction_reasoning_summary": "当前上下文主要被重复 synthesis 和多个已完成用途的原文块占用。",
  "synthesis_update": [
    "REVISE S2：将 S2、S4 中重复的利润率结论合并为……",
    "DROP S4"
  ],
  "research_update": [
    "MERGE Q2 Q5：……",
    "DEFER Q4：当前数据不可得，在最终输出中披露。"
  ],
  "retained_observation_update": [
    {
      "ref": "obs_tc13::section/MD&A/paragraphs/12-14",
      "action": "KEEP_LOADED",
      "reason": "最终报告仍需引用该管理层原文。"
    },
    {
      "ref": "obs_tc14::/results/2",
      "action": "INDEX_ONLY",
      "reason": "内容已经充分进入 Synthesis，暂时无需持续加载原文。"
    },
    {
      "ref": "obs_tc11::/results/5",
      "action": "DROP",
      "reason": "与其他 observation 重复，且不再影响当前判断。"
    }
  ],
  "plan_update": [
    "继续解决剩余的关键 Agenda",
    "完成后基于压缩后的 Synthesis 生成最终输出"
  ]
}
```

Retained Observation maintenance 支持：

```text
KEEP_LOADED
保留 Observation Index，并继续在 Active Context 展示完整原文。

INDEX_ONLY
保留 Observation Index，但暂时不加载完整原文；需要时可重新 read。

DROP
从 Agent Memory 的 Retained Observations 中移除；
原始 Observation 仍保留在 Observation Store 和 Event Log。
```

### 第四步：Harness 应用 Maintenance Action

Harness：

* 软解析 maintenance action；
* 更新当前 Synthesis；
* 更新 Agenda；
* 更新 Retained Observation 加载状态；
* 更新 Plan；
* 将完整 maintenance 过程写入 Event Log；
* 不修改任何原始 observation。

### 第五步：重建 Active Context

Context Assembler 使用压缩后的 Agent Memory 重新生成 Active Context。

### 第六步：继续当前 loop

Full Compaction 不占用一个新的业务 loop。

完成 compaction 后，Harness 在**同一个 ReAct loop**中再次调用主 agent，执行正常业务 action。

因此 Full Compaction 会增加一次模型调用，但不会把研究流程机械地增加一个新的 loop step。

---

## 7. Full Compaction 的边界

Full Compaction 可以：

* 合并重复或高度重叠的 Synthesis；
* DROP 已被新结论明确替代的 Synthesis；
* 合并重复 Agenda；
* 将低优先级问题 DEFER；
* 将部分 Retained Observation 降级为 index only；
* 删除 Agent Memory 中已经没有继续使用价值的 retained entry；
* 更新 Plan。

Full Compaction 不能：

* 修改 Retained Observation 原文；
* 用摘要替换唯一原始证据；
* 删除 Raw ToolResult；
* 删除 Observation Block；
* 删除 Event Log；
* 改写 EvidenceRef；
* 隐藏尚未被首次消费的 Fresh Observation；
* 为节省 token 而改变业务结论；
* 直接输出最终业务结果。

---

## 8. Full Compaction 失败处理

如果 maintenance action：

* 缺失；
* 无法解析；
* 包含无效 ref；
* 未能降低上下文占用；

Harness 记录 warning，并允许同一 task 最多重试一次 Full Compaction。

如果重试后仍超过硬 context budget，runtime 执行最小安全 fallback：

1. 保留全部 Working Synthesis；
2. 保留 active/deferred Agenda；
3. 保留当前 Plan；
4. 保留最近两个 Reasoning Summary；
5. 保留尚未首次消费的 Fresh Observation；
6. 所有 Retained Observation 继续保留 Observation Index；
7. 按原文块体积从大到小，将 Retained Observation 暂时降级为 `index_only`，直到 prompt 可发送；
8. 不执行 DROP；
9. 不修改任何原始数据。

该 fallback 只用于避免模型请求因 context 超限失败，不承担业务价值判断。

---

## 9. Final 阶段的加载规则

当 agent准备生成最终输出时：

* Working Synthesis 全量加载；
* active/deferred Agenda 全量加载；
* 最近两个 Reasoning Summary 加载；
* 所有 `KEEP_LOADED` Retained Observation 原文加载；
* 所有与当前有效 Synthesis 直接关联的 `INDEX_ONLY` Observation 优先重新加载；
* 仍超过 Full Compaction 阈值时，先执行 Full Compaction，再生成 final。

最终输出不能仅依据 Observation Index 的 note 和 reason；需要精确事实或原文时，应读取对应原始 block。

---

# 十四、memory.md Prompt 

`/prompts/workflows/memory.md` 只教 agent 如何使用 Memory Layer。

# 十五、验收标准

## Boundary

* Agent 无法直接修改或读取完整 Event Log；
* Event Log 不因 compaction 改变；
* Raw ToolResult 始终可恢复；
* Agent Memory 与 runtime audit 明确分离；
* Full Compaction 只改变 Agent Memory 和 Active Context。

## Pointer Fidelity

* ref 可稳定定位；
* 可 1:1恢复原文或原始值；
* 表格保留 header / period / unit；
* JSON path 正确；
* DoxAtlas 原生 ID 不丢失；
* OHLCV 范围可精确读取；
* retained ref 可关联已有 EvidenceRef。

## Agent Visibility

* Agent 只看到当前 materialized Memory State；
* resolved/dropped 历史默认不可见；
* Fresh Observation 默认只展示一轮；
* Agent 始终看到所有当前 loaded 的 Retained Observation 原文块；
* index-only observation 可按需重新读取；
* full Event Log 不进入正常 prompt；
* Reasoning Summary 固定保留最近两个 loop。

## Insight Retention

* 前序 loop 形成的有效 insight 能进入最终输出；
* 被 REVISE 的旧判断不会错误复活；
* final 主要基于 Working Synthesis，而不是重新解释 trajectory；
* Full Compaction 不应造成关键 insight 丢失。

## Research Behavior

* Tool call 由 Agenda、Plan 和 Reasoning 驱动；
* 一个 loop 可以推进多个 Agenda；
* Tool result 能转化为 Synthesis；
* Agent 会主动发现遗漏、反例和可信度问题；
* Known Events 不会因少量初始结果过早结束。

## Cost

* 不要求每轮重写完整 memory；
* Agent 只输出增量 insight、问题变化和必要 Observation Index；
* 原文通过 ref 保留，不重复生成；
* 不新增独立 LLM extraction 节点；
* Full Compaction 仅在高水位触发；
* memory 格式错误不阻塞业务流程。

## Compaction

* 低于 75% context budget 不触发 compaction；
* 超过 75% 先执行确定性 Micro Context Maintenance；
* Micro Maintenance 后超过 85% 才触发 Full Compaction；
* Full Compaction 由同一个主 agent 在同一 loop 内执行；
  -尚未首次消费的 Fresh Observation 不得被压缩；
* Full Compaction 失败时有明确安全 fallback；
* Event Log、Raw ToolResult 和 Observation Store 永不被 compaction 修改。

---

# 十六、最终数据流

```text
Agent Action
      ↓
Immutable Task Event Log

Tool Call
      ↓
Raw ToolResult Store
      ↓
Observation Parser / Indexer
      ↓
Observation Blocks
      ↓
Fresh Observation View
      ↓
Agent 消化
      ↓
┌─────────────────────┬──────────────────────┬────────────────────────┐
│Working Synthesis     │Research Agenda       │Retained Observations   │
│已经知道什么          │还不知道什么           │Index + Loaded Originals│
└─────────────────────┴──────────────────────┴────────────────────────┘
      ↓
Plan + 最近两个 Reasoning Summary
      ↓
Active Context View
      ↓
Context Budget Check
      ↓
正常执行 / Micro Maintenance / Full Compaction
      ↓
下一轮 Agent
```

本次重构的核心不是增加更多 memory schema，而是明确三条边界：

> **Runtime 负责完整记录；**
> **Observation Layer 负责高保真存储和定位；**
> **Agent Memory 只保存当前仍有研究价值的结论、问题和原始材料。**

最终让 ReAct 从：

```text
tool result 驱动的循环
```

转变为：

```text
问题驱动研究
→ insight 增量积累
→ 原始材料持续可见或可回看
→ 必要时由主 agent 主动维护 memory
→ 最终报告整合
```
