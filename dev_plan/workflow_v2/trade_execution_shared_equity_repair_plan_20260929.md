# Trade Executor 共享 Equity 与执行故障修复方案

日期：2026-09-29。状态：**方案，尚未改动交易代码或生产环境**。问题证据见[近两周交易执行审计](../../docs/trade-execution-audit-20260928.md)。本方案按现有单 Executor writer、Runtime SQLite、IBKR PAPER 账户实现，不另造资金服务或通用风控框架。

## 1. 先明确三个执行事实

- **15:30 ET 是计划 Exit 时间，不是必然成交时间。** 普通交易日从上一个交易日 15:30 到本日 15:30；早收市沿用 `Sessions.exit_at()` 的实际收盘前 30 分钟。旧周期 Exit、在途 Entry 和订单对账完成后，才从券商取得新 `E_cycle`。若尚未完成，仅等待**新 Entry**；旧 Exit、对账和其他 workflow 继续运行。
- **1.0E/1.5E 是下单准入上限，不能承诺成交后或市价波动时永不超出。** SHORT 卖出限价可能以更高价格成交，市值也会波动。每次新订单按最新持仓重算，超限后不增加 Entry、继续 Exit，并记录实际超额。RTH Entry 第二单改用可成交限价；原有 MKT 重试仅留给 RTH Exit。
- **25% 是准入门槛，不是对部分成交的保证。** 若实际只成交不足 0.25E，真实 lot 和 Exit 义务仍存在；结果记 `PARTIAL_FILLED`，不能因一股成交就称 `FILLED`。

本期 Equity 使用已绑定 **USD IBKR 账户**的真实 NetLiquidation。这个数字包含账户内全部资产，Gross 也必须计入同账户全部真实持仓，不能只算 DoxAgent lot。第一版按当前交易标的 US 股票实现：从券商同步全部持仓及其 USD 市值；若账户里有无法估值的其他品种，该账户的新 Entry 等待估值补齐，不能虚构“全账户 Gross”。这只是资金口径成立所需的检查，不要求事先建设跨资产通用估值系统。

## 2. 代码改动落点

| 当前代码 | 最小改动 |
|---|---|
| `schema.py:Strategy` 每笔 `target_notional_usd=20000`；初单容差 1%，非 RTH 重试 2% | 新 Profile 用 `min_entry_notional_ratio=.25`、初单 `.005`、非 RTH 重试 `.01`；不再给新 Entry 固定单笔 $20,000。旧 Profile revision 的序列化与执行 pin 保持兼容 |
| `executor.py:_step()` 只从固定目标扣本 job fills；锁是 `(account,ticker)` | Entry 先读账户周期、全账户持仓和其他 Entry 预占；在 SQLite 事务内决定额度并预占；重试复用原 quota |
| `repository.py` 已有 `te_jobs`、`te_attempts`、`te_events`、`te_sync_state` 和 `BEGIN IMMEDIATE` | 仅增一张 `te_account_cycles`；在现有 Entry job payload 存 quota/未结预占，扩展 `te_sync_state` 的账户快照，在现有 `te_events` 存准入/拒绝理由；合并“预占+prepare attempt”为一个事务 |
| `ibkr_session.py:sync()` 无资金；`what_if()` 只诊断 PAPER 一股 | 取绑定账户 NLV、AvailableFunds、LookAheadAvailableFunds 与全持仓估值；WhatIf 用待发单完整数量和相同订单参数 |
| `strategy.py` 用 `retry: bool` 选容差、RTH 第二单统一 MKT | 按 `leg + session + attempt index` 选订单类型和价差；每张订单重新取新鲜 side quote |
| `repository.py:apply_order()/finish()`、`v2_read/pnl.py` | 保存拒单根因；`FILLED` 校验最终有效仓位；FIFO_OFFSET 实现已实现损益 |

账户周期采用交易所日历中的 exit 边界独立编号，**不改变** Runtime 02:00 ET 的 `release_semantic_day` 和 Intent 原 `expires_at`。新周期未就绪不会延长旧 Intent。周期行存 `account/environment/cycle_id/E_cycle/status/captured_at` 与必要的券商快照摘要；同账户只允许一个 active 周期。Profile 中的 `min_entry_notional_ratio` 跟 revision pin，新周期冻结本期参数。

取新 E 前完成券商订单、成交、仓位同步，账户必须 ready，NLV 为正、USD、可解析。账户资金回调可能数分钟才变化，不能套用 quote 的 5 秒 tick 规则；在退出/成交后重新查询和对账即可。[IBKR 账户更新说明](https://www.interactivebrokers.com/docs/tws-api/doc/account-portfolio-data/account-updates/receiving-account-updates)、[账户资金字段](https://www.interactivebrokers.com/docs/tws-api/doc/account-portfolio-data/account-updates/account-value-keys)。

## 3. 一个共享池的计算与原子准入

对每个新 Entry，`E=E_cycle`，仓位按当前 USD 市值绝对值相加；LONG、SHORT 不抵消，SHORT 卖出所得不增大 E。未终结 Entry 只预占**尚可能成交的部分**，已成交部分只在真实持仓里算一次。Exit 在成交并对账前不提前释放。

```text
account_capacity = max(0, 1.5E - account_position_gross - other_open_entry_reservations)
ticker_capacity  = max(0, 1.0E - ticker_position_gross  - ticker_open_entry_reservations)
available_target = min(E, account_capacity, ticker_capacity)
minimum          = E × min_entry_notional_ratio     # 第一版 0.25E

if available_target < minimum:
    end Entry: PORTFOLIO_CAPACITY_EXHAUSTED / BELOW_MIN_EFFECTIVE_NOTIONAL

qty = floor(available_target / sizing_price)         # 整数股
if qty < 1 or qty × sizing_price < minimum:
    end Entry: PORTFOLIO_CAPACITY_EXHAUSTED / BELOW_MIN_EFFECTIVE_NOTIONAL
```

`sizing_price` 为按最新 ask/bid 和该单容差取整到合法 tick 后的真实限价，不以原始 bid/ask 代替。例：E=$20,000，已有 MU $20,000，则 BE 最多约 $10,000；剩 $3,000 不下单；剩 $5,050 但整数股只能形成 $4,700，也不下单。已有同 ticker 仓位占满 1E 时，新 Intent 不再取另一份 1E。反向 Intent 也不能偷当成免额度 Exit：按现有 FIFO 规则处理真实抵销，但新开反向净仓必须重新满足准入门槛；普通旧仓仍由计划 Exit 负责。

执行顺序如下：

1. 同步全账户持仓与未结订单，读取本周期 E，拿新鲜 side quote，计算价格、整数股和两次 minimum。若本周期尚未产生，只等待该账户的新 Entry；若 Intent 过期按原语义终结。
2. 在现有 `RuntimeJournal.transaction() / BEGIN IMMEDIATE` 内重读周期、已同步的券商持仓快照及该账户所有未结 Entry job，核对最新有效 fills 后重算 capacity；把 `grant_usd`、`reserved_open_usd` 写到本 job，并在同一事务创建唯一 `PREPARED` attempt。第二个 ticker 会在第一个事务提交后看到预占并重新计算。必要的 E、Gross、qty 和拒绝理由写入现有 `te_events`。
3. 用**完整 qty** 做 WhatIf，要求预计成交后 `AvailableFunds` **和** `LookAheadAvailableFunds` 均 ≥ `0.20E`。明确不足记 `INSUFFICIENT_MARGIN`；预检无有效回报记 `MARGIN_CHECK_UNAVAILABLE` 并等待，不误报为资金不足。此时订单尚未提交，标记 attempt `NOT_SENT` 并释放预占；后续重试再竞争额度，不耗实际下单次数。WhatIf 与最终 submit 使用同账户、conId、side、venue、outsideRth、tif、数量、价格和订单类型。[IBKR WhatIf 字段](https://www.interactivebrokers.com/docs/tws-api/ref/order)。
4. 券商预检成功后提交已持久化的 attempt。现有单进程 `WriterLock` 继续防止两个 Executor 同时控制 socket；账户级短锁只覆盖该账户的 **WhatIf→submit/接收确认**，不串行其他账户、其他 workflow 或订单追踪。若提交是否到达券商仍未知，只保留其额度并暂停该账户新 Entry，直到对账；不能改 orderId 盲重发。
5. `apply_fill()` 已将有效 execId 和 lot 重建放在同一事务；同步更新 job 的“已成交转持仓、未成交仍预占”。撤单/拒单得到券商终态并确认最终 fills 后才释放未成交预占。重启从 job、attempt、broker 同步恢复；无需另一张 reservation 表或超时自动释放。

同一 Entry 的 retry **不重新获得 E**：从原 `grant_usd` 扣该 Entry 已有效成交金额，并按最新账户/该 ticker Gross 与**其他** reservation 再算剩余额度；计算本 job retry 时不能重复扣它自己的未结预占。上一单取消且最终 fills 对账后才建下一单。低于 25% 的**新** Entry 禁止；若已有 $4,500 部分成交而原 quota 尚余 $500，可在本 job 的剩余尝试中补足预计 $5,000，不把这 $500 误当新 Entry。若最终未达到 minimum，保存真实部分成交、停止追单并保留 Exit。若成交价改善或行情波动让实际 Gross 越过准入线，记录超额并禁止新增 Entry，不自动强平。

结果沿用现有 wire 枚举，避免只为两个业务原因改动全链路枚举：无成交时 `entry_result=FAILED`，结构化 `entry_reason=PORTFOLIO_CAPACITY_EXHAUSTED` 或 `INSUFFICIENT_MARGIN`，`detail` 保存 `BELOW_MIN_EFFECTIVE_NOTIONAL`、实际容量或券商原因；UI/Read 展示这个明确原因，不能泛化为 `RETRY_BUDGET_EXHAUSTED`。有实际部分成交则 `PARTIAL_FILLED`；`FILLED` 只在订单/成交完成对账且新建归属仓位达到 minimum 时使用。只抵销旧仓、没有建立新仓的反向成交也不能称 `FILLED`，需保留真实成交和独立的 `OFFSET_ONLY` reason 供 Read 展示。现有 `trade_outcomes` 按有效成交量标记 EXECUTED 是事实口径，与是否达到策略 `FILLED` 门槛分开。

## 4. 下单价格与次数

| 时段 / leg | 第 1 单 | 第 2 单 | 第 3 单 |
|---|---|---|---|
| RTH Entry | BUY ask×1.005 / SELL bid×0.995，LMT | 新报价按相同 ±0.5% 重新定价，LMT | 无 |
| RTH Exit | 同左，LMT | 保留 MKT，仅未平归属股数 | 无 |
| 非 RTH Entry/Exit | BUY ask×1.005 / SELL bid×0.995，LMT | 新报价 BUY ask×1.01 / SELL bid×0.99，LMT | 新报价 BUY ask×1.01 / SELL bid×0.99，LMT |

BUY 合法 tick 向上、SELL 向下取整；RTH 最多 **2 张**、非 RTH 最多 **3 张**，均指总尝试数。每张新单前确认上一单已终结且最终 fills 已同步。无新鲜实时报价不以延迟价替代。RTH Entry 改 LMT 是为让“先算额度、再按可执行价格取整数股”有确定依据；用户允许保留 MKT，本方案把它用于 Exit。

## 5. 审计其余问题的最小修复

| 问题 | 直接改动与验收 |
|---|---|
| 周五 BE 201 保证金拒单后升级重试，最终变成泛化失败 | `apply_order()` 将首次明确 Rejected/原因保存为不可被后续 Cancelled 覆盖的拒单事实；`_advance_order()` 区分“已结清”和“可重试”。明确 margin/权限/配置拒单不再涨价或改 MKT；其他 201 不猜为保证金。[IBKR 201 定义](https://www.interactivebrokers.com/docs/tws-api/doc/error-handling/error-codes)。回归 Rejected→Inactive→Cancelled、乱序、部分成交和迟到 fill |
| RKLB 三个 Case 同一批准事实各释放 LONG | 沿用现有 `TradeOutputService.record()` 写事务，在 W3 能给出可靠“事件 ID + 状态变化”时给正式交易做唯一 claim；重复 Case 留分析、标 `DUPLICATE_EVENT_TRADE`。仅针对可确定的主稿/续稿同事实；不同里程碑可再次交易。无可靠事件键时不以标题 hash 或“一天一单”误挡，账户/ticker 限额仍防金额放大 |
| 7 个盘后 `FRESH_QUOTE_UNAVAILABLE` | `quote()` 输出最后 bid/ask、年龄、marketDataType、requestId、连接/farm 错误；订阅失效时有限重订阅。同一有效 Intent 在 `WAIT_QUOTE` 等待，不因三个 5 秒超时就永久失败；到原 `expires_at` 结束，未知订单先对账 |
| 夜盘 10329 后 201 discarded | 持久化 advanced reject JSON 和 10329→201 的同单关系；核验 Gateway 实际 direct-route precaution，仅修相关设置。以 PAPER 夜盘 Entry→Exit 真实回报验收，不全局关闭 precaution |
| 326 clientId 占用、1100 重连 | 记录 socket generation 与重连原因，有限退避；326 核验旧连接或其他占用，不自动更换 writer clientId，不在未知提交时发新单 |
| `FIFO_OFFSET` 漏产品已实现损益 | `v2_read/pnl.py` 按原 lot 成本、成交价和费用计入已实现 PNL；更正/迟到费用后重算。它只修报表，`E_cycle` 直接取 IBKR NLV，不叠加产品 PNL |

## 6. 实施与验收顺序

1. 修订单终态/拒单证据和 fill 对账；随后做单表周期迁移、job 内预占、全账户 Gross、完整 WhatIf；最后改价格矩阵和 Read 展示。旧 Profile/执行 pin 仍按原 revision 解析，在途旧单先对账，不改历史成交或自动重发失败 Intent。重要代码修改追加 `changelog`。
2. 定向测试：同一账户两个 ticker 同时抢剩余 $10,000、同 ticker 第二意图、E 亏损后的下一周期、$5,050 整数股后仅 $4,700、部分/更正/迟到 fill、撤单未知与重启预占、完整 quantity 的 WhatIf 和 20% 缓冲、RTH/非 RTH 价格次数、早收市/周末/02:00 Intent 到期。其余审计问题各以其现有生产 badcase 复现，回归 V2 Read/PNL。
3. 本地测试通过后再对 PAPER 部署做 SQLite 与 native 内容备份、停止旧 writer、迁移、启动、读取真实账户快照与订单对账；以周期 E、Gross、reservation、券商 margin 回报以及 Entry→Exit 成交验证，不以容器健康代替业务验收。LIVE 另行验收，不因 PAPER 通过自动开启。

本轮只交付此实施方案，不改代码、订单、Gateway 或生产配置。
