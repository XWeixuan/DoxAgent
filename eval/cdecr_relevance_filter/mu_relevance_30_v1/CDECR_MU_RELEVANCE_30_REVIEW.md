# CDECR MU 相关性前置滤除 30 篇测试集人工审核报告

> 数据集版本：`cdecr-mu-relevance-30-v1-20260810`  
> 目标：`US / MU / Micron Technology`  
> 口径：以 event mention 及其 evidence 语句/段落为判断单位；文章类别仅描述样本构成，不代替 mention 级 Gold。  
> 数据边界：只包含冻结新闻和生产 EventMention；不包含 Atomic、Package 或其他归一化结果。

## 验收汇总

- 文档：30；唯一 source_row/fingerprint：30/30
- Mentions：354；标签分布：`{"DIRECT": 81, "INDIRECT_ECONOMIC": 83, "NONE": 190}`
- 类别：`5 / 5 / 10 / 10`，均逐篇阅读完整正文并核对生产 mention 与 evidence。
- 原始 mention payload 保持独立，人工标签仅位于同级 `relevance_gold` 字段。

## 极低或无相关度（5）

| 文档 | 标题 | Mention（Direct / Indirect / None） | 选择理由 |
| --- | --- | ---: | --- |
| D017 | Cabinet Approves ₹26,069 Crore Kamala Hydropower Project in Arunachal Pradesh | 0 / 0 / 15 | 单位缩写误召回，且包含政策、基建、融资、产能等多种强事件。 |
| D019 | Masters’ Union Launches ₹100 Cr MU Ventures Fund for Under | 0 / 0 / 6 | 公司简称碰撞，事件覆盖基金设立、投资条款与创业支持。 |
| D050 | Murdaugh latest: defense bombshells and a lawsuit? | 0 / 0 / 2 | 人物姓名字符串噪声，提供纯法律事件负例。 |
| D067 | After another dismal spring on the diamonds, what's next for Mizzou softball and baseball? | 0 / 0 / 6 | MU 校名缩写碰撞，并覆盖脏文本下的体育、人事与合同事件。 |
| D089 | Mumbai University UG 2nd Merit List 2026 Released: Admission Process, Key Dates Inside | 0 / 0 / 4 | 教育机构缩写碰撞，提供日程/录取流程类纯负例。 |

## 大部分高或极高相关度（5）

| 文档 | 标题 | Mention（Direct / Indirect / None） | 选择理由 |
| --- | --- | ---: | --- |
| D010 | Micron Technology (MU) Gets Downgraded by Analyst | 4 / 0 / 0 | 分析师行动、资本开支、证券表现均直接涉及目标公司。 |
| D062 | Micron Advances Made-in-America Memory With Manufacturing Expansion in Virginia | 2 / 0 / 0 | 生产启动与制造投资的高纯度公司特异性事件。 |
| D232 | Micron Just Broke the Mold for Artificial Intelligence (AI) and Its Stock is Soaring | 17 / 0 / 0 | 高密度、多字段、几乎全为直接相关的财报型文本。 |
| D292 | Micron Hit With Price-Fixing Lawsuit: Real Collusion or Simple Supply and Demand? | 7 / 2 / 0 | 诉讼、历史监管、合同与产能直接相关，夹有少量行业传导事实。 |
| D298 | Micron and General Motors Sign Strategic Agreement to Secure Supply and Accelerate Innovation | 7 / 0 / 0 | 明确客户关系、产品供给、技术验证与本地制造投资。 |

## 相关与不相关事件杂糅（10）

| 文档 | 标题 | Mention（Direct / Indirect / None） | 选择理由 |
| --- | --- | ---: | --- |
| D021 | 3 AI Semiconductor Stocks That Are Now Trading Below 20X Earnings | 3 / 1 / 9 | 同一推荐文中同时存在目标公司事件、间接技术信号和无关公司事件。 |
| D027 | Peace hopes, Disney earnings, dismantling Spirit Airlines and more in Morning Squawk | 2 / 0 / 20 | 典型 Morning Squawk 杂糅文本，相关与无关事件边界清晰。 |
| D072 | Had You Put $1,000 in AMD or Micron 10 Years Ago, Here’s What You’d Have Today | 7 / 2 / 4 | 目标公司直接事件与另一芯片公司的独立及互补需求事件混合。 |
| D095 | This Is My Best Artificial Intelligence (AI) Stock to Buy in June (Hint: It's Not Micron Technology) | 3 / 8 / 5 | 目标公司事件、AI 数据中心间接信号和 Ciena 独立证券事件共存。 |
| D111 | Amid the Artificial Intelligence (AI) Bonanza, Investors Might be Overlooking a Big Opportunity to Buy Netflix Stock | 1 / 0 / 15 | 一个直接目标证券事件被大量跨行业事件包围。 |
| D147 | Stocks making the biggest moves premarket: Super Micro Computer, Cracker Barrel, Nike  and more | 1 / 0 / 20 | 短篇行情清单包含单一直接相关事件和多行业无关事件。 |
| D165 | Stocks making the biggest moves premarket: SpaceX, United Airlines, Roku & more | 2 / 0 / 20 | 多资产盘前清单，直接相关 mention 占少数。 |
| D167 | Forget Micron: 1 Record-Breaking Cloud Powerhouse to Buy Hand Over Fist After the Pullback | 4 / 0 / 11 | 两家公司基本面事件交错，适合测试比较型文章的事件级过滤。 |
| D220 | The global chip rout, Cerebras earnings, the PGA Tour's next chapter and more in Morning Squawk | 1 / 0 / 15 | 宏观、政治、体育和公司事件同时出现的高异质 newsletter。 |
| D223 | Micron Reports After the Close, Home Sales After the Open | 4 / 0 / 9 | 目标公司财报事件与完全不同的房地产主题并置。 |

## 难判定的间接相关（10）

| 文档 | 标题 | Mention（Direct / Indirect / None） | 选择理由 |
| --- | --- | ---: | --- |
| D001 | What Is the Jevons Paradox and What Does It Mean for Micron and Sandisk Investors After Google's Revolutionary AI Breakthrough? | 7 / 5 / 3 | 技术替代与需求弹性的方向相反，属于高难间接相关。 |
| D025 | AMD's stock soars 16% as data center growth pushes revenue and guidance past estimates | 1 / 8 / 7 | 事件主体是 AMD，但 GPU/机架部署对 HBM/DRAM 有具体需求传导。 |
| D045 | JPMorgan Just Hiked Hewlett Packard Enterprise Price Target to $37: Memory Reversal Powers IT Hardware Rally | 1 / 2 / 7 | 下游客户利润与上游 Micron 定价/需求之间存在方向可能相反的传导。 |
| D061 | Memory Chip Maker Kioxia Is Preparing to Debut in the U.S. Kioxia Stock Could Offer a Cheaper, Better Play for Investors. | 1 / 17 / 2 | 竞争者事件是否足以更新 Micron 份额、价格或投资判断，需要逐事件判断。 |
| D097 | AI's Memory Shortage Runs Into 2028 — Goldman Stays Bullish On SanDisk, Samsung, SK Hynix | 0 / 10 / 4 | 行业事件必须通过 Micron 的产品暴露映射到收入、价格和利润率。 |
| D102 | Jensen Huang Says Nvidia and Microsoft Just Reinvented the PC. But There Might Be 1 Problem | 1 / 6 / 0 | 终端新品既可能创造客户需求，也可能因价格抑制采用，方向并不简单。 |
| D138 | The One Hyperscaler Signal That Will Make or Break ARTY in 2026 | 2 / 3 / 3 | 需区分可传导的客户预算信号与仅为 ETF/宏观背景的信息。 |
| D180 | Nvidia Supplier SK Hynix Ships Next-Generation HBM Memory Samples To Major Customers | 0 / 4 / 1 | 直接竞争者的产品资格和核心客户份额会影响 Micron 竞争地位。 |
| D190 | SK Hynix Has a Major Warning for Micron Technology Stock Investors | 3 / 12 / 0 | 竞争供给增加与行业需求增长方向相反，具有真实判定难度。 |
| D293 | Apple Is Raising Prices Due to Higher Memory Costs. Is the Stock in Trouble? | 0 / 3 / 2 | 明确客户成本和需求弹性路径，影响方向具有不确定性。 |

## 使用说明

- `cdecr_mu_relevance_30.jsonl` 每行一篇文章，含原始 `source_message`、按生产 ordinal 排序的 `mentions` 及 mention 级 `relevance_gold`。
- `is_relevant=true` 当且仅当 `relevance_type` 为 `DIRECT` 或 `INDIRECT_ECONOMIC`。
- `INDIRECT_ECONOMIC` 的 `rationale` 给出目标公司特异性的传导路径；不能仅凭同属行业或出现在列表中判为相关。
- 若未来节点在 Dreamer 前运行，应把这里的 mention/evidence Gold 映射回承载语句；若在 Judge 后运行，可直接按 mention_id 评估。
