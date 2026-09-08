# DoxAgent V2 Frontend

独立 React / TypeScript 桌面 SPA。默认运行与生产构建只连接 `/api/doxagent/v2`，没有 V1 页面、组件、样式或 DTO 依赖，也没有失败后切换合成数据的兜底。

功能依据为两份 PRD；视觉与开发规则见 [AGENTS.md](AGENTS.md)、[v2_design.md](../../dev_plan/v2_design.md) 和 [Frontend Foundation](../../dev_plan/workflow_v2/DOXAGENT_V2_FRONTEND_FOUNDATION.md)。

## 启动真实前端

在 `frontend/v2` 执行：

```powershell
pnpm install --frozen-lockfile
Copy-Item .env.example .env.local
# 将 .env.local 中 V2_API_TARGET 设置为实际 V2 API 服务
pnpm dev
```

默认开发地址 `http://127.0.0.1:5174/overview`，默认 API upstream `http://127.0.0.1:8002`，与 V2 后端 Compose 默认端口一致。Supabase 配置从后端 `/api/doxagent/v2/auth/config` 读取；浏览器仅使用 publishable key。没有真实配置时显示连接错误，不提供演示登录。

后端需要已迁移并接通源投影的 V2 读库、控制库、Message Bus 配置库以及 Supabase 开发者身份。其准备流程见 [Backend Runbook](../../dev_plan/workflow_v2/backend_delivery/RUNBOOK.md)。前端启动不会迁移数据库、启动研究 worker 或启用交易。

## 页面

| 路由 | 功能 |
|---|---|
| `/overview` | 状态和周期指标、启动/暂停/重启/移除、初始化进度与恢复、标的导航 |
| `/ticker/:ticker/research` | 当前/历史 Document 1、四类内容、结构化/Markdown 阅读、分块正文、下载 |
| `/ticker/:ticker/expectations` | 当前/历史 D2、Shell/Unit、State/Factors/Gaps、参数与引用 |
| `/ticker/:ticker/strategy` | Shell/ALL、Policy 指标与筛选、固定修订详情、OR 条件、变更时间线、文档来源 |
| `/ticker/:ticker/events` | 完整 Event Library、按日 Reference Delta、Fact 续页、事件索引、移除项 before 快照 |
| `/ticker/:ticker/message-bus` | 消息过滤/搜索、正文、SSE 更新、分钟指标和源状态、Binding 参数表单/JSON/CAS 保存 |
| `/ticker/:ticker/runtime` | 九节点真实流向图、节点详情、Case/W1/W2/W3、候选与执行事实、分钟更新和 Graph SSE |
| `/ticker/:ticker/audit` | API/Codex 分离、成本/Token 汇总、趋势、占比、节点明细；收益审计按 PRD 仅保留占位 |

## 构建与验证

```powershell
pnpm lint
pnpm test
pnpm build
```

`dist/` 是可部署的静态产物。`deploy/nginx.conf` 包含 SPA fallback、同源 V2 代理及关闭 SSE 缓冲的示例；需要设置实际 upstream 和 TLS。生产包不包含 `demo/` 或 `tests/integration` 的身份、数据和入口。

生产部署统一使用仓库根目录 `docker-compose.v2-production.yml`，步骤见 [生产运行手册](../../dev_plan/workflow_v2/backend_delivery/PRODUCTION_RUNBOOK.md)。此前 demo/mock/录制联调入口已退役，不再提供任何绕过 Supabase 的启动模式。单元测试夹具仅位于 tests/fixtures，不被生产入口引用。
