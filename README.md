# DoxAgent

DoxAgent 使用 Codex SDK 编排独立的 Global Research、Market Situation、Document2、Event Library、Document3 与 Monitoring O4 工作流，并通过 Message Bus V2、持久化 Runtime V2、控制服务和交易执行服务运行。前端位于 `frontend/v2`，API 前缀为 `/api/doxagent/v2`。

自建 ReAct／Blackboard V1 编排、旧 Dashboard 和专用入口已经退役。`v1` 或 `legacy_document1` 仍可出现在当前 Codex 资源版本、兼容研究 lane、CDECR 知识库和历史身份中，这些名称不代表旧引擎仍可启动。

## 本地开发

Python 3.11–3.13，使用 `uv.lock`：

```powershell
uv sync --locked --group dev
```

共享供应商配置参考 `.env.example`，V2 进程配置参考 `.env.v2.example`。真实环境文件、凭据、状态库和浏览器身份不纳入 Git。`.uv-python` 是本地 `.venv` 的解释器基础，不能当作垃圾缓存删除。

先按运行手册配置隔离开发数据目录、完成所需迁移，再按需要运行 `uv run doxagent-v2-api`、`uv run doxagent-v2-control`、`uv run doxagent-v2-scheduler`、`uv run doxagent-ticker-init`。所有入口见 `pyproject.toml` 的 `[project.scripts]`，参数见各 CLI 的 `--help`。

完整容器装配使用 `Dockerfile.v2`、`docker-compose.v2-production.yml` 和 `deploy/docker-compose.server.yml`。旧默认 Compose／Dockerfile 已退出。上线与回退步骤见[生产运行手册](dev_plan/workflow_v2/backend_delivery/PRODUCTION_RUNBOOK.md)，连接见 [SSH 指南](docs/ssh-connection-guide.md)。本地开发也使用当前 V2 服务入口，不通过旧 API 代理。

V2 前端有独立 pnpm 锁文件：

```powershell
Set-Location frontend/v2
pnpm install --frozen-lockfile
pnpm dev
```

## 验证

离线测试拒绝真实 HTTP、外部 socket 和真实 Codex app-server 启动。pytest 使用 importlib 模式避免不同子目录同名测试模块冲突。

```powershell
uv run pytest --collect-only --offline -q -m "not real_api and not real_db and not cdecr_real_models and not cdecr_real_db and not cdecr_real_step2"
uv run pytest --offline -q -m "not real_api and not real_db and not cdecr_real_models and not cdecr_real_db and not cdecr_real_step2"
uv run python scripts/check_v1_retirement.py
```

前端运行 `pnpm schema`、`pnpm typecheck`、`pnpm test`、`pnpm lint`、`pnpm build`。真实模型、生产数据、broker、部署及浏览器业务验收分别执行，不由离线通过替代。

## 代码与资源

| 路径 | 内容 |
| --- | --- |
| `src/doxagent/workflows/codex_*` | 当前 SDK 工作流与恢复逻辑 |
| `codex_runtime`、`codex_worker`、`data_runtime`、`observations` | 协议、Worker、受限工具与观测 |
| `message_bus_v2`、`content_enrichment`、`crawler_plane`、`site_strategy` | 消息、正文、爬虫与浏览器身份 |
| `ticker_initialization`、`initialization_repair`、`event_library`、`persistent_runtime_v2` | 初始化、事件和持久化运行 |
| `api_v2`、`v2_control`、`v2_read`、`runtime_scheduler`、`trade_execution` | API、控制、投影、调度和交易 |
| `model_usage`、`horizontal_collection`、`monitoring`、`models` | 当前计费、采集、共享规范与轻量权限类型 |
| `src/cdecr`、`prompts/codex_v2`、`prompts/persistent_runtime_v2` | 当前代码与版本化工程资源 |
| `dev_plan/workflow_v2`、`dev_plan/workflow_v2.1` | V2 契约、运行手册与 staged v2.1 方案 |
| `tests/fixtures`、`eval`、`pilot_runtime` | 当前夹具、冻结评估和 Pilot |

V2 视觉与交互依据见[设计说明](dev_plan/v2_design.md)、[前端 PRD](dev_plan/workflow_v2/DOXAGENT_V2_FRONTEND_PRD_PART1.md)和 `frontend/v2/AGENTS.md`。批准的旧参考截图保留于 `dev_plan/v1_frontend_reference`。

退役范围见[批准方案](dev_plan/v1_retirement_cleanup_final_20261004.md)。项目外档案位于 `C:\Users\WEIXUANXIE\Desktop\DoxAgentArchive\v1-retirement-20261004`，清单与 SHA-256 记录于 `dev_plan/v1_retirement_archive_manifest_20261004.json`。Git 基线、未提交补丁和旧设计原稿可从该档案或退役前提交恢复。
