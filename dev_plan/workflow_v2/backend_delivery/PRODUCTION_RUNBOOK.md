# DoxAgent V2 生产部署与运行手册

适用版本：2026-09-09 生产接线。唯一拓扑文件为仓库根目录 `docker-compose.v2-production.yml`，不要叠加旧 `docker-compose.yml`、ticker-init 或 v2-backend overlay。前端只访问同源 `/api/doxagent/v2`，登录/权限沿用已有 DoxAtlas Supabase 项目；研究数据项目与 Auth 项目可以不同。

## 运行职责与持久化

| 服务 | 职责 |
| --- | --- |
| v2-migrate | 显式备份、建表/迁移、安装事实采集、有限批次回填；成功后 worker 才启动 |
| v2-api / v2-web | 正式 FastAPI + SupabaseAuth；构建后静态 SPA、同源代理和 SSE |
| v2-control | 持久控制命令、工作 epoch、暂停/重启/移除及状态镜像 |
| v2-initialization | 默认真实 D1 → CDECR → O2 → D2 → D3 → O4 → 激活及消费者 ACK |
| codex-worker | 正式 SDK、能力令牌和工作目录；健康检查不代表模型账户已登录 |
| v2-message-bus / v2-o4 | 正式来源获取、爬虫与 O4 后续工作；Bus 不在 scheduler 中重复轮询 |
| v2-scheduler | 受管 Persistent Runtime、W1/W2/W3、日结/维护/选择；单 ticker 故障隔离 |
| v2-projector | 研究/初始化/Bus/Runtime 事实 → 本地 MVCC 读模型及最小 SSE 变化日志 |
| v2-delivery | 独立 durable intent 投递；所有 ticker 暂停后仍可处理已释放 intent |
| v2-executor | Paper/Live 共用的唯一 broker socket writer，OS 锁防止同库第二 writer |

所有业务目录在 `v2-data` 持久卷 `/data`：research、initialization（包含工作检查点和调用 spool）、runtime（包括消费、intent、执行、fill/lot）、bus（含 adapter/candidate）、scheduler、events、o4、usage、cdecr、crawler-plane、workspaces、codex-home、read、backups。不要把 SQLite 放在 NFS/跨主机共享文件系统上，不允许多主机同时写同一 runtime 库。

SQLite 权限是文件级。当前各 backend 服务共用持久卷；这不提供表级隔离，不能宣称同卷中的 API 只有全局只读权限。HTTP 查询用短只读事务，控制写入走正式仓库；Codex Worker 只通过受能力令牌约束的内部服务访问。内部 API、Worker、broker 端口不向公网发布。

## 准备配置与首次部署

1. 使用 Docker Compose v2；复制 `.env.v2.example` 为 `.env.v2`，权限设为 0600。worker bearer/capability secret 各用至少 24 字符的独立随机值；不要把 `.env.v2`、Codex 登录文件或账户配置提交 Git。
2. Auth 填现有 DoxAtlas 项目 URL 和 publishable/anon key；不要填 service_role/secret key。已有 `user_profiles.user_id` 应对应 Auth 用户，`tier=DEVELOPER` 才能操作。该表必须由可信服务/管理员控制 tier，客户端不能提升自己的 tier；authenticated 至少可读取自己的行。不要为通过验收给 anon 开放 profiles。
3. 设置正式 DashScope、DeepSeek/CDECR 和各数据源所需配置；不要将 CDECR 的 Supabase URL 误当登录项目。生产进程从 env 读取，不依赖宿主 `.env` 的隐式文件加载。
4. 构建与迁移：

```bash
docker compose -f docker-compose.v2-production.yml config --quiet
docker compose -f docker-compose.v2-production.yml build v2-api v2-web
docker compose -f docker-compose.v2-production.yml run --rm v2-migrate
```

不要把完整 `compose config` 输出到公开日志，它可能展开环境变量中的密钥。`migrate` 每次迁移前备份已存在的各业务库和读库，安装 capture 后才回填。它不导入录制数据、不创建 ticker、不创建交易 profile、不发订单。

5. 为 `/data/codex-home` 配置正式 Codex SDK/CLI 登录。可使用已授权账户的 auth.json，或按当前官方 CLI 完成登录；不得把登录文件烘焙进镜像。启动后请求内部 `/v1/readiness`（Bearer 为 worker token），确认实际所需模型可用；`/healthz` 只证明 HTTP 进程存活。
6. 启动拓扑：

```bash
docker compose -f docker-compose.v2-production.yml up -d
docker compose -f docker-compose.v2-production.yml ps
docker compose -f docker-compose.v2-production.yml exec v2-api python -m doxagent.production_v2 check
curl --fail http://127.0.0.1:8082/healthz
```

首次部署没有历史数据时，页面显示真实空状态。用正式开发者账户登录后添加 ticker，将通过真实初始化流程生成研究与策略；不能把合成/录制样本填入生产库以使页面看起来有数据。

## Paper / Live 绑定

研究与策略生命周期按 ticker 唯一，Paper/Live 仅是新 intent 的执行环境。一个 ticker 可以分别保存 PAPER_TRADING 与 LIVE_TRADING 的不可变 profile binding；切模式不复制研究、不清空消费、不更换旧 intent 的账户 pin。

1. 按 `ExecutionProfile` schema 准备 JSON，包含 profile_id、environment、account_mode、host、port、client_id、expected_account_id、方向与 strategy。Paper 必须绑定真实 Paper 账户；Live Cash 禁止 short。broker 地址从容器可达，宿主 Gateway 可用 `host.docker.internal`；端口本身不是账户环境的证明。
2. 把 JSON 放在受控 `/data/operator` 路径，导入后记录输出的不可变 revision：

```bash
docker compose -f docker-compose.v2-production.yml exec v2-executor python -m doxagent.trade_execution.cli --db /data/runtime/runtime.sqlite3 import-profile --file /data/operator/paper.json
docker compose -f docker-compose.v2-production.yml exec v2-api python -m doxagent.production_v2 bind-profile --ticker MU --mode PAPER_TRADING --revision REVISION --actor OPERATOR
```

修改已有绑定必须加 `--expected-revision OLD_REVISION`，不得使用全局 activate-profile 替代 ticker binding。Live 使用正式 LIVE profile 和 LIVE_TRADING 绑定；本轮部署验收不导入、不启动 Live 交易任务。

3. broker 只读探测用 `trade_execution.cli probe --profile REVISION`；Paper 真成交验收需要单独明确安排，不能用握手、what-if 或空闲 worker 健康代替。不要在持仓期间停掉 executor，暂停 ticker 仅关闭新分析/新 intent，已接管 Entry、重试、成交与退出仍需 executor 持续管理。

## 现有正式数据迁移与回退

1. 先清点原生库、完整工件目录、未完成 intent、执行/lot、profile 以及运行中的唯一 writer。停止新分析并等待/明确处置在途模型工作；迁移 runtime 数据前必须停止原 executor 与 delivery，避免两个 writer 使用复制库继续交易。不要把本地录制联调库当正式来源。
2. 停止所有写入后，用 SQLite backup 保存业务库；复制完整工件树、candidate adapter 配置、spool、workspace 和控制状态。仅复制 `.sqlite3` 主文件而遗漏 WAL 不是一致备份。
3. 不得随意重写不可变 pin。检查已保存的工件根目录、绝对路径和 worker workspace 地址；切换目录时保留原绝对路径的挂载映射，或先完成有证据的路径迁移。Windows 盘符路径不能直接在 Linux 生产环境使用。新增 Compose override 可调整路径/挂载，但同一领域的所有生产消费者必须使用相同路径。
4. 运行迁移后，先启动 projector，核查 gap、head/checkpoint、固定 artifact ref 以及生产历史分类。BACKFILL 不会证明首次激活时间，证据不足的 Policy 继续为未知。历史业务资格必须通过现有 `v2_read.cli import-history` 的显式清单，不自动将历史 V1 或录制样本提升为 V2。
5. 切换前保留上一版镜像 tag/digest、环境文件、全部库与工件的一致备份。回退时先停新版本全部 writer，再整体恢复一致备份及上一版镜像；不能只回退 read DB 或单独清空消费表。恢复后核对 broker 真实持仓与成交再恢复新交易。

读投影可通过 `v2_read.cli rebuild/verify/switch` 生成影子读库后切换；这是派生读模型恢复，不是重新执行 workflow。更换 generation 后前端应重新获取 read-context。

## Policy ACTIVE 与状态证据

`ACTIVE` 仍要求 lifecycle=ACTIVE、effective=true、consumed=false。完整消费捕获覆盖首次运行接管时间且没有消费记录时才能确认 false/true；捕获追平刷新会重算该状态。覆盖不完整、源不可用、首次激活未知或早于捕获时保留未知；消费记录优先，已退休不能重激活。定义 ADD 时间仍为 D3 published_at，不能用于零消费证明。

## Nginx / SSE / 运维

仓库 Nginx 提供 SPA fallback、静态资源缓存和 SSE 关闭缓冲、缓存、gzip，读超时 75 秒，大于 15 秒 keepalive。公网 TLS 反代到 `127.0.0.1:8082` 时也必须关闭 SSE 缓冲/缓存并保留 Authorization、Last-Event-ID 和查询参数；不要让 CDN 缓存 `/api/`。只有真实会话能打开 SSE，token 过期后客户端应刷新并恢复，不能延长服务器身份有效期。

```nginx
location / {
    proxy_pass http://127.0.0.1:8082;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header Connection "";
    proxy_buffering off;
    proxy_cache off;
    proxy_read_timeout 75s;
}
```

定位故障先看 `compose ps`、单服务近期日志、`v2_read.cli diagnose` 和 worker readiness。source gap 不是零数据；账户连接故障不能通过清空任务/消费记录解决。定期轮转备份并检查磁盘，避免无限增长的工件、日志、源收据挤满 SQLite 所在卷。

本地验收结果与未通过的外部业务分支见 `PRODUCTION_ACCEPTANCE.md`。静态构建、空闲拓扑、模型账户 readiness、真实研究成功、Paper 成交和 Live 放行是不同验收层级，必须分别记录。
