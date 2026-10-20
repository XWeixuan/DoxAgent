# W3 单一方向决策与终态记录重构实施方案

日期：2026-10-21。状态：**代码审查与方案，尚未实施业务修改**。

目标：彻底取消 W3 的 POLICY_TRADE / EXPERT_TRADE 区分，统一为 LONG / SHORT / NO_TRADE；W2 独立负责 Policy HIT/RELATED；W3 二轮仍负责最终 NEW/OLD；消除正常无额外产物 Case 的“结果未记录”。

提示词对齐说明见：[新编排与 Schema 简要说明](w3_unified_direction_prompt_handoff_20261021.md)。本文是实现依据，两份文档使用同一字段契约。

## 1. 审查基线与范围

本轮本地审查 HEAD 为 `118078bc1c9a48cc45de9a37a2b6c5602b85193a`；同时存在另一会话的启动修复未提交修改，涉及 capsules、jobs、thread_lifecycle、w3_ready、settings、client、compose 等。本文只规划必要的业务契约接入，**不重做该会话的 Fork/boot 超时、资源准入或清理逻辑**。实施前重新读取这些文件的最新版本，不能用本轮快照覆盖其修改。

已经检查：W3 Schema/解析/阶段恢复、Bundle 与 Template/READY 兼容性、W1/W2 Router、Policy 消费与召回、交易输出/执行、CLOSED 候选/Sweep、WATCH/PIN、72h 历史与 RECENT_TRADE、delta/Badcase/coverage gap、日终与维护输入、Read 投影/图/指标、API/前端/导出、离线回测、现行 AGENTS/Skill 及相关测试。

生产 badcase 使用上一轮固定证据 `eval/runtime_missing_result_audit_20261021/`，没有为本轮改写生产。其 17 个“结果未记录”Case 中，13 个后置成功且 OLD/无新增产物，4 个后置失败；其中 2 个原 W2 HIT 曾被 W3 改为 NO HIT。新规则会保留这两个 W2 HIT，不能把这一变化当作纯显示修复。

现行业务契约为 `persistent-runtime.w3.v4`，线程/工具入口为 `w3-entry-v5`，两者不是同一版本轴。

## 2. 本次固定的业务决策

1. 新业务契约使用 **`persistent-runtime.w3.v5`**；继续复用 `w3-entry-v5` 的线程/工具协议，W2 契约与 orchestration_revision=3 不变。
2. 一轮只输出 LONG、SHORT、NO_TRADE。不是将 Policy Trade 合并进 Expert Trade，也不让宿主按 HIT 自动补一笔交易。
3. HIT/NO HIT、RELATED、Policy/Condition 身份、W2 reason/confidence 由原冻结 W2 最终输出确定；W3 两阶段均不得改写。RELATED 不等于 HIT。
4. W3 可以把 W1 NEW 改为 OLD，也可以把 OLD 改为 NEW；二轮有效输出是最终新旧依据，原 W1 原样保留供审计。
5. HIT+NO_TRADE、NO HIT+LONG、HIT+SHORT 都合法。Policy Condition 的建议方向不再是 W3 输出方向的校验条件。W2 为 LOW 时保留 LOW，不让宿主冒充已纠正其归因。
6. 二轮的 OLD、HIT 组合不得撤销、补发或重写一轮交易；二轮在真实新增时必须产出 delta，不能为配合交易方向伪造 NEW。
7. 交易权限、时效、原 Case/Effect/intent 幂等、CLOSED 候选选择、外部执行风控继续生效；它们与模型方向独立。
8. 不改变正常 OLD+HIT 无 WATCH 不进 W3、有效 WATCH/LOW 可进入、RELATED 完整 false 可归档等入口规则；标签仍仅传达信息。
9. 不改变模型、推理强度、超时、研究工具、72h 窗口或周末 Sweep 附加方式。
10. 无额外业务产物的正常终态，宿主补记录；Agent 不增加“归档/兜底”输出，也不新增研究阶段。

## 3. 不能遗漏的现状与风险

| 代码落点 | 已核实的现状 | 本次处理 |
|---|---|---|
| `w3_contracts.py` | 一轮三分支；二轮含可复判 policy | 保留旧模型，新增 v5 模型，按 Case 版本选择 |
| `router.py:route_runtime_case` | OLD+HIT 是否进 W3 用 `!= w3.v4` 分支 | 改为明确的两阶段能力判断，防止 v5 误走旧入口 |
| `service.py` | 两阶段执行、失败记录、完成、Effect key 等多处只认 v4 | 全部审查并接纳 v4/v5；不是全库无差别替换版本字符串 |
| `service.py:_run_w2_hot` | 按 executable_directions 过滤召回候选；WATCH 有例外 | v5 将事实召回与历史交易额度解耦，见第 5.2 节 |
| `w3_execution.py:_trade_record` | Policy 分支选择 Condition、绑定方向/消费；Expert 分支 origin=W3 | v5 统一 origin=W3、方向取根 decision，不设置 executed_policy/condition |
| `trade_output.py:event_trade_key` | 有 w3_trade_decision 的现行两阶段交易直接返回 None | v5 保持此行为，不能顺手新增 Event 自动重复拦截 |
| `w3_execution.py:operations` | 二轮 policy 驱动 Badcase/coverage gap | v5 改读冻结 W2，而不是删除所有反馈 |
| `policy_watch.py:apply_v4_actions` | SET 允许 W2 HIT/RELATED 加二轮新增 HIT | v5 只允许 W2 HIT/RELATED，CANCEL 继续基于可见有效对象 |
| `w3_context.py:actual_policy_hits` | 优先二轮 policy，还兼容旧 POLICY_TRADE | v5 按原 W2；历史维持当时语义 |
| `v2_read/projectors.py` | 二轮 policy 会覆盖最终 Policy；标签靠产物增加 | v5 不覆盖 W2，并加入方向展示及归档收束映射 |
| `api_v2/case_detail.py` | `ops["policy"]`、嵌套 expert_trade、POLICY_TRADE 直接读取 | 双契约适配，避免 KeyError 与错误显示为不交易 |
| `v2_read/policy_hits.py` | Policy 指标的身份选择与 summary 来源分散 | 与 Case 最终 Policy 使用同一个版本化函数 |
| `v2_read/executions.py` | policy_executed 依赖 executed_policy_id | 不伪造新 Policy 执行，不将上下文命中计成已执行 Policy |
| `w3_backtest/report.py` | 新 LONG/SHORT 会落到“无法解析” | 新旧方向归一并保留模型决策/宿主处置的区别 |

## 4. 新 Agent 输出契约

### 4.1 一轮：W3DirectionDecision

四个根字段全部出现、禁止额外字段。新增生产模型时，保持现有 `RuntimeV2Model` 的 closed-object 原则。

```json
{
  "decision": "LONG",
  "reason": "交易判断与证据说明",
  "prior_expectation": "消息前市场所依据的预期",
  "expectation_delta": "新事实如何改变该预期"
}
```

- decision 枚举仅 LONG / SHORT / NO_TRADE；无第二个 direction 字段。
- reason：非空、去空白后仍非空、最多 4000 字符。
- prior_expectation、expectation_delta：string 或 null；非 null 时非空、最多 4000 字符。LONG/SHORT 两项必须为非空字符串；NO_TRADE 可以为 null，也可保留已有有效分析，不强迫编造。
- 删除 policy_trade、expert_trade，不输出 Policy 身份、HIT、NEW/OLD、置信度或执行状态。
- 结构验证与 LONG/SHORT 的跨字段要求由本地 Pydantic 验证；不要为了表达条件约束向供应商 strict schema 塞入不受支持的复杂 if/then。

### 4.2 二轮：W3OperationsResultV5

```json
{
  "novelty": {
    "result": "OLD",
    "reference_ids": ["E12"],
    "reason": "消息核心事实已由 E12 覆盖"
  },
  "delta_candidates": [],
  "watch_actions": [],
  "pin_actions": []
}
```

四个根字段全部出现；从当前五字段模型中只删除 policy。保留现有 novelty、Fact、WATCH、PIN 字段及类型：

- novelty：result=NEW/OLD；reference_ids 是实际加载范围内的 E#；reason 非空、最多 4000 字符。OLD 至少一个合法覆盖引用；NEW 可以有用于比较的引用，不把 Prior 当作实际命中。
- NEW 至少一个 delta；OLD 的 delta 必须为空；最多 12 个。Fact 沿用 proposition/assertion_state/subject_time/occurrence_date/entities，不新增 Event ID。
- WATCH 沿用 action/policy_id/reason；PIN 沿用 action/pin_id/reason。SET PIN 的 pin_id=null，CANCEL 要实际 pin_id。
- 不输出任何交易字段，也不输出只读 W2 的复印件。合法动作的逐项接收与错误隔离沿用现行解析机制；核心 novelty/delta 契约错误仍须纠错重试，不能吞掉。

可供实现直接比对的候选 JSON Schema：

- `eval/w3_unified_direction_plan_20261021/proposed_w3_trade_schema.json`
- `eval/w3_unified_direction_plan_20261021/proposed_w3_operations_schema.json`

它们由本轮独立的候选模型生成，**不是已经上线的新生产 Schema**。

## 5. 可执行修改步骤

### 5.1 先做版本分流，不能先改全局常量后碰运气

文件：`w3_contracts.py`、`execution_bundle.py`、`factory.py`、`service.py`、`router.py`、`w3_execution.py`、`w3.py`、`policy_consumption.py`；路径前缀均为 `src/doxagent/persistent_runtime_v2/`。

1. 保留旧 W3TradeDecision/W3OperationsResult 和旧解析行为；新增 W3DirectionDecision/W3OperationsResultV5。
2. 增加 `is_two_phase_contract(version)`（仅 v4/v5）、`is_unified_contract(version)`（仅 v5）、`contract_models(version)`；解析入口接受明确版本参数。新发布默认 v5，旧直接解析 helper 若需兼容默认 v4，不用“解析失败就换个模型试试”。
3. Bundle publish 按版本生成并冻结两份 Schema 与各自 hash；新 W3 输出版本取实际冻结的 `w3_execution_bundle_id`，不能取当前全局常量。未知新版本在派发前明确报不支持。
4. service 的 hot-lane WATCH 召回、Effect 幂等键、执行器选择、失败分阶段记录、完成判断全部使用相应能力函数。v4/v5 继续使用原 `case_id:W3:trade_decision` 与 post_decision_operations 身份格式。
5. Router 的 OLD+HIT 条件改为仅旧非两阶段契约保留旧入口；v5 与 v4 的 W3 准入完全一致。WATCH 前置路由流程也不变。
6. Template、Case thread compatibility 目前有硬编码 v4，改为实际业务契约 + 两份 schema hash + 既有 assets/profile/capabilities/layout。当前 READY scope 已包含 parent_thread_id，新兼容域会生成新母线程，沿用此隔离即可，不另造一套 Worker 业务版本协议；测试确认原母线程不可被新 Case 领取。
7. `w3.py` 的 task.contract_version、schema 选择、冻结恢复解析与引用校验统一按版本；新模型没有 result.policy/result.policy_trade，不能留下直接属性访问。
8. 接入另一会话启动修复时只补业务兼容 key 与版本参数，保留其 timeout、startup admission、owner/receipt、回收和取消修复。

验收：同进程运行旧 v4 与新 v5，旧 Case 仍读旧 Schema/Prompt/Thread；v5 不串旧 READY，也不因新字符串退回旧 service 流程。

### 5.2 固定 W2 权威，同时解除交易额度对事实召回的限制

文件：`service.py`、`w3_context.py`、`w3.py`、`bounded_inputs.py`、`w3_models.py`。

1. 新 v5 在 `_run_w2_hot` 中直接使用冻结、正式发布的 Policy projection 作为 R1 候选全集，**不再按 executable_directions/consumed_directions 过滤**。仍然只包含该发布版本本来有效的 Policy，不复活退休 Policy，不读未来版本。
2. 新 v5 给 W2R2 的 policy_details 不附交易消费/可执行额度提示；Policy 定义与 Condition 身份仍完整。冻结原 usage 供审计、历史契约读取即可，不当作新 HIT 的判断依据。W2 模型、推理强度、R2/R3 Schema、候选身份校验和输出重试不变。可召回的有效 Policy 可能增加，要记录实际输入 token、缓存命中与延迟变化；不为控制输入量重新按交易额度过滤。
3. Case 准入时先确定实际 W3 bundle/version，再做版本相关的输入裁剪和冻结。不能先按旧额度删掉 Policy，后面才发现是 v5；输入缓存/预热若复用该召回投影，确保与实际 v5 输入及 cache key 一致。旧 Case 仍用原 round_inputs，不能重建其候选全集。
4. W2 Final 一经成功冻结，不被 W3 改写；其 HIT/RELATED/Policy/Condition/reason/confidence 原样保留。新 v5 不执行旧的“消费后删除 R2 命中/重置为 no HIT”兜底。
5. v5 W3 user input 继续提供完整 w2 和实际 HIT/RELATED 的 Policy 定义；删除面向 Agent 的 policy_usage 执行额度视图，避免把已退出的新交易门槛重新变成提示词隐性要求。D1/D2/D3/reference/研究工具与实际引用范围不缩减。
6. `freeze_case_profile()` 继续按冻结 W2 HIT 或 RELATED 选择现行模型；不能因为删除 Policy Trade 就将所有 Case 改为普通模型。

这是必要的配套业务改变：**已消费的有效 Policy 仍能被判 HIT，但不会因此获得自动交易权**。历史消费/rearm 记录不清零、不迁移、不删除；仅新 v5 不把它们用作事实召回或交易审批条件。

### 5.3 一轮只构造一类 TradeRecord

文件：`w3_execution.py:_trade_record/trade/_restore_trade`、`trade_output.py`、`schema.py:TradeRecord`。

1. v5 NO_TRADE 返回 `(None, None)`；LONG/SHORT 统一构造 `TradeRecord(decision_origin=W3,w3_case_id='w3:'+case_id,decision=根 decision)`。
2. 不设置 executed_policy_id、executed_condition_id、revision、usage_generation，也不将 Condition 方向填成模型方向。原 W2 完整保存在 w2_result，必要的上下文 policy_ids 来自该原始结果，不能命名成 executed_policy。
3. v5 不调用 Policy 专属 prepare_output_guard/claim_ready；保留旧 v4 POLICY_TRADE 分支和未完成旧 intent 的方向消费机制。
4. TradeOutputService 的控制权限、时效、fencing、同 intent 原子幂等、delivery/reconcile、外部执行 profile/仓位控制不变。统一内部 origin=W3 是历史来源枚举的复用，不再代表名为 Expert Trade 的业务模式。
5. 保留 `event_trade_key()` 对两阶段 w3_trade_decision 返回 None；不新增基于 E#、HIT、RECENT_TRADE 的自动交易拦截。CLOSED 同方向重复释放等既有选择规则保留。
6. `_restore_trade` 对新 LONG/SHORT 设决策路由 TRADE，NO_TRADE 设 ARCHIVE；output_disposition 单独保留。模型建议 LONG 但宿主不允许/过期/只分析，不能改写为 NO_TRADE。
7. 一轮提交交易阶段、Case 与 operations Effect 的原子边界不变；二轮仍不能更改一轮结果、方向或 intent。

### 5.4 二轮反馈改用最终新旧 + 原 W2，保留所有有用产物

文件：`w3_execution.py:operations`、`policy_watch.py`、`message_pin.py`、`schema.py`。

固定业务公式：`final_novelty = committed_operations.novelty`；`final_policy = frozen_w2`。未有有效 operations 时，新旧暂以 W1 显示并明确来源/阶段状态，不能称为二轮已完成。

| 二轮有效结果 | W2 | 仍应生成的业务产物 |
|---|---|---|
| NEW，有 delta | HIT | provisional delta；不因 HIT 自动交易 |
| NEW，有 delta | 无 HIT（包括仅 RELATED） | provisional delta + 原有 coverage gap |
| OLD，无 delta | HIT | 原有 OLD+HIT Badcase 反馈；它描述旧事实命中，不宣称 HIT 一定判错 |
| OLD，无 delta | 无 HIT（可 RELATED） | 无候选/Badcase；符合 5.5 条件时归档兜底 |

1. Badcase 的 hit_policy_ids/w2_reason 取 W2；coverage gap 的触发依据为 `not w2.policy_hit`，不是 `not policy_related`。
2. 新 coverage gap 的 result 写 v5 operations + 原 w2_assessment + 已提交 trade_decision + result contract 标记；不伪造一个 Agent 输出的 policy 对象。`W3CoverageGapRecord` 当前支持 JsonObject，外层 O3 feed 不新增版本；daily/maintenance 的实际序列化消费者加新旧 fixture，确认不直接索引 expert_trade/policy。
3. `apply_v5_actions` 可复用 v4 动作处理核心，但 SET 允许集只为原 W2 HIT ∪ RELATED 且仍属于冻结 Policy；不能依赖已删除 result.policy。CANCEL 仍只处理当前可见、身份一致的 WATCH，不要求取消对象本轮再次命中。
4. WATCH 保持容量 2、D/D+1、重复不续期、同批 CANCEL 优先、成交/消费不自动取消；两类用途及 CLOSED 候选资格不变。PIN 保持容量 2、24h、只 SET 当前消息、CANCEL 用实际 id，不新增 W3 入口。
5. novelty 与 delta 的严格关系、允许 E# 范围、逐项动作错误隔离与真实错误纠正机制保留；不接受把引用写在 reason 里代替 reference_ids。

### 5.5 正常无额外产物时补 ArchiveRecord，不能把失败当兜底成功

文件：`w3_execution.py`、`repository.py`、`schema.py:ArchiveRecord`；增加必要的内存实现对等行为。

**新 v5 的归档兜底条件全部满足才执行：**

- trade_decision 已 COMMITTED，正式 decision=NO_TRADE；
- operations 已获得有效结果且本次将提交 COMMITTED；
- 无 trade intent、analysis decision、CLOSED trade candidate、Fact candidate、Badcase、coverage gap 等独立业务结果；WATCH/PIN 是上下文动作，不取代 Case 终态结果；
- 未有该 Case 的 ArchiveRecord。

正常 NEW 由 Schema 保证 delta 非空，会有候选；真正需要兜底的典型是 OLD+无 HIT。LONG/SHORT 即使交易未成交，也有 intent/分析决策/CLOSED 候选等正式证据，**不补归档伪装为不交易**。

实现：

1. 复用 runtime_v2_archives 的 case_id 唯一性，不新建通用终态表。
2. ArchiveRecord 新增可选 `result_basis`，历史默认 null；包含 `novelty: NEW|OLD`、`novelty_source: W1|W3_OPERATIONS`、`policy_hit: bool`、`policy_related: bool`、`w3_decision: NO_TRADE`。W2 具体身份/版本仍从原 Case 冻结输入读取，不再复制全套 Policy。Agent 不输出此对象。
3. 本 fallback 的 novelty_source 实际必须是 W3_OPERATIONS；W1 取值仅留给普通一轮归档的记录增强，不能在失败/尚未完成的 W3 路径里冒充最终结论。reason 记录已提交不交易原因与最终归档依据，不改原 W1/W2。
4. 在原 operations commit 事务中插入幂等 ArchiveRecord，再一起提交 operations/Case。新增可复用的 repository 同连接插入 helper，避免持写锁再开连接。内容编码在写事务前完成，保留上一轮 SQLite 短事务与租约 fencing 修复。
5. 产物存在性检查用 Case/intent/candidate 的确切键、已有 source_message_id 索引或明确 candidate identities；不在每个 Case 收束时扫描 JSON 大表。
6. 重试后仍只有一条 archive；不得提前在一轮 NO_TRADE 时写归档，否则二轮迟到的 NEW/Badcase 会留下假终态。
7. operations PENDING/FAILED 时仍可展示一轮 NO_TRADE，但不补“二轮正常完成”的归档，不更改真实后置错误。

### 5.6 72h 历史、线程关联、RECENT_TRADE 和 Policy 指标统一来源

文件：`w3_context.py`、`w3_threads.py`、`v2_read/policy_hits.py`、`v2_read/projectors.py`、`v2_read/executions.py`。

1. 增加一个轻量、版本化的只读判断 helper（建议 `case_outcomes.py`）：有效 novelty 及来源、有效 Policy 及来源、方向归一。运行/Read 共享业务定义，禁止写入或发起额外查询。
2. v5 `actual_policy_hits` 只读 W2；v4/更旧历史继续遵循原契约的 W3 复判/Policy Trade 兼容读取，不能升级后默默把旧 NO HIT 改回旧 W2 HIT。
3. event_matches 仍以最终有效 OLD 的实际 E# 为准；NEW 的 Prior 引用不变成 Event 命中。Thread 仍以真实 W2 HIT/RELATED 关联，兼容 key 隔离新旧业务契约；不改生命周期/TTL。
4. RECENT_TRADE 继续按 72h 内正式意图的同类对象确切身份匹配，不要求成交；RELATED 不算实际 HIT，不做文本相似度，也不自动拦截交易。旧历史方向归一仅用于读取，不重写旧回执。
5. 新 Case 的 final_policy_hit、policy_hit_set、policy_ar_hits 统一使用原 W2 HIT/身份，不能一处读 W2、一处读已删除 ops.policy。Policy 发布 revision 与原统计去重口径不变。
6. 新交易不填 executed_policy_id，所以不新增旧 policy_executed 或 Policy 方向消费；已有历史计数保留。不能将“该 Case 命中过 Policy”冒充“执行了该 Policy”。不新增命中交易转化 KPI 以扩张本轮范围。

### 5.7 Read / API / 前端 / 导出一起改，不能只改中文文案

文件：`v2_read/projectors.py`、`trade_outcomes.py`、`graph.py`、`policy_hits.py`、`api_v2/case_detail.py`、API 共享 TS 契约及前端 runtime/runtime-case/runtime-case-export。

1. 共享契约源是 `dev_plan/workflow_v2/api_contract/doxagent-v2-api.types.ts`；保留旧 W3TradeDecision 类型，新增 W3DirectionDecision，W3Detail.decision 为明确的新旧联合。前端按 decision 枚举收窄，不能直接访问新对象的 expert_trade/policy_trade。
2. CaseSummary 新增可选 `w3_decision: Value<LONG|SHORT|NO_TRADE>`、`final_novelty_source: W1|W3_OPERATIONS|null`、`final_policy_source: W2|LEGACY_W3|null`。未进入 W3 不填假 NO_TRADE；旧 Read 行缺字段仍可读，重投影后填齐可确认的信息。
3. W3Detail 新 v5 提供根决策、平铺 prior/delta、原 W2 Policy 结果和二轮 novelty/operations 状态。保留旧 expert_trade_* 字段仅供历史 DTO 兼容，新 v5 填 NOT_APPLICABLE，不伪造“专家交易 true”；增加中性 reasoning.trade，旧 reasoning.expert_trade 仅旧 Case 使用。
4. API 不再对新 operations 直接索引 policy；PolicyDetail 访问权限仍根据原 W2 HIT/RELATED、冻结候选与允许历史来源检查，不因删除 selected Condition 就放开到所有 Policy。
5. 列表直接展示已提交 LONG/SHORT/NO_TRADE，并保留实际 EVENT_DISCOVERY/ARCHIVE/BADCASE/TRADE_INTENT 标签；NO_TRADE 旁边仍可显示 W2 HIT。新 Case 未有标签时，以真实阶段状态区分后置处理中/失败，不能显示“未记录”遮蔽已提交决策。
6. 新 fallback archive 通过现有记录投影进入 ARCHIVE 结果节点/指标，不能只在详情页改字。图连线依实际经过节点，不让补归档产生不存在的 W3 研判。
7. 历史类似 13 个正常 NO_TRADE+operations COMMITTED+无产物的 v4 Case，可在**Read 重投影**中保守补派生 ARCHIVE 分类：只对原始一轮已提交 NO_TRADE、原始二轮已提交 OLD 且按原契约无 HIT、无其他结果标签的可确认样本生效。读取它当时的有效 novelty/Policy，不修改原 DB、不改历史 Policy 判定；数据缺失/来源投影不完整不当作“没有产物”，其 operations FAILED 的 4 个仍保留失败，不派生“全流程成功”归档。
8. 模型 LONG/SHORT 与宿主 ANALYSIS_ONLY/EXPIRED/CANDIDATE/READY/拒绝原因分别显示；CLOSED 候选尚未释放不算 NO_TRADE，也不派生 archive。
9. 导出将新 Case 的“专家交易判断”改为“交易判断”，“Policy复判”改为“W2 Policy判定（继承）”；原始 JSON 决策对象和新旧来源都保留，导出旧 Case 不能丢历史嵌套字段。
10. 先改共享 TS，再同步前后端验证 Schema。前端 `pnpm schema` 只生成前端 wire-schema；本轮未发现后端的一键生成脚本，因此显式同步 `src/doxagent/api_v2/wire_schema.json` 中 CaseSummary、W3Detail、新决策、reasoning 及新增 Value 定义，保留其余手写标量约束，更新为 TS 文件真实 SHA-256。为这些变更定义增加新旧 payload 等价验证，不能仅改 hash 或只改前端产物；本轮无需新建通用 Schema 生成平台。

前端按 `frontend/v2/AGENTS.md` 保持现有紧凑布局，不加版本徽标或解释性常驻组件；浏览器实际验证 1262px、1559px。

### 5.8 CLOSED、回测、日终/维护与历史恢复

1. `trade_output.py`、`selection.py:WeekendSelection` 及 coordinator 的 CLOSED/Sweep 调度继续使用内部 TradeRecord.decision=LONG/SHORT；不改变候选选择 prompt 附加机制和同方向释放规则。验证旧 Policy 候选与新统一候选可共存，旧候选只按原来源消费，不能按最新契约反推。
2. `w3_backtest/runner.py` 默认新回放 Case 使用 v5，并冻结新 AGENTS/Skill/两份 Schema；已有 run 从原 manifest/Case 版本恢复，不因 W3_CONTRACT 默认更新而重解释已运行结果。
3. `w3_backtest/report.py:normalize_decision` 直接接受新 LONG/SHORT/NO_TRADE；旧 Expert 按旧 direction、旧 Policy 按冻结 Condition 解析；无法确认历史方向时保持技术缺失，不默认 LONG。Gold 不重标，不把宿主拒绝算成模型 NO_TRADE。
4. 离线回测仍禁止真实 web/IBKR/交易动作，复用旧证据与价格文件，不因本轮统一决策扩大权限。
5. `daily.py`、`maintenance.py` 与 Document3 消费 fixture 验证新 coverage gap 的 JsonObject 可序列化且保留原 W2；归档兜底不产生新 delta、不把 OLD 重新送为新事实。
6. 旧 v4 交易/operations 已分别提交、只完成前一阶段、失败重试、SDK 结果已回但宿主未提交等场景，全部依据旧冻结版本恢复；不将旧 intent 重签成新方向，也不重放过期历史交易。

## 6. 发布与迁移顺序

1. 本地先完成版本分流和读端新旧兼容，再接入新运行路径、产物/兜底、上下文/指标、回测和前端。重要代码修改追加 changelog；保留其他会话的未提交修改。
2. 提示词对话依据本轮简要说明更新 W3 AGENTS.md + 唯一 Skill；本轮不修改 W1/W2 的模型、提示词或研究方法。若 W2 提示词另有“已消费不得判 HIT”的约束，先删除这一与新权威定义冲突的句子，不能保留矛盾指令上线。
3. 同一发布物冻结新代码能力、新 W3 prompt/skill、两份 Schema 及版本/hash。启动时不要用新 parser 运行旧 prompt、也不要用新 prompt 恢复旧 Case。
4. 发布后先准备新兼容域的 Template/READY，再切对应 ticker 的 `w3_entry_active` 到新 bundle；不能只更新 `execution.active` 就宣布 W3 已切换。W2 新候选范围的启用以准入 Case 最终冻结到的 W3 版本为准。
5. 旧已绑定/运行的 Case、CLOSED 候选、线程和 intent 继续旧协议收束；旧版本解释器保留，不清库、不覆盖历史 Snapshot、不强制归档旧 slot。
6. Read 先支持新旧 wire，再允许新 v5 准入；API/前端都更新后执行有界、可重入 Read 重投影，更新 case/graph/metric 贡献并移除旧贡献，避免只修主列表、图仍缺分类。不得在 HTTP GET 触发全量修复。
7. 回滚必须回到**能读取 v5 的兼容版本**并停止新增 v5 准入；已有 v5 按冻结契约收束。不能直接降级到只认识 v4 的旧镜像。

## 7. 必须通过的回归矩阵

| 编号 | 输入/故障 | 必须确认 |
|---|---|---|
| T01 | LONG/SHORT/NO_TRADE 三个合法一轮对象 | 方向平铺，无交易模式分支 |
| T02 | v5 输出 POLICY_TRADE/EXPERT_TRADE、旧嵌套字段或 policy_hit | 明确契约失败并沿现有纠错，不偷偷转成合法输出 |
| T03 | LONG/SHORT 缺 prior/delta；NO_TRADE 两项 null | 前者拒绝、后者接受；reason 不得空白 |
| T04 | HIT+NO_TRADE | HIT、Policy/Condition 身份和统计不被清掉 |
| T05 | NO HIT+LONG；HIT Condition LONG 而 W3 SHORT | 可合法形成新 W3 方向提案，不被旧 Policy 执行分支否决 |
| T06 | 原 W2 HIT 与 RELATED 多 Policy 混合 | 全部原样保留、RELATED 不计 HIT |
| T07 | W2 LOW | 保留原置信度；W3 不改归因，可独立决定方向 |
| T08 | W1 NEW→二轮 OLD，或 OLD→NEW | 终态用有效二轮；原 W1 不覆盖；NEW 必有 delta |
| T09 | 二轮 OLD 但一轮 LONG/SHORT | 已提交交易不撤销，仍可产生对应运营反馈 |
| T10 | 正常 NO_TRADE+OLD+无 HIT+无额外产物 | 一条 archive，result_basis 正确，列表/图/导出不显示未记录 |
| T11 | NO_TRADE+NEW+delta，或 OLD+HIT | 保留候选/coverage gap/Badcase，不额外重复归档 |
| T12 | WATCH/PIN 唯一动作 | 动作成功不取代正常 Case 归档，TTL/容量不变 |
| T13 | operations pending/failed/非法引用 | 不补成功 archive；已提交决策仍可见、后置错误仍可见 |
| T14 | 输出权限拒绝、过期、CLOSED candidate、执行未成交 | 模型方向与真实 disposition 分离，不伪装 NO_TRADE/归档 |
| T15 | operations commit 中断、归档 insert 后回滚、重复恢复 | 原子回滚、最多一条 archive、无额外交易/动作 |
| T16 | Policy 所有方向已消费、Policy 仍在有效 projection | v5 R1 仍可召回/判 HIT；旧 Case 不变、不复活退休 Policy |
| T17 | v5 对同 Policy 连续合法提案 | 不写新 Policy 消费，也不由 RECENT_TRADE/HIT 自动拦截 |
| T18 | 相同 E# 的新统一交易 | 不误启用旧 event_trade_key 自动拦截；原 Case 幂等仍有效 |
| T19 | W2 HIT/RELATED SET WATCH；新造未归因 Policy | 前者可按现行规则处理，后者拒绝；可见对象 CANCEL 不受本轮 HIT 限制 |
| T20 | 72h 新旧交易混合、仅 RELATED、NEW 的 Prior E# | 精确对象匹配；历史语义不被回写；RELATED/Prior 不算 HIT |
| T21 | 正常 OLD+HIT 无 WATCH；WATCH/LOW；RELATED完整false/不完整 | 准入与当前一致，换版本号不改变 Router |
| T22 | v4 与 v5 同时运行/恢复 | Schema/Prompt/Template/READY不串；失败记录和完成判断都正确 |
| T23 | 原 Policy Trade 待消费/投递、旧 CLOSED候选释放 | 原消费与风控仍生效，无新旧重复意图 |
| T24 | 前端旧 DTO/新 DTO、根 LONG/SHORT | 无 KeyError/TS错误；新 Case 不出现“专家交易模式/Policy复判” |
| T25 | Policy指标、graph归档、过滤与导出 | 原 W2 HIT一致、历史不翻案；新命中不伪造 policy_executed |
| T26 | 历史13成功/4失败的Read回填 | 仅有证据的正常收束补分类；错误完整保留、不重写原DB |
| T27 | 回测新旧输出、宿主拒绝、空Gold | 正常归一评分，未标注不当负样本，不把建议变成实际执行 |
| T28 | delta/gap 日终、维护、O3 feed | 新内容可读，旧内容不损坏，无假新增事实 |

建议新增 `tests/test_w3_v5_contracts.py`、`tests/test_w3_v5_execution.py`、`tests/v2_backend/test_w3_v5_projection.py`，并扩充现有 W3 v4、Router/W2、WATCH、trade outcomes、Read graph、backtest、single skill/READY 兼容测试。没有新增算法的前端标签不写实现镜像式测试，重点覆盖上表真实业务分支。

建议运行范围：

```powershell
.venv/Scripts/python.exe -X utf8 -m pytest tests/test_w3_v5_contracts.py tests/test_w3_v5_execution.py tests/test_w3_v4.py tests/test_w3_case_models.py tests/test_w3_resources_v5.py tests/test_w3_single_skill.py tests/test_w3_backtest_runner.py tests/test_w3_backtest_reports.py tests/v2_backend/test_w3_v5_projection.py tests/v2_backend/test_w3_v4_projection.py tests/v2_backend/test_trade_outcomes.py tests/v2_backend/test_snapshot_graph_equivalence.py tests/v2_backend/test_wire.py
```

再按实际修改覆盖既有 W2/Router、Policy方向消费、CLOSED/Sweep、启动兼容测试；避免为通过与本轮无关的消息采集基线失败改业务代码。前端在 `frontend/v2` 执行 `pnpm schema`、`pnpm typecheck`、相关 Vitest、`pnpm build`，并进行规定尺寸的浏览器实际验收。

## 8. 本轮已做的方案验证及后续验收边界

本轮仅对候选模型执行了 **28 项 Schema/语义边界检查，全部通过**。脚本和结果：`eval/w3_unified_direction_plan_20261021/check_proposed_contracts.py`、`proposal_contract_checks.json`。这验证四字段输出及 NEW/OLD 约束可表达，**不代表生产代码已修改，也不代表上述运行/发布回归已通过**。

实施完成后的验收应分别提供：离线测试、真实新/旧混跑、实际生效 bundle/hash、正常 fallback 原始记录、失败 operations 未被掩盖、CLOSED 候选未被假归档、前端与图统计一致。真实付费回测按实施任务授权执行，本轮没有发起。
