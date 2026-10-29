# 32GB整体集成、正式发布及45分钟观察

## 当前实施边界

用户授权：适配8核32GB，整合本地main全部开发，推送origin/main，正式重建相关生产容器，再进行完整45分钟观察；修复IBKR/Reuters之外仍失败来源。不更改W1/W2/W3能力或无依据削减预热，不删生产事实/Profile。

实测基线：32GB升级后MemTotal约31GiB，远端仍有14G/15G宿主slice、Site6G/Supervisor8G/Worker15G的旧边界。之前正常低在途10分钟Worker2.41–3.36GiB，浏览器合计约7GiB；积压释放时Worker约10–11GiB、Site约4–5GiB、Supervisor约3–5GiB。CPU仍8核，不能因内存翻倍机械翻倍浏览器或SDK并发。

## 内存配额

采用可借用的组件上限，非静态预留，总和允许超过父级。父slice改为MemoryHigh28G、MemoryMax30G，SwapMax保持1G；保留宿主正常余量，避免以大Swap掩盖回收抖动。

|组件|内存上限|内存+Swap上限|依据|
|---|---:|---:|---|
|Worker|20G|21G|覆盖已见10–11G积压峰值及后续ticker增长，不削减预热|
|Site Access|10G|11G|覆盖多Identity controller/managed Chrome及热页|
|Chrome Supervisor|10G|11G|受保护Chrome/Profile驻留与自然多页面负载|
|Scheduler|8G|9G|覆盖W1/W2/W3本地上下文及恢复重叠|
|API、Collector、Bus、BODY、Projector|各4G|各5G|显著高于既有正常占用，Collector补入统一slice|
|CDECR/初始化/O4等|保留既有6G/4G等|保留既有|不另加研究或W硬门禁|

压力控制器改读实际memory.max比例，28G+真实压力进入原PRESSURE逻辑，29.5G进入原接近硬边界逻辑；原时序/滞回不变，不残留14/14.75G硬编码。上线同时核对systemctl set-property生成的持久覆盖、Docker实际HostConfig及正式Compose配置，不能只改仓库文本。

## 全量集成与发布步骤

1. 保存本地/远端dirty、HEAD和资源/在途基线。包含退出策略模块、交易执行/行情修改、W3提示词、Runtime前端、运维脚本及调试工具/文档；不把多年eval生成物、凭证和临时结果作为开发源盲目入库。
2. 合并新退出能力时保留main已存在的ENTRY时效/三次失败放弃、并发订单对账、SDK回调持久性、非阻塞维护及短事务。旧分支覆盖这些修复必须整合，不能以“全部提交”为由直接发布回退。
3. 运行相应Python、schema/typecheck及前端夹具检查，审查完整staged diff，追加changelog，提交并推送main。
4. 先更新宿主slice及控制器，正式build backend/site/supervisor/web等实际需要的镜像。按依赖应用最新Compose与现有私有环境覆盖，正式更新全部实际消费者，保留数据/Profile和可续作在途事实；不用临时生产容器/source overlay。不默认启用任何尚未授权的退出策略账户绑定。
5. 核对组件版本、真实内存边界、健康、游标/case/Read和消息源实际进展。清退真正未引用的作废镜像，保留动态模板/依赖及必要回滚，禁止删卷。

## 45分钟验收及排查

完整窗口从正式必要角色就绪后开始，前段回补与后段自然负载分开，但失败不删除。以全部应执行source/ticker/必需频道周期验证真实取网/新鲜列表、延期/失败/缺失/在途；真实新canonical正文首次/当前FULL及article×ticker完整交付分母。Owner、Runtime消费、Read/UI边界分开，不能用已发布子样本代替最终消费。

资源每分钟低成本宿主/cgroup差分；较低频采集Worker真实job/预热归属。CPU仍8核，观察iowait、IOPS/吞吐/await/队列、匿名/文件缓存、refault、Swap/PSI及工作集增长。W1/W2/W3非LLM跨度只用真实持久跨度，缺失记不可测。按逐source证据修复IBKR/Reuters之外的真实故障，不因更大内存隐藏池故障、缺证据或错误解析。

以下记录实施与中期调查，末尾“45分钟最终验收”给出固定窗口的最终数据，后续修复不回写历史分母。


## 已发布的集成与来源修复（过程记录）

- `3ae8c6c7`：本轮开始构建时的全部完整main开发集成，正式构建backend/site/supervisor/web/guardian/repair-agent并更新19个生产角色。宿主实际MemoryHigh=30064771072、MemoryMax=32212254720；Collector已纳入同一slice。
- `ec98880b`、`7714a540`、`3c6ec643`：DIGITIMES列表迁到同一既有Site HTTP_PUBLIC路径，显式无压缩，并不留存该公开列表的匿名响应Cookie。首请求200、重放匿名Cookie500、清理后200的对照，以及保持连接复用的6次连续NL出口完整列表证据已留存；不改BODY身份、登录Cookie、出口或More News覆盖。
- `dd4be070`：IBKR新闻旧sendMsg覆盖与生产10.49.2 SDK的msgId参数/protobuf不兼容。删除SDK编码覆盖，改在该新闻连接的已编码socket字节发送处取证。Gateway重启后TCP已通；修改后自然3分钟10个ticker共30个周期全部CAPTURED，均有真实发送、historicalNewsEnd与list_validated，不再将SDK调用时间冒充取网。
- Scheduler原20分钟drain阻住正式Compose替换：先留存4个在途Worker job及旧Scheduler精确容器Id/启动时间/版本，再仅结束待退休的5e8f3bb6控制进程，正式Compose接续；未结束Worker任务、清库或清Profile。其停服/重建区间另列，不纳入稳定时段成功。

本轮45分钟观察窗口：2026-10-29 **21:15:39–22:00:39 UTC**（北京时间10月30日05:15:39–06:00:39）。窗口内发生上述来源修复发布，保留全部失败、重建及版本变化；另看最终修复后的稳定子窗口，不能只报子窗口或宣称最新版本连续45分钟稳定。

### 已确认的剩余成本（窗口中期，最终数字待补）

内存余量显著改善，当前无换页抖动；CPU仍高。Bus的非GIL样本显示初始化admission与多处短读取反复建连，在connect内重复解析SQLite schema/设置pragma。生产只读微测：24次新建连接+相同pragma/SELECT1花CPU185ms，一次连接执行24次相同操作花7.9ms。这证明连接初始化存在真实重复CPU成本，**不等于整条Bus可加速24倍**。可直接执行的后续减法是让一次同步admission验证和一个本地工作单元复用现有connection_scope，仍每轮读取当前事实，短事务不跨网络，不新增全局锁、版本TTL或业务门禁。

W3时点证据分离模型执行：中期83份effect trace中context.prepare均值350ms、max1659ms，44份history.payload均值3447ms、max13307ms；ready_claim最长10919ms。它们有嵌套，不能相加当纯本地总耗时。Worker仍出现START_THREAD的dictionary changed size during iteration，已记录任务级失败及恢复，尚无完整异常栈证明具体共享字典归属，不能归因于内存或擅自削减预热。

InvestorsHub慢样例包含22.9秒/26.7秒的真实浏览器执行，分别存在5.6秒建页、导航及重试，且样例靠近Site重建冷启动；应分别检查稳定热访问与发布重建。Reuters独立必需查询的资源排队继续保留在整体分母。

### 取证边界

证据位于 `eval/32gb-release-20261030/`。主机每分钟一条轻量差分；角色重建导致旧cgroup结束，原序列对应角色停止采样，后续新序列从当前epoch接续，不能把缺失cgroup记0或跨重建相减。SQL按时间索引/主键限定；每篇新文章仅定点读取小型编码root取得发布/SDK callback元数据，未展开BODY/历史Native大对象（中期171篇共约590KB）。SDK callback T0与周期真实发送证据分别统计，callback不代替应执行轮询周期证明。

期间新发现另一会话继续修改本地W3回测/提示词文件，属于首次正式集成构建截面之后的新工作；保留原修改，用户已确认仍在开发，本轮保留本地，不静默打包为已验证版本。


### 在途收尾核对

Scheduler交接前记录的4个Worker任务，截至21:50Z均已succeeded，逐ID查询已保留于handoff-jobs-final.json；不是仅看到Worker健康就推断任务完成。原每2小时自动观察任务现场状态为PAUSED，本轮没有擅自恢复；这次45分钟是独立人工授权的完整服务观察。


### 慢浏览器样本再分解

中期InvestorsHub成功访问中，复用暖页217次平均4093ms、p95 6226ms、最大8609ms；无复用/冷访问7次平均16088ms、最大26702ms。显著长尾集中在Site重建后丢失暖页及重试，不能把全部26秒归为网站取网，更不能用其作为日常每次请求必需成本。热路径仍有p95>5秒，CPU并发与DOM/提取成本仍应继续精简。源内错峰也不等于共享Identity间已联合错峰；3秒准入窗与冷访问长占槽的交互仍会丢掉有效周期。

Calcalist复测经原US路线，ONDS返回200且解析9篇，希伯来查询返回200空结果（保留为空，不伪造新闻）；自然生产仍有间歇CSE403。证据定位在上游搜索响应拒绝，未证明是DB/Chrome压力或HTTP池故障；当前已有失败后清token及下一轮恢复，未凭猜测增设浏览器回退、换IP或把403当空成功。

### 验证清单

- 交易/退出与原有恢复63项通过；配额/交易维护/适配器66项通过、3项跳过；32GB阈值专项9项通过。
- 最终消息链路、列表解析、Runtime Native消费与Worker回执60项通过；SDK/端点专项7项通过。测试集合有重叠，不将次数相加当不重复覆盖数。
- 前端类型检查、67项Vitest和1262/1559两种桌面宽度Playwright通过；截图为本地fixture验证，不冒充生产浏览器验收。
- 未自动给账户启用新退出策略，不删除历史/接纳事实，不削减W1/W2/W3或预热，不制造测试新闻。自然采集与来源只读对照请求分别留痕。


## 45分钟最终验收

**结论：32GB配额正式生效，内存/物理读抖动缓解，但采集覆盖及快速交付尚未达标；83.2%的总体正文率也不能代表所有publisher已恢复。** 窗口包含来源修复发布及IBKR恢复后的自然消息，不包含测试新闻。纽约时间为10月29日17:15–18:00盘后，不代表整个盘中或20 ticker/30秒容量验收。另一会话的新W3回测改动由用户明确确认仍在开发，本轮保留本地，未打包发布。

### 资源

|指标|完整45分钟实测|
|---|---:|
|主机样本/差分区间|46 / 45；2700.000秒|
|MemAvailable 最低/均值/结束|13.63 / 16.26 / 16.39GiB|
|Swap使用采样最大|0.246MiB（不等于新分配的大量Swap）|
|CPU busy均值/分钟差分最大|90.35% / 98.10%|
|iowait均值/分钟差分最大|0.50% / 0.82%|
|物理读/写总量|3.06 / 47.51GiB|
|物理读/写平均|1.16 / 18.02MiB/s|
|平均IOPS / await / 磁盘util|1398 / 1.27ms / 12.44%|
|平均/最高分钟磁盘队列|1.87 / 3.16|
|memory some stall分钟差分均值/最大|0.00396% / 0.07823%|

这些是同boot、同窗口物理磁盘差分，不能将容器rbytes再次相加进主机总量。3.06GiB不能外推成24小时已根治旧TB级读取；47.51GiB写入也不等于同等磁盘净增长，WAL/重写/SDK工作区及本窗口构建均有贡献。平均await按一分钟区间加权，非逐请求原始延迟。sysstat显式TZ=UTC读取sa30，单独保留原文，不把10分钟采样点当瞬时真实峰值。

|角色|memory.current采样范围GiB|工作集采样峰值GiB|
|---|---:|---:|
|codex-worker|2.45–4.79|4.17|
|v2-site-access|2.22–4.57|4.29|
|v2-chrome-supervisor|3.18–5.03|4.53|
|v2-scheduler|0.58–2.16|1.49|
|v2-message-collector|0.28–0.43|0.32|
|v2-message-bus|0.14–0.20|0.15|
|v2-content-enrichment|0.19–0.24|0.21|

Site/Collector表使用后续有效epoch的补采，不能视作两个角色45分钟完整逐分钟覆盖；旧epoch已有明细另存，重建缺口保留。其它角色采用原完整序列。所有正式长驻角色最终仍运行，已有healthcheck的均healthy，未见OOM/restart；没有healthcheck的“running”仅是进程状态。v2-migrate是既有退出的迁移角色，不要求常驻。父slice实际位于`/doxagent.slice/doxagent-app.slice`，已按systemctl ControlGroup核验；MemoryHigh=28GiB、Max=30GiB、SwapMax=1GiB。历史high累计16426没有窗口起点差分，不冒充本轮新增；OOM累计为0，当前压力控制器NORMAL。

Worker不是9.9GiB常态：本窗口memory.current峰值4.79GiB、工作集峰值4.17GiB。开始/结束3组明确READY预热PSS合计约674/736MiB；高负载时它们转为BOUND/active，不能把所有SDK进程统称预热。USED保留环境、未映射候选和active任务独立记录。保留原预热及W1/W2/W3能力，没有基于容器总额裁掉预热。

### 采集周期及单次轮询

- 所有应执行周期19751，新鲜18429，全部周期覆盖 **93.31%**；已结算或逾期分母19748，覆盖 **93.32%**。缺少执行438，窗口末尾pending3，失败和真实取网缺证据均保留。
- 有终态的全部attempt共19299，dispatch→finish平均 **1.039秒**、p95 **3.857秒**、最大 **64.008秒**；去重物理run 15208个，均值1.110秒。另有11个attempt在截止点未完成，不能从均值推断它们成功；无attempt的缺失周期也不进入耗时均值，单独进入周期分母。
- due→dispatch平均0.101秒、p95 0.134秒、最大13.982秒。**调度很快、失败也很快，都不能代表必需列表真正刷新。**
- 有效列表内部相邻间隔均值56.91秒、p95 60.91秒；最大缺口2700秒。不同来源配置不同，应逐unit对配置周期验收，不把这个混合平均当所有源达标。

|来源|应执行|新鲜|新鲜/全部周期|
|---|---:|---:|---:|
|calcalist_search|135|57|42.22%|
|spacenews_rss|45|31|68.89%|
|huggingnews_rss|225|155|68.89%|
|reuters_site_search|2100|1455|69.29%|
|ctee_semiconductor|90|64|71.11%|
|digitimes_semiconductors|225|160|71.11%|
|digitimes_tw_rss|225|165|73.33%|
|ibkr_news|450|366|81.33%|
|yahoo_finance_news|1807|1584|87.66%|
|investorshub_ticker_news|450|438|97.33%|
|newswire_rss|630|616|97.78%|
|etnews_rss|225|220|97.78%|
|thefly_ticker_news|405|399|98.52%|
|investing_ticker_news|450|445|98.89%|
|barrons_ticker_news|900|895|99.44%|
|globenewswire_search|1125|1119|99.47%|
|sec_edgar|855|853|99.77%|
|finnhub_company_news|450|449|99.78%|
|corporate_ir_press_releases|1080|1079|99.91%|

以上保留IBKR、DIGITIMES修复前和后续代理故障的整窗影响，不用最后成功样本替换原分母。全部逐unit明细在`eval/32gb-release-20261030/unit-metrics.csv`，包含请求启动与新鲜列表间隔、过期/失败/未完成。RSS不能抵销Yahoo必需Browser及Reuters TAIL等独立单元缺口。

### 正文与真实交付

|指标|结果|
|---|---:|
|首次发现新canonical|191|
|首次FULL/SHORT_FULL|159 / 191 = **83.25%**|
|IBKR SDK原生正文|138 / 138 FULL或SHORT_FULL|
|其它来源正文|21 / 53 = **39.62%**|
|全部发现article×ticker|216|
|明确IRRELEVANT（不属于应交付）|17|
|全部应交付（含NO_PUBLIC_BODY失败）|199|
|Owner已可消费发布|195；另4个NO_PUBLIC_BODY|
|可证明T0且有T1的交付|175|
|可证明≤10秒 / 全部应交付|**96 / 199 = 48.24%**|
|可测175个中的≤10秒|96 / 175 = 54.86%|
|可测T0→Owner T1均值/p50/p95/max|17.53 / 8.37 / 57.16 / 81.90秒|

T0由59篇真实发送完成响应、112篇逐篇SDK callback证明，另20篇缺完整证据不补造；不会用“已发布195”作为成功率分母。发布T1与Runtime消费、UI投影分开。正文总体过80%主要来自IBKR原生正文，**不能宣布P1系统性恢复**。Google News 31篇只有10篇首次FULL，Yahoo 3篇只有1篇，Aviation Week4篇无FULL，TheFly4篇PARTIAL；对聚合publisher必须按最终站点访问能力再细分。TheFly既有允许PARTIAL发布的合同未改变，其快速发布不被当成FULL成功。

### IBKR交付慢的结构性证据

138篇IBKR文章中，对可证T0且匹配捕获提交的119篇，T0→capture提交均值 **18.08秒**、p95 **52.27秒**、最大 **58.47秒**；捕获提交→Bus接纳的整体138篇均值0.791秒、p95 2.238秒。瓶颈主要在进入Bus之前，不是再扩大Bus writer并发。

`IbkrNewsAdapter.poll()`与`_deliver_ticks()`都先`await asyncio.gather(complete(row)...)`为整个批次取得SDK文章正文，再`on_page`。`_article_slots=4`、单篇最多等待5秒，突发批次使已完成的快文章等待其它慢文章，且回调任务运行期间的新tick可能等下一轮。这解释了SDK已取到消息、正文最终100%成功，而真实可消费发布仍慢。UNPROVEN修复主要修正新版SDK真实发送观测；修复前已有成功回调与消息，不能声称这些都是真实漏取网。

后续最小优先改法：复用现有on_page，按已经完成的小批增量提交，完成一批才标记其delivered，收尾对账期间新到tick；保持4个实际SDK获取槽、原provider文章身份、缓存、checkpoint与ARTICLE_ONCE。先用“一个慢文章不阻塞其它已完成文章、处理中又到tick、提交失败续作”测试验证，不为这一点引入新通用队列或额外Chrome。如果仍有SDK获取队列容量缺口，再评估将SDK BODY移至既有BODY责任域，不能先扩大并发。

### Runtime与非LLM开销

197个case在边界查询时均标记COMPLETED，但其post-decision仍有3个effect RUNNING；不把case完成等同后续operations全部结束。Worker共280条本窗口新任务：260成功、17次START_THREAD字典迭代异常、3在途，保留失败任务，不以case层自动恢复掩盖。180份既有边界trace：

|跨度|n|均值ms|p95 ms|最大ms|
|---|---:|---:|---:|---:|
|claim_ms|180|314.0|1259.1|3979.0|
|thread_start_ms|180|6.8|28.3|76.1|
|journal.commit|180|292.1|672.8|885.1|
|context.prepare|180|396.4|911.9|4377.0|
|case.context_bind|168|362.6|1198.1|2254.5|
|thread.ready_claim|75|6052.2|14500.3|29864.8|
|case.thread_prepare|89|6529.1|15928.1|30867.2|
|history.payload|89|5581.7|13356.2|15428.6|

这些跨度有嵌套，不加总为非LLM时长；没有独立thread.fork_roundtrip、W1/W2 case创建函数起止的样本记不可测，不以case/job总时长替代。窗口结束后22:19Z的补充取证显示10 ticker stream pending全0，是截止之后的后续事实。Read source_health当时各head/checkpoint追平，同时仍有历史PROVENANCE_UNVERIFIED缺口，不能把水位追平等同全部UI对象已证明完整。

### 后续优先级（与本轮已执行区分）

1. **P0先恢复真实代理上游能力**，再验收依赖身份的全部频道。不能通过将UNAVAILABLE硬改READY、乱换受保护身份出口或牺牲必需频道来伪造恢复。
2. **P0 IBKR增量交付**：上节gather整批屏障优先于继续调整数据库并发；流式小批及新tick收尾对账保持原业务语义。
3. **P0正常路径建连与W3历史物化减法**：一次同步admission/本地工作单元复用现有connection_scope；相同冻结历史按不可变输入复用准备结果，避免每个阶段重新解码/深复制完整对象。以SQL/CPU与真实历史变更回归证明，不引入TTL状态门禁或W限额。
4. **P1按实际publisher修正文与认证**：先恢复代理后复测Google/Yahoo各最终publisher；Investing登录/权益、DIGITIMES受保护BODY不能套用公开列表Cookie修复。Worker START_THREAD需要补内容无关异常帧并定位共享可变对象，当前证据不足以宣称具体字典根因，不削减预热代替修复。

## 观察后发现的区域代理故障与已执行恢复

- JP/US/NL/DE/TR固定出口均对ipify及真实业务URL发生连接失败，直连同时成功；Mihomo对现有固定节点返回503、缓存的另一provider多国节点返回503/504。此时CPU/内存已经有明显余量，因此不能把当前连接失败归因于内存紧张。
- 订阅刷新SSRDOG返回“当前网络环境风险，请关闭代理获取订阅”，Flower_SS返回403；本机只读读取旧URL也未获得配置。历史节点名带的到期日期不是当前账单证明，**没有据此断言订阅已经过期**。没有改本机Singapore selector，也没有轮换Profile/IP。
- Calcalist先出现独立CSE403，后又叠加区域出口故障；出口恢复后仍需复核token/上游拒绝原因，不能把两个阶段硬归为同一根因。HuggingNews直连403、CTEE直连API403已实测，未做假空列表或强制切直连。
- **已执行 `8085064d`**：SpaceNews固定公开列表/Reader改已验证直连；DIGITIMES Taiwan默认feed_http_routes为direct，生产经MessageBusV2Service.update_source治理更新v6→v7，60秒周期未变。其Collector与BODY正式重建上线。后续3分钟完整样本IBKR30/30、DIGITIMES Taiwan15/15个绑定轮次CAPTURED；SpaceNews有1次CAPTURED、2次真实429延期，不能称其持续恢复或满足既定周期。SpaceNews保留真实Retry-After/既有延期，不以空结果掩盖429。该修复发生在45分钟窗口之后，**不回改93.31%的历史覆盖率，也不宣称它又经过45分钟完整观察**。
- 受保护身份来源仍受区域代理上游影响，当前未全部恢复。已经向用户核对账号/订阅状态；需要有效的现有区域节点或更新订阅，才能完成这部分恢复。停止无效请求由既有egress状态处理，没有追加研究/W硬门禁。
- 正式release已按活跃引用及模板依赖清退确认无引用的`storage-7714a540487b`镜像；保留数据、配置、Profile、已引用镜像和必要回滚，不执行全局prune。


### 最终Review与未解决项

- 完整45分钟取证8个模块均成功，所有分片complete，未遗漏失败分片；结束状态中没有case/Worker完成时刻越过窗口后被误算为窗口内成功。
- 查询覆盖74个source、每个必需单元及其schedule；完整主机序列46样本，角色重建分epoch记录。观察后的RSS路线修复与更晚的代理故障调查均单列，不回填本窗口分母。
- 公共直连有边界：DIGITIMES Taiwan持续成功；SpaceNews后续出现429，需连同BODY/旧文recheck的同publisher总请求量继续核对，不能只看每个独立进程的并发上限。没有通过降低配置频率或忽略429宣称恢复。
- 当前区域代理失效仍阻碍Yahoo、Investing、InvestorsHub、CTEE、HuggingNews等；已定位到真实HTTPS上游连接和无效订阅刷新，未证明是过期账单/新代码错误，不随意轮换国家或动Profile。恢复外部代理前不能完成完整业务负载验收。
- 从本轮CPU/内存/磁盘数据看，再增加内存不能独自解决IBKR批量屏障、W3历史反复物化、SQLite重复建连及Worker启动异常。也没有证据支持削减W3预热作为本轮修复；它的明确READY物理PSS约0.7GiB。
- 正式当前版本：Collector/BODY `8085064d`，Site `3c6ec643`；Scheduler/Worker/API/Supervisor及其它首轮消费者 `3ae8c6c7`。后续commit只有报告/薄证据，无需再重启这些稳定的应用角色。W3并行开发工作按用户回复保留未提交/未发布。
