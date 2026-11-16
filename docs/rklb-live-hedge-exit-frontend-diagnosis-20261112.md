# RKLB LIVE 交易、对冲失败及前端持仓状态诊断

日期：2026-11-12。本文时间除明确注明 UTC / 美东外，均为北京时间。

## 1. 结论

目标 Case：`case_f8319410bef746ca998e29279eaaa216`。

标题：`‘Get Ready for What’s Next,’ Says Morgan Stanley About Rocket Lab Stock - TipRanks`。

本轮核对了用户导出、生产 Runtime 交易账本、订单事件、前端 Read 库及其投影提交时间，并通过远端 LIVE Gateway 独立查询订单、成交和持仓。结论如下：

1. **主仓真实成交，且已按时全部退出。** 11 月 11 日 23:49:41 买入 18 股 RKLB；11 月 12 日 04:30:02 卖出全部 18 股。计划退出时间是 04:30:00，执行账本交易组为 `CLOSED`，自身剩余数量为 0。05:17 的独立券商复核也确认 RKLB、TQQQ 持仓均为 0。
2. **对冲未成交的直接原因是 LIVE 缺少复杂或杠杆交易所交易产品权限。** IBKR 对 TQQQ 的保证金预检明确返回错误 201、`No Trading Permission, Customer Ineligible`。没有实际发送对冲订单。
3. **“刚刚才看到”有明确的数据同步延迟证据。** 源 Case 于昨晚 23:48 形成，但前端读取库直到今天 05:00:16 才投影到该 Case，约晚 5 小时 12 分钟；成交状态也直到 05:00:25 才投影出来。
4. **前端“持仓中”确实是过时展示。** 页面取得的 Read 数据仍为 `OPEN / MAIN=18`，而 Runtime 与券商均已退出。平仓事件仍在投影队列后面；同时页面对 `STALE` 不作提示，放大了误导。

本轮仅诊断，没有修改源代码、生产配置或数据库，没有重启服务、重放意图、提交订单、发送 WhatIf、撤单或人工平仓。只读券商验证记录 `orders_submitted=0`。

## 2. 实际时间线

| 北京时间 | 事实 | 依据 |
| --- | --- | --- |
| 11-11 23:40:17 | 新闻标注发布时间 | 用户导出、源记录 |
| 11-11 23:48:26 | 消息进入 Bus | 源记录 |
| 11-11 23:48:27 | Runtime 接收消息 | Case |
| 11-11 23:49:34 | W3 完成并释放 LIVE LONG 意图 | Runtime 意图 |
| 11-11 23:49:41 | BUY 18 RKLB，成交均价 $67.79 | ENTRY 成交账本 |
| 11-11 23:49:48 | 第一次 TQQQ WhatIf 预检被 IBKR 201 拒绝 | `margin_decision` 原始详情 |
| 11-11 23:54:32 | 最后一次相同拒绝，共 21 次预检 | 对冲 attempt 与账户事件 |
| 11-11 23:54:42 | 对冲开仓到达既有 300 秒时效，结束为未完成 | HEDGE ENTRY job |
| 11-12 04:30:00 | 到达计划时间，触发 `SESSION_CUTOFF` 退出 | MAIN EXIT job |
| 11-12 04:30:02 | SELL 18 RKLB，成交均价 $65.875 | 独立券商成交复核 |
| 11-12 04:30:12 | 退出对账结束，组状态 `CLOSED` | Runtime execution / lot / job |
| 11-12 05:00:16 | Case 首次投影进前端 Read 库 | `commit_retention.projected_at` |
| 11-12 05:00:25 | 主仓成交状态投影进 Read | 同上 |
| 11-12 05:02:16 | 对冲未完成及旧 OPEN 状态投影进 Read | 同上 |
| 11-12 05:15 | 新查询的执行详情仍返回 `OPEN / MAIN=18 / HEDGE=0` | 只读 DoxAgent 执行详情 |
| 11-12 05:17:15 | 独立查询 LIVE：退出订单 Filled，RKLB/TQQQ 均为 0 | Gateway 只读核对 |

04:30 北京时间对应 11 月 11 日美东 15:30，符合现有退出时点。源消息至 REALTIME 判定释放耗时约 67.5 秒，没有触发 12 分钟延迟保护；真实下单也发生在当时，没有等待数小时才执行。

该笔主仓买入金额 $1,220.22、卖出金额 $1,185.75；毛损益 -$34.47。两侧已记录费用合计 $2.024534，净损益 **-$36.494534**，与券商退出成交费用报告的已实现损益一致。

## 3. 对冲为什么没有执行

### 3.1 当时计划本身有效

固定执行快照为 LIVE / MARGIN，主仓 BUY RKLB，对冲 SELL TQQQ，对冲名义比例 0.57，允许区间 0.47–0.67。策略额度 $2,000，扣费用预留后计划可用 $1,998。

初始整数股组合为主仓 18 股、对冲 12 股；估算名义金额分别 $1,225.98、$719.64，比例约 0.587，总计 $1,945.62。组合有效。主仓实成交后按实际金额重新计算，对冲仍可为 12 股。

因此，本次不是原先的整数股组合缺陷，不是 CASH 配置，也没有证据表明是预算不足或主仓未成交阻止了对冲。

### 3.2 券商的确定性拒绝被代码错误归类

21 次 WhatIf 均返回同一个核心错误：

```text
IBKR_201: Order rejected - reason:No Trading Permission, Customer Ineligible;
Restricted: Must request Complex or Leveraged Exchange-Traded Product permissions to trade.
```

这段是券商原始回调，不是从最终超时原因推测。完整信息还说明既有仓位的关闭操作不受这项限制。

`src/doxagent/trade_execution/executor.py` 的 MARGIN 开仓路径先生成 PREPARED attempt，再调用 `broker.what_if`。当前异常分支只把少数保证金/资金文本识别为 `INSUFFICIENT_MARGIN`，其余统一记为 `MARGIN_CHECK_UNAVAILABLE`。

权限拒绝因此被误认为预检暂不可用：attempt 被作废为 `NOT_SENT`，job 等待约 10 秒后再次尝试。21 次均无 `sent_at`，没有发送真实对冲开仓订单，也没有对冲成交。

既有“三次下单机会”主要约束实际下单机会；这些 NOT_SENT 预检没有消耗真实下单计数，最后由释放后 300 秒的 ENTRY 队列时效终止，留下：

- `hedge_status=HEDGE_INCOMPLETE`
- `hedge_entry_result=FAILED`
- `hedge_entry_reason=ENTRY_QUEUE_AGE_EXCEEDED`

因此前端最终只看到队列超时，真正的权限拒绝藏在账户 `margin_decision` 事件里。这是**确定性拒绝错误重试、根因没有向展示端保留**的问题。

升级 MARGIN 不等于自动获得杠杆 ETF 权限。本次直接验证的是 TQQQ；不能据此声称已经逐个验证 SOXL/TNA。IBKR 对这类产品设有单独权限类别，可参考其[交易权限说明](https://www.interactivebrokers.com/campus/trading-lessons/trade-permissions-mkt/)。

### 3.3 为什么仍持有主仓

主仓成交后才开对冲，且对冲失败后保留已成交主仓，是用户此前冻结的业务规则。此次主仓保留到既有退出时点与该规则一致；不应因修复权限错误分类而擅自改成失败即平主仓。

## 4. 前端为什么现在还显示持仓中

### 4.1 三层状态对照

| 层级 | 本轮查到的结果 |
| --- | --- |
| LIVE 券商 | RKLB=0、TQQQ=0；退出 SELL 18 已 Filled |
| Runtime 交易源账本 | `group_state=CLOSED`，主仓 lot remaining=0，EXIT DONE |
| Read / 执行详情 API | 仍为 `execution_group.state=OPEN`，主仓 remaining=18 |
| React Case 执行卡 | 将 API 的 OPEN 渲染成“持仓中”，显示剩余 18 股 |

`frontend/v2/src/pages/runtime-case.tsx` 的映射包含 `OPEN → 持仓中`、`CLOSED → 已退出`；剩余股数来自 `execution_group.main_quantity`。目前没有发现把正确 CLOSED 错翻成 OPEN 的分支。

`src/doxagent/v2_read/executions.py` 从投影的 execution `group_state` 和 `leg_quantities` 生成这些字段。因此这里主要是展示链路拿到旧事实，不是 EXIT 执行器没有工作，也不是仅清理浏览器缓存就能解决。

### 4.2 延迟有真实投影时钟证明

必须区分两种时间：

- `commits.at`：当前实现沿用源事件 `recorded_at`，不能当成实际写入前端库的时间。
- `commit_retention.projected_at`：投影写入时的服务器时钟，可以用于本次延迟核对。

关键投影如下（UTC）：

| 源事件 | 源时间 | 实际投影时间 | 含义 |
| --- | --- | --- | --- |
| runtime 4487010 | 15:48:30.580 | 21:00:16.678820 | Case 首次进入 Read |
| runtime 4487125 | 15:49:42.606 | 21:00:25.594874 | 主仓已成交进入 Read |
| runtime 4487805 | 15:54:43.016 | 21:02:16.650851 | 对冲未完成后的 OPEN 版本进入 Read |

这里证明的是**服务端首次可读时间**，不能证明用户浏览器首次打开或首次看见的准确时刻；但与用户“刚刚才看到”的观察吻合。

主仓平仓相关源事件早已存在：

- 4499262：20:30:00.214 UTC，退出触发。
- 4499333：20:30:07.702 UTC，成交后状态更新。
- 4499387：20:30:12.834 UTC，CLOSED。

05:22:14 北京时间的只读快照：Runtime 投影 checkpoint 为 **4491306**，head 为 **4500895**；下一条事件源时间为 16:36:43 UTC，约落后 **4 小时 45 分钟**。此时 CLOSED 事件还未到消费位置。

这段范围内有 9,589 条 hot outbox 头记录，主要是 runtime_values、effects、cases、turns、tasks 等。交易退出记录和这些 Runtime 事件共用顺序消费通道，没有独立的低延迟展示通道。未发现目标 CLOSED 源事件丢失或交易出口缺少写入。

Projector 仍在运行，checkpoint 持续前进，source_health 未报告该源异常；不能简单称为进程停止。同期已有指标实施报告也记录了数小时 Runtime 投影积压，说明它不限于本 Case。**本轮确认了积压位置、数据顺序和业务后果，尚未把整条投影吞吐问题归因到唯一慢 SQL 或某次发布；不能仅凭 CPU 占用或积压表分布认定具体性能根因。**

### 4.3 过期提示不足

`frontend/v2/src/core/api.ts` 的 `staleMessage` 当前只在 `meta.refresh_error` 存在时返回提示，主动忽略聚合 `STALE`；`Module` 复用该逻辑。查询成功但源数据落后数小时，不会因此展示过期警告。

全局 STALE 本身受多源健康影响，直接把所有页面都改成红色错误也不合适；但交易组“当前持仓”需要明确的 Runtime 源消费时间/延迟说明。否则用户无法区分“刚刷新页面”与“数据已反映最新交易”。

页面使用 view 固定读取快照；续期/刷新可以换到更新 Read 版本，但在 Projector 尚未消费 CLOSED 时，重新打开页面同样只能取得旧 OPEN。不能靠前端强制重试修好源积压。

### 4.4 导出字段也不足以判断是否退出

`frontend/v2/src/core/runtime-case-export.ts` 当前交易摘要主要导出入场结果、数量、金额、首次成交等，未同时导出交易组退出状态、剩余股数和退出成交明细。因此用户提供的 JSON 不能单独证明“尚未平仓”。

另一个概念区别：`execution_state=EXECUTED` 表示这条意图确实有主仓入场成交，平仓后继续保留 EXECUTED 是合理的；是否仍持仓应看交易组 CLOSED/remaining，而非把 EXECUTED 改成未执行。

## 5. 建议修复顺序及边界

本轮未实施以下改动。

1. **修复 MARGIN 预检拒绝分类与可见原因。** 对明确缺交易权限/不符合资格的确定性拒绝终止该腿 ENTRY，记录稳定原因码及券商原始详情；暂时性预检故障沿用现有恢复。对冲失败仍保留已成交主仓。避免将所有 201 一律当同类，也不把 WhatIf 当成真实订单。回归确认无真实下单、无重试到超时、根因正确展示。
2. **处理 Runtime 投影积压。** 先对现有 event load、mapper、ingest 和每轮维护分别做有界计时，再针对实测热点修复；验证 checkpoint 追近 head，主仓成交与关闭能及时出现在新 view。不能直接推进 checkpoint、跳掉中间事实、手改 Read 为 CLOSED，或全库重建掩盖问题。是否需要交易展示的独立快速路径，应在现有吞吐修复效果明确后再决定，避免本轮就增加第二套账本。
3. **补交易状态的新鲜度提示。** 使用 Runtime 源自身的消费锚点/延迟提示“截至何时的持仓状态”，保留最后可信数据。前端回归覆盖旧 OPEN、更新 CLOSED、源滞后但 HTTP 成功及 view 续期；不要把其他源落后误报成交易接口报错。
4. **补齐 Case 导出的退出与对冲字段。** 明确区分入场结果、当前剩余、计划退出、实际退出以及对冲失败原始原因，减少单看入场摘要的误判。

权限本身需要账户持有人通过 IBKR 正常申请并满足资格。不能由程序“修复”券商权限，也不能擅自更换用户选定的 TQQQ/SOXL/TNA 或关闭对冲。无需补做这条已过时交易的对冲，更无需再次退出已经清零的主仓。

## 6. 证据与代码定位

本轮本地只读证据位于 `eval/rklb-exit-diagnosis-20261112/`：

| 文件 | 内容 |
| --- | --- |
| `execution-snapshot.json` | 意图、固定配置、执行组、jobs、attempts、lots、fills |
| `order-events.json` | 21 次相同权限拒绝与账户执行事件 |
| `broker-verification.json` | 独立 LIVE Gateway 只读订单、成交、费用与零仓位 |
| `projection-snapshot.json` / `projection-history.json` | Read 中旧 OPEN 与历史投影版本 |
| `projection-timeline.json` | 原始退出 outbox 坐标与消费位置 |
| `projection-wallclock.json` | 源时间和实际 projected_at 对照 |
| `backlog-headers.json` | 最后消费位置、源 head 与有界积压头分布 |

主要代码：

- `src/doxagent/trade_execution/executor.py`：MARGIN WhatIf、异常分类及延迟重试。
- `src/doxagent/trade_execution/ibkr_session.py`：WhatIf 请求与 IBKR 错误回调。
- `src/doxagent/trade_execution/recovery.py`：ENTRY 队列 300 秒时效。
- `src/doxagent/trade_execution/groups.py`：主/对冲腿生命周期和失败边界。
- `src/doxagent/v2_read/projector.py`：按源坐标顺序消费、每事件投影。
- `src/doxagent/v2_read/repository.py`：源健康、新鲜度与真实 projected_at。
- `src/doxagent/v2_read/executions.py`：执行组状态及剩余量输出。
- `frontend/v2/src/pages/runtime-case.tsx`：交易组状态与剩余股数展示。
- `frontend/v2/src/core/api.ts` / `components/state.tsx`：STALE 与刷新失败提示。
- `frontend/v2/src/core/runtime-case-export.ts`：导出摘要范围。

订单复核锚点：入场 orderId 4 / permId 372542879；退出 orderId 48 / permId 372542998；退出 execId `00030f00.6af76452.01.01`。账户在报告中不公开，证据输出已掩码。券商历史查询窗口只返回了退出成交，入场由持久账本核对，不把窗口外记录未返回解释为入场丢失。
