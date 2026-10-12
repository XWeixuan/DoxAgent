# Policy 命中导出清单

- 截止 UTC：2026-10-09T18:59:35.983285+00:00
- 起点 UTC：2026-09-18T18:59:35.983285+00:00（向前 21 天）
- 口径：Policy 命中观测发生时间；含历史修订中曾命中的记录。
- 命中记录：13；Case：12；实际建仓 Case：1。
- 同一个 Case 命中多条 Policy 时逐条导出，交易只按 Case 关联；`hit_policy_released_for_trade` 标明该 Policy 是否为释放交易的 Policy。
- 买卖点采用归属于该笔 lot 的真实成交加权均价；盈亏采用读投影中的已实现净盈亏，未实现部分留空。

## 分组

- BE: 7 条
- INTC: 2 条
- MU: 1 条
- RKLB: 3 条

## 文件

- `BE_01.json`：5 条；SHA-256 `fe0a6bd1d97c4416d57ba30c87b0e38ac8d084770cfe99783fe68c79920c6ec7`
- `BE_02.json`：2 条；SHA-256 `013c30f61ec36cc11742cd74ed47cbdbf4ab8f14d94e542b2080e130d456d283`
- `INTC_01.json`：2 条；SHA-256 `7a4aa375039f1285ef24195811a44cb2a06049f965f02f8373a704aa3bac7ecd`
- `MU_01.json`：1 条；SHA-256 `8850eec2bf6b61e58bcdee8f7f60228d428aa74534d28f0c877430133036a5b3`
- `RKLB_01.json`：3 条；SHA-256 `7a62403b97ce375455cd8fc1aeed50b70ee2b02847e49638416846ae0f877a4c`
