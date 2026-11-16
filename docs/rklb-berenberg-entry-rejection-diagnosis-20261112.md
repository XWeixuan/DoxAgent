# RKLB Berenberg Case 未成交诊断

Case：`case_1b18423c38084f97931c3fe052d3dfd2`。标题：Rocket Lab USA (RKLB) PT Raised to $85 at Berenberg - StreetInsider。

## 结论

主仓实际发送过一张 LIVE BUY 19股 RKLB、LMT $64.47 的订单，被 Gateway/API 夜盘直连路由相关的预防性设置拦截，随后券商取消并拒绝。成交为0，主仓 ENTRY 已永久结束，交易组 CLOSED。对冲因 MAIN_NOT_FILLED 未提交。本次与此前 TQQQ 杠杆产品权限拒绝不同。

## 时间与证据

以下均为2026-11-12北京时间：

| 时间 | 事件 |
| --- | --- |
| 15:40:18 | Runtime 接收消息 |
| 15:41:17 | 释放 LONG 意图；源消息到判定约61.38秒，未超12分钟 |
| 15:41:29 | 非正常时段首次盘口准入通过 |
| 15:41:33 | MARGIN WhatIf VALIDATED；预估初始保证金变化$456.15，检查后可用资金$5,429.06 |
| 15:41:34.109 | 发送 orderId50、clientId82、permId372543012 |
| 15:41:34.252 | IBKR 10329，直连 OVERNIGHT 及 API Precautionary Settings 限制 |
| 15:41:34.298 | Cancelled，filled0、remaining19 |
| 15:41:34.315 | IBKR 201：Order rejected - reason:Order was discarded. |
| 15:41:41 | 对账结束，主仓失败；对冲 MAIN_NOT_FILLED，交易组关闭 |

原始10329详情：

```text
This order will be directly routed to OVERNIGHT.
Restriction is specified in Precautionary Settings of Global Configuration/API.
```

同时出现2109，说明 Outside Regular Trading Hours 属性因订单类型/目的地被忽略；该信息本身不是最终拒绝。关键是10329之后紧接实际Cancelled及201拒绝。不能仅凭看到一个10329就断言所有该类提示都等于拒单。

15:41北京为美东02:41，执行器选择OVERNIGHT符合当时夜盘时段。下单参考盘口Bid63.98、Ask64.14，价差约0.25%；限价64.47是Ask×1.005后按tick向上取整。计划主仓19股、对冲TQQQ12股，名义对冲比约0.5715，在0.47–0.67范围内。因此没有证据指向盘口、整数股比例或保证金不足。

## 机制与处理边界

`repository.apply_error` 保存10329至attempt.precaution_rejection；`executor` 对已实际拒绝且存在该标记的订单记为ORDER_PRECAUTION_REJECTED并结束ENTRY，不重复提交相同拒绝。这解释了为什么没有用完三次机会：明确券商拒绝和普通5秒未成交不是同一分支。

应核对LIVE Gateway的Global Configuration → API → Precautions内与股票直连/重定向警告有关的具体设置。此次未打开Gateway界面核实具体勾选项，因此不声称已经识别并修改了某一个开关；也不建议未经核对就全局关闭所有预防性检查。官方[错误码说明](https://www.interactivebrokers.com/docs/tws-api/doc/error/codes)将10329列为直接路由提示；[夜盘API说明](https://www.interactivebrokers.com/campus/ibkr-quant-news/api-overnight-trading/)介绍OVERNIGHT路线。

此前TQQQ BUY1测试是SMART盘后挂撤单，只证明该方向、该路径可接受；不能覆盖此次RKLB OVERNIGHT直连路由限制。主仓尚未成交，对冲未下单是正常业务边界，不应补做对冲。

本轮仅只读诊断，没有改配置、重放意图或真实下单。依据生产Runtime账本与事件并与前端执行详情交叉核对；没有额外调用券商交易接口。原始证据：`eval/rklb-exit-diagnosis-20261112/berenberg-execution.json`、`berenberg-events.json`。
