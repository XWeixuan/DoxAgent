# 消息源提频后稳定性修复：实现与验收（2026-11-11）

依据 `dev_plan/workflow_v2/message_sources_post_cadence_stability_root_cause_repair_plan_20261111.md`。本轮不降低任何消息源/ticker/FEED的配置间隔，不调整硬并发上限，不重建Chrome/Profile，不修改Reuters专属代码、配置、出口或验证码现场。

## 本轮确认与实现

1. **公共准入活锁已经离线复现**：120个请求以相反顺序竞争同一组Semaphore，旧`joint_permits`在2秒截止前不能完成，取消后许可数仍归还；这是预约转交不前进，不能靠“没泄漏许可”排除。改为event-loop内按到达顺序选择资源齐备的请求，一次取得全部许可。阻塞host不占role容量，不阻塞其他可执行host。取消覆盖排队中、刚授予未恢复、执行中；保留source pacing与shrink debt。无新增进程/服务/表/锁文件。正常释放立即通知；兼容旧Semaphore使用者仅共用一个50ms内存唤醒timer，队列空时停止，不是网站/DB轮询。
2. **IR host生命周期恢复**：host/global联合准入；仅自有HTTPX client发生PoolTimeout时退休该host的generation，旧请求退出后关闭，下一正常业务周期创建新client。容量排队、普通连接超时、403/429不触发池重建；保留其他host与checkpoint；原urllib线程取消排空机制保留。
3. **NRC准确根因**：通过现有已配置传输取得200的真实报告。事件58409为合法Part 21，`Rep Org: ABB Inc`而Licensee为空且无Facility，旧程序使同页三条事件全失败。保留reporting_org独立语义，不能伪装Licensee或当反应堆查Operator；主体、日期、正文仍校验。单条异常隔离并保留最多8条事件号/错误原因；全页不可用仍失败。旧字段错误的retry仅在该NRC入口释放，未清去重/历史。回归fixture裁成报告正文，不保存整站资源。
4. **TechNews/Quantum首页与补充页解耦**：首页通过缓存新鲜度、传输和parser校验后先提交；分类archive/offset100独立完成，失败则保留`PARTIAL`和checkpoint内`supplement_pending/error`，下个既定轮询继续补充。保留原覆盖范围与全部原有缓存/失败校验，不删第二页。补充失败不再迫使重做已经验证的首页；不能把首页成功称为完整覆盖。Transport证明现在仅绑定本次capture的request_key，避免并行入口互相借用未解析的200。
5. **失败与可用回退分开**：保留主HTTP失败和原有失败状态，不把RSS回退称作爬虫恢复。只有RSS实际传输且delegate解析成功，才保留独立`rss_fallback`证明和`rss_fallback_available`；正常消息仍沿原Capture/去重/BODY路径交付。
6. **轻量等待定位及维护隔离**：复用POLL_TIMINGS/已有unit状态和周期记录，仅保存少量阶段/累计毫秒，失败周期带阶段，无新增采样写入或抓包。Collector定期归档独占一个串行executor，避免与正常解析/提交争用默认executor；维持现有维护频率、批量与保留策略，未增加清理量。

## 最小变更边界

主要修改既有`publisher_http`、`poll_trace`、`corporate_ir/http`、`public_listing`、`energy_sources`及Collector少量状态/maintenance代码。现有schema对外接口不扩张；新增信息为已有JSON中的可选字段。原有效的取消排空、坏curl句柄清理、真实发送证明、缓存新鲜度校验、去重与正文能力保留。

P1逐源判断以原排查表为索引：先在新版本自然流量中复核。如果共享层修复后仍失败，再按真实阶段窄修。Calcalist新鲜token403/SpaceNews或IR的429不会因内部修复自动变成成功；只对明确受限lane遵循服务端要求，不降整批频率。浏览器队列延期与BODY鉴权分列，不自动登录或换身份掩盖。

## 发布和验收记录

待发布及自然业务观察后追加精确SHA、容器、窗口与逐源结果。已有代码回归包含：相反锁序、跨host进度、取消后守恒、source pacing/缩容、IR坏池定点恢复、真实NRC Part 21/单事件隔离、首页先提交/第二页失败及次轮补齐、原有缓存与RSS失败状态契约。验收不能把重启后一次成功算长期稳定；至少30分钟自然业务窗口，之后仍需跨越原约18小时复发点的老化证据。

生产受控取证仅NRC：宿主机直连1次403，现有已配置传输1次200；没有打开Chrome、重建Profile或触发验证码测试。后续验收复用自然请求，以有限窗口聚合为主，不反复读取全历史。

本地组合回归：280 passed（247.75秒），仅Windows curl_cffi现有Selector兼容提示；无失败。另已验证反序竞争旧实现失败、新实现通过；代码compileall与git diff --check通过。

## 首窗后追加的第二层修复

首版约4分钟自然窗口只有2094成功/561失败，不能称为已恢复。NRC、SEC、Benzinga有成功，但Army、Tom’s、NBIS等仍大量失败。新阶段账表明剩余问题含明显准入等待。

受控新客户端对照（各一次）：Army经原既定route返回200约9.066s、NBIS原入口200约0.441s、Tom’s原feed200约0.967s；不更改出口。这说明“同一时刻网站/route必然不可用”并不成立。

代码进一步确认：即使`PublisherClients`的8秒执行计时从准入后开始，外层`public_listing`12秒、Technology15秒、Defense/IR12秒仍提前开始，排队会吃掉整个网络执行预算。因此常驻进程队列中等数秒后，即使正常请求本可成功，也提前取消，继而把普通等待当网络失败重试；仅修FIFO不足。

第二层修改采用同一轻量`network_timeout`上下文：只在联合准入等待期间暂停这些局部**网络阶段**计时，授予后恢复剩余网络预算，重试仍共用同一网络预算。Collector原45/30秒业务总截止、请求8秒执行截止、站点并发和既定周期全部保留。没有把全部超时改成120秒或放大并发。确定性回归同时验证：排队不耗网络预算、真实联网仍受限、父业务截止仍可取消排队且容量归还。

另外核实SiteAccess和BODY运行中都加载旧`held = blocked`准入算法，故将同一修复同步这两个实际消费者；先查维护会话仅CLOSED/RELEASED，无活动Viewer。Supervisor未重建。首版Collector窗口单独保留，后续以最终运行版本重新验收，不混算完整30分钟。

## RSS／Token／限流分支收尾

二版自然流量窗口（UTC15:54:46—15:58:30）出现2065成功/198失败，Army两入口、NBIS、NRC、TechNews、Quantum、Calcalist、Utility均有真实成功；TechNews8轮、Quantum3轮各自有两页独立传输证明。仍不能称全量验收通过：Tom’s11次仍全是准入阶段超时。

继续复核发现`SharedFeedAdapter`、显式RSS route和试验Newswire尚保留旧绝对网络超时，已统一使用同一网络阶段时钟；保留两次尝试的总网络预算与父业务截止。Tom’s不换站点、不换路由、不降低周期。

Calcalist受控新会话bootstrap200＋真实搜索200，不能再把当前失败归为永久访问拒绝。CSE缓存原来只按TTL失效，与既有Publisher client退休/重建脱节；增加仅对实际transport generation变化的失效和一次有界刷新，同一代正常复用、不增加逐轮bootstrap，明确新鲜同代403仍失败。

Collector简化IR路径原来只抛出`IR_HTTP_429`，未将Retry-After持久化到race分支。现在复用现有checkpoint `retry_at`：只暂停被限流lane；健康HTTP保持原发现周期，不把RSS未请求／退避伪装成一次网络成功。

收尾回归120 passed/1 skipped（现有平台分支），另72个共享race/队列/恢复回归通过。两个历史测试mock分别跟上现有PublisherHttpx接口及Scheduler构造器_wake不变量，未为测试改变生产Scheduler。最终发布与30分钟自然窗口另记，先前失败窗口保留。

## 联合准入贯通调用方（最终收尾）

第三版5分钟正式源有效率94.911%，仍未达到稳定目标。失败集中于多feed race；已保存的状态显示Source等待约19–28秒、HTTP准入约21–32秒，实际成功传输可以仅245–1320毫秒。这不是“网络一定要30秒”的证据，而是外层先占Source许可再排全局队列、race15/30秒再提前取消造成的串联排队。

最终修复不再逐站扩大deadline：HTTP调用方统一使用已有`source_http_scope`，Source/host/global在实际传输点联合授予；浏览器transport继续交由原SiteAccess管理。`network_wait`复用于实际Source等待与联合等待；race、Newswire listing也使用同一网络阶段时钟，父Collector45/30秒业务截止保持不变。Newswire已有两个工作槽保留，槽位等待同样不算网络执行。没有额外扩大硬并发或降低频率。

这是“短网络截止→正常等待被取消→反复冷连接/重试→服务时间与排队增大→更多取消”的内部反馈链；坏pool、错误解析和单站真实拒绝是可并存的独立问题，不能把所有失败只归为一种。

271 passed/1 skipped的组合回归覆盖本次贯通，包括真实Source许可在等待全局容量时未被提前占住、Source等待不耗局部网络预算、父截止仍有效。前一版376 passed/1 skipped结果保留在regression.xml，新一版r4-regression.xml独立保存，不以旧版回归冒充最终验证。
## 补充页总截止与最后两个串联准入点

R4完整前15分钟正式有效采集周期8121/8236（98.604%），但不能达到全量稳定验收：试验源仅15/183；TechNews/Quantum存在40秒以上准入，补充页还在等待时45秒父截止先取消整个返回，已经提交的首页仍被标整轮失败。R4窗口单独保留。

最后修正：补充页使用最多12秒、且不越过本轮第40秒的墙钟预算，给既有45秒总截止保留返回空间；超时明确保留supplement_pending/error、PARTIAL及首页证据，不伪称补充完成，下轮继续。Google Search RSS原生HTTP由Source与global串联改联合准入。Newswire两工作槽从“先占再等Source/global”改为传输点一并取得，两个硬槽不变；只是复用ContextVar带入现有联合准入器，没有新增队列/进程或放大容量。保留已有HTTP响应、parser、时间戳和传输证明规则。
补充兼容性回归还发现：旧Scheduler的PollState.last_stage_timings只接受整数，本轮phase字符串不能混入该公共字典。已把phase移到Collector独立的既有JSON状态，公共计时仅整数；20 ticker＋阻塞sibling/intake回归16项通过。不会扩张旧Bus schema或为日志重建Bus。
## 自然窗口发现并复现的许可泄漏（R6）

R5前4分17秒正式1751/1949（89.841%），试验35/54（64.815%）；不能因为试验改善就交付。POWER七个feed无发送、各45秒准入耗尽，与其他入口反复排队并存。没有据此盲目扩大总容量。

进一步的确定性复现：父任务进入network_timeout后创建共享子任务；子任务在joint_permits等待，父任务先结束其timeout上下文；之后释放容量。164883cc2中子任务退出network_wait时抛出`Cannot change state of finished Timeout`，而许可仍为0；独立模拟等待边界异常也泄漏已获许可。两项旧版本均失败。这说明“只测HTTP body内取消后归还”覆盖不足；POWER的shield共享子任务是实际可触发的路径，不能单纯把现象归为容量不足。

修复仍在原架构内：network_timeout记录所属Task，只允许本任务暂停/恢复本任务的网络时钟；joint_permits最外层finally覆盖从排队、授予到退出等待边界及执行的完整生命周期，任何边界异常都归还。POWER物理共享任务使用自身40秒业务截止，调用方等待shield时仅暂停自己的局部网络时钟，45秒总截止保持；没有新后台组件。SpaceNews的stdlib改为调用方先联合准入，然后复用既有StorageLane._execute的线程取消排空，不能把排队本身shield成不可取消的后台工作。Source/global硬容量、所有轮询配置不变。

该缺陷也可能使早期窗口渐进退化，因此早期98.6%不算最终稳定证明；最终修复后重新定义自然验收窗口。新增回归同时覆盖父时钟退出后的共享子任务与获准后等待边界异常，避免未来再以“取消测试通过”漏掉同类泄漏。
