# W3 user prompt 字段删减方案（过程版本，2026-10-13）

> 本文主要明确当前输入中要删除、合并和保留的内容。删减后的组织方式只是供继续讨论的过程版本，不是最终待实现 schema，也不是实施授权。后续可以继续调整和扩充业务字段，不能把本文的字段数量、命名或嵌套形式视为最终约束。本轮仅输出方案文档，不修改 W3 代码、prompt、输出合同或部署。

## 1. 修订结论与业务尺度

W3 要在短时间内判断消息事实的新旧、已有 Policy 的覆盖和执行关系，以及未覆盖事实相对既有预期的交易含义。减负应优先删除流水标识、执行版本、审计包装和重复表达，保留形成判断所需的业务信息。

上一版把部分必读业务信息也压成 ID 或改为另行查找，删减尺度有误。本版作以下修正：

1. W1、W2 的 `confidence` 和 `reason` 保留，不改成只有低置信时才出现的标记，不额外摘要或截断理由。
2. W1 引用 Event 直接展开为至少 **Event ID + 事件时间 + event summary**；不能只留下 ID，让 W3 先做一次查找才能了解 W1 的依据。
3. W2 只保留一份判定，但保留完整业务字段，并直接提供命中和相关 Policy，以及每个命中和相关 Condition 的完整业务内容。
4. Policy 消费与 watch 只小幅删除审计字段、重复字段，保留当前业务状态和时间信息，不改造成极少数异常摘要。
5. `first_round_tags` 标记为 **整体改造，另案设计**。本文不确定其未来结构，也不把它列入直接删除清单。
6. 公共业务上下文的**描述信息**整合为“名字 + 截止时间”；D1/D2、Policy 和已知事实的业务正文继续可用。不能把“描述信息只保留两个字段”解释成“公共业务内容也只剩两个字段”。

核心原则是：**W3 必定要看的、且已经由前序节点定位好的内容，直接组装给它；没有业务意义的包装不进入模型上下文。**

### 字段设计原则

- **以短时间完成交易判断为目标。** 字段要服务事实新旧、Policy 覆盖与执行、预期差和经济传导；极少使用、缺失后基本不影响任务的内容，默认不常驻注入。不为假设中的需求堆叠字段、解释或防御性包装。
- **必读业务信息直接给足。** 前序节点已定位的 Event、命中及相关 Policy/Condition 应直接提供可理解的业务内容，不能只给 ID 再让 W3 查找。省下字段但增加必经读取，不算有效简化。
- **先删重复和审计，再判断业务价值。** 同一判定只给一个版本；流水 ID、执行版本、过程记录和重复元信息由后端保存。Event、Policy、Condition 等实际参与判断和结果引用的 ID 保留，但 ID 不能代替正文。
- **保留判断依据和决定执行的业务状态。** W1/W2 的 confidence、reason，以及 Condition 的方向、范围、触发标准和 calibration 保留；消费与 watch 只小幅去除审计和重复字段，不把正常状态、原因或有效期压没。
- **用简单结构表达已有语义。** 对齐结论可压成一两个是/否，上下文描述只给名字和截止时间；不为删减另建摘要模型、状态机或阻塞校验，也不机械截断正文、混淆时间精度和 HIT/RELATED 含义。
- **按当前 Case 控制信息量，允许后续扩充。** 必读内容直接注入，其他研究材料保留清楚的读取入口，不要求每次全文读取。本文是删减基线；后续新增字段仍按实际决策价值取舍，不受本版字段数和嵌套方式约束。

## 2. 当前实现与本方案的边界

当前 `w3.py` 将 18 个顶层字段组装成 `task`，同时写入 `task.json` 并拼进 user prompt；外层指令又要求读取 `task.json` 和四份公共业务上下文文件。W1 引用、W2 命中归因主要仍以 ID 表达，Policy 正文位于独立文件。

本方案针对 **模型可见输入的投影与组装**。删除模型输入字段，不等于删除 Runtime 内部状态、冻结输入、回执、数据库字段或审计记录。业务引用 ID 与纯流水 ID 分别处理：

| 类型 | 本版处理 | 原因 |
| --- | --- | --- |
| Case、消息、采集、消费记录等流水 ID | 从模型输入删除 | 不参与业务研判，关联由执行层负责 |
| Event ID | 保留，并附时间和摘要 | 支持新旧判断与结果引用 |
| Policy ID、Condition ID | 保留，并附完整业务内容 | 支持命中归因、执行选择、消费和 watch 操作 |
| Policy `ref`、旧版 `source_refs` | 保留业务引用 | 是理解预期来源与传导的定位信息，不按流水 ID 删除 |

本文依据本会话前序排查及本轮本地代码复核；本轮没有重新检查远端部署。对当前代码的描述以本地文件为准，不据此宣称远端已采用本文方案。

## 3. 顶层 18 个字段的处理清单

| 当前字段 | 本版处理 | 具体说明 |
| --- | --- | --- |
| `w3_case_id` | 删除模型可见值 | 后端保留 Case 关联；外层指令中的同一流水 ID 也应一起处理，避免只删 JSON 却继续重复注入 |
| `runtime_case_id` | 删除 | 不影响业务理解，后端继续关联 |
| `mode` | 保留 | 决定重判与独立研判的任务范围 |
| `ticker` | 保留 | 明确交易判断对象 |
| `cutoff_at` | 保留 | 明确当前 Case 的信息截止边界 |
| `event_boundary_at` | 删除重复字段 | 消息发布时间在 `source_message.published_at` 保留；原有回退规则可在执行层处理，不重复输出同一时间 |
| `source_message` | 保留业务投影 | 保留正文、标题、发布时间、来源链接，删除采集与流转包装，详见第 4 节 |
| `original_w1` | 保留并展开 Event 引用 | 保留判定、置信度、完整理由；引用不再只给 ID，详见第 5 节 |
| `original_w2` | 删除重复版本 | 其业务信息由唯一 W2 判定承接，不再并列两套 W2 |
| `first_round_policy_assessment` | 作为唯一 W2 判定保留并补齐业务正文 | 保留现有业务字段，附命中与相关 Policy/Condition 的完整内容，详见第 6 节；最终字段名未定 |
| `w2_alignment` | 压缩为一至两个是/否结论 | 表达 HIT、RELATED 是否有已确认的新事实支持，不展开过程性逐项对齐记录，详见第 7 节 |
| `first_round_tags` | **整体改造，另案设计** | 本版不作直接删除决定，不定义新标签，不推导其后续路由语义；另案明确前不据本文删除当前字段 |
| `orchestration_revision` | 删除模型可见值 | 执行层选择对应合同，W3 读取当前实际任务与输出要求即可 |
| `initial_route_reason` | 删除 | 重复转述 W1/W2 判定及置信度；业务理由保留在 W1/W2 `reason` 中 |
| `policy_usage` | 保留，仅小幅删减 | 不删除整块、不只给异常；详见第 8 节 |
| `policy_watch_followup` | 保留，仅小幅删减 | 保留原因、开始语义日和到期时间；详见第 9 节 |
| `context_version_pin` | 删除模型可见版本指纹，整合上下文描述 | 运行 ID、版本号留在后端；模型看到上下文名字和实际截止时间 |
| `document2_publication_state` | 删除模型可见状态标签 | 当前可用业务内容仍提供；本文不改变发布、加载或可用性规则 |

删除 Case ID 有一个明确的后续接口影响：当前输出 schema 和外层 prompt 要求模型原样返回 `w3_case_id`。实施时需要由执行层补入该关联值，并同步处理模型输出合同，不能仅删除输入后继续要求模型回填未知 ID。本文只标出这一依赖，不据此实施输出合同改造。

## 4. `source_message`：删采集包装，留消息本身

### 4.1 保留内容

| 字段/信息 | 简要说明 | 处理 |
| --- | --- | --- |
| `published_at` | 原消息发布时间，区别于 Case 截止时间 | 保留；保持真实时间精度 |
| `snapshot.title` | 消息标题 | 保留内容，移入扁平消息对象；最终字段名未定 |
| `snapshot.body` | 消息正文 | 保留内容；不因省 token 截掉可能改变判断的事实、限定词或时间表述 |
| `url` | 原文定位及来源判断 | 保留；确实缺失时不制造新阻塞 |

`published_at` 与顶层 `cutoff_at` 含义不同，不能互相替代。事件发生时间、计划时间和消息发布时间也不能混为一谈；正文中的这些业务时间继续保留。

标题与正文只有在内容完全重复、没有新增信息时才去重，不进行语义摘要替换。

### 4.2 删除清单

| 当前字段 | 删除理由 |
| --- | --- |
| `admission_context` 整体 | 属于消息准入和抓取窗口管理，不是交易边界 |
| `admission_context.policy_version`、`mode`、`sweep_id`、`source_task_id`、`window_start`、`cutoff` | 随容器整体删除；这里的准入 policy 版本不是交易 Policy |
| `source_message_id` | 消息流水 ID |
| `source_id` | 采集适配器标识；不能替代真实发布者信息，来源判断由原文及链接承载 |
| `binding_id` | ticker 与采集源绑定标识 |
| `collected_at` | 采集过程时间 |
| `normalized_at` | 标准化过程时间 |
| `message_bus_event_time` | 与顶层 Case 截止边界重复的消息流时间 |
| `stream_item_id` | 消息流条目标识 |
| `member_count`、`member_message_ids` | 聚合与去重记录；不等同于独立来源证实数量 |
| `logical_message_id`、`business_version`、`update_kind`、`previous_raw_message_id` | 消息版本链和采集更新记录 |
| `eligibility_at` | 系统处理资格时间 |
| `snapshot` 外壳及其中的 `ticker` | 标题、正文直接提供；ticker 已在顶层给出 |
| 独立的 `publication_time_basis` | 通过发布时间值本身表达日期/时间精度，不再额外携带采集枚举 |

删除 `publication_time_basis` 的前提是没有丢失它表达的业务区别：只有日期时用日期，确切时间才用时间戳，未知发布时间不伪装成精确时间。若现有序列化无法保留这个区别，应先修正表示方式，不能把默认零点当成真实精确发布时间。

## 5. W1：保留判定、置信度、理由，直接展开已定位 Event

本版撤回“UNCOVERED_NEW 模式下整块省略 W1”的建议。W1 的判断依据直接提供给 W3，不让它仅凭模式名猜测前序判断。

| 当前字段 | 本版处理 | 说明 |
| --- | --- | --- |
| `result` | 保留 | `NEW` / `OLD` |
| `confidence` | 保留原值 | `normal` / `low`；不改成仅低置信时出现的字段 |
| `reason` | 保留完整内容 | 不压缩为简短 note，不额外调用模型改写理由 |
| `reference_ids` | **展开引用对象，替代裸 ID 列表** | 每个引用至少提供 ID、事件时间、event summary；最终字段名未定 |
| `fact_attributions` | 删除裸 Fact ID 归因包装 | 不再单独传 `{event_id, fact_ids}` 审计式清单；被引用事实中影响研判的内容须进入对应 Event 的业务内容 |

每个已引用 Event 至少直接组装：

| 信息 | 含义 | 取值原则 |
| --- | --- | --- |
| Event ID | 已知事件身份，供最终引用和进一步定位 | 使用当前 Case 绑定版本中的真实 ID |
| 事件时间 | 已知事件的发生时间 | 保留原有时间精度；不能换成数据库写入时间 |
| event summary | 事件已知现实的摘要 | 优先使用已存储的 `canonical_summary`，不新增一次模型摘要调用 |

**ID + 时间 + summary 是最低内容要求，不是内容上限。** 若 W1 的新旧判断依赖 Event 内的具体 Fact，而 summary 没有承载该事实，应把相关 Fact 的业务正文一起组装，包括影响判断的发生时间、主体时间、命题及现实状态。不能删掉 Fact ID 清单后，连真正支撑判断的事实也省略。

同一 Event 在输入中提供一份即可，不需要同时放裸 ID 列表、引用对象列表和另一份重复摘要。已引用内容从当前 Case 的固定上下文中解析；当日 provisional Event 也直接带上已有业务内容，不让 W3先经历无谓查找。本文不新增引用缺失时的阻塞校验或研究阶段。

## 6. W2：一份完整判定，附完整相关业务对象

### 6.1 合并的是重复版本，不是业务信息

删除 `original_w2`，保留一份以当前 `first_round_policy_assessment` 为基础的 W2 判定。以下现有业务字段全部保留：

| 字段 | 简要解释 |
| --- | --- |
| `policy_hit` | 是否满足已有 Policy 的触发条件 |
| `policy_related` | 是否与已有 Policy 相关，但相关关系本身不授权执行 |
| `policy_ids` | 已命中的 Policy ID 列表 |
| `hit_condition_ids` | 每个命中 Policy 下实际命中的 Condition 归因 |
| `matched_condition_ids` | 相关 Policy/Condition 归因；保留当前 RELATED 语义，不混同 HIT |
| `confidence` | W2 置信度，保留原值 |
| `reason` | W2 判定理由，保留完整内容 |

本版不把 W2 改成仅两个布尔值或一个短 note，也不为了去重而删除上述业务归因字段。只消除第二套 W2 版本及其重复解释。

### 6.2 直接附上命中与相关 Policy

按命中与相关归因的并集组装 Policy，不能只附 `policy_ids`，不能要求 W3 为了知道触发标准而先读整份 PolicySet。

当前 v2.1 原生 Policy 的业务字段保留如下：

| Policy 字段 | 简要解释 | 本版处理 |
| --- | --- | --- |
| `policy_id` | Policy 身份及业务操作引用 | 保留 |
| `title` | Policy 名称 | 保留 |
| `ref` | 指向研究基线、预期或相关业务来源的引用 | 保留 |
| `transmission` | 现实变化如何传导到目标 ticker | 完整保留 |
| `match_scope` | Policy 适用的对象、事实和范围边界 | 完整保留 |
| `activation_conditions` | 可独立触发的业务 Condition 集合 | 保留完整对象，不退化为 ID |

每个 Condition 的完整业务字段：

| Condition 字段 | 简要解释 | 本版处理 |
| --- | --- | --- |
| `condition_id` | 命中归因和执行选择使用的身份 | 保留 |
| `decision` | 该 Condition 对应的 `LONG` / `SHORT` 方向 | 保留；不能改用 Policy 顶层方向或默认 LONG |
| `trigger_layer` | `OUTER` / `MIDDLE` / `INNER` 触发层次 | 保留 |
| `criterion` | 所监测的现实变化标准 | 完整保留 |
| `calibration.reference_state` | 该条件对应的既有现实/预期锚点 | 完整保留 |
| `calibration.trigger_boundary` | 超过既有锚点、构成触发的边界 | 完整保留 |

命中和相关 Condition 均遵守这一要求，不能只保留命中项而省略相关项的 calibration。作为本版过程安排，直接附上被定位 Policy 的完整业务对象及其 `activation_conditions`，避免因只摘一条 Condition 丢失同一 Policy 下的方向与边界关系；这里没有要求把整份无关 PolicySet 全文常驻拼入 user prompt。

同一 Policy 同时命中和相关时，正文只提供一次，通过现有归因列表区分具体 Condition 的 HIT/RELATED 状态，不复制两份完整 Policy。

旧合同 Case 仍保留其已有业务字段，包括 `source_refs`、Policy 层 `decision`、`match_scope`、Condition 的 `criterion` 和完整 `calibration`；不能为统一展示而编造旧合同不存在的 `transmission` 或 Condition 方向。本文不设计新的跨版本合同。

### 6.3 W2/Policy 中可删的包装

删除的是随完整发布对象带入的 `schema_version`、`workflow_version`、发布/运行版本号、artifact ID、SHA、输入 manifest 和审计型发布记录，不是上述业务正文及其引用。

`PolicySet.as_of` 这类实际业务截止时间在统一上下文描述中保留；Condition 的现实锚点和触发时间表述留在业务内容中。不要把 Policy 业务引用 `ref` 与发布审计对象 `document2_ref` / `event_library_ref` 混为一类整体删除。

## 7. `w2_alignment`：压缩过程记录，不代替 W2 业务归因

按原删减方向，仅提供一至两个是/否结论，例如：

| 过程性字段示意，名称未定 | 含义 |
| --- | --- |
| `hit_has_new_fact` | HIT 归因中是否存在已确认的新事实支持 |
| `related_has_new_fact` | RELATED 归因中是否存在已确认的新事实支持 |

删除逐项 `condition_results[{condition_ids, is_new}]` 展开，W2 原有 Condition 归因及其完整业务内容继续保留。

两点语义要明确：

- `false` 仅表示未确认存在支持性新事实，不能解释为消息必然为 OLD。
- 聚合为 `true` 也不表示每一个命中 Condition 都由新事实支持。W3 仍依据消息、Event 和具体 Condition 判断本次执行选择，不能凭总体布尔值给所有 Condition 授权。

没有运行对齐时不伪造 `false`，可省略这块。本文不扩展第三套对齐状态机。

## 8. `policy_usage`：维持当前业务结构，小幅删减

本版撤回“只留异常方向摘要、去掉常规状态行”的建议。继续提供当前冻结的 Policy 消费快照，包括正常可执行状态；不改变快照覆盖范围，也不因节省上下文把业务状态裁掉。

| 当前字段 | 本版处理 | 理由 |
| --- | --- | --- |
| 外层 `policy_id` | 保留 | 关联 Policy |
| `directions` | 保留 | 保持当前按方向描述消费状态的结构 |
| `directions[].direction` | 保留 | LONG/SHORT 分开消费 |
| `directions[].consumed` | 保留 | 当前方向是否已消费 |
| `executable_directions` | 保留 | 当前实际可执行方向；不能简单从 `consumed=false` 自行推导 |
| `consumed_directions` | 保留 | 直接呈现已消费方向，本版不继续压缩 |
| `directions[].ticker` | 删除 | 与顶层重复 |
| `directions[].policy_id` | 删除 | 与外层重复 |
| `directions[].usage_generation` | 删除模型可见值 | 消费代际由 Runtime 校验和维护 |
| `directions[].consumption_id` | 删除模型可见值 | 消费记录流水 ID，W3 不需要查询该记录才能决策 |

保留 `executable_directions` 很重要：当前代码存在未解决迁移状态使该列表为空的情况，不能用“未消费”直接重建它。本文保持现有明确业务结论，不新增迁移诊断字段，也不要求 W3 查底层账本。

## 9. `policy_watch_followup`：保留原因与有效期，只删流水关联和重复 ticker

| 当前字段 | 本版处理 | 简要解释 |
| --- | --- | --- |
| `policy_id` | 保留 | watch 所指向的 Policy |
| `reason` | 完整保留 | 上次 watch 的业务原因及当前需要跟进的事项 |
| `start_semantic_day` | 保留 | watch 起始语义日，帮助理解持续时间 |
| `expires_at` | 保留 | 当前 watch 到期边界 |
| `ticker` | 删除 | 顶层已经提供 |
| `source_case_id` | 删除模型可见值 | 原始 Case 流水引用，后端继续保留和校验 |

继续提供当前实现选出的有效 follow-up 行，不把它们缩成一个 `watch_reason`，不把有效期隐藏给 W3。固定到期、不可滚动续期等现有业务规则保留，本文不改写 watch 生命周期。

## 10. 公共业务上下文：统一描述为名字和截止时间

将重复的版本 pin 和分散发布描述整合成一个上下文列表。**每项描述只提供名字和截止时间**，例如 D1、D2、PolicySet、已知事件/事实视图各一项；字段命名待后续确定。

| 信息 | 本版处理 |
| --- | --- |
| 上下文名字 | 保留，能让 W3识别对应业务材料 |
| 上下文业务截止时间 | 保留，使用材料实际 as-of/cutoff，不能拿发布或抓取时间冒充 |
| `document1_run_id`、`document2_run_id` | 删除模型可见值 |
| `event_library_version`、`policy_set_version` | 删除模型可见值 |
| schema/workflow 版本、artifact ID、哈希、来源 run、路径清单 | 从模型可见描述中删除，执行层保留 |
| `input_manifest` 以及下载、交接、失败过程信息 | 不作为业务正文注入 |
| `shell_outcomes` 等生成过程记录 | 不作为业务正文注入 |
| `COMPLETE` / `PARTIAL` 等发布状态 | 不在 user prompt 另设标签；可用业务材料照常提供 |

业务正文的处理边界：

- D1 的公司/行业/市场研究、D2 的 State、expectation baseline、realization factors、potential gaps 继续作为公共业务上下文可读；本文不提出删除其中的业务字段。
- 已被 W1 定位的 Event 和被 W2 定位的 Policy/Condition 直接组装到当前 Case 输入，不能仅给文件名、ID 或查找提示。
- 完整 Reference View、完整 PolicySet 仍可用于 W3 重新判断和扩展查找；无需因已直接给出命中内容就把它们撤走，也无需每个 Case 都把全量内容再拼进 prompt。
- 文件读取入口仍需清楚给出，可在外层任务指令中集中提供；“上下文描述只含名字和截止时间”不等于让文件变得不可定位。
- 后端继续使用当前版本 pin 保证同一 Case 的一致性，模型无须承担版本和哈希核验。

删除整份 D2/PolicySet 原始文件中的字段会涉及其他消费者和发布合同，超出本文的输入投影范围。后续实施应处理 W3 可见投影，不能照本文直接改坏共享发布 schema。

## 11. 外层 prompt：去重复阅读，先给必读业务内容

外层文字保留任务模式、当前 Case 输入的权威性、对应 skill、业务材料入口和严格输出要求；删除重复的流水 ID、版本说明和路由复述。

本版建议：

1. 当前 Case payload 在 user prompt 中作为一个权威入口提供；即使后端保留 `task.json`，也不再要求模型把同一份 payload 重读一次。
2. W1 Event 和 W2 Policy/Condition 在 payload 中直接给足，避免为获取必读信息增加工具调用。
3. 取消“无论 Case 情况都先全文读取四份公共材料”的统一要求。W3 根据任务读取所需研究材料，已定位内容不再强制回头全文查找。
4. 保留输出 schema 和任务 skill 的读取要求；本文不顺带简化分析职责、必要业务规则或输出合同。

这不是要求 W3 无依据地下结论：重判新旧时仍需核对现实基线，未覆盖研判仍需恢复 Prior、Delta 和传导。目标是让必读内容直接可得，并取消重复与无关读取。

## 12. 删减落点汇总

### 12.1 明确删除

- 模型可见 Case 流水 ID：`w3_case_id`、`runtime_case_id`，以及外层指令重复值；需同步考虑输出关联方式。
- 重复时间：顶层 `event_boundary_at`，以及消息流与采集过程时间；消息真实发布时间和 Case 截止时间保留。
- 消息准入、采集、绑定、聚合、版本链等包装：第 4 节逐项清单。
- 第二份 W2：`original_w2`，业务内容统一由完整 W2 判定承接。
- 执行说明：`orchestration_revision`、`initial_route_reason`。
- 模型可见上下文版本指纹、发布审计描述及单独 `document2_publication_state`。
- W1 裸 Fact ID 归因清单；其中支撑判断的事实业务内容要保留在引用 Event 中。
- 消费快照中的重复 ticker/Policy ID、代际和消费记录 ID。
- watch 中的重复 ticker 和来源 Case 流水 ID。
- payload 在 prompt 和文件之间的强制重复阅读。

### 12.2 明确合并或重组

- W1 裸 `reference_ids` → 带 ID、时间、summary 及必要事实正文的 Event 引用。
- 两份 W2 → 一份完整判定，附命中/相关 Policy 与完整 Condition 业务正文。
- 逐项 alignment 过程记录 → 一至两个是/否结论，明确聚合语义边界。
- 公共上下文的分散版本/发布描述 → 统一的名字与截止时间列表。

### 12.3 明确保留，不按上一版继续削减

- W1 和 W2 的 `confidence`、完整 `reason`。
- W1 引用的已知 Event 业务信息。
- W2 的完整业务判定字段、HIT/RELATED 区别和归因。
- 命中及相关 Policy 的业务正文、业务引用、全部相关/命中 Condition 的完整字段。
- Policy 消费的方向、消费状态和可执行方向列表，以及正常状态行。
- watch 的业务原因、开始语义日和到期时间。
- 公共研究和事实材料的业务正文及可读入口。

### 12.4 单列待定

`first_round_tags`：整体改造，由用户后续另案给出方案。本版既不把它定为删除，也不提出新的标签体系。

## 13. 本文不作出的决定

本文没有确定最终字段名、顶层字段数量、JSON 嵌套方式、前端呈现、最终实现批次或新字段清单。可以继续补充对交易决策有价值的信息，不需要为了维持本版示意结构而拒绝扩展。

直接补齐 Event 和 Policy/Condition 业务正文，可能让单条 user prompt 比“只给 ID”的极简版本更长，这是有意保留的必要业务成本。当前没有进行删减后端到端评测，不能承诺具体 token 降幅、延迟改善或判断准确率不变；减少重复和查找是设计依据，效果需在最终方案确定后验证。

**本版交付物是一份可继续修订的字段删减基线，不是最终待实现 schema。**

## 14. 代码依据

- [W3 task 及外层 prompt 组装](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/persistent_runtime_v2/w3.py:417)：当前 18 项顶层字段、重复 W2、task 文件与 prompt 双重提供方式。
- [消息 Envelope](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/persistent_runtime_v2/schema.py:167)：消息采集、准入和流转字段。
- [W1 结果](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/persistent_runtime_v2/schema.py:274)：判定、置信度、引用、事实归因和理由。
- [W2 结果及 alignment](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/persistent_runtime_v2/schema.py:379)：完整业务判定及逐 Condition 对齐结构。
- [原生 Policy 与 Condition](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/workflows/codex_document3/schema_v21.py:92)：Condition 方向、trigger layer、criterion、calibration，以及 Policy transmission/ref。
- [旧版 Policy 业务结构](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/workflows/codex_document3/schema.py:115)：source_refs、Policy 方向和旧版 Condition。
- [已知事件视图](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/event_library/compiler.py:191)：Event 时间、精度、摘要及事实正文。
- [Policy 消费快照](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/persistent_runtime_v2/policy_consumption.py:27)：方向状态、消费代际、消费 ID 和实际可执行方向。
- [watch 快照选择](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/persistent_runtime_v2/policy_watch.py:14) 与 [watch 记录](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/persistent_runtime_v2/policy_watch.py:97)：有效期筛选及当前业务/关联字段。
- [W3 业务职责与执行规则](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/prompts/persistent_runtime_v2/w3/agent.md:1)：Case 重判、未覆盖预期差、Condition 执行选择和 watch 规则。
