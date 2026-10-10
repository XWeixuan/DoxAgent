# D3 按方向消费与整 Policy 重新授权：开发需求

日期：2026-10-10。状态：待实现。范围：编排、数据合同、Runtime 消费和维护发布接入；本轮仅输出需求，不修改代码、prompt/skill 或当前消费开关。

## 1. 冻结业务规则

- 消费单位为 `(ticker, policy_id, direction)`。LONG-C1 输出交易后，该 Policy 全部 LONG 条件冻结，包括后续新增/修改的 LONG 条件；SHORT 仍可召回、判断和消费，反向同理。
- 规则 revision、Condition ID、PolicySet 版本、日期变化均不解冻。规则 revision 只标识内容，交易资格独立保存。
- 重新授权以整个 Policy 为单位请求，须重新研究并完整交付该 Policy。普通 upsert 不恢复资格；不机械要求所有字段或每个 SHORT 条件都改写。
- 消费发生在成功接纳 READY 交易意图时，不等待实际成交。CLOSED 候选生成不消费，最终释放才消费；投递失败/未成交不自动解冻。

## 2. 最小状态与合同

Runtime 持久保存每个 Policy × 方向的 `usage_generation`、`consumed`、`consumption_id`，保留实际执行 Condition、规则快照、来源 case/消息、交易意图和时间的消费依据。使用代次由宿主管理，不由 agent 填写。

维护 feed 新增 `policy_consumption`，按 Policy 汇总仍冻结方向及上述消费依据。每次从持久状态读取，不依赖当日 Trade 的 processed 标记。MAINTAIN 失败、漏跑或成功但未授权，冻结状态都保留并继续进入下次维护；存在冻结待处理项时不能被无新 Event 的 NOOP 判断跳过。

维护 patch 新增 `rearm_requests`，默认 `[]`：

```json
{
  "rearm_requests": [
    {"policy_id": "P1", "reason": "已重新建立整个观察范围的现实与预期比较面，说明哪些未来独立变化仍可形成新的交易机会。"}
  ]
}
```

请求不带 direction/condition_id。宿主从本次冻结的维护输入绑定消费 ID：同 Policy 必须有被接受的完整 upsert，不能同时 retire；身份不合法或 upsert 失败时仅拒绝对应授权，不阻断其他合法修改。普通新增 Policy 不作为重授权旧对象的替代操作。

## 3. 授权生效与并发

- 接受的授权及其消费 ID 写入既有 release/activation receipt，不写入 Policy/Condition 业务正文。
- 新版本成功激活后才幂等应用授权；patch/staged 成功不解冻，发布或激活失败继续冻结。合法 PARTIAL 中已接纳的 Policy 可以授权。
- 只解除本次维护实际看到、且提交时仍对应同一消费 ID 的方向；解除后该方向使用代次加一。未消费方向不重置代次。
- 维护期间新发生的消费不被旧请求解除，继续进入下一次维护。重复提交/恢复不重复推进代次；普通版本切换或回滚不清空消费历史。
- 重授权是实质运行变化，不能仅因 Policy 文本未变化被判为 NOOP。
- Runtime admission 和实际交易输出须确保相应活动授权已经应用；继续复用既有事务、lease、候选激活和 ACK，不引入跨库伪原子操作。

## 4. 下游与维护接入

- R1 保留仍有可用方向的中性 Policy；R2/W3读取完整双向条件及方向状态，冻结方向不能成为最终执行选项。
- Trade/candidate 保存明确执行 Condition/revision/方向和使用代次；READY 输出事务校验方向未消费、代次一致，再原子写消费及交易意图。
- CLOSED 最终释放同时检查规则有效性和代次；旧候选不随重新授权自动获得新代次资格。
- 维护沿用单 MAINTAIN，完整输入冻结并支持恢复；feed_has_work、结果 NOOP、发布和 feed processed 均纳入闭环。
- 若已实现 WATCH，实际消费任一方向时同事务取消所属 Policy 的 WATCH，不影响另一方向交易资格。
- 旧版本结果保留旧 codec，不从缺失的历史证据猜新双向状态。本需求依赖 v3/v5 的正式候选读取与激活接入，不能仅改旧 v4 parser 宣称完成。
- 编排合同与对应执行资产一起上线；本轮不改 prompt/skill，也不自行启用当前关闭的冻结开关。

## 5. 实现落点与验收

落点：Runtime schema/repository/provider、service/W3/selection/trade_output；maintenance feed/frame；D3 schema_v21/MAINTAIN/发布 receipt；activation admission；相关读模型与测试。实施完成后追加 changelog。

必须验证：

1. LONG 消费后全部 LONG 冻结，SHORT 可用；修改/新增同方向条件、跨日或升版不解冻。
2. MAINTAIN 失败、漏跑、未授权后，消费状态和依据继续输入下一次维护；无 Event 时不漏处理。
3. 完整 Policy upsert + 合法请求 + 成功激活才能授权；普通改稿、失败 upsert、staged 或激活失败不解冻。
4. 全 Policy 请求解除输入中已知消费方向；并发新消费不被解除，未消费方向不重置。
5. 输出并发争用只成功一次，授权恢复幂等；旧 CLOSED 候选不得跨代释放。
6. PARTIAL 局部授权、授权型非 NOOP、旧结果读取及已有工作流回归通过。仅离线通过不宣称真实业务效果已验证。
