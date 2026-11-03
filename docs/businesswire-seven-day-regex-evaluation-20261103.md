# Business Wire 近七天 × 10 Ticker 正则筛选结果

日期：2026-11-03，北京时间。仅离线评估；没有建立订阅、写入正式消息或修改监测词。

## 1. 结果

对上轮保存的 **4,078 条** RSS 快照全量运行 10 个 Ticker 当前生产 L2：**311 条稿件至少命中一个 Ticker，合计 396 个 article×ticker 命中**。

本轮 21:18:28 重新下载同一个全站 feed，窗口已滚动为 **4,094 条**，全部解析成功，GUID+语言重复 0、解析失败 0。结果是 **316 条命中、402 个 article×ticker 命中**，条目保留率 **7.72%**。两个快照的量级接近。

这里的“条”是 RSS 稿件版本，不是已取得完整正文的消息，也不是跨语言/跨稿型合并后的独立经济事件。

| Ticker | 原 4,078 条快照命中 | 新 4,094 条快照命中 | 新快照中英文稿命中 |
|---|---:|---:|---:|
| INTC | 19 | 19 | 14 |
| MU | 22 | 21 | 9 |
| BE | 28 | 28 | 25 |
| COHR | 6 | 6 | 5 |
| NBIS | 51 | 51 | 37 |
| CEG | 171 | 177 | 144 |
| RKLB | 21 | 23 | 23 |
| SNOW | 48 | 47 | 33 |
| ONDS | 13 | 13 | 10 |
| IONQ | 17 | 17 | 10 |
| **article×ticker 合计** | **396** | **402** | **310** |
| **至少命中一个 Ticker 的稿件** | **311** | **316** | **238** |

新快照：252 条只命中 1 个 Ticker、45 条命中 2 个、16 条命中 3 个、3 条命中 4 个；3,778 条在本次可见字段中未命中。

## 2. 口径与可复现性

- 全站 URL：`https://feed.businesswire.com/rss/home/?rss=G1QFDERJXkJcFVJYWQ==`。
- 新快照发布时间跨度：**10 月 27 日 22:14 至 11 月 3 日 21:10（北京时间）**；RSS 下载约 4.83 MB。原快照 10 月 27 日 21:45 至 11 月 3 日 20:40。
- 导出并冻结生产当前监测词，而非沿用旧 seed。版本：INTC=4、MU=6、BE=3、COHR=2、NBIS=2、CEG=2、RKLB=3、SNOW=2、ONDS=4、IONQ=2。十份配置的 canonical SHA-256 均与生产 `config_hash` 一致。
- 直接调用现有 `relevance.regex_relevant`，保持大小写、字段、`any/all/none`、20ms 正则超时语义；保留命中组、实际匹配片段和上下文。
- 使用 RSS 的 **title + description 转成的 summary**，`body=None`。没有把摘要复制进 body，没有取得 4,094 篇完整正文，没有调用 Decision Model。
- 主口径按文章 URL 语言选择对应 L2，再按现有逻辑回退 English；zh-HK/TW 映射 zh-Hant。另跑全源强制 English 的对照：新快照 **309 条 / 391 个 article×ticker**。该差异来自语言选择，不是调了正则。
- 全量历史回放不套用正常实时 30 分钟接纳门槛；否则无法评价七天样本。结果从未回灌实时链路。
- URL 升为 HTTPS、去 `feedref`，保持原始 XML 和 GUID。没有通过标题相似度自动合并不同稿件或多语种稿。

该 feed 没有全文字段。多数英文摘要约 500 字符，部分中文摘要更长。正文可能新增正向命中，也可能触发 `none` 否定条件，因此本次结果**不是最终全文分发量，不构成完整召回率或准确率评估**。

## 3. 消息量级

CEG 的 177 个命中占总分发命中 **44.0%**；其中 **120 条只命中 CEG**，是总量最大的来源。

按新快照中的完整 UTC 工作日 10 月 28、29、30 日及 11 月 2 日计算：

- 输入共 3,300 条 RSS 稿。
- 至少命中一个 Ticker 共 247 条，约 **61.8 条/工作日**。
- article×ticker 共 319 个，约 **79.8 个/工作日**。

这只是四个已覆盖工作日的历史样本均值，不是长期日量预测。周末很少，直接用七天总量除以七会低估工作日处理负载。

多语种重复不能忽视：MU 的 Phison 获 Micron 供应商奖事件，在本次命中中包含德、荷、法、西、意五个版本，而且它们的 release 数字 ID 不同。因此“按 GUID 去重 0 重复”不等于“没有同一事件的多语言重复”。

## 4. 相关性阅读结论

本轮逐条阅读 RKLB 的全部 23 个命中、COHR 的全部 6 个命中，以及 MU 的英文命中；另对 CEG 英文命中按固定随机种子 `20261103` 均匀抽 20 条，并针对其他 Ticker 的命中组、典型好坏例做定性阅读。读取范围仍是 title + summary。没有把这些阅读笔记冒充正式 GOLD 或全体准确率。

### 4.1 CEG：总量最高，通用词和其他主体名称造成明显噪声

全部 177 条都触发 `power_market`；其中仅 2 条同时触发公司组。**74 条的可见匹配词只有 power，17 条只有 grid**，这些词不带电力业务语境。

随机 20 条英文命中的定性结果：

| 分类 | 数量 | 示例与含义 |
|---|---:|---|
| 明显行业相关 | 6 | 西屋 AP1000 FEED、LNG 五年供应协议、变压器/微电网认证、核能/公用事业公司公告 |
| 边缘、需全文或业务取舍 | 3 | 航空便携电池、数据中心地产顾问团队、锂电正极材料合作 |
| 明显误义或错行业 | 11 | purchasing power、广告赋能、低功耗芯片、普通员工股权、政治集会、ETF 例行 NAV |

具体根因：

- `power` 命中“赋能/力量/购买力”、品牌名及 low-power 芯片，不等于电力供给。
- `grid` 命中 Grid Dynamics、Grid Aero 等企业名称；也有确实属于电网的稿，不能简单删词。
- `NRC` 可是 NRC Health 或加拿大 National Research Council，不一定是美国核监管机构。
- `fusion` 可是 Fusion Health 品牌，不一定是核聚变。
- 基金名含 Nuclear Power/Grid，例行净值公告仍被纳入；没有核电业务新事实。
- `data center` 可能只在公司介绍/客户市场中出现，并非新增设施或电力需求事实。

这是全平台语料下很清楚的规则泛化问题，不只是 Business Wire 本身内容“差”。随机样本小，**11/20 不外推成整个 CEG 的精确错误率**。

### 4.2 RKLB：23 条里 17 条明显错域，主要来自 Falcon

对可见文本逐条阅读：**6 条航天/卫星行业相关，17 条明显错域**。

- 14 条是 CrowdStrike Falcon 网络安全产品、合作或收购。
- 1 条是 Falcon Copper 铜矿企业。
- 1 条是 ARTEMIS CAR-T 生物医药项目。
- 1 条是 Rocket Loans 消费贷款 ABS。

有效的 6 条包括 Momentus 搭载任务合同、AT&T/AST 卫星切换、Voyager 太空业务顾问任命、卫星量子安全活动，以及 Virgin Galactic 两份飞船公告。后两份还可能是同一事件不同发布稿，未自动合并。

**Falcon / Artemis / rocket 单独作为无上下文词，在全站流里不可靠。** 目前只是原样评估，没有修改生产规则。

### 4.3 MU：记忆和品牌歧义明确存在

21 条命中中，英文 9 条；逐条阅读至少发现 4 条明确误义：

| 稿件 | 误命中 |
|---|---|
| Capstone 冬季经营展望 | `in recent memory`，普通“记忆”修辞 |
| Everlaw 法律 AI 功能 | `Case Memory`，软件记忆 |
| Rhino USA 皮卡配件 | `Ram`，汽车品牌 |
| Trust ID 身份平台 | `website memory`，软件功能 |

Penguin DDR5 MRDIMM、Everspin MRAM 财报、TetraMem 存内计算属于合理行业命中。会展宣传、服务数据中心的软件供应商融资与半导体先进封装材料等，需要区别直接存储市场事实、邻近行业和泛背景；本轮没有一律判无关。

### 4.4 COHR：小量、整体较准，但 capital expenditure 单词过宽

全部 6 条中 5 条符合当前宽行业定义：Lumentum 财报、PsiQuantum 光交换/硅光子、AXT 磷化铟产能融资、QumulusAI 容量合同、Sparxity 超算部署。

另 1 条 HappyCo 收购住宅地产运营软件企业，只因 `capital expenditure` 命中 `large_compute_investment`，不是 AI/算力资本开支。**该组允许通用资本开支单独成立，缺少行业语境。**

### 4.5 NBIS / SNOW：有价值，亦受公司简介与泛平台描述干扰

NBIS 的 51 条里，36 条触发 `compute_infrastructure_topic`。可以找到 Nebius 自身财报日程、IREN 设备融资、QumulusAI 容量合同、CoreWeave 合作等真实信号。

但 Comstock 的公寓收购和普通写字楼再融资，也因公司简介含 `data center developments` 被纳入；Spellbook 法律软件自称 `AI infrastructure for contracts`，也触发硬件/算力基础设施规则。这些不是新增算力投资事实。

SNOW 的 47 条包括 Snowflake 自身财报/会议、ZoomInfo Snowflake 连接器、Snowflake 原生应用、数据平台及同行信息；也包含会展、供应商介绍和泛 metadata/data platform。当前 MCP 组可被词首的 `BUSINESS WIRE` 满足 `business` 条件；因此对所有 Business Wire 摘要来说，这个条件几乎没有消歧作用，MCP 稿可能因通讯社固定前缀就被提升为企业场景。

### 4.6 ONDS / IONQ：抽看主题聚焦较好，不等于已证明高准确率

ONDS 命中 AIRO 无人机销售/北约框架、反无人/无人系统、无人机输电线路施工、医院无人机配送等；这些符合当前无人系统宽行业定义，并非只有 Ondas 自身稿才相关。

IONQ 命中 D-Wave、IQM、Infleqtion、量子计算硬件和后量子安全等。同样不要求出现 IonQ。当前 13 / 17 条的量级不大，抽看比 CEG、RKLB 的通用词污染轻；没有用少量例子计算总体准确率。

## 5. 可用信号与潜在漏筛

RSS 摘要里已经能识别有经济意义的内容：

- [Constellation：最高 50 亿美元 DOE 条件贷款承诺](https://www.businesswire.com/news/home/20261027834771/en/)，用于核电增容；命中 CEG、BE。
- [Lumentum 财报及光互连采购承诺](https://www.businesswire.com/news/home/20261102315937/en/)，摘要已包含超过 50 亿美元的采购承诺；命中 COHR。
- [IREN 3.63 亿美元算力设备融资](https://www.businesswire.com/news/home/20261028683272/en/)，明确 GPU 服务器与数据中心用途；命中 INTC、NBIS、CEG。
- [QumulusAI 五年 0.5MW 容量合同](https://www.businesswire.com/news/home/20261103986973/en/)，命中 INTC、COHR、NBIS、CEG。

也发现需要复查的**潜在漏筛**：

- PsiQuantum 的光交换公告命中 COHR，却未命中 IONQ：标题/摘要包含 PsiQuantum 和硅光子，但没有当前量子计算正则需要的独立短语。应验证量子企业/技术上下文，而不是把字符串 quantum 任意放宽。
- Opterus 的航天飞行硬件/大型太阳翼制造公告仅命中 CEG、未命中 RKLB：power 误导了能源规则，现有航天词组未覆盖这类空间部件表达。

这些是基于 RSS 文本的复查线索，未取得全文，不能由此给出总体召回率。更多只在正文出现的合同方/产品/企业关系，本轮无法评价。

## 6. 建议

Business Wire 全站源值得继续测试，确实提供了当前 IR 自身公告之外的行业信息。**数量不是主要问题：当前规则约把七天四千条压到三百条；更突出的是词义污染和重复稿。**

下一步优先修复容易验证的上下文问题：CEG 的 power/grid/NRC/fusion、RKLB 的 Falcon/Artemis/rocket、MU 的 memory/RAM、COHR 的通用资本开支，以及行业词只出现在企业简介/通讯社前缀中的情况。保留行业扩散能力，不把规则退化成只认本公司名称。

本轮不调词，不正式分发。若继续优化，应冻结这次规则和快照作为开发证据，再用新时间窗验证，避免直接在同批样本上调出漂亮结果。不能根据本次标题摘要结果，就把所有未命中稿永久排除出 BODY：完整正文才有的关系尚未测试。

## 7. 文件

证据目录：[eval/businesswire-regex-screen-20261103](../eval/businesswire-regex-screen-20261103/)。

- `fresh-feed.xml` / `original-4078-feed.xml`：全部原始 RSS。
- `production-terms.json`：冻结的十份真实规则、版本及生产 hash。
- `fresh-summary.json` / `original-4078-summary.json`：总量、语言、日期、命中组。
- `fresh-articles.json` / `original-4078-articles.json`：每条稿件及命中证据，包括未命中条目。
- `fresh-matches.csv` / `original-4078-matches.csv`：可直接查看的 article×ticker 命中清单。
- `ceg-random20.json` / `reading-notes.json`：固定随机样本、53 个阅读判断及依据；混合随机/全读/定向反例，不合并成总体准确率。
- `export_terms.py` / `screen.py` / `review.py`：只读导出、离线匹配和阅读笔记落盘脚本。
