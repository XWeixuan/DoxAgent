# DoxAgent Supabase Egress 根因排查与优化方案

日期：2026-07-04  
范围：仅 DoxAgent。未检查或修改 DoxAtlas。

## 结论摘要

当前 Supabase egress 爆量的主因不是 Storage、Realtime、Edge Function、PostgREST 前端直连，也不是 Stocktwits raw 数据继续写入 Supabase。截图显示 2026-07-04 当天 egress 100% 来自 Shared Pooler，Supabase MCP 与远端容器配置也确认 DoxAgent 后端服务仍通过 Session Pooler 5432 访问 Supabase。

根因是后端读放大：

1. `dashboard` 和 `runtime-scheduler` 容器的 `DOXAGENT_STORAGE_MODE=postgres`，Blackboard 业务状态仍走 Supabase Session Pooler 5432。
2. Dashboard Research/Strategy 页面存在 60 秒轮询；部分页面在同一轮会并发调用 `documents/current`、`document*/versions`、`known-events`、`policies`。
3. 这些 API 表面返回的是 dashboard 摘要或分页列表，但服务端仍通过 `BlackboardService.list_runs_by_ticker()` / `get_run()` 拉取完整 Blackboard run。
4. `PostgresBlackboardRepository._get_run()` 每次都会读取 `belief_state_snapshots.documents`、全部 `working_memory_entries.entry_json`、全部 `commit_log_entries.commit_json`、全部 `objections.objection_json`、全部 `delegations.delegation_json`。
5. Supabase DB 当前只有约 76MB，但同一批 JSON 被高频重复从 Pooler 传回应用，导致每日 GB 级 egress。

一句话：Supabase 当前没有被“写爆”，而是被 Dashboard 文档/策略读取路径反复拉取完整 Blackboard payload 读爆。

## 证据

### Supabase 账单侧

用户截图显示：

| 指标 | 值 | 含义 |
| --- | ---: | --- |
| Monthly Egress | 15.544GB | 已超过 Free 10GB/月 |
| 2026-07-04 单日 egress | 6.213GB | 当天继续快速增长 |
| Shared Pooler Egress | 100% | 流量来自 Postgres pooler，不是 Storage/Realtime/Edge |
| Cached Egress | 0GB | 不是 CDN/缓存资源 |
| Database Size | 0.076GB | DB 体积很小，不支持“当天写入膨胀”解释 |
| Realtime / Storage / Edge Function | 0 | 排除这些服务作为主因 |

### Supabase 表体积

MCP `doxagent` schema 当前 13 张表。`stocktwits_*` 远端表均为 0 行，说明当前 Stocktwits 高体积数据没有继续进 Supabase。

表体积排序：

| 表 | 行数 | total size | 说明 |
| --- | ---: | ---: | --- |
| `workflow_checkpoints` | 36 | 28MB | 最大磁盘对象，checkpoint JSON 平均约 808KB |
| `working_memory_entries` | 287 | 13MB | 最大读取 egress 来源，entry JSON 平均约 34KB |
| `objections` | 302 | 2.2MB | 行数多但单行小 |
| `commit_log_entries` | 33 | 1.8MB | commit JSON 平均约 40KB |
| `belief_state_snapshots` | 12 | 1.0MB | documents 平均约 67KB |
| `run_summaries` | 6 | 104KB | 轻量摘要表，应该成为 dashboard 默认读源 |

JSON payload 体积：

| 表/列 | 行数 | JSON 总量 | 平均 | 最大 |
| --- | ---: | ---: | ---: | ---: |
| `workflow_checkpoints.checkpoint_json` | 36 | 28MB | 808KB | 1.58MB |
| `working_memory_entries.entry_json` | 287 | 9.6MB | 34KB | 160KB |
| `commit_log_entries.commit_json` | 33 | 1.3MB | 40KB | 61KB |
| `belief_state_snapshots.documents` | 12 | 789KB | 67KB | 149KB |
| `objections.objection_json` | 302 | 697KB | 2.3KB | 5.7KB |
| `run_summaries.full_payload_ref` | 6 | 1008B | 168B | 168B |

最近 10 天写入分布：

| 日期 | 主要新增 |
| --- | --- |
| 2026-06-30 | 6 个 run，约 17MB checkpoint JSON，5.5MB working memory |
| 2026-07-03 | 6 个 run，约 11MB checkpoint JSON，4.1MB working memory |
| 2026-07-04 | 未观察到同量级新写入 |

这说明 2026-07-04 的 6GB egress 不是当天新写入造成，而是已有数据重复读取造成。

### pg_stat_statements 读放大证据

`pg_stat_statements` 显示 `_get_run()` 相关 SELECT 调用已累计约 14.8k 次。`list_by_ticker` 只有约 2.1k 次，但每次会展开成多个 `_get_run()`，所以后续子表 SELECT 被放大。

按当前平均 row size 粗估已返回 payload 组成：

| 读取对象 | SELECT calls | returned rows | 估算 payload | 说明 |
| --- | ---: | ---: | ---: | --- |
| `working_memory_entries.entry_json` | 14,822 | 353,218 | 约 11GB | 最大 egress 来源 |
| `commit_log_entries.commit_json` | 14,815 | 34,956 | 约 1.35GB | 次要但稳定 |
| `belief_state_snapshots.documents` | 14,836 | 14,867 | 约 955MB | 文档读取必经 |
| `objections.objection_json` | 14,824 | 386,149 | 约 871MB | 行数放大 |
| `workflow_checkpoints.checkpoint_json` | 91 | 749 | 约 577MB | 主要来自 debug/workflow checkpoint 查询，不是当前最高频 dashboard 文档页 |

注意：这是基于当前平均行大小和 `pg_stat_statements` rows 的估算，不能当作精确账单，但组成比例与 15.544GB 月 egress 高度吻合。

短窗口复查：2026-07-04 16:04:27 UTC 到 16:05:44 UTC 的 60 秒窗口内，上述关键 SELECT 计数没有增长，说明当前不是无头服务每秒持续读取；高峰更像是 Dashboard 页面打开后的轮询窗口，以及历史 workflow/dashboard 读回叠加。

## 远端服务与本地 SQLite 状态

远端 `docker compose ps`：

| 服务 | 状态 | 说明 |
| --- | --- | --- |
| `dashboard` | running, healthy | 对外 dashboard/API 服务 |
| `runtime-scheduler` | running, healthy | 15 秒 loop，处理 ticker runtime |

远端容器脱敏配置：

| 服务 | `DOXAGENT_STORAGE_MODE` | DB endpoint | Dashboard mode | 本地状态 |
| --- | --- | --- | --- | --- |
| `dashboard` | `postgres` | Session Pooler 5432 | real/supabase auth | monitoring/stocktwits SQLite |
| `runtime-scheduler` | `postgres` | Session Pooler 5432 | real | monitoring/stocktwits SQLite |

这说明 Supabase 连接是新的 Session Pooler 5432，不是 6543 transaction pooler；但 Blackboard 仍是 Supabase 远端权威库。

远端 SQLite 文件：

| 文件 | 大小 | 主要行数 | 判断 |
| --- | ---: | --- | --- |
| `/app/.tmp/runtime_scheduler.sqlite3` | 114KB | states 2, audit_events 77 | scheduler 状态已本地化 |
| `/app/.tmp/monitoring_message_bus.sqlite3` | 45MB | raw/standard/event 各 5731 | message bus 高频数据已本地化 |
| `/app/.tmp/persistent_runtime_execution.sqlite3` | 100KB | runtime/exceptions/trading 均 0 | runtime 执行尚未形成高体积 |
| `/app/.tmp/stocktwits_polling.sqlite3` | 2.8MB | messages 425, crawl_runs 316 | Stocktwits 已本地化 |

所以当前 egress 不是 SQLite 分工失败导致 raw monitoring 数据进入 Supabase，而是 Blackboard/文档读取仍使用 Supabase 且读取方式过重。

## 当前读写路径

### Supabase 写入路径

| 代码路径 | 表 | 数据类型 | 频率 | payload 风险 | 结论 |
| --- | --- | --- | --- | --- | --- |
| `src/doxagent/workflows/storage.py` -> `PostgresBlackboardRepository` | `blackboard_runs`, `belief_state_snapshots`, `working_memory_entries`, `commit_log_entries`, `objections`, `delegations`, `evidence_refs` | Blackboard run、文档状态、工作记忆、patch、objection | 初始化/工作流执行阶段高频 | 高，尤其 working memory 和 full entry JSON | Supabase 仍承担远端权威业务库职责，但不应被 dashboard full read |
| `src/doxagent/workflows/checkpoint_repository.py` -> `PostgresWorkflowCheckpointRepository.save_checkpoint()` | `workflow_checkpoints`, `run_summaries` | workflow checkpoint、摘要 | 每个工作流节点/checkpoint | 极高，checkpoint 平均 808KB | checkpoint 需要 retention，本阶段 dashboard 不应默认读 |
| `src/doxagent/stocktwits/repository.py` -> `PostgresStocktwitsRepository` | `stocktwits_*` | raw social/crawl state | 理论高频 | 极高 | 远端当前未启用，Supabase 表 0 行 |
| `src/doxagent/dashboard_api/real_service.py` 操作类接口 | scheduler audit/local config，可能触发 document activation/start | 大多 SQLite，start/force refresh 可能触发 workflow/Supabase | 用户操作低频 | 中 | 正常业务链路保留 |

### Supabase 读取路径

| API/模块 | 前端页面 | 后端代码路径 | 实际 Supabase 读取 | Egress 风险 |
| --- | --- | --- | --- | --- |
| `GET /api/dashboard/v1/tickers/{ticker}/documents/current` | Research/Strategy | `documents_current()` -> `_current_document_run()` | `_blackboard_runs(ticker, limit=100)` 后又 `_blackboard_run_by_id()` | 极高 |
| `GET /api/dashboard/v1/tickers/{ticker}/documents/{type}/versions` | Research/Strategy | `document_versions()` -> `_versioned_documents()` | `_blackboard_runs(ticker, limit=100)`，每个 run 完整 `_get_run()` | 极高 |
| `GET /api/dashboard/v1/tickers/{ticker}/known-events` | Strategy | `known_events()` -> `_known_event_items()` | 当前 run 完整 `_get_run()`，再叠加本地 runtime known events | 高 |
| `GET /api/dashboard/v1/tickers/{ticker}/policies` | Strategy | `policies()` -> `_policy_items()` | 当前 run 完整 `_get_run()` | 高 |
| `GET /api/dashboard/v1/overview`, `/tickers` | Overview | `_states()` + `_ticker_card()` | 当前主要读 SQLite message/runtime 状态 | 低到中，轮询频率高但 payload 本地 |
| `GET /api/dashboard/v1/tickers/{ticker}/message-bus/*` | Message Bus | `_raw_messages()`, `_messages()`, `_events()` | 读本地 `monitoring_message_bus.sqlite3` | 对 Supabase 低，对 HTTP egress 中 |
| `GET /api/dashboard/v1/tickers/{ticker}/runtime/*` | Runtime | `_runtime_context()`/runtime repo | 主要读本地 SQLite；Paper Trading 执行时可能 `document_provider.latest()` 读 Blackboard | 中，取决于 runtime 是否真的执行 |
| Debug Viewer | 已移除 | n/a | 不再提供常驻 full run/checkpoint API | 已消除常驻服务风险 |

### 前端轮询路径

`frontend/dashboard/src/hooks/use-dashboard-query.ts` 会首次加载并按 `intervalMs` 重载。

高风险页面：

| 页面 | 轮询 | API |
| --- | ---: | --- |
| `frontend/dashboard/src/pages/strategy.tsx` | 60s | `documents/current`, `document3/versions`, `known-events`, `policies` |
| `frontend/dashboard/src/pages/research.tsx` | 60s | `documents/current`, `document1/versions`, `document2/versions` |
| `frontend/dashboard/src/pages/message-bus.tsx` | 8s/15s/60s | 本地 SQLite 为主 |
| `frontend/dashboard/src/pages/runtime.tsx` | 8s/15s | 本地 SQLite 为主，但 detail/context 可能间接读文档 |
| `frontend/dashboard/src/pages/overview.tsx` | 5s/7s | 本地 SQLite 为主 |

远端 dashboard 容器最近日志窗口中，META Strategy 相关路径占比最高：

| 路径 | 次数 |
| --- | ---: |
| `/tickers/META/documents/current` | 131 |
| `/tickers/META/policies` | 125 |
| `/tickers/META/known-events` | 125 |
| `/tickers/META/documents/document3/versions` | 123 |
| `/tickers/META/message-bus/overview` | 53 |
| `/tickers/META/message-bus/messages` | 50 |
| `/overview` + `/tickers` | 各 43 |

该日志窗口来自最近一次 dashboard 容器启动后，不是完整自然日。即使只按当前 META 数据估算：

| API | 单次 Supabase 读放大估算 | 日志次数 | 估算 Pooler payload |
| --- | ---: | ---: | ---: |
| `documents/current` | 全部 META run 约 4.2MB + 当前 run 约 1.4MB | 131 | 约 730MB |
| `document3/versions` | 全部 META run 约 4.2MB | 123 | 约 520MB |
| `known-events` | 当前 run 约 1.4MB | 125 | 约 175MB |
| `policies` | 当前 run 约 1.4MB | 125 | 约 175MB |
| 小计 |  |  | 约 1.6GB |

如果页面保持打开，按 4 小时窗口线性外推可达到约 9GB/天量级。这与截图中 2026-07-04 当天 6.213GB Shared Pooler Egress 是同一数量级。

## 代码层根因

### 1. `list_runs_by_ticker()` 返回完整 run，而不是摘要

位置：

- `src/doxagent/dashboard_api/real_service.py`
- `src/doxagent/blackboard/postgres_repository.py`

`_blackboard_runs(ticker, limit=DOCUMENT_HISTORY_LIMIT)` 默认 limit 为 100，然后调用 `blackboard.list_runs_by_ticker()`。

`PostgresBlackboardRepository.list_by_ticker()` 先查 run ids，再对每个 run id 调 `_get_run()`。

`_get_run()` 每次都会组装完整 `BlackboardRun`：

- `belief_state=self._get_belief_state(...)`
- `working_memory=self._get_json_models(... working_memory_entries.entry_json ...)`
- `commit_log=self._get_json_models(... commit_log_entries.commit_json ...)`
- `objections=self._get_json_models(... objections.objection_json ...)`
- `delegations=self._get_json_models(... delegations.delegation_json ...)`

Dashboard 只需要某个文档或版本摘要时，也被迫拉完整 working memory、commit log、objections。

### 2. `documents/current` 有多余 full-run 读取

`_current_document_run()` 先执行：

```python
runs = self._blackboard_runs(ticker)
if scheduler_run_id:
    selected = self._blackboard_run_by_id(ticker, scheduler_run_id)
    runs = [selected] if selected is not None else []
```

当 `scheduler_run_id` 已存在时，前面的 `_blackboard_runs(ticker)` 是冗余的，会先拉最近最多 100 个完整 run，然后又拉一次当前 run。

这是当前最容易做、收益很直接的最小修复点。

### 3. `document_versions` 为了版本列表拉完整 run

`document_versions()` 返回版本摘要，但 `_versioned_documents()` 内部遍历 `_blackboard_runs(ticker)`，因此会读取该 ticker 最近 run 的完整 working memory/commit/objection。

正确方向是版本列表只查：

- `blackboard_runs.run_id/ticker/created_at/workflow_state`
- `belief_state_snapshots.documents` 中目标 document type 的轻量字段
- 可选 `run_summaries` 中已有的计数/状态字段

### 4. `known-events` / `policies` 只需要当前 document bucket，却读取完整 run

这两个页面只需要 `belief_state_snapshots.documents` 里的 `known_events` 或 `monitoring_policy` bucket，当前却通过 `_current_or_fallback_run_with_documents()` 读取完整 run。

### 5. `run_summaries` 已存在但 dashboard 默认未用它减负

`run_summaries` 当前只有 104KB，字段包括：

- latest checkpoint status/node/time
- completed nodes
- stable document types
- working memory / commit / objection / evidence counts
- last error preview
- `full_payload_ref`

这正是 dashboard overview/list/version 默认应该读的轻量 summary，但当前核心文档/策略页面还绕回了 full Blackboard。

## 最小必要字段原则

本报告原先的优化方向是“降低 full-run 读取频率并加 cache”。这只能止血，不是最终边界。后续实现应以“前端当前页面真正渲染什么，后端就只读取/组装什么”为优先原则；如果与前文旧建议冲突，以本节为准。

### JSON 对象分类

| JSON 对象 | Dashboard 真正需要 | 当前为什么被读 | 最小必要判断 |
| --- | --- | --- | --- |
| `working_memory_entries.entry_json` | 正常 dashboard 页面不需要任何字段 | 被 `_get_run()` 连带全量读取 | 从 Research/Strategy 轮询与刷新路径彻底移除。仅允许离线诊断、一次性脚本或 agent context 低频读取；随着 Debug Viewer 移除，dashboard API 不再提供此类读取 |
| `commit_log_entries.commit_json` | 只需要文档版本原因：`trigger_reason`、`triggered_by`、`patch.target.document_type`、`patch.target.field_path`、`patch.rationale`、`created_at` | `document_versions()` 为了 `_document_version_reason()` 拉完整 commit | 不读完整 `commit_json`。改为轻量 version summary，或在写 `run_summaries`/版本摘要时预计算 `reason_label`、`reason_text`、`updated_by_label` |
| `belief_state_snapshots.documents` | 需要，但必须按 document type 和字段裁剪 | 当前为了取文档，先把整个 run 的 documents 和 child tables 一起拉出 | 唯一真正业务必需的大 JSON。只能读当前页面所需 document type 子树，不能通过 full `BlackboardRun` 间接读取 |
| `objections.objection_json` | 正常 dashboard 不需要。Runtime 页的 objection/exception 信息来自 runtime SQLite/repository，不是 Blackboard objection | 被 `_get_run()` 连带全量读取 | 从 dashboard 文档刷新路径移除。若未来做质量阻塞面板，只读 `id/status/target/reason/created_at` 摘要 |
| `workflow_checkpoints.checkpoint_json` | 正常 dashboard 不需要 | 主要来自 Debug Viewer bundle/checkpoint 详情 | dashboard 只允许读取 `status/next_node/completed_nodes/is_latest/created_at` 摘要；完整 checkpoint 不再通过 dashboard/debug API 暴露 |

### 页面级最小字段

Research 页当前真正渲染 `DashboardDocument.cards`，类型定义在 `frontend/dashboard/src/lib/dashboard-types.ts` 的 `DashboardDocument` / `DocumentCard`：

| 页面/对象 | 最小字段 |
| --- | --- |
| Document 1 | `document_id`、`generated_at`、`updated_at`、`version_status`、`availability`，以及各 research section 的 `summary`、`text`、`author_agent`、`reviewer_agents`、`evidence_refs` |
| Document 1 evidence refs | 可降级为 compact refs，例如 `evidence_id/source_type/title/summary/confidence`，不传完整 EvidenceRef 大对象 |
| Document 2 | `expectation_id/name`、`why_it_matters`、`direction`、`market_view.summary/text`、`realized_facts_summary`、`realized_facts`、`key_variables`、`event_monitoring_direction` |
| Strategy 默认 Document 3 状态 | `document_id`、`generated_at`、`updated_at`、`version_status`、`availability` |
| Strategy Document 3 cards | 默认不加载。当前主要服务“下载 Markdown”按钮，应改为点击下载或打开详情时再按需读取完整 cards |
| Known Events | `event_id`、`event_name`、`event_time_or_window`、`description`、`related_expectation_ids`、`duplicate_detection_keys`、`source`、`updated_at` |
| Policies | `policy_id`、`expectation_id`、`action_type`、`title`、`trigger_condition`、`severity`、`updated_at` |

Known Events 不需要完整 KnownEvent 原始布尔字段和完整 EvidenceRef。Policies 不需要完整 `scope`、`trigger`、`action`、`confirmation`、`risk_guard`、`evidence_fields`、`escalation_path`。

### 后端 API 拉取方式

需要新增 dashboard 专用轻量 document repository，不再让 Research/Strategy 走 `BlackboardService.get_run()` 或 `list_runs_by_ticker()`：

| 方法 | 读取范围 | 用途 |
| --- | --- | --- |
| `get_document_revision(ticker)` | 当前 `document_run_id` 和各 document type 的 updated_at/status | 页面聚焦、SSE 重连后校验漏事件 |
| `get_current_document(ticker, document_type, fields/profile)` | 当前 run 的指定 document type 子树 | Research 当前文档、Strategy 文档状态 |
| `list_document_versions(ticker, document_type, limit, cursor)` | `blackboard_runs` + 指定 document type 的版本摘要 + 预计算 reason summary | History sheet 按需打开时读取 |
| `get_document_version_detail(ticker, document_type, version_id)` | 单个版本的指定 document type 子树 | 用户选中历史版本后读取 |
| `list_known_events(ticker, page/filter)` | 当前 run 的 `known_events` 子树裁剪字段 | Strategy Known Events |
| `list_policies(ticker, page/filter)` | 当前 run 的 `monitoring_policy` 子树裁剪字段 | Strategy Policies |

分页必须在轻量对象上做，不能先 full run 后 Python 过滤。对于 `belief_state_snapshots.documents` 这种 JSONB 大列，第一阶段至少要避免 child tables；如果 Supabase egress 仍高，再把 Document 1/2/3、Known Events、Policies 的摘要拆成独立 summary table 或 materialized view。

## Research/Strategy 事件驱动刷新

Research / Strategy 页不应继续 60 秒定时拉取文档类数据。文档内容平时视为静态，只在文档重新生成、手动激活、策略/事件更新、用户手动刷新或 ticker 切换时重新读取。

### 前端刷新规则

| 页面 | 数据块 | 新规则 |
| --- | --- | --- |
| Research | `documentsCurrent(document1, document2)` | 首次加载、切换 ticker、手动刷新、收到当前 ticker 的相关文档事件时调用 |
| Research | `documentVersions(document1/document2)` | 不定时刷新。打开 `DocumentHistorySheet` 时按需加载；收到文档更新事件后可标记 stale 或刷新已打开面板 |
| Strategy | `documentsCurrent(document3)` | 首次加载、切换 ticker、手动刷新、收到 document3 相关事件时调用；默认只取状态元数据 |
| Strategy | `documentVersions(document3)` | 不定时刷新。仅打开历史版本面板或下载/详情动作时读取 |
| Strategy | `knownEvents` | 首次加载、切换 ticker、手动刷新、收到 known-events 更新事件时调用；分页逻辑保持 |
| Strategy | `policies` | 首次加载、切换 ticker、手动刷新、收到 policy 更新事件时调用；分页逻辑保持 |

### SSE 事件

复用现有 dashboard SSE，但新增或规范化轻量事件。事件 payload 禁止携带完整文档内容，只允许：

```json
{
  "ticker": "MU",
  "document_run_id": "run_xxx",
  "document_type": "document1|document2|document3|known_events|monitoring_policy",
  "updated_at": "2026-07-04T00:00:00Z"
}
```

建议事件类型：

| 事件 | 触发场景 | 前端动作 |
| --- | --- | --- |
| `document.run.completed` | 新 document run 生成完成 | 当前 ticker 的 Research/Strategy 标记 stale，并按 document type 刷新 |
| `document.run.activated` | 用户手动激活历史 run | Research/Strategy 刷新当前文档状态和可见数据 |
| `document.updated` | Document 1/2/3 更新 | 只刷新匹配 document type 的数据块 |
| `known_events.updated` | Known Events 更新 | 只刷新 Known Events |
| `policies.updated` | Monitoring Policies 更新 | 只刷新 Policies |

### 轻量 revision probe

新增轻量接口：

```http
GET /api/dashboard/v1/tickers/{ticker}/documents/revision
```

只返回 revision/status 元数据，不返回 JSON 文档内容：

```json
{
  "ticker": "MU",
  "document_run_id": "run_xxx",
  "document1_updated_at": "2026-07-04T00:00:00Z",
  "document2_updated_at": "2026-07-04T00:00:00Z",
  "document3_updated_at": "2026-07-04T00:00:00Z",
  "known_events_updated_at": "2026-07-04T00:00:00Z",
  "policies_updated_at": "2026-07-04T00:00:00Z"
}
```

使用场景：

1. 页面重新聚焦。
2. SSE 重连成功后。
3. 低频保险校验，例如 5 分钟一次。

该接口不能替代事件驱动，也不能重新引入完整文档轮询。

### 明确禁止

1. 不使用 Supabase Realtime 订阅 heavy 表。
2. Research / Strategy 页不继续 60 秒拉取完整文档。
3. Version history 不随页面常驻轮询。
4. SSE payload 不携带完整文档内容。
5. 不改变正常 dashboard 展示、切换 ticker、手动刷新和历史版本选择能力。

## 非主因但需要记录的问题

### RLS 状态

MCP `list_tables` 返回 `doxagent` schema 表 `rls_enabled=false`。这不是 egress 主因，但属于安全风险。若 `doxagent` schema 暴露给 Supabase client role 或存在宽松 grants，anon/auth key 可能读写不该暴露的数据。不要直接启用 RLS 到生产，因为没有配套 policy 会阻断访问；应作为独立安全任务设计最小 policy。

### SPA fallback 对探测路径返回 200

dashboard 日志出现：

- `GET /.env` -> 200
- `GET /.aws/credentials` -> 200

这大概率是 SPA fallback 返回 `index.html`，不是实际泄露文件。但它会制造安全噪音，也可能消耗少量 HTTP egress。建议在 Nginx 或 FastAPI static mount 前显式对 dotfile/sensitive path 返回 404。

### Debug Viewer 已移除

本轮实现已从 repo 移除 Debug Viewer 功能模块、compose service、启动脚本和相关测试。Dashboard API 不提供 Debug Viewer 才需要的 full checkpoint/full run API；必要的离线诊断改用一次性脚本或直接 storage export，不进入常驻服务。

## 最小优化方案

### P0：按最小必要字段重构 dashboard 读路径

1. 修复 `documents/current` 的冗余 `_blackboard_runs()`。
   - 如果 `scheduler_run_id` 存在，直接 `_blackboard_run_by_id()`，不要先 `_blackboard_runs(ticker)`。
   - 这是止血点，但不是最终形态；最终应完全不依赖 full `BlackboardRun`。

2. 新增 dashboard 专用轻量 document repository。
   - `documents/current` 只读当前 `document_run_id` 的目标 document type 子树。
   - `known-events` / `policies` 只读对应 document 子树裁剪字段。
   - `document_versions` 只读版本摘要，不读完整 working memory、commit log、objections。

3. 移除 Research/Strategy 文档类 60 秒轮询。
   - 保留首次加载、ticker 切换、手动刷新。
   - 通过 dashboard SSE 轻量事件刷新对应数据块。
   - `document_versions` 仅在打开历史版本面板或需要详情/下载时按需读取。
   - 5 分钟级 revision probe 只返回 revision/status 元数据，不返回文档内容。

4. 对 dashboard API 增加 response/query payload 日志。
   - 记录 endpoint、ticker、调用路径、document type、是否命中轻量 repository、估算读取 bytes、响应 bytes、耗时。
   - 先记录到本地 SQLite 或容器 stdout，不写 Supabase。

### P1：用轻量 SQL/摘要替换 full Blackboard 读取

1. 新增 Blackboard summary/document read repository，不改正常写链路。
   - `get_current_document_bucket(ticker, document_run_id, document_type)`
   - `list_document_versions(ticker, document_type, limit, cursor)`
   - `get_document_version(ticker, document_type, version_id)`
   - 只查询 `blackboard_runs` + `belief_state_snapshots.documents`，不加载 `working_memory_entries`、`commit_log_entries`、`objections`。

2. `known-events` / `policies` 改为只读当前 run 的目标 document bucket。
   - 不再调用 `BlackboardService.get_run()`。
   - 不再读取 working memory/commit/objection。
   - 分页、筛选、搜索在裁剪后的轻量对象上执行。

3. `document_versions` 默认返回 version manifest。
   - version list 只含 version id、document id、generated/updated time、summary、status、reason。
   - 版本原因从轻量字段或预计算摘要生成，不读取完整 `commit_json`。
   - 详情页才读取单个 document body。

4. `run_summaries` 接入 overview/ticker/history。
   - dashboard 默认读取 `run_summaries`。
   - full Blackboard run 不进入 Research/Strategy/dashboard 默认路径。

5. Document 3 cards 改为按需读取。
   - Strategy 默认只取 Document 3 状态元数据。
   - “下载 Markdown”或详情动作触发单独详情接口读取 cards。

### P2：控制磁盘和历史 payload

1. `workflow_checkpoints` retention。
   - 当前每个 run 约 3 条 checkpoint，但单条平均 808KB。
   - 建议保留 latest + failure checkpoint + 最近 N 条，旧 checkpoint 转本地归档或对象存储。

2. `working_memory_entries` retention/summary。
   - Supabase 保留 summary、content_type、agent、created_at、evidence ids、payload preview。
   - full `entry_json` 后续迁移到 SQLite/对象存储，或仅保留最近 N 条/失败相关条目。

3. `objections` / `commit_log_entries` 保留可展示摘要。
   - dashboard 不默认读 full JSON。
   - full JSON 仅离线诊断或一次性脚本按需读取。

4. 定期 VACUUM/TOAST 观察。
   - 当前 `working_memory_entries`、`belief_state_snapshots` 有 dead tuples，短期不是 egress 主因，但 retention 后需要观察表大小回落。

### P3：部署和安全保护

1. 移除 Debug Viewer。
   - 从 repo 中移除 Debug Viewer 功能模块、路由、compose service、部署文档入口和相关测试。
   - dashboard 不再提供 Debug Viewer 才需要的 full checkpoint/full run API。
   - 离线诊断保留为一次性脚本或 SQL，不作为常驻服务。

2. Dotfile/sensitive path 返回 404。
   - `/.env`、`/.aws/*`、`/.git/*` 等在 Nginx 或 FastAPI 层直接拒绝。

3. Supabase RLS 单独设计。
   - 不要直接一键启用 RLS。
   - 先确认 `doxagent` schema 是否对 anon/auth 暴露，再设计 dashboard 只读 summary policy。

4. 连接标识。
   - 为 dashboard、runtime-scheduler 等剩余常驻服务的 Postgres 连接设置 `application_name`，方便 Supabase/pg_stat 归因。

## 建议的数据边界调整

| 数据 | 当前 | 建议 |
| --- | --- | --- |
| `blackboard_runs` 基础元数据 | Supabase | 保留 Supabase |
| `run_summaries` | Supabase，轻量但使用不足 | 作为 dashboard 默认读取主表 |
| `belief_state_snapshots.documents` | Supabase full JSON | 保留，但 dashboard 用目标 bucket 查询，不整 run 读取 |
| `working_memory_entries.entry_json` | Supabase full JSON | Supabase 只保留摘要/最近 N 条，full 迁 SQLite/对象存储 |
| `commit_log_entries.commit_json` | Supabase full JSON | dashboard 只读版本原因摘要或预计算 reason 字段 |
| `objections.objection_json` | Supabase full JSON | dashboard 文档/策略路径不读；未来质量阻塞只读摘要 |
| `workflow_checkpoints.checkpoint_json` | Supabase full JSON | 增 retention；dashboard/debug API 不提供 full checkpoint；旧数据本地归档 |
| `monitoring_raw_messages` | 本地 SQLite | 保持 |
| `monitoring_standard_messages` | 本地 SQLite | 保持 |
| `monitoring_event_stream` | 本地 SQLite | 保持 |
| `persistent_runtime_*` | 本地 SQLite | 保持 |
| `stocktwits_*` | 本地 SQLite，Supabase 远端 0 行 | 保持 |

## 推荐实施顺序

1. 修掉 `documents/current` 的冗余 `_blackboard_runs()` 作为止血。
2. 新增 lightweight document repository，只读当前页面所需 document type 子树和版本摘要。
3. 替换 `documents/current`、`document_versions`、`known_events`、`policies` 的 full run 读取。
4. 移除 Research/Strategy 文档类 60 秒轮询，接入 dashboard SSE 轻量事件。
5. 增加 `GET /api/dashboard/v1/tickers/{ticker}/documents/revision` 作为页面聚焦/SSE 重连后的低频校验。
6. 移除 Debug Viewer 功能模块与 compose service。
7. 设置 checkpoint/working memory retention，并补齐 dotfile fallback、RLS/connection application_name 等保护。

## 验证标准

1. `pg_stat_statements` 中这些 SELECT 的 10 分钟增量应明显下降：
   - `select entry_json from doxagent.working_memory_entries ...`
   - `select commit_json from doxagent.commit_log_entries ...`
   - `select objection_json from doxagent.objections ...`
   - `select snapshot_id, ticker, documents ...`

2. 打开 Strategy 页面 10 分钟：
   - 不应看到 `working_memory_entries.entry_json` 随页面轮询持续增长。
   - `documents/current` 应只读当前 document bucket 或轻量 revision。
   - 不应看到 Research/Strategy 每 60 秒请求完整文档或 version history。

3. Supabase Dashboard：
   - Shared Pooler Egress 日增应从 GB 级降到 MB 级或低百 MB 级。
   - Database Size 可保持不变，重点看 Egress。

4. Dashboard 功能：
   - Overview、Message Bus、Runtime 继续可用。
   - Research/Strategy 文档展示不丢。
   - 历史版本仍可按需打开，但默认不自动全量拉取。

## 目前不建议做的事

1. 不建议把 Supabase 从 dashboard/config/权限/结果展示中移除。
2. 不建议把所有业务库切到 SQLite。
3. 不建议先删表或清空历史来掩盖 egress；DB size 小，真正问题是读取方式。
4. 不建议直接启用 RLS 而不配 policy。
5. 不建议继续用 full `BlackboardRun` 作为 dashboard 默认 view model。
6. 不建议继续保留 Debug Viewer 作为生产/常驻模块。
