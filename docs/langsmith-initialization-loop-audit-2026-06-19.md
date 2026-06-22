# DoxAgent 文档 1/2 初始化 LangSmith Agent Loop 审计报告

审计日期：2026-06-19  
审计对象：用户指定的 10 个 DoxAgent `run_id`，LangSmith project 为 `DoxAgent`。  
审计目标：

1. 统计当前 workflow 中 tool calling 失败集中于哪些 tools，以及失败原因。
2. 梳理各 agent 在各 workflow node 执行任务时最容易遭受的阻塞、困难和失败问题。

## 0. 覆盖结论

本次覆盖了指定 10 个 run 的全部 LangSmith loop 节点，共 590 个 `Agent.WorkflowNode.LOOPn` LLM run。所有指定 run 均未触达 LangSmith 单次查询 100 条上限，因此没有因分页上限造成遗漏。

| 序号 | DoxAgent run_id | loop 数 | 成功 | error | pending | 主要节点分布 |
|---:|---|---:|---:|---:|---:|---|
| 1 | `run_a24eb2cf5c374f159a4b2eeda3b32d0c` | 67 | 64 | 3 | 0 | BuildGlobalResearch 24, ReviewExpectationFields 14, GenerateExpectationDetails 9 |
| 2 | `run_fd2c6aa654c2402fb651e4943d9ae402` | 45 | 45 | 0 | 0 | BuildGlobalResearch 17, ReviewExpectationFields 11 |
| 3 | `run_6bf7637a7b124c7db54845859b4e3706` | 66 | 62 | 0 | 4 | BuildGlobalResearch 25, ReviewExpectationFields 14, ResolveObjectionsAndDelegations 10 |
| 4 | `run_1cff599c3701497c96db3a9bcd34d9c3` | 52 | 52 | 0 | 0 | BuildGlobalResearch 19, ReviewExpectationFields 16 |
| 5 | `run_7632173f73eb4f7e9f6b2cc21fc75112` | 58 | 56 | 2 | 0 | BuildGlobalResearch 20, ReviewExpectationFields 17, GenerateExpectationDetails 11 |
| 6 | `run_a3f1618b088c4693875b87b735b4ea6f` | 55 | 55 | 0 | 0 | BuildGlobalResearch 25, ReviewExpectationFields 8 |
| 7 | `run_dfd3a7c3eb8f481680be892871c509f6` | 56 | 55 | 1 | 0 | BuildGlobalResearch 23, ReviewExpectationFields 14, GenerateExpectationDetails 12 |
| 8 | `run_c78d65c16d6548219d131b3560579356` | 70 | 67 | 2 | 1 | BuildGlobalResearch 24, ReviewExpectationFields 17, GenerateExpectationDetails 17 |
| 9 | `run_c9a8d26664b64cd08a5e55f09fe9cb24` | 57 | 53 | 3 | 1 | BuildGlobalResearch 23, ReviewExpectationFields 16 |
| 10 | `run_f026e119865847628172c560be5e2102` | 64 | 62 | 2 | 0 | BuildGlobalResearch 24, ReviewExpectationFields 22 |

总体 loop 状态：571 成功，13 error，6 pending。  
真实工具调用口径：从每个 loop input 的 `tool_and_delegation_history` 去重后，得到 594 次 tool action / tool result，其中 477 成功、112 失败、5 partial。失败率约 18.9%，partial 约 0.8%。

注意：LangSmith 里这些 agent loop 均记录为 `llm` run，工具执行没有作为独立 `tool` run 展开。因此本报告的工具失败主口径不是 LangSmith `run_type=tool`，而是 ReAct harness 写入 loop input 的 `tool_result` 历史。

## 1. Tool calling 失败集中点

### 1.1 高频失败工具总览

| Tool | 去重调用数 | 失败数 | 失败 loop 数 | 主要 agent/node | 主要失败原因 |
|---|---:|---:|---:|---|---|
| `twelvedata.daily_ohlcv` | 110 | 37 | 13 | O4/BuildGlobalResearch, O4/ReviewExpectationFields, 少量 C2 | 单 agent 单 tool 最多 3 次限制；部分 SSL EOF |
| `tavily.search` | 118 | 28 | 16 | C3/BuildGlobalResearch, C3/ReviewExpectationFields, C1/ReviewExpectationFields | 单 tool 3 次限制；少量 SSL EOF |
| `fred.series_observations` | 28 | 16 | 11 | C2/BuildGlobalResearch | FRED series_id 无效导致 HTTP 400；调用上限；少量 SSL EOF |
| `bls.timeseries` | 16 | 11 | 10 | C2/BuildGlobalResearch | BLS 上游 SSL EOF/ConnectError |
| `alpha.financial_statements` | 57 | 6 | 2 | C1/BuildGlobalResearch | 多 ticker、多 statement 并发拉取触发 3 次调用上限 |
| `doxa_get_event_source` | 3 | 3 | 2 | A1/ReviewExpectationFields | `event_...` ID 传给 UUID 字段，Postgres 22P02 |
| `doxa_get_narrative_report` | 52 | 2 | 2 | O1/GenerateExpectationDetails, O1/GenerateGlobalNarrativeReport | DoxAtlas/HTTP 上游 SSL EOF |
| `doxa_get_ignored_propositions` | 2 | 2 | 2 | A1/ReviewExpectationFields | narrative_id 传 `MU` 或不存在的 UUID |
| `yfinance.daily_ohlcv` | 53 | 2 | 2 | O4/BuildGlobalResearch, C2/BuildGlobalResearch | `database is locked`；调用上限 |
| `finnhub.trade_stream` | 2 | 2 | 1 | O4/ReviewExpectationFields | ToolError schema bug：空 message 触发 Pydantic 校验失败 |
| `bea.nipa_data` | 10 | 1 | 1 | C2/BuildGlobalResearch | RemoteProtocolError/server disconnected |
| `polymarket.market_probability` | 17 | 1 | 1 | C2/BuildGlobalResearch | SSL EOF；另有语义失败：查询衰退概率返回无关市场 |
| `doxa_query_propositions` | 1 | 1 | 1 | A1/ReviewExpectationFields | narrative_id not found |

另有 5 次 partial：

| Tool | partial 数 | 说明 |
|---|---:|---|
| `sec.filing_sections` | 4 | SEC EDGAR 暂时不可用，未解析 filing sections |
| `sec.company_facts_and_filings` | 1 | SEC EDGAR 暂时不可用，未检索到 submissions/companyfacts |

这些 partial 没有被记录为 failed，但对 C1 的官方披露核验、估值/风险段落和字段审查有实际阻塞。

### 1.2 失败原因分组

#### A. Harness 调用上限是最大失败来源

`max_tool_calls_per_name=3` 是最明显的系统性问题。它不是外部供应商失败，而是 ReAct harness 对单个工具名的硬上限。

典型触发：

- O4 在 `BuildGlobalResearch` 中一次性拉 `MU/SOXX/QQQ/WDC/STX/SNDK` 的 OHLCV，前 3 个成功，后续 WDC/STX/SNDK 被 `tool_call_limit_exceeded` 拦截。
- C3 在行业研究和字段复核中需要多个 Tavily 查询，例如 DRAM/NAND 合约价、HBM 份额、Samsung HBM4 认证、capex discipline，超过 3 次后关键查询失败。
- C1 拉 `alpha.financial_statements` 时同时覆盖 MU/WDC/STX 和 income/balance/cashflow，导致 WDC/STX 的报表请求被上限截断。
- C2 对 FRED 多组宏观 series 反复修正后，也会撞到 3 次上限。

影响：大量失败其实不是工具不可用，而是任务粒度和工具预算不匹配。对文档 1 的 Global Research 会留下 peer comps、capex、宏观序列缺口；对文档 2 的 expectation detail/review 会造成字段证据不足或只能标记 unknown。

#### B. 上游 SSL EOF/连接中断跨工具出现

多次出现 `[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol` 或 ConnectError，涉及：

- `bls.timeseries`
- `twelvedata.daily_ohlcv`
- `tavily.search`
- `doxa_get_narrative_report`
- `fred.series_observations`
- `polymarket.market_probability`
- 少量 `bea.nipa_data` 远端断连

影响：C2 宏观、O4 价格验证、O1 DoxAtlas 叙事证据拉取都会被打断。部分 run 能通过重试恢复，但在 `run_dfd3a7c3eb8f481680be892871c509f6` 中，O4 的 Twelve Data、yfinance、Finnhub 三路价格证据都不可用，直接导致 price reaction 字段被高严重度 objection 阻止进入 Belief State。

#### C. 工具参数/ID contract 不匹配

这类问题不是网络波动，而是调用协议不一致：

- `doxa_get_event_source` 期望 UUID，但 A1 传入 `event_23f...`、`event_ff...`、`event_391...` 这类 DoxAtlas 事件业务 ID，Postgres 返回 `invalid input syntax for type uuid`。
- `doxa_get_ignored_propositions` 曾传入 `narrative_id: "MU"`，同样触发 UUID 语法错误；另一次传入 UUID 但返回 `narrative_id not found`。
- `doxa_query_propositions` 也出现 `narrative_id not found`。
- `finnhub.trade_stream` 的失败被包装成 ToolError 时，message 为空，反而触发 Pydantic `String should have at least 1 character`，说明 tool error normalization 本身有 bug。

影响：A1 的 DoxAtlas provenance audit 无法落到命题级、事件源级、ignored proposition 级证据，只能输出 `pass_with_warnings` 或 `needs_revision`。这会让文档 2 的字段审查处在“知道证据追溯不够，但工具查不到底层”的尴尬状态。

#### D. 成功但不可用的输出没有被统一降级

部分工具返回 `succeeded` 或没有 failed 状态，但 agent output 显示它们对任务不可用：

- `yfinance.daily_ohlcv` 在 O4 字段复核中出现“API 返回成功但所有 OHLCV 数据点均为 null”。
- `polymarket.market_probability` 查询 `US recession 2026` 等宏观概率时，返回 GTA VI 等无关热门市场，属于 query miss / fallback 语义失败。
- `sec.filing_sections` 和 `sec.company_facts_and_filings` 以 partial 返回，描述为 SEC EDGAR 暂时不可用。
- 一些 OHLCV 成功结果经过 compaction 后，后续 agent 明确说“原始 OHLCV 数据点在上下文压缩后不可直接访问”，无法给出精确收益率、波动率、支撑/阻力位。

影响：这些情况如果不进入统一 ToolResult 质量分类，会让 workflow 误以为证据已取到，实际后续 review 仍会因为“无底层硬证据”提出 objection。

## 2. Agent/节点阻塞画像

### 2.1 `BuildGlobalResearch`

这是失败最多的节点，也是文档 1 质量的上游瓶颈。

#### O4 市场轨迹

高频问题：

- OHLCV 多标的请求与 3 次工具上限冲突。O4 需要 MU、SOXX、QQQ、WDC、STX、SNDK，但 `twelvedata.daily_ohlcv` 在同一任务中只能调用 3 次，peer 数据经常被截断。
- Twelve Data SSL EOF、yfinance `database is locked` 或备用数据质量不稳定，会让价格相对表现缺失。
- 原始 OHLCV 在 compaction 后不可直接访问，后续复核无法精确复算收益率/波动率/事件窗口。

结果：

- Global Research 的 market trace 段落可以写出来，但 peer relative performance、成交量异常和技术位常常降级为 unknown。
- 更严重的是，后续文档 2 的 price_reaction 如果引用叙事报告而不是 O4 硬市场数据，会在字段审查中被 O4 反对。

#### C3 行业/竞争格局

高频问题：

- `tavily.search` 超过 3 次上限，最常缺的是 capex discipline、wafer starts、三大厂具体资本开支/产能扩张证据。
- `finnhub.company_peers` 返回的是 NVDA/AVGO/AMD/INTC 等半导体 peers，但输入期望的 WDC/STX/SNDK 等存储/HDD/NAND 相关同业覆盖不足。
- C3 多次把 capex 或 peer comps 标记为 `[UNSOURCED]` 或 pending_data_needs。

结果：

- 文档 1 的行业报告在 DRAM/NAND 定价和 HBM 份额上通常有足够证据，但供给侧产能纪律、同业估值、WDC/STX/SNDK 纯存储对比经常不足。
- 这些缺口进入文档 2 后，会变成 C3 字段审查里的 capex/GDP/source gap objection。

#### C2 宏观

高频问题：

- BLS 基本是 SSL EOF 重灾区，CPI/就业序列经常失败。
- FRED 出现 HTTP 400，原因是 series_id 组合里包含无效或不支持的 ID，例如 `ISM`、`SOX`、部分宏观/市场代理 series。
- Polymarket 查询词不稳定，既有 SSL EOF，也有返回无关市场的语义失败。
- BEA 偶发 RemoteProtocolError/server disconnected。

结果：

- 宏观段落可以通过 FRED/BEA/FOMC 的部分成功结果完成，但 BLS 和 Polymarket 的缺口经常被记录为 unknown。
- 当 C2 的 GDP、衰退概率、就业/通胀假设被文档 2 引用时，复核阶段容易被要求补证或降级。

#### C1 基本面

高频问题：

- `alpha.financial_statements` 对多 ticker、多 statement 的调用超过 3 次上限。
- SEC EDGAR partial/unavailable 使 10-K/10-Q section 级披露不足。
- 同业估值、资产负债表、CapEx、毛利率口径经常需要更多证据。
- 个别 loop 出现模型侧失败：`InternalServerError`、`CancelledError` 或 pending。

结果：

- C1 能提供 MU 自身的收入、毛利率、EPS、估值等锚点，但同业财务/估值对比容易缺。
- 字段审查时 C1 最常提出数字口径不一致、字段为空、单一来源不足等 blocking objection。

### 2.2 `GenerateExpectationConstruction`

O1 在该节点总体能完成 expectation shell 生成，但有两个结构性风险：

- 对 DoxAtlas narrative report 依赖很重。叙事报告通常体量极大，常见 `original_chars` 达百万级，进入 context 后需要 preview/compaction。
- shell 阶段容易把 narrative-level 事实提升为 expectation 论点，但底层命题、媒体、社交、事件源证据没有同步带入。

结果：shell 可生成，但后续 A1/C1/C3/O4 会追问“具体 source id、event source、OHLCV、SEC/财报锚点在哪里”。如果 detail 阶段没有补齐，文档 2 promotion 会卡住。

### 2.3 `ReviewExpectationConstruction`

A1 构造审查的主要问题是输出协议和证据追溯：

- 有 loop 只输出工具调用 wrapper 或非标准 JSON，触发 `react_no_progress` warning。
- 审查证据多依赖 `doxa_get_narrative_report` 顶层摘要，能判断 shell 方向是否合理，但不总能验证到底层事件/命题。
- 在工具权限有限时，A1 会明确说无法检查 ignored propositions 或 event-source 质量。

结果：该节点通常不会最大规模阻塞，但它把“底层 DoxAtlas provenance 不足”的问题后移到了 `ReviewExpectationFields`。

### 2.4 `GenerateExpectationDetails`

O1 是文档 2 生成的中心，也是最容易产生 promotion blocker 的节点。

高频问题：

- `doxa_get_narrative_report` 偶发 SSL EOF，虽然后续可重试恢复，但会放大循环次数。
- 多个 O1 detail loop 出现 `CancelledError`，说明请求体、输出体或 resolver/detail 任务仍有超时/取消压力。
- Detail patch 曾出现空 `realized_facts`、空 `key_variables`、通用 placeholder 式 `event_monitoring_direction`。
- 价格反应、估值、毛利率、CapEx 等精确数字有时只挂 DoxAtlas narrative evidence，而不是 SEC/财报/OHLCV/原始来源。
- evidence refs/URL/source id 粒度不稳定，常见 narrative report 单一顶层证据支撑大量细字段。

结果：

- 文档 2 常能生成，但字段完整性和来源粒度不足导致后续 review 反复提出 blocking objection。
- 对 O4 来说，price_reaction 如果没有直接 OHLCV/trade-stream 支持，就不能进入稳定状态。
- 对 C1/C3 来说，数字口径、日期、事件方向和 key variable 当前状态不严谨，就会进入返工。

### 2.5 `ReviewExpectationFields`

这是文档 2 promotion 的主要 gate，发现的问题最多。

#### O4 字段复核

最核心 blocker 是 price_reaction 缺少底层市场数据，或与底层 OHLCV 相矛盾。

典型情况：

- `run_dfd3a7c3...` 中，Twelve Data 对 MU/SOXX SSL 失败，yfinance 返回全 null，Finnhub trade_stream 又触发 ToolError message 为空的校验 bug。O4 最终提出高严重度 objection，阻止涉及 MU 1027.42、跑赢 QQQ/SOXX、COMPUTEX/HBM4 事件价格反应的字段进入 Belief State。
- `run_f026e119...` 中，O4 获取到 OHLCV 后反而发现叙事 price_reaction 与硬数据冲突：Q1 DRAM 合约价上涨窗口内 MU 实际回撤约 25%；FQ1 财报窗口像 sell-the-news；万亿市值突破主要发生在 4-6 月，而不是某些 patch 声称的催化窗口。

结论：O4 不是只被工具失败阻塞；更重要的是它会把 narrative-only price reaction 拦下来。文档 2 里任何具体价格点、相对跑赢、事件窗口涨跌，都需要直接 market data evidence。

#### C1 字段复核

高频 blocker：

- `realized_facts`、`key_variables` 为空。
- `event_monitoring_direction` 是通用占位或空结构。
- 毛利率、EPS、P/S、CapEx、DRAM/NAND 涨幅等数字口径不一致。
- 单一 DoxAtlas narrative source 支撑大量 fundamental claims，缺少 SEC/财报/Alpha Vantage/Tavily 多源锚定。

典型发现：

- 有 run 中 C1 明确指出 Patch 1/2 的 `realized_facts` 和 `key_variables` 为空，Patch 3 全部引用单一 DoxAtlas 来源，提出 critical/high objection。
- 有 run 中 C1 指出 DRAM/NAND 价格涨幅与 TrendForce 实际数据存在 30-40 个百分点差异，必须修正后才能 promote。
- 有 run 中 C1 指出 FY2025 毛利率口径混乱：全年口径、季度非 GAAP 口径、未来指引混在一起。

#### C3 字段复核

高频 blocker：

- 行业事实日期或状态不准，例如 HBM4/Nvidia Vera Rubin 认证时间。
- `key_variables.current_state` 仍停留在“Samsung 追赶失败/未认证”等过期状态，而外部证据显示 Samsung 已取得进展。
- positive_events/negative_events 方向写反，导致看涨/看跌验证逻辑倒置。
- CapEx、GDP、wafer starts、同业证据不足。
- `key_variables` 出现 placeholder/未补全。

典型发现：

- `run_dfd3a7c3...` 中 C3 发现一个 patch 的 `key_variables` 有占位符缺陷，要求退回 O1 补全。
- `run_c78d65c...` 中 C3 发现看空 patch 的 positive_events 和 negative_events 方向完全倒置，属于结构性错误。

#### A1 DoxAtlas 审计

高频 blocker：

- 无法从 top-level narrative report 追到底层 proposition/media/social/event source。
- `doxa_get_event_source` 与 `doxa_get_ignored_propositions` 的 ID contract 失败，使 A1 无法验证是否依赖了 ignored/弱证据/矛盾命题。
- 当工具权限不允许额外调用时，A1 只能留下 unknowns。

结果：A1 往往给 `pass_with_warnings` 或 `needs_revision`，并要求加强 DoxAtlas evidence provenance，而不是直接批准所有字段。

### 2.6 `ResolveObjectionsAndDelegations`

O1 resolver 的问题集中在“高审查压力 + 大 patch + 多 objection”：

- 该节点有 pending 和 `CancelledError`，尤其在 objections 多、需要修订多个 patch 时更明显。
- `run_6bf7637a...` 出现 10 个 resolver loop，说明字段问题能被修，但成本高。
- resolver 有时能正确接受 blocking objection 并提交 revised patch，例如补全 empty fields、修正 DRAM/NAND 涨幅、修正 positive/negative events 方向。
- 但 pending/cancelled loop 说明 resolver 输入仍可能过大或批次仍不够小。

结论：resolver 是必要的返工吸收层，但不应该承担过多本可在 detail 生成阶段避免的字段质量问题。

### 2.7 后置节点：Narrative/KnownEvents/Monitoring

这些节点 loop 数较少，主要问题是继承上游质量：

- `GenerateGlobalNarrativeReport` 曾遇到 DoxAtlas narrative report SSL EOF 和非标准 tool_call 格式，但后续可重试恢复。
- `GenerateKnownEvents` 和 monitoring 节点没有形成主要工具失败集中点。
- 如果文档 2 没有稳定 promotion，后置节点自然缺少高质量 expectation context。

## 3. 非 tool 的 loop 执行层失败

在 590 个 loop 中，有 19 个非成功状态：

| 类型 | 数量 | 典型位置 | 影响 |
|---|---:|---|---|
| `CancelledError` | 11 | O1/GenerateExpectationDetails, O1/ResolveObjectionsAndDelegations, C1/A1 ReviewExpectationFields | 当前 loop 无 output，通常需要重试或依赖后续缓存/恢复 |
| pending 无 output | 6 | C1/BuildGlobalResearch, O1/ResolveObjectionsAndDelegations | LangSmith 记录未完成，审计上视为不可靠 loop |
| `APIConnectionError` | 1 | C2/BuildGlobalResearch compaction loop | compaction/request 失败，可能造成宏观证据摘要断裂 |
| 模型侧 500 | 1 | C1/BuildGlobalResearch | `NoneType` object has no attribute `stream`，属于 provider/SDK 层异常 |

这些不是 tool calling 失败，但它们直接造成 agent loop 空输出，是 workflow 卡顿和重复 dispatch 的重要来源。尤其 O1 detail/resolver 与 C1 review 的 CancelledError，和文档 2 返工压力高度相关。

## 4. 最重要的系统性结论

1. 当前最大的工具失败不是外部 API 本身，而是 `max_tool_calls_per_name=3` 与任务设计不匹配。O4/C3/C1/C2 都有合理任务需要超过 3 次同名工具调用。
2. 价格反应是文档 2 最脆弱字段。只要底层 OHLCV/trade-stream 缺失或与 narrative 冲突，O4 会阻止 promotion。
3. DoxAtlas provenance 工具 contract 不稳，A1 无法稳定追溯 event source、ignored propositions 和 proposition 级证据。
4. O1 detail 生成仍会产生空字段、placeholder、通用 monitoring、source 粒度过粗等问题，导致 review 阶段集中返工。
5. C1/C3 审查非常有效，但也说明生成阶段缺少前置数字/来源 sanity gate。很多 blocking objection 本可在 O1 detail patch 入 Working Memory 前拦截。
6. 成功但不可用的工具输出需要统一进入 ToolResult 质量状态，否则 workflow 会误把“调用成功”当成“证据可用”。

## 5. 建议优先级

### P0

- 为高 fan-out 工具提供批量接口或节点级预算覆盖：`daily_ohlcv` 应支持多 ticker batch，`alpha.financial_statements` 应支持多 statement/ticker batch，`tavily.search` 至少应有 query bundle 或 C3 专用更高上限。
- 把 yfinance 全 null、Polymarket query miss、SEC EDGAR unavailable 这类结果标记为 `partial` 或 `failed_unusable_data`，不要算作普通 success。
- 修复 DoxAtlas 工具 ID contract：`event_...` 业务 ID 到 UUID 的映射、ticker 到 narrative UUID 的解析、`narrative_id not found` 的可恢复提示。
- 修复 `finnhub.trade_stream` ToolError 包装，保证 message 非空，并把真实上游错误带出来。

### P1

- 在 O1 `GenerateExpectationDetails` 后、进入字段 review 前，增加 deterministic sanity gate：非空 facts/key_variables、非 placeholder monitoring、精确价格/财务数字必须有 source-class 匹配证据。
- 对 price_reaction 建立强约束：必须引用 O4 market evidence 或直接 OHLCV/trade-stream evidence；DoxAtlas narrative report 只能作为叙事背景，不能单独支撑价格反应。
- 为 FRED 建 series_id 白名单和分批重试策略，避免一个无效 series 使整组 HTTP 400。
- 为 BLS/TwelveData/Tavily/DoxAtlas 加统一 SSL EOF retry/backoff，并在失败后保留可见的 retry audit。

### P2

- 减少 resolver 负担：继续按 objection taxonomy/dedupe 分批，但更重要的是把空字段、方向倒置、source 粒度过粗等问题前移到 detail 生成后的本地 gate。
- 保存可复算的 market evidence summary，例如每个事件窗口的 start/end close、return、benchmark return、volume spike，而不是只把原始 OHLCV 压缩进 context。
- 为 A1 提供只读 provenance snapshot，避免每次审查都实时调用 DoxAtlas 底层工具。

## 6. 本次审计产物

本地中间产物位于 `.tmp/langsmith_audit/`：

- `coverage_counts.json`：10 个 run 的 loop 覆盖统计。
- `raw_runs_by_doxagent_run.json`：按 DoxAgent run_id 导出的原始 LangSmith run 数据。
- `loop_index.csv`：590 个 loop 的索引。
- `tool_history_analysis.json`：从 input `tool_and_delegation_history` 抽取的工具调用/失败统计。
- `refined_analysis.json`：从 output 抽取的 objection、pending_data_needs、unknowns、schema/source 问题辅助统计。

这些中间产物仅用于本次排查，不是代码路径的一部分。
