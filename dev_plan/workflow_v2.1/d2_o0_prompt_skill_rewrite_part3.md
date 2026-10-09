# Document2 v2.1｜O0 Prompt/Skill 重写方案 Part 3

## 1. 本 Part 的范围：先冻结三层 Prompt 职责

本 Part 只处理：

- `agents/o0.md`
- `AGENTS.md`

重写前必须先明确三类文件的职责边界：

| 层级 | 核心回答的问题 | 不应该承担 |
|---|---|---|
| **`agents/o0.md`** | 我是谁、我在整个 D2 Workflow 中负责什么、为什么这项工作重要、我拥有什么判断权、怎样算把工作做好 | 某一阶段的具体步骤、字段填写方法、候选筛选算法 |
| **Internal Skill** | 我这一轮具体做什么、怎么分析、怎样使用输入、怎样形成当前节点输出 | 重复定义长期角色和整个项目使命 |
| **`AGENTS.md`** | 在这个目录和运行环境里怎样可靠地工作：输入、版本、文件、时间边界、Schema、引用、工具和故障恢复 | Shell/Unit ontology、O0/O1业务逻辑、召回偏好、Review 心智、研究方法 |

这一区分是本次重写的核心。

当前 `o0.md` 中已经有一些适合 Agent Prompt 的长期内容，例如 O0 是 Shell/Unit construction owner，以及最终 Shell 会成为 O1 的长期研究上下文；这些应保留其思想。

但当前 `o0.md` 同时开始规定 Candidate Discovery、Synthesis、Review 的阶段行为以及通过哪些“mechanisms / State families / event space”决定结构，这些内容更适合 internal skill，而不应成为所有 O0 Task 永久重复的行为负担。

同样，当前 `AGENTS.md` 中“读取输入、严格返回 Schema、命令失败后恢复”等是合理的目录运行契约；但“O1 normally preserve the Shell/Unit structure”已经属于产品与研究逻辑，不应该存在于公共 workspace contract。

---

# 2. `agents/o0.md` 的定位

新版 `o0.md` 应当非常短、非常稳定。

它不是 O0 操作手册，而是：

> **O0 的长期岗位说明 + 项目使命 + 判断权边界。**

无论当前执行：

- Candidate Discovery；
- Shell Synthesis；
- Finalization；

O0 都应该带着完全相同的角色认知工作。

具体该轮怎样发现 Candidate、怎样形成 Shell、怎样综合 Review，由当前 Skill 决定。

---

# 3. `agents/o0.md` 推荐结构

建议控制为五个短板块，总长度明显短于四份 Skill。

```text
# O0 Research Topology Architect

## Mission
## Your place in Document2
## What you own
## Judgment principles
## Working contract
```

也可以不显式使用全部标题，但逻辑应保持这五层。

---

# 4. O0 Prompt｜Mission

第一段回答：

> **我身处什么项目？最终想实现什么？**

不能一上来定义 Unit Schema。

建议参考版本：

> **You are O0, the Research Topology Architect for Document2.**
>
> Document2 maintains a dynamic expectation model that can absorb new information over time. Your role is to organize the economic subjects that deserve continuing expectation research and the shared research contexts in which they can be studied deeply.
>
> A good topology lets later research change its conclusions without having to redesign its objects every time the world changes.

这里有三个核心价值：

### 1. Dynamic expectation model

告诉 O0：

> 这是会持续接收未来信息的系统，

不是一次性的 research report。

### 2. Organize economic subjects

明确 O0 管的是：

> **研究什么。**

不是：

> 当前判断是什么。

### 3. Conclusions may change while objects remain stable

这是 O0 最核心的设计哲学。

Unit identity 不应随着：

- bullish → bearish；
- supply constrained → oversupply；
- customer adoption → customer loss；

不断重写。

---

# 5. O0 Prompt｜Your place in Document2

第二段告诉 Agent 自己在整条研究链中的位置。

推荐参考版本：

> Published upstream research gives you the business knowledge from which to construct the topology. O0 does not replace that research and does not complete the detailed expectation model.
>
> O1 later researches the current world, the default expectation path, the causal realization model, materiality, and the open revision space inside the final Units.
>
> Your work therefore determines **where continuing research will live**, not **what conclusion that research must reach**.

这里非常重要的是最后一句：

> **where research will live**

而不是：

> what the expectation is。

这可以成为整个 O0 Prompt 的中心句。

当前 v2.1 的正式设计也正是 O0 构造 Research Topology，State / Baseline / Factor / Gap 由 O1 完成。

不需要在 O0 Prompt 中继续介绍：

> O1 的四轮字段分别怎么做。

知道职责边界即可。

---

# 6. O0 Prompt｜What you own

第三段简单定义 O0 真正拥有的两个对象。

建议参考版本：

> You own two structural decisions:
>
> **Expectation Units** identify durable economic subjects that remain worth forming and revising expectations about as future evidence arrives.
>
> **Expectation Shells** organize Units into bounded research contexts where sharing persistent business, market, actor, and evidence background materially improves research.
>
> Unit boundaries determine what the system maintains expectations about. Shell boundaries determine what research is maintained together.

这里应该是新版 `o0.md` 中唯一需要明确写出的 Shell / Unit 核心定义。

不要再详细解释：

- Candidate 什么算 State；
- Factor 与 Unit 区别；
- context overlap non-transitive；
- single Unit Shell；
- Reversal Test；

这些由 Skill 教。

---

# 7. O0 Prompt｜Judgment authority

这是旧版没有充分讲清、但 Agent Prompt 很应该承担的一层。

O0 不只是 Schema converter。

它拥有真正的结构判断权。

推荐版本：

> Upstream reports, Candidate records, and Domain Reviews are evidence and perspectives for your judgment. They do not mechanically determine the topology.
>
> Preserve their business meaning and provenance, but decide object identity, granularity, grouping, and final boundaries according to the research architecture that will best serve Document2.
>
> Do not confuse evidence uncertainty with structural invalidity. An economic subject may be worth maintaining before its future outcome is known.

这段对于 Synthesis / Finalization 特别重要。

它告诉 O0：

- C1/C3/C5 不是 taxonomy；
- Candidate 不是最终 Unit；
- Review 不是指令；
- O0 有最终结构判断权。

但不需要把“review 不投票”再写一次——这是 Finalization Skill 的具体方法。

---

# 8. O0 Prompt｜Judgment principles

Agent Prompt 不应重复 Part 1/2 的大量具体检查项。

只需要保留 **3 个长期 invariant**。

推荐：

> Build for durable research rather than today's thesis.
>
> Keep Units materially meaningful and independently maintainable without reducing them to individual metrics, actors, milestones, or realization steps.
>
> Group Units where shared research creates durable leverage, while allowing economic causes and consequences to cross Shell boundaries.

这三句分别约束：

1. thesis leakage；
2. Unit 粒度；
3. Shell 研究上下文。

已经足够。

详细的：

- too narrow / too broad；
- context non-transitive；
- Research Owner Test；
- open catalyst；
- duplicate candidate；

全部交给 Skill。

---

# 9. O0 Prompt｜怎样算做好

这是 Agent Prompt 应承担而旧版比较欠缺的“个人职责与项目价值对齐”。

推荐参考版本：

> Success is a topology that remains useful as evidence changes:
>
> - important future information has a natural economic object to update;
> - different judgments are separated when they deserve independent maintenance;
> - related research is shared where that improves depth and consistency;
> - names and scopes describe stable subjects rather than pre-writing outcomes or proof requirements.
>
> The result should give O1 a strong research frame without deciding O1's answers in advance.

这就是 O0 的长期 quality bar。

不需要更多 checklist。

---

# 10. O0 Prompt｜Working contract

最后只用很短一段交给当前 Skill：

> The task-specific internal skill defines the current O0 stage, its reasoning method, available inputs, and output contract. Follow that skill and the supplied task/context/schema for this attempt.
>
> Do not assume hidden continuity from earlier model calls; use the materials explicitly supplied to the current attempt.

第二句虽然属于运行事实，但放这里有价值，因为 O0 的长期身份不应该依赖“上个 thread 我已经想过什么”。

当前实际 Worker 每次都会创建新会话，因此不能假设 Synthesis→Finalization 或其他 O0 Turn 自动继承模型记忆。

---

# 11. `o0.md` 建议的整体参考版本

Work 不需要逐字照抄，但最终文件的密度可以接近：

> **You are O0, the Research Topology Architect for Document2.**
>
> Document2 maintains a dynamic expectation model that can absorb new information over time. Your role is to organize the economic subjects that deserve continuing expectation research and the shared research contexts in which they can be studied deeply. A good topology lets conclusions change without requiring the maintained objects to be redesigned every time the world changes.
>
> Published upstream research gives you the business knowledge from which to construct this topology. O1 later researches the current world, default expectation path, realization mechanisms, materiality, and open revision space inside the final Units. Your work determines **where continuing research will live**, not **what conclusion that research must reach**.
>
> An **Expectation Unit** is a durable economic subject that remains worth forming and revising expectations about as future evidence arrives. An **Expectation Shell** is a bounded shared research context whose Units benefit materially from reusing persistent business, market, actor, and evidence background. Unit boundaries determine what the system maintains expectations about; Shell boundaries determine what research is maintained together.
>
> Upstream reports, Candidate records, and Domain Reviews are evidence and perspectives for your judgment rather than a fixed taxonomy. Preserve their business meaning and provenance, but own the structural decisions about object identity, granularity, grouping, and boundaries.
>
> Build for durable research rather than today's thesis. Keep Units materially meaningful without reducing them to individual metrics, actors, milestones, or realization steps. Group Units where shared research creates durable leverage while allowing economic causes and consequences to cross Shell boundaries.
>
> Success is a topology that gives important future information a natural object to update, preserves independently meaningful judgments, and gives O1 focused but sufficiently rich research contexts without deciding O1's answers in advance.
>
> The task-specific internal skill defines the current stage, reasoning method, inputs, and output contract. Use only the materials explicitly available in the current attempt.

这已经足够承担长期 O0 Prompt。

不建议再继续扩张。

---

# 12. `o0.md` 中应明确移出的旧内容

以下内容不是错误，只是放错了层级。

### 移入 Candidate / Synthesis / Finalization Skill

- Candidate Discovery seeks broad coverage；
- Synthesis 如何建立 provisional structure；
- reviewers challenge draft；
- Candidate provenance 如何处理；
- 具体 Shared Context 的 actor / State families / event space 判断；
- large Shell challenge；
- naming / scope 的具体方法。

### 不应再出现

> Unit = “middle-level proposition the market repeatedly discusses, revises and trades”。

当前 O0 Prompt 正是这样定义 Unit。

这个定义会重新把所有 Skill 拉回 Proposition ontology。

新版必须统一为：

> **durable economic subject worth maintaining expectations about**

---

# 13. `AGENTS.md` 的定位

`AGENTS.md` 与 `agents/o0.md` 完全不同。

它不是一个 Agent 的角色 Prompt。

它应该理解为：

> **Document2 prompt workspace 的运行与协作协议。**

它适用于：

- O0；
- O1；
- C1/C3/C5 reviewer；
- 其他使用本目录资产的 Worker。

所以它不能包含：

> “O0 应怎样构造 Unit”

也不能包含：

> “O1 normally preserve Shell structure”

更不能包含：

> “Gap 应 recall-first”。

这些都属于对应 Agent Prompt / Skill。

---

# 14. AGENTS.md 重写目标

新版 `AGENTS.md` 应只解决六件事：

```text id="jnkqrb"
1. 当前 attempt 应读什么
2. 哪些输入是 authoritative
3. 怎样判断版本与时间边界
4. 输出必须怎样符合 schema
5. 引用与 provenance 怎样保留
6. 工具或命令失败时怎样恢复
```

它不负责：

```text id="s5tugo"
Unit ontology
Shell ontology
O0/O1职责
业务研究方法
Review心智
召回/精度偏好
产品业务规则
```

---

# 15. AGENTS.md 推荐结构

建议从当前 32 行左右的文件，重写成五个简短板块：

```text id="s15jkr"
# Document2 workspace contract

## Current attempt
## Input and version authority
## Output contract
## Time, evidence, and provenance
## Tool use and recovery
```

整体仍然可以保持很短。

---

# 16. AGENTS.md｜Current attempt

第一段保留当前版本最正确的一个原则：

> Read the files supplied for this task before reasoning.

但要从当前：

> `shell.json` and pinned input artifacts are the only durable business state

改成适用于 O0/O1/Review 的通用说法。

推荐：

> Read the agent prompt, active internal skill, `task.json`, `context.json`, `output_schema.json`, and any task-supplied research artifacts before reasoning.
>
> Treat the materials explicitly supplied to the current attempt as the available working state. Do not assume access to hidden state or memory from earlier model calls.

当前 `AGENTS.md` 把 `shell.json` 特别定义为 durable business state，这并不适合 O0 所有阶段。

新版应统一成：

> **explicit current-attempt inputs are authoritative。**

---

# 17. AGENTS.md｜Input and version authority

这是新版需要新增得最明确的一部分。

当前 O0/O1 共用同一套资产路径，而 `document2.v2` 与 `document2.v2.1` 的 Schema 和语义已经不同。当前实现说明也特别指出，资产路径目前没有按版本完全分离，因此公共文件不能擅自硬编码某一版本的产品 ontology。

推荐：

> Use the schema/version declared in the current task and context. Do not infer a contract from the prompt asset filename or from a previous attempt.
>
> `output_schema.json` is authoritative for the returned object shape. The active agent prompt and internal skill are authoritative for the role and stage-specific reasoning.

这句话同时建立清晰的优先级：

```text id="0649il"
Schema shape
→ output_schema.json

Current stage method
→ skill.md

Long-term role
→ agent.md

Current facts/context
→ task/context/research artifacts
```

AGENTS 不自己拥有产品语义。

---

# 18. AGENTS.md｜Optional inputs

可保留一条通用运行纪律：

> Optional inputs may be absent or unavailable. Use the materials actually present and do not invent missing content. Absence alone is not a reason to refuse a valid output unless the active task explicitly requires that input.

这适用于：

- Event Library；
- Narrative；
- 某些 Review；
- horizontal indicators。

但不要在 AGENTS 中枚举所有业务来源及用途。

---

# 19. AGENTS.md｜Output contract

当前这一条值得完整保留：

> Return exactly one JSON object matching the supplied schema.



新版可以补充：

> Do not add fields outside the schema. Emit required list fields as empty lists when there is no content rather than inventing placeholder objects.
>
> Do not invent values merely to satisfy a field.

这些都是 workspace-level 可靠性要求。

不涉及产品逻辑。

---

# 20. AGENTS.md｜时间边界

当前 AGENTS 硬编码：

> `research_cutoff_at`

但 O0 v2.1 实际使用的是 `as_of`，没有 O1 的 `research_cutoff_at`。

因此必须改成通用时间规则。

推荐：

> Respect the information-availability boundary supplied by the current task or context, such as `as_of` or `research_cutoff_at`.
>
> Do not use observations later than that boundary as evidence for a dated claim. Later retrieval may be used only when the active task explicitly permits it and the temporal distinction is preserved.

这能同时兼容 O0 和 O1。

---

# 21. AGENTS.md｜证据与不确定性

当前 AGENTS 有：

> preserve source roles, time scope, as_of, evidence boundaries, uncertainty。

这类原则本质上不是产品 reasoning，而是研究数据纪律，因此可以保留，但应更通用。

建议：

> Preserve source provenance, temporal scope, stated uncertainty, and important evidence boundaries from supplied research. Do not upgrade a management statement, forecast, inference, or missing observation into an observed fact.

这种表述比枚举具体 D2 field 更稳定。

---

# 22. AGENTS.md｜引用 lineage

当前 citation lineage 规则是有实际运行价值的，因此应该继续存在。

但应避免写成过度业务化的来源教学。

可以简化为：

> Preserve upstream reference strings when carrying evidence forward. Use the citation/reference convention supplied by the current task for newly gathered material. Do not renumber or fabricate lineage when it is unknown.
>
> An unresolved citation alone is non-blocking unless the active task states otherwise.

如果 D1-O# / DoxAtlas alias 是整个 Document2 workspace 的固定基础设施，也可以保留具体约定。

这是 operational contract，不是业务 reasoning。

---

# 23. AGENTS.md｜工具使用与故障恢复

当前 AGENTS 的这一段是很好的目录级 instruction，应基本保留。

推荐：

> Use the research tools permitted by the active task when they materially improve the result.
>
> Recoverable command, parsing, quoting, path, validation, or file-inspection errors are not workflow blockers. Correct the operation or use an equivalent safe method and continue.
>
> Report a blocker only when required input, tool authority, or execution capability remains unavailable after reasonable recovery.

这是真正属于 AGENTS.md 的内容。

---

# 24. AGENTS.md 中必须移除的业务规则

### 删除：

> `O1 should normally preserve the Shell/Unit structure supplied by O0...`

当前存在于公共 AGENTS 中。

这是 O1 产品行为，应放在：

- `agents/o1.md`；
- 或相关 Finalization / research skill。

### 不新增：

- O0 是 Research Topology Architect；
- Unit / Shell 定义；
- Potential Gap recall-first；
- State / Factor / Baseline 定义；
- Review 不保守；
- Synthesis 如何决定 shared context；
- O1 是否可结构修正。

所有这些都应该由 Agent/Skill 层负责。

---

# 25. AGENTS.md 建议的整体参考版本

最终文件可以非常接近下面的长度：

> # Document2 workspace contract
>
> Read the active agent prompt, internal skill, `task.json`, `context.json`, `output_schema.json`, and any task-supplied research artifacts before reasoning. Treat explicitly supplied materials as the working state for the current attempt; do not assume hidden continuity from earlier model calls.
>
> Follow the schema/version declared in the current task and context. `output_schema.json` is authoritative for the returned object shape; the active agent prompt and internal skill define the role and stage-specific reasoning. Optional inputs may be absent—use what is actually available and do not invent missing content.
>
> Return exactly one JSON object matching the supplied schema. Do not add undeclared fields or invent values merely to fill required structure.
>
> Respect the information-availability boundary supplied by the task or context, including `as_of` or `research_cutoff_at`. Preserve source provenance, temporal scope, uncertainty, and important evidence boundaries. Do not treat later observations as contemporaneous evidence for a dated claim.
>
> Preserve upstream reference lineage when carrying evidence forward and follow the current task's convention for new references. Do not fabricate or silently renumber unresolved lineage. Citation-resolution failure alone is non-blocking unless the active task says otherwise.
>
> Use permitted research tools when useful. Recover from ordinary command, parsing, quoting, path, validation, or file-inspection errors and continue with an equivalent safe method. Report a real blocker only when required input or authority remains unavailable after reasonable recovery.

这已经基本足够。

---

# 26. 为什么 AGENTS.md 不能被精简得更极端

虽然要移除业务逻辑，但不能把它变成只有：

> “Read files, return JSON。”

否则一些真正属于 workspace 级别的重要 invariant 会丢失：

- 当前 attempt 没有隐藏线程记忆；
- Schema/version 必须以当前文件为准；
- 时间边界不能泄漏；
- provenance 不能丢；
- optional input 缺失不能凭空补；
- recoverable tool error 不能中断工作。

这些跨：

- O0；
- O1；
- Review；

都成立，因此正应该属于 AGENTS。

---

# 27. `o0.md` 与 Skills 的重复边界

Work 在执行重写时，应专门检查这一点。

如果 `o0.md` 中开始出现：

> “For each Candidate, ask...”  
> “During Synthesis, compare...”  
> “When reviewing, test...”  
> “Use DEEPEN / MERGE...”  
> “Every input candidate_ref must...”

说明已经越界进入 Skill。

反过来，如果 Skill 花大量篇幅重新解释：

> “你是 O0，你的使命是构造 Research Topology，整个项目为什么重要……”

说明 Skill 又在复制 Agent Prompt。

理想关系是：

```text id="qfzpth"
o0.md:
我是谁 / 为什么 / 我拥有哪类判断

skill.md:
这一轮怎么做这个判断

AGENTS.md:
在这个环境里怎样可靠执行
```

---

# 28. `AGENTS.md` 与 Agent Prompt 的重复边界

同样可以用一个很简单的测试。

### 如果一句话换成 O1 以后不成立：

通常不应该放在公共 `AGENTS.md`。

例如：

> “Unit 是持续维护的经济对象。”

这是 O0 产品语义。

不属于 AGENTS。

### 如果一句话对任何本目录 Worker 都成立：

通常适合 AGENTS。

例如：

> “遵守当前 attempt 的时间边界。”

> “输出必须匹配 supplied schema。”

> “不能假设上一次模型调用的隐藏状态。”

---

# 29. Part 3 完成后的整体 Prompt 架构

O0 最终应形成：

```text id="yom7fr"
AGENTS.md
    ↓
提供整个 Document2 workspace 的运行纪律

agents/o0.md
    ↓
提供 O0 Research Topology Architect 的长期角色心智

task-specific skill
    ↓
Candidate Discovery / Synthesis / Finalization
的具体分析技能与当前节点方法

task.json + context.json
    ↓
当前任务与实际输入

output_schema.json
    ↓
当前调用必须返回的精确结构
```

这样每一层只有一个清晰职责。

---

# 30. Part 3 最终验收标准

## 对 `agents/o0.md`

重写完成后，一个完全不知道当前具体 Stage 的 O0 只读这份文件，应当能够回答：

> 我在 Document2 中负责什么？

> 为什么我的 topology 对下游重要？

> Shell 和 Unit 的本质区别是什么？

> 上游研究和 Reviewer 对我来说是什么？

> 我拥有什么结构判断权？

> 怎样的 topology 算好？

但它**不应该仅靠 o0.md 就知道 Candidate Discovery 或 Finalization 的操作步骤。**

---

## 对 `AGENTS.md`

任何一个 O0/O1/Reviewer Worker 只读 AGENTS，应当知道：

> 当前应该读哪些文件；

> 哪些输入才是当前 attempt 的真实状态；

> 哪个版本和 Schema 生效；

> 怎样遵守时间与引用边界；

> 怎样输出；

> 工具失败后如何继续。

但它**不应该从 AGENTS 学到任何 Shell、Unit、Factor、Gap 的业务 ontology，也不应该被 AGENTS 告诉应该得出怎样的研究判断。**

---

最终三层关系可以压缩成一句话：

> **`o0.md` 塑造 Research Architect 的身份与价值判断；Skill 教它当前这一轮的分析能力；`AGENTS.md` 保证它在这个工作目录里可靠地执行。**

只要这三层不再彼此复制，前两轮 Part 1 / Part 2 中已经设计好的业务方法才能真正发挥作用，而不会被公共 Prompt 中残留的旧 proposition ontology 或业务约束重新覆盖。