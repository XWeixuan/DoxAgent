# 近 21 天 Policy 命中复盘

只读拉取时间：2026-10-10 02:59:35 北京时间。窗口为 2026-09-19 02:59:35 至 2026-10-10 02:59:35 北京时间，按 Policy 命中观测的 `occurred_at` 选取。检查了当前对象及历史修订，按 `(ticker, case_id, policy_id)` 去重；共 13 条命中、12 个 Case。另有 1 条 RKLB 当前命中发生于 2026-09-18 02:16 北京时间，早于窗口起点，未纳入。

## 结果

| Ticker | 命中记录 | Case | 无正式意图 | 有意图但零建仓成交 | 实际建仓成交 |
|---|---:|---:|---:|---:|---:|
| BE | 7 | 6 | 3 | 2 | 1 |
| INTC | 2 | 2 | 2 | 0 | 0 |
| MU | 1 | 1 | 1 | 0 | 0 |
| RKLB | 3 | 3 | 2 | 1 | 0 |
| 合计 | 13 | 12 | 8 | 3 | 1 |

BE 的 `case_aa6372d40d874adf9172558c14568dad` 同时命中两条 Policy，因此 BE 的命中数比 Case 数多 1。表内后三列按 Case 计数。

### 有交易意图的四个 Case

| Ticker / Case | 执行结果 | 买卖点与已实现净盈亏 |
|---|---|---|
| BE `case_7b76ce24051a44c990d602d5db7b8027` | PAPER LONG，真实 ENTRY 与 EXIT 均成交 57 股 | 买入 2026-10-07 16:01:57 北京时间，成交均价 $292.90；卖出 2026-10-08 03:30:05 北京时间，成交均价 $287.50；已实现净盈亏 **-$310.137925**。命中且被执行的 Policy 为 `pol_b6eb54be9d4d509c8c80`。 |
| BE `case_aa6372d40d874adf9172558c14568dad` | 正式意图产生，但投递失败，零成交 | `DELIVERY_FAILED`；记录的原因是旧版 v2-delivery 不支持冻结的 SHARED_CYCLE Profile。该 Case 的两条命中分别导出，交易只算一笔意图。 |
| BE `case_e7827ec7703c44e6a0960bbebe0b0f79` | 正式意图产生，但投递失败，零成交 | 同为 `DELIVERY_FAILED` / SHARED_CYCLE Profile 校验不兼容。 |
| RKLB `case_cb774ab16f154c5c9535f8453b99ff86` | 执行已接收，ENTRY 失败，零成交 | `INSUFFICIENT_MARGIN`；没有买卖成交价或已实现盈亏。 |

其余八个 Case 无正式交易意图：五个最终带 `ARCHIVE` / `BADCASE` 标签，三个 CLOSED Case 只形成候选记录（BE 一个、INTC 两个）。Policy 命中不等于正式交易意图，也不等于成交。

## 导出证据与边界

每条 JSON 记录包含命中身份及时间、固定版本 Policy 详情、Case 摘要、W1/W2/W3 研判、完整消息正文、发现事实、CLOSED 候选、正式交易意图与执行摘要。实际买卖点来自归属于该 lot 的券商 fill，加权均价按归属数量计算；盈亏来自已实现净盈亏投影，未使用市价估算。

导出通过 SSH 只读访问生产 SQLite；未修改远端数据或服务。Read 投影与 Runtime/Executor 数据库分别开启只读事务，二者没有跨库原子快照，因此交易状态以本次拉取时读到的持久记录为准。完整文件及 SHA-256 见 [export/README.md](export/README.md)。
