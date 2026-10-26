# Google News Search RSS 前置现状勘察（2026-10-27）

本轮为只读生产勘察，不修改查询词、过滤规则、调度、身份、源订阅或生产服务。结论是：**当前需要优先改善查询相关性、全局调度/接纳等待和通用正文判定；Google RSS 网络下载本身不是主要瓶颈。账面完整正文比例未达 70%，存在错误 FULL，不能直接用任务 completed 比例验收。**

## 1. 数据范围与口径

- 主快照时间：2026-10-26 18:31 UTC / 北京时间 2026-10-27 02:31 / 纽约时间 2026-10-26 14:31。使用生产 Bus owner 数据库只读连接与原始正文文件；定向索引查询，不复制数据库，不使用会执行迁移的 Repository 构造器。抽样正文补读在同轮稍后完成，均按固定 raw_id。
- 消息窗口：2026-10-16 06:00 UTC 至快照，共覆盖 10 个完整自然语义日和当前部分日。沿用系统纽约时间 02:00 切日；不是北京时间零点，也不是 09:30 开盘切日。完整交易语义日为 10/16、19、20、21、22、23；10/26 未结束。周末单列，不用它稀释交易日日均。
- 轮询逐轮台账最早从 10/24 00:19 UTC 开始；不能据此反推此前一周的周期兑现。当前版本窗口自 10/26 00:16 UTC，另看当前语义日及最后一小时。
- 消息按 `(ticker, source_id, identity_key)` 合并修订，取本窗口最早 Raw 记录计首次入库，最新修订计正文现状；1353 个 ticker 内消息身份不是跨站新闻事件去重数。存在 12 条额外 Raw 修订，不把它们算成新文章。
- 站点比例以已进入 Standard/Stream 的消息为分母；优先用 RSS publisher_domain，结合已解码 URL。7 条仍保留 Google 包装 URL，按 RSS 声明发布者归属；这不是全部页面最终落地域名已验证的证明。
- `content_evidence.kind=FULL` 是存储层现有完整标签；SHORT_FULL 在此证据层归入 FULL。`BODY` 仅表示已有正文，不视作完整；SUMMARY 也不算成功。终端任务 FULL/SHORT_FULL 另外统计，SKIPPED/completed 不代替完整正文。
- 最新配置版本为 source v3；10/26 00:15 UTC 的更新说明是将列表 HTTP 与包装链接解码预算分离，并将源并发设为 4。历史均值混有旧实现，当前日表单独衡量。部署基线按本轮先前读取为 12bf384c2c611eb4d9246e2577e473b96ac37c12，代码勘察基于本地相应模块；本轮没有发布。

## 2. 订阅与实际查询

生产消息总线登记 9 个 ticker，**仅 BE、INTC 两个订阅 Google News Search RSS，2/9=22.2%**。它们均启用、按搜索 By Search、英语、60 秒目标周期、立即发布、正文异步补全；该源不是自动绑定所有 ticker 的默认源。

|ticker|实际查询（快照 payload 证据）|绑定中遗留的专题词|
|---|---|---|
|BE|`("Bloom Energy" OR "fuel cell" OR "data center power") when:1d`|10 组客户、部署、质保、制造等专题词|
|INTC|`(Intel OR foundry OR Xeon) when:1d`|10 组 foundry customer、18A/14A、封装、CHIPS 等专题词|

`query_plan` 存在时，适配器使用统一监测词 L1 渲染结果，而不使用 binding.search_terms。两者统一监测词当前均 revision 2。**不能把绑定里的 10 条精细专题查询当成当前实际搜索行为。**这里不是简单“公司名搜索”：foundry 是独立 OR 条件，fuel cell 等也是行业宽召回。

源参数支持最多 10 个 domains（追加 site: 约束），但两绑定均未设置。当前隐藏 publisher/domain 环境配置均为空。By Search 通过 L1 搜索取得候选，接纳流程没有自动应用统一词定义里的完整 relevance/irrelevant 语义判断；那份定义不能被理解成已经生效的正文前过滤器。

列表只做一次 HTTP GET、解析 RSS 和时间过滤，不开浏览器、不逐条解包装；没有分页或饱和后的自动分拆查询。返回 100 条且标为 COMPLETE，只代表这次执行流程完成，不能证明 Google 匹配新闻已全部覆盖。

## 3. 消息量与正文现状

### 3.1 实际入库量

|语义日（纽约 02:00 起）|BE 新身份|INTC 新身份|合计|
|---|---:|---:|---:|
|2026-10-16|38|79|117|
|2026-10-19|61|133|194|
|2026-10-20|37|83|120|
|2026-10-21|63|133|196|
|2026-10-22|86|124|210|
|2026-10-23|80|66|146|
|2026-10-26（部分日）|41|101|142|

六个完整交易语义日日均：**BE 60.8、INTC 103.0，合计 163.8 条/日**；当前部分日 142 条。周末 10/18、24、25 共 228 条。整个消息窗口 1353 个身份：BE 466、INTC 887；Raw/Standard/Stream member 都有 1365 条，其中 BE 473、INTC 892，差额为修订。窗口前 10 个完整自然语义日日均为 121.1 条，不能与交易日日均混用。

原始 observation 有 BE 571、INTC 1105 个输入版本，累计 seen_count 分别 30578、48274。这不是每天新消息数。10/24 起保留的物理 capture 内累计条目分别 75267、109536，含大量重复 RSS 候选；与 10/16 起消息窗口不同，不拿两者直接计算“丢弃率”。

### 3.2 消息层完整标签

|ticker|已入库新身份|最新 FULL 标签|SUMMARY|BODY（非完整）|完整标签比例|当前部分日 FULL/新身份|
|---|---:|---:|---:|---:|---:|---:|
|BE|466|244|203|19|52.36%|22/41 = 53.66%|
|INTC|887|539|312|36|60.77%|55/101 = 54.46%|
|合计|1353|783|515|55|57.87%|77/142 = 54.23%|

这是入库文章的最新状态，尚非严格“真实完整”成功率；付费预览、相关推荐污染会高估质量。也不能把已有正文 BODY 加进 FULL，把比例算成 61.9% 后作为完整正文验收。

### 3.3 保留下来的新文章任务 cohort

以 work_kind=new_article 且 body_result_key 去重，BE 114、INTC 201 个，均无同 cohort 重复 key；不是所有历史任务都保留终端记录，不能用此 cohort 代表全窗口。

|cohort|BE|INTC|合计|
|---|---:|---:|---:|
|保留终端新文章 FULL/SHORT_FULL|80/113 = 70.80%|127/201 = 63.18%|207/314 = 65.92%|
|另有未终端新文章|1|0|1|
|当前日终端新文章 FULL/SHORT_FULL|30/49 = 61.22%|68/113 = 60.18%|98/162 = 60.49%|

失败/PARTIAL 必须计入终端分母。未终端的 1 个不能默默剔除：含它时当前 cohort 完整率为 98/163=60.12%。该日任务 cohort 与按 Raw 首次入库日分组不同，因此不能用 163 与上面的 142 做等式。

关联取回的全部任务 601 个，除 315 个 new_article 外，还有 **280 个 recheck（46.59%）与 6 个 content_update**。快照有 BE 48、INTC 98 个 queued/retry_wait，145 个是 recheck，另 1 个是新文章。说明重复检查/复用是实质性负载，但不能把所有 recheck 都当作错误：它们包含正常完整性恢复，须继续按内容变化和既有 FULL 区分。

## 4. 周期兑现与时效

### 4.1 周期兑现：按所有应轮询时隙计算

复用仓库 `cadence.py` 的 ALL_SCHEDULED_CYCLES 算法：按有效 SCHEDULE/版本/实际 request 与 capture 计算，PAUSED 不算应执行周期，缺失/逾期计入，末尾未完成周期按 pending 规则分开。fresh 要求在该目标周期内取得列表；它不是 HTTP 成功率。该函数某些 unit 延迟分布会读取窗口前 run，因此下面阶段耗时另行显式时间过滤，未直接使用其跨窗延迟均值。

|窗口|ticker|应执行|周期内 fresh|缺少 dispatch|末尾 pending|已结算周期兑现率|实际刷新间隔均值 / P95 / 最大（秒）|
|---|---|---:|---:|---:|---:|---:|---|
|台账以来|BE|1324|1059|134|0|79.98%|66.48 / 110.59 / 480.45|
|台账以来|INTC|1326|1054|143|1|79.55%|66.87 / 108.44 / 508.60|
|当前版本|BE|804|711|46|0|88.43%|63.55 / 84.33 / 418.33|
|当前版本|INTC|806|720|41|1|89.44%|62.89 / 86.78 / 245.16|
|当前语义日|BE|749|656|46|0|87.58%|63.97 / 89.61 / 418.33|
|当前语义日|INTC|750|665|41|1|88.79%|63.34 / 90.50 / 245.16|
|最后一小时|BE|60|27|19|0|45.00%|86.53 / 219.54 / 273.89|
|最后一小时|INTC|60|31|17|1|52.54%|83.83 / 219.93 / 239.79|

台账期间有真实 PAUSED 段（包括周末）；这些段不伪造成故障。当前日恢复于 06:01:35 UTC，target_due 仍可能继承暂停前旧 epoch：极端 due→capture 为 BE 16993.7 秒、INTC 16969.5 秒。这类数值含恢复后的陈旧 due，不是 HTTP 连续执行 4.7 小时，应单列；也不能为了指标好看把有缺失的活动周期抹掉。

当前日 BE 749 个应执行周期、INTC 750 个，fresh 分别 656、665；兑现约 **87.58%、88.79%**，明显未接近 100%。均值刷新约 64/63 秒掩盖了 P95 约 90 秒及最长 4–7 分钟的尾部。最后一小时进一步退化到 45.00%/52.54%，属于快照附近的真实现象，未确认是否与其他运行维护/负载峰值重叠，不能直接归咎 Google 限流。

### 4.2 当前语义日每轮耗时：网络快，前后等待慢

|步骤/口径|BE 均值 / P95（秒）|INTC 均值 / P95（秒）|
|---|---:|---:|
|dispatch → 实际 HTTP request|4.96 / 17.87|4.86 / 17.15|
|HTTP request → response 完成|0.168 / 0.392|0.222 / 0.513|
|适配器 acquisition（含轻量解析）|0.189 / 0.441|0.264 / 0.565|
|dispatch → run finished|9.61 / 41.24|11.74 / 40.97|
|capture → durable commit|2.32 / 9.55|3.58 / 10.20|
|capture → 首次 intake（全部已开始 capture）|502.82 / 2056.35|378.55 / 2004.55|
|capture → 全 capture intake 完成|791.94 / 3359.38|1116.43 / 5749.69|

最后两行包括重复/旧条目的接纳积压，**不是每一条新文章的接纳时间，更不是正文下载时间**。当前日 capture 尚 PENDING 的还有 BE 99、INTC 106；最早分别来自 16:32、16:00 UTC，以上完成样本均值有右删失，不能作为积压上界。最后一小时完成 capture 数为零，尤其不能据此报“平均 intake=0”。

在有完整 request/response/finished 时点的相同轮次中，BE dispatch 总耗时约 51.7% 在发请求前、1.7% 在网络、46.6% 在 response 后；INTC 约 41.4%、1.9%、56.8%。response 后包括持久化、状态/回执等，尚无足够细粒度计时把它全部命名为“SQL 耗时”。capture→commit 与 response 后重叠，不再次叠加占比。

最后一小时网络仍仅 0.18/0.23 秒，但 dispatch→request 升至约 21 秒，dispatch→finished 升至 35.55/38.11 秒。与两个 ticker 同时恶化的现象一致，优先定位共享调度、StorageLane、读/准备/写提交争用和事件循环调度；**单纯加源 HTTP 并发或浏览器页数不能解释也不能解决主要等待**。

### 4.3 只看当前日真实新文章任务的链路

用 job.poll_run_id 关联物理 capture；部分旧 run 不在保留期，样本量分别标注，不把缺失关联当成零耗时。

|指标|BE 均值 / P95（秒）|INTC 均值 / P95（秒）|
|---|---:|---:|
|capture → 新文章入正文队列|13.64 / 69.10（48）|35.17 / 47.42（111）|
|入正文队列 → budget_started|3.40 / 16.02（50）|4.28 / 12.66（113）|
|budget_started → 正文终端结果|27.53 / 110.80（49）|27.19 / 170.70（113）|
|入正文队列 → 正文终端结果|30.98 / 124.66（49）|31.47 / 182.37（113）|
|capture → 正文终端结果|43.46 / 199.26（47）|65.97 / 197.70（111）|

这里的终端结果含失败，不能理解为成功正文更快。INTC 一条 capture→job 等待 2458 秒导致均值大于 P95，必须保留为尾部 badcase。budget_started→终端仍可能包含内部并发/身份等待和重试，不是纯网络 CPU 时间。另有当前日新文章 job `enrich-1d07235c255548d8b75ca92c84f955623563c2af33aef128b844b1d5f1b3c1a5`：预算 180 秒、attempt_count=3，budget_started→终端实测 334.24 秒，最终 deadline_exceeded；`enrich-ae6595ca87d2d8935ca6b8067f44ba3934664e85cc65806451443a7ee34bc06b` 同类约 309.24 秒。这证明墙钟终端时延没有被名义 180 秒预算完全约束；还需分清重试排队/调度迟滞/终端持久化与真实网络占用，不能称为“下载连续花了334秒”。

因此，若“端到端轮询 <10s”定义为 dispatch→列表持久化/新候选接纳，目前尚未稳定满足；若包括完整 BODY，则当前约 27 秒的执行均值也需优化，且不能要求所有付费/反爬页面都在 10 秒内完整成功。若定义为发布者发稿→发现，也受 Google 索引与 RSS 更新延迟约束，不能仅靠本系统 60 秒轮询承诺 <10 秒。

## 5. 最终进入 Message Bus 的站点构成

窗口 1353 个消息身份来自 **435 个 publisher domain**。其中 336 个域名各自仅 1–2 条，共 404 条（29.86%）；仅为少数高频站写配方不能解决整个长尾。

|发布者域名/组|消息数|占比|FULL 标签数 / 消息数|
|---|---:|---:|---:|
|marketbeat.com|76|5.62%|63/76|
|on3.com|56|4.14%|30/56|
|ad-hoc-news.de|45|3.33%|40/45|
|finance.yahoo.com|32|2.37%|24/32|
|tradingview.com|28|2.07%|20/28|
|247sports.com|27|2.00%|16/27|
|marketscreener.com|24|1.77%|3/24|
|247wallst.com|23|1.70%|19/23|
|aol.com|22|1.63%|12/22|
|in.investing.com|21|1.55%|3/21|
|tradingkey.com|20|1.48%|20/20|
|fool.com|19|1.40%|18/19|
|gurufocus.com|19|1.40%|19/19|
|theglobeandmail.com|18|1.33%|8/18|
|simplywall.st|18|1.33%|14/18|
|uk.investing.com|18|1.33%|2/18|
|stocktwits.com|17|1.26%|8/17|
|benzinga.com|17|1.26%|6/17|
|seekingalpha.com|16|1.18%|0/16|
|moomoo.com|16|1.18%|1/16|

前 20 域名合计 532 条，占 39.32%。Investing 各国家域合并 81 条（5.99%）；不能只看主 investing.com 的 10 条。完整 435 域名分布见 `eval/google_news_audit_20261027/site-composition.csv`，包括分 ticker 数量和标签比例。

这个分布说明：

1. INTC 的 on3.com 56 条、247sports.com 27 条，合计 83/887=9.36%，都是体育站；它们不是金融领域的低可信站，而是当前 Intel 查询的同词误召回，不能把“可信度”和“业务相关性”混为一谈。另有 drinks-intel.com 8 条、intelregion.com 5 条及剧院/音乐/Palantir Foundry 等误召回。
2. Marketscreener 仅 3/24 FULL；Investing India 3/21、UK 2/18、Nigeria 1/14、Canada 0/12；TechPowerUp 0/11。它们包含值得保留的行业/交易信息，是正文适配优先对象，不应为抬高比例直接排除。
3. MarketBeat 76 条中 63 个 FULL，既有分析师评级，也有常规小额基金持仓模板。高 FULL 不等于高信息增量。
4. 主流/专业媒体与官方源仍有价值，但占全量有限。保守列举 6 个官方/学术域名（intel.com、investor.bloomenergy.com、microsoft.com、energy.mit.edu、news.engineering.tamu.edu、stories.tamu.edu）共 8 条；另列 31 个主流/专业行业站点优先适配集合共 115 条，合计 123/1353=9.09%。这是**已列举的优先审查站点覆盖量，不是“只有 9.09% 新闻可信”，其余也不能判低质量**。官方文章也可能仅为公益活动而非经济事件。

31 站集合：Reuters、Bloomberg、CNBC、Barron’s、WSJ、MarketWatch、Axios、THE ELEC、DIGITIMES、Tom’s Hardware、ServeTheHome、Ars Technica、Phoronix、Notebookcheck、VideoCardz、TechPowerUp、TweakTown、HotHardware、Wccftech、EE Times、EE Times Asia、EE Times India、Data Center Dynamics、Data Center Frontier、Data Center Knowledge、Microgrid Knowledge、Hydrogen Insight、FuelCellsWorks、TechTarget、The Register、TrendForce。这里按业务优先级列举，未逐篇外部事实核验，不是站点信用评级。

## 6. 文章质量抽样与错误 FULL

### 6.1 抽样方法与结果

BE/INTC 各随机抽 40 个已入库新身份，固定随机种子 20261027，按 raw_id 排序后取样，合计 80。抽样结果及标签保留于 `quality-sample.csv/json`。先看标题、已取得正文/摘要；另按 80 个 raw_id 点查存储的完整正文，检查付费预览与尾部污染。不是重新向网站发起 80 次抓取，也不是已对 80 篇逐一做事实核验。

人工初筛定义：H=存在值得继续取正文的新事件/具体经济事实候选；M=背景、观点、预测或需要进一步确认的相关分析；L=常规持仓模板、价格复述、营销/生活化低增量；X=明显业务无关；U=来源/内容可疑尚不能核验。不按涨跌方向打分，不把所有观点都丢弃。

|ticker|H 新事件候选|M 背景/分析|L 低增量|X 不相关|U 待核验|
|---|---:|---:|---:|---:|---:|
|BE（40）|20|6|11|2|1|
|INTC（40）|14|9|7|10|0|
|80 篇样本计数|34|15|18|12|1|
|按实际消息量加权估计|40.17%|19.92%|20.94%|18.11%|0.86%|

因此估计 **39.06% 为 L+X**，有较大减负空间，但不是可以立刻安全删除 39.06% 的证明；样本小、单人标注且 SUMMARY 只能做标题级筛选。H 是值得处理的候选，不能直接报“40.17% 已证实高价值新闻”。全部精确高低质量比例仍需要后续更大样本和业务研判标签验证。

典型对照：
- #54 THE ELEC：Intel 推出 Xeon 推理软件，有明确产品事实；#60 HotHardware：Modal 的 Vera CPU 吞吐对比，属于竞争技术信息；#21 MK：韩国 AIDC 用电规划收缩，正文包含需求数字；#22 Pulse2：Vertical Semiconductor 至多 7300 万美元 CHIPS 意向资助。这些是应保留候选，不因为未直接提目标公司就排除。
- #29 Quiver、#37/#74 MarketBeat：常规基金持仓更新；#34 单纯日内涨跌；#36 Bloom 官方消防员捐赠，来源官方但经济价值低。
- #49/#67/#80 体育招聘 intel；#59 Foundry Theater；#66 POSCO 使用 Palantir Foundry；#69 EDF intel chief 的军事情报。这些是词义误匹配。
- #2 cristinaiglesias.com 上的 Bloom 财经长文：域名/主题组合可疑，只列 U，不能无证据断言其必为 AI 垃圾或虚假，也不能因正文很长就当高质量。

### 6.2 FULL 质量检查

80 个样本中 46 个账面 FULL；确认 **4 个（4/46=8.70%）实际上为付费预览/模板凑长**：#48 Law360（866 字符）、#49 On3（923）、#67 On3（852）、#80 247Sports（1046）。可见 `subscribe now`、`Already a subscriber` 或首段后完整订阅广告，并无付费全文。它们经 trafilatura/article_dom 提取，不能靠 800 字符阈值判断完整。

其中 #48 是“FCC 给 Intel 自动车辆一年豁免”的有效业务新闻，**即使完全修好召回过滤，仍会留下真实重要文章的错误 FULL**。体育预览则同时暴露召回和完整性两类问题。

46 个 FULL 中另有 13 个明显带入推荐文章、作者履历、课程/试用推广或相关股票列表；不能因此一律判为缺失主文，但它们说明清洗不充分，会扩大 downstream 上下文。其余 29 个仅是本次存储正文检查未见上述明显问题，并未与网页原文逐段比对，不能认证为真实完整。

现有 57.87% 的全量标签率已低于 70%，再加上误判，**没有证据证明真实完整正文成功率已达标**。后续必须同时报 complete、partial、summary、paywall preview 与质量抽检通过率，禁止用增加噪声文本把篇幅凑到 FULL。

## 7. 正文失败机制与资源消耗

### 7.1 已部署链路

`RSS HTTP → durable capture → CaptureIntake → 新候选/去重/接纳 → BODY queue → Google wrapper 解码 → 最终 URL 复用检查 → publisher Site Access → HTTP/必要时 browser/Reader → 完整性选择 → Raw/Standard/Stream`。

- Google resolver 有持久化 URL 缓存、同 wrapper single-flight、进程 4 个解码槽；旧链接可本地 base64，现代格式需 GET Google article 页再 POST batchexecute。客户端 8 秒超时，外层 20 秒预算；该 POST 是网站内部协议，不是承诺稳定的官方公开 API。列表成功不等于解码成功。
- 未配置专属站点的发布者进入 generic publisher 运行策略，仍通过 Site Access 共享资源。已命名站点可走已存在 publisher BODY 能力；不应替 Google 为每个域名新建常驻 Chrome。
- 通用顺序默认 HTTP→browser→Reader，但受站点策略、身份及完整性检查控制。HTTP/浏览器/Reader 默认阶段预算约 8/25/12 秒；身份恢复 deferral 直接停止级联，订阅/权限不足不应继续无效 Reader 尝试。高风险站点会限制为适当身份路径。
- record.fetch_url 优先 url，本轮确认解码后 replace(url=target) 可以使用最终发布者 URL；raw_url 保留 Google 并不会把正文重新错误抓回 Google。

### 7.2 当前能量化的失败

保留的新文章任务 314 个终端：207 FULL/SHORT_FULL，102 UNAVAILABLE，5 PARTIAL。最常见终端原因：empty_extract 36、attributeerror 36、transport_timeout 8、risk_combinations_exhausted 7、no_authenticated_profile_available 5，另有 deadline、incomplete、HTTP/DNS/identity 等。**AttributeError 占非完整终端 36/107=33.64%**，是实现异常信号，不能归类为站点反爬。

但 36 个 attributeerror 在当前语义日新文章任务中为 0；已有记录主要来自更早 cohort。本轮容器可用日志范围未找到对应异常栈，不能断言此时仍在复现，更不能凭 reason 猜具体代码行。后续先用原 job_id/URL 重现并保留异常栈；已修复的历史失败也应检查是否需要定向恢复。

保留新文章的 access trace：

|phase|BE 次数 / 平均秒|INTC 次数 / 平均秒|
|---|---:|---:|
|direct|78 / 2.24|149 / 2.11|
|browser|28 / 6.26|59 / 6.02|
|Reader|19 / 5.82|54 / 5.23|

这些是留有 trace 的阶段尝试，不含全部解码、解析、slot 等待和所有失败，不能与约 27 秒新文章执行均值直接相加作分摊。BE 23/114、INTC 60/201 个新文章任务出现多阶段 trace，至少 83/315=26.35% 存在级联访问。

尤其 browser 的 87 次尝试中，**67 次报 net::ERR_BLOCKED_BY_CLIENT（77.01%）**，成功 ok 仅 15 次；仍有其他身份/拒绝原因。它在当前日 trace 也出现，不应仅当成旧问题。该字符串代表浏览器客户端侧拦截现象，可能涉及请求路由/策略/扩展/运行环境；不能直接解释成发布者 HTTP 403。后续应复现一个有效业务页面，检查 Chrome navigation、跨域与请求拦截配置、Site Access 返回观察，而不是对所有站扩大浏览器预算。

另一个区别：HTTP/Reader 的 transport ok 只说明返回了响应，不说明提取成功。比如 INTC 的 PCGuide 任务 `enrich-d2183a851b8dbffe76cc1e258a74067a4ecc5918e7a26a96ee7102353ce181a7`：direct ok ~1.29 秒，browser 被客户端拦截 ~6.32 秒，Reader ok ~5.38 秒，最终仍 empty_extract。这是可重放的跨阶段低效 badcase。

### 7.3 复用与身份边界

已存在 article body result、wrapper→final URL 缓存与 final URL resolved_reuse，并非完全没有去重。重复 capture 还会按输入快照 hash 复用完成回执，fresh candidate 优先入 intake；当前实测仍存在积压，说明“有这些机制”不能代替效果验收。

Google observation 的 language 未设置，取回的 476 个关联 body_result 全为 `language=''`，虽然源 content_language=en。final URL 复用要求 language 完全相同、访问 scope 兼容、FULL/SHORT_FULL、有效 TTL 且 verified_hint 命中；direct 源通常显式 en，Google 标题/摘要又常带发布者后缀，因此跨源同 URL 不一定命中。**这是代码与数据支持的潜在复用障碍，尚未量化实际少命中多少次**。不应为追求命中放松付费身份 scope 或不核实文章更新。

Google 包装链接使 fetch_key 初始归到 `scope:source:google_news_search_rss`；不能把它按包装 URL 当成真正 publisher，也不能把所有失败都记为 Google 的站点失败。应维持两层：采集来源 Google + 最终 publisher/site。

## 8. 覆盖与时间窗口风险

RSS 实际查询是 when:1d，适配器也保留 24h；共享 REALTIME admission 对精确发布时间超过 30 分钟的首次未知文章通常拒绝（已知文章、闭市补扫另有规则）。**Google 晚收录一篇高价值旧文章时，即使反复出现在 RSS，也可能到不了正文/消息流。**这与用户要求“兜底”有关，不能只把过期条目全部视为低价值。

保留快照按输入 hash 合并，BE 全取 327 组、INTC 取 421 中 400 组（hash 顺序系统抽样，并非随机日分层）。各组只取一个代表 capture，代表不保证为该 hash 最早出现时间。因此下述年龄是“样本中首次见到”的年龄，不是 Google 首次收录时延：BE 去重候选 175，其中 118（67.43%）在样本观测时已过 30 分钟；INTC 326 中 211（64.72%）。不能把这些比例直接称为丢失率，有些早已接纳或本就是重复旧条目。

当前日 INTC 711 个 capture 中 **442 个返回 100 条（62.17%）**；BE 为 37–72 条，没有 100 条饱和。这至少表明 INTC 查询宽、结果有饱和风险。没有分页、没有稳定游标，也没有搜索索引全量 ground truth，所以“HTTP 全成功、每分钟轮询、COMPLETE”都不能证明没有漏重要消息。未来按查询拆分必须有统一内部调度与跨查询/跨发布者去重，不能变成源定义碎片化。

已入库消息的 published→Raw 平均 BE 9068 秒、INTC 9854 秒，P95 分别 74138/67842 秒。这混入闭市补扫、晚收录和正文/接纳等待；本轮未把其当成 REALTIME 新消息发现 SLA。尚缺“发布者真实首次发布时间→Google 首次收录”的外部基准，也没有证明所有原本高价值消息均被召回。

## 9. 后续优化应按什么顺序推进（本轮不实施）

|优先级|动作与直接切入点|收益/验收重点|
|---|---|---|
|P0|给 Google dispatch→request、response→capture commit、fresh intake 各阶段补齐关联计时；沿现有 StorageLane/CaptureIntake 排查共享等待，重放最后一小时慢轮次|以全部应轮询周期计兑现；fresh 新候选接纳 P95 与最大值；不能只调低 UI 显示耗时|
|P0|纠正 INTC 的 intel/foundry 歧义；保留 L1 行业覆盖但增加轻量实体/行业上下文判断；检查 binding 专题词与实际 query 的 UI 一致性|先消除明确体育/剧院/军事情报误召回，再评估分拆饱和查询；负面新闻、真实 foundry 竞争消息保留|
|P0/P1|正文前 cheap gate：标题/摘要/域名/实体/事件特征；精确同文章先复用；明确模板低增量降级，不让低价值文章先耗完浏览器|离线80样本及业务高价值样本测误杀；高价值召回覆盖为约束，不能一刀切财经站/行业泛词|
|P1|先修 generic browser ERR_BLOCKED_BY_CLIENT 与有效页面 empty_extract；按剩余价值和过往阶段收益选择 fallback；定位历史 attributeerror 是否仍可重现|有效业务文章真实 FULL/SHORT_FULL >70%；记录总预算和失败阶段，不盲目重复浏览器→Reader|
|P1|强化完整性/清洗：paywall preview 和相关推荐不能凑 FULL；优先 Article DOM/JSON-LD/原站轻量 API；成功候选早停|Law360/On3/247Sports 错FULL样本及推荐污染样本回归；短完整正文不能因长度被误伤|
|P1|统一正确 language，并核对 verified_hint、scope 与跨来源正文复用；限制无变化 recheck 的成本|同文章完整正文可复用，重要实质更新保留；保持 ARTICLE_ONCE 更新原消息及修订语义|
|P1|明确 Google 晚收录/30分钟接纳的兜底策略，并给饱和查询增加覆盖探测|不通过简单放宽所有历史文章窗口造成海量旧闻重放；区分首次高价值晚到与已处理旧闻|
|P2|再按站点数量×业务价值×可修复成功率覆盖 Investing 国家域、Marketscreener、TechPowerUp 和其他长尾配方|优先 HTTP；需要身份的复用现有已验证身份，不为435个域建常驻页/独立Chrome|

过滤建议采用两段轻量逻辑：正文前只拒绝高置信的无关词义、已完整处理的重复文章和明确低增量模板；不确定者保留或低优先级。正文后再判断新增事件、证据来源、是否仅观点/旧事实重述，并清洗噪声。若未来引入模型，只对规则难判的少量候选做评估，不把每分钟的所有重复 RSS 条目交给模型。主流媒体的软文/体育可以低价值，小站的地方项目许可也可能是重要首发，应分别打站点优先级与文章价值标签。

后续验收应至少分开：①全部有效目标周期兑现；②实际请求/列表刷新间隔；③dispatch→capture durable；④capture→新候选接纳；⑤新文章真实 FULL/SHORT_FULL（包含失败与待处理，不混入缓存/重检）；⑥抽检错误 FULL 和过滤误杀；⑦Google/跨查询/跨发布者的事件覆盖。以同一批高价值历史 badcase 作保留集，先修源级时效，再增加正文负载，禁止用过滤掉难站来抬成功率。

## 10. 证据、脚本与未决边界

- `eval/google_news_audit_20261027/preflight.json`：生产源/绑定/索引/隐藏过滤配置快照。
- `summary.json`：日量、完整标签、各窗口 cadence、阶段分布；`timing-detail.json`：新文章任务链路及访问阶段。
- `site-composition.csv`：435 域名全量统计；`quality-sample.csv/json`：80 篇人工筛选与错误 FULL 标记；`sample-bodies.json`：固定 raw_id 的原始正文证据。
- `audit.json.gz`：压缩只读抽取证据（不含完整数据库）；`extract.py`、`analyze.py`、`timing.py`、`sample.py`、`extra.py`、`export.py`、`build-report.py`：可复核脚本。`extra.py` 保存人工标签序列，`export.py` 保存明确的正文问题编号，避免质量分类被当成自动模型输出。
- 实现定位：`news_adapters.py` GoogleNewsSearchRssAdapter；`search_plan.py`；`news_policy.py`；`admission.py`；`polling.py` CaptureIntake；`cadence.py`；`body_results.py`；`content_enrichment/service.py`；`content_enrichment/pipeline.py`；`google_news.py`。
- 未闭合：Google 首次收录时间、全量高价值漏召回率；跨源 body reuse 实际未命中比例；历史 attributeerror 具体栈；最后一小时共享阻塞的具体内部等待点；逐站原文对照后的真实完整率。以上均未用推测补成已验证结论。

本轮未更改业务代码与生产配置，因此不运行无关功能测试、不追加代码变更 changelog。报告及数据脚本只在本地生成；生产端仅写入本轮临时审计脚本/输出，业务数据库连接全部 mode=ro；抽取完成后已清理本轮远端临时文件，本地证据保留并生成 SHA-256 manifest。
