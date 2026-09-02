# D3 O3 INITIALIZE 编排层重构方案

## 1. 重构目标

当前：

```text
Input Preparation
        ↓
O3_INITIALIZE
  research + calibration
  + condition design
  + policy drafting
        ↓
O3_FINAL_REVIEW
```

重构为：

```text
Input Preparation
        ↓
O3_TRIGGER_CALIBRATION
  research-heavy
  multi-Shell waves
        ↓
Stage Gate
        ↓
O3_POLICY_COMPILE
  compile-heavy
  multi-Shell waves
        ↓
Current INITIALIZE Completion Contract
        ↓
O3_FINAL_REVIEW
```

两个新节点共同替代原 `O3_INITIALIZE`。

核心目的不是单纯增加一次模型调用，而是建立一个明确的认知断点：

> **在任何最终 Policy drafting 开始前，先把完整 D2 Gap surface 中真正可交易的现实 Trigger 研究清楚。**

这样避免 Policy schema、Single Condition 偏好或 D2 recognition criteria 反向影响 Trigger Research。

---

# 2. 保持不变的总体边界

本轮重构范围只在 INITIALIZE 生命周期内部。

保持：

* Document 2 仍是 INITIALIZE 的业务输入；
* Reference Event View 的角色不变；
* Previous Policy Set 的角色不变；
  -一个 Shell 仍作为一个完整认知 wave；
* Worklist / Calibration / Wave checkpoint 的渐进工作模式保留；
  -两个新节点使用同一个 O3 thread；
  -两个新节点使用同一个 run workspace；
* `O3_FINAL_REVIEW` 仍位于 INITIALIZE 后；
  -后一个节点的最终 structured response contract 与当前 `O3_INITIALIZE` 保持一致；
  -最终 Policy schema 保持不变；
* `O3_MAINTAIN` 不在本轮重构范围；
* Persistent Runtime / W1 / W2 不在本轮范围。

---

# 3. 为什么两个阶段应按“全量研究 → 全量编译”排列

建议采用：

```text
TRIGGER_CALIBRATION
    Shell A wave
    Shell B wave
    Shell C wave
    ...

完成全部 Trigger Surface

        ↓

POLICY_COMPILE
    Shell A wave
    Shell B wave
    Shell C wave
    ...
```

而不是：

```text
Shell A Calibration
→ Shell A Compile
→ Shell B Calibration
→ Shell B Compile
```

原因是新的节点拆分本身就是为了隔离两种认知模式。

如果每个 Shell 编译完成后马上进入下一个 Shell 的 Trigger Research，同一 thread 中已经形成的 Policy 写作习惯仍可能反向影响后续 Research，例如重新出现：

```text
为了让 Policy 简单
→ 把 Trigger 向后推
```

而先完成所有 Shell 的 Trigger Calibration，可以让第一阶段始终保持同一种问题意识：

> **现实中谁的什么下一变化最早、最小充分地制造预期差？**

只有完整 Trigger Surface 冻结后，才切换到：

> **怎样把这些 Trigger 编译成 W2 可判断的 Policy？**

这能最大化拆节点的实际价值。

---

# 4. Thread 与 Workspace

两个节点继续使用同一个 ticker-level O3 thread：

```text
O3_TRIGGER_CALIBRATION
        │
        │ resume same thread
        ↓
O3_POLICY_COMPILE
        │
        │ resume same thread
        ↓
O3_FINAL_REVIEW
```

理由：

* Trigger Research 中形成的 actor / industry / contract / product 认知可被 Compile 复用；
  -不同 Shell 之间可以复用已经完成的现实状态研究；
  -后续 Shell 可以知道前面已经识别的 trigger-bearing actors；
  -Policy Compile 可以理解 Trigger Calibration 的上下文；
  -Cross-Shell canonicalization 不需要重新建立完整研究认知。

业务事实仍以 workspace artifacts 为准；thread continuation 只提供执行连续性。

---

# 5. 两个节点共享同一组 Frozen Business Inputs

一次 INITIALIZE run 内，两个节点以及后续 Final Review 应基于同一份冻结输入：

```text
Published D2
Reference Event View
Previous Policy Set
task / run context
```

节点切换本身不刷新 D2 或 Reference View。

否则可能出现：

```text
Calibration 使用 Reality Snapshot A
↓
Compile 使用 Reality Snapshot B
```

导致 `reference_state` 与 Trigger Calibration 不一致。

新的现实变化由后续 Maintenance 处理，而不是在同一次 INITIALIZE 中途切换输入基线。

---

# 6. Node A：`O3_TRIGGER_CALIBRATION`

## 6.1 节点职责

只完成：

```text
D2 Gap
→ Tradable Path
→ Actor-level Current State
→ Candidate Trigger
→ Trigger Quality Tests
→ Trigger-ready / UNRESOLVED
```

正常情况下不写最终 Policy drafts。

它是 INITIALIZE 中拥有主要外部研究预算的节点。

---

# 7. Node A 的 Shell Wave

仍保持：

> **一个 Shell = 一个 wave。**

每个 wave 内完成：

```text
理解 Shell
↓
展开全部 Gap → Paths
↓
Worklist Gate
↓
研究 Trigger-bearing actors / objects
↓
Current-State Calibration
↓
Minimal Sufficiency
↓
Disclosure Plausibility
↓
W2 Judgeability
↓
Trigger Calibration Checkpoint
↓
关闭本 Shell 的 Calibration wave
```

然后进入下一 Shell。

---

# 8. Node A 的 Worklist Ownership

建议继续由 Node A 创建当前 INITIALIZE 的完整：

```text
worklist.jsonl
```

原因是：

> Tradable Path 本身是在 Trigger Research 开始前定义的研究工作面。

Node A 负责：

-全部成功 Shell 的 Gap coverage；
-Path 拆分；
-direction；

* `missing_calibration`；
* Path 与 D2 source 的精确映射。

Node A 完成后，Node B 不重新建立 Path surface，只消费和必要时修正现有 Worklist。

---

# 9. Trigger Calibration 需要独立的过程状态

现有五字段 Calibration Log 对新的 Trigger Research 已经不够表达：

```text
actor
current state
candidate trigger
trade sufficiency
minimality
disclosure route
judgeability
```

编排层应提供一个能够持久化这些研究结论的 Stage-A checkpoint。

具体采用：

-扩展现有 `calibration_log.jsonl`；
-新增独立 Trigger Calibration artifact；
-或在现有 workspace contract 中增加相应过程文件；

应由 Codex 根据当前实现选择侵入最小且最清晰的方式。

但最终必须满足一个业务合同：

> **Node B 能够仅通过 Stage A 的结构化过程产物，明确知道每条 Path 已研究出的 trigger-bearing actor、current state、Candidate Trigger、交易充分性、消息披露路径和 W2 可判定性，而不需要重新从 D2 recognition criteria 推导 Trigger。**

---

# 10. Node A 使用独立的 Progress Checkpoint

不建议直接复用当前 `wave_state.json` 的“Shell 已完成”语义来表示 Trigger Calibration 完成。

原因是当前下游对：

```text
completed_shell_ids
completed_path_ids
```

更自然地理解为：

> INITIALIZE 已经完成 Policy Compilation 的 Shell。

Stage A 只完成 Trigger Research，还不能让 downstream 误认为整个 Shell 已闭环。

因此需要一个 **Stage-A progress state** 来表示：

```text
哪些 Shell 已完成 Trigger Calibration
当前正在研究哪个 Shell
哪些 Paths 已有 Trigger disposition
```

具体文件名和字段应结合现有 checkpoint 实现确定。

关键要求是：

> **Trigger Calibration completion 与最终 Initialize wave completion 语义分离。**

---

# 11. Node A Completion Gate

Stage A 不以：

```text
找到了多少 Policies
```

作为完成条件。

对所有成功 D2 Gaps 展开的 Paths，都必须存在明确 disposition：

```text
TRIGGER_READY
```

或者：

```text
UNRESOLVED
```

其中 `TRIGGER_READY` 表示已经完成 Skill 定义的：

```text
Marginality
Direct Trading Sufficiency
Minimality
Actor Granularity
Disclosure Plausibility
W2 Judgeability
```

Node A 全部 Shell waves 完成后才能进入 Node B。

---

# 12. Node A → Node B 的 Deterministic Stage Gate

两个 Agent 节点之间建议加入一个**轻量、结构性、非研究型**的 deterministic gate。

它只检查 Stage A 是否真正形成完整可消费状态，例如：

-成功 D2 Gaps 是否全部进入 Worklist；
-所有 Paths 是否都有 Trigger Calibration disposition；
-Trigger-ready Paths 是否存在所需 Calibration record；
-过程文件是否可解析；
-Path / D2 references 是否闭合；
-Stage-A progress 是否显示全部成功 Shell 已处理；
-冻结输入是否保持不变。

它不判断：

> Trigger 在投资意义上是不是“足够好”。

这种语义质量由 Agent Skill 和 Final Review 负责。

Stage Gate 的目的只是防止：

```text
Trigger Research 尚未完成
↓
Policy Compile 已经开始
```

---

# 13. Node A 的工具能力

Trigger Calibration 是新的 research-heavy 节点。

期望继续拥有：

```text
live Web Search
只读 Data MCP
Source Capture
workspace write
```

这是两个新节点中主要使用外部研究能力的地方。

Data MCP / Web 的调用仍遵循新的 Trigger Calibration Skill：

> 研究必须服务于明确 actor / current state / Candidate Trigger 问题。

---

# 14. Node B：`O3_POLICY_COMPILE`

## 14.1 节点职责

Node B 接收 Stage A 已经形成的完整 Trigger Surface。

它只负责：

```text
Trigger-ready Path
↓
Atomic Decomposition
↓
Single / Multi-condition
↓
Calibration fields
↓
Criterion normalization
↓
Policy expression
↓
Local / Cross-Shell Canonicalization
↓
Policy drafts
```

它不把 D2 `recognition_criteria` 当作重新研究 Trigger 的入口。

---

# 15. Node B 的主要输入优先级

Node B 的 Trigger 语义来源应明确分层：

### 第一层

```text
Stage A Trigger Calibration artifacts
```

决定：

* trigger-bearing actor；
  -current state；
  -Candidate Trigger；
  -Direct Trading Sufficiency；
  -disclosure route；
  -comparator basis。

### 第二层

```text
D2
```

用于：

* source provenance；
  -expectation transmission；
  -direction consistency；
  -必要的边界核对。

### 第三层

```text
Previous Policy Set / existing drafts
```

用于：

* semantic continuity；
  -canonicalization；
  -existing Policy comparison。

这样可以防止 Node B 因为 D2 recognition criteria 看起来“更完整”，重新覆盖 Stage A 已经研究出的更早 Trigger。

---

# 16. Node B 的研究权限应弱于 Node A

理想状态下：

> **Node B 不承担常规开放研究。**

它应该依赖已经冻结的 Trigger Calibration 结果完成编译。

如果当前 Worker 很容易配置节点级工具能力，建议：

-Node A 保留完整 research capability；
-Node B 缩减或关闭不必要的外部 Research capability。

如果现有实现不适合为同一 O3 workflow 维护两套 capability，则可以暂时保持工具可用，但由 `initialize_policy_compile.md` 明确控制其使用。

最终目标是：

```text
Node A
→ research first

Node B
→ compile first
```

而不是两个节点都重新研究一遍。

---

# 17. Node B 继续使用 Shell Waves

Node B 仍按 Shell 推进：

```text
Shell A Trigger-ready Paths
↓
Policy drafting / canonicalization
↓
Shell A compile completion
↓
Shell B
...
```

这里保留当前 progressive policy writing 模式。

Node B 可以读取：

```text
所有 Stage A Trigger records
+
此前 Shell 已生成的 Policy drafts
```

因此遇到跨 Shell 相同现实 Trigger 时，可以立即 canonicalize，而不必全部留给 Final Review。

---

# 18. Node B Ownership

Node B 负责最终 INITIALIZE 过程中的：

```text
policies/
```

以及当前与 Policy Compilation 绑定的工作状态：

-将 Trigger-ready Path 更新为 `COMPILED` 并写入 `policy_ids`；
-保留无法形成 Policy 的 Path disposition；
-维护 Policy draft 与 `source_refs`；
-执行 local / cross-wave canonicalization；
-更新最终 INITIALIZE 的 `wave_state.json`。

也就是说：

> **当前 `wave_state.json` 的最终完成语义继续由 Node B 维护。**

这能最大限度保持后续 Final Review 的现有认知。

---

# 19. Node B 的 Retry

如果：

```text
Node A 已完成
↓
Node B 中途失败
```

重试应从 Node B 当前 workspace checkpoint 继续。

正常情况下不重新执行 Node A。

Stage A 的完整 Trigger Calibration artifacts 是 Node B retry 的稳定输入。

只有 Stage A 工作产物缺失、损坏或无法建立合同一致性时，才应回到前一阶段，而不是因为普通 Node B failure 重做昂贵 Research。

---

# 20. Node A Retry

Node A 本身仍采用当前长任务 checkpoint 思路。

如果在某 Shell Research 中断：

```text
已完成 Shell Calibration
→ 保留

当前 Shell 已有 Trigger records
→ 保留

retry
→ 从当前未完成 work item 继续
```

不重新研究所有 earlier actors。

---

# 21. Node B 的输出协议必须保持当前 INITIALIZE 合同

这是本轮重构最重要的兼容要求之一。

Node B 完成全部 Shell Policy Compilation 后：

> **返回当前 `O3_INITIALIZE` 使用的同一个 structured output contract。**

即对 `O3_FINAL_REVIEW` 和其后 deterministic lifecycle 来说：

```text
旧：
O3_INITIALIZE completed
→ Final Review

新：
O3_POLICY_COMPILE completed
→ Final Review
```

后续看到的 INITIALIZE completion 语义保持一致。

不要求 Final Review 因节点拆分而更换 response schema。

---

# 22. Node A 使用新的内部小型响应合同

Trigger Calibration 节点需要一个独立的小型 structured output，用于 orchestrator 判断：

> Stage A 是否正常完成并可进入 Policy Compile。

它应保持与当前 O3 节点“小结果、业务产物在 workspace”的模式一致。

具体字段名称和数量应结合现有 runner/schema conventions 决定。

这里只要求能够表达：

```text
stage execution completed / failed
work surface 已处理到何处
是否仍有未处理 Trigger Calibration 工作
```

无需通过模型 response 携带完整 Trigger records。

---

# 23. Final Review 的接续

后续仍然：

```text
O3_POLICY_COMPILE
        ↓
deterministic initialize validation
        ↓
O3_FINAL_REVIEW
```

Final Review 继续使用同一个 thread 和 workspace。

它应能够看到：

-完整 Worklist；
-Trigger Calibration 研究结论；
-Policy drafts；
-wave state；
-coverage map；
-D2 / Reference View / Previous Policy Set。

正式 Review output contract 保持现状。

后续 Skill 重构可以让 Final Review 利用 Stage A 的 Trigger Calibration artifact 做更强 Condition Quality 检查，但本轮编排重构不需要改变其节点身份和最终 response contract。

---

# 24. Initialize Final Validation 的必要调整方向

当前 Initialize 完成后的 deterministic validation 可以继续保留其主要职责：

```text
Gap coverage
Path status
Policy schema
source refs
checkpoint closure
frozen inputs
```

但需要确认两点：

### 1. 不把 Single Condition 当作成功标准

MU Pilot 中 Agent 自己写出了：

```text
len(activation_conditions) != 1
→ error
```

这种规则与正式业务合同冲突。

编排/validator 层不应引入：

```text
single-condition ratio
all policies single-condition
```

之类的发布门槛。

### 2. Trigger Calibration completion 成为新的 upstream prerequisite

进入 Final Review 前，需要确认：

> 每个 `COMPILED` Policy 所对应的 Path，都存在 Stage A 已完成的 Trigger Calibration 依据。

但 semantic quality 仍主要由 Agent Review，而不是 deterministic validator 判断。

---

# 25. Workspace 最终应形成两个阶段清晰的过程状态

期望最终工作空间能够让人明确区分：

```text
Stage A
研究了什么 Trigger

Stage B
怎样把 Trigger 编译成 Policy
```

概念上：

```text
output/work/
    worklist
    trigger-calibration records
    trigger-calibration progress

    policies/
    final initialize wave state

    ...

```

具体路径与文件名不要在未核对现有代码前机械重命名。

实现时优先：

> **复用当前 workspace contract，只在无法清楚表达 Stage A 状态时增加最少必要过程 artifact。**

---

# 26. Context Injection 也应按两个阶段收敛

## `O3_TRIGGER_CALIBRATION`

Turn 明确要求读取：

```text
AGENTS
agent
foundation
initialize_trigger_calibration
task
D2
Reference View
Previous Policy Set
Stage-A checkpoints
```

Policy schema 即使仍存在于 workspace，也不需要成为该 turn 的主要必读业务输入。

这样可以减少 Agent 在 Research 阶段过早进入：

> “我要怎么凑成 schema”

的写作模式。

---

## `O3_POLICY_COMPILE`

Turn 明确要求读取：

```text
AGENTS
agent
foundation
initialize_policy_compile
task
D2
Stage-A Trigger Calibration records
Worklist
Previous Policy Set
Policy schema
existing drafts / compile checkpoints
```

Reference View 可以继续存在于冻结 workspace，但正常 Policy Trigger 语义应来自 Stage A 的 Calibration 结果。

---

# 27. 节点之间不再重新构建业务输入

Input Preparation 仍然只做一次。

不建议：

```text
Node A preparation
↓
重新 fetch / assemble
↓
Node B preparation
```

Node B 使用同一次 run 已冻结的：

```text
D2
Reference View
Previous Policy Set
```

并增加 Node A 形成的 workspace outputs。

这样节点拆分不会额外制造新的事实边界。

---

# 28. 当前 INITIALIZE checkpoints 如何重新分工

现有 checkpoint 思想保留，但职责需要重新分层：

| 当前对象                                 | 重构后主要职责                                                  |
| ------------------------------------ | -------------------------------------------------------- |
| Worklist                             | Node A 建立完整 Path Surface；Node B 写入最终 Policy mapping      |
| Calibration Log / 新 Trigger artifact | Node A 的核心研究状态                                           |
| Stage-A progress                     | Node A retry / Shell Calibration completion              |
| `policies/`                          | Node B progressive drafting                              |
| `wave_state.json`                    | Node B 最终 INITIALIZE Shell completion                    |
| Coverage Map                         | 在 Policy Compilation 后按现有 deterministic / review 逻辑产生与同步 |

这样不会为了两个节点把现有 checkpoint 机制全部推翻。

---

# 29. Failure Boundary

推荐的 failure propagation：

```text
Input Preparation failure
→ stop

Trigger Calibration runtime/core contract failure
→ stop before Compile

Individual Path cannot find usable Trigger
→ UNRESOLVED, continue

Policy Compile runtime/core contract failure
→ retry Compile from checkpoint

Individual Trigger cannot compile into valid Policy
→ explicit unresolved/problem state, continue

Final Review
→ retain current blocking/non-blocking semantics
```

业务研究无法收敛不应自动使整个 ticker INITIALIZE fail。

---

# 30. 不建议增加自动 Calibration ↔ Compile 循环

第一版重构不建议：

```text
Calibration
↓
Compile发现问题
↓
自动回Calibration
↓
Compile
↓
...
```

这会快速增加 orchestration complexity。

更简单的第一版：

```text
Stage A
→ 尽可能完整 Trigger Calibration

Stage B
→ Compile Quality Gate

发现 Stage-A Trigger 本身无法满足编译标准
→ 显式保留问题
→ 后续 Final Global Pass 可利用完整上下文和研究权限直接修复
```

如果 Pilot 证明 Stage B 经常大量退回 Stage A，再考虑增加显式 feedback loop。

第一版先观察真实 failure rate。

---

# 31. 为什么这种拆法比继续强化单节点 Skill 更合适

MU Pilot 中，现行 O3 实际已经严格执行：

```text
Worklist Gate
Calibration Gate
Progressive Drafting
Wave Gate
```

Worklist 覆盖和 Calibration process 都完成得非常完整。

但随后仍批量产生坏 Condition，并在长 turn 中形成：

> “全部 Policy 都保持一条消息内单 Condition”

的错误全局 heuristic，甚至自行写入 validation logic。

因此：

> **继续只增加一个更长的 `initialize.md`，虽然会改善质量，但不能完全解决 Research 与 Compilation 在同一 request 中互相污染的问题。**

两个节点的价值是让 Agent 在进入第二个 request 时重新收到一个非常明确的新任务目标：

```text
前一阶段：
研究真实 Trigger

后一阶段：
不要重新发明 Trigger，
把已经校准的 Trigger 编译好
```

这就是此次增加 orchestration complexity 的主要收益。

---

# 32. 第一版重构后的最终形态

```text
Published D2
Reference View
Previous Policy Set
        │
        ▼
Input Preparation / Freeze once
        │
        ▼
┌─────────────────────────────┐
│ O3_TRIGGER_CALIBRATION      │
│ same O3 thread              │
│                             │
│ Shell waves                 │
│ Gap → Path                  │
│ Actor research              │
│ Trigger calibration         │
│ Stage-A checkpoints         │
└─────────────────────────────┘
        │
        ▼
Deterministic Stage Gate
        │
        ▼
┌─────────────────────────────┐
│ O3_POLICY_COMPILE           │
│ resume same O3 thread       │
│                             │
│ Shell waves                 │
│ Trigger → Condition         │
│ Policy drafting             │
│ Canonicalization            │
│ Current wave_state          │
└─────────────────────────────┘
        │
        │ current O3_INITIALIZE
        │ output contract
        ▼
Initialize deterministic validation
        │
        ▼
O3_FINAL_REVIEW
        │
        ▼
existing assembly / publish lifecycle
```

---

# 33. 本轮明确不需要一起改的东西

为了控制重构风险，本轮不应借机扩散到：

```text
O3_MAINTAIN workflow
Monitoring Configuration
Persistent Runtime
W1 / W2
D1 / D2 workflow
Event Library workflow
最终 Policy schema
Final Review response schema
```

如果新 Trigger Calibration 需要更丰富的**过程 artifact**，可以作为 INITIALIZE 内部合同新增或扩展，但不要借此改变最终 Policy schema。

---

# 34. 验收重点

新 Pilot 不应只检查：

```text
Gap coverage
Policy count
schema valid
```

更重要的是比较重构前后的 Agent 行为。

### Stage A

检查：

-实际 Web / Data research 是否更多集中到 actor-level current state；
-是否真正研究“谁下一步会发生什么”；
-是否产生具体 disclosure route；
-是否执行 Minimality；
-是否仍大量直接接受 D2 recognition criteria；
-是否为了避免 `UNRESOLVED` 把 Trigger 推到 realization completion。

### Stage B

检查：

-是否重新覆盖 Stage A Trigger；
-是否仍出现 hidden conjunction；
-是否把多个 actor 拼回一个 Condition；
-是否重新加入 shipment / revenue / margin 等 realization leakage；
-模糊词是否有 comparator；
-Single / Multi-condition 是否由现实状态决定，而不是模型偏好。

### 整体

最重要的比较指标不是：

> Policy 数量增加还是减少。

而是：

> **随机抽取一个 Runtime Condition，是否真的能够说出“现实世界中谁可能在什么正常消息里发布这一事实，而且这项事实本身已经足够形成当前预期差”。**

这应成为本次 INITIALIZE orchestration 重构是否成功的核心验收问题。


---

# 审核注释：实施时必须落实的兼容修正

> 本节为结合当前 DoxAgent v2 D3/O3 正式实现进行审核后追加的实施注释；上方原方案正文保持不变。以下约束优先用于消除编排重构与当前运行时合同之间的冲突。

## 注释 1：保持当前 Final Review 与 deterministic validation 顺序

原方案第 23、32 节中的顺序图将 deterministic initialize validation 放在了 `O3_FINAL_REVIEW` 之前。实际实施时保持当前正式运行时顺序，不按该处顺序图反转：

```text
O3_POLICY_COMPILE
        ↓
生成 provisional coverage_map
        ↓
O3_FINAL_REVIEW
        ↓
重新读取 Final Review 修正后的工作文件
        ↓
post-review deterministic validation
        ↓
deterministic assembly / publish
```

Node A 与 Node B 之间仍设置独立的 Stage-A structural gate；如有必要，Node B 后可增加轻量 compile structural gate，但这些前置 gate 不能替代 Final Review 后的最终 deterministic validation。Final Review 修改后的 Worklist、Trigger Calibration、Calibration Log、Wave State、Policy drafts 与 Coverage Map 必须重新读取并完成最终一致性校验后，才能进入 assembly 和 publish。

## 注释 2：补齐跨节点恢复、冻结输入和非破坏性 resume 前提

当前实现中的 seed 行为会初始化并可能覆盖工作文件，因此不能仅凭 workspace 中存在 checkpoint 就宣称具备跨节点恢复能力。实施时必须同时补齐：

1. Input Preparation 完成并 seed 冻结业务输入后，由 orchestrator 生成受控 input manifest，至少记录 Published D2、Reference Event View、Previous Policy Set 和 task/run context 的路径、字节数与 SHA-256。
2. 在 Stage A 完成后、Stage B 完成后以及 Final Review 完成后，分别核对 manifest；输入缺失、大小变化或哈希变化属于核心合同错误，停止进入下一阶段。
3. seed 改为显式区分 fresh run 与 resume。resume 不得清空已有 Worklist、Stage-A artifacts、Stage-A progress、Policy drafts 或最终 wave state。
4. 持久化 Stage A、Stage B 的节点完成状态与当前 attempt/checkpoint。Node B 普通失败时，只从 Node B checkpoint 恢复，不重新执行已通过 Stage Gate 的 Node A。
5. 恢复前先重新解析并校验已有 Stage-A artifacts、progress 和 input manifest。只有 Stage-A 产物缺失、损坏或合同不闭合时，才允许回退至 Stage A。
6. 模型返回的小型 structured response 只表示本 Turn 的执行声明；是否允许推进必须以 orchestrator 对 workspace artifacts、progress 和 manifest 的确定性核对为准。

## 注释 3：新增独立、严格类型化的 Stage-A artifact

不扩展现有五字段 `calibration_log.jsonl` 来承载 Stage A 的完整 Trigger Research，因为现有 Agent work model 会忽略额外字段，可能造成 actor、current state、candidate trigger、disclosure route 等信息在读取时静默丢失。

第一版新增独立且严格类型化的内部过程合同，例如：

```text
output/work/trigger_calibrations.jsonl
output/work/trigger_calibration_state.json
```

每条 Trigger Calibration record 至少应明确关联 `shell_id + expectation_id + gap_id + path_id`，并结构化保存 trigger-bearing actor/object、current state、candidate trigger、trade sufficiency、minimality、disclosure route、judgeability、source basis 和 disposition。具体字段以实现时冻结的 Pydantic/schema 合同为准，并使用 `extra="forbid"` 或等价严格校验，避免未知字段被静默丢弃。

现有 `calibration_log.jsonl` 继续承担当前五字段的兼容职责；Node B 和 Final Review 同时读取新的 Stage-A artifact。若 Final Review 修正 Trigger 语义，必须同步更新 Stage-A artifact，或写入明确的 supersession/override 记录，禁止最终 Policy 与 Stage-A 过程状态互相矛盾。

## 注释 4：Stage-A disposition 与最终 Worklist status 分离

新增独立枚举 `TriggerDisposition`，至少包含：

```text
TRIGGER_READY
TRIGGER_UNRESOLVED
```

该枚举只描述 Stage A 是否已完成 Trigger Calibration 并形成可供 Node B 消费的结论，不直接复用最终 Worklist 的 `PENDING / COMPILED / UNRESOLVED`。

推荐状态流转：

```text
Node A:
Worklist.status = PENDING
TriggerDisposition = TRIGGER_READY | TRIGGER_UNRESOLVED

Node B / Final Review:
PENDING
→ COMPILED（存在有效 Policy mapping）
或
→ UNRESOLVED（最终确认当前无法形成有效未来 Policy，并写明 unresolved_reason）
```

`TRIGGER_UNRESOLVED` 不应自动等同于最终业务 `UNRESOLVED`。Node B 或 Final Review 仍可基于完整上下文修正 Stage-A 结论；只有在最终收敛后，才更新 Worklist 的正式状态。

## 注释 5：Stage Gate 保持宽松、结构性、非研究型

Stage Gate 只负责确认 Stage A 是否还存在可继续消费的工作状态。确定性读取必须先做逐行、逐记录、逐文件的有损最小恢复；单条记录解析失败、可归一化的类型/形状差异、局部引用不闭合、progress 不一致或单个 `TRIGGER_READY` Path 缺 record，均不得阻断整个 ticker。运行时应隔离坏行、规范化可恢复字段，必要时只将受影响 Path 降级为 `TRIGGER_UNRESOLVED` / 最终 `UNRESOLVED`，并以 warning 或 `PARTIAL` 继续。

硬阻断采用显式全局 allowlist，仅限继续执行会破坏全局可信度或使关键组件整体缺失的情况：

- frozen input manifest 缺失、被篡改或 resume 身份不一致；
- Agent 确实修改 frozen context、确定性发布目录或 canonical artifacts；普通非业务 scratch 文件仅记录 finding；
- 有成功 D2 Gaps，但经过有界恢复后整个 Stage-A Worklist 仍不存在任何可用记录；
- Codex SDK turn 自身连续失败且达到有界重试上限；缺失/无效的小型 structured response 不构成失败，只要 workspace 业务 artifacts 可恢复；
- Maintenance 的 optimistic concurrency base 已过期；
- canonical publish/storage 原子提交失败。

Agent 自报 `FAILED`、非终态或 `REVIEW_BLOCKED` 只作为 advisory receipt，不能把自身意见提升为 deterministic blocker。Compile/Final Review 的局部文件缺失或格式问题应优先重建派生文件、隔离坏记录、修复 mapping 或将受影响 Path 降级；只有上述全局 allowlist 可以导致 hard fail。

下列内容不得成为 deterministic blocker：

- Trigger 在投资意义上是否足够优秀；
- Trigger 是否已经达到最佳的最早边界；
- Single Condition 比例；
- 是否全部 Policy 都是单条件；
- Policy 数量、Trigger 数量或 `UNRESOLVED` 数量；
- 需要业务判断或实质研究才能确定的语义质量问题。

语义质量继续由 Stage-A skill、Node B compile checks 和 Final Review 负责。可解析但研究证据有限、Trigger 暂时无法收敛或个别 Path 无法形成 Policy时，应通过 `TRIGGER_UNRESOLVED`、最终 `UNRESOLVED`、warning 或 `PARTIAL` 表达，不得把宽松 validator 演化为过度审计的发布阻塞器。
