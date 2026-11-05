# completed_orders 复发修复与 LIVE 单股验收

日期：2026-11-06。下文业务时间统一为北京时间（Asia/Shanghai），UTC 原始时间保留在证据 JSON。

**结果：已确认 Gateway 后端重连后的全局 initialLoad/pending 阻塞，修正 DoxAgent 对该历史查询的全账户硬依赖并发布。PAPER 完成 ONDS 1 股买卖；本次新授权的 LIVE 仅提交一次 BUY ONDS 1，实际以 $4.50 成交。独立只读连接再次核对 Filled、1 股、费用和资金，完整历史查询也已恢复。**

## 1. 本次复发的实际经过

用户本次重新授权一次 LIVE BUY ONDS 1。首次 R2 预检在完整同步阶段超时，`orders_submitted=0`，没有 `buy.claim.json`，未调用 `placeOrder`，因此未消耗本次下单授权。后续使用相同 R2 验收身份完成首次且唯一一次提交。

通过 Gateway 官方日志导出功能检查当日日志，确认：

| 北京时间 | LIVE Gateway 事件 |
| --- | --- |
| 04:52:32、04:52:54 | 完成订单查询正常，分别返回 0 和 1 条；上一轮被 201 拒绝的订单已可查询 |
| 05:34:37 | LIVE 后端 CCP socket 被对端关闭，Gateway 清空完成订单缓存 |
| 05:34:41–05:35:08 | FIX 尚未完成认证，多次消息被 Gateway 忽略 |
| 05:35:09 | CCP 再次断开 |
| 05:40:08 | 后端重新登录，恢复 executions、open orders |
| 05:40:09.371–.372 | client82 的完成订单请求被委托给 `pending initialLoad()` |
| 05:41 起 | 每轮请求均报另一 pending 请求存在，完成订单始终没有 End |
| 05:43:52 | 新建独立 client195281 也被同一个 client82 pending 请求阻挡；PAPER 对照正常 |
| 05:55 | 在无 DoxAgent 未结订单/持仓时，使用 Gateway 自带 auto restart 完成一次重启 |
| 05:57:38 | 新版本 LIVE/PAPER 完整只读同步均通过；历史 End 恢复 |

日志原句：

```text
Query completed orders for:20261106 is delegated to pending initialLoad()
Completed orders request cannot be sent for serverId=82 due to another pending request.
```

证据：`eval/live-onds-one-share-20261106-r2/gateway-recurrence-evidence.json`、`completed-readonly-probes.json`。原始 Gateway 日志和含完整账号的截图仅保留在私有审计目录，没有提交仓库。

### 根因与排除项

实际阻塞在 **Gateway 进程内部的完成订单初始加载/请求状态**。它会在后端连接中断、重认证后出现，跨 API client 和 socket 存续。上一轮的串行请求、超时断开 API socket，以及关闭“连接时自动下载订单”，均不足以解除或防止它复发。

- 不属于“空列表本来就不返回 End”：同一 LIVE 之前返回过正常的 0 条结果。
- 不属于新意图自身下单失败：R2 预检没有提交；之前 BE 的历史失败计数误继承是另一项已修复缺陷。
- 不属于仅一个 Python client 的回调丢失：独立 client 同时失败，Gateway 自己记录了 pending 阻塞，PAPER 对照成功。
- 不把外部 session 断开的来源猜成用户操作；日志只证明对端断开和随后重认证，不能进一步断言为何断开。

官方说明该接口返回当日已经执行、拒绝或取消的订单，属于完成订单记录获取：[IBKR Completed Orders](https://www.interactivebrokers.com/docs/tws-api/doc/order-management/completed-orders)。社区存在相同 pending 日志和超时适配案例：[ib_insync issue #224](https://github.com/erdewit/ib_insync/issues/224)。本次定位以生产 Gateway 日志为主要证据，社区记录只作对照。

## 2. 实际代码修复

生产源码仅修改 `src/doxagent/trade_execution/ibkr_session.py`，复用现有连接、sync_lock、事件回调和恢复框架。

1. 顺序取得当前 open orders、executions、positions、account，并按原逻辑取得 CASH 的 SettledCash。
2. 最后查询 completed orders；收到 End 才标记 `history_complete=true`。
3. 缺少历史 End 时，返回已取得的完整当前状态，明确写入 `history_complete=false`、`history_scope=UNAVAILABLE`，发出 `sync_history_unavailable` 诊断事件。**没有将缺失历史当作空订单列表。**
4. 同一 socket 保留唯一的 completed pending 请求，后续同步继续更新当前挂单、成交、持仓和资金，不重复压入历史请求，也不因历史接口挂起反复重连。迟到 End 只结束原 pending 请求；下一轮新历史查询必须等待自己的 End。
5. 当前挂单、成交、持仓或账户同步真正缺少结束响应，仍失败、断开旧 socket，并沿用现有共享退避。

`complete=true` 在新快照中表示必需的当前状态查询完成；完成订单历史是否完整由 `history_complete` 单独表示。未引入新服务、定时重启守护程序或新的订单执行策略。

### 防重复和归属机制保持

- 已提交但没有任何券商确认的 attempt 继续 `RECONCILE_REQUIRED/BROKER_HISTORY_GAP`，绝不因列表里没找到它而判为未发送。
- 撤单后没有终态，不释放该 attempt 去追加订单；终态与成交数量不符，继续等待成交对账。
- 成交按既有 account、orderRef/clientId/orderId、execId 归属和去重。
- 外部/个人订单不生成 DoxAgent owned lot；退出仍只处理自身归属数量。
- 原 ENTRY 时效、次数、盘口、限价、对冲和 EXIT 规则未改。

**修复边界：本次清除了当前 Gateway 挂起，并消除了它对 DoxAgent 全账户当前状态同步的非必要阻塞；不能声称修掉 IBKR 闭源 Gateway 内部 initialLoad 缺陷。以后若再次发生，历史会明确显示不可用；依赖缺失终态的具体订单仍需恢复历史或取得明确订单回报后才能继续。**

## 3. 测试与发布

| 检查 | 结果 |
| --- | --- |
| 历史协议、恢复及 ENTRY 计数定向回归 | 35 passed |
| 交易与退出扩展回归 | 142 passed，1 项既有过时断言失败，无 skipped |
| Ruff check / format check | 通过 |
| 新版 LIVE 只读同步 | 0.959 秒，history_complete=true，完成订单阶段 0.105 秒 |
| 新版 PAPER 只读同步 | 0.767 秒，history_complete=true，完成订单阶段 0.004 秒 |
| PAPER 实际买卖 | 2 单、2 次 1 股成交，最终 ONDS 0 股 |
| LIVE 本次真实买入 | 1 次提交、1 股成交，最终 ONDS 1 股 |
| 独立 LIVE 只读复核 | Filled、唯一成交、1 股、费用及现金一致；历史阶段 0.144 秒 |

新增回归覆盖：空列表有 End、历史无 End 不重复请求、迟到 End 不误认成新请求完成、所有必需当前状态缺 End 仍断线、历史不可用时正常成交退出、历史不可用时未知提交仍不重发。

既有失败为 `tests/test_trade_delivery_failure.py::test_terminal_delivery_validation_failure_is_visible`：仍断言已移除 LEGACY 的 `shared cycle must not set target_notional_usd` 文案，实际校验返回这两个旧字段属于 extra inputs。该测试、Strategy schema、trade_output 及其视图代码均未被本次修改；单独复跑同样失败。本轮没有借机修改该断言或业务逻辑。详见两个 JUnit XML。

发布提交：`de78ec13`，已推送 `codex/agent-readonly-mcp`。

生产镜像：

```text
sha256:71be04315849b1fafcb8140e6d6fa6d9780c88481d222d77e775997b28bbd758
```

仅叠加一份已提交源码，保留原服务 environment、command、entrypoint；`ibkr_session/executor/repository` 三份生产源码摘要均与已提交版本一致。changelog 已追加记录，其他未提交工作未纳入发布。

Gateway 操作：先确认没有 DoxAgent 未结 attempt、活动 job 和 owned position，暂时停止正式 Executor；将 LIVE 自带 auto restart 临时设为北京时间 05:55，重启后恢复原 **23:45**，并保留截图核验。API 的 Read-only 仍关闭，连接时自动下载订单仍关闭。没有更改账号、交易路由、策略额度或认证凭据。

## 4. PAPER 验收

| 项目 | BUY | SELL |
| --- | --- | --- |
| 时间 | 2026-11-06 05:58:33 | 2026-11-06 05:58:34 |
| 账户 | DUT0***665 | 同左 |
| 数量 | 1 | 1 |
| 限价 | $4.53 | $4.45 |
| 实际成交价 | $4.50 | $4.48 |
| orderId | 208 | 209 |
| 状态 | Filled | Filled |

成交后的历史查询均正常返回 End；最终 ONDS 数量由 0 回到 0。

## 5. LIVE 唯一次买入

使用新 R2 身份，和前一轮被拒绝的订单区分：

| 字段 | 实际结果 |
| --- | --- |
| 账号 / 类型 | U1***370 / CASH |
| DoxAgent 独立配置额度 | $950，保持不变 |
| 合约 | ONDS / STK / USD / SMART / conId 455415330，primary NASDAQ |
| 下单前 Bid / Ask | $4.48 / $4.50，实时双侧、size 有效且新鲜 |
| 下单前已结算现金 / AvailableFunds | 均 $2,262.83 |
| 方向 / 数量 / 类型 | BUY / 1 整数股 / LMT，盘后允许成交 |
| 限价 | $4.53（Ask × 1.005 后按最小跳动取整） |
| 实际成交时间 | **2026-11-06 05:59:27 北京时间**；2026-11-05 21:59:27 UTC |
| 实际成交 | **1 股 × $4.50** |
| orderId / clientId | 2 / 82 |
| permId | 1861419970 |
| orderRef | DA-ACCEPT-ONDS1-LIVE-BUY-20261106-R2 |
| execId | 0001506d.6aec8f84.01.01 |
| 最终状态 / 当前数量 | Filled / **1 股** |
| 费用回报 | USD **0.045003**（券商当次 API 回报，以最终账单为准） |
| 后续现金回报 | USD $2,258.28，含券商显示精度的现金扣减 |

提交前核对实际账号、合约、方向、数量、盘口、限价和资金；先以 O_EXCL 创建并 fsync 不可重复的提交 claim，然后才调用 `placeOrder`。正式 Executor 暂停期间，验收 helper 持有同一个 WriterLock，最后自动恢复正式 Executor。单独的 placeOrder 守卫保证 LIVE 只允许这个合约的 BUY 1，调用数最大 1。

实际 `placeOrder` **仅 1 次**，无撤单、替换、追加、对冲或卖出。下单后完整同步通过，随后另一个只读 client195301 再次取得唯一 Filled 订单、唯一 1 股成交、费用和 ONDS 1 股持仓。私有目录已写入 authorization-consumed.json，本次授权已消耗；result 和 claim 双重防止验收脚本重入提交。

本测试刻意绕开普通 Runtime intent/group 创建，避免自动对冲和退出。因此这 1 股是 **人工验收持仓**，没有纳入普通 DoxAgent 交易组，也不会触发自动平仓。此次通过的是 LIVE 下单、成交回报及券商订单/成交/持仓/资金/费用对账；没有宣称验证了真实 W1/W2/W3 决策、双腿自动执行或 LIVE 自动退出。

## 6. 最终状态与留痕

- 正式 Executor 已恢复运行，restart_count=0。
- LIVE/PAPER 的共享 recovery 状态均已清空；独立只读完整同步均成功。
- 没有活动的普通交易 job；既有 owned lots 剩余数量均为 0。
- BE：`trade:case_8da7542affa54cba98d711513ce8c7df`，仍 FAILED / CLOSED、ENTRY_FAILURE_BUDGET_EXHAUSTED、attempts=0。
- INTC：`trade:case_2e138775e1fd4e159988a9682551f3aa`，仍 FAILED / CLOSED、NO_VALID_INTEGER_COMBINATION、attempts=0。
- 没有重放、重置或恢复任何旧失败意图。

证据目录：`eval/live-onds-one-share-20261106-r2/`。主要记录为 `gateway-recurrence-evidence.json`、`post-release-readonly.json`、`acceptance-receipts.json`、`final-readonly.json`、`final-runtime.json` 及回归 XML。公开证据的实际账户 ID 已脱敏；原始日志、完整私有回执留在受限目录。
