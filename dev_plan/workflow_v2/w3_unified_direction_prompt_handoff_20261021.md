# 给提示词对齐对话：W3 统一方向决策与新版 Schema

日期：2026-10-21。**本文描述待实施目标，不表示生产已切换。**代码与提示词必须按同一版本发布。

对应详细方案：[W3 重构实施方案](w3_unified_direction_refactor_implementation_plan_20261021.md)。目标业务契约 `persistent-runtime.w3.v5`；现有 `w3-entry-v5` 是工具/线程入口名，不是本次新增的业务 Schema 版本。

## 1. 这次究竟改什么

彻底取消 `POLICY_TRADE / EXPERT_TRADE` 两种提交模式，**不是把一种模式归入另一种**。第一阶段只有 **LONG / SHORT / NO_TRADE** 三个选择，模型不选择 Policy 交易模式、不提交用于执行的 Condition。

三个判定维度独立：

| 维度 | 权威来源 | W3 能做什么 |
|---|---|---|
| 消息是否新增 | W1 初判，W3 二轮最终修正 | 二轮输出 NEW/OLD 与真实新增事实 |
| Policy HIT/NO HIT、RELATED 及身份 | 本 Case 原 W2 最终结果 | 只读，不增删、不覆盖、不重新输出 |
| 交易方向 | W3 第一阶段 | LONG / SHORT / NO_TRADE |

HIT+NO_TRADE、NO HIT+LONG、HIT+SHORT 都是合法组合。即使 W2 命中的 Condition 建议方向不同，W3 的方向仍独立；它需要有自己的理由，但宿主不会强制跟随 Condition。HIT 是已形成的业务归因，不是交易指令或交易额度。

如果 W2 置信度 LOW，W3可以把其不确定性作为研究/交易判断的背景，但不能假装重新裁定了 HIT。历史已消费 Policy 仍可能被新 Case 判为 HIT；新统一交易不绑定或消费 Policy 方向额度。已有交易历史仍须阅读，不能把“没有额度门槛”理解为无需判断重复机会。

## 2. 现有编排保留什么

- 两阶段仍属于同一个 Case，沿用已有 thread/上下文关联与阶段恢复机制。第一阶段先提交，第二阶段继续；不会为取消模式而新增模型轮次。
- 仍只注入 AGENTS.md + 唯一 skills/skill.md；周末 Sweep 的附加逻辑保留在原 Sweep 任务。
- 仍通过当前阶段 user input、context_ref 和现有研究 MCP 取得消息、W1/W2、命中/相关 Policy 定义、D1/D2/D3、Reference/已加载 Event、provisional、72h 历史、WATCH/PIN、行情与来源。不要因为没有 Policy Trade 就跳过必要的研究，也不要为普通 Case 强制穷举全部 Policy。
- 当前消息的 case_type/additional_tags 仍只是信息；HIT/RELATED 类型、LOW、WATCH、RECENT_TRADE 不自动交易、不自动拦截，也不决定工具权限。
- 原 W2 对象保留 policy_hit、policy_related、policy_ids、hit_condition_ids、related_policy_ids、reason、confidence；一条消息可同时 HIT A、RELATED B。
- 新版不再把 policy_usage 的 executable/consumed 视图作为 Agent 交易输入。不要在 Skill 中重建“先挑未消费方向再决定交易”的路径。
- 同一 Case 的第一阶段已经正式提交后，二轮不得改写方向、撤销交易、补发交易、从 NO_TRADE 升级为 LONG/SHORT。

模型选择、effort、超时、行情核验方法、来源核验深度与现有研究工具不在本次改动范围。

## 3. 第一阶段：统一 Trade Decision

只输出以下四个根字段，全部必须出现，禁止额外字段：

```json
{
  "decision": "LONG",
  "reason": "新事实、传导与当前交易判断的理由",
  "prior_expectation": "消息出现前的关键市场预期",
  "expectation_delta": "这条消息造成的关键预期修正"
}
```

不交易示例：

```json
{
  "decision": "NO_TRADE",
  "reason": "消息不足以支持当前交易选择的具体原因",
  "prior_expectation": null,
  "expectation_delta": null
}
```

字段规则：

- decision 仅 LONG / SHORT / NO_TRADE；方向直接在 decision，不另设 direction。
- reason 非空、最多 4000 字符。
- prior_expectation 与 expectation_delta 为非空字符串或 null；非 null 最多 4000 字符。LONG/SHORT 两项都必须给出；NO_TRADE 可以 null，也可保留已形成的有效分析，不为填字段制造分析。
- 不输出 policy_trade、expert_trade、policy_execution、policy_hit、novelty、confidence、交易提交状态或内部对象 ID。

提交 LONG/SHORT 前，仍执行现有必要核验；不要把“Schema 简化”解释为交易标准降低。NO_TRADE 也是真实、完整的一轮结果，不需要在二轮再补一次交易。

LONG/SHORT 是模型方向建议，真实释放仍可能为 CLOSED 候选、仅分析、过期或控制层拒绝；宿主会单独提供实际 disposition。不能在二轮把宿主拒绝改写为模型当时选择 NO_TRADE。

## 4. 第二阶段：只做新旧与事实/上下文运营

输入继续提供 submitted_decision、output_disposition、必要的 candidate_eligibility、刷新后的 WATCH/PIN 与当前阶段 context_ref，原 W2 是只读依据。

新版仅四个根字段：**novelty、delta_candidates、watch_actions、pin_actions**。删除旧 policy 字段。

正常 OLD、无需动作示例：

```json
{
  "novelty": {
    "result": "OLD",
    "reference_ids": ["E12"],
    "reason": "实际已加载的 E12 覆盖了当前消息核心事实"
  },
  "delta_candidates": [],
  "watch_actions": [],
  "pin_actions": []
}
```

NEW 与运营动作的结构示例（E#、Policy、PIN 必须替换为本 Case 实际对象，不可照抄）：

```json
{
  "novelty": {
    "result": "NEW",
    "reference_ids": [],
    "reason": "本次新增事实未被已知事实覆盖"
  },
  "delta_candidates": [
    {
      "proposition": "某主体已正式宣布一项新的现实进展",
      "assertion_state": "ACTUAL",
      "subject_time": null,
      "occurrence_date": null,
      "entities": ["相关主体"]
    }
  ],
  "watch_actions": [
    {
      "action": "SET",
      "policy_id": "本Case的真实W2 HIT或RELATED Policy ID",
      "reason": "符合现有WATCH用途，需要跟进的具体问题"
    }
  ],
  "pin_actions": [
    {
      "action": "SET",
      "pin_id": null,
      "reason": "本次消息作为后续解释背景的持续重要性"
    }
  ]
}
```

业务约束：

- NEW 必须有 1—12 个 delta；OLD 的 delta 必须为空且 reference_ids 至少一个真实覆盖的 E#。用 reason 写出 E# 不能替代结构化 reference_ids。
- reference_ids 只能取宿主实际提供的允许范围，不能用 F#、PIN、消息 ID 或 URL 替代；不为满足 OLD 硬编引用。
- Fact 字段完全沿用现有五字段；proposition 非空、最多 4000 字符，subject_time 可 null、最多 500 字符；occurrence_date 为日期或 null；entities 为字符串数组，最多 32 项。assertion_state 沿用现有 Schema 枚举，不将预测、计划、传闻写成已实现事实。
- NEW/OLD 可以与原 W1 不同；二轮应维护真实事实，不因一轮交易了就强制 NEW，也不因一轮 NO_TRADE 就强制 OLD。
- 不输出 HIT/RELATED 复判，不在 reason 之外制造一个“修正后的 W2”对象；原 W2 结论不因交易与否或最终 NEW/OLD 变化而变化。

## 5. “无额外产物”由宿主解决，不能要求 Agent 凑输出

W3 一轮已形成 NO_TRADE，二轮正常判为 OLD、无 Policy HIT、无 delta/其他独立产物时，宿主会保存归档终态。最终新旧取有效二轮结果，Policy 取原 W2；不是强制回退原 W1。

因此：

- 不为避免“结果未记录”输出多余 Fact、虚假 NEW、伪造 Policy 或没有用途的 WATCH/PIN。
- 不新增 archive/result/final_status 等字段；归档由宿主确定性生成。
- 后置失败与正常无产物不同。宿主仍保留已提交交易判断，并单独记录后置失败，不能靠一个正常 OLD 对象遮盖未完成的工作。

其他反馈也由宿主形成：最终 NEW+无 W2 HIT 的覆盖缺口、最终 OLD+W2 HIT 的既有反馈，以及实际 delta。RELATED 不变成 HIT；它们均不触发额外交易。

## 6. 提示词文件具体对齐清单

只重构当前 W3 `AGENTS.md` 与唯一 `skills/skill.md` 的相关内容，继续保留现有研究方法、来源纪律和工具用法。

1. AGENTS 中三分支提交说明改成 LONG/SHORT/NO_TRADE；删除“Policy Trade 被拒再补 Expert”等已经不存在的模式描述，保留不得补发交易的原则。
2. Skill 中原 Policy/Expert 分支表改成统一四字段说明；同一套研究判断直接得出方向，Policy 与实际 Condition 只是输入事实/预期依据。
3. 行情核验的触发对象从“POLICY_TRADE 或 EXPERT_TRADE”改成“准备提交 LONG 或 SHORT”；核验要求本身不降级。
4. 二轮章节删除 policy 输出与复判权限，改为继承 W2；WATCH SET 对象同步收窄为 W2 HIT/RELATED。
5. 二轮输出根字段从五个变四个；更新全部 JSON 示例，禁止保留旧字段的 null 占位。旧契约的历史回执仍可作为历史数据读取，不能照着旧格式提交新输出。
6. 不加新交易模式、置信度、评分、阶段、内部 ID 或归档字段；根字段及交叉约束必须与实际 output_schema 一致。
7. 保留“最终 OLD 不撤销一轮方向”“NO_TRADE 不抹去 W2 HIT”“NEW 不等于必须交易”的明确说明。

## 7. 交付自查

至少能正确解释这些情景：

- HIT+NO_TRADE：保留命中，明确不交易；二轮正常做事实运营。
- NO HIT+LONG：仍可交易；没有 Policy 执行分支或 Condition 选择。
- HIT+SHORT 而 Condition 原方向为 LONG：宿主不机械改方向；Agent 说明自己的真实判断。
- 一轮 LONG、二轮 OLD：保持一轮 LONG，不伪造 delta。
- W1 NEW、二轮 OLD、NO_TRADE、无 HIT：真实 OLD 引用、空 delta，宿主归档。
- 只有 RELATED：仍是 NO HIT，不能把 related_policy_ids 填成执行 Policy。
- 二轮失败：既有方向不变；不能重发一轮或编造正常结束对象。

候选严格 Schema 文件位于 `eval/w3_unified_direction_plan_20261021/proposed_w3_trade_schema.json` 与 `proposed_w3_operations_schema.json`。28 项候选契约检查已通过，但生产实现尚未开发；正式上线必须以同批冻结的 output_schema 为准。
