# O3 Trigger Calibration

本 Turn 完成 O3 INITIALIZE 的研究阶段：从完整 D2 Potential Gap surface 中，研究出当前现实里最早、最小充分、可由自然消息披露且可被 W2 判断的 Candidate Trading Triggers。所有成功 Shell 完成本阶段后，编排才进入 Policy Compile。

本阶段的价值是先回答“什么消息第一次值得交易”，让后续节点只负责编译表达。优化目标不是尽可能多地产生 `TRIGGER_READY`，而是为每条能够形成真实 Direct Trading Policy 的 Path 找到可信、足够早且现实可披露的 Trigger。合理剩余不确定性不妨碍 Trigger 成立；极晚、极难发生或只能由合成消息确认的 Trigger 也不比诚实的 `TRIGGER_UNRESOLVED` 更好。

## 1. Working Contract

按当前 node prompt 指定的顺序读取：

```text
task.json
document2.json
reference_event_view.md
previous_policy_set.json
worklist.jsonl
trigger_calibrations.jsonl
trigger_calibration_state.json
Trigger Calibration record/state schemas
```

各输入承担不同角色：

- D2 是 expectation、经济传导、Gap 和 provenance 的研究基线；
- Reference View 是同一次 run 冻结的后续现实补充，不是完整世界状态；
- Previous Policy Set 提供已有 actor、边界和语义连续性的参考，本轮完整 Gap surface 仍由当前 D2 决定；
- Worklist 是 Stage A 建立的完整 Tradable Path surface；
- Trigger records 保存每条 ready Path 的研究结论；Trigger state 保存 Shell 进度和全部 Path dispositions。

本阶段更新 `output/work/worklist.jsonl`、`trigger_calibrations.jsonl` 和 `trigger_calibration_state.json`。Worklist 的 `status` 保持 `PENDING`，`policy_ids` 保持空列表；最终 `COMPILED / UNRESOLVED`、Policy drafts、`calibration_log.jsonl` 和 `wave_state.json` 由 Policy Compile 处理。

## 2. 恢复与冻结状态

先读取已有过程文件，从 `trigger_calibration_state.current_shell_id` 或首个未完成 Shell 继续。已有 Worklist entry、Trigger record 和 disposition 是本次 run 与 retry 之间的持久工作状态；按 `path_id` 和精确 D2 引用补齐未完成工作，保留已经闭合的 Shell。

D2、Reference View、Previous Policy Set 和 task 在本次 INITIALIZE 生命周期中保持冻结。使用 Reference View 自身提供的 snapshot、published 或 `as_of` 信息理解其时间边界；输入未提供的时间保持未知。本 Turn 的研究遵守 `task.json.cutoff_at`。

## 3. 一个 Shell 是一个完整研究 Wave

进入一个 Shell 后，先联合理解：

```text
core_question
boundary_rule
全部 Units
State
Realization Factors
Potential Gaps
```

回答：这个 Shell 描述怎样的一组 expectation uncertainties，各 Gap 从什么现实状态产生，哪些 actors、约束和传导彼此关联？保持完整 Shell 上下文有助于发现共享 current-state research、同一 Gap 的相反方向，以及看似同主题但由不同现实主体承载的 Paths。

进入新 Shell 时将其写入 `current_shell_id`。本 Shell 全部 Paths 形成 disposition 后，再加入 `completed_shell_ids` 并进入下一 Shell。

## 4. Worklist Gate：先展开完整 Path Surface

在本 Shell 的外部研究前，先处理全部 Potential Gaps。对每个 Gap 建立以下链条：

```text
现实主体或对象
→ 当前状态
→ 可能的新状态
→ D2 expected_revision
→ 目标 ticker 的净影响
→ LONG / SHORT
```

一条 Path 保持单一现实含义和方向。以下情况展开为不同 Paths：

- 不同 actors 可以独立变化；
- 同一 Gap 存在不同现实落点；
- 同一变化经不同传导形成相反方向；
- 存在彼此独立的 `A OR B` 触发路径。

D2 中的“多个客户”“多个 OEM”“竞争者供给”等集合表述不是默认 Trigger 单位。若具体 actors 能够独立发生、独立披露并各自形成预期差，先拆成 actor-specific Paths。每条 ready Path 最终只对应一项规范化 Candidate Trigger record；多个独立 Trigger 候选应先拆 Path，而不是并入同一 record。

使用现有 Worklist 字段写入全部 Paths：

```text
shell_id
expectation_id
gap_id
path_id
direction
path_summary
d2_boundary_sufficient
missing_calibration
status
policy_ids
unresolved_reason
```

`path_summary` 紧凑表达具体 actor/object、状态变化和 ticker 方向。`d2_boundary_sufficient=false` 时，将 `missing_calibration` 写成一个会改变 Trigger 选择的具体可回答问题，例如“该客户目前仍处验证，还是已进入具有约束力的商业采购”，而不是“研究客户进展”。完整 Worklist 落盘后再开始本 Shell 的 Trigger Research。

## 5. `d2_boundary_sufficient`

D2 只有已经直接提供一项同时满足以下要求的 Trigger Boundary 时才为 `true`：

```text
相对于最新现实仍面向未来
+ 最小交易充分
+ actor granularity 合理
+ 有现实消息的自然披露可能
+ W2 可直接判断
```

D2 `recognition_criteria` 是寻找边界的重要研究依据，不是默认 Activation Condition。它为了完整确认 Gap 可能列出资格、量产、shipment、收入或利润等多个阶段；完整确认 Gap 与第一次形成可交易预期差是不同问题。

`d2_boundary_sufficient=true` 只表示不需要额外外部 Calibration；该 Path 仍需完成本阶段六项 Trigger 测试并写入严格 Trigger record。

## 6. 建立 Trigger Surface

对每条 Path，先识别哪些具体 actors 或 objects 能承载下一项 expectation-changing event，再研究：

```text
谁现在处于什么状态？
哪些变化已经成为现实或进入 baseline？
基于当前状态，下一项合理可能的状态有哪些？
谁的哪项下一变化最早能够制造新的预期差？
```

Current State 应尽可能贴近 Trigger-bearing object：客户采用阶段、binding commitment、产品资格、商业部署、产能实际投放、合同状态、监管适用状态或可比指标，而不是宽泛行业背景。

D2 定义 expectation 与 revision space；Reference View 和必要的当前研究确定现实已经推进到哪里。Reference View 已确认的 D2 Future Gap 应吸收到 `current_state`，再沿原 Path 寻找下一项仍面向未来的 marginal boundary。Reference View 未记录某项变化，不证明它尚未发生。

## 7. Candidate Trigger 的六项测试

对每个候选 Trigger 依次完成以下思考；结论体现在 record 的具体内容中，而不是只写“已通过”。

### 7.1 Marginality Test

问：相对于最新确认现实和当前 expectation baseline，这项状态真的是新信息吗？已发生或已进入 baseline 的事实进入 `current_state`，不继续充当未来 Trigger。

### 7.2 Counterfactual Trade Test

假设这条消息现在真实出现，而后续 shipment、revenue、margin 或份额兑现仍未知：

> 按照 D2 已建立的研究，这项信息是否已经把相关 expectation 推过一个可以预先规定直接交易判断的边际边界？

观察它是否已经实质改变发生概率、实现时点、潜在规模、经济路径或关键风险中的核心维度。普通进展若仍依赖另一项关键事实才能形成明确方向，就继续寻找真正承担交易边界的变化；已经形成有方向且有经济意义的预期差，则接受正常的剩余不确定性。

### 7.3 Minimality / Deletion Test

如果候选实际包含 `A + B + C`，逐项删除：没有 C 时，A+B 是否仍支持同样的直接交易判断？如果是，删除 C 并继续，直到再删除任何必要事实都会使候选退回普通进展。

这里寻找的是最小交易充分集合，不是完整 thesis 的最小证明集合。资格、binding order 或正式规则变化已经足够交易时，后续 shipment、revenue、margin 或市场反应属于 realization evidence，不再追加到 Trigger。

### 7.4 Actor Granularity Test

检查候选是否把多个可以独立变化的客户、供应商、平台或竞争者聚成集合事实。进一步判断哪些 actors 当前真正 relevant、各自位于什么状态、哪一个 actor 的独立下一变化已经足以形成预期差。不同 actor 的 Trigger 分别评估和记录。

### 7.5 Disclosure Plausibility Test

明确回答：

```text
谁最可能发布？
是什么类型的消息？
发布者是否掌握必要事实？
候选中的事实是否通常在同一时点披露？
基于当前状态，该事件本身是否具有合理发生可能？
```

评估的是真实信息生产过程，不是能否把若干事实写进同一句话。若没有与具体 actor、状态变化和消息类型相匹配的自然披露路径，调整 Trigger granularity 或寻找新的候选。

### 7.6 W2 Judgeability / Comparator Test

假设 W2 只有未来消息和 Runtime Policy：它是否可以直接判断 Trigger 成立或不成立？涉及“明显、重大、大幅、广泛、持续、高位、健康、实质”以及“进一步、相对此前”等判断时，必须形成可供后续 Criterion 使用的显式 comparator。

优先把程度词翻译成业务状态，例如客户 qualification 暂停、deployment freeze、binding commitment 下调或订单削减。无法完全消除时，明确与当前状态、正式 guidance、commitment、timeline、具体数值或有依据的历史区间相比发生了什么变化。

## 8. Targeted Research

围绕已经明确的 actor、current state 和 Candidate Trigger 问题选择信息来源：

```text
D2
→ Reference View
→ targeted Web Search
→ 必要时只读 Data MCP
```

- 公开事实、商业阶段、客户采用、合同、监管、生产状态和 disclosure pattern 通常适合定向 Web Search；一手或官方资料能回答时即可形成结论，否则使用可靠行业或二手来源。
- 历史序列、可比区间、provider-specific consensus 或结构化市场指标适合 Data MCP；只有 D2 未提供当前 Trigger 所需的可比信息，或该数据在 D2 后确有现实可能更新时才重新查询。
- 同一 actor-level finding 可以支持多个 Paths，但每条 Path 保留自己的 Trigger 结论和来源映射。

Research 在已经能够形成可辩护的 `current_state + candidate_trigger + trade_sufficiency + disclosure_route + judgeability` 时结束。目标是解决当前 Trigger 问题，不是穷尽主题或重新形成完整行业报告。经过针对性研究仍无法找到现实可披露且足以形成直接交易判断的 Future Trigger 时，使用 `TRIGGER_UNRESOLVED`；一般的信息不完整本身不等于 unresolved。

## 9. Trigger Calibration Record

每个 `TRIGGER_READY` Path 写一条且仅一条符合 supplied schema 的 record：

- `trigger_bearing_actor`：谁发生变化；
- `trigger_bearing_object`：产品、合同、客户关系、工厂、产能、采购或指标等具体对象；
- `current_state`：最新确认现实和已进入 baseline 的状态；
- `candidate_trigger`：仍面向未来的最小充分状态命题；
- `trade_sufficiency`：为何它本身已足以修改 expectation 和支持 Path 方向，即使后续兑现未知；
- `minimality`：哪些跟随事实被排除，保留事实为何不可再减；
- `disclosure_route`：自然发布者、消息类型以及同消息可行性；
- `judgeability`：未来消息中的真值边界和必要 comparator；
- `source_basis`：实际支持上述结论的 D2、Reference View、Web 或 Data MCP 来源标识；
- `disposition`：`TRIGGER_READY`。

每个 Path 同时在 `trigger_calibration_state.path_dispositions` 保留精确的 `shell_id + expectation_id + gap_id + path_id` 与 disposition。`TRIGGER_UNRESOLVED` 在 state 中写明具体 `unresolved_reason`；若已有 unresolved strict record 需要保留，其引用、disposition 和 reason 与 state 保持一致。

每完成一项工作就更新 state 的 `current_shell_id`、dispositions、`unprocessed_path_count` 和 `updated_at`，使 retry 可以从未闭合处继续。Trigger record、state disposition 和 Worklist 必须使用同一个 `path_id` 与 D2 引用。

## 10. Stage Completion

一个 Shell 在全部 Gaps 已进入 Worklist、全部 Paths 已形成 Stage-A disposition 后完成。所有成功 Shell 完成后：

```text
stage_status = COMPLETED
current_shell_id = null
unprocessed_path_count = 0
completed_shell_ids = 全部成功 D2 Shell
```

同时确认：

- Worklist 覆盖全部成功 D2 Gaps，且全部 status 仍为 `PENDING`；
- 每个 Path 有且只有一个 disposition；
- 每个 `TRIGGER_READY` Path 有一条匹配的严格 record；
- record、state 与 Worklist 的 D2 refs 完全一致；
- 冻结输入未被修改。

最后按当前 node output schema 返回单个 `TriggerCalibrationRunResult`，其中计数与 workspace 实际状态一致。完整研究结果已经保存在过程文件中，不在最终回复重复输出。
