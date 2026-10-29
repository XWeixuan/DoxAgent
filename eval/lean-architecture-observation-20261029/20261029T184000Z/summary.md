# 18:40Z观察结算：Runtime修复期间的真实负载与资源再恶化

## 边界与证据

本次业务窗口2026-10-29T17:18:19Z–18:40:00Z，衔接上轮成功水位，混合版本与失败全部保留。interval.json查询于18:40:52Z，未发现initial_result或consumable时间晚于18:40:00Z的样本；资源host.json为18:41:47Z独立时点。后续应急停浏览器18:45:48Z和新补丁发布不能倒算进该窗口。修复与持续进展详见 [Runtime修复报告](../../../docs/runtime-stream-consumption-repair-plan-20261030.md)。

原始cadence导出的schedule及17:18–17:28 run片在3秒SQL预算内中断；保留原始失败。只对失败run拆为2分钟补证；schedule改为历史各必需unit在起点的最后有效计划，加窗口内所有计划变化，不展开全部旧计划。原始、retry及schedule-heads-retry合并的cadence-reconciled.jsonl由现有cadence函数在本地计算，补证均有complete标记。没有把失败取证当成功，也未修改生产表/数据。将此索引限定探针供后续观察复用，勿重跑全历史schedule宽读。

## 实际采集、正文与交付

- 全部应执行34,880轮，有效新鲜22,330轮：全轮次覆盖64.02%，已结算/逾期口径64.09%；完全缺轮4,851、未完成37。上轮全轮次86.87%，本轮显著恶化。全部source/ticker/必需unit及间隔见cadence-184000.json，不能只看成功源。
- 新鲜间隔23163个样本，mean82.03秒、p95 172.91秒、max4901秒；不同配置周期混合统计仅辅助，逐unit对配置周期验收，不能用该均值代表每个源。
- 主要缺口：IBKR816应执行/0新鲜，DIGITIMES405/0，Reuters2508/11，Calcalist262/54，Yahoo3610/2038，Investing817/423，InvestorsHub816/473。来源失败、延期与资源抖动重叠，不把全部失败都归因于同一机制；Reuters/IBKR等既有能力阻断仍独立保留。
- 真实新canonical31篇，首次FULL/SHORT_FULL28（90.32%）、当前29（93.55%），仍有PARTIAL/UNAVAILABLE各1。这里是程序质量标签，未重新下载或抽取正文制造质量样本，不能声称所有正文独立验证完整。
- 应交付article×ticker32，Owner已发布31，≤10秒19/32（59.38%），NO_PUBLIC_BODY 1保留。已发布mean14.84秒、p95 71.19秒、max80.00秒。T0仍为当前first_seen代理，逐篇物理响应关联本轮未重做，不能冒充全部真实取网T0；T1仅Owner可消费代理，Runtime真实游标/case与Read另验。

## Runtime及非Agent/LLM跨度

当日窗口新case190条：BE40、CEG30、COHR45、INTC5、MU30、NBIS35、RKLB2、SNOW3。包含故障后旧积压的接回，不称190篇真实新新闻。六个原堵塞ticker均真实创建case；API ReadStore.page→legacy_case_summary→CaseSummary逐ticker合同最终返回BE/CEG/COHR/MU/NBIS各20、INTC5，约0.067–0.283秒，证明后端前端查询路径可读；没有用户浏览器截图/认证视图证据，不称用户页面亲测。

已证实并修复的本地机制：16KiB NativeContent别名漏展开；effect领取OR查询终态历史扫描；ACK JSON反查历史并持锁8.255秒；W3 history freeze持写锁11.653秒；INTC稀疏当日页强制全历史排序索引耗时1.618秒，改现有日期索引后31–64毫秒。随后进一步确认整轮历史恢复同步阻挡入站，修复版本另记。具体生产版本、红绿测试及在途ID证据见修复目录。

本窗口188个近期Worker job持久化样本含失败/queued/running；不是188个非LLM跨度完成样本。case创建精确起止、W1/W2本地跨度及精确fork RPC仍缺统一证据，明确不可测，不用case总时长、lane_elapsed或模型roundtrip替代。W3 claim/ACK/history真实锁跨度仅有界日志样本，不能推成全量分位数。INTC残余积压未排空、部分新消息等待必须保留续记，不以reader OK等同实时消费恢复。

## 资源与归因

sysstat-utc.txt为正确窗口：宿主Asia/Shanghai，必须TZ=UTC，并读取当地10月30日sa30。首次sysstat-invalid-local-time-window.txt误取sa29同名本地时段，已明确作废且保留，不用于本轮结论。

17:10:01Z–18:40:00Z sysstat采样均值CPU user42.91%+system15.83%、iowait19.13%；18:10采样iowait47.85%、磁盘await30.10ms/队列138.96；区间平均读107.94MiB/s、写14.73MiB/s、5463 IOPS、await10.20ms，平均available约2.34GiB、Swap约1.47GiB，换入/换出约1156/1154页每秒。均为10分钟采样，不冒充瞬时真实峰值。

可比同epoch窗口17:44:47Z–18:41:47Z：Worker物理读158.109GiB，Site132.361GiB，Supervisor135.425GiB，三者合425.895GiB；Collector13.974GiB、Projector14.499GiB、Bus2.281GiB、BODY0.881GiB。Scheduler换代不跨epoch硬算。完整逐区间值见metrics-summary.json。物理读不是SQL逻辑字节，不能把已识别某个慢SQL等同于全部425GiB；高匿名工作集、refault和换页与三组件同时重读重叠，仍需持续按owner细分。

18:45:19Z内存PSI avg60 some84.60/full78.97、Swap1.76GiB、available约1.42GiB。18:45:48Z应急停止正式Site/Supervisor后available升至约8.63GiB，Worker随后扩张至9.895GiB、约51个capsule而当前job映射20；之后部分又回收。资源压力具有跨组件占用和弹性放大的机制，不能用“浏览器停了/没有OOM”宣称修复。

## 续记与限制

- 临时停止的仅Site Access与Chrome Supervisor；Collector/Bus/BODY及W1/W2/W3仍运行，保留所有数据/Profile。浏览器频道缺口照记，不用HTTP/RSS成功替代；观察任务不得自行启动浏览器。
- 原已记录5个Worker在途job全部按原ID succeeded/SETTLED；后续控制容器切换的在途清单另保存，不能笼统称所有任务均已收尾。
- 周期/正文/交付水位推进至18:40Z；17:18–18:40版本变化、超时、未完成和低覆盖均保留。资源cgroup推进至18:41:47Z；sysstat至18:40Z，后续按各自水位增量。
- 不宣称接近100%周期、≤10秒最终消费或完整负载长期稳定。至少24小时继续观察，最终版本生效起点及后续停服分别记；待用户对SDK驻留业务调整/内存路径作出选择后，人工恢复记录覆盖当前暂停。
