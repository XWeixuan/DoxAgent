# CDECR Bulk Epoch V3 召回与碎片化受限收敛优化方案

> 日期：2026-08-03
> 方案状态：待实施；本文件仅定义下一轮修改，不在本轮调用真实模型
> 依据：`CDECR_BULK_EPOCH_V3_STAGE_GRAPH_30_REAL_AB_ACCEPTANCE_REPORT_20260803.md`、R3 Registry 与真实 Gold 评估产物
> 核心约束：保留 Stage Graph V3 的并发、冻结快照、字典 payload、全局任务去重和单 writer 架构；不以显著增加 Token 或墙钟时间换取质量

## 0. 结论先行

本轮不回滚也不弱化以下已经证实有效的架构：

- `BULK_EPOCH_V3_STAGE_GRAPH` one-shot 阶段图；
- 文档 Map、Field、N9、N12、N13 的跨文档并行；
- M1/M2/M3/M4 物理 lane `32/48/48/16` 与 N9/N12/N13 各 24 个活动请求；
- Field 全局任务去重、冻结 Atomic/Package snapshot；
- N9/N12/N13 dictionary payload、短 ID、Card 复用；
- content-addressed artifact、task ledger、幂等恢复；
- SQLite 进程内短事务单写门与最终单 writer Apply。

下一轮应采用一个明确而克制的策略：

> **首轮继续极致并行；只对首轮留下的高收益残差做一次固定、受预算约束的 Late Convergence；先复用已付费的 SAME/MEMBER 判断，再对极少量歧义残差发起紧凑 LLM 请求。**

方案不是重新执行完整 N9/N12/N13，也不是恢复顺序可见状态，更不是做全图递归闭包。它只新增三个尾部动作：

1. 让非法/不可判结果保持中性，并避免局部业务负边被组件级过度放大；
2. 将已经得到的 N9/N13 正向判断在受限边界内真正 Apply；
3. 对 N9 `RELATED_NOT_SAME` 与 N12 Package fragment 做一次小规模尾部复判和组件收敛。

目标是以总 Input Token 增加 **3%–6%**、总墙钟增加 **5%–10%** 为正常预算，争取：

- N9 conditional MERGE Recall 从 69.70% 恢复到至少 80%；
- Package Pair Recall 从 65.18% 恢复到 75%–80%；
- fragmented Gold groups 从 8 降至不超过 4–5；
- excess components 从 16 降至不超过 8；
- Package Precision 允许从 98.94% 小幅下降，但目标保持不低于 94%；
- Stage Graph 相对旧 R2 的 Token 与耗时数量级收益必须继续成立。

## 1. 事实基线与问题边界

### 1.1 本轮已经成立的效能收益

固定 30 篇真实验收中：

| 指标 | R2 | Stage Graph V3 R3 | 变化 |
| --- | ---: | ---: | ---: |
| 技术成功率 | 30/30 | 30/30 | 持平 |
| 总墙钟 | 12,275,833 ms | 2,726,440 ms | **-77.79%** |
| 模型调用数 | 992 | 816 | -17.74% |
| Input Token | 3,772,039 | 1,582,413 | **-58.05%** |
| Output Token | 2,661,038 | 2,174,729 | -18.28% |
| 总 Token | 6,433,077 | 3,757,142 | **-41.60%** |
| N12 Input | 1,751,219 | 161,861 | **-90.76%** |
| N13 Input | 708,000 | 225,061 | **-68.21%** |

因此，下一轮任何方案只要需要恢复逐文档顺序状态、重新发送全文、对全部 pair 做第二轮判断，或者降低当前并发，都视为方向错误。

### 1.2 当前质量回归不是候选召回不足

N9 candidate coverage 已经达到 100%。66 个可判 SAME opportunity 中，模型/Apply 只成功合并 46 个，漏 20 个；20 个漏合全部以 `N9_RELATED_CREATE_NEW` 结束，而不是没有候选。

这说明 N9 的主要矛盾已经从：

```text
找不到正确候选
```

变成：

```text
正确候选已在输入中
→ 模型把 SAME 判断成 RELATED_NOT_SAME
→ 或正向候选没有在 Apply 层完成收敛
```

N13 同样如此：444 个 pair 中已有 61 个 `SAME_PACKAGE`，但最终只 Apply 1 个 MergePlan；46 个 SAME 被 `SAME_PACKAGE_NOT_APPLIED_WEAK_BOUNDARY` 拦截。

### 1.3 真实残差结构

对 R3 Registry 的 46 个 N13 未 Apply SAME 做只读统计：

| 结构 | 数量 |
| --- | ---: |
| 至少一侧 Package 成员数 ≤ 3 | **46/46** |
| 两侧成员数均 ≤ 3 | 39/46 |
| 无 reaction boundary | 42/46 |
| 相同 Package family | 34/46 |
| 至少一侧 `anchor_conflict=true` | 6/46 |
| 共享任意 canonical anchor | 4/46 |

这非常适合“主簇吸收小碎片”的受限策略，不需要任意 cluster ↔ cluster 闭包。

在当前 96-Atomic Gold 可判范围内，46 个弱 SAME 中可直接由现有错误分组判定 11 个：10 个 Gold SAME，1 个 Gold DIFFERENT，其余 35 个不能从现有 Gold 覆盖安全宣称。可判子集的弱 SAME 命中率为 90.91%，说明：

- 全量禁用 weak SAME Apply 明显过于保守；
- 全量无条件 Apply 也不合理；
- 应通过 pair 级父边界与组件纯度条件放行高收益子集。

### 1.4 两个最关键的结构性根因

#### 根因 A：Package 级 `anchor_conflict` 被当成 pair 级否定

Micron FY2026 Q3 earnings 主 Package 有 63 个成员、10 个聚合 anchor，因此：

```text
anchor_conflict = true
primary_anchor_id = null
```

但它与 2-member capex/FCF fragment：

- 共享 canonical earnings anchor；
- 共享 2026-06-24 parent context；
- 共享 source member 与 issuer；
- N13 已明确输出 SAME_PACKAGE；
- Gold 也判定同属 `G_MICRON_FQ3_2026_EARNINGS`。

当前 Apply 仍因为主 Package 的全局 `anchor_conflict=true` 一票否决。这里的错误不是模型没判断出来，而是一个 Package 聚合属性被错误提升成每一条 pair 的硬冲突。

#### 根因 B：不可判任务没有后续收敛机会，合法负边又被组件级放大

N12 adapter 在 task 缺失、重复或 candidate coverage 非法时，会先构造临时占位：

```json
{"relation":"NOT_RELATED","reason":"N12_INVALID_TASK_CREATE_NEW_PACKAGE"}
```

当前实现随后会对该 task 做 item repair；repair 再失败时，临时占位不会进入最终 Wave B decisions，因此不能把本轮碎片化简单归因为“7 条非法 task 向 `_constrained_components()` 注入了负边”。真实问题是：

1. repair 失败 task 没有有效正边，直接成为 singleton；
2. assignment commit 仍把这种“不可判 singleton”记为 `relation=NOT_RELATED`，语义上混淆技术失败和业务否定；
3. Wave B 后没有组件级第二次收敛机会；
4. 对于模型合法输出的 `NOT_RELATED`，`_constrained_components()` 会把任一 pair 负边提升为跨组件 union veto，单个局部误判仍可能覆盖多条 MEMBER 正边。

因此下一轮要同时做两件事：让技术不可判保持中性，并把合法 NOT_RELATED 分成真正的硬边界与可被更强父证据覆盖的软否定。

## 2. 不可协商的设计原则

### 2.1 保留效能架构

- 主 Stage Graph 与 stage barrier 不变；
- 各节点首轮输入、候选 plan 与冻结 snapshot 语义不变；
- 新增尾部阶段只能读取既有 artifact/decision/card，不重新读取全文；
- 新 LLM 调用必须复用当前异步执行器、限流器与字典协议；
- Apply 仍由一个确定性 reducer 生成计划，再由单 writer 短事务提交；
- 不降低当前 lane 和节点活动请求上限。

### 2.2 激进，但不是无边界

本方案允许小幅 Precision 下降，以换取明显 Recall 和碎片化改善；但“激进”具体指：

- 缺失可选信息不再默认判不同；
- 模型已经明确 SAME 时，默认倾向 Apply；
- Package 级模糊状态不再自动覆盖 pair 级强证据；
- 单条软 NOT_RELATED 不再否决多条正向支持；
- 小碎片允许被大主簇吸收；
- 同一 hub 可以在一轮中吸收多个独立合格小碎片。

它不意味着：

- 同公司、同季度、同主题即可合并；
- N9 可以把同一次财报里的 revenue/EPS/capex 合成一个 Atomic；
- reaction、analyst report、独立 artifact 可被并入 disclosure；
- 新合并结果可以继续触发无限传递合并。

### 2.3 一轮固定收敛

所有 Late Convergence 都必须满足：

```text
Plan once
→ Decide once
→ Reduce once
→ Commit once
```

禁止：

- while-loop convergence；
- 合并后重新召回；
- Wave C 后再生成 Wave D；
- 因局部失败退回完整首轮；
- 在同一轮内让新组件反复扩大候选邻域。

### 2.4 审计服从业务成功

只记录编排层短审计：

- source/target ID；
- evidence route；
- hard/soft boundary；
- decision source；
- Apply 或不 Apply 的规则码；
- 合并前后成员数；
- Token、latency 与 residual budget。

不新增模型 reasoning 字段，不要求模型输出置信度，不扩大 Schema。

## 3. 目标 Stage Graph

目标执行图如下：

```text
Stage 1  Document Map（保持）
  Dreamer / Grounder / Judge
            ↓
  Missing-candidate item recovery（仅技术残差）
            ↓ barrier

Stage 2  Global Field（保持）
            ↓ barrier

Stage 3  N9 frozen-plan Decide（保持）
            ↓
  N9 initial constrained Apply（保持）
            ↓
  N9 bounded late convergence（新增，一轮）
            ↓ barrier

Stage 4  N12 Wave A / Wave B（保持）
            ↓
  Neutral invalid-task reduction（修正）
            ↓
  N12 component Wave C（新增，一轮）
            ↓ barrier

Stage 5  N13 full bounded pair Decide（保持）
            ↓
  Pair-local SAME eligibility（替代全局弱边界）
            ↓
  Hub-and-spoke final reduce（受限放宽）
            ↓
  Boundary Gate / Commit（保持）
```

新增阶段全部位于现有 barrier 内部，不改变跨 stage 的业务顺序。

## 4. P0：修正技术失败与负向边语义

### 4.1 N12 invalid task 从临时占位到最终记录都保持中性

当前代码落点：

- `src/cdecr/cross_document.py`：N12 `adapt_and_audit()`；
- `src/cdecr/bulk_epoch/package_stage.py`：`_constrained_components()`；
- `src/cdecr/bulk_epoch/engine.py`：N12 ledger 状态。

修改后使用已有 `UNCERTAIN` 业务关系和编排层失败码表达中性状态：

```text
UNCERTAIN / NO_DECISION / UNJUDGEABLE_FAILED
```

不需要增加模型 Schema。非法 candidate assessment 不进入最终 `candidate_assessments`，错误信息单独进入 task ledger/audit；最终 singleton assignment 使用 `relation=UNCERTAIN`，不再写成 `NOT_RELATED`。

处理顺序：

1. 先规范化安全的 enum 大小写、短 ID、重复相同 assessment；
2. 保持当前 item-local repair 和失败后丢弃临时占位的正确行为；
3. 可安全保留的合法 assessment 不因同 task 其他 candidate 非法而丢失；
4. 如果仍有合法 MEMBER，重新计算 selected target；
5. 如果没有 MEMBER 但有 EXTERNAL_RELATED，保留外部关系并 CREATE_NEW；
6. 如果全部不可判，形成 singleton，assignment relation 记为 UNCERTAIN；
7. repair 仍并行且只修非法 task，不修整个 batch；
8. repair 失败 task 进入 Wave C residual 高优先队列，而不是追加第二次 N12 task repair。

### 4.2 N9 格式失败也不得产生 `UNRELATED` 语义

当前 N9 的 missing/duplicate task 会先构造全候选 `UNRELATED` 的临时 conservative decision，再做 item repair；repair 失败后最终 decision 已会被移除并以 singleton 保留。下一轮要把这条中性语义从临时 adapter、assignment 到 late plan 全部对齐：

- adapter 不再构造具有业务含义的 UNRELATED 临时占位，直接登记 task-local `decision=None`；
- 当前 Mention 作为 singleton 保留；
- 不向后续 late-convergence graph 写入负边；
- 如果同一 task 中已有合法 assessment，则保留并据此重算 action；
- 只对无法安全规范化的 assessment 视为无判断；
- 最终 `N9_UNJUDGEABLE_FAILED_SINGLETON` 可进入 N9 late residual，但不能凭失败本身触发合并。

这不会让非法结果自动合并，只是停止把技术失败伪装成业务不同。

### 4.3 将 N12 `explicit_not` 拆成 hard veto 与 soft negative

`_constrained_components()` 当前只有一个 `explicit_not` 集合。修改为：

```python
hard_not_edges: set[Pair]
soft_not_edges: set[Pair]
member_edges: list[SupportedEdge]
```

#### hard veto 来源

- `package_hard_conflicts()` 的结构化冲突；
- 不同 trusted artifact identity；
- 不同 issuer；
- disclosure ↔ market reaction；
- disclosure ↔ independent analyst report；
- 不同 analyst institution/report；
- 明确不兼容的 controlled session/parent occurrence；
- PackageBoundaryGate 的 `BLOCKING_CONFLICT`。

#### soft negative 来源

- 模型合法输出 NOT_RELATED，但没有上述结构化冲突；
- 缺少 anchor、period 或 artifact；
- detail/granularity 不同；
- Package family 不同但存在同一 parent evidence；
- 仅依赖 free-text reason 的“信息不足”。

Reducer 规则：

- hard veto 永远阻止 union；
- 一条 soft negative 不再一票否决；
- 有强 parent edge 时可以覆盖 soft negative；
- 没有强 edge 时，至少两条独立正向支持才能覆盖 soft negative；
- 不解析模型长 reasoning 来制造硬边界，只使用结构化字段与既有规则。

### 4.4 P0 的效能影响

P0 不新增 LLM 请求，只有小规模内存图计算，复杂度保持：

```text
O(V + E)
```

预计 Token 增量为 0，墙钟影响应低于 1%。

## 5. P0：N13 已有 SAME 的 pair-local Apply

### 5.1 当前 Apply 为什么过度保守

当前 N13 只有以下情况被视为 `strong_same`：

```text
SHARED_ATOMIC_EVENT
或
(CANONICAL_ARTIFACT / shared_primary_anchor)
且 Package family 相同
```

并且任一侧 `anchor_conflict=true`、存在 reaction boundary 或不满足 `strong_same`，都会把模型 SAME 记为 `SAME_PACKAGE_NOT_APPLIED_WEAK_BOUNDARY`。

这个口径有三处问题：

1. 只比较 `primary_anchor_id`，忽略完整 `package_anchor_ids` 的交集；
2. `anchor_conflict` 是 Package 聚合状态，却被用作所有 pair 的硬否定；
3. Package family 被当成父事件身份，而 Prompt 明确说明 family 只描述容器类型、不识别具体容器。

### 5.2 新的 pair-local boundary evaluation

为每一个模型输出 `SAME_PACKAGE` 的 pair 计算一次确定性 `PackagePairBoundary`：

```text
shared_trusted_artifact
shared_canonical_anchor_ids
conflicting_trusted_artifacts
issuer_conflict
reaction_boundary
analyst_report_boundary
shared_parent_context
shared_source_member
member_identity_support
time_window_support
representative_identity_compatibility
source_size / target_size
```

`anchor_conflict` 只表示“本 Package 内有多个 anchor”，不直接进入 hard veto。真正的 pair 级 anchor 判断为：

```text
若两侧共享一个可信 anchor：这是正向证据；
若两侧各有唯一且不同的 trusted artifact：这是硬冲突；
若一侧有多个 anchor、另一侧命中其中一个：不能因前者 conflict 而否决；
若没有 anchor：转入 parent-context 或 representative identity 路径。
```

### 5.3 三档 SAME Apply

#### Tier A：直接 Apply

模型已输出 SAME，且满足任一：

- 共享 current Atomic root；
- 共享 trusted canonical artifact；
- `package_anchor_ids` 有交集，并且 issuer 相同、没有 reaction/analyst/trusted-artifact 冲突；
- 一侧 anchor 集包含另一侧唯一可信 anchor，且同时存在 PARENT_CONTEXT 或 SAME_SOURCE_MEMBER。

Tier A 不要求 Package family 相同。

Micron earnings 主簇吸收 capex/FCF 2-member fragment 属于该档。

#### Tier B：受限小碎片 Apply

模型已输出 SAME，没有 hard veto，且：

- 至少一侧成员数不超过 3；
- 至少有两个独立正向 route，其中至少一个必须来自：
  - PARENT_CONTEXT；
  - SAME_SOURCE_MEMBER；
  - MEMBER_IDENTITY；
  - TIME_WINDOW；
- 不能仅凭 CORE_ENTITY + PROPOSITION_EMBEDDING；
- 如果缺少任何 parent anchor，则 representative Atomic 必须在 issuer、action/object、assertion、period/session 上兼容。

Tier B 允许 family 不同，但 family mismatch 会要求更强的 parent/identity 证据。

#### Tier C：保持不 Apply

以下仍保持分开：

- reaction boundary；
- analyst institution/report boundary；
- trusted artifact 冲突；
- issuer 冲突；
- 一侧已经被 BoundaryGate 判为 blocking；
- 只有同公司、embedding、同文章来源等弱信号；
- 缺少 parent evidence，且 representative members 的 assertion/action/object 明显不同。

### 5.4 防止 Apple 已污染 Package 继续扩张

本轮唯一可判的 weak-SAME Gold DIFFERENT 是：

```text
已混合的 IDC/Counterpoint Apple outlook Package
↔ Apple 实际产品涨价 Package
```

N13 只看到 CORE_ENTITY + PROPOSITION_EMBEDDING + SAME_SOURCE_MEMBER，并把“同一文章提到的 Apple 价格事项”误当成同一父披露。

新规则下：

- 没有 shared anchor/artifact/parent context；
- 一侧为 EXPECTED guidance/outlook，另一侧为 ACTUAL price action；
- parent identity 不完整；
- family 还不一致。

因此不能进入 Tier A/B。这里的保护不是恢复保守，而是要求在缺少父锚点时，至少保持代表成员的具体身份兼容。

### 5.5 Hub-and-spoke reducer

N13 不再直接对所有 SAME 构造 unrestricted connected components，而使用固定一轮的 hub-and-spoke 吸收：

1. 每个合格 pair 确定 hub 与 spoke；
2. spoke 优先为成员数较少的一侧；
3. hub 可为任意大小；
4. spoke 成员数正常上限 3，硬上限 4；
5. 每个 spoke 只能被吸收一次；
6. 每个 hub 正常最多吸收 3 个 spoke，硬上限 4；
7. 目标选择使用现有 canonical package sort key；
8. 同一 hub 的多个 spoke 可以同轮一起进入一个 MergePlan；
9. 合并后的 Package 不再在本轮生成新 pair；
10. Apply 前对最终成员集合再执行一次 PackageBoundaryGate。

这样既能处理：

```text
39/63-member earnings hub
  ← 2-member capex/FCF fragment
  ← 1-member commentary fragment
  ← 2/3-member guidance fragment
```

也不会形成小碎片之间无边界的传递闭包。

### 5.6 预期收益

46 个未 Apply SAME 中，全部符合“小侧 ≤ 3”；结构筛选下约 20–30 个可能进入 Tier A/B。实际启用数量由 pair-local hard boundary 决定，而不是固定追求数量。

这一项复用既有 N13 决策：

- 新增 LLM 调用：0；
- 新增 Input/Output Token：0；
- 新增耗时：仅 reducer 与一次合并后 embedding，预计低于总墙钟 1%；
- 是下一轮最高优先级、最高性价比的召回修复。

## 6. P1：N9 Bounded Late Convergence

### 6.1 为什么需要尾部复判

本轮 N9 的 20 个漏合均已召回正确候选，最终统一落为 `N9_RELATED_CREATE_NEW`。真实样式包括：

- 完全相同命题仍被拆开：
  - `Micron's multi-year Strategic Customer Agreements will significantly enhance...`；
  - `Micron's stock crossed $1 trillion in market capitalization...`；
- 同 metric/value/period，只是措辞不同：
  - `record gross margin 84.9%` ↔ `adjusted gross margin reached 84.9%`；
  - FYQ4 86% gross-margin guidance；
- 概括与具体细节：
  - `signalled higher 2027 capex` ↔ `over $40B, ~$20B construction`；
  - `SK Hynix disclosed US Nasdaq listing plan` ↔ `$29B US listing plan`；
- 同一市场发生的不同数值快照：
  - KOSPI +6% ↔ open 涨超 5%；
  - Micron +14% ↔ +11.5%，同日同 session；
- 同一 action/object 的覆盖粒度差异：
  - Apple 多产品涨价 umbrella ↔ MacBook/iPad 具体涨价；
  - SCA introduction ↔ 16 个长期协议。

首轮 N9 Prompt 同时强调最小事实与很多高风险边界，模型在边界不完整时倾向把 SAME 降为 RELATED_NOT_SAME。Late Convergence 应只复判这种高收益残差，而不是扩大首轮 batch。

### 6.2 residual task 进入条件

只有同时满足以下条件才进入 N9 late plan：

1. 首轮 action 为 CREATE_NEW；
2. 至少有一个合法 candidate assessment 为 RELATED_NOT_SAME；
3. candidate 没有 enforced Sidecar/hard-invariant 冲突；
4. incoming singleton 仍是独立 Atomic root；
5. 目标 Atomic root 仍存在且未被 redirect 到不兼容簇；
6. 至少命中一个高收益信号：
   - normalized proposition 完全或近完全一致；
   - referent + primary metric + value + period + assertion 对齐；
   - issuer + action/polarity + object + period/session 对齐；
   - analyst institution + target + rating/action 对齐；
   - 同一 market instrument/session，差异仅为同次发生的 claim value；
   - 新表述是候选同一最小事实的更具体或更简略版本。

以下不进入：

- 只有 CORE_ENTITY 或 embedding 相似；
- 不同 metric/facet；
- 不同 assertion state 且不能证明同一 assertion；
- 不同 action polarity/object；
- 不同 market session/measure；
- 不同 issuer/analyst institution/artifact；
- 同一 earnings container 下的不同子事实。

### 6.3 两级收敛

#### N9-L0：确定性安全收敛

无需 LLM，直接把首轮 singleton 并入目标：

- normalized canonical proposition 完全相同；或
- exact Sidecar signature 相同；或
- primary metric、值、单位、period、assertion 与 principal referent 全部相同，差异仅在非身份属性；
- 且没有 hard invariant。

这可以直接回收至少一部分完全重复漏合，并避免再次付费。

#### N9-L1：紧凑 M3 残差复判

其余残差按每 task 只保留 1–2 个最高支持候选，复用首轮：

- Mention compact card；
- Atomic compact card；
- applicable Sidecar axes；
- 短 ID；
- 既有 `RELATED_NOT_SAME` 关系；
- 一行 `late_signals`，只描述编排层命中的结构化对齐项。

不发送：

- 完整候选池；
- 全文；
- 全部代表 claims；
- recall score 长解释；
- 首轮 reasoning。

请求模型只回答：

```text
SAME_EVENT / RELATED_NOT_SAME
```

Apply 目标由编排层从 pair ID 确定，不让模型再次排序长 ID。

### 6.4 N9-L1 批次与预算

| 项目 | 正常预算 | 硬上限 |
| --- | ---: | ---: |
| residual task | 20–32 | 48 |
| candidate / task | 1 | 2 |
| task / request | 6 | 8 |
| 并发请求 | 8–16 | 24 |
| 收敛轮数 | 1 | 1 |
| Input Token | 20k–35k | 45k |

新请求与其他 late tasks 并行执行，继续走 M3 lane，不降低首轮 N9 的 24-request 活动上限。

### 6.5 N9 late Apply

Late 阶段面对的是首轮已经 materialize 的 Atomic root，因此使用单独的受限 Apply：

- source 必须为 1-member singleton，正常可放宽到 2-member 小簇；
- target 可为任意大小；
- 同一 source 只能合入一个 target；
- 一个 target 可吸收多个独立 source；
- 每条边重新执行 Atomic merge invariant；
- 只允许 incoming → target，不做 target ↔ target union；
- 合并结果不再参与当轮候选生成；
- 在 Package stage 之前完成，因此不需要跨 Package 搬迁。

如果多个 target 被判 SAME，继续使用现有完整身份、非 provisional、证据质量与 canonical sort key 选择唯一 target；其余仅记录 possible duplicate，不在 Late 阶段做 transitive union。

## 7. P1：N12 Component Wave C

### 7.1 Wave B 为什么仍会碎

N12 Wave B 当前逐 Atomic seed 对 provisional singleton Package 做判断。候选召回有两条路径：

1. 任一强父信号：CANONICAL_ARTIFACT、PACKAGE_ANCHOR、PARENT_CONTEXT；
2. 否则必须同时满足：PACKAGE_KIND_FAMILY + CORE_ENTITY + TIME_WINDOW。

这会漏掉三类真实父事件成员：

- anchor 缺失，但同一 source block 已经表达同一 parent occurrence；
- seed 被误编译为不同 kind/family，但实际仍是同一财报或发布的子事实；
- 单个 Atomic card 只能看到局部子事实，无法表示完整 fragment 已经累积的父事件证据。

Micron 是最典型例子：

- 主簇为 `BOUNDED / EARNINGS_DISCLOSURE`；
- capex + shareholder-return 小簇被编译成 `EPISODE / TRANSACTION`；
- 它们实际共享 earnings anchor、日期和 parent context；
- N13 已判断 SAME，但 Apply 被主簇 `anchor_conflict` 拦截。

Qualcomm 四个事实则全部缺 anchor，被拆成 4 个 singleton；单个 Atomic card 很难看出它们共同属于一次有边界的数据中心战略发布。

### 7.2 Wave C 的业务语义

Wave C 比较的是 **首轮 Package fragment 是否属于同一父 Package**，不是比较成员 Atomic 是否相同。

因此：

- revenue、EPS、capex 是不同 Atomic，但可以进入同一 earnings Package；
- CPU 客户、出货时间、custom-chip deal、data-center target 可以是同一战略发布的不同成员；
- market reaction 和 analyst reaction 仍通常在父 Package 外；
- 不同 analyst report、不同 trusted artifact、不同 issuer 仍不能因主题相似而合并。

### 7.3 Component Card

Wave C 不重新发送全部 Atomic/Mention。每个首轮 fragment 只物化一次紧凑 Card：

```json
{
  "id": "p1",
  "kind": "BOUNDED",
  "family": "EARNINGS_DISCLOSURE",
  "issuer": ["e1"],
  "anchors": ["h1", "h2"],
  "primary_anchor": null,
  "anchor_conflict": true,
  "trusted_artifact": null,
  "period": "t1",
  "sources": ["s1", "s2"],
  "relations_to_anchor": ["DISCLOSED_IN"],
  "representatives": ["a1", "a2", "a3"],
  "member_count": 39
}
```

字段约束：

- anchors 最多 4 个：优先共享/可信/支持成员最多的 anchor；
- representatives 最多 3 个：优先父边界信息最丰富，而不是只选 embedding 中心；
- source IDs 请求内字典化；
- issuer/period/artifact 使用短 ID；
- 保留 `anchor_conflict` 供模型理解，但 Prompt 明确它不是自动 DIFFERENT；
- 不加入模型 reasoning、审计 hash 或持久化内部 ID。

### 7.4 Wave C 候选召回

候选只从首轮 fragment 图生成，按以下 route 召回：

#### 强 parent route

- shared trusted artifact；
- shared canonical anchor；
- shared parent-context fingerprint；
- 一个 fragment 的唯一 anchor 被另一侧 anchor 集包含；
- 已有 N12/N13 MEMBER/SAME 正边。

#### 无 anchor 的替代 route

至少满足：

- 同 issuer；
- 同一个 source member 或局部 Evidence parent block；
- period/time 不冲突；
- representative members 共同指向同一 bounded announcement/matter；
- 不存在 reaction/analyst/artifact hard boundary。

与当前 Wave B 相比，PACKAGE_KIND_FAMILY 从候选硬条件降为排序与边界信号。family mismatch 不再阻止候选生成，因为被错误分到 TRANSACTION/PRODUCT_SCIENCE 的财报子事实仍可能属于同一 parent Package。

只有 CORE_ENTITY + embedding 的 pair 不直接进入强候选；若两侧代表 Atomic 是高度近重复的同一 action/object，可进入低优先残差槽。

### 7.5 候选上限与排序

Wave C 不是 116 Package 全 pair。计划器采用固定边数：

| 项目 | 正常预算 | 硬上限 |
| --- | ---: | ---: |
| residual fragment | 20–40 | 64 |
| candidate / fragment | 3 | 4 |
| 全 epoch Wave C pair | 30–48 | 64 |
| pair / request | 8 | 12 |
| 并发请求 | 8–16 | 24 |

排序优先级：

1. shared trusted artifact；
2. shared canonical anchor；
3. parent context + same source；
4. parent context + issuer/period；
5. member identity + same source/period；
6. 仅代表成员高度近重复。

每个无父边界、无正向残差的 singleton 不进入 Wave C，从根源上控制 pair 数。

### 7.6 Wave C 决策与 reducer

Wave C 复用 `PackageMergeWireDecisionBatch` 的 relation 语义，但单独记录 decision source：

```text
N12_WAVE_C_M0
N12_WAVE_C_M3
N12_WAVE_C_REUSED_N13_SAME
```

决策分三层：

1. shared trusted artifact / shared canonical anchor 且无 hard veto：M0 MEMBER；
2. 已有同 profile 的 N13 SAME：复用，不重复请求；
3. 其余紧凑 residual pair：M3 判断 SAME_PARENT / DIFFERENT_PARENT / UNCERTAIN。

为避免引入第二套领域关系，持久化时映射为现有：

- SAME_PARENT → MEMBER；
- DIFFERENT_PARENT → NOT_RELATED；
- UNCERTAIN → 不产生边。

Reducer 同样使用 hub-and-spoke：

- 小 fragment 正常不超过 3 个 Atomic，硬上限 4；
- hub 任意大小；
- 同一 hub 可吸收最多 4 个独立 fragment；
- 允许多个 singleton 选择同一 hub；
- 无既存 hub 时，可从一组小 fragment 中选择最完整者为 hub；
- 不允许两个大型组件合并；
- 不允许新组件再次召回；
- hard veto 仍阻止合并，soft negative 仅降低支持分。

### 7.7 针对真实 bad case 的预期行为

#### Micron FY2026 Q3 earnings

当前 Gold 对齐的 48 Atomic 被拆为：

```text
39 + 2 + 2 + 2 + 1 + 1 + 1
```

Wave C 应优先收敛：

- earnings hub ↔ capex/FCF-return fragment；
- earnings hub ↔ supply-constraint fragment；
- earnings hub ↔ AI-demand guidance fragment；
- earnings hub ↔ management commentary fragment。

目标不是强行变成单一组件；若某 fragment 有独立 partnership、analyst 或 reaction artifact，应继续分开。合理目标是从 7 个组件降至 2–4 个，而不是为测试集写死成 1 个。

#### Qualcomm data-center strategy

当前 4 个 singleton：

- Meta 为首个 CPU 客户；
- Dragonfly C1000 计划 2028 向 Meta 出货；
- 两个 hyperscale custom-chip deal；
- 2029 non-handset/data-center revenue target。

前两项具有很强的同 announcement/parent 证据，应该优先合并；后两项是否同一父发布由 Wave C component card 判断。目标是 4 个组件降至 1–2 个，而不是因为都属于 Qualcomm 战略主题就无条件聚合。

#### 小型市场事实组

KOSPI +6% 与开盘涨超 5%、SK Hynix +13% 与早盘近 +12%、Micron pre-market +16% 与同日 +11.5% 需要区分：

- Atomic 层是否同 occurrence 由 session/time/measure 判断；
- Package 层允许同一有边界市场发生容纳不同报道值；
- 明确 pre-market、regular session、close、after-hours 冲突仍是 hard boundary；
- 只有同日不足以自动聚合。

### 7.8 Wave C Token 预算

Wave C 复用 Component Card 与短 ID，预计：

- 新增 4–8 个 M3 请求；
- Input 30k–50k，硬上限 65k；
- Output 20k–45k，禁止长 reasoning；
- aggregate latency 增加，但因请求并行，墙钟预计增加 2–4 分钟；
- 不得使 N12 Input 超过本轮 161,861 的 1.40 倍。

## 8. P1：Mention 缺失候选的最小恢复闭环

### 8.1 当前问题

本轮 Grounder：

- 初始 invalid JSON 6 次；
- missing recovery 6 次；
- missing recovery 再次 invalid JSON 3 次；
- Judge initial invalid JSON 2 次。

现有 Grounder missing recovery 已经只发送 missing candidates，但 `repair_on_failure=False`；请求根结构失败时，当前文档的全部 missing candidates 直接记为 `FAILED_TECHNICAL`。

### 8.2 修改

保留当前文档级 missing recovery，不增加常规请求。仅在其失败或部分缺失时：

1. 保留已经合法恢复的 draft/rejection；
2. 将剩余 candidate 拆成 item-local task；
3. 每个 task 只发送：candidate statement、原 evidence locations、相关 segment、published_at；
4. item tasks 使用 M3 并行执行，最多 8 个活动请求；
5. 每 candidate 只允许一次 item repair；
6. repair 仍非法则保留 `FAILED_TECHNICAL`，不阻塞文档；
7. 不重新处理正常 candidate，不重跑整个 Grounder batch。

为了控制调用数，若同一文档剩余 2–4 个 candidate，可在一个紧凑请求中处理；超过 4 个则分片，每片最多 4 个。

### 8.3 Judge root failure

Judge 当前 batch root failure 会 ACCEPT 全部 Grounder drafts，保障了召回但可能降低 Precision。下一轮不应为 2 次失败新增全 batch 串行重跑。

只做两项轻量调整：

- 对 root invalid JSON 先做表示级 JSON 恢复；
- 恢复失败仍 ACCEPT Grounder draft，不追加额外模型轮次。

Judge 的 semantic item repair 与 coverage recovery 保持现状，避免把召回优化变成新的 M4 成本中心。

### 8.4 Mention 收益边界

该项只修技术性 missing，不重新审查模型明确 REJECTED 的全部候选。此前对 rejected disposition 的全量复审曾增加 FP 与 fragmentation，因此本轮不恢复该路径。

预计新增 Input 10k–20k，正常只在发生 missing-recovery failure 时支付；无失败运行新增成本为 0。

## 9. Field 节点的边界

本轮 Field 436/436 task 均技术成功，Field total 下降主要同时受 Mention 数量减少和字段内容本身错误影响，不存在与 N9/N12 相同的“正向判断未 Apply”证据。

因此本方案不增加全量 Field second pass，也不扩大 `UNRESOLVED` 的 LLM 重判。这样可以避免破坏 Field 全局去重带来的 Token 收益。

只保留一个可选的零/低成本后处理：

- 对唯一高可信候选、namespace/issuer/type 完全匹配、没有 blocker，但模型返回 UNRESOLVED 的 task，进入现有 safe deterministic match；
- 不降低既有 hard dimensions；
- 不对多候选 Field 做 late LLM；
- 单独统计它对 N9 residual 数的影响。

如果固定输入回放不能证明该分支具有 ≥99% precision，则不启用。Field 的广义质量优化应另立方案，不与本轮碎片化收敛混在一起。

## 10. P2：Prompt 最小调整

### 10.1 原则

- 不重写领域 Prompt；
- 不增加长 CoT；
- 不要求置信度；
- 不增加 reasoning payload；
- 只修复“缺信息即拆分”和“子事实不同即父 Package 不同”两种倾向；
- 首轮 Prompt 与 late Prompt 使用同一业务定义，late 只缩小任务范围。

### 10.2 Grounder missing recovery 追加句

替换现有 recovery 附加说明为：

```text
Dispose every supplied missing candidate exactly once. Recover it when its evidence supports a valid atomic Mention; reject it only for a concrete business defect. Missing optional detail is not itself a reason to reject.
```

不修改主 Grounder 的 Atomicity、Evidence 与 candidate disposition 规则。

### 10.3 N9 主 Prompt 调整

在 `atomic_coreference.md` 的 identity assessment 段加入一句，并删除语义重复的保守表述：

```text
Choose SAME_EVENT when the evidence supports the same minimal occurrence despite wording, granularity, omitted detail, or compatible claim values. Choose RELATED_NOT_SAME only for a material identity boundary; missing detail alone is not such a boundary.
```

保留以下硬边界原文：

- 不同 metric/facet；
- 不同 object/action polarity；
- 不同 assertion state；
- 不同 market session/measure；
- 不同 issuer/analyst/artifact；
- shared report/container 不是 Atomic identity。

### 10.4 N9 late Prompt

Late 请求不需要复制完整主 Prompt，只使用主 Prompt加以下短任务说明：

```text
Recheck only the supplied RELATED_NOT_SAME residual pairs. Decide whether each pair is the same minimal occurrence after normalizing wording and detail level. Return SAME_EVENT unless a material identity boundary differs; do not merge merely because both facts share a parent Package.
```

这同时防止两种错误：

- 把同一事实因措辞/细节拆开；
- 把同一财报中的不同 Atomic 子事实合并。

### 10.5 N12 Prompt 调整

在 `package_assignment.md` 加入：

```text
Judge shared parent membership, not Atomic equality. Different child facts may be MEMBER when evidence anchors them to the same bounded parent occurrence; choose NOT_RELATED only for a material parent-boundary conflict. Missing parent detail or a Package-family mismatch alone is not such a conflict.
```

当前 Prompt 已经说明“family does not identify the container”，新增句使模型决策与 Wave C 业务逻辑完全对齐。

### 10.6 N13 Prompt 调整

在 `package_merge.md` 加入：

```text
Use SAME_PACKAGE for the same bounded parent occurrence even when member coverage, child emphasis, or Package family differs. Use DIFFERENT_PACKAGE only for a material parent-boundary conflict; missing detail alone is not a conflict.
```

并保留：

```text
Conflicting trusted artifacts or institutions, exact canonical periods, and explicit controlled trading sessions remain hard boundaries.
```

### 10.7 Prompt 版本与缓存

- 更新单文档 Prompt 版本，仅使 Grounder missing-recovery processing key 变化；
- 更新 N9/N12/N13 Prompt/assignment policy version；
- 不让 Grounder 主流程因为一条 recovery 附加句整体失去缓存；
- late task 使用独立 stage/policy version；
- 已完成首轮 task artifact 可继续复用，late artifact 单独幂等。

## 11. DTO、Schema 与 Artifact

### 11.1 不修改持久化核心模型

不新增 EventMention、AtomicEvent、EventPackage 的业务字段。Late Convergence 需要的内容均为编排层 DTO：

```text
AtomicLatePairTask
AtomicLatePairDecision
PackageFragmentCard
PackageLatePairTask
PackageLatePairDecision
PackagePairBoundary
LateMergeEdge
LateMergePlan
```

### 11.2 最小 Wire DTO

建议 N9 late 输出：

```json
{
  "decisions": [
    {"pair_id": "r1", "relation": "SAME_EVENT"}
  ]
}
```

建议 N12 Wave C 输出：

```json
{
  "decisions": [
    {"pair_id": "r1", "relation": "SAME_PARENT"}
  ]
}
```

原因字段不新增。业务审计依据来自输入 card hash、routes、结构化 boundary 和模型 relation。

### 11.3 Stage artifacts

新增三个 content-addressed artifact：

```text
atomic_late_plan_v1
atomic_late_partition_v1
package_wave_c_plan_v1
package_wave_c_partition_v1
n13_late_apply_plan_v1
```

每个 artifact 至少包含：

- upstream artifact hash；
- snapshot/partition hash；
- residual task IDs；
- budget cap；
- decision refs；
- applied source/target roots；
- resulting partition IDs；
- code/policy version。

幂等要求：

- 相同 manifest + upstream hash 重跑不能新增模型调用；
- 相同 MergePlan 重放不能新增 redirect/membership；
- 若 upstream partition 变化，只失效对应 late artifact，不失效更早 stage。

## 12. Token、调用数与耗时预算

### 12.1 总预算

以 R3 为基线：

| 指标 | R3 | 正常目标 | 硬上限 |
| --- | ---: | ---: | ---: |
| Input Token | 1,582,413 | ≤ 1,677,358（+6%） | ≤ 1,709,006（+8%） |
| 总墙钟 | 2,726,440 ms | ≤ 2,999,084（+10%） | ≤ 3,053,613（+12%） |
| 模型调用 | 816 | ≤ 840 | ≤ 852 |

硬上限 Input 的精确实现值应按 `round(R3 * 1.08)` 计算，避免在代码里写手工近似常量。

### 12.2 节点预算

| 新增项 | Input 正常预算 | Input 硬上限 | 预期墙钟增量 |
| --- | ---: | ---: | ---: |
| P0 neutral reducer | 0 | 0 | < 1% |
| N13 existing SAME Apply | 0 | 0 | < 1% |
| N9 late | 20k–35k | 45k | 1–2 min |
| N12 Wave C | 30k–50k | 65k | 2–4 min |
| Grounder failed recovery item pass | 10k–20k | 25k | 仅失败文档，和其他 repair 并行 |
| 合计 | 60k–95k | 126k | 3–6 min |

### 12.3 运行时预算控制

预算控制在 planner 生成任务时执行，而不是请求失败后才统计：

1. 先加入零 Token reuse/M0 任务；
2. 再按支持分选 N9 residual；
3. 再按 parent evidence 选 Wave C pair；
4. 预计 payload token 超预算时按边缘收益从低到高截断；
5. 不降低并发等待预算，也不串行排队；
6. 达到 wall-clock late deadline 后，取消尚未启动的 residual 请求，保留已完成合法结果；
7. 未完成 task 中性降级，不形成负边。

### 12.4 不允许的降本方式

- 不通过减少 N7/N12/N13 candidate coverage 降 Token；
- 不把多个复杂 pair 塞进超大 batch；
- 不减少 identity/anchor/representative evidence 到模型无法判断父边界；
- 不让 repair/fallback 重发完整初始 payload；
- 不把 thinking/output 截断到大量 invalid JSON。

## 13. 精度保护与允许的牺牲

### 13.1 允许的 Precision 下降

| 层级 | 当前 | 优化目标 | 最低可接受线 |
| --- | ---: | ---: | ---: |
| Mention Precision | 73.71% | ≥72% | ≥71% |
| N9 MERGE Precision | 92.00% | 88%–92% | ≥87% |
| Package Pair Precision | 98.94% | 94%–97% | ≥92% |

该口径明确允许用少量 FP 换回更多 TP，但不允许产生大型污染簇。

### 13.2 绝不放宽的边界

- reaction → earnings/package merge violation = 0；
-不同 issuer merge violation = 0；
- 不同 trusted artifact merge violation = 0；
- analyst institution/report conflict violation = 0；
- N9 primary metric/action/object/session hard conflict violation 不增加；
- 不形成新的超大 Atomic supercluster；
- Package largest cluster 的增长必须能由同一父事件证据解释。

### 13.3 污染半径控制

Late Apply 的最大风险不是单条 FP，而是 FP 进入大簇后的组合放大。因此：

- N9 source 只允许 singleton/2-member；
- N13/N12 source fragment 正常 ≤3、硬上限 4；
- hub 可以大，但每条 spoke 必须独立通过 pair boundary；
- spoke 之间不因共同 hub 自动获得关系；
- 一个 hub 单轮最多吸收 4 个 spoke；
- Apply 后 BoundaryGate 发现 hard conflict 时整份 Late MergePlan 不提交，而不是回滚整个 epoch。

## 14. 代码修改边界

### 14.1 `src/cdecr/bulk_epoch/package_stage.py`

- 将 `_constrained_components()` 拆为带 hard/soft 边的 reducer；
- 新增 `plan_package_wave_c()`；
- 新增 `reduce_package_wave_c()`；
- 新增 hub-and-spoke plan builder；
- 保持 Wave A/B 与现有 candidate index；
- 新增 Wave C telemetry，不在这里调用持久化写入。

### 14.2 `src/cdecr/bulk_epoch/engine.py`

- 在 `_apply_atomic()` 与 embedding sync 之间接入 N9 late；
- 在 N12 Wave B 与 `package_partition` artifact 之间接入 Wave C；
- 在 N13 decision 后使用 pair-local eligibility/reducer；
- stage timing 增加：
  - `atomic_late_ms`；
  - `package_wave_c_ms`；
  - `n13_late_apply_ms`；
- task ledger 增加独立 stage，但不改变旧 stage 的成功定义；
- residual budget 超限只截断 late stage，不影响 epoch finalize。

### 14.3 `src/cdecr/cross_document.py`

- N9/N12 invalid task 从 synthetic negative 改为 neutral omission；
- 抽出 compact late N9 decision helper；
- 抽出 compact Wave C pair decision helper；
- 将 N13 `apply_allowed` 改为 `PackagePairBoundary`；
- 复用现有 `_package_pair_signals()`、Slim View、PackageBoundaryGate 和 merge-plan 持久化；
- 删除 package-level `anchor_conflict` 对所有 pair 的直接一票否决。

### 14.4 `src/cdecr/single_document.py`

- Grounder missing recovery 失败后，对剩余 candidate 做 item-local recovery；
- 保留合法 partial output；
- Judge root invalid JSON 只做表示恢复，不新增完整 LLM retry；
- 新增 item recovery token/latency telemetry。

### 14.5 Prompt 与 contract

- 更新 `atomic_coreference.md`；
- 更新 `package_assignment.md`；
- 更新 `package_merge.md`；
- Grounder 主 Prompt 不改，只改 recovery 追加句；
- 新增两个最小 late Wire DTO；
- 不改 EventMention/AtomicEvent/EventPackage persistence schema。

### 14.6 配置

避免引入通用规则引擎，仅增加少量明确配置：

```text
CDECR_ATOMIC_LATE_TASK_CAP=48
CDECR_PACKAGE_WAVE_C_PAIR_CAP=64
CDECR_LATE_TOTAL_INPUT_BUDGET_RATIO=0.08
CDECR_LATE_WALL_DEADLINE_RATIO=0.12
CDECR_LATE_MAX_SPOKE_MEMBERS=4
CDECR_LATE_MAX_SPOKES_PER_HUB=4
```

运行时默认启用一次性交付的全部优化，但保留三个粗粒度紧急开关用于独立回滚：

```text
CDECR_ATOMIC_LATE_CONVERGENCE=on
CDECR_PACKAGE_WAVE_C=on
CDECR_N13_PAIR_LOCAL_APPLY=on
```

不为每条细规则增加开关，防止配置爆炸。

## 15. 实施顺序：一次性交付，不分批上线

P0/P1/P2 仅表示依赖关系和排查优先级，执行时应一次性落地、集成、测试并默认启用。

### 工作包 A：Neutral failure semantics

- N9 invalid task 不生成 synthetic UNRELATED；
- N12 invalid assessment 不生成 synthetic NOT_RELATED；
- hard/soft negative reducer；
- task ledger 与审计错误分类；
- 相关纯函数测试。

### 工作包 B：N13 pair-local Apply

- PackagePairBoundary；
- 完整 anchor 集 pair 比较；
- package-level anchor conflict 降级为 pair context；
- Tier A/B/C eligibility；
- hub-and-spoke MergePlan；
- BoundaryGate final validation。

### 工作包 C：N9 Late Convergence

- residual planner；
- L0 deterministic convergence；
- L1 compact M3 pair protocol；
- singleton/small-source Apply；
- atomic late artifact/task ledger。

### 工作包 D：N12 Wave C

- Component Card；
- bounded index/recall；
- M0/reused/M3 decisions；
- component hub-and-spoke reducer；
- Wave C artifact/telemetry。

### 工作包 E：Mention technical recovery

- missing-recovery item pass；
- compact payload 与 failure isolation；
- Judge JSON representation recovery；
- no-failure zero-cost path。

### 工作包 F：Prompt、版本、验收脚本

- 四处最小 Prompt 对齐；
- processing/policy version；
- counterfactual replay；
- recorded-response 测试；
- 30 篇真实 A/B 与 detailed bad-case report。

## 16. 测试设计

### 16.1 纯函数测试

#### Neutral failure

- N12 task 中 1 个 assessment 非法、其余 MEMBER 合法时仍选择 MEMBER；
- 全 task 不可判时 singleton，但 reducer 不出现 NOT_RELATED edge；
- N9 missing task 形成 singleton，但不写 UNRELATED；
- repair 失败不影响同 batch 其他合法 task。

#### hard/soft negative

- 一条 soft NOT_RELATED 不阻止两条独立 MEMBER edge；
- trusted artifact conflict 永远阻止 union；
- reaction/disclosure boundary 永远阻止 union；
- soft negative 不能在没有正向支持时自动合并。

#### pair-local anchor

- hub `anchor_conflict=true`，spoke 命中其中一个 canonical anchor：允许；
- 两侧唯一 trusted artifacts 不同：拒绝；
- anchor sets 均为空、只有 same company：拒绝；
- shared parent context + same source + compatible representative identities：允许 Tier B。

#### hub-and-spoke

- 一个大 hub 可吸收 4 个独立 singleton；
- spoke 不能被两个 hub 同时吸收；
- 新合并结果不能触发当轮二次候选；
- 大 cluster ↔ 大 cluster 不执行；
- 最终 BoundaryGate blocking 时不提交整份 late plan。

### 16.2 N9 recorded bad-case 测试

固定 R3 的 20 个 missed SAME task，验证：

- residual planner 全部覆盖符合条件的 RELATED_NOT_SAME；
- hard-conflict candidate 不进入；
- 完全同句与 exact identity 进入 L0；
- value/period、粒度、市场发生变体进入 L1；
- revenue/EPS/capex 不因同 earnings parent 被合并；
- Apple actual price increase 与 iPhone unchanged 不被错误当成同一 action/object；
- pre-market/close/after-hours 冲突继续分开。

离线目标：

- 20 个 Gold SAME 漏合至少回收 10 个；
- 新增 Gold FP 不超过 2 个；
- residual MERGE precision ≥ 83%；
- N9 总 MERGE precision 投影 ≥ 88%。

### 16.3 N13 weak-SAME counterfactual replay

对 46 个未 Apply SAME 使用 R3 冻结记录：

1. 跑新 Tier A/B/C eligibility；
2. 只对现有 96-Atomic Gold 可判范围计算 pair TP/FP；
3. 其余标为 unjudgeable，不用于宣称 precision；
4. 回放 hub-and-spoke 后重新计算 Package pair 与 fragmentation；
5. 单列以下组：
   - Micron earnings；
   - Qualcomm strategy；
   - Apple IDC/Counterpoint vs actual price action；
   - KOSPI/SK Hynix/Micron market moves；
   - Micron-Anthropic partnership。

启用门槛不再要求 99.5% 的极保守 hard-rule 精度；本轮允许小幅 Precision 交换 Recall。建议 replay 门槛：

- 可判 eligible weak-SAME precision ≥ 88%；
- 至少回收 8 个正确 fragment join；
- reaction/artifact/issuer hard violation = 0；
- Package precision 投影 ≥ 94%；
- Package recall 投影提升至少 7pp。

### 16.4 Wave C recorded-response 测试

- Card 上限：4 anchors / 3 representatives；
- 相同 fragment 在多 pair 中只发送一次；
- pair 短 ID 完整唯一覆盖；
- 64 pair cap 严格生效；
- invalid pair 决策中性隔离；
- dictionary payload 不回退 legacy full-card；
- 同 profile pair 复用 N13 evaluation，不重复调用。

### 16.5 并发与复杂度测试

沿用 30/60/120/240 规模测试，新增断言：

```text
N9 late edges <= 2 * late_task_cap
Wave C edges <= wave_c_pair_cap
N13 applied spokes <= 4 * hub_count
late rounds == 1
registry active transaction count == 1
```

同时验证：

- late M3 实际并发至少达到 8；
- late stage 失败不取消首轮已成功结果；
- budget 截断确定性稳定；
- finalized epoch 重跑新增调用为 0；
- 无全表逐 pair 扫描。

### 16.6 全量回归

- `tests/cdecr/test_bulk_epoch_v3.py`；
- N9/Sidecar/MergeInvariant tests；
- N12/N13 Package Engine tests；
- Grounder/Judge item repair tests；
- `uv run pytest tests/cdecr -q`；
- `uv run ruff check src/cdecr tests/cdecr`；
- `uv run mypy src/cdecr`；
- `git diff --check`。

## 17. 真实 30 篇验收

### 17.1 控制变量

- 同一 30 篇 manifest；
- 同一顺序、source fingerprint 与 provider/model 配置；
- 复用本轮 Mention、Field、N9、Package Gold；
- 使用全新 Registry；
- 不复用 R3 最终 partition；
- 同时输出首轮结果与 Late Convergence 后结果，区分增量贡献。

### 17.2 必报成功率

- 单文档成功率；
- 跨文档成功率；
- late task 成功/失败/中性降级数；
- N9/N12/N13 每类 invalid item；
- 幂等重跑新增 model call/Mention/Atomic/Package 数。

任何 late failure 不得把文档或 epoch 变成失败；其最大影响只能是对应 residual 未收敛。

### 17.3 必报效能

- 总 Input/Output/Thinking/Total Token；
- 总墙钟、首轮墙钟、late 增量墙钟；
- 每节点 Token/aggregate latency/wall 占比；
- N9 late 与 Wave C 每次正确新增 join 的 Token 成本；
- N13 existing-SAME Apply 的零 Token join 数；
- 首轮与幂等重跑调用数。

### 17.4 Mention 门槛

| 指标 | 当前 | 验收目标 |
| --- | ---: | ---: |
| Precision | 73.71% | ≥72%，最低 71% |
| Recall | 69.03% | ≥74%，争取 76%–78% |
| missing candidate 技术失败 | 3 recovery calls | 0 或全部 item-local 隔离 |

如果 Mention 的模型波动掩盖了 recovery 收益，必须另报固定 R3 missing-candidate replay，不得把整轮波动全部归因给新架构。

### 17.5 N9 门槛

| 指标 | 当前 | 验收目标 |
| --- | ---: | ---: |
| candidate coverage | 100% | 100% |
| MERGE Precision | 92.00% | ≥88%，最低 87% |
| conditional MERGE Recall | 69.70% | ≥80%，争取 83%–85% |
| missed SAME | 20 | ≤10 |
| largest Atomic cluster | 7 | 不形成新 supercluster |

必须单列：

- 首轮 MERGE；
- L0 deterministic MERGE；
- L1 late M3 MERGE；
- 每类 TP/FP；
- 每个错误 merge 的污染半径。

### 17.6 Package 与碎片化门槛

| 指标 | 当前 | 验收目标 | 最低线 |
| --- | ---: | ---: | ---: |
| Pair Precision | 98.94% | 94%–97% | 92% |
| Pair Recall | 65.18% | 75%–80% | 72% |
| fragmented Gold groups | 8 | ≤4–5 | ≤6 |
| excess components | 16 | ≤8 | ≤10 |
| singleton components | 20 | ≤10 | ≤13 |
| missed links | 398 | <300 | <330 |

特别报告：

- Micron earnings 组件数与 missed links；
- Qualcomm strategy 组件数；
- Apple actual/outlook/report false merge；
- market session groups；
- 46 weak SAME 的 eligible/applied/TP/FP/unjudgeable 分布；
- Wave C 新增 join 与 N13 existing-SAME join 的分别贡献。

### 17.7 效能门槛

- Input Token 正常不超过 R3 +6%，硬门槛 +8%；
- 总墙钟正常不超过 R3 +10%，硬门槛 +12%；
- N12 Input 仍至少比旧 R2 下降 85%；
- N13 Input 仍至少比旧 R2 下降 60%；
- 不因 late stage 降低首轮 M2/M3 并发；
- 不出现 provider queue p95 显著抬升；
- 若超预算，先截断最低收益 residual，不回退主架构。

## 18. 回滚判断

本轮不允许整体回滚 Stage Graph。只评估三个质量层开关：

### 18.1 仅回滚 N9 late

条件：

- N9 MERGE Precision < 87%；或
- conditional Recall 提升 < 5pp；或
- 出现新的大型 Atomic 污染簇。

保留 neutral failure semantics 与其他效能架构。

### 18.2 仅回滚 Wave C

条件：

- Package Recall 提升 < 5pp；或
- Package Precision < 92%；或
- 新增 reaction/artifact/issuer hard violation；或
- Wave C Input 超过 65k 且碎片化改善不足。

保留 N13 pair-local Apply。

### 18.3 仅收紧 N13 Tier B

如果 Tier A 正确但 Tier B 产生 FP：

- 保留 Tier A shared artifact/anchor Apply；
- 将无 anchor 的 Tier B 退回 shadow；
- 不恢复 package-level `anchor_conflict` 一票否决；
- 不回滚 N13 dictionary/full bounded pair plan。

## 19. 预期效果与不确定性

### 19.1 保守可实现部分

- 取消 synthetic negative：零 Token，确定减少不必要 veto；
- N13 复用 existing SAME：零 Token，可直接收回部分 Package TP；
- N9 完全同句/exact identity L0：低风险回收若干漏合；
- Grounder failed recovery item pass：只修技术缺口，不扰动正常输出。

### 19.2 主要增益部分

- N9 L1 有机会回收 8–12 个当前 20 个漏合；
- N13 Tier A/B 有机会 Apply 15–25 个当前 weak SAME；
- Wave C 有机会把 Micron 7 组件压至 2–4、Qualcomm 4 组件压至 1–2；
- Package missed links 有机会从 398 降至约 250–320。

这些是基于 R3 残差结构的区间估计，不是验收前承诺值。

### 19.3 最大不确定性

35/46 weak SAME 不在现有 Package Gold 可判范围内。不能把模型 SAME 数直接当成正确合并数。因此实施时必须：

- 先跑既有可判子集 counterfactual；
- 真实 30 篇再由同一 Gold 评估最终 partition；
- 对 unjudgeable 大簇新增 join 做人工抽样；
- 不用“Package 数减少”代替 Recall/Precision 证明。

## 20. 最终执行判断

本方案建议一次性完整实施 P0/P1/P2，并默认启用：

1. neutral invalid-task semantics；
2. hard/soft negative reducer；
3. N13 pair-local Tier A/B Apply；
4. hub-and-spoke bounded reducer；
5. N9 L0/L1 Late Convergence；
6. N12 Component Wave C；
7. Grounder missing-candidate item recovery；
8. 四处简短 Prompt 对齐；
9. 完整 artifact、预算和验收统计。

这是一版有意保持激进的方案：它不再为了理论上的极高 Precision 放弃已经得到的 SAME/MEMBER 信号，也不把缺信息和格式失败视为事实不同；同时用 pair-local hard boundary、small-spoke、单轮收敛、固定边数与预算上限控制污染半径。

最终目标不是把 Stage Graph 重新变成低效的顺序工作流，而是在继续保有本轮约 78% 墙钟压缩和 58% Input Token 压缩的前提下，让并行 Map 的局部结果在末端真正汇合。
