# CDECR V3 Gate A - R3-derived canonical R2 Final Package Results

> 按“Package → Parent Occurrence → Atomic”整理。canonical、压缩描述和 Parent Occurrence 均为本轮新测试落库值。
> `Erroneously aggregated Atomic(s)` 由独立 Agent 逐包审核填写；`—`表示未发现明确错误或证据不足。

- Registry: `.tmp/cdecr/package_v3_gate_a_r3_canonical_r2_20260814.sqlite3`
- V2.0R R3 published baseline: P/R/F1 72.93%/42.36%/53.59%, 49 Packages, 20 singleton.
- Strict same-73 comparison: V2.0R 81.23%/33.43%/47.37% → V3 82.48%/50.37%/62.55%.

## Test summary

| Item | Value |
| --- | ---: |
| Input Atomic | 192 |
| Parent Proposal | 115 |
| Final Package | 34 |
| Singleton Package | 15/34 (44.12%) |
| Largest Package | 71 |
| V3 model calls | 2 |
| V3 input/output tokens | 7,471 / 36,370 |
| V3 total tokens | 43,841 |
| V3 first wall-clock | 308.36 s |
| Idempotency replay | 0 new calls; partition hash stable=True |

## Gold evaluation

| Metric | Value |
| --- | ---: |
| Alignment | 73/192 (38.02%) |
| Pair Precision | 82.48% |
| Pair Recall | 50.37% |
| Pair F1 | 62.55% |
| TP / FP / FN | 339 / 72 / 334 |

独立Agent审核：34个Package中8个在宽人工口径下明确误合；另有4个Package父边界可接受但内部Atomic已复合污染。Singleton宽口径有5个潜在重组目标；严格同父发生口径仅1个明确漏合、1个review、1个错误owner，其余应保持singleton。

---

## MCP-000001 — Micron fiscal Q3 2026 earnings report and Q4 FY2026 guidance

Package ID: `package-v3:a40ebbf568d9aac2317f9a8b69ae5501`  
Parent occurrence count: 29; Atomic coverage: 71

压缩后的描述：Micron fiscal Q3 2026 earnings report and Q4 FY2026 revenue guidance, including record revenue of $41.5 billion, AI memory demand disclosures, strong demand/market position in AI memory, and pricing/cost structure.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D01 | `D01-PO-000110` Micron reports record fiscal Q3 2026 results<br><sub>`atomic:ccd6361cee2dc431d15f2eb9` Micron topped Wall Street's estimates, posted record revenue, and delivered a record gross margin of 84.9%.</sub> | DISCLOSED_IN | 1 | — |
| D03 | `D03-PO-000026` Micron Q3 FY2026 financial results and guidance disclosure<br><sub>`atomic:307d5d154f718e17e785f064` Micron issued strong forward guidance.<br>`atomic:5c5ced7a5459462118185105` Micron's data center revenue more than doubled in the fiscal third quarter.<br>`atomic:a0678356c6cc3b7041e65619` Micron's fiscal Q3 revenue increased to about $41.5 billion from $23.9 billion in fiscal Q2 and $9.3 billion in the year-ago quarter, a 346% year-over-year jump.</sub> | DISCLOSED_IN | 3 | — |
| D05 | `D05-PO-000071` Micron fiscal Q3 2026 earnings call<br><sub>`atomic:4156bb58e605ca6a3366453f` Micron's balance sheet has never been stronger and is projected to strengthen further even as investment increases.<br>`atomic:522f55cb47a8ba985e1b6d0e` Micron's investments include continued expansion in Taiwan and Singapore.<br>`atomic:9c0d57733dd29260fc7d1b3f` Micron's investments include additional advanced packaging capacity aimed at supporting next-generation high-bandwidth memory (HBM) products.<br>`atomic:b86e281731936130c2776708` Micron's investments include leading-edge DRAM fabs in Idaho and New York.</sub> | DISCLOSED_IN | 4 | — |
| D06 | `D06-PO-000027` Micron fiscal Q3 2026 earnings disclosure<br><sub>`atomic:a0678356c6cc3b7041e65619` Micron's fiscal Q3 revenue increased to about $41.5 billion from $23.9 billion in fiscal Q2 and $9.3 billion in the year-ago quarter, a 346% year-over-year jump.<br>`atomic:ee57659a83077d8a96dd01ce` Micron management guided for approximately 20% sequential revenue growth in the current quarter.<br>`atomic:fb7ac1e57b359308e711e7ea` Micron's adjusted gross margin reached 84.9% in fiscal Q3.</sub> | DISCLOSED_IN | 3 | — |
| D07 | `D07-PO-000010` Micron fiscal Q3 2026 earnings disclosure<br><sub>`atomic:604e5c5b2fe498bfd16e5d54` Micron Technology reported stellar earnings after the market close on June 24, 2026.<br>`atomic:98cb55dfecc7faa6eb649249` Micron expects tight supply conditions to persist beyond its 2027 financial year, with gross margin guided to about 86%.<br>`atomic:a0678356c6cc3b7041e65619` Micron's fiscal Q3 revenue increased to about $41.5 billion from $23.9 billion in fiscal Q2 and $9.3 billion in the year-ago quarter, a 346% year-over-year jump.<br>`atomic:a8601ee5af8394692835a63e` Micron's data centre revenue has reached an annualised run rate of about $100 billion.<br>`atomic:a931b428e3798231b98bec0b` Micron expects revenue of approximately $50 billion in the current quarter.<br>`atomic:ccd6361cee2dc431d15f2eb9` Micron topped Wall Street's estimates, posted record revenue, and delivered a record gross margin of 84.9%.</sub> | DISCLOSED_IN | 6 | — |
| D08 | `D08-PO-000042` Micron fiscal Q3 2026 earnings report<br><sub>`atomic:046caf1751c4a8ac5882423f` Micron raised its 2026 capital expenditure forecast and signalled meaningfully higher spending in 2027.<br>`atomic:0f31ea49cfe03854ce0da82d` Micron's gross margin rivals or exceeds those of Nvidia and Meta.<br>`atomic:1aa613473954efa0a715b65c` Micron's revenues increased by 346% compared to the same period a year earlier.<br>`atomic:23d4ed3acdb5a73f5ffeecfa` Micron's adjusted earnings per share of $25.11 exceeded the expected $20.49.<br>`atomic:a0678356c6cc3b7041e65619` Micron's fiscal Q3 revenue increased to about $41.5 billion from $23.9 billion in fiscal Q2 and $9.3 billion in the year-ago quarter, a 346% year-over-year jump.<br>`atomic:a931b428e3798231b98bec0b` Micron expects revenue of approximately $50 billion in the current quarter.<br>`atomic:ab5408376e96fe6ffa2436a7` Micron expects adjusted earnings of roughly $31 per share in the current quarter.<br>`atomic:ccd6361cee2dc431d15f2eb9` Micron topped Wall Street's estimates, posted record revenue, and delivered a record gross margin of 84.9%.<br>`atomic:f472738b5f5587cfddf64fc2` Micron reported net income of $28.24 billion, or $24.67 per share, compared with less than $2 billion a year earlier.</sub> | DISCLOSED_IN | 9 | `atomic:0f31ea49cfe03854ce0da82d` Micron's gross margin rivals or exceeds those of Nvidia and Meta. |
| D09 | `D09-PO-000031` Micron Q3 FY2026 earnings call and results disclosure<br><sub>`atomic:ccd6361cee2dc431d15f2eb9` Micron topped Wall Street's estimates, posted record revenue, and delivered a record gross margin of 84.9%.<br>`atomic:d8da800e3a1dde07481bc111` Micron's third-quarter revenue increased 346% compared to the same quarter last year.</sub> | DISCLOSED_IN | 2 | — |
| D09 | `D09-PO-000075` Micron Q4 FY2026 guidance<br><sub>`atomic:9deef63d8a189ae1b13c5d84` Micron anticipates fourth-quarter adjusted earnings of $31 per share, with a tolerance of plus or minus $1.</sub> | DISCLOSED_IN | 1 | — |
| D10 | `D10-PO-000033` Micron's strong demand and market position in AI memory<br><sub>`atomic:16dfa617ebacdc192eed15cf` Demand from AI customers is soaring, and Micron may be in the early stages of this growth opportunity.<br>`atomic:b5b74978e24c7a6d5e7bdddc` Demand for Micron's memory products steadily surpassed supply despite competition from multiple players.</sub> | COMPONENT_OF | 2 | — |
| D11 | `D11-PO-000040` Micron Q4 FY2026 guidance disclosure<br><sub>`atomic:1f07eb41591396a559386f1f` Micron signaled capex of over $40 billion for next year, with roughly $20 billion allocated to construction and clean rooms.<br>`atomic:4413a4faa173ab593c42fb74` Micron guided its Q4 gross margin to around 86% and revenue to approximately $50 billion, plus or minus $1.0 billion.</sub> | DISCLOSED_IN | 2 | — |
| D11 | `D11-PO-000072` Micron Q3 FY2026 financial results disclosure<br><sub>`atomic:66a20718ff387571030b3984` Micron reported free cash flow of $18.304 billion for the quarter.<br>`atomic:68bd78ca78c348d3063c8446` Micron's Cloud Memory segment generated $13.769 billion, and Core Data Center generated another $11.524 billion.<br>`atomic:75579d289eded96f9e60f793` Micron reported non-GAAP EPS of $25.11, beating the consensus of $20.2843, marking the seventh consecutive quarter of beating Wall Street.<br>`atomic:88d5d359fc6743af962e57fc` Micron reported GAAP gross margin of 84.6%, compared to 37.7% a year earlier.<br>`atomic:a0678356c6cc3b7041e65619` Micron's fiscal Q3 revenue increased to about $41.5 billion from $23.9 billion in fiscal Q2 and $9.3 billion in the year-ago quarter, a 346% year-over-year jump.<br>`atomic:bb6985bd83b60f6462c1d11c` Micron reported Q3 capex of $7.826 billion, up 166.37% year-over-year.</sub> | DISCLOSED_IN | 6 | — |
| D12 | `D12-PO-000061` Micron fiscal Q3 2026 earnings results<br><sub>`atomic:a0678356c6cc3b7041e65619` Micron's fiscal Q3 revenue increased to about $41.5 billion from $23.9 billion in fiscal Q2 and $9.3 billion in the year-ago quarter, a 346% year-over-year jump.<br>`atomic:f9b75e07919353d91955d92d` Micron Technology's adjusted earnings per share for fiscal Q3 2026 was $25.11.</sub> | DISCLOSED_IN | 2 | — |
| D13 | `D13-PO-000002` Micron fiscal Q3 2026 earnings call disclosure on AI memory demand<br><sub>`atomic:0f5a401771c3724a0f3db308` Micron expects tight memory market conditions to persist beyond calendar 2027.<br>`atomic:2736cf94da1d632dd4d19662` Micron currently has no line of sight as to when memory supply will catch up with increasing demand.<br>`atomic:2e8f18c31eecc12af87a7028` Micron expects a sustained, substantial multi-decade memory demand cycle to begin in the latter part of this decade.<br>`atomic:546b9986034a5f7421166ada` Each Tesla Optimus robot could require substantially more advanced memory than today's driver-assistance-equipped vehicles, conditional on Tesla succeeding in deploying Optimus at scale across factories and eventually commercial markets.<br>`atomic:674deacc97646dbbfe9649cd` Advances in simulation, foundation models, and integrated hardware and software are accelerating the development of physical AI.<br>`atomic:730896e96c611e8f89591be8` AI-driven demand is outpacing the industry's ability to add new supply.<br>`atomic:9af7c1ae3dface122b959e7a` AI system performance is architecturally dependent on memory subsystem performance and capacity.<br>`atomic:9f965f61a65abd6b4c9459b5` There is a growing content-rich opportunity for high-bandwidth, low-power memory and storage that powers real-time perception, inference, and control.<br>`atomic:ceeda3bd4c1c45a23f63c99c` Humanoid robots carry 10 times the amount of memory as an average L2+ vehicle.<br>`atomic:d47171dda03449aab5ff6ae4` Memory has become a strategic asset in the AI era.<br>`atomic:f58d8ead1de0b7de472e832d` Humanoid robots could become memory-intensive as they process real-time vision, perform inference, and plan motion.</sub> | DISCLOSED_IN | 11 | `atomic:546b9986034a5f7421166ada` Each Tesla Optimus robot could require substantially more advanced memory than today's driver-assistance-equipped vehicles, conditional on Tesla succeeding in deploying Optimus at scale across factories and eventually commercial markets.<br>`atomic:674deacc97646dbbfe9649cd` Advances in simulation, foundation models, and integrated hardware and software are accelerating the development of physical AI.<br>`atomic:9f965f61a65abd6b4c9459b5` There is a growing content-rich opportunity for high-bandwidth, low-power memory and storage that powers real-time perception, inference, and control.<br>`atomic:ceeda3bd4c1c45a23f63c99c` Humanoid robots carry 10 times the amount of memory as an average L2+ vehicle.<br>`atomic:f58d8ead1de0b7de472e832d` Humanoid robots could become memory-intensive as they process real-time vision, perform inference, and plan motion. |
| D14 | `D14-PO-000068` Micron fiscal Q3 2026 earnings results disclosure<br><sub>`atomic:1aa613473954efa0a715b65c` Micron's revenues increased by 346% compared to the same period a year earlier.<br>`atomic:604e5c5b2fe498bfd16e5d54` Micron Technology reported stellar earnings after the market close on June 24, 2026.<br>`atomic:c158b302322f818a88bd6a75` Micron's customers committed $22 billion to secure supplies of its chips.</sub> | DISCLOSED_IN | 3 | — |
| D15 | `D15-PO-000037` Micron Q4 2026 guidance and forward outlook<br><sub>`atomic:1224baec04a5ce7a3bb4cc2c` Unit sales in the key data center market are expected to grow by the high teens.<br>`atomic:3f5925a23f8d967d0cf991e1` Micron's profit growth is expected to slow as its gross margin plateaus.<br>`atomic:77a1bd0b74f5e5756fb52040` Micron expects a run rate profit of $160 billion.<br>`atomic:a931b428e3798231b98bec0b` Micron expects revenue of approximately $50 billion in the current quarter.<br>`atomic:dc60f8df8e3f888c62529aaa` Micron expects fourth-quarter net income to exceed $40 billion.<br>`atomic:e40c57a90785a34bfaf8838e` Management guided that gross margin will be 86% for the next quarter.</sub> | DISCLOSED_IN | 6 | `atomic:3f5925a23f8d967d0cf991e1` Micron's profit growth is expected to slow as its gross margin plateaus.<br>`atomic:77a1bd0b74f5e5756fb52040` Micron expects a run rate profit of $160 billion. |
| D15 | `D15-PO-000067` Micron fiscal Q3 2026 earnings report<br><sub>`atomic:0d6b0ee15ce2415df25d911f` Wednesday's earnings report was virtually flawless.<br>`atomic:604e5c5b2fe498bfd16e5d54` Micron Technology reported stellar earnings after the market close on June 24, 2026.<br>`atomic:88d5d359fc6743af962e57fc` Micron reported GAAP gross margin of 84.6%, compared to 37.7% a year earlier.<br>`atomic:9ed5e0d509d2acaed8b44a93` Micron is being disciplined with its spending.<br>`atomic:a0678356c6cc3b7041e65619` Micron's fiscal Q3 revenue increased to about $41.5 billion from $23.9 billion in fiscal Q2 and $9.3 billion in the year-ago quarter, a 346% year-over-year jump.<br>`atomic:ae6c60fbd9c9b56512d2571a` Micron's operating margin for the third quarter was 80.4%.</sub> | DISCLOSED_IN | 6 | `atomic:0d6b0ee15ce2415df25d911f` Wednesday's earnings report was virtually flawless.<br>`atomic:9ed5e0d509d2acaed8b44a93` Micron is being disciplined with its spending. |
| D15 | `D15-PO-000114` Micron's pricing and cost structure<br><sub>`atomic:93e2332f4288eaee212851d8` Micron is selling its chips for approximately six times their direct costs.<br>`atomic:aed2d0049bfdc23223147123` Unit volumes are falling in the PC and smartphone market.</sub> | COMPONENT_OF | 2 | `atomic:93e2332f4288eaee212851d8` Micron is selling its chips for approximately six times their direct costs.<br>`atomic:aed2d0049bfdc23223147123` Unit volumes are falling in the PC and smartphone market. |
| D16 | `D16-PO-000017` Micron fiscal Q3 2026 earnings announcement<br><sub>`atomic:046caf1751c4a8ac5882423f` Micron raised its 2026 capital expenditure forecast and signalled meaningfully higher spending in 2027.<br>`atomic:4413a4faa173ab593c42fb74` Micron guided its Q4 gross margin to around 86% and revenue to approximately $50 billion, plus or minus $1.0 billion.<br>`atomic:81eb83ebece3711082938a23` Micron's data center revenue reached an annualized run rate of approximately $100 billion.<br>`atomic:a0678356c6cc3b7041e65619` Micron's fiscal Q3 revenue increased to about $41.5 billion from $23.9 billion in fiscal Q2 and $9.3 billion in the year-ago quarter, a 346% year-over-year jump.<br>`atomic:a931b428e3798231b98bec0b` Micron expects revenue of approximately $50 billion in the current quarter.<br>`atomic:ccd6361cee2dc431d15f2eb9` Micron topped Wall Street's estimates, posted record revenue, and delivered a record gross margin of 84.9%.<br>`atomic:e8ecbd7b42e76d79d0528276` Micron expects SCAs to eventually cover at least half of total company revenue, generating roughly $100 billion in remaining performance obligations.</sub> | DISCLOSED_IN | 7 | — |
| D17 | `D17-PO-000055` Micron fiscal Q3 2026 earnings report<br><sub>`atomic:a0678356c6cc3b7041e65619` Micron's fiscal Q3 revenue increased to about $41.5 billion from $23.9 billion in fiscal Q2 and $9.3 billion in the year-ago quarter, a 346% year-over-year jump.<br>`atomic:eb57c99e75d9e01942a376f9` Wall Street had expected Micron to earn $20.78 per share on $35.8 billion in quarterly sales.<br>`atomic:f658757304bd04719f0e200d` Micron reported GAAP profits up 104% sequentially.<br>`atomic:f9b75e07919353d91955d92d` Micron Technology's adjusted earnings per share for fiscal Q3 2026 was $25.11.</sub> | DISCLOSED_IN | 4 | — |
| D19 | `D19-PO-000082` Micron reports fiscal Q3 2026 revenue of $41.5 billion<br><sub>`atomic:a0678356c6cc3b7041e65619` Micron's fiscal Q3 revenue increased to about $41.5 billion from $23.9 billion in fiscal Q2 and $9.3 billion in the year-ago quarter, a 346% year-over-year jump.</sub> | DISCLOSED_IN | 1 | — |
| D22 | `D22-PO-000005` Micron fiscal Q3 2026 earnings report and guidance<br><sub>`atomic:604e5c5b2fe498bfd16e5d54` Micron Technology reported stellar earnings after the market close on June 24, 2026.<br>`atomic:9deef63d8a189ae1b13c5d84` Micron anticipates fourth-quarter adjusted earnings of $31 per share, with a tolerance of plus or minus $1.<br>`atomic:a0678356c6cc3b7041e65619` Micron's fiscal Q3 revenue increased to about $41.5 billion from $23.9 billion in fiscal Q2 and $9.3 billion in the year-ago quarter, a 346% year-over-year jump.<br>`atomic:a931b428e3798231b98bec0b` Micron expects revenue of approximately $50 billion in the current quarter.</sub> | DISCLOSED_IN | 4 | — |
| D23 | `D23-PO-000059` Micron fiscal Q3 2026 earnings report<br><sub>`atomic:604e5c5b2fe498bfd16e5d54` Micron Technology reported stellar earnings after the market close on June 24, 2026.<br>`atomic:a0678356c6cc3b7041e65619` Micron's fiscal Q3 revenue increased to about $41.5 billion from $23.9 billion in fiscal Q2 and $9.3 billion in the year-ago quarter, a 346% year-over-year jump.<br>`atomic:dc69ef5117ca405a587c4bc0` The earnings report demonstrated strong demand, continued pricing power, and extended visibility, factors not fully reflected in market expectations.</sub> | DISCLOSED_IN | 3 | `atomic:dc69ef5117ca405a587c4bc0` The earnings report demonstrated strong demand, continued pricing power, and extended visibility, factors not fully reflected in market expectations. |
| D24 | `D24-PO-000007` Micron fiscal Q3 2026 earnings call disclosures<br><sub>`atomic:3178ee4d61d3b167e0ef292a` Micron expects tight memory conditions to persist beyond calendar 2027.<br>`atomic:3dfaa8ae7f2630cf5133968d` Micron currently does not have line of sight as to when memory supply will be able to catch up with increasing demand.<br>`atomic:5832820424ed8958765aca28` Micron still sees no clear end to tightening memory markets.</sub> | DISCLOSED_IN | 3 | — |
| D26 | `D26-PO-000081` Micron fiscal Q3 2026 earnings disclosure<br><sub>`atomic:4413a4faa173ab593c42fb74` Micron guided its Q4 gross margin to around 86% and revenue to approximately $50 billion, plus or minus $1.0 billion.<br>`atomic:ccd6361cee2dc431d15f2eb9` Micron topped Wall Street's estimates, posted record revenue, and delivered a record gross margin of 84.9%.</sub> | DISCLOSED_IN | 2 | — |
| D27 | `D27-PO-000101` Micron Q3 FY2026 earnings disclosure<br><sub>`atomic:1aa613473954efa0a715b65c` Micron's revenues increased by 346% compared to the same period a year earlier.<br>`atomic:604e5c5b2fe498bfd16e5d54` Micron Technology reported stellar earnings after the market close on June 24, 2026.<br>`atomic:d6a6de0c9d6d2545e18224f4` Micron achieved a record adjusted gross margin of about 85%.</sub> | DISCLOSED_IN | 3 | — |
| D28 | `D28-PO-000043` Micron fiscal Q4 2026 guidance<br><sub>`atomic:98cb55dfecc7faa6eb649249` Micron expects tight supply conditions to persist beyond its 2027 financial year, with gross margin guided to about 86%.<br>`atomic:9deef63d8a189ae1b13c5d84` Micron anticipates fourth-quarter adjusted earnings of $31 per share, with a tolerance of plus or minus $1.<br>`atomic:a931b428e3798231b98bec0b` Micron expects revenue of approximately $50 billion in the current quarter.</sub> | DISCLOSED_IN | 3 | — |
| D28 | `D28-PO-000083` Micron fiscal Q3 2026 earnings report<br><sub>`atomic:4328348aecd64ff173851437` Micron's cloud memory business unit's revenue grew from $3.39 billion a year ago to $13.77 billion.<br>`atomic:48a03e53890011d7f4de3a23` Micron's capital expenditures climbed to $7.1 billion in fiscal Q3.<br>`atomic:877526c1eef5b84e31e4ba5e` Micron's core data center unit's revenue grew more than sevenfold year over year.<br>`atomic:951c337cac4a88190283f034` Micron's cloud memory business unit achieved an operating margin of 78%.<br>`atomic:a0678356c6cc3b7041e65619` Micron's fiscal Q3 revenue increased to about $41.5 billion from $23.9 billion in fiscal Q2 and $9.3 billion in the year-ago quarter, a 346% year-over-year jump.<br>`atomic:ae9ba42e3afb83957a65b330` All four of Micron's business units posted higher revenue than both the prior quarter and the year-ago period.<br>`atomic:ccd6361cee2dc431d15f2eb9` Micron topped Wall Street's estimates, posted record revenue, and delivered a record gross margin of 84.9%.<br>`atomic:e680bc0bb817b33e129bdf54` Micron's non-GAAP adjusted earnings per share hit $25.11.</sub> | DISCLOSED_IN | 8 | — |
| D29 | `D29-PO-000001` Micron Q4 2026 revenue guidance<br><sub>`atomic:a931b428e3798231b98bec0b` Micron expects revenue of approximately $50 billion in the current quarter.</sub> | DISCLOSED_IN | 1 | — |
| D29 | `D29-PO-000069` Micron fiscal Q3 2026 earnings results<br><sub>`atomic:14f64260a98ac2852f5a45c1` Micron's earnings have risen 1,368% year-over-year.<br>`atomic:a0678356c6cc3b7041e65619` Micron's fiscal Q3 revenue increased to about $41.5 billion from $23.9 billion in fiscal Q2 and $9.3 billion in the year-ago quarter, a 346% year-over-year jump.<br>`atomic:f5f8a8cadcb6272eb04a94c5` Micron reported third-quarter earnings per share of $25.11, exceeding Wall Street consensus estimates by $4.72.</sub> | DISCLOSED_IN | 3 | — |

## MCP-000002 — Market reaction to Micron Q3 FY2026 earnings on June 25, 2026

Package ID: `package-v3:9eabf94cfa9614a5abe7507e92406244`  
Parent occurrence count: 21; Atomic coverage: 25

压缩后的描述：Market reaction to Micron Q3 FY2026 earnings on June 24-25, 2026: Micron stock rose pre-market, surged to all-time high after results, reached a market-capitalization milestone, and drew after-hours sympathy rally in memory sector stocks; SK Hynix, Sandisk, KOSPI, and chip/memory trade rallied; HBM demand theme; concurrent Apple price hikes on June 25.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D03 | `D03-PO-000109` HBM demand surge and memory trade theme<br><sub>`atomic:748e546725a7a5e8eed48560` Demand for high-bandwidth memory (HBM) chips used in AI servers continued to surge.<br>`atomic:82a62acc013df4de249bf666` Micron's results have strengthened the broader 'memory trade' theme.</sub> | DISCLOSED_IN | 2 | `atomic:748e546725a7a5e8eed48560` Demand for high-bandwidth memory (HBM) chips used in AI servers continued to surge.<br>`atomic:82a62acc013df4de249bf666` Micron's results have strengthened the broader 'memory trade' theme. |
| D06 | `D06-PO-000018` Micron stock market movement on June 25, 2026<br><sub>`atomic:0970ca1a4cecea51ce0db1b3` Micron stock rose sharply on June 25.<br>`atomic:bda93b3149262384e06a9227` Micron is trading at a forward price-earnings multiple of less than 18x.<br>`atomic:e3c54b9c19fdcc4225c96240` Micron's relative strength index (RSI) reached the mid-60s.</sub> | COMPONENT_OF | 3 | `atomic:bda93b3149262384e06a9227` Micron is trading at a forward price-earnings multiple of less than 18x.<br>`atomic:e3c54b9c19fdcc4225c96240` Micron's relative strength index (RSI) reached the mid-60s. |
| D07 | `D07-PO-000065` Memory and chip trade rally<br><sub>`atomic:4be79c2f09a6470a6cec4439` The memory and chip trade remained intact and was still in the early stages of playing out, with the artificial intelligence boom only in its third inning.</sub> | COMPONENT_OF | 1 | `atomic:4be79c2f09a6470a6cec4439` The memory and chip trade remained intact and was still in the early stages of playing out, with the artificial intelligence boom only in its third inning. |
| D08 | `D08-PO-000057` Market reaction to Micron earnings on June 24-25, 2026<br><sub>`atomic:343fe0201773c008ef07f309` Micron's stock price surged to new highs following strong earnings.<br>`atomic:b5cd9bab34cb9cf1af0c400f` Micron's stock climbed about 700% over the past year.</sub> | COMPONENT_OF | 2 | — |
| D09 | `D09-PO-000050` Micron stock price movement after Q3 results<br><sub>`atomic:b5cd9bab34cb9cf1af0c400f` Micron's stock climbed about 700% over the past year.</sub> | DISCLOSED_IN | 1 | `atomic:b5cd9bab34cb9cf1af0c400f` Micron's stock climbed about 700% over the past year. |
| D11 | `D11-PO-000021` Micron stock price rise after Q3 results<br><sub>`atomic:c0c214b9f799ff50efef0cc6` Micron's stock rose 16%.</sub> | DISCLOSED_IN | 1 | — |
| D11 | `D11-PO-000080` Micron market capitalization milestone<br><sub>`atomic:0428e49107657b8566a85975` Micron's market capitalization is above $1.2 trillion.</sub> | DISCLOSED_IN | 1 | — |
| D12 | `D12-PO-000014` KOSPI surge on June 25 2026<br><sub>`atomic:51c3d38fee4c34d8c0b2787e` The KOSPI surged more than 5% at the open on June 25, crossing above 8,900 from 8,400 the prior session.</sub> | COMPONENT_OF | 1 | `atomic:51c3d38fee4c34d8c0b2787e` The KOSPI surged more than 5% at the open on June 25, crossing above 8,900 from 8,400 the prior session. |
| D12 | `D12-PO-000063` Micron earnings surprise market reaction<br><sub>`atomic:589980b90ee5d9c9c43fffb4` Micron's earnings surprise and the more than 5% strength in the KOSPI200 night futures caused the KOSPI to surge at the open.<br>`atomic:b5cd9bab34cb9cf1af0c400f` Micron's stock climbed about 700% over the past year.</sub> | COMPONENT_OF | 2 | `atomic:589980b90ee5d9c9c43fffb4` Micron's earnings surprise and the more than 5% strength in the KOSPI200 night futures caused the KOSPI to surge at the open.<br>`atomic:b5cd9bab34cb9cf1af0c400f` Micron's stock climbed about 700% over the past year. |
| D14 | `D14-PO-000008` Micron stock pre-market rise on June 25, 2026<br><sub>`atomic:74c597b2a6aec23d003e7b94` Micron Technology's stock moved up by more than 16% in pre-market trade on Thursday.</sub> | COMPONENT_OF | 1 | — |
| D14 | `D14-PO-000052` SK Hynix stock surge on June 25, 2026<br><sub>`atomic:0997ed6ed456e69b8057cc61` SK Hynix's stock moved up by 13% on Thursday after the company disclosed plans for a listing on the US Nasdaq.</sub> | DISCLOSED_IN | 1 | `atomic:0997ed6ed456e69b8057cc61` SK Hynix's stock moved up by 13% on Thursday after the company disclosed plans for a listing on the US Nasdaq. |
| D15 | `D15-PO-000089` Market reaction to Micron earnings on June 24-25, 2026<br><sub>`atomic:288e3d5d42f71f5b92d0b247` Micron's stock has jumped nearly 1,000% over the year.<br>`atomic:68972f237a377278a0089c8d` Micron's stock price rose 15% after hours on Wednesday following its third-quarter earnings report.<br>`atomic:6ae054ed0257938c152ca1d4` Micron's stock price soared on Thursday following strong earnings.</sub> | COMPONENT_OF | 3 | `atomic:288e3d5d42f71f5b92d0b247` Micron's stock has jumped nearly 1,000% over the year. |
| D16 | `D16-PO-000092` Micron stock surge to all-time high on June 25, 2026<br><sub>No projected Atomic after unique-owner resolution</sub> | — | 0 | — |
| D17 | `D17-PO-000034` Market reaction to Micron earnings affecting Sandisk stock<br><sub>`atomic:f6d84d8945bcdb8e45b72696` Investors think Sandisk can achieve profitability growth similar to Micron's.</sub> | COMPONENT_OF | 1 | `atomic:f6d84d8945bcdb8e45b72696` Investors think Sandisk can achieve profitability growth similar to Micron's. |
| D18 | `D18-PO-000062` Micron stock surge to new highs after earnings<br><sub>`atomic:343fe0201773c008ef07f309` Micron's stock price surged to new highs following strong earnings.</sub> | COMPONENT_OF | 1 | — |
| D21 | `D21-PO-000058` After-hours sympathy rally in memory sector stocks following Micron earnings<br><sub>`atomic:029721fe42fc328a94974790` Micron's strong results triggered a sympathy rally in WDC, SNDK and STX stocks.</sub> | DISCLOSED_IN | 1 | `atomic:029721fe42fc328a94974790` Micron's strong results triggered a sympathy rally in WDC, SNDK and STX stocks. |
| D22 | `D22-PO-000030` Micron stock price surge on June 25, 2026<br><sub>`atomic:0970ca1a4cecea51ce0db1b3` Micron stock rose sharply on June 25.</sub> | DISCLOSED_IN | 1 | — |
| D23 | `D23-PO-000039` Market reaction to Micron earnings<br><sub>`atomic:27d1ba6bdd90623d327cf16f` Micron has been a key driver of both the memory trade and the semiconductor sector.<br>`atomic:37aec389b512fde6dc512c19` Micron's earnings beat could help fuel another leg higher for AI-related equities and the broader technology market.</sub> | DISCLOSED_IN | 2 | `atomic:27d1ba6bdd90623d327cf16f` Micron has been a key driver of both the memory trade and the semiconductor sector.<br>`atomic:37aec389b512fde6dc512c19` Micron's earnings beat could help fuel another leg higher for AI-related equities and the broader technology market. |
| D26 | `D26-PO-000004` Market reaction to Micron earnings and Apple price hikes on June 25 2026<br><sub>`atomic:13d2537f731bc8b6f9764c8e` Micron's market value increased by more than $100 billion on Thursday.<br>`atomic:1bfee30266e08eb99541e7a9` Apple's stock fell over 5% after raising prices on some Macs and iPads.<br>`atomic:6ae054ed0257938c152ca1d4` Micron's stock price soared on Thursday following strong earnings.</sub> | COMPONENT_OF | 3 | `atomic:1bfee30266e08eb99541e7a9` Apple's stock fell over 5% after raising prices on some Macs and iPads. |
| D27 | `D27-PO-000115` Micron stock reaction on June 25, 2026<br><sub>`atomic:55e0414bc5632e7ed1171fb6` Micron stock soared nearly 16% following blockbuster earnings.</sub> | DISCLOSED_IN | 1 | — |
| D28 | `D28-PO-000036` Market movement in Micron shares after earnings<br><sub>`atomic:0428e49107657b8566a85975` Micron's market capitalization is above $1.2 trillion.<br>`atomic:f21ed9469e3eec78ca2e7b0d` Micron's shares jumped about 16% in after-hours trading on Wednesday, climbing from about $1,049 at Wednesday's close to about $1,215.</sub> | DISCLOSED_IN | 2 | — |

## MCP-000003 — Micron Strategic Customer Agreements announcement

Package ID: `package-v3:0646d90d819034f1ef04707e9ee35fad`  
Parent occurrence count: 12; Atomic coverage: 15

压缩后的描述：Micron announced 16 multi-year Strategic Customer Agreements (SCAs) with $22 billion in customer commitments for memory chip supply, extending through 2030 and tied to HBM production ramp.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D05 | `D05-PO-000099` Micron's strategic customer agreements portfolio<br><sub>`atomic:dcdfd9f419c9355c37378acb` Micron completed 16 Strategic Customer Agreements spanning data center, consumer and automotive markets.</sub> | COMPONENT_OF | 1 | — |
| D06 | `D06-PO-000095` Micron Strategic Customer Agreements (SCAs)<br><sub>`atomic:82f5d32608dc8ac2559a39fb` The multi-year take-or-pay deals lock in approximately $22 billion in cash deposits and commitments.<br>`atomic:9c1a7f14c60cc924420e9b2a` Micron secured 16 long-term Strategic Customer Agreements that sold out its 2026 manufacturing capacity.<br>`atomic:b0535d2c60e48ec50ccb2334` Micron highlighted huge growth in its five-year take-or-pay deals.</sub> | COMPONENT_OF | 3 | — |
| D07 | `D07-PO-000025` Micron's 16 strategic customer agreements<br><sub>`atomic:426490ddd8fce547c24476cd` Micron signed 16 strategic customer agreements, 14 of which carry cumulative revenue of at least $100 billion over the term of the deals.</sub> | COMPONENT_OF | 1 | — |
| D09 | `D09-PO-000044` Micron 16 Strategic Customer Agreements announcement<br><sub>`atomic:dcdfd9f419c9355c37378acb` Micron completed 16 Strategic Customer Agreements spanning data center, consumer and automotive markets.</sub> | DISCLOSED_IN | 1 | — |
| D10 | `D10-PO-000085` Micron's 16 customer agreements through 2030<br><sub>`atomic:414a09767530f2fe59b85e40` Micron signed 16 customer agreements with data center, consumer, and automotive customers that run through 2030 and include commitments to purchase memory products.</sub> | COMPONENT_OF | 1 | — |
| D11 | `D11-PO-000106` Micron multi-year Strategic Customer Agreements<br><sub>`atomic:2c43dcb05381635c61f02a51` Micron CEO Sanjay Mehrotra stated that multi-year Strategic Customer Agreements will significantly enhance the durability and predictability of Micron's strong financial performance.<br>`atomic:6393f11f2187b9759b4d5805` Micron signed 16 long-term customer agreements, with 14 agreements providing minimum guaranteed revenue of approximately $100 billion through 2030.</sub> | DISCLOSED_IN, IMPLEMENTATION_OF | 2 | — |
| D16 | `D16-PO-000094` Micron's strategic customer agreements (SCAs)<br><sub>`atomic:03c48885f9a9c662b8a30f55` The agreements are guaranteed by cash deposits and financial commitments and do not contain provisions allowing for the termination of terms.<br>`atomic:426490ddd8fce547c24476cd` Micron signed 16 strategic customer agreements, 14 of which carry cumulative revenue of at least $100 billion over the term of the deals.<br>`atomic:45fe24ad3ec78376f7c5aa3b` The strategic customer agreements include price floors and ceilings, are backed by cash deposits and financial commitments, and carry no termination provisions.<br>`atomic:89f99471d20b1f2b9ce8d6fb` The agreements currently represent about 20% of DRAM output and one-third of NAND sales.</sub> | COMPONENT_OF | 4 | — |
| D23 | `D23-PO-000019` Micron strategic agreements and HBM production ramp<br><sub>`atomic:167f68a4f2bae70c6c51a10a` Micron's strategic agreements and HBM production ramp are providing visibility beyond a typical memory-pricing cycle.</sub> | DISCLOSED_IN | 1 | — |
| D24 | `D24-PO-000096` Micron strategic customer agreements<br><sub>`atomic:6393f11f2187b9759b4d5805` Micron signed 16 long-term customer agreements, with 14 agreements providing minimum guaranteed revenue of approximately $100 billion through 2030.</sub> | COMPONENT_OF | 1 | — |
| D28 | `D28-PO-000078` Micron Strategic Customer Agreements announcement<br><sub>`atomic:2c43dcb05381635c61f02a51` Micron CEO Sanjay Mehrotra stated that multi-year Strategic Customer Agreements will significantly enhance the durability and predictability of Micron's strong financial performance.<br>`atomic:c0e9a80033af4d94be353b4c` The 16 signed Strategic Customer Agreements represent about 20% of Micron's DRAM volume and a third of its NAND volume over the agreement period.<br>`atomic:c11072eb6924758e29ea5f0a` Micron announced multi-year Strategic Customer Agreements that lock in volume and provide pricing visibility for memory supply.</sub> | DISCLOSED_IN | 3 | — |
| D29 | `D29-PO-000028` Micron strategic customer agreements<br><sub>`atomic:426490ddd8fce547c24476cd` Micron signed 16 strategic customer agreements, 14 of which carry cumulative revenue of at least $100 billion over the term of the deals.</sub> | COMPONENT_OF | 1 | — |
| D30 | `D30-PO-000009` Micron announces $22 billion customer commitments for memory chip supply<br><sub>`atomic:967a88dd0ffccea8b6ad87d4` Micron announced that customers such as Nvidia had committed $22 billion to lock in supplies of memory chips.<br>`atomic:b0535d2c60e48ec50ccb2334` Micron highlighted huge growth in its five-year take-or-pay deals.</sub> | COMPONENT_OF, DISCLOSED_IN | 2 | — |

## MCP-000004 — Micron HBM production, sales, and supply update

Package ID: `package-v3:bfe0422e5fea84acb4a27db448a1f127`  
Parent occurrence count: 2; Atomic coverage: 5

压缩后的描述：Micron HBM sales and supply update for 2026-2028, including HBM4 production and qualification shipments.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D08 | `D08-PO-000041` Micron's high-bandwidth memory sales and supply situation for 2026-2028<br><sub>`atomic:370e5df09bd5e289679b2872` New factories are not expected to add meaningful output until 2028.<br>`atomic:be02f0cb157df9bf358b5203` Micron's entire 2026 output of high-bandwidth memory chips is sold out under fixed-price contracts.<br>`atomic:e9ebdf6b1be83e680afc56c9` Micron pointed to multi-year customer agreements expected to make earnings more durable and predictable.</sub> | COMPONENT_OF | 3 | `atomic:370e5df09bd5e289679b2872` New factories are not expected to add meaningful output until 2028.<br>`atomic:e9ebdf6b1be83e680afc56c9` Micron pointed to multi-year customer agreements expected to make earnings more durable and predictable. |
| D28 | `D28-PO-000035` HBM4 production and qualification shipments<br><sub>`atomic:6d70db11d9360fac51e083f4` HBM4 is already in high-volume shipments to Micron's lead customer.<br>`atomic:ed1968eaec558bc6940ca274` Qualification samples of HBM4 are now being sent to additional end customers beyond the lead customer.</sub> | DISCLOSED_IN | 2 | — |

## MCP-000005 — Micron-Anthropic strategic partnership announcement

Package ID: `package-v3:2a6042a6e36afee5871608673427a527`  
Parent occurrence count: 2; Atomic coverage: 3

压缩后的描述：Announcement of a strategic partnership between Micron and Anthropic.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D04 | `D04-PO-000038` Micron and Anthropic strategic partnership announcement<br><sub>`atomic:3eb7dc2926cf2dd75707486f` The agreement includes a strategic investment in Anthropic's Series H funding round.<br>`atomic:a0d7afb8b51af3612d75d16e` Micron announced a strategic partnership with Anthropic to scale next-generation AI infrastructure.</sub> | COMPONENT_OF, DISCLOSED_IN | 2 | — |
| D04 | `D04-PO-000046` Micron-Anthropic strategic partnership announcement<br><sub>`atomic:e984da92571502aac836401c` The analyst forecasts strong market fundamentals to persist, driven by continued strong demand, a robust pricing environment, and limited capacity additions.</sub> | DISCLOSED_IN | 1 | `atomic:e984da92571502aac836401c` The analyst forecasts strong market fundamentals to persist, driven by continued strong demand, a robust pricing environment, and limited capacity additions. |

## MCP-000006 — Memory market supply-demand imbalance and pricing outlook

Package ID: `package-v3:d497735460bd55780c8d4a1bf1d42d24`  
Parent occurrence count: 8; Atomic coverage: 18

压缩后的描述：Memory market supply-demand imbalance: tight supply/shortage as AI-driven demand outpaces supply, with structural transformation through 2028; memory and storage prices surged over the past three quarters; outlook tied to strategic agreements.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D01 | `D01-PO-000054` Memory and storage price surge over past three quarters<br><sub>`atomic:7cc5b62a77f0c31c2d28644a` Memory and storage prices have quadrupled in the past three quarters.<br>`atomic:f2eed34a745025314faec3ff` Suppliers are redirecting production toward high-bandwidth memory used in AI servers.</sub> | COMPONENT_OF | 2 | `atomic:7cc5b62a77f0c31c2d28644a` Memory and storage prices have quadrupled in the past three quarters.<br>`atomic:f2eed34a745025314faec3ff` Suppliers are redirecting production toward high-bandwidth memory used in AI servers. |
| D07 | `D07-PO-000102` Memory chip demand exceeding supply<br><sub>`atomic:47c1a51b1b5db5855b643688` Demand for Micron's NAND and DRAM memory chips continues to significantly exceed industry supply, indicating tight conditions and strong pricing power.</sub> | COMPONENT_OF | 1 | `atomic:47c1a51b1b5db5855b643688` Demand for Micron's NAND and DRAM memory chips continues to significantly exceed industry supply, indicating tight conditions and strong pricing power. |
| D09 | `D09-PO-000100` Memory supply and demand outlook commentary<br><sub>`atomic:333ea72f9f18edc6680b6767` Some NAND flash suppliers are reallocating cleanroom space toward DRAM production, constraining NAND supply growth.<br>`atomic:a61b7e186183b8f4e08ae063` Growing HBM adoption is putting additional pressure on conventional memory supply because advanced memory products require more manufacturing resources and capacity.<br>`atomic:ce5d0132cd25796e0dfd8320` Industry memory supply is expected to improve gradually in 2028, but there is no line of sight as to when supply will catch up with increasing demand.</sub> | DISCLOSED_IN | 3 | — |
| D15 | `D15-PO-000022` Memory market supply shortage and structural transformation through 2028<br><sub>`atomic:5340b7704d7b3d7b5fce507d` Management stated that the company is in the early innings of significant innovation and productivity improvements.<br>`atomic:ed12a63f7f13283396311cf7` Management stated that the memory industry has been structurally transformed by AI.<br>`atomic:f1658374b24368c4413a4458` Memory shortages are expected to persist at least through 2028.</sub> | COMPONENT_OF | 3 | `atomic:5340b7704d7b3d7b5fce507d` Management stated that the company is in the early innings of significant innovation and productivity improvements.<br>`atomic:ed12a63f7f13283396311cf7` Management stated that the memory industry has been structurally transformed by AI.<br>`atomic:f1658374b24368c4413a4458` Memory shortages are expected to persist at least through 2028. |
| D16 | `D16-PO-000049` Memory market supply-demand conditions<br><sub>`atomic:7a4ffd0fd19114e3e0b822b1` Demand for NAND and DRAM continues to significantly exceed industry supply.</sub> | COMPONENT_OF | 1 | `atomic:7a4ffd0fd19114e3e0b822b1` Demand for NAND and DRAM continues to significantly exceed industry supply. |
| D17 | `D17-PO-000012` Industry-wide memory supply-demand outlook with strategic agreements<br><sub>`atomic:38e205c34e4cffd9a6f2336b` Micron CEO Sanjay Mehrotra does not see supply catching up with demand anytime soon.<br>`atomic:707d13562697f729c8b3798d` Micron CEO Sanjay Mehrotra confirmed that the artificial intelligence revolution is alive and well.<br>`atomic:933412c0d00fb036a13d1bce` Micron and Sandisk are locking in long-term prices at high margins through Strategic Customer Agreements.<br>`atomic:ba731c404aeef43debf3a5d5` Micron is investing at record levels to help meet customer demand.</sub> | COMPONENT_OF | 4 | `atomic:38e205c34e4cffd9a6f2336b` Micron CEO Sanjay Mehrotra does not see supply catching up with demand anytime soon.<br>`atomic:707d13562697f729c8b3798d` Micron CEO Sanjay Mehrotra confirmed that the artificial intelligence revolution is alive and well.<br>`atomic:933412c0d00fb036a13d1bce` Micron and Sandisk are locking in long-term prices at high margins through Strategic Customer Agreements.<br>`atomic:ba731c404aeef43debf3a5d5` Micron is investing at record levels to help meet customer demand. |
| D23 | `D23-PO-000013` Micron tight memory supply conditions<br><sub>`atomic:3ee258d49e3388ba02fca492` Micron's CEO said AI memory shortage could last beyond 2028.<br>`atomic:8d48e46e3d3ecb34339aaa6f` High-bandwidth memory (HBM) remains in tight supply as hyperscalers and enterprises continue pouring money into AI infrastructure.</sub> | COMPONENT_OF, DISCLOSED_IN | 2 | `atomic:3ee258d49e3388ba02fca492` Micron's CEO said AI memory shortage could last beyond 2028.<br>`atomic:8d48e46e3d3ecb34339aaa6f` High-bandwidth memory (HBM) remains in tight supply as hyperscalers and enterprises continue pouring money into AI infrastructure. |
| D30 | `D30-PO-000023` AI-driven demand surge for memory chips outpaces supply<br><sub>`atomic:19b3755066006ec0f83286f3` AI-related demand for memory chips continues to outpace available supply.<br>`atomic:84aa0e54c66c08dc89f97636` Demand for memory chips in the artificial intelligence supply chain has surged alongside the need for greater computing power.</sub> | COMPONENT_OF | 2 | `atomic:19b3755066006ec0f83286f3` AI-related demand for memory chips continues to outpace available supply.<br>`atomic:84aa0e54c66c08dc89f97636` Demand for memory chips in the artificial intelligence supply chain has surged alongside the need for greater computing power. |

## MCP-000007 — Micron shareholder return plan and dividend declaration

Package ID: `package-v3:cfc898764d3c4e657b456504bad6df24`  
Parent occurrence count: 4; Atomic coverage: 3

压缩后的描述：Micron shareholder return commitment: plan to return excess cash to shareholders, with shareholder return plan starting December 2026, and dividend declaration.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D06 | `D06-PO-000045` Micron shareholder return commitment<br><sub>`atomic:bfb73ccc3e41e9f0956298fe` Micron management committed to returning 100% of excess cash to shareholders, up from 50% previously.</sub> | COMPONENT_OF | 1 | — |
| D07 | `D07-PO-000111` Micron's plan to return excess cash to shareholders<br><sub>`atomic:969c60a934acfcd8bfd36ca5` Micron announced plans to return 100% of excess free cash flow to shareholders beginning in December, once CHIPS Act restrictions expire.</sub> | COMPONENT_OF | 1 | — |
| D09 | `D09-PO-000105` Micron dividend declaration<br><sub>`atomic:4339c673af5e3d345eeafe93` Micron declared a quarterly dividend of 15 cents per share, payable July 21 to shareholders of record on July 6.</sub> | DISCLOSED_IN | 1 | — |
| D16 | `D16-PO-000020` Micron shareholder return plan starting December 2026<br><sub>`atomic:969c60a934acfcd8bfd36ca5` Micron announced plans to return 100% of excess free cash flow to shareholders beginning in December, once CHIPS Act restrictions expire.</sub> | IMPLEMENTATION_OF | 1 | — |

## MCP-000008 — Micron stock performance in 2026

Package ID: `package-v3:0ab6e7e28f6c53a40242d56589a19961`  
Parent occurrence count: 3; Atomic coverage: 3

压缩后的描述：Micron stock performance in 2026: year-to-date gain and share price performance over the past year and year-to-date.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D04 | `D04-PO-000090` Micron share price performance over past year and year-to-date<br><sub>`atomic:09dfb5b36b352af816b247a1` Micron's share prices increased by 233.45% year-to-date.<br>`atomic:2b94c241f50faa836678e742` Micron's share prices increased by 722.27% over the past year.</sub> | DISCLOSED_IN | 2 | — |
| D10 | `D10-PO-000073` Micron stock year-to-date gain in 2026<br><sub>`atomic:09dfb5b36b352af816b247a1` Micron's share prices increased by 233.45% year-to-date.</sub> | COMPONENT_OF | 1 | — |
| D29 | `D29-PO-000087` Micron stock market performance 2026<br><sub>`atomic:eb4e002632ee2ed2e84e9bf5` Micron stock is up over 800% in the past year.</sub> | COMPONENT_OF | 1 | — |

## MCP-000009 — SK Hynix US listing announcement

Package ID: `package-v3:62a0449f9c2a5766dfb3296f1773331f`  
Parent occurrence count: 2; Atomic coverage: 3

压缩后的描述：SK Hynix $29 billion US listing announcement (Nasdaq listing disclosure).

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D02 | `D02-PO-000015` SK Hynix $29 billion US listing announcement<br><sub>`atomic:3e5efb6aee8c6f44c890d67c` SK Hynix expects its American depositary receipts to start trading on July 10.<br>`atomic:44ef3a34e5786bc9d13f01c1` SK Hynix announced plans for a $29 billion US listing.<br>`atomic:45a30abde21fc2fd93475d7a` SK Hynix disclosed plans for a listing on the US Nasdaq.</sub> | DISCLOSED_IN, STAGE_OF | 3 | — |
| D14 | `D14-PO-000088` SK Hynix Nasdaq listing disclosure<br><sub>`atomic:45a30abde21fc2fd93475d7a` SK Hynix disclosed plans for a listing on the US Nasdaq.</sub> | COMPONENT_OF | 1 | — |

## MCP-000010 — Apple product price increases in June 2026

Package ID: `package-v3:f6c2733cac4266abfc0181bf2287a644`  
Parent occurrence count: 5; Atomic coverage: 7

压缩后的描述：Apple product price increases in June 2026: device prices raised on June 25, 2026 on MacBook and iPad lineups to offset higher memory costs; Counterpoint Research estimates $150-$200 increases; Apple stock declined after the price hike announcement.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D01 | `D01-PO-000029` Counterpoint Research estimates Apple price increases of $150-$200<br><sub>`atomic:1435a017861e6f17d31d7d1f` Counterpoint expects Apple price increases of $150-$200 across the lineup.</sub> | DISCLOSED_IN | 1 | `atomic:1435a017861e6f17d31d7d1f` Counterpoint expects Apple price increases of $150-$200 across the lineup. |
| D01 | `D01-PO-000053` Apple price hikes on MacBook and iPad lineup<br><sub>`atomic:45523798387a991b8e4bfd76` Tim Cook described the memory crisis as a hundred-year flood.<br>`atomic:455b6fd9613760665cbd491c` Apple left the door open to further price increases.<br>`atomic:5165a6b035beb06020dcab4f` Apple increased prices on MacBook Neo, MacBook Air, MacBook Pro, iPad Pro, iPad Air, HomePod, HomePod mini and Apple TV, while iPhone pricing remained unchanged.<br>`atomic:5bbd0cfac93b60009266f7d4` Apple said it has never seen a component price increase of this magnitude and speed.</sub> | COMPONENT_OF, DISCLOSED_IN | 4 | — |
| D01 | `D01-PO-000098` Apple stock decline after price hike announcement<br><sub>`atomic:ae6ce7106bdc7e4082cd3e4a` Apple fell 0.56% intraday after announcing price hikes across its MacBook and iPad lineup.</sub> | COMPONENT_OF | 1 | `atomic:ae6ce7106bdc7e4082cd3e4a` Apple fell 0.56% intraday after announcing price hikes across its MacBook and iPad lineup. |
| D24 | `D24-PO-000084` Apple product price increases June 2026<br><sub>`atomic:5165a6b035beb06020dcab4f` Apple increased prices on MacBook Neo, MacBook Air, MacBook Pro, iPad Pro, iPad Air, HomePod, HomePod mini and Apple TV, while iPhone pricing remained unchanged.</sub> | COMPONENT_OF | 1 | — |
| D27 | `D27-PO-000091` Apple raises device prices on June 25, 2026 to offset higher memory costs<br><sub>`atomic:5165a6b035beb06020dcab4f` Apple increased prices on MacBook Neo, MacBook Air, MacBook Pro, iPad Pro, iPad Air, HomePod, HomePod mini and Apple TV, while iPhone pricing remained unchanged.<br>`atomic:e5ac74471025a7d386e226be` Apple's stock closed at $275.15, down 6.12%.</sub> | COMPONENT_OF, DISCLOSED_IN | 2 | `atomic:e5ac74471025a7d386e226be` Apple's stock closed at $275.15, down 6.12%. |

## MCP-000011 — Wedbush post-earnings analyst note on Micron

Package ID: `package-v3:514b2343123ea5973cb3eed358902f96`  
Parent occurrence count: 2; Atomic coverage: 4

压缩后的描述：Wedbush post-earnings analyst report on Micron after Q3 2026, including analyst note on AI demand.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D07 | `D07-PO-000032` Wedbush analyst report on Micron after Q3 2026 earnings<br><sub>`atomic:3e3edc45aa41fd61b7f7de65` Dan Ives said there are no cracks in AI demand on the chips/hardware or software front, giving a bright green light to own core tech winners into year-end.<br>`atomic:4b996a3c0a4a24c698225ddc` Wedbush reiterated its bullish stance on technology stocks.<br>`atomic:cdb3f8b6f4667aa51e3eb7d4` Ives flagged risks including a slowdown in enterprise IT spending, rapid technology disruption, rising competition across the AI stack, and geopolitical tensions between the United States and China that could disrupt the semiconductor supply chain.<br>`atomic:e05a0114b6a72fa1df3696cf` Wedbush rates Micron at outperform with a price target of $1,300.</sub> | DISCLOSED_IN | 4 | — |
| D29 | `D29-PO-000066` Wedbush analyst note on AI demand<br><sub>`atomic:3e3edc45aa41fd61b7f7de65` Dan Ives said there are no cracks in AI demand on the chips/hardware or software front, giving a bright green light to own core tech winners into year-end.</sub> | DISCLOSED_IN | 1 | — |

## MCP-000012 — UBS post-earnings analyst note on Micron

Package ID: `package-v3:05773e38587db06406bf98c1fad69798`  
Parent occurrence count: 2; Atomic coverage: 3

压缩后的描述：UBS post-earnings note on Micron: memory supply constraint outlook and price target increase.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D11 | `D11-PO-000079` UBS price target increase for Micron<br><sub>`atomic:57092dfc07be8e5a9e790fb9` UBS tripled Micron's price target last month.</sub> | DISCLOSED_IN | 1 | `atomic:57092dfc07be8e5a9e790fb9` UBS tripled Micron's price target last month. |
| D29 | `D29-PO-000016` UBS memory supply constraint outlook<br><sub>`atomic:14aadcf45dff399443cd976b` UBS analysts previously said NAND is likely to be constrained until at least the end of 2027.<br>`atomic:2344a4a48f27e56e08f3bdbe` UBS analysts previously said DRAM is likely to be constrained until at least halfway through 2028.</sub> | COMPONENT_OF | 2 | — |

## MCP-000013 — Roundhill launch of leveraged DRAM ETF

Package ID: `package-v3:d07091f1f104a8f7970d639dd3a8b9a2`  
Parent occurrence count: 1; Atomic coverage: 1

压缩后的描述：Roundhill launch of a leveraged fund on a DRAM ETF.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D03 | `D03-PO-000003` Roundhill launch of leveraged fund on DRAM ETF<br><sub>`atomic:949cc1597b9e5eabf0236852` Roundhill Investments launched a leveraged fund designed to deliver twice the daily performance of the Roundhill Memory ETF (NASDAQ:DRAM).</sub> | DISCLOSED_IN | 1 | — |

## MCP-000014 — Hyperscaler capital spending in 2026

Package ID: `package-v3:6f64c0ce8cea4395101ceaa0e89a3932`  
Parent occurrence count: 1; Atomic coverage: 1

压缩后的描述：Hyperscaler capital spending in 2026.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D08 | `D08-PO-000006` Hyperscaler capital spending in 2026<br><sub>`atomic:9b9e166531a1f3532c81acaf` Hyperscalers Amazon, Microsoft, Google, and Meta have collectively earmarked hundreds of billions of dollars in capital spending this year.</sub> | COMPONENT_OF | 1 | — |

## MCP-000015 — Citi post-earnings research note on Micron

Package ID: `package-v3:49f5ff5a08a5dedc4be7f8f0ad2a8f18`  
Parent occurrence count: 1; Atomic coverage: 2

压缩后的描述：Citi post-earnings research note on Micron.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D06 | `D06-PO-000011` Citi post-earnings research note on Micron<br><sub>`atomic:0de507f60c1f94d2605f108e` Citi analysts maintained a bullish stance on Micron shares.<br>`atomic:ce837275fcd8b99b4b810d64` Malik expects the SCAs to drive about 40% of Micron's revenue over the next five years.</sub> | COMPONENT_OF | 2 | — |

## MCP-000016 — Bernstein analyst note on Micron contracts

Package ID: `package-v3:b747c10eec51aa9111a62d5610f5cc80`  
Parent occurrence count: 1; Atomic coverage: 1

压缩后的描述：Bernstein analyst note on Micron contracts.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D29 | `D29-PO-000024` Bernstein analyst note on Micron contracts<br><sub>`atomic:4e9ef7f4c6f91d707269b0fc` Mark Newman wrote that the ceiling suggests limited headroom and that these contracts likely wouldn't be able to avoid cyclicality.</sub> | DISCLOSED_IN | 1 | — |

## MCP-000017 — Needham price target increase on Micron

Package ID: `package-v3:12952bec7cc73e9a157c0611fa78818e`  
Parent occurrence count: 1; Atomic coverage: 1

压缩后的描述：Needham price target increase on Micron.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D04 | `D04-PO-000047` Needham price target increase on Micron<br><sub>`atomic:b8a3b22e631ff0a3c2756583` Mizuho raised its price target on Micron Technology to $1375.</sub> | DISCLOSED_IN | 1 | `atomic:b8a3b22e631ff0a3c2756583` Mizuho raised its price target on Micron Technology to $1375. |

## MCP-000018 — Global AI stock selloff on June 23, 2026

Package ID: `package-v3:926a840672976aef6e9b7fbdfa6b084e`  
Parent occurrence count: 1; Atomic coverage: 2

压缩后的描述：Global AI stock selloff on June 23, 2026.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D14 | `D14-PO-000048` Global AI stock selloff on June 23, 2026<br><sub>`atomic:45ed38ff6925811fcfa57524` SK Hynix and Samsung tumbled more than 12% on Tuesday, dragging the rest of South Korea's stock market down with them.<br>`atomic:6b4800e76e220e37c2ef05de` Micron's stock fell 13% on Tuesday, part of a global sell off of AI and AI-adjacent companies.</sub> | COMPONENT_OF | 2 | — |

## MCP-000019 — Micron valuation analysis

Package ID: `package-v3:d2919e94f08b8f21ca6a1bbf11a75af8`  
Parent occurrence count: 1; Atomic coverage: 1

压缩后的描述：Micron valuation analysis.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D29 | `D29-PO-000051` Micron valuation analysis<br><sub>`atomic:bba6f4dad8ed77b00be5a813` Micron's valuation is close to 19 times forward earnings.</sub> | COMPONENT_OF | 1 | — |

## MCP-000020 — Defiance launch of 2X DRAM ETF (DRAL)

Package ID: `package-v3:f4933e8f65bc50f2cf6b6c927e02529b`  
Parent occurrence count: 1; Atomic coverage: 1

压缩后的描述：Defiance launch of 2X DRAM ETF (DRAL).

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D03 | `D03-PO-000056` Defiance launch of 2X DRAM ETF (DRAL)<br><sub>`atomic:b81a3559e8cdf904c06584af` Defiance launched a 2X DRAM ETF (DRAL).</sub> | DISCLOSED_IN | 1 | — |

## MCP-000021 — Jim Lebenthal buys Micron stock

Package ID: `package-v3:8387aa5513956795e4df288b3a55ab3b`  
Parent occurrence count: 1; Atomic coverage: 1

压缩后的描述：Jim Lebenthal buys Micron stock.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D18 | `D18-PO-000060` Jim Lebenthal buys Micron stock<br><sub>`atomic:47bd78e9c0dc8ea362f41f97` Jim Lebenthal bought Micron stock at the current price level.</sub> | COMPONENT_OF | 1 | — |

## MCP-000022 — IDC forecast of Apple iPhone RAM upgrades and ASP increase for 2026

Package ID: `package-v3:001f2eee87cc62d7db293b107168e208`  
Parent occurrence count: 1; Atomic coverage: 2

压缩后的描述：IDC forecast of Apple iPhone RAM upgrades and ASP increase for 2026.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D01 | `D01-PO-000064` IDC forecasts Apple iPhone RAM upgrades and ASP increase for 2026<br><sub>`atomic:14121b9c14b51a2bae5ec7fd` IDC expects all new iPhone models to move to 12GB of RAM.<br>`atomic:a30cb4c3faebd5a01e9281a3` IDC sees Apple's average selling price rising 12% this year.</sub> | COMPONENT_OF, DISCLOSED_IN | 2 | — |

## MCP-000023 — Sandisk upcoming earnings expectations for August 24, 2026

Package ID: `package-v3:c9c85a832a0a082cb4537b6ee7c52706`  
Parent occurrence count: 1; Atomic coverage: 1

压缩后的描述：Sandisk upcoming earnings on August 24, 2026 and expectations.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D17 | `D17-PO-000070` Sandisk upcoming earnings on Aug 24, 2026 and expectations<br><sub>`atomic:4b7ca22a1f166b6107bc8466` Analysts expect Sandisk's earnings to more than double sequentially to $33.72 per share.</sub> | COMPONENT_OF | 1 | — |

## MCP-000024 — Apple iPhone Siri compatibility limitation for devices shipped since 2022

Package ID: `package-v3:df82b6c7dab8559cc053f3f98d381409`  
Parent occurrence count: 1; Atomic coverage: 1

压缩后的描述：Apple iPhone Siri compatibility limitation for devices shipped since 2022.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D01 | `D01-PO-000074` Apple iPhone Siri compatibility limitation for devices shipped since 2022<br><sub>`atomic:0c478a99d100b1274171e8f5` Roughly 54% of iPhones shipped since 2022 will not support the full new Siri experience.</sub> | COMPONENT_OF | 1 | — |

## MCP-000025 — AI adoption acceleration and enterprise use case launch

Package ID: `package-v3:30e67a7ef3e0fa1b64ffdf2aabd35b84`  
Parent occurrence count: 1; Atomic coverage: 1

压缩后的描述：AI adoption acceleration and enterprise use case launch.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D07 | `D07-PO-000076` AI adoption acceleration and enterprise use case launch<br><sub>`atomic:0f8226785afd2208a708440f` The pace of AI adoption is accelerating and the focus is shifting to launching enterprise use cases in the second half of 2026.</sub> | COMPONENT_OF | 1 | — |

## MCP-000026 — Tesla Optimus positioning as long-term growth opportunity

Package ID: `package-v3:1fa801b68c47100138ea64315fbc3ec3`  
Parent occurrence count: 1; Atomic coverage: 1

压缩后的描述：Tesla Optimus positioning as long-term growth opportunity.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D13 | `D13-PO-000077` Tesla Optimus positioning as long-term growth opportunity<br><sub>`atomic:6689d96818c0e261fea5ede9` Tesla continues to position Optimus as one of its biggest long-term growth opportunities.</sub> | COMPONENT_OF | 1 | — |

## MCP-000027 — Mulberry comments on memory chip importance and valuation

Package ID: `package-v3:4eee63f2a9cea28668f7a4a03b73431e`  
Parent occurrence count: 1; Atomic coverage: 1

压缩后的描述：Mulberry comments on memory chip importance and valuation.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D30 | `D30-PO-000086` Mulberry comments on memory chip importance and valuation<br><sub>`atomic:6d9a727067e38b4aea6db44a` Mulberry stated that memory chips, specifically DRAM or H-band memory, are necessary for sufficient computing power and for transferring data between locations.</sub> | DISCLOSED_IN | 1 | — |

## MCP-000028 — Qualcomm Q2 handset revenue decline

Package ID: `package-v3:c5352423e78460c4a0c0e212900ba0b7`  
Parent occurrence count: 1; Atomic coverage: 1

压缩后的描述：Qualcomm Q2 handset revenue decline.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D11 | `D11-PO-000093` Qualcomm Q2 handset revenue decline<br><sub>`atomic:eb39e6fbedc14be540eeef4e` Qualcomm's Q2 handset revenue fell 13% year-over-year to $6.024 billion, with memory supply constraints among Chinese OEMs cited as the cause.</sub> | DISCLOSED_IN | 1 | — |

## MCP-000029 — Citi price target hike for Sandisk

Package ID: `package-v3:221999aad8950a4d8d73271dfa10a4a6`  
Parent occurrence count: 1; Atomic coverage: 3

压缩后的描述：Citi price target hike for Sandisk in June 2026.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D25 | `D25-PO-000097` Citi price target hike for Sandisk June 2026<br><sub>`atomic:b65bde8a3951452a197fdcc3` AI-related data center spending is helping drive storage needs for Sandisk.<br>`atomic:c66a9aaaa34c2dcce27dfcac` The pricing backdrop for Sandisk is improving as more data center operators use less costly solid-state drives to move AI workload data.<br>`atomic:eb6ee20ae46c342c09ffe7b9` Sandisk remains supported by healthier NAND supply and demand conditions.</sub> | COMPONENT_OF | 3 | — |

## MCP-000031 — Commentary on strategic value of memory in AI era

Package ID: `package-v3:5c4871849c693e45a4f07e656f85976b`  
Parent occurrence count: 1; Atomic coverage: 1

压缩后的描述：Commentary on strategic value of memory in the AI era.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D08 | `D08-PO-000104` Commentary on strategic value of memory in AI era<br><sub>`atomic:36eb58b06b0d5ea186bab628` The results reflect the strategic value of memory in the AI era.</sub> | COMPONENT_OF | 1 | — |

## MCP-000032 — Benzinga Edge stock ranking for Micron

Package ID: `package-v3:ccef48aa4ec764175f57dfff30960682`  
Parent occurrence count: 1; Atomic coverage: 1

压缩后的描述：Benzinga Edge stock ranking for Micron.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D21 | `D21-PO-000107` Benzinga Edge stock ranking for Micron<br><sub>`atomic:4b5a5d08ce902f465caf2a53` MU has a positive price trend across all time frames, with momentum in the 99th percentile.</sub> | DISCLOSED_IN | 1 | — |

## MCP-000033 — Analyst actions on Micron after Q3 earnings

Package ID: `package-v3:3ff50c2fcc9a93b53db9969cb3be0694`  
Parent occurrence count: 1; Atomic coverage: 2

压缩后的描述：Analyst actions on Micron after Q3 earnings.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D16 | `D16-PO-000108` Analyst actions on Micron after Q3 earnings<br><sub>`atomic:99b697e067d1b73c05d33990` Wedbush maintained its bullish stance on Micron.<br>`atomic:c6f1ab727eb6a7d771417806` Bank of America reiterated its Buy rating and lifted its price target to $1,550 from $1,500.</sub> | DISCLOSED_IN | 2 | `atomic:99b697e067d1b73c05d33990` Wedbush maintained its bullish stance on Micron.<br>`atomic:c6f1ab727eb6a7d771417806` Bank of America reiterated its Buy rating and lifted its price target to $1,550 from $1,500. |

## MCP-000034 — Qualcomm data center expansion and capacity securing

Package ID: `package-v3:4d85d31e8a6e54c6aadad29c283dcd77`  
Parent occurrence count: 1; Atomic coverage: 3

压缩后的描述：Qualcomm data center expansion and capacity securing.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D11 | `D11-PO-000112` Qualcomm data center expansion and capacity securing<br><sub>`atomic:92be29ead7b590f9e7d41b29` Qualcomm CEO Cristiano Amon stated that Qualcomm has secured capacity from the manufacturer and memory, and expressed confidence in the provided forecast.<br>`atomic:a11af80918fb5cedca155216` Qualcomm raised its non-handset revenue target to $40 billion by 2029, nearly double its prior forecast, with roughly $15 billion from data center.<br>`atomic:a7cf0f6d26f48de8efe5fa00` Qualcomm named META as its first customer for its new data center CPU and signed two hyperscale deals for custom chips, one in the United States and one in China.</sub> | DISCLOSED_IN, IMPLEMENTATION_OF | 3 | — |

## MCP-000035 — Wall Street consensus rating and price targets for Micron

Package ID: `package-v3:d2cf9af37d3097ad37516b6e89d1cd55`  
Parent occurrence count: 1; Atomic coverage: 3

压缩后的描述：Wall Street consensus rating and price targets for Micron.

| Source | Parent occurrence | Relation | Atomic count | Erroneously aggregated Atomic(s) |
| --- | --- | --- | ---: | --- |
| D06 | `D06-PO-000113` Wall Street consensus rating and price targets for Micron<br><sub>`atomic:8cb6aa94dc732c4f46dda1ad` Wall Street price targets for MU go as high as $1,750.<br>`atomic:9c07e94febe4f393a5bbcf87` The article claims potential upside of another 45% from current levels for MU.<br>`atomic:ca8dfbe7e9ec1a74a889b6cb` The consensus rating on MU stock is Strong Buy.</sub> | COMPONENT_OF | 3 | — |


## Independent Agent audit overlay

- 34个Package中8个在宽人工口径下明确误合；另有4个Package父边界可接受但内部Atomic已复合污染。
- Singleton宽人工口径有5个潜在重组目标；严格口径为1个明确漏合、1个review、1个错误owner，其余保持singleton。
- 潜在重组：`MCP-000025 → MCP-000011`；`MCP-000031 → MCP-000001`；将MCP-000006中的Mulberry成员移至MCP-000027；将MCP-000002中的估值Atomic移至MCP-000019；将MCP-000001中的Optimus/physical-AI成员移至MCP-000026。
- Micron earnings为5个Gold组件；market-reaction与generic-memory supercluster仍使质量Gate失败。