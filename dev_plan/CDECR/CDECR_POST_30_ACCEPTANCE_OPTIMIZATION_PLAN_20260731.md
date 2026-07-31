# CDECR 30 篇真实验收后优化与修复方案

> 日期：2026-07-31
> 状态：待实施
> 主要依据：`CDECR_30_REAL_ACCEPTANCE_COMPARATIVE_REPORT_20260730.md`、本轮 30 篇
> Registry/trace、既有实现和用户给定的业务边界
> 本文只定义下一轮实施方案，不包含运行时代码修改或真实模型调用。

## 0. 总体结论与设计原则

本轮不应整体回滚。N13 pair pruning、exact profile-pair 去重、Evidence 确定性定位、
N7 召回路径和部分 Field 映射已有独立收益；失败集中在若干没有闭环或降级粒度过大的局部
机制。优化顺序应是：

1. 先恢复 item/assessment 级韧性，消除“局部非法扩大为整文档或整任务失败”；
2. 再修 Mention、Atomic、Package 三层业务边界；
3. 最后启用批量模式的新编排，以相同业务节点换取并行和阶段级去重，不把重构本身与语义
   规则变更混在一起验收。

全方案遵守以下共同约束：

- 不靠全局提高 MERGE 或 CREATE_NEW 倾向换指标；
- 不为 30 篇中的专名、数值或固定句式增加特例；
- 校验默认做确定性规范化、局部丢弃或局部降级，只有继续处理会完全失去业务意义时才阻断；
- Prompt 只补模型完成当前合同所必需的信息，不增加冗长审计任务；
- reason 协议保持不变，新增审计尽量使用编排层短 `code/id/axis`；
- 复杂 LLM 节点不激进扩大单请求 batch；吞吐提升主要来自独立任务并行、阶段 barrier 和重复
  请求消除。

## 1. 节点一：批量历史新闻场景的编排与效能重构

### 1.1 真实现状与根因

当前实际上存在两种不同的批处理行为：

- `SingleDocumentProcessor.process_batch()` 会先规划 exact duplicate representative，再用
  `document_concurrency` 并行处理代表文档；这一层已经不是纯串行。
- CLI `events.batch` 仍按 source 逐篇执行“单文档全流程 → 跨文档全流程”；验收入口虽先并行
  跑完所有单文档，但随后仍逐篇调用 `CrossDocumentEngine.process()`。
- `CrossDocumentEngine.process_batch()` 本身只是
  `[self.process(message_id) for message_id in message_ids]`。
- 每次 `process()` 都完整执行 N5.5→N13，并在同一篇内读取、判断和修改全局 Field、
  Atomic、Package 状态。因此后文档看到的是前文档已经演化过的候选簇和 Package profile。

这解释了两个现象：

1. 直接并行多篇跨文档流程会让 N7/N9、N12/N13 看见不同快照，可能改变候选、target 和最终
   cluster，质量风险真实存在；
2. 当前逐篇执行又让 Package profile 在每篇后演化，同一个 package-ID pair 虽然 exact
   profile hash 不重复，却因版本变化被重新评估 235 次。串行不仅慢，也制造了批量场景下
   本可避免的重复 Token。

本轮 30 篇总 Token 已下降 36.99%，但墙钟从 89 分 58 秒上升到 100 分 28 秒。主要模型等待
已经转移到 N12（24.21% 聚合时长）、N9（20.93%）、Grounder（25.12%）；继续只压 N13
payload 无法解决墙钟问题。

### 1.2 不采用的方案

以下方案不进入实施：

- **整篇 `CrossDocumentEngine.process()` 直接放入线程池**：读写快照会竞态，输出随调度顺序
  漂移。
- **把 N9/N12/N13 单请求 batch 大幅放大**：复杂判断的注意力和 Schema 失败风险会上升，
  且不能解决跨文档状态依赖。
- **把 Registry 改为多 writer 或引入通用分布式图引擎**：复杂性远超本轮收益。
- **全量固定 Snapshot 后一次性独立判完所有 Mention**：会看不到同批次中新形成的稳定
  Atomic/Package，容易牺牲召回。

### 1.3 双运行模式

保留现有实时增量路径，并新增显式批量路径；两者复用同一套领域节点和 Prompt，不维护两套
业务逻辑。

| 模式 | 入口语义 | 编排 |
| --- | --- | --- |
| `INCREMENTAL` | 日常新增一篇或小批新闻，要求即时更新 | 保持现有逐文档 N5.5→N13 |
| `BULK_EPOCH` | 对一个时间窗内几十至数百篇新闻做一次性抽取 | 先完成全体单文档，再按阶段 barrier 和候选图执行 |

运行模式、epoch ID、稳定输入顺序和候选策略版本必须进入 processing/run config，防止两种模式
错误复用完成结果。业务实体 ID 仍由事实内容决定，不把 epoch ID 混入 Mention/Atomic/Package
身份。

### 1.4 `BULK_EPOCH` 目标编排

```text
固定输入与稳定排序
  ↓
A. 单文档 Map
   exact-duplicate representative plan
   → Dreamer / Grounder / Judge / Mention materialize
   → 文档级结果 checkpoint
  ↓ barrier
B. Field epoch（N5.5）
   全量 Read + bulk embedding + candidate graph
   → 独立 connected components 并行 Decide
   → component 内保持稳定顺序
   → 单 writer Apply
  ↓ barrier
C. Identity / Atomic epoch（N6-N10）
   全量 N6 compile + bulk mention/head embedding
   → N7 候选图
   → 独立 components 并行；component 内顺序 N9/N10
   → 单 writer Apply + late-edge 检查
  ↓ barrier
D. Package seed / assignment epoch（N11-N12）
   一次性完成 anchor materialize
   → 全量 package seed/candidate graph
   → 独立 components 并行 N12
   → 单 writer Apply
  ↓ barrier
E. Package merge epoch（N13）
   在 N12 全部稳定后只生成一次 dirty profile snapshot
   → exact pair dedupe / deterministic guards
   → 独立 pair-components 并行 Decide
   → 单 writer Apply
   → boundary correction 后仅重评“实质身份字段变化”的 pair
```

### 1.5 并行安全边界：候选图连通分量，而不是文档

并行单元不按文章切分，而按该节点的实际候选关系切分：

1. 节点 Read 阶段使用本 epoch 的全量输入和进入 epoch 前的已持久化状态生成候选；
2. 两个任务只要共享候选 Field、Atomic、Package，或互为近邻候选，就放入同一个无向连通
   分量；
3. 不同分量没有可见候选边，可以并行 Decide；
4. 同一分量内部继续按 `published_at + message_id + mention_id` 稳定顺序执行，保留当前
   “前项 Apply 后影响后项”的业务语义；
5. 所有写入仍经单 writer，且 Apply 前比较 candidate/profile version。

为了避免 top-k 截断把潜在边错误切开，构图使用比实际送模更宽的 recall pool：正式 N7/N12
仍只给模型当前上限内的候选，但 component union 使用 top-k 之外的近阈值候选和确定性共享
anchor/identity 边。它只影响调度，不把额外候选塞给模型。

每个 epoch 结束做一次低成本 `late-edge` 检查：如果 Apply 后 profile 变化产生了跨 component
的新候选边，只把受影响的 component 放入串行 convergence lane 重规划；不回滚整个 epoch，
也不静默接受过期决定。正常路径没有第二轮。

### 1.6 Read / Decide / Apply 的最小重构尺度

不把每个节点重写成新的框架，只抽出共用的三段接口：

- `read_plan(epoch_snapshot) -> tasks/components`：只读 Registry，计算候选、版本和短 task ID；
- `decide(task)`：调用现有确定性规则、现有 Prompt 和模型 adapter，不写领域状态；
- `apply(decision, expected_versions)`：单 writer 调用现有 N5.5/N10/N12/N13 Apply 代码。

实时模式可以用同一接口处理“只有一个 component 的 epoch”，避免出现两套实现。只有候选图
规划、epoch checkpoint 和 version conflict 重排是新增编排能力，Field/Atomic/Package 的领域
判定仍留在现有节点。

### 1.7 Token 压缩点

本节点不通过删 Prompt 或放大复杂 batch 降本，采用以下编排级措施：

1. **N13 延迟到 N12 epoch 完成后统一运行**：同一 Package 在连续文档中反复演化不再触发
   235 次中间 profile 重评；只在 boundary Apply 或成员实质变化后重评。
2. **epoch 内 pair ledger**：键为 unordered package IDs + 两端语义 profile hash + policy
   version；完全相同的 pair 决策只调用一次。审计事件变化、时间戳等非身份字段不使 hash
   失效。
3. **Embedding 全量规划后按 provider 上限合批**：只改变 M1 调用编排，不改变向量文本；
   已存在且 input hash 相同的向量直接复用。
4. **候选图先做已经验收的 deterministic DIFFERENT / hard rule**：只把未被安全规则解决的
   pair 送 LLM；不新增未经 Gold/回放验证的宽规则。
5. **同一非法 item 只修一次**：Grounder、Judge、N9 的局部 repair/fallback 由各自节点方案
   处理；bulk orchestrator 不因整个 epoch 重试而重复消费已完成 request。

预期收益应保守分开报告：

- N13 剩余输入 Token 预计再降 10%–30%，主要取决于中间 profile 重评消除量；
- 全流程 Token 目标下降 5%–15%，不得以质量下降换取；
- 更主要收益是跨 component 并行，30 篇墙钟目标先设为相对当前 100 分 28 秒下降至少 30%；
  数百篇场景另报告吞吐（documents/hour）和尾延迟，不能用 30 篇线性外推伪造收益。

### 1.8 并发设置与大 batch 风险

`component` task 可以充分 fan-out，但真实模型请求继续受统一 tier lane 限流。首轮不下调当前
M1/M2/M3/M4 的 `2/6/3/2`，也不把复杂节点 batch 上限调大。实施时增加并发阶梯试验：

- M3：`3 → 4 → 6`；
- M4：`2 → 3 → 4`；
- 每一档固定上游输入，观察 429/timeout、P95 model latency、queue wait、structured success
  和业务结果一致性；
- 仅当错误率不增加、P95 latency 增幅不超过 20%、最终合法率不下降时进入下一档。

这避免把“worker 数”误认为有效并发：bulk document workers 可设为 6–8 以填满 pipeline，
但实际请求并发由 tier lane 控制。若 provider 已饱和，增加 worker 只会增加 queue wait，不应
宣称为加速。

复杂 LLM batch 保持当前大小的原因不是保守，而是本轮已经同时出现长上下文、整 batch 校验
失败和注意力分配问题。只有在节点级固定输入 A/B 证明 batch 增大后：

- per-item input 明确下降；
- first-pass structured valid 和 candidate coverage 不降；
- 业务 P/R 不降；
- P95 latency 没有抵消调用数收益；

才允许对该节点单独增加一级，而不是全局一起放大。

### 1.9 故障、幂等与审计

- checkpoint 粒度为 `single-document result`、`component decisions`、`component apply` 和
  `epoch barrier`；
- 单 component 技术失败不抹掉其他 component；失败 component 可恢复，Mandatory component
  未完成时 epoch 标记 `PARTIAL/FAILED`，不能伪装成 CREATE_NEW；
- version conflict 只重读并重判冲突 component；
- 审计只新增 `epoch_id/component_id/snapshot_hash/expected_version/replan_code`，不增加模型
  reasoning；
- 同一成功 epoch 重跑新增模型调用、Mention、Atomic、Package 必须均为 0。

### 1.10 实施顺序与验收

1. 先把当前 `events.batch` 和验收入口统一到同一 batch orchestrator，但保持
   `INCREMENTAL_COMPAT` 串行行为，建立等价基线；
2. 抽出 N5.5、N9/N10、N12、N13 的 Read/Decide/Apply，不改变 Prompt；
3. 启用单 writer + version check；
4. 依次开启单文档 barrier、Field components、Atomic components、Package components；
5. 最后开启“N12 全量完成后统一 N13”和实质 profile diff 重评。

每一步都用同一固定输入同时跑 serial reference 与 bulk candidate，比较：

- Mention 集合及字段；
- N5.5 links；
- N7/N12 candidate coverage；
- Atomic/Package membership 与 action/target；
- Pair Precision/Recall 和 Mention/Package P/R；
- 总 Token、各节点 Token、wall clock、queue wait、重评 pair 数；
- 并发顺序扰动三次后的最终 ID 与 membership 稳定性。

启用门槛：

- 业务指标不得低于本方案后续节点修复形成的 serial reference；
- candidate coverage 必须 100%；
- 不新增整文档/整 task fallback；
- N13 同 profile pair 重复为 0，同 package-ID pair 的无实质变化重评为 0；
- 30 篇墙钟至少下降 30%，全流程 Token 不上升；
- 若 component 构图导致 late-edge replan 超过任务数 10%，说明分区不可靠，先回退该节点的
  component 并行而不是放宽业务边界。

本节点不修改任何 Prompt。

## 2. 节点二：Mention 生成、拒绝、补处置与字段保真

### 2.1 指标与问题分布

本节点的验收目标为：

- Mention Precision > 90%；
- Mention Recall > 85%；
- candidate business disposition coverage = 100%；
- 已知 compound pattern 修复率 ≥ 80%；
- 新增 fragmentation bad case ≤ 2。

本轮实际为 P=76.88%、R=57.09%，相对上一轮分别下降 8.45pp、14.55pp。324 个 Dreamer
candidate 中 239 个 used、46 个明确 rejected、39 个 missing，coverage 只有 87.96%。
26 次 `grounder_item_repair` 全部再次非法，恢复 0 条，却消耗 77,644 Token 和 226.238 秒
聚合模型时长。

bad case 不是单一“少抽”：

- **静默缺处置**：39 个 missing，集中在 metric/quantity、guidance、benchmark/range、
  market session、source assessment、artifact/commitment；
- **旧 compound 复现**：D03 revenue 与 data-center revenue 仍合并；
- **拆分后信息损失**：D04 拆开 partnership/supply 后漏 investment；
- **新增 compound**：market move + market-cap loss、revenue trend + margin、不同 horizon 的
  DRAM/NAND supply state；
- **新增 fragmentation**：同一事实的 metric/qualifier 或 market move/trigger 被拆成残缺片段；
- **field 错位**：comparison、benchmark、period、action polarity、product/object 或 source
  claimant 被遗漏或复制到错误的 replacement。

因此不能只提高 Grounder 的“多输出倾向”。召回修复必须同时带信息守恒与非重复约束，否则
会用更多 FP 和 fragmentation 换回 Recall。

### 2.2 上一轮修复为什么无效

上一轮方向本身包括 atomicity Prompt、candidate rejection ledger、单非法 draft 并行 repair、
Judge semantic validator，方向不需要整体回滚；失败在三个未闭环点：

1. **coverage 只审计不补处置**：当前发现 missing 后仅记录
   `RETAIN_VALID_ITEMS_AND_LEAVE_MISSING_UNRESOLVED`，没有第二次只针对 missing 的请求。
2. **“恰好一次”被实现成了错误的物理唯一性**：代码不但要求 USED 与 REJECTED 精确覆盖，
   还把同一 candidate ID 出现在第二个 draft 视为 duplicate，并直接丢掉后一个 draft。
   一个 compound Dreamer candidate 因而无法合法产生两个独立 Mention。这与“不同指标/
   action/subject 必须拆”的业务要求冲突。
3. **repair 看不到根级业务错误**：`MentionDraft` 的 Pydantic 根校验会产生诸如
   “metric-bearing Mention requires exactly one PRIMARY quantity”的真实错误，但 collector
   只传 `loc=["mention"]` 和 `type="value_error"`。模型不知道应修 PRIMARY 数量、时间精度
   还是 assertion，26 次只能盲修。

此外，当前 item repair schema 只能返回一个 draft。若非法原因正是一个 draft 含多个独立
PRIMARY metric，强迫它仍返回一个对象只能丢 metric 或把独立 metric 错降为 SUPPORTING；
这也是结构正确与信息完整无法同时满足的原因。

### 2.3 正确定义“每个 candidate 恰好一次”

这里必须对字面约束做一次业务化校正，否则会继续阻止正确拆分：

```text
每个 candidate 恰好有一个 disposition：USED 或 REJECTED。
USED candidate 可以被一个或多个非重复 atomic drafts 引用；
只有 evidence 本身包含多个独立可判真的事件时才允许 fan-out。
REJECTED candidate 只能在 rejected_candidates 中出现一次，且不能出现在任何 draft。
```

编排层验证改为：

```text
used = union(drafts[*].source_candidate_ids)
rejected = set(rejected_candidates[*].id)

used ∩ rejected = empty
used ∪ rejected = all input candidate IDs
rejected IDs are unique
```

不再把“一个 candidate 被两个 draft 引用”直接判为 disposition duplicate。改为检查两个 draft
是否至少在一个核心身份维度上确实不同：core subject/object、predicate/action polarity、
assertion state、event time/session 或 PRIMARY metric。完全相同的身份签名才视为重复 draft，
保留稳定排序中的一个并送 Judge 复核。

这不新增模型字段。只在编排审计中记录短
`candidate_id/fanout_count/distinct_identity_signature_count`，用于区分合法拆分和重复生成。

### 2.4 初始 Grounder 的最小 Prompt 调整

现有 Prompt 已写“Every candidate must appear exactly once”，但模型和 validator 对“出现”
的含义与拆分规则冲突。将 Candidate disposition 段替换为：

> Give every supplied candidate exactly one disposition: USED or REJECTED. USED means it
> appears in `source_candidate_ids` of one or more non-duplicate atomic drafts; REJECTED means
> it appears once in `rejected_candidates` and in no draft. A candidate may support multiple
> drafts only when its evidence contains multiple independently truth-evaluable events. Before
> returning, verify that USED and REJECTED are disjoint and cover every supplied candidate.

在 Atomicity 段后增加一段私有分解顺序，不要求输出 CoT：

> Before drafting, privately factor each candidate by core subject/object, action and polarity,
> Assertion State, event time/session, and PRIMARY metric. Split when one of these defines a
> different truth-evaluable event; do not split a comparison, bound, qualifier, or supporting
> value from the event it qualifies. Each resulting draft must retain its supported period,
> benchmark/range, source attribution, and object.

在 Eventhood 段补一句，收窄错误 `BACKGROUND`：

> Use BACKGROUND only when no independently truth-evaluable event remains; analytical wording
> alone does not make an explicit forecast, rating, plan, commitment, measurable state/change,
> market move, or scheduled occurrence background.

增加一条 umbrella 边界：

> A shared report, plan, article, or topic is context, not a Mention identity. Keep a generic
> umbrella only when it states an independent action or artifact fact not exhausted by the
> specific drafts.

这些句子针对的是开放世界的身份维度和信息守恒，不包含 30 篇中的公司名、指标名或固定数值。
不增加输出字段，不要求模型输出 checklist/reasoning。

### 2.5 Missing candidate“补处置”请求

主 Grounder 的所有 batch 和 item repair 完成后，按文档汇总仍不在 `used ∪ rejected` 中的
candidate。不要跨文档组成一个大 request；不同文档的 source context、短 ID 和 Evidence
不同，跨文档合批会显著增加注意力负担。正确粒度是：

- 同一文档的 missing candidates 合成一个补处置 request；
- 若超过现有 `GROUNDER_CANDIDATE_BATCH=24`，仍按 24 切分，不提高复杂 batch 上限；
- 不重复提供已合法 draft 和已 rejected candidate，只提供 missing candidate、对应局部
  document segments、`published_at` 和原 Grounder 业务规则；
- 不重新处理整篇，也不重写合法结果；
- 不同文档的补处置 request 可受 M3 lane 限流并行。

补处置 Prompt：

> Resolve every supplied missing candidate. Give each one exactly one disposition under the
> same USED/REJECTED rules as Grounder. Preserve all supported event fields; do not reject a
> candidate merely because it contains multiple events—split it into atomic drafts. Return no
> IDs that were not supplied.

补处置返回仍使用 Grounder draft/rejection DTO 的子集，不另建第二套 Mention Schema。结果和
主输出合并后再次执行 exact-cover、重复事实和 item semantic validation。

若补处置后仍有 candidate 未覆盖：

- 文档继续成功，不以硬校验阻断；
- 编排层记录 `FAILED_TECHNICAL` disposition 和短 error code，但不能伪装成 BACKGROUND；
- candidate **business** coverage 仍判失败，不能把技术失败计入 100%；
- 仅对非法 item 走一次 item repair，不重试整个补处置 batch。

这会增加少量正常路径调用，但本轮 39/324 missing 只占 12.04%，且按文档合并通常每篇只有
1–6 条；同时修复 26 次零收益 repair 并加熔断后，净增量应保持有界，但不能在实测前宣称总
Token 一定不增。最终以“每恢复一个正确 Mention 的增量 Token”报告，不能只报 coverage。

### 2.6 单非法 draft repair：传根因，并允许有界 replacement

repair 仍严格是“只修这一条非法 draft”，继续与其他合法 draft 并行；不恢复整 batch 或整篇
串行 repair。但输出改为有界 `replacements`：

- 普通字段/Schema 错误应返回 1 个 replacement；
- `MULTIPLE_PRIMARY_METRICS`、`OPPOSING_CORE_ACTIONS`、
  `OPPOSING_SUBJECT_ACTIONS` 等确需拆分的业务错误，可返回 2–4 个 replacements；
- replacements 必须覆盖原 draft 的 candidate lineage 和受支持信息，不能修改其他 draft；
- 超过 4 个通常意味着上游 candidate 过宽，保留前述合法结果并把该 item 交补处置，不允许
  无限扩张。

ValidationError 先经 JSON-safe normalizer 转为：

```json
{
  "field_path": ["mention"],
  "code": "PRIMARY_QUANTITY_COUNT",
  "message": "A metric-bearing Mention must contain exactly one PRIMARY quantity."
}
```

只允许稳定、短的业务码和安全 message，不传 raw input、exception `ctx` 或长 traceback。至少
映射：

- `PRIMARY_QUANTITY_COUNT`；
- `TIME_PRECISION_WITHOUT_BOUNDS`；
- `MISSING_REQUIRED_FIELD`；
- `EXTRA_FIELD`；
- `INVALID_ENUM`；
- `INVALID_SHORT_ID`；
- `EVIDENCE_FIELD_SHAPE`；
- `OTHER_SCHEMA_ERROR`。

item repair Prompt 替换为：

> Repair one invalid Grounder draft using `business_errors`. Preserve its supported meaning and
> candidate lineage. Return all and only the atomic replacement draft(s) needed to fix those
> errors; retain each replacement's subject/object, action and polarity, Assertion State,
> period/session, PRIMARY metric, comparison/range, and explicit source. Do not repair unrelated
> drafts.

同一 run 内按 error code 统计“最终合法 replacements / repair 数”。同一 code 连续 3 次零恢复
时，对该 run 的后续同 code item 熔断 item repair，直接进入 missing 补处置；不同 code 不受
影响。这样既防止再次发生 26 次同质无效请求，也不会因首条偶发失败全局禁用 repair。

### 2.7 Rejected candidate 的处理

不能把 46 条 rejected 全部再送一次模型。35 条为 BACKGROUND，其中有真背景，也可能有因
Eventhood 误解产生的 FN；无差别复审会提高 Token，并极易以 Recall 换 Precision。

本轮采用两层处理：

1. 用 2.4 的 Eventhood 句子修正“分析性表达 = 背景”的错误捷径；
2. 验收时逐 rejection code 报告 Gold TP/FN，尤其单列具有显式 claimant + action、
   PRIMARY metric/quantity、明确 plan/commitment、market move 或 scheduled occurrence 的
   rejected candidate。

暂不新增 rejected residual LLM 节点。只有新验收仍显示某个受控 rejection code/特征组合
具有足够样本，且错误拒绝率 > 20% 时，才对该窄组合做固定输入 A/B；不能与 missing 补处置
混为一条默认二审链。

### 2.8 Judge 对 Mention Precision/Recall 的职责

Judge 保持“所有合法 Grounder draft 必须有一个最终 decision”，但在正常 Prompt 中补两句：

> For SPLIT, preserve every supported event and attach each period, benchmark/range, source,
> object, and action polarity only to the replacement it qualifies. Do not copy all fields to
> every replacement or leave a supported event behind.

> A shared report, plan, article, or topic does not make distinct objects, actions/polarities,
> PRIMARY metrics, Assertion States, or event times/sessions one Mention.

Judge 不根据动词数、quantity 数机械拆分；对同一 metric 的 bound/comparison/qualifier 仍保持
在主事件中。Generic umbrella 只有存在独立 artifact/action proposition 时保留，避免把“整份
财报/计划”既当独立 Mention，又与具体事实重复。

Judge split 默认继承原 draft 的 `local_package_hint` 和 `relation_to_anchor`；只有 replacement
证据明确显示独立父事件或 reaction，才修改或清空。该继承由编排层完成，不要求 Judge 增加
reasoning。

### 2.9 字段保真与信息守恒

不增加新的 model-facing checklist 字段。编排层利用原 candidate lineage 和 Judge replacement
记录以下短审计：

- `source_candidate_ids`；
- `output_draft_ids`；
- `split_count`；
- `primary_metric_ids`；
- `assertion_states`；
- `time/session present`；
- `source_claim present`；
- `package_hint inherited/overridden`。

评估器按 Gold 和源 Evidence 检查：

- metric、period、benchmark/range、basis、source claimant 是否丢失；
- product/object、participant role、action polarity 是否错位；
- split 后是否仍有 supported proposition 没有对应 replacement；
- 同一 qualifier 是否被错误复制到多个 replacement；
- umbrella 是否与具体 Mention 重复。

这些审计不直接阻断运行。只有非法结构无法物化时丢弃该 item；字段可能不完整但仍有业务价值
时交给 Judge/评估，不把质量问题扩大为文档失败。

### 2.10 节点级实施与验收

实施顺序：

1. 修正 candidate disposition 的“物理唯一”validator；
2. 增加 JSON-safe business error mapping 和有界 item replacements；
3. 增加同 code 零恢复熔断；
4. 增加按文档聚合的 missing 补处置；
5. 最后启用 Grounder/Judge 的短 Prompt 修改。

固定上游 A/B 必须分开报告：

- initial used/rejected/missing；
- 补处置 used/rejected/failed-technical；
- initial、item repair、missing recovery 各自 calls/token/latency；
- item repair 最终合法率及每 code 分布；
- candidate fan-out 与重复事实率；
- Mention P/R、compound、fragmentation、字段守恒。

除总目标外，新增门槛：

- 主 Grounder + 补处置 business coverage = 100%，但技术失败不得伪装 business disposition；
- item repair 最终合法率 ≥ 80%，若低于 50% 则该 error code 保持熔断并回到实现排查；
- missing recovery 带来的新增 Mention Precision ≥ 90%；
- candidate fan-out 生成的 replacement 中重复事实率 ≤ 1%；
- qualifier/period/benchmark/source 信息丢失率相对本轮至少下降 80%；
- 旧 compound pattern 不复现，且不能以新增 fragmentation 换取；
- Judge coverage = 100%，任何单 command 非法只影响该 draft。

## 3. 节点三：N9 / Identity 过度拆分与 assessment 级降级

### 3.1 目标与直接证据

目标：

- Atomic Pair Precision > 70%；
- Atomic Pair Recall > 80%；
- N9 MERGE Precision > 75%；
- N9 conditional MERGE Recall > 85%。

本轮 N7 稳定 Gold 子集 Recall@8=61/61，说明正确 Atomic 已进入候选；conditional MERGE
Recall 却从 87.04% 降至 55.74%。199 个 N9 task 中 45 个触发
`N9_INVALID_TASK_CREATE_NEW`，占有候选 task 的 23.20%，是 Q3 revenue、EPS 25.11、FY27
capex 等事实碎裂的最强工程根因。

这里需精确表述当前失败粒度：并非每次都把整个 model batch 一起改成 CREATE_NEW；实际是一个
Mention task 内，只要任意 candidate assessment 的 coverage、axis 或 target 不合法，就把该
Mention 的所有 assessments（包括已合法 SAME）全部替换为保守 CREATE_NEW。对业务而言仍是
“局部 assessment 错误扩大为整 task 失败”。

代码还有第二条过拆路径：一个 decision 中只要存在任意 `UNCERTAIN` assessment，最终就把该
Mention 改为 CREATE_NEW；即使另一个 candidate 已经是合法 SAME，也会被一并否定。

### 3.2 Prompt 中两股相反力量

当前 Prompt 同时告诉模型：

- identity differences 只是诊断信息，不自动拒绝，由模型自行判断差异是否足以区分事件；
- 只要比较后仍有 significant semantic uncertainty，就 CREATE_NEW。

前者给模型过大语义裁量，容易误并；后者又让一个候选的不确定性污染整条 task，容易过拆。
本轮不以更多“倾向 MERGE/CREATE_NEW”句子对冲，而删除：

> Identity differences are diagnostic observations, not automatic rejection conditions. You
> must use the provided context to determine whether each difference is sufficient to establish
> that the two items represent different events.

并把 CREATE_NEW 定义中的：

> None of the candidate events is supported by sufficient evidence to be identified as the same
> event, or significant semantic uncertainty remains after all candidates have been compared.

替换为：

> After each candidate has been assessed independently, no valid candidate is SAME_EVENT.

增加一条温和、非倾向性指示：

> Determine each candidate independently from the supplied referent, occurrence, and facet
> evidence. A difference establishes a separate event only when it identifies a different
> minimal fact; uncertainty about one candidate does not decide any other candidate's relation
> or the final action.

同时把 Output Rules 中重复的：

> `identity_differences` and `claim_conflict` are audit information and do not automatically
> override or change the final action.

替换为：

> Use `identity_differences` and `claim_conflict` only to summarize evidence already reflected
> in the axis verdicts and relation.

这样模型不再根据“差异存在”或“不确定存在”做全 task 捷径，action 由候选关系的结构化结果
推导。

### 3.3 N9 私有判断顺序

在 Identity Assessment Guidelines 前增加，不要求输出推理过程：

> For each candidate, first resolve the core referent and participant roles; then compare the
> normalized occurrence, time/session, object, action/polarity, Assertion State, and metric/facet;
> only then assign the relation. A specific numeric statement and a qualitative summary may be
> the same occurrence when these identity dimensions align and the broader wording does not
> cover additional facts.

再补两条边界：

> Different wording or granularity alone does not create a new occurrence. Treat a concise
> summary as SAME_EVENT only when it can be narrowed to the same minimal fact without absorbing
> additional objects, actions, metrics, periods, or sources.

> Participant role labels are not referents by themselves. Compare canonical entities and their
> event roles together: a harmless SUBJECT/ACTOR wording difference is not a referent conflict,
> while different issuers, objects, counterparties, analysts, or instruments may establish one.

这一顺序覆盖本轮四类真实误拆模式，但不包含具体公司或数值：

- 不同表达粒度被当成不同事件；
- 具体数值与对同一 occurrence 的定性概括被拆；
- 原始时间文本未先归一就比较；
- participant role 标签差异被误当 referent 差异。

Prompt 之外，N9 input 应优先提供已有的 canonical participant IDs、规范化 event bounds、
session、fiscal period 和 primary metric，而不是要求模型从 raw surface 重做解析。相对日期
和 session 在 N5/N6 解析失败时保留 unknown，不用 published_at 或文章日期伪补身份。

### 3.4 将 relation assessment 设为基本恢复单元

保留现有 N9 模型输出 DTO，避免再增加一套模型可见 Schema；重写 adapter 的校验顺序：

1. 先恢复 request-local mention/candidate ID；
2. 每个 raw candidate assessment 单独归一和校验；
3. axis 单独归一；
4. 收集合法 assessments 后，编排层重新计算 relation 派生字段、action 和 target；
5. 最后才构造持久化 `AtomicAssignmentDecision`。

#### 可确定性规范化

以下不应触发 assessment 失败：

- enum 大小写、首尾空格；
- non-applicable axis：删除并审计；
- 完全相同的重复 axis/assessment：去重；
- `related_candidate_event_ids`、`possible_duplicate_atomic_ids`：从 relation 重新派生；
- 唯一合法 SAME 存在但 target 缺失/抄错：恢复该 SAME candidate；
- `claim_conflict` 缺失：默认 false；
- `identity_differences` 缺失：默认空；非字符串项删除。

#### Axis 处理

- 未知 axis 或不适用于双方的 axis：只删除该 axis；
- 重复且 verdict 相同：去重；
- 重复且 verdict 冲突：只把该 axis 标为非法，不丢整个 assessment；
- exact signature 的适用 axis 可确定性补 MATCH；
- 已启用 Sidecar hard rule 的 deterministic conflict 可补 CONFLICT，并禁止该 candidate
  保持 SAME；
- 非 enforce axis 缺失只形成 `AXIS_COVERAGE_PARTIAL` 审计，不否定一个其他字段均合法的
  relation；
- enforce axis 缺失且无法确定性恢复时，该 candidate assessment 才不可用于 SAME，但其他
  candidate 继续有效。

#### Assessment 失效条件

只有以下情况丢弃单 candidate assessment：

- candidate ID 无法映射到本 task；
- relation 无法规范化；
- 同一 candidate 返回互相矛盾且无法择一的 assessments；
- relation=SAME 但 enforce conflict axis 明确为 CONFLICT；
- relation=SAME 且必需 enforce axis 完全缺失、又无法确定性恢复。

assessment 被丢弃不等于 candidate 是 UNRELATED，不能伪造语义。审计记录：

```text
mention_id
candidate_id
error_code
axis (optional)
normalization
```

不记录长 exception 或模型原 reasoning。

### 3.5 从合法 assessments 重新计算最终 action

不再信任 model action/target 的结构一致性作为整 task 生死开关。归一后按以下规则推导：

```text
存在一个或多个合法 SAME_EVENT：
  action = MERGE
  merge_target = 原 target（若仍是合法 SAME），否则按既有 target priority 选最优合法 SAME
  其余合法 SAME → possible_duplicate_atomic_ids

没有合法 SAME，但存在 RELATED_NOT_SAME：
  action = CREATE_NEW
  reason = N9_RELATED_CREATE_NEW

没有合法 SAME/RELATED：
  action = CREATE_NEW
  reason = N9_NO_VALID_SAME_CREATE_NEW
```

单个 `UNCERTAIN` 不再覆盖同 task 中的合法 SAME。只有“无合法 SAME 且至少一个
UNCERTAIN”才进入定向 M3 escalation；M3 只接收这些 task，不重判同 batch 已稳定 task。
若 M3 仍非法，保留 M2 的合法 assessments，再按上表推导；不能把技术失败伪装成所有候选
UNRELATED。

真正允许 whole-task 降级的情况只剩：

- 没有可恢复 mention ID；
- 整个 decisions 容器不是可枚举结构；
- 该 Mention 的所有 candidate IDs 均无法恢复。

即使如此也只降级该 Mention，不影响同 request 的其他 tasks。

### 3.6 校验严格度的平衡

必须保留的硬一致性：

- MERGE 最终 target 必须是输入 candidate，且归一后 relation=SAME；
- CREATE_NEW 最终 target 必须为空；
- enforce conflict 不能进入 SAME；
- request-local ID 不能构造、猜测或跨 task 引用；
- exact signature 不能在同一已提供 axis 上输出 CONFLICT。

由硬阻断降为局部规范化/审计：

- 所有 applicable axis 必须逐一完整返回；
- `related_candidate_event_ids` 与 assessments 完全相等；
- `possible_duplicate_atomic_ids` 必须由模型精确列全；
- non-applicable/duplicate axis；
- 非身份辅助字段的类型小错。

这些字段错误不会让下游完全失去业务价值，因此不应再触发整 task CREATE_NEW。

### 3.7 Apply 内受限 singleton 吸收

当前 N9 已能识别多个 SAME candidate，并把非 selected SAME 放入
`possible_duplicate_atomic_ids`，但 Apply 只把 incoming Mention 加到一个 target，其余
Atomic 仅留审计，造成明知重复仍碎裂。

增加一次受限、非传递吸收：

1. 先按现有流程把 incoming Mention 合入 selected target；
2. 仅遍历本次 N9 decision 直接给出的其他合法 SAME candidates；
3. selected target 可为任意大小；
4. duplicate candidate 当前必须恰好只有 1 个 Mention；
5. duplicate 与 target 之间不得触发已启用的 Sidecar enforce rule；
6. duplicate 若已有 Package membership，只在“无 membership”或“与 target 属于同一当前
   Package root”时吸收；不同 Package 时仅保留 possible-duplicate 审计，避免借 Atomic
   修复暗中合并 Package；
7. 将 singleton 的唯一 Mention 加入原 selected target，保存 source→target atomic redirect；
8. 所有吸收始终指向原 selected target，不把新吸收项作为下一跳 target。

明确不做：

- cluster ↔ cluster；
- multi-member ↔ multi-member；
- 通过 duplicate 的 duplicate 继续闭包；
- union-find 或全图传递合并；
- 因一个污染簇吸收其周围所有相似 singleton。

若同一 decision 有多个 singleton duplicate，可逐个直接吸收到原 target，但每个都独立通过
上述检查。审计只记录
`incoming_mention_id/source_singleton_id/target_id/source_mention_id/rule_check/package_check`。

需要同步保证：

- redirected Atomic 不再出现在 current head；
- N11/N12 读取 member 时先解析 atomic root；
- 同一 Package 内 source ID 替换为 target ID并去重；
- 历史 assignment 保留 source ID，通过 redirect 可追溯，不改写历史。

### 3.8 为什么旧过拆仍复现

- N7 本轮正确候选 recall 已达 100%，所以不是“召不回来”；
- tri-axis 新校验把诊断完整性提升成了整 task 生死条件，修复方向过严；
- `UNCERTAIN` 的全 task CREATE_NEW 是 Prompt 倾向在编排层的再次放大；
- `possible_duplicate_atomic_ids` 只有审计、没有受限 Apply，模型已经发现的重复无法闭环；
- time/session、participant role、metric/basis 等虽已生成，N9 input 与判断顺序仍未稳定使用
  canonical 值。

因此不回滚 N7、tri-axis DTO 或 QuantityRole；应收缩 validator 的破坏半径，并让已存在的
结构化信息真正参与推导。

### 3.9 实施顺序与验收

1. 先落 assessment/axis 独立 validator 与短错误审计，不改 Prompt；
2. 再改 action/target 派生和 `UNCERTAIN` 局部语义；
3. 固定本轮 N9 原始输出做离线 replay，确认 45 条 fallback 中可恢复多少合法 SAME；
4. 实施 singleton absorption，并单独 replay membership/redirect；
5. 最后对 Prompt 做固定上游 A/B，避免把 validator 收益误归因于文案。

验收除四个总目标外，还要求：

- `N9_INVALID_TASK_CREATE_NEW` 相对 45 条下降至少 90%，理想为 0；
- 非法 candidate/axis 100% 有短 error audit；
- 合法 SAME 因同 task 其他非法 assessment 被丢失 = 0；
- 合法 SAME 因其他 candidate UNCERTAIN 被丢失 = 0；
- singleton absorption 的 merge precision ≥ 95%，且不产生跨 Package 隐式合并；
- Q3 revenue、EPS 25.11、FY27 capex、after-hours、SCA、gross margin 等事实组的
  fragmentation 明显下降，同时错误跨 metric/session/basis merge 不上升；
- N7 Recall@8 不下降；
- M3 escalation task 数和 Token 单列，不能用整 batch 重判掩盖局部修复收益。

## 4. 节点四：N9 / Identity 错误合并与 Sidecar 闭环

### 4.1 根因不是单一 Prompt 缺口

本轮典型误并具有共同模式：

- 相同报告、财报、商业计划或 parent episode 被当作 Atomic identity；
- Apple 其他产品涨价与 iPhone 价格不变被视为“same overall pricing action”，忽略 object 和
  `RAISE/UNCHANGED` polarity；
- Apple intraday -0.56% 与 close -5%/-6.12% 被当作 claim value conflict，忽略 session/
  occurrence；
- Sandisk SCA 与 Micron SCA 的 incoming/candidate referent 已冲突，模型却把三轴写成 MATCH；
- Needham 的具体 target/rating 被泛化的“analysts changed targets” umbrella 吸收，source/
  report identity 没成为稳定边界；
- Q3 revenue、EPS、capex 已有正确 Field link，却得到同一个 GENERIC_OPEN Sidecar signature。

它们由四层缺口叠加：

1. **已解析字段未进入 Sidecar**：`IdentityCompiler.compile()` 在计算可信
   `primary_metric_id` 前就编译 Sidecar；`GENERIC_OPEN._open_facets()` 又直接返回空。
2. **关键规则仍为 shadow**：本轮 `ATOMIC_HARD_CANNOT_LINK_OBSERVED` 的
   `enforced=false`，`AtomicMergeInvariant` 的 `enforced_rules=[]`。
3. **axis 与 relation 可自相矛盾**：模型可返回 `OCCURRENCE/FACET=CONFLICT` 同时
   `relation=SAME_EVENT`，validator 仍允许 Apply。
4. **模型退回 container 捷径**：结构化 identity 不足时，共同报告/主题比 object、polarity、
   session、source artifact 更显眼。

所以本节点采用“补输入合同 → relation 一致性 → 显式 allowlist 窄启用 → 最短必要 Prompt”
的顺序。详细规则边界以
`CDECR_ATOMIC_HARD_RULE_COUNTERFACTUAL_REPLAY_20260730.md` 第 8 节为准，本节将其纳入本轮
整体实施次序。

### 4.2 GENERIC_OPEN 补齐高可信 FACET / REFERENT / OCCURRENCE

调整 N6 编译顺序：

1. 先计算 PRIMARY metric、principal company 和其他已解析 canonical roots；
2. 再把这些 discriminants 传给 `compile_atomic_identity_sidecar()`；
3. 最后计算包含 Sidecar signature 的 processing key。

#### 本轮进入可 enforce 轴的字段

- 唯一 PRIMARY Quantity；
- namespace 为 METRIC；
- external core ontology root，或已稳定归并到该 root 的 internal canonical root；
- principal company/referent 为 external root 或稳定 internal canonical root；
- unresolved、provisional、仅 supporting quantity、XBRL 词面近似但未映射 core root均不参与
  enforce。

GENERIC_OPEN 示例：

```text
Q3 revenue → FACET [metric:revenue]
Q3 EPS     → FACET [metric:eps]
Q3 capex   → FACET [metric:capex]
```

#### 举一反三但首轮只 shadow 的字段

| Canonical 字段 | Sidecar 投影 | 首轮行为 |
| --- | --- | --- |
| principal company | REFERENT | 可供 COMPLETE_REFERENT 使用 |
| resolved location | OCCURRENCE | N9 可见 + shadow warning |
| product/facility/project/asset/technology/program | FACET | N9 可见 + shadow warning |
| analyst/action-artifact adapter 下的 report/artifact ID | OCCURRENCE | N9 可见 + shadow warning |
| canonical predicate/action polarity | OCCURRENCE | 保持现有映射，暂不 hard enforce |
| fiscal period | OCCURRENCE | 保持现有映射 |

不加入 rating、lifecycle stage、无稳定 root 的 open attribute；不因“举一反三”把所有 Field
都塞入 Sidecar。新增投影只复用现有 Field Registry，不新增 LLM 字段或解析节点。

### 4.3 四条允许启用的规则

新增显式 `atomic_enforced_rules` allowlist，默认只允许：

```text
ASSERTION_STATE
COMPLETE_REFERENT
METRIC
PRIMARY_METRIC_FAMILY
```

空 allowlist 可立即回到全 shadow。移除当前构造器把全局 enforce 强制改回 shadow 的临时
逻辑，但也不能恢复一个全局开关一次启用所有 legacy conflicts。

#### `PRIMARY_METRIC_FAMILY`

双方都有可解析 PRIMARY metric family 且 family 不同时阻断，例如 revenue、EPS、capex、
gross margin、free cash flow、profit。family 缺失或 unresolved 时不阻断。

#### `METRIC`

family 相同但可信 canonical metric root 仍不同时阻断，例如 total revenue 与 product
revenue。两条 metric 规则同时命中只 lock 一次，审计保留两个 code。Accounting basis、
comparison/bound 不借 metric 规则扩大。

candidate 已是混合 cluster 时采用用户确认的放宽边界：只要 candidate 中仍存在与 incoming
相同的可信 metric root，就不由 metric 单项锁死，同时写 `MIXED_CANDIDATE_METRIC`；只有
candidate 所有已知 PRIMARY metric roots 都与 incoming 不同才阻断。它避免把已有污染簇中的
正确分支一并拒绝，但必须在验收中单列由此新增的污染 pair。

#### `COMPLETE_REFERENT`

双方各自至少有一个 non-unresolved principal referent，且 canonical referent 集合完全不相交
时阻断；external 与稳定 internal root 均可使用，不要求完整 schema projection。

candidate 只要仍有一个与 incoming 相同的 principal referent，就不因其他污染 referent 锁死，
写 `MIXED_CANDIDATE_REFERENT`。这是对 Sandisk/Micron、Roundhill/Defiance 等完整不同
referent 的窄边界，不是“任意 participant 不同就拒绝”。

#### `ASSERTION_STATE`

只在以下两组之间 hard conflict：

```text
realized:    ACTUAL, ONGOING
prospective: PLANNED, EXPECTED, HYPOTHETICAL
```

组内不因 assertion 单项阻断。任一侧 UNKNOWN，或 RUMORED/DENIED 与其他状态，继续交 N9；
candidate 有多个状态时，只要存在与 incoming 同组或相同 state 就不由 assertion 单项锁死，
所有可判 state 都落入对立组时才阻断。

继续保持 shadow：

- MARKET_MEASURE；
- MARKET_SESSION；
- ISSUER；
- ANALYST_INSTITUTION；
- ACCOUNTING_BASIS；
- legacy EVENT_TIME、NORMALIZED_PREDICATE 及其余 conflict。

特别是 MARKET_MEASURE 的旧回放阻止 2 个 FP 却误杀 3 个正确 after-hours pair，不能因本轮
仍有 market bad case 就硬启用。

### 4.4 区分 shadow warning 与 enforce conflict

N9 candidate input 增加：

- `canonical_conflict_axes`：全部 deterministic shadow warnings，可由模型基于证据覆盖；
- `enforced_conflict_axes`：仅由上述 allowlist 规则产生，模型不能覆盖。

当前 Prompt 句子：

> Every axis listed in `canonical_conflict_axes` must be CONFLICT, not AMBIGUOUS.

替换为：

> `canonical_conflict_axes` are deterministic warnings; use the evidence to confirm or override
> them.

> Every axis in `enforced_conflict_axes` must be `CONFLICT`, and any candidate with a `CONFLICT`
> axis must not be `SAME_EVENT`.

这一修改与第 3 节 assessment 级 validator 合并实现：

1. 模型自己把任一 axis 判为 CONFLICT，则该 candidate 不得保持 SAME；
2. 规范化为 RELATED_NOT_SAME，保留原 axis 与 identity difference；
3. 若它是 target，改选同 task 另一个合法 SAME；
4. 没有替代才对当前 Mention CREATE_NEW；
5. enforced axis 即使被模型写成 MATCH/AMBIGUOUS，Apply guard 仍阻断；
6. shadow warning 被模型覆盖为 MATCH 时只审计，不 hard block。

这会直接封闭 S&P “模型 reasoning 和 axis 都认为 occurrence 不同、relation 却 SAME”的路径，
且不会触发整 task repair。

### 4.5 防止 container/episode 取代 Atomic identity

现有 Prompt 已说 Atomic 不是 article topic 或 whole disclosure package，但重复 bad case
说明还需把 container 与最小事实的关系说得更直接。在 Business Context 后增加：

> A shared report, filing, call, commercial plan, or parent episode may place facts in one Event
> Package; it is not Atomic identity. SAME_EVENT requires the same minimal subject/object,
> action and polarity, occurrence/session, Assertion State, and metric/facet.

在 claim conflict 段增加：

> Apply `claim_conflict` only after the identity axes establish the same minimal fact. Do not use
> it to absorb a different issuer/referent, object, action polarity, session/occurrence,
> Assertion State, metric/facet, analyst institution, or source artifact.

在 High-Risk Boundaries 增加：

> A generic multi-source or umbrella summary is not the same Atomic Event as a named-source
> action unless it resolves to that one minimal action and covers no additional source, object,
> metric, or occurrence.

这些句子分别覆盖 common container、Apple object/polarity、intraday/close、Sandisk/Micron 和
Needham umbrella 的通用根因；不写公司名和测试数值，不扩成长 checklist。

### 4.6 Apply guard 与污染簇

Apply 对所有 N9 SAME candidates 逐一执行 allowlist：

- selected target 被锁，尝试下一个未锁 SAME；
- 全部锁定才 CREATE_NEW；
- 不重跑 N9，不影响同 batch 其他 Mention；
- 第 3 节 singleton absorption 也执行同一 allowlist。

对 candidate sidecar 为多成员并集时，不能把“任一 member 冲突”直接解释为整个 cluster 冲突：

- metric/referent/assertion 只要存在兼容分支，按 4.3 的放宽边界允许 N9 继续判断；
- 所有已知分支均不兼容才 lock；
- 记录兼容 roots、冲突 roots 和 selected branch，不增加模型 reasoning；
- 不在本轮自动拆既有污染簇；污染簇通过重跑新 policy 或专门 migration 处理，避免在 Apply
  中叠加隐式 cluster surgery。

### 4.7 为什么旧 bad cluster 仍复现

| 旧问题 | 上轮动作 | 本轮复现原因 | 本轮闭环 |
| --- | --- | --- | --- |
| agreement/SCA supercluster | Sidecar + supercluster guard | guard 全部 shadow；artifact/facet 不完整 | COMPLETE_REFERENT enforce；object/artifact shadow；container Prompt |
| revenue/EPS/capex | metric conflict shadow | PRIMARY metric 未进入 GENERIC_OPEN FACET | 编译顺序 + METRIC/FAMILY enforce |
| guidance cluster | assertion/metric sidecar | assertion 过宽不能直接开；metric仍缺 FACET | 分组 assertion + metric rules |
| intraday/close | session/occurrence shadow | 模型把时点和值降为 claim conflict | session 投影 + axis/relation 一致性；session rule仍shadow |
| Sandisk/Micron | referent shadow | 模型覆盖三轴且 validator不拦 | COMPLETE_REFERENT + enforced axes |
| Needham umbrella | analyst/source信息弱 | generic summary 与具体报告共享 topic | artifact投影 shadow + umbrella Prompt + conflict consistency |

上一轮大部分机制处在“只记录、不执行”或“字段生成了但没编译进身份”的中间状态。整套回滚会
丢失已有信号，正确做法是完成闭环并保持逐规则回滚。

### 4.8 审计、版本与开关

新增/扩展编排审计：

```text
enforced_rules
shadow_rules
enforced_conflict_axes
model_axis_relation_normalized
original_relation
final_relation
alternate_target_used
final_action
```

shadow deterministic conflict 被模型覆盖记 `ATOMIC_AXIS_DETERMINISTIC_OVERRIDE`；
`CONFLICT + SAME_EVENT` 被规范化记 `ATOMIC_AXIS_RELATION_CONSISTENCY_NORMALIZED`。

写入 processing key：

- Sidecar compiler version；
- Identity Compiler version；
- Atomic Merge Invariant policy version；
- Prompt version；
- enforced-rule allowlist hash。

审计不向模型新增 reasoning 字段。

### 4.9 实施与验收

Phase 1（全 shadow）：

1. 调整 N6 编译顺序；
2. 注入高可信 metric/company/location/object/artifact；
3. 增加 `enforced_conflict_axes` 合同但 allowlist 为空；
4. 完成 axis/relation 一致性和第 3 节 assessment 级恢复；
5. 用本轮固定 N9 输出离线 replay，验证输入覆盖与一致性，不改变原 Gold 控制变量。

Phase 2（逐条 canary）：

1. PRIMARY_METRIC_FAMILY；
2. METRIC；
3. COMPLETE_REFERENT；
4. 分组后的 ASSERTION_STATE；
5. 单条和组合回放通过后，再进入 30 篇真实模型验收。

必须通过：

- GENERIC_OPEN revenue/EPS/capex FACET 分离；
- `CONFLICT + SAME_EVENT` 进入 Apply = 0；
- 非 allowlist enforced count = 0；
- Q3 revenue/EPS/capex 已知误并 = 0；
- reaction/session、basis、analyst 等 shadow 规则不因 validator 被间接 hard-enable；
- 每条 rule 单列 triggered、FP prevented、TP blocked、Pair P/R 变化；
- 任一规则 Gold `TP_blocked > 0` 即独立退回 shadow；
- 四条组合后 Atomic Pair P/R、N9 MERGE P/R 同时达到本轮总目标；
- 33-member supercluster 不复现，hard-conflict violation 继续下降；
- 新增 singleton/fragmentation 不能抵消第 3 节恢复收益；
- 新增文档失败和整 task fallback = 0。

规则回滚只清空对应 allowlist，不回滚 Metric FACET、canonical 字段投影或 axis/relation 一致性。

## 5. 节点五：Judge repair 仍非法与 coverage 恢复

### 5.1 当前机制为何“检测到了但修不动”

本轮有 3 次 `judge_repair`：

- D03 `GUIDANCE_ASSERTION_CONFLICT`：repair 后仍非法，最终保留 ACTUAL guidance；
- D27 `GENERIC_UMBRELLA_DUPLICATE`：repair 后仍非法，最终保留错误 compound/umbrella；
- 另有一条成功捕获的 guidance conflict。

当前 `_judge_semantic_failures()` 实际能得到：

```text
short draft ID → {business error codes}
```

但随后只把所有 code 合并成一个字符串抛给通用 `_invoke_typed()`；通用 repair 收到整个 Judge
request、整个 invalid batch output 和类似
`guidance_assertion_conflict|generic_umbrella_duplicate` 的字符串，不知道：

- 具体哪一个 `d#` 非法；
- 哪个字段触发；
- 对应的 Grounder draft 是什么；
- umbrella 与哪些 peer 具体事实冲突；
- 应 REJECT、SPLIT 还是改 assertion。

repair 因而重写整个 batch，可能扰动其他合法 decisions。二次失败后，代码把非法 ID 回退为
`ACCEPT_GROUNDER_ITEM`；这正确避免了文档失败，但把原 compound/assertion 错误继续送入
N7/N9。

另一个独立问题是 coverage：D03 缺 d4，D10 缺 d2/d4/d5/d6/d7/d8。当前只记录
`JUDGE_COVERAGE_DEGRADED`，再默认 ACCEPT 原 Grounder draft；这不是 Judge 真正完成了审核。

### 5.2 先做安全确定性修正

在调用 repair 前只处理可以无歧义修正的情形：

- 空 `changes` 删除，而不是 invalid；
- reason 超长继续截到协议上限；
- enum 大小写/未知 enum 按现有安全规则规范化；
- 完全相同的重复 command 去重；
- `GUIDANCE_EXPECTATION + guide_metric + ACTUAL` 确定改为 EXPECTED，因为当前合同规定
  `guide_metric` 表达 underlying forecast；若表达“发布 guidance”这一已发生动作，predicate
  应先是 issuance/announce action，不能靠 ACTUAL 掩盖谓词错误；
- ACCEPT 的某个 optional field change 不可物化时只回退该 field 到原值，保留其他合法
  changes。

不确定性高的 compound、umbrella、opposing action、duplicate target、attribute merge 不做
确定性语义修复。

### 5.3 单 decision 定向 repair

废除 semantic failure 的整 batch repair；正常 Judge 主调用仍按现有 batch 运行。对每个失败
short ID 创建一个 item repair，彼此可并行，合法 Judge decisions 同时进入物化，不等待无关
repair。

repair input 只包含：

- `target_id`；
- 该 Grounder draft；
- 当前非法 Judge command 或其归一后结果；
- `business_errors`；
- 该 draft 的局部 source context；
- 只有业务错误需要时才提供的 peer summaries 和 peer IDs。

错误对象示例：

```json
{
  "target_id": "d3",
  "code": "GENERIC_UMBRELLA_DUPLICATE",
  "field_paths": ["canonical_proposition", "quantities"],
  "peer_ids": ["d1", "d2"],
  "required_correction": "Reject an exhausted umbrella, or narrow/split it to independent facts."
}
```

不传整篇原文、全 batch output、raw Pydantic ctx 或 traceback。

repair 输出复用 `JudgeCommandOutput`，但 validator 要求五个 command array 的总 command 数
恰好为 1，且 ID 必须等于 `target_id`。这样不另建 action union Schema，也不能借 repair 改
其他 draft。

repair Prompt：

> Repair exactly one Judge decision using `business_errors`. Return exactly one command for
> `target_id` and no command for any other ID. Preserve every source-supported fact and exact
> evidence; apply the error-specific correction rather than only rewriting the reason. If the
> draft is compound, SPLIT it into complete atomic replacements. If a generic umbrella is
> exhausted by the supplied peer facts and has no independent artifact or action, REJECT it.

错误指导放在短结构化 `required_correction` 中，不把每种 error code 的长解释重复写进 system
Prompt。

### 5.4 各业务码的 repair 边界

| Code | 允许修复 | 不允许的捷径 |
| --- | --- | --- |
| `GUIDANCE_ASSERTION_CONFLICT` | 改 EXPECTED，或把实际 issuance action/predicate 表达正确 | 只改 reason；把 forecast 保留 ACTUAL |
| `MULTIPLE_PRIMARY_METRICS` | SPLIT 为完整 metric-specific Mentions | 把独立 metric 降 SUPPORTING |
| `OPPOSING_CORE_ACTIONS` | 按 object/subject/action polarity SPLIT | 删除一侧动作 |
| `OPPOSING_SUBJECT_ACTIONS` | 按主体和动作 SPLIT | 把所有主体复制到每个 replacement |
| `LIKELY_FRAGMENTATION` | 保留真正不同事实；重复 fragment 改 DUPLICATE/ATTRIBUTE | 因字段相似机械合并不同事件 |
| `GENERIC_UMBRELLA_DUPLICATE` | 无独立事实则 REJECT；有则窄化；compound则 SPLIT | 保留 shared report/topic 作为独立 Atomic fact |

每个 item 最多一次 repair。repair 后仍非法：

- 只回退该 Grounder item；
- reason 明确为 `SEMANTIC_REPAIR_FAILED_RETAIN_GROUNDER_ITEM`；
- 记录 `target_id/code/final_error`；
- 不重试整 batch，不阻塞其他 draft 或文档。

Fallback 必须继续存在，因为错误 Mention 仍可能比整条丢失更有后续修复价值；但验收中计为
quality degradation，不能计作 repair success。

### 5.5 Judge coverage 补判

主输出归一后：

- extra ID 丢弃；
- 完全相同 duplicate command 去重；
- 同一 ID 的冲突 commands 进入该 ID 的 item repair；
- missing IDs 不再直接默认“Judge 已审核通过”。

将同一 Judge batch 的 missing IDs 聚成一次 `judge_coverage_recovery`，只提供这些 drafts 和
必要局部 context，要求每个 ID 恰好一个 command。Prompt：

> Return exactly one Judge command for every supplied missing draft ID. Review only these drafts
> under the same Eventhood, Atomicity, field-correctness, and consolidation rules; do not return
> any other ID.

补判仍不超过现有 `JUDGE_DRAFT_BATCH=24`。若补判 command 非法，才转对应 item repair；仍失败
则局部 Grounder fallback。这样 D10 的 6 个 missing 只需一个补判 request，不是 6 次独立
正常审核，也不会重跑原合法 18 条。

### 5.6 与第 2 节 Mention 规则对齐

Judge 的正常 Prompt 只采用第 2.8 节两句信息守恒/身份边界修改；repair Prompt 使用本节短
版本。两者共享同一业务定义：

- candidate lineage fan-out 可产生多个 atomic replacements；
- split 后每个 replacement 只继承适用于自己的 object/metric/period/source；
- `local_package_hint` 默认由编排层继承；
- shared report/plan/topic 是 Package context，不自动成为独立 Mention/Atomic identity。

若 Grounder Prompt 允许拆分而 Judge repair schema 仍只能返回一个 Mention，模型从空白上下文
无法同时满足两者；因此有界 SPLIT 是必要合同对齐，不是额外业务分支。

### 5.7 审计、成本与验收

审计新增：

```text
target_id
business_error_code
initial_action
repair_action
repair_final_valid
fallback
peer_ids
```

不新增模型 reasoning。节点指标分开统计：

- main Judge；
- `judge_item_repair`；
- `judge_coverage_recovery`。

门槛：

- Judge decision coverage = 100%；技术失败单列，不能伪装已审；
- 已触发 semantic repair 的最终合法率 ≥ 90%；
- `GUIDANCE_ASSERTION_CONFLICT` repair 后残留 = 0；
- known `GENERIC_UMBRELLA_DUPLICATE` 修复率 ≥ 80%；
- 合法 sibling decisions 因 repair 发生变化 = 0；
- repair/fallback 不造成文档失败；
- item repair 总 Token 低于“重写整个 Judge batch”的反事实 payload；
- Judge 修复后的 Mention P/R、compound/fragmentation 必须进入第 2 节总门槛，不能只用
  Schema valid rate宣称成功。

## 6. 节点六：Package anchor、N11、N12 与 N13

### 6.1 本轮下降不是单一 N13 判断问题，而是 parent identity 在上游丢失

本轮 Package Pair Precision/Recall 为 `80.43% / 38.95%`，Recall 环比下降
`34.59pp`。真实输出中 61 个 Package 有 59 个没有 anchor，199 条 Mention 只有
10 条带 `local_package_hint`，且部分 hint 只是泛文本。当前代码进一步放大了这个缺口：

1. `package_seed_for_event()` 使用 `next(...)`，Atomic 有多个 Mention 时只拿第一个
   `local_package_hint`，其余 hint、relation 与支持来源全部丢失；
2. N11 只把已经存在的 raw hint 解析为 artifact 或 `PACKAGE_ANCHOR`，没有对同文档不同
   表述进行 parent-level 归一，也没有把 source fingerprint、Evidence block、issuer、
   Package family 和 period 组合为稳定边界；
3. EventPackage 虽然保存 `package_anchor_ids`，却没有 `primary_anchor_id` 和
   `anchor_conflict`；多 artifact 聚合时把 `anchor_artifact_id` 清空，导致“存在冲突”
   在下游看起来像“完全没有 anchor”；
4. N12 已为 `CANONICAL_ARTIFACT / PACKAGE_ANCHOR / PARENT_CONTEXT` 预留两个候选位，但
   上游 anchor coverage 太低，候选位大多无信号可用；
5. N13 的 profile hash 包含整包及 summary 等易变内容，Package 每次小变化都可能让同一
   Package-ID pair 重新请求；本轮虽无相同 profile pair 重复调用，Package-ID pair 仍重复
   评估 235 次；
6. `MIXED_PACKAGE_REQUIRES_SPLIT_OR_REVIEW` 把 mixed 直接确定性判为 DIFFERENT，
   `PackageBoundaryGate` 又把 review 映射为 FROZEN；边界修复拆出 reaction 后，N13 仍可能
   根据 SAME 组件把它重新并回。

因此本节不通过“把相似 Package 更容易合并”追 Recall。核心是让同一 parent 的证据从
Mention 一直保留到 N13，同时把 reaction、不同 artifact 等边界留在正确层级。

### 6.2 Grounder：提高 parent anchor 产出，但不把开放世界压成枚举

替换现有 Grounder 的 `Optional local_package_hint...` 段落，使用用户确认的短定义：

> Provide `local_package_hint` when the evidence identifies a parent occurrence or matter
> containing this Mention. Reuse the same short, distinguishing anchor for Mentions under the
> same parent. Do not use an entity, ticker, broad topic, article, Package ID, or the Mention
> itself as the anchor. Leave it null ONLY when no parent boundary is supported.
> `relation_to_anchor` points from the Mention to the parent.

不增加行业或文档类型枚举，不要求凭空生成 parent，也不把“文章讲了同一主题”当作 parent。
该字段只表达证据支持的上位发生、披露容器或持续事项。Judge 发生 SPLIT 时，编排层默认把
原 hint 和关系复制到每个 replacement；只有 replacement 明确是独立 parent 或
market/analyst reaction 时，才由其自身结果覆盖或清空继承值。

实施该 Prompt 时允许进行一次 5 样本真实模型小测，但它是未来实施步骤，不在本方案撰写轮
执行。样本按业务类型选取而不按已知 bad case 选取：

1. 同一披露下多项事实；
2. 协议、交易或持续事项；
3. parent 及其市场反应；
4. 具体 analyst report 与泛分析师概述；
5. 证据确实不支持 parent 的独立事件。

检查项是：同 parent 复用、reaction 关系方向、无 parent 时为空、不得使用 entity/ticker/
topic/title/Mention 复述。5 个样本用于发现 Prompt 合同歧义，不能作为正式质量门槛或据此
新增测试集专用词。

### 6.3 N11：只维护一套 canonical parent anchor

不建立 Mention anchor、Atomic anchor、Package anchor 三套 registry。扩展现有 Field
Registry 的 `PACKAGE_ANCHOR` 解析和编排侧 link metadata，所有层级引用同一种
`canonical_anchor_id`：

```text
mention_id
canonical_anchor_id
relation_to_anchor
resolution_method
raw_hint_hash
source_fingerprint
evidence_block_ref
issuer_id
package_family
period_id
```

其中支持 Mention、Source 和推导依据均在编排层留存，不新增 LLM reasoning。解析顺序：

1. raw hint 能唯一命中 artifact KB：复用 artifact identity，标为 `KB_EXTERNAL`；
2. 未命中 artifact：仅在同一文档内，依据 normalized hint、source fingerprint、相邻
   Evidence block、issuer、Package family 与 period 做联合归一；
3. 同文档不同 wording 若由上述证据指向同一 parent，生成同一个 source-scoped 稳定 ID；
4. 只有 raw hint 文本相似、尤其是 `latest report` 等泛文本时，不跨文档合并；
5. 两个 source-scoped provisional anchor 来自不同文档且尚无共同 artifact 时，关系是
   UNKNOWN，不是假定冲突，也不是假定相同。

稳定 ID 的输入包含 source fingerprint 和规范化 parent evidence group，而不是仅包含 raw
hint。这样既能合并同文档措辞差异，也不会让泛 hint 成为跨文档自动 merge key。

### 6.4 从 Mention 到 Package 改为集合聚合

用一个轻量 `ParentAnchorAggregate` 值对象替换“取第一个 hint”的逻辑：

```text
anchor_ids
primary_anchor_id
anchor_conflict
relations_by_anchor
supporting_mention_ids
```

聚合规则逐层一致：

```text
Mention anchors
    → Atomic 汇总全部成员 Mention anchors
    → Package 汇总全部成员 Atomic anchors
    → Package merge 继续取并集
```

`primary_anchor_id` 只在所有有效证据归一到同一 canonical root 时设置。存在多个已确认不兼容
的 trusted anchors 时，保留完整 `anchor_ids` 且 `anchor_conflict=true`；多个尚未判定关系的
provisional anchors 保留集合、`primary=null`、`conflict=false`。UNKNOWN 不能被错误升级为
冲突。现有 `anchor_artifact_id` 暂时保留为兼容投影：仅当 primary 是 trusted artifact 时
赋值，后续业务逻辑不再把它当作唯一 anchor 容器。

EventPackage 持久化模型增加默认兼容字段：

```text
primary_anchor_id: str | null = null
anchor_conflict: bool = false
```

旧记录无迁移阻塞：读取时默认空/false，重建 profile 时再补齐。任何 member 新增、移除、
移动、Package merge、boundary repair 都调用同一个 aggregate 函数，避免各路径各自实现。

### 6.5 N12：共享 anchor 保证进入候选，不直接保证 MEMBER

维持现有 `PACKAGE_TOP_K` 和模型 batch，不通过扩大复杂 LLM batch 提召回。候选槽位调整为：

- incumbent 最多 1 个；
- 共享 canonical anchor 最多 2 个，独立于 embedding 排名预留；
- same-source/parent-context 最多 1 个；
- member identity/embedding 最多 2 个；
- 剩余位置由综合分数补齐并去重。

现有代码已经大致按这个结构选位，实施重点是让新 canonical anchor route 优先于 raw
`local_anchor_hint`，并增加“正确 target 是否因配额被截断”的离线审计。共享 anchor 只保证
召回，不做 deterministic MEMBER。N12 Prompt 在 “No single field...” 段落后增加：

> A shared canonical parent anchor is strong candidate evidence, but `relation_to_anchor` and
> event family still determine membership: disclosure content may be `MEMBER`, while market or
> analyst reactions are normally `EXTERNAL_RELATED`. Similar raw hint text alone is not parent
> identity.

这条约束同时保护 Recall 与 Precision：同一 parent 的财务指标、管理层表述可进同包；市场或
分析师反应即使指向该 parent，也默认是外部关系，不因共享 anchor 被吸收入包。

### 6.6 回滚 mixed deterministic DIFFERENT，移除 FROZEN

按用户要求做以下回滚：

1. `_package_pair_external_guard()` 不再返回
   `MIXED_PACKAGE_REQUIRES_SPLIT_OR_REVIEW`；mixed pair 进入正常 N13 compare；
2. N13 Prompt 把当前包含 `mixed Packages remain separate pending review` 的硬边界句拆开，
   保留已验证 hard boundaries，并用以下句子替代 mixed 结论：

   > For a mixed Package, compare the supported core parent evidence; mixed membership alone
   > does not decide `SAME_PACKAGE` or `DIFFERENT_PACKAGE`.

3. `PackageBoundaryGate` 不再生成 `FREEZE_PACKAGE`，review finding 保持
   `REVIEW_REQUIRED` 审计，但 Package 运行态仍为 ACTIVE；
4. `PackageQualityState.FROZEN` 停止新写入并从 repairable/recall 分支中移除。兼容读取旧
   FROZEN 时一次性按当前成员重建为 ACTIVE 或在真正结构不可用时 QUARANTINED，不新增阻塞性
   migration。

保留 QUARANTINED 仅用于没有成员、互相矛盾的已执行 merge component、多个明确冲突 trusted
artifacts 等下游无法安全使用的结构错误；“需要进一步审阅”本身不再冻结流程。

### 6.7 N13：anchor 强化召回，同时收窄 SAME 的自动 Apply

N13 wire input 使用请求内短 anchor ID，并随 Package view 提供 `anchor_ids`、
`primary_anchor_id`、`anchor_conflict`、trust 和 relation 摘要；同一 anchor view 在 batch
字典中只传一次。Prompt 在 anchor 比较段增加：

> A shared canonical parent anchor is strong evidence of the same Package. Distinct trusted
> artifact anchors are a boundary; similar raw hints alone are not sufficient for
> `SAME_PACKAGE`.

模型仍可判 mixed pair 为 SAME，但并非所有 SAME 都直接 Apply。Apply allowlist：

- 共享当前 Atomic root：保留现有确定性合并；
- 共享同一 trusted canonical artifact，且不存在 relation/family boundary：允许自动合并；
- 模型 SAME 且共享 `primary_anchor_id`：允许自动合并；
- 模型 SAME、仅有 provisional anchor 时，需同时具备相容 Package family、相容 period 以及
  source/parent evidence 支撑，才允许自动合并；
- 仅 entity/topic、embedding、raw hint 相似或 `PARENT_CONTEXT` 单信号：不自动合并，保留
  decision 与审计，等待后续增量证据；
- market/analyst reaction 与其 disclosure/earnings parent、不同 trusted analyst
  institution、不同 trusted artifact、不同 exact period/session：不允许 SAME 自动 Apply；
- `anchor_conflict=true` 的任一侧不自动合并，除非冲突经 canonical redirect 后已消失并重建
  profile。

这不是新增人工 review 队列，也不把 SAME 改写成伪 DIFFERENT。未 Apply 的 SAME 保存为
`SAME_PACKAGE_NOT_APPLIED_WEAK_BOUNDARY`，后续出现新成员或 trusted anchor 后可以按 material
profile 变化重评。

边界修复把 reaction/member 移出 Package 时，写入 pair-level suppression tombstone：

```text
source_root
target_root
boundary_reason
left_material_profile_hash
right_material_profile_hash
```

同一 run 且 material profile 未变化时，N13 不得把该 pair 重新并回；member 集合、primary
anchor、trusted artifact、period、family 发生变化后才可重评。它只防止本轮“刚拆又并”，
不是永久 cannot-link，也不引入 union-find 或 Package cluster surgery。

### 6.8 N13 重复评估与 Token

将 N13 cache key 从完整 Package dump 改为 material merge profile：

```text
current member roots
primary_anchor_id / anchor_conflict
trusted artifact roots
exact period
package kind / family
anchor entities
external relation boundary
representative identity hashes
```

canonical summary 文案、embedding 重建时间、非边界 warning、audit 字段和普通 version bump
不再使相同 Package-ID pair 失效。Apply 前仍校验当前 member roots 与 material hash，防止复用
陈旧判断。批量模式中 N13 只在 N12 epoch 完成后运行一次；增量模式保持当前近实时行为。

目标不是继续追求极端降 Token，而是在维持上轮相对基线输入 Token 至少下降 60% 的前提下，
把 Package-ID pair 重复评估从 235 次降低至少 80%，且正确新增 join 的单位 Token 成本单列。

### 6.9 验收与独立回滚

质量总门槛：

- Package Pair Precision `> 90%`；
- Package Pair Recall `> 80%`；
- N12 共享 canonical anchor 的正确 Package candidate coverage = 100%；
- parent-supported Mention 的 anchor coverage 由人工 Gold/抽检单列，不以强行非空达标；
- raw hint 相似导致的跨文档 deterministic merge = 0；
- reaction→earnings/disclosure 错误 member/merge = 0；
- trusted analyst institution、artifact、period/session hard boundary violation = 0；
- mixed pair 不再因 `MIXED_PACKAGE_REQUIRES_SPLIT_OR_REVIEW` 被确定性 DIFFERENT；
- 新写 FROZEN = 0；边界拆出的 pair 在 material profile 未变化时同 run 重并 = 0；
- anchor conflict 不得伪装为 no-anchor；
- Package Precision、Recall 必须同时达标，不允许靠把所有 weak SAME 都 Apply 提 Recall。

性能门槛：

- N13 输入 Token 相对原始基线仍至少降低 60%；
- 同 material profile pair 重复请求 = 0；
- Package-ID pair 重复评估较本轮降低 ≥ 80%；
- 不扩大 N12/N13 的复杂 batch 上限；
- anchor wire 字段增加后的 N12/N13 单请求输入增幅 ≤ 5%，并由短 ID、dictionary view 抵消。

独立开关与回滚单位：

1. Grounder anchor Prompt；
2. source-scoped N11 normalization；
3. anchor candidate slot；
4. mixed guard rollback；
5. FROZEN removal；
6. N13 SAME Apply allowlist；
7. material-profile cache。

若 Prompt 导致泛 anchor 激增，只回滚 Prompt，不丢弃集合聚合；若 Apply allowlist 伤害 Recall，
按触发原因单独放宽，不回滚 anchor 留存；若 source-scoped 归一出现跨 parent 误连，只关闭该
resolution method，不影响 KB artifact identity。

## 7. 支撑节点：Dreamer/Evidence 韧性与 Field 精度

本节只处理比较报告直接证明、且会影响总验收的残余问题。不扩大为 Evidence semantic LLM、
Field 全量 taxonomy 重构或新一轮大 Prompt。

### 7.1 Dreamer repair：修复唯一文档失败的本地序列化根因

D02 的模型请求已正常返回；整篇失败发生在 `_invoke_typed()` 把
`ValidationError.errors()` 原样放入 `json.dumps()` 时。Pydantic error 的 `ctx` 可携带
exception 对象，当前 `repair_and_validate()` 没有先做 JSON-safe 转换，且 request 构造位于
`try` 之外，`TypeError` 因而越过 block-level `SingleDocumentPipelineError` 降级路径。

增加统一 `_safe_validation_errors()`，只保留：

```text
loc
type
msg（截短）
code
```

显式丢弃 `input/url/ctx/exception/repr`。Dreamer、Grounder item repair、Judge repair 共用
同一函数，避免三套错误协议再次分叉。repair request 的构造也移入受控异常边界；只有
`TypeError/ValidationError/SingleDocumentPipelineError` 被转换为明确 stage code，不能用
`except Exception` 吞掉编程错误。

Dreamer 单 block 首次输出和唯一一次 repair 都非法时，保留现有
`EMPTY_BLOCK_CANDIDATES` 降级并继续其他 block；不得扩大为文档失败，也不串行重跑整篇。
审计记录 block ID、错误码、候选数和 fallback，不保存原始 exception。

门槛：

- 30/30 文档完成；
- Evidence/schema 单项异常导致文档失败 = 0；
- D02 同型 `ctx` exception 序列化单测通过；
- block repair 失败只损失该 block，不损失已完成 sibling blocks；
- semantic Evidence repair LLM 继续为 0。

### 7.2 Evidence：利用已有 candidate lineage 做确定性 span 恢复

4 条 `TEXT_NOT_FOUND` 分成两类：

- D19 的源 HTML/文本等价差异，是上一轮 exact locator 残留；
- D25 两条与 D28 一条把源内多处信息概括或合成，模型文本本身不是连续原文。

现有 `reconcile_evidence_text()` 已支持 exact、punctuation、whitespace 和 anchor
disambiguation，不能回滚；但 `_materialize_mention()` 对 MAIN Evidence 没有收到
`source_candidate_ids` 对应的 Dreamer locator，只有 attribute Evidence 会用已经成功的 main
span 做 anchor。虽然 `_judge_lineage()` 已保存 candidate lineage，materialize 调用却没有把
它传入。

最小修复：

1. materialize 接收当前 Mention 的 lineage，以及本地
   `candidate_id → evidence_locations` 映射；
2. direct reconcile 失败时，只从该 Mention 自身 source candidates 的 locator 中寻找候选；
3. 候选必须位于同一 declared segment，能 materialize 为 exact source span，并与失败文本
   存在足够高的 token/number/entity 覆盖；唯一候选才替换；
4. 多候选、跨 segment 合成、数值或实体不一致时不猜测，继续保留 `TEXT_NOT_FOUND`；
5. 记录 `DREAM_CANDIDATE_ANCHOR_RECOVERY`、原文本 hash、候选 ID 和最终 span hash。

Grounder 的 Document grounding 段再增加一句，明确连续原文要求：

> For each evidence location, copy one contiguous verbatim span from its declared segment;
> never paraphrase or join non-contiguous text.

这不会增加输出字段或 reasoning。它主要防止新 paraphrase；确定性 candidate-anchor fallback
处理仍可能出现的模型偏差。不得启用 fuzzy span 自动吸附或 semantic Evidence repair LLM。

门槛：

- final exact/source-equivalent ≥ 99.5%；
- 自动恢复的 span 对人工 Gold precision = 100%；
- candidate-anchor 恢复不得改变 Mention 的 proposition、field 或 candidate lineage；
- semantic Evidence repair LLM = 0；
- 无法唯一恢复的单条 Evidence 只标记异常，不使 Mention 或文档失败。

### 7.3 Field：先修上游类型，再收紧 resolver，不为 proxy 指标盲目扩候选

本轮 Field total 为 96.40%，predicate、metric、period 已过门槛，不做整体回滚。25 个 strict
deviation 中需要直接修的是：

- 5 个 predicate action 语义被压缩；
- 2 个普通词/ticker/机构名误消歧；
- 9 个 participant 上游类型污染；
- 2 个非 filing 场景 XBRL 词面误链。

另外 5 个 `base metric + QuantityRole` 差异可能是新合同的预期表达，必须先核对
role/assertion/basis/period 是否完整，不能为了旧 Gold 把 qualifier 再塞回 metric ID。

#### 7.3.1 Mention 入口做业务类型分流

不为每个 bad case 新增枚举，而是在 Grounder/Judge field completeness 与 materialize 前做
以下通用分流：

- publisher、ranking/report 名称：进入 `source_claim` 或 artifact field，不作为 principal
  participant；
- metric adoption、development 等概念或原因短语：进入 predicate/object/open attribute，
  不伪造 actor；
- agreement count、容量/数量：进入 Quantity；
- 具体 product、business unit、facility、project、asset、technology、program：进入现有
  object namespace；
- index、ETF、明确 basket/sector collective：进入 instrument/collective 语义；无可信 KB
  identity 时保持 typed unresolved，不创建 company/product；
- 只有真实承担 ACTOR/SUBJECT/TARGET/COUNTERPARTY/AFFECTED/AUTHORITY 角色的实体才进入
  participant resolver。

Grounder Prompt 在 Source separation 后只增加一句：

> Keep `participants` to entities that fill an event role. Put explicit claimants in
> `source_claim`, report or artifact names and other non-participant objects in evidence-backed
> `open_attributes`, and metrics or counts in `quantities`.

若当前 schema 对 object/instrument 表达不足，优先复用 typed `open_attributes` 与现有
FieldNamespace，不在本轮扩充大型 Mention schema。Judge 的 field completeness 检查使用同一
类型路由表，发现明显错槽时只修该 field，不拒绝整条 Mention。

#### 7.3.2 resolver 的窄边界

- ticker hint 只能帮助解析与 source issuer 一致的候选，不能把普通词或 sector 名词提升为
  ticker；
- institution/company 同名时，analyst/report/source context 优先 institution；无充分上下文
  时进入候选决策，不做 lexical deterministic link；
- participant exact match 若跨 company/institution/instrument/object catalog 冲突，必须保留
  namespace-aware candidates，禁止选第一个；
- 非 FILING 且 raw value 没有显式 taxonomy 前缀时，`US_GAAP_/XBRL_` 不得
  deterministic link；只有 core metric 缺失不能成为放行理由；
- XBRL candidate 进入 LLM 前仍需 source type、taxonomy domain 和 expected semantic type
  一致；否则保持 base/internal unresolved；
- predicate canonicalization 将 action polarity、trend/status/volume 与 report/guide/secure/
  constrain 等 hard action dimension 纳入 collision key，不能只按词面近似重定向。

不新增 bad-case 专用 alias。Tech、Mizuho 等已知错误只用于回归测试，规则依据是
namespace/context/domain，而不是字符串黑名单。

#### 7.3.3 把 candidate recall@8 从 proxy 变成真实指标

本轮 `51.81%` 是 provisional proxy，当前审计没有稳定保存 resolver 最终决策前的完整 top-8
及路由原因。实施时对每个 unique raw field 保存：

```text
raw_hash
requested_namespace
candidate_external_ids[:8]
candidate_namespaces
route/source
scores
blocked_reason
selected_id
```

评估时以相同 Gold contract 分开计算 predicate、participant、metric、period 的
candidate recall@8；base metric + QuantityRole 使用校准后的等价判定。不能为追 98% 把无关
catalog 混入 top-8：先做 namespace route，再在 route 内召回；top-8 体积不增加。

Field 验收：

- predicate ≥ 90%，participant ≥ 96%，metric ≥ 90%，fiscal period ≥ 80%，total ≥ 91%；
- candidate recall@8 ≥ 98%；
- safe deterministic precision ≥ 99%；
- 新增 runtime alias 错误 = 0；
- 2 个 XBRL 同型误链 = 0；
- predicate hard action direction 丢失 = 0；
- 不靠显著增加 UNRESOLVED 达标；
- period coverage 与 accuracy 同时报，不再用样本从 70 降到 13 后的 100% 单独宣称成功。

## 8. 跨节点依赖、实施批次与验证顺序

### 8.1 为什么不能把所有改动一次落地后只跑一次

本方案同时改变局部恢复粒度、Prompt 业务边界、Sidecar enforcement、Package anchor 和批量
编排。若一次合入并只做一轮真实验收，即使最终指标变化也无法判断是语义修复、模型波动还是
编排模式造成，回滚只能退整包。实施必须按依赖拆批，但不在代码中长期维护两套路径。

### 8.2 实施批次

#### Batch A：P0 局部韧性，不改变正常合法结果

1. Dreamer/所有 repair error JSON-safe；
2. Grounder 单非法 draft 的结构化 business error；
3. Judge 单 decision repair 与 coverage recovery；
4. N9 assessment/axis 级 normalization、局部 fallback 与 final action 重算；
5. Evidence materialize 接入已有 candidate lineage；
6. 对现有失败 payload、固定 N9 输出和 Evidence bad case 做离线回放。

此批目标是移除“单项异常扩大失败”，不是调 MERGE/CREATE_NEW 倾向。所有正常合法 fixture 的
领域结果必须 bitwise/semantic equivalent。

#### Batch B：P1 Mention、Field 与 Atomic identity 质量

1. Grounder candidate disposition、atomic factorization、Evidence 与 participant Prompt；
2. missing candidate document-local 补处置；
3. Judge field-preserving split；
4. Field type routing、alias/domain gate 与真实 candidate@8 审计；
5. Sidecar trusted field 注入；
6. axis/relation consistency；
7. `PRIMARY_METRIC_FAMILY → METRIC → COMPLETE_REFERENT → ASSERTION_STATE` 逐条 canary；
8. N9 private comparison sequence与受限 singleton absorption。

每条 hard rule 单独 replay/canary，可独立回 shadow。Prompt 与 Sidecar 合同必须在同一批交付，
不能先给模型新字段却不解释，或先写 Prompt 却未真正投影字段。

#### Batch C：P1 Package parent identity 与 N12/N13

1. Grounder parent-anchor Prompt 与 5 样本合同小测；
2. N11 source-scoped canonicalization；
3. Mention→Atomic→Package 集合聚合和默认兼容字段；
4. N12 anchor candidate slots 与 relation-aware Prompt；
5. mixed deterministic guard 回滚、FROZEN removal；
6. N13 anchor wire、material-profile cache、SAME Apply allowlist与同 run suppression；
7. 用同一 30 篇做 `INCREMENTAL` 真实质量验收。

5 样本小测只能决定 Prompt 是否存在明显合同歧义；所有质量结论以完整 30 篇及 Gold 为准。

#### Batch D：P2 `BULK_EPOCH` 编排

1. 抽出 Field/Atomic/Package 的 Read/Decide/Apply；
2. 实现 epoch checkpoint、候选图 component、单 writer Apply；
3. 接入 embedding batch、pair ledger、material-profile reuse；
4. 在完全相同的 Batch C 代码、Prompt、模型与输入上，从干净 Registry 运行同一 30 篇；
5. 比较 `INCREMENTAL` 与 `BULK_EPOCH` 的业务输出、Token、墙钟和 component 分布；
6. 30 篇通过后，再用不发送真实模型的数百篇固定 fixture/recorded response 做调度压力测试。

编排重构最后做，是为了先固定业务节点的正确语义。若候选图形成一个巨型 component，系统应
诚实退化为该 component 内串行，仅保留 barrier/去重收益；不能为追并行强行切断真实候选边。

### 8.3 预计成本变化与预算

局部质量恢复会增加少量请求，不能假装“所有优化都同时降 Token”：

| 项目 | 预计影响 | 边界 |
| --- | ---: | --- |
| Grounder missing recovery | Grounder Token +5%–15% | 只传 missing candidates 与局部 context，每文档聚合一次 |
| Grounder/Judge item repair | 低频增加 | 单 item/单 decision、最多一次，不重写合法 batch |
| 5 样本 anchor 小测 | 固定 5 条 | 仅实施 Prompt 时一次 |
| N9 assessment fallback | 通常下降 | 不再整 task repair/create-new；不新增正常请求 |
| N13 material cache/epoch | N13 Token -10%–30% | 不牺牲正确候选与 anchor payload |
| `BULK_EPOCH` 全流程 | 净 -5%–15% 目标 | 以真实审计为准，不从局部比例机械相加 |

INCREMENTAL 质量验收允许总 Token 相对本轮最多增加 8%，前提是 Mention/N9/Package 目标均有
实质改善；否则增长视为失败。`BULK_EPOCH` 应把净 Token 拉回本轮以下，并实现至少 30% 墙钟
下降。任何 recovery request 都单列命中数、成功数、input/output Token 和每个正确恢复的
Token 成本。

## 9. 完整验收设计

### 9.1 控制变量

每轮均固定：

- 同一 30 篇输入与 stable document order；
- 同一 Gold 版本；只对已确认的 QuantityRole 等价口径做版本化勘误，不在看完结果后移动
  边界；
- 同一模型、temperature、provider、Prompt version 和 runtime tier；
- 独立干净 Registry/DB，禁止上一轮 Atomic/Package/embedding 污染；
- 相同重试、超时和并发上限；比较 bulk 时唯一变量是 orchestration mode；
- 保存 request/response hash、usage、latency、fallback、processing key 与最终
  Package→Atomic→Mention hierarchy。

### 9.2 节点门槛

| 层级 | 指标 | 门槛 |
| --- | --- | ---: |
| Runtime | 文档成功率 | 100% |
| Mention | Precision | >90% |
| Mention | Recall | >85% |
| Mention | candidate disposition coverage | 100% |
| Mention | known compound 修复率 | ≥80% |
| Mention | 新 fragmentation bad case | ≤2 |
| Evidence | exact/source-equivalent | ≥99.5% |
| Evidence | semantic repair LLM | 0 |
| Field | predicate / participant / metric / period / total | ≥90% / 96% / 90% / 80% / 91% |
| Field | candidate recall@8 / deterministic P | ≥98% / ≥99% |
| N9 | MERGE Precision | >75% |
| N9 | conditional MERGE Recall | >85% |
| Atomic Identity | Pair Precision | >70% |
| Atomic Identity | Pair Recall | >80% |
| Package | Pair Precision | >90% |
| Package | Pair Recall | >80% |
| Bulk | 30 篇墙钟 | 较本轮 -30% |
| Bulk | 全流程 Token | 不高于本轮，目标 -5%～15% |

同时检查防投机指标：

- Mention 不能靠把 rejected/missing 全部 ACCEPT 提 Recall；
- Atomic 不能靠 singleton-scoped identity 或全 CREATE_NEW 提 Precision；
- Package 不能靠所有 weak SAME 自动 Apply 提 Recall；
- Field 不能靠增加 UNRESOLVED 提 Precision；
- Evidence 不能靠语义 LLM 或把异常记录删除提高 exact rate；
- bulk 不能通过丢候选、减少 Judge/N9/N12/N13 覆盖或扩大 batch 伪造提速。

### 9.3 必须输出的分层 bad-case 对照

完整验收除总指标外，逐条输出：

1. 上一轮复现 bad case：是否消失；未消失则具体在哪一节点再次进入错误路径；
2. 本轮修改引入 bad case：触发的 policy/Prompt/normalizer 与独立回滚项；
3. 新 bad case：字段、candidate、axis、relation、Apply 与最终 cluster/package 影响；
4. 每个 Grounder/Judge/N9 recovery 的初始错误、修复结果与 fallback；
5. 每条 Sidecar hard rule 的 triggered、FP prevented、TP blocked 和 Pair P/R；
6. 每个 N13 正确新增 join 的 Token 成本；
7. 最终供人审阅的 `Package → Atomic → Mention → Evidence/Source` 层级结果，不能按文章作为
   主目录。

### 9.4 回滚决策

| 现象 | 回滚/调整 |
| --- | --- |
| Mention Recall 提升但 Precision <90% | 回滚对应 umbrella/split Prompt 或 recovery disposition，不回滚 item-level 韧性 |
| N9 Recall 上升但 Precision下降 | 收紧 singleton absorption/weak SAME，保留 assessment fallback |
| 某 Sidecar rule 阻断任一 Gold TP | 该 rule 单独回 shadow，保留字段投影与 consistency validator |
| Anchor coverage上升但 Package Precision下降 | 禁止对应 provisional resolution/Apply route，保留集合聚合与 trusted artifact |
| 去 FROZEN 后 mixed 污染扩散 | 收紧 N13 Apply allowlist和 boundary reassignment，不恢复以 review 作为阻塞状态 |
| Field candidate Recall仍低 | 修 namespace route/candidate source；不扩大跨 catalog top-8 |
| Bulk 输出显著偏离 Incremental | 关闭 `BULK_EPOCH` 对外入口，保留已验证的 embedding/pair reuse；排查 late-edge/component |
| Bulk 未提速但质量等价 | 保留 Incremental 为默认；根据 component 分布决定是否继续，不牺牲质量硬切图 |

## 10. Prompt 与业务合同对齐清单

本节汇总实施时全部计划变更的 Prompt 原文，作为 code review 清单。除下列内容外不顺手重写
其他 Prompt；Schema 改动必须与对应文字同一 commit 落地。

### 10.1 Grounder 主 Prompt

**替换 candidate disposition 段：**

> Give every supplied candidate exactly one disposition: USED or REJECTED. USED means it
> appears in `source_candidate_ids` of one or more non-duplicate atomic drafts; REJECTED means
> it appears once in `rejected_candidates` and in no draft. A candidate may support multiple
> drafts only when its evidence contains multiple independently truth-evaluable events. Before
> returning, verify that USED and REJECTED are disjoint and cover every supplied candidate.

**在 Atomicity 后增加：**

> Before drafting, privately factor each candidate by core subject/object, action and polarity,
> Assertion State, event time/session, and PRIMARY metric. Split when one of these defines a
> different truth-evaluable event; do not split a comparison, bound, qualifier, or supporting
> value from the event it qualifies. Each resulting draft must retain its supported period,
> benchmark/range, source attribution, and object.

> Use BACKGROUND only when no independently truth-evaluable event remains; analytical wording
> alone does not make an explicit forecast, rating, plan, commitment, measurable state/change,
> market move, or scheduled occurrence background.

> A shared report, plan, article, or topic is context, not a Mention identity. Keep a generic
> umbrella only when it states an independent action or artifact fact not exhausted by the
> specific drafts.

**替换 `local_package_hint` 段：**

> Provide `local_package_hint` when the evidence identifies a parent occurrence or matter
> containing this Mention. Reuse the same short, distinguishing anchor for Mentions under the
> same parent. Do not use an entity, ticker, broad topic, article, Package ID, or the Mention
> itself as the anchor. Leave it null ONLY when no parent boundary is supported.
> `relation_to_anchor` points from the Mention to the parent.

**在 Document grounding 增加：**

> For each evidence location, copy one contiguous verbatim span from its declared segment;
> never paraphrase or join non-contiguous text.

**在 Source separation 增加：**

> Keep `participants` to entities that fill an event role. Put explicit claimants in
> `source_claim`, report or artifact names and other non-participant objects in evidence-backed
> `open_attributes`, and metrics or counts in `quantities`.

### 10.2 Grounder recovery Prompt

**Missing candidate 补处置：**

> Resolve every supplied missing candidate. Give each one exactly one disposition under the
> same USED/REJECTED rules as Grounder. Preserve all supported event fields; do not reject a
> candidate merely because it contains multiple events—split it into atomic drafts. Return no
> IDs that were not supplied.

**单非法 draft repair：**

> Repair one invalid Grounder draft using `business_errors`. Preserve its supported meaning and
> candidate lineage. Return all and only the atomic replacement draft(s) needed to fix those
> errors; retain each replacement's subject/object, action and polarity, Assertion State,
> period/session, PRIMARY metric, comparison/range, and explicit source. Do not repair unrelated
> drafts.

### 10.3 Judge 主 Prompt 与 recovery

**主 Prompt 增加：**

> For SPLIT, preserve every supported event and attach each period, benchmark/range, source,
> object, and action polarity only to the replacement it qualifies. Do not copy all fields to
> every replacement or leave a supported event behind.

> A shared report, plan, article, or topic does not make distinct objects, actions/polarities,
> PRIMARY metrics, Assertion States, or event times/sessions one Mention.

**单 decision repair：**

> Repair exactly one Judge decision using `business_errors`. Return exactly one command for
> `target_id` and no command for any other ID. Preserve every source-supported fact and exact
> evidence; apply the error-specific correction rather than only rewriting the reason. If the
> draft is compound, SPLIT it into complete atomic replacements. If a generic umbrella is
> exhausted by the supplied peer facts and has no independent artifact or action, REJECT it.

**Coverage recovery：**

> Return exactly one Judge command for every supplied missing draft ID. Review only these drafts
> under the same Eventhood, Atomicity, field-correctness, and consolidation rules; do not return
> any other ID.

### 10.4 N9 Prompt

**删除：**

> Identity differences are diagnostic observations, not automatic rejection conditions. You
> must use the provided context to determine whether each difference is sufficient to establish
> that the two items represent different events.

> None of the candidate events is supported by sufficient evidence to be identified as the same
> event, or significant semantic uncertainty remains after all candidates have been compared.

> `identity_differences` and `claim_conflict` are audit information and do not automatically
> override or change the final action.

**分别替换为：**

> After each candidate has been assessed independently, no valid candidate is `SAME_EVENT`.

> Determine each candidate independently from the supplied referent, occurrence, and facet
> evidence. A difference establishes a separate event only when it identifies a different
> minimal fact; uncertainty about one candidate does not decide any other candidate's relation
> or the final action.

> Use `identity_differences` and `claim_conflict` only to summarize evidence already reflected
> in the axis verdicts and relation.

**增加私有比较顺序与粒度边界：**

> For each candidate, first resolve the core referent and participant roles; then compare the
> normalized occurrence, time/session, object, action/polarity, Assertion State, and
> metric/facet; only then assign the relation. A specific numeric statement and a qualitative
> summary may be the same occurrence when these identity dimensions align and the broader
> wording does not cover additional facts.

> Different wording or granularity alone does not create a new occurrence. Treat a concise
> summary as `SAME_EVENT` only when it can be narrowed to the same minimal fact without
> absorbing additional objects, actions, metrics, periods, or sources.

> Participant role labels are not referents by themselves. Compare canonical entities and
> their event roles together: a harmless SUBJECT/ACTOR wording difference is not a referent
> conflict, while different issuers, objects, counterparties, analysts, or instruments may
> establish one.

**替换 Sidecar conflict 说明：**

> `canonical_conflict_axes` are deterministic warnings; use the evidence to confirm or override
> them.

> Every axis in `enforced_conflict_axes` must be `CONFLICT`, and any candidate with a `CONFLICT`
> axis must not be `SAME_EVENT`.

**增加 Atomic/container 与 claim 边界：**

> A shared report, filing, call, commercial plan, or parent episode may place facts in one Event
> Package; it is not Atomic identity. `SAME_EVENT` requires the same minimal subject/object,
> action and polarity, occurrence/session, Assertion State, and metric/facet.

> Apply `claim_conflict` only after the identity axes establish the same minimal fact. Do not use
> it to absorb a different issuer/referent, object, action polarity, session/occurrence,
> Assertion State, metric/facet, analyst institution, or source artifact.

> A generic multi-source or umbrella summary is not the same Atomic Event as a named-source
> action unless it resolves to that one minimal action and covers no additional source, object,
> metric, or occurrence.

### 10.5 N12 Prompt

> A shared canonical parent anchor is strong candidate evidence, but `relation_to_anchor` and
> event family still determine membership: disclosure content may be `MEMBER`, while market or
> analyst reactions are normally `EXTERNAL_RELATED`. Similar raw hint text alone is not parent
> identity.

### 10.6 N13 Prompt

**将当前 hard-boundary 句完整替换为：**

> Inferred external relations are advisory. Conflicting externally trusted artifacts or
> institutions, exact canonical periods, or explicit controlled trading sessions are hard
> boundaries.

> For a mixed Package, compare the supported core parent evidence; mixed membership alone does
> not decide `SAME_PACKAGE` or `DIFFERENT_PACKAGE`.

**在 anchor 比较段增加：**

> A shared canonical parent anchor is strong evidence of the same Package. Distinct trusted
> artifact anchors are a boundary; similar raw hints alone are not sufficient for
> `SAME_PACKAGE`.

### 10.7 无上下文模型视角的合同检查

实施 code review 必须逐项回答：

1. 模型是否知道输入每个 ID 的处置基数，以及 split 后 ID lineage 如何计算；
2. repair 是否拿到具体 target、field path、业务 error 与允许的修复动作；
3. N9 是否知道每个 candidate 独立判断、axis 和 relation 必须一致、final action 如何由合法
   assessments 得出；
4. `canonical_conflict_axes` 与 `enforced_conflict_axes` 的权限是否清楚；
5. `local_package_hint` 是否明确表达 parent 而不是 entity/topic/Mention；
6. N12/N13 是否区分 canonical anchor、raw hint、member 与 external reaction；
7. Schema 是否真实暴露 Prompt 提及的字段，enum/nullable/cardinality 是否一致；
8. 编排是否仍依赖 Prompt 中已删除的旧语义。

任一项不能从“该 Prompt + 该 Schema + 当前 request payload”独立回答，就不能进入真实模型
验收。不得用开发者脑中的隐含业务知识补足模型看不到的合同。

## 11. 节点处置总表与最终判断

| 节点 | 本轮动作 | 明确不做 |
| --- | --- | --- |
| Dreamer | repair error JSON-safe、block 级降级 | 不重跑整文档，不改事件发现 Prompt |
| Grounder | disposition、missing recovery、item repair、field/anchor保真 | 不跨文档合并 missing，不批量重写合法 drafts |
| Judge | split保真、单 decision repair、coverage recovery | 不因单条非法阻塞全篇 |
| N5.5 Field | typed route、domain gate、真实 candidate@8 | 不扩大 top-8，不做字符串特例 |
| N6 Sidecar | trusted metric/referent/assertion等编译闭环 | 不把所有 shadow 规则一次性 enforce |
| N7 | bulk构图复用现有候选，指标继续审计 | 当前 Recall@8 已覆盖 Gold，不改排序目标或增大 LLM payload |
| N9 | 独立 assessment、局部降级、Prompt/COT、axis一致性 | 不再 whole-task CREATE_NEW，不全局调 MERGE 阈值 |
| N10 Apply | 受限 singleton absorption、Sidecar Apply guard | 不做 cluster↔cluster、union-find、污染簇自动手术 |
| N11 | canonical parent anchor 与集合聚合 | 不维护三套 anchor，不用 raw hint 跨文档自动归一 |
| N12 | anchor候选槽与 relation-aware assignment | 不增大复杂 batch，不因共享 anchor 自动 MEMBER |
| N13 | mixed guard回滚、去FROZEN、受限SAME Apply、material cache | 不恢复宽 SAME，不让 review 状态阻塞流程 |
| Orchestrator | `INCREMENTAL` + `BULK_EPOCH`、component并行、single writer | 不直接并行全量 mutable workflow |

主要代码面预计集中在：

- `src/cdecr/single_document.py`、`single_document_contracts.py` 与 Grounder/Judge Prompt；
- `src/cdecr/canonical_field_resolution.py`、`field_coreference*.py`；
- `src/cdecr/atomic_identity_sidecar.py`、`identity_compiler.py`；
- `src/cdecr/cross_document.py`、`cross_document_contracts.py` 与 N9/N12/N13 Prompt；
- `src/cdecr/coreference_rules.py`、`package_engine.py`、`contracts.py`、`registry.py`；
- CLI/scheduler 的 bulk mode 与 epoch checkpoint。

测试优先扩展现有
`test_single_document.py`、`test_preprocessing.py`、`test_canonical_field_resolution.py`、
`test_field_alias_safety.py`、`test_atomic_identity_sidecar.py`、
`test_atomic_merge_invariant.py`、`test_cross_document.py`、
`test_package_engine_v13.py` 与 Registry migration tests，不另建脱离真实路径的平行实现。

最终判断：本轮应采用“先缩小错误爆炸半径，再补身份信息，再做受限合并，最后重构批量编排”
的路线。Mention Recall、Atomic Recall 和 Package Recall 的下降具有不同根因，不能靠一个更宽的
合并阈值解决；同样，墙钟过长也不能靠放大复杂 LLM batch解决。上述方案增加的主要复杂性只有
epoch planner、统一 parent-anchor aggregate 和 assessment-level recovery 三处，每一处都替代
现有重复或过粗逻辑，而不是在旧逻辑旁叠加永久旁路。若完整验收不能同时满足 Precision 与
Recall 门槛，应按独立开关回滚具体策略，不应回滚已证明有效的 Evidence exact、N7 recall、
N13降本和局部韧性基础。
