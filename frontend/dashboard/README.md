# DoxAgent Dashboard Frontend

第一阶段 Dashboard 前端实现，基于 Vite、React、TypeScript、Tailwind CSS v4 和 shadcn/ui。页面只消费 Mock Dashboard State API，不直接耦合 workflow、DB 或 runtime 内部实现。

## 启动方式

在仓库根目录启动 Mock API：

```bash
uv run python -m doxagent.dashboard_api --host 127.0.0.1 --port 8780
```

启动前端：

```bash
cd frontend/dashboard
pnpm install
pnpm dev --host 127.0.0.1 --port 5173
```

默认访问：

```text
http://127.0.0.1:5173/overview
```

默认 API 地址：

```text
http://127.0.0.1:8780/api/dashboard/v1
```

可通过环境变量覆盖：

```bash
VITE_DASHBOARD_API_BASE_URL=http://127.0.0.1:8780/api/dashboard/v1
VITE_DASHBOARD_AUTH_TOKEN=dev-mock-token
```

如果 Mock API 以 `mock-required` 鉴权模式运行，也可以打开 `/login` 使用 `dev-mock-token` 保存本地开发 token。

## 页面路由

- `/overview`
- `/ticker/:ticker/research`
- `/ticker/:ticker/strategy`
- `/ticker/:ticker/message-bus`
- `/ticker/:ticker/runtime`
- `/ticker/:ticker/audit`
- `/login`

## 质量检查

```bash
pnpm typecheck
pnpm lint
pnpm test
pnpm build
```

## 实现边界

- 本阶段只接入 Mock Dashboard State API。
- 不接真实生产数据。
- 不修改真实 runtime 执行逻辑。
- 不实现生产 Supabase 鉴权；本地仅提供 mock token 登录入口。
