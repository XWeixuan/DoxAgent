# Document 2 Schema v1 上游数据与 Tool Calling 支持审计

> 初始审计日期：2026-07-29；Document 1 基础预期指标增量审计：2026-08-05
> 审计范围：新版 Expectation Shell / Unit Schema v1 的初始化数据需求、Document 1 基础预期指标采集、当前 DoxAgent tool registry、Document 1 → Document 2 workflow 路由、Monitoring Message Bus，以及 2026-07-29 已完成的只读真实工具探针。
> 本轮方法边界：仅静态检查新增指标、工具实现与 workflow contract；不重新调用真实数据工具，不修改运行时代码，不启动 DoxAtlas 新任务，不发起任何真实 LLM request。第 7 节探针结论是 2026-07-29 历史证据，不代表 2026-08-05 重新验证。

## 1. 结论摘要

当前系统只能支持新版 Document 2 的一部分上游研究，不能直接、完整、可审计地产出 Schema v1。

需要区分三个层面：

1. **数据源层**：SEC 财务事实、公司申报、日线 OHLCV、宏观数据支持较好；公司特定的卖方一致预期、市场隐含参数、供应链私有指标支持最弱。
2. **Tool 层**：DoxAtlas、SEC、Alpha Vantage、Tavily、Twelve Data 等工具能返回材料或结构化序列，但多数输出不是 `StateValue`、`ObjectRef`、`ExpectationUpdate` 所需的业务对象。
3. **Workflow 层**：当前 Document 2 仍生成旧版 `expectation_name / direction / market_view / realized_facts / key_variables / event_monitoring_direction`；O1 生成阶段主要只有 `doxa_get_narrative_report`，其他领域工具只在 Document 1 或候选生成后的 review 阶段可用。因此，“registry 中有工具”不等于“新版 Document 2 可以消费并持久化其结果”。

新增 Document 1 基础指标层后，数据覆盖改善但结论不反转：31 个固定必选指标中，标准财务、部分官方宏观和股价快照可由现有工具较好支撑；核心 PCE、NFCI、跨公司主估值倍数等只能部分支撑；12 个月隐含政策利率、历史远期倍数分位、期权隐含指标仍无当前生产数据路径。更关键的是，仓库尚无指标 registry、确定性 extractor/calculator、`CollectionManifest` 和 `StateValue` 持久化链路，所以“可取原料”仍不等于“已实现基础指标采集”。

总体判断：

| 判断对象 | 当前结论 |
| --- | --- |
| 新版 Shell / Unit 定义 | Agent 研究能力原则上可形成，但当前输出 contract 不支持 |
| `ACTUAL` 财务 State | 数据源较充分，缺少到新 State ledger 的转换与持久化 |
| `MANAGEMENT` State | 可从申报/新闻中收集，定性与非标准指标仍需抽取和口径归一 |
| `SELL_SIDE` State | 仅标准盈利预测局部可得；自定义运营指标的一致预期严重不足 |
| `INDUSTRY_CHAIN` State | 能搜集公开报道，缺少稳定、结构化、可复现的行业数据源 |
| `MARKET_IMPLIED` State | OHLCV 可得，但“某具体参数的市场隐含值”基本不能直接获得 |
| Document 1 固定必选指标 | 31 项均可安排执行状态，但只有一部分有当前确定性数据路径；详见 4.1 |
| Document 1 条件可选指标 | 已按 C1/C2/C3/O4 单列审计；大部分经营、行业和期权指标依赖文本抽取或新增数据源 |
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

### 2.6 单一 `metric_id` 路由与多 `source_role` 采集冲突

新方案一方面规定每个 `metric_id` 只能选择一个 `PROGRAM / AGENT / UNAVAILABLE` 主路径，另一方面又要求同一参数可以同时保留 `ACTUAL / MANAGEMENT / SELL_SIDE / INDUSTRY_CHAIN / MARKET_IMPLIED`。以 `fin_revenue` 为例：实际值和卖方一致预期可走结构化程序，管理层收入指引却必须从文本中抽取；按 `metric_id` 选唯一主路径无法同时完成三类采集。

**前置决策**：路由键至少应改为 `(metric_id, source_role, time_scope)`，或者把一个 metric 展开为显式 `collection_targets[]`，每个 target 独立指定 route、extractor/agent 和 requiredness。“不能由程序和 Agent 各生成一套结果”应解释为禁止同一 target 双写，而不是禁止同一 parameter 的不同 source role 使用不同路径。

### 2.7 指标级 Manifest 无法表达多目标部分成功

当前 `CollectionManifestEntry` 只有一个 `status`。若 `fin_revenue.ACTUAL` 已填、`fin_revenue.MANAGEMENT` 为空、`fin_revenue.SELL_SIDE` 已填，一个指标级 `FILLED/EMPTY` 无法准确表达结果，也会让下游误把“部分填充”当成完整覆盖。

**前置决策**：Manifest 应以 collection target 为粒度，或增加 `target_results[]`；每项至少记录 `source_role`、`time_scope`、`status`、`output_refs`、失败/空值原因。固定必选的“每次执行”也应约束 target，而不仅是 metric 名称。

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

### 4.1 Document 1 固定必选指标逐项支持审计

以下评级沿用本节 0–4 标准，评价的是“当前工具能否取得并确定性转换所需原料”，不是声称现有 workflow 已经生成 `StateValue`。推荐路由按 2.6 的 collection target 粒度给出：`P` = `PROGRAM`，`A` = `AGENT`，`U` = `UNAVAILABLE`。所有 `P` 路径目前仍缺指标 registry、extractor/calculator、统一 as-of 和 Manifest 接线，因此没有一项达到 4。

#### C1：固定财务指标

| metric_id | 评级 | 推荐 target 路由 | 当前静态依据 | 主要缺口 |
| --- | ---: | --- | --- | --- |
| `fin_revenue` | 3 | ACTUAL P；MANAGEMENT A；SELL_SIDE P | SEC/Alpha 报表；Alpha revenue estimates；filing/search 文本 | 单一 metric 路由冲突；管理层区间、GAAP 口径和财年映射未归一 |
| `fin_gross_margin` | 2 | ACTUAL P；MANAGEMENT A；SELL_SIDE U/A | Alpha income statement 可计算；申报文本可抽取指引 | SEC key-fact 白名单无 gross profit；无稳定毛利率 consensus |
| `fin_operating_margin` | 2 | ACTUAL P；MANAGEMENT A；SELL_SIDE U/A | SEC operating income + revenue、Alpha 报表可计算 | 需 period/GAAP 归一；无稳定利润率 consensus |
| `fin_diluted_eps` | 3 | ACTUAL P；MANAGEMENT A；SELL_SIDE P | SEC diluted EPS；Alpha actual/estimate/surprise | adjusted vs GAAP、continuing operations 与拆股口径未统一 |
| `fin_free_cash_flow` | 2 | ACTUAL P；MANAGEMENT A；SELL_SIDE U/A | CFO 与 Capex 可由 SEC/Alpha 取得后计算 | FCF 定义、Capex 符号、TTM/季度拼接尚未固定 |
| `fin_capex` | 3 | ACTUAL P；MANAGEMENT A；SELL_SIDE U/A | SEC PP&E payments；Alpha cash flow | maintenance/growth Capex 不可直接分拆，指引需文本抽取 |
| `fin_cash` | 3 | ACTUAL P | SEC/Alpha balance sheet | 现金、等价物、受限现金和短投合并口径未固定 |
| `fin_total_debt` | 2 | ACTUAL P | Alpha balance sheet；SEC 仅稳定白名单长期债务 | 短债、租赁负债、可转债与 taxonomy 差异需归一 |
| `fin_net_debt` | 2 | ACTUAL P | 由标准化 total debt - cash 计算 | 依赖前两项口径；净现金公司的展示和单位规则未固定 |

#### C2：固定宏观指标

| metric_id | 评级 | 推荐路由 | 当前静态依据 | 主要缺口 |
| --- | ---: | --- | --- | --- |
| `macro_real_gdp_growth` | 3 | P | FRED allowlist `GDPC1`；BEA NIPA | 需固定 QoQ SAAR/YoY 计算和修订 vintage |
| `macro_unemployment_rate` | 3 | P | FRED allowlist `UNRATE`；BLS | 需固定月度 as-of 与修订处理 |
| `macro_core_pce_inflation` | 2 | P | BEA 可取得底层数据 | 当前 FRED allowlist 只有 `PCE/PCEPI`，没有 core PCE 序列；需 BEA 参数映射和 YoY 计算器 |
| `macro_effective_policy_rate` | 3 | P | FRED allowlist `DFF/FEDFUNDS` | 必须固定使用有效联邦基金利率及日/月频规则 |
| `macro_implied_policy_rate_12m` | 0 | U | 当前无 Fed funds futures/OIS curve 工具 | Polymarket 事件概率不等于 12m 隐含政策利率 |
| `macro_us_10y_yield` | 3 | P | FRED allowlist `DGS10` | 交易日缺值和 snapshot date 规则未固定 |
| `macro_high_yield_oas` | 3 | P | FRED allowlist `BAMLH0A0HYM2` | 需声明 ICE BofA 指数口径和单位 |
| `macro_financial_conditions` | 0 | U（当前）；新增序列后 P | 方案固定 Chicago Fed NFCI | 当前 FRED allowlist 不含 NFCI，其他工具无确定性 NFCI 输出 |
| `macro_broad_usd` | 3 | P | FRED allowlist `DTWEXBGS` | 需固定指数基期与更新日处理 |
| `macro_vix` | 3 | P | FRED allowlist `VIXCLS` | 非交易日/as-of 规则未固定 |

#### O4：固定估值、期权与仓位指标

| metric_id | 评级 | 推荐路由 | 当前静态依据 | 主要缺口 |
| --- | ---: | --- | --- | --- |
| `market_share_price` | 3 | P | Twelve Data / yfinance 日线 OHLCV | 需明确 close/adjusted close、交易日和币种 |
| `market_cap` | 3 | P | Alpha company overview；价格 × shares 也可计算 | overview 与价格/股本 as-of 可能错位 |
| `market_enterprise_value` | 2 | P | Alpha overview 可提供快照；也可由市值、债务、现金计算 | 少数股东权益/优先股/租赁与跨时点口径未统一 |
| `market_primary_forward_multiple` | 2 | P（按公司类型配置） | Alpha 主要覆盖 Forward P/E；财务原料可计算部分倍数 | 无跨公司类型的 primary-multiple policy；Forward EV/Sales、EV/EBITDA 等前瞻分母不足 |
| `market_primary_multiple_percentile` | 0 | U | 无 point-in-time 历史远期倍数序列 | 当前价格史不能重建当时 consensus 分母，不能用 hindsight 伪造 |
| `market_peer_premium` | 1 | P/A（仅候选） | Finnhub peers + Alpha overview + OHLCV | peer 集合未经治理，倍数口径/币种/as-of 不一致，不能稳定计算 |
| `market_atm_iv_30d` | 0 | U | 无 option chain / IV surface 工具 | 不能由 OHLCV 替代 |
| `market_next_event_implied_move` | 0 | U | Alpha 仅有事件日历/盈利数据，无事件期权跨式报价 | 无 next-event option expiry 与 straddle 计算链路 |
| `market_put_skew_30d` | 0 | U | 无 option chain / delta/strike surface | 无可审计 skew 定义与输入 |
| `market_short_interest_pct_float` | 1 | P（快照） | Alpha overview 可能返回 short/float 字段 | 无稳定结算日序列、字段 provenance 与跨市场覆盖 |
| `market_days_to_cover` | 1 | P（快照） | Alpha overview 可能返回 short ratio；OHLCV 可算 ADV | short as-of 与成交量窗口可能错位，定义未固定 |
| `market_short_interest_change` | 0 | U | 当前无持久化 short-interest 历史序列 | 单次 overview 快照无法计算可复现变化 |

固定必选总体分布：评级 3 共 13 项，评级 2 共 8 项，评级 1 共 3 项，评级 0 共 7 项。这里的 13 项“较好支持”仍只是原料层；若以“现有 workflow 已能写入合规 `StateValue`”为标准，则 31 项全部仍低于 4。

### 4.2 Document 1 条件可选指标独立清单

为避免把“可搜到”误写成“可结构化采集”，下表按拥有相同主要数据路径和缺口的指标分组；**每个可选 metric_id 在本节只出现一次**。组内评级是保守下限：个别发行人主动披露时可以更高，但不能据此提升系统级支持度。

#### C1 可选财务指标

| 评级 | metric_id | 推荐路由与判断 |
| ---: | --- | --- |
| 3 | `fin_net_income`, `fin_operating_cash_flow`, `fin_r_and_d_expense`, `fin_inventory`, `fin_accounts_receivable`, `fin_accounts_payable`, `fin_diluted_share_count`, `fin_dividend` | ACTUAL P；SEC/Alpha 有结构化原料，仍需 period、unit 和 taxonomy 归一 |
| 2 | `fin_ebitda`, `fin_ebitda_margin`, `fin_adjusted_free_cash_flow`, `fin_sga_expense`, `fin_stock_based_compensation`, `fin_interest_expense`, `fin_tax_rate`, `fin_working_capital`, `fin_deferred_revenue`, `fin_share_repurchase`, `fin_liquidity`, `fin_cash_runway`, `fin_return_on_equity`, `fin_return_on_invested_capital` | ACTUAL P/A；可由报表计算或从披露抽取，但 adjusted 定义、分母和跨期规则未治理 |
| 1 | `fin_debt_maturity` | A；SEC filing 可发现债务到期表，但当前 section extractor 与表格结构化不稳定 |

#### C1 可选经营指标

| 评级 | metric_id | 推荐路由与判断 |
| ---: | --- | --- |
| 1–2 | `op_unit_volume`, `op_shipment_volume`, `op_transaction_volume`, `op_booking_volume`, `op_order_volume`, `op_production_volume`, `op_sales_volume` | A；仅发行人披露时可从 filing/新闻抽取，无通用结构化 feed |
| 1–2 | `op_average_selling_price`, `op_average_order_value`, `op_arpu`, `op_subscription_price`, `op_take_rate`, `op_yield_per_unit`, `op_revenue_per_unit` | A；业务定义高度公司特定，需单位和分母治理 |
| 1–2 | `op_customer_count`, `op_active_customer_count`, `op_subscriber_count`, `op_active_user_count`, `op_paid_user_count`, `op_customer_additions`, `op_user_growth`, `op_retention_rate`, `op_churn_rate` | A；公开披露不稳定，active/paid/retention 口径跨公司不可直接比较 |
| 1–2 | `op_bookings`, `op_backlog`, `op_remaining_performance_obligations`, `op_order_growth`, `op_order_coverage`, `op_contract_duration`, `op_renewal_rate`, `op_cancellation_rate` | A；SEC 可偶发命中 RPO/合同材料，但 backlog、booking、order 不可互换 |
| 1–2 | `op_capacity`, `op_capacity_addition`, `op_capacity_utilization`, `op_yield_rate`, `op_production_ramp`, `op_delivery_volume`, `op_lead_time`, `op_inventory_days` | A；主要来自管理层文本/行业材料，无通用确定性 extractor |
| 1–2 | `op_market_share`, `op_market_share_change`, `op_customer_concentration`, `op_geographic_mix`, `op_product_mix`, `op_channel_mix` | A；mix/concentration 可由部分披露计算，市场份额仍依赖外部行业分母 |
| 1 | `op_product_development_stage`, `op_customer_validation_stage`, `op_qualification_stage`, `op_regulatory_stage`, `op_commercialization_stage`, `op_mass_production_stage`, `op_store_count`, `op_occupancy_rate`, `op_same_store_sales_growth` | A；阶段类需文本证据与状态 ontology；零售 KPI 仅特定公司披露 |

#### C2 可选宏观指标

| 评级 | metric_id | 推荐路由与判断 |
| ---: | --- | --- |
| 3 | `macro_initial_jobless_claims`, `macro_us_2y_yield`, `macro_yield_curve_2s10s`, `macro_yield_curve_3m10y`, `macro_wti_crude_price` | P；当前 FRED allowlist 已直接覆盖 `ICSA/DGS2/T10Y2Y/T10Y3M/DCOILWTICO` |
| 2–3 | `macro_nominal_gdp_growth`, `macro_real_consumption_growth`, `macro_retail_sales_growth`, `macro_industrial_production_growth`, `macro_capacity_utilization`, `macro_durable_goods_orders`, `macro_business_fixed_investment`, `macro_equipment_investment`, `macro_construction_spending`, `macro_ism_manufacturing`, `macro_ism_services`, `macro_consumer_confidence`, `macro_small_business_optimism`, `macro_housing_starts`, `macro_existing_home_sales`, `macro_new_home_sales`, `macro_auto_sales` | P；官方数据原则上可得，但现有 allowlist/API 参数映射只覆盖部分，且增长率/季调规则未配置 |
| 2–3 | `macro_nonfarm_payroll_change`, `macro_job_openings`, `macro_labor_force_participation`, `macro_average_hourly_earnings_growth`, `macro_core_cpi_inflation`, `macro_headline_cpi_inflation`, `macro_headline_pce_inflation`, `macro_producer_price_inflation`, `macro_employment_cost_index`, `macro_inflation_expectation_5y`, `macro_breakeven_inflation_10y` | P；BLS/BEA/FRED 可覆盖相当部分，但当前缺受控 series catalog、计算方法和 revision vintage |
| 2–3 | `macro_us_30y_yield`, `macro_real_yield_10y`, `macro_investment_grade_oas`, `macro_bank_lending_standards`, `macro_money_supply_growth`, `macro_fed_balance_sheet`, `macro_reverse_repo_balance`, `macro_bank_reserves`, `macro_mortgage_rate_30y`, `macro_corporate_borrowing_cost` | P；官方序列可得性较高，但除 `M2SL`/`BOGMBASE` 外多数未进入当前 FRED allowlist |
| 1–2 | `macro_brent_crude_price`, `macro_natural_gas_price`, `macro_copper_price`, `macro_aluminum_price`, `macro_steel_price`, `macro_gold_price`, `macro_electricity_price`, `macro_freight_rate`, `macro_semiconductor_price_index`, `macro_food_commodity_index`, `macro_currency_pair`, `macro_country_specific_fx` | P/A；当前仅 WTI 明确 allowlist，其他需新增 series/provider；泛用 OHLCV 不等于受控宏观口径 |
| 0–1 | `macro_federal_spending_growth`, `macro_defense_spending`, `macro_infrastructure_spending`, `macro_government_procurement`, `macro_subsidy_amount`, `macro_tax_rate`, `macro_tariff_rate`, `macro_export_control_status`, `macro_sanction_status`, `macro_policy_approval_stage`, `macro_regulatory_policy_stage` | A/U；金额类可能来自政府/BEA 材料，政策状态需事件抽取；当前无统一政策数据库、实体范围和 stage ontology |

#### C3 可选行业指标

| 评级 | metric_id | 推荐路由与判断 |
| ---: | --- | --- |
| 1–2 | `ind_market_size`, `ind_market_growth`, `ind_end_demand_growth`, `ind_shipment_growth`, `ind_consumption_growth`, `ind_customer_capex`, `ind_customer_order_growth`, `ind_utilization_demand`, `ind_traffic_growth`, `ind_usage_growth`, `ind_booking_growth`, `ind_geographic_demand`, `ind_segment_demand` | A；DoxAtlas/Tavily/SEC 可发现公开证据，无跨行业稳定分母和时序 feed |
| 1–2 | `ind_total_capacity`, `ind_capacity_growth`, `ind_capacity_addition`, `ind_capacity_reduction`, `ind_capacity_utilization`, `ind_production_growth`, `ind_supply_growth`, `ind_supply_shortage`, `ind_supply_surplus`, `ind_lead_time`, `ind_delivery_time`, `ind_raw_material_availability`, `ind_power_availability`, `ind_labor_availability` | A；公开材料可抽取局部事实，私有供给、利用率和交期覆盖弱 |
| 1–2 | `ind_average_selling_price`, `ind_spot_price`, `ind_contract_price`, `ind_price_growth`, `ind_discount_rate`, `ind_unit_cost`, `ind_input_cost`, `ind_gross_margin`, `ind_incremental_margin`, `ind_customer_acquisition_cost`, `ind_revenue_per_unit`, `ind_profit_pool` | A/U；公司披露和新闻可提供碎片，缺专业价格/成本数据库与统一口径 |
| 1–2 | `ind_inventory_level`, `ind_inventory_days`, `ind_channel_inventory`, `ind_customer_inventory`, `ind_order_growth`, `ind_order_backlog`, `ind_order_coverage`, `ind_book_to_bill`, `ind_cancellation_rate`, `ind_contract_duration`, `ind_renewal_rate`, `ind_preorder_volume` | A；SEC/媒体可偶发披露，渠道/客户库存和订单口径不可稳定复现 |
| 1–2 | `ind_target_market_share`, `ind_market_share_change`, `ind_competitor_market_share`, `ind_relative_price_position`, `ind_relative_cost_position`, `ind_relative_performance`, `ind_customer_concentration`, `ind_supplier_concentration`, `ind_channel_position`, `ind_geographic_position`, `ind_product_mix_position` | A；peers/OHLCV 仅支持候选同业和价格相对表现，不支持竞争位置本身 |
| 1 | `ind_product_development_stage`, `ind_sampling_stage`, `ind_customer_testing_stage`, `ind_customer_validation_stage`, `ind_qualification_stage`, `ind_supplier_entry_stage`, `ind_order_allocation_stage`, `ind_regulatory_approval_stage`, `ind_production_ramp_stage`, `ind_mass_production_stage`, `ind_commercial_launch_stage`, `ind_revenue_recognition_stage` | A；可从文本判断，但无阶段 ontology、迁移规则和冲突处理 |
| 1 | `ind_competitor_capacity_plan`, `ind_competitor_product_timeline`, `ind_competitor_pricing_direction`, `ind_competitor_market_share_outlook`, `ind_customer_demand_outlook`, `ind_customer_procurement_timeline`, `ind_supplier_supply_outlook`, `ind_supplier_cost_outlook`, `ind_channel_demand_outlook`, `ind_industry_balance_timing`, `ind_industry_cycle_direction` | A；本质是带来源角色的 forward-looking statements，不应伪装成结构化实际值 |

#### O4 可选估值、期权、仓位与流动性指标

| 评级 | metric_id | 推荐路由与判断 |
| ---: | --- | --- |
| 2–3 | `market_trailing_pe`, `market_forward_pe`, `market_price_to_sales`, `market_forward_price_to_sales`, `market_ev_to_sales`, `market_forward_ev_to_sales`, `market_ev_to_ebitda`, `market_forward_ev_to_ebitda`, `market_price_to_book`, `market_price_to_tangible_book`, `market_price_to_free_cash_flow`, `market_free_cash_flow_yield`, `market_earnings_yield`, `market_dividend_yield`, `market_peg` | P；Alpha overview 可直接覆盖部分，其余可由财务原料计算；前瞻分母、as-of 和非盈利公司规则仍缺 |
| 0–1 | `market_price_to_nav`, `market_ev_to_subscriber`, `market_ev_to_arr`, `market_ev_to_capacity`, `market_ev_to_resource`, `market_sum_of_parts_value` | A/U；依赖公司特定 NAV/KPI/分部模型，无当前通用数据源或可审计模型对象 |
| 2 | `market_realized_volatility_20d`, `market_realized_volatility_60d` | P；OHLCV 可确定性计算，但需固定复权价格、年化因子、交易日和 as-of |
| 0 | `market_atm_iv_7d`, `market_atm_iv_60d`, `market_atm_iv_90d`, `market_iv_realized_vol_spread`, `market_iv_term_slope_30d_90d`, `market_call_skew_30d`, `market_put_call_volume_ratio`, `market_put_call_open_interest_ratio`, `market_option_open_interest`, `market_option_volume`, `market_event_straddle_price`, `market_event_iv_premium`, `market_expected_daily_move`, `market_expected_weekly_move` | U；当前没有期权链、IV surface 或事件跨式报价，不能由历史波动率替代 |
| 1–2 | `market_short_interest_shares`, `market_float_shares`, `market_average_daily_volume`, `market_turnover_rate`, `market_institutional_ownership`, `market_insider_ownership` | P（快照）；Alpha overview/shares 与 OHLCV 可覆盖部分，缺一致 as-of、历史序列和跨市场口径 |
| 0–1 | `market_borrow_fee`, `market_borrow_utilization`, `market_etf_ownership`, `market_options_open_interest_concentration`, `market_block_trade_activity`, `market_off_exchange_volume_share` | U/A；当前无 securities lending、ETF holdings、期权集中度、block/off-exchange 专用 feed |

可选指标的治理结论不是“全部都应采”：固定枚举适合作为候选 catalog，但 `OPTIONAL` 仍必须同时通过适用性、重要性和可靠来源三道门槛。尤其是公司特定 operating/industry 指标，不能因名称在枚举中就自动生成空 `StateParameter` 或低质量搜索结论。

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

1. **先修 Schema 与采集契约冲突**：Factor 引用类型、模型派生值归属、event-family catalog、空 State Value 规则，以及 2.6/2.7 的 target 级 route/Manifest 粒度。
2. **落地基础指标 registry 与确定性采集骨架**：先覆盖评级 3 的 13 个固定必选指标，逐 target 输出 `FILLED/EMPTY/NOT_APPLICABLE`，不得用空值阻断 Document 1。
3. **定义统一 ObjectRef resolver 与 point-in-time source snapshot**：先保证 `basis_refs` 可回查，再扩工具。
4. **把 Document 1 的结构化 tool observations 保留到 Document 2**：避免只传 `ResearchSection.text`。
5. **建立 State Parameter/Slot registry 与 StateValue ledger**：支持 source role、time scope、current/superseded/disputed。
6. **补固定必选的硬缺口，再扩可选 catalog**：优先接入 12m 隐含政策利率、NFCI、point-in-time 历史远期倍数、期权链和 short-interest 历史；可选指标按适用性逐类开放，不一次性全启用。
7. **补数据源优先级最高的两类通用缺口**：
   - 标准和自定义 KPI 的卖方 consensus；
   - 参数级 market-implied state 的明确定义与可审计推导。
8. **接通事件层**：建立 fine-grained family catalog，并把 DoxAtlas/CDECR/Monitoring 事件映射为统一 Event refs。
9. **再改 Document 2 workflow/output contract**：让领域 agent 贡献 State/Factors，O1 负责归一与 Gap 统合，而不是只在旧 candidate 生成后做 finding review。
10. **最后实现 ExpectationUpdate / GapActivation**：使用真实 before/after state snapshot 计算 delta 和 market absorption。

## 9. 验收边界

未来不能用以下弱标准宣称“新版 Document 2 已有上游支持”：

- “能搜到相关新闻”；
- “有 OHLCV，所以有 MARKET_IMPLIED”；
- “DoxAtlas 有 narrative，所以有卖方 consensus”；
- “FRED/BEA 理论上有该序列，所以当前 tool 已经支持”；
- “Alpha overview 有一个快照，所以已有 point-in-time 历史”；
- “event_family_ids 是字符串，所以 runtime 可以匹配”；
- “source_coordinates 有 id，所以 ObjectRef 已经闭环”。

至少应满足：

1. 固定必选 collection target 每次均有 `FILLED/EMPTY/NOT_APPLICABLE`，且 Manifest 能表达多 source-role 的部分成功；
2. 可选指标只有在适用、重要且来源可靠时才生成，不因枚举存在而制造空 State；
3. 每个生成的 `StateValue` 都能回查原始对象和 as-of；
4. 同一 parameter/source role/time scope 只有一个 CURRENT 值；
5. 新硬事实能确定性替换或争议化旧值；
6. Activation Rule 的 family 来自版本化 catalog；
7. Market-implied slot 有独立证据或可审计 derivation；
8. Gap Activation 使用真实 Event + Update + before/after snapshot，而不是事后叙事补写。

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
| `src/doxagent/tools/providers/fred.py` | 当前 FRED series allowlist；用于区分“外部存在”与“当前工具已放行” |
| `src/doxagent/tools/market_evidence.py` | OHLCV snapshot 与相对收益基础能力 |
| `src/doxagent/monitoring/schema.py` | source catalog 与通用 `monitoring.message.created` event |
| `src/cdecr/contracts.py` | CDECR 粗粒度 EventFamily，与样例细粒度 family 不同 |

2026-07-29 探针通过 `default_real_tool_registry(...).call(...)` 和显式 read-only `AgentPermissions` 执行；没有进入 AgentRunner、ModelGateway 或 workflow resume 路径。2026-08-05 增量审计没有调用任何真实工具或 LLM。
