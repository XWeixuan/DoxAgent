# DoxAgent Supabase / SQLite 数据边界调整方案

日期：2026-06-30  
范围：仅覆盖 DoxAgent，不覆盖 DoxAtlas。  
状态：诊断与调整方案。本文件不代表已经执行迁移或重构。

## 1. 结论

当前 DoxAgent 支持把新 Supabase Free 项目作为轻量远端业务状态库继续使用，但不建议把现有所有 PostgreSQL 写入形态原样搬过去。当前 Supabase 表结构里包含大量完整 JSONB、完整 checkpoint、完整 Working Memory、完整 evidence/ref/debug bundle；这些数据在真实 eval 和 debug viewer 场景下会同时制造 Disk 与 Shared Pooler Egress 风险。

本阶段建议采用以下边界：

- Supabase 继续承担远端权威状态库、轻量结果展示库、run summary、配置/权限可见状态。
- SQLite 承担 runtime 内部状态、monitoring message bus、Stocktwits 原始流水、persistent runtime 执行记录、scheduler audit、eval/debug full bundle。
- Supabase 对大对象只保留摘要、计数、状态、引用 ID、latest pointer；完整 payload 留在 SQLite、本地文件，后续如有需要再接对象存储。
- 不移除 Supabase dashboard/debug viewer 结果展示能力，但默认展示轻量 summary，详情按需读取。
- 不做大范围重构；先做配置收敛、读写保护、payload 日志、retention、debug/eval 限流，再考虑 SQLite-backed Blackboard。

## 2. 当前存储开关

| 配置 | 当前代码支持 | 当前默认 | 建议 |
| --- | --- | --- | --- |
| `DOXAGENT_STORAGE_MODE` | `memory` / `postgres` | `memory` | 正常远端展示跑 `postgres`，eval/debug 默认不要长期高频写 Supabase；后续增加窄范围 SQLite Blackboard |
| `DOXAGENT_DATABASE_URL` | PostgreSQL/Supabase 连接串 | 空 | 新项目写库优先 Direct 或 Session Pooler 5432，不把高频写入绑到 6543 transaction pooler |
| `DOXAGENT_MONITORING_STORAGE_MODE` | `memory` / `sqlite` | `sqlite` | 保持 SQLite |
| `DOXAGENT_STOCKTWITS_STORAGE_MODE` | `memory` / `sqlite` / `postgres` | `sqlite` | 保持 SQLite；Postgres 仅一次性迁移/只读排查 |
| `DOXAGENT_STOCKTWITS_ALLOW_POSTGRES` | bool | `False` | 保持 `0`，避免误写 Supabase |
| `DOXAGENT_PERSISTENT_RUNTIME_STORAGE_MODE` | `memory` / `sqlite` | `sqlite` | 保持 SQLite |
| `DOXAGENT_RUNTIME_SCHEDULER_STORAGE_MODE` | `memory` / `sqlite` | `sqlite` | 保持 SQLite |

依据：

- `src/doxagent/settings.py`
- `.env.example`
- `src/doxagent/workflows/storage.py`
- `src/doxagent/stocktwits/repository.py`

## 3. 当前 Supabase 表结构风险概览

### 3.1 Blackboard / Workflow 相关表

迁移来源：`supabase/migrations/202605300001_blackboard_workflow_persistence.sql`

| 表 | 关键字段 | 数据类型 | 体积风险 | Egress 风险 | 建议 |
| --- | --- | --- | --- | --- | --- |
| `doxagent.blackboard_runs` | `run_id`, `ticker`, `workflow_state`, timestamps, `version` | text/timestamptz/bigint | 低 | 低 | 继续留 Supabase |
| `doxagent.belief_state_snapshots` | `documents`, `commit_ids` | jsonb | 高 | 高 | Supabase 只保留摘要/状态；完整 documents 转 SQLite/本地 |
| `doxagent.working_memory_entries` | `payload`, `evidence_refs`, `entry_json` | jsonb | 高 | 高 | 完整 `entry_json` 转 SQLite；Supabase 只留摘要 |
| `doxagent.commit_log_entries` | `evidence_refs`, `commit_json` | jsonb | 中高 | 高 | Supabase 留 patch summary/field_path/计数；完整 commit 转 SQLite |
| `doxagent.objections` | `target_json`, `objection_json`, `merged_objection_ids` | jsonb | 中高 | 中高 | Supabase 留 open/blocking 摘要；完整 objection 转 SQLite |
| `doxagent.delegations` | `blocking_scope_json`, `delegation_json` | jsonb | 中高 | 中高 | Supabase 留状态/目标/阻塞摘要；完整 delegation 转 SQLite |
| `doxagent.evidence_refs` | `retrieval_metadata`, `evidence_json` | jsonb | 中高 | 高 | Supabase 留 title/summary/source/citation；完整 evidence_json 转 SQLite |
| `doxagent.workflow_checkpoints` | `completed_nodes`, `checkpoint_json`, `is_latest` | jsonb | 极高 | 极高 | 只保留 latest summary 或最近 N 条；完整历史转 SQLite |

### 3.2 Stocktwits 相关表

迁移来源：`supabase/migrations/202606260001_stocktwits_polling_crawler.sql`

| 表 | 关键字段 | 数据类型 | 体积风险 | Egress 风险 | 建议 |
| --- | --- | --- | --- | --- | --- |
| `doxagent.stocktwits_ticker_states` | cadence、next_due、last_seen、mode、coverage | text/timestamptz/int/bool | 低 | 低 | 如需远端展示，可保留轻量状态 |
| `doxagent.stocktwits_messages` | `body`, `symbols`, `raw_payload` | text/jsonb | 极高 | 高 | 默认迁移到 SQLite；Supabase 不存 raw payload |
| `doxagent.stocktwits_message_symbols` | message/symbol mapping | text | 中 | 中 | 跟随 messages 本地化；远端只保留聚合指标 |
| `doxagent.stocktwits_crawl_runs` | crawl stats, `metadata`, errors | text/int/jsonb | 中高 | 中 | 默认 SQLite；Supabase 只保留最近状态或失败摘要 |

## 4. 当前写 Supabase 的主要代码路径

### 4.1 Workflow storage factory

| 代码路径 | 行为 | 触发条件 | 风险 |
| --- | --- | --- | --- |
| `src/doxagent/workflows/storage.py::default_workflow_storage` | 当 `DOXAGENT_STORAGE_MODE=postgres` 时启用 `PostgresBlackboardRepository` 和 `PostgresWorkflowCheckpointRepository` | 真实 eval / real smoke / debug-viewer 持久化运行 | 所有 Blackboard 和 checkpoint 写入进入 Supabase |

### 4.2 Blackboard 写入

代码路径：`src/doxagent/blackboard/postgres_repository.py`

| 方法/位置 | 写入表 | 写入数据类型 | 频率 | Payload 大小风险 | 说明 |
| --- | --- | --- | --- | --- | --- |
| `add()` -> `_insert_run()` | `blackboard_runs` | 轻量 run metadata | 每个 run 1 次 | 低 | 插入后会 `get()` 读回完整 run |
| `save()` -> `_replace_run()` | `blackboard_runs` | 轻量 run metadata | 每次保存 | 低 | 随后 `_replace_children()` 重写子表 |
| `mutate()` -> `_replace_run()` | `blackboard_runs` | 轻量 run metadata | workflow 节点/事务推进时 | 低 | 当前 mutator 已在连接外执行，事务范围较短 |
| `_replace_children()` | `working_memory_entries`, `commit_log_entries`, `objections`, `delegations`, `belief_state_snapshots` | delete + re-insert | 每次 save/mutate | 极高 | 每次保存都可能重写完整 run 子对象 |
| `_insert_belief_state()` | `belief_state_snapshots` | `documents jsonb`, `commit_ids jsonb` | 每次 replace | 高 | documents 可包含完整 stable document |
| `_insert_working_memory()` | `working_memory_entries` | `payload jsonb`, `entry_json jsonb` | 每次 replace，每条 WM | 极高 | ReAct audit、tool outputs、context summary 都可能进入 |
| `_insert_commit_log()` | `commit_log_entries` | `commit_json jsonb` | 每次 replace，每条 commit | 高 | patch/evidence refs 完整写入 |
| `_insert_objections()` | `objections` | `target_json`, `objection_json` | 每次 replace，每条 objection | 中高 | blocking/debug 信息完整写入 |
| `_insert_delegations()` | `delegations` | `blocking_scope_json`, `delegation_json` | 每次 replace，每条 delegation | 中高 | 阻塞范围与任务上下文写入 |
| `_upsert_evidence_refs()` | `evidence_refs` | `retrieval_metadata`, `evidence_json` | 每次 replace，按 evidence 去重 upsert | 高 | evidence_json 可能重复大对象 |

写入特征：

- 不是 append-only 单表，而是“读 full run -> Python 变更 -> 删除子表 -> 重插完整子状态”。
- 对小 run 可接受；对 eval/retry/debug loop 会快速放大写入量和 WAL/索引体积。
- `save()` / `mutate()` 后还会 `get()` 读回完整状态，造成写后读 Egress。

### 4.3 Workflow checkpoint 写入

代码路径：`src/doxagent/workflows/checkpoint_repository.py`

| 方法/位置 | 写入表 | 写入数据类型 | 频率 | Payload 大小风险 | 说明 |
| --- | --- | --- | --- | --- | --- |
| `PostgresWorkflowCheckpointRepository.save_checkpoint()` | `workflow_checkpoints` | `completed_nodes jsonb`, `checkpoint_json jsonb` | 每次 checkpoint save | 极高 | 每条 checkpoint 都保存完整 `WorkflowCheckpoint` |
| `save_checkpoint()` latest update | `workflow_checkpoints` | `is_latest=false` update | 每次 latest checkpoint | 低 | 本身低风险，但会维护历史 |

写入特征：

- `workflow_checkpoints` 已有 `is_latest`，但表仍保留完整历史。
- `checkpoint_json` 包含 pending patches、last error、workflow state、可能的大 context/audit，是 Disk 与 Egress 双重高风险表。

### 4.4 Stocktwits PostgreSQL 写入

代码路径：`src/doxagent/stocktwits/repository.py`

默认情况下，`DOXAGENT_STOCKTWITS_STORAGE_MODE=sqlite` 且 `DOXAGENT_STOCKTWITS_ALLOW_POSTGRES=False`，Postgres 写入不会启用。但代码仍存在，误开后风险很高。

| 方法/位置 | 写入表 | 写入数据类型 | 频率 | Payload 大小风险 | 说明 |
| --- | --- | --- | --- | --- | --- |
| `ensure_schema()` | stocktwits 4 表 | DDL | 手动/初始化 | 低 | 仅建表 |
| `upsert_ticker_state()` | `stocktwits_ticker_states` | 轻量状态 | 每 symbol 调度/抓取后 | 低 | 使用 `returning *`，可改为显式列或不返回 |
| `ingest_messages()` | `stocktwits_messages` | body、symbols、`raw_payload jsonb` | 每 crawl，每 message | 极高 | 原始消息流不应默认进入 Supabase |
| `ingest_messages()` | `stocktwits_message_symbols` | message-symbol mapping | 每 message * symbol | 中高 | 高频 mapping |
| `record_crawl_run()` | `stocktwits_crawl_runs` | crawl stats、`metadata jsonb` | 每 crawl | 中 | 使用 `returning *` |

### 4.5 Eval / smoke 触发写库

| 代码路径 | 写库触发 | 风险 |
| --- | --- | --- |
| `eval/run_blackboard_eval_once.py` | 真实 PostgreSQL-backed Blackboard eval | 高 |
| `eval/run_document1_document2_smoke.py` | 要求 `DOXAGENT_STORAGE_MODE=postgres` | 高 |
| `eval/run_document2_expectation_units_smoke.py` | 要求 `DOXAGENT_STORAGE_MODE=postgres` | 高 |
| `eval/run_document3_smoke.py` | 要求 `DOXAGENT_STORAGE_MODE=postgres` | 高 |
| `tests/test_phase18_supabase_persistence_smoke.py` | Supabase persistence smoke | 中，高频运行时高 |

这些脚本通常会在运行后立即调用 `DebugRunQueryService.brief_state()` 或 `export_brief_state()`，因此同时触发高 Egress 读取。

## 5. 当前读 Supabase 的主要代码路径与 Egress 风险

### 5.1 Debug viewer API / 页面

代码路径：

- `src/doxagent/debug_viewer/server.py`
- `src/doxagent/debug_viewer/query.py`

| API 路径 | 前端触发位置 | 读取表 | Payload | Egress 风险 | 建议 |
| --- | --- | --- | --- | --- | --- |
| `GET /api/config` | 页面加载 | 无 DB 或轻量状态 | 小 | 低 | 保留 |
| `GET /api/runs?limit=50&ticker=...` | `loadRuns()` | `blackboard_runs` | run metadata | 低 | 保留，默认 summary list |
| `GET /api/runs/{run_id}/brief-state` | 选中 run 或 brief tab | `blackboard_runs`, `belief_state_snapshots`, `working_memory_entries`, `commit_log_entries`, `objections`, `delegations`, `workflow_checkpoints`, `evidence_refs` | full bundle 派生 view | 极高 | 改为按需 full；默认 summary |
| `GET /api/runs/{run_id}/agent-metrics` | metrics tab | 同上，经 `load_bundle()` | full bundle 派生 metrics | 高 | metrics 应优先读取预计算摘要 |

当前页面行为：

- 进入页面后 `loadRuns()` 会选最新 run。
- 若默认 tab 是 brief，会立即请求 `/brief-state`。
- 切换 metrics 会再次读取 full bundle。
- 这意味着“只是打开 debug viewer 看最新状态”也可能拉完整 JSON。

### 5.2 DebugRunQueryService 读取

代码路径：`src/doxagent/debug_viewer/query.py`

| 方法 | 读取内容 | Egress 风险 | 说明 |
| --- | --- | --- | --- |
| `list_runs()` | `blackboard_runs` metadata | 低 | 有 `limit`，默认 50 |
| `load_bundle()` | full run bundle | 极高 | 核心高 Egress 路径 |
| `_load_belief_state()` | `documents jsonb`, `commit_ids` | 高 | 返回完整 documents |
| `_load_json_column()` | `entry_json`, `commit_json`, `objection_json`, `delegation_json` | 极高 | `fetchall()` 全部 run-scoped JSON |
| `_load_checkpoints()` | 所有 `checkpoint_json` | 极高 | 当前按 run 读取全部 checkpoint 历史 |
| `_load_evidence_refs()` | `evidence_json` | 高 | 根据 bundle 收集 evidence ids 后批量 hydrate |

### 5.3 Eval / diagnostic 读取

| 路径 | 行为 | Egress 风险 | 说明 |
| --- | --- | --- | --- |
| `eval/export_brief_state.py` | `load_bundle()` 后导出 `brief_state`, `agent_metrics`, `stable_documents`, `workflow_checkpoints`, `working_memory`, `commit_log`, `objections`, `delegations`, `evidence_refs`, `eval_index` | 极高 | 本地已有导出文件最高约 19.5MB |
| `eval/run_document1_document2_smoke.py` | resume 后调用 `brief_state()` | 高 | 每次 smoke 都读 full-ish view |
| `eval/run_document2_expectation_units_smoke.py` | resume 后调用 `brief_state()`，可选 full export | 高到极高 | loop 中重复执行时会放大 |
| `eval/run_document3_smoke.py` | resume 后调用 `brief_state()`，可选 full export | 高到极高 | 同上 |
| `eval/*_records.md` 中的远端 validator 调用 | 经 debug viewer/API 重建 Brief State | 高 | 多轮排查时反复拉 full bundle |

### 5.4 Runtime repository 读取

| 代码路径 | 读取表 | Egress 风险 | 说明 |
| --- | --- | --- | --- |
| `PostgresBlackboardRepository.get()` | full Blackboard run | 高 | `save()`/`mutate()` 后会读回完整 run |
| `PostgresBlackboardRepository.list_by_ticker()` | `blackboard_runs` ids + full `get()` | 高 | 多 run 时风险高 |
| `PostgresBlackboardRepository.list_unresolved_objections()` | `objections.objection_json` | 中 | run-scoped |
| `PostgresBlackboardRepository.list_blocking_delegations()` | `delegations.delegation_json` | 中 | run-scoped |
| `PostgresBlackboardRepository.summary_counts()` | count queries | 低 | 应复用为 summary API 基础 |
| `PostgresWorkflowCheckpointRepository.list_for_run()` | all or latest checkpoint rows | 中到高 | `latest_only=True` 低；全量历史高 |
| `PostgresStocktwitsRepository.get/due/recent` | ticker states / crawl runs | 低到中 | 默认禁用 Postgres |

### 5.5 Realtime / PostgREST

仓库内未发现 DoxAgent 使用 Supabase Realtime、`channel()`、`postgres_changes`、Supabase JS `select("*")` 的前端路径。当前 Egress 主风险来自 psycopg 直连/Pooler 读取 full JSON，而不是 PostgREST 页面 `select *`。

## 6. 数据分类

### 6.1 应继续留在 Supabase

| 数据 | 表/建议表 | 原因 | 备注 |
| --- | --- | --- | --- |
| Run 基础元数据 | `doxagent.blackboard_runs` | 远端权威状态、dashboard 列表、跨服务可见 | 保留现有表 |
| Run 最新状态摘要 | 可扩展 `blackboard_runs` 或新增小表 `doxagent.run_summaries` | dashboard 默认展示、避免 full bundle | 只含状态、计数、latest checkpoint id、latest error code |
| 最新 checkpoint 指针 | `workflow_checkpoints` latest row 的轻量字段 | 恢复/展示需要 | `checkpoint_json` 不应长期全量保留 |
| Objection/delegation 阻塞摘要 | `objections`, `delegations` 的轻量列或 summary table | dashboard 显示 blocking reason | 完整 JSON 本地 |
| Evidence 可引用摘要 | `evidence_refs` 的 source/title/summary/citation_scope | 前端/报告引用 | `evidence_json` 本地或按需存储 |
| Stocktwits ticker 状态摘要 | `stocktwits_ticker_states` 可选 | 如果远端 dashboard 需要轮询状态 | 不包含 raw messages |

### 6.2 应迁移到 SQLite

| 数据 | 当前表/现有 SQLite 能力 | 迁移建议 |
| --- | --- | --- |
| Stocktwits raw messages | `stocktwits_messages.raw_payload`; 已有 `SQLiteStocktwitsRepository` | 默认 SQLite；Supabase 不存 raw payload |
| Stocktwits message-symbol mapping | `stocktwits_message_symbols`; 已有 SQLite 同构表 | 跟随 raw messages 本地化 |
| Stocktwits crawl run 详细 metadata | `stocktwits_crawl_runs.metadata`; 已有 SQLite 同构表 | SQLite 保留完整，Supabase 只留最近状态 |
| Monitoring raw messages | `SQLiteMonitoringRepository.monitoring_raw_messages` | 已经 SQLite，保持 |
| Monitoring standard/event stream | `monitoring_standard_messages`, `monitoring_event_stream` | 已经 SQLite，保持 |
| Persistent runtime executions | `persistent_runtime_executions` | 已经 SQLite，保持 |
| Persistent runtime archive/queue/known events | `persistent_archive`, `persistent_ingest_queue`, `persistent_known_events` | 已经 SQLite，保持 |
| Runtime scheduler audit/events/refresh requests | `runtime_scheduler_*` | 已经 SQLite，保持 |
| Full workflow checkpoint history | `workflow_checkpoints.checkpoint_json` | 新增/复用本地 SQLite workflow store 后迁移 |
| Full Blackboard run bundle | full children JSON | 新增窄范围 SQLite Blackboard repository 后迁移 |
| Eval full Brief State export | `eval/brief_state_exports/*.json` | 保持本地文件；不要反复从 Supabase 重建 |

### 6.3 应在 Supabase 只保留摘要

| 当前数据 | Supabase 摘要字段建议 | 完整数据位置 |
| --- | --- | --- |
| `belief_state_snapshots.documents` | stable document types、document status、updated_at、summary hash、local/object ref | SQLite / local JSON |
| `working_memory_entries.entry_json` | entry_id、author_agent、content_type、created_at、payload_hash、short summary、evidence ids | SQLite |
| `commit_log_entries.commit_json` | commit_id、document_type、object_id、field_path、trigger_reason、created_at、patch hash | SQLite |
| `objections.objection_json` | objection_id、status、severity、taxonomy、target_path、field_path、short reason | SQLite |
| `delegations.delegation_json` | delegation_id、requester、target、status、field_path、blocking flag | SQLite |
| `evidence_refs.evidence_json` | evidence_id、source_type、source_id、title、summary、confidence、citation_scope | SQLite / object storage later |
| `workflow_checkpoints.checkpoint_json` | checkpoint_id、run_id、status、next_node、completed_nodes、is_latest、error_code、counts | SQLite |
| `stocktwits_messages.raw_payload` | message_id、symbol、created_at、sentiment、body preview/hash | SQLite |

### 6.4 应增加 retention / TTL / 最近 N 条限制

| 表 | Retention 策略 | 优先级 |
| --- | --- | --- |
| `workflow_checkpoints` | 每个 run 只保留 latest 或最近 3 条 full checkpoint；老 checkpoint 删除 `checkpoint_json` 或整行转 SQLite | P0 |
| `working_memory_entries` | Supabase 仅保留当前 run summary；full entry 默认不进 Supabase，历史按 run 最近 N 个保留 | P0 |
| `belief_state_snapshots` | 每个 run 唯一，但 documents 只保留摘要；full documents 另存 | P0 |
| `evidence_refs` | 未被近期 run summary 引用的 refs 定期清理；保留 title/summary 更久，full JSON 本地 | P1 |
| `stocktwits_messages` | 默认不写 Supabase；若临时写，7 天或每 symbol 最近 N 条 | P0 |
| `stocktwits_crawl_runs` | 默认不写 Supabase；若临时写，每 symbol 最近 100 条或 7 天 | P1 |
| `runtime_scheduler_audit_events` | SQLite 内可保留最近 N=1000 或 14 天 | P2 |
| `persistent_execution_exceptions` | SQLite 内按 30 天或最近 N 条清理 | P2 |

候选 SQL 示例，仅作为后续 migration 草案，不在本阶段直接执行：

```sql
with ranked as (
  select
    checkpoint_id,
    row_number() over (
      partition by run_id
      order by is_latest desc, created_at desc
    ) as rn
  from doxagent.workflow_checkpoints
)
delete from doxagent.workflow_checkpoints wc
using ranked r
where wc.checkpoint_id = r.checkpoint_id
  and r.rn > 3;
```

## 7. 最小改动计划

### P0：立即收敛高风险路径

1. 新 Supabase 项目只承接轻量 schema 和 summary，不导入旧数据。
2. `.env` / 部署配置保持：
   - `DOXAGENT_MONITORING_STORAGE_MODE=sqlite`
   - `DOXAGENT_STOCKTWITS_STORAGE_MODE=sqlite`
   - `DOXAGENT_STOCKTWITS_ALLOW_POSTGRES=0`
   - `DOXAGENT_PERSISTENT_RUNTIME_STORAGE_MODE=sqlite`
   - `DOXAGENT_RUNTIME_SCHEDULER_STORAGE_MODE=sqlite`
3. `DOXAGENT_DATABASE_URL` 不用于高频 raw/debug 写入；远端写库优先 Direct 或 Session Pooler 5432。
4. 暂停把 Stocktwits Postgres repository 用作正常 runtime storage；仅保留为一次性迁移/排查工具。
5. Debug viewer 默认加载 `/api/runs` summary，不自动拉 `/brief-state` full bundle。
6. Eval/smoke 默认不要自动导出 full Brief State；full export 必须显式开关，并打印预计读取体积。

### P1：加轻量保护与可观测性

1. 在 `src/doxagent/postgres.py` 增加 Supabase 写失败分类：
   - read-only transaction
   - No space left on device
   - `PGRST000`
   - `ECONNREFUSED`
   - `ECHECKOUTTIMEOUT`
   - pooler EOF / server closed connection
2. 对上述错误增加高频 retry 熔断：
   - 当前 run 内同类错误连续出现后停止继续写 Supabase。
   - 错误落本地 `.tmp/supabase_write_failures/*.jsonl`。
3. 错误日志必须包含：
   - 原始 SQLSTATE / pgcode
   - endpoint 类型：direct / session pooler 5432 / transaction pooler 6543 / unknown
   - `transaction_read_only` / `default_transaction_read_only` 可用时的状态
   - table name
   - operation name
   - payload 估算大小
   - stack trace
4. 在 `PostgresBlackboardRepository` 和 `PostgresWorkflowCheckpointRepository` 写入前增加 payload size 日志：
   - `run_id`
   - table
   - method
   - estimated bytes
   - item count
5. 对 `PostgresStocktwitsRepository` 的 `returning *` 改为显式列或不返回 full row，虽然默认禁用，但避免误开后产生额外 Egress。

### P2：摘要化 Supabase 展示面

1. 增加轻量 summary 生成函数，复用现有 `summary_counts()` 和 latest checkpoint。
2. Debug viewer 增加 summary API：
   - `GET /api/runs/{run_id}/summary`
   - 默认 UI 使用 summary。
   - `/brief-state` 变成显式详情操作。
3. Supabase 只保留：
   - run metadata
   - latest status
   - node progress
   - counts
   - latest error code/message preview
   - object/local ref
4. `agent-metrics` 优先从预计算 summary/metrics 读取，不默认 `load_bundle()`。

### P3：本地化 full Blackboard / checkpoint

这一阶段会触碰 workflow storage，因此放在 P0/P1/P2 之后，保持最小可控：

1. 新增窄范围 SQLite repository，接口对齐现有：
   - `SQLiteBlackboardRepository`
   - `SQLiteWorkflowCheckpointRepository`
2. `DOXAGENT_STORAGE_MODE` 增加 `sqlite`，但默认仍不改变正常业务链路。
3. Eval/runtime/debug 默认使用 SQLite full storage。
4. Supabase 只同步最终摘要和 latest run status。
5. 保持 Postgres mode 可用，用于需要远端展示/跨服务共享的正常链路。

## 8. 不做的事情

- 不把 Supabase 从结果展示、debug viewer、远端状态可见性中移除。
- 不把 Stocktwits raw stream、完整 trace、完整 checkpoint、完整 Brief State 默认写回 Supabase。
- 不引入 Kafka、Redis、对象存储、多数据库同步框架等复杂新架构。
- 不在当前阶段重写 workflow 业务逻辑。
- 不把 6543 transaction pooler 当作高频持久化写库路径。

## 9. 新 Free Supabase 项目迁移前检查清单

1. 在新项目 SQL Editor 应用现有 migration 前，确认只创建结构，不导入旧数据。
2. 应用 `202605300001_blackboard_workflow_persistence.sql` 和必要后续 migration。
3. 应用 Stocktwits migration 时，确认正常配置仍不会写 Postgres Stocktwits 表。
4. 用只读 SQL 检查：
   - `transaction_read_only`
   - `default_transaction_read_only`
   - `pg_is_in_recovery()`
   - `current_user`
5. 配置连接：
   - dashboard/低频读写：Direct 或 Session Pooler 5432
   - 避免高频 runtime/debug 写 6543
6. 先跑最小 smoke：
   - 只创建一个轻量 run。
   - 不打开 full Brief State export。
   - 检查 Supabase 表体积和 Egress。
7. 开启 payload size 日志后，再进行真实 eval。

## 10. 优先级排序

| 优先级 | 工作 | 影响 | 风险 |
| --- | --- | --- | --- |
| P0 | 保持 Stocktwits/monitoring/runtime/scheduler SQLite，禁用 Stocktwits Postgres | 立即降低 Disk | 低 |
| P0 | Debug viewer 默认 summary，不自动 full bundle | 立即降低 Egress | 低到中 |
| P0 | checkpoint retention：每 run 最近 N 条 | 立即降低 Disk | 中 |
| P1 | Supabase 写失败熔断 + 本地错误记录 | 防止 disk full/read-only 后继续打爆 | 中 |
| P1 | payload size 日志 | 让下一次膨胀可定位 | 低 |
| P2 | run summary API/table | 让 dashboard 轻量化 | 中 |
| P3 | SQLite Blackboard/checkpoint repository | eval/debug full state 本地化 | 中高，需测试 |

## 11. 验收标准

1. 打开 debug viewer 默认只产生轻量 `/api/runs` 和 summary 请求。
2. 一次 real eval 不再把 full checkpoint history 和 full Brief State 反复写入/读取 Supabase。
3. Stocktwits 正常 polling 不向 Supabase 写 `stocktwits_messages.raw_payload`。
4. Supabase 写失败时不会无限高频 retry；本地有结构化错误记录。
5. 日志能回答每次大 payload 写入的表名、路径、大小、run_id。
6. 新 Free Supabase 项目中 Supabase 仍能展示 run 状态、latest progress、错误摘要和最终结果摘要。

