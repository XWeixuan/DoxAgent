# DoxAgent 当前状态同步：Document 1 v2 与 Codex SDK

> **已于 2026-08-20 被取代。** 本文保留为 `codex_d1_v2` 历史状态快照。当前 Global
> Research 只包含 C1/C3/C5 与 C4 两阶段，C2/O4 已进入可独立启动的 Market Situation
> lane；详情见 [`global_market_research_lane_rearchitecture_plan_20260820.md`](./global_market_research_lane_rearchitecture_plan_20260820.md)。

**日期：** 2026-08-18  
**当前版本：** `workflow_version=codex_d1_v2`

这份说明用于给 Document 2 的工作流设计提供最新上下文。重点是：

- Document 1 v2 已经迁移到独立的 Codex SDK 路径；
- 旧的 DoxAgent/Legacy Run Type 仍然保留，默认行为不变；
- Document 2 本轮没有迁移到 Codex SDK，只冻结了它未来接入 Document 1 的边界。

## 1. 最新 Document 1 v2 工作流

整体 DAG 如下：

```text
Program Collection / Horizontal State
                |
          C4 pre-scan
                |
   +------------+-------------+----------------+
   |                          |                |
  C1                         C2               C3              O4-B
公司基本面                 宏观环境          行业/产业链       市场事实与历史定价
   +------------+-------------+----------------+
                |
      Agent observation normalization
                |
          C4 enrichment
                |
          C4 finalization
                |
              O4-A
     定价逻辑与市场隐含情景
                |
       deterministic assembly
                |
             publish
```

### 各阶段产物

| 阶段 | 主要职责 | 主要产物/下游用途 |
|---|---|---|
| Program Collection | 先按指标和采集目标收集横向数据，并编译成带版本的 Horizontal State | collection manifest、target progress、horizontal bundle；供各研究节点按角色读取 |
| C4 pre-scan | 识别初步实体关系和未来事项 | 结构化 `NodeOutput`、报告/完成记录；作为 C1/C3 的上游上下文 |
| C1 | 当前基本面、近期变化、公司驱动、执行约束和业务变量传导 | 基本面研究 Markdown、结构化完成记录、候选观察值；不创建 Document 2 的 Expectation Unit 或 Gap |
| C2 | 宏观环境、增长/就业、通胀/政策、利率/流动性/汇率及其传导 | 宏观研究 Markdown、结构化完成记录和候选观察值 |
| C3 | 行业、价值链、外部参与者、产业驱动和商业化里程碑 | 行业/产业链研究 Markdown、结构化完成记录和候选观察值 |
| O4-B | 当前市场事实、历史价格变化、重定价事件、估值/相对表现 | 市场事实与历史定价 Markdown、结构化完成记录和候选观察值 |
| Agent normalization | 将 C1/C2/C3/O4-B 的候选指标归一化，区分 governed metric 与 agent-found metric | 一个上下文/规范化观察产物；不是新的研究报告 |
| C4 enrichment | 在 C4 pre-scan 基础上吸收 C1、C3 和规范化观察 | enriched C4 结构化完成记录和报告 |
| C4 finalization | 去重并定稿实体关系与未来事项 | 最终 `entity_relations` 和 `future_nodes`；对外字段严格受限，各行只保留 5 个公开字段 |
| O4-A | 综合 C1/C2/C3/O4-B 与最终未来事项，解释市场定价、隐含业务/财务/时间情景和不确定性 | O4-A 市场隐含预期研究 Markdown、结构化完成记录和候选观察值 |
| Assembly | 确定性拼接各节点报告 | `artifacts/document1/document1_v2.md`，固定顺序为 C1、C2、C3、C4 final、O4-B、O4-A |
| Citation/Publish | 合并各 attempt 的 citation manifest，校验 SHA-256 后发布 | 最终正文、citation manifest、published artifact metadata 和 D1 handoff |

C4 实际上是同一个角色的三次连续 turn：pre-scan → enrichment → finalization。O4-B 和 O4-A 也共享同一个 O4 角色 thread。C1、C2、C3 各自独立执行。

所有节点统一返回受限的结构化 `NodeOutput`，其中可包含：`status`、`summary`、`report_markdown`、`warnings`、`observation_candidates`、`entity_relations` 和 `future_nodes`。渐进式 Markdown 节点还会持续刷新 `report_draft.md` 与 `progress.json`，最终 `report_markdown` 必须与草稿一致。

## 2. Codex SDK 现在如何实现和串联

### 服务拓扑

```text
Dashboard/API
     |
     v
Document 1 v2 Orchestrator
     | 负责 DAG、checkpoint、重试、artifact 和发布
     v
独立 codex-worker HTTP 服务
     |
     v
Python AsyncCodex / pinned Codex CLI runtime
     |
     +--> attempt-scoped Data MCP
     +--> Source Capture MCP
     +--> run-scoped workspace volume
```

API 通过显式的 `workflow_version=codex_d1_v2` 进入这条路径。Orchestrator 不直接依赖宿主机文件系统，而是通过 worker 的 Workspace API 读写 run workspace；因此本地和远端 Docker 部署使用同一套文件边界。

### 一个节点是怎样执行的

1. Orchestrator 为节点创建 `NodeAttempt`，编译当前节点的不可变 context、任务文件、AGENTS、skill 和 horizontal 输入，并计算输入 SHA-256。
2. Orchestrator 向 worker 提交 `WorkerRunRequest`，携带 `run_id`、ticker、node、role、attempt、模型、reasoning effort、结构化 output schema 和当前 prompt。
3. worker 使用官方 `AsyncCodex` 启动或恢复 thread，再执行一个 turn。SDK 使用 `deny_all` approval；普通节点使用受限 workspace sandbox，容器隔离时由 Docker 提供外层边界。
4. worker 按当前 node/role/ticker 签发短期 Data capability，并在该 turn 中挂载 Data MCP 与可选 Source Capture MCP。Data MCP 只暴露该节点允许的工具，观察结果写入 attempt-local `O#` 命名空间。
5. SDK 返回结构化 JSON。Orchestrator 校验 `NodeOutput`、渐进式文件、候选指标和 citation，再写入 report artifact、structured completion、audit 和 thread/turn/job metadata。
6. 节点成功后 checkpoint 前进；后续节点只接收明确的 artifact/context handoff，不依赖隐含的模型记忆。

默认模型配置是 OpenAI Codex 登录对应的 `gpt-5.6-luna`，reasoning effort 为 `max`；provider 可以通过配置显式切换。C1、C3、O4-A 可开启最多 2 个 subagent，其他节点不开放该能力。

### Thread、重试和恢复

- thread identity 绑定 `workflow_version + ticker + run_id + agent_role`，不能跨 ticker 或跨 run 隐式继承历史。
- C4 的三次 turn 共用 C4 role thread；O4-B/O4-A 共用 O4 role thread；失败重试会创建新的 attempt，必要时使用 fresh thread。
- 每个节点最多按配置重试；相同 input SHA-256 的已成功节点可以直接恢复已有 artifact，不重复调用模型。
- checkpoint、attempt、artifact、event、usage、thread 和 citation manifest 都是显式持久化对象。Thread 只是可恢复的执行句柄，不是业务事实的唯一来源。
- workspace 的 `context/` 和已发布目录不可覆盖；文件读写、inventory、publish、export、delete 都经过 run/operation capability 校验，并附带 checksum。

### 当前存储边界

本地默认使用 SQLite 保存 Codex runtime 状态；hybrid 模式下高频、可能较大的证据和 attempt 数据继续保留在本地，远端只保存受控的结构化运行状态和已发布文档元数据/正文。Supabase/远端存储不是模型上下文，也不是替代 workspace 的隐式文件系统。

## 3. Document 2 当前到底处于什么状态

Document 2 仍走原有 Legacy workflow 和数据行为。本轮没有把 Expectation Unit、Gap、realization、review、repair 或 promotion 迁移到 Codex SDK，也没有要求 D1 节点生成这些对象。

当前代码冻结的 D1→D2 最小 handoff 是 `document1-handoff-v1`，包含：

```text
run_id
ticker
document1_artifact_id
citation_manifest_artifact_id
published_at
```

已发布的 `Document1V2Bundle` 另外可以取得各节点 artifact refs、最终 `entity_relations`、最终 `future_nodes` 和 citation manifest。未来设计可以扩展 handoff，但必须采用显式版本化字段和 ArtifactRef，不应读取某个 Codex thread 的未发布隐含内容。

因此，Document 2 设计时需要先决定：

- 是只消费已发布的 D1 handoff，还是同时消费 C1/C2/C3/O4-A 的独立 artifact；
- 如何把 D1 的公司/行业/宏观/市场事实转换为 Expectation Unit 的候选证据，而不把 D1 Markdown 直接当成已验证的预期对象；
- cutoff、source/citation、identity 和后续 realization 的持久化边界；
- Document 2 是否需要独立 Codex threads，还是只让 D2 worker 读取显式 context snapshot；
- Legacy D2 的 API、评测入口和非回归约束如何保持不变。

## 4. 讨论时应遵守的几个约束

1. 不要把当前状态描述成“整个 DoxAgent 已迁移”；准确说法是“Legacy 保留，同时新增 Codex SDK Document 1 v2 路径”。
2. 不要让 D2 依赖 thread history、未发布 artifact 或模型上下文中没有落盘的结论。
3. 不要让 C1/C2/C3/O4 节点直接创建 D2 的 Expectation Unit、Gap 或投资建议。
4. 研究质量评估、模型上限和性能评估与开发/运行合同验收分开进行。

相关实现入口：

- `src/doxagent/workflows/codex_document1/orchestrator.py`
- `src/doxagent/workflows/codex_document1/node_runner.py`
- `src/doxagent/codex_worker/sdk_runtime.py`
- `src/doxagent/codex_worker/app.py`
- `src/doxagent/codex_runtime/schema.py`
- `codex_assets/document1_v2/`
