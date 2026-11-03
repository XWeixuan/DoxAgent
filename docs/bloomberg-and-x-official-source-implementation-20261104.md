# Bloomberg 与 X 官方账号源接入

日期：2026-11-04（北京时间）。按用户要求实施两个外部消息源；复用已有采集、CaptureBridge、Live、标准化与发布体系，没有新增常驻服务、浏览器身份或独立测试系统。

## Bloomberg：Massive Distribution 隔离测试

外部唯一源 `bloomberg_massive`，内部独立七单元，目标间隔 30 秒：

- https://www.bloomberg.com/feeds/news.rss
- https://www.bloomberg.com/feeds/economics/news.rss
- https://www.bloomberg.com/feeds/industries/news.rss
- https://www.bloomberg.com/feeds/technology/news.rss
- https://www.bloomberg.com/feeds/business/news.rss
- https://www.bloomberg.com/feeds/markets/news.rss
- https://www.bloomberg.com/sitemaps/news/latest.xml

端点各自检查点/ETag/Last-Modified/内容哈希，最多两个并行 HTTP 请求，同一源每轮每端点只下载一次，供十个目标共用。一个端点失败不阻塞其他端点的独立调度；不等待全组成功再输出。

RSS/sitemap 全项扫描，不因遇到旧条目提前结束，不使用最大时间戳当游标。Bloomberg 保留所有新出现的候选到既有 capture，便于评估晚到/缓存与初始化窗口；原有 Bus 新鲜度接纳条件保持不变，旧候选不会因此进入正文和下游。初始化历史候选与自然新稿必须分开统计。

统一文章 URL、标题及既有正文去重。RSS 描述保存在原始证据 `listing_summary`，不让“RSS 有摘要、sitemap 无摘要”的表现差异反复触发正文更新；真正标题变化继续沿现有修订机制处理，正文沿既有复查规则更新。英语与 ticker 规则按当前生产词库运行，不调用 Decision Model。

十个测试目标：INTC、MU、BE、COHR、NBIS、CEG、RKLB、SNOW、ONDS、IONQ。只有内存测试绑定，真实订阅为零。与前一批测试源一样，BODY、清洗、L2、Raw/Standard 构建后在最后正式发布前写 `test_publications`，不进入正式消息表或下游。正式消息与测试的文章/所有者作用域隔离，14 天留存和资源约束沿用现有实现。

### 实测边界

生产网络七端点均 200 并完整解析；六个 RSS 各 20 项，sitemap 约 446 项。各 RSS 的最新发布时间和覆盖窗口差异明显，不能把七端点加总当独立文章数，也不能用 20 项窗口证明全站无漏抓。实际响应及请求耗时见 `bloomberg-production-canary.json`。

两个真实正文样本均被识别为 `PARTIAL / subscription_required`。保持 HTTP→Reader 的低成本路径，未绕开订阅要求，也未将摘要、订阅墙当 FULL。正文不足时记录 NO_PUBLIC_BODY/待处理，不当作正则阴性；不会为得到“发布成功”降低完整性条件。连续三个订阅墙结果复用五分钟测试站点冷却，继续列表发现，避免逐篇重复访问相同墙面。

自然流量抽查还发现音频节目简介和视频页面导航/简介被旧通用提取误标 FULL：音视频路径改记 `PARTIAL / media_requires_transcript`，本版未实现字幕提取，因此不请求或宣称取得媒体全文；Money Stuff 等 opinion newsletter 的三段付费预览也记 `PARTIAL / newsletter_requires_full_text`，不误作全文。提交层同时防止旧缓存再次变成 FULL。已用同库测试记录校正脚本撤回误发、更新当前质量结论并保存审计；原始抓取、旧快照及坏例保留，不修改正式消息。

这意味着**发现链路可运行，完整正文能力尚未验收通过**；纯公开路径不能承诺覆盖付费正文。需要区分发现覆盖、正文可得、正则命中和真实相关性。

## X Official Account Posts：生产 By Ticker

外部源 `x_official_posts`，使用官方 `GET /2/tweets/search/stream`。一个 Collector 内异步长连接共享所有账号，按 API 的真实作者 `author_id→username` 路由到目标 ticker。不是十条连接，没有网络轮询单元，也不以假轮询回执表示连接健康。

账号配置存储在现有统一监测词的版本化 `x_accounts` 字段，支持多个 handle，去掉 @、大小写归一、去重并校验格式；不将账号复制到 binding 参数或放进 L1/L2。沿用 CAS/幂等/审计、配置修订触发的 Collector 刷新。账号或绑定启停变化及时更新路由及自有流规则，账号变更保留现有连接，不以重连来应用规则，其他语言和规则草稿保留。页面增加账号输入，流式源隐藏轮询间隔编辑。

| ticker | 官方账号 |
|---|---|
| INTC | @intel |
| MU | @MicronTech |
| BE | @Bloom_Energy |
| COHR | @CoherentCorp |
| NBIS | @nebiusai |
| CEG | @ConstellationEG |
| RKLB | @RocketLab |
| SNOW | @Snowflake |
| ONDS | @OndasHoldings |
| IONQ | @IonQ_Inc |

核对了 X 官方用户查询和企业网站链接。特别排除了空白个人账号 `@nebius`、旧名占位账号 `@SnowflakeDB`，使用官网当前指向及公司域名对应的账号。账号后续由用户在统一监测词页面维护，不硬编码为不可修改的监测名单。

采用 `from:<handle>` 规则，不排除回复/转发。只增删 `doxagent:official:v1:` 自有 tag 的规则，其他应用规则保留；接收端再次检查作者及当前 ticker 启停授权。账号变更不依赖旧连接规则标签来决定最终路由。

帖子优先 `note_tweet.text`，保留全文、换行、展开链接、引用/编辑等原始 API 证据和准确发布时间。编辑链首个 ID 作为稳定身份，重复重连幂等，实质更新沿 ARTICLE_ONCE 修订原消息。标题取原帖首行（上限 240 字符），不额外生成摘要或调用标题模型。

原生帖子进入同一个 durable capture/bridge 后直接准备为 READY/NOT_APPLICABLE，**不创建正文下载任务，不借用 BODY slot，不下载帖子所链文章，不跑 L2 或 Decision Model**，随后复用正常标准化和正式发布。并非将短帖冒充 FULL 正文。

流式接收有 90 秒无数据超时、provider 心跳、断开后的指数退避、认证/额度错误冷却和 429 reset 等待。接收循环直接等待持久化，使用既有 SQLite 写入重试，没有第二消息队列。保留当前连接、账号、最近心跳、接收数和断开原因。流断开期间不声称无损补发；本版没有引入定时搜索补洞轮询。

X 源与 IR/SEC 一样支持连续监听，同时遵循 ticker running、绑定/源启停和全量暂停；用户配置的 active_windows 仍生效。凭据只在现有受保护生产 `.env.v2` 中配置 `X_BEARER_TOKEN`，未写入代码、配置台账、Git 或本报告。按服务读取配置载入，规则管理和流连接均使用已验证的现有 US HTTP 出口。

### X 实测与切换条件

官方规则 GET=200、十条 from 规则 POST=201；初始出现 ProvisioningSubscription 503。短探测随后发生读取超时，复测发现空闲流约 20.45 秒才返回首批响应；延长流式读取等待后生产出口得到 200。这是空闲时等响应头/首个心跳的时间，不是测得的帖子发布到接收延迟。并发路由探测触发过单连接上限 429，探测均已退出，生产只保留一个连接，不自动清理可能属于其他应用的连接。

先发布兼容代码和页面，再配置十个账号及真实订阅，确认新连接及全部十账号就绪后才禁用 TikHub 两个源和旧绑定；保留历史消息与审计，不删除旧数据。配置脚本幂等，退役步骤要求新鲜 CONNECTED 状态和完整账号覆盖。

## 测试与版本

- 相关 Python 回归：59 passed，覆盖采集、监测词、跨源 Live 去重及当时专项。
- 最后专项：25 passed，覆盖七共享单元/十目标/零 X 轮询、乱序 RSS/sitemap、跨端点同文、重复捕获、原生帖子无需 BODY/模型即可标准发布、账号归一与 API 读回、自有规则管理、订阅墙冷却、音视频简介、付费 newsletter 预览/旧缓存质量边界、账号修改保持同一连接与旧生产测试边界。
- 前端浏览器：1262px、1559px 共六项通过，包括账号编辑/保存、原监测词不受影响、语言绑定刷新、横向溢出检查；查看了实际截图。
- 浏览器截图：[1262px](../eval/bloomberg-x-official-20261104/x-accounts-1262.png)、[1559px](../eval/bloomberg-x-official-20261104/x-accounts-1559.png)。
- TypeScript typecheck、wire schema 生成、生产 Web 构建通过；修改文件 Ruff E9/F 和 diff 检查通过。

生产真实读回发现并修正了生成器遗漏同步后端 MonitoringTermsValue/BindingConfig 的问题；生产十份监测词和十份 BindingConfig 均通过实际服务 wire 校验，不以 mock 表单替代这一检查。

上述测试组有重叠，不相加当独立总数。主体功能 commit `5e98b47d`，启动出口/退役完整性收尾 `88a7968a`，候选保留与核验 `af875e91`；后端契约 `ddc0dd7c`；媒体质量与保留连接 `88e1999a`；已验证的 newsletter 预览边界 `d87a54ec`。最终实际角色版本、自然流量和零泄漏读回在下方运行记录及证据中列出。没有把模拟帖子测试伪装成 Filtered Stream 自然投递。

## 运维查看

```sh
# Bloomberg 与其他 Massive 测试源共用的状态/正文结果导出
sudo docker exec doxagent-v2-v2-message-bus-1 python -m doxagent.message_bus_v2.test_delivery status --source bloomberg_massive
sudo docker exec doxagent-v2-v2-message-bus-1 python -m doxagent.message_bus_v2.test_delivery export --source bloomberg_massive --limit 50
# 同一采集台账的流状态
sudo docker exec doxagent-v2-v2-message-collector-1 python -c "from doxagent.message_bus_v2.collection_plane.store import AcquisitionStore; print(AcquisitionStore('/data/bus/acquisition.sqlite3').get_meta('x_official_stream',{}))"
```

配置及切换：`scripts/ops/enable_bloomberg_x.py`；只读核验：`scripts/ops/verify_bloomberg_x.py`。在正常容器中执行，使用既有生产挂载，不另起测试守护进程。

官方接口依据：[Filtered Stream](https://docs.x.com/x-api/posts/filtered-stream/introduction)、[Quickstart](https://docs.x.com/x-api/posts/filtered-stream/quickstart)、[连接查询](https://docs.x.com/x-api/connections/get-connection-history)。

运行与回归证据：[eval/bloomberg-x-official-20261104](../eval/bloomberg-x-official-20261104/)。

## 最终运行核验

截止北京时间 **2026-11-04 03:46:46**：

- Bloomberg enabled，七个独立端点均有连续 CAPTURED；每单元十个测试目标，零真实绑定、零正式 raw/standard、零模型决策。
- 当前隔离测试发布 0 条。启动时三类误标记录已校正（其中撤回一条错误的播客测试发布），不是将正文失败当作正则阴性；详情见各 reclassification 及原始坏例证据。
- X 十个真实绑定及对应账号均有效，CONNECTED、HTTP 200，有真实 provider 心跳；官方连接查询返回 **一个活动 filtered_stream 连接**。
- 当前进程收到自然目标帖子 0 条；因此实流帖子入库/下游验收仍为 PENDING_NATURAL_POST。原生帖子标准发布、无 BODY/模型的端到端执行已在隔离本地库用实际字段结构验证，未将模拟帖子塞入生产。
- TikHub Search/User Posts 两源 disabled；原有 MU TikHub 绑定 disabled，历史记录保留。幂等配置脚本重复执行不额外增加账号修订或真实订阅。
- 全部原有 L1/L2/相关性定义内容哈希不变，只按授权新增 x_accounts 及其修订；生产 API 对十份监测词、十份绑定的 wire 校验均通过。
- 七角色 running/restart0；有健康检查的 API/Collector/Web 均 healthy，实际镜像与发布清单一致。没有新增 Chrome/Profile。发布工具因现有资源压力延后旧资源回收，应用发布成功；没有删除身份或卷。

| 角色 | 实际产品 commit |
|---|---|
| v2-message-bus | d87a54ec |
| v2-content-enrichment | d87a54ec |
| v2-message-collector | 88e1999a |
| v2-projector | 5e98b47d |
| v2-api | ddc0dd7c |
| v2-scheduler | 5e98b47d |
| v2-web | 5e98b47d |

测试源继续保持启用；Bloomberg 全文可得性、覆盖/时效与真实相关性尚未完成长期业务验收，X 实流时效也不从空闲心跳估算。上述边界均在 acceptance-summary.json 显式保留。
