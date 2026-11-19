# Yahoo / Google 共享站点黑名单（2026-11-20）

实现使用原有 `google_news_blocklist.json` 和进程级缓存，无第二份名单。后续名单修改随同一次代码发布/进程重载对两源生效；不新增热加载文件轮询。

Yahoo 首先使用 metadata publisher_domain、GraphQL provider.url、source_url 和原文链接中的外部域名。Yahoo 托管域 yahoo.com 及其子域只在 Yahoo 路径跳过；再使用精确出版社名称别名或明确域名标签。未知标签不猜测，不从标题/URL slug 反推出版社。主域与子域按完整域名边界匹配。Google 原有 yahoo.com 拦截行为保留。

RSS、GraphQL、浏览器统一经过 Yahoo mapper，在正文任务和正式接纳之前过滤。现有 service/bridge 及兼容 intake 的共享函数提供补充保护。过滤数量、站点及 policy hash 记录在 acquisition_metadata 的 yahoo_blacklist_* 字段，重复过滤不会重复计数。既有 Google 字段不变。

真实验证：只读生产最近1,000条 Yahoo live_articles 输入，全部有效；RSS 358、浏览器400、GraphQL242。可识别外部域名751（75.1%），命中162：trefis.com 92、simplywall.st 63、marketbeat.com 7。剩余249（24.9%）包括 Yahoo 自有内容和缺少充分来源信息的托管转载，保留；不能把75.1%当成真实出版社识别准确率，也不能声称未知转载已全部覆盖。原始采集输入保留于本地 eval/yahoo-blacklist-20261120/production-inputs.json，回放结果 validation.json。一次1,000条模型验证及判定约28毫秒，不代表严格性能基准。

新增测试覆盖黑名单子域、域名边界、Yahoo托管转载、正常Yahoo、未知来源、畸形域名、共用名单变更、重复统计，以及三渠道mapper；既有Google测试按新授权更新Yahoo例外断言，其他Google预期保留。未发布生产。

验证结果：Google/Yahoo联合回归98项通过；随后补充原文外链和Yahoo域+出版社名称组合用例，新增来源过滤测试18项全部通过。

生产发布：北京时间2026-11-20 01:18—01:20，提交b4b360192已推送origin/main并同步远端；沿用带锁发布/回滚管理，在原角色镜像上窄覆盖四个源码/名单文件，更新v2-message-collector、v2-message-bus、v2-content-enrichment，保留其余服务与身份。三个角色运行中，四文件均与提交字节哈希一致，容器内黑名单命中/Yahoo例外/Google规则断言通过。短窗口自然RSS/GraphQL/浏览器分别49/50/48轮CAPTURED，唯一FAILED为采集重建时SNOW SUPPLEMENT_HEAD的CANCELLED，未观察到规则异常。统计样本并非长期稳定性验收。证据位于eval/yahoo-blacklist-20261120/production-verification.json、natural-cycles.json及release-output.txt。
