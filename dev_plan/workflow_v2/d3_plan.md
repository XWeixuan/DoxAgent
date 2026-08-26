# DoxAgent Document 3 / O3 Monitoring Execution Policy V2 开发方案

## 1. 目标与边界

Document 3 V2 只负责 **Monitoring Execution Policy**。

业务编排：

```text
Document 1
    ↓
Document 2 / Expectation Model
    ↓
Document 3 / O3 Policy Compilation
    ↓
Monitoring Configuration
    ↓
Persistent Runtime
```

Event Library 是平行基础设施：

```text
CDECR / O2
    ↓
Published Event / Fact Library
    ↓
Reference Event View
    ├─→ O3 日常 Policy 维护
    └─→ 其他消费者
```

Event Library 不参与实时消息进入 Policy Evaluation 的同步路径。

当前 D2 已经将每个 Expectation Unit 建模为 `State + Realization Factors + Potential Gaps`；Potential Gap 已包含 `possible_occurrence`、`expected_revision` 和 `recognition_criteria`。因此 O3 不重新建立投资 thesis，而是将这些已经研究完成的未来修订空间编译为可直接判断的交易执行 Policy。

---

# 2. O3 的角色

O3 是 **Monitoring Execution Policy Agent**。

核心职责：

> 将 D2 已经形成的未来 expectation revision space，通过必要的现实状态确认和 Calibration Research，编译为能够由后续实时系统低自由度判定的 Direct Trading Policies。

D2 负责：

```text
什么重要？
为什么重要？
当前 expectation 是什么？
哪些现实变化会迫使 expectation 修订？
发生后应该如何修订？
```

O3 负责：

```text
这个变化究竟达到什么可观察状态时，
已经足以成为直接交易条件？
```

O3 可以密集研究，但研究属于：

> **Operationalization / Calibration Research**

不是新的 thesis discovery。

---

# 3. 全局输出语言

O3 所有自然语言产物默认使用 **中文**。

包括：

* Policy title；
* `match_scope`；
* Activation Conditions；
* Calibration；
* Activation Summary；
* checkpoint 中的过程结论；
* Calibration Research 结论；
  -人工可读 Document 3。

以下内容可保持原文或英文：

* schema 字段名；
* ticker；
* `policy_id` / `condition_id`；
* 公司、产品、协议等专有名词；
* DRAM、NAND、HBM、ASP、EPS 等行业缩写；
* 原始数据口径与单位。

---

# 4. O3 只存在两种运行模式

## 4.1 `O3_INITIALIZE`

低频、研究密集。

用于：

* 第一次构建 D3；
* 上游新的 D1/D2 research cycle 完成后重新构建或迁移 Policy。

核心输入：

```text
Latest Published Document 2
Latest Event Library Reference View（可选现实上下文）
Previous Policy Set（如存在）
```

采用：

> **一个 O3 Agent turn + 多个内部 work waves**

不把 Surface、Research、Compile、Finalize 拆成多个 Agent 节点。

---

## 4.2 `O3_MAINTAIN`

高频、轻量。

每日盘前独立运行。

输入：

```text
Current Published Policy Set
Latest Event Library Reference View
Previous reviewed Reference View version
```

默认不重新读取完整 D2。

采用：

> **一个轻量 O3 Agent turn**

只处理现实变化导致的 Policy 更新。

---

# 5. O3_INITIALIZE 顶层 DAG

```text
deterministic input preparation
        ↓
      O3_INITIALIZE
     one Agent turn
     multi-wave execution
        ↓
deterministic validation
        ↓
deterministic assembly
        ↓
publish Policy Set
```

Agent 内部存在明确工作步骤，但这些步骤不是独立 DAG node。

这样保证：

* Calibration Research 保持连续上下文；
* 多个 Gap 可以复用同一次研究；
* 不重复建立行业认知；
* 不支付多个 Agent turn 的上下文重建成本。

---

# 6. 为什么采用一个 Turn + 多 Waves

一个 Shell 是一个独立的研究单元。D2 一个 Shell 可能包含数十个 Potential Gaps，而完整 D2 可能达到上百条。

直接一次性思考全部 Shell 容易出现：

* 前部详细、后部明显降质；
* 忘记已经研究过的 baseline；
* duplicate Policy；
* 跳过 Calibration；
* 最后为了完成任务快速批量生成。

因此：

```text
一个 O3 turn
    ↓
内部多个 wave
```

每个 wave 对应一个完整 Shell，并完成该 Shell 内所有 Gap 的完整编译闭环：

```text
理解 Shell
    ↓
展开 Tradable Paths
    ↓
识别 Calibration 缺口
    ↓
必要时进行定向研究
    ↓
建立 Activation Conditions
    ↓
编译该 Shell 的 Policies
```

Wave 不是按 Gap 数量切分，也不是认知阶段，而是：

> **以 Shell 为边界的独立研究单元。**

建议：

```text
一个 Shell
→ 一个 wave
```

不设置 `O3_INITIALIZE_WAVE_SIZE` 作为按 Gap 数量切分的规则。

如果单个 Shell 包含大量 Gap，也不拆成多个 wave；应在同一个 wave 内完成该 Shell 的整体理解和编译，以保持：

* Shell 内部 State、Realization Factors 与 Potential Gaps 的完整上下文；
* 不同 Gap 之间的关联判断；
* Tradable Path 的统一拆分；
* Calibration baseline 的一致性；
* Policy 去重与合并的一致性。

因此，O3_INITIALIZE 的 wave 数量等于待处理 Shell 的数量，而不是 Gap 的数量。

---

# 7. O3_INITIALIZE 的真实执行思路

## Step 1 — 先理解 Unit，再处理 Gap

进入一个 Unit 时，O3 首先读取：

```text
proposition
State
Realization Factors
all Potential Gaps
```

先形成：

> 这个 Unit 当前到底在交易什么 uncertainty？

不逐 Gap 独立阅读后直接生成 Policy。

例如真实 D2 中 Conventional DRAM Unit 当前同时存在：

* ASP improving；
* bit shipments improving；
  -客户端/手机需求吸收偏弱；
* HBM resource displacement 对传统 DRAM 供给形成支撑。

Policy 必须放在这个整体状态中理解。

这一步主要属于认知准备，不单独产生 artifact。

---

# 8. Step 2 — 将 Potential Gap 展开成 Tradable Paths

对每个 Gap 问：

> 这个 Gap 是否包含多个现实落点、不同方向或不同交易含义？

例如：

```text
制造中断
```

可能变成：

```text
Path A
Micron 自身生产受损
→ SHORT

Path B
主要竞争者生产受损
→ LONG
```

CXL 也可能拆成：

```text
CXL 降低系统总 DRAM consumption
→ SHORT

CXL 提高系统总 DRAM capacity
→ LONG
```

---

## Checkpoint A — `worklist.jsonl`

这一步不能只存在于模型思维中。

O3 必须先写入：

```text
output/work/worklist.jsonl
```

每个 Tradable Path 一条。

最小结构：

```json
{
  "gap_id": "...",
  "path_id": "...",
  "direction": "LONG",
  "path_summary": "...",
  "d2_boundary_sufficient": false,
  "missing_calibration": "当前实际商业化阶段"
}
```

字段含义：

### `gap_id`

对应 D2 Potential Gap。

### `path_id`

O3 本轮生成的工作 ID，只用于初始化过程追踪，不进入最终 Policy schema。

### `direction`

该现实路径对应的预定交易方向。

### `path_summary`

用一句中文描述真正需要编译的现实变化。

### `d2_boundary_sufficient`

D2 现有信息是否已经足够直接写出可执行标准。

### `missing_calibration`

如果不足，明确记录**缺什么**。

可以为空。

---

## 为什么这个 Checkpoint 很重要

它强迫 O3 在 Research 之前先回答：

```text
这个 Gap 真正有几个交易路径？
我到底缺什么？
```

而不是：

```text
看到 Gap
→ 搜很多资料
→ 直接写 Policy
```

最终 validator 必须确认：

> 每个 D2 Potential Gap 至少存在一个对应 worklist entry。

---

# 9. Step 3 — 判断 D2 是否已经足够编译

对于每个 path：

```text
D2 recognition criteria
+
current State
+
expected revision
```

如果已经足够明确：

> 直接进入 Policy Compilation。

例如：

```text
binding customer agreement
→ cancellation / procurement delay / dispute
```

这种现实状态本身已经高度确定，没有必要为了“Calibration”重新做行业研究。

---

# 10. Step 4 — 只识别真正缺失的 Calibration

如果无法直接编译，O3 首先必须回答：

> **我现在具体缺哪一个执行参数？**

允许的典型缺口包括：

```text
current stage
current baseline
normal range
relative timing benchmark
commercial adoption state
production state
contract status
regulatory status
comparable metric
```

避免泛化成：

> “需要进一步研究。”

---

# 11. Step 5 — Lightweight Current-State Check

初始化发生在刚完成的 D1/D2 后，因此：

> **最新 D2 默认作为当前研究基线，不重新审计。**

研究顺序：

```text
D2 sufficient?
    │
    ├─ YES
    │    → compile
    │
    └─ NO
         ↓
Reference View answers?
    │
    ├─ YES
    │    → use it
    │
    └─ NO
         ↓
targeted Web Search
         ↓
只有确实需要高频结构化数据，
且该数据可能已经更新时
         ↓
Data MCP
```

Reference Event View 只提供近期梳理好的 Event / Fact，不假设其拥有世界状态的完整覆盖。当前 O2 的 Published View 本身就是由 Pending Delta / Canonical Event maintenance 产生的版本化事件事实视图。

因此：

> Reference View 没有记录 ≠ 现实没有发生。

但初始化也不因此对所有 Policy 做 Web 审计。

---

# 12. Research Tool 使用原则

## Web Search

用于补充：

> 制定 Activation Standard 实际缺少的现实信息。

典型问题：

```text
目前某产品已经处于什么商业阶段？
当前行业主流预期时间是什么？
某客户采用已经是 sample、qualification 还是 production？
某监管限制目前具体覆盖什么范围？
```

---

## Data MCP

只在两个条件同时满足时调用：

### 条件 A

需要的对象本身属于适合结构化数据查询的 metric。

例如：

```text
historical estimates
time-series price
inventory series
provider-specific consensus
```

### 条件 B

该数据从 D2 完成到 O3 运行之间有现实可能已经更新，或者 D2 根本没有取得该数据。

不因为：

> “我要确认 D2 是不是最新”

而重新调用 Data MCP。

---

# 13. Checkpoint B — `calibration_log.jsonl`

只有发生 Calibration 或现实状态补充时才写。

```text
output/work/calibration_log.jsonl
```

最小结构：

```json
{
  "path_id": "...",
  "calibration_need": "...",
  "source_kind": "REFERENCE_VIEW",
  "finding": "...",
  "resolved": true
}
```

`source_kind`：

```text
D2
REFERENCE_VIEW
WEB
DATA_MCP
```

字段定义：

### `path_id`

对应 Worklist 中的 path。

### `calibration_need`

前一步明确的缺失项。

### `source_kind`

这个结论主要来自哪里。

### `finding`

用于 Policy 编译的当前结论。

### `resolved`

是否已经足够建立执行边界。

---

## Research 行为约束

任何 Web Search / Data MCP 的使用，都应该能够对应到：

```text
worklist.missing_calibration
        ↓
calibration_log.calibration_need
```

这使最终运行记录可以判断：

> O3 是因为一个明确的 Policy 缺口去研究，还是开始无目的扩展研究。

它不是为了做过度审计，而是为了防止 O3 偏离编译任务。

---

# 14. Step 6 — Calibration

O3 将研究结果转换成一个可执行的 Activation Condition。

Calibration 不要求所有条件使用相同模式。

可以是：

### Numeric

```text
新值 > 已冻结 reference
```

### Stage Transition

```text
qualification
→ commercial production
```

### Contractual / Legal State

```text
binding agreement
→ cancellation
```

### Relative Timing

```text
原预计 2H27
→ 明确提前至 1H27
```

### Commercial Adoption

```text
limited deployment
→ recurring scaled procurement
```

### Production State

```text
planned capacity
→ qualified saleable output
```

---

# 15. Calibration 不再压成单一字符串

上一版把：

```text
Baseline
Threshold
Evidence Form
```

压缩成一个 `calibration_basis`。

从 schema 极简角度合理，但根据 Codex 的实际行为，容易变成：

> “当前采用有限，规模扩大即可触发。”

信息量不足。

因此正式 schema 改成：

```json
"calibration": {
  "reference_state": "...",
  "trigger_boundary": "...",
  "qualifying_evidence": "..."
}
```

三个字段已经足够。

不继续拆更多。

---

## `reference_state`

定义：

> 当前比较起点是什么？

例如：

```text
当前已经存在 Tier-1 OEM qualification，
但仅有有限产品和有限区域采用。
```

---

## `trigger_boundary`

定义：

> 从当前状态变化到什么程度时，才达到交易边界？

例如：

```text
从有限采用进入多个主流量产产品的重复规模采购。
```

---

## `qualifying_evidence`

定义：

> 什么性质的现实证据足以认定这个 boundary 已经发生？

例如：

```text
OEM 或供应商正式确认持续商业供货或重复采购；
样品、资格测试和单一孤立 SKU 不构成满足。
```

三者分别回答：

```text
现在在哪里？
↓
需要走到哪里？
↓
如何确认真的走到了？
```

这是 O3 Calibration 最核心的认知结构。

---

# 16. Step 7 — 编译 Activation Condition

每个 Pending Condition 的 canonical 表达：

```json
{
  "condition_id": "C1",
  "criterion": "...",
  "calibration": {
    "reference_state": "...",
    "trigger_boundary": "...",
    "qualifying_evidence": "..."
  }
}
```

---

## `criterion`

定义：

> 实时判定时真正需要判断是否成立的条件。

要求明显短于 Calibration。

例如：

```text
Tier-1 OEM 将 CXMT DRAM 从当前有限采用扩大至
多个主流量产产品的重复规模采购。
```

Calibration 解释：

> 为什么“扩大至这一状态”才算满足。

---

# 17. Step 8 — 优先 Single-Observation，但不牺牲 Alpha

O3 首先尝试：

```text
C1
→ TRADE
```

如果必须把 Policy 推迟到一个明显更晚的状态才能实现单条件，并因此损失合理的早期交易价值，则允许：

```text
C1 + C2
→ TRADE
```

必要时：

```text
C1 + C2 + C3
```

原则：

> **使用足以支持直接交易的最少条件，而不是为了单条件而选择最晚的证明点。**

多条件 Policy 的全部条件采用：

```text
AND
```

语义。

不引入复杂的 Boolean DSL。

如果两个路径是：

```text
A OR B
```

优先拆成两条 Policy。

---

# 18. Step 9 — Policy Compilation

当 tradable path 已经完成 Calibration 后，生成 Policy Draft：

```text
output/work/policies/{policy_id}.json
```

写入之后才算该 path 已编译完成。

---

# 19. 同 Wave 内即时 Canonicalization

每创建一个 Policy 前，先查看 Working Policy Registry：

> 是否已经存在一个现实 occurrence、Activation Boundary 和 Direction 基本相同的 Policy？

如果存在：

```text
追加 source_refs
```

而不是重复创建。

因此 duplicate handling 不是最后才进行。

---

# 20. Checkpoint C — Wave Completion

每个 wave 完成后更新：

```text
output/work/wave_state.json
```

例如：

```json
{
  "completed_wave": 3,
  "processed_gap_ids": ["...", "..."],
  "compiled_policy_ids": ["...", "..."],
  "unresolved_path_ids": [],
  "next_wave": 4
}
```

作用：

1. 强迫 O3 显式结束一个 wave；
2. 中途失败后可以从最后一个完成 wave 继续；
3. 防止后半段因为上下文压力跳过 Gap；
4. deterministic validator 能确认 worklist 与 policy draft 对应关系。

---

# 21. Final Global Pass

全部 waves 完成后，启动一个独立的 O3 Global Check Turn。

这是一个轻量化的检查节点，注入独立的 internal skill，负责完成一次全局检查和必要调优：

```text
是否有 Gap 未进入 Worklist？
是否有 path 未编译且没有 unresolved 说明？
是否有明显重复 Policy？
是否存在互相矛盾的 Activation Standard？
是否存在已经由 Reference View 明确成为历史事实的 trigger？
```

如发现问题，只进行必要的 Policy 调整、去重或条件修正，不重新开展研究。

该 Turn 不创建 Reviewer Agent，也不拆分为更复杂的 Agent 流程。

---

# 22. O3_INITIALIZE 的最终过程结构

```text
D2
+
optional Reference View
        ↓
O3_INITIALIZE
        │
        ├─ Understand Unit
        │
        ├─ Surface Tradable Paths
        │      ↓
        │   CHECKPOINT A
        │   worklist.jsonl
        │
        ├─ Identify Missing Calibration
        │
        ├─ Light Current-State Check
        │
        ├─ Targeted Research if needed
        │      ↓
        │   CHECKPOINT B
        │   calibration_log.jsonl
        │
        ├─ Calibrate
        │
        ├─ Compile Policy
        │
        ├─ Compare Working Registry
        │
        └─ finish wave
               ↓
            CHECKPOINT C
            wave_state.json

        ... next wave ...

        ↓
Final Global Pass
        ↓
small O3RunResult
```

---

# 23. Canonical Policy Schema

正式业务状态只维护一份 Policy。

Runtime artifact 从 canonical Policy 中确定性投影。

---

## Policy

```json
{
  "policy_id": "pol_xxx",
  "title": "...",

  "source_refs": [
    {
      "expectation_id": "...",
      "gap_id": "..."
    }
  ],

  "decision": "LONG",

  "match_scope": "...",

  "activation_conditions": [
    {
      "condition_id": "C1",
      "criterion": "...",
      "calibration": {
        "reference_state": "...",
        "trigger_boundary": "...",
        "qualifying_evidence": "..."
      }
    }
  ],

  "activation_summary": "..."
}
```

---

# 24. Policy 字段定义

## `policy_id`

稳定 Policy 身份。

用途：

* 每日维护；
* Policy Set version 间追踪；
  -历史恢复。

如果只发生：

```text
condition removal
calibration update
wording refinement
```

保持 `policy_id`。

交易含义实质改变时才创建新 ID。

---

## `title`

人类可读名称。

例如：

```text
CXMT DRAM规模采用扩张
```

要求：

* 中文；
* 简短；
  -描述现实触发主题；
  -不写成长句。

主要用于：

* Dashboard；
  -人工检查；
  -日志；
  -调试。

---

## `source_refs`

说明 Policy 来自哪个 D2 研究对象。

结构：

```text
expectation_id
gap_id
```

允许：

```text
one policy
← multiple gaps
← multiple units
```

暂不加入：

```text
factor_ids
derivation
shell_id
```

除非未来确认 `expectation_id` 并非稳定全局唯一。

---

## `decision`

Policy 满足后的预定方向：

```text
LONG
SHORT
```

当前不加入：

```text
severity
confidence
urgency
position_size
```

---

# 25. `match_scope`

定义：

> **什么类型的新消息可能与这条 Policy 有关，应进入候选召回。**

它不是 Activation Condition。

例如：

```text
涉及 CXMT DRAM 客户资格、OEM采用、
量产产品、商业采购、供货规模或海外市场扩张的消息。
```

这个字段允许写得比 Activation Condition 宽。

作用：

```text
message
↓
Policy candidate recall
```

防止实时系统必须先读全部复杂 Calibration 才能找到候选 Policy。

---

# 26. `activation_conditions`

真正执行标准。

单条件：

```text
[C1]
```

多条件：

```text
[C1, C2]
```

默认语义：

```text
ALL conditions required
```

不再增加：

```text
condition_logic = AND
```

因为属于固定业务合同。

---

# 27. `condition_id`

稳定的 Policy 内条件标识。

例如：

```text
C1
C2
```

用途：

-每日 O3 移除已经变成现实的条件；
-未来运行状态引用；
-人工审查。

---

# 28. `criterion`

给实际判定使用的条件描述。

要求：

* 中文；
  -描述现实状态；
  -尽量单义；
  -不重新解释 thesis；
  -不使用宽泛 `material / significant`，除非 Calibration 已明确它的业务含义。

---

# 29. `calibration.reference_state`

记录当前起点。

回答：

> **目前已经成立什么？**

它让下一次 O3 Maintenance 能知道：

> Policy 是基于什么状态写出来的。

---

# 30. `calibration.trigger_boundary`

回答：

> **相比当前状态，需要发生什么变化才能进入交易状态？**

这是 O3 Calibration Research 最重要的结果。

---

# 31. `calibration.qualifying_evidence`

回答：

> **什么证据足以证明 trigger boundary 已经发生？**

它关注事实确认标准，不规定：

* 去哪里监控；
* 搜索什么关键词；
* 使用什么数据源。

这些属于后续 Monitoring Configuration。

---

# 32. `activation_summary`

Policy 的紧凑运行表达。

例如：

```text
当 Tier-1 OEM 将 CXMT DRAM 从当前有限采用
扩大至主流产品的重复规模采购时做空 MU。
```

用途：

1. 人类快速阅读；
2. Runtime compact projection；
3. candidate recall 的辅助语义。

它不替代：

```text
match_scope
activation_conditions
calibration
```

---

# 33. 为什么同时保留 `match_scope` 和 `activation_summary`

二者语义不同。

### `match_scope`

回答：

> 什么消息值得把这条 Policy 找出来？

宽。

### `activation_summary`

回答：

> 这条 Policy 到底什么时候成立？

窄。

例如：

```text
match_scope:
CXMT qualification、OEM adoption、
commercial shipment、海外扩张相关消息
```

而：

```text
activation_summary:
CXMT 从当前有限采用进入 Tier-1 OEM
主流产品重复规模采购时做空 MU。
```

这样更适合未来高召回 Policy retrieval。

---

# 34. Policy Set Schema

公共 metadata 不重复写进每条 Policy。

```json
{
  "schema_version": "...",
  "ticker": "MU",
  "policy_set_version": 12,
  "document2_ref": "...",
  "event_library_ref": "...",
  "policies": []
}
```

---

## `schema_version`

D3 schema 版本。

---

## `ticker`

目标股票。

---

## `policy_set_version`

每次 Published Policy Set 单调递增。

---

## `document2_ref`

该 Policy Set 基于哪个 Published D2。

---

## `event_library_ref`

本次初始化或维护参考的 Event Library Reference View 版本。

---

## `policies`

当前全部 Active Policies。

Retired Policy 通过历史 Policy Set 保留，不继续进入当前集合。

---

# 35. Runtime Projection

Runtime 不维护独立业务表。

从 Policy Set 中确定性抽取：

```text
policy_id
title
decision
match_scope
condition_id
criterion
activation_summary
```

Calibration 不需要默认进入紧凑 Runtime Projection。

必要时未来消费者可以按 `policy_id` 读取完整 Policy。

Canonical source 永远只有：

```text
Policy Set
```

---

# 36. Coverage Map

最终额外生成：

```text
coverage_map.json
```

关系：

```text
Expectation
→ Gap
→ path
→ Policy
```

这是过程/审查 artifact，不属于 Policy schema。

作用：

> Potential Gap 不能在编译过程中静默消失。

一个 Gap 可以：

```text
→ Policy A
→ Policy B
```

多个 Gap 也可以：

```text
Gap A ┐
      ├→ Policy X
Gap B ┘
```

---

# 37. O3_MAINTAIN

与初始化完全分离。

每日盘前运行：

```text
Current Published Policy Set
+
Latest Reference Event View
        ↓
O3_MAINTAIN
one lightweight turn
        ↓
Policy Patch Set
        ↓
deterministic apply / validate
        ↓
new Published Policy Set
```

不重新输入完整 D2。

---

# 38. 每日 Maintenance 的思考方式

## Step 1 — 快速扫描全部 Policy

读取：

```text
当前 Policy
+
新的 Reference View
```

判断：

```text
NO_CHANGE
POSSIBLE_CHANGE
```

绝大多数应该是：

```text
NO_CHANGE
```

不输出详细分析。

---

# 39. Checkpoint — `maintenance_candidates.jsonl`

只记录：

```text
POSSIBLE_CHANGE
```

例如：

```json
{
  "policy_id": "pol_xxx",
  "reason": "Reference View显示C1可能已经成为现实"
}
```

没有变化的数百条 Policy 不逐条写过程日志。

这样保持 Daily O3 轻量。

---

# 40. Step 2 — 只研究 Possible Change

如果 Reference View 已经足够：

> 直接修改。

如果不足：

> targeted Web Search。

只有 Policy 依赖的对象属于高频结构化数据，并且从上次 review 至今确实可能更新时：

> 才调用 Data MCP。

---

# 41. Step 3 — 已满足条件直接移除

例如现行 Policy：

```text
C1
C2
```

最新事实确认：

```text
C1 已发生
```

新 Policy 变为：

```text
C2
```

保持：

```text
policy_id
```

不再要求未来运行重复判断已经成为历史事实的 C1。

---

# 42. Step 4 — Reality 已经推进时更新 Calibration

例如：

```text
reference_state:
qualification pending
```

现在已经变成：

```text
qualification completed
```

则 O3 将：

```text
reference_state
trigger_boundary
criterion
activation_summary
```

推进到下一个仍然具有交易意义的状态。

---

# 43. Daily Patch Schema

Daily O3 不重写整个 Policy Set。

只输出发生改变的内容。

进一步按最小字段原则，Patch 不设计复杂 operation DSL。

```json
{
  "base_policy_set_version": 12,
  "event_library_ref": "...",

  "upsert_policies": [],
  "retire_policy_ids": []
}
```

---

## `upsert_policies`

包含：

* 新 Policy；
* 修改后的完整 Policy。

例如移除 C1 后：

> 直接输出修改后的完整 Policy。

这样不需要：

```text
REMOVE_CONDITION
UPDATE_CALIBRATION
UPDATE_SUMMARY
UPDATE_SCOPE
```

等大量 patch operation。

---

## `retire_policy_ids`

现实状态使某条 Policy 完全失效时，将 ID 放入此数组。

---

# 44. O3_MAINTAIN 不做什么

它不：

-重新读取完整 D2；
-重新遍历所有 Potential Gaps；
-重新建立 Policy Surface；
-系统性重新搜索所有 Calibration；
-重新生成所有 Policy；
-因为一天过去就更新所有 reference state。

它是：

> **变化驱动的 Policy maintenance。**

---

# 45. D1 / D2 / D3 Rebase

本轮不独立实现一个 `O3_REBASE` Agent。

后续整体设计：

```text
D1 Research Refresh
    ↓
D2 Expectation Update
    ↓
D3 Policy Rebase
```

本轮只保证 D3 已具备未来 Rebase 所需的基础：

```text
stable policy_id
source_refs
policy_set_version
document2_ref
```

不提前定义完整 Rebase 策略。

---

# 46. Workspace 结构

建议：

```text
output/
├── work/
│   ├── worklist.jsonl
│   ├── calibration_log.jsonl
│   ├── wave_state.json
│   ├── maintenance_candidates.jsonl
│   └── policies/
│       ├── pol_xxx.json
│       └── ...
│
└── final/
    ├── document3.json
    ├── coverage_map.json
    └── document3.md
```

Initial 与 Maintenance run 使用各自 run workspace，因此不会同时产生所有 work 文件。

---

# 47. O3 最终返回值

模型不返回完整 Policy Set。

只返回一个小型：

```text
O3RunResult
```

例如：

```json
{
  "status": "COMPLETED",
  "processed_gap_count": 112,
  "policy_count": 164,
  "unresolved_path_count": 0
}
```

真实业务产物以 workspace 文件为准。

这与当前 O2 已采用的“模型在 workspace 完成完整 revision，最终只返回小型 RunResult”的模式一致。

---

# 48. Deterministic Validation

Validation 保持必要而轻量。

## Initial Build

必须检查：

### Worklist Coverage

每个 D2 Gap 至少有一个 Worklist entry。

### Path Completion

每个 path 必须：

```text
→ Policy
```

或：

```text
→ unresolved
```

不能静默消失。

### Calibration Discipline

`d2_boundary_sufficient = false` 的 path 必须存在：

```text
calibration_log
```

### Policy Schema

所有 Policy 字段完整。

### Condition Calibration

每个 condition 必须同时有：

```text
reference_state
trigger_boundary
qualifying_evidence
```

### Direction

必须：

```text
LONG
or
SHORT
```

### Duplicate

不得存在明显相同：

```text
现实状态
+
Activation Boundary
+
Direction
```

的重复 Policy。

### Chinese Output

`title`、`match_scope`、`criterion`、Calibration 和 `activation_summary` 应以中文为主体；专有名词和缩写除外。

---

# 49. Maintenance Validation

检查：

```text
base_policy_set_version
```

必须等于当前 Published version。

所有：

```text
upsert_policy.policy_id
```

要么已经存在，要么是合法新 ID。

被 retire 的 ID 必须存在。

新的 Policy Set 重新执行标准 Policy schema validation。

---

# 50. Prompt / Skill 结构

```text
prompts/codex_v2/document3/
├── agents/
│   └── o3.md
│
├── skills/
│   ├── foundation.md
│   ├── initialize.md
│   └── maintain.md
│
└── schemas/
    ├── policy_set.schema.json
    └── policy_patch.schema.json
```

不再拆：

```text
surface.md
reconcile.md
compile.md
finalize.md
```

因为它们并不是独立 Agent 阶段。

---

# 51. `o3.md`

只定义身份和完成目标：

> O3 负责将 D2 已研究完成的未来 expectation changes 编译并维护为 Direct Trading Policies，使后续实时系统可以依据预先制定的标准完成低自由度判断。

保持很短。

---

# 52. `foundation.md`

只保存初始化和维护共同使用的语义：

```text
Potential Gap
Tradable Path
Policy
Activation Condition
Calibration
Single / Multi-condition
Policy canonicalization
Current state
```

以及全局：

> 所有自然语言输出使用中文，字段名、专有名词和行业缩写除外。

---

# 53. `initialize.md`

这是整个 D3 Prompt 体系最重要的 Skill。

它需要重点控制 **O3 的思考方式**，而不是堆大量禁止项。

核心执行逻辑：

```text
先理解 Unit
↓
为 Gap 建立 Tradable Path
↓
先写 Worklist checkpoint
↓
判断 D2 是否已经足够
↓
明确缺少的 Calibration
↓
优先利用 D2
↓
再利用 Reference View
↓
只针对明确缺口做 Search
↓
必要时 Data MCP
↓
写 Calibration checkpoint
↓
建立 Activation Condition
↓
优先最少条件而不牺牲 alpha
↓
写 Policy
↓
与已有 Registry 比较
↓
完成 Wave checkpoint
```

重点是：

> **Checkpoint 是 workflow 的组成，而不是审计附属物。**

---

# 54. `initialize_final_review.md`

这是初始化流程最后一个整体检查 turn 使用的 internal skill。

它不重新研究 D2，也不重新执行各个 wave 的编译工作，而是读取已经生成的：

```text
worklist.jsonl
calibration_log.jsonl
wave_state.json
policies/
coverage_map.json
```

并对最终结果做一次全局一致性检查。

核心检查逻辑：

```text
读取全部 Worklist
↓
确认每个 Gap 都有对应 Path
↓
确认每个 Path 都已编译或明确 unresolved
↓
读取全部 Policy Draft
↓
检查明显重复或互相矛盾的 Policy
↓
检查 Calibration 是否完整且可执行
↓
检查 Policy 是否过度多条件化
↓
检查是否有条件已经成为现实但仍被保留
↓
检查中文输出和 schema 完整性
↓
写入最终 Review Result
```

这个 turn 只负责发现和记录问题，不重新展开大规模 Web Search 或 Data MCP。

如果发现可以确定性修复的问题，例如：

```text
字段缺失
格式错误
明显重复
coverage_map 漏项
```

则直接写入 review result，交给后续 deterministic validator 或 assembly 阶段处理。

如果发现需要重新进行实质性研究的问题，则记录为：

```json
{
  "status": "REVIEW_BLOCKED",
  "reason": "...",
  "affected_policy_ids": ["..."],
  "requires_research": true
}
```

Internal skill 的核心要求是：

> **Final Review 只做全局一致性检查，不重新发明 Policy，也不因为局部措辞问题重新启动完整初始化流程。**

最终返回一个小型结果：

```json
{
  "status": "PASSED",
  "issue_count": 0,
  "blocking_issue_count": 0
}
```

或：

```json
{
  "status": "REVIEW_BLOCKED",
  "issue_count": 2,
  "blocking_issue_count": 1
}
```

---

# 54. `maintain.md`

只定义：

```text
读取当前 Policy
+
新增 Reference View
↓
找 Possible Changes
↓
写 maintenance_candidates
↓
只调查这些变化
↓
更新 / 推进 / retire Policy
↓
输出 Patch
```

不继承初始化阶段的完整研究工作流。

---

# 55. 开发实现顺序

## Phase 1 — Schema

实现：

```text
Policy
PolicySet
PolicyPatchSet
O3RunResult
```

---

## Phase 2 — Initial Input Preparation

从 Published D2 确定性生成：

```text
Unit input
Gap inventory
Reference View
Previous Policy Set
```

---

## Phase 3 — O3_INITIALIZE

实现：

```text
single Codex turn
multi-wave workspace workflow
```

支持：

```text
worklist checkpoint
calibration checkpoint
wave checkpoint
progressive policy writes
```

---

## Phase 4 — Initial Validator / Publisher

校验：

```text
gap coverage
path completion
calibration completeness
policy schema
duplicate policy
Chinese output
```

然后发布 Policy Set。

---

## Phase 5 — O3_MAINTAIN

实现单 turn：

```text
Policy Set
+
Reference View Delta
→
PolicyPatchSet
```

---

## Phase 6 — Patch Apply / Publish

确定性：

```text
Current Policy Set
+
Patch
↓
New Policy Set
↓
validation
↓
publish
```

---

## Phase 7 — Pilot

不要首先用人工制造的简单 Gap。

直接使用真实 D2。

重点测试：

### Agent 是否真的建立 Worklist

而不是直接输出 Policy。

### Search 是否只围绕明确 Calibration 缺口

观察是否再次出现“为了放心”大范围搜索。

### Data MCP 调用是否有明确必要性

避免重复上游 research。

### 后部 Wave 是否质量下降

比较 Wave 1 与最终 Wave。

### Calibration 是否足够具体

重点检查：

```text
reference_state
trigger_boundary
qualifying_evidence
```

是否真的回答三个不同问题。

### Policy 是否过度多条件化

统计：

```text
single-condition ratio
2-condition ratio
3+ condition ratio
```

不是规定硬 KPI，而是识别 O3 是否滥用多条件。

### Daily Maintenance 是否真正轻量

验证绝大部分没有变化的 Policy 不产生 patch。

---

# 56. 当前最终架构

```text
                    LOW FREQUENCY

Published D2
+
optional Reference View
        ↓
     O3_INITIALIZE
     one Agent turn
     multiple waves
        ↓
 Worklist Checkpoint
        ↓
 Calibration Checkpoint
        ↓
 Policy Drafts
        ↓
 Wave Checkpoints
        ↓
deterministic validation
        ↓
Published Policy Set
```

每日：

```text
                     HIGH FREQUENCY

Published Policy Set
+
New Reference View
        ↓
      O3_MAINTAIN
      one light turn
        ↓
Possible-Change Checkpoint
        ↓
targeted research if needed
        ↓
Policy Patch Set
        ↓
deterministic apply
        ↓
New Published Policy Set
```

---

# 57. 最终设计原则

整个 D3 V2 的核心可以收敛为：

> **D2 负责把未来可能改变 expectation 的现实空间研究清楚；O3 负责把这些未来变化校准成预先规定的直接交易判断标准。**

O3 初始化允许重研究，但所有研究必须服务于一个已经明确的：

> **Policy Calibration 缺口。**

O3 日常维护不重复研究，只维护：

> **Policy 与已经变化的现实之间的一致性。**

而整个设计最重要的可靠性原则是：

> **复杂性尽量在低频 O3 阶段提前消化；通过 Worklist、Calibration Log 和 Wave State 三个轻量过程 checkpoint，强迫 Agent 展示必要的中间结论，再允许其形成最终 Policy。**

这样既保留一个连续 Research/Compile turn 的上下文优势，也降低 Codex 直接跳过中间思考、批量生成表面合理 Policy 的概率。
