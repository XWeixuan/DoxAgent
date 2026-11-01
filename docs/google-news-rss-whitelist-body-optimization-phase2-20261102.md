# Google News RSS 白名单正文补全第二轮优化、原生接管及发布验收

日期：2026-11-02（北京时间）。接续 `google-news-rss-whitelist-body-optimization-20261102.md`。白名单仍为人工确认的34站；没有改变黑白名单、第一搜索词查询规则或原生源订阅。

## 1. 实施结论和统计口径

本轮集中处理前一轮弱站点，完成11个公共站点的轻量路由/正文边界优化，以及4个已有原生来源域名的即时接管。没有新建浏览器池、数据库表、后台扫描或常驻浏览器。每篇Google文章至多一个主路径、一个fallback；Hub不再把已经执行完的两条公共路径重新执行一遍。

六个核心弱站的冻结开发集30篇、未参与调试的历史验证集28篇，共58个不同URL；最终真实FULL为46/58。开发集23/30、验证集23/28。除TheStreet之外五站为45/48。另五个Reader/API路径站点各5篇、25个不同URL，单独记录实测，不把连续重试或换引擎次数作为新增文章分母。

11站合计83个不同URL，最终70个FULL（84.34%）；保留3个非标准文章目标在原始分母中。五个路径控制站最终24/25，其中唯一未FULL为Stocktwits行情页；Barchart最后一次5/5，修复前的4/5记录保留。

这些是面向历史失败的站点覆盖样本，**不代表流量加权生产成功率，不宣称已长期稳定达到SLA**。没有补扫RSS30天，也没有将这些历史样本发布到生产消息总线或创建Runtime Case。FULL要求文章身份相符、完整正文边界可靠；报价页、滚动话题、很短社交帖、付费预览、评论区均不能充数。

## 2. 最终逐站路径和结果

每条路径仍受文章总预算约束。表中平均/最长耗时为整个已测文章执行，含失败及fallback；不含排队和Google wrapper首次解析。

|Site|主路径 → 唯一fallback|核心实测FULL|平均 / 最长|结论|
|---|---|---:|---:|---|
|Futunn|Jina browser → Zyte browser|9/10；独立验证5/5|11.31s / 30.01s|失败项为滚动discussion话题页，现直接non_article_target，无请求|
|GuruFocus|Zyte browser → 按需共享Chrome|10/10；独立验证5/5|10.17s / 15.97s|仅HTTP/换IP无效；清除实时股价、GF Score浮层，避免把55评分混成55名内幕人|
|Moomoo|HTTP → Zyte browser|8/10；独立验证5/5|7.67s / 20.00s|修正正文容器；一条57字社交帖不算FULL，另一次API超时保留失败|
|StockTitan|现有土耳其HTTP出口 → Zyte browser|10/10；独立验证5/5|0.70s / 1.68s|沿用已有代理listener；修复远端解析DNS前被本地DNS预检阻断的问题；完整内嵌Form4不再跳SEC|
|TechPowerUp|Zyte HTTP → Zyte browser|8/8；独立验证3/3|2.21s / 6.03s|历史池独立文章只有3篇，如实保留；限定newspost正文，不再用评论区补长度|
|TheStreet|HTTP → Reader（最多4s）|1/10；独立验证0/5|旧8秒Reader样本7.79s / 8.77s|四个出口、Zyte HTTP/browser、按需Chrome均无可靠收益，停止昂贵fallback；失败不伪装成功|
|Benzinga|Zyte HTTP → Zyte browser|5/5|4.49s / 11.46s|匿名Reader从曾经可用变成不稳定，换为实测API顺序；不改变其原生源浏览器顺序|
|TipRanks|Jina browser → Zyte HTTP|5/5|5.33s / 11.68s|保留完整原文，失败不再追加第三路径|
|StreetInsider|Jina browser（.article_body）→ Zyte HTTP|5/5|5.39s / 17.41s|修复文档标题匹配后误从公司介绍H2开始、工具栏及推荐串入正文|
|Stocktwits|Jina browser → Zyte HTTP|4/5；4篇真实新闻均FULL|8.28s / 12.04s|另1条/symbol/行情页直接拒绝；正文中Read Next跳过，不提前截断；Latest News才结束|
|Barchart|Jina auto → Zyte browser|最终5/5|3.33s / 4.97s|Reader出现403域名不稳定；Zyte HTTP仅JS壳，browser可读；修复页脚Continue Reading误判并清除推广尾部|

Stocktwits真实原文修复例：weekly文章从错误截断的1103字恢复至6699字，RKLB/Iridium稿从3114字恢复至6269字。旧版本“5/5”包含1条行情页且正文边界有遗漏，不能沿用为成功结论。

Barchart新增正反例：正文之外的Continue Reading不代表正文缺失；正文之内的展开按钮仍保留PARTIAL/expand_required。不会按正文长度直接豁免不完整检查。

原34站中其他站的公开正文、ANS、付费/视频/失效分类沿用第一轮报告及既有回归；本轮没有假称对全部34站再完成一次实时大样本验收。

## 3. 出口、引擎和资源成本

现有6个出口标签只对应4个不同公网IP：日本/荷兰同IP，德国/美国同IP，另有土耳其和服务器直连。测试复用已有listener，未切换全局Clash selector或改变原生身份出口。24次定向请求证明：StockTitan土耳其出口可用；Moomoo多数响应实际已有正文，失败来自范围/原文检查；GuruFocus、TechPowerUp、TheStreet不能仅靠换IP解决。

Jina X-Engine browser对Futunn、Stocktwits、TipRanks等有实际收益。cf-browser-rendering和HTML输出均在本账户匿名测试中返回401；未新增账户/接受服务条款来扩大权限。StreetInsider采用可用的目标selector，仍有一次422，由唯一fallback承接。

Zyte密钥只经隐藏输入进入测试子进程环境/SSH stdin；正式使用宿主0600私有env_file，仅挂接BODY服务。没有写入源码、镜像、Git、结果JSON或URL。HTTP/API客户端惰性创建、最多4连接；付费API调用仅用于已测有收益的Google Site，不增加轮询阶段的正文负载。

GPU/LLM未参与抓取；没有新服务或常驻Chrome。全Google共用一个专用Profile目录，Playwright persistent context按需启动、finally关闭，同一时刻最多一个；忙时返回既有资源延后原因，不排长浏览器锁队列。现有原生Chrome身份/Profile/出口保持原状。共享Profile的首选用途是GuruFocus API失败后的有限兜底，其可用性不能代替API成功率承诺。

服务商响应未提供可核对的单次美元费用头，因此仅报告请求次数、HTTP/browser类别及耗时，**不编造美元成本**。公开API返回HTML也必须经过相同质量与身份检查，HTTP200不等于FULL。主实验请求次数保留在逐篇attempts，另有独立引擎比较，不合并为文章成功率。

参考官方接口：[Jina Reader](https://github.com/jina-ai/reader)、[Zyte API](https://docs.zyte.com/zyte-api/usage/reference.html)。

## 4. Google发现 → 现有原生源即时接管

域名映射：Barrons/MarketWatch→barrons_ticker_news；Reuters→reuters_site_search；Investing→investing_ticker_news。只在原生源已启用、该文章全部ticker已有有效原生绑定时接管，不创造影子绑定或放宽认证政策。Google wrapper解析与原生canonical归并发生在BODY开始前，复用既有Live文章、交付与ARTICLE_ONCE流程，无并行消息管线。

接管保留Google最早发现时间、wrapper/raw_url、原发现URL与原生canonical、目标source，审计GOOGLE_NATIVE_HANDOFF。BODY和最终发布均由原生source身份继续，使用其原有Site Access scope和浏览器策略；不等待下一次轮询。

Google摘要与原生列表摘要属于两个origin观察，不能误当原生实质更新并重复BODY。已知追踪参数、裸域/www、HTTPS及尾斜线在这4个确定映射域中归一；保留文章选择query、国家子域，不扩大未知站点canonical规则。对后到原生URL的相同归一仅复用已有reserve_resolved，不改原生抓取顺序。Google-first/native-first/同时发现及URL追踪变体均测试一个BODY、一个Stream；Runtime唯一Case另由同期跨源去重集成测试覆盖。

**实际原生身份验收有剩余限制**：Barrons测试返回subscription_required；MarketWatch返回access_combinations_unavailable；Reuters12秒受限探针超时；Investing返回no_authenticated_profile_available。这些是账号/现有访问能力问题，接管不会凭空修复权益。没有重置身份、登录或换原生Profile。额外公共比较中Reuters Zyte HTTP仅1/2 FULL，另一篇仍订阅预览；Investing Jina1/2、Zyte HTTP1/2且有超时，尚不值得替换现有原生策略。

对于没有相应原生源的IBD/MarketScreener/SeekingAlpha付费稿、媒体/失效链接，本轮保留原分类，不擅自创建新源/认证或用公开预览冒充全文。

## 5. 回归、发布及绑定

本轮专用解析/路径/接管回归最初61项全部通过，最后Barchart新增2项正反例；最终回执见后续发布附录。此前整组210项通过。按用户最新要求，同步main所有待提交开发代码，包括同期跨源去重、轻量接纳、IBKR双数据源/O2/W3已完成开发；原始抓取HTML、Chrome Profile、私密环境和大体积临时包不作为源码提交。

发布使用现有host-owned storage_release及正式不可变镜像，不使用早先准备但未启用的8文件BODY临时overlay。保留生产现有私有Compose环境、Live/Paper数据源、数据卷和身份。生产版本/测试失败说明/绑定独立回读在完成后追加。

计划绑定当前9个ticker：BE、CEG、COHR、INTC、IONQ、NBIS、ONDS、RKLB、SNOW。已有BE/INTC不重复创建；其余7个新增；MU不修改。脚本先验证实际统一监测词和第一搜索词计划，通过现有审计API写入后独立回读，而非直接SQL改配置。

## 6. 可复核证据

工作目录：`eval/google_news_body_phase2_20261102/`。核心：validation-cohort.json、validation-final.json.gz、accepted-final.json、reader-validation-accepted.json.gz、reader-captures-accepted.json.gz、egress-summary.json、engine-results.json、native-probe-summary.json、native-c-public-comparison.json、final-focused.xml、integration-final.xml。捕获响应是复核材料，不进入运行镜像；真实原文不复制到此报告。失败样本、超时、非文章目标和旧的不正确统计均保留，不用后续成功覆盖历史证据。

### 发布前完整回归结果

扩大范围回归447项：441通过、6失败，333秒。对6条失败在未修改HEAD e111a2b1的隔离源码/测试中独立复现，均失败，保留head-baseline-tests.xml/txt。失败为Event Library三个时间规则、Document2一次reference注入、CDECR失败文档NOOP语义、W3回测运行延迟READY预期；未修改相应生产语义来使本轮全绿。Google最终63项通过；Ruff与编译检查通过。发布工具另修正环境中旧source archive SHA覆盖新镜像身份的问题，previous环境不修改。
