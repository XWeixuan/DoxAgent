# 更宽去重实验与 Newswire 列表标签核查

日期：2026-11-17。承接[消息效率评估](message-efficiency-assessment-20261117.md)。本轮只做离线实验和公开列表字段核查，未应用生产去重、过滤或源配置变更。

## 1. 结论与应用判断

**更宽去重值得试，但“命中候选数量”与“可以直接丢弃数量”必须分开。**

- 对 11 月 16 日业务日的 2,065 条正式消息，同日、同 ticker 内比较：标题 ≥95% 可合并 118 条；正文 ≥95% 可合并 45 条；URL／标题／正文任一路命中可合并 130 条。
- 正文 ≥95% 的 45 次合并逐项复核：**36 次纯重复（80.0%）、6 次同稿内容补充（13.3%）、3 次明确字段变化（6.7%）**。把一切内容增量都视为反例，则为 9/45＝20.0%；其中应独立保留或处理冲突的明确字段变化是 3/45。
- 三个字段变化是两次行情快照变化、一次 IONQ 竞品 D-Wave 诉讼通告的截止日冲突。**本日样本没有发现财报、订单、指引、监管决定等重大新增事实被该规则误合并。** 这是本日样本结论，不是以后不会发生的保证。
- 将已经复核的纯重复匹配重新按到达顺序回放，可确认减少 **43 次重复处理，占 2,065 的 2.08%**。其中 **20 条 initial_route=W3**，因此节省的价值高于“总消息量只降约 2%”所暗示的程度；尚未做逐调用 token 金额归因。
- **建议试用正文 ≥95% 的候选检出，加关键变化保留和同稿补全；不建议把标题 ≥95% 或去掉文章 ID 的 URL 直接当作删除条件。** 首轮适合选择同源新闻稿、已证实的跨源转载、仅来源尾注/联系信息变化的样本；行情变化不必重新分析整篇文章，技术限定和冲突字段则应保留到原事件证据中。

**列表标签也确实有用，而且目前丢失了不少现成字段：** GlobeNewswire RSS 有主题/关键词/证券标识；Business Wire 原生产列表 JSON 有 `industryKeywords`；PR Newswire RSS 有 `industry/subject`；ACCESS HTML 卡片有 `nr-card-tag`。这些可以在 BODY 之前参与筛选，但不是每个通道、每条候选都有完整标签。

## 2. 实验范围与算法口径

冻结对象为上一轮 11 月 16 日业务日的 **2,065 个 Standard ID**，本轮于 `2026-11-17T12:37:08Z` 只读回取其保留内容。Standard 正文可能在两次审计间补全或更新，因此本轮与上一轮标题/正文数字不能当成同一逐字节快照。原始内容只放在被 Git 忽略的 `.tmp-codex/message_efficiency_followup_20261117/`，报告证据只包含必要片段和指标。

- **只在同 ticker 内去重。** 不把同一稿合理投递给不同 ticker 算成重复节省。
- **只比较本业务日内消息。** 不代表已有多日历史库能去掉的全部数量；也不包括跨语言翻译的语义去重。
- 标题先统一 Unicode、空白、大小写，Google 标题去掉末尾媒体署名；相似度采用 RapidFuzz `fuzz.ratio`，即 `2×最长公共子序列长度/(两文本长度之和)`。正文用相同归一口径，没有调用语义模型。
- 正文近似比较要求至少 400 字符：1,781 条满足长度，其中 1,768 条具有保留的 FULL/SHORT_FULL 或 FULL 内容证据。正文 ≥95% 命中的 45 次合并两端均属于完整正文标记；但 FULL 标记本身并不保证没有导航/广告污染。
- URL 实验分为普通规范化，以及“同发布站点、同语言、去掉地区路径和末尾编号后比较文本 slug”的宽松候选。保留查询参数中可能决定文章身份的部分；Google wrapper、IBKR 通用入口不参与 slug 去重。
- 表中数量为**按正式到达顺序保留代表消息、其后只与已保留代表匹配**的减少数，不把 A≈B、B≈C 自动推导成 A≈C。联合规则的连通分量会给出 133 条，本实验实际代表匹配为 130 条。
- 本地完成全部 ticker 内两两比较，未在生产机器执行大规模相似度计算；工具依赖位于隔离 uv 环境，未修改项目依赖。

## 3. 各方案实测

|规则|原始可合并数|占全天消息|已核实纯重复|明确字段变化／其他问题|
|---|---:|---:|---:|---|
|普通 URL 规范化|0|0%|0|现有硬身份去重已覆盖这部分|
|去编号后的同站点同 slug|24|1.16%|15|1 条正文补充；7 条申报身份未验证；另 1 条证据不足|
|URL slug ≥95%|24|1.16%|15|本日没有比精确 slug 多找出有用候选|
|归一标题完全相同|102|4.94%|34|1 条行情更新、1 条正文补充、其余未证实|
|标题 ≥95%|118|5.71%|36|2 条字段变化、11 条不同系列分篇、1 条补充；68 条仍需原文/申报身份核验|
|标题 ≥90%|130|6.30%|37|增加的候选中仍含分篇、内容补充及大量证据不足项|
|正文 ≥98%|20|0.97%|18|2 条行情快照变化|
|正文 ≥95%|45|2.18%|36|6 条内容补充、3 条字段变化|
|正文 ≥90%|51|2.47%|38|6 条补充、3 条字段变化、4 条未证实|
|标题 ≥95% 且完整正文 ≥95%|30|1.45%|28|仍含 2 条字段变化|
|宽 URL／标题 ≥95%／正文 ≥95% 任一路|130|6.30%|42|3 条字段变化、11 条分篇、6 条补充、68 条未证实|

**“未证实”不等于误合并，也不等于真实重复。** 多数是 Google wrapper 只有标题/摘要、转载正文未补全，或申报信息缺失。不能把 118 或 130 条直接报告为已经证实可节省的量。

人工复核后重放的 43 条，来自正文 ≥95% 的纯重复边，以及几组完整短更正、广告污染导致相似度降低的转载、相同公告正文改写标题等。其保留代表可能与上述裸规则不同，故不是简单取“130 次合并中的 42 次”这一子集。

43 条的来源归属为 Yahoo 14、PR Newswire 13、Google 7、GlobeNewswire 4、IBKR 2、InvestorsHub 2、Quantum Insider 1。对应 ticker 为 INTC 14、NBIS 9、BE 5、MU 5、RKLB 3、CEG 2、IONQ 2、SNOW 2、COHR 1。

其中原始路由 23 条 ARCHIVE、20 条 W3；最终 39 条 ARCHIVE、4 条 FAILED。这里只说明重复稿实际占用了下游处理入口，未把 ARCHIVE/FAILED 当作内容价值标签，也未执行删除后的交易决策反事实重跑。

## 4. 反例比例、严重性和是否值得用

### 4.1 正文 ≥95% 的反例分解

|类别|次数／45|比例|内容与严重性|建议处理|
|---|---:|---:|---|---|
|主体无新增事实|36|80.0%|来源署名、版权/联系信息、转载链接、广告/推荐区、图表控件或冗余摘要变化|合并证据，避免再次独立分析|
|行情快照变化|2|4.4%|MU：盘前 −4.4%／743.08 变为早盘 −7.1%／723.09；NBIS：−8.8%／105.70 与 −7.9%／106.70。正文主事件未变|对消息内容保真有变化；对“是否值得再次分析整稿”通常较低。更新结构化行情/证据，不必因为价位刷新重跑全部流程|
|诉讼截止日冲突|1|2.2%|D-Wave 通告两版本为 12 月 15 日与 14 日，且交易所标识也有差异|内容保真风险中等；对 IONQ 的短线业务关联较弱，但不应静默选错日期。保留冲突/更正，或排除该字段作为交易依据|
|同稿技术限定更完整|5|11.1%|ZutaCore 同一发布跨 5 个 ticker：完整版本增加 pPUE 模型温度、液/风冷配比、满负载假设和对照值等|不宜丢弃更完整正文。可合入原事件，保留较完整版本，避免五次独立全篇分析|
|额外日期/历史说明|1|2.2%|Robbins 同稿某转载额外保留 Intel 10 月 29 日及“50 年最大单日跌幅”的图片/尾部说明|保守视为补充；合入较完整证据，不将其称为完全重复|

这 45 次按消息×ticker 计，不是 45 个独立事件：5 次 ZutaCore 补充来自同一个稿件家族。**6.7% 是本日明确字段变化占候选合并决策的比例，不能当作未来独立事件错误率。** 三次占全部 2,065 条消息的 0.145%，但用全天分母会淡化命中后的风险，因此应用判断主要看 3/45。

**提高到 98% 不是充分防线。** 20 次合并中仍有 MU、NBIS 两次行情变化，占 10.0%；其中 NBIS 两个 IBKR 版本正文约 99.9% 相似。这说明短关键片段变化可以发生在非常相似的长文中，单纯继续抬阈值会先损失许多真重复召回。

将标题条件叠加到正文也无法消除反例：标题≥95% 且正文≥95% 的 30 次里，仍有 MU 行情、D-Wave 截止日两次，占 6.7%。

### 4.2 对 DoxAgent 的实际严重性

两类变化应区别对待：

1. **可合并的低决策增量**：同一报道中的价位刷新、无新主事件的图表更新、来源署名变化。它们需要保留最新证据或独立行情输入，但未必值得再付出一整次 W1/W2/W3 成本。股价只是本文两个反例的变化内容；本轮没有确认所有策略都不依赖新闻里的价位，因此不宣称无条件零风险。
2. **应保留的事实变化/约束**：业绩、指引、订单金额、监管决定、否定词、重要日期、技术性能成立条件。这些不能因占全文不到 5% 就被丢掉。截止日冲突应留痕；技术脚注应保留较完整版本。

本日三个明确字段变化涉及的 Case 最终都在 ARCHIVE。MU 更新稿和 NBIS 各版本初始路由曾进入 W3；D-Wave 两稿初始均为 ARCHIVE。**没有观察到本日这些反例触发最终交易，但不能据此证明合并后所有策略结论不变。**

综合判断：**值得开展有明确范围的试用，正文 95% 是合适的候选阈值；不值得直接部署“95% 即丢弃”。** 本日纯重复复核下限 43 条，其中 20 条进入过 W3，已体现实际成本空间。试用应衡量“省去多少次独立分析”，允许同稿补正文或更新字段，而非要求把另一份原始证据删除。

### 4.3 标题与 URL 的具体误合并

- IBKR 的 `Insider Review … -2-/-3-/-4-` 等 11 份分篇，标题相似度超过 95%，但正文不同，不能当重复删掉；可考虑一份业务输入内合并阅读。
- MU 7 条 Form 4、SNOW 2 条 Form 144，去掉 URL 编号或只比标题会分别压成一条。本轮对应 7 份拟删除项缺少可靠申报人/申报身份核验，其中大部分无正文；不应删除编号这一重要身份线索。
- 相同 INTC 律师通知标题及 URL slug 的两条，后者增加完整的被指控事项，属于正文补充，不是同标题即可删除。

### 4.4 时间窗影响

|同 ticker 历史比较窗|正文 ≥95% 原始合并|标题 ≥95% 原始合并|
|---|---:|---:|
|5 分钟|8|47|
|30 分钟|22|91|
|2 小时|33|106|
|12 小时|45|118|

不少跨 Newswire/地区转载相隔 9–11 小时，仅做几分钟缓存会漏掉真实重复。这一窗口实验只用本业务日数据，未测跨日库；正式实现可按同 ticker、来源/标题候选与既有身份索引缩小比较范围，不必逐条扫描所有历史全文。

## 5. Newswire 列表有没有可以筛掉无关消息的 tag

**有，且其中几类很适合前置低成本筛选。必须区分“HTML 列表”“RSS”和“文章详情页”。** 本轮用与生产相同的公开 HTTP 客户端及既有路由检查响应，未修改登录态或源状态。最初通用 httpx 出现 403/超时，后续沿现有 PublisherClients 成功，不把最初失败误认为网站没有字段。

|来源／当前入口|实际字段|当前代码是否使用|价值与限制|
|---|---|---|---|
|GlobeNewswire HTML `Search/load/more`|50 个卡片只有时间、发布机构、链接、标题、摘要；本次没看到逐条主题/行业 tag|未使用分类；机构也未提取为独立筛选信号|不能声称 HTML 主列表每条都已有 tag|
|GlobeNewswire `/RssFeed`|`dc:subject`、`dc:keyword`；`category` 有证券/ISIN 标识|通用 feed parser 未保留这些字段|可复用并行 RSS 的标签，在相同文章身份上合并；这次 RSS 仅 20 条，不能保证覆盖 HTML 的全部 50 条及分页|
|Business Wire RSC JSON|每条 `industryKeywords`，另有 `language`、`newsItemId`|`parse_businesswire` 丢弃 `industryKeywords`|行业和主题混合值，例：`Class Action Lawsuit`、`Legal`、`Contracts`、`Data Management`、`Defense`。无需额外详情请求即可使用|
|PR Newswire HTML all-news|100 个卡片主要是时间、标题、摘要和链接；没看到逐条分类属性|无逐条 tag|网站有分类页，不等于 all-news 卡片自带分类|
|PR Newswire RSS|`prn:industry`、`prn:subject`、`dc:subject`，含可读名称/代码|通用 feed parser 丢弃这些字段|可前置筛选；当前源 RSS 是 fallback，不能把 RSS 的 20 条当作 HTTP 主列表 100 条的完整标签覆盖|
|ACCESS newsroom HTML|`nr-card-tags` / `nr-card-tag`，卡片有行业＋公告类型；URL 也含行业路径|`parse_access` 只读标题/日期/摘要，未保留 tag|直接利用现有响应即可，如 Biotechnology＋Exchange Listing Upgrade、Computers/Technology＋Company Update|
|TMX Newsfile Last25Stories RSS|本次 25 条只有标题、链接、摘要、日期、guid，部分有副标题|此通道未见主题/行业字段|不能据网站或其他服务可能支持分类，就声称这个现用 feed 带 tag|
|Bloomberg 当前 Latest 路径|现有解析器保留 locale、kind/type、故事身份等；未验证可用的行业/体裁 tag|尚无这类前置过滤|本轮没有额外触发 Bloomberg 身份/抓取流程，不推断能直接套用另外五源标签|

Business Wire 的标签并非必填。本轮三个时点/页面快照共 30 条，14 条 `industryKeywords` 非空、16 条为空；大量基金 NAV 稿件没有标签。**缺标签要走原流程，不能按“没有命中行业标签”直接丢弃。**

### 5.1 GlobeNewswire 有精确对应前轮低价值池的主题

[官方 RSS 目录](https://www.globenewswire.com/Rss/List)公开提供按 subject、industry 等维度的分类 feed。本轮实际请求以下两个 feed，均为 200、各返回 20 条，并在条目中验证了主题标签：

- `subjectcode/84-Class Action`：条目出现 `Class Action`、`Law & Legal Issues`；关键词包含目标公司符号和 lawsuit 等信息。
- `subjectcode/81-Market Research Reports`：条目出现 `Market Research Reports`、`Research Analysis and Reports`，有的附行业主题。

例如 Class Action feed 中的律师催客稿具有相关标签；Market Research feed 中的 DNA 测序应用市场预测、印度餐饮行业预测也有对应主题。**不是只有根据标题猜测体裁，发布方确实提供了结构化信号。** [官方分类目录](https://www.globenewswire.com/Rss/List)

注意两个细节：

1. GlobeNewswire 的 `category` 不能直接理解成“行业”：本轮看到其 `domain` 为 stock 或 ISIN，内容是交易所代码、证券标识。体裁主要看 `dc:subject`，关键词看 `dc:keyword`。
2. 一些条目只标 `Advisory`、`Press releases` 或 `Company Announcement`，过于宽泛；同样不能凭这种宽标签判断无关。

前轮标题筛选出的 GlobeNewswire 27 份法律类、49 份市场报告类，**不能直接换算成这次 tag 过滤会删掉 76 份**：历史解析器没有保留完整标签，本轮验证的是当前响应，需要新自然 cohort 才能测实际标签覆盖和误删率。

### 5.2 Business Wire、PRN、ACCESS

[Business Wire Newsroom](https://www.businesswire.com/newsroom)公开提供 Industry、Subject、Language 筛选，真实列表也显示相应标签。本轮原生 JSON 直接读到 `industryKeywords`；律师催客样本有 Class Action Lawsuit/Legal，DARPA 合同样本则含 Contracts、Defense 等。此字段最适合先保留后评估，不需要改成“逐篇先访问详情页”。

[PR Newswire RSS 目录](https://www.prnewswire.com/rss/)提供行业及主题 feed。本次 RSS 原文同时有可读名称和短代码，例如 Construction & Building/CST、Awards/AWD，以及 Corporate Expansion 等。实际 `prn:industry` 是**可重复字段**，不能转成普通字典只留下最后一个值；主题名、主题代码也不能混为同一个标签。当前通用 parser 只保留 feed URL 和 guid，确实有可修复的元数据丢失。

[ACCESS Newsroom](https://www.accessnewswire.com/newsroom)卡片的两个 tag 是现成的行业/公告类型信息，正文抓取前就能拿到；现有 parser 没有提取它们。URL 行业路径可作辅助，不应作为唯一排除条件。

## 6. 具体建议

### 去重

1. **值得试的核心：正文 ≥95% 检出，保留原证据，以“减少独立分析”作为目标。** 先限定同 ticker 和有界历史窗，优先来源可证明的新闻稿/转载家族。保留一个较完整代表并合并来源，不删除历史审计事实。
2. 对正文差异先区分站点模板、行情快照和业务事实。行情刷新可更新原事件证据；纯模板变化不重跑；业绩/订单/监管/否定词/日期及技术约束变化保留更新通路。不是只因“数字变了”就全部拒绝，因为 URL 编号、联系人电话也会变化。
3. 标题和松 URL 用来找候选，不作为独立拒收凭据。短完整更正可用精确正文匹配，但系列分篇、Form 4/144、含更正语义的条目要保留身份核对。
4. 不需因本日 3 个反例放弃去重；也不因 130 个候选就全量放宽。首轮可用本次复核下限 43 份作为回归样本，再以新的自然消息检验重要更新是否保留。相似度计算本身无需增加付费模型。

### 标签过滤

1. **先补元数据保留**：GNW/PRN RSS 的主题、关键词、证券身份；BW `industryKeywords`；ACCESS 卡片 tag。可沿用现有 metadata/raw_payload，不需要先构建一个全站分类模型。
2. **体裁标签优先于行业白名单**：市场报告销售、重复 Class Action 催客更贴近已发现的低价值池。行业白名单会漏掉矿业中的无人机磁测、非科技企业的算力项目等已有反例。
3. 不建议现在就全屏蔽 Class Action：目标公司首次诉讼、裁决、实质新指控与重复律所催客不同；截止日变化也应留痕。可让标签参与“同案件重复提醒”的节流，而非把法律类全部删除。
4. 没标签、标签过宽，或明确目标公司/已知重要事件的消息，继续正常流程；不要等待 RSS 标签而拖慢 HTTP 新稿。只在标签已可用且排除证据充分时，争取提前省去 BODY 和下游判定。
5. GNW 的两个专项 feed 可用于补充标签或建立对照；不能只保留若干正向分类 feed 就宣称全覆盖，也不能在标签稍后到达时不经验证地取消已经进行的正文任务。

## 7. 证据与验证

本轮没有合并/删除任何生产消息，没有改词表、绑定、采集频率或交易策略。原始全文保留于本地忽略目录；生产只执行显式 ID 的只读导出。公开列表探测为有界请求，复用既有客户端/代理路由，不创建浏览器或账户，不改变 Clash 地区。

证据目录：[eval/message_efficiency_followup_20261117](../eval/message_efficiency_followup_20261117/)。

- [experiment.json](../eval/message_efficiency_followup_20261117/experiment.json)：所有相似规则、阈值、合并决策和输入哈希。
- [candidates.json](../eval/message_efficiency_followup_20261117/candidates.json)、[candidate_review.json](../eval/message_efficiency_followup_20261117/candidate_review.json)：候选与差异；部分样本正文含站点污染，不把保留内容自动当作正确新闻正文。
- [adjudication_ledger.json](../eval/message_efficiency_followup_20261117/adjudication_ledger.json)、[adjudication_summary.json](../eval/message_efficiency_followup_20261117/adjudication_summary.json)：逐边人工复核说明及各规则真重复/变化/待核验数量。是本次离线评估标签，不是已验证的自动分类器。
- [impact.json](../eval/message_efficiency_followup_20261117/impact.json)：43 次保守回放、W3 初始路由、时间窗和反例关联。
- [listing_tags_native.json](../eval/message_efficiency_followup_20261117/listing_tags_native.json)、[targeted_tags.json](../eval/message_efficiency_followup_20261117/targeted_tags.json)：实际 HTML/RSS/RSC 字段、状态及样本；另保留首次通用客户端失败结果，不混成成功覆盖。
- `experiment.py`、`adjudicate.py`、`summarize_impact.py` 可从冻结证据复算。数据恒等式、同 ticker 边界、43 次回放及 20 个 W3 路由均通过断言核对；未运行无关产品测试。

对应现有解析代码：[newswire_listing.py](../src/doxagent/message_bus_v2/newswire_listing.py)、[newswire_sources.py](../src/doxagent/message_bus_v2/newswire_sources.py)、[industry_sources.py](../src/doxagent/message_bus_v2/industry_sources.py)。
