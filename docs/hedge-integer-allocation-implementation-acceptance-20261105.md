# 对冲整数配仓修复、发布与验收报告

日期：2026-11-05，北京时间。已完成开发、远端发布与新的 PAPER 完整开平仓验收。

代码提交：`022d918c64e15ed73d050bf9ad93d3d8d5b0df20`，已推送 `codex/agent-readonly-mcp`。
依据：[优化方案](hedge-integer-allocation-optimization-plan-20261105.md)。未重放旧 INTC，未提交 LIVE 测试委托。

## 1. 实际修复

- 新接收 execution 冻结 `allocation_policy_version=2`；无此字段的既有组继续使用原固定分腿额度、相对容差语义，不批量迁移。
- 新组统一固定 **±10 个百分点**，即 `max(0,r−0.10)` 至 `r+0.10`；保留主仓上限 `B/(1+r)`，取消独立对冲硬上限。
- 初始正整数搜索从最大主仓股数逐股下降，动态使用 `B−M`；同主仓数量选择最接近目标的对冲股数，平局选较少股数。不复用旧跳跃公式。
- MAIN 终结并对账后，以自身有效成交账本重算 MAIN 名义金额和已成交 HEDGE 金额。HEDGE 只用原 `B−M−S` 余额；正数对冲已经进入范围即完成，超上限不追加，真实无解则保留主仓和退出责任。
- 新组占用为 `G+max(0,M_actual+H_actual−B)`，合法使用主仓剩余预算的对冲不会产生额外分腿占用。G/F/B 在首次成功授权时冻结。
- 最近规划摘要落在既有 job/execution payload；创建 attempt 前失败也保存金额、价格、来源/时间、比例范围、候选边界及子原因。不保存逐 tick 历史。
- 现有 case 详情新增“配仓诊断”，展示目标、范围、实际/预计比例、名义预算或失败解释；保留原机器原因码，不增加页面、模式开关或后台任务。

未改变账户/ticker 配置、LIVE 策略本金、W1/W2/W3、过期与禁交易日、盘口准入、LMT 定价、ENTRY 三次机会、撤单对账、持仓归属或原退出规则。

实现位置：

- [planning.py](../src/doxagent/trade_execution/planning.py)：统一数学约束及新旧策略读取。
- [repository.py](../src/doxagent/trade_execution/repository.py)：事务内冻结预算、剩余额度及诊断。
- [groups.py](../src/doxagent/trade_execution/groups.py)：版本冻结与整组实际占用。
- [executor.py](../src/doxagent/trade_execution/executor.py)：对冲终态成交对账后按实际范围决定完成或补差。
- [executions.py](../src/doxagent/v2_read/executions.py)、[case 详情](../frontend/v2/src/pages/runtime-case.tsx)：既有读取/展示链路。

## 2. 必要回归与 review

| 验证 | 结果 |
|---|---|
| 相关后端全组 | **148 passed，3 skipped**，114.87 秒 |
| 使用生产部署的官方 IBKR SDK 补测上述 adapter 用例 | **3 passed，0 skipped**；没有安装或更换本地依赖 |
| 新增独立穷举 | 450 组预算/价格/目标输入；直接枚举、按金额过滤和排序，与实现一致 |
| 前端类型检查 | 通过 |
| case 前端单测 | 5 passed |
| Playwright 两种桌面宽度 | 1262px、1559px，共 2 passed；无横向溢出 |
| Ruff 与提交差异检查 | 通过 |

原 3 项 skipped 来自本地旧 SDK 缺少 `ibapi.order_cancel`，不是主动跳过失败；已从运行镜像只读提取同一 SDK 到隔离目录后完成补测。结合两轮结果，本轮所选后端 **151 个用例均已执行通过**。

覆盖 INTC 离线 5+2、高价主仓 1+3、成交后两美分边界、动态余额反例 9+2；低目标和超过100%目标、精确比例/预算边界、部分成交补差、已达比例停止、实际成交价格改善、迟到成交更正、双 CASH 名额及费用占用、旧版本、终态重复投递与重启、UNKNOWN/撤单/恢复、EXIT 归属等。

原恢复测试中的 3 个失败在未修改基线上也可复现：它们注入同步断链但仍保持流式观察通道健康，无法触发预期故障路径。仅修正测试为连接不健康，生产重试/恢复规则未为此扩改。

CASH 第二名额继续受原费用扣款与余额保留约束；本轮没有借修复取消现有资金检查。PAPER 实测是 MARGIN，CASH 路径由确定性账户/资金回归验证，未做 LIVE 下单验收。

## 3. 远端发布

按运行组件原镜像派生覆盖，未用本地脏工作区重建全部后端。源码提交、镜像及源文件哈希已核对，原有服务环境和启动命令逐项比较通过。

| 服务 | 运行镜像短 ID | 源码/配置校验 |
|---|---|---|
| v2-executor | `d804c69be470` | 通过 |
| v2-api | `83be9081c20e` | 通过 |
| v2-projector | `096c2da80066` | 通过 |
| v2-web | `15ce110b6e21` | 通过 |
| v2-delivery | `f7e1d50e32cc` | 通过 |

Delivery 负责接收意图并创建交易组，因此其规划、repository、groups、executor 同步更新；只更新订单执行进程会让新组继续缺少版本字段。Scheduler 已使用外置 Delivery，本轮没有重启 Scheduler、Worker、控制服务或新闻采集服务。

API、Web 均 healthy，其他三个进程正常运行。共享可选展示字段在各组件自己的原 wire schema 上增量加入，保留各镜像既有兼容定义。

服务端清单、构建日志、前序配置与回滚信息保存在 `/var/lib/doxagent/releases/hedge-integer-022d918c/`，其中配置备份为私有文件，没有导出密钥。

发布过程有一次校验中断：首轮哈希未归一化 CRLF，且清单未完整携带服务覆盖项；已中止，修正校验并完整保留原 environment/command/entrypoint 后重新发布。API 经短暂重建恢复健康；执行器与 Delivery 在首轮仍是原版本。Delivery 原运行镜像已无可用镜像记录，替换前对其运行文件系统制作了本机回滚镜像；未推送容器镜像到外部仓库。最终五组件镜像、源码哈希、原环境和命令全部通过。

## 4. 新 PAPER 任务完整验收

独立新 ID：`acceptance:hedge-integer-v2-20261105-001`。通过新 Delivery 代码的 `ExecutionIntake.submit()` 持久接收，由正式单写 Executor 执行。没有重新激活或修改任何 ticker 的 LIVE/PAPER 配置，也没有重新分配账户本金。

该测试没有业务 Case provenance，沿用既有 acceptance 隔离方式，不伪造历史 Case 或 W3 决策。前端业务详情展示由相关读取回归、类型检查及双宽浏览器 fixture 验证，不将此测试冒充自然业务 case 的 UI 验收。

| 项目 | 实际结果 |
|---|---|
| 账户 | PAPER / MARGIN，配置版本 `ep_7bf502b633753cde64a06b7a` |
| 新接收策略版本 | 2 |
| G / F / B | $3987.519931 / $3.987519931 / $3983.532411069 |
| 主仓 | BUY MU 3 股，实际 $782.40/股，名义 $2347.20 |
| 对冲 | SELL SOXX 2 股，实际 $446.44/股，名义 $892.88 |
| 目标与实际 | 目标29%，允许19%–39%；实际38.040218% |
| 整组开仓名义 | $3240.08 ≤ B；G 没有被分腿重复扩大 |
| 对冲重试 | 首单确认取消、零成交；重新报价，第二单成交 |
| 主仓退出 | 北京时间08:27:10，SELL MU 3 股，$781.60/股 |
| 对冲退出 | 北京时间08:27:17，BUY SOXX 2 股，$447.15/股 |
| 终态 | 两 ENTRY、两 EXIT 都 DONE，组 CLOSED，两腿 owned remaining_qty=0 |
| 委托/成交 | 5 张委托，均 LMT、均 SETTLED；4 笔实际成交 |
| 费用与测试损益 | 已回传 USD 费用 $4.066726；毛损益 −$3.82，费用后 −$7.886726 |

券商独立只读连接在北京时间 **08:28:25** 回查 PAPER：MU=0、SOXX=0；该回查调用未提交任何订单。测试仓位已全部退出，不留下等待将来平仓的责任。费用保留 F 是估算，实际佣金仍按回传账本入账，本次不新增费用预测模型。

## 5. 旧 INTC 不复活的证据与机制

Case：`case_2e138775e1fd4e159988a9682551f3aa`。

发布前、发布期间、发布后分别只读核对；截至北京时间 **08:29:21**：

- execution 仍为 **FAILED / NO_VALID_INTEGER_COMBINATION / CLOSED**。
- MAIN ENTRY 与 HEDGE ENTRY 仍为 **DONE / FAILED**，后者原因 MAIN_NOT_FILLED；generation、失败次数均未增加。
- **attempt=0、fill=0、lot=0**；未增加策略版本字段，未回写旧规划或重建报价。
- 原 delivery task 仍 SUCCEEDED，generation=1、failures=0，没有重新排队。
- 所有账户当前待执行 job 为0；本轮起始观测后 **LIVE 新 attempt=0**。
- LIVE 券商只读回查 INTC=0；DoxAgent 策略本金仍 **$950**。

保证依靠原终态机制：接收对相同 ID 使用 INSERT OR IGNORE，不重建已有 job；Executor.step() 在 DONE/FAILED 时直接返回；reserve_attempt 和最终 mark_submitting 均拒绝终态任务；groups.advance() 不重开已终结 ENTRY。新容差只作用于新接收组，失败组合不新增观察/回放队列。

旧意图在账面上的自然 `expires_at` 为 2026-11-05 07:00 UTC（北京时间15:00），最终核对时尚未到这个机械截止，但交易机会早已过时。**本次没有篡改时间；它已在失败终态中永久停止，安全不依赖等待自然截止。** 后续过期检查继续保留。独立回归验证了价格变得可行、重复投递、跨过期时间、重启和组推进均不复活失败任务。

## 6. 验收边界与证据

本次完成新的 PAPER 实际订单生命周期；LIVE 仅只读连接、配置/预算、终态和账本核对，没有 LIVE 小额委托。不能据此声称 LIVE CASH 实际成交、结算或将来所有价格组合都已验收。真正的整数无解仍会合理拒绝，不自动关闭对冲、不借第二名额或个人资金。

证据目录：[eval/hedge-integer-upgrade-20261105](../eval/hedge-integer-upgrade-20261105)。关键文件：

- `tests.xml`、`tests.log`、`deployed-sdk-tests.xml`：后端及官方 SDK 补测。
- `production-before.json`、`intc-during-release.json`、`production-after.json`：旧终态前后对照。
- `release-verification.json`：五组件镜像、源文件哈希和原环境/命令校验。
- `paper-entry-state.json`、`paper-final-state.json`、`paper-exit-request.json`：新 PAPER 实际执行与退出。
- `broker-readonly-final.json`：独立券商只读持仓与 LIVE 零新增委托、$950 本金。
- `acceptance-summary.json`：结构化结果。

根目录 `changelog` 已追加代码变更。本次提交未纳入或覆盖工作区原有的 API、成本审计、Schema 生成脚本等未提交工作。
