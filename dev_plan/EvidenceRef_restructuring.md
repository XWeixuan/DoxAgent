# DoxAgent EvidenceRef 与事件时间标注整体重构方案

## 一、重构定位

本次不是在现有 `EvidenceRef` 上继续增加字段或附加正文 citation，而是：

1. 移除当前分散在模型、工具、Blackboard、workflow、reviewer、prompt 和 ReAct Memory 中的 legacy EvidenceRef 机制；
2. 将事实引用重构为一条轻量关系：

```text
正文位置
→ Observation Block
→ Raw ToolResult
→ 原始来源内容
```

3. 在同一套正文 Annotation Runtime 中增加独立的事件时间标注；
4. 整个模块作为全局、通用、旁路式审计能力运行，不参与 workflow 路由、patch 准入、promotion 或 objection 阻断。

重构后的 Evidence 不再是一个需要在业务对象之间传递的来源对象，而是：

> **正文中的某段内容与某个 Observation Block 之间的 runtime 索引关系。**

---

# 二、核心原则

## 2.1 移除 legacy EvidenceRef

不再保留一个来源级 `EvidenceRef` 对象，也不再保留：

```text
evidence_id
source_type
source_id
title
summary
retrieval_metadata
confidence
citation_scope
```

作为跨系统通用合同。

来源信息直接来自：

```text
Observation Block
→ ToolResult
→ output / raw / context_envelope / metadata
```

需要知道 URL、标题、provider、报告期或数据单位时，从对应 Observation 和 ToolResult 中读取，不再额外维护一份来源对象。

## 2.2 Observation 是唯一原文索引底座

Evidence 模块不再建立自己的 Source Store、Fragment Store 或 Evidence Store。

继续复用现有：

```text
RawToolResultStore
ObservationBlockStore
ObservationParser
ObservationCallIndex
```

Observation Block 已经具有：

```text
block_id
tool_call_id
parent_block_id
locator
content
context_envelope
content_hash
block_type
metadata
```

这些字段足以支持：

* 精确原文读取；
* JSON path 定位；
* 表格行定位；
* 段落定位；
* 时间序列区间定位；
* DoxAtlas narrative/event/proposition 定位；
* 内容完整性校验。

本次只删除其中的 `evidence_ref_ids`，不再让 Retained Observation 或 Observation Parser 关联旧 EvidenceRef。

## 2.3 Agent 只使用一套 Observation Alias

Agent 不再看到：

```text
obs_tc3::/results/2/paragraphs/0-3
```

Runtime 在一个 AgentTask 内为每个可见 Observation Block 分配稳定短 alias：

```text
O1
O2
O3
```

同一个 `O#` 在该 AgentTask 的全部 ReAct loop 中保持稳定，并统一用于：

```text
Retain Observation
正文 Citation
read_observation
Working Synthesis 与原文的关联
Full Compaction
```

Agent 不再维护两套索引。

## 2.4 全链路非阻塞

Annotation 系统不参与任何业务 hard gate。

以下问题全部只能记录 warning，不得阻断 AgentResult、Blackboard 写入或 workflow：

* 没有 citation；
* 没有时间标签；
* alias 无效；
* 标签格式错误；
* 时间无法解析；
* Observation 不存在；
* Annotation 持久化失败；
* Citation 与正文关系不准确；
* 整份报告没有可解析标注。

## 2.5 Citation 与时间相互独立

允许正文只标注其中任意一种：

```text
【cite:O2】

【occurred_at:2026-Q2】

【published_at:2026-06-25】

【occurred_at:2026-Q2】【cite:O2】
```

Citation 不要求时间，时间也不要求 Citation。

---

# 三、目标架构

```text
Tool Call
   ↓
Raw ToolResult Store
   ↓
Observation Parser
   ↓
Observation Block Store
   ↓
Task-local Observation Alias Registry
   ↓
Agent Context：O1 / O2 / O3
   ↓
Agent 自然正文 + 可选标签
   ↓
Global Text Annotation Runtime
   ├── Citation Annotation
   └── Time Annotation
   ↓
Audit Persistence / Event-Time Query
```

系统只保留三层。

## 3.1 Observation Data Layer

保存真实工具结果和具体原文块。

## 3.2 Agent-facing Alias Layer

将内部 Observation Block 映射为 Agent 易于使用的 `O#`。

## 3.3 Text Annotation Layer

将 Agent 输出的正文标签解析为：

```text
正文 span → Observation Block
正文 span → occurred_at / published_at
```

不再增加 Evidence Source Layer 或 EvidenceRef 对象。

---

# 四、Observation Alias 重构

## 4.1 Alias Registry

新增 task-local：

```text
ObservationAliasRegistry
```

内部关系：

```text
O1 → block_id
O2 → block_id
block_id → O1
```

Alias 在 Observation Block 首次进入 Agent Context 时生成，并在整个 AgentTask 内保持不变。

Runtime 内部仍然可以使用：

```text
block_id
tool_call_id
locator
```

但不得再向 Agent 暴露。

## 4.2 Fresh Observation 展示

当前：

```text
ref: obs_tc1::/results/0
```

调整为：

```text
alias: O1
```

例如：

```text
Observation O1
Tool: tavily.search
Path: /results/0

<原始内容>
```

Agent 只需要记住 `O1`。

## 4.3 Retained Observation 协议

调整前：

```json
{
  "ref": "obs_tc1::/results/0",
  "note": "……",
  "reason": "……"
}
```

调整后：

```json
{
  "alias": "O1",
  "note": "……",
  "reason": "……"
}
```

Runtime 收到后立即解析为内部状态：

```text
observation_block_id
note
reason
load_state
```

Retained Observation 内部不保存 agent-facing alias 作为主键，而保存 `block_id`。每次生成 Active Context 时再通过 Alias Registry 渲染 `O#`。

## 4.4 read_observation

现有输入：

```json
{
  "ref": "obs_tc1::/results/0"
}
```

调整为：

```json
{
  "alias": "O1"
}
```

Runtime 自行完成：

```text
O1 → block_id → parent / child / content
```

该工具继续服务 ReAct Memory，但不因新的 Citation 系统增加调用场景，也不在 prompt 中鼓励 Agent 为审查 Evidence 主动读取原文。

## 4.5 Working Synthesis

当前 Synthesis 通过正文中的复杂 Observation ref 识别与原文的关联。

调整为识别：

```text
【cite:O1】
```

Runtime 在应用 Synthesis Update 时，将 `O1` 解析为 `block_id` 并保存在内部：

```text
SynthesisBlock.observation_block_ids
```

Agent 仍只看到 `O#`。

## 4.6 Full Compaction

Maintenance Action 同样使用 alias：

```json
{
  "alias": "O1",
  "action": "INDEX_ONLY",
  "reason": "……"
}
```

Runtime 根据 alias 找到 retained block。

---

# 五、正文标签协议

正文继续使用自然语言或 Markdown，不转为 JSON claim block。

只支持三个独立句末标签：

```text
【cite:O1】
【occurred_at:2026-06-25】
【published_at:2026-06-26】
```

## 5.1 Citation

```text
公司将毛利率改善归因于产品组合变化和成本下降。【cite:O1】
```

多个 Observation：

```text
公司披露与行业数据均显示新增产能将在下半年释放。【cite:O1】【cite:O4】
```

不得输出：

```text
【cite:O1,O4】
```

也不得输出：

```text
【cite:obs_tc1::/results/0】
```

## 5.2 occurred_at

表示事件实际发生的时间点或周期，可为空。

支持最小必要格式：

```text
2026-06-25
2026-06
2026-Q2
2026-H2
2026-06-01/2026-06-30
2026-06-25T16:00:00-04:00
```

## 5.3 published_at

表示信息正式公开、公告或发布的时间点，可为空。

支持：

```text
2026-06-25
2026-06-25T16:00:00-04:00
```

## 5.4 标签位置

标签放在对应句子的标点之后：

```text
公司在季度业绩会上上调全年收入指引。【occurred_at:2026-06-25】【published_at:2026-06-25】【cite:O2】
```

一个句子包含多个独立事件时，应拆成多个句子，不增加复杂的句内起止标签。

## 5.5 时间标注要求

在不编造的前提下，Agent 应尽可能为事件型陈述增加时间标签。

不要求标注：

* 纯分析观点；
* 没有时间依据的推测；
* 静态公司背景；
* 长期结构性判断。

---

# 六、Global Text Annotation Runtime

## 6.1 全局统一处理

Annotation Runtime 位于通用 AgentResult 后处理边界，不写进某个 Document workflow。

所有 AgentResult 完成 schema validation 后，统一经过：

```text
TextAnnotationProcessor
```

处理器递归扫描 payload 中的字符串字段。

它不需要知道当前节点是：

```text
Document 1
Document 2
Document 3
O1
C1
A1
W1
```

只需要知道：

```text
run_id
task_id
result_id
payload_path
当前 Task 的 Alias Registry
```

## 6.2 Citation Annotation

解析：

```text
【cite:O2】
```

生成：

```text
CitationAnnotation
```

最小字段：

```text
annotation_id
run_id
task_id
result_id
payload_path
text_hash
span_start
span_end
observation_block_id
created_at
```

不保存：

```text
EvidenceRef
source_type
source_id
confidence
citation_scope
```

需要查看来源时，按 `observation_block_id` 回查 Observation 和 ToolResult。

## 6.3 Time Annotation

解析：

```text
【occurred_at:...】
【published_at:...】
```

生成：

```text
TimeAnnotation
```

最小字段：

```text
annotation_id
run_id
task_id
result_id
payload_path
text_hash
span_start
span_end
occurred_at
published_at
created_at
```

两项时间可以只存在一项。

## 6.4 不新增独立 EventFact 对象

V1 不再额外建立：

```text
EventFactRecord
EventFact ID
Event Fact Status
Event Fact Occurrence
```

带时间标注的正文 span 本身就是最小事件索引单位：

```text
正文句子
payload_path
occurred_at
published_at
```

后续需要事件聚合或去重时，再基于 Time Annotation 和正文内容构建派生索引，不在本轮增加新的维护对象。

---

# 七、正文保存与下游消费

## 7.1 Runtime 保存

Runtime 保留原始 Agent 输出用于 audit，同时生成无标签正文：

```text
raw_tagged_text
plain_text
CitationAnnotation
TimeAnnotation
```

核心业务文档保存 `plain_text`，不在数据库正文中永久写入 task-local `O#`。

## 7.2 时间标签重新渲染

后续 Agent 消费上游正文时，通用 Context Assembler 根据 Time Annotation 将时间标签重新渲染到正文：

```text
公司上调了全年指引。【occurred_at:2026-06-25】【published_at:2026-06-25】
```

因此，时间标签可以继续帮助后续 Agent：

* 识别事件；
* 理解先后顺序；
* 判断旧消息和新消息；
* 区分历史事实与未来事件；
* 构建 Known Events；
* 判断运行期消息的新颖性。

该能力是全局文本渲染规则，不属于某个 workflow 的定制逻辑。

## 7.3 Citation 不默认向下游渲染

Citation Annotation 默认只进入审计侧。

下游 Agent 不自动看到上游 citation，也不能根据 citation 主动读取 Observation。

只有在未来明确启用 Evidence Review Mode 时，runtime 才可以：

1. 将相关 Observation 加入当前 Task；
2. 为其分配当前 Task 的新 `O#`；
3. 重新渲染 citation。

该模式当前保留能力但不启用。

---

# 八、ToolResult 与 Observation 调整

## 8.1 ToolResult

删除：

```text
ToolResult.evidence_refs
ToolResult.to_evidence_ref()
```

工具只需要返回：

```text
tool_name
status
output
output_summary
raw
error
```

## 8.2 Provider

删除 Provider 中创建 EvidenceRef 的逻辑。

Provider 应确保真正有价值的来源坐标保留在：

```text
output
raw
```

例如：

* 搜索结果保留 URL、标题、发布时间和正文；
* SEC 结果保留 filing、form、period、document URL；
* DoxAtlas 保留 narrative、event、proposition 和 source ID；
* 行情数据保留 symbol、interval、period、timestamp 和数值。

Observation Parser 再将这些必要信息写入现有：

```text
context_envelope
metadata
content
locator
```

不新增来源对象。

## 8.3 Observation Block

删除：

```text
evidence_ref_ids
```

调整后：

```text
ObservationBlock
├── block_id
├── tool_call_id
├── parent_block_id
├── locator
├── content
├── context_envelope
├── content_hash
├── block_type
└── metadata
```

Agent view 额外由 Alias Registry 渲染：

```text
alias: O1
```

但 alias 不成为 Observation Block 的持久字段。

---

# 九、移除 legacy EvidenceRef

本次需要整体排查并删除以下内容。

## 9.1 Domain Model

删除：

```text
EvidenceRef
EvidenceSourceType
```

删除所有业务对象中的：

```text
evidence_refs
source: EvidenceRef
resolution_evidence_refs
required_evidence
```

涉及但不限于：

* AgentResult；
* ToolResult；
* ResearchSection；
* RealizedFact；
* PriceReaction；
* VariableStatus；
* KnownEvent；
* BlackboardPatch；
* Objection；
* WorkingMemoryEntry；
* reviewer finding；
* resolver output；
* delegation result。

## 9.2 ReAct Runtime

删除：

* `_evidence_refs()` 聚合；
* AgentResult evidence hydration；
* 模型手写 EvidenceRef 校验；
* `_agent_output_evidence_ref()`；
* evidence gap fallback；
* final payload evidence fallback；
* ToolCall 到 EvidenceRef 的转换。

## 9.3 ReAct Memory

删除：

* `ObservationBlock.evidence_ref_ids`；
* Fresh Observation 中的 evidence ref 展示；
* Retained Observation 与 EvidenceRef 的关联；
* memory prompt 中“不能修改 EvidenceRef”的表述；
* Event Log 中专门记录 evidence ref ID 的逻辑。

Retained Observation 只与 Observation Block 关联。

## 9.4 Blackboard 与 Context

删除：

* Blackboard Evidence 聚合与 upsert；
* `doxagent.evidence_refs` 新写入；
* Context Snapshot 顶层 `evidence_refs`；
* EvidenceDigest；
* Working Memory 与 Objection 的 EvidenceRef 传播；
* Commit/Patch 的 Evidence 非空要求；
* scoped context 中的 Evidence 清理和 hydration 分支。

历史表可暂时保留为 legacy read-only 数据，但新 run 不再写入。

## 9.5 Reviewer 与 Promotion

删除 EvidenceRef 作为 reviewer 结构化输出的一部分。

删除：

* supported/unsupported 结果对 evidence refs 的要求；
* EvidenceAssessment；
* Evidence 不足 promotion blocker；
* market evidence ref 类型判断；
* Evidence 缺口 objection；
* resolver 的 evidence request/evidence ref 关闭逻辑。

Reviewer 仍可审查业务内容，但不能通过 Evidence 模块阻断 workflow。

## 9.6 Prompt 与 Skill

对目前的prompt/skill仓库进行排查，删除所有要求 Agent 进行以下动作的相关段落或语句（局部移除，不要完整删除整个文件）：

* 输出完整 EvidenceRef；
* 维护 evidence ID；
* 在 patch 中提供 Evidence；
* 为 Known Event 选择 EvidenceRef；
* 根据 EvidenceRef 判断是否 promotion；
* 遇到缺失 EvidenceRef 时创建 objection；
* 将 partial source clue 填入 EvidenceRef。

统一替换为：

* Observation Memory 规则；
* 自然正文 Annotation 规则；
* 不编造 citation alias；
* 尽可能标注事件时间。

## 9.7 Eval

删除 EvidenceRef schema 和数组非空类 hard gate。

替换为非阻塞质量指标：

```text
Citation coverage
Citation resolution rate
Invalid alias rate
Time annotation coverage
Invalid time rate
Observation locator fidelity
```

---

# 十、持久化

## 10.1 Observation 持久化

为了支持正文回溯，需要将现有 task-local Observation 持久化。

复用现有字段：

```text
run_id
task_id
block_id
tool_call_id
parent_block_id
locator
content
context_envelope
content_hash
block_type
metadata
```

由于当前 `block_id` 只在单个 task 内生成，数据库主键可使用：

```text
(run_id, task_id, block_id)
```

不需要再创建 Evidence ID。

## 10.2 Annotation 持久化

新增两类轻量记录：

```text
citation_annotations
time_annotations
```

它们只保存正文位置与 Observation/时间关系，不复制原文。

## 10.3 Raw ToolResult

完整 Raw ToolResult 按：

```text
run_id
task_id
tool_call_id
```

持久化或归档。

常规 workflow 和 Dashboard 不读取完整 raw payload。只有审计详情页或定位原文时读取。

## 10.4 Legacy 数据

原有 `evidence_refs` 表和历史 JSON：

* 停止新写入；
* 不参与新 workflow；
* 暂时保留供旧 run 查看；
* 稳定后再决定是否删除迁移和数据库表。

---

# 十一、非阻塞边界

Annotation Runtime 必须满足：

1. 在 AgentResult 已经成功后再执行；
2. 使用独立的 best-effort 处理；
3. 不与 Blackboard 核心写入共享必须成功的事务；
4. 任何异常不得向 workflow 抛出；
5. 不触发 Agent 重试；
6. 不创建 objection；
7. 不修改 result status；
8. 不影响 patch、promotion、checkpoint 和恢复。

具体降级行为：

```text
无标签
→ 直接通过

无效 O#
→ 忽略 citation，记录 warning

无效时间
→ 忽略该时间，保留正文

span 定位失败
→ 不创建 annotation

Observation 缺失
→ citation unresolved 或忽略

持久化失败
→ 记录 audit warning，业务继续

Annotation Runtime 整体不可用
→ 相当于本次没有标注
```

---

# 十二、统一 Prompt

新增一个全局共享 Prompt Block，而不是分别为 Document 1/2/3 编写 Evidence 规则。

```text
## Observation and text annotations

当前可引用原始材料使用 O1、O2 等短 alias。

需要跨 loop 保留材料时，在 retain_observations 中填写对应 alias。

正文中的事实直接来自某个 Observation 时，可在句末标注：
【cite:O#】

对具有明确时间的事件型陈述，在不编造的前提下尽可能标注：
【occurred_at:<时间点或周期>】
【published_at:<时间点>】

规则：
- Retain、Citation 和 read_observation 使用同一套 O#。
- 只使用当前上下文真实存在的 O#。
- 多个来源分别使用多个 cite 标签。
- 时间未知时省略，不得猜测。
- Citation、occurred_at、published_at 可独立使用。
- 标签缺失或无效不影响任务完成。
```

同时更新 `memory.md`：

* `ref` 改为 `alias`；
* 示例统一改为 `O1`；
* 删除 EvidenceRef 相关描述；
* 明确 alias 由 Harness 管理；
* Agent 不接触内部 locator 和 block ID。

---

# 十三、实施顺序

## Phase 1：Legacy EvidenceRef 拆除

先移除：

* Domain schemas；
* Tool/provider 生成；
* ReAct 聚合；
* Blackboard/context 传播；
* reviewer/promotion blocker；
* prompt/skill；
* eval hard gates。

目标是让核心 workflow 在完全没有 EvidenceRef 的情况下正常运行。

## Phase 2：Observation Alias 统一

实现：

* Task-local `O#` Registry；
* Fresh Observation alias；
* Retain alias；
* read_observation alias；
* Synthesis alias 解析；
* Full Compaction alias；
* 隐藏 `obs_tc...`。

## Phase 3：Global Annotation Runtime

实现：

* `【cite:O#】` parser；
* `occurred_at` parser；
* `published_at` parser；
* payload 字符串递归扫描；
* span 定位；
* plain text；
* Annotation Audit；
* 全部非阻塞降级。

## Phase 4：Observation 与 Annotation 持久化

实现：

* Raw ToolResult；
* Observation Block；
* Citation Annotation；
* Time Annotation；
* 按正文位置查询原文。

## Phase 5：时间标签下游渲染

实现通用 Context Renderer：

* 从 Time Annotation 恢复正文时间标签；
* 后续 Agent 默认可见；
* Citation 不默认向下游传播；
* 不增加 Evidence 读取入口。

## Phase 6：质量评估

增加：

* Citation coverage；
* Citation resolution rate；
* Time annotation coverage；
* invalid alias/time rate；
* Observation locator fidelity；
* workflow non-blocking 回归测试。

---

# 十四、验收标准

## Legacy Removal

* Agent 输出 schema 中不存在 EvidenceRef；
* ToolResult 不再包含 evidence refs；
* Blackboard 文档和 patch 不再携带 evidence refs；
* reviewer 和 promotion 不依赖 Evidence；
* Context Builder 不再聚合 Evidence；
* ReAct Memory 不再关联 EvidenceRef；
* 新 run 不再写入 legacy evidence 表。

## Unified Alias

* Agent 只看到 `O#`；
* Retain、Citation、read_observation 共用 `O#`；
* Agent 不再输出 `obs_tc...`；
* 同一 AgentTask 内 alias 稳定；
* alias 错误不阻塞 task；
* Runtime 能将 alias 精确映射回 Observation Block。

## Citation

* 正文保持自然语言或 Markdown；
* Citation 可从正文 span 回到具体 Observation；
* Observation 可继续回到 ToolResult 原文；
* 不存在额外 Source/Evidence 对象；
* Citation 缺失或失败不影响 AgentResult。

## Time

* 只使用 `occurred_at` 与 `published_at`；
* 两者独立、均可为空；
* Agent 在不编造的前提下尽可能标注事件时间；
* 下游 Agent 能看到重新渲染的时间标签；
* 时间解析失败不影响正文和 workflow。

## Global and Non-blocking

* Annotation Runtime 不包含 Document 1/2/3 特殊分支；
* workflow 不读取 Annotation 决定路由；
* Annotation 不参与 patch、promotion 或 objection；
* Annotation 服务完全关闭时核心系统照常运行；
* 所有错误只进入 audit 和 eval。

---

# 十五、最终数据流

```text
ToolResult
   ↓
Raw ToolResult Store
   ↓
Observation Block
   ├── block_id
   ├── locator
   ├── content
   ├── context_envelope
   └── content_hash
   ↓
Runtime 分配 task-local alias：O1
   ↓
Agent 在同一套 alias 上执行：
   ├── retain_observations: O1
   ├── read_observation: O1
   └── 正文：【cite:O1】
   ↓
Global Annotation Runtime
   ├── Citation：正文 span → Observation Block
   └── Time：正文 span → occurred_at / published_at
   ↓
Observation / Annotation Audit Store
   ↓
原文回溯、事件时序识别、离线质量评估
```

重构后的核心边界是：

> **不再存在需要 Agent 和 workflow 共同维护的 EvidenceRef 对象。**
> **Observation 负责保存和定位真实原文。**
> **统一的 ****`O#`**** 负责 AgentTask 内的全部原文指向。**
> **Annotation Runtime 负责将正文 Citation 和时间标签转成审计索引。**
> **整个模块是全局旁路系统，永远不参与核心 workflow 的成败判断。**
