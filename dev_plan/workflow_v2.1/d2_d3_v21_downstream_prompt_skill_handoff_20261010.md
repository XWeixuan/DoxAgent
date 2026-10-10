# D2/D3 v2.1 下游合同：prompt/skill 会话同步

日期：2026-10-10。对应统一实施方案与本轮代码；本轮没有编辑 prompt/skill。本说明只说明业务输入、交付与宿主行为，不要求模型承担数据库协议。

## 1. 输入与判断边界

- 新 D2 为原生 `document2.v2.1`，Shell/Unit 使用 name、scope、boundary/horizon 和新版四块研究内容；不把展示定位符当业务 ID，不编造旧 Gap。
- 新 Policy 为 `document3.v3`；运行投影为 `document3.runtime_projection.v5`。一份 Policy 可以同时包含 LONG/SHORT、不同 trigger_layer 的多个 Condition；每条 Condition 独立充分，OR 语义。transmission 是经济传导说明，不能改造成所有条件的隐含 AND；层级不代表执行优先级。
- W2 R1 每条只看到 `policy_id/title/match_scope`，负责范围召回。R2 看到召回 Policy 的完整原生正文和 `policy_usage` 中的 executable_directions/consumed_directions。正文不会因为一个方向被消费而删掉该方向条件。
- W3 看到固定版本的完整 D1/D2/Policy、事件参考材料、方向资格和本次 WATCH 跟进。宿主保存使用代次及消费身份；研究不需要自行推导这些协议字段。
- 原有 W1 新旧、W2 HIT/RELATED、W2 R3 条件事实新旧和第一轮标签保持各自含义。真实 HIT 与能否执行交易是两件事，禁止通过否认 HIT 来表达不交易或 WATCH。

## 2. W3 最终交付

新版结果合同为 `persistent-runtime.w3-result.v3`，新增必填可空 `policy_execution` 和 `watch_actions` 数组。

| 最终判断 | policy_execution | 其他约束 |
|---|---|---|
| OLD | null | 无 Delta，无专家交易；可以提出合法 WATCH 动作 |
| NEW 且存在最终 HIT | 对象 | 专家交易不执行；正常交付 Delta；显式选择一个命中 Condition 或说明不交易 |
| NEW 且没有最终 HIT | null | 必须完成专家研究；可以交易或有理由不交易；正常交付 Delta |

有 HIT 时示例：

```json
{
  "policy_execution": {
    "selected_condition": {"policy_id": "P1", "condition_id": "C2"},
    "reason": "选择该命中条件的实际交易依据"
  },
  "watch_actions": []
}
```

不交易使用 `selected_condition: null` 并填写 reason，不能省略整个评价对象。所选 Condition 必须出现在最终 HIT 归因中。可以保留多个双向命中，但最终只选择一个；宿主不替模型执行首位选项，不默认 LONG。交易被资格检查拒绝，合法 Delta 仍被保留。

## 3. 方向消费与 MAINTAIN

READY 接纳消费 `(ticker, policy_id, direction)`，该方向现有及未来所有 Condition 都冻结；反方向独立可用。READY 是交易意图接纳，不等于成交；未成交、跨日、改标题/正文/Condition ID/版本都不恢复资格。

MAINTAIN 继续单节点。每轮 `persistent-runtime.o3-maintenance-feed.v2` 的 `policy_consumption` 都会包含尚未重新授权的消费依据，不依赖当日是否有消息、Event 或新增成交。没有重新授权的记录，下轮仍会出现。

重新授权必须重新研究整 Policy，并同时交付：

1. `upsert_policies` 中完整的同 ID Policy；不是仅补几个字段，不要求为改变而机械重写已有合理内容。
2. `rearm_requests` 中 `{ "policy_id": "P1", "reason": "整 Policy 复核后的重新授权依据" }`。

请求不携带 direction、generation 或 consumption_id。宿主把请求绑定到本轮实际看到的消费记录；成功激活后，才解除仍匹配的已消费方向。维护过程中发生的新消费、已更换的消费身份不会被旧请求解除。没有完整有效 upsert、普通改稿、仅 staged、失败或未激活均不解冻；正文不变但有效 rearm 也不是 NOOP。单个非法请求局部拒绝，不牵连其他合法对象。

## 4. 临时 WATCH

- 仅适用于本轮 W2 HIT/RELATED 已经明确关联的 Policy。R1 召回、未知 ID、不相关稿件不构成 WATCH 资格。
- 显式不交易、没有 CLOSED 候选时，可提交 `{ "policy_id": "P1", "action": "SET", "reason": "短期需要确认的具体证据缺口" }`；CANCEL 使用同样三个字段。不要填写自定义期限。
- 来源 case 语义日 D 与 D+1 有效，D+2 边界自动过期；包括周末，不滚动续期。重复 SET 不延长，冲突时 CANCEL 优先。
- WATCH 只改变后续路由。后续消息 W2 关联该 Policy 时，冻结本次有效关注原因/期限并进入 W3 复核模式；W1 可以仍然是 OLD，不能为进入 W3 强行改为 NEW。
- 任一方向 READY 接纳会自动取消该 Policy WATCH；迟到旧 SET 不把它重新挂回。反方向交易资格不因取消 WATCH 消失。CANCEL 只操作本次冻结的同一来源关注，不删除后来别人设置的新关注。
- WATCH 不是长期任务队列，不恢复冻结方向，不替代 NEW 无 Policy 的专家研究，也不要求共享 thread 记住关注状态。

## 5. 执行与上线边界

新 case 使用 revision 3 冻结合同/schema；旧在途任务保留原包和旧交付合同。原生 v5 不接入缺少新合同的旧执行包。本轮代码已恢复共享自动冻结开关，但没有部署、切换活动版本或修改提示词。

提示词会话需核对 R1/R2/W3/MAINTAIN 的上述实际合同，再进行隔离 MU 真实模型验收。离线代码通过不等于 Agent 业务行为已经完成验收。
