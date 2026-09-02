# O3 Policy Compile

本 Turn 完成 O3 INITIALIZE 的编译阶段：把冻结的 Stage-A Trigger hypotheses 编译为 supplied Policy schema，使 W2 能够依据一条未来消息和紧凑 Runtime Projection 判断预先定义的现实状态是否成立。

Stage A 是 actor state、expectation update 与消息生产研究的 authority；Policy Compile 是 Boolean representation 与 Runtime semantics 的 authority。Compile 不重做完整研究，也不以维护 Stage-A 原文为目标。它应使用 Stage-A 证据独立判断 Candidate Trigger 能否成为可执行 predicates，并在发现 hidden conjunction、realization leakage、不可观察 comparator 或不现实的消息结构时修正 Trigger artifacts。

编译质量按以下顺序判断：

```text
忠实于 D2 revision space 与 ticker transmission
→ Trigger 能由真实消息承载
→ 保留最早的 Minimal Sufficient Predicate Set
→ W2 可以低自由度判断
→ 最后优化表达、Condition 与 Policy 数量
```

## 1. Working Contract 与输入关系

按当前 node prompt 指定的顺序读取 D2、Previous Policy Set、Policy schema，以及完整的：

```text
trigger_calibrations.jsonl
trigger_calibration_state.json
worklist.jsonl
calibration_log.jsonl
wave_state.json
policies/
```

各输入承担不同职责：

- Stage-A records 提供经过研究的 Trigger hypothesis、current state、expectation update、minimality、message production 与 judgeability；
- D2 提供 provenance、`expected_revision`、ticker transmission、direction 和 revision boundary；
- Previous Policy Set 与 existing drafts 提供语义连续性、已有边界和 canonicalization 参考；
- supplied Policy schema 是最终 draft 的精确字段合同。

本阶段不从 D2 `recognition_criteria` 重新发明 Trigger，也不承担常规开放研究；当前节点没有常规 Data MCP budget。Stage-A 文案与 Runtime 语义冲突时，使用现有 Stage-A evidence、D2 和同线程研究认知重新检查 Trigger，而不是优先保留原句。若确实缺少新的现实事实才能确定有效边界，使用当前 unresolved 机制留给后续处理。

## 2. 恢复与 Shell Compile Wave

先读取已有 Worklist、Policy drafts、Calibration Log 和 `wave_state.json`。从 `current_shell_id` 或首个未完成 Shell 继续，保留已经编译并通过当前质量检查的 Paths 和 drafts。

按 Shell 推进：

```text
读取本 Shell 全部 Stage-A dispositions 与 records
→ 对 Trigger hypotheses 做 Predicate Compilation
→ 收敛 unresolved Paths
→ 更新 Worklist、Calibration Log 和 Policy drafts
→ 关闭本 Shell compile wave
```

Stage A 已建立完整 Path surface。本阶段以现有 `path_id` 为单位消费和必要时修正，不另建一套覆盖面。

## 3. 从 Disposition 进入编译

每条 `TRIGGER_READY` Path 必须存在引用一致的严格 Trigger record。先重建：

```text
Path 的单一交易含义与 direction
→ Stage-A current state 与 Candidate Trigger
→ Marginal expectation update
→ D2 expected_revision / ticker transmission
→ D2 provenance
```

把 record 视为研究充分的 hypothesis，而非已经通过 Runtime 表示检查的最终 Condition。Compile 可以基于该 record 的 `trade_sufficiency`、`minimality`、`disclosure_route`、`judgeability` 和 `source_basis` 修正 actor、事实集合、边界或 disposition。

`TRIGGER_UNRESOLVED` 不直接产生 Policy。现有证据足以修正时可以恢复为 ready 并继续编译；仍无法形成有效 Future Direct Trading Boundary 时，将对应 Worklist entry 更新为 `UNRESOLVED` 并写明具体原因。

修正 Trigger 语义或 disposition 时，同步更新 `trigger_calibrations.jsonl` 与 `trigger_calibration_state.json`，使 Trigger artifact、Worklist 和 Policy 一致。只使用 supplied schema 中已有字段。

## 4. Predicate Decomposition

在写任何 Condition 前，回答：

> 未来消息需要让 W2 对几个独立 Boolean propositions 作出 TRUE / FALSE / INSUFFICIENT 判断？

将 Candidate Trigger 在内部拆成 `P1 / P2 / ...`；这是编译分析，不是新增输出字段。每个 predicate 检查：

```text
能否在其他事实未发生时独立为真或为假？
是否处于不同 causal layer 或商业阶段？
是否由不同 information holder 掌握？
是否通常由不同主体、载体或时点披露？
```

主体、对象、原值、新值和必要适用范围若共同定义同一次不可分割状态变化，可以属于一个 predicate。qualification、production adoption、repeated shipment、收入与利润通常是不同 predicates。`BOM 下调 + 订单削减 + 库存上升` 共同描述“需求恶化”，不使三项事实变成一个 predicate。

把多个 predicates 写进一条长 `criterion` 仍是 hidden conjunction。Single Condition 是一个 Boolean predicate，不是一个主题或完整商业叙事。

## 5. Minimal Predicate Set 与 Condition 数量

先按 Stage-A causal analysis 独立判断每个 predicate 位于：

```text
Precursor
→ Trigger
→ Transmission Evidence
→ Realization Evidence
```

Compile 自己判断 predicate 是定义 Trigger，还是只说明 Trigger 后来产生 downstream outcome。Stage-A `trade_sufficiency` 是重要依据，不是豁免检查。shipment、revenue、margin、份额或市场反应若只证明前一状态最终兑现，应从 Condition 中删除。

对剩余 predicates 逐项问：

> 删除 P 后，目标 expectation lever 的方向性更新及 D2 ticker transmission 是否仍然成立？

若仍成立，P 只增加确认程度，不属于 Minimal Sufficient Predicate Set。若删除后必须假设另一项关键未知事实才能建立同一交易判断，P 才必要。

Condition 数量由最终 predicate 数量推导：

```text
1 个独立必要 predicate
→ 1 个 Condition

N 个独立必要 predicates
+ 同一种正常消息确实会同时确认全部 predicates
→ N 个 Conditions
```

全部 `activation_conditions` 使用当前同一条消息同时满足的固定 `AND` 语义。多个必要 predicates 若不属于同一正常消息，说明问题在 Trigger granularity，而不是 Condition 写法。此时利用已有 Stage-A evidence 重新判断：

```text
是否已有某个更早 predicate 单独充分？
actor / object 是否拆分错误？
是否把不同 causal layers 或两条信息生产链并入同一 Trigger？
```

据此把 Trigger 收窄到更早的充分 predicate，或将各 predicate 归回 Worklist 中已经存在且实际支持它们的独立 Paths；现有证据和 Path surface 都无法形成消息级边界时，收敛为 `UNRESOLVED`。Condition 数量和 Single/Multi 比例不是质量目标。

## 6. 编译 Activation Condition

对 Minimal Sufficient Predicate Set 中每个 predicate，按以下顺序编译：

### `reference_state`

说明该 actor/object 当前具体处于什么状态，以及哪些原 D2 future facts 已进入现实或 expectation baseline。它是直接比较锚，不是宽泛行业背景。

### `trigger_boundary`

说明相对于 `reference_state`，哪一项仍面向未来的状态变化第一次形成交易边界。它与修正后的 Candidate Trigger 一致，不延后到完整经营兑现。

### `qualifying_evidence`

先说明未来消息必须确认什么事实内容，再说明哪些现实来源能够可信地掌握或报道它。Source authority 与 state truth 是两个维度：公司公告、客户或供应商消息、监管文本、可信 sourced report 或行业情报都可能直接确认状态，取决于 Stage-A Message Production Model。

计划、意向、样品或讨论若尚未证明 predicate 所定义的状态，只说明它们确认了什么较早事实；不因来源不是 formal/official 就自动视为不足，也不为提高确定性追加后续经营结果。

### `criterion`

把一个 predicate 表达成 W2 可独立判断真假的自足命题，根据实际需要包含：

```text
actor / object
+ state change
+ necessary scope
+ observable comparator
```

Criterion 使用事件本身的领域语言，不写分析解释，也不复制 `natural disclosure`、`actor-specific`、`同一自然披露`、`一名可识别的` 等内部推理词。为什么该事件影响 ticker 已由 D2 transmission 与 Stage-A sufficiency承担。

Runtime Round 1 使用 `match_scope`、`criterion[]` 和 `activation_summary`，不读取完整 Calibration；真值判断必需的主体、状态、范围和 comparator 应在 Criterion 自身可见。

## 7. Observable Comparator

每个相对或程度判断都要回答：

> W2 在 Runtime Projection 与当前消息中，具体从哪里得到比较值或比较状态？

有效来源包括：

- Criterion 中明确写出的 current value/state；
- 公开且确定的 current commitment、guidance 或 timeline；
- 当前消息自身给出的 before/after；
- Runtime 可直接取得的明确历史状态。

“明显、重大、大幅、广泛、持续、高位、健康、实质”等词应优先转换为可观察业务状态。需要 W2 自行估算“无保护基线”“无上限情景”“正常库存”“合理水平”等反事实或主观基准时，Criterion 尚未完成，应回到 Calibration 重新规定边界。数值阈值应来自 Stage-A research、D2 baseline 与经济含义。

## 8. Disclosure Consistency

对最终每个 predicate 检查 Stage A 已研究的信息生产链：

```text
谁首先掌握该事实？
谁通常发布或报道？
通过什么消息载体？
该载体的正常内容边界是否包含此 predicate？
多个 Conditions 是否会在同一条该类消息中同时出现？
```

检查的是事实内容与信息生产机制，不只是 source class 名称。平台 BOM 与供应商 allocation、客户订单与目标公司财务结果可能属于不同信息链；一篇综合报道理论上可以汇总它们，不证明这些必要 predicates 会由一条正常消息同步产生。

发现不一致时，以 Compile 的 Runtime semantic authority 修正 predicate set、Trigger artifact 或 disposition。Policy 字段使用真实事件语言，不把“同一自然消息”测试写成固定输出模板。

## 9. Policy 表达与 `match_scope`

完成 Conditions 后确认 `decision` 与 Path 的 D2 transmission 一致，并写入精确 `source_refs`；每项只使用 D2 中真实存在且支持当前交易含义的 `shell_id + expectation_id + gap_id`。

- `title` 简短概括现实触发主题；
- `activation_summary` 用领域语言概括哪些状态成立时对应何种方向；
- `match_scope` 是 Trigger 周围的 retrieval envelope，回答“哪些消息值得让 W2 看一眼”。

生成 `match_scope` 时重新从以下元素建立召回范围，而不是改写 Criterion：

```text
actor / object
+ 相关状态变化
+ precursor
+ partial satisfaction
+ 可能改变 Trigger 判断的消息类型
```

它以 high recall 为职责，可以覆盖尚未满足 Activation 的相关消息。现实 message bus 中的公司、客户、供应商、监管消息、可信 sourced reporting 与行业报道都可以进入召回；最终事实是否充分由 Conditions 判断。`match_scope` 不承担 source precision，也不应默认收窄为 formal/official/direct confirmation。

完成后反向检查：一条与 Trigger 高度相关、但尚未满足 Condition 的消息是否仍可能落入 `match_scope`？若否，它只是 Activation paraphrase，需要扩展 retrieval envelope。

完整编译顺序是：

```text
Stage-A Trigger hypothesis
→ Predicate Decomposition
→ Minimal Sufficient Predicate Set
→ Condition Calibration / Criterion
→ decision / source_refs
→ title / activation_summary
→ 独立生成 match_scope
```

## 10. Canonicalization

创建或修改 draft 前，比较 Previous Policy Set、已有 drafts 和当前 Trigger 的：

```text
actor / object 类型
+ current-state dependency
+ state transition
+ Activation Boundary
+ disclosure structure
+ decision
```

比较的是状态依赖，不是名称是否不同。多个主体的 current state、下一边界、消息结构和 ticker transmission 实质相同，而且 Policy 每次只判断其中一个主体事件时，可以共享通用 Policy 并合并去重后的 `source_refs`。主体当前 baseline 或下一 transition 不同，则保持独立 Policy。

同一主题不足以合并；措辞、例示来源或 actor 名称不同也不足以拆分。相关 Paths 指向同一 temporary Policy ID；底层交易语义不同则使用独立 drafts。

## 11. Progressive Write 与 Compatibility Log

一项 Trigger 完成编译后立即：

```text
写入 output/work/policies/<temporary_policy_id>.json
→ 更新对应 Worklist status / policy_ids
→ 写入适用的 calibration_log 记录
→ 再处理下一 Trigger / Path
```

Policy draft 使用 supplied schema 的现有字段；INITIALIZE 使用 temporary `policy_id` 和 Policy 内 `condition_id`，稳定身份由确定性 assembly 分配或延续。

对 `d2_boundary_sufficient=false` 的每条 Path，使用 Stage-A 结论在 `calibration_log.jsonl` 中保留现有五字段兼容记录：

```text
path_id
calibration_need
source_kind
finding
resolved
```

`source_kind` 使用 `D2`、`REFERENCE_VIEW`、`WEB` 或 `DATA_MCP`，并与实际 Stage-A source basis 一致。Trigger-ready 且已解决缺口时写 `resolved=true`；最终仍无法建立边界时写 `resolved=false`。该 log 保存缺口与结论，不重复完整 Trigger record 或工具轨迹。

`TRIGGER_READY` 编译成功后将 Path 更新为 `COMPILED` 并写入有效 `policy_ids`；无法形成有效未来 Policy 的 Path 更新为 `UNRESOLVED` 并写具体 `unresolved_reason`。

## 12. Compile Quality Gate 与 Wave Completion

一项 Trigger 只有满足以下条件才形成 Policy draft：

1. Stage-A disposition、record 与 D2 refs 闭合，且 Trigger hypothesis 已通过独立 Runtime 语义检查；
2. Predicate Decomposition 完整，每个 Condition 只表达一个独立 Boolean predicate；
3. Minimal Sufficient Predicate Set 排除了 precursor、transmission explanation 和 realization evidence；
4. Condition 数量由必要 predicates 推导，没有把 conjunction 藏进 C1；
5. 多条件会由同一种正常消息同时确认，符合固定 same-message `AND`；
6. `criterion` 使用领域语言并对 Runtime Round 1 自足；
7. 所有相对判断使用 observable comparator；
8. `qualifying_evidence` 以事实内容为中心，来源要求符合实际 Message Production Model；
9. Condition 与 Stage-A information holder、publisher、message type 和 content boundary 一致；
10. `match_scope` 保持 high recall，能够召回相关但尚未触发的消息，不是 Activation paraphrase；
11. direction、source refs 和 State-Dependency canonicalization 准确；
12. Trigger artifact、Worklist、Calibration Log 和 Policy draft 语义一致。

一个 Shell 在全部 Paths 已成为 `COMPILED` 或 `UNRESOLVED`、Policy mappings 和适用 Calibration Log 已闭合后完成。随后使用现有 WaveState 字段更新 `completed_shell_ids`、`current_shell_id`、`completed_path_ids` 和 `updated_at`，再进入下一 Shell。

全部成功 Shell 完成后，确认所有 terminal Paths 已进入 `completed_path_ids`、`current_shell_id=null`、Policy drafts 可按 supplied schema 解析。最后按当前 node output schema 返回单个 `O3RunResult`，计数与 workspace 实际状态一致；完整业务产物保留在 workspace。
