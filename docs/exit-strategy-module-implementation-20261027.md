# 退出策略模块开发与验证记录

开发开始：2026-10-27；收尾验证：2026-10-28，北京时间。依据[开发方案](../dev_plan/workflow_v2/exit_strategy_module_development_plan_20261027.md)实施。

**状态：开发实现完成，未部署生产、未修改生产策略绑定、未发送券商订单。**当前实现从真实成交接管新交易，现有持仓不会被无依据地重置MFE后接管。生产真实行情联通和PAPER真实订单闭环仍属于后续部署验收，不能用本轮隔离检查冒充已上线。

## 已实现

- 新增独立 `src/doxagent/exit_strategy/`，由既有Executor进程托管；复用Runtime SQLite、IBKR会话及原来的 `lot_id:exit` 任务，没有新增常驻容器、行情库、消息队列或模型调用。
- 默认策略：净MFE达到股票实际入场金额的5%，且可锁定预计成本后的正收益，才激活 `max(50% MFE, 2.5 ATR20)`；止盈线不放宽。每5秒观察，激活后至少30秒、窗口六次均有效、至少五次越线且最新越线才请求退出。
- LONG用bid、SHORT用ask估值；成交现金流、FIFO已实现收益、剩余持仓、实际/估计佣金和持有成本共同计算净清算收益。部分成交不重置MFE；ATR按当前实际数量计算联合价格波动，不把会计现金流变化当作行情振幅。
- 持久化MFE、止盈线、激活状态、ATR恢复数据与钉住的策略版本；重启不丢高水位，但重新收集确认窗口。原子写入状态与退出义务，并用状态版本校验抵御成交/控制操作竞争。单个持仓配置或任务异常不阻塞其他持仓。
- 共享持续BBO订阅，支持价格和数量回调、真实接收时间、连接/订阅代次、引用计数与行情丢失后重订。数量变化可以刷新报价有效性，不能用本地读取、账户心跳或旧价格伪造新鲜度；同侧价格/数量完成后才作为完整观察使用。
- 新增有界后台ATR预热和I/O；稳定持仓的ATR按分钟缓存。历史请求、旧订阅释放和数据库等待不占用行情计算路径。没有历史种子时明确预热，不用ATR=0抢先激活。
- 退出请求会停止尚未完成的开仓、撤销残量并最终对账，发送前再次阻止同合约新ENTRY。重复触发不重复建EXIT任务；发送结果未知时先对账，普通未成交尝试耗尽后仅在所有旧attempt已结束且冷却后处理剩余量，确定拒单不盲目重发。
- 提前止盈失败可以在原定截止之前恢复。全平后迟到成交会重新形成退出义务，不把旧信号当成新交易继续持有。美东截止、半日市、夏令时和已有ENTRY三次失败放弃规则保留。
- 显式多腿管理接口、联合ATR及整组退出已实现；一腿发生非计划FIFO减少或归零时请求关闭整个组的剩余量。没有自动配ETF、没有Beta重估、没有对冲ENTRY生成；当前执行侧尚未提供真实成组开仓，因此**不能称为真实两腿对冲生产上线**。
- 交易详情增加跟踪/激活/退出已触发/已平仓、行情健康、净浮盈/MFE、ATR/止盈线、确认窗口和剩余量。只经现有执行摘要做低频投影；`ts_positions` 不注册到高频CDC。

关键文件：

| 职责 | 文件 |
| --- | --- |
| 规则、价格质量与联合ATR | [engine.py](../src/doxagent/exit_strategy/engine.py)、[market.py](../src/doxagent/exit_strategy/market.py)、[schema.py](../src/doxagent/exit_strategy/schema.py) |
| 状态、版本、成组接口与原子退出 | [repository.py](../src/doxagent/exit_strategy/repository.py) |
| 5秒调度、预热、日志与恢复 | [runner.py](../src/doxagent/exit_strategy/runner.py) |
| 导入、绑定、状态与暂停 | [cli.py](../src/doxagent/exit_strategy/cli.py) |
| 原有执行器接点 | `trade_execution/repository.py / executor.py / worker.py / ibkr_session.py` |
| 契约及读侧 | `api_contract/doxagent-v2-api.types.ts`、`api_v2/wire_schema.json`、`v2_read/executions.py`、前端既有交易详情 |

## 验证结果

证据目录：[eval/exit_strategy_development_20261027](../eval/exit_strategy_development_20261027/)。测试和隔离压力重放均不发送真实/PAPER券商订单。

- **96项**选定的Python交易、退出、账户恢复、资金模型和执行投影回归通过，见 `tests-final.xml`。覆盖因果前缀、5%与正收益门槛、尖峰过滤、单向线、30秒窗口、重启/漏格、联合ATR、真实账本接管、原子回滚、版本竞争、提前退出恢复、ENTRY撤单、FIFO、费用回报、迟到成交和故障隔离。
- 前端TypeScript检查通过；相关单元测试 **12项通过**；1262px与1559px的浏览器检查 **2项通过**，实际查看了截图。截图为明确的合成状态夹具，不是生产截图。
- 远端现有生产SDK容器中，用临时目录加载候选代码并禁用网络连接，完成官方SDK回调、共享订阅、size-only刷新、下单报价复用新鲜数量更新、行情丢失清缓存与最后引用释放检查。`orders_submitted=0`；没有覆盖运行中的源码、主Runtime账本或绑定。
- `manifest.json`记录本轮相关文件哈希；`benchmark-sources.json`记录远端实际加载的规范化源码哈希。测试时SDK适配器和Runner与本地对应源码一致；压测使用临时账本和合成持仓，不是生产长时间实盘负载验收。

资源测量见 `benchmark.jsonl`：20组持仓、40个合约，40轮加速重放，包含SQLite和压缩采样日志；另对SDK直接回调执行16,000次调用。**资源数值只代表该隔离样本**，网络解析、市场行情特征及长期驻留仍需发布后观察。

| 指标 | 实测 |
| --- | ---: |
| 完整5秒轮次P95（含状态读取、持久化、日志） | 133.06ms |
| 纯计算P95 | 38.57ms |
| 状态持久化P95 | 68.84ms |
| 按5秒节奏折算的CPU核占用 | 0.0109核 |
| 直接SDK回调640次/秒的CPU折算 | 0.0013核 |
| 峰值RSS增量 | 8.30MiB |
| 样本私有数据库 | 680KiB |


## 配置和启用方式

默认不替任何账户开启策略。先发布兼容代码，再导入并绑定不可变策略；绑定只影响之后新接纳的execution，真实成交后才建立管理状态。

生产发布必须覆盖：Executor；运行 `ExecutionIntake.admit` 的Delivery及Coordinator宿主；执行投影、API契约和前端。**只更新Executor、不更新接纳方，会导致新交易没有钉住exit policy。**发布兼容组件后再启用绑定，避免混合版本期间静默漏接管。

以下命令为部署后在正确Runtime数据库上的操作说明，本轮未执行绑定。账户和revision请使用实际配置，不能照抄占位符：

```bash
# 默认导入50% MFE + 2.5 ATR；返回xp_开头的revision。
python -m doxagent.exit_strategy.cli --db /data/runtime/runtime.sqlite3 import-policy

# PAPER例；LIVE必须明确使用LIVE环境和对应账户，不以端口猜账户身份。
python -m doxagent.exit_strategy.cli --db /data/runtime/runtime.sqlite3 bind \
  --account <actual-paper-account> --environment PAPER --revision <xp_revision> --reason 'enable trailing exits'

python -m doxagent.exit_strategy.cli --db /data/runtime/runtime.sqlite3 status
python -m doxagent.exit_strategy.cli --db /data/runtime/runtime.sqlite3 status --position <execution_or_group_id>

# 暂停未来止盈观察，保留截止和已请求的退出义务；--resume恢复并重建确认窗口。
python -m doxagent.exit_strategy.cli --db /data/runtime/runtime.sqlite3 pause \
  --position <execution_or_group_id> --reason 'maintenance'

# 不传--revision：仅关闭新交易绑定；已有仓位仍按钉住策略管理。
python -m doxagent.exit_strategy.cli --db /data/runtime/runtime.sqlite3 bind \
  --account <actual-paper-account> --environment PAPER --reason 'disable new admissions'
```

`status`直接只读查询，不为查看状态构造执行器或发券商指令。需要自定义明确参数时 `import-policy --file <policy.json>`；历史ExecutionProfile内容hash保持不变。多腿只接受执行侧明确的真实lot映射，不通过ticker名称推断或占用手工仓位。

## 运行边界与剩余验收

1. 本轮没有生产部署、没有账户启用、没有真实PAPER订单闭环。代码回归中的订单/成交来自MockBroker；远端SDK和资源检查均禁网。下一步应在候选镜像完成真实行情和PAPER执行闭环，再按目标环境启用，不宣称当前已经保护生产仓位。
2. 这是应用侧止盈；主机停机/券商断线期间不能保证按止盈价成交。已提交退出义务会持续恢复，确定权限/身份/拒单错误会保留未平数量和错误，不无限下单。
3. 发生不可可靠恢复的成交依据变化、拆股/归属调整时，标记 `STATE_GAP`、停止该持仓的止盈观察并保留原截止退出；没有做一个庞大的历史重建子系统，也不会猜测归零MFE来恢复。
4. 多腿内核和退出适配通过合成/账本测试；真实对冲ENTRY、成组归属及其券商闭环仍是执行侧前置条件。两腿共享退出义务，不保证券商原子成交；残量继续恢复。
5. 采样压缩日志仅7天、总上限512MiB；退出决定含最后六次完整观察和参数依据，订单/成交走原持久审计。调试数据不是永久逐tick历史库。

本轮未把工作区其他消息管线修改打包或部署；重要代码变更已追加到 `changelog`。
