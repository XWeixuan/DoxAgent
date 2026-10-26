# Google News Search RSS：近 30 天 Site 黑名单治理测试（2026-10-27）

本轮实际召回 **4641 条去重 Google 新闻条目、1168 个域名**；排名前 30 共 2015 条。建议进入全局黑名单候选的是 **on3.com、247sports.com**，合计 149/4641 = 3.21% 的合并历史召回。两个站的召回记录已全量审核，均为体育语境的 `intel`；这里的 100% 只描述本次两个 ticker 的召回样本，不是全网内容定性。**本轮仅评估，不启用过滤。**

## 一、扫描范围、去重与覆盖边界

截至 **2026-10-26 20:08:41 UTC**（北京时间 10/27 04:08:41），回溯精确 30×24 小时至 **2026-09-26 20:08:41 UTC**。当前源绑定和统一监测词 revision 2 已在生产通过只读查询核对。BE 查询是 `("Bloom Energy" OR "fuel cell" OR "data center power")`；INTC 查询是 `(Intel OR foundry OR Xeon)`，RSS locale 为 en-US/US。以标题/摘要、Google RSS 声明发布者及可能取得的本地历史正文开展审核，不更改生产。

首先各 ticker 按日进行 30 次 RSS 查询，共 60 次；BE 5 天、INTC 23 天返回恰好 100 条。为提高召回，对这 28 个触顶日按原查询的三个 OR 词项补查 84 次，4 次传输失败随后重试均成功；滚动窗口首尾再补查 5 次（09/26，两个全查询及 INTC 三个词项）。原始 XML 和每轮状态、日期、计数及 SHA-256 已保存。拆词补查仍有 BE 2 次、INTC 18 次达到 100 条，边界 INTC 全查询也达到 100；**故不能声称 30 天搜索结果完整，排名和收益仅代表本次可召回集合。**

RSS 的 `after`/`before` 为日粒度，结果中有跨 UTC 日与大量 07:00 UTC 时间；按返回的 pubDate 对固定滚动窗口重新截断，包含截至快照的部分日。Google 结果更新、日期粒度、排序/索引及 100 条上限均可能造成遗漏。不是系统中已入库的全部 Google 历史消息，也不是 Google 可查新闻的总体随机样本。

去重键为 RSS GUID（缺失时使用 wrapper URL），合并多个日查询和多个词项返回的同一 Google 条目；同时保存 ticker 归属。它不能保证把同一原文的不同 GUID、联合转载或实质修订识别为同一新闻。按同站、同日、规范化同标题还发现 39 组、40 条可能重复，但 13F/Form 4 同标题可能对应不同申报、标题会复用，因此未无证据合并。最终网址也不能一律去掉 query 后合并：THE ELEC 的 idxno 就是文章 ID。

域名按 RSS source_url 的 host 小写、去尾点及 `www.`；Investing 各国家子域归并 `investing.com`，TradingView 地区子域归并 `tradingview.com`，Yahoo Finance 地区子域归并 `finance.yahoo.com`。Yahoo Sports 和其他 Yahoo 板块仍独立，避免把不同产品错算为同一站；未把所有共享托管平台的子域归到父域。全量统计见 [ranking.csv](../eval/google_news_blacklist_audit_20261027/ranking.csv)。

## 二、召回量与拟过滤收益

|范围|BE|INTC|两 ticker 去重合计|
|---|---:|---:|---:|
|所有召回|1685|2956|4641|
|建议黑名单命中|0|149|149|
|历史召回量占比|0.00%|5.04%|3.21%|

本次两 ticker 的 GUID 没有重叠，所以两列相加等于合并去重总量。上述比例是对**去重候选数**的事后模拟，并不等于线上 CPU、内存、BODY 请求或下游研判任务节省比例；它也不保证未来实时召回分布相同。

|建议黑名单|历史量|BE|INTC|已审核|低价值 / 审核数|主要原因|
|---|---:|---:|---:|---:|---:|---|
|on3.com|109|0|109|109|109/109|体育赛事/球员/招募情报，`intel` 词义碰撞|
|247sports.com|40|0|40|40|40/40|体育赛事/球员/招募情报，`intel` 词义碰撞|


这两站在两个 ticker 召回中未见高价值例外；不排除未来新增体育产业或体育公司 ticker 时这些站成为有效来源，应在未来扩展监测范围时复核全局策略。若 RSS publisher_domain 缺失或与实际原文域名不一致，当前域名过滤可能漏匹配，不能把历史模拟等同于线上过滤命中证明。

现有 `HiddenNewsIngressPolicy` 的域名规则匹配精确域名及其子域，且同时作用于 `google_news_search_rss` **和** `yahoo_finance_news`，并非只作用 Google；配置站点前需明确这两个源是否都应受影响。若将来只要求 Google，应先按源限定策略或验证误伤，而不是直接设置现有共享环境变量。

## 三、审核方式与结果

由 3 名 **Codex 子 Agent（gpt-6.1-terra，medium）** 独立分组审核。每条样本逐项阅读标题、RSS 摘要和可获得的历史正文片段，逐条写 low/valuable/uncertain 标签；除明确说明的历史片段外，不能声称阅读全文。历史存储匹配到 769/4641 条既有记录，其中 447 标为 FULL、38 标为 BODY、284 本身只是 SUMMARY；FULL 仍可能误标。大多数样本主要依赖标题/RSS，低价值判断与估计因此受信息缺口影响。

低价值包括明确的词义碰撞、与目标产业无关联内容、没有新催化的机械行情/小额持仓模板和广告；合同、客户、业绩指引、融资、监管、重要芯片/竞争技术、数据中心电力等有具体事件事实者记为潜在有价值；标题无法辨别新增事实记 uncertain。低质量比例以全部审核数为分母，并列 `uncertain`；不能把 uncertain 默认算低或高。下表 lower=low/样本，仅是可识别低价值占比，区间上界=(low+uncertain)/样本，表明资料不足带来的范围，不是统计置信区间或全量真实率。

每站≤80条时全量；较大站按发布时间、ID 排序做等距中点抽样（原始 80 条或约 50%，上限 250），后续新发现的区域子域及滚动边界条目全部补审；On3 原抽样后又对余下 28 条补齐，最终全量。抽样尽量覆盖时间但不保证同一 ticker、独家报道、突发事件的抽样比例，也不能证明所有历史高价值例外都已发现。

|排名|Site|召回总数|BE / INTC|审核数|低价值|有价值|待核验|低价值范围|建议|
|---:|---|---:|---|---:|---:|---:|---:|---|---|
|1|finance.yahoo.com|348|149 / 199|181|55|67|59|30.4%–63.0%|保留|
|2|tradingview.com|165|70 / 95|104|18|35|51|17.3%–66.3%|保留|
|3|ad-hoc-news.de|121|58 / 63|81|18|14|49|22.2%–82.7%|待复核|
|4|on3.com|109|0 / 109|109|109|0|0|100.0%–100.0%|建议封禁|
|5|marketbeat.com|100|73 / 27|80|64|5|11|80.0%–93.8%|待复核|
|6|investing.com|89|61 / 28|81|7|19|55|8.6%–76.5%|保留|
|7|notebookcheck.net|87|0 / 87|83|75|6|2|90.4%–92.8%|保留|
|8|moomoo.com|74|24 / 50|74|30|17|27|40.5%–77.0%|保留|
|9|fool.com|72|30 / 42|72|12|5|55|16.7%–93.1%|待复核|
|10|finance.biggo.com|66|24 / 42|66|7|50|9|10.6%–24.2%|保留|
|11|247wallst.com|53|21 / 32|53|15|11|27|28.3%–79.2%|待复核|
|12|seekingalpha.com|50|25 / 25|50|1|10|39|2.0%–80.0%|保留|
|13|benzinga.com|48|26 / 22|48|13|14|21|27.1%–70.8%|保留|
|14|wccftech.com|48|0 / 48|48|3|28|17|6.2%–41.7%|保留|
|15|digitalfoundry.net|46|0 / 46|46|39|2|5|84.8%–95.7%|待复核|
|16|simplywall.st|45|36 / 9|45|16|12|17|35.6%–73.3%|待复核|
|17|marketscreener.com|41|28 / 13|41|3|21|17|7.3%–48.8%|保留|
|18|247sports.com|40|0 / 40|40|40|0|0|100.0%–100.0%|建议封禁|
|19|shattered.io|40|0 / 40|40|12|13|15|30.0%–67.5%|待复核|
|20|tradingkey.com|39|13 / 26|39|19|6|14|48.7%–84.6%|待复核|
|21|aol.com|38|11 / 27|38|8|3|27|21.1%–92.1%|待复核|
|22|techpowerup.com|38|0 / 38|38|9|25|4|23.7%–34.2%|保留|
|23|theglobeandmail.com|37|18 / 19|37|6|7|24|16.2%–81.1%|保留|
|24|pluang.com|36|28 / 8|36|6|7|23|16.7%–80.6%|保留|
|25|media.unisba.ac.id|35|35 / 0|35|15|7|13|42.9%–80.0%|待复核|
|26|kalkinemedia.com|32|28 / 4|32|1|1|30|3.1%–96.9%|待复核|
|27|tomshardware.com|32|0 / 32|32|12|14|6|37.5%–56.2%|保留|
|28|gurufocus.com|30|19 / 11|30|10|17|3|33.3%–43.3%|保留|
|29|pcguide.com|28|0 / 28|28|27|1|0|96.4%–96.4%|待复核|
|30|phoronix.com|28|0 / 28|28|0|13|15|0.0%–53.6%|保留|


30 站审核合计 1715 条；650 条 low、430 条 valuable、635 条 uncertain。结论为建议封禁 2、保留 16、待复核 12；不按统一比例阈值自动决定封禁。逐条标签见 [combined-review.json](../eval/google_news_blacklist_audit_20261027/combined-review.json)，样本完整内容及原始标签在对应 agent 文件。

## 四、边界站点与早披露核对

- **Notebookcheck** 在该样本中很多是消费级产品、规格或促销，低价值占比高，但也有 Intel 18A 的拆解与制程信息，禁止仅按比例全局封禁。Wccftech、Tom’s Hardware、TechPowerUp、Phoronix 也可能提供不同阶段的硬件和行业信息。

- **MarketBeat** 的持仓及行情模板占多数，但样本也包含 Oracle Project Jupiter 力量供给/不可抗力讨论；同日扫描中 Engineering News-Record 在 9/28 有相关报道、MarketBeat 为 9/29，故这个案例没有证明 MarketBeat 是最早来源。时点主要为日期级，不能推算分钟级提前量。

- **TradingView、Yahoo Finance、Moomoo、AOL** 有聚合或转载；不应因文章重复、观点类较多就认为它们永远没有独特价值。当前样本中的 AEP/Google 702 MW 燃料电池项目同见 AEP、Bloomberg、TradingView，均显示 10/14 的 07:00 UTC；不能据此判谁先发布或断定其他来源总能覆盖。

- **Digital Foundry、PC Guide** 分别有大量游戏或商品促销型噪音，但数字铸造/硬件评测亦可能服务其他 ticker；优先研究词义和消息级过滤。**media.unisba.ac.id** 的标题含多个投资视频/异常混杂内容，亦有待核实的事件标题；暂列待复核，不凭域名直接指控虚假。

本次没有可靠的逐篇 Google 首次收录时刻、原发布平台时间精度和系统中其他消息源的同步发现证据，因此不能证明哪个高噪站“较早独家披露”或其高价值消息一定可由其他源替代；这些是后续决定封禁边界站之前需要用事件级来源对照验证的事项。

## 五、Top 30 逐站审核

### 1. finance.yahoo.com

历史总量 **348**（BE 149，INTC 199）；实际审核 **181**；低价值 **55/181 = 30.4%**，有价值 67，待核验 59。抽样：按发布时间及 ID 排序后等距中点抽样，比例约 50%，上限 250；另加全部边界及区域子域增量。建议：**保留**。聚合转载兼有大量常规荐股及股价故事，但存在公司表态、合同、技术、监管和业绩事件；跨 ticker 封禁会失去广泛来源。

聚合转载兼有大量常规荐股及股价故事，但存在公司表态、合同、技术、监管和业绩事件；跨 ticker 封禁会失去广泛来源。

- 低价值（仅标题及 RSS 摘要，ID f11c66f162f35ea5）：[Intel Stock Surged Over 40% in September. History Shows What's Next. - Yahoo Finance](https://news.google.com/rss/articles/CBMilgFBVV95cUxNMnJZQkVWa1g4OVBKTDlLYXlEcFd0QjRtcjZ2WFk0RmRtQWE0RGhUcGxLWE16dFpvTl9LSzV0Q0JLdDcyLUN1aTViWVF5eUVRa2w5OERZSVNZYWxvUmhQZUE4MGkzMkQ2NjM3MUZPVHJKUHBRUVpjclNqS3gtVEpXVnZKaE1oVGlZcEJXMFRnWnlzckdEQ3c?oc=5)
- 低价值（仅标题及 RSS 摘要，ID 10f4fc2f5950f01f）：[Zacks Investment Ideas feature highlights: Nvidia, Bloom Energy and Amphenol - Yahoo Finance](https://news.google.com/rss/articles/CBMiqgFBVV95cUxOSHBFTGJxMDhXRUxoNldqX25oM3dtd0owSnFEd0JOTVhEcm1tWGpBQVl1YlBURWtwZTk4b1JTY21uTUFwUkRJWVd1dWFETGJqeGtOOUtaWVB0TU1ObFhyWEpDSHFaMUthdlhENmVYN2JqMGdKUHBURFJkS3ZpbDhxakpMVmJvb1lIVVpjVHd6ZFJORElKWm9tamRpdDVKM1dscjZXTkZwYXd5QQ?oc=5)
- 有价值例外（仅标题及 RSS 摘要，ID 2c7368915ef77fda）：[Intel (INTC) Shared Its Factory Bill. Did It Give Up Too Much Flexibility? - Yahoo Finance](https://news.google.com/rss/articles/CBMilgFBVV95cUxOZ21ZNEFCeGczTUFsODMtR0tGWU8zMUY5TkJ4bHk2bGx6MXFqQktySUdPckpod3NkUGdPSGQ2VFVVc0R0NlV2bHBGRGZrUXBjYzJ3cTNDdGs0LV9saDVwemg2enpHdXIwcVZsMFY3Y2RfN0RNWHlLMmpOeHEyaEpiekt6VWN6ZU1jMGh3NXNPdzFZXy1LSWc?oc=5)

### 2. tradingview.com

历史总量 **165**（BE 70，INTC 95）；实际审核 **104**；低价值 **18/104 = 17.3%**，有价值 35，待核验 51。抽样：按发布时间及 ID 排序后等距中点抽样，比例约 50%，上限 250；另加全部边界及区域子域增量。建议：**保留**。汇聚多来源，既有大量行情/词义碰撞，也有数据中心供电、芯片合作及监管动态；全局封禁会丢失事件。

**典型低价值**
- 5499efd609cdca3e：[AMD Just Hit $1 Trillion After A Blistering 194% Surge YTD — Beth Kindig Says The ‘Dark Horse’ Still Has Room To Run - TradingView](https://news.google.com/rss/articles/CBMigAJBVV95cUxPODBwWlZRT19NYzhNcE1SSFZET2VJdTZvbHJWUXFVSmpwX2FwU3hWN2thOUQ4T25EQTFOeGZsWlBMODVmRXZGdnJFWm9HcTR6N1hyaUFfLU1zNU5RdUVySkFPSkIyUVI2Y0N5Ri1hWGQ2bGlpLVlMWGRiSVZXeHlEU1FGNDIwS2pBTm1nQ3ItZFpsaldSNmlzQThpSGhHZUJTWkRxTy1tSWtnckNGbHJUejRNMjlvVnlZc1YxeUFjVTk5NGFXZHlLWEhHZ0ZaMlg2M1NrZ2ZSUVc3cGF1Wk5ZbTZUYjlHZ3IwZzVHRXM4dUV0cDBIaGo0R1Z0WEU2ZjRR?oc=5)（标题/RSS摘要）
- 59f1c8ddf5f555ba：[This Quantum Computing Stock Has Gained For Six Straight Sessions, With A Buy Call From BofA - TradingView](https://news.google.com/rss/articles/CBMi5wFBVV95cUxPa3hpeWx5ZkhoSXhKcnAwZUdaRU90RG8yQzVTbkRjUkttSGUzX1RCSndXTHdBT0RZcV9TZkhWUXM5cmxlcEwzS2lXX2ZadllrQXJCX3JiRFBDQ1psLXBCZldTVjJyTWtoQUg0NDhaTkNWMHVVcFhVenVTeEdoZVNDZzQydk4zY2otb3lRZTRlMG1RdlY0MDBOUlZxZmtlbjFIdTBoSVhVUFR5Q2hoeUEwWVl6cXVRcTJhUGViSlhZbXJpd1VLc01OS090NDdCeDZ6YjhLT3NDTTVodEdvMUM4UjN6a1RPZUE?oc=5)（标题/RSS摘要）

**有价值例外**
- b461e2f7de91431d：[Alibaba, Solaria In Talks For Spanish Data Center Power Supply - Bloomberg News - TradingView](https://news.google.com/rss/articles/CBMi3wFBVV95cUxOYnJzWC0tX1lCaF9sck9fU29kbFd2NTVjLXdzQ1A3aElYaWt4NlBUdGxkWG0wN29Pd2cyUlR6OW5RV3RES0tHcW41MWE3N205czRzUWtMQTJYcjB6ektNbmtlcXZPN3RYQ1hNOFM3cmlxdUNPelYzdXhNeE9OUE1ldzNBeTBIWjdxVG1INHFwSFRTSHhsUDFFemVOS3RUcXpkVEpIeDFDNzVQYjRUTENQNmY1eGZQSVNfMHNpeDdDTGh3dzdWeE1XUmJDUS1DRkZHX0pRbV9JQUk1ODhMU3A0?oc=5)（标题/RSS摘要）

逐条标签见配套 JSON；uncertain 表示现有证据不足，并非低价值。

### 3. ad-hoc-news.de

历史总量 **121**（BE 58，INTC 63）；实际审核 **81**；低价值 **18/81 = 22.2%**，有价值 14，待核验 49。抽样：按发布时间及 ID 排序后等距中点抽样，比例约 50%，上限 250；另加全部边界及区域子域增量。建议：**待复核**。行情与重复叙事多，历史正文也有 AEP/Google 燃料电池和 Oracle 项目事实，跨 ticker 封禁有遗漏风险。

典型低价值：
- [Bloom Energy stock at USD 288.70 on September 25, 2026 - ad-hoc-news.de](https://news.google.com/rss/articles/CBMisAFBVV95cUxQTTRVVFlUamhCcGtwYWY3Mnk3UUtvUV92cF9Tbm9mMXh5aTRYQlBYTjNtVUdTZmhoUGxqWDV4c1ZLUG52UWlTMTMweVdrdHh0dHgtNDU1VUloOVQ2Sk9rQnllRGdjNmdvS2tkejBoOWdXQkZkLUxSV0R0T2VtN2NfTHBwTmRabDEwQ1J0NUZZWm4zNWp2QUlhZVpYdC03ZzFhODBHdFNQZmRfMGJrZklPTg?oc=5)（ID 2160807907349e9c；标题/摘要证据）
- [Intel stock at USD 115.92 on September 28, 2026, down 5.76 percent - AD HOC NEWS](https://news.google.com/rss/articles/CBMiwAFBVV95cUxOSWc3bjhUY1RvU0VfMkN6Y0hEU1NQRENPS05LVTZBc1VGWDAwRG1KY0VTZ1ZBbi1OdFYwMURrb2RKSEVyY2RCanFJSXNLT1dCaE5lYzhuSmRsLUt0cjljOThUNG5PdWRGUW9rT3JDYzJ1WGZhb2l4LUdWWFJtejBuZmh2RnFiTFhPVnZWX2hyX1dXV3d1RGpQVDdjNXFyX0U3bzJzczdoY0ZaX0YwdzAyQU1YZFVxeW00Y21BR1Y2TEs?oc=5)（ID cd4bc356fe002a84；标题/摘要证据）
有价值例外：
- [Bloom Energy Weathers Legal Storm as Pipeline Delays Cloud Oracle-Linked Project - ad-hoc-news.de](https://news.google.com/rss/articles/CBMi5wFBVV95cUxPNWIwQTZNU0tIQXBLVGpCZ0p0MFRFMjBEdTg1YVZVRjdMMW9ULXh4S09hM1FFNkNmUldaQ2JFQllkMGdIbmxXeG85TjFQNGUxZEloaXFNS2l0R1FqSWF5T21mRnRaUHRwb1B3TERnNWZqX29rMGN4RlFIOHFKTnFFYWp6MWdNLUZyQy10UUpic3BSaDB0QXh4VTAtbFlFRndBV0tQNnFoM1N0VFRnbGNvRFRYSUdZb1VRX2k4cjJvcC0tQmlteWZOVllka1pkT0RHYzdodFFBSkJlWW5ublBMdThaQ003Vmc?oc=5)（ID 2a0b33c1713c77f5；标题/摘要证据）

### 4. on3.com

历史总量 **109**（BE 0，INTC 109）；实际审核 **109**；低价值 **109/109 = 100.0%**，有价值 0，待核验 0。抽样：全量。建议：**建议封禁**。样本均为体育报道，intel 意为情报；全局封禁注意样本不代表站外其他可能用途。

样本均为体育报道，intel 意为情报；全局封禁注意样本不代表站外其他可能用途。

- 低价值（仅标题及 RSS 摘要，ID 00060a7bc524b08f）：[College football coaching carousel intel: Latest at Maryland, South Carolina - On3](https://news.google.com/rss/articles/CBMiogFBVV95cUxPM29sQUVKRzFUWE5iXzlkbzdLQVhYRFNQV01qSzd2SWRBeV9oSXRGdHhjNE96SkcyRDFITW9ldzUtUjF2UU5lc3pwejQxM3NvdURSY1VOMTd6ZEVYekRxZVdSeFVXMHlKc01tN2tzNzRyNTFUX09FV3BFeENvUHVoQ1hDWFdyajBWQVk0c1BXWGRNSklSeEJ2SkFydGllZzk4VVE?oc=5)
- 低价值（仅标题及 RSS 摘要，ID 4909c8bc90e625cd）：[K-State post-game intel: What went wrong in Cincinnati - On3](https://news.google.com/rss/articles/CBMijwFBVV95cUxQVERHSmhOeUNsZGxIYnkzd2NWRTdtbmIwOVhzLUpuZURQamw5VndqSzhnTC0wX1I5S0diWk03ZkNMV0FRdFpvNTNxTDUzYUpJeVVzdkx2dlBiY3NwRUo4Z01yTkFjRzdmSS10ME1NNjd1ZTdKcG1xNnBkajJ4N0lldFBGZjM3RG5uN0RfTkJBOA?oc=5)
- 有价值例外：样本中未见。

### 5. marketbeat.com

历史总量 **100**（BE 73，INTC 27）；实际审核 **80**；低价值 **64/80 = 80.0%**，有价值 5，待核验 11。抽样：按发布时间及 ID 排序后等距中点抽样，比例约 50%，上限 250；另加全部边界及区域子域增量。建议：**待复核**。持仓模板和行情占多数，但也有 Texas 接电监管与 Oracle 电力问题；全局封禁应复核其他覆盖及独特性。

**典型低价值**
- 5fa3e91e2c6ee28f：[Bloom Energy (NYSE:BE) Shares Down 8.9% - What's Next? - MarketBeat](https://news.google.com/rss/articles/CBMiqgFBVV95cUxOU2hZdExqa0VWYzJCcDdGMXF2dnU2aXB2OWg1ZlYzM3EwUVB2dEloVE5CcXhYS1dJTERVSkJsa3VQMVpFdjZLM2dDMTh2TnBVc1Vyb0ZfZ3JUcmVQYWt6WDE4QXFibEs5NXpSRjNqNXVXUzlic1M2XzVWa3drVm5QYjhCVm05SDhXc2lNRnJ0SFZva1lDS3lJYzVSU2Y1N2Myb2tVOWlnd3V0dw?oc=5)（标题/RSS摘要）
- c58b70b8ed48da5f：[Intel (NASDAQ:INTC) Shares Down 5.7% - Here's Why - MarketBeat](https://news.google.com/rss/articles/CBMipAFBVV95cUxNRWpZYVotM3VWb0k5NEFuZDFmeF9hY2hCaFlKY0xYLUpENDVCdnJoLVFleEQzTDJOMFA0TnJkcW9odHJjS3hMZjRrNmRheExaRnpCNDdURE16QkhTOVlXbEo1VkFBaDlsNG5KcUNQS1N5am9VLUdnd29ETnVFWTF4bWNtbXFoWUpVNHRENmdQUFN3Vi1PNEJsVW9FT25xclAzc0pnaA?oc=5)（标题/RSS摘要）

**有价值例外**
- c1e5fee43aaeef8d：[Oracle’s Force Majeure Notice Exposes a Bigger Problem for the AI Build-Out - MarketBeat](https://news.google.com/rss/articles/CBMisAFBVV95cUxQbFM0Q1B6XzVMRVlQTTVpU2pJSUJtQkpsRF82eHhzSnlqZTc0ODM0WGJ3UGVucVpUbU90TE1ud1Nta0Nva01lQ3p3Mmc0cEZ4eEE4XzdFSW00a0lTZTZIR01hYTU1X1hFZ1pOLVZhS0Y5U3l0MlZFMGotaTd4U1JkVFhGSThTODRscHRsQ3c3YkoxQVJhSnBxN3IwQzZsUGR0X3V2VHRJVG1sRDZmUy15Qg?oc=5)（标题/RSS摘要）

逐条标签见配套 JSON；uncertain 表示现有证据不足，并非低价值。

### 6. investing.com

历史总量 **89**（BE 61，INTC 28）；实际审核 **81**；低价值 **7/81 = 8.6%**，有价值 19，待核验 55。抽样：按发布时间及 ID 排序后等距中点抽样，比例约 50%，上限 250；另加全部边界及区域子域增量。建议：**保留**。含数据中心电力合同、Intel 产品、业绩及重要评级，地区版本和行情可消息级去重。

典型低价值：
- [Intel and Palo Alto among market cap stock movers on Monday - Investing.com UK](https://news.google.com/rss/articles/CBMivAFBVV95cUxQVnRYY3ltVUlfWWFSSG1TVjE4eEsta0xzcDcxaHJIQlhjMTdoeFB4WjZHcEtfZ2FkbUgyVlRoaFZpaG1FRHp5aEFBNWpTQ0Z4UHdQUHMyT3kyUzhoZWViVXJCUDVoNjA1MW45SkNZNXhSbmI0SmY4OUZud3VVREVEa0phdmh3azdDM1hvREZROV9JeHVOQlZRNzBJREVacllMeW1pOU1pZlBrc1BhVmZaUXZuNWd4S2l0MXFyOQ?oc=5)（ID 3792010a10e41030；标题/摘要证据）
- [Intel stuck in $115-$127 range as momentum fades: Live levels - Investing.com](https://news.google.com/rss/articles/CBMiuAFBVV95cUxPU09QeG1NcllrdW5HZnJtaEhoTjVrejhIOXRQamxQSGZ0TFQzbkhFN0RLczQzSXNSVEhpY01XMFU2VDQ0QkxSaHFMd2tZQllSYVB2WjhOT2dKTjYxSmpuZTJsVVZhY0NJSWE4REdpYkU5UWNQU3d2R2FCckl4ZlhpZEpaM2JJRmlabkFMRC0xZXZULWhKWE9EemVscjhuWmpzZXVfZ0dXdTFuekl1QXBsRVVQd1ZJSVFN?oc=5)（ID d34d2e9a26dfb3ed；标题/摘要证据）
有价值例外：
- [Enerflex spikes on 450 MW data center power contract By Investing.com - Investing.com India](https://news.google.com/rss/articles/CBMirAFBVV95cUxNUWp2amJxRGk5OElYUzhnMnhHSUpDMW5pQ1V2dUNSSVhOeWZqU3BaRU9wcExsaERtaXBJWlZhdWhCdVgtY251VkhtZGI2cWlCUktlTVl4M1pkcG5YZWF1eWczYUs0TnpQMncwaGNTTmVrSmEwUkEtUjdWYjJCekZPSDU2SkZSdUxWQi1hbHFpTlBPN2VteTh0ejJ4V3NydzBrYkEySnA5NnNraUJK?oc=5)（ID 4d073d7915101289；标题/摘要证据）

### 7. notebookcheck.net

历史总量 **87**（BE 0，INTC 87）；实际审核 **83**；低价值 **75/83 = 90.4%**，有价值 6，待核验 2。抽样：按发布时间及 ID 排序后等距中点抽样，比例约 50%，上限 250；另加全部边界及区域子域增量。建议：**保留**。大量旧芯片规格页或零售评测造成噪声，但 18A 拆解、CEO 重组和产品路线具有监测价值，建议保留。

大量旧芯片规格页或零售评测造成噪声，但 18A 拆解、CEO 重组和产品路线具有监测价值，建议保留。

- 低价值（仅标题及 RSS 摘要，ID 8e8f6ef0fcf82692）：[Palm-sized Intel Wildcat Lake mini PC debuts with rich port configuration - Notebookcheck](https://news.google.com/rss/articles/CBMiugFBVV95cUxOWmJlVHpyWm9ITlpTOWxPZFB1VjZPWHFjVm53RzNKSEFKWlB5amVsblc5bFNoUTcxckVtRVVHX0xpYnFPQXl2RmdKbXFOTFA1eDkxcTJkeEpoLUFqbTFhd01jR2RJbXlvOFhNTl9uSFQ2YkU2UzNELWNaZExyQXQ5V3hkU3NmSEIybEtoakZPZHBXS2FMRkFlYTBoc3pyZlQ4VzN2UkRiRG5iTFJPYkVHVGlRTDZGX3VFTFE?oc=5)
- 低价值（仅标题及 RSS 摘要，ID e721ff16af0ab967）：[Acemagic launches new 16-inch laptop with Intel Panther Lake and 24 GB RAM - Notebookcheck](https://news.google.com/rss/articles/CBMiuwFBVV95cUxNcGRTenJoUEdkUTNDQ0h1UURfUThMUjdZc25DZjdhVThUQ0s2U0ZQLTI3SFVLa2pzT3BBa2wyMWZfNVRRc2JiVV93c2dqZXNTNnZxaDl3cG93clVwa09FdVFuWmh1aVUxdnIwZjM1T3ZYNUFVTGZzU0lndm1wYy1NQjN5ZHZ0ZzBtZ0NwZjRSanR1UHNhTWNWN0liWnl6Qk9EeWZLaF9KWXN3clV6MlQyVnBQNTAyNnVia3FJ?oc=5)
- 有价值例外（仅标题及 RSS 摘要，ID 391b9665fd1c79fc）：[Panther Lake teardown reveals Intel 18A in detail — TSMC still leads in density - Notebookcheck](https://news.google.com/rss/articles/CBMivwFBVV95cUxPU013bTNXZ3Q0T2VIRE02bEFPUjdUcklmOGRGSjJOYmRxdl9lZ011c3RYTjhhM0JPdnJyckV5dHVTcUJMQVd3dG5Tdnd5MS1pRkRJdGJTMU53bnlKUjA4eEx3VFgxeU5mNWRTMV9KYm12NGx2VjV3VW05YW0zUWd3aXB4NWtfdy1DOWpTLW13Vzd6M2JvY25vMUFRM2pXUk90dmtjcjdwd1BBVjVOQXZPeXVTdHozMzh6SWtSR1RWUQ?oc=5)

### 8. moomoo.com

历史总量 **74**（BE 24，INTC 50）；实际审核 **74**；低价值 **30/74 = 40.5%**，有价值 17，待核验 27。抽样：全量。建议：**保留**。社交及期权模板噪声多，但转载明确工厂进展、合作与供电事件，不能全局屏蔽。

**典型低价值**
- b88bc89227e4e30e：[Intel Options Spot-On: On September 28th, 1.1 Million Contracts Were Traded, With 6.24 Million Open Interest - Moomoo](https://news.google.com/rss/articles/CBMimwFBVV95cUxPem1RWlJTNVl5NE5OSjRCWWxiMEVlWGJxX09aOEpzdjdwNUUtUnY1a0pyT2tkeG0zeGlvUEhfOENLdFlqelNjT0JHcmZHczVmZTh5dmNSTXItU0duUlNrM1RITklxZk9WeTRTNE9GcGF5ZDFEakxyaHJXUU52N29BbW1vRXBIRncyVkVuNU41U1o5TTZDSENPaDNrbw?oc=5)（标题/RSS摘要）
- d6eab67e3be857be：[Intel Options Spot-On: On September 29th, 643.32K Contracts Were Traded, With 6.09 Million Open Interest - Moomoo](https://news.google.com/rss/articles/CBMiowFBVV95cUxPVlBiNHBOYTNnWGNYNGN4ZTV1VTlITGdWcWZkQ3ZCZ0t2Z2liQTdNMThxdEs5X2JOSWU2REZYempFUUk4VXFweE9xbm5JUkkxZkQzTGtsQmZHSDJxcS1ZWXhpX3VycWJJaHRWWjg1cGJfdndzYnJRSnFfOFVIZEdRNW4zZkJZLTNxZU42N3h2RHM1T2s0ZjlGNHdCdEMxcXJGYTBz?oc=5)（标题/RSS摘要）

**有价值例外**
- 22c6e21e4b71ddd1：[AI Boom Reshapes Semiconductor Foundry Landscape! UBS Group: Surge in Demand Accelerates Capacity Expansion for Advanced Nodes, While Mature Nodes Enter Upward Cycle - Moomoo](https://news.google.com/rss/articles/CBMisAFBVV95cUxNYm9LR3VhUnh0TTMxTm1jUXVhYjdVSk1IdVN5bjVlbXNzLVFUd29XSmh0Ymgzbl9YZk1OMlE0YW5LUUdHdVNkT1Q3bmRfaE1vNWFTaTZ2QXctbnEtY1RhbEFMM3h2OUpBZHVTbWpZbl9yNXVmVkZSc3E3b3pJT3ozeXZ6ZEpkUHpCVFZXTFRPWVBSbmtIVFhwQXVmNlc1TEFvbVdHOUk4aFBLQk9aSWhIbA?oc=5)（标题/RSS摘要）

逐条标签见配套 JSON；uncertain 表示现有证据不足，并非低价值。

### 9. fool.com

历史总量 **72**（BE 30，INTC 42）；实际审核 **72**；低价值 **12/72 = 16.7%**，有价值 5，待核验 55。抽样：全量。建议：**待复核**。荐股模板多，但包括 Terafab、数据中心电力及业绩指引，标题不足以排除原创分析价值。

典型低价值：
- [History Says Intel's Best Years Have Been Hard to Follow. 2026 Is Its Best in More Than 4 Decades. - The Motley Fool](https://news.google.com/rss/articles/CBMi1gFBVV95cUxNcnBFclB3LVJMQXh3TVBvZnNDYWdnaDFrVFFwY2RrOXUzaE1vZWZrUUdGQjhLdDFRMmhMVTZWeC11YkVkNVlpZjdUNTlXUHNxTlZNVEJRSG13dDMtSjROaE4yN0FMZ1lucmVvWEFNcmZzYWlyem9lRWNmNDB1bERlRUppRHhOTXlJRkk0MFAyNkxMc2x2bTd0NjVTYWFuQjg5aGFfWFl3RnVOdERRRHhndlNiMHZJUy14c1hUNWYxQTJKVk5kazN1STR6SFJ2OHJrb0xnbEFn?oc=5)（ID 13f310b39373f959；标题/摘要证据）
- [Intel Stock Surged Over 40% in September. History Shows What's Next. - The Motley Fool](https://news.google.com/rss/articles/CBMimAFBVV95cUxOTDFLclMzZjJ3Ti1keVQ2SFdpSUZuY2NrMnhHbUtDSU1PTzdnN21oQ2JJM2pLcDlQZ2RsZHlUalIxVUZpN2VPR0c2SjEtUTF6Y21SZ01vQklaU2tJT0RuaTFCNWh0bHM0S1lJYUtwSnF2eXN6UTFGZHNzbDgyYi14VEUtRGlyMnc1cjlXQnZKc0RoTG5EMENnTQ?oc=5)（ID 15f73c787ebf223e；标题/摘要证据）
有价值例外：
- [Bloom Energy Says Its Fuel Cells Can Save a Big AI Data Center $3.6 Billion. I'd Still Wait to Buy the Stock. - The Motley Fool](https://news.google.com/rss/articles/CBMi5wFBVV95cUxPcEJmaU1RNlpHbWJTOXJ6MGhSbi1yUnNxQnVKNG9rS18zU2M0UEg3ZU1kb3ZUem9jMlZ2OVFJOVFXUUxFaXpMNWhyNlpIVmNzclg2YkhxZjIwZVVSakU1Z1pPdkNZNU8wQ0lYeWpKOXRjSWZwWkp3alBWdGY2cWQ5Q1FZUHBLQzlEZk9qNHY2Z0x3VC1rS3dkd2ZIbVRCb2ZpVzQzMzFDYWl0UFNUMndiNWRHWFo0dVgtYzN3dUNQc09qNm9QTFJSZXp3UFo2TEZJMzRteHZjUXIxcjhHejRGRGQ3d0pmOVk?oc=5)（ID 5325701cd1acf095；标题/摘要证据）

### 10. finance.biggo.com

历史总量 **66**（BE 24，INTC 42）；实际审核 **66**；低价值 **7/66 = 10.6%**，有价值 50，待核验 9。抽样：全量。建议：**保留**。含大量半导体供应链和数据中心电力订单、BE 供货与财报；存在零星无关故事，重要报道可能较独特，建议保留。标题中的说法尚未独立核实。

含大量半导体供应链和数据中心电力订单、BE 供货与财报；存在零星无关故事，重要报道可能较独特，建议保留。标题中的说法尚未独立核实。

- 低价值（仅标题及 RSS 摘要，ID bfc655aed6291073）：[Intel Shares Ride AI Server Boom to Best Year Since 1981, but History Flashes Caution - BigGo Finance](https://news.google.com/rss/articles/CBMidkFVX3lxTE8xNmVkckx3SmxHNUh4bTdncU4wMlIwNFJvUGZQaFZSME1wdE14M3k2cUFIRjJ4RWZTaXBnTUZDdzZRQmlFNjVVTWQzWUNhemxlUzZpS1BGMnAwbVF1Q1ZMZGs5MlQ1QVNJMGVZcEtKTzRYR0ZyTlE?oc=5)
- 低价值（仅标题及 RSS 摘要，ID 3ac2b34f859df446）：[Toyota Launches New "SORA" Fuel Cell Bus in Japan with Range Exceeding 300 km - BigGo Finance](https://news.google.com/rss/articles/CBMidkFVX3lxTE9SYjlzX1dmZ2ZGcHVha1JVMFlqQlRaNDE0TFY5cElSYU1GUkYyU1kybUFrMHNickVxU01HRDhJTG9GekNmSkQta2o4RVFYLVFQYWp0MmJCMV9JUnowNmRzcmlLNnJ6TEtFU05rc3dlcmJXNEJJS3c?oc=5)
- 有价值例外（仅标题及 RSS 摘要，ID 39222016660ae76f）：[Star Catcher to Test Orbital Laser Power Beaming as Google Launches First Space Data Center Prototype - finance.biggo.com](https://news.google.com/rss/articles/CBMidkFVX3lxTE15bW5QODc5LTVJQkV2Ykw5aU4xR0JzQU1GNXc2ZzB3NVg3a0FfRXcyb2ZDZ1ZTcV9MZGx2Z3VPeDRCc0VOZkdDYUM1MEFuVHdrM0NVbEFHUHBMN3lYSnB4MFBJSWdpRWUtUTZ4VG5CY3lRRUp6emc?oc=5)

### 11. 247wallst.com

历史总量 **53**（BE 21，INTC 32）；实际审核 **53**；低价值 **15/53 = 28.3%**，有价值 11，待核验 27。抽样：全量。建议：**待复核**。多为涨跌解释，亦有财报、供应合同及产业观点；是否可替代及提前性仍待核实。

**典型低价值**
- 697ad6fe4ece7d70：[Bloom Energy Sheds About $5 Billion in Market Cap in One Session - 24/7 Wall St.](https://news.google.com/rss/articles/CBMiigFBVV95cUxQWjJJSlVTc0hvUjJWTmdtTG5PWUp0ZVRMa1pENkNCemg2bG9RUThqWEs2VGtMc05QbTR3X3BUaHJuek9DODlfbHJOcVpRUHdiR0Rzb0M1QmpVc0NkaEw2ZldjQjRiM0JYYnVpdVdzbk9NRHdGRFdRV21WSG1LLUlINm5xVjJ4d2wzTlE?oc=5)（标题/RSS摘要）
- aeb86d9c60ea76a7：[Intel Drops 4% on Oil-Driven Rate Fears, NVIDIA Rises 3% on Record $150B Buyback; Taiwan Semiconductor Slips - 24/7 Wall St.](https://news.google.com/rss/articles/CBMi4AFBVV95cUxQXzRzbk8yU3RhekhEaXNXMF9WNUFObjZhLW9NWG9Fclp6U2xyckZmSDRRNDFxd2sxanY5VUpXMlJLOGR4TUVDaGpMeUYwc1M2azRGVzFrRm9JeVphSndQckUzRU9wYlE5aXE5UGVUZkN4ZHNGNWVZTnRWS24ydHc3MGVkZTljQk9kR3o0TklBOFRtamw2OHF3NXc1Ykk4eEVWUEJGa3N3Sll5TkVvRlZ0cGJDWm9UQlFhZTVTRkdEV09MRm8yTzNvZHRIWmFOQ3FESm9HRG5mM3c1eHRpaFMzQg?oc=5)（标题/RSS摘要）

**有价值例外**
- 0160eaffcecb1f09：[Plug Power Advances 4% on Electrolyzer Supply Agreement; Bloom Energy Sinks 3%, FuelCell Energy Treads Water - 24/7 Wall St.](https://news.google.com/rss/articles/CBMi4gFBVV95cUxOSldEcHVmVWhOSTVRQkVvbGZpUWhVcU5mUVQ0X0gzcVZzWkNFY0Z1ZVBlNkVCLTlFcloyNEtRUjlTMndHSEs2MEZvRmJJclhxck43dzNMb2dNcmR3SXNIRFRFWUszMWY5c3lqX1VpT0Rvc1JSQy1CME9pNnVZUWxFU0xlYVBoeEFUc3ZQYUpScXFsb3dYVUg0cUhidWc3UXlQUzFNbUhRWTFBZFVZVDNybHR5N21la3Z1LXo4OHZRYlQwT1dQX0RuY21kS0hUVm44MlBFLVR0RW5OUDBOM29UbXpR?oc=5)（标题/RSS摘要）

逐条标签见配套 JSON；uncertain 表示现有证据不足，并非低价值。

### 12. seekingalpha.com

历史总量 **50**（BE 25，INTC 25）；实际审核 **50**；低价值 **1/50 = 2.0%**，有价值 10，待核验 39。抽样：全量。建议：**保留**。有项目合同、财报和产业分析；仅标题的观点无法判断新增价值。

典型低价值：
- [Industrial stock winners: Vicor, Bloom Energy lead September’s top ten (XLI:NYSEARCA) - Seeking Alpha](https://news.google.com/rss/articles/CBMiqAFBVV95cUxQajM2SnY5RFRXa2w1QzZrbG5nUUZLMnFSbjBXMm14a2dYZFVGbUM0SFgzNy1fZ0dnT3dxU2JZNkkyal9aVWs3Rm1TRGxuYzZyOHNGR1FId1RvMUh4SzlHbk1ZYzd2QWlhYjIzLUVjTTRsUzNJOXIyU2hfdjlZNEFuX2RFMExGanJwbU80UnF3Qnp0TUtUX2U5RjE0bXRJU0c4Ym5zWkpzM0U?oc=5)（ID ac4dd42f0fcdcde4；标题/摘要证据）
有价值例外：
- [Bloom Energy stock gains on factory expansion, fuel-cell demand outlook (BE:NYSE) - Seeking Alpha](https://news.google.com/rss/articles/CBMirAFBVV95cUxPOHQ3ZDVsTm4tTVZDNXZyN05XWW5sZmVaYWI3V2xfTFo1TVpWT1JKcXp6bUZiZjZidHVUXzNMNjNNNTgycE0wQlZMVkcxTU1Uc1h2Tm1uc2g3U2dPdm9mbll6R2lzRGZteVlxc2pQWDRlX2g3SVZiYUk4ekxaLTJ2TkJzRUNBTmtMemFFU0RubWFaNUgtYVJwMU5vTFdIODRIQ0FqcUc4UjA1TkRy?oc=5)（ID af5e4a0da7e3ddbd；标题/摘要证据）

### 13. benzinga.com

历史总量 **48**（BE 26，INTC 22）；实际审核 **48**；低价值 **13/48 = 27.1%**，有价值 14，待核验 21。抽样：全量。建议：**保留**。股价、期权和旧回报模板较多，亦有国会电力政策、AEP/Google 订单及财报；股价标题若未说明原因则保留 uncertain，建议保留。

股价、期权和旧回报模板较多，亦有国会电力政策、AEP/Google 订单及财报；股价标题若未说明原因则保留 uncertain，建议保留。

- 低价值（仅标题及 RSS 摘要，ID dca603060a790c3f）：[If You Invested $100 In Bloom Energy Stock 5 Years Ago, You Would Have This Much Today - Benzinga](https://news.google.com/rss/articles/CBMivgFBVV95cUxQSmtlWkFsYTg1REF5WEc1NkJRV0tfa09fWk5ER1FvNGNVMHoyc01VM3hLLTd1Y1ZyeWI2UVl2akFaS19XNEJ2d3dfN05xZ3lZdlNIV2VRYUxxMEk3ZTJoNWozX1MxMUpibXdhZHNXUFpMX1BhR0RTMVNrNFZILWQ4ZU0tbWZ0amdqWmRZc2tGLXFWLVMyNkNBS3JNb3RZbWNYVWZZM1RhRG9zZFIwUHJDRHVRQkthNjgydUk1V0N3?oc=5)
- 低价值（仅标题及 RSS 摘要，ID f9b23b43e54ed6f1）：[Nancy Pelosi Owns This Hot 2026 Stock — And LeBron James Is About to Wear It - Bloom Energy (NYSE:BE) - Benzinga](https://news.google.com/rss/articles/CBMixwFBVV95cUxPbDVlUjlnWXZ0NXdRM1lrWi1vRE5wMVlYcV9QTDNiS0NWQkNNeHJsSTRRbVd0T3JLU3Z1OXZ3eXpvYmNBM0dNSUxXLXlCRmRhQlhBZU9WeUZSMWZiLUZXdzVycEs2LXlpTUZ4T2hVRDlRQnBsWW4yczI0TVV1aUVYOEdxNld6UkxTYjlZU3ZENHIxckk2MVFreTZWd0ZienJFblk4Yld1VEMxUHk0Tmh1Y2l1b0MzTnZyODZUR2pPY0Z4Ny1sdGZv?oc=5)
- 有价值例外（仅标题及 RSS 摘要，ID 57ed1477d4812ddd）：[SCHMID Stock Rises as Nvidia, AMD, Intel Eye Glass Substrate Technology - Schmid Group (NASDAQ:SHMD) - Benzinga](https://news.google.com/rss/articles/CBMiygFBVV95cUxNMVdwN0lJOU9fQVJvcEJMczNYSGVlbmxzdmlKZWFwdGJLOHg0SU9XUnNtRWdKTVF1Q3ppNEdHeU85N1BPalpRb2lwMXo2bE9UOXhUeXNiRnRMYl9ldklDWkdmU3V6RUM5Y2NrdFlBWXhabHhONjZDZ193NkxVNGZQVFlIX3RuYmNCUXdMMDRwSjFYTG9LYVI5dXNNV1FVckYtVWV4T2dha2xvd05JaXRNRTh6U1VpRjBraGVvbGZYQW1MR2FVSENlX2lR?oc=5)

### 14. wccftech.com

历史总量 **48**（BE 0，INTC 48）；实际审核 **48**；低价值 **3/48 = 6.2%**，有价值 28，待核验 17。抽样：全量。建议：**保留**。专业芯片技术、产能及订单传闻具有潜在独特性；传闻须核实，消费者内容不支持全局封禁。

**典型低价值**
- 93a62b4194c36898：[User Orders AMD Ryzen 7 9850X3D Twice From Amazon, Receives Intel Core i3 Twice Despite Both Boxes Being Sealed Pack - Wccftech](https://news.google.com/rss/articles/CBMilAFBVV95cUxPTS1MUDdNSURWLXNSUklVdzFBZVlrd2dDZ3pNVzFwS0U5YWk2ZnNrUE11Q0lMYmxWQXhOYmdLbFpHb1pEb3VnSGlMbjBwcUNLdGRrN0VkQWNFNGpZSGZrSkd1VEZHaFFCSEpiQ1VYRnM1VWxpQzNYN3l2ckJBLXBxZUpZWGZRczJRakpLV2tkaUdBZ3ht0gGaAUFVX3lxTFBEMU0zNW1Oc0wzNFFwUGZTWjZfWmI4VDJ0dWJKVWFqYmdyaWdYeWtSWXd5Z3FtdTRjQnFJNTBEQVRMZXpTVFFIV1U5WXNiTC1XdDJ5a3laaXdVeTFtMWNQY0t3Ui1EbW55MUFBd2UyamxOeTd2c0tDcDUwdUJWaDR5ZkF5X3g4LVdzYzdNU1QydElMRVRrV3M3MVE?oc=5)（标题/RSS摘要）
- d320bab9407b1bc6：[Intel Core i5 4670K Pushed To 6.3 GHz, Setting A New Cinebench R11.5 World Record After 12 Years - Wccftech](https://news.google.com/rss/articles/CBMipAFBVV95cUxPQXY2T0szUG8wV21mVksybkpuVG1kaVh4ckpwa3RUeFFUZHRUZlJxdFg3UUVDbWJNbWtVSVhVbGRWa0Y4dE8zeDY3VURxYURJTGhQemI5RDN1UzhUenhtRkVCU0F6aVR0cV9iQ3hBVzhkR19IYW1oS1FncnFvYWFnZko1TFAtSGJFbWxPNUhMUEpGZW91TVItUzEwT1FUdl9kZ1lWSdIBqgFBVV95cUxOeFRXQXFfamlBNEVLR044R3gtMVJDRHNrTVkwd05OM1JQUkZMa1B0REdNcHQ5QzZpSXRvN0JXVVpiOUVSRHdKc1FES1JuVWg0dThpaEVPYjFub2tFUTNHRThKY0NWOTRfVEl1NFRwemJsN3o0QkJTN3JPd3g2YWE5bFZRcnpZTURfY2tRcmRKVk10aDI1NElPZ2xiQng2SVFaTGpUQkd4cENpZw?oc=5)（标题/RSS摘要）

**有价值例外**
- 2767763494bc09b4：[Intel CEO Lip-Bu Tan Calls TSMC a Partner Not a Rival, Even as He Rebuilds the Foundry - Wccftech](https://news.google.com/rss/articles/CBMiqwFBVV95cUxPUmZyQkJXemJ5NGk4WjdGM2k3SXllQVI0WWZZX1RDMnVkeElPQzRGZElHQXVQU2JnVkFEOVh6ZjJBbWlFeTZ6QzlZc2lacWoxWEZ2QnBOMUg3OWo3MDZ0cFpkX0stTG83NEZKOHVNbkQ1U1RqVnYyLVZsQnJDWGdaVWRmMTR0UmE0bUlpTmtLMVpRcGdUTFlhOVlLQ25CSEREc0NaNmFXblpHVUHSAbABQVVfeXFMTmtoSUxMWWVuajdwTGcyTFl4TlFsT0VqZjYyMnI5ejR0b2FGUUxOTktYWkp3clk4QV9yUHZOU0FXMk9SaDB5ZXdHbnA5NlJxN3F6QXhvRXR1X0k2ZVJVQW1nU09seE03VTd0Y3VhZkdvLVlZSGxvQWxuZ3BDUFl0MmN1R3NtbDNWWnhCUHh1VC1hd1ozaFplbm12OU5DLUF1enRDbW0wQ1Y2WGxLamxYOV8?oc=5)（标题/RSS摘要）

逐条标签见配套 JSON；uncertain 表示现有证据不足，并非低价值。

### 15. digitalfoundry.net

历史总量 **46**（BE 0，INTC 46）；实际审核 **46**；低价值 **39/46 = 84.8%**，有价值 2，待核验 5。抽样：全量。建议：**待复核**。游戏和外设误召回多，但有 CPU 份额及 AI 工作站芯片供应消息，封禁前需复核。

典型低价值：
- [Creative XF1 Review: Classy PC Speakers That Hit Harder Than You'd Expect - Digital Foundry](https://news.google.com/rss/articles/CBMisAFBVV95cUxNNjBYSVM0TUpSV1F5cVhMb0JfdlZvMzhZcjBnQmpYRFpybmJBdERNV1lTSmJJelVUWjRyRjYyRS1pR1FnRE52M3dYRGhXU091M2hQN3pvMHlaeDlHOU1NR3ZYUC1jYTQxU202enFTR3ByeldyRG9jSk9XZjUwdFBJYUJHbmtEdDBHUkRGUlJ6QllrdVNzLTc1b1o1b0phdEl3NDZNa2tIVHpNelNBc2VlOA?oc=5)（ID 006647b007f3ac40；标题/摘要证据）
- [Review: Control Resonant: An Impressive PC Version With Superb Scalability - Digital Foundry](https://news.google.com/rss/articles/CBMipwFBVV95cUxOV3p1dzJubThCcUdOZTZpVzdFZFpCeU9qUUtxMkF4dTVTMjNNSkd4dDctQ3NrWjN0THFWQmxqNEVUZzNyeHFLeUpEeXdlLVotTDJ1UVN4c3RCRjFmSkRsYUJialhYZWRuNDloTHUybmNWajRIR29pclRBcEhLVDQ1cGpVdGkwZ2UyLVR4QzNQVDNDd2ZNYVp5Q3o4YW9sTkl2VWtjMnBqYw?oc=5)（ID 4a3afed0a36ea166；标题/摘要证据）
有价值例外：
- [Steam Hardware Survey: Intel CPU Share Drops To Lowest Figure, Nvidia GPUs See Bump, Octa-Core CPUs Hit 30% - Digital Foundry](https://news.google.com/rss/articles/CBMi6gFBVV95cUxPc1U2cEdxaGh3V2lsdzJmb01CNjZDdS16ZE5yREdIaURTQVBWUlBDeHBiby03VXRQYU1meVFPa25odkcyWlkya0hCcm1MbW9oMklIMlJ3b0laMzFKRW96LUFqckZBUWtyNTdRSVVTVm9GR2FlNGY0dkpJOGZ5cGdhM2ExU2Z3VFVLdUlfUExoWkczaUpSaUR2YnJQdVFvbks1YjNGUy1Xazh5Wnd3TUhxVExsc0hMZnc3MDNZeGkxNFBMUHdja0VMOTVzVWxIN0l5QmJLb2FIZER6UURSbFRrQi1iVnBFNFFnSWc?oc=5)（ID 05938cf21acd438e；标题/摘要证据）

### 16. simplywall.st

历史总量 **45**（BE 36，INTC 9）；实际审核 **45**；低价值 **16/45 = 35.6%**，有价值 12，待核验 17。抽样：全量。建议：**待复核**。投资选股和估值模板多，但样本仍有代工客户、竞品电力订单和新 GPU 事件；可能只是事件再包装，跨 ticker 封禁有损失，建议待复核。

投资选股和估值模板多，但样本仍有代工客户、竞品电力订单和新 GPU 事件；可能只是事件再包装，跨 ticker 封禁有损失，建议待复核。

- 低价值（仅标题及 RSS 摘要，ID 0c8b3c6be8ec5bba）：[3 Hydrogen Stocks With Revenue Growth Up To 41% - Simply Wall Street](https://news.google.com/rss/articles/CBMitAFBVV95cUxNdW5rSlVxV2I0Vzg5dnhyNUlCam01c2psLWktZXdOWS14Z296ZWUyRjdhZEdPSnU0MGFHZ3BNZHJsR3c1SFh3Q25ZZ3I1ZzRBWHZRUmdMQXkwQ3Q1dTcydHB3R3BPWnAwdWFkaTdpUlRfeV9IdUpuM25OX2tRNmtlM2ptZnhyVTdobXRsR0dCN3FVYml5ZzRHN0x1U2VkZENyXzdRbTRJQjZDNWJ0WXIwakwyT1PSAboBQVVfeXFMTmp0bTBxaHEzcE00Z2pKYVU0WXl0ZDdMWjJCNDMwTDN5UTRLNnpsQTVMeUxDcWtNU1FPS29sZ0RiclRQcnh6b2t5YzBBcUdOMnlWZEdFUHdNa2J0NmRGUVVINzJaMlRsZTc3REFMLUloNlo1dG1USmVpNkxyaVRXR0EwUUpvLXVnLWI4b1FYS2hTV1QwanZXdzhXZkZJb0gtZTB4ODZtX3NZaG5vSmN3azJZMlFqY2RmWWJn?oc=5)
- 低价值（仅标题及 RSS 摘要，ID 1a372984f3503041）：[3 Hydrogen Stocks With Revenue Growth Up To 43% - Simply Wall Street](https://news.google.com/rss/articles/CBMitAFBVV95cUxQbjdZSm56MHlCODc1TTl6TzZXR0JqWDVWQzE5Z28wMzZtMlZTTDUtV1QtZk0zRHNvd2F2QzY4MkV5TDZLRUhyakRCbjlLd3Nxd2hYZ2hoZnlLMHpjdUF6YmRNS0t2ZHFQVDVUeG5SamZWcGR3bXB4Zlh4R1dVYml1V2dTY3NYdlJWVlR4ZDhFSElJdTVZbnpmcjRPR21nNy1xYV9aWC1OdFkxYXlkN25FYjhzakrSAboBQVVfeXFMTWk0RWRFY1kxejVjc0hvTzJCNElRRjFhd1NCSXY4VmdNcWVvalRnWUFZXy1ieGNXamZMa0x2Q2x6Nk1GaG84TFpkZHNFRE1EOGQxX3NRazgtRUduUGhHZ0gyZ0kxQTVRM1NVMzFkazRWeUQ2RHV2eXhmTEliblVzbkczaWZPZDFUMlUydWNWQTFCM0otWGdGSE1IS0xNajhUS2RRdVkzMlgxZE5JYVM3S05GaU5SQnAtc0FB?oc=5)
- 有价值例外（仅标题及 RSS 摘要，ID 7276319800c0bc11）：[How Investors Are Reacting To ABB (SWX:ABBN) AI Data Center Power Launch - Simply Wall Street](https://news.google.com/rss/articles/CBMiywFBVV95cUxPcFIzcHNad3BDZEthY29yeWpsdmZPVURicWxGSUlzNVlPRFZSSkVDRzBWS0t0R2M1a1hUV2E4UTRGaEZwX2FsR0EtSnNwUkEwUzNna09VUHlxOHd4MkFRUVV6UER4bUpSV09ybUxKQ2ljS2VIbnc0SWNTYVdEdGVGeHFjOXFDQXFoTThaQ0tlRlJ6MFA3WXpoZlZjbGJTZFM5WElRM3dfaEtYMEhVQzFqZzMtYnp1eVBLOFFEaGVnaGlJTTBkSmFSQVV3d9IB0AFBVV95cUxNS3RTUnczVk43TTM5RFZSY25jTTRIdDRCNGY3NGlfakh2ejJNdjh1YV9MMVRXMXVReHYwbWhJclp4VW5HVzhEaGJpU09TaWhYYlhkd1Y1NGNiYzgxTXl5aWh2Yk5hZEp2T2FLTXZPOTBVdEJOOWpZWjlTc1pBNVlhcFZPUWZ2d3BXLWFiejgyT2pBclhhN3ZkYmFrSEVMNnQzUnJ0dWZiQWpGeDdZM3YzTTNwX2luRnp6allJNHZsbndRRmJMOFNfT3pRMW5VT3ZH?oc=5)

### 17. marketscreener.com

历史总量 **41**（BE 28，INTC 13）；实际审核 **41**；低价值 **3/41 = 7.3%**，有价值 21，待核验 17。抽样：全量。建议：**保留**。存在真实合同、业绩快讯、监管及产能信息，亦有关键词碰撞；不宜全局封禁。

**典型低价值**
- 2c9da77fc860d75c：[Pioneer AI Foundry Inc. Reports Earnings Results for the First Quarter Ended August 31, 2026 - www.marketscreener.com](https://news.google.com/rss/articles/CBMi2wFBVV95cUxOVFJXVExEbUlkM3lIWk9hckNYblJPc0hzdnFIZkRmMk9uNWNTajJEdFNTa0hySzhQZE1PZlY4VG1lRUxjbFdLclQ5MzhXZkRLMXZaZEFPUDVncWVoN010TTgtVVpQQUxnNlFCVWotcy1LN2lXX0xZSE55WXN6Q2xGR1pSYWVrYk9pWkU2Y0dvOXpIZ0g0TEZzOXZrVVRsSGFWWEVkTUtoQmJoand6WDN0OXJTcWFDWmhHVVVHZWNKNGo4bnNLRUVPM2ZsWk9CRE9UdG1mTWhKLUV2emc?oc=5)（标题/RSS摘要）
- 76dec25572546816：[Customers Bank to Deploy Palantir's Foundry, AI Platforms to Improve Customer Experience - www.marketscreener.com](https://news.google.com/rss/articles/CBMi1wFBVV95cUxOZ1ltellYSmNQdWJTaEo3R3RscDBSeVI2ZVljUDdUMEpLNHpMc1QycGZ0UlNzdGVIU1MzckNRT0tqem1MUnMzeFJFMFozajJZTFlveUVvTDBWQjdqVHdSYnNGdjFNTUZzbk1qYkJWMEZCX3FKLUwyY0hxNkVLdDBLaFpGUnFZWFFrS0JPUTBqMFdOa0pURmkzcnptT2NfbWt4UVdYZlRMc2Q2Mko1LXZ2STZfVGVNWmN6c010TW81NHJRYVJjenpmbEJIRkFzdnpGYTFURzJONA?oc=5)（标题/RSS摘要）

**有价值例外**
- 601093bdc4d3fa12：[Alibaba in Talks With Solaria for Data Center Power Supply in Spain - marketscreener.com](https://news.google.com/rss/articles/CBMivAFBVV95cUxQcGczWXFPYTJwRDZkWHRDZVRlRF9KUHdJUGg2V05LUF9UNmJKa2JaQVJQYUtsMHNlSjBQVGIzaU1uOTFNaHlvN05YMGwzWFZhbDF4SjJtcDJmbm51S05TLXFaZjlRRnQ4U29WdVFqMWlYYmtiQ0c5RkdLMmxCaExmTzFad0F3Z0h5S01aOHY0U0ZHUnNoU0hEOWpPa3Q2WXNmREFKbjRlREd3dHZwRkw0OUlQT1NFMkdFbzFtdg?oc=5)（标题/RSS摘要）

逐条标签见配套 JSON；uncertain 表示现有证据不足，并非低价值。

### 18. 247sports.com

历史总量 **40**（BE 0，INTC 40）；实际审核 **40**；低价值 **40/40 = 100.0%**，有价值 0，待核验 0。抽样：全量。建议：**建议封禁**。样本均为体育报道，intel 意为情报，非 Intel 公司；建议封禁，未见相关例外。

样本均为体育报道，intel 意为情报，非 Intel 公司；建议封禁，未见相关例外。

- 低价值（仅标题及 RSS 摘要，ID 6590b0d8b5d8d6a2）：[Notebook: Intel from massive recruiting weekend for Florida's win over Ole Miss - 247Sports](https://news.google.com/rss/articles/CBMi8AFBVV95cUxNVHJxTHg2RDRIamNpdmQyYzdEU21ScEZnS3RoLUtSTWVVZFBoZ3R3bXU0dW5NTVVIcHhsZXZyanB5UldKRk54TUVxR0RJV0RBN2FKLU5ScTVoUm5MWUNlYkg4S29hbjdqYWZ4N3AzQUplRVc0RHFDR3RjZUw4ZDNUMTdJaVdiUHVGQkpNMWJrVU5NTHdaS2E5WlBpbEhWSm9wQ2ExUFdVTU04Vk80eEJPYi1YN2plV3c1cjREWDcyd1c3NnVvT082VVJiY0duZ3dQR3dTcUhtd2RJQmM1NE16bWoxckhPaG9tcDVkcU1wWVg?oc=5)
- 低价值（仅标题及 RSS 摘要，ID 39745eef4f23e470）：[Intel on Michigan LB commit Frederrick Ford - 247Sports](https://news.google.com/rss/articles/CBMirgFBVV95cUxQUUNUSGEyUnpLNWZhLWlYS1VjSEt5MFZiT3lCVnpkdDl4bGlXS0hCczFpRjZHNTJjOFJzQWkycTY2cVRJSE1lZzJyWmVYWUo4MThySzdwQUJSNXNQQlk5NnJ1NzZxWG5lYlFocVR6ZDVCNE9FVUlvZEsyb0M4dnU4LTNNdk5GQ2ZuYjZsMUdBZFdiVTJXdG9QV2VJR1BITkJGczFucGtydXUzT2txalE?oc=5)
- 有价值例外：样本中未见。

### 19. shattered.io

历史总量 **40**（BE 0，INTC 40）；实际审核 **40**；低价值 **12/40 = 30.0%**，有价值 13，待核验 15。抽样：全量。建议：**待复核**。智能合约 Foundry 词义碰撞多，也含芯片、制程和服务器技术；需复核来源可靠性及独家性。

典型低价值：
- [Truncation Bug Testing: 12 Foundry Steps, $1.7M Lost [2026] - shattered.io](https://news.google.com/rss/articles/CBMiaEFVX3lxTE44SFNseVlsd01RZnIwRVk5ejVuanFVN0I4aEZxa2RWNDhIZTBqMGlHblZ3c1lvd0tORzVMZkdudC1XMVh6cmJrampVWWxKT0Ywalp6aGR1c05YZmZaZGxsSGxZYlVxc25X?oc=5)（ID 638d0f99ae5131c4；标题/摘要证据）
- [ERC-4626 Inflation Attack Testing: 12 Foundry Steps [2026] - shattered.io](https://news.google.com/rss/articles/CBMidkFVX3lxTFBsVkZuR3M3N1Z5LTBPWTU3bWxya3NCWnFleHE0U0RaZ01sel9qckRwaml6ZXlhT0M2Q01CdDhkekY3RVJ3RzBkUFF0OHFnRXF0Um1IY1hNZ3RJRVZUMkhqbjdRRG5BeEFyTVF4WV9SSzU5VkptQ1E?oc=5)（ID ca66aa4aceb79b7a；标题/摘要证据）
有价值例外：
- [CoreWeave’s NVIDIA Vera CPU: 11,264 Cores, 3x Faster [2026] - shattered.io](https://news.google.com/rss/articles/CBMib0FVX3lxTE5xUzRHNnI1WS1PUi0xV01wNkZMNDlIN3FFYzFwREdQZ1JZV0dxRnA2NnRzZnJMTE9xNGdJcUpFTUctM19NOU53RENNNTFrWm9iZEpCcEZQclNUcUtRUjJZS0Q3U25SOUZMVFkyOFI1Zw?oc=5)（ID 6a17feef84cd8847；标题/摘要证据）

### 20. tradingkey.com

历史总量 **39**（BE 13，INTC 26）；实际审核 **39**；低价值 **19/39 = 48.7%**，有价值 6，待核验 14。抽样：全量。建议：**待复核**。大量机械行情模板，但也出现晶圆良率及订单报道、财报和电力动态；提前性与来源待复核。

**典型低价值**
- 67a6b6b3e32ffa49：[Bloom Energy Corp Stock (BE) Moved Down by 8.11% on Sep 28: Drivers Behind the Movement - TradingKey](https://news.google.com/rss/articles/CBMiiAFBVV95cUxQMGFZWW5ZRDVoblBTM3ZpWkQ4ZVU4S2VmMEJ1cndQSFVkWGxSZ29ZYm1ET3RGQmE2Z1BmSWxCTFdqcHhFUlNDX2kxSUhaRVR6S3ZGTVE2cU1qem9VR3FXT2dSd3BZckpxZDQ0SG1lVk1SVDF3cnB0SWhhLWFlamNCa2szS3NWSERq?oc=5)（标题/RSS摘要）
- d4fdc679592f49d2：[Intel Corp Stock (INTC) Opened Down by 4.99% on Sep 28: What Signal Does It Send? - TradingKey](https://news.google.com/rss/articles/CBMiiwFBVV95cUxPWUpFSlp0d3RxVHJVUmxERlVMOFczRlhlVTU3Vnp1dW9qU3dpRm5nVFZxSTdMelA5aTVqc1pwN2NwU3pETkQ4Z3VGcTlpd1ltb1FkMWpXTHB4dnlVbXR2YXVQVXFvdjY4WkRWazg1SzNoMWpWX0hadWZSRHdKZFhMYWtGSzhWXzhKVGVJ?oc=5)（标题/RSS摘要）

**有价值例外**
- 3baf019df848bd24：[Intel Stock Forecast: Kiryat Gat Blast and Foundry Doubts Push INTC Toward $81.64 Before Q3 Earnings - TradingKey](https://www.tradingkey.com/analysis/stocks/us-stocks/262223052-intel-stock-forecast-kiryat-gat-terafab-q3-earnings-81-support-october-19-2026-tradingkey)（标题及历史正文片段）

逐条标签见配套 JSON；uncertain 表示现有证据不足，并非低价值。

### 21. aol.com

历史总量 **38**（BE 11，INTC 27）；实际审核 **38**；低价值 **8/38 = 21.1%**，有价值 3，待核验 27。抽样：全量。建议：**待复核**。聚合大量股评和情报一词误召回，也有芯片供应链消息；跨 ticker 封禁前复核。

典型低价值：
- [History Says Intel's Best Years Have Been Hard to Follow. 2026 Is Its Best in More Than 4 Decades. - AOL.com](https://news.google.com/rss/articles/CBMif0FVX3lxTFBQS0NRa3RqQjhLTDdZV0txTDdNbk5EcnpjMkFUcXplVFRpRmdKWXZ6NzlydkoyQkM2dFh2Q3loOWFVY1BORnVGZ1JEdWcyUU9oejRDWWJZR0VmRjJMRVo1MXRMLXpHS2o0MzU0S1hCWE9NLUZjb2JqVEZTd09QN3M?oc=5)（ID 4cd00b3663ab985f；标题/摘要证据）
- [Intel Stock Surged Over 40% in September. History Shows What's Next. - AOL.com](https://news.google.com/rss/articles/CBMiekFVX3lxTE9OS0pmc3NYMktUX2xvRnFUV1o0S21EUldnQlpBbTQzX1hxSTNQUGctWDkxbXFzcF9sdzhoWHZjTzlpUnpURS1vZEFSTlVZNUNFLWpaTS1QQUtBamZMZkhTb2UzRGJBcHhmYS0xMTFYaGpTaW9Fbjd3ZUFR?oc=5)（ID ad939a776a1877ac；标题/摘要证据）
有价值例外：
- [FuelCell Surges 11% on Fit Energy’s Pennsylvania Data Center Plan; Bloom Energy Climbs 4%, Plug Power Barely Budges - AOL.com](https://news.google.com/rss/articles/CBMifkFVX3lxTE1kX2dhM0p0U0pHbGVvM2tHWGlzbDRJQ1ZnUVlVSmpCZUpCdFJVbFdobzVtd0VLWnF0S2tkYzBxOFR1MURtMm5RU0NVVlNoTl8xcXhlM0R1WTNFNTdVeWZFNmx1eDZWdTVPNC01amdLYlhfLURjU3BzY1lMTDBDUQ?oc=5)（ID 7f2fc3c6de71c661；标题/摘要证据）

### 22. techpowerup.com

历史总量 **38**（BE 0，INTC 38）；实际审核 **38**；低价值 **9/38 = 23.7%**，有价值 25，待核验 4。抽样：全量。建议：**保留**。多为技术和新品信息，含工艺拆解、代工客户和工厂进度；小量旧规格和设备评测无经济催化，建议保留。

多为技术和新品信息，含工艺拆解、代工客户和工厂进度；小量旧规格和设备评测无经济催化，建议保留。

- 低价值（仅标题及 RSS 摘要，ID 7c3cf2a1cf95398d）：[Lenovo Introduces Its First Googlebook, Delivering Premium AI Experiences in a Sleek, Lightweight Design - TechPowerUp](https://news.google.com/rss/articles/CBMi0wFBVV95cUxQOF9aVkN2aEJCb21DMmpHVkN1M0tlS2diVHBqd3p5cjB4VmlSQUszdGRqU3ZZc1lESnpFTXNxSDRTRm1iQmEzTm90TmRoU09nWU02R2RDbERqUUd2dXRjTDN4ajNvbmJFd1R6eHhNU3JXeTZpY2k4eEREWlNvdmlUaVZvQ0NBWlBjQU5lc04tc2dkNVdZVEVrdlBadVJ5RUNYdVFGOUs1WHFwdEF2d2gxS0pnNG5CWEJBdDdCbW5Wa2dIVEFkN2FHbUZjS3RxZ2g4aXlj0gHYAUFVX3lxTE53a0tvWmRVVUZRNUZ3QlNYMTJyMVQ4VXFCQkF1Q2txTEFxRE1ZWEE1NS1XSGEyR1dmaWM3ZVZ4V2FzN2NtaFNvaElQN0tZel9BN2dqZEZ2MHVBX2RodXB2VlRVRVRzelJFelEzZF83MVF4SGd4RWFyanZDLWZhSl9oclRCODVPZHlUdEV1TnFybkN5N01rM2tBWWw3c1ZfaG4zYkVFYkZtbU9wUDcyRVNPRVJxR1ItcXZfTF82WWdKLW1KNEo2MVRqZzlqNHBoQmpGMFBQMzBLOQ?oc=5)
- 低价值（仅标题及 RSS 摘要，ID 3d1ede975a29bb4d）：[Intel Xeon E5-2666 v4 Specs - TechPowerUp](https://news.google.com/rss/articles/CBMia0FVX3lxTE5CM0UtQzhRWVJjMkRQa1N4NnB0a19ZSW5lN0pZWDlYV1dDQ3p4REstSVhkQ1lTUm5Sb1ZpTnZhVkhtWWhzM1YyakJfTHpkNzZpVURMMS1iSTlxb0pRSHhDcEhCVEdlcHl5dlYw?oc=5)
- 有价值例外（仅标题及 RSS 摘要，ID 4e56c9c7308be380）：[Panther Lake Teardown Shows Intel 18A Near TSMC N3E, Newer TSMC Nodes Still Ahead - TechPowerUp](https://news.google.com/rss/articles/CBMitgFBVV95cUxOeENucXk0X3dBWXgxb0FEWmJ4RXJ4dWRVZjBRQlBRNTRrZEZDZi1WV1NJZDZEeE42SVE2V1I4TndBZmVfMnl5bWo4ZWJ5dUQtb01pRUZIV0N0RTFZR1FweVBaNndsUkVFcEhwOTVnMHM4cjlqV3Q4Z0JLYVF6VVA0Qzg0SW1iWDhoZjBEdU9NckdJR3kya2QyUTAzblAwaEJ0SkJfalljazV4bVpZNzNCb2s4UDdhUdIBuwFBVV95cUxNdkVxSGNwdWlOQzFQYmZQNlNIcjRxRXZPWWNXZ3k2cUhRc3VkU3FhMXJvUHBtMzNKcFJ2bXJBa3JqYTJfeENiWmtGRFhqWWlqRHVNZGpqLWRHOE12NXo0Q1RPUXhqUVZDOTBlTG52bFBVZ0l3bFpidHFYbGJNQjB4X2xzSkdRWDl4N2JvTnBBMm00dlF5Yl9LbG52SUxEVWZ2cFk0QkZfQ0dGcG9SYzBnem0wdVVBN3J5azVn?oc=5)

### 23. theglobeandmail.com

历史总量 **37**（BE 18，INTC 19）；实际审核 **37**；低价值 **6/37 = 16.2%**，有价值 7，待核验 24。抽样：全量。建议：**保留**。转载含监管、数据中心电力合同及财报，常规行情之外存在实质事件。

**典型低价值**
- 1026a25c59dcfcc0：[2 Growth Stocks to Stash and 1 We Turn Down - The Globe and Mail](https://news.google.com/rss/articles/CBMixAFBVV95cUxPVkVXa2VjYU9kUlk4RjNMcEtoRC1MYklfdnhIZUQyWWdIMDhQVXhlTHVEbTFFV2NLZllqN19oTVhMOWEyYkhWUEtGeG1rQkYxSkNKVl9FdlZMUnkxT3oxbXdfVTRXb2lWRUd0Q3haeEQ0TFo0MGMzdXRVU2lzYUNfamZxSUJjeUxDWmJFYjdIN2R1N0Y0NElNc1BDaU5peE41aklqVFJXZE1JZ29COXY4V2FFTmFrSERSbEM4QzNfZC1VdVYy?oc=5)（标题/RSS摘要）
- b279659d10d915a0：[Generac, American Superconductor, Bloom Energy, Redwire, and Matrix Service Shares Skyrocket, What You Need To Know - The Globe and Mail](https://news.google.com/rss/articles/CBMingJBVV95cUxNRFRqVF91eFhkYmlMS1pLWHcybG1MdnZNbXFpQVp1RWlOX1JoRnFsR3RaaHQwVU1Uci1WZ21QUnBDb2ZFVnI2SGh5WmtKdU13YVJIV0pJX2xqcUg1dEpPalI1UFBhZWdhdkUxLWRWaEw3Vy02STAyQ2RFdnpMN0p6UTZhMXYzU2k3UWJKcmxGNUF4dTVJaHloVmJWZXg1R0dkaG5DMUNPQnBEdkFtNEhNMU9paTV4VU5sZEZyb1R0WGk5QUJsOGFBVUpIaHBnT3BjYjRPeG16VkVURk5RWm9wbmhVZ1h4Q2tueE5pZFp1aUVYZzl0bTBFTDI3ZXdlSjVxY1V4dVU4OXVwb2E5Mk0yTWxaQ1kyd0pWbkhRSlNR?oc=5)（标题/RSS摘要）

**有价值例外**
- 626a83c14af3e53d：[Luxshare Faces U.S. ITC Patent Probe on Data Center Power Technology - The Globe and Mail](https://news.google.com/rss/articles/CBMi4gFBVV95cUxPaXZWUDBrcFlqNGxvTWoxa1RMOUlETnVGd2dKMUF6MzBsbG85X3JXcXRpLXFOZUNCNnEyRlNIcFFqT3JyczhDWlBGVUw4UTgyLVVNQmw0S3Mzalo2Q3N5djAySm9SNnhqS0RReVBPQWtiMmZMajlTa1l5XzFVQTZKa0MwdFM2Z1lrTlpsNVNpQ3M1SE5vZFNNd2NvX1NLcG9PX1A1STJ3MGdLMEZqQ0tsRlFDN2pTbkVDY082S2xnSW9nUUFoUEN6QmNzeEQtVDZydHJSc1VOdXBNLVJUNnlxWU13?oc=5)（标题/RSS摘要）

逐条标签见配套 JSON；uncertain 表示现有证据不足，并非低价值。

### 24. pluang.com

历史总量 **36**（BE 28，INTC 8）；实际审核 **36**；低价值 **6/36 = 16.7%**，有价值 7，待核验 23。抽样：全量。建议：**保留**。含财报指引、数据中心电力项目及行情短讯。

典型低价值：
- [Bloom Energy shares fall 8% despite UBS raising price target to $350, dragging fuel cell stocks lower. - Pluang](https://news.google.com/rss/articles/CBMiqgFBVV95cUxOZVJ4V0FUc0FIX21zNVdEOEpTbjZIMHRaaVZHNW02ZU1EQlFVcnIwVWxTN29vN3UxV2JnTVNQVGdaM1VKUUpIM3dzU1BMeWhmbmh6QXpGeVJ2MXh1bV9yNFNKS0pEMTR4c0ZXOUt2MGwwZy1wS2lkX25sTDV4WUhyM0ZwM3FKSW5yNWFBV3JfMnR5b0NJS3JiM0FmVWtUa1R0RjZMRlNaM3JYZw?oc=5)（ID fb60f66047ae2aae；标题/摘要证据）
- [Intel's market value surpasses Coca-Cola and Pe... - Pluang](https://news.google.com/rss/articles/CBMikAFBVV95cUxNZGZVS2Q1LVEzOElqY2dBMGxuS2VKN2JMOVBTb0ptYmFuX19oeGd0aHdJVElJR3B4OEdJdXRVd1BqY2lvUDMwY2U0cmhEZmVidk15WXVWeUxFck9uc0VTbkFLTEZhUjZWeWxRLXJMaTU1RjdEZzJvNEU5em9JeFkyLUdscXV4RXRoQ1ViX2k0Wks?oc=5)（ID a5a07d49fd6f3962；标题/摘要证据）
有价值例外：
- [Bloom Energy posts record Q2 revenue, boosts ma... - Pluang](https://news.google.com/rss/articles/CBMif0FVX3lxTFBpTm0yQXgtZl9pV25JWDVNVFZnT1c5ZTBRSG9td0xhMEdFVjNKbHdKSHIyTDM3c1lQSjlFandna3M1UzFCWVRGcXM0VzNYQlpGelJob08taTRNd2ZPQllWdWV6dG9XWGM4cmppeDVWM2ExRERjcWNmRGlzT19wNGs?oc=5)（ID 1212a1e3d8262180；标题/摘要证据）

### 25. media.unisba.ac.id

历史总量 **35**（BE 35，INTC 0）；实际审核 **35**；低价值 **15/35 = 42.9%**，有价值 7，待核验 13。抽样：全量。建议：**待复核**。标题夹杂无关人物/热门词和重复标题，来源真实性与时效不足以判断；有合同/业绩标题但未核实是否原创或可靠，建议复核而非贸然跨 ticker 封禁。

标题夹杂无关人物/热门词和重复标题，来源真实性与时效不足以判断；有合同/业绩标题但未核实是否原创或可靠，建议复核而非贸然跨 ticker 封禁。

- 低价值（仅标题及 RSS 摘要，ID ab6a0c37effe3144）：[The $2,200 Options Mistake: Why Selling Puts On Bloom Energy Cost Me A $3,350 Day Sergio Bonelli (PsokFodbtq) - Unisba Media](https://news.google.com/rss/articles/CBMiZkFVX3lxTE1LbGRTYzNFdER3b1phQXR4RGMydm41SGFfNXNCSm5oZnNFOU1wZjFaTm83VEwzSFhDcFlKS09jQ1NRRElNaWRBeER4Q29yeU90LThFcjk2cUJ3d1VZdnA4Z2hCd3U4QQ?oc=5)
- 低价值（仅标题及 RSS 摘要，ID 7246812ced7c030b）：[Bloom Energy 1.07B Revenue, Stock Down 40% – Buy Now! BE Stock Analysis Vancouver Vs Fc Dallas (OBUbWBjvOM) - Unisba Media](https://news.google.com/rss/articles/CBMiggFBVV95cUxOUkRHUnpXSERtbUctSEdzUm90blNvV1NrQXpOanMteUZFVmstNDZqRDVGZkpBVWxQaDhybHVmSGJRNkxHM2M2ZjNuYjJIZ3ZWUlJQMUJFTlRwZmtla2ZlUXg5VnBpdjBXVDJ4dU53MjR4dnZXYjliOTRxcnRZQUlxMUhn?oc=5)
- 有价值例外（仅标题及 RSS 摘要，ID 083fac89b72eccfd）：[Bloom Energy Corporation (BE) Earnings: $1.1B Revenue, $0.62 EPS — Full Same-Day Breakdown Scott Foley (lO6ssaE0VP) - Unisba Media](https://news.google.com/rss/articles/CBMiggFBVV95cUxNeTNKSDJCelo2X2hfNFR3dThneEVneW44NkxfUF9mNzlCLWNraERTWXJmNnZtcnZMeDNieHlYT1hkMkExeFNYMnQxRXJ5QjdqY3dySkR0ZllVYWxSTkZBNUlPT0tIb24yMGw4NENLYnZmWFZ4MlliQ29YN0F2aEc2NlZn?oc=5)

### 26. kalkinemedia.com

历史总量 **32**（BE 28，INTC 4）；实际审核 **32**；低价值 **1/32 = 3.1%**，有价值 1，待核验 30。抽样：全量。建议：**待复核**。标题多为泛化数据中心电力题材和投资讨论，标题不足以判断分析深度；业绩指引例外使全局封禁待复核。

**典型低价值**
- 4ed2be7f67347ce1：[Why Pioneer AI Foundry (CBOE:JPEG) Is in Focus After Its Latest Board Appointment? - Kalkine Media](https://news.google.com/rss/articles/CBMi0wFBVV95cUxNY0RMOGpNdzRQc3hWMHRiSnl4Q3NJaXpOclRFWDJkM1NyMUFoZFZudl9NWXNhcm1kMHVxZ29xSWVQS3FoQXRGbkFTX29fWFd6dHY5bWE3eDJDZ1I0WElCQS1tNm44bnQwWnRGdElmaGlQTmkzalBlWFNPT2xnUjJZMHRwNE1rQVl2Qm9RSGl2dFVhU29UTjhkWGY4NGxLZEdXbjZaSjVrRklOQ3BRaE5Xd3NiQ3BQQjkyZ2ZMYzYwQnFKWHN1TmlyRm55QWp5T25KZnhn?oc=5)（标题/RSS摘要）

**有价值例外**
- cc474061556fec4c：[Why Is Bloom Energy (NYSE BE) Raising Its 2026 Growth Outlook? - Kalkine Media](https://kalkinemedia.com/us/stocks/growth/why-is-bloom-energy-nyse-be-raising-its-2026-growth-outlook)（标题及历史正文片段）

逐条标签见配套 JSON；uncertain 表示现有证据不足，并非低价值。

### 27. tomshardware.com

历史总量 **32**（BE 0，INTC 32）；实际审核 **32**；低价值 **12/32 = 37.5%**，有价值 14，待核验 6。抽样：全量。建议：**保留**。导购不少，也有 Intel 工厂、事故、制程和竞品技术报道。

典型低价值：
- [CPU Price Index 2026 — the lowest price on every PC processor today - Tom's Hardware](https://news.google.com/rss/articles/CBMiYEFVX3lxTFBjT2k3djBvR2F5TVFfcXllZzlLc2tNMm5oVTBtU3AwbWl3OWdkN2hiN1FFMlZlVG01Y3lNUlZZYmhrQkU1WERxbDkteUN2RlFlQjFTU3o2STdJeWh6Smdlaw?oc=5)（ID 93f39e2ad0fb323d；标题/摘要证据）
- [It's finally a good time to buy a Raptor Lake CPU during Prime Big Deals Day — chips drop to all-time low prices as inventory seemingly stabilizes - Tom's Hardware](https://news.google.com/rss/articles/CBMimwJBVV95cUxQYW50OXlDcGx2WmRPZ0hIbFFCbTJscUpUNm00QWQyWTN2cmVSRnFPSGtpMUItcjZueVdVWC03REx1RXZfTmRxeXR1OGlXaE5RRnZ5NXdCZFBJemFqb3hZLUV0elpycUhVRFFQRXRJTEdYc1pvZTRFT1NGZ09YaEhSeTNmakhIeXBfaS1rZm1lTEJPV2dBazU2djQ1eEQ4SEJNeFNzeWNPdnNsVldiZ2ZNSm42eThUVUwzUmc5TWVscjI1RXhxUTY0NWtVRl81V1lEMTVzQm1NTG1HVnBIdmZ0c1BDRnMwNExuN3l5a1R2WFlsaEhZSHRTaWV0VnAzUGY1WVdUUUwwcG91RExjS2pSTHpFUnZXWEI5V3Rj?oc=5)（ID 0193dd4edcc6a5aa；标题/摘要证据）
有价值例外：
- [Intel's next-gen Nova Lake platforms pass compliance at USB and PCIe standards bodies as launch looms — PCI-SIG and USB-IF give their stamp of approval - Tom's Hardware](https://news.google.com/rss/articles/CBMiwgFBVV95cUxOS1FJQmVmOW1tSGE2V1o0Ry11cTR0U1U3YjQzUkNQSTNwa1hCZWp1SWhqN1FROXJrMzJVOXNfREpWSmFnZXgyQmFEQ2tEZFYyUHR4Y3h4T3V4YktWY1RHRDR4YUJhNFBteERnM1ZzeWlNdGxwZElVSzBVb2h1SUo0cW1HZ0dlVy00em90d0N5cnZlV0VwVUhScG9PUnlZajYycUs3U2JFV1ZTSWJ3V0hFamZnbThzSUJoOHBDTHRXeHJ5dw?oc=5)（ID 15adf7900dc1c91f；标题/摘要证据）

### 28. gurufocus.com

历史总量 **30**（BE 19，INTC 11）；实际审核 **30**；低价值 **10/30 = 33.3%**，有价值 17，待核验 3。抽样：全量。建议：**保留**。估值模板和日内价格文章频繁，但有竞品 CPU 客户、具体评级、财报与现金流分析；建议保留。

估值模板和日内价格文章频繁，但有竞品 CPU 客户、具体评级、财报与现金流分析；建议保留。

- 低价值（仅标题及 RSS 摘要，ID f1ff44d433cc5ae5）：[Bloom Energy Corp (BE) Shares Fall 8.9% -- What GF Score of 69 T - GuruFocus](https://news.google.com/rss/articles/CBMirwFBVV95cUxQU2M4cUVOc0p5RFRGQmsydnN2OGk3VEFyX0UyZ3hpVjBGN2Fha1k4bHB5MW5NYWc0eGNZSEtaa1U0U0JoaGU2elBtSE5ocG1ERlNhU2kwZ3FETzg5OWpsWUdNMFZBQ1BOcFlyb185emNZeHN0RUNQdDl1VGt1Y0tvdlI2aWxJb3IxdlZON19MRHBrY2F3TjQ1QUlhZUxIZi1mT0NvYkRnQlJDSElxTzU0?oc=5)
- 低价值（仅标题及 RSS 摘要，ID 17adc8e9571f629e）：[Intel, AMD Lead 5 Biggest Stock Winners in September - GuruFocus](https://news.google.com/rss/articles/CBMilAFBVV95cUxQTk9hakNKV1daYXNqQkI4bmRYWVpQZy1iSEpkUlhSNFJtYmZFWWo0c09RR2hDcy12YjVXenBwaVM5ckY5TGxBVlpYb05Vc1hIMWVXTmJUY08xV0RCbGpZaldNZ1Q4WVlHRmRKQlA1WHJORHNpbkRMbmk0SUJYZF80VEtHR1RZM1g2UFhqREdvQTM1eTdL?oc=5)
- 有价值例外（仅标题及 RSS 摘要，ID 38a371ea6fc57721）：[Intel Stock Falls Over 4.8% as OpenShell Preserves an x86 Route - GuruFocus](https://news.google.com/rss/articles/CBMiogFBVV95cUxNbFo1SUd4UWxPRnVkeDgxOGFheGFIbFVBU0lHQm96b1FWYmNGeUxnSU1pWVZTMnhoSE9FazFMWTdvZ1dkdUtFWVRZREpYbUYzYWhxUVk1UURURmJVZDZZMVhrZ1E0Z0VzVkNycHNZV1U1OFY3dEFzZV9NSVdJMlV1OThKYkQwQlBidTdmYWY0aVo4a1BQbzNLNGh0QUxsSk5uMWc?oc=5)

### 29. pcguide.com

历史总量 **28**（BE 0，INTC 28）；实际审核 **28**；低价值 **27/28 = 96.4%**，有价值 1，待核验 0。抽样：全量。建议：**待复核**。绝大多数为电脑零售促销，但 Amazon CPU 销售排名提供竞争格局的有限观察；是否可替代及跨 ticker 损失待复核。

**典型低价值**
- 4a0ec79c91ce854b：[Dell RTX 5050 gaming laptop with 240H and 32GB DDR5 RAM is worth checking out at its 30-day low price - PC Guide](https://news.google.com/rss/articles/CBMizAFBVV95cUxPMHJZTXIyRktHOVFEaFhoSmdEYWMybzN3TWdXVW94cElpekVvd2s0NEJueDFhUG83NjdpTWhSeEF5a0xBZzVqUVdLTnRkU1dxLWVnMGJ2Y2x6Z0pDN3dsN0NUVjJFZVcwcWxUbkpPeFBKUkFRNzQ4emxnZTBDMU9CVFlOUGs0Z293TlJWblpVOTRKTUpkcG4xYmJhSl9oZV8wWktLVVU5V0ZyOFl0blE2VG9uTERjcnFCc0w2Zm1vTkRfTlc5bmJuYkk0dFI?oc=5)（标题/RSS摘要）
- 6ddcdf5b7c2081ad：[RTX 5070 and Intel Core i9 14900KF PC with 32GB of RAM drops to just $1,900 in this phenomenal deal for $600 off the MSRP! - PC Guide](https://news.google.com/rss/articles/CBMi4wFBVV95cUxOSDJid0FPMW4xRU9kXzNGUDJkRXd1QzMyVEduNFh1MmpzN1FCQ203LVpiYVBpMDlQeUdYZHBRTUFqcjVBbU54T29iWjNDekdiU3hMVkdXLTZMQW5Rdy1xd0FTYnZQSWYxZTVHbmlsZnBSQVFLUm9tc0NPNm5sSXNwY1FXejl0ZFE5b2NKX01QaDhfOXozZ3l6Mjg5WUNoQmpuM0VWbzFzZTg0Um5acEdRWmVGTW1zLU96aDY3bWl0b3dudS05dkZoRmNCdktsbVB6SENrY2JGdl9KeGRESVZkNHpDcw?oc=5)（标题/RSS摘要）

**有价值例外**
- aaa1f822d3f72ffd：[AMD continues to dominate Amazon CPU sales, with only one Intel processor in the top 10, and it's a last-gen model - PC Guide](https://news.google.com/rss/articles/CBMi2AFBVV95cUxPNDlBbTBsWndfTmwwMWZPMTNSbVdvMGxFZkJnQWFkSkduenlXZGVPczhGdEFJSmdlQ254UGY5elR4Nngwc0NzR1M1U0piZXl4Q3YzcFd5Qmx1bUJDRFhmZVBKX285dHdTem1Db2h6aDlWQVVoT2FKTmIzYTN5WUplRmROM0dZOUhuU29IQlB6LWRaQ1lzcU9IUFd0ZDdydmx4VUs4YjJVakdBWFlRdm1VUkYybVY2a09NUGxyYzRGcHhjT09sZjJqR1FYeWFUa0tJUjgxdERDT1I?oc=5)（标题/RSS摘要）

逐条标签见配套 JSON；uncertain 表示现有证据不足，并非低价值。

### 30. phoronix.com

历史总量 **28**（BE 0，INTC 28）；实际审核 **28**；低价值 **0/28 = 0.0%**，有价值 13，待核验 15。抽样：全量。建议：**保留**。包含服务器、AI 软件和产品的一手技术跟踪。

典型低价值：
- 未见。
有价值例外：
- [NVIDIA Vera SMT Performance Benchmarks Review - Phoronix](https://news.google.com/rss/articles/CBMiW0FVX3lxTE9kNDRDUFN3a0ZvV2pkd1AyeW14aFZpR201OHF3bVBFYnVRMnNYa0lEQnFhVVZfY3NhNklzVnVrbFVrYlRORG5kaTNnRVRmeDlOSFR6dnpmbGdLV1E?oc=5)（ID cd9e4d0e503034fc；标题/摘要证据）

## 六、证据与可复核材料

- [全部 Site 统计](../eval/google_news_blacklist_audit_20261027/ranking.csv)、[4,641 条去重召回原始明细](../eval/google_news_blacklist_audit_20261027/articles.json)、[逐条标签与站点评估](../eval/google_news_blacklist_audit_20261027/combined-review.json)。

- 每日首轮、拆词及滚动边界查询：`scan.json`、`supplement.json`、`supplement-retry.json`、`boundary.json` 和 `rss/` 原始 XML；`prepare.py` 构建去重域名统计；三组原始样本及子 Agent 详细结论见 `original-agent-*.json/.md` 与 `agent-*-review.json/.md`，增量在 `agent-*-extra*.json/.md`，On3 余样在 `on3-remaining*`。

- 本轮完成样本 ID 与输入集合、额外样本 ID、跨组站点覆盖、标签取值及封禁站全量性校验；`evidence-manifest.json` 保留主要文件哈希。子 Agent 提供的潜在有价值例外为召回标题或正文片段证据，不代表独立验证该主张真实、首发或独家。

- 历史扫描与只读审核未改动消息源配置、消息、正文或过滤策略。生产当前隐藏域名/发布者列表为空；本次候选只供用户审阅。
