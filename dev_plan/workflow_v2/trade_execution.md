这版调整之后，我认为已经可以落成第一版 Trade Executor。整体逻辑很一致：**Policy 决定方向，账户配置决定该方向是否允许，后面全部由确定性订单状态机完成，不再引入任何 AI 风控或审核。**

我只会对两个地方做语义上的精确化：

* `1% / 2% tolerance` 应理解为**可接受成交价格上限/下限（price protection cap）**，不是预计真的会滑点 1%/2%。限价单仍可能在更优价格成交。
* “Quantity 用实际 Order Price”最好定义为**每一次提交时都根据剩余目标 notional 和该次实际下单价格重新计算剩余数量**，否则部分成交后价格跳变可能让最终投入明显超过 `$20,000`。

IBKR 当前 Overnight 美股/ETF 确实是 Limit Order 交易，因此你把非 RTH 路径改成纯限价 retry 是合理的。([Interactive Brokers][1])

---

# 一、账户与 Direction

配置：

```text
ACCOUNT_MODE
=
PAPER
LIVE_CASH
```

对应：

```text
PAPER
LONG  → enabled
SHORT → enabled

LIVE_CASH
LONG  → enabled
SHORT → disabled
```

因此收到：

```text
Policy decision = SHORT
ACCOUNT_MODE = LIVE_CASH
```

程序直接结束：

```text
execution_status = DIRECTION_DISABLED
```

不向 IBKR 提交订单。

这个状态**不要叫 `TRADE_FAILED`**，因为不是执行失败，而是当前账户配置明确不执行这个方向。

以后如果切 Margin Account，只需：

```text
SHORT_ENABLED = true
```

不需要改变 Policy 和 Runtime。

---

# 二、核心参数

第一版可以冻结成：

```text
TARGET_NOTIONAL_USD       = 20_000

INITIAL_TOLERANCE         = 1.00%
NON_RTH_RETRY_TOLERANCE   = 2.00%

ORDER_WAIT_SECONDS        = 5

RTH_MAX_RETRIES           = 1
NON_RTH_MAX_RETRIES       = 2

EXIT_OFFSET_MINUTES       = 30

ALLOW_FRACTIONAL_SHARES   = false
```

其中：

```text
INITIAL
+
RTH 1 retry
```

最多两张订单。

非 RTH：

```text
INITIAL
+
RETRY 1
+
RETRY 2
```

最多三张订单。

---

# 三、价格规则统一成 BUY / SELL 两套

不要分别为：

```text
long entry
long exit
short entry
short cover
```

写四套公式。

只定义 Side。

## BUY

包括：

```text
LONG entry
SHORT cover
```

初始限价：

```text
limit_price
=
ASK × (1 + 1%)
```

非 RTH retry：

```text
limit_price
=
fresh ASK × (1 + 2%)
```

---

## SELL

包括：

```text
SHORT entry
LONG exit
```

初始限价：

```text
limit_price
=
BID × (1 - 1%)
```

非 RTH retry：

```text
limit_price
=
fresh BID × (1 - 2%)
```

这样就彻底修复了之前 `SELL = bid + tolerance` 的方向错误。

---

# 四、每一次 Retry 都重新取 Quote

这一点我建议明确写死。

不要：

```text
09:30:00
ASK = 100

第一次：
101

5秒后 retry：
仍然 102
```

而应该：

```text
Attempt 0
↓
fetch fresh bid/ask
↓
calculate order price
↓
submit
```

如果失败：

```text
Attempt 1
↓
重新 fetch bid/ask
↓
重新 calculate price
↓
submit
```

因此 tolerance 永远相对于：

> **当前这个 attempt 提交瞬间的 quote**

而不是最初触发 Trade 时的 quote。

---

# 五、价格 Tick 处理

计算出：

```text
101.237
```

不能直接假设 IBKR 接受。

根据合约允许的 minimum price increment 做确定性 rounding。

### BUY

向上取到有效 tick：

```text
101.237
→
101.24
```

### SELL

向下取到有效 tick：

```text
98.763
→
98.76
```

这样 rounding 不会意外降低订单的 marketability。

TWS Order 本身通过 `lmtPrice` 表达 Limit Price，IBKR 也提供 contract/order rule 信息用于识别可用 order types 和价格规则。([Interactive Brokers][2])

---

# 六、Entry Quantity

你的目标本质上不是：

> 固定买 200 股。

而是：

> 尽量部署 `$20,000` notional，并且只用整数股。

所以第一次：

```text
remaining_notional
=
20,000
```

然后：

```text
qty
=
floor(
    remaining_notional
    /
    order_price
)
```

例如：

```text
ASK = $99

BUY limit
= 99 × 1.01
= $99.99

qty
= floor(20,000 / 99.99)
= 200 shares
```

---

# 七、部分成交以后重新计算剩余 Notional

这个我建议作为状态机核心，而不是只简单：

```text
remaining shares
```

例如：

```text
Target = $20,000

第一次订单：
200 shares @ limit $100
```

实际成交：

```text
100 shares @ $99.80
```

那么已经部署：

```text
filled_notional
=
100 × 99.80
=
$9,980
```

剩余预算：

```text
remaining_notional
=
20,000 - 9,980
=
$10,020
```

如果下一次：

```text
new order price = $105
```

则：

```text
retry_qty
=
floor(10,020 / 105)
=
95 shares
```

不是继续机械提交剩余 100 股。

这样即使新闻以后价格在 5 秒内快速变化：

> `$20,000` 始终是仓位目标，而不是第一张订单生成时才有效的参考值。

SHORT 同样以成交绝对 notional 计算。

---

# 八、Entry — RTH 状态机

Regular Trading Hours：

```text
TRADE signal
↓
direction enabled?
↓
fresh quote
↓
calculate 1% marketable limit
↓
calculate qty
↓
submit LIMIT
↓
WAIT 5s
```

然后：

```text
FULL FILLED
→ POSITION_OPEN
```

否则：

```text
PARTIAL / UNFILLED
↓
RECONCILE
```

---

# 九、成交对账

每一次 retry 之前固定执行：

```text
1. 读取 order status
2. 读取 executions / fills
3. 计算实际 filled quantity
4. 计算实际 filled notional
5. cancel remaining original order
6. 等待订单进入已取消/终态
7. 再读取一次 final fills
8. 重新计算 remaining notional
```

这样可以处理：

```text
cancel request 发出去
↓
期间又成交 30 shares
```

这种典型 race condition。

不要：

```text
5秒没收到完整成交
→ 立刻发第二张单
```

否则最容易形成 unintended overfill。

---

# 十、RTH Retry

RTH 初始 Limit 未完全成交：

```text
reconcile
↓
remaining_notional > minimum share value?
```

如果还有可执行数量：

```text
submit MARKET
```

只允许：

```text
1 次
```

不再继续 retry。

因此：

```text
RTH Entry

Attempt 0
1% marketable LIMIT
↓ 5s

Attempt 1
MARKET
↓
final reconciliation
↓
FILLED / PARTIAL_FILLED / FAILED
```

这里的 `MARKET` 是最终执行 fallback，不再受到 1% tolerance 保护。

这一点应该明确接受，因为这是你现在设计本身的含义：

> 第一优先是快速成交，限价只是第一次尝试的价格保护。

---

# 十一、Non-RTH Entry

非 Regular Trading Hours 不使用 Market retry。

IBKR 的 Overnight 美股/ETF订单目前是限价交易；TWS API 也有 `includeOvernight` 等订单属性。([Interactive Brokers][2])

流程：

```text
Attempt 0
fresh quote
↓
1% marketable LIMIT
↓
WAIT 5s
```

不完整：

```text
reconcile
↓
fresh quote
↓
Attempt 1
2% marketable LIMIT
↓
WAIT 5s
```

仍不完整：

```text
reconcile
↓
fresh quote
↓
Attempt 2
2% marketable LIMIT
↓
WAIT 5s
```

仍不完整：

```text
final reconcile
↓
结束
```

不再追加第三次 retry。

---

# 十二、注意 2% Retry 是基于新 Quote

例如 BUY：

```text
初始：
ASK = 100
limit = 101
```

5 秒后：

```text
ASK = 104
```

Retry 1：

```text
limit
=
104 × 1.02
=
106.08
```

而不是：

```text
100 × 1.02
=
102
```

这一点对于新闻交易尤其重要。

---

# 十三、Execution Result 建议区分 4 种

不要只有：

```text
SUCCESS
FAIL
```

程序至少区分：

```text
FILLED
PARTIAL_FILLED
FAILED
DIRECTION_DISABLED
```

其中：

### FILLED

目标预算对应的可买整数股全部完成。

### PARTIAL_FILLED

Retry 全部结束以后仍有未使用 budget，但已经形成非零持仓。

这里我建议：

> **保留已成交仓位，不为了追求 $20k 而继续交易。**

因为你已经明确：

```text
max retry
```

再补单就破坏了规则。

### FAILED

```text
最终 position_qty = 0
```

且所有尝试结束。

### DIRECTION_DISABLED

例如：

```text
LIVE_CASH + SHORT
```

不是失败。

---

# 十四、Trade 生命周期以“实际持仓”为准

一旦：

```text
FILLED
或
PARTIAL_FILLED
```

都进入：

```text
POSITION_OPEN
```

然后安排 Exit。

不能因为：

```text
只买到 $13,000 / $20,000
```

就没有退出任务。

---

# 十五、Exit 时间算法

不硬编码：

```text
15:30
```

而是：

```text
scheduled_exit
=
relevant_regular_session_close
-
30 minutes
```

因此正常 NYSE 日：

```text
16:00
→
15:30 ET
```

提前收市：

```text
13:00
→
12:30 ET
```

使用交易所 Calendar 确定当天实际 Regular Session Close。

---

# 十六、Entry → Exit Session 映射

我建议冻结成这一套，非常清楚。

### Premarket

例如：

```text
08:10 ET entry
```

当天 RTH 尚未开始：

```text
EXIT
=
today RTH close - 30min
```

---

### RTH 且早于 Exit Cutoff

```text
10:00
15:00
15:20
```

全部：

```text
EXIT
=
today close - 30min
```

---

### RTH 但已经到/超过 Exit Cutoff

例如：

```text
15:35
```

则：

```text
EXIT
=
next regular trading session close - 30min
```

不在：

```text
15:35 entry
→ 立即卖掉
```

---

### After-hours

```text
18:00 Monday
```

：

```text
EXIT
=
Tuesday RTH close - 30min
```

---

### Overnight

例如：

```text
22:00 Monday
```

或者：

```text
02:00 Tuesday
```

都：

```text
EXIT
=
next upcoming RTH session
close - 30min
```

也就是通常：

```text
Tuesday 15:30
```

这比按：

> “消息属于哪一个 trade date”

处理简单得多。

IBKR Overnight 在 20:00–00:00 成交甚至会记为下一交易日 trade date，因此用**下一场实际 RTH session**而不是 broker trade-date 标签做退出调度也更干净。([Interactive Brokers][1])

---

# 十七、周末和节假日自然由 Calendar 解决

例如：

```text
Friday 18:00 entry
```

不是：

```text
Saturday exit
```

而是：

```text
next valid RTH session
通常 Monday
↓
Monday close - 30min
```

如果 Monday 休市：

```text
Tuesday
```

同样自动推进。

---

# 十八、Exit 不重新计算仓位目标

Entry 与 Exit 不一样。

Entry：

```text
$20,000 target notional
→ calculate quantity
```

Exit：

> **关闭实际净持仓。**

例如 LONG：

```text
actual_position = +187
```

则：

```text
SELL 187
```

SHORT：

```text
actual_position = -194
```

则：

```text
BUY TO COVER 194
```

不再使用 `$20,000` 重新计算数量。

---

# 十九、Exit Initial Order

和 Entry 完全复用 Side execution logic。

LONG position：

```text
SELL
limit
=
fresh BID × 0.99
```

SHORT position：

```text
BUY TO COVER
limit
=
fresh ASK × 1.01
```

然后：

```text
WAIT 5s
↓
reconcile
```

---

# 二十、Exit Retry

### RTH Exit

如果 scheduled exit 本身就在：

```text
close - 30min
```

自然是 RTH。

因此正常情况下：

```text
Initial:
1% marketable LIMIT
↓ 5s
reconcile
↓
remaining shares
↓
MARKET
```

一次 retry 后结束。

---

### 异常情况下非 RTH Exit

如果因为：

* executor downtime；
  -重启 recovery；
  -某次退出任务错过 RTH；

导致真正执行 Exit 时已经不在 RTH：

按照同一 Non-RTH fallback：

```text
1% LIMIT
↓
2% LIMIT
↓
2% LIMIT
↓
stop
```

而不是强行发 Market。

---

# 二十一、Entry 与 Exit 可以共用一个 Execution Primitive

实际上我认为代码最适合抽象成：

```text
execute_side(
    side,
    target,
    session,
    retry_policy
)
```

其中：

### Entry target

```text
target = TARGET_NOTIONAL
```

### Exit target

```text
target = POSITION_QUANTITY
```

价格：

```text
BUY:
ask × (1 + tolerance)

SELL:
bid × (1 - tolerance)
```

Retry：

```text
RTH:
LMT 1%
→ MKT

NON_RTH:
LMT 1%
→ LMT 2%
→ LMT 2%
```

整个系统因此没有四套不同 execution logic。

---

# 二十二、建议的 Entry State Machine

```text
POLICY_HIT
    ↓
CHECK_DIRECTION
    ├── disabled
    │      → DIRECTION_DISABLED
    │
    └── enabled
           ↓
       DETERMINE_SIDE
           ↓
      FETCH_FRESH_QUOTE
           ↓
      CALCULATE_PRICE
           ↓
      CALCULATE_QTY
           ↓
       SUBMIT_LIMIT
           ↓
         WAIT 5s
           ↓
        RECONCILE
           │
      ┌────┴────┐
      │         │
 complete    remaining
      │         │
      ↓         ↓
   FILLED   session?
               │
          ┌────┴────┐
          │         │
         RTH      NON_RTH
          │         │
         MKT       LMT 2%
          │         ↓
          │       WAIT 5s
          │         ↓
          │      RECONCILE
          │         ↓
          │      LMT 2%
          │         ↓
          │       WAIT 5s
          │         ↓
          └────→ FINAL RECONCILE
                       ↓
             FILLED / PARTIAL / FAILED
```

---

# 二十三、然后立刻创建 Exit Schedule

只要：

```text
position_qty != 0
```

：

```text
calculate relevant RTH session
↓
scheduled_exit
=
session_close - 30min
↓
persist exit schedule
```

这应该发生在 Entry 完成后立即确定。

不要等尾盘 scheduler 才现场判断：

> “这笔仓位应该今天还是明天卖？”

---

# 二十四、关于你的 1% / 2% tolerance，我的最终评价

我认为可以。

但应该明确它优化的是：

> **成交确定性**

而不是：

> **价格安全性。**

例如：

```text
BUY ask = $100
limit = $101
```

并不意味着 IBKR 会直接以 $101 买。

Limit Buy 的意思只是：

> 可以在 `$101` 或更好价格成交。

正常情况下如果 `$100` 卖盘足够，还是会从最佳可成交报价执行。

真正的代价是在：

```text
新闻爆发
+
报价快速跳动
+
流动性稀薄
```

的时候，你允许最多追到这个范围。

对你目前明确：

> DoxAgent 核心优势是速度，并假设 Policy 触发时 alpha 尚未被完全交易

这种策略定位来说，我认为 1% 首单、非 RTH 2% retry 是一个可以拿 Paper 数据进一步验证的初始参数。

---

# 二十五、我建议 Paper 阶段重点记录这几个东西

这不是增加策略逻辑，只是为了以后知道参数好不好。

每个 attempt 记录：

```text
session
side

quote_bid
quote_ask

tolerance
limit_price

submitted_qty

filled_qty
avg_fill_price

time_to_first_fill
time_to_final_fill

retry_number
order_type
```

最终再算：

```text
spread_at_submission

fill_vs_ask   # BUY
fill_vs_bid   # SELL

effective_slippage_bps
```

然后实际回答：

```text
1% 是否必要？
5 秒是不是太长？
多少订单第一秒已经成交？
2% retry 实际用了几次？
MKT retry 的真实价格损失多大？
```

Paper 的成交模型与实盘并不完全相同，所以这些数据主要用于验证**状态机和参数方向**，不能直接当实盘 slippage 估计。

---

## 最终我认为第一版可以冻结成这张表

| 项目                   | 规则                                       |
| -------------------- | ---------------------------------------- |
| Paper LONG           | 执行                                       |
| Paper SHORT          | 执行                                       |
| Live Cash LONG       | 执行                                       |
| Live Cash SHORT      | `DIRECTION_DISABLED`                     |
| Position allocation  | `$20,000` configurable                   |
| Fractional shares    | 不使用                                      |
| Initial BUY          | `ask × 1.01`                             |
| Initial SELL         | `bid × 0.99`                             |
| Initial wait         | 5s                                       |
| RTH retry            | 剩余仓位/预算一次 MKT                            |
| Non-RTH retry 1      | fresh quote ±2% LMT                      |
| Non-RTH wait         | 5s                                       |
| Non-RTH retry 2      | fresh quote ±2% LMT                      |
| Max non-RTH retries  | 2                                        |
| 每次 retry 前           | cancel + final fill reconciliation       |
| Entry retry quantity | 按剩余 target notional 和新 order price 重算整数股 |
| Exit quantity        | 当前实际全部净持仓                                |
| Exit time            | 下一适用 RTH `close - 30min`                 |
| Exit initial order   | 同样 ±1% marketable LMT                    |
| Exit retry           | RTH=MKT；非RTH=2% LMT ×最多2                 |
| AI risk/review       | 无                                        |
| Trade decision       | Policy 确定                                |
| Execution            | deterministic IBKR TWS API               |

这版我认为已经足够简单，同时把**订单 race condition、部分成交、不同交易时段、short 账户能力和退出调度**这些真正会导致程序实盘出错的问题闭合了。