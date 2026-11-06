# Reuters 双活采集：实施与验收

## 最小实现方案

只改变 Reuters Site Search 的 CRAWLER（QUERY/TAIL）身份分配，现有 BODY路由/鉴权、人工维护和其他网站保持原行为。Scope Registry 的 verification_plan.crawler_routing=dual_active 启用，删除该配置即可退回主备。保留两个现有Chrome/Profile/出口，不重建Supervisor、不修改账号或验证码处理。

复用 JointBudget：在已有全站/身份并发与节奏预算之外增加内存中的任务分配预约；按实际在途负载、下一可用时刻、身份容量及恢复保护间隔选择健康成员，空闲同分时轮转。预约覆盖整个请求并在异常/取消时释放，不持久化队列，不创建额外调度进程。保留26个QUERY及动态TAIL的60秒目标；主身份重新接单时，TAIL已从10个增长到15个。各身份原有预算不提高；单身份故障时只使用健康成员的合理容量，容量不足明确延期。

分配在现有同请求/共享抓取去重之后进行，同一任务不向两个Chrome并发发送。进入实际执行前再检查该身份的维护、恢复与认证状态，状态变化则交回原有候选流程。正常双活采集不切换active route指针，因此不破坏缓存generation、BODY路由或人工激活流程。

复用候选可用性及真实业务证明；缺少证明的成员通过既有有界canary重新验证。双活成员的warm canary不再把非active成员当冷备而停止Chrome，不因正常能力刷新切换健康route。Registry允许双活成员保留各自并发/间隔容量，运行方式和环境仍遵守既有约束。

Scope仅修改路由方式时，保留原能力验证版本，避免让已有CRAWLER/BODY证明及订阅文章样本无故失效；真实验证契约、身份或会话变化仍按原规则失效。保留Main并发2/1500ms、Backup并发1/3000ms。健康双活成员无需再次满足冷备出口差异化要求；本次不修改任何出口。

首轮生产发现旧全站1500ms最多40次/分钟，低于当时41个60秒单元的需求，纯轮转不能解决该矛盾。第二窗TAIL进一步增长到19个，Main成功请求平均占用3465ms，Backup1886ms，共享并发2又成为尾页瓶颈。因此双活共享间隔取原站点间隔与两个健康成员既有速率之和的倒数中较小值（当前1000ms），共享页面槽位合并两个成员既有容量（当前2+1=3）；进程总上限、各身份物理permit及保护期不变。单成员回到原共享预算；不通过降低QUERY/TAIL频率消除超时，不提高任何身份限额。

BODY使用同一Scope共享预算，避免采集占满两个槽位后，BODY还被旧的站点并发2挡在第三个可用槽位之外。BODY的身份选择、登录权限及正文质量验证不变，不参与身份轮转；全局BODY并发上限也不修改。若自然任务需求继续超过实际容量，必须如实记录延期，不能把配置60秒冒充真实成功间隔。

随后还确认预算层的旧自动缩容与双活容量冲突：`JointBudget.permit`使用只更新首页、刻意排除backfill的EWMA耗时估算并发；较快首页把3槽位压回1槽位，现场确有Reuters等待、Main仅1任务/Backup空闲、全局仅2/8任务而site_limit=1。Reuters双活采集不再使用该失配估算缩容，直接遵守上面的物理硬上限、身份节奏、优先级和全局容量。其他消息源仍保留原估算。新增真实并发回归：模拟极快首页估算、三个任务并发进入既有Main 2 + Backup 1槽位，确认不会被重新串行化。

已人工恢复但长时间未承担业务的身份，在首次自然业务成功后锚定原有保护间隔向正常间隔的线性升载，避免保护期在闲置期间耗尽后突然满载。保留真实recovered_at和复发时长，新的首次使用时间同时参与保护窗口内复发统计。备用正在正常服务时不会重新进入保护期。

TAIL收尾：验收中CEG一个TAIL在6.75分钟内反复产生ENABLED/DISABLED。Collector的binding_state排除停用单元，导致已完成TAIL的done/head_at检查点丢出规划视图；旧首页tail_pending再次复活它，下一次刷新又停用。仅为Reuters当前仍活跃QUERY保留对应已完成TAIL检查点作为规划证据，不改变required=-1、不执行停用任务，不加载旧查询或停用的未完成TAIL。相同head不再复活；新head仍正常创建TAIL。此项仅发布Collector。

## 验证

- 离线：并发任务均摊、保护期降权、单成员继续服务、全部不可用、取消释放、共享站点上限、去重、候选失效、无route抖动、非Reuters/BODY不变、warm canary不停止Chrome。
- 发布：main精确提交，仅重建Site Access；保留Supervisor/Chrome实例。通过现有Scope Owner CAS启用，不直接写SQLite。保存Registry回滚快照，不复制Chrome/Profile。
- 生产：分别统计两个身份的自然QUERY/TAIL成功与延期、按实际QUERY集合计算完整覆盖、正式BODY FULL/SHORT_FULL、控制故障/新Challenge与资源。使用当前acquisition.sqlite3采集Owner，不能读取已退役轮询投影冒充现状。低频观察，不制造验证码或购买测试任务。

## 执行记录

发布前只读：主身份已于北京时间04:10手工验证恢复，备用也已恢复；20:05 UTC起的旧单活窗口仍主要由备用承担。改动与实际验收结果待完成后补充。

Site Access最终源码`5c95dfdf`，2026-11-06 21:16:16 UTC发布，13文件运行哈希匹配。Scope Owner CAS已启用revision3；主备Chrome实例和PID始终未变（388180/1011505），Supervisor未重建。

最终版本自然窗口21:16:17—21:23:01 UTC，共6.75分钟：Main/Backup分别188/110次真实CRAWLER成功，10 ticker各完成6—7个完整QUERY集合。4次采集队列延期均位于21:16启动分钟，此后无新增延期；当前26 QUERY+17动态TAIL均为succeeded，目标全部60s。QUERY实际完成中位间隔（排除首2分钟）按单元在58.38—63.81s之间，最坏单次67.98s；TAIL含停用/重启间隔，不能混作持续活跃60s验收，故额外诊断并修复上述完成检查点问题。

窗口无新恢复事件、无新Provider任务、无同一成功operation跨身份重复成功；不得把旧付费job更新当新增购买或本轮打码成功。没有观察到新BODY访问及正式FULL/SHORT_FULL回执，正文真实验收仍有覆盖缺口；本轮未故障注入，单身份退出/重新加入由回归覆盖。短窗无Challenge不代表长期稳定性已证明。全过程只读观察既有业务流量，不额外请求新闻网站或Provider。

详见`eval/reuters_dual_active_20261107/acceptance.md`及JSON。综合回归81项通过；最后收紧TAIL仅保留当前查询完成检查点后，采集层22项复验通过。

Collector补丁`0a2f9d20`于21:33:22 UTC发布，worker哈希匹配，Site Access/Supervisor未变化；随后另一会话在21:35:14/21:35:45重建Collector/Enrichment，当前Collector `db08791f`是该补丁的后代，不能把21:33—21:36混合窗口当连续稳定验收。该短窗127次成功、15次延期均保留。CEG此前35次ENABLED/34次DISABLED，补丁后对应记录不再反复生成（0/0），新TAIL仍有真实CAPTURED。

另补齐分配层耗时估算：在原有内存cost表中记录各身份所有真实CRAWLER耗时（包含TAIL），仅双活分配使用该估算；其他源原来的首页估算及其准入不变。不增加permit、外部请求或持久日志。路由/恢复61项、Collector22项分别通过。Site Access `3dcf8b34`于21:42:31 UTC发布，13模块哈希匹配；最终独立观察窗口从21:42:32 UTC开始。

首次收尾窗口（3dcf8b34）21:42:32—21:48:10 UTC / 5.64分钟：Main140、Backup83次真实成功；10 ticker分别完成4—6轮完整QUERY。25次site_queue_timeout及1次identity_control_busy发生于21:42—21:45，截至该次21:48:10快照，21:46之后没有新增延期；当时26 QUERY+14动态TAIL全部succeeded、目标均60s，route generation970保持不变。QUERY排除首2分钟后各单元完成中位58.01—62.90s，最大单次63.80s；TAIL包含停用/重启和初期排队，不能宣称所有TAIL完成间隔都严格60s。无新恢复事件、无新Provider提交、无同一成功operation跨身份重复成功。最终BODY FULL/SHORT_FULL覆盖仍为空，未把它算作正文通过。详见routed-final.json/md。

单次资源采样未发现CPU硬配额，Supervisor/Site Access PID及内存上限均未触顶；这不等于已排除主机竞争或长页面耗时。短窗只能证明双活已运行、启动延期后恢复，不能宣称长期不再Challenge或零排队。下一步应补充更长自然窗口及正式正文样本，按实际排队/单页耗时回执判断瓶颈，不降低QUERY/TAIL目标频率来掩盖问题。

后续完整收尾窗口扩展至21:51:51 UTC / 9.33分钟：Main237、Backup147次真实成功，10 ticker各8—10轮完整QUERY；仍保留26次采集队列延期+1次控制占用延期，不能用此前短窗全绿掩盖。当前41活跃单元40成功、1个延期；目标仍全部60s。BODY的15次延期经逐条原始回执确认全部为低优先级recheck（均在实际网站请求前被3秒预算挡回），不能冒充15条新消息正文失败，也不能据此验证前台BODY成功；自然正式FULL证据依然缺失。详见final-verification.json/md及body-queue.json。

最终9.33分钟窗口内另有21:51的一次CEG TAIL:c8364ae9213cd4844f861c55队列延期，不能沿用前一快照的“之后无延期”结论。双活功能、持续QUERY覆盖已验证；全部TAIL零延期及自然BODY FULL仍未全面通过，作为明确剩余限制保留，未通过降频或抹去失败回执制造通过。当前主备均READY、Chrome/PID不变，路由generation970无抖动。
