# Document 1 / Document 2 数据源与 Tooling 补充方案

> 版本：v1.0  
> 日期：2026-08-06  
> 范围：基础预期指标采集（Document 1）与 Expectation Unit 生成（Document 2）的上游数据源、业务工具、路由、Manifest 与降级边界  
> 核验方式：本轮检查当前代码并核对各数据源官方 API 文档；未调用真实数据接口，也未发起真实 LLM request。

## 1. 执行结论

当前上游不是“完全缺数据”，而是存在三个结构性问题：

1. **现有工具覆盖面窄于供应商能力。** 例如 FMP 只接了行业表现、Finnhub 只接了 peers 与逐笔流、BEA 只接了 NIPA、FRED 只有小型固定白名单。供应商存在的接口不能等同于系统已经具备的能力。
2. **部分工具按供应商而非业务语义封装。** SEC 同时承担财务事实、原始文件、合同、管理层披露等完全不同的检索任务；继续用一个复合工具会令 Agent 难以选择，也会扩大返回数据和误判空间。
3. **原始数据、派生指标与 Document 2 状态生成之间缺少受治理的编译层。** ATM IV、偏斜、事件隐含波动、远期估值分位等不应由 Agent 临时自由计算，而应由有版本的方法生成，并把输入、时间点和公式写入审计信息。

目标形态建议为 **18 个来源族、46 个面向业务语义的 source-facing tools**。它们不是全部开放给所有 Agent，而是通过 `collection_target` 进行白名单授权：

```text
provider client / official source
  -> semantic business tool
  -> normalization + governed calculation
  -> collection target result
  -> target-level Manifest
  -> Document 1 metric observations
  -> Document 2 State / Gap / Realization Factor
```

其中：

- “已存在供应商接口”不标为“已支持”；只有完成 provider、tool schema、权限、测试和 Manifest 接线才算已接入。
- 私有或高敏感供应链指标不能因为 SEC 有披露而视为可得。SEC 只能覆盖发行人公开披露的部分，未披露部分仍为 `UNAVAILABLE`。
- Benzinga 的 unusual option activity 不是完整期权链，不能替代 IBKR 计算 ATM IV、skew 或事件隐含波动。
- FMP/Twelve Data 的当前公开接口可以提供最新或按期间的卖方预测，但尚不能证明可重建“任意历史时点当时可见的 forward consensus”。因此历史远期估值分位仍是一个未闭合缺口。
- 图片中已暴露 API key。本文不复述密钥；上线前应轮换，并只通过 secret manager / 环境变量注入，禁止进入 Prompt、日志、Manifest 与工具结果。

## 2. 设计原则

### 2.1 何时拆分为多个 tools

同一供应商满足以下任一条件时，应拆成不同 tool：

- 查询意图、过滤条件或证据强度不同；
- 输出 schema 不同；
- 权限或订阅等级不同；
- 一个返回结构化指标，另一个返回长文本或原始文件；
- 一个是权威观测，另一个只是发现、定位或降级来源；
- 需要不同的速率限制、缓存周期或失败处理。

反之，多个 endpoint 共同完成一个不可分割的业务动作时，可封装为一个 tool。例如 `ibkr.option_surface` 内部需要按顺序调用合约搜索、执行价与合约详情，再批量拉取 snapshot；不应让 Agent 手工拼接这条协议流程。

### 2.2 Tool 的统一返回契约

每个业务 tool 至少返回：

```yaml
collection_target_id: string
provider: string
tool_name: string
as_of: datetime
retrieved_at: datetime
items:
  - metric_or_record_type: string
    value: scalar | range | object
    unit: string | null
    period_start: date | null
    period_end: date | null
    published_at: datetime | null
    source_role: enum
    source_ref: object
    entity_ref: object
    quality_flags: [string]
coverage:
  requested: integer
  succeeded: integer
  unavailable: integer
  failed: integer
errors: [object]
methodology:
  method_id: string | null
  version: string | null
  input_refs: [object]
```

硬性规则：

- 不把缺失字段填成零；不把无可靠状态填成空区间。
- `published_at`、`as_of`、`retrieved_at` 分开保存，避免未来数据污染。
- 原始来源、聚合商数据和模型派生结果保留不同 provenance。
- 长文本 tool 返回 document locator、section、坐标和摘录，不直接生成 State。
- provider 的原始字段在 adapter 层保存；业务 tool 只暴露稳定、受治理的字段。

### 2.3 路由与可用性

路由键应为：

```text
collection_target_id
  -> provider priority
  -> semantic tool
  -> endpoint/mode
  -> normalization/calculation method
```

`PROGRAM`、`AGENT`、`UNAVAILABLE` 只描述编排方式或本次执行结果，不参与指标路由。一个指标可以有多个采集目标，也可以出现部分成功；因此 Manifest 必须以 `collection_target` 为粒度。

## 3. 当前实现再审计

| 来源 | 当前 tools / 实现 | 当前实际能力 | 主要缺口 | 处置 |
|---|---|---|---|---|
| SEC | `sec.company_facts_and_filings`、`sec.filing_sections` | submissions + companyfacts；有限概念白名单；按 filing section 抽取 | 财务、文件、合同、管理层披露混在一起；概念白名单过窄；section 可能只命中目录片段 | 拆为 5 个语义 tools，旧复合工具在等价验证后退役 |
| Alpha Vantage | overview、statements、shares、earnings events | 公司概况、标准财务、股本、盈利事件 | 不解决行业经营指标、期权、合同、官方宏观 | 保留为标准财务与事件降级源，不扩展为主干 |
| Twelve Data | `twelvedata.daily_ohlcv` | 日线 OHLCV | 尚未接 earnings/revenue estimates；`/earnings` 不是 forward consensus | 保留 OHLCV，新增临时卖方预测 tool |
| FRED | `fred.series_observations` | `/fred/series/observations` + 小型固定白名单 | 缺 NFCI、core PCE 等必选项；175 个候选序列没有语义目录 | 拆为 4 个语义 tools，共用受治理 series registry |
| BLS | `bls.timeseries` | 可按原始 series id 查询 | Agent 不应直接猜 series id；没有 PPI、进出口价格目录 | 拆为 3 个语义 tools，建立 series alias/行业目录 |
| BEA | `bea.nipa_data` | NIPA | 没有 GDPByIndustry、InputOutput 等行业账户 | 扩展为 national accounts + industry accounts |
| Federal Reserve | `fed.fomc_calendar_materials` | FOMC 官方材料 | 不提供市场隐含的 12 个月政策利率路径 | 保留；政策路径由 IBKR Fed Funds futures 计算 |
| FMP | `fmp.sector_performance` | 行业表现快照 | 供应商的大量估值、预测、同业、transcript 能力均未接入 | 新增 4 个 tools；现有行业表现保留 |
| Finnhub | `finnhub.company_peers`、`finnhub.trade_stream` | 同业列表、实时逐笔流 | 所有权、内部人、公司事件未接；逐笔流不适合作为基础日频指标 | 新增 2 个业务 tools，逐笔流保留为监控能力 |
| DoxAtlas | research / evidence tools | 新闻与叙事发现 | 不是权威财务、宏观或监管数值源 | 只用于证据发现和交叉验证 |
| Tavily | 搜索/抽取 | 开放网页发现 | 结果不具备固定 schema 和稳定点时性 | 仅降级发现，不直接写 State Value |
| yfinance | 财务/行情等 | 非官方聚合降级 | 字段稳定性、授权、点时性不足 | 不作为生产主源 |
| Polymarket | 事件概率 | 特定预测市场事件 | 不等于 Fed Funds 12 个月隐含利率 | 保留在事件概率场景，不挪用 |
| IBKR、Benzinga、Census M3、EIA、USAspending、SAM.gov、Regulations.gov、Federal Register、Congress.gov、openFDA、公司 IR | 未形成生产 tools | 供应商或官方接口存在 | provider、tool、权限、缓存、测试与 Manifest 均未完成 | 按本文新增 |

## 4. 信息类目与最终来源映射

支持度定义：

- **A：可闭合**——官方/付费接口和计算方法均明确，完成工程接入即可。
- **B：部分闭合**——只能覆盖公开披露或部分行业/公司，需要明确 unavailable。
- **C：仍有缺口**——现有候选来源不能满足点时性、完整度或目标定义。

| 信息类目 | 主来源 | 降级/交叉验证 | 支持度 | 关键边界 |
|---|---|---|---|---|
| 参数级 `MARKET_IMPLIED`：ATM IV、skew、事件隐含波动 | IBKR 期权链与 snapshot | Benzinga option activity 仅作定位/仓位证据 | A | 需要 OPRA/标的行情订阅；派生公式版本化 |
| 12 个月市场隐含政策利率 | IBKR Fed Funds futures | FRED 实际政策利率、FOMC 材料 | A | 不是 FRED series；需合约映射与加权规则 |
| short interest / borrow pressure | Benzinga short interest；IBKR shortable/fee 字段 | SEC 公开披露仅作背景 | A | report date 与 publication date 均需保存 |
| 自定义经营 KPI 卖方共识 | 无 | 公司 guidance / transcript 只能是管理层口径 | C | 按用户方案放弃；不得用单家分析师观点伪装成共识 |
| 私有/高敏感供应链指标 | SEC 公开文件与合同附件 | 公司 IR、公开政府合同 | B/C | 未公开部分仍不可得；不得标为“SEC 可满足” |
| 行业价格、供需、产能、良率、份额 | BLS、BEA、Census M3、EIA、openFDA、FRED | SEC / IR 公司披露 | B | 可闭合通用及部分行业；良率/份额常需公司披露或专有行业源 |
| 合同、订单、验收与项目执行 | SEC 8-K/附件、USAspending、SAM.gov | 公司 IR | B | 政府来源只覆盖公共采购；8-K Item 1.01 需阅读正文/附件 |
| 产品、技术与监管里程碑 | SEC、Regulations.gov、Federal Register、openFDA、Congress.gov | 公司 IR | A/B | 各行业监管源不同；必须做实体/产品 alias |
| 管理层指引与定性展望 | Benzinga transcript/guidance、SEC、公司 IR | FMP transcript | A/B | transcript 是聚合转录；SEC/IR 是更强证据 |
| 标准收入/EPS 卖方共识 | FMP | Benzinga consensus/earnings；Twelve Data 临时 | A（最新/期间） | 历史 point-in-time vintage 尚未验证 |
| 标准公司财务实际值 | SEC companyfacts / filing | Alpha Vantage / FMP | A | US issuer 以 SEC 为主；非美公司需单独主源策略 |
| 公司申报与原文 | SEC EDGAR | 公司 IR | A | 保存 accession、原文和 exhibit 级 source ref |
| 日线行情及财报后价格反应 | IBKR / Twelve Data | Alpha/Finnhub fallback | A | 明确 split/dividend adjustment 与交易日历 |
| 官方宏观 | FRED、BLS、BEA | Census、EIA | A | 需要受治理 series/dataset registry 与 vintage 策略 |
| 监管/立法环境 | Federal Register、Regulations.gov、Congress.gov | SEC 风险因素/IR | A/B | Federal Register API 页面不是法律正式版本，保留原始 PDF/GovInfo 链接 |
| 所有权、内部人交易 | Finnhub；SEC 原始 filing 可后续增强 | FMP | A/B | 聚合商字段与原始 filing 保持可追溯 |
| 历史远期估值百分位 | FMP 当前估值 + 同业；历史价格/财务 | 尚无被验证的历史 consensus vintage | C | 只能先做 trailing 或 current-forward 横截面对比，不能偷换概念 |

## 5. 目标 Tool 清单（18 个来源族、46 个 tools）

### 5.1 SEC：5 个

官方接口：[EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)。

| Tool | Endpoint / 资源 | 负责的数据 | 不负责 |
|---|---|---|---|
| `sec.issuer_filings` | `GET https://data.sec.gov/submissions/CIK##########.json`；历史 submissions 文件 | 按 CIK、form、filing date、report date、accession 建立申报索引 | 不解析正文，不生成经营结论 |
| `sec.company_financials` | `/api/xbrl/companyfacts/CIK....json`；`/api/xbrl/companyconcept/{cik}/{taxonomy}/{tag}.json`；必要时 `/api/xbrl/frames/...` | 标准财务事实、单位、period、form、filed、accession；扩充受治理 taxonomy/concept registry | 不从 narrative 猜数，不把 frames 当公司原始申报 |
| `sec.filing_content` | `https://www.sec.gov/Archives/edgar/data/{cik}/{accession-no-dashes}/{primary_doc}` 与同目录 exhibits | 原始 filing、附件、可定位文本、表格与 section coordinates | 不按单一 item 直接断言合同/指引 |
| `sec.material_contracts_projects` | 先以 submissions 过滤 8-K Item 1.01 等，再读取 primary document 与相关 exhibits | 合同主体、金额/范围、期限、里程碑、终止条款、项目状态；证据定位 | 不覆盖私下合同；未披露字段保持 unavailable |
| `sec.management_disclosures` | 8-K Item 2.02/7.01、Exhibit 99.1、10-Q/10-K MD&A | 管理层 guidance、定性 outlook、经营 KPI、风险与变化原因 | 不把管理层指引标成卖方共识 |

实施说明：为 SEC 设置 User-Agent、请求节流、原始文件缓存和 accession 去重。当前 `sec.company_facts_and_filings` 在上述工具达到等价覆盖并通过回归后再废弃。

### 5.2 IBKR：5 个

官方接口：[Client Portal Web API](https://ibkrcampus.com/campus/ibkr-api-page/cpapi-v1/)、[market snapshot](https://ibkrcampus.com/docs/web-api/api-reference/trading-market-data/get-md-snapshot)、[market history](https://ibkrcampus.com/docs/web-api/api-reference/trading-market-data/get-md-history)。

| Tool | Endpoint / 协议 | 负责的数据 | 关键规则 |
|---|---|---|---|
| `ibkr.contract_search` | `/iserver/secdef/search`、`/iserver/secdef/strikes`、`/iserver/secdef/info` | symbol -> conid；期权到期日、执行价、call/put 合约；Fed Funds futures 合约解析 | 结果缓存并绑定交易所、币种和 multiplier，禁止只凭 symbol 猜合约 |
| `ibkr.market_snapshot` | `GET /v1/api/iserver/marketdata/snapshot` | last/bid/ask、成交量、shortable shares、fee rate、标的 30-day IV 等字段 | 先完成账户/行情 preflight；只暴露字段白名单与字段定义版本 |
| `ibkr.market_history` | `GET /v1/api/iserver/marketdata/history` | 日线/盘中 OHLCV、财报窗口价格反应 | 固定 bar、交易时段、调整口径；不使用已废弃 HMDS endpoint |
| `ibkr.option_surface` | 内部编排 secdef 三步 + 批量 snapshot | strike IV、delta/gamma/theta/vega、mark、OI、volume；计算 ATM IV、25-delta skew、term structure、事件 implied move | 公式进入 `method_id/version`；缺少 bid/ask、到期覆盖或订阅时不生成 |
| `ibkr.fed_funds_curve` | 搜索 ZQ/Fed Funds futures 合约 + snapshot/history | 按未来月份合约构建隐含平均政策利率曲线，并计算 12m target | 显式记录合约月份、价格转利率公式、跨月加权与会议日历版本 |

订阅与运行前提：funded PRO 账户、2FA/session、对应交易所行情权限；美股期权通常还需 OPRA 与标的行情。IBKR 已在 2026 年停止部分 fundamentals 字段，不应把它作为 P/E 或公司基本资料主源。

### 5.3 Benzinga：5 个

官方接口总览：[Benzinga API docs](https://docs.benzinga.com/llms.txt)，base URL 为 `https://api.benzinga.com`。

| Tool | Endpoint | 负责的数据 | 关键边界 |
|---|---|---|---|
| `benzinga.short_interest`（`UNAVAILABLE`，不注册） | `GET /api/v1/shortinterest` | total short interest、float short %、days to cover、float、ADV、当期/前期日期 | 当前 key 无产品权限；相关 targets 不路由到 tool，待 entitlement 补齐后再启用 |
| `benzinga.management_guidance` | `GET /api/v2.1/calendar/guidance` | EPS/revenue guidance 的 low/high、period、prior、类型 | source role 是管理层；不能当卖方共识 |
| `benzinga.transcripts`（`UNAVAILABLE`，不注册） | `GET /api/v1/transcripts/calls`；`GET /api/v1/transcripts/calls/{call_id}` | call 索引、状态、公司、时间、transcript 正文、speaker 和音频 metadata | 当前 key 无产品权限；保留 client 实现，不暴露给 Agent |
| `benzinga.analyst_events` | `GET /api/v1/consensus-ratings`；`GET /api/v2.1/calendar/ratings`；`GET /api/v2.1/calendar/earnings` | consensus rating/price target、个体评级变化、earnings actual/estimate/event | 作为 FMP 补充与交叉验证；保留 analyst count 与 updated_at |
| `benzinga.market_signals` | `GET /api/v1/signal/option_activity`；`GET /api/v1/signal/block_trade` | unusual options、sweep/block 等市场活动证据 | 不是完整期权链；不得产出 ATM IV/skew/事件 implied move |

### 5.4 Financial Modeling Prep（FMP）：4 个

官方接口：[FMP Stable API](https://site.financialmodelingprep.com/developer/docs/stable)。base URL：`https://financialmodelingprep.com/stable`。

| Tool | Endpoint | 负责的数据 | 关键边界 |
|---|---|---|---|
| `fmp.sell_side_estimates` | `/analyst-estimates?symbol=...&period=annual|quarter&page=...&limit=...`、`/ratings-snapshot`、`/ratings-historical`、`/price-target-summary`、`/price-target-consensus`、`/grades`、`/grades-historical` | revenue/EPS estimates、评级、目标价、覆盖数量与区间 | 按订阅实测 endpoint entitlement；未证明 point-in-time vintage 前不得用于历史远期估值回测 |
| `fmp.valuation_snapshot` | `/profile`、`/enterprise-values`、`/key-metrics-ttm`、`/ratios-ttm` | market cap、EV、TTM 指标、标准估值倍数 | 每个倍数保留 numerator/denominator date；不跨币种直接比较 |
| `fmp.peers_relative_valuation` | `/stock-peers?symbol=...` 或 `/peers-bulk`，再批量调用 valuation snapshot | 受治理的 peer set、横截面估值分布、目标公司相对百分位 | peer 先经过行业、业务、币种与规模规则；供应商 peers 不是最终治理目录 |
| `fmp.transcript_fallback`（`UNAVAILABLE`，不注册） | `/earning-call-transcript-dates?symbol=...`、`/earning-call-transcript?symbol=...&year=...&quarter=...` | Benzinga/IR 不可用时的 transcript 降级 | 当前套餐无权限；保留 client 实现，不暴露给 Agent |

### 5.5 Twelve Data：2 个

官方接口：[API docs](https://twelvedata.com/docs)、[estimates/recommendations endpoints announcement](https://twelvedata.com/blog/twelve-data-unveils-analysis-data-with-estimations-and-recommendations)。

| Tool | Endpoint | 负责的数据 | 处置 |
|---|---|---|---|
| `twelvedata.daily_ohlcv` | `/time_series` | 日线 OHLCV 与价格反应 | 保留现有实现；补 adjustment、calendar、as-of 契约测试 |
| `twelvedata.sell_side_estimates` | `/earnings_estimate`、`/revenue_estimate`；必要时 EPS trend/revision endpoints | FMP 接入前的标准 EPS/revenue consensus 临时来源 | `/earnings` 只用于历史 actual/estimate，不替代 forward estimate；FMP 稳定后降为 fallback |

### 5.6 Finnhub：3 个

官方接口：[Finnhub API docs](https://finnhub.io/docs/api)。

| Tool | Endpoint | 负责的数据 | 处置 |
|---|---|---|---|
| `finnhub.company_peers` | `GET /stock/peers?symbol=...` | 候选 peers | 保留现有实现，仅作为 peer discovery |
| `finnhub.insider_transactions` | `GET /stock/insider-transactions?symbol=...&from=...&to=...` | 内部人交易主体、申报/交易日期、数量、价格与交易代码 | 已移除付费 fund ownership endpoint 与旧 composite tool；最多返回 50 条受控记录 |
| `finnhub.company_news_events` | `GET /company-news?symbol=...&from=...&to=...`、`GET /calendar/earnings?...`、必要时 `/stock/recommendation` | 公司事件发现、财报日历和辅助 consensus | 新闻只能作发现；数值以权威/主聚合源复核 |

现有 `finnhub.trade_stream` 保留给实时监控，不计入 Document 1 的默认基础采集工具。

### 5.7 FRED：4 个

官方接口：[series observations](https://fred.stlouisfed.org/docs/api/fred/series_observations.html)、[series search](https://fred.stlouisfed.org/docs/api/fred/series_search.html)、[vintage dates](https://fred.stlouisfed.org/docs/api/fred/series_vintagedates.html)。四个 tools 均调用 `/fred/series/observations`，差异在受治理的 series registry 和输出 schema。

| Tool | 必选/优先 series 示例 | 负责的数据 |
|---|---|---|
| `fred.activity_demand` | `GDPC1`、`PCECC96`、`RSAFS`、`INDPRO`、`TCU`、`DGORDER`、`HOUST`、`TOTALSA`、`UMCSENT` | 经济活动、消费、工业、产能利用、耐用品、地产、汽车、信心 |
| `fred.inflation_labor` | `UNRATE`、`PAYEMS`、`ICSA`、`JTSJOL`、`CPIAUCSL`、`CPILFESL`、`PCEPI`、`PCEPILFE`、`PPIACO`、`ECIALLCIV`、`T5YIE`、`T10YIE` | 劳动力、工资/成本、通胀与盈亏平衡通胀 |
| `fred.rates_credit_liquidity` | `DFF`、`DGS2`、`DGS10`、`DGS30`、`T10Y2Y`、`T10Y3M`、`DFII10`、`BAMLC0A0CM`、`BAMLH0A0HYM2`、`NFCI`、`M2SL`、`WALCL`、`RRPONTSYD` | 利率曲线、实际利率、信用利差、金融条件、流动性 |
| `fred.commodities_fx` | `DCOILWTICO`、`DCOILBRENTEU`、`DHHNGSP`、`PCOPPUSDM`、`PALUMUSDM`、`GOLDAMGBD228NLBM`、`DTWEXBGS`、`VIXCLS` | 大宗商品、美元与市场波动背景 |

不建议直接把“约 175 条 series”全部暴露给 Agent。应先通过 search/metadata 离线建立 registry，记录 `series_id`、业务别名、频率、单位、季调、发布方、可用起始日、vintage policy 与目标行业；只有 registry 中的 series 可被业务 tool 调用。当前白名单至少补入 `PCEPILFE` 与 `NFCI`。

### 5.8 BLS：3 个

官方接口：[BLS Public Data API v2](https://www.bls.gov/developers/api_signature_v2.htm)，统一使用 `POST https://api.bls.gov/publicAPI/v2/timeseries/data/`。

| Tool | 负责的数据 | Registry 要求 |
|---|---|---|
| `bls.labor_inflation` | CPI、就业、工资、生产率等 BLS 原始系列 | series id 映射到地区、行业、季调、单位和频率 |
| `bls.industry_producer_prices` | PPI final demand、commodity/industry PPI | 按 NAICS/commodity 建立行业模板，避免 Agent 猜 series id |
| `bls.import_export_prices` | import/export price indexes，含行业/商品维度 | 标明 locality、end use/industry 分类与 base period |

注册 key 后的请求上限与批量能力更高；tool 负责按官方限制分批、重试和合并，不把 key 暴露给 Agent。

### 5.9 BEA：2 个

官方接口：[BEA API](https://apps.bea.gov/api/signup/)，base 为 `https://apps.bea.gov/api/data`。

| Tool | Dataset / API mode | 负责的数据 |
|---|---|---|
| `bea.national_accounts` | `datasetname=NIPA`；metadata 使用 `GetDataset`、`GetParameterList`、`GetParameterValues` | GDP、PCE、收入、利润、投资等国家账户；替代并扩展当前 `bea.nipa_data` |
| `bea.industry_accounts` | `GDPByIndustry`、`InputOutput`、`UnderlyingGDPByIndustry`、必要时 `FixedAssets` | 行业增加值、产出、中间投入、价格/数量指标、资本存量与投资 |

### 5.10 Census M3：1 个

官方接口：[Manufacturers' Shipments, Inventories, and Orders](https://api.census.gov/data/timeseries/eits/m3.html)。

| Tool | Endpoint | 负责的数据 |
|---|---|---|
| `census.manufacturing_orders` | `GET https://api.census.gov/data/timeseries/eits/m3?...` | 制造业 shipments、inventories、new orders、unfilled orders，按类别/NAICS、月份和季调口径返回 |

### 5.11 EIA：2 个

官方接口：[EIA Open Data API v2](https://www.eia.gov/opendata/documentation.php)。API v2 是 route/facet 架构；实现时先用 route metadata 固化 series registry，再访问 `/v2/{route}/data/`。

| Tool | 主要 route family | 负责的数据 |
|---|---|---|
| `eia.energy_prices` | `/v2/petroleum/.../data/`、`/v2/natural-gas/.../data/`、`/v2/electricity/.../data/` | 原油、成品油、天然气、电力价格，带地区、单位、频率和 facet |
| `eia.energy_supply_operations` | petroleum supply、natural gas storage/production、`/v2/electricity/electric-power-operational-data/data/` | 产量、库存、进口、炼厂利用率、发电量、装机/燃料结构等 |

### 5.12 USAspending：2 个

官方接口：[USAspending API endpoints](https://api.usaspending.gov/docs/endpoints)。

| Tool | Endpoint | 负责的数据 |
|---|---|---|
| `usaspending.award_search` | `POST /api/v2/search/spending_by_award/`；计数 `/api/v2/search/spending_by_award_count/`；必要时 spending-by-category | 按 recipient、UEI、agency、NAICS、award type、时间范围查政府合同/资助，返回 award id、金额、期间、状态 |
| `usaspending.award_detail` | `GET /api/v2/awards/{generated_unique_award_id}/` 及官方 award 子资源 | 单项 award 详情、修改、资金、机构、地点与描述，供项目执行与订单证据使用 |

### 5.13 SAM.gov：1 个

官方接口：[SAM.gov Get Opportunities Public API](https://open.gsa.gov/api/get-opportunities-public-api/)。

| Tool | Endpoint | 负责的数据 |
|---|---|---|
| `sam.contract_opportunities` | `GET https://api.sam.gov/opportunities/v2/search` | solicitation、award notice、响应截止、agency、NAICS、place、notice id、status 与附件链接；按 UEI/name 与发行人做实体映射 |

该接口要求日期窗口，并返回 active/latest 版本语义；归档数据另按官方 archive 机制处理。SAM 机会不等于已经确认的公司收入或订单。

### 5.14 Regulations.gov：1 个

官方接口：[Regulations.gov API](https://open.gsa.gov/api/regulationsgov/)。

| Tool | Endpoint | 负责的数据 |
|---|---|---|
| `regulations.rulemaking_records` | `GET /v4/documents`、`/v4/ documents/{id}`、`/v4/dockets`、`/v4/dockets/{id}` | docket、proposed/final rule、notice、comment period、agency、发布日期、附件与对象实体命中 |

本系统只读，不暴露提交评论的写操作。

### 5.15 Federal Register：1 个

官方接口：[Federal Register API v1](https://www.federalregister.gov/developers/documentation/api/v1)。

| Tool | Endpoint | 负责的数据 |
|---|---|---|
| `federal_register.documents` | `GET https://www.federalregister.gov/api/v1/documents.json`、`GET /api/v1/documents/{document_number}.json` | rules、proposed rules、notices、presidential documents，按 agency、topic、CFR、date 检索并返回正式原文链接 |

FederalRegister.gov API 自身是便于检索的非正式版本；证据应保留 Federal Register PDF/GovInfo 等正式版本链接。

### 5.16 Congress.gov：1 个

官方接口：[Congress.gov API](https://api.congress.gov/)。

| Tool | Endpoint family | 负责的数据 |
|---|---|---|
| `congress.legislative_actions` | API v3 的 `/bill`、`/bill/{congress}/{type}/{number}/actions`、`/committee-report`、`/hearing` 等资源 | 法案状态、actions、委员会报告、听证与政策推进时间线；按行业/产品/监管对象匹配 |

### 5.17 openFDA：2 个

官方接口：[510(k)](https://open.fda.gov/apis/device/510k/how-to-use-the-endpoint/)、[PMA](https://open.fda.gov/apis/device/pma/how-to-use-the-endpoint/)、[Drugs@FDA](https://open.fda.gov/apis/drug/drugsfda/)。

| Tool | Endpoint | 负责的数据 |
|---|---|---|
| `openfda.approval_milestones` | `/device/510k.json`、`/device/pma.json`、`/drug/drugsfda.json` | 医械 clearance/PMA、补充批准、药品 application/product/submission 里程碑 |
| `openfda.safety_actions` | `/device/enforcement.json`、`/drug/enforcement.json`；按目标再启用相应 adverse-event endpoint | recall/enforcement、安全事件与状态变化 |

必须建立 issuer、manufacturer、brand、application number、product code 的 alias；开放 FDA 数据不能覆盖所有临床/监管事实，源端免责声明要透传。

### 5.18 公司 IR：2 个

公司 IR 没有统一 API，必须把“一次性发现”和“稳定采集”拆开。

| Tool | 数据入口 | 负责的数据 |
|---|---|---|
| `ir.official_feed_discovery` | 发行人官方域名 allowlist；HTML metadata、RSS/Atom、JSON-LD、investor calendar | 一次性发现并生成待审核的 issuer feed config；允许 AGENT 辅助，但不能直接写 State |
| `ir.official_updates` | 已批准 config 中的 RSS/Atom、press release、events、presentation 链接 | 确定性轮询官方公告、管理层材料和活动；返回文档定位与 source ref |

## 6. 派生指标的受治理计算

### 6.1 市场隐含指标

| 输出指标 | 输入 | 推荐方法 | 失败条件 |
|---|---|---|---|
| 30D ATM IV | IBKR 标的 30-day IV 字段，或目标期限附近 ATM option IV | 优先官方 30D 字段；否则在两侧到期上按方差时间插值 | 无双边报价、无足够期限、订阅缺失 |
| 25-delta skew | 同期限 put/call IV + delta | 固定定义如 `IV(25Δ put) - IV(25Δ call)`，记录 delta convention | 一侧不存在或报价质量不合格 |
| 事件 implied move | 跨事件最近合适到期的 ATM straddle mark / spot | 记录到期、事件时间、是否含其他重大事件；可输出百分比区间 | 无可靠事件时间、价差过宽或期限污染 |
| 12m Fed Funds implied rate | 月度 Fed Funds futures 价格、FOMC 日历、当前 EFFR | `100 - futures_price` 得隐含月均利率，再按目标日规则构建 12m 值 | 合约缺失、流动性不足、月份映射不完整 |

这些结果的 `source_role` 继承其权威输入角色；派生身份由 `methodology.method_id/version/input_refs` 表达，不创建一个含义模糊的“MODEL”来源角色。

### 6.2 估值指标

建议分三层，不混写：

1. **当前 trailing valuation**：当前价格/EV + TTM 财务，可立即闭合。
2. **当前 forward valuation**：当前价格/EV + 当前卖方 NTM/FY estimates，可在 FMP entitlement 验证后闭合。
3. **历史 point-in-time forward percentile**：要求每个历史观察日当时可见的 consensus vintage；在供应商证明支持并通过无未来数据测试前保持 unavailable。

横截面 peer percentile 可以先实现，但 peer universe、币种、财年错位、负分母和极端值处理必须版本化。

## 7. Collection Target、Manifest 与 Tools 暴露

### 7.1 Collection target 示例

```yaml
collection_target_id: o4_market_implied_atm_iv_30d
metric_id: market_implied_atm_iv_30d
requirement: OPTIONAL
source_role: MARKET_IMPLIED
time_scope: MARKET_SNAPSHOT_TIME
collection_mode: PROGRAM
tool_name: ibkr.option_surface
output_policy: STATE_VALUE
```

同一个 `metric_id` 可以对应多个 targets，例如管理层 guidance、卖方 consensus、市场隐含值分别采集；它们有各自的 `source_role`、`time_scope` 和成功状态。`time_scope` 是相对期间规则；涉及公司财期时，由程序根据目标公司的财务日历解析具体期间，不由 Agent 填写，也不得按自然季度写死。

### 7.2 Manifest 示例

```yaml
collection_target_id: o4_market_implied_atm_iv_30d
status: PARTIAL
output_refs: [...]
reason_codes: [...]
```

Manifest 以 `collection_target_id` 为粒度，只记录该 target 的采集状态与产物引用。允许状态为 `FILLED | PARTIAL | EMPTY | NOT_APPLICABLE | FAILED | UNAVAILABLE`。禁止用指标级单一 success/failure 覆盖多个 targets 的不同结果。

### 7.3 Tools 如何向 Agent 暴露

Collection Target、Manifest 和 Tools 权限不是同一回事：

* Collection Target 定义“本轮要采什么”；
* Manifest 记录“这个 target 最终采得怎么样”；
* Tools 暴露由 Agent 配置和 runtime tool allowlist 独立控制。

`tool_name` 只供系统路由 PROGRAM target，不授予 Agent 工具权限，也不把 tool 自动暴露给 Agent。PROGRAM target 由程序执行后把结果交给 Agent；只有 AGENT target 确实需要 Agent 调用工具时，runtime 才按该 Agent 的独立 allowlist 暴露对应 tool。Agent 只接收完成研究所需的指标语义、时间范围、采集结果和精简状态，不接收 provider fallback、endpoint、缓存或完整 Manifest 审计细节。


## 8. 实施优先级

### Wave 0：契约与安全（先做）

1. 落地 collection-target Manifest、统一 ToolResult、三时间字段和 quality flags。
2. 增加 `REALIZATION_FACTOR` ObjectRef，并落实第 10 节的契约修正。
4. 建立 ProviderCapabilityRegistry，区分 `documented`、`entitled`、`implemented`、`tested`、`production_ready`。

### Wave 1：Document 1 必选指标闭合

1. SEC 5-tool 拆分与旧工具兼容迁移。
2. FRED 4-tool + registry；补 core PCE、NFCI 等必选系列。
3. BLS 3-tool、BEA 2-tool、Census M3 1-tool。
4. IBKR contract/snapshot/history/option/fed-funds 5-tool。
5. FMP estimates/valuation/peers；Twelve Data estimate 作为临时 fallback。
6. Benzinga short interest、guidance、transcript。

### Wave 2：行业、合同与监管证据

1. EIA、openFDA。
2. USAspending、SAM.gov。
3. Regulations.gov、Federal Register、Congress.gov。
4. IR discovery + approved-feed polling。

### Wave 3：补充与治理

1. Finnhub ownership/insiders/events，Benzinga analyst/signals。
2. 扩充 IndustryMetricRegistry，不做无治理的 175-series 全开放。
3. 供应商成本、限流、缓存、失败率与 coverage dashboard。
4. 只有在验证 point-in-time consensus vintage 后，才启用历史 forward valuation percentile。

## 9. 验收标准

每个 tool 上线前至少通过：

1. **契约测试**：真实但脱敏的 provider fixture，覆盖正常、空结果、部分字段、401/403、429、5xx 和 schema drift。
2. **点时性测试**：确保查询截止日之后发布的数据不会进入结果；保存 provider publication timestamp。
3. **实体解析测试**：ticker/CIK/UEI/conid/company alias 的显式映射，不在 provider 内部隐式猜测。
4. **质量门槛测试**：单位、币种、季调、频率、财年、split adjustment、期权报价质量。
5. **部分成功测试**：Manifest 能准确表达同一 metric 的多个 collection targets 与单 target 内多个 items。
6. **权限测试**：Agent 只能看到任务白名单 tools；keys 不出现在 args、results、prompt、trace 或错误文本。
7. **派生值测试**：方法版本、输入 refs、计算结果可重放；期权与远期估值做边界样本。
8. **证据测试**：SEC/IR/监管文本可以回到 accession/document number/docket 与具体位置。
9. **商业可用性测试**：Benzinga、FMP、IBKR、Finnhub、Twelve Data 的目标 endpoint 都以实际订阅做 entitlement probe；未实测前状态只能是 documented，不能是 production-ready。

## 10. 已确认的 7 项契约修正

1. **`ObjectRef` 与 `REALIZATION_FACTOR`**：把 `REALIZATION_FACTOR` 加入 `ObjectRef` 可引用对象枚举。
2. **模型派生值与 `source_role`**：派生值使用其权威输入所对应的现有 `source_role`；派生方法、版本和输入 refs 记录在 methodology/provenance，不新增含义不清的来源枚举。若一个计算混合多个 source roles，collection target 必须事先声明唯一的权威角色和输入优先级，否则拒绝生成。
3. **空区间**：样例错误。无可靠 State Value 时不生成该 State，不以空区间占位。
4. **`event_family_ids`**：字段已从方案移除，不再建立其治理目录。
5. **统一 ObjectRef 仓库**：本轮不阻塞数据源接入，记录为后续统一开发项；在 resolver 落地前，provider-specific id 与规范化 id 必须同时保留，禁止伪装成已全局可解析。
6. **`metric_id` 与多 `source_role`**：`PROGRAM` / `AGENT` / `UNAVAILABLE` 是编排或实际可用性，不作为路由键。路由由 collection target、provider、tool 和 extractor/method 决定。
7. **Manifest 粒度**：Manifest 改为 collection-target 级，支持同一指标的多目标与部分成功。

## 11. 本轮仍未被数据源方案解决的事项

- 未公开的私有供应链、真实良率、客户级订单等数据依然不可得。
- 自定义经营 KPI 的标准化卖方共识按方案放弃。
- 历史 point-in-time forward consensus vintage 尚未找到被验证的候选接口；不能以当前 estimate 反推历史。
- ObjectRef 统一解析仓库已明确延期，当前只能做 provider id + canonical candidate 的过渡方案。
- 非美上市公司标准财务主源未在本轮完整设计；SEC 主线仅适用于 EDGAR 覆盖范围。
- API 文档能力不等于订阅已购买。所有付费来源需在实施阶段逐 endpoint 做 entitlement 与速率实测。

## 12. 配置与密钥命名建议

仅使用 secret 注入，不在文档、代码默认值或测试 fixture 中保存真实 key：

```text
IBKR_*
BENZINGA_API_KEY
FMP_API_KEY
TWELVEDATA_API_KEY
FINNHUB_API_KEY
FRED_API_KEY
BLS_API_KEY
BEA_API_KEY
EIA_API_KEY
DATA_GOV_API_KEY
SAM_GOV_API_KEY
```

USAspending 与 Federal Register 的公开接口即使不要求 key，也要纳入统一 client 的限流、缓存、User-Agent、重试和审计机制。
