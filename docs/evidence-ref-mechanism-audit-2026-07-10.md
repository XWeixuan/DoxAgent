# DoxAgent `evidence_ref` 运行机制审查报告

> 审查日期：2026-07-11  
> 审查范围：当前工作树的 workflow、agent runtime、工具 provider、prompt / internal task skill、Blackboard 持久化、评测合同和相关测试。  
> 报告性质：现状审查，仅描述机制、效果和问题；不包含改造方案或实现计划。  
> 业务目标：判断 `evidence_ref` 是否能支撑 DoxAgent 生成一份事实可核验、数据可溯源的投研报告。

## 1. 审查结论

`evidence_ref` 当前同时承担了“工具来源记录、上下游材料传递、patch 入库条件、异议/审计线索”四种职责。

从业务效果看，它已经能证明“某次运行调用过什么工具、取回过什么类型的材料”，也能在 Document 2 的部分节点阻止明显证据不足的预期单元进入稳定状态；但它还不能稳定证明“投研报告中的这一条事实或这个数字，确实由这一份具体来源中的这一处内容支持”。

因此，当前机制的主要能力是**过程溯源**，事实核验能力集中且不完整，最终报告的逐条数据来源定位能力较弱。

最重要的审查结论如下：

1. `evidence_ref` 的结构合同严格，但业务语义不严格：字段齐全不等于来源真实、来源相关或来源足以支持报告中的 claim。
2. 真实工具结果是最可信的来源生产路径；agent 在结构化输出中自行写出的 ref 和 workflow 生成的 `agent_output` fallback，业务可信度较低，但当前可与真实工具 ref 一起出现在同一数组中。
3. agent 会消费 `evidence_ref`，但主要通过读取带 refs 的文档、候选 patch、context pack 和 objections 间接消费；不存在一个统一的“按 claim 查询已验证证据”的业务界面。
4. Document 2 是当前唯一具有较完整“生成—审查—异议—解决—promotion”证据闭环的区域；Document 1 的 `ReviewGlobalResearch` 实际没有执行独立审查，Document 3 的证据主要是上游材料的继承和 patch 级存在性要求。
5. 当前可以把报告内容大致指向 SEC、DoxAtlas、TwelveData、Tavily 等 provider 或查询，但多数情况下不能进一步定位到原始文件的页码/段落、数据表单元、行情时间区间或具体搜索结果。
6. 上一轮相关合同测试为 `149 passed, 5 failed`；5 个失败集中在 Document 2 的“证据缺口 finding -> objection -> resolver -> promotion”业务链路。

## 2. 从投研工作流看，`evidence_ref` 应承担的业务角色

DoxAgent 的目标是从公司、宏观、行业、市场和叙事材料中生成预期单元、风险判断、Known Events 与监控规则。对投研产物而言，证据至少有四种不同业务用途：

| 业务用途 | 投研中的问题 | 当前 `evidence_ref` 的实际作用 |
| --- | --- | --- |
| 来源溯源 | “这段材料来自 SEC、DoxAtlas 还是行情数据？” | 做得较好。工具 provider 通常会写入 provider、工具名、标的或查询。 |
| 事实支持 | “公司指引为 X、股价上涨 Y% 的依据是什么？” | 做得不完整。ref 常挂在 section 或 document 上，而非精确挂在一个事实或数值上。 |
| 质量核验 | “这条事实是否足够可靠，可以进入稳定报告？” | 主要在 Document 2 生效；其他阶段多是结构性检查或 prompt 约束。 |
| 最终引用 | “报告读者能否回到原始资料具体位置？” | 较弱。当前没有统一的 URL、文件位置、页码、段落或数据点定位合同。 |

业务上，`evidence_ref` 不是只用于“事实核验”。它更常作为跨 agent 的来源随行标签和 workflow 的审计材料。事实核验只是它的一部分职责，并且主要集中在预期单元生成之后的审查阶段。

## 3. 当前对象合同、字段和典型形态

### 3.1 Canonical 对象

`EvidenceRef` 定义在 `src/doxagent/models/blackboard.py:29-37`，继承配置了 `extra="forbid"` 的 Pydantic model。其顶层字段固定为：

| 字段 | 当前约束 | 典型形态 | 业务含义 |
| --- | --- | --- |
| `evidence_id` | 非空字符串 | `evidence_cc7284f2e2374f17ae75f06a3c9084bf` | 一次 evidence 实例的 ID。由 `new_id("evidence")` 生成，不是来源内容的稳定哈希。 |
| `source_type` | 6 个枚举值 | `doxatlas_source`、`market_data`、`external_report` | 来源类别；用于部分质量判断。 |
| `source_id` | 非空字符串 | `twelvedata:daily_ohlcv:MU`、`doxatlas:get-narrative-report:MU` | provider、端点、标的或查询的复合标识。 |
| `title` | 非空字符串 | `SEC filings and companyfacts for MU` | 人类可读的来源标题。 |
| `summary` | 非空字符串 | `SEC submissions and XBRL company facts were retrieved.` | 通常是检索结果摘要，不一定是对某个报告 claim 的精确解释。 |
| `retrieval_metadata` | 字典，可为空 | `tool_name`、`provider`、`symbol`、`query`、`endpoint`、`market_evidence_snapshot` | provider 侧附加信息；结构未统一。 |
| `confidence` | 0 到 1 | `0.55`、`0.60`、`0.76`、`0.80`、`0.90` | 来源/结果置信度；当前没有跨 provider 的统一评分口径。 |
| `citation_scope` | 非空字符串 | `twelvedata_daily_ohlcv`、`doxatlas_narrative_report` | 引用用途或来源范围标签；当前不是枚举。 |

`EvidenceSourceType` 定义在 `src/doxagent/models/common.py:106-112`：

| 类型 | 当前业务含义 | 实际供给情况 |
| --- | --- | --- |
| `doxatlas_source` | DoxAtlas 叙事、分析、事件、媒体或社媒材料 | 有真实 provider。 |
| `market_data` | 行情、OHLCV、交易或市场数据 | 有真实 provider 和 O4 market trace 路径。 |
| `external_report` | SEC、宏观数据、搜索、行业/外部资料 | 是最广泛使用的真实类型。 |
| `fact_check` | 应表示事实核验材料 | 当前直接生产者主要是 mock；真实 A2 搜索结果通常为 `external_report`。 |
| `agent_output` | agent/工作流自身输出或证据缺口溯源 | 不是外部事实来源。 |
| `tool_result` | 通用工具结果兜底类型 | 真实 provider 通常会覆盖为更具体的类型。 |

### 3.2 典型来源形态

现有 Brief State 导出 `eval/brief_state_exports/run_ec34cd84757a4f939b8acebe01a96e0e.json` 中可见以下真实形态：

| 场景 | `source_id` 示例 | metadata 中通常能看到 | 能回答什么 | 不能回答什么 |
| --- | --- | --- | --- | --- |
| 公司披露 | `sec:company:0000723125` | CIK、`sec.company_facts_and_filings` | 本次运行取过某公司 SEC/company facts | 具体数字来自哪份 filing 的哪一页/哪一行。 |
| 基本面数据 | `alpha_vantage:financial_statements:MU` | symbol、财务表 functions | 取过 MU 的财务报表数据 | 报告中的某个营收/毛利率具体取自哪期数据。 |
| 搜索材料 | `tavily:search:<query>` | query、topic、max_results | 执行过相关搜索 | 采用了哪篇文章、哪段原文、是否一手来源。 |
| 叙事材料 | `doxatlas:get-narrative-report:MU` | endpoint、provider、tool name | 读取过 MU 的 DoxAtlas narrative | 哪个叙事句子支持哪个精确事实。 |
| 市场数据 | `twelvedata:daily_ohlcv:MU` | symbol、工具名、market snapshot | 取过 MU 的 OHLCV | 使用的日期窗口、复权口径、收益率算法和事件对齐方式。 |
| 模型 fallback | `react:<task_id>` | agent、task、ticker、`evidence_gap=true` | 此输出来自某次 agent 运行且外部证据不足 | 任何外部事实本身。 |

## 4. 证据如何产生、流转和保存

### 4.1 产生路径

当前最主要的生产路径是：

```text
真实工具 provider
  -> ToolResult.evidence_refs
  -> AgentResult.evidence_refs / ToolCallSummary
  -> ResearchSection、Expectation、Objection、Patch、Working Memory
  -> CommitLog、PostgreSQL evidence_refs、Brief State
```

对应实现锚点如下：

| 业务动作 | 当前实现位置 | 审查结论 |
| --- | --- | --- |
| 工具成功时创建来源对象 | `tools/providers/base.py::_success()` | provider 必须传入类型、来源标识、标题、摘要、scope、置信度和 metadata；这是最标准的生产方式。 |
| 工具结果转通用引用 | `tools/schema.py::ToolResult.to_evidence_ref()` | 默认可生成 `tool_result`，真实 provider 多会提供更具体类型。 |
| ReAct 汇总证据 | `agents/runtime/react.py::_evidence_refs()` | 仅汇总成功工具和 delegation result 的 refs；失败工具不会进入最终 AgentResult。 |
| O4 专用市场证据 | `agents/market_trace/module.py::_evidence_refs()` | 直接生成 `market_data` ref，并附带 market-trace metadata。 |
| 模型输出对象校验 | `agents/runtime/react.py::_valid_evidence_ref_payloads()` | 只验证对象形状；没有检查该 ID 是否真由本 run 的工具产生。 |

除真实工具之外，agent 也可在其 JSON output 中写入完整对象。只要字段通过 Pydantic 校验，就可以成为 payload 的 refs。当前没有一个全局约束要求“每个模型输出的 evidence_id 必须在本 run 的成功工具结果或持久化 evidence 表中存在”。

### 4.2 文档与工作流中的落点

`EvidenceRef` 会被挂在以下业务容器中：

| 容器 | 业务目的 |
| --- | --- |
| `ResearchSection` | 给 C1/C2/C3/O4 的基础研究段落提供来源背景。 |
| `ExpectationShell` / `ExpectationUnitDocument` | 支撑 O1 的市场观点、已实现事实、价格反应和关键变量。 |
| `BlackboardPatch` | 表示某次稳定状态修改的来源理由。 |
| `WorkingMemoryEntry` | 保存 agent 当时的结果、工具调用和证据，用于后续恢复与审计。 |
| `Objection` / `resolution_evidence_refs` | 说明为什么提出异议，以及为什么接受、拒绝或解决它。 |
| `Document2ReviewFinding` / `EvidenceAssessment` | 表示 reviewer 认为某个字段的证据状态。 |
| `KnownEvent.source` | 为后续 W1 判断“新消息还是旧消息”提供事件来源。 |
| `CommitLogEntry.patch` | 通过 patch 留存稳定写入的证据。 |

这种设计的特点是 ref 主要附着于“段落、文档、patch、流程对象”，而不是附着于独立的业务 claim。例如一个 section 可以同时写营收、指引、客户关系、估值和风险，却只携带一组共同 refs。

### 4.3 保存与读取

`src/doxagent/blackboard/postgres_repository.py` 会将 refs upsert 到 `doxagent.evidence_refs`，并将对象 JSON 保存为 `evidence_json`。同一 ref 还会在 Working Memory、CommitLog、Objection 等 JSON 中重复保存。

`ContextBuilder` 的读取行为分两类：

| 下游场景 | 当前读取方式 | 业务含义 |
| --- | --- | --- |
| 非 scoped 节点 | 从 Working Memory 和未解决 Objection 收集 refs，按 `evidence_id` 去重后放入 `AgentContextSnapshot.evidence_refs` | agent 可看到较通用的运行历史来源集合。 |
| Document 2/3 scoped 节点 | 顶层 `working_memory_summary`、`unresolved_objections`、`blocking_delegations`、`evidence_refs` 被清空；改读 belief state、pending patch、局部 objections 和 Document1ContextPack | 下游不会消费完整来源历史，而是消费压缩的、任务相关的材料。 |

Document1ContextPack 会把上游 refs 压成 `EvidenceDigest`，保留 ID、类型、来源、标题、摘要、置信度、scope、工具名和 market snapshot。它能保留“来源线索”，但不会增加“来源具体支持哪条事实”的关系。

## 5. 当前 agent / 节点对 evidence_ref 的产生和消费

### 5.1 范围计数

- `AgentName` 一共 **11 个**：W1、W2、O1–O4、A1–A2、C1–C3。
- **9 个** agent 具有真实工具 allowlist：O1/O2/O3/O4、A1/A2、C1/C2/C3；它们都可能通过标准工具路径生产 refs。
- 初始化 workflow 有 **19 个节点**；其中 **15 类节点会调度 agent**，实际会涉及 C1/C2/C3/O1/O2/O4/A1/A2 共 **8 个**身份。O3 出现在 runtime judgement；W1/W2 没有工具 allowlist。

### 5.2 按业务阶段审查

| 工作流阶段/节点 | 主要 agent | evidence_ref 如何产生 | agent 如何消费 | 当前核验方式 |
| --- | --- | --- | --- | --- |
| `BuildGlobalResearch` | C1/C2/C3/O4 | 各自工具返回来源，形成四个研究 section | 基础研究任务从空或极少上游历史开始，主要生产新材料 | section 可由 result refs hydration；没有独立 Global Research reviewer。 |
| `ReviewGlobalResearch` | 无 | 不产生 | 不消费 | 当前直接完成，没有实际审查。 |
| `GenerateExpectationConstruction` | O1 | 可调用 DoxAtlas narrative，result 携带 refs | 消费 Document 1、context pack 和 DoxAtlas 材料 | O1 narrative tool 有 required/gap 约束。 |
| `ReviewExpectationConstruction` | A1 | A1 工具可产生 DoxAtlas refs；finding/objection 可携带 refs | 消费 expectation shells 和相关叙事材料 | 空 objection refs 会从 parent result/tool refs 回填。 |
| `ResolveExpectationConstruction` | O1 | 可带 revision/resolution refs | 消费 construction objections 与其 refs | transaction 负责后续闭环。 |
| `GenerateExpectationDetails` | O1（每个 shell） | 继承上游 refs，也可从工具/DoxAtlas 取得 refs | 消费 shell、Document 1 evidence、市场/叙事上下文 | prompt 要求事实与变量带 refs；candidate 会进入后续 review。 |
| `ReviewExpectationFields` | A1/C1/C3/O4 | reviewer 可附补充 refs，且可创建 objection | 消费 pending expectation、事实、价格反应、变量上的 refs | status 被转为 `EvidenceAssessment`；无效 reviewer ref 被移除并告警。 |
| `ResolveObjectionsAndDelegations` | O1 / A2 | A2 可新增外部搜索 refs；O1 写 resolution refs | O1 消费 objection、delegation 和已有证据；该路径禁止 O1 新工具调用 | transaction/deterministic revalidation 决定异议是否真正关闭。 |
| `PromoteExpectationToBeliefState` | SYSTEM | 不以 agent 方式生产新研究证据 | 消费 review finding 和 evidence assessment | insufficient/unavailable/stale/contradictory 会形成 blocker。 |
| `GenerateGlobalNarrativeReport` | O1 | 必须使用 DoxAtlas narrative tool 或记录 gap | 消费 stable Global Research 与 expectation refs | workflow 检查 required tool/gap。 |
| `GenerateKnownEvents` | O1 | 为每个事件选择或复用 source ref | 消费 expectation 的 market/fact/price/variable refs 与 Global Research refs | 优先 source-specific ref；没有时允许 fallback。 |
| `GenerateMonitoringConfig` | O2 | patch 需要 refs，通常继承/聚合上游材料 | 消费稳定文档的业务内容与 refs | 重点检查 monitoring item 的配置质量，不逐条验证来源。 |
| `Review/ResolveMonitoringConfig` | C1/C3 / O2 | review 可产生 objection；resolve 的 patch 可带 refs | 消费 config、objection 和上下游业务内容 | Document 3 生命周期/异议闭环。 |
| `Generate/Review/ResolveMonitoringPolicy` | O4 / O2 / O4 | 同上 | 消费 config、policy、objection 和稳定文档 | 重点在监控规则业务正确性，而非 claim 级引文核验。 |
| `FinalizeInitialization` | SYSTEM | 写入 monitoring runtime apply 的 workflow audit ref | 消费 Monitoring Config patch | 该 ref 是运行审计记录，不是投研事实来源。 |

## 6. 对 agent 的 prompt、skill 与运行时约束

### 6.1 对“不要伪造来源”的约束

| 资源 | 覆盖角色 | 当前约束 |
| --- | --- | --- |
| `prompts/internal_task_skills/source-discipline.md` | O1/O2/O4/C1/C2/C3/A1 | 没有支持的 claim 应留在 Working Memory/unknowns，不应直接提升到 Belief State。 |
| `prompts/agents/c1.md`、`c3.md`、`o4.md`、`a1.md` | C1/C3/O4/A1 | 只放完整 EvidenceRef；部分 source clue 应放在 rationale/recommended statement。 |
| `a1-expectation-construction-audit.md`、`a1-expectation-field-audit.md` | A1 | 要求完整字段；field audit 同时说明 refs 有帮助但可选。 |
| `prompts/agents/a2.md` | A2 | 不得伪造 source、URL、citation、date、source ID；需返回 source refs、置信度和 query log。 |
| `known-events.md` | O1 | 每个 Known Event 必须给完整 source ref，只能使用 stable context 或工具结果。 |

这些约束能减少明显的格式错误和伪造行为，但多数属于 prompt/skill 层约束。运行时能验证“对象是不是完整”，不能普遍验证“对象是否真的来自本次工具调用、是否支持本条事实”。

### 6.2 对 Document 2 的特定约束

`expectation-detail.md` 要求：

- `realized_facts` 不能为空，且每条事实必须有 refs；
- `key_variables` 不能为空，且每项变量必须有 refs；
- 没有可靠市场数据时，价格反应必须披露不确定性，而不能编造价格数字。

`document2-field-repair.md` 和 `agents/o1.md` 要求：

- `evidence_refs` 必须是完整对象列表，不能是 string ID 列表；
- 只有 evidence ID 而没有完整对象时，应留空并通过 `evidence_requests` 说明缺口；
- resolved/rejected/deferred 等决策不能静默关闭问题，应使用 `changed_paths`、`evidence_refs`、`unresolved_reason` 等解释。

运行时的 `validate_resolution_plan_for_transaction()` 及 field repair 对应函数，若非 deferred 决策同时没有 `changed_paths` 和 `evidence_refs`，当前写入 audit note，但不直接拒绝 transaction。这意味着 prompt 的“必须有 changed path 或证据”在该层不是绝对硬门。

## 7. 工作流中的审查、核验和审计方式

### 7.1 在线结构核验

当前在线机制首先保证对象结构：

- Pydantic 拒绝缺失必填字段、错误枚举、`confidence` 越界和额外字段；
- `BlackboardPatch` 的顶层 `evidence_refs` 非空，否则 `_validate_patch_contract()` 拒绝该 patch；
- Document 1 section 为空时，会从 AgentResult 继承 refs；仍为空时生成 `agent_output` fallback；
- Document 2 final payload adapter 会用 `EvidenceRef.model_validate()` 过滤无效 ref；如果顶层 refs 空，可能用 runtime tool/delegation refs 回填；
- reviewer finding 的无效 ref 会被移除，写为 `invalid_evidence_refs_removed`、`severity=non_fatal` warning。

这些检查保证“系统中留下的是结构合法对象”，但不保证“每个合法对象都足以支持其所在文档的核心内容”。

### 7.2 Document 2 的事实审查和 promotion

Document 2 是当前最完整的核验路径：

1. O1 生成 expectation detail candidate，其中包含 market view、realized facts、price reactions、key variables；
2. A1/C1/C3/O4 审查字段，形成 supported / unsupported / needs more evidence / contradicted 等 finding；
3. finding 被映射为 `EvidenceAssessment`；
4. `insufficient`、`unavailable`、`stale`、`contradictory` 默认阻断 promotion；
5. O1/A2 处理异议或委托检索；
6. promotion 再检查 active finding、unresolved objections 和 deterministic blockers。

对价格反应，`document2/evidence.py::is_market_evidence_ref()` 会把 `market_data` 类型，或 `source_id`/工具名中包含 OHLCV、quote、market、trade、price 等标记的 ref 视为市场证据。该判断比“任意外部链接”更接近投研业务需要，但仍是来源类型/命名识别，而不是对具体价格窗口和计算结果的复算。

### 7.3 持久化和运行审计

| 审计对象 | 保存内容 | 业务价值 |
| --- | --- | --- |
| Working Memory | AgentResult、工具调用、refs、模型/skill/acceptance audit | 可复盘 agent 当时看过和产生过什么。 |
| Patch / CommitLog | patch 的 refs、作者、触发原因 | 可追踪稳定文档为何被写入。 |
| Objection | 原始 evidence refs、resolution changed paths、resolution evidence refs | 可追踪异议提出与关闭理由。 |
| PostgreSQL `evidence_refs` | 以 `evidence_id` 保存完整对象 JSON | 可按运行聚合来源对象。 |
| Brief State / Eval | 文档内 refs、工作记忆、异议、commit 和 validator 结果 | 用于人工复核、回归评测和轨迹审计。 |

其中有一个持久化边界：PostgreSQL 的规范化 evidence 收集会收 Working Memory refs、Commit patch refs 和原始 `objection.evidence_refs`；`resolution_evidence_refs` 会保留在 objection JSON 中，但不在同一收集路径中单独 upsert。若 resolution 引用没有同时进入 patch 或 Working Memory，它不一定会出现在规范化 evidence 表中。

### 7.4 离线评测

`eval/blackboard_hard_gates.yaml` 的 HG05 和 `eval/document2_eval/document2_hard_gates.yaml` 的 D2-HG05 要求：

- Global Research section、market view、realized fact、price reaction、key variable 都有 hydrated refs；
- refs 具备 `source_id`、`source_type`、`title`、`summary`、`confidence` 和 `citation_scope`；
- 最终引用的 source/tool 可以在 Brief State、Working Memory 和 LangSmith 工具轨迹中找到。

这是目前最接近端到端证据完整性的检查，但它属于 eval/hard gate，不是每一次线上内容写入时都会对每条报告 claim 执行的语义验证。

## 8. 对“报告事实”和“数据指向来源”的审查结论

### 8.1 当前能证明的内容

当 ref 来自真实工具成功结果时，当前系统通常能证明：

- 哪个 agent 在哪次 run 中调用过哪个工具；
- 工具属于哪类 provider；
- 工具检索的是哪个 ticker、端点或搜索 query；
- 工具返回后该 ref 被哪些 AgentResult、section、patch、working memory 或 commit 使用。

例如，`twelvedata:daily_ohlcv:MU` 能证明 MU 日线 OHLCV 数据被取过；`doxatlas:get-narrative-report:MU` 能证明 MU 的叙事报告被读取过；`sec:company:<cik>` 能证明公司披露接口被取过。

### 8.2 当前不能稳定证明的内容

当前无法稳定回答下列投研审计问题：

| 报告中的内容 | 需要的证据定位 | 当前缺失的业务信息 |
| --- | --- | --- |
| 财务数字 | filing、报告期、表格、行项目、GAAP/non-GAAP 口径 | `source_id` 通常只到 SEC/provider，不到数据单元。 |
| 公司指引或管理层表述 | 文件/电话会、页码或段落、发布日期 | 没有统一 source locator 或原文摘录字段。 |
| 价格反应 | 事件时间、起止交易日、基准、复权/未复权、计算方法 | ref 可表明用了 OHLCV，但不记录计算口径。 |
| 行业/宏观数字 | 数据集、观察期、发布日期、修订版本 | metadata 由 provider 自由决定，没有统一 vintage 合同。 |
| 市场叙事 | 哪个 narrative/proposition/article 支持哪一个判断 | DoxAtlas/搜索 ref 经常只是背景材料，不是 claim 的直接支撑。 |
| 投资结论 | 哪些事实共同支持或反驳结论、推理是否超出事实 | ref 不能表达“支持/反驳/仅背景”的关系。 |

因此，最终报告中的数据在当前体系下可以获得“来源线索”，但不能稳定获得“可点击、可复算、可复核”的引用。

## 9. 当前存在的问题

### 9.1 evidence_ref 附在内容容器上，而不是附在业务 claim 上

同一个 section 或 expectation 可以带多个 refs，但 refs 与其中具体事实、数字、推断之间没有显式映射。一个包含“收入、毛利率、HBM、估值、风险”的段落，即使有 refs，也不能说明其中每个结论分别由什么支持。

这会使“整段有引用”被误判成“每条重要事实都已被核验”。

### 9.2 当前 ref 更像检索记录，不是可引用的数据定位符

`source_id` 多数只记录 provider、端点、ticker 或搜索 query；`summary` 多数是“已检索某结果”。它通常不能定位到：

- 原始 URL/文件；
- 页码、段落、表格或行项目；
- 数据观察期、发布日期、抓取时间；
- 行情窗口与计算公式；
- 搜索结果中最终被采用的文章和原文。

因此 `evidence_ref` 不足以直接承担最终投研报告的 citation 职责。

### 9.3 不同可信度的来源对象混在同一引用机制中

真实工具结果、上游复制结果、agent 自行写的 JSON ref、workflow 的 `agent_output` fallback 都可成为 `evidence_refs` 数组成员。它们的业务含义不同：

- 工具 ref 证明过一次外部/数据工具调用；
- 复制 ref 证明上游曾使用该材料；
- agent 手写 ref 只证明字段符合格式；
- `agent_output` 只证明模型产生了内容或存在证据缺口。

当前虽然存在 `source_type` 与部分 metadata 标记，但没有统一业务层级或准入语义。顶层 patch 的“refs 非空”检查无法区分它们的事实支持强度。

### 9.4 `agent_output` fallback 可能把“证据缺口”伪装成“已满足引用条件”

Document 1 section、直接文档转 patch、Known Events source 选择都存在 fallback 路径。当没有外部 refs 时，系统可写入 `agent_output` 类型，并带 `evidence_gap=true`。

该对象对审计而言有价值，但不是外部来源。当前 workflow 的部分 patch 级检查只要求 refs 非空；Known Events 的质量检查也不要求 source 必须是 source-specific。这使“有一个合法 ref”与“有一个可验证来源”没有被完全分开。

### 9.5 无效 agent 引用通常被软删除，而不是形成一致的流程失败

Document 2 final payload adapter 和 reviewer sanitizer 会过滤 Pydantic 校验失败的引用；reviewer 路径记录 `non_fatal` warning 并继续处理。业务结果可能是：

- reviewer 原本给出的引用被移除；
- finding 仍可能保留；
- 下游只看到没有补充 refs 的 finding 或 runtime fallback；
- 问题是否阻断取决于后续 assessment/objection，而不是无效引用本身。

这使“模型输出的来源格式有问题”在不同节点有不同的业务后果。

### 9.6 `fact_check` 类型与真实 A2 取证路径不一致

系统枚举中存在 `fact_check`，而 A2 的真实工具 allowlist 是 AnySearch、Tavily search、Tavily extract；这些真实工具会生成 `external_report`。`fact_check` 的直接产出主要来自 mock。

业务上，这使“经过事实核验”与“找到外部搜索材料”无法通过类型稳定区分。对投研报告而言，官方披露、权威媒体、搜索摘要和已验证事实不能被视为同一等级。

### 9.7 来源的新鲜度、数据版本和口径没有统一合同

`retrieval_metadata` 是自由字典，provider 可自行写 symbol、query、endpoint 或 market snapshot，但没有系统级必填的：

- 来源发布日期；
- 抓取时间；
- 数据截止日/观察期；
- 财务期与会计口径；
- 市场数据的复权、交易日和计算方式；
- 来源内容版本或 hash；
- 失效、替换、修订状态。

这限制了 workflow 对“旧事实是否仍可作为新催化剂”“历史数值是否被修订”“市场反应是否使用正确窗口”的业务判断。

### 9.8 Document 1 是事实地基，但没有实质独立审查

`BuildGlobalResearch` 由 C1/C2/C3/O4 并发生产 foundational research。O1 的预期、Known Events、监控配置和运行期判断都会继承这些材料。

但 `ReviewGlobalResearch` 当前只完成节点状态，不调度审查 agent，也不运行独立 evidence 质量检查。基础研究中的错误、泛化来源或过期信息可能在进入 Document 2 前就被带入后续多条预期。

### 9.9 下游 agent 消费的是压缩/局部材料，证据关系容易变弱

Document 2/3 为控制上下文体积，刻意不传递完整的通用 Working Memory/Objection/evidence list，而改读 scoped documents、pending patches、局部 objections 和 Document1ContextPack。

这降低了上下文成本，但下游看到的往往是一组 evidence digest 或文档嵌套 refs。由于 refs 本来就不是 claim 级关系，经过压缩后更难判断“哪份材料支撑哪条事实”。

### 9.10 异议解决证据的持久化可见性不完整

Objection 有原始 `evidence_refs` 和解决时的 `resolution_evidence_refs`。Blackboard service 会保存后者到 objection JSON，但 Postgres 规范化 evidence 收集只直接收原始 objection refs。

因此，某条只在 resolution 阶段出现、且未被同时放入 patch 或 Working Memory 的新证据，可能在规范化 evidence 表/聚合统计中不可见。这会削弱“为什么异议最终被关闭”的独立审计性。

### 9.11 Document 2 的 evidence 闭环存在当前回归信号

本次审查相关测试运行结果为：

```text
149 passed, 5 failed, 4 warnings in 24.31s
```

5 个失败均位于 `tests/test_document2_node_contract_matrix.py`，集中表现为：

- placeholder 或 unknown price reaction 的证据问题没有形成预期的 blocking finding；
- 预期的 finding / source objection 生命周期没有写入；
- resolver revision 后的 metadata/promotion audit 未满足测试预期。

从业务角度，这意味着当前最关键的“发现证据问题后，能否把问题一路带到异议、解决和 promotion 决策”的链路并不稳定。

## 10. 审查范围内的总体判断

当前 `evidence_ref` 已经为 DoxAgent 提供了必要但有限的证据基础：

- 对运行过程而言，它可追踪工具、agent、patch 和持久化记录；
- 对 Document 2 而言，它能参与一定程度的事实审查和 promotion 阻断；
- 对最终投研报告而言，它尚不能稳定承担逐条事实支持和具体数据引用；
- 对跨 agent 协作而言，它是材料标签和审计线索，而不是统一、可验证的事实知识层。

因此，当前系统不能仅凭“报告/patch/事实对象带有 `evidence_refs`”就认定投研结论已经被充分核验。`evidence_ref` 的存在、合法性、来源真实性、与 claim 的相关性、数据口径正确性和最终可引用性，当前仍是不同层级、不同强度的事情。

---

## 附录：关键实现锚点

| 审查主题 | 主要文件 |
| --- | --- |
| EvidenceRef 合同与枚举 | `src/doxagent/models/blackboard.py`、`src/doxagent/models/common.py` |
| 工具来源生成 | `src/doxagent/tools/schema.py`、`src/doxagent/tools/providers/base.py`、各 provider |
| ReAct 汇总与 payload 校验 | `src/doxagent/agents/runtime/react.py` |
| 上下文读取与压缩 | `src/doxagent/context/builder.py`、`src/doxagent/workflows/document1/context_pack.py` |
| 初始化节点编排 | `src/doxagent/workflows/initialization/orchestrator.py`、`shared.py` |
| Document 1 ref hydration | `src/doxagent/workflows/document1/validators.py` |
| Document 2 审查、证据评估、promotion | `src/doxagent/workflows/document2/evidence.py`、`review.py`、`promotion.py`、`legacy_pipeline.py`、`legacy_quality.py` |
| 异议与证据持久化 | `src/doxagent/blackboard/service.py`、`postgres_repository.py` |
| Prompt / skill 约束 | `prompts/agents/`、`prompts/internal_task_skills/` |
| Eval / 回归证据 | `eval/blackboard_hard_gates.yaml`、`eval/document2_eval/document2_hard_gates.yaml`、`tests/test_document2_node_contract_matrix.py` |
