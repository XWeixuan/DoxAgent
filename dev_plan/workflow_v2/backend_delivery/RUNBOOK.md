# V2 后端运行入口

当前完整拓扑使用根目录 `docker-compose.v2-production.yml`，服务器参数使用 `deploy/docker-compose.server.yml`。
安装、迁移、启停、权限、数据路径与回退步骤见 [生产运行手册](PRODUCTION_RUNBOOK.md)。
旧 v2-backend 和 ticker-init overlay 已退役；原手册可从退役前 Git 基线恢复。

本地开发使用 `uv sync --locked` 和各 V2 console entrypoint；前端在 `frontend/v2` 用独立的 pnpm 锁文件。
