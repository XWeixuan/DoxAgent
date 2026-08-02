# Document 2 Schema v1 上游数据与 Tool Calling 支持审计

> 审计日期：2026-07-29
> 审计范围：新版 Expectation Shell / Unit Schema v1 的初始化数据需求、当前 DoxAgent tool registry、Document 1 → Document 2 workflow 路由、Monitoring Message Bus，以及只读真实工具探针。
> 明确排除：本轮不修改运行时代码，不启动 DoxAtlas 新任务，不发起任何真实 LLM request。

## 1. 结论摘要

当前系统只能支持新版 Document 2 的一部分上游研究，不能直接、完整、可审计地产出 Schema v1。

需要区分三个层面：

1. **数据源层**：SEC 财务事实、公司申报、日线 OHLCV、宏观数据支持较好；公司特定的卖方一致预期、市场隐含参数、供应链私有指标支持最弱。
2. **Tool 层**：DoxAtlas、SEC、Alpha Vantage、Tavily、Twelve Data 等工具能返回材料或结构化序列，但多数输出不是 `StateValue`、`ObjectRef`、`ExpectationUpdate` 所需的业务对象。
3. **Workflow 层**：当前 Document 2 仍生成旧版 `expectation_name / direction / market_view / realized_facts / key_variables / event_monitoring_direction`；O1 生成阶段主要只有 `doxa_get_narrative_report`，其他领域工具只在 Document 1 或候选生成后的 review 阶段可用。因此，“registry 中有工具”不等于“新版 Document 2 可以消费并持久化其结果”。

总体判断：

| 判断对象 | 当前结论 |
| --- | --- |
| 新版 Shell / Unit 定义 | Agent 研究能力原则上可形成，但当前输出 contract 不支持 |
| `ACTUAL` 财务 State | 数据源较充分，缺少到新 State ledger 的转换与持久化 |
| `MANAGEMENT` State | 可从申报/新闻中收集，定性与非标准指标仍需抽取和口径归一 |
| `SELL_SIDE` State | 仅标准盈利预测局部可得；自定义运营指标的一致预期严重不足 |
| `INDUSTRY_CHAIN` State | 能搜集公开报道，缺少稳定、结构化、可复现的行业数据源 |
| `MARKET_IMPLIED` State | OHLCV 可得，但“某具体参数的市场隐含值”基本不能直接获得 |
| Realization Factors | 可从公开世界材料研究形成，但当前没有统一 observability catalog |
| Potential Gaps | 可以推理提议；缺少可解析的 State/Factor 引用和 event-family runtime |
| Expectation Update / Gap Activation | 当前没有对应生产、状态迁移、delta 和 market absorption 管道 |

## 2. 设计材料中必须先修正的契约冲突

这些不是数据源不足，而是 Schema 和样例自身不闭合。若不先处理，新增数据工具也无法稳定落入对象。

### 2.1 `ObjectRef` 不允许引用 `REALIZATION_FACTOR`

最终 Schema 的 `ObjectRef.object_type` 仅允许：

```text
EVENT | METRIC | EVIDENCE | FILING | ANALYST_ESTIMATE | STATE_VALUE
```

但 MU、RKLB、FLNC 样例中的 `PotentialGap.basis_refs` 大量使用：

```yaml
object_type: REALIZATION_FACTOR
```

同时，最终 Schema 自带的 Potential Gap 示例把 `factor_mu_hbm4_customer_qualification` 伪装为 `object_type: EVENT`，语义同样错误。

**前置决策**：要么把 `REALIZATION_FACTOR` 加入 `ObjectRef`，要么明确 `PotentialGap` 另设 `factor_refs`；不能继续使用错误类型承载 factor id。

### 2.2 “模型派生值”在方向设计中存在，但 `source_role` 枚举中消失

重置方向允许“少量可审计的模型派生值”，但最终 `StateValue.source_role` 只有：

```text
ACTUAL | MANAGEMENT | SELL_SIDE | INDUSTRY_CHAIN | MARKET_IMPLIED
```

如果市场隐含时间、估值期限或转化率来自模型反推，它既不应伪装成 `MARKET_IMPLIED` 原始事实，也没有合法的 `MODEL_DERIVED` 角色。

**前置决策**：增加可审计的 `MODEL_DERIVED`，或把推导结果移到独立 projection/derivation object，并强制记录方法和输入引用。

### 2.3 样例中的空区间违反 State Value 核心规则

MU 样例为 HBM4 良率生成：

```yaml
value:
  lower: null
  upper: null
```

但核心验证规则明确要求：无可靠状态时不生成，不使用 UNKNOWN 占位。空 RANGE 不是有效 State Value。

### 2.4 细粒度 `event_family_ids` 没有治理目录

样例使用了几十种细粒度 family，例如：

```text
SUPPLIER_QUALIFICATION
YIELD_MILESTONE
CUSTOMER_ACCEPTANCE
INTERCONNECTION_APPROVAL
WARRANTY_CHARGE
PLATFORM_DELAY
```

当前仓库内：

- Monitoring Message Bus 的事件只是 `monitoring.message.created`，没有这些业务 family；
- CDECR 的 `EventFamily` 是较粗的 12 类，如 `PRODUCTION_SUPPLY`、`COMMERCIAL_OPERATION`、`PRODUCT_SCIENCE`；
- CDECR 是独立模块，当前未接入 Document 2 的 State/Factor/Gap 更新链路；
- 没有发现新版 Schema 所需的细粒度 family registry、别名、版本、父子映射或验证器。

**前置决策**：建立稳定的 event-family catalog，并明确“细粒度 trigger family ↔ CDECR 粗分类/谓词”的映射。否则 `observability` 和 `activation_rules` 只是自由文本。

### 2.5 `ObjectRef` 可枚举，但当前没有统一的可解析对象仓库

当前工具通常返回 `source_coordinates.source_kind/source_id`，但新版对象要求：

```text
EVENT | METRIC | EVIDENCE | FILING | ANALYST_ESTIMATE | STATE_VALUE
```

目前没有统一 resolver 证明任意 `{object_type, object_id}` 能够：

- 回查原始来源；
- 区分公司申报、新闻、卖方预测和派生 metric；
- 保留 point-in-time 快照；
- 在来源撤回或新值出现后更新 validity。

因此 `basis_refs` 的主要缺口不只是“有没有 id”，而是“id 是否进入统一、可长期解析的审计层”。

## 3. 新版 Document 2 的通用信息类目

下面按 MU、RKLB、FLNC 三类业务样例归纳大多数股票都会使用的上游信息，不穷举行业专属字段。

| 信息类目 | 典型内容 | 主要 Schema 消费位置 |
| --- | --- | --- |
| 公司实际财务与经营结果 | 收入、毛利率、现金流、Capex、Backlog、订单、出货、发射次数 | `ACTUAL StateValue`、硬事件更新 |
| 管理层指引与定性展望 | 收入/利润指引、量产时间、供需判断、订单覆盖期、执行评论 | `MANAGEMENT StateValue`、Factor evidence |
| 卖方一致预期 | 收入/EPS/利润率、份额、供需平衡时间、项目转化率、发射节奏 | `SELL_SIDE StateValue`、market anchor |
| 产业链与行业状态 | 价格、需求、产能、良率、份额、客户认证、供应分配、竞争者进度 | `INDUSTRY_CHAIN StateValue`、Realization Factors |
| 产品与技术里程碑 | 规格、测试、认证、首飞、可靠性、量产阶段、良率爬坡 | `STAGE/TIME/EVIDENCE StateValue`、Factors |
| 客户与合同 | 合同金额/数量/期限、采购份额、取消、验收、载荷准备、付款节点 | State、Factors、Gap activation/invalidation |
| 项目与运营执行 | 建设、并网、交付、验收、制造节奏、供应交付、质保和整改 | State、Factors |
| 监管与政策 | 审批、许可、关税、本地化、补贴、空域/发射许可 | Factors、Activation rules |
| 宏观与终端需求 | 利率、GDP、通胀、AI Capex、云需求、项目融资环境 | `MACRO StateValue`、Modifiers |
| 市场交易状态 | OHLCV、成交量、相对表现、估值、期权、注意力、情绪 | `MARKET_IMPLIED StateValue`、absorption |
| 来源与时间元数据 | 来源角色、发布日期、数据期、as-of、URL、source grade、撤回/替代 | `basis_refs`、`time_scope`、`validity_state` |
| 事件可观测性 | event family、匹配条件、主体、时间、硬/软事件、独立来源数 | `observability`、Gap rules、Updates |

## 4. 数据源 / 工具支持清单：从不能获取到能够获取

评级：

- **0 不支持**：当前没有可靠数据源或只能由模型主观猜测；
- **1 很弱**：偶发公开材料可以搜索，但不可稳定、结构化、复现；
- **2 部分支持**：可收集并有来源，但需抽取、归一或额外计算；
- **3 较好支持**：当前工具可直接返回稳定结构化数据；
- **4 直接满足**：工具输出已与新版 State/Factor/Gap 对象及历史更新契约对齐。

当前没有任何类目达到 4。

| 排序 | 信息类目 | 评级 | 当前工具/来源 | 能获得什么 | 关键缺口 |
| ---: | --- | ---: | --- | --- | --- |
| 1 | 公司特定、参数级 `MARKET_IMPLIED` 状态 | 0 | Twelve Data、yfinance、Finnhub trade stream | 价格、成交量、区间回报、有限实时成交 | 不能从行情直接推出“HBM 紧缺定价到哪一年”“Neutron 商业化时间”“Backlog 转化率”等具体 slot；缺估值/期权/反推模型与方法审计 |
| 2 | 自定义运营指标的卖方一致预期 | 0–1 | Alpha `earnings_events`、DoxAtlas、Tavily | 标准盈利预测或新闻中零散分析师观点 | 无稳定 consensus feed；份额、良率、供需平衡时间、发射次数、Backlog 转化率等通常没有结构化覆盖 |
| 3 | 私有或高敏感供应链指标 | 0–1 | DoxAtlas media/social、Tavily | 公开报道、传闻、供应链评论 | 良率、供应商资格、订单分配、合同价格/量等常不公开；无法保证真实性、完整性和时效性 |
| 4 | State 历史、替代、撤回与 point-in-time ledger | 0–1 | 各工具的日期和 source coordinates | 当前查询结果、部分发布日期 | 没有跨工具统一 state slot ledger；不能自动产生 `SUPERSEDED/DISPUTED/RETRACTED` |
| 5 | 细粒度 event-family 可观测与自动路由 | 1 | Monitoring raw events、DoxAtlas events；独立 CDECR | 新闻/社交消息、DoxAtlas narrative event；CDECR 可形成粗类事件 | 新 family catalog 不存在；Monitoring 未分类；CDECR 未接入 Document 2 |
| 6 | 行业价格、供需、产能、良率和份额 | 1–2 | DoxAtlas、Tavily、FMP sector、SEC | 公开新闻、公司披露、板块表现 | 缺 DRAM/HBM、储能部件等专门行业时序数据；公开证据不等于行业 consensus |
| 7 | 客户合同、订单、验收与项目执行 | 1–2 | SEC filing sections、DoxAtlas、Tavily、Monitoring | 已公开合同、申报披露、新闻和项目更新 | 合同细节经常缺失；跨项目实体、阶段、金额和有效期未结构化 |
| 8 | 产品/技术/监管里程碑 | 2 | DoxAtlas、Tavily、SEC、Newswire/RSS | 官方公告、媒体报道、申报风险与项目更新 | 可发现但需事件抽取、主体匹配、阶段归一和冲突处理；部分技术指标不公开 |
| 9 | 管理层指引与定性展望 | 2–3 | SEC filing sections、SEC facts、DoxAtlas、Tavily | 申报、正式指引、公司评论及相应时间 | 标准财务字段较好；非标准运营 KPI 仍需文本抽取；当前 Document 1 输出丢失字段级 refs |
| 10 | 标准收入/EPS 卖方一致预期 | 2–3 | Alpha earnings estimates | avg/high/low、analyst count、历史均值、修正人数 | 没有分析师/研报级 provenance；不能外推为产品/份额/利润率等运营 KPI consensus |
| 11 | 社交情绪、注意力和叙事 | 2 | DoxAtlas social、Stocktwits、TikHub（若配置） | 社交内容、情绪、叙事和来源 | 代表性与操纵风险；不能等同市场隐含预期；当前本地 MU monitoring 未绑定 |
| 12 | 同业、板块与相对表现 | 2–3 | Finnhub peers、FMP sector、OHLCV | 同业列表、板块收益、股票价格序列 | peer 列表不是竞争位置判断；相对表现需编排多标的调用并保留统一窗口 |
| 13 | 标准公司财务实际值 | 3 | SEC company facts、Alpha statements | 财务报表、XBRL facts、财报期和申报 | 非 GAAP/分部/行业 KPI 覆盖不稳定；尚未转换为 StateParameter/StateValue ledger |
| 14 | 公司申报与原文材料 | 3 | SEC filings、filing sections | 10-K/10-Q/8-K 等及指定章节 | 可以作为 `FILING`，但当前统一 ObjectRef resolver 和字段级 locator 尚未接通；章节解析存在静默假成功风险 |
| 15 | 日线市场数据与事件窗口价格反应 | 3 | Twelve Data、yfinance fallback | OHLCV、区间收益、高低点、成交量摘要 | 支持 price reaction，不支持参数级 market-implied state；事件前快照需显式冻结 |
| 16 | 官方宏观数据 | 3 | FRED、BLS、BEA、Fed | 利率、通胀、就业、GDP/PCE、FOMC 材料 | 需先定义 series mapping 与 horizon；对公司专属 Unit 通常只是 modifier |

## 5. 按 Schema 对象检查生产能力

| Schema 对象/字段 | 数据源是否可能提供 | 当前 workflow 是否能产出 | 当前主要阻塞 |
| --- | --- | --- | --- |
| `ExpectationShell.core_question` | 是 | 旧 shell 可生成近似内容 | 旧 `ExpectationShell` 不是新版 shell，且当前限制少于 4 个“expectations” |
| `boundary_rule` | 主要依靠研究归纳 | 否 | 当前输出 contract 没有 `shared_system/separation_test` |
| Unit `proposition/horizon` | 是 | 只能以旧 `expectation_name/why_it_matters` 近似 | 无新版字段和验证规则 |
| `StateParameter` | 可由领域研究定义 | 否 | 无 schema、无参数 registry、无跨来源口径归一 |
| `StateValue.ACTUAL` | 部分较好 | 否 | 工具结果未物化为 StateValue |
| `StateValue.MANAGEMENT` | 部分支持 | 否 | 文本抽取、time scope、有效期和 supersession 未实现 |
| `StateValue.SELL_SIDE` | 局部 | 否 | 自定义 KPI consensus 数据源不足 |
| `StateValue.INDUSTRY_CHAIN` | 较弱 | 否 | 来源多为公开报道，缺稳定 feed 与冲突治理 |
| `StateValue.MARKET_IMPLIED` | 很弱 | 否 | OHLCV 不等于具体参数隐含状态；缺反推模型 |
| `basis_refs` | 工具有部分 source id | 否 | ObjectRef 类型/ID resolver 不统一；Document 1 `ResearchSection` 不保留 refs |
| `validity_state` | 需要系统维护 | 否 | 没有 slot history 和 replacement transaction |
| `RealizationFactor` | 领域研究可提出 | 否 | 无新版 contract、factor registry 和 observability catalog |
| `PotentialGap` | 可以基于研究推理 | 否 | 当前 Gap 对象不存在；Factor ref 类型冲突；anchor slot 常无数据 |
| `ExpectationUpdate` | 事件与旧/新状态齐备后可确定 | 否 | 当前 runtime 没有 Event → State/Factor mutation transaction |
| `GapActivation.gap_delta` | 可确定性计算 | 否 | 无 before/after slot snapshot |
| `market_absorption` | 价格部分可收集 | 否 | 无事件前 anchor snapshot、参数级隐含状态和吸收模型 |

## 6. Tool Calling 与 Workflow 路由审计

### 6.1 Document 1 有研究工具，但输出是无引用的长文本块

`BuildGlobalResearch` 的四类 agent 当前大致拥有：

- C1：SEC、Alpha Vantage、Tavily；
- C2：FRED、BLS、BEA、Fed、Polymarket、OHLCV；
- C3：Finnhub peers、SEC、FMP sector、Tavily；
- O4：Twelve Data、yfinance、Finnhub trade stream。

但 `GlobalResearchDocument` 只保存：

```text
ResearchSection.text
ResearchSection.summary
ResearchSection.author_agent
```

它不保存字段级来源、source role、time scope 或 object ref。于是上游即使查到了结构化事实，进入 Document 2 时也主要退化为文本上下文。

### 6.2 O1 是 Document 2 唯一生成者，但生成阶段工具过窄

当前 O1：

- construction/detail 阶段核心工具是 `doxa_get_narrative_report`；
- detail 对每个旧 shell 限制最多一次成功 narrative call；
- 不直接拥有 SEC、Alpha、Tavily、OHLCV 或 DoxAtlas detail/source 工具；
- 生成的是旧 `ExpectationUnitCandidateBody`。

这不足以在一次生成中为每个 Unit 可靠建立 3–8 个 State Parameters、各来源角色 State Values、3–6 个 Factors 和 1–4 个 Gaps。

### 6.3 A1/C1/C3/O4 的深工具位于“生成后 review”，且只能返回 finding

`ReviewExpectationFields` 中：

- A1 可查 DoxAtlas propositions/media/social/source；
- C1 可查 SEC/Alpha/Tavily；
- C3 可查 peers/SEC/FMP/Tavily；
- O4 可查 OHLCV/trade stream；
- 每个 reviewer 最多一批 tool calls；
- reviewer 不直接修改 candidate，只返回 finding，再由 O1 resolver 处理。

该架构适合对少量旧字段做审查，不适合把大量缺失的 State Values、Factors 和 Gap Rules 当作主生产路径。

### 6.4 C2 未进入 Document 2 field review

C2 的宏观数据只通过 Document 1 的 `macro_report` 间接进入。新版若要求 Unit 级 MACRO State Parameter、精确 series、as-of 和 basis ref，当前 Document 2 review 没有 C2 的结构化补充路径。

### 6.5 市场证据只对 OHLCV 有专门快照

OHLCV tool 能形成 `market_evidence_snapshot`，包括窗口起止、回报、区间高低、成交量和数据质量标记。这可支持：

- 事件前后价格反应；
- 相对表现（前提是另外调用基准/同业）；
- Market Absorption 的一个输入。

它不能单独支持：

- 某业务参数的市场隐含数值/阶段/时间；
- priced-in 程度；
- residual delta。

### 6.6 Monitoring 当前能收消息，不能直接匹配新版 Activation Rule

Monitoring source catalog 包含 Benzinga、Finnhub company news、Stocktwits、TikHub X、RSS。当前 EventStreamItem 的 `event_type` 仍是通用 `monitoring.message.created`。

本地只读探针显示 MU 当前：

- `by_ticker_sources=[]`；
- `by_parameter_sources=[]`；
- 六个 source 全部处于 `missing_source_ids`；
- `recent_events=[]`。

因此本地当前状态下，MU 的 factor observability 并未真正启用。即使绑定后，消息仍需后续分类、实体/谓词提取和 State/Factor update 逻辑。

### 6.7 CDECR 是潜在上游资产，但本轮不能按“当前已支持”计入

CDECR 已具备：

- 带证据 locator 的 Event Mention；
- 粗粒度 EventFamily；
- assertion state、时间、数值和跨文档事件聚合；
- 独立 registry。

但它明确是独立模块，当前没有接入 Document 2 的 ObjectRef、fine-grained event-family、ExpectationUpdate 或 GapActivation。因此本审计只把它视为可复用基础，不把它计为现有生产路径。

## 7. 当前环境配置与只读探针

配置存在性检查只输出布尔值，不读取或展示密钥：

| Provider | 当前环境 |
| --- | --- |
| DoxAtlas | base URL 与 token 已配置 |
| SEC | 无需 API key |
| Alpha Vantage | 已配置 |
| FRED / BLS / BEA | 已配置 |
| FMP / Finnhub | 已配置 |
| Tavily | 已配置 |
| Twelve Data | 已配置 |
| Stocktwits RapidAPI | 已配置 |
| AnySearch | 未配置 |
| Benzinga | 未配置 |
| TikHub | 未配置 |

以下是真实工具探针结果。所有探针只调用只读 endpoint，不启动分析任务，不调用 LLM。

### 7.1 DoxAtlas：MU 既有完成态 run

只读调用链：

```text
doxa_get_narrative_report
→ doxa_query_propositions
→ doxa_get_media_result / detail
→ doxa_get_social_result / detail
→ doxa_get_event_source
→ doxa_query_analysis / doxa_get_analysis
```

结果：

- `doxa_get_narrative_report(view=agent_provenance)` 成功，返回既有 completed run 的 10 个 narratives，以及 Nxx/Exx、event time、方向、解释、预期和下钻入口；
- N01/E01 的 proposition/social 成功，但 media/source 为 0，说明 endpoint 存在不代表每个 event 都有多通道证据；
- N01/E02 的 propositions、9 条 media、10 条 social、9 条 event source 均可取得，detail 层包含 URL、来源名、发布时间、source grade、正文/摘要；
- `doxa_query_analysis` 找到 8 个 completed tasks；`doxa_get_analysis(T01)` 返回 social/media 的 KPI、sentiment matrix、chart time series、topic atlas 和 noise summary。

能力判定：

| 目标 | 真实探针判断 |
| --- | --- |
| `ACTUAL` | 中：有事件、时间、URL 和正文，但没有权威实际值或 source-role 分类 |
| `MANAGEMENT` | 弱：没有管理层专用指引/电话会/filing 结构 |
| `SELL_SIDE` | 弱–中：新闻可能包含评级/目标价/预测，但没有结构化一致预期 |
| `INDUSTRY_CHAIN` | 中：供需、价格、产能和竞争叙事较丰富，但不是参数曲线 |
| `MARKET_IMPLIED` | 中：方向、情绪、热度、分歧和时间序列较强；不能给出具体参数隐含值 |
| 时间 / as-of | 中强：event time、published at、run window 和 task timeframe 可用 |
| STAGE | 弱：需要从正文推断 |
| 证据 | 中强：N/E/P/M/S/D 短码、URL、来源等级和正文可下钻 |
| `basis_refs` | 需适配：可由 scoped short codes 构造；ToolResult `source_coordinates` 仅到 endpoint 级 |
| event family / observability | 不支持：响应没有新版 family 和 match condition |

质量风险：真实 narrative 中出现过极强数值主张。带媒体来源不等于 `ACTUAL`；具体财务/行情数值仍需 SEC 或市场数据交叉验证。

### 7.2 OHLCV、相对表现、同业、板块与开放搜索

所有目标调用均成功：

- `twelvedata.daily_ohlcv(MU, 60)`：60/60 usable bars，2026-05-01 至 2026-07-28，总回报约 +51.33%，包含标准 OHLCV、区间高低、成交量和质量标记；
- `yfinance.daily_ohlcv(MU, 60)`：同窗口结果近似，明确标记为 unofficial fallback；OHLC 和 volume 有轻微口径差异；
- 同窗口 SOXX 约 +5.52%、QQQ 约 +0.20%，可计算 MU 相对超额收益，但工具本身不负责基准选择、共同窗口和 excess-return 规则；
- `finnhub.company_peers(MU)` 返回候选 peer tickers，但把存储、算力和半导体设备公司混在一起，没有关系类型和权重；
- `fmp.sector_performance` 返回单日大类板块表现，只适合背景 regime，不支持 HBM/memory 子行业状态；
- Tavily 对 MU HBM4/qualification/share/consensus 的查询能发现供需、长期协议、开始出货等公开线索，但没有命中结构化卖方 consensus、资格清单或份额数值；部分低分结果偏题。

明确边界：

- 日线行情强支持 price reaction、volume observability 和相对表现；
- 它不支持期权隐含波动、expected move、事件预期差，也不能直接生成业务参数级 `MARKET_IMPLIED` State；
- Tavily 适合发现 Realization Factor 线索，不能把搜索摘要直接写成精确 State；
- 多数工具缺统一 `retrieved_at/effective_as_of`；Tavily 结果缺发布时间，Finnhub peers 缺生效日期。

### 7.3 SEC 与 Alpha Vantage：MU 基本面和卖方预测

`sec.company_facts_and_filings` 成功：

- 返回 MU（CIK 0000723125）的公司信息、20 条 material filings、635 个 XBRL concepts；
- 最新探针中的 10-Q 带 accession、filed date、report date 和 primary document；
- key facts 的 observation 带 unit、start/end、value、accession、fiscal period、form、filed date；
- ACTUAL 财务 State 和 time scope 支持很强。

但对业务专属 State：

- 没有可靠命中 HBM、良率、市场份额、bit shipment、ASP、产能和订单等字段；
- `RevenueRemainingPerformanceObligation` 有 5.0B 的真实值，可作为合同义务代理，但不能无口径说明地等同“业务 Backlog”；
- SEC 发行人自定义 taxonomy 也没有提供上述产品运营指标。

`sec.filing_sections` 使用明确 accession/primary document 调用成功，但暴露了静默质量问题：

- Item 1A 返回了长篇 risk-factor 内容；
- Item 2 只返回约 309 字符的目录片段，并非 MD&A 正文；
- tool status 仍为 succeeded 且 `unknowns=[]`。

因此 management narrative 只能判为“部分可获取”；上层必须做正文质量检查，不能把 tool succeeded 当作字段可用。

Alpha Vantage：

- `company_overview` 成功，包含 TTM 财务、估值、目标价和评级分布；可支持浅层 SELL_SIDE/MARKET context，但没有目标价区间、分析师身份、报告日期和分项假设；
- `financial_statements` 的 income/balance/cash flow 全部成功；ACTUAL 财务覆盖好，但部分季度现金流可能为累计口径，且大量值以字符串/None 表达，time scope 弱于 SEC；
- `earnings_events` 成功，确实返回 EPS 和 Revenue 的卖方一致预期：avg/high/low、analyst count、7/30/60/90 日前均值、上下修人数，以及 reported vs estimated surprise；
- 这使“标准收入/EPS consensus”达到较好支持，但毛利率、产品份额、良率、Backlog 转化、发射次数等自定义 KPI 仍不支持。

来源边界：

- SEC accession/date 能形成较强字段级 provenance；
- Alpha 的 `source_coordinates` 多数只到工具/function/symbol 层，字段级 analyst/report provenance 不足；
- 两者都仍需上层转换为新版 `ObjectRef` 和统一 `as_of/retrieved_at`。

## 8. 最小补齐顺序

这不是完整实施计划，而是按前置依赖排列的最小顺序。

1. **先修 Schema 内部冲突**：Factor 引用类型、模型派生值归属、event-family catalog、空 State Value 规则。
2. **定义统一 ObjectRef resolver 与 point-in-time source snapshot**：先保证 `basis_refs` 可回查，再扩工具。
3. **把 Document 1 的结构化 tool observations 保留到 Document 2**：避免只传 `ResearchSection.text`。
4. **建立 State Parameter/Slot registry 与 StateValue ledger**：支持 source role、time scope、current/superseded/disputed。
5. **补数据源优先级最高的两类缺口**：
   - 标准和自定义 KPI 的卖方 consensus；
   - 参数级 market-implied state 的明确定义与可审计推导。
6. **接通事件层**：建立 fine-grained family catalog，并把 DoxAtlas/CDECR/Monitoring 事件映射为统一 Event refs。
7. **再改 Document 2 workflow/output contract**：让领域 agent 贡献 State/Factors，O1 负责归一与 Gap 统合，而不是只在旧 candidate 生成后做 finding review。
8. **最后实现 ExpectationUpdate / GapActivation**：使用真实 before/after state snapshot 计算 delta 和 market absorption。

## 9. 验收边界

未来不能用以下弱标准宣称“新版 Document 2 已有上游支持”：

- “能搜到相关新闻”；
- “有 OHLCV，所以有 MARKET_IMPLIED”；
- “DoxAtlas 有 narrative，所以有卖方 consensus”；
- “event_family_ids 是字符串，所以 runtime 可以匹配”；
- “source_coordinates 有 id，所以 ObjectRef 已经闭环”。

至少应满足：

1. 每个生成的 `StateValue` 都能回查原始对象和 as-of；
2. 同一 parameter/source role/time scope 只有一个 CURRENT 值；
3. 新硬事实能确定性替换或争议化旧值；
4. Activation Rule 的 family 来自版本化 catalog；
5. Market-implied slot 有独立证据或可审计 derivation；
6. Gap Activation 使用真实 Event + Update + before/after snapshot，而不是事后叙事补写。

## 10. 主要代码证据

| 证据 | 当前事实 |
| --- | --- |
| `src/doxagent/models/documents.py` | `ExpectationUnitDocument` 仍是旧版字段；`ResearchSection` 只有 text/summary/author |
| `src/doxagent/models/agent_outputs.py` | construction/detail 输出仍是旧 `ExpectationShell` 和 `ExpectationUnitCandidateBody` |
| `src/doxagent/agents/config.py` | 各 agent 的默认 tool permissions |
| `src/doxagent/workflows/initialization/shared.py` | Document 2 node-specific tool overrides；C2 不在 field review |
| `src/doxagent/workflows/document2/legacy_pipeline.py` | O1 生成、一次 narrative call 预算、reviewer finding 路径 |
| `src/doxagent/tools/factory.py` | 当前真实工具 descriptors 与注册清单 |
| `src/doxagent/tools/providers/doxatlas.py` | DoxAtlas read/run endpoint、scope 与 item-detail contract |
| `src/doxagent/tools/providers/sec.py` | SEC facts/filings 和 section extraction |
| `src/doxagent/tools/providers/alpha_vantage.py` | 标准财务、earnings estimates 和 calendar |
| `src/doxagent/tools/market_evidence.py` | OHLCV snapshot 与相对收益基础能力 |
| `src/doxagent/monitoring/schema.py` | source catalog 与通用 `monitoring.message.created` event |
| `src/cdecr/contracts.py` | CDECR 粗粒度 EventFamily，与样例细粒度 family 不同 |

探针通过 `default_real_tool_registry(...).call(...)` 和显式 read-only `AgentPermissions` 执行；没有进入 AgentRunner、ModelGateway 或 workflow resume 路径。
