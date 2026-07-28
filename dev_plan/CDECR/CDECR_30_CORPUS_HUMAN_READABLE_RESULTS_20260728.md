# CDECR 30 篇真实测试集：最终层级结果

> 本文档按照 CDECR 的最终业务产物组织：`Package → Atomic Event → Event Mention → Source Evidence`。文章仅作为 Mention 的来源与审计坐标，不作为结果层级。

> 本文档展示冻结运行的实际结果，而非人工修正后的 Gold。“边界错误”与“需拆分”是逐条审计已确认的问题。

## 1. 总览

- 输入文档：30；成功：30/30
- 最终层级：65 Package → 95 Atomic → 225 Mention
- Token：输入 4,403,264 / 输出 443,843 / 合计 4,847,107
- Mention P/R/F1：85.33% / 71.64% / 77.89%
- Atomic Pair P/R/F1：41.84% / 76.92% / 54.20%
- Package Pair P/R/F1：91.54% / 73.37% / 81.45%

## 2. Package 索引

| # | Package | Family / Kind | Atomic | Mention | 标题 | 审计 |
| ---: | --- | --- | ---: | ---: | --- | --- |
| 1 | `package-boundary-split:7f911bb67a…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | 4 | Micron stock traded nearly 14% higher as of 11:50 a.m. ET. | — |
| 2 | `package-boundary-split:d1096f9e04…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | 1 | Micron Technology stock gained more than 260% in 2026. | — |
| 3 | `package:0ce3ec3c1f…` | `ANALYST_REPORT` / `BOUNDED` | 1 | 1 | Bernstein analyst Mark Newman expressed concern in a research note that Micron’s new strategic customer agreements could include pricing ceilings, limiting price increases and failing to avoid cyclicality. | — |
| 4 | `package:0ce5f9300a…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | 1 | Triller Group Inc stock price increased by 259%. | — |
| 5 | `package:1376ae528d…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | 1 | High-bandwidth memory remains in tight supply due to continued investment by hyperscalers and enterprises in AI infrastructure. | — |
| 6 | `package:1446c5fae0…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 2 | 2 | Some semiconductor suppliers are reallocating cleanroom space from NAND flash to DRAM production, constraining NAND supply growth. | — |
| 7 | `package:17a5c90e61…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | 1 | SK Hynix's market value rose above $1 trillion. | — |
| 8 | `package:1cebbc3b2c…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | 1 | The NASDAQ index increased by 0.7% to close at 25,654.49. | — |
| 9 | `package:234ea60dcf…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | 3 | The KOSPI index surged more than 5% at the open on June 25, 2026, rising above 8,900 from 8,400 in the prior session. | — |
| 10 | `package:24b79621ae…` | `ANALYST_REPORT` / `BOUNDED` | 1 | 1 | Jim Lebenthal buys Micron stock. | — |
| 11 | `package:26cd0ab57a…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | 1 | Japan’s Nikkei 225 index closed 4.6% higher on Thursday. | — |
| 12 | `package:2a1f2600f5…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | 3 | The Magnificent Seven stocks declined by about 2% to a two-month low. | — |
| 13 | `package:3238525d63…` | `ANALYST_REPORT` / `BOUNDED` | 1 | 1 | Wedbush analyst Dan Ives stated in a research note that there are no cracks in AI demand and recommended owning core tech winners into year-end. | — |
| 14 | `package:381ba50e7e…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | 1 | SK Hynix stock fell more than 12% on Tuesday. | — |
| 15 | `package:41ecc1960d…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | 1 | Trading volume for SanDisk, Western Digital, and Seagate was active during the regular trading session. | — |
| 16 | `package:45e9c8e1b6…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | 2 | S&P 500 rose 0.75% in pre-market trade on Thursday. | — |
| 17 | `package:49660a11e4…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | 1 | Micron Technology stock trades at 16 times forward earnings estimates. | — |
| 18 | `package:4b9bca8396…` | `ANALYST_REPORT` / `BOUNDED` | 1 | 1 | Citi analysts maintained a Buy rating on Micron shares and raised the price target to $1,400. | — |
| 19 | `package:4beafa57bb…` | `PRODUCT_SCIENCE` / `EPISODE` | 1 | 1 | Roughly 54% of iPhones shipped since 2022 will not support the full new Siri experience. | — |
| 20 | `package:505a6a1dde…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | 1 | Memory and storage prices quadrupled in the past three quarters. | — |
| 21 | `package:5435464829…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | 1 | Microsoft stock closed at $352.83, down 3.46%. | — |
| 22 | `package:5708fe2147…` | `EARNINGS_DISCLOSURE` / `BOUNDED` | 1 | 1 | Sandisk is scheduled to report earnings on August 24, 2026. | — |
| 23 | `package:5adc5d2d81…` | `EARNINGS_DISCLOSURE` / `BOUNDED` | 1 | 1 | IDC sees Apple's average selling price rising 12% this year. | — |
| 24 | `package:5b976a8648…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | 1 | The Nasdaq Composite fell 0.46% to 25,359. | — |
| 25 | `package:5d9a1bbe9e…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | 1 | Micron Technology's stock fell 13% on Tuesday. | — |
| 26 | `package:6058d5a588…` | `TRANSACTION` / `EPISODE` | 1 | 1 | On June 25, 2026, individual investors net bought roughly 490 billion won, institutions net bought around 100 billion won, and foreign investors net sold approximately 600 billion won in the South Korean market. | — |
| 27 | `package:60b2bd5ab2…` | `PRODUCT_SCIENCE` / `EPISODE` | 1 | 2 | Defiance launches a 2X DRAM ETF. | — |
| 28 | `package:6876b1bd57…` | `EARNINGS_DISCLOSURE` / `BOUNDED` | 1 | 1 | Qualcomm reported Q2 handset revenue of $6.024 billion, a 13% year-over-year decline. | — |
| 29 | `package:6f8e6c1606…` | `ANALYST_REPORT` / `BOUNDED` | 1 | 1 | Analysts expect Sandisk's earnings to reach $33.72 per share. | — |
| 30 | `package:6ff29e057e…` | `ANALYST_REPORT` / `BOUNDED` | 1 | 1 | Mizuho maintains an Outperform rating and raises the price target for Micron Technology to $1375. | — |
| 31 | `package:7436f3cb1a…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | 2 | Qualcomm named Meta as the first customer for its new data center CPU. | — |
| 32 | `package:7477ca5e3f…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | 1 | Technology sector shares increased by 1.6%. | — |
| 33 | `package:7b63ae2bed…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | 4 | SK Hynix stock jumped more than 10% in early morning trading on June 25, 2026, triggering a static volatility interruption (VI) at the open. | — |
| 34 | `package:87feed0946…` | `TRANSACTION` / `EPISODE` | 1 | 3 | SK Hynix disclosed plans for a listing on the US Nasdaq. | — |
| 35 | `package:8b2aa282fc…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | 1 | Samsung stock fell more than 12% on Tuesday. | — |
| 36 | `package:8da9a30bee…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | 1 | Sandisk is locking in long-term prices at high margins through Strategic Customer Agreements. | — |
| 37 | `package:92b321481c…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | 1 | Apple trading volume reached 106.4 million shares. | — |
| 38 | `package:9591ed1b81…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | 1 | South Korea’s Kospi index fell 10% on Tuesday, triggering a circuit breaker. | — |
| 39 | `package:9834189450…` | `ANALYST_REPORT` / `BOUNDED` | 1 | 2 | Needham increased its price target on Micron to $1,550 from $500 while maintaining a Buy rating. | — |
| 40 | `package:9885ef27fb…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | 1 | Communication services sector stocks decreased by 1.9%. | — |
| 41 | `package:99a65e40d7…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 2 | 4 | Sandisk stock price increased by 11.2% on Thursday morning. | 边界错误 |
| 42 | `package:9d6bd8a92b…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | 1 | Alphabet stock ended at $343.71, down 0.46%. | — |
| 43 | `package:9e56c5c231…` | `REGULATORY_LEGAL` / `EPISODE` | 1 | 1 | The Korea Exchange (KRX) activated a buy-side sidecar mechanism shortly after the market open on June 25, 2026, suspending program trading for five minutes. | — |
| 44 | `package:a3df4c2e9b…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | 4 | Apple increased prices on MacBook Neo, MacBook Air, MacBook Pro, iPad Pro, iPad Air, HomePod, HomePod mini, and Apple TV due to tightening supplies of memory and storage components. | — |
| 45 | `package:abe7b28592…` | `ANALYST_REPORT` / `BOUNDED` | 1 | 2 | UBS analysts stated that DRAM is likely to be constrained until at least halfway through 2028. | — |
| 46 | `package:b4f7480155…` | `ANALYST_REPORT` / `BOUNDED` | 1 | 1 | Wedbush rates Micron Technology at outperform with a price target of $1,300. | — |
| 47 | `package:b59befa9c6…` | `EARNINGS_DISCLOSURE` / `BOUNDED` | 1 | 1 | Qualcomm raised its non-handset revenue target to $40 billion by 2029. | — |
| 48 | `package:b66a7ef51b…` | `ANALYST_REPORT` / `BOUNDED` | 1 | 1 | Bank of America reiterated its Buy rating on Micron and lifted its price target to $1,550. | — |
| 49 | `package:c8772c8640…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | 1 | Qualcomm signed two hyperscale deals for custom chips. | — |
| 50 | `package:cb954f7ffe…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | 1 | Qualcomm shares rose 12%. | — |
| 51 | `package:ce7060342c…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | 1 | Apple stock closed at $275.15, down 6.12%. | — |
| 52 | `package:cf38c56e27…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | 1 | Europe’s Stoxx 600 index rose 0.6% by early afternoon on Thursday. | — |
| 53 | `package:d12945f75e…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 3 | 20 | Micron is investing at record levels in technology, products and supply. | 边界错误 |
| 54 | `package:d33829fa2d…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 2 | 5 | Micron expects memory shortages to persist at least through 2028. | — |
| 55 | `package:d5510532c5…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | 1 | Sandisk is scheduled to hold an investor day in August where it may provide updates on demand expectations, technology roadmap, and capital return plans. | — |
| 56 | `package:d8100b7e91…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | 2 | The Dow Jones index increased by 0.5% to close at 52,107.28. | — |
| 57 | `package:dd8916aafa…` | `PRODUCT_SCIENCE` / `EPISODE` | 1 | 1 | Humanoid robots carry 10 times the amount of memory as an average L2+ vehicle. | — |
| 58 | `package:ddaf0a70ec…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | 1 | The S&P 500 index increased by 0.6% to close at 7,401.17. | — |
| 59 | `package:e0c5d3a5e8…` | `ANALYST_REPORT` / `BOUNDED` | 1 | 1 | UBS tripled its price target for Micron. | — |
| 60 | `package:e89d138864…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | 1 | CEO Tim Cook described the memory crisis as a 'hundred-year flood'. | — |
| 61 | `package:f14acdb591…` | `EARNINGS_DISCLOSURE` / `BOUNDED` | 1 | 1 | IDC expects all new iPhone models to move to 12GB of RAM. | — |
| 62 | `package:f2901e2479…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | 1 | US Nasdaq rose 2.15% in pre-market trade on Thursday. | — |
| 63 | `package:f5ca4df942…` | `EARNINGS_DISCLOSURE` / `BOUNDED` | 26 | 113 | Micron Technology reported third-quarter profit of $28.2 billion. | 边界错误 |
| 64 | `package:f86074f9ae…` | `EARNINGS_DISCLOSURE` / `BOUNDED` | 1 | 1 | Price increases of $150-$200 are expected across the iPhone lineup. | — |
| 65 | `package:fd0ebddcd7…` | `EARNINGS_DISCLOSURE` / `BOUNDED` | 1 | 1 | Counterpoint estimates that higher component costs could add roughly $200 per iPhone for Apple. | — |

## 3. Package → Atomic → Mention 最终结构

### P01. Micron stock traded nearly 14% higher as of 11:50 a.m. ET.

- Package ID：`package-boundary-split:7f911bb67a01fa26de3d08ee`
- Family / Kind：`COMPANY_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 4 Mention
- Anchor entities：COMPANY_MU, INSTRUMENT_MU
- Anchor artifact / period：— / —
- 摘要：Micron stock traded nearly 14% higher as of 11:50 a.m. ET.

#### P01-A01. Micron stock traded nearly 14% higher as of 11:50 a.m. ET.

- Atomic ID：`atomic:c76f127017553634012fa26c`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- Mention 数：4；Version：4
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / TIMESTAMP
- **人工审计：需拆分。** 该 Atomic 产生 4 个 FP pair；建议分区：june25_regular_price_move, june25_market_cap_move

##### M01. Micron stock traded nearly 14% higher as of 11:50 a.m. ET.

- Mention ID：`mention:f8c36aaa9c1cfe43d05fe5741da50f87c38fcc6561d11ded934852c5748ecd4f`
- 来源：[Why Everyone Is Talking About Micron](https://www.fool.com/investing/2026/06/25/why-everyone-is-talking-about-micron/)；`doxatlas:raw_media:49fb27c5-84b4-4e56-8791-f7ac23de38b7`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Micron stock (SUBJECT)
- 数量：nearly 14% [price_change_percent]
- 时间：2026-06-25T11:50:00 / TIMESTAMP
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron stock traded nearly 14% higher, as of 11:50 a.m. ET.

##### M02. Micron's market value increased by more than $100 billion on Thursday.

- Mention ID：`mention:308a8a9bb243c9bad23662a339295a45175fbafdabf593f4e41aaa6ed8194e3e`
- 来源：[Why Micron's blowout earnings are a headache for Apple](https://finance.yahoo.com/markets/article/why-microns-blowout-earnings-are-a-headache-for-apple-161932441.html)；`doxatlas:raw_media:6ccb4979-525c-4467-a381-538f38a4fa9c`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Micron (SUBJECT)
- 数量：more than $100 billion [market_value_change]
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron has added more than $100 billion in market value Thursday even after giving back part of its early surge.

##### M03. Micron's stock market capitalization crossed $1 trillion.

- Mention ID：`mention:840f5d62dc64a0a3063479135144a6a0924ac0f7001ba3fce3c1097c2c432cf1`
- 来源：[Micron Just Locked In $100 Billion in Sales, and Wall Street Thinks the Boom-Bust Chip Cycle Is Dead](https://247wallst.com/investing/2026/06/25/micron-just-locked-in-100-billion-in-sales-and-wall-street-thinks-the-boom-bust-chip-cycle-is-dead/)；`doxatlas:raw_media:1e515897-64d4-4896-b936-10a04501eff4`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Micron (SUBJECT)
- 数量：$1 trillion [market_cap]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：, and the stock crossed $1 trillion in market cap alongside SK Hynix.

##### M04. Micron (MU) stock price increased on June 25, 2026.

- Mention ID：`mention:15bc748ad69588856a5bf492a7f9c30373b0a7b20ecbfc99222de19f2ec128f8`
- 来源：[MU Stock Soars as Micron’s Revenue More Than Quadrupled in Q3](https://www.barchart.com/story/news/3092/mu-stock-soars-as-microns-revenue-more-than-quadrupled-in-q3)；`doxatlas:raw_media:181db9d6-5959-4fb0-947e-3c6680861566`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Micron (MU) (SUBJECT)
- 数量：—
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron (MU) stock is ripping higher on June 25

### P02. Micron Technology stock gained more than 260% in 2026.

- Package ID：`package-boundary-split:d1096f9e04a63092eccf843e`
- Family / Kind：`COMPANY_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：COMPANY_MU
- Anchor artifact / period：— / —
- 摘要：Micron Technology stock gained more than 260% in 2026.

#### P02-A01. Micron Technology stock gained more than 260% in 2026.

- Atomic ID：`atomic:c771a0d16ce91c7edc65160f`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：2026-01-01T00:00:00 / 2026-06-25T00:00:00 / INTERVAL

##### M01. Micron Technology stock gained more than 260% in 2026.

- Mention ID：`mention:403b761a8cd928d30ddde879114a0d7e2b077042919e2e3d2c9d3a2f55bd0a30`
- 来源：[Is Micron a Buy After Its Blowout Earnings Report?](https://www.fool.com/investing/2026/06/25/is-micron-a-buy-after-its-blowout-earnings-report/)；`doxatlas:raw_media:a424d484-3e57-47ed-b21d-50aeceafe593`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Micron Technology (SUBJECT)
- 数量：more than 260% [stock_return]
- 时间：2026-01-01T00:00:00 / 2026-06-25T00:00:00 / INTERVAL
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：gaining more than 260% this year alone.

### P03. Bernstein analyst Mark Newman expressed concern in a research note that Micron’s new strategic customer agreements could include pricing ceilings, limiting price increases and failing to avoid cyclicality.

- Package ID：`package:0ce3ec3c1f9b958dcd5f05ab`
- Family / Kind：`ANALYST_REPORT` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：field:25f376a6aee69b8ddb58881c
- Anchor artifact / period：— / —
- 摘要：Bernstein analyst Mark Newman expressed concern in a research note that Micron’s new strategic customer agreements could include pricing ceilings, limiting price increases and failing to avoid cyclicality.

#### P03-A01. Bernstein analyst Mark Newman expressed concern in a research note that Micron’s new strategic customer agreements could include pricing ceilings, limiting price increases and failing to avoid cyclicality.

- Atomic ID：`atomic:29028dee584579ffb52773cc`
- Family / Assertion：`ANALYST_ACTION` / `HYPOTHETICAL`
- Mention 数：1；Version：1
- 时间：UNKNOWN

##### M01. Bernstein analyst Mark Newman expressed concern in a research note that Micron’s new strategic customer agreements could include pricing ceilings, limiting price increases and failing to avoid cyclicality.

- Mention ID：`mention:8ecff04418d2918cbd6140827d7ce5fbfd4949b504fbc5c7517ded2f8519a1a6`
- 来源：[Why Everyone Is Talking About Micron](https://www.fool.com/investing/2026/06/25/why-everyone-is-talking-about-micron/)；`doxatlas:raw_media:49fb27c5-84b4-4e56-8791-f7ac23de38b7`
- Family / Assertion：`ANALYST_ACTION` / `HYPOTHETICAL`
- 参与者：Bernstein analyst Mark Newman (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Bernstein analyst Mark Newman thinks Micron’s new strategic customer agreements could include pricing ceilings, which would limit how much Micron could raise memory prices.
  - E02 `VERIFIED` / `text:0`："We wonder if the ceiling suggests limited headroom," he wrote in a recent research note, suggesting these contracts likely wouldn’t be able to avoid cyclicality.

### P04. Triller Group Inc stock price increased by 259%.

- Package ID：`package:0ce5f9300a6b55f251c997b0`
- Family / Kind：`COMPANY_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：COMPANY_ILLR
- Anchor artifact / period：— / —
- 摘要：Triller Group Inc stock price increased by 259%.

#### P04-A01. Triller Group Inc stock price increased by 259%.

- Atomic ID：`atomic:756087450af567e2c4f4673c`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：2026-06-24T00:00:00 / 2026-06-24T00:00:00 / DAY

##### M01. Triller Group Inc stock price increased by 259%.

- Mention ID：`mention:ec496c78f9997bf9c10d5c4328f339acf5cbc77dcbfcc22ca5eb63b3c81c8208`
- 来源：[Dow Surges 250 Points; Micron Posts Upbeat Q3 Earnings](https://finnhub.io/api/news?id=7316a88ccda79b3354a49965fa82f15ed81fe3722e51491463cf118f097a0143)；`doxatlas:raw_media:fce73732-1c68-4d0f-9d7f-c5800393ffff`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Triller Group Inc (SUBJECT)
- 数量：259% [stock_change_percent]
- 时间：2026-06-24T00:00:00 / 2026-06-24T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Triller Group Inc up 259%

### P05. High-bandwidth memory remains in tight supply due to continued investment by hyperscalers and enterprises in AI infrastructure.

- Package ID：`package:1376ae528d454ded2ade7d0b`
- Family / Kind：`COMPANY_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：field:15ac8f9b5ff61195783568e7, field:5afa17980eeb8cc46b8e5c0f
- Anchor artifact / period：— / —
- 摘要：High-bandwidth memory remains in tight supply due to continued investment by hyperscalers and enterprises in AI infrastructure.

#### P05-A01. High-bandwidth memory remains in tight supply due to continued investment by hyperscalers and enterprises in AI infrastructure.

- Atomic ID：`atomic:d3bf058d47d9c6fbc299c2f5`
- Family / Assertion：`PRODUCTION_SUPPLY` / `ONGOING`
- Mention 数：1；Version：1
- 时间：UNKNOWN

##### M01. High-bandwidth memory remains in tight supply due to continued investment by hyperscalers and enterprises in AI infrastructure.

- Mention ID：`mention:ed9f8bf1982b19a07d4ca848d369ed6482655d15e71ab5294010b0d3df27b819`
- 来源：[Micron CEO Says AI Memory Shortage Could Last Beyond 2028: These ETFs Are Positioned To Profit](https://www.benzinga.com/etfs/sector-etfs/26/06/60094351/micron-ceo-says-ai-memory-shortage-could-last-beyond-2028-these-etfs-are-positioned-to-profit)；`doxatlas:raw_media:bf5258ec-37f8-408b-ace2-8b016d16f725`
- Family / Assertion：`PRODUCTION_SUPPLY` / `ONGOING`
- 参与者：High-bandwidth memory (HBM) (SUBJECT)；hyperscalers and enterprises (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：High-bandwidth memory (HBM), a critical component used alongside advanced AI processors, remains in tight supply as hyperscalers and enterprises continue pouring money into AI infrastructure.

### P06. Some semiconductor suppliers are reallocating cleanroom space from NAND flash to DRAM production, constraining NAND supply growth.

- Package ID：`package:1446c5fae0007b4c708d3ee5`
- Family / Kind：`COMPANY_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：2
- 层级规模：2 Atomic / 2 Mention
- Anchor entities：field:71029c6b83c90f75b40d7e6f
- Anchor artifact / period：— / —
- 摘要：Some semiconductor suppliers are reallocating cleanroom space from NAND flash to DRAM production, constraining NAND supply growth. Suppliers are redirecting production toward high-bandwidth memory used in AI servers.

#### P06-A01. Some semiconductor suppliers are reallocating cleanroom space from NAND flash to DRAM production, constraining NAND supply growth.

- Atomic ID：`atomic:7f051714151e73d250e7afd3`
- Family / Assertion：`PRODUCTION_SUPPLY` / `ONGOING`
- Mention 数：1；Version：1
- 时间：UNKNOWN

##### M01. Some semiconductor suppliers are reallocating cleanroom space from NAND flash to DRAM production, constraining NAND supply growth.

- Mention ID：`mention:9154bf87a57969849d59b1fa5d14b28ce106c430c10ac941187d3844f35b5d45`
- 来源：[Micron CEO Expects Memory Supply To Improve Gradually In 2028, But There's No 'Line Of Sight' When It Will Catch Up To Rising AI Demand](https://www.benzinga.com/markets/tech/26/06/60089761/micron-ceo-sees-memory-supply-improving-by-2028-but-no-clear-end-in-sight-to-ai-demand-shortfallmicron-ceo-sees-memory-supply-improving-by-2028-but-no-clear-end-in-sight-to-ai-demand-shortfall)；`doxatlas:raw_media:25387f96-b423-437c-b2cd-8652aa176bd8`
- Family / Assertion：`PRODUCTION_SUPPLY` / `ONGOING`
- 参与者：suppliers (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：In NAND flash memory, Mehrotra said some suppliers are reallocating cleanroom space toward DRAM production, further constraining NAND supply growth.

#### P06-A02. Suppliers are redirecting production toward high-bandwidth memory used in AI servers.

- Atomic ID：`atomic:ec314a84284ea80ceaa11623`
- Family / Assertion：`PRODUCTION_SUPPLY` / `ONGOING`
- Mention 数：1；Version：1
- 时间：UNKNOWN

##### M01. Suppliers are redirecting production toward high-bandwidth memory used in AI servers.

- Mention ID：`mention:165ff3f1e1e47792e0a113a4693c1d1bffd109b15cc1f64ed21e787f1244e413`
- 来源：[Tim Cook Calls the Memory Crisis a "Hundred-Year Flood"](https://finance.yahoo.com/technology/articles/tim-cook-calls-memory-crisis-170140774.html)；`doxatlas:raw_media:d42584c3-2d2d-4559-affc-ccae5b388ce1`
- Family / Assertion：`PRODUCTION_SUPPLY` / `ONGOING`
- 参与者：suppliers (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：as suppliers redirect production toward high-bandwidth memory used in AI servers.

### P07. SK Hynix's market value rose above $1 trillion.

- Package ID：`package:17a5c90e61e2115aefa9b4f9`
- Family / Kind：`COMPANY_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：COMPANY_SKHY
- Anchor artifact / period：— / —
- 摘要：SK Hynix's market value rose above $1 trillion.

#### P07-A01. SK Hynix's market value rose above $1 trillion.

- Atomic ID：`atomic:b77472587c61ca4d0d0f869b`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：UNKNOWN

##### M01. SK Hynix's market value rose above $1 trillion.

- Mention ID：`mention:6f4a45641d3f33421494ed314962dfbbf423aa3b6427ce13f87a1f8aab86038d`
- 来源：[SK Hynix Unveils $29 Billion US Listing as Shares Soar Over 800%](https://finance.yahoo.com/markets/stocks/articles/sk-hynix-unveils-29-billion-135023709.html)；`doxatlas:raw_media:b24ea6b3-557e-4053-9cae-c8e419455b39`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：SK Hynix (SUBJECT)
- 数量：$1 trillion [market_cap]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：lifting the company's market value above $1 trillion.

### P08. The NASDAQ index increased by 0.7% to close at 25,654.49.

- Package ID：`package:1cebbc3b2cd1faf5a7d3f8f7`
- Family / Kind：`COMPANY_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：INSTITUTION_NASDAQ
- Anchor artifact / period：— / —
- 摘要：The NASDAQ index increased by 0.7% to close at 25,654.49.

#### P08-A01. The NASDAQ index increased by 0.7% to close at 25,654.49.

- Atomic ID：`atomic:3a47d57ba57e4305737cf083`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：2026-06-24T00:00:00 / 2026-06-24T00:00:00 / DAY

##### M01. The NASDAQ index increased by 0.7% to close at 25,654.49.

- Mention ID：`mention:3e5ed70e1061ff3a64ddc225a812be4620b10b56adbc962810fa5a704c889c3a`
- 来源：[Dow Surges 250 Points; Micron Posts Upbeat Q3 Earnings](https://finnhub.io/api/news?id=7316a88ccda79b3354a49965fa82f15ed81fe3722e51491463cf118f097a0143)；`doxatlas:raw_media:fce73732-1c68-4d0f-9d7f-c5800393ffff`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：NASDAQ (SUBJECT)
- 数量：0.7% [index_change_percent]；25,654.49 [index_close_value]
- 时间：2026-06-24T00:00:00 / 2026-06-24T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：NASDAQ up 0.7% to 25,654.49

### P09. The KOSPI index surged more than 5% at the open on June 25, 2026, rising above 8,900 from 8,400 in the prior session.

- Package ID：`package:234ea60dcf7131f97522fd3f`
- Family / Kind：`COMPANY_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：2
- 层级规模：1 Atomic / 3 Mention
- Anchor entities：field:2c357507655a9c3c584ee6fd, field:a2f7532b0aa9aa9cbe6498e9
- Anchor artifact / period：— / —
- 摘要：The KOSPI index surged more than 5% at the open on June 25, 2026, rising above 8,900 from 8,400 in the prior session.

#### P09-A01. The KOSPI index surged more than 5% at the open on June 25, 2026, rising above 8,900 from 8,400 in the prior session.

- Atomic ID：`atomic:0c739830e2e7a5f3dd70fde4`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- Mention 数：3；Version：3
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY
- **人工审计：需拆分。** 该 Atomic 产生 2 个 FP pair；建议分区：kospi_open_move, kospi_close_move

##### M01. The KOSPI index surged more than 5% at the open on June 25, 2026, rising above 8,900 from 8,400 in the prior session.

- Mention ID：`mention:c1f3cc62556158bd3450d3d9dc9a6df5d4eb9fb4aa805913c775464f877a3ec4`
- 来源：[KOSPI Spikes 5% on Opening, Riding Micron’s Surprise Earnings](https://beincrypto.com/micron-earnings-kospi-sidecar-chip-rally/)；`doxatlas:raw_media:3f25dec4-7f5f-4203-9e0f-d37c2b06a2c7`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：KOSPI (SUBJECT)
- 数量：more than 5% [index_change_percent]；above 8,900 [index_level]；8,400 [index_level_prior]
- 时间：2026-06-25T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：South Korea’s KOSPI surged more than 5% at the open on June 25, pushing back above 8,900 from 8,400 the prior session.

##### M02. South Korea’s Kospi index closed 5.4% higher on Thursday.

- Mention ID：`mention:e9e1ca9c44cf060eb1c4dd2c5880ee68fe88d95c8bb7fe95787fcaf157d00cc4`
- 来源：[Investors bet on AI again after Micron reports 346% sales jump](https://edition.cnn.com/2026/06/25/business/micron-results-ai-stocks-volatility)；`doxatlas:raw_media:1aaec309-8ccb-42d6-b974-b4aa3bf2d43c`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：South Korea’s Kospi (SUBJECT)
- 数量：5.4% [index_change]
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：South Korea’s Kospi finished 5.4% higher.

##### M03. The Kospi Index rose 6%.

- Mention ID：`mention:6c34c485b9b392d7222de3f8fe85731d7c744cd2c63f6e209eeaecb4a4552c25`
- 来源：[SK Hynix Unveils $29 Billion US Listing as Shares Soar Over 800%](https://finance.yahoo.com/markets/stocks/articles/sk-hynix-unveils-29-billion-135023709.html)；`doxatlas:raw_media:b24ea6b3-557e-4053-9cae-c8e419455b39`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Kospi Index (SUBJECT)
- 数量：6% [index_change_percent]
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：helping push the Kospi Index up 6%.

### P10. Jim Lebenthal buys Micron stock.

- Package ID：`package:24b79621ae37676c80ffe34d`
- Family / Kind：`ANALYST_REPORT` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：COMPANY_MU, field:832acd4436905959bb970caf
- Anchor artifact / period：— / —
- 摘要：Jim Lebenthal buys Micron stock.

#### P10-A01. Jim Lebenthal buys Micron stock.

- Atomic ID：`atomic:bd060b5e9f463353ac3226c7`
- Family / Assertion：`ANALYST_ACTION` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：UNKNOWN

##### M01. Jim Lebenthal buys Micron stock.

- Mention ID：`mention:77f03afaac18382c694b4133b0b5429782e49c214e28bf5f32289a92963f6dcc`
- 来源：[Micron surges to new highs on blockbuster earnings: Jim Lebenthal buys the stock](https://finnhub.io/api/news?id=760aa3bb75cb55185d1ad9b8997324a47603aeac7746264f37945ce55241212a)；`doxatlas:raw_media:6e04e5e6-801c-4141-ac0f-81d29029b012`
- Family / Assertion：`ANALYST_ACTION` / `ACTUAL`
- 参与者：Jim Lebenthal (ACTOR)；Micron (SUBJECT)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `title:0`：Jim Lebenthal buys the stock
  - E02 `VERIFIED` / `text:0`：joins CNBC's "Halftime Report" to explain why he's buying it here.

### P11. Japan’s Nikkei 225 index closed 4.6% higher on Thursday.

- Package ID：`package:26cd0ab57ad831e917a2ca20`
- Family / Kind：`COMPANY_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：field:90982fbfd66dfdf75763f8f8
- Anchor artifact / period：— / —
- 摘要：Japan’s Nikkei 225 index closed 4.6% higher on Thursday.

#### P11-A01. Japan’s Nikkei 225 index closed 4.6% higher on Thursday.

- Atomic ID：`atomic:6c7460d3de94ef7913f965e0`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY

##### M01. Japan’s Nikkei 225 index closed 4.6% higher on Thursday.

- Mention ID：`mention:e7e1f9da6276a862cb81fb24101e3f44ab58341f48aba11f168a9ca7c5a09c9a`
- 来源：[Investors bet on AI again after Micron reports 346% sales jump](https://edition.cnn.com/2026/06/25/business/micron-results-ai-stocks-volatility)；`doxatlas:raw_media:1aaec309-8ccb-42d6-b974-b4aa3bf2d43c`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Japan’s Nikkei 225 (SUBJECT)
- 数量：4.6% [index_change]
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Japan’s Nikkei 225 index closed up 4.6%

### P12. The Magnificent Seven stocks declined by about 2% to a two-month low.

- Package ID：`package:2a1f2600f53066e347968ef9`
- Family / Kind：`COMPANY_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 3 Mention
- Anchor entities：COMPANY_AAPL, field:e32cc55f4db3183daf3b622b
- Anchor artifact / period：— / —
- 摘要：The Magnificent Seven stocks declined by about 2% to a two-month low.

#### P12-A01. The Magnificent Seven stocks declined by about 2% to a two-month low.

- Atomic ID：`atomic:d6f7a01524b27938c61b1bc4`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- Mention 数：3；Version：3
- 时间：UNKNOWN
- **人工审计：需拆分。** 该 Atomic 产生 3 个 FP pair；建议分区：magnificent_seven_price_move, apple_price_move, apple_market_cap_move

##### M01. The Magnificent Seven stocks declined by about 2% to a two-month low.

- Mention ID：`mention:bc87f5fabcbfbf1ced14a0cb1b871e3a2df846378a8d188cdcd1931edecb607b`
- 来源：[Why Micron's blowout earnings are a headache for Apple](https://finance.yahoo.com/markets/article/why-microns-blowout-earnings-are-a-headache-for-apple-161932441.html)；`doxatlas:raw_media:6ccb4979-525c-4467-a381-538f38a4fa9c`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：The Magnificent Seven (SUBJECT)
- 数量：about 2% [index_change]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：The Magnificent Seven are down about 2%, sliding to a two-month low after a month in which megacap AI winners had already lost trillions in market value.

##### M02. Apple's stock price declined by over 5%.

- Mention ID：`mention:c5833636965c7c77ddf68a915fce62af30e70a8d0653ccb8e54119d134ac46c9`
- 来源：[Why Micron's blowout earnings are a headache for Apple](https://finance.yahoo.com/markets/article/why-microns-blowout-earnings-are-a-headache-for-apple-161932441.html)；`doxatlas:raw_media:6ccb4979-525c-4467-a381-538f38a4fa9c`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Apple (SUBJECT)
- 数量：over 5% [stock_price_change]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Apple (AAPL), down over 5% after raising prices on some Macs and iPads, is showing the other side of that squeeze.

##### M03. Apple's market value decreased by nearly $200 billion.

- Mention ID：`mention:f683a5be9ae18e31af323c94e5d7096feae874c0981134f384036ede3b98d9bf`
- 来源：[Why Micron's blowout earnings are a headache for Apple](https://finance.yahoo.com/markets/article/why-microns-blowout-earnings-are-a-headache-for-apple-161932441.html)；`doxatlas:raw_media:6ccb4979-525c-4467-a381-538f38a4fa9c`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Apple (SUBJECT)
- 数量：nearly $200 billion [market_value_change]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Apple has erased nearly $200 billion.

### P13. Wedbush analyst Dan Ives stated in a research note that there are no cracks in AI demand and recommended owning core tech winners into year-end.

- Package ID：`package:3238525d634c06a78590b8fb`
- Family / Kind：`ANALYST_REPORT` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：field:7e23fa1f88f3e5fe9490dbbc
- Anchor artifact / period：— / —
- 摘要：Wedbush analyst Dan Ives stated in a research note that there are no cracks in AI demand and recommended owning core tech winners into year-end.

#### P13-A01. Wedbush analyst Dan Ives stated in a research note that there are no cracks in AI demand and recommended owning core tech winners into year-end.

- Atomic ID：`atomic:54d5ed92d1a2413c53694ca6`
- Family / Assertion：`ANALYST_ACTION` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：UNKNOWN

##### M01. Wedbush analyst Dan Ives stated in a research note that there are no cracks in AI demand and recommended owning core tech winners into year-end.

- Mention ID：`mention:ba54bc643fdbf6f7085b2ce87832f35b0d22f4b891afb54d1f77db37db43bf1a`
- 来源：[Why Everyone Is Talking About Micron](https://www.fool.com/investing/2026/06/25/why-everyone-is-talking-about-micron/)；`doxatlas:raw_media:49fb27c5-84b4-4e56-8791-f7ac23de38b7`
- Family / Assertion：`ANALYST_ACTION` / `ACTUAL`
- 参与者：Wedbush analyst Dan Ives (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`："We are seeing no cracks in AI demand on the chips/hardware or software front which gives us a bright green light to own the core tech winners into year-end," Wedbush analyst Dan Ives said in a recent research note.

### P14. SK Hynix stock fell more than 12% on Tuesday.

- Package ID：`package:381ba50e7ec123e3daeeb854`
- Family / Kind：`COMPANY_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：COMPANY_SKHY
- Anchor artifact / period：— / —
- 摘要：SK Hynix stock fell more than 12% on Tuesday.

#### P14-A01. SK Hynix stock fell more than 12% on Tuesday.

- Atomic ID：`atomic:12756db64f31ae2277edc079`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：2026-06-23T00:00:00 / 2026-06-23T00:00:00 / DAY

##### M01. SK Hynix stock fell more than 12% on Tuesday.

- Mention ID：`mention:1378ec6e5e635aa9279f8f950b2d2a07c74cecd702c78c16da68850de2216444`
- 来源：[Investors bet on AI again after Micron reports 346% sales jump](https://edition.cnn.com/2026/06/25/business/micron-results-ai-stocks-volatility)；`doxatlas:raw_media:1aaec309-8ccb-42d6-b974-b4aa3bf2d43c`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：SK Hynix (SUBJECT)
- 数量：more than 12% [stock_change]
- 时间：2026-06-23T00:00:00 / 2026-06-23T00:00:00 / DAY
- Evidence 状态：`TEXT_NOT_FOUND`
  - E01 `TEXT_NOT_FOUND` / `text:0`：SK Hynix ... tumbled more than 12%

### P15. Trading volume for SanDisk, Western Digital, and Seagate was active during the regular trading session.

- Package ID：`package:41ecc1960df328814f33f476`
- Family / Kind：`COMPANY_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：COMPANY_SNDK, COMPANY_STX, COMPANY_WDC
- Anchor artifact / period：— / —
- 摘要：Trading volume for SanDisk, Western Digital, and Seagate was active during the regular trading session.

#### P15-A01. Trading volume for SanDisk, Western Digital, and Seagate was active during the regular trading session.

- Atomic ID：`atomic:f31cd9fdec29fd1878d66717`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：2026-06-24T00:00:00 / 2026-06-24T00:00:00 / DAY

##### M01. Trading volume for SanDisk, Western Digital, and Seagate was active during the regular trading session.

- Mention ID：`mention:218f115adfa70a39bb819a2d47d16766f7556e55f816e9c33bc37b6f6d947e81`
- 来源：[SanDisk, Western Digital, Seagate Surge After Hours As Micron's Blowout Earnings Signal Memory Upcycle](https://www.benzinga.com/markets/equities/26/06/60088616/sandisk-western-digital-and-seagate-surge-after-microns-blowout-earnings-signal-memory-upcycle)；`doxatlas:raw_media:6389d63c-442c-4c9b-9ba5-b943b7e91804`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：SanDisk (SUBJECT)；Western Digital (SUBJECT)；Seagate (SUBJECT)
- 数量：—
- 时间：2026-06-24T00:00:00 / 2026-06-24T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Trading volume for all three names remained active during the regular session.

### P16. S&P 500 rose 0.75% in pre-market trade on Thursday.

- Package ID：`package:45e9c8e1b658426a70852c2e`
- Family / Kind：`COMPANY_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 2 Mention
- Anchor entities：INSTRUMENT_SP500
- Anchor artifact / period：— / —
- 摘要：S&P 500 rose 0.75% in pre-market trade on Thursday.

#### P16-A01. S&P 500 rose 0.75% in pre-market trade on Thursday.

- Atomic ID：`atomic:867ac3b963efe2418629b2f3`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- Mention 数：2；Version：2
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY
- **人工审计：需拆分。** 该 Atomic 产生 1 个 FP pair

##### M01. S&P 500 rose 0.75% in pre-market trade on Thursday.

- Mention ID：`mention:72c6e132fa80db0352d98ab497a76c3c6b1b7f546c5a0a519b100c2a8ff748f4`
- 来源：[Investors bet on AI again after Micron reports 346% sales jump](https://edition.cnn.com/2026/06/25/business/micron-results-ai-stocks-volatility)；`doxatlas:raw_media:1aaec309-8ccb-42d6-b974-b4aa3bf2d43c`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：S&P 500 (SUBJECT)
- 数量：0.75% [index_change_sp500]
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY
- Evidence 状态：`TEXT_NOT_FOUND`
  - E01 `TEXT_NOT_FOUND` / `text:0`：S&P 500 were up ... 0.75% ... in pre-market trade

##### M02. The S&P 500 slipped 0.01% to 7,357.

- Mention ID：`mention:fe3cee57ff2c0f1ae0a3e1cbcf571a9aeef3aa0001ed9bb8427e02be3d3bcb2e`
- 来源：[Stock Market Today, June 25: Apple Drops After Raising Device Prices to Offset Higher Memory Costs](https://www.fool.com/coverage/stock-market-today/2026/06/25/stock-market-today-june-25-apple-drops-after-raising-device-prices-to-offset-higher-memory-costs/)；`doxatlas:raw_media:d9404d58-cd88-4ccc-a9d2-ec4004629dbc`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：S&P 500 (SUBJECT)
- 数量：7,357 [index_value]；slipped 0.01% [index_change_percent]
- 时间：2026-06-25T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：The S&P 500 (^GSPC +0.00%) slipped 0.01% to 7,357

### P17. Micron Technology stock trades at 16 times forward earnings estimates.

- Package ID：`package:49660a11e4347560241c5601`
- Family / Kind：`COMPANY_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：COMPANY_MU
- Anchor artifact / period：— / —
- 摘要：Micron Technology stock trades at 16 times forward earnings estimates.

#### P17-A01. Micron Technology stock trades at 16 times forward earnings estimates.

- Atomic ID：`atomic:a3237267169b1da055cb3f25`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：UNKNOWN

##### M01. Micron Technology stock trades at 16 times forward earnings estimates.

- Mention ID：`mention:155a1fcc7579f58bfb8bfce5272007bdf19cd044b16fb2aa4e81d70baa9d9ca5`
- 来源：[Is Micron a Buy After Its Blowout Earnings Report?](https://www.fool.com/investing/2026/06/25/is-micron-a-buy-after-its-blowout-earnings-report/)；`doxatlas:raw_media:a424d484-3e57-47ed-b21d-50aeceafe593`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Micron Technology (SUBJECT)
- 数量：16x [forward_pe_ratio]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron trades at 16x forward earnings estimates,

### P18. Citi analysts maintained a Buy rating on Micron shares and raised the price target to $1,400.

- Package ID：`package:4b9bca8396d23cd8c7cf6a38`
- Family / Kind：`ANALYST_REPORT` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：COMPANY_MU, INSTITUTION_CITIGROUP
- Anchor artifact / period：— / —
- 摘要：Citi analysts maintained a Buy rating on Micron shares and raised the price target to $1,400.

#### P18-A01. Citi analysts maintained a Buy rating on Micron shares and raised the price target to $1,400.

- Atomic ID：`atomic:90b416672c9f2e085c9e0f63`
- Family / Assertion：`ANALYST_ACTION` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：UNKNOWN

##### M01. Citi analysts maintained a Buy rating on Micron shares and raised the price target to $1,400.

- Mention ID：`mention:babd7bd2317a09d2c203b1b5f36fd5ebdb5abaa0433be459ae8c263915937190`
- 来源：[MU Stock Soars as Micron’s Revenue More Than Quadrupled in Q3](https://www.barchart.com/story/news/3092/mu-stock-soars-as-microns-revenue-more-than-quadrupled-in-q3)；`doxatlas:raw_media:181db9d6-5959-4fb0-947e-3c6680861566`
- Family / Assertion：`ANALYST_ACTION` / `ACTUAL`
- 参与者：Citi analysts (ACTOR)；Micron (SUBJECT)
- 数量：$1,400 [price_target]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Citi analysts led by Atif Malik maintained their “Buy” rating on Micron shares and raised their price target to $1,400.

### P19. Roughly 54% of iPhones shipped since 2022 will not support the full new Siri experience.

- Package ID：`package:4beafa57bb02542df7e81574`
- Family / Kind：`PRODUCT_SCIENCE` / `EPISODE`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：field:22704647d5c545abeaeb01bb
- Anchor artifact / period：— / —
- 摘要：Roughly 54% of iPhones shipped since 2022 will not support the full new Siri experience.

#### P19-A01. Roughly 54% of iPhones shipped since 2022 will not support the full new Siri experience.

- Atomic ID：`atomic:25e57cf4113fd95b92ce7c55`
- Family / Assertion：`PRODUCT_SCIENCE` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：2022-01-01T00:00:00 / INTERVAL

##### M01. Roughly 54% of iPhones shipped since 2022 will not support the full new Siri experience.

- Mention ID：`mention:c2dddc5a61120218e6ed379ed958945a2bde6443136fe9a7574793dc3d96733b`
- 来源：[Tim Cook Calls the Memory Crisis a "Hundred-Year Flood"](https://finance.yahoo.com/technology/articles/tim-cook-calls-memory-crisis-170140774.html)；`doxatlas:raw_media:d42584c3-2d2d-4559-affc-ccae5b388ce1`
- Family / Assertion：`PRODUCT_SCIENCE` / `ACTUAL`
- 参与者：iPhones shipped since 2022 (SUBJECT)
- 数量：54% [share_of_units]
- 时间：2022-01-01T00:00:00 / INTERVAL
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Roughly 54% of iPhones shipped since 2022 won't support the full new Siri experience,

### P20. Memory and storage prices quadrupled in the past three quarters.

- Package ID：`package:505a6a1dde5bd7b7288e8628`
- Family / Kind：`COMPANY_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：field:ec54ab86f15ab3a777336788
- Anchor artifact / period：— / field:24e8f82b70fc9dc55f8e03f7
- 摘要：Memory and storage prices quadrupled in the past three quarters.

#### P20-A01. Memory and storage prices quadrupled in the past three quarters.

- Atomic ID：`atomic:0972f436a642c2cb9c3095e3`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：past three quarters / UNKNOWN

##### M01. Memory and storage prices quadrupled in the past three quarters.

- Mention ID：`mention:2839d4915ee2cb6a5d12f72b2294ab32fbc62bde298a59e47dce086891677b5b`
- 来源：[Tim Cook Calls the Memory Crisis a "Hundred-Year Flood"](https://finance.yahoo.com/technology/articles/tim-cook-calls-memory-crisis-170140774.html)；`doxatlas:raw_media:d42584c3-2d2d-4559-affc-ccae5b388ce1`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Memory and storage (SUBJECT)
- 数量：quadrupled [price_change_factor]
- 时间：past three quarters / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Memory and storage prices have quadrupled in the past three quarters, according to Counterpoint Research,

### P21. Microsoft stock closed at $352.83, down 3.46%.

- Package ID：`package:5435464829f337a86cc389e6`
- Family / Kind：`COMPANY_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：COMPANY_MSFT
- Anchor artifact / period：— / —
- 摘要：Microsoft stock closed at $352.83, down 3.46%.

#### P21-A01. Microsoft stock closed at $352.83, down 3.46%.

- Atomic ID：`atomic:dedf8a8ae42cac136cf5559a`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：2026-06-25T00:00:00 / DAY

##### M01. Microsoft stock closed at $352.83, down 3.46%.

- Mention ID：`mention:48b906f93269a0da5d134370ef0ed63891bdcfd0475878754dbf9643787dceda`
- 来源：[Stock Market Today, June 25: Apple Drops After Raising Device Prices to Offset Higher Memory Costs](https://www.fool.com/coverage/stock-market-today/2026/06/25/stock-market-today-june-25-apple-drops-after-raising-device-prices-to-offset-higher-memory-costs/)；`doxatlas:raw_media:d9404d58-cd88-4ccc-a9d2-ec4004629dbc`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Microsoft (SUBJECT)
- 数量：$352.83 [closing_price]；down 3.46% [price_change_percent]
- 时间：2026-06-25T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Microsoft (MSFT +1.69%) closed at $352.83, down 3.46%

### P22. Sandisk is scheduled to report earnings on August 24, 2026.

- Package ID：`package:5708fe2147e4e99bdd3cc163`
- Family / Kind：`EARNINGS_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：COMPANY_SNDK
- Anchor artifact / period：— / —
- 摘要：Sandisk is scheduled to report earnings on August 24, 2026.

#### P22-A01. Sandisk is scheduled to report earnings on August 24, 2026.

- Atomic ID：`atomic:9dbe9852102eb1fd41a38ebf`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `PLANNED`
- Mention 数：1；Version：1
- 时间：2026-08-24T00:00:00 / DAY

##### M01. Sandisk is scheduled to report earnings on August 24, 2026.

- Mention ID：`mention:7c4a48dc68aafac8a210b6769e1313593c4e766c94f324d3899d837274f806e0`
- 来源：[Why Sandisk Stock Soared Today](https://www.fool.com/investing/2026/06/25/why-sandisk-stock-soared-today/)；`doxatlas:raw_media:35a4333e-6608-482b-b963-ba2f64392063`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `PLANNED`
- 参与者：Sandisk (ACTOR)
- 数量：—
- 时间：2026-08-24T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Sandisk itself doesn't report earnings again for another couple of months, on Aug. 24.

### P23. IDC sees Apple's average selling price rising 12% this year.

- Package ID：`package:5adc5d2d81e7a0598addd4d9`
- Family / Kind：`EARNINGS_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：3
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：COMPANY_AAPL, COMPANY_WDC
- Anchor artifact / period：— / —
- 摘要：IDC sees Apple's average selling price rising 12% this year.

#### P23-A01. IDC sees Apple's average selling price rising 12% this year.

- Atomic ID：`atomic:016450982529acf16a843ccc`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- Mention 数：1；Version：1
- 时间：2026-01-01T00:00:00 / 2026-12-31T00:00:00 / YEAR

##### M01. IDC sees Apple's average selling price rising 12% this year.

- Mention ID：`mention:c03a1c54e90efaabce8b1912a29d1c6355312396eda078a7164a0272812d7180`
- 来源：[Tim Cook Calls the Memory Crisis a "Hundred-Year Flood"](https://finance.yahoo.com/technology/articles/tim-cook-calls-memory-crisis-170140774.html)；`doxatlas:raw_media:d42584c3-2d2d-4559-affc-ccae5b388ce1`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- 参与者：IDC (ACTOR)；Apple (SUBJECT)
- 数量：12% [average_selling_price_change]
- 时间：2026-01-01T00:00:00 / 2026-12-31T00:00:00 / YEAR
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：and sees Apple's average selling price rising 12% this year.

### P24. The Nasdaq Composite fell 0.46% to 25,359.

- Package ID：`package:5b976a86486c030c21077033`
- Family / Kind：`COMPANY_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：INSTRUMENT_NASDAQ_COMPOSITE
- Anchor artifact / period：— / —
- 摘要：The Nasdaq Composite fell 0.46% to 25,359.

#### P24-A01. The Nasdaq Composite fell 0.46% to 25,359.

- Atomic ID：`atomic:6773792f0f7af307eba78d74`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：2026-06-25T00:00:00 / DAY

##### M01. The Nasdaq Composite fell 0.46% to 25,359.

- Mention ID：`mention:c400d15d88dc4c711d248f834264d20d95658b0c85b23f5cdd89022e33702d8f`
- 来源：[Stock Market Today, June 25: Apple Drops After Raising Device Prices to Offset Higher Memory Costs](https://www.fool.com/coverage/stock-market-today/2026/06/25/stock-market-today-june-25-apple-drops-after-raising-device-prices-to-offset-higher-memory-costs/)；`doxatlas:raw_media:d9404d58-cd88-4ccc-a9d2-ec4004629dbc`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Nasdaq Composite (SUBJECT)
- 数量：25,359 [index_value]；fell 0.46% [index_change_percent]
- 时间：2026-06-25T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：the Nasdaq Composite (^IXIC 0.80%) fell 0.46% to 25,359

### P25. Micron Technology's stock fell 13% on Tuesday.

- Package ID：`package:5d9a1bbe9ea66c1e8f5a44e8`
- Family / Kind：`COMPANY_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：COMPANY_MU
- Anchor artifact / period：— / —
- 摘要：Micron Technology's stock fell 13% on Tuesday.

#### P25-A01. Micron Technology's stock fell 13% on Tuesday.

- Atomic ID：`atomic:aa540ad4f52978002d0d7f23`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：2026-06-23T00:00:00 / 2026-06-23T00:00:00 / DAY

##### M01. Micron Technology's stock fell 13% on Tuesday.

- Mention ID：`mention:7663bc437850e929210513adc609df34e0c6bb8920b102bb4b659f93b63c3e5f`
- 来源：[Investors bet on AI again after Micron reports 346% sales jump](https://edition.cnn.com/2026/06/25/business/micron-results-ai-stocks-volatility)；`doxatlas:raw_media:1aaec309-8ccb-42d6-b974-b4aa3bf2d43c`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Micron Technology (SUBJECT)
- 数量：fell 13% [stock_change]
- 时间：2026-06-23T00:00:00 / 2026-06-23T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron’s stock fell 13% on Tuesday

### P26. On June 25, 2026, individual investors net bought roughly 490 billion won, institutions net bought around 100 billion won, and foreign investors net sold approximately 600 billion won in the South Korean market.

- Package ID：`package:6058d5a58812bcd32da2d814`
- Family / Kind：`TRANSACTION` / `EPISODE`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：INSTITUTION_OSAIC_INSTITUTIONS, field:8a71e29593363f2ca7dd48eb, field:9d199ac227cc711041a737a1
- Anchor artifact / period：— / —
- 摘要：On June 25, 2026, individual investors net bought roughly 490 billion won, institutions net bought around 100 billion won, and foreign investors net sold approximately 600 billion won in the South Korean market.

#### P26-A01. On June 25, 2026, individual investors net bought roughly 490 billion won, institutions net bought around 100 billion won, and foreign investors net sold approximately 600 billion won in the South Korean market.

- Atomic ID：`atomic:d638e7b1244c3be5ed1827ec`
- Family / Assertion：`TRANSACTION_CAPITAL` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：2026-06-25T00:00:00 / DAY

##### M01. On June 25, 2026, individual investors net bought roughly 490 billion won, institutions net bought around 100 billion won, and foreign investors net sold approximately 600 billion won in the South Korean market.

- Mention ID：`mention:76a4613f3eee08c8346a4d13a2bd9f362d6178801c505e660fb543c1b4415e33`
- 来源：[KOSPI Spikes 5% on Opening, Riding Micron’s Surprise Earnings](https://beincrypto.com/micron-earnings-kospi-sidecar-chip-rally/)；`doxatlas:raw_media:3f25dec4-7f5f-4203-9e0f-d37c2b06a2c7`
- Family / Assertion：`TRANSACTION_CAPITAL` / `ACTUAL`
- 参与者：individual investors (ACTOR)；institutions (ACTOR)；Foreign investors (ACTOR)
- 数量：roughly 490 billion won [net_buy_amount]；around 100 billion won [net_buy_amount]；approximately 600 billion won [net_sell_amount]
- 时间：2026-06-25T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：By investor type, individuals net bought roughly 490 billion won and institutions added around 100 billion won.
  - E02 `VERIFIED` / `text:0`：Foreign investors net sold approximately 600 billion won, extending a streak of net selling that has now totaled around 12.2 trillion won over the past five trading days.

### P27. Defiance launches a 2X DRAM ETF.

- Package ID：`package:60b2bd5ab28b73aad19ba4d8`
- Family / Kind：`PRODUCT_SCIENCE` / `EPISODE`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 2 Mention
- Anchor entities：INSTITUTION_ROUNDHILL_INVESTMENTS, field:8e154bfa9809bd04210178df, field:b7d3928c0328ef24d185c7ec, field:f0909aa117fa753733c21322
- Anchor artifact / period：— / —
- 摘要：Defiance launches a 2X DRAM ETF.

#### P27-A01. Defiance launches a 2X DRAM ETF.

- Atomic ID：`atomic:4f47bc2ad78422a30085179b`
- Family / Assertion：`PRODUCT_SCIENCE` / `ACTUAL`
- Mention 数：2；Version：2
- 时间：UNKNOWN
- **人工审计：需拆分。** 该 Atomic 产生 1 个 FP pair

##### M01. Defiance launches a 2X DRAM ETF.

- Mention ID：`mention:479178721392ef3a072d5fe24100aa072665217bc2ebd5f69933e6d24efda55c`
- 来源：[The AI Memory Trade Is Heating Up: Defiance Launches 2X DRAM ETF After Micron's Blowout Quarter](https://www.benzinga.com/etfs/new-etfs/26/06/60103609/the-ai-memory-trade-is-heating-up-defiance-launches-2x-dram-etf-after-microns-blowout-quarter)；`doxatlas:raw_media:238a879b-e686-457d-b83d-5b0405bd28c6`
- Family / Assertion：`PRODUCT_SCIENCE` / `ACTUAL`
- 参与者：Defiance (ACTOR)；2X DRAM ETF (SUBJECT)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `title:0`：Defiance Launches 2X DRAM ETF After Micron's Blowout Quarter

##### M02. Roundhill Investments launches a leveraged fund designed to deliver twice the daily performance of the Roundhill Memory ETF.

- Mention ID：`mention:6ae4dfe23b46a98c9c348c0a0bcf18899121aa0dc4dd34a1112231326931b4e5`
- 来源：[The AI Memory Trade Is Heating Up: Defiance Launches 2X DRAM ETF After Micron's Blowout Quarter](https://www.benzinga.com/etfs/new-etfs/26/06/60103609/the-ai-memory-trade-is-heating-up-defiance-launches-2x-dram-etf-after-microns-blowout-quarter)；`doxatlas:raw_media:238a879b-e686-457d-b83d-5b0405bd28c6`
- Family / Assertion：`PRODUCT_SCIENCE` / `ACTUAL`
- 参与者：Roundhill Investments (ACTOR)；leveraged fund (SUBJECT)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Roundhill Investments’ launch of a leveraged fund designed to deliver twice the daily performance of the Roundhill Memory ETF (NASDAQ:DRAM).

### P28. Qualcomm reported Q2 handset revenue of $6.024 billion, a 13% year-over-year decline.

- Package ID：`package:6876b1bd5782d0655e3e372e`
- Family / Kind：`EARNINGS_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：3
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：COMPANY_QCOM
- Anchor artifact / period：— / field:c7dc1a30dc2693c691aea30b
- 摘要：Qualcomm reported Q2 handset revenue of $6.024 billion, a 13% year-over-year decline.

#### P28-A01. Qualcomm reported Q2 handset revenue of $6.024 billion, a 13% year-over-year decline.

- Atomic ID：`atomic:e286cb6982e7f9d189880e69`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：Q2 / UNKNOWN

##### M01. Qualcomm reported Q2 handset revenue of $6.024 billion, a 13% year-over-year decline.

- Mention ID：`mention:6cf6f9b8f1d5c81665a630102ec93196dd2b379e96f721cec594a47d7faf0adb`
- 来源：[Micron Just Locked In $100 Billion in Sales, and Wall Street Thinks the Boom-Bust Chip Cycle Is Dead](https://247wallst.com/investing/2026/06/25/micron-just-locked-in-100-billion-in-sales-and-wall-street-thinks-the-boom-bust-chip-cycle-is-dead/)；`doxatlas:raw_media:1e515897-64d4-4896-b936-10a04501eff4`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Qualcomm (SUBJECT)
- 数量：$6.024 billion [handset_revenue]
- 时间：Q2 / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Qualcomm’s Q2 handset revenue had already fallen 13% year-over-year to $6.024 billion, with memory supply constraints among Chinese OEMs cited as the cause.

### P29. Analysts expect Sandisk's earnings to reach $33.72 per share.

- Package ID：`package:6f8e6c16060ceb541ed2f8cc`
- Family / Kind：`ANALYST_REPORT` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：COMPANY_SNDK, field:321e4e1566b5bfbc9978decb
- Anchor artifact / period：— / —
- 摘要：Analysts expect Sandisk's earnings to reach $33.72 per share.

#### P29-A01. Analysts expect Sandisk's earnings to reach $33.72 per share.

- Atomic ID：`atomic:e9943ecb3ac9b7641002e768`
- Family / Assertion：`ANALYST_ACTION` / `EXPECTED`
- Mention 数：1；Version：1
- 时间：UNKNOWN

##### M01. Analysts expect Sandisk's earnings to reach $33.72 per share.

- Mention ID：`mention:0c1ce3a74a89c892dd3b4295fde2040c21d20be550757421c6317da86fcb1b8d`
- 来源：[Why Sandisk Stock Soared Today](https://www.fool.com/investing/2026/06/25/why-sandisk-stock-soared-today/)；`doxatlas:raw_media:35a4333e-6608-482b-b963-ba2f64392063`
- Family / Assertion：`ANALYST_ACTION` / `EXPECTED`
- 参与者：analysts (ACTOR)；Sandisk (SUBJECT)
- 数量：$33.72 per share [earnings_per_share]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：analysts are expecting big things, with earnings forecast to more than double sequentially to $33.72 per share.

### P30. Mizuho maintains an Outperform rating and raises the price target for Micron Technology to $1375.

- Package ID：`package:6ff29e057e36c8b9dd131d55`
- Family / Kind：`ANALYST_REPORT` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：COMPANY_MFG, COMPANY_MU
- Anchor artifact / period：— / —
- 摘要：Mizuho maintains an Outperform rating and raises the price target for Micron Technology to $1375.

#### P30-A01. Mizuho maintains an Outperform rating and raises the price target for Micron Technology to $1375.

- Atomic ID：`atomic:98d8ee305c113cd16fa58284`
- Family / Assertion：`ANALYST_ACTION` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：UNKNOWN

##### M01. Mizuho maintains an Outperform rating and raises the price target for Micron Technology to $1375.

- Mention ID：`mention:f02358594507733146f56f32d851d54a43e374ab4ce94a8a2b0e891ccc02e242`
- 来源：[Mizuho Maintains Outperform on Micron Technology, Raises Price Target to $1375](https://www.benzinga.com/news/26/06/60106539/mizuho-maintains-outperform-micron-technology-raises-price-target-1375)；`doxatlas:raw_media:1516d084-62ab-4c8a-a7f3-e635c464425a`
- Family / Assertion：`ANALYST_ACTION` / `ACTUAL`
- 参与者：Mizuho (ACTOR)；Micron Technology (SUBJECT)
- 数量：$1375 [price_target]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `title:0`：Mizuho Maintains Outperform on Micron Technology, Raises Price Target to $1375

### P31. Qualcomm named Meta as the first customer for its new data center CPU.

- Package ID：`package:7436f3cb1afc86973ef6cbca`
- Family / Kind：`COMPANY_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 2 Mention
- Anchor entities：COMPANY_META, COMPANY_QCOM, field:d45b8acb3fc2282a15a4a4f5
- Anchor artifact / period：— / —
- 摘要：Qualcomm named Meta as the first customer for its new data center CPU.

#### P31-A01. Qualcomm named Meta as the first customer for its new data center CPU.

- Atomic ID：`atomic:aa857b656c46c801278c9026`
- Family / Assertion：`COMMERCIAL_OPERATION` / `ACTUAL`
- Mention 数：2；Version：2
- 时间：2028-01-01T00:00:00 / 2028-12-31T00:00:00 / YEAR
- **人工审计：需拆分。** 该 Atomic 产生 1 个 FP pair

##### M01. Qualcomm named Meta as the first customer for its new data center CPU.

- Mention ID：`mention:66f20be84fd4a37c388cfd180e8520939eed85d3c6652ee16b6ae35f5a76c8f5`
- 来源：[Micron Just Locked In $100 Billion in Sales, and Wall Street Thinks the Boom-Bust Chip Cycle Is Dead](https://247wallst.com/investing/2026/06/25/micron-just-locked-in-100-billion-in-sales-and-wall-street-thinks-the-boom-bust-chip-cycle-is-dead/)；`doxatlas:raw_media:1e515897-64d4-4896-b936-10a04501eff4`
- Family / Assertion：`COMMERCIAL_OPERATION` / `ACTUAL`
- 参与者：Qualcomm (ACTOR)；META (COUNTERPARTY)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Qualcomm “named META as its first customer for its new data center CPU

##### M02. Qualcomm's Dragonfly C1000 is expected to ship to Meta in 2028.

- Mention ID：`mention:7bdd2db041232ed4d373d1242da5b789dfc4137c786ed01b19d307996c1037ef`
- 来源：[Micron Just Locked In $100 Billion in Sales, and Wall Street Thinks the Boom-Bust Chip Cycle Is Dead](https://247wallst.com/investing/2026/06/25/micron-just-locked-in-100-billion-in-sales-and-wall-street-thinks-the-boom-bust-chip-cycle-is-dead/)；`doxatlas:raw_media:1e515897-64d4-4896-b936-10a04501eff4`
- Family / Assertion：`PRODUCT_SCIENCE` / `EXPECTED`
- 参与者：Qualcomm (ACTOR)；Meta (TARGET)；Dragonfly C1000 (SUBJECT)
- 数量：—
- 时间：2028-01-01T00:00:00 / 2028-12-31T00:00:00 / YEAR
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：until the Dragonfly C1000 actually ships to Meta in 2028.

### P32. Technology sector shares increased by 1.6%.

- Package ID：`package:7477ca5e3fb06e028609e985`
- Family / Kind：`COMPANY_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：INSTRUMENT_TECH
- Anchor artifact / period：— / —
- 摘要：Technology sector shares increased by 1.6%.

#### P32-A01. Technology sector shares increased by 1.6%.

- Atomic ID：`atomic:8adcefd7fe4c260fd498bbeb`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：2026-06-24T00:00:00 / 2026-06-24T00:00:00 / DAY

##### M01. Technology sector shares increased by 1.6%.

- Mention ID：`mention:050b8d304b8c2f045d77f44e83b8657edcc30300368cfb6a45c3138e7bd67e95`
- 来源：[Dow Surges 250 Points; Micron Posts Upbeat Q3 Earnings](https://finnhub.io/api/news?id=7316a88ccda79b3354a49965fa82f15ed81fe3722e51491463cf118f097a0143)；`doxatlas:raw_media:fce73732-1c68-4d0f-9d7f-c5800393ffff`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Tech shares (SUBJECT)
- 数量：1.6% [sector_change_percent]
- 时间：2026-06-24T00:00:00 / 2026-06-24T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Tech shares jump 1.6%

### P33. SK Hynix stock jumped more than 10% in early morning trading on June 25, 2026, triggering a static volatility interruption (VI) at the open.

- Package ID：`package:7b63ae2bed3efaf2c18511a0`
- Family / Kind：`COMPANY_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：2
- 层级规模：1 Atomic / 4 Mention
- Anchor entities：COMPANY_SKHY, field:c71ef1da935b0979d469d4e8
- Anchor artifact / period：— / —
- 摘要：SK Hynix stock jumped more than 10% in early morning trading on June 25, 2026, triggering a static volatility interruption (VI) at the open.

#### P33-A01. SK Hynix stock jumped more than 10% in early morning trading on June 25, 2026, triggering a static volatility interruption (VI) at the open.

- Atomic ID：`atomic:f51200ad8eaac7d7424ccfde`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- Mention 数：4；Version：4
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY
- **人工审计：需拆分。** 该 Atomic 产生 0 个 FP pair

##### M01. SK Hynix stock jumped more than 10% in early morning trading on June 25, 2026, triggering a static volatility interruption (VI) at the open.

- Mention ID：`mention:492ec86f1e3a5b0b56c9ff9c6501bcc9c5c81a82b8d44167b6ac5aae4ce182ab`
- 来源：[KOSPI Spikes 5% on Opening, Riding Micron’s Surprise Earnings](https://beincrypto.com/micron-earnings-kospi-sidecar-chip-rally/)；`doxatlas:raw_media:3f25dec4-7f5f-4203-9e0f-d37c2b06a2c7`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：SK Hynix (SUBJECT)
- 数量：more than 10% [stock_change_percent]
- 时间：2026-06-25T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：SK Hynix jumped more than 10% in early morning trading and triggered a static volatility interruption (VI) at the open, briefly switching to single-price trading for two minutes.

##### M02. Samsung Electronics stock reclaimed the 360,000 won level and SK Hynix stock reclaimed the 2.8 million won level on June 25, 2026.

- Mention ID：`mention:d8df9ceff7133cd979e5bccc0c252b262edb1e8f67e1411ef59c7393c014c0ac`
- 来源：[KOSPI Spikes 5% on Opening, Riding Micron’s Surprise Earnings](https://beincrypto.com/micron-earnings-kospi-sidecar-chip-rally/)；`doxatlas:raw_media:3f25dec4-7f5f-4203-9e0f-d37c2b06a2c7`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Samsung Electronics (SUBJECT)；SK Hynix (SUBJECT)
- 数量：360,000 won [stock_price]；2.8 million won [stock_price]
- 时间：2026-06-25T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Samsung Electronics and SK Hynix reclaimed the 360,000 won and 2.8 million won levels, respectively.

##### M03. SK Hynix's stock rose 13% on Thursday.

- Mention ID：`mention:b6a27ee27d6e6bfb41cb1e1b7a4ed70c9e6f688176bfc058a85cf5f0deecb703`
- 来源：[Investors bet on AI again after Micron reports 346% sales jump](https://edition.cnn.com/2026/06/25/business/micron-results-ai-stocks-volatility)；`doxatlas:raw_media:1aaec309-8ccb-42d6-b974-b4aa3bf2d43c`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：SK Hynix (SUBJECT)
- 数量：13% [stock_change]
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：SK Hynix’s stock shot up 13% on Thursday after the company disclosed plans for a listing on the US Nasdaq.

##### M04. SK Hynix shares climbed nearly 12% in early trading before trimming gains to around 10% on Thursday.

- Mention ID：`mention:be8833e26f52bd64ea97473306d8d97e47f051153b9aab8bfee7df5501d71899`
- 来源：[SK Hynix Unveils $29 Billion US Listing as Shares Soar Over 800%](https://finance.yahoo.com/markets/stocks/articles/sk-hynix-unveils-29-billion-135023709.html)；`doxatlas:raw_media:b24ea6b3-557e-4053-9cae-c8e419455b39`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：SK Hynix (SUBJECT)
- 数量：nearly 12% [price_change_percent]；around 10% [price_change_percent]
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：The stock climbed nearly 12% in early Thursday trading before trimming gains to around 10%

### P34. SK Hynix disclosed plans for a listing on the US Nasdaq.

- Package ID：`package:87feed0946169a318e38e710`
- Family / Kind：`TRANSACTION` / `EPISODE`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：2
- 层级规模：1 Atomic / 3 Mention
- Anchor entities：COMPANY_SKHY
- Anchor artifact / period：— / —
- 摘要：SK Hynix disclosed plans for a listing on the US Nasdaq.

#### P34-A01. SK Hynix disclosed plans for a listing on the US Nasdaq.

- Atomic ID：`atomic:a21c0d5b806ff1b534ce36e5`
- Family / Assertion：`TRANSACTION_CAPITAL` / `ACTUAL`
- Mention 数：3；Version：3
- 时间：2026-06-25T00:00:00 / 2026-07-10T00:00:00 / DAY
- **人工审计：需拆分。** 该 Atomic 产生 0 个 FP pair

##### M01. SK Hynix disclosed plans for a listing on the US Nasdaq.

- Mention ID：`mention:2f5dbd902841afb6393529f45cf509ab50a3561084c56ad20787912fac775c02`
- 来源：[Investors bet on AI again after Micron reports 346% sales jump](https://edition.cnn.com/2026/06/25/business/micron-results-ai-stocks-volatility)；`doxatlas:raw_media:1aaec309-8ccb-42d6-b974-b4aa3bf2d43c`
- Family / Assertion：`TRANSACTION_CAPITAL` / `ACTUAL`
- 参与者：SK Hynix (ACTOR)
- 数量：—
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：the company disclosed plans for a listing on the US Nasdaq.

##### M02. SK Hynix seeks to raise 45.45 trillion won through a US listing.

- Mention ID：`mention:2ed9b896ebe372bb9803f3f9fae536ba9b2df356a499cbc93379118ee6b7d81f`
- 来源：[SK Hynix Unveils $29 Billion US Listing as Shares Soar Over 800%](https://finance.yahoo.com/markets/stocks/articles/sk-hynix-unveils-29-billion-135023709.html)；`doxatlas:raw_media:b24ea6b3-557e-4053-9cae-c8e419455b39`
- Family / Assertion：`TRANSACTION_CAPITAL` / `PLANNED`
- 参与者：SK Hynix (ACTOR)
- 数量：45.45 trillion won [capital_raise_amount]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：SK Hynix is seeking 45.45 trillion won through the US listing

##### M03. SK Hynix expects its American depositary receipts to start trading on July 10.

- Mention ID：`mention:e6bd44a19b15a0e63b80b209d1cd68eee2f0031c1c606e926910de576abf0623`
- 来源：[SK Hynix Unveils $29 Billion US Listing as Shares Soar Over 800%](https://finance.yahoo.com/markets/stocks/articles/sk-hynix-unveils-29-billion-135023709.html)；`doxatlas:raw_media:b24ea6b3-557e-4053-9cae-c8e419455b39`
- Family / Assertion：`TRANSACTION_CAPITAL` / `EXPECTED`
- 参与者：SK Hynix (ACTOR)
- 数量：—
- 时间：2026-07-10T00:00:00 / 2026-07-10T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：The company expects its American depositary receipts to start trading on July 10

### P35. Samsung stock fell more than 12% on Tuesday.

- Package ID：`package:8b2aa282fc7bd0a2220f7220`
- Family / Kind：`COMPANY_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：field:c71ef1da935b0979d469d4e8
- Anchor artifact / period：— / —
- 摘要：Samsung stock fell more than 12% on Tuesday.

#### P35-A01. Samsung stock fell more than 12% on Tuesday.

- Atomic ID：`atomic:a5ea3a84642d4577b3348a5c`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：2026-06-23T00:00:00 / 2026-06-23T00:00:00 / DAY

##### M01. Samsung stock fell more than 12% on Tuesday.

- Mention ID：`mention:2e20e3e5de3cc3c390a67b09f0d2a402bdaf4ce6774de1719d01e91a76a166f7`
- 来源：[Investors bet on AI again after Micron reports 346% sales jump](https://edition.cnn.com/2026/06/25/business/micron-results-ai-stocks-volatility)；`doxatlas:raw_media:1aaec309-8ccb-42d6-b974-b4aa3bf2d43c`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Samsung (SUBJECT)
- 数量：more than 12% [stock_change]
- 时间：2026-06-23T00:00:00 / 2026-06-23T00:00:00 / DAY
- Evidence 状态：`TEXT_NOT_FOUND`
  - E01 `TEXT_NOT_FOUND` / `text:0`：Samsung ... tumbled more than 12%

### P36. Sandisk is locking in long-term prices at high margins through Strategic Customer Agreements.

- Package ID：`package:8da9a30bee844751c3d23022`
- Family / Kind：`COMPANY_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：COMPANY_SNDK
- Anchor artifact / period：— / —
- 摘要：Sandisk is locking in long-term prices at high margins through Strategic Customer Agreements.

#### P36-A01. Sandisk is locking in long-term prices at high margins through Strategic Customer Agreements.

- Atomic ID：`atomic:cb534b9cbda1c72d2e371456`
- Family / Assertion：`COMMERCIAL_OPERATION` / `ONGOING`
- Mention 数：1；Version：1
- 时间：UNKNOWN

##### M01. Sandisk is locking in long-term prices at high margins through Strategic Customer Agreements.

- Mention ID：`mention:0cde9995e304598266e9726267562d5e6f02d20d6e684cc83cc4e7469ea1c8ab`
- 来源：[Why Sandisk Stock Soared Today](https://www.fool.com/investing/2026/06/25/why-sandisk-stock-soared-today/)；`doxatlas:raw_media:35a4333e-6608-482b-b963-ba2f64392063`
- Family / Assertion：`COMMERCIAL_OPERATION` / `ONGOING`
- 参与者：Sandisk (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron is locking in long-term prices at these high margins through Strategic Customer Agreements -- and so is Sandisk.

### P37. Apple trading volume reached 106.4 million shares.

- Package ID：`package:92b321481c21d56e2de07a92`
- Family / Kind：`COMPANY_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：COMPANY_AAPL
- Anchor artifact / period：— / —
- 摘要：Apple trading volume reached 106.4 million shares.

#### P37-A01. Apple trading volume reached 106.4 million shares.

- Atomic ID：`atomic:9700251cbcca9767f2f2897a`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：2026-06-25T00:00:00 / DAY

##### M01. Apple trading volume reached 106.4 million shares.

- Mention ID：`mention:0014d28adeaf1d94f6ad52c18641cf37857df347f73720faea4a11c985e56c6c`
- 来源：[Stock Market Today, June 25: Apple Drops After Raising Device Prices to Offset Higher Memory Costs](https://www.fool.com/coverage/stock-market-today/2026/06/25/stock-market-today-june-25-apple-drops-after-raising-device-prices-to-offset-higher-memory-costs/)；`doxatlas:raw_media:d9404d58-cd88-4ccc-a9d2-ec4004629dbc`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Apple (SUBJECT)
- 数量：106.4 million shares [trading_volume]
- 时间：2026-06-25T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Trading volume reached 106.4 million shares, coming in about 119% above its three-month average of 48.5 million shares.

### P38. South Korea’s Kospi index fell 10% on Tuesday, triggering a circuit breaker.

- Package ID：`package:9591ed1b81438f01d9758b1d`
- Family / Kind：`COMPANY_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：field:2c357507655a9c3c584ee6fd
- Anchor artifact / period：— / —
- 摘要：South Korea’s Kospi index fell 10% on Tuesday, triggering a circuit breaker.

#### P38-A01. South Korea’s Kospi index fell 10% on Tuesday, triggering a circuit breaker.

- Atomic ID：`atomic:dd876f70c179e4363ce8ab83`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：2026-06-23T00:00:00 / 2026-06-23T00:00:00 / DAY

##### M01. South Korea’s Kospi index fell 10% on Tuesday, triggering a circuit breaker.

- Mention ID：`mention:dfb8dadb2d686a62e418fc0d3c1f20e24464bbe804eabaf8e2d825bd9f568019`
- 来源：[Investors bet on AI again after Micron reports 346% sales jump](https://edition.cnn.com/2026/06/25/business/micron-results-ai-stocks-volatility)；`doxatlas:raw_media:1aaec309-8ccb-42d6-b974-b4aa3bf2d43c`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：South Korea’s Kospi (SUBJECT)
- 数量：10% [index_change]
- 时间：2026-06-23T00:00:00 / 2026-06-23T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：The latter tumbled 10% Tuesday, tripping a circuit breaker that prompted a 20-minute cooling off period.

### P39. Needham increased its price target on Micron to $1,550 from $500 while maintaining a Buy rating.

- Package ID：`package:98341894507e31a32759b534`
- Family / Kind：`ANALYST_REPORT` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 2 Mention
- Anchor entities：COMPANY_MU, INSTITUTION_NEEDHAM
- Anchor artifact / period：— / —
- 摘要：Needham increased its price target on Micron to $1,550 from $500 while maintaining a Buy rating.

#### P39-A01. Needham increased its price target on Micron to $1,550 from $500 while maintaining a Buy rating.

- Atomic ID：`atomic:94faf7382f26afa7ad177924`
- Family / Assertion：`ANALYST_ACTION` / `ACTUAL`
- Mention 数：2；Version：2
- 时间：2026-06-22T00:00:00 / DAY
- **人工审计：需拆分。** 该 Atomic 产生 1 个 FP pair

##### M01. Needham increased its price target on Micron to $1,550 from $500 while maintaining a Buy rating.

- Mention ID：`mention:33245950431e47967382cf396c28882cb94b23c5629f5738eb7deb583fbac99a`
- 来源：[Micron Technology (MU) Announces Collaboration with Anthropic to Scale Next Generation AI Infrastructure](https://finance.yahoo.com/technology/ai/articles/micron-technology-mu-announces-collaboration-062406354.html)；`doxatlas:raw_media:d07de614-f3e3-431a-89dd-33bd139acd24`
- Family / Assertion：`ANALYST_ACTION` / `ACTUAL`
- 参与者：Needham (ACTOR)；Micron (SUBJECT)
- 数量：$1,550 [price_target]；$500 [previous_price_target]
- 时间：2026-06-22T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：In other news, TheFly reported on the same day that Needham increased its price target on Micron to $1,550 from $500 while maintaining a Buy rating on the stock, citing the continued strength of the memory market.

##### M02. Needham analyst forecasts strong market fundamentals to persist, driven by strong demand, robust pricing, and limited capacity additions, and cites optimism for long-term agreements.

- Mention ID：`mention:b978859f42849988a761994f0989b71a9e168442feb4f46c7abde25a9e9ddd22`
- 来源：[Micron Technology (MU) Announces Collaboration with Anthropic to Scale Next Generation AI Infrastructure](https://finance.yahoo.com/technology/ai/articles/micron-technology-mu-announces-collaboration-062406354.html)；`doxatlas:raw_media:d07de614-f3e3-431a-89dd-33bd139acd24`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- 参与者：Needham analyst (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：The analyst forecasts strong market fundamentals to persist, driven by the continued strong demand, a robust pricing environment, and limited capacity additions.
  - E02 `VERIFIED` / `text:0`：Additionally, the analyst also cited optimism for the long-term agreements being signed in the industry, as it provides suppliers with better demand visibility that extends over multiple years.

### P40. Communication services sector stocks decreased by 1.9%.

- Package ID：`package:9885ef27fb41e14fcfba6048`
- Family / Kind：`COMPANY_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：field:e42453ca47da2aafd0172a09
- Anchor artifact / period：— / —
- 摘要：Communication services sector stocks decreased by 1.9%.

#### P40-A01. Communication services sector stocks decreased by 1.9%.

- Atomic ID：`atomic:ff519c2c6f47e35c43a9cd80`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：2026-06-24T00:00:00 / 2026-06-24T00:00:00 / DAY

##### M01. Communication services sector stocks decreased by 1.9%.

- Mention ID：`mention:546e6b3ccd253fc9cb8a403d983e054f8b96922783f4258965f8046e1ea8a9ee`
- 来源：[Dow Surges 250 Points; Micron Posts Upbeat Q3 Earnings](https://finnhub.io/api/news?id=7316a88ccda79b3354a49965fa82f15ed81fe3722e51491463cf118f097a0143)；`doxatlas:raw_media:fce73732-1c68-4d0f-9d7f-c5800393ffff`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：communication services stocks (SUBJECT)
- 数量：1.9% [sector_change_percent]
- 时间：2026-06-24T00:00:00 / 2026-06-24T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：communication services stocks fell 1.9%

### P41. Sandisk stock price increased by 11.2% on Thursday morning.

- Package ID：`package:99a65e40d75cb25b31c7c845`
- Family / Kind：`COMPANY_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：3
- 层级规模：2 Atomic / 4 Mention
- Anchor entities：COMPANY_SNDK, COMPANY_STX, COMPANY_WDC, INSTITUTION_CITIGROUP
- Anchor artifact / period：— / —
- 摘要：Sandisk stock price increased by 11.2% on Thursday morning. SanDisk, Western Digital, and Seagate stock prices rose in after-hours trading.
- **人工审计：边界错误。** Sandisk Thursday-morning move and the multi-company after-hours move have different temporal/entity boundaries; the first Atomic is also polluted by a Citi target mention.

#### P41-A01. Sandisk stock price increased by 11.2% on Thursday morning.

- Atomic ID：`atomic:abde442d4d1632dd0b490a9c`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- Mention 数：3；Version：3
- 时间：2026-06-25T00:00:00 / DAY
- **人工审计：需拆分。** 该 Atomic 产生 2 个 FP pair；建议分区：sandisk_price_move, citi_analyst_action

##### M01. Sandisk stock price increased by 11.2% on Thursday morning.

- Mention ID：`mention:827a61b0fde7f60fdf1b6680526757ab6514fc34eeff796cafa2dd2347093884`
- 来源：[Why Sandisk Stock Soared Today](https://www.fool.com/investing/2026/06/25/why-sandisk-stock-soared-today/)；`doxatlas:raw_media:35a4333e-6608-482b-b963-ba2f64392063`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Sandisk (SUBJECT)
- 数量：11.2% [stock_price_change_percent]
- 时间：2026-06-25T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Computer memory specialist Sandisk (SNDK 14.13%) stock surged 11.2% Thursday morning after archrival Micron (MU 5.68%) crushed analyst forecasts for its fiscal Q3 earnings.

##### M02. Sandisk shares increased by approximately 15% in early trading on Thursday.

- Mention ID：`mention:ac3e0d7e03dd0a13a1c0fce1dac1ffe59ca2155f9e7d4730194bcad8a8d9f95d`
- 来源：[Sandisk Stock Spikes 15% After Citi Unleashes Massive Price Target Hike](https://finance.yahoo.com/markets/stocks/articles/sandisk-stock-spikes-15-citi-170934290.html)；`doxatlas:raw_media:4a7c28e1-f1dd-4efc-8d16-80debc253418`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Sandisk (SUBJECT)
- 数量：about 15% [stock_price_change_percent]
- 时间：2026-06-25T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Sandisk (NASDAQ:SNDK) shares jumped about 15% early Thursday

##### M03. Citi raised its price target on Sandisk to $2,500 from $2,025.

- Mention ID：`mention:d97788db62c1e89a95a10ecb254df1650531e0718595b1b08c7622d8b3729a36`
- 来源：[Sandisk Stock Spikes 15% After Citi Unleashes Massive Price Target Hike](https://finance.yahoo.com/markets/stocks/articles/sandisk-stock-spikes-15-citi-170934290.html)；`doxatlas:raw_media:4a7c28e1-f1dd-4efc-8d16-80debc253418`
- Family / Assertion：`ANALYST_ACTION` / `ACTUAL`
- 参与者：Citi (ACTOR)；Sandisk (SUBJECT)
- 数量：$2,500 [price_target]；$2,025 [previous_price_target]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Citi lifted its target on Sandisk to $2,500 from $2,025

#### P41-A02. SanDisk, Western Digital, and Seagate stock prices rose in after-hours trading.

- Atomic ID：`atomic:b8387e0e77f68d9022cf401d`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：UNKNOWN

##### M01. SanDisk, Western Digital, and Seagate stock prices rose in after-hours trading.

- Mention ID：`mention:a7d6526d387cd16aee9fea61d4e88d1596eca98e13597754fa72491f4cb8ba0e`
- 来源：[SanDisk, Western Digital, Seagate Surge After Hours As Micron's Blowout Earnings Signal Memory Upcycle](https://www.benzinga.com/markets/equities/26/06/60088616/sandisk-western-digital-and-seagate-surge-after-microns-blowout-earnings-signal-memory-upcycle)；`doxatlas:raw_media:6389d63c-442c-4c9b-9ba5-b943b7e91804`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：SanDisk (SUBJECT)；Western Digital (SUBJECT)；Seagate (SUBJECT)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `title:0`：SanDisk, Western Digital, Seagate Surge After Hours As Micron's Blowout Earnings Signal Memory Upcycle
  - E02 `VERIFIED` / `text:0`：All three technology stocks reversed intraday losses in after-hours trading following Micron’s results.
  - E03 `VERIFIED` / `text:0`：Micron’s strong results triggered a sympathy rally in WDC, SNDK and STX, which are exposed to the same demand trends.

### P42. Alphabet stock ended at $343.71, down 0.46%.

- Package ID：`package:9d6bd8a92b5a99c4e318d105`
- Family / Kind：`COMPANY_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：COMPANY_GOOG
- Anchor artifact / period：— / —
- 摘要：Alphabet stock ended at $343.71, down 0.46%.

#### P42-A01. Alphabet stock ended at $343.71, down 0.46%.

- Atomic ID：`atomic:2bc04e77112c184bf2760417`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：2026-06-25T00:00:00 / DAY

##### M01. Alphabet stock ended at $343.71, down 0.46%.

- Mention ID：`mention:ea1e0c500e1c1e96c50ac5615f1055e6cf1417852cc8b1d8c514dc8ea1e4beee`
- 来源：[Stock Market Today, June 25: Apple Drops After Raising Device Prices to Offset Higher Memory Costs](https://www.fool.com/coverage/stock-market-today/2026/06/25/stock-market-today-june-25-apple-drops-after-raising-device-prices-to-offset-higher-memory-costs/)；`doxatlas:raw_media:d9404d58-cd88-4ccc-a9d2-ec4004629dbc`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Alphabet (SUBJECT)
- 数量：$343.71 [closing_price]；down 0.46% [price_change_percent]
- 时间：2026-06-25T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Alphabet (GOOGL 0.23%) ended at $343.71, down 0.46%

### P43. The Korea Exchange (KRX) activated a buy-side sidecar mechanism shortly after the market open on June 25, 2026, suspending program trading for five minutes.

- Package ID：`package:9e56c5c2313e71875ce68812`
- Family / Kind：`REGULATORY_LEGAL` / `EPISODE`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：field:f58b7fc92f1917f16003ace8
- Anchor artifact / period：— / —
- 摘要：The Korea Exchange (KRX) activated a buy-side sidecar mechanism shortly after the market open on June 25, 2026, suspending program trading for five minutes.

#### P43-A01. The Korea Exchange (KRX) activated a buy-side sidecar mechanism shortly after the market open on June 25, 2026, suspending program trading for five minutes.

- Atomic ID：`atomic:1750219269a120e4a890a6c9`
- Family / Assertion：`REGULATORY_LEGAL_POLICY` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：2026-06-25T00:00:00 / DAY

##### M01. The Korea Exchange (KRX) activated a buy-side sidecar mechanism shortly after the market open on June 25, 2026, suspending program trading for five minutes.

- Mention ID：`mention:c64ba388d849bbb3e9d78214f6cab07077c6101e457164c4c93209bc01298b6f`
- 来源：[KOSPI Spikes 5% on Opening, Riding Micron’s Surprise Earnings](https://beincrypto.com/micron-earnings-kospi-sidecar-chip-rally/)；`doxatlas:raw_media:3f25dec4-7f5f-4203-9e0f-d37c2b06a2c7`
- Family / Assertion：`REGULATORY_LEGAL_POLICY` / `ACTUAL`
- 参与者：Korea Exchange (KRX) (ACTOR)
- 数量：—
- 时间：2026-06-25T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：The Korea Exchange (KRX) activated a buy-side sidecar shortly after the open, suspending program trading for five minutes.

### P44. Apple increased prices on MacBook Neo, MacBook Air, MacBook Pro, iPad Pro, iPad Air, HomePod, HomePod mini, and Apple TV due to tightening supplies of memory and storage components.

- Package ID：`package:a3df4c2e9b86e00eacb4a628`
- Family / Kind：`COMPANY_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：2
- 层级规模：1 Atomic / 4 Mention
- Anchor entities：COMPANY_AAPL
- Anchor artifact / period：— / —
- 摘要：Apple increased prices on MacBook Neo, MacBook Air, MacBook Pro, iPad Pro, iPad Air, HomePod, HomePod mini, and Apple TV due to tightening supplies of memory and storage components.

#### P44-A01. Apple increased prices on MacBook Neo, MacBook Air, MacBook Pro, iPad Pro, iPad Air, HomePod, HomePod mini, and Apple TV due to tightening supplies of memory and storage components.

- Atomic ID：`atomic:f2be60dbeafa371181445947`
- Family / Assertion：`COMMERCIAL_OPERATION` / `ACTUAL`
- Mention 数：4；Version：4
- 时间：2026-06-25T00:00:00 / DAY
- **人工审计：需拆分。** 该 Atomic 产生 0 个 FP pair

##### M01. Apple increased prices on MacBook Neo, MacBook Air, MacBook Pro, iPad Pro, iPad Air, HomePod, HomePod mini, and Apple TV due to tightening supplies of memory and storage components.

- Mention ID：`mention:e30ba8948561b098464159e81e95de3a3e38b28ba43a694e2f6e059f72cc0850`
- 来源：[Apple Just Confirmed Micron's Biggest AI Prediction](https://www.benzinga.com/trading-ideas/long-ideas/26/06/60108831/apple-just-confirmed-microns-biggest-ai-prediction)；`doxatlas:raw_media:35c3d868-abe6-4617-8c1f-c1460a8e8179`
- Family / Assertion：`COMMERCIAL_OPERATION` / `ACTUAL`
- 参与者：Apple (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：The price increases affect products including the MacBook Neo, MacBook Air, MacBook Pro, iPad Pro, iPad Air, HomePod, HomePod mini and Apple TV, while iPhone pricing remains unchanged. Apple attributed the increases to tightening supplies of memory and storage components as AI infrastructure spending accelerates.

##### M02. Apple raised prices on some MacBooks and iPads by $100 to $300.

- Mention ID：`mention:3c19bd333e7da829d0259103068a4934b03d67e0a840baf16a67ce8b74a584ee`
- 来源：[Why Micron's blowout earnings are a headache for Apple](https://finance.yahoo.com/markets/article/why-microns-blowout-earnings-are-a-headache-for-apple-161932441.html)；`doxatlas:raw_media:6ccb4979-525c-4467-a381-538f38a4fa9c`
- Family / Assertion：`COMMERCIAL_OPERATION` / `ACTUAL`
- 参与者：Apple (ACTOR)
- 数量：$100 [price_increase]；$300 [price_increase]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Apple raised prices on some MacBooks and iPads amid the global memory crisis, with increases running from $100 to $300 on some devices.

##### M03. Apple announced price hikes across its MacBook and iPad lineup.

- Mention ID：`mention:1b1fbcbaf73911ed5329a470340001f928f93779c1c32120f8f2a7757a3816c8`
- 来源：[Tim Cook Calls the Memory Crisis a "Hundred-Year Flood"](https://finance.yahoo.com/technology/articles/tim-cook-calls-memory-crisis-170140774.html)；`doxatlas:raw_media:d42584c3-2d2d-4559-affc-ccae5b388ce1`
- Family / Assertion：`COMMERCIAL_OPERATION` / `ACTUAL`
- 参与者：Apple (ACTOR)
- 数量：$100 to $300 [price_increase_range]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Apple (NASDAQ:AAPL) fell 0.56% intraday after the company announced price hikes across its MacBook and iPad lineup, its first formal move to pass higher memory and storage costs on to consumers. Price increases range from $100 to $300 across the lineup.

##### M04. Apple raised prices across Macs, iPads, home devices, and Vision Pro.

- Mention ID：`mention:15cdfada2d32ff648dce51a03734cd06915518942a545950d2037aee6f8d3ef2`
- 来源：[Stock Market Today, June 25: Apple Drops After Raising Device Prices to Offset Higher Memory Costs](https://www.fool.com/coverage/stock-market-today/2026/06/25/stock-market-today-june-25-apple-drops-after-raising-device-prices-to-offset-higher-memory-costs/)；`doxatlas:raw_media:d9404d58-cd88-4ccc-a9d2-ec4004629dbc`
- Family / Assertion：`COMMERCIAL_OPERATION` / `ACTUAL`
- 参与者：Apple (ACTOR)
- 数量：—
- 时间：2026-06-25T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Apple fell after it raised prices across Macs, iPads, home devices, and Vision Pro to offset higher memory and storage costs.
  - E02 `VERIFIED` / `text:0`：Apple announced price increases on several of its products.

### P45. UBS analysts stated that DRAM is likely to be constrained until at least halfway through 2028.

- Package ID：`package:abe7b285920a9d5d2cf1e623`
- Family / Kind：`ANALYST_REPORT` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 2 Mention
- Anchor entities：field:bea0cf1bcc875734080bdd78
- Anchor artifact / period：— / —
- 摘要：UBS analysts stated that DRAM is likely to be constrained until at least halfway through 2028.

#### P45-A01. UBS analysts stated that DRAM is likely to be constrained until at least halfway through 2028.

- Atomic ID：`atomic:f50a7c89fd9bba77ea1f0721`
- Family / Assertion：`ANALYST_ACTION` / `HYPOTHETICAL`
- Mention 数：2；Version：2
- 时间：UNKNOWN
- **人工审计：需拆分。** 该 Atomic 产生 1 个 FP pair

##### M01. UBS analysts stated that DRAM is likely to be constrained until at least halfway through 2028.

- Mention ID：`mention:0e249047d974d9ecc340547c4627bf397b119a175cf8c62ca2c73275c478f113`
- 来源：[Why Everyone Is Talking About Micron](https://www.fool.com/investing/2026/06/25/why-everyone-is-talking-about-micron/)；`doxatlas:raw_media:49fb27c5-84b4-4e56-8791-f7ac23de38b7`
- Family / Assertion：`ANALYST_ACTION` / `HYPOTHETICAL`
- 参与者：Analysts at UBS (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Analysts at UBS have previously said that DRAM is likely to be constrained until at least halfway through 2028

##### M02. UBS analysts stated that NAND is likely to be constrained until at least the end of 2027.

- Mention ID：`mention:e4d8685f38e11ff35833b1aa4de35478d75e89aa695f7b6f960545207fd1f3fb`
- 来源：[Why Everyone Is Talking About Micron](https://www.fool.com/investing/2026/06/25/why-everyone-is-talking-about-micron/)；`doxatlas:raw_media:49fb27c5-84b4-4e56-8791-f7ac23de38b7`
- Family / Assertion：`ANALYST_ACTION` / `HYPOTHETICAL`
- 参与者：Analysts at UBS (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：NAND is likely to be constrained until at least the end of 2027.

### P46. Wedbush rates Micron Technology at outperform with a price target of $1,300.

- Package ID：`package:b4f7480155e414dbcd0ffa31`
- Family / Kind：`ANALYST_REPORT` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：COMPANY_MU, field:adfc043125dadab998cd6d80
- Anchor artifact / period：— / —
- 摘要：Wedbush rates Micron Technology at outperform with a price target of $1,300.

#### P46-A01. Wedbush rates Micron Technology at outperform with a price target of $1,300.

- Atomic ID：`atomic:08ff55db26df05e83ab3784e`
- Family / Assertion：`ANALYST_ACTION` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：UNKNOWN

##### M01. Wedbush rates Micron Technology at outperform with a price target of $1,300.

- Mention ID：`mention:14ae255021aa94fa07d300ade28008176e392b67750b4695bd4de5caa3299e4b`
- 来源：[Wedbush says chip rally has further to run after Micron's blowout quarter](https://www.proactiveinvestors.com/companies/news/1094490/wedbush-says-chip-rally-has-further-to-run-after-micron-s-blowout-quarter-1094490.html)；`doxatlas:raw_media:79fe1f85-a1cc-44e1-928b-d0256c075eb4`
- Family / Assertion：`ANALYST_ACTION` / `ACTUAL`
- 参与者：Wedbush (ACTOR)；Micron Technology Inc (TARGET)
- 数量：$1,300 [price_target]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：The broker rates Micron at outperform with a price target of $1,300

### P47. Qualcomm raised its non-handset revenue target to $40 billion by 2029.

- Package ID：`package:b59befa9c621570971bbabbb`
- Family / Kind：`EARNINGS_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：3
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：COMPANY_QCOM
- Anchor artifact / period：— / field:3a5169f646679c1f910f30b4
- 摘要：Qualcomm raised its non-handset revenue target to $40 billion by 2029.

#### P47-A01. Qualcomm raised its non-handset revenue target to $40 billion by 2029.

- Atomic ID：`atomic:961d23cc8485b5ccf7bbdc96`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- Mention 数：1；Version：1
- 时间：by 2029 / UNKNOWN

##### M01. Qualcomm raised its non-handset revenue target to $40 billion by 2029.

- Mention ID：`mention:518264eae5bcc6b3a6a42096ea6d490907a92832b89c4b733f6167985ba3f9dd`
- 来源：[Micron Just Locked In $100 Billion in Sales, and Wall Street Thinks the Boom-Bust Chip Cycle Is Dead](https://247wallst.com/investing/2026/06/25/micron-just-locked-in-100-billion-in-sales-and-wall-street-thinks-the-boom-bust-chip-cycle-is-dead/)；`doxatlas:raw_media:1e515897-64d4-4896-b936-10a04501eff4`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- 参与者：Qualcomm (ACTOR)
- 数量：$40 billion [non_handset_revenue_target]
- 时间：by 2029 / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Qualcomm raised its non-handset revenue target to $40 billion by 2029, nearly double its prior forecast, with roughly $15 billion from data center.

### P48. Bank of America reiterated its Buy rating on Micron and lifted its price target to $1,550.

- Package ID：`package:b66a7ef51b75620cbbccb9db`
- Family / Kind：`ANALYST_REPORT` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：COMPANY_MU, INSTITUTION_BANK_OF_AMERICA
- Anchor artifact / period：— / —
- 摘要：Bank of America reiterated its Buy rating on Micron and lifted its price target to $1,550.

#### P48-A01. Bank of America reiterated its Buy rating on Micron and lifted its price target to $1,550.

- Atomic ID：`atomic:14d005d5aad634711bc1dff9`
- Family / Assertion：`ANALYST_ACTION` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：2026-06-25T00:00:00 / DAY

##### M01. Bank of America reiterated its Buy rating on Micron and lifted its price target to $1,550.

- Mention ID：`mention:902b8b08370cd0fe2f3c1f27576c2ea831aec9bff5fa98aabcf74993ebb924a8`
- 来源：[Micron shares surge to all-time high as Wall Street cheers unprecedented contract visibility](https://www.proactiveinvestors.com/companies/news/1094520/micron-shares-surge-to-all-time-high-as-wall-street-cheers-unprecedented-contract-visibility-1094520.html)；`doxatlas:raw_media:6b5d042a-9236-453b-8b60-2ba6c2f16b4f`
- Family / Assertion：`ANALYST_ACTION` / `ACTUAL`
- 参与者：Bank of America (ACTOR)；Micron (SUBJECT)
- 数量：$1,550 [price_target]
- 时间：2026-06-25T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Bank of America reiterated its Buy rating and lifted its price target to $1,550 from $1,500

### P49. Qualcomm signed two hyperscale deals for custom chips.

- Package ID：`package:c8772c8640b848553cb3806c`
- Family / Kind：`COMPANY_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：3
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：COMPANY_QCOM
- Anchor artifact / period：— / —
- 摘要：Qualcomm signed two hyperscale deals for custom chips.

#### P49-A01. Qualcomm signed two hyperscale deals for custom chips.

- Atomic ID：`atomic:356b9951f21a069c6b1bf3a2`
- Family / Assertion：`COMMERCIAL_OPERATION` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：UNKNOWN

##### M01. Qualcomm signed two hyperscale deals for custom chips.

- Mention ID：`mention:0f47a295c129cbb7a726c37bdf5909e97433283a76ae5434e69b6c33ece5b997`
- 来源：[Micron Just Locked In $100 Billion in Sales, and Wall Street Thinks the Boom-Bust Chip Cycle Is Dead](https://247wallst.com/investing/2026/06/25/micron-just-locked-in-100-billion-in-sales-and-wall-street-thinks-the-boom-bust-chip-cycle-is-dead/)；`doxatlas:raw_media:1e515897-64d4-4896-b936-10a04501eff4`
- Family / Assertion：`COMMERCIAL_OPERATION` / `ACTUAL`
- 参与者：Qualcomm (ACTOR)
- 数量：two hyperscale deals [deal_count]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：said it signed two hyperscale deals for custom chips, one in the United States, one Chinese

### P50. Qualcomm shares rose 12%.

- Package ID：`package:cb954f7ffef2027be6058c76`
- Family / Kind：`COMPANY_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：3
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：COMPANY_QCOM
- Anchor artifact / period：— / —
- 摘要：Qualcomm shares rose 12%.

#### P50-A01. Qualcomm shares rose 12%.

- Atomic ID：`atomic:7d9f5eee67dfc6d53ac176e6`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：UNKNOWN

##### M01. Qualcomm shares rose 12%.

- Mention ID：`mention:aeb564c8ba5d12fe4bf7eb4281a80e721b0ea819125729262547229117362a2d`
- 来源：[Micron Just Locked In $100 Billion in Sales, and Wall Street Thinks the Boom-Bust Chip Cycle Is Dead](https://247wallst.com/investing/2026/06/25/micron-just-locked-in-100-billion-in-sales-and-wall-street-thinks-the-boom-bust-chip-cycle-is-dead/)；`doxatlas:raw_media:1e515897-64d4-4896-b936-10a04501eff4`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Qualcomm (SUBJECT)
- 数量：12% [stock_price_change_percent]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Qualcomm shares rose 12% on the data center news

### P51. Apple stock closed at $275.15, down 6.12%.

- Package ID：`package:ce7060342cecbd9e4c0213a3`
- Family / Kind：`COMPANY_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：COMPANY_AAPL
- Anchor artifact / period：— / —
- 摘要：Apple stock closed at $275.15, down 6.12%.

#### P51-A01. Apple stock closed at $275.15, down 6.12%.

- Atomic ID：`atomic:95433fb8c4f53ded64601259`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：2026-06-25T00:00:00 / DAY

##### M01. Apple stock closed at $275.15, down 6.12%.

- Mention ID：`mention:d646f794c9ae67102a9b2184eb295007f67f84e79808b35506cff02aec85d540`
- 来源：[Stock Market Today, June 25: Apple Drops After Raising Device Prices to Offset Higher Memory Costs](https://www.fool.com/coverage/stock-market-today/2026/06/25/stock-market-today-june-25-apple-drops-after-raising-device-prices-to-offset-higher-memory-costs/)；`doxatlas:raw_media:d9404d58-cd88-4ccc-a9d2-ec4004629dbc`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Apple (SUBJECT)
- 数量：$275.15 [closing_price]；down 6.12% [price_change_percent]
- 时间：2026-06-25T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：The stock closed at $275.15, down 6.12%.

### P52. Europe’s Stoxx 600 index rose 0.6% by early afternoon on Thursday.

- Package ID：`package:cf38c56e277e189e9983fef5`
- Family / Kind：`COMPANY_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：field:3b65dc22c526c32e5f95e18d
- Anchor artifact / period：— / —
- 摘要：Europe’s Stoxx 600 index rose 0.6% by early afternoon on Thursday.

#### P52-A01. Europe’s Stoxx 600 index rose 0.6% by early afternoon on Thursday.

- Atomic ID：`atomic:6babebc5c42651a4a2b4077f`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY

##### M01. Europe’s Stoxx 600 index rose 0.6% by early afternoon on Thursday.

- Mention ID：`mention:255ef47c39c33557d19d143bf3ea9f685b04d1a7a0c9866af489cf527455ae56`
- 来源：[Investors bet on AI again after Micron reports 346% sales jump](https://edition.cnn.com/2026/06/25/business/micron-results-ai-stocks-volatility)；`doxatlas:raw_media:1aaec309-8ccb-42d6-b974-b4aa3bf2d43c`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Stoxx 600 (SUBJECT)
- 数量：0.6% [index_change]
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Europe’s benchmark Stoxx 600 index was up 0.6% by early afternoon local time.

### P53. Micron is investing at record levels in technology, products and supply.

- Package ID：`package:d12945f75eeee3658f7bec49`
- Family / Kind：`COMPANY_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：5
- 层级规模：3 Atomic / 20 Mention
- Anchor entities：COMPANY_MU, COMPANY_NVDA, field:6553f767b94f1bb2fdd8ba2d, field:8aec1db3a5ce176797365375, field:9e06a513adb1bb8c56cd8bcd
- Anchor artifact / period：— / —
- 摘要：Micron is investing at record levels in technology, products and supply. Micron Technology entered into a strategic partnership and supply agreement with Anthropic, including a strategic investment in Anthropic's Series H funding round. Micron Technology announced 16 signed Strategic Customer Agreements, which are multi-year deals locking in volume and providing pricing visibility for memory supply.
- **人工审计：边界错误。** June 22 Anthropic partnership/investment is a different bounded announcement from the earnings-origin SCA/investment commentary.

#### P53-A01. Micron is investing at record levels in technology, products and supply.

- Atomic ID：`atomic:3a4cce33a353c1eb9150244e`
- Family / Assertion：`COMMERCIAL_OPERATION` / `ONGOING`
- Mention 数：2；Version：2
- 时间：UNKNOWN
- **人工审计：需拆分。** 该 Atomic 产生 0 个 FP pair

##### M01. Micron is investing at record levels in technology, products and supply.

- Mention ID：`mention:5d71ec2f4cd3710b8ae6561686063cc8c980c6d4528762ff4f1402b670bcce0c`
- 来源：[These Analysts Boost Their Forecasts On Micron Technology After Better-Than-Expected Q3 Results](https://www.benzinga.com/analyst-stock-ratings/price-target/26/06/60100007/these-analysts-boost-their-forecasts-on-micron-technology-after-better-than-expected-q3-results)；`doxatlas:raw_media:5d890b9d-c789-4a99-87d6-8a34a3ea4cc2`
- Family / Assertion：`COMMERCIAL_OPERATION` / `ONGOING`
- 参与者：Micron (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron is investing at record levels in technology, products and supply to address our customers’ rapidly growing demand.

##### M02. Micron is investing at record levels to meet customer demand.

- Mention ID：`mention:c27f6f4025e10697cde6581d66792d8f6624533fac90db3dcf87d84b50561823`
- 来源：[Why Sandisk Stock Soared Today](https://www.fool.com/investing/2026/06/25/why-sandisk-stock-soared-today/)；`doxatlas:raw_media:35a4333e-6608-482b-b963-ba2f64392063`
- Family / Assertion：`COMMERCIAL_OPERATION` / `ONGOING`
- 参与者：Micron (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron's "investing at record levels" to help meet customer demand

#### P53-A02. Micron Technology entered into a strategic partnership and supply agreement with Anthropic, including a strategic investment in Anthropic's Series H funding round.

- Atomic ID：`atomic:61b1e7c3d5a0f6575cd8bed2`
- Family / Assertion：`COMMERCIAL_OPERATION` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：2026-06-22T00:00:00 / DAY

##### M01. Micron Technology entered into a strategic partnership and supply agreement with Anthropic, including a strategic investment in Anthropic's Series H funding round.

- Mention ID：`mention:4608a6338b17b480352e169ba2fa06e3fb8840bc4d874f45fc0df92e984175b9`
- 来源：[Micron Technology (MU) Announces Collaboration with Anthropic to Scale Next Generation AI Infrastructure](https://finance.yahoo.com/technology/ai/articles/micron-technology-mu-announces-collaboration-062406354.html)；`doxatlas:raw_media:d07de614-f3e3-431a-89dd-33bd139acd24`
- Family / Assertion：`COMMERCIAL_OPERATION` / `ACTUAL`
- 参与者：Micron Technology (ACTOR)；Anthropic (COUNTERPARTY)
- 数量：—
- 时间：2026-06-22T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：On June 22, Micron announced a strategic partnership with Anthropic to scale next-generation AI infrastructure.
  - E02 `VERIFIED` / `text:0`：In a press statement, Micron said the agreement spans memory and storage AI architecture design, supply and demand, and enterprise adoption of Claude across the company.
  - E03 `VERIFIED` / `text:0`：Additionally, it also includes a strategic investment in Anthropic's Series H funding round.
  - E04 `VERIFIED` / `text:0`：Among the key points of the collaboration is a memory and storage supply agreement spanning Micron's industry-leading data center portfolio, in which Micron will support Anthropic's multi-year growth trajectory as the frontier AI lab scales its compute strategy over the long term.

#### P53-A03. Micron Technology announced 16 signed Strategic Customer Agreements, which are multi-year deals locking in volume and providing pricing visibility for memory supply.

- Atomic ID：`atomic:d600f32b5062823e19b1134a`
- Family / Assertion：`COMMERCIAL_OPERATION` / `ACTUAL`
- Mention 数：17；Version：17
- 时间：2030-12-31T00:00:00 / UNKNOWN
- **人工审计：需拆分。** 该 Atomic 产生 84 个 FP pair；建议分区：signed_sca_program, sca_22b_commitments, future_revenue_share_from_sca, sca_contract_terms

##### M01. Micron Technology announced 16 signed Strategic Customer Agreements, which are multi-year deals locking in volume and providing pricing visibility for memory supply.

- Mention ID：`mention:041a24fc61183aa89ad49ed6d3807636e431259b1b8ed6ffc02590d193311263`
- 来源：[Micron Just Guided for a Staggering $50 Billion in Fiscal Q4 Revenue. Here's What It Means for the AI Trade.](https://www.fool.com/investing/2026/06/24/micron-just-guided-for-a-staggering-50-billion-in/)；`doxatlas:raw_media:c762d097-1190-4ea7-9802-5d44fadefd9f`
- Family / Assertion：`COMMERCIAL_OPERATION` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：16 signed agreements [agreement_count]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron announced what it called "transformational Strategic Customer Agreements" -- multi-year deals that lock in volume and provide pricing visibility for memory supply.
  - E02 `VERIFIED` / `text:0`：The 16 signed agreements represent about 20% of Micron's DRAM volume and a third of its NAND volume over the agreement period.

##### M02. Micron completed 16 Strategic Customer Agreements spanning data center, consumer, and automotive markets.

- Mention ID：`mention:d99d176ed8bc584f854552e52d7ae3a917c38c897bddbbb1ddfe23ebbaf9cd6e`
- 来源：[Micron CEO Expects Memory Supply To Improve Gradually In 2028, But There's No 'Line Of Sight' When It Will Catch Up To Rising AI Demand](https://www.benzinga.com/markets/tech/26/06/60089761/micron-ceo-sees-memory-supply-improving-by-2028-but-no-clear-end-in-sight-to-ai-demand-shortfallmicron-ceo-sees-memory-supply-improving-by-2028-but-no-clear-end-in-sight-to-ai-demand-shortfall)；`doxatlas:raw_media:25387f96-b423-437c-b2cd-8652aa176bd8`
- Family / Assertion：`COMMERCIAL_OPERATION` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：16 [agreement_count]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron announced it has completed 16 Strategic Customer Agreements, or SCAs, spanning data center, consumer and automotive markets.

##### M03. Micron introduced strategic customer agreements (SCA).

- Mention ID：`mention:1e402c6de87189e2fea9dfd118487b43fbf6b3fe0d7db5644c8f912c41f64d1a`
- 来源：[4 Blowout Numbers From Micron's Earnings Investors Need To See](https://www.fool.com/investing/2026/06/25/x-blowout-numbers-from-microns-earnings-investors/)；`doxatlas:raw_media:34fc2a61-fa6e-4db0-8e39-f862da0af41c`
- Family / Assertion：`COMMERCIAL_OPERATION` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`TEXT_NOT_FOUND`
  - E01 `TEXT_NOT_FOUND` / `text:0`：Micron just introduced strategic customer agreements (SCA), longer-term contracts that typically last five years.

##### M04. Micron has announced strategic customer agreements providing long-term demand visibility.

- Mention ID：`mention:7e759e908103a748724b65845a223d03e29af697fb88a5e3b545a3ce6308d155`
- 来源：[Micron Is Spending Billions On New Fabs—And Still Says It'll Return 100% Of Excess Cash To Shareholders](https://www.benzinga.com/trading-ideas/long-ideas/26/06/60095272/micron-is-spending-billions-on-new-fabs-and-still-says-itll-return-100-of-excess-cash-to-shareholders)；`doxatlas:raw_media:4fcc4fb0-ab7d-4157-8e65-a09ef40a7581`
- Family / Assertion：`COMMERCIAL_OPERATION` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：That confidence is also backed by Micron’s recently announced strategic customer agreements, which provide long-term demand visibility and support the company’s plans to invest aggressively in manufacturing while maintaining an increasingly shareholder-friendly capital allocation strategy.

##### M05. Micron Technology's customers committed $22 billion to secure supplies of its chips.

- Mention ID：`mention:6eb68ef8968c2a6a34f109fa76135aa76ff9b37e92b30dd9c8b3dfdc42a413fc`
- 来源：[Investors bet on AI again after Micron reports 346% sales jump](https://edition.cnn.com/2026/06/25/business/micron-results-ai-stocks-volatility)；`doxatlas:raw_media:1aaec309-8ccb-42d6-b974-b4aa3bf2d43c`
- Family / Assertion：`COMMERCIAL_OPERATION` / `ACTUAL`
- 参与者：customers (ACTOR)；Micron Technology (TARGET)
- 数量：$22 billion [commitment_value]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron said in its results that its customers had committed $22 billion to secure supplies of its chips.

##### M06. Micron Technology signed 16 strategic customer agreements.

- Mention ID：`mention:2468654dac3a4af0a246bcac57b2025aec0c996518066a6bbbc3943c4f126464`
- 来源：[Wedbush says chip rally has further to run after Micron's blowout quarter](https://www.proactiveinvestors.com/companies/news/1094490/wedbush-says-chip-rally-has-further-to-run-after-micron-s-blowout-quarter-1094490.html)；`doxatlas:raw_media:79fe1f85-a1cc-44e1-928b-d0256c075eb4`
- Family / Assertion：`COMMERCIAL_OPERATION` / `ACTUAL`
- 参与者：Micron Technology Inc (ACTOR)
- 数量：16 [agreement_count]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：The company also signed 16 strategic customer agreements, 14 of which carry cumulative revenue of at least $100 billion over the term of the deals.

##### M07. Micron disclosed that customers have committed approximately $22 billion through strategic agreements.

- Mention ID：`mention:555ef3ac59ffede47c466ccd76128d5951f37d42a1f8dfa7e50e181c78c59de6`
- 来源：[Apple Just Confirmed Micron's Biggest AI Prediction](https://www.benzinga.com/trading-ideas/long-ideas/26/06/60108831/apple-just-confirmed-microns-biggest-ai-prediction)；`doxatlas:raw_media:35c3d868-abe6-4617-8c1f-c1460a8e8179`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：$22 billion [committed_value]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron recently disclosed that customers have committed approximately $22 billion through strategic agreements, providing additional visibility into future demand as the company ramps production of high-bandwidth memory used in AI accelerators.

##### M08. Micron Technology signed 16 customer agreements involving commitments to purchase memory products, running through 2030.

- Mention ID：`mention:23b9391d3195fbc47b9681d1e702a84e02d8fc33612c8b0e07e0b2c571bc416e`
- 来源：[Is Micron a Buy After Its Blowout Earnings Report?](https://www.fool.com/investing/2026/06/25/is-micron-a-buy-after-its-blowout-earnings-report/)；`doxatlas:raw_media:a424d484-3e57-47ed-b21d-50aeceafe593`
- Family / Assertion：`COMMERCIAL_OPERATION` / `ACTUAL`
- 参与者：Micron Technology (ACTOR)；data center, consumer, and automotive customers (COUNTERPARTY)
- 数量：16 customer agreements [agreement_count]
- 时间：2030-12-31T00:00:00 / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron also signed 16 customer agreements that offer the company and investors visibility on revenue ahead, reinforcing the idea that the demand we've seen so far is set to continue.
  - E02 `VERIFIED` / `text:0`：The deals, with data center, consumer, and automotive customers, run through 2030 and involve commitments to purchase a certain volume of memory products.

##### M09. Micron Technology expects $22 billion in commitments from signed deals.

- Mention ID：`mention:789f718a130290d6351963f954e11719e8a9be63e3f5810175963bfc832f0242`
- 来源：[Is Micron a Buy After Its Blowout Earnings Report?](https://www.fool.com/investing/2026/06/25/is-micron-a-buy-after-its-blowout-earnings-report/)；`doxatlas:raw_media:a424d484-3e57-47ed-b21d-50aeceafe593`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- 参与者：Micron Technology (ACTOR)
- 数量：$22 billion [commitments]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：The company expects $22 billion in commitments from the deals signed so far,

##### M10. Micron Technology expects half of its revenue to eventually come from strategic agreements.

- Mention ID：`mention:8f2da55e4d8c2786fc98a608d440126a120f18e7c22675457944c37e1dc65936`
- 来源：[Is Micron a Buy After Its Blowout Earnings Report?](https://www.fool.com/investing/2026/06/25/is-micron-a-buy-after-its-blowout-earnings-report/)；`doxatlas:raw_media:a424d484-3e57-47ed-b21d-50aeceafe593`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- 参与者：Micron Technology (ACTOR)
- 数量：half [revenue_share]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：and it expects half of its revenue to eventually come from such strategic agreements.

##### M11. Micron secured 16 contracts with customers, including data centers and automakers, in the three- to five-year range that could bring in $22 billion.

- Mention ID：`mention:ba1fe298a674dcea08aa6271352e3f76a8b3e2a79f2bb2f14eb21fe7d207bc6d`
- 来源：[Why Everyone Is Talking About Micron](https://www.fool.com/investing/2026/06/25/why-everyone-is-talking-about-micron/)；`doxatlas:raw_media:49fb27c5-84b4-4e56-8791-f7ac23de38b7`
- Family / Assertion：`COMMERCIAL_OPERATION` / `ACTUAL`
- 参与者：Micron (ACTOR)；customers (COUNTERPARTY)
- 数量：16 contracts [contract_count]；$22 billion [potential_revenue]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron also said it has secured 16 contracts with customers, including data centers and automakers, in the three- to five-year range that could bring in $22 billion.

##### M12. Micron expects strategic customer agreements to eventually cover at least half of total company revenue.

- Mention ID：`mention:6fbfa9a2cddd803fb31a62a151a21e32506bd8d66724f9525776718bf989f9ec`
- 来源：[Micron shares surge to all-time high as Wall Street cheers unprecedented contract visibility](https://www.proactiveinvestors.com/companies/news/1094520/micron-shares-surge-to-all-time-high-as-wall-street-cheers-unprecedented-contract-visibility-1094520.html)；`doxatlas:raw_media:6b5d042a-9236-453b-8b60-2ba6c2f16b4f`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- 参与者：Micron (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron expects SCAs to eventually cover at least half of total company revenue, generating roughly $100 billion in remaining performance obligations.

##### M13. Micron's strategic customer agreements include price floors and ceilings, are backed by cash deposits and financial commitments, and carry no termination provisions.

- Mention ID：`mention:ab793f71f0e9424af0e53de06c3dbb8c5381c9af14ba712d387efbf3ea236c77`
- 来源：[Micron shares surge to all-time high as Wall Street cheers unprecedented contract visibility](https://www.proactiveinvestors.com/companies/news/1094520/micron-shares-surge-to-all-time-high-as-wall-street-cheers-unprecedented-contract-visibility-1094520.html)；`doxatlas:raw_media:6b5d042a-9236-453b-8b60-2ba6c2f16b4f`
- Family / Assertion：`COMMERCIAL_OPERATION` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：The deals include price floors and ceilings, are backed by cash deposits and financial commitments, and carry no termination provisions.
  - E02 `VERIFIED` / `text:0`："The agreements are guaranteed by cash deposits and financial commitments and do not contain provisions allowing for the termination of terms," Wedbush noted

##### M14. Micron has 16 strategic customer agreements in place.

- Mention ID：`mention:c220b18509153924accc10ee5102cea7cd3897793639150c3c188f1d2a3e0d91`
- 来源：[Micron shares surge to all-time high as Wall Street cheers unprecedented contract visibility](https://www.proactiveinvestors.com/companies/news/1094520/micron-shares-surge-to-all-time-high-as-wall-street-cheers-unprecedented-contract-visibility-1094520.html)；`doxatlas:raw_media:6b5d042a-9236-453b-8b60-2ba6c2f16b4f`
- Family / Assertion：`COMMERCIAL_OPERATION` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：16 SCAs [strategic_customer_agreements_count]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron now has 16 SCAs in place, with 14 of those carrying cumulative minimum revenue commitments of approximately $100 billion over the remaining agreement terms.

##### M15. Micron signed 16 long-term customer agreements, with 14 agreements securing approximately $100 billion in minimum guaranteed revenue through 2030.

- Mention ID：`mention:0ddc28b9f4f9624dea2d571530d9c09c368ea39ef8b63001a2cafedeead4963f`
- 来源：[Micron Just Locked In $100 Billion in Sales, and Wall Street Thinks the Boom-Bust Chip Cycle Is Dead](https://247wallst.com/investing/2026/06/25/micron-just-locked-in-100-billion-in-sales-and-wall-street-thinks-the-boom-bust-chip-cycle-is-dead/)；`doxatlas:raw_media:1e515897-64d4-4896-b936-10a04501eff4`
- Family / Assertion：`COMMERCIAL_OPERATION` / `ACTUAL`
- 参与者：Micron Technology (ACTOR)
- 数量：16 long-term customer agreements [agreement_count]；$100 billion [guaranteed_revenue]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron Technology (NASDAQ:MU | MU Price Prediction) had signed 16 long-term customer agreements, 14 of them locking in roughly $100 billion in minimum guaranteed revenue through 2030

##### M16. Micron secured 16 long-term Strategic Customer Agreements (SCAs) locking in roughly $22 billion in cash deposits and commitments, effectively selling out its 2026 manufacturing capacity.

- Mention ID：`mention:0e0f17cad752fbe56c5b1e0a700403f67f7d282b73548a9409d44b868770c86d`
- 来源：[MU Stock Soars as Micron’s Revenue More Than Quadrupled in Q3](https://www.barchart.com/story/news/3092/mu-stock-soars-as-microns-revenue-more-than-quadrupled-in-q3)；`doxatlas:raw_media:181db9d6-5959-4fb0-947e-3c6680861566`
- Family / Assertion：`COMMERCIAL_OPERATION` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：16 [agreement_count]；$22 billion [cash_deposits_commitments]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：the company has secured 16 long-term Strategic Customer Agreements (SCAs), which have effectively “sold out” its 2026 manufacturing capacity.
  - E02 `VERIFIED` / `text:0`：These multi-year take-or-pay deals lock in roughly $22 billion in cash deposits and commitments

##### M17. Micron disclosed that customers including Nvidia committed $22 billion to five-year take-or-pay deals for memory chip supplies.

- Mention ID：`mention:2d2b5b8f07fb1019eccd961a54e89ced4da76a0c98ec9540aabf48c966215e4d`
- 来源：[AI boom keeps memory chip makers in sweet spot, says expert](https://finnhub.io/api/news?id=83f579c98258ef8f6999941b3227364c4d014b9cc9cfd5300374568a2fb3e0c7)；`doxatlas:raw_media:f70bfd87-b3f2-4a7f-9a1c-6a94cd5279b2`
- Family / Assertion：`COMMERCIAL_OPERATION` / `ACTUAL`
- 参与者：Micron (ACTOR)；Nvidia (COUNTERPARTY)
- 数量：$22 billion [contract_value]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron said on Wednesday customers such as Nvidia had committed $22 billion to lock in supplies of memory chips, playing up huge growth in five-year "take-or-pay" deals that require clients to either buy its chips or hand over cash.

### P54. Micron expects memory shortages to persist at least through 2028.

- Package ID：`package:d33829fa2dab78ac6e67feea`
- Family / Kind：`COMPANY_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`QUARANTINED`；Version：3
- 层级规模：2 Atomic / 5 Mention
- Anchor entities：COMPANY_MU, PERSON_SANJAY_MEHROTRA
- Anchor artifact / period：— / COMPANY_MU_FY2027
- 摘要：Micron expects memory shortages to persist at least through 2028. Micron CEO Sanjay Mehrotra stated that supply will not catch up with demand anytime soon.

#### P54-A01. Micron expects memory shortages to persist at least through 2028.

- Atomic ID：`atomic:691794fba604f5143846ab99`
- Family / Assertion：`PRODUCTION_SUPPLY` / `EXPECTED`
- Mention 数：4；Version：4
- 时间：beyond its 2027 financial year / 2028-12-31T00:00:00 / YEAR
- **人工审计：需拆分。** 该 Atomic 产生 0 个 FP pair

##### M01. Micron expects memory shortages to persist at least through 2028.

- Mention ID：`mention:5fc444a5c917b0377f1d7c8429a77a8635abd5fcfe8fe09b74a74849e2cb0fda`
- 来源：[4 Blowout Numbers From Micron's Earnings Investors Need To See](https://www.fool.com/investing/2026/06/25/x-blowout-numbers-from-microns-earnings-investors/)；`doxatlas:raw_media:34fc2a61-fa6e-4db0-8e39-f862da0af41c`
- Family / Assertion：`PRODUCTION_SUPPLY` / `EXPECTED`
- 参与者：Micron (ACTOR)
- 数量：—
- 时间：2028-12-31T00:00:00 / YEAR
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：memory shortages are expected to persist at least through 2028

##### M02. Micron indicated that AI memory supply constraints could persist beyond 2028.

- Mention ID：`mention:7044939ee209cea8f78488c194bfff1fe698b014421e7484edf031e648acf523`
- 来源：[Micron CEO Says AI Memory Shortage Could Last Beyond 2028: These ETFs Are Positioned To Profit](https://www.benzinga.com/etfs/sector-etfs/26/06/60094351/micron-ceo-says-ai-memory-shortage-could-last-beyond-2028-these-etfs-are-positioned-to-profit)；`doxatlas:raw_media:bf5258ec-37f8-408b-ace2-8b016d16f725`
- Family / Assertion：`PRODUCTION_SUPPLY` / `EXPECTED`
- 参与者：Micron (ACTOR)
- 数量：—
- 时间：2028-12-31T00:00:00 / YEAR
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `title:0`：Micron CEO Says AI Memory Shortage Could Last Beyond 2028
  - E02 `VERIFIED` / `text:0`：Micron’s indication that supply constraints could persist through 2028

##### M03. Micron Technology expects tight supply conditions to persist beyond its 2027 financial year.

- Mention ID：`mention:7208f9b3cfcbaa6a5401a8c4a013108961e32a20ff699c8c19ded16553b76fb4`
- 来源：[Wedbush says chip rally has further to run after Micron's blowout quarter](https://www.proactiveinvestors.com/companies/news/1094490/wedbush-says-chip-rally-has-further-to-run-after-micron-s-blowout-quarter-1094490.html)；`doxatlas:raw_media:79fe1f85-a1cc-44e1-928b-d0256c075eb4`
- Family / Assertion：`PRODUCTION_SUPPLY` / `EXPECTED`
- 参与者：Micron Technology Inc (ACTOR)
- 数量：—
- 时间：beyond its 2027 financial year / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：the company expects tight supply conditions to persist beyond its 2027 financial year

##### M04. Micron expects tight memory market conditions to persist beyond calendar 2027 with no clear end in sight for supply catching up with demand.

- Mention ID：`mention:abb24a1a0d87c263b84ffae4536731505c12f7daf052e2e2a587920eb884a453`
- 来源：[Apple Just Confirmed Micron's Biggest AI Prediction](https://www.benzinga.com/trading-ideas/long-ideas/26/06/60108831/apple-just-confirmed-microns-biggest-ai-prediction)；`doxatlas:raw_media:35c3d868-abe6-4617-8c1f-c1460a8e8179`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- 参与者：Micron (ACTOR)；Sanjay Mehrotra (OTHER)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：During its fiscal Q3 earnings call, Micron CEO Sanjay Mehrotra said the company still sees no clear end to tightening memory markets. “We currently do not have line of sight as to when memory supply will be able to catch up with increasing demand,” Mehrotra told investors. “We expect tight conditions to persist beyond calendar 2027.”

#### P54-A02. Micron CEO Sanjay Mehrotra stated that supply will not catch up with demand anytime soon.

- Atomic ID：`atomic:c9e91519c630d697566bb451`
- Family / Assertion：`PRODUCTION_SUPPLY` / `EXPECTED`
- Mention 数：1；Version：1
- 时间：UNKNOWN

##### M01. Micron CEO Sanjay Mehrotra stated that supply will not catch up with demand anytime soon.

- Mention ID：`mention:83e55796080fccba2b4f942dea43849173eb655ebd265cca9e6d6f2d540ea571`
- 来源：[Why Sandisk Stock Soared Today](https://www.fool.com/investing/2026/06/25/why-sandisk-stock-soared-today/)；`doxatlas:raw_media:35a4333e-6608-482b-b963-ba2f64392063`
- Family / Assertion：`PRODUCTION_SUPPLY` / `EXPECTED`
- 参与者：Sanjay Mehrotra (ACTOR)；Micron (OTHER)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Mehrotra still doesn't see supply catching up with demand anytime soon.

### P55. Sandisk is scheduled to hold an investor day in August where it may provide updates on demand expectations, technology roadmap, and capital return plans.

- Package ID：`package:d5510532c528cea177f1b90b`
- Family / Kind：`COMPANY_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：COMPANY_SNDK
- Anchor artifact / period：— / —
- 摘要：Sandisk is scheduled to hold an investor day in August where it may provide updates on demand expectations, technology roadmap, and capital return plans.

#### P55-A01. Sandisk is scheduled to hold an investor day in August where it may provide updates on demand expectations, technology roadmap, and capital return plans.

- Atomic ID：`atomic:6319858efa55f24468a0c991`
- Family / Assertion：`GOVERNANCE_PERSONNEL` / `PLANNED`
- Mention 数：1；Version：1
- 时间：2026-08-01T00:00:00 / 2026-08-31T00:00:00 / MONTH

##### M01. Sandisk is scheduled to hold an investor day in August where it may provide updates on demand expectations, technology roadmap, and capital return plans.

- Mention ID：`mention:dfb089a1fe38d4d8552c54bbc87a32f01b3cd84dc13b9f01e88ada9c874ec9a4`
- 来源：[Sandisk Stock Spikes 15% After Citi Unleashes Massive Price Target Hike](https://finance.yahoo.com/markets/stocks/articles/sandisk-stock-spikes-15-citi-170934290.html)；`doxatlas:raw_media:4a7c28e1-f1dd-4efc-8d16-80debc253418`
- Family / Assertion：`GOVERNANCE_PERSONNEL` / `PLANNED`
- 参与者：Sandisk (ACTOR)
- 数量：—
- 时间：2026-08-01T00:00:00 / 2026-08-31T00:00:00 / MONTH
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Sandisk may also get another lift from its investor day in August. Citi said the event could bring updates on demand expectations, the company's technology roadmap and capital return plans

### P56. The Dow Jones index increased by 0.5% to close at 52,107.28.

- Package ID：`package:d8100b7e915a284a260fbd3f`
- Family / Kind：`COMPANY_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：2
- 层级规模：1 Atomic / 2 Mention
- Anchor entities：INSTRUMENT_DOW_JONES
- Anchor artifact / period：— / —
- 摘要：The Dow Jones index increased by 0.5% to close at 52,107.28.

#### P56-A01. The Dow Jones index increased by 0.5% to close at 52,107.28.

- Atomic ID：`atomic:df838df0795aaa8b8a77aaa3`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- Mention 数：2；Version：2
- 时间：2026-06-24T00:00:00 / 2026-06-25T00:00:00 / DAY
- **人工审计：需拆分。** 该 Atomic 产生 1 个 FP pair

##### M01. The Dow Jones index increased by 0.5% to close at 52,107.28.

- Mention ID：`mention:e3b4594cead251e24dcb27120747bbcda28e9012ebcb5892ff6812c7d0240bac`
- 来源：[Dow Surges 250 Points; Micron Posts Upbeat Q3 Earnings](https://finnhub.io/api/news?id=7316a88ccda79b3354a49965fa82f15ed81fe3722e51491463cf118f097a0143)；`doxatlas:raw_media:fce73732-1c68-4d0f-9d7f-c5800393ffff`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Dow Jones index (SUBJECT)
- 数量：0.5% [index_change_percent]；52,107.28 [index_close_value]
- 时间：2026-06-24T00:00:00 / 2026-06-24T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：The Dow Jones index rose 0.5% to 52,107.28

##### M02. Dow Jones Industrial Average rose 0.3% in pre-market trade on Thursday.

- Mention ID：`mention:2b4f7b432fe8f527d530d5ba4eee2d13029f726e57346687a6020785ea48914a`
- 来源：[Investors bet on AI again after Micron reports 346% sales jump](https://edition.cnn.com/2026/06/25/business/micron-results-ai-stocks-volatility)；`doxatlas:raw_media:1aaec309-8ccb-42d6-b974-b4aa3bf2d43c`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Dow (SUBJECT)
- 数量：0.3% [index_change_dow]
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Dow was pointing up by 0.3%

### P57. Humanoid robots carry 10 times the amount of memory as an average L2+ vehicle.

- Package ID：`package:dd8916aafacf09cd6062afbc`
- Family / Kind：`PRODUCT_SCIENCE` / `EPISODE`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：field:03c98af4f8977b0f06f8a7b2, field:d4745b6cd0380329543331f1
- Anchor artifact / period：— / —
- 摘要：Humanoid robots carry 10 times the amount of memory as an average L2+ vehicle.

#### P57-A01. Humanoid robots carry 10 times the amount of memory as an average L2+ vehicle.

- Atomic ID：`atomic:b3f22db973806e37cd4be4f3`
- Family / Assertion：`PRODUCT_SCIENCE` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：UNKNOWN

##### M01. Humanoid robots carry 10 times the amount of memory as an average L2+ vehicle.

- Mention ID：`mention:e62380e83729ecade9b378e0cbe2990da14decaa694173a835051a93a87759ed`
- 来源：[Tesla's Optimus Could Become A Bigger Memory Customer Than Its Cars, If Micron Is Right](https://www.benzinga.com/trading-ideas/long-ideas/26/06/60094569/tesla-optimus-could-become-a-bigger-memory-customer-than-its-cars-if-micron-is-right)；`doxatlas:raw_media:005d3637-8724-483c-9077-7f643d3fa481`
- Family / Assertion：`PRODUCT_SCIENCE` / `ACTUAL`
- 参与者：Humanoid robots (SUBJECT)；average L2+ vehicle (TARGET)
- 数量：10 times [memory_ratio]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：“Humanoid robots carry 10 times the amount of memory as an average L2+ vehicle,” Mehrotra said.

### P58. The S&P 500 index increased by 0.6% to close at 7,401.17.

- Package ID：`package:ddaf0a70ecf3437162dc447b`
- Family / Kind：`COMPANY_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：INSTRUMENT_SP500
- Anchor artifact / period：— / —
- 摘要：The S&P 500 index increased by 0.6% to close at 7,401.17.

#### P58-A01. The S&P 500 index increased by 0.6% to close at 7,401.17.

- Atomic ID：`atomic:57e65b6d44bdca232795e00a`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：2026-06-24T00:00:00 / 2026-06-24T00:00:00 / DAY

##### M01. The S&P 500 index increased by 0.6% to close at 7,401.17.

- Mention ID：`mention:1863890551bd3e27d85dc1db51152a4f273cde46cb2c14f2f3284054c667e2af`
- 来源：[Dow Surges 250 Points; Micron Posts Upbeat Q3 Earnings](https://finnhub.io/api/news?id=7316a88ccda79b3354a49965fa82f15ed81fe3722e51491463cf118f097a0143)；`doxatlas:raw_media:fce73732-1c68-4d0f-9d7f-c5800393ffff`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：S&P 500 (SUBJECT)
- 数量：0.6% [index_change_percent]；7,401.17 [index_close_value]
- 时间：2026-06-24T00:00:00 / 2026-06-24T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：S&P 500 gained 0.6% to 7,401.17

### P59. UBS tripled its price target for Micron.

- Package ID：`package:e0c5d3a5e8c29cb06dff2732`
- Family / Kind：`ANALYST_REPORT` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：COMPANY_MU, COMPANY_UBS
- Anchor artifact / period：— / field:0bff461e507317f811c5f6d0
- 摘要：UBS tripled its price target for Micron.

#### P59-A01. UBS tripled its price target for Micron.

- Atomic ID：`atomic:0465adcdc67122ff3325e8ae`
- Family / Assertion：`ANALYST_ACTION` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：last month / 2026-05-01T00:00:00 / 2026-05-31T00:00:00 / MONTH

##### M01. UBS tripled its price target for Micron.

- Mention ID：`mention:71e01eb3df902a2caf6746f2f35f8be20060f65cf05bf19b1b862edd4b9136f6`
- 来源：[Micron Just Locked In $100 Billion in Sales, and Wall Street Thinks the Boom-Bust Chip Cycle Is Dead](https://247wallst.com/investing/2026/06/25/micron-just-locked-in-100-billion-in-sales-and-wall-street-thinks-the-boom-bust-chip-cycle-is-dead/)；`doxatlas:raw_media:1e515897-64d4-4896-b936-10a04501eff4`
- Family / Assertion：`ANALYST_ACTION` / `ACTUAL`
- 参与者：UBS (ACTOR)；Micron (TARGET)
- 数量：—
- 时间：last month / 2026-05-01T00:00:00 / 2026-05-31T00:00:00 / MONTH
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：UBS tripled Micron’s price target last month

### P60. CEO Tim Cook described the memory crisis as a 'hundred-year flood'.

- Package ID：`package:e89d13886493246fecd98372`
- Family / Kind：`COMPANY_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：PERSON_TIM_COOK_AF589A27
- Anchor artifact / period：— / —
- 摘要：CEO Tim Cook described the memory crisis as a 'hundred-year flood'.

#### P60-A01. CEO Tim Cook described the memory crisis as a 'hundred-year flood'.

- Atomic ID：`atomic:8871912b4d6f1ca97fe82b87`
- Family / Assertion：`OTHER` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：UNKNOWN

##### M01. CEO Tim Cook described the memory crisis as a 'hundred-year flood'.

- Mention ID：`mention:ef71600a7cd1f2f0fc2ab7e7f125c5e08c3fa3eb0b1aa99d731577fa13157566`
- 来源：[Tim Cook Calls the Memory Crisis a "Hundred-Year Flood"](https://finance.yahoo.com/technology/articles/tim-cook-calls-memory-crisis-170140774.html)；`doxatlas:raw_media:d42584c3-2d2d-4559-affc-ccae5b388ce1`
- Family / Assertion：`OTHER` / `ACTUAL`
- 参与者：Tim Cook (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：CEO Tim Cook told the Wall Street Journal it was a "hundred-year flood."

### P61. IDC expects all new iPhone models to move to 12GB of RAM.

- Package ID：`package:f14acdb5916e197398055d48`
- Family / Kind：`EARNINGS_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：3
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：COMPANY_WDC, field:d85f6257dc3b00dc4993266c
- Anchor artifact / period：— / —
- 摘要：IDC expects all new iPhone models to move to 12GB of RAM.

#### P61-A01. IDC expects all new iPhone models to move to 12GB of RAM.

- Atomic ID：`atomic:b43782f39f7d332def65d146`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- Mention 数：1；Version：1
- 时间：UNKNOWN

##### M01. IDC expects all new iPhone models to move to 12GB of RAM.

- Mention ID：`mention:897a4113b62ac706b54e9f90412a5547d463f0ee89c2fdd30615f783ee5c774d`
- 来源：[Tim Cook Calls the Memory Crisis a "Hundred-Year Flood"](https://finance.yahoo.com/technology/articles/tim-cook-calls-memory-crisis-170140774.html)；`doxatlas:raw_media:d42584c3-2d2d-4559-affc-ccae5b388ce1`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- 参与者：IDC (ACTOR)；new iPhone models (SUBJECT)
- 数量：12GB [ram_capacity]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：IDC expects all new iPhone models to move to 12GB of RAM as Apple pushes Apple Intelligence features that require more memory,

### P62. US Nasdaq rose 2.15% in pre-market trade on Thursday.

- Package ID：`package:f2901e24793c49cb9f7531a2`
- Family / Kind：`COMPANY_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：COMPANY_NDAQ
- Anchor artifact / period：— / —
- 摘要：US Nasdaq rose 2.15% in pre-market trade on Thursday.

#### P62-A01. US Nasdaq rose 2.15% in pre-market trade on Thursday.

- Atomic ID：`atomic:4095e0e89b9d137a24edb7b0`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY

##### M01. US Nasdaq rose 2.15% in pre-market trade on Thursday.

- Mention ID：`mention:40466af0c927686ec363fa3387a8428d4fa686ccf14852e1dc6f4fe086dc60a6`
- 来源：[Investors bet on AI again after Micron reports 346% sales jump](https://edition.cnn.com/2026/06/25/business/micron-results-ai-stocks-volatility)；`doxatlas:raw_media:1aaec309-8ccb-42d6-b974-b4aa3bf2d43c`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Nasdaq (SUBJECT)
- 数量：2.15% [index_change_nasdaq]
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY
- Evidence 状态：`TEXT_NOT_FOUND`
  - E01 `TEXT_NOT_FOUND` / `text:0`：the tech-heavy Nasdaq ... were up 2.15% ... in pre-market trade

### P63. Micron Technology reported third-quarter profit of $28.2 billion.

- Package ID：`package:f5ca4df942a4fe3b4f114c02`
- Family / Kind：`EARNINGS_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`FROZEN`；Version：25
- 层级规模：26 Atomic / 113 Mention
- Anchor entities：COMPANY_MU, INSTITUTION_CARSON_MANAGEMENT, INSTRUMENT_MU, field:467688e04f9945cb3e030d92, field:83ff209b13966cb801a64490, field:874fbcf8d97a024be027aa3a, field:9822265adfc889340ed18916, field:af4a1c588e106b096a870586, field:e07556b38df30cf89aa55ecd
- Anchor artifact / period：— / —
- 摘要：Micron Technology reported third-quarter profit of $28.2 billion. Micron expects fourth-quarter net income to exceed $40 billion. Micron reported a quarterly operating margin of 80.4%.
- **人工审计：边界错误。** Immediate stock reaction is external to the earnings disclosure.

#### P63-A01. Micron Technology reported third-quarter profit of $28.2 billion.

- Atomic ID：`atomic:05958579c837a77c50442ce3`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- Mention 数：3；Version：3
- 时间：third quarter / UNKNOWN
- **人工审计：需拆分。** 该 Atomic 产生 0 个 FP pair

##### M01. Micron Technology reported third-quarter profit of $28.2 billion.

- Mention ID：`mention:af2773b30acff06b5626d8664aac997a35986e2993919fef66f46939b30430cb`
- 来源：[Investors bet on AI again after Micron reports 346% sales jump](https://edition.cnn.com/2026/06/25/business/micron-results-ai-stocks-volatility)；`doxatlas:raw_media:1aaec309-8ccb-42d6-b974-b4aa3bf2d43c`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron Technology (ACTOR)
- 数量：$28.2 billion [profit]
- 时间：third quarter / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：The company reported a surge in profit during its third quarter to $28.2 billion

##### M02. Micron reported GAAP profits increased by 104% sequentially.

- Mention ID：`mention:4693c1709846c86ae447f1f9b0ba97ac240e0a0533ff89d71deab2c200b58800`
- 来源：[Why Sandisk Stock Soared Today](https://www.fool.com/investing/2026/06/25/why-sandisk-stock-soared-today/)；`doxatlas:raw_media:35a4333e-6608-482b-b963-ba2f64392063`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：104% [gaap_profits_sequential_change]
- 时间：UNKNOWN
- Evidence 状态：`TEXT_NOT_FOUND`
  - E01 `TEXT_NOT_FOUND` / `text:0`：Micron just reported GAAP profits up 104% sequentially.

##### M03. Micron Technology reported quarterly net income of $28 billion.

- Mention ID：`mention:435155369ac6e1b83d580f9d4a6067959d602b6caf06349a42f4250e3d5c29a3`
- 来源：[Is Micron a Buy After Its Blowout Earnings Report?](https://www.fool.com/investing/2026/06/25/is-micron-a-buy-after-its-blowout-earnings-report/)；`doxatlas:raw_media:a424d484-3e57-47ed-b21d-50aeceafe593`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron Technology (ACTOR)
- 数量：$28 billion [net_income]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：and net income jumped in the quadruple digits to $28 billion.

#### P63-A02. Micron expects fourth-quarter net income to exceed $40 billion.

- Atomic ID：`atomic:13a527c7aa56eb8f628cb552`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- Mention 数：1；Version：1
- 时间：fourth quarter / UNKNOWN

##### M01. Micron expects fourth-quarter net income to exceed $40 billion.

- Mention ID：`mention:4024afe93ac3d1ecf49240dbf2b7c1c77d9fa6ddf7378312cdbc43b52e47077a`
- 来源：[4 Blowout Numbers From Micron's Earnings Investors Need To See](https://www.fool.com/investing/2026/06/25/x-blowout-numbers-from-microns-earnings-investors/)；`doxatlas:raw_media:34fc2a61-fa6e-4db0-8e39-f862da0af41c`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- 参与者：Micron (ACTOR)
- 数量：$40 billion [net_income_guidance]
- 时间：fourth quarter / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：the company expects to top $40 billion in the fourth quarter

#### P63-A03. Micron reported a quarterly operating margin of 80.4%.

- Atomic ID：`atomic:1e49f029065a4b10594ce62d`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：third-quarter / UNKNOWN

##### M01. Micron reported a quarterly operating margin of 80.4%.

- Mention ID：`mention:eb479d5220858902e2b5b29a915eba50513bcdec73fdea50d0ca866fd6c1c1b8`
- 来源：[4 Blowout Numbers From Micron's Earnings Investors Need To See](https://www.fool.com/investing/2026/06/25/x-blowout-numbers-from-microns-earnings-investors/)；`doxatlas:raw_media:34fc2a61-fa6e-4db0-8e39-f862da0af41c`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：80.4% [operating_margin]
- 时间：third-quarter / UNKNOWN
- Evidence 状态：`TEXT_NOT_FOUND`
  - E01 `TEXT_NOT_FOUND` / `text:0`：80.4% was Micron's operating margin in the quarter.

#### P63-A04. Micron Technology guided gross margin to about 86%.

- Atomic ID：`atomic:2c9c41c1152e775bbf22d6db`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- Mention 数：5；Version：5
- 时间：this quarter / UNKNOWN
- **人工审计：需拆分。** 该 Atomic 产生 6 个 FP pair；建议分区：q4_gross_margin_guidance, q4_revenue_guidance

##### M01. Micron Technology guided gross margin to about 86%.

- Mention ID：`mention:985f831dc852f58d6efd52a3fabd7062d5465a51a48fbd253ad2e86c00923885`
- 来源：[Wedbush says chip rally has further to run after Micron's blowout quarter](https://www.proactiveinvestors.com/companies/news/1094490/wedbush-says-chip-rally-has-further-to-run-after-micron-s-blowout-quarter-1094490.html)；`doxatlas:raw_media:79fe1f85-a1cc-44e1-928b-d0256c075eb4`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- 参与者：Micron Technology Inc (ACTOR)
- 数量：86% [gross_margin]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：with gross margin guided to about 86%.

##### M02. Micron expects its gross margin to rise to about 86% in the current quarter.

- Mention ID：`mention:53ccfa3ccdddf5e415107a533812be7b8d25a3882453a9ff8d5d493d11308462`
- 来源：[Why Micron's blowout earnings are a headache for Apple](https://finance.yahoo.com/markets/article/why-microns-blowout-earnings-are-a-headache-for-apple-161932441.html)；`doxatlas:raw_media:6ccb4979-525c-4467-a381-538f38a4fa9c`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- 参与者：Micron (ACTOR)
- 数量：about 86% [gross_margin]
- 时间：this quarter / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：The company expects that figure to rise to about 86% this quarter.

##### M03. Micron guided fourth-quarter gross margin to roughly 86%.

- Mention ID：`mention:de81534d98c814d281fee5ddd29332f1ec5ee94a438947fd05c220c671c26576`
- 来源：[Micron shares surge to all-time high as Wall Street cheers unprecedented contract visibility](https://www.proactiveinvestors.com/companies/news/1094520/micron-shares-surge-to-all-time-high-as-wall-street-cheers-unprecedented-contract-visibility-1094520.html)；`doxatlas:raw_media:6b5d042a-9236-453b-8b60-2ba6c2f16b4f`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- 参与者：Micron (ACTOR)
- 数量：roughly 86% [gross_margin]
- 时间：Fourth-quarter / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Gross margin is expected to reach roughly 86%, with non-GAAP EPS guided to $31.

##### M04. Micron guided fiscal Q4 revenue to approximately $50 billion plus or minus $1.0 billion and gross margin to around 86%.

- Mention ID：`mention:85ce228bbd4bdf19e3babe678a0ae029aac140987abd1881a71cb2fd23cfbd87`
- 来源：[Micron Just Locked In $100 Billion in Sales, and Wall Street Thinks the Boom-Bust Chip Cycle Is Dead](https://247wallst.com/investing/2026/06/25/micron-just-locked-in-100-billion-in-sales-and-wall-street-thinks-the-boom-bust-chip-cycle-is-dead/)；`doxatlas:raw_media:1e515897-64d4-4896-b936-10a04501eff4`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- 参与者：Micron (ACTOR)
- 数量：$50 billion [revenue_guidance]；86% [gross_margin_guidance]
- 时间：Q4 / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron is guiding Q4 to around 86% on $50 billion plus or minus $1.0 billion in revenue.

##### M05. Micron management guided for approximately 20% sequential revenue growth in the current quarter.

- Mention ID：`mention:f93e6ebc1789f367cf930cf73e0963f4373cd6d8d2df4a2de4851c91424512bf`
- 来源：[MU Stock Soars as Micron’s Revenue More Than Quadrupled in Q3](https://www.barchart.com/story/news/3092/mu-stock-soars-as-microns-revenue-more-than-quadrupled-in-q3)；`doxatlas:raw_media:181db9d6-5959-4fb0-947e-3c6680861566`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- 参与者：Micron management (ACTOR)
- 数量：20% [sequential_growth]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：As investors reacted to management’s impressive guidance for about a 20% sequential growth in the current quarter

#### P63-A05. Micron's entire 2026 output of high-bandwidth memory chips is sold out under fixed-price contracts.

- Atomic ID：`atomic:357ca01b998198a7becbb4e7`
- Family / Assertion：`COMMERCIAL_OPERATION` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：2026 / UNKNOWN

##### M01. Micron's entire 2026 output of high-bandwidth memory chips is sold out under fixed-price contracts.

- Mention ID：`mention:64446444016fa6d814b30b017fcaf98bdd9dc0dd9d7789c2fa10753f52e6397e`
- 来源：[Micron posts record results as AI boom drives 15-fold jump in net profit](https://www.euronews.com/business/2026/06/25/micron-posts-record-results-as-ai-boom-drives-15-fold-jump-in-net-profit)；`doxatlas:raw_media:2b5bba2a-e472-4a1f-a341-5be6e4286dd1`
- Family / Assertion：`COMMERCIAL_OPERATION` / `ACTUAL`
- 参与者：Micron Technology (ACTOR)
- 数量：—
- 时间：2026 / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron has said its entire 2026 output of these chips is already sold out under fixed-price contracts.

#### P63-A06. Micron Technology stock gained roughly 15% in after-hours trading following the announcement of its fiscal Q3 2026 results.

- Atomic ID：`atomic:471dce1eefd76ebaf3fd27fc`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- Mention 数：11；Version：11
- 时间：2026-06-24T00:00:00 / 2026-06-25T00:00:00 / DAY
- **人工审计：需拆分。** 该 Atomic 产生 39 个 FP pair；建议分区：june24_after_hours_price_move, june25_regular_price_move, june25_premarket_price_move, june24_market_cap_level

##### M01. Micron Technology stock gained roughly 15% in after-hours trading following the announcement of its fiscal Q3 2026 results.

- Mention ID：`mention:5972075a71b2347bfd9124e9f878584dfdc4294defaf7992276b9ca362c3060e`
- 来源：[KOSPI Spikes 5% on Opening, Riding Micron’s Surprise Earnings](https://beincrypto.com/micron-earnings-kospi-sidecar-chip-rally/)；`doxatlas:raw_media:3f25dec4-7f5f-4203-9e0f-d37c2b06a2c7`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Micron Technology (SUBJECT)
- 数量：roughly 15% [stock_change_percent]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：The stock gained roughly 15% in after-hours trading following the announcement.

##### M02. Micron Technology shares increased approximately 16% in after-hours trading on Wednesday, rising from about $1,049 to about $1,215.

- Mention ID：`mention:26162eae8902778f75fc614c06f6bb06cf2eaab26b2da06c7012ccb3cc518fdd`
- 来源：[Micron Just Guided for a Staggering $50 Billion in Fiscal Q4 Revenue. Here's What It Means for the AI Trade.](https://www.fool.com/investing/2026/06/24/micron-just-guided-for-a-staggering-50-billion-in/)；`doxatlas:raw_media:c762d097-1190-4ea7-9802-5d44fadefd9f`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Micron Technology (SUBJECT)
- 数量：about 16% [price_change_percent]；about $1,049 [stock_price_start]；about $1,215 [stock_price_end]
- 时间：2026-06-24T00:00:00 / 2026-06-24T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Shares of memory specialist Micron Technology (MU 5.68%) jumped about 16% in after-hours trading on Wednesday, climbing from about $1,049 at Wednesday's close to about $1,215

##### M03. Micron Technology's market capitalization exceeded $1.2 trillion.

- Mention ID：`mention:a2c6cccb8e09811b781d7b8aa6023bd1071560119972038693d2f988500f3c49`
- 来源：[Micron Just Guided for a Staggering $50 Billion in Fiscal Q4 Revenue. Here's What It Means for the AI Trade.](https://www.fool.com/investing/2026/06/24/micron-just-guided-for-a-staggering-50-billion-in/)；`doxatlas:raw_media:c762d097-1190-4ea7-9802-5d44fadefd9f`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Micron (SUBJECT)
- 数量：$1.2 trillion [market_capitalization]
- 时间：2026-06-24T00:00:00 / 2026-06-24T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：it puts Micron above a $1.2 trillion market capitalization.

##### M04. Micron shares closed at $1,048.51 on June 24, 2026, and rose to $1,213.96 in after-hours trading.

- Mention ID：`mention:0dab08b4e3c69a8c7b7d7dc7cea7a72a41711df71e49e03c80d2ec0c456370be`
- 来源：[Micron CEO Expects Memory Supply To Improve Gradually In 2028, But There's No 'Line Of Sight' When It Will Catch Up To Rising AI Demand](https://www.benzinga.com/markets/tech/26/06/60089761/micron-ceo-sees-memory-supply-improving-by-2028-but-no-clear-end-in-sight-to-ai-demand-shortfallmicron-ceo-sees-memory-supply-improving-by-2028-but-no-clear-end-in-sight-to-ai-demand-shortfall)；`doxatlas:raw_media:25387f96-b423-437c-b2cd-8652aa176bd8`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Micron (SUBJECT)
- 数量：$1,048.51 [closing_price]；$1,213.96 [after_hours_price]
- 时间：2026-06-24T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Price Action: Micron shares closed down 0.31% at $1,048.51 on Wednesday but surged 15.78% to $1,213.96 in after-hours trading, according to Benzinga Pro.

##### M05. Micron's stock price increased by 15% after hours on Wednesday.

- Mention ID：`mention:fdad20e650f8b7375ff3e78b7800fb2625e2151fa35325498e34cdfa9e92959d`
- 来源：[4 Blowout Numbers From Micron's Earnings Investors Need To See](https://www.fool.com/investing/2026/06/25/x-blowout-numbers-from-microns-earnings-investors/)；`doxatlas:raw_media:34fc2a61-fa6e-4db0-8e39-f862da0af41c`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Micron (SUBJECT)
- 数量：15% [stock_price_change_percent]
- 时间：2026-06-24T00:00:00 / 2026-06-24T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：sending the stock up 15% after hours on Wednesday

##### M06. Micron shares rose more than 15% in after-hours trading to around $1,213.

- Mention ID：`mention:c9a9b539f777708672ad0593cad4e24cbb4f8a35422cfc24c2a959080b8c47d4`
- 来源：[Micron posts record results as AI boom drives 15-fold jump in net profit](https://www.euronews.com/business/2026/06/25/micron-posts-record-results-as-ai-boom-drives-15-fold-jump-in-net-profit)；`doxatlas:raw_media:2b5bba2a-e472-4a1f-a341-5be6e4286dd1`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Micron shares (SUBJECT)
- 数量：more than 15% [share_price_change_percent]；around $1,213 [share_price]
- 时间：2026-06-24T00:00:00 / 2026-06-24T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron shares rose more than 15% in after-hours trading to around $1,213, leaving the company valued at roughly $1.16 trillion (€1tn).

##### M07. Micron Technology's stock rose more than 16% in pre-market trade on Thursday.

- Mention ID：`mention:0911a195782f6a0629aa57abf8153d6d1b74f9fa3f604f2a832686774211e081`
- 来源：[Investors bet on AI again after Micron reports 346% sales jump](https://edition.cnn.com/2026/06/25/business/micron-results-ai-stocks-volatility)；`doxatlas:raw_media:1aaec309-8ccb-42d6-b974-b4aa3bf2d43c`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Micron Technology (SUBJECT)
- 数量：more than 16% [stock_change]
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：sending its stock up by more than 16% in pre-market trade Thursday.

##### M08. Micron stock price surges to new highs.

- Mention ID：`mention:ae62830db62f03cfb6168dcaadef92c7fefe92fcc4ecd84d3e3a59a63308b2ae`
- 来源：[Micron surges to new highs on blockbuster earnings: Jim Lebenthal buys the stock](https://finnhub.io/api/news?id=760aa3bb75cb55185d1ad9b8997324a47603aeac7746264f37945ce55241212a)；`doxatlas:raw_media:6e04e5e6-801c-4141-ac0f-81d29029b012`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Micron (SUBJECT)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `title:0`：Micron surges to new highs on blockbuster earnings

##### M09. Micron shares surged more than 15% to a record high of around $1,208 on June 25, 2026.

- Mention ID：`mention:2846e0c9d29ced6e4bf520e37406cab33aecdac56f17b2052da8401587438a6d`
- 来源：[Micron shares surge to all-time high as Wall Street cheers unprecedented contract visibility](https://www.proactiveinvestors.com/companies/news/1094520/micron-shares-surge-to-all-time-high-as-wall-street-cheers-unprecedented-contract-visibility-1094520.html)；`doxatlas:raw_media:6b5d042a-9236-453b-8b60-2ba6c2f16b4f`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Micron Technology Inc (SUBJECT)
- 数量：around $1,208 [share_price]；more than 15% [price_change_percent]
- 时间：2026-06-25T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron Technology Inc (NASDAQ:MU) shares soared more than 15% to a record high of around $1,208 Thursday

##### M10. Micron stock rose 16%.

- Mention ID：`mention:c6f31379190292e7862ae6f21c89297d1d5a9b618844cc63951e2c7ac90b5c6f`
- 来源：[Micron Just Locked In $100 Billion in Sales, and Wall Street Thinks the Boom-Bust Chip Cycle Is Dead](https://247wallst.com/investing/2026/06/25/micron-just-locked-in-100-billion-in-sales-and-wall-street-thinks-the-boom-bust-chip-cycle-is-dead/)；`doxatlas:raw_media:1e515897-64d4-4896-b936-10a04501eff4`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Micron (SUBJECT)
- 数量：16% [stock_price_change_percent]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：, and the stock was up 16% because, in her words, “Memory has always been just been boom and then bust. But Micron is essentially telling everyone that’s completely over.”

##### M11. Micron stock soared nearly 16%.

- Mention ID：`mention:6a5954eda5d77da4783f95295bb72a2aed433ea7007e210e2a765494c6015987`
- 来源：[Stock Market Today, June 25: Apple Drops After Raising Device Prices to Offset Higher Memory Costs](https://www.fool.com/coverage/stock-market-today/2026/06/25/stock-market-today-june-25-apple-drops-after-raising-device-prices-to-offset-higher-memory-costs/)；`doxatlas:raw_media:d9404d58-cd88-4ccc-a9d2-ec4004629dbc`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Micron (SUBJECT)
- 数量：nearly 16% [price_change_percent]
- 时间：2026-06-25T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron stock soared nearly 16% after blockbuster earnings

#### P63-A07. Micron reported a quarterly gross margin of 84.6%, exceeding its guidance of 81%.

- Atomic ID：`atomic:486603c43fac84556963ed2a`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- Mention 数：10；Version：10
- 时间：third-quarter / UNKNOWN
- **人工审计：需拆分。** 该 Atomic 产生 23 个 FP pair；建议分区：adjusted_or_unspecified_gross_margin, gaap_gross_margin, net_income

##### M01. Micron reported a quarterly gross margin of 84.6%, exceeding its guidance of 81%.

- Mention ID：`mention:361f285a1c829b66a1497fde6cd41faf2dbe1b57284a1dc0046b1270b730acab`
- 来源：[4 Blowout Numbers From Micron's Earnings Investors Need To See](https://www.fool.com/investing/2026/06/25/x-blowout-numbers-from-microns-earnings-investors/)；`doxatlas:raw_media:34fc2a61-fa6e-4db0-8e39-f862da0af41c`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：84.6% [gross_margin]
- 时间：third-quarter / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron's gross margin came in at 84.6% in the quarter, ahead of its own guidance at 81%

##### M02. Micron reported quarterly net income of $28.2 billion.

- Mention ID：`mention:9cc917df83c37a650ad8500d38ea17b2adc9dafaf7f24ab4b6974756b818645a`
- 来源：[4 Blowout Numbers From Micron's Earnings Investors Need To See](https://www.fool.com/investing/2026/06/25/x-blowout-numbers-from-microns-earnings-investors/)；`doxatlas:raw_media:34fc2a61-fa6e-4db0-8e39-f862da0af41c`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：$28.2 billion [net_income]
- 时间：third-quarter / UNKNOWN
- Evidence 状态：`TEXT_NOT_FOUND`
  - E01 `TEXT_NOT_FOUND` / `text:0`：Micron produced $28.2 billion in net income.

##### M03. Micron reported a gross margin of around 85% for the quarter.

- Mention ID：`mention:3066f2a7a7858bb60f2640e4af861a5b1991a8e0f3428ba7fd9777ad3a137ece`
- 来源：[Micron posts record results as AI boom drives 15-fold jump in net profit](https://www.euronews.com/business/2026/06/25/micron-posts-record-results-as-ai-boom-drives-15-fold-jump-in-net-profit)；`doxatlas:raw_media:2b5bba2a-e472-4a1f-a341-5be6e4286dd1`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron Technology (ACTOR)
- 数量：around 85% [gross_margin]
- 时间：third quarter / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：The company reported a gross margin of around 85% for the quarter, a level that now rivals or exceeds those of far larger technology names such as Nvidia and Meta

##### M04. Micron Technology reported gross margin of 84.9%.

- Mention ID：`mention:186022491f08a35da230d9058e427983d8fdd53d7b74f8d0bd1063176b7eca39`
- 来源：[Wedbush says chip rally has further to run after Micron's blowout quarter](https://www.proactiveinvestors.com/companies/news/1094490/wedbush-says-chip-rally-has-further-to-run-after-micron-s-blowout-quarter-1094490.html)；`doxatlas:raw_media:79fe1f85-a1cc-44e1-928b-d0256c075eb4`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron Technology Inc (ACTOR)
- 数量：84.9% [gross_margin]
- 时间：third-quarter / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：while gross margin of 84.9% beat expectations.

##### M05. Micron Technology reported gross margin of more than 84%.

- Mention ID：`mention:6d650169b35c648dece9d76018c657ba01bb11163fc1dd00c8d7450be3d20a95`
- 来源：[Is Micron a Buy After Its Blowout Earnings Report?](https://www.fool.com/investing/2026/06/25/is-micron-a-buy-after-its-blowout-earnings-report/)；`doxatlas:raw_media:a424d484-3e57-47ed-b21d-50aeceafe593`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron Technology (ACTOR)
- 数量：more than 84% [gross_margin]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：gross margin came in at more than 84%.

##### M06. Micron reported a record gross margin of 84.9%.

- Mention ID：`mention:d2bce0f883a2617d38dda3efdc72e14b9e7ce94155208c55538e7d07c1ca9d26`
- 来源：[Why Micron's blowout earnings are a headache for Apple](https://finance.yahoo.com/markets/article/why-microns-blowout-earnings-are-a-headache-for-apple-161932441.html)；`doxatlas:raw_media:6ccb4979-525c-4467-a381-538f38a4fa9c`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：84.9% [gross_margin]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：delivered a record gross margin of 84.9%

##### M07. Micron reported gross margin of 84.9%.

- Mention ID：`mention:c4a84a40f0ce31016faf8a9d4e4e655f8f15ea7567effc52a0b6fdfd580734f8`
- 来源：[Micron shares surge to all-time high as Wall Street cheers unprecedented contract visibility](https://www.proactiveinvestors.com/companies/news/1094520/micron-shares-surge-to-all-time-high-as-wall-street-cheers-unprecedented-contract-visibility-1094520.html)；`doxatlas:raw_media:6b5d042a-9236-453b-8b60-2ba6c2f16b4f`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：84.9% [gross_margin]
- 时间：fiscal third-quarter / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Gross margin came in at 84.9%, topping consensus of 81.7%

##### M08. Micron reported GAAP gross margin of 84.6% for fiscal Q3.

- Mention ID：`mention:78464e1f0ec5fc64a14f1bc2fdd560f2f439ea1696223b07070f60a3c9d27154`
- 来源：[Micron Just Locked In $100 Billion in Sales, and Wall Street Thinks the Boom-Bust Chip Cycle Is Dead](https://247wallst.com/investing/2026/06/25/micron-just-locked-in-100-billion-in-sales-and-wall-street-thinks-the-boom-bust-chip-cycle-is-dead/)；`doxatlas:raw_media:1e515897-64d4-4896-b936-10a04501eff4`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron (SUBJECT)
- 数量：84.6% [gaap_gross_margin]
- 时间：fiscal Q3 / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：GAAP gross margin was 84.6%, against 37.7% a year earlier.

##### M09. Micron reported gross margins of 84.9% in its most recent quarter.

- Mention ID：`mention:20aad98ff070dfe7d253bd6901b7d088a123862032f7dd4938a2f773742c8992`
- 来源：[Tim Cook Calls the Memory Crisis a "Hundred-Year Flood"](https://finance.yahoo.com/technology/articles/tim-cook-calls-memory-crisis-170140774.html)；`doxatlas:raw_media:d42584c3-2d2d-4559-affc-ccae5b388ce1`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：84.9% [gross_margin]
- 时间：most recent quarter / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron (NASDAQ:MU) reported gross margins of 84.9% in its most recent quarter, up from 39% a year ago,

##### M10. Micron's adjusted gross margins reached 84.9% in fiscal Q3.

- Mention ID：`mention:db607da3a281e68a7fbd11cb50c7f183092c72bea7998fd24abae134d2682ee2`
- 来源：[MU Stock Soars as Micron’s Revenue More Than Quadrupled in Q3](https://www.barchart.com/story/news/3092/mu-stock-soars-as-microns-revenue-more-than-quadrupled-in-q3)；`doxatlas:raw_media:181db9d6-5959-4fb0-947e-3c6680861566`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron (SUBJECT)
- 数量：84.9% [adjusted_gross_margin]
- 时间：fiscal Q3 / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：An insatiable demand for Micron’s high-bandwidth memory (HBM) chips that power artificial intelligence (AI) data centers drove its adjusted gross margins to a whopping 84.9% in fiscal Q3.

#### P63-A08. Micron expects quarterly capital expenditures in fiscal 2027 to exceed fiscal fourth-quarter 2026 levels, with more than half of the increase coming from construction spending.

- Atomic ID：`atomic:487518cb8fa7b021d7fcebe2`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- Mention 数：2；Version：2
- 时间：fiscal 2027 / UNKNOWN
- **人工审计：需拆分。** 该 Atomic 产生 0 个 FP pair

##### M01. Micron expects quarterly capital expenditures in fiscal 2027 to exceed fiscal fourth-quarter 2026 levels, with more than half of the increase coming from construction spending.

- Mention ID：`mention:5bf077b106edbdce423fffbcc47a5c64adc96931e68e8f6759a1a0c5cacd9cf1`
- 来源：[Micron Is Spending Billions On New Fabs—And Still Says It'll Return 100% Of Excess Cash To Shareholders](https://www.benzinga.com/trading-ideas/long-ideas/26/06/60095272/micron-is-spending-billions-on-new-fabs-and-still-says-itll-return-100-of-excess-cash-to-shareholders)；`doxatlas:raw_media:4fcc4fb0-ab7d-4157-8e65-a09ef40a7581`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- 参与者：Micron (ACTOR)
- 数量：—
- 时间：fiscal 2027 / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Chief Financial Officer Mark Murphy said the company expects quarterly capital expenditures in fiscal 2027 to exceed fiscal fourth-quarter levels as Micron accelerates construction of new clean-room capacity to meet long-term AI demand.
  - E02 `VERIFIED` / `text:0`：“We expect quarterly CAPEX in fiscal 2027 to be above fiscal Q4 levels,” Murphy said, adding that more than half of the increase next year will come from construction spending

##### M02. Micron signaled meaningfully higher capital expenditure spending in 2027.

- Mention ID：`mention:90fc1a32727f3c8f38d08dd2c98844e093f307467e44781933f8ed5a8aa4f658`
- 来源：[Micron shares surge to all-time high as Wall Street cheers unprecedented contract visibility](https://www.proactiveinvestors.com/companies/news/1094520/micron-shares-surge-to-all-time-high-as-wall-street-cheers-unprecedented-contract-visibility-1094520.html)；`doxatlas:raw_media:6b5d042a-9236-453b-8b60-2ba6c2f16b4f`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- 参与者：Micron (ACTOR)
- 数量：—
- 时间：2027 / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：signalled meaningfully higher spending in 2027

#### P63-A09. Micron Technology reported earnings per share of $25.11.

- Atomic ID：`atomic:58553e037897096c35056f8e`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- Mention 数：6；Version：6
- 时间：third-quarter / UNKNOWN
- **人工审计：需拆分。** 该 Atomic 产生 5 个 FP pair；建议分区：eps, revenue_record

##### M01. Micron Technology reported earnings per share of $25.11.

- Mention ID：`mention:bc790b0408cfb2d332a7702147624e1c101d6b0794106775873320fad59c2955`
- 来源：[Wedbush says chip rally has further to run after Micron's blowout quarter](https://www.proactiveinvestors.com/companies/news/1094490/wedbush-says-chip-rally-has-further-to-run-after-micron-s-blowout-quarter-1094490.html)；`doxatlas:raw_media:79fe1f85-a1cc-44e1-928b-d0256c075eb4`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron Technology Inc (ACTOR)
- 数量：$25.11 [earnings_per_share]
- 时间：third-quarter / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Earnings per share came in at $25.11, more than double the previous quarter and ahead of the $20.86 consensus

##### M02. Micron reported third-quarter earnings per share of $25.11, beating Wall Street consensus estimates by $4.72.

- Mention ID：`mention:94f6b1e4e4ff0a535a08ac49d307c00cf1a95c363234611eb0a6d5b29741f8df`
- 来源：[Why Everyone Is Talking About Micron](https://www.fool.com/investing/2026/06/25/why-everyone-is-talking-about-micron/)；`doxatlas:raw_media:49fb27c5-84b4-4e56-8791-f7ac23de38b7`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：$25.11 [earnings_per_share]；$4.72 [beat_estimate]
- 时间：third-quarter / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Earnings per share of $25.11 beat Wall Street consensus estimates by $4.72.

##### M03. Micron exceeded Wall Street earnings estimates.

- Mention ID：`mention:8feaeb210bc41cc56c8f51e05fddd23ab3119010ad170cbf56235e85c65c6f28`
- 来源：[Why Micron's blowout earnings are a headache for Apple](https://finance.yahoo.com/markets/article/why-microns-blowout-earnings-are-a-headache-for-apple-161932441.html)；`doxatlas:raw_media:6ccb4979-525c-4467-a381-538f38a4fa9c`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron topped Wall Street's estimates

##### M04. Micron reported record revenue.

- Mention ID：`mention:c5c2ca7230a3e1b46f537d6b8cb67c216044c3122a3ab62776cef2189bb793da`
- 来源：[Why Micron's blowout earnings are a headache for Apple](https://finance.yahoo.com/markets/article/why-microns-blowout-earnings-are-a-headache-for-apple-161932441.html)；`doxatlas:raw_media:6ccb4979-525c-4467-a381-538f38a4fa9c`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：posted record revenue

##### M05. Micron reported non-GAAP earnings per share of $25.11.

- Mention ID：`mention:ad9f92f0755be83251a26169e88e27b6e96ec387d7e359809644c00621f99513`
- 来源：[Micron shares surge to all-time high as Wall Street cheers unprecedented contract visibility](https://www.proactiveinvestors.com/companies/news/1094520/micron-shares-surge-to-all-time-high-as-wall-street-cheers-unprecedented-contract-visibility-1094520.html)；`doxatlas:raw_media:6b5d042a-9236-453b-8b60-2ba6c2f16b4f`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：$25.11 [non_gaap_eps]
- 时间：fiscal third-quarter / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：non-GAAP earnings per share of $25.11 doubled quarter-over-quarter and surpassed expectations of $20.86.

##### M06. Micron reported non-GAAP EPS of $25.11 for fiscal Q3.

- Mention ID：`mention:d2766166393f3f8cd91b1918829e3abfdfbdafe6c64ae0b1b3d1fe1918f08dec`
- 来源：[Micron Just Locked In $100 Billion in Sales, and Wall Street Thinks the Boom-Bust Chip Cycle Is Dead](https://247wallst.com/investing/2026/06/25/micron-just-locked-in-100-billion-in-sales-and-wall-street-thinks-the-boom-bust-chip-cycle-is-dead/)；`doxatlas:raw_media:1e515897-64d4-4896-b936-10a04501eff4`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron (SUBJECT)
- 数量：$25.11 [non_gaap_eps]
- 时间：fiscal Q3 / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Non-GAAP EPS landed at $25.11 against a $20.2843 consensus, the seventh consecutive quarter of beating Wall Street.

#### P63-A10. Micron plans to return 100% of its excess cash to shareholders beginning December 9, 2026.

- Atomic ID：`atomic:61a33de5f020fe092e8f56eb`
- Family / Assertion：`TRANSACTION_CAPITAL` / `PLANNED`
- Mention 数：4；Version：4
- 时间：2026-12-01T00:00:00 / DAY
- **人工审计：需拆分。** 该 Atomic 产生 0 个 FP pair

##### M01. Micron plans to return 100% of its excess cash to shareholders beginning December 9, 2026.

- Mention ID：`mention:cc18cd3637ab9d251f3d25cd51c8843ee7b34e6cf901b8daf197b8eade151a48`
- 来源：[Micron Is Spending Billions On New Fabs—And Still Says It'll Return 100% Of Excess Cash To Shareholders](https://www.benzinga.com/trading-ideas/long-ideas/26/06/60095272/micron-is-spending-billions-on-new-fabs-and-still-says-itll-return-100-of-excess-cash-to-shareholders)；`doxatlas:raw_media:4fcc4fb0-ab7d-4157-8e65-a09ef40a7581`
- Family / Assertion：`TRANSACTION_CAPITAL` / `PLANNED`
- 参与者：Micron (ACTOR)；shareholders (TARGET)
- 数量：100% [excess_cash_return_pct]
- 时间：2026-12-09T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Murphy said that beginning Dec. 9, 2026—the second anniversary of the company’s definitive CHIPS Act agreements—Micron plans to increase capital returns over time. “We expect to return 100% of our excess cash to shareholders,”

##### M02. Micron Technology intends to return all excess cash to shareholders.

- Mention ID：`mention:436ca4300203d84334d8eecfd8d4c14e479b48a0f5801bdff6f3a1418bc8c4d5`
- 来源：[Wedbush says chip rally has further to run after Micron's blowout quarter](https://www.proactiveinvestors.com/companies/news/1094490/wedbush-says-chip-rally-has-further-to-run-after-micron-s-blowout-quarter-1094490.html)；`doxatlas:raw_media:79fe1f85-a1cc-44e1-928b-d0256c075eb4`
- Family / Assertion：`TRANSACTION_CAPITAL` / `PLANNED`
- 参与者：Micron Technology Inc (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron also said it intends to return all excess cash to shareholders as free cash flow builds.

##### M03. Micron announced plans to return 100% of excess free cash flow to shareholders beginning in December 2026.

- Mention ID：`mention:b9ca946a442fea236459efc7b0e0dc5614f079fafc5c52480191193950f8842c`
- 来源：[Micron shares surge to all-time high as Wall Street cheers unprecedented contract visibility](https://www.proactiveinvestors.com/companies/news/1094520/micron-shares-surge-to-all-time-high-as-wall-street-cheers-unprecedented-contract-visibility-1094520.html)；`doxatlas:raw_media:6b5d042a-9236-453b-8b60-2ba6c2f16b4f`
- Family / Assertion：`TRANSACTION_CAPITAL` / `PLANNED`
- 参与者：Micron (ACTOR)；shareholders (TARGET)
- 数量：100% [excess_free_cash_flow_return_percentage]
- 时间：2026-12-01T00:00:00 / MONTH
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron announced plans to return 100% of excess free cash flow to shareholders beginning in December, once CHIPS Act restrictions on certain uses of cash expire.

##### M04. Micron management committed to returning 100% of excess cash to shareholders, increasing from the prior 50%.

- Mention ID：`mention:95c6495b4dab48d41d8e5a6eee14c0d41d9051e552187cc2aede5670ccc5e886`
- 来源：[MU Stock Soars as Micron’s Revenue More Than Quadrupled in Q3](https://www.barchart.com/story/news/3092/mu-stock-soars-as-microns-revenue-more-than-quadrupled-in-q3)；`doxatlas:raw_media:181db9d6-5959-4fb0-947e-3c6680861566`
- Family / Assertion：`GOVERNANCE_PERSONNEL` / `ACTUAL`
- 参与者：Micron management (ACTOR)
- 数量：100% [excess_cash_return_rate]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Management has also committed to returning 100% excess cash to shareholders moving forward, up from prior 50%

#### P63-A11. Micron issues forward guidance regarding demand for AI memory products.

- Atomic ID：`atomic:62d95926796043b7066a4b79`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- Mention 数：1；Version：1
- 时间：UNKNOWN

##### M01. Micron issues forward guidance regarding demand for AI memory products.

- Mention ID：`mention:f4950155495a39f674da29adb8ca8e2daf5b81f3b545f28de5a6f6b0030cf719`
- 来源：[The AI Memory Trade Is Heating Up: Defiance Launches 2X DRAM ETF After Micron's Blowout Quarter](https://www.benzinga.com/etfs/new-etfs/26/06/60103609/the-ai-memory-trade-is-heating-up-defiance-launches-2x-dram-etf-after-microns-blowout-quarter)；`doxatlas:raw_media:238a879b-e686-457d-b83d-5b0405bd28c6`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- 参与者：The company (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：The company also issued strong forward guidance, signaling that demand for AI memory products remains robust.

#### P63-A12. Micron declared a quarterly dividend of 15 cents per share, payable on July 21, 2026, to shareholders of record on July 6, 2026.

- Atomic ID：`atomic:706b87b43593c22b274c51a4`
- Family / Assertion：`TRANSACTION_CAPITAL` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：2026-06-24T00:00:00 / DAY

##### M01. Micron declared a quarterly dividend of 15 cents per share, payable on July 21, 2026, to shareholders of record on July 6, 2026.

- Mention ID：`mention:73bcd3e1946f7f4fa2ac7be409e44a21d7d69a8da63e121b3bf9177c92daf43e`
- 来源：[Micron CEO Expects Memory Supply To Improve Gradually In 2028, But There's No 'Line Of Sight' When It Will Catch Up To Rising AI Demand](https://www.benzinga.com/markets/tech/26/06/60089761/micron-ceo-sees-memory-supply-improving-by-2028-but-no-clear-end-in-sight-to-ai-demand-shortfallmicron-ceo-sees-memory-supply-improving-by-2028-but-no-clear-end-in-sight-to-ai-demand-shortfall)；`doxatlas:raw_media:25387f96-b423-437c-b2cd-8652aa176bd8`
- Family / Assertion：`TRANSACTION_CAPITAL` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：15 cents per share [dividend_per_share]
- 时间：2026-06-24T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron also declared a quarterly dividend of 15 cents per share, payable July 21 to shareholders of record on July 6.

#### P63-A13. Micron is expanding manufacturing capacity including leading-edge DRAM fabs in Idaho and New York, continued expansion in Taiwan and Singapore, and additional advanced packaging capacity for HBM products.

- Atomic ID：`atomic:8efb7edb15798704261ded31`
- Family / Assertion：`PRODUCTION_SUPPLY` / `ONGOING`
- Mention 数：1；Version：1
- 时间：UNKNOWN

##### M01. Micron is expanding manufacturing capacity including leading-edge DRAM fabs in Idaho and New York, continued expansion in Taiwan and Singapore, and additional advanced packaging capacity for HBM products.

- Mention ID：`mention:6a72c543e0751c049cb13cab6050b817b3ab88ba20e6d82124cdf555c655ec12`
- 来源：[Micron Is Spending Billions On New Fabs—And Still Says It'll Return 100% Of Excess Cash To Shareholders](https://www.benzinga.com/trading-ideas/long-ideas/26/06/60095272/micron-is-spending-billions-on-new-fabs-and-still-says-itll-return-100-of-excess-cash-to-shareholders)；`doxatlas:raw_media:4fcc4fb0-ab7d-4157-8e65-a09ef40a7581`
- Family / Assertion：`PRODUCTION_SUPPLY` / `ONGOING`
- 参与者：Micron (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：The investments include leading-edge DRAM fabs in Idaho and New York, continued expansion in Taiwan and Singapore, and additional advanced packaging capacity aimed at supporting next-generation high-bandwidth memory (HBM) products

#### P63-A14. Micron's multi-year Strategic Customer Agreements will significantly enhance the durability and predictability of its financial performance.

- Atomic ID：`atomic:97af10afbc6c138125f0b75b`
- Family / Assertion：`COMMERCIAL_OPERATION` / `EXPECTED`
- Mention 数：1；Version：1
- 时间：UNKNOWN

##### M01. Micron's multi-year Strategic Customer Agreements will significantly enhance the durability and predictability of its financial performance.

- Mention ID：`mention:c2938c9390637838dc2e28e02fde8a0f4540c5ee37ce8fe9ad5c88749cd0a980`
- 来源：[These Analysts Boost Their Forecasts On Micron Technology After Better-Than-Expected Q3 Results](https://www.benzinga.com/analyst-stock-ratings/price-target/26/06/60100007/these-analysts-boost-their-forecasts-on-micron-technology-after-better-than-expected-q3-results)；`doxatlas:raw_media:5d890b9d-c789-4a99-87d6-8a34a3ea4cc2`
- Family / Assertion：`COMMERCIAL_OPERATION` / `EXPECTED`
- 参与者：Micron (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：We believe our multi-year Strategic Customer Agreements will significantly enhance the durability and predictability of Micron’s strong financial performance

#### P63-A15. Micron provided financial guidance projecting $50 billion in revenue and $31 in EPS.

- Atomic ID：`atomic:a464025a309b1932b5ce9192`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- Mention 数：15；Version：15
- 时间：fiscal Q4 2026 / UNKNOWN
- **人工审计：需拆分。** 该 Atomic 产生 49 个 FP pair；建议分区：q4_revenue_guidance, q4_gross_margin_guidance, q4_eps_guidance, fy2026_capex_plan

##### M01. Micron provided financial guidance projecting $50 billion in revenue and $31 in EPS.

- Mention ID：`mention:7087fad96e276ab8124c92c49a81c0d8647d5b94c090290f1cc90f7cc625f410`
- 来源：[SanDisk, Western Digital, Seagate Surge After Hours As Micron's Blowout Earnings Signal Memory Upcycle](https://www.benzinga.com/markets/equities/26/06/60088616/sandisk-western-digital-and-seagate-surge-after-microns-blowout-earnings-signal-memory-upcycle)；`doxatlas:raw_media:6389d63c-442c-4c9b-9ba5-b943b7e91804`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- 参与者：Micron (ACTOR)
- 数量：$50 billion in revenue [revenue]；$31 EPS [eps]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：The guidance of $50 billion in revenue and $31 EPS both stand well ahead of analyst estimates.

##### M02. Micron Technology provided guidance for fiscal fourth quarter 2026 gross margin of about 86% and adjusted earnings per share of $31.00, plus or minus $1.00.

- Mention ID：`mention:67a49018c452e065d0e1ab9f6c73fe5405561726e006a457fd8e2e71da1d5c88`
- 来源：[Micron Just Guided for a Staggering $50 Billion in Fiscal Q4 Revenue. Here's What It Means for the AI Trade.](https://www.fool.com/investing/2026/06/24/micron-just-guided-for-a-staggering-50-billion-in/)；`doxatlas:raw_media:c762d097-1190-4ea7-9802-5d44fadefd9f`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- 参与者：Micron (ACTOR)
- 数量：about 86% [gross_margin_guidance]；$31.00 [eps_guidance]；plus or minus $1.00 [eps_guidance_range]
- 时间：fiscal Q4 2026 / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Management said its fiscal fourth-quarter gross margin is expected to climb to about 86%. Additionally, it guided for adjusted earnings per share of $31.00, plus or minus $1.00, for the period.

##### M03. Micron Technology provided guidance for fiscal fourth quarter 2026 revenue of $50 billion, plus or minus $1 billion.

- Mention ID：`mention:833a705bd6b49ab9a59914e567e4b0e963677f7afffdba01f8430d70bf57430c`
- 来源：[Micron Just Guided for a Staggering $50 Billion in Fiscal Q4 Revenue. Here's What It Means for the AI Trade.](https://www.fool.com/investing/2026/06/24/micron-just-guided-for-a-staggering-50-billion-in/)；`doxatlas:raw_media:c762d097-1190-4ea7-9802-5d44fadefd9f`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- 参与者：Micron (ACTOR)
- 数量：$50 billion [revenue_guidance]；plus or minus $1 billion [guidance_range]
- 时间：fiscal Q4 2026 / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron guided fiscal Q4 revenue to $50 billion, plus or minus $1 billion

##### M04. Micron forecast fiscal fourth-quarter 2026 revenue of $50 billion (plus or minus $1 billion) and adjusted earnings of $31 per share (plus or minus $1).

- Mention ID：`mention:f8b393b27673c5238bae0de07295fa6f2d36e0c9c79f37568e61771b03db1e05`
- 来源：[Micron CEO Expects Memory Supply To Improve Gradually In 2028, But There's No 'Line Of Sight' When It Will Catch Up To Rising AI Demand](https://www.benzinga.com/markets/tech/26/06/60089761/micron-ceo-sees-memory-supply-improving-by-2028-but-no-clear-end-in-sight-to-ai-demand-shortfallmicron-ceo-sees-memory-supply-improving-by-2028-but-no-clear-end-in-sight-to-ai-demand-shortfall)；`doxatlas:raw_media:25387f96-b423-437c-b2cd-8652aa176bd8`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- 参与者：Micron (ACTOR)
- 数量：$50 billion [revenue_guidance]；$31 per share [adjusted_earnings_per_share_guidance]
- 时间：fiscal fourth-quarter 2026 / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：For the fourth quarter, the company forecast $50 billion in revenue, plus or minus $1 billion, and adjusted earnings of $31 per share, plus or minus $1, both above consensus estimates of $42.95 billion and $25.50 per share.

##### M05. Micron guided for fourth-quarter revenue to reach $50 billion.

- Mention ID：`mention:27e54db1904ece5af881caaab2b494db3b017f99a12e8d80341aa90683634f37`
- 来源：[4 Blowout Numbers From Micron's Earnings Investors Need To See](https://www.fool.com/investing/2026/06/25/x-blowout-numbers-from-microns-earnings-investors/)；`doxatlas:raw_media:34fc2a61-fa6e-4db0-8e39-f862da0af41c`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- 参与者：Micron (ACTOR)
- 数量：$50 billion [revenue_guidance]
- 时间：fourth quarter / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron's guidance called for similar growth in the fourth quarter, with revenue expected to reach $50 billion.

##### M06. Micron guided for a future gross margin of 86%.

- Mention ID：`mention:ed187438491633cee1565b16f101e4e722c2dfec85d98accc4c7aadeb0510378`
- 来源：[4 Blowout Numbers From Micron's Earnings Investors Need To See](https://www.fool.com/investing/2026/06/25/x-blowout-numbers-from-microns-earnings-investors/)；`doxatlas:raw_media:34fc2a61-fa6e-4db0-8e39-f862da0af41c`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- 参与者：Micron (ACTOR)
- 数量：86% [gross_margin_guidance]
- 时间：fourth quarter / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：management guided to a gross margin of 86%

##### M07. Micron provided fourth-quarter guidance for $50 billion in revenue and $31 per share in earnings.

- Mention ID：`mention:c73aaef99e934b601abcc501cd5d223650fd89d1a0ebbfeb684e98b8c752f64f`
- 来源：[Micron CEO Says AI Memory Shortage Could Last Beyond 2028: These ETFs Are Positioned To Profit](https://www.benzinga.com/etfs/sector-etfs/26/06/60094351/micron-ceo-says-ai-memory-shortage-could-last-beyond-2028-these-etfs-are-positioned-to-profit)；`doxatlas:raw_media:bf5258ec-37f8-408b-ace2-8b016d16f725`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：$50 billion in revenue [revenue_guidance]；$31 per share in earnings [earnings_per_share_guidance]
- 时间：Fourth-quarter / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Fourth-quarter guidance for $50 billion in revenue and - $31 per share in earnings came in significantly above consensus forecasts.

##### M08. Micron lifted planned capital spending to about $27 billion for this fiscal year.

- Mention ID：`mention:4047216c3fea8e048fb6bec389a502f9a669c034ee034eaea25a6691c425c2b8`
- 来源：[Micron posts record results as AI boom drives 15-fold jump in net profit](https://www.euronews.com/business/2026/06/25/micron-posts-record-results-as-ai-boom-drives-15-fold-jump-in-net-profit)；`doxatlas:raw_media:2b5bba2a-e472-4a1f-a341-5be6e4286dd1`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `PLANNED`
- 参与者：Micron Technology (ACTOR)
- 数量：about $27 billion [capital_spending]
- 时间：this fiscal year / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：It is ramping up investment to match, lifting planned capital spending to about $27 billion (€23.7bn) this fiscal year and signalling a further jump in 2027, management told analysts during the earnings call.

##### M09. Micron expects revenue of around $50 billion and adjusted earnings of roughly $31 per share for the current quarter.

- Mention ID：`mention:f3f68ef7dbd634119f389dce260292c3d04343e69326d8ae98832a60b7c1cc62`
- 来源：[Micron posts record results as AI boom drives 15-fold jump in net profit](https://www.euronews.com/business/2026/06/25/micron-posts-record-results-as-ai-boom-drives-15-fold-jump-in-net-profit)；`doxatlas:raw_media:2b5bba2a-e472-4a1f-a341-5be6e4286dd1`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- 参与者：Micron Technology (ACTOR)
- 数量：around $50 billion [revenue]；roughly $31 a share [adjusted_earnings_per_share]
- 时间：current quarter / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：The company expects revenue of around $50 billion (€44bn) in the current quarter and adjusted earnings of roughly $31 a share

##### M10. Micron expects fourth-quarter revenue of $50 billion, plus or minus $1 billion.

- Mention ID：`mention:1d90c0a1490b4ba54d6a023548abe1b92e0d9712dd9b739729ef03b3df9214e4`
- 来源：[These Analysts Boost Their Forecasts On Micron Technology After Better-Than-Expected Q3 Results](https://www.benzinga.com/analyst-stock-ratings/price-target/26/06/60100007/these-analysts-boost-their-forecasts-on-micron-technology-after-better-than-expected-q3-results)；`doxatlas:raw_media:5d890b9d-c789-4a99-87d6-8a34a3ea4cc2`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- 参与者：Micron (ACTOR)
- 数量：$50 billion [revenue]
- 时间：Q4 FY2026 / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron expects fourth-quarter revenue of $50 billion, plus or minus $1 billion, versus estimates of $42.95 billion.

##### M11. Micron expects fourth-quarter adjusted earnings of $31 per share, plus or minus $1.

- Mention ID：`mention:7634f7ce211b8ed2b6d52b1eaaa18b106f7eabf7e0e6c2ae9aaa226df809fa4b`
- 来源：[These Analysts Boost Their Forecasts On Micron Technology After Better-Than-Expected Q3 Results](https://www.benzinga.com/analyst-stock-ratings/price-target/26/06/60100007/these-analysts-boost-their-forecasts-on-micron-technology-after-better-than-expected-q3-results)；`doxatlas:raw_media:5d890b9d-c789-4a99-87d6-8a34a3ea4cc2`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- 参与者：Micron (ACTOR)
- 数量：$31 per share [adjusted_earnings_per_share]
- 时间：Q4 FY2026 / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：The company anticipates fourth-quarter adjusted earnings of $31 per share, plus or minus $1, versus estimates of $25.50 per share.

##### M12. Micron Technology forecasted fourth-quarter revenue of $50 billion.

- Mention ID：`mention:08f7252a3459d65a9beb9c6d695913f468618572685123e8783346d73f1135dd`
- 来源：[Wedbush says chip rally has further to run after Micron's blowout quarter](https://www.proactiveinvestors.com/companies/news/1094490/wedbush-says-chip-rally-has-further-to-run-after-micron-s-blowout-quarter-1094490.html)；`doxatlas:raw_media:79fe1f85-a1cc-44e1-928b-d0256c075eb4`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- 参与者：Micron Technology Inc (ACTOR)
- 数量：$50 billion [revenue]
- 时间：fourth-quarter / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron forecasting fourth-quarter revenue of $50 billion, against the $43.6 billion the market had expected.

##### M13. Micron Technology posted a quarterly sales forecast that beat Wall Street expectations.

- Mention ID：`mention:ee01c398619329b92bb0de2a300db85796565d24c9b79d96c8ca8491ce6f9714`
- 来源：[SK Hynix Unveils $29 Billion US Listing as Shares Soar Over 800%](https://finance.yahoo.com/markets/stocks/articles/sk-hynix-unveils-29-billion-135023709.html)；`doxatlas:raw_media:b24ea6b3-557e-4053-9cae-c8e419455b39`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `ACTUAL`
- 参与者：Micron Technology (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Samsung Electronics (SSNLF) also rallied after Micron Technology (NASDAQ:MU) posted a quarterly sales forecast that beat Wall Street expectations

##### M14. Micron guided to $50 billion in revenue for its current quarter.

- Mention ID：`mention:a8b962c9737978a962ee19ddc1cbbca7bee930384eaaba58dfc193005ad82706`
- 来源：[Why Everyone Is Talking About Micron](https://www.fool.com/investing/2026/06/25/why-everyone-is-talking-about-micron/)；`doxatlas:raw_media:49fb27c5-84b4-4e56-8791-f7ac23de38b7`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- 参与者：Micron (ACTOR)
- 数量：$50 billion [revenue_guidance]
- 时间：current quarter / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron is now guiding to $50 billion in revenue for its current quarter.

##### M15. Micron guided fourth-quarter revenue to $50 billion.

- Mention ID：`mention:8288d3800af58814ea63c769b9ce916ffb89ae9055d8168e2e04c5e4dd391290`
- 来源：[Micron shares surge to all-time high as Wall Street cheers unprecedented contract visibility](https://www.proactiveinvestors.com/companies/news/1094520/micron-shares-surge-to-all-time-high-as-wall-street-cheers-unprecedented-contract-visibility-1094520.html)；`doxatlas:raw_media:6b5d042a-9236-453b-8b60-2ba6c2f16b4f`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- 参与者：Micron (ACTOR)
- 数量：$50 billion [revenue]
- 时间：Fourth-quarter / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Fourth-quarter guidance was equally striking, with Micron projecting revenue of $50 billion against the Street's $43.6 billion estimate.

#### P63-A16. Micron projects its balance sheet will strengthen further despite increased investment in technology and capacity.

- Atomic ID：`atomic:b304861b2f214806a4b8b182`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `EXPECTED`
- Mention 数：1；Version：1
- 时间：UNKNOWN

##### M01. Micron projects its balance sheet will strengthen further despite increased investment in technology and capacity.

- Mention ID：`mention:0929b35b2fefbfd3dd20aeeb71c2ce80540b86cb818c461f721f0b1288445c56`
- 来源：[Micron Is Spending Billions On New Fabs—And Still Says It'll Return 100% Of Excess Cash To Shareholders](https://www.benzinga.com/trading-ideas/long-ideas/26/06/60095272/micron-is-spending-billions-on-new-fabs-and-still-says-itll-return-100-of-excess-cash-to-shareholders)；`doxatlas:raw_media:4fcc4fb0-ab7d-4157-8e65-a09ef40a7581`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `EXPECTED`
- 参与者：Micron (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：“Our balance sheet has never been stronger, and we project it to strengthen further even as we increase investment in technology and needed capacity,”

#### P63-A17. Micron Technology reported fiscal Q3 2026 financial results, including revenue of $41.46 billion and adjusted earnings per share of $25.11.

- Atomic ID：`atomic:bb912535671d7379793ea4da`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- Mention 数：33；Version：33
- 时间：fiscal Q3 2026 / 2026-06-24T00:00:00 / DAY
- **人工审计：需拆分。** 该 Atomic 产生 327 个 FP pair；建议分区：total_revenue, eps, cloud_memory_revenue, core_data_center_growth, all_business_units_revenue_trend, gaap_gross_margin, net_income, free_cash_flow, adjusted_gross_margin

##### M01. Micron Technology reported fiscal Q3 2026 financial results, including revenue of $41.46 billion and adjusted earnings per share of $25.11.

- Mention ID：`mention:e7fdbefba6bc0d73302924759b0bf1a6ec8a07de99f5efac465ba1236e5a5841`
- 来源：[KOSPI Spikes 5% on Opening, Riding Micron’s Surprise Earnings](https://beincrypto.com/micron-earnings-kospi-sidecar-chip-rally/)；`doxatlas:raw_media:3f25dec4-7f5f-4203-9e0f-d37c2b06a2c7`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron Technology (ACTOR)
- 数量：$41.46 billion [revenue]；$25.11 [adjusted_eps]
- 时间：fiscal Q3 2026 / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron reported fiscal Q3 2026 revenue of $41.46 billion, more than four times the $9.30 billion it posted in the same quarter a year earlier.
  - E02 `VERIFIED` / `text:0`：Adjusted earnings per share came in at $25.11, well above the analyst consensus of around $20.78.

##### M02. Micron Technology's cloud memory business unit reported fiscal third quarter 2026 revenue of $13.77 billion, up from $3.39 billion a year ago, with an operating margin of 78%.

- Mention ID：`mention:015285929d4c26e5b1a2bba1bed6a2beae193148d7d127220e5feb5d7e268f7c`
- 来源：[Micron Just Guided for a Staggering $50 Billion in Fiscal Q4 Revenue. Here's What It Means for the AI Trade.](https://www.fool.com/investing/2026/06/24/micron-just-guided-for-a-staggering-50-billion-in/)；`doxatlas:raw_media:c762d097-1190-4ea7-9802-5d44fadefd9f`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron's cloud memory business unit (SUBJECT)
- 数量：$13.77 billion [revenue_current]；$3.39 billion [revenue_prior_year]；78% [operating_margin]
- 时间：fiscal Q3 2026 / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron's cloud memory business unit, the most direct AI proxy in the company's portfolio, grew revenue from $3.39 billion a year ago to $13.77 billion, and its operating margin hit 78%.

##### M03. Micron Technology's core data center unit reported fiscal third quarter 2026 revenue growth of more than sevenfold year over year.

- Mention ID：`mention:296b4d87dd678dd665832a57338c9b95e69b0afb0153cca9190f120399c5ed47`
- 来源：[Micron Just Guided for a Staggering $50 Billion in Fiscal Q4 Revenue. Here's What It Means for the AI Trade.](https://www.fool.com/investing/2026/06/24/micron-just-guided-for-a-staggering-50-billion-in/)；`doxatlas:raw_media:c762d097-1190-4ea7-9802-5d44fadefd9f`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：core data center unit (SUBJECT)
- 数量：more than sevenfold [revenue_growth_yoy]
- 时间：fiscal Q3 2026 / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：The core data center unit grew even faster, with revenue up more than sevenfold year over year.

##### M04. Micron Technology reported fiscal third quarter 2026 revenue of approximately $41.5 billion for the period ended May 28, 2026.

- Mention ID：`mention:3846c08f2e6b505537e4f1dec498d693de62270dfc498ad1968193132e519701`
- 来源：[Micron Just Guided for a Staggering $50 Billion in Fiscal Q4 Revenue. Here's What It Means for the AI Trade.](https://www.fool.com/investing/2026/06/24/micron-just-guided-for-a-staggering-50-billion-in/)；`doxatlas:raw_media:c762d097-1190-4ea7-9802-5d44fadefd9f`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：about $41.5 billion [revenue]
- 时间：fiscal Q3 2026 / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron's fiscal Q3 revenue, for the period ended May 28, 2026, came in at about $41.5 billion.

##### M05. All four of Micron Technology's business units reported higher revenue in fiscal third quarter 2026 compared to both the prior quarter and the year-ago period.

- Mention ID：`mention:e208269c1a69cba9f6f708fd2b5ee3e430ea56bed278b16ef416d3d6451758cc`
- 来源：[Micron Just Guided for a Staggering $50 Billion in Fiscal Q4 Revenue. Here's What It Means for the AI Trade.](https://www.fool.com/investing/2026/06/24/micron-just-guided-for-a-staggering-50-billion-in/)；`doxatlas:raw_media:c762d097-1190-4ea7-9802-5d44fadefd9f`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron's business units (SUBJECT)
- 数量：—
- 时间：fiscal Q3 2026 / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Topping it all off, all four of Micron's business units -- cloud memory, core data center, mobile and client, and automotive and embedded -- posted higher revenue than both the prior quarter and the year-ago period.

##### M06. Micron Technology reported fiscal third quarter 2026 GAAP gross margin of 84.6% and non-GAAP adjusted earnings per share of $25.11.

- Mention ID：`mention:f946142a1aa8dfcd500f5153838cd4301e9b54603d1865f1f17f08ea6815f15b`
- 来源：[Micron Just Guided for a Staggering $50 Billion in Fiscal Q4 Revenue. Here's What It Means for the AI Trade.](https://www.fool.com/investing/2026/06/24/micron-just-guided-for-a-staggering-50-billion-in/)；`doxatlas:raw_media:c762d097-1190-4ea7-9802-5d44fadefd9f`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：84.6% [gross_margin_gaap]；$25.11 [eps_non_gaap]
- 时间：fiscal Q3 2026 / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：In addition, Micron's gross margin reached 84.6% on a GAAP basis, and non-GAAP (adjusted) earnings per share hit $25.11.

##### M07. Micron's fiscal third-quarter 2026 revenue increased by 346% year over year.

- Mention ID：`mention:241c4c574077c4d8f02c62480e8344b9700d9668f7faed8d8b289d240e33b979`
- 来源：[Micron CEO Expects Memory Supply To Improve Gradually In 2028, But There's No 'Line Of Sight' When It Will Catch Up To Rising AI Demand](https://www.benzinga.com/markets/tech/26/06/60089761/micron-ceo-sees-memory-supply-improving-by-2028-but-no-clear-end-in-sight-to-ai-demand-shortfallmicron-ceo-sees-memory-supply-improving-by-2028-but-no-clear-end-in-sight-to-ai-demand-shortfall)；`doxatlas:raw_media:25387f96-b423-437c-b2cd-8652aa176bd8`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：346% [revenue_growth_yoy]
- 时间：fiscal third-quarter 2026 / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Revenue jumped 346% year over year.

##### M08. Micron reported fiscal third-quarter 2026 revenue of $41.46 billion and adjusted earnings of $25.11 per share.

- Mention ID：`mention:f7e4e4fe1f0e7811eda970d8559994ff9ea871659f674c7325d6ef99a81537db`
- 来源：[Micron CEO Expects Memory Supply To Improve Gradually In 2028, But There's No 'Line Of Sight' When It Will Catch Up To Rising AI Demand](https://www.benzinga.com/markets/tech/26/06/60089761/micron-ceo-sees-memory-supply-improving-by-2028-but-no-clear-end-in-sight-to-ai-demand-shortfallmicron-ceo-sees-memory-supply-improving-by-2028-but-no-clear-end-in-sight-to-ai-demand-shortfall)；`doxatlas:raw_media:25387f96-b423-437c-b2cd-8652aa176bd8`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：$41.46 billion [revenue]；$25.11 per share [adjusted_earnings_per_share]
- 时间：fiscal third-quarter 2026 / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron topped Wall Street expectations in the third quarter, reporting $41.46 billion in revenue and adjusted earnings of $25.11 per share, ahead of analyst estimates of $35.59 billion and $20.63 per share, respectively.

##### M09. Micron reported quarterly revenue of $41.5 billion, representing 346% year-over-year growth.

- Mention ID：`mention:e8fe5222a0bcefed19e21cd2b751a2374a1ffb4d4996a32f7fdb69c3c4ff23c5`
- 来源：[4 Blowout Numbers From Micron's Earnings Investors Need To See](https://www.fool.com/investing/2026/06/25/x-blowout-numbers-from-microns-earnings-investors/)；`doxatlas:raw_media:34fc2a61-fa6e-4db0-8e39-f862da0af41c`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：$41.5 billion [revenue]；346% [revenue_growth_yoy]
- 时间：third-quarter / UNKNOWN
- Evidence 状态：`TEXT_NOT_FOUND`
  - E01 `TEXT_NOT_FOUND` / `text:0`：Micron reported 346% revenue growth in the quarter to $41.5 billion.

##### M10. Micron reported revenue of $41.46 billion.

- Mention ID：`mention:6a847a3b7a7b9f22ab3ac52829aa13064af86c344f210e147c913999450d219c`
- 来源：[Micron CEO Says AI Memory Shortage Could Last Beyond 2028: These ETFs Are Positioned To Profit](https://www.benzinga.com/etfs/sector-etfs/26/06/60094351/micron-ceo-says-ai-memory-shortage-could-last-beyond-2028-these-etfs-are-positioned-to-profit)；`doxatlas:raw_media:bf5258ec-37f8-408b-ace2-8b016d16f725`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：$41.46 billion [revenue]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Revenue of $41.46 billion, up roughly 346% from a year earlier and well ahead of Wall Street estimates of $35.6 billion.

##### M11. Micron reported adjusted earnings of $25.11 per share.

- Mention ID：`mention:aa02ea6635cc2b12db484151297472d02538116b838219688f1da6cd67b76d3f`
- 来源：[Micron CEO Says AI Memory Shortage Could Last Beyond 2028: These ETFs Are Positioned To Profit](https://www.benzinga.com/etfs/sector-etfs/26/06/60094351/micron-ceo-says-ai-memory-shortage-could-last-beyond-2028-these-etfs-are-positioned-to-profit)；`doxatlas:raw_media:bf5258ec-37f8-408b-ace2-8b016d16f725`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：$25.11 per share [adjusted_earnings_per_share]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Adjusted earnings of $25.11 per share, which topped expectations

##### M12. Micron reported third-quarter revenue of $41.4 billion.

- Mention ID：`mention:07737c83d33ac5ee450e165a60248567a748d2146eb0a54081d48403d9ec68dc`
- 来源：[Micron posts record results as AI boom drives 15-fold jump in net profit](https://www.euronews.com/business/2026/06/25/micron-posts-record-results-as-ai-boom-drives-15-fold-jump-in-net-profit)；`doxatlas:raw_media:2b5bba2a-e472-4a1f-a341-5be6e4286dd1`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron Technology (ACTOR)
- 数量：$41.4 billion [revenue]
- 时间：third quarter / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：revenue in the third quarter reached $41.4 billion (€36.5bn), more than four times the $9.3 billion (€8.2bn) it recorded in the same period last year
  - E02 `VERIFIED` / `text:0`：The figure also comfortably beat the roughly $35.7 billion (€31.4bn) analysts had forecast

##### M13. Micron posted net income of $28.24 billion and adjusted earnings of $25.11 per share for the quarter.

- Mention ID：`mention:11cf0c0289fced9c30f11ad26dcf0c131c331cf06f222c52216d0d9c650ffbec`
- 来源：[Micron posts record results as AI boom drives 15-fold jump in net profit](https://www.euronews.com/business/2026/06/25/micron-posts-record-results-as-ai-boom-drives-15-fold-jump-in-net-profit)；`doxatlas:raw_media:2b5bba2a-e472-4a1f-a341-5be6e4286dd1`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron Technology (ACTOR)
- 数量：$28.24 billion [net_income]；$25.11 a share [adjusted_earnings_per_share]
- 时间：third quarter / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：The Idaho-based group posted net income of $28.24 billion (€24.9bn), or $24.67 per share, against less than $2 billion (€1.7bn) a year ago. Adjusted earnings of $25.11 a share sailed past the $20.49 expected.

##### M14. Micron reported Q3 earnings that exceeded market expectations.

- Mention ID：`mention:bd8fe6d057681317f3f8ad2df5eba3d3263d017f2c1f3c0b89206f18bb189b7c`
- 来源：[Dow Surges 250 Points; Micron Posts Upbeat Q3 Earnings](https://finnhub.io/api/news?id=7316a88ccda79b3354a49965fa82f15ed81fe3722e51491463cf118f097a0143)；`doxatlas:raw_media:fce73732-1c68-4d0f-9d7f-c5800393ffff`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：—
- 时间：Q3 / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron beats expectations with Q3 results

##### M15. Micron reported third-quarter adjusted earnings of $25.11 per share.

- Mention ID：`mention:837a219a54837ada10a2c6c8445c5ebbbeb84d7902572dc79a33e9dfbf61aae9`
- 来源：[These Analysts Boost Their Forecasts On Micron Technology After Better-Than-Expected Q3 Results](https://www.benzinga.com/analyst-stock-ratings/price-target/26/06/60100007/these-analysts-boost-their-forecasts-on-micron-technology-after-better-than-expected-q3-results)；`doxatlas:raw_media:5d890b9d-c789-4a99-87d6-8a34a3ea4cc2`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：$25.11 per share [adjusted_earnings_per_share]
- 时间：Q3 FY2026 / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：The semiconductor company posted adjusted earnings of $25.11 per share, beating analyst estimates of $20.63 per share.

##### M16. Micron reported third-quarter revenue of $41.46 billion.

- Mention ID：`mention:f2042616dfd8d94dd283840efb7856428ce3d74d755145126451a8ae607012f2`
- 来源：[These Analysts Boost Their Forecasts On Micron Technology After Better-Than-Expected Q3 Results](https://www.benzinga.com/analyst-stock-ratings/price-target/26/06/60100007/these-analysts-boost-their-forecasts-on-micron-technology-after-better-than-expected-q3-results)；`doxatlas:raw_media:5d890b9d-c789-4a99-87d6-8a34a3ea4cc2`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：$41.46 billion [revenue]
- 时间：Q3 FY2026 / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron reported third-quarter revenue of $41.46 billion, exceeding analyst estimates of $35.59 billion

##### M17. Micron Technology reported third-quarter revenue growth of 346% year-over-year.

- Mention ID：`mention:212a5e2e9d8b16248b6121905dbc64723b1aead50ef56eb7c1fce6ea35eb8637`
- 来源：[Investors bet on AI again after Micron reports 346% sales jump](https://edition.cnn.com/2026/06/25/business/micron-results-ai-stocks-volatility)；`doxatlas:raw_media:1aaec309-8ccb-42d6-b974-b4aa3bf2d43c`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron Technology (ACTOR)
- 数量：346% [revenue_growth]
- 时间：third quarter / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Revenues soared 346% over the same period.

##### M18. Micron reports fiscal third-quarter financial results including revenue and data center revenue performance.

- Mention ID：`mention:1e98ff65ecd79ca93872bfad1612c727d59a3173d5aef1e33450cb8aebc04b53`
- 来源：[The AI Memory Trade Is Heating Up: Defiance Launches 2X DRAM ETF After Micron's Blowout Quarter](https://www.benzinga.com/etfs/new-etfs/26/06/60103609/the-ai-memory-trade-is-heating-up-defiance-launches-2x-dram-etf-after-microns-blowout-quarter)；`doxatlas:raw_media:238a879b-e686-457d-b83d-5b0405bd28c6`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：$11.3 billion [revenue]
- 时间：fiscal third-quarter / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron reported fiscal third-quarter revenue of $11.3 billion, up 37% year-over-year, while data center revenue more than doubled as demand for high-bandwidth memory (HBM) chips used in AI servers continued to surge.

##### M19. Micron Technology reported third-quarter revenue of $41.5 billion.

- Mention ID：`mention:75072da862bd5748938191b1ae217e4c7e1dea613c959c55ef2e7848730dceef`
- 来源：[Wedbush says chip rally has further to run after Micron's blowout quarter](https://www.proactiveinvestors.com/companies/news/1094490/wedbush-says-chip-rally-has-further-to-run-after-micron-s-blowout-quarter-1094490.html)；`doxatlas:raw_media:79fe1f85-a1cc-44e1-928b-d0256c075eb4`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron Technology Inc (ACTOR)
- 数量：$41.5 billion [revenue]
- 时间：third-quarter / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron reported third-quarter revenue of $41.5 billion, up 74% on the prior quarter and well ahead of the $35.9 billion expected by analysts.

##### M20. Micron reported fiscal Q3 earnings of $25.11 per share.

- Mention ID：`mention:c97518140c7c2c16e306148796ec5e5d70a21a88069a1de4bcf0dc5e85dbd928`
- 来源：[Why Sandisk Stock Soared Today](https://www.fool.com/investing/2026/06/25/why-sandisk-stock-soared-today/)；`doxatlas:raw_media:35a4333e-6608-482b-b963-ba2f64392063`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：$25.11 per share [earnings_per_share]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron actually earned $25.11 per share

##### M21. Micron reported fiscal Q3 sales of $41.5 billion.

- Mention ID：`mention:f53e10a4ab17b43e2ef2ce4afafa352932b30c96433a0d8498b5a166738cb940`
- 来源：[Why Sandisk Stock Soared Today](https://www.fool.com/investing/2026/06/25/why-sandisk-stock-soared-today/)；`doxatlas:raw_media:35a4333e-6608-482b-b963-ba2f64392063`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：$41.5 billion [sales]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：sales quadrupled year over year to $41.5 billion

##### M22. Micron Technology reported quarterly revenue of more than $41 billion.

- Mention ID：`mention:1d4627d72e0cbe6a986874340b82f183b33c16923c3024654d4305ea9251117e`
- 来源：[Is Micron a Buy After Its Blowout Earnings Report?](https://www.fool.com/investing/2026/06/25/is-micron-a-buy-after-its-blowout-earnings-report/)；`doxatlas:raw_media:a424d484-3e57-47ed-b21d-50aeceafe593`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron Technology (ACTOR)
- 数量：more than $41 billion [revenue]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron's revenue soared in the triple digits to more than $41 billion,

##### M23. Micron Technology's quarterly revenue reached record levels for the fifth consecutive time.

- Mention ID：`mention:fc3ebd3a2728db813ce98f1b8c137ac74590d4d09c51098bf2c24a1c202306d1`
- 来源：[Is Micron a Buy After Its Blowout Earnings Report?](https://www.fool.com/investing/2026/06/25/is-micron-a-buy-after-its-blowout-earnings-report/)；`doxatlas:raw_media:a424d484-3e57-47ed-b21d-50aeceafe593`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron Technology (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Quarterly revenue reached record levels for the fifth consecutive time.

##### M24. Micron reported third-quarter revenue of $41.5 billion, beating estimates by $6.4 billion.

- Mention ID：`mention:393be66eec42b4ea86d426554ca0d540238a024bc2e4ca766562689e289c177d`
- 来源：[Why Everyone Is Talking About Micron](https://www.fool.com/investing/2026/06/25/why-everyone-is-talking-about-micron/)；`doxatlas:raw_media:49fb27c5-84b4-4e56-8791-f7ac23de38b7`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：$41.5 billion [revenue]；$6.4 billion [beat_estimate]
- 时间：third-quarter / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Revenue of $41.5 billion beat estimates by $6.4 billion.

##### M25. Micron's third-quarter revenue quadrupled from the prior year.

- Mention ID：`mention:c1d26b1b35c34311ec9a43118154ce2878f28a0cf305f4dae83c5bf20df2e692`
- 来源：[Why Everyone Is Talking About Micron](https://www.fool.com/investing/2026/06/25/why-everyone-is-talking-about-micron/)；`doxatlas:raw_media:49fb27c5-84b4-4e56-8791-f7ac23de38b7`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron (SUBJECT)
- 数量：—
- 时间：third-quarter / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Revenue quadrupled from the prior year.

##### M26. Micron reported fiscal third-quarter revenue of $41.5 billion.

- Mention ID：`mention:02fdfbbe9ab4cd7b2079b0540b1517b016ab5f339b9572821d2ce0cc530e4dab`
- 来源：[Micron shares surge to all-time high as Wall Street cheers unprecedented contract visibility](https://www.proactiveinvestors.com/companies/news/1094520/micron-shares-surge-to-all-time-high-as-wall-street-cheers-unprecedented-contract-visibility-1094520.html)；`doxatlas:raw_media:6b5d042a-9236-453b-8b60-2ba6c2f16b4f`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：$41.5 billion [revenue]
- 时间：fiscal third-quarter / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron reported fiscal third-quarter revenue of $41.5 billion, up 74% year-over-year and well above the Street's $35.9 billion estimate.

##### M27. Micron reported free cash flow of $18.304 billion for fiscal Q3.

- Mention ID：`mention:08817ceb229e2f422fb07bd53b61c89fee45a7e99a35b33eccae4a53c759bcd3`
- 来源：[Micron Just Locked In $100 Billion in Sales, and Wall Street Thinks the Boom-Bust Chip Cycle Is Dead](https://247wallst.com/investing/2026/06/25/micron-just-locked-in-100-billion-in-sales-and-wall-street-thinks-the-boom-bust-chip-cycle-is-dead/)；`doxatlas:raw_media:1e515897-64d4-4896-b936-10a04501eff4`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron (SUBJECT)
- 数量：$18.304 billion [free_cash_flow]
- 时间：fiscal Q3 / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Free cash flow was a robust $18.304 billion in the quarter

##### M28. Micron reported Cloud Memory segment revenue of $13.769 billion and Core Data Center revenue of $11.524 billion for fiscal Q3.

- Mention ID：`mention:0b0073706af574369b7bcab77b4b6d8b06ab37b995d8f9ee46a2bba4b09f11f2`
- 来源：[Micron Just Locked In $100 Billion in Sales, and Wall Street Thinks the Boom-Bust Chip Cycle Is Dead](https://247wallst.com/investing/2026/06/25/micron-just-locked-in-100-billion-in-sales-and-wall-street-thinks-the-boom-bust-chip-cycle-is-dead/)；`doxatlas:raw_media:1e515897-64d4-4896-b936-10a04501eff4`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron (SUBJECT)
- 数量：$13.769 billion [cloud_memory_revenue]；$11.524 billion [core_data_center_revenue]
- 时间：fiscal Q3 / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron’s Cloud Memory segment alone did $13.769 billion, with Core Data Center another $11.524 billion.

##### M29. Micron reported fiscal Q3 revenue of $41.456 billion.

- Mention ID：`mention:c6cbef71b1ddad7336ef483ab737c5ed002afeaf740a2a24bb3a40ba4ec25207`
- 来源：[Micron Just Locked In $100 Billion in Sales, and Wall Street Thinks the Boom-Bust Chip Cycle Is Dead](https://247wallst.com/investing/2026/06/25/micron-just-locked-in-100-billion-in-sales-and-wall-street-thinks-the-boom-bust-chip-cycle-is-dead/)；`doxatlas:raw_media:1e515897-64d4-4896-b936-10a04501eff4`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron (SUBJECT)
- 数量：$41.456 billion [revenue]
- 时间：fiscal Q3 / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron’s fiscal Q3 revenue came in at $41.456 billion, a 17.60% beat on consensus and 345.72% year-over-year growth from the $9.3 billion Micron printed in the prior-year quarter.

##### M30. Micron reported Q3 revenue of $41.46 billion, representing a 346% year-over-year increase.

- Mention ID：`mention:e9faa7d6b880272d7014c336c2f37041527052fe115148aba9097719b0ae5c1c`
- 来源：[MU Stock Soars as Micron’s Revenue More Than Quadrupled in Q3](https://www.barchart.com/story/news/3092/mu-stock-soars-as-microns-revenue-more-than-quadrupled-in-q3)；`doxatlas:raw_media:181db9d6-5959-4fb0-947e-3c6680861566`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：$41.46 billion [revenue]；346% [revenue_yoy_growth]
- 时间：Q3 / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron (MU) stock is ripping higher on June 25 after the memory chip giant posted a blockbuster Q3, featuring a 346% year-over-year increase in revenue to $41.46 billion.

##### M31. Micron Technology reported earnings.

- Mention ID：`mention:604b280d5e386d6a8a20af115cc2e4503f3ab7ff941a3e73b27373ff6eb1d91d`
- 来源：[Stock Market Today, June 25: Apple Drops After Raising Device Prices to Offset Higher Memory Costs](https://www.fool.com/coverage/stock-market-today/2026/06/25/stock-market-today-june-25-apple-drops-after-raising-device-prices-to-offset-higher-memory-costs/)；`doxatlas:raw_media:d9404d58-cd88-4ccc-a9d2-ec4004629dbc`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron Technology (ACTOR)
- 数量：—
- 时间：2026-06-24T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron Technology (MU 5.68%) reported stellar earnings after the bell yesterday.

##### M32. Micron Technology reported record adjusted gross margin of about 85%.

- Mention ID：`mention:e89cefcef2c67839677f63115dd35ea5417a6022c9c79a953d31135d6176d96a`
- 来源：[Stock Market Today, June 25: Apple Drops After Raising Device Prices to Offset Higher Memory Costs](https://www.fool.com/coverage/stock-market-today/2026/06/25/stock-market-today-june-25-apple-drops-after-raising-device-prices-to-offset-higher-memory-costs/)；`doxatlas:raw_media:d9404d58-cd88-4ccc-a9d2-ec4004629dbc`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron Technology (ACTOR)
- 数量：about 85% [adjusted_gross_margin]
- 时间：2026-06-24T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：record adjusted gross margin of about 85%

##### M33. Micron Technology revenue quadrupled year over year.

- Mention ID：`mention:eb9da1bf53e46877b872ee0eb009301539527ff9e5ccd64faaa90d57e12cc3c3`
- 来源：[Stock Market Today, June 25: Apple Drops After Raising Device Prices to Offset Higher Memory Costs](https://www.fool.com/coverage/stock-market-today/2026/06/25/stock-market-today-june-25-apple-drops-after-raising-device-prices-to-offset-higher-memory-costs/)；`doxatlas:raw_media:d9404d58-cd88-4ccc-a9d2-ec4004629dbc`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron Technology (SUBJECT)
- 数量：—
- 时间：2026-06-24T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：revenue quadrupling year over year

#### P63-A18. Micron Technology's HBM4 product, built on 1-beta DRAM technology, is in high-volume shipments to its lead customer and qualification samples are being sent to additional end customers.

- Atomic ID：`atomic:be9c6f8d7931a02584e2c1ce`
- Family / Assertion：`PRODUCTION_SUPPLY` / `ONGOING`
- Mention 数：1；Version：1
- 时间：UNKNOWN

##### M01. Micron Technology's HBM4 product, built on 1-beta DRAM technology, is in high-volume shipments to its lead customer and qualification samples are being sent to additional end customers.

- Mention ID：`mention:fe40ed6e87dff7e130691461cb86b67a95cc139c34fd318d5b87ca9a9ab89a16`
- 来源：[Micron Just Guided for a Staggering $50 Billion in Fiscal Q4 Revenue. Here's What It Means for the AI Trade.](https://www.fool.com/investing/2026/06/24/micron-just-guided-for-a-staggering-50-billion-in/)；`doxatlas:raw_media:c762d097-1190-4ea7-9802-5d44fadefd9f`
- Family / Assertion：`PRODUCTION_SUPPLY` / `ONGOING`
- 参与者：Micron (ACTOR)；HBM4 (TARGET)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：HBM4, built on Micron's 1-beta DRAM technology, is already in high-volume shipments to its lead customer, with qualification samples now going to additional end customers.

#### P63-A19. Micron Technology reported fiscal third quarter 2026 capital expenditures of $7.1 billion.

- Atomic ID：`atomic:c84ff842aaee90fca47aee08`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- Mention 数：2；Version：2
- 时间：fiscal Q3 2026 / UNKNOWN
- **人工审计：需拆分。** 该 Atomic 产生 0 个 FP pair

##### M01. Micron Technology reported fiscal third quarter 2026 capital expenditures of $7.1 billion.

- Mention ID：`mention:61cd32e5305c99b2661ca40d42eafcf4c55edc2a746cc0e3b2a620339e8ddab9`
- 来源：[Micron Just Guided for a Staggering $50 Billion in Fiscal Q4 Revenue. Here's What It Means for the AI Trade.](https://www.fool.com/investing/2026/06/24/micron-just-guided-for-a-staggering-50-billion-in/)；`doxatlas:raw_media:c762d097-1190-4ea7-9802-5d44fadefd9f`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：$7.1 billion [capital_expenditures]
- 时间：fiscal Q3 2026 / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron's capital expenditures climbed to $7.1 billion in fiscal Q3 alone

##### M02. Micron reported fiscal Q3 capital expenditures of $7.826 billion.

- Mention ID：`mention:703645b86fbcce935af82718b1fb33205974b10760bda92d21f2c5b76486dec3`
- 来源：[Micron Just Locked In $100 Billion in Sales, and Wall Street Thinks the Boom-Bust Chip Cycle Is Dead](https://247wallst.com/investing/2026/06/25/micron-just-locked-in-100-billion-in-sales-and-wall-street-thinks-the-boom-bust-chip-cycle-is-dead/)；`doxatlas:raw_media:1e515897-64d4-4896-b936-10a04501eff4`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron (SUBJECT)
- 数量：$7.826 billion [capex]
- 时间：Q3 / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Q3 capex was $7.826 billion, up 166.37% year-over-year

#### P63-A20. Micron Technology management stated that the AI revolution is in its early stages.

- Atomic ID：`atomic:d0f40d740f2061be5326b424`
- Family / Assertion：`OTHER` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：UNKNOWN

##### M01. Micron Technology management stated that the AI revolution is in its early stages.

- Mention ID：`mention:e0d51d30e0681e267521cfad6a31e1e100d3402d2351e8d6e747bd647b18c3c8`
- 来源：[Is Micron a Buy After Its Blowout Earnings Report?](https://www.fool.com/investing/2026/06/25/is-micron-a-buy-after-its-blowout-earnings-report/)；`doxatlas:raw_media:a424d484-3e57-47ed-b21d-50aeceafe593`
- Family / Assertion：`OTHER` / `ACTUAL`
- 参与者：Micron Technology (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：the company offered an extremely positive message, saying we're in "the early innings" of the AI revolution

#### P63-A21. Micron expects industry memory supply to improve gradually in 2028 but lacks visibility on when supply will meet AI demand.

- Atomic ID：`atomic:e306240c299431a69373d0a4`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- Mention 数：3；Version：3
- 时间：2028 / UNKNOWN
- **人工审计：需拆分。** 该 Atomic 产生 3 个 FP pair；建议分区：2028_supply_improvement_expectation, current_no_line_of_sight_statement, current_demand_outpaces_supply_statement

##### M01. Micron expects industry memory supply to improve gradually in 2028 but lacks visibility on when supply will meet AI demand.

- Mention ID：`mention:18b28d9d0fc936d541a486f86f2adb9aebb48844b26e388a79264633d9ed7948`
- 来源：[Micron CEO Expects Memory Supply To Improve Gradually In 2028, But There's No 'Line Of Sight' When It Will Catch Up To Rising AI Demand](https://www.benzinga.com/markets/tech/26/06/60089761/micron-ceo-sees-memory-supply-improving-by-2028-but-no-clear-end-in-sight-to-ai-demand-shortfallmicron-ceo-sees-memory-supply-improving-by-2028-but-no-clear-end-in-sight-to-ai-demand-shortfall)；`doxatlas:raw_media:25387f96-b423-437c-b2cd-8652aa176bd8`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- 参与者：Micron (ACTOR)
- 数量：—
- 时间：2028 / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：“Even as we expect industry supply to improve gradually in 2028, we currently do not have line of sight as to when memory supply will be able to catch up with increasing demand,” he said.

##### M02. Micron does not have line of sight as to when memory supply will be able to catch up with increasing demand.

- Mention ID：`mention:02ddb4576e963127ebe046454a8f476a075d269fdc99ca7ca7336aed720bc64f`
- 来源：[Tesla's Optimus Could Become A Bigger Memory Customer Than Its Cars, If Micron Is Right](https://www.benzinga.com/trading-ideas/long-ideas/26/06/60094569/tesla-optimus-could-become-a-bigger-memory-customer-than-its-cars-if-micron-is-right)；`doxatlas:raw_media:005d3637-8724-483c-9077-7f643d3fa481`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：“We currently do not have line of sight as to when memory supply will be able to catch up with increasing demand,”

##### M03. Micron believes AI-driven demand is outpacing the industry’s ability to add new supply.

- Mention ID：`mention:1e0b37d516f20ca8b862de838c18df38c78fa7977af4468d2e7bd299ef4b50fa`
- 来源：[Tesla's Optimus Could Become A Bigger Memory Customer Than Its Cars, If Micron Is Right](https://www.benzinga.com/trading-ideas/long-ideas/26/06/60094569/tesla-optimus-could-become-a-bigger-memory-customer-than-its-cars-if-micron-is-right)；`doxatlas:raw_media:005d3637-8724-483c-9077-7f643d3fa481`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- 参与者：Micron (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：The company believes AI-driven demand is outpacing the industry’s ability to add new supply

#### P63-A22. Micron Technology's data centre revenue reached an annualised run rate of about $100 billion.

- Atomic ID：`atomic:e89133b8c02b1a57ec1e3f6b`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- Mention 数：2；Version：2
- 时间：fiscal third-quarter / UNKNOWN
- **人工审计：需拆分。** 该 Atomic 产生 0 个 FP pair

##### M01. Micron Technology's data centre revenue reached an annualised run rate of about $100 billion.

- Mention ID：`mention:8e98bca0120603c30fe64fbcb949c616ada06083427199e31638e802c78eb7ff`
- 来源：[Wedbush says chip rally has further to run after Micron's blowout quarter](https://www.proactiveinvestors.com/companies/news/1094490/wedbush-says-chip-rally-has-further-to-run-after-micron-s-blowout-quarter-1094490.html)；`doxatlas:raw_media:79fe1f85-a1cc-44e1-928b-d0256c075eb4`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron Technology Inc (ACTOR)
- 数量：$100 billion [data_centre_revenue_run_rate]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Its data centre revenue has now reached an annualised run rate of about $100 billion

##### M02. Micron's data center revenue reached an annualized run rate of approximately $100 billion.

- Mention ID：`mention:0ba10094d55ecce783c91c9381d438ec4b1c9b3d0fd5c3336baec2c7928e37ab`
- 来源：[Micron shares surge to all-time high as Wall Street cheers unprecedented contract visibility](https://www.proactiveinvestors.com/companies/news/1094520/micron-shares-surge-to-all-time-high-as-wall-street-cheers-unprecedented-contract-visibility-1094520.html)；`doxatlas:raw_media:6b5d042a-9236-453b-8b60-2ba6c2f16b4f`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：approximately $100 billion [data_center_revenue_run_rate]
- 时间：fiscal third-quarter / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Data center revenue hit an annualized run rate of approximately $100 billion.

#### P63-A23. Micron expects free cash flow margins to approach 50-60%.

- Atomic ID：`atomic:eb527bb5c56037d79cd6c91a`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- Mention 数：1；Version：1
- 时间：UNKNOWN

##### M01. Micron expects free cash flow margins to approach 50-60%.

- Mention ID：`mention:9c672feb795d5f43543398fc61311733f6728eb157f25dc6ba84da1ab572e084`
- 来源：[Micron shares surge to all-time high as Wall Street cheers unprecedented contract visibility](https://www.proactiveinvestors.com/companies/news/1094520/micron-shares-surge-to-all-time-high-as-wall-street-cheers-unprecedented-contract-visibility-1094520.html)；`doxatlas:raw_media:6b5d042a-9236-453b-8b60-2ba6c2f16b4f`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- 参与者：Micron (ACTOR)
- 数量：50-60% [free_cash_flow_margin]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：free cash flow margins expected to approach 50-60%

#### P63-A24. Micron Technology reported free cash flow of $18 billion.

- Atomic ID：`atomic:eddfbc06cb30fd3b22da9f51`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：UNKNOWN

##### M01. Micron Technology reported free cash flow of $18 billion.

- Mention ID：`mention:90863ec88d229f8387ec8d0bcf6297a16f9bae61b8eb1eb470562ad941014f2b`
- 来源：[Is Micron a Buy After Its Blowout Earnings Report?](https://www.fool.com/investing/2026/06/25/is-micron-a-buy-after-its-blowout-earnings-report/)；`doxatlas:raw_media:a424d484-3e57-47ed-b21d-50aeceafe593`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron Technology (ACTOR)
- 数量：$18 billion [free_cash_flow]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Free cash flow climbed to record levels of $18 billion,

#### P63-A25. Micron expects a sustained, substantial multi-decade memory demand cycle to begin in the latter part of this decade.

- Atomic ID：`atomic:efd8d4f39fde4d4f6ff5df60`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- Mention 数：2；Version：2
- 时间：UNKNOWN
- **人工审计：需拆分。** 该 Atomic 产生 1 个 FP pair

##### M01. Micron expects a sustained, substantial multi-decade memory demand cycle to begin in the latter part of this decade.

- Mention ID：`mention:8d3ee1e030c0768366892c5e270aae11651d185b3c2c44a20d4c022de2b2a730`
- 来源：[Tesla's Optimus Could Become A Bigger Memory Customer Than Its Cars, If Micron Is Right](https://www.benzinga.com/trading-ideas/long-ideas/26/06/60094569/tesla-optimus-could-become-a-bigger-memory-customer-than-its-cars-if-micron-is-right)；`doxatlas:raw_media:005d3637-8724-483c-9077-7f643d3fa481`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- 参与者：Micron (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：“We expect a sustained, substantial multi-decade memory demand cycle to begin in the latter part of this decade.”

##### M02. Micron expects tight memory market conditions to persist beyond calendar 2027.

- Mention ID：`mention:ede26d77b42e597d34753a58a03765e995fa60cf3b454f9aaa33a1ae4565b037`
- 来源：[Tesla's Optimus Could Become A Bigger Memory Customer Than Its Cars, If Micron Is Right](https://www.benzinga.com/trading-ideas/long-ideas/26/06/60094569/tesla-optimus-could-become-a-bigger-memory-customer-than-its-cars-if-micron-is-right)；`doxatlas:raw_media:005d3637-8724-483c-9077-7f643d3fa481`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- 参与者：Micron (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：with Micron now expecting tight memory market conditions to persist beyond calendar 2027.

#### P63-A26. Micron expects fiscal fourth-quarter 2026 capital expenditures of around $10 billion and total fiscal 2026 capital spending of approximately $27 billion.

- Atomic ID：`atomic:fe9b7445cde8f6120a3eac33`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- Mention 数：3；Version：3
- 时间：fiscal 2026 / UNKNOWN
- **人工审计：需拆分。** 该 Atomic 产生 2 个 FP pair；建议分区：fy2026_capex_guidance, fy2027_capex_plan

##### M01. Micron expects fiscal fourth-quarter 2026 capital expenditures of around $10 billion and total fiscal 2026 capital spending of approximately $27 billion.

- Mention ID：`mention:5f261a44873022b622a1a01ea9729707265ac27dff0230206a4c4e189813f844`
- 来源：[Micron Is Spending Billions On New Fabs—And Still Says It'll Return 100% Of Excess Cash To Shareholders](https://www.benzinga.com/trading-ideas/long-ideas/26/06/60095272/micron-is-spending-billions-on-new-fabs-and-still-says-itll-return-100-of-excess-cash-to-shareholders)；`doxatlas:raw_media:4fcc4fb0-ab7d-4157-8e65-a09ef40a7581`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- 参与者：Micron (ACTOR)
- 数量：$10 billion [capex_q4_fy2026]；$27 billion [capex_total_fy2026]
- 时间：fiscal 2026 / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron expects fiscal fourth-quarter capital expenditures of around $10 billion, bringing total fiscal 2026 capital spending to approximately $27 billion.

##### M02. Micron raised its 2026 capital expenditure forecast.

- Mention ID：`mention:2eb745ecc0ad72ffe9577016fab8973862d2ef0457fab282069a00183ab7e739`
- 来源：[Micron shares surge to all-time high as Wall Street cheers unprecedented contract visibility](https://www.proactiveinvestors.com/companies/news/1094520/micron-shares-surge-to-all-time-high-as-wall-street-cheers-unprecedented-contract-visibility-1094520.html)；`doxatlas:raw_media:6b5d042a-9236-453b-8b60-2ba6c2f16b4f`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：—
- 时间：2026 / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron raised its 2026 capital expenditure forecast

##### M03. Micron signaled capital expenditures of over $40 billion for the next year.

- Mention ID：`mention:bce6d476b2ebbfb1a88c16fdbd9cf660658fe0b52c5e1ab49f196fb2072629b6`
- 来源：[Micron Just Locked In $100 Billion in Sales, and Wall Street Thinks the Boom-Bust Chip Cycle Is Dead](https://247wallst.com/investing/2026/06/25/micron-just-locked-in-100-billion-in-sales-and-wall-street-thinks-the-boom-bust-chip-cycle-is-dead/)；`doxatlas:raw_media:1e515897-64d4-4896-b936-10a04501eff4`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `PLANNED`
- 参与者：Micron (ACTOR)
- 数量：$40 billion [capex_guidance]
- 时间：next year / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：, and the company has signaled capex over $40 billion next year, with roughly $20 billion going to construction and clean rooms.

### P64. Price increases of $150-$200 are expected across the iPhone lineup.

- Package ID：`package:f86074f9ae91ee6af9e177f9`
- Family / Kind：`EARNINGS_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：3
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：field:7a1cd7ea17be4100b9b7028d
- Anchor artifact / period：— / —
- 摘要：Price increases of $150-$200 are expected across the iPhone lineup.

#### P64-A01. Price increases of $150-$200 are expected across the iPhone lineup.

- Atomic ID：`atomic:feaceb0fc407589e4b9a7495`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- Mention 数：1；Version：1
- 时间：UNKNOWN

##### M01. Price increases of $150-$200 are expected across the iPhone lineup.

- Mention ID：`mention:253ff5fa5a66fc701ca7bc5c2cdcd58673d93fb1fc4816024995242d2235dca7`
- 来源：[Tim Cook Calls the Memory Crisis a "Hundred-Year Flood"](https://finance.yahoo.com/technology/articles/tim-cook-calls-memory-crisis-170140774.html)；`doxatlas:raw_media:d42584c3-2d2d-4559-affc-ccae5b388ce1`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- 参与者：iPhone lineup (SUBJECT)
- 数量：$150-$200 [price_increase_range]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：with price increases of $150-$200 expected across the lineup.

### P65. Counterpoint estimates that higher component costs could add roughly $200 per iPhone for Apple.

- Package ID：`package:fd0ebddcd7b133dcd6e05d84`
- Family / Kind：`EARNINGS_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：3
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：COMPANY_AAPL, INSTITUTION_COUNTERPOINT_FUNDS
- Anchor artifact / period：— / —
- 摘要：Counterpoint estimates that higher component costs could add roughly $200 per iPhone for Apple.

#### P65-A01. Counterpoint estimates that higher component costs could add roughly $200 per iPhone for Apple.

- Atomic ID：`atomic:fce90089e1c4a8ad8f3074a9`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- Mention 数：1；Version：1
- 时间：UNKNOWN

##### M01. Counterpoint estimates that higher component costs could add roughly $200 per iPhone for Apple.

- Mention ID：`mention:e12a5a64f1f5996b2b4587af493b7549a6e840a815d453b3d6da64b480ad19f3`
- 来源：[Tim Cook Calls the Memory Crisis a "Hundred-Year Flood"](https://finance.yahoo.com/technology/articles/tim-cook-calls-memory-crisis-170140774.html)；`doxatlas:raw_media:d42584c3-2d2d-4559-affc-ccae5b388ce1`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- 参与者：Counterpoint (ACTOR)；Apple (AFFECTED)
- 数量：$200 per iPhone [cost_increase_per_unit]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Counterpoint estimates the higher component costs could add roughly $200 per iPhone for Apple,

## 4. 层级完整性与质量警告

- Package 覆盖：65/65
- Atomic 唯一覆盖：95/95；未归包：0
- Mention 唯一覆盖：225/225；未归 Atomic：0
- 已确认 Atomic FP pair：556；主要为不同 metric、交易时段、产品或业务动作的过合并。
- 已确认 Package FP/FN pair：28/110；主要为 reaction/earnings 边界错误和包碎片化。
- 本文档是运行结果的业务层级展示；人工 Gold、节点指标与根因详见配套验收报告。
