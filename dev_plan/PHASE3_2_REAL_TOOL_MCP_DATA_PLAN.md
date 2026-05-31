# Phase 3.2 Real Tool / MCP Data Access Plan

## 0. Scope

本文件用于审阅 `3.2 真实 Tool / MCP 接入` 中 C1/C2/C3/O4 四类 agent 的真实数据接入边界。当前只做数据依赖、接口能力、free 层级限制和 tool/API 名单规划，不进行业务代码修改。

本计划遵守以下约束：

1. 同一类数据只绑定一个主 API，不做 fallback 逻辑。
2. `yfinance` 只在其他正式接口无法覆盖时使用，且必须限定用途。
3. 所有外部接口按当前 free/basic/development 层级设计调用量、缓存和降级语义。
4. Tool 返回结果必须能转成 `EvidenceRef`，并保留 `source_refs`、retrieval metadata、confidence、unknowns。
5. DoxAtlas tool server 已在 `dev_plan/DOXATLAS_TOOL_SCHEMAS.md` 单独规划；本文只补充 C1/C2/C3/O4 的外部数据工具。

## 1. Agent 数据依赖

### C1 Fundamental Research

当前架构来源：

- `FundamentalBriefAgentModule` 包装 Vibe-Trading `fundamental_research_team`。
- 子任务为 financial analysis、valuation、quality/moat、report editor。
- 输出字段包含 `financial_analysis`、`valuation`、`quality`、`investment_rating`、`thesis`、`risks`、`catalysts`。

需要的数据：

| 数据域 | 需要字段 | 用途 |
| --- | --- | --- |
| 公司基础画像 | name, exchange, currency, country, sector, industry, description | 标的识别、业务概览、quality/moat 上下文 |
| 市值与估值指标 | market cap, PE, forward PE, PB, PS, EV/Revenue, EV/EBITDA, beta, dividend yield, shares outstanding | valuation、相对估值、风险提示 |
| 三大表 | income statement, balance sheet, cash flow, annual/quarterly | financial health、earnings quality、FCF、债务风险 |
| SEC 结构化事实 | US-GAAP/IFRS XBRL facts, filing accessions, fiscal periods | 可审计财务事实、与 normalized statement 交叉解释 |
| SEC filing text / section | 10-K/10-Q/8-K primary document URL, complete submission text, selected Item/section text | risk factors、MD&A、business overview、管理层叙事证据 |
| 盈利与预期 | EPS history, surprise, analyst EPS/revenue estimates, earnings calendar | catalysts、预期兑现窗口 |
| 公司公告 | press releases | 非财报催化剂、管理层/产品/并购事件 |
| HK/basic quote 缺口 | HK 股票基础 ratios、market cap、dividend yield | 仅当 US/SEC/Alpha Vantage 不能覆盖时使用 |

### C2 Macro / Market Research

当前架构来源：

- `MacroContextAgentModule` 包装 Vibe-Trading `macro_rates_fx_desk`。
- 子任务为 rates analyst、FX strategist、commodity/inflation analyst、macro PM。
- 输出字段包含 `rates`、`fx`、`commodity_inflation`、`macro_allocation`、`risk_scenarios`、`monitoring_dashboard`。

需要的数据：

| 数据域 | 需要字段 | 用途 |
| --- | --- | --- |
| 利率与曲线 | Fed funds, SOFR, 2Y/10Y/30Y Treasury, 2s10s, 3m10y | rate regime、curve signal、duration risk |
| 实际利率与通胀预期 | TIPS real yield, breakeven inflation, 5y5y inflation expectation | discount rate、gold/growth sensitivity |
| 信用与波动 | IG/HY spread, VIX, financial conditions | risk appetite、credit stress、bear scenario |
| CPI/PPI/就业/工资 | CPI, core CPI, PPI, unemployment, payrolls, participation, hourly earnings | inflation/labor regime |
| GDP/PCE/收入/利润 | GDP, PCE, personal income, corporate profits | growth/income/profit cycle |
| Fed 官方日程与 FOMC 材料 | meeting dates, statement, minutes, implementation note, SEP/projection materials, press conference links | policy event calendar、央行措辞、政策路径证据 |
| 市场隐含政策概率 | rate-cut/hike probability markets | market-implied policy path |
| 商品价格/指数 | WTI, Brent, natural gas, copper, broad commodity index, optional food/agriculture indexes | commodity/inflation analyst、成本压力、通胀传导 |
| 大盘价格代理 | SPY/QQQ daily OHLCV, volume, 30d return/volatility | equity risk context 与大盘反应 |

### C3 Industry Research

当前架构来源：

- `IndustryResearchAgentModule` 包装 financial-services Market Researcher。
- 子任务为 scope、sector overview、competitive analysis、comps analysis、idea generation、note synthesis。
- 输出字段包含 `industry_overview`、`competitive_landscape`、`peer_comps`、`idea_shortlist`、`risks`、`catalysts`、`downstream_hints`。

需要的数据：

| 数据域 | 需要字段 | 用途 |
| --- | --- | --- |
| Peer universe | same country/sector/industry peers | comps、竞争格局、shortlist |
| 公司 filings/profile | SEC submissions, companyfacts, SIC, ticker/exchange metadata | 竞争格局、业务/财务事实 |
| 行业/sector 市场表现 | sector performance snapshot | 行业相对热度、why-now |
| 外部网页证据 | 行业报告、公司官网、IR、政策、监管、新闻网页 | market size、drivers、risks、政策证据 |
| URL 原文提取 | selected source raw content / markdown | 可引用证据与 source capsule |

### O4 Market Trace

当前架构来源：

- `MarketTraceAgentModule` 是 native DoxAgent O4。
- provider protocol 已有 `get_quote(symbol)`、`get_historical(symbol, period, interval)`、`get_multiple_quotes(symbols)`。
- 输出字段包含 `quote_context`、`ohlcv_summary`、`relative_performance`、`volume_analysis`、`technical_signals`、`valuation_context`、`data_quality`、`source_refs`、`unknowns`。

需要的数据：

| 数据域 | 需要字段 | 用途 |
| --- | --- | --- |
| 近 30d 日线 | open, high, low, close, volume, adjusted close | price reaction、technical signals |
| benchmark/peer 日线 | SPY/QQQ/peer 30d OHLCV | relative performance |
| 实时/准实时交易流 | trade price, volume, timestamp, symbol | monitoring 阶段 tick/trade stream |
| 基础 quote context | current/previous close, volume, 52w range, market cap, PE | price context 与 valuation context |

## 2. Interface Capability Notes

### FRED API

官方文档确认 FRED API Version 1/2 支持按 series/release/category/source/tag 检索经济数据，`fred/series/observations` 用于按 `series_id` 获取时间序列观测值。所有 web service 请求需要 API key。

可用能力：

- `fred/series/observations`: 获取单个 macro/financial series 的历史观测值。
- `fred/series`: 获取 series metadata。
- `fred/series/search`: 搜索经济指标 series。
- `fred/release/*`: release-level 数据发现与更新日历。

free 层级/限制：

- FRED 文档要求 API key；官方文档未在当前页面明确 daily quota。
- 实现侧应设置保守 throttle，例如不超过 100 requests/min，并缓存 series metadata。

本项目用途：

- C2 使用 FRED 作为 rates、credit、VIX、financial conditions、breakeven、TIPS 主数据源。
- C2 同时使用 FRED 作为 commodity/inflation analyst 的商品价格主数据源，优先覆盖能源、工业金属与综合商品指数。
- 不用 FRED 拉 CPI/PCE/GDP，避免与 BLS/BEA 重复。

候选 series manifest：

- Rates: `DFF`, `FEDFUNDS`, `DGS2`, `DGS10`, `DGS30`, `T10Y2Y`, `T10Y3M`
- Real rates / breakeven: `DFII5`, `DFII10`, `T5YIE`, `T10YIE`, `T5YIFR`
- Credit / volatility / conditions: `BAMLC0A0CM`, `BAMLH0A0HYM2`, `VIXCLS`, `NFCI`, `ANFCI`
- Commodities: `DCOILWTICO` WTI crude, `DCOILBRENTEU` Brent crude, `PNGASUSUSDM` US Henry Hub natural gas, `PCOPPUSDM` copper, `PALLFNFINDEXM` broad all-commodities index. Gold/silver spot series should not be assumed available in FRED because LBMA/precious-metals benchmark data has licensing constraints; if needed later, decide a separate licensed source instead of silently using a proxy.

### BLS API

官方文档确认 BLS API v2 支持 single series、multiple series、one or more series with optional parameters、latest series、popular series、surveys。v2 注册用户可一次请求最多 50 个 series、最多 20 年，并可请求 calculations、annual average、catalog、aspects。

free/registered 层级限制：

- v2 需要 registration key。
- 500 queries/day。
- 50 requests / 10 seconds。
- 50 series/query。
- 20 years/query。

本项目用途：

- C2 使用 BLS 作为 CPI/PPI/labor/wage 主数据源。
- 不用 FRED 重复拉 CPI、unemployment 等指标。

候选 series manifest：

- CPI: `CUSR0000SA0`, `CUSR0000SA0L1E`
- Labor: `LNS14000000`, `CES0000000001`, `LNS11300000`
- Wages: `CES0500000003`
- PPI: 初始实现建立可配置 series manifest，先人工确认 final demand 与行业 PPI series ID，避免硬编码错误。

### BEA API

官方 user guide 确认 BEA API 支持 `GetDatasetList`、`GetParameterList`、`GetParameterValues`、`GetParameterValuesFiltered`、`GetData` 等调用。BEA 限制包括 100 requests/min、100 MB/min、30 errors/min，超限返回 429 和 `RETRY-AFTER`。

可用能力：

- `GetDatasetList`: 获取数据集。
- `GetParameterList`: 获取 dataset 参数。
- `GetParameterValues` / `Filtered`: 获取 TableName、LineNumber、Frequency 等合法取值。
- `GetData`: 拉取 NIPA/GDPbyIndustry 等数据。

本项目用途：

- C2 使用 BEA 作为 GDP、PCE、personal income、corporate profits 主数据源。
- 初始实现只接 `DatasetName=NIPA`，通过 metadata 方法生成并缓存 `TableName + LineNumber` manifest；不要先硬编码完整 BEA 表。

### Federal Reserve Official FOMC Materials

Federal Reserve 官方 FOMC 页面提供 meeting calendars, statements, minutes，并明确 FOMC 通常每年举行 8 次定期会议；minutes 一般在 policy decision 后三周发布。页面按年份列出 meeting dates、Statement PDF/HTML、Implementation Note、Press Conference、Projection Materials、Minutes PDF/HTML。Fed RSS 页面提供官方 RSS/Atom 入口，但当前没有发现稳定的、专门面向 FOMC calendar/materials 的 JSON API。

可用能力：

- FOMC calendar HTML: 解析 `fomccalendars.htm` 中的 meeting dates、SEP 标记、statement/minutes/projection/press conference links。
- FOMC materials HTML/PDF: 拉取并转换已选中的 statement、minutes、implementation note、projection materials。
- Federal Reserve RSS feeds: 监控 Press Releases / Monetary Policy 相关更新，用作增量发现；不作为历史主数据。

本项目用途：

- C2 使用 Fed 官方页面作为 policy event calendar 与 FOMC 原文材料主数据源。
- Polymarket 仍只表达 prediction-market-implied probability，不承担官方日程或官方措辞数据。

限制：

- 这是官方网页抓取/解析，不是正式 JSON API；需要 HTML 结构漂移测试与快照 fixture。
- PDF 只在必要时解析；优先使用 HTML 链接，避免引入复杂 PDF 表格/布局问题。

### Polymarket API

官方文档将 API 分为 Gamma、Data、CLOB 三类。Gamma API 用于 markets/events/tags/search 等发现，Data API 用于 positions/trades/activity/open interest，CLOB API 用于 orderbook/pricing/midpoint/spread/price history；Gamma 与 Data 公开无需认证，CLOB 的 public pricing/orderbook 端点可读，交易端点需要认证。

本项目用途：

- C2 只读市场隐含政策概率。
- 用 Gamma search/list events 找到 Fed/rate-cut 相关市场，再用 CLOB public price/midpoint/last trade price 读取概率。
- 不接任何 orders/trading endpoint。

限制：

- Polymarket 是预测市场，问题定义和流动性会变化；tool result 必须保留 market id、slug、outcome token、liquidity、last_updated，并把低流动性结果标记为低 confidence。

### Alpha Vantage

官方文档确认 Alpha Vantage 提供 stock time series、fundamental data、earnings、calendar 等 API，并提供官方 MCP server。support/pricing 页面确认 free tier 覆盖大多数数据集但只有 25 API requests/day，real-time 和 15-minute delayed US market data 为 premium-only。

可用能力：

- `TIME_SERIES_DAILY`: daily OHLCV；free 可用 `outputsize=compact`，返回最新 100 个交易日，足够近 30d。
- `OVERVIEW`: company information、financial ratios、key metrics。
- `INCOME_STATEMENT`, `BALANCE_SHEET`, `CASH_FLOW`: annual/quarterly normalized statements。
- `SHARES_OUTSTANDING`: quarterly basic/diluted shares outstanding。
- `EARNINGS`: EPS history、surprise metrics。
- `EARNINGS_ESTIMATES`: EPS/revenue estimates、analyst count、revision history。
- `EARNINGS_CALENDAR`: future earnings schedule，CSV 输出。

本项目用途：

- C1 使用 Alpha Vantage 作为 normalized company overview、三大表、shares outstanding、earnings history/estimates/calendar 主数据源。
- C2/O4 使用 Alpha Vantage `TIME_SERIES_DAILY` 作为 SPY/QQQ/target/peer 近 30d 日线主数据源。
- 不使用 `GLOBAL_QUOTE` 作为 O4 主行情，避免和 Finnhub websocket/Alpha daily 重复。

限制：

- 25 requests/day 非常紧，必须做 per-symbol/day cache。
- 实时/15 分钟延迟 US market data 不在 free 层级中；O4 的实时流使用 Finnhub，不用 Alpha Vantage 承担。

### yfinance

官方 GitHub README 说明 yfinance 是 Yahoo Finance 公共 API 的 Python 封装，提供 `Ticker`、`Tickers`、`download`、`Market`、`WebSocket`、`Search`、`Sector`、`Industry` 等组件。README 同时说明它不由 Yahoo 官方背书，数据使用应遵守 Yahoo terms，主要用于 research/educational purposes。

本项目用途：

- 只作为 C1 的 HK/basic snapshot 缺口工具：当 market 为 HK，且 SEC 不适用、Alpha Vantage 无法稳定覆盖基本 ratios 时，读取 PE/PB/ROE/market cap/dividend yield 等基础字段。
- 不用于 US 标的的主流程，不用于 O4 OHLCV，不作为 fallback。

限制：

- 非官方、无 SLA、terms 风险高；tool metadata 必须标记 `unofficial_source=true` 和较低 confidence。

### SEC EDGAR APIs

官方文档确认 `data.sec.gov` REST APIs 提供 submissions history 与 XBRL data，且不需要认证或 API key。当前包括 company submissions history、companyconcept、companyfacts、frames。SEC 另有 fair access 要求：当前最大 10 requests/second，并要求声明 User-Agent。

可用能力：

- `data.sec.gov/submissions/CIK##########.json`: 公司 filing history、name、former names、exchanges、tickers、recent filings。
- `data.sec.gov/api/xbrl/companyfacts/CIK##########.json`: 单公司所有 company concepts/facts。
- `data.sec.gov/api/xbrl/companyconcept/CIK##########/{taxonomy}/{tag}.json`: 单概念 facts。
- `data.sec.gov/api/xbrl/frames/{taxonomy}/{tag}/{unit}/{period}.json`: cross-entity frame data。
- EDGAR Archives filing documents: 由 submissions 里的 accession number 和 primary document 拼出 `https://www.sec.gov/Archives/edgar/data/{cik}/{accession_no_no_dashes}/{primaryDocument}`，获取 10-K/10-Q/8-K HTML/TXT 原文。
- Complete submission text file: 同一 archive 目录下的 `{accession_no_no_dashes}.txt` 可作为 filing 原文兜底读取对象，用于 section extraction。
- bulk zip: `companyfacts.zip`、`submissions.zip`，适合后续批处理，不作为本轮首选。

本项目用途：

- C1/C3 共用一个 SEC tool：US 公司 filings 与 XBRL facts 主证据源。
- C1 用于财务事实审计、filing history、fiscal periods。
- C1 增加 filing text/section extraction：只针对已确定 accession 的 10-K/10-Q/8-K 拉取原文，并本地解析 `Item 1`、`Item 1A`、`Item 2`、`Item 7`、`Item 7A`、`Item 8`、`Item 9A` 等白名单 section。
- C3 用于 company profile、SIC/industry metadata、竞争格局中的 issuer facts。

限制：

- ticker -> CIK 映射需要独立 manifest/cache，可由 SEC submissions/ticker metadata 建立。
- 必须设置 User-Agent，例如 `DoxAgent/0.1 contact@example.com`。
- section extraction 不是 SEC API 的结构化字段，属于本地解析能力；必须保留原始 filing URL、accession、form、filed date、section heading match，并在解析失败时返回 `unknowns`，不能编造缺失 section。

### Financial Modeling Prep

FMP Press Releases 文档确认 Press Releases API 用于公司官方公告、earnings reports、product launches、M&A 等，示例 endpoint 为 `/stable/news/press-releases-latest?page=0&limit=20`，并有 Search Press Releases 相关 API。pricing 页面显示 Basic/free plan 为 250 calls/day、EOD historical data、profile/reference data、150+ endpoints，且 trailing 30 days bandwidth limit 为 500 MB；很多 financial/fundamental endpoint 在 Basic 下存在 symbol 或覆盖限制。

本项目用途：

- C1 使用 FMP Press Releases 作为公司公告/催化剂主数据源。
- C3 使用 FMP sector performance snapshot 作为 sector/market performance 主数据源。
- 不使用 FMP financial statements/ratios，避免与 SEC/Alpha Vantage 重复，且 free/basic 覆盖存在限制。

限制：

- 250 calls/day，需要按 ticker/date cache。
- 文档对具体 endpoint 的 free/basic 可用性不如 pricing matrix 直观；实现前要用 key 做一次 endpoint entitlement smoke test。

### Finnhub

官方 docs 页面为动态页面，静态抓取内容有限；搜索索引中的官方文档摘要确认：

- 所有 GET 请求需要 `token` query 参数或 `X-Finnhub-Token` header。
- `/stock/peers?symbol=AAPL&grouping=industry` 获取同国家、sector/industry/subIndustry peers。
- WebSocket 使用 `wss://ws.finnhub.io?token=...`，通过 `{"type":"subscribe","symbol":"AAPL"}` 订阅 trade stream。
- Finnhub 首页说明提供 RESTful APIs 和 WebSocket，覆盖 stocks、currencies、crypto，且提供 free API key。

本项目用途：

- C3 使用 Finnhub `/stock/peers` 作为 peer universe 主数据源。
- O4 使用 Finnhub WebSocket trades 作为实时 trade stream 主数据源。
- 不使用 Finnhub quote/financial statements，以避免和 Alpha Vantage/SEC 重复。

限制：

- 当前静态文档未能可靠读取 free plan 的 symbol/connection 限额；实现前必须做 key-level smoke test，并在 config 中预留 `max_symbols_per_ws` 和 `one_connection_per_key` 约束。

### Tavily

官方文档确认 Tavily 提供 Search、Extract、Crawl、Map、Research 等 API。Search 支持 `search_depth`、`topic=general/news/finance`、`time_range/start_date/end_date`、include/exclude domains、raw content、answer、max results 等参数。Extract 支持对指定 URL 抽取 markdown/text/raw_content，可按 query rerank chunks。

free/development 限制：

- Researcher/free: 1,000 credits/month。
- Development key default: 100 RPM；Production key 1,000 RPM。
- Search basic/fast/ultra-fast: 1 credit/request；advanced: 2 credits/request。
- Extract basic: 每 5 个成功 URL 1 credit；advanced: 每 5 个成功 URL 2 credits。

本项目用途：

- C3 使用 Tavily Search 找行业资料、公司官网、IR、报告、政策、网页证据。
- C3 使用 Tavily Extract 抽取已选 URL 的可引用正文。
- 不使用 Tavily Crawl/Research，避免 free credits 消耗过快。

## 3. Data-to-API Assignment

### No Duplicate Rule

| 数据类型 | 主 API | 不再调用的重复源 |
| --- | --- | --- |
| US filings / XBRL facts | SEC EDGAR | Alpha Vantage statements 仅用于 normalized statement，不作为 filing fact；FMP/yfinance 不取 |
| SEC filing text / sections | SEC EDGAR Archives + local parser | Tavily/general web search 不抓 SEC 正文；Alpha/FMP 不替代 filing 原文 |
| Normalized statements | Alpha Vantage `INCOME_STATEMENT/BALANCE_SHEET/CASH_FLOW` | FMP financial statements、yfinance financials |
| Company overview ratios | Alpha Vantage `OVERVIEW` | yfinance for US、FMP ratios |
| Shares outstanding | Alpha Vantage `SHARES_OUTSTANDING` | SEC/yfinance 同字段不重复取 |
| Earnings history/estimates/calendar | Alpha Vantage `EARNINGS/EARNINGS_ESTIMATES/EARNINGS_CALENDAR` | FMP calendar、yfinance calendar |
| Company press releases | FMP Press Releases | Tavily news search 仅用于网页证据，不替代 press releases |
| Rates/credit/VIX/financial conditions | FRED | Alpha Vantage economic indicators、BEA/BLS 不重复 |
| CPI/PPI/jobs/wages | BLS | FRED 同类 series 不取 |
| GDP/PCE/income/profits | BEA | FRED 同类 series 不取 |
| Fed official calendar / FOMC materials | Federal Reserve official FOMC pages/RSS | Polymarket 不替代官方日程；Tavily 仅发现外部解读，不作为官方材料源 |
| Policy market probability | Polymarket | Tavily/search 只可发现页面，不做概率源 |
| Commodity prices/indexes | FRED commodity series | Alpha/yfinance/FMP commodity proxies 不取；黄金现货暂不接非授权替代源 |
| Daily OHLCV | Alpha Vantage `TIME_SERIES_DAILY` | yfinance/YahooChart/Finnhub candle 不取 |
| Real-time trades | Finnhub WebSocket | Alpha realtime/delayed、yfinance websocket 不取 |
| Peer universe | Finnhub `/stock/peers` | FMP peer comparison、manual web search |
| Sector performance | FMP sector performance snapshot | Alpha sector ETF proxy、Tavily search |
| Web evidence discovery/extraction | Tavily Search/Extract | General scraping/browser automation |
| HK basic ratios gap | yfinance HK-only | SEC/Alpha unavailable path only，不用于 US |

## 4. Proposed Tool/API List

### C1 Tools

| Tool name | Underlying API | Required inputs | Output summary | Notes |
| --- | --- | --- | --- | --- |
| `sec.company_facts_and_filings` | SEC `submissions`, `companyfacts` | `ticker` or `cik`, `forms`, `concepts` | filing history, accession refs, XBRL facts, fiscal periods | US only; shared by C1/C3 |
| `sec.filing_sections` | SEC EDGAR Archives document fetch + local section parser | `cik/accession`, `form`, `sections` | selected filing text sections with source offsets/headings | US only; no LLM-only extraction |
| `alpha.company_overview` | Alpha Vantage `OVERVIEW` | `symbol` | company profile, sector/industry, market cap, PE/PB/ROE, dividend, valuation ratios | cache daily |
| `alpha.financial_statements` | Alpha Vantage `INCOME_STATEMENT`, `BALANCE_SHEET`, `CASH_FLOW` | `symbol`, `period=annual|quarterly`, `limit` | normalized statements | one logical tool, three statement APIs |
| `alpha.shares_outstanding` | Alpha Vantage `SHARES_OUTSTANDING` | `symbol` | quarterly diluted/basic shares | separate because call is costly and optional |
| `alpha.earnings_events` | Alpha Vantage `EARNINGS`, `EARNINGS_ESTIMATES`, `EARNINGS_CALENDAR` | `symbol`, `horizon` | EPS history, surprise, estimates, upcoming earnings | use only when C1 needs catalyst window |
| `fmp.press_releases` | FMP Press Releases / Search Press Releases | `symbol`, `limit`, `from/to` | company official announcements | entitlement smoke test required |
| `yfinance.hk_basic_snapshot` | yfinance `Ticker.info/fast_info` | `symbol`, `market=HK` | HK basic ratios and market cap | no fallback; HK-only gap tool |

### C2 Tools

| Tool name | Underlying API | Required inputs | Output summary | Notes |
| --- | --- | --- | --- | --- |
| `fred.series_observations` | FRED `fred/series/observations` | `series_ids`, `start/end`, `units`, `frequency` | rates, curve, real yields, breakeven, credit, VIX, FCI, commodity observations | batch orchestration over single-series endpoint |
| `bls.timeseries` | BLS v2 `/timeseries/data` | `series_ids`, `startyear/endyear`, `calculations` | CPI/PPI/labor/wage series | use v2 key; 50 series/query |
| `bea.nipa_data` | BEA `GetData` plus metadata methods | `dataset=NIPA`, `table/line/frequency/year` | GDP/PCE/income/profits | first build table-line manifest |
| `fed.fomc_calendar_materials` | Federal Reserve FOMC official pages/RSS | `year`, `material_types`, `meeting_date` | FOMC dates, statements, minutes, SEP/projection links/text | official web parser, not a JSON API |
| `polymarket.market_probability` | Gamma search/list + CLOB public price/midpoint | `query`, `market_slug/id`, `outcome` | market-implied probability | read-only, no order endpoints |
| `alpha.daily_ohlcv` | Alpha Vantage `TIME_SERIES_DAILY` | `symbol`, `outputsize=compact` | latest 100 daily OHLCV bars | used for SPY/QQQ and O4 |

### C3 Tools

| Tool name | Underlying API | Required inputs | Output summary | Notes |
| --- | --- | --- | --- | --- |
| `finnhub.company_peers` | Finnhub `/stock/peers` | `symbol`, `grouping=sector|industry|subIndustry` | peer ticker list | primary peer universe |
| `sec.company_facts_and_filings` | SEC `submissions`, `companyfacts` | `ticker/cik`, `forms`, `concepts` | issuer facts/profile/filings | same shared SEC tool as C1 |
| `fmp.sector_performance` | FMP sector performance snapshot | optional `exchange/date` | sector performance snapshot | entitlement smoke test required |
| `tavily.search` | Tavily Search | `query`, `topic=finance|news|general`, domains, time range | candidate sources with title/url/content/score | basic depth by default |
| `tavily.extract` | Tavily Extract | `urls`, `query`, `format=markdown|text` | selected source body chunks | only for accepted source URLs |

### O4 Tools

| Tool name | Underlying API | Required inputs | Output summary | Notes |
| --- | --- | --- | --- | --- |
| `alpha.daily_ohlcv` | Alpha Vantage `TIME_SERIES_DAILY` | `symbol`, `outputsize=compact` | 30d/100d OHLCV | primary historical/daily market data |
| `finnhub.trade_stream` | Finnhub WebSocket trades | `symbols`, `duration/window`, `max_events` | trade ticks with price/volume/timestamp | monitoring/runtime tool; bounded capture |

## 5. Implementation Plan

### Step 1: Contracts and config only

1. Add provider config schema for API keys, base URLs, timeouts, cache TTLs, and per-provider rate limits.
2. Add a `ToolSourceRef`/retrieval metadata convention if current `EvidenceRef` metadata is insufficient.
3. Add static manifests:
   - FRED series IDs for C2 financial macro indicators.
   - FRED commodity series IDs for oil, gas, copper, and broad commodity indexes.
   - BLS series IDs for CPI/labor/wage/PPI.
   - BEA NIPA table-line manifest, generated from metadata and reviewed before use.
   - SEC filing section whitelist and form-specific parsing rules.
   - Federal Reserve FOMC material types and URL patterns.
   - Polymarket market search queries for Fed/rate-cut themes.

### Step 2: Shared HTTP client layer

1. Implement normalized REST error model: auth, entitlement, quota, rate limit, not found, schema drift, upstream unavailable.
2. Add provider-level retry respecting `Retry-After` for BEA/Tavily/HTTP 429.
3. Add response caching:
   - SEC submissions/companyfacts: 6-24h depending endpoint.
   - SEC filing documents/sections: cache by accession and primary document URL.
   - Fed FOMC calendar/materials: cache by year/material URL; refresh current-year calendar daily.
   - Alpha daily/fundamental: 24h for fundamentals, same-day for OHLCV.
   - FRED/BLS/BEA macro: 24h or release-aware.
   - FMP press/sector: 1-6h.
   - Tavily: by query hash and URL hash.

### Step 3: Implement low-risk read-only tools

1. `sec.company_facts_and_filings`
2. `sec.filing_sections`
3. `fred.series_observations`
4. `bls.timeseries`
5. `bea.nipa_data`
6. `fed.fomc_calendar_materials`
7. `alpha.daily_ohlcv`

These are deterministic read-only data tools and should be implemented before WebSocket/Tavily because they are easier to test with recorded fixtures.

### Step 4: Implement equity/fundamental tools

1. `alpha.company_overview`
2. `alpha.financial_statements`
3. `alpha.shares_outstanding`
4. `alpha.earnings_events`
5. `fmp.press_releases`
6. `yfinance.hk_basic_snapshot`

Before enabling FMP/yfinance in agent permissions, run entitlement/terms smoke tests and mark confidence accordingly.

### Step 5: Implement industry/search tools

1. `finnhub.company_peers`
2. `fmp.sector_performance`
3. `tavily.search`
4. `tavily.extract`

Tavily should be constrained by max results, domain allow/deny lists, and credit budget per run.

### Step 6: Implement O4 streaming tool

1. `finnhub.trade_stream`
2. Add bounded capture window rather than open-ended stream.
3. Persist stream summary, not every tick, unless monitoring pipeline later needs raw tick storage.

### Step 7: Wire tools into agent permissions

Replace mock tool names gradually:

| Agent | Allowed real tools |
| --- | --- |
| C1 | `sec.company_facts_and_filings`, `sec.filing_sections`, `alpha.company_overview`, `alpha.financial_statements`, `alpha.shares_outstanding`, `alpha.earnings_events`, `fmp.press_releases`, `yfinance.hk_basic_snapshot` |
| C2 | `fred.series_observations`, `bls.timeseries`, `bea.nipa_data`, `fed.fomc_calendar_materials`, `polymarket.market_probability`, `alpha.daily_ohlcv` |
| C3 | `finnhub.company_peers`, `sec.company_facts_and_filings`, `fmp.sector_performance`, `tavily.search`, `tavily.extract` |
| O4 | `alpha.daily_ohlcv`, `finnhub.trade_stream` |

## 6. Test and Acceptance Plan

Unit tests:

- Each tool parses a recorded fixture into normalized `ToolResult`.
- Each tool emits at least one `EvidenceRef` or explicit `unknowns`.
- `sec.filing_sections` fixture verifies 10-K/10-Q item heading extraction and returns source offsets/heading matches.
- `fed.fomc_calendar_materials` fixture verifies current-year calendar parsing and one historical statement/minutes link.
- Each provider maps 401/403/429/5xx/schema drift to normalized `ToolError`.
- `ToolRegistry` permission tests confirm agents cannot call unassigned providers.

Contract tests:

- C1 output can be produced from SEC facts + SEC filing sections + Alpha + FMP fixtures without yfinance for US ticker.
- C2 output can be produced from FRED macro + FRED commodity + BLS + BEA + Fed FOMC + Polymarket + Alpha fixtures.
- C3 output can be produced from Finnhub + SEC + FMP + Tavily fixtures.
- O4 output can be produced from Alpha daily OHLCV and bounded Finnhub trade stream fixture.

Smoke tests with real free keys:

- One low-volume ticker, e.g. `AAPL`, verifies SEC/Alpha/FMP/Finnhub.
- One SEC filing document fetch verifies archive URL construction and section parser behavior.
- One macro batch verifies FRED/BLS/BEA plus commodity series.
- One Fed FOMC calendar/material fetch verifies official page parsing.
- One Polymarket query verifies discovery + read-only price.
- One Tavily basic search + one extract verifies credits and output shape.

Acceptance criteria:

1. No tool writes Blackboard state directly.
2. No duplicate provider is called for the same data type in one run.
3. Free-tier quota budgets are visible in config and enforced.
4. Every stable field entering Working Memory has evidence or is explicitly marked unknown.
5. `yfinance.hk_basic_snapshot` is not called for US tickers.

## 7. Known Risks / Decisions Needed

1. Alpha Vantage free quota is only 25 requests/day. A full C1 run can consume many calls if overview/statements/earnings/shares are all requested. Recommendation: cache heavily and make C1 data depth configurable.
2. FMP Basic/free has 250 calls/day and endpoint coverage restrictions. Recommendation: use only press releases and sector performance after entitlement smoke test.
3. Finnhub dynamic docs did not expose exact free WebSocket connection/symbol limits in static fetch. Recommendation: do not implement unlimited streaming; force bounded capture and config limits.
4. BEA table/line IDs should be generated from BEA metadata and reviewed. Recommendation: add a manifest file rather than hardcoding guessed table names.
5. Polymarket probability markets are not official Fed probabilities. Recommendation: label as `prediction_market_implied`, not as economic ground truth.
6. SEC filing section extraction is not a structured EDGAR API capability. Recommendation: treat it as best-effort deterministic parsing with source offsets, fixtures, and explicit `unknowns` on parse failure.
7. Federal Reserve FOMC official materials are available from official pages/RSS, but not as a stable JSON API. Recommendation: use official HTML/RSS only, cache aggressively, and add DOM drift tests.
8. FRED can cover core commodity series such as WTI/Brent/natural gas/copper/broad commodity indexes. Recommendation: do not add a second commodity API now; leave gold/silver spot unsupported unless a licensed source is approved.
9. `yfinance` is unofficial and should remain HK/basic-only until a better licensed HK fundamental source exists.

## 8. Source Documents Consulted

- FRED API overview and `fred/series/observations`: https://fred.stlouisfed.org/docs/api/fred/series_observations.html
- FRED API keys: https://fred.stlouisfed.org/docs/api/api_key.html
- FRED WTI crude series: https://fred.stlouisfed.org/series/DCOILWTICO
- FRED Brent crude series: https://fred.stlouisfed.org/series/DCOILBRENTEU
- FRED US natural gas series: https://fred.stlouisfed.org/series/PNGASUSUSDM
- FRED copper series: https://fred.stlouisfed.org/series/PCOPPUSDM
- FRED all commodities index series: https://fred.stlouisfed.org/series/PALLFNFINDEXM
- BLS API v2 signatures: https://www.bls.gov/developers/api_signature_v2.htm
- BLS API FAQ/limits: https://www.bls.gov/developers/api_faqs.htm
- BEA Web Service API user guide: https://apps.bea.gov/api/_pdf/bea_web_service_api_user_guide.pdf
- Polymarket API introduction: https://docs.polymarket.com/api-reference/introduction
- Alpha Vantage documentation: https://www.alphavantage.co/documentation/
- Alpha Vantage support/free limits: https://www.alphavantage.co/support/
- yfinance README: https://github.com/ranaroussi/yfinance
- SEC EDGAR APIs: https://www.sec.gov/search-filings/edgar-application-programming-interfaces
- SEC fair access / EDGAR data: https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data
- Federal Reserve FOMC meeting calendars and materials: https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
- Federal Reserve RSS feeds: https://www.federalreserve.gov/feeds/feeds.htm
- FMP Press Releases: https://site.financialmodelingprep.com/developer/docs/stable/press-releases
- FMP pricing/free limits: https://site.financialmodelingprep.com/developer/docs/pricing
- FMP sector performance docs/search result: https://site.financialmodelingprep.com/developer/docs/
- Finnhub company peers docs: https://finnhub.io/docs/api/company-peers
- Finnhub websocket trades docs: https://finnhub.io/docs/api/websocket-trades
- Tavily welcome/API list: https://docs.tavily.com/welcome
- Tavily Search: https://docs.tavily.com/documentation/api-reference/endpoint/search
- Tavily Extract: https://docs.tavily.com/documentation/api-reference/endpoint/extract
- Tavily credits: https://docs.tavily.com/documentation/api-credits
- Tavily rate limits: https://docs.tavily.com/documentation/rate-limits
