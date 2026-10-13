# 2026-10-14 实施暂停记录

用户批准方案：dev_plan/workflow_v2.1/frontend_readability_and_policy_metrics_optimization_plan_20261014.md。

## 已完成
- 引用共享解析/编号/安全 Markdown AST 和 JSX 渲染；只改变显示，不重写研究源或下载。
- 原生 D2 State 分组对照表、预期基线、单列因素、带名称/影响/分隔的差异路径；保留模式和锚点。
- Policy 命名关联、默认折叠资料、条件中文层级/方向资格、经济传导图标、小型 pinned Policy 读取。
- 关联解析验证来源 run/artifact/SHA；D3 shell 文件采用一基源序号，映射到 Shell view_id 原始指针，不误用零基阅读 ID 或 outcome ordinal。
- KPI 集合聚合、ACTIVE 页先分页后补标记、同一聚合 unknown_count；两条正贡献部分索引仅显式命令执行，GET/启动不迁移。
- 必要回归：前端63项通过；后端 policy_queries/expectation_refs/formal/direction_usage_reads/policy_consumption_coverage 共28项通过；旧查询小库等价检查后再次 policy_queries 3项通过。TypeScript、改动相关 ESLint、生产构建通过。后续微调 CSS 与正文引用入口后 TypeScript/ESLint/63项前端测试再次通过，最终构建待收尾时再跑一次。
- CUA 浏览器：BE已发布研究只读快照，1262/1559宽度，状态表/路径/BASELINE索引/业务名称深链；无整页横向溢出，圆标无焦点交互。Markdown 表格与代码引用检查通过。测试夹具已扩充，Playwright spec 文件更新但没有用独立 CLI 执行。

## 未完成，恢复后从这里继续
1. 用户最新指示：SSH重试一次，失败就暂停。实际单次重试在8秒后 Connection timed out during banner exchange，故已停止开发/验收。不要自发改代理/服务器网络。
2. 先核对 API容器中 /tmp/ui-impl/benchmark.py 隔离测试进程是否仍在运行，生产服务本身未重启/改动。
3. 隔离库 /tmp/ui-impl/isolated-final.sqlite3 已复制所需生产 Policy 对象/贡献并建立读索引；前两次失败副本 isolated.sqlite3 / isolated-bulk.sqlite3 也是本轮测试文件，不是业务库。源 /data/read/v2.sqlite3 只读；未修改生产索引/业务状态。
4. benchmark-final.log 中 BE/MU已获得样本，INTC上一交易日ALL范围5样本最大9609.81ms、冻结范围最大1183ms，尚未达到SQL p95≤500ms/API p95≤1s目标。需执行计划/冷热I/O证据进一步定位，不能宣称KPI性能修复已验收。
5. 验证脚本API段需修正：create_app内部从 canonical doxagent.api_v2.policies 导入 install，当前 api_module.policies 属性赋值不足以换装候选；应 load 同名 canonical module 再创建隔离应用。无需改生产包。按键增量保存结果并远端日志落盘以避免SSH中断丢失结果。
6. 新文件Ruff format已运行；尚余14项长SQL字符串 E501待断行，涉及 policy_queries.py / policy_query_reference.py。其他Ruff规则已通过；不要宣称全部Ruff通过。
7. 收尾补最终构建、必要新增回归、完整性能/API/浏览器验收和正式实施回执。当前截图保存在本目录；fullPage截图曾超时，采用viewport保存。
8. 无Git提交、推送或部署。本地有其他任务大量未提交改动，基线快照在 baseline/；不得整体覆盖或全量打包。后续发布边界遵循方案。

## 证据
- benchmark.py / benchmark-final.log / extract_visual.py
- d2-gaps-1262.png / d2-state-1559.png / policy-1262.png
- frontend/v2/tests/fixtures/v21-reading.real.json：公开研究业务快照，不含认证数据。

---
续接状态（2026-10-14）：开发与必要回归已收尾，以上待办为暂停时历史记录，不再作为当前任务清单。完整结果、忙时性能尖峰和后续发布边界见 `dev_plan/workflow_v2.1/frontend_readability_and_policy_metrics_optimization_implementation_receipt_20261014.md`。本轮未部署。
