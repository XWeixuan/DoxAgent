# CDECR Parent Occurrence Package V2.1：误合并与效能 one-shot 核心重构方案

> 日期：2026-08-13  
> 方案性质：一次性替换 V2 核心机制，不在现有路径上继续叠加例外规则  
> 依据：`CDECR_PARENT_OCCURRENCE_PACKAGE_V2_DEEP_DIVE_AUDIT_20260812.md`、`CDECR_PARENT_OCCURRENCE_PACKAGE_V2_30_REAL_ACCEPTANCE_REPORT_20260811.md`、冻结 Registry 与当前真实代码  
> 本文只制定方案，不在本轮修改代码、不调用真实模型、不运行 MU300

## 0. 最终判断

### 0.1 决策

**不立即整体回滚 Parent Occurrence 路线，但冻结当前 V2，不允许以当前实现进入 300 篇。执行一次且仅一次 V2.1 核心重构；若 Package-only 冻结验收未同时通过质量、规模和成本闸门，则果断回滚到 V1。**

这个判断不是折中拖延，而是基于两组同时成立的证据：

1. V2 的业务压缩能力真实存在。30 篇中 281 个 Atomic 被压缩到 75 个 Package，`Package / Atomic = 26.69%`；最终只剩 27 个 singleton Package，其中 Gold 可判的漏合并 singleton 只有 4 个。对用户最在意的“不要把 300 篇变成几千个 Package”，Parent-first 路线明显比保守 pairwise 合并更有价值。
2. 当前 V2 的误合并不是不可控的开放世界长尾，而是由少数系统性机制集中制造。48 个非 singleton Package 中 17 个存在明确混合；61 个需移出的 Atomic 中 29 个来自唯一的 Micron earnings 超大簇，前两个坏簇合计贡献 36/61。17/27 个支撑最大簇的文档内 proposal 在 Induction 时已经污染。修复最大两类机制后，错误规模有显著下降空间。
3. 当前错误机制已经定位到确定性代码和输入合同，不是只能靠“模型再聪明一点”：107 个 proposal 中 `artifact_cues`、`metric_cues`、`object_cues` 的有效覆盖均为 0；候选打分中“同 family + 同 participant”得到 `0.6 + 0.8 = 1.4`，恰好越过 `1.35` 阈值，因此“同公司、同类事件”天然变成跨文档连接；随后基于连通分量的处理又放大传递污染。
4. V2 的成本上升同样集中在可删除的结构上：全文重复进入 Induction、proposal 卡片多轮重复、R1/R2 候选覆盖不单调、末端 oversized review 把大簇重新展开后再请求模型。该 review 相对 67-Package 中间态只减少 92 个 FP，却增加 127 个 FN，既昂贵又有害，应直接删除而不是优化。

因此，正确路线不是回到“高准确、几千碎片”的旧极端，也不是接受当前“低 Package 数但超大污染簇”的新极端，而是保留 Parent-first 分区，把**父发生边界信号、候选图和局部纠错**一次性重做。

### 0.2 为什么有把握值得再做一次

V2.1 不依赖为 30 篇 bad case 编写公司、ticker 或财报专用规则，而是修复四个通用缺陷：

- 模型看不到本来已经存在于 Mention/Atomic/Field 中的对象、指标、artifact、机构、session 等身份信号；
- “同 issuer + family”这种弱上下文被代码当成足够的连接证据；
- proposal 一旦文档内污染，后续 resolution 把它当作不可拆原子继续传播；
- 超大簇只在末端以“默认父事件 + 例外”一次性修剪，模型负担大且漏拆、误拆同时发生。

这四项都能通过一个边界信号编译器、一个受限候选图和一次前移的局部重分区解决，不需要持续增加字段、节点或规则层级。若完成后仍无法通过冻结 30 篇 Package-only 闸门，说明 Parent-first 在当前模型/输入粒度下不具备可控性，应停止投入并回滚。

## 1. 本轮目标与非目标

### 1.1 核心目标

本轮不是追求传统 Pair Precision/Recall 的单点最优，而是使父事件聚合在 300 篇规模下同时具备以下业务属性：

1. **聚得动**：Package 数量随 Atomic 近似线性增长，`Package / Atomic` 保持在可用区间，不重新退化为海量 singleton。
2. **不塌簇**：共同公司、共同季度、共同主题或共同文章不能形成跨父事件 supercluster；反应、独立报告、交易/协议、市场发生和披露内容保持父边界。
3. **成本可控**：只做两轮标准 LLM 分区；纠错限定在被确定性信号判为可疑的小组，不再对所有超大簇做全量 review。
4. **失败局部化**：单个文档、proposal、resolution task 或修复请求失败，不把整篇文章、整批 Atomic 或整轮 CDECR 判失败，也不伪造为全 singleton 成功结果。
5. **合同最小化**：不增加 reasoning 字段，不给模型暴露复杂审计字段，不维护第二套长期 Package identity schema。

### 1.2 明确非目标

- 不在本轮重做 Mention、Atomic、N9 或 Field 业务逻辑；上游已污染 Atomic 只作为已知输入噪声单列评估。
- 不恢复 N12/Wave C/N13 的旧三路 Package 决策，也不并行保留 V1/V2 双活逻辑。
- 不重新引入 pairwise `P²` 扫描或 N13 pair-local Apply。
- 不用 company/ticker、Micron、Apple、SCA、earnings 等测试集专用规则修补结果。
- 不新增模型 reasoning、置信度、边界解释或大段审计输出字段。
- 不以缩小 Package 数量作为唯一成功标准；通过错误 supercluster 压低 Package 数属于失败。

## 2. 已确认的根因与设计含义

### 2.1 根因 A：身份信号存在，但没有真正进入父发生候选合同

当前 `_event_cues()` 主要从少数 identity field 名称中寻找 artifact/metric/object。真实 Atomic 常见字段却是 `assertion_state`、`event_time`、`normalized_predicate`、`principal_participant_ids`、`reference_period_id`；指标和对象更多存在于 Mention `quantities`、`schema_projection`、`open_attributes` 和 canonical field link 中。

冻结运行的 107 个 proposal 实际表现为：

| Cue | 有效 proposal |
| --- | ---: |
| artifact | 0/107 |
| metric | 0/107 |
| object | 0/107 |
| period | 31/107 |
| participant | 107/107 |

模型和召回器最终只稳定看到“公司 + family + 少量 period”，自然会把共同报告上下文误认成同一父事件。**结论：先修数据编译，再谈 Prompt；Prompt-only 修复无效。**

### 2.2 根因 B：弱上下文在候选图中被提升为强连接

现有候选分数中：

```text
same family       +0.6
shared participant +0.8
candidate threshold 1.35
```

同公司同 family 不需要 artifact、object、period 或 occurrence bridge 就能进入候选图。participant-family route 在本轮单独产生 501 个候选 pair；候选图随后以 connected component 组织任务，使一个弱边能通过传递闭包把更多 proposal 拉入同一处理区域。

**结论：issuer/family/period 只能用于召回，不能成为连接或合并充分条件。候选边必须包含至少一个 parent-specific bridge。**

### 2.3 根因 C：Induction 污染无法由后续完整恢复

最大 79-Atomic 簇由 27 个 proposal 支撑，其中 17 个在文档内已混入 reaction、SCA、估值、行业背景或其他独立父发生。R1/R2 接收的是 proposal 卡片，而不是可自由拆分的 Atomic；后续即便判断某 proposal 与其他 proposal 不同，也无法拆开 proposal 内部成员。

**结论：纯度修复必须前移到 Induction 输出之后，且只重分区可疑 proposal；不能继续指望末端清理。**

### 2.4 根因 D：R2 没有形成单调补召回

本轮 R1 proposal candidate coverage 为 91.59%，R2 反而降至 87.80%，没有达到“第二轮补齐第一轮漏连”的目的。当前通用 bucket 先按 ID 截到 96，再打分；连通分量超过上限后又按排序任意切块。同一真实父发生可能因 ID、bucket 或 chunk 边界永远不能同场判断。

**结论：R2 必须继承 R1 未解决桥边和孤立 proposal，并在缩约原型上重新召回；coverage 必须单调不减。**

### 2.5 根因 E：oversized review 同时增加成本和碎片

当前 review 把大簇写成“一个 default parent + explicit exceptions”，要求模型从大量相似 Atomic 中一次列全所有例外。结果是：

- 最大簇从 96 缩到 79，但仍显著污染；
- 相对 67-Package 中间态，FP 减少 92，FN 增加 127；
- Micron FQ3 Gold 父发生最终被拆成 12 个组件；
- 大量 Atomic 卡片被再次发送，显著增加 token 和钟墙。

**结论：删除 size-triggered review。大小不是错误证据；只对存在边界矛盾的局部 group 做 bounded reconcile。**

### 2.6 根因 F：当前成本数据受恢复/重试污染，但方向性问题确定

累计运行记录显示 1,131 次调用、2,557,172 input token，相对性能基线 input 增加 93.53%，elapsed 增加约 557%。该钟墙包含断电恢复、重放和复核，不能当作干净单次运行成本；但是以下成本来源不依赖该口径，已可确认：

- Induction 重复发送全文；
- Atomic evidence 文本在同一文档 payload 中重复；
- proposal/prototype 卡片跨 task 重复；
- R2 没有减少任务范围；
- oversized review 重新展开大簇。

因此 V2.1 的成本目标应使用**干净 Package stage 单次运行**重新计量，不能继续拿累计恢复墙钟作为唯一基准。

## 3. One-shot 总体架构

V2.1 保留四段式主路径，但替换其中的信号、候选和纠错机制：

```text
冻结 Atomic snapshot
    ↓
一次性 Boundary Signal Compile + compact document blocks
    ↓
Document-local Induction
    ↓
仅对可疑 proposal 做局部 Repartition
    ↓
结构化多路召回 + 受限语义召回
    ↓
R1 bounded set resolution
    ↓
缩约 prototype + bridge ledger 的 R2 补召回
    ↓
仅对存在高置信边界冲突的 group 做 local reconcile
    ↓
完整 partition 校验与一次性 Apply
```

一次性删除：

- 现有“同 participant + family 即过阈值”的候选分数；
- `sorted(... )[:96]` 之后才评分的通用 bucket 截断；
- connected component 的任意 ID chunk；
- size-triggered oversized review；
- default parent + explicit event exceptions 输出协议；
- review 专用的完整 Atomic 再展开字段与配置。

一次性新增且仅新增一个内部模块：

- `parent_occurrence_signals.py`：负责边界信号编译、兼容性判断、候选多路索引和轻量语义哈希。

`parent_occurrence.py` 只保留 orchestration、LLM task、reducer、reconcile 和 Apply 协调，避免继续把所有策略堆在一个超长文件中。该模块划分是降低复杂度，不是增加业务层级。

## 4. P0：建立真实可用的 Parent Boundary Signal

### 4.1 一次性批量编译，不新增模型字段

Package epoch 启动时，对冻结 Atomic snapshot 做一次批量信号编译：

1. 收集所有 Atomic 的 Mention IDs；
2. 一次批量读取 Mention、source、canonical field links；现有 Registry `get_field_links_for_mentions()` 直接复用，不允许在 proposal/pair 循环内逐条查询；
3. 从以下真实来源编译父边界信号：
   - Atomic：family、assertion state、normalized predicate、event time、reference period；
   - Mention：participants、quantities/metric、schema projection、open attributes；
   - Field links：canonical issuer、period、metric、artifact/report、object/instrument、analyst institution、counterparty；
   - Source：document fingerprint、published time、显式 artifact identity；
4. 编译结果只存在当前 epoch snapshot 和 telemetry 中，不作为新的 LLM 输出或长期业务 schema。

内部数据结构命名为 `ParentBoundarySignature`，保持最小必要字段：

```python
ParentBoundarySignature(
    role,                  # coarse parent role
    issuer_ids,
    period_ids,
    artifact_ids,
    institution_ids,
    object_ids,
    metric_ids,
    market_scope,          # instrument/session/measure，存在才填
    counterparty_ids,
)
```

不新增 `reasoning`、`confidence`、`boundary_explanation`、`anchor_level` 等字段。信号的来源与支持数写入编排 telemetry，而不是交给模型生成。

### 4.2 统一 coarse role，避免 bad-case 专用规则

`role` 只使用以下开放世界可复用的粗粒度：

- `DISCLOSURE`
- `MARKET_EPISODE`
- `ANALYST_REPORT`
- `TRANSACTION_MATTER`
- `OPERATIONAL_MATTER`
- `CONTINUING_MATTER`
- `OTHER`

role 由已有 family、predicate、participant role 和字段确定性编译。它不是最终 Package family，也不要求覆盖所有开放世界事件；无法安全判断时为 `OTHER`。只在双方 role 都明确且确属不兼容时形成边界，不把缺失信号当冲突。

### 4.3 兼容性原则

确定性兼容器只回答三个内部状态：`COMPATIBLE / INCOMPATIBLE / UNKNOWN`，不直接决定模型的最终父事件归属。

高置信 `INCOMPATIBLE` 限定为双方字段均明确的通用边界，例如：

- disclosure 与纯 market reaction；
- analyst report 与非该 report 内容；
- 不同完整 analyst institution/report identity；
- 不同完整 transaction artifact/counterparty/object；
- 不同明确 market date/session/instrument 且不是同一 roundup artifact；
- 明确不同 artifact/report/agreement；
- 同一 coarse role 下完整且互斥的主体或对象边界。

以下均不得单独判为不兼容：

- 一方缺字段；
- child metric 不同；
- child fact 值不同；
- 同一披露中 revenue、EPS、capex 等不同内容；
- 粒度不同但明确指向同一父发生；
- Package family 表达差异。

这保证边界信号用于阻止明显跨父事件污染，而不会把 earnings 内不同 child fact 再次拆碎。

### 4.4 完成标准

- 真实 30 篇 snapshot 中，artifact/metric/object cue 不再出现系统性 `0/107`；
- cue 编译覆盖必须报告“已知/未知”，禁止用空字段伪装为一致；
- 编译阶段只做批量 DB 读取与内存构建，不产生 LLM 调用；
- 相同 snapshot 重跑 signature hash 完全一致。

## 5. P0：重写 Document-local Induction，阻断 proposal 原生污染

### 5.1 输入从“重复全文”改为“共享上下文块 + 引用”

当前 `ParentInductionDocument.article` 携带全文，同时每个 Atomic 又携带 evidence 文本，造成同一内容重复。V2.1 将输入合同收缩为：

```text
document metadata
document_context: [B1]...[Bn]              # 每个块只出现一次
atomics:
  - ref
  - proposition
  - event_family
  - time / period
  - participants
  - object / artifact / metric cues
  - evidence_refs: [B3, B4]                # 只引用共享块
```

`document_context` 的确定性构建规则：

1. 短文档低于阈值时保留全文，避免为了省 token 丢失边界；
2. 长文档保留标题、导语和每个 Atomic Evidence 前后句窗口；
3. 重叠窗口合并，重复文本只出现一次；
4. 块使用请求内短 ID `[B1]...[Bn]`；
5. 单文档上下文设置软预算，优先保留直接 Evidence，超限时丢弃无 Atomic 支持的背景段，而不是截断某条 Evidence；
6. 只有某个可疑 proposal 局部修复且 compact context 确实不足时，才对该 proposal 的 Atomic 升级一次更宽窗口，不对整篇文档重新请求。

这项改动不增加模型任务数量，反而删除文章和 Evidence 的重复 payload。

### 5.2 最小 Schema 调整

输入 DTO：

- `ParentInductionDocument.article` → `document_context`；
- `AtomicDocumentSlice.evidence: list[str]` → `evidence_refs: list[str]`；
- 其余 cue 字段保留，但改由 4 节的统一编译器提供。

输出 DTO：

- 保留 `local_group_id`、`scope`、`label`、`members(ref, membership_relation)`、`external_links`；
- 删除模型生成的 `package_family`，由 group 成员的 Atomic family 确定性编译；
- 不新增 parent type、置信度、reasoning 或冲突解释字段。

删除 `package_family` 的原因是它不参与文档内 coverage，却给模型增加额外分类任务；最终 family 可由成员稳定派生，模型生成值不具备不可替代性。

### 5.3 初次分区后的局部纯度扫描

Induction schema 合法且 coverage 完整，并不代表语义纯净。Reducer 在接收 group 后用 `ParentBoundarySignature` 做一次轻量纯度扫描：

- 无高置信 incompatible pair：直接接受；
- 存在 incompatible pair：仅把该 group 标为 `SUSPECT`；
- 不因 group 大小、成员数量或缺失字段触发修复；
- 不使文档失败，也不让整篇重新 Induction。

对 `SUSPECT` group，将成员按明确 coarse role / artifact / report / transaction / market boundary 形成少量兼容桶，再用**同一 Induction Schema**做一次局部 repartition。输入只包含该 group 的 Atomics、相关 context blocks 和一条简短后缀。禁止设计第三套“修复输出 schema”。

局部后缀固定为：

> Repartition only these Atomics. They came from one group with incompatible parent boundaries. Keep every ref exactly once; do not force different occurrences back together.

若局部请求失败：

1. 确定性保留明确互斥的 coarse bucket；
2. `UNKNOWN` 成员保留在其支持最多的兼容主体中，不自动变 singleton；
3. 仅无法与任何主体兼容的成员独立成组；
4. 记录局部 degraded 状态，文档继续成功。

### 5.4 必须保持的语义

- 同一 earnings release 中不同指标、业务线、管理层陈述和 guidance child facts 可以属于同一父发生；
- disclosure 后的价格变化、独立 analyst report、单独协议/交易以及背景行业叙事不是 disclosure 内容；
- “文章同时提到”不等于“父发生包含”；
- 一条 Atomic 只能出现一次；external relation 不改变 partition membership；
- singleton 合法，但只有在没有其他 Atomic 属于该父发生时才选择。

### 5.5 Induction Prompt 完整替换文本

模型只看到 Prompt、共享上下文块和 Schema，也必须能独立理解任务。因此使用以下完整核心文本，不再依赖旧 Prompt 的隐含上下文：

```text
A parent is a bounded real-world occurrence, process, or identifiable continuing matter. It is not an article, entity, topic, period, or shared background.

Partition every supplied Atomic exactly once. A disclosure and facts the evidence identifies as content of that disclosure may share one parent. A market or analyst reaction, a separate agreement or transaction, an independent report, and background or industry context are different parents even when the article discusses them together. A statement belongs to a call, filing, or release only when its evidence identifies it as content of that disclosure.

Use event family, participants, object, artifact/report, metric, and normalized time as identity evidence. Shared issuer, period, family, source, or topic alone is insufficient.

Link separate parents only when the evidence supports the relation. A one-Atomic group is valid only when no other supplied Atomic belongs to its parent. Assign every Atomic once and output only the schema.
```

这段 Prompt 不列公司、财报数字或测试集 bad case；只定义父发生、包含关系和开放世界通用边界。新增的信息全部对应实际传入字段，避免 Prompt 声称模型能看到实际为空的 cue。

## 6. P0：把候选生成从“弱字段连通图”改为“父发生桥接图”

### 6.1 总原则

候选召回的目的只是让可能属于同一父发生的 proposal 同场判断，不直接 Apply。V2.1 明确区分：

- **recall hint**：issuer、family、period、source、topic；只能帮助找邻居；
- **parent-specific bridge**：artifact/report/agreement、canonical issuer-period-parent-role、analyst institution/report date、transaction counterparties/object、market instrument-date-session-measure、明确继续事项 object；可以支撑一条候选边；
- **incompatible boundary**：4.3 节双方均明确的冲突；禁止进入同一 resolution task 的可合并区域。

任何边都不能仅凭 shared issuer + family、shared period 或 shared source 成为强连接。

### 6.2 多路召回

按以下 route 独立生成候选并各自保留配额，最后去重并集：

1. `artifact/report route`：同 canonical artifact 或 report identity；
2. `issuer-period-role route`：同 issuer、canonical period、coarse role，作为高召回入口但仍需后续 bridge/semantic gate；
3. `analyst route`：institution + issuer + report date/window；
4. `transaction route`：issuer + counterparty/object/agreement；
5. `market route`：instrument + trading date + session + measure/episode；
6. `continuing-matter route`：issuer + stable object/matter；
7. `semantic route`：对完整向量做确定性近邻召回。

配额按 route 分配后再并集，避免高频 issuer/family bucket 吞掉全部 top-k。建议每 proposal 的结构化并集软上限 32，语义邻居 16，总候选上限 48；最终精排后最多 24 条边进入 task 构建。数字必须由 30 篇和冻结 MU300 的覆盖/成本回放校准，但不允许恢复 ID-first 截断。

### 6.3 轻量语义索引，不引入重型依赖

现有“只看 embedding 前 16 维符号、拆成 4 个 4-bit band”的 LSH 信息量过低。V2.1 在 `parent_occurrence_signals.py` 内实现：

- 固定种子、基于完整 embedding 的 64-bit SimHash；
- 8 个 8-bit bands；
- 每 band 允许一次受限单 bit multiprobe；
- bucket 内再算真实 cosine；
- embedding 仍使用现有批量 executor 和稳定 profile hash 缓存。

它只需要少量向量运算，不引入 FAISS/HNSW 服务或新数据库索引。目标不是构造通用 ANN 平台，而是在数千 proposal 范围内避免 `P²`，同时不因前 16 维偶然符号漏掉同义父发生。

### 6.4 候选边准入

候选 union 后，边按以下顺序处理：

1. `INCOMPATIBLE`：不进入可合并边，保留为 task hard-negative boundary；
2. 存在明确 parent-specific bridge：进入候选；
3. 无明确 bridge，但语义高度接近且至少一个结构化信号兼容：进入候选；
4. 只有 issuer/family/period/source/topic 相同：不得进入可合并边；可记为召回审计，但不发给模型占用 payload；
5. 字段未知：不自动冲突，但也不因“双方都空”形成 match。

模型返回的 `NOT_RELATED / DIFFERENT_PARENT` 必须进入 reducer 的 `hard_negative_pairs`，禁止后续通过第三个 proposal 的传递路径重新归入同一 component。

### 6.5 图切分：删除任意 connected-component chunk

现有做法把大 connected component 按 ID 排序切成固定块，既造成顺序偏差，也割断真实桥边。替换为 weighted microcomponent：

1. 候选边按 bridge strength、semantic score、cue completeness 降序；
2. 使用受 hard-negative 约束的贪心 union 构造 microcomponent；
3. 单 task 上限保持 24 proposal；达到上限时切断最弱边，不按 ID 切；
4. 被切断但仍有价值的边写入 `bridge ledger`，供 R2 缩约后重新考虑；
5. 不直接把 graph component 当成最终 Package，最终 partition 仍由 resolver 决定。

这样既能阻止一个弱边制造 supercluster，也不会把 chunk 边界永久变成碎片边界。

## 7. P1：R1/R2 改为真正单调的两轮收敛

### 7.1 R1

R1 接收 bounded microcomponent，输出 proposal/prototype 的完整 partition。Reducer：

- 校验每个 proposal 恰好一次；
- 对重复 ref 做确定性去重；
- 对 missing ref 只做该 task 的一次局部补处置，不能整轮重跑；
- 保存模型明确的 hard-negative pairs；
- 将每个 group 缩约为 prototype，并聚合全部成员 signature，而不是只取第一条 cue。

### 7.2 R2

R2 不是独立重抽样，而是对 R1 结果做单调补召回：

- 输入 R1 prototypes、未合并 proposals、R1 被 cap 切断的 bridge ledger；
- 使用新的 prototype embedding 和同一多路索引重新召回；
- 每个 R1 singleton/孤立 proposal 至少获得一个满足准入条件的语义或结构化邻居，否则明确记为“无合格邻居”，不能静默漏出 coverage 分母；
- R2 coverage 必须定义为 `R1 已覆盖有效边 ∪ R2 新覆盖有效边`，所以只能持平或上升；
- hard-negative 在 R2 继续生效；
- R2 只处理跨 R1 group 的桥接，不重新发送 R1 内部完整成员卡片。

### 7.3 Resolution Prompt 完整替换文本

```text
A proposal is one document-local candidate parent; a prototype is a provisional or existing parent. Partition them by the same bounded occurrence or continuing matter.

Merge only when the combined evidence identifies one parent-specific bridge, such as the same artifact, report, agreement, or market session, or an equivalent occurrence description with compatible role, participants, object, and time. Shared issuer, period, family, source, or topic alone is insufficient. Different child facts or values may share a parent when they are content of the same occurrence.

A disclosure, its market or analyst reaction, a separate transaction, an independent report, and background context remain different parents. Assign every proposal exactly once; use each prototype at most once; output only the schema.
```

Prompt 不要求模型输出 reason，不让模型复述 axis，也不让它承担候选召回。代码先提供边界完整、规模有限的 task，模型只做集合分区。

### 7.4 删除第三个常规模型波次

R1、R2 是唯一常规 resolution 波次。V2 的 oversized review 不再存在。只有第 8 节纯度扫描发现明确 incompatible boundary 时，才触发小范围 reconcile；正常大簇不会因 size 自动增加调用。

## 8. P1：用局部 Reconcile 替代 oversized review

### 8.1 触发条件

最终 R2 group 聚合所有成员 signature 后，仅在以下条件触发：

- 同一 group 内存在至少一对高置信 `INCOMPATIBLE`；或
- group 的 proposal lineage 显示之前局部 repartition 失败且冲突仍未消失。

以下不触发：

- group 规模大；
- child metrics 多；
- Package family 多；
- 部分字段缺失；
- 仅 semantic dispersion 较大但没有明确边界。

### 8.2 处理方式

只取冲突两侧涉及的 proposals/prototypes 和必要桥邻居，重新走同一 Resolution Schema。不得把整个 79/96-member group 展开为 Atomic 列表，也不得恢复 default + exceptions 协议。

Reconcile 后：

- 合法 partition：替换该局部子图；
- 模型失败或 coverage 非法：只按双方明确 incompatible boundary 切开；
- UNKNOWN 成员留在原主体，不因为修复失败大规模变 singleton；
- 重新跑一次 purity scan；仍有冲突则标记 `DEGRADED_BOUNDARY_SPLIT`，但 Package epoch 可继续 Apply 已得到的完整 partition。

### 8.3 为什么不会重新制造碎片

该机制的拆分单位是 proposal/prototype 局部子图，而不是全簇 Atomic exceptions；触发依据是双方完整字段的边界冲突，而不是簇大小。真实 earnings 大簇即使包含 revenue、EPS、capex、margin 和 guidance child facts，只要都由同一 disclosure artifact/occurrence 支撑，就不会被 review。market reaction、独立 SCA、独立 analyst report 则会因角色/artifact 边界被局部移出。

## 9. P1：非阻塞失败语义与恢复

### 9.1 局部失败处理矩阵

| 失败位置 | 禁止行为 | V2.1 处理 |
| --- | --- | --- |
| cue 编译某字段异常 | 整批失败、把空值当 MATCH | 该字段记 UNKNOWN，其他信号继续 |
| Induction 少量 ref 缺失/重复 | 整篇重跑或整篇失败 | 确定性去重；只补 missing/illegal ref |
| Induction 请求不可用 | 全部 Atomic singleton | 用高置信 coarse boundary 形成少量 provisional groups，UNKNOWN 归入兼容主体；标记待恢复 |
| local repartition 失败 | 回滚整篇 Induction | 仅按明确 incompatible bucket 分开 |
| R1 task 失败 | 把 task 内全部最终 singleton | proposals 原样进入 R2 retryable 集合 |
| R2 task 失败 | 丢弃 R1 已形成结果 | 保留 R1 partition，失败边保持 retryable |
| reconcile 失败 | 整个 Package stage 失败 | 仅执行高置信边界切分，其余保留 |
| 单个 Apply chunk 失败 | 重做全 epoch/模型调用 | 从冻结 partition checkpoint 重试该 chunk |

### 9.2 Provider 整体不可用的唯一特殊状态

若整个 Parent stage 无法获得任何有效模型分区，不能把“所有 Atomic 均 singleton”写成业务成功。此时：

- 上游文档、Mention、Atomic 保持成功；
- Parent epoch 保持 `FAILED_RETRYABLE`；
- 不覆盖上一个已发布 Package snapshot；
- provider 恢复后只从冻结 Atomic snapshot 重跑 Parent stage。

这不是阻塞整篇文章，而是拒绝把没有业务价值的伪 partition 发布为正式结果。除这种“完全没有可用分区”的情况外，所有校验和 repair 均局部化。

### 9.3 幂等与审计最小集

沿用现有 epoch/checkpoint/partition hash。新增 telemetry 仅记录编排层聚合数字：

- cue known/unknown coverage；
- route candidate 数、去重后候选数、cap 截断数；
- R1/R2 cumulative coverage；
- suspect proposal/group 数与局部修复状态；
- incompatible boundary 数；
- token、latency、cache hit、retry；
- partition hash 与 Apply chunk 状态。

不把逐 pair reasoning、逐字段解释或长审计对象发送给模型，也不新增模型输出负担。

## 10. P1：Token 与钟墙 one-shot 优化

### 10.1 直接删除的成本

1. 删除全文 + evidence 重复：共享 context blocks，只传短引用；
2. 删除模型生成 `package_family`；
3. 删除 size-triggered oversized review 整个波次；
4. 删除 review 专用完整 Atomic 卡片和输出字段；
5. R2 只传 prototype compact card 与新桥边，不重复 R1 全量成员；
6. 同一 proposal/prototype full card 在单个 wave payload 中只出现一次，邻接表只用请求内短 ID；
7. 不再发送 issuer/family/source-only 的弱候选 pair；
8. embedding 以稳定 content hash 跨 R1/R2/recovery 复用。

### 10.2 批量和并发

- cue/field/source 读取一次批量完成，禁止 task 内 DB N+1；
- embedding 继续使用批量 executor；
- Induction 跨文档并行；同一文档 local repair 与其他文档 Induction 可并行；
- R1/R2 task 在 wave 内并行，wave 间保持依赖；
- 保留当前较激进的 Parent 并发量级（Induction 96、Resolution 128、Reconcile 96）作为上限起点，由统一 provider controller 动态 backoff；
- 每批之间不固定 sleep 1～2 秒浪费钟墙；只在 provider 明确 rate-limit/overload 后指数退避并带 jitter；
- 单 task 上限按 proposal 数与估算 token 双预算控制，不能只看条数。

### 10.3 复杂度上界

设 proposal 数为 `P`、每 proposal 最终候选上限为 `K≤48`：

- 结构化 route 构建近似 `O(P)`；
- SimHash 建桶近似 `O(P)`；
- 精确 cosine 上限 `O(PK)`；
- LLM task 总输入随 `P` 和 compact card 近似线性增长；
- 禁止任何全局 `O(P²)` pair materialization。

300 篇下即使产生数千 proposal，候选边也由 `P×K` 上界控制，不会像深扫描 planner 一样随规模平方爆炸。

### 10.4 预期收益与风险

基于本轮真实 payload 形态而不是虚假精确估算：

| 优化 | Parent input token 预期 | 钟墙预期 | 主要风险 |
| --- | ---: | ---: | --- |
| context block 去重 | -25%～-45% | 小幅下降 | 长文背景丢失 |
| 删除 oversized review | -15%～-30% | 显著下降 | 冲突簇未清理 |
| R2 prototype-only | -15%～-25% | 中等下降 | prototype 信息不足 |
| 候选去弱边/route quota | -10%～-20% | 中等下降 | 召回漏边 |
| cache/bulk DB | token 不变 | -10%～-25% | cache key 错误 |

这些比例不能简单相加。综合目标设为：相对 V2 **干净 Package-only 单次运行**，Parent input token 至少降低 45%，钟墙至少降低 50%；若只能取得很小收益，说明重构没有消除主要成本源。

对风险的直接控制：

- compact context 对短文保留全文；仅可疑局部允许一次宽窗口升级；
- R2 bridge ledger 防止候选去弱边后漏掉真正跨文档父发生；
- purity reconcile 代替 oversized review，保留冲突清理能力；
- embedding hash 包含模型、输入 profile 与 compiler version，禁止跨版本误命中。

## 11. 代码与配置修改清单

### 11.1 `src/cdecr/parent_occurrence_signals.py`（唯一新增模块）

职责：

- `ParentBoundarySignature` 内部结构；
- 从 Atomic/Mention/Field/Source 批量编译 cues；
- coarse role 编译；
- compatible/incompatible/unknown 判断；
- route index 与配额；
- full-vector SimHash 和候选精排；
- weighted microcomponent 与 bridge ledger 所需的纯函数。

禁止职责：LLM 调用、Registry 写入、Apply、业务 Package 持久化。

### 11.2 `src/cdecr/parent_occurrence_contracts.py`

- `article` 改为 `document_context`；
- Atomic evidence 文本改为 `evidence_refs`；
- Induction output 删除 `package_family`；
- Resolution output 删除 oversized review 专用 `event_refs/includes_remaining_events`；
- 保持 request-local 短 ID 和严格 coverage schema；
- 不增加 reasoning/confidence 字段。

迁移期只在 checkpoint 读取边界提供旧 DTO 兼容转换；新调用只生成 V2.1 DTO，不维护双写。

### 11.3 `src/cdecr/parent_occurrence.py`

- `_event_cues()` 替换为统一 signal compiler；
- `_slices()` 改为共享 context block builder；
- `_candidate_keys()`、旧 score 和 ID-first bucket 截断整体删除；
- `_resolution_tasks()` 改为 route union → exact score → weighted microcomponent；
- R1 reducer 输出 bridge ledger 与 hard negatives；
- R2 基于 prototype + ledger 单调补召回；
- 删除 oversized review 选择、Prompt suffix、任务构造和 reducer；
- 新增局部 proposal repartition 与 boundary reconcile，两者复用现有两套 Schema；
- final purity scan 后再构造冻结 partition。

### 11.4 `src/cdecr/package_projection.py`

- Package family 从成员 Atomic 确定性派生；
- Apply 只接受完整 partition + version hash；
- 不改变 active membership/redirect 的原子事务和幂等语义；
- 不重新引入 N13 pair-local redirect。

### 11.5 `src/cdecr/registry.py` 与 ports

- 复用 `get_field_links_for_mentions(mention_ids)`，确保单 epoch 批量读取；
- 若当前 snapshot API 不能一次提供 Mention/Source/Field，则扩展一个只读 bulk snapshot 方法，而不是在 compiler 内跨表逐条查询；
- telemetry 使用现有审计表/阶段字段，除非现有 JSON payload 无法容纳聚合数字，否则不新建表。

### 11.6 `src/cdecr/config.py`

保留必要配置：

- 两类 task 的 proposal/token 上限；
- per-route candidate quota 与总 K；
- context soft token budget；
- provider concurrency 与 backoff；
- local repair/reconcile 最大 1 次。

删除配置：

- oversized review size/event threshold；
- review default/exception 上限；
- 旧 generic bucket head/tail 截断；
- 旧 participant/family 候选权重阈值。

不要把每种 event family 的规则、阈值都开放为环境变量。核心兼容语义集中在代码和测试中，避免配置层成为第二套业务规则系统。

### 11.7 Prompt

- `src/cdecr/prompts/v1/parent_occurrence_induction.md` 完整替换为 5.5 节文本；
- `src/cdecr/prompts/v1/parent_occurrence_resolution.md` 完整替换为 7.3 节文本；
- 局部 repartition 只追加 5.3 节一句后缀；
- 不新增第三份 review Prompt；
- Prompt version/hash 随 V2.1 更新，旧 checkpoint 不得误复用新 Prompt。

## 12. 测试设计

### 12.1 确定性单元测试

1. 使用接近真实结构的 Mention/Atomic/Field fixture，证明 metric/object/artifact/institution/session cue 可以从真实路径编译；
2. 缺字段输出 UNKNOWN，不输出 MATCH/CONFLICT；
3. 同 issuer + family 不产生强候选边；
4. 同 artifact/report 或兼容 occurrence bridge 能被召回；
5. disclosure child metrics 不因 metric 不同被拆；
6. disclosure/reaction、独立 analyst report、独立 agreement 的明确边界被识别；
7. hard-negative 不会经第三节点传递闭包重新合并；
8. SimHash 使用完整向量、结果确定、bucket 和候选 K 有界；
9. route quota 不被单一大 bucket 吞噬；
10. proposal cap 不按 ID 截断，输入顺序变化不改变 component；
11. bridge ledger 使 R2 cumulative coverage 不低于 R1；
12. compact context block 去重且所有 `evidence_refs` 可解析；
13. Package family 编译确定、同 snapshot hash 稳定。

### 12.2 Reducer 与失败测试

- Induction missing/duplicate ref 只局部补处置；
- 可疑 group 只局部 repartition，其他 group 不重跑；
- repartition 失败不会使整篇失败；
- R1 失败项进入 R2，而非最终 singleton；
- R2 失败保留 R1 partition；
- reconcile 失败只切明确 incompatibility；
- 大而纯的 disclosure group 不因 size 触发调用或拆分；
- 小而混的 group 会触发 local reconcile；
- provider 全不可用时 epoch 为 retryable 且不覆盖旧 Package；
- Apply chunk 恢复不产生新增 LLM 调用；
- 幂等重放 0 新增 Mention/Atomic/Package/call。

### 12.3 Payload 与效能契约测试

- 同一文章正文块在单个 Induction payload 中只出现一次；
- 同一 proposal full card 在同一 wave/task 中只出现一次；
- R2 不包含 R1 group 的全量 Atomic 卡片；
- 正常大簇不产生 review call；
- 候选边总量 `≤P×K`；
- SQL query 数随 Mention/Atomic 近似常数批次增长，不随 pair 数增长；
- token estimator 在调用前拆分超预算 task，并按最弱边切 microcomponent，而不是截断 JSON。

### 12.4 回归测试

- 现有 Parent contracts、Registry v12、projector、recovery 和 idempotency 测试更新到 V2.1；
- 删除只证明 oversized review 行为的测试，替换为 purity/reconcile 测试；
- 保留 incremental 与 bulk epoch 共用同一 Parent service 的测试；
- 保证兼容读取旧 Package snapshot，但新旧 checkpoint/version 不能混跑；
- 全量 CDECR 单元/集成、Ruff、mypy、`git diff --check` 全部通过。

## 13. 一次性实施顺序

虽然风险和验收仍按 P0/P1/P2 管理，代码执行不分多轮上线，不允许用开关长期并存两套机制。

### Step 0：冻结与可回滚边界

- 记录当前 commit、V1 rollback tag、V2 Registry snapshot、30 篇 corpus hash；
- 保存 V2 的 Package-only clean-run manifest；若现有累计记录无法分离干净成本，先从冻结 Atomic 做一次只用于基线计量的 V2 Package-only 重放；
- 禁止修改或覆盖现有验收 Registry。

### Step 1：合同与 signal compiler 一次落地

- 实现 `parent_occurrence_signals.py`；
- 接通 bulk Mention/Field/Source snapshot；
- 修改 compact context/evidence ref DTO；
- 更新两个 Prompt 与版本 hash；
- 完成 signal/context/contracts 单元测试。

### Step 2：候选图与 R1/R2 整体替换

- 删除旧 candidate weight/key/bucket/component chunk；
- 实现 route quota、SimHash、精排、hard negatives、weighted microcomponent、bridge ledger；
- 改写 R1/R2 reducer 和 coverage 统计；
- 旧逻辑不保留 feature flag 作为生产 fallback，避免未来两条路径漂移。

### Step 3：前移纯度控制并删除 oversized review

- 接入 Induction group purity scan/local repartition；
- 接入 final group local reconcile；
- 删除 review Prompt、合同、配置、task/reducer 与对应死代码；
- 接入局部失败语义。

### Step 4：效能收口与静态验收

- payload 去重、embedding cache、bulk DB、token budget；
- 运行确定性/集成/静态测试；
- 抽取真实 payload，人工确认一个完全无项目上下文的模型能从 Prompt + Schema 理解任务，且 Prompt 引用的所有字段真实存在。

### Step 5：按第 14 节逐级真实验收

只允许一次小范围缺陷修复机会。若冻结 30 篇 Package-only 首轮失败，应先判断是实现 bug 还是架构门槛失败：

- 明确实现 bug（字段未接通、ID reducer 错误、缓存误用）可以修复后重跑一次；
- 若仍是候选/语义架构导致的误合并或成本失败，不继续叠加规则，直接回滚 V2。

## 14. 验收设计与停止闸门

### 14.1 为什么先做 Package-only

本轮修改只影响 Package stage。先固定相同 Atomic snapshot，可以排除 Mention/N9/模型波动，直接判断父发生机制是否解决问题，并大幅降低测试成本。Package-only 通过后才运行完整 30 篇；冻结 MU300 验证扩展性，不重新跑上游真实模型。

### 14.2 Gate A：冻结 30 篇 Package-only

输入使用 V2 最终 281 Atomic snapshot，至少完成三次不同 request batching/order 的稳定性检查，其中只有一次需要作为正式真实模型计量，其余可在缓存/可复用条件下验证 reducer 顺序不敏感。

业务主闸门：

| 指标 | 硬门槛 | 目标 |
| --- | ---: | ---: |
| 最终 Package 数 | ≤100 | 65～90 |
| `Package / Atomic` | ≤35% | 23%～32% |
| Gold 可判 singleton 中应合并数 | ≤6 | ≤4 |
| 非 singleton 中需移出的误合并 Atomic | ≤10% | ≤7% |
| size≥8 Package 的误成员率 | 每簇≤10% | 每簇≤5% |
| 高置信跨 coarse-role 污染 | 0 个系统性簇 | 0 |
| R2 cumulative candidate coverage | ≥98%，且≥R1 | ≥99% |
| failure-created singleton | 0 | 0 |

已知簇只作为诊断样本而非专用优化目标：

- Micron earnings 应保留为少量父发生组件，但不得继续吸收独立 market reaction、SCA、估值/行业背景；
- global market 不得只因共同日期/恢复主题形成跨 session/instrument 的 supercluster；
- 独立 analyst report、协议/交易、产品/运营事项不得因共同 issuer 混入 disclosure；
- 同一真实 disclosure 的不同 child facts 不得因 metric/object 不同被重新拆成大量 singleton。

传统 Package Pair P/R/F1 继续报告，但不作为唯一 release gate。这里的主指标直接对应用户业务目标：Package 数、漏合并 singleton、误成员和超大簇纯度。

效能主闸门：

| 指标 | 硬门槛 | 目标 |
| --- | ---: | ---: |
| Parent stage input token | 相对 V2 clean-run 降低≥45% | 降低≥60% |
| Parent stage total token | ≤500k | ≤350k |
| Parent stage clean wall time | ≤15 分钟 | ≤10 分钟 |
| 常规 resolution waves | 恰好 2 | 2 |
| 正常 oversized review calls | 0 | 0 |
| repair/reconcile token 占 Parent token | ≤15% | ≤8% |

如果无法获得可比 V2 clean-run，绝不使用含断电/恢复/重复 review 的 2h02m 累计墙钟伪装精确提升；仍必须满足 V2.1 的绝对 token/墙钟门槛。

### 14.3 Gate B：冻结 MU300 Package-only 扩展性

只使用已经存在的 MU300 Atomic snapshot，不重跑 Mention/N9，不把测试数据换成新样本。若没有完整可用的冻结 snapshot，则该 Gate 暂停，不能用 30 篇线性外推冒充通过。

| 指标 | 硬门槛 | 目标 |
| --- | ---: | ---: |
| `Package / Atomic` | ≤32% | 15%～28% |
| 约 3,100 Atomic 时 Package 数 | <1,000 | 450～850 |
| 最大簇 | 不得为明显多父 supercluster | 大簇由同一 artifact/occurrence 支撑 |
| 抽审非 singleton 误成员率 | ≤12% | ≤8% |
| 抽审 singleton 漏合并率 | ≤15% | ≤10% |
| failure-created singleton | 0 | 0 |
| Parent input token | ≤3.5M | ≤2.5M |
| Parent clean wall time | ≤45 分钟 | ≤30 分钟 |
| Parent LLM calls | ≤350 | ≤250 |
| 候选边增长 | `O(PK)` | 无全局 pair scan |

独立 Agent 审计方式：

- 全审 size≥20 的 Package 与最大 1% Package；
- 对 size 8～19 分层抽样；
- 从 singleton Package 分层抽样判断漏合并；
- 报告“误成员占成员比例”，不能只报 bad Package 个数；
- 审计者只能依据 Atomic proposition、Evidence、source、结构化 identity 判断，不以当前 Package label 为真值。

### 14.4 Gate C：真实完整 30 篇

Gate A/B 均通过后，才运行真实 30 篇全流程，确认：

- 30/30 文档与跨文档成功；
- 上游 Mention/Atomic 指标未因 DTO/snapshot 改动回归；
- Package 主指标落在 Gate A 的容许波动内；
- token/墙钟按节点拆分，Parent 不再成为异常增长主因；
- 幂等重跑 0 新增实体/调用；
- provider/schema 局部失败不扩大为文档失败。

### 14.5 Gate D：真实 MU300

本方案**不直接授权**真实 MU300。只有 Gate A、B、C 全通过，且独立审计确认没有系统性 supercluster 后，才另行进入真实 MU300。这样不是保守，而是避免在已知候选机制仍有问题时用高成本验证必然失败的规模效应。

## 15. 回滚判定与操作边界

### 15.1 必须继续 V2.1 的条件

同时满足以下条件才可继续：

1. Gate A 所有质量和效能硬门槛通过；
2. 最大簇错误从“同 issuer/family 吸收”变为少量开放世界边界争议，而非换一种系统性污染；
3. Package 数仍保持业务可用，不通过大规模拆 singleton 换纯度；
4. local repair/reconcile 保持低触发率，没有演化成隐形第三、第四波全量调用；
5. Gate B 证明候选边、调用数和 token 随 proposal 近似线性增长。

### 15.2 任一命中即整体回滚 V2

- 冻结 30 篇非 singleton 误成员仍 >10%；
- 仍形成由 shared issuer/family/source/topic 驱动的多父 supercluster；
- 为压误合并导致 Package >100 或 singleton 漏合并超过门槛；
- R2 cumulative coverage 仍低于 R1 或 <98%；
- Parent input token 未降低 45%，或 clean wall time >15 分钟；
- 修复依赖继续增加第三套输出 Schema、每 family 特例或持续 Prompt 补丁；
- 冻结 MU300 出现近 `P²` 候选增长、Package ≥1,000 或规模越大污染率显著上升。

### 15.3 回滚方式

回滚必须同时覆盖代码和 Package 数据边界：

1. 代码恢复到 `cdecr-package-v1-baseline-20260810` 对应实现；
2. Registry 恢复 V1 Package snapshot/active membership，不把 V2/V2.1 partition 残留为 active；
3. Parent V2 Registry tables/telemetry 可只读保留作研究证据，不继续由生产写入；
4. 不只关闭一个环境开关或删除 Prompt，因为 V2 已改变 Package 生成和持久化路径；
5. 回滚后单独规划 V1 的低风险聚合增强，不把 V2.1 代码片段重新零散移植。

## 16. 主要风险与全局权衡

| 风险 | 可能后果 | 本方案的控制 | 不采用的低性价比做法 |
| --- | --- | --- | --- |
| 边界信号过强 | earnings child facts 被拆碎 | 只有双方明确才 incompatible；metric 差异不等于父冲突 | 对每个 metric/object 设置 hard cannot-link |
| 信号缺失 | 漏候选或错误合并 | 缺失=UNKNOWN；多 route + semantic 邻居 + R2 ledger | 把空字段当相同或不同 |
| compact context 丢语义 | Induction 误分 | 短文全文；Evidence 窗口优先；可疑组一次扩窗 | 所有文档永久发送全文 |
| 语义索引漏边 | Package 碎片化 | 结构化 route 并行召回、full-vector SimHash、R2 单调补召回 | 全局 pair scan |
| 候选图传递污染 | supercluster | hard negative、弱边不 union、bounded weighted component | connected component 直接当父事件 |
| reconcile 过多 | token/墙钟反弹 | 仅明确 conflict 触发、最多一次、局部 proposal 输入 | 所有 size≥N 大簇 review |
| 为压 Package 数纵容误合并 | 结果不可用 | 同时 gate Package 数、误成员率、超大簇纯度 | 只报 Package 数或 Pair Recall |
| 为提纯度制造 singleton | 聚合价值丢失 | singleton 漏合并绝对数和比例双门槛 | 失败即 CREATE_NEW |
| 上游 Atomic 已污染 | Package 无法内部拆分 | 指标中单列 inherited pollution；不把它错归 V2.1 | 在 Package stage 偷做 Mention/Atomic 重构 |
| 300 篇 provider 波动 | 结果/时间不稳 | frozen Package-only 先验证；provider backoff；checkpoint | 降并发到极保守串行 |

这里最重要的权衡是：**Parent-specific bridge 负责限制误合并，R2 bridge ledger 负责补回漏合并。** 二者必须同时落地。只做前者会回到海量碎片，只做后者会重现 supercluster。

## 17. Prompt、Schema 与业务逻辑对齐复核

站在一个完全不了解 CDECR 的模型视角，实施完成后必须逐项回答“是”：

1. Prompt 是否先定义 parent 是什么、明确它不是什么？
2. Prompt 是否明确任务是 partition，而不是 pair classification 或文章摘要？
3. Schema 是否能表达每个输入恰好归属一次？
4. Prompt 提到的 family、participant、object、artifact/report、metric、time 是否真的出现在输入，而不是像 V2 一样名义存在、实际全空？
5. `document_context` 的 `[B#]` 与 Atomic `evidence_refs` 是否可互相解析？
6. 模型是否知道 disclosure child fact 可以共享父发生，而 reaction/report/transaction/background 通常不同？
7. 模型是否知道 shared issuer/period/family/source/topic 不充分？
8. Resolution 输入是否清楚区分 proposal 与 prototype？
9. 输出是否只要求模型不可替代的 partition/relation，不要求它重复确定性可编译的 family、置信度或审计原因？
10. 局部 repair 是否复用相同语义和 Schema，而不是让模型猜测新的修复协议？

任一项为否，不允许通过“validator 再严格一点”掩盖 Prompt/输入不对齐；必须先修合同或 Prompt。

## 18. 最终交付与验收报告要求

实施轮最终必须交付：

1. V2.1 代码、测试、迁移说明和 changelog；
2. 删除的旧 V2 路径清单，证明没有双活/死代码；
3. 两份真实 Prompt 的最终全文和严格 JSON Schema diff；
4. 30 篇 Package-only A/B 报告：Package 数、singleton 漏合并、非 singleton 误成员、超大簇、候选覆盖、token、调用、墙钟；
5. 冻结 MU300 Package-only 扩展报告；
6. 独立 Agent 逐簇审计报告；
7. 真实完整 30 篇报告；
8. 明确的“继续 / 回滚”结论，不使用“技术通过但质量待观察”替代发布判断。

报告必须把以下口径分开：

- clean single-run 与累计 recovery/retry；
- inherited Atomic pollution 与 Package 新增污染；
- 可判 singleton 比例与全量保守下界；
- Package 个数与 Package 纯度；
- 30 篇可比结果与 MU300 规模扩展结果。

## 19. 结论

当前 V2 **不应回滚其方向，但必须回滚其核心实现思路**：

- 保留：Parent-first、文档内 proposal、两轮 set resolution、冻结 partition Apply、bulk/incremental 共用服务、失败可恢复；
- 替换：贫乏 cue 编译、issuer/family 弱边、ID-first 截断、任意 component chunk、非单调 R2；
- 删除：全文重复 payload、模型生成 family、size-triggered oversized review、default + exceptions 协议；
- 新增但严格受限：一个内部 signal/index 模块、proposal 局部 repartition、冲突局部 reconcile。

这是一条值得执行的 one-shot 方案，因为它同时攻击已证实的误合并根因和成本根因，又保留 V2 已经证明的低 Package 数价值。它不是无限试错授权：**冻结 30 篇 Package-only 只允许一次实现级修正；不能同时通过“Package≤100、误成员≤10%、R2 coverage≥98%、input token 下降≥45%”时，立即整体回滚 V2。**

换言之，V2.1 的使命不是把现有指标稍微修好，而是证明 Parent-first 能否在不产生 supercluster 的前提下以近线性成本压缩到业务可用的 Package 数。证明成功则继续；证明失败则停止。

## 附录 A：关键证据到修改项的映射

| 已确认事实 | 直接修改 |
| --- | --- |
| 107 proposal 的 artifact/metric/object cue 均为 0 | 批量 `ParentBoundarySignature` compiler |
| participant-family route 单独产生大量候选 | issuer/family 降为 recall hint，不能作为桥 |
| 同 family+participant 1.4 越过 1.35 | 删除旧加权阈值，改 parent-specific bridge 准入 |
| 最大簇 17/27 proposal 在 Induction 已污染 | Induction 后局部 purity scan/repartition |
| R1/R2 coverage 91.59%/87.80% | R2 继承 ledger，cumulative coverage 单调 |
| 通用 bucket 先截 96 再评分 | 先 route/score，再按配额截断 |
| connected component 按 ID 切块 | weighted microcomponent + 弱边 ledger |
| review 减 92 FP 却增 127 FN | 删除 oversized review，局部 conflict reconcile |
| 累计 input +93.53%、elapsed +557% | shared context、prototype-only R2、删 review、clean-run 计量 |
| 281 Atomic→75 Package | 保留 Parent-first 与冻结 partition Apply |

## 附录 B：实施中不得偏离的五条红线

1. 不得恢复“同 issuer + family/period 即可合并”的任何变体。
2. 不得为每个已知 bad case 新增专用字段、Prompt 例子或 hard rule。
3. 不得把缺失 cue 当 MATCH，也不得把 UNKNOWN 当冲突。
4. 不得用全簇 Atomic review、全局 pair scan 或失败全 singleton 解决问题。
5. 不得以较低 Package 数掩盖误合并，也不得以较高 Precision 掩盖 Package 爆炸。
