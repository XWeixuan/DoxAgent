# CDECR Parent Occurrence Package V2.0R：基于真实 Bad Case 的语义回退与既有问题修复方案

> 日期：2026-08-13  
> 性质：可直接执行的一次性代码回退方案；本文件只制定方案，不执行代码修改或真实模型测试。  
> 回退方式：以当前分支为基础纯改代码，必要时只用 `git show 238e3f9:<path>` 读取 V2.0 参考实现；禁止 `git checkout`、`git revert`、`git reset` 或覆盖当前分支的其他改动。  
> 目标版本代号：`Parent Occurrence Package V2.0R`（V2.0 的业务语义 + 已验证的工程优化 + 对 V2.0 既有问题的必要修复）。

---

## 0. 最终决策

### 0.1 结论

V2.1 已充分达到语义回退门槛，但不能整体恢复 V2.0 原代码。

建议一次性落地 **V2.0R**：

1. 恢复 V2.0 的核心业务语义：Parent 身份由模型综合证据做集合分区；字段匹配或冲突只是证据，不是 Atomic 级确定性身份规则。
2. 整体撤销 V2.1 的 bridge-only 候选准入、跨波次 hard-negative 闭包和 final Atomic purity split。
3. 保留 V2.1 已证明有价值的 compact context、短 ID、两轮 Resolution、批量快照、缓存、token budget、checkpoint、幂等和相关性 Enforce。
4. 保留并修正文档内 Induction 对 `disclosure / reaction / independent report / agreement / background` 的拆分能力；这是阻止 V2.0 supercluster 的第一道也是最重要的防线。
5. 用 **proposal 级合并准入复检** 修复 V2.0 的误并，不再在最终 Package 上按 Atomic 字段二次切簇。
6. 恢复 V2.0 的宽候选召回，但修复其 ID-first 截断、R2 非单调和大连通分量任意切块问题。
7. 不恢复 V2.0 的 `default parent + explicit exceptions` oversized review。它已经被真实数据证明是有损切割协议。

这不是在 V2.1 上继续叠加补丁，也不是把代码机械退回旧版本，而是删除 V2.1 中已经证明方向错误的身份执行层，将有效工程能力收敛到一条 V2.0 业务主线。

### 0.2 为什么这一回退不应更保守

严格共同 Gold 50 项上：

| 运行 | Precision | Recall | F1 |
| --- | ---: | ---: | ---: |
| 正式 V2.0 | 73.23% | 44.08% | 55.03% |
| V2.1 R3 | 97.56% | 18.96% | 31.75% |
| V2.1 变化 | +24.33pp | **-25.12pp** | **-23.28pp** |

V2.1 将系统从“过并且仍碎”推成“几乎不误并但严重过碎”。这不是合理的 Precision/Recall 交换：F1 下降 23.28pp，Package singleton 从 36.00% 上升到 73.95%，Micron FQ3 在严格共同 21 项上从 6 个组件恶化为 10 个组件。

因此，回退不能只调低几个阈值。必须删除造成系统性偏置的三段语义：

```text
bridge 才准入候选
        ↓
不同模型分组永久固化为 hard negative
        ↓
最终按 Atomic 字段冲突再次 deterministic split
```

只修其中一段，另外两段仍会把 Recall 拉回低位。

---

## 1. 审查依据与可比性边界

### 1.1 主要证据

- `CDECR_PARENT_OCCURRENCE_PACKAGE_V2_30_REAL_ACCEPTANCE_REPORT_20260811.md`
- `CDECR_PARENT_OCCURRENCE_PACKAGE_V2_DEEP_DIVE_AUDIT_20260812.md`
- `CDECR_PARENT_OCCURRENCE_PACKAGE_V2_1_30_REAL_ACCEPTANCE_REPORT_20260813.md`
- V2.0 冻结 Registry、最终 clusters、Package Gold eval
- V2.1 R3 冻结 Registry、最终 clusters、Package Gold eval
- 当前 `src/cdecr/parent_occurrence.py`
- 当前 `src/cdecr/parent_occurrence_signals.py`
- Git `238e3f9` 中的 V2.0 Parent 实现，仅作逐函数参考

### 1.2 能够确认的因果与不能夸大的部分

能够确认：

- V2.0 的 79-Atomic Micron earnings supercluster 是从 Induction 污染开始，并在宽松 Resolution 中扩大；17/27 个支持 proposal 在文档内已经混入异类 Atomic。
- V2.1 确实消除了同等级 supercluster；这是 Induction 拆分、边界约束和最终切分共同作用的结果。
- V2.1 Recall 回归由两个独立机制共同造成：候选覆盖只有 57.58%，以及 final split 对已经成组的父发生继续拆分。
- V2.1 当前最终 119 Package 内 deterministic incompatible pair 为 0，但这是 33 次 final boundary split 换来的，不代表模型语义聚类自然达到纯度。

不能夸大：

- 两轮 Mention/Atomic 数量不同，只有严格共同 Gold 50 项是直接质量 A/B。
- V2.0 的旧 coverage 91.59%/87.80% 与 V2.1 lineage coverage 定义不同，不能直接写成 coverage 下降 34pp。
- Gold 只覆盖当前 Atomic 的一部分，所以具体 bad case 用于识别通用错误模式，不能将某个公司或某篇文章写成生产规则。

---

## 2. 真实 Bad Case 到失败机制的逐项还原

## 2.1 V2.0：文档内 proposal 原生污染是 supercluster 的入口

### Case V2-A：同一文章把 earnings、reaction 和经营背景放入一个 proposal

文档 `4 Blowout Numbers From Micron's Earnings Investors Need To See`：

- V2.0 proposal `c4b0e1ee...`，标题为 `Micron's fiscal Q3 2026 earnings report and related announcements`；
- 9 个 Atomic 同组；
- 除 revenue、gross margin、operating margin、net income 外，还包含 after-hours +15% 股价反应、销售价格、PC/手机销量下降和数据中心销量展望。

V2.1 对相同文档输出 6 个 proposal：

- Q3 earnings disclosure；
- Q4 guidance；
- pricing / market-unit dynamics；
- shortage outlook；
- management statements；
- stock reaction。

**判定：V2.1 的文档内边界定义有效，不能回滚。** 应保留 compact context 和当前 Induction Prompt 的核心边界，但删除其后基于不可信字段的确定性切分。

### Case V2-B：earnings、SCA、市场反应和估值被共同上下文吞并

文档 `Micron Just Guided for a Staggering $50 Billion in Fiscal Q4 Revenue...`：

- V2.0 proposal `9a28e94e...` 含 19 个 Atomic；
- 混入 Q3 results、Q4 guidance、SCA、HBM4 shipment、market cap、after-hours reaction、forward P/E 和 contract uncertainty。

V2.1 将其分成 5 个 proposal：

- SCA announcement；
- Q4 guidance；
- Q3 earnings；
- market reaction；
- HBM4 production/shipment。

**判定：保留 V2.1 的 Induction 语义。** V2.0R 不允许恢复“文章里的 related announcements 都属于 earnings parent”这一捷径。

### Case V2-C：同一个错误模板在多文档复现

- `43511df0...`：earnings、memory shortage、行业观点、分析师周期判断和 market reaction 同组；
- `f8a736c9...`：label 直接写成 `earnings release and investor reaction`；
- `e35c44e...`：label 直接写成 `earnings report and market reaction`。

这说明 V2.0 不是某一条数据偶发误判，而是 Induction 对“父发生包含关系”约束不足。V2.0R 必须保留这轮 Prompt 修正，不能简单恢复旧 Prompt 全文。

## 2.2 V2.0：跨文档 Resolution 把共同上下文当父身份

### Case V2-D：79-Atomic Micron earnings supercluster

最终 `4723fa8c...` / parent group `c1f6d551...`：

- 79 Atomic；
- 主审计估计 29/79 为错误成员；
- 错误成员包括独立 SCA、market/analyst reaction、forward valuation、行业/产品叙事和 shareholder-return matter；
- 同时真正 earnings facts 又散落在另外 11 个组件中。

失败不是“召回太高”，而是候选、模型和 Apply 没有区分：

```text
同 issuer / period / family / source context
        ≠
同一个 bounded parent occurrence
```

V2.0R 不能只恢复 V2.0 Resolver。必须在 proposal 形成质量和 proposal 合并准入处拦截 `disclosure ↔ reaction / independent report / agreement / background`，否则 supercluster 会复现。

### Case V2-E：Tuesday selloff 与 Thursday recovery 被合成一个父发生

V2.0 `710a1841...`：14 Atomic，至少 7 个属于另一个 occurrence。共同 AI 主题不能把不同交易日、不同方向的市场波次合成一个父发生。

但 V2.1 又把同一个 `Global AI stock selloff on Tuesday June 23` proposal 内的 SK Hynix/Samsung 与 Micron 两条 Atomic 按 issuer 拆成两个 singleton。

**判定：**

- 日期/session/方向不同是强分离证据；
- 同一显式 market episode 内 instrument/issuer 不同不是冲突；
- 因此保留 normalized market time/session 作为模型证据和窄准入边界，撤销 market proposal 内按 issuer 切分。

### Case V2-F：不同分析师报告被共同“coverage”吞并

V2.0 `b5172546...` 将 Mizuho target、BofA valuation 和独立 capital-return 分析合成一个 Package。

**判定：** canonical analyst institution/report identity 是高收益边界，应在 proposal 级合并复检中保留；但同一 Citi/Wedbush 报告里的 target、rating、forecast/risk 不能因为 Atomic family 或局部 issuer 字符串不同而被拆。

## 2.3 V2.0：oversized review 修错方向且代价过高

67-Package 中间态到 75-Package 最终态：

| 指标 | 变化 |
| --- | ---: |
| Precision | +1.90pp |
| Recall | **-15.97pp** |
| F1 | **-10.45pp** |
| FP | -92 |
| FN | **+127** |

`default parent + explicit event exceptions` 让模型必须在大簇里找齐所有例外；漏列的错误项留在 default，误列的真实事实被拆走。它既没有清干净 79-member 大簇，又显著增加碎片。

**判定：不恢复。** 大簇大小只用于验收和人工 review，不触发第三套输出协议，也不触发 Atomic 级重分区。

## 2.4 V2.1：bridge-only 候选准入使正确父发生根本没有相遇

V2.1 R3：

- R1 lineage coverage：74/132 = 56.06%；
- R1∪R2 累计：76/132 = 57.58%；
- R2 只新增 2 个 lineage；
- 20,394 次 pair evaluation 后仍有 56 个 lineage 无合格邻居。

当前 Resolution Prompt 又规定：

> Merge only when the combined evidence identifies one parent-specific bridge...

同一父发生的跨文档表达通常只有“语义等价 + 若干兼容 cue”，不一定拥有相同 artifact ID、report ID 或规范化 period。将 bridge 同时设为候选门槛和模型决策门槛，相当于把字段召回率上限变成 Package Recall 上限。

**判定：整体撤销 bridge-only admission。** Bridge 继续参与排序，不再决定“是否允许相遇”或“是否允许合并”。

## 2.5 V2.1：final split 在同一个模型父发生内部制造大规模假冲突

V2.1 最终：

- final boundary split = 33；
- 19 个已成组父发生被扩成 52 个子组；
- 42 个子组为 singleton。

用当前真实 `ParentBoundarySignature` 对冻结 R3 的 19 个 split origin 重算：从每个 origin 的最大子组之外共分离 34 个成员，其主导切分原因是：

| 主导原因 | 被分离成员 |
| --- | ---: |
| issuer conflict | **27** |
| 当前函数并无直接 incompatible | 3 |
| coarse role conflict | 2 |
| market scope conflict | 1 |
| artifact conflict | 1 |

这 27 个 issuer 冲突并不是可靠公司身份冲突。真实输入包括：

- `Micron` vs `Micron's cloud memory business unit`；
- `Micron` vs `Management`；
- `Micron` vs `Wednesday's report`；
- `Micron` vs `all four of Micron's business units`；
- `Micron` vs `the company`；
- `Micron` vs `core data center unit`。

当前 compiler 把 participant surface、局部 field identity 和语法主语共同塞进 `issuer_ids`；`_disjoint_known()` 再把它们当成同精度身份做 hard conflict。这是结构性类型错误，不是阈值过严。

### Case V2.1-A：同一 earnings disclosure 被二次切成 `29+2+1×6`

同一组 18 个 earnings proposals 已被 R1 合并为 `Micron fiscal Q3 2026 earnings disclosure`，final split 又把 37 个 event 分为 8 个组：

- 主组 29；
- Cloud Memory revenue + margin 2；
- `Wednesday's earnings report was virtually flawless` 1；
- Core Data Center revenue 1；
- disciplined spending 1；
- all business units growth 1；
- earnings report summary 1；
- adjusted gross margin 1。

这些分离项不是另一个父发生。它们只是主语粒度、业务单元、metric 或 artifact cue 不一致。

**判定：删除 final Atomic purity split，不做阈值微调。**

### Case V2.1-B：同一 proposal 被 final split 拆碎

以下结果尤其能证明 final split 的业务层级错误：

- Citi post-earnings note proposal `P52` 的两个事实被拆成两个 singleton，因为一条是 `ANALYST_ACTION`，另一条 forecast 被编成 `DISCLOSURE`；
- Global Tuesday selloff proposal `P101` 的两条跨股票事实因 issuer 不同被拆；
- SCA terms proposal `P106` 的两条条款因 `Micron` vs `The deals` 被拆；
- 一个 earnings-call commentary proposal 被拆为 `2+1×6`；
- Q4 guidance 同父组被拆为 `5+1+1`，其中 `Management` 被误当成另一个 issuer。

成功 Induction proposal 是文档内已经结合全文证据形成的父发生单位。最终按 Atomic 局部字段把它拆开，实际上用低上下文确定性代码覆盖了高上下文模型判断。

**V2.0R 不允许任何跨文档阶段拆开一个成功且 coverage 合法的 Induction proposal。** 若文档内 proposal 确实可疑，只能在 Induction 后立刻用同一文档上下文局部重分区。

## 2.6 V2.1：模型“不同组”被永久升级为 hard negative

当前 `_resolve_wave()` 会把同一 task 中被模型放入不同输出组的每一对 proposal 写入 `stable_hard_negatives`，并在后续波次禁止它们经新证据重新合并。

模型的一次局部分区只表示“在当前 task/context 下没有合并”，不等于确定性 cannot-link。把它永久升级会造成：

- R1 一次保守判断无法被 R2 纠正；
- task context 缺失被固化为身份边界；
- 一个弱判断通过传递闭包切断更多正确路径；
- R2 名义上补召回，实际上受 R1 hard-negative 限制只能新增 2 个 lineage。

**判定：整体撤销模型派生 hard negative。** 只有明确、可信、proposal 级的 merge guard 可以阻止一次合并；它不写回永久负边集合。

---

## 3. V2.1 修改项逐项回退判定

| V2.1 修改项 | Bad case 影响 | V2.0R 决策 |
| --- | --- | --- |
| shared `document_context` + `evidence_refs` | 降低重复 token，未发现质量副作用 | **保留** |
| Induction 删除模型 `package_family` | 减少无关分类负担 | **保留** |
| 当前 Induction Prompt 的 disclosure/reaction/report/agreement 边界 | 修复 `c4b0/9a28/f8a7/e35c` 原生污染 | **保留并小幅澄清** |
| Induction suspect local repartition | 能局部修复混合 proposal | **保留机制，收窄触发；删除失败后的确定性碎拆** |
| `ParentBoundarySignature` | 有用作 cue 汇总，但内部类型混杂 | **保留数据通道，重写可信度与用途** |
| bridge-only candidate admission | coverage 57.58%，大量正确 proposal 不相遇 | **撤销** |
| V2.0 式宽 recall keys | V2.0 召回较高，但有 ID-first 截断 | **恢复语义，修复排序和配额** |
| full-vector SimHash / embedding cache | 比旧 16 维 sign-band 稳定，属工程优化 | **保留** |
| weighted microcomponent | 作为装箱比 ID chunk 好 | **仅保留为 task packing，不再作为身份裁决** |
| deterministic hard-negative candidate exclusion | 提升 P，但切断正确连接 | **撤销** |
| 模型不同组 → stable hard negative | R2 无法纠正 R1 | **撤销** |
| `incompatible_pairs` 进入模型 payload | 增加模型负担并预设答案 | **删除** |
| final Atomic boundary purity split | 19 组→52组，42 singleton；issuer 主导 27/34 | **整体删除** |
| local reconcile | 当前没有解决 18 个 conflict group，最终交给 split | **改为 proposal 级合并修复，失败则保留波次前结构** |
| oversized review 删除 | V2.0 review 已证实有损 | **继续删除，不恢复** |
| 两轮 R1/R2、checkpoint、hash、批量调用 | 工程和恢复能力有效 | **保留** |
| prompt/schema/compiler version 进入 checkpoint hash | 防旧结果误复用 | **保留并更新版本** |
| relevance Enforce | 69 个明确 irrelevant 未入下游 | **完全保留，非 Parent 回退范围** |

---

## 4. V2.0R 的业务不变量

### 4.1 Parent 的定义不变

Parent 是包含一个或多个 Atomic 的有边界现实发生、披露、过程或可识别持续事项。它不是：

- 文章；
- 公司、ticker 或人物；
- 宽泛主题；
- 单独日期/季度；
- 共同来源或共同上下文。

### 4.2 三条必须由代码保证的层级边界

1. **Atomic 是最小事实。** Package 不负责修补 N9 的 Atomic 错并或漏并。
2. **Induction proposal 是文档内父发生假设。** 成功、coverage 合法的 proposal 在跨文档阶段不可按 Atomic 字段拆开。
3. **Resolution 合并 proposal，不合并字段。** 它可以合并包含不同指标、业务线、数值和表述粒度的 proposal，只要它们属于同一个父发生。

### 4.3 字段的用途重新统一

| 信号 | 候选召回 | 模型输入 | 合并准入复检 | 最终 Atomic split |
| --- | --- | --- | --- | --- |
| issuer / family / period / source | 是 | 是 | 否（单独不足） | 禁止 |
| semantic similarity | 是 | 是 | 否（单独不足） | 禁止 |
| trusted first-party artifact | 是 | 是 | 仅高可信冲突可触发局部复核 | 禁止 |
| canonical analyst institution | 是 | 是 | 两个独立 report proposal 明确不同时可拒绝合并 | 禁止 |
| normalized market date/session | 是 | 是 | 双方明确且不同 occurrence 时可拒绝合并 | 禁止 |
| coarse parent role | 是 | 是 | 仅 proposal 级明确 disclosure/reaction/report/transaction 边界 | 禁止 |
| object / metric / participant surface | 是 | 是 | 否 | 禁止 |

关键原则：**candidate recall 与 merge admission 分离；merge admission 与 final partition 分离。** 任何 cue 都不能在所有阶段重复变成一道硬门。

---

## 5. 一次性目标架构

```text
冻结 Atomic / Mention / Field / Source snapshot
        ↓
Document-local Induction
        ↓
仅对真正 mixed-role proposal 做一次局部重分区
        ↓
V2.0-style broad multi-route recall
        ↓
score-before-cap + bounded task packing
        ↓
R1 set partition
        ↓
proposal-level merge admission repair（只检查本轮新合并）
        ↓
R2 对全部残余与截断桥重新召回
        ↓
proposal-level merge admission repair
        ↓
ownership / coverage / schema 校验
        ↓
Freeze + Apply
```

明确不存在：

- candidate hard-negative graph；
- 模型输出派生的永久 cannot-link；
- final Atomic purity split；
- oversized default/exception review；
- 第三套模型 Schema；
- N13 pair-local Apply；
- 通过 Package 粗并修补 Atomic。

---

## 6. 可落地修改方案

## 6.1 `parent_occurrence_signals.py`：从身份执行器退回 cue compiler

### 6.1.1 修复 cue 类型污染

`compile_parent_signatures()` 改为：

- `issuer_ids` 只接受 canonical company/entity ID；不再把 participant surface、`Management`、`the company`、业务单元、report subject 当 issuer；
- `artifact_ids` 只接受 canonical artifact namespace、明确 first-party filing/announcement identity 或显式 parent-message identity；不再把 `fiscal Q3`、report date、普通 open attribute 值当 artifact ID；
- `institution_ids` 只接受 canonical institution identity；
- `market_scope` 分开编译 instrument、date、session、measure，空值不构成 match；
- `object/metric/counterparty` 继续保留为 cue，但不参与最终 hard split；
- 所有无法确认类型的值降为普通文本 cue，只给模型看或参与软排序。

不新增持久化字段，不改变外部 Schema。可信度只体现在 compiler 选择什么进入已有内部集合。

### 6.1.2 删除通用 `boundary_compatibility()` 的 hard identity 职责

删除或停止业务路径消费当前 `COMPATIBLE / INCOMPATIBLE / UNKNOWN` 作为候选和最终切分裁决。替换为两个职责更窄的纯函数：

```python
recall_features(card) -> soft keys / scores
proposal_merge_guard(left, right) -> PASS | REVIEW(reason)
```

`proposal_merge_guard()` 只能对双方证据完整的 proposal 生效，允许的 REVIEW 原因限制为：

1. 明确 disclosure 与明确 market reaction；
2. 明确独立 analyst report 与 disclosure/market reaction；
3. 两个 analyst-report proposal 的 canonical institution/report identity 明确不同；
4. 明确独立 agreement/transaction 与 earnings disclosure；
5. 两个 market-occurrence proposal 的 normalized date/session 明确不同，且没有同一显式 roundup/episode 证据。

它不直接返回 `SPLIT`，只让本次合并进入一次局部普通 Resolution repair。issuer 不同、metric 不同、family 不同、source 不同、字段为空一律不能单独触发。

### 6.1.3 保留 SimHash，但取消近似索引的裁决权

保留 full-vector 64-bit SimHash、band lookup 和真实 cosine 精排；它只负责从大集合中找语义邻居，不参与 hard boundary。

旧 V2.0 的 16 维 sign-band 不恢复。

## 6.2 `parent_occurrence.py`：Induction 保留纠错，失败不扩散

### 6.2.1 保留 compact DTO

继续使用：

- `document_context`；
- `evidence_refs`；
- request-local Atomic ref；
- 编排层确定性 package family；
- 同一 Induction Schema。

不恢复全文在每个 Atomic 中重复，不恢复模型生成 `package_family`，不增加 reasoning/confidence/axis 输出。

### 6.2.2 收窄 suspect group 触发

`_repartition_suspect_groups()` 只在文档内 proposal 同时含明确的父类型交叉时触发，例如：

- disclosure + market reaction；
- disclosure + independent analyst report；
- disclosure + independent agreement/transaction；
- analyst reports with different canonical institutions；
- different normalized market occurrences。

以下不得触发：

- issuer surface 不同；
- metrics / business units / objects 不同；
- FINANCIAL_PERFORMANCE 与 GUIDANCE_EXPECTATION 同时出现；
- 同一 call 中 statement family 不同；
- group 大；
- cue 缺失。

### 6.2.3 局部请求失败时保留原 proposal

当前“失败后按 deterministic bucket 拆开”的 fallback 改为：

```text
local repair 成功且 coverage 合法 → 使用修复 partition
local repair 失败/仍非法        → 保留原 Induction group，标记 repair_failed
```

失败不能把每条 Atomic 变 singleton，也不能使文档失败。这个选择可能暂时保留一个 mixed proposal，但其影响局限于该 proposal；相比无证据碎拆，是更高价值的保守降级，并可在下一次 checkpoint 恢复中重试。

## 6.3 恢复 V2.0 宽候选语义，修复其三个工程缺陷

### 6.3.1 召回 routes

恢复 V2.0 的软召回思想：

- event overlap；
- participant + family；
- participant + period；
- period + family；
- artifact；
- object + family；
- metric + period；
- semantic neighbor。

同时使用当前已编译的 institution、market scope、counterparty 作为额外 route。所有 route 都只召回，不决定 SAME/DIFFERENT。

### 6.3.2 必须修复 V2.0 的 ID-first 截断

旧实现先做：

```python
for ref in sorted(proposal_refs)[:96]:
```

再计算 score。高质量候选可能仅因 ID 排序被丢弃。

V2.0R 必须：

1. 每 route 先做 bounded retrieval；
2. 去重形成 union；
3. 对 union 计算 exact score；
4. 最后按 score 和 route diversity 截断。

不得在 score 前按 ID 截断。

### 6.3.3 适度激进的候选额度

默认建议：

- 每 route 最多 16 个邻居；
- semantic route 最多 24 个；
- union 精排后每 proposal 保留 32 条候选边；
- 对“无合格邻居”的 proposal，补入同 issuer/time window 的 top 4 与全局 semantic top 4；
- fallback 候选仍由模型判断，不自动合并。

这些是一次性配置项，不按 event family 建立一套阈值矩阵。实际实现前用冻结 R3 proposal 做无模型 candidate replay，确保 lineage coverage ≥95%，再固定值。

### 6.3.4 task packing 不再裁决身份

保留 weighted microcomponent 只用于把候选边装入 bounded task：

- 强边优先；
- task 超限切最弱边；
- 被切边进入 R2 ledger；
- 不使用 hard negative 阻止 component；
- 不把 connected component 本身当 Package。

这保留 V2.1 的工程可扩展性，同时恢复 V2.0 的集合判断权。

## 6.4 R1/R2 恢复真正的二次判断

### 6.4.1 R1

- 对宽召回候选 task 做完整集合分区；
- 只校验 ref 唯一、覆盖完整和 prototype 引用合法；
- 不校验 cue 冲突；
- 不传 `incompatible_pairs`；
- 模型分成不同组不生成 stable hard negative。

### 6.4.2 proposal 级合并准入 repair

对 R1 新产生的每个多 proposal group：

1. 在 proposal 聚合层运行 `proposal_merge_guard()`；
2. 无 REVIEW：直接接受；
3. 有 REVIEW：只把该组 proposals 用同一 Resolution Schema 重判一次，并把冲突 cue 作为普通 card 字段提供；
4. repair 成功且不再触发 REVIEW：接受；
5. repair 失败或仍跨明确边界：拒绝这次新 union，恢复为本波次输入结构。

“恢复为本波次输入结构”的含义：

- R1：保留各 document proposal；
- R2：保留 R1 prototype；
- 绝不拆开 proposal 内 Atomic；
- 绝不使整轮或文档失败。

这是 V2.0R 唯一的确定性 merge admission，作用点只在**新合并**，不是全局重写 partition。

### 6.4.3 R2

R2 输入：

- R1 prototypes；
- R1 未进入任务的 proposal；
- R1 被 cap 切断的 bridge ledger；
- 每个 singleton/residual 的 fallback neighbors。

R2 必须对全部当前 root 重新编译 profile 和 embedding；不得复用合并前 cue。R2 继续宽召回，不继承模型 hard negative，只避免在同一波次重复判断完全相同的 pair。

coverage 定义固定为：

```text
拥有至少一个已发送候选邻居的原始 lineage / 原始 lineage universe
```

同时报告 R1、R2 新增和累计覆盖；R2 累计只能上升。

## 6.5 删除 final split 与 Atomic 级 reconcile

删除活动路径中的：

- `_enforce_final_boundary_purity()`；
- `_deterministic_repartition_group()` 在最终 partition 的调用；
- final `BoundaryCompatibility.INCOMPATIBLE` pair scan 触发拆组；
- final split telemetry 作为业务成功条件。

最终阶段只允许：

- 每个 event 恰好属于一个 group；
- group 引用存在；
- active membership ownership 唯一；
- schema/hash/version 合法。

这些错误会导致 Apply 无法正确落库，所以可以阻止该 Apply chunk；其他语义不一致只记录 compact audit，不阻塞文档和 epoch。

## 6.6 不恢复 oversized review

删除状态保持不变：

- 不按 size 自动调用模型；
- 不恢复 `event_refs/includes_remaining_events`；
- 不恢复 default/exception suffix；
- 不维护第三套 review validator。

超大簇只进入离线验收报告。在线质量由文档内分区、两轮 proposal Resolution 和 proposal-level admission 保证。

## 6.7 checkpoint 与恢复

- bump Parent contract/prompt/compiler version；
- input hash 必须包含 Prompt、Schema、compiler version、candidate policy version 和 payload；
- 旧 V2.1 checkpoint 不得被 V2.0R 复用；
- provider 整体不可用时 Parent stage `FAILED_RETRYABLE`，不发布全 singleton 伪结果；
- 单 task 失败只保留波次前结构并进入后续/重试集合。

---

## 7. Prompt 与 Schema 最终方案

## 7.1 Schema

保持当前两套输出 Schema，不增加字段：

### Induction 输出

- `local_group_id`
- `scope`
- `label`
- `members.atomic_ref`
- `members.membership_relation`
- `external_links`

### Resolution 输出

- `resolution_group_id`
- `proposal_refs`
- `existing_parent_refs`
- `canonical_label`

删除动态模型 payload 中的 `incompatible_pairs`。内部 card 继续携带现有 cues，不新增 parent type、reasoning、confidence、conflict axis 或 merge reason。

## 7.2 Induction Prompt 完整文本

```text
A parent is a bounded real-world occurrence, disclosure, process, or identifiable continuing matter. It is not an article, entity, topic, period, or shared background.

Partition every supplied Atomic exactly once using the document context and evidence. Group different child facts when the evidence shows that they are content of the same parent. Separate a market or analyst reaction, an independent report, a separate agreement or transaction, and background context from the occurrence they discuss.

Use participants, artifact or report, object, normalized time, event family, and representative facts together. No single matching or differing field decides parent identity. Missing detail, different metrics, business units, values, or phrasing do not by themselves require separate parents.

A one-Atomic group is valid only when no other supplied Atomic belongs to its parent. Link separate parents only when the evidence supports the relation. Assign every Atomic once and output only the schema.
```

相对当前 Prompt 的实质变化只有一处：明确“单字段差异不裁决身份”，避免模型把 V2.1 card 当硬规则；其余保留已验证的 disclosure/reaction/report/agreement 边界。

## 7.3 Resolution Prompt 完整文本

```text
A proposal is one document-local candidate parent; a prototype is a provisional or existing parent. Partition them by the same bounded occurrence, disclosure, process, or identifiable continuing matter.

Decide parent identity from the combined evidence, not exact field equality. Different child facts, metrics, business units, values, or levels of detail may share a parent when they belong to the same occurrence. Missing or differing cues alone do not require separation.

Separate a market or analyst reaction, an independent report, a separate agreement or transaction, and background context from the occurrence they discuss. For market facts, distinguish materially different dates, sessions, or movement episodes unless the evidence identifies one explicit roundup or episode.

Shared issuer, source, period, family, or topic alone is insufficient. Exact artifact equality is supporting evidence but is not required. Assign every proposal exactly once, use each prototype at most once, and output only the schema.
```

这段文本恢复 V2.0 的综合语义判断，删除 V2.1 的 `Merge only when ... parent-specific bridge`，同时保留 V2.0 已暴露 bad case 所需的通用边界。

## 7.4 局部 repair 后缀

Induction：

```text
Repartition only these Atomics using the same parent definition. Review the evidence for the whole group. A differing field or event family is diagnostic, not an automatic split.
```

Resolution：

```text
Recheck only this proposed merge using the same parent definition. Keep different child facts together when they share one parent; separate only a materially different occurrence, report, agreement, reaction, or continuing matter.
```

不新增修复 Schema，不要求 reasoning。

---

## 8. 具体 Bad Case 的预期修复结果

| Bad case | V2.0 错误 | V2.1 错误 | V2.0R 预期 |
| --- | --- | --- | --- |
| Micron Q3 earnings facts | 大量异类并入 79-member 簇 | 同一 disclosure 被切 `29+2+1×6` | 正确 disclosure child facts合并；reaction/SCA/report/background 分离 |
| Micron after-hours/Thursday stock reaction | 并入 earnings | 较小 market 包仍混 annual/single-day/session | 独立 market parent；同一明确 session 可合，不同 session/长期表现分离 |
| Micron SCA terms/commitments | 并入 earnings或分为多包 | 同一 P106 因 `Micron` vs `The deals` 被切 | SCA 独立于 earnings，同一 agreement 条款跨表述合并 |
| Citi post-earnings note | V2 可能与其他 analyst coverage 混 | 同一 P52 被 role 拆成两个 singleton | 同一 report 的 rating/forecast/commentary 保持一包 |
| Mizuho vs BofA | 同 issuer 导致误并 | institution boundary有帮助 | canonical institution/report 不同则拒绝合并 |
| Tuesday global selloff vs Thursday recovery | 被合成 selloff-and-recovery | 同一 Tuesday proposal 又按 issuer 拆 | Tuesday/Thursday 分开；同一显式跨市场 episode 可跨 issuer 合并 |
| Q4 guidance | 被 earnings supercluster 吞或碎裂 | `5+1+1`，Management 被当 issuer | 同一次 Q4 guidance disclosure 聚合不同指标和表述 |
| earnings-call physical AI commentary | 宽主题可能污染 earnings | 同一 proposal 被拆 `2+1×6` | 保持同一 call/commentary parent；是否归 earnings由模型综合上下文判断 |
| Apple intraday vs close | V2 存在 reaction 混包 | V2.1 仍将 -0.56% 与 -5%/-6.12% 合并 | date+session+occurrence 综合判断，不靠 issuer 或同日自动合并 |
| 79/96-member oversized | review 移错成员且大幅降 R | final split 改成系统性过拆 | 不做大簇事后 Atomic 切割；在 proposal 形成和新 union 时阻止污染 |

这些用例必须抽象成通用 fixture，不在生产代码中出现 `Micron`、`Apple`、`Citi`、具体日期或数字。

---

## 9. 文件级实施清单

### 9.1 `src/cdecr/parent_occurrence_signals.py`

- 收紧 canonical issuer/artifact/institution 编译；
- 保留 soft recall routes、SimHash、cosine 和 task packing；
- 删除 hard-negative graph 语义；
- 将 `boundary_compatibility()` 替换为 proposal-level `proposal_merge_guard()`；
- issuer/object/metric 不参与硬拒绝；
- 保持纯函数，不访问 Registry、不 Apply。

### 9.2 `src/cdecr/parent_occurrence.py`

- 收窄 `_repartition_suspect_groups()` 触发；
- local repair 失败保留原 proposal；
- `_resolution_tasks()` 改回宽 route union，score-before-cap；
- 保留 bounded weighted packing 与 R2 ledger；
- 删除 `incompatible_pairs` payload/validator；
- 删除模型不同组写 stable hard negative；
- 新增或改写 proposal-level merge admission repair；
- R2 重新召回全部 residual/root；
- 删除 `_enforce_final_boundary_purity()` 活跃调用及最终 Atomic split；
- final 只做 coverage/ownership/schema/version 校验。

### 9.3 `src/cdecr/parent_occurrence_contracts.py`

- 不增加模型输出字段；
- 保持当前 compact DTO；
- 若仍有 V2 oversized `event_refs/includes_remaining_events` 兼容残留，删除活动引用但只在旧 checkpoint read boundary 做兼容；
- `package_family` 继续是内部 card/frozen package 字段，不恢复为模型任务。

### 9.4 Prompt

- 将两份 Prompt 替换为第 7 节全文；
- 不新增第三份 Prompt；
- bump prompt version/hash。

### 9.5 `src/cdecr/config.py`

保留少量配置：task proposal/token limit、每 route quota、总 K、两轮并发、local repair 一次。删除或停用 hard-negative、final purity、oversized review 相关配置。不要增加按 family 的阈值矩阵。

### 9.6 测试与 changelog

- 更新 `tests/cdecr/test_parent_occurrence_signals.py`；
- 更新 `test_parent_occurrence_induction.py`、`resolution.py`、`failure_semantics.py`、`performance.py`；
- 在 `changelog` 追加 V2.0R 语义回退、保留项和验证结果；
- 不触碰其他 DoxAgent 模块的未提交改动。

---

## 10. 实施顺序：语义分层、执行一次完成

### Step 0：冻结边界

1. 记录当前 commit、dirty worktree 和 Parent 相关 diff；
2. 复制 V2.0/V2.1 冻结 DB、clusters、Gold eval 的路径到实施日志；
3. 用 `git show 238e3f9:<path>` 导出只读参考，不 checkout；
4. bump V2.0R contract/prompt/candidate policy version。

### Step 1：先删除错误语义

1. 删除 final Atomic split；
2. 删除 candidate hard exclusion；
3. 删除 model-derived hard negative；
4. 删除 `incompatible_pairs` 模型 payload；
5. 删除 local repair 失败后的 deterministic singleton/bucket split。

先删后改，防止旧路径和新路径同时存在。

### Step 2：恢复并修复 V2.0 candidate/Resolution

1. 恢复宽 recall keys；
2. 接入 current SimHash；
3. score-before-cap；
4. bounded packing + R2 ledger；
5. R2 全 residual/root 重新召回；
6. 更新 coverage telemetry 真值。

### Step 3：建立唯一 proposal merge admission

1. 修可信 cue 编译；
2. 实现窄 `proposal_merge_guard()`；
3. R1/R2 新 union 后一次局部 Resolution repair；
4. 失败恢复波次前结构；
5. 确认不拆 proposal 内 Atomic。

### Step 4：Prompt、checkpoint、测试一次收口

1. 替换 Prompt；
2. checkpoint hash 纳入新版本；
3. 单元/集成/失败/幂等/性能测试；
4. frozen Package-only replay；
5. 真实 30 篇全流程和独立评估；
6. 最后更新 changelog 和验收报告。

P0/P1/P2 可用于风险说明，但执行时不分批发布；必须一次完成全部语义删除和替换，避免出现半套 V2.1 hard rule + 半套 V2.0 recall 的中间版本。

---

## 11. 测试设计

## 11.1 必须新增的通用回归用例

### 应合并

1. 同一 earnings disclosure 的 revenue、EPS、margin、capex、业务线表现和同次 guidance；
2. 同一 analyst report 的 rating、price target、forecast 和 risk commentary；
3. 同一 SCA/agreement 的数量、金额、期限和条款；
4. 同一显式 market roundup/session 的多个 instrument；
5. 同一 call 中不同 family/metric/object 的 child statements；
6. canonical issuer 缺失但文档和语义明确同父的跨文档 proposal。

### 应分开

1. disclosure 与其 market reaction；
2. disclosure 与独立 analyst report；
3. earnings 与单独 agreement/transaction；
4. 不同 canonical institutions 的独立 reports；
5. Tuesday selloff 与 Thursday recovery；
6. intraday 与 close 且证据指向不同 market occurrence；
7. 当前事件与宽泛 industry/background context。

### 机制断言

1. 同一成功 Induction proposal 永远不会在 Resolution/finalize 被拆 Atomic；
2. participant surface 不进入 trusted issuer conflict；
3. `fiscal Q3` 不进入 trusted artifact ID；
4. 模型不同组不生成下一轮 hard negative；
5. zero-neighbor proposal 获得 fallback candidates；
6. 截断发生在 exact score 后；
7. R2 cumulative coverage 不低于 R1；
8. local repair 失败保留输入 proposal/prototype；
9. provider task 失败不导致文档失败或 Atomic 全 singleton；
10. 幂等二跑 0 新模型调用、0 membership delta。

## 11.2 冻结 Package-only 验收

先分别使用正式 V2.0 和 V2.1 R3 的冻结 Atomic snapshot 做 Package-only 测试。两份都要测：

- V2.0 snapshot 用于验证不会恢复 79-member 污染簇；
- V2.1 R3 snapshot 用于验证 Recall/singleton/final split 修复；
- 不允许用离线投影代替新版模型实际 Resolution；
- 同一 snapshot 的 A/B 使用相同 Prompt/model/provider 参数。

### 质量门槛

| 指标 | 门槛 |
| --- | ---: |
| Pair Precision | ≥90% |
| Pair Recall | ≥65%，并相对 V2.1 R3 至少 +30pp |
| Pair F1 | ≥75% |
| Package singleton | ≤45% |
| Gold 可判 singleton 中应合并 | ≤25% |
| Micron earnings components | ≤4 |
| Micron earnings high-confidence reaction/SCA/report/background intruder | 0 |
| final Atomic split | 0（架构断言） |
| lineage candidate coverage | ≥95% |

Precision 从 V2.1 的 97.56% 回落到 90%-95% 是允许且有性价比的，只要 Recall/F1 大幅恢复；低于 90% 则说明 V2.0 supercluster 风险未被 proposal 级防线控制，不能发布。

### 关键 bad case 门槛

- Tuesday selloff 与 Thursday recovery 不得同包；
- 同一 Tuesday/global episode 不得只因 issuer 不同被拆；
- Citi/Wedbush 同一 report 的 target/rating/outlook 不得被 Atomic family 拆开；
- Mizuho/BofA 独立 report 不得合并；
- SCA 不得并入 earnings，但同一 SCA 条款不得碎成多个 singleton；
- Q4 guidance、earnings disclosure、earnings-call comments 不得因 `Management`/业务单元/metric 差异碎拆；
- Apple intraday/close 依据 occurrence/session 判断，不得只因同 issuer/date 自动合并。

## 11.3 真实 30 篇全流程

Package-only 通过后再跑真实 30 篇：

- 30/30 文档成功；
- relevance Enforce 且明确 irrelevant 0 条进入 Grounder；
- Mention/Atomic 指标不因 Parent 改动下降；
- 采用独立 Agent 做 package-atomic-mention 层级复核；
- 报告 singleton、可判漏合 singleton、超大簇、误并成员比例、pair P/R/F1、token、wall 和每阶段占比；
- 同时对比正式 V2.0、V2.1 R3 和 V2.0R，不用某个恢复段墙钟冒充 fresh full-run wall。

## 11.4 效能门槛

| 指标 | 门槛 |
| --- | ---: |
| Parent clean input token | ≤500k |
| Parent clean total token | ≤550k |
| repair token / Parent total | ≤15% |
| Package clean wall（30篇） | ≤15 min |
| 常规 Resolution waves | 2 |
| 单 proposal candidate edges | bounded K |
| pair evaluation | 不允许全量 P²；报告 `evaluations / P` |
| 幂等复验 | 0 新调用 |

V2.0R 保留 compact DTO 和两轮并发，质量回退不应牺牲当前性能基准。若宽召回导致 token 激增，优先优化 score/packing，不得重新用 bridge-only admission 压 Recall。

---

## 12. 风险与全局权衡

### 12.1 Precision 必然低于 V2.1 的极端高值

删除 final split 后，Precision 预计从 97%-99% 回落。只要保持 ≥90% 且 Recall/F1 显著恢复，这一交换是高性价比的。V2.1 的极高 Precision 建立在 73.95% singleton 和 18.96% Recall 上，不应作为业务目标保留。

### 12.2 V2.0 supercluster 可能复现

控制方式不是恢复全局 hard rule，而是两道前置防线：

1. 文档内 Induction 不生成混合 proposal；
2. R1/R2 只对新 proposal union 做可信边界复检。

若仍出现 supercluster，应排查具体是哪一个 proposal 污染或哪一次 union 误判，不得重新添加 final Atomic split。

### 12.3 宽候选可能增加 token

宽候选不等于大 batch。通过 per-route quota、exact score、bounded K、weighted packing 和 R2 ledger 控制 payload；不能用降低候选覆盖换 token。

### 12.4 部分真实边界没有 canonical cue

未知字段既不 MATCH 也不 CONFLICT。模型仍可依赖上下文、代表事实和语义判断。V2.0R 不新增模型字段去弥补所有未知；先修既有 cue 的类型正确性。

### 12.5 上游 Atomic 污染仍存在

例如历史 `atomic:654d...` 将多种最小事实混在一起。V2.0R 不在 Package 内拆 Atomic；这类问题必须单列为 N9 指标和后续任务，不能通过 Package 复杂化掩盖。

---

## 13. 禁止事项

实施过程中禁止：

1. Git 粗暴回滚或覆盖当前 dirty worktree；
2. 为某个 30 篇 bad case 写公司名、数字或日期特判；
3. 新增模型 reasoning/confidence/axis 字段；
4. 恢复 oversized default/exception Schema；
5. 把所有 field conflict 改成较低阈值 hard conflict；
6. 在 final Package 上拆 Atomic；
7. 把模型不同输出组永久写成 cannot-link；
8. local repair 失败后整篇重跑或全量 singleton；
9. 为提升 Recall 自动合并候选；
10. 为压 token 重新采用 bridge-only candidate admission。

---

## 14. 完成定义

以下全部满足才算 V2.0R 落地完成：

- V2.1 错误三链路（bridge-only、stable hard-negative、final Atomic split）从活动路径删除；
- V2.0 宽召回以 score-before-cap 方式恢复；
- R2 对 residual/root 真正补召回；
- proposal-level merge admission 是唯一业务防误并层；
- 成功 Induction proposal 不会被跨文档阶段拆 Atomic；
- 模型 Schema 无新增字段；
- 两份 Prompt 与最新业务逻辑逐句对齐；
- 通用 bad-case tests 全通过；
- 两份冻结 snapshot 的真实 Package-only A/B 通过；
- 真实 30 篇全流程通过质量、碎片化、反误并和效能门槛；
- 独立 Agent 审计没有发现 hidden singleton、supercluster 或数据丢失；
- changelog 完整记录代码和测试结果；
- 无论验收结果如何，不在同一轮自动执行回滚，由报告单独评估下一步。

---

## 15. 总结判断

V2.0 的问题是父边界过松，V2.1 的问题是把不完整字段提升成了全流程身份执行规则。两者都不能直接发布。

V2.0R 的关键不是在两者之间取平均阈值，而是重新划清职责：

```text
Induction 用文档证据形成干净 proposal
候选层尽量让可能同父的 proposal 相遇
模型做集合语义判断
代码只拒绝极少数可信 proposal-level 错误 union
最终层不再覆盖模型并拆 Atomic
```

这条路径在业务逻辑上回到 V2.0，在工程上保留 V2.1 的有效成果，并直接修复 V2.0 的三项已知缺陷：Induction 污染、候选截断/R2 无效、oversized review 有损切割。它不新增 Schema，不维护多套规则系统，也不以文档失败或大规模 singleton 换取表面 Precision，是当前最有全局收益且可一次落地的回退方案。
