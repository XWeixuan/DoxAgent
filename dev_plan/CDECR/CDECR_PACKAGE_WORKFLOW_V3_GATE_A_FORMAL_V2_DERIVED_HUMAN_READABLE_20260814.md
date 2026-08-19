# CDECR V3 Gate A - formal-V2-derived canonical R2 Final Package Results

> 按“Package → Parent Occurrence → Atomic”整理。canonical、压缩描述和 Parent Occurrence 均为本轮新测试落库值。
> `Erroneously aggregated Atomic(s)` 由独立 Agent 逐包审核填写；`—`表示未发现明确错误或证据不足。

- Registry: `.tmp/cdecr/package_v3_gate_a_formal_v2_canonical_r2_20260814.sqlite3`
- V2.0R formal published baseline: P/R/F1 77.55%/38.78%/51.70%, 73 Packages, 23 singleton.
- Strict same-99 comparison: V2.0R 81.85%/33.46%/47.50% → V3 81.99%/56.10%/66.62%.

## Test summary

| Item | Value |
| --- | ---: |
| Input Atomic | 281 |
| Parent Proposal | 132 |
| Final Package | 44 |
| Singleton Package | 14/44 (31.82%) |
| Largest Package | 74 |
| V3 model calls | 2 |
| V3 input/output tokens | 9,058 / 46,652 |
| V3 total tokens | 55,710 |
| V3 first wall-clock | 409.39 s |
| Idempotency replay | 0 new calls; partition hash stable=True |

## Gold evaluation

| Metric | Value |
| --- | ---: |
| Alignment | 99/281 (35.23%) |
| Pair Precision | 81.99% |
| Pair Recall | 56.10% |
| Pair F1 | 66.62% |
| TP / FP / FN | 446 / 98 / 349 |

独立Agent审核：44个Package中14个明确含误成员，29个可接受，1个边界不确定。14个singleton中3个明确漏合，人工漏合率21.43%。

---

## MCP-000001 — Micron fiscal Q3 2026 earnings report and guidance

Package ID: `package-v3:9f14a5e1f5be37a330281250539d17c8`  
Parent occurrence count: 30; Atomic coverage: 74

压缩后的描述：Micron fiscal Q3 2026 (period ended May 28, 2026) earnings report, release, and guidance: record revenue, record gross margin of 84.9%, EPS of $25.11, operating margin of 80.4%, Q4 guidance for revenue, gross margin (~86%) and EPS, sales forecast beating expectations, record capital investment, quarterly dividend declaration, shareholder capital return program/commitment to return 100% of excess free cash flow, AI growth outlook, and strategic customer agreement disclosures.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D01 | `D01-PO-000103` Micron Q3 FY2026 earnings report<br><sub>`atomic:6f27765c87cf77a991ce09eb` Micron's gross margin surpassed the gross margins of Nvidia and Meta.<br>`atomic:986d4cead855d973bc808d0e` Micron reported gross margins of 84.9% in its most recent quarter.</sub> | DISCLOSED_IN | 2 | `atomic:6f27765c87cf77a991ce09eb` Micron's gross margin surpassed the gross margins of Nvidia and Meta. |
| D02 | `D02-PO-000005` Micron quarterly sales forecast beating expectations<br><sub>`atomic:73282ccbb83b7c2abe8081de` Micron Technology posted a quarterly sales forecast that beat Wall Street expectations.</sub> | DISCLOSED_IN | 1 | — |
| D03 | `D03-PO-000006` Micron fiscal Q3 2026 earnings disclosure<br><sub>`atomic:654d1dbc4140313a4d476ac1` Micron reported fiscal third-quarter revenue of $41.5 billion, up 74% year-over-year.<br>`atomic:c7fbfc69fc19d71fcbcfc3b4` Micron issued strong forward guidance indicating that demand for AI memory products remains robust.<br>`atomic:ff2e39f75af16b5b9553d46f` Micron's data center revenue more than doubled.</sub> | DISCLOSED_IN | 3 | — |
| D05 | `D05-PO-000003` Micron shareholder capital return program<br><sub>`atomic:1bd4bccdf663c1d9fcb4f5a6` Micron intends to return all excess cash to shareholders as free cash flow builds.<br>`atomic:855b3da1b8a9a130d95a1fad` Micron plans to increase capital returns over time beginning Dec. 9, 2026.<br>`atomic:c84cbe94f7653fd2d756eff5` Micron pledged to return all excess cash to investors.</sub> | COMPONENT_OF | 3 | — |
| D06 | `D06-PO-000022` Micron Q3 FY2026 earnings disclosure<br><sub>`atomic:654d1dbc4140313a4d476ac1` Micron reported fiscal third-quarter revenue of $41.5 billion, up 74% year-over-year.<br>`atomic:722383cf20e7afbedec11627` Micron management guided for approximately 20% sequential revenue growth in the current quarter.<br>`atomic:9efcc02b28f85bfc09105739` Micron management committed to returning 100% of excess cash to shareholders moving forward, up from the prior 50%.<br>`atomic:a08711e247427a8371dc6bac` Micron's adjusted gross margins reached 84.9% in fiscal Q3.<br>`atomic:cd07f511063bceae745026e3` Micron secured 16 long-term Strategic Customer Agreements (SCAs) that effectively sold out its 2026 manufacturing capacity.<br>`atomic:fe8e2c3637ae8b810eafcf9e` Micron's multi-year take-or-pay deals lock in approximately $22 billion in cash deposits and commitments.</sub> | DISCLOSED_IN | 6 | — |
| D07 | `D07-PO-000062` Micron fiscal Q3 2026 earnings disclosure<br><sub>`atomic:01be8aaace1cfca446f8ba0e` Micron's fiscal fourth-quarter gross margin is expected to climb to about 86%.<br>`atomic:0579c187003109cbe82df2ac` Micron's earnings per share came in at $25.11.<br>`atomic:2a1fecba66036ef0234b7b6f` Micron expects fourth-quarter revenue of $50 billion, with a range of $49 billion to $51 billion, versus consensus estimates of $42.95 billion.<br>`atomic:654d1dbc4140313a4d476ac1` Micron reported fiscal third-quarter revenue of $41.5 billion, up 74% year-over-year.<br>`atomic:956756737544e40b21dec000` Micron's gross margin was 84.9%.<br>`atomic:adb3630475353d170b65b4a5` Micron expects tight supply conditions to persist beyond its 2027 financial year.<br>`atomic:fef1c17dbaa56428dc1a24b1` Micron's data centre revenue reached an annualised run rate of about $100 billion.</sub> | DISCLOSED_IN | 7 | — |
| D07 | `D07-PO-000098` Micron capital return commitment<br><sub>`atomic:1bd4bccdf663c1d9fcb4f5a6` Micron intends to return all excess cash to shareholders as free cash flow builds.</sub> | COMPONENT_OF | 1 | — |
| D08 | `D08-PO-000040` Micron fiscal Q3 2026 earnings report<br><sub>`atomic:0579c187003109cbe82df2ac` Micron's earnings per share came in at $25.11.<br>`atomic:058673290d7e00b0d3e4f0e3` Micron's multi-year Strategic Customer Agreements will significantly enhance the durability and predictability of Micron's strong financial performance.<br>`atomic:170a1dc16b3a43acc0415181` CEO Sanjay Mehrotra said the results reflect the strategic value of memory in the AI era.<br>`atomic:2a1fecba66036ef0234b7b6f` Micron expects fourth-quarter revenue of $50 billion, with a range of $49 billion to $51 billion, versus consensus estimates of $42.95 billion.<br>`atomic:36b40d2a2f86cf7744135f47` Micron posted net income of $28.24 billion, or $24.67 per share, compared with less than $2 billion a year ago.<br>`atomic:482af580c8fab8a40a2f5861` Micron signalled a further jump in capital spending in 2027.<br>`atomic:4ad347361da920ab684b1605` Micron reported a gross margin of approximately 85% for the quarter.<br>`atomic:5441f336e8af76d9e521c748` Micron expects adjusted earnings of roughly $31 per share in the current quarter.<br>`atomic:654d1dbc4140313a4d476ac1` Micron reported fiscal third-quarter revenue of $41.5 billion, up 74% year-over-year.<br>`atomic:843d73922d49ce9bc6daddef` Micron is lifting planned capital spending to approximately $27 billion for this fiscal year.</sub> | DISCLOSED_IN | 10 | — |
| D09 | `D09-PO-000024` Micron quarterly dividend declaration<br><sub>`atomic:4339c673af5e3d345eeafe93` Micron declared a quarterly dividend of 15 cents per share.</sub> | DISCLOSED_IN | 1 | — |
| D09 | `D09-PO-000068` Micron fiscal Q3 2026 earnings disclosure<br><sub>`atomic:1aa613473954efa0a715b65c` Micron's revenues soared 346% year-over-year.<br>`atomic:2a1fecba66036ef0234b7b6f` Micron expects fourth-quarter revenue of $50 billion, with a range of $49 billion to $51 billion, versus consensus estimates of $42.95 billion.<br>`atomic:654d1dbc4140313a4d476ac1` Micron reported fiscal third-quarter revenue of $41.5 billion, up 74% year-over-year.</sub> | DISCLOSED_IN | 3 | — |
| D10 | `D10-PO-000048` Micron fiscal Q3 2026 earnings disclosure<br><sub>`atomic:0e669ab2f79e2aaf4dd176c8` Micron's third-quarter gross margin was 84.6%, ahead of its own guidance of 81%.<br>`atomic:31153a9c08798711a9512ac3` Micron's free cash flow reached a record level of $18 billion.<br>`atomic:654d1dbc4140313a4d476ac1` Micron reported fiscal third-quarter revenue of $41.5 billion, up 74% year-over-year.<br>`atomic:7f46948f105fbea4f7583507` Micron's revenue and net income for the latest quarter both largely beat analysts' estimates.<br>`atomic:d249f45345f50705b41d1833` Micron's net income for the latest quarter reached $28 billion, representing a quadruple-digit increase.</sub> | DISCLOSED_IN | 5 | — |
| D10 | `D10-PO-000065` Micron AI growth outlook and market positioning<br><sub>`atomic:312bf41a7b4da385f51c1c34` Demand from AI customers for Micron's memory is soaring.<br>`atomic:4024baf7bfa8425fb6db22f8` Micron states that AI expansion into various industries creates key memory opportunities.<br>`atomic:527393d3fa42ee13fc3ac954` Micron may be in the early stages of the AI-driven growth opportunity.<br>`atomic:8e7fb246448012146623f4c7` Micron stated that the AI revolution is in its early stages.<br>`atomic:d2509e8d160f6220d584a289` Demand for memory has steadily surpassed supply even with multiple players including Seagate Technology and SK Hynix.</sub> | COMPONENT_OF | 5 | — |
| D11 | `D11-PO-000090` Micron fiscal Q3 2026 earnings report<br><sub>`atomic:2a1fecba66036ef0234b7b6f` Micron expects fourth-quarter revenue of $50 billion, with a range of $49 billion to $51 billion, versus consensus estimates of $42.95 billion.<br>`atomic:57a874bc89ff90d8f8a1338a` Micron's Cloud Memory segment generated $13.769 billion, and Core Data Center generated another $11.524 billion.<br>`atomic:5d1637cfd62a9262326deb55` Micron's GAAP gross margin was 84.6% in fiscal Q3, compared to 37.7% a year earlier.<br>`atomic:654d1dbc4140313a4d476ac1` Micron reported fiscal third-quarter revenue of $41.5 billion, up 74% year-over-year.<br>`atomic:66a20718ff387571030b3984` Micron reported free cash flow of $18.304 billion in fiscal Q3.<br>`atomic:bb6985bd83b60f6462c1d11c` Micron's fiscal Q3 capex was $7.826 billion, up 166.37% year-over-year.<br>`atomic:bf834fcdd4fd5ef49c7cbe9f` Micron signaled capex over $40 billion for next fiscal year, with roughly $20 billion allocated to construction and clean rooms.</sub> | DISCLOSED_IN | 7 | — |
| D12 | `D12-PO-000026` Micron fiscal Q3 2026 earnings disclosure<br><sub>`atomic:0579c187003109cbe82df2ac` Micron's earnings per share came in at $25.11.<br>`atomic:654d1dbc4140313a4d476ac1` Micron reported fiscal third-quarter revenue of $41.5 billion, up 74% year-over-year.</sub> | DISCLOSED_IN | 2 | — |
| D14 | `D14-PO-000110` Micron Q3 FY2026 earnings disclosure<br><sub>`atomic:1aa613473954efa0a715b65c` Micron's revenues soared 346% year-over-year.<br>`atomic:82fa82c7e4c68df0682b536b` Micron reported a surge in profit to $28.2 billion in its third quarter.<br>`atomic:c158b302322f818a88bd6a75` Micron's customers committed $22 billion to secure supplies of its chips.<br>`atomic:f311794692dfe6014f1a8799` Micron Technology posted its quarterly results on Wednesday afternoon.</sub> | DISCLOSED_IN | 4 | — |
| D15 | `D15-PO-000128` Micron's third-quarter operating margin was 80.4%.<br><sub>`atomic:01be8aaace1cfca446f8ba0e` Micron's fiscal fourth-quarter gross margin is expected to climb to about 86%.<br>`atomic:0e669ab2f79e2aaf4dd176c8` Micron's third-quarter gross margin was 84.6%, ahead of its own guidance of 81%.<br>`atomic:1aa613473954efa0a715b65c` Micron's revenues soared 346% year-over-year.<br>`atomic:2a1fecba66036ef0234b7b6f` Micron expects fourth-quarter revenue of $50 billion, with a range of $49 billion to $51 billion, versus consensus estimates of $42.95 billion.<br>`atomic:3c1b58580323511624dc24e6` Micron's third-quarter operating margin was 80.4%.<br>`atomic:65dcaaa6702721b71651e665` Micron expects fourth-quarter net income to exceed $40 billion.<br>`atomic:9b2e72eb2d703ba563cb115d` Unit sales in the key data center market are expected to grow only by the high teens for Micron.<br>`atomic:eb6463b504081729d403bdef` Micron produced $28.2 billion in net income in the third quarter.</sub> | COMPONENT_OF | 8 | — |
| D16 | `D16-PO-000035` Micron fiscal Q3 2026 earnings release and guidance<br><sub>`atomic:0579c187003109cbe82df2ac` Micron's earnings per share came in at $25.11.<br>`atomic:1201cb65e94e6e1739050b5f` Micron raised its 2026 capital expenditure forecast and signalled meaningfully higher spending in 2027.<br>`atomic:2a1fecba66036ef0234b7b6f` Micron expects fourth-quarter revenue of $50 billion, with a range of $49 billion to $51 billion, versus consensus estimates of $42.95 billion.<br>`atomic:5441f336e8af76d9e521c748` Micron expects adjusted earnings of roughly $31 per share in the current quarter.<br>`atomic:654d1dbc4140313a4d476ac1` Micron reported fiscal third-quarter revenue of $41.5 billion, up 74% year-over-year.<br>`atomic:92e130cf973bbd7689e42c76` Micron reported record revenue and a record gross margin of 84.9%, exceeding Wall Street's estimates.</sub> | DISCLOSED_IN | 6 | — |
| D16 | `D16-PO-000067` Micron Q3 FY2026 results and strategic customer agreement disclosures<br><sub>`atomic:4dd1c642ced3e3e9897a3d30` Micron expects its strategic customer agreements to eventually cover at least half of total company revenue, generating roughly $100 billion in remaining performance obligations.<br>`atomic:80a8f09414ddea71c8596b6d` The strategic customer agreements include price floors and ceilings, are backed by cash deposits and financial commitments, and carry no termination provisions.</sub> | DISCLOSED_IN | 2 | — |
| D16 | `D16-PO-000105` Micron plan to return 100% of excess free cash flow to shareholders<br><sub>`atomic:25fe94f65c989387adc33a89` For fiscal 2027, $32 billion in share repurchases would represent only about 25% of Micron's potential free cash flow generation.</sub> | DISCLOSED_IN | 1 | — |
| D17 | `D17-PO-000018` Micron record capital investment<br><sub>`atomic:c7cc8f6380ec62883aed8f3d` Micron is investing at record levels to meet customer demand.</sub> | DISCLOSED_IN | 1 | — |
| D17 | `D17-PO-000085` Micron fiscal Q3 earnings disclosure<br><sub>`atomic:0579c187003109cbe82df2ac` Micron's earnings per share came in at $25.11.<br>`atomic:1ecc039a3dc213761285752c` Micron reported GAAP profits up 104% sequentially.<br>`atomic:eb57c99e75d9e01942a376f9` Analysts expected Micron to earn $20.78 per share adjusted on $35.8 billion in quarterly sales.</sub> | DISCLOSED_IN | 3 | — |
| D19 | `D19-PO-000123` Micron Q3 earnings announcement<br><sub>`atomic:654d1dbc4140313a4d476ac1` Micron reported fiscal third-quarter revenue of $41.5 billion, up 74% year-over-year.</sub> | DISCLOSED_IN | 1 | — |
| D22 | `D22-PO-000082` Micron Q3 FY2026 earnings disclosure<br><sub>`atomic:0579c187003109cbe82df2ac` Micron's earnings per share came in at $25.11.<br>`atomic:058673290d7e00b0d3e4f0e3` Micron's multi-year Strategic Customer Agreements will significantly enhance the durability and predictability of Micron's strong financial performance.<br>`atomic:2a1fecba66036ef0234b7b6f` Micron expects fourth-quarter revenue of $50 billion, with a range of $49 billion to $51 billion, versus consensus estimates of $42.95 billion.<br>`atomic:6258d64d46b6f50ee478924c` Micron anticipates fourth-quarter adjusted earnings of $31 per share, with a range of $30 to $32 per share, versus estimates of $25.50 per share.<br>`atomic:654d1dbc4140313a4d476ac1` Micron reported fiscal third-quarter revenue of $41.5 billion, up 74% year-over-year.</sub> | DISCLOSED_IN | 5 | — |
| D23 | `D23-PO-000118` Micron earnings report demonstrating strong demand and pricing power<br><sub>`atomic:11ce5de9676b1f1903081275` Micron's earnings report demonstrated strong demand, continued pricing power, and extended visibility.<br>`atomic:b8eb4ea5e84a1b72927749af` The factors in Micron's earnings report (strong demand, continued pricing power, extended visibility) were not fully reflected in market expectations.</sub> | DISCLOSED_IN | 2 | — |
| D23 | `D23-PO-000129` Micron fiscal Q4 guidance for revenue and earnings<br><sub>`atomic:25e31e53ee373ac39f8f8031` Micron's fourth-quarter guidance for revenue and earnings exceeded consensus forecasts.<br>`atomic:2a1fecba66036ef0234b7b6f` Micron expects fourth-quarter revenue of $50 billion, with a range of $49 billion to $51 billion, versus consensus estimates of $42.95 billion.<br>`atomic:5441f336e8af76d9e521c748` Micron expects adjusted earnings of roughly $31 per share in the current quarter.</sub> | DISCLOSED_IN | 3 | — |
| D26 | `D26-PO-000112` Micron reported record revenue and a record gross margin of 84.9%, exceeding Wall Street's estimates.<br><sub>`atomic:92e130cf973bbd7689e42c76` Micron reported record revenue and a record gross margin of 84.9%, exceeding Wall Street's estimates.</sub> | COMPONENT_OF | 1 | — |
| D26 | `D26-PO-000114` Micron expects its gross margin to rise to approximately 86% in the current quarter.<br><sub>`atomic:18860b8be7e01923814679e0` Micron expects its gross margin to rise to approximately 86% in the current quarter.</sub> | COMPONENT_OF | 1 | — |
| D28 | `D28-PO-000042` Micron fiscal Q3 earnings report for period ended May 28, 2026<br><sub>`atomic:045eb510dcb9a5c288b1ab91` All four of Micron's business units (cloud memory, core data center, mobile and client, and automotive and embedded) posted higher revenue than both the prior quarter and the year-ago period.<br>`atomic:101a1239b5a45daa2ed2e466` Micron's cloud memory business unit's operating margin reached 78%.<br>`atomic:654d1dbc4140313a4d476ac1` Micron reported fiscal third-quarter revenue of $41.5 billion, up 74% year-over-year.<br>`atomic:877526c1eef5b84e31e4ba5e` Micron's core data center unit grew revenue more than sevenfold year over year.<br>`atomic:88ec993d9c20a6b50eb074d8` Micron's fiscal Q3 revenue came in at about $41.5 billion for the period ended May 28, 2026.<br>`atomic:90a3131bfd546ec510cf0805` Micron's cloud memory business unit grew revenue from $3.39 billion a year ago to $13.77 billion.<br>`atomic:bb6985bd83b60f6462c1d11c` Micron's fiscal Q3 capex was $7.826 billion, up 166.37% year-over-year.<br>`atomic:bfe1ae26def3e78dc9f2c519` Micron's gross margin reached 84.6% on a GAAP basis in fiscal Q3.</sub> | DISCLOSED_IN | 8 | — |
| D28 | `D28-PO-000089` Micron fiscal Q4 guidance for revenue, gross margin, and EPS<br><sub>`atomic:01be8aaace1cfca446f8ba0e` Micron's fiscal fourth-quarter gross margin is expected to climb to about 86%.<br>`atomic:0ff8efb98dbc8dde9b60d4cd` Micron guided for adjusted earnings per share of $31.00, plus or minus $1.00, for fiscal Q4.<br>`atomic:2a1fecba66036ef0234b7b6f` Micron expects fourth-quarter revenue of $50 billion, with a range of $49 billion to $51 billion, versus consensus estimates of $42.95 billion.</sub> | DISCLOSED_IN | 3 | — |
| D29 | `D29-PO-000080` Micron's earnings per share came in at $25.11.<br><sub>`atomic:0579c187003109cbe82df2ac` Micron's earnings per share came in at $25.11.<br>`atomic:072c6978068919e24acebc01` Micron's valuation is close to 19 times forward earnings.<br>`atomic:0b6e4b4252d89b3a1d9fe811` Micron's revenue quadrupled from the prior year.<br>`atomic:1a3ea022cd942f457f08af4b` Micron's revenue of $41.5 billion beat estimates by $6.4 billion.<br>`atomic:2a1fecba66036ef0234b7b6f` Micron expects fourth-quarter revenue of $50 billion, with a range of $49 billion to $51 billion, versus consensus estimates of $42.95 billion.<br>`atomic:b69df01e074bd08936917947` Micron's earnings have risen 1,368% year-over-year.</sub> | COMPONENT_OF | 6 | `atomic:072c6978068919e24acebc01` Micron's valuation is close to 19 times forward earnings. |

## MCP-000002 — Micron strategic customer agreements announcement and signing

Package ID: `package-v3:fdcde819496fb11e0ed40b8cb90fe931`  
Parent occurrence count: 11; Atomic coverage: 16

压缩后的描述：Micron strategic customer agreements (SCAs) signing/announcement: longer-term take-or-pay deals typically lasting five years, 16 contracts with data centers and automakers in the three- to five-year range potentially worth $22 billion, plus HBM4 shipments and HBM capacity commitments.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D07 | `D07-PO-000016` Micron strategic customer agreements signing<br><sub>`atomic:6ce959dec062a91386e93292` Micron signed 16 strategic customer agreements.</sub> | COMPONENT_OF | 1 | — |
| D08 | `D08-PO-000092` Micron HBM capacity commitments<br><sub>`atomic:a2f2c723a5d55cc58fb0fa40` Micron's entire 2026 output of high-bandwidth memory chips is sold out under fixed-price contracts.</sub> | COMPONENT_OF | 1 | — |
| D09 | `D09-PO-000131` Micron Strategic Customer Agreements (SCAs)<br><sub>`atomic:6ce959dec062a91386e93292` Micron signed 16 strategic customer agreements.</sub> | COMPONENT_OF | 1 | — |
| D10 | `D10-PO-000101` Micron strategic customer agreements signing<br><sub>`atomic:073d6437dbc1b6c1bfb03aa6` Micron is locking in long-term prices at high margins through Strategic Customer Agreements.<br>`atomic:2475bf90b8899b0676fd56bd` Micron entered into 16 customer agreements that run through 2030 and require customers to purchase a certain volume of memory products.<br>`atomic:48cff2c4b5e6934c1ac72d26` Micron expects $22 billion in commitments from the customer agreements signed so far.<br>`atomic:d4b26b37640b1210fd68bfc8` Micron expects half of its revenue to eventually come from strategic agreements.</sub> | COMPONENT_OF | 4 | — |
| D11 | `D11-PO-000052` Micron Strategic Customer Agreements (SCAs)<br><sub>No projected Atomic after unique-owner resolution</sub> | — | 0 | — |
| D15 | `D15-PO-000104` Micron introduced strategic customer agreements, longer-term contracts that typically last five years.<br><sub>`atomic:5667ac59ea64eca066e01862` Micron introduced strategic customer agreements, longer-term contracts that typically last five years.<br>`atomic:84fd9d3028d28d80b0ce548e` Micron is selling its chips for roughly six times their direct costs.<br>`atomic:a320d3ca18116933bad40c51` Micron is being disciplined with its spending.<br>`atomic:c7185058d4b9a50bdd9a37b5` Unit volumes are falling in the PC and smartphone markets for Micron.</sub> | COMPONENT_OF | 4 | `atomic:84fd9d3028d28d80b0ce548e` Micron is selling its chips for roughly six times their direct costs.<br>`atomic:a320d3ca18116933bad40c51` Micron is being disciplined with its spending.<br>`atomic:c7185058d4b9a50bdd9a37b5` Unit volumes are falling in the PC and smartphone markets for Micron. |
| D17 | `D17-PO-000127` Micron Strategic Customer Agreements<br><sub>`atomic:073d6437dbc1b6c1bfb03aa6` Micron is locking in long-term prices at high margins through Strategic Customer Agreements.</sub> | DISCLOSED_IN | 1 | — |
| D24 | `D24-PO-000117` Micron disclosure of $22 billion customer strategic commitments<br><sub>`atomic:80739eaedc690ebfb0381866` Micron disclosed that customers have committed approximately $22 billion through strategic agreements, as the company ramps production of high-bandwidth memory used in AI accelerators.</sub> | DISCLOSED_IN | 1 | — |
| D28 | `D28-PO-000041` Micron strategic customer agreements and HBM4 shipments<br><sub>`atomic:b316f1386b845078c8f2f8a2` Micron is shipping HBM4 in high volume to its lead customer and sending qualification samples to additional end customers.<br>`atomic:b3573175de3c1ab80cf8afac` Micron announced multi-year Strategic Customer Agreements that lock in volume and provide pricing visibility for memory supply.<br>`atomic:eb2299cec03670d1d4c997bd` The 16 signed agreements represent about 20% of Micron's DRAM volume and a third of its NAND volume over the agreement period.</sub> | DISCLOSED_IN | 3 | `atomic:b316f1386b845078c8f2f8a2` Micron is shipping HBM4 in high volume to its lead customer and sending qualification samples to additional end customers. |
| D29 | `D29-PO-000071` Micron has secured 16 contracts with customers, including data centers and automakers, in the three- to five-year range that could bring in $22 billion.<br><sub>`atomic:2e81f026563f277a00e1e248` Micron has secured 16 contracts with customers, including data centers and automakers, in the three- to five-year range that could bring in $22 billion.</sub> | COMPONENT_OF | 1 | — |
| D30 | `D30-PO-000132` Micron take-or-pay deals announcement<br><sub>`atomic:1c13f1b0b9b4cb22b0476a0e` Customers such as Nvidia committed $22 billion to lock in supplies of memory chips from Micron.</sub> | COMPONENT_OF | 1 | — |

## MCP-000003 — Micron fiscal Q3 2026 earnings call

Package ID: `package-v3:cc09ce1f53ec24094ee8060d74c62478`  
Parent occurrence count: 6; Atomic coverage: 22

压缩后的描述：Micron fiscal Q3 2026 earnings call: CAPEX guidance, CEO/management commentary on AI-driven memory demand and supply, structural transformation of the memory industry, memory shortages expected to persist at least through 2028, and memory demand outlook including humanoid robots.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D05 | `D05-PO-000008` Micron fiscal Q3 2026 earnings call CAPEX guidance<br><sub>`atomic:7b32f1749dc809864ad387c9` Micron expects quarterly capital expenditures in fiscal 2027 to exceed its fiscal fourth-quarter capital expenditure levels.<br>`atomic:adbac165884c3af1e148a19b` Micron expects total fiscal 2026 capital spending of approximately $27 billion.<br>`atomic:bc16c8a5da2fae4551c1ca30` Micron expects fiscal fourth-quarter capital expenditures of approximately $10 billion.<br>`atomic:c5dfa95aa588d161a2de0d94` More than half of the increase in quarterly capital expenditures in fiscal 2027 will come from construction spending.</sub> | DISCLOSED_IN | 4 | — |
| D13 | `D13-PO-000120` Micron fiscal Q3 2026 earnings call statements on memory demand and humanoid robots<br><sub>`atomic:015e9cfcace125e2e7045c6f` Memory has become a strategic asset in the AI era.<br>`atomic:0999cce6feb2c625ad0e5ecd` Advances are creating a growing content-rich opportunity for high-bandwidth, low-power memory and storage that powers real-time perception, inference, and control.<br>`atomic:622dbdddd5fe424d8104ef20` Humanoid robots carry 10 times the amount of memory as an average L2+ vehicle.<br>`atomic:74392e8a721818c0c808a405` Micron believes AI-driven demand is outpacing the industry's ability to add new supply.<br>`atomic:9905f62e78d1000ce638fd64` Micron now expects tight memory market conditions to persist beyond calendar 2027.<br>`atomic:9af7c1ae3dface122b959e7a` AI system performance is architecturally dependent on memory subsystem performance and capacity.<br>`atomic:9f44f5c7e28f10712b84b52f` Micron expects a sustained, substantial multi-decade memory demand cycle to begin in the latter part of this decade.<br>`atomic:e4482448868381d146572d43` Continued advances in simulation, foundation models and integrated hardware and software are accelerating the development of physical AI.<br>`atomic:fb2aecddd77dadb6cfc07589` Micron currently has no line of sight as to when memory supply will be able to catch up with increasing demand.</sub> | DISCLOSED_IN | 9 | — |
| D15 | `D15-PO-000044` Micron's management stated that the memory industry has been structurally transformed by AI and that memory shortages are expected to persist at least through 2028.<br><sub>`atomic:21d6ec15284a4ec610f9471b` Micron's management stated it is in the early innings of significant innovation and productivity improvements.<br>`atomic:a226cb40f0a235040497a099` Micron's management stated that the memory industry has been structurally transformed by AI and that memory shortages are expected to persist at least through 2028.</sub> | COMPONENT_OF | 2 | — |
| D17 | `D17-PO-000047` Micron CEO comments on AI demand and supply<br><sub>`atomic:157f96bb2ad63fddb5c8307b` Micron CEO Sanjay Mehrotra believes supply will not catch up with demand in the foreseeable future.<br>`atomic:d7aef5925cde7ca6f96b68dd` Micron CEO Sanjay Mehrotra confirmed the artificial intelligence revolution is ongoing.</sub> | DISCLOSED_IN | 2 | — |
| D23 | `D23-PO-000102` Micron AI memory shortage and supply constraints through 2028<br><sub>`atomic:b589c784ecc94881450fb230` Micron's CEO stated that the AI memory shortage could last beyond 2028.<br>`atomic:e26336d84698dc78e1e5c68c` Micron indicated that supply constraints could persist through 2028.</sub> | DISCLOSED_IN | 2 | — |
| D24 | `D24-PO-000013` Micron fiscal Q3 earnings call guidance on memory supply tightness<br><sub>`atomic:3a27ee694f3a8ddf59c2bba6` Micron's CEO Sanjay Mehrotra stated that the company still sees no clear end to tightening memory markets.<br>`atomic:618ee538dc481503f1c3f873` Micron does not currently have line of sight as to when memory supply will be able to catch up with increasing demand.<br>`atomic:63e8f89d8b2237a9faa7636c` Micron expects tight memory market conditions to persist beyond calendar 2027.</sub> | DISCLOSED_IN | 3 | — |

## MCP-000004 — Micron stock price reaction to Q3 FY2026 earnings

Package ID: `package-v3:003b4974e31e21a011b341a30742366e`  
Parent occurrence count: 13; Atomic coverage: 19

压缩后的描述：Micron stock rose roughly 14-15% after Q3 FY2026 earnings, including after-hours gains and a sharp rise on June 25, 2026, adding more than $100 billion in market value and hitting new highs.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D06 | `D06-PO-000009` Micron stock market reaction on June 25, 2026<br><sub>`atomic:0970ca1a4cecea51ce0db1b3` Micron stock rose sharply on June 25.<br>`atomic:23938b4447d10ac16c414490` Micron stock traded nearly 14% higher as of 11:50 a.m. ET.<br>`atomic:56c78e1eb8e691b1a9c1c171` MU shares are trading at about 4 times their price at the start of 2026.<br>`atomic:73b5dba36ce9fc2952f39c42` MU's relative strength index (RSI) rose into the mid-60s on June 25.<br>`atomic:b3b5a86624956b869fe3621c` MU is trading at a forward P/E multiple of less than 18x.</sub> | COMPONENT_OF | 5 | `atomic:56c78e1eb8e691b1a9c1c171` MU shares are trading at about 4 times their price at the start of 2026.<br>`atomic:73b5dba36ce9fc2952f39c42` MU's relative strength index (RSI) rose into the mid-60s on June 25.<br>`atomic:b3b5a86624956b869fe3621c` MU is trading at a forward P/E multiple of less than 18x. |
| D08 | `D08-PO-000031` Market reaction to Micron earnings<br><sub>`atomic:0970ca1a4cecea51ce0db1b3` Micron stock rose sharply on June 25.<br>`atomic:0a5fd6e48b2991de4747bb50` Micron's market value reached approximately $1.16 trillion following the share price increase.<br>`atomic:5dfe26397727a1e06050fe9a` Micron's stock has climbed approximately 700% over the past year.</sub> | COMPONENT_OF | 3 | `atomic:5dfe26397727a1e06050fe9a` Micron's stock has climbed approximately 700% over the past year. |
| D12 | `D12-PO-000081` Micron after-hours stock gain following earnings<br><sub>`atomic:93899f49394acdc5198b8359` Micron's stock gained roughly 15% in after-hours trading following the earnings announcement.</sub> | COMPONENT_OF | 1 | — |
| D14 | `D14-PO-000054` Micron stock market reaction June 23-25, 2026<br><sub>`atomic:3e64bd9a814e6473b4c47ecc` Micron's stock was up by more than 16% in pre-market trade on Thursday.<br>`atomic:b8d935f6a452a84bfcf87677` Micron's stock fell 13% on Tuesday, part of a global sell off of AI and AI-adjacent companies.</sub> | COMPONENT_OF | 2 | `atomic:b8d935f6a452a84bfcf87677` Micron's stock fell 13% on Tuesday, part of a global sell off of AI and AI-adjacent companies. |
| D15 | `D15-PO-000002` Micron's stock rose 15% after hours on Wednesday following its third-quarter earnings report.<br><sub>`atomic:e770e8b363664584026501d6` Micron's stock rose 15% after hours on Wednesday following its third-quarter earnings report.</sub> | COMPONENT_OF | 1 | — |
| D18 | `D18-PO-000017` Micron stock surge to new highs after earnings<br><sub>`atomic:96dc99f8de265bb4c29543e5` Micron's stock surged to new highs following its earnings report.</sub> | DISCLOSED_IN | 1 | — |
| D22 | `D22-PO-000010` Micron stock price movement on June 25, 2026<br><sub>`atomic:f573dc834b8f883bedef8d90` Micron shares jumped 11.5% and traded at $1,167.88 on Thursday.</sub> | DISCLOSED_IN | 1 | — |
| D26 | `D26-PO-000083` Micron added more than $100 billion in market value on Thursday.<br><sub>`atomic:35f193a4cce20433f98c4018` Micron added more than $100 billion in market value on Thursday.</sub> | COMPONENT_OF | 1 | — |
| D26 | `D26-PO-000111` Micron's earnings release on Thursday caused its stock to rise sharply.<br><sub>`atomic:56fe52314fc615b78488dad5` Micron's earnings release on Thursday caused its stock to rise sharply.<br>`atomic:95b85a071de2db6df2851471` Apple's stock has fallen into the upper end of its old trading range, bringing the $275 to $280 area into focus.</sub> | COMPONENT_OF | 2 | `atomic:95b85a071de2db6df2851471` Apple's stock has fallen into the upper end of its old trading range, bringing the $275 to $280 area into focus. |
| D27 | `D27-PO-000074` Micron stock reaction to earnings<br><sub>`atomic:c8fa3ec270c6f43d009fe367` Micron stock soared nearly 16% after its earnings report.</sub> | COMPONENT_OF | 1 | — |
| D28 | `D28-PO-000066` Micron stock jump and market capitalization rise after earnings<br><sub>`atomic:11423b957ed2f909816316be` Micron's market capitalization is above $1.2 trillion.<br>`atomic:89d7adce6048d8da90de3bf3` Micron's shares jumped about 16% in after-hours trading on Wednesday, climbing from about $1,049 at Wednesday's close to about $1,215.</sub> | COMPONENT_OF | 2 | — |
| D29 | `D29-PO-000025` Micron stock traded nearly 14% higher as of 11:50 a.m. ET.<br><sub>`atomic:23938b4447d10ac16c414490` Micron stock traded nearly 14% higher as of 11:50 a.m. ET.</sub> | COMPONENT_OF | 1 | — |
| D29 | `D29-PO-000087` Micron stock rose sharply on June 25.<br><sub>`atomic:0970ca1a4cecea51ce0db1b3` Micron stock rose sharply on June 25.</sub> | COMPONENT_OF | 1 | — |

## MCP-000005 — Micron stock price movement on June 24, 2026

Package ID: `package-v3:34081b2ece7a110ca5a10c3c611364fc`  
Parent occurrence count: 1; Atomic coverage: 1

压缩后的描述：Micron stock price movement on June 24, 2026.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D09 | `D09-PO-000037` Micron stock price movement on June 24, 2026<br><sub>`atomic:fadda38a2ca9dd4569f67494` Micron shares closed down 0.31% at $1,048.51 on Wednesday.</sub> | COMPONENT_OF | 1 | — |

## MCP-000006 — Micron stock price performance over the past year and 2026

Package ID: `package-v3:f30befc1dfd360cd8a3b57f8dcf80d05`  
Parent occurrence count: 2; Atomic coverage: 4

压缩后的描述：Micron share price performance over the past year and year-to-date in 2026.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D04 | `D04-PO-000088` Micron share price performance over past year and year-to-date<br><sub>`atomic:09dfb5b36b352af816b247a1` Micron's share prices increased 233.45% year-to-date.</sub> | COMPONENT_OF | 1 | — |
| D10 | `D10-PO-000113` Micron stock performance in 2026<br><sub>`atomic:4bc15b2a922454a4fef3c0e2` Micron's stock is currently cheaper than other tech giants such as Nvidia and Alphabet.<br>`atomic:cb5a0eb0bddc81870b7a7424` Micron's stock gained more than 260% during the current year.<br>`atomic:ed70b0ce9f6e8f63f0649b2b` Micron stock currently trades at 16 times forward earnings estimates.</sub> | COMPONENT_OF | 3 | `atomic:4bc15b2a922454a4fef3c0e2` Micron's stock is currently cheaper than other tech giants such as Nvidia and Alphabet.<br>`atomic:ed70b0ce9f6e8f63f0649b2b` Micron stock currently trades at 16 times forward earnings estimates. |

## MCP-000007 — Micron post-earnings analyst price target updates

Package ID: `package-v3:423f1e88b54dfe7d630a8a87f4e44932`  
Parent occurrence count: 5; Atomic coverage: 8

压缩后的描述：Post-Q3 earnings analyst price target updates for Micron: Mizuho raised its target to $1,375, UBS increased its target, Needham raised to $1,550, with consensus price target and upside noted.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D04 | `D04-PO-000094` Needham price target increase on Micron to $1,550<br><sub>`atomic:00a227f90f5a1a1ef214ed44` Needham's analyst cited the continued strength of the memory market as a reason for the price target increase.<br>`atomic:1637ee8ffb18d54809c4b6fe` The analyst expressed optimism for long-term agreements being signed in the industry, as they provide suppliers with better demand visibility over multiple years.<br>`atomic:2afc94fa4c408671db173d91` Needham increased its price target on Micron to $1,550 from $500 while maintaining a Buy rating.<br>`atomic:e984da92571502aac836401c` The analyst forecasts that strong market fundamentals will persist due to continued strong demand, a robust pricing environment, and limited capacity additions.</sub> | COMPONENT_OF, DISCLOSED_IN | 4 | — |
| D06 | `D06-PO-000058` Consensus price target and upside for MU stock<br><sub>`atomic:0bd5a97f9bb936caa6aefadf` The highest price target on MU is $1,750, implying potential upside of another 45% from the current price.</sub> | DISCLOSED_IN | 1 | `atomic:0bd5a97f9bb936caa6aefadf` The highest price target on MU is $1,750, implying potential upside of another 45% from the current price. |
| D11 | `D11-PO-000063` UBS price target increase on Micron<br><sub>`atomic:f1f559c42591a380db9b871c` UBS tripled Micron's price target last month.</sub> | COMPONENT_OF | 1 | `atomic:f1f559c42591a380db9b871c` UBS tripled Micron's price target last month. |
| D20 | `D20-PO-000014` Mizuho price target raise for Micron Technology to $1375<br><sub>`atomic:b8a3b22e631ff0a3c2756583` Mizuho raised its price target for Micron Technology to 1375 dollars.</sub> | DISCLOSED_IN | 1 | `atomic:b8a3b22e631ff0a3c2756583` Mizuho raised its price target for Micron Technology to 1375 dollars. |
| D22 | `D22-PO-000019` Analyst price target changes following Micron Q3 earnings<br><sub>`atomic:ecf8f4b6d1982084cf70f412` Analysts made changes to their price targets on Micron following the earnings announcement.</sub> | DISCLOSED_IN | 1 | `atomic:ecf8f4b6d1982084cf70f412` Analysts made changes to their price targets on Micron following the earnings announcement. |

## MCP-000008 — Micron post-earnings analyst commentary and reports

Package ID: `package-v3:afa3cbf7e095969cb88c784647ae32dd`  
Parent occurrence count: 12; Atomic coverage: 25

压缩后的描述：Post-earnings analyst commentary and reports: Citi and Bank of America reports; Bernstein's Mark Newman said strategic customer agreements could include pricing ceilings limiting Micron's ability to raise memory prices; Wedbush's Dan Ives said there are no cracks in AI demand, giving a bright green light to own core tech winners; other analysts called Micron a buying opportunity; also included Nvidia forward P/E comparison on June 25, 2026, forward valuation/uncertainty on agreements, and investment recommendation.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D06 | `D06-PO-000028` Citi post-earnings analyst report on Micron<br><sub>`atomic:6505a86bf7c13a1dd01ed8de` The SCAs are expected to drive approximately 40% of Micron's revenue over the next five years.<br>`atomic:7fa07dec3ebef9088da5203e` Citi analysts led by Atif Malik maintained their Buy rating on Micron shares and raised their price target to $1,400.</sub> | DISCLOSED_IN | 2 | `atomic:6505a86bf7c13a1dd01ed8de` The SCAs are expected to drive approximately 40% of Micron's revenue over the next five years.<br>`atomic:7fa07dec3ebef9088da5203e` Citi analysts led by Atif Malik maintained their Buy rating on Micron shares and raised their price target to $1,400. |
| D06 | `D06-PO-000029` Nvidia forward P/E comparison on June 25, 2026<br><sub>`atomic:d54b7ec9117821a2a8bf5cfe` Nvidia is trading at about 23x forward P/E at the time of writing.</sub> | DISCLOSED_IN | 1 | `atomic:d54b7ec9117821a2a8bf5cfe` Nvidia is trading at about 23x forward P/E at the time of writing. |
| D07 | `D07-PO-000122` Wedbush analyst commentary on Micron results and AI trade<br><sub>`atomic:546be09a449c0abf09758d44` Ives framed the results against rising nervousness over the AI trade.<br>`atomic:68f8305964ee9062c2d40371` Wedbush's recent checks across Asia reinforced its confidence in strong demand trends.<br>`atomic:901f6b9073f0b58a4dcd29f1` Dan Ives said the memory and chip trade remained intact and in early stages.<br>`atomic:aa741e2f8d23dc6a2ab0da75` Wedbush sees no cracks in AI demand on the hardware or software front.<br>`atomic:ae83d036b893d5f0f1f4679e` Ives gave a green light to own core technology winners into year-end.<br>`atomic:d806cf501e405c954feb90a3` Ives argued the figures showed no cracks in AI demand across hardware or software.<br>`atomic:e05a0114b6a72fa1df3696cf` Wedbush rates Micron at outperform with a price target of $1,300.<br>`atomic:ee411c7d734fcea68243cbb8` Ives flagged risks including rapid technology disruption.</sub> | COMPONENT_OF | 8 | — |
| D10 | `D10-PO-000097` Micron valuation assessment<br><sub>No projected Atomic after unique-owner resolution</sub> | — | 0 | — |
| D10 | `D10-PO-000100` Investment recommendation for Micron<br><sub>`atomic:22c29a006c6d12587352009b` It is a favorable opportunity for growth investors to add Micron stock to their portfolios.</sub> | COMPONENT_OF | 1 | `atomic:22c29a006c6d12587352009b` It is a favorable opportunity for growth investors to add Micron stock to their portfolios. |
| D16 | `D16-PO-000091` Bank of America analyst report on Micron<br><sub>`atomic:c1c1f05e73c7c322d03eee41` Bank of America sees Micron's shares implying a roughly 10% free cash flow yield at current levels.</sub> | DISCLOSED_IN | 1 | `atomic:c1c1f05e73c7c322d03eee41` Bank of America sees Micron's shares implying a roughly 10% free cash flow yield at current levels. |
| D16 | `D16-PO-000116` Wedbush analyst commentary on memory demand and AI trade<br><sub>`atomic:aa741e2f8d23dc6a2ab0da75` Wedbush sees no cracks in AI demand on the hardware or software front.<br>`atomic:e08449bb418a607fbfc95fb3` Demand for NAND and DRAM continues to significantly exceed industry supply.</sub> | DISCLOSED_IN | 2 | `atomic:e08449bb418a607fbfc95fb3` Demand for NAND and DRAM continues to significantly exceed industry supply. |
| D23 | `D23-PO-000030` Analyst and market commentary on Micron earnings implications<br><sub>`atomic:21a7f6ce36750dd29f11c4d5` Micron's indication that supply constraints could persist through 2028 strengthened the view that the AI infrastructure buildout is far from over.<br>`atomic:3179b0aae7aa554ee530f48f` Demand was never the question; durability was the question.<br>`atomic:366c1cc340d0115994f0c88e` Some analysts caution that memory markets remain cyclical.<br>`atomic:b6db2b8ed1f4478f80a4c869` Micron's earnings beat could contribute to further gains in AI-related equities and the broader technology market.</sub> | COMPONENT_OF | 4 | `atomic:21a7f6ce36750dd29f11c4d5` Micron's indication that supply constraints could persist through 2028 strengthened the view that the AI infrastructure buildout is far from over.<br>`atomic:3179b0aae7aa554ee530f48f` Demand was never the question; durability was the question.<br>`atomic:366c1cc340d0115994f0c88e` Some analysts caution that memory markets remain cyclical.<br>`atomic:b6db2b8ed1f4478f80a4c869` Micron's earnings beat could contribute to further gains in AI-related equities and the broader technology market. |
| D28 | `D28-PO-000060` Micron forward valuation and analyst uncertainty on agreements<br><sub>`atomic:83d92f7bf8d7558bf2d100e4` Micron's stock forward price-to-earnings ratio is about 10.<br>`atomic:b923f3fe48f72519660868e4` The terms of the Strategic Customer Agreements have not been disclosed, and there is uncertainty about their implications.</sub> | COMPONENT_OF | 2 | `atomic:83d92f7bf8d7558bf2d100e4` Micron's stock forward price-to-earnings ratio is about 10.<br>`atomic:b923f3fe48f72519660868e4` The terms of the Strategic Customer Agreements have not been disclosed, and there is uncertainty about their implications. |
| D29 | `D29-PO-000043` Bernstein analyst Mark Newman thinks Micron's new strategic customer agreements could include pricing ceilings that would limit how much Micron could raise memory prices.<br><sub>`atomic:82305c488da56c06817d2960` Bernstein analyst Mark Newman thinks Micron's new strategic customer agreements could include pricing ceilings that would limit how much Micron could raise memory prices.<br>`atomic:b1f2e2cedd4babdbe416795a` Mark Newman wrote that the ceiling suggests limited headroom and that these contracts likely wouldn't be able to avoid cyclicality.<br>`atomic:e12e7147c0670a87ce044fc1` The author recommends that long-term investors can buy Micron and should dollar-cost average and be prepared for volatility.</sub> | COMPONENT_OF | 3 | `atomic:82305c488da56c06817d2960` Bernstein analyst Mark Newman thinks Micron's new strategic customer agreements could include pricing ceilings that would limit how much Micron could raise memory prices.<br>`atomic:b1f2e2cedd4babdbe416795a` Mark Newman wrote that the ceiling suggests limited headroom and that these contracts likely wouldn't be able to avoid cyclicality.<br>`atomic:e12e7147c0670a87ce044fc1` The author recommends that long-term investors can buy Micron and should dollar-cost average and be prepared for volatility. |
| D29 | `D29-PO-000076` Wedbush analyst Dan Ives said that there are no cracks in AI demand and that it gives a bright green light to own core tech winners into year-end.<br><sub>`atomic:9491e8eb328aa69683c51862` Wedbush analyst Dan Ives said that there are no cracks in AI demand and that it gives a bright green light to own core tech winners into year-end.</sub> | COMPONENT_OF | 1 | — |
| D30 | `D30-PO-000070` Analyst comment about Micron buying opportunity<br><sub>`atomic:dcc41f85c79727a8a68a5005` If sales trade at five times and demand rose by twelve times, it is still a good buying opportunity for a company like Micron.</sub> | COMPONENT_OF | 1 | `atomic:dcc41f85c79727a8a68a5005` If sales trade at five times and demand rose by twelve times, it is still a good buying opportunity for a company like Micron. |

## MCP-000009 — UBS analyst commentary on memory supply constraints

Package ID: `package-v3:21b9e0d4d41e14c46951120890a4041e`  
Parent occurrence count: 2; Atomic coverage: 2

压缩后的描述：UBS analysts said DRAM supply is likely to be constrained until at least halfway through 2028 and NAND until at least the end of 2027.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D29 | `D29-PO-000073` UBS analysts have said that DRAM is likely to be constrained until at least halfway through 2028.<br><sub>`atomic:7cc2eb3969fa3998135c92ab` UBS analysts have said that DRAM is likely to be constrained until at least halfway through 2028.</sub> | COMPONENT_OF | 1 | — |
| D29 | `D29-PO-000084` UBS analysts have said NAND is likely to be constrained until at least the end of 2027.<br><sub>`atomic:13fabf7bd62b1ecb2f854fd7` UBS analysts have said NAND is likely to be constrained until at least the end of 2027.</sub> | COMPONENT_OF | 1 | — |

## MCP-000010 — Apple price increase announcement on June 25, 2026

Package ID: `package-v3:ccb8b5aa87076cd6becf52aed49e7a94`  
Parent occurrence count: 6; Atomic coverage: 7

压缩后的描述：Apple announced price increases on June 25, 2026 for MacBook Neo, MacBook Air, MacBook Pro, iPad Pro, iPad Air, HomePod, HomePod mini, and Apple TV, while leaving iPhone prices unchanged; the size of the increases was not expected.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D01 | `D01-PO-000064` Apple price hike announcement on June 25, 2026<br><sub>`atomic:a157122f90fff5988857f2b1` Apple said it has never seen a component price increase this much, this quickly and left the door open to further increases.<br>`atomic:f4be232cc162eba75f689a05` Tim Cook described the memory crisis as a "hundred-year flood."<br>`atomic:f4bf36877cae0bcd900e543e` Apple's stock price fell 0.56% intraday following the announcement of price hikes on its MacBook and iPad products.</sub> | COMPONENT_OF | 3 | `atomic:f4bf36877cae0bcd900e543e` Apple's stock price fell 0.56% intraday following the announcement of price hikes on its MacBook and iPad products. |
| D24 | `D24-PO-000057` Apple price increase on certain products announced June 25, 2026<br><sub>`atomic:2123b9406e10669e4f43641a` Apple increased prices on certain products including MacBook Neo, MacBook Air, MacBook Pro, iPad Pro, iPad Air, HomePod, HomePod mini and Apple TV, while iPhone pricing remained unchanged.</sub> | DISCLOSED_IN | 1 | — |
| D26 | `D26-PO-000053` Apple left iPhone prices unchanged.<br><sub>`atomic:8e32737c174d789170d7183d` Apple left iPhone prices unchanged.</sub> | COMPONENT_OF | 1 | — |
| D26 | `D26-PO-000106` The size of Apple's price increases was not expected.<br><sub>`atomic:726348dbf2b89776cc87c2a6` The size of Apple's price increases was not expected.</sub> | COMPONENT_OF | 1 | — |
| D26 | `D26-PO-000126` Apple increased prices on certain products including MacBook Neo, MacBook Air, MacBook Pro, iPad Pro, iPad Air, HomePod, HomePod mini and Apple TV, while iPhone pricing remained unchanged.<br><sub>`atomic:2123b9406e10669e4f43641a` Apple increased prices on certain products including MacBook Neo, MacBook Air, MacBook Pro, iPad Pro, iPad Air, HomePod, HomePod mini and Apple TV, while iPhone pricing remained unchanged.</sub> | COMPONENT_OF | 1 | — |
| D27 | `D27-PO-000125` Apple price increase announcement on June 25, 2026<br><sub>`atomic:2123b9406e10669e4f43641a` Apple increased prices on certain products including MacBook Neo, MacBook Air, MacBook Pro, iPad Pro, iPad Air, HomePod, HomePod mini and Apple TV, while iPhone pricing remained unchanged.<br>`atomic:f070836b40638b31c9ea4309` Apple announced price increases on several of its products.</sub> | COMPONENT_OF | 2 | — |

## MCP-000011 — Apple stock technical analysis

Package ID: `package-v3:228761cd1ffc4ab7479a16d4dd149f43`  
Parent occurrence count: 1; Atomic coverage: 1

压缩后的描述：Apple stock technical analysis: a close above the $275-$280 zone would confirm a successful test of old price supply.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D26 | `D26-PO-000115` If Apple's stock closes above the $275-$280 zone, it would confirm a successful test of old price supply.<br><sub>`atomic:70e76fe2c641a8803fa37291` If Apple's stock closes above the $275-$280 zone, it would confirm a successful test of old price supply.</sub> | COMPONENT_OF | 1 | — |

## MCP-000012 — Global AI selloff on Tuesday June 23, 2026

Package ID: `package-v3:adae4828089f73f1ce911461b61d95e8`  
Parent occurrence count: 3; Atomic coverage: 7

压缩后的描述：Global AI selloff on Tuesday, June 23, 2026: the Magnificent Seven stocks declined about 2% and reached a two-month low, with analysts offering explanations for the selloff.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D14 | `D14-PO-000059` Analyst explanations for the selloff<br><sub>`atomic:14175a11df06f281714a7634` Some market analysts attributed the sell off to worries sparked by Google and SpaceX falling sharply the previous day.<br>`atomic:9fd4dd62e0759fde5f6b9e6d` Some suggested that investors were spooked by likely forthcoming Federal Reserve rate hikes.</sub> | COMPONENT_OF | 2 | — |
| D14 | `D14-PO-000108` Global AI selloff on Tuesday June 23, 2026<br><sub>`atomic:2f9db6945b1dbdefbccba02f` SK Hynix and Samsung tumbled more than 12% on Tuesday, dragging the rest of South Korea's stock market down.<br>`atomic:425f9bb116f4dcffcd058ae4` The sell off was not triggered by any specific cause according to the report.<br>`atomic:62d3cccb13397911c8b6b0ae` On Tuesday, investors were dumping AI stocks.<br>`atomic:b9a01d8cda589bfd35d0a72b` The Kospi tumbled 10% on Tuesday, causing a circuit breaker and a 20-minute trading halt.</sub> | COMPONENT_OF | 4 | — |
| D26 | `D26-PO-000001` The Magnificent Seven stocks declined by about 2% and reached a two-month low.<br><sub>`atomic:b8b50c74c656e40aa973e0fd` The Magnificent Seven stocks declined by about 2% and reached a two-month low.</sub> | COMPONENT_OF | 1 | — |

## MCP-000013 — US-China geopolitical tensions risk to semiconductor supply chain

Package ID: `package-v3:313206cc94b9848068a487504b461689`  
Parent occurrence count: 1; Atomic coverage: 1

压缩后的描述：US-China geopolitical tensions pose a risk to the semiconductor supply chain.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D07 | `D07-PO-000055` US-China geopolitical tensions risk to semiconductor supply chain<br><sub>`atomic:01094bc14651a6e72f014fb3` Geopolitical tensions between the United States and China could disrupt the semiconductor supply chain.</sub> | COMPONENT_OF | 1 | — |

## MCP-000014 — Megacap AI market value losses over the past month

Package ID: `package-v3:745457fffc64f14ad7dfc9f468c0cfac`  
Parent occurrence count: 1; Atomic coverage: 1

压缩后的描述：Megacap AI winners lost trillions of dollars in market value over the past month.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D26 | `D26-PO-000056` Megacap AI winners lost trillions of dollars in market value over the past month.<br><sub>`atomic:d9a16d7ea6c9d4502f7520e4` Megacap AI winners lost trillions of dollars in market value over the past month.</sub> | COMPONENT_OF | 1 | — |

## MCP-000015 — Apple market value decline

Package ID: `package-v3:d659a6c9485a8de5080e02e11c76b1d8`  
Parent occurrence count: 1; Atomic coverage: 4

压缩后的描述：Apple has lost nearly $200 billion in market value.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D26 | `D26-PO-000079` Apple has lost nearly $200 billion in market value.<br><sub>`atomic:be50718dab5494ed9eb5cba9` Apple has lost nearly $200 billion in market value.<br>`atomic:c999d6e9939711c14ebcd843` Apple's stock is currently trading below the level where its May breakout started.<br>`atomic:ce0562c0c58e5f1c2f4509e7` If Apple's stock closes below $275, it would signal a bull trap and require investors to reassess megacaps' ability to absorb AI hardware costs.<br>`atomic:f8d9cddfc4af87c6bc3bfa28` Apple's stock fell more than 5% following its price increases on some Macs and iPads.</sub> | COMPONENT_OF | 4 | — |

## MCP-000016 — Broad market rebound and trading activity on June 25, 2026

Package ID: `package-v3:ef5f0e083a418ce8a47c9482ea36ae2e`  
Parent occurrence count: 4; Atomic coverage: 21

压缩后的描述：Broad market rebound on Thursday, June 25, 2026; trading volume for WDC, SNDK, and STX remained active during the regular session.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D14 | `D14-PO-000023` Market rebound on Thursday June 25, 2026<br><sub>`atomic:36b9f67f537c1f3f4fbd5197` South Korea's Kospi finished 5.4% higher on Thursday.<br>`atomic:64b5f35451ee87e80fa0bf05` By Thursday, investors were believers again in AI stocks.<br>`atomic:7675ecc092ebebb14c140a3e` Japan's Nikkei 225 index closed up 4.6% on Thursday.<br>`atomic:8bfbdd6a5bf51bfecbbbe143` SK Hynix's stock rose 13% on Thursday.<br>`atomic:99590c6c06065c77cc707c1b` The Stoxx 600 index was up 0.6% by early afternoon local time on Thursday.<br>`atomic:d3d4ea43dfa8edafb6923907` The Dow was pointing up by 0.3% in pre-market trade on Thursday.<br>`atomic:e41e8d29d288ff2d4ea282d5` The Nasdaq was up 2.15% in pre-market trade on Thursday.</sub> | COMPONENT_OF | 7 | `atomic:8bfbdd6a5bf51bfecbbbe143` SK Hynix's stock rose 13% on Thursday. |
| D19 | `D19-PO-000096` Market trading session on June 25, 2026<br><sub>`atomic:05749fdb5e41b0fc6e982ed3` Triller Group Inc shares increased by 259%.<br>`atomic:2c4d56de0c27d7f76600ba39` Technology shares increased by 1.6%.<br>`atomic:8bf3f9e867aa75bc70ed9ef8` The NASDAQ index increased by 0.7% to a level of 25,654.49.<br>`atomic:9ae391a361e479cd70c8d445` Communication services stocks decreased by 1.9%.<br>`atomic:ffafda54ccf95c5fc47ed091` The Dow Jones index increased by 0.5% to a level of 52,107.28.</sub> | COMPONENT_OF | 5 | `atomic:05749fdb5e41b0fc6e982ed3` Triller Group Inc shares increased by 259%. |
| D21 | `D21-PO-000033` Trading volume for WDC, SNDK, and STX remained active during the regular session.<br><sub>`atomic:663aa24e2fab23d0fa210fdf` Trading volume for WDC, SNDK, and STX remained active during the regular session.<br>`atomic:a1e2c704e3b04666a454b8f3` Micron's strong results triggered a sympathy rally in WDC, SNDK, and STX.<br>`atomic:a3bc5ada9edd6fcca03d1aa5` Benzinga's Edge Stock Rankings indicate that MU has a positive price trend across all time frames, with momentum in the 99th percentile.<br>`atomic:ca0d366718edab6c6155eaf5` WDC, SNDK, and STX reversed their intraday losses in after-hours trading following Micron's results.</sub> | COMPONENT_OF | 4 | `atomic:663aa24e2fab23d0fa210fdf` Trading volume for WDC, SNDK, and STX remained active during the regular session.<br>`atomic:a1e2c704e3b04666a454b8f3` Micron's strong results triggered a sympathy rally in WDC, SNDK, and STX.<br>`atomic:a3bc5ada9edd6fcca03d1aa5` Benzinga's Edge Stock Rankings indicate that MU has a positive price trend across all time frames, with momentum in the 99th percentile.<br>`atomic:ca0d366718edab6c6155eaf5` WDC, SNDK, and STX reversed their intraday losses in after-hours trading following Micron's results. |
| D27 | `D27-PO-000061` Market trading day on June 25, 2026<br><sub>`atomic:2b4637c385fa573c1f3e18e0` The Nasdaq Composite fell 0.46% to 25,359.<br>`atomic:594329784dae5179af7a9c6b` Alphabet ended at $343.71, down 0.46%.<br>`atomic:a92c1e9b29a7a3e5b5e017db` The S&P 500 slipped 0.01% to 7,357.<br>`atomic:a97d3b61e23b28362fc45e03` Apple trading volume reached 106.4 million shares, about 119% above its three-month average of 48.5 million shares.<br>`atomic:c7d140b2493307f66e6b3d3a` Microsoft closed at $352.83, down 3.46%.</sub> | COMPONENT_OF | 5 | `atomic:a97d3b61e23b28362fc45e03` Apple trading volume reached 106.4 million shares, about 119% above its three-month average of 48.5 million shares. |

## MCP-000017 — KOSPI surge and Korean memory stock rally on June 25, 2026

Package ID: `package-v3:787efa1ecd8b85348e6eea26e47ed2b7`  
Parent occurrence count: 4; Atomic coverage: 11

压缩后的描述：KOSPI surged on June 25, 2026 with sidecar activation and investor flows, as SK Hynix and Samsung Electronics stocks rallied/recovered.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D02 | `D02-PO-000021` SK Hynix and Samsung stock rally and Kospi rise on June 25, 2026<br><sub>`atomic:426ffe083563a429511d4acb` SK Hynix's stock climbed nearly 12% in early Thursday trading before trimming gains to around 10%.<br>`atomic:93185c2fed2e97d0b3c1aed1` Samsung Electronics' stock rallied on June 25, 2026.<br>`atomic:9512ec48e79fba35b21ebcc7` The Kospi Index rose 6%.</sub> | COMPONENT_OF | 3 | — |
| D12 | `D12-PO-000007` KOSPI surge on June 25, 2026 with sidecar activation and investor flows<br><sub>`atomic:2a66ac50d0cf5b13500bbdbd` The Korea Exchange (KRX) activated a buy-side sidecar shortly after the open, suspending program trading for five minutes.<br>`atomic:4113d971175d4db5e49c163d` Foreign investors net sold approximately 600 billion won in the KOSPI market.<br>`atomic:4b652cadf30cbe33b38f2c6e` Institutions net bought around 100 billion won in the market.<br>`atomic:5e6e13fe66697ecb9ca122d1` Individual investors net bought roughly 490 billion won in the market.<br>`atomic:c241f97d9a79957e92c66710` Foreign net selling has totaled approximately 12.2 trillion won over the past five trading days.<br>`atomic:c3a8389d6d288ec2f4b9569e` KOSPI surged more than 5% at the open on June 25, rising above 8,900 from 8,400 the prior session.</sub> | COMPONENT_OF | 6 | — |
| D12 | `D12-PO-000027` Samsung Electronics stock price recovery on June 25, 2026<br><sub>`atomic:32a056e076c39d8a1cdc4001` Samsung Electronics reclaimed the 360,000 won level.</sub> | COMPONENT_OF | 1 | — |
| D12 | `D12-PO-000046` SK Hynix stock movement on June 25, 2026<br><sub>`atomic:f7d9fbfa7761836dacefeebe` SK Hynix reclaimed the 2.8 million won level.</sub> | COMPONENT_OF | 1 | — |

## MCP-000018 — SK Hynix and Samsung composition of Kospi market value

Package ID: `package-v3:fbcd758d2bb3e8e98efddb637f9a4378`  
Parent occurrence count: 1; Atomic coverage: 1

压缩后的描述：SK Hynix and Samsung Electronics' combined composition of KOSPI market value.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D14 | `D14-PO-000012` SK Hynix and Samsung composition of Kospi market value<br><sub>`atomic:db0f10c7e193a127a9272cf1` SK Hynix and Samsung make up about half of the Kospi's total market value.</sub> | COMPONENT_OF | 1 | — |

## MCP-000019 — SK Hynix Nasdaq listing plan and ADR trading

Package ID: `package-v3:7defa4ac87bfe2723f826723183407e7`  
Parent occurrence count: 2; Atomic coverage: 3

压缩后的描述：SK Hynix's planned Nasdaq US listing ($29 billion plan) and related ADR trading.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D02 | `D02-PO-000086` SK Hynix $29 billion US listing plan and ADR trading<br><sub>`atomic:2202577d6da96af5d36fee7b` SK Hynix is seeking 45.45 trillion won through the US listing.<br>`atomic:8536e6da70363b018736f77f` SK Hynix expects its American depositary receipts to start trading on July 10.<br>`atomic:8ca4d7b9c805cceb64e50df7` SK Hynix laid out plans for a $29 billion US listing.</sub> | DISCLOSED_IN, STAGE_OF | 3 | — |
| D14 | `D14-PO-000077` SK Hynix Nasdaq listing plan<br><sub>`atomic:8ca4d7b9c805cceb64e50df7` SK Hynix laid out plans for a $29 billion US listing.</sub> | COMPONENT_OF | 1 | — |

## MCP-000020 — SK Hynix 12-month market value gain and share performance

Package ID: `package-v3:58fe5be11389b3d1eff66bec8b7f45c1`  
Parent occurrence count: 1; Atomic coverage: 2

压缩后的描述：SK Hynix 12-month market value gain and share performance.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D02 | `D02-PO-000072` SK Hynix 12-month market value gain and share performance<br><sub>`atomic:6411a5af2c6ae2928c5a76e4` The share gains lifted SK Hynix's market value above $1 trillion.<br>`atomic:e5ae76e52715225327050446` SK Hynix's Seoul-traded shares gained more than 800% over the past 12 months.</sub> | COMPONENT_OF | 2 | — |

## MCP-000021 — Sandisk stock surge on June 25, 2026

Package ID: `package-v3:a45b18fc48dff94f0950a48f560120da`  
Parent occurrence count: 2; Atomic coverage: 1

压缩后的描述：Sandisk stock surged on June 25, 2026.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D17 | `D17-PO-000119` Sandisk stock surge on June 25, 2026<br><sub>`atomic:19e30c64047d9530dcb3d25b` Sandisk shares jumped about 15% early Thursday after Citi raised its price target.</sub> | DISCLOSED_IN | 1 | — |
| D25 | `D25-PO-000015` Sandisk stock market reaction on June 25, 2026<br><sub>`atomic:19e30c64047d9530dcb3d25b` Sandisk shares jumped about 15% early Thursday after Citi raised its price target.</sub> | COMPONENT_OF | 1 | — |

## MCP-000022 — Sandisk upcoming earnings expectation and schedule

Package ID: `package-v3:6618006b868925bcd555cdbf4764b6e9`  
Parent occurrence count: 1; Atomic coverage: 2

压缩后的描述：Sandisk upcoming earnings expectation and schedule.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D17 | `D17-PO-000039` Sandisk upcoming earnings expectation and schedule<br><sub>`atomic:359b19888b2570a1a25d76dc` Sandisk is scheduled to report earnings on Aug. 24.<br>`atomic:c4e48f21c9620f1b8ca18ffd` Analysts expect Sandisk earnings to more than double sequentially to $33.72 per share.</sub> | DISCLOSED_IN | 2 | — |

## MCP-000023 — Sandisk investor day in August and anticipated updates

Package ID: `package-v3:0df1b5979e42540691a4e013a6d2509b`  
Parent occurrence count: 1; Atomic coverage: 3

压缩后的描述：Sandisk investor day in August and anticipated updates.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D25 | `D25-PO-000011` Sandisk investor day in August and its anticipated updates<br><sub>`atomic:0b75cdf4d50be5c6c72c8cb9` Sandisk investor day could bring updates on demand expectations, technology roadmap, and capital return plans.<br>`atomic:5cc1f88bc6cd21742357ffd7` Sandisk investor day is scheduled in August.<br>`atomic:6cd3882b1e94ae83a7367c80` Updates from Sandisk's investor day could help shape the next phase of Sandisk's stock move.</sub> | COMPONENT_OF, DISCLOSED_IN | 3 | — |

## MCP-000024 — Citi analyst report on Sandisk on June 25, 2026

Package ID: `package-v3:0fe59fba65d4e5e7df5765a529518235`  
Parent occurrence count: 1; Atomic coverage: 3

压缩后的描述：Citi analyst report on Sandisk on June 25, 2026.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D25 | `D25-PO-000130` Citi analyst report on Sandisk on June 25, 2026<br><sub>`atomic:22e68772baff0e7bdb53c0c7` Citi raised its price target on Sandisk to $2,500 from $2,025.<br>`atomic:8a9bef46f0990ea9b5a17ba5` Citi expects Sandisk to gain over the next 90 days.<br>`atomic:c4692d9ad28c451200e25282` Higher estimates for Sandisk are being driven by current trends.</sub> | DISCLOSED_IN | 3 | — |

## MCP-000025 — Sandisk Strategic Customer Agreements

Package ID: `package-v3:09c2b894ee7c651bf1189be2f24b88af`  
Parent occurrence count: 1; Atomic coverage: 1

压缩后的描述：Sandisk Strategic Customer Agreements.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D17 | `D17-PO-000049` Sandisk Strategic Customer Agreements<br><sub>`atomic:89f37b973d1fb02c2b0cbc03` Sandisk is also locking in long-term prices at high margins through Strategic Customer Agreements.</sub> | DISCLOSED_IN | 1 | — |

## MCP-000026 — NAND market conditions and AI-driven storage demand supporting Sandisk

Package ID: `package-v3:f96159ffda4de1b6672aadfd8fe12884`  
Parent occurrence count: 1; Atomic coverage: 3

压缩后的描述：NAND market conditions and AI-driven storage demand supporting Sandisk.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D25 | `D25-PO-000095` NAND market conditions and AI-driven storage demand supporting Sandisk<br><sub>`atomic:37ac95eeebbb464181254ebf` The pricing backdrop for Sandisk appears to be improving as more data center operators use less costly solid-state drives for AI workloads.<br>`atomic:640cd5589f2b7bd4d7364fdc` AI-related data center spending is helping drive storage needs.<br>`atomic:b946f612cb2765c4f7573751` Sandisk remains supported by healthier NAND supply and demand conditions.</sub> | COMPONENT_OF | 3 | — |

## MCP-000027 — Qualcomm data center announcements and Q2 results

Package ID: `package-v3:c057518594da93ddebbf9d330e3ffeb1`  
Parent occurrence count: 1; Atomic coverage: 5

压缩后的描述：Qualcomm data center announcements and Q2 results.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D11 | `D11-PO-000093` Qualcomm data center announcements and Q2 results<br><sub>`atomic:042a4f6b8b10ce16f27d6ba6` Qualcomm raised its non-handset revenue target to $40 billion by 2029, nearly double its prior forecast, with roughly $15 billion expected from data center.<br>`atomic:295c89f48ccee1c0d657ba38` Qualcomm's Q2 handset revenue fell 13% year-over-year to $6.024 billion, with memory supply constraints among Chinese OEMs cited as the cause.<br>`atomic:5675a65afa8bf08f5026b233` Qualcomm named META as the first customer for its new data center CPU and signed two hyperscale deals for custom chips, one in the United States and one in China.<br>`atomic:83f24507bc8d53e960f13098` Qualcomm is mostly a handset business until the Dragonfly C1000 ships to Meta in 2028.<br>`atomic:87c7064e3ad4ed295d8147bb` Qualcomm had a market cap of $215.827 billion.</sub> | COMPONENT_OF, DISCLOSED_IN | 5 | `atomic:295c89f48ccee1c0d657ba38` Qualcomm's Q2 handset revenue fell 13% year-over-year to $6.024 billion, with memory supply constraints among Chinese OEMs cited as the cause.<br>`atomic:87c7064e3ad4ed295d8147bb` Qualcomm had a market cap of $215.827 billion. |

## MCP-000028 — Market reaction to Micron and Qualcomm announcements

Package ID: `package-v3:216c475a28f632b0e9d110dd72e25ce0`  
Parent occurrence count: 1; Atomic coverage: 2

压缩后的描述：Market reaction to Micron and Qualcomm announcements.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D11 | `D11-PO-000069` Market reaction to Micron and Qualcomm announcements<br><sub>`atomic:75bf1ac9fd1d49a2183ef9d4` Qualcomm's shares rose 12% following the data center news.<br>`atomic:75e9ed3cc157921ab8687e74` Micron's stock crossed $1 trillion in market cap alongside SK Hynix.</sub> | COMPONENT_OF | 2 | `atomic:75e9ed3cc157921ab8687e74` Micron's stock crossed $1 trillion in market cap alongside SK Hynix. |

## MCP-000029 — Micron-Anthropic strategic partnership and investment

Package ID: `package-v3:7bc8f01cc3a207de978fd376b7bb4783`  
Parent occurrence count: 1; Atomic coverage: 2

压缩后的描述：Micron-Anthropic strategic partnership and investment.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D04 | `D04-PO-000078` Micron-Anthropic strategic partnership and investment<br><sub>`atomic:985017a3394f1a0e9f1771c5` Micron makes a strategic investment in Anthropic's Series H funding round.<br>`atomic:a0d7afb8b51af3612d75d16e` Micron announced a strategic partnership with Anthropic to scale next-generation AI infrastructure.</sub> | COMPONENT_OF, DISCLOSED_IN | 2 | — |

## MCP-000030 — Micron business description design and manufacturing

Package ID: `package-v3:4b82888cb48c3af786e8fd4d3265d8b2`  
Parent occurrence count: 1; Atomic coverage: 1

压缩后的描述：Micron business description: design and manufacturing.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D04 | `D04-PO-000034` Micron business description design and manufacturing<br><sub>`atomic:8af091a968ddc410157b9552` Micron designs, develops, manufactures, and markets memory and storage products globally.</sub> | COMPONENT_OF | 1 | — |

## MCP-000031 — Tesla positioning Optimus as long-term growth opportunity

Package ID: `package-v3:01fb10062c17d68a4ed4f7e0a348e7cd`  
Parent occurrence count: 1; Atomic coverage: 1

压缩后的描述：Tesla positioning Optimus as long-term growth opportunity.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D13 | `D13-PO-000107` Tesla positioning Optimus as long-term growth opportunity<br><sub>`atomic:337fb9d4d247c3e8f383ef8d` Tesla continues to position Optimus as one of its biggest long-term growth opportunities.</sub> | COMPONENT_OF | 1 | — |

## MCP-000032 — Defiance and Roundhill leveraged memory ETF launches

Package ID: `package-v3:da0a1f299cee36162a2bc045c160162e`  
Parent occurrence count: 1; Atomic coverage: 2

压缩后的描述：Defiance and Roundhill leveraged memory ETF launches.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D03 | `D03-PO-000124` Defiance and Roundhill leveraged memory ETF launches<br><sub>`atomic:949cc1597b9e5eabf0236852` Roundhill Investments launched a leveraged fund designed to deliver twice the daily performance of the Roundhill Memory ETF (NASDAQ:DRAM).<br>`atomic:f4290552dc08096cc722f03a` Defiance launched a leveraged 2X DRAM ETF.</sub> | COMPONENT_OF | 2 | `atomic:f4290552dc08096cc722f03a` Defiance launched a leveraged 2X DRAM ETF. |

## MCP-000033 — Jim Lebenthal's purchase of Micron stock

Package ID: `package-v3:22a81f60851714136aef6749d5db6be7`  
Parent occurrence count: 1; Atomic coverage: 1

压缩后的描述：Jim Lebenthal's purchase of Micron stock.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D18 | `D18-PO-000121` Jim Lebenthal's purchase of Micron stock<br><sub>`atomic:527004a431d24b5a58d6a937` Jim Lebenthal bought Micron stock.</sub> | DISCLOSED_IN | 1 | — |

## MCP-000034 — Micron HBM supply and AI infrastructure demand

Package ID: `package-v3:9d9cd8b2c6baa9dad4d3f3b6c89631d7`  
Parent occurrence count: 1; Atomic coverage: 1

压缩后的描述：Micron HBM supply and AI infrastructure demand.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D23 | `D23-PO-000099` Micron HBM supply and AI infrastructure demand<br><sub>`atomic:58cb7dcf171d4481e82dfae0` High-bandwidth memory (HBM) remains in tight supply as hyperscalers and enterprises continue investing in AI infrastructure.</sub> | COMPONENT_OF | 1 | — |

## MCP-000035 — Hyperscaler capital spending and memory supply

Package ID: `package-v3:9dffb51fbc3df252b346786cfc63cb5b`  
Parent occurrence count: 1; Atomic coverage: 2

压缩后的描述：Hyperscaler capital spending and memory supply.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D08 | `D08-PO-000109` Hyperscaler capital spending and memory supply<br><sub>`atomic:086afbd3eadfd4a0816d9c4e` Amazon, Microsoft, Google and Meta have collectively earmarked hundreds of billions of dollars in capital spending this year.<br>`atomic:cc4a339d2c57050c9115df74` New factories are not expected to add meaningful memory output until 2028.</sub> | COMPONENT_OF | 2 | — |

## MCP-000036 — Memory supply constraints and dynamics

Package ID: `package-v3:02239009427eb0ad65340ec821d823b2`  
Parent occurrence count: 1; Atomic coverage: 2

压缩后的描述：Memory supply constraints and dynamics.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D09 | `D09-PO-000020` Memory supply constraints and dynamics<br><sub>`atomic:d9fa37aa9ec5363e824d3276` Some NAND flash suppliers are reallocating cleanroom space toward DRAM production, constraining NAND supply growth.<br>`atomic:e851545c0906caab922cdfbb` Growing HBM adoption is putting additional pressure on conventional memory supply.</sub> | COMPONENT_OF | 2 | — |

## MCP-000037 — Memory demand-supply conditions and AI adoption trends

Package ID: `package-v3:953a4b9b8c7d743f0396521ef5da5923`  
Parent occurrence count: 1; Atomic coverage: 3

压缩后的描述：Memory demand-supply conditions and AI adoption trends.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D07 | `D07-PO-000004` Memory demand-supply conditions and AI adoption trends<br><sub>`atomic:5f3df833fb51933753019a8a` Demand for Micron's NAND and DRAM memory chips continued to significantly exceed industry supply.<br>`atomic:93bada97360ad5e668a41a09` The pace of AI adoption is accelerating.<br>`atomic:a725065df1cc784e8c1ff0ab` The focus is shifting to launching enterprise use cases in the second half of 2026.</sub> | COMPONENT_OF | 3 | `atomic:5f3df833fb51933753019a8a` Demand for Micron's NAND and DRAM memory chips continued to significantly exceed industry supply. |

## MCP-000038 — AI-driven memory market supply tightening and demand expansion

Package ID: `package-v3:6e9055dcc7065f8729539efa3d9f4aec`  
Parent occurrence count: 1; Atomic coverage: 4

压缩后的描述：AI-driven memory market supply tightening and demand expansion.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D24 | `D24-PO-000038` AI-driven memory market supply tightening and demand expansion<br><sub>`atomic:31a05eee10f25dedba6c162e` Memory has become a strategic asset, and AI system performance is architecturally dependent on memory subsystem performance and capacity.<br>`atomic:6c302e58e2aa83826f7d9679` That demand has helped tighten industry supply, allowing memory manufacturers to secure stronger pricing and long-term customer agreements.<br>`atomic:a956b3f145b196fef2ec2f4d` Cloud providers and AI developers have been aggressively expanding data center capacity to support generative AI applications, fueling demand for advanced DRAM and NAND memory.<br>`atomic:bdb145135fc69d21ad1293af` Artificial intelligence has fundamentally reshaped the memory industry, with AI systems requiring increasingly larger amounts of high-performance memory for training and inference workloads.</sub> | COMPONENT_OF | 4 | — |

## MCP-000039 — Memory trade theme strengthening

Package ID: `package-v3:deca67ab5385f5ceb9a3a566c5eb9eab`  
Parent occurrence count: 1; Atomic coverage: 1

压缩后的描述：Memory trade theme strengthening.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D03 | `D03-PO-000045` Memory trade theme strengthening<br><sub>`atomic:faa03a5bb27b877974c319bc` Micron's results have strengthened the broader 'memory trade' theme.</sub> | COMPONENT_OF | 1 | — |

## MCP-000040 — Memory chip demand surge

Package ID: `package-v3:37bc73412a7d22cddbe513f4c5a310ed`  
Parent occurrence count: 1; Atomic coverage: 2

压缩后的描述：Memory chip demand surge.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D30 | `D30-PO-000051` Memory chip demand surge<br><sub>`atomic:43e872cbb8058ff7bd532b67` AI-related demand continues to outpace available supply of memory chips, fueling investor interest in memory-chip makers.<br>`atomic:afbed6102d18bc92ed158416` Demand for memory chips has surged alongside the need for greater computing power.</sub> | COMPONENT_OF | 2 | — |

## MCP-000041 — AI memory demand surge

Package ID: `package-v3:a5d16783cd0d6d880f76b12664eb57f5`  
Parent occurrence count: 1; Atomic coverage: 1

压缩后的描述：AI memory demand surge.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D03 | `D03-PO-000075` AI memory demand surge<br><sub>`atomic:748e546725a7a5e8eed48560` Demand for high-bandwidth memory (HBM) chips used in AI servers continued to surge.</sub> | COMPONENT_OF | 1 | — |

## MCP-000042 — Memory and storage price surge

Package ID: `package-v3:90e139a0c46f6db07c541ca51acf5df6`  
Parent occurrence count: 1; Atomic coverage: 2

压缩后的描述：Memory and storage price surge.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D01 | `D01-PO-000036` Memory and storage price surge<br><sub>`atomic:4714b36cbace3e43e5e95a74` Suppliers are redirecting production toward high-bandwidth memory used in AI servers.<br>`atomic:48eea1b53fa0842abe52bf2d` Memory and storage prices have quadrupled in the past three quarters.</sub> | COMPONENT_OF | 2 | `atomic:4714b36cbace3e43e5e95a74` Suppliers are redirecting production toward high-bandwidth memory used in AI servers. |

## MCP-000043 — Apple iPhone memory and AI features

Package ID: `package-v3:f0ff4b74b1f2564f976a008e2a339f45`  
Parent occurrence count: 1; Atomic coverage: 5

压缩后的描述：Apple iPhone memory and AI features.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D01 | `D01-PO-000050` Apple iPhone memory and AI features<br><sub>`atomic:39f00ee30afae5f01f3c7439` Apple's average selling price will rise 12% this year.<br>`atomic:4f28c5d10b0ec998eef0b8b2` IDC expects all new iPhone models to move to 12GB of RAM.<br>`atomic:7c686525f5c7043813f2da6d` Apple is pushing Apple Intelligence features that require more memory.<br>`atomic:97e2bda67198d487becf27f4` Approximately 54% of iPhones shipped since 2022 will not support the full new Siri experience.<br>`atomic:f7615e4e0e01f6bbd74244f3` Higher component costs could add roughly $200 per iPhone for Apple.</sub> | COMPONENT_OF | 5 | `atomic:7c686525f5c7043813f2da6d` Apple is pushing Apple Intelligence features that require more memory.<br>`atomic:97e2bda67198d487becf27f4` Approximately 54% of iPhones shipped since 2022 will not support the full new Siri experience. |

## MCP-000044 — AI companies with $1 trillion-plus valuations club

Package ID: `package-v3:66dfee2b576a2e53e03756759dfbd2ff`  
Parent occurrence count: 1; Atomic coverage: 1

压缩后的描述：AI companies with $1 trillion-plus valuations club.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D14 | `D14-PO-000032` AI companies with $1 trillion-plus valuations club<br><sub>`atomic:e8fe171898c2de68e9c8954e` SK Hynix and Micron are part of the group of AI companies with $1 trillion-plus valuations.</sub> | COMPONENT_OF | 1 | — |


## Independent Agent audit overlay

- 44个Package中，14个明确含误成员，29个可接受，1个边界不确定。
- 确定错误Atomic保守下界为50/281（17.79%），另有1个不确定Atomic。
- 14个singleton中3个明确漏合，人工漏合率21.43%。
- Singleton targets：`MCP-000011 → MCP-000015`；`MCP-000034 → MCP-000036`；`MCP-000041 → MCP-000040`。
- Micron earnings为3个组件；analyst-report与broad-market supercluster仍使质量Gate失败。

