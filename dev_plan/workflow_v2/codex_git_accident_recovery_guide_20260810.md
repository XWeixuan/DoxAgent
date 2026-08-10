# Codex SDK / Data MCP Git 事故取证与整体恢复指引

日期：2026-08-10  
状态：取证完成，尚未执行代码恢复  
适用仓库：`C:\Users\WEIXUANXIE\Desktop\DoxAgent`

## 1. 目的与边界

本文件用于指导后续一次性恢复 2026-08-09 04:45:02 之后、2026-08-10
19:02:25 之前丢失的 tracked 工作区修改，覆盖：

- Codex SDK Document 1 v2 平行框架；
- 独立 codex-worker、Workspace、Data MCP、Source Capture；
- Hybrid SQLite/Supabase/Storage 持久化；
- Dashboard API 和前端 v2 分支；
- Data MCP / Observation 基础设施；
- IBKR TWS 与 provider-neutral market route；
- 相关依赖、配置、测试和验收记录。

本文件只梳理恢复证据、目标内容、顺序和验收方式。当前取证轮次没有恢复
任何被清除的 tracked 源码，也没有重新迁移 Supabase、修改远端服务器或切换
生产默认 workflow。

## 2. 事故结论

### 2.1 时间线

| 北京时间 | 事件 |
|---|---|
| 2026-08-09 04:45:02 | 原始真实提交 `427ebab9751b9076ee92222e6a5ad214a0b3d6d7`，消息“开始codex迁移” |
| 2026-08-09 04:55 起 | Codex SDK / D1 v2 Phase 1-8 开始实施 |
| 2026-08-09 17:23 起 | Data MCP / Observation 延续会话开始落地代码 |
| 2026-08-10 16:00 起 | IBKR TWS、Hybrid DB 与 provider-neutral market route 收尾 |
| 2026-08-10 18:46:33 | 最终 Hybrid/Storage/镜像验收完成 |
| 2026-08-10 19:02:25 | reflog 出现 `fast-import`，随后 `reset: moving to HEAD`；tracked 工作区修改消失 |

### 2.2 Git 状态

- 当前 `main` 为 `de185c829b79939ddd9fc0c3969836c89903a344`，不是原始
  `427ebab`；两者提交时间和消息相同，但 parent、tree 和 commit hash 不同。
- `de185c8` 与 `427ebab` 的树差异为 `recovery/**` 下 376 个文件删除，20,682
  行删除；当前分支属于 fast-import 重写历史。
- 事故前最后一次完整 `git status` 记录中有 109 个 tracked `M`，另有 1 个
  异常嵌套仓库指针，合计即“110 个改动”。
- 事故前 `git diff --stat` 实际包含 98 个内容变化文件，7,834 insertions、
  2,389 deletions；状态数更高主要来自行尾/索引差异。
- 可见 agent tool log 中没有显式 `git reset`、`git clean`、`git checkout` 或
  `fast-import` 命令。破坏动作更符合 Codex Desktop Git capture/restore 内部过程，
  不能把它误记为正常 agent shell 修改。

### 2.3 事故后断裂的直接复现证据

在当前事故后工作树上运行以下只读测试集合：

```powershell
uv run pytest -q -p no:cacheprovider --basetemp=.tmp/pytest-git-recovery-a `
  tests/test_data_mcp_runtime.py `
  tests/test_ibkr_tws_bottom_layer.py `
  tests/test_commercial_provider_tools.py `
  tests/test_codex_runtime_v2.py `
  tests/test_codex_document1_workflow.py `
  tests/test_horizontal_collection_execution.py
```

测试在 collection 阶段即停止；`tests/test_codex_document1_workflow.py` 与
`tests/test_horizontal_collection_execution.py` 均无法从当前 tracked 基线的
`src/doxagent/horizontal_collection/schema.py` 导入 `HorizontalCollectionBundle`。
这与会话补丁、最终镜像和仍存的 untracked 调用方相互印证，说明事故确实清除了
tracked 的契约接线，而不是新增模块本身实现错误。恢复时应先完成 R2 中的
`schema.py`、`settings.py`、`tools/registry.py` 等契约脊柱，再运行完整测试；在此之前
继续追逐后续导入错误没有诊断价值。

### 2.4 当前禁止事项

在整体恢复完成并验收前：

1. 不得 push 当前 `main`；
2. 不得运行 `git clean`、`git reset --hard`、`git checkout -- .`、`git restore .`；
3. 不得在当前工作区直接执行历史重放或大范围 formatter；
4. 不得重新执行 Supabase migration；远端 `20260808210847` 已应用；
5. 不得删除当前 untracked 目录、`.env`、SQLite、Docker volumes 或最终镜像；
6. 不得将 88 个 Ruff 格式化噪声当作业务恢复目标。

## 3. 已完成的保护快照

快照根目录：

```text
C:\Users\WEIXUANXIE\Desktop\DoxAgent_recovery_snapshots\20260810T193000_hybrid_git_accident
```

保护内容：

- `doxagent-protected-refs.bundle`：321,556,201 bytes，19 refs，
  `git bundle verify` 返回 complete history；
- `refs/rescue/20260810/original-427ebab`：原始真实提交；
- `refs/rescue/20260810/current-de185c8`：当前 fast-import 重写提交；
- `refs/rescue/20260810/unreachable/*`：6 个原不可达提交；
- `untracked_worktree/`：76 个关键文件，复核为 76/76 SHA-256 相同；
- `dashboard_image/`：最终 dashboard 镜像中的 `/app/src`、`pyproject.toml`、
  `uv.lock`、`codex_assets` 和前端构建产物；
- `codex-runtime.sqlite3`：`PRAGMA integrity_check=ok`，schema version 2，
  17 sources、34 manifests；
- `.env`、worker workspaces、Codex Home volume tar、SQLite pre-v2 backup。

原始提交和当前 untracked 文件因此已有两层保护；后续恢复必须在新的 recovery
worktree 中进行，不得把快照目录当作可写工作目录。

## 4. 证据来源与可信等级

### 4.1 证据 A：Codex / Hybrid 主会话

```text
C:\Users\WEIXUANXIE\.codex\sessions\2026\08\08\
rollout-2026-08-08T19-12-43-019fe113-41fc-78a3-9ca7-06285d1fea81.jsonl
```

- 截止点后包含 131 次 `apply_patch`；
- 涉及 71 个唯一历史路径，包括后续移动/删除的临时路径；
- 关键补丁位置：681、781、850、856、911、2515、2958、3266、3648、
  4455、4464、4613、4713；
- 事故前完整状态和 diff stat 位于 call/output 4305/4306；
- 最终镜像、健康状态与正文/event 验收位于 4724/4725。

### 4.2 证据 B：Data MCP / IBKR 延续会话

```text
C:\Users\WEIXUANXIE\.codex\sessions\2026\07\29\
rollout-2026-07-29T19-19-13-019fad99-9eb5-7722-933c-07e9e579a3d3.jsonl
```

该会话虽然从 7 月 29 日开始，但在 2026-08-09 至 08-10 继续执行：

- 截止点后包含 77 次 `apply_patch`、44 个唯一文件；
- Data MCP 关键补丁：2591、2597、2617、2639、2665、2768、2891、3371；
- IBKR TWS 关键补丁：3644、3772、3802、3874、3879、3890、4067、4111；
- `data_mcp_development.md` 的两处精确修改位于 3371 和 3463；
- `tests/test_commercial_provider_tools.py` 的 TWS 测试迁移位于 3890、3895、3930。

### 4.3 证据 C：最终 dashboard 镜像

镜像快照是事故前成功运行的 Python runtime 最终副本。对镜像内文件可直接按
SHA-256 恢复，不应先手工重写：

| 文件 | 镜像 SHA-256 |
|---|---|
| `src/doxagent/settings.py` | `22727b452c25846f6a6746a8ffd3e828b64c8be598fe7a6de89c5f651aae2fd7` |
| `src/doxagent/dashboard_api/app.py` | `41c91a784920cc22af632a475e66b4652f46a320937d3afb2578465734f7c36d` |
| `src/doxagent/horizontal_collection/registry.py` | `90e4847412412d5ca7646236f30445c2691b73a5ef0b327a4d5ffb295d92d90b` |
| `src/doxagent/horizontal_collection/schema.py` | `052168a90459a2b4964f6a743c0b8e77b84c8f1b76bf3189c1718979060cb9df` |
| `src/doxagent/agents/config.py` | `5fd8c8f97e03ceabfd1d2c21df4f4c55e3ce15cf8587ee1e02ce4657a6865e91` |
| `src/doxagent/skills/registry.py` | `ea0fcae8cf1cf88ad4a89c7cb41c7eda3733861fe8a80958a0bf3df11b144125` |
| `src/doxagent/tools/factory.py` | `80df3adc4a294a63b815a3141388ef6d7eadcd91c400ec27402977359a45d875` |
| `src/doxagent/tools/providers/ibkr.py` | `5270a73bef44a1602d4f8a707300465a29f3ef71f042148de1ecc2b9f3377ab2` |
| `src/doxagent/tools/registry.py` | `e8c7fbb3c03073fcfdd52302c557ff589d94ad67a73aecfea28456a20be14844` |
| `src/doxagent/workflows/initialization/shared.py` | `27b48dcc71931d721114c139d641b6ca2c95cb07b108e15012573407126d8c42` |
| `pyproject.toml` | `16c5da7372db0559d83ce50148a5e9cd1e10f5086fee98e550dc38f9bd226c3d` |
| `uv.lock` | `99b579f0e93bf6deb6931b99cfb00a78402ccfc28b4ce807c381ed9868e304f1` |

### 4.4 证据 D：当前 untracked 工作树

以下实现仍存在，并已与保护快照逐字节核对：

- `src/doxagent/codex_runtime/`；
- `src/doxagent/codex_worker/`；
- `src/doxagent/data_runtime/`；
- `src/doxagent/observations/`；
- `src/doxagent/mcp/`；
- `src/doxagent/workflows/codex_document1/`；
- `src/doxagent/horizontal_collection/{collector,compiler,context}.py`；
- `src/doxagent/tools/providers/{ibkr_tws,market}.py`；
- `codex_assets/document1_v2/`；
- Codex/Data/IBKR 测试、migration、backfill 和 smoke 脚本。

这些文件不得从聊天记录重新生成；后续应直接从保护快照复制，并与当前工作区
再次做 SHA-256 对照。

## 5. 语义丢失文件清单：21 个

恢复策略分为：

- **IMAGE**：最终镜像存在逐字节副本，优先直接覆盖到 recovery worktree；
- **PATCH**：镜像不含源文件，按原始会话补丁重建；
- **MERGE**：同时被 Codex、Data MCP、IBKR 修改，使用镜像最终版，不可只重放
  某一条会话的早期补丁。

### 5.1 运行时与契约

| 文件 | 丢失改动 | 恢复源 | 验收重点 |
|---|---|---|---|
| `src/doxagent/settings.py` | Codex feature flag、worker URL/token/capability、workspace、`memory/sqlite/hybrid/postgres`、remote egress opt-in、local mirror、published Storage、模型/effort/retry/subagent；同时移除旧 IBKR HTTP 配置并增加 localhost TWS 配置 | **IMAGE + MERGE** | `DoxAgentSettings` 能解析 Hybrid、Storage、IBKR TWS；默认保持 v2 disabled |
| `src/doxagent/dashboard_api/app.py` | 注入 `CodexDocument1RunService`，feature flag 打开时注册 `/codex-runs` router | **IMAGE** | flag off 时 legacy app 不受影响；flag on 时 router 存在 |
| `src/doxagent/horizontal_collection/schema.py` | 新增 `PromotedStateValue`、placeholder/range validator、`HorizontalCollectionBundle` | **IMAGE** | null/UNKNOWN/缺半边 range 被拒绝；合法 StateValue 可序列化 |
| `src/doxagent/horizontal_collection/registry.py` | PROGRAM provider capability 修正、VIX FRED route 修正、类型注解；最终 share price target 改走 `market.quote_snapshot` | **IMAGE + MERGE** | registry target 与 collector/untracked market provider 一致 |
| `src/doxagent/tools/registry.py` | `ToolDescriptor` 增加 input schema、source、业务分类、use/avoid、fallback、freshness、PIT、read-only、contract version、availability、output profile | **IMAGE** | Data MCP contract 投影字段完整 |
| `src/doxagent/tools/providers/ibkr.py` | 从 Client Portal HTTP 全量迁移到官方 TWS socket；contract、snapshot、history、trade tape；环境 gating、错误归一化、延迟字段映射 | **IMAGE + MERGE** | 不暴露下单/账户 API；disabled 时 unavailable；TWS 失败诚实降级 |
| `src/doxagent/tools/factory.py` | 注册 TWS clients；新增 `market.daily_ohlcv`、`market.quote_snapshot`、`market.trade_tape` provider-neutral routes；IBKR-first，Twelve/yfinance/Finnhub 仅 fallback；availability metadata | **IMAGE + MERGE** | `default_real_tool_registry()` 与 Data MCP guide/list 一致 |
| `src/doxagent/agents/config.py` | C1/O4 等 allowlist 从直接 Twelve/yfinance/Finnhub 切到三个 `market.*` 工具 | **IMAGE** | agent 不再绕过 provider-neutral route |
| `src/doxagent/skills/registry.py` | market trace / macro skills 的 allowed tools 切到 `market.*` | **IMAGE** | skill 和 agent allowlist 交集非空 |
| `src/doxagent/workflows/initialization/shared.py` | legacy global research / market trace 的 market tools 切到 `market.*` | **IMAGE** | legacy workflow 仍可执行，fallback 保留在 factory 内部 |

### 5.2 配置、依赖和容器

| 文件 | 丢失改动 | 恢复源 | 验收重点 |
|---|---|---|---|
| `pyproject.toml` | `openai-codex==0.144.4`、`mcp==2.0.0`、`cryptography>=50,<51`、dev `types-jsonschema>=4.23,<5` | **IMAGE** | 依赖版本精确；不要升级 SDK |
| `uv.lock` | 上述依赖的完整锁文件，337 insertions/3 deletions | **IMAGE** | 与镜像 hash 相同；`uv sync --frozen` 成功 |
| `.env.example` | Codex/worker/capability/workspace/model/subagent；container isolation；Hybrid/egress/local mirror；published Storage；IBKR TWS localhost 配置；删除旧 IBKR HTTP key/base/cache 示例 | **PATCH + MERGE** | 不写真实 secret；backend-only Storage key 不暴露前端/worker |
| `docker-compose.yml` | dashboard Codex/Hybrid/Storage env；独立 `codex-worker` profile；named volumes；healthcheck；read-only rootfs、tmpfs、container isolation；workspace/Codex home 持久化 | **PATCH** | `docker compose config -q`；worker 只能写 workspace/home/tmp |
| `Dockerfile.dashboard` | `COPY codex_assets ./codex_assets` | **PATCH** | dashboard 镜像内存在 `/app/codex_assets/document1_v2` |

### 5.3 Dashboard 前端

| 文件 | 丢失改动 | 恢复源 | 验收重点 |
|---|---|---|---|
| `frontend/dashboard/src/lib/dashboard-types.ts` | Codex artifact、entity relation、future node、run summary、D1 bundle、artifact detail 类型 | **PATCH** | 与 backend payload 和 untracked view 对齐 |
| `frontend/dashboard/src/lib/dashboard-api.ts` | `codexDocument1Runs`、`codexDocument1Run`、`codexArtifact` 三个 API client | **PATCH** | endpoint 为 `/codex-runs...`；鉴权沿用 dashboard request |
| `frontend/dashboard/src/pages/research.tsx` | `VITE_CODEX_D1_V2_ENABLED` 分支和 `CodexDocument1View` | **PATCH** | flag off 不渲染；flag on 只读取 API、不读取 workspace path |

最终镜像的 `frontend_dist` 已确认含 `codex-runs` 三个 endpoint，可作为构建结果
反证；源代码仍以会话补丁为准。

### 5.4 文档、日志和既有测试

| 文件 | 丢失改动 | 恢复源 | 验收重点 |
|---|---|---|---|
| `dev_plan/workflow_v2/data_mcp_development.md` | 状态从“已完成需求澄清，可进入实现”改为“代码已完成；见 `data_mcp_implementation.md`”；把“本轮不实施”改为“契约保留，实际实现/偏差/验收见实现报告” | **PATCH，Data 会话 3371/3463** | 只改这两处，不重写整个设计文档 |
| `changelog` | Codex Phase 1-8、真实 SDK 修复、device login、持久化 secrets/full smoke、Data MCP、IBKR TWS、provider-neutral route、Hybrid migration、最终镜像共若干条记录 | **PATCH + 人工去重** | 不机械复现早期乱码/重复行；按最终语义合并到正确日期标题 |
| `tests/test_commercial_provider_tools.py` | 旧 IBKR HTTP mock 改成 `_FakeIbkrSession`；验证 TWS snapshot/history、transport、connect/close；启用 `IBKR_TWS_ENABLED` | **PATCH，Data 会话 3890/3895/3930** | 与镜像 `ibkr.py` 及 untracked `ibkr_tws.py` 一起通过 |

## 6. 88 个非语义 Ruff/行尾噪声文件

主会话曾执行：

```text
uv run ruff format src/doxagent ...
```

这一步范围过宽，导致大量 legacy D1/D2/runtime 文件显示为修改。取证结果：

- 75 个 Python 文件与最终镜像做 AST 比较后完全相同；
- 10 个文件没有任何 post-cutoff `apply_patch`，只出现在 broad Ruff 输出中；
- 3 个既有测试同样没有语义补丁，只被 formatter 触及。

后续恢复不得复制这些文件的镜像格式化版本，也不得重新运行 repo-wide Ruff
format。保留 `427ebab`/当前基线版本即可：

```text
src/doxagent/adapters/financial_services/data.py
src/doxagent/adapters/financial_services/executor.py
src/doxagent/adapters/financial_services/modules.py
src/doxagent/adapters/vibe_trading/executor.py
src/doxagent/adapters/vibe_trading/modules.py
src/doxagent/agents/market_trace/providers.py
src/doxagent/agents/runner.py
src/doxagent/agents/runtime/memory/archive.py
src/doxagent/agents/runtime/memory/context.py
src/doxagent/agents/runtime/memory/observations.py
src/doxagent/agents/runtime/memory/runtime.py
src/doxagent/agents/runtime/memory/state.py
src/doxagent/agents/runtime/react.py
src/doxagent/agents/runtime/runner.py
src/doxagent/annotations/citations.py
src/doxagent/annotations/processor.py
src/doxagent/audit/query.py
src/doxagent/blackboard/postgres_repository.py
src/doxagent/blackboard/repository.py
src/doxagent/blackboard/service.py
src/doxagent/context/builder.py
src/doxagent/context/schema.py
src/doxagent/gateway/gateway.py
src/doxagent/gateway/providers.py
src/doxagent/gateway/tracing.py
src/doxagent/model_usage/pricing.py
src/doxagent/model_usage/repository.py
src/doxagent/models/contracts.py
src/doxagent/models/documents.py
src/doxagent/monitoring/collectors.py
src/doxagent/monitoring/media_enrichment.py
src/doxagent/monitoring/repository.py
src/doxagent/monitoring/schema.py
src/doxagent/persistent_runtime/replay.py
src/doxagent/persistent_runtime/repository.py
src/doxagent/persistent_runtime/schema.py
src/doxagent/persistent_runtime/service.py
src/doxagent/persistent_runtime/workers.py
src/doxagent/postgres.py
src/doxagent/prompts/assembler.py
src/doxagent/runtime_scheduler/documents.py
src/doxagent/runtime_scheduler/loop.py
src/doxagent/runtime_scheduler/repository.py
src/doxagent/skills/__init__.py
src/doxagent/skills/injection.py
src/doxagent/stocktwits/client.py
src/doxagent/stocktwits/crawler.py
src/doxagent/stocktwits/repository.py
src/doxagent/tools/__init__.py
src/doxagent/tools/market_evidence.py
src/doxagent/tools/providers/alpha_vantage.py
src/doxagent/tools/providers/base.py
src/doxagent/tools/providers/benzinga.py
src/doxagent/tools/providers/bls.py
src/doxagent/tools/providers/doxatlas.py
src/doxagent/tools/providers/fed.py
src/doxagent/tools/providers/finnhub.py
src/doxagent/tools/providers/fred.py
src/doxagent/tools/providers/macro_industry.py
src/doxagent/tools/providers/polymarket.py
src/doxagent/tools/providers/sec.py
src/doxagent/tools/providers/tavily.py
src/doxagent/tools/providers/yfinance.py
src/doxagent/tools/real.py
src/doxagent/workflow_memory/compiler.py
src/doxagent/workflow_memory/policies.py
src/doxagent/workflows/__init__.py
src/doxagent/workflows/checkpoint_repository.py
src/doxagent/workflows/document1/builder.py
src/doxagent/workflows/document1/context.py
src/doxagent/workflows/document1/context_pack.py
src/doxagent/workflows/document1/validators.py
src/doxagent/workflows/document2/deterministic_findings.py
src/doxagent/workflows/document2/legacy_pipeline.py
src/doxagent/workflows/document2/legacy_promotion.py
src/doxagent/workflows/document2/legacy_quality.py
src/doxagent/workflows/document2/promotion.py
src/doxagent/workflows/document2/transaction.py
src/doxagent/workflows/global_research.py
src/doxagent/workflows/initialization/agent_dispatch.py
src/doxagent/workflows/initialization/audit.py
src/doxagent/workflows/initialization/mock.py
src/doxagent/workflows/initialization/orchestrator.py
src/doxagent/workflows/initialization/recovery.py
src/doxagent/workflows/output_validation.py
tests/test_horizontal_collection_contracts.py
tests/test_phase14_global_research_integration.py
tests/test_phase8_o4_market_trace.py
```

如果后续发现其中某文件出现真实测试缺口，必须先找到独立 patch/trace 证据，再把它
从噪声组提升为语义恢复项；不得因为“事故前 status 显示 M”就整文件覆盖。

## 7. 推荐的整体恢复顺序

### Phase R0：建立隔离恢复工作树

1. 从 `refs/rescue/20260810/original-427ebab` 建立新分支，例如
   `codex/recover-codex-hybrid-20260810`；
2. 在仓库外新建 worktree；
3. 验证 worktree HEAD 必须为原始 `427ebab` lineage，而不是 `de185c8`；
4. 当前主工作区和保护快照保持只读；
5. 记录 recovery worktree 初始 `git status`、tree hash 和磁盘位置。

### Phase R1：恢复仍存在的新增文件

从 `untracked_worktree/` 复制 76 个已核对文件到 recovery worktree。复制后：

1. 逐文件 SHA-256 与快照对照；
2. 排除 `.pytest-*` 测试运行目录；
3. 不复制 `recovery/**` 缓存目录；
4. 不复制真实 `.env` 到 Git index；只在 recovery worktree 本地使用；
5. 确认 Codex/Data/Observation/IBKR 新模块均仍为 additive path。

### Phase R2：恢复 12 个镜像权威文件

从 `dashboard_image/` 覆盖第 4.3 节列出的 12 个文件。覆盖后立即核对 SHA-256。

这些文件应整文件恢复，因为它们包含 Codex、Data MCP、IBKR、Hybrid 多条会话的
合并最终状态；只重放早期补丁会丢失后续 route 或 Storage 改动。

### Phase R3：按补丁恢复 9 个非镜像语义文件

按以下顺序人工合并：

1. `.env.example`：先 Data/IBKR，再 Codex/worker，再 Hybrid/Storage；
2. `docker-compose.yml`：先 codex-worker 服务，再 isolation，再 Hybrid/Storage env；
3. `Dockerfile.dashboard`：补 `codex_assets`；
4. 三个 frontend 源文件；
5. `data_mcp_development.md` 两处状态文字；
6. `tests/test_commercial_provider_tools.py` TWS fake session 测试；
7. `changelog`：按最终语义人工去重合并。

不要直接把 JSONL 中每个 changelog patch 原样连续套用；早期曾出现重复和乱码邻接，
最终恢复应只保留可读、唯一、按日期归类的正式记录。

### Phase R4：静态与契约验证

建议最小验证集：

```powershell
uv sync --frozen
uv run ruff check src/doxagent/codex_runtime src/doxagent/codex_worker `
  src/doxagent/data_runtime src/doxagent/observations src/doxagent/mcp `
  src/doxagent/horizontal_collection src/doxagent/workflows/codex_document1 `
  src/doxagent/dashboard_api/codex_document1.py src/doxagent/dashboard_api/app.py `
  src/doxagent/tools/factory.py src/doxagent/tools/registry.py `
  src/doxagent/tools/providers/ibkr.py src/doxagent/tools/providers/ibkr_tws.py `
  src/doxagent/tools/providers/market.py
uv run mypy src/doxagent/codex_runtime src/doxagent/codex_worker `
  src/doxagent/data_runtime src/doxagent/observations src/doxagent/mcp `
  src/doxagent/workflows/codex_document1
```

只对恢复目标运行 formatter check，不执行 `ruff format src/doxagent`。

### Phase R5：测试验证

```powershell
uv run pytest -q -p no:cacheprovider `
  tests/test_codex_runtime_v2.py `
  tests/test_codex_document1_workflow.py `
  tests/test_data_mcp_runtime.py `
  tests/test_horizontal_collection_execution.py `
  tests/test_ibkr_tws_bottom_layer.py `
  tests/test_commercial_provider_tools.py
```

必须覆盖：

- worker bearer/capability 和 workspace containment；
- strict output schema、retry/fresh thread、citation sanitization；
- Data MCP capability intersection、guide/list/call、O#、Pack、promotion；
- Hybrid SQLite evidence 与 Supabase runtime 分工；
- event 原子 sequence、checkpoint、published/unpublished 隔离；
- IBKR disabled/available/degraded 三态；
- provider-neutral route 和 fallback provenance；
- legacy workflow flag-off 非回归。

### Phase R6：前端与 Compose

```powershell
npm --prefix frontend/dashboard run typecheck
npm --prefix frontend/dashboard run lint
npm --prefix frontend/dashboard run test
npm --prefix frontend/dashboard run build
docker compose --profile codex-v2 config --quiet
```

构建产物应再次包含：

- `/codex-runs?ticker=...`；
- `/codex-runs/{run_id}`；
- `/codex-runs/{run_id}/artifacts/{artifact_id}`。

### Phase R7：本地数据库与真实 smoke

1. 复制 SQLite 快照，不直接对唯一原件测试；
2. `PRAGMA integrity_check` 必须为 `ok`；
3. 核对 schema version 2、sources=17、manifests=34；
4. 对 Supabase 只做 read-only schema/version/row-count 核对，不重复 migration；
5. 重建 dashboard 和 worker 镜像；
6. 先做一个极小 structured turn；
7. 再跑低成本 D1 smoke，验证 12/12 checkpoint、一次注入重试、citation、publish、
   final artifact 和重启读取；
8. 对照历史 smoke `codex-hybrid-smoke-20260810T0915Z` 的 published 状态、
   event sequence 9 和 12,295-byte 正文。

### Phase R8：形成可审计提交

恢复提交建议拆分：

1. `recover: restore codex d1 v2 additive runtime`；
2. `recover: restore data mcp and ibkr integrations`；
3. `recover: restore hybrid persistence and dashboard wiring`；
4. `docs: record git accident recovery and acceptance`。

每个提交都应只包含明确语义文件；88 个格式化噪声不得混入。完成验证前不 push、
不把 recovery branch fast-forward 到当前 `main`。

## 8. 恢复完成判据

只有同时满足以下条件，才可以认定恢复完成：

1. recovery branch 继承原始 `427ebab` lineage；
2. 76 个新增文件与保护快照一致；
3. 12 个镜像文件与记录的 SHA-256 一致；
4. 9 个 PATCH 文件通过人工 diff 审核；
5. 88 个格式化噪声未进入提交；
6. `.env` 和 secret 未被 stage；
7. targeted Ruff、mypy、pytest、frontend build、Compose config 通过；
8. SQLite 完整性和 Hybrid/Supabase 分工核对通过；
9. 极小 structured turn 与低成本 D1 smoke 通过；
10. legacy flag-off 路径无行为回归；
11. 最终 `git diff --check` 通过；
12. 用户审核恢复 diff 后，才决定如何替换或合并当前 `main`。

## 9. 当前未决风险

- `de185c8` 是 fast-import 重写历史，不能直接作为恢复基底；
- 最终 dashboard 镜像是 Python runtime 权威源，但不包含 frontend TypeScript 源码
  和 tests，因此这些文件必须使用 JSONL patch；
- provider-neutral market route 的部分动作可能来自共享工作区中的 agent loop，未以
  独立 parent patch 出现在主 JSONL；最终镜像和 untracked `market.py`/测试共同构成
  其恢复证据；
- changelog 曾出现早期重复/乱码邻接，必须人工语义合并；
- Supabase 已有真实数据，恢复代码时禁止顺手重跑 migration 或 backfill；
- 真实 `.env` 和 Windows user secrets 虽已快照，但不得写入恢复提交。

## 10. 总结

本次事故不是“全部代码丢失”。可恢复范围已经收敛为：

- 76 个仍存在且已双重快照的新增文件；
- 12 个由最终运行镜像逐字节恢复的 tracked 文件；
- 9 个由两条原始会话 patch 精确重建的 tracked 文件；
- 88 个应主动舍弃的 Ruff/行尾噪声文件。

因此后续不需要重新开发 Codex SDK、Data MCP、IBKR 或 Hybrid DB。正确路径是建立
隔离 recovery worktree，按“untracked snapshot -> image overlay -> patch merge ->
targeted validation -> smoke -> audited commits”的顺序整体恢复。
