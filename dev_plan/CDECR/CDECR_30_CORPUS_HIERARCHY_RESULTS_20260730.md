# CDECR 30 篇真实测试集：最终层级结果

> 本文档按照 CDECR 的最终业务产物组织：`Package → Atomic Event → Event Mention → Source Evidence`。文章仅作为 Mention 的来源与审计坐标，不作为结果层级。

> 本文档展示冻结运行的实际结果，而非人工修正后的 Gold。“边界错误”与“需拆分”是逐条审计已确认的问题。

## 1. 总览

- 输入文档：30；成功：29/30
- 最终层级：61 Package → 131 Atomic → 199 Mention
- Token：输入 2,610,616 / 输出 443,211 / 合计 3,053,827
- Mention P/R/F1：76.88% / 57.09% / 65.52%
- Atomic Pair P/R/F1：56.45% / 39.11% / 46.20%
- Package Pair P/R/F1：80.43% / 38.95% / 52.48%

## 2. Package 索引

| # | Package | Family / Kind | Atomic | Mention | 标题 | 审计 |
| ---: | --- | --- | ---: | ---: | --- | --- |
| 1 | `package:034a6b4325…` | `ANALYST_REPORT` / `BOUNDED` | 1 | 2 | IDC expects Apple's average selling price to rise 12% this year. | — |
| 2 | `package:0b2833b436…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 3 | 3 | Citi stated that Sandisk is supported by healthier NAND supply and demand conditions and an improving pricing backdrop driven by AI-related data center spending. | — |
| 3 | `package:0f0d639e5a…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | 1 | Memory supply capacity expansion is constrained by construction timelines, labor shortages, regulatory hurdles, and energy infrastructure requirements. | — |
| 4 | `package:11c506e22f…` | `TRANSACTION` / `EPISODE` | 1 | 1 | Institutional investors net purchased approximately 100 billion won worth of stocks. | — |
| 5 | `package:126a151abf…` | `ANALYST_REPORT` / `BOUNDED` | 1 | 1 | Counterpoint estimates that higher component costs could add roughly $200 per iPhone for Apple. | — |
| 6 | `package:1520205d68…` | `PRODUCT_SCIENCE` / `EPISODE` | 1 | 1 | Continued advances in simulation, foundation models and integrated hardware and software are accelerating the development of physical AI. | — |
| 7 | `package:1aee0236cc…` | `ANALYST_REPORT` / `BOUNDED` | 1 | 1 | The consensus analyst rating on Micron stock is Strong Buy. | — |
| 8 | `package:1c31e2aabd…` | `ANALYST_REPORT` / `BOUNDED` | 2 | 2 | Bank of America reiterated its Buy rating on Micron and raised its price target to $1,550 from $1,500. | — |
| 9 | `package:1cc7ec8427…` | `ANALYST_REPORT` / `BOUNDED` | 2 | 2 | UBS tripled its price target for Micron. | — |
| 10 | `package:20f71cffec…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | 1 | The Nasdaq Composite index fell 0.46% to close at 25,359. | — |
| 11 | `package:3229092305…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | 1 | Sandisk is scheduled to hold an investor day in August where it may provide updates on demand expectations, technology roadmap, and capital return plans. | — |
| 12 | `package:34aa07329c…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | 1 | Alphabet stock ended at $343.71, representing a 0.46% decline. | — |
| 13 | `package:371809e25d…` | `TRANSACTION` / `EPISODE` | 2 | 4 | Micron management committed to returning 100% of excess cash to shareholders, increasing from the prior 50% policy. | — |
| 14 | `package:3785f9b0cd…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 6 | 6 | Micron's stock price increased, adding more than $100 billion in market value. | — |
| 15 | `package:419e02bc22…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 2 | 2 | Cloud providers and AI developers are expanding data center capacity for generative AI, driving demand for advanced DRAM and NAND memory and tightening industry supply. | — |
| 16 | `package:41ff7661a1…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | 1 | Qualcomm CEO Cristiano Amon stated that Qualcomm has secured capacity from manufacturers and expressed confidence in its forecast. | — |
| 17 | `package:4650ebb82c…` | `ANALYST_REPORT` / `BOUNDED` | 3 | 4 | Wedbush rates Micron at outperform with a price target of $1,300. | — |
| 18 | `package:4d41545c77…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | 2 | The KOSPI index increased by more than 5% at the market open on June 25, 2026, rising above 8,900 from a previous close of 8,400. | — |
| 19 | `package:4f609f4b26…` | `EARNINGS_DISCLOSURE` / `BOUNDED` | 2 | 2 | Analysts forecast Sandisk's earnings to reach $33.72 per share, representing a sequential doubling. | — |
| 20 | `package:51f6b21bb3…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 2 | 2 | NASDAQ index increased by 0.7% to reach 25,654.49 | — |
| 21 | `package:577d2cba79…` | `ANALYST_REPORT` / `BOUNDED` | 1 | 1 | Han Ji-young of Kiwoom Securities attributed the KOSPI surge to favorable macro conditions, Micron's earnings surprise, and strength in KOSPI200 night futures. | — |
| 22 | `package:5d9658fd35…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | 1 | Microsoft stock closed at $352.83, representing a 3.46% decline. | — |
| 23 | `package:6309780f9b…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | 1 | Tim Cook described the situation as a "hundred-year flood" in an interview with the Wall Street Journal. | — |
| 24 | `package:67f4ce99d5…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | 1 | The Magnificent Seven stocks declined by about 2%, reaching a two-month low. | — |
| 25 | `package:69ee11909b…` | `EARNINGS_DISCLOSURE` / `BOUNDED` | 27 | 51 | Demand for Micron's NAND and DRAM memory chips exceeded industry supply. | — |
| 26 | `package:6a5f4e89f7…` | `TRANSACTION` / `EPISODE` | 1 | 1 | SK Hynix disclosed plans for a listing on the US Nasdaq. | — |
| 27 | `package:6a8b93027c…` | `ANALYST_REPORT` / `BOUNDED` | 1 | 1 | Citi analyst Atif Malik expects Micron's SCAs to drive 40% of its revenue over the next five years. | — |
| 28 | `package:6bf4e0ab6a…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | 1 | Sandisk shares increased by approximately 15% in early trading on Thursday. | — |
| 29 | `package:6c22b5080a…` | `EARNINGS_DISCLOSURE` / `BOUNDED` | 6 | 11 | Micron Technology's market capitalization exceeded $1.2 trillion. | — |
| 30 | `package:6d47d35160…` | `PRODUCT_SCIENCE` / `EPISODE` | 1 | 1 | Humanoid robots carry 10 times the amount of memory as an average L2+ vehicle. | — |
| 31 | `package:6f96b26d35…` | `ANALYST_REPORT` / `BOUNDED` | 1 | 1 | Citi analysts maintained a Buy rating on Micron shares and raised the price target to $1,400. | — |
| 32 | `package:70dca7ead9…` | `ANALYST_REPORT` / `BOUNDED` | 1 | 1 | Jim Lebenthal is buying Micron stock. | — |
| 33 | `package:73bd641db2…` | `EARNINGS_DISCLOSURE` / `BOUNDED` | 2 | 2 | Qualcomm raised its non-handset revenue target to $40 billion by 2029, with approximately $15 billion expected from data center. | — |
| 34 | `package:74c07ddb56…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 18 | 43 | Micron guided next-quarter gross margin to 86%. | — |
| 35 | `package:7979eb2d42…` | `ANALYST_REPORT` / `BOUNDED` | 1 | 1 | Bernstein analyst Mark Newman suggested that Micron's new strategic customer agreements could include pricing ceilings, limiting headroom and failing to avoid cyclicality. | — |
| 36 | `package:8fd0a99cc0…` | `TRANSACTION` / `EPISODE` | 1 | 1 | Individual investors net purchased approximately 490 billion won worth of stocks. | — |
| 37 | `package:9141a03ee5…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | 1 | Apple trading volume reached 106.4 million shares, which was 119% above its three-month average of 48.5 million shares. | — |
| 38 | `package:91f1fb9f2a…` | `REGULATORY_LEGAL` / `EPISODE` | 1 | 1 | The Korea Exchange activated a buy-side sidecar mechanism, suspending program trading for five minutes shortly after the market open. | — |
| 39 | `package:92945fdbfa…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | 3 | Apple's stock price fell by over 5%, resulting in a loss of nearly $200 billion in market value. | — |
| 40 | `package:94ecfcedb5…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | 1 | South Korea's Kospi index decreased by 10%, triggering a circuit breaker. | — |
| 41 | `package:95fc89d844…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | 1 | Triller Group Inc stock price increased by 259% | — |
| 42 | `package:99a4a4c51e…` | `TRANSACTION` / `EPISODE` | 2 | 2 | Foreign investors net sold approximately 600 billion won worth of stocks. | — |
| 43 | `package:a11cec2773…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 3 | 4 | SK Hynix stock price increased by more than 10% in early morning trading on June 25, 2026. | — |
| 44 | `package:a1ea544f09…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | 1 | The artificial intelligence sector remains robust and active. | — |
| 45 | `package:ab32497c0b…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | 4 | Apple raised prices on multiple product lines including MacBook Neo, MacBook Air, MacBook Pro, iPad Pro, iPad Air, HomePod, HomePod mini, and Apple TV due to tightening supplies of memory and storage components. | — |
| 46 | `package:ab868458ce…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | 1 | Micron Technology announced a strategic partnership with Anthropic to scale next-generation AI infrastructure. | — |
| 47 | `package:ac2de482ee…` | `PRODUCT_SCIENCE` / `EPISODE` | 1 | 2 | Defiance launches the 2X DRAM ETF (DRAL). | — |
| 48 | `package:adb3f204d6…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | 1 | Stoxx 600 index increased by 0.6%. | — |
| 49 | `package:baaf016039…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 2 | 2 | Dow Jones Industrial Average indicated an increase of 0.3% in pre-market trade. | — |
| 50 | `package:bcc15f9e41…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | 1 | Micron Technology stock decreased by 13%. | — |
| 51 | `package:c5d532a320…` | `ANALYST_REPORT` / `BOUNDED` | 1 | 1 | Wedbush analyst Dan Ives expressed confidence in AI demand and recommended owning core tech winners into year-end. | — |
| 52 | `package:c7bb41f739…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | 1 | Technology sector shares increased by 1.6% | — |
| 53 | `package:cb0ca3c858…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | 1 | Communication services sector stocks decreased by 1.9% | — |
| 54 | `package:cd5b1adeef…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | 1 | Suppliers are reallocating cleanroom space from NAND flash to DRAM production, constraining NAND supply growth. | — |
| 55 | `package:d1fe96f1a9…` | `TRANSACTION` / `EPISODE` | 1 | 1 | Micron declared a quarterly dividend of 15 cents per share, payable on July 21, 2026, to shareholders of record on July 6, 2026. | — |
| 56 | `package:d2ef0b61ad…` | `ANALYST_REPORT` / `BOUNDED` | 3 | 4 | Benzinga’s Edge Stock Rankings indicate that MU has a positive price trend across all time frames. | — |
| 57 | `package:d92e1a50dd…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | 1 | Western Digital, SanDisk, and Seagate stock prices surged and reversed intraday losses in after-hours trading. | — |
| 58 | `package:e4b71e9ecd…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | 1 | Japan's Nikkei 225 index increased by 4.6% at close. | — |
| 59 | `package:f5a2e9878c…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | 1 | Growing HBM adoption is putting additional pressure on conventional memory supply due to higher manufacturing resource requirements. | — |
| 60 | `package:f88cf5c1fb…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | 2 | S&P 500 index increased by 0.6% to reach 7,401.17 | — |
| 61 | `package:ffb9fc27e5…` | `COMPANY_DISCLOSURE` / `BOUNDED` | 1 | 1 | Qualcomm's stock price increased by 12%. | — |

## 3. Package → Atomic → Mention 最终结构

### P01. IDC expects Apple's average selling price to rise 12% this year.

- Package ID：`package:034a6b432591de1ad4b17ade`
- Family / Kind：`ANALYST_REPORT` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 2 Mention
- Anchor entities：COMPANY_AAPL, field:c609201ffc663e0f3eecce7f
- Anchor artifact / period：— / —
- 摘要：IDC expects Apple's average selling price to rise 12% this year.

#### P01-A01. IDC expects Apple's average selling price to rise 12% this year.

- Atomic ID：`atomic:508a780de10cf90612d7e4f3`
- Family / Assertion：`ANALYST_ACTION` / `EXPECTED`
- Mention 数：2；Version：2
- 时间：UNKNOWN

##### M01. IDC expects Apple's average selling price to rise 12% this year.

- Mention ID：`mention:0d832246ef241fba1b28cbec002192ee36afebd1eac45f9fa6e59a712bbe8bc4`
- 来源：[Tim Cook Calls the Memory Crisis a "Hundred-Year Flood"](https://finance.yahoo.com/technology/articles/tim-cook-calls-memory-crisis-170140774.html)；`doxatlas:raw_media:d42584c3-2d2d-4559-affc-ccae5b388ce1`
- Family / Assertion：`ANALYST_ACTION` / `EXPECTED`
- 参与者：IDC (ACTOR)；Apple (SUBJECT)
- 数量：12% [average_selling_price_change_percent]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：sees Apple's average selling price rising 12% this year

##### M02. IDC expects all new iPhone models to move to 12GB of RAM.

- Mention ID：`mention:1ee9f3468b783ddaa07f009e79f35cb95b91b33e6ae6daa182785241c953cb50`
- 来源：[Tim Cook Calls the Memory Crisis a "Hundred-Year Flood"](https://finance.yahoo.com/technology/articles/tim-cook-calls-memory-crisis-170140774.html)；`doxatlas:raw_media:d42584c3-2d2d-4559-affc-ccae5b388ce1`
- Family / Assertion：`ANALYST_ACTION` / `EXPECTED`
- 参与者：IDC (ACTOR)；Apple (SUBJECT)
- 数量：12GB [ram_capacity]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：IDC expects all new iPhone models to move to 12GB of RAM as Apple pushes Apple Intelligence features that require more memory

### P02. Citi stated that Sandisk is supported by healthier NAND supply and demand conditions and an improving pricing backdrop driven by AI-related data center spending.

- Package ID：`package:0b2833b4360c3501dee4fadc`
- Family / Kind：`COMPANY_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`FROZEN`；Version：3
- 层级规模：3 Atomic / 3 Mention
- Anchor entities：COMPANY_SNDK, INSTITUTION_CITIGROUP
- Anchor artifact / period：— / —
- 摘要：Citi stated that Sandisk is supported by healthier NAND supply and demand conditions and an improving pricing backdrop driven by AI-related data center spending. Citi has issued higher financial estimates for Sandisk. Citi raised its price target for Sandisk to $2,500 from $2,025.

#### P02-A01. Citi stated that Sandisk is supported by healthier NAND supply and demand conditions and an improving pricing backdrop driven by AI-related data center spending.

- Atomic ID：`atomic:1401a6465c14baf3da7425c6`
- Family / Assertion：`COMMERCIAL_OPERATION` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：UNKNOWN

##### M01. Citi stated that Sandisk is supported by healthier NAND supply and demand conditions and an improving pricing backdrop driven by AI-related data center spending.

- Mention ID：`mention:3e43c040aad6dde89e49ab7a55a144099659c03c2d7db77c7dbb4f1f1d630b97`
- 来源：[Sandisk Stock Spikes 15% After Citi Unleashes Massive Price Target Hike](https://finance.yahoo.com/markets/stocks/articles/sandisk-stock-spikes-15-citi-170934290.html)；`doxatlas:raw_media:4a7c28e1-f1dd-4efc-8d16-80debc253418`
- Family / Assertion：`COMMERCIAL_OPERATION` / `ACTUAL`
- 参与者：Citi (ACTOR)；Sandisk (SUBJECT)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`TEXT_NOT_FOUND/VERIFIED`
  - E01 `TEXT_NOT_FOUND` / `text:0`：Citi said Sandisk remains supported by healthier NAND supply and demand conditions, with AI-related data center spending helping drive storage needs.
  - E02 `VERIFIED` / `text:0`：For Sandisk, Citi said the pricing backdrop appears to be improving as more data center operators use less costly solid-state drives to move data tied to artificial intelligence workloads.

#### P02-A02. Citi has issued higher financial estimates for Sandisk.

- Atomic ID：`atomic:58436927d5668cc2d425b420`
- Family / Assertion：`ANALYST_ACTION` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：UNKNOWN

##### M01. Citi has issued higher financial estimates for Sandisk.

- Mention ID：`mention:88ee893a95f71255b78946b6ac5622931209a47096f229b81b30227ecc2871f6`
- 来源：[Sandisk Stock Spikes 15% After Citi Unleashes Massive Price Target Hike](https://finance.yahoo.com/markets/stocks/articles/sandisk-stock-spikes-15-citi-170934290.html)；`doxatlas:raw_media:4a7c28e1-f1dd-4efc-8d16-80debc253418`
- Family / Assertion：`ANALYST_ACTION` / `ACTUAL`
- 参与者：Citi (ACTOR)；Sandisk (TARGET)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`TEXT_NOT_FOUND`
  - E01 `TEXT_NOT_FOUND` / `text:0`：Citi said those trends are feeding into higher estimates for the company.

#### P02-A03. Citi raised its price target for Sandisk to $2,500 from $2,025.

- Atomic ID：`atomic:f16e9ceb61ce7189733ef922`
- Family / Assertion：`ANALYST_ACTION` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：UNKNOWN

##### M01. Citi raised its price target for Sandisk to $2,500 from $2,025.

- Mention ID：`mention:ec7177491e2835783f6fd7087b138ab74ab6a80ddc8003d1bf9cb6d1b173b900`
- 来源：[Sandisk Stock Spikes 15% After Citi Unleashes Massive Price Target Hike](https://finance.yahoo.com/markets/stocks/articles/sandisk-stock-spikes-15-citi-170934290.html)；`doxatlas:raw_media:4a7c28e1-f1dd-4efc-8d16-80debc253418`
- Family / Assertion：`ANALYST_ACTION` / `ACTUAL`
- 参与者：Citi (ACTOR)；Sandisk (TARGET)
- 数量：$2,500 [price_target]；$2,025 [price_target]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Citi lifted its target on Sandisk to $2,500 from $2,025 and said it sees room for further gains over the next 90 days.

### P03. Memory supply capacity expansion is constrained by construction timelines, labor shortages, regulatory hurdles, and energy infrastructure requirements.

- Package ID：`package:0f0d639e5a2854c5191f2254`
- Family / Kind：`COMPANY_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：field:1418591a642b9504c964967b
- Anchor artifact / period：— / —
- 摘要：Memory supply capacity expansion is constrained by construction timelines, labor shortages, regulatory hurdles, and energy infrastructure requirements.

#### P03-A01. Memory supply capacity expansion is constrained by construction timelines, labor shortages, regulatory hurdles, and energy infrastructure requirements.

- Atomic ID：`atomic:5d1bc9b895c70b79e1e36020`
- Family / Assertion：`PRODUCTION_SUPPLY` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：UNKNOWN

##### M01. Memory supply capacity expansion is constrained by construction timelines, labor shortages, regulatory hurdles, and energy infrastructure requirements.

- Mention ID：`mention:271eb070c46ae324a66d960eed224d9f05a66ee988f89429b9fe90d66444912a`
- 来源：[Micron CEO Expects Memory Supply To Improve Gradually In 2028, But There's No 'Line Of Sight' When It Will Catch Up To Rising AI Demand](https://www.benzinga.com/markets/tech/26/06/60089761/micron-ceo-sees-memory-supply-improving-by-2028-but-no-clear-end-in-sight-to-ai-demand-shortfallmicron-ceo-sees-memory-supply-improving-by-2028-but-no-clear-end-in-sight-to-ai-demand-shortfall)；`doxatlas:raw_media:25387f96-b423-437c-b2cd-8652aa176bd8`
- Family / Assertion：`PRODUCTION_SUPPLY` / `ACTUAL`
- 参与者：Mehrotra (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：“The pace is constrained by several factors,” Mehrotra said, noting long construction timelines, labor shortages, regulatory hurdles and growing energy infrastructure requirements.

### P04. Institutional investors net purchased approximately 100 billion won worth of stocks.

- Package ID：`package:11c506e22f7333d3204765fd`
- Family / Kind：`TRANSACTION` / `EPISODE`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：field:3cf6b8d902295105670297d5
- Anchor artifact / period：— / —
- 摘要：Institutional investors net purchased approximately 100 billion won worth of stocks.

#### P04-A01. Institutional investors net purchased approximately 100 billion won worth of stocks.

- Atomic ID：`atomic:4d59d2e94336c287321cb2b9`
- Family / Assertion：`TRANSACTION_CAPITAL` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：2026-06-25T00:00:00 / DAY

##### M01. Institutional investors net purchased approximately 100 billion won worth of stocks.

- Mention ID：`mention:a391be22059d10011456edc43d8fb416cf86e2adf3f38db659a17844a05c2b4e`
- 来源：[KOSPI Spikes 5% on Opening, Riding Micron’s Surprise Earnings](https://beincrypto.com/micron-earnings-kospi-sidecar-chip-rally/)；`doxatlas:raw_media:3f25dec4-7f5f-4203-9e0f-d37c2b06a2c7`
- Family / Assertion：`TRANSACTION_CAPITAL` / `ACTUAL`
- 参与者：institutions (ACTOR)
- 数量：around 100 billion won [net_buy_value]
- 时间：2026-06-25T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：institutions added around 100 billion won.

### P05. Counterpoint estimates that higher component costs could add roughly $200 per iPhone for Apple.

- Package ID：`package:126a151abfd78a172b6df40f`
- Family / Kind：`ANALYST_REPORT` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：COMPANY_AAPL, field:6198820f5bf21326091c4885
- Anchor artifact / period：— / —
- 摘要：Counterpoint estimates that higher component costs could add roughly $200 per iPhone for Apple.

#### P05-A01. Counterpoint estimates that higher component costs could add roughly $200 per iPhone for Apple.

- Atomic ID：`atomic:ddf6aec9037a8aac2bb8f32b`
- Family / Assertion：`ANALYST_ACTION` / `EXPECTED`
- Mention 数：1；Version：1
- 时间：UNKNOWN

##### M01. Counterpoint estimates that higher component costs could add roughly $200 per iPhone for Apple.

- Mention ID：`mention:e281c3a959e2b55fb3dba6afe3931500a3ed4a831b3f615686e29db24cb90934`
- 来源：[Tim Cook Calls the Memory Crisis a "Hundred-Year Flood"](https://finance.yahoo.com/technology/articles/tim-cook-calls-memory-crisis-170140774.html)；`doxatlas:raw_media:d42584c3-2d2d-4559-affc-ccae5b388ce1`
- Family / Assertion：`ANALYST_ACTION` / `EXPECTED`
- 参与者：Counterpoint (ACTOR)；Apple (AFFECTED)
- 数量：$200 [cost_increase_per_unit]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Counterpoint estimates the higher component costs could add roughly $200 per iPhone for Apple, with price increases of $150-$200 expected across the lineup.

### P06. Continued advances in simulation, foundation models and integrated hardware and software are accelerating the development of physical AI.

- Package ID：`package:1520205d6829557ee6fc2898`
- Family / Kind：`PRODUCT_SCIENCE` / `EPISODE`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：field:3b83193ce11da12ca71c4a62, field:cee7bcb4c0f56dc02c869def
- Anchor artifact / period：— / —
- 摘要：Continued advances in simulation, foundation models and integrated hardware and software are accelerating the development of physical AI.

#### P06-A01. Continued advances in simulation, foundation models and integrated hardware and software are accelerating the development of physical AI.

- Atomic ID：`atomic:f62c84a089a6c02e12062594`
- Family / Assertion：`PRODUCT_SCIENCE` / `ONGOING`
- Mention 数：1；Version：1
- 时间：UNKNOWN

##### M01. Continued advances in simulation, foundation models and integrated hardware and software are accelerating the development of physical AI.

- Mention ID：`mention:66306e7648ba7b20feb923060b8f3daa115af63474459d314e246b1085ce0f1e`
- 来源：[Tesla's Optimus Could Become A Bigger Memory Customer Than Its Cars, If Micron Is Right](https://www.benzinga.com/trading-ideas/long-ideas/26/06/60094569/tesla-optimus-could-become-a-bigger-memory-customer-than-its-cars-if-micron-is-right)；`doxatlas:raw_media:005d3637-8724-483c-9077-7f643d3fa481`
- Family / Assertion：`PRODUCT_SCIENCE` / `ONGOING`
- 参与者：advances in simulation, foundation models and integrated hardware and software (ACTOR)；development of physical AI (SUBJECT)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：According to Mehrotra, continued advances in simulation, foundation models and integrated hardware and software are accelerating the development of physical AI, creating “a growing content-rich opportunity for high-bandwidth, low-power memory and storage that powers real-time perception, inference, and control.”

### P07. The consensus analyst rating on Micron stock is Strong Buy.

- Package ID：`package:1aee0236cc43f420440066c8`
- Family / Kind：`ANALYST_REPORT` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：COMPANY_MU
- Anchor artifact / period：— / —
- 摘要：The consensus analyst rating on Micron stock is Strong Buy.

#### P07-A01. The consensus analyst rating on Micron stock is Strong Buy.

- Atomic ID：`atomic:04df2b9b7a3482b662ed3ff2`
- Family / Assertion：`ANALYST_ACTION` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY

##### M01. The consensus analyst rating on Micron stock is Strong Buy.

- Mention ID：`mention:5a701c15ca915024a3531d3e7db698857c2dd86ff5df9abdf41d40e048cf7c5c`
- 来源：[MU Stock Soars as Micron’s Revenue More Than Quadrupled in Q3](https://www.barchart.com/story/news/3092/mu-stock-soars-as-microns-revenue-more-than-quadrupled-in-q3)；`doxatlas:raw_media:181db9d6-5959-4fb0-947e-3c6680861566`
- Family / Assertion：`ANALYST_ACTION` / `ACTUAL`
- 参与者：MU (SUBJECT)
- 数量：—
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：The consensus rating on MU stock sits at “Strong Buy” currently,

### P08. Bank of America reiterated its Buy rating on Micron and raised its price target to $1,550 from $1,500.

- Package ID：`package:1c31e2aabd33d49da232b612`
- Family / Kind：`ANALYST_REPORT` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：2
- 层级规模：2 Atomic / 2 Mention
- Anchor entities：COMPANY_MU, INSTITUTION_BANK_OF_AMERICA
- Anchor artifact / period：— / —
- 摘要：Bank of America reiterated its Buy rating on Micron and raised its price target to $1,550 from $1,500. Bank of America sees Micron shares implying a roughly 10% free cash flow yield at current levels.

#### P08-A01. Bank of America reiterated its Buy rating on Micron and raised its price target to $1,550 from $1,500.

- Atomic ID：`atomic:24388eaf10805b89b44334ac`
- Family / Assertion：`ANALYST_ACTION` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：UNKNOWN

##### M01. Bank of America reiterated its Buy rating on Micron and raised its price target to $1,550 from $1,500.

- Mention ID：`mention:c03f6fb304a85b18222850ff627f130ce30caeb6c1a608affc4b784ba122fd24`
- 来源：[Micron shares surge to all-time high as Wall Street cheers unprecedented contract visibility](https://www.proactiveinvestors.com/companies/news/1094520/micron-shares-surge-to-all-time-high-as-wall-street-cheers-unprecedented-contract-visibility-1094520.html)；`doxatlas:raw_media:6b5d042a-9236-453b-8b60-2ba6c2f16b4f`
- Family / Assertion：`ANALYST_ACTION` / `ACTUAL`
- 参与者：Bank of America (ACTOR)；Micron (TARGET)
- 数量：$1,550 [price_target]；$1,500 [prior_price_target]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Bank of America reiterated its Buy rating and lifted its price target to $1,550 from $1,500

#### P08-A02. Bank of America sees Micron shares implying a roughly 10% free cash flow yield at current levels.

- Atomic ID：`atomic:b95ba17a063d919f90a047f7`
- Family / Assertion：`ANALYST_ACTION` / `EXPECTED`
- Mention 数：1；Version：1
- 时间：UNKNOWN

##### M01. Bank of America sees Micron shares implying a roughly 10% free cash flow yield at current levels.

- Mention ID：`mention:4533359bb07bdbfb573f985672dce93571cae40f04e6e3a4699dda33dda77e4a`
- 来源：[Micron shares surge to all-time high as Wall Street cheers unprecedented contract visibility](https://www.proactiveinvestors.com/companies/news/1094520/micron-shares-surge-to-all-time-high-as-wall-street-cheers-unprecedented-contract-visibility-1094520.html)；`doxatlas:raw_media:6b5d042a-9236-453b-8b60-2ba6c2f16b4f`
- Family / Assertion：`ANALYST_ACTION` / `EXPECTED`
- 参与者：Bank of America (ACTOR)；Micron (SUBJECT)
- 数量：10% [free_cash_flow_yield]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：The firm sees shares implying a roughly 10% free cash flow yield at current levels.

### P09. UBS tripled its price target for Micron.

- Package ID：`package:1cc7ec8427d61fede79c1ef7`
- Family / Kind：`ANALYST_REPORT` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：2
- 层级规模：2 Atomic / 2 Mention
- Anchor entities：COMPANY_MU, INSTITUTION_UBS
- Anchor artifact / period：— / —
- 摘要：UBS tripled its price target for Micron. UBS analysts stated that DRAM supply is likely to be constrained until at least halfway through 2028 and NAND supply until at least the end of 2027.

#### P09-A01. UBS tripled its price target for Micron.

- Atomic ID：`atomic:8ea97047a4ab0e3abefc5fea`
- Family / Assertion：`ANALYST_ACTION` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：last month / UNKNOWN

##### M01. UBS tripled its price target for Micron.

- Mention ID：`mention:07e2b48e99cea0aa19211159e44cacb713b2f7a9301212bc47a895f0fe8269d3`
- 来源：[Micron Just Locked In $100 Billion in Sales, and Wall Street Thinks the Boom-Bust Chip Cycle Is Dead](https://247wallst.com/investing/2026/06/25/micron-just-locked-in-100-billion-in-sales-and-wall-street-thinks-the-boom-bust-chip-cycle-is-dead/)；`doxatlas:raw_media:1e515897-64d4-4896-b936-10a04501eff4`
- Family / Assertion：`ANALYST_ACTION` / `ACTUAL`
- 参与者：UBS (ACTOR)；Micron (SUBJECT)
- 数量：—
- 时间：last month / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：UBS tripled Micron’s price target last month

#### P09-A02. UBS analysts stated that DRAM supply is likely to be constrained until at least halfway through 2028 and NAND supply until at least the end of 2027.

- Atomic ID：`atomic:ea68b6917e3d3ad41b224fac`
- Family / Assertion：`ANALYST_ACTION` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：UNKNOWN

##### M01. UBS analysts stated that DRAM supply is likely to be constrained until at least halfway through 2028 and NAND supply until at least the end of 2027.

- Mention ID：`mention:3dd6f4ff86505876dccc75210f3eed854a2ee368868c54dc76848bac2a34a82d`
- 来源：[Why Everyone Is Talking About Micron](https://www.fool.com/investing/2026/06/25/why-everyone-is-talking-about-micron/)；`doxatlas:raw_media:49fb27c5-84b4-4e56-8791-f7ac23de38b7`
- Family / Assertion：`ANALYST_ACTION` / `ACTUAL`
- 参与者：UBS (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Analysts at UBS have previously said that DRAM is likely to be constrained until at least halfway through 2028
  - E02 `VERIFIED` / `text:0`：, while NAND is likely to be constrained until at least the end of 2027.

### P10. The Nasdaq Composite index fell 0.46% to close at 25,359.

- Package ID：`package:20f71cffecf7b5e08efeb4a9`
- Family / Kind：`COMPANY_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：INSTRUMENT_NASDAQ_COMPOSITE
- Anchor artifact / period：— / —
- 摘要：The Nasdaq Composite index fell 0.46% to close at 25,359.

#### P10-A01. The Nasdaq Composite index fell 0.46% to close at 25,359.

- Atomic ID：`atomic:ecd4bfca388e6c463d41fc5e`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY

##### M01. The Nasdaq Composite index fell 0.46% to close at 25,359.

- Mention ID：`mention:98f126520b46c6b30a6cd9442d88607a3c5f14d0edac69e54250ada1ebdfe383`
- 来源：[Stock Market Today, June 25: Apple Drops After Raising Device Prices to Offset Higher Memory Costs](https://www.fool.com/coverage/stock-market-today/2026/06/25/stock-market-today-june-25-apple-drops-after-raising-device-prices-to-offset-higher-memory-costs/)；`doxatlas:raw_media:d9404d58-cd88-4ccc-a9d2-ec4004629dbc`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Nasdaq Composite (SUBJECT)
- 数量：25,359 [index_close_value]；fell 0.46% [index_change_percent]
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：the Nasdaq Composite (^IXIC 0.80%) fell 0.46% to 25,359

### P11. Sandisk is scheduled to hold an investor day in August where it may provide updates on demand expectations, technology roadmap, and capital return plans.

- Package ID：`package:322909230554019675eaa014`
- Family / Kind：`COMPANY_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：COMPANY_SNDK
- Anchor artifact / period：— / —
- 摘要：Sandisk is scheduled to hold an investor day in August where it may provide updates on demand expectations, technology roadmap, and capital return plans.

#### P11-A01. Sandisk is scheduled to hold an investor day in August where it may provide updates on demand expectations, technology roadmap, and capital return plans.

- Atomic ID：`atomic:cf0abb6391e13069cabe2957`
- Family / Assertion：`GOVERNANCE_PERSONNEL` / `PLANNED`
- Mention 数：1；Version：1
- 时间：2026-08-01T00:00:00 / 2026-08-31T00:00:00 / MONTH

##### M01. Sandisk is scheduled to hold an investor day in August where it may provide updates on demand expectations, technology roadmap, and capital return plans.

- Mention ID：`mention:808830000e416ad35004411083ae502dc696839542f10f0b5a27fd2ad41d1049`
- 来源：[Sandisk Stock Spikes 15% After Citi Unleashes Massive Price Target Hike](https://finance.yahoo.com/markets/stocks/articles/sandisk-stock-spikes-15-citi-170934290.html)；`doxatlas:raw_media:4a7c28e1-f1dd-4efc-8d16-80debc253418`
- Family / Assertion：`GOVERNANCE_PERSONNEL` / `PLANNED`
- 参与者：Sandisk (ACTOR)
- 数量：—
- 时间：2026-08-01T00:00:00 / 2026-08-31T00:00:00 / MONTH
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Sandisk may also get another lift from its investor day in August.
  - E02 `VERIFIED` / `text:0`：Citi said the event could bring updates on demand expectations, the company's technology roadmap and capital return plans, all of which could help shape the next phase of the stock's move.

### P12. Alphabet stock ended at $343.71, representing a 0.46% decline.

- Package ID：`package:34aa07329c6208f58784d9fb`
- Family / Kind：`COMPANY_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：COMPANY_GOOG
- Anchor artifact / period：— / —
- 摘要：Alphabet stock ended at $343.71, representing a 0.46% decline.

#### P12-A01. Alphabet stock ended at $343.71, representing a 0.46% decline.

- Atomic ID：`atomic:90ac199ca9f59bd55f4f6ee0`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY

##### M01. Alphabet stock ended at $343.71, representing a 0.46% decline.

- Mention ID：`mention:c7ea0171b98ee192c82d19a206f76d5b3516de8dc6b2165fb2745765b2a20c97`
- 来源：[Stock Market Today, June 25: Apple Drops After Raising Device Prices to Offset Higher Memory Costs](https://www.fool.com/coverage/stock-market-today/2026/06/25/stock-market-today-june-25-apple-drops-after-raising-device-prices-to-offset-higher-memory-costs/)；`doxatlas:raw_media:d9404d58-cd88-4ccc-a9d2-ec4004629dbc`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Alphabet (SUBJECT)
- 数量：$343.71 [closing_price]；down 0.46% [price_change_percent]
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Alphabet (GOOGL 0.23%) ended at $343.71, down 0.46%

### P13. Micron management committed to returning 100% of excess cash to shareholders, increasing from the prior 50% policy.

- Package ID：`package:371809e25dfc7cf666bb1332`
- Family / Kind：`TRANSACTION` / `EPISODE`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：3
- 层级规模：2 Atomic / 4 Mention
- Anchor entities：COMPANY_MU, field:467688e04f9945cb3e030d92, field:d4fa06736fa75b5b2c1ad9be
- Anchor artifact / period：— / —
- 摘要：Micron management committed to returning 100% of excess cash to shareholders, increasing from the prior 50% policy. Micron plans to begin increasing capital returns on Dec. 9, 2026, and expects to return 100% of excess cash to shareholders.

#### P13-A01. Micron management committed to returning 100% of excess cash to shareholders, increasing from the prior 50% policy.

- Atomic ID：`atomic:26eed389d1920732da3dc9a6`
- Family / Assertion：`GOVERNANCE_PERSONNEL` / `PLANNED`
- Mention 数：1；Version：1
- 时间：UNKNOWN

##### M01. Micron management committed to returning 100% of excess cash to shareholders, increasing from the prior 50% policy.

- Mention ID：`mention:d087b8f0d9c9db5e22f713f82797ad92b378bc9148dba900b1a6fe11b3a85bb4`
- 来源：[MU Stock Soars as Micron’s Revenue More Than Quadrupled in Q3](https://www.barchart.com/story/news/3092/mu-stock-soars-as-microns-revenue-more-than-quadrupled-in-q3)；`doxatlas:raw_media:181db9d6-5959-4fb0-947e-3c6680861566`
- Family / Assertion：`GOVERNANCE_PERSONNEL` / `PLANNED`
- 参与者：Micron management (ACTOR)
- 数量：100% [excess_cash_return_rate]；prior 50% [prior_excess_cash_return_rate]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Management has also committed to returning 100% excess cash to shareholders moving forward, up from prior 50%, which makes MU shares even more attractive as a long-term holding.

#### P13-A02. Micron plans to begin increasing capital returns on Dec. 9, 2026, and expects to return 100% of excess cash to shareholders.

- Atomic ID：`atomic:b71e302370f0291210260af2`
- Family / Assertion：`TRANSACTION_CAPITAL` / `PLANNED`
- Mention 数：3；Version：3
- 时间：2026-12-01T00:00:00 / DAY

##### M01. Micron plans to begin increasing capital returns on Dec. 9, 2026, and expects to return 100% of excess cash to shareholders.

- Mention ID：`mention:58c4a5c6f3d950b9832d7c87d8f2f5ef87df1b5e100eade5c7dea1e95a91329e`
- 来源：[Micron Is Spending Billions On New Fabs—And Still Says It'll Return 100% Of Excess Cash To Shareholders](https://www.benzinga.com/trading-ideas/long-ideas/26/06/60095272/micron-is-spending-billions-on-new-fabs-and-still-says-itll-return-100-of-excess-cash-to-shareholders)；`doxatlas:raw_media:4fcc4fb0-ab7d-4157-8e65-a09ef40a7581`
- Family / Assertion：`TRANSACTION_CAPITAL` / `PLANNED`
- 参与者：Micron (ACTOR)；shareholders (TARGET)
- 数量：100% [excess_cash_return_rate]
- 时间：2026-12-09T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：beginning Dec. 9, 2026—the second anniversary of the company’s definitive CHIPS Act agreements—Micron plans to increase capital returns over time
  - E02 `VERIFIED` / `text:0`：“We expect to return 100% of our excess cash to shareholders,”

##### M02. Micron intends to return all excess cash to shareholders.

- Mention ID：`mention:192e1eeee4be976a393922397455dcfb026b0d6c1990e9aa022ee13d3fbdc89b`
- 来源：[Wedbush says chip rally has further to run after Micron's blowout quarter](https://www.proactiveinvestors.com/companies/news/1094490/wedbush-says-chip-rally-has-further-to-run-after-micron-s-blowout-quarter-1094490.html)；`doxatlas:raw_media:79fe1f85-a1cc-44e1-928b-d0256c075eb4`
- Family / Assertion：`TRANSACTION_CAPITAL` / `PLANNED`
- 参与者：Micron (ACTOR)；shareholders (TARGET)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron also said it intends to return all excess cash to shareholders as free cash flow builds.

##### M03. Micron announced plans to return 100% of excess free cash flow to shareholders beginning in December 2026.

- Mention ID：`mention:bdbcaa99155ded67a58d5056de37a5289c48a917461eb24ddc792fea3fa06a8d`
- 来源：[Micron shares surge to all-time high as Wall Street cheers unprecedented contract visibility](https://www.proactiveinvestors.com/companies/news/1094520/micron-shares-surge-to-all-time-high-as-wall-street-cheers-unprecedented-contract-visibility-1094520.html)；`doxatlas:raw_media:6b5d042a-9236-453b-8b60-2ba6c2f16b4f`
- Family / Assertion：`TRANSACTION_CAPITAL` / `PLANNED`
- 参与者：Micron (ACTOR)
- 数量：100% [excess_fcf_payout_ratio]
- 时间：2026-12-01T00:00:00 / MONTH
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron announced plans to return 100% of excess free cash flow to shareholders beginning in December, once CHIPS Act restrictions on certain uses of cash expire.

### P14. Micron's stock price increased, adding more than $100 billion in market value.

- Package ID：`package:3785f9b0cdc53dcfeaa79b55`
- Family / Kind：`COMPANY_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：6
- 层级规模：6 Atomic / 6 Mention
- Anchor entities：COMPANY_MU
- Anchor artifact / period：— / —
- 摘要：Micron's stock price increased, adding more than $100 billion in market value. Micron's stock has risen nearly 1,000% over the past year. Micron's shares gained more than 260% year-to-date.

#### P14-A01. Micron's stock price increased, adding more than $100 billion in market value.

- Atomic ID：`atomic:2c45a2c8d5da378bc01684c3`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY

##### M01. Micron's stock price increased, adding more than $100 billion in market value.

- Mention ID：`mention:94e10beb59e29580c0f663c7bf44078e585409b90c1658d9e2087c20ffdb6dff`
- 来源：[Why Micron's blowout earnings are a headache for Apple](https://finance.yahoo.com/markets/article/why-microns-blowout-earnings-are-a-headache-for-apple-161932441.html)；`doxatlas:raw_media:6ccb4979-525c-4467-a381-538f38a4fa9c`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Micron (SUBJECT)
- 数量：more than $100 billion [market_value_change]
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron has added more than $100 billion in market value Thursday even after giving back part of its early surge.

#### P14-A02. Micron's stock has risen nearly 1,000% over the past year.

- Atomic ID：`atomic:517c2497441cef1965512314`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：UNKNOWN

##### M01. Micron's stock has risen nearly 1,000% over the past year.

- Mention ID：`mention:88cb3140519038ea80a2796a089cbf41a8f80b85e98b1f00703264d400ab9894`
- 来源：[4 Blowout Numbers From Micron's Earnings Investors Need To See](https://www.fool.com/investing/2026/06/25/x-blowout-numbers-from-microns-earnings-investors/)；`doxatlas:raw_media:34fc2a61-fa6e-4db0-8e39-f862da0af41c`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Micron (SUBJECT)
- 数量：nearly 1,000% [stock_price_change_pct]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：the stock jumping nearly 1,000%

#### P14-A03. Micron's shares gained more than 260% year-to-date.

- Atomic ID：`atomic:6f1f6ecadb4bb49ad1f07f4b`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：2026-01-01T00:00:00 / 2026-06-25T00:00:00 / INTERVAL

##### M01. Micron's shares gained more than 260% year-to-date.

- Mention ID：`mention:7b71ed0d69fd0aa29646cd8df72130a89bca9da519f6792bfc569ec78335103c`
- 来源：[Is Micron a Buy After Its Blowout Earnings Report?](https://www.fool.com/investing/2026/06/25/is-micron-a-buy-after-its-blowout-earnings-report/)；`doxatlas:raw_media:a424d484-3e57-47ed-b21d-50aeceafe593`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Micron Technology (SUBJECT)
- 数量：more than 260% [ytd_return]
- 时间：2026-01-01T00:00:00 / 2026-06-25T00:00:00 / INTERVAL
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：The shares have skyrocketed in recent times, gaining more than 260% this year alone.

#### P14-A04. Micron Technology Inc shares surged more than 15% to a record high of around $1,208 on June 25, 2026.

- Atomic ID：`atomic:8f414a30b0f155725f28285f`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY

##### M01. Micron Technology Inc shares surged more than 15% to a record high of around $1,208 on June 25, 2026.

- Mention ID：`mention:0aab9f0f8e7ea4e41a956fd7be9c5b4e257cf4bd35ae746c530857c2a8236ed0`
- 来源：[Micron shares surge to all-time high as Wall Street cheers unprecedented contract visibility](https://www.proactiveinvestors.com/companies/news/1094520/micron-shares-surge-to-all-time-high-as-wall-street-cheers-unprecedented-contract-visibility-1094520.html)；`doxatlas:raw_media:6b5d042a-9236-453b-8b60-2ba6c2f16b4f`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Micron Technology Inc (SUBJECT)
- 数量：$1,208 [stock_price]；more than 15% [price_change_percent]
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron shares surge to all-time high as Wall Street cheers unprecedented contract visibility
  - E02 `VERIFIED` / `text:0`：Published: 12:27 25 Jun 2026 EDT Micron Technology Inc (NASDAQ:MU) shares soared more than 15% to a record high of around $1,208 Thursday

#### P14-A05. Micron's Relative Strength Index (RSI) reached the mid-60s.

- Atomic ID：`atomic:9ae9cf82c1368908a73c3ec5`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY

##### M01. Micron's Relative Strength Index (RSI) reached the mid-60s.

- Mention ID：`mention:d0036201fafaae9f7d6788e14277fcd2c4d2a82dcd30d7970f3680f153bf63fa`
- 来源：[MU Stock Soars as Micron’s Revenue More Than Quadrupled in Q3](https://www.barchart.com/story/news/3092/mu-stock-soars-as-microns-revenue-more-than-quadrupled-in-q3)；`doxatlas:raw_media:181db9d6-5959-4fb0-947e-3c6680861566`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：MU (SUBJECT)
- 数量：mid-60s [rsi]
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：MU’s relative strength index (RSI) soared into the mid-60s, signaling the stock is now approaching “overbought” territory.

#### P14-A06. Micron's market capitalization crossed $1 trillion.

- Atomic ID：`atomic:cd77cfae9096463dc602d50b`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：UNKNOWN

##### M01. Micron's market capitalization crossed $1 trillion.

- Mention ID：`mention:8430a518bc8e1d879fb90b2a511380b63396314ce7a0c4cb6ec127b1963dd916`
- 来源：[Micron Just Locked In $100 Billion in Sales, and Wall Street Thinks the Boom-Bust Chip Cycle Is Dead](https://247wallst.com/investing/2026/06/25/micron-just-locked-in-100-billion-in-sales-and-wall-street-thinks-the-boom-bust-chip-cycle-is-dead/)；`doxatlas:raw_media:1e515897-64d4-4896-b936-10a04501eff4`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Micron (SUBJECT)
- 数量：$1 trillion [market_cap]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：the stock crossed $1 trillion in market cap alongside SK Hynix.

### P15. Cloud providers and AI developers are expanding data center capacity for generative AI, driving demand for advanced DRAM and NAND memory and tightening industry supply.

- Package ID：`package:419e02bc22e774e3f65fce7e`
- Family / Kind：`COMPANY_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：2
- 层级规模：2 Atomic / 2 Mention
- Anchor entities：field:c02c0f55cc6762c5f5fc293b, field:cbe07fcfc3477dfae2bc9608, field:de21ef673f4a8c07b3b4a7dd
- Anchor artifact / period：— / —
- 摘要：Cloud providers and AI developers are expanding data center capacity for generative AI, driving demand for advanced DRAM and NAND memory and tightening industry supply. Memory and storage prices quadrupled in the past three quarters.

#### P15-A01. Cloud providers and AI developers are expanding data center capacity for generative AI, driving demand for advanced DRAM and NAND memory and tightening industry supply.

- Atomic ID：`atomic:331c0aac04dcb9f84109fed9`
- Family / Assertion：`PRODUCTION_SUPPLY` / `ONGOING`
- Mention 数：1；Version：1
- 时间：UNKNOWN

##### M01. Cloud providers and AI developers are expanding data center capacity for generative AI, driving demand for advanced DRAM and NAND memory and tightening industry supply.

- Mention ID：`mention:c2de44674d72f6fb01599b103da39d6dfe43a1e72c308a778a4d3a647de50319`
- 来源：[Apple Just Confirmed Micron's Biggest AI Prediction](https://www.benzinga.com/trading-ideas/long-ideas/26/06/60108831/apple-just-confirmed-microns-biggest-ai-prediction)；`doxatlas:raw_media:35c3d868-abe6-4617-8c1f-c1460a8e8179`
- Family / Assertion：`PRODUCTION_SUPPLY` / `ONGOING`
- 参与者：Cloud providers (ACTOR)；AI developers (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Cloud providers and AI developers have been aggressively expanding data center capacity to support generative AI applications, fueling demand for advanced DRAM and NAND memory.
  - E02 `VERIFIED` / `text:0`：That demand has helped tighten industry supply, allowing memory manufacturers to secure stronger pricing and long-term customer agreements.

#### P15-A02. Memory and storage prices quadrupled in the past three quarters.

- Atomic ID：`atomic:d46e323fd4b99f86becc33cd`
- Family / Assertion：`PRODUCTION_SUPPLY` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：UNKNOWN

##### M01. Memory and storage prices quadrupled in the past three quarters.

- Mention ID：`mention:6beb4471e15a430ec380797b572fa200741136389ac542fb6d8ceffdd8294aaa`
- 来源：[Tim Cook Calls the Memory Crisis a "Hundred-Year Flood"](https://finance.yahoo.com/technology/articles/tim-cook-calls-memory-crisis-170140774.html)；`doxatlas:raw_media:d42584c3-2d2d-4559-affc-ccae5b388ce1`
- Family / Assertion：`PRODUCTION_SUPPLY` / `ACTUAL`
- 参与者：Memory and storage (SUBJECT)
- 数量：quadrupled [price_change_factor]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Memory and storage prices have quadrupled in the past three quarters, according to Counterpoint Research, as suppliers redirect production toward high-bandwidth memory used in AI servers.

### P16. Qualcomm CEO Cristiano Amon stated that Qualcomm has secured capacity from manufacturers and expressed confidence in its forecast.

- Package ID：`package:41ff7661a14b7be96a1d5275`
- Family / Kind：`COMPANY_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：COMPANY_QCOM
- Anchor artifact / period：— / —
- 摘要：Qualcomm CEO Cristiano Amon stated that Qualcomm has secured capacity from manufacturers and expressed confidence in its forecast.

#### P16-A01. Qualcomm CEO Cristiano Amon stated that Qualcomm has secured capacity from manufacturers and expressed confidence in its forecast.

- Atomic ID：`atomic:dc8efc184d09f02e3da12635`
- Family / Assertion：`PRODUCTION_SUPPLY` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：UNKNOWN

##### M01. Qualcomm CEO Cristiano Amon stated that Qualcomm has secured capacity from manufacturers and expressed confidence in its forecast.

- Mention ID：`mention:7b368f50702d5b16445c89556d8dc7e7e03f990fa43594b61fd0653ee3690c31`
- 来源：[Micron Just Locked In $100 Billion in Sales, and Wall Street Thinks the Boom-Bust Chip Cycle Is Dead](https://247wallst.com/investing/2026/06/25/micron-just-locked-in-100-billion-in-sales-and-wall-street-thinks-the-boom-bust-chip-cycle-is-dead/)；`doxatlas:raw_media:1e515897-64d4-4896-b936-10a04501eff4`
- Family / Assertion：`PRODUCTION_SUPPLY` / `ACTUAL`
- 参与者：Qualcomm (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：“I have secure the capacity from the manufacturer as well as memory,” he said, “we’re very confident in the forecast we provided.”

### P17. Wedbush rates Micron at outperform with a price target of $1,300.

- Package ID：`package:4650ebb82cd9ee3364fa682c`
- Family / Kind：`ANALYST_REPORT` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`FROZEN`；Version：4
- 层级规模：3 Atomic / 4 Mention
- Anchor entities：COMPANY_MFG, COMPANY_MU, field:adfc043125dadab998cd6d80
- Anchor artifact / period：— / —
- 摘要：Wedbush rates Micron at outperform with a price target of $1,300. Mizuho maintains an Outperform rating and raises the price target for Micron Technology to $1375. Micron trades at 16x forward earnings estimates.

#### P17-A01. Wedbush rates Micron at outperform with a price target of $1,300.

- Atomic ID：`atomic:43d4f0e877a8405b9175168a`
- Family / Assertion：`ANALYST_ACTION` / `ACTUAL`
- Mention 数：2；Version：2
- 时间：UNKNOWN

##### M01. Wedbush rates Micron at outperform with a price target of $1,300.

- Mention ID：`mention:4585d21dd3343ea62f28fb723cd8c4fd55a5abf07215f569b405a383f5351a93`
- 来源：[Wedbush says chip rally has further to run after Micron's blowout quarter](https://www.proactiveinvestors.com/companies/news/1094490/wedbush-says-chip-rally-has-further-to-run-after-micron-s-blowout-quarter-1094490.html)；`doxatlas:raw_media:79fe1f85-a1cc-44e1-928b-d0256c075eb4`
- Family / Assertion：`ANALYST_ACTION` / `ACTUAL`
- 参与者：Wedbush (ACTOR)；Micron (TARGET)
- 数量：$1,300 [price_target]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：The broker rates Micron at outperform with a price target of $1,300, against a recent price of about $1,052.

##### M02. Wedbush maintained its bullish stance on Micron.

- Mention ID：`mention:da384436df5dc5cea40deb4b15a106ebe0b88664ba3bc71caa79a9b61cc3e234`
- 来源：[Micron shares surge to all-time high as Wall Street cheers unprecedented contract visibility](https://www.proactiveinvestors.com/companies/news/1094520/micron-shares-surge-to-all-time-high-as-wall-street-cheers-unprecedented-contract-visibility-1094520.html)；`doxatlas:raw_media:6b5d042a-9236-453b-8b60-2ba6c2f16b4f`
- Family / Assertion：`ANALYST_ACTION` / `ACTUAL`
- 参与者：Wedbush (ACTOR)；Micron (TARGET)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Wedbush maintained its bullish stance

#### P17-A02. Mizuho maintains an Outperform rating and raises the price target for Micron Technology to $1375.

- Atomic ID：`atomic:919286775472d1df6f8785df`
- Family / Assertion：`ANALYST_ACTION` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：UNKNOWN

##### M01. Mizuho maintains an Outperform rating and raises the price target for Micron Technology to $1375.

- Mention ID：`mention:deaedd73e115e9ad5c548740f2bca423cec08ece5e78b45247fc1b3481555242`
- 来源：[Mizuho Maintains Outperform on Micron Technology, Raises Price Target to $1375](https://www.benzinga.com/news/26/06/60106539/mizuho-maintains-outperform-micron-technology-raises-price-target-1375)；`doxatlas:raw_media:1516d084-62ab-4c8a-a7f3-e635c464425a`
- Family / Assertion：`ANALYST_ACTION` / `ACTUAL`
- 参与者：Mizuho (ACTOR)；Micron Technology (SUBJECT)
- 数量：$1375 [price_target]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `title:0`：Mizuho Maintains Outperform on Micron Technology, Raises Price Target to $1375

#### P17-A03. Micron trades at 16x forward earnings estimates.

- Atomic ID：`atomic:d84a02e62edeeec32a670bb8`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：UNKNOWN

##### M01. Micron trades at 16x forward earnings estimates.

- Mention ID：`mention:a8ca5523425d4e3b63d5fbc52cec2447f7d49e48b99c9cd195de8fa189b428f3`
- 来源：[Is Micron a Buy After Its Blowout Earnings Report?](https://www.fool.com/investing/2026/06/25/is-micron-a-buy-after-its-blowout-earnings-report/)；`doxatlas:raw_media:a424d484-3e57-47ed-b21d-50aeceafe593`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Micron Technology (SUBJECT)
- 数量：16x [forward_pe_ratio]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron trades at 16x forward earnings estimates, which is lower than levels just a year ago

### P18. The KOSPI index increased by more than 5% at the market open on June 25, 2026, rising above 8,900 from a previous close of 8,400.

- Package ID：`package:4d41545c77942ea639987057`
- Family / Kind：`COMPANY_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：2
- 层级规模：1 Atomic / 2 Mention
- Anchor entities：field:e1237b3941bfac8e9a75a1ba
- Anchor artifact / period：— / —
- 摘要：The KOSPI index increased by more than 5% at the market open on June 25, 2026, rising above 8,900 from a previous close of 8,400.

#### P18-A01. The KOSPI index increased by more than 5% at the market open on June 25, 2026, rising above 8,900 from a previous close of 8,400.

- Atomic ID：`atomic:74e94df5229fadefe8939349`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- Mention 数：2；Version：2
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY

##### M01. The KOSPI index increased by more than 5% at the market open on June 25, 2026, rising above 8,900 from a previous close of 8,400.

- Mention ID：`mention:940f64158016868be2b34550c347053b28b0e0007484bc27cadbe15875c361c3`
- 来源：[KOSPI Spikes 5% on Opening, Riding Micron’s Surprise Earnings](https://beincrypto.com/micron-earnings-kospi-sidecar-chip-rally/)；`doxatlas:raw_media:3f25dec4-7f5f-4203-9e0f-d37c2b06a2c7`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：KOSPI (SUBJECT)
- 数量：more than 5% [index_change_percent]；above 8,900 [index_level]；from 8,400 [index_level_prior]
- 时间：2026-06-25T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：South Korea’s KOSPI surged more than 5% at the open on June 25, pushing back above 8,900 from 8,400 the prior session.

##### M02. South Korea's Kospi index increased by 5.4%.

- Mention ID：`mention:09ea01f3ba45bc7f735f51d5cda3016a39f2c04c098f61e44838358f2dce7859`
- 来源：[Investors bet on AI again after Micron reports 346% sales jump](https://edition.cnn.com/2026/06/25/business/micron-results-ai-stocks-volatility)；`doxatlas:raw_media:1aaec309-8ccb-42d6-b974-b4aa3bf2d43c`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Kospi (SUBJECT)
- 数量：5.4% higher [index_change_pct]
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：South Korea’s Kospi finished 5.4% higher

### P19. Analysts forecast Sandisk's earnings to reach $33.72 per share, representing a sequential doubling.

- Package ID：`package:4f609f4b264d091b1951dc90`
- Family / Kind：`EARNINGS_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：2
- 层级规模：2 Atomic / 2 Mention
- Anchor entities：COMPANY_SNDK
- Anchor artifact / period：— / —
- 摘要：Analysts forecast Sandisk's earnings to reach $33.72 per share, representing a sequential doubling. Sandisk is scheduled to report earnings on August 24, 2026.

#### P19-A01. Analysts forecast Sandisk's earnings to reach $33.72 per share, representing a sequential doubling.

- Atomic ID：`atomic:09fa64aa869f83a8757d2f8f`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- Mention 数：1；Version：1
- 时间：UNKNOWN

##### M01. Analysts forecast Sandisk's earnings to reach $33.72 per share, representing a sequential doubling.

- Mention ID：`mention:70679e11ea30610523b963b04f33913a8cabc7ccb5d42f3d32a0ea3857b28b98`
- 来源：[Why Sandisk Stock Soared Today](https://www.fool.com/investing/2026/06/25/why-sandisk-stock-soared-today/)；`doxatlas:raw_media:35a4333e-6608-482b-b963-ba2f64392063`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- 参与者：Sandisk (SUBJECT)
- 数量：$33.72 per share [eps_forecast]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：analysts are expecting big things, with earnings forecast to more than double sequentially to $33.72 per share.

#### P19-A02. Sandisk is scheduled to report earnings on August 24, 2026.

- Atomic ID：`atomic:f1b6661a6476fae91f4b783c`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `PLANNED`
- Mention 数：1；Version：1
- 时间：2026-08-24T00:00:00 / DAY

##### M01. Sandisk is scheduled to report earnings on August 24, 2026.

- Mention ID：`mention:e9c9e1b8ecd2de2d908f8e2adaaf603aac4285c9ed5bb4259197171138111b7d`
- 来源：[Why Sandisk Stock Soared Today](https://www.fool.com/investing/2026/06/25/why-sandisk-stock-soared-today/)；`doxatlas:raw_media:35a4333e-6608-482b-b963-ba2f64392063`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `PLANNED`
- 参与者：Sandisk (ACTOR)
- 数量：—
- 时间：2026-08-24T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Sandisk itself doesn't report earnings again for another couple of months, on Aug. 24.

### P20. NASDAQ index increased by 0.7% to reach 25,654.49

- Package ID：`package:51f6b21bb3eac65484c776e9`
- Family / Kind：`COMPANY_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：2
- 层级规模：2 Atomic / 2 Mention
- Anchor entities：field:be1ef5a7afb6bf47f9ea20fc
- Anchor artifact / period：— / —
- 摘要：NASDAQ index increased by 0.7% to reach 25,654.49 Nasdaq index increased by 2.15% in pre-market trade.

#### P20-A01. NASDAQ index increased by 0.7% to reach 25,654.49

- Atomic ID：`atomic:69d9e9c075f47e1ba25c1b78`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：UNKNOWN

##### M01. NASDAQ index increased by 0.7% to reach 25,654.49

- Mention ID：`mention:1e1909bbbe067acf958a4d2342a20800d2a8ea41dec2929aa55c0f5da2deac10`
- 来源：[Dow Surges 250 Points; Micron Posts Upbeat Q3 Earnings](https://finnhub.io/api/news?id=7316a88ccda79b3354a49965fa82f15ed81fe3722e51491463cf118f097a0143)；`doxatlas:raw_media:fce73732-1c68-4d0f-9d7f-c5800393ffff`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：NASDAQ (SUBJECT)
- 数量：25,654.49 [index_level]；0.7% [percentage_change]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：NASDAQ up 0.7% to 25,654.49

#### P20-A02. Nasdaq index increased by 2.15% in pre-market trade.

- Atomic ID：`atomic:7091ac47609c134081e0bca4`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY

##### M01. Nasdaq index increased by 2.15% in pre-market trade.

- Mention ID：`mention:4816419a2b604c199f577c21d436d5d77dda0e8d550914a55757c7a65efc2e06`
- 来源：[Investors bet on AI again after Micron reports 346% sales jump](https://edition.cnn.com/2026/06/25/business/micron-results-ai-stocks-volatility)；`doxatlas:raw_media:1aaec309-8ccb-42d6-b974-b4aa3bf2d43c`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Nasdaq (SUBJECT)
- 数量：2.15% [index_change_pct]
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：the tech-heavy Nasdaq and the S&P 500 were up 2.15% and 0.75% respectively in pre-market trade

### P21. Han Ji-young of Kiwoom Securities attributed the KOSPI surge to favorable macro conditions, Micron's earnings surprise, and strength in KOSPI200 night futures.

- Package ID：`package:577d2cba791054db57a717b6`
- Family / Kind：`ANALYST_REPORT` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：field:c60fd4bc506db223357ac02c, field:f39062cf6ee4d63215cfe76a
- Anchor artifact / period：— / —
- 摘要：Han Ji-young of Kiwoom Securities attributed the KOSPI surge to favorable macro conditions, Micron's earnings surprise, and strength in KOSPI200 night futures.

#### P21-A01. Han Ji-young of Kiwoom Securities attributed the KOSPI surge to favorable macro conditions, Micron's earnings surprise, and strength in KOSPI200 night futures.

- Atomic ID：`atomic:f7c8eea300655fcafe6b6276`
- Family / Assertion：`ANALYST_ACTION` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：UNKNOWN

##### M01. Han Ji-young of Kiwoom Securities attributed the KOSPI surge to favorable macro conditions, Micron's earnings surprise, and strength in KOSPI200 night futures.

- Mention ID：`mention:20eb1e6ea989cdcca476e5a0752d891f517cb707372e36bad690762d6ff04c20`
- 来源：[KOSPI Spikes 5% on Opening, Riding Micron’s Surprise Earnings](https://beincrypto.com/micron-earnings-kospi-sidecar-chip-rally/)；`doxatlas:raw_media:3f25dec4-7f5f-4203-9e0f-d37c2b06a2c7`
- Family / Assertion：`ANALYST_ACTION` / `ACTUAL`
- 参与者：Han Ji-young (ACTOR)；Kiwoom Securities (OTHER)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：“In a favorable macro environment, including falling oil prices and the U.S. 10-year Government Bonds yield falling below 4.4%, Micron’s earnings surprise and the more than 5% strength in the KOSPI200 night futures combined to send the index surging at the open.” — Han Ji-young, Kiwoom Securities

### P22. Microsoft stock closed at $352.83, representing a 3.46% decline.

- Package ID：`package:5d9658fd35007e739a96164a`
- Family / Kind：`COMPANY_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：COMPANY_MSFT
- Anchor artifact / period：— / —
- 摘要：Microsoft stock closed at $352.83, representing a 3.46% decline.

#### P22-A01. Microsoft stock closed at $352.83, representing a 3.46% decline.

- Atomic ID：`atomic:758bad21d9d774bb8405bc1a`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY

##### M01. Microsoft stock closed at $352.83, representing a 3.46% decline.

- Mention ID：`mention:0937f1532a4ee920d87c4c744565f2df4c04e442ffc5465f5e22a1c3f7e23058`
- 来源：[Stock Market Today, June 25: Apple Drops After Raising Device Prices to Offset Higher Memory Costs](https://www.fool.com/coverage/stock-market-today/2026/06/25/stock-market-today-june-25-apple-drops-after-raising-device-prices-to-offset-higher-memory-costs/)；`doxatlas:raw_media:d9404d58-cd88-4ccc-a9d2-ec4004629dbc`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Microsoft (SUBJECT)
- 数量：$352.83 [closing_price]；down 3.46% [price_change_percent]
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Microsoft (MSFT +1.69%) closed at $352.83, down 3.46%

### P23. Tim Cook described the situation as a "hundred-year flood" in an interview with the Wall Street Journal.

- Package ID：`package:6309780f9bb6409f2bb24ae9`
- Family / Kind：`COMPANY_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：INSTITUTION_THE_WALL_STREET_JOURNAL, PERSON_TIM_COOK_AF589A27
- Anchor artifact / period：— / —
- 摘要：Tim Cook described the situation as a "hundred-year flood" in an interview with the Wall Street Journal.

#### P23-A01. Tim Cook described the situation as a "hundred-year flood" in an interview with the Wall Street Journal.

- Atomic ID：`atomic:423279b6e1570bf932aa06cb`
- Family / Assertion：`OTHER` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：UNKNOWN

##### M01. Tim Cook described the situation as a "hundred-year flood" in an interview with the Wall Street Journal.

- Mention ID：`mention:ca1dbdd915cdb35a8796fa8d34d64f3bfd68f39d2c2dcbe7c6ce6745e142bc71`
- 来源：[Tim Cook Calls the Memory Crisis a "Hundred-Year Flood"](https://finance.yahoo.com/technology/articles/tim-cook-calls-memory-crisis-170140774.html)；`doxatlas:raw_media:d42584c3-2d2d-4559-affc-ccae5b388ce1`
- Family / Assertion：`OTHER` / `ACTUAL`
- 参与者：Tim Cook (ACTOR)；Wall Street Journal (TARGET)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：CEO Tim Cook told the Wall Street Journal it was a "hundred-year flood."

### P24. The Magnificent Seven stocks declined by about 2%, reaching a two-month low.

- Package ID：`package:67f4ce99d5720067e51903a2`
- Family / Kind：`COMPANY_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：field:c7e1ef6747b1aaa676432eb1
- Anchor artifact / period：— / —
- 摘要：The Magnificent Seven stocks declined by about 2%, reaching a two-month low.

#### P24-A01. The Magnificent Seven stocks declined by about 2%, reaching a two-month low.

- Atomic ID：`atomic:2974307169b4fbe0f1f2ba1b`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY

##### M01. The Magnificent Seven stocks declined by about 2%, reaching a two-month low.

- Mention ID：`mention:af6ebd0f3a67109e22b74e2b363c01b4f4ea5df98d3f9e0c9cbeab05cbe5baf7`
- 来源：[Why Micron's blowout earnings are a headache for Apple](https://finance.yahoo.com/markets/article/why-microns-blowout-earnings-are-a-headache-for-apple-161932441.html)；`doxatlas:raw_media:6ccb4979-525c-4467-a381-538f38a4fa9c`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Magnificent Seven (SUBJECT)
- 数量：down about 2% [price_change_percent]
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：The Magnificent Seven are down about 2%, sliding to a two-month low after a month in which megacap AI winners had already lost trillions in market value.

### P25. Demand for Micron's NAND and DRAM memory chips exceeded industry supply.

- Package ID：`package:69ee11909bd9d84cb2a2e740`
- Family / Kind：`EARNINGS_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`FROZEN`；Version：9
- 层级规模：27 Atomic / 51 Mention
- Anchor entities：COMPANY_MU, COMPANY_NVDA, COMPANY_SNDK, field:6553f767b94f1bb2fdd8ba2d, field:9af92771046d9567cef4b2f1, field:d4fa06736fa75b5b2c1ad9be
- Anchor artifact / period：— / —
- 摘要：Demand for Micron's NAND and DRAM memory chips exceeded industry supply. Micron signaled meaningfully higher capital expenditure spending in 2027. Micron's gross margin came in at more than 84%.

#### P25-A01. Demand for Micron's NAND and DRAM memory chips exceeded industry supply.

- Atomic ID：`atomic:07813f562dddebbfe642b752`
- Family / Assertion：`PRODUCTION_SUPPLY` / `ONGOING`
- Mention 数：1；Version：1
- 时间：UNKNOWN

##### M01. Demand for Micron's NAND and DRAM memory chips exceeded industry supply.

- Mention ID：`mention:74cad5b5fa03f97f5b1857e0e563b621d825af79516f557f0d8f13e343aaaf2f`
- 来源：[Wedbush says chip rally has further to run after Micron's blowout quarter](https://www.proactiveinvestors.com/companies/news/1094490/wedbush-says-chip-rally-has-further-to-run-after-micron-s-blowout-quarter-1094490.html)；`doxatlas:raw_media:79fe1f85-a1cc-44e1-928b-d0256c075eb4`
- Family / Assertion：`PRODUCTION_SUPPLY` / `ONGOING`
- 参与者：Micron (SUBJECT)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Wedbush highlighted that demand for Micron's NAND and DRAM memory chips continued to significantly exceed industry supply, pointing to tight conditions and strong pricing power.

#### P25-A02. Micron signaled meaningfully higher capital expenditure spending in 2027.

- Atomic ID：`atomic:0fa87dbe5ca3440b61f8963e`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- Mention 数：1；Version：1
- 时间：2027 / UNKNOWN

##### M01. Micron signaled meaningfully higher capital expenditure spending in 2027.

- Mention ID：`mention:258134e5e6ed7abaf4b89f05d2d0f8e0ffd12fb4a3bd7a4a3e9a709ce5e04bea`
- 来源：[Micron shares surge to all-time high as Wall Street cheers unprecedented contract visibility](https://www.proactiveinvestors.com/companies/news/1094520/micron-shares-surge-to-all-time-high-as-wall-street-cheers-unprecedented-contract-visibility-1094520.html)；`doxatlas:raw_media:6b5d042a-9236-453b-8b60-2ba6c2f16b4f`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- 参与者：Micron (ACTOR)
- 数量：—
- 时间：2027 / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：signalled meaningfully higher spending in 2027

#### P25-A03. Micron expects half of its revenue to eventually come from strategic customer agreements.

- Atomic ID：`atomic:194648161d475620c14c90ad`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- Mention 数：2；Version：2
- 时间：UNKNOWN

##### M01. Micron expects half of its revenue to eventually come from strategic customer agreements.

- Mention ID：`mention:3e69885d609ff8d3635806680253ef59da34f2837f01d736cc35c85292f93317`
- 来源：[Is Micron a Buy After Its Blowout Earnings Report?](https://www.fool.com/investing/2026/06/25/is-micron-a-buy-after-its-blowout-earnings-report/)；`doxatlas:raw_media:a424d484-3e57-47ed-b21d-50aeceafe593`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- 参与者：Micron Technology (ACTOR)
- 数量：half [revenue_share_from_agreements]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：it expects half of its revenue to eventually come from such strategic agreements

##### M02. Micron expects strategic customer agreements to eventually cover at least half of total company revenue.

- Mention ID：`mention:ef6ed3ed02326fa3e977c25674084303e88fcdd76db1c96271a1af946c67f1b7`
- 来源：[Micron shares surge to all-time high as Wall Street cheers unprecedented contract visibility](https://www.proactiveinvestors.com/companies/news/1094520/micron-shares-surge-to-all-time-high-as-wall-street-cheers-unprecedented-contract-visibility-1094520.html)；`doxatlas:raw_media:6b5d042a-9236-453b-8b60-2ba6c2f16b4f`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- 参与者：Micron (ACTOR)
- 数量：at least half [sca_revenue_coverage_target]；$100 billion [remaining_performance_obligations]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron expects SCAs to eventually cover at least half of total company revenue, generating roughly $100 billion in remaining performance obligations.

#### P25-A04. Micron's gross margin came in at more than 84%.

- Atomic ID：`atomic:1aeb13364650a8d9a70ded3b`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：latest quarterly report / UNKNOWN

##### M01. Micron's gross margin came in at more than 84%.

- Mention ID：`mention:0688bd1a87fb9448cd488a2a3dc78a74a1a5f46356fd344d11d34b09288befb7`
- 来源：[Is Micron a Buy After Its Blowout Earnings Report?](https://www.fool.com/investing/2026/06/25/is-micron-a-buy-after-its-blowout-earnings-report/)；`doxatlas:raw_media:a424d484-3e57-47ed-b21d-50aeceafe593`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron Technology (SUBJECT)
- 数量：more than 84% [gross_margin]
- 时间：latest quarterly report / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：gross margin came in at more than 84%.

#### P25-A05. Micron's data centre revenue reached an annualised run rate of about $100 billion.

- Atomic ID：`atomic:1bb6f6df5e55ddc06c05a95d`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- Mention 数：2；Version：2
- 时间：fiscal third-quarter / UNKNOWN

##### M01. Micron's data centre revenue reached an annualised run rate of about $100 billion.

- Mention ID：`mention:1605c0b586283f6fbe78450275dfe6ec53eb65c38743bfd3594f4ab7b25dec14`
- 来源：[Wedbush says chip rally has further to run after Micron's blowout quarter](https://www.proactiveinvestors.com/companies/news/1094490/wedbush-says-chip-rally-has-further-to-run-after-micron-s-blowout-quarter-1094490.html)；`doxatlas:raw_media:79fe1f85-a1cc-44e1-928b-d0256c075eb4`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：about $100 billion [data_centre_revenue_run_rate]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Its data centre revenue has now reached an annualised run rate of about $100 billion, Wedbush noted.

##### M02. Micron's data center revenue hit an annualized run rate of approximately $100 billion.

- Mention ID：`mention:04185e7ce36065fc369c5d429850797dd69560339d8e2276797c4c7337c76ce7`
- 来源：[Micron shares surge to all-time high as Wall Street cheers unprecedented contract visibility](https://www.proactiveinvestors.com/companies/news/1094520/micron-shares-surge-to-all-time-high-as-wall-street-cheers-unprecedented-contract-visibility-1094520.html)；`doxatlas:raw_media:6b5d042a-9236-453b-8b60-2ba6c2f16b4f`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：$100 billion [data_center_revenue_run_rate]
- 时间：fiscal third-quarter / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Data center revenue hit an annualized run rate of approximately $100 billion.

#### P25-A06. Micron's free cash flow climbed to record levels of $18 billion.

- Atomic ID：`atomic:24765229d1a50c728495dbe1`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：latest quarterly report / UNKNOWN

##### M01. Micron's free cash flow climbed to record levels of $18 billion.

- Mention ID：`mention:99f6137bf8f91ca117d26c3ce2beb23cf4ded945315dd168c427a1901390b109`
- 来源：[Is Micron a Buy After Its Blowout Earnings Report?](https://www.fool.com/investing/2026/06/25/is-micron-a-buy-after-its-blowout-earnings-report/)；`doxatlas:raw_media:a424d484-3e57-47ed-b21d-50aeceafe593`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron Technology (SUBJECT)
- 数量：$18 billion [free_cash_flow]
- 时间：latest quarterly report / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Free cash flow climbed to record levels of $18 billion

#### P25-A07. Micron reports earnings results characterized as blockbuster.

- Atomic ID：`atomic:263b6ecc9a6eaac90257f5b9`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：UNKNOWN

##### M01. Micron reports earnings results characterized as blockbuster.

- Mention ID：`mention:05753a507488fe8d5ec0fbb3e9069d724f5b4ce175259b5dcecbbfcfe4b0e902`
- 来源：[Micron surges to new highs on blockbuster earnings: Jim Lebenthal buys the stock](https://finnhub.io/api/news?id=760aa3bb75cb55185d1ad9b8997324a47603aeac7746264f37945ce55241212a)；`doxatlas:raw_media:6e04e5e6-801c-4141-ac0f-81d29029b012`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron (SUBJECT)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `title:0`：Micron surges to new highs on blockbuster earnings

#### P25-A08. Micron management guided for approximately 20% sequential revenue growth in the current quarter.

- Atomic ID：`atomic:37c796912b1bd5e68c78df07`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- Mention 数：1；Version：1
- 时间：current quarter / UNKNOWN

##### M01. Micron management guided for approximately 20% sequential revenue growth in the current quarter.

- Mention ID：`mention:f4783558255b48585e4638ddecee478ed86655dafb3a0cc25092e30d4cf01064`
- 来源：[MU Stock Soars as Micron’s Revenue More Than Quadrupled in Q3](https://www.barchart.com/story/news/3092/mu-stock-soars-as-microns-revenue-more-than-quadrupled-in-q3)；`doxatlas:raw_media:181db9d6-5959-4fb0-947e-3c6680861566`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- 参与者：Micron management (ACTOR)
- 数量：about a 20% [sequential_growth]
- 时间：current quarter / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：As investors reacted to management’s impressive guidance for about a 20% sequential growth in the current quarter,

#### P25-A09. Micron's quarterly revenue reached record levels for the fifth consecutive time.

- Atomic ID：`atomic:466466fac70b1e303e5d5841`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：latest quarterly report / UNKNOWN

##### M01. Micron's quarterly revenue reached record levels for the fifth consecutive time.

- Mention ID：`mention:0e56a119858e923d6b8afc72d663b8b40d25aa72bff8680c05544cd7a6b373b1`
- 来源：[Is Micron a Buy After Its Blowout Earnings Report?](https://www.fool.com/investing/2026/06/25/is-micron-a-buy-after-its-blowout-earnings-report/)；`doxatlas:raw_media:a424d484-3e57-47ed-b21d-50aeceafe593`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron Technology (SUBJECT)
- 数量：more than $41 billion [quarterly_revenue]
- 时间：latest quarterly report / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Quarterly revenue reached record levels for the fifth consecutive time.

#### P25-A10. Micron secured 16 long-term Strategic Customer Agreements (SCAs), selling out its 2026 manufacturing capacity.

- Atomic ID：`atomic:542c5f526e7959e37f161ac3`
- Family / Assertion：`COMMERCIAL_OPERATION` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：UNKNOWN

##### M01. Micron secured 16 long-term Strategic Customer Agreements (SCAs), selling out its 2026 manufacturing capacity.

- Mention ID：`mention:315606b65a8594c1514329f506f184588959ba55323821eaa2f8c817ead7bf04`
- 来源：[MU Stock Soars as Micron’s Revenue More Than Quadrupled in Q3](https://www.barchart.com/story/news/3092/mu-stock-soars-as-microns-revenue-more-than-quadrupled-in-q3)；`doxatlas:raw_media:181db9d6-5959-4fb0-947e-3c6680861566`
- Family / Assertion：`COMMERCIAL_OPERATION` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：16 [agreement_count]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：More importantly, the company has secured 16 long-term Strategic Customer Agreements (SCAs), which have effectively “sold out” its 2026 manufacturing capacity.

#### P25-A11. Micron reported fiscal third-quarter revenue of $41.5 billion.

- Atomic ID：`atomic:70ff18cc099af1c26f4ac5d3`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：fiscal third-quarter / UNKNOWN

##### M01. Micron reported fiscal third-quarter revenue of $41.5 billion.

- Mention ID：`mention:dff44d2045c5c2f2dabfdf5ab588ba634b2b6af311f0fcf6ea1210ca21c4b82c`
- 来源：[Micron shares surge to all-time high as Wall Street cheers unprecedented contract visibility](https://www.proactiveinvestors.com/companies/news/1094520/micron-shares-surge-to-all-time-high-as-wall-street-cheers-unprecedented-contract-visibility-1094520.html)；`doxatlas:raw_media:6b5d042a-9236-453b-8b60-2ba6c2f16b4f`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：$41.5 billion [revenue]；74% year-over-year [revenue_growth_yoy]；$35.9 billion [consensus_revenue_estimate]
- 时间：fiscal third-quarter / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron reported fiscal third-quarter revenue of $41.5 billion, up 74% year-over-year and well above the Street's $35.9 billion estimate.

#### P25-A12. Micron reported Q3 revenue of $41.46 billion, representing a 346% year-over-year increase.

- Atomic ID：`atomic:73f7a87858d56eb0d050ce16`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：Q3 / UNKNOWN

##### M01. Micron reported Q3 revenue of $41.46 billion, representing a 346% year-over-year increase.

- Mention ID：`mention:a470302e83cc147fbbf8cc75eb40f4bcfd8114d37aa3197bf4a6df9c4800d85b`
- 来源：[MU Stock Soars as Micron’s Revenue More Than Quadrupled in Q3](https://www.barchart.com/story/news/3092/mu-stock-soars-as-microns-revenue-more-than-quadrupled-in-q3)；`doxatlas:raw_media:181db9d6-5959-4fb0-947e-3c6680861566`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：$41.46 billion [revenue]；346% [revenue_yoy_growth]
- 时间：Q3 / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron (MU) stock is ripping higher on June 25 after the memory chip giant posted a blockbuster Q3, featuring a 346% year-over-year increase in revenue to $41.46 billion.

#### P25-A13. Micron reported fiscal third-quarter gross margin of 84.9%.

- Atomic ID：`atomic:784be7cdafb34187d26767fe`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- Mention 数：3；Version：3
- 时间：fiscal third-quarter / UNKNOWN

##### M01. Micron reported fiscal third-quarter gross margin of 84.9%.

- Mention ID：`mention:ffa0337e9e387643efc0e8140c5e7f395031176a1aca5029b32825cca6f1c0cf`
- 来源：[Micron shares surge to all-time high as Wall Street cheers unprecedented contract visibility](https://www.proactiveinvestors.com/companies/news/1094520/micron-shares-surge-to-all-time-high-as-wall-street-cheers-unprecedented-contract-visibility-1094520.html)；`doxatlas:raw_media:6b5d042a-9236-453b-8b60-2ba6c2f16b4f`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：84.9% [gross_margin]；81.7% [consensus_gross_margin]
- 时间：fiscal third-quarter / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Gross margin came in at 84.9%, topping consensus of 81.7%

##### M02. Micron reported gross margins of 84.9% in its most recent quarter.

- Mention ID：`mention:4c9279fcb4b885a70ccba352c3377ed7d1a13d5722904f01a6522b54c28a669c`
- 来源：[Tim Cook Calls the Memory Crisis a "Hundred-Year Flood"](https://finance.yahoo.com/technology/articles/tim-cook-calls-memory-crisis-170140774.html)；`doxatlas:raw_media:d42584c3-2d2d-4559-affc-ccae5b388ce1`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：84.9% [gross_margin]；39% [gross_margin]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron (NASDAQ:MU) reported gross margins of 84.9% in its most recent quarter, up from 39% a year ago, surpassing Nvidia (NASDAQ:NVDA) and Meta (NASDAQ:META).

##### M03. Micron's adjusted gross margins reached 84.9% in fiscal Q3.

- Mention ID：`mention:5706ab8d9630110c84323f920f44b3436ffdf1b3fbaacd1dc909ed2fda78ed85`
- 来源：[MU Stock Soars as Micron’s Revenue More Than Quadrupled in Q3](https://www.barchart.com/story/news/3092/mu-stock-soars-as-microns-revenue-more-than-quadrupled-in-q3)；`doxatlas:raw_media:181db9d6-5959-4fb0-947e-3c6680861566`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：84.9% [adjusted_gross_margin]
- 时间：fiscal Q3 / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：An insatiable demand for Micron’s high-bandwidth memory (HBM) chips that power artificial intelligence (AI) data centers drove its adjusted gross margins to a whopping 84.9% in fiscal Q3.

#### P25-A14. Micron does not expect supply to meet demand in the near term.

- Atomic ID：`atomic:7d534accb85829ba6725494f`
- Family / Assertion：`PRODUCTION_SUPPLY` / `EXPECTED`
- Mention 数：1；Version：1
- 时间：UNKNOWN

##### M01. Micron does not expect supply to meet demand in the near term.

- Mention ID：`mention:798dd69d62259e417398eba6854763b934815867a731d171b892c183af2a1eeb`
- 来源：[Why Sandisk Stock Soared Today](https://www.fool.com/investing/2026/06/25/why-sandisk-stock-soared-today/)；`doxatlas:raw_media:35a4333e-6608-482b-b963-ba2f64392063`
- Family / Assertion：`PRODUCTION_SUPPLY` / `EXPECTED`
- 参与者：Micron (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Mehrotra still doesn't see supply catching up with demand anytime soon.

#### P25-A15. Micron stated that demand from AI customers is soaring and that the AI revolution is in its early stages.

- Atomic ID：`atomic:7f612632308fce2e080baf5f`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：UNKNOWN

##### M01. Micron stated that demand from AI customers is soaring and that the AI revolution is in its early stages.

- Mention ID：`mention:3d1f96e40b4dc4ff9efbb1df17abbca84da062ed4041907a63d0e0da76082352`
- 来源：[Is Micron a Buy After Its Blowout Earnings Report?](https://www.fool.com/investing/2026/06/25/is-micron-a-buy-after-its-blowout-earnings-report/)；`doxatlas:raw_media:a424d484-3e57-47ed-b21d-50aeceafe593`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `ACTUAL`
- 参与者：Micron Technology (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：The company's message was positive too: Demand from AI customers is soaring, and we may be in the early stages of this growth opportunity.
  - E02 `VERIFIED` / `text:0`：the company offered an extremely positive message, saying we're in "the early innings" of the AI revolution and that the expansion of AI into various industries represents key memory opportunities.

#### P25-A16. Micron signaled capital expenditure of over $40 billion for the next year, with approximately $20 billion allocated to construction and clean rooms.

- Atomic ID：`atomic:80ce7bb58deeb2c61261bf40`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- Mention 数：1；Version：1
- 时间：next year / UNKNOWN

##### M01. Micron signaled capital expenditure of over $40 billion for the next year, with approximately $20 billion allocated to construction and clean rooms.

- Mention ID：`mention:fa5d9320434a19e4680ba0bbc1978aeab8499ee28b0ad66928fe9d2ca7055a2a`
- 来源：[Micron Just Locked In $100 Billion in Sales, and Wall Street Thinks the Boom-Bust Chip Cycle Is Dead](https://247wallst.com/investing/2026/06/25/micron-just-locked-in-100-billion-in-sales-and-wall-street-thinks-the-boom-bust-chip-cycle-is-dead/)；`doxatlas:raw_media:1e515897-64d4-4896-b936-10a04501eff4`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- 参与者：Micron (ACTOR)
- 数量：$40 billion [capex_guidance]；$20 billion [construction_capex_component]
- 时间：next year / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：the company has signaled capex over $40 billion next year, with roughly $20 billion going to construction and clean rooms.

#### P25-A17. Micron reported earnings per share of $25.11.

- Atomic ID：`atomic:8fa3b639f7d5dd877531aa25`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：third-quarter / UNKNOWN

##### M01. Micron reported earnings per share of $25.11.

- Mention ID：`mention:cdf3910a2ed5d26cdf5df33f1241c806c181974abaa6a20d4c35d5e6661e2e83`
- 来源：[Wedbush says chip rally has further to run after Micron's blowout quarter](https://www.proactiveinvestors.com/companies/news/1094490/wedbush-says-chip-rally-has-further-to-run-after-micron-s-blowout-quarter-1094490.html)；`doxatlas:raw_media:79fe1f85-a1cc-44e1-928b-d0256c075eb4`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：$25.11 [earnings_per_share]；$20.86 [earnings_per_share]
- 时间：third-quarter / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Earnings per share came in at $25.11, more than double the previous quarter and ahead of the $20.86 consensus, while gross margin of 84.9% beat expectations.

#### P25-A18. Micron reported non-GAAP EPS of $25.11, beating the consensus estimate of $20.2843.

- Atomic ID：`atomic:a769833785dbb1102a551dbc`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：fiscal Q3 / UNKNOWN

##### M01. Micron reported non-GAAP EPS of $25.11, beating the consensus estimate of $20.2843.

- Mention ID：`mention:42b22429674cc6cf3736187dc820af4d5c2d0ea0fa89a7464811cb77d0416c96`
- 来源：[Micron Just Locked In $100 Billion in Sales, and Wall Street Thinks the Boom-Bust Chip Cycle Is Dead](https://247wallst.com/investing/2026/06/25/micron-just-locked-in-100-billion-in-sales-and-wall-street-thinks-the-boom-bust-chip-cycle-is-dead/)；`doxatlas:raw_media:1e515897-64d4-4896-b936-10a04501eff4`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：$25.11 [non_gaap_eps]；$20.2843 [eps_consensus]
- 时间：fiscal Q3 / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Non-GAAP EPS landed at $25.11 against a $20.2843 consensus, the seventh consecutive quarter of beating Wall Street.

#### P25-A19. Micron announced 16 signed Strategic Customer Agreements representing approximately 20% of its DRAM volume and one-third of its NAND volume over the agreement period.

- Atomic ID：`atomic:abce7a2240c62e734c8316a2`
- Family / Assertion：`COMMERCIAL_OPERATION` / `ACTUAL`
- Mention 数：12；Version：12
- 时间：2026-06-24T00:00:00 / DAY

##### M01. Micron announced 16 signed Strategic Customer Agreements representing approximately 20% of its DRAM volume and one-third of its NAND volume over the agreement period.

- Mention ID：`mention:47e6c231e80280cb89f818dc8068b0b9c30802ca737b15cf3fe8b817e16ec3b4`
- 来源：[Micron Just Guided for a Staggering $50 Billion in Fiscal Q4 Revenue. Here's What It Means for the AI Trade.](https://www.fool.com/investing/2026/06/24/micron-just-guided-for-a-staggering-50-billion-in/)；`doxatlas:raw_media:c762d097-1190-4ea7-9802-5d44fadefd9f`
- Family / Assertion：`COMMERCIAL_OPERATION` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：16 signed agreements [agreement_count]；about 20% [dram_volume_share]；a third [nand_volume_share]
- 时间：2026-06-24T00:00:00 / DAY
- Evidence 状态：`TEXT_NOT_FOUND`
  - E01 `TEXT_NOT_FOUND` / `text:0`：Micron announced what it called "transformational Strategic Customer Agreements" -- multi-year deals that lock in volume and provide pricing visibility for memory supply. ... The 16 signed agreements represent about 20% of Micron's DRAM volume and a third of its NAND volume over the agreement period.

##### M02. Micron completed 16 Strategic Customer Agreements spanning data center, consumer, and automotive markets.

- Mention ID：`mention:bd4dc94ff74171d193280660b4b5c53a8bd6b998f565f2c0192642b14468e06d`
- 来源：[Micron CEO Expects Memory Supply To Improve Gradually In 2028, But There's No 'Line Of Sight' When It Will Catch Up To Rising AI Demand](https://www.benzinga.com/markets/tech/26/06/60089761/micron-ceo-sees-memory-supply-improving-by-2028-but-no-clear-end-in-sight-to-ai-demand-shortfallmicron-ceo-sees-memory-supply-improving-by-2028-but-no-clear-end-in-sight-to-ai-demand-shortfall)；`doxatlas:raw_media:25387f96-b423-437c-b2cd-8652aa176bd8`
- Family / Assertion：`COMMERCIAL_OPERATION` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：16 [agreement_count]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron announced it has completed 16 Strategic Customer Agreements, or SCAs, spanning data center, consumer and automotive markets.

##### M03. Micron introduced strategic customer agreements (SCA).

- Mention ID：`mention:2bfbd0d95489d7efbb6866410413fc4c3aad3376bd0599c37ef407565ac209b7`
- 来源：[4 Blowout Numbers From Micron's Earnings Investors Need To See](https://www.fool.com/investing/2026/06/25/x-blowout-numbers-from-microns-earnings-investors/)；`doxatlas:raw_media:34fc2a61-fa6e-4db0-8e39-f862da0af41c`
- Family / Assertion：`COMMERCIAL_OPERATION` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron just introduced strategic customer agreements (SCA), longer-term contracts that typically last five years, to alleviate some of the cyclical risk facing the company.

##### M04. Micron stated that its entire 2026 output of high-bandwidth memory chips is sold out under fixed-price contracts.

- Mention ID：`mention:b2533293d4861377a68897afedc03a437d86474a3c2a0cf07820551301845b39`
- 来源：[Micron posts record results as AI boom drives 15-fold jump in net profit](https://www.euronews.com/business/2026/06/25/micron-posts-record-results-as-ai-boom-drives-15-fold-jump-in-net-profit)；`doxatlas:raw_media:2b5bba2a-e472-4a1f-a341-5be6e4286dd1`
- Family / Assertion：`COMMERCIAL_OPERATION` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron has said its entire 2026 output of these chips is already sold out under fixed-price contracts

##### M05. Micron's multi-year Strategic Customer Agreements will significantly enhance the durability and predictability of its financial performance.

- Mention ID：`mention:ea2471c896440af2f992d95a876a68f176d3647bf942f4043e028558c4553cc9`
- 来源：[These Analysts Boost Their Forecasts On Micron Technology After Better-Than-Expected Q3 Results](https://www.benzinga.com/analyst-stock-ratings/price-target/26/06/60100007/these-analysts-boost-their-forecasts-on-micron-technology-after-better-than-expected-q3-results)；`doxatlas:raw_media:5d890b9d-c789-4a99-87d6-8a34a3ea4cc2`
- Family / Assertion：`COMMERCIAL_OPERATION` / `EXPECTED`
- 参与者：Micron (SUBJECT)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：We believe our multi-year Strategic Customer Agreements will significantly enhance the durability and predictability of Micron’s strong financial performance," said Sanjay Mehrotra, chairman, president and CEO of Micron.

##### M06. Micron signed 16 strategic customer agreements.

- Mention ID：`mention:36b22caa2f4ce40163a6bf59fc7df4e68c594f5661ff48179c3f0b7ce9dfe9ee`
- 来源：[Wedbush says chip rally has further to run after Micron's blowout quarter](https://www.proactiveinvestors.com/companies/news/1094490/wedbush-says-chip-rally-has-further-to-run-after-micron-s-blowout-quarter-1094490.html)；`doxatlas:raw_media:79fe1f85-a1cc-44e1-928b-d0256c075eb4`
- Family / Assertion：`COMMERCIAL_OPERATION` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：16 [agreement_count]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：The company also signed 16 strategic customer agreements, 14 of which carry cumulative revenue of at least $100 billion over the term of the deals.

##### M07. Sandisk is securing long-term pricing at high margins via Strategic Customer Agreements.

- Mention ID：`mention:85adafb1ed8f74a6815e9e8afea4f6156ae17c8690bca5f24f91af60af4b2ea0`
- 来源：[Why Sandisk Stock Soared Today](https://www.fool.com/investing/2026/06/25/why-sandisk-stock-soared-today/)；`doxatlas:raw_media:35a4333e-6608-482b-b963-ba2f64392063`
- Family / Assertion：`COMMERCIAL_OPERATION` / `ONGOING`
- 参与者：Sandisk (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：and so is Sandisk.

##### M08. Micron is securing long-term pricing at high margins via Strategic Customer Agreements.

- Mention ID：`mention:ca8818ec4e2a722e10185aea1ac1ab606891d2d94b64646d1af9c544b23f63e2`
- 来源：[Why Sandisk Stock Soared Today](https://www.fool.com/investing/2026/06/25/why-sandisk-stock-soared-today/)；`doxatlas:raw_media:35a4333e-6608-482b-b963-ba2f64392063`
- Family / Assertion：`COMMERCIAL_OPERATION` / `ONGOING`
- 参与者：Micron (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron is locking in long-term prices at these high margins through Strategic Customer Agreements -- and so is Sandisk.

##### M09. Micron signed 16 customer agreements running through 2030 involving commitments to purchase memory products.

- Mention ID：`mention:5a4bbf187e3bca6376faae51acc45902b0d14da1cac217e002b1b3f500b0b93b`
- 来源：[Is Micron a Buy After Its Blowout Earnings Report?](https://www.fool.com/investing/2026/06/25/is-micron-a-buy-after-its-blowout-earnings-report/)；`doxatlas:raw_media:a424d484-3e57-47ed-b21d-50aeceafe593`
- Family / Assertion：`COMMERCIAL_OPERATION` / `ACTUAL`
- 参与者：Micron Technology (ACTOR)；16 customer agreements (TARGET)
- 数量：16 [agreement_count]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron also signed 16 customer agreements that offer the company and investors visibility on revenue ahead, reinforcing the idea that the demand we've seen so far is set to continue.
  - E02 `VERIFIED` / `text:0`：The deals, with data center, consumer, and automotive customers, run through 2030 and involve commitments to purchase a certain volume of memory products.

##### M10. Micron secured 16 contracts with customers including data centers and automakers, with a potential value of $22 billion.

- Mention ID：`mention:3a1d69be1f28133181cdc1436c2e65f0b89b019bf879b5ff1d9986a1fcd1a914`
- 来源：[Why Everyone Is Talking About Micron](https://www.fool.com/investing/2026/06/25/why-everyone-is-talking-about-micron/)；`doxatlas:raw_media:49fb27c5-84b4-4e56-8791-f7ac23de38b7`
- Family / Assertion：`COMMERCIAL_OPERATION` / `ACTUAL`
- 参与者：Micron (ACTOR)；customers (COUNTERPARTY)
- 数量：16 contracts [contract_count]；$22 billion [contract_value]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron also said it has secured 16 contracts with customers, including data centers and automakers, in the three- to five-year range that could bring in $22 billion.

##### M11. Micron has 16 strategic customer agreements in place, with 14 carrying cumulative minimum revenue commitments of approximately $100 billion.

- Mention ID：`mention:0417d925064e633766335799a87319d5bf57194c0be3d95c132f7b40c6bca05d`
- 来源：[Micron shares surge to all-time high as Wall Street cheers unprecedented contract visibility](https://www.proactiveinvestors.com/companies/news/1094520/micron-shares-surge-to-all-time-high-as-wall-street-cheers-unprecedented-contract-visibility-1094520.html)；`doxatlas:raw_media:6b5d042a-9236-453b-8b60-2ba6c2f16b4f`
- Family / Assertion：`COMMERCIAL_OPERATION` / `ONGOING`
- 参与者：Micron (ACTOR)
- 数量：16 [sca_count]；14 [sca_with_commitments_count]；$100 billion [cumulative_minimum_revenue_commitment]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron now has 16 SCAs in place, with 14 of those carrying cumulative minimum revenue commitments of approximately $100 billion over the remaining agreement terms.

##### M12. Micron Technology signed 16 long-term customer agreements, with 14 agreements securing approximately $100 billion in minimum guaranteed revenue through 2030.

- Mention ID：`mention:9b6218e311d0d70d3acda2c76634c41e1ea8c1b99e04de84bef2cf88e3e262dc`
- 来源：[Micron Just Locked In $100 Billion in Sales, and Wall Street Thinks the Boom-Bust Chip Cycle Is Dead](https://247wallst.com/investing/2026/06/25/micron-just-locked-in-100-billion-in-sales-and-wall-street-thinks-the-boom-bust-chip-cycle-is-dead/)；`doxatlas:raw_media:1e515897-64d4-4896-b936-10a04501eff4`
- Family / Assertion：`COMMERCIAL_OPERATION` / `ACTUAL`
- 参与者：Micron Technology (ACTOR)
- 数量：16 [agreement_count]；$100 billion [guaranteed_revenue]；14 [binding_agreement_count]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron Technology (NASDAQ:MU | MU Price Prediction) had signed 16 long-term customer agreements, 14 of them locking in roughly $100 billion in minimum guaranteed revenue through 2030

#### P25-A20. Micron reported third-quarter revenue of $41.5 billion.

- Atomic ID：`atomic:b90ec777216299b29d2c7152`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：third-quarter / UNKNOWN

##### M01. Micron reported third-quarter revenue of $41.5 billion.

- Mention ID：`mention:5d6f3c9e0bdd3dafdac3586344c4ce21c986d5a6fa0449b66889eec2de57257b`
- 来源：[Wedbush says chip rally has further to run after Micron's blowout quarter](https://www.proactiveinvestors.com/companies/news/1094490/wedbush-says-chip-rally-has-further-to-run-after-micron-s-blowout-quarter-1094490.html)；`doxatlas:raw_media:79fe1f85-a1cc-44e1-928b-d0256c075eb4`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：$41.5 billion [revenue]；$35.9 billion [revenue]
- 时间：third-quarter / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron reported third-quarter revenue of $41.5 billion, up 74% on the prior quarter and well ahead of the $35.9 billion expected by analysts.

#### P25-A21. Micron reported free cash flow of $18.304 billion for the quarter.

- Atomic ID：`atomic:bd9bed5f81b9c99c2f0d1b53`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：Q3 / UNKNOWN

##### M01. Micron reported free cash flow of $18.304 billion for the quarter.

- Mention ID：`mention:1dc96ae52cf209f48558195307fc48aa9a3a090fd39d8ec4dffd485651a325d2`
- 来源：[Micron Just Locked In $100 Billion in Sales, and Wall Street Thinks the Boom-Bust Chip Cycle Is Dead](https://247wallst.com/investing/2026/06/25/micron-just-locked-in-100-billion-in-sales-and-wall-street-thinks-the-boom-bust-chip-cycle-is-dead/)；`doxatlas:raw_media:1e515897-64d4-4896-b936-10a04501eff4`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：$18.304 billion [free_cash_flow]
- 时间：Q3 / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Free cash flow was a robust $18.304 billion in the quarter

#### P25-A22. Micron guided fiscal fourth-quarter revenue to $50 billion, plus or minus $1 billion, exceeding Wall Street consensus estimates of $43 billion.

- Atomic ID：`atomic:c80f86691be58b1fa2cb59d5`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- Mention 数：7；Version：7
- 时间：fiscal Q4 / 2026-06-24T00:00:00 / DAY

##### M01. Micron guided fiscal fourth-quarter revenue to $50 billion, plus or minus $1 billion, exceeding Wall Street consensus estimates of $43 billion.

- Mention ID：`mention:a989099436b5ce681c33be757d0c0d92634f8d12ced17f8089d41c5d43b85594`
- 来源：[Micron Just Guided for a Staggering $50 Billion in Fiscal Q4 Revenue. Here's What It Means for the AI Trade.](https://www.fool.com/investing/2026/06/24/micron-just-guided-for-a-staggering-50-billion-in/)；`doxatlas:raw_media:c762d097-1190-4ea7-9802-5d44fadefd9f`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- 参与者：Micron (ACTOR)
- 数量：$50 billion [revenue_guidance]；$1 billion [revenue_guidance_tolerance]；$43 billion [revenue_consensus]
- 时间：fiscal Q4 / 2026-06-24T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron guided fiscal Q4 revenue to $50 billion, plus or minus $1 billion -- well above the $43 billion Wall Street had been modeling.

##### M02. Micron guided fourth-quarter revenue to reach $50 billion.

- Mention ID：`mention:28fcfba3024fb0ae4d11a15fece7244f97b7e67fa44491554f098ce472b369ed`
- 来源：[4 Blowout Numbers From Micron's Earnings Investors Need To See](https://www.fool.com/investing/2026/06/25/x-blowout-numbers-from-microns-earnings-investors/)；`doxatlas:raw_media:34fc2a61-fa6e-4db0-8e39-f862da0af41c`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- 参与者：Micron (ACTOR)
- 数量：$50 billion [revenue]
- 时间：fourth quarter / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron's guidance called for similar growth in the fourth quarter, with revenue expected to reach $50 billion

##### M03. Micron expects fourth-quarter revenue of $50 billion, plus or minus $1 billion.

- Mention ID：`mention:043070cffff634ee9ed96f2f6bd4048623dfaf1411ce88963d1d0d0517c95800`
- 来源：[These Analysts Boost Their Forecasts On Micron Technology After Better-Than-Expected Q3 Results](https://www.benzinga.com/analyst-stock-ratings/price-target/26/06/60100007/these-analysts-boost-their-forecasts-on-micron-technology-after-better-than-expected-q3-results)；`doxatlas:raw_media:5d890b9d-c789-4a99-87d6-8a34a3ea4cc2`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- 参与者：Micron (ACTOR)
- 数量：$50 billion [revenue_guidance]；$1 billion [revenue_guidance_tolerance]；$42.95 billion [revenue_estimate]
- 时间：fourth-quarter / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron expects fourth-quarter revenue of $50 billion, plus or minus $1 billion, versus estimates of $42.95 billion.

##### M04. Micron anticipates fourth-quarter adjusted earnings of $31 per share, plus or minus $1.

- Mention ID：`mention:ebacd35e6269f053ac9336aa0de8d92b8c5e11d458abd84511048da3bc827191`
- 来源：[These Analysts Boost Their Forecasts On Micron Technology After Better-Than-Expected Q3 Results](https://www.benzinga.com/analyst-stock-ratings/price-target/26/06/60100007/these-analysts-boost-their-forecasts-on-micron-technology-after-better-than-expected-q3-results)；`doxatlas:raw_media:5d890b9d-c789-4a99-87d6-8a34a3ea4cc2`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- 参与者：Micron (ACTOR)
- 数量：$31 per share [adjusted_earnings_per_share_guidance]；$1 [adjusted_earnings_per_share_guidance_tolerance]；$25.50 per share [adjusted_earnings_per_share_estimate]
- 时间：fourth-quarter / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：The company anticipates fourth-quarter adjusted earnings of $31 per share, plus or minus $1, versus estimates of $25.50 per share.

##### M05. Micron issued forward guidance indicating robust demand for AI memory products.

- Mention ID：`mention:f241f8b0a1882d822b1597d9b73adef1769d496e327b344b49b86a8adfb04371`
- 来源：[The AI Memory Trade Is Heating Up: Defiance Launches 2X DRAM ETF After Micron's Blowout Quarter](https://www.benzinga.com/etfs/new-etfs/26/06/60103609/the-ai-memory-trade-is-heating-up-defiance-launches-2x-dram-etf-after-microns-blowout-quarter)；`doxatlas:raw_media:238a879b-e686-457d-b83d-5b0405bd28c6`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：The company also issued strong forward guidance, signaling that demand for AI memory products remains robust.

##### M06. Micron forecasted fourth-quarter revenue of $50 billion.

- Mention ID：`mention:1eb7bc42e96b048f83ed7e7877bd744828b69030b05ac24ecc5880bc6b7f337d`
- 来源：[Wedbush says chip rally has further to run after Micron's blowout quarter](https://www.proactiveinvestors.com/companies/news/1094490/wedbush-says-chip-rally-has-further-to-run-after-micron-s-blowout-quarter-1094490.html)；`doxatlas:raw_media:79fe1f85-a1cc-44e1-928b-d0256c075eb4`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- 参与者：Micron (ACTOR)
- 数量：$50 billion [revenue]；$43.6 billion [revenue]
- 时间：fourth-quarter / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Guidance impressed the broker further, with Micron forecasting fourth-quarter revenue of $50 billion, against the $43.6 billion the market had expected.

##### M07. Micron guided to $50 billion in revenue for its current quarter.

- Mention ID：`mention:f561308cd83b6eed98132200f4d26a536cb9d1d628c2861f6e06331923b266f0`
- 来源：[Why Everyone Is Talking About Micron](https://www.fool.com/investing/2026/06/25/why-everyone-is-talking-about-micron/)；`doxatlas:raw_media:49fb27c5-84b4-4e56-8791-f7ac23de38b7`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- 参与者：Micron (ACTOR)
- 数量：$50 billion [revenue_guidance]
- 时间：current quarter / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Furthermore, Micron is now guiding to $50 billion in revenue for its current quarter.

#### P25-A23. Micron Technology's customers committed $22 billion to secure supplies of its chips.

- Atomic ID：`atomic:d67d8ebd7a12d60ab9a107aa`
- Family / Assertion：`COMMERCIAL_OPERATION` / `ACTUAL`
- Mention 数：4；Version：4
- 时间：UNKNOWN

##### M01. Micron Technology's customers committed $22 billion to secure supplies of its chips.

- Mention ID：`mention:2e7dc645fcf3d5a5b59593cab622eb719026d0cbe598a6b1c1925587a75bacbc`
- 来源：[Investors bet on AI again after Micron reports 346% sales jump](https://edition.cnn.com/2026/06/25/business/micron-results-ai-stocks-volatility)；`doxatlas:raw_media:1aaec309-8ccb-42d6-b974-b4aa3bf2d43c`
- Family / Assertion：`COMMERCIAL_OPERATION` / `ACTUAL`
- 参与者：Micron Technology (TARGET)；customers (ACTOR)
- 数量：$22 billion [committed_capital]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron said in its results that its customers had committed $22 billion to secure supplies of its chips

##### M02. Micron disclosed that customers have committed approximately $22 billion through strategic agreements for high-bandwidth memory.

- Mention ID：`mention:263d21e6496a3ddeaaa73d1284a7ad1d411420c394dfcf7b57867c3c631d8026`
- 来源：[Apple Just Confirmed Micron's Biggest AI Prediction](https://www.benzinga.com/trading-ideas/long-ideas/26/06/60108831/apple-just-confirmed-microns-biggest-ai-prediction)；`doxatlas:raw_media:35c3d868-abe6-4617-8c1f-c1460a8e8179`
- Family / Assertion：`COMMERCIAL_OPERATION` / `ACTUAL`
- 参与者：Micron (ACTOR)；customers (COUNTERPARTY)
- 数量：$22 billion [committed_revenue]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron recently disclosed that customers have committed approximately $22 billion through strategic agreements, providing additional visibility into future demand as the company ramps production of high-bandwidth memory used in AI accelerators.

##### M03. Micron expects $22 billion in commitments from signed customer agreements.

- Mention ID：`mention:6372f0cac33b2533e20721463c8dee620affb91380248e13fc2bba338a85a23e`
- 来源：[Is Micron a Buy After Its Blowout Earnings Report?](https://www.fool.com/investing/2026/06/25/is-micron-a-buy-after-its-blowout-earnings-report/)；`doxatlas:raw_media:a424d484-3e57-47ed-b21d-50aeceafe593`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- 参与者：Micron Technology (ACTOR)
- 数量：$22 billion [commitment_value]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：The company expects $22 billion in commitments from the deals signed so far

##### M04. Customers including Nvidia committed $22 billion to Micron under five-year take-or-pay supply agreements.

- Mention ID：`mention:3659599cb40396ebd82186d50896c932b9ca92ec190900cf01f14d1524898099`
- 来源：[AI boom keeps memory chip makers in sweet spot, says expert](https://finnhub.io/api/news?id=83f579c98258ef8f6999941b3227364c4d014b9cc9cfd5300374568a2fb3e0c7)；`doxatlas:raw_media:f70bfd87-b3f2-4a7f-9a1c-6a94cd5279b2`
- Family / Assertion：`COMMERCIAL_OPERATION` / `ACTUAL`
- 参与者：Micron (ACTOR)；Nvidia (COUNTERPARTY)；customers (COUNTERPARTY)
- 数量：$22 billion [contract_value]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron said on Wednesday customers such as Nvidia had committed $22 billion to lock in supplies of memory chips, playing up huge growth in five-year "take-or-pay" deals that require clients to either buy its chips or hand over cash.

#### P25-A24. Micron expects tight supply conditions to persist beyond its 2027 financial year.

- Atomic ID：`atomic:e79184de78f971bc2f96d80c`
- Family / Assertion：`PRODUCTION_SUPPLY` / `EXPECTED`
- Mention 数：1；Version：1
- 时间：beyond its 2027 financial year / UNKNOWN

##### M01. Micron expects tight supply conditions to persist beyond its 2027 financial year.

- Mention ID：`mention:2ab187e1dada59dc6555d584e9fa3aa383850db263942fb5b68aa3ecb928529f`
- 来源：[Wedbush says chip rally has further to run after Micron's blowout quarter](https://www.proactiveinvestors.com/companies/news/1094490/wedbush-says-chip-rally-has-further-to-run-after-micron-s-blowout-quarter-1094490.html)；`doxatlas:raw_media:79fe1f85-a1cc-44e1-928b-d0256c075eb4`
- Family / Assertion：`PRODUCTION_SUPPLY` / `EXPECTED`
- 参与者：Micron (ACTOR)
- 数量：—
- 时间：beyond its 2027 financial year / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Wedbush said the company expects tight supply conditions to persist beyond its 2027 financial year, with gross margin guided to about 86%.

#### P25-A25. Micron reported Q3 capital expenditure of $7.826 billion, a 166.37% year-over-year increase.

- Atomic ID：`atomic:f50466e71954861a16c7b6da`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：Q3 / UNKNOWN

##### M01. Micron reported Q3 capital expenditure of $7.826 billion, a 166.37% year-over-year increase.

- Mention ID：`mention:0f9da8d38f37a7dcee2c4c63a93e58fa079e7a2500ae2eb7a02aa3af6e104cf9`
- 来源：[Micron Just Locked In $100 Billion in Sales, and Wall Street Thinks the Boom-Bust Chip Cycle Is Dead](https://247wallst.com/investing/2026/06/25/micron-just-locked-in-100-billion-in-sales-and-wall-street-thinks-the-boom-bust-chip-cycle-is-dead/)；`doxatlas:raw_media:1e515897-64d4-4896-b936-10a04501eff4`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：$7.826 billion [capex]；166.37% [capex_yoy_growth_pct]
- 时间：Q3 / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Q3 capex was $7.826 billion, up 166.37% year-over-year

#### P25-A26. Micron reported fiscal third-quarter non-GAAP earnings per share of $25.11.

- Atomic ID：`atomic:f6794cce421538a0f1e880da`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：fiscal third-quarter / UNKNOWN

##### M01. Micron reported fiscal third-quarter non-GAAP earnings per share of $25.11.

- Mention ID：`mention:7c8d5eca7362476af3a23db37f7c26f0809edc9a63bb7e1687194369b0265fc9`
- 来源：[Micron shares surge to all-time high as Wall Street cheers unprecedented contract visibility](https://www.proactiveinvestors.com/companies/news/1094520/micron-shares-surge-to-all-time-high-as-wall-street-cheers-unprecedented-contract-visibility-1094520.html)；`doxatlas:raw_media:6b5d042a-9236-453b-8b60-2ba6c2f16b4f`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：$25.11 [non_gaap_eps]；$20.86 [consensus_non_gaap_eps]
- 时间：fiscal third-quarter / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：non-GAAP earnings per share of $25.11 doubled quarter-over-quarter and surpassed expectations of $20.86.

#### P25-A27. Micron reported fiscal third-quarter revenue of $11.3 billion, up 37% year-over-year, and data center revenue more than doubled.

- Atomic ID：`atomic:f7eb6f5cb3cef797eaf484c4`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：fiscal third-quarter / UNKNOWN

##### M01. Micron reported fiscal third-quarter revenue of $11.3 billion, up 37% year-over-year, and data center revenue more than doubled.

- Mention ID：`mention:ea0bc24268b46610d5804e50b71829e517e7cb5f1089b3011cb434ee5459ec31`
- 来源：[The AI Memory Trade Is Heating Up: Defiance Launches 2X DRAM ETF After Micron's Blowout Quarter](https://www.benzinga.com/etfs/new-etfs/26/06/60103609/the-ai-memory-trade-is-heating-up-defiance-launches-2x-dram-etf-after-microns-blowout-quarter)；`doxatlas:raw_media:238a879b-e686-457d-b83d-5b0405bd28c6`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：$11.3 billion [revenue]；37% [revenue_yoy_growth]
- 时间：fiscal third-quarter / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron reported fiscal third-quarter revenue of $11.3 billion, up 37% year-over-year, while data center revenue more than doubled

### P26. SK Hynix disclosed plans for a listing on the US Nasdaq.

- Package ID：`package:6a5f4e89f7259b5420c4ab6e`
- Family / Kind：`TRANSACTION` / `EPISODE`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：COMPANY_SKHY
- Anchor artifact / period：— / —
- 摘要：SK Hynix disclosed plans for a listing on the US Nasdaq.

#### P26-A01. SK Hynix disclosed plans for a listing on the US Nasdaq.

- Atomic ID：`atomic:ccd65e7dc63403a2ca49f2d9`
- Family / Assertion：`TRANSACTION_CAPITAL` / `PLANNED`
- Mention 数：1；Version：1
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY

##### M01. SK Hynix disclosed plans for a listing on the US Nasdaq.

- Mention ID：`mention:86fecfa91518a2e2ab72a990dec58f2d01ed7297e5bd085e9cfb5d98128d7079`
- 来源：[Investors bet on AI again after Micron reports 346% sales jump](https://edition.cnn.com/2026/06/25/business/micron-results-ai-stocks-volatility)；`doxatlas:raw_media:1aaec309-8ccb-42d6-b974-b4aa3bf2d43c`
- Family / Assertion：`TRANSACTION_CAPITAL` / `PLANNED`
- 参与者：SK Hynix (ACTOR)
- 数量：—
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：the company disclosed plans for a listing on the US Nasdaq

### P27. Citi analyst Atif Malik expects Micron's SCAs to drive 40% of its revenue over the next five years.

- Package ID：`package:6a8b93027c481a6177f98b1a`
- Family / Kind：`ANALYST_REPORT` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：COMPANY_MU, field:e67f9e751548b5656e56e418
- Anchor artifact / period：— / —
- 摘要：Citi analyst Atif Malik expects Micron's SCAs to drive 40% of its revenue over the next five years.

#### P27-A01. Citi analyst Atif Malik expects Micron's SCAs to drive 40% of its revenue over the next five years.

- Atomic ID：`atomic:18f6072927a420a622fe04e0`
- Family / Assertion：`ANALYST_ACTION` / `EXPECTED`
- Mention 数：1；Version：1
- 时间：next five years / UNKNOWN

##### M01. Citi analyst Atif Malik expects Micron's SCAs to drive 40% of its revenue over the next five years.

- Mention ID：`mention:d11e9437e19a8152028628e35ea8477327404fe501505976773b99bcf171c5cc`
- 来源：[MU Stock Soars as Micron’s Revenue More Than Quadrupled in Q3](https://www.barchart.com/story/news/3092/mu-stock-soars-as-microns-revenue-more-than-quadrupled-in-q3)；`doxatlas:raw_media:181db9d6-5959-4fb0-947e-3c6680861566`
- Family / Assertion：`ANALYST_ACTION` / `EXPECTED`
- 参与者：Atif Malik (ACTOR)；Micron (SUBJECT)
- 数量：40% [revenue_share_from_sca]
- 时间：next five years / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Malik is bullish on the firm’s SCAs, which are expected to drive some 40% of its revenue over the next five years.

### P28. Sandisk shares increased by approximately 15% in early trading on Thursday.

- Package ID：`package:6bf4e0ab6a8c3913821002e9`
- Family / Kind：`COMPANY_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：COMPANY_SNDK
- Anchor artifact / period：— / —
- 摘要：Sandisk shares increased by approximately 15% in early trading on Thursday.

#### P28-A01. Sandisk shares increased by approximately 15% in early trading on Thursday.

- Atomic ID：`atomic:fe3018c53aec9a593ebc507f`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY

##### M01. Sandisk shares increased by approximately 15% in early trading on Thursday.

- Mention ID：`mention:2dd23b273d2b832ea72ddb01e4f2b2e43f955a66f1cd7fa924b55e345930f9ee`
- 来源：[Sandisk Stock Spikes 15% After Citi Unleashes Massive Price Target Hike](https://finance.yahoo.com/markets/stocks/articles/sandisk-stock-spikes-15-citi-170934290.html)；`doxatlas:raw_media:4a7c28e1-f1dd-4efc-8d16-80debc253418`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Sandisk (SUBJECT)
- 数量：about 15% [stock_price_change_pct]
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Sandisk (NASDAQ:SNDK) shares jumped about 15% early Thursday after Citi raised its price target on the flash-storage maker and pointed to stronger demand trends following Micron's (NASDAQ:MU) latest quarterly results.

### P29. Micron Technology's market capitalization exceeded $1.2 trillion.

- Package ID：`package:6c22b5080aee34d1e9c68bd5`
- Family / Kind：`EARNINGS_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`FROZEN`；Version：14
- 层级规模：6 Atomic / 11 Mention
- Anchor entities：COMPANY_MU, field:3cf24cd9687936cf51b5d914, field:59d81af0c92f5edc552c7637, field:eef3a9eb327d70b2d8884268
- Anchor artifact / period：— / COMPANY_MU_FY2026_Q3
- 摘要：Micron Technology's market capitalization exceeded $1.2 trillion. Micron Technology's stock price increased by approximately 15% in after-hours trading following its earnings announcement. Micron's core data center unit revenue increased more than sevenfold year over year.

#### P29-A01. Micron Technology's market capitalization exceeded $1.2 trillion.

- Atomic ID：`atomic:1939b79b2d20ac87838c733a`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：2026-06-24T00:00:00 / DAY

##### M01. Micron Technology's market capitalization exceeded $1.2 trillion.

- Mention ID：`mention:1d9fd7b96a40d36c37d4f1bcd9ffca77310dd671d15a9dc76652d4469bde6700`
- 来源：[Micron Just Guided for a Staggering $50 Billion in Fiscal Q4 Revenue. Here's What It Means for the AI Trade.](https://www.fool.com/investing/2026/06/24/micron-just-guided-for-a-staggering-50-billion-in/)；`doxatlas:raw_media:c762d097-1190-4ea7-9802-5d44fadefd9f`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Micron (SUBJECT)
- 数量：$1.2 trillion [market_capitalization]
- 时间：2026-06-24T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：it puts Micron above a $1.2 trillion market capitalization.

#### P29-A02. Micron Technology's stock price increased by approximately 15% in after-hours trading following its earnings announcement.

- Atomic ID：`atomic:26d921bbfa4285190ba414b2`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- Mention 数：4；Version：4
- 时间：2026-06-24T00:00:00 / 2026-06-25T00:00:00 / DAY

##### M01. Micron Technology's stock price increased by approximately 15% in after-hours trading following its earnings announcement.

- Mention ID：`mention:e601b55da1778d8cc276b909c1e7a15f5b90fa121b07cda3773bccf79e1b384e`
- 来源：[KOSPI Spikes 5% on Opening, Riding Micron’s Surprise Earnings](https://beincrypto.com/micron-earnings-kospi-sidecar-chip-rally/)；`doxatlas:raw_media:3f25dec4-7f5f-4203-9e0f-d37c2b06a2c7`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Micron Technology (SUBJECT)
- 数量：roughly 15% [stock_change_percent]
- 时间：2026-06-24T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：The stock gained roughly 15% in after-hours trading following the announcement.

##### M02. Micron's stock rose 15% in after-hours trading on Wednesday.

- Mention ID：`mention:0e77de7e545b786c385c77b8229173bc06e88c04e5a7818cac25120ddf51ff04`
- 来源：[4 Blowout Numbers From Micron's Earnings Investors Need To See](https://www.fool.com/investing/2026/06/25/x-blowout-numbers-from-microns-earnings-investors/)；`doxatlas:raw_media:34fc2a61-fa6e-4db0-8e39-f862da0af41c`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Micron (SUBJECT)
- 数量：15% [stock_price_change_pct]
- 时间：2026-06-24T00:00:00 / 2026-06-24T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：sending the stock up 15% after hours on Wednesday

##### M03. Micron shares rose more than 15% in after-hours trading to approximately $1,213, resulting in a market valuation of roughly $1.16 trillion.

- Mention ID：`mention:7ef4d165e71912edd027b8d1be7625d9a644b22f5b696ee3bc1160d5f5717a07`
- 来源：[Micron posts record results as AI boom drives 15-fold jump in net profit](https://www.euronews.com/business/2026/06/25/micron-posts-record-results-as-ai-boom-drives-15-fold-jump-in-net-profit)；`doxatlas:raw_media:2b5bba2a-e472-4a1f-a341-5be6e4286dd1`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Micron (SUBJECT)
- 数量：more than 15% [price_change_percent]；$1,213 [share_price]；$1.16 trillion [market_cap]
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron shares rose more than 15% in after-hours trading to around $1,213, leaving the company valued at roughly $1.16 trillion (€1tn)

##### M04. Micron stock price surges to new highs.

- Mention ID：`mention:70d8341737040994af88b6581c8c42a243091f6382c128332172d7ac9bc70ace`
- 来源：[Micron surges to new highs on blockbuster earnings: Jim Lebenthal buys the stock](https://finnhub.io/api/news?id=760aa3bb75cb55185d1ad9b8997324a47603aeac7746264f37945ce55241212a)；`doxatlas:raw_media:6e04e5e6-801c-4141-ac0f-81d29029b012`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Micron (SUBJECT)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `title:0`：Micron surges to new highs

#### P29-A03. Micron's core data center unit revenue increased more than sevenfold year over year.

- Atomic ID：`atomic:34e51dabf2a1232bdc8cd75b`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：fiscal Q3 / 2026-06-24T00:00:00 / DAY

##### M01. Micron's core data center unit revenue increased more than sevenfold year over year.

- Mention ID：`mention:b974fce053efa9a08cd792fd18ebc2ef385683d0a1abdbad14f15c393df8a515`
- 来源：[Micron Just Guided for a Staggering $50 Billion in Fiscal Q4 Revenue. Here's What It Means for the AI Trade.](https://www.fool.com/investing/2026/06/24/micron-just-guided-for-a-staggering-50-billion-in/)；`doxatlas:raw_media:c762d097-1190-4ea7-9802-5d44fadefd9f`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：core data center unit (SUBJECT)
- 数量：more than sevenfold [revenue_growth_factor]
- 时间：fiscal Q3 / 2026-06-24T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：The core data center unit grew even faster, with revenue up more than sevenfold year over year.

#### P29-A04. Micron Technology stock price increased approximately 16% in after-hours trading, rising from $1,049 to $1,215.

- Atomic ID：`atomic:b941076092e65b5a1564822e`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- Mention 数：3；Version：3
- 时间：2026-06-24T00:00:00 / 2026-06-24T00:00:00 / DAY

##### M01. Micron Technology stock price increased approximately 16% in after-hours trading, rising from $1,049 to $1,215.

- Mention ID：`mention:0f1ed5ec0431c171da5b0e897d110ec80c2a3c69e35ca469d6d224ee15ddfc40`
- 来源：[Micron Just Guided for a Staggering $50 Billion in Fiscal Q4 Revenue. Here's What It Means for the AI Trade.](https://www.fool.com/investing/2026/06/24/micron-just-guided-for-a-staggering-50-billion-in/)；`doxatlas:raw_media:c762d097-1190-4ea7-9802-5d44fadefd9f`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Micron Technology (SUBJECT)
- 数量：about 16% [price_change_percent]；about $1,049 [stock_price]；about $1,215 [stock_price]
- 时间：2026-06-24T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Shares of memory specialist Micron Technology (MU 5.68%) jumped about 16% in after-hours trading on Wednesday, climbing from about $1,049 at Wednesday's close to about $1,215

##### M02. Micron's stock price increased by 16%.

- Mention ID：`mention:00aca3ac70b28aa89f59d0ae10aa2263037e935ec5a273a4b0f2d7337ad92802`
- 来源：[Micron Just Locked In $100 Billion in Sales, and Wall Street Thinks the Boom-Bust Chip Cycle Is Dead](https://247wallst.com/investing/2026/06/25/micron-just-locked-in-100-billion-in-sales-and-wall-street-thinks-the-boom-bust-chip-cycle-is-dead/)；`doxatlas:raw_media:1e515897-64d4-4896-b936-10a04501eff4`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Micron (SUBJECT)
- 数量：16% [stock_price_change_pct]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：and the stock was up 16%

##### M03. Micron stock price increased by nearly 16% following its earnings report.

- Mention ID：`mention:2bbfa608665f7f67c0279da4d7c5f202a1a548deb57fd8c2265f454e14942fc6`
- 来源：[Stock Market Today, June 25: Apple Drops After Raising Device Prices to Offset Higher Memory Costs](https://www.fool.com/coverage/stock-market-today/2026/06/25/stock-market-today-june-25-apple-drops-after-raising-device-prices-to-offset-higher-memory-costs/)；`doxatlas:raw_media:d9404d58-cd88-4ccc-a9d2-ec4004629dbc`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Micron (SUBJECT)
- 数量：nearly 16% [price_change_percent]
- 时间：2026-06-24T00:00:00 / 2026-06-24T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron stock soared nearly 16% after blockbuster earnings

#### P29-A05. Micron is shipping HBM4 memory chips in high volume to its lead customer and sending qualification samples to additional customers.

- Atomic ID：`atomic:f0ea01ad9c748d94e9e42dba`
- Family / Assertion：`PRODUCTION_SUPPLY` / `ONGOING`
- Mention 数：1；Version：1
- 时间：UNKNOWN

##### M01. Micron is shipping HBM4 memory chips in high volume to its lead customer and sending qualification samples to additional customers.

- Mention ID：`mention:b5c84177b2f5ff25d16772cb7c67e5b90077e0ae5cd495c737dbb3d383ccb55e`
- 来源：[Micron Just Guided for a Staggering $50 Billion in Fiscal Q4 Revenue. Here's What It Means for the AI Trade.](https://www.fool.com/investing/2026/06/24/micron-just-guided-for-a-staggering-50-billion-in/)；`doxatlas:raw_media:c762d097-1190-4ea7-9802-5d44fadefd9f`
- Family / Assertion：`PRODUCTION_SUPPLY` / `ONGOING`
- 参与者：Micron (ACTOR)；lead customer (TARGET)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：HBM4, built on Micron's 1-beta DRAM technology, is already in high-volume shipments to its lead customer, with qualification samples now going to additional end customers.

#### P29-A06. All four of Micron's business units reported higher revenue than both the prior quarter and the year-ago period.

- Atomic ID：`atomic:fb6b0a986bb8d6cd295b6689`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：fiscal Q3 / 2026-06-24T00:00:00 / DAY

##### M01. All four of Micron's business units reported higher revenue than both the prior quarter and the year-ago period.

- Mention ID：`mention:a419cf13145dc711992bb88691745ce5e0a367ade507fb9ff599a3f1fdadab4b`
- 来源：[Micron Just Guided for a Staggering $50 Billion in Fiscal Q4 Revenue. Here's What It Means for the AI Trade.](https://www.fool.com/investing/2026/06/24/micron-just-guided-for-a-staggering-50-billion-in/)；`doxatlas:raw_media:c762d097-1190-4ea7-9802-5d44fadefd9f`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron's business units (SUBJECT)
- 数量：—
- 时间：fiscal Q3 / 2026-06-24T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：all four of Micron's business units -- cloud memory, core data center, mobile and client, and automotive and embedded -- posted higher revenue than both the prior quarter and the year-ago period.

### P30. Humanoid robots carry 10 times the amount of memory as an average L2+ vehicle.

- Package ID：`package:6d47d35160b402968f6012e5`
- Family / Kind：`PRODUCT_SCIENCE` / `EPISODE`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：field:03c98af4f8977b0f06f8a7b2, field:5095e49ec54b14b1ac1010e8
- Anchor artifact / period：— / —
- 摘要：Humanoid robots carry 10 times the amount of memory as an average L2+ vehicle.

#### P30-A01. Humanoid robots carry 10 times the amount of memory as an average L2+ vehicle.

- Atomic ID：`atomic:a3877648b713b99f79366481`
- Family / Assertion：`PRODUCT_SCIENCE` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：UNKNOWN

##### M01. Humanoid robots carry 10 times the amount of memory as an average L2+ vehicle.

- Mention ID：`mention:04e5c382e610c4dafe4c5ba285b3e47279cd0f405898711d9a692288527cd4f2`
- 来源：[Tesla's Optimus Could Become A Bigger Memory Customer Than Its Cars, If Micron Is Right](https://www.benzinga.com/trading-ideas/long-ideas/26/06/60094569/tesla-optimus-could-become-a-bigger-memory-customer-than-its-cars-if-micron-is-right)；`doxatlas:raw_media:005d3637-8724-483c-9077-7f643d3fa481`
- Family / Assertion：`PRODUCT_SCIENCE` / `ACTUAL`
- 参与者：Humanoid robots (SUBJECT)；average L2+ vehicle (TARGET)
- 数量：10 times [memory_content_ratio]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：“Humanoid robots carry 10 times the amount of memory as an average L2+ vehicle,” Mehrotra said.

### P31. Citi analysts maintained a Buy rating on Micron shares and raised the price target to $1,400.

- Package ID：`package:6f96b26d35c5526efec3a546`
- Family / Kind：`ANALYST_REPORT` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：COMPANY_MU, field:436cb94be1ea3e80aaec4d2a
- Anchor artifact / period：— / —
- 摘要：Citi analysts maintained a Buy rating on Micron shares and raised the price target to $1,400.

#### P31-A01. Citi analysts maintained a Buy rating on Micron shares and raised the price target to $1,400.

- Atomic ID：`atomic:7a955a8354ac29e69c15a9be`
- Family / Assertion：`ANALYST_ACTION` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY

##### M01. Citi analysts maintained a Buy rating on Micron shares and raised the price target to $1,400.

- Mention ID：`mention:b9e9195c11f84768caf65ac57a3f767690a5fca62905e8cb814794ad5e4d8936`
- 来源：[MU Stock Soars as Micron’s Revenue More Than Quadrupled in Q3](https://www.barchart.com/story/news/3092/mu-stock-soars-as-microns-revenue-more-than-quadrupled-in-q3)；`doxatlas:raw_media:181db9d6-5959-4fb0-947e-3c6680861566`
- Family / Assertion：`ANALYST_ACTION` / `ACTUAL`
- 参与者：Citi analysts (ACTOR)；Micron (SUBJECT)
- 数量：$1,400 [price_target]
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：In a post-earnings research note, Citi analysts led by Atif Malik maintained their “Buy” rating on Micron shares and raised their price target to $1,400.

### P32. Jim Lebenthal is buying Micron stock.

- Package ID：`package:70dca7ead9a7b4a6afc1c4d4`
- Family / Kind：`ANALYST_REPORT` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：COMPANY_MU, field:832acd4436905959bb970caf
- Anchor artifact / period：— / —
- 摘要：Jim Lebenthal is buying Micron stock.

#### P32-A01. Jim Lebenthal is buying Micron stock.

- Atomic ID：`atomic:07f610331eba926514baf220`
- Family / Assertion：`ANALYST_ACTION` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：UNKNOWN

##### M01. Jim Lebenthal is buying Micron stock.

- Mention ID：`mention:732d51e3343b637e5ad5baba0970205f3dfb15a9847230d37b10a92dfb770073`
- 来源：[Micron surges to new highs on blockbuster earnings: Jim Lebenthal buys the stock](https://finnhub.io/api/news?id=760aa3bb75cb55185d1ad9b8997324a47603aeac7746264f37945ce55241212a)；`doxatlas:raw_media:6e04e5e6-801c-4141-ac0f-81d29029b012`
- Family / Assertion：`ANALYST_ACTION` / `ACTUAL`
- 参与者：Jim Lebenthal (ACTOR)；Micron (TARGET)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `title:0`：Jim Lebenthal buys the stock
  - E02 `VERIFIED` / `text:0`：Jim Lebenthal, Chief Market Strategist at Cerity Partners, joins CNBC's "Halftime Report" to explain why he's buying it here.

### P33. Qualcomm raised its non-handset revenue target to $40 billion by 2029, with approximately $15 billion expected from data center.

- Package ID：`package:73bd641db29dc221e6cc1782`
- Family / Kind：`EARNINGS_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：2
- 层级规模：2 Atomic / 2 Mention
- Anchor entities：COMPANY_QCOM
- Anchor artifact / period：— / —
- 摘要：Qualcomm raised its non-handset revenue target to $40 billion by 2029, with approximately $15 billion expected from data center. Qualcomm reported Q2 handset revenue of $6.024 billion, a 13% year-over-year decline, attributed to memory supply constraints among Chinese OEMs.

#### P33-A01. Qualcomm raised its non-handset revenue target to $40 billion by 2029, with approximately $15 billion expected from data center.

- Atomic ID：`atomic:4ccddc56deb7ae7a6ad57047`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- Mention 数：1；Version：1
- 时间：2029-12-31T00:00:00 / YEAR

##### M01. Qualcomm raised its non-handset revenue target to $40 billion by 2029, with approximately $15 billion expected from data center.

- Mention ID：`mention:57c6888b88090ed4d7be303e2272090eaaeb39c9b8a7f242460e2f537e1466af`
- 来源：[Micron Just Locked In $100 Billion in Sales, and Wall Street Thinks the Boom-Bust Chip Cycle Is Dead](https://247wallst.com/investing/2026/06/25/micron-just-locked-in-100-billion-in-sales-and-wall-street-thinks-the-boom-bust-chip-cycle-is-dead/)；`doxatlas:raw_media:1e515897-64d4-4896-b936-10a04501eff4`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- 参与者：Qualcomm (ACTOR)
- 数量：$40 billion [non_handset_revenue_target]；$15 billion [data_center_revenue_component]
- 时间：2029-12-31T00:00:00 / YEAR
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Qualcomm raised its non-handset revenue target to $40 billion by 2029, nearly double its prior forecast, with roughly $15 billion from data center.

#### P33-A02. Qualcomm reported Q2 handset revenue of $6.024 billion, a 13% year-over-year decline, attributed to memory supply constraints among Chinese OEMs.

- Atomic ID：`atomic:5c8f682c18dfb8fc3358cd06`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：Q2 / UNKNOWN

##### M01. Qualcomm reported Q2 handset revenue of $6.024 billion, a 13% year-over-year decline, attributed to memory supply constraints among Chinese OEMs.

- Mention ID：`mention:5f9530c767208501bb5f506d62eb230f7300cd6345e82ad0b12ae50b8ea15314`
- 来源：[Micron Just Locked In $100 Billion in Sales, and Wall Street Thinks the Boom-Bust Chip Cycle Is Dead](https://247wallst.com/investing/2026/06/25/micron-just-locked-in-100-billion-in-sales-and-wall-street-thinks-the-boom-bust-chip-cycle-is-dead/)；`doxatlas:raw_media:1e515897-64d4-4896-b936-10a04501eff4`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Qualcomm (ACTOR)
- 数量：$6.024 billion [handset_revenue]；13% [handset_revenue_yoy_change_pct]
- 时间：Q2 / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Qualcomm’s Q2 handset revenue had already fallen 13% year-over-year to $6.024 billion, with memory supply constraints among Chinese OEMs cited as the cause.

### P34. Micron guided next-quarter gross margin to 86%.

- Package ID：`package:74c07ddb560548272f47bcc1`
- Family / Kind：`COMPANY_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`FROZEN`；Version：27
- 层级规模：18 Atomic / 43 Mention
- Anchor entities：COMPANY_MU, INSTRUMENT_MU, field:15ac8f9b5ff61195783568e7
- Anchor artifact / period：— / —
- 摘要：Micron guided next-quarter gross margin to 86%. Memory shortages are expected to persist at least through 2028. Micron Technology stock increased by more than 16% in pre-market trade.

#### P34-A01. Micron guided next-quarter gross margin to 86%.

- Atomic ID：`atomic:05471f447ceeed3eaff06afa`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- Mention 数：2；Version：2
- 时间：next quarter / UNKNOWN

##### M01. Micron guided next-quarter gross margin to 86%.

- Mention ID：`mention:dc6a8bba58e24a7419b1cc5f5599df65fc1b63ce6c40430b9a136dbea5d91eba`
- 来源：[4 Blowout Numbers From Micron's Earnings Investors Need To See](https://www.fool.com/investing/2026/06/25/x-blowout-numbers-from-microns-earnings-investors/)；`doxatlas:raw_media:34fc2a61-fa6e-4db0-8e39-f862da0af41c`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- 参与者：Micron (ACTOR)
- 数量：86% [gross_margin]
- 时间：next quarter / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：management guided to a gross margin of 86%

##### M02. Micron guided gross margin to about 86%.

- Mention ID：`mention:3f5d39c83b74a5dfbc6aa8e12f9bc1c8eef93af4cfa997ff6e7f223ecf16522c`
- 来源：[Wedbush says chip rally has further to run after Micron's blowout quarter](https://www.proactiveinvestors.com/companies/news/1094490/wedbush-says-chip-rally-has-further-to-run-after-micron-s-blowout-quarter-1094490.html)；`doxatlas:raw_media:79fe1f85-a1cc-44e1-928b-d0256c075eb4`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- 参与者：Micron (ACTOR)
- 数量：about 86% [gross_margin]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Wedbush said the company expects tight supply conditions to persist beyond its 2027 financial year, with gross margin guided to about 86%.

#### P34-A02. Memory shortages are expected to persist at least through 2028.

- Atomic ID：`atomic:098606851979e3969fa47c79`
- Family / Assertion：`PRODUCTION_SUPPLY` / `EXPECTED`
- Mention 数：1；Version：1
- 时间：2028-12-31T00:00:00 / YEAR

##### M01. Memory shortages are expected to persist at least through 2028.

- Mention ID：`mention:2b8a3b579a2831fe6b27bea34c535004d071cd2a0e16d3efdbb4d0a167d47855`
- 来源：[4 Blowout Numbers From Micron's Earnings Investors Need To See](https://www.fool.com/investing/2026/06/25/x-blowout-numbers-from-microns-earnings-investors/)；`doxatlas:raw_media:34fc2a61-fa6e-4db0-8e39-f862da0af41c`
- Family / Assertion：`PRODUCTION_SUPPLY` / `EXPECTED`
- 参与者：—
- 数量：—
- 时间：2028-12-31T00:00:00 / YEAR
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：memory shortages are expected to persist at least through 2028

#### P34-A03. Micron Technology stock increased by more than 16% in pre-market trade.

- Atomic ID：`atomic:123d0953d050307066d111f2`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY

##### M01. Micron Technology stock increased by more than 16% in pre-market trade.

- Mention ID：`mention:31fd20ce6b8224fcd4e7b4664b6f8cfbdd6d7e7452efa86392f2e65316a78b0a`
- 来源：[Investors bet on AI again after Micron reports 346% sales jump](https://edition.cnn.com/2026/06/25/business/micron-results-ai-stocks-volatility)；`doxatlas:raw_media:1aaec309-8ccb-42d6-b974-b4aa3bf2d43c`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Micron Technology (SUBJECT)
- 数量：more than 16% [stock_price_change_pct]
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：sending its stock up by more than 16% in pre-market trade Thursday

#### P34-A04. Micron reported third-quarter operating margin of 80.4%.

- Atomic ID：`atomic:1d53e835cae7e3463bb33fea`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：third-quarter / UNKNOWN

##### M01. Micron reported third-quarter operating margin of 80.4%.

- Mention ID：`mention:346bd4c7e506dab0291104a18fada4a33ceae3e85aac16dd7cc288635df2919b`
- 来源：[4 Blowout Numbers From Micron's Earnings Investors Need To See](https://www.fool.com/investing/2026/06/25/x-blowout-numbers-from-microns-earnings-investors/)；`doxatlas:raw_media:34fc2a61-fa6e-4db0-8e39-f862da0af41c`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：80.4% [operating_margin]
- 时间：third-quarter / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：80.4% was Micron's operating margin in the quarter

#### P34-A05. Micron signaled a further increase in capital spending for 2027.

- Atomic ID：`atomic:25153eedd5a3ac903fba38ed`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- Mention 数：3；Version：3
- 时间：2027 / UNKNOWN

##### M01. Micron signaled a further increase in capital spending for 2027.

- Mention ID：`mention:232472fa4bab712b802e62b0d62da916d92ea9099bd25ff71323117b28d427d5`
- 来源：[Micron posts record results as AI boom drives 15-fold jump in net profit](https://www.euronews.com/business/2026/06/25/micron-posts-record-results-as-ai-boom-drives-15-fold-jump-in-net-profit)；`doxatlas:raw_media:2b5bba2a-e472-4a1f-a341-5be6e4286dd1`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- 参与者：Micron Technology (ACTOR)
- 数量：—
- 时间：2027 / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：signalling a further jump in 2027

##### M02. Micron expects quarterly capital expenditures in fiscal 2027 to exceed fiscal Q4 2026 levels.

- Mention ID：`mention:e4445c0264fe17131010d48b94e311b486417cb8d68824c2a277bc18143c5593`
- 来源：[Micron Is Spending Billions On New Fabs—And Still Says It'll Return 100% Of Excess Cash To Shareholders](https://www.benzinga.com/trading-ideas/long-ideas/26/06/60095272/micron-is-spending-billions-on-new-fabs-and-still-says-itll-return-100-of-excess-cash-to-shareholders)；`doxatlas:raw_media:4fcc4fb0-ab7d-4157-8e65-a09ef40a7581`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- 参与者：Micron (ACTOR)
- 数量：—
- 时间：fiscal 2027 / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Chief Financial Officer Mark Murphy said the company expects quarterly capital expenditures in fiscal 2027 to exceed fiscal fourth-quarter levels
  - E02 `VERIFIED` / `text:0`：“We expect quarterly CAPEX in fiscal 2027 to be above fiscal Q4 levels,”

##### M03. Micron raised its 2026 capital expenditure forecast.

- Mention ID：`mention:00bd06b500408f2bd77723922ec4cc09e575089102665571df90bcc793400df2`
- 来源：[Micron shares surge to all-time high as Wall Street cheers unprecedented contract visibility](https://www.proactiveinvestors.com/companies/news/1094520/micron-shares-surge-to-all-time-high-as-wall-street-cheers-unprecedented-contract-visibility-1094520.html)；`doxatlas:raw_media:6b5d042a-9236-453b-8b60-2ba6c2f16b4f`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- 参与者：Micron (ACTOR)
- 数量：—
- 时间：2026 / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron raised its 2026 capital expenditure forecast

#### P34-A06. Micron Technology reported fiscal Q3 2026 revenue of $41.46 billion, compared to $9.30 billion in the same quarter of the prior year.

- Atomic ID：`atomic:34cfe8775636bbfa862ed8ac`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- Mention 数：10；Version：10
- 时间：fiscal Q3 2026 / 2026-06-24T00:00:00 / 2026-06-24T00:00:00 / DAY

##### M01. Micron Technology reported fiscal Q3 2026 revenue of $41.46 billion, compared to $9.30 billion in the same quarter of the prior year.

- Mention ID：`mention:3389ccc20a674118e69617153a8e30366a884d52f1b6e052258b00afc7ed0b2f`
- 来源：[KOSPI Spikes 5% on Opening, Riding Micron’s Surprise Earnings](https://beincrypto.com/micron-earnings-kospi-sidecar-chip-rally/)；`doxatlas:raw_media:3f25dec4-7f5f-4203-9e0f-d37c2b06a2c7`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron Technology (ACTOR)
- 数量：$41.46 billion [revenue]；$9.30 billion [revenue_prior_year]
- 时间：fiscal Q3 2026 / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron reported fiscal Q3 2026 revenue of $41.46 billion, more than four times the $9.30 billion it posted in the same quarter a year earlier.

##### M02. Micron Technology reported adjusted earnings per share of $25.11 for fiscal Q3 2026, exceeding the analyst consensus estimate of $20.78.

- Mention ID：`mention:cbb0da4840d7ec882ad608d5c4b469d3b2b817653d00f1979ac0a021f1abb798`
- 来源：[KOSPI Spikes 5% on Opening, Riding Micron’s Surprise Earnings](https://beincrypto.com/micron-earnings-kospi-sidecar-chip-rally/)；`doxatlas:raw_media:3f25dec4-7f5f-4203-9e0f-d37c2b06a2c7`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron Technology (ACTOR)
- 数量：$25.11 [adjusted_eps]；around $20.78 [consensus_eps]
- 时间：fiscal Q3 2026 / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Adjusted earnings per share came in at $25.11, well above the analyst consensus of around $20.78.

##### M03. Micron reported fiscal third-quarter revenue of $41.5 billion for the period ended May 28, 2026, compared to $23.9 billion in the prior quarter and $9.3 billion in the year-ago quarter.

- Mention ID：`mention:0e3da8b7ffbc92db5ecc1278caed0fbf105f1a9dfddf1cb279477f1d2d617ed1`
- 来源：[Micron Just Guided for a Staggering $50 Billion in Fiscal Q4 Revenue. Here's What It Means for the AI Trade.](https://www.fool.com/investing/2026/06/24/micron-just-guided-for-a-staggering-50-billion-in/)；`doxatlas:raw_media:c762d097-1190-4ea7-9802-5d44fadefd9f`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：$41.5 billion [revenue]；$23.9 billion [revenue]；$9.3 billion [revenue]
- 时间：fiscal Q3 / 2026-06-24T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron's fiscal Q3 revenue, for the period ended May 28, 2026, came in at about $41.5 billion. That's up from $23.9 billion in fiscal Q2 and just $9.3 billion in the year-ago quarter

##### M04. Micron reported capital expenditures of $7.1 billion for fiscal third quarter.

- Mention ID：`mention:3c0492f6b8af76e7bd3f36a7b449bb392fa9ae53e546ad98bbb5378818aff22a`
- 来源：[Micron Just Guided for a Staggering $50 Billion in Fiscal Q4 Revenue. Here's What It Means for the AI Trade.](https://www.fool.com/investing/2026/06/24/micron-just-guided-for-a-staggering-50-billion-in/)；`doxatlas:raw_media:c762d097-1190-4ea7-9802-5d44fadefd9f`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：$7.1 billion [capital_expenditures]
- 时间：fiscal Q3 / 2026-06-24T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron's capital expenditures climbed to $7.1 billion in fiscal Q3 alone as it races to bring new HBM capacity online.

##### M05. Micron reported third-quarter revenue of $41.5 billion.

- Mention ID：`mention:2e3834b1052a79ac1c7d0415cd58ea0ef05d7ac29e6cebbd47636386267a62c0`
- 来源：[4 Blowout Numbers From Micron's Earnings Investors Need To See](https://www.fool.com/investing/2026/06/25/x-blowout-numbers-from-microns-earnings-investors/)；`doxatlas:raw_media:34fc2a61-fa6e-4db0-8e39-f862da0af41c`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：$41.5 billion [revenue]；346% [revenue_growth_yoy]
- 时间：third-quarter / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron reported 346% revenue growth in the quarter to $41.5 billion

##### M06. Micron reported revenue of $41.46 billion.

- Mention ID：`mention:4eedc226d62c2b262abe8780927439ffa408c0c4b0ae6e2c5b4f75ed445a5efa`
- 来源：[Micron CEO Says AI Memory Shortage Could Last Beyond 2028: These ETFs Are Positioned To Profit](https://www.benzinga.com/etfs/sector-etfs/26/06/60094351/micron-ceo-says-ai-memory-shortage-could-last-beyond-2028-these-etfs-are-positioned-to-profit)；`doxatlas:raw_media:bf5258ec-37f8-408b-ace2-8b016d16f725`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：$41.46 billion [revenue]；$35.6 billion [revenue]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Revenue of $41.46 billion, up roughly 346% from a year earlier and well ahead of Wall Street estimates of $35.6 billion.

##### M07. Micron reported third-quarter revenue of $41.4 billion, exceeding analyst forecasts of $35.7 billion and prior-year revenue of $9.3 billion.

- Mention ID：`mention:42e75637f3804a252bdac99a04b214fc5efd06887fad8d95481e5f92fc1fdc7e`
- 来源：[Micron posts record results as AI boom drives 15-fold jump in net profit](https://www.euronews.com/business/2026/06/25/micron-posts-record-results-as-ai-boom-drives-15-fold-jump-in-net-profit)；`doxatlas:raw_media:2b5bba2a-e472-4a1f-a341-5be6e4286dd1`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron Technology (ACTOR)
- 数量：$41.4 billion [revenue]；$35.7 billion [revenue_forecast]；$9.3 billion [revenue_prior_year]
- 时间：third quarter / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：revenue in the third quarter reached $41.4 billion (€36.5bn), more than four times the $9.3 billion (€8.2bn) it recorded in the same period last year
  - E02 `VERIFIED` / `text:0`：The figure also comfortably beat the roughly $35.7 billion (€31.4bn) analysts had forecast

##### M08. Micron reported third-quarter revenue of $41.46 billion.

- Mention ID：`mention:125a3084e2150182b6f2fc619ca8877aa4e5d5904302a648803a5c7b858c78c4`
- 来源：[These Analysts Boost Their Forecasts On Micron Technology After Better-Than-Expected Q3 Results](https://www.benzinga.com/analyst-stock-ratings/price-target/26/06/60100007/these-analysts-boost-their-forecasts-on-micron-technology-after-better-than-expected-q3-results)；`doxatlas:raw_media:5d890b9d-c789-4a99-87d6-8a34a3ea4cc2`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：$41.46 billion [revenue]；$35.59 billion [revenue_estimate]
- 时间：third-quarter / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron reported third-quarter revenue of $41.46 billion, exceeding analyst estimates of $35.59 billion, according to Benzinga Pro.

##### M09. Micron Technology reported third-quarter revenue increase of 346% year-over-year.

- Mention ID：`mention:eb827c6ff457119fee45511960d9b8d22429bc8f8cb81a5e01b084192a434f13`
- 来源：[Investors bet on AI again after Micron reports 346% sales jump](https://edition.cnn.com/2026/06/25/business/micron-results-ai-stocks-volatility)；`doxatlas:raw_media:1aaec309-8ccb-42d6-b974-b4aa3bf2d43c`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron Technology (ACTOR)
- 数量：346% [revenue_yoy_change_pct]
- 时间：third quarter / 2026-06-24T00:00:00 / 2026-06-24T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Revenues soared 346% over the same period

##### M10. Micron reported fiscal Q3 revenue of $41.456 billion, representing a 345.72% year-over-year increase from $9.3 billion in the prior-year quarter and beating consensus by 17.60%.

- Mention ID：`mention:11ef46c35a7892a57491c23c1bec72ff31b77eb851488abe9c415ad6063df95a`
- 来源：[Micron Just Locked In $100 Billion in Sales, and Wall Street Thinks the Boom-Bust Chip Cycle Is Dead](https://247wallst.com/investing/2026/06/25/micron-just-locked-in-100-billion-in-sales-and-wall-street-thinks-the-boom-bust-chip-cycle-is-dead/)；`doxatlas:raw_media:1e515897-64d4-4896-b936-10a04501eff4`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：$41.456 billion [revenue]；17.60% [revenue_consensus_beat_pct]；345.72% [revenue_yoy_growth_pct]；$9.3 billion [prior_year_revenue]
- 时间：fiscal Q3 / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron’s fiscal Q3 revenue came in at $41.456 billion, a 17.60% beat on consensus and 345.72% year-over-year growth from the $9.3 billion Micron printed in the prior-year quarter.

#### P34-A07. Micron reported adjusted earnings of $25.11 per share.

- Atomic ID：`atomic:492c87e2a7360a08436b1371`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- Mention 数：3；Version：3
- 时间：third quarter / UNKNOWN

##### M01. Micron reported adjusted earnings of $25.11 per share.

- Mention ID：`mention:9241a460aa1f709d61f7ad14a0ea8f076a2e55a9eaf4ba6ee9a391250ee86b21`
- 来源：[Micron CEO Says AI Memory Shortage Could Last Beyond 2028: These ETFs Are Positioned To Profit](https://www.benzinga.com/etfs/sector-etfs/26/06/60094351/micron-ceo-says-ai-memory-shortage-could-last-beyond-2028-these-etfs-are-positioned-to-profit)；`doxatlas:raw_media:bf5258ec-37f8-408b-ace2-8b016d16f725`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：$25.11 per share [adjusted_earnings_per_share]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Adjusted earnings of $25.11 per share, which topped expectations

##### M02. Micron reported adjusted earnings per share of $25.11, beating the expected $20.49.

- Mention ID：`mention:3f6ba562f6136a0e799ba9c8c4a0edfde318ec66c90d556ea2d8374b18eacc52`
- 来源：[Micron posts record results as AI boom drives 15-fold jump in net profit](https://www.euronews.com/business/2026/06/25/micron-posts-record-results-as-ai-boom-drives-15-fold-jump-in-net-profit)；`doxatlas:raw_media:2b5bba2a-e472-4a1f-a341-5be6e4286dd1`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron Technology (ACTOR)
- 数量：$25.11 a share [adjusted_eps]；$20.49 expected [adjusted_eps_forecast]
- 时间：third quarter / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Adjusted earnings of $25.11 a share sailed past the $20.49 expected

##### M03. Micron reported third-quarter adjusted earnings of $25.11 per share.

- Mention ID：`mention:4012c18da1d940ded92bd72042e6d037de1dc21e123b95cdd719902e0d609c27`
- 来源：[These Analysts Boost Their Forecasts On Micron Technology After Better-Than-Expected Q3 Results](https://www.benzinga.com/analyst-stock-ratings/price-target/26/06/60100007/these-analysts-boost-their-forecasts-on-micron-technology-after-better-than-expected-q3-results)；`doxatlas:raw_media:5d890b9d-c789-4a99-87d6-8a34a3ea4cc2`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：$25.11 per share [adjusted_earnings_per_share]；$20.63 per share [adjusted_earnings_per_share_estimate]
- 时间：third-quarter / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：The semiconductor company posted adjusted earnings of $25.11 per share, beating analyst estimates of $20.63 per share.

#### P34-A08. Micron reported third-quarter gross margin of 84.6%.

- Atomic ID：`atomic:51eb29ce1aebdc61fa7c5d62`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- Mention 数：3；Version：3
- 时间：third-quarter / 2026-06-24T00:00:00 / 2026-06-24T00:00:00 / DAY

##### M01. Micron reported third-quarter gross margin of 84.6%.

- Mention ID：`mention:d605043a78a4f1e5257bc558a36058630c59e1b171993aef63bd89bb0e17beea`
- 来源：[4 Blowout Numbers From Micron's Earnings Investors Need To See](https://www.fool.com/investing/2026/06/25/x-blowout-numbers-from-microns-earnings-investors/)；`doxatlas:raw_media:34fc2a61-fa6e-4db0-8e39-f862da0af41c`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：84.6% [gross_margin]；81% [gross_margin_guidance]
- 时间：third-quarter / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron's gross margin came in at 84.6% in the quarter, ahead of its own guidance at 81%

##### M02. Micron reported GAAP gross margin of 84.6%, compared to 37.7% in the prior year.

- Mention ID：`mention:7e34cf0c07f79b32cfcfa5931db6109db5ed27422017bf95e2350b08251f77e5`
- 来源：[Micron Just Locked In $100 Billion in Sales, and Wall Street Thinks the Boom-Bust Chip Cycle Is Dead](https://247wallst.com/investing/2026/06/25/micron-just-locked-in-100-billion-in-sales-and-wall-street-thinks-the-boom-bust-chip-cycle-is-dead/)；`doxatlas:raw_media:1e515897-64d4-4896-b936-10a04501eff4`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：84.6% [gaap_gross_margin]；37.7% [prior_year_gaap_gross_margin]
- 时间：fiscal Q3 / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：GAAP gross margin was 84.6%, against 37.7% a year earlier.

##### M03. Micron Technology reported earnings with revenue quadrupling year over year and a record adjusted gross margin of about 85%.

- Mention ID：`mention:38d4975b3a55c0ded32909a369abdb8e299d3c7b2b2880a5c5b93cc2936e2de6`
- 来源：[Stock Market Today, June 25: Apple Drops After Raising Device Prices to Offset Higher Memory Costs](https://www.fool.com/coverage/stock-market-today/2026/06/25/stock-market-today-june-25-apple-drops-after-raising-device-prices-to-offset-higher-memory-costs/)；`doxatlas:raw_media:d9404d58-cd88-4ccc-a9d2-ec4004629dbc`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron Technology (ACTOR)
- 数量：about 85% [adjusted_gross_margin]；quadrupling year over year [revenue_growth_yoy]
- 时间：2026-06-24T00:00:00 / 2026-06-24T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron Technology (MU 5.68%) reported stellar earnings after the bell yesterday. Micron stock soared nearly 16% after blockbuster earnings that included revenue quadrupling year over year and a record adjusted gross margin of about 85%.

#### P34-A09. Micron expects industry memory supply to improve gradually in 2028 but lacks visibility on when it will meet AI demand.

- Atomic ID：`atomic:64e179d06e3a7c66bf63b19a`
- Family / Assertion：`PRODUCTION_SUPPLY` / `EXPECTED`
- Mention 数：4；Version：4
- 时间：2028 / UNKNOWN

##### M01. Micron expects industry memory supply to improve gradually in 2028 but lacks visibility on when it will meet AI demand.

- Mention ID：`mention:d8c457e0738c22be1b459d2f7ab0b8803999230b0c750fd43b2715cdb86db0ee`
- 来源：[Micron CEO Expects Memory Supply To Improve Gradually In 2028, But There's No 'Line Of Sight' When It Will Catch Up To Rising AI Demand](https://www.benzinga.com/markets/tech/26/06/60089761/micron-ceo-sees-memory-supply-improving-by-2028-but-no-clear-end-in-sight-to-ai-demand-shortfallmicron-ceo-sees-memory-supply-improving-by-2028-but-no-clear-end-in-sight-to-ai-demand-shortfall)；`doxatlas:raw_media:25387f96-b423-437c-b2cd-8652aa176bd8`
- Family / Assertion：`PRODUCTION_SUPPLY` / `EXPECTED`
- 参与者：Micron (ACTOR)
- 数量：—
- 时间：2028 / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：“Even as we expect industry supply to improve gradually in 2028, we currently do not have line of sight as to when memory supply will be able to catch up with increasing demand,”

##### M02. Micron indicated that AI memory supply constraints could persist beyond 2028.

- Mention ID：`mention:416efec8b77a036e8d906cd8c327ea0e5f81dde851bdcd97a44c1c9f7d3576c0`
- 来源：[Micron CEO Says AI Memory Shortage Could Last Beyond 2028: These ETFs Are Positioned To Profit](https://www.benzinga.com/etfs/sector-etfs/26/06/60094351/micron-ceo-says-ai-memory-shortage-could-last-beyond-2028-these-etfs-are-positioned-to-profit)；`doxatlas:raw_media:bf5258ec-37f8-408b-ace2-8b016d16f725`
- Family / Assertion：`PRODUCTION_SUPPLY` / `EXPECTED`
- 参与者：Micron (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `title:0`：Micron CEO Says AI Memory Shortage Could Last Beyond 2028
  - E02 `VERIFIED` / `text:0`：Micron’s indication that supply constraints could persist through 2028

##### M03. High-bandwidth memory (HBM) remains in tight supply.

- Mention ID：`mention:42f5b48f8e439e2f37086d91690a09318835c363bcb73d0d1c78d1fb2aae1a17`
- 来源：[Micron CEO Says AI Memory Shortage Could Last Beyond 2028: These ETFs Are Positioned To Profit](https://www.benzinga.com/etfs/sector-etfs/26/06/60094351/micron-ceo-says-ai-memory-shortage-could-last-beyond-2028-these-etfs-are-positioned-to-profit)；`doxatlas:raw_media:bf5258ec-37f8-408b-ace2-8b016d16f725`
- Family / Assertion：`PRODUCTION_SUPPLY` / `ONGOING`
- 参与者：High-bandwidth memory (HBM) (SUBJECT)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：High-bandwidth memory (HBM), a critical component used alongside advanced AI processors, remains in tight supply as hyperscalers and enterprises continue pouring money into AI infrastructure.

##### M04. Tight memory market conditions are expected to persist beyond calendar 2027 due to supply inability to catch up with increasing demand.

- Mention ID：`mention:a4a1d76a91b315be48148a8adedef5d6b1012665181e18b9d817905858b6964f`
- 来源：[Tesla's Optimus Could Become A Bigger Memory Customer Than Its Cars, If Micron Is Right](https://www.benzinga.com/trading-ideas/long-ideas/26/06/60094569/tesla-optimus-could-become-a-bigger-memory-customer-than-its-cars-if-micron-is-right)；`doxatlas:raw_media:005d3637-8724-483c-9077-7f643d3fa481`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- 参与者：Micron (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron now expecting tight memory market conditions to persist beyond calendar 2027. “We currently do not have line of sight as to when memory supply will be able to catch up with increasing demand,” Mehrotra said.

#### P34-A10. Micron increased planned capital spending to approximately $27 billion for the current fiscal year.

- Atomic ID：`atomic:748a0948fae6bf852a47cb4f`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `PLANNED`
- Mention 数：2；Version：2
- 时间：this fiscal year / UNKNOWN

##### M01. Micron increased planned capital spending to approximately $27 billion for the current fiscal year.

- Mention ID：`mention:33f074d0d33a5e0c4122f38b87f961e0b239eacee9ddcf702f20f94ae04d9b6a`
- 来源：[Micron posts record results as AI boom drives 15-fold jump in net profit](https://www.euronews.com/business/2026/06/25/micron-posts-record-results-as-ai-boom-drives-15-fold-jump-in-net-profit)；`doxatlas:raw_media:2b5bba2a-e472-4a1f-a341-5be6e4286dd1`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `PLANNED`
- 参与者：Micron Technology (ACTOR)
- 数量：$27 billion [capital_spending_plan]
- 时间：this fiscal year / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：lifting planned capital spending to about $27 billion (€23.7bn) this fiscal year

##### M02. Micron expects fiscal 2026 capital expenditures to total approximately $27 billion, with fiscal Q4 2026 capital expenditures of around $10 billion.

- Mention ID：`mention:bba8494fbe854532b0b44ec44abdec0e75ab96b409e1d0b853628cfb24f37b6e`
- 来源：[Micron Is Spending Billions On New Fabs—And Still Says It'll Return 100% Of Excess Cash To Shareholders](https://www.benzinga.com/trading-ideas/long-ideas/26/06/60095272/micron-is-spending-billions-on-new-fabs-and-still-says-itll-return-100-of-excess-cash-to-shareholders)；`doxatlas:raw_media:4fcc4fb0-ab7d-4157-8e65-a09ef40a7581`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- 参与者：Micron (ACTOR)
- 数量：approximately $27 billion [capital_expenditures]；around $10 billion [capital_expenditures_q4_2026]
- 时间：fiscal 2026 / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron expects fiscal fourth-quarter capital expenditures of around $10 billion, bringing total fiscal 2026 capital spending to approximately $27 billion.

#### P34-A11. Micron is investing at record levels in technology, products and supply.

- Atomic ID：`atomic:8a7eb6914edba7c3cfe6602e`
- Family / Assertion：`COMMERCIAL_OPERATION` / `ONGOING`
- Mention 数：2；Version：2
- 时间：UNKNOWN

##### M01. Micron is investing at record levels in technology, products and supply.

- Mention ID：`mention:b3ef0971d0b732e789ddc701d4457a58f6fc34d8b8cb932d47446b86876ae3ff`
- 来源：[These Analysts Boost Their Forecasts On Micron Technology After Better-Than-Expected Q3 Results](https://www.benzinga.com/analyst-stock-ratings/price-target/26/06/60100007/these-analysts-boost-their-forecasts-on-micron-technology-after-better-than-expected-q3-results)；`doxatlas:raw_media:5d890b9d-c789-4a99-87d6-8a34a3ea4cc2`
- Family / Assertion：`COMMERCIAL_OPERATION` / `ONGOING`
- 参与者：Micron (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`："Micron is investing at record levels in technology, products and supply to address our customers’ rapidly growing demand.

##### M02. Micron is investing at record levels to meet customer demand.

- Mention ID：`mention:f70e1647f55bb67fe8da9e43dfb4793ce17d22f3e571a828fac430e09a6d4ffc`
- 来源：[Why Sandisk Stock Soared Today](https://www.fool.com/investing/2026/06/25/why-sandisk-stock-soared-today/)；`doxatlas:raw_media:35a4333e-6608-482b-b963-ba2f64392063`
- Family / Assertion：`COMMERCIAL_OPERATION` / `ONGOING`
- 参与者：Micron (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron's "investing at record levels" to help meet customer demand

#### P34-A12. Micron reported a gross margin of approximately 85% for the quarter, a level comparable to or exceeding Nvidia and Meta.

- Atomic ID：`atomic:8ecf6cb3966d28acce2fc1ab`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：third quarter / UNKNOWN

##### M01. Micron reported a gross margin of approximately 85% for the quarter, a level comparable to or exceeding Nvidia and Meta.

- Mention ID：`mention:f80257251ecffd14429471d85c8aef3b58a947ea191aace49b55813577f6597b`
- 来源：[Micron posts record results as AI boom drives 15-fold jump in net profit](https://www.euronews.com/business/2026/06/25/micron-posts-record-results-as-ai-boom-drives-15-fold-jump-in-net-profit)；`doxatlas:raw_media:2b5bba2a-e472-4a1f-a341-5be6e4286dd1`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron Technology (ACTOR)
- 数量：around 85% [gross_margin]
- 时间：third quarter / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：The company reported a gross margin of around 85% for the quarter
  - E02 `VERIFIED` / `text:0`：a level that now rivals or exceeds those of far larger technology names such as Nvidia and Meta

#### P34-A13. Micron reported third-quarter net income of $28.2 billion.

- Atomic ID：`atomic:ad55cc1bf5fe127b029b9dc6`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- Mention 数：3；Version：3
- 时间：third-quarter / 2026-06-24T00:00:00 / 2026-06-24T00:00:00 / DAY

##### M01. Micron reported third-quarter net income of $28.2 billion.

- Mention ID：`mention:1e65e6c27eb950350fc12d3d9ae86d5078c4a023f55d70196991e093fa23b3d3`
- 来源：[4 Blowout Numbers From Micron's Earnings Investors Need To See](https://www.fool.com/investing/2026/06/25/x-blowout-numbers-from-microns-earnings-investors/)；`doxatlas:raw_media:34fc2a61-fa6e-4db0-8e39-f862da0af41c`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：$28.2 billion [net_income]
- 时间：third-quarter / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron produced $28.2 billion in net income

##### M02. Micron reported Q3 financial results that exceeded market expectations

- Mention ID：`mention:5ca7331aaced84c1b0fcea0a56fd196c2460761f2aa06c75132db587015046da`
- 来源：[Dow Surges 250 Points; Micron Posts Upbeat Q3 Earnings](https://finnhub.io/api/news?id=7316a88ccda79b3354a49965fa82f15ed81fe3722e51491463cf118f097a0143)；`doxatlas:raw_media:fce73732-1c68-4d0f-9d7f-c5800393ffff`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron beats expectations with Q3 results

##### M03. Micron Technology reported third-quarter profit of $28.2 billion.

- Mention ID：`mention:6bbc7b23480571752df2a7c814a42ec319bdfd8c9c78f66ee63f9e15ac6d0ee6`
- 来源：[Investors bet on AI again after Micron reports 346% sales jump](https://edition.cnn.com/2026/06/25/business/micron-results-ai-stocks-volatility)；`doxatlas:raw_media:1aaec309-8ccb-42d6-b974-b4aa3bf2d43c`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron Technology (ACTOR)
- 数量：$28.2 billion [net_income]；almost 15 times more [net_income_yoy_multiplier]
- 时间：third quarter / 2026-06-24T00:00:00 / 2026-06-24T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：The company reported a surge in profit during its third quarter to $28.2 billion – almost 15 times more than what it made during the same quarter a year ago

#### P34-A14. Micron shares traded at $1,167.88 on Thursday, representing an 11.5% increase.

- Atomic ID：`atomic:bb54f972a586ab1d539cbe68`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- Mention 数：2；Version：2
- 时间：2026-06-25T00:00:00 / 2026-06-25T11:50:00 / DAY

##### M01. Micron shares traded at $1,167.88 on Thursday, representing an 11.5% increase.

- Mention ID：`mention:13adefe4eb553987fb5874f63d7494a2e42098db0a2067f29892ca1b8f6261ef`
- 来源：[These Analysts Boost Their Forecasts On Micron Technology After Better-Than-Expected Q3 Results](https://www.benzinga.com/analyst-stock-ratings/price-target/26/06/60100007/these-analysts-boost-their-forecasts-on-micron-technology-after-better-than-expected-q3-results)；`doxatlas:raw_media:5d890b9d-c789-4a99-87d6-8a34a3ea4cc2`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Micron shares (SUBJECT)
- 数量：$1,167.88 [share_price]；11.5% [price_change_percent]
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron shares jumped 11.5% to trade at $1,167.88 on Thursday.

##### M02. Micron stock traded nearly 14% higher as of 11:50 a.m. ET on June 25, 2026.

- Mention ID：`mention:56660911311838c55c44f7d5eab106ce0f012f552a58d8695e4787408b9bea33`
- 来源：[Why Everyone Is Talking About Micron](https://www.fool.com/investing/2026/06/25/why-everyone-is-talking-about-micron/)；`doxatlas:raw_media:49fb27c5-84b4-4e56-8791-f7ac23de38b7`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Micron stock (SUBJECT)
- 数量：nearly 14% [price_change_percent]
- 时间：2026-06-25T11:50:00 / 2026-06-25T11:50:00 / TIMESTAMP
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron stock traded nearly 14% higher, as of 11:50 a.m. ET.

#### P34-A15. A sustained, substantial multi-decade memory demand cycle will begin in the latter part of this decade.

- Atomic ID：`atomic:bcdf43c20ce42c6f595cbea7`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- Mention 数：1；Version：1
- 时间：UNKNOWN

##### M01. A sustained, substantial multi-decade memory demand cycle will begin in the latter part of this decade.

- Mention ID：`mention:e6e9a1ef2ee0b012e338773054b64e077559b398fdd119661fe591d71d47fd5c`
- 来源：[Tesla's Optimus Could Become A Bigger Memory Customer Than Its Cars, If Micron Is Right](https://www.benzinga.com/trading-ideas/long-ideas/26/06/60094569/tesla-optimus-could-become-a-bigger-memory-customer-than-its-cars-if-micron-is-right)；`doxatlas:raw_media:005d3637-8724-483c-9077-7f643d3fa481`
- Family / Assertion：`GUIDANCE_EXPECTATION` / `EXPECTED`
- 参与者：Micron (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：“We expect a sustained, substantial multi-decade memory demand cycle to begin in the latter part of this decade.”

#### P34-A16. Micron reported net income of $28.24 billion ($24.67 per share) for the quarter, compared to less than $2 billion in the prior year period.

- Atomic ID：`atomic:d3752625b403e31b5c2e1550`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- Mention 数：2；Version：2
- 时间：third quarter / UNKNOWN

##### M01. Micron reported net income of $28.24 billion ($24.67 per share) for the quarter, compared to less than $2 billion in the prior year period.

- Mention ID：`mention:e190f53cc2d2b4d52d79573b260e7ea4434139828cd9ea44fc7defb4afe8fa69`
- 来源：[Micron posts record results as AI boom drives 15-fold jump in net profit](https://www.euronews.com/business/2026/06/25/micron-posts-record-results-as-ai-boom-drives-15-fold-jump-in-net-profit)；`doxatlas:raw_media:2b5bba2a-e472-4a1f-a341-5be6e4286dd1`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron Technology (ACTOR)
- 数量：$28.24 billion [net_income]；$24.67 per share [eps]；less than $2 billion [net_income_prior_year]
- 时间：third quarter / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：The Idaho-based group posted net income of $28.24 billion (€24.9bn), or $24.67 per share, against less than $2 billion (€1.7bn) a year ago

##### M02. Micron reported a 104% sequential increase in GAAP profits.

- Mention ID：`mention:5307ea6db172567a166df7525ff65642991e9577ae7b511247d66ef31e8a5035`
- 来源：[Why Sandisk Stock Soared Today](https://www.fool.com/investing/2026/06/25/why-sandisk-stock-soared-today/)；`doxatlas:raw_media:35a4333e-6608-482b-b963-ba2f64392063`
- Family / Assertion：`FINANCIAL_PERFORMANCE` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：104% [gaap_profit_change_sequential]
- 时间：fiscal Q3 / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Sandisk's archrival just reported GAAP profits up 104% sequentially.

#### P34-A17. Micron shares closed at $1,048.51 on June 24, 2026, and traded at $1,213.96 in after-hours trading.

- Atomic ID：`atomic:d8b3fd956fe16781db3a3f24`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：2026-06-24T00:00:00 / DAY

##### M01. Micron shares closed at $1,048.51 on June 24, 2026, and traded at $1,213.96 in after-hours trading.

- Mention ID：`mention:f65b8eb20bf765858f2d0072f8b03e9397007275694b00413c2529fdd970019a`
- 来源：[Micron CEO Expects Memory Supply To Improve Gradually In 2028, But There's No 'Line Of Sight' When It Will Catch Up To Rising AI Demand](https://www.benzinga.com/markets/tech/26/06/60089761/micron-ceo-sees-memory-supply-improving-by-2028-but-no-clear-end-in-sight-to-ai-demand-shortfallmicron-ceo-sees-memory-supply-improving-by-2028-but-no-clear-end-in-sight-to-ai-demand-shortfall)；`doxatlas:raw_media:25387f96-b423-437c-b2cd-8652aa176bd8`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Micron (SUBJECT)
- 数量：$1,048.51 [closing_price]；$1,213.96 [after_hours_price]
- 时间：2026-06-24T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron shares closed down 0.31% at $1,048.51 on Wednesday but surged 15.78% to $1,213.96 in after-hours trading, according to Benzinga Pro.

#### P34-A18. Micron is investing in leading-edge DRAM fabs in Idaho and New York, continuing expansion in Taiwan and Singapore, and adding advanced packaging capacity for HBM products.

- Atomic ID：`atomic:fa8016b9d571eae8ad6338a6`
- Family / Assertion：`PRODUCTION_SUPPLY` / `PLANNED`
- Mention 数：1；Version：1
- 时间：UNKNOWN

##### M01. Micron is investing in leading-edge DRAM fabs in Idaho and New York, continuing expansion in Taiwan and Singapore, and adding advanced packaging capacity for HBM products.

- Mention ID：`mention:fc262837b934574243656bc878a3bc6a5c762d32f6f839c9712c4b76539fa725`
- 来源：[Micron Is Spending Billions On New Fabs—And Still Says It'll Return 100% Of Excess Cash To Shareholders](https://www.benzinga.com/trading-ideas/long-ideas/26/06/60095272/micron-is-spending-billions-on-new-fabs-and-still-says-itll-return-100-of-excess-cash-to-shareholders)；`doxatlas:raw_media:4fcc4fb0-ab7d-4157-8e65-a09ef40a7581`
- Family / Assertion：`PRODUCTION_SUPPLY` / `PLANNED`
- 参与者：Micron (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：The investments include leading-edge DRAM fabs in Idaho and New York, continued expansion in Taiwan and Singapore, and additional advanced packaging capacity aimed at supporting next-generation high-bandwidth memory (HBM) products

### P35. Bernstein analyst Mark Newman suggested that Micron's new strategic customer agreements could include pricing ceilings, limiting headroom and failing to avoid cyclicality.

- Package ID：`package:7979eb2d42e369337dcd3d5e`
- Family / Kind：`ANALYST_REPORT` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：INSTITUTION_BERNSTEIN, PERSON_MARK_J_NEWMAN
- Anchor artifact / period：— / —
- 摘要：Bernstein analyst Mark Newman suggested that Micron's new strategic customer agreements could include pricing ceilings, limiting headroom and failing to avoid cyclicality.

#### P35-A01. Bernstein analyst Mark Newman suggested that Micron's new strategic customer agreements could include pricing ceilings, limiting headroom and failing to avoid cyclicality.

- Atomic ID：`atomic:8c4fa8b2d97d35eae7f73e3c`
- Family / Assertion：`ANALYST_ACTION` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：UNKNOWN

##### M01. Bernstein analyst Mark Newman suggested that Micron's new strategic customer agreements could include pricing ceilings, limiting headroom and failing to avoid cyclicality.

- Mention ID：`mention:11aa223f72b716699fbd57c59b427ee1ce212b5ecf155706e2ad844e5a519bb8`
- 来源：[Why Everyone Is Talking About Micron](https://www.fool.com/investing/2026/06/25/why-everyone-is-talking-about-micron/)；`doxatlas:raw_media:49fb27c5-84b4-4e56-8791-f7ac23de38b7`
- Family / Assertion：`ANALYST_ACTION` / `ACTUAL`
- 参与者：Mark Newman (ACTOR)；Bernstein (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Bernstein analyst Mark Newman thinks Micron’s new strategic customer agreements could include pricing ceilings
  - E02 `VERIFIED` / `text:0`："We wonder if the ceiling suggests limited headroom," he wrote in a recent research note, suggesting these contracts likely wouldn’t be able to avoid cyclicality.

### P36. Individual investors net purchased approximately 490 billion won worth of stocks.

- Package ID：`package:8fd0a99cc0a1300f6a6f29c2`
- Family / Kind：`TRANSACTION` / `EPISODE`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：field:e87846d7bf4ac2f6de012b37
- Anchor artifact / period：— / —
- 摘要：Individual investors net purchased approximately 490 billion won worth of stocks.

#### P36-A01. Individual investors net purchased approximately 490 billion won worth of stocks.

- Atomic ID：`atomic:794fb83c44a0e132990c6546`
- Family / Assertion：`TRANSACTION_CAPITAL` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：2026-06-25T00:00:00 / DAY

##### M01. Individual investors net purchased approximately 490 billion won worth of stocks.

- Mention ID：`mention:e52bb6ca37de1c483925c6ae4b8f1f700af4f07d9f2ca6884171ca438e6c0065`
- 来源：[KOSPI Spikes 5% on Opening, Riding Micron’s Surprise Earnings](https://beincrypto.com/micron-earnings-kospi-sidecar-chip-rally/)；`doxatlas:raw_media:3f25dec4-7f5f-4203-9e0f-d37c2b06a2c7`
- Family / Assertion：`TRANSACTION_CAPITAL` / `ACTUAL`
- 参与者：individuals (ACTOR)
- 数量：roughly 490 billion won [net_buy_value]
- 时间：2026-06-25T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：individuals net bought roughly 490 billion won

### P37. Apple trading volume reached 106.4 million shares, which was 119% above its three-month average of 48.5 million shares.

- Package ID：`package:9141a03ee5169c9dec0e1e8e`
- Family / Kind：`COMPANY_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：COMPANY_AAPL
- Anchor artifact / period：— / —
- 摘要：Apple trading volume reached 106.4 million shares, which was 119% above its three-month average of 48.5 million shares.

#### P37-A01. Apple trading volume reached 106.4 million shares, which was 119% above its three-month average of 48.5 million shares.

- Atomic ID：`atomic:26aaa44a29988e4e6f01ca58`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY

##### M01. Apple trading volume reached 106.4 million shares, which was 119% above its three-month average of 48.5 million shares.

- Mention ID：`mention:369f77a010575c23ec97d6e7eac216b967417aaebf0deac3510d6b1ad35b1334`
- 来源：[Stock Market Today, June 25: Apple Drops After Raising Device Prices to Offset Higher Memory Costs](https://www.fool.com/coverage/stock-market-today/2026/06/25/stock-market-today-june-25-apple-drops-after-raising-device-prices-to-offset-higher-memory-costs/)；`doxatlas:raw_media:d9404d58-cd88-4ccc-a9d2-ec4004629dbc`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Apple (SUBJECT)
- 数量：106.4 million shares [trading_volume]；48.5 million shares [average_trading_volume]；119% above [volume_deviation_percent]
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Trading volume reached 106.4 million shares, coming in about 119% above its three-month average of 48.5 million shares.

### P38. The Korea Exchange activated a buy-side sidecar mechanism, suspending program trading for five minutes shortly after the market open.

- Package ID：`package:91f1fb9f2adfe99dfa595916`
- Family / Kind：`REGULATORY_LEGAL` / `EPISODE`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：field:ab715c4807c9a169688b1ac1
- Anchor artifact / period：— / —
- 摘要：The Korea Exchange activated a buy-side sidecar mechanism, suspending program trading for five minutes shortly after the market open.

#### P38-A01. The Korea Exchange activated a buy-side sidecar mechanism, suspending program trading for five minutes shortly after the market open.

- Atomic ID：`atomic:5966cea56736096920b968fd`
- Family / Assertion：`REGULATORY_LEGAL_POLICY` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：2026-06-25T00:00:00 / DAY

##### M01. The Korea Exchange activated a buy-side sidecar mechanism, suspending program trading for five minutes shortly after the market open.

- Mention ID：`mention:81b2e2414d57ff7f5b12386a5a935f9ab4ada48cbe1883c51e76a76056815e6a`
- 来源：[KOSPI Spikes 5% on Opening, Riding Micron’s Surprise Earnings](https://beincrypto.com/micron-earnings-kospi-sidecar-chip-rally/)；`doxatlas:raw_media:3f25dec4-7f5f-4203-9e0f-d37c2b06a2c7`
- Family / Assertion：`REGULATORY_LEGAL_POLICY` / `ACTUAL`
- 参与者：The Korea Exchange (KRX) (ACTOR)
- 数量：five minutes [suspension_duration]
- 时间：2026-06-25T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：The Korea Exchange (KRX) activated a buy-side sidecar shortly after the open, suspending program trading for five minutes.

### P39. Apple's stock price fell by over 5%, resulting in a loss of nearly $200 billion in market value.

- Package ID：`package:92945fdbfa2677d4d77992e2`
- Family / Kind：`COMPANY_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 3 Mention
- Anchor entities：COMPANY_AAPL
- Anchor artifact / period：— / —
- 摘要：Apple's stock price fell by over 5%, resulting in a loss of nearly $200 billion in market value.

#### P39-A01. Apple's stock price fell by over 5%, resulting in a loss of nearly $200 billion in market value.

- Atomic ID：`atomic:fce9f9726004c558d0fda987`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- Mention 数：3；Version：3
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY

##### M01. Apple's stock price fell by over 5%, resulting in a loss of nearly $200 billion in market value.

- Mention ID：`mention:a85e4c133c5814b074c266b80a29621e4580e96136095a44c8763614986b4f0f`
- 来源：[Why Micron's blowout earnings are a headache for Apple](https://finance.yahoo.com/markets/article/why-microns-blowout-earnings-are-a-headache-for-apple-161932441.html)；`doxatlas:raw_media:6ccb4979-525c-4467-a381-538f38a4fa9c`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Apple (SUBJECT)
- 数量：down over 5% [price_change_percent]；nearly $200 billion [market_value_change]
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Apple (AAPL), down over 5% after raising prices on some Macs and iPads, is showing the other side of that squeeze.
  - E02 `VERIFIED` / `text:0`：Apple has erased nearly $200 billion.

##### M02. Apple stock fell 0.56% intraday.

- Mention ID：`mention:ce250a37d77ec8452a668abe9801af1321e2883953c604b1bf33af3994877df3`
- 来源：[Tim Cook Calls the Memory Crisis a "Hundred-Year Flood"](https://finance.yahoo.com/technology/articles/tim-cook-calls-memory-crisis-170140774.html)；`doxatlas:raw_media:d42584c3-2d2d-4559-affc-ccae5b388ce1`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Apple (SUBJECT)
- 数量：fell 0.56% [intraday_price_change_percent]
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Apple (NASDAQ:AAPL) fell 0.56% intraday

##### M03. Apple stock closed at $275.15, representing a 6.12% decline.

- Mention ID：`mention:40a1eebf10003ff4b36269c3765fb2a9d858d8c7e4d99a8d2651843933e3e0ee`
- 来源：[Stock Market Today, June 25: Apple Drops After Raising Device Prices to Offset Higher Memory Costs](https://www.fool.com/coverage/stock-market-today/2026/06/25/stock-market-today-june-25-apple-drops-after-raising-device-prices-to-offset-higher-memory-costs/)；`doxatlas:raw_media:d9404d58-cd88-4ccc-a9d2-ec4004629dbc`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Apple (SUBJECT)
- 数量：$275.15 [closing_price]；down 6.12% [price_change_percent]
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：The stock closed at $275.15, down 6.12%.

### P40. South Korea's Kospi index decreased by 10%, triggering a circuit breaker.

- Package ID：`package:94ecfcedb51cd1e3530dd400`
- Family / Kind：`COMPANY_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：field:e1237b3941bfac8e9a75a1ba
- Anchor artifact / period：— / —
- 摘要：South Korea's Kospi index decreased by 10%, triggering a circuit breaker.

#### P40-A01. South Korea's Kospi index decreased by 10%, triggering a circuit breaker.

- Atomic ID：`atomic:0ebfee8ada73a5e4c8376398`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：2026-06-23T00:00:00 / 2026-06-23T00:00:00 / DAY

##### M01. South Korea's Kospi index decreased by 10%, triggering a circuit breaker.

- Mention ID：`mention:63392034e001c9aa23832a86a1af11201887c621056d214484233b3fd820f3c9`
- 来源：[Investors bet on AI again after Micron reports 346% sales jump](https://edition.cnn.com/2026/06/25/business/micron-results-ai-stocks-volatility)；`doxatlas:raw_media:1aaec309-8ccb-42d6-b974-b4aa3bf2d43c`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Kospi (SUBJECT)
- 数量：tumbled 10% [index_change_pct]
- 时间：2026-06-23T00:00:00 / 2026-06-23T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：The latter tumbled 10% Tuesday, tripping a circuit breaker that prompted a 20-minute cooling off period

### P41. Triller Group Inc stock price increased by 259%

- Package ID：`package:95fc89d84440c0a4f135e363`
- Family / Kind：`COMPANY_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：COMPANY_ILLR
- Anchor artifact / period：— / —
- 摘要：Triller Group Inc stock price increased by 259%

#### P41-A01. Triller Group Inc stock price increased by 259%

- Atomic ID：`atomic:b90b31de35045f01c554815c`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：UNKNOWN

##### M01. Triller Group Inc stock price increased by 259%

- Mention ID：`mention:41f9f6cf77f5cb31e104e678ccd488e4701b632a1ea1c2fab9888d916e0895a7`
- 来源：[Dow Surges 250 Points; Micron Posts Upbeat Q3 Earnings](https://finnhub.io/api/news?id=7316a88ccda79b3354a49965fa82f15ed81fe3722e51491463cf118f097a0143)；`doxatlas:raw_media:fce73732-1c68-4d0f-9d7f-c5800393ffff`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Triller Group Inc (SUBJECT)
- 数量：259% [percentage_change]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Triller Group Inc up 259%

### P42. Foreign investors net sold approximately 600 billion won worth of stocks.

- Package ID：`package:99a4a4c51ed5f8d8e29dbec5`
- Family / Kind：`TRANSACTION` / `EPISODE`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：2
- 层级规模：2 Atomic / 2 Mention
- Anchor entities：field:5b89f3be96287758431d66c5
- Anchor artifact / period：— / —
- 摘要：Foreign investors net sold approximately 600 billion won worth of stocks. Foreign investors' cumulative net sales totaled approximately 12.2 trillion won over the five trading days preceding June 25, 2026.

#### P42-A01. Foreign investors net sold approximately 600 billion won worth of stocks.

- Atomic ID：`atomic:338151bd50627164ae1f4c7d`
- Family / Assertion：`TRANSACTION_CAPITAL` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：2026-06-25T00:00:00 / DAY

##### M01. Foreign investors net sold approximately 600 billion won worth of stocks.

- Mention ID：`mention:19c4b0f00813b6c251ca9a77126dcf90d4eb4cfc5b721c1da56c8ee1415a1d38`
- 来源：[KOSPI Spikes 5% on Opening, Riding Micron’s Surprise Earnings](https://beincrypto.com/micron-earnings-kospi-sidecar-chip-rally/)；`doxatlas:raw_media:3f25dec4-7f5f-4203-9e0f-d37c2b06a2c7`
- Family / Assertion：`TRANSACTION_CAPITAL` / `ACTUAL`
- 参与者：Foreign investors (ACTOR)
- 数量：approximately 600 billion won [net_sell_value]
- 时间：2026-06-25T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Foreign investors net sold approximately 600 billion won

#### P42-A02. Foreign investors' cumulative net sales totaled approximately 12.2 trillion won over the five trading days preceding June 25, 2026.

- Atomic ID：`atomic:7d8a0370963f72af993834f1`
- Family / Assertion：`TRANSACTION_CAPITAL` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：2026-06-18T00:00:00 / 2026-06-24T00:00:00 / INTERVAL

##### M01. Foreign investors' cumulative net sales totaled approximately 12.2 trillion won over the five trading days preceding June 25, 2026.

- Mention ID：`mention:f48df9b0b3d216f4829ff7e78889830cbb1222eecdd249a45dc8743ea76239e1`
- 来源：[KOSPI Spikes 5% on Opening, Riding Micron’s Surprise Earnings](https://beincrypto.com/micron-earnings-kospi-sidecar-chip-rally/)；`doxatlas:raw_media:3f25dec4-7f5f-4203-9e0f-d37c2b06a2c7`
- Family / Assertion：`TRANSACTION_CAPITAL` / `ACTUAL`
- 参与者：Foreign investors (ACTOR)
- 数量：around 12.2 trillion won [cumulative_net_sell_value]
- 时间：2026-06-18T00:00:00 / 2026-06-24T00:00:00 / INTERVAL
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：extending a streak of net selling that has now totaled around 12.2 trillion won over the past five trading days.

### P43. SK Hynix stock price increased by more than 10% in early morning trading on June 25, 2026.

- Package ID：`package:a11cec2773d68ebcbf4a44d9`
- Family / Kind：`COMPANY_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`FROZEN`；Version：4
- 层级规模：3 Atomic / 4 Mention
- Anchor entities：COMPANY_SKHY
- Anchor artifact / period：— / —
- 摘要：SK Hynix stock price increased by more than 10% in early morning trading on June 25, 2026. SK Hynix stock price reached the 2.8 million won level. SK Hynix triggered a static volatility interruption at the market open on June 25, 2026, resulting in a two-minute switch to single-price trading.

#### P43-A01. SK Hynix stock price increased by more than 10% in early morning trading on June 25, 2026.

- Atomic ID：`atomic:3a474240ed8d198e3ebfd0af`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- Mention 数：2；Version：2
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY

##### M01. SK Hynix stock price increased by more than 10% in early morning trading on June 25, 2026.

- Mention ID：`mention:9ca98f357891c1dc59ef4dfe74f5b42430ade73000d35972ddba13631b783cbc`
- 来源：[KOSPI Spikes 5% on Opening, Riding Micron’s Surprise Earnings](https://beincrypto.com/micron-earnings-kospi-sidecar-chip-rally/)；`doxatlas:raw_media:3f25dec4-7f5f-4203-9e0f-d37c2b06a2c7`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：SK Hynix (SUBJECT)
- 数量：more than 10% [stock_change_percent]
- 时间：2026-06-25T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：SK Hynix jumped more than 10% in early morning trading

##### M02. SK Hynix stock increased by 13%.

- Mention ID：`mention:9b4e9523dc0555efea7deb027daee2a01e2953cf3719e16a26457dd0f37ee2b3`
- 来源：[Investors bet on AI again after Micron reports 346% sales jump](https://edition.cnn.com/2026/06/25/business/micron-results-ai-stocks-volatility)；`doxatlas:raw_media:1aaec309-8ccb-42d6-b974-b4aa3bf2d43c`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：SK Hynix (SUBJECT)
- 数量：shot up 13% [stock_price_change_pct]
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：SK Hynix’s stock shot up 13% on Thursday

#### P43-A02. SK Hynix stock price reached the 2.8 million won level.

- Atomic ID：`atomic:9d8b706fe9c776932633a7b2`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：2026-06-25T00:00:00 / DAY

##### M01. SK Hynix stock price reached the 2.8 million won level.

- Mention ID：`mention:010463d22e1951105fc8a49021c2e2ee4b2ead8053c0e3dfd634fac59b6a3770`
- 来源：[KOSPI Spikes 5% on Opening, Riding Micron’s Surprise Earnings](https://beincrypto.com/micron-earnings-kospi-sidecar-chip-rally/)；`doxatlas:raw_media:3f25dec4-7f5f-4203-9e0f-d37c2b06a2c7`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：SK Hynix (SUBJECT)
- 数量：2.8 million won [stock_price]
- 时间：2026-06-25T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Samsung Electronics and SK Hynix reclaimed the 360,000 won and 2.8 million won levels, respectively.

#### P43-A03. SK Hynix triggered a static volatility interruption at the market open on June 25, 2026, resulting in a two-minute switch to single-price trading.

- Atomic ID：`atomic:b742c471cb439b788e74f534`
- Family / Assertion：`REGULATORY_LEGAL_POLICY` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：2026-06-25T00:00:00 / DAY

##### M01. SK Hynix triggered a static volatility interruption at the market open on June 25, 2026, resulting in a two-minute switch to single-price trading.

- Mention ID：`mention:6ce9880f1af4a0ee14a130746fbbb330f59a91d8070889f3b1211b14c4b1a80f`
- 来源：[KOSPI Spikes 5% on Opening, Riding Micron’s Surprise Earnings](https://beincrypto.com/micron-earnings-kospi-sidecar-chip-rally/)；`doxatlas:raw_media:3f25dec4-7f5f-4203-9e0f-d37c2b06a2c7`
- Family / Assertion：`REGULATORY_LEGAL_POLICY` / `ACTUAL`
- 参与者：SK Hynix (SUBJECT)
- 数量：two minutes [interruption_duration]
- 时间：2026-06-25T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：triggered a static volatility interruption (VI) at the open, briefly switching to single-price trading for two minutes.

### P44. The artificial intelligence sector remains robust and active.

- Package ID：`package:a1ea544f09c5df053719e79b`
- Family / Kind：`COMPANY_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：PERSON_SANJAY_MEHROTRA
- Anchor artifact / period：— / —
- 摘要：The artificial intelligence sector remains robust and active.

#### P44-A01. The artificial intelligence sector remains robust and active.

- Atomic ID：`atomic:69548ea1fc3743dfe0265ea2`
- Family / Assertion：`OTHER` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：UNKNOWN

##### M01. The artificial intelligence sector remains robust and active.

- Mention ID：`mention:6d1bc470363441a0173093ff4e32071ec4e7814b9a0aa81e5dc8ecf363715c5e`
- 来源：[Why Sandisk Stock Soared Today](https://www.fool.com/investing/2026/06/25/why-sandisk-stock-soared-today/)；`doxatlas:raw_media:35a4333e-6608-482b-b963-ba2f64392063`
- Family / Assertion：`OTHER` / `ACTUAL`
- 参与者：Sanjay Mehrotra (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron CEO Sanjay Mehrotra confirmed that the artificial intelligence revolution is alive and well.

### P45. Apple raised prices on multiple product lines including MacBook Neo, MacBook Air, MacBook Pro, iPad Pro, iPad Air, HomePod, HomePod mini, and Apple TV due to tightening supplies of memory and storage components.

- Package ID：`package:ab32497c0bf1cb5c2c7e135e`
- Family / Kind：`COMPANY_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：2
- 层级规模：1 Atomic / 4 Mention
- Anchor entities：COMPANY_AAPL
- Anchor artifact / period：— / —
- 摘要：Apple raised prices on multiple product lines including MacBook Neo, MacBook Air, MacBook Pro, iPad Pro, iPad Air, HomePod, HomePod mini, and Apple TV due to tightening supplies of memory and storage components.

#### P45-A01. Apple raised prices on multiple product lines including MacBook Neo, MacBook Air, MacBook Pro, iPad Pro, iPad Air, HomePod, HomePod mini, and Apple TV due to tightening supplies of memory and storage components.

- Atomic ID：`atomic:f9a3160ae2f9eb85e4928158`
- Family / Assertion：`COMMERCIAL_OPERATION` / `ACTUAL`
- Mention 数：4；Version：4
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY

##### M01. Apple raised prices on multiple product lines including MacBook Neo, MacBook Air, MacBook Pro, iPad Pro, iPad Air, HomePod, HomePod mini, and Apple TV due to tightening supplies of memory and storage components.

- Mention ID：`mention:15643574ceb2c228dded3057de87a8a3e449f9145f507ba91004e86185ffafed`
- 来源：[Apple Just Confirmed Micron's Biggest AI Prediction](https://www.benzinga.com/trading-ideas/long-ideas/26/06/60108831/apple-just-confirmed-microns-biggest-ai-prediction)；`doxatlas:raw_media:35c3d868-abe6-4617-8c1f-c1460a8e8179`
- Family / Assertion：`COMMERCIAL_OPERATION` / `ACTUAL`
- 参与者：Apple (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：The price increases affect products including the MacBook Neo, MacBook Air, MacBook Pro, iPad Pro, iPad Air, HomePod, HomePod mini and Apple TV, while iPhone pricing remains unchanged.
  - E02 `VERIFIED` / `text:0`：Apple attributed the increases to tightening supplies of memory and storage components as AI infrastructure spending accelerates.

##### M02. Apple left iPhone prices unchanged.

- Mention ID：`mention:913f7cbb61a63b71fdba4baa8128efc1b2eb6e38141fcb6e2fc2dfd612ec0235`
- 来源：[Why Micron's blowout earnings are a headache for Apple](https://finance.yahoo.com/markets/article/why-microns-blowout-earnings-are-a-headache-for-apple-161932441.html)；`doxatlas:raw_media:6ccb4979-525c-4467-a381-538f38a4fa9c`
- Family / Assertion：`COMMERCIAL_OPERATION` / `ACTUAL`
- 参与者：Apple (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：iPhone prices were left unchanged for now.

##### M03. Apple stated it has never seen a component price increase of this magnitude and speed, and indicated potential for further increases.

- Mention ID：`mention:a62b02444c0cbfcf4feefdbcae6c4addbf6b291a04401f7b74d378f061106f8d`
- 来源：[Tim Cook Calls the Memory Crisis a "Hundred-Year Flood"](https://finance.yahoo.com/technology/articles/tim-cook-calls-memory-crisis-170140774.html)；`doxatlas:raw_media:d42584c3-2d2d-4559-affc-ccae5b388ce1`
- Family / Assertion：`COMMERCIAL_OPERATION` / `ACTUAL`
- 参与者：Apple (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Apple said it has "never seen a component price increase this much, this quickly" and left the door open to further increases.

##### M04. Apple raised prices across Macs, iPads, home devices, and Vision Pro to offset higher memory and storage costs.

- Mention ID：`mention:ff5a11f273bd8a7ec35226d5e5508f17b0850442b796ce4a29220e9a53ec24e6`
- 来源：[Stock Market Today, June 25: Apple Drops After Raising Device Prices to Offset Higher Memory Costs](https://www.fool.com/coverage/stock-market-today/2026/06/25/stock-market-today-june-25-apple-drops-after-raising-device-prices-to-offset-higher-memory-costs/)；`doxatlas:raw_media:d9404d58-cd88-4ccc-a9d2-ec4004629dbc`
- Family / Assertion：`COMMERCIAL_OPERATION` / `ACTUAL`
- 参与者：Apple (ACTOR)
- 数量：—
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Apple fell after it raised prices across Macs, iPads, home devices, and Vision Pro to offset higher memory and storage costs.

### P46. Micron Technology announced a strategic partnership with Anthropic to scale next-generation AI infrastructure.

- Package ID：`package:ab868458cec896df28277775`
- Family / Kind：`COMPANY_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：COMPANY_MU, field:9e06a513adb1bb8c56cd8bcd
- Anchor artifact / period：— / —
- 摘要：Micron Technology announced a strategic partnership with Anthropic to scale next-generation AI infrastructure.

#### P46-A01. Micron Technology announced a strategic partnership with Anthropic to scale next-generation AI infrastructure.

- Atomic ID：`atomic:ed45affef07706ffac2ddc47`
- Family / Assertion：`COMMERCIAL_OPERATION` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：2026-06-22T00:00:00 / DAY

##### M01. Micron Technology announced a strategic partnership with Anthropic to scale next-generation AI infrastructure.

- Mention ID：`mention:f2831f2d050e2bd5e19a7fe2d972eae902652c90683d76f968bd3332b8c842ba`
- 来源：[Micron Technology (MU) Announces Collaboration with Anthropic to Scale Next Generation AI Infrastructure](https://finance.yahoo.com/technology/ai/articles/micron-technology-mu-announces-collaboration-062406354.html)；`doxatlas:raw_media:d07de614-f3e3-431a-89dd-33bd139acd24`
- Family / Assertion：`COMMERCIAL_OPERATION` / `ACTUAL`
- 参与者：Micron Technology Inc. (NASDAQ:MU) (ACTOR)；Anthropic (COUNTERPARTY)
- 数量：—
- 时间：2026-06-22T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：On June 22, Micron announced a strategic partnership with Anthropic to scale next-generation AI infrastructure.

### P47. Defiance launches the 2X DRAM ETF (DRAL).

- Package ID：`package:ac2de482ee4559df63ecbeef`
- Family / Kind：`PRODUCT_SCIENCE` / `EPISODE`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 2 Mention
- Anchor entities：INSTITUTION_ROUNDHILL_INVESTMENTS, field:62ade39b65a3c8da077ca542, field:c4e5499266a61f3ebfde835a, field:f0909aa117fa753733c21322
- Anchor artifact / period：— / —
- 摘要：Defiance launches the 2X DRAM ETF (DRAL).

#### P47-A01. Defiance launches the 2X DRAM ETF (DRAL).

- Atomic ID：`atomic:f745f7d5ec182f2928efe085`
- Family / Assertion：`PRODUCT_SCIENCE` / `ACTUAL`
- Mention 数：2；Version：2
- 时间：UNKNOWN

##### M01. Defiance launches the 2X DRAM ETF (DRAL).

- Mention ID：`mention:444a2c1b5d4fdccdb8c8ac542b78df70f899f9b966cca74201e5cfb9d3da60ba`
- 来源：[The AI Memory Trade Is Heating Up: Defiance Launches 2X DRAM ETF After Micron's Blowout Quarter](https://www.benzinga.com/etfs/new-etfs/26/06/60103609/the-ai-memory-trade-is-heating-up-defiance-launches-2x-dram-etf-after-microns-blowout-quarter)；`doxatlas:raw_media:238a879b-e686-457d-b83d-5b0405bd28c6`
- Family / Assertion：`PRODUCT_SCIENCE` / `ACTUAL`
- 参与者：Defiance (ACTOR)；2X DRAM ETF (DRAL) (TARGET)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `title:0`：Defiance Launches 2X DRAM ETF
  - E02 `VERIFIED` / `text:0`：Defiance’s latest launch gives traders another leveraged tool to capitalize on momentum in the semiconductor memory space.

##### M02. Roundhill Investments launched a leveraged fund designed to deliver twice the daily performance of the Roundhill Memory ETF (DRAM).

- Mention ID：`mention:c3e00a750a164002106bca001ac165bbc8bb983a373cc8e99a6c42fa51141f7c`
- 来源：[The AI Memory Trade Is Heating Up: Defiance Launches 2X DRAM ETF After Micron's Blowout Quarter](https://www.benzinga.com/etfs/new-etfs/26/06/60103609/the-ai-memory-trade-is-heating-up-defiance-launches-2x-dram-etf-after-microns-blowout-quarter)；`doxatlas:raw_media:238a879b-e686-457d-b83d-5b0405bd28c6`
- Family / Assertion：`PRODUCT_SCIENCE` / `ACTUAL`
- 参与者：Roundhill Investments (ACTOR)；leveraged fund (TARGET)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Roundhill Investments’ launch of a leveraged fund designed to deliver twice the daily performance of the Roundhill Memory ETF (NASDAQ:DRAM).

### P48. Stoxx 600 index increased by 0.6%.

- Package ID：`package:adb3f204d66cbaadd13aa9be`
- Family / Kind：`COMPANY_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：field:3b65dc22c526c32e5f95e18d
- Anchor artifact / period：— / —
- 摘要：Stoxx 600 index increased by 0.6%.

#### P48-A01. Stoxx 600 index increased by 0.6%.

- Atomic ID：`atomic:f6440223a8754256c2a3a0ed`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY

##### M01. Stoxx 600 index increased by 0.6%.

- Mention ID：`mention:da10fe9941768ac18184a9d57c7b2dbb995cea34b1c329f895ffde7062ae68fe`
- 来源：[Investors bet on AI again after Micron reports 346% sales jump](https://edition.cnn.com/2026/06/25/business/micron-results-ai-stocks-volatility)；`doxatlas:raw_media:1aaec309-8ccb-42d6-b974-b4aa3bf2d43c`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Stoxx 600 (SUBJECT)
- 数量：0.6% [index_change_pct]
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Europe’s benchmark Stoxx 600 index was up 0.6% by early afternoon local time

### P49. Dow Jones Industrial Average indicated an increase of 0.3% in pre-market trade.

- Package ID：`package:baaf0160390d36c4b9471299`
- Family / Kind：`COMPANY_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：2
- 层级规模：2 Atomic / 2 Mention
- Anchor entities：INSTRUMENT_DOW_JONES
- Anchor artifact / period：— / —
- 摘要：Dow Jones Industrial Average indicated an increase of 0.3% in pre-market trade. Dow Jones index increased by 0.5% to reach 52,107.28

#### P49-A01. Dow Jones Industrial Average indicated an increase of 0.3% in pre-market trade.

- Atomic ID：`atomic:e57559d10d969938ac2d0958`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY

##### M01. Dow Jones Industrial Average indicated an increase of 0.3% in pre-market trade.

- Mention ID：`mention:a088dd02e6ca2a0e279d1def0da57d842f79d0e16b45786fd202440c91922c78`
- 来源：[Investors bet on AI again after Micron reports 346% sales jump](https://edition.cnn.com/2026/06/25/business/micron-results-ai-stocks-volatility)；`doxatlas:raw_media:1aaec309-8ccb-42d6-b974-b4aa3bf2d43c`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Dow (SUBJECT)
- 数量：0.3% [index_change_pct]
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：the Dow was pointing up by 0.3%

#### P49-A02. Dow Jones index increased by 0.5% to reach 52,107.28

- Atomic ID：`atomic:e6d28c27fd6525daa8739052`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：UNKNOWN

##### M01. Dow Jones index increased by 0.5% to reach 52,107.28

- Mention ID：`mention:a0aca6c3ad5794ffb2a1b7031edcfcb2bed05b8a99dcd5d06032f5912b582b38`
- 来源：[Dow Surges 250 Points; Micron Posts Upbeat Q3 Earnings](https://finnhub.io/api/news?id=7316a88ccda79b3354a49965fa82f15ed81fe3722e51491463cf118f097a0143)；`doxatlas:raw_media:fce73732-1c68-4d0f-9d7f-c5800393ffff`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Dow Jones index (SUBJECT)
- 数量：52,107.28 [index_level]；0.5% [percentage_change]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：The Dow Jones index rose 0.5% to 52,107.28

### P50. Micron Technology stock decreased by 13%.

- Package ID：`package:bcc15f9e41da39c224d3b20b`
- Family / Kind：`COMPANY_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：COMPANY_MU
- Anchor artifact / period：— / —
- 摘要：Micron Technology stock decreased by 13%.

#### P50-A01. Micron Technology stock decreased by 13%.

- Atomic ID：`atomic:7e687e14958b95b177fdb0a4`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：2026-06-23T00:00:00 / 2026-06-23T00:00:00 / DAY

##### M01. Micron Technology stock decreased by 13%.

- Mention ID：`mention:6df2ddd41c6b75f1525474d6b3d3d746cc15f0a24744bcccf9cb6f915565f77c`
- 来源：[Investors bet on AI again after Micron reports 346% sales jump](https://edition.cnn.com/2026/06/25/business/micron-results-ai-stocks-volatility)；`doxatlas:raw_media:1aaec309-8ccb-42d6-b974-b4aa3bf2d43c`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Micron Technology (SUBJECT)
- 数量：fell 13% [stock_price_change_pct]
- 时间：2026-06-23T00:00:00 / 2026-06-23T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron’s stock fell 13% on Tuesday

### P51. Wedbush analyst Dan Ives expressed confidence in AI demand and recommended owning core tech winners into year-end.

- Package ID：`package:c5d532a3204e6fd3a007bd04`
- Family / Kind：`ANALYST_REPORT` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：field:adfc043125dadab998cd6d80, field:eafa2e7cf25ac9ab2e61ed89
- Anchor artifact / period：— / —
- 摘要：Wedbush analyst Dan Ives expressed confidence in AI demand and recommended owning core tech winners into year-end.

#### P51-A01. Wedbush analyst Dan Ives expressed confidence in AI demand and recommended owning core tech winners into year-end.

- Atomic ID：`atomic:efdfbf6bb62c0082317e43c9`
- Family / Assertion：`ANALYST_ACTION` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：UNKNOWN

##### M01. Wedbush analyst Dan Ives expressed confidence in AI demand and recommended owning core tech winners into year-end.

- Mention ID：`mention:03db75d0303881ece5e41611f276385de97269ea2af5862626bc98ef2b81a8ec`
- 来源：[Why Everyone Is Talking About Micron](https://www.fool.com/investing/2026/06/25/why-everyone-is-talking-about-micron/)；`doxatlas:raw_media:49fb27c5-84b4-4e56-8791-f7ac23de38b7`
- Family / Assertion：`ANALYST_ACTION` / `ACTUAL`
- 参与者：Dan Ives (ACTOR)；Wedbush (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`："We are seeing no cracks in AI demand on the chips/hardware or software front which gives us a bright green light to own the core tech winners into year-end," Wedbush analyst Dan Ives said in a recent research note.

### P52. Technology sector shares increased by 1.6%

- Package ID：`package:c7bb41f739302578d209fe8c`
- Family / Kind：`COMPANY_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：INSTRUMENT_TECH
- Anchor artifact / period：— / —
- 摘要：Technology sector shares increased by 1.6%

#### P52-A01. Technology sector shares increased by 1.6%

- Atomic ID：`atomic:516ee542e2bc9c5b6b587e35`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：UNKNOWN

##### M01. Technology sector shares increased by 1.6%

- Mention ID：`mention:a867ae73a685259946b7f74531ba9ea3a99b258d9c522db153d2b18f2054d54e`
- 来源：[Dow Surges 250 Points; Micron Posts Upbeat Q3 Earnings](https://finnhub.io/api/news?id=7316a88ccda79b3354a49965fa82f15ed81fe3722e51491463cf118f097a0143)；`doxatlas:raw_media:fce73732-1c68-4d0f-9d7f-c5800393ffff`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Tech shares (SUBJECT)
- 数量：1.6% [percentage_change]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Tech shares jump 1.6%

### P53. Communication services sector stocks decreased by 1.9%

- Package ID：`package:cb0ca3c85815e48c31ba591e`
- Family / Kind：`COMPANY_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：field:e42453ca47da2aafd0172a09
- Anchor artifact / period：— / —
- 摘要：Communication services sector stocks decreased by 1.9%

#### P53-A01. Communication services sector stocks decreased by 1.9%

- Atomic ID：`atomic:8d04de2a823e991a3451b803`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：UNKNOWN

##### M01. Communication services sector stocks decreased by 1.9%

- Mention ID：`mention:3cd87026cd0c41be83f71879434f80d2a204619adcc556768bffcea203e989f1`
- 来源：[Dow Surges 250 Points; Micron Posts Upbeat Q3 Earnings](https://finnhub.io/api/news?id=7316a88ccda79b3354a49965fa82f15ed81fe3722e51491463cf118f097a0143)；`doxatlas:raw_media:fce73732-1c68-4d0f-9d7f-c5800393ffff`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：communication services stocks (SUBJECT)
- 数量：1.9% [percentage_change]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：communication services stocks fell 1.9%

### P54. Suppliers are reallocating cleanroom space from NAND flash to DRAM production, constraining NAND supply growth.

- Package ID：`package:cd5b1adeef139b161fb8a9cd`
- Family / Kind：`COMPANY_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：field:58265a7ad0c0cc06a286fba2
- Anchor artifact / period：— / —
- 摘要：Suppliers are reallocating cleanroom space from NAND flash to DRAM production, constraining NAND supply growth.

#### P54-A01. Suppliers are reallocating cleanroom space from NAND flash to DRAM production, constraining NAND supply growth.

- Atomic ID：`atomic:d19b3eb53dda55d363cf3d7b`
- Family / Assertion：`PRODUCTION_SUPPLY` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：UNKNOWN

##### M01. Suppliers are reallocating cleanroom space from NAND flash to DRAM production, constraining NAND supply growth.

- Mention ID：`mention:94004c00b8469e312eac83f9a34c782df4ddffc4d1d1b04063abb19666a81361`
- 来源：[Micron CEO Expects Memory Supply To Improve Gradually In 2028, But There's No 'Line Of Sight' When It Will Catch Up To Rising AI Demand](https://www.benzinga.com/markets/tech/26/06/60089761/micron-ceo-sees-memory-supply-improving-by-2028-but-no-clear-end-in-sight-to-ai-demand-shortfallmicron-ceo-sees-memory-supply-improving-by-2028-but-no-clear-end-in-sight-to-ai-demand-shortfall)；`doxatlas:raw_media:25387f96-b423-437c-b2cd-8652aa176bd8`
- Family / Assertion：`PRODUCTION_SUPPLY` / `ACTUAL`
- 参与者：some suppliers (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：In NAND flash memory, Mehrotra said some suppliers are reallocating cleanroom space toward DRAM production, further constraining NAND supply growth.

### P55. Micron declared a quarterly dividend of 15 cents per share, payable on July 21, 2026, to shareholders of record on July 6, 2026.

- Package ID：`package:d1fe96f1a9b620f38bd88d22`
- Family / Kind：`TRANSACTION` / `EPISODE`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：COMPANY_MU
- Anchor artifact / period：— / —
- 摘要：Micron declared a quarterly dividend of 15 cents per share, payable on July 21, 2026, to shareholders of record on July 6, 2026.

#### P55-A01. Micron declared a quarterly dividend of 15 cents per share, payable on July 21, 2026, to shareholders of record on July 6, 2026.

- Atomic ID：`atomic:7e3e9b48a9d16049cdf932fd`
- Family / Assertion：`TRANSACTION_CAPITAL` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：UNKNOWN

##### M01. Micron declared a quarterly dividend of 15 cents per share, payable on July 21, 2026, to shareholders of record on July 6, 2026.

- Mention ID：`mention:af2dfc0611aa4116ed17f998b5841dd1d10d39218e8a2452633bef928bc538c4`
- 来源：[Micron CEO Expects Memory Supply To Improve Gradually In 2028, But There's No 'Line Of Sight' When It Will Catch Up To Rising AI Demand](https://www.benzinga.com/markets/tech/26/06/60089761/micron-ceo-sees-memory-supply-improving-by-2028-but-no-clear-end-in-sight-to-ai-demand-shortfallmicron-ceo-sees-memory-supply-improving-by-2028-but-no-clear-end-in-sight-to-ai-demand-shortfall)；`doxatlas:raw_media:25387f96-b423-437c-b2cd-8652aa176bd8`
- Family / Assertion：`TRANSACTION_CAPITAL` / `ACTUAL`
- 参与者：Micron (ACTOR)
- 数量：15 cents per share [dividend_per_share]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Micron also declared a quarterly dividend of 15 cents per share, payable July 21 to shareholders of record on July 6.

### P56. Benzinga’s Edge Stock Rankings indicate that MU has a positive price trend across all time frames.

- Package ID：`package:d2ef0b61addeb8e2e759541e`
- Family / Kind：`ANALYST_REPORT` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：4
- 层级规模：3 Atomic / 4 Mention
- Anchor entities：COMPANY_MU, INSTITUTION_BANK_OF_AMERICA, INSTITUTION_NEEDHAM, field:321e4e1566b5bfbc9978decb, field:8e7d15f3d23505c813136c39
- Anchor artifact / period：— / —
- 摘要：Benzinga’s Edge Stock Rankings indicate that MU has a positive price trend across all time frames. Bank of America stated that $32 billion in repurchases for fiscal 2027 would represent only about 25% of potential free cash flow generation. Needham increased its price target on Micron to $1,550 from $500 while maintaining a Buy rating.

#### P56-A01. Benzinga’s Edge Stock Rankings indicate that MU has a positive price trend across all time frames.

- Atomic ID：`atomic:3a0234454749d29d89567473`
- Family / Assertion：`ANALYST_ACTION` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：UNKNOWN

##### M01. Benzinga’s Edge Stock Rankings indicate that MU has a positive price trend across all time frames.

- Mention ID：`mention:a3d3d6a9468f9264a01ad46b2ba3bdbf8892175ebfcdca422add81e306aba260`
- 来源：[SanDisk, Western Digital, Seagate Surge After Hours As Micron's Blowout Earnings Signal Memory Upcycle](https://www.benzinga.com/markets/equities/26/06/60088616/sandisk-western-digital-and-seagate-surge-after-microns-blowout-earnings-signal-memory-upcycle)；`doxatlas:raw_media:6389d63c-442c-4c9b-9ba5-b943b7e91804`
- Family / Assertion：`ANALYST_ACTION` / `ACTUAL`
- 参与者：MU (SUBJECT)；Benzinga’s Edge Stock Rankings (ACTOR)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：With a strong Momentum in the 99th percentile, Benzinga’s Edge Stock Rankings indicate that MU has a positive price trend across all time frames.

#### P56-A02. Bank of America stated that $32 billion in repurchases for fiscal 2027 would represent only about 25% of potential free cash flow generation.

- Atomic ID：`atomic:f8f27b1fe44f3a2b93507949`
- Family / Assertion：`ANALYST_ACTION` / `HYPOTHETICAL`
- Mention 数：1；Version：1
- 时间：fiscal 2027 / UNKNOWN

##### M01. Bank of America stated that $32 billion in repurchases for fiscal 2027 would represent only about 25% of potential free cash flow generation.

- Mention ID：`mention:5590ef367d4eedec7c6dfbeb169efe1bf1b5963f95de6433242281402547320c`
- 来源：[Micron shares surge to all-time high as Wall Street cheers unprecedented contract visibility](https://www.proactiveinvestors.com/companies/news/1094520/micron-shares-surge-to-all-time-high-as-wall-street-cheers-unprecedented-contract-visibility-1094520.html)；`doxatlas:raw_media:6b5d042a-9236-453b-8b60-2ba6c2f16b4f`
- Family / Assertion：`ANALYST_ACTION` / `HYPOTHETICAL`
- 参与者：Bank of America (ACTOR)；Micron (SUBJECT)
- 数量：$32 billion [potential_buyback_amount]；25% [fcf_percentage]
- 时间：fiscal 2027 / UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：$32 billion in repurchases for fiscal 2027 would represent only about 25% of potential free cash flow generation.

#### P56-A03. Needham increased its price target on Micron to $1,550 from $500 while maintaining a Buy rating.

- Atomic ID：`atomic:fc7f86f9b27ad6171b1c2a84`
- Family / Assertion：`ANALYST_ACTION` / `ACTUAL`
- Mention 数：2；Version：2
- 时间：2026-06-22T00:00:00 / DAY

##### M01. Needham increased its price target on Micron to $1,550 from $500 while maintaining a Buy rating.

- Mention ID：`mention:77849fce71a92343d5664ace6a05f98cba537160bd5aa5f6001c5b5193bc3793`
- 来源：[Micron Technology (MU) Announces Collaboration with Anthropic to Scale Next Generation AI Infrastructure](https://finance.yahoo.com/technology/ai/articles/micron-technology-mu-announces-collaboration-062406354.html)；`doxatlas:raw_media:d07de614-f3e3-431a-89dd-33bd139acd24`
- Family / Assertion：`ANALYST_ACTION` / `ACTUAL`
- 参与者：Needham (ACTOR)；Micron (SUBJECT)
- 数量：$1,550 [price_target]；$500 [price_target]
- 时间：2026-06-22T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Needham increased its price target on Micron to $1,550 from $500 while maintaining a Buy rating on the stock, citing the continued strength of the memory market.

##### M02. Analysts made changes to their price targets on Micron.

- Mention ID：`mention:3b1bd95cb6e9d5f6d06ac4a2dc01c062b52af0ff28ec68e6c761752642f20838`
- 来源：[These Analysts Boost Their Forecasts On Micron Technology After Better-Than-Expected Q3 Results](https://www.benzinga.com/analyst-stock-ratings/price-target/26/06/60100007/these-analysts-boost-their-forecasts-on-micron-technology-after-better-than-expected-q3-results)；`doxatlas:raw_media:5d890b9d-c789-4a99-87d6-8a34a3ea4cc2`
- Family / Assertion：`ANALYST_ACTION` / `ACTUAL`
- 参与者：analysts (ACTOR)；Micron (TARGET)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：These analysts made changes to their price targets on Micron following earnings announcement.

### P57. Western Digital, SanDisk, and Seagate stock prices surged and reversed intraday losses in after-hours trading.

- Package ID：`package:d92e1a50dd428559c149353a`
- Family / Kind：`COMPANY_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：COMPANY_STX, COMPANY_WDC, field:245e55450af87507cb33a684
- Anchor artifact / period：— / —
- 摘要：Western Digital, SanDisk, and Seagate stock prices surged and reversed intraday losses in after-hours trading.

#### P57-A01. Western Digital, SanDisk, and Seagate stock prices surged and reversed intraday losses in after-hours trading.

- Atomic ID：`atomic:806c8aab3b7cb5ef226c9592`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：UNKNOWN

##### M01. Western Digital, SanDisk, and Seagate stock prices surged and reversed intraday losses in after-hours trading.

- Mention ID：`mention:7649a3d77ef3efa6d32eeba5b042dd1e0406a8d9cba018fd5863577a423de7d9`
- 来源：[SanDisk, Western Digital, Seagate Surge After Hours As Micron's Blowout Earnings Signal Memory Upcycle](https://www.benzinga.com/markets/equities/26/06/60088616/sandisk-western-digital-and-seagate-surge-after-microns-blowout-earnings-signal-memory-upcycle)；`doxatlas:raw_media:6389d63c-442c-4c9b-9ba5-b943b7e91804`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Western Digital (SUBJECT)；SanDisk (SUBJECT)；Seagate (SUBJECT)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `title:0`：SanDisk, Western Digital, Seagate Surge After Hours As Micron's Blowout Earnings Signal Memory Upcycle
  - E02 `VERIFIED` / `text:0`：The gains in late trading session reflect a ripple effect, where earnings from a major industry player drive moves in related stocks. Micron’s strong results triggered a sympathy rally in WDC, SNDK and STX, which are exposed to the same demand trends.
  - E03 `VERIFIED` / `text:0`：Sector Stocks Reverse Intraday Losses After the Bell All three technology stocks reversed intraday losses in after-hours trading following Micron’s results.

### P58. Japan's Nikkei 225 index increased by 4.6% at close.

- Package ID：`package:e4b71e9ecdf4a733ebd19ac1`
- Family / Kind：`COMPANY_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：field:c175de912831ffd0b37986f9
- Anchor artifact / period：— / —
- 摘要：Japan's Nikkei 225 index increased by 4.6% at close.

#### P58-A01. Japan's Nikkei 225 index increased by 4.6% at close.

- Atomic ID：`atomic:1226240652a84a5ec514afe0`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY

##### M01. Japan's Nikkei 225 index increased by 4.6% at close.

- Mention ID：`mention:f5e542564aa1e3cbd81e769b283e99e780f21e33044d3286ef24c4fd4714715a`
- 来源：[Investors bet on AI again after Micron reports 346% sales jump](https://edition.cnn.com/2026/06/25/business/micron-results-ai-stocks-volatility)；`doxatlas:raw_media:1aaec309-8ccb-42d6-b974-b4aa3bf2d43c`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Nikkei 225 (SUBJECT)
- 数量：up 4.6% [index_change_pct]
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Japan’s Nikkei 225 index closed up 4.6%

### P59. Growing HBM adoption is putting additional pressure on conventional memory supply due to higher manufacturing resource requirements.

- Package ID：`package:f5a2e9878cb5b674fd4fc9da`
- Family / Kind：`COMPANY_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：field:bbcf955fe7c252c8c118628a
- Anchor artifact / period：— / —
- 摘要：Growing HBM adoption is putting additional pressure on conventional memory supply due to higher manufacturing resource requirements.

#### P59-A01. Growing HBM adoption is putting additional pressure on conventional memory supply due to higher manufacturing resource requirements.

- Atomic ID：`atomic:f5bb350fb0d097ff8e1097b5`
- Family / Assertion：`PRODUCTION_SUPPLY` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：UNKNOWN

##### M01. Growing HBM adoption is putting additional pressure on conventional memory supply due to higher manufacturing resource requirements.

- Mention ID：`mention:a664ce3776052dad4d8384087f73cd4d6fc2e37fc98f8082323e5c95ee29ce9a`
- 来源：[Micron CEO Expects Memory Supply To Improve Gradually In 2028, But There's No 'Line Of Sight' When It Will Catch Up To Rising AI Demand](https://www.benzinga.com/markets/tech/26/06/60089761/micron-ceo-sees-memory-supply-improving-by-2028-but-no-clear-end-in-sight-to-ai-demand-shortfallmicron-ceo-sees-memory-supply-improving-by-2028-but-no-clear-end-in-sight-to-ai-demand-shortfall)；`doxatlas:raw_media:25387f96-b423-437c-b2cd-8652aa176bd8`
- Family / Assertion：`PRODUCTION_SUPPLY` / `ACTUAL`
- 参与者：HBM adoption (ACTOR)；conventional memory supply (AFFECTED)
- 数量：—
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：He added that growing HBM adoption is putting additional pressure on the conventional memory supply because advanced memory products require more manufacturing resources and capacity.

### P60. S&P 500 index increased by 0.6% to reach 7,401.17

- Package ID：`package:f88cf5c1fba0dc76e997e83a`
- Family / Kind：`COMPANY_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：2
- 层级规模：1 Atomic / 2 Mention
- Anchor entities：INSTRUMENT_SP500
- Anchor artifact / period：— / —
- 摘要：S&P 500 index increased by 0.6% to reach 7,401.17

#### P60-A01. S&P 500 index increased by 0.6% to reach 7,401.17

- Atomic ID：`atomic:610f85f5e27427d329fc8103`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- Mention 数：2；Version：2
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY

##### M01. S&P 500 index increased by 0.6% to reach 7,401.17

- Mention ID：`mention:5353f7c229e0dfcf02d37ae720208230d4ca5b0e52518f40aadcba23590bd4af`
- 来源：[Dow Surges 250 Points; Micron Posts Upbeat Q3 Earnings](https://finnhub.io/api/news?id=7316a88ccda79b3354a49965fa82f15ed81fe3722e51491463cf118f097a0143)；`doxatlas:raw_media:fce73732-1c68-4d0f-9d7f-c5800393ffff`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：S&P 500 (SUBJECT)
- 数量：7,401.17 [index_level]；0.6% [percentage_change]
- 时间：UNKNOWN
- Evidence 状态：`TEXT_NOT_FOUND`
  - E01 `TEXT_NOT_FOUND` / `text:0`：S&P 500 gained 0.6% to 7,401.17

##### M02. The S&P 500 index slipped 0.01% to close at 7,357.

- Mention ID：`mention:523e4eabd5fc93d421b3621ff5378be8e7da571fdf7a4628b3cda9c5d2a09f48`
- 来源：[Stock Market Today, June 25: Apple Drops After Raising Device Prices to Offset Higher Memory Costs](https://www.fool.com/coverage/stock-market-today/2026/06/25/stock-market-today-june-25-apple-drops-after-raising-device-prices-to-offset-higher-memory-costs/)；`doxatlas:raw_media:d9404d58-cd88-4ccc-a9d2-ec4004629dbc`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：S&P 500 (SUBJECT)
- 数量：7,357 [index_close_value]；slipped 0.01% [index_change_percent]
- 时间：2026-06-25T00:00:00 / 2026-06-25T00:00:00 / DAY
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：The S&P 500 (^GSPC +0.00%) slipped 0.01% to 7,357

### P61. Qualcomm's stock price increased by 12%.

- Package ID：`package:ffb9fc27e5be0a0a1609546f`
- Family / Kind：`COMPANY_DISCLOSURE` / `BOUNDED`
- 状态：`UNKNOWN`；Quality：`ACTIVE`；Version：1
- 层级规模：1 Atomic / 1 Mention
- Anchor entities：COMPANY_QCOM
- Anchor artifact / period：— / —
- 摘要：Qualcomm's stock price increased by 12%.

#### P61-A01. Qualcomm's stock price increased by 12%.

- Atomic ID：`atomic:cea5fa3c56b4bbbbc209fe25`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- Mention 数：1；Version：1
- 时间：UNKNOWN

##### M01. Qualcomm's stock price increased by 12%.

- Mention ID：`mention:abab98f937c243c17a8f796684d0a50af6dc76f850cb50d942f75726b742c170`
- 来源：[Micron Just Locked In $100 Billion in Sales, and Wall Street Thinks the Boom-Bust Chip Cycle Is Dead](https://247wallst.com/investing/2026/06/25/micron-just-locked-in-100-billion-in-sales-and-wall-street-thinks-the-boom-bust-chip-cycle-is-dead/)；`doxatlas:raw_media:1e515897-64d4-4896-b936-10a04501eff4`
- Family / Assertion：`MARKET_MOVEMENT` / `ACTUAL`
- 参与者：Qualcomm (SUBJECT)
- 数量：12% [stock_price_change_pct]
- 时间：UNKNOWN
- Evidence 状态：`VERIFIED`
  - E01 `VERIFIED` / `text:0`：Qualcomm shares rose 12% on the data center news

## 4. 层级完整性与质量警告

- Package 覆盖：61/61
- Atomic 唯一覆盖：131/131；未归包：0
- Mention 唯一覆盖：199/199；未归 Atomic：0
- 已确认 Atomic FP pair：54；主要为不同 metric、交易时段、产品或业务动作的过合并。
- 已确认 Package FP/FN pair：81/522；主要为 reaction/earnings 边界错误和包碎片化。
- 本文档是运行结果的业务层级展示；人工 Gold、节点指标与根因详见配套验收报告。
