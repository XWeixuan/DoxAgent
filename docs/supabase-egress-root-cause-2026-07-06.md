# DoxAgent Supabase Egress 2026-07-06 根因排查与优化方案

日期：2026-07-07  
范围：仅 DoxAgent，不包含 DoxAtlas。  
事件：Supabase Usage 图显示 2026-07-06 `Shared Pooler Egress` 约 11.363GB，且占当日 egress 100%。

## 结论摘要

7 月 6 日的 egress 暴涨不符合“Supabase Dashboard 单纯显示 bug”的主要特征。虽然无法从 Supabase 免费项目里拿到逐请求账单明细，但项目侧已有足够证据解释这 11GB 级流量：

1. Supabase egress 类型是 `Shared Pooler Egress`，不是 Storage、Realtime、Edge Function 或前端 REST API。
2. `pg_stat_statements` 中存在一组高度一致的 full Blackboard run 读取 SQL：`blackboard_runs`、`belief_state_snapshots.documents`、`working_memory_entries.entry_json`、`commit_log_entries.commit_json`、`objections.objection_json` 被重复读取约 29,600 次。
3. 按当前 live row 平均大小估算，这组 SQL 读回 payload 约 11GB 量级，和截图中的 11.363GB 高度吻合。
4. 远端 runtime-scheduler 当前有 `MU`、`META` 两个 ticker 处于 `running + paper_trading`，循环间隔 15 秒；7 月 6 日是美股交易日，运行时段会每个 tick 重建 runtime document bundle。
5. 当前 `WorkflowDocumentProvider.latest()` 每次不是只读当前 active document run，而是 `list_runs_by_ticker(limit=20)`，再对每个 run 执行完整 `get_run()`；MU 当前 11 个 run，META 当前 5 个 run，所以一次轻量状态检查会被放大成多次 heavy JSON 读取。

一句话根因：

> 7 月 6 日的主要异常流量来自 runtime-scheduler 在交易/监控时段每 15 秒调用 `document_provider.latest()`，而该方法通过 Supabase Shared Pooler 对同一 ticker 最近多个 Blackboard run 做全量 hydration，反复读回 working memory、commit log、objections 和 documents。

这和上一轮 dashboard Research/Strategy 文档轮询根因相邻，但不是同一个直接触发点。上一轮优化已经显著降低 Research/Strategy 的文档轮询读放大；这次暴露的是后台 paper-trading runtime loop 的文档 bundle 读取放大。

## 证据链

### 1. Supabase 侧：Shared Pooler 而非 API/Realtime/Storage

用户截图显示：

| 日期 | 指标 | 值 |
| --- | --- | ---: |
| 2026-07-06 | Shared Pooler Egress | 11.363GB |
| 2026-07-06 | Shared Pooler 占比 | 100% |

Supabase MCP 日志交叉验证：

| 服务 | 观察 |
| --- | --- |
| `api` logs | 最近 24 小时基本是 Supabase 管理/健康检查，如 `/rest-admin/v1/ready`、`/auth/v1/health` |
| `postgres` logs | 大量来自 pooler/DB 的连接认证记录 |
| `pg_stat_activity` | 当前没有长时间 active 查询；异常更像历史窗口内的高频短查询 |

判断：异常 egress 主要来自项目服务通过 Supabase Pooler 执行 SQL 后读回结果，不是前端直接高频访问 Supabase REST、Realtime 或 Storage。

### 2. pg_stat_statements：full run 读取次数和返回行数足以解释 11GB

`pg_stat_statements` reset 时间为：

| 字段 | 值 |
| --- | --- |
| `stats_reset` | 2026-06-30 02:56:32 UTC |

当前最关键 SQL 统计如下：

| SQL 模式 | calls | returned rows | 说明 |
| --- | ---: | ---: | --- |
| `select entry_json from doxagent.working_memory_entries where run_id = $1 ...` | 29,605 | 683,799 | 最大 payload 来源 |
| `select objection_json from doxagent.objections where run_id = $1 ...` | 29,598 | 713,945 | 行数最大 |
| `select commit_json from doxagent.commit_log_entries where run_id = $1 ...` | 29,598 | 78,686 | 中等行数但 JSON 较大 |
| `select snapshot_id, ticker, documents, commit_ids, created_at from doxagent.belief_state_snapshots where run_id = $1` | 29,607 | 29,607 | 每次 full run 都读 documents |
| `select run_id, ticker, created_by, workflow_state, created_at from doxagent.blackboard_runs where run_id = $1` | 29,610 | 29,608 | `_get_run()` header |
| `select run_id from doxagent.blackboard_runs where ticker = $1 order by created_at desc limit $2` | 3,895 | 26,889 | `list_runs_by_ticker()` 入口 |

按当前 live row 平均 payload 粗估：

| 返回对象 | returned rows | 当前平均大小 | 估算读回 payload |
| --- | ---: | ---: | ---: |
| `working_memory_entries.entry_json` | 683,799 | 约 12KB | 约 8.0GB |
| `commit_log_entries.commit_json` | 78,686 | 约 16KB | 约 1.2GB |
| `objections.objection_json` | 713,945 | 约 1.3KB | 约 0.9GB |
| `belief_state_snapshots.documents` | 29,607 | 约 29KB | 约 0.8GB |
| header / delegation / protocol overhead | - | - | 数百 MB 以内 |
| 合计 | - | - | 约 11GB 量级 |

这不是精确账单，但数量级和 Supabase 2026-07-06 的 11.363GB 非常接近，因此应优先按真实流量处理。

### 3. 代码路径：runtime-scheduler 每 tick 触发全量 document bundle

主要链路：

| 层级 | 文件 | 关键点 |
| --- | --- | --- |
| runtime loop | `src/doxagent/runtime_scheduler/loop.py` | 默认 `sleep_seconds=15` |
| 每 ticker tick | `src/doxagent/runtime_scheduler/service.py` | `tick_ticker()` 在 paper trading 且处于 `PRE_MARKET_DIGEST` / `FORMAL_MONITORING` 时调用 `_runtime_bundle_for_tick()` |
| document provider | `src/doxagent/runtime_scheduler/documents.py` | `WorkflowDocumentProvider.latest()` 调用 `self.blackboard.list_runs_by_ticker(normalized, limit=20)` |
| Blackboard repository | `src/doxagent/blackboard/postgres_repository.py` | `list_by_ticker()` 先查 run ids，再逐个 `_get_run()` |
| full run hydration | `src/doxagent/blackboard/postgres_repository.py` | `_get_run()` 会读 `belief_state_snapshots.documents`、`working_memory_entries.entry_json`、`commit_log_entries.commit_json`、`objections.objection_json`、`delegations.delegation_json` |

当前实现的问题不是“读取当前 active document set”，而是“读取当前 ticker 最近最多 20 个完整 run，然后在内存里判断哪个 bundle 可用”。这对 workflow 初始化场景可以接受，但对 15 秒 runtime loop 是错误的数据边界。

### 4. 远端 runtime 状态：7 月 6 日活动和交易日吻合

远端容器环境：

| 服务 | 关键配置 |
| --- | --- |
| `dashboard` | `DOXAGENT_STORAGE_MODE=postgres`，业务 Blackboard 仍走 Supabase Session Pooler 5432 |
| `runtime-scheduler` | `DOXAGENT_STORAGE_MODE=postgres`，`DOXAGENT_RUNTIME_SCHEDULER_LOOP_SLEEP_SECONDS=15` |
| monitoring / runtime / stocktwits | SQLite 本地化已启用 |

远端 SQLite `runtime_scheduler_states` 当前有两个 running ticker：

| ticker | status | monitor_mode | document_run_id | poll_cycles | runtime_executions |
| --- | --- | --- | --- | ---: | ---: |
| MU | running | paper_trading | `run_22cbcd33a06b48e883d06f1589095fe9` | 13,732 左右 | 154 |
| META | running | paper_trading | `run_b6ab0e6f76d343e3bc495e219cf76cc4` | 13,731 左右 | 407 |

7 月 6 日 scheduler audit 分布：

| 日期 | ticker | 事件 | 次数 |
| --- | --- | --- | ---: |
| 2026-07-06 | META | `message_poll_completed` | 1,334 |
| 2026-07-06 | MU | `message_poll_completed` | 729 |
| 2026-07-06 | META | `events_consumed` | 119 |
| 2026-07-06 | MU | `events_consumed` | 40 |
| 2026-07-06 | META/MU | weekly document update started/completed | 存在 |

解释：

- 2026-07-05 是周日，runtime 虽然可能继续做低频 poll，但不进入交易监控 runtime 上下文构建高峰。
- 2026-07-06 是周一交易日，`paper_trading + PRE_MARKET_DIGEST/FORMAL_MONITORING` 会触发 `_runtime_bundle_for_tick()`。
- `list_runs_by_ticker(limit=20)` 当前会把 MU 的 11 个 run 和 META 的 5 个 run 逐个 `_get_run()`；这解释了 `list_runs_by_ticker` 3,895 次却放大成 `_get_run` 29,610 次。

### 5. Supabase 当前数据体量不支持“写爆导致 egress”

当前表内 retained 数据不大：

| 表 | 当前行数 | 主要风险 |
| --- | ---: | --- |
| `blackboard_runs` | 16 | header 很小 |
| `belief_state_snapshots` | 16 | `documents` 当前约 470KB 总量 |
| `working_memory_entries` | 353 | `entry_json` 当前约 4.3MB |
| `commit_log_entries` | 49 | `commit_json` 当前约 760KB |
| `objections` | 345 | `objection_json` 当前约 457KB |
| `workflow_checkpoints` | 48 | `checkpoint_json` 当前约 12MB，磁盘风险高，但不是本次 top SQL |

7 月 6 日当前保留行的新增 payload 也只是 MB 级：

| 表 | 2026-07-06 当前保留行 | 估算 payload |
| --- | ---: | ---: |
| `working_memory_entries` | 66 | 约 1.5MB |
| `workflow_checkpoints` | 12 | 约 2.0MB |
| `commit_log_entries` | 16 | 约 184KB |
| `objections` | 43 | 约 68KB |
| `belief_state_snapshots` | 4 | 约 108KB |

判断：7 月 6 日不是“当天写入了 11GB 数据”，而是“已有 MB 级 JSON 被重复通过 pooler 读回数万次”。

### 6. 远端 SQLite 排查结果

容器内 SQLite 文件：

| 文件 | 大小 | 关键行数 | 判断 |
| --- | ---: | --- | --- |
| `/app/.tmp/monitoring_message_bus.sqlite3` | 57.36MB | raw/standard/event 各 6,806 | 高频 monitoring 数据已本地化，不计 Supabase egress |
| `/app/.tmp/stocktwits_polling.sqlite3` | 7.44MB | crawl_runs 2,320，messages 1,050 | Stocktwits 已本地化 |
| `/app/.tmp/runtime_scheduler.sqlite3` | 3.54MB | states 2，audit_events 3,842 | scheduler 状态已本地化 |
| `/app/.tmp/persistent_runtime_execution.sqlite3` | 3.31MB | executions 561，archive 502，exceptions 45 | runtime 执行结果已本地化 |
| `/app/.tmp/model_usage.sqlite3` | 32KB | model_usage_events 0 | 非本次 egress 来源 |

结论：本地 SQLite 分工本身在 monitoring/runtime 侧已经生效；本次 Supabase egress 的主因是 runtime loop 仍从 Supabase Blackboard 取 document bundle，而且取法是 full run hydration。

### 7. 部署状态风险

远端 `/root/doxagent` 当前不是干净的 GitHub commit：

| 项 | 状态 |
| --- | --- |
| remote HEAD | `0417d19` |
| working tree | 大量 modified/untracked 文件 |
| 运行服务 | `dashboard`、`runtime-scheduler`、`debug-viewer` 均在运行 |

风险：

- 线上实际运行代码不能只通过 GitHub commit 判断，必须以远端 dirty tree/container 为准。
- `debug-viewer` 容器仍在运行，虽然本次主因不是它，但它历史上拥有 full run/checkpoint 读取能力，应从代码、compose、远端部署面彻底移除。

## 根因定位

### P0 根因：runtime document bundle 每 tick 全量读取多个 Blackboard run

触发条件：

1. ticker 处于 `running`。
2. `monitor_mode=paper_trading`。
3. 市场阶段为 `PRE_MARKET_DIGEST` 或 `FORMAL_MONITORING`。
4. runtime loop 每 15 秒执行。

实际读取：

```text
RuntimeSchedulerLoop.run()
  -> RuntimeSchedulerService.run_due_once()
    -> tick_ticker()
      -> _runtime_bundle_for_tick()
        -> WorkflowDocumentProvider.latest()
          -> blackboard.list_runs_by_ticker(ticker, limit=20)
            -> PostgresBlackboardRepository._get_run(run_id) * N
```

每个 `_get_run()` 都读：

- `belief_state_snapshots.documents`
- `working_memory_entries.entry_json`
- `commit_log_entries.commit_json`
- `objections.objection_json`
- `delegations.delegation_json`

runtime 实际只需要当前 active document run 的 `known_events` 和 `monitoring_policy` 子树，完全不需要 working memory、commit log、objections，也不需要同 ticker 的历史 run。

### 次级风险 1：dashboard Runtime 页仍有高频本地 API 轮询

远端 dashboard 日志窗口中，`/runtime/nodes/o3`、`/runtime/graph`、`/runtime/overview` 等请求较热。当前这些主要读 SQLite，不是 Supabase 11GB 主因；但它们会增加 dashboard 后端压力，并可能在未来某些 route 间接触发 document provider。

应优化，但优先级低于 P0。

### 次级风险 2：Research/Strategy 文档路径已改善，但仍需保持禁止回退

当前 Research/Strategy 已从 60 秒 full document 轮询改成事件驱动 + 5 分钟 revision probe，Supabase 统计里 `_postgres_document_records` 轻量文档查询 calls 只有几十级，不是 7 月 6 日主因。

但仍要保持约束：

- 不允许 fallback 到 `_blackboard_document_records()` 做 full run。
- `document_versions` 必须只在打开历史面板时按需读取。
- `known-events` / `policies` 必须只读当前 document bucket，不读 full run。

### 次级风险 3：workflow_checkpoints 磁盘风险仍高

`workflow_checkpoints.checkpoint_json` 当前 retained payload 约 12MB，单行可到数百 KB；pg_stat 中 `select checkpoint_json` calls 很低，不是这次 11GB 主因，但它是 Supabase disk size 和未来 debug egress 的主要风险。

## 可执行优化方案

本次修复不能只处理 2026-07-06 的 runtime-scheduler 直接爆量点。更深层的问题是当前 Blackboard Postgres repository 把“读取完整 BlackboardRun”作为默认读写抽象，导致两类粗暴数据库访问：

| 类型 | 表现 | 后果 |
| --- | --- | --- |
| 直接粗暴读 | 业务代码显式调用 `get_run()` / `list_runs_by_ticker()` | 最终进入 `_get_run()`，读取 documents、working memory、commit log、objections、delegations |
| 隐式粗暴读 | 业务代码看起来只是 `submit_patch()` / `add_working_memory_entry()` / `create_objection()` | 内部走 `repository.mutate()`，先 `_get_run()` 读完整 run，再整体 `_replace_run()` 写回 |

核心目标不是简单删除 `get_run()`，而是把完整 `BlackboardRun` hydration 从默认业务路径隔离出去，只保留给低频 debug、eval、离线恢复和兼容接口。线上 runtime、dashboard、workflow agent context、patch/write 路径都必须改成按字段读取和 targeted write。

### 总原则

1. `get_run()` / `list_runs_by_ticker()` / `mutate()` 保留，但明确标记为 full-read/full-write compatibility API。
2. 新增轻量 read repository，按真实字段需求查询：

| 方法 | 用途 | 禁止读取 |
| --- | --- | --- |
| `get_run_header(run_id)` | 只取 run_id/ticker/workflow_state/created_at/updated_at | child tables |
| `get_document_buckets(run_id, document_types)` | 读取指定 document type 的 JSON bucket | working memory、commit log、objections |
| `get_document_bundle_by_run_id(ticker, run_id, document_types)` | runtime/dashboard 按当前 active run 获取 document bundle | 同 ticker 历史 run、child tables |
| `list_document_bundle_candidates(ticker, document_types, limit)` | 无 active run_id 时找最近少量候选 | `limit=20` full run 展开 |
| `list_document_keys(run_id)` | belief_state summary 只需要 document type -> ids | document 正文 |
| `list_working_memory_summaries(run_id)` | agent context 只需要 entry 摘要 | payload/full entry_json |
| `list_unresolved_objection_summaries(run_id, filters)` | blocker/resolver 只需要 unresolved 摘要 | full objection_json |
| `list_blocking_delegation_summaries(run_id, filters)` | resolver 只需要 blocking delegation 摘要 | full delegation_json |
| `get_objections_by_ids(run_id, ids)` | field repair 按 id 读取必要 objection | 全 run objections |
| `get_commit_summaries(run_ids, document_types)` | document version reason | full commit_json |

3. 写路径不再统一走 `mutate()`。append/update 类操作改成 targeted SQL。
4. 高频路径先接入轻量接口；full-read 只允许显式调用，例如 `get_full_run_for_debug()` 或离线脚本。
5. 增加可观测性，但不做 full run 读取保护/禁止开关，避免把兼容 API 改成线上隐性熔断点。

### 底层粗暴读取入口

| 底层函数 | 位置 | 实际行为 | 上层业务入口 | 处理方向 |
| --- | --- | --- | --- | --- |
| `get()` | `src/doxagent/blackboard/postgres_repository.py` | 单个 run 完整 `_get_run()` | `BlackboardService.get_run()` | 保留兼容，新增显式命名 full-read API |
| `list_by_ticker()` | `src/doxagent/blackboard/postgres_repository.py` | 先查最近 run_id，再对每个 run `_get_run()` | `BlackboardService.list_runs_by_ticker()` | dashboard/runtime/workflow 不再调用 |
| `mutate()` | `src/doxagent/blackboard/postgres_repository.py` | 写入前完整 `_get_run()`，再 `_replace_run()` | `submit_patch/create_objection/add_working_memory_entry/...` | P2 分阶段替换 targeted SQL |
| `_get_run()` | `src/doxagent/blackboard/postgres_repository.py` | 读 documents、working_memory、commit_log、objections、delegations | 上面三者内部调用 | 加可观测性，保留低频兼容 |

### P0-1：修复 runtime-scheduler 的 document bundle 读取

直接根因链路：

```text
runtime_scheduler/loop.py
  -> RuntimeSchedulerService.tick_ticker()
    -> _runtime_bundle_for_tick()
      -> WorkflowDocumentProvider.latest()
        -> blackboard.list_runs_by_ticker(ticker, limit=20)
          -> PostgresBlackboardRepository._get_run(run_id) * N
```

替代方案：

| 调用点 | 当前粗暴读 | 实际需要字段 | 替代读取 |
| --- | --- | --- | --- |
| `WorkflowDocumentProvider.latest()` | `list_runs_by_ticker(limit=20)` 展开多个 full run | run header、5 类 document bucket：global_research、expectation_unit、known_events、monitoring_config、monitoring_policy | 优先按 scheduler `document_run_id` 查当前 run 的 document buckets；无 run_id 时只查最近 1-3 个候选 run 的 bucket |
| `WorkflowDocumentProvider.by_run_id()` | `get_run(document_run_id)` | 同上，但只限单 run | `get_document_bundle_by_run_id()` |
| `RuntimeSchedulerService._runtime_context()` | 间接 `latest()` | runtime 只需要 known_events + monitoring_policy compact fields | 缓存 `(ticker, document_run_id)` 的 compact runtime context |
| `RuntimeSchedulerService.detail()` / `document_status()` fallback | `document_provider.latest()` | `DocumentSetStatus` | 优先用 `state.document_status`；缺失时轻量 probe document status |
| dashboard backtest `_ensure_documents()` | `document_provider.latest()` | 可用 document bundle | 复用轻量 document provider |

实施步骤：

1. 在 Postgres repository 层新增 document bundle 轻量读取方法，只 select：

```sql
select b.run_id, b.ticker, b.workflow_state, b.created_at, b.updated_at,
       s.documents -> $document_type_1 as document_bucket_1,
       s.documents -> $document_type_2 as document_bucket_2
from doxagent.blackboard_runs b
join doxagent.belief_state_snapshots s on s.run_id = b.run_id
where b.ticker = $ticker and b.run_id = $run_id
```

2. `WorkflowDocumentProvider.latest()` 增加可选 `preferred_run_id` 或由 scheduler 传入当前 `state.document_run_id`。
3. `RuntimeSchedulerService._runtime_bundle_for_tick()` 优先读取当前 active `document_run_id`，不再用 `list_runs_by_ticker(limit=20)`。
4. 无 active run 时，最多查最近 1-3 个候选 run 的 document buckets，仍不得读取 working memory、commit log、objections。
5. `by_run_id()` 改成同一套轻量 document bucket 查询。

验收标准：

- `pg_stat_statements` 中 `working_memory_entries.entry_json`、`objections.objection_json`、`commit_log_entries.commit_json` calls 不随 scheduler tick 增长。
- `blackboard_runs where ticker order by created_at limit $2` 可存在，但调用不再展开 `_get_run()`。
- `runtime-scheduler` 在交易时段运行 30 分钟，Shared Pooler egress 不再进入 GB 级。

### P0-2：给 runtime context 加 `(ticker, document_run_id)` 缓存

当前 active document run 在同一交易日内通常稳定，不应每 15 秒重新从 Supabase 读取。

| 缓存 key | 值 | 失效条件 |
| --- | --- | --- |
| `(ticker, document_run_id)` | compact runtime context / DocumentBundle | 手动激活 document run、weekly update 完成、known events 更新、policies 更新、进程重启 |

缓存内容只保留 runtime 真正需要的字段：

| 对象 | compact fields |
| --- | --- |
| known events | `event_id/event_name/event_time_or_window/description/related_expectation_ids/duplicate_detection_keys/source/updated_at` |
| policies | `policy_id/expectation_id/action_type/title/trigger_condition/severity/updated_at` |

不要缓存 full `BlackboardRun`，也不要把 working memory、commit log、objections 放进 runtime context。

### P0-3：移除 debug-viewer 相关代码并确保远端对齐

这次不只是移除远端常驻容器，而是彻底移除 Debug Viewer 功能模块和默认部署面，避免未来任何 dashboard/debug API 再提供 full run 或 full checkpoint 读取。

执行清单：

| 范围 | 动作 |
| --- | --- |
| compose / Docker | 从默认 `docker-compose.yml`、deployment docs、远端服务中移除 `debug-viewer` service |
| backend code | 删除或停用 debug viewer router/app/loader 中读取 `BlackboardRun`、`workflow_checkpoints.checkpoint_json` 的 API |
| frontend / scripts | 删除 Debug Viewer 入口、launcher、说明文档中鼓励常驻使用的路径 |
| tests | 删除或改写只服务 Debug Viewer 的 full bundle 测试 |
| remote | 部署后确认 `docker compose ps` 不再出现 `doxagent-debug-viewer`，远端文件与 Git commit 对齐 |

保留原则：离线诊断可以通过一次性脚本显式调用 `get_full_run_for_debug()`，但不再作为线上服务常驻。

### P0-4：增加 full-read 可观测性，不做禁止保护

在 `_get_run()` 和 `list_by_ticker()` 增加日志/metrics，目标是定位和量化，不做强制阻断。

记录字段：

| 字段 | 说明 |
| --- | --- |
| operation | `blackboard.full_read.get` / `blackboard.full_read.list_by_ticker` |
| caller stack | 截断后的业务调用栈 |
| run_id / ticker | 能定位到具体 run |
| estimated_payload_bytes | 当前 run 的 documents/working_memory/commit/objection/delegation 粗略大小 |
| child_counts | working_memory_count、commit_count、objection_count、delegation_count |
| service | dashboard / runtime-scheduler / workflow / script |

输出策略：

- 单次 full read 超过阈值打印 warning。
- 同一进程同一分钟 full read 次数超过阈值打印聚合 warning。
- 不引入 `DOXAGENT_ALLOW_FULL_BLACKBOARD_READS` 之类禁止开关。

### P1-1：改 workflow agent context 为按 node/permissions lazy read

`_task_input_context()` 是 workflow 内部最值得优先改的点。它当前每个 agent task 都可能 `get_run()`，但实际字段可以按 node 和 permissions 裁剪。

| 字段 | 当前来源 | 实际是否需要 | 替代 |
| --- | --- | --- | --- |
| `completed_nodes` / `stable_document_types` | checkpoint | 必需 | 直接用 checkpoint，不查 DB |
| `pending_patch_ids` / `pending_patches` | checkpoint | 只在部分 review/resolve 节点需要 | 先判断 compaction，再决定是否构造 |
| `belief_state_summary` | `run.belief_state.documents` | 只需要 document type -> document ids | `list_document_keys(run_id)` |
| `working_memory_summary` | full working memory | 多数 Document2/3 节点会被 compact 掉 | 只有保留时查 `entry_id/author_agent/content_type`，不查 payload |
| `unresolved_objections` | full objections | 多数节点会被 compact 掉，resolver 节点另有专用上下文 | `list_unresolved_objection_summaries()` |
| `blocking_delegations` | full delegations | 多数节点会被 compact 掉 | `list_blocking_delegation_summaries()` |
| `global_research_context` | full run 中取 global_research | 只在权限允许且未 compact 时需要 | 只查 `documents -> global_research` bucket |

关键实现点：

1. `_task_input_context()` 先根据 `node`、`permissions`、compaction 规则计算需要哪些块。
2. 再按块调用轻量 SQL。
3. 不允许先 full read 再 compact。

### P1-2：改 Known Events 生成路径，循环内不碰 DB

`_normalize_known_events_document()` 当前每个 event 会间接多次读取 run。应在进入循环前一次性读取 compact context。

| 函数 | 实际依赖 | 替代 |
| --- | --- | --- |
| `_known_event_expectation_id()` | expectation docs 的 `expectation_id/name`、`realized_facts.event_id/description`、`key_variables.name/current_status` | 从 `KnownEventContext.expectation_docs_compact` 读取 |
| `_expectation_source_refs_for_event()` | expectation docs 的 `market_view.evidence_refs`、`realized_facts.evidence_refs/price_reaction.evidence_refs`、`key_variables.evidence_refs` | 从预读 compact evidence refs 读取 |
| `_global_research_source_refs_for_event()` | global research sections 的 `summary/text/evidence_refs` | 从 `KnownEventContext.global_research_sections_compact` 读取 |

新增 `KnownEventContext`：

```text
KnownEventContext
  - expectation_docs_compact
  - global_research_sections_compact
  - fallback_evidence
```

这样一个 known-events document 不会按 event 数重复 full run read。

### P1-3：改 Document2 resolver / blocker 循环

`document2/legacy_quality.py` 是 workflow 内部第二个高风险区，尤其 `_resolve_blockers()` while loop。先替换循环内 `get_run()`，收益最大且风险较低。

| 函数 | 当前问题 | 最小替代 |
| --- | --- | --- |
| `_resolve_blockers()` | loop 内多次 `get_run()`，status update 又触发 `mutate()` full read | 开始时查 blocking delegations + actionable unresolved objections；每次 update 后只刷新相关 objection/delegation summaries |
| `_document2_field_repair_tasks()` | 函数内至少两次 full read：`run` 和 `refreshed_run` | 传入最新 unresolved objections；如需刷新只查 ids/status |
| `_field_repair_context()` | 为 task objections 读完整 run | `get_objections_by_ids(run_id, task.objection_ids)` |
| `_complete_o1_revision_delegations()` | 只需判断是否还有 actionable objections + O1 blocking delegations | `has_actionable_unresolved_objections()` + `list_blocking_delegations(target_agent=O1)` |
| `_document2_resolution_decision_retains_blocker()` | 只查一个 objection 的 dedupe_hash/status | `get_objection_summary(objection_id)` |
| `_reopen_numeric_sanity_objections_after_o1_revision()` | 只需要 existing objections by ids/status | `get_objections_by_ids()` |

实施边界：不要一次性重写 resolver 业务逻辑；先把循环内 full read 替换为 summary/id 查询，再保留现有决策流程。

### P1-4：改 Document1 / Promotion 辅助函数

| 函数 | 实际需要 | 替代 |
| --- | --- | --- |
| `_latest_global_research_document_payload()` | latest global_research document payload | 只查 `documents -> global_research` |
| `_expectation_names_from_belief_state()` | expectation docs 的 `expectation_name/expectation_id` | 只查 `documents -> expectation_unit` 并裁剪 |
| `_document1_context_pack_from_checkpoint()` | global_research document | 只查 global_research bucket |
| `_active_document2_review_findings_for_promotion()` | objections by id/status/dedupe_hash | `list_objection_summaries()` |
| `_document2_promotion_runtime_blockers()` | actionable unresolved objections | `list_unresolved_objection_summaries()` |

### P1-5：改 ContextBuilder，避免未来每个 agent task full read

当前默认 runner 未必注入 `ContextBuilder`，但一旦启用就是每个 agent task full read，必须提前改。

| ContextBuilder 字段 | 最小读取 |
| --- | --- |
| `belief_state_summary` | 只读 permissions/scoped document types 的 document bucket |
| `working_memory_summary` | 仅当 scope 包含 `working_memory` 或 private memory 时查；默认不要查 payload |
| `unresolved_objections` | 只查 unresolved summary |
| `blocking_delegations` | 只查 blocking summary |
| `build_document3_runtime_context()` | 只读 known_events + monitoring_policy document buckets |

### P1-6：保持 dashboard 文档 API 轻量边界

已有 dashboard 优化继续保持，并补上 fallback 风险：

- Research/Strategy 不恢复 60 秒 full document polling。
- `documentVersions` 只在历史面板打开时读取。
- `knownEvents` / `policies` 只读当前 document bucket 或 runtime SQLite repository。
- Strategy 默认不加载 Document 3 full cards；下载 Markdown 时再按需读取。
- 不使用 Supabase Realtime 订阅 heavy 表。
- `real_service.py` 的 `_blackboard_document_records()` fallback 保留兼容，但默认线上不应进入；后续应移除或改成显式 debug/offline feature flag。

### P2-1：拆 `mutate()` 写路径，替换隐式写前 full read

`mutate()` 是隐含 full read 的根。按操作拆 targeted SQL，先做低风险 append/status update，再处理最敏感的 `submit_patch()`。

| 当前方法 | 当前依赖 | 新写法 |
| --- | --- | --- |
| `add_working_memory_entry()` | 只需要 run.ticker + insert entry | `select ticker from blackboard_runs where run_id=?`，insert `working_memory_entries`，更新 `run_summaries` |
| `create_objection()` | target ticker、existing objection by id/dedupe | 查 run header；查 unresolved matching objection；insert/update one objection |
| `create_delegation()` | target ticker、delegation_id exists | 查 run header + delegation id；insert one delegation |
| objection status methods | one objection | `update objections set objection_json=..., status=... where objection_id=?` |
| delegation status methods | one delegation | targeted update one delegation |
| `submit_patch()` | validate target ticker、检查 blockers、更新 documents、insert commit | 查 run header；查 target 相关 unresolved blockers；局部更新 `belief_state_snapshots.documents`；insert commit row |

`submit_patch()` 的最小安全校验必须保留：

- target ticker 是否匹配 run ticker。
- permissions 是否允许写 document type。
- patch evidence_refs 非空。
- target 是否被 unresolved objection / blocking delegation 阻塞。
- document JSON 更新后仍能被对应 model validate。

建议落地顺序：

1. `add_working_memory_entry()` targeted insert。
2. objection/delegation status targeted update。
3. `create_objection()` / `create_delegation()` targeted insert。
4. `submit_patch()` 局部 document update + commit insert。
5. 最后再把 `mutate()` 标记为 compatibility-only。

### P2-2：retention 与磁盘保护

虽然本次主因是读放大，但 disk 风险仍要处理：

- `workflow_checkpoints` 只保留 latest + 最近 N 条摘要。
- full `checkpoint_json` 默认进入 SQLite 或对象存储。
- `working_memory_entries` / `objections` 对 Supabase 仅保留摘要或设置 TTL。
- `commit_log_entries.commit_json` 只保留版本原因需要的派生字段。

## 验证方案

修复后用以下方式确认：

1. 记录 `pg_stat_statements` baseline：
   - `_get_run()` 相关 5 条 SQL calls。
   - `_postgres_document_records` 轻量 SQL calls。
2. 让 `runtime-scheduler` 在交易时段运行 30 分钟，保持 `MU` / `META` 等 running ticker 正常工作。
3. 再次查询 `pg_stat_statements`：
   - `working_memory_entries.entry_json` calls 不应随 scheduler tick 增长。
   - `objections.objection_json` calls 不应随 scheduler tick 增长。
   - `commit_log_entries.commit_json` calls 不应随 scheduler tick 增长。
   - 只允许 `belief_state_snapshots.documents -> <type>` 轻量查询低频增长。
4. 检查 workflow 运行日志：
   - `_task_input_context()` 不再每个 agent task full read。
   - Known Events normalization 循环内不再触发 DB read。
   - Document2 resolver loop 内不再重复 `get_run()`。
5. 检查 targeted write：
   - `add_working_memory_entry()`、objection/delegation status update 不再进入 `mutate()`。
   - `submit_patch()` 保留校验并只更新目标 document bucket + commit row。
6. 检查 Supabase Usage：
   - Shared Pooler Egress 应回落到 MB 级/小时，而不是 GB 级/日。
7. 检查远端 dashboard：
   - Research / Strategy / Runtime 正常展示。
   - 手动刷新仍可用。
   - paper-trading runtime 仍可消费 monitoring events。
8. 检查远端部署面：
   - `docker compose ps` 不再出现 `doxagent-debug-viewer`。
   - 远端 `/root/doxagent` 与 Git commit 对齐，没有依赖未提交 dirty tree 的 debug-viewer 文件。

## 后续实施优先级

| 优先级 | 项目 | 预期收益 |
| --- | --- | --- |
| P0 | 新增轻量 document bundle repository，并改 `WorkflowDocumentProvider.latest()/by_run_id()` | 最大，直接消除 runtime-scheduler 7 月 6 日主因 |
| P0 | runtime context 缓存 `(ticker, document_run_id)` | 大，减少每 tick DB 读取 |
| P0 | 增加 `_get_run()` / `list_by_ticker()` payload 与 caller 可观测性 | 快速发现残留粗暴读，不做禁止保护 |
| P0 | 彻底移除 Debug Viewer 代码、compose 服务和远端常驻容器 | 消除 full run/checkpoint 线上服务入口 |
| P1 | 改 `_task_input_context()` 为按 node/permissions lazy read | 降低 workflow agent task 爆发读 |
| P1 | 改 Known Events normalization 为一次性 compact context 预读 | 消除 event 循环内重复 full read |
| P1 | 改 Document2 resolver/blocker loop 的 objection/delegation 查询 | 消除 resolver while loop 内 full read |
| P1 | 改 Document1 / Promotion / ContextBuilder 辅助读路径 | 防止中频路径继续依赖 full run |
| P1 | 保持 dashboard Research/Strategy/knownEvents/policies 轻量边界，移除 full-run fallback 默认入口 | 防止 dashboard 路径回退 |
| P2 | 拆 `mutate()` 写路径，先 targeted insert/status update，再处理 `submit_patch()` | 消除隐式写前 full read |
| P2 | checkpoint/working memory/objection retention | 控制 disk size 与未来 debug egress |

## 最终判断

7 月 6 日的异常不是 Supabase 写入膨胀，也不是 SQLite 分工失败；直接爆量点是后台 runtime-scheduler 把 Supabase Blackboard 当成可高频 full hydration 的运行时状态源。更深层的问题是业务代码中仍存在显式 `get_run()` / `list_runs_by_ticker()` full read，以及通过 `mutate()` 写入前隐式 full read 的兼容抽象。

Supabase 应继续作为远端业务状态库和 dashboard 展示库，但 runtime tick、dashboard API、workflow agent context、resolver loop 和常规写入路径都只能按字段读取或 targeted write；完整 `BlackboardRun` 读取只应保留给低频 debug、eval、离线恢复和显式兼容入口。
