# O3 Policy Compile

本 Turn 完成 O3 INITIALIZE 的编译阶段：消费已经冻结的完整 Stage-A Trigger surface，把 `TRIGGER_READY` Paths 压缩为 supplied Policy schema，使下游 W2 能够从一条未来消息中低自由度判断。研究问题已经由 Trigger Calibration 解决；本阶段的核心是忠实表达、条件原子化、Runtime 可判定性和全局语义连续性。

## 1. Working Contract 与输入优先级

按当前 node prompt 指定的顺序读取 D2、Previous Policy Set、Policy schema，以及完整的：

```text
trigger_calibrations.jsonl
trigger_calibration_state.json
worklist.jsonl
calibration_log.jsonl
wave_state.json
policies/
```

Trigger 语义按以下优先级使用：

1. **Stage-A Trigger Calibration**：决定 trigger-bearing actor/object、current state、Candidate Trigger、交易充分性、最小性、disclosure route 和 comparator basis；
2. **D2**：提供 provenance、`expected_revision`、ticker transmission、direction 和必要边界核对；
3. **Previous Policy Set / existing drafts**：提供语义连续性、已有边界比较和 canonicalization 参考。

本阶段不重新从 D2 `recognition_criteria` 推导 Trigger，也不承担常规开放研究。当前节点没有常规 Data MCP 预算；编译以冻结的 Stage-A artifacts 为主要事实来源。发现 Stage-A 结论内部不一致时，使用当前冻结输入和同线程研究认知直接修正相应 Trigger artifact，或将 Path 明确收敛为 `UNRESOLVED`，再继续编译。

## 2. 恢复与 Shell Compile Wave

先读取已有 Worklist、Policy drafts、Calibration Log 和 `wave_state.json`。从 `current_shell_id` 或首个未完成 Shell 继续，保留已经编译并通过当前质量检查的 Paths 和 drafts。

按 Shell 推进：

```text
读取本 Shell 全部 Stage-A dispositions
→ 编译 Trigger-ready Paths
→ 收敛 Trigger-unresolved Paths
→ 更新 Worklist、Calibration Log 和 Policy drafts
→ 关闭本 Shell compile wave
```

Stage A 已经建立完整 Path surface，本阶段以现有 `path_id` 为单位消费和必要时修正，不重新生成另一套覆盖面。

## 3. 从 Disposition 进入编译

每条 `TRIGGER_READY` Path 必须存在引用一致的严格 Trigger record，先重建：

```text
Path
→ calibrated Candidate Trigger
→ D2 expected_revision / direction
→ D2 provenance
```

`TRIGGER_UNRESOLVED` 不直接产生 Policy。若当前冻结证据不足以修正 Stage-A 结论，将对应 Worklist entry 更新为 `UNRESOLVED` 并写明当前无法形成有效 Future Direct Trading Boundary 的具体原因。

若本阶段确实修正 Trigger actor、状态、边界或 disposition，同步更新 `trigger_calibrations.jsonl` 与 `trigger_calibration_state.json`，使 Stage-A 工件、Worklist 和最终 Policy 不互相矛盾。Ready disposition 必须有匹配 record；Unresolved disposition 必须有具体 reason。

## 4. Atomic Decomposition

在写 `criterion` 前，把 Candidate Trigger 拆成彼此可独立判断真假的现实事实：

> 它实际包含几个能够单独发生、单独为假，或通常在不同时间由不同主体披露的状态？

**Single Condition 不等于一条长句。** 把 `A AND B AND C` 写进一个字符串，仍然是 hidden conjunction。主体、原值、新值和必要适用范围若共同构成同一次合同修改，可以属于一个不可分割状态；资格通过、量产、重复 shipment 和收入确认通常是多个阶段。

对每项事实检查：

```text
能否独立发生？
能否在其他事实未发生时单独为真？
是否通常由不同主体或消息来源确认？
是否发生在不同商业阶段或时点？
```

任一答案为是，都应把它视为独立事实，再按 Minimality 与同消息披露条件决定其归属。

## 5. Single Condition 与 Multi-condition

### Single Condition

表达一个完整、单义、可由一条自然消息判断的世界状态命题。Condition 可以包含识别该同一状态不可缺少的 actor、object、数值变化和适用 scope，但不隐藏其他独立经营阶段。

### Multi-condition

只有同时满足以下条件时使用多个 Conditions：

1. 每项都是同一直接交易判断不可缺少的独立事实；
2. 删除任一项后不再达到相同的交易充分性；
3. Stage A 确认同一种自然消息确实会同时披露全部事实。

全部 `activation_conditions` 采用当前同一条消息同时满足的固定 `AND` 语义。多条件是对真实复合披露的透明表达，不是把跨消息信息累积成状态机。

如果多个事实通常跨主体、时点或消息出现：

- 先判断其中是否已有一项更早地达到 Direct Trading Sufficiency；
- 独立替代 Trigger 按不同 Paths 或 Policies 表达；
- 调整 actor 或 Trigger 粒度后仍没有自然消息级边界，则将当前 Path 收敛为 `UNRESOLVED`。

条件数量服务于最早可靠的边界；不以 Single Condition 比例或最少 Policy 数量为目标。

## 6. 编译每个 Activation Condition

按以下顺序形成四层语义：

### `reference_state`

直接说明 trigger-bearing actor/object 当前具体处于什么状态，以及哪些原 D2 future facts 已被吸收到现实或 baseline。避免用宽泛行业背景替代比较起点。

### `trigger_boundary`

只表达相对于当前状态，哪一步未来变化构成新的交易边界。它应与 Stage-A `candidate_trigger` 保持一致，既不是普通进展，也不延后到完整经营兑现。

### `qualifying_evidence`

说明未来消息中出现什么事实，足以认定 boundary 已经跨过。它负责事实确认，并可排除计划、样品、无约束意向或其他不足以成立的弱证据；它不是追加更多经济必要条件的地方。

### `criterion`

把边界压缩为 Runtime 最终判断的自足世界状态。根据实际判断需要，让 W2 能识别：

```text
trigger-bearing actor / object
+ future state change
+ necessary product/customer/region/contract/time scope
+ comparator（当判断依赖相对或程度变化时）
```

Criterion 明显短于完整 Calibration，但不能省略 Round 1 真值判断所需的关键语义。

## 7. Comparator 与低自由度表达

“明显、重大、大幅、广泛、持续、高位、健康、实质”以及“进一步、提前、延后、相对此前”等表达若没有比较锚，仍需完成 Calibration。

优先顺序：

```text
明确业务状态
> 与当前 reference_state 比较
> 与最新正式 guidance / commitment / timeline 比较
> 与当前明确数值或有依据的可比区间比较
> 开放式程度判断
```

例如，“重大质量问题”应尽可能落为会导致 qualification 暂停、deployment freeze、产品撤回或订单削减的业务状态。若保留“明显下降”，Criterion 至少说明哪个指标相对于哪个当前值或正式区间发生下降。

Runtime Round 1 使用紧凑 Projection，不读取完整 Calibration；因此关键 comparator 必须能从 `criterion` 本身识别。数值阈值来自 Stage-A research、D2 baseline 和经济意义，不为了形式精确而任意创造。

## 8. Realization Leakage Test

逐条检查：

> Criterion 是否把“为什么 Trigger 对 ticker 重要”的后续兑现重新写回触发条件？

如果客户采用、binding commitment、正式资格或规则生效已经由 Stage A 证明 Direct-Trade Sufficient，后续 shipment、revenue、margin、市场份额或股价反应不再进入 Condition。D2 expected revision 与 `decision` 解释经济意义；Condition 只表达触发该修订的现实状态。

若删除后续结果仍支持同样的直接判断，按 Deletion Test 删除。只有 Stage A 已证明该跟随事实本身不可缺少且会在同一自然消息披露时，它才可以保留。

## 9. Disclosure Consistency Test

最终 `criterion` 和 `qualifying_evidence` 必须符合 Stage A 确认的 disclosure route：同一个自然发布者或消息类型能够掌握并披露判断所需的事实。

若 Stage A 认定客户公告可自然确认采用，Compile 不再附加只能由目标公司财报、另一供应商或后续行业数据确认的独立事实。发现这种不一致时，修正 Trigger record 或 disposition，再继续 drafting，而不是扩大 Condition 制造 Synthetic Message。

## 10. 完成 Policy 表达

先确认 `decision` 与 Path 的 D2 transmission 一致，再写精确 `source_refs`；每项只使用 D2 中真实存在且支持当前交易含义的 `shell_id + expectation_id + gap_id`。

最后依次完成：

- `title`：简短概括现实触发主题；
- `activation_summary`：紧凑表达什么状态成立时采取何种方向；
- `match_scope`：最后写，描述哪些消息值得召回当前 Policy，可以宽于 Condition，但围绕同一现实对象和变化路径。

完整编译顺序是：

```text
Candidate Trigger
→ Atomic Condition(s)
→ Calibration
→ decision / source_refs
→ title / activation_summary
→ match_scope
```

这样避免从“哪些新闻与主题有关”反向发明 Activation Boundary。

## 11. Canonicalization

创建或修改 draft 前，比较 Previous Policy Set、已有 drafts 和当前待编译 Trigger 的：

```text
trigger-bearing actor / object
+ state transition
+ Activation Boundary
+ decision
```

四者实质相同时维护同一 Policy，合并去重后的 `source_refs` 并让相关 Paths 指向同一 temporary Policy ID。具体 actors 能够独立发生、独立披露并各自产生足够 expectation delta 时保持不同 Policies，即使它们来自同一 D2 Gap、同一行业主题和同一方向。

文案、例示来源或消息措辞不同不足以拆分；同一抽象主题也不足以合并。

## 12. Progressive Write 与 Compatibility Log

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

`source_kind` 使用 `D2`、`REFERENCE_VIEW`、`WEB` 或 `DATA_MCP`，与实际 Stage-A source basis 一致。Trigger-ready 且已解决缺口时写 `resolved=true`；最终仍无法建立边界时写 `resolved=false`。该 log 保存缺口与结论，不重复完整 Trigger record 或工具轨迹。

`TRIGGER_READY` 编译成功后将 Path 更新为 `COMPILED` 并写入有效 `policy_ids`；无法形成有效未来 Policy 的 Path 更新为 `UNRESOLVED` 并写具体 `unresolved_reason`。

## 13. Compile Quality Gate 与 Wave Completion

一项 Trigger 只有满足以下条件才形成 Policy draft：

1. Stage A disposition 与严格 record 闭合；
2. 每个 Condition 是独立可判定的世界状态；
3. 没有把跨主体、跨阶段或跨消息事实隐藏进一个 Condition；
4. 多条件确实能由同一自然消息全部满足；
5. `reference_state` 是直接比较锚；
6. 所有相对或程度判断有 comparator；
7. `criterion` 对 Runtime Round 1 自足；
8. `qualifying_evidence` 只承担事实确认；
9. 没有 realization leakage；
10. Condition 与 Stage-A disclosure route 一致；
11. direction、source refs 和 canonicalization 保持准确。

一个 Shell 在全部 Paths 已成为 `COMPILED` 或 `UNRESOLVED`、Policy mappings 和适用 Calibration Log 已闭合后完成。随后使用现有 WaveState 字段更新 `completed_shell_ids`、`current_shell_id`、`completed_path_ids` 和 `updated_at`，再进入下一 Shell。

全部成功 Shell 完成后，确认所有 terminal Paths 已进入 `completed_path_ids`、`current_shell_id=null`、Policy drafts 可按 supplied schema 解析。最后按当前 node output schema 返回单个 `O3RunResult`，计数与 workspace 实际状态一致；完整业务产物保留在 workspace。
