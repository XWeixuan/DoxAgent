# TQQQ LIVE 单股低价限价挂撤单测试

测试日期：2026-11-12，北京时间。用户明确授权仅一次 BUY 1 股 TQQQ，以很低限价验证真实挂单，确认接受后撤单。

## 结果

**通过：仅提交一次真实订单，收到 Submitted，随后确认 Cancelled；成交 0 股、残留挂单 0、TQQQ 最终持仓 0。**

| 项目 | 实际值 |
| --- | --- |
| 账户 | LIVE / MARGIN，U1***370，与此前 RKLB 实盘账户一致 |
| 合约 | TQQQ / STK / USD / SMART，主交易所 NASDAQ，conId 72539702 |
| 方向、数量 | BUY 1 股 |
| 订单 | LMT，限价 $1.00，DAY，允许盘后 |
| 预检实时盘口 | Bid $58.96 / Ask $59.00（07:03:40） |
| 初始 TQQQ 持仓 | 0 股 |
| 初始券商 AvailableFunds | $5,885.13 USD；不是策略额度调整 |
| clientId / orderId | 195623 / 49 |
| permId | 372543003 |
| orderRef | DA-TEST-TQQQ1-20261112-ONCE |
| 券商回报 | 07:05:37.874 PreSubmitted → 07:05:37.914 Submitted → 07:05:38.318 Cancelled |
| 下单、撤单次数 | 1、1 |
| 成交 | 0 股，无测试成交回报 |
| 最终持仓 | 0 股 |

真实提交前再次取得最新有效盘口、核对账户及合约、资金、零初始持仓，以及不存在同合约活动订单。采用远低于当时市价的 $1.00 限价；低限价减少成交可能，不能事先承诺绝对不会成交。

接受判断依据是服务端 `Submitted`，并非仅 SDK 方法返回或本地 PendingSubmit。IBKR 对各状态的含义见[官方 Order Status 文档](https://www.interactivebrokers.com/docs/tws-api/doc/order-management/order-status)。

## 最终只读核对

07:06:07 使用独立 clientId 195624 再次查询 LIVE Gateway：

- completed orders 中恰有该测试订单，permId 372543003，状态 Cancelled。
- reqAllOpenOrders 没有该测试订单。
- 成交查询没有该 orderRef 的成交。
- TQQQ 持仓为 0，订单/持仓同步与历史窗口查询均完成。
- 独立核对连接关闭全部交易方法，提交订单数为 0。

券商错误列表中的 202 是本次正常撤单回报；没有出现此前的 201 交易权限拒绝。

## 范围与留痕

本次只验证 **TQQQ 买入方向的真实接受与撤单链路**。没有发送 SELL/卖空测试、对冲腿、追加订单、改价订单或 WhatIf，没有重放 RKLB 旧意图，也没有改动应用代码、策略配置或额度。

因此，结果支持“当前 LIVE 可以接受这张 TQQQ BUY 限价单”，不替代卖空借券、保证金预检或整套自动对冲流程验收。此前诊断的预检错误分类及前端同步问题，不因本次挂单成功而视为已修复。

测试使用独立 API client 和唯一 orderRef；提交前以独占创建并落盘的一次性 claim 防止重跑产生第二张订单，交易调用额外限定合约、账户、BUY、1 股、LMT $1.00，并只允许撤销自身订单。未启动正常执行任务，因此不会派生对冲或退出订单。

本地证据位于 `eval/rklb-exit-diagnosis-20261112/`：

- `tqqq-preflight.json`：只读账户、合约、资金与行情预检。该通用会话的订单缓存不含合约字段，因此最终提交前的无同合约活动订单校验以 `tqqq_once.py` 的原始 openOrder 回调为准。
- `tqqq-once-result.json`：一次提交、接受/撤单回调、券商订单属性及最终对账。
- `tqqq-final-readonly.json`：独立连接最终复核。
- `tqqq_preflight.py` / `tqqq_once.py` / `tqqq_final_readonly.py`：本次探针。

远端原始测试日志和不可重复提交标记保留在 `/data/runtime/acceptance/tqqq-buy1-low-limit-20261112/`，未发布至 Git。
