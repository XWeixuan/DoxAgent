# 交易意图投递版本不一致：修复与 PAPER 验收

日期：2026-10-01（北京时间）。范围为 9 月 29–30 日固定 `SHARED_CYCLE` Profile 后的投递失败，不涉及 IBKR 拒单。

## 根因与影响

生产 `v2-delivery` 曾停留在六天前的 `doxagent-v2:server`，而 Scheduler 和 Executor 已采用共享 Equity 版本。旧 `ExecutionProfile` 校验要求 `target_notional_usd`，不识别新 Profile 的 `SHARED_CYCLE` 和 `min_entry_notional_ratio`，因此 Intent 在投递层重试两次后成为 `FAILED`。`submitted=true / UNKNOWN` 只表示开始过投递尝试；原页面把它误呈现为执行结果未知。

生产 Runtime SQLite 核实共有 13 个同因失败的 DELIVERY 任务：10 个在复核时已超过语义有效期；余下 3 个为 9 月 30 日的 MU LONG（北京时间 16:00:25）、INTC SHORT（17:43:16）、INTC LONG（19:20:06）。复核时这三笔均已滞后数小时，INTC 方向相互矛盾，且未取得新的信号确认，因此全部判定不补下历史订单。13 笔均无对应 `te_executions`；修复后仍无新增 execution 或待执行 job。

## 修复

- 先将 `v2-delivery` 切至共享 Equity 兼容镜像。用生产冻结 Profile 在隔离临时 SQLite 中实测 `ExecutionIntake` 返回 `EXECUTION_ACCEPTED`，且仅在临时数据库生成 execution/Entry job；未连接券商、未向生产 Runtime 写烟测订单。
- 代码提交 `9dd43b03`：Delivery 异常后先对账持久化 intake。若已经接收，只补齐回执；若确认 `NOT_FOUND` 且重试耗尽，记录 `DELIVERY_FAILED`、脱敏的 Profile 校验错误。Read/UI 使用 `REJECTED / NOT_EXECUTED` 和“意图投递失败”区分真实执行失败、投递失败以及执行状态待核对。
- 投影补丁 `8c55fcd3`：`CaseSummary.trade_disposition` 仍用契约已有的 `READY`，详细失败原因由 execution/trade 结果承担。否则新的 `DELIVERY_FAILED` 字符串会触发 Case 契约 `ValueError`，把更新送入 Read gap。
- 生产逐条备份原始记录至 `/data/backups/trade-delivery-20261001/failed-intents-before.json`（权限 0600），原子更新 13 个 Intent 的投递终态及“不重放”判定；保留 DELIVERY 任务原有 `FAILED` 与 ValidationError gap 以供审计。Read 的 13 个对应 gap 经补丁自动修复为 0。

## 生产状态与验证边界

当前 `v2-delivery` 使用 `doxagent-v2:trade-delivery-9dd43b03-patch`，Read projector 使用 `doxagent-v2:trade-projector-8c55fcd3-patch`，Web 使用 `doxagent-v2-web:trade-delivery-9dd43b03`。三个服务启动；Read 侧 13/13 个 execution 摘要为 `DELIVERY_FAILED / REJECTED / NOT_EXECUTED`，对应 Executor execution 为 0。前端镜像的静态资源包含“意图投递失败”标签。

后端定向测试 33 项通过；补丁新增的投影测试所在定向组 10 项通过；前端类型检查、目标文件 ESLint 和生产构建通过。真实生产新 Intent 的端到端投递须由下一笔自然产生的 PAPER 信号验证，本次未制造交易信号。此前完整容器环境变量被输出至工具记录的凭据暴露事项独立处理：本轮未读取、复述或保存凭据值；应按该记录涉及的凭据来源轮换，不能用本次代码部署代替轮换。
