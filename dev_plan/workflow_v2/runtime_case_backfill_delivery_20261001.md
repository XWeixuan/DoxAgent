# 旧 Case 结果回填与 Policy 命中筛选交付

## 修复与边界

Executor 已经将下单重试耗尽、资金/容量不足等零成交终态写为 FAILED；问题在 Read 分类：历史投递 UNKNOWN 优先于明确执行终态，且生产 projector 曾切回缺少交易结果投影的旧版。修复后，有效 ENTRY 成交优先，其次明确 FAILED/DIRECTION_DISABLED 零成交终态，无须等待语义交易日结束。同期投递修复补齐的 DELIVERY_FAILED + REJECTED 也归为未执行；证据不足不会按超时猜测为未执行。

最近处理记录新增 `result=POLICY_HIT`，只匹配 `final_policy_hit.value=true`，不是 R1 召回候选，也不是新结果/路由/图节点。保留来源、日期、固定 view 与分页语义。

## 生产回填验收

2026-10-01 在 Luna 恢复结束后完成全部 3,243 条 Case 的派生 Read 回填。固定读快照 seq=2609916 的核验结果：

| Ticker | 已执行 | 未执行 | 无交易意图 | Policy 命中 |
| --- | ---: | ---: | ---: | ---: |
| BE | 2 | 14 | 361 | 3 |
| INTC | 1 | 3 | 611 | 0 |
| MU | 5 | 7 | 2111 | 1 |
| RKLB | 0 | 6 | 122 | 2 |
| 合计 | 8 | 30 | 3205 | 6 |

全部 Case 汇总与同一快照的 execution 证据一致；图中每条已执行/未执行边均从 TRADE_INTENT 出发，Graphs 实际节点与边计数一致。Policy 命中分页集合与最终布尔判定一致。首次验收跨连接读取遇到实时更新造成单条不一致，固定事务快照复核后差异为零。同期投递修复补齐了历史未执行证据，最终快照无 PENDING/UNKNOWN；这一结果不改变未来证据不足时保留 UNKNOWN 的规则。

回填只更新派生 Read，不重放消息、不重新下单、不改原始交易账本。操作期间停止 Read projector；完成后最新 projector 已恢复运行，未重启 Executor、Scheduler、Initialization、O4。

## 备份与部署

- 完整 Read 在线备份 19,837,399,040 字节，SQLite quick_check=ok；压缩归档约 1.8 GiB，zstd 完整性检查通过。
- 服务器备份及验收报告目录：`/home/ubuntu/doxagent-backups/runtime-trade-20260930`，保留 `read-before-backfill.sqlite3.zst`、SHA256 sidecar、`shadow-check.json`、`production-check.json`。
- 压缩归档 SHA256：`499bbbf21209a16153255987fa4901ab923852d7964870ce07b908f08f4140a4`。
- 本次修复提交 d11f7fa1 已推送 main；部署同时包含后续交易投递修复 9dd43b03 和 projector 契约修复 8c55fcd3。
- 运行镜像：API `trade-delivery-9dd43b03-patch`、Web `trade-delivery-9dd43b03`、projector `trade-projector-8c55fcd3-patch`（均为 doxagent-v2 对应镜像）。API/Web healthy，页面及 healthz HTTP 200；恢复后 projector 进程持续运行，无新增日志错误。

## 必要测试

定向后端 8 passed，覆盖终态优先级、成交优先、当日未执行历史回填、Policy 命中 HTTP 筛选与快照分页；前端类型检查、生产构建、Runtime ESLint 和 diff 检查通过。生产验收直接检查真实派生数据及 Graphs/分页读取，未另做全页面浏览器回归。
