# O4-A Data MCP 扩展与真实调用验收（2026-08-20）

## 结论

本轮已落地上一轮归为第一、二、三类的可实现项，并以 O4-A signed capability、真实 stdio MCP、真实 provider 调用完成验收。真实验收执行时，O4-A 服务端 policy 有 36 个 canonical tool 授权，其中 32 个形成可暴露 contract。

验收后按实际 provider/API tier 结果进一步收紧了当前 O4-A 暴露面：8 个已确认不可用、且该 tool contract 没有可用 fallback 的接口已从 O4-A effective policy、guide、catalog 和 signed capability 中移除。当前 O4-A 为 **28 个授权、28 个可暴露 contract**；这些限制仅作用于 O4-A，不删除底层 provider client，不影响直接 ToolRegistry 调用或 O4-B/其他节点。

当前不向 O4-A 暴露的 8 个接口为：`benzinga.analyst_events`、`benzinga.market_signals`、`fmp.valuation_snapshot`、`twelvedata.sell_side_estimates`、`ibkr.historical_ticks`、`ibkr.option_surface`、`ibkr.fed_funds_curve`、`alpha.historical_options`。

本轮没有为满足 O4-A 改写 C1/C3 专用输出。相对收益、O4 估值、机构持仓、期权、空头、Form 4 富化和一致预期路由均使用新 tool ID；共享 provider 的修改限于 cutoff、状态治理、编码修复、SEC raw Form 4 URL 规范化与 IBKR 官方 socket 行为修复。

## 一、MCP 设计缺陷修复

| 原缺口 | 落地结果 | 真实验收 |
|---|---|---|
| 多意图 guide 被压成单标签 | guide 同时识别 valuation、consensus、options、relative performance、positioning、event timing，并给出逐 capability coverage/gap | 综合请求返回 12 个候选，4 个 capability 均为 covered；不可用 entitlement 单列 |
| cutoff/look-ahead | quote、daily、trade tape 在服务端执行 cutoff；历史 cutoff 不再调用 live tape/quote | quote 降级为 `partial/daily_close_fallback`；live tape 为 `not_applicable` 并推荐 historical ticks |
| quote fallback 冒充实时报价 | 返回 `price_kind`、requested/resolved fields、bar date、session、staleness，并提升为 partial | Twelve Data fallback 返回 2026-08-19 close，明确不等价于 bid/ask/last |
| 日线结束日与复权口径不明 | 返回 requested/actual end、end completeness、raw/adjusted 口径、split/dividend metadata，数值字段标准化 | 未形成完整 8 月 20 日 bar 时返回 `requested_end_date_missing`；调整后 yfinance 路由返回 35 根及 corporate-action 字段 |
| 基准/同行被 ticker 注入锁死 | 新增治理后的显式 basket 相对收益工具 | NVDA/QQQ/SOXX/AMD 同 cutoff 返回 close-to-close 与相对收益 |
| live tape 无法回放历史 | 新增 `ibkr.historical_ticks`，live tape 对历史 cutoff 直接拒绝 | 工具路由正确；本账户实调因 IBKR API 历史逐笔订阅 2188 被拒绝，保留外部 entitlement 状态 |
| citation validator 名称误导 | 返回 `alias_valid` 与 `claim_entailment=not_checked`；保留 `valid` 兼容字段 | O2 可解析、O9999 不可解析，整体 alias_valid=false；没有声称语义蕴含 |
| Observation 回读缺日期定位 | `data_read_observation` 支持 date_from/date_to，并可与 JSON pointer 组合 | `/observations` 在 2026-08-10—20 范围返回 5/9 条并正确标记 truncated |
| mcporter/case root 不一致 | capability root 允许合法 case descendant，catalog 保持 attempt immutable | mcporter schema/call 成功；修改 contract 后旧 attempt 正确拒绝 catalog 覆盖，新 attempt 正常启动 |
| SEC capture 403 | Source Capture 使用合规 SEC User-Agent 与 SEC 限速 | 原 Pilot 的 NVDA 10-Q Archives URL 真实捕获成功并持久化为 O51 |
| 公共 Observation 元数据膨胀 | 延续并复验统一 Agent projection，仅暴露 alias/title/content/source 与必要执行状态 | inline、pack、read 均未重新暴露 schema/run/tool-call/hash 等 runtime 字段 |

## 二、新增/补齐的数据工具

| 能力 | 工具 | 返回内容与质量判断 |
|---|---|---|
| 相对行情 | `market.relative_performance` | 显式 basket、共同 cutoff、每个 symbol 的区间/收益/质量标记；部分日期缺失会 partial |
| 当前一致预期与修订 | `market.sell_side_consensus` | 当前/次季、当前/次年 EPS 与收入均值/区间/分析师数、7/30/60/90 日 trend 与 revision breadth；因无历史 vintage 固定 partial |
| 估值 | `alpha.valuation_snapshot` | 市值、EBITDA、Trailing/Forward PE、P/S、P/B、EV/Revenue、EV/EBITDA 等紧凑字段 |
| 显式同行估值 | `yfinance.peer_relative_valuation` | 只比较显式 peers；输出 quote/financial currency；相对统计排除 target 自身、绝对金额及币种不匹配行 |
| 期权 | `ibkr.option_surface`、`alpha.historical_options` | 官方 TWS 有界 chain/quotes/Greeks 与 Alpha 历史备选已实现；本账户分别受期权行情和 Alpha premium entitlement 限制，未伪造 surface |
| 空头/借券 | `yfinance.short_interest`、`ibkr.shortability_snapshot` | 前者返回 settlement shares、float%、days-to-cover 并标 unofficial；后者真实返回 shortable shares=205,948,620、tier=3，明确不等于 listed short interest |
| 机构持仓 | `alpha.institutional_holdings` | 汇总 holder/share 增减与 top 10 holdings；从原 6,326 行源数据投影为紧凑可用结果 |
| 内部人语境 | `sec.insider_transactions_enriched` | 3 个 Form 4 的 role、security、direct/indirect、transaction code、10b5-1/gift/award footnotes、accession/source URL；修正 transform path 后约 9 秒完成 |
| 事件 | `alpha.earnings_events`、`finnhub.company_news_events`、`ir.official_updates` | 财报日期/时段、公司新闻与 surprise、官方 IR feed/candidate URL；Finnhub 文本乱码已确定性修复 |
| 宏观/政策 | `fred.series_observations`、`fred.rates_credit_liquidity`、`fed.fomc_calendar_materials`、`ibkr.fed_funds_curve` | FRED 支持 vintage 参数；DFF/DGS10/VIX 与 FOMC 日程成功。ZQ 曲线实现完成，但本次 TWS 未返回可用价格 |
| 预测市场 | `polymarket.market_probability` | 改用官方 `public-search`；搜索词真实生效，返回 5 个匹配市场、outcome/probability、bid/ask、liquidity/volume，而非 35KB 无关 raw payload |

## 三、32 个 O4-A 暴露工具逐项真实调用

| 工具组 | 结果 |
|---|---|
| market（5） | daily=partial；quote=partial；relative=partial；consensus=partial/current-only；trade tape=not_applicable for historical cutoff |
| IBKR（4） | shortability=succeeded；historical ticks=failed/2188 subscription；option surface=failed/option market-data entitlement；Fed Funds curve=failed/no usable subscribed price |
| Alpha Vantage（5） | valuation、earnings calendar、institutional holdings、insider transactions succeeded；historical options=unavailable/premium |
| yfinance（4） | adjusted OHLCV、peer valuation succeeded/degraded；sell-side consensus=partial/current-only；short interest=partial/unofficial |
| SEC（5） | issuer filings、company financials、10-Q filing content、10-Q management disclosures、enriched Form 4 全部 succeeded |
| Finnhub（3） | peers、insider transactions、company news/events 全部 succeeded；一次 transient SSL EOF 后单次重试成功 |
| FRED/Fed（3） | direct vintage series、semantic rates/liquidity、FOMC calendar 全部 succeeded |
| Polymarket（1） | succeeded；搜索与紧凑概率投影符合预期 |
| IR/Tavily（2） | official updates succeeded；Tavily 对具体 URL 可抽取，但 IR listing page 导航噪声较高，应先用 IR tool 取得具体 press-release URL 再 extract |

本节记录的是收紧前的真实调用验收快照。验收后，原四个 catalog-unavailable tool 与真实调用确认 entitlement 不足的四个 tool 已一并从 O4-A effective policy 移除；当前 agent 的 guide、catalog 与执行入口均不再出现这 8 个接口。

## 四、仍属于第四类或外部条件的边界

- 无 provider 可证明检索时点之前的历史卖方 consensus vintage；当前 yfinance 数据只能作为 retrieval-time current snapshot。
- 历史 forward multiple percentile 仍无可靠 point-in-time 序列。
- 官方 listed short-interest 历史、borrow fee/utilization 仍未由当前账户/公共 API 完整覆盖；yfinance 仅作受控 fallback，IBKR shortability 不是 short interest。
- IBKR historical ticks、option surface、Fed Funds futures 曲线的代码路径已经贯通，但本地账户相应行情 entitlement 不足；这是本轮无法自行消除的外部状态。
- 跨来源“claim 是否被证据蕴含”仍不是确定性 citation validator 的职责；现在契约已明确为 alias resolution only。

## 五、回归结果

- 最终 Data MCP/provider/horizontal/Codex Pilot/C3/O4 回归：117 passed。
- touched-file Ruff 与 provider compileall：通过。
- 所有真实验收均通过 MCP stdio 与 signed O4-A capability 完成；没有真实 LLM request。
