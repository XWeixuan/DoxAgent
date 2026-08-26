# DoxAgent 当前实现同步：Document 1、Document 2、事件库与 Codex Research Lanes

> **更新时间：2026-08-25**
>
> 本文已从 2026-08-18 的旧版 current-state 快照更新为当前实现说明。旧版
> `codex_d1_v2` 的节点值、运行记录和读取路径仍然保留；本文不改写历史
> attempt、artifact、Pilot case 或 acceptance 报告中的旧名称。
>
> 当前新增运行应区分两条 lane：Global Research（Document 1 新研究路径）和
> Market Situation Research（独立市场情势路径）。后续讨论架构、Prompt、Skill、Pilot、
> API 或数据库时，以本文和
> [`global_market_research_lane_rearchitecture_plan_20260820.md`](./global_market_research_lane_rearchitecture_plan_20260820.md)
> 的已实现部分为准；该方案文档中“待迁移/未验收”的历史表述不代表当前运行状态。
>
> Document 2 Expectation workflow 与 Event Library/O2 是独立的 Codex SDK 工作流；它们以
> 显式发布边界或冻结视图作为业务输入。Document 2 的 O0 domain review 另有意恢复对应的
> Document 1 role thread，以保留 review 所需的上游隐藏上下文；该 thread 仍是审查执行句柄，
> 不是替代已发布 artifact 的事实边界。本文以下部分同时记录这两条工作流的当前实现和与
> Document 1 的接入边界。

## 1. 当前版本与边界

| 路径 | workflow version | lane | 当前用途 | 兼容策略 |
|---|---|---|---|---|
| Legacy Document 1 | `codex_d1_v2` | `legacy_document1` | 读取和复现历史 D1 v2 路径 | 保留旧 DAG、旧 node 值、旧 bundle/API reader，不原地重命名 |
| Global Research | `codex_global_research_v1` | `global_research` | 目标公司的基本面、行业/价值链、核心驱动与市场隐含预期 | 当前 Document 1 新研究入口 |
| Market Situation Research | `codex_market_situation_v1` | `market_situation_research` | 当日及长周期的大盘/宏观环境、个股价格面、相对表现、波动与流动性 | 与 Global Research 平行、可独立启动和发布 |
| Document 2 Expectation Research | `codex_document2_v1` | `document2` | 将已发布 Global Research 转换为 Expectation Shell、Unit、State、Realization Factor 与 Potential Gap | 只消费显式 `GlobalResearchHandoffV1`；每个 Shell 有独立 O1 工作区和可恢复 thread |
| Event Library / O2 | `codex_event_library_v1` | `event_library` | 从 CDECR Delta 维护 ticker 级 Canonical Event/Fact Library，并发布版本化只读视图 | 独立维护工作流；D1 不直接调用，D2 仅通过可选只读 provider 接入 |

新旧路径共享 Codex worker、Workspace API、Data MCP、Source Capture MCP、Observation
Kernel、provider client 和 repository 基础设施，但不共享同一次 run 的 checkpoint、thread、
attempt、artifact handoff、输入 payload 或失败状态。共享代码不代表业务编排耦合。

当前 `CodexD1Node` 仍包含历史值 `o4_a`、`o4_b`、`c4_finalization`，这是为了反序列化和
审计历史运行；新 lane 使用独立的 `c5` 和 `o4`。历史 `O4-A/O4-B` 不在数据库中原地改名。

## 2. 当前实际编排

### 2.1 Global Research / Document 1

程序化 Horizontal Collection 是内部前置步骤，不算研究 Agent turn。当前真实 DAG 为：

```text
Global horizontal collection（c1/c3/c5 targets）
        |
    C4 pre-scan
        |
   +----+----+
   |         |
  C1        C3       （并行）
   +----+----+
        |
Agent observation normalization（内部步骤）
        |
        C5
        |
 C4 enrichment（最后一个 Agent turn）
        |
 deterministic citation aggregation
        |
  assemble / publish
```

节点顺序和输入关系如下：

| 节点 | 当前职责 | 当前输入/输出边界 |
|---|---|---|
| C4 pre-scan | 初步实体关系和未来事项扫描 | 输出结构化 `NodeOutput`；作为 C1/C3 与 C4 enrichment 的显式上游 |
| C1 | 公司基本面、近期变化、管理层/卖方口径、核心驱动、执行约束和业务到财务传导 | 只接收 C4 pre-scan 的 `entity_relations` 投影，不接收 pre-scan `future_nodes`；输出 Markdown、completion 和候选观察 |
| C3 | 行业、价值链、外部主体、分配机制、产业驱动与商业化里程碑 | 只接收 C4 pre-scan 的 `entity_relations` 投影；不接收 future nodes；输出 Markdown、completion 和候选观察 |
| Agent normalization | 归一化 C1/C3 候选观察，区分 governed metric 与 agent-found metric | 内部结构化步骤，不产生研究报告章节 |
| C5 | 原 O4-A 语义迁移后的市场隐含预期研究 | 接收 C1/C3 报告、C1/C3 规范化观察和自己的 c5 horizontal 输入；不接收 C2、O4 或 C4 future nodes |
| C4 enrichment | 读取并补充 pre-scan，合并 C1/C3/C5 的研究结论 | 必须返回完整最终 `entity_relations` 与 `future_nodes` 快照，不是 delta；直接作为 Global bundle 的结构化结果 |
| Assemble | 确定性拼接正文并聚合引用 | 正文只包含 C1、C3、C5；C4 结构化结果不生成空白章节 |
| Publish | 校验 artifact/citation、写入 bundle/handoff 并发布 | 生成 `global_research_v1.md`、citation manifest 和 `GlobalResearchHandoffV1` |

C4 enrichment 是最后一个研究 Agent turn；新 Global 路径不执行 C4 finalization，也不生成
新的 C4 finalization attempt。C4 pre-scan 与 enrichment 使用 C4 role 的 run-scoped thread
语义；C1、C3、C5 分别使用独立 role/thread identity。

Global assembler 的固定正文顺序是：

```text
C1 基本面研究 -> C3 行业与价值链研究 -> C5 市场隐含预期研究
```

### 2.2 Market Situation Research

当前首版只实现物理分离和独立运行：

```text
Market horizontal collection（c2/o4 targets）
        |
   +----+----+
   |         |
  C2        O4       （并行）
   +----+----+
        |
Agent observation normalization
        |
 deterministic citation aggregation
        |
  assemble / publish
```

- C2 负责宏观、大盘、增长/就业、通胀/政策、利率/信用/流动性/汇率及其传导。
- O4 负责个股价格快照、多窗口收益、重定价区间、相对表现、波动、定位/流动性和未知项。
- C2 与 O4 首版互不读取对方报告，也不读取 Global Research bundle。
- Global Research 的失败不影响 Market Situation；Market Situation 的失败也不影响 Global。
- Market assembler 只拼接 `C2 大盘与宏观环境` 和 `O4 个股价格面与走势`。
- 当前还没有盘前/盘中/盘后持续监测、增量 watch loop 或独立 reviewer DAG；这些属于后续
  Market lane 方案，不应写入当前实现状态。

### 2.3 Document 2 / Expectation Research

Document 2 当前使用 `codex_document2_v1`、`document2` lane。输入必须来自已发布的
`GlobalResearchBundle` 与 `GlobalResearchHandoffV1`，并通过 checksum 校验读取 C1、C3、C5
报告、entity relations、Future Nodes、horizontal collection 和 D1 citation manifest。O0 的
C1/C3/C5 domain review 会恢复对应的 Global Research role thread，保留 D1 review 所需的
隐藏上下文；除此之外，Document 2 不以任意 thread history 或未发布 artifact 替代显式
handoff，也不读取 Market Situation bundle。

Document 2 的完整编排为：

```text
Input Preparation
        |
        +--> O0 Candidate Discovery（C1 / C3 / C5；Narrative 可选并行）
        |
        +--> O0 Shell Synthesis
        |
        +--> C1 / C3 / C5 Domain Review（恢复对应 D1 role thread）
        |
        +--> O0 Shell Finalization
                     |
          +----------+----------+
          |                     |
     Shell A / O1          Shell B / O1 ...
          |                     |
   STATE -> REALIZATION -> GAPS -> FINALIZATION
          +----------+----------+
                     |
          deterministic assembly / citation manifest
                     |
                 publish Document2
```

O0 先从各研究域发现候选，再进行 Shell/Unit 结构综合、领域 review 和全局 finalization；最终
只向 O1 交付已 review 的 Shell topology 与 Unit seeds。每个最终 Shell 都对应独立的 O1
workspace/run 和可恢复 thread，Shell 之间可并行研究，但同一 Shell 的四个 turn 始终按同一
thread 顺序执行：

- `STATE`：为每个 Unit 建立可持续更新的 Parameter、Source-role Value、`time_scope`、
  `as_of` 与 validity state；
- `REALIZATION`：拆解影响 Unit 能否实现、何时实现、由谁获得价值以及如何转化为财务结果的
  Realization Factors；
- `GAPS`：开放未来事件空间，记录一旦发生就会迫使当前 Unit 修订的 Potential Gaps；
- `FINALIZATION`：统一 State、Factor、Gap 的边界、语义、时间和引用，返回完整 canonical
  `ExpectationShell`。

O1 返回的是可持续维护的结构化 expectation model，不是从 D1 报告复制出的叙述性章节。每
个 turn 都刷新 `artifacts/shell.json` 和 checkpoint；最终由确定性 assembler 生成
`document2.json`、Markdown、citation manifest 和 `Document2HandoffV1`。引用解析和单个
Shell 失败不应阻止其他合法 Shell 产物发布；所有 Shell 失败时仍可发布 `PARTIAL`，同时在
outcome/checkpoint 中保留失败边界。

Narrative Research 仍读取 DoxAtlas 已完成的旧 narrative report；运行时只在 ticker 存在
七日内完成报告时注入。Event Library 也是可选输入，当前默认 provider 为 `NOT_CONFIGURED`；
这两类可选输入缺失都不改变 O1 的基本编排。

### 2.4 Event Library / O2 维护工作流

Event Library 使用独立的 `codex_event_library_v1`、`event_library` lane 和 `O2_MAINTAIN`
节点。它不把 CDECR Runtime Registry 当作 Canonical Event Library，也不让 O2 直接写 SQLite。
业务边界为：

```text
Ticker CDECR Pipeline
    -> FINALIZED Runtime epoch
    -> Atomic Delta Compiler
    -> O2 Frozen View
    -> O2 Event Library Maintainer
    -> Revision Bundle validation
    -> Working Revision import
    -> Published version / exported views
```

Canonical Event Library 是 ticker 级独立 SQLite，内部区分 `Pending Delta`、`Working Revision`
和 `Published Revision`。CDECR Runtime Package 只提供 occurrence 候选提示，不能直接决定
Canonical Event 的合并、拆分或去重；Atomic Delta `D#` 才是 O2 的 disposition 单位。

O2 的冻结输入是一次运行内不变的 `Frozen View`，包括完整 `Known Event Index`、本轮 Pending
Delta、Runtime Package/Delta 的精简匹配提示、可按稳定 Event ID 展开的 Event Detail，以及
需要复审的 Reference candidates。O2 在 Codex workspace 的 `output/work/` 中起草，在
`output/revision_bundle/` 中提交 Event-per-file 的完整目标修订；模型只返回小型
`O2RunResult`，不拥有任意 SQL、Published head 切换或物理删除权限。

初始化模式按 `SURVEY -> LOCAL_RECONSTRUCTION` waves -> `GLOBAL_RECONCILIATION` 完成全量
occurrence 重建；增量模式按 `READ_FULL_INDEX -> BUILD_CANDIDATE_MAP -> LOAD_EVENT_DETAILS
-> RECONSTRUCT_AND_EDIT -> REFERENCE_REVIEW` 处理新 Delta 和到期复审。随后 Dedicated O2
Runner 做确定性 Bundle 校验、copy-on-write 导入和整数 `published_version` 切换，导出
`Known Event Index` 与 `Reference Event View`。局部事件问题可以转为 Pending 或跳过；只有
版本冲突、SQLite 事务失败等会阻止尚未发布的 Working Revision，上一版 Published Revision
保持可读。

Event Library 的消息新旧判定由 Persistent Runtime 负责，CDECR 不另建 Message Bus consumer
或重复判定；CDECR 只接收已经确认的新非社媒消息批次。事件库工作流目前独立于主 D1 DAG 和
定时器。D2 只允许通过 `PublishedEventLibraryReader`/`EventLibraryProvider` 读取已发布、
版本化的只读视图，不能读取 Pending/Working 状态；当前 D2 默认仍为 `NOT_CONFIGURED`，因此
事件库尚未成为每次 D2 运行的必需输入。D3 消费路径不属于本文范围。

## 3. Context、handoff 与事件库边界

主编排采用显式 payload handoff；只有 Document 2 的 O0 domain-review 阶段按合同恢复对应的
Document 1 role thread，以保留 review 所需的隐藏上下文。thread 不是跨 lane 的通用事实源：

- C1/C3 的 payload 含 `c4_pre_scan`，但运行时会剔除 `future_nodes`，只保留关系投影。
- C5 的 payload 含 C1、C3 结构化/报告 handoff 和规范化观察；不由 orchestrator 注入 C4
  future nodes、C2、O4 或 Market bundle。
- C4 enrichment 的 payload 含完整 C4 pre-scan、C1/C3/C5 报告和规范化观察。
- Market C2/O4 的 payload 只有本 lane 的 request context 与本节点 horizontal 输入。
- `NodeOutput`、`ArtifactRef`、`NodeAttempt`、`WorkflowCheckpoint`、`ThreadRecord`、
  `WorkerRunRequest` 和 Data capability 都带 `workflow_version`/`research_lane`，用于防止
  跨 lane、跨角色或跨 run 使用。

**Document 1 尚未完成的边界：** Global request 当前仍保留 `base_context: dict[str, Any]`，没有完成
方案中要求的 request schema allowlist/负向隔离。因此“事件库不注入 Document 1”目前是
编排约束和 Prompt/asset 约束，不是完整的代码级输入拒绝合同。`known_events`、
 `event_library`、`event_registry` 等字段隔离仍是明确延期项；后续实现不能把当前状态描述
为已完成的事件库隔离。

这段 Document 1 的延期边界不等于 Event Library/O2 自身未实现：O2 已有独立的 Frozen View、
Revision Bundle、validator、import/publish 和 Published reader；未完成的是 Document 1 的
request 级负向隔离，以及 D2/D3 的业务消费者接入。

D2 的业务事实输入只消费显式发布边界。当前 Document 1 lane 的 handoff 形态如下；其中
MarketSituationHandoffV1 仅供 Market Situation lane，不能作为 Document 2 输入：

```text
GlobalResearchHandoffV1:
  schema_version, run_id, ticker, document_artifact_id,
  citation_manifest_artifact_id, published_at

MarketSituationHandoffV1:
  schema_version, run_id, ticker, document_artifact_id,
  citation_manifest_artifact_id, published_at
```

Document 2 当前已经使用 `codex_document2_v1` Codex SDK workflow。除 O0 domain review 按合同
恢复对应的 D1 role thread 外，它不得从任意 thread history、未发布 artifact 或模型上下文
直接读取 D1 结论，也不得把 C1/C3/C5 的 Markdown 自动当作已验证的 Expectation Unit、Gap
或 realization 对象；这些对象必须由 O0/O1 按 Document2 schema 重新构建。

## 4. Codex SDK 服务拓扑与节点生命周期

```text
Dashboard/API
    |
    v
CodexResearchLaneService
    |
    +--> CodexGlobalResearchOrchestrator
    +--> CodexMarketSituationOrchestrator
    +--> CodexDocument2Orchestrator
    +--> EventLibraryFoundationOrchestrator / Dedicated O2 Runner
              |
              v
       lane-specific Codex SDK runner
              |
              v
       independent codex-worker HTTP service
              |
              v
       official AsyncCodex / pinned openai-codex runtime
              |
              +--> attempt-scoped Data MCP
              +--> optional Source Capture MCP
              +--> run-scoped Workspace volume
```

`CodexGlobalResearchOrchestrator`、`CodexMarketSituationOrchestrator`、
`CodexDocument2Orchestrator` 和 Event Library runner 共享 Codex worker、Workspace、repository
和 provider 基础设施，但各自拥有独立 workflow version、lane、node/role、attempt、checkpoint
和输入合同。Dashboard 侧以 asyncio task 启动已接入的 research run；Document2 与 Event Library
也可由各自 coordinator/runner 启动。模型 turn 在独立 `codex-worker` HTTP 服务中执行。当前
Docker 服务为 dashboard `8780`、worker `8791`；本地最终镜像已经构建并健康运行，远端应用
服务器尚未同步。

Global/Market/Document2 的一个节点 attempt 的真实流程：

1. 生成带 lane/version 的 `NodeAttempt`，重绑定显式上游 payload。
2. 从对应 lane 的 bundle manifest 读取 agent prompt、internal skill、AGENTS 和 schema，
   生成不可变 `task.json`、`context.json`、horizontal 输入及必要的人工上游文件，并计算
   `input_sha256`。
3. 发送带 run/ticker/node/role/attempt/cutoff/model/effort/output schema 的
   `WorkerRunRequest`。
4. worker 在 run-scoped cwd 中启动/恢复 SDK thread，使用 `deny_all` approval；Data MCP
   依据 node/role/ticker 签发短期 capability，Source Capture MCP 仅作为可选服务挂载。
5. 读取结构化 JSON，校验 `NodeOutput`、progressive 文件或 C4 structured completion、
   candidate 文件和当前 attempt citation。
6. 写入 report/completion/audit/thread/usage/artifact，并推进 checkpoint；失败按配置创建
   新 attempt，必要时使用 fresh thread。

当前默认配置（未被环境变量覆盖时）为：

```text
model              = gpt-5.6-luna
reasoning_effort   = max
node_timeout       = 1800 seconds
node_max_attempts  = 2
max_subagents      = 2
```

C1、C3、C5 允许最多 2 个 subagent；C2、O4 和 C4 不开放 subagent。OpenAI Codex 登录是
默认 provider；`codex_model_provider` 可显式切换 provider。

Thread identity 的业务键是 `workflow_version + ticker + run_id + agent_role`。同一 run 内
C4 的两个新 Global turn 共享 C4 role identity；C5 使用自己的 C5 role；新 Market O4
使用 O4 role，不与 C5 混用；Document2 的 O0 C1/C3/C5 domain review 恢复对应的 Global
Research role thread，而每个 Shell 的 O1 research 使用独立 workspace/role thread；Event
Library 的一个 O2 run 在所有维护阶段恢复同一 O2 thread。Thread 只是可恢复执行句柄，业务
事实仍以 artifact、bundle、Frozen View、Published Revision、checkpoint 和 citation
manifest 为准。

## 5. Workspace、MCP 与安全边界

Dashboard 不直接依赖宿主机文件系统；跨服务文件操作都通过 worker Workspace API，路径
相对于已经校验的 `run_id`。当前 Workspace 合同包括：

- 受限 read/write、SHA-256 metadata 与 optimistic checksum；
- run/attempt 级 inventory、export、publish；
- atomic write 和 immutable context/published 路径；
- delete 只能作用于经校验的 attempt 目录；
- bearer token 加 run/operation capability 双层授权。

worker 以非 root Docker 用户运行，Codex SDK 使用 run directory 作为 cwd、受限 workspace
sandbox 与 `deny_all` approval。Data MCP 结果写入 attempt-local Observation store，
高文本证据不会直接变成高频远端数据库 payload；Source Capture MCP 的失败不应伪装成有
效证据。

## 6. Horizontal Collection 与 Data policy

Horizontal Collection 已按 lane 拆成独立 target registry：

| lane | target 前缀 | 语义 |
|---|---|---|
| Global Research | `c1_*`、`c3_*`、`c5_*` | 基本面、行业/价值链、市场隐含预期 |
| Market Situation | `c2_*`、`o4_*` | 宏观/大盘、价格、收益、相对表现、波动、流动性 |

C5 的指标来自原 O4-A 的市场隐含预期面，O4 的指标来自原 O4-B 的价格研究面。底层
provider 可以相同，但一次 run 不通过另一 lane 的 workspace artifact 复用结果。

Data capability 同时校验 workflow、lane、node、role、attempt 和 ticker；错误的 C5/O4、
Global/Market 组合应被拒绝。工具 registry 和 provider 实现已将市场证据抽象为中性的
`market_evidence`，避免在新路径继续绑定 O4-A/O4-B 语义。

## 7. Prompt、Skill、Codex Asset 与 Pilot

新 lane 不再使用混合的 `prompts/codex_v2/document1/compatibility/legacy_document1` bundle：

```text
prompts/codex_v2/document1/global_research/
  bundle_manifest.json
  -> c1, c3, c4_pre_scan, c5, c4_enrichment

prompts/codex_v2/document1/market_situation/
  bundle_manifest.json
  -> c2, o4
```

两个 manifest 的 `resource_sources` 直接指向当前 canonical workspace：
`prompts/codex_v2/document1/agents/{c1,c2,c3,c4,c5,o4}.md` 与
`prompts/codex_v2/document1/skills/` 下的对应 skill。这样更新 prompt/skill 后，新的 attempt
会在 bundle seed 阶段读取当前文件并纳入 input hash，不需要手工维护过时的静态副本。

当前新 lane Pilot 身份和人工上游白名单为：

| lane/节点 | 允许人工粘贴的上游 |
|---|---|
| Global C1 | `c4_pre_scan.json`（运行时仅投影 entity relations） |
| Global C3 | `c4_pre_scan.json`（运行时仅投影 entity relations） |
| Global C5 | `c1.md`、`c3.md` |
| Global C4 enrichment | `c4_pre_scan.json`、`c1.md`、`c3.md`、`c5.md` |
| Market C2 | 无上游 |
| Market O4 | 无上游 |

旧 Pilot case 继续不可变；新 case 使用新 lane、workflow version 和 node 值。人工上游的
JSON 会经过 `NodeOutput` 校验，旧 attempt 的 `O#` 会被视为失效，正式结论必须在当前
attempt 重新核验。Pilot task 会显式注入当天 EST 日期（`yyyy-mm-dd`）以及“报告内容和
结论在该时间点仍具参考价值，不要给出过时结论”的时序约束。

## 8. API、前端与配置

新 lane API 位于 `/api/dashboard/v1/research-runs`，并要求 Dashboard auth：

```text
POST /global
POST /market-situation
GET  /
GET  /{run_id}
POST /{run_id}/cancel
POST /{run_id}/retry
GET  /{run_id}/events
GET  /{run_id}/artifacts/{artifact_id}
```

列表支持 `lane`、`ticker`、cursor 和 limit；artifact 返回 ETag，并只允许读取已发布
artifact。Global 与 Market 在 Dashboard 中是两个独立的状态/报告分支，不把 Market 报告
显示为 Document 1 章节。

当前本地 `.env` 已启用：

```text
DOXAGENT_CODEX_D1_V2_ENABLED=true
DOXAGENT_CODEX_RESEARCH_LANES_ENABLED=true
DOXAGENT_CODEX_RUNTIME_STORAGE_MODE=hybrid
DOXAGENT_CODEX_REMOTE_RUNTIME_STORAGE_ENABLED=true
DOXAGENT_CODEX_HYBRID_LOCAL_MIRROR_ENABLED=true
```

worker bearer 和 capability secret 只进入本地/容器环境，不写入文档或前端 payload。

## 9. 持久化与最终 migration 状态

本地 Codex runtime SQLite schema version 为 3。Hybrid repository 的边界是：

- 本地 SQLite/workspace：attempt、context、report、completion、Observation/source、
  citation、workspace 文件和高文本证据；
- Supabase/Postgres：run registry、checkpoint、thread/attempt/artifact 等受控运行状态、
  lane bundle 的小型 report index、C4 结构化 relations/future nodes，以及已发布文档
  的元数据/正文引用。

已新增并应用/核验 `supabase/migrations/202608200001_codex_research_lanes.sql`：

- `codex_run_registry`、`codex_workflow_checkpoints` 增加 `research_lane` 并增加
  workflow/lane 一致性约束和 lane+ticker 索引；
- 新增 `doxagent.codex_global_research_bundles`；
- 新增 `doxagent.codex_market_situation_bundles`；
- 新表限制 JSON payload 大小，启用并强制 RLS，撤销 anon/authenticated，仅 service role
  访问；
- `GlobalResearchBundle` 和 `MarketSituationBundle` 分开保存，不能回填进旧
  `codex_document1_bundles`。
- Event Library 的 Canonical 业务状态保留在 ticker 级独立 SQLite（Pending Delta、Working
  Revision、Published Revision）；CDECR Runtime Registry 与 Canonical Event Library 分离。
  O2 通过冻结视图和 Revision Bundle 工作，不直接执行任意 SQL 或切换 Published head。

报告正文不作为高频、大 payload 的 bundle 行写入；发布服务仍通过 checksum 校验，必要时
使用私有 Supabase Storage。远端应用服务器代码/镜像本轮没有同步，当前验证范围是本地
Docker dashboard/worker 与远端 Supabase 数据库 migration。

## 10. 真实运行与验收状态

2026-08-20/21 已使用当前 canonical prompt/skill、真实 Data MCP、`gpt-5.6-luna max`
完成一次正式 MU Global Research run：

```text
run_id       = mu-global-formal-20260820-01
workflow     = codex_global_research_v1
observed DAG = C4 pre-scan -> C1/C3 parallel -> C5 -> C4 enrichment -> publish
status       = published
```

五个研究节点均首轮成功，无 C2/O4、C4 finalization 或事件库节点。最终报告约 89 KB，
包含 25 条 entity relations 和 26 个 future nodes。完整记录见
[`eval/codex_global_research_mu_formal_run_20260821.md`](../../eval/codex_global_research_mu_formal_run_20260821.md)。

这里的“无事件库节点”只描述该次 Global Research run 的观测 DAG；Event Library 当前由
独立的 O2 workflow 维护，未被这次 Global Research run 调用。

真实运行终检暴露了旧聚合路径的 citation 问题：历史产物中一个 C3 alias `O660` 未解析，
并且旧 assembler 允许不同 node attempt 复用裸 `O#`。当前代码已修复新 lane：

- 非 legacy lane 的 unresolved citation 在节点层触发失败/重试，不能继续发布；
- final assembly 按 `(attempt_id, local_alias)` 确定性重编号为全局唯一 alias；
- 只改写 aggregate document copy，保留 attempt-local report 和已发布历史产物不变。

本次运行的功能、节点传递、checkpoint 和产物生成通过；本次历史文档本身的引用正式门禁
不通过。修复后的新 lane 通过 35 项聚焦回归、Ruff、mypy，以及对真实产物的离线 citation
重放验证。研究深度、模型质量上限、token/墙钟性能仍与开发/运行合同验收分开。

另一个后续优化观察是 C4 pre-scan 在真实 run 中生成了约 670 条 attempt-local
Observation；它没有突破远端 payload 边界，但应在高频生产前做上下文体积和耗时优化。

## 11. 明确保留与延期项

已实现：

- 双 lane versioned schema、node/role/capability/checkpoint/artifact discriminator；
- Global 与 Market 独立 orchestrator、horizontal profile、bundle、API 和 Dashboard 分支；
- C5/O4 语义拆分及 canonical prompt/skill/asset；
- C4 enrichment 完整快照和新 lane citation 聚合门禁；
- Document 2 `codex_document2_v1` 的 O0 Shell Construction、领域 review、per-Shell O1
  四阶段 research、确定性 assembly/publish 与非阻塞 citation manifest；
- Event Library `codex_event_library_v1` 的 Frozen View、O2 initial/incremental runner、
  Revision Bundle validator、SQLite import/publish 和 Published reader；
- Pilot lane identity、人工上游注入、当前日期注入和持久化 case 兼容；
- SQLite v3、Supabase lane/bundle migration、hybrid repository；
- Docker 独立 worker、Workspace API、Data MCP/Source Capture MCP 和真实 MU 功能 run。

明确未完成或不属于本文当前实现：

1. Global request 的事件库字段 allowlist/负向隔离（当前 `base_context` 仍为开放 dict）。
2. Market Situation 的持续监测、盘前/盘中/盘后编排和独立 reviewer。
3. Document 2 的生产质量/真实运行验收、结果深度优化，以及与 Dashboard/Document 3 的后续
   消费者接入；当前 `codex_document2_v1` runtime、O0/O1 schema 与编排已经实现。
4. 研究质量优化、模型上限建立以及独立性能评测。
5. 远端应用服务器的代码、镜像和 Docker volume 同步。

## 12. 主要实现入口

- Global DAG：`src/doxagent/workflows/codex_global_research/orchestrator.py`
- Market DAG：`src/doxagent/workflows/codex_market_situation/orchestrator.py`
- Document 2 DAG：`src/doxagent/workflows/codex_document2/orchestrator.py`
- Document 2 input/assembly/schema：`src/doxagent/workflows/codex_document2/inputs.py`、
  `src/doxagent/workflows/codex_document2/assembler.py`、
  `src/doxagent/workflows/codex_document2/schema.py`
- Event Library/O2 runner：`src/doxagent/workflows/codex_event_library/remote_runner.py`、
  `src/doxagent/workflows/codex_event_library/orchestrator.py`
- Event Library published reader：`src/doxagent/event_library/provider.py`
- Legacy/D1 shared lifecycle：`src/doxagent/workflows/codex_document1/orchestrator.py`
- Shared node execution：`src/doxagent/workflows/codex_document1/node_runner.py`
- Version/lane/schema：`src/doxagent/codex_runtime/schema.py`
- Runtime repository：`src/doxagent/codex_runtime/repository.py`
- Lane API/service：`src/doxagent/dashboard_api/research_lanes.py`
- SDK worker：`src/doxagent/codex_worker/sdk_runtime.py`
- Canonical bundle manifests：`prompts/codex_v2/document1/global_research/`、
  `prompts/codex_v2/document1/market_situation/`
- Document 2 prompt/skill：`prompts/codex_v2/document2/`
- Event Library/O2 prompt/skill：`prompts/codex_v2/event_library/`
- Pilot lane builder：`src/doxagent/pilot/case_builder.py`
- Supabase migration：`supabase/migrations/202608200001_codex_research_lanes.sql`
- Lane regression tests：`tests/test_codex_research_lanes.py`、
  `tests/test_codex_document1_workflow.py`
