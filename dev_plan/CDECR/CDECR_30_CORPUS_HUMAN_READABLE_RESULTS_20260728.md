# CDECR 30 篇真实测试集：人类可读结果

> 本文档展示本次冻结运行的实际输出，不是人工修正后的理想结果。带有“需复核”标记的 Atomic/Package 已由逐条人工审计确认存在边界风险；其余结果也不等同于业务真值。完整质量判断以配套验收报告为准。

## 1. 运行概览

- 文档成功：30/30（100%）
- 跨文档成功：30/30（100%）
- 输出：225 Mention / 95 Atomic / 65 Package
- Token：输入 4,403,264 / 输出 443,843 / 合计 4,847,107
- Mention P/R/F1：85.33% / 71.64% / 77.89%
- Atomic Pair P/R/F1：41.84% / 76.92% / 54.20%
- Package Pair P/R/F1：91.54% / 73.37% / 81.45%

## 2. 阅读说明

- 每篇文档下列出该文档生成的全部 Mention，而不是新闻原文摘要。
- `Evidence` 显示原始证据定位状态；`TEXT_NOT_FOUND` 仍按本轮容错协议保留。
- `Atomic` 是系统最终归并结果；标注“需拆分”的 Atomic 存在已确认的过合并。
- `Package` 是系统最终事件包；标注“边界错误”的 Package 存在已确认的误纳入。
- 同一错误可能在多篇文档中重复出现，这是跨文档聚类的自然展示，不代表多个独立根因。

## 3. 30 篇文档目录

| # | 来源 | 标题 | Mention 数 |
| ---: | --- | --- | ---: |
| 1 | benzinga.com | [SanDisk, Western Digital, Seagate Surge After Hours As Micron's Blowout Earnings Signal Memory Upcycle](https://www.benzinga.com/markets/equities/26/06/60088616/sandisk-western-digital-and-seagate-surge-after-microns-blowout-earnings-signal-memory-upcycle) | 3 |
| 2 | beincrypto.com | [KOSPI Spikes 5% on Opening, Riding Micron’s Surprise Earnings](https://beincrypto.com/micron-earnings-kospi-sidecar-chip-rally/) | 7 |
| 3 | fool.com | [Micron Just Guided for a Staggering $50 Billion in Fiscal Q4 Revenue. Here's What It Means for the AI Trade.](https://www.fool.com/investing/2026/06/24/micron-just-guided-for-a-staggering-50-billion-in/) | 12 |
| 4 | benzinga.com | [Micron CEO Expects Memory Supply To Improve Gradually In 2028, But There's No 'Line Of Sight' When It Will Catch Up To Rising AI Demand](https://www.benzinga.com/markets/tech/26/06/60089761/micron-ceo-sees-memory-supply-improving-by-2028-but-no-clear-end-in-sight-to-ai-demand-shortfallmicron-ceo-sees-memory-supply-improving-by-2028-but-no-clear-end-in-sight-to-ai-demand-shortfall) | 8 |
| 5 | fool.com | [4 Blowout Numbers From Micron's Earnings Investors Need To See](https://www.fool.com/investing/2026/06/25/x-blowout-numbers-from-microns-earnings-investors/) | 10 |
| 6 | finance.yahoo.com | [Micron Technology (MU) Announces Collaboration with Anthropic to Scale Next Generation AI Infrastructure](https://finance.yahoo.com/technology/ai/articles/micron-technology-mu-announces-collaboration-062406354.html) | 3 |
| 7 | benzinga.com | [Micron CEO Says AI Memory Shortage Could Last Beyond 2028: These ETFs Are Positioned To Profit](https://www.benzinga.com/etfs/sector-etfs/26/06/60094351/micron-ceo-says-ai-memory-shortage-could-last-beyond-2028-these-etfs-are-positioned-to-profit) | 5 |
| 8 | benzinga.com | [Tesla's Optimus Could Become A Bigger Memory Customer Than Its Cars, If Micron Is Right](https://www.benzinga.com/trading-ideas/long-ideas/26/06/60094569/tesla-optimus-could-become-a-bigger-memory-customer-than-its-cars-if-micron-is-right) | 5 |
| 9 | euronews.com | [Micron posts record results as AI boom drives 15-fold jump in net profit](https://www.euronews.com/business/2026/06/25/micron-posts-record-results-as-ai-boom-drives-15-fold-jump-in-net-profit) | 7 |
| 10 | benzinga.com | [Micron Is Spending Billions On New Fabs—And Still Says It'll Return 100% Of Excess Cash To Shareholders](https://www.benzinga.com/trading-ideas/long-ideas/26/06/60095272/micron-is-spending-billions-on-new-fabs-and-still-says-itll-return-100-of-excess-cash-to-shareholders) | 6 |
| 11 | Benzinga | [Dow Surges 250 Points; Micron Posts Upbeat Q3 Earnings](https://finnhub.io/api/news?id=7316a88ccda79b3354a49965fa82f15ed81fe3722e51491463cf118f097a0143) | 7 |
| 12 | benzinga.com | [These Analysts Boost Their Forecasts On Micron Technology After Better-Than-Expected Q3 Results](https://www.benzinga.com/analyst-stock-ratings/price-target/26/06/60100007/these-analysts-boost-their-forecasts-on-micron-technology-after-better-than-expected-q3-results) | 6 |
| 13 | edition.cnn.com | [Investors bet on AI again after Micron reports 346% sales jump](https://edition.cnn.com/2026/06/25/business/micron-results-ai-stocks-volatility) | 16 |
| 14 | benzinga.com | [The AI Memory Trade Is Heating Up: Defiance Launches 2X DRAM ETF After Micron's Blowout Quarter](https://www.benzinga.com/etfs/new-etfs/26/06/60103609/the-ai-memory-trade-is-heating-up-defiance-launches-2x-dram-etf-after-microns-blowout-quarter) | 4 |
| 15 | benzinga.com | [Mizuho Maintains Outperform on Micron Technology, Raises Price Target to $1375](https://www.benzinga.com/news/26/06/60106539/mizuho-maintains-outperform-micron-technology-raises-price-target-1375) | 1 |
| 16 | proactiveinvestors.com | [Wedbush says chip rally has further to run after Micron's blowout quarter](https://www.proactiveinvestors.com/companies/news/1094490/wedbush-says-chip-rally-has-further-to-run-after-micron-s-blowout-quarter-1094490.html) | 10 |
| 17 | CNBC | [Micron surges to new highs on blockbuster earnings: Jim Lebenthal buys the stock](https://finnhub.io/api/news?id=760aa3bb75cb55185d1ad9b8997324a47603aeac7746264f37945ce55241212a) | 2 |
| 18 | finance.yahoo.com | [SK Hynix Unveils $29 Billion US Listing as Shares Soar Over 800%](https://finance.yahoo.com/markets/stocks/articles/sk-hynix-unveils-29-billion-135023709.html) | 6 |
| 19 | benzinga.com | [Apple Just Confirmed Micron's Biggest AI Prediction](https://www.benzinga.com/trading-ideas/long-ideas/26/06/60108831/apple-just-confirmed-microns-biggest-ai-prediction) | 3 |
| 20 | fool.com | [Why Sandisk Stock Soared Today](https://www.fool.com/investing/2026/06/25/why-sandisk-stock-soared-today/) | 9 |
| 21 | fool.com | [Is Micron a Buy After Its Blowout Earnings Report?](https://www.fool.com/investing/2026/06/25/is-micron-a-buy-after-its-blowout-earnings-report/) | 11 |
| 22 | fool.com | [Why Everyone Is Talking About Micron](https://www.fool.com/investing/2026/06/25/why-everyone-is-talking-about-micron/) | 10 |
| 23 | finance.yahoo.com | [Why Micron's blowout earnings are a headache for Apple](https://finance.yahoo.com/markets/article/why-microns-blowout-earnings-are-a-headache-for-apple-161932441.html) | 9 |
| 24 | proactiveinvestors.com | [Micron shares surge to all-time high as Wall Street cheers unprecedented contract visibility](https://www.proactiveinvestors.com/companies/news/1094520/micron-shares-surge-to-all-time-high-as-wall-street-cheers-unprecedented-contract-visibility-1094520.html) | 15 |
| 25 | 247wallst.com | [Micron Just Locked In $100 Billion in Sales, and Wall Street Thinks the Boom-Bust Chip Cycle Is Dead](https://247wallst.com/investing/2026/06/25/micron-just-locked-in-100-billion-in-sales-and-wall-street-thinks-the-boom-bust-chip-cycle-is-dead/) | 18 |
| 26 | finance.yahoo.com | [Tim Cook Calls the Memory Crisis a "Hundred-Year Flood"](https://finance.yahoo.com/technology/articles/tim-cook-calls-memory-crisis-170140774.html) | 10 |
| 27 | finance.yahoo.com | [Sandisk Stock Spikes 15% After Citi Unleashes Massive Price Target Hike](https://finance.yahoo.com/markets/stocks/articles/sandisk-stock-spikes-15-citi-170934290.html) | 3 |
| 28 | barchart.com | [MU Stock Soars as Micron’s Revenue More Than Quadrupled in Q3](https://www.barchart.com/story/news/3092/mu-stock-soars-as-microns-revenue-more-than-quadrupled-in-q3) | 7 |
| 29 | Yahoo | [AI boom keeps memory chip makers in sweet spot, says expert](https://finnhub.io/api/news?id=83f579c98258ef8f6999941b3227364c4d014b9cc9cfd5300374568a2fb3e0c7) | 1 |
| 30 | fool.com | [Stock Market Today, June 25: Apple Drops After Raising Device Prices to Offset Higher Memory Costs](https://www.fool.com/coverage/stock-market-today/2026/06/25/stock-market-today-june-25-apple-drops-after-raising-device-prices-to-offset-higher-memory-costs/) | 11 |

## 4. 按文档结果

### 1. SanDisk, Western Digital, Seagate Surge After Hours As Micron's Blowout Earnings Signal Memory Upcycle

- 来源：benzinga.com
- 发布时间：2026-06-25T00:04:59+00:00
- Message ID：`doxatlas:raw_media:6389d63c-442c-4c9b-9ba5-b943b7e91804`
- 原文：https://www.benzinga.com/markets/equities/26/06/60088616/sandisk-western-digital-and-seagate-surge-after-microns-blowout-earnings-signal-memory-upcycle
- Mention 数：3

#### 1.1 Trading volume for SanDisk, Western Digital, and Seagate was active during the regular trading session.

- Mention：`mention:218f115a…`；Family：`MARKET_MOVEMENT`；Assertion：`ACTUAL`
- 参与者：SanDisk (SUBJECT)；Western Digital (SUBJECT)；Seagate (SUBJECT)
- 数量：—
- 时间：2026-06-24T00:00:00 / 2026-06-24T00:00:00 / DAY
- Evidence：`VERIFIED`
- Atomic：`atomic:f31cd9fd…` — Trading volume for SanDisk, Western Digital, and Seagate was active during the regular trading session.
- Package：`package:41ecc196…` — Trading volume for SanDisk, Western Digital, and Seagate was active during the regular trading session.

> Evidence: Trading volume for all three names remained active during the regular session.

#### 1.2 Micron provided financial guidance projecting $50 billion in revenue and $31 in EPS.

- Mention：`mention:7087fad9…`；Family：`GUIDANCE_EXPECTATION`；Assertion：`EXPECTED`
- 参与者：Micron (ACTOR)
- 数量：$50 billion in revenue [revenue]；$31 EPS [eps]
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:a464025a…` — Micron provided financial guidance projecting $50 billion in revenue and $31 in EPS.；**需拆分复核**（该簇人工核定 FP pair=49）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: The guidance of $50 billion in revenue and $31 EPS both stand well ahead of analyst estimates.

#### 1.3 SanDisk, Western Digital, and Seagate stock prices rose in after-hours trading.

- Mention：`mention:a7d6526d…`；Family：`MARKET_MOVEMENT`；Assertion：`ACTUAL`
- 参与者：SanDisk (SUBJECT)；Western Digital (SUBJECT)；Seagate (SUBJECT)
- 数量：—
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:b8387e0e…` — SanDisk, Western Digital, and Seagate stock prices rose in after-hours trading.
- Package：`package:99a65e40…` — Sandisk stock price increased by 11.2% on Thursday morning.；**边界错误**：Sandisk Thursday-morning move and the multi-company after-hours move have different temporal/entity boundaries; the first Atomic is also polluted by a Citi target mention.

> Evidence: SanDisk, Western Digital, Seagate Surge After Hours As Micron's Blowout Earnings Signal Memory Upcycle

### 2. KOSPI Spikes 5% on Opening, Riding Micron’s Surprise Earnings

- 来源：beincrypto.com
- 发布时间：2026-06-25T01:28:28+00:00
- Message ID：`doxatlas:raw_media:3f25dec4-7f5f-4203-9e0f-d37c2b06a2c7`
- 原文：https://beincrypto.com/micron-earnings-kospi-sidecar-chip-rally/
- Mention 数：7

#### 2.1 SK Hynix stock jumped more than 10% in early morning trading on June 25, 2026, triggering a static volatility interruption (VI) at the open.

- Mention：`mention:492ec86f…`；Family：`MARKET_MOVEMENT`；Assertion：`ACTUAL`
- 参与者：SK Hynix (SUBJECT)
- 数量：more than 10% [stock_change_percent]
- 时间：2026-06-25T00:00:00 / DAY
- Evidence：`VERIFIED`
- Atomic：`atomic:f51200ad…` — SK Hynix stock jumped more than 10% in early morning trading on June 25, 2026, triggering a static volatility interruption (VI) at the open.；**需拆分复核**（该簇人工核定 FP pair=0）
- Package：`package:7b63ae2b…` — SK Hynix stock jumped more than 10% in early morning trading on June 25, 2026, triggering a static volatility interruption (VI) at the open.

> Evidence: SK Hynix jumped more than 10% in early morning trading and triggered a static volatility interruption (VI) at the open, briefly switching to single-price trading for two minutes.

#### 2.2 Micron Technology stock gained roughly 15% in after-hours trading following the announcement of its fiscal Q3 2026 results.

- Mention：`mention:5972075a…`；Family：`MARKET_MOVEMENT`；Assertion：`ACTUAL`
- 参与者：Micron Technology (SUBJECT)
- 数量：roughly 15% [stock_change_percent]
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:471dce1e…` — Micron Technology stock gained roughly 15% in after-hours trading following the announcement of its fiscal Q3 2026 results.；**需拆分复核**（该簇人工核定 FP pair=39）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: The stock gained roughly 15% in after-hours trading following the announcement.

#### 2.3 On June 25, 2026, individual investors net bought roughly 490 billion won, institutions net bought around 100 billion won, and foreign investors net sold approximately 600 billion won in the South Korean market.

- Mention：`mention:76a4613f…`；Family：`TRANSACTION_CAPITAL`；Assertion：`ACTUAL`
- 参与者：individual investors (ACTOR)；institutions (ACTOR)；Foreign investors (ACTOR)
- 数量：roughly 490 billion won [net_buy_amount]；around 100 billion won [net_buy_amount]；approximately 600 billion won [net_sell_amount]
- 时间：2026-06-25T00:00:00 / DAY
- Evidence：`VERIFIED`
- Atomic：`atomic:d638e7b1…` — On June 25, 2026, individual investors net bought roughly 490 billion won, institutions net bought around 100 billion won, and foreign investors net sold approximately 600 billion won in the South Korean market.
- Package：`package:6058d5a5…` — On June 25, 2026, individual investors net bought roughly 490 billion won, institutions net bought around 100 billion won, and foreign investors net sold approximately 600 billion won in the South Korean market.

> Evidence: By investor type, individuals net bought roughly 490 billion won and institutions added around 100 billion won.

#### 2.4 The KOSPI index surged more than 5% at the open on June 25, 2026, rising above 8,900 from 8,400 in the prior session.

- Mention：`mention:c1f3cc62…`；Family：`MARKET_MOVEMENT`；Assertion：`ACTUAL`
- 参与者：KOSPI (SUBJECT)
- 数量：more than 5% [index_change_percent]；above 8,900 [index_level]；8,400 [index_level_prior]
- 时间：2026-06-25T00:00:00 / DAY
- Evidence：`VERIFIED`
- Atomic：`atomic:0c739830…` — The KOSPI index surged more than 5% at the open on June 25, 2026, rising above 8,900 from 8,400 in the prior session.；**需拆分复核**（该簇人工核定 FP pair=2）
- Package：`package:234ea60d…` — The KOSPI index surged more than 5% at the open on June 25, 2026, rising above 8,900 from 8,400 in the prior session.

> Evidence: South Korea’s KOSPI surged more than 5% at the open on June 25, pushing back above 8,900 from 8,400 the prior session.

#### 2.5 The Korea Exchange (KRX) activated a buy-side sidecar mechanism shortly after the market open on June 25, 2026, suspending program trading for five minutes.

- Mention：`mention:c64ba388…`；Family：`REGULATORY_LEGAL_POLICY`；Assertion：`ACTUAL`
- 参与者：Korea Exchange (KRX) (ACTOR)
- 数量：—
- 时间：2026-06-25T00:00:00 / DAY
- Evidence：`VERIFIED`
- Atomic：`atomic:17502192…` — The Korea Exchange (KRX) activated a buy-side sidecar mechanism shortly after the market open on June 25, 2026, suspending program trading for five minutes.
- Package：`package:9e56c5c2…` — The Korea Exchange (KRX) activated a buy-side sidecar mechanism shortly after the market open on June 25, 2026, suspending program trading for five minutes.

> Evidence: The Korea Exchange (KRX) activated a buy-side sidecar shortly after the open, suspending program trading for five minutes.

#### 2.6 Samsung Electronics stock reclaimed the 360,000 won level and SK Hynix stock reclaimed the 2.8 million won level on June 25, 2026.

- Mention：`mention:d8df9cef…`；Family：`MARKET_MOVEMENT`；Assertion：`ACTUAL`
- 参与者：Samsung Electronics (SUBJECT)；SK Hynix (SUBJECT)
- 数量：360,000 won [stock_price]；2.8 million won [stock_price]
- 时间：2026-06-25T00:00:00 / DAY
- Evidence：`VERIFIED`
- Atomic：`atomic:f51200ad…` — SK Hynix stock jumped more than 10% in early morning trading on June 25, 2026, triggering a static volatility interruption (VI) at the open.；**需拆分复核**（该簇人工核定 FP pair=0）
- Package：`package:7b63ae2b…` — SK Hynix stock jumped more than 10% in early morning trading on June 25, 2026, triggering a static volatility interruption (VI) at the open.

> Evidence: Samsung Electronics and SK Hynix reclaimed the 360,000 won and 2.8 million won levels, respectively.

#### 2.7 Micron Technology reported fiscal Q3 2026 financial results, including revenue of $41.46 billion and adjusted earnings per share of $25.11.

- Mention：`mention:e7fdbefb…`；Family：`FINANCIAL_PERFORMANCE`；Assertion：`ACTUAL`
- 参与者：Micron Technology (ACTOR)
- 数量：$41.46 billion [revenue]；$25.11 [adjusted_eps]
- 时间：fiscal Q3 2026 / UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:bb912535…` — Micron Technology reported fiscal Q3 2026 financial results, including revenue of $41.46 billion and adjusted earnings per share of $25.11.；**需拆分复核**（该簇人工核定 FP pair=327）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: Micron reported fiscal Q3 2026 revenue of $41.46 billion, more than four times the $9.30 billion it posted in the same quarter a year earlier.

### 3. Micron Just Guided for a Staggering $50 Billion in Fiscal Q4 Revenue. Here's What It Means for the AI Trade.

- 来源：fool.com
- 发布时间：2026-06-25T03:11:00+00:00
- Message ID：`doxatlas:raw_media:c762d097-1190-4ea7-9802-5d44fadefd9f`
- 原文：https://www.fool.com/investing/2026/06/24/micron-just-guided-for-a-staggering-50-billion-in/
- Mention 数：12

#### 3.1 Micron Technology's cloud memory business unit reported fiscal third quarter 2026 revenue of $13.77 billion, up from $3.39 billion a year ago, with an operating margin of 78%.

- Mention：`mention:01528592…`；Family：`FINANCIAL_PERFORMANCE`；Assertion：`ACTUAL`
- 参与者：Micron's cloud memory business unit (SUBJECT)
- 数量：$13.77 billion [revenue_current]；$3.39 billion [revenue_prior_year]；78% [operating_margin]
- 时间：fiscal Q3 2026 / UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:bb912535…` — Micron Technology reported fiscal Q3 2026 financial results, including revenue of $41.46 billion and adjusted earnings per share of $25.11.；**需拆分复核**（该簇人工核定 FP pair=327）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: Micron's cloud memory business unit, the most direct AI proxy in the company's portfolio, grew revenue from $3.39 billion a year ago to $13.77 billion, and its operating margin hit 78%.

#### 3.2 Micron Technology announced 16 signed Strategic Customer Agreements, which are multi-year deals locking in volume and providing pricing visibility for memory supply.

- Mention：`mention:041a24fc…`；Family：`COMMERCIAL_OPERATION`；Assertion：`ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：16 signed agreements [agreement_count]
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:d600f32b…` — Micron Technology announced 16 signed Strategic Customer Agreements, which are multi-year deals locking in volume and providing pricing visibility for memory supply.；**需拆分复核**（该簇人工核定 FP pair=84）
- Package：`package:d12945f7…` — Micron is investing at record levels in technology, products and supply.；**边界错误**：June 22 Anthropic partnership/investment is a different bounded announcement from the earnings-origin SCA/investment commentary.

> Evidence: Micron announced what it called "transformational Strategic Customer Agreements" -- multi-year deals that lock in volume and provide pricing visibility for memory supply.

#### 3.3 Micron Technology shares increased approximately 16% in after-hours trading on Wednesday, rising from about $1,049 to about $1,215.

- Mention：`mention:26162eae…`；Family：`MARKET_MOVEMENT`；Assertion：`ACTUAL`
- 参与者：Micron Technology (SUBJECT)
- 数量：about 16% [price_change_percent]；about $1,049 [stock_price_start]；about $1,215 [stock_price_end]
- 时间：2026-06-24T00:00:00 / 2026-06-24T00:00:00 / DAY
- Evidence：`VERIFIED`
- Atomic：`atomic:471dce1e…` — Micron Technology stock gained roughly 15% in after-hours trading following the announcement of its fiscal Q3 2026 results.；**需拆分复核**（该簇人工核定 FP pair=39）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: Shares of memory specialist Micron Technology (MU 5.68%) jumped about 16% in after-hours trading on Wednesday, climbing from about $1,049 at Wednesday's close to about $1,215

#### 3.4 Micron Technology's core data center unit reported fiscal third quarter 2026 revenue growth of more than sevenfold year over year.

- Mention：`mention:296b4d87…`；Family：`FINANCIAL_PERFORMANCE`；Assertion：`ACTUAL`
- 参与者：core data center unit (SUBJECT)
- 数量：more than sevenfold [revenue_growth_yoy]
- 时间：fiscal Q3 2026 / UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:bb912535…` — Micron Technology reported fiscal Q3 2026 financial results, including revenue of $41.46 billion and adjusted earnings per share of $25.11.；**需拆分复核**（该簇人工核定 FP pair=327）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: The core data center unit grew even faster, with revenue up more than sevenfold year over year.

#### 3.5 Micron Technology reported fiscal third quarter 2026 revenue of approximately $41.5 billion for the period ended May 28, 2026.

- Mention：`mention:3846c08f…`；Family：`FINANCIAL_PERFORMANCE`；Assertion：`ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：about $41.5 billion [revenue]
- 时间：fiscal Q3 2026 / UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:bb912535…` — Micron Technology reported fiscal Q3 2026 financial results, including revenue of $41.46 billion and adjusted earnings per share of $25.11.；**需拆分复核**（该簇人工核定 FP pair=327）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: Micron's fiscal Q3 revenue, for the period ended May 28, 2026, came in at about $41.5 billion.

#### 3.6 Micron Technology reported fiscal third quarter 2026 capital expenditures of $7.1 billion.

- Mention：`mention:61cd32e5…`；Family：`FINANCIAL_PERFORMANCE`；Assertion：`ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：$7.1 billion [capital_expenditures]
- 时间：fiscal Q3 2026 / UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:c84ff842…` — Micron Technology reported fiscal third quarter 2026 capital expenditures of $7.1 billion.；**需拆分复核**（该簇人工核定 FP pair=0）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: Micron's capital expenditures climbed to $7.1 billion in fiscal Q3 alone

#### 3.7 Micron Technology provided guidance for fiscal fourth quarter 2026 gross margin of about 86% and adjusted earnings per share of $31.00, plus or minus $1.00.

- Mention：`mention:67a49018…`；Family：`GUIDANCE_EXPECTATION`；Assertion：`EXPECTED`
- 参与者：Micron (ACTOR)
- 数量：about 86% [gross_margin_guidance]；$31.00 [eps_guidance]；plus or minus $1.00 [eps_guidance_range]
- 时间：fiscal Q4 2026 / UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:a464025a…` — Micron provided financial guidance projecting $50 billion in revenue and $31 in EPS.；**需拆分复核**（该簇人工核定 FP pair=49）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: Management said its fiscal fourth-quarter gross margin is expected to climb to about 86%. Additionally, it guided for adjusted earnings per share of $31.00, plus or minus $1.00, for the period.

#### 3.8 Micron Technology provided guidance for fiscal fourth quarter 2026 revenue of $50 billion, plus or minus $1 billion.

- Mention：`mention:833a705b…`；Family：`GUIDANCE_EXPECTATION`；Assertion：`EXPECTED`
- 参与者：Micron (ACTOR)
- 数量：$50 billion [revenue_guidance]；plus or minus $1 billion [guidance_range]
- 时间：fiscal Q4 2026 / UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:a464025a…` — Micron provided financial guidance projecting $50 billion in revenue and $31 in EPS.；**需拆分复核**（该簇人工核定 FP pair=49）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: Micron guided fiscal Q4 revenue to $50 billion, plus or minus $1 billion

#### 3.9 Micron Technology's market capitalization exceeded $1.2 trillion.

- Mention：`mention:a2c6cccb…`；Family：`MARKET_MOVEMENT`；Assertion：`ACTUAL`
- 参与者：Micron (SUBJECT)
- 数量：$1.2 trillion [market_capitalization]
- 时间：2026-06-24T00:00:00 / 2026-06-24T00:00:00 / DAY
- Evidence：`VERIFIED`
- Atomic：`atomic:471dce1e…` — Micron Technology stock gained roughly 15% in after-hours trading following the announcement of its fiscal Q3 2026 results.；**需拆分复核**（该簇人工核定 FP pair=39）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: it puts Micron above a $1.2 trillion market capitalization.

#### 3.10 All four of Micron Technology's business units reported higher revenue in fiscal third quarter 2026 compared to both the prior quarter and the year-ago period.

- Mention：`mention:e208269c…`；Family：`FINANCIAL_PERFORMANCE`；Assertion：`ACTUAL`
- 参与者：Micron's business units (SUBJECT)
- 数量：—
- 时间：fiscal Q3 2026 / UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:bb912535…` — Micron Technology reported fiscal Q3 2026 financial results, including revenue of $41.46 billion and adjusted earnings per share of $25.11.；**需拆分复核**（该簇人工核定 FP pair=327）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: Topping it all off, all four of Micron's business units -- cloud memory, core data center, mobile and client, and automotive and embedded -- posted higher revenue than both the prior quarter and the year-ago period.

#### 3.11 Micron Technology reported fiscal third quarter 2026 GAAP gross margin of 84.6% and non-GAAP adjusted earnings per share of $25.11.

- Mention：`mention:f946142a…`；Family：`FINANCIAL_PERFORMANCE`；Assertion：`ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：84.6% [gross_margin_gaap]；$25.11 [eps_non_gaap]
- 时间：fiscal Q3 2026 / UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:bb912535…` — Micron Technology reported fiscal Q3 2026 financial results, including revenue of $41.46 billion and adjusted earnings per share of $25.11.；**需拆分复核**（该簇人工核定 FP pair=327）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: In addition, Micron's gross margin reached 84.6% on a GAAP basis, and non-GAAP (adjusted) earnings per share hit $25.11.

#### 3.12 Micron Technology's HBM4 product, built on 1-beta DRAM technology, is in high-volume shipments to its lead customer and qualification samples are being sent to additional end customers.

- Mention：`mention:fe40ed6e…`；Family：`PRODUCTION_SUPPLY`；Assertion：`ONGOING`
- 参与者：Micron (ACTOR)；HBM4 (TARGET)
- 数量：—
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:be9c6f8d…` — Micron Technology's HBM4 product, built on 1-beta DRAM technology, is in high-volume shipments to its lead customer and qualification samples are being sent to additional end customers.
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: HBM4, built on Micron's 1-beta DRAM technology, is already in high-volume shipments to its lead customer, with qualification samples now going to additional end customers.

### 4. Micron CEO Expects Memory Supply To Improve Gradually In 2028, But There's No 'Line Of Sight' When It Will Catch Up To Rising AI Demand

- 来源：benzinga.com
- 发布时间：2026-06-25T03:27:06+00:00
- Message ID：`doxatlas:raw_media:25387f96-b423-437c-b2cd-8652aa176bd8`
- 原文：https://www.benzinga.com/markets/tech/26/06/60089761/micron-ceo-sees-memory-supply-improving-by-2028-but-no-clear-end-in-sight-to-ai-demand-shortfallmicron-ceo-sees-memory-supply-improving-by-2028-but-no-clear-end-in-sight-to-ai-demand-shortfall
- Mention 数：8

#### 4.1 Micron shares closed at $1,048.51 on June 24, 2026, and rose to $1,213.96 in after-hours trading.

- Mention：`mention:0dab08b4…`；Family：`MARKET_MOVEMENT`；Assertion：`ACTUAL`
- 参与者：Micron (SUBJECT)
- 数量：$1,048.51 [closing_price]；$1,213.96 [after_hours_price]
- 时间：2026-06-24T00:00:00 / DAY
- Evidence：`VERIFIED`
- Atomic：`atomic:471dce1e…` — Micron Technology stock gained roughly 15% in after-hours trading following the announcement of its fiscal Q3 2026 results.；**需拆分复核**（该簇人工核定 FP pair=39）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: Price Action: Micron shares closed down 0.31% at $1,048.51 on Wednesday but surged 15.78% to $1,213.96 in after-hours trading, according to Benzinga Pro.

#### 4.2 Micron expects industry memory supply to improve gradually in 2028 but lacks visibility on when supply will meet AI demand.

- Mention：`mention:18b28d9d…`；Family：`GUIDANCE_EXPECTATION`；Assertion：`EXPECTED`
- 参与者：Micron (ACTOR)
- 数量：—
- 时间：2028 / UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:e306240c…` — Micron expects industry memory supply to improve gradually in 2028 but lacks visibility on when supply will meet AI demand.；**需拆分复核**（该簇人工核定 FP pair=3）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: “Even as we expect industry supply to improve gradually in 2028, we currently do not have line of sight as to when memory supply will be able to catch up with increasing demand,” he said.

#### 4.3 Micron's fiscal third-quarter 2026 revenue increased by 346% year over year.

- Mention：`mention:241c4c57…`；Family：`FINANCIAL_PERFORMANCE`；Assertion：`ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：346% [revenue_growth_yoy]
- 时间：fiscal third-quarter 2026 / UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:bb912535…` — Micron Technology reported fiscal Q3 2026 financial results, including revenue of $41.46 billion and adjusted earnings per share of $25.11.；**需拆分复核**（该簇人工核定 FP pair=327）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: Revenue jumped 346% year over year.

#### 4.4 Micron declared a quarterly dividend of 15 cents per share, payable on July 21, 2026, to shareholders of record on July 6, 2026.

- Mention：`mention:73bcd3e1…`；Family：`TRANSACTION_CAPITAL`；Assertion：`ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：15 cents per share [dividend_per_share]
- 时间：2026-06-24T00:00:00 / DAY
- Evidence：`VERIFIED`
- Atomic：`atomic:706b87b4…` — Micron declared a quarterly dividend of 15 cents per share, payable on July 21, 2026, to shareholders of record on July 6, 2026.
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: Micron also declared a quarterly dividend of 15 cents per share, payable July 21 to shareholders of record on July 6.

#### 4.5 Some semiconductor suppliers are reallocating cleanroom space from NAND flash to DRAM production, constraining NAND supply growth.

- Mention：`mention:9154bf87…`；Family：`PRODUCTION_SUPPLY`；Assertion：`ONGOING`
- 参与者：suppliers (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:7f051714…` — Some semiconductor suppliers are reallocating cleanroom space from NAND flash to DRAM production, constraining NAND supply growth.
- Package：`package:1446c5fa…` — Some semiconductor suppliers are reallocating cleanroom space from NAND flash to DRAM production, constraining NAND supply growth.

> Evidence: In NAND flash memory, Mehrotra said some suppliers are reallocating cleanroom space toward DRAM production, further constraining NAND supply growth.

#### 4.6 Micron completed 16 Strategic Customer Agreements spanning data center, consumer, and automotive markets.

- Mention：`mention:d99d176e…`；Family：`COMMERCIAL_OPERATION`；Assertion：`ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：16 [agreement_count]
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:d600f32b…` — Micron Technology announced 16 signed Strategic Customer Agreements, which are multi-year deals locking in volume and providing pricing visibility for memory supply.；**需拆分复核**（该簇人工核定 FP pair=84）
- Package：`package:d12945f7…` — Micron is investing at record levels in technology, products and supply.；**边界错误**：June 22 Anthropic partnership/investment is a different bounded announcement from the earnings-origin SCA/investment commentary.

> Evidence: Micron announced it has completed 16 Strategic Customer Agreements, or SCAs, spanning data center, consumer and automotive markets.

#### 4.7 Micron reported fiscal third-quarter 2026 revenue of $41.46 billion and adjusted earnings of $25.11 per share.

- Mention：`mention:f7e4e4fe…`；Family：`FINANCIAL_PERFORMANCE`；Assertion：`ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：$41.46 billion [revenue]；$25.11 per share [adjusted_earnings_per_share]
- 时间：fiscal third-quarter 2026 / UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:bb912535…` — Micron Technology reported fiscal Q3 2026 financial results, including revenue of $41.46 billion and adjusted earnings per share of $25.11.；**需拆分复核**（该簇人工核定 FP pair=327）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: Micron topped Wall Street expectations in the third quarter, reporting $41.46 billion in revenue and adjusted earnings of $25.11 per share, ahead of analyst estimates of $35.59 billion and $20.63 per share, respectively.

#### 4.8 Micron forecast fiscal fourth-quarter 2026 revenue of $50 billion (plus or minus $1 billion) and adjusted earnings of $31 per share (plus or minus $1).

- Mention：`mention:f8b393b2…`；Family：`GUIDANCE_EXPECTATION`；Assertion：`EXPECTED`
- 参与者：Micron (ACTOR)
- 数量：$50 billion [revenue_guidance]；$31 per share [adjusted_earnings_per_share_guidance]
- 时间：fiscal fourth-quarter 2026 / UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:a464025a…` — Micron provided financial guidance projecting $50 billion in revenue and $31 in EPS.；**需拆分复核**（该簇人工核定 FP pair=49）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: For the fourth quarter, the company forecast $50 billion in revenue, plus or minus $1 billion, and adjusted earnings of $31 per share, plus or minus $1, both above consensus estimates of $42.95 billion and $25.50 per share.

### 5. 4 Blowout Numbers From Micron's Earnings Investors Need To See

- 来源：fool.com
- 发布时间：2026-06-25T05:05:00+00:00
- Message ID：`doxatlas:raw_media:34fc2a61-fa6e-4db0-8e39-f862da0af41c`
- 原文：https://www.fool.com/investing/2026/06/25/x-blowout-numbers-from-microns-earnings-investors/
- Mention 数：10

#### 5.1 Micron introduced strategic customer agreements (SCA).

- Mention：`mention:1e402c6d…`；Family：`COMMERCIAL_OPERATION`；Assertion：`ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence：`TEXT_NOT_FOUND`
- Atomic：`atomic:d600f32b…` — Micron Technology announced 16 signed Strategic Customer Agreements, which are multi-year deals locking in volume and providing pricing visibility for memory supply.；**需拆分复核**（该簇人工核定 FP pair=84）
- Package：`package:d12945f7…` — Micron is investing at record levels in technology, products and supply.；**边界错误**：June 22 Anthropic partnership/investment is a different bounded announcement from the earnings-origin SCA/investment commentary.

> Evidence: Micron just introduced strategic customer agreements (SCA), longer-term contracts that typically last five years.

#### 5.2 Micron guided for fourth-quarter revenue to reach $50 billion.

- Mention：`mention:27e54db1…`；Family：`GUIDANCE_EXPECTATION`；Assertion：`EXPECTED`
- 参与者：Micron (ACTOR)
- 数量：$50 billion [revenue_guidance]
- 时间：fourth quarter / UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:a464025a…` — Micron provided financial guidance projecting $50 billion in revenue and $31 in EPS.；**需拆分复核**（该簇人工核定 FP pair=49）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: Micron's guidance called for similar growth in the fourth quarter, with revenue expected to reach $50 billion.

#### 5.3 Micron reported a quarterly gross margin of 84.6%, exceeding its guidance of 81%.

- Mention：`mention:361f285a…`；Family：`FINANCIAL_PERFORMANCE`；Assertion：`ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：84.6% [gross_margin]
- 时间：third-quarter / UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:486603c4…` — Micron reported a quarterly gross margin of 84.6%, exceeding its guidance of 81%.；**需拆分复核**（该簇人工核定 FP pair=23）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: Micron's gross margin came in at 84.6% in the quarter, ahead of its own guidance at 81%

#### 5.4 Micron expects fourth-quarter net income to exceed $40 billion.

- Mention：`mention:4024afe9…`；Family：`GUIDANCE_EXPECTATION`；Assertion：`EXPECTED`
- 参与者：Micron (ACTOR)
- 数量：$40 billion [net_income_guidance]
- 时间：fourth quarter / UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:13a527c7…` — Micron expects fourth-quarter net income to exceed $40 billion.
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: the company expects to top $40 billion in the fourth quarter

#### 5.5 Micron expects memory shortages to persist at least through 2028.

- Mention：`mention:5fc444a5…`；Family：`PRODUCTION_SUPPLY`；Assertion：`EXPECTED`
- 参与者：Micron (ACTOR)
- 数量：—
- 时间：2028-12-31T00:00:00 / YEAR
- Evidence：`VERIFIED`
- Atomic：`atomic:691794fb…` — Micron expects memory shortages to persist at least through 2028.；**需拆分复核**（该簇人工核定 FP pair=0）
- Package：`package:d33829fa…` — Micron expects memory shortages to persist at least through 2028.

> Evidence: memory shortages are expected to persist at least through 2028

#### 5.6 Micron reported quarterly net income of $28.2 billion.

- Mention：`mention:9cc917df…`；Family：`FINANCIAL_PERFORMANCE`；Assertion：`ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：$28.2 billion [net_income]
- 时间：third-quarter / UNKNOWN
- Evidence：`TEXT_NOT_FOUND`
- Atomic：`atomic:486603c4…` — Micron reported a quarterly gross margin of 84.6%, exceeding its guidance of 81%.；**需拆分复核**（该簇人工核定 FP pair=23）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: Micron produced $28.2 billion in net income.

#### 5.7 Micron reported quarterly revenue of $41.5 billion, representing 346% year-over-year growth.

- Mention：`mention:e8fe5222…`；Family：`FINANCIAL_PERFORMANCE`；Assertion：`ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：$41.5 billion [revenue]；346% [revenue_growth_yoy]
- 时间：third-quarter / UNKNOWN
- Evidence：`TEXT_NOT_FOUND`
- Atomic：`atomic:bb912535…` — Micron Technology reported fiscal Q3 2026 financial results, including revenue of $41.46 billion and adjusted earnings per share of $25.11.；**需拆分复核**（该簇人工核定 FP pair=327）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: Micron reported 346% revenue growth in the quarter to $41.5 billion.

#### 5.8 Micron reported a quarterly operating margin of 80.4%.

- Mention：`mention:eb479d52…`；Family：`FINANCIAL_PERFORMANCE`；Assertion：`ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：80.4% [operating_margin]
- 时间：third-quarter / UNKNOWN
- Evidence：`TEXT_NOT_FOUND`
- Atomic：`atomic:1e49f029…` — Micron reported a quarterly operating margin of 80.4%.
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: 80.4% was Micron's operating margin in the quarter.

#### 5.9 Micron guided for a future gross margin of 86%.

- Mention：`mention:ed187438…`；Family：`GUIDANCE_EXPECTATION`；Assertion：`EXPECTED`
- 参与者：Micron (ACTOR)
- 数量：86% [gross_margin_guidance]
- 时间：fourth quarter / UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:a464025a…` — Micron provided financial guidance projecting $50 billion in revenue and $31 in EPS.；**需拆分复核**（该簇人工核定 FP pair=49）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: management guided to a gross margin of 86%

#### 5.10 Micron's stock price increased by 15% after hours on Wednesday.

- Mention：`mention:fdad20e6…`；Family：`MARKET_MOVEMENT`；Assertion：`ACTUAL`
- 参与者：Micron (SUBJECT)
- 数量：15% [stock_price_change_percent]
- 时间：2026-06-24T00:00:00 / 2026-06-24T00:00:00 / DAY
- Evidence：`VERIFIED`
- Atomic：`atomic:471dce1e…` — Micron Technology stock gained roughly 15% in after-hours trading following the announcement of its fiscal Q3 2026 results.；**需拆分复核**（该簇人工核定 FP pair=39）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: sending the stock up 15% after hours on Wednesday

### 6. Micron Technology (MU) Announces Collaboration with Anthropic to Scale Next Generation AI Infrastructure

- 来源：finance.yahoo.com
- 发布时间：2026-06-25T06:24:06+00:00
- Message ID：`doxatlas:raw_media:d07de614-f3e3-431a-89dd-33bd139acd24`
- 原文：https://finance.yahoo.com/technology/ai/articles/micron-technology-mu-announces-collaboration-062406354.html
- Mention 数：3

#### 6.1 Needham increased its price target on Micron to $1,550 from $500 while maintaining a Buy rating.

- Mention：`mention:33245950…`；Family：`ANALYST_ACTION`；Assertion：`ACTUAL`
- 参与者：Needham (ACTOR)；Micron (SUBJECT)
- 数量：$1,550 [price_target]；$500 [previous_price_target]
- 时间：2026-06-22T00:00:00 / DAY
- Evidence：`VERIFIED`
- Atomic：`atomic:94faf738…` — Needham increased its price target on Micron to $1,550 from $500 while maintaining a Buy rating.；**需拆分复核**（该簇人工核定 FP pair=1）
- Package：`package:98341894…` — Needham increased its price target on Micron to $1,550 from $500 while maintaining a Buy rating.

> Evidence: In other news, TheFly reported on the same day that Needham increased its price target on Micron to $1,550 from $500 while maintaining a Buy rating on the stock, citing the continued strength of the memory market.

#### 6.2 Micron Technology entered into a strategic partnership and supply agreement with Anthropic, including a strategic investment in Anthropic's Series H funding round.

- Mention：`mention:4608a633…`；Family：`COMMERCIAL_OPERATION`；Assertion：`ACTUAL`
- 参与者：Micron Technology (ACTOR)；Anthropic (COUNTERPARTY)
- 数量：—
- 时间：2026-06-22T00:00:00 / DAY
- Evidence：`VERIFIED`
- Atomic：`atomic:61b1e7c3…` — Micron Technology entered into a strategic partnership and supply agreement with Anthropic, including a strategic investment in Anthropic's Series H funding round.
- Package：`package:d12945f7…` — Micron is investing at record levels in technology, products and supply.；**边界错误**：June 22 Anthropic partnership/investment is a different bounded announcement from the earnings-origin SCA/investment commentary.

> Evidence: On June 22, Micron announced a strategic partnership with Anthropic to scale next-generation AI infrastructure.

#### 6.3 Needham analyst forecasts strong market fundamentals to persist, driven by strong demand, robust pricing, and limited capacity additions, and cites optimism for long-term agreements.

- Mention：`mention:b978859f…`；Family：`GUIDANCE_EXPECTATION`；Assertion：`EXPECTED`
- 参与者：Needham analyst (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:94faf738…` — Needham increased its price target on Micron to $1,550 from $500 while maintaining a Buy rating.；**需拆分复核**（该簇人工核定 FP pair=1）
- Package：`package:98341894…` — Needham increased its price target on Micron to $1,550 from $500 while maintaining a Buy rating.

> Evidence: The analyst forecasts strong market fundamentals to persist, driven by the continued strong demand, a robust pricing environment, and limited capacity additions.

### 7. Micron CEO Says AI Memory Shortage Could Last Beyond 2028: These ETFs Are Positioned To Profit

- 来源：benzinga.com
- 发布时间：2026-06-25T08:24:51+00:00
- Message ID：`doxatlas:raw_media:bf5258ec-37f8-408b-ace2-8b016d16f725`
- 原文：https://www.benzinga.com/etfs/sector-etfs/26/06/60094351/micron-ceo-says-ai-memory-shortage-could-last-beyond-2028-these-etfs-are-positioned-to-profit
- Mention 数：5

#### 7.1 Micron reported revenue of $41.46 billion.

- Mention：`mention:6a847a3b…`；Family：`FINANCIAL_PERFORMANCE`；Assertion：`ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：$41.46 billion [revenue]
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:bb912535…` — Micron Technology reported fiscal Q3 2026 financial results, including revenue of $41.46 billion and adjusted earnings per share of $25.11.；**需拆分复核**（该簇人工核定 FP pair=327）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: Revenue of $41.46 billion, up roughly 346% from a year earlier and well ahead of Wall Street estimates of $35.6 billion.

#### 7.2 Micron indicated that AI memory supply constraints could persist beyond 2028.

- Mention：`mention:7044939e…`；Family：`PRODUCTION_SUPPLY`；Assertion：`EXPECTED`
- 参与者：Micron (ACTOR)
- 数量：—
- 时间：2028-12-31T00:00:00 / YEAR
- Evidence：`VERIFIED`
- Atomic：`atomic:691794fb…` — Micron expects memory shortages to persist at least through 2028.；**需拆分复核**（该簇人工核定 FP pair=0）
- Package：`package:d33829fa…` — Micron expects memory shortages to persist at least through 2028.

> Evidence: Micron CEO Says AI Memory Shortage Could Last Beyond 2028

#### 7.3 Micron reported adjusted earnings of $25.11 per share.

- Mention：`mention:aa02ea66…`；Family：`FINANCIAL_PERFORMANCE`；Assertion：`ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：$25.11 per share [adjusted_earnings_per_share]
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:bb912535…` — Micron Technology reported fiscal Q3 2026 financial results, including revenue of $41.46 billion and adjusted earnings per share of $25.11.；**需拆分复核**（该簇人工核定 FP pair=327）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: Adjusted earnings of $25.11 per share, which topped expectations

#### 7.4 Micron provided fourth-quarter guidance for $50 billion in revenue and $31 per share in earnings.

- Mention：`mention:c73aaef9…`；Family：`GUIDANCE_EXPECTATION`；Assertion：`ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：$50 billion in revenue [revenue_guidance]；$31 per share in earnings [earnings_per_share_guidance]
- 时间：Fourth-quarter / UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:a464025a…` — Micron provided financial guidance projecting $50 billion in revenue and $31 in EPS.；**需拆分复核**（该簇人工核定 FP pair=49）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: Fourth-quarter guidance for $50 billion in revenue and - $31 per share in earnings came in significantly above consensus forecasts.

#### 7.5 High-bandwidth memory remains in tight supply due to continued investment by hyperscalers and enterprises in AI infrastructure.

- Mention：`mention:ed9f8bf1…`；Family：`PRODUCTION_SUPPLY`；Assertion：`ONGOING`
- 参与者：High-bandwidth memory (HBM) (SUBJECT)；hyperscalers and enterprises (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:d3bf058d…` — High-bandwidth memory remains in tight supply due to continued investment by hyperscalers and enterprises in AI infrastructure.
- Package：`package:1376ae52…` — High-bandwidth memory remains in tight supply due to continued investment by hyperscalers and enterprises in AI infrastructure.

> Evidence: High-bandwidth memory (HBM), a critical component used alongside advanced AI processors, remains in tight supply as hyperscalers and enterprises continue pouring money into AI infrastructure.

### 8. Tesla's Optimus Could Become A Bigger Memory Customer Than Its Cars, If Micron Is Right

- 来源：benzinga.com
- 发布时间：2026-06-25T08:30:39+00:00
- Message ID：`doxatlas:raw_media:005d3637-8724-483c-9077-7f643d3fa481`
- 原文：https://www.benzinga.com/trading-ideas/long-ideas/26/06/60094569/tesla-optimus-could-become-a-bigger-memory-customer-than-its-cars-if-micron-is-right
- Mention 数：5

#### 8.1 Micron does not have line of sight as to when memory supply will be able to catch up with increasing demand.

- Mention：`mention:02ddb457…`；Family：`GUIDANCE_EXPECTATION`；Assertion：`ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:e306240c…` — Micron expects industry memory supply to improve gradually in 2028 but lacks visibility on when supply will meet AI demand.；**需拆分复核**（该簇人工核定 FP pair=3）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: “We currently do not have line of sight as to when memory supply will be able to catch up with increasing demand,”

#### 8.2 Micron believes AI-driven demand is outpacing the industry’s ability to add new supply.

- Mention：`mention:1e0b37d5…`；Family：`GUIDANCE_EXPECTATION`；Assertion：`EXPECTED`
- 参与者：Micron (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:e306240c…` — Micron expects industry memory supply to improve gradually in 2028 but lacks visibility on when supply will meet AI demand.；**需拆分复核**（该簇人工核定 FP pair=3）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: The company believes AI-driven demand is outpacing the industry’s ability to add new supply

#### 8.3 Micron expects a sustained, substantial multi-decade memory demand cycle to begin in the latter part of this decade.

- Mention：`mention:8d3ee1e0…`；Family：`GUIDANCE_EXPECTATION`；Assertion：`EXPECTED`
- 参与者：Micron (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:efd8d4f3…` — Micron expects a sustained, substantial multi-decade memory demand cycle to begin in the latter part of this decade.；**需拆分复核**（该簇人工核定 FP pair=1）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: “We expect a sustained, substantial multi-decade memory demand cycle to begin in the latter part of this decade.”

#### 8.4 Humanoid robots carry 10 times the amount of memory as an average L2+ vehicle.

- Mention：`mention:e62380e8…`；Family：`PRODUCT_SCIENCE`；Assertion：`ACTUAL`
- 参与者：Humanoid robots (SUBJECT)；average L2+ vehicle (TARGET)
- 数量：10 times [memory_ratio]
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:b3f22db9…` — Humanoid robots carry 10 times the amount of memory as an average L2+ vehicle.
- Package：`package:dd8916aa…` — Humanoid robots carry 10 times the amount of memory as an average L2+ vehicle.

> Evidence: “Humanoid robots carry 10 times the amount of memory as an average L2+ vehicle,” Mehrotra said.

#### 8.5 Micron expects tight memory market conditions to persist beyond calendar 2027.

- Mention：`mention:ede26d77…`；Family：`GUIDANCE_EXPECTATION`；Assertion：`EXPECTED`
- 参与者：Micron (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:efd8d4f3…` — Micron expects a sustained, substantial multi-decade memory demand cycle to begin in the latter part of this decade.；**需拆分复核**（该簇人工核定 FP pair=1）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: with Micron now expecting tight memory market conditions to persist beyond calendar 2027.

### 9. Micron posts record results as AI boom drives 15-fold jump in net profit

- 来源：euronews.com
- 发布时间：2026-06-25T08:43:07+00:00
- Message ID：`doxatlas:raw_media:2b5bba2a-e472-4a1f-a341-5be6e4286dd1`
- 原文：https://www.euronews.com/business/2026/06/25/micron-posts-record-results-as-ai-boom-drives-15-fold-jump-in-net-profit
- Mention 数：7

#### 9.1 Micron reported third-quarter revenue of $41.4 billion.

- Mention：`mention:07737c83…`；Family：`FINANCIAL_PERFORMANCE`；Assertion：`ACTUAL`
- 参与者：Micron Technology (ACTOR)
- 数量：$41.4 billion [revenue]
- 时间：third quarter / UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:bb912535…` — Micron Technology reported fiscal Q3 2026 financial results, including revenue of $41.46 billion and adjusted earnings per share of $25.11.；**需拆分复核**（该簇人工核定 FP pair=327）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: revenue in the third quarter reached $41.4 billion (€36.5bn), more than four times the $9.3 billion (€8.2bn) it recorded in the same period last year

#### 9.2 Micron posted net income of $28.24 billion and adjusted earnings of $25.11 per share for the quarter.

- Mention：`mention:11cf0c02…`；Family：`FINANCIAL_PERFORMANCE`；Assertion：`ACTUAL`
- 参与者：Micron Technology (ACTOR)
- 数量：$28.24 billion [net_income]；$25.11 a share [adjusted_earnings_per_share]
- 时间：third quarter / UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:bb912535…` — Micron Technology reported fiscal Q3 2026 financial results, including revenue of $41.46 billion and adjusted earnings per share of $25.11.；**需拆分复核**（该簇人工核定 FP pair=327）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: The Idaho-based group posted net income of $28.24 billion (€24.9bn), or $24.67 per share, against less than $2 billion (€1.7bn) a year ago. Adjusted earnings of $25.11 a share sailed past the $20.49 expected.

#### 9.3 Micron reported a gross margin of around 85% for the quarter.

- Mention：`mention:3066f2a7…`；Family：`FINANCIAL_PERFORMANCE`；Assertion：`ACTUAL`
- 参与者：Micron Technology (ACTOR)
- 数量：around 85% [gross_margin]
- 时间：third quarter / UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:486603c4…` — Micron reported a quarterly gross margin of 84.6%, exceeding its guidance of 81%.；**需拆分复核**（该簇人工核定 FP pair=23）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: The company reported a gross margin of around 85% for the quarter, a level that now rivals or exceeds those of far larger technology names such as Nvidia and Meta

#### 9.4 Micron lifted planned capital spending to about $27 billion for this fiscal year.

- Mention：`mention:4047216c…`；Family：`GUIDANCE_EXPECTATION`；Assertion：`PLANNED`
- 参与者：Micron Technology (ACTOR)
- 数量：about $27 billion [capital_spending]
- 时间：this fiscal year / UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:a464025a…` — Micron provided financial guidance projecting $50 billion in revenue and $31 in EPS.；**需拆分复核**（该簇人工核定 FP pair=49）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: It is ramping up investment to match, lifting planned capital spending to about $27 billion (€23.7bn) this fiscal year and signalling a further jump in 2027, management told analysts during the earnings call.

#### 9.5 Micron's entire 2026 output of high-bandwidth memory chips is sold out under fixed-price contracts.

- Mention：`mention:64446444…`；Family：`COMMERCIAL_OPERATION`；Assertion：`ACTUAL`
- 参与者：Micron Technology (ACTOR)
- 数量：—
- 时间：2026 / UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:357ca01b…` — Micron's entire 2026 output of high-bandwidth memory chips is sold out under fixed-price contracts.
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: Micron has said its entire 2026 output of these chips is already sold out under fixed-price contracts.

#### 9.6 Micron shares rose more than 15% in after-hours trading to around $1,213.

- Mention：`mention:c9a9b539…`；Family：`MARKET_MOVEMENT`；Assertion：`ACTUAL`
- 参与者：Micron shares (SUBJECT)
- 数量：more than 15% [share_price_change_percent]；around $1,213 [share_price]
- 时间：2026-06-24T00:00:00 / 2026-06-24T00:00:00 / DAY
- Evidence：`VERIFIED`
- Atomic：`atomic:471dce1e…` — Micron Technology stock gained roughly 15% in after-hours trading following the announcement of its fiscal Q3 2026 results.；**需拆分复核**（该簇人工核定 FP pair=39）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: Micron shares rose more than 15% in after-hours trading to around $1,213, leaving the company valued at roughly $1.16 trillion (€1tn).

#### 9.7 Micron expects revenue of around $50 billion and adjusted earnings of roughly $31 per share for the current quarter.

- Mention：`mention:f3f68ef7…`；Family：`GUIDANCE_EXPECTATION`；Assertion：`EXPECTED`
- 参与者：Micron Technology (ACTOR)
- 数量：around $50 billion [revenue]；roughly $31 a share [adjusted_earnings_per_share]
- 时间：current quarter / UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:a464025a…` — Micron provided financial guidance projecting $50 billion in revenue and $31 in EPS.；**需拆分复核**（该簇人工核定 FP pair=49）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: The company expects revenue of around $50 billion (€44bn) in the current quarter and adjusted earnings of roughly $31 a share

### 10. Micron Is Spending Billions On New Fabs—And Still Says It'll Return 100% Of Excess Cash To Shareholders

- 来源：benzinga.com
- 发布时间：2026-06-25T08:58:06+00:00
- Message ID：`doxatlas:raw_media:4fcc4fb0-ab7d-4157-8e65-a09ef40a7581`
- 原文：https://www.benzinga.com/trading-ideas/long-ideas/26/06/60095272/micron-is-spending-billions-on-new-fabs-and-still-says-itll-return-100-of-excess-cash-to-shareholders
- Mention 数：6

#### 10.1 Micron projects its balance sheet will strengthen further despite increased investment in technology and capacity.

- Mention：`mention:0929b35b…`；Family：`FINANCIAL_PERFORMANCE`；Assertion：`EXPECTED`
- 参与者：Micron (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:b304861b…` — Micron projects its balance sheet will strengthen further despite increased investment in technology and capacity.
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: “Our balance sheet has never been stronger, and we project it to strengthen further even as we increase investment in technology and needed capacity,”

#### 10.2 Micron expects quarterly capital expenditures in fiscal 2027 to exceed fiscal fourth-quarter 2026 levels, with more than half of the increase coming from construction spending.

- Mention：`mention:5bf077b1…`；Family：`GUIDANCE_EXPECTATION`；Assertion：`EXPECTED`
- 参与者：Micron (ACTOR)
- 数量：—
- 时间：fiscal 2027 / UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:487518cb…` — Micron expects quarterly capital expenditures in fiscal 2027 to exceed fiscal fourth-quarter 2026 levels, with more than half of the increase coming from construction spending.；**需拆分复核**（该簇人工核定 FP pair=0）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: Chief Financial Officer Mark Murphy said the company expects quarterly capital expenditures in fiscal 2027 to exceed fiscal fourth-quarter levels as Micron accelerates construction of new clean-room capacity to meet long-term AI demand.

#### 10.3 Micron expects fiscal fourth-quarter 2026 capital expenditures of around $10 billion and total fiscal 2026 capital spending of approximately $27 billion.

- Mention：`mention:5f261a44…`；Family：`GUIDANCE_EXPECTATION`；Assertion：`EXPECTED`
- 参与者：Micron (ACTOR)
- 数量：$10 billion [capex_q4_fy2026]；$27 billion [capex_total_fy2026]
- 时间：fiscal 2026 / UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:fe9b7445…` — Micron expects fiscal fourth-quarter 2026 capital expenditures of around $10 billion and total fiscal 2026 capital spending of approximately $27 billion.；**需拆分复核**（该簇人工核定 FP pair=2）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: Micron expects fiscal fourth-quarter capital expenditures of around $10 billion, bringing total fiscal 2026 capital spending to approximately $27 billion.

#### 10.4 Micron is expanding manufacturing capacity including leading-edge DRAM fabs in Idaho and New York, continued expansion in Taiwan and Singapore, and additional advanced packaging capacity for HBM products.

- Mention：`mention:6a72c543…`；Family：`PRODUCTION_SUPPLY`；Assertion：`ONGOING`
- 参与者：Micron (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:8efb7edb…` — Micron is expanding manufacturing capacity including leading-edge DRAM fabs in Idaho and New York, continued expansion in Taiwan and Singapore, and additional advanced packaging capacity for HBM products.
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: The investments include leading-edge DRAM fabs in Idaho and New York, continued expansion in Taiwan and Singapore, and additional advanced packaging capacity aimed at supporting next-generation high-bandwidth memory (HBM) products

#### 10.5 Micron has announced strategic customer agreements providing long-term demand visibility.

- Mention：`mention:7e759e90…`；Family：`COMMERCIAL_OPERATION`；Assertion：`ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:d600f32b…` — Micron Technology announced 16 signed Strategic Customer Agreements, which are multi-year deals locking in volume and providing pricing visibility for memory supply.；**需拆分复核**（该簇人工核定 FP pair=84）
- Package：`package:d12945f7…` — Micron is investing at record levels in technology, products and supply.；**边界错误**：June 22 Anthropic partnership/investment is a different bounded announcement from the earnings-origin SCA/investment commentary.

> Evidence: That confidence is also backed by Micron’s recently announced strategic customer agreements, which provide long-term demand visibility and support the company’s plans to invest aggressively in manufacturing while maintaining an increasingly shareholder-friendly capital allocat…

#### 10.6 Micron plans to return 100% of its excess cash to shareholders beginning December 9, 2026.

- Mention：`mention:cc18cd36…`；Family：`TRANSACTION_CAPITAL`；Assertion：`PLANNED`
- 参与者：Micron (ACTOR)；shareholders (TARGET)
- 数量：100% [excess_cash_return_pct]
- 时间：2026-12-09T00:00:00 / DAY
- Evidence：`VERIFIED`
- Atomic：`atomic:61a33de5…` — Micron plans to return 100% of its excess cash to shareholders beginning December 9, 2026.；**需拆分复核**（该簇人工核定 FP pair=0）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: Murphy said that beginning Dec. 9, 2026—the second anniversary of the company’s definitive CHIPS Act agreements—Micron plans to increase capital returns over time. “We expect to return 100% of our excess cash to shareholders,”

### 11. Dow Surges 250 Points; Micron Posts Upbeat Q3 Earnings

- 来源：Benzinga
- 发布时间：2026-06-25T09:40:55+00:00
- Message ID：`doxatlas:raw_media:fce73732-1c68-4d0f-9d7f-c5800393ffff`
- 原文：https://finnhub.io/api/news?id=7316a88ccda79b3354a49965fa82f15ed81fe3722e51491463cf118f097a0143
- Mention 数：7

#### 11.1 Technology sector shares increased by 1.6%.

- Mention：`mention:050b8d30…`；Family：`MARKET_MOVEMENT`；Assertion：`ACTUAL`
- 参与者：Tech shares (SUBJECT)
- 数量：1.6% [sector_change_percent]
- 时间：2026-06-24T00:00:00 / 2026-06-24T00:00:00 / DAY
- Evidence：`VERIFIED`
- Atomic：`atomic:8adcefd7…` — Technology sector shares increased by 1.6%.
- Package：`package:7477ca5e…` — Technology sector shares increased by 1.6%.

> Evidence: Tech shares jump 1.6%

#### 11.2 The S&P 500 index increased by 0.6% to close at 7,401.17.

- Mention：`mention:18638905…`；Family：`MARKET_MOVEMENT`；Assertion：`ACTUAL`
- 参与者：S&P 500 (SUBJECT)
- 数量：0.6% [index_change_percent]；7,401.17 [index_close_value]
- 时间：2026-06-24T00:00:00 / 2026-06-24T00:00:00 / DAY
- Evidence：`VERIFIED`
- Atomic：`atomic:57e65b6d…` — The S&P 500 index increased by 0.6% to close at 7,401.17.
- Package：`package:ddaf0a70…` — The S&P 500 index increased by 0.6% to close at 7,401.17.

> Evidence: S&P 500 gained 0.6% to 7,401.17

#### 11.3 The NASDAQ index increased by 0.7% to close at 25,654.49.

- Mention：`mention:3e5ed70e…`；Family：`MARKET_MOVEMENT`；Assertion：`ACTUAL`
- 参与者：NASDAQ (SUBJECT)
- 数量：0.7% [index_change_percent]；25,654.49 [index_close_value]
- 时间：2026-06-24T00:00:00 / 2026-06-24T00:00:00 / DAY
- Evidence：`VERIFIED`
- Atomic：`atomic:3a47d57b…` — The NASDAQ index increased by 0.7% to close at 25,654.49.
- Package：`package:1cebbc3b…` — The NASDAQ index increased by 0.7% to close at 25,654.49.

> Evidence: NASDAQ up 0.7% to 25,654.49

#### 11.4 Communication services sector stocks decreased by 1.9%.

- Mention：`mention:546e6b3c…`；Family：`MARKET_MOVEMENT`；Assertion：`ACTUAL`
- 参与者：communication services stocks (SUBJECT)
- 数量：1.9% [sector_change_percent]
- 时间：2026-06-24T00:00:00 / 2026-06-24T00:00:00 / DAY
- Evidence：`VERIFIED`
- Atomic：`atomic:ff519c2c…` — Communication services sector stocks decreased by 1.9%.
- Package：`package:9885ef27…` — Communication services sector stocks decreased by 1.9%.

> Evidence: communication services stocks fell 1.9%

#### 11.5 Micron reported Q3 earnings that exceeded market expectations.

- Mention：`mention:bd8fe6d0…`；Family：`FINANCIAL_PERFORMANCE`；Assertion：`ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：—
- 时间：Q3 / UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:bb912535…` — Micron Technology reported fiscal Q3 2026 financial results, including revenue of $41.46 billion and adjusted earnings per share of $25.11.；**需拆分复核**（该簇人工核定 FP pair=327）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: Micron beats expectations with Q3 results

#### 11.6 The Dow Jones index increased by 0.5% to close at 52,107.28.

- Mention：`mention:e3b4594c…`；Family：`MARKET_MOVEMENT`；Assertion：`ACTUAL`
- 参与者：Dow Jones index (SUBJECT)
- 数量：0.5% [index_change_percent]；52,107.28 [index_close_value]
- 时间：2026-06-24T00:00:00 / 2026-06-24T00:00:00 / DAY
- Evidence：`VERIFIED`
- Atomic：`atomic:df838df0…` — The Dow Jones index increased by 0.5% to close at 52,107.28.；**需拆分复核**（该簇人工核定 FP pair=1）
- Package：`package:d8100b7e…` — The Dow Jones index increased by 0.5% to close at 52,107.28.

> Evidence: The Dow Jones index rose 0.5% to 52,107.28

#### 11.7 Triller Group Inc stock price increased by 259%.

- Mention：`mention:ec496c78…`；Family：`MARKET_MOVEMENT`；Assertion：`ACTUAL`
- 参与者：Triller Group Inc (SUBJECT)
- 数量：259% [stock_change_percent]
- 时间：2026-06-24T00:00:00 / 2026-06-24T00:00:00 / DAY
- Evidence：`VERIFIED`
- Atomic：`atomic:75608745…` — Triller Group Inc stock price increased by 259%.
- Package：`package:0ce5f930…` — Triller Group Inc stock price increased by 259%.

> Evidence: Triller Group Inc up 259%

### 12. These Analysts Boost Their Forecasts On Micron Technology After Better-Than-Expected Q3 Results

- 来源：benzinga.com
- 发布时间：2026-06-25T10:14:58+00:00
- Message ID：`doxatlas:raw_media:5d890b9d-c789-4a99-87d6-8a34a3ea4cc2`
- 原文：https://www.benzinga.com/analyst-stock-ratings/price-target/26/06/60100007/these-analysts-boost-their-forecasts-on-micron-technology-after-better-than-expected-q3-results
- Mention 数：6

#### 12.1 Micron expects fourth-quarter revenue of $50 billion, plus or minus $1 billion.

- Mention：`mention:1d90c0a1…`；Family：`GUIDANCE_EXPECTATION`；Assertion：`EXPECTED`
- 参与者：Micron (ACTOR)
- 数量：$50 billion [revenue]
- 时间：Q4 FY2026 / UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:a464025a…` — Micron provided financial guidance projecting $50 billion in revenue and $31 in EPS.；**需拆分复核**（该簇人工核定 FP pair=49）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: Micron expects fourth-quarter revenue of $50 billion, plus or minus $1 billion, versus estimates of $42.95 billion.

#### 12.2 Micron is investing at record levels in technology, products and supply.

- Mention：`mention:5d71ec2f…`；Family：`COMMERCIAL_OPERATION`；Assertion：`ONGOING`
- 参与者：Micron (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:3a4cce33…` — Micron is investing at record levels in technology, products and supply.；**需拆分复核**（该簇人工核定 FP pair=0）
- Package：`package:d12945f7…` — Micron is investing at record levels in technology, products and supply.；**边界错误**：June 22 Anthropic partnership/investment is a different bounded announcement from the earnings-origin SCA/investment commentary.

> Evidence: Micron is investing at record levels in technology, products and supply to address our customers’ rapidly growing demand.

#### 12.3 Micron expects fourth-quarter adjusted earnings of $31 per share, plus or minus $1.

- Mention：`mention:7634f7ce…`；Family：`GUIDANCE_EXPECTATION`；Assertion：`EXPECTED`
- 参与者：Micron (ACTOR)
- 数量：$31 per share [adjusted_earnings_per_share]
- 时间：Q4 FY2026 / UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:a464025a…` — Micron provided financial guidance projecting $50 billion in revenue and $31 in EPS.；**需拆分复核**（该簇人工核定 FP pair=49）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: The company anticipates fourth-quarter adjusted earnings of $31 per share, plus or minus $1, versus estimates of $25.50 per share.

#### 12.4 Micron reported third-quarter adjusted earnings of $25.11 per share.

- Mention：`mention:837a219a…`；Family：`FINANCIAL_PERFORMANCE`；Assertion：`ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：$25.11 per share [adjusted_earnings_per_share]
- 时间：Q3 FY2026 / UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:bb912535…` — Micron Technology reported fiscal Q3 2026 financial results, including revenue of $41.46 billion and adjusted earnings per share of $25.11.；**需拆分复核**（该簇人工核定 FP pair=327）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: The semiconductor company posted adjusted earnings of $25.11 per share, beating analyst estimates of $20.63 per share.

#### 12.5 Micron's multi-year Strategic Customer Agreements will significantly enhance the durability and predictability of its financial performance.

- Mention：`mention:c2938c93…`；Family：`COMMERCIAL_OPERATION`；Assertion：`EXPECTED`
- 参与者：Micron (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:97af10af…` — Micron's multi-year Strategic Customer Agreements will significantly enhance the durability and predictability of its financial performance.
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: We believe our multi-year Strategic Customer Agreements will significantly enhance the durability and predictability of Micron’s strong financial performance

#### 12.6 Micron reported third-quarter revenue of $41.46 billion.

- Mention：`mention:f2042616…`；Family：`FINANCIAL_PERFORMANCE`；Assertion：`ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：$41.46 billion [revenue]
- 时间：Q3 FY2026 / UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:bb912535…` — Micron Technology reported fiscal Q3 2026 financial results, including revenue of $41.46 billion and adjusted earnings per share of $25.11.；**需拆分复核**（该簇人工核定 FP pair=327）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: Micron reported third-quarter revenue of $41.46 billion, exceeding analyst estimates of $35.59 billion

### 13. Investors bet on AI again after Micron reports 346% sales jump

- 来源：edition.cnn.com
- 发布时间：2026-06-25T11:10:14+00:00
- Message ID：`doxatlas:raw_media:1aaec309-8ccb-42d6-b974-b4aa3bf2d43c`
- 原文：https://edition.cnn.com/2026/06/25/business/micron-results-ai-stocks-volatility
- Mention 数：16

#### 13.1 Micron Technology's stock rose more than 16% in pre-market trade on Thursday.

- Mention：`mention:0911a195…`；Family：`MARKET_MOVEMENT`；Assertion：`ACTUAL`
- 参与者：Micron Technology (SUBJECT)
- 数量：more than 16% [stock_change]
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY
- Evidence：`VERIFIED`
- Atomic：`atomic:471dce1e…` — Micron Technology stock gained roughly 15% in after-hours trading following the announcement of its fiscal Q3 2026 results.；**需拆分复核**（该簇人工核定 FP pair=39）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: sending its stock up by more than 16% in pre-market trade Thursday.

#### 13.2 SK Hynix stock fell more than 12% on Tuesday.

- Mention：`mention:1378ec6e…`；Family：`MARKET_MOVEMENT`；Assertion：`ACTUAL`
- 参与者：SK Hynix (SUBJECT)
- 数量：more than 12% [stock_change]
- 时间：2026-06-23T00:00:00 / 2026-06-23T00:00:00 / DAY
- Evidence：`TEXT_NOT_FOUND`
- Atomic：`atomic:12756db6…` — SK Hynix stock fell more than 12% on Tuesday.
- Package：`package:381ba50e…` — SK Hynix stock fell more than 12% on Tuesday.

> Evidence: SK Hynix ... tumbled more than 12%

#### 13.3 Micron Technology reported third-quarter revenue growth of 346% year-over-year.

- Mention：`mention:212a5e2e…`；Family：`FINANCIAL_PERFORMANCE`；Assertion：`ACTUAL`
- 参与者：Micron Technology (ACTOR)
- 数量：346% [revenue_growth]
- 时间：third quarter / UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:bb912535…` — Micron Technology reported fiscal Q3 2026 financial results, including revenue of $41.46 billion and adjusted earnings per share of $25.11.；**需拆分复核**（该簇人工核定 FP pair=327）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: Revenues soared 346% over the same period.

#### 13.4 Europe’s Stoxx 600 index rose 0.6% by early afternoon on Thursday.

- Mention：`mention:255ef47c…`；Family：`MARKET_MOVEMENT`；Assertion：`ACTUAL`
- 参与者：Stoxx 600 (SUBJECT)
- 数量：0.6% [index_change]
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY
- Evidence：`VERIFIED`
- Atomic：`atomic:6babebc5…` — Europe’s Stoxx 600 index rose 0.6% by early afternoon on Thursday.
- Package：`package:cf38c56e…` — Europe’s Stoxx 600 index rose 0.6% by early afternoon on Thursday.

> Evidence: Europe’s benchmark Stoxx 600 index was up 0.6% by early afternoon local time.

#### 13.5 Dow Jones Industrial Average rose 0.3% in pre-market trade on Thursday.

- Mention：`mention:2b4f7b43…`；Family：`MARKET_MOVEMENT`；Assertion：`ACTUAL`
- 参与者：Dow (SUBJECT)
- 数量：0.3% [index_change_dow]
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY
- Evidence：`VERIFIED`
- Atomic：`atomic:df838df0…` — The Dow Jones index increased by 0.5% to close at 52,107.28.；**需拆分复核**（该簇人工核定 FP pair=1）
- Package：`package:d8100b7e…` — The Dow Jones index increased by 0.5% to close at 52,107.28.

> Evidence: Dow was pointing up by 0.3%

#### 13.6 Samsung stock fell more than 12% on Tuesday.

- Mention：`mention:2e20e3e5…`；Family：`MARKET_MOVEMENT`；Assertion：`ACTUAL`
- 参与者：Samsung (SUBJECT)
- 数量：more than 12% [stock_change]
- 时间：2026-06-23T00:00:00 / 2026-06-23T00:00:00 / DAY
- Evidence：`TEXT_NOT_FOUND`
- Atomic：`atomic:a5ea3a84…` — Samsung stock fell more than 12% on Tuesday.
- Package：`package:8b2aa282…` — Samsung stock fell more than 12% on Tuesday.

> Evidence: Samsung ... tumbled more than 12%

#### 13.7 SK Hynix disclosed plans for a listing on the US Nasdaq.

- Mention：`mention:2f5dbd90…`；Family：`TRANSACTION_CAPITAL`；Assertion：`ACTUAL`
- 参与者：SK Hynix (ACTOR)
- 数量：—
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY
- Evidence：`VERIFIED`
- Atomic：`atomic:a21c0d5b…` — SK Hynix disclosed plans for a listing on the US Nasdaq.；**需拆分复核**（该簇人工核定 FP pair=0）
- Package：`package:87feed09…` — SK Hynix disclosed plans for a listing on the US Nasdaq.

> Evidence: the company disclosed plans for a listing on the US Nasdaq.

#### 13.8 US Nasdaq rose 2.15% in pre-market trade on Thursday.

- Mention：`mention:40466af0…`；Family：`MARKET_MOVEMENT`；Assertion：`ACTUAL`
- 参与者：Nasdaq (SUBJECT)
- 数量：2.15% [index_change_nasdaq]
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY
- Evidence：`TEXT_NOT_FOUND`
- Atomic：`atomic:4095e0e8…` — US Nasdaq rose 2.15% in pre-market trade on Thursday.
- Package：`package:f2901e24…` — US Nasdaq rose 2.15% in pre-market trade on Thursday.

> Evidence: the tech-heavy Nasdaq ... were up 2.15% ... in pre-market trade

#### 13.9 Micron Technology's customers committed $22 billion to secure supplies of its chips.

- Mention：`mention:6eb68ef8…`；Family：`COMMERCIAL_OPERATION`；Assertion：`ACTUAL`
- 参与者：customers (ACTOR)；Micron Technology (TARGET)
- 数量：$22 billion [commitment_value]
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:d600f32b…` — Micron Technology announced 16 signed Strategic Customer Agreements, which are multi-year deals locking in volume and providing pricing visibility for memory supply.；**需拆分复核**（该簇人工核定 FP pair=84）
- Package：`package:d12945f7…` — Micron is investing at record levels in technology, products and supply.；**边界错误**：June 22 Anthropic partnership/investment is a different bounded announcement from the earnings-origin SCA/investment commentary.

> Evidence: Micron said in its results that its customers had committed $22 billion to secure supplies of its chips.

#### 13.10 S&P 500 rose 0.75% in pre-market trade on Thursday.

- Mention：`mention:72c6e132…`；Family：`MARKET_MOVEMENT`；Assertion：`ACTUAL`
- 参与者：S&P 500 (SUBJECT)
- 数量：0.75% [index_change_sp500]
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY
- Evidence：`TEXT_NOT_FOUND`
- Atomic：`atomic:867ac3b9…` — S&P 500 rose 0.75% in pre-market trade on Thursday.；**需拆分复核**（该簇人工核定 FP pair=1）
- Package：`package:45e9c8e1…` — S&P 500 rose 0.75% in pre-market trade on Thursday.

> Evidence: S&P 500 were up ... 0.75% ... in pre-market trade

#### 13.11 Micron Technology's stock fell 13% on Tuesday.

- Mention：`mention:7663bc43…`；Family：`MARKET_MOVEMENT`；Assertion：`ACTUAL`
- 参与者：Micron Technology (SUBJECT)
- 数量：fell 13% [stock_change]
- 时间：2026-06-23T00:00:00 / 2026-06-23T00:00:00 / DAY
- Evidence：`VERIFIED`
- Atomic：`atomic:aa540ad4…` — Micron Technology's stock fell 13% on Tuesday.
- Package：`package:5d9a1bbe…` — Micron Technology's stock fell 13% on Tuesday.

> Evidence: Micron’s stock fell 13% on Tuesday

#### 13.12 Micron Technology reported third-quarter profit of $28.2 billion.

- Mention：`mention:af2773b3…`；Family：`FINANCIAL_PERFORMANCE`；Assertion：`ACTUAL`
- 参与者：Micron Technology (ACTOR)
- 数量：$28.2 billion [profit]
- 时间：third quarter / UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:05958579…` — Micron Technology reported third-quarter profit of $28.2 billion.；**需拆分复核**（该簇人工核定 FP pair=0）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: The company reported a surge in profit during its third quarter to $28.2 billion

#### 13.13 SK Hynix's stock rose 13% on Thursday.

- Mention：`mention:b6a27ee2…`；Family：`MARKET_MOVEMENT`；Assertion：`ACTUAL`
- 参与者：SK Hynix (SUBJECT)
- 数量：13% [stock_change]
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY
- Evidence：`VERIFIED`
- Atomic：`atomic:f51200ad…` — SK Hynix stock jumped more than 10% in early morning trading on June 25, 2026, triggering a static volatility interruption (VI) at the open.；**需拆分复核**（该簇人工核定 FP pair=0）
- Package：`package:7b63ae2b…` — SK Hynix stock jumped more than 10% in early morning trading on June 25, 2026, triggering a static volatility interruption (VI) at the open.

> Evidence: SK Hynix’s stock shot up 13% on Thursday after the company disclosed plans for a listing on the US Nasdaq.

#### 13.14 South Korea’s Kospi index fell 10% on Tuesday, triggering a circuit breaker.

- Mention：`mention:dfb8dadb…`；Family：`MARKET_MOVEMENT`；Assertion：`ACTUAL`
- 参与者：South Korea’s Kospi (SUBJECT)
- 数量：10% [index_change]
- 时间：2026-06-23T00:00:00 / 2026-06-23T00:00:00 / DAY
- Evidence：`VERIFIED`
- Atomic：`atomic:dd876f70…` — South Korea’s Kospi index fell 10% on Tuesday, triggering a circuit breaker.
- Package：`package:9591ed1b…` — South Korea’s Kospi index fell 10% on Tuesday, triggering a circuit breaker.

> Evidence: The latter tumbled 10% Tuesday, tripping a circuit breaker that prompted a 20-minute cooling off period.

#### 13.15 Japan’s Nikkei 225 index closed 4.6% higher on Thursday.

- Mention：`mention:e7e1f9da…`；Family：`MARKET_MOVEMENT`；Assertion：`ACTUAL`
- 参与者：Japan’s Nikkei 225 (SUBJECT)
- 数量：4.6% [index_change]
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY
- Evidence：`VERIFIED`
- Atomic：`atomic:6c7460d3…` — Japan’s Nikkei 225 index closed 4.6% higher on Thursday.
- Package：`package:26cd0ab5…` — Japan’s Nikkei 225 index closed 4.6% higher on Thursday.

> Evidence: Japan’s Nikkei 225 index closed up 4.6%

#### 13.16 South Korea’s Kospi index closed 5.4% higher on Thursday.

- Mention：`mention:e9e1ca9c…`；Family：`MARKET_MOVEMENT`；Assertion：`ACTUAL`
- 参与者：South Korea’s Kospi (SUBJECT)
- 数量：5.4% [index_change]
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY
- Evidence：`VERIFIED`
- Atomic：`atomic:0c739830…` — The KOSPI index surged more than 5% at the open on June 25, 2026, rising above 8,900 from 8,400 in the prior session.；**需拆分复核**（该簇人工核定 FP pair=2）
- Package：`package:234ea60d…` — The KOSPI index surged more than 5% at the open on June 25, 2026, rising above 8,900 from 8,400 in the prior session.

> Evidence: South Korea’s Kospi finished 5.4% higher.

### 14. The AI Memory Trade Is Heating Up: Defiance Launches 2X DRAM ETF After Micron's Blowout Quarter

- 来源：benzinga.com
- 发布时间：2026-06-25T11:19:23+00:00
- Message ID：`doxatlas:raw_media:238a879b-e686-457d-b83d-5b0405bd28c6`
- 原文：https://www.benzinga.com/etfs/new-etfs/26/06/60103609/the-ai-memory-trade-is-heating-up-defiance-launches-2x-dram-etf-after-microns-blowout-quarter
- Mention 数：4

#### 14.1 Micron reports fiscal third-quarter financial results including revenue and data center revenue performance.

- Mention：`mention:1e98ff65…`；Family：`FINANCIAL_PERFORMANCE`；Assertion：`ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：$11.3 billion [revenue]
- 时间：fiscal third-quarter / UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:bb912535…` — Micron Technology reported fiscal Q3 2026 financial results, including revenue of $41.46 billion and adjusted earnings per share of $25.11.；**需拆分复核**（该簇人工核定 FP pair=327）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: Micron reported fiscal third-quarter revenue of $11.3 billion, up 37% year-over-year, while data center revenue more than doubled as demand for high-bandwidth memory (HBM) chips used in AI servers continued to surge.

#### 14.2 Defiance launches a 2X DRAM ETF.

- Mention：`mention:47917872…`；Family：`PRODUCT_SCIENCE`；Assertion：`ACTUAL`
- 参与者：Defiance (ACTOR)；2X DRAM ETF (SUBJECT)
- 数量：—
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:4f47bc2a…` — Defiance launches a 2X DRAM ETF.；**需拆分复核**（该簇人工核定 FP pair=1）
- Package：`package:60b2bd5a…` — Defiance launches a 2X DRAM ETF.

> Evidence: Defiance Launches 2X DRAM ETF After Micron's Blowout Quarter

#### 14.3 Roundhill Investments launches a leveraged fund designed to deliver twice the daily performance of the Roundhill Memory ETF.

- Mention：`mention:6ae4dfe2…`；Family：`PRODUCT_SCIENCE`；Assertion：`ACTUAL`
- 参与者：Roundhill Investments (ACTOR)；leveraged fund (SUBJECT)
- 数量：—
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:4f47bc2a…` — Defiance launches a 2X DRAM ETF.；**需拆分复核**（该簇人工核定 FP pair=1）
- Package：`package:60b2bd5a…` — Defiance launches a 2X DRAM ETF.

> Evidence: Roundhill Investments’ launch of a leveraged fund designed to deliver twice the daily performance of the Roundhill Memory ETF (NASDAQ:DRAM).

#### 14.4 Micron issues forward guidance regarding demand for AI memory products.

- Mention：`mention:f4950155…`；Family：`GUIDANCE_EXPECTATION`；Assertion：`EXPECTED`
- 参与者：The company (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:62d95926…` — Micron issues forward guidance regarding demand for AI memory products.
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: The company also issued strong forward guidance, signaling that demand for AI memory products remains robust.

### 15. Mizuho Maintains Outperform on Micron Technology, Raises Price Target to $1375

- 来源：benzinga.com
- 发布时间：2026-06-25T12:23:10+00:00
- Message ID：`doxatlas:raw_media:1516d084-62ab-4c8a-a7f3-e635c464425a`
- 原文：https://www.benzinga.com/news/26/06/60106539/mizuho-maintains-outperform-micron-technology-raises-price-target-1375
- Mention 数：1

#### 15.1 Mizuho maintains an Outperform rating and raises the price target for Micron Technology to $1375.

- Mention：`mention:f0235859…`；Family：`ANALYST_ACTION`；Assertion：`ACTUAL`
- 参与者：Mizuho (ACTOR)；Micron Technology (SUBJECT)
- 数量：$1375 [price_target]
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:98d8ee30…` — Mizuho maintains an Outperform rating and raises the price target for Micron Technology to $1375.
- Package：`package:6ff29e05…` — Mizuho maintains an Outperform rating and raises the price target for Micron Technology to $1375.

> Evidence: Mizuho Maintains Outperform on Micron Technology, Raises Price Target to $1375

### 16. Wedbush says chip rally has further to run after Micron's blowout quarter

- 来源：proactiveinvestors.com
- 发布时间：2026-06-25T13:04:00+00:00
- Message ID：`doxatlas:raw_media:79fe1f85-a1cc-44e1-928b-d0256c075eb4`
- 原文：https://www.proactiveinvestors.com/companies/news/1094490/wedbush-says-chip-rally-has-further-to-run-after-micron-s-blowout-quarter-1094490.html
- Mention 数：10

#### 16.1 Micron Technology forecasted fourth-quarter revenue of $50 billion.

- Mention：`mention:08f7252a…`；Family：`GUIDANCE_EXPECTATION`；Assertion：`EXPECTED`
- 参与者：Micron Technology Inc (ACTOR)
- 数量：$50 billion [revenue]
- 时间：fourth-quarter / UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:a464025a…` — Micron provided financial guidance projecting $50 billion in revenue and $31 in EPS.；**需拆分复核**（该簇人工核定 FP pair=49）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: Micron forecasting fourth-quarter revenue of $50 billion, against the $43.6 billion the market had expected.

#### 16.2 Wedbush rates Micron Technology at outperform with a price target of $1,300.

- Mention：`mention:14ae2550…`；Family：`ANALYST_ACTION`；Assertion：`ACTUAL`
- 参与者：Wedbush (ACTOR)；Micron Technology Inc (TARGET)
- 数量：$1,300 [price_target]
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:08ff55db…` — Wedbush rates Micron Technology at outperform with a price target of $1,300.
- Package：`package:b4f74801…` — Wedbush rates Micron Technology at outperform with a price target of $1,300.

> Evidence: The broker rates Micron at outperform with a price target of $1,300

#### 16.3 Micron Technology reported gross margin of 84.9%.

- Mention：`mention:18602249…`；Family：`FINANCIAL_PERFORMANCE`；Assertion：`ACTUAL`
- 参与者：Micron Technology Inc (ACTOR)
- 数量：84.9% [gross_margin]
- 时间：third-quarter / UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:486603c4…` — Micron reported a quarterly gross margin of 84.6%, exceeding its guidance of 81%.；**需拆分复核**（该簇人工核定 FP pair=23）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: while gross margin of 84.9% beat expectations.

#### 16.4 Micron Technology signed 16 strategic customer agreements.

- Mention：`mention:2468654d…`；Family：`COMMERCIAL_OPERATION`；Assertion：`ACTUAL`
- 参与者：Micron Technology Inc (ACTOR)
- 数量：16 [agreement_count]
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:d600f32b…` — Micron Technology announced 16 signed Strategic Customer Agreements, which are multi-year deals locking in volume and providing pricing visibility for memory supply.；**需拆分复核**（该簇人工核定 FP pair=84）
- Package：`package:d12945f7…` — Micron is investing at record levels in technology, products and supply.；**边界错误**：June 22 Anthropic partnership/investment is a different bounded announcement from the earnings-origin SCA/investment commentary.

> Evidence: The company also signed 16 strategic customer agreements, 14 of which carry cumulative revenue of at least $100 billion over the term of the deals.

#### 16.5 Micron Technology intends to return all excess cash to shareholders.

- Mention：`mention:436ca430…`；Family：`TRANSACTION_CAPITAL`；Assertion：`PLANNED`
- 参与者：Micron Technology Inc (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:61a33de5…` — Micron plans to return 100% of its excess cash to shareholders beginning December 9, 2026.；**需拆分复核**（该簇人工核定 FP pair=0）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: Micron also said it intends to return all excess cash to shareholders as free cash flow builds.

#### 16.6 Micron Technology expects tight supply conditions to persist beyond its 2027 financial year.

- Mention：`mention:7208f9b3…`；Family：`PRODUCTION_SUPPLY`；Assertion：`EXPECTED`
- 参与者：Micron Technology Inc (ACTOR)
- 数量：—
- 时间：beyond its 2027 financial year / UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:691794fb…` — Micron expects memory shortages to persist at least through 2028.；**需拆分复核**（该簇人工核定 FP pair=0）
- Package：`package:d33829fa…` — Micron expects memory shortages to persist at least through 2028.

> Evidence: the company expects tight supply conditions to persist beyond its 2027 financial year

#### 16.7 Micron Technology reported third-quarter revenue of $41.5 billion.

- Mention：`mention:75072da8…`；Family：`FINANCIAL_PERFORMANCE`；Assertion：`ACTUAL`
- 参与者：Micron Technology Inc (ACTOR)
- 数量：$41.5 billion [revenue]
- 时间：third-quarter / UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:bb912535…` — Micron Technology reported fiscal Q3 2026 financial results, including revenue of $41.46 billion and adjusted earnings per share of $25.11.；**需拆分复核**（该簇人工核定 FP pair=327）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: Micron reported third-quarter revenue of $41.5 billion, up 74% on the prior quarter and well ahead of the $35.9 billion expected by analysts.

#### 16.8 Micron Technology's data centre revenue reached an annualised run rate of about $100 billion.

- Mention：`mention:8e98bca0…`；Family：`FINANCIAL_PERFORMANCE`；Assertion：`ACTUAL`
- 参与者：Micron Technology Inc (ACTOR)
- 数量：$100 billion [data_centre_revenue_run_rate]
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:e89133b8…` — Micron Technology's data centre revenue reached an annualised run rate of about $100 billion.；**需拆分复核**（该簇人工核定 FP pair=0）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: Its data centre revenue has now reached an annualised run rate of about $100 billion

#### 16.9 Micron Technology guided gross margin to about 86%.

- Mention：`mention:985f831d…`；Family：`GUIDANCE_EXPECTATION`；Assertion：`EXPECTED`
- 参与者：Micron Technology Inc (ACTOR)
- 数量：86% [gross_margin]
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:2c9c41c1…` — Micron Technology guided gross margin to about 86%.；**需拆分复核**（该簇人工核定 FP pair=6）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: with gross margin guided to about 86%.

#### 16.10 Micron Technology reported earnings per share of $25.11.

- Mention：`mention:bc790b04…`；Family：`FINANCIAL_PERFORMANCE`；Assertion：`ACTUAL`
- 参与者：Micron Technology Inc (ACTOR)
- 数量：$25.11 [earnings_per_share]
- 时间：third-quarter / UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:58553e03…` — Micron Technology reported earnings per share of $25.11.；**需拆分复核**（该簇人工核定 FP pair=5）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: Earnings per share came in at $25.11, more than double the previous quarter and ahead of the $20.86 consensus

### 17. Micron surges to new highs on blockbuster earnings: Jim Lebenthal buys the stock

- 来源：CNBC
- 发布时间：2026-06-25T13:05:02+00:00
- Message ID：`doxatlas:raw_media:6e04e5e6-801c-4141-ac0f-81d29029b012`
- 原文：https://finnhub.io/api/news?id=760aa3bb75cb55185d1ad9b8997324a47603aeac7746264f37945ce55241212a
- Mention 数：2

#### 17.1 Jim Lebenthal buys Micron stock.

- Mention：`mention:77f03afa…`；Family：`ANALYST_ACTION`；Assertion：`ACTUAL`
- 参与者：Jim Lebenthal (ACTOR)；Micron (SUBJECT)
- 数量：—
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:bd060b5e…` — Jim Lebenthal buys Micron stock.
- Package：`package:24b79621…` — Jim Lebenthal buys Micron stock.

> Evidence: Jim Lebenthal buys the stock

#### 17.2 Micron stock price surges to new highs.

- Mention：`mention:ae62830d…`；Family：`MARKET_MOVEMENT`；Assertion：`ACTUAL`
- 参与者：Micron (SUBJECT)
- 数量：—
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:471dce1e…` — Micron Technology stock gained roughly 15% in after-hours trading following the announcement of its fiscal Q3 2026 results.；**需拆分复核**（该簇人工核定 FP pair=39）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: Micron surges to new highs on blockbuster earnings

### 18. SK Hynix Unveils $29 Billion US Listing as Shares Soar Over 800%

- 来源：finance.yahoo.com
- 发布时间：2026-06-25T13:50:23+00:00
- Message ID：`doxatlas:raw_media:b24ea6b3-557e-4053-9cae-c8e419455b39`
- 原文：https://finance.yahoo.com/markets/stocks/articles/sk-hynix-unveils-29-billion-135023709.html
- Mention 数：6

#### 18.1 SK Hynix seeks to raise 45.45 trillion won through a US listing.

- Mention：`mention:2ed9b896…`；Family：`TRANSACTION_CAPITAL`；Assertion：`PLANNED`
- 参与者：SK Hynix (ACTOR)
- 数量：45.45 trillion won [capital_raise_amount]
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:a21c0d5b…` — SK Hynix disclosed plans for a listing on the US Nasdaq.；**需拆分复核**（该簇人工核定 FP pair=0）
- Package：`package:87feed09…` — SK Hynix disclosed plans for a listing on the US Nasdaq.

> Evidence: SK Hynix is seeking 45.45 trillion won through the US listing

#### 18.2 The Kospi Index rose 6%.

- Mention：`mention:6c34c485…`；Family：`MARKET_MOVEMENT`；Assertion：`ACTUAL`
- 参与者：Kospi Index (SUBJECT)
- 数量：6% [index_change_percent]
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY
- Evidence：`VERIFIED`
- Atomic：`atomic:0c739830…` — The KOSPI index surged more than 5% at the open on June 25, 2026, rising above 8,900 from 8,400 in the prior session.；**需拆分复核**（该簇人工核定 FP pair=2）
- Package：`package:234ea60d…` — The KOSPI index surged more than 5% at the open on June 25, 2026, rising above 8,900 from 8,400 in the prior session.

> Evidence: helping push the Kospi Index up 6%.

#### 18.3 SK Hynix's market value rose above $1 trillion.

- Mention：`mention:6f4a4564…`；Family：`MARKET_MOVEMENT`；Assertion：`ACTUAL`
- 参与者：SK Hynix (SUBJECT)
- 数量：$1 trillion [market_cap]
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:b7747258…` — SK Hynix's market value rose above $1 trillion.
- Package：`package:17a5c90e…` — SK Hynix's market value rose above $1 trillion.

> Evidence: lifting the company's market value above $1 trillion.

#### 18.4 SK Hynix shares climbed nearly 12% in early trading before trimming gains to around 10% on Thursday.

- Mention：`mention:be8833e2…`；Family：`MARKET_MOVEMENT`；Assertion：`ACTUAL`
- 参与者：SK Hynix (SUBJECT)
- 数量：nearly 12% [price_change_percent]；around 10% [price_change_percent]
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY
- Evidence：`VERIFIED`
- Atomic：`atomic:f51200ad…` — SK Hynix stock jumped more than 10% in early morning trading on June 25, 2026, triggering a static volatility interruption (VI) at the open.；**需拆分复核**（该簇人工核定 FP pair=0）
- Package：`package:7b63ae2b…` — SK Hynix stock jumped more than 10% in early morning trading on June 25, 2026, triggering a static volatility interruption (VI) at the open.

> Evidence: The stock climbed nearly 12% in early Thursday trading before trimming gains to around 10%

#### 18.5 SK Hynix expects its American depositary receipts to start trading on July 10.

- Mention：`mention:e6bd44a1…`；Family：`TRANSACTION_CAPITAL`；Assertion：`EXPECTED`
- 参与者：SK Hynix (ACTOR)
- 数量：—
- 时间：2026-07-10T00:00:00 / 2026-07-10T00:00:00 / DAY
- Evidence：`VERIFIED`
- Atomic：`atomic:a21c0d5b…` — SK Hynix disclosed plans for a listing on the US Nasdaq.；**需拆分复核**（该簇人工核定 FP pair=0）
- Package：`package:87feed09…` — SK Hynix disclosed plans for a listing on the US Nasdaq.

> Evidence: The company expects its American depositary receipts to start trading on July 10

#### 18.6 Micron Technology posted a quarterly sales forecast that beat Wall Street expectations.

- Mention：`mention:ee01c398…`；Family：`GUIDANCE_EXPECTATION`；Assertion：`ACTUAL`
- 参与者：Micron Technology (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:a464025a…` — Micron provided financial guidance projecting $50 billion in revenue and $31 in EPS.；**需拆分复核**（该簇人工核定 FP pair=49）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: Samsung Electronics (SSNLF) also rallied after Micron Technology (NASDAQ:MU) posted a quarterly sales forecast that beat Wall Street expectations

### 19. Apple Just Confirmed Micron's Biggest AI Prediction

- 来源：benzinga.com
- 发布时间：2026-06-25T14:20:00+00:00
- Message ID：`doxatlas:raw_media:35c3d868-abe6-4617-8c1f-c1460a8e8179`
- 原文：https://www.benzinga.com/trading-ideas/long-ideas/26/06/60108831/apple-just-confirmed-microns-biggest-ai-prediction
- Mention 数：3

#### 19.1 Micron disclosed that customers have committed approximately $22 billion through strategic agreements.

- Mention：`mention:555ef3ac…`；Family：`FINANCIAL_PERFORMANCE`；Assertion：`ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：$22 billion [committed_value]
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:d600f32b…` — Micron Technology announced 16 signed Strategic Customer Agreements, which are multi-year deals locking in volume and providing pricing visibility for memory supply.；**需拆分复核**（该簇人工核定 FP pair=84）
- Package：`package:d12945f7…` — Micron is investing at record levels in technology, products and supply.；**边界错误**：June 22 Anthropic partnership/investment is a different bounded announcement from the earnings-origin SCA/investment commentary.

> Evidence: Micron recently disclosed that customers have committed approximately $22 billion through strategic agreements, providing additional visibility into future demand as the company ramps production of high-bandwidth memory used in AI accelerators.

#### 19.2 Micron expects tight memory market conditions to persist beyond calendar 2027 with no clear end in sight for supply catching up with demand.

- Mention：`mention:abb24a1a…`；Family：`GUIDANCE_EXPECTATION`；Assertion：`EXPECTED`
- 参与者：Micron (ACTOR)；Sanjay Mehrotra (OTHER)
- 数量：—
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:691794fb…` — Micron expects memory shortages to persist at least through 2028.；**需拆分复核**（该簇人工核定 FP pair=0）
- Package：`package:d33829fa…` — Micron expects memory shortages to persist at least through 2028.

> Evidence: During its fiscal Q3 earnings call, Micron CEO Sanjay Mehrotra said the company still sees no clear end to tightening memory markets. “We currently do not have line of sight as to when memory supply will be able to catch up with increasing demand,” Mehrotra told investors. “We…

#### 19.3 Apple increased prices on MacBook Neo, MacBook Air, MacBook Pro, iPad Pro, iPad Air, HomePod, HomePod mini, and Apple TV due to tightening supplies of memory and storage components.

- Mention：`mention:e30ba894…`；Family：`COMMERCIAL_OPERATION`；Assertion：`ACTUAL`
- 参与者：Apple (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:f2be60db…` — Apple increased prices on MacBook Neo, MacBook Air, MacBook Pro, iPad Pro, iPad Air, HomePod, HomePod mini, and Apple TV due to tightening supplies of memory and storage components.；**需拆分复核**（该簇人工核定 FP pair=0）
- Package：`package:a3df4c2e…` — Apple increased prices on MacBook Neo, MacBook Air, MacBook Pro, iPad Pro, iPad Air, HomePod, HomePod mini, and Apple TV due to tightening supplies of memory and storage components.

> Evidence: The price increases affect products including the MacBook Neo, MacBook Air, MacBook Pro, iPad Pro, iPad Air, HomePod, HomePod mini and Apple TV, while iPhone pricing remains unchanged. Apple attributed the increases to tightening supplies of memory and storage components as AI…

### 20. Why Sandisk Stock Soared Today

- 来源：fool.com
- 发布时间：2026-06-25T14:40:00+00:00
- Message ID：`doxatlas:raw_media:35a4333e-6608-482b-b963-ba2f64392063`
- 原文：https://www.fool.com/investing/2026/06/25/why-sandisk-stock-soared-today/
- Mention 数：9

#### 20.1 Analysts expect Sandisk's earnings to reach $33.72 per share.

- Mention：`mention:0c1ce3a7…`；Family：`ANALYST_ACTION`；Assertion：`EXPECTED`
- 参与者：analysts (ACTOR)；Sandisk (SUBJECT)
- 数量：$33.72 per share [earnings_per_share]
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:e9943ecb…` — Analysts expect Sandisk's earnings to reach $33.72 per share.
- Package：`package:6f8e6c16…` — Analysts expect Sandisk's earnings to reach $33.72 per share.

> Evidence: analysts are expecting big things, with earnings forecast to more than double sequentially to $33.72 per share.

#### 20.2 Sandisk is locking in long-term prices at high margins through Strategic Customer Agreements.

- Mention：`mention:0cde9995…`；Family：`COMMERCIAL_OPERATION`；Assertion：`ONGOING`
- 参与者：Sandisk (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:cb534b9c…` — Sandisk is locking in long-term prices at high margins through Strategic Customer Agreements.
- Package：`package:8da9a30b…` — Sandisk is locking in long-term prices at high margins through Strategic Customer Agreements.

> Evidence: Micron is locking in long-term prices at these high margins through Strategic Customer Agreements -- and so is Sandisk.

#### 20.3 Micron reported GAAP profits increased by 104% sequentially.

- Mention：`mention:4693c170…`；Family：`FINANCIAL_PERFORMANCE`；Assertion：`ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：104% [gaap_profits_sequential_change]
- 时间：UNKNOWN
- Evidence：`TEXT_NOT_FOUND`
- Atomic：`atomic:05958579…` — Micron Technology reported third-quarter profit of $28.2 billion.；**需拆分复核**（该簇人工核定 FP pair=0）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: Micron just reported GAAP profits up 104% sequentially.

#### 20.4 Sandisk is scheduled to report earnings on August 24, 2026.

- Mention：`mention:7c4a48dc…`；Family：`GUIDANCE_EXPECTATION`；Assertion：`PLANNED`
- 参与者：Sandisk (ACTOR)
- 数量：—
- 时间：2026-08-24T00:00:00 / DAY
- Evidence：`VERIFIED`
- Atomic：`atomic:9dbe9852…` — Sandisk is scheduled to report earnings on August 24, 2026.
- Package：`package:5708fe21…` — Sandisk is scheduled to report earnings on August 24, 2026.

> Evidence: Sandisk itself doesn't report earnings again for another couple of months, on Aug. 24.

#### 20.5 Sandisk stock price increased by 11.2% on Thursday morning.

- Mention：`mention:827a61b0…`；Family：`MARKET_MOVEMENT`；Assertion：`ACTUAL`
- 参与者：Sandisk (SUBJECT)
- 数量：11.2% [stock_price_change_percent]
- 时间：2026-06-25T00:00:00 / DAY
- Evidence：`VERIFIED`
- Atomic：`atomic:abde442d…` — Sandisk stock price increased by 11.2% on Thursday morning.；**需拆分复核**（该簇人工核定 FP pair=2）
- Package：`package:99a65e40…` — Sandisk stock price increased by 11.2% on Thursday morning.；**边界错误**：Sandisk Thursday-morning move and the multi-company after-hours move have different temporal/entity boundaries; the first Atomic is also polluted by a Citi target mention.

> Evidence: Computer memory specialist Sandisk (SNDK 14.13%) stock surged 11.2% Thursday morning after archrival Micron (MU 5.68%) crushed analyst forecasts for its fiscal Q3 earnings.

#### 20.6 Micron CEO Sanjay Mehrotra stated that supply will not catch up with demand anytime soon.

- Mention：`mention:83e55796…`；Family：`PRODUCTION_SUPPLY`；Assertion：`EXPECTED`
- 参与者：Sanjay Mehrotra (ACTOR)；Micron (OTHER)
- 数量：—
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:c9e91519…` — Micron CEO Sanjay Mehrotra stated that supply will not catch up with demand anytime soon.
- Package：`package:d33829fa…` — Micron expects memory shortages to persist at least through 2028.

> Evidence: Mehrotra still doesn't see supply catching up with demand anytime soon.

#### 20.7 Micron is investing at record levels to meet customer demand.

- Mention：`mention:c27f6f40…`；Family：`COMMERCIAL_OPERATION`；Assertion：`ONGOING`
- 参与者：Micron (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:3a4cce33…` — Micron is investing at record levels in technology, products and supply.；**需拆分复核**（该簇人工核定 FP pair=0）
- Package：`package:d12945f7…` — Micron is investing at record levels in technology, products and supply.；**边界错误**：June 22 Anthropic partnership/investment is a different bounded announcement from the earnings-origin SCA/investment commentary.

> Evidence: Micron's "investing at record levels" to help meet customer demand

#### 20.8 Micron reported fiscal Q3 earnings of $25.11 per share.

- Mention：`mention:c9751814…`；Family：`FINANCIAL_PERFORMANCE`；Assertion：`ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：$25.11 per share [earnings_per_share]
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:bb912535…` — Micron Technology reported fiscal Q3 2026 financial results, including revenue of $41.46 billion and adjusted earnings per share of $25.11.；**需拆分复核**（该簇人工核定 FP pair=327）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: Micron actually earned $25.11 per share

#### 20.9 Micron reported fiscal Q3 sales of $41.5 billion.

- Mention：`mention:f53e10a4…`；Family：`FINANCIAL_PERFORMANCE`；Assertion：`ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：$41.5 billion [sales]
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:bb912535…` — Micron Technology reported fiscal Q3 2026 financial results, including revenue of $41.46 billion and adjusted earnings per share of $25.11.；**需拆分复核**（该簇人工核定 FP pair=327）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: sales quadrupled year over year to $41.5 billion

### 21. Is Micron a Buy After Its Blowout Earnings Report?

- 来源：fool.com
- 发布时间：2026-06-25T15:30:00+00:00
- Message ID：`doxatlas:raw_media:a424d484-3e57-47ed-b21d-50aeceafe593`
- 原文：https://www.fool.com/investing/2026/06/25/is-micron-a-buy-after-its-blowout-earnings-report/
- Mention 数：11

#### 21.1 Micron Technology stock trades at 16 times forward earnings estimates.

- Mention：`mention:155a1fcc…`；Family：`MARKET_MOVEMENT`；Assertion：`ACTUAL`
- 参与者：Micron Technology (SUBJECT)
- 数量：16x [forward_pe_ratio]
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:a3237267…` — Micron Technology stock trades at 16 times forward earnings estimates.
- Package：`package:49660a11…` — Micron Technology stock trades at 16 times forward earnings estimates.

> Evidence: Micron trades at 16x forward earnings estimates,

#### 21.2 Micron Technology reported quarterly revenue of more than $41 billion.

- Mention：`mention:1d4627d7…`；Family：`FINANCIAL_PERFORMANCE`；Assertion：`ACTUAL`
- 参与者：Micron Technology (ACTOR)
- 数量：more than $41 billion [revenue]
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:bb912535…` — Micron Technology reported fiscal Q3 2026 financial results, including revenue of $41.46 billion and adjusted earnings per share of $25.11.；**需拆分复核**（该簇人工核定 FP pair=327）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: Micron's revenue soared in the triple digits to more than $41 billion,

#### 21.3 Micron Technology signed 16 customer agreements involving commitments to purchase memory products, running through 2030.

- Mention：`mention:23b9391d…`；Family：`COMMERCIAL_OPERATION`；Assertion：`ACTUAL`
- 参与者：Micron Technology (ACTOR)；data center, consumer, and automotive customers (COUNTERPARTY)
- 数量：16 customer agreements [agreement_count]
- 时间：2030-12-31T00:00:00 / UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:d600f32b…` — Micron Technology announced 16 signed Strategic Customer Agreements, which are multi-year deals locking in volume and providing pricing visibility for memory supply.；**需拆分复核**（该簇人工核定 FP pair=84）
- Package：`package:d12945f7…` — Micron is investing at record levels in technology, products and supply.；**边界错误**：June 22 Anthropic partnership/investment is a different bounded announcement from the earnings-origin SCA/investment commentary.

> Evidence: Micron also signed 16 customer agreements that offer the company and investors visibility on revenue ahead, reinforcing the idea that the demand we've seen so far is set to continue.

#### 21.4 Micron Technology stock gained more than 260% in 2026.

- Mention：`mention:403b761a…`；Family：`MARKET_MOVEMENT`；Assertion：`ACTUAL`
- 参与者：Micron Technology (SUBJECT)
- 数量：more than 260% [stock_return]
- 时间：2026-01-01T00:00:00 / 2026-06-25T00:00:00 / INTERVAL
- Evidence：`VERIFIED`
- Atomic：`atomic:c771a0d1…` — Micron Technology stock gained more than 260% in 2026.
- Package：`package-boundary-split:d1096f9e…` — Micron Technology stock gained more than 260% in 2026.

> Evidence: gaining more than 260% this year alone.

#### 21.5 Micron Technology reported quarterly net income of $28 billion.

- Mention：`mention:43515536…`；Family：`FINANCIAL_PERFORMANCE`；Assertion：`ACTUAL`
- 参与者：Micron Technology (ACTOR)
- 数量：$28 billion [net_income]
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:05958579…` — Micron Technology reported third-quarter profit of $28.2 billion.；**需拆分复核**（该簇人工核定 FP pair=0）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: and net income jumped in the quadruple digits to $28 billion.

#### 21.6 Micron Technology reported gross margin of more than 84%.

- Mention：`mention:6d650169…`；Family：`FINANCIAL_PERFORMANCE`；Assertion：`ACTUAL`
- 参与者：Micron Technology (ACTOR)
- 数量：more than 84% [gross_margin]
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:486603c4…` — Micron reported a quarterly gross margin of 84.6%, exceeding its guidance of 81%.；**需拆分复核**（该簇人工核定 FP pair=23）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: gross margin came in at more than 84%.

#### 21.7 Micron Technology expects $22 billion in commitments from signed deals.

- Mention：`mention:789f718a…`；Family：`GUIDANCE_EXPECTATION`；Assertion：`EXPECTED`
- 参与者：Micron Technology (ACTOR)
- 数量：$22 billion [commitments]
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:d600f32b…` — Micron Technology announced 16 signed Strategic Customer Agreements, which are multi-year deals locking in volume and providing pricing visibility for memory supply.；**需拆分复核**（该簇人工核定 FP pair=84）
- Package：`package:d12945f7…` — Micron is investing at record levels in technology, products and supply.；**边界错误**：June 22 Anthropic partnership/investment is a different bounded announcement from the earnings-origin SCA/investment commentary.

> Evidence: The company expects $22 billion in commitments from the deals signed so far,

#### 21.8 Micron Technology expects half of its revenue to eventually come from strategic agreements.

- Mention：`mention:8f2da55e…`；Family：`GUIDANCE_EXPECTATION`；Assertion：`EXPECTED`
- 参与者：Micron Technology (ACTOR)
- 数量：half [revenue_share]
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:d600f32b…` — Micron Technology announced 16 signed Strategic Customer Agreements, which are multi-year deals locking in volume and providing pricing visibility for memory supply.；**需拆分复核**（该簇人工核定 FP pair=84）
- Package：`package:d12945f7…` — Micron is investing at record levels in technology, products and supply.；**边界错误**：June 22 Anthropic partnership/investment is a different bounded announcement from the earnings-origin SCA/investment commentary.

> Evidence: and it expects half of its revenue to eventually come from such strategic agreements.

#### 21.9 Micron Technology reported free cash flow of $18 billion.

- Mention：`mention:90863ec8…`；Family：`FINANCIAL_PERFORMANCE`；Assertion：`ACTUAL`
- 参与者：Micron Technology (ACTOR)
- 数量：$18 billion [free_cash_flow]
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:eddfbc06…` — Micron Technology reported free cash flow of $18 billion.
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: Free cash flow climbed to record levels of $18 billion,

#### 21.10 Micron Technology management stated that the AI revolution is in its early stages.

- Mention：`mention:e0d51d30…`；Family：`OTHER`；Assertion：`ACTUAL`
- 参与者：Micron Technology (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:d0f40d74…` — Micron Technology management stated that the AI revolution is in its early stages.
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: the company offered an extremely positive message, saying we're in "the early innings" of the AI revolution

#### 21.11 Micron Technology's quarterly revenue reached record levels for the fifth consecutive time.

- Mention：`mention:fc3ebd3a…`；Family：`FINANCIAL_PERFORMANCE`；Assertion：`ACTUAL`
- 参与者：Micron Technology (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:bb912535…` — Micron Technology reported fiscal Q3 2026 financial results, including revenue of $41.46 billion and adjusted earnings per share of $25.11.；**需拆分复核**（该簇人工核定 FP pair=327）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: Quarterly revenue reached record levels for the fifth consecutive time.

### 22. Why Everyone Is Talking About Micron

- 来源：fool.com
- 发布时间：2026-06-25T16:15:01+00:00
- Message ID：`doxatlas:raw_media:49fb27c5-84b4-4e56-8791-f7ac23de38b7`
- 原文：https://www.fool.com/investing/2026/06/25/why-everyone-is-talking-about-micron/
- Mention 数：10

#### 22.1 UBS analysts stated that DRAM is likely to be constrained until at least halfway through 2028.

- Mention：`mention:0e249047…`；Family：`ANALYST_ACTION`；Assertion：`HYPOTHETICAL`
- 参与者：Analysts at UBS (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:f50a7c89…` — UBS analysts stated that DRAM is likely to be constrained until at least halfway through 2028.；**需拆分复核**（该簇人工核定 FP pair=1）
- Package：`package:abe7b285…` — UBS analysts stated that DRAM is likely to be constrained until at least halfway through 2028.

> Evidence: Analysts at UBS have previously said that DRAM is likely to be constrained until at least halfway through 2028

#### 22.2 Micron reported third-quarter revenue of $41.5 billion, beating estimates by $6.4 billion.

- Mention：`mention:393be66e…`；Family：`FINANCIAL_PERFORMANCE`；Assertion：`ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：$41.5 billion [revenue]；$6.4 billion [beat_estimate]
- 时间：third-quarter / UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:bb912535…` — Micron Technology reported fiscal Q3 2026 financial results, including revenue of $41.46 billion and adjusted earnings per share of $25.11.；**需拆分复核**（该簇人工核定 FP pair=327）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: Revenue of $41.5 billion beat estimates by $6.4 billion.

#### 22.3 Bernstein analyst Mark Newman expressed concern in a research note that Micron’s new strategic customer agreements could include pricing ceilings, limiting price increases and failing to avoid cyclicality.

- Mention：`mention:8ecff044…`；Family：`ANALYST_ACTION`；Assertion：`HYPOTHETICAL`
- 参与者：Bernstein analyst Mark Newman (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:29028dee…` — Bernstein analyst Mark Newman expressed concern in a research note that Micron’s new strategic customer agreements could include pricing ceilings, limiting price increases and failing to avoid cyclicality.
- Package：`package:0ce3ec3c…` — Bernstein analyst Mark Newman expressed concern in a research note that Micron’s new strategic customer agreements could include pricing ceilings, limiting price increases and failing to avoid cyclicality.

> Evidence: Bernstein analyst Mark Newman thinks Micron’s new strategic customer agreements could include pricing ceilings, which would limit how much Micron could raise memory prices.

#### 22.4 Micron reported third-quarter earnings per share of $25.11, beating Wall Street consensus estimates by $4.72.

- Mention：`mention:94f6b1e4…`；Family：`FINANCIAL_PERFORMANCE`；Assertion：`ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：$25.11 [earnings_per_share]；$4.72 [beat_estimate]
- 时间：third-quarter / UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:58553e03…` — Micron Technology reported earnings per share of $25.11.；**需拆分复核**（该簇人工核定 FP pair=5）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: Earnings per share of $25.11 beat Wall Street consensus estimates by $4.72.

#### 22.5 Micron guided to $50 billion in revenue for its current quarter.

- Mention：`mention:a8b962c9…`；Family：`GUIDANCE_EXPECTATION`；Assertion：`EXPECTED`
- 参与者：Micron (ACTOR)
- 数量：$50 billion [revenue_guidance]
- 时间：current quarter / UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:a464025a…` — Micron provided financial guidance projecting $50 billion in revenue and $31 in EPS.；**需拆分复核**（该簇人工核定 FP pair=49）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: Micron is now guiding to $50 billion in revenue for its current quarter.

#### 22.6 Micron secured 16 contracts with customers, including data centers and automakers, in the three- to five-year range that could bring in $22 billion.

- Mention：`mention:ba1fe298…`；Family：`COMMERCIAL_OPERATION`；Assertion：`ACTUAL`
- 参与者：Micron (ACTOR)；customers (COUNTERPARTY)
- 数量：16 contracts [contract_count]；$22 billion [potential_revenue]
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:d600f32b…` — Micron Technology announced 16 signed Strategic Customer Agreements, which are multi-year deals locking in volume and providing pricing visibility for memory supply.；**需拆分复核**（该簇人工核定 FP pair=84）
- Package：`package:d12945f7…` — Micron is investing at record levels in technology, products and supply.；**边界错误**：June 22 Anthropic partnership/investment is a different bounded announcement from the earnings-origin SCA/investment commentary.

> Evidence: Micron also said it has secured 16 contracts with customers, including data centers and automakers, in the three- to five-year range that could bring in $22 billion.

#### 22.7 Wedbush analyst Dan Ives stated in a research note that there are no cracks in AI demand and recommended owning core tech winners into year-end.

- Mention：`mention:ba54bc64…`；Family：`ANALYST_ACTION`；Assertion：`ACTUAL`
- 参与者：Wedbush analyst Dan Ives (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:54d5ed92…` — Wedbush analyst Dan Ives stated in a research note that there are no cracks in AI demand and recommended owning core tech winners into year-end.
- Package：`package:3238525d…` — Wedbush analyst Dan Ives stated in a research note that there are no cracks in AI demand and recommended owning core tech winners into year-end.

> Evidence: "We are seeing no cracks in AI demand on the chips/hardware or software front which gives us a bright green light to own the core tech winners into year-end," Wedbush analyst Dan Ives said in a recent research note.

#### 22.8 Micron's third-quarter revenue quadrupled from the prior year.

- Mention：`mention:c1d26b1b…`；Family：`FINANCIAL_PERFORMANCE`；Assertion：`ACTUAL`
- 参与者：Micron (SUBJECT)
- 数量：—
- 时间：third-quarter / UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:bb912535…` — Micron Technology reported fiscal Q3 2026 financial results, including revenue of $41.46 billion and adjusted earnings per share of $25.11.；**需拆分复核**（该簇人工核定 FP pair=327）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: Revenue quadrupled from the prior year.

#### 22.9 UBS analysts stated that NAND is likely to be constrained until at least the end of 2027.

- Mention：`mention:e4d8685f…`；Family：`ANALYST_ACTION`；Assertion：`HYPOTHETICAL`
- 参与者：Analysts at UBS (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:f50a7c89…` — UBS analysts stated that DRAM is likely to be constrained until at least halfway through 2028.；**需拆分复核**（该簇人工核定 FP pair=1）
- Package：`package:abe7b285…` — UBS analysts stated that DRAM is likely to be constrained until at least halfway through 2028.

> Evidence: NAND is likely to be constrained until at least the end of 2027.

#### 22.10 Micron stock traded nearly 14% higher as of 11:50 a.m. ET.

- Mention：`mention:f8c36aaa…`；Family：`MARKET_MOVEMENT`；Assertion：`ACTUAL`
- 参与者：Micron stock (SUBJECT)
- 数量：nearly 14% [price_change_percent]
- 时间：2026-06-25T11:50:00 / TIMESTAMP
- Evidence：`VERIFIED`
- Atomic：`atomic:c76f1270…` — Micron stock traded nearly 14% higher as of 11:50 a.m. ET.；**需拆分复核**（该簇人工核定 FP pair=4）
- Package：`package-boundary-split:7f911bb6…` — Micron stock traded nearly 14% higher as of 11:50 a.m. ET.

> Evidence: Micron stock traded nearly 14% higher, as of 11:50 a.m. ET.

### 23. Why Micron's blowout earnings are a headache for Apple

- 来源：finance.yahoo.com
- 发布时间：2026-06-25T16:19:32+00:00
- Message ID：`doxatlas:raw_media:6ccb4979-525c-4467-a381-538f38a4fa9c`
- 原文：https://finance.yahoo.com/markets/article/why-microns-blowout-earnings-are-a-headache-for-apple-161932441.html
- Mention 数：9

#### 23.1 Micron's market value increased by more than $100 billion on Thursday.

- Mention：`mention:308a8a9b…`；Family：`MARKET_MOVEMENT`；Assertion：`ACTUAL`
- 参与者：Micron (SUBJECT)
- 数量：more than $100 billion [market_value_change]
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY
- Evidence：`VERIFIED`
- Atomic：`atomic:c76f1270…` — Micron stock traded nearly 14% higher as of 11:50 a.m. ET.；**需拆分复核**（该簇人工核定 FP pair=4）
- Package：`package-boundary-split:7f911bb6…` — Micron stock traded nearly 14% higher as of 11:50 a.m. ET.

> Evidence: Micron has added more than $100 billion in market value Thursday even after giving back part of its early surge.

#### 23.2 Apple raised prices on some MacBooks and iPads by $100 to $300.

- Mention：`mention:3c19bd33…`；Family：`COMMERCIAL_OPERATION`；Assertion：`ACTUAL`
- 参与者：Apple (ACTOR)
- 数量：$100 [price_increase]；$300 [price_increase]
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:f2be60db…` — Apple increased prices on MacBook Neo, MacBook Air, MacBook Pro, iPad Pro, iPad Air, HomePod, HomePod mini, and Apple TV due to tightening supplies of memory and storage components.；**需拆分复核**（该簇人工核定 FP pair=0）
- Package：`package:a3df4c2e…` — Apple increased prices on MacBook Neo, MacBook Air, MacBook Pro, iPad Pro, iPad Air, HomePod, HomePod mini, and Apple TV due to tightening supplies of memory and storage components.

> Evidence: Apple raised prices on some MacBooks and iPads amid the global memory crisis, with increases running from $100 to $300 on some devices.

#### 23.3 Micron expects its gross margin to rise to about 86% in the current quarter.

- Mention：`mention:53ccfa3c…`；Family：`GUIDANCE_EXPECTATION`；Assertion：`EXPECTED`
- 参与者：Micron (ACTOR)
- 数量：about 86% [gross_margin]
- 时间：this quarter / UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:2c9c41c1…` — Micron Technology guided gross margin to about 86%.；**需拆分复核**（该簇人工核定 FP pair=6）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: The company expects that figure to rise to about 86% this quarter.

#### 23.4 Micron exceeded Wall Street earnings estimates.

- Mention：`mention:8feaeb21…`；Family：`FINANCIAL_PERFORMANCE`；Assertion：`ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:58553e03…` — Micron Technology reported earnings per share of $25.11.；**需拆分复核**（该簇人工核定 FP pair=5）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: Micron topped Wall Street's estimates

#### 23.5 The Magnificent Seven stocks declined by about 2% to a two-month low.

- Mention：`mention:bc87f5fa…`；Family：`MARKET_MOVEMENT`；Assertion：`ACTUAL`
- 参与者：The Magnificent Seven (SUBJECT)
- 数量：about 2% [index_change]
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:d6f7a015…` — The Magnificent Seven stocks declined by about 2% to a two-month low.；**需拆分复核**（该簇人工核定 FP pair=3）
- Package：`package:2a1f2600…` — The Magnificent Seven stocks declined by about 2% to a two-month low.

> Evidence: The Magnificent Seven are down about 2%, sliding to a two-month low after a month in which megacap AI winners had already lost trillions in market value.

#### 23.6 Apple's stock price declined by over 5%.

- Mention：`mention:c5833636…`；Family：`MARKET_MOVEMENT`；Assertion：`ACTUAL`
- 参与者：Apple (SUBJECT)
- 数量：over 5% [stock_price_change]
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:d6f7a015…` — The Magnificent Seven stocks declined by about 2% to a two-month low.；**需拆分复核**（该簇人工核定 FP pair=3）
- Package：`package:2a1f2600…` — The Magnificent Seven stocks declined by about 2% to a two-month low.

> Evidence: Apple (AAPL), down over 5% after raising prices on some Macs and iPads, is showing the other side of that squeeze.

#### 23.7 Micron reported record revenue.

- Mention：`mention:c5c2ca72…`；Family：`FINANCIAL_PERFORMANCE`；Assertion：`ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:58553e03…` — Micron Technology reported earnings per share of $25.11.；**需拆分复核**（该簇人工核定 FP pair=5）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: posted record revenue

#### 23.8 Micron reported a record gross margin of 84.9%.

- Mention：`mention:d2bce0f8…`；Family：`FINANCIAL_PERFORMANCE`；Assertion：`ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：84.9% [gross_margin]
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:486603c4…` — Micron reported a quarterly gross margin of 84.6%, exceeding its guidance of 81%.；**需拆分复核**（该簇人工核定 FP pair=23）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: delivered a record gross margin of 84.9%

#### 23.9 Apple's market value decreased by nearly $200 billion.

- Mention：`mention:f683a5be…`；Family：`MARKET_MOVEMENT`；Assertion：`ACTUAL`
- 参与者：Apple (SUBJECT)
- 数量：nearly $200 billion [market_value_change]
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:d6f7a015…` — The Magnificent Seven stocks declined by about 2% to a two-month low.；**需拆分复核**（该簇人工核定 FP pair=3）
- Package：`package:2a1f2600…` — The Magnificent Seven stocks declined by about 2% to a two-month low.

> Evidence: Apple has erased nearly $200 billion.

### 24. Micron shares surge to all-time high as Wall Street cheers unprecedented contract visibility

- 来源：proactiveinvestors.com
- 发布时间：2026-06-25T16:27:00+00:00
- Message ID：`doxatlas:raw_media:6b5d042a-9236-453b-8b60-2ba6c2f16b4f`
- 原文：https://www.proactiveinvestors.com/companies/news/1094520/micron-shares-surge-to-all-time-high-as-wall-street-cheers-unprecedented-contract-visibility-1094520.html
- Mention 数：15

#### 24.1 Micron reported fiscal third-quarter revenue of $41.5 billion.

- Mention：`mention:02fdfbbe…`；Family：`FINANCIAL_PERFORMANCE`；Assertion：`ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：$41.5 billion [revenue]
- 时间：fiscal third-quarter / UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:bb912535…` — Micron Technology reported fiscal Q3 2026 financial results, including revenue of $41.46 billion and adjusted earnings per share of $25.11.；**需拆分复核**（该簇人工核定 FP pair=327）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: Micron reported fiscal third-quarter revenue of $41.5 billion, up 74% year-over-year and well above the Street's $35.9 billion estimate.

#### 24.2 Micron's data center revenue reached an annualized run rate of approximately $100 billion.

- Mention：`mention:0ba10094…`；Family：`FINANCIAL_PERFORMANCE`；Assertion：`ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：approximately $100 billion [data_center_revenue_run_rate]
- 时间：fiscal third-quarter / UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:e89133b8…` — Micron Technology's data centre revenue reached an annualised run rate of about $100 billion.；**需拆分复核**（该簇人工核定 FP pair=0）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: Data center revenue hit an annualized run rate of approximately $100 billion.

#### 24.3 Micron shares surged more than 15% to a record high of around $1,208 on June 25, 2026.

- Mention：`mention:2846e0c9…`；Family：`MARKET_MOVEMENT`；Assertion：`ACTUAL`
- 参与者：Micron Technology Inc (SUBJECT)
- 数量：around $1,208 [share_price]；more than 15% [price_change_percent]
- 时间：2026-06-25T00:00:00 / DAY
- Evidence：`VERIFIED`
- Atomic：`atomic:471dce1e…` — Micron Technology stock gained roughly 15% in after-hours trading following the announcement of its fiscal Q3 2026 results.；**需拆分复核**（该簇人工核定 FP pair=39）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: Micron Technology Inc (NASDAQ:MU) shares soared more than 15% to a record high of around $1,208 Thursday

#### 24.4 Micron raised its 2026 capital expenditure forecast.

- Mention：`mention:2eb745ec…`；Family：`GUIDANCE_EXPECTATION`；Assertion：`ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：—
- 时间：2026 / UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:fe9b7445…` — Micron expects fiscal fourth-quarter 2026 capital expenditures of around $10 billion and total fiscal 2026 capital spending of approximately $27 billion.；**需拆分复核**（该簇人工核定 FP pair=2）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: Micron raised its 2026 capital expenditure forecast

#### 24.5 Micron expects strategic customer agreements to eventually cover at least half of total company revenue.

- Mention：`mention:6fbfa9a2…`；Family：`GUIDANCE_EXPECTATION`；Assertion：`EXPECTED`
- 参与者：Micron (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:d600f32b…` — Micron Technology announced 16 signed Strategic Customer Agreements, which are multi-year deals locking in volume and providing pricing visibility for memory supply.；**需拆分复核**（该簇人工核定 FP pair=84）
- Package：`package:d12945f7…` — Micron is investing at record levels in technology, products and supply.；**边界错误**：June 22 Anthropic partnership/investment is a different bounded announcement from the earnings-origin SCA/investment commentary.

> Evidence: Micron expects SCAs to eventually cover at least half of total company revenue, generating roughly $100 billion in remaining performance obligations.

#### 24.6 Micron guided fourth-quarter revenue to $50 billion.

- Mention：`mention:8288d380…`；Family：`GUIDANCE_EXPECTATION`；Assertion：`EXPECTED`
- 参与者：Micron (ACTOR)
- 数量：$50 billion [revenue]
- 时间：Fourth-quarter / UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:a464025a…` — Micron provided financial guidance projecting $50 billion in revenue and $31 in EPS.；**需拆分复核**（该簇人工核定 FP pair=49）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: Fourth-quarter guidance was equally striking, with Micron projecting revenue of $50 billion against the Street's $43.6 billion estimate.

#### 24.7 Bank of America reiterated its Buy rating on Micron and lifted its price target to $1,550.

- Mention：`mention:902b8b08…`；Family：`ANALYST_ACTION`；Assertion：`ACTUAL`
- 参与者：Bank of America (ACTOR)；Micron (SUBJECT)
- 数量：$1,550 [price_target]
- 时间：2026-06-25T00:00:00 / DAY
- Evidence：`VERIFIED`
- Atomic：`atomic:14d005d5…` — Bank of America reiterated its Buy rating on Micron and lifted its price target to $1,550.
- Package：`package:b66a7ef5…` — Bank of America reiterated its Buy rating on Micron and lifted its price target to $1,550.

> Evidence: Bank of America reiterated its Buy rating and lifted its price target to $1,550 from $1,500

#### 24.8 Micron signaled meaningfully higher capital expenditure spending in 2027.

- Mention：`mention:90fc1a32…`；Family：`GUIDANCE_EXPECTATION`；Assertion：`EXPECTED`
- 参与者：Micron (ACTOR)
- 数量：—
- 时间：2027 / UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:487518cb…` — Micron expects quarterly capital expenditures in fiscal 2027 to exceed fiscal fourth-quarter 2026 levels, with more than half of the increase coming from construction spending.；**需拆分复核**（该簇人工核定 FP pair=0）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: signalled meaningfully higher spending in 2027

#### 24.9 Micron expects free cash flow margins to approach 50-60%.

- Mention：`mention:9c672feb…`；Family：`GUIDANCE_EXPECTATION`；Assertion：`EXPECTED`
- 参与者：Micron (ACTOR)
- 数量：50-60% [free_cash_flow_margin]
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:eb527bb5…` — Micron expects free cash flow margins to approach 50-60%.
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: free cash flow margins expected to approach 50-60%

#### 24.10 Micron's strategic customer agreements include price floors and ceilings, are backed by cash deposits and financial commitments, and carry no termination provisions.

- Mention：`mention:ab793f71…`；Family：`COMMERCIAL_OPERATION`；Assertion：`ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:d600f32b…` — Micron Technology announced 16 signed Strategic Customer Agreements, which are multi-year deals locking in volume and providing pricing visibility for memory supply.；**需拆分复核**（该簇人工核定 FP pair=84）
- Package：`package:d12945f7…` — Micron is investing at record levels in technology, products and supply.；**边界错误**：June 22 Anthropic partnership/investment is a different bounded announcement from the earnings-origin SCA/investment commentary.

> Evidence: The deals include price floors and ceilings, are backed by cash deposits and financial commitments, and carry no termination provisions.

#### 24.11 Micron reported non-GAAP earnings per share of $25.11.

- Mention：`mention:ad9f92f0…`；Family：`FINANCIAL_PERFORMANCE`；Assertion：`ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：$25.11 [non_gaap_eps]
- 时间：fiscal third-quarter / UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:58553e03…` — Micron Technology reported earnings per share of $25.11.；**需拆分复核**（该簇人工核定 FP pair=5）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: non-GAAP earnings per share of $25.11 doubled quarter-over-quarter and surpassed expectations of $20.86.

#### 24.12 Micron announced plans to return 100% of excess free cash flow to shareholders beginning in December 2026.

- Mention：`mention:b9ca946a…`；Family：`TRANSACTION_CAPITAL`；Assertion：`PLANNED`
- 参与者：Micron (ACTOR)；shareholders (TARGET)
- 数量：100% [excess_free_cash_flow_return_percentage]
- 时间：2026-12-01T00:00:00 / MONTH
- Evidence：`VERIFIED`
- Atomic：`atomic:61a33de5…` — Micron plans to return 100% of its excess cash to shareholders beginning December 9, 2026.；**需拆分复核**（该簇人工核定 FP pair=0）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: Micron announced plans to return 100% of excess free cash flow to shareholders beginning in December, once CHIPS Act restrictions on certain uses of cash expire.

#### 24.13 Micron has 16 strategic customer agreements in place.

- Mention：`mention:c220b185…`；Family：`COMMERCIAL_OPERATION`；Assertion：`ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：16 SCAs [strategic_customer_agreements_count]
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:d600f32b…` — Micron Technology announced 16 signed Strategic Customer Agreements, which are multi-year deals locking in volume and providing pricing visibility for memory supply.；**需拆分复核**（该簇人工核定 FP pair=84）
- Package：`package:d12945f7…` — Micron is investing at record levels in technology, products and supply.；**边界错误**：June 22 Anthropic partnership/investment is a different bounded announcement from the earnings-origin SCA/investment commentary.

> Evidence: Micron now has 16 SCAs in place, with 14 of those carrying cumulative minimum revenue commitments of approximately $100 billion over the remaining agreement terms.

#### 24.14 Micron reported gross margin of 84.9%.

- Mention：`mention:c4a84a40…`；Family：`FINANCIAL_PERFORMANCE`；Assertion：`ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：84.9% [gross_margin]
- 时间：fiscal third-quarter / UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:486603c4…` — Micron reported a quarterly gross margin of 84.6%, exceeding its guidance of 81%.；**需拆分复核**（该簇人工核定 FP pair=23）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: Gross margin came in at 84.9%, topping consensus of 81.7%

#### 24.15 Micron guided fourth-quarter gross margin to roughly 86%.

- Mention：`mention:de81534d…`；Family：`GUIDANCE_EXPECTATION`；Assertion：`EXPECTED`
- 参与者：Micron (ACTOR)
- 数量：roughly 86% [gross_margin]
- 时间：Fourth-quarter / UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:2c9c41c1…` — Micron Technology guided gross margin to about 86%.；**需拆分复核**（该簇人工核定 FP pair=6）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: Gross margin is expected to reach roughly 86%, with non-GAAP EPS guided to $31.

### 25. Micron Just Locked In $100 Billion in Sales, and Wall Street Thinks the Boom-Bust Chip Cycle Is Dead

- 来源：247wallst.com
- 发布时间：2026-06-25T16:30:05+00:00
- Message ID：`doxatlas:raw_media:1e515897-64d4-4896-b936-10a04501eff4`
- 原文：https://247wallst.com/investing/2026/06/25/micron-just-locked-in-100-billion-in-sales-and-wall-street-thinks-the-boom-bust-chip-cycle-is-dead/
- Mention 数：18

#### 25.1 Micron reported free cash flow of $18.304 billion for fiscal Q3.

- Mention：`mention:08817ceb…`；Family：`FINANCIAL_PERFORMANCE`；Assertion：`ACTUAL`
- 参与者：Micron (SUBJECT)
- 数量：$18.304 billion [free_cash_flow]
- 时间：fiscal Q3 / UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:bb912535…` — Micron Technology reported fiscal Q3 2026 financial results, including revenue of $41.46 billion and adjusted earnings per share of $25.11.；**需拆分复核**（该簇人工核定 FP pair=327）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: Free cash flow was a robust $18.304 billion in the quarter

#### 25.2 Micron reported Cloud Memory segment revenue of $13.769 billion and Core Data Center revenue of $11.524 billion for fiscal Q3.

- Mention：`mention:0b007370…`；Family：`FINANCIAL_PERFORMANCE`；Assertion：`ACTUAL`
- 参与者：Micron (SUBJECT)
- 数量：$13.769 billion [cloud_memory_revenue]；$11.524 billion [core_data_center_revenue]
- 时间：fiscal Q3 / UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:bb912535…` — Micron Technology reported fiscal Q3 2026 financial results, including revenue of $41.46 billion and adjusted earnings per share of $25.11.；**需拆分复核**（该簇人工核定 FP pair=327）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: Micron’s Cloud Memory segment alone did $13.769 billion, with Core Data Center another $11.524 billion.

#### 25.3 Micron signed 16 long-term customer agreements, with 14 agreements securing approximately $100 billion in minimum guaranteed revenue through 2030.

- Mention：`mention:0ddc28b9…`；Family：`COMMERCIAL_OPERATION`；Assertion：`ACTUAL`
- 参与者：Micron Technology (ACTOR)
- 数量：16 long-term customer agreements [agreement_count]；$100 billion [guaranteed_revenue]
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:d600f32b…` — Micron Technology announced 16 signed Strategic Customer Agreements, which are multi-year deals locking in volume and providing pricing visibility for memory supply.；**需拆分复核**（该簇人工核定 FP pair=84）
- Package：`package:d12945f7…` — Micron is investing at record levels in technology, products and supply.；**边界错误**：June 22 Anthropic partnership/investment is a different bounded announcement from the earnings-origin SCA/investment commentary.

> Evidence: Micron Technology (NASDAQ:MU | MU Price Prediction) had signed 16 long-term customer agreements, 14 of them locking in roughly $100 billion in minimum guaranteed revenue through 2030

#### 25.4 Qualcomm signed two hyperscale deals for custom chips.

- Mention：`mention:0f47a295…`；Family：`COMMERCIAL_OPERATION`；Assertion：`ACTUAL`
- 参与者：Qualcomm (ACTOR)
- 数量：two hyperscale deals [deal_count]
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:356b9951…` — Qualcomm signed two hyperscale deals for custom chips.
- Package：`package:c8772c86…` — Qualcomm signed two hyperscale deals for custom chips.

> Evidence: said it signed two hyperscale deals for custom chips, one in the United States, one Chinese

#### 25.5 Qualcomm raised its non-handset revenue target to $40 billion by 2029.

- Mention：`mention:518264ea…`；Family：`GUIDANCE_EXPECTATION`；Assertion：`EXPECTED`
- 参与者：Qualcomm (ACTOR)
- 数量：$40 billion [non_handset_revenue_target]
- 时间：by 2029 / UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:961d23cc…` — Qualcomm raised its non-handset revenue target to $40 billion by 2029.
- Package：`package:b59befa9…` — Qualcomm raised its non-handset revenue target to $40 billion by 2029.

> Evidence: Qualcomm raised its non-handset revenue target to $40 billion by 2029, nearly double its prior forecast, with roughly $15 billion from data center.

#### 25.6 Qualcomm named Meta as the first customer for its new data center CPU.

- Mention：`mention:66f20be8…`；Family：`COMMERCIAL_OPERATION`；Assertion：`ACTUAL`
- 参与者：Qualcomm (ACTOR)；META (COUNTERPARTY)
- 数量：—
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:aa857b65…` — Qualcomm named Meta as the first customer for its new data center CPU.；**需拆分复核**（该簇人工核定 FP pair=1）
- Package：`package:7436f3cb…` — Qualcomm named Meta as the first customer for its new data center CPU.

> Evidence: Qualcomm “named META as its first customer for its new data center CPU

#### 25.7 Qualcomm reported Q2 handset revenue of $6.024 billion, a 13% year-over-year decline.

- Mention：`mention:6cf6f9b8…`；Family：`FINANCIAL_PERFORMANCE`；Assertion：`ACTUAL`
- 参与者：Qualcomm (SUBJECT)
- 数量：$6.024 billion [handset_revenue]
- 时间：Q2 / UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:e286cb69…` — Qualcomm reported Q2 handset revenue of $6.024 billion, a 13% year-over-year decline.
- Package：`package:6876b1bd…` — Qualcomm reported Q2 handset revenue of $6.024 billion, a 13% year-over-year decline.

> Evidence: Qualcomm’s Q2 handset revenue had already fallen 13% year-over-year to $6.024 billion, with memory supply constraints among Chinese OEMs cited as the cause.

#### 25.8 Micron reported fiscal Q3 capital expenditures of $7.826 billion.

- Mention：`mention:703645b8…`；Family：`FINANCIAL_PERFORMANCE`；Assertion：`ACTUAL`
- 参与者：Micron (SUBJECT)
- 数量：$7.826 billion [capex]
- 时间：Q3 / UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:c84ff842…` — Micron Technology reported fiscal third quarter 2026 capital expenditures of $7.1 billion.；**需拆分复核**（该簇人工核定 FP pair=0）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: Q3 capex was $7.826 billion, up 166.37% year-over-year

#### 25.9 UBS tripled its price target for Micron.

- Mention：`mention:71e01eb3…`；Family：`ANALYST_ACTION`；Assertion：`ACTUAL`
- 参与者：UBS (ACTOR)；Micron (TARGET)
- 数量：—
- 时间：last month / 2026-05-01T00:00:00 / 2026-05-31T00:00:00 / MONTH
- Evidence：`VERIFIED`
- Atomic：`atomic:0465adcd…` — UBS tripled its price target for Micron.
- Package：`package:e0c5d3a5…` — UBS tripled its price target for Micron.

> Evidence: UBS tripled Micron’s price target last month

#### 25.10 Micron reported GAAP gross margin of 84.6% for fiscal Q3.

- Mention：`mention:78464e1f…`；Family：`FINANCIAL_PERFORMANCE`；Assertion：`ACTUAL`
- 参与者：Micron (SUBJECT)
- 数量：84.6% [gaap_gross_margin]
- 时间：fiscal Q3 / UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:486603c4…` — Micron reported a quarterly gross margin of 84.6%, exceeding its guidance of 81%.；**需拆分复核**（该簇人工核定 FP pair=23）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: GAAP gross margin was 84.6%, against 37.7% a year earlier.

#### 25.11 Qualcomm's Dragonfly C1000 is expected to ship to Meta in 2028.

- Mention：`mention:7bdd2db0…`；Family：`PRODUCT_SCIENCE`；Assertion：`EXPECTED`
- 参与者：Qualcomm (ACTOR)；Meta (TARGET)；Dragonfly C1000 (SUBJECT)
- 数量：—
- 时间：2028-01-01T00:00:00 / 2028-12-31T00:00:00 / YEAR
- Evidence：`VERIFIED`
- Atomic：`atomic:aa857b65…` — Qualcomm named Meta as the first customer for its new data center CPU.；**需拆分复核**（该簇人工核定 FP pair=1）
- Package：`package:7436f3cb…` — Qualcomm named Meta as the first customer for its new data center CPU.

> Evidence: until the Dragonfly C1000 actually ships to Meta in 2028.

#### 25.12 Micron's stock market capitalization crossed $1 trillion.

- Mention：`mention:840f5d62…`；Family：`MARKET_MOVEMENT`；Assertion：`ACTUAL`
- 参与者：Micron (SUBJECT)
- 数量：$1 trillion [market_cap]
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:c76f1270…` — Micron stock traded nearly 14% higher as of 11:50 a.m. ET.；**需拆分复核**（该簇人工核定 FP pair=4）
- Package：`package-boundary-split:7f911bb6…` — Micron stock traded nearly 14% higher as of 11:50 a.m. ET.

> Evidence: , and the stock crossed $1 trillion in market cap alongside SK Hynix.

#### 25.13 Micron guided fiscal Q4 revenue to approximately $50 billion plus or minus $1.0 billion and gross margin to around 86%.

- Mention：`mention:85ce228b…`；Family：`GUIDANCE_EXPECTATION`；Assertion：`EXPECTED`
- 参与者：Micron (ACTOR)
- 数量：$50 billion [revenue_guidance]；86% [gross_margin_guidance]
- 时间：Q4 / UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:2c9c41c1…` — Micron Technology guided gross margin to about 86%.；**需拆分复核**（该簇人工核定 FP pair=6）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: Micron is guiding Q4 to around 86% on $50 billion plus or minus $1.0 billion in revenue.

#### 25.14 Qualcomm shares rose 12%.

- Mention：`mention:aeb564c8…`；Family：`MARKET_MOVEMENT`；Assertion：`ACTUAL`
- 参与者：Qualcomm (SUBJECT)
- 数量：12% [stock_price_change_percent]
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:7d9f5eee…` — Qualcomm shares rose 12%.
- Package：`package:cb954f7f…` — Qualcomm shares rose 12%.

> Evidence: Qualcomm shares rose 12% on the data center news

#### 25.15 Micron signaled capital expenditures of over $40 billion for the next year.

- Mention：`mention:bce6d476…`；Family：`GUIDANCE_EXPECTATION`；Assertion：`PLANNED`
- 参与者：Micron (ACTOR)
- 数量：$40 billion [capex_guidance]
- 时间：next year / UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:fe9b7445…` — Micron expects fiscal fourth-quarter 2026 capital expenditures of around $10 billion and total fiscal 2026 capital spending of approximately $27 billion.；**需拆分复核**（该簇人工核定 FP pair=2）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: , and the company has signaled capex over $40 billion next year, with roughly $20 billion going to construction and clean rooms.

#### 25.16 Micron reported fiscal Q3 revenue of $41.456 billion.

- Mention：`mention:c6cbef71…`；Family：`FINANCIAL_PERFORMANCE`；Assertion：`ACTUAL`
- 参与者：Micron (SUBJECT)
- 数量：$41.456 billion [revenue]
- 时间：fiscal Q3 / UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:bb912535…` — Micron Technology reported fiscal Q3 2026 financial results, including revenue of $41.46 billion and adjusted earnings per share of $25.11.；**需拆分复核**（该簇人工核定 FP pair=327）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: Micron’s fiscal Q3 revenue came in at $41.456 billion, a 17.60% beat on consensus and 345.72% year-over-year growth from the $9.3 billion Micron printed in the prior-year quarter.

#### 25.17 Micron stock rose 16%.

- Mention：`mention:c6f31379…`；Family：`MARKET_MOVEMENT`；Assertion：`ACTUAL`
- 参与者：Micron (SUBJECT)
- 数量：16% [stock_price_change_percent]
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:471dce1e…` — Micron Technology stock gained roughly 15% in after-hours trading following the announcement of its fiscal Q3 2026 results.；**需拆分复核**（该簇人工核定 FP pair=39）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: , and the stock was up 16% because, in her words, “Memory has always been just been boom and then bust. But Micron is essentially telling everyone that’s completely over.”

#### 25.18 Micron reported non-GAAP EPS of $25.11 for fiscal Q3.

- Mention：`mention:d2766166…`；Family：`FINANCIAL_PERFORMANCE`；Assertion：`ACTUAL`
- 参与者：Micron (SUBJECT)
- 数量：$25.11 [non_gaap_eps]
- 时间：fiscal Q3 / UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:58553e03…` — Micron Technology reported earnings per share of $25.11.；**需拆分复核**（该簇人工核定 FP pair=5）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: Non-GAAP EPS landed at $25.11 against a $20.2843 consensus, the seventh consecutive quarter of beating Wall Street.

### 26. Tim Cook Calls the Memory Crisis a "Hundred-Year Flood"

- 来源：finance.yahoo.com
- 发布时间：2026-06-25T17:01:40+00:00
- Message ID：`doxatlas:raw_media:d42584c3-2d2d-4559-affc-ccae5b388ce1`
- 原文：https://finance.yahoo.com/technology/articles/tim-cook-calls-memory-crisis-170140774.html
- Mention 数：10

#### 26.1 Suppliers are redirecting production toward high-bandwidth memory used in AI servers.

- Mention：`mention:165ff3f1…`；Family：`PRODUCTION_SUPPLY`；Assertion：`ONGOING`
- 参与者：suppliers (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:ec314a84…` — Suppliers are redirecting production toward high-bandwidth memory used in AI servers.
- Package：`package:1446c5fa…` — Some semiconductor suppliers are reallocating cleanroom space from NAND flash to DRAM production, constraining NAND supply growth.

> Evidence: as suppliers redirect production toward high-bandwidth memory used in AI servers.

#### 26.2 Apple announced price hikes across its MacBook and iPad lineup.

- Mention：`mention:1b1fbcba…`；Family：`COMMERCIAL_OPERATION`；Assertion：`ACTUAL`
- 参与者：Apple (ACTOR)
- 数量：$100 to $300 [price_increase_range]
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:f2be60db…` — Apple increased prices on MacBook Neo, MacBook Air, MacBook Pro, iPad Pro, iPad Air, HomePod, HomePod mini, and Apple TV due to tightening supplies of memory and storage components.；**需拆分复核**（该簇人工核定 FP pair=0）
- Package：`package:a3df4c2e…` — Apple increased prices on MacBook Neo, MacBook Air, MacBook Pro, iPad Pro, iPad Air, HomePod, HomePod mini, and Apple TV due to tightening supplies of memory and storage components.

> Evidence: Apple (NASDAQ:AAPL) fell 0.56% intraday after the company announced price hikes across its MacBook and iPad lineup, its first formal move to pass higher memory and storage costs on to consumers. Price increases range from $100 to $300 across the lineup.

#### 26.3 Micron reported gross margins of 84.9% in its most recent quarter.

- Mention：`mention:20aad98f…`；Family：`FINANCIAL_PERFORMANCE`；Assertion：`ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：84.9% [gross_margin]
- 时间：most recent quarter / UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:486603c4…` — Micron reported a quarterly gross margin of 84.6%, exceeding its guidance of 81%.；**需拆分复核**（该簇人工核定 FP pair=23）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: Micron (NASDAQ:MU) reported gross margins of 84.9% in its most recent quarter, up from 39% a year ago,

#### 26.4 Price increases of $150-$200 are expected across the iPhone lineup.

- Mention：`mention:253ff5fa…`；Family：`GUIDANCE_EXPECTATION`；Assertion：`EXPECTED`
- 参与者：iPhone lineup (SUBJECT)
- 数量：$150-$200 [price_increase_range]
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:feaceb0f…` — Price increases of $150-$200 are expected across the iPhone lineup.
- Package：`package:f86074f9…` — Price increases of $150-$200 are expected across the iPhone lineup.

> Evidence: with price increases of $150-$200 expected across the lineup.

#### 26.5 Memory and storage prices quadrupled in the past three quarters.

- Mention：`mention:2839d491…`；Family：`MARKET_MOVEMENT`；Assertion：`ACTUAL`
- 参与者：Memory and storage (SUBJECT)
- 数量：quadrupled [price_change_factor]
- 时间：past three quarters / UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:0972f436…` — Memory and storage prices quadrupled in the past three quarters.
- Package：`package:505a6a1d…` — Memory and storage prices quadrupled in the past three quarters.

> Evidence: Memory and storage prices have quadrupled in the past three quarters, according to Counterpoint Research,

#### 26.6 IDC expects all new iPhone models to move to 12GB of RAM.

- Mention：`mention:897a4113…`；Family：`GUIDANCE_EXPECTATION`；Assertion：`EXPECTED`
- 参与者：IDC (ACTOR)；new iPhone models (SUBJECT)
- 数量：12GB [ram_capacity]
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:b43782f3…` — IDC expects all new iPhone models to move to 12GB of RAM.
- Package：`package:f14acdb5…` — IDC expects all new iPhone models to move to 12GB of RAM.

> Evidence: IDC expects all new iPhone models to move to 12GB of RAM as Apple pushes Apple Intelligence features that require more memory,

#### 26.7 IDC sees Apple's average selling price rising 12% this year.

- Mention：`mention:c03a1c54…`；Family：`GUIDANCE_EXPECTATION`；Assertion：`EXPECTED`
- 参与者：IDC (ACTOR)；Apple (SUBJECT)
- 数量：12% [average_selling_price_change]
- 时间：2026-01-01T00:00:00 / 2026-12-31T00:00:00 / YEAR
- Evidence：`VERIFIED`
- Atomic：`atomic:01645098…` — IDC sees Apple's average selling price rising 12% this year.
- Package：`package:5adc5d2d…` — IDC sees Apple's average selling price rising 12% this year.

> Evidence: and sees Apple's average selling price rising 12% this year.

#### 26.8 Roughly 54% of iPhones shipped since 2022 will not support the full new Siri experience.

- Mention：`mention:c2dddc5a…`；Family：`PRODUCT_SCIENCE`；Assertion：`ACTUAL`
- 参与者：iPhones shipped since 2022 (SUBJECT)
- 数量：54% [share_of_units]
- 时间：2022-01-01T00:00:00 / INTERVAL
- Evidence：`VERIFIED`
- Atomic：`atomic:25e57cf4…` — Roughly 54% of iPhones shipped since 2022 will not support the full new Siri experience.
- Package：`package:4beafa57…` — Roughly 54% of iPhones shipped since 2022 will not support the full new Siri experience.

> Evidence: Roughly 54% of iPhones shipped since 2022 won't support the full new Siri experience,

#### 26.9 Counterpoint estimates that higher component costs could add roughly $200 per iPhone for Apple.

- Mention：`mention:e12a5a64…`；Family：`GUIDANCE_EXPECTATION`；Assertion：`EXPECTED`
- 参与者：Counterpoint (ACTOR)；Apple (AFFECTED)
- 数量：$200 per iPhone [cost_increase_per_unit]
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:fce90089…` — Counterpoint estimates that higher component costs could add roughly $200 per iPhone for Apple.
- Package：`package:fd0ebddc…` — Counterpoint estimates that higher component costs could add roughly $200 per iPhone for Apple.

> Evidence: Counterpoint estimates the higher component costs could add roughly $200 per iPhone for Apple,

#### 26.10 CEO Tim Cook described the memory crisis as a 'hundred-year flood'.

- Mention：`mention:ef71600a…`；Family：`OTHER`；Assertion：`ACTUAL`
- 参与者：Tim Cook (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:8871912b…` — CEO Tim Cook described the memory crisis as a 'hundred-year flood'.
- Package：`package:e89d1388…` — CEO Tim Cook described the memory crisis as a 'hundred-year flood'.

> Evidence: CEO Tim Cook told the Wall Street Journal it was a "hundred-year flood."

### 27. Sandisk Stock Spikes 15% After Citi Unleashes Massive Price Target Hike

- 来源：finance.yahoo.com
- 发布时间：2026-06-25T17:09:34+00:00
- Message ID：`doxatlas:raw_media:4a7c28e1-f1dd-4efc-8d16-80debc253418`
- 原文：https://finance.yahoo.com/markets/stocks/articles/sandisk-stock-spikes-15-citi-170934290.html
- Mention 数：3

#### 27.1 Sandisk shares increased by approximately 15% in early trading on Thursday.

- Mention：`mention:ac3e0d7e…`；Family：`MARKET_MOVEMENT`；Assertion：`ACTUAL`
- 参与者：Sandisk (SUBJECT)
- 数量：about 15% [stock_price_change_percent]
- 时间：2026-06-25T00:00:00 / DAY
- Evidence：`VERIFIED`
- Atomic：`atomic:abde442d…` — Sandisk stock price increased by 11.2% on Thursday morning.；**需拆分复核**（该簇人工核定 FP pair=2）
- Package：`package:99a65e40…` — Sandisk stock price increased by 11.2% on Thursday morning.；**边界错误**：Sandisk Thursday-morning move and the multi-company after-hours move have different temporal/entity boundaries; the first Atomic is also polluted by a Citi target mention.

> Evidence: Sandisk (NASDAQ:SNDK) shares jumped about 15% early Thursday

#### 27.2 Citi raised its price target on Sandisk to $2,500 from $2,025.

- Mention：`mention:d97788db…`；Family：`ANALYST_ACTION`；Assertion：`ACTUAL`
- 参与者：Citi (ACTOR)；Sandisk (SUBJECT)
- 数量：$2,500 [price_target]；$2,025 [previous_price_target]
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:abde442d…` — Sandisk stock price increased by 11.2% on Thursday morning.；**需拆分复核**（该簇人工核定 FP pair=2）
- Package：`package:99a65e40…` — Sandisk stock price increased by 11.2% on Thursday morning.；**边界错误**：Sandisk Thursday-morning move and the multi-company after-hours move have different temporal/entity boundaries; the first Atomic is also polluted by a Citi target mention.

> Evidence: Citi lifted its target on Sandisk to $2,500 from $2,025

#### 27.3 Sandisk is scheduled to hold an investor day in August where it may provide updates on demand expectations, technology roadmap, and capital return plans.

- Mention：`mention:dfb089a1…`；Family：`GOVERNANCE_PERSONNEL`；Assertion：`PLANNED`
- 参与者：Sandisk (ACTOR)
- 数量：—
- 时间：2026-08-01T00:00:00 / 2026-08-31T00:00:00 / MONTH
- Evidence：`VERIFIED`
- Atomic：`atomic:6319858e…` — Sandisk is scheduled to hold an investor day in August where it may provide updates on demand expectations, technology roadmap, and capital return plans.
- Package：`package:d5510532…` — Sandisk is scheduled to hold an investor day in August where it may provide updates on demand expectations, technology roadmap, and capital return plans.

> Evidence: Sandisk may also get another lift from its investor day in August. Citi said the event could bring updates on demand expectations, the company's technology roadmap and capital return plans

### 28. MU Stock Soars as Micron’s Revenue More Than Quadrupled in Q3

- 来源：barchart.com
- 发布时间：2026-06-25T20:11:24+00:00
- Message ID：`doxatlas:raw_media:181db9d6-5959-4fb0-947e-3c6680861566`
- 原文：https://www.barchart.com/story/news/3092/mu-stock-soars-as-microns-revenue-more-than-quadrupled-in-q3
- Mention 数：7

#### 28.1 Micron secured 16 long-term Strategic Customer Agreements (SCAs) locking in roughly $22 billion in cash deposits and commitments, effectively selling out its 2026 manufacturing capacity.

- Mention：`mention:0e0f17ca…`；Family：`COMMERCIAL_OPERATION`；Assertion：`ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：16 [agreement_count]；$22 billion [cash_deposits_commitments]
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:d600f32b…` — Micron Technology announced 16 signed Strategic Customer Agreements, which are multi-year deals locking in volume and providing pricing visibility for memory supply.；**需拆分复核**（该簇人工核定 FP pair=84）
- Package：`package:d12945f7…` — Micron is investing at record levels in technology, products and supply.；**边界错误**：June 22 Anthropic partnership/investment is a different bounded announcement from the earnings-origin SCA/investment commentary.

> Evidence: the company has secured 16 long-term Strategic Customer Agreements (SCAs), which have effectively “sold out” its 2026 manufacturing capacity.

#### 28.2 Micron (MU) stock price increased on June 25, 2026.

- Mention：`mention:15bc748a…`；Family：`MARKET_MOVEMENT`；Assertion：`ACTUAL`
- 参与者：Micron (MU) (SUBJECT)
- 数量：—
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY
- Evidence：`VERIFIED`
- Atomic：`atomic:c76f1270…` — Micron stock traded nearly 14% higher as of 11:50 a.m. ET.；**需拆分复核**（该簇人工核定 FP pair=4）
- Package：`package-boundary-split:7f911bb6…` — Micron stock traded nearly 14% higher as of 11:50 a.m. ET.

> Evidence: Micron (MU) stock is ripping higher on June 25

#### 28.3 Micron management committed to returning 100% of excess cash to shareholders, increasing from the prior 50%.

- Mention：`mention:95c6495b…`；Family：`GOVERNANCE_PERSONNEL`；Assertion：`ACTUAL`
- 参与者：Micron management (ACTOR)
- 数量：100% [excess_cash_return_rate]
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:61a33de5…` — Micron plans to return 100% of its excess cash to shareholders beginning December 9, 2026.；**需拆分复核**（该簇人工核定 FP pair=0）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: Management has also committed to returning 100% excess cash to shareholders moving forward, up from prior 50%

#### 28.4 Citi analysts maintained a Buy rating on Micron shares and raised the price target to $1,400.

- Mention：`mention:babd7bd2…`；Family：`ANALYST_ACTION`；Assertion：`ACTUAL`
- 参与者：Citi analysts (ACTOR)；Micron (SUBJECT)
- 数量：$1,400 [price_target]
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:90b41667…` — Citi analysts maintained a Buy rating on Micron shares and raised the price target to $1,400.
- Package：`package:4b9bca83…` — Citi analysts maintained a Buy rating on Micron shares and raised the price target to $1,400.

> Evidence: Citi analysts led by Atif Malik maintained their “Buy” rating on Micron shares and raised their price target to $1,400.

#### 28.5 Micron's adjusted gross margins reached 84.9% in fiscal Q3.

- Mention：`mention:db607da3…`；Family：`FINANCIAL_PERFORMANCE`；Assertion：`ACTUAL`
- 参与者：Micron (SUBJECT)
- 数量：84.9% [adjusted_gross_margin]
- 时间：fiscal Q3 / UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:486603c4…` — Micron reported a quarterly gross margin of 84.6%, exceeding its guidance of 81%.；**需拆分复核**（该簇人工核定 FP pair=23）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: An insatiable demand for Micron’s high-bandwidth memory (HBM) chips that power artificial intelligence (AI) data centers drove its adjusted gross margins to a whopping 84.9% in fiscal Q3.

#### 28.6 Micron reported Q3 revenue of $41.46 billion, representing a 346% year-over-year increase.

- Mention：`mention:e9faa7d6…`；Family：`FINANCIAL_PERFORMANCE`；Assertion：`ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：$41.46 billion [revenue]；346% [revenue_yoy_growth]
- 时间：Q3 / UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:bb912535…` — Micron Technology reported fiscal Q3 2026 financial results, including revenue of $41.46 billion and adjusted earnings per share of $25.11.；**需拆分复核**（该簇人工核定 FP pair=327）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: Micron (MU) stock is ripping higher on June 25 after the memory chip giant posted a blockbuster Q3, featuring a 346% year-over-year increase in revenue to $41.46 billion.

#### 28.7 Micron management guided for approximately 20% sequential revenue growth in the current quarter.

- Mention：`mention:f93e6ebc…`；Family：`GUIDANCE_EXPECTATION`；Assertion：`EXPECTED`
- 参与者：Micron management (ACTOR)
- 数量：20% [sequential_growth]
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:2c9c41c1…` — Micron Technology guided gross margin to about 86%.；**需拆分复核**（该簇人工核定 FP pair=6）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: As investors reacted to management’s impressive guidance for about a 20% sequential growth in the current quarter

### 29. AI boom keeps memory chip makers in sweet spot, says expert

- 来源：Yahoo
- 发布时间：2026-06-25T21:15:40+00:00
- Message ID：`doxatlas:raw_media:f70bfd87-b3f2-4a7f-9a1c-6a94cd5279b2`
- 原文：https://finnhub.io/api/news?id=83f579c98258ef8f6999941b3227364c4d014b9cc9cfd5300374568a2fb3e0c7
- Mention 数：1

#### 29.1 Micron disclosed that customers including Nvidia committed $22 billion to five-year take-or-pay deals for memory chip supplies.

- Mention：`mention:2d2b5b8f…`；Family：`COMMERCIAL_OPERATION`；Assertion：`ACTUAL`
- 参与者：Micron (ACTOR)；Nvidia (COUNTERPARTY)
- 数量：$22 billion [contract_value]
- 时间：UNKNOWN
- Evidence：`VERIFIED`
- Atomic：`atomic:d600f32b…` — Micron Technology announced 16 signed Strategic Customer Agreements, which are multi-year deals locking in volume and providing pricing visibility for memory supply.；**需拆分复核**（该簇人工核定 FP pair=84）
- Package：`package:d12945f7…` — Micron is investing at record levels in technology, products and supply.；**边界错误**：June 22 Anthropic partnership/investment is a different bounded announcement from the earnings-origin SCA/investment commentary.

> Evidence: Micron said on Wednesday customers such as Nvidia had committed $22 billion to lock in supplies of memory chips, playing up huge growth in five-year "take-or-pay" deals that require clients to either buy its chips or hand over cash.

### 30. Stock Market Today, June 25: Apple Drops After Raising Device Prices to Offset Higher Memory Costs

- 来源：fool.com
- 发布时间：2026-06-25T22:10:35+00:00
- Message ID：`doxatlas:raw_media:d9404d58-cd88-4ccc-a9d2-ec4004629dbc`
- 原文：https://www.fool.com/coverage/stock-market-today/2026/06/25/stock-market-today-june-25-apple-drops-after-raising-device-prices-to-offset-higher-memory-costs/
- Mention 数：11

#### 30.1 Apple trading volume reached 106.4 million shares.

- Mention：`mention:0014d28a…`；Family：`MARKET_MOVEMENT`；Assertion：`ACTUAL`
- 参与者：Apple (SUBJECT)
- 数量：106.4 million shares [trading_volume]
- 时间：2026-06-25T00:00:00 / DAY
- Evidence：`VERIFIED`
- Atomic：`atomic:9700251c…` — Apple trading volume reached 106.4 million shares.
- Package：`package:92b32148…` — Apple trading volume reached 106.4 million shares.

> Evidence: Trading volume reached 106.4 million shares, coming in about 119% above its three-month average of 48.5 million shares.

#### 30.2 Apple raised prices across Macs, iPads, home devices, and Vision Pro.

- Mention：`mention:15cdfada…`；Family：`COMMERCIAL_OPERATION`；Assertion：`ACTUAL`
- 参与者：Apple (ACTOR)
- 数量：—
- 时间：2026-06-25T00:00:00 / DAY
- Evidence：`VERIFIED`
- Atomic：`atomic:f2be60db…` — Apple increased prices on MacBook Neo, MacBook Air, MacBook Pro, iPad Pro, iPad Air, HomePod, HomePod mini, and Apple TV due to tightening supplies of memory and storage components.；**需拆分复核**（该簇人工核定 FP pair=0）
- Package：`package:a3df4c2e…` — Apple increased prices on MacBook Neo, MacBook Air, MacBook Pro, iPad Pro, iPad Air, HomePod, HomePod mini, and Apple TV due to tightening supplies of memory and storage components.

> Evidence: Apple fell after it raised prices across Macs, iPads, home devices, and Vision Pro to offset higher memory and storage costs.

#### 30.3 Microsoft stock closed at $352.83, down 3.46%.

- Mention：`mention:48b906f9…`；Family：`MARKET_MOVEMENT`；Assertion：`ACTUAL`
- 参与者：Microsoft (SUBJECT)
- 数量：$352.83 [closing_price]；down 3.46% [price_change_percent]
- 时间：2026-06-25T00:00:00 / DAY
- Evidence：`VERIFIED`
- Atomic：`atomic:dedf8a8a…` — Microsoft stock closed at $352.83, down 3.46%.
- Package：`package:54354648…` — Microsoft stock closed at $352.83, down 3.46%.

> Evidence: Microsoft (MSFT +1.69%) closed at $352.83, down 3.46%

#### 30.4 Micron Technology reported earnings.

- Mention：`mention:604b280d…`；Family：`FINANCIAL_PERFORMANCE`；Assertion：`ACTUAL`
- 参与者：Micron Technology (ACTOR)
- 数量：—
- 时间：2026-06-24T00:00:00 / DAY
- Evidence：`VERIFIED`
- Atomic：`atomic:bb912535…` — Micron Technology reported fiscal Q3 2026 financial results, including revenue of $41.46 billion and adjusted earnings per share of $25.11.；**需拆分复核**（该簇人工核定 FP pair=327）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: Micron Technology (MU 5.68%) reported stellar earnings after the bell yesterday.

#### 30.5 Micron stock soared nearly 16%.

- Mention：`mention:6a5954ed…`；Family：`MARKET_MOVEMENT`；Assertion：`ACTUAL`
- 参与者：Micron (SUBJECT)
- 数量：nearly 16% [price_change_percent]
- 时间：2026-06-25T00:00:00 / DAY
- Evidence：`VERIFIED`
- Atomic：`atomic:471dce1e…` — Micron Technology stock gained roughly 15% in after-hours trading following the announcement of its fiscal Q3 2026 results.；**需拆分复核**（该簇人工核定 FP pair=39）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: Micron stock soared nearly 16% after blockbuster earnings

#### 30.6 The Nasdaq Composite fell 0.46% to 25,359.

- Mention：`mention:c400d15d…`；Family：`MARKET_MOVEMENT`；Assertion：`ACTUAL`
- 参与者：Nasdaq Composite (SUBJECT)
- 数量：25,359 [index_value]；fell 0.46% [index_change_percent]
- 时间：2026-06-25T00:00:00 / DAY
- Evidence：`VERIFIED`
- Atomic：`atomic:6773792f…` — The Nasdaq Composite fell 0.46% to 25,359.
- Package：`package:5b976a86…` — The Nasdaq Composite fell 0.46% to 25,359.

> Evidence: the Nasdaq Composite (^IXIC 0.80%) fell 0.46% to 25,359

#### 30.7 Apple stock closed at $275.15, down 6.12%.

- Mention：`mention:d646f794…`；Family：`MARKET_MOVEMENT`；Assertion：`ACTUAL`
- 参与者：Apple (SUBJECT)
- 数量：$275.15 [closing_price]；down 6.12% [price_change_percent]
- 时间：2026-06-25T00:00:00 / DAY
- Evidence：`VERIFIED`
- Atomic：`atomic:95433fb8…` — Apple stock closed at $275.15, down 6.12%.
- Package：`package:ce706034…` — Apple stock closed at $275.15, down 6.12%.

> Evidence: The stock closed at $275.15, down 6.12%.

#### 30.8 Micron Technology reported record adjusted gross margin of about 85%.

- Mention：`mention:e89cefce…`；Family：`FINANCIAL_PERFORMANCE`；Assertion：`ACTUAL`
- 参与者：Micron Technology (ACTOR)
- 数量：about 85% [adjusted_gross_margin]
- 时间：2026-06-24T00:00:00 / DAY
- Evidence：`VERIFIED`
- Atomic：`atomic:bb912535…` — Micron Technology reported fiscal Q3 2026 financial results, including revenue of $41.46 billion and adjusted earnings per share of $25.11.；**需拆分复核**（该簇人工核定 FP pair=327）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: record adjusted gross margin of about 85%

#### 30.9 Alphabet stock ended at $343.71, down 0.46%.

- Mention：`mention:ea1e0c50…`；Family：`MARKET_MOVEMENT`；Assertion：`ACTUAL`
- 参与者：Alphabet (SUBJECT)
- 数量：$343.71 [closing_price]；down 0.46% [price_change_percent]
- 时间：2026-06-25T00:00:00 / DAY
- Evidence：`VERIFIED`
- Atomic：`atomic:2bc04e77…` — Alphabet stock ended at $343.71, down 0.46%.
- Package：`package:9d6bd8a9…` — Alphabet stock ended at $343.71, down 0.46%.

> Evidence: Alphabet (GOOGL 0.23%) ended at $343.71, down 0.46%

#### 30.10 Micron Technology revenue quadrupled year over year.

- Mention：`mention:eb9da1bf…`；Family：`FINANCIAL_PERFORMANCE`；Assertion：`ACTUAL`
- 参与者：Micron Technology (SUBJECT)
- 数量：—
- 时间：2026-06-24T00:00:00 / DAY
- Evidence：`VERIFIED`
- Atomic：`atomic:bb912535…` — Micron Technology reported fiscal Q3 2026 financial results, including revenue of $41.46 billion and adjusted earnings per share of $25.11.；**需拆分复核**（该簇人工核定 FP pair=327）
- Package：`package:f5ca4df9…` — Micron Technology reported third-quarter profit of $28.2 billion.；**边界错误**：Immediate stock reaction is external to the earnings disclosure.

> Evidence: revenue quadrupling year over year

#### 30.11 The S&P 500 slipped 0.01% to 7,357.

- Mention：`mention:fe3cee57…`；Family：`MARKET_MOVEMENT`；Assertion：`ACTUAL`
- 参与者：S&P 500 (SUBJECT)
- 数量：7,357 [index_value]；slipped 0.01% [index_change_percent]
- 时间：2026-06-25T00:00:00 / DAY
- Evidence：`VERIFIED`
- Atomic：`atomic:867ac3b9…` — S&P 500 rose 0.75% in pre-market trade on Thursday.；**需拆分复核**（该簇人工核定 FP pair=1）
- Package：`package:45e9c8e1…` — S&P 500 rose 0.75% in pre-market trade on Thursday.

> Evidence: The S&P 500 (^GSPC +0.00%) slipped 0.01% to 7,357

## 5. 最终 Package 索引

| Package | Family / Kind | 成员 Atomic | 标题 | 审计标记 |
| --- | --- | ---: | --- | --- |
| `package-boundary-split:7f911bb6…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | Micron stock traded nearly 14% higher as of 11:50 a.m. ET. | 未在已确认误包列表中 |
| `package-boundary-split:d1096f9e…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | Micron Technology stock gained more than 260% in 2026. | 未在已确认误包列表中 |
| `package:0ce3ec3c…` | `ANALYST_REPORT` / `BOUNDED` | 1 | Bernstein analyst Mark Newman expressed concern in a research note that Micron’s new strategic customer agreements could include pricing ceilings, limiting price increases and failing to avoid cyclicality. | 未在已确认误包列表中 |
| `package:0ce5f930…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | Triller Group Inc stock price increased by 259%. | 未在已确认误包列表中 |
| `package:1376ae52…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | High-bandwidth memory remains in tight supply due to continued investment by hyperscalers and enterprises in AI infrastructure. | 未在已确认误包列表中 |
| `package:1446c5fa…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 2 | Some semiconductor suppliers are reallocating cleanroom space from NAND flash to DRAM production, constraining NAND supply growth. | 未在已确认误包列表中 |
| `package:17a5c90e…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | SK Hynix's market value rose above $1 trillion. | 未在已确认误包列表中 |
| `package:1cebbc3b…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | The NASDAQ index increased by 0.7% to close at 25,654.49. | 未在已确认误包列表中 |
| `package:234ea60d…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | The KOSPI index surged more than 5% at the open on June 25, 2026, rising above 8,900 from 8,400 in the prior session. | 未在已确认误包列表中 |
| `package:24b79621…` | `ANALYST_REPORT` / `BOUNDED` | 1 | Jim Lebenthal buys Micron stock. | 未在已确认误包列表中 |
| `package:26cd0ab5…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | Japan’s Nikkei 225 index closed 4.6% higher on Thursday. | 未在已确认误包列表中 |
| `package:2a1f2600…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | The Magnificent Seven stocks declined by about 2% to a two-month low. | 未在已确认误包列表中 |
| `package:3238525d…` | `ANALYST_REPORT` / `BOUNDED` | 1 | Wedbush analyst Dan Ives stated in a research note that there are no cracks in AI demand and recommended owning core tech winners into year-end. | 未在已确认误包列表中 |
| `package:381ba50e…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | SK Hynix stock fell more than 12% on Tuesday. | 未在已确认误包列表中 |
| `package:41ecc196…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | Trading volume for SanDisk, Western Digital, and Seagate was active during the regular trading session. | 未在已确认误包列表中 |
| `package:45e9c8e1…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | S&P 500 rose 0.75% in pre-market trade on Thursday. | 未在已确认误包列表中 |
| `package:49660a11…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | Micron Technology stock trades at 16 times forward earnings estimates. | 未在已确认误包列表中 |
| `package:4b9bca83…` | `ANALYST_REPORT` / `BOUNDED` | 1 | Citi analysts maintained a Buy rating on Micron shares and raised the price target to $1,400. | 未在已确认误包列表中 |
| `package:4beafa57…` | `PRODUCT_SCIENCE` / `EPISODE` | 1 | Roughly 54% of iPhones shipped since 2022 will not support the full new Siri experience. | 未在已确认误包列表中 |
| `package:505a6a1d…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | Memory and storage prices quadrupled in the past three quarters. | 未在已确认误包列表中 |
| `package:54354648…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | Microsoft stock closed at $352.83, down 3.46%. | 未在已确认误包列表中 |
| `package:5708fe21…` | `EARNINGS_DISCLOSURE` / `BOUNDED` | 1 | Sandisk is scheduled to report earnings on August 24, 2026. | 未在已确认误包列表中 |
| `package:5adc5d2d…` | `EARNINGS_DISCLOSURE` / `BOUNDED` | 1 | IDC sees Apple's average selling price rising 12% this year. | 未在已确认误包列表中 |
| `package:5b976a86…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | The Nasdaq Composite fell 0.46% to 25,359. | 未在已确认误包列表中 |
| `package:5d9a1bbe…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | Micron Technology's stock fell 13% on Tuesday. | 未在已确认误包列表中 |
| `package:6058d5a5…` | `TRANSACTION` / `EPISODE` | 1 | On June 25, 2026, individual investors net bought roughly 490 billion won, institutions net bought around 100 billion won, and foreign investors net sold approximately 600 billion won in the South Korean market. | 未在已确认误包列表中 |
| `package:60b2bd5a…` | `PRODUCT_SCIENCE` / `EPISODE` | 1 | Defiance launches a 2X DRAM ETF. | 未在已确认误包列表中 |
| `package:6876b1bd…` | `EARNINGS_DISCLOSURE` / `BOUNDED` | 1 | Qualcomm reported Q2 handset revenue of $6.024 billion, a 13% year-over-year decline. | 未在已确认误包列表中 |
| `package:6f8e6c16…` | `ANALYST_REPORT` / `BOUNDED` | 1 | Analysts expect Sandisk's earnings to reach $33.72 per share. | 未在已确认误包列表中 |
| `package:6ff29e05…` | `ANALYST_REPORT` / `BOUNDED` | 1 | Mizuho maintains an Outperform rating and raises the price target for Micron Technology to $1375. | 未在已确认误包列表中 |
| `package:7436f3cb…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | Qualcomm named Meta as the first customer for its new data center CPU. | 未在已确认误包列表中 |
| `package:7477ca5e…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | Technology sector shares increased by 1.6%. | 未在已确认误包列表中 |
| `package:7b63ae2b…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | SK Hynix stock jumped more than 10% in early morning trading on June 25, 2026, triggering a static volatility interruption (VI) at the open. | 未在已确认误包列表中 |
| `package:87feed09…` | `TRANSACTION` / `EPISODE` | 1 | SK Hynix disclosed plans for a listing on the US Nasdaq. | 未在已确认误包列表中 |
| `package:8b2aa282…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | Samsung stock fell more than 12% on Tuesday. | 未在已确认误包列表中 |
| `package:8da9a30b…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | Sandisk is locking in long-term prices at high margins through Strategic Customer Agreements. | 未在已确认误包列表中 |
| `package:92b32148…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | Apple trading volume reached 106.4 million shares. | 未在已确认误包列表中 |
| `package:9591ed1b…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | South Korea’s Kospi index fell 10% on Tuesday, triggering a circuit breaker. | 未在已确认误包列表中 |
| `package:98341894…` | `ANALYST_REPORT` / `BOUNDED` | 1 | Needham increased its price target on Micron to $1,550 from $500 while maintaining a Buy rating. | 未在已确认误包列表中 |
| `package:9885ef27…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | Communication services sector stocks decreased by 1.9%. | 未在已确认误包列表中 |
| `package:99a65e40…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 2 | Sandisk stock price increased by 11.2% on Thursday morning. | 边界错误：Sandisk Thursday-morning move and the multi-company after-hours move have different temporal/entity boundaries; the first Atomic is also polluted by a Citi target mention. |
| `package:9d6bd8a9…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | Alphabet stock ended at $343.71, down 0.46%. | 未在已确认误包列表中 |
| `package:9e56c5c2…` | `REGULATORY_LEGAL` / `EPISODE` | 1 | The Korea Exchange (KRX) activated a buy-side sidecar mechanism shortly after the market open on June 25, 2026, suspending program trading for five minutes. | 未在已确认误包列表中 |
| `package:a3df4c2e…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | Apple increased prices on MacBook Neo, MacBook Air, MacBook Pro, iPad Pro, iPad Air, HomePod, HomePod mini, and Apple TV due to tightening supplies of memory and storage components. | 未在已确认误包列表中 |
| `package:abe7b285…` | `ANALYST_REPORT` / `BOUNDED` | 1 | UBS analysts stated that DRAM is likely to be constrained until at least halfway through 2028. | 未在已确认误包列表中 |
| `package:b4f74801…` | `ANALYST_REPORT` / `BOUNDED` | 1 | Wedbush rates Micron Technology at outperform with a price target of $1,300. | 未在已确认误包列表中 |
| `package:b59befa9…` | `EARNINGS_DISCLOSURE` / `BOUNDED` | 1 | Qualcomm raised its non-handset revenue target to $40 billion by 2029. | 未在已确认误包列表中 |
| `package:b66a7ef5…` | `ANALYST_REPORT` / `BOUNDED` | 1 | Bank of America reiterated its Buy rating on Micron and lifted its price target to $1,550. | 未在已确认误包列表中 |
| `package:c8772c86…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | Qualcomm signed two hyperscale deals for custom chips. | 未在已确认误包列表中 |
| `package:cb954f7f…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | Qualcomm shares rose 12%. | 未在已确认误包列表中 |
| `package:ce706034…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | Apple stock closed at $275.15, down 6.12%. | 未在已确认误包列表中 |
| `package:cf38c56e…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | Europe’s Stoxx 600 index rose 0.6% by early afternoon on Thursday. | 未在已确认误包列表中 |
| `package:d12945f7…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 3 | Micron is investing at record levels in technology, products and supply. | 边界错误：June 22 Anthropic partnership/investment is a different bounded announcement from the earnings-origin SCA/investment commentary. |
| `package:d33829fa…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 2 | Micron expects memory shortages to persist at least through 2028. | 未在已确认误包列表中 |
| `package:d5510532…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | Sandisk is scheduled to hold an investor day in August where it may provide updates on demand expectations, technology roadmap, and capital return plans. | 未在已确认误包列表中 |
| `package:d8100b7e…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | The Dow Jones index increased by 0.5% to close at 52,107.28. | 未在已确认误包列表中 |
| `package:dd8916aa…` | `PRODUCT_SCIENCE` / `EPISODE` | 1 | Humanoid robots carry 10 times the amount of memory as an average L2+ vehicle. | 未在已确认误包列表中 |
| `package:ddaf0a70…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | The S&P 500 index increased by 0.6% to close at 7,401.17. | 未在已确认误包列表中 |
| `package:e0c5d3a5…` | `ANALYST_REPORT` / `BOUNDED` | 1 | UBS tripled its price target for Micron. | 未在已确认误包列表中 |
| `package:e89d1388…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | CEO Tim Cook described the memory crisis as a 'hundred-year flood'. | 未在已确认误包列表中 |
| `package:f14acdb5…` | `EARNINGS_DISCLOSURE` / `BOUNDED` | 1 | IDC expects all new iPhone models to move to 12GB of RAM. | 未在已确认误包列表中 |
| `package:f2901e24…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | US Nasdaq rose 2.15% in pre-market trade on Thursday. | 未在已确认误包列表中 |
| `package:f5ca4df9…` | `EARNINGS_DISCLOSURE` / `BOUNDED` | 26 | Micron Technology reported third-quarter profit of $28.2 billion. | 边界错误：Immediate stock reaction is external to the earnings disclosure. |
| `package:f86074f9…` | `EARNINGS_DISCLOSURE` / `BOUNDED` | 1 | Price increases of $150-$200 are expected across the iPhone lineup. | 未在已确认误包列表中 |
| `package:fd0ebddc…` | `EARNINGS_DISCLOSURE` / `BOUNDED` | 1 | Counterpoint estimates that higher component costs could add roughly $200 per iPhone for Apple. | 未在已确认误包列表中 |

## 6. 已确认的全局质量警告

- Atomic：预测正对 956，其中 FP 556；主要问题是不同 metric、交易时段、产品或业务动作被误认为同一事实。
- Package：预测正对 331，其中 FP 28、FN 110；主要问题是 reaction/earnings 边界和包碎片化。
- Field Resolution：658/790 正确；UNRESOLVED 正确率 36.54%。
- 本文档用于人工浏览系统实际产物，不应替代 JSON、Registry 或逐条审计文件。
