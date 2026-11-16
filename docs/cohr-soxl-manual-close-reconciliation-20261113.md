# COHR/SOXL 人工平仓账本核对与修正

2026-11-13，北京时间19:17完成。用户授权修正人工平仓后的策略持仓，防止原美东15:30退出任务误下单。

目标交易组：`trade:case_6db274e8e6ce40bb96a4aabc55c11b8d`。

## 券商事实

- 原主仓：COHR SHORT6，conId584127832。人工BUY6于19:09:38成交，$232.22，execId `0000e95f.6af6e960.01.01`。
- 原对冲：SOXL LONG7，conId73340487。人工SELL7于19:14:54成交，$65.16，execId `0002bd06.6af6c443.01.01`。
- 修改前独立LIVE查询：两合约均0股，无账户活动挂单、无策略未对账attempt，订单历史窗口查询完整。

## 执行

短暂停止唯一Executor，保留原状态备份，再以WriterLock独占执行现有 `ExecutionRepository.adjust_lot` 接口：主仓quantity_delta=-6，对冲quantity_delta=-7。每项有不可重复调整ID、真实人工成交时间与execId/价格说明。

没有改动个人持仓基线，没有删除或修改原始入场成交，没有发送任何买卖、WhatIf或撤单。原始状态及结果保存在远端 `/data/runtime/acceptance/manual-close-cohr-20261113/`。

两条EXIT任务均由既有finish接口结束为DONE，原因 `MANUALLY_CLOSED_BROKER_VERIFIED`；交易组CLOSED，MAIN/HEDGE数量0，活动任务0。既有接口返回的EXIT_FILLED仅表示退出义务已清零，不是新增自动平仓成交。

此操作修正数量归属及退出生命周期；没有把人工成交伪装成策略自动订单成交，也没有回填人工平仓产生的已实现盈亏和费用。策略收益/资金核算若需完整反映该次人工退出，仍须另做基于真实成交及费用的归属核算，不应把本次数量调整解释为盈亏补齐。

## 恢复后核验

19:17:40独立只读连接核对：

- COHR=0、SOXL=0。
- 账户无活动挂单，策略无未对账attempt。
- 目标交易组CLOSED；两条ENTRY均DONE、两条EXIT均DONE。
- 无剩余LIVE策略lot、目标活动任务0。

原定时间美东11月13日15:30，对应北京11月14日04:30。该组已无退出责任：调度不选DONE任务，`recover_failed_exits`不选DONE任务，组推进也不会因零剩余量再建立待执行平仓。

Executor已经恢复运行。这只结束该组原有交易，不关闭后续新意图的正常交易能力。无应用代码改动。

本地证据：`eval/rklb-exit-diagnosis-20261112/manual-close-before.json`、`manual-close-applied.json`、`manual-close-after.json`；配套探针 `manual_close_probe.py`、`manual_close_apply.py`。Apply探针仅供留痕，不应重复执行。
