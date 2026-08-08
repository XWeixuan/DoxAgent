# Document 1 / Document 2 Tool Result 质量验收（2026-08-08）

## 1. 范围与验收口径

- 范围：初轮对 43 个规划内非派生 tools 做验收并排除 3 个 IBKR；本轮按权限结论停用 3 个 tools、移除 Finnhub fund ownership composite 并新增纯 insider-transactions tool。当前规划内 registry 为 40 个 tools，其中 37 个非 IBKR active tools。
- 方法：使用当前本地 provider 配置直接调用真实 tool client；不发起任何 LLM request。固定股票样本以 `AAPL` 为主，行业样本使用 `MU / NAICS 334`，宏观与公共记录使用小窗口、小页数请求。
- 质量对象：重点检查会进入 agent prompt 的 `ToolResult.output`、`output_summary` 与错误语义。`raw` 不进入 prompt，由运行时单独作为原始审计证据保留。
- 合格标准：返回目标业务数据；字段与条数有界；不把供应商大而全 payload、无关 concept、HTML 正文或无关嵌套元数据暴露给 agent；部分成功必须保留子 endpoint 错误，不能伪装为完整成功。
- 状态说明：`可用` 表示真实调用返回预期信息；`部分可用` 表示至少一个独立 collection target 可用；`外部阻断` 表示代码路径正确，但当前 key/订阅不允许取得数据；`配置阻断` 表示缺少该供应商独立 key。

## 2. 逐 Tool 结果与质量结论

### 2.1 SEC、商业数据与市场数据

| Tool | 当前状态 | 实际返回内容（简述） | 质量处理与结论 |
|---|---|---|---|
| `sec.issuer_filings` | 可用 | issuer 摘要及过滤后的 10-K/10-Q/8-K filing index | 只返回请求 form 与 limit 内的 accession、日期、主文档坐标；不把完整 submissions 暴露给 agent。 |
| `sec.company_financials` | 可用 | 请求 XBRL concept 的单位、最近精确观测、form/fiscal period/accession | 已修复“请求 1 个 concept 却返回所有 fact pages”；`Revenues` 实测由约 220 KB 降至 2.2 KB，单 concept 最多 8 条去重观测。 |
| `sec.filing_content` | 可用 | 指定 filing 的目标 section 文本与 SEC 坐标 | 只保留命中的 section；不输出整份 filing HTML。 |
| `sec.material_contracts_projects` | 可用 | 最近相关 8-K/10-K/10-Q 中命中的合同、项目或重大事件 section | 已修复只查最新一份 filing 导致漏报；最多检查 12 份同 form filing，语义 section 为“任一命中即成功”。 |
| `sec.management_disclosures` | 可用 | MD&A、风险、市场风险等管理层披露 section | 同上；实测可返回目标 section，不再因未同时命中全部候选 section 而误报 PARTIAL。 |
| `benzinga.short_interest` | `UNAVAILABLE` | 当前 key 请求返回 HTTP 401，未取得 short interest | 已从 registry 与 Agent allowlist 移除；三个 short-interest targets 改为无 tool route 的 `UNAVAILABLE/BLOCKED`。 |
| `benzinga.management_guidance` | 可用 | AAPL 管理层 revenue/EPS 等 guidance，实测 3 条 | ticker/date 使用官方嵌套参数；仅保留 guidance 区间、期间、币种、发布日期等业务字段，约 1.3 KB。 |
| `benzinga.transcripts` | `UNAVAILABLE` | 当前 key 请求 transcript index/detail 返回 HTTP 401 | 已从 registry 与 Agent allowlist 移除；client 代码保留，待 entitlement 补齐后再启用。 |
| `benzinga.analyst_events` | 可用 | rating/consensus/earnings 事件；ratings 实测 25 条 | 只保留 analyst firm、action、rating、price target、日期等字段；分页上限 25，约 8.1 KB。 |
| `benzinga.market_signals` | 可用 | option activity 或 block trade；实测 25 条 | 只保留合约、方向、价格、成交量/OI、时间等信号字段；上限 25，约 14.4 KB。 |
| `fmp.sell_side_estimates` | 可用 | EPS/revenue estimates、price-target summary/consensus | 移除不属于 sell-side consensus 的 FMP model rating；estimates 最多 10 期，实测约 5.7 KB。 |
| `fmp.valuation_snapshot` | 可用 | profile、enterprise value、TTM key multiples 与 ratios | 仅保留估值计算需要的市值、EV、价格、收入/EBITDA及核心倍数，实测约 2.8 KB。 |
| `fmp.transcript_fallback` | `UNAVAILABLE` | stable endpoint 为 HTTP 402，legacy endpoint 为 HTTP 403 | 已从 registry 与 Agent allowlist 移除；client 代码保留，待 entitlement 补齐后再启用。 |
| `twelvedata.sell_side_estimates` | 可用 | EPS 与 revenue estimate 的期间、均值、高低值、分析师数量 | 删除 provider 额外字段，仅保留 meta 与最多 4 期估计，实测约 2.3 KB。 |
| `twelvedata.daily_ohlcv` | 可用 | 日线 OHLCV 及 market evidence snapshot | 使用调用方 `outputsize` 控制条数；实测 5 根日线，字段与股票范围正确。 |
| `finnhub.company_peers` | 可用 | AAPL 行业 peers，实测 12 个 ticker | 返回同业 ticker 集合，无额外公司画像。 |
| `finnhub.insider_transactions` | 可用 | dated insider transactions，真实复测返回 50 条 | 已删除 fund ownership endpoint、旧 composite tool 和全部 allowlist 引用；新工具只返回 insider 交易业务字段，并支持 from/to 窗口。 |
| `finnhub.company_news_events` | 可用 | company news 25 条、earnings events 4 条 | 修复 earnings list envelope 识别；新闻仅保留标题、摘要、来源、URL、时间等，新闻上限 25、其他记录上限 50。 |

### 2.2 宏观、行业与政府统计

| Tool | 当前状态 | 实际返回内容（简述） | 质量处理与结论 |
|---|---|---|---|
| `fred.activity_demand` | 可用 | real GDP、real PCE 等指定序列的 date/value | 修正 real PCE series id；每个 metric 仅返回请求 limit 内观测，不返回完整 FRED envelope。 |
| `fred.inflation_labor` | 可用 | CPI/PCE inflation、unemployment 等指定序列 | metric key 到 series id 固定治理；输出仅 date/value 与失败 metric。 |
| `fred.rates_credit_liquidity` | 可用 | policy rate、yield、credit/liquidity 指标 | 同上；逐 metric 部分失败可追踪，不污染其他序列。 |
| `fred.commodities_fx` | 可用 | WTI、天然气、美元等指定序列 | 同上；实测小窗口正常。 |
| `bls.labor_inflation` | 可用 | labor/CPI series 观测、期间与值 | 仅返回 metric key、series id 和 observation 必需字段。 |
| `bls.industry_producer_prices` | 可用 | 指定行业 PPI 序列 | 同上；不输出 BLS catalog 或无关 series。 |
| `bls.import_export_prices` | 可用 | import/export price index 序列 | 同上；实测返回目标序列。 |
| `bea.national_accounts` | 可用 | NIPA table/line 的年度或季度 DataValue | 修复 NIPA 与 GDPByIndustry 参数混用；在本地按 LineNumber 过滤，实测 2 条目标记录。 |
| `bea.industry_accounts` | 可用 | GDPByIndustry 指定 table/industry 的 DataValue | 使用 TableID 与 Industry 正确路由，实测 1 条目标记录。 |
| `census.manufacturing_orders` | 可用 | MU/NAICS 334 new orders；真实复测返回 18 条月度季调记录 | 独立 `CENSUS_API_KEY` 已配置；默认只取 `seasonally_adj=yes`，按 period 倒序，输出由双口径 36 条/6.5 KB 降至 18 条/3.6 KB。可显式请求 `no` 或 `both`。 |
| `eia.energy_prices` | 可用 | gasoline/energy price 的 period、value、units、series | 修复 gasoline facet id；按 period 倒序，仅返回业务字段，实测 3 条。 |
| `eia.energy_supply_operations` | 可用 | production、inventory、capacity/operation 等指定 series | 同上；按 metric key 选择 endpoint/facet，实测 3 条。 |

### 2.3 合同、监管、立法、审批与 IR

| Tool | 当前状态 | 实际返回内容（简述） | 质量处理与结论 |
|---|---|---|---|
| `usaspending.award_search` | 可用 | 过滤条件下的 award 摘要 | 仅保留 award id、recipient、amount、dates、agency/type 等；实测 1 条。 |
| `usaspending.award_detail` | 可用 | 单一 award 的描述、金额、履约期、机构、NAICS/PSC 等 | 使用 detail 白名单，不输出无关交易历史；实测 1 条。 |
| `sam.contract_opportunities` | 可用 | 关键词与日期窗口内 opportunity；实测 2 条 | 修复 `opportunitiesData` 容器未识别；仅保留 notice、title、agency、date、NAICS、award/link 等。无匹配时仍应是业务空结果，而非代码失败。 |
| `regulations.rulemaking_records` | 可用 | Regulations.gov documents/dockets/comments 摘要 | 只保留 id/type 与标题、文档类型、机构、docket、日期、comment window、self URL；实测 5 条。 |
| `federal_register.documents` | 可用 | Federal Register 文档标题、摘要、发布/生效日、机构及链接 | 结果上限由 limit 控制；实测 5 条，已排除大而全 API 元数据。 |
| `congress.legislative_actions` | 可用 | bills、committee reports 或 hearings；bill 实测 2 条 | 修复 `bills`/`committeeReports`/`hearings` 容器未识别；按 record type 使用独立字段白名单。 |
| `openfda.approval_milestones` | 可用 | 510(k)/PMA 等审批里程碑；实测 2 条 | openFDA 嵌套 metadata 改为小白名单，实测由约 14.7 KB 降至 1.1 KB。 |
| `openfda.safety_actions` | 可用 | recall/安全行动的 firm、原因、产品、日期、分布等 | 仅保留安全行动字段和受控 openFDA 标识；实测 3 条、约 3.1 KB。 |
| `ir.official_feed_discovery` | 可用 | 官方域名页面中候选 RSS/news/filing/earnings URL | 不再返回 HTML 行片段；只输出 label、URL、类型和只读坐标，实测 1 个候选。 |
| `ir.official_updates` | 可用 | 官方 IR 页面中的受控 update links | 与 discovery 共用域名 allowlist，禁止跨域抓取与写状态；实测 1 条。 |

## 3. 本轮修复汇总

1. 写入本地 `.env` 的 `BENZINGA_API_KEY` 已被运行时读取；密钥未写入报告、测试或版本控制文件。
2. 修复 SEC 最近 filing 搜索与 section 的“候选项误当全部必需项”语义，并对 XBRL 指定 concept 做强制有界投影。
3. 修复 Benzinga calendar/signal 参数、业务错误识别和五类工具的独立字段投影。
4. 移除 Finnhub fund ownership endpoint 与旧 composite tool，新增纯 `finnhub.insider_transactions`；同时修复 earnings envelope，并给新闻/交易记录设置条数上限。
5. 修复 FRED real PCE、EIA gasoline facet、BEA dataset 参数、Census M3 字段与 key 契约。
6. 修复 SAM/Congress 响应容器，并为 USAspending、Regulations.gov、Federal Register、Congress、openFDA 建立业务字段白名单。
7. 删除 IR discovery 的原始 HTML snippet；候选链接仍保留官方域名验证和只读语义。

## 4. 仍需外部条件的 collection targets

| Target | 当前阻断 | 解除条件 |
|---|---|---|
| Benzinga short interest | 当前 key 对 endpoint 返回 HTTP 401 | 开通 Short Interest 数据产品或提供具备该权限的 key。 |
| Benzinga transcripts | 当前 key 对 transcript endpoints 返回 HTTP 401 | 开通 Transcripts 产品或提供具备该权限的 key。 |
| FMP transcript fallback | stable 402、legacy 403 | 升级包含 Earnings Call Transcript 的 FMP 套餐。 |
| Finnhub fund ownership | 已按决定移除，不再作为 tool 或 fallback | 如未来重新纳入，需重新立项并配置 Premium entitlement。 |

这些阻断不能用参数猜测、网页抓取或把其他数据冒充目标数据来“修复”。在外部权限补齐前，Manifest 必须以 collection target 粒度保留 `FAILED/PARTIAL`，且无可靠状态时不生成 State Value。

## 5. 自动化复核

- provider / contract 测试：`34 passed`（新增 SEC bounded concept、openFDA nested whitelist、Census dedicated-key、Finnhub field projection 断言）。
- 真实定点复验：Census M3 使用独立 key 成功返回 18 条季调月度记录；`finnhub.insider_transactions` 成功返回 50 条；三个 entitlement tools 均不再注册，short-interest targets 保持 `UNAVAILABLE/BLOCKED`。
- 本验收没有调用 LLM，也没有把 API key 写入任何 tracked file。
