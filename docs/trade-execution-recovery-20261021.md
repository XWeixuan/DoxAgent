# 交易执行与退出恢复修复验收

北京时间 2026-10-21。代码提交：`91503abf461bcbc2a2a7ef6c40975c77f16deaee`。

## 已实施

- 在既有 Runtime KV 中持久化账户恢复冷却，间隔为 **2、5、10、30、60 秒，此后 60 秒**。同账户的 ticker jobs、连接维护和空闲恢复共享状态；重启不清空冷却。恢复探测按账户串行，正常 ticker 执行保持并发。
- EXIT 的连接/同步/报价/SQLite 瞬态故障不再消耗三次后永久终止退出。启动和每分钟检查只恢复已到期、仍有 owned lot、因明确瞬态原因失败的退出任务，复用原 generation/attempt。权限、身份、确定拒绝不进入自动恢复白名单。
- 纯查询使用读事务；主循环与 delivery heartbeat 遇到瞬态争用会退避；成交事件未成功应用时不推进 cursor，成功事件按批推进。
- 合并同 broker 的同步请求，完整快照最多短期复用 1 秒。重连、订单/成交事件、发送/撤单时失效；活动订单和发送后的最终对账强制刷新。
- 2100 信息保留内存累计计数，不再逐次写 te_events；拒单和成交仍持久记录。同步错误包含缺失阶段与连接代次。
- Read 以持久 intake 证据优先，归一化旧嵌入 UNKNOWN，展示真实等待错误。维持现有资金周期及额度契约。

## 部署

分别基于正在运行的三个后端基础镜像，仅叠加九个本次模块，避免覆盖其他修复。已发布 Executor、Delivery、Projector、API、Scheduler；发布账本与 Compose 当前镜像均更新，保留每个组件旧镜像作为 previous，未执行资源清理。

UTC 18:27 验证：五个服务九个模块的 SHA-256 均与发布清单一致；未选择服务的镜像无变化；Executor、Delivery、Projector、Scheduler 重启计数为 0。API 启动时因已有 SQLite 写锁争用重启 1 次，随后 healthy，`/healthz` 与 `/readyz` 均通过。这是短期验收窗口，不代表高负载下永久不会争锁。

本地其他会话的 W3 源码、配置与未提交 changelog 内容未混入交易代码提交；运行镜像中的已有修复通过各自原镜像继承。

## 业务验收

1. 原 INTC 退出：`trade:case_c5e73efb13bc4bf9b262e198fd4227cf:exit` 自动恢复，generation 保持 0，只创建 1 个退出 attempt。北京时间 **02:24:15**，BUY 165 股、价格 92.49，已实际成交；原 lot.remaining_qty=0，退出结果 EXIT_FILLED。
2. 新账户资金周期建立：`2026-10-20`，冻结 E=18,209.46 USD，解除旧仓位带来的周期阻塞。
3. 原本两个 DELIVERY_UNKNOWN 意图在各自原有效期内自然执行：

| Ticker | 原 intent_id | ENTRY 实际成交 | ENTRY attempt 数 |
|---|---|---:|---:|
| INTC | trade:case_0b5a60b35b174fbfa3f15a067d76a832 | SHORT 198 股，18,292.30 USD | 1 |
| MU | trade:case_fef78df457004eff92e0b4704f5970cd | SHORT 10 股，9,063.80 USD | 1 |

4. 两条 Execution 与 Case 汇总均已通过正常 CDC 投影更新为 EXECUTED/ORDER_FILLED，读取库 DELIVERY_UNKNOWN 数为 0。无需停 Projector 或手工改物化记录。
5. 六条此前已过期的意图仍为 EXPIRED_SEMANTIC_DAY，均无新增 ENTRY 发送，未进行历史补单。
6. 两笔新持仓已各自生成 READY 退出任务，计划北京时间 10-21 03:30（美东 10-20 15:30）。截至本次验收尚未到期，因此不能把它们的未来退出称为已验证；本次已实际验证的是遗留 INTC 退出及当前两笔开仓。

## 测试

- 交易/恢复/资金周期/投递/结果分类：55 passed，3 skipped（本地无 IBKR SDK）。
- 执行投影、P&L、通用投影：5 passed。
- 在含生产 SDK 的候选镜像内、断网运行补充验证：订单参数映射、拒单回调、2100 不产生落库风暴、缺失 completed_orders End 的阶段诊断通过；不连接券商、不发送真实订单。
- Ruff 与 diff whitespace 检查通过。
- 回归包括：多 ticker 共用账户冷却、冷却跨重启保留、超过三次瞬态故障后恢复退出、恢复不改变 generation、确定错误不自动解封、SQLite 争用不跳过 fill、重复回放不重复成交、读查询不申请写锁、同步合并与强制刷新、已接收 UNKNOWN 的正确分类。

## 可复核记录

本地目录：`eval/trade_delivery_audit_20261021/`。

- `tests-repair-final.xml`、`tests-projection.xml`、`sdk-offline-smoke.json`：测试。
- `build-manifest.json`、`release.jsonl`、`acceptance.json`：镜像、发布账本、运行文件校验与健康状态。
- `pre-repair-ledger.json`、`final-ledger.json`、`business-acceptance.json`：修复前后完整执行账本与业务断言。
- `read-final.json`：两条 Execution、Case 的实际读取结果。

服务器发布目录：`/var/lib/doxagent/releases/trade-recovery-20261021/`。私有 Compose/容器配置备份仅保留在服务器，未复制凭据到本地报告。
