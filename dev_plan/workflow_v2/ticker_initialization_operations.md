# Ticker Initialization V2 运维手册

本文件描述已接线的默认生产入口及人工恢复合同。本次只做开发与离线验证；真实供应商、真实 ticker、部署与远端迁移未执行。初始化完成后移交现有 Runtime V2/TRADING，不包含第二部分的持续运行调度重构或代码热替换。

## 进程与存储

初始化 Worker、Message Bus Worker、Runtime Scheduler 必须共享同一个 `DOXAGENT_TICKER_INITIALIZATION_CONTROL_PATH`。同时共享 Codex runtime SQLite、Event Library、Message Bus SQLite 和 crawler-plane volume。独立进程各自读取同一 active revision；初始化进程不能代写消费者 ACK。

可选部署叠加文件：`docker-compose.ticker-init.yml`。实际部署由运维显式执行，本次开发不运行部署命令。使用时同时加载原 compose 与此 overlay，并启用既有 `codex-v2` profile。必须先配置 Codex Worker、CDECR 历史数据与模型供应商凭证；凭证不写命令行或本文件。

Bus 在真实调度循环接纳候选配置并启动 ticker，Runtime 在 Bus ACK 后加载同 revision 的 Index/Projection。首次 cursor 只初始化一次。暂停同一 revision 的 ticker 不会因周期性 admission 自动恢复。

三个常驻进程必须使用持久卷，不要用容器临时目录保存控制库或成果。Bus/Runtime 的心跳由各自进程续写，长轮询/长 Case 期间也每 15 秒续写已接纳 revision；有效窗口 60 秒，启动节点默认等待 180 秒。W3 仅做签名 readiness 检查，不发模型任务。升级镜像时可用 `DOXAGENT_BUILD_COMMIT` 记录构建 commit；执行记录另存本地源代码、prompt/skill 文件与请求摘要，不提供本期代码热替换。

## 已有命令

以下命令会在用户真实运行时创建或执行任务，本文仅列出说明。

```text
uv run doxagent-ticker-init submit --ticker MU --research-cutoff-at 2026-09-05T00:00:00Z
uv run doxagent-ticker-init worker
uv run doxagent-ticker-init status --initialization-id <id>
uv run doxagent-ticker-init inspect-node --initialization-id <id> --node <key>
uv run doxagent-ticker-init resume --initialization-id <id> --node <failed-key> --reason <reason>
uv run doxagent-ticker-init rerun-node --initialization-id <id> --node <registered-node> --reason <reason>
uv run doxagent-ticker-init rerun-block --initialization-id <id> --block D3 --reason <reason>
uv run doxagent-ticker-init adopt-artifact --initialization-id <id> --node d3 --result <node-result.json> --reason <reason>
uv run doxagent-ticker-init invalidate --initialization-id <id> --node d3 --node o4.configure --reason <reason>
uv run doxagent-ticker-init activate --ticker MU --artifacts <references.json> --research-cutoff-at <timestamp> --reason <reason>
uv run doxagent-ticker-init replace-artifact --ticker MU --role document3 --reference <reference.json> --reason <reason>
uv run doxagent-ticker-init rollback --ticker MU --revision <revision-id> --reason <reason>
uv run doxagent-ticker-init reinitialize --ticker MU --research-cutoff-at <timestamp>
```

`--database <path>` 是全局参数，位于子命令前；其路径必须与两个消费者的控制库配置一致。`submit` 默认使用生产 V2 DAG；`--plan` 和 `--adapter-factory` 仍可用于隔离测试。

`references.json` 包含 `document1.run_id`、`document2.run_id`、`event_library.version`、`document3.version`、`monitoring_configuration.initialization_id` 等本地已发布引用。替换只改变指定引用，不自动重算下游。人工 activate/rollback 会复制选定候选配置为新 operation 的独立配置，不修改旧快照。

第一次启动失败保持未就绪并等待精确 resume；替换旧运行时启动失败会 CAS 回滚，消费者随后接纳原 revision。人工恢复会重新提交同一 activation identity，不重新 seek-to-tail。

### 如何选择人工操作

- 失败恢复用 `resume`：默认只开放失败节点的新一代预算；指定内部 key 时，容器只恢复遍历和交接，已经成功的内部节点不重新 dispatch。每代最多两次逻辑执行。历史新闻按日/分页缓存；CDECR 复用原生 Registry 的任务/epoch 成果，并与原生 Bulk Task 共用预算。不能通过不断重启 Worker 刷新预算。
- 主动重做 agent 内部节点用 `rerun-node`：选择 `status` / `inspect-node` 中实际存在的 D1/D2/D3/O2 turn key，从首次执行前的不可变 workspace 快照 fork 新 workspace，只执行该 turn，保留原 workspace、后继和 active revision。输出是候选成果，**不会自动 publish/activate**。旧运行若没有该快照不能伪造精确重跑；原生 CDECR/抓取单元使用各自 checkpoint 做失败恢复，主动重建其成果使用 `rerun-block CDECR`。
- `rerun-block` 只选指定板块，冻结外部依赖；新任务不会暗含下游重算。D3 板块产出 candidate version，不修改 current/active。
- `adopt-artifact` 的 JSON 是 `NodeResult`（如 `{"artifacts":{"document3":{"version":2}},"quality_annotations":[]}`）；用于认领已发布的板块交接物，不把一段内部 turn 回答冒充完整发布物。原任务未完成时允许继续未完成部分；不会使已成功后继失效，也不会修改运行中的 activation。
- `invalidate` 只标记逐个明确指定的节点，不自动扩散、不启动计算。随后须人工认领替代交接物或创建隔离重跑，不能让普通 resume 误用已失效旧产物。
- 显式 `activate` / `replace-artifact` / `rollback` 才改变消费者的 active revision；这些操作同样排他、可恢复并保留审计。新的输入只影响新 Case，正在运行的 Case 保持原有引用。
- `reinitialize` 是显式完整重初始化入口，默认不会自动触发；同一 ticker 有 active operation 时所有重复请求都被拒绝。

内部原始输入、完整 receipt、workspace snapshot、错误详情仅在本地。不要把 `inspect-node` 输出或数据库随意上传。进度 phase 为 UPSTREAM、O2、D2、D3、O4_CONFIGURE、O4_DELIVER、REGISTER、ACTIVATE、START_BUS、START_RUNTIME、VERIFY_READY；动态节点不提供误导性的固定百分比。PARTIAL/DEGRADED 仅为诊断标签，运行结果只使用 SUCCEEDED/FAILED。

### 备份与灾难恢复

断点恢复以控制 SQLite、各成果 SQLite、CDECR registry、候选配置目录和 Codex Worker workspace/snapshot 仍可访问为前提。停写后整组备份共享持久卷，或使用 SQLite 一致性备份并协调对应不可变成果目录；不要只复制有活动 WAL 的单个 `.db` 文件。恢复需保持原控制库和路径映射；Supabase 摘要不能还原完整工作流。恢复前确保旧消费者/初始化进程已停止，避免两个服务实例同时占用同一实际工作目录。

## 云端摘要

仅在配置 `DOXAGENT_TICKER_INITIALIZATION_SUMMARY_URL` 与后台 `DOXAGENT_TICKER_INITIALIZATION_SUMMARY_KEY` 时启用。每 60 秒尝试清空本地合并 outbox，云端断开不影响节点执行。白名单包含 9 个基础状态字段和 5 个可选摘要字段：operation_kind、failed_nodes（最多 10 个）、diagnostics_count、activation_manifest（限定引用字段）、last_operator_action（原因仅保留 hash）。不发送正文、prompt、receipt、错误文本、完整操作理由或 Worker 心跳。

迁移为 `supabase/migrations/20260907061350_ticker_initialization_summaries.sql`，已于 2026-09-07 应用到远端。RPC 按 state_seq 条件更新，旧摘要不能覆盖新摘要。按 [Supabase RLS 文档](https://supabase.com/docs/guides/database/postgres/row-level-security) 和 [函数权限文档](https://supabase.com/docs/guides/database/functions)，表启用 RLS，撤销 public/anon/authenticated 权限，仅授予后台 service_role 必要权限，函数使用 SECURITY INVOKER。

## 验证边界

本轮只运行 pytest `--offline`、MockTransport、fake worker、隔离 SQLite 及本地 PostgreSQL/WASM（PGlite）测试。默认 12 阶段逐阶段覆盖两次失败/人工恢复，以及副作用提交后崩溃/成果认领；另用真实生产适配器组件验证 D3 内部 Compile 独立重跑和跨库启动/替换失败回滚/恢复。它们不是一次真实供应商端到端验收。

SQL 离线检查覆盖重复 DDL、service_role 写入、乱序/重复摘要、非法状态、anon/authenticated 访问拒绝、RLS 与 SECURITY INVOKER。测试依赖只安装在忽略目录，不加入产品依赖：

```text
npm install --prefix .tmp/ticker-init-sql-check --no-audit --no-fund --ignore-scripts @electric-sql/pglite@0.5.8
node scripts/check_ticker_initialization_migration.mjs
```

Compose overlay 已通过 `config --quiet` 静态解析；未启动 Docker 服务。远端迁移已确认表、RLS、RPC、权限和 migration history；真实环境的凭证、持久卷、外部服务和发布验收由后续获授权的上线流程验证。
