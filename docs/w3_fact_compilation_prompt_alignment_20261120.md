# W3 输出 Schema 与事实编译分工调整说明

供提示词对齐使用。以下是新版合同的行为；本轮没有修改任何 W3 Input 字段、上下文构造、注入逻辑或 skill 文件。

## 1. 事实编译只交给一端

| Case 最终路由 | 事实候选编译节点 |
|---|---|
| 进入 W3：包括 NEW、LOW 置信度、WATCH 跟进等 | W3 Post-Decision Operations |
| 未进入 W3，且原路由需要编译事实 | W1R3 |

进入 W3 的新版 Case 不再同时派发 W1R3。正常 OLD、未进 W3 且原本无需事实编译的 Case，不因此增加一次 W1R3 调用。CLOSED 流程也等 W2／WATCH 确定最终路由后再选择编译端，不在 W1 分阶段完成时抢先抽取。

交易先提交，事实编译后执行。Operations 失败或返回空候选，不撤销、延后、重判或重发 Trade Decision。W1/W2 的 NEW/OLD、HIT/RELATED 结果保持只读，产生事实候选也不改变这些终态参数。

## 2. Trade Decision 输出不变

仍只输出当前的方向判断，`decision` 为 `LONG / SHORT / NO_TRADE`。`LONG/SHORT` 的分析字段要求沿用现有合同。本轮未改这一轮的执行、共识或交易提交机制。

```json
{
  "decision": "NO_TRADE",
  "reason": "本次不形成交易意图的原因",
  "prior_expectation": null,
  "expectation_delta": null
}
```

## 3. Post-Decision Operations 输出

新版根字段固定为三个数组，必须显式输出；不能用 `null`。没有相应产出时使用空数组：

```json
{
  "pin_actions": [],
  "watch_actions": [],
  "delta_candidates": []
}
```

`pin_actions`、`watch_actions` 的对象格式、合法目标、容量和时效规则均不变。

`delta_candidates` 最多 12 条。单条使用已有事实候选格式，例如：

```json
{
  "proposition": "公司披露客户已完成产品认证。",
  "assertion_state": "ACTUAL",
  "subject_time": null,
  "occurrence_date": null,
  "entities": ["公司", "客户"]
}
```

- `proposition`：事实命题，1～4000 字符；不是交易方向、投资推演或凑数占位。
- `assertion_state`：沿用现有枚举，区分事实、计划、预测、传闻、否认等，不创造新枚举。
- `subject_time`：业务适用时期或 `null`；`occurrence_date`：消息支持的 `YYYY-MM-DD` 或 `null`，不为填字段猜日期。
- `entities`：主要实体字符串数组，允许 `[]`，最多 32 项。

**没有合格事实必须返回 `delta_candidates: []`。** NEW、LOW、WATCH、LONG/SHORT 都不构成“必须产出至少一条”的理由；NO_TRADE 也不等于没有值得编译的事实。沿用当前 Thread 已可见的消息和认识，不假设宿主额外注入了 Event／Fact 库或 W1R2 详情。

只恢复 `delta_candidates`，不恢复 `novelty`、`reference_ids`、`policy` 或其他 W1/W2 决策复核字段；也不允许在 Operations 输出新的交易选择。

## 4. W1R3 与历史兼容

W1R3 的合同仍为 `{"candidates": [...]}`，但现在允许 `{"candidates": []}`。空结果是合法完成，不会为了满足最少数量而修复重试或补造候选，也不会查询 Event 最大 ID 或写入空事实。

宿主沿用现有 provisional 写入及幂等身份。W3 候选、PIN/WATCH 应用与 Operations 提交在同一事务中完成；失败整体回滚，重试不重复入库。

已冻结旧双字段 Operations Schema 的在途 Case 按原合同收尾；旧的成功 W1R3 结果不被另一端再次编译入库。不会通过修改旧输入、重开 Trade 或重写历史候选来迁移这些任务。

## 5. 提示词需同步的地方

现行 `prompts/persistent_runtime_v2/w3/skills/operations.md` 明确写着“不抽事实”、只提交 PIN/WATCH 两个根字段。负责提示词的对话需要把这一处职责和输出示例对齐为“PIN/WATCH＋事实候选”，并明确无合格事实时返回空数组；不得恢复新旧或 Policy 命中复核职责，也无需增加任何 Input 注入。W1R3 的完成标准也应允许空候选。

本轮按用户要求没有修改该 skill，也没有发布生产或发起真实 W3 调用。提示词与代码需在上线时同批对齐。

已完成 253 项编排回归，另有 2 个不适用参数组合跳过；覆盖空候选、NEW/OLD/LOW、WATCH 派发、CLOSED 分阶段、旧合同收尾、原子回滚、重复消费，以及事实与 PIN/WATCH 同批应用。W3 输入构造和 Trade 提交函数另做源码对比，均未变化。
