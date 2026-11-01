# Google News RSS 白名单站点正文补全优化与验收

日期：2026-11-02，北京时间。范围：当前人工白名单全部 **34 个 Site**。本轮完成本地开发、历史文章复测及服务器现有 HTTP 出口探测；**未部署，未新增或修改 Chrome 身份，未启动浏览器抓取测试**。

## 1. 结论

本轮同时修复“页面中已有完整正文却解析不到”和“预览/推荐列表被误算完整正文”两类问题，并调整一处已有公共站点的访问顺序。不能把所有失败归为需要浏览器：HTTP 出口、Reader 临时限流、真实订阅权限和已删除文章必须分开判断。

- **Chosun**：提取同篇文章的公开 ANS JSON，固定样本 **1/5 → 5/5**；另取未参与调试的历史文章 **5/5**。服务器直接 HTTP 复测亦为 **5/5**。
- **StockTitan**：识别已内嵌的完整 Form 4，保留表格内容，避免无意义跳转 SEC。固定样本 **1/5 → 5/5**；未参与调试的文章 **5/5**，其中三篇为内嵌 Form 4、两篇普通新闻。不过服务器现有出口的五篇均耗尽预算，Reader 仅 **3/5**；**解析修复完成，服务器访问稳定性仍未验收通过**。
- 服务器现有 HTTP 出口下，**247WallSt、Fool、Syracuse、Wccftech 各 5/5**，TIKR **4/5**；这些不需要仅为 Google 源新建身份。TIKR 公共 Reader 另为 **5/5**。
- **Benzinga** 公共 Reader **5/5**，约 0.56–0.97 秒。将已有公共访问顺序调整为 HTTP → Reader → 原有 Browser，先使用低成本成功路径，保留浏览器恢复能力。
- **Barchart、Stocktwits** 公共 Reader 各 **5/5**，TipRanks、StreetInsider 各 **4/5**；富途首次 **3/5**，仅续测两个失败样本恢复 **1/2**，尚不足以宣称稳定解决。
- 纠正 **7 条伪 FULL**：Barron's 五条订阅预览混入推荐文章；IBD 两条视频播放列表冒充正文/Transcript。没有通过放松正文完整性门槛增加成功率。

需要评估浏览器维护成本的站点及现有身份复用情况见第 5 节。不能对所有 34 站承诺高成功率；付费全文、持续验证码、JS 挑战及服务器出口超时仍有明确未解决项。

## 2. 样本和统计口径

白名单依据：`docs/google-news-search-site-whitelist-20261028.md` 当前版本，共 34 站；Yahoo 和 Chartmill 已不在白名单。没有修改黑白名单或查询词规则。

历史样本来自前几轮已经抓取的 BE/INTC、五 ticker、公司名修订版及 COHR/MU 数据。每站固定五篇：有历史失败记录时最多纳入两条失败，其余使用固定随机种子选择；文章 URL 去重后 **170 个不同 URL**。这是面向故障的覆盖测试，**不是按生产流量加权的随机统计**。Google 包装链接已全部解析为原站文章 URL，没有为本轮重新扫描 30 天 RSS。

另在核心解析修复后，从历史池按固定 ID 排序取未参与调试的 Chosun、StockTitan 各五篇。共 **10 个不同 URL，与原 170 篇无交集**；全部取得 FULL，未根据这十篇再调整这两个解析器。

验证分层保留，不能合并成一个生产成功率：

1. **基线与冻结响应回放**：同一批 HTML/Reader 响应、同一 170 篇文章，只改变解析与流水线代码。基线标记 FULL 为 68/170，其中确认七条伪 FULL，因此真实完整文本最多为 61/170；修复回放为 **69/170**。新增八条真实成功，删除七条虚假成功。基线 Reader 存在临时匿名限流、域名冷却及超时，冻结回放不会把缺失响应伪造成实时成功。
2. **服务器 HTTP 探测**：先对每站一篇及 StockTitan 五篇，使用现有 Site Access、现有 HTTP 出口、BODY/backfill、HTTP_PUBLIC 模式；其后对七个站各五篇复测，并在原 12 秒总预算内跟随重定向。该模式不会启动 Chrome。另有服务器直接 HTTP 探测，用来区分原始服务器网络与 Site Access 配置出口。
3. **公共 Reader 复测**：最多两个并发、启动间隔至少 3.3 秒；每站先测两篇，有成功再测剩余三篇。首轮 66 次请求中 40 次 FULL；针对富途/StockTitan 续测七次为四次 FULL；针对出口敏感的 Stocktwits/StreetInsider 再测十次为九次 FULL。重复请求和不同时间结果保持独立，**不折算成一次成功率**。

表中 FULL 指流水线通过正文完整性与文章身份检查。对新增路径另检查正文边界、金额/比例、表格、原文段落以及推荐区排除；这些小样本结果不等于长期稳定率，也不等于生产端到端完成率。

## 3. 全部 34 站结果

“冻结前→后”是同一五篇的 FULL/SHORT_FULL 数；“服务器 HTTP”优先展示五篇复测，没有复测则是一篇探测。标注权限拒绝的探测结果只表示现有访问政策，没有绕开认证。Reader 为独立公共探测，不能视为已修改生产访问顺序。

|Site|冻结前→后|服务器 HTTP|公共 Reader 复测|判断及处理|
|---|---:|---:|---:|---|
|247wallst.com|0/5→0/5|5/5|4/5|本地出口 403；服务器现有出口可直接完整提取，无需新增身份|
|barchart.com|0/5→0/5|0/1|5/5|HTTP 202/JS 页面；现有通用 HTTP→Reader 能覆盖，保留限流风险|
|barrons.com|5/5→0/5|认证要求|未额外请求|纠正五条伪 FULL；订阅内容需已有账户与有效权益|
|benzinga.com|1/5→1/5|0/1|5/5|已改为公共 Reader 优先于已有 Browser；Reader 失败仍可回退|
|chosun.com|1/5→5/5|直接 HTTP 5/5；Site Access 1/1|无需|新增公开 ANS 提取；独立历史样本另 5/5|
|cnbc.com|4/5→4/5|所测视频不支持|未额外请求|四篇正文正常；余一篇是视频，不能用播放页充当全文|
|fool.com|0/5→0/5|5/5|3/5|服务器 HTTP 直接成功；收紧正文范围及 Reader 推广尾部清洗|
|futunn.com|0/5→0/5|0/5|首次3/5，失败续测1/2|JS 防护壳；Reader 有低成本可用路径但仍不稳定；仅此域 Reader 默认预算调至12秒|
|gurufocus.com|0/5→0/5|0/1|0/2|人工/Cloudflare 验证；列入浏览器成本评估|
|indiatimes.com|5/5→5/5|1/1|无需|现有 HTTP 正文能力保持|
|investing.com|0/5→0/5|0/1|2/5|现有 Registry 为 Browser 路径；保留身份与国家域策略，不启用低成功率公共绕行|
|investors.com|2/5→0/5|所测视频不支持|未额外请求|两篇视频列表、三篇订阅截断；纠正伪成功并保留真实全文要求|
|invezz.com|5/5→5/5|1/1|无需|现有 HTTP 正文能力保持|
|marketscreener.com|0/5→0/5|0/1|未额外请求|四篇订阅/截断、一篇非文章目标；浏览器本身不能提供订阅权益|
|marketwatch.com|1/5→1/5|认证要求|0/2|已有受保护 Browser 策略；新增正文范围识别，账户权益仍需验证|
|moomoo.com|0/5→0/5|1/5|0/2|跟随重定向后多为纯 JS 防护壳；浏览器/JS 能力评估候选|
|nebius.com|5/5→5/5|1/1|无需|现有企业官网 HTTP 路径保持|
|notebookcheck.net|5/5→5/5|1/1|无需|现有 HTTP 正文能力保持|
|quiverquant.com|5/5→5/5|1/1|无需|现有 HTTP 正文能力保持|
|reuters.com|0/5→0/5|0/1|0/2|原站挑战、Reader 挑战/超时；已有 Browser→Reader 策略，优先核验已有身份|
|seekingalpha.com|1/5→1/5|认证要求|5/5|公开 News 页 Reader 可读，但现行 BODY 必须认证；本轮保留此政策，没有扩大公共访问|
|snowflake.com|5/5→5/5|1/1|无需|现有企业官网 HTTP 路径保持|
|stocktitan.net|1/5→5/5|0/5，预算耗尽|3/5|内嵌完整文件解析修复；独立样本5/5；服务器出口问题仍待解决，不能据此要求Chrome|
|stocktwits.com|5/5→5/5|0/1|5/5|服务器出口403而本地直取与Reader正常；保留低成本回退，无证据表明必须新增身份|
|streetinsider.com|4/5→4/5|0/1|4/5|修复 URL 编码等价性检查；余一篇持续挑战，低成本路径仍有80%样本成功|
|syracuse.com|0/5→0/5|5/5|4/5|支持公开 ANS JSON，现有出口直接成功；首轮本地为429/冷却|
|techpowerup.com|0/5→0/5|0/1|0/2|拖拽验证码页面；正确记为挑战，列入浏览器成本评估|
|theglobeandmail.com|2/5→2/5|所测404|0/2|部分历史链接已失效；先区分删除、转载和订阅，不把404归为需要浏览器|
|thestreet.com|1/5→1/5|0/1|0/2|HTTP挑战、Reader403；已有公共站点策略含Browser，列入身份/会话评估|
|tikr.com|0/5→0/5|4/5|5/5|一次HTTP错误，其余直接成功；Reader覆盖完整，适合现有低成本回退|
|tipranks.com|0/5→0/5|0/1|4/5|公开Reader可覆盖多数样本，余一篇挑战；WP接口实测403，无新接口接入|
|tradingview.com|5/5→5/5|1/1|无需|现有 HTTP 正文能力保持|
|wccftech.com|0/5→0/5|5/5|0/2，正文不完整|现有服务器 HTTP 出口可直接成功；不采用无收益的WP接口或Reader路径|
|zacks.com|5/5→5/5|1/1|无需|现有 HTTP 正文能力保持|

没有把“换出口或稍后重试成功”的全部收益归功于代码修复。特别是 247WallSt 等站的零到五，反映的是当前服务器已有 HTTP 能力；本轮没有更改出口配置。

## 4. 已实现的改动

### 4.1 正文文档适配

新增 `src/doxagent/content_enrichment/publisher_documents.py`，保持为纯解析模块，不建立新服务、数据库、常驻任务或身份池。

- Chosun/Syracuse：解析 `Fusion.globalContent` 中公开的 `story`，校验文章标题和 canonical URL；提取正文、标题段、引文、列表及表格单元格，不读取 related content，不执行页面 JS，不接纳已标受限的内容。
- StockTitan：仅对完整内嵌 Form 4 使用专用范围，要求文档包含 Form 4、SEC 全称和签名。已包含原文时，外部“View Original”视为出处；仅有摘要时仍要求原文，避免漏掉真实附件/正文。
- Barron's：正文段落与 crawler 预览、推荐卡分离。存在真实完整段落时保留全部段落；订阅预览不再借推荐区长度变成 FULL；公开免费正文不被一律拒绝。
- MarketWatch、Fool、Stocktwits：增加实际正文范围；Registry 明确指定的选择器仍优先。
- IBD：视频列表中的“Read Transcript”标签不再代表实际 Transcript；只有真实 transcript 节点可作为字幕正文。订阅截断判定采用已观察到的“末尾省略号紧邻版权”形态，不因正常文章中存在省略号就判为付费。

### 4.2 通用 Reader 和失败机制

- 支持 Markdown H1–H6 与 Setext 标题；指定站点可使用匹配标题及同篇 URL Source 的 Reader 文档头识别正文。
- URL Source 验证保留域名、路径与业务 query 身份；允许相同 URL 的编码形式差异及跟踪参数差异，解决 StreetInsider 括号/逗号编码差异；不把不同子域或不同文章 ID 当同一篇。
- 忽略 `javascript:` 等非 HTTP(S) 的“原文”链接；传输返回非法重定向目标时不污染后续 Reader 的有效文章 URL。
- 正确认出拖拽验证码、bot check，以及 Moomoo/Futunn 的纯 `Document` JS 壳，保持可解释失败原因。
- 清理 Reader 的推荐区、Seeking Alpha 的 More on 及 Fool Stock Advisor 推广尾部，不压缩正文事实。

### 4.3 访问成本与事件循环

- 仅对已验证 Reader 可用的公共 Benzinga，在原策略已允许 HTTP、Reader、Browser 且不要求认证时，采用 HTTP→Reader→Browser。Reader 默认4秒，保留原 Browser 回退；受保护路径不变。
- 仅 Futunn 域及其子域 Reader 默认预算从8秒调至12秒，因为成功样本的冷请求耗时约8.5–11.1秒。**仍受原始文章总截止时间约束，显式 Registry 参数优先**，没有额外重试循环、全局超时膨胀或并发扩容。
- HTML 与 Reader 解析放入线程执行，避免大文档解析占据 Hub 的 asyncio 事件循环；复用原有文章并发限制，没有增加 HTTP 会话或浏览器页数。
- 未修改 Google 查询、黑名单、消息发现、发布去重、ARTICLE_ONCE 或正文缓存机制。共享正文解析改动可同时惠及相同原站的其他消息来源，已执行跨来源回归。

## 5. 浏览器及身份维护待评估清单

以下是基于 HTTP/公开 Reader 实测的候选，**没有通过本轮浏览器测试证明“只要维护身份就一定成功”**。浏览器只能处理一部分 JS/会话挑战，不能替代付费订阅，也不能恢复已删除的文章。

|类别|站点|观察依据|建议|
|---|---|---|---|
|优先评估新增/补齐能力|GuruFocus|原站与Reader均出现人机验证|若价值足够，做一次受控现有Chrome/CDP能力验证，再决定是否维护身份|
|优先评估新增/补齐能力|TechPowerUp|原站为拖拽验证码；Reader空正文|需要真实页面交互/会话能力测试，单纯增加无身份headless页面未必有效|
|优先评估新增/补齐能力|Moomoo|服务器仅1/5，其他多为JS壳，Reader0/2|评估共享site级身份或短时浏览器Context，不按ticker建profile|
|按价值决定|Futunn|JS壳，Reader有成功但有空文/超时|先使用已实现的12秒低成本路径；要求更高覆盖率才评估浏览器|
|核验已有能力|TheStreet|HTTP挑战、Reader403；Registry已含Browser路径|先检查已有site级身份/会话可用性，避免重复维护Google专属身份|
|复用已有身份优先|Reuters、Investing|当前Registry已配置Browser；匿名路径低成功率|核验现有BODY路由及身份健康；本轮没有验证/修改其登录与挑战状态|
|账户权益与身份一起评估|Barron's、MarketWatch、Seeking Alpha|当前Registry要求BODY认证|复用已有身份，核对真实全文权益；SA公开News Reader成功不代表应擅自覆盖全站认证策略|
|权益问题优先|MarketScreener、IBD文字新闻|真实订阅/预览截断|先确认是否有可用账户及全文权；不要为拿到同样预览而新建Chrome|

暂不建议因为本轮结果就新增身份：

- **StockTitan**：本地HTTP取得全文、服务器请求耗尽预算、Reader部分成功，尚无“身份是根因”的证据。优先排查现有HTTP出口/网络预算，这是仍未完成的服务器侧稳定性问题。
- **Stocktwits、StreetInsider、TipRanks、Barchart**：公开Reader已覆盖多数或全部样本，先使用低成本路径；余下挑战单独观察，不扩大到全站常驻浏览器。
- **The Globe and Mail 的404**、**CNBC/IBD无文本视频**：属于链接/内容形态问题，不能以浏览器成功率包装解决。

术语：本仓库的 NCP 指 Yahoo `/xhr/ncp` 数据接口，Yahoo 当前不在白名单；本轮这些站点没有可直接套用的通用 NCP 接口。若指使用已有 Chrome 原生调试连接，对应是 CDP，仍需单独评估会话和资源成本。

## 6. 测试、产物与发布状态

- 集成回归 **166 passed**：新解析测试及现有 Pipeline、Publishers、Native、Hub、Freshness Delivery、Publisher Scope Routing 测试；结果见 `eval/google_news_body_whitelist_20261102/final-tests.xml` 和 `final-tests.txt`。
- 收尾解析回归 **32 passed**，包括正常省略号不误判订阅、异常ANS元数据、不串文章、完整表格、伪FULL、非法链接、Reader标题/URL身份、已有认证边界、Reader失败后的原Browser恢复、子域预算与原始总截止时间。此组与166项存在重叠，不相加成198项。
- 新增模块与测试 Ruff 检查通过；受改动的已有文件 `git diff --check` 通过。未整体格式化已有文件、未修改并行任务的其他实现。
- 没有引入新依赖、浏览器进程、定时抓取器或额外数据库。服务器探测是隔离子进程，仅临时加载候选解析文件并请求 HTTP_PUBLIC，没有替换生产服务代码或重启服务。

主要证据目录：`eval/google_news_body_whitelist_20261102/`。

|文件|用途|
|---|---|
|`cohort.json`、`heldout-cohort.json`|固定170篇及未参与调试的10篇URL/标题|
|`baseline.json`、`replay.json`、`snapshots/`|同响应前后对照及原始响应|
|`heldout.json`|十篇独立历史样本完整验收|
|`owner-http.json.gz`、`owner-http-focused.json.gz`|现有Site Access HTTP出口原始回执与HTML|
|`remote.json.gz`|34站现有策略及服务器直接HTTP结果|
|`owner-reparsed.json`|以最终解析器复核服务器HTTP响应|
|`reader-recheck.json`、`reader-final.json`、`reader-egress-recheck.json`|公共Reader分轮结果，保留失败回执|
|`summary.json`、`site-results.csv`|34站分层统计，避免混算成功率|

所有代码仍为本地候选，**未提交、未发布生产**。上线后才可以用真实新文章计算流量加权的FULL/SHORT_FULL成功率及耗时，不能用本报告的故障抽样或“多轮至少一次成功”替代生产验收。

## 后续实施状态（2026-11-02）

第二轮已正式发布全部main开发代码及Google专用两路径访问/原生接管，BE/INTC之外新增7个绑定、MU排除。Benzinga公共路径调整最终仅作用Google源，原生HTTP/Browser顺序保留；最终结果、此前统计中的Stocktwits行情页/截断纠正及生产剩余限制，以[第二轮报告](google-news-rss-whitelist-body-optimization-phase2-20261102.md)为准。
