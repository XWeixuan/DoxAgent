# Google News Search RSS 人工白名单

记录日期：2026-10-28。依据用户对上一批 20 站的人工判断：其中 8 站加入黑名单，其余以下 12 站保留。

|人工保留的 Site|
|---|
|tradingview.com|
|investing.com|
|notebookcheck.net|
|moomoo.com|
|fool.com|
|247wallst.com|
|seekingalpha.com|
|benzinga.com|
|wccftech.com|
|marketscreener.com|
|techpowerup.com|

本文件只记录人工保留结论，不是运行时“只允许这些站”的 allowlist，也不覆盖其他普通 Site 的正常接纳。后续人工判断后继续追加或调整。

本次新增判黑：ad-hoc-news.de、marketbeat.com、finance.biggo.com、digitalfoundry.net、simplywall.st、shattered.io、tradingkey.com、aol.com。此前判黑：on3.com、247sports.com、pcguide.com、media.unisba.ac.id。

运行时黑名单位于 `src/doxagent/message_bus_v2/google_news_blocklist.json`，只作用于 Google News Search RSS。`finance.biggo.com` 使用 `biggo.com` 及全部子域匹配，AOL 同时涵盖 `aol.com`、`aol.co.uk` 及子域；`media.unisba.ac.id` 保持该媒体子域边界，不扩散到学校其他域名。

## 第二批人工判断：2026-10-28 公司名查询修订版 Top 30

依据用户最新明确判断，以下20站判白，与上方12站合计32站。来源为 `google-news-five-ticker-company-only-top30-site-extraction-20261028.md` 的30站排名；另10站写入黑名单。白名单仍只作为人工记录，不启用“只允许白名单”的运行限制。

|人工保留的 Site|
|---|
|snowflake.com|
|tipranks.com|
|gurufocus.com|
|stocktwits.com|
|theglobeandmail.com|
|tikr.com|
|quiverquant.com|
|stocktitan.net|
|barrons.com|
|barchart.com|
|cnbc.com|
|nebius.com|
|streetinsider.com|
|futunn.com|
|investors.com|
|zacks.com|
|invezz.com|
|indiatimes.com|
|reuters.com|
|thestreet.com|

本批判黑：pluang.com、tradersunion.com、foreignpolicyjournal.com、kalkinemedia.com、stockstotrade.com、timothysykes.com、trefis.com、tokenpost.com、kalkine.ca、maxpreps.com；各根域及下级子域匹配。当前累计22个判黑 Site，对应23个运行匹配域名（AOL另有aol.co.uk）。

## 后续调整：2026-10-28 MU 排名人工判断

用户因已有独立 Yahoo 消息源，明确将 yahoo.com 加入 Google News RSS 专用黑名单，包含 finance.yahoo.com 及其他下级子域。因此撤销上方原 finance.yahoo.com 白名单记录；原批次数量仅表示当时判断。

|人工保留的 Site|
|---|
|syracuse.com|
|marketwatch.com|
|chosun.com|

用户明确确认 syracuse.com、marketwatch.com、chosun.com 判白，chartmill.com 判黑。当前人工白名单共34站，运行黑名单25个匹配域名。白名单仍仅用于人工记录；Yahoo 独立消息源不受此 Google 专用规则影响。
