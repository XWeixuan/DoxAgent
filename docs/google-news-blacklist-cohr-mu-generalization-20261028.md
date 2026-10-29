# Google News RSS 黑名单扩充、首搜索词查询与 COHR / MU 泛化评估

日期：2026-10-28。本轮代码及名单在本地完成，未提交、未发布生产；统计读取公开 RSS 与只读监测词配置，没有抓取正文、抽样或组装案例 JSON。

## 本轮变更

Google News RSS 对所有 ticker 固定只用首个搜索词，统一监测词路径取第一个 L1 的源语言表达，旧配置路径取 search_terms[0]；不再自动添加 ticker，也不使用其余词。多词短语按原规则加引号。未设置首词时明确报 CONFIG_INCOMPLETE，不回退成裸 ticker 或无关键词的广泛新闻检索。其他消息源不变，预览、查询计划、实际请求及 query_rule=first_l1_only 一致；计划不再声称使用 ticker_symbol。

新增判黑：pluang.com、tradersunion.com、foreignpolicyjournal.com、kalkinemedia.com、stockstotrade.com、timothysykes.com、trefis.com、tokenpost.com、kalkine.ca、maxpreps.com。累计22个判黑 Site，23个匹配域（含 AOL 的 aol.co.uk）；本域及下级子域匹配，保留域名边界，仍仅作用于 Google News RSS，在正文前过滤。

上一轮30站中其余20站按用户明确决定加入人工白名单；当前共32站，记录于 google-news-search-site-whitelist-20261028.md。白名单是人工判断记录，不是运行时 allowlist，未知 Site 仍正常接纳。策略 JSON 按进程缓存，后续发布需更新并重启加载策略的组件；本轮没有热加载或生产配置写入。

## 查询与统计口径

读取生产首词：COHR=`Coherent Corp`（统一词revision 1），MU=`Micron`（revision 5），两者当前均未绑定 Google 源，本轮是独立离线历史查询。每 ticker 30个日窗口，共60次正式 RSS 查询，查询分别为 `"Coherent Corp" after:日期 before:下一日` 与 `Micron after:日期 before:下一日`。`hl=en-US&gl=US&ceid=US:en`；没有 OR ticker、额外词分片或正文访问。

检索日期为2026-09-28至2026-10-27，发布时间严格保留从 2026-09-28T00:00:00+00:00 至 2026-10-28T00:00:00+00:00（不含）的记录。本轮抓取时点为 2026-10-28T12:06:50.482485+00:00（北京时间 2026-10-28T20:06:50.482485+08:00），查询窗口包含30个已完成UTC日，但不代表消息召回完整。Google 日期检索与pubDate有偏差，合并全部列表后统一裁剪全局范围，避免先逐日裁剪丢掉相邻日期结果。

按 GUID 去重，再合并同一归一化 Site、同一标题（空白/大小写/HTML实体归一）和同一 UTC 发布日期的记录；不同 Site 转载保留，跨ticker同文只在合并分母计一次。Site按本地PSL注册域归一，学校媒体子域保持特定边界；黑名单按原发布host匹配，避免扩大到整所学校。白名单 finance.yahoo.com 按 yahoo.com 注册域比较。未逐篇解码跳转或取正文，因此不同地区URL且RSS日期不同的副本仍可能残留，不能假装完成全网canonical去重。

黑名单命中比例分母是去重后的全部召回消息，白名单不会从此分母剔除。Top20主榜也先不剔除黑白名单，只对每站标注分类；另给“仅黑名单拦截后”的Top20（仍保留白名单）查看剩余未判定站。相同消息数按域名字母排序，严格取20站。

## 泛化统计

|范围|去重消息|原黑名单命中|新增10站命中|累计拦截|累计比例|过滤后剩余|
|---|---:|---:|---:|---:|---:|---:|
|COHR|157|26|7|33|21.02%|124|
|MU|1595|158|47|205|12.85%|1390|
|combined|1752|184|54|238|13.58%|1514|

|范围|Top20黑名单站|Top20白名单站|Top20未判定站|拦截后Top20未判定站|
|---|---:|---:|---:|---:|
|COHR|3|12|5|6|
|MU|5|13|2|4|
|combined|5|13|2|4|

## Top20 中的未判定 Site

|范围|原始Top20：Site（消息数）|黑名单拦截后Top20：Site（消息数）|
|---|---|---|
|COHR|chartmill.com（9）、fortunebusinessinsights.com（3）、simpletechtrend.com（3）、globenewswire.com（2）、morningstar.com（2）|chartmill.com（9）、fortunebusinessinsights.com（3）、simpletechtrend.com（3）、globenewswire.com（2）、morningstar.com（2）、scanx.trade（2）|
|MU|syracuse.com（17）、marketwatch.com（13）|syracuse.com（17）、marketwatch.com（13）、chosun.com（10）、digitimes.com（10）|
|combined|syracuse.com（17）、marketwatch.com（13）|syracuse.com（17）、marketwatch.com（13）、chartmill.com（12）、chosun.com（10）|

## 查询覆盖与边界

|Ticker|首词|RSS原始行|去重消息|站点数|100条触顶日|请求失败|
|---|---|---:|---:|---:|---:|---:|
|COHR|Coherent Corp|212|157|50|0|0|
|MU|Micron|2067|1595|243|13|0|

MU触顶日：2026-09-28、2026-09-29、2026-09-30、2026-10-01、2026-10-08、2026-10-11、2026-10-16、2026-10-19、2026-10-20、2026-10-21、2026-10-22、2026-10-26、2026-10-27。COHR未触顶。触顶会使MU历史召回及Site分布不完整，不能把本次比例当成全网精确比例或真实生产日均。

GUID 唯一 1765 条，同站同标题同发布日期额外合并 13 条；无发布域名的原始行 0 条。最终合并 1752 条、259 个Site，其中白名单 1084 条，未判定 430 条、210 个Site。

## 结论

累计黑名单在这两个新增验证ticker上命中 238/1752 条（13.58%），本轮新增10站贡献 54 条、增加 3.08 个百分点，说明既有站点治理可以跨ticker减少一批召回消息。收益强度存在差异：COHR 21.02%，MU 12.85%。这是消息数量收益，不能直接换算成服务器成本同比降幅。

合并原始Top20有 2 个未判定Site，黑名单拦截后Top20有 4 个；但长尾仍有 210 个未判定Site、430 条消息。名单覆盖了部分高频站点，不能据此认定剩余消息都高质量或噪音已清除。未判定不代表低质量；本轮不增加自动判黑，不做质量审核。

## 完整 Top20 榜单

### COHR：过滤前

|排名|Site|消息|占原始召回比例|分类|COHR|MU|
|---:|---|---:|---:|---|---:|---:|
|1|tradingview.com|19|12.10%|白名单|19|0|
|2|yahoo.com|17|10.83%|白名单|17|0|
|3|ad-hoc-news.de|11|7.01%|黑名单|11|0|
|4|chartmill.com|9|5.73%|未判定|9|0|
|5|gurufocus.com|9|5.73%|白名单|9|0|
|6|tradingkey.com|7|4.46%|黑名单|7|0|
|7|benzinga.com|6|3.82%|白名单|6|0|
|8|stocktitan.net|6|3.82%|白名单|6|0|
|9|247wallst.com|5|3.19%|白名单|5|0|
|10|simplywall.st|5|3.19%|黑名单|5|0|
|11|theglobeandmail.com|5|3.19%|白名单|5|0|
|12|seekingalpha.com|4|2.55%|白名单|4|0|
|13|fortunebusinessinsights.com|3|1.91%|未判定|3|0|
|14|investing.com|3|1.91%|白名单|3|0|
|15|quiverquant.com|3|1.91%|白名单|3|0|
|16|simpletechtrend.com|3|1.91%|未判定|3|0|
|17|globenewswire.com|2|1.27%|未判定|2|0|
|18|marketscreener.com|2|1.27%|白名单|2|0|
|19|moomoo.com|2|1.27%|白名单|2|0|
|20|morningstar.com|2|1.27%|未判定|2|0|

### COHR：黑名单拦截后

|排名|Site|消息|占原始召回比例|分类|COHR|MU|
|---:|---|---:|---:|---|---:|---:|
|1|tradingview.com|19|12.10%|白名单|19|0|
|2|yahoo.com|17|10.83%|白名单|17|0|
|3|chartmill.com|9|5.73%|未判定|9|0|
|4|gurufocus.com|9|5.73%|白名单|9|0|
|5|benzinga.com|6|3.82%|白名单|6|0|
|6|stocktitan.net|6|3.82%|白名单|6|0|
|7|247wallst.com|5|3.19%|白名单|5|0|
|8|theglobeandmail.com|5|3.19%|白名单|5|0|
|9|seekingalpha.com|4|2.55%|白名单|4|0|
|10|fortunebusinessinsights.com|3|1.91%|未判定|3|0|
|11|investing.com|3|1.91%|白名单|3|0|
|12|quiverquant.com|3|1.91%|白名单|3|0|
|13|simpletechtrend.com|3|1.91%|未判定|3|0|
|14|globenewswire.com|2|1.27%|未判定|2|0|
|15|marketscreener.com|2|1.27%|白名单|2|0|
|16|moomoo.com|2|1.27%|白名单|2|0|
|17|morningstar.com|2|1.27%|未判定|2|0|
|18|scanx.trade|2|1.27%|未判定|2|0|
|19|tikr.com|2|1.27%|白名单|2|0|
|20|barchart.com|1|0.64%|白名单|1|0|

### MU：过滤前

|排名|Site|消息|占原始召回比例|分类|COHR|MU|
|---:|---|---:|---:|---|---:|---:|
|1|yahoo.com|320|20.06%|白名单|0|320|
|2|fool.com|107|6.71%|白名单|0|107|
|3|tradingview.com|77|4.83%|白名单|0|77|
|4|moomoo.com|74|4.64%|白名单|0|74|
|5|seekingalpha.com|73|4.58%|白名单|0|73|
|6|investing.com|52|3.26%|白名单|0|52|
|7|ad-hoc-news.de|46|2.88%|黑名单|0|46|
|8|247wallst.com|43|2.70%|白名单|0|43|
|9|benzinga.com|41|2.57%|白名单|0|41|
|10|aol.com|35|2.19%|黑名单|0|35|
|11|barrons.com|34|2.13%|白名单|0|34|
|12|tradingkey.com|31|1.94%|黑名单|0|31|
|13|cnbc.com|28|1.75%|白名单|0|28|
|14|biggo.com|26|1.63%|黑名单|0|26|
|15|gurufocus.com|24|1.50%|白名单|0|24|
|16|pluang.com|24|1.50%|黑名单|0|24|
|17|syracuse.com|17|1.07%|未判定|0|17|
|18|theglobeandmail.com|17|1.07%|白名单|0|17|
|19|marketscreener.com|15|0.94%|白名单|0|15|
|20|marketwatch.com|13|0.81%|未判定|0|13|

### MU：黑名单拦截后

|排名|Site|消息|占原始召回比例|分类|COHR|MU|
|---:|---|---:|---:|---|---:|---:|
|1|yahoo.com|320|20.06%|白名单|0|320|
|2|fool.com|107|6.71%|白名单|0|107|
|3|tradingview.com|77|4.83%|白名单|0|77|
|4|moomoo.com|74|4.64%|白名单|0|74|
|5|seekingalpha.com|73|4.58%|白名单|0|73|
|6|investing.com|52|3.26%|白名单|0|52|
|7|247wallst.com|43|2.70%|白名单|0|43|
|8|benzinga.com|41|2.57%|白名单|0|41|
|9|barrons.com|34|2.13%|白名单|0|34|
|10|cnbc.com|28|1.75%|白名单|0|28|
|11|gurufocus.com|24|1.50%|白名单|0|24|
|12|syracuse.com|17|1.07%|未判定|0|17|
|13|theglobeandmail.com|17|1.07%|白名单|0|17|
|14|marketscreener.com|15|0.94%|白名单|0|15|
|15|marketwatch.com|13|0.81%|未判定|0|13|
|16|thestreet.com|12|0.75%|白名单|0|12|
|17|tipranks.com|12|0.75%|白名单|0|12|
|18|barchart.com|11|0.69%|白名单|0|11|
|19|chosun.com|10|0.63%|未判定|0|10|
|20|digitimes.com|10|0.63%|未判定|0|10|

### combined：过滤前

|排名|Site|消息|占原始召回比例|分类|COHR|MU|
|---:|---|---:|---:|---|---:|---:|
|1|yahoo.com|337|19.23%|白名单|17|320|
|2|fool.com|107|6.11%|白名单|0|107|
|3|tradingview.com|96|5.48%|白名单|19|77|
|4|seekingalpha.com|77|4.39%|白名单|4|73|
|5|moomoo.com|76|4.34%|白名单|2|74|
|6|ad-hoc-news.de|57|3.25%|黑名单|11|46|
|7|investing.com|55|3.14%|白名单|3|52|
|8|247wallst.com|48|2.74%|白名单|5|43|
|9|benzinga.com|47|2.68%|白名单|6|41|
|10|tradingkey.com|38|2.17%|黑名单|7|31|
|11|aol.com|36|2.06%|黑名单|1|35|
|12|barrons.com|34|1.94%|白名单|0|34|
|13|gurufocus.com|33|1.88%|白名单|9|24|
|14|cnbc.com|28|1.60%|白名单|0|28|
|15|biggo.com|27|1.54%|黑名单|1|26|
|16|pluang.com|26|1.48%|黑名单|2|24|
|17|theglobeandmail.com|22|1.26%|白名单|5|17|
|18|marketscreener.com|17|0.97%|白名单|2|15|
|19|syracuse.com|17|0.97%|未判定|0|17|
|20|marketwatch.com|13|0.74%|未判定|0|13|

### combined：黑名单拦截后

|排名|Site|消息|占原始召回比例|分类|COHR|MU|
|---:|---|---:|---:|---|---:|---:|
|1|yahoo.com|337|19.23%|白名单|17|320|
|2|fool.com|107|6.11%|白名单|0|107|
|3|tradingview.com|96|5.48%|白名单|19|77|
|4|seekingalpha.com|77|4.39%|白名单|4|73|
|5|moomoo.com|76|4.34%|白名单|2|74|
|6|investing.com|55|3.14%|白名单|3|52|
|7|247wallst.com|48|2.74%|白名单|5|43|
|8|benzinga.com|47|2.68%|白名单|6|41|
|9|barrons.com|34|1.94%|白名单|0|34|
|10|gurufocus.com|33|1.88%|白名单|9|24|
|11|cnbc.com|28|1.60%|白名单|0|28|
|12|theglobeandmail.com|22|1.26%|白名单|5|17|
|13|marketscreener.com|17|0.97%|白名单|2|15|
|14|syracuse.com|17|0.97%|未判定|0|17|
|15|marketwatch.com|13|0.74%|未判定|0|13|
|16|barchart.com|12|0.69%|白名单|1|11|
|17|chartmill.com|12|0.69%|未判定|9|3|
|18|thestreet.com|12|0.69%|白名单|0|12|
|19|tipranks.com|12|0.69%|白名单|0|12|
|20|chosun.com|10|0.57%|未判定|0|10|

## 验证与文件

98项相关回归通过，覆盖首词查询、SNOW/CEG歧义代码不再自动添加、空首词不放大召回、10个新增域名及子域、Google专属过滤、旧/统一词路径、计划预览与接纳/正文入队。Ruff通过。60份真实RSS原文哈希、查询语义、分类分区和Top20排序统计校验通过。

`eval/google_news_cohr_mu_governance_20261028/` 保存只读词配置快照、60份rss原文、scan.json、去重明细articles.json、summary.json、validation.json及复算脚本；这些是内部统计证据，不是抽样案例包。

[统计CSV目录](../exports/google-news-cohr-mu-blacklist-generalization-20261028/) 包含summary.csv、三个原始Top20、三个拦截后Top20、unreviewed-top20-sites.csv、blacklist-hits.csv。没有正文抓取、抽样或案例JSON组装。
