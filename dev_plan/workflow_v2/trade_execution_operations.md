# Trade Executor 操作手册

交付日期：2026-09-06。策略依照 `trade_execution_implementation_plan_20260906.md`；用户最终确认的 Exit 口径为各笔 DoxAgent 归属份额，反向成交按 FIFO 抵销。操作入口为 `python -m doxagent.trade_execution.cli`，与 Data MCP 只读 API 独立。

## 1. 安装与数据库

在仓库根目录执行 PowerShell 命令。沿用项目虚拟环境；交易会话额外要求官方 TWS API Python 包，本地验收版本 **ibapi 10.49.2 / TWS server 225**。`uv sync` 不会安装这份官方本地发行包；不要用旧 PyPI ibapi 替代。当前安装源为 `D:\TWS API\source\pythonclient`；新机器从 [IBKR 官方 API 下载](https://interactivebrokers.github.io/) 获取相同兼容发行版，然后执行：

```powershell
$py = '.venv/Scripts/python.exe'
uv pip install --python $py 'D:\TWS API\source\pythonclient'
& $py -c "from importlib.metadata import version; from ibapi.order_cancel import OrderCancel; print(version('ibapi'))"
```

先停止旧 Executor，再升级数据库。Runtime 与 Executor 必须使用同一个持久 Runtime SQLite；生产路径可不传 `--db`，由 `DoxAgentSettings.persistent_runtime_v2_sqlite_path` 决定。以下 `$db` 是本次**隔离验收库**，不要误认为生产库：

```powershell
$db = '.tmp/trade_executor_acceptance_20260906.db'
& $py -m doxagent.trade_execution.cli --db $db migrate --dry-run
# 已有库升级必须使用尚不存在的备份文件路径：
& $py -m doxagent.trade_execution.cli --db $db migrate --backup '.tmp/trade_executor_before_upgrade.db'
```

迁移新增 `te_*` 表和 `trade_execution/active` 指针，不改历史 Runtime 结果。历史没有 execution_pin 的意图继续 output-only，不能通过激活配置重放成订单。本期 schema version=1。备份使用 SQLite backup API；跨机器搬迁必须停旧进程并保留完整库，不能只拷贝正在写入的主数据库文件。

## 2. 账户发现、配置与热切换

```powershell
& $py -m doxagent.trade_execution.cli --db $db bootstrap-profile --profile-id paper-local --environment PAPER --port 7497 --client-id 82
& $py -m doxagent.trade_execution.cli --db $db bootstrap-profile --profile-id live-local --environment LIVE --port 7496 --client-id 81
```

发现端口唯一账户后生成 immutable revision，但不自动激活。多账户情况使用 `discover-accounts` 和显式配置文件。示例位于 `trade_execution_profiles/paper.example.json`、`live.example.json`；示例账户必须替换为实际发现值。精确账户存库，CLI 输出脱敏。端口不是环境识别依据：提交/撤单必须匹配 managedAccounts 和固定账户；Paper/Live 映射、同账户 clientId 也校验。

```powershell
& $py -m doxagent.trade_execution.cli --db $db import-profile --file 'path/to/paper.json'
& $py -m doxagent.trade_execution.cli --db $db activate-profile --profile '<ep_revision>' --reason 'Paper trial'
& $py -m doxagent.trade_execution.cli --db $db status
```

后续修改金额、方向、行情源或策略参数：编辑配置副本 → import → activate。配置激活检查账户连接，使用独立只读 clientId，不抢占 writer。每笔 TRADE 在 Runtime 持久化时原子 pin 当时 profile；切换只影响之后释放的交易，既有重试、成交份额及退出继续使用原配置和原账户。Live Cash 默认只开 LONG，SHORT 得到 `DIRECTION_DISABLED`；退出不被 entry 方向开关禁止。

可将 `quote_profile_revision` 指向另一个已导入的账户 profile（例如 Paper 使用 Live 的已授权实时行情）。行情请求必须是同 conId、同 venue、实时 type=1、新鲜 side quote。行情会话不会因此承担另一账户的订单。行情权限修复后重新探测/重启连接；已经失败的 entry 不自动制造新交易。

本次隔离库已导入并验过：

| 环境 | Revision | Endpoint | 账户展示 |
|---|---|---|---|
| Paper | `ep_b8478abd6c59a5d2bd5e1dca` | 127.0.0.1:7497 / client 82 | DU***665 |
| Live Cash | `ep_9ec324c69a0a7cc945755d09` | 127.0.0.1:7496 / client 81 | U1***370 |

隔离库最终 active 为 Paper。**生产 Runtime 库未激活，未启动长期交易进程。**

## 3. 启停与状态

```powershell
& $py -m doxagent.trade_execution.cli --db $db run-worker
# 只推进一次调度轮，不能代替持续执行：
& $py -m doxagent.trade_execution.cli --db $db run-worker --once
& $py -m doxagent.trade_execution.cli --db $db inspect --execution '<trade:intent-id>'
```

生产需同时运行既有 Runtime coordinator（负责正式输出/intake）与独立 Executor（负责 broker）。不需要 W 节点再次批准。Candidate 未正式释放不进入 Executor；Policy/W3/周末最终 winner 共用正式输出入口。

每个账户/股票小步运行，失败有限重试并记录 gap，其余交易和 Message Bus/O2/O3 不等待该笔完成。单 DB 的 OS writer lock 随进程退出释放；不会因 lease 过期让两个活进程同时发单。Ctrl+C 后在途委托可能仍在 broker，有持久账本负责下次恢复，不能把进程退出当撤单成功。启动同库 worker 会先对账再继续。技术连接失败可能使某 entry 最终失败；未知提交继续低频对账，绝不盲重发。

`inspect` 展示 entry/exit job、attempt 参数、报价、broker 状态、有效 fills、金额、滑点、首末成交耗时、撤单确认耗时、份额。原始费用回调保存在 `runtime_values/execution_fees`，原始 broker 事件在 `te_events`。`status` 展示持仓、队列、验收和缺口；DONE 表示任务终态，**不等于成交**，须看 `entry_result` / Exit result。

## 4. 故障恢复和人工账本修复

下面涉及回放/修正的命令需停止 worker，以同一个 OS lock 排除并发写；profile 激活不需要停机。保留原始证据和 reason。

```powershell
& $py -m doxagent.trade_execution.cli --db $db reconcile --execution '<trade:id>'
& $py -m doxagent.trade_execution.cli --db $db resume-exit --lot '<trade:id>' --reason 'quote permission restored'
& $py -m doxagent.trade_execution.cli --db $db import-fills --file 'statement-normalized.json' --reason 'broker history window exceeded'
& $py -m doxagent.trade_execution.cli --db $db retry-event --event 123 --reason 'calendar/ledger dependency corrected'
```

`reconcile` 只查询并导入回报；不发交易。`resume-exit` 仅对 FAILED 且有归属残余的退出开新一轮预算，不能用于 entry 重放。UNKNOWN/CANCEL_PENDING 必须先看到明确 broker 状态；净仓为零不是“从未提交”的证明。TWS 历史窗口有限，跨日长停机可能需要人工导入 execution statement；没有证据时保留 `BROKER_HISTORY_GAP`，不能声称完整成本已恢复。

`import-fills` 输入为 JSON 数组，每项至少包含 `account, exec_id, client_id, order_id, order_ref, con_id, quantity, price, side(BUY/SELL), time(带时区ISO)`；可含 `perm_id,cum_qty`。必须是 broker 真实 execution ID/成交时间/订单归属；按 account+execId 去重，IB 修订后缀替换有效版本。此入口接收规范化记录，不自动解析所有 IB 报表格式。未知外部订单不会被认作 DoxAgent。`retry-event` 成功后清除对应 event gap；历史 job gap 保留审计。

人工基础持仓变动导致 `POSITION_ATTRIBUTION_GAP` 时，确认实际外部份额后：

```powershell
& $py -m doxagent.trade_execution.cli --db $db rebase-position --profile '<ep_revision>' --con-id 9939 --external-quantity 50 --reason 'verified manual position'
# 例如拆股，单独修正已核实的 DoxAgent 归属份额，不伪造成交：
& $py -m doxagent.trade_execution.cli --db $db adjust-lot --lot '<trade:id>' --adjustment-id '<unique-action-id>' --quantity-delta 198 --effective-at '2026-09-08T15:00:00+00:00' --reason 'verified split allocation'
```

`adjust-lot` 只改变该笔应退出整数份额，保留追加调整记录；不改 broker 原始成本。不能提前应用未来调整、使份额为负或在未解决委托中调整。若公司行动同时改变人工持仓，外部 baseline 也要按真实证据修正。分数股、现金替代和复杂公司行动仍作为明确缺口人工处理。

## 5. 日历修正与 Gateway 迁移

```powershell
& $py -m doxagent.trade_execution.cli --db $db set-session --date '2026-11-27' --open-at '2026-11-27T09:30:00-05:00' --close-at '2026-11-27T13:00:00-05:00' --reason 'verified exchange schedule'
& $py -m doxagent.trade_execution.cli --db $db replan-exit --lot '<trade:id>' --reason 'calendar correction'
& $py -m doxagent.trade_execution.cli --db $db set-endpoint --profile '<ep_revision>' --host '127.0.0.1' --port 4002 --reason 'move Paper TWS to Gateway'
```

日历覆盖共享既有 MarketCalendar；`--closed` 可指定休市。变更后不会静默重排所有已登记退出，显式 `replan-exit` 仅允许尚未开始且原计划在未来的任务；新算出的时刻已到则立即到期。已逾期退出保持待偿义务，恢复后使用当前实际交易时段执行。

Gateway 与 TWS 使用同一 socket API。迁移步骤：停止旧 writer → SQLite backup → 搬迁数据库/配置与依赖 → Gateway 登录/2FA → `set-endpoint` 对同账户、原 clientId 握手并记录迁移 → 对账 → 启动 worker。端口通常 Live4001/Paper4002，可覆盖。不要同时在不同主机用数据库副本运行两个 writer；本地 OS lock 无法跨两个独立副本互斥。Gateway 认证仍需账户持有人完成，程序不保存密码。

## 6. 真实验收与待开市项目

```powershell
& $py -m doxagent.trade_execution.cli --db $db probe --profile '<ep_revision>' --symbol MU --output 'probe.json'
# Paper 参数诊断，what-if 不等于可成交验收：
& $py -m doxagent.trade_execution.cli --db $db probe --profile 'ep_b8478abd6c59a5d2bd5e1dca' --symbol MU --what-if
```

已持久化四个**未 arm**的单次 suite：`mu-rth-long-20260906`、`mu-rth-short-20260906`、`mu-extended-long-20260906`、`mu-overnight-long-20260906`。修复行情授权后，每次只 arm 一个，观察该笔实际退出再启下一项，避免反向验收彼此 FIFO 抵销：

```powershell
& $py -m doxagent.trade_execution.cli --db $db arm-suite --suite 'mu-rth-long-20260906'
& $py -m doxagent.trade_execution.cli --db $db run-worker
```

Arm 后会按 pinned Paper 生产策略目标 $20,000 下单，有真实模拟仓位和退出义务；条件满足只执行一次，重启不会每日新建仓。suite 在 `WAIT_MARKET / WAIT_MARKET_DATA / RUNNING` 之间推进；完整 entry 且实际 Exit 有成交才 PASS；只有 FIFO 抵销、不完整成交或退出失败为 PARTIAL。观察 retry 自然发生，不能更改策略故意强迫回退后称生产验收通过。

当前实时 quote 未通过：Paper 有2186且无新鲜报价，Live 有10089权限错误；请在 Client Portal 确认 API 实时 L1 bid/ask 订阅及 Paper 共享，再跑 probe。Paper 保持7497非只读和股票/卖空权限、足够模拟资金。Live 继续只读，不需要现在关闭。2026-09-07美国 Labor Day休市，下一 RTH 为09-08 09:30ET（北京时间21:30），正常退出为当日15:30ET（北京时间09-09 03:30）。实际 Overnight 可交易窗口以返回的合约时段为准。

本次没有注册定时自动下单、未启动后台常驻 worker。待开市用例已入本地账本，需按上述命令启动；不把“已入队”等同“正在后台运行”。
