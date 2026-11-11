# 消息源提频后持续／间歇失败：根因排查与可执行修复方案（2026-11-11）

## 结论与边界

优先保留现有业务轮询目标，修复内部准入／连接池生命周期、首页与补充工作成功边界、NRC解析契约与分支恢复。没有证据支持将全部失败归因于提频或网站风控，也没有证据支持一键回调所有频率。真正的429/明确拒绝须独立处理：不能承诺在服务端明确限流时仍无条件按原请求次数发送。保留“业务发现周期”与“某受限lane的实际请求退避”的区分。
本轮只排查及产出方案：没有修改业务代码、配置、出口、Profile，没有重启/发布，没有主动向新闻网站或验证码供应商发送测试请求。Reuters在SQL与方案中排除，不处理其问题。

## 时间、数据与版本

- 主窗口：北京时间 11-11 03:54:30 至 11-11 22:40:17，约18小时46分。近期细查窗为最近3小时；状态表为最近1小时（11-11 21:40:17—11-11 22:40:17）。不是完整24小时，更不是连续一个自然日。
- 去重检查 827,464 个采集周期、92 个源/IR入口范围。按 physical_run_id 去掉 ticker fanout；CAPTURED仅表示周期取得有效采集，不等于所有阶段无错误、新文章或一次新的外部网络访问。
- 当前 Collector 仍为 c232f094、容器自03:53:43持续运行；SiteAccess/Supervisor05d60622，Bus8c203e62、BODY9a38f508。源治理容器本窗口无重启/OOM。远端主线虽已到3348f2fc，不代表这些容器已加载该主线的全部代码。不得把其他角色发布假定为Collector已更新。
- 上轮首窗与最终窗见 docs/message-source-cadence-rollout-20261111.md：最终窗11198 CAPTURED、253 FAILED、224截止时在途，正式记录为“部分通过”；用户后来观察到的恢复段在小时历史中确实存在，随后再次衰减。必须同时区分上轮已遗留的问题与运行后复发。源聚合轮次数受活跃ticker/FEED数量及跨轮在途影响；不能只凭成功次数变少推导容量退化，应联合失败占比与同一unit成功间隔，本方案不以跨源聚合次数推导因果。
- 86个正式源/入口最近小时：5个整小时持续阻塞、28个失败率≥10%的间歇故障、32个失败率<10%的间歇故障、21个没有失败。整小时阻塞为NBIS IR、NRC Event、Canary Media、TechNews、Quantum Zeitgeist；Army两入口与Tom’s则在21:49后转为持续超时，尚未占满本小时，不能被“间歇”标签掩盖。试验 Massive 单列，不冒充正式源故障率。表格按源/入口聚合，不能据聚合最大连续失败数声称每个ticker都相同。

## 从“恢复→复发”的机制看问题

### A. 多源随运行时间衰减，已有恢复机制覆盖不到等待阶段（P0）

- SEC在北京时间11-11 06:00–07:00（UTC10日22时）有3173次成功、0失败，当前小时505成功/230失败；Benzinga早期约1900成功/小时，当前1430成功/120失败。当前3小时，Benzinga334个FAILED周期中320个没有请求事件；SEC667个中642个无请求事件。并非大量返回429，不能用“供应商太慢”解释。
- NBIS IR前数小时每小时约178–179成功/180次，16:04:47最后成功，此后当前三小时0成功/539失败（另1次在途）；有明确PoolTimeout，近期无发送回执。连接池等待阶段已确认；究竟是连接未归还、长读体占槽还是池对象状态损坏，现有遥测不能唯一证明。
- Army新闻、Army RSS、Tom's Hardware原本数小时每小时约180成功，最后成功均集中于北京时间21:49:07–08，随后大面积超时。跨不同站与传输同时衰减更支持公共调度/等待因素；不能直接断言三站同时触发风控，也不能武断说它们共用同一个HTTP连接池。
- 代码链：publisher_http.joint_permits 用多个Semaphore的 locked/_value 与 held 预约来回移动，缺乏显式FIFO/阶段等待证据；外层45/30/12秒截止含准入排队，但 PublisherClients 的8秒计时与 _transport_timeout 只在 joint admission 后才启动。若卡在准入，外层先取消，既没有发送回执，也不会进入已有“连续pre-send timeout更换client”的恢复路径。该覆盖缺口是代码确认；具体某一次是否为活锁/饥饿须后续可复现测试与阶段指标证明。
- corporate_ir/http.py 的host HTTPX client固定2连接、pool timeout1秒，_client只创建不做坏池恢复；重复PoolTimeout不会重建该host。poll_trace.install先占host与全局许可，等待时间又与网络执行混在外层截止中。
- 修复边界：不通过全局重启、加大所有timeout或清Cookie恢复；也不能把“无发送记录”一律视作坏池（DNS/TLS/追踪缺口也可能无发送）。

### B. 首响应与整轮成功强耦合，补充步骤是明确风险点（P0）

- TechNews近3小时WordPress JSON有456次200，但主接口和分类HTML总共仅2个验证结果；最近1小时0成功/180失败。Quantum Zeitgeist首接口169次200，仅2次验证，最近1小时0/60。两者checkpoint的listings为空，与长期未建立完整成功状态一致。
- public_listing._one 的同一个12秒scope覆盖主请求及补充工作：wordpress强制offset100第二页；technews_embed强制再抓分类页。任一补充失败就抛弃整轮，转RSS；fallback仍附带主失败、清空next_checkpoint/recovery_delta，且不能续主爬虫健康。首次主响应成功不等于整个采集契约通过，必须把二者分开。
- 不应简单删第二页/分类页，那会损失现有覆盖。改成“验证首页先提交＋补充待办独立有界重试”，保留页游标、未完成覆盖和错误；补充完成前不能宣称FULL coverage。仍使用现有unit/checkpoint/on_page，不新增一个平台。
- Utility Dive主页200但仅部分被验证；其代码没有上述WordPress强制第二页，不能套用同一精确根因。应单独分解读体、解析及已有 fallback 的错误，保留为未完全定位项。Canary则近3小时请求没有收到状态码，是另一个“发出后等待”分支，不可也写成首200被第二页阻断。

### C. NRC是确定的解析失败循环，频率无关（P0）

- 当前小时0/180，原上轮发布前就15/15失败；当前540次页面响应均200、0验证。稳定异常 nrc_event_licensee_or_facility_missing。持久endpoint状态failures已4016，仍反复重抓同一整日页面。
- energy_sources.field只认精确<b>Label:</b>及node.tail；parse_events遍历任意en数字块，一条缺Licensee/Facility就raise整批。合法非反应堆事件字段差异与真实DOM变化尚需保存HTML样本确认，不能把缺字段直接判成真实无效事件。
- 改为标签规范化（b/strong/td、空白/冒号、相邻单元格），依据稳定事件号切分；每事件独立解析。事件号/报告日/正文必须有效，缺主体则依据正式报文类型走受控兼容或隔离该事件，不编造licensee、不把坏页当空列表成功。坏事件不得吞掉同页其他可用事件；解析失败与网络重试分类分开。

### D. 真实外部拒绝与分支健康不能混成全源不可用（P1）

- Calcalist通过Google CSE；近3小时搜索JSON274次403，cse.js能返回200并不证明查询可用。现有token失效单次刷新可保留；真实拒绝不得无限刷新/换IP。需要许可稳定搜索入口，必要时该源单独降级，而非降低其他源。
- SpaceNews仍保持60秒，近3小时8次429；BE/MU IR RSS各约246/249次429，HTTP仍有427/309个有效响应。维持业务目标，正确遵循受限lane的Retry-After、让健康lane持续发现；不能为宣称“不回调”无视明确服务端限流。
- public_listing fallback主动保留primary failure是有意设计，不应把RSS消息成功伪装成HTTP爬虫成功。但SOURCE可用性、主HTTP健康、RSS能力应分别表达；已获有效传输/解析证明的RSS可继续交付并标主lane退化。优先复用已有rss_http_race，避免新增复杂状态机。

### E. 浏览器与BODY问题要按用途分开（P1）

- Investing当前小时1187成功/11队列失败，InvestorsHub1178/20，Barron's3459/41，Yahoo3578/19，SDx116/4；大量回执属于BUDGET_DEFERRED而非验证码。SiteAccess的3秒队列截止、BODY争用、单身份实际容量与公平性应单独查。
- 网站维度最近3小时另有Barron's14次subscription_required、Investing10次login_required；这是BODY/权限线索，不应据此把所有ticker listing失败解释为掉号。本轮没有打开浏览器，不能据DB代替人工确认登录态。保留认证与订阅错误，不自动重登/清Profile。

## 可执行实施顺序（本轮不执行）

### P0-1：阶段账与守恒回归，先解决公共故障

1. 修改 publisher_http.py / poll_trace.py / corporate_ir/http.py / collection_plane/worker.py：已有周期中增加 admission_wait、host_wait、pool_wait、connect/TLS、headers、body、parse、commit_wait 的轻量耗时及最后阶段。使用现有ContextVar/trace，不完整抓包、不持续截图、不每100ms写SQLite。正常成功只存标量；失败附带owner/client-generation/attempt-id与有限shape。
2. 给 joint_permits 做确定性竞争/取消复现：两个publisher、共享role、相反到达顺序、等待者取消、source pacing、动态shrink；断言全部许可最终归还且饥饿有界。若证实预约循环，改成同一event loop内的FIFO eligibility仲裁；不得等待host时长期占全局容量，也不把共享_Source并发硬上限偷偷放大。
3. 对PoolTimeout或句柄枯竭实现按host/client generation的单次恢复：标记DRAINING、停止新租约、旧租约在有界期限内释放，再切新client；不关其他host，不清checkpoint。容量排队不触发client重建，DNS/真实403/429不算坏池；HTTPX及Curl均覆盖。
4. 将准入截止与已发出请求截止分开记账：前者返回可追踪的内部延期并保留下一正常轮询机会；不重试同一个未确认提交的网络请求。不要仅把45秒改成120秒。
5. 查线程/存储竞争：记录已有executor pending、ledger writer wait与maintenance批次耗时；本轮source容器未重启而渐进恶化，不能仅靠资源快照认定具体GC代码有错。修复GC/读取工作不能长期占默认executor或热路径writer，复用现有bounded lane，不新增第二套维护服务。

### P0-2：逐源窄修复

- NRC按C修解析与单事件隔离，使用生产失败页fixture。
- TechNews/Quantum按B拆首页/补充成功边界；定义supplement_pending及覆盖欠账，复用现有checkpoint持久化。
- NBIS IR先用P0-1的pool守恒/host恢复回归证明，再应用相同host策略；不能以重启一次成功交付。
- RSS fallback的验证证据必须带回Caller，主HTTP失败独立保留；只有RSS真实有效才允许其支撑SOURCE降级交付，不能将403 HTML或未验证200当成功。

### P1：仍复现的单站问题与调度容量

- 按下表逐入口验收；共享层修好后仍有的read-body、解析或拒绝才改adapter/route。
- 保持已核定source/identity硬预算及所有QUERY/TAIL业务目标；用公平错峰、真实request_multiplier（双lane/第二页）记账。GLOBAL一刀切扩容不能替代计量。现有试验Massive占用算入负载，并给正式源明确可用份额；不将其当成不消耗预算。
- 修正配置覆盖清单：API评估曾建议DIGITIMES English listing按HTTP20秒，但上轮SOURCE_INTERVALS没有列入它，目前仍60秒。这是执行名单漏项而非运行漂移；它当前健康，先修公共层，再独立确认是否纳入20秒，不能偷偷把频率缺口说成已全部兑现。CTEE60秒是明确保留例外。


### 实施文件与最小完成条件

| 工作 | 现有文件/接口 | 完成条件 |
|---|---|---|
| 等待与连接恢复 | `src/doxagent/message_bus_v2/publisher_http.py`：`joint_permits`、`PublisherClients`；`poll_trace.py`：`TRANSPORT_PROGRESS`、`httpx_request`、`install` | 保留真实发送证明；既有周期只增加有限阶段标量。HTTPX/hcore未暴露的pool等待不凭空计算，记unknown并以外部租约计数/阶段定位。取消后许可与client租约守恒，容量等待不误触发重建。 |
| IR长期退化 | `corporate_ir/http.py`：`PublicHttp._client/fetch/_stdlib` | NBIS坏host可在不重启Collector时恢复；旧client有界排空，线程仍在工作时不释放假许可，保留正常host与checkpoint。 |
| 首页/补充及回退 | `public_listing.py`：`ListingAdapter._one`；`rss_http_race.py`；`collection_plane/worker.py`现有`on_page`与checkpoint提交 | 首页通过解析才提交；补充页独立欠账、到期重试，保留历史覆盖；RSS成功带其自己的验证证据，主HTTP失败持续可见。不能凭200绕过parser/transport gate。 |
| NRC事件契约 | `energy_sources.py`：`field`、`parse_events`；`technology_sources.py`现有endpoint状态 | 复现当前字段错误的fixture通过；一条坏报文隔离、同页正常报文继续，合法空页/异常壳严格区分；修复后只释放对应parser版本的旧重试状态。 |
| 浏览器延期 | Site Access现有调度／身份队列及调用方SiteAccessError分类 | 并发硬上限不变，CRAWLER/BODY公平与deadline分开；排队延期不自动生成人工Challenge，也不能仅改错误名而不改善实际成功间隔。 |

不引入新守护服务、新数据库或通用状态平台。复用已有lease/client generation、checkpoint和结果模型；确需新增字段时同时检查已部署旧Bus的严格schema兼容。重要代码修改写changelog。离线回归先锁定取消/释放和结果边界，再在自然流量中验证，不以“监控补齐”替代真实修复。

当前`worker`在valid分支已提交`next_checkpoint`，未证实通用的“零新增文章就丢checkpoint”缺陷；不得顺手重写。POWER当前9条ticker绑定实际对应7个唯一FEED，预算按唯一FEED及真实lane计算，不能按9×2误算独立18请求。

## 逐源／IR入口诊断与决策

下表包含当前小时每一个有失败的正式源，以及试验源。首完整小时为北京时间04:00–05:00；当前小时为21:40:17–22:40:17。全部92个入口的全窗、首完整小时、当前小时统计另见CSV；小时走势在audit.json。最近失败形状来自14:47 UTC前的补充读取，可能晚于主快照22:40:17北京截止，不能将其次数混入主统计。

HTTP记录是采集账中请求事件的状态分布，不等同独立物理请求总量，None表示未记录响应状态；IBKR为SDK，None不是HTTP失败。失败无发送事件仅定位观测边界，不能单独证明许可泄漏。原因计数可能包含部分成功轮中的分支错误，因此不强求与FAILED轮数相等。聚合初小时已失败的入口须按“遗留/持续故障”处理，不能都称提频新故障。共享层分类与修复路线不等于每个网站内部原因已经被唯一证明。

| 范围 | 模式 | 首完整小时成功/失败 → 当前小时成功/失败 | 最近3h证据 | 修复决策 | 频率 |
|---|---|---:|---|---|---|
| access_newswire_massive | test | 111/9 → 0/120 | 周期成功/失败 0/360；HTTP记录 无记录；失败无发送事件 360。最近小时原因 HTTP_LISTING_FAILED:120 | P1：试验源：准入/请求或回退超时，未证明供应商拒绝；先纳入公平容量、取消回收与坏池治理；delivery_mode=test 不得长期挤占正式源全部预算；单独恢复各publisher。 | 保持30s |
| airspaceforces_space | production | 180/0 → 176/3 | 周期成功/失败 529/10；HTTP记录 200:533，304:492，None:30；失败无发送事件 4。最近小时原因 HTTP_RSS_BOTH_FAILED:3；最新形状 http:TimeoutError:; rss:RuntimeError:defense_all_endpoints_failed:TimeoutError: | P2：间歇失败；现有回执不能唯一定位，需复用共享层分阶段证据；不另写整站重构；在同一源/同一feed验收共享层修复，再仅修仍复现的超时或解析形状。 | 保持20s |
| ans_nuclear_newswire | production | 180/0 → 37/65 | 周期成功/失败 152/165；HTTP记录 200:219，None:630；失败无发送事件 0。最近小时原因 HTTP_RSS_BOTH_FAILED:65；最新形状 http:TimeoutError:; rss:TimeoutError: | P1：近3h219个200、630个发送后无状态，HTTP/RSS共同超时；失败周期并非无发送。分解响应等待与双lane预算，保留有证明的先到结果，不只增加准入重试。 | 保持20s |
| army_news_releases | production | 178/2 → 18/161 | 周期成功/失败 268/270；HTTP记录 200:268，None:54；失败无发送事件 216。最近小时原因 RuntimeError:161；最新形状 RuntimeError:defense_all_endpoints_failed:TimeoutError: | P0：同一时刻停止成功；跨传输共性故障，非已证实三站同时风控；优先排除共享准入、线程/存储等待和各自池退化；失败阶段确认后只重建坏资源，不重启全部服务。 | 保持20s |
| army_news_rss | production | 178/2 → 16/163 | 周期成功/失败 213/326；HTTP记录 200:25，304:188，None:128；失败无发送事件 198。最近小时原因 RuntimeError:163；最新形状 RuntimeError:defense_all_endpoints_failed:TimeoutError: | P0：同一时刻停止成功；跨传输共性故障，非已证实三站同时风控；优先排除共享准入、线程/存储等待和各自池退化；失败阶段确认后只重建坏资源，不重启全部服务。 | 保持20s |
| aviationweek_rocketlab | production | 166/14 → 143/36 | 周期成功/失败 431/106；HTTP记录 200:45，304:386，None:45；失败无发送事件 15。最近小时原因 RuntimeError:28，UNPROVEN_LISTING:8；最新形状 RuntimeError:defense_all_endpoints_failed:TimeoutError: | P1：28次RuntimeError及8次UNPROVEN_LISTING；近3h大量304/200成功。区分真实fetch失败与无验证证明/恢复待办，保持baseline与已验证304链，不把所有空列表当失败。 | 保持20s |
| aviationweek_rss | production | 300/0 → 202/97 | 周期成功/失败 645/254；HTTP记录 200:645，503:1，None:207；失败无发送事件 3。最近小时原因 HTTP_LISTING_FAILED_RSS_FALLBACK:97；最新形状 TimeoutError:; rss:RuntimeError:defense_all_endpoints_failed:TimeoutError: | P1：五个HTTP分类都能取得有效列表，超时比例不同，另有1次503；fallback错误保留。按分类隔离与分lane提交，不能据某一分支失败宣称全站403。 | 保持60s |
| barrons_ticker_news | production | 3530/54 → 3459/41 | 周期成功/失败 10446/99；HTTP记录 200:10476，403:53，None:10；失败无发送事件 65。最近小时原因 TimeoutError:8，UNPROVEN_LISTING:6，site_queue_timeout:27 | P1：主要间歇准入/控制延期；不等同人工验证码；分开公共HTTP与真实浏览器，检查队列公平/短3秒窗口/BODY争用；身份故障定点恢复，登录权限按BODY单列。 | 保持20s |
| benzinga_news | production | 1976/27 → 1430/120 | 周期成功/失败 4353/334；HTTP记录 200:4353，None:14；失败无发送事件 320。最近小时原因 TimeoutError:126 | P0：已确认大部分失败缺少发送证据；准入/连接阶段老化待分层证实；修共享准入公平/取消守恒与容量等待记账，PoolTimeout 定点池治理；失败不得一概重建连接或当成供应商限流。 | 保持15s |
| bloomberg_massive | test | 831/2 → 450/71 | 周期成功/失败 1402/206；HTTP记录 200:570，304:840，None:42；失败无发送事件 156。最近小时原因 TimeoutError:72 | P1：试验源：准入/请求或回退超时，未证明供应商拒绝；先纳入公平容量、取消回收与坏池治理；delivery_mode=test 不得长期挤占正式源全部预算；单独恢复各publisher。 | 保持30s |
| breakingdefense_space | production | 180/0 → 176/4 | 周期成功/失败 524/16；HTTP记录 200:38，304:486，None:12；失败无发送事件 4。最近小时原因 RuntimeError:4；最新形状 RuntimeError:defense_all_endpoints_failed:TimeoutError: | P2：间歇失败；现有回执不能唯一定位，需复用共享层分阶段证据；不另写整站重构；在同一源/同一feed验收共享层修复，再仅修仍复现的超时或解析形状。 | 保持20s |
| businesswire_massive | test | 111/9 → 0/119 | 周期成功/失败 0/359；HTTP记录 无记录；失败无发送事件 359。最近小时原因 HTTP_LISTING_FAILED_RSS_FALLBACK:119；最新形状 TimeoutError:;rss:total_timeout | P1：试验源：准入/请求或回退超时，未证明供应商拒绝；先纳入公平容量、取消回收与坏池治理；delivery_mode=test 不得长期挤占正式源全部预算；单独恢复各publisher。 | 保持30s |
| calcalist_search | production | 40/165 → 22/205 | 周期成功/失败 68/584；HTTP记录 200:159，403:274，None:267；失败无发送事件 107。最近小时原因 RuntimeError:194，TimeoutError:11；最新形状 RuntimeError:defense_all_endpoints_failed:TimeoutError: | P1：搜索JSON近3h274次403、68次有效200、261次无状态；cse.js另有91次200。拒绝与本地超时并存；只对可证实token失效刷新，保留60s，明确拒绝时按入口恢复或更换许可查询途径。 | 保持60s |
| canary_media | production | 172/7 → 0/65 | 周期成功/失败 0/218；HTTP记录 None:202；失败无发送事件 0。最近小时原因 HTTP_LISTING_FAILED_RSS_FALLBACK:62，TimeoutError:3 | P1：持续HTTP_LISTING_FAILED_RSS_FALLBACK；近3h202个发送事件均无响应状态，未发现首200。分解连接后/响应头/读体与fallback期限，先公共层，再按现有可用lane交付，不能套用WP第二页根因。 | 保持20s |
| corporate_ir_press_releases&#124;IR:be.press | production | 120/0 → 109/10 | 周期成功/失败 326/33；HTTP记录 304:427，429:246，None:26；失败无发送事件 7。最近小时原因 HTTP_RSS_BOTH_FAILED:10；最新形状 http:TimeoutError:; rss:TimeoutError: | P1：HTTP/RSS双lane共同失败才阻塞；RSS已有429，健康HTTP可覆盖但自身超时仍使发现中断。按lane遵循Retry-After，修HTTP准入/取消及分支证据；不重跑/重置另一lane。 | 保持30s；真实429仅对受限lane按Retry-After退避 |
| corporate_ir_press_releases&#124;IR:ceg.press | production | 180/0 → 121/59 | 周期成功/失败 422/118；HTTP记录 200:422，None:78；失败无发送事件 40。最近小时原因 ConnectTimeout:2，TimeoutError:57 | P1：以TimeoutError为主，近3h失败118次，其中40次无发送事件；部分另有ConnectTimeout。等待与联网失败混合，未证实站点限流；先修IR host/client生命周期与准入公平，残留失败按phase定点处理。 | 保持20s |
| corporate_ir_press_releases&#124;IR:cohr.corporate | production | 180/0 → 178/2 | 周期成功/失败 525/15；HTTP记录 200:34，304:491，None:7；失败无发送事件 8。最近小时原因 TimeoutError:2 | P1：以TimeoutError为主，近3h失败15次，其中8次无发送事件；部分另有ConnectTimeout。等待与联网失败混合，未证实站点限流；先修IR host/client生命周期与准入公平，残留失败按phase定点处理。 | 保持20s |
| corporate_ir_press_releases&#124;IR:cohr.financial | production | 180/0 → 170/9 | 周期成功/失败 513/26；HTTP记录 200:32，304:481，None:14；失败无发送事件 12。最近小时原因 TimeoutError:9 | P1：以TimeoutError为主，近3h失败26次，其中12次无发送事件；部分另有ConnectTimeout。等待与联网失败混合，未证实站点限流；先修IR host/client生命周期与准入公平，残留失败按phase定点处理。 | 保持20s |
| corporate_ir_press_releases&#124;IR:intc.press | production | 180/0 → 159/20 | 周期成功/失败 475/63；HTTP记录 200:939，None:3；失败无发送事件 59。最近小时原因 HTTP_RSS_BOTH_FAILED:20；最新形状 http:TimeoutError:; rss:TimeoutError: | P1：HTTP/RSS双lane超时；近3h 63次失败中59次无发送事件，成功响应为200。优先共同准入/取消回收，保留两lane独立checkpoint；不因双lane配置存在就视作可接替。 | 保持20s |
| corporate_ir_press_releases&#124;IR:mu.ir | production | 120/0 → 101/19 | 周期成功/失败 309/51；HTTP记录 304:394，429:249，None:34；失败无发送事件 15。最近小时原因 HTTP_RSS_BOTH_FAILED:19；最新形状 http:TimeoutError:; rss:TimeoutError: | P1：HTTP/RSS双lane共同失败才阻塞；RSS已有429，健康HTTP可覆盖但自身超时仍使发现中断。按lane遵循Retry-After，修HTTP准入/取消及分支证据；不重跑/重置另一lane。 | 保持20s；真实429仅对受限lane按Retry-After退避 |
| corporate_ir_press_releases&#124;IR:mu.newsroom | production | 179/1 → 169/10 | 周期成功/失败 517/22；HTTP记录 200:517，None:4；失败无发送事件 18。最近小时原因 TimeoutError:10 | P1：以TimeoutError为主，近3h失败22次，其中18次无发送事件；部分另有ConnectTimeout。等待与联网失败混合，未证实站点限流；先修IR host/client生命周期与准入公平，残留失败按phase定点处理。 | 保持20s |
| corporate_ir_press_releases&#124;IR:nbis.press | production | 178/2 → 0/179 | 周期成功/失败 0/539；HTTP记录 无记录；失败无发送事件 539。最近小时原因 PoolTimeout:20，TimeoutError:159 | P0：已确认连接池等待失败；未定位到具体遗失/占用连接；corporate_ir/http.py 单 host HTTPX 池增加 pending/active/phase；取消释放回归，证实坏池后只重建该 host client，保留 checkpoint。 | 保持20s |
| corporate_ir_press_releases&#124;IR:onds.press | production | 174/6 → 151/28 | 周期成功/失败 461/78；HTTP记录 200:461，None:6；失败无发送事件 72。最近小时原因 ConnectTimeout:1，TimeoutError:27 | P1：以TimeoutError为主，近3h失败78次，其中72次无发送事件；部分另有ConnectTimeout。等待与联网失败混合，未证实站点限流；先修IR host/client生命周期与准入公平，残留失败按phase定点处理。 | 保持20s |
| corporate_ir_press_releases&#124;IR:rklb.ir | production | 180/0 → 177/2 | 周期成功/失败 532/7；HTTP记录 200:36，304:1024，None:6；失败无发送事件 5。最近小时原因 HTTP_RSS_BOTH_FAILED:2；最新形状 http:TimeoutError:; rss:TimeoutError: | P1：HTTP/RSS双lane少量超时，绝大多数304/200有效；保留race现状，复用准入与截止修复，不为偶发故障迁移浏览器。 | 保持20s |
| corporate_ir_press_releases&#124;IR:rklb.updates | production | 178/2 → 99/80 | 周期成功/失败 320/218；HTTP记录 200:2，304:444，None:88；失败无发送事件 4。最近小时原因 TimeoutError:76，URLError:4 | P1：TimeoutError/URLError且urllib读线程参与；取消后线程仍须排空，不能提前释放许可造成假容量。记录线程排队与实际读体阶段，修单入口host资源，不牵连rklb.ir。 | 保持20s |
| corporate_ir_press_releases&#124;IR:snow.press | production | 180/0 → 156/24 | 周期成功/失败 467/73；HTTP记录 200:467，None:31；失败无发送事件 42。最近小时原因 ConnectTimeout:1，TimeoutError:23 | P1：以TimeoutError为主，近3h失败73次，其中42次无发送事件；部分另有ConnectTimeout。等待与联网失败混合，未证实站点限流；先修IR host/client生命周期与准入公平，残留失败按phase定点处理。 | 保持20s |
| crn_cloud_security_datacenter | production | 540/0 → 431/54 | 周期成功/失败 1334/143；HTTP记录 200:5，304:1329，None:75；失败无发送事件 0。最近小时原因 HTTP_LISTING_FAILED_RSS_FALLBACK:54 | P2：间歇失败；现有回执不能唯一定位，需复用共享层分阶段证据；不另写整站重构；在同一源/同一feed验收共享层修复，再仅修仍复现的超时或解析形状。 | 保持20s |
| data_center_frontier | production | 178/1 → 137/21 | 周期成功/失败 386/76；HTTP记录 200:386，None:61；失败无发送事件 0。最近小时原因 HTTP_LISTING_FAILED_RSS_FALLBACK:21 | P2：间歇失败；现有回执不能唯一定位，需复用共享层分阶段证据；不另写整站重构；在同一源/同一feed验收共享层修复，再仅修仍复现的超时或解析形状。 | 保持20s |
| defensescoop_drones | production | 356/2 → 291/34 | 周期成功/失败 873/103；HTTP记录 200:873，403:1，None:71；失败无发送事件 4。最近小时原因 HTTP_LISTING_FAILED_RSS_FALLBACK:34 | P2：间歇失败；现有回执不能唯一定位，需复用共享层分阶段证据；不另写整站重构；在同一源/同一feed验收共享层修复，再仅修仍复现的超时或解析形状。 | 保持20s |
| defensescoop_space | production | 169/11 → 167/13 | 周期成功/失败 502/38；HTTP记录 200:32，304:470；失败无发送事件 6。最近小时原因 UNPROVEN_LISTING:13；最新形状 RuntimeError:defense_all_endpoints_failed:TimeoutError: | P2：当前小时主要UNPROVEN_LISTING，而近3h已有502次有效304/200；最新另见endpoint TimeoutError。检查backoff/304基线如何丢失采集证明，不仅重试网络，不能把未知当成功。 | 保持20s |
| digitimes_tw_rss | production | 179/1 → 80/99 | 周期成功/失败 281/256；HTTP记录 200:281，None:130；失败无发送事件 126。最近小时原因 TimeoutError:99 | P2：近3h256次失败，126次无发送事件；其余记录未获状态；281次200对应成功。没有401/403/付费门槛证据。修直接HTTP的准入/超时与取消释放，保留“仅免费正文”业务规则。 | 保持20s |
| dod_releases | production | 178/1 → 171/4 | 周期成功/失败 507/16；HTTP记录 200:507，None:6；失败无发送事件 4。最近小时原因 HTTP_LISTING_FAILED_RSS_FALLBACK:4 | P2：间歇失败；现有回执不能唯一定位，需复用共享层分阶段证据；不另写整站重构；在同一源/同一feed验收共享层修复，再仅修仍复现的超时或解析形状。 | 保持20s |
| enterprise_apps_official_rss | production | 835/4 → 555/174 | 周期成功/失败 1735/478；HTTP记录 200:1735，None:357；失败无发送事件 0。最近小时原因 HTTP_LISTING_FAILED_RSS_FALLBACK:173，TimeoutError:1 | P2：按入口拆分：Workday响应前等待153次，SAP153次；Salesforce三API分别8/30/13次。共享层先修，Workday30s及SAP/Salesforce20s保留，禁止单入口失败拖累另四入口。 | 保持20/30s |
| etnews_rss | production | 180/0 → 171/4 | 周期成功/失败 505/17；HTTP记录 200:505，None:3；失败无发送事件 6。最近小时原因 HTTP_LISTING_FAILED_RSS_FALLBACK:4；最新形状 TimeoutError:; rss:TimeoutError: | P2：间歇失败；现有回执不能唯一定位，需复用共享层分阶段证据；不另写整站重构；在同一源/同一feed验收共享层修复，再仅修仍复现的超时或解析形状。 | 保持20s |
| finnhub_company_news | production | 1200/0 → 915/123 | 周期成功/失败 2710/387；HTTP记录 200:2710，None:152；失败无发送事件 235。最近小时原因 ReadError:3，TimeoutError:90，publisher_pre_send_timeout:2，publisher_response_timeout:31 | P0：同时有publisher_response_timeout与外层TimeoutError，另有ReadError/pre-send；近3h387次失败中235次无发送事件。先共享层阶段与资源回收，再修实际读响应失败；不归为单一供应商慢或429。 | 保持30s |
| globenewswire_massive | test | 231/8 → 58/136 | 周期成功/失败 186/403；HTTP记录 200:121，304:67，None:30；失败无发送事件 371。最近小时原因 HTTP_LISTING_FAILED_RSS_FALLBACK:119，TimeoutError:18；最新形状 TimeoutError:;rss:total_timeout | P1：试验源：准入/请求或回退超时，未证明供应商拒绝；先纳入公平容量、取消回收与坏池治理；delivery_mode=test 不得长期挤占正式源全部预算；单独恢复各publisher。 | 保持30s |
| google_news_search_rss | production | 1618/0 → 1350/3 | 周期成功/失败 4154/3；HTTP记录 200:4154，503:1，None:2；失败无发送事件 0。最近小时原因 HTTPStatusError:1，publisher_response_timeout:2 | P2：2次publisher_response_timeout、1次HTTPStatusError；近3h1次503与2次无状态，其他4154次200。保留当前周期，沿既有请求后有界恢复，不能因3次偶发故障重构整个搜索源。 | 保持20s |
| huggingnews_rss | production | 178/1 → 107/36 | 周期成功/失败 375/82；HTTP记录 200:375，None:64；失败无发送事件 1。最近小时原因 HTTP_LISTING_FAILED_RSS_FALLBACK:36 | P1：375次200形成成功；64个发送后无状态、1个失败周期无事件，主要HTTP_LISTING_FAILED_RSS_FALLBACK。响应阶段问题占可见记录，不能只靠重建未发送池解决；修分支截止、失败阶段与有证据的RSS交付。 | 保持20s |
| ibkr_news | production | 1794/0 → 1775/2 | 周期成功/失败 5275/10；HTTP记录 None:5283；失败无发送事件 6。最近小时原因 TimeoutError:1，UNPROVEN_LISTING:1 | P2：最近小时1次TimeoutError、1次UNPROVEN_LISTING，其余1775成功；IBKR trace没有HTTP状态属正常，不能归入HTTP池/网站拒绝。保留20s，检查SDK完成/空结果证明及截止后的旧回调隔离。 | 保持20s |
| investing_ticker_news | production | 1180/19 → 1187/11 | 周期成功/失败 3543/52；HTTP记录 200:3543，403:12；失败无发送事件 52。最近小时原因 site_queue_timeout:11 | P1：主要间歇准入/控制延期；不等同人工验证码；分开公共HTTP与真实浏览器，检查队列公平/短3秒窗口/BODY争用；身份故障定点恢复，登录权限按BODY单列。 | 保持30s |
| investorshub_ticker_news | production | 1182/17 → 1178/20 | 周期成功/失败 3538/60；HTTP记录 200:3540，301:37；失败无发送事件 58。最近小时原因 site_queue_timeout:20 | P1：主要间歇准入/控制延期；不等同人工验证码；分开公共HTTP与真实浏览器，检查队列公平/短3秒窗口/BODY争用；身份故障定点恢复，登录权限按BODY单列。 | 保持30s |
| iridium_press_releases | production | 180/0 → 176/4 | 周期成功/失败 525/15；HTTP记录 200:37，304:488，None:8；失败无发送事件 7。最近小时原因 RuntimeError:4；最新形状 RuntimeError:defense_all_endpoints_failed:TimeoutError: | P2：间歇失败；现有回执不能唯一定位，需复用共享层分阶段证据；不另写整站重构；在同一源/同一feed验收共享层修复，再仅修仍复现的超时或解析形状。 | 保持20s |
| lakehouse_platform_official_rss | production | 359/0 → 354/2 | 周期成功/失败 1059/6；HTTP记录 200:1059，None:6；失败无发送事件 0。最近小时原因 ReadError:2；最新形状 ReadError: | P2：两次ReadError，近3h1059次200、6次未获状态；是读传输间歇失败而非已证实解析/鉴权。保持20s，走既有有界读失败恢复与下一正常周期。 | 保持20s |
| nasa_2026_releases | production | 175/4 → 165/13 | 周期成功/失败 499/38；HTTP记录 200:499，None:11；失败无发送事件 27。最近小时原因 RuntimeError:13；最新形状 RuntimeError:defense_all_endpoints_failed:TimeoutError: | P2：间歇失败；现有回执不能唯一定位，需复用共享层分阶段证据；不另写整站重构；在同一源/同一feed验收共享层修复，再仅修仍复现的超时或解析形状。 | 保持20s |
| nbis_official_actor_rss | production | 357/1 → 275/4 | 周期成功/失败 841/9；HTTP记录 200:1393，None:118；失败无发送事件 0。最近小时原因 HTTP_RSS_BOTH_FAILED:4；最新形状 http:TimeoutError:; rss:TimeoutError: | P2：间歇失败；现有回执不能唯一定位，需复用共享层分阶段证据；不另写整站重构；在同一源/同一feed验收共享层修复，再仅修仍复现的超时或解析形状。 | 保持20s |
| nist_news | production | 180/0 → 172/2 | 周期成功/失败 510/9；HTTP记录 200:40，304:470，None:29；失败无发送事件 0。最近小时原因 publisher_endpoint_backoff:2 | P2：publisher_endpoint_backoff包裹TimeoutError；大多数304有效。恢复时保留已验证baseline并正确安排下一到期，避免退避截止后仍只重复旧错误。 | 保持20s |
| nrc_event_notifications | production | 0/180 → 0/180 | 周期成功/失败 0/540；HTTP记录 200:540；失败无发送事件 0。最近小时原因 publisher_endpoint_backoff:180；最新形状 ValueError:nrc_event_licensee_or_facility_missing | P0：已确认：解析契约过严，单条报文使整日失败；energy_sources.field/parse_events 支持实际表格/标签变体，单事件隔离；保留事件号、日期及正文硬校验；不能用频率修复解析错误。 | 保持20s |
| nsf_news | production | 177/2 → 49/66 | 周期成功/失败 184/184；HTTP记录 200:198，None:148；失败无发送事件 0。最近小时原因 HTTP_LISTING_FAILED_RSS_FALLBACK:66 | P1：近3h198次200/184次采集成功，148次发送后无状态，主要fallback失败。修期限和有效结果提交，单列200未验证样本，不把外层TimeoutError当根因。 | 保持20s |
| power_magazine_energy | production | 1467/27 → 366/467 | 周期成功/失败 1131/1371；HTTP记录 200:1239，None:42；失败无发送事件 1278。最近小时原因 HTTP_RSS_BOTH_FAILED:467；最新形状 http:TimeoutError:; rss:TimeoutError: | P0：近3h1371轮失败中1278轮无发送事件，成功1131；9条ticker绑定对应7个不同FEED，双lane潜在14条请求/全源一轮而非18。修公平准入/取消与按feed去重计费，保留抢先有效结果。 | 保持20s |
| prnewswire_massive | test | 107/12 → 0/119 | 周期成功/失败 0/360；HTTP记录 无记录；失败无发送事件 360。最近小时原因 HTTP_LISTING_FAILED_RSS_FALLBACK:119；最新形状 TimeoutError:;rss:total_timeout | P1：试验源：准入/请求或回退超时，未证明供应商拒绝；先纳入公平容量、取消回收与坏池治理；delivery_mode=test 不得长期挤占正式源全部预算；单独恢复各publisher。 | 保持30s |
| quantum_computing_report | production | 177/2 → 42/69 | 周期成功/失败 138/203；HTTP记录 200:148，None:157；失败无发送事件 0。最近小时原因 HTTP_LISTING_FAILED_RSS_FALLBACK:69 | P1：近3h148次200/138次采集成功、157次发送后无状态；主HTTP与fallback失败混合。先修共享准入与响应完成，再独立保留有证据的fallback结果，保持20s。 | 保持20s |
| quantum_insider | production | 533/0 → 338/7 | 周期成功/失败 1038/21；HTTP记录 200:1358，None:96；失败无发送事件 4。最近小时原因 HTTP_RSS_BOTH_FAILED:7；最新形状 http:TimeoutError:; rss:TimeoutError: | P2：间歇失败；现有回执不能唯一定位，需复用共享层分阶段证据；不另写整站重构；在同一源/同一feed验收共享层修复，再仅修仍复现的超时或解析形状。 | 保持20s |
| quantum_zeitgeist | production | 55/5 → 0/60 | 周期成功/失败 1/179；HTTP记录 200:169，None:11；失败无发送事件 0。最近小时原因 HTTP_LISTING_FAILED_RSS_FALLBACK:60 | P0：已确认首响应200而整轮失败；补充请求为高概率阻断点；拆分首页与补充页成功边界，首页独立解析提交；补充工作持久化重试并验收完整覆盖，不能直接删除第二页/分类页。 | 保持60s |
| register_software_cloud_security | production | 534/0 → 350/7 | 周期成功/失败 1064/19；HTTP记录 200:1421，None:62；失败无发送事件 1。最近小时原因 HTTP_RSS_BOTH_FAILED:7；最新形状 http:TimeoutError:; rss:TimeoutError: | P2：间歇失败；现有回执不能唯一定位，需复用共享层分阶段证据；不另写整站重构；在同一源/同一feed验收共享层修复，再仅修仍复现的超时或解析形状。 | 保持20s |
| sdxcentral_news | production | 118/2 → 116/4 | 周期成功/失败 342/18；HTTP记录 200:342，403:2；失败无发送事件 17。最近小时原因 SiteAccessError:1，publisher_endpoint_backoff:3；最新形状 ConnectTimeout: | P1：SiteAccessError与publisher_endpoint_backoff混合，最近失败形状ConnectTimeout；近3h17/18失败无发送事件。先修准入/连接恢复，只有真实Challenge证据才走人工维护，不能只延长冷却。 | 保持30s |
| sec_edgar | production | 3070/7 → 505/230 | 周期成功/失败 1578/667；HTTP记录 200:1579，None:24；失败无发送事件 642。最近小时原因 TimeoutError:230 | P0：已确认大部分失败缺少发送证据；准入/连接阶段老化待分层证实；修共享准入公平/取消守恒与容量等待记账，PoolTimeout 定点池治理；失败不得一概重建连接或当成供应商限流。 | 保持10s |
| shephard_uncrewed | production | 175/5 → 33/146 | 周期成功/失败 114/424；HTTP记录 200:153，None:725；失败无发送事件 66。最近小时原因 DEFENSE_ENDPOINT_FAILED:38，RuntimeError:140；最新形状 RuntimeError:defense_all_endpoints_failed:TimeoutError:;TimeoutError: | P1：近3h725个发送后无状态、153次200，另66个失败无事件；RuntimeError包裹双endpoint超时。隔离endpoint与有效列表提交，先公共层再定位连接/读体，未见明确403/429证据。 | 保持20s |
| siliconangle_software | production | 536/0 → 352/4 | 周期成功/失败 1085/7；HTTP记录 200:469，304:980，None:52；失败无发送事件 6。最近小时原因 HTTP_RSS_BOTH_FAILED:4；最新形状 http:TimeoutError:; rss:TimeoutError: | P2：间歇失败；现有回执不能唯一定位，需复用共享层分阶段证据；不另写整站重构；在同一源/同一feed验收共享层修复，再仅修仍复现的超时或解析形状。 | 保持20s |
| spacecom_launches | production | 180/0 → 169/10 | 周期成功/失败 507/32；HTTP记录 200:507，None:16；失败无发送事件 16。最近小时原因 RuntimeError:10；最新形状 RuntimeError:defense_all_endpoints_failed:TimeoutError: | P2：间歇失败；现有回执不能唯一定位，需复用共享层分阶段证据；不另写整站重构；在同一源/同一feed验收共享层修复，再仅修仍复现的超时或解析形状。 | 保持20s |
| spacenews_rss | production | 47/13 → 48/12 | 周期成功/失败 145/35；HTTP记录 200:37，304:135，429:8；失败无发送事件 0。最近小时原因 RuntimeError:10，publisher_http_429:2；最新形状 RuntimeError:defense_all_endpoints_failed:TimeoutError: | P1：已确认429，另有超时；优先条件请求/现有获准HTTP发现路径；按 Retry-After 控制被限流 lane，不压低其他源；无证据不能承诺所有请求照发仍永不429。 | 保持60s；真实429仅对受限lane按Retry-After退避 |
| ssc_newsroom | production | 180/0 → 173/6 | 周期成功/失败 522/16；HTTP记录 200:522，None:10；失败无发送事件 6。最近小时原因 RuntimeError:6；最新形状 RuntimeError:defense_all_endpoints_failed:TimeoutError: | P2：间歇失败；现有回执不能唯一定位，需复用共享层分阶段证据；不另写整站重构；在同一源/同一feed验收共享层修复，再仅修仍复现的超时或解析形状。 | 保持20s |
| technews_hardware | production | 334/18 → 0/180 | 周期成功/失败 2/546；HTTP记录 200:492，None:57；失败无发送事件 0。最近小时原因 HTTP_LISTING_FAILED_RSS_FALLBACK:180 | P0：已确认首响应200而整轮失败；补充请求为高概率阻断点；拆分首页与补充页成功边界，首页独立解析提交；补充工作持久化重试并验收完整覆盖，不能直接删除第二页/分类页。 | 保持20s |
| thefly_ticker_news | production | 1061/19 → 1070/6 | 周期成功/失败 3220/16；HTTP记录 200:3220，None:15；失败无发送事件 1。最近小时原因 TimeoutError:6 | P2：间歇失败；现有回执不能唯一定位，需复用共享层分阶段证据；不另写整站重构；在同一源/同一feed验收共享层修复，再仅修仍复现的超时或解析形状。 | 保持30s |
| thelec_semiconductors_rss | production | 178/1 → 159/10 | 周期成功/失败 487/26；HTTP记录 200:487，None:10；失败无发送事件 9。最近小时原因 HTTP_LISTING_FAILED_RSS_FALLBACK:10；最新形状 TimeoutError:; rss:TimeoutError: | P2：间歇失败；现有回执不能唯一定位，需复用共享层分阶段证据；不另写整站重构；在同一源/同一feed验收共享层修复，再仅修仍复现的超时或解析形状。 | 保持20s |
| tmx_newsfile_massive | test | 120/0 → 34/40 | 周期成功/失败 126/103；HTTP记录 200:126，None:26；失败无发送事件 77。最近小时原因 TimeoutError:40 | P1：试验源：准入/请求或回退超时，未证明供应商拒绝；先纳入公平容量、取消回收与坏池治理；delivery_mode=test 不得长期挤占正式源全部预算；单独恢复各publisher。 | 保持30s |
| tomshardware_rss | production | 179/1 → 12/167 | 周期成功/失败 218/321；HTTP记录 200:218，None:97；失败无发送事件 224。最近小时原因 TimeoutError:167 | P0：同一时刻停止成功；跨传输共性故障，非已证实三站同时风控；优先排除共享准入、线程/存储等待和各自池退化；失败阶段确认后只重建坏资源，不重启全部服务。 | 保持20s |
| unmannedairspace_counteruas | production | 357/3 → 282/42 | 周期成功/失败 862/121；HTTP记录 200:532，304:770，None:345；失败无发送事件 23。最近小时原因 HTTP_RSS_BOTH_FAILED:42；最新形状 http:TimeoutError:; rss:RuntimeError:defense_all_endpoints_failed:TimeoutError: | P1：近3h多feed有304/200成功、345个发送后无状态、23个失败无事件。HTTP/RSS双lane超时需按feed与phase治理，不能整站重试或由一条lane错误清另一条checkpoint。 | 保持20s |
| utility_dive | production | 174/3 → 26/76 | 周期成功/失败 92/223；HTTP记录 200:296，None:4；失败无发送事件 0。最近小时原因 HTTP_LISTING_FAILED_RSS_FALLBACK:76 | P1：近3h296个200仅92个验证，223轮失败；没有WordPress强制第二页路径。优先读体完成/解析与验证证据边界，有限失败样本复现后窄修，尚不能唯一指定网站侧根因。 | 保持20s |
| wccftech_hardware | production | 534/0 → 354/1 | 周期成功/失败 1085/3；HTTP记录 200:188，304:1292，None:34；失败无发送事件 3。最近小时原因 HTTP_RSS_BOTH_FAILED:1；最新形状 http:TimeoutError:; rss:TimeoutError: | P2：间歇失败；现有回执不能唯一定位，需复用共享层分阶段证据；不另写整站重构；在同一源/同一feed验收共享层修复，再仅修仍复现的超时或解析形状。 | 保持20s |
| world_nuclear_news | production | 180/0 → 159/10 | 周期成功/失败 483/28；HTTP记录 200:484，None:15；失败无发送事件 0。最近小时原因 HTTP_LISTING_FAILED_RSS_FALLBACK:10 | P2：间歇失败；现有回执不能唯一定位，需复用共享层分阶段证据；不另写整站重构；在同一源/同一feed验收共享层修复，再仅修仍复现的超时或解析形状。 | 保持20s |
| yahoo_finance_news | production | 3592/8 → 3578/19 | 周期成功/失败 10773/24；HTTP记录 200:10773，None:2；失败无发送事件 22。最近小时原因 RuntimeError:5，TimeoutError:13，browser_coverage_pending:1 | P1：近小时13次TimeoutError、5次RuntimeError、1次browser_coverage_pending。HTTP与CDP覆盖状态分别记账，保留30s；coverage待补不得当完整成功，也不因BODY错误冻结有效listing。 | 保持30s |

## 验收与发布边界

1. 先离线回归：许可守恒、公平性、取消、坏池代际、HTTP200主结果＋补充超时、RSS真实成功、NRC异构事件。不得用镜像重启或单次200替代回归。
2. 优先Collector窄发布；仅当浏览器准入模块实际改动才发布SiteAccess，Supervisor/Profile不重建。保留原始Challenge任务和当前出口；本轮不碰Reuters。数据迁移只修明确的坏client/无效retry状态，不清整库、全量checkpoint或消息去重键。
3. 发布后先30分钟低频自然业务观察，再至少跨越此前复发点：24小时或大于故障前请求次数的2倍。每5–15分钟读增量累计账（远端聚合后仅取脱敏小结果；全窗重扫留到阶段截止）；不能只看最近100条，也不额外制造网站请求。
4. 验收必须按ticker×QUERY/FEED列实际成功间隔P50/P95/最大值、准入延期、网络失败、valid listing与真实发现/正文证据；成功率分母排除未到期/在途，PARTIAL与完整覆盖分别列。只读“最新绿灯”不够。
5. 目标：NRC/坏池等连续故障消失；普通正式入口完整周期有效率目标≥99.5%，被外部明确拒绝单列；没有周期性许可损失、client/线程/target/WAL无界增长。10/15/20/30秒是目标，不用大幅延后成功后再填“已调度”美化。
6. 回调频率为有证据的最后手段：公共层修复和完整周期容量测量后，仍出现明确429/Retry-After或可证明单站容量低于需求，才提出该lane/入口的临时方案。没有证据不降低整批频率；不能保证外部拒绝在不改变请求行为时必定可修。

## 证据缺口与资源风险

- 主机快照：CPU PSI some avg60约48.5%，I/O some avg60约21.4%，可用内存约5.56GiB，Swap几乎用尽；磁盘可用约31.41GiB。和发布时相比余量减少，但单次PSI不能当作18小时分位或证明某一role独占。acquisition文件仍约13.18GiB，不足以把约25GB总空间减少全归因消息账本。不得本轮顺手删数据。
- 生产未保存这些失败页面的完整HTML、每一个准入层的等待快照、坏池活动租约与取消堆栈。因此NRC字段兼容的具体HTML分支、Canary发出后停在哪一段、TechNews补充页与准入各占多大比例，都必须在实施阶段用受控fixture/有限失败样本进一步证明；不能声称已经穷尽。
- 这份方案确认了可修的内部覆盖缺口与多个失败阶段，保留尚待验证项；不是承诺所有外部403/429或订阅权限都能靠程序消除。

证据：[`audit.json`](../../eval/source_stability_audit_20261111/audit.json)、[`details.json`](../../eval/source_stability_audit_20261111/details.json)、[`逐源CSV`](../../eval/source_stability_audit_20261111/per-source-diagnosis.csv)。包含完整窗口聚合、最新失败形状、checkpoint摘要、最近3小时有限日志形状；排除Reuters。
