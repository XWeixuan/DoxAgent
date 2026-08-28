# O2 Event Library 时间语义与 Reference View 判定联合修复方案

> 日期：2026-08-27  
> 状态：待实施  
> 适用范围：CDECR FINALIZED → Frozen Runtime Snapshot → O2 Revision Bundle → Validator → Importer → Atomic Publish → Published/Reference View  
> 基准：保持当前 Event/Fact、主题簇聚合、完整 Canonical Event Library、`is_important` 与 `include_in_reference_view` 的既有业务口径；本方案只修复时间语义、上下文缺失、Reference View 判定标准及其确定性约束。

## 0. 当前真实验收发现的问题

本轮 MU 真实验收已经完成从旧 CDECR R2 SQLite、只读 Frozen Runtime Snapshot、O2 编辑、Revision Bundle 校验到原子发布 V1 的完整链路。发布结果包含 185 个 Event、518 个 Fact；确定性 Validator 和 semantic report 均通过。但人工检查 Reference View 后发现：**“成功发布”只证明结构和流程有效，并不等于时间语义与 Reference View 质量合格。**

### 0.1 时间字段大量结构合法、业务不可用

- 全库 185 个 Event 中，`occurred_at` 为 YEAR 粒度的有 120 个；Reference View 的 80 个 Event 中，YEAR 粒度有 55 个。
- 许多原始新闻或证据可以追溯到具体发布日期，O2 最终却只写成 `2026`、`2027` 等宽泛年份。
- 上游 Source Message 的 `published_at` 存在，但在进入 Frozen/O2 工作区前没有作为 occurrence date candidate 完整传递。
- 当前 Frozen Runtime Atomic/Delta 的 `time` 同时承载“信息发生时间”和“预测、财报、规划所指向的期间”，导致 O2 容易把 FY2027、下一季度或未来计划日期误写为 Event 的发生时间。
- 真实结果中也存在正确的 DAY 时间，说明 O2 并非完全没有定位日期的能力；根因是契约歧义、输入缺失、Prompt 指示不足以及 Validator 没有拦截语义错误共同造成的，不能简单归因于模型“偷懒”。
- 未来日期被写入 `occurred_at`：例如“公司宣布将在未来某日发布财报”，事件应是“公司宣布”这一行为，发生时间是公告当天；未来财报日属于被陈述对象的时间，而不是公告事件的发生时间。

### 0.2 聚合 Event 与 Fact 的时间被混为一谈

- 当前业务口径允许把同一周期、同一催化剂或同一主题下，多名分析师调整评级/目标价聚合为一个主题簇 Event。
- 此类 Event 的整体时间可以是 MONTH 或 INTERVAL 等较宽粒度；不同 Fact 的具体发生日期不同，并不自动要求拆成多个 Event。
- 但主题簇内每条 Fact 都应记录分析师实际表达观点或采取行动的具体日期。现有契约没有独立的 Fact occurrence time，宽粒度 Event 下 Fact 又可能使用 `SAME`，因此具体日期在聚合过程中丢失。

### 0.3 `include_in_reference_view` 的判定目标不够清楚

- 当前 O2 Prompt 只笼统说明 Reference View 服务于 W1、D2/D3，却没有告诉一个无项目上下文的模型：完整 Event Library、Reference View、D2 Expectation Model、D3/O3 Policy 和 Runtime 分别做什么。
- `is_important` 被定义为长期重要性，`include_in_reference_view` 被定义为当前用途，方向正确；但缺少可执行的判定顺序、遗漏测试、冗余测试和正反例。
- Survey/Index/Edit 中的“宽松相关性筛选”用于决定是否进入完整 Library，容易被模型误用为 Reference View 的收录标准。
- 真实结果中只有 `is_important=true && include_in_reference_view=true` 的记录被保留，未出现 `is_important=false && include_in_reference_view=true`。这不能单独证明结果必错，但高度提示 O2 可能把 Reference View 当成了“重要事件的子集”，没有真正独立判断当前下游用途。
- 当前 Schema 对两个布尔字段几乎是同义反复式描述，没有要求给出判定依据；Validator 只能验证布尔值一致性与时间钟，不能发现“结构合法但语义判断错误”。
- `EXPIRED_30D`、`expiration_days` 等名称容易让模型把 30 天理解为业务过期，而现有代码实际上只是安排复核；“时间到了”不等于事件不再影响当前现实。
- 质量指标没有基于人工 Gold 统计关键漏召回、过时误召回和 superseded 误保留，因此即使语义质量不足也可能放行。

### 0.4 两类问题的共同根因

两类问题都不是单改一句 Prompt 可以解决：

1. 模型不知道自己在更大的 research-to-monitoring 系统中贡献什么，个人任务没有与系统目标对齐。
2. 输入契约丢失了完成判断所需的时间、反向 supersession 和 Fact 级上下文。
3. Schema 只让模型提交结果，没有要求提交可校验的判定依据。
4. Validator 与质量门只检查“能否导入”，没有检查“是否表达了正确的业务语义”。

因此必须分别修改确定性编排/代码和 Prompt/skill；两部分采用同一套冻结口径，但不得互相代替。

## 1. 冻结业务口径、目标与边界

### 1.1 时间字段的冻结定义

- `Event.occurred_at`：该动作实际发生、该信息首次公开或该观点被表达的时间。
- 新增 `Fact.fact_occurred_at`：该条 Fact 被陈述、披露、确认或形成的时间。
- `Fact.subject_time`：Fact 所讨论、预测、规划或报告的目标期间。保留其现有语义，不将其改造成 occurrence time。
- 例如分析师在 2026-08-13 表示 MU FY2027 营收可能增长：Event/Fact 的 occurrence time 是 2026-08-13，`subject_time` 是 FY2027，assertion 仍为 FORECAST。
- “宣布将在 2026-09-22 发布财报”的 occurrence time 是宣布当天；2026-09-22 只属于未来目标/计划时间。

### 1.2 主题簇 Event 的冻结聚合口径

- 继续按照当前已确定的共享催化剂、同质响应、限定时间窗口、同一信息周期等业务标准聚合 Event。
- 同一周期内多个分析师调整目标价，可以继续聚合为一个主题簇 Event。
- 主题簇 Event 的 `occurred_at` 可以采用 MONTH、INTERVAL 等较宽粒度。
- 主题簇下每条 Fact 的 `fact_occurred_at` 必须是具体 `YYYY-MM-DD`；父 Event 不是 DAY 时，Fact 禁止使用 `SAME`。
- Fact 日期不同不会机械触发 Event 拆分。只有当它们属于不同周期、主题/类型、催化剂/论点、报告或信息周期，或已超出当前主题簇边界时，才按现有业务口径拆分。

### 1.3 Reference View 的冻结业务目标

完整链路的部门级目标为：

`Document1 research → CDECR 持续发现现实事件 → O2 维护完整 Canonical Event Library → Reference View 投影当前现实 → D2 构造/刷新 Expectation Model → D3/O3 构造与维护 Direct Trading Policy → Runtime 低自由度判断`

- 完整 Canonical Event Library 是可追溯、可增量维护的现实记忆，不因某条记录当前不进 Reference View 就删除历史。
- `is_important` 判断一条 Event 是否具有持久研究重要性。
- `include_in_reference_view` 判断一条 Event 在冻结 `as_of` 时是否仍应进入“当前现实”的紧凑投影。
- 两个字段必须独立判断，四种组合都合法；不得把 Reference View 固定为 important Event 的子集。
- Reference View 不是重要新闻榜、近期新闻流或简单的最近 N 天窗口。

冻结判定句：

> 在冻结 `as_of` 时，如果省略该 Event，是否可能让 D2 的 expectation 构造，或 D3/O3 的 policy 校准与维护，对 MU 的当前现实形成实质性过时或不完整的认识？如果是，则 `include_in_reference_view=true`。

判定顺序固定为：

1. 目标相关路径：Event 是否直接涉及目标公司，或存在具体、可信、非微不足道的 `Event → exposure → target consequence` 路径。
2. 当前信息影响：它是否改变当前基线、尚未解决的问题、仍有效的前瞻承诺、最新控制性信息或尚未吸收的变化。
3. 信息状态：它是最新/进行中/未解决/仍有效，还是已完成并被吸收、被更新信息取代或已经冗余。
4. 时间状态：老事件仍可能有效，新事件也可能低信号；新旧本身不能决定收录。
5. 遗漏测试：删掉它会不会使下游当前现实明显不完整或过时。
6. 冗余测试：相同当前信息是否已被更晚、更完整或更控制性的 Event 覆盖。

### 1.4 与 CDECR Dreamer 相关性过滤的关系

CDECR 相关性 Gate 继续负责候选能否进入 Event Library：

- 直接相关：actor/object/action/outcome 直接涉及目标公司、证券或业务。
- 间接经济相关：存在具体、可信、非微不足道的路径影响目标收入、需求、定价、成本、利润率、产能、份额、竞争、资本开支、融资、监管、投资者预期或证券表现。
- 仅有 ticker 提及、同行业、背景列表、宽泛市场叙述或“宏观影响股票、股票影响 MU”的模糊长链，不足以构成相关。

该 Gate 是 Reference View 判断的第一层输入，不等于最终收录。进入完整 Library 后，还必须判断它是否仍影响冻结时点的当前现实。

### 1.5 非目标

- 不重写当前 Event/Fact 主体定义和主题簇聚合业务规则。
- 不把日期不同机械等同于 Event 不同。
- 不把 Reference View 改成时间窗口、配额或“important=true”的子集。
- 不回写或覆盖已发布 V1；修复通过新 Bundle 原子发布 V2。
- 不在本方案中扩展 D3、正式调度或生产增量 adapter。
- 不为修复语义问题重新运行 CDECR 742 Delta 全链路；优先复用已冻结 SQLite、Frozen Snapshot 和 Published V1。

# 第一部分：编排/代码层修改

## 2. Event/Fact 时间契约与版本兼容

1. 在 Canonical Fact 和 Revision Bundle Fact wire 中新增：
   - `fact_occurred_at`
   - `fact_occurrence_time_precision`
2. 保留 `subject_time` 及其现有含义。
3. 父 Event 为 DAY 时，Fact 可以写同一天或按明确 wire 规则使用 `SAME`。
4. 父 Event 为 MONTH、QUARTER、YEAR、INTERVAL 等宽粒度时，每条 Fact 必须写精确 DAY，禁止 `SAME`。
5. 旧版本保持可读；新 Bundle 使用新契约。不得通过 reinterpretation 静默改变旧字段含义。

## 3. Frozen Runtime Snapshot 与 Delta 时间输入

把当前混合语义的 `time` 拆开：

- CDECR Atomic/Delta 原有 `time` 明确映射为 `subject_time` 或原始语义字段，不再直接当作 occurrence time。
- Frozen Runtime Atomic 增加 `occurrence_date_candidates[]`。
- Source Message/Runtime Package 的 `published_at` 必须进入 Frozen Snapshot，并可追溯到 source/package ID。

候选日期优先级冻结为：

1. proposition/evidence 中明确描述的动作日期；
2. 官方 filing、release 或公告日期；
3. Runtime Package 已确认的 occurrence date；
4. source `published_at`；
5. 在 `as_of` 内进行的聚焦 Web Search；
6. 仍无法解决则进入 Pending，不允许用 YEAR 猜测掩盖缺失。

## 4. Date Resolution Ledger

新增 `output/work/date_resolution_ledger.jsonl`，至少记录：

- Delta/Atomic/Package/Source ID；
- 候选日期及其证据来源；
- 选定日期和 precision；
- 该时间承担 Event occurrence、Fact occurrence 还是 subject time；
- 解析状态：`RESOLVED`、`GENUINELY_PERIOD_WIDE`、`CONFLICTING`、`UNRESOLVED`；
- 对应 Event/Fact 临时或 Canonical ID。

Ledger 是 O2 与 Validator 共享的可追溯中间产物；不把推理文本写入 Canonical Library。

## 5. occurrence 合并与初始化/增量编排

- 保留当前 analyst response episode/theme cluster 合并逻辑，不因 Fact 日期不同自动拆 Event。
- 编排在形成宽粒度主题簇 Event 时，必须保留每个 Fact 的来源、actor、action 和具体 occurrence date。
- Event 的聚合时间可由主题簇边界或 episode start/end 形成；Fact 日期不得被父 Event 粒度覆盖。
- 增量匹配继续综合 ticker、event type、主题、当前周期、actor/institution、Fact occurrence date 和 subject period；不同日期只是一项证据，先判断是否属于既有主题簇。
- 只有不同周期、主题/类型、催化剂/论点、报告或信息周期，或超出当前边界时才拆分。

## 6. Reference View 决策契约与编排

### 6.1 保留 Canonical 布尔字段并增加可校验依据

保留 `include_in_reference_view: boolean` 兼容现有消费者，同时为每个新建或实质变化的 Event 要求决策对象：

- `event_id`
- `is_important`
- `include_in_reference_view`
- `reference_view_basis`
- `note`

建议正向 basis：

- `CURRENT_BASELINE`
- `OPEN_OR_EVOLVING_MATTER`
- `LATEST_CONTROLLING_UPDATE`
- `STILL_EFFECTIVE_FORWARD_ITEM`
- `RECENT_UNABSORBED_UPDATE`

建议负向 basis：

- `SUPERSEDED`
- `COMPLETED_AND_ABSORBED`
- `SUBJECT_HORIZON_PASSED`
- `ROUTINE_OR_REDUNDANT`
- `NO_CURRENT_DOWNSTREAM_UTILITY`

新增 `output/work/reference_view_decision_ledger.jsonl` 保存初始化与增量过程中的最终判定依据。

### 6.2 补全 Reference Review Candidate

Candidate 除现有字段外，应提供判定所需的最小上下文：

- event type、summary、关键 Facts；
- Event occurrence 和各 Fact occurrence；
- subject horizon；
- `supersedes_event_ids` 与反向 `superseded_by_event_ids`；
- 当前 `is_important`、Reference flag 和上次 basis；
- review reason 与冻结 `as_of`。

当新 Event supersede 旧 Event 时，应同时把新旧双方加入 Review，并实际使用 `SUPERSEDED_TARGET` 原因。

### 6.3 区分“到期复核”与“语义过期”

- 将 `EXPIRED_30D` 改为 `AGE_REVIEW_DUE_30D`。
- 将 `expiration_days` 改为 `explicit_review_age_days`。
- 达到 review age 只表示需要重新判断，不预设结果为排除。
- 主题簇的 review anchor 使用 episode end 或最大 Fact occurrence date；不得使用 FY2027 等 subject horizon 作为 occurrence review clock。
- subject horizon 可以参与“事项是否仍有效”的语义判断，但不能替代事件发生时间。

## 7. Validator、Semantic Gate 与质量指标

### 7.1 时间硬错误

Validator/semantic gate 新增并稳定输出：

- `FACT_OCCURRENCE_MISSING`
- `FACT_OCCURRENCE_NOT_DAY`
- `FACT_SAME_WITH_BROAD_EVENT`
- `FUTURE_OCCURRENCE_AFTER_AS_OF`
- `FACT_OCCURRENCE_AFTER_AS_OF`
- `SUBJECT_PERIOD_USED_AS_OCCURRENCE`
- `SCHEDULED_TARGET_USED_AS_OCCURRENCE`
- `TRACEABLE_DAY_DOWNGRADED`
- `DATE_SPECIFIC_EVENT_UNRESOLVED`

执行规则：

- 存在可追溯具体日期的单一 occurrence Event 必须为 DAY。
- `occurred_at` 和 `fact_occurred_at` 不得晚于冻结 `as_of`。
- 未来计划/预测/报告期间只能进入 subject time。
- 明确需要具体日期但无法解决时必须 Pending，不能用 YEAR 逃逸。
- 真正覆盖一段时间的 Event 可以保持宽粒度，但须在 Ledger 标为 `GENUINELY_PERIOD_WIDE`。
- 宽粒度主题簇 Event 下每条 Fact 必须为 DAY。

### 7.2 Reference 判定一致性与语义质量

- `reference_view_basis` 必须与布尔值同向，且 Event flag、决策 Ledger 与 Bundle 一致。
- `is_important` 与 `include_in_reference_view` 不得以相等关系或蕴含关系作为 Validator 规则。
- semantic gate 检查 superseded 旧 Event、最新控制性 Event、仍有效事项和明显冗余项的成对一致性。
- 质量报告增加人工 Gold 指标：
  - current-state critical recall；
  - exclusion precision；
  - superseded false-positive rate；
  - current-baseline false-negative rate；
  - 四象限覆盖仅作诊断，不设配额；
  - Reference View token ratio；
  - D2 current-state coverage；
  - D3/O3 maintenance change recall。
- 本次 MU 结果没有 `important=false/reference=true` 是诊断信号，不应被当成自动失败或强制配额；是否错误必须由 Gold 和具体语义验证。

## 8. Published 与 Reference View 展示

Reference View 的人类可读格式要同时展示 occurrence 与 subject：

```text
Event time: 2026-08 [MONTH]
Fact occurred_at: 2026-08-13
Fact subject_time: FY2027
```

- 不再把 `2026-08 [MONTH]` 误读为所有 Fact 同日或无法确定日期。
- 不再把 FY2027 渲染为 Event/Fact 的发生时间。
- 可选展示 `reference_view_basis` 的短标签，方便人工验收；Canonical 消费者仍读取稳定布尔字段。

## 9. 迁移、恢复与无模型测试

### 9.1 迁移与恢复

- Published V1 保持不可变。
- 以 V1 为 base 生成受影响范围的 repair Bundle，原子发布 V2。
- 复用旧 CDECR SQLite、Frozen Snapshot、Published V1 和可恢复的 O2 同 thread/checkpoint；不从 742 Delta 起点重跑。
- 只修复受时间或 Reference 判定影响的 Event/Fact。
- 明确 DAY 的旧 Fact 可按可证明规则迁移；宽粒度 Event 下的 Fact 必须重新解析，不做盲目 `SAME` 推导。
- 未解决日期或语义冲突保留为 Pending。
- W1/D2 在 V2 验收通过前继续 pin V1。

### 9.2 无模型回归

至少覆盖：

- source `published_at` 与 explicit action date 的候选优先级；
- “2026-08-13 发表 FY2027 预测”的 occurrence/subject 分离；
- 未来 scheduled date 不进入 occurrence；
- 回顾性报道中的明确动作日期；
- 主题簇 Event 保持聚合、每条 Fact 保持精确日期；
- 不允许 YEAR 逃逸和 broad Event 下 Fact `SAME`；
- 旧版本读取与新版本写入兼容；
- Reference basis/boolean 一致性；
- review age 不自动排除；
- supersession 双向候选；
- 原子性、stale base、幂等重放和 Pending 局部隔离。

# 第二部分：Prompt/skill 层修改

> 本部分只约束 O2 的项目理解、判断标准和编辑行为，不承担底层日期补全、Schema 兼容、调度或硬校验职责。对应的 Schema、编排和 Validator 改动全部保留在第一部分，避免把 Prompt 补强误当成确定性保障。

## 10. 当前 Prompt/skill/schema 对 Reference View 的全部相关指示审计

当前 Prompt 已经告诉 O2“Reference View 与重要性不同、要看当前是否有用”，但没有把“为什么有用、对谁有用、如何判断时间相关性”解释成可执行标准。因此方向基本正确，执行定义明显不足。

真实验收结果也显示：O2 实际上把 Reference View 近似理解成了“更严格的重要事件子集”，而不是“供 D2/D3 使用的当前现实视图”。

### 10.1 O2 角色 Prompt

当前 [o2.md](../../prompts/codex_v2/event_library/agents/o2.md) 只说明：

- O2 位于 CDECR Runtime 与 Event Library publisher 之间；
- 维护 W1、D2/D3 所需的 flags；
- 完整库应广覆盖，压缩通过 flags 完成。

问题是，它没有解释：

- D2 究竟在建立什么；
- D3/O3 如何使用 Reference View；
- Reference View 是“重要新闻榜”“近期事件流”，还是“当前现实状态的紧凑投影”；
- 漏掉和误放事件分别会给下游造成什么损害。

相比之下，新 O3 Prompt 已经完整解释了 research-to-monitoring 总目标和部门分工，明显比当前 O2 Prompt 更容易与系统目标对齐。

### 10.2 Foundation Skill

当前 [foundation.md](../../prompts/codex_v2/event_library/skills/foundation.md) 的核心定义是：

- `is_important`：长期预期、价格分析或长期理解的重要性；
- `include_in_reference_view`：当前对 D2/D3 是否有用；
- 近期、演进中、仍影响未来预期的事件通常保留；
- 陈旧、完成、被替代的例行事件可以退出；
- 两个字段独立。

这是正确方向，但缺少可执行定义：

- “近期”没有参照 Frozen `as_of` 的判断方法；
- “仍影响未来预期”没有经济传导测试；
- “完成”不等于失效，例如已经完成的管理层更换仍定义当前管理层；
- “陈旧”与“长期有效的现实基线”没有区分；
- 没有说明旧但仍有效、近期但低价值、重要但已被吸收等边界案例；
- 没有强制 O2 比较更新 Event 和被替代 Event。

Foundation 还包含完整库的宽松相关性规则。这容易让模型混淆三个不同问题：

1. 是否进入完整 Canonical Library；
2. 是否重要；
3. 是否进入当前 Reference View。

### 10.3 初始化阶段 Skills

- [initialize-wave.md](../../prompts/codex_v2/event_library/skills/initialize-wave.md) 要求先做一个初始判断，但没有判定流程。
- [initialize-reconcile.md](../../prompts/codex_v2/event_library/skills/initialize-reconcile.md) 要求独立重判，但仍只写“present usefulness to D2/D3”。
- 同一 Reconcile skill 的异常检查只防止“全部 false”，并不检查具体选择质量。

因此，模型知道必须填值，却不知道如何系统性地填。

### 10.4 增量与 Reference Review Skills

[incremental-reference-review.md](../../prompts/codex_v2/event_library/skills/incremental-reference-review.md) 只列出：

- recency；
- evolving matter；
- supersession；
- still shapes forward expectations。

但没有决策顺序、正反例或“遗漏测试”。

[incremental-edit.md](../../prompts/codex_v2/event_library/skills/incremental-edit.md) 要求先填初始 flag，再由 Reference Review 终审。这会放大锚定效应：Reference Review 没有更强规则时，容易继承初始判断。

[revision-bundle.md](../../prompts/codex_v2/event_library/skills/revision-bundle.md) 又要求只修 Validator 报错，并保持其他判断稳定。因此，只要 Reference flag 在 Schema 上合法，即使语义错误，repair 也不会主动纠正。

### 10.5 Schema 说明

当前 Event Schema 只有：

```text
is_important:
  Durable decision relevance

include_in_reference_view:
  Current usefulness to D2/D3
```

Reference Review Schema 也只有“Model-selected current Reference-view usefulness”。主要缺陷是：

- boolean 没有选择依据；
- 没有 reason code；
- 初始化阶段不需要留下判断理由；
- 无法验证模型是否把 importance 复制给 reference；
- Schema 没解释 D2/D3 的职责；
- Review Candidate 没有 `event_type`、反向 `superseded_by_event_ids` 等关键判断信息；
- 示例几乎都是 `include=true` 和“Still useful”，没有成组正反例。

本小节只记录 Schema 如何影响 Agent 理解；实际 Schema 和编排修改严格留在第一部分第 6 节。

### 10.6 确定性时钟造成的潜在误导

当前 10/30/7 天复审时钟使用：

- `expiration_days`
- `EXPIRED_30D`
- `INCLUDED_RECHECK_7D`

代码不会在 30 天时自动删除 Event，但“EXPIRED”很容易让模型误以为：

> 超过 30 天通常就应退出 Reference View。

这与真实业务不一致。30 天只是“必须重新做语义判断”的时间，不应是语义有效期。

而且 `SUPERSEDED_TARGET` 虽然存在于 enum，目前没有形成“新 Event 指向旧 Event 时，旧 target Event 必须立即进入复审”的完整保证。

### 10.7 Validator 和质量指标

当前 Validator 只检查：

- flag 与 review decision 是否一致；
- review clock 字段是否正确；
- Event 是否存在。

它不判断选择是否有业务意义。

质量指标同样偏弱：

- 是否至少有一个 Reference Event；
- important Event 被 Reference 收录的比例；
- Reference/full payload 比例。

没有 Gold 意义上的漏召回和误召回校验。对应确定性和指标修改严格留在第一部分第 7 节。

### 10.8 真实验收反映出的偏差

真实验收使用的 10 个 Prompt/skill 文件与验收时工作树 SHA-256 全部一致，所以不是“测试用了旧 Prompt”。

本轮 185 个 Event 的结果是：

| `is_important` | `include_in_reference_view` | 数量 |
|---|---:|---:|
| false | false | 50 |
| true | false | 55 |
| true | true | 80 |
| false | true | 0 |

这不能单独证明 80 条中的每一条都错，但它是很强的行为证据：

- Prompt 明明说“近期但不重要的 Event 也可以进入 Reference View”；
- 实际却一个 `important=false/reference=true` 都没有；
- O2 很可能把 Reference View 理解成了 importance 的严格子集。

也就是说，当前“两个字段独立”的一句话，没有转化成模型真正理解的决策体系。

### 10.9 CDECR 相关性 Prompt 的启示及适用边界

严格来说，这不是 Dreamer 主 Prompt，而是 Dreamer 高召回抽取之后的独立 Relevance Gate：

```text
Dreamer 候选抽取
→ Candidate 级 Relevance Gate
→ Grounder/Judge
```

当前 [relevance_filter.md](../../src/cdecr/prompts/v1/relevance_filter.md) 把相关性写得很清楚：

- 每条 candidate 独立判断，不能按整篇文章判断；
- Direct relevance：直接涉及目标公司、证券、业务、产品、客户、财务、运营等；
- Indirect economic relevance：必须存在具体、合理、非微不足道的传导路径；
- 使用最短链条：

```text
事件
→ 目标 ticker 的具体经济暴露
→ 收入、需求、价格、成本、利润率、份额、预期或证券影响
```

- 共享行业、名单出现、泛市场影响、模糊的“经济→股票→MU”不够；
- 问投资者是否会针对这个 ticker 更新判断，而不是只更新行业判断。

这套定义不能直接复制给 Reference View，因为两个门解决不同问题：

```text
CDECR relevance
= 这条候选是否值得进入 ticker 事件处理链

Canonical Library admission
= 是否是有效且与 ticker 有关的 occurrence

is_important
= 是否具有长期研究或价格分析价值

include_in_reference_view
= 此刻是否仍值得占用 D2/D3 的有限现实上下文
```

但 CDECR 的“具体传导路径”和“逐项判断”适合作为 Reference 判断的第一层，不能代替后续的当前信息状态、遗漏和冗余判断。

## 11. O2 Agent Prompt：补齐系统任务与角色对齐

在 `agents/o2.md` 开头增加稳定的“System Mission / Department OKR”上下文，但不改变 O2 的现有所有权：

- 说明 Document1、CDECR、O2 Event Library、Reference View、D2、D3/O3、Runtime 的顺序和分工。
- 说明 O2 的部门贡献是同时维护：
  1. 完整、可追溯、可增量维护的现实记忆；
  2. 对下游当前判断足够、不过载的 Reference View。
- 明确 O2 不替 D2 建 expectation、不替 D3/O3 写 policy，也不因当前 Reference 排除而删除历史。
- 明确两类不对称错误：漏掉仍改变当前现实的 Event，会让下游过时或不完整；保留已吸收、过时或冗余 Event，会挤占上下文并偏置判断。

在 Agent Prompt 中保留完整链路的直接表述：

```text
D1 研究现实
→ CDECR 持续发现事件
→ O2 建立完整 Canonical 现实记忆
→ Reference View 提供紧凑当前状态
→ D2 建立 Expectation Model
→ O3 维护 Monitoring Policy
→ Runtime 处理新消息
```

并明确：D2 使用 Reference View 建立或刷新 expectation、现实基线和未来修订空间；D3/O3 使用它判断 Policy 的当前起点、已满足条件、仍待发生边界以及是否需要推进、校准或退役。

目标是让一个只读到 O2 Prompt/skills/schema、完全不了解项目的模型，也能说明“我维护什么、服务谁、为什么这两个布尔值不同”。

## 12. Foundation：时间语义执行标准

保留现有 Foundation 结构，仅补入以下冻结规则和最小正反例：

1. occurrence time 是动作、披露、确认或观点表达发生的时间。
2. subject time 是预测、财报、规划或安排指向的期间。
3. 分析师在某日预测 FY2027：某日是 Event/Fact occurrence，FY2027 是 subject。
4. 某日宣布未来财报日：某日是 occurrence，未来日期是 scheduled subject。
5. 单一可追溯 Event 应使用 DAY；无法解决则 Pending，不得用 YEAR 掩盖。
6. 主题簇 Event 可宽粒度，但每条 Fact 必须有具体 DAY。
7. Fact 日期不同不自动拆 Event，继续使用当前主题簇业务标准。

Foundation 中必须保留以下原始解释，不用更短的概括替代：

> Event time answers “这个事件或主题簇发生在什么时候”。  
> Fact occurrence time answers “这条具体陈述、评级、预测或披露是在什么时候产生的”。  
> Subject time answers “这条 Fact 谈论的是哪个期间”。

加入正确和错误示例：

```text
错误：
分析师在 2026-08-13 预测 MU FY2027 营收增长
Event occurred_at = 2027

正确：
Event occurred_at = 2026-08-13
Fact fact_occurred_at = SAME
Fact subject_time = FY2027
```

主题簇示例：

```text
Event occurred_at = 2026-08
Fact A fact_occurred_at = 2026-08-05
Fact B fact_occurred_at = 2026-08-13
Fact C fact_occurred_at = 2026-08-19
```

禁止为了减少 Event 数量而丢失 Fact 日期，但允许按照既定业务规则聚合主题簇。

## 13. Foundation：Reference View 执行标准

在不改动现有字段定义的前提下，补入：

- 完整 Library、`is_important`、`include_in_reference_view` 三者的区别。
- 本方案第 1.3 节的冻结判定句和六步判定顺序。
- CDECR direct/indirect relevance path 只决定是否可能相关，不直接决定当前 Reference 收录。
- recency 既不是充分条件，也不是必要条件；temporal aging 作用于信息状态，而不是原始日历天数。
- 四象限均合法：
  - important=true/reference=true：最新财报或指引、未解决监管事项、仍有效重大合同；
  - important=true/reference=false：历史上重大但已被当前信息完全吸收或取代；
  - important=false/reference=true：临近投资者日、近期仍未吸收的限定分析师主题簇；
  - important=false/reference=false：已过时的例行目标价或价格快照。

加入成对反例，避免模型只记抽象定义：

- 老但仍有效的产能承诺应保留；新但例行、冗余的目标价可排除。
- 老的未解决诉讼应保留；已被更新财报取代的旧业绩不保留。
- 未来财报日的公告在实际财报前可保留；实际财报发布后，旧日程通常被 supersede。

Foundation 中必须保留完整的判定问句，不用“current usefulness”代替：

> 在 Frozen `as_of` 时点，如果遗漏该 Event 可能使 D2 对当前 expectation、现实基线或未来修订空间形成实质不完整的理解，或者使 O3 对 Policy 的当前起点、已满足条件或仍待发生边界产生过时判断，则 `include_in_reference_view=true`。

判定顺序必须完整写明：

1. **目标相关性**
   - 是否存在目标 ticker 的直接关系或具体间接经济传导？
2. **当前信息作用**
   - 它是否建立、改变或约束当前现实状态？
3. **信息状态**
   - 是最新有效信息、仍在演进、仍未解决、仍在生效，还是已经被更新吸收？
4. **时间状态**
   - 事件虽然较旧，其影响是否仍在持续？
   - 事件虽然很新，是否只是低信号例行噪声？
5. **遗漏测试**
   - 删除它后，下游是否可能误解“现在现实已经走到哪里”？
6. **冗余测试**
   - 更新 Event 是否已经完整表达相同现实状态？

并保留成组时间反例：

- 90 天前但仍有效的产能承诺：保留；
- 5 天前但无新 thesis 的例行目标价：可排除；
- 旧业绩被新季度结果替代：排除；
- 旧诉讼启动但案件仍未裁决：保留；
- 未来财报日期在发生前保留，财报发布后由实际业绩 Event 替代。

## 14. 各阶段 skill 的局部修改

### 14.1 Initialize Survey

- 读取/建立 Date Resolution Ledger，将 occurrence candidates 与 subject periods 分开。
- 识别当前业务口径下的主题簇候选，并为每条 Fact 建立日期映射。
- 建立 Reference 判定所需的 current-state/supersession 初始映射。
- 对日期冲突或无法确认项标 Pending，不猜测。
- 明确完整 Library admission 的宽松相关性判断不等于 Reference View 收录。

### 14.2 Initialize Wave

每次编辑前显式检查：

- Fact 是何时被陈述、披露、确认或形成；
- 它讨论的 subject period 是什么；
- 父 Event 是单一 occurrence 还是主题簇；
- 父 Event 较宽时 Fact 是否已写具体 DAY；
- Fact 日期不同是否仍属于同一当前主题簇，而不是机械拆分；
- 当前 Reference 初判基于什么 current-state effect，而不是是否“看起来重要”。

禁止为了减少 Event 数量而丢失 Fact 日期，但允许按照既定业务规则聚合主题簇。

### 14.3 Initialize Reconcile

全局收敛时增加两组检查。

时间组：

- subject period 泄漏到 occurrence；
- 未来 occurrence；
- scheduled target 被当成 occurrence；
- 宽 Event 下 Fact 使用 `SAME` 或丢失具体日期；
- 同一主题簇是否因日期被错误拆分；
- 不同周期/主题是否被过度合并。

Reference 组：

- 每个新建或实质修改 Event 都写 Reference decision ledger；
- 执行 omission test 与 redundancy test；
- 查找 old-but-active 漏召回、recent-but-routine 误召回和 superseded 误保留；
- 独立复核 `is_important` 与 Reference flag，不从一个字段推导另一个。
- “全部 false/全部 true”只是异常信号，不能代替逐 Event 的选择质量审查。

### 14.4 Incremental Index/Detail/Edit

- Known Index 只用于发现可能匹配，Detail 提供完整 Fact、时间与 supersession 上下文后再决定。
- 匹配同时考虑 ticker、type、theme、cycle、actor、Fact occurrence 和 subject period。
- 不因不同日期自动建新 Event，先判断是否属于既有 episode/theme cluster。
- 新 Event 若替代旧 Event，必须将新旧双方送入 Reference Review。
- 先修正 occurrence，再用正确 review anchor 判断当前信息状态。
- 新建/修改 Event 的初始 flag 必须给出 basis，但最终结果仍由独立 Reference Review 决定，避免锚定初判。

### 14.5 Incremental Reference Review

明确保留以下直接表述：

> `PERIODIC_10D`、`AGE_REVIEW_DUE_30D` 只说明为什么今天复审，不是保留或排除依据。

- review reason 只是“为什么现在复核”，不是预设语义结果。
- 30 天只触发重新判断，不意味着自动过期。
- 按六步顺序输出 boolean、basis 和简短 note。
- 必须检查反向 supersession、最新控制性 Event、仍有效事项和信息是否已被吸收。
- 对每个候选执行 omission/redundancy test，禁止以 recent/important 单字段替代判断。
- 在实际业绩等控制性 Event 到来后，复核并适当排除已被替代的日程/预期 Event。
- 对 review age 到期但仍定义当前现实的 Event 继续保留。

### 14.6 Revision Bundle Repair

- 除确定性 wire 错误外，允许在明确指向的 repair scope 内修复 occurrence/subject 混淆和 Reference 语义错误。
- Repair 不得借机重写未命中的 Event，也不得改变当前主题簇业务口径。
- 修复后同步更新 Date Resolution Ledger、Reference decision ledger 与 Bundle。

## 15. 无上下文模型可理解性检查

每次修改 O2 Prompt/skill/schema 后，用一个没有项目历史、只看到以下材料的模型做盲测：

- `agents/o2.md`
- Foundation
- 当前阶段 skill
- 当前输入/输出 schema

模型必须能够用自己的话正确回答：

1. 它身处哪个完整业务链路，O2 对下游贡献什么。
2. 完整 Event Library、importance 与 Reference View 分别是什么。
3. 为什么老事件可能保留、新事件可能排除。
4. 分析师何时说话与其预测 FY2027 的时间为何不同。
5. 为什么多名分析师主题簇 Event 可以宽时间，但每条 Fact 必须具体到日。
6. 为什么 Fact 日期不同不自动拆 Event。
7. 遇到日期冲突、无法解决或 semantic ambiguity 时应如何 Pending，而不是猜测。
8. 对 10 组成对案例作判断并给出 `reference_view_basis`。

若模型不能稳定回答，应优先补充其缺失的系统上下文或执行标准，不进行无关的全局重写。

如果模型仍需要追问“D2/D3 是什么”“Reference View 到底是近期新闻还是重要事件”，就说明 Prompt 仍未完成部门目标对齐。

如果模型为了日期准确拆散现有主题簇，或者为了保持主题簇而把 Fact 日期降级成年份，也说明 Prompt 尚未正确表达冻结业务口径。

核心优化方向不是增加更多“近期、重要、演进中”这类形容词，而是让 O2 明确理解：

> 它维护的不是“值得看的新闻列表”，而是“让下游在有限上下文中正确理解当前现实已经走到哪里”的状态投影。Event 是否聚合由现有业务口径决定；时间准确性在 Fact 层逐条保留。

## 16. 定向真实模型验收

### 16.1 测试方式

- 复用本次 MU R2 SQLite、Frozen Snapshot、Published V1 和当前 O2 thread/checkpoint（如仍可恢复）。
- 只对受时间或 Reference 判定影响的 Event/Fact 生成 repair worklist。
- 不重新运行 CDECR，不从 742 Delta 开始，不重新消费全部模型 token。
- O2 输出 repair Revision Bundle，经 Validator、Importer 后原子发布 V2。
- 对 V2 做一次无模型幂等重放，确认不产生 V3。

时间重点覆盖：

- 多家分析师目标价与评级调整主题簇；
- FY2027/FY2028 预测；
- 未来财报发布日期；
- 财报发布与后续指引；
- 已经正确使用 DAY 的记录作为正向对照。

Reference 重点覆盖：

- old-but-active 与 recent-but-routine；
- superseded 旧状态与 latest controlling update；
- 已完成但仍定义当前基线，与已完成且已被吸收；
- 日程 Event 在目标发生前后；
- 仍未解决事项与已关闭事项；
- important/reference 四种组合。

### 16.2 时间验收标准

- 可追溯到具体日期的单一 occurrence Event：DAY 覆盖率 100%。
- 主题簇 Event 保持现有聚合口径，可使用宽粒度。
- 可追溯 Fact 的 `fact_occurred_at`：DAY 覆盖率 100%。
- 宽粒度 Event 下 `SAME` Fact 为 0。
- occurrence 晚于 `as_of` 为 0。
- subject period 泄漏到 occurrence 为 0。
- future scheduled target 被写作 occurrence 为 0。
- 无法解决的明确日期问题全部 Pending，不以 YEAR 逃逸。
- 聚合未因日期规则产生无业务依据的 Event 爆炸。

### 16.3 Reference View 验收标准

建立分层 MU Gold，覆盖 Event 年龄、类型、直接/间接相关、未解决/已完成/被取代以及四种 importance/reference 组合，至少报告：

- current-state critical recall；
- exclusion precision；
- superseded false-positive rate；
- current-baseline false-negative rate；
- old-but-active 与 recent-but-routine 成对正确率；
- D2 expectation current-state coverage；
- D3/O3 maintenance change recall；
- Reference View token ratio。

四象限用于发现模型是否把两个字段错误绑定，不设置人为配额。最终是否通过由逐条 Gold 与下游覆盖质量决定。

## 17. 实施顺序

1. 冻结并版本化 Event/Fact 时间字段、Reference decision 和 Ledger schema。
2. 修改 Frozen Snapshot/Delta Compiler，补齐 published_at、date candidates、Fact occurrence 与 supersession 上下文。
3. 修改 Reference Review Candidate、review reason、anchor 与新旧 Event 双向入队。
4. 修改 Validator、semantic gate、Published renderer 和质量指标。
5. 完成所有无模型兼容、聚合、日期、Reference、原子性与幂等测试。
6. 局部修改 O2 Agent Prompt、Foundation 和各阶段 skills；不进行无关全局重写。
7. 完成无上下文模型理解测试和反事实配对测试。
8. 复用本次真实验收产物做定向 V1→V2 repair acceptance。
9. V2 通过后再允许 W1/D2 切换 Published pointer；否则继续 pin V1。

## 18. 完成定义

本方案只有在以下条件全部满足时才算完成：

- 时间 occurrence 与 subject 语义在契约、输入、Prompt、Validator 和展示中一致。
- 当前主题簇业务口径保持不变，Fact 精确日期不丢失，也不造成日期驱动的 Event 过度拆分。
- 一个无项目上下文的 O2 能解释自己的系统位置、完整 Library 与 Reference View 的不同目标。
- `is_important` 与 `include_in_reference_view` 能独立判定，并有 basis/ledger 可审计。
- Reference Review 按当前信息状态而不是原始年龄工作，supersession 新旧双方都能被复核。
- MU Gold 能量化漏召回、误召回和下游覆盖，不再以“Validator PASS”代替语义验收。
- 定向真实模型修复通过 Validator、Importer、原子发布与幂等重放；Published V1 始终保持不可变。
