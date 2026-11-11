# Bloomberg Latest HTTP 新闻发现：方案与实施（2026-11-12）

## 本轮范围

用户在实施中明确改为先完成列表/新消息爬虫，提供订阅账号之后再开发、测试 Chrome 正文。本轮保留外部 `bloomberg_massive` 单一身份、10个测试Ticker、正则筛选和最终正式Bus隔离；不新增正式订阅，不更改正文质量门，不引入常驻Chrome、身份或第二套测试处理系统。**RSS和Sitemap全部退出新消息轮询，也不作兜底。** 历史记录及其解码测试保留。

## 实测路线与选择

| 路线 | 实测结果 | 选择 |
| --- | --- | --- |
| Latest HTML → `__NEXT_DATA__.props.pageProps.initialState.modulesById` | HTTP可200，但当前Latest的initialState只有导航、breakingNews等，没有modulesById，也没有文章链接 | 不采用；禁止把空结构当成功 |
| 第三方`/api/stories?types=ARTICLE&locale=en&pageNumber=1&limit=3` | 生产直连与美国出口均403 | 不照搬该URL |
| 用户提供的`paginate?id=latest&page=phoenix-americas&offset=0&variation=latest_filtered&type=story_list` | 部分直连/美国请求200、其他403；有效JSON为`latest.items`，每页6条，nextItemOffset递增；offset6/12确实返回不同旧稿 | 是首页Latest模块，不能等同当前Latest全列表；不选作覆盖缩水的隐形兜底 |
| 当前Latest前端“All Latest News”真实调用的`/api/stories` | 直连可200，25条/页，pageNumber=2独立有效；类型参数来自官方客户端 | 本轮唯一主路径 |
| Latest前端`your-news` | 属于登录后的个性化组件；不是“All Latest News” | 不拿个性化结果代替全列表 |
| Latest实时推送Artemis | 官方客户端支持SSE及HTTP polling；公共sentry直连412，美国先出现一次200/token，随后403；尚不能证明稳定授权及新闻流可用 | 记录后续调查入口，不上线不可靠流、不声称一定需要付费订阅 |
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

## 已实施的执行路径

1. `bloomberg_latest.py`独立实现列表解析与轮询；`NewswireAdapter`将Bloomberg直接路由至它。即使数据库暂存旧RSS配置，代码也不会回到RSS；直接调用旧feed通道明确拒绝Bloomberg。
2. 源配置为crawler + `bloomberg_latest_http`，Latest页面为entry，真实JSON接口为内部endpoint，只有`TEST:WIRE:latest`一个共享单元。30秒一次，最多一个并发；10个Ticker复用同一物理采集。
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

需先修正现有`SharedContentExtractor`对全部`_massive`源强制公共HTTP的路由，将Bloomberg单独交给受治理的浏览器BODY链路。订阅登录后以实际文章核验可见正文、原始精确时间、完整性与订阅墙；继续保留音视频/Newsletter质量边界。届时再验证Artemis授权、前端实际首显时间与HTTP列表的剩余差距，决定是否值得接入现成HTTP polling；不预先承诺授权可用，不扩成第二采集服务。
