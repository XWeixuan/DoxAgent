# O3 Shared Foundation

本文件定义 `O3_INITIALIZE`、Final Global Pass 和 `O3_MAINTAIN` 共同使用的业务语义。当前 stage skill 决定这一 Turn 如何执行；supplied schema 决定对象的精确字段。

## 1. 从 Potential Gap 到 Direct Trading Policy

D2 已经研究当前 expectation、Realization Factors，以及哪些未来现实变化可能迫使 expectation 修订。O3 在这个 revision space 内完成以下转换：

```text
Potential Gap
→ Tradable Path
→ Activation Boundary
→ Direct Trading Policy
```

- **Potential Gap** 是 D2 对未来可能发生什么、为什么可能发生、如何修改 expectation 以及如何识别的研究对象。它提供 Policy 的研究来源，不直接等于交易条件。
- **Tradable Path** 是从 Gap 中展开的一条经济含义完整的现实路径：一个主体或系统从当前状态转向一个新状态，并对目标 ticker 形成单一 `LONG` 或 `SHORT` 方向。一个 Gap 可以有多个现实落点或相反 Paths。
- **Direct Trading Policy** 是对一条或多条相同现实路径预先制定的判断标准。它规定哪些消息值得召回、什么现实边界成立时触发，以及触发后的预定方向；它本身不是实时交易执行。

多个 Gaps 或 Units 可以汇入同一 Policy；同一 Gap 也可以因主体、现实落点或方向不同形成多条 Policies。Policy surface 服从真实交易路径，而不是一 Gap 一 Policy 的机械映射。

## 2. 方向来自目标 ticker 的经济传导

`decision` 按以下链条确定：

```text
现实变化
→ D2 的 expected_revision
→ 目标 ticker 的净经营、风险或估值影响
→ LONG / SHORT
```

消息对其直接主体的正面或负面含义，不一定等于目标 ticker 的方向。竞争者扩产、客户采用、监管变化或供应中断，都需要先判断它怎样改变 D2 expectation，再判断这种修订对目标 ticker 的净结果。

一条 Tradable Path 保持单一现实含义和方向。相反方向通常意味着不同主体、不同状态跃迁或不同传导，应分别表达；若只是措辞视角不同而底层传导相同，则维护同一方向和 Policy。

## 3. Recall、Activation 与 Calibration

Policy 的几个部分承担不同任务：

- `match_scope` 描述哪些新消息可能与该 Policy 有关，用于高召回候选匹配；
- `activation_conditions` 描述哪些尚未成为现实的边界必须成立，用于实际触发；
- `activation_summary` 紧凑表达 Policy 何时成立以及对应方向，服务于快速理解和 Runtime Projection；
- Calibration 解释 Condition 相对于当前现实为什么这样设定。

因此，相关消息可以进入 `match_scope`，但只有跨过交易边界的事实才满足 Activation Condition。

每个 Condition 形成一条连续判断链：

```text
reference_state
当前已经成立什么
↓
trigger_boundary
还需要发生什么具有经济意义的变化
↓
qualifying_evidence
什么可观察事实足以确认变化已经发生
↓
criterion
实时系统最终判断的清晰命题
```

四者表达不同层次，不是同一句话的重复改写。Calibration 可以使用数值阈值，也可以使用阶段跃迁、相对时间、合同或监管状态、商业采用、生产状态等更符合现实机制的边界。

## 4. 当前现实与最早可靠边界

Active Policy 描述相对于当前 State 仍待发生的现实变化。已经发生、已经成为持续现实或已经进入 expectation baseline 的事实属于 `reference_state`；下一项足以迫使 expectation 修订的变化才属于 `trigger_boundary`。

高质量边界位于普通进展与结果完全兑现之间：它应是最早的可靠状态，既能排除不足以改变 thesis 的噪音，也没有推迟到经济结果已经充分反映。判断重点是现实变化是否达到足够的商业、法律、采用、供需、生产或财务意义，而不是一律追求更晚或更精确的证明。

当某个 Condition 已成为现实时，把它吸收到当前起点。同一 Path 仍有自然且可交易的下一阶段时推进 Policy；没有剩余未来边界时，由当前 stage 将该 Path 收敛为 `UNRESOLVED` 或 retire 对应 Policy。

## 5. Condition Composition

先判断一个可观察 Condition 能否完整表达交易状态。多个事实各自都不可缺少、且同一条消息能够同时确认时，使用多条件 Policy；全部 Activation Conditions 采用同消息 `AND` 语义。

跨不同消息或阶段逐步成立的事实，可以用一个包含前序进展的后续现实状态表达，或按不同 Tradable Paths 拆开。彼此独立的 `A OR B` 是不同 Paths 或 Policies。目标是在保留足够早的有效交易边界下，使用最少且最清晰的 Conditions，而不是以条件数量本身决定边界。

## 6. Canonicalization、Identity 与 Provenance

Canonicalization 比较底层语义：

```text
现实主体或对象
+ 状态跃迁
+ Activation Boundary
+ decision
```

四者实质相同通常维护一条 Policy，合并其 `source_refs`；主体、边界、时间范围、方向或目标 ticker 的经济含义不同则保持独立。标题和措辞相似不构成合并依据，措辞不同也不妨碍共享同一 Policy。

每个 `source_refs` 必须精确对应 D2 中真实存在且支持该交易含义的 `shell_id + expectation_id + gap_id`。它表达 D2 provenance，不是 Web/Data MCP citation 字段；外部研究用于校准现实状态和执行边界。

Initialize draft 可以使用 `tmp_pol_001` 一类临时 `policy_id`，新 Condition 可顺序使用 `C1`、`C2`；确定性 assembly 负责分配或延续稳定身份。Maintenance 对同一 Tradable Path 的现实起点、Calibration、Condition 或措辞进行推进时保持既有身份；交易含义实质改变时建立独立 Policy。语义未改变的 Condition 保持对应 `condition_id`。

所有自然语言字段以中文为主体，字段名、公司与产品名、行业缩写和原始计量单位可以保留英文。
