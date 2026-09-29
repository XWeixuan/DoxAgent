# 运行状态：交易意图、执行结果与 Case JSON 导出实施方案

日期：2026-09-29。勘察代码基线：`f0f171eb`。

本轮交付为开发方案。已阅读当前前端、V2 API / Read 投影、Runtime 路由与 TradeOutput、Executor 接收及成交记录；未修改业务代码、生产数据库或部署服务。截图用于理解界面诉求，未把截图中的数量当作当前生产事实。

## 1. 产品目标与本次决策

运行状态页应回答三件事：消息得出了什么研判、是否形成交易意图、该意图最终有没有实际建仓成交。导出让用户离线复核所选 Case 的完整业务依据，而不需要逐条展开、复制正文或收集内部审计日志。

本次前后端一起改，主要修改 V2 Read 投影、查询契约和 V2 前端。交易模型、路由决策、资金管理、下单与重试策略不属于此次功能开发范围。

采用以下明确口径：

1. 图的第四列改为“研判结果”，原交易节点位置显示“交易意图产生”；第五列新增“交易执行”，包含“交易执行”“交易未执行”两个节点。
2. **交易执行仍要求实际 ENTRY 成交。** 订单接收、提交成功、券商状态为 Filled 都不能替代有效成交记录。部分建仓成交也算执行，数量与未完成部分在详情说明。
3. “交易未执行”表示有意图、没有有效 ENTRY 成交且已有确定终结依据。等待执行、等待行情、提交结果未知不归入该节点；它们暂时停留在“交易意图产生”。
4. “交易意图产生”以正式 `trade_intents` 记录为依据；生成后因重复、过期等原因被终结，也计为产生过意图。仅有模型建议、`analysis_trade_decisions` 或未释放的 CLOSED candidate 不计入。
5. 最近记录表将“最终结果”改名“研判结果”，增加相邻“交易执行”列，显示执行、未执行、等待执行、结果未知或 `—`。原“状态”列保留 Case 处理状态。
6. 导出采用前端有界并发收集现有 API，生成一个 JSON 下载文件。后端补齐缺少的 `view_id` 读取能力，不新增导出队列、任务表、后台文件存储或服务。
7. **全选 = 当前已加载记录**。按钮明确写“全选已加载 N 条”，与现有“更早记录”分页配合。不会隐式导出筛选下尚未加载的全部历史；若后续需要跨全部历史的一键导出，应作为独立需求扩展。

## 2. 勘察结论与关键证据

| 当前实现 | 代码位置 | 对方案的影响 |
|---|---|---|
| 图固定四列，结果节点 `TRADE_EXECUTION` 显示“交易执行”；表格与筛选复用相同结果名 | `frontend/v2/src/pages/runtime.tsx`：`nodeNames`、`positions`、`FlowGraph`、`CaseRows` | 需要新增节点和第五列，不能只替换中文文案 |
| 当前 `TRADE_EXECUTION` 由有效 ENTRY 数量大于零产生，并可随成交更正撤回 | `src/doxagent/v2_read/executions.py`：`ExecutionProjector.project` | 当前节点确实是实际成交，不能将它直接改名为意图 |
| `pending_intent` 已将正式意图投影为 `ExecutionSummary`，未接收时也有记录 | 同上：`pending_intent` | 可复用现有执行读模型做意图与执行状态归纳，不需要新建业务账本 |
| TradeOutput 对 CLOSED 先写 candidate；控制抑制写 analysis decision；正常/重复/过期输出写 trade intent | `persistent_runtime_v2/trade_output.py`：`record` | 三种记录必须区分，`route=TRADE` 不等于正式意图，也不等于成交 |
| durable intake 只确认接收；无 execution pin 会落 `OUTPUT_RECORDED` | `trade_execution/intake.py`、`trade_execution/repository.py`：`admit` | `EXECUTION_ACCEPTED` 不是成交；`OUTPUT_RECORDED` 应解释为仅记录输出 |
| Entry finish 有 FILLED、PARTIAL_FILLED、FAILED、DIRECTION_DISABLED；成交更正会影响有效数量 | `trade_execution/repository.py`：`finish`；`v2_read/executions.py` | 要按成交优先判断；失败/禁用只有在零成交时才是未执行 |
| Case 初始投影、意图更新与执行更新都可能重写 Case；图从 results 直接连到结果 | `v2_read/projectors.py`、`v2_read/graph.py` | 应共用一个交易状态归纳函数，避免后来的 Case 更新覆盖已有成交状态 |
| 结果筛选是后端 `json_each(payload,'$.results')` | `v2_read/repository.py`：列表 result 条件；`api_v2/runtime.py` | 扩展结果枚举后可复用查询，不需要前端过滤当前页 |
| 图有成员、边、指标贡献、路径汇总及 SSE revision | `v2_read/graph.py`、`graph_seed.py`、`api_v2/graph.py` | 新节点必须同时进入 baseline、增量、节点详情和路径聚焦 |
| CaseDetail 含 W1/W2/W3 与引用；reasoning 是 ContentRef，子列表首批通常 20 条 | `api_v2/case_detail.py`；`frontend/v2/src/pages/runtime-case.tsx` | 直接 JSON.stringify(CaseDetail) 会缺少完整正文和后续分页 |
| attempts/messages 支持 view_id；candidates/executions/orders/fills 暂未全部支持同一 view | `api_v2/runtime.py`、`executions.py` | 导出前必须补齐固定读取序列，避免混用点击时明细和后来的成交 |
| 正文已有连续分块与身份校验 | `api_v2/content.py`；`frontend/v2/src/core/content.ts` | 复用 `readContentChunk` / `appendContent`；不用 DOM 抓取或截断后的预览 |
| API 普通查询限时 2s，详情 5s；请求体上限 64KiB；读视图有效期 1 天 | `api_v2/app.py`、`v2_read/settings.py`、`api_v2/views.py` | 不创建一次读取任意多 Case 的大请求，采用现有分页和分块 |

旧 PRD Part 2 §6.5.2 把实际成交列为终点；本次用户需求改变图的展示结构，但保留其“不能把交易意图当成交”的业务约束。实现时同步修订 §6.5、§6.6、§6.7 及刷新相关条款。

另参考 `docs/trade-execution-audit-20260928.md`：历史确有正式意图零成交、拒单、行情失败等情形。该文档是历史取证，本方案不把其中当时的生产数量当作本日实时值，也不顺带实施其资金和重试改造。

## 3. 交易状态模型

### 3.1 枚举与新增字段

保持现有 `TRADE_EXECUTION` 含义；为 `ResultKind` / `NodeId` 增加：

```ts
type AddedResultKind = "TRADE_INTENT" | "TRADE_NOT_EXECUTED";
type CaseTradeState =
  | "NOT_APPLICABLE" // 无正式意图，也无正式执行证据
  | "PENDING"        // 有意图，尚未形成明确执行终态
  | "UNKNOWN"        // 投递/执行结果未知或证据不足
  | "EXECUTED"       // 至少一次有效 ENTRY 成交
  | "NOT_EXECUTED";  // 所有意图均明确终结，且没有 ENTRY 成交

// CaseSummary 新增；CaseDetail.summary 使用同一对象
trade: {
  intent_count: Count;
  state: CaseTradeState;
  reason_codes: string[];
};
```

`results` 仍是可多值的查询/图成员标签，允许同时包含 `EVENT_DISCOVERY`、`TRADE_INTENT`、`TRADE_EXECUTION`。前端将前三种交易标签按列分别呈现，不在“研判结果”中重复显示实际成交。

`ExecutionSummary` 增加 `execution_state`（PENDING / UNKNOWN / EXECUTED / NOT_EXECUTED）与 `execution_reason_codes`，供 Case 归纳、明细和导出共享。保留现有原生 `intent_status`、`intake_status`、`entry_result`、`entry_reason`，用于解释，不靠前端重新推导。

### 3.2 单个意图的判定顺序

| 优先级 | 事实 | 展示 / 执行状态 |
|---|---|---|
| 1 | 最高更正版本中，ENTRY 有效成交数量 > 0 | EXECUTED。即使随后失败、过期或只有部分成交，也不能抹去已成交 |
| 2 | 有明确执行不确定性；或记录声称成交但有效成交证据缺失 | UNKNOWN，保留原因；不猜成未执行 |
| 3 | ENTRY result 为 FAILED / DIRECTION_DISABLED，零有效成交 | NOT_EXECUTED，显示 entry_reason；禁用不是技术失败 |
| 4 | 未接收执行，正式 intent 为 DUPLICATE_POLICY / DUPLICATE_REALTIME_OUTPUT / EXPIRED_SEMANTIC_DAY / OUTPUT_RECORDED | NOT_EXECUTED，显示对应原因 |
| 5 | READY / EXECUTION_ACCEPTED，尚无终态和有效成交 | PENDING |
| 6 | 未识别状态或关键证据不完整 | UNKNOWN；保留原状态，不静默新增终态 |

重要边界：

- 时间超过 expires_at 本身不作为未执行证据；若已可能提交，必须看原系统的终结/对账事实。
- UNKNOWN 投递不能沿用当前 Case 的 `UNKNOWN → READY` 文案映射判成“正常等待”；读取意图/执行事实保留不确定性。
- `has_actual_fill` 当前可含 EXIT，应以 `filled_quantity` 的 ENTRY 数量为执行口径。顺便让执行卡的“已实际成交”也使用同一 ENTRY 状态。
- 有历史 ENTRY 成交被更正为零、而 entry_result 仍写 FILLED / PARTIAL_FILLED 时，归为 UNKNOWN（例如 `ENTRY_FILL_EVIDENCE_MISSING`），不能继续沿用旧执行标签。
- CLOSED candidate 经 selection 正式产生意图后，才增加 TRADE_INTENT；未入选候选不归入“交易未执行”。
- 分析模式下被控制层抑制的交易建议不称正式意图。在明细/导出保留 `trade_disposition`，例如 ANALYSIS_ONLY、SUPPRESSED_BY_CONTROL。
- 多条意图以 `intent_id` 去重；native trade intent 与 durable execution 携带同一 intent 时不能计两次。

### 3.3 Case 聚合与结果标签

按该 Case 所有意图的现状聚合：任意 EXECUTED → Case EXECUTED；否则任意 UNKNOWN → UNKNOWN；否则任意 PENDING → PENDING；否则所有意图 NOT_EXECUTED → NOT_EXECUTED；无意图 → NOT_APPLICABLE。

- `intent_count > 0`：包含 TRADE_INTENT。
- Case EXECUTED：包含 TRADE_EXECUTION，移除 TRADE_NOT_EXECUTED。
- Case NOT_EXECUTED：包含 TRADE_NOT_EXECUTED，移除 TRADE_EXECUTION。
- 其余：移除两个执行结果标签。
- 一条成交、一条失败的 Case 在图中只进入“交易执行”；详情/导出继续保留每条意图结果。
- 重算只替换交易相关结果，保留 ARCHIVE、EVENT_DISCOVERY、BADCASE、FAILURE。

不能令“意图数 = 执行数 + 未执行数”：进行中和未知的 Case 尚未分流。每个节点/每条边计唯一 Case，不计订单数、成交回报数或重试次数。

### 3.4 周期、KPI 与处理完成

- 图、最近记录及两个新增结果筛选都沿用 Case 的 `semantic_day`。次日成交可更新原 Case 的执行结果，不把 Case 迁移到成交日。
- Runtime KPI 的“交易执行 / executed_cases”保留实际 ENTRY 成交的 Case 口径；不替换为意图数。本轮不增加意图 KPI。
- Overview `trade_triggered` 等原有指标保持各自已有口径；本次意图节点包含重复/过期意图，因此不能直接取该 KPI 当意图节点计数。
- Case 状态及 completed_at / duration_seconds 仍表示研判处理，不把等待券商成交算入研判耗时。
- `result_settled` 由 Case 研判终结且全部意图已有确定执行终态共同决定。修复现有仅因 `entry_result=null` 就永久不 settled 的重复/过期/OUTPUT_RECORDED 记录。PENDING / UNKNOWN 均不 settled。
- 新节点的“最近处理”只用可证明的意图释放/首次成交/终结时间；无持久终结时间就显示未记录，不用投影时间或 Case 完成时间冒充。平均执行延时本次不扩展，显示未记录。

## 4. 后端实施路径

### 4.1 单一投影归纳函数

新增 `src/doxagent/v2_read/trade_outcomes.py`：

- `classify_execution(summary, evidence)`：纯函数，依据 §3 分类。
- `rollup_case_trade(case, execution_summaries)`：纯函数，生成 trade/results/result_settled。
- `project_case_trade(store, ticker, case_id, incoming)`：读取已投影的同 Case 执行摘要，并用本批 incoming 覆盖同 ID，调用上述函数，返回 case 记录及 executed_cases 指标贡献。

挂载位置：`projectors.py` 完成 native Case、trade_intents、执行投影后、计算 `changed_cases` 和 graph 之前。按 `(ticker, case_id)` 合并本批最后版本，避免同一事务多条 case 记录相互覆盖。`ExecutionProjector` 原有直接增删 Case.results 的逻辑迁到公共函数；pending_intent 与已接收执行摘要都生成统一单意图状态。

触发覆盖 runtime_v2_cases、trade_intents、te_executions、te_jobs、te_attempts、te_fills 及成交更正。analysis/candidate 的 disposition 更新也经过公共 Case 归纳，不能覆盖已有意图/成交事实。

正常查询只读 V2 Read，不跨进程直连 Executor、不探测 Gateway、不触发交易或重放。

### 4.2 图与结果筛选

1. `v2_read/graph.py` 扩展 NODES，显式定义 JUDGMENT_RESULTS、EXECUTION_RESULTS，移除依赖 `NODES[4:]` 表示同一业务阶段的隐含假设。
2. 研判结果的来路保持当前真实 W1/W2/W3 规则；TRADE_INTENT 从真实决策节点连出。
3. TRADE_EXECUTION / TRADE_NOT_EXECUTED **只能从 TRADE_INTENT 连出**，不再由 W2/W3 直接连出。零计数不制造可见假边。
4. 延用 prior ∪ current 的重算方式，对撤回的 graph_member、graph_edges、graph_results 贡献写零/删除值，使更正后旧边真正消失。
5. `graph_seed.contributions` 的 graph_paths 同步重算，节点聚焦可追溯完整上游；TRADE_INTENT 作为中间节点，也能显示它真实的下游分流。
6. `api_v2/graph.py` 的 node 校验、NodeCounts.result_counts、baseline、delta 都支持新枚举。W3 统计可包含意图与执行结果，不能把它们当互斥分区求和。
7. `api_v2/runtime.py` 结果白名单增加 TRADE_INTENT、TRADE_NOT_EXECUTED；TRADE_EXECUTION 原参数仍表示实际成交。Repository 的 results membership 过滤直接复用。
8. Case revision 必须随执行事实更新。验证 SSE 会 upsert 更新后的 Case；不能只有图节点变化，而记录标签仍停留旧值。

### 4.3 固定快照的明细读取

导出所有可变记录必须落在勾选模式开始时的同一 `view_id` / read sequence。

以下接口增加可选 `view_id`，有值时校验 owner、ticker、RUNTIME 页面，并将同一 seq 传到存在性检查和分页：

| 接口 | 改动 |
|---|---|
| `GET /tickers/{ticker}/runtime/cases/{case_id}/candidates` | 补 view_id；Case 和 candidates 同 seq |
| `GET /tickers/{ticker}/runtime/cases/{case_id}/executions` | 补 view_id；Case 与执行列表同 seq |
| `GET /tickers/{ticker}/executions/{execution_id}` | 有 view 时不用当前 highwater；execution/orders/fills 同 seq |
| `GET /tickers/{ticker}/executions/{execution_id}/orders`、`/fills` | 补 view_id，分页后续仍绑定原 seq / view |

无 view_id 的既有调用保持兼容。本次运行状态明细中的 Candidates / Executions 组件也传入 view，避免屏幕详情本身混合时点。现有 attempts/messages 已支持 view_id，直接复用。

在 `executions.py` 抽一个小的 view 解析辅助函数，避免各路由漏校验。确认游标的 scope 与 view_id、parent、ticker 一致；若请求带 cursor，不能覆盖它绑定的读取水位。

ContentRef 指向不可变内容，正文分块无需改成可变 view 查询；引用仍来自该 view 的 CaseDetail，不能回退到当前 Event Library/PolicySet。

### 4.4 契约和部署兼容

更新 `dev_plan/workflow_v2/api_contract/doxagent-v2-api.types.ts`，包含新增结果、Case.trade、ExecutionSummary 状态字段；同步 API Contract 说明、相关 examples、OpenAPI 与前后端 wire schema。`route_contract.json` 的路由参数来自 API Contract Markdown 表格，先改源表格，再运行 `node scripts/generate_v2_schema.cjs`；前端运行 `pnpm --dir frontend/v2 schema`。不手工只改生成 JSON。

前端发布最终要求 trade 字段齐全。后端先兼容读取旧投影，并对缺少 trade 的 Case 调用同一归纳函数生成响应；投影回填后去掉临时查询回退，避免每次查询都扇出。发布窗口内旧前端可能不识别新枚举，因此 API / web 同一发布批次切换，现存页面重新建立 read context。

## 5. 前端呈现

### 5.1 五列链路图

```text
接收          一轮并行判定        二轮研判       研判结果          交易执行
               W1                              归档
接收消息       W2                 W3           事件发现
                                               Badcase
                                               交易意图产生 ── 交易执行
                                                           └─ 交易未执行
                                               失败
```

此图只示意列关系；渲染时仍按真实边，W1/W2 快速路径不强行经过 W3。

- 将节点位置/阶段配置从散落常量集中到 `runtime-graph.ts`（或现文件同一区域），节点宽度和字体保持可读。
- 去掉 `bx === 702` 等第四列硬编码；旁路判断使用节点阶段。跨 W3 的快速路径走上下通道，新的意图→执行边短接第五列。
- 图容器在普通桌面宽度展示五列；右侧节点详情打开后不足宽度时，图区域自身允许横向滚动，不把整个页面撑宽，不把 14px 文本压成细字。
- 第五列两个节点与意图节点形成可见分叉；选中时沿用完整路径高亮、空白恢复与键盘操作。

### 5.2 最近记录与详情

表格列序：`[勾选] 消息/来源 | 研判结果 | 交易执行 | 状态 | 接收时间 | 完成时间 | 耗时 | 详情`。

- 研判结果显示 ARCHIVE、EVENT_DISCOVERY、BADCASE、TRADE_INTENT、FAILURE。
- 交易执行列用 `Case.trade.state`：交易执行 / 交易未执行 / 等待执行 / 执行结果未知 / `—`。
- 原有 `CaseResults` 拆为研判结果和执行状态两个小组件；节点详情紧凑列表也显示两层，不重复同一标签。
- 结果筛选项包含交易意图产生、交易执行、交易未执行，各自按后端结果集合筛选。例如实际成交 Case 同时会被“意图产生”和“交易执行”查到，这是预期行为。
- `runtime-case.tsx` 执行卡从统一状态显示中文结果，并保留原始原因码/可读原因。待执行不显示成已经终结的“未成交”。

## 6. 导出交互与边界

### 6.1 状态流程

`浏览 → 选择 → 导出中 → 成功回到浏览`，失败回到选择并保留勾选。

1. 默认在结果、来源筛选左侧显示“导出”。没有记录时禁用。
2. 点击后变成“确认导出（N）”；显示取消、已选择数量与“全选已加载 N 条”，记录最左侧出现复选框。初始不自动选择全部。
3. 表头复选框支持全选/取消全选及半选状态；按 case_id 去重。勾选控件 stopPropagation，不打开详情；其他行区域维持打开明细的交互。
4. 加载更多使用冻结 view 的原 cursor。新增记录初始不勾选；用户再次点全选可纳入它们。文案数量随已加载记录更新。
5. 未选任何记录时“确认导出”禁用。导出中显示 `正在导出 3/12`，禁用重复提交，保留取消。
6. 导出完成后触发一次浏览器下载，退出选择模式并恢复列表刷新；失败保留选择和错误反馈，不下载一个伪装完整的文件。
7. 取消、切换 ticker/周期/结果/来源、离开页面、退出登录时中止导出并清空选择；新范围不继承旧 case_id。手动刷新同样退出选择模式。

### 6.2 与自动刷新的协调

`RuntimeBody` 现在分钟刷新同时更新 metrics/cases/node，并替换 readView，直接加 Set 会造成勾选中的行跳动。

实现 `exportSession = { viewId, period, result, source, selectedIds }`：

- 点击“导出”记录当前列表 readView。Cases 查询与选中详情在选择期间固定该 view。
- 分钟任务继续刷新 KPI 和打开的节点详情；跳过最近记录的刷新/缓存替换，图 SSE 继续。不要为了导出暂停整个页面。
- `usePages` 在选择模式使用 exportSession.viewId；分页也用它。UI row 的 selectCase 必须携带该列表自己的 view，不误用新的 minute view。
- scope 改变立即销毁 session。不会把已收集的旧 snapshot 数据拼进新 snapshot。
- view 过期时停止并提示重新进入选择模式。不要默默换新 view 继续导出。

## 7. JSON 内容与收集方式

### 7.1 文件结构

文件名：`doxagent-MU-runtime-cases-20260929T120000Z.json`，UTF-8，`application/json`，日期取导出时刻；ticker 做文件名字符清理。对象键使用稳定英文，业务正文保持原文。

```ts
interface RuntimeCasesExport {
  format: "doxagent.runtime-cases.v1";
  exported_at: string;
  ticker: string;
  selection: {
    scope: "LOADED_SELECTED";
    period: string;
    trading_days: string[] | null; // ALL 时 null
    result: string | null;
    source_id: string | null;
    case_count: number;
  };
  cases: RuntimeCaseExport[];
}
```

每条 RuntimeCaseExport 采用明确字段白名单：

| 分组 | 内容 |
|---|---|
| summary | case_id、标题、source、semantic_day、运行模式、状态、接收/完成时间、耗时、初始/最终路由、最终 novelty/policy_hit、研判结果、trade、trade_disposition |
| w1 | novelty、confidence、rounds、完整 attempts 的轮次/次数/状态/耗时/业务错误、完整 reasoning 文本、全部 Event/Fact 引用及未解析引用 ID |
| w2 | skipped、reasoning_stage、policy_hit、confidence、rounds、完整 attempts、完整 reasoning、R1 召回候选和最终命中 Policy、命中条件、未解析 ID |
| w3 | status、mode、novelty、policy_hit、专家交易判定/方向、prior_expectation、expectation_delta、全部 references/policies、完整 attempts、三类完整 reasoning |
| messages | 全部关联消息的标题、source、原始 URL、发布时间/抓取时间等已有业务时间、完整 body 原文与 content_type；不能只取第一条 |
| candidates | 全部事件发现的 proposition、assertion_state、subject_time、occurrence_date、entities、originating_node、provisional_event_id、created_at |
| executions | 全部意图/执行的方向、PAPER/LIVE、执行状态、原因、triggered/accepted/first_fill 时间、ENTRY 成交量与金额；附所有 order 和有效 fill（含 ENTRY/EXIT、价格、数量、手续费） |
| failures | 明细已有的全部业务失败 stage、status、code、message、occurred_at，去除 request_id / trace 等审计包装 |
| references | runtime_activation_id、library_snapshot_id/version、policy_set_version 等最小业务版本锚点，用于理解“当时引用什么”；不导出整个 D1/D2/PolicySet |

正文统一为 `{ state, reason, content_type, text }`；AVAILABLE 时 text 为全文。真实未产出/未记录用 null 和 reason，不能改为空字符串假装有正文。引用未解析保留原 ID 与状态，不拿最新版本补齐。

保留 case_id、intent_id、execution_id、event_id、fact_id、policy_id 与必要业务版本：它们是串联业务对象所需的身份。移除 request_id、view_id、cursor、read_seq、graph_revision、trace/span、lease、fencing、worker 路径、账户明文、API 密钥、原始提示词、冻结巨型输入与原始 broker events。金额/数量保持 DecimalString，不转浮点丢精度。

这份导出是业务复核材料，不是原始审计账本，也不声称能独立重放模型。必须至少包含当前研判明细所有可见/可展开内容，不以“去审计字段”为由删除 reasoning、尝试状态、错误或引用依据。

### 7.2 收集器实现

新增 `frontend/v2/src/core/runtime-case-export.ts`：

```text
exportSelectedCases(session, selectedIds, signal)
  按冻结列表顺序，以 case_id 去重
  全局最多 3 个 API 请求并发（涵盖所有分页/正文，不是每 Case 各自 3 个）
  每个 Case：
    GET CaseDetail(view_id)
    各 W1/W2/W3 attempts：消费内嵌首批，然后续取到 has_more=false
    messages：同样完整续页
    candidates、executions：固定 view_id 完整续页
    execution detail/orders/fills：完整续页；始终同 view_id
    对全部 reasoning/body ContentRef 读取到 complete=true
    以白名单映射为 RuntimeCaseExport
  所有 Case 成功后，生成一个 Blob，触发下载并 revokeObjectURL
```

- 复用 ApiClient.request 的认证、401 refresh、scope reset 和 AbortSignal；不得裸 fetch 绕过会话管理。
- 分页遵循 `next_cursor`，检查 has_more 与游标推进；合并按业务 ID 去重。不再次把内嵌首批请求成“第一页”导致重复。
- 正文复用 `appendContent` 校验 content_id/hash/type/index 连续性，按 content_id 在本次导出内缓存，避免反复读取重复正文；不能复用仅完成一半的阅读器缓存。
- 不用页面 DOM 文本拼导出，不依赖用户之前展开过哪些模块。
- CaseDetail 的失败列表目前 SQL LIMIT 20，而界面也只展示这批；为满足“至少界面全部”，完整保留现有 failures，并从已取齐 attempts 合并其余失败，按 identity 去重后去掉导出无关 ID。最终 Case failure 单独保留。
- 网络失败、快照失效、正文损坏、权限失败属于收集失败；整份文件不下载。保留已完成 Case 的内存结果，原 view 仍有效时可重试失败 Case；取消/切 scope 清理缓存。
- 后端明确返回 NOT_PRODUCED、PARTIAL 或引用未解析是业务数据状态，可以导出并保留说明；不得把网络失败吞成这类状态。
- 不人为限定只能 20 条，不新增任意“小批量上限”。总量由用户已加载并选中的记录决定；必要资源控制采用现有分页、全局并发 3 和取消机制。

## 8. 具体修改清单与实施顺序

| 步骤 | 文件/模块 | 交付 |
|---|---|---|
| 1 | API types、PRD Part 2、API Contract | 固定 §3 状态、结果枚举、新增字段及 view 参数 |
| 2 | 新增 `v2_read/trade_outcomes.py`；修改 `projectors.py`、`executions.py` | 统一意图/执行归纳；保持原始业务账本不变 |
| 3 | `v2_read/graph.py`、`graph_seed.py`、`api_v2/graph.py`、`runtime.py` | 五列节点数据、正确边、结果筛选、节点详情、SSE |
| 4 | `api_v2/runtime.py`、`executions.py`；route_contract / schema | candidates/executions/orders/fills 固定快照 |
| 5 | `frontend/v2/src/pages/runtime.tsx`、`runtime-case.tsx`、`refinement.css` | 五列图、表格列、中文执行状态、三类筛选 |
| 6 | `core/runtime-case-export.ts`、导出类型、Runtime selection 状态 | 勾选、全选、取消、固定表格快照、完整 JSON 下载 |
| 7 | Read 派生数据重投影维护入口、定向测试 | 旧 Case 回填新状态/图，更新 changelog |
| 8 | 构建与前后端联调 | 按 §10 验收；真实部署须作为实施交付的一部分单独记录 |

不要借机拆全站状态管理、替换图形库、重写 Executor、引入通用导出平台。现有 SVG、表格、复选框和下载 Blob 足够。

## 9. 历史数据更新与上线边界

仅添加代码不会让已完成的历史 Case 自动补出意图节点，需要一次 **派生读模型回填**。

在 `v2_read/maintenance.py` 与 CLI 增加范围明确的 `reproject-trade-outcomes` 操作：

1. 按 ticker/case_id 稳定键分页，使用当前已收录的 native/ExecutionSummary 证据重算；缺少正式意图且执行摘要也无法证明时记 UNKNOWN/缺失，不制造 READY。
2. 单批走 ReadStore 现有提交路径，更新 Case、execution state、graph_member/graph_case、graph_*、graph_paths 和 executed_cases；不直接裸写 metric_buckets。
3. 使用新投影版本/维护水位标记范围与进度，重跑幂等；每批与 projector 写入串行并基于最新已提交证据，避免覆盖刚到的成交。先离线副本验证再运行生产维护。
4. 不清空整个 V2 Read，不重跑模型，不 replay trade delivery，不修改 `trade_intents` 或 `te_*` 原始账本。
5. 完成后比较三类结果筛选集合、图节点 case_count 和 per-Case 分类；对旧的 W2/W3→TRADE_EXECUTION 贡献确认已撤回，不能只增加新边。
6. API/web 同批发布，重建 read context 后验收；回滚需要恢复前后端同版本及派生读模型备份/重投影，不能只退前端留下不认识的新枚举。

现有 `rebuild` 能重建全量读库，但对这两项需求范围过大，不作为默认上线步骤。具体部署命令须以实施当日服务与 Compose 配置为准，本方案不把旧机器状态当现状。

## 10. 必要测试与验收

### 10.1 后端定向测试

扩展 `tests/v2_backend/test_execution_projection.py`、`test_node_paths.py`、`test_streaming.py`、`test_api.py`，新增 `test_trade_outcomes.py`：

- 快速路径 NEW+Policy：W2→TRADE_INTENT；进入 W3 的路径：W3→TRADE_INTENT。
- READY 无成交、EXECUTION_ACCEPTED 未完成均只停在意图；UNKNOWN 不进入未执行。
- FAILED / DIRECTION_DISABLED 零成交、过期/重复/OUTPUT_RECORDED 正确归入未执行；analysis-only 与未释放 candidate 不冒充意图。
- 部分成交后失败仍执行；多意图一成一败 Case 只计一次执行；EXIT-only 不算 ENTRY。
- 同一成交 correction 撤销为零后撤回执行节点/边，待明确证据时进入 UNKNOWN；恢复有效成交时重新进入执行。
- 各结果 SQL 筛选与图成员一致；周期按 Case semantic_day；分钟/SSE 更新不重复计数。
- 更新 native Case / analysis / candidate 后，已有意图或成交不丢失。
- snapshot A 后新增成交或候选，携 A view 导出仍读旧状态；不带 view 的原调用仍可读最新；错误 ticker/view/cursor 被拒绝。
- 维护回填重复执行不增计，旧直达成交边被撤回，路径聚焦和历史筛选集合正确。

历史审计提到 `test_execution_projection.py` 曾有“仅看最后一条 delta”的基线失败。实施时先重现当前版本；如仍存在，按完整 revision 顺序累计 delta 后断言，不放宽业务正确性要求。

### 10.2 前端与导出定向测试

新增 `frontend/v2/tests/runtime-case-export.test.ts` 和 Runtime 交互测试：

- 单选、全选、半选、加载更多不自动选新行；复选框不触发详情；取消/切 scope 清空。
- 选择期间分钟刷新不替换表格与 view，KPI/节点继续刷新；导出中双击不启动第二任务。
- 超过 20 条消息/attempts/candidates/orders/fills 和多块中英文正文均完整，无重复、无截断；全局 API 并发不超过 3。
- W1/W2/W3 reasoning、候选/命中 Policy、Event/Fact 引用、失败、消息全文、执行原因均进入 JSON；金额字符串保真。
- 任一内容获取失败时不下载；取消/退出登录中止；未解析引用保留 ID/状态；JSON 不含 auth/账户明文/原始审计输入。
- 下载文件可 JSON.parse，case_count 与实际 cases.length 相同，顺序与冻结列表一致。

执行必要类型检查、schema/wire 校验、定向 pytest/vitest 及生产构建。文案/样式不额外写镜像实现的单元测试。

实现后在仓库根目录执行以下最小检查集（新增测试文件须先落地）：

```powershell
node scripts/generate_v2_schema.cjs
uv run pytest tests/v2_backend/test_trade_outcomes.py tests/v2_backend/test_execution_projection.py tests/v2_backend/test_node_paths.py tests/v2_backend/test_streaming.py tests/v2_backend/test_api.py tests/v2_backend/test_wire.py tests/v2_backend/test_openapi.py
pnpm --dir frontend/v2 exec vitest run tests/runtime-case.test.ts tests/runtime-case-export.test.ts
pnpm --dir frontend/v2 build
```

新增接口快照测试如单列新文件，应加入上述 pytest 集合。页面交互测试按现有浏览器测试配置定向执行，不为此次改动重跑交易下单验收。

### 10.3 浏览器验收

在 1262px、1559px 桌面宽度检查：五列图不缩字或遮挡，旁路不穿节点，选中意图时下游可见，节点详情与图并排时页面不横向溢出；表格选择列与业务列对齐。

用至少一条真实成交与一条真实零成交终态 Case 验证三个筛选；下载选中 JSON 并离线比对屏幕明细全文。进行中/未知/更正场景若生产没有样本，用定向 fixture 验证并在交付记录中标明，不能伪称生产覆盖。

## 11. 本轮交付与未执行事项

本文件已明确状态口径、API/投影改动、图与表格布局、导出交互、完整内容范围、快照一致性、历史回填和验收顺序，可直接作为实现清单。

本轮未开始功能实现、未运行功能测试、未同步远端或重启服务。当前工作区已有 changelog 与 SSH 文档修改，后续提交应保持范围隔离。本轮只新增方案文件与对应文档交付记录。
