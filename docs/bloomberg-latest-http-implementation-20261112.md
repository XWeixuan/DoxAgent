# Bloomberg Latest HTTP 新闻发现：方案与实施（2026-11-12）

> 最终选择：Artemis官方实时快照10秒HTTP轮询 + 全列表30秒独立补覆盖。下文前半保留首轮慢列表的根因取证；**不是以该慢列表通过高时效验收**。最终新增实时路径与验收见后半。Chrome正文按用户后续要求留待账号到位。

## 本轮范围

用户在实施中明确改为先完成列表/新消息爬虫，提供订阅账号之后再开发、测试 Chrome 正文。本轮保留外部 `bloomberg_massive` 单一身份、10个测试Ticker、正则筛选和最终正式Bus隔离；不新增正式订阅，不更改正文质量门，不引入常驻Chrome、身份或第二套测试处理系统。**RSS和Sitemap全部退出新消息轮询，也不作兜底。** 历史记录及其解码测试保留。

## 实测路线与选择

| 路线 | 实测结果 | 选择 |
| --- | --- | --- |
| Latest HTML → `__NEXT_DATA__.props.pageProps.initialState.modulesById` | HTTP可200，但当前Latest的initialState只有导航、breakingNews等，没有modulesById，也没有文章链接 | 不采用；禁止把空结构当成功 |
| 第三方`/api/stories?types=ARTICLE&locale=en&pageNumber=1&limit=3` | 生产直连与美国出口均403 | 不照搬该URL |
| 用户提供的`paginate?id=latest&page=phoenix-americas&offset=0&variation=latest_filtered&type=story_list` | 部分直连/美国请求200、其他403；有效JSON为`latest.items`，每页6条，nextItemOffset递增；offset6/12确实返回不同旧稿 | 是首页Latest模块，不能等同当前Latest全列表；不选作覆盖缩水的隐形兜底 |
| 当前Latest前端“All Latest News”真实调用的`/api/stories` | 直连可200，25条/页，pageNumber=2独立有效；类型参数来自官方客户端 | 首轮主路径；最终作为独立补覆盖单元 |
| Latest前端`your-news` | 属于登录后的个性化组件；不是“All Latest News” | 不拿个性化结果代替全列表 |
| Latest实时推送Artemis | 首轮公共sentry直连412，美国先200后403；继续实测固定日本会话取得有效HTTP快照及批量元数据 | 最终接入HTTP polling；SSE不部署，详见后半 |
| 临时Playwright浏览器对照Latest | 匿名、独立临时Context、只导航Latest；403“Are you a robot?”，未取得有效列表 | 已关闭，不引入浏览器列表轮询；没有访问正文或使用已有登录Profile |

研究参考：[ScrapingBee示例](https://github.com/ScrapingBee/bloomberg-api/blob/main/examples/bloomberg_monitor.py)实际通过ScrapingBee付费代理请求栏目HTML，并不证明裸HTTP或Latest兼容；[Quocthai23示例](https://github.com/Quocthai23/rwa-mobile-app/blob/main/scripts/test-apis.mjs)本身提示403风险。第三方代码用于定位思路，生产可用性以下述实测为准。

当前官方客户端证据：Latest页面加载的`pages/latest-f9b837bf2875977f.js`引用`6623-5b158a9bd6752ba7.js`中的“All Latest News”过滤器，调用25条/页的chronological stories，类型为ARTICLE、FEATURE、INTERACTIVE、LETTER、EXPLAINERS。`your-news`是同包内另一组件，已区分；不是凭模块名称猜接口。

## 缓存与真实时效：不能把HTTP200当作实时通过

生产网络按30秒节奏持续对照约10分钟，每条路径21次请求：

| 请求方式 | HTTP200 | 实际Age | 内容变化 |
| --- | --- | --- | --- |
| 标准stories URL | 21/21 | 最大169秒，虽声明max-age=120仍可能返回旧缓存 | 新记录较慢出现 |
| 相同URL + no-cache/Pragma请求头 | 21/21 | 与标准请求相同 | 没有消除缓存 |
| 相同有效参数 + 共享30秒时间片`_` | 21/21 | 每次0 | 两篇新增可比较记录分别提前120秒、90秒出现 |

时间片不是每次随机数；同一30秒与同一页共享一个键。采用依据是实际Age和内容首次出现对照，不是“加了参数所以肯定新鲜”。该办法只验证减少这层缓存，**没有证明上游数据零延迟**。

| 文章ID | 官方publishedAt（UTC，11月11日） | 时间片路径首次出现 | 标准路径首次出现 | 发布至时间片首次出现 |
| --- | --- | --- | --- | --- |
| TOCIIRKJH6VE00 | 19:04:05.866 | 19:10:51.552 | 19:12:51.347 | 405.687秒 |
| TOAYPVKJH6V400 | 18:59:54.142 | 19:13:51.563 | 19:15:21.345 | 837.421秒 |

两篇平均621.554秒，不满足秒级新闻时效。第二篇是旧发布时间的晚出现文章，不伪装成刚发布。另有Google/CarPlay文章18:57发布，19:07才进入标准请求；时间片首屏已经包含它，属于基线，不纳入成对领先统计。

更换为有效limit=50、去掉types、以及另查paginate模块，均未发现比当时最新记录更近的新稿；limit=100虽HTTP200但不是正常新闻数组，不能当扩窗成功。尚不能把剩余延迟精确归因到某一内部缓存或发布索引；只能确认外层Age=0仍存在此现象，不能通过提高本地轮询频率解决。

## 首轮全列表执行路径（最终保留为补覆盖单元）

1. `bloomberg_latest.py`独立实现列表解析与轮询；`NewswireAdapter`将Bloomberg直接路由至它。即使数据库暂存旧RSS配置，代码也不会回到RSS；直接调用旧feed通道明确拒绝Bloomberg。
2. 首轮源配置为crawler + `bloomberg_latest_http`，Latest页面为entry，真实JSON接口为内部endpoint，`TEST:WIRE:latest`一个共享单元。最终加入独立realtime单元，并发上限改为2；10个Ticker仍复用各单元的一次物理采集。
3. 正常只读第一页25条；遇到冷启动或无已知文章交集才按需翻页，最多4页/100条、12秒网络总预算。完整扫描一页，不在第一个旧时间/已知条目处截断；重复分页、结构错误、首页空列表明确失败，后续空页正常结束。
4. 官方ID作为检查点键，标题+官方updatedAt作为版本信号。保留原publishedAt及EXACT精度，不把发现时刻/updatedAt替代发布时间。摘要只保留为证据，不制造FULL正文或摘要差异驱动的重复任务。
5. 沿用Live已有provider alias/canonical机制：同URL首次导入可复用旧owner，再将官方ID绑定该owner；后续改slug/标题仍为同篇文章的修订，不新建重复owner。测试/正式别名有作用域，保持ARTICLE_ONCE。
6. 保留晚出现的候选到现成capture/审计链路；正常业务新鲜度接纳不变。请求完成时间、解析验证、缓存证据、页数、候选数和真实请求ID沿原采集台账记录，不另造延迟统计系统。
7. 403、挑战JSON、Age超过60秒、无效分页或超预算：`BLOOMBERG_LATEST_FAILED`，保留上次完整检查点、有效部分候选和错误证据，下轮正常节奏恢复。无RSS/Sitemap兜底，也不在403时轮换代理或紧循环重试。

三轮正常节奏生产网络适配器只读试跑：首轮2页50条约3.69秒，后两轮0增量、约0.43/0.44秒，真实传输与解析验证成立。另一个故意无间隔的探测在首轮后连续403，已记录；它不是30秒生产节奏稳定性的证据，也说明不应添加立即连打的重试。

## 配置、测试与运行验收

- 幂等配置脚本：`scripts/ops/configure_bloomberg_latest.py --bus /data/bus/bus.sqlite3 --receipt <path> --apply`。只更新现有Bloomberg入口、内部端点、调度约束与发现模式；不改10个Ticker、监测词、enabled、BODY或正式绑定。
- 回归覆盖真实25条JSON、有效分页/尾页、反爬伪JSON、过期缓存、跨轮去重、更新版本、旧稿晚出现、部分失败恢复、不走RSS、十目标单物理单元、旧owner/新slug修订以及现有测试隔离。
- 最终回归计数、生产角色/配置及自然轮询观察在收尾时追加；缓存改善与真实发布延迟分别报告。

## 账号到位后的正文阶段（本轮未实施）

使用统一Site Access的Bloomberg专属Identity/Profile，先Managed Playwright再按实测需要评估External Chrome/CDP；固定既有出口、单BODY并发、按需启动、短闲置回收、每篇关闭页面。HTTP列表独立运行，不等待身份启动、登录、正文或Reader。

需先修正现有`SharedContentExtractor`对全部`_massive`源强制公共HTTP的路由，将Bloomberg单独交给受治理的浏览器BODY链路。订阅登录后以实际文章核验可见正文、原始精确时间、完整性与订阅墙；继续保留音视频/Newsletter质量边界。本轮已接入匿名Artemis HTTP polling；届时再复核订阅身份的元数据及正文能力、前端首显时间与发现的剩余差距，不扩成第二采集服务。

## 按时效约束继续排查后的最终实现

用户强调不能接受只证明HTTP可用而继续忍受分钟级发布延迟。因此又完成以下对照，不停止在首轮方案：

1. **Playwright与CDP均真实测试**：美国、日本固定出口分别测试有界面Playwright Chromium与Supervisor托管的普通Google Chrome/CDP。四次均为403机器人页、无有效列表，不拿启动浏览器当解决方案。所有自有临时浏览器/显示进程均关闭，CDP停止有回执；未修改既有登录Profile或正式Identity。
2. **找到真正的前端实时协议**：官方`artemis-client/1.1/artemis.min.js`将`STORIES_SESSION/LATEST_STORIES`用于Latest增量。正常页面会话→`/artemis/sentry`可获得匿名短期授权；固定日本、荷兰出口实际取得200。该授权不需要本轮提供订阅，但存在403/412及过期401，不能假设永远可用。令牌只在内存，不进Source配置、checkpoint、日志或Git。
3. **SSE和HTTP polling都实测**：`/artemis/oscillator/stream`确实返回200、`text/event-stream`、`no-store`、文章ID数据与ping。可是当前出口持续出现约10–20秒后的传输中断，尝试HTTP/1.1仍不能证明稳定；不把它作为生产依赖，也不新增流式服务。官方客户端本身亦支持`oscillator/poll/<topic checksum>`。
4. **选择小型HTTP快照**：响应约0.8KB、五个最新ID/slug/publishedAt。两组约10分钟对照中，一组21轮为20次200+一次令牌过期401；另一组19次200+一次初始412+一次过期401。新实现对已确认的401在同轮刷新一次匿名授权；403/412保留失败，仍不轮换出口。该快照与原故事全列表是不同入口，不是RSS/Sitemap。
5. **避免元数据阶段成为新瓶颈**：单ID查询曾持续403；官方客户端另有`/lineup-next/api/stories/<comma-separated IDs>?autofillData={"locale":"en"}`批量入口。实测两条ID一次返回两个`fulfilled`对象。最终采用固定直连、单独无Cookie公共HTTP池读此批量入口，授权会话仍固定日本出口；不是403后换IP。该组合三轮完整适配器试跑：5条/0条/0条增量，零失败，约9.369/0.397/0.320秒，首轮仅4次请求、稳态1次。原组合与新组合改变了多个因素，尚不单独断言是哪一个Cookie导致403。

最终外部仍只有`bloomberg_massive`一个源；内部为`TEST:WIRE:realtime`（10秒、小快照、仅对新/待处理ID做一次批量元数据查询）和`TEST:WIRE:latest`（原全列表30秒，按需恢复翻页）。两个共享单元分别调度、检查点和失败状态，不等待对方。并发上限2，复用当前HTTP配额；没有新守护服务、消息队列或Chrome常驻。官方ID别名统一两路文章和正文owner，十个Ticker不重复下载。

实时路径的有效快照解析后才记录真实传输证明。`id_first_seen_at`来自请求实际收完的时间，不能用排队/dispatch时间冒充发现；原发布时间精度仍为EXACT。批量返回的失败项留在既有checkpoint.pending，正常项通过已有on_page/capture事务先接纳；原检查点不丢失，不为单项失败重复补全已完整取得的旧正文。无论失败在授权、快照、缓存还是元数据，均明确`BLOOMBERG_REALTIME_FAILED`，不以慢列表成功掩盖。

本轮只证明了更直接的官方发现路径和有界连续试跑，**不能把冷启动五条旧快照的发布时间差，当成新稿稳态延迟，也不能仅凭no-store/低Age宣称已经低于一分钟**。最终自然流量的新稿时间差、成功与失败轮数会在下方验收追加；若尚无足够样本，明确保留时效未验收的结论。

## 最终交付边界与生产验收

**结论：HTTP列表采集、统一去重和测试链路已实现并启用；“发布到首次发现足够短”的核心目标尚未通过验收。** 官方实时协议提供了第二种发现入口，但名称和HTTP200不等于已经解决上游延迟。实测仍观察到分钟级晚出现记录，不能把本轮交付描述为已经实现稳定的秒级新闻。

- 首轮全列表版本`b246dc753`已发布Collector/Message Bus；其中native-ID别名由Bus提供。后续双单元版本`686e0e780`只重新发布Collector，保留其他角色既有版本；Collector/Bus均running且零重启。
- Bloomberg配置为v3、两个内部共享单元、10个测试Ticker，幂等配置第二次返回unchanged。正式绑定、正式raw/standard消息及decision-model调用均为0。其他104个源定义、317条绑定、40条监测词修订和默认Profile前后哈希一致。
- 相关回归89通过、1跳过；跳过的是未提供的可选Business Wire历史样本，不是Bloomberg运行验收。修改Python的Ruff E9/F检查通过。详细有界生产观察结果见同目录`bloomberg-latest-http-acceptance-20261112.json`，轮数按physical_run_id去重，不能将十Ticker回执算十次物理请求。

最终观察窗口为北京时间04:28:28–04:33:59（约5分31秒）。全列表11/11轮CAPTURED，平均执行1.475秒、最大7.044秒，实际平均调度间隔29.993秒。实时快照34轮中32轮CAPTURED、2轮FAILED，平均执行1.535秒、最大8.229秒，平均间隔9.900秒；两次失败均为实际Age超过45秒，最大观察Age59秒，保留明确stale-cache原因。两个单元最终健康头均恢复成功。这些执行耗时是轮询成本，**不是发布至发现延迟**。

最终仅新增两个article owner、两条采集capture被既有测试链路ACCEPTED；两路各自发现同两篇，复用native-ID owner，没有变成四篇。两篇均由全列表先发现，实时入口随后看到；当前数据不支持“Artemis比全列表快”的结论。观察期间新发布并被抓到的样本为0，不能提供新稿稳态平均/P95或低于一分钟的成功率。

生产初版还取得一条明确反证：Nikki Haley文章`TOCNC6T9NJLT00`官方19:29:06.220 UTC发布，19:32:15.087558首次入测试article，延迟188.867秒。双单元启用后的观察又发现以下两篇；它们都在观察开始前发布，不混入“观察期间新发布文章”的成功率样本，但晚出现本身仍需如实计入风险：

| 文章 | 官方发布时间（UTC，11月11日） | DoxAgent首次发现 | 发布至首次发现 |
| --- | --- | --- | --- |
| Iran Asks UN to Condemn $20 Million US Bounty for Nuclear Info | 20:23:27.066 | 20:29:58.293381 | 391.227秒 |
| Zelenskyy Says Political Shifts Open Window for Russia Talks | 20:23:03.344 | 20:31:28.315677 | 504.972秒 |

不能从当前证据进一步声称延迟一定来自哪一层内部索引，或订阅一定能消除它。也不能把`capture ACCEPTED`解释为已正式发布到Message Bus：它只是既有测试流水线接纳了采集结果，最终下游隔离仍有效。旧公共HTTP正文的UNAVAILABLE/超时不属于本轮Chrome正文交付；未把摘要当FULL。

已经实际尝试HTTP页面、官方全列表/分页/批量元数据、Artemis快照及SSE、Playwright、普通Chrome/CDP。因此本轮没有停在一个不达标的HTTP方案上。可用HTTP采集保持测试运行、失败明确上报；没有用RSS/Sitemap隐藏时效缺口。匿名浏览器当前均被挑战页阻断，后续用户提供可使用的账号/会话后，先核对可访问Latest首显与实时协议是否仍存在同样延迟，再开发受治理的Chrome正文。**这是尚待解决的访问与时效验收边界，不宣称账号必然解决。**
