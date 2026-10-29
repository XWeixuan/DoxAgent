# Google News RSS 五 ticker Top 30 Site 历史抽取：公司名查询修订版

日期：2026-10-28。仅历史数据抽取和随机案例包，未新增黑名单、未改变生产查询规则或订阅。

## 时间与查询口径


本轮仅重新查询 SNOW、CEG 各30次，共60次；RKLB、ONDS、NBIS 各30次、共90份 RSS 完整复用上一轮文件并校验哈希。合并仍为150个日窗口，日期条件为2026-09-28至2026-10-27，`after:日期 before:下一日`，`hl=en-US&gl=US&ceid=US:en`。

查询正文固定为 SNOW=`Snowflake`、CEG=`"Constellation Energy"`；RKLB=`(RKLB OR "Rocket Lab")`、ONDS=`(ONDS OR Ondas)`、NBIS=`(NBIS OR Nebius)`。只有本次历史抽取采用这两个例外，没有改生产 Google 消息源特殊规则或 ticker 订阅。

为保证其他三个 ticker 不变，沿用上一轮共同冻结范围：发布时间从 2026-09-28T00:00:00+00:00 起、截至 2026-10-27T17:32:11.067063+00:00，即北京时间2026-10-28 01:32:11；两个公司名查询的本轮抓取时点为 2026-10-27T17:59:43.175164+00:00。末个 UTC 日尚未完成，不把未完成日算成完整日。Google 按检索日期返回的记录与 pubDate 不完全对齐，因此合并全部150个列表后统一按发布时间裁剪，没有在每个小窗口先丢弃相邻日期记录。新旧 Google 索引会变化，样本对比不能完全解释成单一查询条件的因果效果。

本轮合并列表共有 3 次返回100条上限：RKLB 1次, ONDS 0次, NBIS 1次, CEG 1次, SNOW 0次。SNOW 从上一轮23次触顶降至本轮0次。触顶日不额外分词补查，结果不是完整互联网新闻总量。

## 去重、排除与抽样

先按 Google GUID 去重，再合并同一 PSL Site、完全相同标题（空白/大小写/HTML实体归一）及同一 UTC 发布日期的重复记录；可验证的真实文章 URL 亦检查去重，保留有语义的查询参数。跨 ticker 命中只计一篇，ticker 计数允许一篇多计。不同 Site 的转载各自保留，便于衡量站点噪音。

复核发现 Invezz 的三组文章存在国家路径（/in/、/nz/、/en-ae/ 等）副本，文章路径日期、slug和标题相同，RSS日期略有差异，其中两组提取正文也完全相同；已将该站地区路径归一，额外合并3条。其他三个 ticker 的 RSS 文件和查询未改，合并去重统计会体现这一修正。证据见 regional-alias-audit.json。

Site 用随已安装 tld 库附带的 PSL 归一到注册域名；学校媒体 media.unisba.ac.id 保留特定子域边界。黑名单取当前 google_news_blocklist.json，白名单取20261028人工文档并按注册域归并（finance.yahoo.com归入yahoo.com）；先排除，再对剩余 Site 排名。

随机种子为 `20261028:<Site>`，按稳定文章 ID 排序后无放回随机抽样。不因正文抓取失败换样，不人工偏选低价值文章。既有黑白名单未修改；排名不等于封禁建议。

## 正文状态与限制

新增网页正文是本次抓取时的页面版本，不保证等于发布时点快照；历史存储正文仅在发布域名匹配时复用。未用 RSS 摘要填补缺失正文。

- stored_FULL_unverified / stored_url_FULL_unverified：生产原存储或真实文章URL匹配的正文缓存标为 FULL 的内容，本次未逐篇证明全文完整；SHORT_FULL 同理。
- http_extracted_unverified / http_impersonated_extracted_unverified：原站 HTTP 文本提取，无主动长度截断；可能仍有页面噪声或漏段。
- reader_extracted_unverified：原站轻量 Reader 提取文本，截去可识别导航和推荐页尾；不保证每篇完整。
- partial_original_preview：确认为原站预览/摘要区域，保留真实片段但不冒充全文。
- blocked_page、http_*、fetch_*、no_article_body、article_mismatch 等且 body=null：未获得可用原文；不是空白文章，不得按有完整正文计算。
- video_caption_only / video_metadata_only / newsletter_link_only / navigation_shell / interactive_model_shell：只有视频说明、页面入口或非正文组件，body=null。不能用 RSS 摘要填充。

已复用上一轮同一原站的正文与失败证据，对新增样本继续 HTTP/Reader 补取；未使用或修改生产浏览器身份。不得将“HTTP 200”或 body 非空等同于全文验收。

IBD 的可见截断预览明确标为 partial_original_preview，视频说明和推荐播放列表不算正文；正文获取失败只代表访问/证据限制，不能据此判为低质量 Site。

公司名查询去掉了上一轮大批天气/滑雪及 CEG 缩写歧义，但 `Snowflake` 仍能命中同名高中/球队；例如 MaxPreps 的9篇均来自 SNOW，其中包含 Snowflake High School 赛程及比赛。继续保留这类原始噪音供人工判断，未私自做相关性筛选。排名本身不是封禁结论。



## 与上一轮样本对比

|口径|上一轮|修订版|
|---|---:|---:|
|合并去重消息|4773|3279|
|排除黑白名单后消息|2947|1520|
|未判定 Site|1054|517|
|SNOW去重消息|2089|718|
|CEG去重消息|582|461|
|SNOW触顶查询|23|0|


## 召回汇总

|Ticker|正式查询|原始RSS行数|去重后消息|触顶查询|
|---|---:|---:|---:|---:|
|RKLB|30|1026|781|1|
|ONDS|30|402|278|0|
|NBIS|30|1374|1042|1|
|CEG|30|651|461|1|
|SNOW|30|976|718|0|

五 ticker 合并：GUID 唯一 3293 篇；再合并同站同标题同发布日期重复 11 篇，URL额外合并 3 篇，最终 3279 篇。黑名单排除 462 篇，白名单排除 1297 篇，余 1520 篇、517 个 Site；Top 30 共 724 篇。

抽样 465 篇；body非空 387 篇（包含原站预览，未经逐篇全文验收），未取得可用原文 78 篇。不能把此非空比例当成生产正文补全成功率。

## Top 30 排名

|排名|Site|总量|RKLB|ONDS|NBIS|CEG|SNOW|抽样|body非空|
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
|1|snowflake.com|85|0|0|0|0|85|20|20|
|2|tipranks.com|73|29|3|32|4|5|20|19|
|3|pluang.com|64|15|8|28|4|9|20|20|
|4|gurufocus.com|54|13|6|26|5|4|20|0|
|5|tradersunion.com|43|12|2|13|5|11|20|1|
|6|stocktwits.com|33|16|8|9|0|0|20|20|
|7|foreignpolicyjournal.com|31|21|1|4|5|0|20|20|
|8|theglobeandmail.com|29|12|1|9|4|3|20|14|
|9|kalkinemedia.com|26|10|0|1|12|3|20|20|
|10|stockstotrade.com|21|0|12|8|0|1|20|20|
|11|tikr.com|20|4|0|8|3|5|20|20|
|12|timothysykes.com|20|0|9|10|0|1|20|20|
|13|trefis.com|20|6|4|0|6|4|20|20|
|14|quiverquant.com|18|5|3|4|4|2|18|18|
|15|stocktitan.net|16|3|3|5|2|3|16|16|
|16|barrons.com|15|2|5|2|6|0|15|2|
|17|barchart.com|14|7|0|4|0|3|14|14|
|18|cnbc.com|14|1|0|6|4|3|14|14|
|19|nebius.com|14|0|0|14|0|0|14|14|
|20|streetinsider.com|14|1|1|9|2|1|14|12|
|21|futunn.com|12|2|1|8|0|1|12|12|
|22|investors.com|11|2|0|0|1|8|11|8|
|23|invezz.com|11|0|0|8|1|2|11|11|
|24|zacks.com|11|7|0|2|2|0|11|11|
|25|tokenpost.com|10|4|0|6|0|0|10|10|
|26|indiatimes.com|9|0|0|2|7|0|9|9|
|27|kalkine.ca|9|1|4|4|0|0|9|9|
|28|maxpreps.com|9|0|0|0|0|9|9|9|
|29|reuters.com|9|1|0|2|4|2|9|0|
|30|thestreet.com|9|6|1|2|1|0|9|4|

## 文件分组

|JSON|Site排名|消息数|body非空|
|---|---|---:|---:|
|[group-01-sites-01-05.json](../exports/google-news-five-ticker-company-only-site-cases-20261028/group-01-sites-01-05.json)|1–5|100|60|
|[group-02-sites-06-10.json](../exports/google-news-five-ticker-company-only-site-cases-20261028/group-02-sites-06-10.json)|6–10|100|94|
|[group-03-sites-11-15.json](../exports/google-news-five-ticker-company-only-site-cases-20261028/group-03-sites-11-15.json)|11–15|94|94|
|[group-04-sites-16-20.json](../exports/google-news-five-ticker-company-only-site-cases-20261028/group-04-sites-16-20.json)|16–20|71|56|
|[group-05-sites-21-25.json](../exports/google-news-five-ticker-company-only-site-cases-20261028/group-05-sites-21-25.json)|21–25|55|52|
|[group-06-sites-26-30.json](../exports/google-news-five-ticker-company-only-site-cases-20261028/group-06-sites-26-30.json)|26–30|45|31|

[全部打包下载](../exports/google-news-five-ticker-company-only-site-cases-20261028.zip)；[Top 30 CSV](../exports/google-news-five-ticker-company-only-site-cases-20261028/top30.csv)。

## 证据与复算

`eval/google_news_five_ticker_company_only_20261028/`：terms.json为只读生产监测词快照；rss/为150个原始RSS；scan.json保留查询、发布时间裁剪、请求哈希及触顶证据；articles.json、ranking.csv为去重和站点排名；selected.json为固定随机样本；bodies/、reader/、rejected/保留正文及拒绝正文证据；manifest.json记录每篇原GUID、URL、正文来源状态和哈希。生产读取仅用既有ticker/title索引做点查，未全库扫描。

复算脚本：scan.py（缓存存在时不再联网）、prepare.py、harvest.py、fallback.py、quality.py、package.py。正式日期查询没有额外词分片；触顶日没有扩展补查，因此覆盖限制明确保留。
