# 交易执行 DELIVERY_UNKNOWN 排查与修复方案

排查时间：北京时间 2026-10-21。范围：新加坡生产部署的 PAPER 交易。仅只读查询生产 SQLite、容器日志及运行源码；未调用券商下单/撤单/恢复接口，未修改生产数据、配置或重启服务。本轮交付为调查与方案，不是修复验收。

## 1. 统计口径与结果

以 Runtime 同一只读事务的快照时间 UTC 2026-10-20 16:57:06 为截止，向前滚动 14 天，即北京时间 **10-07 00:57:06 至 10-21 00:57:06**。交易日列使用系统 semantic_day，不与北京时间自然日混用。

按 intent_id 关联 trade_intents → te_executions → ENTRY jobs → attempts → fills。成交按同账户 execution family 的最高 correction 计算，不把接受、DONE 或 broker_status 当成交。Read 数据为稍后独立快照，不声称跨库原子一致。

| 结果 | 数量 | 解释 |
|---|---:|---|
| 意图记录 | 13 | 包含正常重复拦截 |
| 有实际 ENTRY 成交 | 2 | 10-07 BE LONG 57 股；10-14 INTC SHORT 165 股 |
| 零成交 | 11 | 含 1 条正常重复拦截、2 条截至快照仍等待的意图 |
| 实际接受投递 | 12 | 全部在 te_executions 有持久接收记录 |
| 实际接受后零成交 | 10 | 不能全部归为投递故障 |
| 最近 7 天 | 10 | 1 成交、9 零成交，含重复拦截 |

零成交拆分：

| 交易日 | Ticker | 数量 | 账本状态/原因 | 深层证据 |
|---|---|---:|---|---|
| 10-06 | INTC | 1 | INSUFFICIENT_MARGIN | 时间落在北京时间统计窗口内；无 ENTRY 发送 |
| 10-09 | MU | 1 | RETRY_BUDGET_EXHAUSTED | 3 次盘前 LMT 发送，均 Cancelled，零成交 |
| 10-14 | RKLB | 1 | EXPIRED_SEMANTIC_DAY | 过期前 job.error=WAIT_CYCLE_EQUITY；无 ENTRY attempt |
| 10-14 | RKLB | 1 | DUPLICATE_POLICY | 正常去重，无接收/发送 |
| 10-15 | RKLB | 1 | EXPIRED_SEMANTIC_DAY | WAIT_CYCLE_EQUITY；无 ENTRY attempt |
| 10-19 | INTC | 3 | EXPIRED_SEMANTIC_DAY | BROKER_ORDER_SYNC_INCOMPLETE；无 ENTRY attempt |
| 10-19 | MU | 1 | EXPIRED_SEMANTIC_DAY | BROKER_ORDER_SYNC_INCOMPLETE；无 ENTRY attempt |
| 10-20 | INTC / MU | 2 | WAIT_QUOTE，尚无 entry_result | 实际 job.error=WAIT_CYCLE_EQUITY；Read 错报 DELIVERY_UNKNOWN；无 ENTRY attempt |

因此，零成交的 11 条中，10 条没有发送 ENTRY 订单，另 1 条发送了 3 次但未成交。获准投递的 12 条中，9 条未发送 ENTRY 订单、1 条已发未成交、2 条成交。10-19 和截至快照的 10-20 均无新的 ENTRY 成交。

## 2. 根因一：DELIVERY_UNKNOWN 是已接收执行的错误展示

两条当前 badcase：

- INTC：`trade:case_0b5a60b35b174fbfa3f15a067d76a832`，UTC 07:42:27 释放，07:42:28 持久接收。
- MU：`trade:case_fef78df457004eff92e0b4704f5970cd`，UTC 11:37:25 释放，11:37:26 持久接收。

两者 trade_intents.status=EXECUTION_ACCEPTED，delivery task=SUCCEEDED，te_executions 和 ENTRY job 均存在。并不存在本轮持久账本证明的投递丢失。

`TradeOutputService.deliver()` 在调用 intake 前把 intent.status 持久化为 UNKNOWN；`ExecutionRepository.admit()` 把当时的 intent 嵌入 execution。该嵌入是接收前的快照，之后外层 intent 已变成 EXECUTION_ACCEPTED，但嵌入仍为 UNKNOWN。

`v2_read/executions.py::ExecutionProjector.project()` 使用嵌入的 UNKNOWN，同时设置 intake_status=EXECUTION_ACCEPTED。`trade_outcomes.py::classify_execution()` 先判断 UNKNOWN，后判断 EXECUTION_ACCEPTED，故尚未出现 entry_result 的执行被错分为 DELIVERY_UNKNOWN。已有成功/失败 entry_result 会覆盖此分支，使问题表现为“正在卡住的意图显示投递未知”。

本地对两条真实 Read summary 重放得到 UNKNOWN/DELIVERY_UNKNOWN；仅将已证实接收的 intent 状态归一化后得到 PENDING。证据在 summary.json。这是分类验证，不是生产修复。

## 3. 根因二：瞬时故障永久终止退出，遗留仓位阻塞全账户周期

关键链路：

1. UTC 10-14 12:07:31，INTC SHORT 成交 165 股。
2. 计划退出 UTC 10-14 19:30，即美东 15:30、北京时间 10-15 03:30。
3. UTC 19:30:03，执行 gap 记录 IBKR_RECONNECT_BACKOFF，随后 EXIT_FAILED；该 EXIT 没有创建任何 order attempt。
4. `Executor.step()` 的通用异常处理在 failures 达到 3、且非 ENTRY、无未决订单时调用 repo.finish()。重连退避也计入该预算，因此几秒内就用尽。
5. repo.finish() 发现仍有仓位，写 job.state=FAILED。`ExecutionRepository.jobs()` 不再选择 FAILED。连接恢复或容器重启也不会恢复它，`_rebuild_lots()` 不会把 FAILED 退出自动变为 READY。
6. 当前 te_lots 仍有 INTC remaining_qty=165、sign=-1；最新已有券商同步快照 UTC 10-20 16:57:05 也报告 INTC quantity=-165。此处只读取执行器已经获得的快照，未建立另一个券商连接。
7. te_account_cycles 最后有效周期为 10-14。`cycle_for_entry()` 在任意旧 owned lot 未平时返回 None，执行器写 WAIT_CYCLE_EQUITY。`capture_ready_cycles()` 同样跳过旧仓位。

直接由 WAIT_CYCLE_EQUITY 证实的有 4 条：10-14/15 两条过期 RKLB，以及 10-20 两条等待中的 INTC/MU。10-19 四条保留的最后错误为同步不完整，不能把其每次失败都说成资金周期阻塞；但未解决的旧仓位同样会阻止它们在同步恢复后进入新周期。

这不是“当日/前日没有成交却误触发每日交易次数上限”，而是更早的未退出仓位仍在影响账户。

## 4. 根因三及放大因素：同步、写锁与进程生命周期

- 当前 executor 容器 RestartCount=39；其保留日志中恰有 39 次 `sqlite3.OperationalError: database is locked`。可见栈包含主循环 drain_events、heartbeat 等写入/事务入口。delivery RestartCount=53，尾部同样有写锁超时栈；未对其全部 53 次逐次归因。
- `ExecutionRepository.get()/jobs()`、`Executor.drain_events()` 读取及多处检查使用默认 write=True 的 transaction，因此纯查询也执行 BEGIN IMMEDIATE，与真正写入争锁。worker 顶层缺少对此类短暂争用的循环恢复，异常会退出进程。
- 同一个 broker 虽有 sync_lock，却是把每个 job 的同步串行排队执行，没有共享同一轮完整快照；`sync()` 每次重发 orders/executions/positions/account 请求并订阅再取消 account updates。窗口内有 **23,052 条 2100** 信息写入 te_events；`drain_events()` 对这些消息仍逐条更新 cursor，放大写入。
- 2100 表示账户更新订阅被取消/替换，不能把它当订单拒绝。官方说明：https://www.interactivebrokers.com/docs/tws-api/doc/error-handling/error-codes 。目前不证明每条 2100 都是另一个客户端抢占。
- 10-19 四条无订单意图各累计约 181–182 次失败，最后错误 BROKER_ORDER_SYNC_INCOMPLETE。这个合并错误只说明 openOrderEnd 或 completedOrdersEnd 未按时到齐；没有阶段耗时/代次日志，不能确定每次是 Gateway 未回、锁延迟还是客户端回调延迟。10-20 两条还曾保留 HANDSHAKE_TIMEOUT 的 gap。不能仅提高超时就宣称解决。
- IBKR 回调 `_emit()` 同步写 SQLite，会延迟同一读线程后续 End 回调；这是可由代码确认的放大路径，但缺历史阶段测量，不能宣称已逐次证明。

运行源码归一化换行后与本地比较：executor、worker、repository、ibkr_session、trade_output、executions、trade_outcomes 一致。**运行 journal.py 与当前本地不同**；本地已有 retry_write 等其他工作，不能误称已经部署，修复应与正在进行的 Runtime 事务工作衔接，避免重复改造。

## 5. 建议实施方案：小范围解决持续退出与有效执行

### P0：恢复退出义务，修正读状态

1. 将明确的连接、重连退避、同步超时、SQLite BUSY/LOCKED 作为基础设施瞬态错误处理。每次请求有界，按既有短退避重试；**不得用累计三次瞬态故障永久结束仍有仓位的 EXIT**。不重置/增加 order generation，不重复创建订单。有未决 attempt 继续原单对账；账户/所有权/权限/参数等确定错误保留显式阻塞和原因。
2. Worker 启动及低频巡检复用已有 lots/jobs：恢复“过期应退出、有剩余 owned lot、因已知瞬态原因 FAILED”的原退出任务。先检查同账户同标的未决订单，复用原任务/attempt，不对所有 FAILED 一律解封。无需新服务、新队列或另设守护进程。
3. 已存在 te_execution 持久接收证据时，Read summary 使用 EXECUTION_ACCEPTED 语义。分类以持久 intake 优先于嵌入旧 UNKNOWN。显示等待阶段及真正 job.error（例如 WAIT_CYCLE_EQUITY），不再统称 WAIT_QUOTE/DELIVERY_UNKNOWN。失效 intent_id 没有接收证据时仍按原投递对账处理。
4. 历史 Read 数据需要对受影响 execution 和 case rollup 定向重投影；仅改分类函数不会自动修复已有物化记录。

### P1：消除不必要争锁与同步堆积

1. 纯读事务明确 write=False，保留 admission、配额分配、attempt 创建、fill/cursor 落账的原子写事务。不要整体串行化不同 ticker，也不要通过提高 busy_timeout 掩盖问题。
2. 与本地已有 Runtime retry_write 工作衔接，只重试完整、短小、rollback-safe 的数据库事务。worker 遇明确争用保持运行并继续原任务；**绝不把 broker.submit/cancel 放进通用事务重试闭包**。事件实际应用成功前不推进 cursor，避免把锁冲突记成业务 gap 后越过 fill。
3. 同账户同连接代次共享一次正在进行的完整 sync，短期有效快照供同批无订单变化的任务复用；发送/撤单、重连及要求最终成交对账时强制失效。先做 single-flight，缓存有效期只用于明确无变化场景。保留不同 ticker 的研判/执行并发与账户原子配额。
4. 2100 等无业务状态变化的信息去重/计数，事件 cursor 按成功批次推进，减少无意义写入。fill/order/error 不能为降压而静默丢弃。为 open/completed/fill/position/account End 分别保留耗时及连接代次，以后错误直接指明缺哪一步。

### 资金周期边界

首选修复退出后沿用现有“旧仓位清零才冻结下一周期 E”的契约，直接解除本次永久阻塞；不额外添加每日/前日无成交证明或多级审批。

若要求在旧仓跨日仍继续开新仓，必须改成“当前权益快照 + 全部遗留仓位/活动订单计入总额度”，这会改变现有周期策略，应单列策略变更，不能只删除 open_lot 检查。本次无需以此扩大修复。

### 历史处置与验收

- 原 INTC 空仓退出任务的恢复会触发真实 PAPER 订单，应在实施阶段按实际券商持仓/未决订单对账后恢复原任务；本轮未执行。
- 已过期的 6 条意图不重放；当前 2 条在实施时重新检查原 expires_at，仍有效才自然推进，过期则留真实终态。
- 测试覆盖：超过三次瞬态故障后连接恢复仍能退出；进程重启恢复已失败瞬态 EXIT；未决订单不重复发；fill correction 不重复平仓；一个 ticker 退出故障不饿死其他 ticker；读锁不拿写锁；SQLite BUSY 恢复不重复外部动作；两条真实 summary 不再 DELIVERY_UNKNOWN；重投影后 case/execution 一致。
- 再做小范围 PAPER 验证：原持仓退出数量与 broker/lot 一致、下一资金周期可建立、有效新意图能进入 attempt、已过期意图零新增下单、稳定期间无锁超时退出。不用“服务 Up”代替成交验收，也不承诺新市场信号一定成交。

## 6. 证据与复核

目录 `eval/trade_delivery_audit_20261021/`：ledger.json 为 Runtime 原始只读快照；read_model.json 为 Read 快照；support.json 包含 lot/cycle/gap/同步事件统计；deployed_sources.json 为运行源码；executor.log 与 latest_executor.txt 为日志；intent_audit.csv 为逐条完整 ID 台账；summary.json 为统计与真实分类重放；build_audit.py 可离线重算统计。

本轮未修改业务代码，因此未执行生产部署或下单验收。原有 dirty checkout 与 changelog 不作覆盖。
