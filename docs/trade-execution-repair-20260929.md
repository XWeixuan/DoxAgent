# 交易执行共享 Equity 修复：实施与生产验收

日期：2026-09-29。依据：[实施方案](../dev_plan/workflow_v2/trade_execution_shared_equity_repair_plan_20260929.md)和[两周审计](trade-execution-audit-20260928.md)。代码提交 `1d6234f5`、`427ad43d`。

## 已实施

- 新交易 Profile 取消固定 `$20,000`，使用交易周期的 USD NetLiquidation；账户 Gross 上限 `1.5E`、单 ticker `1.0E`、最低有效新仓 `0.25E`，整数股取整后二次检查。全部券商持仓和未结 Entry 预占参与额度计算，SQLite 将 quota 和待发送 attempt 原子写入；部分成交、撤单、迟到成交和重启继续按现有 fill/lot 账本对账。反向 Entry 先按已归属 FIFO lot 抵销，只有净新仓占额度。
- 完整数量的 IBKR WhatIf 及 `AvailableFunds`、`LookAheadAvailableFunds` 均保留 `0.20E` 的检查在真实提交前执行；策略额度不足与保证金不足分别给出 `PORTFOLIO_CAPACITY_EXHAUSTED`、`INSUFFICIENT_MARGIN`。RTH 初单/Entry 重报价限价采用 ±0.5%，RTH Exit 的第二单可用市价；非 RTH 第二、三单采用 ±1%。
- 保存 201 拒单根因及 10329 关联证据，拒单不再因随后 Cancelled 而盲目加价重试；报价缺口在 Intent 原到期前等待并留诊断，连接重建有退避。对能确定同一 W3 事件事实状态的 Case 只释放一份正式交易意图；FIFO_OFFSET 的已实现损益与迟到费用纳入 Read 投影。
- 常驻 Executor 在旧仓、未结订单完成后主动同步券商并记录下一周期 E，不等下一笔 Entry 才落盘。旧 Profile/旧 Intent 的 revision pin、lot 和 Exit 保持历史语义。

## 新加坡 PAPER 现场

| 项目 | 结果 |
|---|---|
| 发布 | 仅重建 Executor、Scheduler、Read projector。Executor 使用 `doxagent-v2:trade-427ad43d`；另两个服务使用同业务修复的 `trade-1d6234f5`。其余服务未切换。 |
| SQLite | `te_meta` 从 1→2，新增 `te_account_cycles`；31 个历史 attempt 在迁移前后均为已结清，无未结 Entry。 |
| Profile 绑定 | 原 `ep_e1d7c65e0656af2e6c4923b1` 冻结保留；`*` 默认和 `MU` 专属 PAPER_TRADING 绑定已通过 ControlRepository CAS 切至共享 Profile `ep_a5dfda256b2f7863fc85a605`。生产原本没有 `trade_execution/active` 指针，因此未新增该旁路。 |
| 券商同步 | 新镜像用独立只读客户端实测 `AccountReady=true`，USD NLV、AvailableFunds、LookAheadAvailableFunds 及完整组合估值均可取得。检查时仅有 MU 18 股旧仓，Gross 约 $19,270.98，旧 Exit 到期 `2026-09-29 19:30 UTC`；尚不应生成新周期 E。 |
| Gateway | 官方配置 API 对 `configuration.api.precautions` 返回“read-only”；在 Gateway 图形配置中仅打开 `Bypass Redirect Order warning for Stock API Orders`，随后 API 重新读取为 `true`。原来已启用的全局 `Bypass Order Precautions for API Orders` 仍为 `true`，本次未改动。 |
| 备份 | `/data/backups/trade-execution-20260929T122500Z/` 保存 Runtime、Read SQLite 和 SHA-256 manifest。Runtime 副本 `quick_check=ok`；Read 副本可独立打开且 SHA-256 已记录，完整 `quick_check` 因长时间扫描未完成。Gateway 修改前原 `ibg.xml` 备份在 `/home/ubuntu/doxagent-backups/trade-execution-20260929/`。 |
| 运行 | 三个目标容器持续 `Up`，Executor 心跳及券商连接正常；没有自动重放历史失败意图或发送验收订单。 |

## 验证边界与后续观察

本地交易/Read 定向测试 41 passed、2 skipped（本机未安装官方 `ibapi`）；Runtime/Read 联合测试 64 passed；补修周期快照后交易链 36 passed。另有一项与本轮无关的既有 Runtime 测试 `test_closed_candidates_do_not_consume_policy_and_w1_replays_receipts` 失败：预期增加一条模型 turn，实际增加两条；发生在 TradeOutput 写意图之前，未用修改交易代码掩盖。

待今日 15:30 ET 的旧 MU Exit 确认成交并对账后，检查 `te_account_cycles` 是否立刻记录新 E，且与当时券商 NLV 一致；之后才可在真实 PAPER 时段验证新 Entry→Exit 的完整成交、预占与保证金判定。夜盘 10329 的配置已修，但尚无新的夜盘真实订单回报，不能宣称该 badcase 已被交易实证关闭。Gateway 重启后的设置持久性也尚未验证；当前仅确认运行中配置读回为 `true`。生产未启用 LIVE Profile。

原 Gateway 全局 precaution bypass 已为 `true`，比本次所需的直连路由单项开关更宽。这是现场既存配置风险，若要缩窄必须单独评估 Gateway 其他 API 订单的交互提示及对现有自动化的影响。
