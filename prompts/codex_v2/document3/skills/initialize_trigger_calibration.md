# O3 Trigger Calibration

本 Turn 完成 O3 INITIALIZE 的研究阶段：从完整 D2 Potential Gap surface 中，研究出现实世界下一项能够制造边际 expectation update、具有正常消息生成路径且可由 W2 判断的 Candidate Trading Triggers。所有成功 Shell 完成本阶段后，编排才进入 Policy Compile。

本阶段优化的是 Trigger 质量，而不是 `TRIGGER_READY` 数量或确认程度。可靠性主要来自正确的现实状态、expectation transmission、可观察比较基准和消息确认方式，不来自继续等待更多后续经营结果。优先顺序是：

```text
忠实于 D2 revision space 与 ticker transmission
→ Trigger 存在于真实消息流
→ 在可观察候选中选择最早的交易充分边界
→ 提高 W2 judgeability
→ 最后优化表达与数量
```

正常的后续不确定性可以保留；只有在完成针对性研究后仍无法建立上述边界时，才使用 `TRIGGER_UNRESOLVED`。

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
- Previous Policy Set 提供已有主体、边界和语义连续性的参考，本轮完整 Gap surface 仍由当前 D2 决定；
- Worklist 是 Stage A 建立的完整 Tradable Path surface；
- Trigger records 保存每条 ready Path 的研究结论；Trigger state 保存 Shell 进度和全部 Path dispositions。

本阶段更新 `output/work/worklist.jsonl`、`trigger_calibrations.jsonl` 和 `trigger_calibration_state.json`。Worklist 的 `status` 保持 `PENDING`，`policy_ids` 保持空列表；最终 `COMPILED / UNRESOLVED`、Policy drafts、`calibration_log.jsonl` 和 `wave_state.json` 由 Policy Compile 处理。只使用 supplied schema 中已有字段。

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

回答：这个 Shell 描述怎样的一组 expectation uncertainties，各 Gap 从什么现实状态产生，哪些主体、约束和传导彼此关联？保持完整 Shell 上下文，用于复用 current-state 与消息生成研究，并区分同一主题下真正不同的状态路径。

进入新 Shell 时将其写入 `current_shell_id`。本 Shell 全部 Paths 形成 disposition 后，再加入 `completed_shell_ids` 并进入下一 Shell。

## 4. Worklist Gate：先解析 Actor-State，再确定 Path Surface

对本 Shell 的全部 Potential Gaps 先做 Actor-State Resolution，再决定 Path 拆分。不要从 D2 列出的实体名称或“多个客户”“主要 OEM”等集合表述直接枚举 Paths。

对可能承载 Gap 的主体或对象，在内部形成以下认知表；这是分析方法，不是新增输出字段：

```text
Actor / Object
Current State
Already in Reality or Expectation Baseline
Next Plausible State
Expectation Effect on the ticker
Normal Message Route
```

先利用 D2、Reference View 和 Previous Policy Set 解析已知状态。若主体当前状态会改变 Path 设计但输入不足，先把问题压缩成 Trigger-selection question，并做必要的定向研究，再完成拆分。问题应能改变 `actor / current_state / candidate_trigger / comparator / disclosure_route` 中至少一项，例如：

> 当前相关客户分别处于 qualification、production adoption 还是 recurring shipment；哪一主体的下一项变化会成为相对于现有 baseline 的第一项新 expectation delta？

拆分依据是现实状态和交易语义，而不是主体数量：

- 当前状态、下一边界、披露结构或 ticker 方向不同，形成不同 Paths；
- 同一 Gap 的正反落点或彼此独立的 `A OR B` 事件形成不同 Paths；
- 多个主体当前状态、下一状态、消息结构和 ticker transmission 实质对称，而且未来每条消息只判断其中一个主体时，可以保留通用的单主体事件 Path；
- “一名可识别的客户/供应商”本身不是 Actor-State Resolution 的结果。若边界依赖具体主体的当前状态，应研究到该现实主体；若不依赖，应明确通用事件成立的对称性依据。

每条 Path 保持单一现实含义和方向，并表达：

```text
现实主体或对象
→ 当前状态
→ 下一项可能状态
→ D2 expected_revision
→ 目标 ticker 的净影响
→ LONG / SHORT
```

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

`path_summary` 使用真实业务语言紧凑表达主体/对象、状态变化和 ticker 方向。`d2_boundary_sufficient=false` 时，`missing_calibration` 写成能够决定 Trigger 选择的具体问题，而不是宽泛研究主题。完整 Path surface 落盘后再进行本 Shell 其余 Trigger Research；后续研究若证明 actor-state 假设有误，应在 disposition 前修正 Worklist。

## 5. `d2_boundary_sufficient`

D2 只有已经直接回答以下问题、无需外部研究即可完成 Trigger Selection 时才为 `true`：

```text
谁承载变化，当前处于什么状态？
哪项仍面向未来的新事实会更新 expectation？
该事实怎样经 D2 transmission 形成明确 ticker 方向？
哪些事实真正必要，哪些只是后续兑现？
现实信息生产机制通常怎样让它成为消息？
W2 从哪里获得必要 comparator？
```

D2 `recognition_criteria` 是寻找边界的研究线索，不是默认 Candidate Trigger。它可能为了完整确认 Gap 同时列出资格、量产、shipment、收入或利润；完整确认 Gap 与第一次形成可交易预期差是不同问题。

`d2_boundary_sufficient=true` 只表示上述关键不确定性已经由冻结输入解决，不表示可以跳过本阶段分析。该 Path 仍需完成下面的 Trigger Selection，并写入严格 record。

## 6. Trigger Selection Analysis

### 6.1 Current State 与 Marginal Expectation Update

D2 定义 expectation prior、revision space 与经济传导；Reference View 和必要的当前研究确定现实已经推进到哪里。Reference View 已确认的 D2 Future Gap 应吸收到 `current_state`，再沿同一 Path 寻找下一项仍面向未来的边界。Reference View 未记录某项变化，不证明它尚未发生。

对每个 candidate fact / candidate fact set 按以下链条判断：

```text
D2 expectation prior
+ 最新确认的 current state
+ candidate fact / fact set
→ probability / timing / scale / economic path / risk 的更新
→ D2 expected_revision
→ ticker 的 LONG / SHORT transmission
```

候选事实或事实集合整体成立后，只要某个重要 expectation lever 已产生明确、具有交易意义的方向更新，而且 ticker transmission 不需要再假设集合之外另一项尚未出现的关键事实，该集合就可能构成充分 Trigger。shipment、revenue、margin、份额或最终结果仍未知，不会自动使更早事实不充分。若候选仍只是普通进展，或方向必须依赖集合之外另一关键未知事实，继续寻找真正承担边界的状态变化。

### 6.2 Causal Layer Analysis

在判断最小性前，把候选中的每项事实分到事件链的相应层级：

```text
Precursor
→ Trigger Candidate
→ Transmission Evidence
→ Realization Evidence
```

- Precursor 只提高关注度，尚未跨过交易边界；
- Trigger Candidate 是第一次产生上述 expectation update 的状态变化；
- Transmission Evidence 解释变化为何影响 ticker；
- Realization Evidence 说明 Trigger 后来转化为 shipment、revenue、margin、份额或其他经营结果。

Condition 通常应停在 Trigger Candidate。后两层用于证明研究逻辑，不因提高确定性就自动成为 Candidate Trigger 的组成部分。若某项 shipment 或商业结果实际上定义事件本身，而非仅证明后续兑现，应结合该 Path 的业务机制说明其不可替代作用。

### 6.3 Minimal Sufficient Fact Set

Candidate Trigger 可以包含一个或多个事实，但只保留完成本次 expectation update 所需的最小事实集合。逐项判断：

> 删除事实 C 后，D2 中目标 expectation lever 的更新是否仍然成立且方向不变？

若仍成立，C 没有必要的信息贡献，应从 Trigger 中删除，即使它能增加信心。若删除后必须再假设一项关键事实才能建立 transmission，C 才可能必要。

这不是“删掉后我是否仍足够确信最终 thesis”的测试。资格、binding commitment、production adoption 或正式规则变化已经产生充分 expectation delta 时，后续重复 shipment、收入增长、利润改善或市场反应属于 realization。对每个最终保留事实说明其独立信息贡献，并说明排除了哪些前兆、传导说明或跟随结果。

### 6.4 Message Production Analysis

对最小事实集合建立现实信息生产链：

```text
event owner
→ information holder
→ likely publisher / reporter
→ normal message type
→ normal content boundary
```

核心问题不是“能否想象一篇综合文章把这些事实写在一起”，而是：现实中哪个信息持有者会在该时点掌握必要事实，谁有义务、动机或习惯披露，正常消息通常能披露到什么粒度？

分别检查候选事实是否属于同一信息生产链。平台 BOM、供应商 allocation、客户订单和目标公司的财务结果即使经济上相关，也可能由不同主体、不同载体和不同时点产生；综合报道能够事后汇总，不等于一条未来消息会自然生成全部必要事实。

当披露方式并不明显时，定向查看同一主体、同类事件或同行历史上的 Disclosure Analogy：谁最先公开、通过何种载体、通常披露哪些内容、哪些事实需要另一主体或更晚消息确认。研究结论写入 `disclosure_route`，包括具体信息持有者/发布者、消息类型、正常内容边界，以及 Candidate 必要事实为何属于该边界；泛写 `company filing`、`credible report` 或“同一自然披露”不足以表达研究结果。

一条 Candidate Trading Trigger record 表达一个 message-level trading event，不预设 Policy Compile 最终产生一个还是多个 Conditions。同一消息中若存在多个各自可判断、且都不可缺少的事实，Stage B 可以拆成多个 Conditions；彼此独立发生或来自不同消息生产链的 `A OR B` 应在 Stage A 调整 Trigger granularity 或拆为不同 Paths。

### 6.5 Observable Comparator 与 W2 Judgeability

假设 W2 只有未来消息和 Runtime Policy，问：它从哪里取得判断所需的比较对象？可用 comparator 至少属于以下一类：

- Candidate/后续 Criterion 中明确给出的当前值或状态；
- 公开且确定的 current commitment、guidance 或 timeline；
- 未来消息自身明确给出的 before/after；
- Runtime 能直接获得的明确历史状态。

“明显、重大、大幅、广泛、持续、高位、健康、实质”等程度词应优先翻译成可观察业务状态。若必须保留相对判断，明确 comparison baseline 及其来源。需要 W2 自行估算“无保护基线”“无上限情景”“正常库存”或其他反事实模型的比较对象，不是可观察 comparator，应重新校准边界。数值阈值来自研究和经济含义，而不是为了形式精确而任意设置。

## 7. Targeted Research

研究始终服务于 Trigger Selection，按问题选择：

```text
D2
→ Reference View
→ targeted Web Search
→ 必要时只读 Data MCP
```

- 公开事实、主体当前状态、商业阶段、客户采用、合同、监管、生产状态和历史 disclosure pattern 通常适合定向 Web Search；一手资料能回答时即可形成结论，一手不存在或不披露该类事实时使用可靠行业或二手来源。
- 历史序列、可比区间、provider-specific consensus 或结构化市场指标适合 Data MCP；只有 D2 未提供当前 Trigger 所需的可比信息，或该数据在 D2 后确有现实可能更新时才重新查询。
- 同一 actor-level finding 可以支持多个 Paths，但每条 Path 保留自己的 Trigger 结论和来源映射。

Research 在以下关键问题已有明确答案时结束：

```text
谁或什么对象承载 Trigger？
现实现在在哪里？
哪项下一事实第一次更新重要 expectation lever？
哪些事实对该更新真正必要？
这项变化在现实中怎样成为消息？
W2 依据什么可观察状态和 comparator 判断？
```

目标是解决这些不确定性，不是写完整行业报告或搜集更完美的证明。经过针对性研究仍无法找到现实可披露、仍面向未来且能够建立明确 expectation transmission 的 Trigger 时，使用 `TRIGGER_UNRESOLVED`；一般的信息不完整本身不等于 unresolved。

## 8. Trigger Calibration Record

每个 `TRIGGER_READY` Path 写一条且仅一条符合 supplied schema 的 record。字段承担以下职责：

- `trigger_bearing_actor`：Actor-State Resolution 确定的现实主体，或经研究证明 current state 与下一边界对称的通用单一事件主体；
- `trigger_bearing_object`：产品、合同、客户关系、工厂、产能、采购、规则或指标等具体对象；
- `current_state`：最新确认现实、已进入 expectation baseline 的状态及必要可观察锚点；
- `candidate_trigger`：使用真实领域语言表达仍面向未来的 Minimal Sufficient Fact Set，不写内部测试口号；
- `trade_sufficiency`：哪一个 expectation lever 怎样更新，以及该更新如何经 D2 transmission 支持 Path 方向；
- `minimality`：保留事实的必要信息贡献，以及已排除的 precursor、transmission 或 realization facts；
- `disclosure_route`：信息持有者/发布者、正常消息载体、内容边界及必要事实的同消息可行性；
- `judgeability`：W2 判断的真值边界、可观察 comparator 及其可获得位置；
- `source_basis`：实际支持 actor state、Trigger、披露路径和 comparator 的 D2、Reference View、Web 或 Data MCP 来源标识；
- `disposition`：`TRIGGER_READY`。

`TRIGGER_READY` 表示研究已经具体解决 Actor-State、Marginal Expectation Update、Causal Layer、Minimal Fact Set、Message Production 和 Observable Comparator，而不是每个字段都有一句自洽文字。以下均不足以达到 ready：需要具体主体时只写“一名可识别的……”；披露结构未知时只写“可能由综合报道披露”；comparator 不可见时只给反事实基线名称。

每个 Path 同时在 `trigger_calibration_state.path_dispositions` 保留精确的 `shell_id + expectation_id + gap_id + path_id` 与 disposition。`TRIGGER_UNRESOLVED` 在 state 中写明具体 `unresolved_reason`；若已有 unresolved strict record 需要保留，其引用、disposition 和 reason 与 state 保持一致。

每完成一项工作就更新 state 的 `current_shell_id`、dispositions、`unprocessed_path_count` 和 `updated_at`，使 retry 可以从未闭合处继续。Trigger record、state disposition 和 Worklist 使用同一个 `path_id` 与 D2 引用。

## 9. Stage Completion

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
- 每条 ready record 实际解决了 Actor-State、expectation update、causal layer、minimality、message production 和 observable comparator；
- record、state 与 Worklist 的 D2 refs 完全一致；
- 冻结输入未被修改。

最后按当前 node output schema 返回单个 `TriggerCalibrationRunResult`，其中计数与 workspace 实际状态一致。完整研究结果保存在过程文件中，不在最终回复重复输出。
