# BE 实盘意图未执行：根因与机制诊断

日期：2026-11-05，北京时间。范围：只读诊断；没有修改代码/配置/生产数据库、重启服务、清零正式恢复状态、重新入队或下单。

## 1. 结论

这笔交易不是 BE 自己下单失败了490次，也不是三张限价委托均未成交。**在自己的第一次执行尝试前，账户已有恢复周期的累计失败数490被复制为新意图的 entry_failures，越过阈值3，约0.21秒内即被终态放弃。**

直接缺陷在 `Executor.abandon_entry()`：它取“本任务累计失败、本任务失败计数、账户恢复累计失败”三者最大值，不区分哪些账户故障发生在这个任务创建之前。

同时，LIVE 确有独立的底层对账异常：诊断开始时不断等待 `completedOrdersEnd` 超时。独立新连接同样能握手，但10秒内没有收到完成订单查询的结束回调；同代码、同SDK访问 PAPER 约0.44秒结束。当前证据将问题定位到 LIVE 的 completed-orders 响应/会话链路，不能确定 IB Gateway 内部为何没有响应。

诊断过程中观察到正式 client82 建立 generation4 连接，并在北京时间23:45:36完成全量同步；截至23:52:19，账户恢复状态已为空。**BE仍保持 FAILED/CLOSED、零委托零成交。**账户恢复未复活旧单，计数口径缺陷本轮未修复。

## 2. 身份与时间线

- 标题：NTT DATA Announces Agreement with Bloom Energy for 900 MW of Onsite Power Generation Across Multiple Data Center Projects - Stock Titan。
- Case：`case_8da7542affa54cba98d711513ce8c7df`。
- 执行组：`trade:case_8da7542affa54cba98d711513ce8c7df`。
- LIVE/CASH，账户按实际账户隔离；冻结 profile `ep_69fe18f1c528fd9ca3644519`。
- LONG BE；开启66%名义对冲，CASH方向为 BUY SQQQ；禁交易日列表为空。
- allocation_policy_version=2，已是本次整数配仓升级版本。没有进入配仓函数，plan和budget均为空。

| 北京时间 2026-11-05 | 原始 UTC | 事实 |
|---|---|---|
| 22:00:00 | 14:00:00 | 消息发布时间 |
| 22:04:24.196593 | 14:04:24.196593 | 源消息进入 Message Bus |
| 22:04:29.073409 | 14:04:29.073409 | Case创建 |
| 22:06:38.114481 | 14:06:38.114481 | 正式意图释放 |
| 22:06:38.910101 | 14:06:38.910101 | 执行组持久创建 |
| 22:06:38.921 | 14:06:38.921 | 首份MAIN job receipt：READY，failures=0，无entry_failures |
| 22:06:38.966554 | 14:06:38.966554 | 投递任务SUCCEEDED，回执EXECUTION_ACCEPTED |
| 22:06:39.125062 | 14:06:39.125062 | entry_abandoned_at：写入entry_failures=490 |
| 22:06:39.192 | 14:06:39.192 | 后续MAIN receipt：DONE/FAILED |
| 22:06:39.193203 | 14:06:39.193203 | HEDGE终结，MAIN_NOT_FILLED |

从Message Bus至释放约133.92秒，没有超过12分钟REALTIME延迟保护。意图释放时为美东09:06盘前，但在进入盘口观察、报价、整数配仓之前就被放弃；不能将本次失败归因于30秒盘口门槛。

`runtime_v2_cases` 的 COMPLETED 说明上游研判结束；投递回执 EXECUTION_ACCEPTED 说明持久接收成功，均不证明订单已发送。

## 3. 代码机制与证据

### 3.1 两种“三次”不同

- ENTRY最多三张真实订单机会：统计已提交的订单尝试。
- ENTRY_FAILURE_LIMIT=3：执行/恢复故障的放弃阈值。本次命中的是这一条，不是资金预算不足，也不是三张订单已发出。

现行计算在 [executor.py:207](../src/doxagent/trade_execution/executor.py#L207)：

```python
failures = max(
    current.get("entry_failures", 0),
    current.get("failures", 0),
    self.recovery.state(current["account"]).get("failures", 0),
)
```

达到3后，把这个最大值持久保存为任务entry_failures，写入entry_abandoned_at；无在途订单时直接finish。`step()`在任何业务broker操作前执行这一守卫；维护任务也每10秒检查同一守卫。

本次源receipt明确证明是 READY/failures=0 → entry_failures=490 → DONE/FAILED；没有BE自己的原始故障记录或attempt。终态receipt不保存调用栈，无法区分是step还是同一维护守卫首先进入，但两条路径都使用上述错误口径。

账户计数来自 [recovery.py:78](../src/doxagent/trade_execution/recovery.py#L78)：每次实际失败探测递增，并持久化同账户的冷却状态。成功后 [success():97](../src/doxagent/trade_execution/recovery.py#L97) 清空。**这是尚未恢复成功的这一轮累计值，并非账户终身计数。**冷却期间重复轮询不应重复增加账户计数；多个ticker共用该账户的2/5/10/30/60秒退避，稳定故障后约分钟级恢复。

共享冷却与保护已等待的旧意图本身有用途；缺陷是把新意图诞生前的累计故障也当作它已经经历的失败，因此当前故障周期累计≥3时，新ENTRY可以一出生即失败。

### 3.2 本地离线复现

在隔离SQLite和合成PAPER账户中，使用与生产哈希相同的Executor。broker factory被替换成“调用即断言失败”，没有真实IB连接：

| 账户已有恢复失败数 | 新任务初始failures | 守卫结果 | attempts / broker factory调用 |
|---|---:|---|---|
| 0 | 0 | 保持READY | 0 / 0 |
| 2 | 0 | 保持READY | 0 / 0 |
| 3 | 0 | 立即FAILED | 0 / 0 |
| 490 | 0 | 立即FAILED，entry_failures=490 | 0 / 0 |

在**离线合成库**清除恢复状态、重复接收、重新创建Executor后，原失败组仍未恢复。没有在生产库做这些清零/重复投递操作。

复现脚本：[reproduce_offline.py](../eval/be-entry-failure-diagnosis-20261105/reproduce_offline.py)。

## 4. LIVE对账为何阻断

[ibkr_session.py:356](../src/doxagent/trade_execution/ibkr_session.py#L356) 的每次全量sync均执行：请求open orders、completed orders、executions，再等待对应结束回调；之后才请求positions、account及CASH settled cash。

```text
reqOpenOrders / reqCompletedOrders(True) / reqExecutions
  → executions完成
  → open_orders完成
  → 等待completed_event（默认5秒）
  → positions → account → settled cash → complete=True
```

`completedOrdersEnd()`才设置completed_event。因此该结束回调不来，即使连接和握手都正常，sync也无法完成。首次诊断读取的错误为：

```text
BROKER_ORDER_SYNC_INCOMPLETE:
stage=completed_orders generation=3 elapsed=5.107s
```

这不是券商拒单回执。`elapsed`是整次sync已经经过的时间，不是订单等待5秒规则。恢复循环持续跑到574/575等后续累计值；完整sync不成功时，不能清除恢复状态。开始诊断时，最新成功sync甚至仍停在11月4日17:30:38 UTC。

[IBKR官方定义](https://www.interactivebrokers.com/docs/tws-api/doc/order-management/completed-orders)表明该接口查询给定交易日的已完成订单；apiOnly控制是否包含TWS手工订单。[官方EWrapper参考](https://interactivebrokers.github.io/tws-api/interfaceIBApi_1_1EWrapper.html)及部署SDK均提供completedOrdersEnd结束回调。本轮只查询历史订单，没有下单、撤单、what-if、账户订阅或行情订阅。

### 独立只读对照

正式client82完全保持原状；另建一次性独立client，采用部署中的同一个SDK，所有下单入口均设置拒绝写入guard：

| 连接/请求 | 握手 | 查询观察 | 结果 |
|---|---:|---:|---|
| LIVE，apiOnly=True | 0.603秒 | 10秒 | 0行，未收到结束回调，无请求错误 |
| PAPER，apiOnly=True | 0.594秒 | 0.443秒 | 5行，收到结束回调 |
| LIVE，apiOnly=False | 成功 | 10秒 | 无结束回调；socket/reader正常，无线程异常 |
| LIVE，apiOnly=True，独立探测内采用SDK原生非protobuf请求 | 成功 | 10秒 | 同样无结束回调；socket/reader正常 |

第三、四项还记录了解码入口的消息编号/长度，不记录订单原文：请求后没有收到相关输入帧，不像是本地有结束包却被wrapper漏处理。切换编码仅发生在一次性诊断进程内，未改正式执行进程或持久配置。

可以确认：当时的LIVE completed-orders链路确实不可完成，换一个client并未立即解决；不是仅把5秒改成10秒就得到完成，也不是只由apiOnly筛选或protobuf请求方式决定。

不能确认：IB Gateway内部的会话、交易日历史/缓存、IB服务器响应中究竟哪一层导致无返回。没有以真实LIVE单“种一条历史”做试验，也没有重启Gateway或修改设置来验证。Gateway打开的日志为加密格式，没有解密的内部调用证据；不能把“当日无API成交”等猜测写成已证实根因。

另外，恢复逻辑遇到此类请求超时会继续退避重试；只要socket仍被标记健康，connect()会复用现有连接，而非每次超时都新建连接。这解释了“能握手/显示连接”不等于交易前对账可用，也使健康socket上的请求停滞可能持续。

## 5. 诊断期间观察到恢复，BE仍不重开

- 23:45:33.886113：正式client82记录generation4 CONNECTED。
- 23:45:36.505880：正式sync成功，complete=True；completed_orders阶段立即完成，positions/account完成。
- 23:48以后：持久execution_recovery为空。
- 23:52:19最终只读核对：BE仍FAILED/CLOSED，MAIN和HEDGE仍DONE，attempt/fill/lot均0；原delivery仍SUCCEEDED、generation1、failures0。

本轮没有调用正式连接重连、重启任何服务、清空恢复状态或恢复任务。没有事件足以确定旧socket为何转入新连接，因此不把恢复归功于只读探测，也不声称Gateway内部问题已永久修复。生产执行器容器仍是此前发布的d804c69be470，启动时间00:24:48 UTC，RestartCount=0；八个相关源文件哈希与本地代码一致。

终态保护由原有机制完成：[admit():158](../src/doxagent/trade_execution/repository.py#L158) 对同ID不重复创建执行组/任务，step在DONE/FAILED直接返回，最终发送守卫也拒绝终态；groups.advance不重开已终结ENTRY。账户恢复只影响后续合格新交易，不补做这笔旧交易。

这笔意图的账面自然expires_at仍是11月6日07:00 UTC（北京时间15:00），与用户所说“机会已经过时”不同；**保持安全依靠现有永久失败终态，不通过篡改expires_at实现**。没有删除该Case、订单、故障历史或把失败改成成功。

## 6. 排除项、局限和后续修复边界

已排除作为本次直接原因：旧整数配仓约束、66%配比本身、950美元资金不足、CASH不能直接SHORT、禁交易日、盘口价差、三张委托机会用尽、W3超过12分钟。没有进入这些下游检查，不表示它们在当时行情下必然都会通过。

490之前的每一次故障及其发生时间无法完整还原：账户恢复payload只保留当前值，runtime_values的源receipt allowlist不捕获execution_recovery，容器相应窗口也没有故障明细日志。不能将诊断时看到的completed_orders错误倒写成“490次全部都是此错误”。这不影响新job被历史累计数直接放弃的因果证据。

后续修复应收敛在两点，本轮均未实施：

1. 区分账户共享退避与新意图自身的失败预算。意图创建前的账户故障不直接扣掉新意图的全部机会；等待中的意图仍保留过期、真实故障阈值和终态单调性。
2. 针对completed-orders停滞完善恢复与前置依赖范围，保留已发送/UNKNOWN订单的完整对账责任；不能简单把超时当空历史，也不能只清零计数或取消安全对账后直接下单。

仅改计数不能消除底层对账阻塞；仅恢复连接也没有修复新意图继承旧计数的代码口径。以后开发使用新PAPER测试，不恢复本BE和此前INTC失败组。

证据：[eval/be-entry-failure-diagnosis-20261105](../eval/be-entry-failure-diagnosis-20261105)，包含首次/最终状态、原job三份receipt、独立只读对照、恢复观测、源码哈希与离线复现。无交易代码变更，因此本轮不追加代码changelog、不部署。
