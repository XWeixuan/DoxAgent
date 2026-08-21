# Global Research / Market Situation Research 双 Lane 重构方案

> 日期：2026-08-20  
> 状态：本地实现完成，待真实模型功能验收与远端迁移批准
> 适用范围：Codex SDK 新研究架构；旧 ReAct Run Type 和既有
> `codex_d1_v2` 历史运行保持可读、可审计，不做原地改名  
> 本文是后续命名、DAG、Prompt、Skill、Pilot 与持久化改造的当前权威口径。

## 一、目标口径

研究体系拆成两条可独立启动、独立运行、独立失败、独立发布的 lane：

1. **Global Research / Document 1**
   - 研究目标公司的基本面、行业与价值链、核心驱动因子和市场隐含预期；
   - 正式研究 Agent 为 C1、C3、C5；
   - C4 是结构化研究辅助节点，不是独立报告章节；
   - 不接收 C2、O4、Market Situation 产物或事件库上下文。
2. **Market Situation Research**
   - 研究当日及长周期的宏观/大盘环境和目标证券价格、相对表现、波动、流动性与走势；
   - Agent 为 C2、O4；
   - 不读取 Global Research、C1/C3/C5/C4 产物；
   - 首版只完成物理分离与独立运行，不扩展更复杂的内部编排。

两条 lane 可以共享 worker、Workspace API、Provider client、Observation Kernel、Data
MCP 实现和底层存储基础设施，但不得共享 run、checkpoint、thread、artifact handoff、输入
payload 或失败状态。共享代码不等于业务耦合。

## 二、当前实现与目标的差距

当前 `CodexDocument1Orchestrator` 的实际路径是：

```text
Program Collection
  -> C4 pre-scan
  -> parallel(C1, C2, C3, O4-B)
  -> Agent normalization
  -> C4 enrichment
  -> C4 finalization
  -> O4-A
  -> assemble / publish
```

主要问题：

- C2 和 O4-B 被写入 Document 1 正文与 bundle；
- O4-A 与 O4-B 共用 `CodexAgentRole.O4`，thread registry 的身份语义不清晰；
- `CodexD1Node`、worker request、Data MCP capability、artifact/checkpoint 均把两条未来
  lane 固化成 Document 1 节点；
- C4 enrichment 当前只被定义为补充未来节点，最终完整实体关系和未来节点依赖额外的
  finalization turn；若直接删除 finalization，现有 enrichment 未必返回完整快照；
- Horizontal Collection 会先遍历 C1、C2 和 O4 target，单纯从 assembler 删除章节不会
  实现成本、状态和输入上的独立；
- `base_context` 是开放字典，没有契约级机制阻止 Known Events、Event Registry 或其他
  事件库内容进入节点；
- Pilot、Prompt、Skill、方案文档和测试仍大量使用 O4-A、O4-B、C4 finalization；
- 当前 Pilot 常量把 `c4_finalization.json.json` 作为统一 C4 上游文件名，这是现存错误，
  不能带入新命名体系。

## 三、目标命名

| 当前名称 | 新名称 | 所属 lane | 说明 |
| --- | --- | --- | --- |
| C1 | C1 | Global Research | 公司基本面研究 |
| C3 | C3 | Global Research | 行业与价值链研究 |
| O4-A / `o4_a` | C5 / `c5` | Global Research | 市场隐含预期研究 |
| C4 pre-scan | C4 pre-scan | Global Research | 初始实体图与未来事项扫描 |
| C4 enrichment | C4 enrichment | Global Research | 完整合并、补充并产出最终 C4 快照 |
| C4 finalization | 删除 | 无 | 不保留别名，不再生成新 attempt |
| C2 | C2 | Market Situation | 宏观、大盘与金融环境研究 |
| O4-B / `o4_b` | O4 / `o4` | Market Situation | 个股价格、相对表现、波动、流动性和走势研究 |

共享 Agent Registry 的目标命名：

- 保留 `AgentName.O4_MARKET_TRACE = "O4"`，语义收窄为价格与市场状态研究；
- 新增 `AgentName.C5_MARKET_IMPLIED_EXPECTATIONS = "C5"`；
- 新增 `CodexAgentRole.C5 = "c5_researcher"`；
- `CodexAgentRole.O4 = "o4_researcher"` 只对应新的 O4；
- 历史 `o4_a` / `o4_b` 数据保持原值，读取时通过显式 legacy schema 解释，禁止在数据库
  中原地更新为 `c5` / `o4`。

## 四、目标 DAG

### 4.1 Global Research / Document 1

```text
internal Global horizontal collection
  -> C4 pre-scan
  -> parallel(C1, C3)
  -> internal C1/C3 observation normalization
  -> C5
  -> C4 enrichment
  -> deterministic assemble / citation merge / publish
```

规则：

- “C4 pre-scan 是第一个节点”指第一个研究 Agent turn；程序化 horizontal collection
  可以作为内部前置采集，但不得在报告、UI 或 checkpoint 中冒充研究 Agent；
- C1/C3 只接收 C4 pre-scan 的实体关系投影，不接收 future nodes；
- C5 接收 C1、C3 报告和必要的规范化 observation，以及 C5 自己的 horizontal 输入；
- C5 不接收 C2、O4、Known Events、Event Registry、C4 future nodes；
- C4 enrichment 接收 C4 pre-scan、C1、C3、C5，因此 C5 完成是其真实依赖而不只是
  人工排序；
- C4 enrichment 必须返回**完整最终快照**，包括继承/修正后的全部
  `entity_relations` 和合并去重后的全部 `future_nodes`，而不是只返回 delta；
- C4 enrichment 是最后一个 Agent turn；assemble 和 publish 是基础设施步骤，不算额外
  研究节点；
- Document 1 正文固定只拼接 C1、C3、C5。C4 结构化结果保存在 bundle/handoff，
  不生成空白正文章节。

### 4.2 Market Situation Research

首版最小 DAG：

```text
internal Market horizontal collection
  -> parallel(C2, O4)
  -> deterministic assemble / citation merge / publish
```

规则：

- C2 和 O4 首版互不读取对方报告；
- 任何一方失败只影响本 lane，不影响同 ticker 的 Global Research run；
- lane 使用独立 run ID、checkpoint、thread 和 bundle；
- 后续可以在不改变 Global Research 的前提下，为本 lane 添加盘前/盘中/盘后、长周期
  基线、增量监测或独立 reviewer。

## 五、事件库隔离合同

“事件库不注入 Document 1”必须落成输入合同，而不是 Prompt 提醒。

1. 新的 Global request 不再暴露任意 `base_context: dict`；改为有界的
   `GlobalResearchSeedContext`，只允许 issuer/profile、research constraints 和明确的静态
   company metadata。
2. 以下 key 及其别名不得进入任一 Global attempt：
   `known_events`、`event_library`、`event_registry`、`event_store`、
   `historical_events`、`monitoring_events`、DoxAtlas event/narrative payload。
3. C1/C3/C5 的 context projector 使用字段白名单，不能把调用方的未知 key 透传。
4. C4 pre-scan 到 C4 enrichment 的 `future_nodes` 是 C4 自己的 attempt-local 上游，
   不是外部事件库；它只在 C4 内部流转，不注入 C1/C3/C5。
5. 增加负向测试：即使调用方提交事件库字段，request validation 或 projector 也必须拒绝，
   并验证生成的每个 `context.json` 不含这些 key。

## 六、版本和兼容策略

本次是语义和 DAG 的破坏性变化，不能继续冒充同一个 `codex_d1_v2`。

建议版本：

- 旧路径：`codex_d1_v2`，保留历史读取和审计，迁移后停止新建；
- 新 Global：`codex_global_research_v1`；
- 新 Market：`codex_market_situation_v1`。

兼容边界：

- 不修改历史 checkpoint、attempt、artifact 的 node 文本；
- 不把历史 O4-A 自动映射为 C5，也不把历史 O4-B 自动映射为 O4；
- 旧 `/codex-runs` API 和 `Document1V2Bundle` reader 保留；新 run 使用新 endpoint 和
  新 bundle schema；
- 部署切换前先停止新建 `codex_d1_v2` 并排空 active run；旧 run 不跨版本 resume；
- 保留旧 ReAct 初始化 Run Type 的代码、数据和 API 行为。其 `GlobalResearchDocument`
  四报告结构标记为 legacy compatibility，不把新语义强塞进旧模型；
- 新增 `GlobalResearchHandoffV1` 和 `MarketSituationHandoffV1`。旧
  `Document1HandoffV1` 继续读取历史 run；D2 适配在后续显式选择新 handoff，不静默混用。

## 七、运行时代码修改面

### 7.1 共享契约与 worker

主要文件：

- `src/doxagent/codex_runtime/schema.py`
- `src/doxagent/codex_worker/schema.py`
- `src/doxagent/codex_runtime/repository.py`
- `src/doxagent/data_runtime/contracts.py`
- `src/doxagent/data_runtime/policy.py`
- `src/doxagent/data_runtime/execution.py`

修改要求：

- 引入 `ResearchLane`、版本化 workflow identity 和中性的 `CodexResearchNode`；
- `NodeAttempt`、`ArtifactRef`、`WorkflowCheckpoint`、`ThreadRecord`、worker request 和
  Data MCP claims 带 workflow/lane discriminator；
- capability 校验同时约束 lane、workflow version、node、role、attempt 和 ticker，防止
  C5 token 被 O4 或另一 lane 使用；
- 新增 C5 role/tool policy；O4 policy 收窄为价格面；C2 保持宏观面；
- 把当前 O4-A entitlement exclusions 迁到 C5 node policy；
- `src/doxagent/tools/providers/o4_market.py` 中实际为共享市场证据的实现改成中性命名
  `market_evidence.py`，避免 provider 层继续绑定旧 O4-A 语义。

不建议只给 `CodexD1Node` 添加 enum alias。alias 会让历史序列化值、新 checkpoint 和新
Data MCP capability 混在同一个类型中，后续无法判断 lane。

### 7.2 Global orchestrator

建议新目录：

```text
src/doxagent/workflows/codex_global_research/
  schema.py
  orchestrator.py
  assembler.py
  context.py
```

共享的 attempt bundle、node runner、citation promotion、upstream rebinder 和 workspace
生命周期下沉到中性 `codex_research_runtime/`。旧 `codex_document1/` 留作 v2 兼容层，
不要边改边让旧 run 走新 DAG。

Global orchestrator 的精确动作：

- 从 parallel specs 删除 C2、O4；
- 把 O4-A 执行段改成独立 C5 role/node；
- C5 成功后再调用 C4 enrichment；
- 删除 C4 finalization 执行、验证、checkpoint、retry 和 thread turn；
- bundle 的 relations/future nodes 直接取 C4 enrichment；
- normalization 只处理 C1/C3（如 C5 candidates 后续确有消费方，再单独版本化加入）；
- assembler 顺序固定为 C1、C3、C5；
- C4 pre/enrichment 两次 turn 复用 C4 thread；C5 使用独立 thread。

### 7.3 Market orchestrator

建议新目录：

```text
src/doxagent/workflows/codex_market_situation/
  schema.py
  orchestrator.py
  assembler.py
  context.py
```

首版仅负责 C2/O4 并行、独立 checkpoint/retry、引用合并和发布，不读取 Global bundle。

### 7.4 Agent Registry 与旧路径

主要文件：

- `src/doxagent/models/common.py`
- `src/doxagent/models/documents.py`
- `src/doxagent/agents/config.py`
- `src/doxagent/skills/registry.py`
- `src/doxagent/workflows/global_research.py`
- `src/doxagent/workflows/document1/*`
- `src/doxagent/workflows/initialization/*`

做法：

- 新增 C5 AgentName/config/skill binding；
- O4 config 删除 market-implied skill 的默认绑定，只保留价格面和既有 Document 3
  monitoring override；
- 新增 `DocumentType.MARKET_SITUATION_RESEARCH` 和对应新文档模型时，不删除旧
  `GlobalResearchDocument.macro_report/market_trace_report`；旧模型由 legacy 路径继续使用；
- 旧初始化 workflow 不在本轮强制改 DAG；在代码和文档上明确其 legacy 身份，避免
  后续会话把它当作新 Codex 架构。

## 八、Horizontal Collection 修改面

主要文件：

- `src/doxagent/horizontal_collection/registry.py`
- `src/doxagent/horizontal_collection/context.py`
- `src/doxagent/horizontal_collection/collector.py`
- `src/doxagent/horizontal_collection/generated_metric_catalog.py`

必须从“一个 registry 全量跑完”改成 lane profile：

- Global profile：C1、C3、C5 targets；
- Market profile：C2、O4 targets。

目标前缀：

- `c1_*`、`c3_*` 不变；
- 当前供 O4-A 使用的估值、隐含波动、定位、市场预期类 target 改为 `c5_*`；
- 当前供 O4-B 使用的价格、收益、相对表现、波动、流动性和技术类 target 统一为
  `o4_*`；
- C2 保持 `c2_*`。

同一个底层 quote/provider 可以被两个 lane 独立调用，但结果不能通过另一个 lane 的
workspace artifact 复用。若以后需要跨 lane 缓存，只能使用与 run 无关、按
provider+ticker+as_of+parameters 寻址的只读 provider cache。

## 九、Prompt、Internal Skill 与 Bundle 修改面

### 9.1 Agent Prompt

- 新建 `prompts/agents/c5.md`：从当前 O4-A 语义迁移为 C5；
- 重写 `prompts/agents/o4.md`：只保留 Market Situation 的价格研究和非 D1 的
  monitoring-policy override 兼容说明；
- 更新 `prompts/agents/c2.md`：所属文档改为 Market Situation Research；
- 更新 `prompts/agents/c4.md`：enrichment 读取 C1/C3/C5，并输出完整最终快照；
- 更新 C1/C3：跨域宏观或价格问题只能标记为独立 Market Situation handoff/Unknown，
  不能把 C2/O4 当作本 lane 依赖。

### 9.2 Internal Skill

- `market-implied-expectations.md` 可保留描述性 skill id，但全文和 metadata 的
  applicable agent 改为 C5；若希望文件名也完全对齐，可 git mv 为
  `c5-market-implied-expectations.md`，同时保留旧 id 的显式 legacy alias；
- `ticker_price_tracking.md` 全文从 Document 1/O4-B 改为 Market Situation/O4；
- `entity-map-and-future-nodes.md` 删除 finalization 模式，enrichment 明确为完整合并输出；
- `fundamental-research.md`、`industry-research.md` 删除“交给 C2/O4 继续本工作流”的表述；
- `source-discipline.md` 的 applicable agents 加 C5；
- `src/doxagent/skills/registry.py` 增加 C5，拆开 C5 与 O4 的默认 skill 注入。

### 9.3 Codex Assets

不要继续维护一个混合 `codex_assets/document1_v2/bundle_manifest.json`。目标为：

```text
codex_assets/global_research_v1/
  agents/{c1,c3,c4,c5}.md
  skills/...
  schemas/node_output.schema.json
  bundle_manifest.json

codex_assets/market_situation_v1/
  agents/{c2,o4}.md
  skills/...
  schemas/node_output.schema.json
  bundle_manifest.json
```

旧 `codex_assets/document1_v2` 保留用于读取/复现旧 run，不再生成新 case。

## 十、Pilot 修改面

主要文件：

- `src/doxagent/pilot/case_builder.py`
- `src/doxagent/pilot/templates.py`
- `pilot_runtime/prepare_case.py`
- `pilot_runtime/UPSTREAM_SETS.md`
- `scripts/install_codex_d1_pilot_runtime.py`

目标：

- CLI 增加 `--lane global_research|market_situation_research`；
- 新节点名为 `c1/c3/c4_pre_scan/c5/c4_enrichment` 和 `c2/o4`；
- Pilot task 标题不再把 C2/O4 称为 Document 1；
- 删除 C4 finalization quality profile、probe、case 路径和 task 文案；
- O4-A quality profile/Doctor/Data MCP 测试改为 C5；O4-B 改为 O4；
- 修复 `c4_finalization.json.json`，禁止再使用“统一 C4 JSON”歧义名。

人工上游白名单：

| 节点 | 允许文件 |
| --- | --- |
| C1 | `c4_pre_scan.json`（构建时只投影 entity relations） |
| C3 | `c4_pre_scan.json`（构建时只投影 entity relations） |
| C5 | `c1.md`、`c3.md` |
| C4 enrichment | `c4_pre_scan.json`、`c1.md`、`c3.md`、`c5.md` |
| C2 | 无上游 |
| O4 | 无上游 |

旧 Pilot case 保持不可变，不能批量改名；新 case 使用新目录和新 attempt/node 值。

## 十一、API、配置与前端修改面

后端主要文件：

- `src/doxagent/dashboard_api/codex_document1.py`
- `src/doxagent/dashboard_api/app.py`
- `src/doxagent/settings.py`
- `src/doxagent/codex_runtime/config.py`
- `.env.example`

建议新 API：

```text
POST /api/dashboard/v1/research-runs/global
POST /api/dashboard/v1/research-runs/market-situation
GET  /api/dashboard/v1/research-runs?lane=...&ticker=...
GET  /api/dashboard/v1/research-runs/{run_id}
POST /api/dashboard/v1/research-runs/{run_id}/cancel
POST /api/dashboard/v1/research-runs/{run_id}/retry
GET  /api/dashboard/v1/research-runs/{run_id}/events
GET  /api/dashboard/v1/research-runs/{run_id}/artifacts/{artifact_id}
```

旧 `/codex-runs` 保持 legacy reader/兼容入口。新 start request 不允许客户端通过 payload
伪造 lane 与 workflow version 的矛盾组合。

前端修改面：

- `frontend/dashboard/src/lib/dashboard-types.ts`
- `frontend/dashboard/src/lib/dashboard-api.ts`
- `frontend/dashboard/src/components/dashboard/codex-document1-view.tsx`
- `frontend/dashboard/src/pages/research.tsx`
- `frontend/dashboard/src/components/dashboard/document-view.tsx`

首版只需把 Global 与 Market 显示为两个独立卡片/状态源，不做 Market lane 的高级页面。

## 十二、SQLite / Supabase 持久化迁移

现有 Supabase schema 在三处强制 `workflow_version='codex_d1_v2'`：run registry、checkpoint
和 Document 1 bundle。不能只改 Python Literal。

迁移策略：

1. `codex_run_registry` 增加 `research_lane`，旧行回填 `legacy_document1`；
2. workflow version check 允许 legacy 和两个新版本；
3. checkpoint 同步增加 lane/version 一致性校验；
4. attempt/artifact 的 node 保持 text，但 repository 在读取时根据 run version 选择正确 enum；
5. 保留 `codex_document1_bundles` 供旧 run；新增小型
   `codex_global_research_bundles`、`codex_market_situation_bundles`；
6. 新 bundle 表只保存 ArtifactRef index、C4 小型结构化数组和 manifest/handoff ID；报告正文
   继续走既有 published document/Storage 路径，不把大文本塞入高频 Supabase 行；
7. 新增 `(research_lane, ticker, created_at desc, run_id desc)` 索引；
8. 新表沿用 private `doxagent` schema、enable+force RLS、撤销 anon/authenticated、仅
   service role 访问；
9. SQLite generic record 增加 lane/version discriminator，并保留旧 payload reader；
10. 更新 `scripts/backfill_codex_hybrid.py`，按 bundle type 分流，禁止把 Market bundle
    回填进 Document 1 表。

实施时使用新的 Supabase migration 文件，先在本地 stack reset/migration list 验证，再经
advisors 和显式批准部署远端；不直接编辑已应用的 20260808 migration，也不在本轮方案阶段
连接远端。

## 十三、文档治理

需要更新或建立 superseded 指针的当前方案：

- `dev_plan/workflow_v2/codex_sdk_migration.md`
- `dev_plan/workflow_v2/codex_sdk_migration_implementation.md`
- `dev_plan/workflow_v2/document1_codex_sdk_current_state_20260818.md`
- `dev_plan/workflow_v2/d1_horizontal_indicators_collection.md`
- `dev_plan/workflow_v2/d1_o4_a.md`（迁移为 `d1_c5.md`，旧文件保留短跳转说明）
- `dev_plan/workflow_v2/d1_c1.md`
- `dev_plan/workflow_v2/d1_c3.md`
- `dev_plan/workflow_v2/d1_new_research_section.md`
- `dev_plan/workflow_v2/data_mcp_development.md`
- `dev_plan/workflow_v2/data_mcp_implementation.md`
- D2 方案中把旧 C1/C2/C3/O4 总称改成新来源定义，但不在本轮实现 D2。

历史 acceptance、Pilot issue log 和运行报告不做全文替换。它们记录的是当时真实节点名；
只在仍可能被当作当前设计入口的文档顶部增加“历史口径 / 已被本文取代”说明。

## 十四、测试修改面和验收门

### 14.1 契约测试

- 新旧 workflow version 均可反序列化；
- 历史 `o4_a/o4_b/c4_finalization` 不被自动改写；
- 新 role/node/lane 组合非法时 capability 拒绝；
- C5 与 O4 thread key 永不相同。

### 14.2 DAG 测试

- Global 精确顺序：C4 pre -> parallel C1/C3 -> C5 -> C4 enrichment；
- 不产生 C2、O4、C4 finalization attempt；
- C4 两 turn 复用 thread；C5 独立 thread；
- C4 enrichment 输出是完整快照，bundle 直接使用它；
- Global 正文只有 C1/C3/C5；
- Market 精确只运行 C2/O4，且可在 Global 不存在/失败时独立发布。

### 14.3 上下文与事件隔离测试

- Global 所有节点不含 known events/event library/event registry；
- C1/C3 不含 C4 future nodes；
- C5 只含 C1/C3 和自身 horizontal；
- C4 enrichment 含 pre/C1/C3/C5，但不含 Market lane；
- Market context 不含 Global artifact 或 handoff。

### 14.4 Horizontal / MCP 测试

- Global collector 不调用 C2/O4 targets；Market collector 不调用 C1/C3/C5 targets；
- C5 暴露当前 O4-A 的经真实验收工具面；
- O4 暴露价格研究工具面；
- C2 保持宏观工具面；
- Doctor 对 C5/O4 分别通过，交叉 capability 被拒绝。

### 14.5 Persistence / API / UI 测试

- SQLite 和 Supabase repository 能同时读取 legacy/new bundles；
- lane 过滤分页索引路径稳定，payload/egress 上限不退化；
- 两个 start endpoint、retry/cancel/events/artifact 权限与 ETag 保持；
- 前端不会把 Market report 显示为 Document 1 章节。

### 14.6 真实低成本验收

开发验收和研究质量验收分开：

1. Global 结构化最小 turn，验证精确 DAG、checkpoint、引用和 C4 完整快照；
2. Market 低成本 C2/O4 并行 smoke，验证独立 run/publish；
3. 分别做 C1/C3/C5/C4 和 C2/O4 Pilot；
4. 研究质量优化另开任务，不以功能 smoke 结论替代。

## 十五、建议实施顺序

### Phase A：冻结语义和兼容边界

- 合入本文；
- 标记旧 current-state 文档 superseded；
- 冻结 `codex_d1_v2` 新建，保留 reader；
- 建立命名和上下文负向测试。

### Phase B：中性运行时契约

- 引入 lane/version/node/role；
- 泛化 worker、repository、capability 和 node runner；
- 保持旧 schema reader 测试通过。

### Phase C：Global lane

- 新 bundle/prompt assets；
- C5 与新 DAG；
- 删除新路径的 finalization；
- C4 enrichment 完整快照；
- event context allowlist。

### Phase D：Market lane

- C2/O4 独立 orchestrator、horizontal profile、bundle 和 API；
- 不增加高级编排。

### Phase E：Persistence/API/Frontend

- 本地 SQLite migration；
- 新 Supabase migration 本地验证后再单独批准远端；
- 新 API 和两个独立前端状态卡。

### Phase F：Pilot、文档和验收

- 新 Pilot 入口与人工上游映射；
- 修复双 `.json` 缺陷；
- 全量术语扫描；
- 两 lane 分别 smoke 和 Pilot。

## 十六、最终全局防遗漏门

实施完成后对非历史目录运行残留扫描：

```text
o4_a | O4-A | o4_b | O4-B | c4_finalization | C4 finalization
```

允许残留的位置仅限：

- legacy schema/adapter；
- 历史 migration；
- 历史 acceptance/Pilot/audit 文件；
- 明确标注为 legacy 的兼容测试。

任何新的 Prompt、Skill、bundle manifest、Pilot、API type、Dashboard label、Data MCP policy
或 current-state 文档中出现这些名称都视为验收失败。

## 十七、实施结果与明确延期项

截至 2026-08-20，本方案除下列延期项外已在本地代码、Prompt、Skill、Codex Assets、
Pilot、SQLite/Supabase migration、API 与 Dashboard 中落地；旧 `codex_d1_v2` reader/runtime
和历史节点值继续保留。Supabase migration 已在临时 PostgreSQL 15 基线库实际应用验证，
尚未推送远端。

本轮明确延期：

- 外部事件库的请求 schema allowlist 与负向隔离测试；
- Market Situation 的盘前/盘中/盘后和持续监控高级 DAG；
- D2 runtime 适配与质量验收；
- 远端 Supabase migration；
- 真实模型研究质量验收。

历史 artifact、attempt、Pilot case 和 acceptance 记录不会原地重命名。
