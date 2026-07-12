# DoxAgent Workflow Memory 整体重构方案

## 一、重新审查后的结论

本次重构的核心不是继续对现有 `input_context` 或 `context_snapshot` 做局部瘦身，而是从架构上重新划分以下内容：

1. 什么是稳定业务记忆；
2. 什么是 workflow 控制状态；
3. 什么只是审计留存；
4. 哪些内容最终允许进入 LLM；
5. task 内 ReAct memory 如何与 workflow memory 衔接。

当前项目已经连续完成了 Document3 input context 优化和 task 内 ReAct memory 重构，因此下一阶段不应重新设计节点级输入或 ReAct memory，而应集中解决 workflow 级信息入口的结构问题。

最终目标应定义为：

> 将目前分散在 `input_context`、`context_snapshot`、Blackboard document bucket、checkpoint metadata 和各节点 helper 中的 LLM-visible 信息，统一收敛为一个独立、明确、可审计的 `workflow_memory` 模块；其他内容分别归入 Workflow Control State 和 Audit Plane，不再进入模型上下文。

---

# 二、对上版方案需要修正的地方

## 1. 不能将当前问题简单描述为“生产环境双重注入”

当前 runner 确实保留了两条上下文入口：

* `task.input_context`
* `context_snapshot`

> `context_snapshot` 是一套仍然完整存在、可被启用、可被持久化的平行上下文机制；即使默认路径暂时未使用，它依然造成架构概念重复和未来误用风险，应该在本次重构中被正式替代，而不是继续保留为另一条 LLM-visible 入口。

---

## 2. 不应再以 compact summary 作为 workflow memory 的主体

新的原则应当是：

> 一旦某个稳定文档被当前节点选中，就向 LLM 提供该文档完整的业务正文，而不是用 summary 替代 text；上下文精简主要通过“不加载无关文档”和“删除审计字段”实现，而不是通过压缩正文实现。

---

## 3. “完整文档”不等于原始 Blackboard 对象直接透传

Blackboard 中的业务文档本身嵌套了大量 EvidenceRef：

* Document1 section 含 `evidence_refs`；

* Document2 的 market view、realized facts、price reaction、key variables 都可能包含 `evidence_refs`；

* KnownEvent 的 `source` 本身就是一个完整 EvidenceRef。

因此不能把 `GlobalResearchDocument.model_dump()` 或整个 `belief_state.documents` bucket 直接作为 workflow memory。

应该建立单独的 LLM-facing document view：

> 保留完整业务语义和正文，但删除 EvidenceRef、retrieval metadata、patch wrapper、commit provenance、author/reviewer 等非任务必要信息。

这个 view 不需要继续满足原 Blackboard document 的 Pydantic schema。它是一个独立的模型输入契约，而不是稳定文档的复制品。

---

# 三、当前架构问题的准确定位

## 1. `input_context` 是一个没有明确类型边界的通用容器

当前 `AgentTask` 将全部附加信息放在：

```python
input_context: dict[str, Any]
```

其中可能同时出现：

* task 指令；
* workflow 文档；
* pending patch；
* objection；
* delegation；
  -工具要求；
* ReAct runtime budget；
* retry 信息；
  -内部 skill IDs；
  -节点特有输入。

这导致 `input_context` 既像 task contract，又像 memory，又像 control state，还承担工具和运行参数。

---

## 2. Orchestrator 仍采用“先组装，再删除”的方式

当前 `_task_input_context()` 会先统一加入：

* completed nodes；
* stable document types；
* belief-state key summary；
* pending patches；
* working-memory summaries；
* objections；
* delegations；
* global research context。

然后再通过 Document1、Document2、Document3 三组函数不断 `pop()` 字段。

这带来三个问题：

1. context policy 分散在大量条件分支中；
2. 新节点容易漏删字段；
3. 很难从代码上判断某个节点最终看到了什么。

正确方向应改成：

> 默认无 workflow memory，再根据当前节点 policy 正向选择需要加载的内容。

---

## 3. `ContextBuilder` 混合了多种职责

当前 ContextBuilder 同时负责：

* Blackboard document 加载；

* document scope 选择；

* working-memory 压缩；

* objection/delegation 汇总；

* evidence 收集；

* Document3 runtime context 构建。

这实际混合了：

* workflow memory；
* audit projection；
  -权限控制；
* runtime handoff。

本次重构后，ContextBuilder 不应继续作为一个通用“大上下文构造器”。

---

## 4. ReAct 又单独构建了一套模型输入

当前 ReAct 输入顶层已经包括：

* `react_protocol`
* `task`
* `tool_call_policy`
* `output_contract`
* `available_tools`
* `available_skills`
* `loaded_skills`
* `task_memory`

并在此基础上额外注入 `context_snapshot`。同时，`task.input_context` 又嵌套在 `task` 内。

因此模型可见信息目前缺少明确边界：

```text
task.input_context
context_snapshot
task_memory
```

三者都可能被理解为 memory。

---

# 四、目标架构

建议形成以下逻辑分层：

```text
┌──────────────────────────────────────┐
│ 1. Stable Blackboard Documents       │
│    Document1 / Document2 / Document3 │
└──────────────────────────────────────┘
                     │
                     │ 选择并去除审计字段
                     ▼
┌──────────────────────────────────────┐
│ 4. LLM-visible Workflow Memory View  │
└──────────────────────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────┐
│ 5. Task-local ReAct Memory           │
│    当前 task 内 observations /       │
│    synthesis / agenda / active ctx   │
└──────────────────────────────────────┘
```

旁路保留两层非默认 LLM-visible 状态：

```text
2. Workflow Control State
   checkpoint / pending work /
   objection / delegation / retry state

3. Audit Plane
   AgentResult / working-memory journal /
   commit log / model audit / ReAct audit /
   transaction audit / runtime records
```

其中只有第 4 层是本次新增的统一模块。

---

# 五、各层的明确职责

## 1. Stable Blackboard Document Plane

继续保存完整、经过 promotion 的业务文档：

* Document1；
* Document2；
* Document3。

它们仍然是 workflow 跨节点业务信息的唯一 source of truth。

不需要为了新的 memory view 修改现有稳定文档 schema，也不建议现在迁移 Blackboard 表结构。

---

## 2. Workflow Control State

包括：

* checkpoint；
* active pending work；
* pending patches；
* objections；
* delegations；
* review findings；
* resolution task；
  -节点完成状态和恢复状态。

这些内容用于：

* workflow 编排；
  -恢复；
  -判断当前待处理对象；
* reviewer / resolver 的局部任务。

控制状态不是默认 workflow memory。

只有 policy 明确要求时，才投影为当前任务的 `active_work_item`。普通生成节点不接收 checkpoint history；resolver 也只接收当前待解决范围，而不是整个 workflow control history。

---

## 3. Audit Plane

包括：

* current working memory entries；
* AgentResult 完整返回；
* commit log；
* patch lineage；
* acceptance warning；
* transaction audit；
* model audit；
* ReAct persisted audit；
* tool usage audit；
* dispatch / timeout / retry audit；
* runtime apply audit；
* persistent runtime execution records。

现有 Blackboard 模型已经明确区分了 WorkingMemoryEntry、CommitLogEntry、Patch、Objection、Delegation 和 BeliefState，但这些对象目前仍可能被 ContextBuilder 或 input-context helper 转为模型可见信息。

新的硬性规则是：

> Audit Plane 不得成为 WorkflowMemoryCompiler 的输入源。

这条边界最好通过依赖关系保证，而不仅是靠字段过滤：

* WorkflowMemoryCompiler 可以依赖 StableDocumentReader；
  -可以依赖受限的 WorkflowControlReader；
  -不能依赖 WorkingMemoryRepository；
  -不能依赖 CommitLogReader；
  -不能读取历史 AgentResult 或 runtime audit。

---

## 4. LLM-visible Workflow Memory View

它是根据当前 workflow node、agent、task type 和 output contract，从稳定文档与必要控制状态中临时编译出的只读模型视图。

其特点应为：

* typed；
* bounded；
* node-scoped；
* default deny；
  -没有审计历史；
  -没有 EvidenceRef；
  -没有隐式摘要替换；
  -可追踪来源，但 provenance 留在 Audit Plane。

---

## 5. Task-local ReAct Memory

保持目前已完成的设计：

* observation store；
* retained observations；
* working synthesis；
* research agenda；
* reasoning summary；
* active context；
* compaction。

Workflow memory 是 task 开始时的稳定跨节点输入；task memory 是当前 task 内的动态状态。

二者不能合并，也不能让 task memory 自动成为下一个 workflow node 的 memory。

---

# 六、统一的模型输入结构

建议保留当前 ReAct 顶层协议，只做两项结构调整：

1. `task` 改为更明确的 `task_contract`；
2. 删除 `task.input_context` 和 `context_snapshot`，新增唯一的 `workflow_memory`。

目标结构：

```json
{
  "react_protocol": {},
  "task_contract": {},
  "tool_call_policy": {},
  "output_contract": {},
  "available_tools": [],
  "available_skills": [],
  "loaded_skills": [],
  "workflow_memory": {},
  "task_memory": {}
}
```

## `task_contract`

只保存当前任务本身的静态契约，例如：

```json
{
  "task_id": "...",
  "run_id": "...",
  "ticker": "...",
  "agent_name": "...",
  "workflow_node": "...",
等等
```

`task_directives` 只允许保存当前 task 的明确指令，例如：

* review scope；
* section instruction；
* resolution mode；
  -当前任务边界；
  -业务要求。

它不能再携带 stable documents、workflow history 或 audit record。

---

## 现有 `input_context` 字段的迁移原则

| 当前内容                                  | 新位置                                |
| ------------------------------------- | ---------------------------------- |
| task identity、任务指令                    | `task_contract`                    |
| ReAct max steps、timeout、batch budget  | `react_protocol`                   |
| required tools、tool requirements      | `tool_call_policy`                 |
| output schema 及约束                     | `output_contract`                  |
| Blackboard stable documents           | `workflow_memory.documents`        |
| 当前 resolver/reviewer work item        | `workflow_memory.active_work_item` |
| task 内动态 memory                       | `task_memory`                      |
| audit、retry history、dispatch metadata | Audit Plane，不进模型                   |

短期内内部 `AgentTask.input_context` 可以继续存在，以降低改造风险；但在进入 LLM 前必须被拆解为上述 typed 结构，不能再把整个 dict 直接注入模型。

---

# 七、Workflow Memory View 的核心设计

## 1. `WorkflowMemoryPolicyRegistry`

每种任务注册一个显式 policy。

匹配维度建议包括：

```text
workflow
workflow_node
agent_name
task_type
required_output_schema
```

Policy 决定：

* 允许读取哪些 stable document types；
  -读取最新稳定版本还是指定版本；
  -是否读取一个或多个 Document2；
  -是否需要 active work item；
  -是否需要当前 scoped objection/delegation；
  -是否允许 resolver control data；
  -使用哪一种 document body projector。

具体 document type 组合沿用现有节点设计，不在本次架构方案中重新定义。

---

## 2. Policy 和 Permission 的关系

两者不能合并。

* Permission 表示 agent 是否被授权读取某类内容；
* Policy 表示当前任务是否有必要读取这类内容。

最终可见范围应是：

```text
policy requested scope
∩
agent permission scope
```

没有 policy 的节点默认没有 workflow memory，而不是回退到 generic Blackboard history。

---

## 3. `BlackboardDocumentBodyProjector`

负责把稳定文档转换为 LLM-visible business body。

核心原则：

### 保留

-完整业务正文；
-稳定文档的业务字段；
-必要的业务 ID；
-业务时间；
-用于文档间连接的 expectation/event/item/policy ID；

* Document1 的完整 `ResearchSection.text`。

### 默认删除

* `evidence_refs`；
* EvidenceRef 类型的 `source`；
* `retrieval_metadata`；
* author/reviewer provenance；
* commit IDs；
* patch ID 和 validation status；
* storage wrapper；
* model audit；
* tool call audit；
* context compaction metadata。

不能简单写一个递归的 `if key == evidence_refs` 过滤器，因为一些 EvidenceRef 字段名可能叫 `source`，而其他业务对象也可能有合法的 source 字段。

应为 Document1、Document2、Document3 建立 typed projector，通过字段语义明确决定保留内容。

---

## 4. 正文优先规则

Document1 section 的正文规则应为：

```text
text 是权威正文
summary 只能作为可选辅助字段
summary 不得替代 text
```

最稳妥的做法是：

-始终保留完整 `text`；

* `summary` 可根据 token 情况保留或删除；
  -不能在超出预算时自动退化为 summary；
  -不能对正文做隐式截断。

Document2 和 Document3 的主要信息已经是结构化业务字段，应完整保留其业务字段，只删除 audit/provenance 字段。

---

## 5. 上下文预算的处理方式

新的 workflow memory 不应继续依赖有损 summary compaction。

如果超过预算，应：

-明确产生 `workflow_memory_over_budget`；
-记录是哪几个 document 占用；
-调整该节点的 document selection policy。

不能静默截断正文或回退到 summary，因为这会重新引入不可见的信息损失。

---

# 八、Workflow Control State 的有限投影

建议统一生成：

```json
{
  "active_work_item": {}
}
```

而不是向模型提供：

-整个 checkpoint；
-全部 pending patches；
-全部 objections；
-全部 delegations；
-全部 review history。

普通节点通常没有 `active_work_item`。

Reviewer 可以获得当前被审查的业务对象正文，但无需获得完整 patch wrapper 和 commit history。

Resolver 可以根据 policy 获得：

-当前待修订业务对象；
-相关 objection；
-相关 delegation；
-目标字段或 target path；
-必要的 active patch/control record；
-当前 resolution contract。

即使是 resolver，也只能得到当前 resolution scope，不得读取全局 Blackboard history。

---

# 九、统一编译和组装流程

建议形成以下数据流：

```text
Internal AgentTask
      +
Workflow Checkpoint
      +
Stable Blackboard Documents
      │
      ├── TaskContractBuilder
      │
      ├── WorkflowMemoryPolicyRegistry
      │
      ├── BlackboardDocumentBodyProjector
      │
      └── WorkflowControlProjector
                  │
                  ▼
         WorkflowMemoryCompiler
                  │
          ┌───────┴────────┐
          ▼                ▼
AgentVisibleWorkflowMemory ContextAssemblyAudit
          │                │
          │                └── Audit Plane
          ▼
       LLMInputAssembler
          │
          ▼
       ReAct Harness
```

---

## `WorkflowMemoryCompiler` 输出

### 模型可见输出

```python
AgentVisibleWorkflowMemory(
    policy_id=...,
    documents=...,
    active_work_item=...,
)
```

`policy_id` 是否直接给模型可自行决定；一般只需要放进 audit，不一定占用模型上下文。

### 审计输出

```python
ContextAssemblyAudit(
    policy_id=...,
    source_document_ids=...,
    source_document_versions=...,
    included_blocks=...,
    excluded_field_categories=...,
    control_records_selected=...,
    estimated_tokens=...,
    content_hash=...,
)
```

Audit 中不需要重复保存完整 document body。稳定文档本身已经持久化，只需记录 ID、版本、hash 和选择结果即可复现。

---

# 十、现有模块的调整方向

## `workflows/initialization/orchestrator.py`

当前职责：

-直接创建大块 input context；
-加入文档、patch、history；
-然后按 Document1/2/3 分支删除。

重构后：

-只负责生成 task identity 和 task-specific directives；
-向 compiler 提交 `WorkflowMemoryRequest`；
-不再直接拼装 stable document；
-删除 `_compact_document1/2/3_task_input_context()` 这类负向裁剪逻辑。

---

## `context/builder.py`

建议拆分或替换为：

```text
WorkflowMemoryCompiler
BlackboardDocumentBodyProjector
WorkflowControlProjector
ContextAssemblyAuditor
```

其中 `build_document3_runtime_context()` 应移动到 runtime scheduler 或 persistent runtime 的专用 adapter，不再放在通用 agent context 模块中。

---

## `prompts/assembler.py`

当前 PromptAssembler 自己判断 Document1/2/3 snapshot 的可见形式，并实现另一套 document compaction。

重构后：

-不再判断 workflow node；
-不再压缩 document；
-不再处理 `context_snapshot`；
-只负责把已经编译好的 `task_contract` 和 `workflow_memory` 放入模型输入；
-相同 builder 同时服务 ReAct 和 single-shot 路径。

---

## `agents/runtime/runner.py`

当前 runner：

1.注入 prompt / skill；
2.可选构建 context snapshot；
3.调用 PromptAssembler；
4.再把 task 和 snapshot 交给 ReAct。

重构后应变为：

1.生成内部 AgentTask；
2.构建 `task_contract`；
3.编译 `workflow_memory`；
4.构建 `context_assembly_audit`；
5.将同一份 compiled input 交给 ReAct 或 single-shot；
6.把 audit 独立持久化。

---

## `agents/runtime/react.py`

保留：

* `react_protocol`；
* tool / skill 部分；
* `task_memory`；
  -现有 task 内 memory 逻辑。

调整：

* `task` 改为 `task_contract`；
  -删除 `task.input_context` 透传；
  -删除 `context_snapshot`；
  -新增 `workflow_memory`；
* required tools、runtime budget 等只从其所属 typed contract 读取。

当前 ReAct 成功结果还会在 AgentResult 中保存 `context_snapshot`、model audit、react audit 和 prompt metadata。

这些内容可以继续保留用于审计，但应归入 Audit Plane，且不能再通过 working memory 或 ContextBuilder 返回给后续 LLM。

---

# 十一、Audit Plane 的处理策略

短期不建议立即迁表或大规模修改 Blackboard persistence。

可以先做逻辑上的分类：

```text
working_memory entry        -> AUDIT_ONLY
commit_log                  -> AUDIT_ONLY
agent_result                -> AUDIT_ONLY
model/react/tool audit      -> AUDIT_ONLY
runtime execution record    -> AUDIT_ONLY
```

随后逐步将当前 `working_memory` 重命名为更准确的概念，例如：

```text
WorkflowAuditJournal
```

但命名和物理迁移应该放在 LLM-visible boundary 稳定之后。

最重要的不是名字，而是：

> WorkflowMemoryCompiler 从类型和依赖层面就无法读取 Audit Plane。

---

# 十二、推荐实施顺序

## Phase 1：建立契约与真实输入观测

先不改变模型输入。

新增：

* `TaskContractView`
* `AgentVisibleWorkflowMemory`
* `WorkflowMemoryPolicy`
* `ContextAssemblyAudit`

记录当前真实请求中：

* top-level 字段；
* input_context 字段来源；
* context_snapshot 是否存在；
  -各字段字符数和 token；
  -重复内容；
* evidence / patch / audit 泄漏情况。

---

## Phase 2：Shadow 编译

新 compiler 与旧 input-context 逻辑并行运行：

-旧输入继续发送给 LLM；
-新 compiler 只生成 shadow view；
-比较两者业务正文完整性；
-确认新 view 没有 evidence、patch history 和 audit；
-验证所选 document body 与 Blackboard 原正文一致。

---

## Phase 3：接入新的顶层输入

ReAct 请求切换为：

```text
task_contract
workflow_memory
task_memory
```

暂时保留旧 `AgentTask.input_context` 作为内部兼容字段，但不再整体注入模型。

---

## Phase 4：迁移 orchestrator

逐步把当前节点 helper 中的内容分类迁移到：

* task directives；
* document policy；
* control-state projection；
* tool policy；
* runtime protocol。

删除大 context 后再 `pop()` 的逻辑。

---

## Phase 5：移除 context snapshot

确认所有执行模式都使用相同 compiler 后：

-移除 model-visible `context_snapshot`；
-删除 PromptAssembler 中的 snapshot document compaction；
-删除 `AgentContextSnapshot` 中与 workflow memory 重复的字段；
-将 snapshot provenance 替换为 `ContextAssemblyAudit`。

---

## Phase 6：清理 Audit Plane 和 runtime adapter

最后再进行：

* working memory 语义重命名；
* audit repository 的物理拆分；
* Document3 runtime context builder 移出通用 ContextBuilder；
  -清理 legacy context helpers。

---

# 十三、关键验收标准

## 输入结构

模型顶层只能有：

```text
react_protocol
task_contract
tool_call_policy
output_contract
available_tools
available_skills
loaded_skills
workflow_memory
task_memory
```

不得再出现：

```text
task.input_context
context_snapshot
belief_state_summary
working_memory_summary
```

作为通用模型入口。

---

## 文档完整性

对于 policy 选中的文档：

* Document1 完整 `text` 必须存在；
  -不得只剩 summary；
  -不得静默截断正文；
* Document2/3 的完整业务字段必须存在；
  -文档内容必须能对应到稳定 Blackboard document。

---

## 审计隔离

非 resolver 请求中不得出现：

* `evidence_refs`
* EvidenceRef 类型的 source；
* retrieval metadata；
* patch ID；
* commit log；
* working-memory entry；
  -历史 AgentResult；
* model audit；
* ReAct audit；
* tool audit；
* transaction audit；
* runtime execution record。

Resolver 只允许看到 policy 指定的当前 control scope。

---

## 可见性控制

-没有 policy 的任务默认无 workflow memory；

* policy 和 permissions 必须同时通过；
  -新增节点缺少 policy 时测试直接失败；
  -不能回退到 generic Blackboard history。

---

## 去重

同一业务内容不得同时存在于：

* `task_contract.task_directives`
* `workflow_memory`
* `task_memory`

同一稳定文档不得通过 summary、context pack、document bucket 三种形式重复注入。

---

## 执行一致性

* ReAct 与 single-shot 使用同一份 `task_contract` 和 `workflow_memory`；
  -不同 runner 不得拥有独立的 workflow context 组装逻辑；
* audit 内容完整保留，但不影响模型输入。

---

# 十四、最终架构原则

本次重构最核心的原则可以收敛为六条：

1. **Stable Blackboard Documents 是 workflow 跨节点业务记忆的唯一来源。**
2. **选中的文档提供完整业务正文，不再以 summary 代替 text。**
3. **Workflow Memory 是临时编译的 LLM view，不是新的持久化历史仓库。**
4. **Workflow Control State 只按当前 work item 局部投影，默认不进入模型。**
5. **Audit Plane 完整留存，但从依赖结构上禁止进入 WorkflowMemoryCompiler。**
6. **模型只有一个 workflow 信息入口：****`workflow_memory`****；****`input_context`**** 和 ****`context_snapshot`**** 不再承担 memory 职责。**

最终要实现的不是：

> 把当前大 context 压缩得更小。

而是：

> 从结构上保证每一类状态只存在于正确的层中，并让 LLM 只收到当前任务需要的完整业务记忆。
