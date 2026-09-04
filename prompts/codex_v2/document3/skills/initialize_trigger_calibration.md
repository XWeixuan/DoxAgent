# O3 Trigger Calibration

你的任务不是把 D2 中每个 Gap 转写成一条 Trigger，而是重新研究这个 Gap 所代表的 expectation uncertainty 在当前现实和当前市场预期下有哪些可交易的未来落点。对每条 Path，先确认现实现在在哪里，再研究市场目前已经预计到哪里，然后寻找刚刚越过这一预期、能够由现实消息直接确认、并在后续经营结果仍未知时已经足以产生独立方向性 revision 的最低业务边界。D2 的 `possible_occurrence`、`expected_revision` 和 `recognition_criteria` 都是研究材料，不是字段映射来源。

```text
理解正在变化的预期
→ 开放展开未来现实可能性
→ 隔离判断每个候选事件
→ 校准现实边界
→ 保留所有合格的独立 Triggers
```

目标是建立具有合理覆盖度的 Candidate Trigger Surface。开放召回先扩大可能性覆盖，严格筛选再决定哪些事件达到独立充分性；两者是连续但不同的工作。每条 `TRIGGER_READY` record 应能独立进入 Policy Compile，不依赖其他 records 才获得方向意义。`TRIGGER_UNRESOLVED` 是有效 disposition，研究缺口真实存在时不为表面覆盖强造 Trigger。

后续 Policy Compile 负责 Candidate 关系分类、OR grouping、Policy-level fields 与 canonicalization；本阶段只建立和校准 Paths 与 Candidate records。

## 1. Working Contract

按当前 node prompt 读取：

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

- `document2.json`：expectation、State、Realization Factors、Potential Gaps、expected revision、recognition surface 与 provenance 的研究基线；
- `reference_event_view.md`：冻结的近期现实补充，不是完整世界状态；
- `previous_policy_set.json`：已有 actor、现实边界和语义连续性的参考，不决定本轮 Future Surface；
- `worklist.jsonl`：所有待研究 Tradable Paths 的持久清单；
- `trigger_calibrations.jsonl`：ready Paths 的严格研究记录；
- `trigger_calibration_state.json`：Shell 进度与全部 Path dispositions。

本阶段可以新增或补充 Worklist Paths、写入或修正 Trigger records 与 dispositions，并更新 current/completed Shell 状态。Worklist `status` 保持 `PENDING`，`policy_ids` 保持空列表；最终 `COMPILED` / `UNRESOLVED`、Policy drafts、`calibration_log.jsonl` 与 `wave_state.json` 由 Policy Compile 处理。

每条 Path 的业务语义应来自对该对象逐项完成的研究与判断。程序适合读取、检索、计数、排序、ID/引用检查、schema validation、格式转换，以及在内容已经逐项形成后的机械写入；循环、模板、字段映射或字符串拼接不代替 Worklist、Trigger record 或自然语言字段的语义形成。D2 原文可以在重新判断后保留，但 `candidate_trigger = possible_occurrence`、`current_state = Unit State`、`judgeability = recognition_criteria` 之类的默认映射不构成研究结论。

## 2. Recovery 与 Frozen Context

先读取已有 Worklist、Trigger records 和 Trigger state，从 `current_shell_id` 或首个未完成 Shell 恢复。已闭合 Shell 保持不变；已有 Path 以 `path_id` 和精确 D2 refs 恢复，新发现的重要 Path 可以追加而不覆盖无关条目。每次修改同步更新 Trigger state，使 retry 能从未闭合处继续。

同一次 INITIALIZE run 中，D2、Reference View、Previous Policy Set、task 与 cutoff 保持冻结。使用输入实际提供的 snapshot、published 或 `as_of` 理解时间边界；未提供的时间保持未知。本 Turn 的外部研究遵守 `task.json.cutoff_at`。

最新 D2 是 expectation research baseline；Reference View 可以把 D2 future fact 推进为 current fact，但其空白不证明现实尚未发生。D2 定义所研究的 expectation 与 revision space，Reference View 和必要的当前研究确定现实已经推进到哪里；市场预期研究则确定其中哪些下一步已被 baseline 吸收。缺少其中任一项会改变 Candidate 选择、独立充分性或 boundary 时，进行定向研究。

## 3. Understand the Shell

进入一个 Shell 后，联合读取：

```text
core_question and boundary_rule
all Units and propositions
State and Realization Factors
Potential Gaps
possible_occurrence
expected_revision
recognition_criteria
```

在展开 Paths 前形成三项认知：

1. 当前已经进入 baseline 的核心状态；
2. 这个 Shell 仍在交易哪些 expectation uncertainties；
3. 每项 uncertainty 通过什么经济机制影响目标 ticker。

D2 字段按研究角色使用：

- `State` 提供当前 expectation 与已知现实，用来判断未来事件是否仍有新增量；
- `Realization Factors` 提供实现所依赖的经济机制，用来理解共同 transmission 并发现未被逐项列出的现实表现；
- `possible_occurrence` 表达 D2 已识别的 Gap-level realization idea。进入 O3 后，重新判断其中是否混合多个 actors、阶段、完整确认链或 aggregate reality，并寻找是否存在更早、更小但已经独立 material 的 message-level occurrence；原表述经过这轮 Operationalization 后才可能成为 Candidate Trigger。
- `expected_revision` 约束所研究的 expectation component、经济 transmission 和方向；D2 为更新完整 State 使用的确认门槛不自动成为 D3 Trigger threshold，O3 可以选择在 full Gap confirmation 之前已经产生独立 marginal revision 的事实。
- `recognition_criteria` 描述 D2 为较完整识别 Gap 考虑的观察节点与 evidence surface。逐项判断其信息作用：它可能是独立 Trigger，也可能只是 corroboration、realization 或后续确认，不直接提供 D3 activation threshold。

后续每个 Candidate Trigger 都应改变这里识别出的某项 expectation uncertainty。仅与行业主题相关、却无法回到 D2 transmission 和 ticker impact 的消息不进入 Worklist。

## 4. Expand the Future Trigger Surface

对每个 D2 source object 先形成开放但有边界的候选现实事件集合，再把经济含义不同的候选展开为 Tradable Paths。可以从以下角度推演，不要求机械逐项填写：

- **Actor realization**：哪些重要客户、供应商、竞争者、平台、监管机构或渠道可以独立承载变化；actor materiality 是否不同，或能否用具有一致经济边界的主体类别表达。
- **Business manifestation**：合同、产品规格、采用、采购、部署、供给、产能、价格、库存、质量或监管状态可能怎样变化。
- **Expectation dimension**：哪些事件改变概率、实现时间、规模、经济性或风险约束，哪些使意向、试验或可逆状态进入更确定阶段。
- **Directional asymmetry**：公司自身与竞争者、不同技术路径、地区、产品或客户范围是否形成不同 ticker transmission 和方向。
- **Recognition nodes**：qualification、shipment、revenue、margin、inventory、price 等 D2 节点中，哪些可能单独产生显著预期差，哪些只是完整确认或后果验证。
- **Mechanism-consistent additions**：D2 是否未逐项列出某个重要 actor、现实落点或尚未完整兑现但已显著推进 expectation 的状态。

预计披露阶段只用于帮助召回候选和区分 occurrence/Evidence。现实消息可以跳跃、倒序或同时出现，因此保留所有独立充分事件，不以“哪条消息最先出现”为统一排序，也不把阶段序列写入 Candidate Trigger。

### Path 粒度

一条 Worklist Path 表示一种方向明确、现实含义单一、能够形成一条独立 Candidate Trigger record 的 realization path。以下差异通常形成不同 Paths：

- 重要 actors 能够独立变化，并各自足以改变 expectation；
- 同一 Gap 存在不同 occurrences；
- 相同主题通过不同 transmission 影响 ticker；
- 方向不同；
- recognition nodes 各自可能独立充分；
- 一个事件改变概率，另一个形成新的规模、时点或经济性 update。

同一 occurrence 的不同披露来源、同一合同变化中的 actor/product/period/quantity 属性、同一状态的 Evidence variants、纯 corroboration，以及无法独立影响 ticker 的低重要性 actor，不拆为 Paths。

使用现有 Worklist 字段。`path_summary` 紧凑表达具体 actor/object、现实变化、被改变的 expectation dimension 和 `LONG` / `SHORT` 方向。

`d2_boundary_sufficient=true` 是例外状态：只有冻结输入已经直接给出 policy-specific current reality、当前市场 expectation baseline、最低 tradable deviation、material actor/scope 和现实消息确认路径，且不需要外部研究即可选择 Candidate 时才成立。D2 内容丰富、`recognition_criteria` 详细或 `possible_occurrence` 完整，都不等于 boundary sufficient；该 Path 仍需经过本阶段逐项判断并写入严格 record。缺少任何会改变 Candidate 或 boundary 的信息时设为 `false`，并把 `missing_calibration` 写成一个具体、可回答的问题。

### Coverage Saturation

一个 Gap 在以下状态下可以结束开放展开：

- D2 直接提出的主要 occurrences 已被覆盖；
- 重要 actor asymmetry 与相反方向已被考虑；
- recognition criteria 中可能独立充分的节点已被考虑；
- 与既有机制一致的其他重要现实表现已被考虑；
- 继续新增的候选主要落入 Evidence variants、低 materiality、重复 occurrence 或已覆盖状态。

Coverage Saturation 追求重要 Future Surface 的充分覆盖，不要求枚举所有公司、产品和可能新闻。完整 Worklist surface 落盘后，再开始本 Shell 的定向研究。

## 5. Isolate and Evaluate Candidate Events

对每条 Path 依次回答五个问题：

1. **Reality**：承担变化的 actor/object 当前真实状态是什么，哪些进展已经发生？
2. **Market Expectation**：在 cutoff 时点，市场普遍预计该对象下一步发生什么、何时发生、达到多大范围？
3. **Tradable Surprise**：相对上述两层 baseline，哪项最小未来事实已经足以形成显著、定向的 expectation revision？
4. **Message Reality**：谁掌握并通常发布该事实，什么正常消息能够完整确认这一 occurrence？
5. **Residual Uncertainty**：若更晚的 shipment、revenue、margin、share 或其他兑现仍未知，这项事实是否仍独立充分？

五项均有可辩护答案时才形成 `TRIGGER_READY`；其中任一缺口会改变事件选择、充分性或可判定边界时，先完成对应 Calibration。

对每个 Path 隔离设定：

```text
Current reality + current market expectation
+ only this future fact
+ all other candidate facts unknown
```

然后只问：

> 这项事实本身是否已经足以使相关 expectation 发生显著、方向明确并支持当前 Path `LONG` / `SHORT` 方向的变化？

判断集中在四个维度：

- **Incremental**：相对 current state 是新事实，尚未被 D2 或 Reference View 吸收，也不是当前 expectation 的普通延续；
- **Material**：actor、规模、承诺强度、商业阶段或时间变化足以影响 ticker，而非孤立测试或低影响进展；
- **Directional**：ticker transmission 清晰，方向不依赖另一个未知事实才能确定；
- **Observable**：能够形成消息、文件或数据事实，W2 可依据单条新消息判断，无需跨消息累积隐含前提。

Candidate 可以完成整个 expectation revision，也可以只形成一次独立且具有交易意义的 significant expectation progress，例如显著改变概率、timing、规模、商业状态、合同/监管约束或风险现实性。

候选内容按其作用处理：

| 候选性质 | 本阶段处理 |
| --- | --- |
| 单独达到 Direct Trading Sufficiency | 进入 Calibration |
| 多项属性共同构成一个自然完整 occurrence，整体单独充分 | 收敛为一个复合 Candidate Trigger |
| 只用于证明 occurrence | 进入 `disclosure_route` / `judgeability` |
| 只增强可信度或确认后果 | 作为 supporting information，不生成 ready Trigger |
| 已进入 baseline | 写入 `current_state` |
| 代表另一项独立 expectation update | 建立新的 Path |
| 缺少会改变选择或边界的事实 | 写明确 `missing_calibration` 并定向研究 |
| 研究后仍无法形成可观察且独立充分的状态 | `TRIGGER_UNRESOLVED` |

若 A、B 单独均不足，只有当它们共同描述同一次自然 occurrence 或一个直接可判断的业务状态，actor/object/期间相容，一条现实消息可以合理确认整体，并且整体独立充分时，才形成一个复合 Candidate。跨期 qualification、shipment、revenue、margin 等 realization chain 不因写在一起而成为一个 occurrence；当前单消息 Runtime 无法表达的跨消息累计条件，需要调整粒度或保留 unresolved。

### Stop at the first tradable revision

当 Candidate 同时包含 adoption/qualification、shipment、revenue、margin、share、inventory 等节点时，先区分它们是在共同定义一个现实事件，还是在描述“事件发生 → 经济传导 → 后续兑现”。若前面的业务状态在后续结果未知时已经使当前市场 expectation 产生足够大的方向性偏移，Trigger 停在该状态；后续节点作为 transmission、realization 或 supporting information。

### Real Message Check

对 Candidate 逐项确认：谁拥有该事实，什么正常消息会披露它，该消息是否自然包含 Candidate 要求的全部事实。“一篇综合报道理论上可以汇总多个来源”不等于存在这样的正常单消息 occurrence。若 Candidate 依赖不同事实所有者、发布时间或商业阶段的独立确认，重新拆分、前移到独立充分的边界，或保留 unresolved。

## 6. Calibrate Reality、Expectation 与 Trigger Boundary

Calibration 围绕四个相邻但不同的问题：

```text
现实现在在哪里？
市场已经预计到哪里？
什么额外变化刚好足够？
怎样认定它发生？
```

### Current state

确认 trigger-bearing actor/object 当前状态、已经发生或进入 baseline 的事实，以及直接相关的商业、合同、资格、生产、监管或数据水平。Current state 只记录建立 Trigger boundary 所需的比较起点，不重写整个 D2。

### Market expectation baseline

研究市场对同一 actor/object 下一步状态、时点、规模或商业阶段的当前基准判断。优先使用管理层指引与明确承诺、卖方 consensus 或观点分布、行业预测、已披露 roadmap、合同与监管预期等能直接表达 expectation 的材料；价格表现通常不能单独说明市场具体预计了什么。目标是识别已经被普遍预期的普通进展，而不是重建完整市场研究。

### Trigger boundary

校准真正决定充分性的业务属性：actor importance、commitment strength、commercial stage、magnitude、product/customer/region scope、applicable period、timing shift、production/legal status 和必要 comparator。

### Economic minimality

寻找最低但仍保持独立交易充分性的 actor、阶段、规模、承诺强度和范围。依次思考：actor 重要性、承诺强度、幅度、商业阶段或 scope 再降低一级，是否仍足以形成同方向 update；time horizon 改变后，传导与方向是否仍稳定。当继续放宽会使事件退化为普通进展时，当前边界就是 Candidate 的 minimality。

对于“主要客户”“多个 OEM”“重大客户”“主要供应商”“多个平台”等 aggregate actor，判断最大单一 actor 是否已充分、普通单一 actor 是否仍充分；确实需要 aggregate 时，校准最低市场份额、客户类别、产品覆盖或独立主体数量。能用具体 material actor/state 表达时优先具体化，aggregate 表述则应解释其经济 materiality。

Minimality 校准的是业务阈值，不把自然事件中的 actor、object、period、magnitude 拆成逻辑原子。按以下顺序建立可判定边界：先使用有可靠依据的 quantitative threshold；缺少可靠数值时使用 `non-binding → binding`、`sample → qualification`、`qualification → production` 等 categorical business boundary；两者都无法建立且程度会改变 sufficiency 时保留 unresolved。“显著”“主要”“重大”“大幅”等程度词本身不构成第三种校准方法。

## 7. Conduct Targeted Research

研究从已经明确的 Candidate 或 `missing_calibration` 出发：

```text
D2
→ Reference View
→ targeted Web Search
→ necessary read-only Data MCP
```

外部研究只解决会改变 Candidate 选择、独立充分性或可判定边界的问题，例如 current reality、当前市场 expectation、actor materiality、合同或监管状态、可比区间、trigger magnitude、disclosure route、Runtime comparator，或 Candidate 是否仍有独立 expectation delta。

- Web Search 适合公司、客户、产品、合同、监管、生产、采用阶段、公开 deployment、actor 当前相关性与现实披露模式；来源选择依据事实所有权和可靠性，不使用统一 official-only 门槛。
- Data MCP 仅用于 Candidate 真正依赖的历史序列、current comparable range、provider-specific consensus，或 D2 未提供且可能已经更新的结构化指标。数据类型适合 Data MCP 并不自动构成查询理由；没有该数据会阻止 comparator 或边界成立时才调用。

当已经能够说明以下完整组合时结束研究；这是判断清单，不是新增输出字段：

```text
当前现实状态
+ 当前市场预期基线
+ 最小未来偏离
+ 独立交易充分性与方向
+ 现实消息路径
+ Runtime 可判定边界
```

研究目标是解决既定 Trigger 问题，而不是穷尽主题或重做 D2。一般的信息不完整不等于 unresolved；只有缺失事实阻止形成独立充分或 Runtime 可判定边界时，才收敛为 `TRIGGER_UNRESOLVED`。

## 8. Write Trigger Calibration Records

每个 `TRIGGER_READY` Path 写一条且仅一条 supplied schema record：

- `trigger_bearing_actor`：承担现实变化、materiality 明确的主体或主体类别；粒度符合事实所有权和 ticker impact，不因追求具体而虚构名称。
- `trigger_bearing_object`：真正变化的合同、产品、平台规格、客户关系、工厂、产能、采购、价格、库存、监管范围或可比指标。
- `current_state`：最新确认现实及已经吸收进 baseline 的相关状态，只回答当前比较起点。
- `candidate_trigger`：仍面向未来、自然完整且独立充分的现实状态，包含必要 actor/object、状态变化、scope 与最低业务边界；不写 Evidence chain、预计消息顺序或后续 earnings realization。
- `trade_sufficiency`：说明在其他候选未知时，该事实相对当前市场预期改变哪个 expectation dimension、变化为何显著、怎样传导到 ticker，以及为何剩余不确定性不妨碍当前方向。
- `minimality`：说明当前 actor、承诺强度、阶段、规模和范围为何是最低仍充分的业务边界，可以指出被排除的更弱状态，但不记录逐词 deletion 过程。
- `disclosure_route`：掌握事实的自然发布者、可能消息类型、可接受的可靠确认渠道，以及同一 occurrence 的替代 Evidence routes；不同 routes 不生成不同 records。
- `judgeability`：未来消息必须包含哪些事实与 comparator，才能让 W2 判断 Candidate 成立或不成立；不重复完整 `trade_sufficiency`。
- `source_basis`：只记录实际支持结论的 D2、Reference View、Web 或 Data MCP 来源标识。
- `disposition`：使用 supplied `TRIGGER_READY` 或 `TRIGGER_UNRESOLVED`。

`TRIGGER_UNRESOLVED` 的原因应指出具体缺失的是 actor materiality、current state、独立方向意义、现实可观察性、单消息表达能力或最低边界，而不是笼统的信息不足。Ready Path 必须有匹配的严格 record；每个 Path 同时在 `trigger_calibration_state.path_dispositions` 保留精确 `shell_id + expectation_id + gap_id + path_id`、disposition 和适用 reason。

Trigger record、state disposition 与 Worklist 使用相同 `path_id` 和 D2 refs。每完成一项工作，就更新 `current_shell_id`、dispositions、`unprocessed_path_count` 与 `updated_at`。

## 9. Close the Shell and Complete the Stage

一个 Shell 只有在以下工作闭合后完成：

- 所有 D2 source objects 已完成 Future Surface expansion；
- 重要 actors、方向、transmissions 与现实落点均已考虑；
- 全部 Paths 已写入 Worklist 并形成唯一 Stage-A disposition；
- 每个 ready Path 有且仅有一条独立完整的 Trigger record；
- supporting information 没有成为 ready Trigger；
- Evidence variants 没有制造重复 Paths；
- unresolved reason 指向具体边界；
- Worklist `status=PENDING` 且 `policy_ids=[]`，等待 Compile 接管。

全部成功 Shell 完成后：

```text
stage_status = COMPLETED
current_shell_id = null
unprocessed_path_count = 0
completed_shell_ids = all completed D2 Shells
```

最后确认 Worklist 覆盖完整 source surface，每个 Path 只有一个 disposition，record/state/Worklist refs 一致，冻结输入未被修改。按 supplied node schema 返回单个 `TriggerCalibrationRunResult`，计数与 workspace 实际状态一致；完整研究结论留在过程工件中，不在最终回复重复。
