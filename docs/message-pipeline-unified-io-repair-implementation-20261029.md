# 统一I/O与业务性能修复执行记录

执行依据：[统一方案](message-pipeline-unified-io-and-business-performance-repair-plan-20261029.md)。本文件逐批记录实际实现、测试、正式发布与90分钟观测，不将开发测试等同业务达标。

## 实施进度

|批次|已实施的正常路径减法|验证状态|
|---|---|---|
|A|Worker薄索引及合并READY/BUILDING；Site非终态索引；删除匿名页Registry登记及逐publisher失效收尾；旧类别维护退出及重复backlog删除|Worker/Site大历史与索引回归通过；生产READY覆盖索引读取0.058ms|
|B|Read先限量退出ID/游标；DELETING声明后锁外文件工作；禁止Native复活删除中对象；复用时间floor；Acquisition RO连接/引用索引/有限schedule；Native不可变缓存取消逐chunk复核|Read/采集/GC并发、引用竞争与重启恢复回归通过|
|C|独立控制watch和维护；无5秒年龄撤销；HTTP联合容量及独立客户端关闭；Browser外层重复许可删除；Finnhub默认容量更新|113项采集/身份相关回归通过，2项匿名立即退出旧断言按新2秒burst合同更新|
|D|独立native CDP证据，控制故障不直接标坏/销毁健康Chrome；按版本发送worker配置；freeze失败保留暖页；VNC按需；非业务后台关闭|已正式发布，实测纠正Ukm启动回归及snapshot复用后的隐式串行|
|E|正文获取轮次、provider延期、首次结果字段；304复用digest；同篇多ticker一次物化；结果spool/ARTICLE_ONCE保留|正文/交付、复用/更新/首次失败/延期回归通过；90分钟观察完成，首次56/70，未证明稳定>80%|
|F|Owner首次结果/decision与CDC、Projector、API可选initial指标契约；组合Review；必需TAIL纳入周期，checkpoint变化触发续页计划|契约及组合测试通过，正式容器已发布；90分钟完整观察完成，全部覆盖72.72%|

## 迁移与业务变化

- 现有SQLite表增加索引、首次结果和获取轮次字段，不增加任务库、全局门禁或恢复协调器。旧首态缺证据保留NULL/UNKNOWN。
- 临时开关仍只覆盖Message Bus；控制读取失败沿用最近明确授权，已知期限本地生效。手动关闭以applied revision确认，控制不可达时不虚报生效。
- 匿名公开publisher保留隔离Context，空闲最多2秒退出；已验证受保护身份、Profile、Chrome启动路径和Reuters不冻结约束保留。
- 普通首次失败最多一次短重试；真实provider延期保留not_before，不拿20秒执行预算裁掉60秒能力等待。完整正文更新仍30分钟/24小时，未变结果不重复分发。
- W1/W2/W3容量及模型不变；其它对话交易、退出策略、提示词和前端工作已核对；存在下述交易测试冲突，保留工作树，未夹入本轮消息系统发布，不覆盖或丢弃。

## 生产基线

北京时间21:27只读核对：8核16GB，uptime约3小时34分钟，load约1.5–1.9，可用内存约11.6GiB、Swap存量约593MiB。Collector、Bus、BODY、Site、Supervisor保持停止；Worker、Scheduler、交易、API/Projector/Maintenance在运行。此低负载状态不能当作全负载收益。

## 发布与观测

完整观测窗口：2026-10-29 14:30:32.571819Z–16:00:32.571819Z（北京时间22:30:32–次日00:00:32）。正式Collector实际启动为起点，含冷启动、失败、修复和正式容器替换。稳态子窗口另列，不能替代整体。原始证据、测试XML及资源快照在`eval/unified-io-repair-20261029/`。

## 开发阶段整体Review

- 删除的是宽历史读取、匿名逐页写/失效扫描、每轮backlog复查、相同正文跨ticker物化、每次chunk签名复核、请求内HTTP close及配置刷新串行维护；未添加新的全局队列、门禁或恢复台账。
- GC先取得DELETING再锁外搬文件；新引用被明确挡在本地重建边界，Projector保留原坐标重试，不能导致BODY重新下载。已验证“引用先赢”和“删除先赢”、held首批越过与释放后继续回收。
- 匿名cleanup不在全局lock中等待；清理已开始则只等待原owner，未确认退出不创建第二个runtime。Protected身份仍独立driver；正常Reuters页面不freeze；冻结优化失败不强制冷建。
- 配置watch独立于完整compile和维护；明确关闭/到期仍在实际发网前判断，配置健康摘要30秒更新，避免删除高频写后旧healthcheck误判。
- 修正未变配置快路跳过checkpoint续页计划的问题。必要TAIL从新增/迁移生效边界进入分母，不倒填旧证据；旧历史仍保留原口径缺口。
- 304只有在已有同表示FULL缓存下才能复用；普通重试按获取轮次，wrapper/跳转不算不同重试；provider等待不消耗取网轮。首态不被recheck覆盖，无法还原的旧数据不填当前FULL。
- 已有20ticker共享一次BODY/ARTICLE_ONCE测试通过；这不是20ticker真实30秒浏览器容量验收。
- 本地另有未提交的交易/退出策略、前端详情及提示词改动。整合核对发现其删除ENTRY五分钟规则与现有交易测试冲突（70通过、3平台跳过、1失败）；不覆盖该工作，也不将其夹入消息系统发布。该冲突不以本轮消息优化为名修改交易语义。

测试证据：`eval/unified-io-repair-20261029/`。已完成采集/交付148项、正文/Read/API契约114项、Site/Worker/GC66项、专项成本与故障10项、前端TypeScript及67项单元测试；首轮失败及修正保留在执行历史。旧“必须后台探针解锁”的测试已按当前真实业务半开语义修正，没有改生产health逻辑来迎合旧断言。

发布前追加核对：纯已提交归档运行56项关键回归通过，确认未依赖其它会话的交易工作树。另补采集统计的范围分支读取和薄schedule部分索引，避免OR条件把48小时周期历史拖入30分钟UI窗口；探针只取窗口内周期与生效schedule，并输出首态。专用IR/SEC等读取也计获取轮，失败后的既定30分钟内容更新仍保留，不把有限首次重试变成永久取消更新。

Finnhub实际只读探针HTTP200，响应声明`x-ratelimit-limit=60`、remaining=59，6237字节。迁移只解除“上一响应结束才允许下一请求”，保留原最小发网间隔；20ticker/30秒该端点为40次/分钟，其它同账号用量仍需纳入余量，不能从一次探针保证全账号持续容量。

## 生产观察中的启动回归修复

首轮正式5fc746a6已于14:30:32Z启动Collector；14:37–14:39Z追踪ensure失败至Chrome自身退出。同镜像、同UID、临时隔离目录与Xvfb对照：只禁用domain-reliability存活、只禁用Ukm返回-11、只禁用Segmentation/Optimization存活；移除Ukm禁用参数，External及Managed一致修正。未清理任何真实身份目录。UKM后台I/O治理不能据此视为已完成，后续以真实I/O差分判断其剩余影响。首轮失败及修复停顿保留整个90分钟窗口。

后续实测发现HTTP联合许可在多人排队时会反复转让而无请求进入；8c964891修正为唤醒后立即保留该许可检查其余容量，不持许可await其它容量，40并发及100请求/6publisher/4source均能完成且容量归零恢复。Registry惰性publisher/profile写入不得进入只读scope，e41694a2补齐所有相关入口。Compose旧ENV覆盖镜像commit已在正式release契约修正，旧ENV不能作为实际版本依据。进一步删除legacy迁移完成后的旧BODY JSON检查；Bloom两个非press RSS同出口curl200/RSS有效、httpx403，固定低成本curl路线，明确配置出口不变。

## 生产阶段整体Review

- 182项冻结提交组合回归通过；同身份双命令实际重叠13项、市场/SEC68项另行通过。开发阶段暴露的NameError/旧测试断言等均在本地修正，没有带入对应发布。
- 发现并处理了三项本轮优化引入的回归：Ukm禁用使批准版Chrome SIGSEGV；联合许可转让形成无进展循环；snapshot不再每命令发送后，worker读取并发默认1使实际串行。均保留生产失败记录，补故障合同测试。
- 发现并修复前轮减法遗留的FormalProjectors serving_skipped契约遗漏（c770409d于10月29日07:10引入SourceOutbox瘦化），约700个KeyError缺口经原有repair归零；54个缺provenance的历史问题没有宽松放行。
- HTTP仍有真实provider慢请求，不能把7.76秒实测响应归为DB问题，也不盲换curl（同端点curl8秒仍超时）。修正的是排队提前烧掉网络预算，整体45秒与网络15秒都没有增加。
- Bloom非press两RSS的curl路线已经实测200/RSS有效；补实际完成证据后才能计有效取网。Benzinga双查询用解析器绑定实际ID，未给无请求轮伪造时间。
- Globe HEAD的内部head_at周期与Collector相位冲突导致空转；正式HEAD改由Collector单一节奏触发，TAIL义务保留。SEC正式HEAD去掉2秒结果TTL捷径，继续条件请求，不变更申报/表格/正文质量合同。
- 30秒eBPF窗口（15:07:52Z起）只统计实际提交BIO并按首vector归文件：Site约0.23MiB、Supervisor约7.25MiB、Worker约0.20MiB、Collector约1.17MiB、Maintenance约0.38MiB；主要有swrast_dri.so、acquisition.sqlite3、site_strategy.sqlite3与SDK日志WAL。该短样本不是90分钟峰值，也不能与容器累计字节重复相加。完整cgroup/磁盘差分另行报告。
- W1/W2/W3容量/模型/业务入口保持原配置，Worker运行作业及3个READY已有实测；未以暂停这些能力换消息指标。

## 观察后半程的调度与剩余问题

- 物理HEAD随机hash相位在IHub有5/10单元挤入约5秒；同身份2并发真实恢复后仍触发3秒准入失败。88ad4250采用按当前物理单元数均匀错峰，9/10/20测试及共享roster/动态TAIL测试通过，不提高并发和超时。生产15:36–15:41窗口IHub已无失败（此前5分钟有约14–24失败），不将其等同20ticker/30秒容量证明。
- 相位/配置变化纳入现有unit generation，旧在途不能覆写新时钟；保留checkpoint和inputs去重，不新增恢复台账。也修复纯调度Source版本变化而SCHEDULE仍旧版，导致Finnhub有真实响应但新鲜证明因版本不一致失败的问题；旧窗口不补填取网/版本证据。
- IONQ现有启用订阅缺定位：真实Investing URL为`https://www.investing.com/equities/dmy-technology-group-iii-news`（[官方新闻页](https://www.investing.com/equities/dmy-technology-group-iii-news)），普通名称slug实测404。通过原身份及正式MarketTickerAdapter验证10条记录；IHub NYSE验证13条。已有增量locator迁移将两个Source版本4→5，不覆盖自定义URL、周期和身份。
- Yahoo相同video有带`?.tsrc=rss`与无参数两个canonical任务；仅对finance.yahoo.com去除已验证追踪参数，保留其它查询选择参数与其它publisher。既有事实不批量删除/重写，不把unsupported视频记FULL。
- IBKR 7496代理接收到连接并不代表上游API健康：socat转到127.0.0.1:4002，而4002实测没有监听；交易Executor也已有连接失败和逾期EXIT报告。未重启交易、修改持仓/交易规则或新增W门禁，属于需恢复上游API的既存基础设施缺口。
- Reuters仍human_challenge_pending；Shephard有明确端点失败；Finnhub仍有真实慢响应/读错误；RKLB updates有上游TLS/读超时。均留在全部应执行周期分母，不能靠其它频道成功抵消。
- 15:00–15:30中段口径：12781应执行、10847新鲜，全部覆盖84.87%（非达标）；22篇适用新canonical中首次FULL18，50个应交付article×ticker中44个≤10秒，已完成48个均值5.56秒/max34.35秒。该中段混合多个版本，尚未包含最终修复的完整观察。
- 去重专项20通过、1条`RuntimeJournal.claim`旧合同测试失败；在之前fc2e8a9a冻结归档中单独复现相同失败，当前RuntimeJournal代码与该归档一致，和Yahoo追踪URL修改无关。没有通过改W1/W2/W3领取规则让该断言变绿；该既有差异作为剩余检查项保留。其余182项冻结组合、21项计划generation/采集、68项市场/SEC及双worker13项等证据分别保留，不能把重复测试累加成独立总数。


## 最终90分钟结算与验收结论

**A–F代码和必要迁移已实施、组合Review及正式构建发布已完成；业务目标尚未全部达标，长期稳定性尚未通过。** 观察于16:00:32.571819Z实际满90分钟，之后只做有界结算、计时纠偏和发布后短检，未重置观察起点。10ticker、267条启用订阅、396个有期望周期的必需unit、74个来源进入本次总账；独立配置SQL未发现整条启用binding被漏出分母。多频道逐unit列出，未用RSS抵消Browser。

|指标|完整90分钟|最后10分钟（仅辅助）|
|---|---:|---:|
|全部应执行周期|39,505|4,221|
|有效新鲜周期|28,730 / **72.72%**|3,761 / **89.10%**|
|缺失 / 在途|1,769 / 4|12 / 3|
|新canonical首次FULL/SHORT_FULL|56/70 = **80.00%**|4/5 = **80.00%**|
|结算时已有FULL/SHORT_FULL|58/70 = 82.86%|4/5 = 80.00%|
|应交付article×ticker，包含未完成|125|5|
|已发布 / ≤10秒|121 / 98（全部分母78.40%）|4 / 4（全部分母80.00%）|
|已发布真实T0→Owner可消费 mean / p95 / max|8.1647 / 17.5290 / 116.8371秒|4.4419 / 7.3768 / 7.3768秒|

首次80%没有达到严格“>80%”，后续成功不能覆盖首次失败；最后10分钟样本很小。完整窗口逐unit≥99%仅16/396，包含本轮启动回归、重建、相位迁移和原有外部失败，不用后半程替代。`missing`是完全缺少对应执行轮，并不包含所有已执行失败；所以不能用1−missing/expected冒充成功率。

计时纠偏：13d2d63b将分页回调T0改为**本页解析验证关联的真实response_received_at优先**，SDK无本页物理事件时才沿用明确回调时间，不拿上一页请求填证据。本地21项采集测试通过，已正式只重建Collector。90分钟窗口处于这一提交之前：用索引限定的`first_seen_at+source→capture→request_started_at→validated physical ID`重新对账，70/70篇、125/125应交付项均有唯一物理响应（重复capture共享同一个physical ID合并取证），记录时间与真实T0最大差17.876毫秒；≤10秒计数与p95/max不变，mean由8.1644校正为8.1647。没有改写历史生产事实。首次结果与发布时点均未越过窗口截止。原始对账见`t0-reconcile.json`及`delivery-physical-t0.json`，逐篇CSV同时保留记录耗时与physical耗时。

### 最后10分钟逐来源剩余缺口

|来源|新鲜/应执行|主要证据与状态|
|---|---:|---|
|Barron’s两频道|200/200|两个必需频道分别计数；最慢单元启动间隔p95约60.17秒|
|InvestorsHub|97/100|均匀相位后明显恢复，仍有3轮失败；最慢单元启动间隔p95 61.92秒|
|Investing|99/100|包含1个在途；最慢单元启动间隔p95 63.39秒；BODY能力另看正文账|
|Yahoo HEAD/补充|379/380|独立频道保留，1个在途；不能把RSS成功替代补充|
|Finnhub|98/100|仍有真实上游慢响应/读错误，未扩大网络超时|
|企业IR|236/240|主要RKLB updates TLS握手/读超时，兄弟端点继续执行|
|Calcalist|7/30|失败物理样本为cse.js HTTP200、实际search element HTTP403，不能把bootstrap算新鲜；16:06–16:11后续15轮CAPTURED，说明间歇性访问问题，未声称根治|
|Reuters|0/320|human_challenge_pending；保留Profile、原Challenge和人工处理边界，不能静默重置身份或削掉必需TAIL|
|IBKR News|0/100|7496 socat代理能接连，127.0.0.1:4002无监听，真实API握手失败；12轮完全缺失也保留|
|HuggingNews / Shephard|45/50、9/10|上游失败，完整失败及状态见逐unit/原因CSV|

BODY失败不能只归为视频：70篇中初始未FULL14篇，结算仍非FULL12篇，包括Investing `no_authenticated_profile_available`、TheFly/AviationWeek subscription_required、SpaceNews/Yahoo unsupported_media。生产Investing的站点auth仍为optional，CRAWLER正常，BODY真实事件却可返回AUTH_REQUIRED；尚不能仅凭汇总码认定是配置要求登录或浏览器坏，不能伪造auth VALID、把订阅预览改FULL，需对具体文章实际权限/返回内容进一步核验。另有旧FULL在最近检查失败时保留FULL及last_error，表示“已有完整正文、最近刷新失败”，不能把它等同首次获取成功。5篇正文头尾抽检未发现仅预览，但不足以证明70篇全量质量。

IBKR同一个上游缺口还影响既有交易Executor，发现一条已逾期EXIT义务。已如实提示，未擅自重启交易网关或执行交易；新闻/资源修复不能通过更改持仓、交易规则或W容量解决该基础设施问题。

### 资源、物理I/O与生命周期

资源差分实际覆盖14:31:12.862415Z–16:01:59.644241Z，与业务精确90分钟窗口差约首40秒/尾87秒，明确单列。共13个离散快照、同一boot ID；容器重建后分段，不能相减不同实例的累计cgroup。磁盘数据为整机vda，包括正式build、其它业务与观察自身，非只计消息系统。

- 整机物理读取约40.03GiB、写入45.32GiB / 90分47秒，均值约7.53/8.52MiB/s；这与旧事故不同累计时长的2.59TB不构成严格净降比例，不能直接宣称节省>90%。
- 冷启动早段CPU busy约92–97%；15:21以后区间CPU busy约53–63%、iowait约1.2–1.8%。末区间15:52:38–16:01:59为CPU busy52.57%、iowait1.40%、读4.08/写6.96MiB/s、约838IOPS、await0.566ms、util12.73%。区间均值不是瞬时峰值。
- 最小**已采样**MemAvailable4.43GiB，末快照4.97GiB、AnonPages8.78GiB。Swap存量约1.22GiB；累计swap-in1,136MiB/swap-out2,511MiB，末9分21秒仍in15.78MiB/out40.11MiB，不声称没有换页或内存压力已永久消失。
- 末快照memory PSI avg300约0，avg10约0.14%；CPU some avg300约9.32%、IO full avg300约0.66%。仍有CPU排队，不能把磁盘改善等同所有阶段低延迟。
- Site anon约2.84GiB/file0.37GiB、Supervisor anon2.60/file1.04GiB、Worker anon0.98/file0.61GiB。cgroup pids为线程/进程任务数（分别669/1489/181），不当作同等数量Chrome进程。不同cgroup文件页/RSS不机械相加成独立物理工作集。
- 15:44:10–15:44:44有界探针中，7个角色cgroup精确读差分合计约80.23MiB/33.86秒，Site10.20、Supervisor62.13、Worker0.12、Collector4.10、Bus0.59、Projector2.87、Maintenance0.23MiB。BODY和其它角色不在此子账。eBPF BIO提交/首vector文件归属不是完整完成字节，未归属部分保留，不能再与cgroup字节相加。
- 已去掉Worker历史宽读和Site匿名历史Target复核；实际READY covering读取0.058ms、合并聚合0.106ms。短窗口文件样本主要仍有Chrome图形库、acquisition/site SQLite及SDK WAL；没有把全部剩余读取硬归因同一SQL。
- 最新正式容器无OOM、RestartCount均0，定义了healthcheck的相关组件健康，其余仅确认进程运行；这些不等于Reuters/IBKR业务健康。Read KeyError类缺口归零，54个PROVENANCE_UNVERIFIED保留。末快照BODY仅1个FETCHING recheck、3个FETCH_DUE recheck，未见长时间全局新稿积压；当时Worker有3个READY、无running作业，不能用该时刻证明W1/W2/W3峰值重叠容量。

仍需既有24小时/72小时观察核对工作集、文件refault、Swap流量与真实W重叠峰值。当前数据未证明需要马上32GB，也未证明16GB在全部峰值足够。20ticker/关键30秒的真实容量尚未通过：IHub若单次4–5秒、2槽，20/30秒平均需求已超2槽，必须进一步降低真实占槽成本或有证据地调整容量，不能宣称均匀相位已解决容量数学。

### 正式版本、最终Review与记录

|组件|实际部署的代码commit|
|---|---|
|Collector|13d2d63b（16:05:22Z正式重建；这是原90分钟之后的计时纠偏，仅短检）|
|Bus / BODY|2fe86d0f|
|Site Access|3df25aef|
|Chrome Supervisor|3e320e58|
|Projector|80c775c9|
|API / Worker / Maintenance / Web|5fc746a6|

全部通过既有`storage_release.py`从已提交归档build并替换正式Compose服务；没有临时生产容器、代码挂载覆盖或删除Volume/Profile。镜像标签及`/var/lib/doxagent/releases/`receipt为准；旧组件遗留BUILD_COMMIT环境值可能过时，e41694a2已修正新发布覆盖，未为改显示字段重启Worker。正式清退仅处理无运行/动态模板/回退引用的镜像，受保护current/previous保留，未做全局prune数据卷。

最终精确3df冻结候选64项通过；13d2d63b新增T0后采集21项通过；此前182项冻结组合及其它模块/前端结果保留原XML，不叠加重复测试虚报总数。`git diff --check`通过。已知RuntimeJournal旧合同测试和其它会话交易工作树冲突如前文保留，不宣称全仓所有测试通过。

本轮修复同时修正了自身引入的3类生产回归（Ukm启动、联合许可无进展、无snapshot隐式串行），承认其影响，未从结算删除。最后整体Review未发现需要新增全局门禁、历史恢复台账或再扩一层队列。剩余工作主要为真实访问能力/权益、关键Browser成本、持续资源增长与全量容量，不再把上线/无OOM当业务验收通过。

详细结算：[观察summary](../eval/unified-io-repair-20261029/summary.md)、[逐unit](../eval/unified-io-repair-20261029/unit-summary.csv)、[逐来源](../eval/unified-io-repair-20261029/source-summary.csv)、[逐篇正文](../eval/unified-io-repair-20261029/articles.csv)、[逐篇交付及物理T0](../eval/unified-io-repair-20261029/deliveries.csv)、[失败与状态](../eval/unified-io-repair-20261029/cycle_status_reasons.csv)、[资源分段](../eval/unified-io-repair-20261029/resource-intervals-final.json)。原始JSON、日志、内核探针、脚本及测试XML继续本地留存同目录。


## 后续Runtime消费契约故障纠偏（2026-10-30）

后续真实消费者检查发现6ticker被SQL别名下的NativeContent引用解析错误堵头，527条等待；这不是正文文件坏，也不能由Owner PUBLISHED时点推导“实际Runtime可消费”。之前表格的交付统计仅成立于Owner发布边界，未覆盖消费者解析/claim/case创建，属于本次验收盲区。新修复不扩大数据库自动解码，仅在选中Stream成员的业务读取边界展开；用真实Scheduler交接及SQLite case唯一性测试和生产游标/case验证收口，详见[修复与发布记录](runtime-stream-consumption-repair-plan-20261030.md)。原始观察数字保留，不改写历史失败。
