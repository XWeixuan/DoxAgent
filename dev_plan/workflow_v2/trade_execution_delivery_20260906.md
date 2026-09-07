# Trade Executor 开发交付记录

日期：2026-09-06。按 `trade_execution_implementation_plan_20260906.md` 完成 E0–E6 的代码、确定性测试、周末可进行的连接/协议验收及操作文档。开市成交验收、实时行情授权和真实 Live 交易能力未标记通过。当前没有生产交易进程，也没有开启 Live 下单。

## 1. 交付内容

| 范围 | 实现与结果 |
|---|---|
| E0 配置/策略 | 不可变 Paper/Live profile、固定账户保险丝、方向与金额配置；Decimal 整数股与有效 tick；初始±1%，RTH 一次 MKT、非 RTH 两次新报价±2% LMT |
| E1 TWS API | 独立官方 ibapi 长连接；连接/账户/合约/实际 venue hours/marketRule/实时 quote；订单、成交、费用回调；精确账户与 OS writer 校验；兼容 Gateway endpoint |
| E2 账本 | 同 Runtime SQLite 的索引 `te_*` 表；业务 intent 幂等；持久 SUBMITTING/委托身份；fill 去重与修订；FIFO 归属、非零份额与 Exit 原子落库 |
| E3 状态机 | Entry/Exit 共用确定性价格与重试原语；先撤单、终态后再次同步成交才重试；部分成交保留；跨日 entry 失效但既有 Exit 继续；未知提交不盲重发 |
| E4 Runtime | 正式输出事务 pin 当前 profile；Policy、W3、最终释放 winner 共用 intake；未释放 Candidate 不发单；旧 output-only 无追溯下单；独立 worker 不阻塞节点维护 |
| E5 运维 | 迁移备份、profile 导入/热切换、账户发现、probe/status/inspect、reconcile、失败 Exit 手工续轮、statement 规范化导入、event 重放、归属份额/外部 baseline/日历修正及 Gateway 迁移 |
| E6 验收 | 47 项离线回归、官方适配器映射测试、进程崩溃恢复；真实双 TWS 探测、Paper 本地暂存撤单、隔离配置切换；4 个持久化待开市 suite |

源码集中于 `src/doxagent/trade_execution/`（14 个模块）。会话内包含 contracts/quotes，仓库内包含 reconciliation/lot projections，因此没有机械拆分方案列举的每个建议文件。既有 Runtime 主要接线为 `trade_output.py`、`coordinator.py`、`calendar.py`。交易指针采用 **`trade_execution/active`**，避免覆盖既有 prompt 的 execution bundle 命名空间。

退出只管理每笔 DoxAgent 实际归属份额；反向实际成交按 FIFO 抵销旧份额，超额才建立新方向 lot。人工持仓 baseline 不参与退出。$20,000 为目标预算；MKT 的最终价格不能预知，不宣称严格金额上限。无 AI 交易复核，无模型调用，无 Data MCP 下单工具。

## 2. 离线验证

最终同一次校验：**47 passed，3 个第三方依赖警告，34.21 秒**。其中本次新增交易测试24项，既有 Runtime/IBKR 底层回归23项。无真实模型验收。

- 金额/梯度/完整重试矩阵、RTH/非RTH预算、部分成交、撤单时再成交、最后一次尝试撤单。
- 人工50股不被退出、按笔退出、反向 FIFO、重复与修订 fill、拆股份额调整幂等回放。
- 崩溃注入：broker 回调已落盘但 submit 尚未返回，子进程 `os._exit(23)`；重启恢复原提交身份，不重复首单。
- 未知提交不重发、缺行情隔离、跨日取消 entry 残余与保留 Exit、失败 Exit 手动新一轮恢复。
- profile 原子 pin、Live Cash SHORT 禁用、账户保险丝、OS 单 writer、已有活动 entry 不被到期 Exit 饿死。
- Calendar 休市/提前收市/cutoff、受控重排与逾期禁止延期、suite 入账后崩溃不重复生成交易。
- 官方 ibapi 参数映射、OrderCancel、拒单回调持久化、日期格式；迁移 dry-run 不写库。

Ruff、严格目标 mypy（14源文件）、compileall、CLI help 均通过。源文件校验前后 SHA-256 一致。证据：

- `eval/trade_execution/20260906/offline_verification.json`
- `eval/trade_execution/20260906/pytest.txt`
- 同目录 `ruff.txt`、`mypy.txt`、`compile.txt`、`cli_help.txt`

这些验证证明实现和恢复路径，不等于市场撮合、卖空许可或真实行情已通过。

## 3. 周末真实验收

全部使用 `.tmp/trade_executor_acceptance_20260906.db` 隔离库；真实账户信息仅存本地，分享证据脱敏。API版本10.49.2，TWS server225。

| 验收项 | 结果 | 证据与边界 |
|---|---|---|
| Paper7497握手与账户保险丝 | PASS | `paper_weekend_probe_fixed.json` |
| Paper open/completed/executions/positions同步 | PASS | 所需结束回调均取得；不等于存在成交 |
| Live7496只读握手与positions | PASS | `live_readonly_probe_final.json`；不请求受只读设置禁止的订单同步 |
| MU合约、marketRule、Overnight合约 | PASS | conId9939，返回的规则包含0.01梯度；两环境实际交易时段可读 |
| 实时新鲜bid/ask | 未通过 | Paper2186且无fresh quote；Live10089订阅错误；没有降级使用历史/延迟价格下策略订单 |
| Paper what-if | UNAVAILABLE | 首轮缺DAY产生10052，已修复；修复后请求超时。原始失败证据保留，未伪装PASS |
| Paper本地暂存/撤单 | PASS（协议） | `paper_staged_cancel.json`：1股/$1诊断单，`transmit=false`，TWS返回Cancelled；未送broker、无成交，初始staged_status未收到 |
| Paper→Live→Paper配置切换 | PASS（控制） | `environment_switch.json`三次真实账户握手成功，只修改隔离配置，不提交订单 |

原始第一轮 `paper_weekend_probe.json`、`live_readonly_probe.json` 保留以解释修复。Live Read-Only 返回321说明其能力边界，随后改用真实可用的positions探测，没有为测试关闭只读。

实际 Overnight 合约在节假日09-07返回18:00–次日03:50窗口，已据此使执行器检测另一可用 venue，而不是硬编码20:00才能交易。订单是否实际可成交仍留开市验证。

## 4. 待开市验收与必要准备

四个 suite 已存 `te_acceptance_runs` 并导出 `pending_market_suites.json`：

| Suite | 等待内容 |
|---|---|
| `mu-rth-long-20260906` | RTH LONG实际entry→scheduled Exit |
| `mu-rth-short-20260906` | RTH SHORT许可、成交与BUY退出 |
| `mu-extended-long-20260906` | 非RTH真实LMT/撤单，有限retry自然触发时观察 |
| `mu-overnight-long-20260906` | 实际venue、权限、实时报价及Paper撮合支持 |

均未 arm、未启动后台 worker。它们不是Codex定时任务。按操作手册逐项启动后只执行一次，按生产$20,000策略建模拟仓并管理退出。LMT→MKT、cancel race、断线及提前收市的真实观察，只有自然发生且证据充分才补验收，当前相应分支为离线验证。

需要用户准备：API实时L1行情及Paper共享授权；Paper保持非只读、有股票/卖空权限与足够模拟资金。Live无需现在解除只读。下一常规RTH是2026-09-08 09:30ET/北京时间21:30；09-07为Labor Day休市。完整成交测试不能用周末暂存单替代。

## 5. 运维与恢复边界

详见 **`trade_execution_operations.md`**，含可直接执行的PowerShell命令及双环境JSON模板。

实现支持同日停机续跑、跨日旧订单对账/退出、有限失败隔离与明确gap。超出TWS可查询历史的长停机不可能仅凭净仓自动还原每笔成本，需真实statement规范化导入；未知提交不更换orderId盲重发。复杂公司行动保留人工修正证据，不伪造fills。

本机与Gateway迁移复用账本、账户及clientId；必须停旧writer、备份搬迁后恢复，不能在不同主机用数据库副本并发写同账户。本地锁不冒充跨主机分布式fence。官方ibapi来自本地TWS API发行包，远端需要同样安装，Gateway登录/2FA由账户持有人处理。

生产环境配置尚未激活，生产持久进程尚未启动；既有未配置行为仍为Runtime输出。用户验收通过后的环境切换属于独立运维动作，Paper结果不会自动打开Live。
