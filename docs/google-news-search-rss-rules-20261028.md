# Google News Search RSS 黑名单与窄召回实现记录

更新说明：本文记录第一版“ticker OR 首词”的历史实现和当时估算。2026-10-28后续按用户要求已改为所有ticker只使用首词，并新增10个判黑Site、20个判白Site；当前实现、测试和COHR/MU泛化数据见 [后续评估报告](google-news-blacklist-cohr-mu-generalization-20261028.md)。下文原始估算不是最新生产状态或当前查询规则。

日期：2026-10-28。状态：本地实现、回归测试及公开 RSS 请求验证完成，未提交、未发布生产。

## 实现结果

规则只针对 `google_news_search_rss`，对所有当前及后续 ticker 一致生效。其他消息源的搜索词与接纳规则保持原行为。

1. 查询固定为“ticker 代码 OR 第一个统一监测词 L1 的源语言表达”，忽略第二、第三个 L1。例如 BE 为 `(BE OR "Bloom Energy") when:1d`，INTC 为 `(INTC OR Intel) when:1d`。已有 site 限定继续生效。统一监测词预览与运行查询共用同一生成函数；未迁移统一监测词的旧配置也仅取第一个 `search_terms`。后续修改第一个 L1 会正常刷新查询计划，不需要新增配置开关。
2. 按 RSS 自带的 `<source url>` / `publisher_domain` 匹配黑名单，列表返回时就移除命中条目，避免 Google 跳转解码、正文下载与后续研判成本。未知发布域名正常保留，不为过滤额外发起网络请求。
3. Bus 接纳入口再次应用相同规则，覆盖旧持久采集结果的交付；直接正文入队入口也拒绝命中项。只控制后续接纳，不删除历史消息、不扫描或取消此前已入队的正文任务。
4. 轮询采集元数据记录 `query_rule`、`rss_received_count`、`google_news_blacklist_policy_hash`、`google_news_blacklist_filtered_count`、`google_news_blacklist_filtered_sites`。重复应用过滤不会重复累计已移除条目。

没有增加数据库表、浏览器身份、模型调用或后台扫描；黑名单 JSON 每进程读取并缓存一次，匹配只是小规模字符串比较。

## 初始黑名单与白名单

运行策略文件：[google_news_blocklist.json](../src/doxagent/message_bus_v2/google_news_blocklist.json)。用户确认的 12 个 Site 对应 13 个匹配域名：

|用户判黑 Site|运行匹配边界|
|---|---|
|on3.com|本域及子域|
|247sports.com|本域及子域|
|pcguide.com|本域及子域|
|media.unisba.ac.id|该媒体子域及其下级，不扩大到整所学校|
|ad-hoc-news.de|本域及子域|
|marketbeat.com|本域及子域|
|finance.biggo.com|泛化为 biggo.com 及子域|
|digitalfoundry.net|本域及子域|
|simplywall.st|本域及子域|
|shattered.io|本域及子域|
|tradingkey.com|本域及子域|
|aol.com|aol.com、样本已出现的 aol.co.uk 及各自子域|

域名忽略大小写、`www.` 和末尾点，按完整域名边界匹配，避免把 `notmarketbeat.com`、`marketbeat.com.example.org` 当成 MarketBeat。

人工保留的其余 12 个站点已记录于[白名单文档](google-news-search-site-whitelist-20261028.md)。白名单是人工决策记录，不是运行时“只允许这些站”的限制。

后续添加黑名单：修改 JSON 的 `blocked_domains`，补充人工判断依据，运行域名及入队回归，正常发布并重启使用该策略的采集/Bus/BODY 进程。策略不是热加载，避免每次轮询读文件。若撤销封禁，同时维护白名单记录。

## 日新增量估算

估算只覆盖目前启用该源的 BE、INTC。按用户允许，忽略新增 ticker 代码可能带来的额外召回。域名过滤是历史样本上的精确重算；窄召回只能估算，因为 Google 的全文索引、排序和结果上限不能用本地有限文本完整复刻。

### 实际 Bus 接纳样本：更接近日新增处理成本

读取 `eval/google_news_audit_20261027/audit.json.gz`，按 ticker 内的 `identity_key` 去重并取首次采集时点，选取六个完整纽约业务日：10 月 16、19、20、21、22、23 日，业务日以纽约时间 02:00 切分。黑名单之后，检查标题、留存摘要及正文片段中的第一个公司词。结果保存在 `eval/google_news_rules_20261028/admitted-estimate.json`，复算脚本为同目录 `admitted-estimate.py`。

|Ticker|原日均接纳|仅黑名单后日均|再加窄召回的文本粗估|
|---|---:|---:|---:|
|BE|60.8|50.7|30.8|
|INTC|103.0|78.7|65.5|
|合计|163.8|129.3|96.3|

原样本共 983 个消息身份，黑名单排除 207 个（21.1%），剩 776 个；再按留存文本公司词估算剩 578 个。用于成本规划，可先按合计约 **100 条/完整交易日**、相对原样本减少约 **41%** 理解。正文片段缺失/截断、Google 语义和全文匹配都会影响估计；这不是部署后的保证值，也不是下游相关性通过量。

### 30 天历史 RSS 召回样本：不同口径的交叉验证

冻结窗口为 2026-09-26 20:08:41 UTC 至 2026-10-26 20:08:41 UTC。按 RSS GUID 去重，BE 1,685 条，INTC 2,956 条，共 4,641 条。该样本是按天历史查询召回，并对饱和日增加搜索词分片，不能等同于生产连续轮询的唯一入库消息数。

|Ticker|原召回/日历日|仅黑名单后|窄召回证据粗估后|
|---|---:|---:|---:|
|BE|56.2|47.7|20.6|
|INTC|98.5|83.1|60.1|
|合计|154.7|130.8|80.7|

黑名单精确排除 716 条（15.4%，包含 aol.co.uk 的 9 条）。窄召回列综合标题/摘要/已存正文片段以及历史“仅公司词查询”命中证据，计 2,421 条，约减少 47.8%；缺少公司词证据不等于 Google 全文不会命中，因此不把 80.7 当作严格上限或精确预测。详见 `estimate.py`、`estimate.json`。

额外对同一窗口重新发起 62 次官方 RSS 公司词查询（每 ticker 31 个相交日历日），全部 HTTP 200；黑名单后去重剩 2,197 条，即 73.2 条/日。INTC 20 次、BE 1 次查询达到 100 条上限，且新旧索引不同，这组数据存在漏召回，不能据此声称生产会精确降到 73 条。详见 `estimate-replay.py/json`。

综合判断：两个 ticker 的历史样本显示可显著降低下游负载，实际接纳口径先以约 100 条/交易日规划；最终应在发布后用真实首次接纳计数复核，不能将上述不同窗口和去重单位拼成单一精确预测。

## 验证

- 6 个测试文件的 85 项不同用例分批通过：本轮新增 35 项规则测试，另外覆盖原 Google 适配器、搜索计划、统一监测词、API 预览、正文复用和采集交付等回归。验证了三个 ticker 共用规则、旧/新查询配置、黑名单子域边界、接纳前拒绝、Yahoo 不受影响以及不额外解析 Google 跳转。
- 新模块、搜索计划及新测试通过 Ruff；改动通过 `git diff --check`。
- 6 次真实当前 RSS 请求全部 HTTP 200。BE 旧查询返回 54 条，旧查询加黑名单为 44 条；新查询返回 40 条，加黑名单为 31 条。INTC 新旧均触及 100 条上限，过滤后分别为 93/92 条，不能由单次列表推断全天降幅。原始结果见 `live-query-probe.json`。
- 本轮未进行生产发布，未将离线结果当作生产验收。

主要实现：`google_news_rules.py`、`google_news_blocklist.json`、`news_adapters.py`、`search_plan.py`、`service.py`；回归：`tests/test_google_news_rules.py`。
