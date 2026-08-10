# Codex D1 v2 hybrid storage migration acceptance

日期：2026-08-10  
范围：本地代码、Docker、SQLite 与当前 Supabase 项目；不含远端服务器同步。

## 结论

Hybrid 存储与最终数据库迁移已经落地。高文本 evidence 只写本地 SQLite v2；运行控制状态、发布摘要和发布正文索引写 Supabase；小于等于 2 MiB 的已发布正文写 PostgreSQL TEXT，大于 2 MiB 的正文写私有 Supabase Storage。Dashboard 列表/详情不读取正文，正文只允许按 artifact ID 精确读取并使用 SHA256 ETag。

数据库、真实功能与最终镜像验收均已通过。Docker Desktop 重启后访问 Docker Hub manifest 仍出现 EOF，因此最终收尾采用可审计的离线派生构建：以已经通过 hybrid、Storage 和重启验收的本地 dashboard 镜像为基底，只覆盖最后变更的 `repository.py`。当前健康镜像为 `sha256:03b7b854104c2db86385c41166db54e36996308e1b3fe801997dc065f6bf0701`，镜像内已确认包含“PublishedDocument 与 ArtifactRef 的 run/kind 一致性”防御性守卫。

## 已落地结构

- SQLite `PRAGMA user_version=2`，新增 `codex_local_sources` 与 `codex_local_citation_manifests`，继续使用 WAL 和 Docker named volume。
- Supabase private `doxagent` schema 只创建八张表：run registry、thread registry、attempts、checkpoints、artifact metadata、events、bundle summary、published documents。
- Supabase 不存在 `codex_sources` 或 `codex_citation_manifests`。
- 八张表均启用并强制 RLS；`anon/authenticated` 无授权，`service_role` 逐表只有 `SELECT/INSERT/UPDATE`，没有 `DELETE`。
- `codex-published-documents` Storage bucket 为 private，单对象上限 50 MiB；后端 secret 只持久化在 Windows 用户环境并只注入 dashboard，不传 worker 或前端。

## 迁移与回填证据

- Supabase migration `20260808210847` 已真实应用并记录；旧的远端 migration history 未重写。
- SQLite 迁移前备份：`.tmp/codex-runtime.sqlite3.pre-v2-20260810.bak`。
- 当前容器 SQLite：17 条 source、34 份 citation manifest；旧 generic evidence 仍保留 10 条 source 与 17 份 citation，满足两轮真实 smoke 保留要求。
- 历史 run `codex-smoke-20260809T183538Z`：5 threads、10 attempts、28 artifacts、10 events、9 published documents。
- 历史 run `codex-smoke-20260809T185650Z`：5 threads、9 attempts、29 artifacts、10 events、10 published documents。
- sources/citations 未回填到 Supabase；workspace 正文按 SHA256 与 size 校验后上传。

## 最终真实 smoke

成功运行：`codex-hybrid-smoke-20260810T0915Z`。

- `status=published`，12 个节点完成，0 个失败节点。
- 9 attempts，含 C2 注入失败后重试成功。
- Supabase：5 threads、9 attempts、1 checkpoint、29 artifacts、10 events、1 bundle、10 published documents。
- event sequence 为 0..9，10 个 distinct sequence；registry `latest_event_sequence=9`。
- 10 份正文 SHA256 与字节数全部一致；最终 Document 1 为 12,295 bytes。
- 6 条 citation entries，4 条 resolved，2 条 O4-A unresolved warning；属于后续研究质量验收，不是链路故障。
- dashboard 重启后可从 Supabase 读取 run/detail/event/body；未发布 artifact 精确读取返回不可见。

## Egress 与 SQL

- Pydantic、Repository、数据库 CHECK 三层均有限额；所有远端 list 使用 LIMIT，run list 使用 keyset cursor，event 使用 `sequence > after`，无 `SELECT *` 或 Python 端全量 after 过滤。
- 本轮本地 payload audit 记录 61 条 Codex 查询，最大估算响应 12,653 bytes，低于 1 MiB 非正文硬上限。
- 成功 smoke 最大 event payload 51 bytes、attempt 总估算 121 bytes、bundle JSONB 合计 5,454 bytes。
- `pg_stat_statements` 已验证结构化 insert/update 及精确 artifact/document/event 查询；Supabase `db lint --schema doxagent` 返回 0 issue。
- Supabase 官方当前提供 Usage 页面、按项目 egress 分解与平台 limit warnings，但 Management API 没有可配置自定义 egress threshold 的公开端点。因此没有伪造“已设置自定义告警”；需要在组织 Usage 页面按账户策略确认通知/Spend Cap。

## Storage 验收

- 使用真实后端 secret 对 private bucket 上传并下载 2,097,153-byte 文本，SHA256 与大小一致。
- 临时对象随后通过 Storage API 删除，复读返回不存在；未留下测试对象。
- runtime 对 >2 MiB 正文自动写 Storage 并只在数据库保存 `storage_path`；读取后再次校验 size/SHA256。

## 自动化验证

- Ruff：通过。
- `tests/test_codex_runtime_v2.py` 与 `tests/test_codex_document1_workflow.py`：18 passed，3 个第三方 warning。
- Supabase schema lint：0 issue。
- 当前 dashboard 容器健康；最终镜像包含 hybrid、Storage、未发布 artifact 隔离与 run/kind 一致性守卫。重启读取验收返回 published Document 1 正文 12,295 bytes、增量 event sequence 9，并阻断未发布 artifact。

## 回滚

```dotenv
DOXAGENT_CODEX_RUNTIME_STORAGE_MODE=sqlite
DOXAGENT_CODEX_REMOTE_RUNTIME_STORAGE_ENABLED=false
```

回滚后重建 dashboard。小型运行状态仍有 SQLite mirror，证据与旧 generic records 未删除。
