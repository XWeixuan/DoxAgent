# DoxAgent V2 API 源模型审查记录

- 审查日期：2026-09-07。
- 对应 [API Contract](DOXAGENT_V2_API_CONTRACT.md) `2.0.0-draft.1`。
- checkout HEAD：`e00a3cefcb7667ea29fe42aa8280e98f832b80e0`；工作区存在大量既有修改，本审查使用包含这些修改的实际文件，HEAD 不能代表本次模型快照。
- 方法：静态核对 V2 入口、节点输入/输出类型、编排、持久化与投影、当前 HTTP 接线，并与前端数据需求及 PRD Part 1 §4.6 逐项比对。没有执行 workflow、启动服务、调用模型/broker、连接业务数据库或改变业务状态。
- [原生模型清单](api_contract/native-model-inventory.json) 提供源文件 SHA-256、类位置、声明字段/类型/默认值、枚举及 validator 位置。它是静态 AST 清单，**不是**可执行 JSON Schema，也不是 public DTO；继承约束和业务 validator 仍以引用源码为准。

## 1. 主链与节点数据流

### 1.1 Ticker 初始化

入口：[catalog.py](../../src/doxagent/ticker_initialization/catalog.py)、[research_adapter.py](../../src/doxagent/ticker_initialization/research_adapter.py)、[activation_adapter.py](../../src/doxagent/ticker_initialization/activation_adapter.py)。

```text
             d1 ──────┐
                      ├─ o2 ─ d2 ─ d3 ─ o4.configure ─ o4.deliver ─ o4.register
             cdecr ───┘           │                         │
                各正式产物引用 ───┴─────────────────────────┘
                                    ↓
                         activation.prepare → activation.commit
                                    ↓
                               bus.ready → runtime.ready
```

准确依赖：o2 等 d1+cdecr；d2 等 d1+o2；d3 等 d2+o2；activation.prepare 等 d1+o2+d2+d3+register；bus.ready 等 prepare+commit；runtime.ready 等 prepare+bus.ready。图用于理解，完整依赖以 default_plan() 为准。

| 层 | 原生输入/输出 | API 决策 |
|---|---|---|
| 初始化请求/父 | RunRecord：initialization_id、ticker、research_cutoff_at、semantic_day、status、phase、monitor_mode、operation_kind、base_revision、state_seq、manual_resume_required | 不把内部 monitor_mode 直接当三种前端模式；父状态仅 QUEUED/RUNNING/SUCCEEDED/FAILED |
| 动态节点 | NodeSpec/NodeRecord：key/block/dependencies、generation、execution_id/version、receipt、result、error | 六步稳定归并；inputs/receipt 不进入页面摘要 |
| 交接结果 | NodeResult：artifacts、quality_annotations | PARTIAL/DEGRADED 是质量，不独立改变父门禁 |
| 恢复 | repository.resume、substeps 中的 managed execution | same initialization_id，当前失败集合，成功节点不重跑 |
| 激活 | revision_id、base_revision、artifacts，worker ACK | runtime_activation_id；选定引用不再分别查询 latest |

初始化控制仓库有 attempt/event，但 NodeRecord 自身没有明确 first_started_at/completed_at；UI 六步需要从真实事件/尝试结算取得。云摘要 outbox 限定字段、failed_nodes 只取前10，不能承担完整失败集合 API。

ResearchInitializationAdapter 显式 local-first：SQLite Codex repository、禁用远程大正文 mirror；激活 prepare 会确认 D1/D2 本地正式正文及 checksum、Event Index/Policy Projection、W3 readiness，commit 后等待真实 Bus/Runtime ACK。

### 1.2 D1 / Global Research

来源：[Global orchestrator](../../src/doxagent/workflows/codex_global_research/orchestrator.py)、[共用运行模型](../../src/doxagent/codex_runtime/schema.py)、[NodeOutput](../../src/doxagent/workflows/codex_document1/schema.py)。

| 节点 | 输入 | 输出及正式存储身份 |
|---|---|---|
| program_collection | ticker、cutoff、Global lane horizontal targets | horizontal context/observations；不是报告正文 |
| C4_PRE_SCAN | 基础研究上下文 | NodeOutput 的 entity_relations/future_nodes；给 C1/C3 的 relation-only handoff 不带预扫 future_nodes |
| C1 / C3 并行 | C4 relations、各自 horizontal context | report_markdown、observations、warnings，ArtifactRef + NodeAttempt |
| agent_normalization | C1/C3 observations | NormalizedAgentObservation，非独立用户报告 |
| C5 | C1/C3 报告、normalized observations、horizontal | report + observations |
| C4_ENRICHMENT | 前述研究成果 | 完整 merged relations/future_nodes |
| assemble / publish | 成功报告与 citation manifests | GlobalResearchBundle、GlobalResearchHandoffV1、PublishedDocument、CodexRunSummary |

GlobalResearchBundle：reports map、entity_relations、future_nodes、citation_manifest、handoff、created_at/published_at，**无 updated_at**。FutureNode 源字段为时间、未来事项、与目标公司的关系、来源、来源发布日期，无稳定业务 ID。NodeOutput.status 是 string，不能直接充当前端枚举。

当前 Global lane failed_nodes 非空阻止最终 Bundle 发布。单项是否能返回必须检查 ArtifactRef.published/PublishedDocument，不能只因工作目录有 Markdown 就给“已发布”。

另有独立 Market Situation lane（C2/O4 并行），其 O4 不等于监测配置 O4；它不进入本期四项基础投研页面。若它的真实 V2 模型调用在所选审计范围内，仍按该 lane 独立记账。

### 1.3 CDECR → O2

来源：[CDECR integration contracts](../../src/doxagent/cdecr_integration/contracts.py)、[workflow_runner](../../src/doxagent/cdecr_integration/workflow_runner.py)、[runtime_factory](../../src/doxagent/cdecr_integration/runtime_factory.py)、[Event Library contracts](../../src/doxagent/event_library/contracts.py)。

| 层/阶段 | 主要数据模型 | 与 UI 的关系 |
|---|---|---|
| 历史采集 | HistoricalLoadReport、SourceMessage、TickerJobState | 仅初始化进度/诊断，不混入 Runtime Standard Message 统计 |
| 单文档处理 | PreprocessedDocument/SourceSegment → DreamerOutput → GrounderOutput → JudgeOutput → NormalizationDecision → SingleDocumentResult | Source evidence、mention 与事实候选，不是 Canonical Event/Facts |
| 字段共指 | FieldCoreferenceInput/Candidate/Result、CanonicalFieldLink/RegistryEntry | 内部语义链接，前端不直接读这些大集合 |
| 原子共指/迟归并 | AtomicEvent、AtomicAssignmentDecision/Record、AtomicLateDecisionBatch、AtomicIdentitySidecar | Registry 内原子身份；不能把内部 event_id 与 Canonical E# 混为一类 |
| 父事件/Package | ParentInduction/Resolution、FrozenParentPartition；PackageWorkflowV3Result、FrozenPackagePartitionV3、EventPackage | 内部候选和冻结分组；不是前端 Canonical Library |
| 冻结接口 | CDECRWorkflowResult、FrozenRuntimeSnapshot、FrozenRuntimeAtomic、RuntimePackageSnapshot | 必须 FINALIZED 才冻结；无 eligible 文档可 FINALIZED_NOOP |
| Delta 编译 | DeltaBatch、DeltaItem、RuntimePackageDelta、OccurrenceDateCandidate | 为 O2 分配 D#，保留 source_message_ids、subject_time 与发生日期候选 |
| O2 上下文 | O2UpstreamContextManifest、FrozenViewManifest | 固定 D1 C1/C3/C5、CDECR epoch/snapshot、输入 cutoff |
| O2 Agent | O2RunResult/State、CandidateMap、SurveyDeltaCatalog、WaveIndex、CanonicalRevisionBundle | Agent 返回 BUNDLE_READY 不是发布成功；经验证、导入、发布才有正式库 |

CDECR runner 复用 CLI 的 SingleDocumentProcessor 与 BulkEpochEngine；其内部模型协议保留多种版本，不能因为枚举存在就认为该阶段本轮被执行。前端只呈现父级成果，成本则依据实际模型调用身份归属节点。模型清单保留这些内部类型以便后续映射核查，不把它们设计成额外页面 API。

O2 主业务模型：CanonicalEvent 含 E#、ticker、title、event_type、occurred_at/precision、status、canonical_summary、known_event_summary、is_important、include_in_reference_view、relations、facts、price_analysis。CanonicalFact 含 F#、proposition、assertion_state、subject_time、fact_occurred_at/precision。Fact 生命周期不在该对象字段内，而在 repository states/membership。

时间与状态分离：subject_time 是事实所指对象期，不是披露发生时刻；include_in_reference_view=false 不等于 Canonical 失效；Runtime Provisional E# 不能自动作为正式 Canonical E# 读取。

### 1.4 D2 / O0 / O1

来源：[schema](../../src/doxagent/workflows/codex_document2/schema.py)、[orchestrator](../../src/doxagent/workflows/codex_document2/orchestrator.py)、[assembler](../../src/doxagent/workflows/codex_document2/assembler.py)。

O0 candidate 分支 C1/C3/C5/可用 Narrative → CandidateDiscoveryResult；synthesis → ProvisionalShellDraft；C1/C3/C5 domain review → DomainReviewResult；finalization → ExpectationShellSeed。各 Shell O1 顺序 STATE → REALIZATION → GAPS → FINALIZATION，Shell 间可并行。

| 正式模型 | 关键关联与字段 |
|---|---|
| Document2Document | schema=document2.v2、document2_run_id、ticker、as_of、source_global_run_id、input_manifest、shells、shell_outcomes |
| ExpectationShell | shell_id、core_question、boundary_rule、units |
| ExpectationUnit | expectation_id、proposition、horizon、state、realization_factors、potential_gaps |
| ExpectationState | parameters 与 values，通过 parameter_id 关联；value_type 决定六种 value union |
| RealizationFactor | factor_id、condition、structural_role、current_status、impact、citation、observability.match_condition |
| PotentialGap | gap_id、possible_occurrence、derivation、citation、expected_revision、recognition_criteria |
| ShellOutcome | completed/failed、artifact_id、failed_stage、failure_kind、error_code/error、seed |
| Bundle/Handoff | published COMPLETE/PARTIAL、citation_status、document2_artifact_id、source_global_run_id |

PARTIAL 可能不被 Bundle.current 自动选中，但初始化可复用 published PARTIAL；API 当前页因此必须取 active pin。citation manifest 分 RESOLVED/UNRESOLVED/INVALID；不能只给 alias 就宣称完整证据。

### 1.5 D3 / O3 与监测 O4

来源：[D3 schema](../../src/doxagent/workflows/codex_document3/schema.py)、[runtime_projection](../../src/doxagent/workflows/codex_document3/runtime_projection.py)、[O4 schema](../../src/doxagent/workflows/codex_monitoring_o4/schema.py)。

D3 初始化：input preparation → TriggerCalibrationRecord/State → Policy compile → ReviewResult/SemanticDiagnostics → validate/assemble/publish；维护输入 O3MaintenanceFeed，输出 PolicyPatchSet（upsert_policies、retire_policy_ids），经验证形成 PolicySet。

Policy 的完整字段只有 policy_id/title/source_refs/decision/match_scope/activation_conditions；原生无 lifecycle 与完整 Policy revision 字段，不能向前端声称它本来就有。source_refs={shell_id,expectation_id,gap_id}；condition={condition_id,criterion,calibration:{reference_state,trigger_boundary}}。固定 OR，没有可切换 ALL 模式。

RuntimePolicyProjection 为 v4，Policy record 内 activation_revision 是 OR conditions+calibration 的 hash，**不是**整体 Policy hash；PolicyActivationRecord 以 ticker+policy_id+activation_revision 消费。API 给这三类 revision 不同名字。

O4 CONFIGURE：MonitoringConfigurationPlan/SourceNeedPlanItem；DELIVER：DeliveryCheckpoint/WorkItem 与 DeliverySettlement；REGISTER 将候选配置接入待激活范围。某个 delivery item 失败可以形成 degraded settlement，不等于父节点必然失败。REPAIR 有 schema 与操作能力，但当前持久编排只在初始化触发 O4；本期 Repair KPI 按产品固定0。

## 2. Message Bus、持久运行与交易

### 2.1 Message Bus

来源：[schema](../../src/doxagent/message_bus_v2/schema.py)、[service](../../src/doxagent/message_bus_v2/service.py)、[scheduler](../../src/doxagent/message_bus_v2/scheduler.py)、[content](../../src/doxagent/message_bus_v2/content.py)。

SourceDefinition → TickerSourceBinding → PollState/RawMessage → StandardMessage → StreamMember/StreamItem → ConsumerOffset。binding_id 为 ticker:source_id；raw 精确重复与内容 Revision 分开；standard body 在正式创建后不可变。buffered item 可包含多个 Standard member。

PollStatus=never_polled/succeeded/partial/failed/disabled。last_latency_ms 是轮询墙钟时长；target_due_at 与 next_dispatch_at 不同。TickerMonitoringState.started_at 不自动等于“当前连续区间”起点。

ArticleContentMaterializer 复用一个既有提取库函数，但没有因此读取其他代数据库；API lineage 根据 V2 Raw/Standard/Stream 身份验证。补全 metadata 不天然证明全部 attempt 分母。

### 2.2 Runtime

来源：[schema](../../src/doxagent/persistent_runtime_v2/schema.py)、[service](../../src/doxagent/persistent_runtime_v2/service.py)、[router](../../src/doxagent/persistent_runtime_v2/router.py)、[coordinator](../../src/doxagent/persistent_runtime_v2/coordinator.py)。

| 节点/阶段 | 模型与关键约束 |
|---|---|
| 接入 | SourceMessageEnvelope + 仅含 ticker/title/body 的 SourceMessageSnapshot；buffered envelope 使用末成员身份，API 需要补全所有成员关系 |
| 冻结输入 | RuntimeVersionPin 固定 activation_revision_id、D1/D2 run、event_library_root/version、PolicySet/provisional 版本；execution bundle 固定 prompt/skills |
| W1 R1 | W1Round1Result.event_ids 高召回 |
| W1 R2 | W1NoveltyResult={NEW/OLD,normal/low,reference_ids,reason}；OLD 要有引用 |
| W2 R1/R2 | W2PolicyResult={policy_ids,matched_condition_ids,confidence,reason}；最多3 Policy，条件归因是 advisory |
| Router | ARCHIVE/TRADE/ADD_TO_DELTA/W3；side_effects 可多项，BADCASE 不是主路由 |
| W1 R3 | RuntimeFactCandidate 列表，含 proposition/assertion/subject_time/occurrence_date/entities；signature 用于稳定去重 |
| W3 | W3RouteCase + W3CaseResult：novelty/policy/expert_trade/delta_candidates；回落到同一主路由词表 |
| 落账 | RuntimeEffect、ArchiveRecord、BadcaseRecord、TradeRecord、PolicyActivationRecord、ProvisionalFactDetail |
| 模型记录 | RuntimeModelTurn 的 lane/round/attempt/status/model/provider、nullable input/output/cached、latency_ms/output/error、created_at |

REALTIME 并行运行 W1/W2。CLOSED 顺序处理，W1 OLD/normal 可 w2_skipped；不能把每次运行都画成两次真实并行调用。RuntimeCase 没有专门 completed_at；updated_at 会重复写入。RuntimeTerminalProjection 仅有小摘要，并不包含新 UI 图所需全部结果/时刻。

### 2.3 日结、休市维护与最终选择

来源：[daily](../../src/doxagent/persistent_runtime_v2/daily.py)、[maintenance](../../src/doxagent/persistent_runtime_v2/maintenance.py)、[selection](../../src/doxagent/persistent_runtime_v2/selection.py)、[event_branch](../../src/doxagent/persistent_runtime_v2/event_branch.py)。

日结：候选冻结 → DeltaBatch → O2 publication → ReferenceViewDeltaSnapshot + trades/badcases/coverage gaps → O3 patch → activation CAS。DailyCloseRun 保存阶段及引用；新编排 journal 还保存各 task/input/receipt/gap、source sweep roster、高水位与候选选择。

休市每天有界 source sweep，W1/W2/W3 形成候选；最终 SelectionResult={selection_id,candidate_id|null,reason} 只释放0或1项。历史缺口可被隔离，健康任务继续；不允许用 source gap 推断整个 ticker 必然阻塞。

Event Library 是 copy-on-write 分支，base 回退后可能出现不同 root 中相同整数版本；源 pin 中 root 很关键。公开契约用 library_snapshot_id 与稳定 event_key/fact_key，不能把 root 暴露前端或只按 version 查询。

### 2.4 Trade Executor

来源：[trade_output](../../src/doxagent/persistent_runtime_v2/trade_output.py)、[ExecutionRepository](../../src/doxagent/trade_execution/repository.py)、[executor](../../src/doxagent/trade_execution/executor.py)、[schema](../../src/doxagent/trade_execution/schema.py)。

Runtime READY intent → frozen execution_pin → admit/EXECUTION_ACCEPTED → ENTRY job/attempt → effective fills → owned lots/allocations → EXIT job。te_profiles/executions/jobs/attempts/events/fills/lots/allocations 保存不同层事实；它们不是同一个 TradeRecord。

entry_result 可为 FILLED/PARTIAL_FILLED/FAILED/DIRECTION_DISABLED。同 fill family correction 选最高修订；quantity/price 为 decimal。fees 回执记录在 execution_fees journal；不能把 broker realized_pnl 直接当 DoxAgent 归属净收益。te_allocations 区分 ENTRY/EXIT/FIFO_OFFSET，PRD Overview 本期只计实际 EXIT。

ExecutionRepository.activate 仍写全局 trade_execution/active；没有足以证明不同 ticker 同时固定不同环境的公开事实。API 的 ticker 模式是明确要求补足的结果能力，不假装已实现。

## 3. 当前后端、用量与云投影

| 来源 | 已核对内容 | 契约影响 |
|---|---|---|
| [dashboard app](../../src/doxagent/dashboard_api/app.py)、[real_router](../../src/doxagent/dashboard_api/real_router.py)、[research_lanes](../../src/doxagent/dashboard_api/research_lanes.py) | 当前挂载 `/api/dashboard/v1` 与 research runs；部分 Bus 控制已用 V2 | 当前路由不等于新独立 API contract，不沿用旧 DTO |
| [real_service](../../src/doxagent/dashboard_api/real_service.py) | `_message_bus_v2_item` 返回 summary、固定 completed、runtime_execution_id=null | 与新消息卡片/路由关联要求不符 |
| [auth](../../src/doxagent/dashboard_api/auth.py) | Supabase Bearer、开发者 principal | 沿用身份能力，公开契约不暴露服务密钥 |
| [model_usage/schema](../../src/doxagent/model_usage/schema.py) | ModelUsageEvent input/output/total 默认0，无独立 cached 字段，metadata/raw_usage 可有来源值 | 必须区分未返回与真实0 |
| [codex_worker/schema](../../src/doxagent/codex_worker/schema.py) | WorkerTokenUsage 默认0；WorkerJob telemetry/turn_id/job_id | 累计或重放回执不能按新 call 重计 |
| [runtime projection](../../src/doxagent/persistent_runtime_v2/projection.py) | terminal/daily 小投影和 outbox | 不提供全文/实时全图；summary.updated_at 不是 completed_at |
| [D3 repository](../../src/doxagent/workflows/codex_document3/repository.py) | PolicySet/current、metadata、version reservation/candidate | candidate 不自动等于已生效生命周期 |
| [Event repository](../../src/doxagent/event_library/repository.py) | event/fact revisions/states/membership；reference_view_deltas | 具备版本事实，但结构化日增量仍要准确投影 |
| [初始化 summary migration](../../supabase/migrations/20260907061350_ticker_initialization_summaries.sql) | 紧凑 allowlist、state_seq 防旧覆盖、service_role 权限 | 只承担摘要，完整进度与正文不能从云摘要伪造 |
| [D3 migration](../../supabase/migrations/20260826104142_codex_document3_v1_low_egress.sql) | policy JSON 与 runtime projection 分开、有大小约束 | 不能分钟扫描完整 PolicySet |
| [D2 migration](../../supabase/migrations/20260822153708_codex_document2_v1.sql)、[research lane migration](../../supabase/migrations/202608200001_codex_research_lanes.sql) | Bundle/registry 的 lane 与版本独立 | 不用版本名尾缀判代、不默认批量读取 Bundle 大对象 |

这些迁移文件说明仓库设计，不证明线上已应用。本次未执行远程验证。

## 4. 审查产物验证

API 类型文件使用当前项目 TypeScript 编译器独立运行 `--noEmit --strict --skipLibCheck --target ES2022 --module ESNext`；验证其声明可编译，不代表 HTTP 已实现。原生模型清单仅 AST 解析，不 import 生产模块。

交付还检查：需求§2–17覆盖、接口表与类型名称、相对文件链接、JSON合法性、源模型 SHA-256、Git diff whitespace。本轮没有新增应用测试或执行真实 workflow；仅修改合同文档及 changelog。

本次检查结果：79 个唯一方法/路径组合；27 个源模型文件、482 个模型/枚举声明；另42份相关实现文件指纹，共69份源码指纹核验一致。TypeScript 类型及合成示例通过严格编译；5份交付文件的链接、代码围栏、空白与 JSON 检查无问题。上述数字是静态合同核验，不是79个线上接口通过验收。
