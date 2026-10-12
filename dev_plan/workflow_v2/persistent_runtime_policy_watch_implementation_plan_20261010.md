# Policy 临时 WATCH 最小实现方案

日期：2026-10-10。本文件只规划后续实现；本轮不修改业务代码、prompt/skill、消费开关或运行状态。

## 1. 目的与范围

W3 对因 Policy HIT/相关而进入研判的消息，可以判断“当前不交易，但存在明确、短期内可能补齐的证据缺口”，为对应 Policy 建立临时 WATCH。

WATCH 有效期间，后续消息只要被 W2 判断为同一 Policy 的 HIT 或相关，就进入 W3，并明确携带此前的关注原因；W1 新旧判断、W2 R3 新事实对齐结果不再阻止这次路由。

WATCH 在来源 case 当前及下一个语义日有效，过期自然失效。Policy 被消费时自动取消；W3 也可以主动请求取消。

**WATCH 只改变研判路由，不改变消息的新旧事实，不代表交易许可，不解冻已消费 Policy，不修改 D3 正文。** 不增加主动抓取、定时研究、额外 LLM 调用、续期任务或独立工作流。

## 2. 当前接入点

- `src/doxagent/persistent_runtime_v2/schema.py`：W3CaseResult、RuntimeCase/W3RouteCase 的结果与上下文合同。
- `service.py`：W2 完成后调用 route_runtime_case，构造 W3 case；W3 返回后接受结果并执行副作用。
- `router.py`：现有路由判断；当前 Policy HIT 已通常进入 W3，WATCH 主要补齐 OLD + 相关、以及相关但未通过新事实对齐的路由，并为所有 WATCH 跟进提供显式原因。
- `w3.py`：生成 case 专属 task.json，注入原 W1/W2 判断及上下文。
- `trade_output.py`：READY 输出和 Policy 消费的原子事务；CLOSED 候选先保存，最终选择释放时才进入该事务。
- `journal.py`：已有 runtime_values、事务、时钟与 lease fencing，直接复用。
- `semantic_clock.py`：美东每日 02:00 的语义日边界。

本地相关源码正有其他开发中的改动；实施时以届时实际调用链为准，合并进入上述已有能力，不覆盖其他任务。此方案不要求重新设计 W2 R3 或 D2/D3 合同。

## 3. 一个 W3 输出字段，两个动作

在 W3CaseResult 增加 `watch_actions`，默认空数组。动作结构统一：

```json
{
  "watch_actions": [
    {
      "policy_id": "P1",
      "action": "SET",
      "reason": "尚缺客户对新增订单规模的确认，近期披露可能补齐；关注新增规模是否超过当前校准边界。"
    },
    {
      "policy_id": "P2",
      "action": "CANCEL",
      "reason": "后续披露已经否定原先等待的证据方向，不再需要继续关注。"
    }
  ]
}
```

只允许 SET/CANCEL；policy_id 和 reason 非空，reason 用一段话写明缺口/取消原因。Agent 不填写期限、状态、revision、优先级或新的 WATCH ID。

### SET 的边界

1. 本次有效最终判断没有形成交易决定，也没有生成 CLOSED 交易候选。
2. 对应 Policy 属于本条消息 W2 的 HIT/相关集合，存在于本次固定版本的可用 Policy 中。R1 候选召回不等于“相关”。
3. Agent 应说明具体缺什么证据、短期内为什么可能补齐，以及等待它确认什么；宿主不使用关键词或额外模型判断理由质量。
4. 不要求 novelty=NEW。重复报道也可能揭示一个值得短期等待的缺口；WATCH 不能把 OLD 结果篡改成 NEW。

### CANCEL 的边界

W3 可以取消本次上下文中实际看到的有效 WATCH，不需要本次交易或新增事实。缺口被否定、已无短期补齐可能，或继续关注已无意义时都可取消。没有输出动作时保持原状态，不隐式取消。

无效 Policy、越界动作、同一 Policy 同时 SET/CANCEL 等局部问题，记录处置结果，不使整个 W3 研究失败重跑。同 Policy 冲突时 CANCEL 优先；交易决定与 SET 冲突时忽略 SET。

## 4. 最小存储与期限

在现有 `runtime_values` 增加 namespace `policy_watch`，key 用稳定编码的 `(ticker, policy_id)`；每个键只保存一条当前记录：

```json
{
  "ticker": "MU",
  "policy_id": "P1",
  "source_case_id": "case-xxx",
  "reason": "明确的短期证据缺口与关注原因",
  "start_semantic_day": "2026-10-10",
  "expires_at": "由宿主计算的UTC时间"
}
```

不新建业务表、不存状态枚举、不建立 WATCH 历史库。建立与取消的历史依据保留在 W3 result、case 路由记录及副作用 receipt 中。

- D 取来源 case 已固定的 trading_date，而不是 W3 完成或重试的日期。
- `expires_at = boundary(D + 2日)`，覆盖 D 与 D+1；比较到期时间使用 journal.clock，UTC 存储。
- 生效条件为当前时刻已经进入 D 且严格小于 expires_at；W3 结果迟到、提交时已过期则不建立。
- 使用现有 semantic_clock，不按 48 小时硬加秒，也不跳过周末/假日。美东夏令时由边界函数处理。
- 现有语义日是每日 02:00 切换，不是交易所开市日。例如周五来源 case 覆盖周五、周六，周日 02:00 ET 到期。
- 查询时把过期记录视为不存在；下一次合法 SET 可覆盖过期记录，无须定时删除任务。
- 同一 Policy 已有有效 WATCH 时，新的 SET 不覆盖原因、来源或期限，不续期。需要主动撤销就输出 CANCEL；不支持同一次取消后重设来延长窗口。

WATCH 跟随 policy_id，不绑定 activation_revision。普通维护后同 ID 的关注仍在，新消息研判使用自己的固定 Policy 快照和 calibration；新 ID 不继承旧 WATCH。Policy 退役后不再产生可用匹配，旧记录自然到期，不增加发布联动清理。

## 5. 后续消息的路由与 W3 上下文

### 5.1 取得真正的 HIT/相关身份

W2 完成并经过现有引用清理后，取以下集合的并集：

- policy_hit=true 时的 policy_ids / hit_condition_ids 所属 Policy。
- policy_related=true 时的 matched_condition_ids 所属 Policy。

当前相关 Policy 的身份不能只从 policy_ids 取得；matched_condition_ids 里的分组必须保留。只有命中/相关布尔值而无法定位 Policy 时，不猜测身份。

### 5.2 保存一次路由依据

在首次确定 case 路由时，按上述身份读取有效 WATCH，并将命中的完整小记录冻结到 `case.frozen_inputs.policy_watch_followup`。没有命中时保存空列表，明确表示该 case 已作过判定。

命中时强制 primary_route=W3，沿用现有 W3 effect 构造，不能遗留原 ARCHIVE effect；route.reason 明确追加 `POLICY_WATCH_FOLLOWUP` 及 Policy IDs，原 W1/W2/W2 R3 结果和 first_round_tags 原样保留。原本已会进入 W3 的 case 同样记录原因。

重试沿用已冻结的路由依据，不重新查 WATCH 后悄悄改变同一 case 的路线。到期或取消不会撤回已经排入队列的 W3；上下文会明确给出到期时间，它仍可以完成这次研究。

WATCH 不跳过 W1/W2、不扩大 R1 召回、不修改入口消息去重；没有进入 W2 的消息不受本功能影响。技术失败也不由 WATCH 绕过。

### 5.3 注入现有 W3 task.json

将被冻结的 `policy_watch_followup` 放入 W3 的 case 专属 task.json，包含 policy_id、source_case_id、reason、start_semantic_day、expires_at，并明确标识本次为 WATCH 跟进。

这些是每个 case 的上下文，不写进共享 AGENTS.md 或长期 thread 的全局配置，避免残留。W3 需判断“本条消息是否补齐原缺口”，仍可得出 OLD、继续不交易或取消 WATCH。

## 6. 取消、提交顺序和幂等

### 6.1 Policy 消费时自动取消

在 `TradeOutputService.record` 成功接纳 READY 意图的同一数据库事务里，删除 executed_policy_id 对应的 policy_watch。消费记录、Trade、READY 意图和 WATCH 取消一并提交；事务失败则一并回滚。

- CLOSED 只生成候选时不取消；最终选中并释放为 READY 时取消。
- 输出权限不允许、初始过期或重复拦截没有形成新的消费，不触发本次自动取消。
- 自动取消只针对实际执行的 Policy，不按候选列表或 HIT/相关列表批量取消。
- 无 executed_policy_id 的 W3 expert_trade 不猜测要取消哪条 Policy；需要取消可由 W3 显式 CANCEL。
- READY 后外部投递失败或未成交，不恢复 WATCH，与当前“意图接纳即消费”的边界保持一致。
- 即使暂时关闭 POLICY_AUTO_FREEZE_ENABLED，成功 READY 的 Policy 输出仍取消 WATCH；此清理不改变消费开关、不补写消费锁，也不恢复冻结功能。

将取消钩子放在现有实际输出事务，而不是 W3 返回成功时另行删除。后续采用 Condition 级消费后，任一 Condition 实际执行仍取消所属 Policy 的 WATCH，符合本次 Policy 级关注范围。

### 6.2 W3 主动取消

处理 CANCEL 时，宿主从该 case 冻结的 WATCH 上下文取得 source_case_id，仅当当前记录仍属于这一来源 case 时删除。Agent 不必输出这个字段。

这样，旧 W3 迟到或重试不会删除同一 Policy 后来新建的 WATCH。当前已不存在、已到期或已被替换时按幂等无操作记录。

### 6.3 接受动作与崩溃恢复

复用现有 journal 短事务与写入 lease。W3 结果通过既有校验、业务副作用正常完成后，提交 watch_actions；SET 的“未形成交易”判定以实际最终结果为准，不能只看一个原始布尔值。

WATCH 变更与“本 case 动作已处理”的 receipt 必须在同一事务提交；可在 runtime_values 使用 `policy_watch_applied` namespace，以 case_id 为键记录简短动作处置结果，不另建任务或队列。相同 case 重放只读 receipt，不重做 SET/CANCEL。

SET 时在事务内再读当前 WATCH：有效记录不覆盖，过期才可覆盖。冻结启用时，当前相同规则已消费则不建立；还需利用现有成功 Trade 记录检查本 case 开始后该 Policy 是否已输出，防止并发的迟到 SET 在消费自动取消之后重新挂回 WATCH。该检查同样覆盖消费开关暂时关闭的情况，不新增水位表或取消墓碑。

如果 WATCH 先建立，随后 READY 消费，则由输出事务删除；如果 READY 先提交，迟到 SET 则由上述检查忽略。动作 receipt 记录 ACCEPTED/IGNORED 的实际原因即可。

本功能以现有带 journal 的耐久编排为正式接入点。无 journal 的旧/内存路径保持原行为，不为 WATCH 新造另一套存储；如需测试，使用隔离临时 journal。

## 7. 文件级执行清单

| 文件 | 修改边界 |
|---|---|
| persistent_runtime_v2/schema.py | 增加 WatchAction 与 W3CaseResult.watch_actions；旧存档缺字段默认空，不回填动作 |
| persistent_runtime_v2/policy_watch.py（新增小模块） | 有效性读取、期限计算、受控动作提交和事务内自动取消 helper；复用 journal，不建通用状态机 |
| persistent_runtime_v2/service.py | W2 后取 HIT/相关身份、冻结跟进上下文、覆盖路由；W3 接受结果时幂等提交动作 |
| persistent_runtime_v2/router.py | 支持可选 WATCH 命中参数和原因；无 WATCH 时完全保持现有分支 |
| persistent_runtime_v2/w3.py | case 专属 task.json 注入 WATCH 跟进；新调用提供新结果 schema |
| persistent_runtime_v2/trade_output.py | READY 接纳事务内取消实际执行 Policy 的 WATCH |
| W3 执行资产及 schema/执行包冻结入口 | 后续同步 SET/CANCEL 使用边界和短期缺口判断；不让历史执行临时换最新输出 schema |
| tests | 增加有实际行为差异的路由、时钟、动作、消费及并发/恢复验证 |
| changelog | 实施完成后追加重要代码变更及验证摘要；本轮只有方案文档，无代码变更 |

旧 W3 存档缺 watch_actions 正常读取为 []，历史回放不执行新副作用。新输出 schema 与对应 W3 执行资产一起生效，不顺带修改既有 novelty/Policy/expert_trade 的合法组合规则。新的可选 watch_actions 不能成为“NEW 无 Policy 无需研究”的逃生字段。

不新增前端页面、配置开关、监控后台或主动消息推送。现有 case 详情应能展示 route.reason 和 W3 原始 watch_actions；如果详情采用字段白名单，补齐这两个既有证据展示入口即可。

## 8. 必要验收

1. 符合边界的 W3 SET 建立 WATCH；只有 R1 召回、无关 Policy 或已交易结果不能 SET。
2. OLD + 同 Policy 相关、未通过新事实对齐 + 同 Policy 相关，都因有效 WATCH 进入 W3；普通无 WATCH 分支不变。
3. 同 Policy HIT 原本已进入 W3，也携带关注原因；不同 ticker/Policy 不串用。
4. W3 能看到原缺口、来源 case、期限，同时保留原新旧判断；WATCH 不自动产生 Trade/Delta。
5. D 与 D+1 有效，D+2 边界立即无效；包含周末、夏令时边界和 W3 迟到场景。
6. 重复 SET 不续期，不覆盖来源；同 case 重试不重建；已冻结路由不随到期改变。
7. W3 CANCEL 删除本次看到的 WATCH；无动作保持原样，旧 CANCEL 不删除后建记录。
8. READY 消费与取消同事务；输出事务回滚则 WATCH 保留；CLOSED 候选不取消，最终释放取消。
9. 初始过期、权限禁止、重复拦截不误取消；独立 expert_trade 不批量取消；关闭自动冻结时 Policy READY 仍取消 WATCH。
10. SET/消费交错、动作提交后崩溃重试、旧结果取消/新记录并发，都不留下被消费后重新挂回的旧 WATCH。
11. 旧结果可读且不补执行动作；现有 W2 R3、W3研究和闭市选择回归通过。

最终形态是“一份 Policy 临时记录 + 一个 W3 动作列表 + 一个路由覆盖 + 一个消费事务取消钩子”。其他规则只用于保证这四个动作的时间边界和重试结果一致，不扩展成持续监控系统。
