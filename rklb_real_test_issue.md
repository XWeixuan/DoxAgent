# RKLB 真实初始化：故障根因与修复审计

## 1. 文档范围

本文不再按轮询时间记录运行流水，而只保留能够暴露系统性缺陷、影响后续初始化可靠性的故障。

以下内容已从本文移除：

- 内存预留、cgroup 上限、资源 admission 与优先级反转；
- Worker 重任务队列、公平性和 heavy-batch 排队策略；
- O2/D2 子任务因初始化批次身份缺失而被资源队列误分类的问题。

这些问题已有独立修复和测试，不再在本文件重复。本文集中讨论四个后续问题域：

1. durable invocation 无法持久化真实节点参数；
2. 投影链被一个合法的退役 Event 永久阻塞，导致 Overview 与权威状态不一致；
3. D3 产生系统性无效结果后仍被标记为成功，并进一步让 O4 形成空配置假完成。

## 2. 结论摘要

| 问题域 | 直接表现 | 系统根因 | 当前状态 |
| --- | --- | --- | --- |
| Durable invocation 完整性 | O2 Wave 在模型调用前因 `WavePlan` 无法序列化而失败 | durable codec 只覆盖通用类型，没有覆盖 workflow 自有的冻结 dataclass | 已修复已出现类型；仍需做类型面审计 |
| 投影与 Overview 一致性 | Runtime 已 RUNNING，Overview 仍为 INITIALIZING/BLOCKED | Event Library 读取模型把“退役后零活跃 Fact”的合法状态判成非法，activation gap 又阻断后续 ticker-control 投影 | RKLB 实例已闭环；同类 poison-event 可观测性仍不足 |
| D3 语义假完成 | 147 条 Policy 在数分钟内生成，Final Review 又全部撤销，但父节点仍 SUCCEEDED | 全量 Surface 被塞进两个大 Turn；同时编排器把 `REVIEW_BLOCKED` 降级成 `PARTIAL` | Shell-wave 编排已修复并通过 RKLB 重跑；阻塞语义仍未彻底修复 |
| O4 空配置假完成 | D3 为零 Policy 时，O4 不运行模型却在约 1 秒内完成 | 空 Policy 快速路径被解释成“无需专门监测”，注册又只验证存在任意 Binding，不验证 Policy coverage | 尚未修复；本次 O3-only 重跑主动绕过 O4 |

## 3. Issue A：O2 WavePlan 无法进入 durable invocation

### 3.1 现象与排除项

`o2-survey` 已成功，但第一个 O2 Wave 在模型 dispatch 之前失败：

```text
TypeError: unsupported invocation argument: WavePlan
```

失败发生在 durable substep 冻结调用参数时，因此不是模型错误、额度问题、Event Library 校验失败，也不是运行时资源不足。父 O2 因子节点连续失败耗尽重试预算，只能等待人工 resume。

### 3.2 根因

Ticker initialization 的内部子节点要把“调用参数 + pre-node workspace snapshot”写入 durable receipt，才能做到：

- 进程重启后重附着；
- 只重跑失败的内部节点；
- 从节点执行前的精确 workspace 恢复，而不是重跑整个父阶段。

但 invocation codec 的类型系统只支持 Pydantic model、Enum、Path、datetime、tuple/list/dict 等通用对象。O2 的 `WavePlan` 是 repository-owned frozen dataclass，属于正常业务合同，却不在 codec 可表达范围内。

本质上这是两套合同漂移：workflow 已把 `WavePlan` 当成 durable node 输入，durable layer 却没有把它纳入可持久化类型闭包。错误直到真实 O2 Wave 才暴露，说明此前测试只验证了通用示例，没有覆盖生产节点的完整 invocation surface。

### 3.3 已实施修复

提交 `c48a5258` 增加了受控 dataclass 编解码：

- 使用显式 `dataclass` tag 持久化 repository-owned dataclass；
- 字段继续递归经过既有 codec，不使用 pickle；
- decode 前仍执行 `doxagent.*` module/name allowlist，不能由 receipt 指定任意 import path；
- 新增 `WavePlan` round-trip 回归测试；
- resume 只重置失败 Wave 与其托管父 O2，保留已经成功的 survey。

修复后，原失败节点通过正式 resume 恢复，没有重做成功的上游研究。

### 3.4 同类风险与待修复项

当前修复解决了“已出现的 dataclass 类型”，但还不是 durable invocation 完整性的系统证明：

- 缺少对全部 durable-decorated 函数签名的自动枚举与 encode/decode round-trip 测试；
- `dataclasses.asdict()` 会把嵌套 dataclass 展平成普通 dict，未来若出现需要保留嵌套具体类型的合同，仍可能解码失真；
- 新增 workflow 参数类型时，没有静态或启动期检查确保它属于 codec 的闭包。

建议增加一项合同测试：枚举所有 durable invocation fixture，以真实参数执行 `decode(encode(args))`，并比较类型和值；任何新类型必须先注册和测试，不能等到生产 resume 路径暴露。

## 4. Issue B：合法退役 Event 阻断 activation 与 Overview 投影

### 4.1 表面矛盾

RKLB 初始化和 Runtime 权威状态已经完成：

- Runtime control revision 4 为 `RUNNING`；
- `initialization_incomplete=false`；
- `analysis_allowed=true`；
- activation ID 已提交；
- 初始化 progress 为 `SUCCEEDED`。

但 Overview read model 一直停在 revision 3，并显示：

```text
INITIALIZING
initialization_failed=true
health=BLOCKED
```

因此问题不在初始化执行，而在“权威写模型 → projector → read model”的投影链。

### 4.2 失败链路

投影 gap 不是两个独立错误，而是一条依赖链：

```text
RKLB Event E66 读取失败
→ activation event 55594 无法建立 pinned Event Library 索引
→ activation revision 未进入 read model
→ ticker-control event 866098 找不到前置 activation
→ revision 4 每次重试都失败
→ Overview 永久显示旧 revision 3
```

源 checkpoint 已经越过这些事件，但 gap 会持续重试；所以“checkpoint 前进”并不等于 read model 已完整收敛。

### 4.3 根因

RKLB Event `E66` 是已经合并退役的 `MERGED` Event。其 Fact 已迁移或退休，因此当前活跃 Fact membership 为空是合法终态。

旧 `CanonicalEvent` 合同却对所有生命周期状态统一施加 `facts.min_length=1`。这混淆了两类不同不变量：

- `ACTIVE` Event 必须至少有一个当前 Fact；
- `MERGED` / `SUPPRESSED` Event 可以在退役后没有当前活跃 Fact，但仍必须作为不可变历史快照存在。

Projector 读取合法的历史产物时触发 `ValidationError`，导致一个记录级生命周期差异升级为 activation 和整个 ticker-control 投影的永久阻塞。

### 4.4 已实施修复

提交 `ce74500a` 调整了读取合同，而没有修改生产数据：

- `ACTIVE` Event 继续强制至少一个 Fact；
- 已发布的 `MERGED` / `SUPPRESSED` Event 允许零当前 Fact；
- 新写入的 `CanonicalEventRevision` 仍保留严格的非空约束，避免用“退役兼容”放宽新 revision 的写入质量；
- 增加 ACTIVE-empty 拒绝、retired-empty 接受和 V2 Library indexing 测试。

部署后 projector 使用原有不可变 receipts 自动恢复：

- gap `55594` 和 `866098` 清除；
- activation revision 与 active pointer 进入 read model；
- ticker control 到达 revision 4 / `RUNNING`；
- Overview 不再有显示 INITIALIZING/BLOCKED 的后端依据。

### 4.5 同类风险与待修复项

RKLB 的具体 schema 问题已修复，但 poison-event 的故障放大机制仍值得处理：

- 当前下游 gap 主要暴露最后一个 `ValueError`，不直接展示被哪个上游 gap 阻塞；
- Overview 只显示旧状态，无法区分“初始化未完成”和“投影滞后/投影失败”；
- 对其他 lifecycle-aware read contract 尚无统一审计，未来 schema 演进仍可能让合法历史对象无法读取；
- checkpoint、gap 数量、active revision 和 read-model revision 没有形成一个面向运营的收敛判据。

建议把 `source event → prerequisite gap → downstream gap` 依赖链投影到诊断接口，并在 Overview 将 `CONTROL_STATE_STALE` 与真实 `INITIALIZING` 分开。部署验收必须同时验证 source head、checkpoint、零 gap、active revision 和 read-model revision，不能只看容器健康或初始化节点终态。

## 5. Issue C：D3/O3 在语义上失败，却在控制面成功

### 5.1 真实失败结果

原 D3 run：

```text
init-rklb-9631e4071e75470a97313eafbbdc51aa-d3
```

原编排只用了三个 Agent Turn 处理完整的 147 Path Surface：

| 阶段 | 耗时 | 返回表象 |
| --- | ---: | --- |
| Trigger Calibration | 约 3 分 07 秒 | 147 Path 全部完成 |
| Policy Compile | 约 1 分 18 秒 | 147 Policy |
| Final Review | 约 1 分 27 秒 | `REVIEW_BLOCKED` |

Final Review 发现的不是少量残留，而是系统性失败：

- 147/147 Trigger 机械复用了 D2 `possible_occurrence`；
- 147/147 被声明为 boundary sufficient；
- 139 条存在 hidden-OR 候选；
- 31 条使用没有 comparator 的程度词；
- 前两阶段产生了不可信的零 unresolved 结果。

Final Review 随后将 147 条 Path 全部改为 `UNRESOLVED`，将 Trigger disposition 全部改成 `TRIGGER_UNRESOLVED`，删除全部 147 个 Policy draft，并返回：

```text
REVIEW_BLOCKED
SYSTEMIC_TRIGGER_CALIBRATION_MISSING
requires_research=true
blocking=true
```

最终发布物实际是：

```text
publication_state=PARTIAL
policy_count=0
```

前端 D3 全空因此不是展示错误，而是后端真实发布了零 Policy。

### 5.2 已排除模型额度故障

对应 Codex thread 明确记录：

```text
model=gpt-5.6-sol
reasoning_effort=medium
weekly used_percent=100%
credits.has_credits=true
rate_limit_reached_type=null
```

三个 Turn 都正常执行并持续扣减赠送额度，没有 rate-limit rejection、模型 fallback 或 context overflow。并且同一个模型在 Final Review 正确发现了系统性缺陷。

因此不能把结果归因于“周额度用完导致模型没运行”。更准确的结论是：前两个 Turn 在过大的工作面上选择了机械批量生成，而编排和验收没有及时阻止这种执行方式。

### 5.3 根因一：工作面与 Turn 粒度错误

旧编排把五个业务 Shell、147 条 Path 放进一次 Trigger Calibration 和一次 Policy Compile。模型面对过大的输入和写入面时，用程序化批量转换代替逐 Path calibration；结构校验仍能通过，因为引用、数量和 schema 看起来闭合。

这暴露了一个重要边界：

> schema 完整、Path 数量覆盖和 Agent 返回 `COMPLETED`，都不能证明 Trigger 已完成独立市场基线和可交易边界校准。

### 5.4 根因二：Final Review 的阻塞语义被降级

当前 orchestrator 对以下结果：

```text
review.status == REVIEW_BLOCKED
blocking_issue_count > 0
```

仍写入 `FINAL_REVIEW_AGENT_BLOCK_REQUEST`，并统一转换为：

```text
severity=RECOVERABLE
recovery_action=continue_partial
```

随后完成 Final Review checkpoint、继续 deterministic validation、assembly 和 publish。由于“所有 Path 均为 UNRESOLVED、零 Policy”在结构上仍可表达为 PARTIAL，父 D3 最终被控制面标记为成功。

这里误用了“局部坏项不得阻塞整体”的恢复原则。该原则适用于个别 Path 无法研究；不适用于 147/147 全部失效、`requires_research=true`、关键发布组件为空的系统性失败。

### 5.5 已实施修复：按 Shell 拆分并持久化每个 Wave

提交 `d7d1c24c` 重构了 D3 编排：

- 每个 Document2 Shell 单独生成冻结 slice；
- 每个 Shell 依次运行 Trigger Calibration 和 Policy Compile；
- 五个 Shell 共形成十个可观察、可恢复的业务 Turn，Final Review 保持全局复核；
- durable child key 加入 `shell_id`，可以只恢复失败的 calibration/compile wave；
- `trigger_calibration_state.completed_shell_ids` 与 `wave_state.completed_shell_ids` 决定断点，不再把整个 D3 当作一个不可分割节点；
- input manifest 纳入动态 Shell slices，保持冻结输入校验；
- 新增双 Shell 执行顺序、同线程连续性、per-Shell slice 和第二 Shell Compile 精确 resume 测试。

RKLB O3-only 正式重跑 `init-rklb-70af5e578a774711b05dcd70e19a67b2` 的结果证明该编排能够产出可用结果：

```text
Final Review: PASSED
Path: 147 completed
Policy: 98
Condition: 106
UNRESOLVED Path: 29
Policy Set version: 2
publication_state: PARTIAL
activation: SUCCEEDED / VERIFY_READY
```

`PARTIAL` 在这次结果中来自有明确理由的 29 条 Path-level `UNRESOLVED`，不是零 Policy 或全局 Review 阻塞。新 D3 已激活为：

```text
init-rklb-70af5e578a774711b05dcd70e19a67b2-activation
```

### 5.6 尚未闭环的同类问题

Shell-wave 重构降低了机械批量生成风险并改善了断点恢复，但以下假成功条件仍存在于当前代码：

1. **`REVIEW_BLOCKED` 仍被无条件降级。** 需要区分 Path-local recoverable issue 与 global/critical blocker。至少在全部或高比例 Path unresolved、零 Policy、`requires_research=true`、Final Review 明确 blocking 时停止发布并让 D3 可正式 resume。
2. **非空关键产物不是 D3 成功条件。** 对有 147 条 expected Path 的初始化，零 Policy 不能只凭结构闭合进入成功终态；应增加“可用 Policy 或有业务上充分的全量 NO_POLICY 证明”这一关键组件 gate。
3. **阶段声明仍过度信任 artifact shape。** 每个 Shell 的 `COMPLETED` 应结合语义诊断，例如 D2 文本逐字复制率、boundary-sufficient 异常集中、零 unresolved、hidden-OR 密度和 comparator 缺失；这些指标不能自动定罪，但达到系统性阈值时必须进入 Final Review 前的显式风险状态。
4. **状态轮询仍可能只看控制面。** 初始化监测应同时读取 Final Review status、Policy 数量、unresolved 比例和 publication state；`SUCCEEDED` 只表示编排终止，不应单独对外宣称业务完成。

## 6. Issue D：D3 空产物触发 O4 空配置假完成

### 6.1 失败传播

原 D3 发布零 Policy 后，O4 收到的输入为：

```text
publication_state=PARTIAL
policies=[]
```

O4 并没有运行所谓的 `gpt-5.6-sol medium` 配置节点。`_configure()` 对空 Policy Set 存在确定性快速路径，直接生成：

```text
source_needs=[]
baseline_summary={}
stopping_rationale="No policies require dedicated monitoring; existing baseline retained"
```

因此 `o4.configure` 在不到一秒内完成；`o4.deliver` 因不存在 `NEW_CRAWLER_REQUIRED` 返回 `NOOP`；`o4.register` 只要发现 ticker 已存在任意 enabled Binding 就成功。RKLB 原有五个通用 Binding 因而被错误当成 O4 配置完成证据。

真实 O4 数据却是：

```text
source_needs=0
delivery_checkpoints=0
delivery_settlements=0
worker_dispatches=0
```

这解释了为什么 O4 看似瞬间完成、前端却没有任何与 D3 Policy 对应的监测配置。

### 6.2 根因

系统没有区分两种语义完全不同的“空 Policy Set”：

- 合法业务结论：经过完整 Review，确实没有任何需要持续监测的 Policy；
- 上游失败产物：D3 被阻塞、关键 Policy 被全部撤销或尚未完成研究。

O4 仅检查 `policies == []`，没有检查 D3 Final Review、publication reason、expected Path、unresolved 比例或 critical-component 状态。注册阶段又验证“存在任意可用 Binding”，而不是“每个 Policy 的 monitoring need 已被覆盖或有明确 omission”。两个弱验收叠加后形成跨节点假成功。

### 6.3 当前处理与验证边界

这次 RKLB 重跑根据额度约束只执行 O3，并保留现有监测配置，没有运行 O4。这样避免再次消耗 O4 模型额度，也让 RKLB 以新 D3 version 2 完成激活；但它不是 O4 bug 的修复或验收。

因此当前状态必须准确表述为：

- D3/O3 已重跑、通过 Final Review 并正式激活；
- 原监测配置被保留，用户将另行调整；
- O4 自动配置的空输入假成功路径仍存在；
- 原五个 Binding 不能被当作 98 条新 Policy 的 coverage 证明。

### 6.4 待修复项

1. O4 初始化收到 `policies=[]` 时，只有上游携带明确的 `NO_MONITORING_REQUIRED` 业务证明才允许成功；`PARTIAL`、`REVIEW_BLOCKED` 或关键组件缺失必须拒绝配置。
2. O4 configure plan 需要记录 Policy coverage：每条 Policy 必须映射到 existing coverage、new source need 或 deliberate omission，不能只返回空 `source_needs`。
3. O4 register 成功条件必须验证 plan coverage 和 delivery settlement；ticker 上已有任意 Binding 只能证明 Message Bus 可用，不能证明本次 Plan 已交付。
4. Overview 应分别展示 D3 Policy readiness、O4 plan coverage 和 Message Bus binding readiness，避免一个笼统“初始化完成”掩盖空配置。
5. 增加端到端回归：`REVIEW_BLOCKED + zero policies` 不得进入 O4；非空 Policy Set 的 O4 plan 必须覆盖全部 Policy 或留下逐项可审计 omission。

## 7. 后续修复优先级

### P0：阻止再次产生假完成

- 把系统性 `REVIEW_BLOCKED` 升级为真正的 D3 blocker；
- 阻止异常零 Policy Set 进入 O4；
- 初始化业务完成判定加入 Final Review、Policy 数量和 O4 coverage。

### P1：让故障可定位而不是只显示旧状态

- 暴露 projector gap 的前置依赖链；
- 区分初始化执行中、初始化失败和 read-model stale；
- 对 durable invocation 建立全类型 round-trip 合同测试。

### P2：防止语义批量生成回归

- 将异常复制率、零 unresolved、boundary-sufficient 集中度等作为风险诊断；
- 保留 per-Shell Turn、per-Shell durable key 和精确 resume；
- 用真实多 Shell acceptance 覆盖 D3 → activation，并单独补做 D3 → O4 coverage acceptance。

## 8. 最终状态

截至本次审计：

- RKLB D3 version 2 已激活，Final Review `PASSED`；
- 98 条 Policy / 106 个 Condition 可用，29 条 Path 以明确原因保留 `UNRESOLVED`；
- 初始化运行 `init-rklb-70af5e578a774711b05dcd70e19a67b2` 为 `SUCCEEDED / VERIFY_READY`；
- Overview 投影故障已经恢复；
- O4 自动配置没有在本次重跑中执行，现有监测配置不代表新 Policy coverage；
- D3 系统性 Review blocker 的终态语义和 O4 空计划验收仍是两个必须补齐的 P0。
