# O3_INITIALIZE

O3 的目标是尽可能完整地把 D2 Potential Gap 空间编译成仍面向未来的 Direct Trading Policies。高质量 Policy 捕捉尚未进入当前 expectation baseline、但一旦发生已足以产生边际修订的最早可靠现实变化，并具有明确方向、现实边界和可由未来消息低自由度识别的确认标准。

在一个持续线程中，以一个 Shell 为一个完整 wave，处理最新 Published D2 中全部成功 Shell，并把每个 Potential Gap 的未来修订空间编译为 Policy drafts。`PARTIAL` D2 仍是有效基线；`task.json` 中的 failed shells 构成已知输入边界，由后续 Coverage Map 保留，不为其补造 Gap。

过程控制面是 `worklist.jsonl`、`calibration_log.jsonl`、`wave_state.json` 和 `policies/`。它们用于保持长任务覆盖、研究结论和恢复位置；最终 Policy Set、Coverage Map、稳定 ID 与 Runtime Projection 由确定性阶段生成。

## 1. 恢复工作状态

先读取 `task.json`、完整 D2、可选 Reference View、Previous Policy Set，再读取已有 worklist、calibration log、wave state 和全部 Policy drafts。把 workspace 视为本次 INITIALIZE 及 retry 之间的持久工作记忆：

- `completed_shell_ids` 表示已经闭环的 waves；
- `current_shell_id` 表示需要继续的未完成 Shell；
- 已有 Worklist、Calibration 和 Policy 内容代表已经完成的工作，按 `path_id` 与精确 Gap 引用继续更新；
- 后续 Shell 如与既有 Policy 指向同一现实状态，可以补充该 draft 及相关 Path 映射，而无需重跑已完成 wave。

进入新 Shell 时先将其写入 `current_shell_id`。恢复未完成 Shell 时，先重建已有 Path 与 Policy registry，再补齐缺失工作，避免重复建立 Path 或 Policy。

## 2. 将一个 Shell 作为完整认知 Wave

先理解 Shell 的 `core_question` 和 `boundary_rule`，再联合阅读其全部 Units。对每个 Unit，结合 `proposition`、`horizon`、State、Realization Factors 和全部 Potential Gaps，回答：

> 这个 Shell 由哪些相互关联的 expectation uncertainties 构成，什么现实变化会使其中一个或多个 Units 发生新的修订？

保持整个 Shell 的上下文，识别不同 Gap 是否共享现实路径、相反方向、同一 Calibration baseline 或同一 Policy。Unit 提供具体 expectation 接口；Shell 提供合并、拆分和保持边界一致所需的全局视角。

## 3. Worklist Gate：先展开完整 Tradable Path Surface

在本 Shell 的任何外部研究或 Policy drafting 前，先处理全部 Potential Gaps。对每个 Gap，依次辨认：

1. 哪个主体、对象或系统状态发生变化；
2. 它从当前什么状态转向什么新状态；
3. 该变化如何通过 `expected_revision` 改变目标 ticker 的经营、风险或估值预期；
4. 对应的预定方向是 `LONG` 还是 `SHORT`。

一个 Path 保持单一现实含义和交易方向。不同主体导致相反影响、同一 Gap 包含多个现实落点，或存在彼此独立的 `A OR B` 触发方式时，展开为不同 Paths。方向来自“现实变化 → expectation revision → 目标 ticker”的传导，而不是消息表面的利好或利空措辞。

判断 `d2_boundary_sufficient` 时，结合当前可用现实证据检查 D2 是否已经给出：

- 可作为比较起点的 reference state；
- 相对最新确认现实和 expectation baseline 仍面向未来、且足以迫使修订的边界；
- 能由未来消息识别的确认方式；
- 可解释的交易方向。

如果这些信息完整，Path 可以直接编译。否则把 `missing_calibration` 压缩成具体、可回答的问题。例如，将“研究竞争情况”改成“该竞品当前仍处客户验证，还是已进入重复商业采购”；将“研究新增产能”改成“已宣布产能中，多少已经转化为合格可销售产出，预计何时实际进入市场”。

使用现有字段将本 Shell 的全部 Paths 写入 `output/work/worklist.jsonl`：`shell_id`、`expectation_id`、`gap_id`、`path_id`、`direction`、`path_summary`、`d2_boundary_sufficient`、`missing_calibration`、`status`、`policy_ids`、`unresolved_reason`。新 Path 从 `PENDING` 开始；恢复时更新既有 entry。完整 Worklist surface 落盘后，进入 Calibration 与编译。

## 4. Calibration Gate：把缺口研究成执行边界

D2 是 expectation / research baseline；Reference View 是其后可用的现实状态补充。使用 Reference View 或其 pinned metadata 实际提供的 snapshot、published 或 `as_of` 时间；未提供的时间保持未知。若它已确认 D2 某项未来变化成为现实，以最新确认状态更新 `reference_state`，再寻找下一项仍面向未来的 marginal boundary。Reference View 未收录某项变化，不构成其尚未发生的证明。

围绕已经写明的 `missing_calibration` 选择最适合的问题形式和工具：

- 公开事实、商业阶段、客户采用、合同、监管或生产状态通常适合定向 Web Search；一手或官方资料足以回答时直接使用，否则采用可靠二手或行业来源；
- 历史序列、区间、provider-specific consensus 或结构化市场指标适合只读 Data MCP；只有 D2 未提供编译所需的可比信息，或该数据在 D2 后确有现实可能更新时才重新查询；
- 同一研究结论可以服务多个 Paths，研究深度以形成清晰 Activation Boundary 为终点。

Research 在能够形成可辩护的 `reference_state + trigger_boundary + qualifying_evidence + direction` 时结束；目标是解决既定 `missing_calibration`，不是穷尽主题或寻找更完美的证据。研究结果应回答“原来缺什么、现在确认到哪里、是否已经足以编译”，而不是形成工具轨迹或新的行业报告。发生 Calibration 或现实状态补充时，使用既有五字段追加 `calibration_log.jsonl`：`path_id`、`calibration_need`、`source_kind`、`finding`、`resolved`；`source_kind` 使用 `D2`、`REFERENCE_VIEW`、`WEB` 或 `DATA_MCP`。同一 finding 支持多个 Paths 时，为每个相关 `path_id` 保留对应结论。

精确数值并非所有边界的必要形式。阶段跃迁、相对时间、约束生效、重复商业采用或合格可销售产出同样可以构成有效 Calibration。经过针对性研究仍无法建立可靠 direct-trade boundary 时，将 Path 收敛为 `UNRESOLVED`；它表示无法形成直接交易边界，而不是一般意义上的信息不完整。

## 5. 从 Tradable Path 编译 Policy

### 5.1 先确定可交易的现实边界

Activation Boundary 必须相对于最新确认现实：以 D2 State 为研究起点，并吸收 Reference View 已确认的后续进展。已经成为现实或已经包含在 expectation baseline 中的状态不是新的触发；寻找下一项足以产生边际修订的变化。

边界选择平衡两种错误：过早会把普通进展误判为 thesis-changing information，过晚则等到结果完全兑现后才交易。目标是最早的可靠状态：它已经足以迫使合理投资者修改 expectation，又没有机械推迟到最终财务结果。

按以下顺序形成每个 Condition：

1. `reference_state`：当前已经成立到哪里；
2. `trigger_boundary`：还需跨过哪条具有经济意义的边界；
3. `qualifying_evidence`：什么事实足以确认边界已经跨过；
4. `criterion`：把该边界压缩成实时系统需要判断的清晰命题。

Calibration 可以采用数值阈值、阶段跃迁、合同或监管状态、相对时间变化、商业采用或生产状态。阈值来自 D2 baseline 与现实经济含义，而不是为了显得精确而任意设定。

### 5.2 选择 Activation Conditions

先尝试在 `activation_conditions` 中用一个可观察 Condition 表达完整可交易状态。多个事实各自都是直接交易所必需、且同一条消息能够同时确认全部条件时，使用多条件 Policy；这些 Conditions 具有固定 `AND` 语义。

如果多个事实通常跨不同消息或不同阶段出现，优先使用能够包含前序进展的后续现实状态作为一个 Condition；彼此独立的替代触发则拆成不同 Paths 或 Policies。条件数量服务于尽早且可靠的交易边界，不以追求单条件为目的推迟到明显更晚的证明点。

### 5.3 完成 Policy 表达

`decision` 应与 Path 的 expectation transmission 一致。随后写入精确的 `source_refs`，每项包含当前 D2 中真实存在的 `shell_id + expectation_id + gap_id`。

最后完成 `title`、`activation_summary` 和 `match_scope`：

- `title` 概括现实触发主题；
- `activation_summary` 紧凑表达什么状态成立时采取何种方向；
- `match_scope` 最后写，描述哪些新消息值得召回本 Policy，可以宽于 Activation Conditions，但仍围绕同一现实对象和变化路径。

## 6. Progressive Drafting 与 Canonicalization

一条 Path 完成编译后，立即将符合 Policy schema 的完整 draft 写入 `output/work/policies/<temporary_policy_id>.json`，再把对应 Worklist entry 更新为 `COMPILED` 并写入 `policy_ids`。INITIALIZE 始终写 temporary draft identity；Previous Policy Set 只提供 semantic continuity 与 Canonicalization reference，是否继承 stable `policy_id` 由后续确定性 ID 阶段处理。无法收敛的 Path 写为 `UNRESOLVED` 并记录具体原因，使其显式进入后续 Coverage Map。

创建新 draft 前，比较当前 Shell、此前 waves 的全部 drafts 和 Previous Policy Set。Canonicalization 比较的是：

```text
现实触发状态 + Activation Boundary + decision
```

同一底层状态跃迁、同一具有经济意义的边界和同一 `decision` 表示同一 Policy。文案、消息来源、例示证据或具体措辞不同不足以拆分；被交易的现实状态、经济边界或方向不同才保持独立，同一主题本身也不足以合并。合并时汇总 `source_refs`，并让相关 Paths 指向同一临时 Policy ID；当前 D2 的 Gap surface 决定本轮完整 drafts。

## 7. Wave Gate：关闭 Shell

一个 Shell 在以下状态成立后完成：

- 全部 Gap 已进入 Worklist；
- 每条 Path 已为 `COMPILED` 或 `UNRESOLVED`；
- 需要 Calibration 的 Path 已有对应结论；
- 每个 `policy_ids` 都指向实际存在的 draft；
- 当前 Policy drafts 能按 Policy schema 解析。

随后使用现有 WaveState 字段原子更新 `completed_shell_ids`、`current_shell_id`、`completed_path_ids` 和 `updated_at`，再进入下一 Shell。`wave_state.json` 只承载恢复位置；研究结论保留在 Worklist、Calibration Log 和 Policy drafts。

全部成功 Shell 完成后，保留完整过程文件和 drafts 给同线程 Final Global Pass，并返回节点 output schema 要求的小型 `O3RunResult`。
