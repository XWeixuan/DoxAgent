# Document2 当前实现梳理：供 Document3 整体编排重构设计使用

审计日期：2026-10-03（Asia/Shanghai）。审计对象：`C:/Users/WEIXUANXIE/Desktop/DoxAgent` 的本地工作区文件。

本文件是**当前实现说明**，不是新的重构方案。依据代码、实际 prompt/skill 文件和现有测试；方案文档仅用于解释背景，遇到差异以代码为准。没有查询生产服务器、运行真实模型、调用业务数据接口或改变工作流。

审计时分支为 `codex/trade-execution-repair`，HEAD 为 `aa0efcdb997941b33a83927abc165353e2cef290`。工作区已有尚未提交的 D2 v2.1 编排/schema/Pilot 修改，以及其他模块修改。本文件包含这些本地修改的现状，不能等同于该 HEAD 的纯提交快照，也不能证明生产已部署 v2.1。

## 1. 先明确版本、职责与当前可用边界

| 项目 | 默认 Document2 v2 | 显式 Document2 v2.1 |
| --- | --- | --- |
| 请求版本 | `document_schema_version="document2.v2"`，缺省值 | `document_schema_version="document2.v2.1"` |
| 内部 workflow 标识 | `codex_document2_v1` | 同一个 `codex_document2_v1` |
| research lane | `document2` | `document2` |
| O0 主轴 | Candidate branches → Synthesis → Domain Reviews → Finalization | 主轴相同，但输出合同换成 V21 模型 |
| 每个 Shell 的 O1 主轴 | State → Realization → Gaps → Finalization | Discovery Scan → Discovery Selection → State → Realization → Gaps → Finalization |
| Unit 业务资产 | State、Realization Factors、Potential Gaps | State、Expectation Baseline、Realization Factors、Potential Gaps/Revision Space |
| 命名字段 | `shell_id`、`expectation_id`、各类 `*_id` | 主要改为语义 `name`，State Value 用 `parameter` 关联 |
| 正式文档 schema | `Document2Document` / `document2.v2` | `Document2DocumentV21` / `document2.v2.1` |
| COMPLETE 是否成为 current | 是 | 否；PARTIAL 也不成为 current |
| 默认初始化/API 入口 | 构造请求时不传版本，仍走 v2 | 需要内部/Pinned/Pilot 显式传版本 |
| Prompt/Skill 配套现状 | 当前目录资产按 v2 写作 | 仍复用旧资产；新增两个 Discovery skill 路径存在于代码，但文件缺失 |
| 当前 D3 消费合同 | 直接支持 v2 | 尚未适配；直接按 `Document2Document` 解码会拒绝 v2.1 |

`codex_document2_v1` 是这一套 Codex Document2 工作流的内部版本标识，不能据此把它和旧业务 v1 工作流混为一谈。本文不梳理 `src/doxagent/workflows/document2*` 或 `prompts/v1` 的旧流程，也不把旧流程的 field-repair/promotion 校验套用到本模块。

v2.1 的业务背景是让 D2 提供现实、默认预期、因果模型和未来修订空间，由 O3 独立研究交易边界与 Policy。但当前“代码合同已经增加 v2.1”与“生产提示词已经支持 v2.1”是两件事。下文分别说明。

证据：[schema.py](C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/workflows/codex_document2/schema.py:479)、[默认初始化入口](C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/ticker_initialization/research_adapter.py:354)、[API 启动入口](C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/dashboard_api/research_lanes.py:124)。

## 2. 整体执行轴与节点衔接

### 2.1 在 ticker initialization 中的位置

```text
D1 Global Research ─────────┐
                           ├── O2 / Event Library ──┐
CDECR ─────────────────────┘                         │
D1 Global Research ─────────────────────────────────┼── D2
                                                    │    │
                                                    └────┼── D3
                                                         │
                                              后续 O4 / Activation / Runtime
```

准确的依赖边：`o2` 依赖 `d1,cdecr`；`d2` 依赖 `d1,o2`；`d3` 依赖 `d2,o2`。初始化 `_d2` 取 O2 的 published version，构造只读 `PublishedEventLibraryProvider(pinned_version=version)`；D2 的研究 cutoff 取整个 initialization 的 `research_cutoff_at`。

这里的 O2 是 Event Library Owner；Document2 自己的 Agent 是 O0 与 O1，Domain Review 使用 C1/C3/C5。

证据：[初始化 DAG](C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/ticker_initialization/catalog.py:15)、[D2 adapter](C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/ticker_initialization/research_adapter.py:354)。

### 2.2 Document2 默认 v2 主流程

```text
d2_input_preparation（代码，冻结输入）
    ↓
O0 Candidate Discovery（分支并发，有并发上限）
    ├─ d2_o0_candidate_c1
    ├─ d2_o0_candidate_c3
    ├─ d2_o0_candidate_c5
    └─ d2_o0_candidate_narrative（仅 Narrative AVAILABLE 时创建）
    ↓ 等分支结束；部分研究失败可降级
d2_o0_synthesis（O0 合并全部成功 Candidate Sets）
    ↓
Domain Review（同一 provisional draft，分支并发）
    ├─ d2_o0_review_c1
    ├─ d2_o0_review_c3
    └─ d2_o0_review_c5
    ↓ 等分支结束；部分研究失败可降级
d2_o0_finalization（O0 决定全局 Shell / Unit Seeds）
    ↓
按 Shell 分支并发，每个 Shell 内严格串行：
    d2_o1_state
        ↓ 完整 Shell → 下一节点
    d2_o1_realization
        ↓ 完整 Shell → 下一节点
    d2_o1_gaps
        ↓ 完整 Shell → 下一节点
    d2_o1_finalization
        ↓ Shell checkpoint / 最终 shell.json
    ↓ 等所有 Shell 分支结束
d2_assemble（代码，成功 Shell 聚合 + 引用重编号）
    ↓
d2_publish（代码，发布白名单工件 + handoff）
```

这不是“先让一个 Agent 对全部 Shell 做 State，再让它对全部 Shell 做 Realization”。`asyncio.gather` 为每个 Seed 创建 `_research_shell`，每个分支完成自己的四轮，再全局 Assemble。某个 Shell 可以已经做 Gaps，而另一个还在 State。

O0 candidate/review 共用一个 semaphore；O1 使用另一个 Shell semaphore。两者上限都来自 `max_shell_concurrency`，构造器默认 2，设置 `DOXAGENT_CODEX_D2_MAX_CONCURRENCY` 默认也为 2。O1 获取 semaphore 后持有至该 Shell 全部阶段结束，不是按每一个 turn 重新放行。

O1 Finalization 是**各 Shell 局部 finalization**。所有 Shell 完成后没有额外的 LLM Final Global Review；全局只做 deterministic assembly、citation remapping 和 publication。

### 2.3 v2.1 的变化

O0 主轴和并发方式不变。每个 Shell 分支变为：

```text
O0 finalized Seed → 初始 canonical Shell（各研究数组为空）
    ↓
d2_o1_discovery_scan       → OpenDiscoveryScanV21（不改 canonical）
    ↓
d2_o1_discovery_selection  → OpenDiscoverySelectionV21（不改 canonical）
    ↓
d2_o1_state               → ShellResearchTurnResultV21
    ↓
d2_o1_realization         → ShellResearchTurnResultV21
    ↓
d2_o1_gaps                → ShellResearchTurnResultV21
    ↓
d2_o1_finalization        → ShellResearchTurnResultV21 + discovery 闭环
    ↓
final shell.json → 同样的全局 Assemble / Publish
```

后四轮返回 envelope，`canonical_shell` 替换当前完整 Shell；另外两个数组记录 late additions、discovery resolution。没有独立的 Baseline turn，Baseline 放在 canonical Unit 中，由研究轮生成/维护。当前旧 State skill 未规定新版 Baseline 的形成方法。

正常无重试、无恢复的模型调用数：O0 为 `3 或 4 candidates + 1 synthesis + 3 reviews + 1 finalization`，即 8 或 9 次；v2 再加 `4 × Shell 数`，v2.1 再加 `6 × Shell 数`。代码节点 Input/Assemble/Publish 不调用模型。实际并发还会受 Worker 队列与资源调度限制。

证据：[O0 编排](C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/workflows/codex_document2/orchestrator.py:451)、[v2 Shell 分支](C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/workflows/codex_document2/orchestrator.py:773)、[v2.1 Shell 分支](C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/workflows/codex_document2/orchestrator.py:913)。

## 3. Run、Workspace、Thread：真实语义

### 3.1 Workspace 拆分

`logical_workspace_id(run_id, logical_key)` 生成 `d2ws-<SHA256(run_id + NUL + logical_key) 前32位>`。

| 对象 | Workspace |
| --- | --- |
| 整体 D2 run | `request.run_id`，存输入快照、聚合工件、O0 中间交接、各 Shell 最终交付 |
| O0 candidate C1/C3/C5/Narrative | 各自 `logical_key=o0-candidate-<source>` |
| O0 synthesis/finalization | 同一个 `logical_key=o0-synthesis` |
| C1/C3/C5 Domain Review | D1 的 `source_global_run_id` workspace |
| 每个 O1 Shell | `logical_key=shell-<shell_key>`；`shell_key=SHA256(语义 Shell 名) 前16位` |
| 每个 attempt | 所属 workspace 下 `attempts/<attempt_id>/input,output,audit` |

`shell_key` 是文件与调度定位键，不是输出文档的业务身份。v2 业务身份仍是自然语言 `shell_id`，v2.1 为自然语言 `name`。

### 3.2 当前实际不是跨业务节点的同一 SDK thread

Orchestrator 仍保存并向 Runner 传入 O0/O1 的 thread IDs；Domain Review 也会查找 D1 原 researcher thread。但 `Document2TurnRunner.run()` 创建 `WorkerRunRequest` 时**固定 `thread_id=None`**。

Worker runtime 对 `request.thread_id=None` 调用 `thread_start`，非空才调用 `thread_resume`。正常下一业务节点因此新开 SDK thread；上一轮的研究理解并不会自动以 conversation history 进入下一轮。上一轮结果通过 canonical Shell、context.json、持久化工件衔接。

原 prompt 中“Resume O0 Synthesis thread”“Continue original C1/C3/C5”“same O1 thread performs four sequential turns”与这一行为不一致。现有测试明确要求 review/finalization requests 的 thread_id 为 None，注释说明这是为了刷新 attempt-local MCP capabilities。它是已有行为，不应仅凭旧 prompt 推断系统真的恢复了原 D1/O1 conversation。

同一 Worker job 的基础设施故障恢复可能使用该 job 已取得的 thread_id；这与正常跨 State/Realization/Gaps 业务节点续 thread 不同。

**对 D3 方案设计的含义：**可以参考 D2 的分阶段目标和 canonical handoff，但不能把 D2 当前实现描述成“已验证同一 thread 连续多 wave”的参照。若 D3 要保留同一 thread，需要由 D3 自己的 dispatch 行为保证。

证据：[Runner request](C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/workflows/codex_document2/runner.py:156)、[Worker thread 分支](C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/codex_worker/sdk_runtime.py:378)、[测试及能力隔离注释](C:/Users/WEIXUANXIE/Desktop/DoxAgent/tests/test_codex_document2_workflow.py:710)。

## 4. 输入准备、冻结与每个节点的 context

### 4.1 D1 是必需输入，其他输入按 availability 处理

`source_global_run_id` 必须是 published GlobalResearchBundle，有 handoff 和 published_at。ticker 如显式传入，必须与 D1 相同。C1/C3/C5 三份报告均必须存在，读取后与 ArtifactRef 的 SHA256 对照。

准备出的 `GlobalResearchInput` 是以下完整结构：

```text
GlobalResearchInput {
  run_id: string, ticker: string, published_at: datetime,
  reports: map<string,string>,                 // c1/c3/c5 全文
  report_artifact_ids: map<string,string>,
  entity_relations: object[], future_nodes: object[],
  horizontal_collection: object,
  horizontal_artifact_id: string|null,
  document_artifact_id: string,
  citation_manifest_artifact_id: string|null
}
```

D1 报告及结构化 context 的 `【cite:O#】` 统一加 namespace 为 `【cite:D1-O#】`。entity relations、future nodes 来自 bundle；horizontal collection 从 D1 `PROGRAM_COLLECTION/BUNDLE` 工件中选择可读取且 hash 一致的产物，找不到可保持 `{}`。

Narrative 和 Event Library 并发加载；provider 的异常转为 `UNAVAILABLE`，不因可选输入失败而阻止 D2 输入准备。OptionalInput 的结构为：

```text
OptionalInput {
  status: AVAILABLE|ABSENT|UNAVAILABLE|NOT_CONFIGURED,
  payload: object|string|null,
  source_run_id: string|null, as_of: datetime|null,
  warning: string|null, metadata: object
}
```

Narrative provider 读取 DoxAtlas 最新 completed report，要求 completed_at 存在，且相对研究 cutoff 的 age 在 `[0,7天]`；过期或晚于 cutoff 作为 ABSENT，不自动寻找更早的一份。AVAILABLE 时才开 Narrative Candidate branch。

Event provider 只读 Published Reference View。无配置为 NOT_CONFIGURED，无版本为 ABSENT。未 pin 时，最新版本晚于 cutoff 则 ABSENT，不在该函数里寻找更早版本。Pinned provider 可额外核对版本 hash 与 published_at；这些检查的异常在可选加载 wrapper 内仍转为 UNAVAILABLE。初始化 adapter pin 版本；`PinnedDocument2Runner` 还可以 pin hash/timestamp。

研究 cutoff 为 requested_as_of，否则取 D1 published_at；无时区按 UTC 处理。若 cutoff 早于 D1 publication，保留原 cutoff，记录 warning，**不把 publication time 当作证据时间**。每条数据是否真属于 cutoff，主要仍由 Agent 的研究规则判断。

### 4.2 一次加载，后续恢复复用

第一次把 `PreparedDocument2Inputs` 写到主 run 的 `context/document2/prepared_inputs.json` 并保存 CONTEXT ArtifactRef。后续运行若文件存在且可解码，就直接复用，避免重新查询 Narrative/Event provider。

```text
PreparedDocument2Inputs {
  ticker: string, as_of: datetime,
  global_research: GlobalResearchInput,
  narrative_research: OptionalInput,
  event_library: OptionalInput,
  manifest: Document2InputManifest
}
```

这个复用函数本身没有再次把 prepared file 的 SHA 与原 ArtifactRef 对照，也没有用当前请求重建全部输入。不能把“冻结复用”扩大解释成所有入口都有同样严格的输入快照验证。

### 4.3 Common context

v2 的 O0 common context 只有：

```json
{
  "ticker": "TICKER",
  "as_of": "ISO datetime",
  "future_nodes": [],
  "horizontal_indicators": {}
}
```

v2.1 在 O0 common 中再加入 `document_schema_version`、`entity_relations`、完整 `event_library: OptionalInput`。默认 v2 O0 没有显式注入 Event Library，也没有显式注入 entity_relations，虽然 Candidate skill 文字提到 Event Library 可辅助研究。

### 4.4 节点注入矩阵

以下是明确写入 `context.json` 的字段；同 workspace 可能还有历史工件，但不能把“文件可见”当作“本节点要求读取”。

| 节点 | 在 common 之外注入 | 衔接特点 |
| --- | --- | --- |
| O0 Candidate C1/C3/C5 | `source_role`、`primary_source`、`narrative_run_id=null` | primary_source 是该分支 D1 报告全文；没有显式加入其他两份报告 |
| O0 Candidate Narrative | 同上，primary_source 为 JSON 序列化 Narrative payload，narrative_run_id 为来源 run | 独立候选视角，不让其他分支都重复读 Narrative |
| O0 Synthesis | `candidate_sets` | key 为 c1/c3/c5/narrative，只含成功分支；代码为每条 Candidate 添加跨来源 candidate_ref |
| C1/C3/C5 Domain Review | `reviewer_role`、`original_domain_report`、`provisional_shells` | original_domain_report 为对应 D1 报告；provisional_shells 字段内装整个 Synthesis result，包括 unassigned 和 warnings |
| O0 Finalization | `provisional_shells`、`domain_reviews` | 只合并成功 reviews，key 为 C1/C3/C5；没有代码投票或自动套用 reviewer patch |
| O1 四轮/六轮 | `canonical_shell`、完整 `o0_finalization`、`research_cutoff_at`、`source_global_research_published_at`、完整 `global_research`、`narrative_research`、`event_library`、`turn` | 当前 Shell 为完整对象；O0 topology 包含所有 Seeds；D1 三报告等完整重复写入每轮 context |
| v2.1 O1 补充 | 再有 ticker、document_schema_version；Scan 后加入 `open_discovery_scan`；Selection 后加入 selection 与累计 late additions；有 resolution 时加入累计 resolution | Scan 只读固定输入；后四轮用 envelope 继续 canonical/sidecars |

O1 默认 v2 context 没有独立 ticker 字段，但 Worker request 有 ticker，global_research 内也有 ticker；v2.1 明确补入顶层 ticker。

Event Library AVAILABLE 时，每一个 O1 turn 都注入完整 payload；`event_library_injected` 并不使后续轮跳过 payload。非 AVAILABLE 时 context 只有 status/source/as_of/metadata/warning，加 `payload_injected_earlier_in_thread` 标志。

**上下文体量现状：**D2 当前没有按 Shell 切分 D1 三报告、Narrative、Reference View，也没有按 token budget 做自动裁剪。每轮 context 是一个整体 JSON，要求 Agent 从文件读取。Sibling Shells 注入的是 O0 Seeds，不是其他 Shell 已完成的 State/Factors/Gaps。各 Shell 独立研究，不会通过后续轮 context 自动获得兄弟 Shell 的完整成果。

证据：[输入 loader](C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/workflows/codex_document2/inputs.py:249)、[common/event context](C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/workflows/codex_document2/orchestrator.py:1256)。

## 5. Prompt、Internal Skill 与工具注入

### 5.1 每个 turn 固定六份文件

Runner 在对应 workspace 写入：

```text
attempts/<attempt_id>/input/
  AGENTS.md           ← prompts/codex_v2/document2/AGENTS.md
  agent.md            ← 该节点 agent asset
  skill.md            ← 该节点 internal skill asset
  context.json        ← 当前节点完整 context，ensure_ascii=False / indent=2
  output_schema.json  ← 当前 output_model 动态生成的 strict JSON Schema
  task.json           ← document2-task-v1、node、required_files、previous_failure
```

dispatch prompt 指令顺序为读 `AGENTS.md → agent.md → skill.md → task.json → context.json`，返回一份符合 output_schema.json 的 JSON。task 的 required_files 也列出 output_schema.json。output_schema 同时作为 Worker request/SDK turn 的 structured output schema 传入。

没有额外 D2 foundation skill，也不会在每轮一起注入全部八份 skill。代码只将当轮 skill 复制为 skill.md。默认 asset_root 是 `prompts/codex_v2/document2`，构造器可传其他 asset_root；因此线上部署资产应与代码版本单独核实。

### 5.2 精确 asset 映射

所有节点同时读取同一个 Document2 AGENTS.md。

| 节点 | agent asset（相对上述 asset_root） | internal skill asset | 输出模型 |
| --- | --- | --- | --- |
| 4 个 O0 Candidate 分支 | `agents/o0.md` | `skills/candidate-discovery.md` | CandidateDiscoveryResult / V21 |
| O0 Synthesis | `agents/o0.md` | `skills/shell-synthesis.md` | ShellSynthesisResult / V21 |
| C1 Review | `agents/c1-review.md` | `skills/domain-review.md` | DomainReviewResult / V21 |
| C3 Review | `agents/c3-review.md` | 同上 | 同上 |
| C5 Review | `agents/c5-review.md` | 同上 | 同上 |
| O0 Finalization | `agents/o0.md` | `skills/shell-finalization.md` | ShellFinalizationResult / V21 |
| v2.1 Discovery Scan | `agents/o1.md` | `skills/open-discovery-scan.md` **当前缺失** | OpenDiscoveryScanV21 |
| v2.1 Discovery Selection | `agents/o1.md` | `skills/open-discovery-selection.md` **当前缺失** | OpenDiscoverySelectionV21 |
| O1 State | `agents/o1.md` | `skills/state-research.md` | v2 ExpectationShell；v2.1 ShellResearchTurnResultV21 |
| O1 Realization | `agents/o1.md` | `skills/realization-research.md` | 同上 |
| O1 Gaps | `agents/o1.md` | `skills/gap-research.md` | 同上 |
| O1 Finalization | `agents/o1.md` | `skills/research-finalization.md` | 同上 |

### 5.3 当前 prompt/skill 的业务要求

| 资产 | 主要职责与约束（当前文本） |
| --- | --- |
| AGENTS.md | 先读全部 task 文件；完整 schema 输出；自然语言身份；不为填字段造值；保留 source role/time/as_of/不确定性；引用失败非阻断；cutoff 是信息可得边界；命令语法/路径错误应自行纠正继续；O0 topology 通常保留，但实质研究需要时可改结构 |
| o0.md | Shell 是共享研究上下文边界；Unit 是可独立更新的中层命题；共享价值链不自动意味着同一 Shell；候选来源不是结构归属边界；O0 决定最终 topology |
| candidate-discovery.md | 高召回持续投资问题；不把已发生结果/指标/机制自动升为 Unit；主要读指定来源；C5 从市场锚推断 underlying expectation；warnings 只记实质覆盖限制 |
| shell-synthesis.md | 对全部 Candidate 做对象层级、重复、因果和共享 context 判断；逐条 disposition；source-qualified candidate_ref；6+ Units 触发 prompt 级 Boundary Challenge，但不是数量上限 |
| c1/c3/c5-review.md + domain-review.md | 以各自原研究视角挑战遗漏、过度提升、Unit integrity、Shell context 与后续影响；必要时研究；空 feedback 合法；recommendation 交 O0，reviewer 不直接改 canonical |
| shell-finalization.md | 整合 reviews 作全局决策；不是投票；retain/rephrase/split/merge/move/add/remove；保留自然语言最终身份；输出 Seed，不提前写 Detail |
| o1.md | 在当前 Shell 内联合研究全部 Units；Sibling Seeds 仅作为边界 context；C1/C3/C5/Future Nodes 是并行 primary assets；研究不是上游字段映射；跨 Unit 共享对象意义一致、后果 Unit-specific；旧文本宣称四轮 same thread |
| state-research.md | 先理解上游与完整 Shell，再建稳定 Parameters；当前 Values 分五个 source roles；可比 previous_value；有意义的缺失 role 留空；输出完整 Shell；仅更新 State 和必要语义 refinement |
| realization-research.md | 研究实现/失败/时序/分配/财务转化机制；不能压缩成单个 Value 的对象为 Factor；用 REQUIRED/BLOCKER/MODIFIER；有 observation interface；允许局部回填 State |
| gap-research.md | evidence-led 和 possibility-led 两引擎；先扩展再判断；探索 known/adjacent/outside-model；低概率/无直接预测来源不能自动淘汰；一个 coherent occurrence/revision；recognition_criteria 消除识别含糊；输出完整 Shell |
| research-finalization.md | 局部 Shell curation/schema closure；保留有效研究；修正 State/Factor/Gap 分类、引用和类型、重复、时间基准；不以对象数量/五种 role 齐全/高概率 Gap 为完成标准 |

这些是模型的业务指令，不等于 deterministic validator 一定逐项执行。例如 State skill 建议 DIRECTION/EVIDENCE 的限定词，代码字段仍是 string；6+ Units 的 Boundary Challenge 没有 deterministic gate。

### 5.4 v2.1 prompt 配套缺口

当前真实文件仍写 v2 的 candidate_id/candidate/reason/references、core_question/boundary_rule/proposition、Factor condition/structural_role/observability、Gap possible_occurrence/recognition_criteria 和“无 wrapper 的 ExpectationShell”。v2.1 代码要求 name/scope/ref、新 Factor/Gap、Baseline 和 research envelope。旧 prompt 没有完成上述字段迁移。

新 Scan/Selection skill 不存在，真实执行会在读取 asset 时抛 `D2_DISCOVERY_ASSET_MISSING`，kind=SYSTEM、retryable=false；不会自动生成空 Scan/PARK。现有离线新版测试通过临时 asset_root fixture 提供这些文件，不证明仓库 prompt 完整或真实模型已验收。

### 5.5 Worker 额外指令和工具权限

新 thread 的 Worker base instructions 要求在当前 run workspace 工作、保持 audit 边界、先读 attempt-local AGENTS/task、只用授权 MCP、限制 subagent 数。SDK config 开启 live native web search，并配置 source_capture；D2 request 的 data_mcp_enabled 缺省 true。

Data MCP 按 node/role 授权：

| 节点 | Data tool 权限上限 |
| --- | --- |
| O0 Candidate C1 | C1 domain tools |
| O0 Candidate C3 | C3 domain tools |
| O0 Candidate C5 | C5 domain tools，并排除配置中不支持的一组市场工具 |
| O0 Candidate Narrative | O0 role 上限为空 |
| O0 Synthesis / Finalization | O0 role 上限为空 |
| Domain Review C1/C3/C5 | 对应 researcher role 上限 |
| 所有 O1 轮 | C1 ∪ C3 ∪ C4 ∪ C5 的只读工具集合 |

Data 上限为空不等于禁止 native web search；Data guide/read 服务与具体 tools 也不同。工具实际可调用范围还受 contract exposure、capability、ticker scope 和 provider 可用性约束。

D2 本身调用 `allow_subagents=False`；`max_subagents` 即使有配置，也不会开启这些 turn 的 multi-agent。这里的 Shell 并发是编排层多个 Worker jobs，不是模型主动 spawn 子 Agent。

证据：[attempt 注入](C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/workflows/codex_document2/runner.py:291)、[Data policy](C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/data_runtime/policy.py:145)、[SDK 指令及工具](C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/codex_worker/sdk_runtime.py:240)。

## 6. Schema 阅读规则与中间交接合同

以下用字段级类型记法完整展开业务交接模型，避免复制每个 JSON Schema 中大量相同的 `$defs`。`T[]` 表示数组，`string|null` 表示可空；没有 `=...` 的字段在 Pydantic 中没有缺省值。`=[]`/`={}` 为 default_factory，`=null` 为可省略的可空字段。**这不是实际交付格式**；实际交付是一份 JSON。

Runner 使用 `strict_json_schema(output_model.model_json_schema())` 动态生成机器 schema。转换会递归删除 default，把每个 object 设为 `additionalProperties:false`，并把所有 properties 放进 required。因此：

- SDK 的输出 schema 要求所有字段出现，包括有缺省的 arrays、nullable 字段；nullable 应写 null，数组可写 []。
- 实际 ingest 的 AgentModel 设为 `extra="ignore"`；多余字段会被忽略，缺省数组可补空，但不替 Agent 补造缺失的核心业务字符串。
- Published document/checkpoint/request 等 ContractModel 为 `extra="forbid"`，直接解码合同较严格。
- 数组没有固定数量下限，核心字符串通常没有 min_length；“完整业务含义、非占位符”多为 prompt 要求，不是字符串非空 gate。

### 6.1 v2 O0 的四类交接

```text
CandidateDiscoveryResult {
  candidates: CandidateUnit[] = [], warnings: string[] = []
}
CandidateUnit {
  candidate_id: string, candidate: string, reason: string,
  references: string[] = []
}

ShellSynthesisResult {
  provisional_shells: ProvisionalShellDraft[] = [],
  unassigned_candidates: UnassignedCandidate[] = [], warnings: string[] = []
}
ProvisionalShellDraft {
  shell_temp_id: string, core_question: string, boundary_reasoning: string,
  candidate_units: ProvisionalCandidateUnit[] = []
}
ProvisionalCandidateUnit {
  candidate_ref: string, candidate_id: string, candidate: string
}
UnassignedCandidate {
  candidate_ref: string, candidate_id: string, candidate: string, reason: string
}

DomainReviewResult {
  reviewer_role: C1|C3|C5, overall_assessment: string,
  targeted_feedback: DomainReviewFeedback[] = [], warnings: string[] = []
}
DomainReviewFeedback {
  feedback_id: string, target: string, issue: string, reasoning: string,
  references: string[] = [], recommendation: string
}

ShellFinalizationResult {
  shells: ExpectationShellSeed[] = [],
  finalization_note: string[] = [], warnings: string[] = []
}
ExpectationShellSeed {
  shell_id: string, core_question: string, boundary_rule: string,
  units: ExpectationUnitSeed[] = []
}
ExpectationUnitSeed {
  expectation_id: string, proposition: string, horizon: string
}
```

Candidate branch 临时编号如 U1/U2 仅分支内唯一。代码向 Synthesis context 添加 `candidate_ref="C1:U1" / "C3:U1" / "NARRATIVE:U1"`，防止同号串分支。Synthesis 输出保留 candidate_ref；Finalization 换为最终自然语言 Shell/Unit 身份。

Review 的 target 是字符串，不是 runtime 自动执行的 patch path。Synthesis/Finalization 是模型作结构判断，不是程序从 Candidate 数组机械拼正式 Unit。

### 6.2 v2.1 O0 交接

```text
CandidateDiscoveryResultV21 {
  candidates: CandidateUnitV21[] = [], warnings: string[] = []
}
CandidateUnitV21 {
  name: string, scope: string, why_material: string, ref: string[] = []
}

ShellSynthesisResultV21 {
  provisional_shells: ProvisionalShellDraftV21[] = [],
  unassigned_candidates: UnassignedCandidateV21[] = [], warnings: string[] = []
}
ProvisionalShellDraftV21 {
  shell_temp_id: string, name: string, scope: string, boundary: string,
  ref: string[] = [], candidate_units: ProvisionalCandidateUnitV21[] = []
}
ProvisionalCandidateUnitV21 {
  name: string, scope: string, why_material: string, ref: string[] = [],
  candidate_ref: string
}
UnassignedCandidateV21 {
  name: string, scope: string, why_material: string, ref: string[] = [],
  candidate_ref: string, reason: string
}

DomainReviewResultV21 {
  reviewer_role: C1|C3|C5, overall_assessment: string,
  targeted_feedback: DomainReviewFeedbackV21[] = [], warnings: string[] = []
}
DomainReviewFeedbackV21 {
  feedback_id: string, target: string, issue: string, reasoning: string,
  recommendation: string, ref: string[] = []
}

ShellFinalizationResultV21 {
  shells: ExpectationShellSeedV21[] = [],
  finalization_note: string[] = [], warnings: string[] = []
}
ExpectationShellSeedV21 {
  name: string, scope: string, boundary: string, ref: string[] = [],
  units: ExpectationUnitSeedV21[] = []
}
ExpectationUnitSeedV21 {
  name: string, scope: string, horizon: string, ref: string[] = []
}
```

v2.1 candidate_ref 改为 `<SOURCE_ROLE>:<candidate.name>`，不是继续依赖不存在的 candidate_id。**当前代码 Unit 字段是 `scope`，不是 `proposition`**；计划示意中出现 proposition，不能据此设计不存在的当前输入字段。

### 6.3 所有版本共用的 State Value union 与枚举

```text
ParameterValueType = NUMBER|RANGE|TIME|STAGE|DIRECTION|EVIDENCE
SourceRole = ACTUAL|MANAGEMENT|SELL_SIDE|INDUSTRY_CHAIN|MARKET_IMPLIED
ValidityState = CURRENT|SUPERSEDED|DISPUTED|RETRACTED
StructuralRole（仅 v2 Factor）= REQUIRED|BLOCKER|MODIFIER

NumberValue { number: float, unit: string }
RangeValue { lower: float, upper: float, unit: string }
TimeValue {
  point: string|null = null, start: string|null = null,
  end: string|null = null, precision: string
}
StageValue { stage: string }
DirectionValue { direction: string }
EvidenceValue { stance: string, strength: string }

StateValueData = NumberValue|RangeValue|TimeValue|StageValue|DirectionValue|EvidenceValue
```

`value` 是上述 union，没有 discriminator 字段。Parameter 的 value_type 在 schema 上是 enum，但 union 本身不根据 parameter_id/parameter 字段自动选择模型。v2.1 validator 额外检查两者一致，v2 没有相同的 deterministic cross-object 检查。

Direction 的 IMPROVING/STABLE/WEAKENING、Evidence 的 SUPPORTING/OPPOSING/MIXED 与 WEAK/MODERATE/STRONG 是 skill 约定；代码 string 字段未将这些值设为 enum。Range 没有 deterministic lower≤upper 检查；Time 没有“point 或 start/end 至少一个”的模型检查。

### 6.4 v2 每轮 O1 输出：完整 ExpectationShell

```text
ExpectationShell {
  shell_id: string, core_question: string, boundary_rule: string,
  units: ExpectationUnit[] = []
}
ExpectationUnit {
  expectation_id: string, proposition: string, horizon: string,
  state: ExpectationState = {parameters:[],values:[]},
  realization_factors: RealizationFactor[] = [], potential_gaps: PotentialGap[] = []
}
ExpectationState {
  parameters: StateParameter[] = [], values: StateValue[] = []
}
StateParameter {
  parameter_id: string, definition: string, value_type: ParameterValueType
}
StateValue {
  state_value_id: string, parameter_id: string, source_role: SourceRole,
  value: StateValueData, previous_value: StateValueData|null = null,
  time_scope: string, as_of: string, citation: string[] = [],
  validity_state: ValidityState = CURRENT
}
RealizationFactor {
  factor_id: string, condition: string, structural_role: StructuralRole,
  current_status: string, impact: string, citation: string[] = [],
  observability: Observability
}
Observability { match_condition: string }
PotentialGap {
  gap_id: string, possible_occurrence: string, derivation: string,
  citation: string[] = [], expected_revision: string,
  recognition_criteria: string|null = null
}
```

每轮不是 delta，也没有单独的 `StateResearchResult` / `GapResearchResult`。下一轮收到上一轮返回的整份 Shell。Stage 1 输出可以已有后续对象；代码不会强制清空它们。正常由 skill 规定保留已有工作、只更新当前目标及必要回填。

Seed 初始化把 Unit 复制成完整 Unit，state/factors/gaps 为空。每次成功输出直接替换 shell；代码没有按 Unit/field merge，也没有自动检查上一轮有效对象有没有被遗漏。

### 6.5 v2.1 每轮完整 canonical Shell

```text
ExpectationShellV21 {
  name: string, scope: string, boundary: string, ref: string[] = [],
  units: ExpectationUnitV21[] = []
}
ExpectationUnitV21 {
  name: string, scope: string, horizon: string, ref: string[] = [],
  state: ExpectationStateV21 = {parameters:[],values:[]},
  expectation_baseline: ExpectationBaselineV21[] = [],
  realization_factors: RealizationFactorV21[] = [],
  potential_gaps: PotentialGapV21[] = []
}
ExpectationStateV21 {
  parameters: StateParameterV21[] = [], values: StateValueV21[] = []
}
StateParameterV21 {
  name: string, definition: string, value_type: ParameterValueType, ref: string[] = []
}
StateValueV21 {
  name: string, parameter: string, source_role: SourceRole,
  value: StateValueData, previous_value: StateValueData|null = null,
  time_scope: string, as_of: string, validity_state: ValidityState = CURRENT,
  ref: string[] = []
}
ExpectationBaselineV21 {
  name: string, baseline: string, ordinary_progress: string,
  open_frontier: string, time_scope: string, ref: string[] = []
}
RealizationFactorV21 {
  name: string, mechanism: string, current_status: string,
  materiality_context: string, scope_boundary: string, ref: string[] = []
}
PotentialGapV21 {
  name: string, why_live: string, revision_logic: string,
  possibility_space: PossibilityV21[] = [], ref: string[] = []
}
PossibilityV21 { name: string, implication: string }
```

Baseline 记录默认预期与普通推进/open frontier；新版 Factor 不再带 structural_role、observability；新版 Gap 不再带 possible_occurrence、recognition_criteria 或 expected_revision 字段。这里不是旧字段改名后保持原监测执行语义，O3 不能继续把每个新版 Gap 当作已定义的触发事件。

`PossibilityV21` 没有独立 ref，证据在其所属 Gap；StateParameter、Shell/Unit/Baseline 都新增 ref。Baseline/Factor/Gap 均没有 Policy comparator、trigger boundary 或交易动作字段。

### 6.6 v2.1 Discovery 与研究 envelope

```text
OpenDiscoveryScanV21 {
  shell: string, units: DiscoveryUnitV21[] = []
}
DiscoveryUnitV21 {
  name: string, candidates: DiscoveryCandidateV21[] = []
}
DiscoveryCandidateV21 {
  name: string, change_hypothesis: string, relevance: string,
  live_basis: string, ref: string[] = []
}

OpenDiscoverySelectionV21 {
  shell: string, selections: DiscoverySelectionV21[] = []
}
DiscoverySelectionV21 {
  unit: string, candidate: string, decision: DEEPEN|MERGE|PARK,
  merge_into: string|null = null, reason: string, research_focus: string,
  ref: string[] = []
}

ShellResearchTurnResultV21 {
  canonical_shell: ExpectationShellV21,
  late_additions: LateAdditionV21[] = [],
  open_discovery_resolution: DiscoveryResolutionV21[] = []
}
LateAdditionV21 {
  unit: string, name: string, discovered_during: string,
  change_hypothesis: string, reason: string, ref: string[] = []
}
DiscoveryResolutionV21 {
  unit: string, candidate: string, resolution: string,
  destination: string|null = null, reason: string
}
```

Scan 是 frozen discovery pool；Selection 必须为每个 `(unit,candidate)` 给 disposition。MERGE target 是**同 Unit 的 Candidate 名字**，可链式指向另一个 MERGE，最终可到 DEEPEN 或 PARK，不能成环。

late additions 按 `(unit,name)` 在编排层累积；同键用最新完整记录替换。中间研究轮 resolution 也按 `(unit,candidate)` 累积更新；Finalization 用当轮 authored resolution 作为最后版本，不借旧累计记录悄悄补齐最终遗漏。

Scan/Selection 不修改 canonical。后四轮才通过 envelope.canonical_shell 替换。Discovery sidecars 是过程研究/审计工件，**不在正式 Document2DocumentV21 内，也不在当前发布白名单内**。O3 当前输入不是“正文 + Scan/Selection”，而是发布的 D2 正文。

证据：[所有模型定义](C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/workflows/codex_document2/schema.py:29)、[strict schema 转换](C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/workflows/codex_document2/schema.py:619)、[sidecar 提交](C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/workflows/codex_document2/orchestrator.py:1044)。

## 7. 最终交付：正式 JSON、引用、Outcomes、Handoff

### 7.1 document2.json 顶层

两版本顶层字段相同，shells/outcomes 的内部类型不同：

```text
Document2Document / Document2DocumentV21 {
  schema_version: "document2.v2" 或 "document2.v2.1",
  workflow_version: "codex_document2_v1",
  document2_run_id: string, ticker: string, as_of: datetime,
  source_global_run_id: string,
  input_manifest: Document2InputManifest,
  shells: ExpectationShell[] 或 ExpectationShellV21[] = [],
  shell_outcomes: ShellOutcome[] 或 ShellOutcomeV21[] = []
}
Document2InputManifest {
  global_research: InputManifestEntry,
  narrative_research: InputManifestEntry,
  event_library: InputManifestEntry
}
InputManifestEntry {
  status: AVAILABLE|ABSENT|UNAVAILABLE|NOT_CONFIGURED,
  artifact_ids: string[] = [], workspace_paths: string[] = [],
  source_run_id: string|null = null, as_of: datetime|null = null,
  warning: string|null = null, metadata: object = {}
}
```

manifest 描述 provenance/availability，不在正式 document2.json 中复制整份 D1/Narrative/Event payload。`as_of` 是研究截止，不是 publish timestamp。

### 7.2 Outcomes：未成功的 Shell 仍可定位

```text
ShellOutcome（v2） {
  shell_id: string, status: completed|failed,
  artifact_id: string|null = null,
  failed_stage: ShellResearchStage|null = null,
  failure_kind: SYSTEM|TRANSIENT|FORMAT|SHELL|null = null,
  error_code: string|null = null, error: string|null = null,
  seed: ExpectationShellSeed|ExpectationShellSeedV21|null = null
}
ShellOutcomeV21 {
  shell: string, status: completed|failed,
  artifact_id: string|null = null,
  failed_stage: ShellResearchStage|null = null,
  failure_kind: SYSTEM|TRANSIENT|FORMAT|SHELL|null = null,
  error_code: string|null = null, error: string|null = null,
  seed: ExpectationShellSeedV21|null = null
}
ShellResearchStage = PENDING|DISCOVERY_SCAN|DISCOVERY_SELECTION|STATE|
                     REALIZATION|GAPS|FINALIZATION|COMPLETED|FAILED
```

只有完成四轮/六轮的 Shell 放入正式 `shells`。失败的 Shell 不以半成品冒充成功正文，通过 shell_outcomes.failed + Seed + error 交付。Bundle 内部仍使用 ShellOutcome 的 shell_id，即使 v2.1；assembler 才映射正式 outcome 的 `shell` 字段。

### 7.3 v2 正式 JSON 示例

以下为**结构示例，不是真实 ticker 研究产出**。展示一个完整 Unit 及每种业务数组的位置；未展示全部 StateValueData 变体，见 6.3。

```json
{
  "schema_version": "document2.v2",
  "workflow_version": "codex_document2_v1",
  "document2_run_id": "d2-example",
  "ticker": "EXAMPLE",
  "as_of": "2026-10-03T00:00:00Z",
  "source_global_run_id": "global-example",
  "input_manifest": {
    "global_research": {"status":"AVAILABLE","artifact_ids":[],"workspace_paths":[],"source_run_id":"global-example","as_of":"2026-10-03T00:00:00Z","warning":null,"metadata":{}},
    "narrative_research": {"status":"ABSENT","artifact_ids":[],"workspace_paths":[],"source_run_id":null,"as_of":null,"warning":null,"metadata":{}},
    "event_library": {"status":"AVAILABLE","artifact_ids":[],"workspace_paths":[],"source_run_id":"event-library:EXAMPLE:v1","as_of":"2026-10-02T00:00:00Z","warning":null,"metadata":{"view":"REFERENCE_VIEW"}}
  },
  "shells": [{
    "shell_id": "新产品商业化研究系统",
    "core_question": "新产品能否形成可持续的商业贡献？",
    "boundary_rule": "共同研究客户认证、交付和财务转化，区分独立可更新的命题。",
    "units": [{
      "expectation_id": "新产品商业贡献",
      "proposition": "新产品逐步形成有意义的收入贡献。",
      "horizon": "未来两个财年",
      "state": {
        "parameters": [{"parameter_id":"新产品季度收入","definition":"同一产品口径下的季度已确认收入","value_type":"NUMBER"}],
        "values": [{"state_value_id":"当前季度实际收入","parameter_id":"新产品季度收入","source_role":"ACTUAL","value":{"number":10.0,"unit":"USD million"},"previous_value":null,"time_scope":"示例季度","as_of":"2026-10-02","citation":["【cite:O1】"],"validity_state":"CURRENT"}]
      },
      "realization_factors": [{"factor_id":"客户认证到批量订单的转换","condition":"已完成认证的客户进入持续采购","structural_role":"REQUIRED","current_status":"认证进展存在，规模化采购仍未完全确定","impact":"影响收入兑现的时序与规模","citation":["【cite:O1】"],"observability":{"match_condition":"客户采购承诺和后续交付证据"}}],
      "potential_gaps": [{"gap_id":"关键客户取消采购","possible_occurrence":"关键客户取消原定产品采购","derivation":"客户集中度使单一采购决策能够改变当前兑现路径","citation":["【cite:O1】"],"expected_revision":"下调商业贡献或延后其实现周期","recognition_criteria":null}]
    }]
  }],
  "shell_outcomes": [{"shell_id":"新产品商业化研究系统","status":"completed","artifact_id":"shell-artifact-example","failed_stage":null,"failure_kind":null,"error_code":null,"error":null,"seed":null}]
}
```

### 7.4 v2.1 Shell JSON 示例

同样是结构示例。此对象放在正式 document2.json.shells 内；研究 turn 则包在 `canonical_shell` 字段中。

```json
{
  "name": "新产品商业化研究系统",
  "scope": "研究产品进入客户系统到规模化财务贡献的路径",
  "boundary": "共享客户、认证、供给与交付背景，保留 Unit 的独立更新性",
  "ref": [],
  "units": [{
    "name": "新产品商业贡献",
    "scope": "新产品产生有意义商业贡献的实现过程与边界",
    "horizon": "未来两个财年",
    "ref": [],
    "state": {
      "parameters": [{"name":"新产品季度收入","definition":"同一产品口径下的季度已确认收入","value_type":"NUMBER","ref":[]}],
      "values": [{"name":"当前季度实际收入","parameter":"新产品季度收入","source_role":"ACTUAL","value":{"number":10.0,"unit":"USD million"},"previous_value":null,"time_scope":"示例季度","as_of":"2026-10-02","validity_state":"CURRENT","ref":[]}]
    },
    "expectation_baseline": [{"name":"现有客户逐步放量的默认路径","baseline":"现有产品在已进入客户系统内逐步爬坡","ordinary_progress":"既有计划内的认证和交付继续推进","open_frontier":"新客户大规模采用与利润转化仍未确定","time_scope":"未来两个财年","ref":[]}],
    "realization_factors": [{"name":"认证到持续采购的转换机制","mechanism":"通过产品验证、客户采用与持续交付转成收入","current_status":"部分环节已建立，规模兑现尚不确定","materiality_context":"决定商业贡献的时序和强度","scope_boundary":"认证本身不等于规模化订单或现金贡献","ref":[]}],
    "potential_gaps": [{"name":"客户采购模式的结构变化","why_live":"客户可能调整架构、采购策略或供应商组合","revision_logic":"采购模式变化会改变目前的收入路径与受益分配","possibility_space":[{"name":"采购内化","implication":"外部供应份额和单位价值下降"},{"name":"多供应商扩展","implication":"总需求扩大但单家份额更不确定"}],"ref":[]}]
  }]
}
```

### 7.5 引用交付合同

Runner 每轮读取 attempt observations，调用 CitationPromotionService；推广异常只形成 warning，不阻止业务 JSON。裸 `O#` 或独立 `【cite:O#】` 字符串写成 `D2REF:<attempt_id>:O#`，以免跨 turn 撞号。它不对任意 prose 内部的所有 alias 子串作全局重写。

Assembler 把正式成功 Shell 的 citations/refs 重新编号成 `【cite:O1】...`，并生成：

```text
Document2CitationManifest {
  schema_version: "document2-citation-manifest-v1",
  run_id: string, artifact_id: string,
  entries: Document2CitationEntry[] = [], warnings: string[] = [],
  created_at: datetime
}
Document2CitationEntry {
  alias: string, status: RESOLVED|UNRESOLVED|INVALID,
  origin_run_id: string|null = null, origin_attempt_id: string|null = null,
  origin_alias: string|null = null, source_id: string|null = null,
  url: string|null = null, title: string|null = null, warning: string|null = null
}
```

来源识别支持 D1-O#、D2REF、DoxAtlas:run_id 和 HTTP(S) URL；未知字符串标 INVALID，来源缺失标 UNRESOLVED，不扔掉业务对象。DoxAtlas 有 run_id、URL 有合法前缀即可在这一步标 RESOLVED，不意味着该段业务结论已经被独立事实核验。

v2 重写 State Values、Factors、Gaps 的 citation；v2.1 再覆盖 Shell/Unit/Parameter/Value/Baseline/Factor/Gap 的 ref。Discovery sidecars 不参与正式 remapping；outcome.seed 也不经过该遍历。

发布的每个 Shell `shell.json` 是 assembly 前的 canonical snapshot，可能仍使用 D2REF/D1 namespace。最终 document2.json 是重写引用后的正式正文。**不能默认拿正式 citation manifest 去解所有 per-Shell snapshot 的原始 alias。**

CitationStatus：没有任何 manifest entries 为 UNAVAILABLE；全部 RESOLVED 为 COMPLETE；存在 UNRESOLVED/INVALID 为 PARTIAL。该状态独立于 publication_state，引用 PARTIAL 不会让完整 Shell 集的 publication_state 自动 PARTIAL。

### 7.6 发布白名单与 handoff

| 主 run 路径 | 内容 | 正式发布 |
| --- | --- | --- |
| `artifacts/document2/document2.json` | 完整聚合文档 | 是 |
| `artifacts/document2/document2_citation_manifest.json` | 正式引用目录 | 是 |
| `artifacts/document2/document2.md` | Shell/Unit 摘要与对象数量、失败列表 | 是；不是包含全部正文研究内容的另一份完整报告 |
| `artifacts/document2/shells/<hash>/shell.json` | 每个成功 Shell 的 canonical snapshot | 是 |
| `artifacts/document2/o0/final_shell_seeds.json` | O0 reviewed Seeds，含 notes/warnings | 是 |
| O0 candidates/synthesis/reviews、turn snapshots | 调试、恢复、来源追踪 | 不在正式 publish 白名单 |
| v2.1 Scan/Selection/late additions/resolution | discovery 过程工件 | 不在正式 publish 白名单 |

```text
Document2HandoffV1 {
  schema_version: "document2-handoff-v1",
  run_id: string, ticker: string, source_global_run_id: string,
  document2_artifact_id: string,
  citation_manifest_artifact_id: string|null = null,
  publication_state: COMPLETE|PARTIAL,
  citation_status: COMPLETE|PARTIAL|UNAVAILABLE,
  published_at: datetime
}
```

handoff 不内嵌正文，也没有直接列每个 Shell/Discovery 路径。消费者通过 document2_artifact_id 找 PublishedDocument。

`_publish` 先调用 workspace.publish 创建 immutable release，再校验原 artifact body/hash/size、登记 ArtifactRef.published 和 PublishedDocument。配置 PublishedDocumentStorage 时正文存外部 storage，metadata 存 storage_path；否则 content_text 可内联。未配置外部 storage 且单文件 >2 MiB，非 managed 调用会抛 `PUBLISHED_DOCUMENT_STORAGE_REQUIRED`；managed initialization 有现行例外。初始化 `_d2` adapter 目前额外要求能取得本地 content_text 来交付文件，不能假定所有入口都完整支持同一外置正文路径。

`publication_state=COMPLETE` 只取决于 outcomes 非空且全部 completed；否则 PARTIAL。O0 分支 warnings、optional-input warnings、引用 unresolved 不直接改变这个判定。它**不是研究质量全部验收通过**的证明。v2 COMPLETE 成为 current，v2.1 即便 COMPLETE 也不成为 current。

证据：[Assembler](C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/workflows/codex_document2/assembler.py:35)、[发布状态与白名单](C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/workflows/codex_document2/orchestrator.py:363)、[发布存储逻辑](C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/workflows/codex_document2/orchestrator.py:1349)。

## 8. 持久化状态、Checkpoint 与恢复逻辑

### 8.1 两种 checkpoint 不要混淆

通用 WorkflowCheckpoint 保存 node enum 的 completed/current/failed 列表，不表达每个 Shell 的独立阶段实例。D2 自己的 Document2Checkpoint 才记录 O0 stage pointers、各 Shell RunState。

```text
Document2Checkpoint {
  document_schema_version: document2.v2|document2.v2.1 = document2.v2,
  schema_version: "document2-checkpoint-v1",
  run_id: string, source_global_run_id: string, o0_workspace_run_id: string,
  o0_thread_ids: map<string,string> = {},
  completed_stages: string[] = [], stage_artifacts: map<string,string> = {},
  attempt_workspaces: map<string,string> = {},
  final_shell_seed_path: string|null = null,
  shell_runs: map<string,ShellRunState> = {},
  warnings: string[] = [], updated_at: datetime
}
ShellRunState {
  shell_id: string, workspace_run_id: string, thread_id: string|null = null,
  stage: ShellResearchStage = PENDING,
  canonical_path: string|null = null, canonical_sha256: string|null = null,
  stage_outputs: map<string,ArtifactRef> = {},
  discovery_scan_ref: ArtifactRef|null = null,
  discovery_selection_ref: ArtifactRef|null = null,
  late_additions_ref: ArtifactRef|null = null,
  discovery_resolution_ref: ArtifactRef|null = null,
  snapshot_paths: string[] = [], event_library_injected: boolean = false,
  error: string|null = null
}
```

O0 stage_artifacts 常见 key 为 `o0:candidate:c1/c3/c5/narrative`、`o0:synthesis`、`o0:review:c1/c3/c5`、`o0:final`。completed_stages 当前主要记 `o0`，不应把它当成完整 wave 列表。

### 8.2 artifact / bundle / published body 的关系

```text
ArtifactRef {
  workflow_version, research_lane, artifact_id, run_id, node, attempt_id,
  kind, relative_path, sha256, size_bytes, content_type,
  published: boolean=false, created_at: datetime
}
PublishedDocument {
  artifact_id: string, run_id: string,
  artifact_kind: report|bundle|manifest,
  sha256: string, size_bytes: integer, content_type: string,
  content_text: string|null = null, storage_path: string|null = null,
  published_at: datetime
}
Document2Bundle {
  workflow_version: "codex_document2_v1", research_lane: "document2",
  run_id: string, ticker: string, source_global_run_id: string,
  status: draft|published|failed,
  publication_state: COMPLETE|PARTIAL|null = null,
  citation_status: COMPLETE|PARTIAL|UNAVAILABLE = UNAVAILABLE,
  artifacts: map<string,ArtifactRef> = {}, shell_outcomes: ShellOutcome[] = [],
  checkpoint: Document2Checkpoint|null = null,
  handoff: Document2HandoffV1|null = null,
  current: boolean = false, created_at: datetime, published_at: datetime|null = null
}
```

ArtifactRef 只保存定位/hash，不带正文。PublishedDocument 必须且只能有 content_text/storage_path 其中一个；内联正文必须 size 一致且 ≤2 MiB。因此上一节所述 managed exception 只绕过 `_publish` 的显式 `PUBLISHED_DOCUMENT_STORAGE_REQUIRED` 分支，**并没有取消 PublishedDocument 模型本身的 2 MiB 上限**。

Runner 成功时写两处：子 workspace 的 `artifacts/snapshots/<attempt_id>.json`，以及主 D2 run 的 `artifacts/document2/turns/<artifact_key>/<attempt_id>.json`；ArtifactRef 指向后者。v2.1 raw stage snapshot 保存完整 envelope，不只 canonical_shell。

### 8.3 v2 恢复

O0 根据 stage_artifacts 查 ref/path、读文件、对 hash、按模型解码；已有 final seeds 可以直接跳过整个 O0。v2 若 stage pointer 的工件不存在/损坏，`_restore_stage` 返回 None，允许重新执行相应 stage。

O1 从 child workspace 的 `artifacts/shell.json` 恢复 canonical；stage 表示最后成功阶段，据此跳过已完成轮。`_restore_or_initialize_shell` 本身不核对 canonical_sha256，读/解码失败会重新从 Seed 初始化。v2 每轮成功会记录 stage、snapshot path、canonical_path，但没有新版那样逐轮成功 ArtifactRef 的明确 replay 链。

这意味着不要把 v2 的“读 canonical + 跳过 stage”描述成 v2.1 那种“从成功 raw outputs 全量重建效果”的同等恢复保证。

### 8.4 v2.1 恢复：成功输出先于效果

进入一个 Shell 分支时，从 Seed 构造初始空 canonical，并一次查询该 run 的 artifacts、按本 Shell 的 `turns/v21/shells/<hash>/...` 前缀收集可恢复 outputs。每轮：

1. 优先使用 state.stage_outputs 的 exact ref，否则采用查到的该 Shell/stage 成功工件。
2. 恢复输出时核对 hash、解码，并用当轮 frozen context 再跑 structural validator。
3. 没有已成功输出才调用模型；完成状态声明某阶段成功但对应 output 缺失则报 integrity error。
4. **先写 stage_outputs 成功指针、保存 checkpoint**。
5. 提交 Scan/Selection 或 canonical + late additions/resolution sidecars。
6. 再更新 stage/canonical hash/snapshot list、保存 checkpoint。

如果第 4 步后提交 sidecars/canonical 中断，下次可重放成功输出的效果，不重复研究模型调用。已有成功输出 hash/类型损坏、已成功 output 与 frozen context 不一致，会抛 RuntimeError 阻止整个 run 静默重建；这属于现行 integrity gate。

v2.1 的 O0 stage pointer 缺 ref/坏 hash/坏类型也会抛 integrity failure，而非像 v2 返回 None 重跑。

### 8.5 managed initialization 的 durable child

`Document2TurnRunner.run()` 带 `@durable("d2")`。存在 initialization execution_scope 时，会展开每个实际 turn 的 child NodeSpec，逻辑 key 包含：

```text
<parent key>.<node.value>:<workspace_run_id>:<artifact_key 或 default>
```

因此不同 Shell 同名 O1 stage 不会只共享一条 durable 节点记录；workspace/artifact_key 将它们分开。成功 receipt 中保存 Document2TurnResult，含 typed output、ArtifactRef、NodeAttempt、WorkerJob、thread_id、citation manifest、workspace_run_id。恢复优先解码 receipt，必要时按 exact artifact/hash 修复 receipt。

DurableWorker 将 WorkerRunRequest 与 input/prompt hashes、model/provider/effort 冻结在 receipt，赋予 execution_id idempotency_key；重复 dispatch 可重接同一请求/job。恢复旧请求时不因外部 prompt 文件后来变化而自动重写已冻结请求。

Durable wrapper 自身有至多两次执行 ordinal 的有限重试；Orchestrator `_run_with_retry` 还有自身 max_attempts。不能把 constructor 的 max_attempts=1 解释为整条 Worker/infra/durable 路径永远只有一次尝试。

### 8.6 输入请求与重用身份

```text
Document2RunRequest {
  document_schema_version: document2.v2|document2.v2.1 = document2.v2,
  workflow_version: "codex_document2_v1", research_lane: "document2",
  run_id: string, source_global_run_id: string,
  ticker: string|null = null, as_of: datetime|null = null,
  initialization_id: string|null = null,
  force_new: boolean = false, reuse_published_partial: boolean = false
}
StartDocument2Request（API） {
  source_global_run_id: string, run_id: string|null = null,
  as_of: datetime|null = null, force_new: boolean = false
}
```

已有 run 的 checkpoint/document schema 与当前请求不一致，抛 Document2VersionMismatch；force_new 也不允许覆盖错版 run，必须另取 run_id。已有 published COMPLETE 可直接复用；published PARTIAL 仅在 reuse_published_partial=true 时直接复用，否则会继续未完成工作。

force_new 在 Orchestrator 内不自动替请求换 run_id；API launcher 可根据 force_new 创建新 identity。PinnedDocument2Runner 的 v2 identity 由 source_global_run_id/ticker/Event Library version/hash 组成；v2.1 再加 schema version 和 as_of。v2 identity 不包含 cutoff，兼容旧算法。

### 8.7 本地/远端持久化的现行抽象

Orchestrator 只依赖 CodexRuntimeRepository/WorkspaceClient；具体配置可为内存、SQLite、远端或 Hybrid。Hybrid 对 D2 把 attempts、workflow checkpoint、未发布 artifacts、source/citation、正文与详细恢复状态优先留本地；远端主要同步发布 metadata、Bundle lifecycle/current 等投影变化。不是每个 `_save_progress` 都重新写一份远端完整恢复状态。

这里是代码支持的策略，不是本轮查证后的生产 repository 配置。实际远端部署与 DB/egress 行为需要另外读运行配置，本文不作推断。

证据：[恢复代码](C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/workflows/codex_document2/orchestrator.py:902)、[durable child](C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/ticker_initialization/substeps.py:200)、[Hybrid Repository](C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/codex_runtime/repository.py:1966)。

## 9. 校验、重试、降级与硬失败：逐层说明

### 9.1 通用 turn ingestion

正常链路为：

```text
Worker response
  → 去 BOM / 接受 JSON code fence / json.loads
  → ingest_model(Pydantic)
  → 如解码失败，尝试仅 O0 Synthesis/Finalization 的 fallback
  → validate_output（当前仅 V21 分支有具体检查）
  → 非阻断 CitationPromotion
  → 当前 O# 加 D2REF namespace，再次 typed decode
  → 保存 snapshots、ArtifactRef、SUCCEEDED NodeAttempt
```

AgentModel 的多余字段忽略；通用 ingest_model 还可去除 forbidden extension fields，但不会自动将任意错误类型转成正确业务对象。缺核心字段、无法解 JSON/union、枚举非法，仍能触发 FORMAT。

Runner 不是在进入解析之前统一要求 job.status=succeeded。若有可解码且满足合同的结构化结果，会继续接受；对无法解析的结果才进一步判断 Worker 异常/状态和 fallback。NodeAttempt.SUCCEEDED 表示可接受结构化结果已保存，不必与原 Worker status 完全等同。

### 9.2 v2 的 deterministic 业务检查边界

`validate_output` 对 CandidateDiscoveryResult、ShellSynthesisResult、DomainReviewResult、ShellFinalizationResult、ExpectationShell 这些 v2 类型**没有具体检查分支**。它主要依赖 Pydantic 字段形状与 prompt 约束。

所以以下 v2 业务约束不是当前代码的硬 gate：Candidate disposition 全覆盖；语义 ID 非空/唯一；StateValue.parameter_id 在本 Unit 可解析；union shape 与 Parameter.value_type 一致；旧对象完整保留；Gap coverage；6+ Unit Boundary Challenge；每个 Field 有成熟内容。

这并不证明模型一定违反这些要求，只说明其约束由 prompt 而非 deterministic validator 执行。Assembler 同样不替 Agent 做上述实质研究审计。

### 9.3 v2.1 structural validator 的确切规则

| 输出类型/阶段 | deterministic 检查 | 失败效果 |
| --- | --- | --- |
| CandidateDiscoveryResultV21 | 当前分支 Candidate.name 不重复 | FORMAT，可有限重试 |
| ShellSynthesisResultV21 | 所有 retained + unassigned 的 candidate_ref 不重复，集合恰等于输入 Candidate refs | 同上 |
| ShellFinalizationResultV21 | Shell.name 全局不重复；各 Shell 内 Unit.name 不重复 | 同上 |
| DomainReviewResultV21 | 没有额外 context-bound 分支；仍有 typed fields/reviewer_role enum | typed 失败走 FORMAT |
| OpenDiscoveryScanV21 | output.shell 等于当前 canonical.name；每个输入 Unit 恰一次；该 Unit 下 Candidate.name 唯一 | FORMAT，可重试；耗尽后 Shell 可失败 |
| OpenDiscoverySelectionV21 | Shell 与 frozen Scan 一致；每个 `(unit,candidate)` 恰一次 disposition；非 MERGE 的 merge_into 必须 null；MERGE 同 Unit 目标存在且无环 | 同上 |
| 研究 envelope 的每轮 canonical | Unit.name 唯一；Unit 内 Parameters/Values/Baseline/Factors/Gaps 分组各自 name 唯一；Value.parameter 存在；value/previous_value 类型与 Parameter.value_type 一致；各 Gap possibility names 唯一 | 同上 |
| 每轮 late additions | `(unit,name)` 唯一；unit 在 frozen Scan、输入 canonical 或输出 canonical 可识别；discovered_during 恰等于当前 turn | 同上 |
| Finalization discovery closure | 全部 DEEPEN、累计 late、当前新 late 必须出现在当轮 resolution；resolution keys 唯一；resolution 字符串非空；非空 destination 必须有效 | 同上 |

destination 可指向最终当前 Shell 的 Unit/Factor/Gap，或邻接 O0 Shell 的 Unit。当前 Shell 已删除 Unit 不能假借旧 O0 topology 继续作为有效目标。destination=null 可以表达没有 canonical 归宿的自然语言 disposition；没有要求所有 DEEPEN 都产生 Gap，也没有要求固定对象数量。

严格按当前 validator：它没有检查每轮输出 canonical_shell.name 必须等于输入/Scan Shell 名，也没有锁死 O0 Unit 数或成员集合；允许结构调整。没有 enforce 语义质量、Gap surprise、baseline correctness、引用全解析、numeric threshold、研究轮真实 tool calls、research depth 或“不机械复制”规则。也没有检查每个 resolution 一定只对应预期集合（当前要求必要集合被包含，而不是完全相等）。

### 9.4 O0 fallback 的实际内容与边界

只对 Synthesis 与 Shell Finalization 模型有 fallback，没有给 Candidate/Review/O1 凭空补空结果。

- v2 Synthesis fallback：保留每条 Candidate，分别生成 provisional Shell，candidate 文本作为 core_question，reason 作为 boundary_reasoning。
- v2 Finalization fallback：把 provisional drafts 变 Seed，原 candidate 文本保留为 proposition，horizon 为 UNRESOLVED。
- v2.1 Synthesis fallback：保留 Candidate 的 name/scope/ref，每条独立 Shell，boundary=""；不把 why_material 假充为 boundary。
- v2.1 Finalization fallback：保留 provisional name/scope/boundary/ref 和 Units，horizon=UNRESOLVED。

fallback 带 SYNTHESIS_UNAVAILABLE / FINALIZATION_UNAVAILABLE warnings；它保留已有研究，没有重新合成事实。v2.1 对无法解析且已判为非降级 SYSTEM 的 Worker 错误先抛出，不走 fallback；v2 在这一段没有同等的前置 SYSTEM 排除。正常可解析输出也不因为 optional-input/citation warning 触发 fallback。

### 9.5 失败分类与传播

| 类别/情形 | 处理 |
| --- | --- |
| SYSTEM：invalid request/schema、鉴权、权限、unsupported model 等已识别 Worker error | 不可 PARTIAL；通常不可 retry；向 run 上抛 |
| TRANSIENT：timeout/network/rate limit/connection 等 | retryable；耗尽后可作为分支失败 |
| FORMAT：JSON/Pydantic/context-bound structural validator 失败 | retryable；耗尽后可作为 Shell/候选/Review 分支失败 |
| SHELL：其他研究 turn Worker failure | retryable；可分支降级 |
| LeaseLost、CapabilityDenied、InvalidWorkspacePath、ImmutableWorkspacePath | 不作为研究空缺降级，向上抛 |
| 保存/存储错误、成功 artifact integrity error | 通常不是可降级的研究失败，向上抛 |
| 可选 Narrative/Event provider 失败 | availability=UNAVAILABLE，保留 warning |
| citation promotion/remapping 未解析 | warning/entry status，业务交付继续 |
| 普通 Agent 工具/命令 syntax/path 错误 | prompt 要求修正继续；不等于 orchestrator 自动对任意命令错误设置 gate |

Orchestrator 默认 max_attempts=2；默认 timeout 1800 秒、model=gpt-6-luna、effort=max。实际初始化 adapter 从 settings 获取 model/provider/effort/timeout，并设 max_attempts=1；本轮没有读取部署环境，不能据此宣称生产的实际模型。

bounded retry 把上一错误的最多约 2000 字符写入下个 task.json.previous_failure。fresh_on_retry 默认 true；Domain Review 传 false。不过当前 Runner 已固定 request.thread_id=None，正常 dispatch 实际仍是 fresh thread。

O0 candidate/review 与 O1 Shell 分支通过 gather(return_exceptions=True) 收集；研究类失败可只隔离本分支。O0 Synthesis/Finalization 不在相同的 candidate/review 降级收集层，但有自身 fallback。全局 SYSTEM/integrity 错误会令父 run failed；不能概括成“所有错误都非阻断”。

### 9.6 COMPLETE / PARTIAL / current 的准确判断

```text
至少一个 Shell outcome，且全部 status=completed
    → publication_state=COMPLETE
否则（包括零 Shell）
    → publication_state=PARTIAL

COMPLETE + document2.v2
    → bundle.current=true
其他情况
    → current=false
```

父初始化 adapter 在已有 published handoff 时交付 D2，无条件要求 COMPLETE 的检查不在该入口；质量 annotations 会带 `D2:<publication_state>`。D3 preparer 可接受 published PARTIAL D2，按 failed outcomes 记覆盖缺口，失败 Shell 不放入 expected gap denominator。

这是现状说明，不是建议新增 gate，也不是提议改变现有失败/publication 语义。

证据：[Validator 全文件](C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/workflows/codex_document2/validation.py:11)、[失败分类](C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/workflows/codex_document2/errors.py:15)、[fallback](C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/workflows/codex_document2/recovery.py:11)、[重试](C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/workflows/codex_document2/orchestrator.py:1206)。

## 10. D2 到当前 D3 的实际接口，以及设计时不能忽略的差异

### 10.1 当前 D3 输入准备

`Document3InputPreparer.prepare_initialize` 根据请求中的 document2_run_id：

1. 读取 Document2Bundle，要求 published、有 handoff、ticker 一致；不要求 D2.current，也不要求 COMPLETE。
2. 根据 handoff.document2_artifact_id 取 PublishedDocument，读取正文或外部 storage。
3. 校验 bytes size、SHA256，再用 **Document2Document（v2）** 解码。
4. 遍历 `shell.shell_id → unit.expectation_id → gap.gap_id`，构造 expected_gap_refs。
5. failed shell_outcomes 单列，排除在该分母之外。
6. 加入独立读取的 Event Library Reference View、previous Policy Set。

当前 D3 Runner 根据 v2 document.shells 把正式 document2.json 按 Shell 切片，顶层公共字段在每片保留，shells 只放当前 Shell。该动作发生在 D3 workspace seed 层，不是 D2 逐 Shell产物的正式发布合同。它同样直接按 v2 Document2Document 解码。

### 10.2 若 D3 重构要面向 v2.1，上游已有能力与未衔接项

| D3 设计关心的点 | 当前事实 |
| --- | --- |
| D2 新版是否提供默认预期 | schema 有 Unit.expectation_baseline[]，含 baseline/ordinary_progress/open_frontier/time_scope/ref；但当前旧 prompt 未教模型如何按新版生成 |
| 是否仍有 possible_occurrence/recognition_criteria | 只有 v2；v2.1 正式 Gap 换成 why_live/revision_logic/possibility_space |
| 是否仍以每个 Gap=一个未来触发事件 | v2 更接近这种表达；v2.1 是 revision space 加多个 possibilities，没有这种代码合同 |
| D3 路径身份能否继续取旧三元组 | v2 可以；v2.1 需要面对 name 和 revised Gap semantics，当前消费者未适配 |
| 能否把 Discovery Scan 当作既有正式 handoff | 不能；它是主 run/child workspace sidecar，不在正式 publish 白名单，handoff 不指向它 |
| O3 是否自动收到 D1 报告/Narrative 完整 context | D2 正文只带 manifest；当前 D3 preparer 没有将 D2 的 prepared_inputs 全部向 O3透传 |
| 能否依赖某个 Shell 最终产出已跨 Shell统一 | O0 做 topology 全局判断；O1 各 Shell Detail 独立，之后没有模型全局 Detail review |
| 能否认为 COMPLETE 证明 Baseline/Gap业务成熟 | 不能；COMPLETE 是 Shell execution closure，citation status 另记 |
| 能否直接将 v2.1 published artifact 发给旧 D3 | 不能；旧 parser literal 与字段结构都不兼容，即使它是 published |
| 是否已有版本选择与隔离 | 有 request/checkpoint/run identity 保护；v2.1 current=false，默认入口仍为 v2 |

这些是重构必须明确处理的**接口事实**，不构成自动增加新流程/skill/校验的建议。O3 的目标仍需要由其 own prompt、编排和交付合同确定，不能让 D2 的研究 sidecar 或旧 trigger-shaped Gap 替代 O3 的研究任务。

除 D3 外，当前 W3、v2 read artifacts 等模块也直接 import/解码 Document2Document v2。选择新版正式文档会影响这些消费者；本文只标出边界，没有给它们设计或实施适配。

证据：[D3 输入](C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/workflows/codex_document3/inputs.py:62)、[D3 切片](C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/workflows/codex_document3/runner.py:115)、[W3 消费](C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/persistent_runtime_v2/w3.py:141)、[read 消费](C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/v2_read/artifacts.py:257)。

## 11. Pilot 与测试：能证明什么

Document2PilotCoordinator 是 Pilot-only sequential coordinator，与正式 runtime 的并发方式不同：按 stage dependencies 创建一个 case，等 completion 再建立后继；它不是正式初始化调度器。

单 Shell v2 Pilot 最多 13 个模型节点（包含 Narrative branch），v2.1 为 15 个，增加 Scan/Selection。可从 D1 bootstrap 或已有 D2 checkpoint/source attempt 建 case；选择单个 Shell；允许 O0 handoff override。source-attempt 会复制 frozen attempt inputs，不重新查 provider；bootstrap 第一次加载后缓存上游输入。v2.1 builder 知道 envelope.canonical_shell 与累计 sidecars，避免把整个 envelope 错当作 canonical。

当前关键测试与所覆盖合同：

| 文件 | 主要覆盖 |
| --- | --- |
| `tests/test_codex_document2_workflow.py` | v2 全流程、context、Narrative 缺失/7天规则、candidate/ref lineage、失败隔离/PARTIAL、恢复、发布 storage、Data policy、Hybrid local recovery |
| `tests/test_codex_document2_v21_orchestration.py` | 新版 schema、6轮 context、Selection/finalization retry、MERGE target/cycle、coverage/type/binding、late/resolution、sidecar非发布、效果 replay、成功工件损坏、版本身份、Pilot/pinned |
| `tests/test_document2_pilot_coordinator.py` | Pilot sequential cases、handoff/选择/覆盖输入的流程 |
| `tests/test_ticker_initialization_substeps.py`、`substep_recovery.py`、`internal_rerun.py` | durable child、receipt recovery、精确节点重跑等初始化衔接 |

`test_document2_canonical_contracts.py` / `test_document2_node_contract_matrix.py` 的大量 old promotion/field repair 项不应直接当成本文 Codex D2 v2/v2.1 的现行 validator 证据。

已有 2026-10-03 v2.1 实施验收文件记录组合离线回归 `74 passed, 3 warnings`。**本轮未重新执行该测试组合，也未运行真实模型或生产验收。**本轮做的是代码/资产审计和说明文档，不能把历史 test report 当成本轮跑出的结果。

证据：[新版实施验收记录](C:/Users/WEIXUANXIE/Desktop/DoxAgent/dev_plan/workflow_v2.1/d2_v2.1_orchestration_implementation_acceptance_20261003.md)、[Pilot coordinator](C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/pilot/document2_coordinator.py:59)。

## 12. 给方案设计者的当前状态检查表

这是基于已读代码的现状归纳，便于审查后续 D3 方案是否误读上游，不是本轮批准的新开发项。

1. **先确定所消费 D2 版本。** 默认/当前入口是 v2；本地 v2.1 已增加合同与编排，但 staged、不成为 current、prompt 尚未对齐。
2. **不要把 D2 当作一个总 Agent 按 phase 横扫全部 Shell。** 实际是 O0 global topology、O1 per-Shell sequential research，各 Shell 并发，最后代码 assembly。
3. **不要把 workspace 的延续写成 SDK conversation 的延续。** 当前 D2 turn request 的 thread_id=None；fresh context carryover 是真实机制。
4. **每个 O1 轮返回完整 canonical。** v2 无 wrapper；v2.1 用 envelope；不能拿 patch/delta/报告正文作为当前合同。
5. **新版 Unit 是 scope，不是 proposition。** 以当前 schema 实现为准；新 Gap 是 revision space，旧 possible_occurrence 等字段已不在新版合同内。
6. **新版 Baseline 有字段，但没有独立 turn。** 旧 State/agent skill 未对齐其研究方法；不能假定新版本真实模型自然会正确填它。
7. **Discovery 是研究过程工件，不是现有 O3 发布输入。** 是否要使用它属于新的 handoff 选择，应在方案中显式说明，不能写成系统已经注入。
8. **D2 COMPLETE 不是语义质量结论。** 当前以 Shell outcomes 判定；引用状态、optional input、O0 warnings 单独存在。
9. **v2 与 v2.1 校验强度不同。** v2 mostly shape/prompt；v2.1 有 context-bound structural FORMAT gate；不要笼统概括为“全部宽松非阻断”或“全部强审计”。
10. **没有跨 Shell Detail 的模型 Final Global Pass。** Sibling O0 Seeds 提供 topology，不是其他 Shell 完整研究成果。
11. **上下文仍会重复较大全文。** D2 O1 每轮读完整 global_research、完整 optional payload；沒有自动 Shell-aware 上游裁剪。Document2 的 Shell slicing 则是当前 D3 自己完成。
12. **失败 Shell 正文缺失但 outcome/seed 可追踪。** D3 当前按 published PARTIAL 继续、排除失败 Shell 的 gap coverage 分母；不要在重构描述中悄悄替换这套语义。

## 13. 源码与资产定位索引

| 内容 | 直接入口 |
| --- | --- |
| D2 主编排、并发、上下文、恢复、发布 | [orchestrator.py](C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/workflows/codex_document2/orchestrator.py) |
| 单 turn 的输入文件、schema、Worker request、snapshots | [runner.py](C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/workflows/codex_document2/runner.py) |
| 全部业务模型、内部 request/checkpoint/handoff | [schema.py](C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/workflows/codex_document2/schema.py) |
| D1/Narrative/Event 准备和 cutoff | [inputs.py](C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/workflows/codex_document2/inputs.py) |
| v2.1 deterministic checks | [validation.py](C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/workflows/codex_document2/validation.py) |
| 仅 O0 的保留型 fallback | [recovery.py](C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/workflows/codex_document2/recovery.py) |
| Worker 失败分类 | [errors.py](C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/workflows/codex_document2/errors.py) |
| 发布正文组装与引用重编号 | [assembler.py](C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/workflows/codex_document2/assembler.py) |
| Pin Event Library 与版本身份 | [pinned_runner.py](C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/workflows/codex_document2/pinned_runner.py) |
| 共同执行合同 | [AGENTS.md](C:/Users/WEIXUANXIE/Desktop/DoxAgent/prompts/codex_v2/document2/AGENTS.md) |
| O0 / O1 稳定角色 | [o0.md](C:/Users/WEIXUANXIE/Desktop/DoxAgent/prompts/codex_v2/document2/agents/o0.md)、[o1.md](C:/Users/WEIXUANXIE/Desktop/DoxAgent/prompts/codex_v2/document2/agents/o1.md) |
| 三个 reviewer 角色 | [c1-review.md](C:/Users/WEIXUANXIE/Desktop/DoxAgent/prompts/codex_v2/document2/agents/c1-review.md)、[c3-review.md](C:/Users/WEIXUANXIE/Desktop/DoxAgent/prompts/codex_v2/document2/agents/c3-review.md)、[c5-review.md](C:/Users/WEIXUANXIE/Desktop/DoxAgent/prompts/codex_v2/document2/agents/c5-review.md) |
| O0 四个 internal skills | [candidate-discovery.md](C:/Users/WEIXUANXIE/Desktop/DoxAgent/prompts/codex_v2/document2/skills/candidate-discovery.md)、[shell-synthesis.md](C:/Users/WEIXUANXIE/Desktop/DoxAgent/prompts/codex_v2/document2/skills/shell-synthesis.md)、[domain-review.md](C:/Users/WEIXUANXIE/Desktop/DoxAgent/prompts/codex_v2/document2/skills/domain-review.md)、[shell-finalization.md](C:/Users/WEIXUANXIE/Desktop/DoxAgent/prompts/codex_v2/document2/skills/shell-finalization.md) |
| O1 四个现有 skills | [state-research.md](C:/Users/WEIXUANXIE/Desktop/DoxAgent/prompts/codex_v2/document2/skills/state-research.md)、[realization-research.md](C:/Users/WEIXUANXIE/Desktop/DoxAgent/prompts/codex_v2/document2/skills/realization-research.md)、[gap-research.md](C:/Users/WEIXUANXIE/Desktop/DoxAgent/prompts/codex_v2/document2/skills/gap-research.md)、[research-finalization.md](C:/Users/WEIXUANXIE/Desktop/DoxAgent/prompts/codex_v2/document2/skills/research-finalization.md) |
| v2.1 业务目标（不是当前 prompt 实现） | [d2_v2.1.md](C:/Users/WEIXUANXIE/Desktop/DoxAgent/dev_plan/workflow_v2.1/d2_v2.1.md) |
| 默认参数 | [settings.py](C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/settings.py:164) |

本文的合同展开与示例用于让未接触仓库的方案设计者理解当前接口；需要精确机器 JSON Schema 时，应从上表 schema.py 中选定版本的模型，经 strict_json_schema 生成，与每个实际 attempt/input/output_schema.json 一致，而不要从旧 prose/计划示意反推。
