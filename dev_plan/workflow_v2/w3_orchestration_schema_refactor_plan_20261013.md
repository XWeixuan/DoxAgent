# W3 编排与 Schema 重构实施方案

日期：2026-10-13。状态：开发方案，尚未实施。本文以当前工作区、六项需求、字段删减基线、代码勘察和本轮五项问答为依据，明确工程侧可直接执行的改造。

本轮只交付本文，不修改代码、数据库、部署或 prompt/skill。方案中的输入 DTO、输出 schema、阶段参数和执行包属于工程合同设计；不在本文编写、改写或替换任何角色指令、分析方法、Skill、Template 初始化文案或阶段任务文案。

## 1. 已确定的业务边界

### 1.1 需求依据及优先级

- [六项需求原文](/C:/Users/WEIXUANXIE/.codex/attachments/a0c1eaa1-edc6-4931-8ab8-d1fd4e72d585/已粘贴的文本.txt)。
- [W3 user prompt 字段删减方案](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/dev_plan/workflow_v2/w3_user_prompt_field_reduction_plan_20261013.md)：迭代前的输入基线，继承业务信息直接给足、删除重复及流水包装的原则，不把其字段数量和组织形式视为最终目标。
- [代码勘察发现](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/dev_plan/workflow_v2/w3_orchestration_refactor_recon_findings_20261013.md)：现有链路、约束和文件依据。
- 本轮问答决定优先于旧代码及旧文档，特别是 WATCH 和 Expert Trade 的旧限制。

| 决策 | 本方案的明确落点 |
| --- | --- |
| 取消 NEW 交易硬门槛 | 第一阶段不要求先交付正式 novelty/Fact；不因 W1=OLD 或第二阶段 novelty=OLD 拒绝、撤销交易 |
| Expert Trade 原则上不受 Policy 约束 | 不检查是否 HIT、RELATED、有无可执行方向、Policy 是否消费或迁移是否完成来决定 Expert 资格；不消费 Policy 方向 |
| 关联线程忙碌时新 Fork | 不排队等关联线程，不抢占、不把其他 Case 插入该线程两阶段之间 |
| 开放 Web Search | W3 明确启用 live Web Search；Data MCP 保持关闭，不顺带开放其他 MCP |
| 允许交易后 WATCH | 删除 NO_TRADE 才能 SET、READY 自动撤销和以方向未消费为 WATCH 资格的限制；WATCH 期限仍沿用语义日 D/D+1、D+2 到期且不滚动续期 |
| 调整 OLD 的 W3 入口 | NEW/OLD 不单独作为禁止进入 W3 的硬门槛；有效 WATCH 跟进或任一 LOW confidence 可使 OLD 进入；双方 confidence 正常且无 WATCH 的 OLD 不进入，包括 OLD+HIT |
| Case 标签仅传达信息 | 主类型唯一、附加标签可叠加，简单注入第一阶段 user prompt；不增加路由、权限、交易资格、重复拦截或额外模型调用等能力 |

两条交易路径的经济判断、重复催化识别和选择方法留给 Agent，不在编排代码补造替代 Skill。第一阶段仍只有 `NO_TRADE / POLICY_TRADE / EXPERT_TRADE` 三种互斥选择。

### 1.2 直接指出的兼容冲突

现有 W3 prompt/skill 规定 OLD 不交易、Policy HIT 不做 Expert，并要求单轮交付交易及 Fact。这与新合同冲突。**保留旧文案原样、换成新 schema，不能作为可靠的真实 Agent 上线方案。** 本方案完成工程能力和工程验收；后续单独完成同一套 W3 prompt/skill 的适配后，才能做新合同的真实模型业务验收并启用生产新 Case。

这项依赖不妨碍开发 Thread API、输入投影、切片、业务账本、两阶段编排、Web 权限及前端。工程测试使用明确返回新合同的测试 Worker；不能把这些测试写成真实模型已理解新职责。取消旧文案中的重复读 task、必读全部研究全文要求，同样留待专项；本轮做出精简 payload 和索引接口，不等于已经改变真实 Agent 的阅读行为。执行包中区分旧、新 W3 合同，禁止新二进制自动把旧 prompt/skill 宣告为新合同兼容。这里不增加人工逐 Case 审批或新的交易判断门控。

### 1.3 改造范围

覆盖 Runtime、Codex Worker、派生研究资产、消息信源全链路、交易历史投影、WATCH/PIN、Read/API/前端、恢复及有限留存。

不重做 W1/W2 判断方法、消息准入、研究逻辑、O2/O3 的业务算法、券商执行参数、CLOSED 候选选择策略、全站存储治理。W3 入口仅按第 1.4 节调整正常置信 OLD 的分支，其余现行路由保留；新增 PIN 不改变入口，WATCH 按现有 Policy HIT/RELATED follow-up 扩展入口。新增第 3.2.1 节的纯信息 Case 标签，旧 mode/first_round_tags 保留历史兼容，不再作为新合同面向 Agent 的类型导航。唯一必要的 W2 输入过滤联动是：已消费但仍有效 WATCH 的 Policy 继续可被召回，详见第 8 节，不借此重做 W2。已有 FIRST_ROUND_TRADE 兼容分支仍交给 W3，不恢复旧的快判直接发单。

### 1.4 W3 入口规则

先按业务状态决定是否进入 W3，再为进入 W3 的 Case 生成信息标签；路由不得读取 case_type 或 additional_tags 作为控制开关。

| 按顺序判断的情况 | 入口行为 |
| --- | --- |
| 当前消息符合有效 WATCH 的跟进规则 | 进入 W3，包括 W1=OLD；沿用真实 Policy HIT/RELATED 与有效 WATCH 的匹配关系 |
| 未匹配 WATCH，但 W1 或 W2 confidence 为 LOW | 进入 W3，包括 W1=OLD |
| 未匹配 WATCH，双方 confidence 正常，W1=OLD | 不进入 W3，包括 OLD+HIT；沿用归档路径 |
| 未匹配 WATCH，双方 confidence 正常，W1=NEW | 保留其余现行规则：HIT 进入；无 HIT 但 RELATED 且 related_has_new_fact=false 时归档；其他情况进入 W3 |

NEW/OLD 不是独立的全面准入或否决条件，也不代表所有消息无条件进入 W3。与现状相比，必要变化是正常置信 OLD+HIT 不再仅凭 HIT 进入；LOW 与有效 WATCH 的入口仍保留。已经进入 W3 的 OLD Case 不因 OLD 被否决交易，仍可在第一阶段选择 NO_TRADE、POLICY_TRADE 或 EXPERT_TRADE。新入口用于新合同 Case；已冻结旧 Case 按自己的路由和合同恢复，不追溯改写。

## 2. 目标链路及模块拆分

```text
消息 Publish：规范化 source_name
    ↓
W1/W2：沿用现行快判，按第 1.4 节决定 W3 入口
    ↓
首次接收 W3 Case：冻结本次业务输入与 72h 历史、WATCH/PIN 快照，生成纯信息 Case 标签
    ↓
Policy 关联优先 → Event 重复关联 → 无可用关联则 Template Fork
    ↓
同一 Case 独占同一个子 Thread
    ├─ Task 1：Trade Decision（realtime）
    │    → 严格解析 → 持久化不可变决策
    │    → 现有交易输出事务/候选流程
    │    → 立即允许现有 delivery 执行；不等待 Task 2
    │    → 同事务登记 Task 2 的 Runtime Effect
    └─ Task 2：Post-Decision Operations（background）
         → Resume 同一 Thread，继承原上下文及已提交决策
         → Fact / Policy coverage / WATCH / PIN / 反馈
         → 独立保存、恢复、结束；释放 Case 对 Thread 的占用
    ↓
每天清理过期且无活动任务的子 Thread；结构化业务数据独立保留
```

不引入新的消息队列、向量数据库、模型摘要服务或通用工作流框架。复用现有 Runtime Effect、journal 事务、Worker Job、execution bundle、交易 outbox 和日维护机制。

建议新增四个职责明确的 Runtime 文件：

| 文件 | 职责 |
| --- | --- |
| `src/doxagent/persistent_runtime_v2/w3_contracts.py` | 新输入投影和两个阶段的业务模型、解析及本地校验；不承载文案 |
| `src/doxagent/persistent_runtime_v2/w3_threads.py` | Template 单次初始化、关联排序、占用、TTL、清理登记 |
| `src/doxagent/persistent_runtime_v2/w3_context.py` | 当前输入、72h 历史及全局状态的业务投影和冻结 |
| `src/doxagent/persistent_runtime_v2/w3_research_assets.py` | D1/D2/D3 无损派生、索引、缓存与版本绑定 |

PIN 和 WATCH 的事务操作放入 `policy_watch.py` 及新增 `message_pin.py`；不再把全部新逻辑继续堆进 `w3.py`。现有 `w3.py` 保留资产装配、工作区传输和分阶段 Worker 调用，`service.py` 负责落地业务结果。

## 3. Schema 与输入投影

### 3.1 合同版本独立于 W1/W2

新增 W3 工程合同 `persistent-runtime.w3.v4`，两个输出合同分别为 `persistent-runtime.w3-trade-decision.v4`、`persistent-runtime.w3-operations.v4`。版本存于执行包、阶段记录和 Worker 请求元信息，模型不回填版本或 Case ID。

沿用现有原生 Policy 的 `orchestration_revision=3`，不为 W3 两阶段强行升级整个 W1/W2 编排版本。新旧 W3 的分支依据明确的 `w3_contract_version`，不再用 `revision >= 3` 推断只能接受 `W3CaseResultV3`。旧执行包没有新标记时仍走旧合同。

### 3.2 第一阶段模型输入

定义 `W3CaseInputV4`。模型看到业务投影；完整 RuntimeCase、版本 pin、回执、路由和幂等身份继续留后端。

| 顶层业务字段 | 确定内容及边界 |
| --- | --- |
| `ticker`、`cutoff_at` | ticker 只给一次；cutoff 为本次可使用信息的截止边界，不混同发布时间 |
| `case_type`、`additional_tags` | 第 3.2.1 节的唯一主类型与可叠加附加标签，仅作结果性信息传达；不匹配附加标签时给空数组 |
| `message` | `title/body/source_name/published_at/url`；正文不截断、不摘要；同一标题和正文仅完全相等时去重 |
| `w1` | 原 result、confidence、完整 reason；引用对象展开 Event ID、真实事件时间、canonical_summary 及支撑判断的必要 Fact 正文 |
| `w2` | 唯一完整判定：policy_hit、policy_related、policy_ids、hit_condition_ids、matched_condition_ids、confidence、完整 reason |
| `policies` | W2 HIT/RELATED 并集的完整 Policy 业务对象，各给一次；所有 Condition 的方向、trigger_layer、criterion、完整 calibration，以及 ref/transmission/match_scope 均保留 |
| `alignment` | 实际运行时提供 hit_has_new_fact/related_has_new_fact 聚合值；没运行则省略；不作为交易授权 |
| `policy_usage` | 保留正常状态行、方向消费状态、executable_directions/consumed_directions；去掉重复 ticker、generation、consumption_id 等模型无需使用的值 |
| `watch` | ticker 全部当前有效 WATCH、容量 limit=2/used；每行 policy_id、reason、start_semantic_day、expires_at，不限于本 Case 相关项 |
| `pins` | ticker 全部当前有效 PIN、容量 limit=2/used；每行 pin_id、规定的消息内容、创建理由和 expires_at |
| `recent_trade_intents` | 过去 72h 全部符合口径的历史条目，详见第 7 节，无条数上限 |
| `contexts` | D1/D2/D3/Reference View 的名字和实际业务截止时间；文件入口由工作区清单集中提供，不混入发布审计 |

当前 W1/W2 reason 不因“历史 W1/W2 应简洁”而缩短。历史投影是另一个模型，不共用一个删减函数。新合同用 case_type/additional_tags 向 Agent 传达 Case 类型，不再用旧 mode/first_round_tags 导航；旧字段保持原语义，仅供旧合同恢复和历史读取，不要求新 Agent 同时解释两套类型字段。新旧标签均不能作为 Expert 交易资格校验。外层任务文案及 Skill 如何解释新标签留待 prompt/skill 专项，本方案只定义信息投影和传输。

消息 `published_at` 的模型类型为 `string | null`：EXACT 序列化带时区时间戳，DATE 序列化 `YYYY-MM-DD`，UNKNOWN_FIRST_SEEN 序列化 null。后端保留原 datetime 和 basis；不把 DATE 默认零点、首见时间冒充真实发布时间。事件正文中的发生、计划、报告期时间保留原精度。

PIN 正文在 `pins` 中给一份，WATCH 状态在 `watch` 给一份；不再重复一个 policy_watch_followup 正文列表，follow-up 路由关系保留后端。同一历史意图投递多次只给一条；不同意图即使正文相同也保留各自业务结果和规定正文，不做语义相似合并。当前消息、历史和PIN采用同一种消息对象，不另建多层引用压缩协议；先去掉现有判定和审计包装重复，保留各业务对象明确要求的信息。

### 3.2.1 主类型与附加标签

标签是已形成业务信息的结果性投影，在第一阶段 user prompt 的业务 payload 中简单注入，不新增解释任务或独立分类调用。例如：

```json
{
  "case_type": "POLICY_HIT_REVIEW",
  "additional_tags": ["WATCH_FOLLOWUP", "RECENT_TRADE"]
}
```

主类型必须且只能有一个，按 HIT → RELATED → 无关系的优先级确定，不因 confidence 改变主类型：

| case_type | 生成依据 |
| --- | --- |
| POLICY_HIT_REVIEW | W2 policy_hit=true；同时 RELATED 时仍取此主类型，原 W2 业务信息不丢失 |
| POLICY_RELATED_EVALUATION | W2 policy_hit=false 且 policy_related=true |
| OPEN_EVENT_EVALUATION | W2 policy_hit=false 且 policy_related=false |

additional_tags 只包含以下五种标签，可叠加、去重，无匹配时为空数组；按下表顺序稳定输出：

| 附加标签 | 生成依据 |
| --- | --- |
| W1_LOW_CONFIDENCE | 当前 W1 confidence=LOW |
| W2_LOW_CONFIDENCE | 当前 W2 confidence=LOW |
| WATCH_FOLLOWUP | 当前消息符合有效 WATCH 跟进规则；仅 ticker 存在有效 WATCH 不构成匹配 |
| BODY_MISSING | 当前消息正文缺失或去除空白后为空；不根据正文短、来源或标题推断缺失，不新增质量评分阈值 |
| RECENT_TRADE | 当前消息实际命中的 Event 或 Policy，与同 ticker 近期已产生交易意图的历史消息实际命中的同类对象至少有一个相同 |

RECENT_TRADE 沿用第 7 节本 Case 首次接收时间 t0 及冻结的 `[t0-72h, t0)` 历史窗口，不另查另一套时间范围。比较双方实际匹配的 Event 身份或实际 HIT 的 Policy ID；任一同类对象相同即可，不要求 Condition、方向或全文相同。仅 RELATED、W1=NEW 用于 Prior 的 Event 引用均不算命中。Event 采用第 5.3 节可确认的稳定身份、正式映射及 redirect，不把跨日相同临时 E编号当成同一事件，也不做标题、正文或 summary 相似度推断。

历史必须实际产生过 trade_intents，未成交、投递失败、执行失败、过期或重复拒绝不影响这一资格；第 7 节补充注入的 analysis_trade_decisions 若未释放意图，以及尚未释放的 CLOSED 候选，不用于生成 RECENT_TRADE。历史缺少可确认的匹配对象时不猜测。该标签只表示对象有交集，不表示重复催化、已经成交、必须拦截或应该再次交易。

标签随首次业务输入冻结，重试不重新分类或滑动历史窗口；第二阶段继承已有标签，不另行调用模型生成。标签不替代 W1/W2 reason、命中对象、WATCH 状态或完整历史条目，也不改变路由、Thread 关联、工具权限、交易资格、Policy 消费和任何业务动作。不得据标签自动交易、拦截重复交易、开启额外研究阶段或增加新的交易门控。路由及 Thread 关联继续直接使用各自原始业务依据，不能通过标签反向驱动。主类型描述上游判断，不预先限定最终 NO_TRADE/POLICY_TRADE/EXPERT_TRADE 选择。

### 3.3 第一阶段输出：固定对象，三选一

模型输出形状如下，全部字段固定出现，不适用的分支为 null；使用本地 validator 保证互斥，避免依赖 provider 对顶层复杂 union 的支持。

```json
{
  "decision": "NO_TRADE | POLICY_TRADE | EXPERT_TRADE",
  "reason": "本次决策的核心业务理由",
  "policy_trade": {"policy_id": "真实Policy ID", "condition_id": "真实Condition ID"},
  "expert_trade": {
    "direction": "LONG | SHORT",
    "prior_expectation": "事件前的具体预期",
    "expectation_delta": "本次预期修订"
  }
}
```

上例表示字段类型，不是可同时填两个分支的有效实例。具体校验：

| decision | policy_trade | expert_trade | 后端动作 |
| --- | --- | --- | --- |
| NO_TRADE | null | null | 保存决策与理由；启动运营阶段，不造交易意图 |
| POLICY_TRADE | 非空 | null | 解析 Condition 既定方向，沿用 Policy 输出校验和原子消费 |
| EXPERT_TRADE | null | 非空 | 使用显式 LONG/SHORT，沿用一般交易输出流程，不进入 Policy 消费代码 |

`reason/prior_expectation/expectation_delta` 沿用现有业务文本的最大长度约束，不新增二次模型压缩或裁切。第一阶段没有 novelty、policy coverage 全集、delta_candidates、WATCH、PIN、Case ID；Policy Trade 的理由直接说明为什么选该 Condition，不再重复 evaluated/trade 布尔。

Policy Trade 可以选择 Case 冻结完整 PolicySet 中的真实 Condition，不以 W2 已命中清单作为唯一许可集合，否则会失去 W3 复核遗漏命中的能力。新合同保存完整原生 projection 供该选择及后端校验，W2 的召回过滤另做局部视图，不把过滤后子集冒充完整可读 PolicySet。输出选择本身表达最终触发判断，后端只验证对象存在、方向/修订/授权真实有效；RELATED 本身不自动执行，编排层也不替 Agent 宣告命中。

### 3.4 第二阶段输出

定义 `W3OperationsResultV4`：

```text
novelty          复用 W3NoveltyResult：result/reference_ids/reason
policy           复用 W3PolicyResult：policy_ids/matched_condition_ids/reason
delta_candidates 复用 RuntimeFactCandidate，最多12个
watch_actions    [{action: SET|CANCEL, policy_id, reason}]
pin_actions      [{action: SET|CANCEL, pin_id: string|null, reason}]
```

SET PIN 时 pin_id 必须为 null，后端绑定本 Case 实际收到的消息；CANCEL 时必须引用当前 ticker 的已注入 pin_id。模型不能指定任意 message_id、URL、历史正文来 SET。

第二阶段继续保留事实维护的现有一致性：NEW 需有至少一个原子 Fact，OLD 不产 Fact 且需要有效 Event 引用。**这只影响运营结果接纳，不影响第一阶段交易。** Policy coverage、coverage gap 和 BADCASE 由阶段二结果生成，不能从“选择 Expert”推断“没有 Policy 覆盖”。

第二阶段 schema 不包含交易选择、方向、重新发单或覆盖第一阶段理由的字段。额外交易字段按非法合同处理；不默默执行。WATCH/PIN 单项错误局部拒绝并形成简短 outcome，不能让一个失效 CANCEL 丢掉已经有效的 Fact 和其他动作。有效核心结果一经保存，应用业务动作失败只重放应用，不重新调用模型。

### 3.5 后端业务模型适配

- 新增两个独立解析入口 `parse_w3_trade_decision`、`parse_w3_operations`，不要让新结果继承带旧跨字段 validator 的 `W3CaseResult`。
- `TradeRecord` 增加可选 `w3_trade_decision`，新合同直接保存阶段一业务结果；旧 `w3_result` 留给历史单轮记录，不伪造 novelty、Fact 或 expert_trade.evaluated。
- Policy 路径继续用 `decision_origin=POLICY` 和 executed_policy/condition/revision/generation；Expert 继续用 `decision_origin=W3` 和 w3_case_id，executed_policy_id 为 null。W3 关联由 case_id/阶段记录提供，不为了展示破坏现有来源 validator。
- `W3CoverageGapRecord` 支持运营结果合同及已提交决策的引用；O3 输入投影读取真正的 coverage，不再依赖旧复合 W3 result。
- RuntimeCase/W3RouteCase 对外可呈现两个阶段；权威结果放阶段表，避免在 frozen_inputs、case、route case 中复制整份大输入。旧字段仅用于兼容读取。

## 4. 两阶段编排、交易落账与恢复

### 4.1 Effect 与阶段身份

保留 `route_to_w3` 作为第一阶段 effect，新增 `w3_post_decision` effect，幂等键固定为 `case_id:W3:trade_decision`、`case_id:W3:post_decision_operations`。阶段二单独走 background 调度，不占住处理当前消息的 CASE 任务等待它结束。

`WorkerRunRequest` 新增可选 `task_phase`，新 W3 取 `w3_template / trade_decision / post_decision_operations`。`ReceiptWorker` 对新合同使用显式 phase；旧 W3 和 WeekendSelection 没有新 phase 时仍沿用各自原 identity，不把所有 node=persistent_runtime_w3 的任务强制两阶段。新 Case identity 必须包含 case_id，再组合 scope/run/node/phase；相同 Thread/工作区上的不同 Case 不能复用对方回执。attempt 只用于同一阶段失败重试，成功回执永远优先复用。

`dispatch_pending_effects` 目前只把 route_to_w3 放入独立执行线程，其他 effect 会在 pump 中同步执行。新增运营 effect 必须也异步派发至独立的 agent-effect 执行入口，并在 Worker 请求上明确 background；不能只增加一个 effect 类型就让其模型调用阻塞只有一个线程的 effect pump。dispatcher 按 effect 状态查询运营任务，不要求其 Case 仍是 RUNNING，所以决策完成的 Case 仍能继续运营。

两个阶段传相同 thread_id、相同冻结 W3 角色与 Skill 资产，使用不同 output_schema。第二阶段只新增已提交决策、提交结果及最新全局状态的精简快照，不再次拼接全量消息、72h 历史和研究全文；继承线程已有内容。phase 是请求和 task DTO 中的合同参数，本文不撰写其解释文案。

### 4.2 第一阶段顺序

1. 首次接收 Case 时准备冻结输入、选定 Thread，创建 trade_decision 阶段记录。网络/模型调用期间不持有 SQLite 写事务。
2. 调用 Worker；合法输出先持久化为不可变阶段结果。此后即使交易输出失败，也只重试输出，不重新研究、不改选路径。
3. 把 `TradeOutputService.record` 内部数据库操作抽成 `record_in_transaction(db, ...)`，保留原 public wrapper 供旧消费者使用。
4. 在同一 Runtime SQLite 事务中：校验写租约/输出许可、登记交易意图或 CLOSED 候选或明确拒绝结果、更新阶段提交状态、完成阶段一 effect，并插入阶段二 effect。所有网络提交仍由现有 delivery 完成。
5. 事务提交后立即让现有 delivery 处理 READY；运营 Worker 已排队或失败都不构成 delivery 的前置条件。

Policy 当前授权尚未完成属于可恢复输出问题，保存原阶段一决策并复用现有 admission 恢复；不能因此重新让 Agent 换成 Expert。Policy 选择变为无效、已消费或语义日过期时保存真实 disposition，结束该次输出；不能由后端自动降级到另一交易路径。

### 4.3 必须拆掉的旧依赖

- 不再调用旧 `route_w3_result` 决定新合同第一阶段；新增按三选一直接生成交易/不交易业务结果的函数。
- 从 `_apply_w3_v3` 中拆出 Fact、coverage gap、BADCASE、WATCH，放阶段二应用函数；阶段一不分配 provisional Event。
- 新合同停止调用依赖 delta_candidates 的 `event_trade_key`，不等第二阶段再决定第一阶段能否发单，也不新建文本相似度硬去重。保留 Case/intent_id/Worker identity 的技术幂等；旧合同恢复保留其原去重逻辑。
- Expert 不经过 `_claim_first_policy`、Policy generation/CAS、Policy migration、executable_directions 或命中优先分支。一般输出许可、交易时效和券商执行规则继续适用于两条路径。
- 第二阶段判断为 OLD、发现 Policy 命中或产生 BADCASE，都不能回写第一阶段决策、撤销意图、再发一次交易。

### 4.4 第二阶段应用和失败边界

阶段状态统一使用 `PENDING / RUNNING / RESULT_SAVED / COMMITTED / FAILED`，沿用现有 effect 的 lease/retry，不再建立一套独立调度框架。结果保存和业务应用分开，以支持“模型成功、DB应用失败”的恢复。

Fact 使用现有 `source_message_id + candidate_index` 的稳定幂等身份；WATCH/PIN 使用阶段应用回执；coverage gap/BADCASE 使用 Case 唯一记录。分组应用，每组幂等完成后记小回执，失败仅重放未完成组。不要为了原子性对多个数据库维持长事务。

阶段二自动尝试最多 3 次（首次加两次重试），沿用当前 Runtime Effect 恢复框架；同阶段重试不刷新 Thread 的 48h。自动重试截止不晚于 Thread 的新任务有效期。确认 Worker 已终止后最终失败，保存失败原因并释放 Case 占用；原交易状态保持已提交。第一阶段最终失败且没有可提交结果时也必须确认 Worker 已终止并释放占用，不创建虚假的运营任务。Thread 丢失且无法 Resume 时该阶段失败，不以新线程伪装“继承了第一阶段全部上下文”。

### 4.5 完成状态及下游消费

`_refresh_case_completion` 按“交易决策”和“运营”分开汇总：阶段一已提交后，Case 的决策链完成，记录 decision_completed_at；运营另有 status/error/completed_at。运营 FAILED 不把已成功交易的 Case 改成“交易失败”，也不掩盖运营失败。阶段二可以增加 Fact/反馈记录，但不能用 OLD、ArchiveRecord 或 ADD_TO_DELTA 覆盖阶段一已确定的交易路径及 resolved_route；运营去向在自己的结果中表达。

维护读取已完成的 Fact/反馈组，运营 pending 不阻塞交易 delivery、CLOSED selection 或整 ticker 的日维护。`bounded_inputs.unfinished`、coordinator 的 `_waiting_cases` 要排除纯运营待完成造成的交易等待；尚未完成的事实不假装已经产出。运营晚到后用现有 supplemental maintenance 补交，只处理新完成记录，不能重复消费已处理业务结果。

CLOSED 模式仍由阶段一形成候选，按现有 WeekendSelection 决定是否释放意图。选优不等待运营；选优的独立 Worker schema 不变。不能把“立即处理第一阶段”解释成 CLOSED 模式直接向券商发单。

## 5. Template、关联 Resume 和并发

### 5.1 Template 的粒度及初始化

每 ticker、W3 合同/资产/模型及工具配置、D1/D2/D3 研究组合生成一个 Template key。相同 key 只初始化一次，使用 DB 唯一约束和短租约 single-flight；不按每条消息建母会话。

母会话准备稳定规则及必要研究背景入口，背景以研究索引、Shell/Unit/Policy 的主题和结构信息为主，不预读全部长篇原文。完整原文和切片均可访问。当前消息、72h 意图、PIN/WATCH 和当前 Policy 消费状态属于动态 Case 输入，不能写入母会话。

只有初始化任务真正完成、thread_id 和初始化回执已持久化才标记 READY，空 thread_start 不算已加载背景。初始化任务文案和共用 W3 指令留待 prompt/skill 专项；工程侧实现 API 和状态，使用测试初始化 Worker 验收。

研究组合变化生成新的 Template；旧 Template 保持不可变、永久排除常规定时删除，不把新背景追加到旧母会话。母会话不得执行实际 Case。已有子 Thread 若 W3 合同、角色/Skill、模型及权限兼容，可以继续关联 Resume；新 Case 使用自己冻结的新研究资产，旧研究记忆仅是历史背景，不要求每次维护都切断业务连续性。

### 5.2 关联选择的确定算法

1. 同 ticker、未过期、合同/资产及权限兼容的子 Thread 才参与；跨 ticker 永不复用。
2. 当前 W2 的 HIT 按 policy_ids 原排名排列；RELATED 按 matched_condition_ids 首次出现顺序排列。HIT 在前，重复 Policy 仅保留最高优先级；confidence 不过滤。已有归因顺序不能经过 set/sorted 后当成排名。
3. 找到第一个拥有有效关联 Thread 的 Policy；同 Policy 多线程优先最近接收任务的空闲线程，时间相同按 thread_id 固定排序。该 Policy 的有效线程全忙则立即 Fork，不等待，不回退到较低排名 Policy 或 Event。
4. 没有有效 Policy 关联时，才使用 W1=OLD 的实际重复 Event 关联，按 W1 引用顺序及同样的线程次级规则选取；W1=NEW 的 Prior 引用不能当重复事件关联。
5. 无匹配或匹配组全忙时，只从当前 READY Template Fork 新子线程。CAS 抢占空闲线程失败可在同优先级组重选一次，再失败直接 Fork，不忙等。

首次接收时即保存 W2 的 HIT/RELATED→Thread 关系，无论后来是否交易或最终 coverage 改判；这符合“曾被 W2 命中或相关即可延续”的规则。一个 Thread 可关联多个 Policy/Event，一个 Policy 可有多个子 Thread。

不增加 per-ticker 固定并发门或“最多N个子线程”的新限制。Worker 原有真实资源预算继续生效；Case 占用 Thread 不等于一直占用模型进程或全局 Worker 容量。

### 5.3 Event 身份贯通

临时 E编号不是跨日全局身份。后端保存：ticker、event_library_root、稳定 candidate_identity（`runtime-v2-msg:source_message_id:candidate_index`）、当时版本/语义日和 provisional alias。

阶段二产出 Fact 后绑定 candidate_identity→Thread。O2 发布后，优先调用 Event Library repository 已有 `runtime_target_suggestion(runtime_scope, runtime_atomic_id)`，从 `runtime_atomic_mappings` / `delta_items.target_event_no/target_fact_no` 获取正式落点，增加正式 Event→Thread 别名；遵循已有 Event redirect 解析。`maintenance.py` 在 O2 成功回执之后调用这个关系同步，重复同步幂等。

未解决 candidate 继续使用临时业务身份；DROP_INVALID 不生成正式关联。不能靠 `canonical_summary` 文本相似或只看到 `resolved_candidates` 列表就猜一个 Event。跨日 provisional 展示 alias 重编号不修改稳定 candidate 身份。原有 Event 用作 Prior 的引用不建立“当前消息重复于该 Event”关系。

### 5.4 Case 级占用和恢复

Thread 表保存 owner_case_id、lease_token、lease_until。占用从阶段一准备持续到阶段二 COMMITTED 或确认终止的 FAILED；两个 Job 之间也保持逻辑归属。Worker Job 锁继续防止同 thread 同时运行，Runtime Case 占用防止两阶段之间插入其他 Case。

租约过期不能直接认定线程空闲：先查对应 phase 和 Worker Job 的终态/恢复状态，活跃 turn 保持忙碌；确认旧 Worker 不再执行后由同 Case 恢复或终止。所有释放和清理按 lease_token CAS，旧进程不能释放新占用。该校验是实际同线程完整性约束，不扩展成 ticker 全局阻塞。

新 Case 首次被 W3 接收时记录 accepted_at，并更新 Thread.last_received_at/expires_at=accepted_at+48h。同 Case 阶段二、网络重投、应用重试、Worker 恢复及工具调用都不续期；新的 Case Resume 才续期。

## 6. D1/D2/D3 原文、索引和切片

### 6.1 工作区结构与缓存

```text
research/<asset_key>/
  index.json
  originals/document1.md
  originals/document2.json
  originals/policy_set.json
  d1/001_<章节Title>.md
  d2/001_<ShellTitle>/001_<UnitTitle>.json
  d3/001_<ShellTitle>/<PolicyID>_<PolicyTitle>.json
  d3/000_cross_shell/<PolicyID>_<PolicyTitle>.json
  d3/000_unassigned/<PolicyID>_<PolicyTitle>.json
  manifest.json
```

asset_key 由实际 pin 的原文内容 hash 和派生器版本确定，后端使用，不在 user payload 重复展示。文件名必须带编号/真实 ID 和 Title，做安全字符、重复名及路径长度处理；文件内容始终保留原真实 ID/name/ref，不拿文件编号改写上游身份。

原文逐字节保留。JSON 切片与对应业务对象结构等价，保留未知但已存在的业务字段，不能只用展示 Markdown 重建 JSON。manifest 记录输入 hash、切片对象对应位置、派生器版本和完整性；index 面向模型只包含类型、编号/业务身份、Title、父 Shell、已有 scope/主题、文件位置及截止时间，不含大段摘要。

同一资产组合生成一次，临时目录写完并校验后原子发布。每个 Thread 工作区装配不可变研究目录；Case 输入放独立目录，不覆写旧 Case 文件。默认从 Worker 缓存复制文件到 Thread 工作区；现有部署若提供真正只读的共享挂载可复用该挂载，不把 Agent 可写目录的 hardlink 指向正式原文。`read_only=True` 本身不是操作系统只读证明。不依赖 host 专有路径给模型读。

### 6.2 各资产具体切法

| 资产 | 实现 |
| --- | --- |
| D1 | 先按 assembler 已有 C1/C3/C5 边界，再按各报告实际顶层自然章节切；使用识别 fenced code 的 Markdown heading 扫描，不把代码中的 # 当章节；保留章节前导文字、父章节语境、完整子节 |
| D2 | 每个完整 Unit 一个 JSON，包含 state、expectation_baseline、realization_factors、potential_gaps 及已有字段；父 Shell 的 name/scope/boundary/ref 放索引，不丢独立于 Unit 的原文内容 |
| D3 | 每个完整 Policy 一个 JSON，含 ref、transmission、match_scope 和全部 Conditions；按可证实的 Shell 归属分类，跨 Shell/无法确定使用明确目录，不虚构归属 |

D1 验证所有原文字节区间被覆盖且无遗漏，切片拼接可恢复报告正文；为可读性重复的父标题另标为导航包装，不参与原文等价校验。D2/D3 验证每个对象恰有一个权威切片且结构相等，索引不会遗漏同名对象。

v2.1 D2 没有独立 Unit ID：保留 name/ref，生成仅在本研究版本内稳定的定位编号，并在内部 manifest 标注 derived_locator。旧合同已有 Unit ID 则原样保留。不改 D2 研究 schema 或给历史 Unit 造全局正式 ID。

### 6.3 D3 Shell 归属及维护

新增发布派生 sidecar `policy_research_origins.json`，只记录可证实的来源关系，不改 PolicyV3 的业务判断字段，也不要求研究 Agent 新增输出。

从现有 prepared.topology、owner_shell、被接纳的 Policy 路径/ID、整合编辑目标和继承关系提取 Policy→Shell 来源；按正式 Policy ID 聚合。合并/编辑涉及多个 Shell 时保存多归属，放 cross_shell，索引在各 Shell 下引用同一文件。来源无法可靠映射时放 unassigned，完整 Policy 仍可读，不阻塞 W3。

明确区分 research origin 和已证明的业务 Shell 归属，不能把 source_directory 本身当业务 Shell。自由 ref 不做模糊猜测；历史数据只接受可验证的原来源，缺失就诚实保留 unassigned。

维护中同 ID 未改变来源的 Policy 继承 sidecar；可证明的新来源合并更新；退休移除对应索引；无法证明迁移的新 Policy 放 unassigned。切片消费位置集中在 PublishedW3ContextProvider，因此初始化、维护、正式采纳和手工激活最终都按实际 pin 触发同一个派生器，避免只修首次 publish。

发布侧尽早生成、激活时预热当前组合；首次遇到旧研究版本时按同样的派生器懒生成。研究索引和切片不追随 Case 运行期间的 current 指针变化。

## 7. 72h 跨 Case 意图历史

### 7.1 查询口径

时间 t0 为该 Case 第一次逻辑接收 W3 任务的 accepted_at；窗口 `[t0-72h, t0)`，按意图第一次形成时间排序、再按稳定 identity 排序。不是消息发布时间、成交时间或 Case 创建时间。重试不滑动窗口、不纳入本 Case 刚形成的意图。

权威来源是 Runtime 的 `trade_intents`，包含全部路径、READY、投递失败、过期、重复拒绝及执行失败记录，不只查 `runtime_v2_trade_records`、READY、成交或前端 Read 库。相同意图的投递重试只给一条，不把多个不同意图按标题/消息相似合并。

现有 `analysis_trade_decisions` 记录“已提交交易选择但控制层拒绝输出”，为避免漏掉这类未执行判断，作为补充历史条目一起给出，并明确实际未释放意图；不伪造 intent_id 或成交，不计入正式已释放意图数量。单纯 NO_TRADE 不加入。CLOSED 尚未释放的候选也不冒充正式意图，只有选优释放后纳入。

底层 SQL 可以分批读取全部记录，最终投影无条数上限；不能用现有 deliver limit=20、W2 max3 或 API 分页上限裁掉历史。给 trade_intents 增加 ticker + released_at 的局部表达式索引；补充拒绝记录使用 recorded_at。UTC 时间比较使用 julianday 或统一可排序时间列，不能混合时区直接字符串比较。

### 7.2 每条历史投影

```text
message:       title/body/source_name/published_at
w1:            result + 必要Event引用
w2:            HIT/RELATED结果 + 必要Policy/Condition引用
w3_decision:   三选一或旧结果的真实交易结论 + 核心reasoning/Prior/Delta
direction:     LONG/SHORT
initiated_at:  首次形成意图/被拒绝输出的时间
execution:     实际投递、执行、成交情况及必要失败原因
```

历史 W1/W2 不带详细 reason/confidence/过程；W3 保留交付的业务理由，不存或注入隐藏推理。旧 Case 未经过 W3 时 w3_decision 为 null，不补造。模型不看 broker order_id、流水 ID、Worker 日志、账户配置或完整会话。

execution 从 trade_receipts 和 `te_executions` 按 intent_id 批量关联，明确 OUTPUT_RECORDED、投递失败、EXECUTION_ACCEPTED、entry_result、实际 filled_qty 及必要执行时间。EXECUTION_ACCEPTED 不是成交；缺少回执不是执行失败。执行库不可读时保留已知事实并标 UNKNOWN，不因这项补充查询阻塞阶段一；Runtime 意图主查询失败不能静默伪装空历史。

### 7.3 冻结及持久化

本 Case 冻结历史成员 identity、t0 和当时 execution 小快照；完整消息/业务结果引用不可变的已有 Case/TradeRecord，必要时生成每条意图只保存一次的业务上下文快照。不要把同一篇历史正文复制进每个新 Case 的 DB frozen_inputs。

组装后的 user payload 可放 Case 工作区用于恢复，但它只是有期限的派生输入，不是永久审计副本。第二阶段继承第一阶段历史，不重新查询 72h 全文。后续新 Case 则读取届时最新真实执行情况。

并发 Case 可能同时在对方意图形成前开始，72h 注入不能绝对防止这种并发语义重复；不为此添加 ticker 串行锁或代码相似度封锁。保留同一 intent 的硬幂等，跨消息增量判断由后续 Skill 适配解决。

## 8. WATCH 与 PIN 全局状态

### 8.1 WATCH 新边界

保留 Policy 级 WATCH 和现行语义日有效期，ticker 最多 2 个。可在 Policy Trade、Expert Trade、NO_TRADE 后 SET，包括 CLOSED 候选；WATCH 是后续消息能否进入 W3 的状态，不再绑定当前是否发单或是否有未消费方向。

具体删除新运营路径中 `policy_watch.apply_actions` 的 FINAL_TRADE_DECISION、CLOSED_CANDIDATE、NO_EXECUTABLE_DIRECTION、READY_AFTER_CASE_STARTED 这四类 SET 资格限制，移除 `claim_ready_in_transaction` 中无条件删除 policy_watch 的动作。历史迁移是否完成和方向消费仍约束 Policy Trade，不约束 WATCH 路由。READY 不再自动撤销是本次全局 WATCH 语义更新，不能让一个旧在途 Case 的 READY 又删掉新规则下保留的 WATCH；旧交易输出合同本身不因此改写。

SET 对象限本 Case W2 的真实 HIT/RELATED，或阶段二复核明确命中的有效 Policy；不得任意 WATCH 无关 Policy。CANCEL 可操作已注入的本 ticker 全部有效 WATCH，不再要求来自本 Case follow-up。Policy 被退休/失效时自然取消对应 WATCH，不能继续用不存在的 Policy 授权入口。

现有 `service._run_w2_hot` 和 `_without_consumed_policies` 会剔除 executable_directions 为空的 Policy，单独保留 WATCH 行不足以让其后续生效。新合同在消息快判输入冻结时记录当时有效 watch_policy_ids，W2 召回集合改为“有可执行方向的 Policy ∪ 有效 WATCH 的 Policy”，其余过滤保留。真实完整 projection 不因消费被删，召回使用派生子视图；W2 R1 输出校验及 R2 detail 装配使用同一召回集合。WATCH 到期或 Policy 退休后该例外消失，不放开所有已消费 Policy。W2 只判断现实关联/触发，已消费方向仍不能通过 Policy Trade 再次输出；此处不修改 W2 prompt/skill 文案。

重复 SET 已有效 WATCH 不刷新期限；同一对象 CANCEL 与 SET 同时出现时 CANCEL 优先，不能用同批 CANCEL+SET 滚动延长该对象。每次成功的新 WATCH 使用该 Case 原语义日的 D+2 boundary；运营晚于该窗口时拒绝 SET，不用实际操作时间偷偷延长。

### 8.2 PIN 的绑定和期限

新增 `message_pin.py`，使用 journal namespace `message_pins`。后端生成 pin_id，存 ticker、当前不可变消息快照引用、source_case_id、reason、created_at、expires_at=created_at+24h。

SET 只能绑定本次实际接收的消息，不允许 Agent 传任意正文替换。相同实际消息已经有效置顶时返回 ALREADY_ACTIVE，不续期、不再占一个名额。CANCEL 按已注入 pin_id 定位本 ticker 状态。PIN 的消息业务快照必须能独立于原 Thread 的删除存活；reason 保留完整创建理由。

PIN 仅注入背景，不参与 Router 的消息准入、Policy follow-up 或 Thread 选择。过期时间到即在查询中失效，不等清理任务跑过才消失；物理行删除是后台整理。

### 8.3 容量及并发写入

WATCH 与 PIN 分别计数、分别最多 2，互不抢名额。阶段二首次启动前刷新这两类精简状态并冻结供本阶段使用；并发结果应用时再检查实际状态。

在同一个短 SQLite 写事务中：过滤/清除过期行 → 校验有效 CANCEL → 应用 CANCEL → 按 Agent 输出顺序应用 SET → 记录逐项 outcome。已有操作顺序和取消释放名额生效，不自动淘汰旧项，不硬编码“更重要”的排序。

WATCH 保存 watch_id，后台用阶段冻结的 watch_id 对 CANCEL 做 CAS；新一代同 Policy WATCH 不能被旧 Case 迟到的 CANCEL 删掉。PIN 用 pin_id 自带代际区分。原子计数防止两个 Case 同时 SET 超过 2；容量不足只拒绝对应 SET，不退回交易、不失败整个阶段。

全局状态投影带 limit/used 和有效对象，提供已规定的理由/期限；不再让 Agent 为容量查询历史会话。操作回执按 `case_id:operations` 幂等，重复应用不得续期。

## 9. 信源字段和工具权限

### 9.1 统一 source_name

新增业务字段统一命名 `source_name: string | null`，值例 `reuters`。Bus 已有 source 是采集/展示混合语义，Read/API 的 source 又是 SourceLabel 对象；不能直接全局替换旧 source 或用 source_id 冒充新闻发布者。

新增纯函数 `resolve_source_name`，落在 `message_bus_v2/source_identity.py`：

1. 优先使用适配器已明确提供的可信 publisher_name/原发者信息，经有限别名表规范化；不从标题或正文猜 Reuters。
2. 其次使用已存在的最终落地 resolved_domain 或消息 URL hostname。已知域名映射短名，如 reuters.com→reuters；未知站点保留小写规范 host、去 www，不盲取倒数第二段破坏 co.uk 等域名。
3. 无可靠值为 null，不用采集任务名填充，也不新增网络请求、跳转溯源或模型调用。

Publish 前形成默认字段，并沿 Raw/Standard/MaterializedMember/Envelope/Snapshot 全链路保留。正文补全已有的最终 URL/publisher 若在发布前可得就参与生成；新消息业务版本按它自己的可用事实计算，不回写旧 Case 的冻结消息。

BUFFERED 单源聚合可给统一 source_name；混源聚合顶层为 null，编译正文逐条保留各成员规范信源、Title 和真实发布时间，不能取 latest.source 假装整个包同源。PIN 和历史保存实际收到的完整聚合快照，不只挑最后一条。修正 compiler.format_member 的来源及 DATE/UNKNOWN 发布时间表示，避免全链路又引入假精确时间。

W1/W2 的 snapshot 自动获得 source_name，不改其分析指令。W3、72h 历史、PIN 均读已生成字段，不各自解析 URL。旧数据用同一纯函数从已保存 URL/publisher 派生，无法确定就 null；不进行全量联网历史修复。

### 9.2 Web Search 与 Data MCP 解耦

`WorkerRunRequest` 新增 `web_search_mode: disabled | live | null`，null 保持现有非 W3 调用行为；新 W3 显式 live，data_mcp_enabled=false，allow_subagents=false。SDK 配置优先按显式 web_search_mode 决定，不再因为 read_only && !data_mcp_enabled 就连带关闭 Web Search。

保留 data/source_capture/o4_operations/d2_discovery 的原独立关闭逻辑；不把开放 Web 等同于开放这些 MCP。W3 结果写入 DB 仍由宿主接纳，Agent 不直接操作业务库。

官方配置支持 `web_search="live"`；这里采用该现有能力，不建立新的搜索 MCP。[OpenAI Web Search 文档](https://learn.chatgpt.com/docs/web-search)。搜索如何控制 cutoff、何时停止研究属于后续 Skill 适配，本轮不重写。

## 10. Worker Thread API、48h 清理和最小留存

### 10.1 Worker 接口

新增受现有 Worker 认证保护的最小接口，并同步 HTTP/local clients：

| 接口 | 必要参数/行为 |
| --- | --- |
| `POST /v1/threads/fork` | parent_thread_id、目标 workspace/run、兼容配置、idempotency_key；只从注册 READY Template Fork，返回 child_thread_id |
| `GET /v1/threads/{id}/status` | 返回是否存在、是否有活跃 turn/Job，用于恢复及清理；不默认返回完整会话 |
| `DELETE /v1/threads/{id}` | 删除已注册非 Template、无活动任务的 W3 子线程；幂等 NOT_FOUND 视为已删 |
| `POST /v1/jobs/{id}/acknowledge` | 宿主已持久化终态输出后，允许压缩该 W3 Job 的冗余大 payload；保留幂等和必要回执 |

Fork 接口先保存本地操作登记，为该 operation 分配唯一目标工作区 cwd，再调用 SDK，得到 thread_id 后立即保存。相同 idempotency_key 单次执行；超时未知结果先查询登记，进程恰在SDK已创建但本地未落账时退出，则按唯一 cwd 查询持久化线程并核对母会话关系，恢复原 child_thread_id。不能仅凭相同 parent 猜是哪一次 Fork，也不能网络重试就再造一条会话。只补这一层必需的操作登记，不做通用分布式事务。

Worker 将初始化阶段创建的线程注册为 TEMPLATE，将 Fork 返回的线程注册为 CASE，生命周期接口只操作这个受认证调用维护的 registry。Template 防删除同时在 Runtime 与 Worker 检查；status 提供活跃 turn/Job 和角色，不需要返回完整历史。Case 不从另一个 Case Fork，且不开放subagents；一次性测试还必须证明删除一个子线程不删除母会话或同母会话的其他子线程。

会话 Fork 复制对话历史；WorkspaceStore 的 fork_snapshot 只复制文件，不替代会话 Fork。线程使用自己的可写 Case 目录和不可变研究入口，不能共享一个会被下个 Case 覆写的 task.json。

### 10.2 实际 SDK/协议实现顺序

已勘察安装的 openai-codex==0.159.3 有 thread_fork、thread_archive，未见同级 thread_delete。实施先在与生产一致的 Worker 依赖中检查实际 SDK 和 app-server 协议，使用一次性测试线程验证 Fork/Resume/Delete；不碰真实业务会话。

优先使用 SDK 原生 thread_fork。Delete 优先使用 SDK 已公开的同能力；当前 wrapper 缺失但实际 app-server 支持时，在 Worker 的 thread lifecycle adapter 中封装正式 `thread/delete` 协议。若实际二进制不支持，则只升级 Worker 所需 SDK/二进制到经测试支持的固定版本，回归现有 start/resume/receipt/capsule，不以全局依赖大升级代替。

官方语义中 archive 只是移动日志，delete 才永久删除持久化会话；Fork 复制存储历史。不能用 archive 宣称完成 48h 存储回收。[OpenAI App Server 文档](https://learn.chatgpt.com/docs/app-server)。不直接改共享 Codex 内部 SQLite/WAL，也不手删 session 文件假装协议已成功。

### 10.3 每日清理流程

复用现有 coordinator/ops 调度每天一次，以服务时区日为幂等运行键；过期比较使用 UTC 的 last_received_at+48h。只处理注册归属明确的 W3 CASE/LEGACY_CASE，Template 无论是否旧代际都排除。

先短事务把候选从 ACTIVE CAS 为 DELETING，阻止新路由；检查 owner/未完成恢复和 Worker 活跃 turn，仍运行就恢复 ACTIVE 并跳过。本轮无活动任务的线程先释放其空闲 affinity capsule，再调用正式 delete；成功后标 DELETED、解除路由关系，再删专属临时工作区和已无引用的派生缓存。删除失败保留短错误，次日重试；关系保持不可选，不让失效线程阻塞新 Fork。

到达 48h 的子线程不能接收新 Case，但运行中的 Case 不被强杀。待恢复阶段二不无限延期：在无实际运行且超过自动恢复截止后标运营失败，再回收。真正活跃的 turn 始终跳过。每日清理通常意味着物理删除晚于到期、最多多等一个日调度周期；逻辑路由在 48h 当刻已失效。

### 10.4 留存对象分层

| 对象 | 留存决定 |
| --- | --- |
| Template | 永久排除常规自动删除；保留母会话本体及其必要资产 |
| 子 Thread 的 SDK 历史 | 48h 内为真实 Resume 保留唯一原生副本；到期空闲后删除，不另存完整备份 |
| Case 业务结果、核心理由、消息、意图、真实执行、必要关联、WATCH/PIN | 独立于会话；继续已有业务留存，48h 清理不得删除，72h 历史始终可读 |
| 阶段输入 | 保留重试所需的快照成员/版本/执行状态及工作区派生输入；不在 DB 多份复制历史正文和研究全文 |
| Worker 请求/完整 agent_loop/工具输出/工作区重复日志 | 宿主持久化合法结果并 ACK 后删除冗余大 payload；只保留小回执、统计和错误，原生会话历史按上行规则留存 |
| Worker 未确认结果及运行中恢复材料 | 保留到成功落地或确定放弃；fsync 的 result_receipt 不能先删 |
| 派生研究缓存 | 原始正式研究不删除；缓存仅在无存活 Template/Thread/恢复引用后回收，可重建，不做全库研究清理 |

ACK 后 JobStore 仍保存 idempotency_key→job_id、request_sha256、status、thread/turn、输出摘要或小型结果；重复投递返回同终态，不因为 request 大字段被清空而新起任务。回收只处理本次 W3 注册对象，不改变 D1/D2/D3/O4 的 Job 留存。

## 11. 数据库及代码落点

### 11.1 最小新增存储

在当前 Runtime SQLite 迁移中新增三张专用表；InMemory repository 同步实现同等语义供测试。现有 journal 的 WATCH、PIN、操作回执和意图内容继续复用 KV，不为每个功能再建一组状态机表。

| 表 | 关键列/索引 |
| --- | --- |
| `runtime_v2_w3_threads` | 本地record_id、唯一thread_id、ticker、kind(TEMPLATE/CASE/LEGACY_CASE)、template_key、parent、compatibility_key、workspace、status、last_received_at、expires_at、owner_case_id、lease_token/lease_until、短error；Template key唯一索引、ticker/expiry索引 |
| `runtime_v2_w3_thread_links` | thread_id、ticker、link_kind(POLICY/PROVISIONAL_EVENT/CANONICAL_EVENT)、subject_key、来源case_id；线程/类型/subject唯一，ticker/类型/subject查询索引 |
| `runtime_v2_w3_stages` | case_id+phase主键、thread_id、status、result_json、result_hash、input_refs/input_hash、attempt、worker_job_id、accepted/started/saved/committed时间、输出disposition、短error |

Thread record_id 在 Fork 前分配，用来处理创建尚未返回 thread_id 的恢复。Template 可处于 CREATING/READY/FAILED，Case 线程生命周期使用 ACTIVE/DELETING/DELETED，不把租约状态再变成业务结论。

新增 journal namespaces：`message_pins`、`w3_operations_applied`、必要的 `w3_intent_business_context`，并复用 policy_watch、worker_requests/receipts。大字段只存一次，表间身份留后端，不进入 user payload。

### 11.2 主要文件与具体改动

以下现有文件均已勘察；新增文件采用第 2 节的职责拆分。

| 文件 | 必须完成的改动 |
| --- | --- |
| [schema.py](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/persistent_runtime_v2/schema.py) | source_name、新合同引用、TradeRecord 新决策字段、运营结果/反馈兼容、effect 类型、阶段展示；旧 validator 不套新合同 |
| [w3.py](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/persistent_runtime_v2/w3.py) | 调用业务投影和派生资产；分阶段 schema/Worker 请求；取消新合同 case_id 回填校验；旧 runner 保留恢复分支 |
| [service.py](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/persistent_runtime_v2/service.py) / [router.py](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/persistent_runtime_v2/router.py) | 分阶段执行、结果先存、交易先提交、运营独立effect；删除新合同的 NEW/HIT→Expert 门控；按第 1.4 节调整正常置信 OLD+HIT 入口，保留 LOW/WATCH 入口及其余路由；完整projection与W2召回视图分开，有效WATCH加入召回；Case 标签仅作输入投影 |
| [repository.py](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/persistent_runtime_v2/repository.py) / [journal.py](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/persistent_runtime_v2/journal.py) | 三表迁移、关联排序查询、短事务CAS、阶段和线程仓库接口；旧MAIN/FALLBACK只供旧Case |
| [worker_receipts.py](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/persistent_runtime_v2/worker_receipts.py) | 显式phase身份、阶段成功回执复用、保存后ACK，保留原失败模型override规则 |
| [trade_output.py](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/persistent_runtime_v2/trade_output.py) | 提取同事务record；新合同不用依赖Fact的event key；原意图、CLOSED和delivery逻辑继续使用 |
| [policy_watch.py](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/persistent_runtime_v2/policy_watch.py) / [policy_consumption.py](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/persistent_runtime_v2/policy_consumption.py) | WATCH 新资格、全局状态和容量；移除READY自动删WATCH，Policy消费本身保留 |
| [execution_bundle.py](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/persistent_runtime_v2/execution_bundle.py) / [factory.py](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/persistent_runtime_v2/factory.py) | 冻结两个 schema 和W3专用合同版本；资产兼容key；组装新能力，现有生产默认合同不自动切换 |
| [coordinator.py](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/persistent_runtime_v2/coordinator.py) / [bounded_inputs.py](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/persistent_runtime_v2/bounded_inputs.py) / [maintenance.py](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/persistent_runtime_v2/maintenance.py) | background运营、恢复/补交、O2映射同步、日清理；纯运营待处理不阻塞交易及选优 |
| [Worker schema](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/codex_worker/schema.py) / [app.py](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/codex_worker/app.py) / [client.py](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/codex_runtime/client.py) / [local_client.py](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/codex_worker/local_client.py) | phase、显式web开关、Fork/status/delete/ACK接口及一致的本地/HTTP协议 |
| [sdk_runtime.py](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/codex_worker/sdk_runtime.py) / [jobs.py](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/codex_worker/jobs.py) / [capsules.py](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/codex_worker/capsules.py) | 真Fork及生命周期adapter、活跃检查、幂等恢复、终态大payload回收、Web与Data解耦；不强制两阶段一直占一个进程 |
| [D3 orchestrator](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/workflows/codex_document3/orchestrator_v21.py) / [publication_v21.py](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/workflows/codex_document3/publication_v21.py) | 从已有结构提取派生来源sidecar并随发布传递；不改研究指令、Policy判定或强造Shell |
| [Bus schema](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/message_bus_v2/schema.py) / [service.py](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/message_bus_v2/service.py) / [repository.py](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/message_bus_v2/repository.py) / [compiler.py](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/message_bus_v2/compiler.py) | 发布source_name、raw/standard/member传递、聚合逐成员来源及时间精度 |

Worker 可另增 `thread_lifecycle.py` 集中 SDK/协议兼容和操作登记，避免把底层 thread/delete 封装散进 Runtime。实际存储路径和认证沿现有 Worker 配置，不新开公网服务。

## 12. Read/API/前端

API 新增业务 source_name，原 SourceLabel 继续表示采集渠道，两者不互相覆盖。更新 [API types](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/dev_plan/workflow_v2/api_contract/doxagent-v2-api.types.ts)、[projectors.py](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/v2_read/projectors.py)、[runtime.py](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/v2_read/runtime.py)、[views.py](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/api_v2/views.py) 和前后端生成 wire schema，不手改生成 JSON。

Case 详情按新合同展示 decision、实际意图/执行 disposition、operations_status、运营结果或错误。阶段一已提交时立即可见，不等待完整W3结果；运营失败明确呈现“交易决策已提交，运营失败”，不把 EXECUTION_ACCEPTED 显示为成交。

每次尝试记录 phase、耗时、模型用量及错误；可区分交易决定耗时与运营耗时，保持最终业务理由可查询，不增加完整工具调用查看器。旧 Case 仍按单轮结果展示，不伪造两轮时间。

消息总线、Runtime消息/Case、意图相关消息、历史上下文、PIN状态及导出均展示 source_name。前端 W3 分支由 decision 枚举决定，删除“有 policy_execution 就Policy，否则Expert”的新合同推断。现有 WATCH 展示扩为全局 WATCH/PIN，但不新增人工操作面板或无业务用途的说明卡片。

涉及 [message-bus.tsx](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/frontend/v2/src/pages/message-bus.tsx)、[runtime.tsx](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/frontend/v2/src/pages/runtime.tsx)、[runtime-case.tsx](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/frontend/v2/src/pages/runtime-case.tsx)、[runtime-case-export.ts](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/frontend/v2/src/core/runtime-case-export.ts) 及实际共用消息组件。实现按 [前端AGENTS](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/frontend/v2/AGENTS.md) 在 1262px、1559px 逐页验证桌面布局和交互。

## 13. 实施顺序与每批完成条件

各批是开发依赖顺序，不是长期并行维护两套产品。旧路径只覆盖已冻结旧 Case 的恢复和历史读取；新合同准备好后新 Case 单一路径进入新实现。

| 批次 | 直接执行内容 | 完成条件 |
| --- | --- | --- |
| 1 合同和仓库 | 新DTO/两个输出模型、解析器、三表迁移、内存仓库；冻结W3独立合同，提取record_in_transaction | 旧数据可读；三选一及阶段身份测试通过；迁移重复运行不改业务记录 |
| 2 Worker能力 | Fork/status/delete/ACK、显式phase/web、操作登记；必要的最小SDK兼容 | 一次性线程Fork继承、母会话不污染、Resume、真正Delete得到验证；HTTP与local一致；旧Worker场景回归 |
| 3 信源全链路 | source_name解析和传播、聚合及时间精度、旧数据纯函数投影 | Reuters可信发布者/域名回退/混源/缺失/正文补全路径一致，W1W2 snapshot不丢来源 |
| 4 研究派生 | 无损切片、索引、来源sidecar、版本缓存、激活预热 | D1覆盖无遗漏；D2Unit/D3Policy结构等价；跨Shell/未知来源及维护同步可用 |
| 5 输入与历史 | 字段删减投影、Event展开、Policy并集、72h全集、状态快照冻结、主类型及附加标签 | 当前reason和完整业务依据保留；历史失败意图不漏；RECENT_TRADE按真实命中身份及实际意图判定；标签无附加控制能力；正文不多份常驻DB；重试输入稳定 |
| 6 Thread路由 | Template registry/初始化合同、Policy/Event links、Case占用和忙时Fork | 排序/低confidence/48h/跨日正式归并正确；并发不进入同一Thread；模板排除Case运行 |
| 7 两阶段主链 | 第 1.4 节的 W3 入口、第一阶段不可变提交+现有输出+运营effect；独立运营应用和恢复 | 正常置信 OLD+HIT 无 WATCH 不进入、有 WATCH 则进入，LOW 入口保留；阶段二阻塞/失败时交易已可delivery；各崩溃点只交易一次；CLOSED候选保持原路径 |
| 8 全局状态 | 新WATCH资格、有效WATCH的W2召回例外、PIN24h、各2容量、CAS取消和局部outcome | 交易后WATCH、已消费Policy后续RELATED可进入W3、并发SET/取消换位/过期均符合决定；PIN绝不改入口 |
| 9 消费与展示 | 日维护/补交、Read/API/wire/前端/导出、逐阶段指标 | 运营pending不拖选优和交易；晚到Fact可维护；新旧Case均能查看真实业务状态 |
| 10 留存和联调 | 日清理、ACK压缩、旧Thread归属登记、集成/故障注入测试 | 删除后72h历史/PIN/业务结果仍完整；幂等恢复材料不早删；工程验收报告明确真实Agent依赖 |

批次 1–10 均不修改 prompt/skill 文案，也不把旧文案生成器中的业务说明悄悄改成新规则。可提取 request 构造、schema和payload传输接口；新阶段文案留空为外部待接入资产，测试注入明确的 fixture。每批重要代码改动简要追加 `changelog`，记录行为、验证和未完成边界，不覆盖工作区已有变更。

## 14. 测试与验收

### 14.1 必须覆盖的业务和故障用例

| 分类 | 最低验收内容 |
| --- | --- |
| W3入口 | 正常置信 OLD+HIT 无 WATCH 不进入；匹配有效 WATCH 的 OLD+HIT/OLD+RELATED 进入；任一 LOW 的 OLD 进入；WATCH 过期后不再授权入口；正常置信 NEW 的其余现行路由保留；标签值不驱动路由；旧冻结 Case 不追溯改路由 |
| Case标签 | 主类型 HIT优先RELATED且唯一、与confidence独立；两种LOW可叠加；WATCH仅实际follow-up；正文null/空白/短正文分别验证；RECENT_TRADE覆盖同ticker的实际Event/Policy命中交集、72h边界、失败未成交意图、仅RELATED/Prior引用排除、无意图拒绝记录/CLOSED候选排除、跨日Event身份；去重/稳定顺序/空数组/重试冻结正确；不增加模型调用、权限、关联或交易控制 |
| 交易路径 | 三种选择互斥；按入口规则进入W3的W1=OLD仍可Expert；HIT且方向已消费/迁移未解决仍可Expert；Expert不改变Policy usage；Policy仍按真实Condition方向及授权原子消费 |
| 交易优先 | 阶段二人为阻塞/超时/失败，阶段一意图已落账并可被delivery读取；NO_TRADE也进入运营；阶段二OLD不撤销交易 |
| 崩溃恢复 | Worker成功宿主未保存、结果保存未输出、输出事务提交响应丢失、运营effect未调度、运营组部分完成、服务/Worker重启；每处恢复均不重复交易 |
| 回执 | 两阶段identity不同；成功阶段不重跑；非法运营额外交易字段不发单；失败阶段只重做该阶段或未完成应用组 |
| Thread路由 | HIT优先RELATED、保留排名、低置信仍关联、同Policy多线程、Policy高于Event、繁忙Fork、CAS竞争、无关Case隔离、模板不污染 |
| 生命周期 | 48h临界、仅新Case续期、阶段二和重试不续期、运行/恢复状态检查、过期不可选、每日一次、Template及其他节点不删除 |
| Event连续性 | 多candidate、跨日alias、O2正式映射/重复归并/redirect、未解决/DROP_INVALID不假造关系；NEW的Prior引用不当重复关联 |
| 研究完整性 | D1代码围栏/不同heading/前导正文，D2同名Unit/无ID/完整baseline，D3全部Condition/跨Shell/未知来源/退休/维护；切片与原对象等价 |
| 72h历史 | 窗口边界、超过常见分页条数、跨线程、Policy/Expert、失败/过期/重复拒绝/控制拒绝、未成交/部分成交/未知，恢复窗口不漂移 |
| WATCH/PIN | 交易后SET、已消费Policy经W2召回并产生OLD+RELATED仍进W3、READY不自动取消、WATCH到期取消召回例外、CANCEL跨本Case相关范围但不跨ticker、各2并发、旧代际CANCEL、无滚动续期、PIN仅当前消息/24h/不路由 |
| 信源/权限 | 可信publisher、转载域名回退、缺失、混源聚合、旧数据、新业务版本、W1W2W3/API/导出一致；Web live且Data/其他MCP仍关闭 |
| 留存 | ACK前结果fsync材料可恢复；ACK后重复request仍返回同job；Session删除不删交易历史、有效PIN、正式研究及旧Case结果 |
| 消费/展示 | CLOSED候选和Selection不误套schema、运营失败不显示交易失败、晚到维护补交、真实执行和成交分开、新旧Case展示/导出 |

复用现有 `test_persistent_runtime_w3.py`、`test_runtime_policy_direction_usage.py`、`test_persistent_runtime_worker_receipts.py`、`test_worker_resource_budget.py`、`test_runtime_orchestration_execution.py`、`test_runtime_orchestration_recovery.py`、Bus/D3/v2_backend相关测试，新增按模块聚焦的W3 contracts/threads/research/history/PIN测试。更新与新合同明确冲突的旧断言，同时保留旧合同恢复用例；不能把所有历史测试一删了之。

### 14.2 执行检查

以仓库现有 Python 开发环境和 pnpm lockfile为准，命令从仓库根目录执行；安装运行环境属于后续实施，本轮未运行：

```powershell
python -m pytest tests/test_persistent_runtime_w3.py tests/test_runtime_policy_direction_usage.py tests/test_persistent_runtime_worker_receipts.py tests/test_worker_resource_budget.py tests/test_runtime_orchestration_execution.py tests/test_runtime_orchestration_recovery.py
python -m pytest tests/v2_backend/test_wire.py tests/v2_backend/test_trade_outcomes.py
python -m ruff check src/doxagent/persistent_runtime_v2 src/doxagent/codex_worker src/doxagent/codex_runtime src/doxagent/message_bus_v2
node scripts/generate_v2_schema.cjs
pnpm --dir frontend/v2 typecheck
pnpm --dir frontend/v2 test
pnpm --dir frontend/v2 build
git diff --check
```

新增文件的定向测试、研究发布/维护回归、仓库类型检查和前端浏览器检查按实际改动加入。运行前核对已有脚本用法，wire生成以规范TS定义为源；检查通过后不无理由反复跑同一批测试。

### 14.3 性能与真实 Agent 验收边界

记录 accepted→Worker start 的排队时间、阶段一模型耗时、合法结果→意图落账时间、delivery受理时间、阶段二耗时、读取文件/搜索次数、压缩次数及耗时、输入规模。阶段一和阶段二分别计时，不能拿整个Case的耗时冒充交易延迟。

用量优先记录可证明的 turn总量或逐调用累加，明确 usage_basis；若 SDK 仅给最后一次调用值，就标 last_call，不当成整轮总量。可从流式usage累积的数值立即汇总，不为统计长期保留全工具payload。Fork复制的父历史累计值不能再次记入子任务账单。

工程验收使用固定输入和可控测试Worker，验证时序、数据完整性及恢复。实际SDK生命周期使用一次性测试线程验证，不产生交易。真实Agent业务/延迟对比需要后续prompt/skill适配完成后，用同一组输入、模型和研究组合比较，至少涵盖交易/不交易、Policy/Expert、OLD+WATCH、关联Resume/新Fork、大72h历史及运营失败。10月12日旧样本只作现象参考，不作为新版已通过证据。

不预先承诺百分比降时或“不会压缩”：全量72h正文、较大的Template索引和48h内频繁关联Resume仍可能触发压缩。保留这些业务需求，先去掉重复和无关全文读取，不用偷偷截断/限条数换性能指标。

## 15. 迁移、切换和交付边界

1. 迁移只增表/可选字段及必要索引，不重写存量 frozen bundle、已提交意图或研究原文。旧Case继续按自己的合同恢复；不把旧复合结果假装成曾执行过两轮。
2. 现有有效WATCH补watch_id并纳入全局2容量；若存量超过2，按原开始时间倒序、policy_id固定排序保留最近2项，其余标CAPACITY_MIGRATION结束，理由留小回执。不得默默截断注入而DB仍认为全部有效。
3. 旧MAIN/FALLBACK只作为LEGACY_CASE登记，不作为新Template或新Case复用。必须能从W3Case/Worker请求证明归属；无法证明的会话不纳入本次自动删除。能证明归属的旧线程按最近真实Case接收时间计算TTL，无法恢复精确时间则以迁移登记时间起算48h，不依据文件mtime立即删。
4. 历史source_name与72h上下文按已保存业务字段懒补齐，优先预热近72h；不发网络回填请求、不重跑历史研判。旧已完成业务数据继续可查。
5. 新合同、两个schema和资产key可先构建/测试，但当前factory不能因二进制更新自动激活新W3合同；执行包发布增加可构建但不激活的能力。后续prompt/skill适配及真实验收完成后，在一个明确的新执行包中启用v4。两阶段共用同一套资产，这项要求不能被拆成两套各自Skill规避。
6. 切换时旧在途Case按旧合同收尾，新Case用新合同；不迁移旧Codex历史到Template。结构化72h历史自然连接新旧业务，保留现有一般交易输出权限和CLOSED释放规则。
7. 回退按执行包恢复新Case的旧合同入口，不回滚已提交交易、已完成运营或业务表。已创建v4Case按冻结合同收尾，暂停新合同入口不等于强杀旧在途任务。

本轮方案交付后，代码实施应交付：变更及changelog、迁移与兼容测试、工程链路验收记录、一次性SDK生命周期证据、前端实际浏览器验证，以及清晰列出的prompt/skill专项和真实模型验收依赖。本轮未实施上述动作，未新建模型会话、未执行会话删除、未迁移或部署。
