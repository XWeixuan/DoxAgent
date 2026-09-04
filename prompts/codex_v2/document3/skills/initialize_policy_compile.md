# O3 Policy Compile

你的任务不是把 Trigger record 重新排版成 Policy，而是判断哪些已经研究充分的 Candidates 属于同一次决策，并为每个 Condition 重新形成稳定 `criterion`、当前具体 `reference_state` 和基于当前市场预期的 `trigger_boundary`。Candidate record 是研究依据，不是字段模板；一对一字段映射不能替代这一轮编译判断。

```text
逐项复核独立充分性
→ 识别候选之间的真实关系
→ 合并属于同一次决策的替代 Triggers
→ 编译一次性激活的 OR Policies
```

Compile 既不是对 Stage A 的机械包装，也不是第二次开放研究。它负责清除辅助事实、合并同一 occurrence 的 Evidence variants、建立正确的 Policy groups，并使最终表达能够由 Runtime 低自由度判断。

## 1. Working Contract 与输入优先级

按当前 node prompt 读取 D2、Previous Policy Set、Policy schema，以及完整的：

```text
trigger_calibrations.jsonl
trigger_calibration_state.json
worklist.jsonl
calibration_log.jsonl
wave_state.json
policies/
```

语义优先级是：

1. **Stage-A Trigger Calibration**：Candidate occurrence、trigger-bearing actor/object、current state、独立充分性、最低边界、disclosure route 与 judgeability；
2. **D2**：principal expectation revision、ticker transmission、direction 与 provenance；
3. **Previous Policy Set / existing drafts**：Policy 连续性、已有边界、身份与 canonicalization 参考。

本阶段在冻结输入内完成语义复核与局部修正，不重新从 D2 `recognition_criteria` 生成 Trigger Surface，也不开展常规开放 Web/Data MCP 研究。若 Candidate 需要新增实质研究才能确定充分性或边界，将其收敛为 `UNRESOLVED`，并在现有工件中写明具体缺口；辅助 Conditions 不能代替缺失研究。

Compile 负责更新最终 Worklist status 和 `policy_ids`、Policy drafts、`calibration_log.jsonl`、`wave_state.json`，以及必要时同步修正 Trigger record/state；最终回复严格使用 supplied `O3RunResult`。

逐项复核每个 Candidate，并以完成关系分类后的 provisional group 为单位形成 Policy 业务语义。程序适合读取、检索、计数、排序、ID/引用检查、schema validation、格式转换，以及在内容已逐项形成后的机械写入；循环、模板、字段映射或字符串拼接不代替 Conditions、Calibration、title 或 `match_scope` 的语义编译。

## 2. Recovery 与 Shell Compile Wave

先恢复已有 Stage-A 工件、Worklist、Policy drafts、Calibration Log 和 WaveState。从 `current_shell_id` 或首个未完成 Shell 继续，保留已经完成并通过当前语义检查的 drafts。

每个 Shell 必须先读取全部 Candidate dispositions，再开始编译：

```text
load all Candidates in the Shell
→ reconstruct every ready Candidate
→ review standalone sufficiency
→ classify Candidate relationships
→ build provisional Policy groups
→ compare with the global Working Registry
→ write or update Policy drafts
→ map all member Paths
→ close the Shell
```

以完成关系分类的 provisional Policy group 作为 progressive write 单位，而不是看到第一条 Candidate 就锁定一条 Policy。这样，同一 Shell 后续出现的替代 Trigger、Evidence variant 或重复 occurrence 能进入同一次分组判断。

新 Shell 的 groups 同时与 Previous Policy Set、已完成 Shell 的 drafts 和当前 Shell 其他 groups 比较。跨 Shell 指向同一 principal expectation revision 和一次性决策边界时，更新同一个 canonical draft，并追加准确的 source refs 与 Path mappings。

## 3. 重建 Candidate 的业务含义

对每个 `TRIGGER_READY` Path，联合 Trigger record、所属 Unit/Gap 和 D2 transmission，重建以下内部理解：

```text
real-world occurrence
trigger-bearing actor / object
current state and trigger boundary
principal expectation revision
ticker transmission and direction
evidence routes
source provenance
```

这些是编译判断，不新增 schema 字段。分别回答：

- **Occurrence**：现实中究竟发生了什么；发布者、报道渠道和证据数量不是 occurrence 本身。
- **Principal revision**：哪个 expectation component 的概率、时点、规模、经济性、实现路径或风险发生什么变化。
- **Decision meaning**：Candidate 成立时为何支持该 `LONG` / `SHORT`，以及它消费哪一项当前 baseline 下的决策边界。
- **Evidence routes**：哪些现实消息形式能够确认同一个 occurrence；来源数量不等于 Candidate 数量。

`TRIGGER_UNRESOLVED` 不直接产生 Policy。Ready Path 必须有引用一致的严格 Trigger record；Unresolved disposition 必须有具体原因。若 Compile 在冻结材料内修正 actor、状态、边界或 disposition，同步更新 `trigger_calibrations.jsonl` 与 `trigger_calibration_state.json`，使 Trigger、Worklist 和 Policy 保持一致。

## 4. Standalone Sufficiency Review

对每个 ready Candidate 单独复核：

```text
Current baseline
+ only this Candidate
+ all other Candidates unknown
```

判断该 occurrence 是否仍然：

- 相对 current state 形成新增量；
- 使相关 expectation 发生显著变化；
- 通过 D2 transmission 对目标 ticker 产生稳定方向；
- 不依赖另一条 record 才获得经济意义；
- 不是另一 occurrence 的 Evidence、corroboration 或后果说明；
- 仍然面向未来，并可由一条新消息判定。

复核结果按以下方式处理；这些类别只控制编译，不新增输出字段：

- **Ready as-is**：独立充分且 Calibration 完整，进入关系分类。
- **Locally repairable**：冻结材料已足以修正 actor scope、current-state 重叠、Evidence 泄漏、comparator、occurrence wording 或最低充分边界；修正并同步 Trigger 工件后进入关系分类。
- **Research-dependent**：新增外部研究才能确定充分性或边界；该 Path 收敛为 `UNRESOLVED`，记录会改变结论的具体未知项。
- **Supporting only**：单独不产生显著、定向 update；若它只是同一 occurrence 的证据，将其留在 Stage A 的 disclosure route、source basis 或 judgeability 记录中，不形成 Condition。只有在相关 D2 source 确实支持接收 Policy 时，该 Path 才映射到该 Policy；没有有效归属时收敛为 `UNRESOLVED`。

本阶段复核 Candidate 单独成立时的经济意义，而不是它与其他 Candidates 能否拼成完整证明链。

## 5. Candidate Relationship Classification

在整个 Shell 范围内对通过复核的 Candidates 分类。关系由 occurrence、principal revision 和决策边界决定，而不是主题、方向或措辞相似度。

### Same occurrence, different Evidence

现实动作、actor/object 和 boundary 相同，仅发布者、来源或证据形式不同。合并为一个 Condition；Evidence routes 保留在 Stage A 研究工件中，Policy 仅合并真实 source refs，不制造多个 OR Conditions。

### Semantic duplicate

措辞不同，但 actor、state transition、boundary、revision 和 direction 实质相同。保留最自然、最低充分且最容易判定的 canonical 表达。

### Nested Candidates

一个状态完全包含另一个，且较强状态发生时较弱边界必然成立。若较弱边界已经独立充分，保留最低充分边界，较强状态不再形成同义 Condition；若较强状态本身代表另一项新的 principal revision 或独立决策机会，则建立不同 Policy。

若一个 Candidate 描述 aggregate state，另一个描述促成该 aggregate state 的具体 mechanism 或 actor realization，判断具体事件是否已被 aggregate boundary 覆盖。已覆盖且没有额外独立 revision 时映射到 aggregate Policy 或去重；具体事件自身形成新的 expectation delta 时建立独立 Policy。主题和措辞不同不使两者自动成为平级 OR members。

### Decision-equivalent alternative occurrences

Occurrences 不同，但每项单独充分，修改同一个 principal expectation revision，支持相同方向，并会消费同一个 current-baseline 决策边界。它们组成同一 Policy 的 `OR` Conditions。

使用一次性边界反事实：假设 C1 今天触发并使 Policy 被消费、现实 baseline 已更新，C2 明天随后发生是否仍会产生新的、值得再次交易的 expectation delta？若是，C1/C2 属于不同 Policies；若否，才可能是同一 Policy 的替代充分路径。

### Different principal revisions

主题或方向可能相同，但 expectation component、revision type、经济机制或增量决策含义不同。例如短期 timing 与长期规模、公司自身经营与竞争格局分别变化。它们形成不同 Policies。

### Supporting chain

Qualification、shipment、revenue、margin 等可以共同构成完整确认链，但其中单独不具备交易充分性的环节只是支持信息。它可以扩大 `match_scope` 或不进入最终 Policy；证明路线保留在 Stage A 工件中，不在 Published Policy 中另建证据字段。

### Natural composite occurrence

多个事实单独不足，但共同描述同一次自然合同、产品、规则或生产状态，且一条合理消息可以确认整体 occurrence。将整体编译为一个 Condition，其 actor、object、scope、period、magnitude 等是事件属性，不拆成多个 OR Conditions。

### Internal OR Check

若拟定的 `criterion` 含“或 / either / alternatively”等替代关系，分别判断各分支能否独立发生并单独达到 Direct Trading Sufficiency：同一 principal revision、方向和一次性边界的分支拆成 Policy-level OR Conditions；改变不同 revision 或 execution meaning 的分支拆成 Policies。只有对同一 occurrence 的等价表述或共同必要属性才留在一个 `criterion` 中。

## 6. 建立 Provisional Policy Groups

每个 provisional group 在内部明确：

```text
principal expectation revision
decision
member Candidate Triggers and Paths
source refs
common one-time decision boundary
```

这些内容不增加到 Policy schema。Candidates 只有同时满足以下关系才进入同一 Policy：

1. 修改同一个 principal expectation component；
2. revision type 相容；
3. `LONG` / `SHORT` direction 相同；
4. current-baseline execution meaning 相同；
5. 任一 Candidate 成立都会消费同一个 Policy boundary；
6. 任一 Candidate 成立时，Policy title 与 decision 都准确；
7. Candidates 之间不是 occurrence/Evidence 关系，也不是必须共同成立的关系。

同一 Policy 固定采用：

```text
Policy Activated = C1 OR C2 OR ... OR Cn

Ci + current reference state
→ material expectation update
→ Policy decision
```

任一 Condition 首次满足，Policy 即激活；Runtime 不等待其他 Conditions，同一消息满足多个 Conditions 也只激活一次。剩余 Conditions 不表示完成进度，激活后的新 baseline 和后续路径由 Maintenance 重新判断。Compile 只表达当前 baseline 下的替代触发面，不编码预计的 `C1 → C2 → C3` 顺序。

只有一个独立充分 Candidate 时保留单条件 Policy；多个 Conditions 只来自真实的 decision-equivalent alternative occurrences。

## 7. 编译 Activation Conditions

Supplied schema 是字段含义与结构的唯一输出合同。Condition 的单位是自然完整 occurrence，而不是最小逻辑 predicate；以下顺序用于把同一个已校准事件重新编译成彼此分工清楚的字段。

### `criterion`

用自足的业务命题表达：

```text
actor or actor class
+ object and state change
+ necessary scope / period
+ material boundary or comparator
```

Actor、product、customer、contractual nature、quantity、threshold 和 commercial stage 可以共同定义同一个 occurrence，不因逻辑可拆分就生成多个 Conditions。Criterion 只描述现实发生了什么，不解释它为什么对 ticker 有利或不利，也不承载完整 thesis、披露来源清单、后续 revenue/margin realization、D2 recognition chain 或内部分析过程。

### `calibration.reference_state`

写明 cutoff 时点 trigger-bearing actor/object 当前具体、已确认且直接相关的现实状态，以及 Candidate 相对哪个现实起点构成新增量。它是 condition-specific 的现实实例，不是 Unit/Shell State 摘要或 Criterion 复述；不同 OR Conditions 可以有不同 reference states。

### `calibration.trigger_boundary`

基于 Stage A 已研究的当前市场 expectation，写明现实还需向当前 Policy 方向跨过的最低可交易 surprise boundary，包括必要 actor、stage、magnitude、scope、timing 或 comparator。它与 Candidate 语义一致，但不是 `candidate_trigger` 或 `criterion` 的复制。

当 Criterion 使用“扩大、下调、提前、推迟、持续、大规模、主流、实质、显著”等相对或程度表达时，使用 Stage A 校准出的可观察数值或 categorical business boundary；若程度决定 sufficiency 而两者均不存在，该 Condition 尚不可编译。Criterion 自身包含 Runtime 判断所需的必要 comparator，数值不为形式精确而创造。

完成后只问一个 Runtime 问题：

> 如果 Runtime 只看到 Criterion 与当前消息，是否能判断这个自然 occurrence 已经成立？

这检查语义自足性，不要求 Runtime 重新判断完整 expectation 是否兑现。

## 8. 完成 Policy-Level Fields

使用以下顺序，先固定决策结构，再生成召回语言：

```text
Provisional Policy Group
→ Activation Conditions and Calibration
→ decision / source_refs
→ title
→ match_scope
```

- `decision` 来自所有 member Candidates 共同的 ticker transmission，只使用 supplied `LONG` 或 `SHORT`。
- `activation_conditions` 在当前 schema/version 中固定按 OR 执行；不输出额外 mode 字段。
- `source_refs` 纳入所有真实支持该 Policy 的 D2 refs，精确存在、去重并与 member Paths 一致；主题相近但不支持该 principal revision 的 Gap 不加入。
- `title` 用简短中文概括 principal expectation revision 或现实触发主题，不枚举 Conditions 或写成长因果句。
- `match_scope` 针对当前 Policy 的 OR Conditions 重新生成，取直接相关消息、确认或否定消息、接近 boundary 的进展和同一 occurrence 不同披露渠道的合理并集。它宽于 Activation，但仍围绕当前 Policy reality surface；不使用跨 Policy 通用事件类别清单，也不从 D2 `recognition_criteria` 自动扩充。

## 9. Working Registry Canonicalization

写入 draft 前，比较 Previous Policy Set、completed-Shell drafts 与当前 provisional groups 的：

```text
principal expectation revision
+ real-world occurrence surface
+ trigger boundaries
+ decision
+ one-time execution meaning
+ source coverage
```

实质相同时维护同一个 Policy，合并去重后的 OR Conditions 与 source refs，并让所有相关 Paths 指向同一 temporary `policy_id`；同时保持与 Previous Policy 的语义连续性，供确定性 assembly 延续稳定身份。交易含义实质改变时建立独立 Policy。

以下差异通常要求分开：direction、principal revision、公司自身经营与竞争格局、短期 timing 与长期规模、概率变化与后续独立经济兑现，或任一 Condition 成立时无法共用同一 title 和一次性 execution meaning。标题、措辞或 source Gap 不同本身不足以拆分；宽泛主题或相同方向也不足以合并。

Condition 去重采用同一逻辑：同一 occurrence 的不同来源、同一最低边界的不同措辞、被最低充分状态完全包含的更强状态，以及纯 supporting evidence 只保留一个 Condition。Actors 能独立发生、各自足够重要，且不同 occurrences 消费同一决策边界时，可以保留多个 OR Conditions。

## 10. Progressive Write 与 Stage Completion

一个 provisional group 完成关系分类、编译和 canonicalization 后立即：

```text
write or update output/work/policies/<temporary_policy_id>.json
→ update the Working Registry
→ attach policy_id to every member Path
→ update Worklist statuses
→ write applicable Calibration Log entries
→ update wave_state.json
```

Policy draft 只使用 supplied schema 的现有字段。INITIALIZE 使用 temporary `policy_id` 和 Policy 内 `condition_id`；稳定身份由确定性 assembly 分配或延续。

对 `d2_boundary_sufficient=false` 的 Path，在 `calibration_log.jsonl` 中保留现有五字段兼容记录：`path_id`、`calibration_need`、`source_kind`、`finding`、`resolved`。它记录 Stage-A 已识别的缺口与最终结论，不重复 Trigger record 或工具轨迹；`source_kind` 与实际 source basis 一致。

Path 的终态必须与工件一致：

- 成功进入某个 Policy 的 `TRIGGER_READY` Path 设为 `COMPILED`，写入实际 `policy_ids`；
- Stage-A unresolved 或 Compile review 后仍无法形成有效未来边界的 Path 设为 `UNRESOLVED`，写明具体 `unresolved_reason`；
- Trigger record/state、Worklist、Policy mappings 与 source refs 同步反映任何局部修正。

关闭一个 Shell 前确认：全部 Paths 已进入 `COMPILED` 或 `UNRESOLVED`；所有 ready Candidates 均经 standalone review 和关系分类；每个 Condition 单独充分；Evidence variants 与 supporting information 未变成 Conditions；同一 Policy 成员共享 principal revision、direction 和一次性决策含义；title 对任一 Condition 准确；`match_scope` 覆盖全部 Conditions 且宽于 Activation；Path、Trigger、Policy 与 source refs 一致。

随后使用现有 WaveState 字段更新 `completed_shell_ids`、`current_shell_id`、`completed_path_ids` 和 `updated_at`。全部成功 Shell 完成后，`current_shell_id=null`，所有 terminal Paths 进入 completed state，Policy drafts 均可由 supplied schema 解析，counts 与 workspace 一致；最后只返回 supplied `O3RunResult`，完整业务产物留在 workspace。
