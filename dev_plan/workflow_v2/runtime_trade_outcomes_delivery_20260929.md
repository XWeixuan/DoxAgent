# Runtime 交易结果与 Case 导出实施记录（2026-09-29）

## 已实现

- Read 投影把正式交易意图、有效 ENTRY 成交、明确零成交终结和证据不完整区分为独立状态；Case 结果、图节点/边、路径计数、Runtime KPI 使用同一归纳结果。旧 Case 的 API 临时读取兼容仅在缺少 `trade` 字段时查询同一水位执行摘要。
- 五列运行链路、最近记录的研判/执行两列与三个交易结果筛选；勾选模式固定列表 view，按选中顺序取齐 Case 明细、全部分页、全部正文和交易订单/成交，全部成功后才下载 JSON。取消和范围变化中止读取。
- 候选、执行、执行详情、订单及成交接口接受可选 RUNTIME `view_id`，沿用无 view 参数的兼容调用。契约、PRD、schema 与示例同步。
- 新增 `python -m doxagent.v2_read.cli reproject-trade-outcomes --read-db <read.sqlite3> --ticker MU --limit 100`。该操作只改派生 Read，按 ticker/case_id 记录水位，重跑幂等，不改交易原始账本。

## 本地验证

- 后端指定检查集：18 passed；包含状态优先级、成交更正撤边、历史回填幂等、固定 view 下的候选/执行/订单/成交读取与 SSE 更新。
- 前端 Vitest：7 passed；跨页 attempts/messages/candidates/orders/fills、多块中英文正文及失败不生成文件。
- Playwright：1262px 与 1559px 两项通过；五列图、节点展开、表格列对齐、复选框与详情点击互不干扰。浏览器使用当前组件的离线 fixture；并非生产 API 验收。
- ESLint 定向文件、V2 类型检查、生产构建及 `git diff --check` 通过。

## 发布顺序与未执行事项

本轮未提交/推送、未改生产服务、未运行生产回填。发布时先备份 Read 数据库，在离线副本运行上述命令并核对三类结果筛选集合、图节点/旧边撤销和已成交 Case；随后在停止当前 Read projector 持锁进程的窗口运行正式回填。命令使用与 projector 相同的 OS 锁，无法并发执行；完成后把后端 API 与前端同批切换、重建 read context，再通过真实已授权浏览器验证下载。回滚须恢复同版 API/Web 和 Read 备份或重投影。

旧的本地真实数据联调说明 `backend_delivery/REAL_DATA_INTEGRATION.md` 所列 `scripts/start_v2_integration.ps1` 与 `src/doxagent/integration_v2` 当前检出中不存在，不能把该说明当作可启动环境。本轮用真实 API 路由的 TestClient 和浏览器组件夹具覆盖开发路径，生产数据联调仍需部署环境执行。

## 2026-09-29 发布进度

代码已以 f3da669b 推送至 main；新加坡服务器从独立、干净的 /home/ubuntu/doxagent-release-f3da669b 检出构建并逐个切换 V2 API、Web、Read projector 与 Message Bus。原生产检出的未提交改动保留，Luna 恢复队列的 initialization、O4、scheduler 容器和恢复脚本均未重启。API/Web 健康，生产前端与 /healthz 返回 200。

正式 Read 历史回填及其前置备份尚未执行：生产 Read SQLite 约 17 GB、当前 Case 约 2,803 个，Luna 恢复队列正在处理历史任务；此时停投影器并对同盘数据库进行全量备份/回填会增加队列争用。待恢复队列结束或取得独立维护窗口后，按上文顺序备份、离线验证，再运行 reproject-trade-outcomes 并验收历史图与筛选。新 Case 已由新版 projector 实时投影；旧 Case 的完整历史交易结果在回填前不视为验收完成。

## 2026-10-01 历史回填完成

Luna 恢复完成后，完整备份、离线核验及生产 3,243 条 Case 回填均已完成；8 条已执行、30 条未执行的图节点和边一致，projector 已恢复。新增 Policy 命中筛选，按最终命中布尔值筛出 6 条 Case。详见 [交付与验收记录](runtime_case_backfill_delivery_20261001.md)，本节取代上文历史回填待执行状态。
