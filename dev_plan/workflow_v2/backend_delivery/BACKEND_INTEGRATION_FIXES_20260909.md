# 前后端联调后端问题修复

日期：2026-09-09。依据 `FRONTEND_INTEGRATION_ISSUES.md` 中 BE-01～BE-10；保留原前端验收记录。代码、共享契约和本机联调读库已更新，5178 / 8098 已重启。

| 问题 | 修复及复验结果 |
| --- | --- |
| BE-01 | 录制环境实际控制循环定期写入正式 control worker 心跳，monitoring capability 恢复 true；Paper/Live 不变。RUNNING 不允许 restart 是正确门禁，原因改为 ALREADY_RUNNING，而非误报没有激活版本；暂停后原 restart 门禁仍可用。 |
| BE-02 | 非例行维护指标固定 AVAILABLE `0`、COMPLETE，无环比，不再依赖不存在的维护观测。 |
| BE-03 | UNKNOWN 健康不计入 normal/blocked；相应响应 coverage=PARTIAL，reason=HEALTH_UNKNOWN，已知计数仍保留。 |
| BE-04 | revenue_audit.available 固定 false，NOT_RELEASED。 |
| BE-05 | 补充修复：消费账本完整覆盖首次激活、且无消费记录时，投影 consumed=false / effective=true。当前 42 条已有完整零消费证明，ACTIVE 返回 42 / COMPLETE；覆盖不完整或首次激活未知、早于捕获开始时继续保持未知。详见下方补充验收。 |
| BE-06 | 定义 ADD/MODIFY/RETIRE/RESTORE 的发生时间使用正式 PolicySet published_at；激活接管时间仍独立保留。现有 42 条 ADD 已从导入时间修正为 `2026-09-03T15:18:40.001Z`，相应语义日/排序同步修复。 |
| BE-07 | Event 按精度解析 UTC 排序锚点：季度/月/年使用区间起点、区间使用起点、未知置后；正文展示字符串与原精度不变，分页游标使用同一锚点。原 E178/E179/E40/E166 不再排在 E21 前。 |
| BE-08 | 两个原失败 URL 都返回 HTTP 200；保留 W1/W2 判断、attempt 和正文，局部缺失引用标 PARTIAL / UNRESOLVED_REFERENCE；原身份通过 unresolved_reference_ids 保留，不换 pin、不伪造链接。 |
| BE-09 | SSE 从每次提交中发生变化的身份计算前后筛选匹配，不再比较固定首屏。首屏挤出不 REMOVE；后续页身份更新仍 UPSERT；只有真实失配/删除才 REMOVE。维持提交内逐条序号和恢复游标；旧首屏语义的游标明确失效。 |
| BE-10 | MessageSummary 新增必填 stream_offset，来自原生 StreamItem；后端复合排序键、游标和前端比较器使用同一四元组。共享 TS、后端/前端 JSON Schema、相关测试/预览种子已同步。 |

## 保留的真实缺失

- `case_66b98f01d0f740ccb36a4c11a003f79a`：固定上下文中的 E186/E187/E188 缺失；W1 可用部分及 W2 保留。
- `case_7365a02a94054c0a8da01f737c23b2e7`：固定上下文中的 E186/E187 缺失；W1 可用部分及 W2 保留。
- Policy 的此前未知状态已在补充验收中修复：当前样本有完整零消费证明，不能继续按证据缺失处理。

## 验证与环境

- `scripts/verify_v2_backend_fixes.py` 经 **5178 前端代理**复验报告涉及的真实接口，证据为 `.tmp/v2-integration/mu/backend-fixes-verification.json`。
- 后端 2 个聚焦测试通过：SSE 不误移除、后续页更新/失配、提交内续传；时间精度及 offset 排序。
- 前端 3 个消息排序测试通过，TypeScript 检查通过。未重复全套 UI、压力测试或广泛回归。
- 仍保留原 25/25 案例，auto=false，MU RUNNING / MESSAGE_MONITORING，projection_gaps=[]。没有新增模型调用、交易、生产部署或外部写入。
- 本次修改本地派生读库前，通过 SQLite backup 留存 `read-before-be-fixes-20260908T164132.sqlite3`；`be-repair.json` 记录了修复范围。原生源库和证据正文未修改。因排序契约更新，旧本机 View/分页游标已失效，**请完整刷新 5178 页面**后继续联调。

复验命令（只读，不消耗回放情景）：

```powershell
./.venv/Scripts/python.exe scripts/verify_v2_backend_fixes.py
```

派生库修复脚本为 `scripts/repair_v2_integration_read.py --root <隔离联调目录>`，只接受带明确 integration manifest 的目录；使用前停止对应联调服务。其他部署应依据源事实重新生成受影响投影并使旧排序游标失效，不能把本数据包迁移视为生产部署完成。

本记录解决本轮已报告的接口错误/契约缺口，不改变原前端记录列出的完整初始化、同 pin W3、真实成交、Supabase 多身份及部署故障注入等验收边界。


## BE-05 补充修复：完整覆盖下的零消费证明

2026-09-09：纠正上一轮“当前数据无法证明 42 条有效”的结论。Runtime 捕获从 `2026-09-08T13:51:17.571Z` 开始，包含 `runtime_v2_policy_activations`；原生 runtime 收据 2 是控制记录 UPDATE，首次激活时间为 `2026-09-08T13:51:19.132Z`。此前控制历史没有 activation_id。修复时 head/checkpoint 为 179/179，无 runtime gap，原生消费表为零。因此这些 activation revision 可以确认未消费，而非未知。

- `lifecycle.py` 保存每个 Policy activation revision 的首次运行接管证据。D3 发布时间仍用于定义变更；BACKFILL 不冒充首次激活，再次启动或恢复也不重置首次时间。
- 投影 worker 在 runtime 捕获覆盖更新时重新计算消费状态，并将覆盖和 Policy 更新放入同一读库提交；捕获未追平、缺少消费表覆盖或首次时间不足时保留未知。已有消费记录始终优先，effective=false；已退休策略不会被重新激活。
- 捕获缺口或源不可用会撤销依赖完整覆盖的负面证明；重新追平后恢复。checkpoint 参与更新判定，解决同一 head 下先未追平、后追平时不刷新投影的问题。
- `scripts/repair_v2_policy_coverage.py` 只修复当前 MU 隔离联调读库：先核验原生收据与首次控制历史、备份 SQLite，再补 admission 证据并重投影。源库没有写入虚构消费或新业务数据。
- 已备份 `read-before-policy-coverage-20260908T171234225023.sqlite3`。证据位于 `.tmp/v2-integration/mu/policy-coverage-repair.json` 与 `policy-coverage-verification.json`。
- 必要验证：9 个聚焦测试通过，覆盖零消费证据、未知边界、消费优先、恢复不重置首次时间、捕获追平刷新。经真实 5178 代理请求，ACTIVE=42、coverage=COMPLETE、active metric=AVAILABLE 42，ADD 仍为 42 条。
- 5178 / 8098 已重启；刷新页面获取新 read-context 后继续联调。旧 read-context 维持原有快照语义。
