# O2 Event Library 上下文组件与确定性程序对齐修复方案

> 日期：2026-08-26  
> 状态：待实施  
> 适用范围：`codex_event_library_v1` 的初始化、增量维护、Reference Review、Revision Bundle 校验/导入以及 Published 视图编译  
> 依据：`O2_Event_Library_Maintainer_Development_Plan.md`、`CDECR_CANONICAL_EVENT_LIBRARY_INCREMENTAL_AGENT_INTEGRATION_PLAN_20260824.md`、当前 O2 prompt/skill、当前冻结 Schema、确定性实现及 MU R2 真实验收结果

## 1. 本轮裁决口径

以下口径优先于当前实现或旧 prompt 中的冲突表述：

1. 初始化阶段允许把原计划中的 `CANONICAL_EDIT` 合并进 `GLOBAL_RECONCILIATION`，不强制增加新的模型 turn；但 Reconciliation 必须承担完整的逐字段终审，不能只做 Event 合并、拆分和 Delta coverage。
2. Web Search 以 Survey、Wave 和 Reconciliation 的日期裁定规则为准。当前 `AGENTS.md` 中“只有 proposition 冲突或 occurrence 边界无法判断时才可搜索”的旧限制需要重写。
3. Canonical Event/Fact、Event Retirement、Reference Review 等 Pydantic/冻结 Schema 是正式字段契约。Schema 与 validator 冲突时，以 Schema 为准，调整 validator/importer/repository，不通过收窄 Schema 规避冲突。
4. Reference Review 的 Event Detail 访问以 `incremental-reference-review.md` 为准：显式候选必须能读取完整 Detail；隐式候选可先读 review index，必要时再展开 Detail；review-only run 也必须能读取其候选 Detail。
5. Reference Review 的 10/30/7 天时钟由确定性程序计算。模型只负责语义判断，不负责计算或自由决定 `reviewed_at`、`review_mode`、`candidate_reason`、`changed` 和 `next_review_at`。
6. O2 必须能在自己的只读 Frozen View 中读取 D1 C1/C3/C5 已发布报告正文；只有 ArtifactRef、路径和 hash 而没有正文不算完成接入。
7. 现有 Published V1、原始 Revision Bundle 和验收报告保持不可变。修复通过新的 O2 run 验证，不回写历史产物，也不重跑昂贵的 CDECR 上游。

## 2. 修复目标

修复后必须同时满足：

- 每个 O2 阶段实际获得共享 Canonical 语义和当前阶段方法，而不是只在角色文案中引用一个不可见的 foundation。
- 所有需要模型判断的重要字段都有明确、可执行、无冲突的语义说明和最小正反例。
- Agent 能看到的 Schema 覆盖整个 Revision Bundle，而不只有 Event 和 manifest。
- Reconciliation 对最终 V1 的每个 Event、Fact、关系、摘要、时间和视图标签执行逐字段终审。
- Reference Review 的语义判断与确定性时钟彻底分离。
- Schema允许的合法值不会被 validator 用额外的硬编码规则静默拒绝。
- Known Event Index、Reference View 和 review schedule 使用同一套规范化 occurrence time。
- 结构通过、语义异常和真实业务验收三者分开报告，不能再由空集合指标产生形式满分。

---

# 第一部分：Prompt、Skill 与其他上下文组件修改

## 3. 上下文装配方式

### 3.1 保留六文件 attempt 契约

继续保留：

```text
AGENTS.md
agent.md
skill.md
task.json
context.json
output_schema.json
```

不新增第七个必读文件，以减少 Worker、恢复和旧 workspace 契约变更。

### 3.2 `skill.md` 改为确定性组合结果

每次 seed attempt 时生成：

```text
skill.md = skills/foundation.md + 当前阶段 skill
```

组合内容必须有清晰边界，例如：

```markdown
# Shared Canonical Foundation
...

---

# Current Stage Workflow
...
```

`foundation.md` 和阶段 skill 继续作为独立源码维护；attempt 中的 `skill.md` 是二者的不可变组合快照。Prompt manifest同时记录两个源文件和组合结果的 SHA-256。

### 3.3 修复输入读取顺序

当前“必须按 `task.json` 声明顺序读取，但 `task.json` 本身排在第四”的表述存在自指问题。调整为：

1. 先读 `task.json`，取得当前阶段、必读路径、权限和恢复点；
2. 再按其中的 `content_input_order` 读取 `AGENTS.md`、`agent.md`、组合后的 `skill.md`、`context.json` 和 `output_schema.json`；
3. 再读取 Frozen View manifest及当前阶段列出的业务文件。

## 4. 重写 `AGENTS.md`

`AGENTS.md` 只保留跨阶段权限和硬边界，不重复阶段方法。需要完成以下修改：

1. 删除旧的 Web Search 限制，改为：
   - proposition冲突、与既有Canonical内容矛盾、occurrence边界不清时可以搜索；
   - 对财报、公告、filing、analyst action、交易里程碑等应为`DAY`的公开事件，若Frozen View只有 broad/`UNKNOWN`时间且日期可合理查证，也应执行聚焦搜索；
   - 搜索只用于裁定，结果不得变成Canonical Source字段；超过Frozen `as_of`的信息不得用于判断。
2. 明确Runtime Package、`runtime_hint_ids`和`target_suggestion_ids`都只是导航提示，不能决定Canonical归属。
3. 明确`entities`是Delta匹配提示，不得复制进Canonical Fact。
4. 明确D1正文只是背景上下文；每个Published Fact仍必须由Delta支撑。
5. 明确正式Bundle只能写入当前attempt的`output/revision_bundle/`，返回的`bundle_path`不得指向其他attempt、artifact或published目录。
6. 保留Source/Mention/Evidence/reasoning禁入、Atomic处置、`price_analysis`保护和Published控制面禁写规则。
7. 删除“Reconciliation owns flags”可能被理解为Wave统一写占位值的表述；Wave字段规则改由对应skill明确规定。

## 5. 修订共享 `foundation.md`

### 5.1 Analyst response episode

统一为开发计划口径：

- 机构不同本身不是强制拆分条件；
- 相同ticker、相同明确催化剂、同质动作、有限信息消化窗口且单条行动独立研究价值较低时，可以形成一个有界analyst response episode；
- 每家机构、行动日期、rating/target新旧值、方向和理由仍分别保留为独立Fact；
- 没有共同催化剂、进入新信息周期、出现新thesis/report或新事实触发时拆分为新Event。

Survey、Wave、Reconciliation和Incremental Edit中的“different institution直接形成新Event”全部同步修改。

### 5.2 `event_type`

Schema继续保持非空字符串，但Prompt冻结首版规范词表和兜底规则。至少覆盖：

```text
EARNINGS_RELEASE
GUIDANCE_UPDATE
ANALYST_ACTION
ANALYST_RESPONSE_EPISODE
INVESTOR_EVENT
PRODUCT_ANNOUNCEMENT
PRODUCT_MILESTONE
CAPACITY_OR_CAPEX
SUPPLY_DEMAND_UPDATE
MATERIAL_CONTRACT
FINANCING
CAPITAL_RETURN
M_AND_A
REGULATORY_ACTION
LITIGATION_MILESTONE
MANAGEMENT_CHANGE
MARKET_EPISODE
OTHER_CORPORATE_EVENT
```

要求：

- 使用大写snake case；
- 同义事件复用既有类型，不自由发明近义词；
- 无法归类时使用`OTHER_CORPORATE_EVENT`，不得创建`market_move/market_movement`一类重复标签；
- 新增类型必须作为后续合同升级处理，不能由单个模型turn临时扩张词表。

### 5.3 `occurred_at` 与 `occurrence_time_precision`

在skill中给出一一对应的Canonical写法：

| precision | `occurred_at`写法 |
| --- | --- |
| `TIMESTAMP` | 带时区ISO-8601时间 |
| `DAY` | `YYYY-MM-DD` |
| `MONTH` | `YYYY-MM` |
| `QUARTER` | `YYYY-Q1..Q4` |
| `YEAR` | `YYYY` |
| `INTERVAL` | `<canonical-start>..<canonical-end>` |
| `UNKNOWN` | `UNKNOWN` |

同时明确：

- `occurred_at`只能是行动发生或信息公开时间；
- 财报对象期、forecast horizon和文章覆盖区间不得代替occurrence时间；
- `fiscal Q3 2026`等对象期属于Fact的`subject_time`；
- date-specific公开事件优先解析到`DAY`；
- 只有合理搜索仍无法确定日期时才使用`UNKNOWN`。

### 5.4 `assertion_state`

保留Schema中的全部合法枚举，但定义新写入时的首选规范：

- `ACTUAL`：已经发生或正式披露的事实；
- `GUIDANCE`：issuer/management正式给出的经营或财务指引；
- `FORECAST`：分析师、第三方或模型预测；
- `PLAN`：主体已经表达但尚未完成的计划；
- `RUMOR`：未经确认的报道或市场传闻；
- `DENIAL`：主体明确否认；
- `SCHEDULED`：已有正式时间安排但尚未发生；
- `ONGOING`：当前持续中的状态或过程；
- `EXPECTED`：有依据的预期，但不属于正式guidance或明确forecast；
- `HYPOTHETICAL`：条件情景或假设；
- `UNKNOWN`：无法可靠判断。

`PLANNED/RUMORED/DENIED`作为兼容别名保留在Schema中；新Fact和被实质修订的Fact分别规范为`PLAN/RUMOR/DENIAL`。不得仅因文章语气使用`EXPECTED/HYPOTHETICAL`替代更准确的业务状态。

### 5.5 `subject_time`

明确以下规则：

- 与Event occurrence完全相同且没有独立对象期：`SAME`；
- 没有对象期且不适用：`null`；
- 财务期优先`FY2026-Q3`等稳定形式；
- 日期、月份、年份和区间复用Canonical时间写法；
- `CURRENT/FUTURE/NEAR_TERM/LONG_TERM`不得作为默认占位值；只有命题本身明确使用该语义且无法更精确表达时才能保留；
- 若`subject_time`等于`occurred_at`，必须改为`SAME`。

### 5.6 双摘要

明确区分：

- `canonical_summary`：1至2句，说明发生了什么及主要业务结果，不重复枚举全部Fact；
- `known_event_summary`：服务W1识别，必须尽可能保留日期、主体、动作、阶段、关键数字、期限、机构和新旧限定；
- 二者不得机械复制；`known_event_summary`与title相同只适用于确实没有更多可区分信息的简单Event。

### 5.7 重要性和Reference View

初始化和增量统一使用同一套规则：

- `is_important`判断长期、价格分析或预期变化意义，覆盖业绩/指引、资本配置、重大合同、产品/产能、融资、并购、监管、诉讼、管理层、供需变化和其他可能改变预期的occurrence；
- `include_in_reference_view`判断当前对D2及未来D3是否仍有用；近期信息、演进中事项和仍影响前瞻预期的Event通常进入；陈旧、已完成、被替代的例行Event可以退出；
- 两个字段分别判断，不互相复制；
- 不设置人为比例配额，但非空初始化库出现“全部false”时必须触发重新检查，不能直接提交。

### 5.8 关系字段

补充方向和使用条件：

- `related_event_ids`：相关但没有替代、派生方向的独立milestone；不要求自动对称，但同一Bundle中应保持关系意图一致；
- `supersedes_event_id`：当前Event取代或更新其指向的旧Event；方向为“当前→被取代Event”；
- `derived_from_event_ids`：当前Event由错误合并Event拆分或从既有Event派生；方向为“新/拆分Event→来源Event”；
- ongoing matter应优先建立milestone Event并通过关系连接，不做无限膨胀Event；
- merge/split必须同时说明稳定ID保留、retirement和关系的组合方式。

### 5.9 Event status与retirement

按Schema解释：

- `ACTIVE`：本版本正常Published Event；
- `MERGED`：该对象在本次Bundle中被合并到redirect目标；
- `SUPPRESSED`：该对象因无效或不应继续active而退出Published active集合；
- `MERGED/SUPPRESSED`不是`KEEP_PENDING`的替代；只适用于既有或同Bundle内明确创建并退休的Canonical对象；
- retirement reason与场景固定映射：
  - merge：`MERGED_DUPLICATE_OCCURRENCE`；
  - invalid suppression：`SUPPRESSED_INVALID_OCCURRENCE`；
  - split后原对象指向主要successor：`SPLIT_TO_SUCCESSOR`。

## 6. 阶段Skill修改

### 6.1 Survey

- 保留全局occurrence地图职责。
- analyst institution规则改为先判断共同催化剂和有限窗口。
- 时间规则使用新的Canonical写法。
- `delta_catalog.json`内每个D#必须恰好出现一次。
- 给`KEEP_PENDING_*` key规定最小结构和允许原因，不把key误当正式Delta resolution。

### 6.2 Wave

- 不再写“Reconciliation owns flags”后让Wave统一使用false占位。
- 方案A为首选：Wave必须做真实的初步flags判断，Reconciliation再独立复核；
- Wave草稿仍使用完整Canonical Event Revision Schema，不引入nullable flags或第二套Draft Schema；
- `wave_index.json`增加明确inline contract或Frozen Schema，记录assigned Delta、draft path、candidate key和unresolved recommendation；
- 每个Fact执行`subject_time/assertion_state/event_type/time`规范化。

### 6.3 Global Reconciliation

保留合并承担Canonical Edit的阶段设计，但增加强制逐Event终审清单：

1. occurrence identity与跨wave merge/split；
2. `event_id/ticker/status`；
3. title与规范`event_type`；
4. `occurred_at`和precision一致性；
5. 每个Fact的稳定/临时ID、最小命题、assertion state、subject time及Delta绑定；
6. `canonical_summary`与`known_event_summary`的不同用途；
7. 独立重判`is_important`；
8. 独立重判`include_in_reference_view`，不得继承Wave占位值；
9. related/supersedes/derived关系与retirement；
10. `price_analysis=null`；
11. 每个Delta恰好一个处置；
12. Bundle manifest身份和全部子文件wire字段。

提交前强制执行异常复核：

- Event非空但important数为0；
- Event非空但reference数为0；
- date-specific Event仍大量`UNKNOWN`；
- 同义或大小写混乱的event type；
- title重复；
- 单Event Fact数异常大；
- 全库关系数为0；
- `subject_time == occurred_at`但没有使用`SAME`。

这些检查是复核触发器，不是机械比例配额。

### 6.4 Incremental Candidate Map与Edit

- `target_suggestion_ids`明确为非约束提示。
- Candidate Map必须高召回，且不能因索引中没有明显候选就断言新Event。
- Incremental Edit使用与初始化相同的analyst episode、时间、event type、assertion和subject time规则。
- 修改既有Event时完整保留未受影响Fact、flags、关系和price analysis；Reference Review再终审flags。

### 6.5 Reference Review

以当前skill的Detail读取口径为准，并改写时间字段说明：

- 模型只判断`is_important`、`include_in_reference_view`和可选短`note`；
- `reviewed_at/review_mode/candidate_reason/changed/next_review_at`由task给出的确定性结果逐字使用，不允许模型重新计算；
- explicit candidate始终允许读取完整Detail；
- implicit candidate可先用index，必要时读取Detail；
- review-only run中全部候选都属于合法Detail访问集合；
- flag变化时必须输出完整stable-ID Event revision；没有变化时只记录review decision，不创建空V+1。

### 6.6 Revision Bundle Repair

补全小型返回契约：

- `status=BUNDLE_READY`；
- `stage=BUNDLE_VALIDATE`；
- 当前attempt的bundle path；
- Frozen base version；
- 从最终Bundle重新计算的Delta coverage；
- `validation=NOT_RUN`，因为确定性validator在模型turn结束后运行。

Repair只能修改validator指出的范围；不能借repair重写全部语义或改变已经合法的Event。

## 7. D1上下文说明

在`AGENTS.md`和初始化skill中说明：

- 读取Frozen View内本地复制的C1/C3/C5正文及其hash manifest；
- D1用于理解主体、业务背景、关系、未来关注点和潜在边界；
- D1不能单独支持Canonical Fact；Fact必须由本轮Delta支撑；
- D1和Delta冲突时以Delta为正式事实输入，无法裁定则`KEEP_PENDING`；
- D1 citation alias、Source或Observation ID不得进入Canonical Event/Fact。

---

# 第二部分：确定性程序、Schema物化与编排修改

## 8. Runner与attempt seeding

修改：

- `src/doxagent/workflows/codex_event_library/remote_runner.py`
- `src/doxagent/workflows/codex_event_library/context.py`
- `src/doxagent/workflows/codex_event_library/runner.py`

要求：

1. 收敛两套seeder，避免generic runner默认foundation、remote runner只注入stage skill的漂移。
2. seed时确定性组合foundation与stage skill，组合内容和hash写入不可变attempt输入。
3. `task.json`增加：
   - `content_input_order`；
   - `allowed_event_detail_ids`；
   - `reference_review_policy`；
   - `deterministic_review_fields_by_event`；
   - `frozen_as_of`；
   - `required_bundle_identity`；
   - 当前阶段必读Frozen文件和prior work路径。
4. Runner校验每个`O2RunResult`：
   - stage必须等于当前phase；
   - base必须等于Frozen base；
   - intermediate不得返回bundle path；
   - final/repair bundle path必须位于当前attempt的`output/revision_bundle/`；
   - coverage total与assigned或全batch范围一致；
   - 模型阶段`validation`必须为`NOT_RUN`。
5. 对错误结果立即标记attempt失败并保持Published不变，不把不可信run result写成成功审计。

## 9. Frozen View与完整Schema物化

修改`src/doxagent/event_library/compiler.py`，在Frozen View中至少提供：

```text
schemas/
  canonical_event_revision.schema.json
  revision_bundle_manifest.schema.json
  event_retirement.schema.json
  residual_delta_resolution.schema.json
  reference_review_decision.schema.json
  candidate_map.schema.json
  survey_delta_catalog.schema.json
  wave_index.schema.json
  schema_index.json
```

要求：

- 不再把manifest-only schema命名成容易误解的完整`revision_bundle.schema.json`；若保留旧名，必须在title/description中明确它只描述manifest。
- Pydantic Field增加业务`description`，不能只提供类型和枚举名。
- `schema_index.json`说明每个文件、格式（JSON/JSONL）、是否必需和对应Schema。
- residual正式字段只有`resolution`；`disposition`继续只作为兼容读取别名，新的Agent Bundle出现别名应产生质量警告。
- 提供一个最小、非MU专属的Bundle正例，覆盖新Event、既有Event、retirement、duplicate、pending和review decision；正例是Frozen只读参考，不参与业务输入。

## 10. 以Schema为准修复validator/importer/repository

### 10.1 Event status

删除validator中“Event revision必须ACTIVE”的额外硬编码。按Schema支持：

- `ACTIVE`正常写入active revision；
- `MERGED/SUPPRESSED`走确定性生命周期处理，不静默跳过；
- 非ACTIVE revision必须具备与retirement/redirect一致的语义，否则返回明确业务错误，不把整个Event悄悄转Pending；
- Published active集合只暴露`ACTIVE`对象，稳定旧ID仍可解析到状态/redirect。

### 10.2 Temporary retirement source

Schema允许`E#`或`T#`作为retirement ID，因此validator必须支持：

- `E#`：必须存在于base Published；
- `T#`：必须是同一Bundle中的Event revision，Importer先分配稳定ID，再在同一事务内执行retirement；
- 不存在于base或本Bundle的ID才是非法source。

### 10.3 生命周期组合校验

新增：

- 同一Event同时revision和retirement时，状态、reason和redirect必须一致；
- merge/split redirect目标必须存在且不会在同一版本变成不可解析对象；
- `related/supersedes/derived`的目标和方向满足Schema及无环约束；
- 不允许通过局部warning静默丢失一个Schema合法、可确定性处理的生命周期操作。

## 11. Reference Review确定性时钟

修改：

- `src/doxagent/event_library/reference_review.py`
- `src/doxagent/event_library/repository.py`
- `src/doxagent/event_library/validator.py`
- Frozen View/task编译逻辑

要求：

1. 所有review计算使用Frozen `as_of`，不得以`datetime.now(UTC)`代替。
2. 确定性程序根据occurrence anchor、Frozen as_of、当前include状态和last review计算：
   - `reviewed_at`；
   - `review_mode`；
   - `candidate_reason`；
   - `next_review_at`；
   - `changed`。
3. task向模型提供这些只读结果和10/30/7规则版本，例如`reference-review-policy-v1`。
4. Agent只决定两个flags和短note；Bundle组装/validator用确定性结果填充或校正完整`ReferenceReviewDecision`。
5. Validator检查：
   - review event ID存在或是同Bundle新Event；
   - 同一run、同一Event只有一个decision；
   - reviewed_at等于Frozen as_of；
   - mode/reason/next time等于确定性计算；
   - `changed`等于旧值与新值比较；
   - flag发生变化时存在完整Event revision，未变化时不得产生无意义revision。
6. review-only run的Bundle必须使用空`delta_batch_ids`；Frozen View即使由空DeltaBatch建立，也要在task中明确最终Bundle不得携带该空batch。
7. review-only只有review history/schedule变化时不创建V+1；有flag变化且存在Event revision时才发布新版本。

## 12. Event Detail访问集合

Runner确定性生成：

```text
allowed_event_detail_ids =
  candidate_map.detail_event_ids
  ∪ explicit_reference_review_candidate_ids
  ∪ 本阶段允许复核的implicit_candidate_ids
```

规则：

- Candidate Map阶段仍禁止打开Detail；
- Incremental Edit可打开Candidate Map列出的Detail；
- Reference Review可打开全部显式候选，隐式候选按skill按需打开；
- review-only不依赖不存在的Candidate Map；
- 文件系统或Workspace读取层应执行同一访问集合，不能只在task中写标签而不校验。

## 13. D1正文提升到O2 Frozen View

修改初始化总编排和Frozen View编译：

```text
upstream/
  o2_upstream_context_manifest.json
  d1/
    c1.md
    c3.md
    c5.md
    artifact_manifest.json
```

要求：

1. 只复制D1已经Published且在`O2UpstreamContextManifest`中声明的C1/C3/C5正文。
2. 从D1 workspace读取后校验ArtifactRef的run ID、relative path、size和SHA-256。
3. 复制到O2 Frozen View后重新记录本地相对路径和hash；O2不需要跨run读取权限。
4. 内容和manifest参与Frozen View identity hash，恢复时不得静默变化。
5. entity relations、future nodes和citation manifest继续保留，但Prompt明确其上下文边界。
6. Artifact不可读、hash不符或未Published时，在启动O2前失败，不让模型依据残缺D1上下文继续初始化。

## 14. Canonical字段语义校验与质量门槛

在结构validator之后增加独立semantic validation/report，不把所有问题混成Schema错误。

### 14.1 可确定性硬校验

- `occurred_at`与precision格式一致；
- `subject_time == occurred_at`时要求`SAME`；
- 新写入event type符合冻结词表；
- assertion state是Schema合法值，并对兼容别名产生规范化提示；
- Bundle identity严格等于Frozen task；
- relationship/retirement生命周期一致；
- Reference Review确定性字段一致。

### 14.2 必须触发模型复核或阻止语义放行的异常

- 非空初始化库important数为0；
- 非空初始化库reference数为0；
- date-specific类型中`UNKNOWN`比例超过冻结质量阈值；
- 重复title或同occurrence高相似重复Event；
- 单Event Fact数超过审查阈值；
- 具有明显ongoing/milestone数据但全库关系为空；
- event type出现未注册近义词。

阈值只触发复核和质量失败，不要求人为制造固定比例的important/reference Event。

### 14.3 修正空集合指标

`important_ids`为空时：

- `reference_important_event_recall`不得返回`1.0`；
- 改为`null/UNDEFINED`，或同时输出`important_event_count=0`并令release gate失败；
- 质量报告必须同时显示important/reference绝对数量。

## 15. Published视图时间排序

Known Event Index和Reference View不再直接按`occurred_at`原始字符串排序。

统一计算：

```text
occurrence_sort_anchor
```

规则：

- TIMESTAMP/DAY使用对应日期；
- MONTH使用月末；
- QUARTER使用季末；
- YEAR使用年末；
- INTERVAL使用区间结束时间；
- UNKNOWN放在有时间Event之后，再按Event ID稳定排序；
- 显示仍保留Canonical `occurred_at`，排序anchor不新增为Canonical业务字段。

Reference Review的`occurrence_anchor`复用同一解析器，避免视图排序和review clock采用两套时间解释。

## 16. 测试修改

新增或强化：

### 16.1 Prompt/上下文契约测试

- 每个attempt的`skill.md`同时包含foundation和当前stage；
- AGENTS与阶段skill没有Web Search冲突；
- analyst response episode规则在四个相关skill中一致；
- 完整Schema索引和全部Bundle子Schema存在；
- D1正文被复制且hash一致。

### 16.2 Runner结果测试

- stage/base/coverage/path/validation任一不符时attempt失败；
- intermediate不能返回Bundle；
- repair必须返回当前repair attempt路径和`NOT_RUN`。

### 16.3 Schema/validator测试

- `ACTIVE/MERGED/SUPPRESSED`按Schema得到确定性处理；
- T# retirement source在同Bundle存在时合法，不存在时失败；
- lifecycle、redirect、relationship一致性；
- residual只接受正式`resolution`作为新写wire。

### 16.4 Reference Review测试

- 第29/30天、10天、7天和`TIME_UNRESOLVED`边界；
- 全部时间使用Frozen as_of；
- explicit/review-only Detail可读；
- changed与Event revision一致；
- review-only无Delta、无flag变化时不创建V+1。

### 16.5 语义fixture与真实验收

- 同催化剂多机构analyst response可合并；
- 新thesis/新日期analyst action正确拆分；
- earnings occurrence time与FY subject period分离；
- `SAME`、双摘要、event type和assertion规范；
- importance/reference非空且无比例配额；
- ongoing matter形成milestone关系。

## 17. 实施顺序

1. 冻结本方案中的字段语义、event type词表和Reference Review policy version。
2. 修复prompt组合与AGENTS，完成全部阶段skill改写。
3. 补齐Schema物化、description和Bundle最小正例。
4. 修复Runner对O2RunResult、路径和Detail访问集合的校验。
5. 按Schema修复status、retirement及生命周期导入。
6. 将Reference Review时钟收回确定性程序并补task规则。
7. 将D1正文提升到O2 Frozen View。
8. 修复时间解析、Published排序、semantic report和空集合指标。
9. 运行聚焦单元/集成测试。
10. 复用现成MU冻结Runtime Snapshot、742 Delta和185 Runtime Package启动新的O2 run，形成隔离的新Event Library V1；不重跑CDECR，不覆盖旧V1。

## 18. 完成标准

只有同时满足以下条件，本修复才算完成：

- 每个真实attempt均能证明foundation已注入；
- Reconciliation逐字段终审清单在workspace产物和最终Bundle中得到验证；
- analyst response episode符合开发计划；
- AGENTS与各阶段Web Search规则无冲突；
- 所有Bundle文件都有可读Schema和字段说明；
- Schema合法status/retirement不再被validator额外拒绝；
- Reference Review Detail权限、10/30/7 task规则和确定性时间闭环；
- O2可读取校验后的D1 C1/C3/C5正文；
- event type、occurrence time、assertion state和subject time达到规范化要求；
- important/reference不再出现未经复核的全零发布；
- Known Event Index按真实时间anchor排序；
- 空important集合不再得到1.0形式满分；
- 新MU真实O2验收通过结构、事务、幂等和独立语义质量门槛；
- 原CDECR Registry、Frozen Runtime Snapshot、旧Bundle和旧Published V1均保持不变。
