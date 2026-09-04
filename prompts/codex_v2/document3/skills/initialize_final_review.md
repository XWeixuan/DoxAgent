# O3 Final Global Pass

本 Turn 是 INITIALIZE 生命周期最后的全局语义复核与有界修复。Trigger Calibration 已展开并校准 Candidate surface，Policy Compile 已把 ready Candidates 编译为 drafts；Final Review 从 workspace 的全部冻结输入和工作工件重建完整状态，处理只有跨 wave、跨 Gap、跨 Path 或跨 Policy 比较才能发现的问题。

Stage-A `TRIGGER_READY`、Worklist `COMPILED` 和前序研究结论本身都不是质量证据。把当前 Policy Set 当成待证伪对象，从最终 Runtime 只能依据未来消息应用既定标准的视角，重新检查前两阶段是否真正满足 Foundation、当前 stage contracts 与 supplied schema。

Foundation 定义共享业务 invariant，前两个 stage skills 定义研究与编译方法，supplied schema 定义最终字段。Final Review 验证并修复这些合同的实际执行结果，不另建字段 ontology、充分性标准或研究 surface。

最终集合应满足三个总合同：

```text
每个 Active Condition 相对于当前现实与当前市场 expectation 均独立产生显著、定向的 expectation update
+
同一 Policy 的 Conditions 是同一次决策边界的替代触发，任一成立即激活
+
任一 Condition 已成为现实，原 Policy 相对于旧 baseline 的边界即已被消费
```

Final Review 在冻结证据足以唯一决定修改时直接修复并同步全部相关工件；需要新事实、新阈值、新 actor 或新传导机制的问题进入 ReviewResult。目标是得到覆盖闭合、语义一致、仍面向未来且可由 Runtime 低自由度判定的 Policy drafts，而不是重跑前两阶段或追求更少的 Policy、Condition 或 issue。

## 1. 重建全局状态

按当前 node prompt 读取 `task.json`、完整 Published D2、可选 Reference View、Previous Policy Set、supplied schemas，以及当前 attempt 的完整：

```text
worklist.jsonl
trigger_calibrations.jsonl
trigger_calibration_state.json
calibration_log.jsonl
wave_state.json
policies/
coverage_map.json
```

这些工件而非会话记忆构成本 Turn 的完整上下文。D2 定义 expectation、经济传导和 revision space；Reference View 表示冻结时点可用的现实补充；Trigger records/state 保存 Candidate 语义与 disposition；Worklist 和 Policy drafts 保存编译结果。D2、Worklist 与 Policy drafts 是 coverage 关系的业务源，`coverage_map.json` 是 Final Review 需要与修复后源对象保持同步的派生视图。Previous Policy Set 仅用于语义与身份连续性比较。

先在内部建立四个视图；它们是复核框架，不新增文件或字段。

### D2 Coverage View

```text
Expectation / Unit
→ Gap
→ Path
→ disposition
→ Trigger record
→ Policy / Condition 或 unresolved
```

### Candidate-to-Condition Trace View

对每个 Candidate 明确其最终去向：独立 Condition、同一 occurrence 的 supporting evidence、被 canonicalized 到哪一 Condition，或具体为何 unresolved。

### OR Policy Semantic View

对每条 Policy 明确 principal expectation revision、`decision`、各 Condition 的独立经济含义、Condition 之间的替代关系，以及任一成立后被消费的共同决策边界。

### Current-Reality View

仅依据冻结的 D2 baseline、Reference View、Trigger `current_state` 和 Calibration `reference_state`，将各 Condition 判断为 `STILL_FUTURE`、`ALREADY_REAL`、`DISPROVED_OR_NO_LONGER_POSSIBLE` 或 `UNCERTAIN`。以上只是内部审查分类，不新增 schema 枚举。

### Fresh Semantic Recheck

在接受任何前序 disposition 或表达前，逐 Condition 回到 D2 revision、Trigger research 与当前现实，重新回答：

1. `criterion` 是否是自然、稳定的业务 occurrence，而非 D2 研究结论或 transmission？
2. `reference_state` 是否只包含该 Condition 直接相关的当前现实锚，而非 Unit/Shell 背景复制？
3. `trigger_boundary` 是否表达相对于当前市场 expectation 的最低 tradable surprise，而非 `criterion` 的复制？
4. 三个字段是否针对同一 actor/object/variable，scope、时间方向和经济方向是否一致？
5. Criterion 是否等待了本应属于后续 realization 的 shipment、revenue、margin、share 等完整确认？
6. 现实中是否存在一条正常消息可以直接确认整个 Criterion，还是暗含多个独立 information holders、时点或阶段？
7. 决定充分性的程度是否已有可靠 numeric threshold 或 categorical business boundary？
8. Criterion 内是否隐藏多个彼此可独立发生、应拆成 OR Conditions 或不同 Policies 的替代分支？

### Semantic Diagnostics

必须读取 `context/document3/semantic_diagnostics.json`，并从现有工件复核其中的 non-blocking findings：`criterion` 与 `trigger_boundary` 大面积相同、`reference_state` 高重复、Criterion 直接复制 D2 `possible_occurrence`、`d2_boundary_sufficient=true` 异常集中、零 unresolved、临时语义批量生成脚本、hidden OR、无 comparator 的程度词，以及异常大的 OR groups。

这些模式是抽查入口，不自动判错，也不以降低 warning 数量为目标。对异常样本回到具体 D2/Path/Trigger/Policy 判断；只有实际语义缺陷才直接修复或形成 residual issue。大面积字段相同、全部 boundary sufficient、零 unresolved 或高 reference duplication 等形态，只有在抽查证明其逐项依据成立，并在 ReviewResult 中留下实质说明后才可 `PASSED / issue_count=0`。

进入下一节前，应能说明 Gap、Path、ready/unresolved Candidate、Policy 和 Condition 的数量及完整映射。

## 2. Coverage 与引用闭环

按 D2 的成功 Shell 逐级检查：

1. 每个 Potential Gap 至少有一条 Worklist Path，且 `shell_id + expectation_id + gap_id` 精确存在于 D2；failed shells 只作为已知输入边界保留。
2. 每条 Path 已使用现有状态收敛为 `COMPILED` 或 `UNRESOLVED`，并与 Trigger disposition 一致。
3. 每个 ready Candidate 已成为独立 Condition，或明确 canonicalized 到同一 occurrence 的 Condition；同一 occurrence 的证据路线仍保留在 Stage A 研究工件中，其他 supporting information 已正确归位而没有伪装成 Condition；无法形成有效未来边界的对象有具体 `unresolved_reason`。
4. 每条 `COMPILED` Path 的 `policy_ids` 指向真实 draft；每条 Condition 可反向追溯到已处理的 Path/Candidate。
5. 每条 Policy 的 `source_refs` 只包含真实支持其交易含义的 D2 refs，并保留合并前全部有效 provenance。
6. Worklist、Trigger state、Policy drafts、WaveState 与 CoverageMap 对同一关系给出一致结果。

若成功 Shell 的整个 Gap 缺失，现有工件不足以在 Final Review 中补造其 Path 和 Candidate：记录需要返回前序阶段处理的 residual issue。Coverage 完整不等于每条 Path 都生成 Policy；当前 schema 中不能发布的历史、耗尽或无法校准路径统一使用 `UNRESOLVED` 和准确原因表达。

## 3. 全局 Canonicalization

Canonicalization 先处理 Condition，再处理 Policy。

### 3.1 Condition 级

比较 occurrence、actor/object、现实边界、principal revision 和方向：

| 实质关系 | 处理 |
| --- | --- |
| 同一 occurrence、不同披露或证明形式 | 保留一个 Condition，将证据路线留在 Stage A 研究工件，并合并真实 source refs |
| 同一现实边界的语义重复 | 保留最清楚、可判定的 canonical 表达 |
| 一个状态包含另一个，较低边界已独立充分 | 保留较低的充分边界；较强状态只有产生新的独立 expectation delta 时才另行表达 |
| 只提高置信度、解释后果或帮助召回 | 归入 evidence、`match_scope` 或支持性研究，不作为 Condition |
| 多项属性共同定义一次自然合同、规则、产品或生产状态 | 在单条自然消息可判断整体 occurrence 时保留为一个完整 Condition |

Condition 的单位是自然完整 occurrence，不是语法上最小的 Boolean 片段。同一 occurrence 的 actor、object、范围、数量、期间、承诺强度或商业阶段可以共同定义它；需要不同未来消息分别确认的不足事实不能通过拼接获得表面上的充分性。

### 3.2 Policy 级

多条 Conditions 只有同时满足以下关系才属于同一 Policy：

```text
同一个 principal expectation component 与 revision type
+ 同一个 LONG / SHORT decision
+ 同一个 current-baseline execution meaning
+ 任一 Condition 成立都会消费同一次决策边界
+ title 与 decision 对任一 Condition 单独成立都准确
```

对每条 multi-condition Policy 再逐项验证：每个 Ci 是否单独充分；所有 Ci 是否修改同一 principal revision；若 C1 今天触发并更新 baseline，C2 明天发生是否仍会产生新的独立交易机会；aggregate state 与促成它的 specific mechanism/actor realization 是否重复；任一 Ci 成立时 `title` 与 `decision` 是否都准确。C2 仍产生新 delta 表示两者不属于同一个 one-time Policy boundary。

Occurrences、actors 或证据渠道可以不同；分组依据是 expectation revision 与一次性决策含义。方向相同但机制、时间维度、revision 类型或增量决策不同的对象保持不同 Policies。相反方向沿 D2 transmission 重新核对并分别表达；若冲突来自编译错误且冻结依据唯一，直接修正。

合并、拆分或删除后，同步更新 Policy drafts、`source_refs`、Condition IDs、title/match scope、相关 Path 的 status 与 `policy_ids`、Trigger records/state、CoverageMap，以及受影响的 WaveState 进度。冗余 draft file 应从工作集合移除，避免后续 assembly 再次读取。

## 4. 交易逻辑与 Activation 质量

### 4.1 Standalone Sufficiency

对每条 Condition 使用固定反事实：

```text
只知道当前现实与当前市场 expectation
+ 只知道该 Condition 已成立
+ 其他 Conditions 全部未知
```

该事实必须足以使相关 expectation 发生显著边际变化，方向明确，并支持 Policy 的 `LONG` 或 `SHORT`。时间早晚只用于判断相对 baseline 是否仍有信息增量，不决定 Condition 的结构优先级。

未通过时按真实作用归位：同一 occurrence 的证明路线保留在 Stage A 研究工件，仅用于召回的内容进入 `match_scope`，仅增强信心的内容不进入 Policy；若多个属性共同定义一次自然且可判断的充分 occurrence，可在冻结语义完整时合成一个 Condition；仍依赖另一项未来事实或新研究才能获得交易充分性，则隔离为 unresolved。

### 4.2 Recall 与 Activation

`match_scope` 高召回地覆盖整条 Policy 的 change surface，以及所有 OR Conditions 相关消息范围的合理并集；Activation Conditions 窄而明确地规定真实触发边界。相关、接近边界、确认或否定消息均可被召回，而 Conditions 仍可全部为 false。只有事件的成立本身依赖法定或正式状态时，证据范围才自然收敛到对应正式渠道。

### 4.3 固定 OR 合同与自然 occurrence

```text
Policy Activated = C1 OR C2 OR ... OR Cn
```

任一 Condition 被一条新消息确认，Policy 即激活；同一消息确认多个 Conditions 时仍只激活一次。每个 Condition 是独立决策理由，而不是共同完成证明链的证据碎片。

一个 Condition 可以包含定义同一次 occurrence 所必需的多项业务属性。只有这些属性共同描述一个自然状态，且正常消息能够判断整体状态时，复合表达才成立。Condition 数量由真实存在的独立充分替代触发面决定，不以单条件或多条件比例为目标。

## 5. Calibration 质量

对每条 OR Condition 独立检查以下链条：

### `criterion`

- 描述可由单条运行消息判断的现实状态或变化，而非研究结论；
- 脱离其他 Conditions 后语义仍完整；
- actor、object、scope、period、quantity 或 stage 达到独立充分所需的最低业务边界；
- 程度词有 Runtime 可见的现实状态或 comparator。

### `reference_state`

- 准确描述当前已经成立并进入 baseline 的状态；
- 与该 Condition 使用同一对象、口径和阶段；
- 足以解释 Condition 相对什么形成新增量；
- 只陈述当前现实，不借用另一未来 Condition 补足起点。

### `trigger_boundary`

- 明确现实相对于当前市场 expectation 跨过哪一最低 surprise boundary；
- 给出该 Condition 独立支持 decision 的最低充分经济边界；
- 数值、时间、范围、合同强度或商业阶段均有冻结依据；
- corroboration 与证明偏好不被写成额外经济条件。

### Internal Consistency Check

```text
reference_state → 当前现实
trigger_boundary → 相对当前市场 expectation 的最低 surprise boundary
criterion → 对该 boundary 的稳定 Runtime 表达
```

三者应描述同一 actor/object/variable，使用一致的 scope、时间基准、方向和程度门槛。任何字段都不应把 Activation 改写成另一项更严格、更晚的条件。

最后检查 Policy 层一致性：每条 `criterion` 与自己的 Calibration 对齐；所有 Conditions 支持同一 `decision`；title 对任一 Condition 单独成立均准确；`match_scope` 宽于 Activation 且不改变边界。

## 6. 已成为现实的 Trigger

在 Current-Reality View 中处理四类结果：

### 仍面向未来

保留 Condition，并确保 `reference_state` 已吸收冻结输入确认的当前进展。

### 已经成为现实

任一 OR Condition 已成为现实，表示原 Policy 相对于旧 baseline 的决策边界已经被消费。此时重新评估整条 Policy，而不是删除该 Condition 后继续等待其他替代触发：

- 其余 Conditions 只是在同一 revision 下的替代实现方式：原 Policy 不进入新的 Active draft 集合；相关 Path 使用 `UNRESOLVED`、Trigger disposition 使用 `TRIGGER_UNRESOLVED`，并在现有 reason 字段说明边界已进入 baseline 或被消费，再同步 CoverageMap。
- 其余 Condition 相对于新 baseline 仍产生新的独立显著增量：只有冻结工件已经明确定义新 baseline、边界、revision 和方向时，才重校准为新的 Policy，并按经济含义变化维护 temporary identity。
- 下一项边界需要新事实或实质研究：隔离受影响 Policy，将相关 Path 设为 unresolved，并在 ReviewResult 标记 `requires_research`。

### 已被证伪或失去可能性

若 Policy 尚未被激活，移除该替代 Condition 并复核剩余 OR group；没有剩余有效 Condition 时移除 Policy draft，并同步所有映射和状态。

### 现有证据无法确定

Reference View 的空白不是尚未发生的证明。将不确定性作为 residual issue；若它可能使已经发生的 Condition 被当作未来 Trigger 发布，且无法完整隔离，计为 blocking。

本节只保证初始化发布时的 Policy 面向未来，不设计激活后的持仓、重复交易或日常维护逻辑。

## 7. 有界修复与问题升级

使用以下顺序决定处理方式：

```text
仅涉及引用、格式、重复或冻结语义的表达
→ 直接修复并同步

全部冻结工件共同指向唯一业务结论
→ 直接修复并全局复读

不能唯一决定，但受影响对象可完整隔离
→ unresolved / 移出 Active drafts + residual issue

不能隔离而会污染最终集合
→ blocking issue
```

冻结证据充分时，可直接修正引用与 Coverage、合并同一 occurrence、移除 supporting 或 nested Condition、修复 OR 分组、对齐唯一明确的方向/标题/Calibration、重写 summary/scope、处理已成现实 Condition、同步状态以及修复 schema/语言问题。

Condition 数量或时间窗口、actor materiality、复合 occurrence、Policy 拆分、已消费边界后的新 Policy、`decision` 等实质判断，只有冻结工件共同指向唯一答案时才直接修改。需要新增事实、阈值、actor、Path、thesis 或 transmission 才能决定的问题保留原始依据，识别受影响的 Gap/Path/Trigger/Policy/Condition，并在 ReviewResult 中准确说明缺口；Final Review 不在本 Turn 获取新证据或现场补造 Candidate。

一次直接修复完成意味着：业务工件已经修改、所有引用工件已经同步、受影响 Policy group 已重新复核、问题不再存在于最终集合。每次修改后重新读取受影响的全部 Policy drafts、Worklist Paths、Trigger records/state 和 CoverageMap；发生 Policy 合并或拆分时，再比较同一 revision 和 direction 下的相关 Policies。

## 8. 最终一致性检查

所有修复完成后重新读取完整工作工件，并按以下顺序检查最终状态。

### Coverage 与 Completion

- 每个成功 Shell 的 D2 Gap 至少有一条 Path；每条 Path 有合法终态和具体去向；
- 每个 ready Candidate 已成为 Condition 或有明确 canonicalization 终点；每个 unresolved 对象有具体原因；
- 没有孤立 Policy、Condition、Path mapping 或无来源 `source_ref`。

### Condition 独立充分性

- 其他 Conditions 未知时，每条 Condition 仍足以支持 `decision`；
- supporting signal 与 Evidence 没有被伪装为独立触发；
- 多个不足事实没有被写成 OR Conditions；一次自然 occurrence 的必要属性没有被错误拆分；
- nested Conditions 已保留最低且独立充分的有效边界。

### OR Policy 一致性

- `activation_conditions` 由当前 schema/version 固定按 OR 解释；
- 同一 Policy 的所有 Conditions 共享 principal revision、direction 和一次性 execution meaning；
- 任一 Condition 首次满足都会消费同一次决策边界；
- 后续仍有独立新增决策价值的事件没有被混入原 Policy；
- 不同 revisions 没有仅因方向相同而合并。

### Occurrence、Calibration 与 Runtime

- 同一 occurrence 的不同 Evidence routes 只形成一个 Condition；
- `criterion`、`reference_state` 与 `trigger_boundary` 相互一致；
- 比较对象、口径、方向、时间或阶段明确，没有无冻结依据的伪精确阈值；
- 一条现实消息可以依据 Condition 和 Calibration 判断 true/false；
- title 对任一 OR Condition 成立，`match_scope` 覆盖全部 Conditions 且宽于 Activation。

### Current Reality 与工件

- 所有 Active Conditions 仍面向未来；已成现实边界已按整条 Policy 被消费处理；
- Reference View 空白没有被当作未发生证明；需要新研究的后续边界没有被猜测写入；
- Policy drafts、Worklist、Trigger records/state、Calibration Log、WaveState 与 CoverageMap 一致；
- Policy/Condition temporary identity 与经济语义变化相符，supplied schemas 可解析全部工件；
- 自然语言以中文为主体，字段名、ticker、专有名词和缩写除外。

## 9. ReviewResult

ReviewResult 只记录全部直接修复后仍存在的问题，不是修改日志。使用 supplied schema 的既有字段：

```text
status
issue_count
blocking_issue_count
issues[]:
  code
  message
  affected_policy_ids
  requires_research
  blocking
diagnostics_reviewed
diagnostics_explanation
```

在 `message` 中说明受影响的 Gap、Path、Trigger 或 Condition ID；`affected_policy_ids` 只填写真实 Policy IDs。已经修复的 Coverage、重复、supporting Condition、引用、状态、格式或语言问题不再计入 issues。

`diagnostics_reviewed` 必须为 `true`；`diagnostics_explanation` 简要说明 diagnostics 中的主要异常如何被抽查、修复或判定为有依据。存在显著 finding 时，不得用空泛措辞绕过，也不得无解释返回 `PASSED / issue_count=0`。

`PASSED / REVIEW_BLOCKED` 是残留事实的结果，不是优化目标；Semantic Diagnostics 本身不是 issue，只有抽查后仍真实存在的缺陷进入 ReviewResult。

当不合格对象可完整隔离时，将 Path 设为 `UNRESOLVED`、从 Active drafts 移除受影响 Policy，并以 `blocking: false` 记录仍值得下游理解的边界；其他合格 Policies 继续保留。以下问题在无法修复或隔离时属于 blocking：

- 无法确认拟发布 Condition 是否独立充分；
- 同一 Policy 混入不同 principal revisions，且冻结证据不足以拆分；
- `decision` 与 D2 `expected_revision` 冲突或不明确；
- Trigger 可能已经成为现实，却无法确认或重新校准；
- 成功 Shell 的整个 Gap 缺失，需要重新生成 Candidate；
- 关键阈值需要新数据；
- 核心工件损坏或引用无法恢复；
- 无法保证 Active Policy 按 OR 合同被 Runtime 正确判定；
- 受影响对象无法从其他合格 Policy 中隔离。

`PASSED` 只用于 `issue_count = 0` 且 `blocking_issue_count = 0` 的完全闭合结果。当前 schema 只有两个 status，因此存在任何 residual issue 时返回 `REVIEW_BLOCKED`；每项 `blocking` 与 `blocking_issue_count` 再区分问题是否阻止安全发布。使 `issue_count` 等于 `issues` 数量，`blocking_issue_count` 等于其中 `blocking: true` 的数量。

将完整结果写入 `output/work/final_review_result.json`，最终回复只返回与之完全相同、符合当前 `*.output_schema.json` 的单个 ReviewResult。
