# 消息管线业务增量、I/O 减负与时效优化方案

日期：2026-10-27（北京时间）。本轮为生产只读排查与开发方案，未修改生产配置、身份、数据库或容器，未实施下述修复。

## 1. 决策结论

**下一轮首先减少系统对“同一份旧输入”的反复处理、序列化、写入及投影，再解决发布关键路径和来源访问约束。继续单纯提高并发，会把应用排队转换成磁盘、内存及 CPU 争用。**

本轮确认的共同问题不是某一个下载器慢，而是：

1. 物理采集与业务增量没有充分分离。未变化的长列表仍反复生成完整 capture payload、接纳工作、分发快照和技术状态；共享分发路径直接禁用了输入复用。NBIS 一个来源有 6.62 万条剩余输入，119 个带指纹的待处理 capture 仅对应两种输入。
2. 高频技术状态占用大量写通道和 Read 投影。近期 10,000 条 CDC 事件约 75.6% 的载荷字符来自 `poll_states`。采集提交在慢操作样本中执行约 39ms，却先等待应用写队列约 5.4s。网络前后的多次队列往返足以吞掉 10s 目标。
3. 真实新消息仍被日常工作阻塞。247 条可进行因果关联的已发布记录中，正文实际执行均值 5.46s，正文结束至可消费发布均值却是 46.22s。几个分钟级 badcase 是 regex 已确定相关的消息，不能全部归因于必要的 L2 模型判定。补充对账还确认L2续作重放已完成交付时会使用旧token报错，当前两个槽存在这种无法正常收尾的任务。
4. 周期统计和真实执行同时有问题。旧 `due_at` 造成统计归属失真，但实际请求与列表刷新也确实延期。不能把修正归属当作性能提升。
5. Yahoo 必需浏览器补充频道有持续准入失败；Reuters 有可直接计算的源/Site 串行容量不足。两者不能用统一“网站风控”解释，也不能用 RSS 成功掩盖。
6. 主机是明显的 I/O、内存和页缓存压力，叠加 CPU 调度竞争；不是“8 核一直在执行计算”。正文台账的 FULL 标签率也不是经核验的真实完整率，现阶段没有达标证明。

建议首先修复 **C中的续作幂等恢复缺陷**，随后按 **A 输入增量与去重复工作 → B 薄状态和写通道 → C其余新消息发布 → D 来源准入与驻留资源 → E 正文正确性及有效获取路径** 推进；默认页面重统计减负与 A/B 并行处理。D 中 Yahoo 恢复不等待所有存储改造完成，但不得先通过全局扩大浏览器页数抢资源。

目标保持：各必需采集单元实际请求与新鲜列表间隔基本兑现配置周期，接近 100% 的有效采集覆盖；新文章首次列表采集至下游可消费尽量 ≤10s；真实完整正文成功率 >80%。这些是共同约束，不能通过减少必需频道、放松 FULL、排除难站或丢掉失败样本实现。

## 2. 数据范围、运行版本与证据边界

### 2.1 固定窗口

主窗口为 **2026-10-26 13:30–18:00 UTC**，即纽约 10 月 26 日 09:30–14:00、北京时间 10 月 26 日 21:30–27 日 02:00。北京时间已是 27 日，业务交易日仍为 26 日。

- 主元数据采集约 18:08–18:10 UTC；补充存储、进程、决策关联读取稍后完成。不是单一原子快照，终端状态可能包含窗口结束后的收尾；未完成和窗口边界单列。
- 分开比较开盘 13:30–14:00、16:00–17:00、最近版本 17:15–18:00。17:13 UTC 能源源发布发生在后一个窗口之前，**明显退化在该发布之前已存在**。
- Bus/BODY 运行代码 `12bf384c`，Site Access `e56b8697`；API `2c3729e4`、Projector `5290f5cb`、Web `46a9d7dd`。相关容器健康不等于时效达标。Chrome Supervisor 保留既有实例和身份。
- 快照登记 99 个来源、80 个 enabled 来源、285 个绑定、252 个 enabled 且未删除绑定。主窗口有应执行周期的来源 71 个；按各自生效时间计算，不能给新增源补算发布前的周期，也不能给暂停段伪造故障。
- 当前 27 个绑定配置 30s，主要是 Yahoo、企业 IR、SEC 各 9 个；224 个配置 60s，另一个 TikHub 600s。Barron’s、InvestorsHub、Investing 目前是 60s，30s 是后续承载验证目标。

### 2.2 证据目录和复算

证据位于 `eval/pipeline-global-audit-20261027/`：

|文件|用途及限制|
|---|---|
|`bus-regular-session.json.gz`|配置、SCHEDULE、正文台账、任务、发布审计；其中初次 `runs/captures` 大查询超时，**这两个空数组不作为数据**|
|`poll-runs.jsonl.gz`、`poll-runs-resumed.jsonl.gz`|前段 9,000 行与恢复后的完整流，按 run_id 合并；恢复流有完成 footer|
|`poll-captures.jsonl.gz`|完整物理采集回执元数据，含实际 request/listing/commit 时间；未下载整库|
|`site-regular-session.json.gz`|Site Access 结果、阶段、身份/目标状态；不含全部底层实时 permit 状态|
|`analysis.json`、`unit-coverage.csv`、`source-coverage.csv`|全部来源和必需单元的现有 due 口径对账，不只列成功轮次|
|`physical-intervals.json`|独立按实际请求/响应时点重分配周期、实际间隔；**与旧 due 口径并列，不能覆盖旧缺口**|
|`article-stage-analysis.json`|正文与可消费发布的同 capture 因果关联，排除了后续正文修订造成的时间穿越|
|`io-business-verified.json.gz`|CDC 载荷、待处理输入指纹、状态大小、正文队列类型；早期同名前缀失败样本不用|
|`resources-before/after.json`、`host-pressure*.json`、`process-io-*.json`|CPU、PSI、内存、swap、同 PID/starttime 进程 I/O 差分|
|`log-summary.json`|13:30–18:00 的慢操作与错误分类；**仅被日志阈值捕获的样本，不代表全部调用均值**|
|`deep-links.json.gz`|三个发布 badcase 的决策记录、领取查询执行计划、Google 最终发布者；article_id 批量 delivery 查询 12s 中断，不能用空结果推论没有 delivery|
|`continuation-links.json.gz`、`continuation-links-followup.json.gz`|19:16和19:17:50 UTC按当前续作/delivery主键点查，同样两条续作重新获得更晚lease但业务状态未前进；证明已完成/未完成混合与payload旧token重放问题。晚于主窗口，不混进窗口比率|
|`cache-age.json`|带 Age 头响应的源端缓存年龄，按物理请求去重；只代表有此头的子样本|

复算：使用项目 Python 依次执行 `analyze.py`、`enrich_analysis.py`、`physical_intervals.py`。采集脚本使用只读连接和执行预算；不实例化会迁移数据库的业务 Repository。一次性诊断查询也有 I/O 成本，因此后续验收以小字段、索引范围、分段读取为准，避免复制造成新的压力。

另参考同期只读报告：`docs/message-bus-page-performance-audit-20261027.md`、`docs/google-news-search-rss-current-state-audit-20261027.md`。其样本窗口和分母不同，下文明确作为补充证据，不混算本轮比率。

## 3. 真实运行结果

### 3.1 周期缺口：存在真实延期，也存在陈旧计划时点

|窗口（UTC）|全部应执行周期|按原 due 归属的新鲜周期|全部周期覆盖|窗口末尾 pending|
|---|---:|---:|---:|---:|
|13:30–18:00|95,896|59,220|61.75%|22|
|13:30–14:00|10,413|9,409|90.36%|7|
|16:00–17:00|20,826|10,079|48.40%|9|
|17:15–18:00|17,734|12,023|67.80%|22|

主窗口剔除未结算尾部后的旧指标为 61.77%；本方案主要使用包含 pending 的全部周期口径。既不能只看 CAPTURED 成功数，也不能把阶段内所有失败都换算成漏采。

按**真实 request_started_at 与 listing_received_at 所在的当前有效时隙**重分配，要求有物理请求关联、版本匹配、同周期内请求并取得列表，主窗口为 **72,346/95,896 =75.44%**，最近窗口为 **14,193/17,734 =80.03%**。这是辅助诊断：既有实现把一些后来完成的新请求继续归到很早的 due，从而漏记了当前真实采集；实际边界跨越也会造成两口径差异。

**61.75%→75.44% 的差额不是性能收益。** 修复前后必须同时给出旧归属、真实时间归属、连续间隔、开放缺口及原始请求数。两种口径仍都远低于目标；其中的缓存年龄还使“收到新的 HTTP 响应”不等于已证明源端列表及时更新。

|来源|旧 due 覆盖|实际时间归属覆盖|真实列表间隔均值 / P95 / 最大|配置周期|
|---|---:|---:|---:|---:|
|Reuters|0.40%|58.95%|98.63 / 203.57 / 911.88s|60s|
|Yahoo（两个必需频道合计）|42.5%|53.86%|54.53 / 91.39 / 8,980.47s|30s|
|InvestorsHub|64.2%|76.38%|75.54 / 182.85 / 740.11s|60s|
|Investing|71.4%|81.65%|71.12 / 135.13 / 719.03s|60s|
|Barron’s（两个频道）|76.5%|86.34%|67.83 / 114.69 / 441.64s|60s|
|Google Search RSS|77.8%|87.78%|66.67 / 113.84 / 418.33s|60s|

间隔含有效活动窗口的首尾开放边界，因此不能把首尾截断值当成完整相邻请求间隔；不存在请求时保留整段开放缺口。Reuters 主窗口 25 个有过暴露的 QUERY 单元，最新窗口为 24 个，不固定写成某个历史查询数。

轻量来源同样退化：SEC 旧 due 覆盖约58.5%、企业 IR75.6%、Google77.8%、Benzinga77.8%。这支持共享调度/存储压力的重要性，不能把普遍延期都归因于 Chrome。

### 3.2 接纳积压和重复旧工作

补充快照有 **5,558 个 PENDING/RUNNING capture、149,475 条剩余输入**。这是输入条目，含重复和旧文，**不是 14.95 万篇独立新文章**。

|来源|待处理 capture|剩余条目|不同已记录输入指纹|说明|
|---|---:|---:|---:|---|
|NBIS official actor RSS|120|66,210|2（119 个有指纹）|旧 capture 从06:00开始；抽取两个 payload 分别930、921条，包含大量历史条目|
|Google RSS|199|11,692|94|仍有真变化，不能用整批丢弃代替增量|
|Finnhub|259|9,887|13|重复输入远多于独立输入|
|Barron’s|483|9,313|25|两个必需频道分别保留采集证据|
|GlobeNewswire|566|5,660|25|搜索单元与查询版本要保留|
|Observability RSS|61|3,916|2|少量稳定输入反复进入工作队列|

`fresh_remaining` 标志总数 495，其中491集中于 NBIS，但不是491篇已核实的新文章。当前 `candidate_order()` 已经对明确过期的 publication 做前置降级，不能声称完全没有日期过滤；仍需进一步区分未知日期、未建立 observation 的拒绝输入、首次未完成输入与真实内容变化。无论细分如何，**同样两份长列表没有必要重复处理119遍**。

正文 queued 任务约926个，**全部是 recheck**，不能拿它当926篇新文章正在等下载。正文重检暂停与列表输入复用失效之间的相互作用见4.1。

### 3.3 新消息的关键路径，正文之后也很慢

主窗口有首次列表时点的280条 CONSUMABLE发布审计：列表→Bus提交后可读取发布，均值 **78.60s**、P95 **228.69s**、最大 **2,473.98s**；57/280≤10s。这里只是已发布样本，失败、未接纳、未发布文章还没有进入这个分布。

其中247条能关联到同一次 capture、正文任务及对应执行，排除30条后续正文修订和3条缺失执行关联：

|互不重叠阶段|均值|P95|最大|
|---|---:|---:|---:|
|列表响应→capture durable commit|6.43s|22.69s|97.81s|
|commit→候选准备|11.31s|5.26s|2,452.03s|
|候选准备→正文 job 创建|6.05s|21.19s|65.70s|
|job 创建→本次正文执行开始|8.71s|69.78s|209.96s|
|本次正文执行|5.46s|25.46s|64.75s|
|正文执行结束→CONSUMABLE|46.22s|212.75s|623.08s|
|合计|84.18s|235.49s|2,473.98s|

均值可相加，P95不能相加；极端样本使候选等待均值高于P95。最后一段包含正文结果持久化、READY领取、相关性判定及发布提交，**目前缺少足够细的 durable时点将46.22s全部分给某一环节**。

三个实证 badcase：

- Tom’s Hardware：正文1.66s，结束后623.08s才发布。`article-f9ae74a8d45f0985a3b6ce75cb5d6dbf` 的BE/INTC决策均 `regex_result=1`，分别16:18:55/16:19:29才记录相关，不能解释成必须等待L2才能决定相关。
- Huggingnews：正文3.69s，结束后约511s才发布。`article-a0f48d8cd9aee5df9bc5ca2fca6f6255` 的MU/INTC也是regex确定相关，16:11才有对应决策；其他负向L2决策到16:29仍在完成。
- Wccftech：正文1.04s，MU/INTC之后仍分别等待约417/497s；同样是regex相关。

关联的175个新正文文章key中，首次匹配ticker发布均值72.31s、P95 228.69s；45个≤10s。最后一个匹配ticker均值81.33s、P95 268.78s，42个≤10s。这个集合不能代表所有新文章；真正验收需要补齐未发布文章及分发相关性状态。

CONSUMABLE审计证明Bus Stream已经提交可读，**不证明下游消费者已领取，也不证明Web投影已经可见**。后续分别记录，不把前端投影等同于业务发布。

### 3.4 正文：标签率有窗口差异，真实完整率尚未达标

- 主窗口已进入正文台账、eligible且非legacy的261个新文章key：216 FULL、5 SHORT_FULL、40 FAILED，**标签率221/261=84.67%**。
- 当天语义日06:00–18:00的同口径合并：604个key、471个FULL/SHORT_FULL，**77.98%**。16:00–17:00仅39/53=73.58%；最新19/20=95%样本太少，不能称稳定。
- 这些分母尚不包含卡在capture/intake、未进入正文台账的独立新文章；不能凭84.67%宣布P1完成。
- 同期Google专项报告对80条已存正文抽检，46条FULL中确认4条为付费预览/模板凑长，另13条明显含推荐等污染。该抽样不能外推成本轮全量真实失败率，但足以证明FULL标签不是质量认证。

主窗口非完整的重点分布：

|采集来源|标签完整 / 台账新文章|主要原因|
|---|---:|---|
|Google RSS|56/76|20失败，涉及多个最终发布者，见下表|
|Huggingnews|18/22|3 empty_extract、1 deadline|
|AviationWeek RSS|1/6|5 subscription_required|
|TheFly|1/4|3 subscription_required|
|Investing|8/11|2 no_authenticated_profile、1 login，含 `/news/pro/`|
|Reuters|4/5|1 readtimeout，小样本|
|Barron’s|1/2|失败实际落地MarketWatch，缺认证能力|
|Army RSS|4/5|1 incomplete_extract；不能直接放松为FULL|

通过持久化 wrapper→final URL 缓存，已把Google这20个失败对账到真实发布者：On3四个incomplete；Moomoo两个empty；Seeking Alpha三个不同失败；FuelCellsWorks两个超时；其余为BusinessWire、Morningstar、JoplinGlobe、Barchart、MarketScreener、Intel官网、AOL、PCGuide及Twaslnews各一个。**Google是发现渠道，不是这些BODY失败站点的共同根因。**

## 4. 根因与改动价值

### 4.1 列表复用发生得太晚，且与正文重检相互牵制

代码：`message_bus_v2/polling.py` 的 `capture_input_key/prepare/candidate_order/reuse_waiting_inputs`，`repository.py` observation/reuse相关函数，`body_results.py`。

已确认：

1. `prepare()`先构建完整source/binding/result/admission/checkpoint，扫描全部输入，`content.encode()`完成压缩、对象写入后才计算输入key。即使随后业务复用，前面的完整编码/文件I/O已发生。
2. key直接包含完整messages及metadata。trace、观察时间、非业务字段变化会破坏同一语义输入的复用。
3. `capture_input_key()`遇到distribution直接返回空串；共享源每次仍进入分发旧列表加工。
4. 现有 `reuse_waiting_inputs()`主要在leader完成后帮助排空，不能阻止同一份尚未处理完的输入不断生成同等业务工作。
5. 完整列表复用受相关BODY `next_check_at`最小值约束。压力下recheck长期暂停，过期的最小值不前进，可能反过来使列表复用持续失效。**列表发现新输入与BODY恢复/重要更新重检必须解耦**。
6. observation已有已接纳输入的语义，但不能把“未出现过已发布消息”一概视为值得重新加工。已确定过期/不合范围的输入需要有轻量、可失效的判定记忆；日期/terms/admission变化时仍重新判定。

目标复杂度：物理采集每次留一份薄证据；业务处理接近 `O(新输入+实质变化+相关配置变化)`，不再是`轮次×列表条目×ticker数`。不以只保留最新列表代替增量，因为有限列表里先出现后滑出的文章会因此丢失。

### 4.2 技术状态、CDC与内容包装重复写入

代码：`repository.save_poll_unit_state()`、`polling.py`、`distribution_repository.py`、`v2_read/outbox.py`、`v2_read/native_content.py`、`content_files.py`。

- 一个unit更新会读取整个binding的PollState、复制全部units和checkpoint，HEAD还在root重复保存checkpoint，再重写大JSON。快照中最大poll_state约71K字符，不是固定几百字节。
- `poll_states` CDC触发器仅排除少数时间字段；大JSON被复制到source receipt，再进入Read MVCC。列表/游标/请求数组无需在每个UI版本中重复保存。
- 近期约15分钟的10,000条CDC事件中，总载荷43,501,385字符，`poll_states UPDATE`为32,883,011字符（75.59%）；run/capture/IR技术表合计占9,127个事件。**SQLite `length(TEXT)`是字符数，不能伪称物理磁盘写入字节，也不能据此承诺磁盘I/O降低75%。**
- `distribution_articles.last_seen_at`、`delivery_keys.seen_count/last_seen_at`在重复输入上仍更新；不断展开快照/roster、写观察集合。它们大量是可从采集回执推导的技术触碰，不是新业务事实。
- `NativeContent.encode()`可能先将整个大message包装外置，body与变化的trace一起重新压缩、写入对象。现有`keep`能力可使已知message容器保留结构、正文字符串单独按内容寻址，避免引入另一套对象存储。
- 部分on_page已持久化响应之后，最终空result又生成空terminal capture；需要确认没有新物理响应后合并，不能把真正空列表/304的采集证明删掉。

### 4.3 应用写队列是共同等待，不等于SQL锁耗时

生产慢操作样本：

|操作|样本数|入应用写通道前等待均值/P95|执行均值|
|---|---:|---:|---:|
|PollJournal.commit|9,663|5.37 /14.81s|39ms|
|PollJournal.start|7,084|4.11 /10.81s|29ms|
|poll finish|6,971|4.86 /12.33s|46ms|
|accept_and_claim|2,698|4.76 /10.50s|319ms|
|distribution poll状态记录|2,419|7.86 /15.70s|34ms|
|claim_decisions|121|6.93 /13.19s|27ms|

`StorageLane`已区分read/preparation/write，已有短写优先与公平机制，不能再把“实现独立读队列”作为全新修复。但大量技术写仍占同一个进程写入口，多段串行await累加，长接纳事务也占槽；跨进程SQLite竞争另有存在。

另外，`_update_capacity_alerts`的39个慢样本发生2,301次commit，说明一次容量评估不是一个小写；BODY `prune_site_strategy_outbox`的32个慢样本平均执行1.64s，事务1.62s，最慢2.77s。保留七天outcome并不要求每次成功上报都立即执行清理。

方向是减少写次数、缩小载荷和占槽区间，保留单写一致性；**不把SQLite改成多个无协调写者，不通过关闭fsync/WAL或弱化业务事务“提速”**。

### 4.4 发布分发仍被泵进度、技术写与续作错误拖慢

`DistributionWorker.run_available()`已有rolling任务；regex确定相关也已经先于L2发布。这两项不需要重新发明。

仍需处理：

- 持续泵依赖orchestrator推进，且领取L2、过期清理、schedule记录等存在await；要量出并解除“准备/清理没做完就不补新READY槽”的依赖。
- 正向判定后，`record_decision→读取当前binding/ticker→accept_message→finish_delivery`多次跨读/写通道。可将不变版本读取与一次小的有fence发布提交合并，避免每一步都排同一队列。
- L1正向文章仍进入JEV实验对照，产生完整continuation payload和模型/写入；这部分不影响已确定相关性的业务覆盖，应从默认必经路径移除。必要的L2仍完整保留，不能以放弃负向初筛文章的二次判断减少覆盖。
- 必需L2中，某ticker已明确达到相关阈值后，还要等其他分段/ticker处理完成才走发布。应即时兑现已成立的正向结论，其他判定独立收尾。
- 主窗口日志有49次 `distribution continuation failed: RuntimeError`，日志只留异常类型。19:16 UTC进一步按主键对账，**确认了一个具体的续作幂等恢复缺陷**：续作`4baa9cd7…`为RUNNING，其COHR/NBIS交付均已NOT_RELEVANT、payload旧token已失效；另一个RUNNING续作`2f86dde2…`的BE已NOT_RELEVANT且token失效，后面的INTC仍L2_WAIT。两个槽上限恰为2。约108.5秒后复核，这两条续作lease从19:16:45变成19:18:50、业务状态及旧token问题完全未变，其后三个PENDING续作也未前进，排除了“只是在正常结束前瞬间采到”的解释。
- `_continue_decisions()`重放原payload；`_finish_article_decisions()`不先跳过已完成delivery，重新判定后仍以旧token调用`finish_delivery()`，其UPDATE影响0行即抛出`distribution_delivery_lease_lost`；随后`finish_decisions()`执行不到。lease到期又按原顺序重放，形成重复判定/模型调用、已完成首条报错、后续未完成条目不推进的闭环。**该代码路径与当前状态已对应；不能据此声称49次历史错误全部都由这一处引起。** 修复必须是幂等续作和有效所有权移交，不是延长lease或手工删全部续作。
- 生产EXPLAIN表明领取查询使用现有state索引，但内部GROUP BY和ORDER BY需临时B树；不是“整个领取必然全表扫描”。优先对就绪集合做小ID领取，再取正文。只有证明需要时增加一条匹配就绪集合的部分索引。

### 4.5 主机I/O与内存压力会放大所有路径

227.32s的CPU差分：实际user/system/softirq约47.02%，**iowait45.24%**、idle7.74%；不能把load20解释为20个计算核心需求。同期内存可用约1.3–1.5GiB，2GiB swap几乎用尽，I/O PSI some/full有显著等待。

61.13s同PID/starttime进程差分中，既有Chrome单进程约1.14GiB读，也有多个Codex进程约0.42–0.64GiB读；系统守护进程也发生明显读入/major fault。**不是全部磁盘压力都来自消息SQLite写入**，还存在驻留浏览器、工作进程、换页/页缓存抖动与默认页面重查询的共同影响。

Site快照约3.43GiB、Supervisor约3.75GiB、Codex Worker约3.5GiB。身份登记514条不等于514个Chrome：实际进程和运行实例必须另核对；数百条旧BUSINESS/CLOSE_PENDING/ALLOCATING账本也不能直接当作几百张活页关闭。

当前Site环境是 **全局browser8、BODY2、borrow=false**，已不是旧4核的4页设置。缺省browser idle为43,200s。需要降低无工作公共上下文驻留、反复全catalog读取及对象重复处理，保留现有高风控身份、人工CHALLENGE/LOGIN目标和磁盘profile。

同期页面报告另证实：一次INTC默认页面可扇出约39个请求；每来源只取当天新增，却调用完整metrics并额外计算BODY cohort；旧poll_window和重复cadence重统计进入默认请求。**这些读操作可以直接做业务减法，不需要另建指标平台**。

### 4.6 来源差异及真实约束

**Yahoo**：主窗口3,659次CRAWLER结果，1,702成功、1,957 BUDGET_DEFERRED（1,423 site_queue_timeout、534 access_budget_exhausted）。15:31之后必需SUPPLEMENT几乎无成功；多个unit due仍停在15:29–15:31。失败记录在身份选择/尝试前退出，queue约3s、network0。RSS可用不代表该频道正常。

已有RSS/browser独立源limiter、同identity framed-command并发、原子Site/Identity/global准入；不能重复提出“新增这些能力”。当前诊断缺少global_active、site_next、identity_next、具体阻塞gate和permit持有人，不能确认是某一种泄漏或只因3s超时。应首先补少量实时计数和一条失败链路；核对probation节奏、profile恢复、取消释放、动态容量与实占槽，再针对已证实gate修复。

**Reuters**：Site CRAWLER 3,886/3,895成功，平均Site执行2.92s；不是全站身份失效。当前source `max_concurrency=1`，Site也为1、起始间隔3s；最新24个QUERY要求60s，单按节奏下界就是72s。再加整个源slot覆盖Site RPC及前后写入，真实平均刷新接近99s。

应让source并发限制覆盖实际请求而非持久化/接纳；在已验证identity命令并发内测试2个独立查询，审查3s规则是否有真实依据。若3s是当前身份可靠性的真实约束，单身份24查询/60s物理不可兑现，需合法等召回查询合并或既有已验证能力分担；不能隐瞒缺口、删查询或依赖重复追赶。恢复/禁用身份的真实窗口独立保留，不重置session来追指标。

**Barron’s/Investing/IHub**：已有轻量/同源获取，实际请求→响应远快于整轮，但IHub单次仍显著偏慢；承载见第7节。Barron’s还存在60s左右CDN缓存，不能仅增加本地轮询宣称30s新鲜度。

**RSS/JSON/SDK/IR**：多数轻网络任务也被前后写等待拖慢。共享物理请求只能按冻结roster、source/binding版本和真实request_id扇出证明；IBKR推送接收与历史请求不能伪造为每ticker新HTTP请求；企业IR每个必需endpoint仍独立兑现，不用成功RSS遮盖失败页面。

## 5. 业务导向的I/O减法清单

以下不是“删除审计”。保留能决定接纳、去重、更新、恢复、身份所有权和真实采集证明的数据；删除可推导的重复包装、未变触碰与默认界面不需要的精确重算。

|对象/调用|直接移除或简化|必须保留|验证方式|
|---|---|---|---|
|每轮完整capture包装|先算语义input摘要；相同输入复用一个immutable payload引用，不再次写source/binding/messages全文|每次真实请求与响应证据、版本、checkpoint推进、最早观察与独立页面变化|100次相同采集有100份薄证据，业务输入对象及接纳工作近1份|
|PENDING重复列表|同绑定/unit/语义input/策略代次只保留一个待处理leader，其余capture关联leader|先前列表独有但后来滑出的文章；leader原子完成及恢复|A→B→A且每批含独有文章，重启后无漏采|
|共享源重复分发|启用输入/文章版本级复用；不为同roster同版本重展开每个ticker|新增ticker、terms变更、admission窗口变化的补判定|9→20个ticker只新增所需判定，既有消息不重发布|
|拒绝/旧输入反复扫描|记轻量判定指纹+策略/terms/日期代次，避免永久“未知新输入”|重新变得可接纳及实质变更的重判定|日期跨日、terms变更、重要更新、UNKNOWN转EXACT|
|PollState大JSON|unit一行薄状态；共享root只保留兼容摘要；checkpoint按真正变化更新引用|due/下一次可启动、版本、游标、失败恢复等必要状态|一个unit变化不重写其他unit的checkpoint|
|poll技术CDC|投影薄状态/计数/最后时间，不复制完整checkpoint、requests数组及历史大JSON|UI水位一致性、15分钟pin需要的薄版本、原始Owner采集证明|旧新API显示一致；pin分页不串水位；逻辑载荷显著下降|
|run/capture多次状态写|同一小阶段合并短事务；无新物理响应的空terminal capture取消|真正空列表/304、失败/延期/取消、请求启动证据|一个响应不生成两份capture；空新闻仍有有效采集证明|
|job_id数组/观察集合|receipt留计数/范围，按现有poll_run_id索引取任务；新增关系增量写|完整任务可追溯及恢复所有权|百个旧item不重写同一增长数组百次|
|重复last_seen/seen_count|以采集journal推导或按小时间桶合并；无业务变化不写完整记录|业务first_seen、内容版本、重要更新及真实最近采集时间|相同输入反复轮询不触发正文和分发业务写|
|嵌套正文包装|复用现有NativeContent keep结构、正文字符串内容寻址，trace留薄字段|完整正文、表格、附件、修订与对象引用生命周期|正文不变仅trace变化不新增一份全文对象；GC/备份解码通过|
|容量告警|状态/原因变化才upsert，合并小事务；正常tick不重复resolve/commit|真实告警首末时点和原因变化|稳定一小时不按tick产生数千commit|
|BODY outcome清理|移出每次flush；分钟/更低频、有行数上限清理已确认过期记录|待确认outcome与恢复策略反馈|新BODY启动不等待七日历史DELETE|
|JEV实验对照|L1相关后的全量对照不再默认持久化完整续作/调用模型；必要时显式离线抽样|L1未命中时业务必需的L2覆盖与决策证据|原相关消息集合一致，已相关消息不为对照新增负载|
|默认UI重统计|去掉旧poll_window默认计算；逐源metrics改一次桶归并；诊断按需低频|消息列表、基础状态、业务计数、质量及真实周期诊断入口|请求数不随来源数线性增加；隐藏字段对应SQL也不执行|
|Worker每次全catalog读取|按scope/strategy/identity/egress版本缓存不可变snapshot；无版本变更复用|身份修订、egress变更立刻失效，控制命令所有权|514scope时普通命令不再读全scope目录|

不能删：accepted消息/ARTICLE_ONCE去重键、正文实质更新指纹及修订、未完成输入、checkpoint fence、请求与必需unit关联、paid scope、人工浏览器所有权、失败原因。不能用直接删除生产数据库历史或全库VACUUM作为第一步。

## 6. 可直接执行的开发任务

### A. P0：先让业务只处理增量

**位置**：`message_bus_v2/polling.py`、`deduplication.py`、`repository.py`、`distribution_repository.py`、`body_results.py`。

1. 明确两种指纹：列表传输/输入对象hash与文章业务fingerprint。文章hash包含标题/摘要/正文或正文版本提示、发布时间及其置信基础、附件等业务变化；排除poll trace、当前observed_at、耗时、响应头和重复统计字段。不能把一切metadata全部丢掉，字段按adapter契约白名单核对。
2. 在完整包装编码与全列表候选排序之前查找相同语义输入。扩展现有 `poll_capture_inputs`/复用表支持PENDING leader；保持键包含source/binding/unit、配置和admission策略代次。共享源输入独立于roster，roster差异仅产生缺少的ticker判定。
3. capture薄行和payload引用一起durable后才推进checkpoint；leader未完成时followers不冒充已接纳，完成由同一事务确认。失败/重启可重新领取；保留每次物理新鲜证据。先写引用再推进游标，避免崩溃漏文。
4. 对长列表按文章指纹批量读取已知状态，只将新增、实质变化、判定已失效的条目加入工作。复用现有observation结构保存必要的轻量判定；若其canonical消息约束不允许无消息判定，增加一个窄的输入判定表即可，不建通用缓存平台。
5. 拒绝判定区分永久内容不符、当日admission、terms/classifier版本与UNKNOWN时间；按对应代次失效。现有明确过期过滤继续保留；不会因“拒绝过一次”永久忽略重新满足条件的新闻。
6. BODY重检由既有独立job调度，列表复用不再被某个过期next_check_at全局否决。未完成FULL恢复、已验证FULL轻量更新检查、实质内容更新分开；有更新证据时立即生成content_update，保持ARTICLE_ONCE更新原消息/修订/更新状态。
7. 接纳shard按有业务增量的leader公平分配；同source慢历史工作不挡其他source新输入。不要增加目前16个intake shard作为第一修复。历史工作低优但有进度，重要更新不得无限饿死。

**交付**：重复poll只增加薄采集事实，不增加等量接纳/正文/分发工作；NBIS两种重复输入归并为两份可恢复业务工作。现有积压先按input hash/文章版本做无损合并，不直接清空队列。

### B. P0：缩减状态写与跨队列往返

**位置**：`repository.save_poll_unit_state`、`polling`、`scheduler`、`storage_lane`、`v2_read/outbox/native_content`。

1. 增加简单的 `poll_unit_states(binding_id, unit, source_version, binding_version, due_at, next_dispatch_at, last_request_at, last_capture_at, status, error, checkpoint_ref, ...)`；按unit更新。索引以当前due和主键读取为准，不复制所有历史维度。
2. 兼容读取可组装旧PollState，但热调度改读薄unit状态；旧大JSON一次迁移、切换后停止热路径双写。IR endpoint状态也走相同原则，保留其独立失败隔离。
3. 技术CDC输出显式薄投影，保留固定水位与pin窗口；Read不再存完整checkpoint/全请求数组/大unit map。详细诊断从Owner journal按run/capture_id点查。业务Raw/Standard/Stream和正文修订仍保留完整内容引用。
4. 采集前准备、run启动、状态完成等合并相邻的小事务，按一次调度帧批量写薄记录。新鲜capture/新候选/最终发布优先，低价值touch/告警不插队。采集请求不得等待一串与该请求无关的状态保存。
5. 不在source/site/identity/page permit内做内容压缩、解码、投影或接纳。permit在真实网络或浏览器访问完成后释放；后续可靠落盘仍完成，且同unit不能形成无限多未提交结果。用已有inflight/单leader限制，必要的存储故障明确报延期，不默默覆盖。
6. `_update_capacity_alerts`按变化写，合并一次评估事务；`prune_site_strategy_outbox`改低频限量，先验证当前执行计划再决定是否补CONFIRMED/confirmed_at索引。无待任务先只读探测，避免高频空领取写事务。
7. 用现有NativeContent `keep`精简正文包装；明确对象引用计数/GC/备份关系。先不做嵌套外置引用图，不全局关闭fsync。

**迁移策略**：先落向后兼容读取和新薄投影，再短事务切换写路径；不重编码整库正文，不全库一次性扫描迁移。可按活跃binding逐批填充旧状态，异常时保留旧checkpoint。每批验证后推进，兼容期结束停止旧热写。

### C. P0：BODY一结束就推进新消息，消除发布前分钟级等待

**位置**：`distribution.py`、`distribution_repository.py`、`persistent_runtime_v2/bus_orchestration.py`、`content_enrichment/service.py`。

1. 在既有READY状态中增加最小持久时点 `body_ready_at/decision_ready_at/consumable_at`，配合现有capture/候选/job时间，分离BODY完成到READY提交、READY等领取、regex/L2、最终发布。只记录状态转移，避免每步新增完整audit副本。
2. 分发泵独立推进就绪任务，不先await容量告警/历史过期/所有schedule保存；利用现有wake和短周期轮询，BODY跨进程写READY后的唤醒不能完全依赖进程内Event。无需新增消息中间件或守护服务。
3. 先小ID领取，再按claim取article内容；新READY按最早未交付相关消息公平推进。保留现有rolling实现，移走阻止补槽的await，不用成批gather重新制造慢伙伴等待。
4. 正向decision与发布原子性尽量放在一次短业务事务；currentbinding/ticker/source版本用小快照读取，提交时fence配置及ARTICLE_ONCE。网络/模型/内容编码都不在事务内。terminal delivery必须可恢复，避免“已发布但状态未收尾”导致重算。
5. 读取并复用已有 `(article_id, content_version, ticker, terms_revision, classifier_version)` 决策；不要有唯一索引却仍重复调用模型。content/terms真正变化才重判，模型临时失败不缓存成永久NOT_RELEVANT。
6. 默认移除L1确定相关后的全量实验JEV比较；L2只计算业务尚未决的ticker。某个ticker已有确定正向结果即发布，其他分段/负向/实验工作不阻塞。
7. **首批P0处理当前毒续作**：领取/恢复时按delivery主键读取当前状态与已持久化decision，剔除已终端交付；全部已完成则直接幂等结束continuation，部分完成则只恢复剩余条目。不要继续把原payload中的旧token当作永久有效所有权。将新结果与delivery收尾原子提交或持久化逐项完成进度，恢复时复用已记录decision、不重跑整个模型批次；旧worker落地仍受continuation/delivery代次fence。长await按现有lease机制续租。日志补reason/stack，定向追溯其余RuntimeError；不能仅将90s lease调长或删除L2_WAIT。
8. BODY outcome flush使用既有durable outbox独立异步收尾，不能在run补槽前await远端上报和历史DELETE；确认当前日志无flush失败，不将此项冒称已证实所有延迟根因。

**关键回归**：已知三个regex正向badcase即使L2挂起60s、实验判定失败、其他ticker慢，均应先完成相应发布；崩溃/重领后只更新同一消息，不二次触发已消费ARTICLE_ONCE。另用“全部delivery已完成”和“首条已完成、次条L2_WAIT”两个真实状态重放，确认不重跑已完成模型、不因旧token卡住后续、两个槽可释放。

### D. P0：恢复真实采集节奏与可兑现浏览器容量

**位置**：`scheduler.py`、`cadence.py`、`poll_trace.py`、`capacity.py`、`site_strategy/{budget,service,worker_manager,runtime,fetch_anchors}.py`、相关源adapter。

1. 修复恢复/失败重试仍沿用陈旧due的问题。活动epoch内一次延迟后，记录未兑现的旧slot，再将下一次有效采集对齐当前网格；同unit最多一个执行/一个合并待启动。**记录欠账，不用几百次追赶把旧due继续拖两小时**。现有 `_next_due()`已有跳到未来能力，重点修核所有deferral、恢复、共享分发及temporary override调用路径，不重写另一套时钟。
2. 暂停/日历/临时开关切换有明确epoch和SCHEDULE；关闭/到期不启动新临时请求，允许durable后续正常完成。保留上下文、checkpoint和下一交易日接续，不能靠重新初始化清除旧due。
3. Yahoo增加只读permit诊断：global/site/identity active与limit、next-ready延迟、waiter优先级/age、实际lease拥有者、profile/worker等待。超时记录首要gate及是否曾发请求；最多保留必要字段，避免大目录快照入每条日志。
4. 用一个失败SUPPLEMENT请求核对从source limiter到JointBudget、profile、worker、page的实际占槽及取消。检查probation_interval在3s准入预算下的交互、队列优先级及cost估计是否导致饥饿。先修已证明释放/节奏/队列问题，再决定预算是否需覆盖一次可预测的等待；不直接把3s全部改成30s。
5. Reuters去除包住持久化/接纳的source级串行；同identity独立页面可以并发，但登录、profile控制和恢复保持互斥。测试单身份2个请求的真实稳定性；有真实访问限制时按其约束做查询与能力方案，不能跨paid/public scope乱复用。
6. 同源warm HTTP/JSON成功时及时结束浏览器操作；只等取得列表所需的条件，取消networkidle、无关DOM和重复导航等不必要等待。已有lightweight路径逐源核实命中率，失败后保留必要fallback，防止空列表被当成功。
7. `worker_manager.snapshot`按catalog/身份/egress版本缓存，移出已占browser permit后的重准备；identity runtime小字段读取避免在全局condition锁内做潜在慢数据库读取。
8. 公共publisher空闲上下文及无工作worker缩短到分钟级回收，先用现有public生命周期/idle配置实现；锚点尽量复用且有总驻留预算。只释放执行资源，保留profile文件、cookies、认证、身份配置、egress和人工目标。注册ledger与实际CDP/workerinstance核对后才修旧孤儿记录。
9. 保持8核基线global8/BODY2为初始硬上限，先测减负后的需求再调；动态容量依据实际endpoint/query/channel的请求率与permit占用，不按ticker固定9。普通HTTP走独立有界连接池，不能消耗浏览器页；CPU/内存压力应降低背景重检和空闲驻留，不先延迟新列表。

### E. P1：提升真实正文成功率，减少无效回退

**位置**：`content_enrichment/{pipeline,service,managed}.py`、`message_bus_v2/{google_news,body_results}.py`、Site各publisher recipe及正文完整性检测。

1. Google先完成最终URL解析，再以真实publisher/site/capability选路径；保留采集来源维度。正常语言显式继承source语言并规范化，保持paid访问scope隔离；量化跨源同URL命中，不能仅调整TTL掩盖实质更新。
2. 当前generic BODY的 `ERR_BLOCKED_BY_CLIENT`优先复现一个有效业务文章（如PCGuide/BusinessWire），检查主文档导航、路由/扩展/请求拦截、重定向和页面类型。它是客户端拦截信号，不能归类成发布者403。保留主文档/正文XHR，减少图片/视频等无关资源需证明不影响正文。
3. 对本轮失败发布者分别落地有效路径：BusinessWire/Morningstar/Intel官网优先正文DOM/结构化数据/现有HTTP能力；FuelCellsWorks记录响应/阶段再处理连接或提取；MarketScreener/MarketWatch/Investing Pro/Reuters按已验证身份及权益访问，不能给public fallback授权假FULL。
4. 强化paywall preview/主文完整性：完整结构、结尾、阻断提示优先于字符阈值；去掉推荐、课程、作者履历等凑长。保留真实短稿SHORT_FULL，Army/ETNews等短正文样本用完整结构证明，不直接统一降低长度门槛。
5. HTTP→browser→Reader继续支持，但按publisher阶段收益选择路径。HTTP已完整立即停；已知客户端拦截未修复的相同组合不每篇重试数轮；订阅/login/身份恢复按明确原因停止无效Reader级联，有恢复能力则保留durable重试。
6. 新文章正文与content_update优先于例行recheck，完整正文缓存优先，但不无限暂停实际失败恢复。预算从实际fetch启动，分别记执行/准入/重试/终端持久化；修复名义180s却墙钟300+s的调度/终端迟滞，不只延长超时。
7. 旧AttributeError按原job定向重现、保存堆栈；同期专项报告当前日为0，不能当仍持续发生的根因。仅恢复确认受影响且仍有业务价值的失败，不全历史重抓。
8. AviationWeek/TheFly等缺权益的完整文章单列能力依赖，失败仍留全局分母；没有合法全文访问能力时，优化程序不能保证这些站点各自>80%。不能用付费预览冒充FULL或直接删除这些来源。

### F. P0配套：默认读路径也做减法

**位置**：`frontend/v2/src/pages/message-bus.tsx`、`api_v2/{bus,bus_metrics,news_timeliness}.py`、`v2_read/repository.py`。细节复用同期页面报告，不另建指标系统。

1. 默认status取消旧poll_window；sources不默认重复计算cadence。当前状态/最近成功/上次耗时/配置周期读取薄投影。
2. 逐来源当天新增一次读取现有bucket归并，取消N次完整metrics；BODY cohort、逐unit实际周期保留按需独立诊断，显示统计窗口及未知/截断。
3. 消息列表、基础状态、来源和诊断分开加载/报错，接入取消signal；一个慢诊断不抹掉已有消息与来源。
4. 分页修正已有current有序索引选择；固定水位/pin/history语义保留。优先不新增大索引，不提高读worker并发与磁盘争用。

## 7. 8核16G、最多20 ticker的30s承载设计

### 7.1 以请求单元计需求

对每站点分别计算：`λ = Σ(该必需endpoint/query/channel每周期请求数 / 周期秒数)`，共享物理请求只计一次；冷启动、恢复、分页/备用频道另外计成本。需要的执行槽约为 `ceil(λ × 实测permit占用 / 0.75)`，还要受真实Identity/CPU/内存约束。0.75为规划余量，不是通过人为等待实现的利用率限制。

不能拿纯HTTP传输耗时替代浏览器执行槽占用，也不能拿包含排队的整轮耗时反推应该无限加槽。先有每段时间及CPU-ms，再调容量。

|来源（20ticker、30s）|物理请求需求|本轮实测成本|容量判断和具体动作|
|---|---|---|---|
|Barron’s|两必需频道若不共享则40/30=1.33req/s|request→response均值0.532s、P95 1.105s；Site备用路径另计|HTTP链路约需1个平均在途，配2–4有界HTTP连接有空间；应先消除写等待，并验证另一频道、CDN及时性及备用路径成本，不默认每条开Chrome|
|Investing|20/30=0.667req/s|传输0.758s，Site执行2.279s，P95 4.923s|2个执行槽平均已接近75%规划边界，尾部会溢出；优先减少重复导航/worker准备/跨层占槽，目标常态占槽≤约1.8s，再验证2槽及身份稳定性|
|InvestorsHub|20/30=0.667req/s|传输3.197s，Site执行6.458s，P95 16.914s|按当前成本约需6槽，现有身份2槽无法兑现；必须先找稳定轻量列表/同源JSON或压缩浏览器步骤。2槽在75%余量下需≤2.25s常态占用；若做不到，明确访问容量缺口，不能冒充30s已可用|
|Reuters|不按ticker数估算，最新24QUERY/60s|Site执行2.918s，配置起始间隔3s/串行1|节奏下界72s；先解除无依据外层锁并证明可用2槽/更合理节奏，真实站点限制不允许时再做等召回查询合并或已验证能力分担|
|Yahoo|20RSS+20必需SUPPLEMENT/30s，分别计|补充频道目前存在准入故障|先恢复SUPPLEMENT，量出两类成本；RSS成功不能代替20个浏览器补充必需请求|
|企业IR/聚合RSS|按实际内部endpoint，而非统一9/20|部分共享、部分独立HTTP/RSS/JSON|一个endpoint一次采集可服务多个ticker，但每个独立页面必须有证据；新增ticker自动增加需求，无固定9阈值|

### 7.2 源端缓存与“新鲜”的界限

主窗口Barron’s有Age头的1,338个去重物理响应，Age均值51.68s、P95/最大59s，1,309个>30s；Tom’s/Huggingnews/部分官方聚合RSS还有数分钟乃至更长Age。**Age是缓存中响应的年龄，不是文章发布时间，也不单凭它证明期间确有新闻被漏掉；但它阻止我们仅凭每30s HTTP200就宣称源端已刷新。**

开发时保留Date/Age/ETag/Last-Modified及必要Cache-Control、304验证来源；区分本地重用、CDN响应、明确重新验证。对Barron’s验证可用的正式列表/API查询及合理revalidation能否取得30s级更新；不默认附随机参数无限绕缓存。若上游端点自身缓存60s，明确“本地30s执行、源端更新约60s”的能力限制，研究等覆盖替代端点，而不是把统计阈值改宽。

30s验收分三步：减少重复工作后先跑当前9ticker；再用冻结真实响应做20ticker同成本回放；最后在保留身份且不重复已订阅请求的前提下，按真实20ticker等价请求率定向验证B/I/I。离线回放只能证明本地容量，真实网站承载和反爬稳定性必须实测，不能宣布已经验证通过。

### 7.3 资源分配原则

- global browser8、BODY2作为初始上限，保留crawler优先和新BODY最低进展；先减驻留和单位执行成本，禁止直接把8提高到20。
- 公共HTTP、内容准备与短写分别有界；内容准备不靠增加线程在I/O紧张时同时写更多对象。缓存以小对象数量和字节数界定，不能将所有列表/正文放进内存换取表面磁盘下降。
- 负载诊断同时看CPU执行、event-loop delay、iowait、I/O PSI、major fault、RSS/working set/swap、每角色读写与permit利用率。主机其他研究/交易/Codex任务不在本方案内改变业务语义，先消除Message Bus自身无效工作；资源冲突再基于运行数据协调。
- 成本预算和容量阈值由当前启用unit与实测成本计算，并限定物理资源上限；ticker从9变20无需重新写固定阈值。

## 8. 开发顺序、依赖、测试与发布

|批次|工作|依赖|主要资源/迁移成本|通过条件|
|---|---|---|---|---|
|1|C续作幂等恢复热修；A输入增量/未完成输入合并；F默认读减负；D的最小gate诊断|复用现有journal与观察键|少量索引/窄状态，避免整库正文改写|毒续作可正常收尾、剩余交付推进；相同长列表不线性增加业务队列；覆盖一致|
|2|B薄unit状态、薄CDC、正文包装复用、空terminal/告警/清理减法|批次1语义键确定|一个unit状态表；兼容投影和pin；按活跃绑定迁移|写次数/载荷/对象数下降，调度和发布应用队列P95进入低延迟区|
|3|C就绪泵/正向快速发布/决策复用/续作所有权；D Yahoo故障修复|最小时点/gate证据；可与2部分并行|现有任务内修改，无新服务|三个regex badcase不再等L2/历史；Yahoo每个SUPPLEMENT持续真实刷新|
|4|D Reuters/B/I/I单位成本、同身份并发及空闲资源；E高收益正文修复|先减少全局I/O；真实publisher失败归因|有界浏览器/HTTP验证，不增身份数量|当前周期兑现改善；20ticker30s能力实证或明确缺口；质量无倒退|
|5|全链路验收、源级回调参数、修复新badcase|前述关键路径完成|主交易窗口自然运行，低频快照|满足下述验收；保留长尾/失败/不确定项，不以容器healthy结束|

必要测试直接围绕业务不变量，不为每个小重构写镜像实现的测试：

1. **输入减法**：100次同样930条列表、两个变化版本、A→B→A、条目滚出列表、未知时间转精确时间、content变化但URL不变、仅trace变化、跨ticker共享。检查完整覆盖、业务工作/对象创建次数和状态写量。
2. **恢复与版本**：capture事务前后崩溃、leader领取后重启、followers先到、lease过期/旧token、绑定disabled/terms/source变更、日期跨日、暂停/临时开关截止及下一交易日接续；ARTICLE_ONCE不重复发布，原消息重要更新及修订保留。
3. **新消息快路径**：大量旧输入+一个新文章、BODY快速完成但L2慢/失败、另一个source卡住、SQLite短暂busy、投影慢；分别验证真实请求继续启动、新候选不被扫描旧文堵住、正向消息尽快可消费。
4. **存储兼容**：薄state旧接口兼容、checkpoint恢复、Read pin/MVCC分页、GC/备份/NativeContent解码、正文表格/附件/修订完整。测逻辑payload和物理fsync/write_bytes，不假设两者同比例下降。
5. **浏览器**：同identity并发两个只读页面、取消/超时permit归还、profile控制互斥、恢复probation、fallback身份选择、空闲回收不动人工目标和持久profile；RSS成功但必需浏览器失败必须验收失败。
6. **正文**：Google final publisher路由、语言规范化与scope隔离复用、PCGuide客户端拦截、Intel/BusinessWire有效主文、付费预览误FULL、Army/ETNews短完整、Reader超时与恢复deferral，保留404/付费/未知结果。
7. **读端**：默认请求图无N次重metrics、模块独立成功、ticker切换取消旧请求、诊断按需、current/history分页同水位。前端只隐藏字段而SQL仍执行判不通过。

实施时先补changelog并说明schema/投影兼容范围；部署前重新整合其他会话main改动，按实际文件影响重建正式Bus/BODY/Site/API/Projector/Web等必要容器，不用临时生产替代容器，不清生产数据/配置/Chrome身份。**本轮仅交付方案，不执行上述发布。**

## 9. 统一验收口径

### 9.1 配置周期兑现

- 以所有应执行周期及所有required unit为分母；缺dispatch、延期、失败、取消、身份不可用、未完成都保留。真实暂停段按明确SCHEDULE排除，配置变更分epoch。
- 同时给出实际请求启动覆盖、有效列表覆盖、实际相邻间隔、P95/P99/最大、`>1.1×周期`及`>2×周期`次数、当前开放缺口。旧due归属和实际时点归属并列，说明统计修复贡献。
- 一个时隙最多一次有效刷新信用，重试/追赶不补造过去fresh；共享请求按冻结roster和物理ID证据关联；RSS、browser、IRendpoint、QUERY逐项验收。
- 计划稳定窗口目标≥99%且逐来源/逐必需单元接近100%，不是靠总量平均掩盖Yahoo/Reuters；同时实际刷新均值应贴近周期、P95尽量≤1.1周期，不隐藏窗口末尾。
- 将“收到新响应”和“上游列表可证明重新验证”分开，缓存年龄/验证证据不足记unknown，不自动当业务覆盖成功或真实漏文。

### 9.2 端到端时延

逐篇记录首次列表响应→capture commit→candidate→job→BODY开始/结束→body_ready→decision_ready→Bus可消费；另外记录下游实际claim与Web投影。以真正新文章、首次匹配ticker和全部应交付ticker分别统计，附不相关、被业务门禁拒绝、失败、未完成类别。

设计预算用于定位：列表→候选尽量≤1s，候选→BODY启动≤1s，常见有效正文≤4–5s，BODY就绪→正向Bus发布≤1s，总体留2–3s弹性。预算不是提前标FULL或超时丢文章的规则。慢站/付费/恢复仍保留长尾，不能把≤10s变成“10s后取消所有任务”。

首要成功标准是大多数可正常访问的新文章列表采集→可消费≤10s，均值/P95及≤10s比例明显改善；同时报告全量失败/未完成上界。发布者发稿→首次发现受30/60s轮询、上游索引/CDN影响，**不能承诺所有发稿10s内被发现**，也不能与本系统采集→发布10s混为一谈。

### 9.3 真实正文与资源

- 分母为首次列表中识别出的独立新文章；同时列出未进入BODY台账、已入队未终端、失败、PARTIAL与FULL/SHORT_FULL。不以发布成功子集、缓存命中次数、recheck执行数代替新文章分母。
- 先按观察时间冻结cohort，再给随时间成熟的结果；最新未完成保留pending/censored。业务相关性和paid能力分类可做解释，不能事后从全量分母删除难站。
- 标签完整率>80%之外，抽检有效主文、付费预览、相关推荐污染与短正文；确认错误FULL率不升。每个主要失败publisher保留真实badcase回归，不能只验证source名称。
- 减法验收同时测每100轮的业务事务/对象/CDC字符、进程实际读写与fsync、PSI/major fault、RSS/swap增量、应用写队列等待和CPU成本。目标是重复输入的业务写入不随轮询线性放大，且新文章持续有可兑现执行容量。
- 用同长度、相近负载窗口比较，部署/恢复段单列但不删除；至少一个盘中连续窗口与9→20ticker负载验证。低频读取生产快照即可，不进行高频全库统计或为了验收自身制造I/O风暴。

## 10. 不采用的捷径与尚未确定项

不新增Kafka/Redis/独立分布式调度框架；不将数据库全面迁移作为前置；不增加固定20个浏览器；不全库重抓/清空历史积压；不直接关闭交易/研究任务；不删必需频道/查询、不把fallback全部取消、不放松FULL与身份权益。

尚未确定但已给出最小验证路径：Yahoo具体阻塞gate；除已证实续作重放缺陷之外其余历史RuntimeError是否同因；BODY结束到READY/领取各自占多少；各公共publisher实际可回收驻留量；缓存端点能否用合法等覆盖途径达到30s；哪些轻量请求支持更短占槽。这些不确定项不妨碍先做已确认的续作幂等、输入去重复、状态/CDC减负和L1发布链路修复。

**本轮没有证明目标已经实现。** 已具备执行依据的是：先让旧输入和技术状态不再吞噬I/O及串行写通道，再把新文章从正文就绪到可消费的快速路径兑现，最后按真实端点成本和身份能力完成30s容量验证及正文质量修复。

## 附录：全部有应执行周期来源的对账

以下附表按主窗口旧due覆盖从低到高排列；真实时点覆盖使用独立物理证据重分配，不能将二者差额算作优化收益。更细的ticker/unit和开放缺口见 `unit-coverage.csv`。没有主窗口应执行周期的来源不填0%；新配置以实际生效时点计。

|来源ID|应执行|旧due新鲜|旧due全部覆盖|实际时间覆盖|最近窗口实际时间覆盖|
|---|---:|---:|---:|---:|---:|
|reuters_site_search|6258|25|0.4%|58.9%|68.3%|
|yahoo_finance_news|9720|4131|42.5%|53.9%|38.0%|
|trendforce_news|1350|735|54.4%|58.9%|71.1%|
|aviationweek_rss|270|149|55.2%|62.6%|60.0%|
|darpa_news|1350|770|57.0%|68.5%|77.8%|
|shephard_uncrewed|270|156|57.8%|66.7%|77.8%|
|sec_edgar|4860|2844|58.5%|75.5%|77.8%|
|army_news_rss|270|161|59.6%|69.3%|73.3%|
|army_news_releases|270|162|60.0%|68.1%|77.8%|
|digitimes_semiconductors|1350|819|60.7%|66.3%|71.1%|
|sdxcentral_news|1350|820|60.7%|72.2%|82.2%|
|register_software_cloud_security|809|492|60.8%|74.2%|83.0%|
|breakingdefense_space|270|166|61.5%|70.0%|75.6%|
|spacecom_launches|270|167|61.9%|70.7%|75.6%|
|wccftech_hardware|4046|2510|62.0%|74.5%|82.2%|
|dod_releases|540|338|62.6%|71.9%|80.0%|
|globenewswire_semiconductors_rss|1350|846|62.7%|67.4%|84.4%|
|siliconangle_ai|1350|850|63.0%|76.3%|84.4%|
|spacenews_rss|270|171|63.3%|73.0%|84.4%|
|dod_contracts|540|343|63.5%|73.7%|77.8%|
|ctee_semiconductor|540|344|63.7%|70.7%|84.4%|
|unmannedairspace_counteruas|270|173|64.1%|73.7%|80.0%|
|nasa_2026_releases|270|173|64.1%|73.0%|82.2%|
|investorshub_ticker_news|2430|1559|64.2%|76.4%|87.9%|
|airspaceforces_space|270|174|64.4%|75.6%|84.4%|
|defensescoop_space|270|174|64.4%|75.2%|80.0%|
|digitimes_tw_rss|1350|875|64.8%|75.2%|82.2%|
|huggingnews_rss|1350|875|64.8%|76.3%|82.2%|
|siliconangle_software|808|526|65.1%|77.2%|85.2%|
|trendforce_press_releases|1350|879|65.1%|75.2%|80.0%|
|venturebeat_software|808|529|65.5%|77.4%|85.2%|
|defensescoop_drones|270|177|65.6%|76.3%|80.0%|
|tomshardware_rss|1350|890|65.9%|76.7%|84.4%|
|data_center_frontier|282|186|66.0%|78.7%|80.0%|
|ans_nuclear_newswire|47|31|66.0%|76.6%|77.8%|
|thelec_semiconductors_rss|1350|905|67.0%|78.5%|84.4%|
|crn_cloud_security_datacenter|810|544|67.2%|77.8%|83.7%|
|power_engineering_energy|329|221|67.2%|79.6%|81.0%|
|etnews_rss|1350|910|67.4%|78.5%|84.4%|
|aviationweek_rocketlab|270|185|68.5%|73.7%|80.0%|
|venturebeat_ai|1350|925|68.5%|78.5%|84.4%|
|technews_hardware|2700|1855|68.7%|78.5%|84.4%|
|finnhub_company_news|2430|1684|69.3%|82.1%|91.1%|
|power_magazine_energy|418|293|70.1%|75.1%|74.8%|
|nrc_news_releases|47|33|70.2%|80.9%|82.2%|
|world_nuclear_news|47|33|70.2%|80.9%|82.2%|
|ssc_newsroom|270|190|70.4%|78.9%|82.2%|
|newswire_rss|2970|2103|70.8%|79.5%|82.8%|
|investing_ticker_news|2430|1734|71.4%|81.6%|91.6%|
|canary_media|92|66|71.7%|82.6%|82.2%|
|dcd_infrastructure_rss|276|198|71.7%|82.6%|82.2%|
|doe_energy_news|322|231|71.7%|82.6%|82.2%|
|utility_dive|92|66|71.7%|82.6%|82.2%|
|globenewswire_search|6258|4622|73.9%|84.1%|92.7%|
|calcalist_search|810|602|74.3%|83.1%|83.0%|
|corporate_ir_press_releases|6480|4898|75.6%|84.5%|90.4%|
|iridium_press_releases|270|205|75.9%|84.1%|93.3%|
|barrons_ticker_news|4860|3717|76.5%|86.3%|92.7%|
|ibkr_news|2430|1885|77.6%|87.2%|92.3%|
|enterprise_apps_official_rss|810|629|77.7%|87.7%|93.3%|
|google_news_search_rss|540|420|77.8%|87.8%|91.1%|
|optical_systems_official_rss|270|210|77.8%|88.5%|91.1%|
|benzinga_news|2430|1891|77.8%|87.5%|92.3%|
|thefly_ticker_news|2430|1896|78.0%|87.6%|92.8%|
|aws_platform_marketplace_rss|540|422|78.1%|88.7%|93.3%|
|lakehouse_platform_official_rss|540|423|78.3%|88.0%|92.2%|
|nbis_official_actor_rss|540|424|78.5%|88.7%|92.2%|
|observability_vendors_official_rss|540|427|79.1%|88.3%|93.3%|
|nrc_event_notifications|47|42|89.4%|95.7%|95.6%|
|dcd_energy_ticker|93|84|90.3%|94.6%|94.4%|
|tikhub_x_user_posts|27|27|100.0%|100.0%|100.0%|
