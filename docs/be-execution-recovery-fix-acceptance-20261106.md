> 后续复发与验收更新：见 [completed_orders 复发修复与 LIVE 单股验收](completed-orders-recurrence-fix-live-acceptance-20261106.md)。上一版 API 重连不足以清除 Gateway 全局 pending；后续已调整历史查询依赖、恢复 Gateway，并完成新授权的 LIVE BUY ONDS 1 股成交。

# BE失败计数与LIVE完成订单查询：修复、发布与验收

日期：2026-11-06，北京时间。代码 `515d66ef` 已提交并推送；运行镜像 `4b87d1097974`。

**计数缺陷已修复，订单查询时序及超时恢复已修改并远端发布。157项相关回归通过，PAPER完成一股买卖及完整对账。LIVE严格只提交一次ONDS 1股买单，被IBKR 201邮件验证要求拒绝；成交0、最终Cancelled、持仓0，没有重试。LIVE真实成交回报这一项尚未验收通过。**

## 1. 实际根因：Gateway历史日志已取到

通过IB Gateway自身 File → Gateway Logs → Export Logs 导出11月5日日志。原始明文只存于服务器私有目录，完成摘录后压缩为约3.5MB，未上传到Git或外部服务。

| 北京时间11月5日 | Gateway记录 |
|---|---|
| 01:30:34 | 完成订单查询正常返回0行，证明空列表本身可以正常完成 |
| 13:15:03.419 | 后端重连中，FIX authentication尚未完成；Gateway记录不能发送消息 |
| 13:15:03.489 | 此时已收到client82的completed-orders请求 |
| 13:15:03.493 | executions尚未恢复，请求进入等待队列 |
| 13:15:03.494 | completed-orders查询交给pending initialLoad() |
| 13:15:13.728 | executions恢复并返回0条；同轮completed-orders没有结束 |
| 13:15:14.657 | completed-orders仍委托给pending initialLoad() |
| 13:16:08.964起 | 后续完成订单请求持续被拒发：已有pending request |
| 22:06:01.310 | BE意图出现前，仍是同一pending故障 |
| 23:45:12 | Gateway按DAILY自动重启恢复会话 |
| 23:45:34.237 | 重启后completed-orders重新返回0行 |

关键本机日志：

```text
Query completed orders for:20261105 is delegated to pending initialLoad()
Completed orders request cannot be sent for serverId=82 due to another pending request.
```

这将此前“客户端能握手但无结束回调”的边界进一步定位到：**IBKR后端重连/初始化时，Gateway的完成订单查询陷入未结束的initialLoad/pending状态；后续请求也无法继续。**当天自动重启清除了该状态。不是CASH配仓、整数股、零完成订单、单纯客户端解码漏回调或仅仅5秒太短。

原实现会并行发open orders、completed orders、executions，且每次超时继续复用健康标记仍为true的同一socket。这会在Gateway尚未恢复时提前压入完成订单查询，并让没有请求ID的旧查询/迟到End与后续重试混在一起。新连接也不能保证清除Gateway内部已经存在的全局pending状态，上一轮独立client探测和本轮日志均支持这一点。

Gateway当前版本中这一内部状态的全部实现细节没有源代码级证据；本轮修复我们能控制的初始化时序、请求生命周期和错误计数，不宣称修改了IBKR专有实现或保证其后台服务永不故障。

## 2. 精益修复

### 2.1 任务失败预算与账户冷却分开

修改 [executor.py](../src/doxagent/trade_execution/executor.py) 和 [repository.py](../src/doxagent/trade_execution/repository.py)：

- abandon_entry只看该job自身entry_failures/failures，不再取账户恢复累计值的最大值。
- 最终mark_submitting也去掉账户累计次数阈值，避免只修前置守卫、最终发送仍被旧计数阻断。
- 账户retry_at仍是共享冷却。若新的账户故障发生在检查与发送之间，最终事务守卫阻止发送，但不把它伪记为这个新job已经失败490次。
- job自己的三次实际故障、队列时效、意图过期及终态保护继续生效；EXIT恢复责任继续保留。
- 未清理或迁移旧失败组。BE、INTC仍按原终态保存。

现有字段足够，没有增加账户故障epoch、分布式锁、后台服务或新的计数表。账户维护探测可以继续共享退避，不给尚未实际执行的其他任务扣掉全部预算。

### 2.2 查询顺序及超时生命周期

修改 [ibkr_session.py](../src/doxagent/trade_execution/ibkr_session.py)：

```text
连接 → reqOpenOrders/End
     → reqExecutions/End
     → reqCompletedOrders/End
     → positions/End
     → account/End
     → CASH已结算现金
```

- 在现有sync_lock内连接并顺序完成订单查询；不在Gateway正在加载open orders/executions时同时压入completed-orders。
- 真正收到completedOrdersEnd才认为此阶段完成；0行且有End是有效空结果，缺End仍失败。
- 全量sync超时后关闭旧socket，下一次按原账户2/5/10/30/60秒退避重新连接；不在尚未结束的固定ID查询上直接叠加新请求。
- 原始sync_failure、连接generation、关闭动作写入既有事件链，便于下次区分“握手正常”与“哪一步对账不完整”。
- 保持持久attempt、UNKNOWN/SUBMITTING、撤单确认、有效成交去重及剩余数量处理；关闭socket本身不代表订单撤销、未发送或零成交，不盲目补单。

### 2.3 Gateway一项配置

LIVE Gateway实查：Read-Only API未勾选，Socket port=4001；“Download open orders on connection”原为勾选。已将**连接时自动下载订单关闭并Apply**，让执行端显式顺序查询成为统一来源，消除自动首包与显式固定ID查询的竞态。没有修改交易权限或自动重启时间；本轮未重启Gateway。

自动下载是额外竞态来源，本轮没有单独通过重现initialLoad死锁证明它是唯一触发条件。历史日志确认的是重连期间pending initialLoad；程序顺序和配置同时消除可控制的重复/过早请求。

## 3. 参考与验证判断

[IBKR完成订单接口](https://www.interactivebrokers.com/docs/tws-api/doc/order-management/completed-orders)、[官方日志导出](https://www.interactivebrokers.com/docs/tws-api/doc/troubleshooting-support/log-files/exporting-logs)。

社区线索与本机日志交叉核验：

- [ib_insync #224](https://github.com/erdewit/ib_insync/issues/224)：同类Gateway pending request导致完成订单查询超时。本机已取得同类错误，不再仅凭社区推测。
- [ib_async #216](https://github.com/ib-api-reloaded/ib_async/issues/216)：无请求ID的并发请求覆盖及迟到结果错配；采用现有锁内顺序和超时后连接隔离解决我们自己的生命周期，无须切换SDK。
- [ib_async #128](https://github.com/ib-api-reloaded/ib_async/issues/128)：完成订单回调的另类错误。本机此前reader存活、未收到相关帧，未发现该类KeyError，未据此扩大改造。

| 验证 | 结果 |
|---|---|
| 相关后端回归 | **157 passed，0 skipped**，120.52秒；使用已部署官方SDK |
| 新job遇到历史账户故障3/490 | 等待共享冷却，不继承历史失败；恢复后可正常执行 |
| job自身三次失败 | 保持永久放弃；重启/重复投递不复活 |
| 发送前新账户故障竞态 | 阻止发送、不虚增job预算；PREPARED释放 |
| 空完成订单 | 有End正常完成；缺End超时、断连，不当成空成功 |
| 查询串行与单飞 | 确定性回归通过，重复全量同步不覆盖另一轮End |
| UNKNOWN/部分成交/撤单/EXIT/账户归属 | 原相关回归通过 |
| 候选LIVE只读完整sync | 0.908秒完成，completed_orders 0.002秒 |
| 候选PAPER只读完整sync | 0.867秒完成，completed_orders 0.001秒 |
| Ruff与差异检查 | 通过 |

## 4. 发布与实际PAPER验收

仅派生更新Executor的三个源文件，保留既有服务环境/命令；源哈希与提交核对通过。未纳入当前工作区其他Docker、模型、W3、API等未提交修改。

PAPER专项：ONDS/SMART/NASDAQ/USD，conId **455415330**。

- 北京时间04:47:39，BUY 1，限价4.62，成交4.59。
- 04:47:40，SELL 1，限价4.55，成交4.58。
- 两张限价单均Filled，fills与完整订单/持仓/资金对账一致；ONDS从0到1再到0。
- 成交后的completed-orders阶段分别0.021秒、0.048秒完成。

专项验收通过独立、受限脚本运行。每次执行前确认正常策略没有待执行job和owned持仓；短暂停止正式Executor，专项进程取得**同一个WriterLock**，完成后恢复正式Executor。未让两个订单写入者并行运行。脚本没有创建普通Runtime交易意图，故不会生成对冲腿或自动退出任务。

## 5. 严格一次LIVE验收：券商拒单，未成交

账户LIVE/CASH，冻结profile `ep_69fe18f1c528fd9ca3644519`。下单前完整sync成功，AccountReady=true，USD已结算现金/AvailableFunds均 **2262.83**；订单金额加费用保留低于DoxAgent可用资金及475美元名额限额。已有ONDS数量0。

预检L1：Bid **4.58**、Ask **4.59**，双方正数数量、实时market_data_type=1、接收时间在5秒内；价差约0.218%。按Ask×1.005及市场价格档位得到 **LMT 4.62**。

唯一提交：

| 字段 | 值 |
|---|---|
| 合约 | ONDS / STK / USD / SMART，主交易所NASDAQ |
| 方向、数量 | **BUY 1** |
| 订单类型、限价 | LMT，**$4.62** |
| 时间 | 2026-11-06北京时间04:52:34附近，美东11月5日15:52盘中 |
| client / order | 82 / **1** |
| orderRef | `DA-ACCEPT-ONDS1-LIVE-BUY-20261106` |
| permId | **1905464174** |
| 实际placeOrder调用 | **1次** |
| 成交 | **0股** |
| 最终状态 | **Cancelled**，保留原始201拒单记录 |

IBKR原始拒单要求：账户所有人需登录Client Portal，使用其发送到邮箱的token完成验证，然后等待几分钟再重新发送订单。本轮没有访问用户邮箱、代做账户验证或修改账户安全设置。

这里不是DoxAgent又遇到了490计数或completed-orders超时：下单前后完整sync都成功，真实订单获得permId、Inactive/201/Cancelled回报；发送后completed_orders 0.071秒完成。**实际成交回报仍因外部账户验证限制而没有LIVE样本，不能标成通过。**

验收脚本使用持久O_EXCL一次性提交标记、fsync和实际placeOrder字段断言；即使状态不明也不再次发送。没有LIVE对冲、补单、第二张买单或卖单。后来增加终态验收回执以永久封住这个测试ID，没有给它留下可自动重试状态。

北京时间04:55:38，另一独立只读连接再查：唯一匹配orderRef为上述Cancelled订单，fills为空，ONDS持仓0；完整sync再次通过，completed_orders 0.006秒。后续若需实测成交，须用户完成券商验证并**另行授权新订单**；此次一次授权已使用。

## 6. 旧失败与当前运行状态

最终核对：

- BE `trade:case_8da7542affa54cba98d711513ce8c7df`：FAILED / ENTRY_FAILURE_BUDGET_EXHAUSTED / CLOSED，attempt=0。
- INTC `trade:case_2e138775e1fd4e159988a9682551f3aa`：FAILED / NO_VALID_INTEGER_COMBINATION / CLOSED，attempt=0。
- 无正常策略待执行job；两笔旧失败没有重放或清零。
- 正式Executor已恢复运行，镜像4b87d1097974；专项辅助容器已退出，临时密钥env文件已删除。

专项订单记录独立保存于服务器私有验收目录，未伪造W3或业务Case，未向正常策略账本导入一笔不存在的成交。因此“旧执行组attempt=0”不代表本轮没有提交LIVE订单；本轮确实提交且只提交了上述一次被拒买单。

## 7. 证据与边界

报告和脱敏证据目录：[eval/be-execution-repair-20261106](../eval/be-execution-repair-20261106)。重点：`gateway-pending-origin.json`、`gateway-initial-load-origin.json`、`tests.xml`、`candidate-readonly.json`、`release-verification.json`、`acceptance-evidence.json`、`live-one-shot-outcome.json`、`final-readonly.json`。

服务端私有构建/验收目录：`/var/lib/doxagent/releases/be-repair-515d66ef/`；Gateway原始日志压缩档为桌面用户私有目录中的`gateway-live-20261105.txt.gz`。未导出登录凭据，报告不包含完整账户号。

本轮没有添加常驻服务、数据库表、复杂风险模型或新的自动重试策略。Gateway内部服务仍可能出现外部异常；修复保留可见失败、共享退避和严格对账，尤其不会把一个丢失End回调当成“无订单”后盲目补单。一次健康验收不等于跨下一次后端重连长期运行的证明，本轮未伪造这种覆盖。

根目录changelog已记录代码及交付变更。LIVE真实成交验收待券商账户验证完成及新的明确授权。
