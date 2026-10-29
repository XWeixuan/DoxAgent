# Google News RSS 五 ticker Top 30 Site 历史抽取报告

日期：2026-10-28。仅历史数据抽取和随机案例包，未新增黑名单、未改变订阅或发布生产。

## 时间与查询口径


正式查询每 ticker 30次，共150次，日期条件为2026-09-28至2026-10-27，`after:日期 before:下一日`，`hl=en-US&gl=US&ceid=US:en`。使用当前统一监测词的首个英文 L1，加上 ticker 代码 OR；不是此前全部三个词的宽召回。

严格保留发布时间从 2026-09-28T00:00:00+00:00 起、截至 2026-10-27T17:32:11.067063+00:00 的已发生消息；末个 UTC 日尚未完成，观察截止为北京时间2026-10-28 01:32:11，不把未完成日或未来时间记录算成完整日。Google 按检索日期返回的记录与 pubDate 不完全对齐，因此合并全部150个列表后统一按发布时间裁剪，没有在每个小窗口先丢弃相邻日期记录。

有26次正式查询返回100条上限，其中 SNOW 23次、其他三个 ticker 各1次；ONDS没有触顶。结果是可复现的历史召回样本，不是完整互联网新闻总量。早期时间戳参数试探返回空结果并已废弃，未进入统计。

## 去重、排除与抽样

先按 Google GUID 去重，再合并同一 PSL Site、完全相同标题（空白/大小写/HTML实体归一）及同一 UTC 发布日期的重复记录；可验证的真实文章 URL 亦检查去重，保留有语义的查询参数。跨 ticker 命中只计一篇，ticker 计数允许一篇多计。不同 Site 的转载各自保留，便于衡量站点噪音。

Site 用随已安装 tld 库附带的 PSL 归一到注册域名；学校媒体 media.unisba.ac.id 保留特定子域边界。黑名单取当前 google_news_blocklist.json，白名单取20261028人工文档并按注册域归并（finance.yahoo.com归入yahoo.com）；先排除，再对剩余 Site 排名。

随机种子为 `20261028:<Site>`，按稳定文章 ID 排序后无放回随机抽样。不因正文抓取失败换样，不人工偏选低价值文章。既有黑白名单未修改；排名不等于封禁建议。

## 正文状态与限制

新增网页正文是本次抓取时的页面版本，不保证等于发布时点快照；历史存储正文仅在发布域名匹配时复用。未用 RSS 摘要填补缺失正文。

- stored_FULL_unverified：生产消息总线原存储标为 FULL 的正文，本次未逐篇证明全文完整。
- http_extracted_unverified / http_impersonated_extracted_unverified：原站 HTTP 文本提取，无主动长度截断；可能仍有页面噪声或漏段。
- reader_extracted_unverified：原站轻量 Reader 提取文本，截去可识别导航和推荐页尾；不保证每篇完整。
- partial_original_preview：确认为原站预览/摘要区域，保留真实片段但不冒充全文。
- blocked_page、http_*、fetch_*、no_article_body、article_mismatch 等且 body=null：未获得可用原文；不是空白文章，不得按有完整正文计算。
- video_caption_only / video_metadata_only / newsletter_link_only / navigation_shell / interactive_model_shell：只有视频说明、页面入口或非正文组件，body=null。不能用 RSS 摘要填充。

原站、Reader 后仍无法读取的阻断已保留；另对 MensJournal、TradersUnion、Legacy 做了一次本地临时浏览器验证，未使用或修改生产浏览器身份。不得将“HTTP 200”或 body 非空等同于全文验收。

SNOW 裸词可召回天气/滑雪/同名人物，CEG 可召回 Construction Equipment Guide 的自称缩写。这是 ticker 搜索歧义，不能仅据该股票下无关就把网站一概判为跨 ticker 低质。



## 召回汇总

|Ticker|正式查询|原始RSS行数|去重后消息|触顶查询|
|---|---:|---:|---:|---:|
|RKLB|30|1026|781|1|
|ONDS|30|402|278|0|
|NBIS|30|1374|1044|1|
|CEG|30|756|582|1|
|SNOW|30|2649|2089|23|

五 ticker 合并：GUID 唯一 4811 篇；再合并同站同标题同发布日期重复 38 篇，URL额外合并 0 篇，最终 4773 篇。黑名单排除 422 篇，白名单排除 1404 篇，余 2947 篇、1054 个 Site；Top 30 共 995 篇。

抽样 576 篇；body非空 434 篇（包含原站预览，未经逐篇全文验收），未取得可用原文 142 篇。不能把此非空比例当成生产正文补全成功率。

## Top 30 排名

|排名|Site|总量|RKLB|ONDS|NBIS|CEG|SNOW|抽样|body非空|
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
|1|constructionequipmentguide.com|75|0|0|0|75|0|20|20|
|2|tipranks.com|69|29|3|32|5|0|20|20|
|3|snowflake.com|66|0|0|0|0|66|20|20|
|4|snowbrains.com|63|0|0|0|0|63|20|19|
|5|pluang.com|58|15|8|28|7|0|20|20|
|6|gurufocus.com|54|13|6|26|7|2|20|0|
|7|mensjournal.com|54|0|0|0|0|54|20|0|
|8|weather.com|53|0|0|0|0|53|20|20|
|9|stocktwits.com|38|16|8|9|5|0|20|20|
|10|tradersunion.com|36|12|2|13|8|1|20|2|
|11|cbsnews.com|33|0|0|0|1|32|20|20|
|12|theglobeandmail.com|30|12|1|9|5|3|20|14|
|13|foreignpolicyjournal.com|29|21|1|4|3|0|20|20|
|14|opensnow.com|29|0|0|0|0|29|20|20|
|15|kalkinemedia.com|25|10|0|1|13|1|20|20|
|16|alaskasnewssource.com|24|0|0|0|0|24|20|20|
|17|autoblog.com|22|0|0|0|0|22|20|0|
|18|stockstotrade.com|21|0|12|8|0|1|20|20|
|19|timothysykes.com|20|0|9|10|0|1|20|20|
|20|trefis.com|20|6|4|0|6|4|20|20|
|21|9news.com|19|0|0|0|0|19|19|0|
|22|denvergazette.com|19|0|0|0|0|19|19|19|
|23|legacy.com|19|0|0|0|0|19|19|0|
|24|powder.com|19|0|0|0|0|19|19|0|
|25|nbcmontana.com|18|0|0|0|0|18|18|18|
|26|tikr.com|18|4|0|8|4|2|18|18|
|27|stocktitan.net|17|3|3|5|5|1|17|17|
|28|cnbc.com|16|1|0|6|6|3|16|16|
|29|quiverquant.com|16|5|3|4|3|1|16|16|
|30|patch.com|15|0|0|0|0|15|15|15|

## 文件分组

|JSON|Site排名|消息数|body非空|
|---|---|---:|---:|
|[group-01-sites-01-05.json](../exports/google-news-five-ticker-site-cases-20261028/group-01-sites-01-05.json)|1–5|100|99|
|[group-02-sites-06-10.json](../exports/google-news-five-ticker-site-cases-20261028/group-02-sites-06-10.json)|6–10|100|42|
|[group-03-sites-11-15.json](../exports/google-news-five-ticker-site-cases-20261028/group-03-sites-11-15.json)|11–15|100|94|
|[group-04-sites-16-20.json](../exports/google-news-five-ticker-site-cases-20261028/group-04-sites-16-20.json)|16–20|100|80|
|[group-05-sites-21-25.json](../exports/google-news-five-ticker-site-cases-20261028/group-05-sites-21-25.json)|21–25|94|37|
|[group-06-sites-26-30.json](../exports/google-news-five-ticker-site-cases-20261028/group-06-sites-26-30.json)|26–30|82|82|

[全部打包下载](../exports/google-news-five-ticker-site-cases-20261028.zip)；[Top 30 CSV](../exports/google-news-five-ticker-site-cases-20261028/top30.csv)。

## 证据与复算

`eval/google_news_five_ticker_20261028/`：terms.json为只读生产监测词快照；rss/为150个原始RSS；scan.json保留查询、发布时间裁剪、请求哈希及触顶证据；articles.json、ranking.csv为去重和站点排名；selected.json为固定随机样本；bodies/、reader/、rejected/保留正文及拒绝正文证据；manifest.json记录每篇原GUID、URL、正文来源状态和哈希。生产读取仅用既有ticker/title索引做点查，未全库扫描。

复算脚本：scan.py（缓存存在时不再联网）、prepare.py、harvest.py、fallback.py、quality.py、package.py。正式日期查询没有额外词分片；触顶日没有扩展补查，因此覆盖限制明确保留。
