# O2 Event Library Maintainer Agent 开发方案

> 日期：2026-08-24  
> 状态：设计基线，可进入契约冻结与原型实施  
> 对象：由 Codex SDK 启动的 ticker 级事件库维护 Agent；首版默认模型配置为 GPT-5.6 Luna（`reasoning.effort=max`），但模型与 effort 不是业务契约  
> 配套方案：`CDECR Canonical Event Library 增量维护与 Agent 接入方案`  
> 本文范围：O2 的事件语义、任务输入输出、初始化与日增量工作流、Agent 工作方式、编排、校验、恢复和验收  

## 1. 结论

O2 不应把 CDECR 的 Package 改名后直接发布，也不应输出字段级或动作级 Change Plan。O2 的正确职责是：把 CDECR 的 `Package → Atomic` 冻结结果重新解释为 `Event Occurrence → Fact`，并为本次受影响的事件产出完整目标版本。

正式处理链路为：

```text
CDECR Frozen View
        ↓
O2 事件边界重建与 Canonical 编辑
        ↓
Canonical Revision Bundle
        ↓
确定性校验
        ↓
直接导入 Working Revision
        ↓
原子切换 Published Revision
        ↓
Known Event Index / Reference Event View
```

关键设计决定如下：

1. `Canonical Event` 默认等于一个 `Event Occurrence`，即在一个可识别时间锚点上发生的一次行动、披露、决定、结果、里程碑或市场事件；也可以是在同一明确催化剂和有限信息消化窗口内形成的有界同类响应 episode。
2. `Canonical Fact` 是描述该 occurrence 的最小独立事实。一个 occurrence 可以包含多个 Fact；CDECR 的一个 Package 可以拆成多个 occurrence，不同 Package 中的 Atomic 也可以合入同一 occurrence。
3. O2 以文件工作区作为编辑界面。它可以用局部 patch 修改几个字，不需要把文字修改翻译成复杂数据库操作。
4. O2 最终交付 `Canonical Revision Bundle`，其中只包含新增或受影响 Event 的完整目标 schema、事件退役关系以及未进入 Event 的 Delta 处置。Repository 直接导入，不再先生成 Change Plan。
5. “直接导入”不等于让 Agent 直接操作 SQLite。O2 只写受 schema 约束的文件；确定性程序负责 ID 分配、事务、版本校验和发布。
6. 初始建库允许分波次起草，但所有波次必须在同一冻结输入上完成全局交叉核对，并最终一次性形成 V1。
7. 日增量先让 O2 读取完整、紧凑的 Known Event Index；只有候选或不确定事件才展开 Event Detail。完整索引保证候选召回不依赖 Top-K，Event Detail 负责恢复精确语义。
8. Canonical Library 是唯一事实源。Known Event Index 和面向计划中 D2/D3 消费路径的 Reference Event View 都是从 Published Revision 确定性编译的只读视图。
9. O2 维护 `canonical_summary`、`known_event_summary`、`is_important` 和 `include_in_reference_view`；价格分析字段在 O2 阶段保持空值，由未来独立 Agent 填充。
10. O2 不维护 Source 数量或 Source 列表。CDECR 编译后的 proposition 是 O2 的权威事实输入；只有 proposition 自身冲突、与既有 Canonical Event 明显矛盾，或无法据此判断 occurrence 边界时，才临时使用 Web Search，且检索结果不转存为 Canonical Source 字段。

## 2. 与配套方案的边界

本方案与 CDECR/Canonical Event Library 方案相互配合，但不相互包含。

| 能力 | 配套方案负责 | O2 负责 |
| --- | --- | --- |
| 新闻采集、CDECR Mention/Atomic/Package 生成 | 是 | 否 |
| Runtime Registry、FINALIZED epoch、60 天 Runtime eligibility | 是 | 否 |
| Runtime Atomic 增量识别与 Pending Delta 编译 | 是 | 否 |
| Frozen View 的冻结、版本号与工作区物化 | 是 | 只读 |
| Package/Atomic 到 Occurrence/Fact 的语义重建 | 否 | 是 |
| 事件文字、事实表述、摘要和重要性编辑 | 否 | 是 |
| Canonical Revision Bundle 生成 | 接收 | 是 |
| Bundle schema/coverage/base version 校验 | 是，O2 可调用校验器预检 | 配合修正 |
| SQLite copy-on-write、Working/Published、事务发布 | 是 | 无数据库写权限 |
| Known Event Index 与 Reference View 编译 | 是 | 维护其所需语义字段 |
| W1 新旧判定 | 否 | 否；O2 只为 W1 提供事件库 |
| 事件价格面/alpha 分析 | 否或未来独立模块 | 否，字段留空 |

为了采用本文方案，配套方案的接口层需要同步四项口径：

1. Canonical 命名由 `Package/Atomic` 调整为 `Event Occurrence/Fact`；Runtime 侧仍保留 Package/Atomic 名称。
2. O2 Frozen View 删除 `sources` 字段。
3. O2 输出由 `Change Plan` 改为 `Canonical Revision Bundle`。
4. Canonical Event 增加双摘要、重要性、Reference View 选择字段以及预留的空价格分析字段。

本文不规定 CDECR 内部表、Canonical SQLite 最终 DDL、上游消息交付 API 或 DoxAgent 其他工作流的实现。

## 3. O2 的角色与任务目标

### 3.1 角色

O2 是一个 ticker 级 Canonical Event Library editor，而不是摘要器、新闻聚类器或数据库迁移器。

它承担三项核心工作：

1. **重建事件边界**：从主题性 Package 和原子 Atomic 中辨认真实 occurrence。
2. **编辑 Canonical 内容**：形成稳定、清晰、无重复的 Event 与 Fact，维护双摘要和下游视图标签。
3. **增量对账**：把新 Atomic 判定为既有 occurrence 的重复事实、新补充事实、一个相关但新的 occurrence，或暂不发布内容。

### 3.2 优先级

O2 的决策优先级固定为：

1. 事实正确与事件边界正确；
2. 有效 Delta 不遗漏；
3. 新旧/归属判定可供 W1 使用；
4. 表述简洁、稳定、容易被低参数模型理解；
5. D2/D3 参考视图足够紧凑；
6. 尽量减少不必要的旧对象重写。

压缩不能通过删除有效 occurrence 或丢失有区分力的 Fact 实现。压缩主要来自去除 Mention/Evidence/Source/audit、合并真正重复的 Fact、双摘要编译，以及 Reference View 过滤。

## 4. Canonical 语义模型

### 4.1 Event Occurrence 定义

`Event Occurrence` 是在一个可识别的发生或信息释放锚点上，由特定主体完成或经历的一次行动、披露、决定、结果、里程碑、外部冲击或市场事件。对于单项价值较低、但共同响应同一明确催化剂的同类外部行动，Occurrence 也可以表示一个时间边界明确的响应 episode，而不要求每个行动都独立成 Event。

判断一个 occurrence 至少使用以下四个要素：

- **发生锚点**：何时发生、宣布、披露、提交、裁定、发布或被观察到；
- **主体与动作**：谁做了什么，或谁发生了什么；
- **对象与阶段**：针对哪项业绩、产品、交易、诉讼、监管事项或市场阶段；
- **信息释放场景**：同一财报、同一公告、同一会议发言、同一裁决、同一交易日市场 episode，或同一催化剂后的有界 analyst response episode。

同一 occurrence 可以含有多个对象期。例如，2026-06-24 的 Micron FY2026 Q3 earnings release 可以同时包含 FY2026 Q3 已实现业绩、FY2026 Q4 guidance、供需判断和当日披露的战略客户协议；它们的 `subject_time` 不同，但信息释放 occurrence 相同。

对于 analyst rating/price target 等外部响应，可在以下条件同时成立时聚合为一个有界 response episode：相同 ticker；明确响应同一财报、Investor Day、产品发布等催化剂；位于同一短期信息消化窗口，默认参考不超过 5 个交易日但不作为硬日期校验；动作类型同质；单条行动的独立研究价值较低。每个机构行动仍必须作为独立 Fact 保留机构、日期、评级或目标价的新旧值、方向和核心理由。没有共同催化剂、已跨入新的信息周期、由新信息触发，或形成具有独立价值的新 thesis/report 时，仍应拆成新的 occurrence。

### 4.2 Fact 定义

`Fact` 是描述某个 occurrence 的最小、独立、仍然具有业务含义的 Canonical 命题。

一个 Fact 应满足：

- 单独阅读能够理解主体、动作或指标；
- 与同 Event 内其他 Fact 不是同义重复；
- 保留数字、方向、期限、条件、状态等区分性限定；
- 明确区分 `ACTUAL`、`GUIDANCE`、`FORECAST`、`PLAN`、`RUMOR`、`DENIAL`、`SCHEDULED` 等 assertion state；
- 需要时保存 `subject_time`，避免把发生时间与财务季度、目标年份或未来计划混为一谈。

O2 可以对 CDECR Atomic 做文字规范化、真正重复合并或必要拆分，但不得为了摘要化把多个不同事实压成一个泛化句子。

### 4.3 不属于同一 occurrence 的典型情况

以下内容即使主题相同，也通常应拆成不同 Event：

- 没有共同催化剂、跨越不同信息周期，或由新的独立 thesis/report 触发的 analyst rating/price target action；仅机构不同不足以强制拆分同一催化剂后的有界 response episode；
- 财报发布与数周后的投资者会议更新；
- 交易宣布、监管审批、股东通过、交割完成等不同里程碑；
- 诉讼提起、初步裁决、最终判决、和解等不同阶段；
- 产品发布、量产、首批交付、后续扩产等不同动作；
- 同一长期供需主题下，不同日期出现的新数据或管理层新表态；
- 不同交易日的独立市场异动。

它们可以通过 `related_event_ids`、`supersedes_event_id` 或事件退役 redirect 保持关系，但不能因为共享主题而合为一个超长 Event。

### 4.4 不足以合并的信号

以下任一信号单独出现都不足以证明是同一 occurrence：

- 同一 ticker 或同一公司；
- 同一 Runtime Package；
- 同一天；
- 相似关键词或相同事件类型；
- 同一季度或目标年份；
- 同一事实被不同报道重复引用。

合并需要 occurrence 身份一致，或满足同一催化剂、有限窗口和同类响应的有界 episode 条件；共享宽泛主题本身仍不足以合并。

### 4.5 重复报道、重复事实与新 occurrence

需要区分三类情况：

1. **重复报道**：新 Atomic 只是转述已存在的 occurrence 和 Fact。映射为已知，不新建 Event/Fact。
2. **同一 occurrence 的新 Fact**：后续报道补充了同一发布场景中的数字或限定。更新既有 Event 的完整 revision。
3. **新的沟通或行动 occurrence**：管理层在新日期正式重申、更新、否认或撤回此前观点。即使内容相似，这个新的沟通动作本身也可能是新 Event；Fact 应表述为“在某日重申/更新/否认”，而不是再次复制原指导事实。

### 4.6 Ongoing matter 的处理

长期事项不做成一个不断膨胀的 Event。每个有独立时间锚点和业务意义的里程碑建立一个 Event Occurrence，再用关系连接。例如：

```text
E1 诉讼提起
  → related/superseded_by E2 初步裁决
  → related/superseded_by E3 和解
```

这样既能让 W1 识别“同一事项”，又能识别“新的阶段进展”。

### 4.7 事件覆盖边界

Canonical Library 应尽可能完整收录对 ticker 有意义的有效 occurrence，而不是只收录重大事件。低重要性事件通过 `is_important=false` 和 `include_in_reference_view=false` 退出 D2/D3 紧凑视图，不应仅因“不重要”而从完整库删除。

已发布的历史 Event 不因超过 CDECR Runtime 的 60 天活动窗口而自动删除。时间老化只影响 Reference View；只有确认重复、错误、撤销或合并时才退役 Canonical 对象。

只有以下内容可不进入 Event Library：

- 无法形成 occurrence 的泛泛背景句；
- 与 ticker 关系不足或明显错配；
- 同义重复且无新限定；
- 纯技术指标/估值快照/模板化市场描述，且不构成有业务意义的市场 episode；
- 信息冲突严重、时间边界无法确定且 Web Search 也不能合理裁定的内容，此类保留 `KEEP_PENDING`；
- 明显抽取错误或无业务语义的噪声，此类记录 `DROP_INVALID`。

显著价格异动可以作为 `MARKET_EPISODE` Event 保存，但 O2 不计算异常收益或归因；`price_analysis` 仍为空。

## 5. Canonical Event 与 Fact 目标 Schema

以下是 O2 编辑的业务 schema。具体 SQLite 拆表方式由配套方案决定。

```yaml
event_id: E142                         # 既有稳定 ID；新事件在 Bundle 中使用 T1 等临时 ID
ticker: MU
title: Micron FY2026 Q3 earnings release
event_type: EARNINGS_RELEASE
occurred_at: 2026-06-24
occurrence_time_precision: DAY
status: ACTIVE

canonical_summary: >-
  Micron reported record FY2026 Q3 results and issued sharply higher
  FY2026 Q4 guidance amid severe memory supply constraints.

known_event_summary: >-
  On Jun. 24, Micron reported FY2026 Q3 revenue of $41.46B and an
  81.2% operating margin; guided FY2026 Q4 revenue to about $50B and
  gross margin to about 86%; said data-center revenue exceeded $25B,
  memory supply would remain tight beyond 2027, and disclosed 16
  strategic customer agreements with $22B of projected deposits and
  related commitments.

is_important: true
include_in_reference_view: true
related_event_ids: [E188]
supersedes_event_id: null

facts:
  - fact_id: F901
    proposition: Micron reported FY2026 Q3 revenue of $41.46 billion.
    assertion_state: ACTUAL
    subject_time: FY2026-Q3
    entities: [Micron]

  - fact_id: F902
    proposition: Micron reported an FY2026 Q3 operating margin of 81.2%.
    assertion_state: ACTUAL
    subject_time: FY2026-Q3
    entities: [Micron]

  - fact_id: F903
    proposition: Micron guided FY2026 Q4 revenue to approximately $50 billion.
    assertion_state: GUIDANCE
    subject_time: FY2026-Q4
    entities: [Micron]

  - fact_id: F904
    proposition: Micron guided FY2026 Q4 gross margin to approximately 86%.
    assertion_state: GUIDANCE
    subject_time: FY2026-Q4
    entities: [Micron]

price_analysis: null
```

字段口径：

- `title`：能够识别 occurrence 的短标题，优先写主体、动作、期间/对象；避免使用“outlook”“development”“market dynamics”等无时间边界主题名。
- `occurred_at`：行动或信息释放时间，不等于 Fact 的 `subject_time`。允许日、月、区间或 `UNKNOWN` 精度，但在时间不精确时仍必须有足以区分 occurrence 的动作/阶段锚点。
- `canonical_summary`：供普通研究 Agent 快速参考，1–2 句，表达事件主干与投资相关性，不枚举全部细节。
- `known_event_summary`：供低参数 W1 进行新旧判断，长度可略长，必须保留能区分该 occurrence 的日期、主体、动作、阶段、关键数字、期限和新旧限定。
- `is_important`：事件是否值得未来价格分析或长期研究关注。
- `include_in_reference_view`：当前是否应进入 D2/D3 的紧凑参考事件库。重要事件通常为 true；近期但不重大的事件也可暂时为 true。
- `facts`：Event Detail 的完整 Canonical Fact 集合。
- `price_analysis`：O2 始终保留空值或原有值，不生成、不修改。未来价格分析 Agent 只处理 `is_important=true` 的 Event。

不在 Canonical Event/Fact 中保存：Source 数量、Source 列表、Mention、Evidence、Runtime Package ID、模型 reasoning、网页检索过程或逐字段编辑操作。

## 6. O2 Frozen View 输入契约

O2 的输入必须在单次 run 内冻结，并固定 `base_library_version`。输入既可以是单个文件，也可以物化为目录；推荐目录形态，因为 Codex 可以搜索和局部读取，而不需要把所有内容反复塞进 prompt。

```text
context/event_library/<frozen_view_id>/
  manifest.json
  known_event_index.md
  event_details/
    E1.json
    E2.json
    ...
  pending_delta.jsonl
  runtime_package_hints.jsonl
  reference_review_candidates.jsonl
  schemas/
    canonical_event.schema.json
    revision_bundle.schema.json
```

### 6.1 manifest

```yaml
run_id: o2_MU_20260824_D43
mode: INITIALIZE | INCREMENTAL
ticker: MU
as_of: 2026-08-24
base_library_version: 42
delta_batch_ids: [D43]
published_event_count: 186
pending_delta_count: 100
```

### 6.2 Known Event Index

Known Event Index 包含全部 active Published Event，但每个 Event 只编译为一行紧凑 Markdown：

```markdown
E001 | 2026-06-24 | MU FY26 Q3 results/Q4 guide | Revenue 41.46B; op margin 81.2%; Q4 revenue ~50B; GM ~86%; DC revenue >25B; supply tight beyond 2027; 16 strategic agreements.
```

固定列为 `event_id | occurred_at_or_range | title | known_event_summary`。文件不输出表头；每个 Event 严格占一行；内部换行折叠为空格；字段内容中的 `|` 转义为 `\|`；按 `occurred_at DESC, event_id ASC` 确定性排序。`event_type`、`entities` 和 `status` 不进入 Known Event Index，但仍保留在完整 Event Detail 和 Canonical 数据模型中。该 Markdown 是从 Published Revision 编译的只读 wire view，不是 Agent 可编辑的 Canonical 源文件。

索引不是 Top-K。O2 在日增量开始时必须看到完整 Known Event Index，然后按 ID 读取候选 Event Detail。这样不以模糊检索决定“哪些历史事件存在”，同时避免把每个历史 Fact 全部加载到上下文。

完整 Published Event/Fact 仍全部物化在 `event_details/`，任何 Event 都可按稳定 ID 展开；index-first 是上下文编排方式，不是删减历史库或检索召回门槛。

初始化时 Published 为空，Known Event Index 为空。

### 6.3 Event Detail

`event_details/E*.json` 是 Published Event 的完整目标 schema，包括全部 active Fact。只有在确认候选、修改、合并、拆分或 Reference 状态复审时需要读取。

### 6.4 Pending Delta

每个 CDECR Atomic 编译为一个 Delta item：

```yaml
delta_id: D43-17
proposition: Micron guided fiscal Q4 revenue to approximately $50 billion.
time: 2026-06-24 | FY2026-Q4
assertion_state: GUIDANCE
entities: [Micron]
runtime_hint_ids: [R8]
target_suggestion_ids: [E142]
```

`runtime_hint_ids` 和 `target_suggestion_ids` 只是检查入口，不具有约束力。O2 可以拆分 Runtime Package、跨 Package 合并 Atomic，也可以拒绝 suggestion。

输入不包含 `sources`。O2 默认把 CDECR 编译后的 proposition 作为权威事实输入；只有 proposition 自身冲突、与既有 Canonical 内容明显矛盾，或无法据此判断 occurrence 边界时，才按需使用 Web Search；若仍不确定则保持 Pending。

### 6.5 Reference review candidates

为了日常维护 `include_in_reference_view`，编排器可确定性列出需要复审的 Event，例如：

- 新建或本次发生实质更新的 Event；
- 曾因“近期”进入 Reference View、现已到复审时间的 Event；
- 被后续 Event supersede 的 Event；
- 重要性状态可能因新事实变化的 Event。

O2 只复审候选，不为日期自然推进而每天重写全库。

## 7. O2 工作区与输出契约

### 7.1 为什么采用文件工作区

O2 是编辑型 Agent。事件标题、Fact 和摘要可能只需要改几个字，因此工作区中的 pretty JSON/Markdown 文件是比操作 DSL 更自然的编辑面。

工作区沿用 Codex SDK V2 的 run/attempt 分层：

```text
context/event_library/<frozen_view_id>/     # 编排层写入、Agent只读
attempts/<attempt_id>/input/                # 编排层写入、Agent只读
  AGENTS.md
  agent.md
  skill.md
  task.json
  context.json
  output_schema.json
attempts/<attempt_id>/output/               # Agent可写
  work/
    occurrence_ledger.md
    candidate_maps/
    drafts/
      T1.json
      T2.json
    review_notes.md
  revision_bundle/
    manifest.json
    events/
      E142.json
      T1.json
    retirements.json
    residual_delta_resolutions.jsonl
  run_result.json
artifacts/event_library/revision_bundles/<bundle_id>/  # runner校验并提升
published/<release_id>/                              # 不可变发布结果
```

`attempts/<attempt_id>/output/work/` 允许自由起草和修改，不导入数据库。`attempts/<attempt_id>/output/revision_bundle/` 才是受 schema 约束的正式交付物。Dedicated O2 Runner 读取、hash并校验该目录；通过后复制到 `artifacts/`，Importer/Publisher 只消费已提升的 artifact，Agent 不直接写 `artifacts/` 或 `published/`。

`work/` 中的 ledger、candidate map 和 review notes 是 run 内临时工作材料，成功发布并完成必要 artifact 提升后，由编排层按 retention policy 清理，不作为 Canonical 审计长期保存。

### 7.2 Canonical Revision Bundle

Bundle 只表达本次 Published 目标状态，不表达“如何改”的命令序列。

```yaml
bundle:
  run_id: o2_MU_20260824_D43
  ticker: MU
  base_library_version: 42
  delta_batch_ids: [D43]

event_revisions:
  - events/E142.json       # 既有受影响 Event 的完整新 revision
  - events/T1.json         # 新 Event 的完整目标 schema

event_retirements:
  - event_id: E91
    redirect_to_event_id: E142
    reason: MERGED_DUPLICATE_OCCURRENCE

residual_delta_resolutions:
  - delta_id: D43-22
    resolution: DUPLICATE_FACT
    target_event_id: E142
    target_fact_id: F903
  - delta_id: D43-47
    resolution: KEEP_PENDING
  - delta_id: D43-88
    resolution: DROP_INVALID
```

进入新增/修订 Event 的 Delta ID 记录在对应 Fact 的 `consumes_delta_ids` 中。每个 Delta 必须恰好出现在以下一个位置：

- 某个 Event revision 的某个 Fact 的 `consumes_delta_ids`；
- `DUPLICATE_FACT`；
- `KEEP_PENDING`；
- `DROP_INVALID`。

`consumes_delta_ids` 是 Revision Bundle 的导入映射字段，不属于 Published Event Detail，也不是 Source provenance；Importer 完成 Runtime Delta disposition/lineage 后可从公开 Event schema 中移除它。

无需为“加入已有 Event”“编辑摘要”“移动 Fact”设计不同动作。既有 Event 的新文件就是该 Event 的完整目标版本。Repository 以同一 `event_id` 写入新 revision；未出现在 Bundle 中的 Event 自动继承上一 Published Revision。

Event/Fact ID 稳定性规则：

- Event occurrence 身份不变，只调整标题、摘要或少量措辞时，保留原 `event_id`；
- Fact 语义身份不变，只做纠错或表述优化时，保留原 `fact_id`；
- 真正新增的 Fact 使用临时 Fact ID，由 Importer 分配稳定 ID；
- 不得为避免编辑旧文件而给同一 occurrence 或同一 Fact 新建 ID。

### 7.3 合并与拆分

合并和拆分需要保留最小生命周期语义，但不恢复通用 Change Plan。

- **合并**：保留语义最完整或最早稳定的 Event ID作为目标；输出目标 Event 的完整 revision，并在 `event_retirements` 中把其他 Event redirect 到目标。
- **拆分**：原 Event ID保留给其主要 occurrence；其他 occurrence 使用新临时 ID，并写 `derived_from_event_ids`。如果原 Event 本身不再代表任何单一 occurrence，则将其退役并 redirect 到主要 successor。
- **Fact 去重**：只在目标 Event revision 中保留一个 Fact；旧 Fact redirect/lineage 由 importer 按稳定 ID 和 bundle 元数据维护。

### 7.4 小型最终响应

Codex thread 的最终文本响应不承载完整事件库，只返回小型 `run_result`：

```json
{
  "status": "BUNDLE_READY",
  "bundle_path": "attempts/<attempt_id>/output/revision_bundle",
  "base_library_version": 42,
  "delta_coverage": {"total": 100, "resolved": 96, "pending": 4},
  "validation": "PASS"
}
```

完整内容由工作区文件承载，避免一次性长结构化回复，也方便 Codex 对任意字段做局部 patch。

## 8. O2 的固定决策流程

每次处理都遵循同一语义顺序。Prompt 应要求执行这些检查，但不要求输出长 reasoning。

### Step 1：建立 occurrence anchor

先回答“这条 Atomic 描述的是哪一次发生或信息释放”，再看主题相似性。提取：

- actor/issuer；
- action or disclosure；
- occurred/announced/published time；
- object、stage、subject period；
- assertion state。

### Step 2：重建 Delta 内部事件边界

在本轮 Delta 内先形成候选 occurrence：

- 同 Package 可以拆分；
- 跨 Package 可以合并；
- 同一 occurrence 的互补 Atomic 合为多 Fact Event；
- 同义 Atomic 只保留一个 Canonical Fact；
- 无 occurrence 锚点内容标记为背景、Pending 或 invalid。

### Step 3：匹配既有 Event

日增量使用完整 Known Event Index 扫描候选。匹配顺序为：

1. occurrence 时间/发布场景；
2. actor、action、object/stage；
3. assertion state 和 subject time；
4. distinctive numbers/terms；
5. title/lexical similarity。

候选不明确时读取多个 Event Detail，不得只因第一个候选“看起来相似”就停止。

### Step 4：对每个 Delta 做归属

内部处置保持少量枚举，系统仍可向上归并为二元“已知/新”：

| O2 处置 | 含义 | 对新旧的上层归类 |
| --- | --- | --- |
| `DUPLICATE_FACT` | 同一 occurrence、同一事实 | 已知 |
| `ADD_FACT_TO_EXISTING` | 同一 occurrence、新的互补事实 | 已知事件的更新 |
| `REVISE_EXISTING_FACT` | 同一 occurrence、对既有事实的更正/更精确表述 | 已知事件的更新 |
| `CREATE_NEW_EVENT` | 相关或不相关，但发生锚点是新的 occurrence | 新 |
| `KEEP_PENDING` | 证据不足，暂不判定 | 未决 |
| `DROP_INVALID` | 抽取错误、无关或不构成事件 | 不进入库 |

这些枚举是 O2 的编辑处置，不要求 W1 暴露同等复杂度。W1 仍可将自己的细分枚举统一归入二元新/旧类别。

### Step 5：编辑目标 Event

对所有新增或受影响 Event：

1. 确定 occurrence title、type、occurred_at；
2. 整理完整 Fact 集合；
3. 去除真正重复，保留重要限定；
4. 生成 `canonical_summary`；
5. 生成更充分的 `known_event_summary`；
6. 判断 `is_important`；
7. 判断 `include_in_reference_view`；
8. 维护 related/supersede/derived 关系；
9. 保留 `price_analysis=null` 或既有值。

### Step 6：全局交叉核对

发布前必须跨 Runtime Package 和跨起草波次检查：

- 是否把同一 occurrence 建成多个 Event；
- 是否把不同日期/阶段的 occurrence 错并；
- 是否存在重复 Fact；
- 是否有 Delta 未被处置；
- 标题、摘要和 Fact 是否互相矛盾；
- Event relation 是否形成循环；
- 新旧 Event ID 是否稳定、退役 redirect 是否明确。

### Step 7：输出并预检 Bundle

O2 写入正式 Bundle 后调用本地 validator。可修复的错误直接 patch 对应 Event 文件；不能可靠处理的 Delta 转 `KEEP_PENDING`。最终只返回简短运行结果。

## 9. 初始建库工作流

初始化输入可能达到数万 token，且 Runtime Package 边界质量不稳定。不要要求模型在一次 response 中直接生成完整 V1。推荐同一 run、同一 frozen view 下的多阶段文件编辑工作流。

### 9.1 编排阶段

```text
PREPARE
  → SURVEY
  → LOCAL_RECONSTRUCTION
  → GLOBAL_RECONCILIATION
  → CANONICAL_EDIT
  → BUNDLE_VALIDATE
  → IMPORT_WORKING
  → PUBLISH_V1
```

### 9.2 PREPARE

编排器完成：

- 校验 CDECR snapshot/epoch 已 FINALIZED；
- 生成不可变 manifest 和 Delta IDs；
- 物化全部 Package/Atomic，但去除 Mention/Evidence/Source/audit；
- 按 Runtime Package、时间和实体生成导航文件；
- 创建空 `occurrence_ledger.md` 和 drafts 目录；
- 固定 ticker 单写锁和 `base_library_version=0`。

### 9.3 SURVEY

O2 先浏览全局目录和 Package 标题，不立即发布 Event。它建立 occurrence ledger，记录：

- Runtime Package 可能包含的 occurrence 数；
- 可能跨 Package 重复的高风险主题；
- 时间未知或冲突的 Atomic；
- 需要 Web Search 裁定的少数事项。

这一步的目标是建立全局地图，避免按文件顺序机械复制 Package。

### 9.4 LOCAL_RECONSTRUCTION

O2 按可管理波次处理 Package/Atomic，每个波次只生成 `work/drafts/T*.json`：

- 为 occurrence 分组；
- 合并真正重复 Atomic；
- 保留所有互补 Fact；
- 标记可能跨波次重复的 candidate key；
- 所有 Delta ID先完成暂定 coverage。

波次只是工作区分片，不形成 Published 中间版本。

### 9.5 GLOBAL_RECONCILIATION

所有波次结束后，O2 必须读取全局 occurrence ledger 和 draft index，专门执行一次：

- 跨 Package 合并同一 occurrence；
- 拆分主题型超大 Event；
- 对 analyst actions、市场日行情、长期事项 milestone 做时间边界检查；
- 统一 title/type/time/assertion state；
- 删除 draft 级重复，重新核对 Delta coverage。

没有这一阶段，分波次只会把 Runtime Package 错误固化进 Canonical Library。

### 9.6 CANONICAL_EDIT 与发布

O2 为全部 V1 Event 生成完整 schema、双摘要和两个视图标记，再形成一个最终 Bundle。Validator 全部通过后，Repository 一次事务导入 Working V1 并切换 Published head。

初始化验收重点不是“Event 数量接近 Runtime Package 数量”，而是 occurrence 边界与 Fact 完整性。

## 10. 每日增量工作流

日增量只处理新/变化 Runtime Atomic 和少量 Reference 复审对象，不重写整个 Published Library。

### 10.1 编排阶段

```text
PREPARE_DELTA
  → READ_FULL_INDEX
  → BUILD_CANDIDATE_MAP
  → LOAD_EVENT_DETAILS
  → RECONSTRUCT_AND_EDIT
  → REFERENCE_REVIEW
  → VALIDATE_BUNDLE
  → IMPORT_WORKING
  → PUBLISH_VN
```

### 10.2 READ_FULL_INDEX

O2 第一轮读取完整 Known Event Index 和本轮全部 Delta。Known Event Index 的职责是让 O2 知道所有历史 occurrence 的存在，并提供足够详细的识别信息；它不是 Event Detail 的替代品。

### 10.3 BUILD_CANDIDATE_MAP

O2 先对 Delta 内部进行 occurrence 分组，再为每个候选 occurrence 列出：

- 可能是同一 occurrence 的既有 Event IDs；
- 可能只是 related matter 的 Event IDs；
- 需要展开的 Event Detail IDs；
- 可能直接判为新 Event 的理由标签。

这是工作笔记，不进入正式 Bundle。

### 10.4 LOAD_EVENT_DETAILS

O2 按 ID 读取候选 Event 的完整 Fact。若 index 信息不足，可以扩大到其他 ID 或使用本地字段/FTS 查询；任何检索都只帮助导航，不能因 Top-K 未返回而断言历史事件不存在。

只有 CDECR proposition 自身冲突、与既有 Canonical 内容明显矛盾，或无法据此确定发生时间/动作边界时才使用 Web Search。Web Search 不默认触发，也不生成 Canonical Source 字段。

### 10.5 RECONSTRUCT_AND_EDIT

O2 形成受影响 Event Working Set：

- 新 Event 使用临时 ID；
- 既有 Event 使用稳定 ID并写完整新 revision；
- 只重复报道的 Delta 写 residual resolution；
- 不确定项保留 Pending；
- 不受影响 Event 不出现在 Bundle。

### 10.6 REFERENCE_REVIEW

O2 对新/变化 Event 和 `reference_review_candidates` 复审：

- `is_important` 关注业绩/指导、资本配置、重大订单/合同、产品与产能、监管/诉讼、管理层变化、并购融资、显著供需变化及其他可能改变预期的事件；
- `include_in_reference_view` 关注当前 D2/D3 是否仍需要看到。重要事件、近期新信息、仍在演进的 matter 或会影响未来预期的事件通常保留；陈旧且已被更新替代的例行事件可以退出。

字段没有变化时不写新 revision。

### 10.7 发布

Bundle 通过校验后直接导入新的 Working Revision。Repository 比较 `base_library_version`，成功后原子切换 Published head，并从新版本重建 Known Event Index 和 Reference View。

默认一个交易日一个 O2 run、一个最终发布版本。如果 Delta 太大，可分波次起草，但必须先在工作区全局对账，再形成一个最终 Bundle；不允许各波次独立发布而产生跨波次重复 Event。

如果本日 Delta 和 Reference review candidates 都为空，编排器直接记录 `FINALIZED_NOOP`，不启动模型。如果 Delta 为空但存在到期复审对象，可以启动只包含这些 Event Detail 的轻量 Reference review run。

## 11. Codex SDK 编排设计

### 11.1 Thread 生命周期

推荐采用“一个 O2 run 一个 Codex thread”：

- 初始化 run 独立一个 thread；
- 每日增量 run 新建一个 thread；
- 同一 run 因超时、进程重启或 validator repair 时恢复原 thread；
- 跨日事实连续性来自 Published Library，不依赖长期对话记忆。

这样可以避免长期 thread 的隐式状态与当前 `base_library_version` 发生漂移，同时保留 run 内断点恢复。

O2 在 Codex SDK V2 中继续使用业务身份 `O2`；旧自建 ReAct V1 中的同名 Monitoring Config Agent 可独立重命名，不构成 V2 命名阻塞。V2 实现新增独立的 `codex_event_library_v1` workflow、`event_library` lane 和 O2 role，不把 Event Library Maintainer 塞入 `codex_document2_v1`。

同一 run 的超时、validator repair和进程恢复必须读取已持久化的 `thread_id` 并恢复同一 thread；同时将阶段、Frozen View ID、base version、Bundle path/hash和 validator 状态持久化在 workspace/repository。Thread 保留运行内交互连续性，但不能成为唯一业务状态源。

### 11.2 模型配置

```yaml
model: <event_library_model，首版默认 gpt-5.6-luna>
reasoning_effort: <event_library_effort，首版默认 max>
workspace_access: read/write run workspace only
network: web search only when adjudication requires it
database_access: none
```

首版可以使用 GPT-5.6 Luna（`reasoning_effort=max`），但具体 model/provider/effort 由 Event Library workflow settings 提供并记录在 run metadata 中，不进入 Event/Revision Bundle 业务契约。无论模型上下文能力如何，文件编辑、局部 patch和分阶段校验仍是主要工作模式。

### 11.3 Prompt 分层

Prompt 不应把所有规则和全部数据写成一个超长 user message。建议：

```text
prompts/codex_v2/event_library/
  AGENTS.md
  agents/
    o2.md
  skills/
    initialize.md
    incremental.md
    global-reconciliation.md
    revision-bundle.md

attempts/<attempt_id>/input/
  AGENTS.md          # V2工作区权限、只读/可写路径和发布不变量
  agent.md           # 稳定O2角色、Event Occurrence/Fact职责
  skill.md           # 当前阶段的方法与少量必要正反例
  task.json          # mode、stage、必读文件、输出路径、恢复点和上次错误
  context.json       # ticker、manifest、Frozen View和当前业务状态
  output_schema.json # 小型O2RunResult的精确Pydantic契约
```

Canonical Event 与 Revision Bundle schema 作为 Frozen View 中的独立只读文件提供，由本地 validator 校验，不在 Prompt 中重复描述完整 schema。完整 fixture 留在测试/eval；运行所需的少量正反例放入对应 `skill.md`，不另设每轮重复注入的 examples 层。

正常通过项不要求输出解释或 chain-of-thought。只保留结构化 disposition、目标 ID 和 validator 结果。

### 11.4 Agent 可用工具

以下能力是 O2 V2 workflow 的目标能力，不应在接通前写成已经注册的工具。首版实现采用：

- 文件列表、全文搜索和局部读取；
- 对工作区 JSON/Markdown 的 create/patch；
- Event Detail 通过 Frozen View 本地文件按 ID 读取，暂不要求独立 `get_event_details` 工具；
- Event 搜索通过本地全文搜索和确定性索引查询完成，暂不要求独立 `search_events` 工具；
- `validate_revision_bundle` 由 Dedicated O2 Runner 接入确定性本地 validator；若未来暴露为工具，必须先注册并加入该 V2 workflow/node/role 的服务端 allowlist；
- 按需 Web Search。

O2 不拥有：

- 任意 SQLite/SQL 工具；
- Published head 切换权限；
- 物理删除 Event/Fact 的能力；
- CDECR Runtime Registry 写权限；
- 价格数据或价格分析写入权限。

## 12. 确定性校验与直接导入

### 12.1 必须阻断发布的校验

- Bundle `ticker/base_library_version/delta_batch_ids` 与 Frozen View 一致；
- base version 仍等于当前 Published head；
- 每个 Delta 恰好处置一次；
- 每个 Event 至少一个有效 Fact；
- Event ID、Fact ID 和临时 ID 无冲突；
- 同一 active Fact 不属于多个 active Event；
- retire/redirect/related/supersede 无循环；
- 既有 Event 的稳定 ID没有被无依据替换；
- schema 类型和必要枚举有效；
- O2 新建或修改的 `price_analysis` 为空，既有非空值未被覆盖；
- Bundle 中不存在 Source/Mention/Evidence/reasoning 字段。

### 12.2 可局部修复或转 Pending 的问题

- Delta 漏处置：转 `KEEP_PENDING`；
- 候选 Event Detail 未读取充分：提示 O2补读并 repair；
- 可选实体或 subject time 格式错误：忽略可选值或 patch；
- 摘要缺少 occurrence 区分信息：要求 O2重写该 Event 文件；
- temp ID 格式问题：确定性重新编号；
- 单个 invalid Delta：转 `DROP_INVALID`，不阻断其他合法 Event。

### 12.3 Import 语义

Importer 对每个 Bundle Event 执行“整 Event revision 替换”：

- 既有 `event_id`：创建一个新的 Event revision 和对应 Fact/membership revision；
- 临时 Event ID：分配新的稳定 Event ID和 Fact IDs；
- 未出现在 Bundle 的 Event：继续引用上一 Published revision；
- retirement：写状态和 redirect，不物理删除；
- residual Delta：写最小 disposition 或继续 Pending；
- 全部写入同一个 Working Revision，最后原子切换 Published head。

Importer 可以内部计算 SQL 差异以减少写入，但这只是 repository 实现细节，不是 O2 的协议。

## 13. 两个下游编译视图

### 13.1 Known Event Index

用途：W1 第一轮新旧判断和 O2 日增量历史候选扫描。

包含全部 active Event，每个 Event 编译为无表头 Markdown 单行：

```markdown
E001 | 2026-06-24 | MU FY26 Q3 results/Q4 guide | Revenue 41.46B; op margin 81.2%; Q4 revenue ~50B; GM ~86%; DC revenue >25B; supply tight beyond 2027; 16 strategic agreements.
```

固定列为 `event_id | occurred_at_or_range | title | known_event_summary`，不导出 `event_type`、`entities` 或 `status`；转义、换行归一与排序规则与 §6.2 一致。

W1 通过 Responses API 读取 index。若不能确定，首轮返回希望查看的 Event IDs；服务再把完整 Event Detail 作为下一轮输入。Known Event Index 必须足够具体，使低参数模型能直接识别日期、动作、阶段和关键数字。

### 13.2 Reference Event View

用途：计划中的 Blackboard 初始化 D2 预期研究与 D3 交易策略。当前 Codex SDK V2 仅在 D2 预留了只读 Event Library port，尚未配置；D3 消费路径尚未实现，均应在 Phase 5 分别接入和验收，不能作为 O2 首版已存在能力。

只导出 `include_in_reference_view=true` 的 Event，推荐包含：

```text
event_id
occurred_at
event_type
title
canonical_summary
is_important
```

需要细节的下游 Agent 可按 Event ID 请求 Event Detail，不在公共上下文重复携带全量 Fact。

两个视图均由 Published Canonical Library 编译，不接受 Agent 直接编辑或反向覆盖数据库。

## 14. 基于 MU sample 的边界示例

### 14.1 跨 Runtime Package 合成一个 occurrence

Sample 中以下 Runtime Package/Atomic 可能共同指向 2026-06-24 的同一次 earnings release：

- `Micron Q3 FY2026 earnings release`；
- `Micron long-term customer agreements` 中当日披露的 16 份协议与 $22B commitments；
- `Micron memory supply outlook` 中当日管理层对 2027 后供需的表态；
- `AI memory market competition` 中同次披露的数据中心收入和 HBM 信息。

只要发生/披露锚点确认为同一财报发布，它们应组成一个 Event Occurrence，多条 Atomic 变为多条 Fact，而不是四个主题 Event。

### 14.2 一个 Runtime Package 拆成多个 occurrence

`Analyst actions on Micron in 2026` 不能成为一个无边界的 Canonical Event，但也不应仅按机构机械拆成大量低价值 Event。应先识别共同催化剂和信息消化窗口：

- 2026-06-24 BofA、2026-06-25 Needham、2026-06-29 Cantor Fitzgerald 如果均明确响应同一次 FY2026 Q3 earnings，可聚合为 `MU post-FY26 Q3 analyst rating/target revisions`，每家机构行动保留为独立 Fact；
- 2026-07-14 KeyBanc 若由新的 conference/channel 信息触发，应建立新的 occurrence；
- 2026-08-07 Citi target cut 与 2026-08-14 New Street upgrade 只有在响应同一新催化剂且位于同一有限窗口时才可聚合，否则分别建立 occurrence。

机构不同本身不是拆分条件；共同催化剂、时间窗口、动作同质性和独立研究价值共同决定 occurrence 边界。

### 14.3 相关但不同 occurrence

`Micron presentation at KeyBanc Technology Leadership Forum` 是 2026-08-10 的新沟通 occurrence，不应并入 2026-06-24 earnings release。它可以关联前次财报，并包含：

- 2027 供给预计比 2026 更紧；
- 数据中心客户需求只能满足约一半；
- earnings 后又签署了更多协议；
- 美国投资承诺从 $200B 增至 $250B。

即使其中一些观点与财报一致，新日期上的管理层更新和重申本身仍可构成新的 Event。

### 14.4 重复事实去重

Sample 中 `Micron generated $41.46B in quarterly revenue`、`revenue surged 345.8% to $41.46B`、`revenue reached $41.5B` 可能是同一基础指标的重复或带不同限定的表述。Canonical 处理应是：

- 保留一个主 Fact：FY2026 Q3 revenue = $41.46B；
- 如果同比增长 345.8% 是可靠且有区分力的独立限定，可保留第二个 Fact；
- 不保留三个只因措辞不同而重复的 Fact。

## 15. 失败恢复与幂等

| 失败点 | 处理 |
| --- | --- |
| Frozen View 编译失败 | 不启动 O2，不改变 Published |
| Codex 调用失败 | 保留 workspace、thread ID 和 Pending Delta；恢复同一 thread |
| O2 已写 Bundle、进程中断 | 先重新运行 validator；通过则直接 import，不重调模型 |
| Validator 可修复错误 | 恢复同一 thread，只给错误报告和目标文件 |
| Validator 仍不通过 | 可可靠隔离的 Delta 转 Pending；否则整 run 不发布 |
| base version 过期 | 拒绝 import；基于新 Published 重新编译 Frozen View并启动新 run |
| SQLite Working 写入失败 | 回滚 Working，不影响上一 Published；复用已验证 Bundle重试 |
| Published head 切换前停电 | 恢复时检查事务/Working 状态，不出现半发布 |
| 导出视图失败 | Published 仍有效，从同一 version无模型重建 |

幂等键由配套方案提供，至少包含 ticker、Runtime epoch/Delta batch、Runtime Atomic ID/version 和整数 base library version。O2 不另造 hash/fingerprint 体系。

## 16. 评测与验收

### 16.1 Gold fixture

以当前 MU sample 构造人工标注 fixture，至少覆盖：

- 跨 Package 合并同一 earnings occurrence；
- analyst-actions 年度宽泛 Package 拆分，以及同一催化剂后有界 response episode 的聚合；
- later conference update 与 earlier earnings 分离但关联；
- 同 Fact 多种措辞去重；
- 同 occurrence 新补充 Fact；
- later reaffirmation 作为新沟通 occurrence；
- ongoing legal/transaction milestone；
- Rumor/denial/scheduled/guidance；
- 市场大幅异动与普通技术快照的边界；
- 时间冲突和 `KEEP_PENDING`。

### 16.2 核心指标

- Delta coverage = 100%；
- occurrence boundary precision/recall；
- 同一 occurrence 重复建 Event 的比例；
- 不同 occurrence 错并比例；
- Canonical Fact 业务信息保留率；
- 真正重复 Fact 的消除率；
- 日增量 stable Event ID 保持率；
- `ADD_FACT_TO_EXISTING` 与 `CREATE_NEW_EVENT` 准确率；
- Known Event Index 单轮判断准确率；
- W1 index + Event Detail 二轮判断准确率；
- Reference View token 压缩率与重要/近期事件召回率；
- O2 非法 Bundle、repair 和 Pending 比例。

### 16.3 必须通过的业务验收

- Runtime Package 不会被未经重建地直接发布；
- 修改摘要几个字只需 patch 一个 Event 文件，不需要模型生成字段操作；
- 初始化分波次不产生跨波次重复 Event；
- 日增量只输出受影响 Event 的完整 revision；
- 每个 Delta 恰好处置一次；
- Known Event Index 包含全部 active Event；
- Reference View 只包含被 O2 标记的 Event；
- Source 数量/列表不进入 O2 输入和 Canonical schema；
- O2 不产生价格分析；
- stale base、Agent失败或 DB失败都不会污染上一 Published version；
- 同一成功 Bundle 可在不重新调用模型的情况下恢复导入和导出。

## 17. 建议模块边界

O2 自身作为独立模块实现，通过 ports 对接配套方案：

```text
src/doxagent/event_library/        # 业务契约与Canonical repository；不依赖Codex SDK
  contracts.py
  semantics.py
  repository.py
  frozen_view_provider.py
  revision_bundle_importer.py
  publisher.py
  view_compiler.py
  reference_policy.py
  bundle_validator.py

src/doxagent/workflows/codex_event_library/  # Codex SDK V2 O2 workflow
  inputs.py
  orchestrator.py
  runner.py
  workspace.py
  occurrence_index.py
  web_adjudication.py

prompts/codex_v2/event_library/
  AGENTS.md
  agents/o2.md
  skills/*.md

tests/
  fixtures/event_library/mu/
  test_event_library_semantics.py
  test_codex_event_library_initialization.py
  test_codex_event_library_incremental.py
  test_event_library_revision_bundle.py
  test_event_library_reference_policy.py
  test_codex_event_library_runner_resume.py
```

Codex Event Library workflow 不得 import CDECR SQLite 具体实现；它只依赖 Frozen View contract。Event Library repository 也不应依赖 O2 prompt 或 Codex SDK。

## 18. 实施顺序

### Phase 1：冻结语义与 fixture

1. 冻结 Event Occurrence/Fact 定义和边界案例。
2. 用 MU sample 标注 gold occurrence、Fact 和关系。
3. 冻结 Canonical Event、Known Event Index、Revision Bundle schema。
4. 明确 `is_important/include_in_reference_view` 标注准则。

### Phase 2：工作区与 Bundle 基础设施

1. 实现 Frozen View 目录物化。
2. 实现 Event-per-file 编辑格式。
3. 实现 Bundle validator 和 Delta coverage。
4. 实现 direct importer、Working Revision 与原子 publish。
5. 完成无模型的 schema/事务/恢复测试。

### Phase 3：O2 初始化原型

1. 注册独立的 `codex_event_library_v1` workflow/lane/O2 role，建立 Codex SDK runner 和 run-scoped thread。
2. 实现 SURVEY、分波次 draft、全局 reconcile prompt。
3. 运行 MU fixture，人工审查 occurrence 边界。
4. 迭代示例和 prompt，而不是扩大操作枚举。

### Phase 4：O2 日增量

1. 实现完整 Known Event Index 首轮读取。
2. 实现按 ID Event Detail 展开和 candidate map。
3. 实现受影响 Event Revision Bundle。
4. 实现 Reference review candidates。
5. 完成 stale base、resume 和部分 Pending 测试。

### Phase 5：下游接入与评测

1. 编译 Known Event Index并接 W1 的 index → detail 两轮流程。
2. 编译 Reference Event View；接通并验收 D2 已预留的只读 port，D3 待其 Codex SDK V2 消费路径实现后另行接入。
3. 跑新旧判断、边界、压缩和稳定性 eval。
4. 在质量基线达标后接入每日开市前正式任务。

## 19. 首版发布门槛

只有同时满足以下条件，O2 才进入正式每日维护：

1. MU gold fixture 的 occurrence 拆分/合并结果达到人工可接受基线；
2. 初始化可以从空库形成一个完整、无跨波次重复的 V1；
3. 日增量能正确区分重复 Fact、同 Event 新 Fact 和新 occurrence；
4. O2 可以自由编辑完整 Event 文件，Repository 无需 Change Plan；
5. Bundle 具备 100% Delta coverage和稳定 ID校验；
6. Published/Working 隔离、stale base 拒绝和原子 head switch 成立；
7. 完整 Known Event Index + 按 ID Event Detail 流程通过 W1 eval；
8. Reference View 显著压缩 payload，且重要/近期事件召回达标；
9. Source 字段已经移除；CDECR proposition 是权威事实输入，Web Search 仅用于真实冲突或 occurrence 边界无法判定时的临时裁定；
10. `price_analysis` 在 O2 全链路保持空值或不被覆盖；
11. Agent、validator、importer、export 任一阶段失败均可恢复且不污染 Published；
12. 运行产物只保留必要 manifest、Bundle、validator report 和版本结果，不保存模型 reasoning。

首版应优先验证事件边界质量、编辑体验和增量稳定性。检索裁剪、并行发布、价格分析和更复杂的事件关系网络，应在真实数据证明有必要后分别立项。

## 20. OpenAI 能力依据

- GPT-5.6 Luna（`reasoning.effort=max`）是首版默认配置，不是 Event Library 业务契约；模型、provider和effort由 workflow settings 提供并记录在 run metadata 中。实现不得依赖某个模型的宣传上下文或最大输出容量，而应通过分阶段文件编辑、本地校验和业务 eval 保持可替换性。[GPT-5.6 Luna model](https://developers.openai.com/api/docs/models/gpt-5.6-luna)
- Codex SDK 支持启动、继续和按 thread ID 恢复本地 Codex thread，适合实现“一次 O2 run 一个 thread、run 内断点恢复”的 runner。[Codex SDK](https://learn.chatgpt.com/docs/codex-sdk)
- Structured Outputs 能保证 schema adherence，但结构正确不等于 occurrence 判断正确，因此只用于小型结果/接口约束；完整事件内容仍需本地 validator 与业务 eval。[Structured model outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
