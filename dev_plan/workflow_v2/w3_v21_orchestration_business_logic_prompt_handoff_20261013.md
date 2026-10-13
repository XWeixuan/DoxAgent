# v2.1 W3 编排与重构业务逻辑说明

用途：交给负责 W3 Skill/Prompt 重构的 ChatGPT 对话，作为从零设计新版提示词所需的业务上下文和接口约束。

日期：2026-10-13。

本文已纳入用户最新确认的 Case 标签和 OLD 入口调整；与勘察描述不一致时，以这里明确说明的新版目标为准。本文不编写最终 Skill、角色 prompt 或两阶段任务文案，也不修改现有实施方案、代码或 prompt/skill 文件。

## 1. 设计提示词之前，先理解新版 W3 的职责

W3 是消息驱动的最终交易研判节点。它接收上游已经处理过的消息、W1/W2 判断和研究背景，判断这条消息是否值得交易，以及通过哪条路径交易；随后继续完成事实维护和短期关注状态管理。

新版要求把这两类工作拆成**同一个 Case、同一个 Thread、同一套 Skill/Prompt 下的两个连续 Task**：

1. **Trade Decision**：尽快提交可执行的交易选择及必要理由。
2. **Post-Decision Operations**：继承前一轮判断，完成 Fact、Policy coverage、WATCH、PIN 等运营输出。

第一轮的合法决策立即交给现有下游。第二轮耗时或失败不阻塞第一轮，也不能改写、撤回或重新提交第一轮决策。NO_TRADE 同样需要第二轮，因为“不交易”不代表没有新的事实、值得跟进的 Policy 或重要市场背景。

W3 不只是处理“没有 Policy 的 NEW 消息”。它会处理 Policy HIT、仅 RELATED、无 Policy 关系三类业务 Case，也会处理因 LOW confidence 或 WATCH 而进入的 OLD 消息。

### 1.1 必须分开的概念

| 概念 | 业务含义 | 不能混同为 |
| --- | --- | --- |
| NEW / OLD | 消息核心现实相对已知 Event/Fact 是否有新增 | 是否有交易价值、是否能进入 W3 |
| Event / Fact | Event 是已知事件身份，Fact 是具体原子现实命题 | 一篇文章、一个标题、投资观点或市场预期 |
| Policy HIT | 当前消息被判断实际触发某 Policy 的 Condition | 主题相似、已经执行、必须交易 |
| Policy RELATED | 与 Policy 有关，但未确认完整触发 | HIT、执行授权、RECENT_TRADE 的命中 |
| Expectation Delta | 新现实使事件前合理预期需要怎样修订 | 写入事件库的 Fact，或新闻的情绪强弱 |
| Trade Intent | 已形成并登记的交易意图 | 券商已经下单、已经成交 |
| WATCH | 持续跟进某个 Policy，影响相关消息能否进入 W3 | 消费授权、交易信号、消息置顶 |
| PIN | 保留一条当前消息作为同 ticker 的全局短期背景 | W3 入口条件、任意外部信息收藏 |

Policy 每个 Condition 都是独立充分条件，Policy 内固定 OR；方向来自该 Condition 的 `decision`。不要从 Policy 顶层猜方向，也不要把不同消息的零散证据拼成一个已触发 Condition。

### 1.2 相对旧 W3，哪些规则已经改变

| 旧实现/旧提示词中的规则 | 新版目标 |
| --- | --- |
| 单个 Task 同时交付交易、新旧、Fact、WATCH | 交易先输出，运营紧接着在第二个 Task 完成 |
| 只有 NEW + no Policy 才允许 Expert | Expert 与 Policy Trade 平行；HIT、RELATED、Policy 已消费都不取消 Expert 资格 |
| 最终 OLD 只能归档，不交易 | 已进入 W3 的 OLD 不被代码或提示词自动否决交易 |
| 先收敛 OLD/NEW × Policy/no Policy 四象限，才进入 Expert 分支 | 围绕当下交易判断解决必要疑点；不把完整状态裁定和 Fact 编写作为第一轮的交付前置 |
| MAIN Thread 长期接收同 ticker 的无关 Case | Template Fork 隔离，真正 Policy/Event 连续性才 Resume |
| 近期交易主要靠当前 Thread 记忆 | 每个新 Case 都注入同 ticker 的 72h 交易历史，不依赖 Thread |
| WATCH 只能未交易时设置，READY 自动撤销 | 交易后也可以 WATCH，READY 不自动撤销，已消费 Policy 也可继续跟进 |
| 无全局消息置顶 | 新增 24h PIN；WATCH/PIN 各自最多 2 个 |
| mode / first_round_tags 引导旧模式 | 新 Case 使用 case_type / additional_tags 传达信息，旧字段只作历史兼容 |
| 正常置信 OLD + HIT 仅凭 HIT 就进入 W3 | 没有有效 WATCH 跟进且双方置信正常的 OLD 均不进入 |

不能把“取消 OLD 硬否决”改写成“OLD 应当交易”，也不能把“Expert 不受 Policy 约束”改写成“通过 Expert 绕过重复催化判断”。是否交易仍需要可辩护的业务判断；只是这些标签或 Policy 状态不替 Agent 预先作结论。

## 2. 哪些消息会交给 W3

### 2.1 系统先路由，Agent 后接收

W1 判断现实新旧及置信度，W2 判断 Policy HIT/RELATED 及置信度，必要时还有相关 Condition 的新事实支持对齐。系统基于这些结果及有效 WATCH 决定是否启动 W3。Agent 不需要重新执行准入路由，也不能通过输出标签要求系统额外启动一次研判。

新版按以下优先级决定入口：

| 优先级 | 当前消息情况 | 是否进入 W3 |
| --- | --- | --- |
| 1 | 符合有效 WATCH 跟进规则，即实际 HIT/RELATED 到被 WATCH 的 Policy | 进入，包括 OLD |
| 2 | 不满足上一项，但 W1 或 W2 confidence 为 LOW | 进入，包括 OLD |
| 3 | 无 WATCH 跟进、双方 confidence 正常、W1=OLD | 不进入，包括 OLD + HIT |
| 4 | 无 WATCH 跟进、双方 confidence 正常、W1=NEW、W2 HIT | 进入 |
| 5 | 同上，W2 无 HIT、仅 RELATED，相关归因已确认有新事实支持 | 进入 |
| 6 | 同上，W2 无 HIT、仅 RELATED，未确认相关归因有新事实支持 | 不进入，沿用当前非 W3 路径 |
| 7 | 同上，W2 既无 HIT 也无 RELATED | 进入 |

第 6 项是保留的现行路由尺度：对齐为 false 或未形成肯定支持结论时，不新增一个默认进入 W3 的分支。它不意味着模型输入可以把“未运行对齐”伪造为 false；实际进入的 Case 没有运行对齐时仍省略该块。

**“不进入 W3”与“进入后不交易”是不同问题。** 正常置信 OLD + HIT 无 WATCH，不交给 W3；OLD 因 WATCH 或 LOW 已进入后，可以由 W3选择三种交易结果之一。PIN、RECENT_TRADE、BODY_MISSING 都不是额外入口。

非 W3 的消息继续按原工作流处理；不进入 W3 不代表系统必须丢弃全部事实，也不代表 W1/W2 会直接替 W3 发单。

### 2.2 主类型：只描述上游认为当前 Case 属于哪类

第一轮 user prompt 包含一个且仅一个 `case_type`，按 HIT → RELATED → 无关系的顺序生成：

| case_type | 条件 | Agent 需要理解的业务问题 |
| --- | --- | --- |
| `POLICY_HIT_REVIEW` | W2 policy_hit=true | 复核消息与 Condition 的实际触发关系及交易价值，选择 Policy、Expert 或不交易 |
| `POLICY_RELATED_EVALUATION` | 无 HIT，但 policy_related=true | 区分未触发、误漏触发、值得跟进和独立预期差，形成最终交易选择 |
| `OPEN_EVENT_EVALUATION` | 既无 HIT，也无 RELATED | 判断消息所涉及现实、事件前预期及 ticker 传导，不因缺少 Policy 自动不交易 |

同时 HIT 和 RELATED 时主类型取 HIT，但输入仍保留两类完整归因。LOW confidence 不改变主类型；一个 LOW 的 HIT 仍是 POLICY_HIT_REVIEW，不能把这个类型当作已经被确认的最终事实。

三个主类型**均不是交易路径许可名单**。HIT Case 可以 Expert 或 NO_TRADE；RELATED/Open Case 若复核发现真实 Condition 已触发，可以选择 Policy Trade。必要时可查完整 PolicySet，但不要求每个 Open Case 为证明“无 Policy”重读整份研究。

### 2.3 附加标签：已有信息的简短提示

```json
{
  "case_type": "POLICY_HIT_REVIEW",
  "additional_tags": ["WATCH_FOLLOWUP", "RECENT_TRADE"]
}
```

`additional_tags` 可以叠加，无匹配时为 `[]`：

| 标签 | 系统已有的生成依据 | 提示词不应赋予的含义 |
| --- | --- | --- |
| `W1_LOW_CONFIDENCE` | W1 confidence=LOW | 必须不交易，或强制启动独立新研究阶段 |
| `W2_LOW_CONFIDENCE` | W2 confidence=LOW | W2 必然错误，或 Policy 一律不可用 |
| `WATCH_FOLLOWUP` | 本消息实际符合有效 WATCH 跟进规则 | ticker 存在任意 WATCH 就算；或者必须交易/续期 |
| `BODY_MISSING` | 本消息正文缺失或去除空白后为空 | 自动 NO_TRADE，或可以根据标题补造正文 |
| `RECENT_TRADE` | 72h 历史中存在与当前消息实际命中同类对象的真实意图 | 同一催化重复、同一方向、已成交、必须拦截 |

标签随当前输入冻结；第二轮继承，不额外分类。它们不替代 W1/W2 原始判断和理由，不赋予额外工具、研究阶段、路由、Thread 关联或交易资格，不要求输出任何与标签对应的动作。

### 2.4 对 RECENT_TRADE 口径的审核结论

采用用户提出的限定：**只比较可确认的实际 Event 匹配或 Policy HIT，RELATED 与 Prior Event 引用不算命中。** 这能避免把“主题有关系”误写成“曾交易同一对象”。

具体含义是：

- 同 ticker、同一个冻结的 `[本 Case 首次接收时间-72h, 首次接收时间)` 窗口，历史必须真实形成过 `trade_intents`；不要求成交。
- Event 与 Event、Policy 与 Policy 比较，任一可确认对象相同即可；不要求相同 Condition、方向或文章。
- Event 使用可确认的正式身份、临时事实身份的正式映射及合并关系。跨日同样叫 E1 不代表同一事件；系统负责身份处理，Agent 不凭编号或 summary 猜测。
- W1 的实际重复 Event 匹配可以作为依据；NEW Case 用于建立 Prior 的 Event 引用不算。引用本身若没有明确的实际匹配语义，就不能仅凭出现 Event ID 判为命中。
- Policy 仅 RELATED 不算；当前 Case 使用上游已有的实际 HIT，历史也必须有可确认的结构化实际 HIT 关系。不能因为历史选择了交易就把 RELATED 补成 HIT，也不能把 Expert 路径当作没有 Policy HIT 的证明。
- 历史没有可确认对象时不做文本相似推断，不为了补标签增加模型调用。
- 控制层拒绝、尚未形成真实意图的补充历史判断，以及尚未释放的 CLOSED 候选，不触发此标签。

标签有无都不能代替查看历史：有标签可能只是同 Policy 下的另一个 Condition 或新的重要增量；无标签仍可能是不同对象表达下的重复催化。提示词应利用已提供的业务内容判断，而非把标签变成重复交易规则。

## 3. 同一 Case 的两个连续 Task 如何工作

### 3.1 第一轮：提交交易决策

输入已经直接提供当前消息和上游定位的必要依据。Agent 解决足以影响本次交易的疑点，形成 NO_TRADE / POLICY_TRADE / EXPERT_TRADE 之一，并立即输出规定对象。

必要研究可以发生在第一轮，但不能把编写 Fact、决定 WATCH/PIN、完成全量 coverage 报告作为提交交易的前置工作。对 W1/W2 存在的疑点，只需完成当下决策真正需要的澄清；不要求额外产生一份旧式四象限终态报告。

系统保存合法决策后交给既有下游。对于 CLOSED 模式，现有下游仍先形成候选、由原选择流程决定是否释放意图；“决策优先”不把 CLOSED 改成直接交易。

### 3.2 第二轮：延续分析，完成运营

第二轮在同一 Thread 中紧接着启动，使用同一套 W3 Skill/Prompt。系统提供明确阶段身份、第一轮已提交决策和下游接纳/拒绝结果；原消息、已读材料、历史、判断上下文仍在会话内。

Agent 不重新做一遍完整 Alpha 研究，不再选择交易路径，不重新输出方向、交易理由或交易对象。它继续交付正式 novelty、Policy coverage、新 Fact、WATCH/PIN 动作。第一轮决策不会因为第二轮给出 OLD、不同 coverage 或失败而被覆盖。

第二轮启动时会刷新简短的全局 WATCH/PIN 状态，便于在并发情况下管理当前容量；无需重读全部研究或重新装载 72h 历史。最终状态可能与当前输入快照相比又有变化，系统负责拒绝失效或超容量的单项动作，不需要 Agent 先查底层数据库。

如果模型结果已有效保存，但宿主应用动作失败，系统重放动作，不要求 Agent 重复研判。提示词无需设计第三轮交易修订、手工回滚或重复提交协议。

### 3.3 “同一套 Skill/Prompt”的实际要求

一套共同角色和业务方法必须能识别当前阶段，分别遵守两个输出 schema。不能把两个阶段拆成两个互不知情的专家，也不能让第二阶段重新建立第一阶段已有的事实框架、Prior 和交易逻辑。

允许主类型提示不同的关注问题，但不能恢复三套各有交易资格限制的模式。特别是不能把新 POLICY_HIT_REVIEW 映射回“Expert 禁用”，或把 Open Case 映射回“固定 NEW、永不复核 Policy”。

## 4. 会话连续性：Agent 可以依赖什么记忆

系统先准备 Template Thread，其中包含稳定规则和必要研究背景/索引入口；新 Case 原则上从它 Fork。Template 不接收实际消息判断、交易历史、PIN/WATCH 等动态 Case 内容，不被子会话写回污染。**Template 不意味着已预读全部研究全文。**

两种情况允许 Resume：

1. 当前 W2 与已有子 Thread 处理过的 Case 命中或相关同一 Policy：HIT 优先 RELATED，再按上游 Policy 排名；LOW confidence 不取消关联资格。
2. 没有更优 Policy 关联时，当前消息实际重复于既有 Thread 对应的 Event，可以延续该事件上下文。

Policy 关联优先 Event。关联线程正忙时从 Template 新 Fork，避免当前决策等待另一个 Case 的运营；两个 Task 之间不会插入别的 Case。Agent 不需要选择线程，也不能要求系统因标签切换线程。

子 Thread 自最近接收新 Case 起 48h 有效，新 Case Resume 才续期；同 Case 第二轮和重试不续期。过期空闲子会话会清理，Template 排除；核心业务结果、历史意图、有效 PIN/WATCH 独立持久化。

因此提示词应明确以下记忆边界：

- Resume 可能提供同一 Policy 的历史，并不保证是同一个催化；Fork 也不代表系统没有历史交易。
- 不同 Thread 的完整历史不会自动互通；72h 意图、PIN/WATCH 是系统显式提供的共享业务信息。
- 本 Case 当前输入及绑定研究版本是判断依据。旧线程的历史研究、消费状态和关注状态不能覆盖当前快照。
- 不能把“以前在本 Thread 没见过”当作 NEW、市场未知或从未交易的证据。

## 5. 系统给 Agent 的信息与研究能力

### 5.1 第一轮直接提供的业务输入

| 输入块 | 内容及用途 |
| --- | --- |
| ticker / cutoff_at | 标的及本 Case 可用信息截止边界 |
| case_type / additional_tags | 上游已形成的类型和附加信息；不预先决定最终交易 |
| message | Title、完整 Body、source_name、真实发布时间、URL |
| w1 | NEW/OLD、confidence、完整 reason；被引用 Event 的 ID、真实事件时间、canonical_summary，以及必要支撑 Fact 正文 |
| w2 | 唯一完整判断、HIT/RELATED 区分、对应 Policy/Condition 归因、confidence 和完整 reason |
| policies | W2 已定位 HIT/RELATED 的 Policy 并集，每条完整业务对象只给一次 |
| alignment | 实际运行时提供 HIT/RELATED 是否有新事实支持的聚合结论；未运行不伪造 false |
| policy_usage | 正常及已消费的方向状态、可执行方向；给 Policy Trade 使用，不是 Expert 的资格约束 |
| watch | 同 ticker 全部有效 WATCH、理由、开始语义日、到期时间及已用/总容量 |
| pins | 同 ticker 全部有效 PIN，当前置顶消息的 Title/Body/Source/发布时间/创建理由、可取消标识及期限、容量 |
| recent_trade_intents | 同 ticker 过去 72h 的完整规定历史，跨 Thread、包含未成功执行情况 |
| contexts | D1、D2、D3/PolicySet、已知事实视图的名字、真实业务截止时间及清晰读取入口 |

Event/Policy/Condition ID 是业务引用，可以用于选择和操作；Case ID、执行版本、采集流水、消费代际、哈希不是 Agent 应回填的业务内容。新版输出不要求模型原样复制未知的 w3_case_id。

当前消息的 W1/W2 confidence 和完整 reason 都保留，不能只给一个标签。已定位 Event 和 Policy/Condition 直接给到可理解正文，不让 Agent 为获知必读信息再按 ID 查一次。

### 5.2 Policy 具体给到哪些内容

每条原生 Policy 提供 `policy_id/title/ref/transmission/match_scope/activation_conditions`；每个 Condition 提供 `condition_id/decision/trigger_layer/criterion/calibration.reference_state/calibration.trigger_boundary`。

不仅给命中那一条 Condition，也提供同 Policy 的完整 Conditions，避免丢失另一方向和边界。HIT 与 RELATED 到同一 Policy 时正文只给一份，通过归因区分。完整 PolicySet 仍可查，不限于上游召回对象。

注意一个容易误写提示词的同名字段差异：

| 字段位置 | 语义 |
| --- | --- |
| 输入 `w2.policy_ids` / `w2.hit_condition_ids` | W2 实际 HIT 的 Policy / Condition |
| 输入 `w2.matched_condition_ids` | W2 的 RELATED Condition 归因 |
| 第二轮输出 `policy.policy_ids` / `policy.matched_condition_ids` | W3 最终确认 HIT 的 Policy / Condition；这里不是输出 RELATED 列表 |

不能直接把 W2 的 matched_condition_ids 复制到 W3 最终 HIT 输出；RELATED 需要实际确认完整触发后才能变成 HIT。上述命名沿用现有业务合同，最终调用以阶段 output_schema 为准。

### 5.3 72h 历史的含义

每条历史给：消息 Title、Source、完整 Body、发布时间；精简 W1/W2 结果及必要 Event/Policy/Condition 引用；W3 真实交易结论和核心业务 reasoning/Prior/Delta；方向、意图发起时间、实际执行情况。

当前 W1/W2 理由完整，历史 W1/W2 则刻意简短，两者不能混用删减尺度。历史 W3 reasoning 指已经交付的业务理由，不是隐藏思维链或会话日志。

历史包括未成交、投递/执行失败、过期和重复拒绝等真实意图；不以最近几条或只成功成交代替 72h 全集。还可能提供“已有交易判断但控制层拒绝、未释放意图”的补充条目，必须标明实际未释放，它不触发 RECENT_TRADE。旧记录没有 W3 判断时不编造一个。

Agent 需要据此区分：相同现实的重复报道、已有披露的补充、交易曾失败但现实未改变、新的重要规模/阶段/确定性/经济传导变化。以前交易过不自动禁止再交易；以前没有成交也不自动证明现在值得再交易。

EXECUTION_ACCEPTED 只说明执行系统受理，不代表下单或成交。缺少回执或标 UNKNOWN 时不能推成失败、零成交或已持仓。历史以首次接收本 Case 时的快照为准，第二轮不要求查询实时账户来修订第一轮。

### 5.4 公共研究怎样读取

| 研究资产 | 含义与组织 |
| --- | --- |
| D1 | 公司、行业、市场及传导研究；按原报告自然章节提供完整切片 |
| D2 | 结构化预期；按完整 Unit 提供 State、expectation_baseline、Realization Factors、Potential Gaps 等 |
| D3 / PolicySet | 既有触发边界和交易策略；每 Policy 一个完整切片，按可证实 Shell 归属组织 |
| Reference View | 截至绑定版本的已知 Event/Fact；不能把它与未来触发 Policy 混为一个库 |

全部保留权威原文、结构化索引、无损业务切片。索引和文件名给编号/真实 ID、Title、所属研究单元、位置和已有主题线索，不靠另一轮模型摘要代替原文。

Agent 可以按问题定位相关章节/Unit/Policy，需要扩展视野时读取其他切片或全文。**不要求每个 Case 先全文读取 D1/D2/D3/Reference View，也不要求把已经在 payload 给足的内容重新查一遍。**

v2.1 D2 不一定有正式 Unit ID，文件定位编号不是新造的正式研究身份；D3 可能跨 Shell 或无法确定归属，cross_shell/unassigned 不代表内容无效、没有 Policy 或不可交易。研究材料缺少某变量也不等于市场没有该预期。

### 5.5 工具、信源和时间边界

目标能力为：本地研究读取 + live Web Search；Data MCP、其他运营 MCP和 subagents 不开放。Agent 不能假定能查询实时行情、账户、数据库、Broker，也不直接写入业务状态；宿主接纳结构化输出后执行动作。

source_name 是消息发布者/网站规范名称，例如 reuters；优先用可靠已知原发者，否则用当前已知落地域名，不承诺复杂转载溯源。它不是采集渠道 source_id，也不是内容真实性的保证。混源聚合不伪造单一来源，正文中保留逐成员信源；PIN 绑定的是本次实际收到的完整消息快照。

发布时间可能是确切时间戳、只有日期，或 null；不能把 null 当事件没有发生，也不能把日期默认成零点。cutoff_at 与发布时间不同义。研究新现实可使用 cutoff 内信息；恢复 Prior 时，必须区分事件前证据和事件后解释，不能用后见之明制造 surprise。必要澄清可搜索，但不因某个标签自动开启一套额外研究流程。

## 6. 第一轮需要输出什么

### 6.1 输出对象和三个有效形状

第一轮固定四个字段：`decision`、`reason`、`policy_trade`、`expert_trade`。两个分支互斥，未使用分支为 null。以下均为结构示例，Policy/Condition ID 和内容需替换为实际输入，不能当成真实 Case 的建议。

不交易：

```json
{
  "decision": "NO_TRADE",
  "reason": "当前披露未改变既有预期，尚无足以支持方向性交易的增量。",
  "policy_trade": null,
  "expert_trade": null
}
```

通过 Policy 交易：

```json
{
  "decision": "POLICY_TRADE",
  "reason": "消息确认的现实跨过 P7/C2 的触发边界，选择该条件执行。",
  "policy_trade": {"policy_id": "P7", "condition_id": "C2"},
  "expert_trade": null
}
```

通过 Expert 交易：

```json
{
  "decision": "EXPERT_TRADE",
  "reason": "商业阶段提前改变收入兑现时点，经济影响能直接传导至目标公司。",
  "policy_trade": null,
  "expert_trade": {
    "direction": "LONG",
    "prior_expectation": "事件前基线仍为试产，批量供货预计在更晚阶段启动。",
    "expectation_delta": "本次确认已开始批量供货，兑现时间早于原有基线。"
  }
}
```

第一轮不输出 novelty、Fact、coverage 全集、WATCH、PIN、Case ID 或第二轮结果。reason 是当前选择的核心业务理由；Expert 保留具体 Prior 与 Delta，不需要额外重复 evaluated/trade 布尔。

### 6.2 Policy Trade 与 Expert Trade 的选择边界

Policy Trade 选择一个真实、可执行的 Condition，方向由系统读取该 Condition 的 decision。OR 意味着任一完整 Condition 成立即可，不需要全部满足；多条件成立也只选择一个本次执行对象。

可选择完整冻结 PolicySet 中真实存在的 Condition，不局限于 W2 已命中列表。系统最终检查该对象的有效性、方向消费及授权；W3 不负责自行 rearm。已消费 LONG 不代表 SHORT 也消费，consumed=false 也不能代替 executable_directions 的实际状态。

Expert Trade 原则上不受 Policy 约束：W2 HIT、RELATED、Policy 方向已消费或没有可执行方向，不自动禁止 Expert；Expert 不消费、解冻或修改 Policy。它表达独立的交易判断及显式方向，不附 executed_policy_id 假装走 Policy。

两条路径平行，不是“先试 Policy，无法执行就自动 Expert”的后端降级链。若 Policy 选择后来被系统拒绝，系统不会自行改成 Expert；Agent 也不能在第二轮补发另一条交易。既有一般输出许可、交易时效及执行规则仍生效，合法模型结果不保证形成实际成交。

是否存在已知 Policy 与是否存在值得交易的预期差，是相关但不同的问题。交易路径的选择由 Agent 基于真实业务事实作出，不能用 case_type 或标签替代判断。

## 7. 第二轮需要输出什么

### 7.1 运营结果 schema

第二轮固定输出：

| 字段 | 内容 |
| --- | --- |
| `novelty` | result=NEW/OLD、reference_ids、reason |
| `policy` | 最终实际 HIT 的 policy_ids、matched_condition_ids、reason |
| `delta_candidates` | 原子 Fact 列表，最多12个 |
| `watch_actions` | SET/CANCEL、policy_id、reason；不操作时 [] |
| `pin_actions` | SET/CANCEL、pin_id、reason；不操作时 [] |

示例：延续同一 Case，确认新事实，保持对当前 Policy 的跟进，并置顶本次消息。

```json
{
  "novelty": {
    "result": "NEW",
    "reference_ids": [],
    "reason": "批量供货已经启动这一现实尚未被已知事实覆盖。"
  },
  "policy": {
    "policy_ids": ["P7"],
    "matched_condition_ids": [{"policy_id": "P7", "condition_ids": ["C2"]}],
    "reason": "当前消息确认了 C2 所要求的完整现实变化。"
  },
  "delta_candidates": [{
    "proposition": "示例公司宣布目标产品已经开始批量供货。",
    "assertion_state": "ACTUAL",
    "subject_time": null,
    "occurrence_date": "2026-10-13",
    "entities": ["示例公司", "目标产品"]
  }],
  "watch_actions": [{"action": "SET", "policy_id": "P7", "reason": "继续跟进供货规模和后续商业进展。"}],
  "pin_actions": [{"action": "SET", "pin_id": null, "reason": "该消息改变短期商业兑现主线，后续相关判断需要考虑。"}]
}
```

这不是固定动作模板：没有充分理由就用空动作列表。示例日期只有在实际可确认时才填写；这里的消息、ID均为说明结构的虚构例子。

### 7.2 Novelty 与 Fact 的业务职责

第二轮 NEW 至少提供一个支撑新增现实的 Fact；OLD 的 delta_candidates 为空，reference_ids 至少有一个真实且支持覆盖判断的 Event。该要求仅决定运营结果是否有效，不能反向否定已提交交易。

每个 Fact 包含 `proposition/assertion_state/subject_time/occurrence_date/entities`。proposition 只表达原子现实，不把 Prior、估值、利好利空或因果推导塞进事件库。区分“宣布计划”与“计划内容已经兑现”，保留实际陈述状态和真实时间精度；日期无法确认可为 null，subject_time 用于计划期、报告期等业务时间。

assertion_state 沿用现有合同枚举：ACTUAL、GUIDANCE、FORECAST、PLAN、RUMOR、DENIAL、SCHEDULED、ONGOING、PLANNED、EXPECTED、RUMORED、DENIED、HYPOTHETICAL、UNKNOWN；具体调用按 output_schema，不创造新状态。

W3 只产候选 Fact，不自行创建永久 Event ID。系统分配临时身份，后续 O2 正式新增/归并；Agent 不通过 output 新造 EVENT01，也不把跨日临时 E编号当成永恒身份。

### 7.3 Coverage 与反馈

第二轮交付真实 novelty/Policy coverage 后，系统据此生成必要的 coverage gap、BADCASE 等反馈。选择 Expert 不必然是 coverage gap；选择 NO_TRADE 也不等于没有新 Fact。Agent 不额外输出一套交易反馈 schema，也不负责研究资产维护、Policy 发布或 rearm。

若第二轮发现 OLD 或 coverage 与第一轮预期不同，应准确记录运营结果，但不能另作一轮交易决策。保持业务记录真实与保持交易结果不可改写可以同时成立。

### 7.4 WATCH 动作

- 对象是 Policy；SET 限本 Case W2 明确 HIT/RELATED，或本轮复核明确 HIT 的有效 Policy，不能随意选择无关 Policy。
- 可在 Policy Trade、Expert Trade、NO_TRADE 后 SET，也不因 CLOSED 候选而禁止。READY 不自动撤销，已消费方向不取消 WATCH 资格。
- 作用是让后续 HIT/RELATED 该 Policy 的消息继续有机会进入 W3，包括 OLD。为此系统保留有效 WATCH Policy 的 W2 召回可见性，即使其交易方向已消费。
- 每 ticker 最多2个有效 WATCH。期限沿用系统语义日 D/D+1、D+2 边界到期，不是创建起固定48h；语义日由系统定义，不按本机自然日猜测。
- 已有效对象重复 SET 不续期；同批同 Policy 的 CANCEL 优先 SET，不能用取消再设置绕过不滚动续期。
- CANCEL 可以针对当前已注入的本 ticker 全部有效 WATCH，不限于本 Case 相关项。可取消旧对象给新的相关 Policy 腾位置；系统不自动挑一个淘汰。

### 7.5 PIN 动作

- 对象是**本次实际收到的消息**。SET 使用 pin_id=null，宿主绑定该消息；不能指定其他历史消息、搜索结果或外部文章作为 PIN。
- 目的在于保留短期交易主线背景，不是把所有利好利空都置顶。reason 应解释持续性及后续 Case 为什么需要考虑。
- 创建后24h内自动注入同 ticker 每个新 W3 Case，不受 Policy/Event/Thread 归属限制。PIN 本身不让其他消息进入 W3。
- 每 ticker 最多2个，与 WATCH 分别计数。CANCEL 使用已注入的 pin_id，可取消本 ticker 的已有 PIN。
- 同一消息已有有效 PIN 时重复 SET 不续期；到期自然失效，不靠 Thread 存活维持。

WATCH/PIN 操作只在第二轮输出。并发情况下容量或对象可能变化，系统按当前实际状态接纳有效动作；单项拒绝不会撤销交易。Agent 依据当前提供状态选择即可，不先查数据库或设计额外确认流程。

## 8. 典型 Case：提示词必须能处理这些组合

下表描述应具备的判断空间，不规定投资结论。

| 场景 | 系统行为/类型 | Agent 的职责 |
| --- | --- | --- |
| NEW、正常置信、HIT | 进入；POLICY_HIT_REVIEW | 复核真实触发与交易含义，三选一；不强制 Policy Trade |
| NEW、正常置信、仅 RELATED、有新事实支持 | 进入；POLICY_RELATED_EVALUATION | 判断真实增量，必要时确认遗漏HIT；RELATED本身不授权交易 |
| NEW、正常置信、无 Policy 关系 | 进入；OPEN_EVENT_EVALUATION | 恢复 Prior/Delta/传导，决定 Expert、复核发现的Policy或不交易 |
| OLD、正常置信、HIT、无WATCH | 不进入W3 | 不为这种Case设计必跑W3分支，也不通过标签恢复入口 |
| OLD、正常置信、HIT或RELATED、匹配WATCH | 进入；相应主类型+WATCH_FOLLOWUP | 延续跟进，评估当前可行动信息；OLD不是自动NO_TRADE |
| OLD、任一LOW、无WATCH | 进入；按W2关系分类并附LOW标签 | 解决会改变交易判断的疑点，不默认W1或W2必然错 |
| HIT的Policy方向已消费，当前有重要新信息 | 若满足入口则进入 | 可Expert或不交易；Policy消费只约束Policy路径，不自动降级或封锁Expert |
| RECENT_TRADE，历史意图未成交 | 不改变入口，照常提供历史 | 区分执行失败和业务增量，不按“没成交”自动再交易 |
| RECENT_TRADE，同Policy不同Condition/方向 | 不改变入口或资格 | 读取具体历史与Condition，不把对象交集当重复交易结论 |
| BODY_MISSING但有标题 | 不因标签额外进入或退出 | 不编正文；判断必要证据是否足够，缺的是否值得聚焦搜索 |
| Fork新Thread，但存在PIN和历史意图 | 独立会话仍注入共享背景 | 不能把新Thread当无历史；也不能因PIN替代本消息判断 |
| 第一轮已Policy/Expert交易，第二轮OLD或失败 | 原决策保持已提交 | 输出真实运营结果，不撤销、不补发另一条交易 |
| 第一轮NO_TRADE，但消息有长期短期背景价值 | 继续第二轮 | 可产Fact、WATCH或PIN；不把NO_TRADE等同无运营价值 |

## 9. 从零改写提示词时，必须消除的隐性旧假设

检查新文案中是否仍残留这些等价表达：

1. “OLD 一律不交易”“先证明 NEW 才有交易资格”。它们与新版交易前提冲突；事实维护的 NEW 规则只能放第二轮。
2. “HIT 必走 Policy”“有 Policy 不做 Expert”“Policy 用完就不能再判断该方向”。它们把平行路径又串回两级判断。
3. “RECENT_TRADE 就是重复”“无标签就是从未交易”。它们把纯信息投影变成拦截规则。
4. “LOW/BODY_MISSING 必须额外执行一个完整阶段”。必要澄清取决于实际疑点，不由标签增加流程。
5. “交付 Fact/WATCH/PIN 后才能输出交易”“第二轮重新评估是否交易”。它们破坏第一轮优先及不可改写边界。
6. “所有 Case 先读全部研究”“通过ID找已经给出的Event/Condition”。它们重新引入无关阅读和重复工具调用。
7. “母会话已读完全部研究”“Resume 就知道所有近期交易”“会话没记录就是市场未知”。它们超出系统记忆保证。
8. “只有未交易、未消费Policy才能WATCH”“READY会自动取消”“WATCH与PIN同一额度”。它们是被明确替换的旧状态规则。
9. “SET PIN 可以选择搜索得到的重要文章”“PIN会把相关消息强制送入W3”。它们混淆消息绑定及入口职责。
10. “输出合法就已经成交”“执行受理等于成交”。它们混淆 Agent 决策与真实执行。

应继续保留的是业务判断质量：清楚界定当前现实及其陈述状态、可比较的事件前预期、具体预期修订、经济重要性和目标 ticker 的传导；研究围绕会改变结论的问题收敛。新版没有要求降低这些判断标准，而是取消机械资格限制和非时效任务的前置等待。

## 10. 提示词重构的交付对齐点

负责提示词的对话可以依据本文设计一套共同W3角色/Skill、Template初始化内容、第一轮/第二轮任务入口文案及相应示例。技术实现另按工程方案执行，提示词不承担线程选择、TTL清理、数据库幂等、容量事务或交易发送。

交付前应能回答以下问题：

- 同一套业务方法如何只在第一轮输出三选一，在第二轮只输出运营结果？
- 三个主类型、LOW、WATCH、RECENT_TRADE、缺正文如何帮助聚焦，而不形成隐藏交易门槛或额外阶段？
- OLD + WATCH/LOW、HIT但选Expert、方向已消费仍Expert的Case，文案是否允许真实判断？
- 当前W1/W2完整依据、72h历史、PIN和按需研究各承担什么角色，有没有要求重复读取？
- 每个schema字段是否有清晰含义，W2和W3同名 matched_condition_ids 的差别是否避免误填？
- WATCH/PIN理由、对象、容量、期限和取消范围是否符合系统能力，有没有让Agent做系统已经负责的操作？
- 第二轮得到不同事实维护结论或动作被拒绝时，是否仍保证不再输出交易、不修改第一轮？

本文使用“v2.1”表示这次业务迭代。工程方案中的 `persistent-runtime.w3.v4` 等是合同版本名，D3又有自己的原生schema版本，三者不是同一编号；不要求Agent把任何版本号复制进业务输出。正式调用的 phase、output_schema 和当前冻结输入是接口依据，旧历史合同另由系统兼容，不让新Agent同时输出两套结果。

本文自包含业务说明；需要追溯依据时可查：

- [代码勘察发现](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/dev_plan/workflow_v2/w3_orchestration_refactor_recon_findings_20261013.md)：描述旧实现及实现缺口，不代表目标规则。
- [重构实施方案](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/dev_plan/workflow_v2/w3_orchestration_schema_refactor_plan_20261013.md)：工程改造、合同和验收依据，当前文件已包含本轮提及的标签及入口补充。
- [字段删减基线](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/dev_plan/workflow_v2/w3_user_prompt_field_reduction_plan_20261013.md)：继承信息取舍原则，不把其过程schema当最终上限。

本轮仅新增本文，未回改以上文档，未修改业务代码、prompt/skill、数据库或部署；也未以本文示例执行模型研判或交易。
