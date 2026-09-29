# 消息总线统一监测词管理交付记录

日期：2026-09-29

按 `message_bus_monitoring_terms_ui_plan_20260929.md` 完成前后端实现。V2 消息总线配置页在逐源设置前显示当前 Ticker 的搜索概念、分发匹配规则和 Jev 相关性定义。三处共用草稿与一次原子应用；L2可在规则视图与原始JSON之间无损往返。Google Search RSS 的旧 `search_terms` 与 Reuters 的旧 `company_short_name` 在统一词表接管后不可误改，其他参数及轮询间隔继续可编辑。

API新增当前配置GET、无写校验POST和带 If-Match / Idempotency-Key 的完整PUT。词表写入沿用原生 Bus SQLite revision；新增同事务幂等回执。CLI与Web复用语言、组身份、非空定义及正则编译校验。GET只读原库；查询预览由现有 QueryPlan生成。用户更新仅作用于后续读取新版本的任务，既有冻结窗口和已创建分发目标保持原revision。

验证：

- 聚焦后端20项通过，含原生Bus与HTTP契约、CAS、幂等重试、回滚、正则错误定位、搜索词接管及既有搜索/分发回归。
- Vitest监测规则1项通过；ESLint、Ruff、TypeScript类型检查与Vite生产构建通过。
- Playwright组件在1262px和1559px两种桌面宽度完成规则/JSON切换及应用，未见页面横向溢出。
- Playwright通过隔离的真实MessageBusV2Repository + V2 API端到端读、修改、应用、重新读取词表；隔离服务仅在本地临时数据库运行。

尚未把词表应用到生产Bus、提交代码或部署服务。生产部署时先备份Bus SQLite并迁移 `v2_monitoring_terms_commands`；升级API及前端后，在指定Ticker核对当前revision和语言需求，再人工编辑应用。保存成功只证明配置提交，召回效果需要新poll与distribution target证据确认。

本地隔离浏览器复现：先启动 `pnpm --dir frontend/v2 dev --host 127.0.0.1 --port 5174`，再启动 `uv run uvicorn tests.v2_backend.monitoring_terms_browser_app:app --host 127.0.0.1 --port 8099`，然后设置 `DOXAGENT_TERMS_BACKEND=1` 运行 `pnpm --dir frontend/v2 exec playwright test tests/browser/monitoring-terms-integration.spec.ts`。每次后端进程启动使用新的临时Bus数据库。
