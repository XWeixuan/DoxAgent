# O3 Final Global Pass

本 Turn 承接同一线程的 `O3_INITIALIZE`，把各 Shell wave 形成的局部结果收敛为一套全局一致、可执行的 Policy drafts。Initialize 已负责展开完整 Gap surface、研究 Calibration 并渐进写入 drafts；本阶段从完整集合判断覆盖、重复、冲突和交易边界，并直接修复能够解决的问题。

Final Review 是初始化生命周期的最后一次研究与编辑，不是只读审计。它有权修改 Policy drafts、worklist、calibration log、wave state 和 coverage map，也可以为明确的全局问题开展必要研究。目标是留下相互一致的最终工作文件，以及只描述修复后仍然存在问题的 ReviewResult。

Final Global Pass 要在保持 D2 Potential Gap 覆盖的前提下，使最终 Policies 全局一致，并分别表达仍面向未来、能够产生边际 expectation revision、尽可能早但可靠、可由未来消息低自由度判定的交易状态。优化对象是这些 Policy 品质，而不是最高确定性、最少 Policy 或最少 issue。

## 1. 重建全局工作状态

读取 `task.json`、完整 Published D2、可选 Reference View、Previous Policy Set，以及当前 attempt 的完整 `worklist.jsonl`、`calibration_log.jsonl`、`wave_state.json`、`policies/` 和 `coverage_map.json`。

D2、worklist 和 Policy drafts 是 coverage 关系的业务源；calibration log 记录边界研究依据。`coverage_map.json` 是 Final Review 维护的派生工作视图：先修正 Gap / Path / Policy 源对象，再同步 coverage map，使其反映最终状态；单独修改派生视图不改变业务关系。Previous Policy Set 用于判断语义与身份连续性。

D2 定义 expectation、经济传导和 revision space；Reference View 及必要的当前研究确定现实已经推进到哪里。若后续事实已确认 D2 中的未来状态发生，将其吸收到新的 `reference_state`，再沿原 D2 Path 寻找下一项仍面向未来的边界，而不是继续以 D2 publication 时点作为现实起点。

先形成两个全局视图：

```text
D2 coverage view
→ 每个成功 Shell 的全部 Gap、展开的 Paths、状态和 Policy 映射

Policy semantic view
→ 每条 Policy 的现实对象、状态跃迁、Activation Boundary、decision 和来源 Gaps
```

这两个视图用于发现 Shell 内局部处理无法看见的遗漏、重复和方向冲突；它们是思考框架，不新增工作产物或字段。

## 2. 覆盖与引用闭合

按 D2 的成功 Shell 逐项核对：

- 每个 Potential Gap 均有对应 Worklist entry，且 `shell_id + expectation_id + gap_id` 精确指向 D2；
- 每条 Tradable Path 已收敛为 `COMPILED` 或 `UNRESOLVED`；
- `COMPILED` Path 的每个 `policy_ids` 都指向实际存在的 draft，`UNRESOLVED` Path 有具体 `unresolved_reason`；
- 每条 Policy 的 `source_refs` 只包含真实支持其交易含义的 D2 Gaps，并完整使用 `shell_id + expectation_id + gap_id`；
- 多个 Paths 可以指向同一 Policy，同一 Gap 也可以展开多个方向或现实落点；
- `completed_path_ids`、`completed_shell_ids`、`current_shell_id` 与实际完成状态一致；
- failed shells 继续作为已知输入边界保留在 coverage，不为其构造缺失的 Gap 或 Policy。

发现映射问题时先还原真实语义，再修改 Worklist、Policy 引用或状态。覆盖完整并不要求每条 Path 都产生 Policy。`UNRESOLVED` 表示当前 Path 无法对应一条仍有效的 Direct Trading Policy：可能是处理后仍无法建立可靠边界，也可能是未来交易空间已经兑现或耗尽；`unresolved_reason` 应准确区分原因，一般的信息不完整本身不足以成立。

## 3. 全局 Canonicalization

跨全部 Shell、Units 和 waves 比较 Policy 的底层含义，而不是标题或措辞。依次判断：

```text
现实主体或对象是否相同
→ 状态跃迁是否相同
→ Activation Boundary 是否实质相同
→ decision 是否相同
```

四者相同通常是一条共享 Policy。选择表达最清晰、边界最可靠的 draft 作为主体，合并去重后的 `source_refs`，将全部相关 Paths 的 `policy_ids` 指向它，并移除冗余 draft file。主体、时间范围、现实边界、方向或目标 ticker 的经济含义不同，则保持独立。

看似相反的 Policies 应重新走一遍传导链：

```text
现实变化
→ D2 所描述的 expectation revision
→ 对目标 ticker 的净经营、风险或估值影响
→ LONG / SHORT
```

如果相反方向来自不同现实状态或不同 Paths，分别保留；如果源于传导遗漏或消息表面措辞，修正 `decision`、Path 表达或 Policy 边界。不要用形式上的“正面/负面”替代目标 ticker 层面的净经济判断。

## 4. 交易逻辑与 Activation Boundary

逐条检查 Policy 是否把一项未来消息转化为低自由度、可执行的判断：

### 4.1 边际信息

`reference_state` 应准确说明当前已经成立、并已进入 expectation baseline 的状态；`trigger_boundary` 是下一项足以迫使 expectation 修订的现实跃迁。已经发生、已被普遍预期或只是现状的事实应被吸收到 reference state，而不是继续作为未来触发。

边界应位于“普通进展”与“结果完全兑现”之间的最早可靠位置：过早会把噪音当成 thesis-changing information，过晚会失去合理 alpha。审查其是否达到足够的商业、法律、产能、采用、供需或财务意义，而不是机械追求更晚或更确定的证明。

### 4.2 召回与触发

`match_scope` 负责高召回地描述哪些新消息值得交给该 Policy 判断；Activation Conditions 负责窄而明确地决定是否触发。相关消息可以进入 match scope，但只有跨过现实边界的证据才进入 Conditions。

### 4.3 条件结构

先判断一个 Condition 能否完整表达可交易状态。多项事实各自都不可缺少、且同一条消息能够同时确认时，使用多条件 Policy；全部 Conditions 具有同消息 `AND` 语义。

跨不同消息或阶段逐步成立的事实，更适合压缩为能够包含前序进展的后续现实状态，或按不同 Tradable Paths 拆开。彼此独立的 `A OR B` 触发方式应形成不同 Paths 或 Policies。条件结构服务于清晰且足够早的边界，不以条件数量本身为优化目标。

## 5. Calibration 质量

对每个 Activation Condition 分别回答四个问题：

1. `reference_state`：当前已经成立到哪里？
2. `trigger_boundary`：还需要跨过哪条具有经济意义的边界？
3. `qualifying_evidence`：什么可观察事实足以确认已经跨过？
4. `criterion`：实时系统最终需要判断的清晰命题是什么？

四者应构成从现实起点到执行判断的连续链，而不是相互改写同一句话。重点修复循环定义、任意精确阈值、无法判断的“重大/明显”表述、只确认计划或小样本的过早证据，以及等到最终业绩完全兑现的过晚标准。

有效边界不必总是数值化。客户验证转为重复采购、试生产转为合格商业产出、提案转为生效规则、单点任务或发射达到新的执行阶段，都可以比武断数值更准确。Calibration 的精度应服从对应现实机制和消息可观察性。

## 6. 已成为现实的触发条件

结合 D2、Reference View 和必要的当前研究，判断每项触发是否已经成为现实：

- 若尚未跨过边界，保留未来条件并校正当前 reference state；
- 若边界已经跨过，且同一 D2 Path 存在自然的下一项可交易阶段，将已发生进展吸收到 reference state，推进 `trigger_boundary`、`qualifying_evidence`、`criterion` 和相关 Worklist / Calibration 表达；
- 若该 Path 的可交易变化已经耗尽，移除对应 Policy 或无效映射，将相关 Path 设为 `UNRESOLVED`，并在 `unresolved_reason` 说明当前已无待触发的有效边界。

推进边界时仍以原 Gap 的 `expected_revision` 和目标 ticker 传导为范围。研究发现的新事实用于校准既有 Path，而不是脱离 D2 Gap surface 新建 thesis。

## 7. 必要研究与直接修复

研究从一个明确问题开始，例如“两个 Policies 是否实际指向同一商业状态”“当前 trigger 是否已经发生”“哪个阶段才足以改变目标 ticker expectation”。先使用完整 D2 和现有过程产物，再查看 Reference View；仍缺现实锚点时，按问题形态选择定向 Web Search 或只读 Data MCP，并遵守 `task.json` 的 cutoff。

Final Review 可以进行实质研究，只要它服务于已识别的覆盖、Canonicalization、方向或 Calibration 问题。当新增证据已足以决定保留、合并或拆分、修正方向、推进边界或标记 `UNRESOLVED` 时，本次研究完成并回到编辑。涉及 Calibration 的新增结论继续使用现有五字段写入 `calibration_log.jsonl`：`path_id`、`calibration_need`、`source_kind`、`finding`、`resolved`；共享结论按相关 `path_id` 分别保留。

按问题所在层级直接修复：

```text
Path 拆分、方向或状态问题
→ 更新 worklist

现实起点或触发边界问题
→ 更新 calibration log、Condition 与 Policy

重复、冲突或来源问题
→ 更新 Policy drafts 与相关 Path 映射

进度与派生覆盖问题
→ 最后同步 wave state 与 coverage map
```

合并后移除冗余 draft file，避免确定性 assembly 再次读取；修改临时 Policy ID 后同步所有 `policy_ids`。每次修复都保持现有 schema 字段和枚举。

## 8. 最终一致性 Pass

所有直接修复完成后，重新读取完整 Worklist、Calibration Log 和 Policy drafts，再执行一次全局复核：

- Gap→Path→Policy / `UNRESOLVED` 是否闭合；
- draft files、`policy_ids` 与 `source_refs` 是否互相一致；
- 重复或方向冲突是否已经解决；
- 每个 Condition 是否相对于当前 State、可由单条未来消息判断；
- 多条件是否满足同消息全部成立的语义；
- wave state 与 coverage map 是否反映最终源文件；
- Policy drafts 是否符合 supplied schema，所有自然语言是否以中文为主体。

这一步检验修复后的完整集合，不把已经解决的问题继续带入发布状态。

## 9. ReviewResult

ReviewResult 是最终残留问题清单，不是审计轨迹或修改日志。只使用以下字段：

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
```

已直接修复的问题不进入 `issues`。修复后仍存在、但不妨碍结构化发布的研究边界，保留相关 Path 为 `UNRESOLVED`，在 `issues` 中准确说明；此时 `status` 为 `PASSED`、`blocking` 为 `false`。Review status 与 publication state 是残留事实的确定性结果，不是 Final Review 的优化目标。由于任何最终 `issue_count` 都会使 publication state 降为 `PARTIAL`，只记录直接修复后仍真实存在且值得下游理解其边界的问题。

只有核心工作文件无法解析且不能恢复、冻结输入已被修改、写边界无法恢复，或 workspace 的核心合同无法重新建立一致性时，使用 `REVIEW_BLOCKED` 和 blocking issue。语义疑问、研究证据有限或部分 Path 无法收敛，使用 `UNRESOLVED` 与非阻塞 issue 表达。

使 `issue_count` 等于 `issues` 数量，`blocking_issue_count` 等于其中 `blocking: true` 的数量。将完整结果写入 `output/work/review_result.json`，并返回完全相同的 ReviewResult 作为本 Turn 输出。
