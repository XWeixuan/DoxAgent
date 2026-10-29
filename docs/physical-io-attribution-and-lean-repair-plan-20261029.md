# TB级磁盘读取：物理归因、重复工作与精益修复方案

日期：2026-10-29。展示时间为北京时间；原始证据保留UTC。范围：同一生产宿主的Site、Chrome Supervisor、Codex Worker、Collector、Bus/BODY、Runtime、Read/Projector及维护。

本轮仅取证和设计，未修改生产代码/配置、清数据、部署或降低业务容量。用户授权的临时启动均使用原正式容器；结束后恢复五个采集相关容器停止。W1/W2/W3相关服务始终保留。

## 1. 结论与确定性

**已确认主要放大机制是文件缓存反复淘汰后重读，而不是新闻本身需要读取TB数据，也不是三大角色主要在读Swap。** 共同窗口10:09:24–16:09:53，所有可比容器累计块读取2,096.51GiB；Site、Supervisor、Worker合计1,724.25GiB，占82.24%。三者同期文件页refault折算量合计约1,720.39GiB，与块读取非常接近；三者自身Swap换入合计仅约0.37GiB。

这不是把refault当作另一个可相加的I/O来源。它是另一组内核计数对“原文件页被反复回收并重新需要”的独立佐证。**历史文件名没有被记录，不能把这1,724GiB追认成某一数据库或某个Chrome文件。** 本轮补了进程、inode、SQL和资源时间线，能够定位实际存在的主要成本，并为未归属部分保留边界。

具体已定位的浪费包括：

- Worker为维护几个READY/BUILDING槽位反复扫描数千条历史槽位。10秒SQL取证约1,331MiB逻辑读取，几乎全部是ready库存查询；30秒文件探针约4,187MiB指向`worker-jobs.sqlite3`。不是W1/W2/W3模型执行所必需的正文读取。
- Site原生实例丢失处理会扫描约23.7万条目标历史；一个实测调用读取约142MiB。已关闭目标历史与当前物理所有权未隔离，实例恢复成本随历史增长。
- Site旧成功事件清理在无实际可删除记录时仍读约144MiB，再为显示backlog重复读约144MiB；SQL命中时间索引仍不等于读得少。
- Read维护10秒产生约1,345MiB逻辑读取，其中已归属的两条artifact退出反查约428MiB；外层批次/时间预算没有消除每批重复查历史的工作。
- Chrome真实读取包含可执行文件、Node、字体/资源、Profile数据库、Safe Browsing列表，以及UKM/分群模型等后台资源。持续短测中Supervisor的UKM约231MiB读请求，Site出现Node/Chrome及Registry共同重读；不能把这些都叫作DB，也不能把所有Profile数据都当身份必需。

**整体根因是：频繁的当前状态判断仍依赖宽历史表和重复验证，同时多套浏览器/控制器/模型运行环境挤压有限的文件工作集；缓存一旦容不下，原本“命中缓存所以看起来很快”的逻辑读取变成持续块读取。控制超时、实例重接及冷加载又放大它。** 降低SQL锁等待、分开进程和增加并发没有改变这条关系。

当前证据不支持“加Redis即可解决”“全部归咎于Chrome启动”或“只要补几个索引，16GB一定足够”。修复应先消除已量化的扫描和重复职责，再减少非业务驻留与后台工作，最后按同负载结果判断容量。第8–10节给出非重复计算的收益模型、执行步骤及硬件缺口。

## 2. 计量口径与取证边界

|口径|本轮用途|不能当作什么|
|---|---|---|
|cgroup `io.stat rbytes`增量|同容器、同启动、同窗口的客体块设备读取；含其子进程|不是Python读文件字节；Site包括Chrome/Node；也不是云底层SSD介质的直接计数|
|`/proc/diskstats` / sar|整台虚拟机`vda`吞吐、IO次数、等待和队列|不能把`vda`和分区`vda3`相加；`%util`不是多队列云盘唯一饱和指标|
|eBPF `submit_bio`读请求|发起进程/cgroup、页mapping对应的dev/inode与读字节|是提交而非成功完成；不能与cgroup数值直接相加|
|`filemap_read`请求长度|文件级逻辑读取需求，包括缓存命中|不是物理I/O，也不是网络下载；请求长度与最终完成字节有差别|
|`workingset_refault_file`×4KiB|此前淘汰的文件页再次需要的规模|不是唯一文件大小，也不与read_bytes相加；跨角色共享页归属可能变化|
|`pswpin/out`×4KiB|页换入/换出的流量|Swap已用容量不是累计流量；同一页能换入换出多次|
|PSS/匿名内存/文件页|区分活跃匿名工作集、共享映射和缓存|不能把PSS_File与同一份文件缓存重复计入容量|

计数定义依据[Linux cgroup v2文档](https://docs.kernel.org/admin-guide/cgroup-v2.html)。文件refault与Swap分开观察；memory.high触发回收不要求发生OOM。

历史累计“1023G/921G/651G”来自不同scope生命周期的关机核算；不再将它们除以同一个时长。原日志G单位也不当作精确十进制GB。下面主要使用原始字节差分换算GiB/MiB，避免把约2.59TB的表述当作精确总账。

新取证分四类：停机基线、首次全链路短启动、Site SQL专门窗口、再次持续短启动。它们都不是数小时事故的逐文件历史账，也不是业务性能验收。取证不drop cache、不跑fio、不读凭证、不制造新闻、不新增W1/W2/W3限制。

探针边界：最初一次全宿主探针发生map容量告警，已排除其文件级汇总；正式探针限制到DoxAgent cgroup且无map溢出。首次短启动第二片被停止条件中断，没有有效BIO trace，保留cgroup记录但不补造文件账。BIO按首个有效页mapping归属整个读请求；全宿主独立8秒校验中约1.66%字节首末inode不同、约25.85%无法作此校验，故文件账是可核对的近似归因，不能宣称逐页100%精确。这些比例不能回填为其它窗口的误差上界。未映射和未命名项单列。eBPF编译/输出、PSS及SQL探针本身有成本，本轮没有将它们当成零成本。

## 3. 历史物理I/O贡献总账：共同六小时

窗口：10:09:24.996–16:09:53.341，约21,628秒。各角色启动时间一致才计算差分；合计仅包括有连续快照的容器。

|角色|块读取GiB|占可比总量|文件refault折算GiB|自身Swap换入GiB|
|---|---:|---:|---:|---:|
|Site|703.29|33.55%|693.14|0.122|
|Chrome Supervisor|566.12|27.00%|582.48|0.126|
|Codex Worker|454.84|21.69%|444.77|0.124|
|Read Maintenance|122.38|5.84%|28.77|0.017|
|Collector|95.45|4.55%|22.80|63.97|
|Clash|60.03|2.86%|52.87|7.60|
|Scheduler|47.45|2.26%|44.94|0.208|
|Projector|22.01|1.05%|18.75|0.070|
|Bus|10.15|0.48%|8.26|0.005|
|BODY|4.67|0.22%|2.82|0.003|
|其它可比角色|约10.12|约0.48%|单独保留|单独保留|
|合计|2,096.51|100%|不与读取合计相加|不与读取合计相加|

重要差异：**Collector的Swap流量是实质性成本，三大角色的巨量读取则主要对应文件页。** “整机在换页”不能被推成“三个大角色主要读取Swap”；反过来，小匿名组件也能在竞争下成为换页受害者。

分窗口三大角色读速率：

|窗口|Site MiB/s|Supervisor MiB/s|Worker MiB/s|
|---|---:|---:|---:|
|10:09–12:08|28.54|17.56|15.73|
|12:08–14:13|31.28|22.44|19.98|
|14:13–16:09|40.33|40.94|29.15|

并非启动一次后逐步变轻，而是持续反复读，后三者合计从约62MiB/s升到约110MiB/s。若只修某一次冷启动，解释不了这条持续增长曲线。

## 4. 本轮文件与操作账：物理读取与缓存命中分开

### 4.1 停机基线

19:19:53–19:20:28的cgroup差分约34秒，eBPF有效窗口30秒。Worker约4,187MiB逻辑读取`worker-jobs.sqlite3`，但实际块读取约0；Scheduler约1,827MiB逻辑读取Runtime，也几乎不触盘。Maintenance约1,815MiB逻辑读取Read文件，cgroup约11MiB实读。**停机后IO低，不证明正常逻辑工作少，只证明当时能命中缓存。**

### 4.2 首次全链路短启动

19:21:04–19:21:41 cgroup窗口约36秒，BIO探针30秒：

|角色|cgroup实际读MiB|BIO提交读MiB|主要文件读请求MiB|
|---|---:|---:|---|
|Site|920.0|900.9|Registry约549.7；Chrome约164.6；Node22.3；resources.pak20.1；Widevine19.7；另有字体/威胁列表/History|
|Supervisor|544.6|518.4|UKM127.8；Chrome63.9；History37.4；模型26.7；多个约16MiB威胁列表；字体/CRL/DIPS|
|Bus|124.6|95.3|bus.sqlite3约90.0|
|Projector|78.8|77.4|Read约74.5|
|Collector|30.6|26.4|Acquisition约22.6|

cgroup窗口含探针加载前后，因此两列不强求相等。启动45秒左右Swap增长触发当时较低的256MiB停止条件，服务19:21:57停止；当时并未重现整机崩溃，不将此算作事故复现。

### 4.3 约两分半持续短启动

19:39:24–19:41:58临时运行；四个30秒BIO窗口，前后cgroup采样跨度合计约141秒，窗口之间有短空隙；未触发本次停止条件。按计划结束后五容器均停止。

|角色|四段cgroup读MiB|BIO可归文件MiB|未映射BIO MiB|逻辑读取MiB|可归文件中的主要项|
|---|---:|---:|---:|---:|---|
|Supervisor|987.2|791.1|10.1|301.3|UKM231.3、Chrome77.4、History53.5、威胁列表/模型/字体；55.3MiB名称未解析|
|Site|876.6|606.7|103.8|28,260|Registry175.7、Chrome141.8、Node105.5、Registry WAL21.2、威胁列表等|
|Scheduler|238.0|158.8|57.7|5,532|Runtime155.5|
|Worker|167.9|130.9|31.0|15,466|运行环境JSON32.6、Codex31.6、code-mode host18.3、thread_history WAL9.4；worker-jobs逻辑读约15GiB但大多命中缓存|
|Projector|97.8|95.5|0.2|1,797|Read94.4|
|Collector|67.9|49.2|4.3|847|Acquisition34.3、运行库等|
|Bus|60.9|52.9|3.9|5,537|Bus47.2|
|Maintenance|44.4|43.9|0.3|7,137|Read43.5|

“cgroup减可归文件减未映射BIO”不是凭空丢失：采样边界不同、提交与完成不同、进程之外/异步归属及探针缺口均会产生差额，作为未对账部分保留。不能将这张新窗口文件比例乘回旧六小时，伪造历史精确文件账。

该窗口也显示缓存竞争如何出现：Site进程数5→56→59→63，Supervisor4→58→111→146；PSS分别增长到约2.61GiB和3.78GiB，Worker约1.47GiB。Site/Chrome的实际读取在后两段上升，Scheduler末段也由缓存命中转为约229MiB实际读。PSS增长包含合法暖起和业务执行，不能仅凭四点就定义泄漏；但它证明单看“8个活动许可”没有覆盖常驻工作集。

文件结论：Chrome/Profile、Registry、Runtime、Worker运行环境都已经有实际触盘证据；NativeContent全展开目前有代码成本证据，尚无证据证明它独占TB级物理读，不能排在已测得的大扫描前面。

## 5. 业务操作到重复读取的根因

### 5.1 Worker：3个READY的判断为什么读上GiB

`worker-jobs.sqlite3`约383MiB。`w3_ready_slots`实存3,987行：USED3,278、RETIRED632、BOUND26、ABANDONED24、RETIRING24、READY3。当前只有主键及退役部分索引，没有正常ready判断所需的state/scope索引。

生产`EXPLAIN QUERY PLAN`确认：`WHERE state='READY'`及`WHERE scope=? AND state='READY'`均为`SCAN w3_ready_slots`。10秒SQL关联结果：

|判断|逻辑读MiB|
|---|---:|
|reconcile所有READY槽位|589.9|
|最近失败与最近ready比较|255.6|
|是否已有BUILDING|193.4|
|BUILDING/READY数量|193.4|
|指定scope是否READY|96.7|
|上述之外|约2.4|

对应`w3_ready.py:fill/_fill/reconcile_residents`及`app.py:configure_ready/w3_readiness`。背景prepare每30秒运行，但configure/readiness也会触发fill/reconcile，**不能按30秒后台循环推定实际读取频率**。样本中READY扫描约4次/10秒，相关scope检查亦多次发生。sqlite_step还按返回行调用，不能把step次数全部叫作独立SQL执行次数。

必要业务事实是少量当前可用槽位、在建状态和明确失败后的恢复节奏；**每次对历史所有宽行重新证明这些事实没有必要**。当前“SQL索引化存储、取消无界Python缓存”的方向减少了常驻，但未补足实际热查询，造成数据库页反复加载——这是典型的瓶颈转移。

修复最小路径：保留现有表，增加针对state/scope的薄覆盖索引和scope失败时间索引；normal fill/readiness只读这些薄事实，同次调用合并ready/reconcile结果；最近失败/成功条件不再反复读config宽行。无需Redis、新队列或降低ready数量，更不限制W1/W2/W3。

此处不是全部新引入：reconcile读取源于`0ac58494`（10/19）；当前fill/失败判断改写于`24a5fde5`（10/28）。判断业务仍合理，历史增长与查询路径未隔离才是问题。

### 5.2 Site：物理目标的“当前所有权”被宽历史拖住

`site_strategy.sqlite3`约789.5MiB。`owned_browser_targets`共236,622行，其中CLOSED234,416、LOST1,485；其余ALLOCATING291、CLOSE_PENDING247、LIVE183，合计721条非终态。非终态不等于721个实际活页，仍需实例归属核验。

`mark_instance_lost(identity,instance)`查询：

```sql
SELECT data_json FROM owned_browser_targets
WHERE identity_id=? AND browser_instance_id=?
  AND state NOT IN ('CLOSED','LOST');
```

实测计划为全表SCAN；已有`(browser_instance_id,target_id) WHERE target_id IS NOT NULL`和清理索引不能覆盖该合同。15秒专门窗口的一个sqlite_step调用产生约142MiB逻辑读；45秒全链路SQL窗口，这一查询累计约2,113.5MiB，占该窗口已关联SQL读取约83.6%。约相当15份整表扫描读量；不把29个step调用机械解释为29次扫描。

**它不是每个普通页面请求都必然执行**：调用发生在实例更换、确认丢失、恢复/清理，以及新增公开共享runtime收尾。原方法来自`c2a6b679`（10/18）。但`public_runtime.py:_closed_facts`在`c770409d`（10/29 07:10）新增：共享匿名Chrome的burst结束后，对使用过的每个逻辑identity逐一调用该方法并写runtime状态。原本为少量受保护原生实例故障设计的历史扫描，被复用到高频匿名临时Context收尾。

这说明上轮公开Context减驻留的收益是真实的，**但它又继承了不适配的持久化身份/历史收尾职责**。必须拆开：匿名共享runtime用其真实物理owner和本次Context集合完成收尾，不为每个publisher伪装一次受保护实例故障；受保护身份仍保留必要所有权、登录/Challenge和人工页合同。必要实例丢失查询加非终态identity/instance索引，目标数据只读实际命中的几行。

本轮另一全链路文件窗口，Site Registry约30秒读19GiB、后续30秒读6.5GiB，随后降到约1GiB/30秒。这是逻辑重复读取，不是同量物理盘读。45秒SQL窗口确认上述扫描具有GiB级贡献，但窗口/实例状态不同，**未将所有19GiB逐语句完全对账**。保留这个差额，不能称全部已经精确归属。

### 5.3 Site维护：LIMIT和时间索引没有阻止读旧历史

两条针对7日前`ACCESS_SUCCEEDED`的查询各约143.7MiB：一条找待删行，另一条为backlog重新读同一范围。即使类别没有匹配，也要走过该历史时间范围；实际计划是走UTC时间索引，不是“完全没索引”。旧类别过滤在索引之外，LIMIT在找到匹配行之后才生效。

删除本已无生产者的兼容类别规则，或完成一次有界迁移后退出正常维护；不再每10分钟重证一次“没有旧类别”。backlog显示可以使用本批结果/进度，不需要再跑相同范围。现代SUCCESS按既有部分索引处理；保留真实审计所需失败与小时摘要。

量级校验：若每10分钟重复这约287MiB，六小时约10GiB，**单独只能解释Site历史703GiB的一小部分**。它应删，但不是可以拿来宣称TB根因已全部解决的故事。

### 5.4 Read Maintenance：每批有界不等于整体读量有界

10秒SQL窗口约1,345MiB逻辑读，其中：artifact退出候选标记约216MiB、清退出候选约212MiB、object_history删除约155MiB；约680MiB未能关联到当时捕获的prepared statement，不能强行分配。

`serving_gc.py`、`artifact_registry.py`及`maintenance.run_gc`用时间/批次约束，但部分LIMIT位于过滤、反查引用/hold之后；一个批次仍会寻找大量不可处理历史，下一批/下轮又从相同范围开始。已耗时的查询不会因为外层2秒预算而撤销之前读量，部分前置查询还不在SQL progress handler边界内。

必要业务是回收真正到期、无引用的退出候选；不是不停检查全体历史是否可能退出。先取有限的新增退出ID，再对这批做主键/引用判断；有hold的项用现有游标前进，hold释放/引用变化再使其到期，避免每15秒重扫。只在实际有新到期范围时进行相应维护。保留Owner→CDC→Read的重放和历史水位，不恢复旧宽CDC，也不靠暂停GC造成长期膨胀。

### 5.5 Chrome/Profile与Worker运行环境：哪些读值得保留

进程归属已按cgroup子树核对。Supervisor的读不是其Python单进程在读新闻：主要执行者包括Chrome的ThreadPoolForeg、主浏览器、renderer/utility等。Site同时包括主Python、identity Python、Node和Managed Chrome；Worker还包括Codex及code-mode host。

- **必要但应少次**：可信Chrome/Node/Codex二进制、必要运行库、字体、登录Cookie/Profile、实际正文/频道资源。暖态需要这些页；反复驱逐后再读没有新业务信息。不能因为必须保留身份数据，就默认每轮重新加载它们。
- **可删/可关闭的候选**：与新闻读取无关的UKM/segmentation/optimization后台数据库与模型工作。UKM在持续短测贡献约231MiB，已是可见成本；具体禁用项需按当前Chrome版本做页面/登录等价验证，不直接抹整份Profile。
- **不能一刀切**：Safe Browsing威胁列表、History、DIPS可能与安全/站点行为相关，不能为了数字直接全清。先区分非必要测量和已有身份行为，减少反复启动；不把受保护站全部迁到匿名headless。
- **Worker当前实读**包含编码/模型运行环境JSON、Codex二进制及历史WAL；它们与ready表扫描是不同成本。正常W1/W2/W3取上下文有必要，重复冷建和无关历史重载不必要。新窗口这些文件触盘，不能倒推旧Worker的454GiB全是ready表。

不把Cold/Warm差异简单称泄漏。优先让相同已验证运行环境和必需高频页按实际需求复用、非业务后台资源不创建；再用同实例PSS、匿名增量、物理target数和进程出生/退出确认长期增长来源。

### 5.6 其余热路径的优先级

Registry同请求重复取session/profile/egress、worker snapshot反复校验仍应合并；NativeContent命中后chunk stat、deepcopy、definition重hash仍应收敛到首次/变化边界；Bus/BODY同篇×ticker重复物化仍应共享一次结果。**这些项有明确的逻辑/CPU工作量，却尚未被本轮计量证明是TB主项。** 排在已测得的ready/owner/维护扫描之后，不以代码气味代替物理贡献。

Collector的Acquisition约1.2GiB、Bus约26GiB、Runtime约6.8GiB，加上Read和Codex历史，说明“热表改薄”没有自动清除旧页和热查询对旧表的访问。清理与压缩必须离线/有界进行，不在每请求做全库一致性复核，不在生产高负载下同时VACUUM所有库。

## 6. 内存回收、磁盘容量和故障反馈

### 6.1 因果顺序

|时间/范围|内存及回收|磁盘及业务|
|---|---|---|
|09:00–09:30|Cached约2.7–3.2GiB，available约2.7–3.4GiB|读约11–21MiB/s，await约0.8ms|
|09:40–10:10|Cached降至约1.6GiB，Swap换入升到约5MiB/s|读升至91MiB/s、队列16、iowait15.8%|
|13:10|持续refault/换页|读约142MiB/s，队列84、await11.5ms；普通HTTP开始失去周期|
|14:10–14:20|可用内存低位，随后恢复/冷建交织|读157–180MiB/s、队列69–116、iowait31–46%|
|17:00|回收/换页长期存在|读159MiB/s、队列146、await26.2ms、iowait58.8%|

历史快照16:09全局AnonPages约12.28GiB，文件缓存仅约1.38GiB；三大角色refault与实读增长同步。这支持：**本已存在的大量重复读取在内存工作集增长后变成实际读盘；读盘拖慢控制/处理，恢复与重建又增加工作集和文件加载，形成正反馈。** 不是一个“内存先”或“SQL先”的互斥单点：宽历史扫描制造不必要的热数据，过重驻留使其无法缓存，两者同时需要减。

```mermaid
flowchart LR
  A[少量当前状态反查宽历史] --> B[反复逻辑读/解码]
  C[多套浏览器/driver/模型驻留] --> D[可保留文件工作集不足]
  B --> E[文件页淘汰与refault]
  D --> E
  E --> F[块读/换页/IO队列]
  F --> G[控制超时/配置STALE/任务延迟]
  G --> H[重接/冷建/恢复与重复工作]
  H --> B
  H --> C
```

这也解释为什么只读任务未持长写锁仍能卡整机：它能持续消耗内存带宽、页缓存及块队列；写锁只是多个后果之一。

### 6.2 云盘真实规格与边界

元数据和块设备确认：`SA2.2XLARGE16`，8核16GB；单块300GiB `CLOUD_SSD`系统盘，`vda3`承载Docker、数据库、Profile及Swap。按[腾讯云当前CBS公式](https://cloud.tencent.com/document/product/362/2353)，该容量SSD标称约10,800随机IOPS、180MB/s吞吐。实例规格也可能另有限制，最终取盘与实例较小值，见[实例存储性能](https://cloud.tencent.com/document/product/362/122632)。本轮未取得云控制台实时QoS/监控，不能声称已确认某一限速器精确生效。

09:00–17:50的10分钟样本加权平均：读115.13MiB/s、约7,968tps；最大样本读186.69MiB/s、11,642tps。高峰已在标称能力附近，部分采样略超公式值，可能涉及单位、统计层级及平台实现；不能将其强行解释成严格180MB/s平顶。真实await/队列恶化已证明服务时延受损，无需用`util=100%`才认定有问题。

宿主read_ahead为8MiB，是值得单独测量的配置，但尚未证明大量无用预读占主导；不把调低它列为已量化收益，不直接修改内核参数。没有对生产盘跑压力基准。

## 7. 正常必要成本与应删除/合并的实现

|保留的业务事实/能力|删除、合并或变轻的实现|实际业务影响|
|---|---|---|
|W1/W2/W3 ready及真实在建状态|每次扫描全部历史config来数几个ready；同次重复reconcile/count|保持ready数量和启动能力，降低本地准备等待|
|受保护物理实例/人工页所有权|匿名临时Context逐逻辑publisher登记/丢失受保护实例；失效扫描全部旧target|匿名收尾只管实际运行资源；受保护身份安全合同保留|
|必要故障证据与更新历史|每次普通状态查询展开历史全文，终态target宽行充当实时所有权|历史可按需查，正常路径只读当前事实|
|历史回收和未完成责任|空转兼容清理、重复backlog扫描、LIMIT前无界寻找可删对象|保留真实到期回收，减少无进展维护|
|必要Profile、登录、Chrome版本和高频暖页|无需求常驻、每burst重复冷建、无关测量/模型后台资源|不降轮询/频道/质量；非业务浏览器后台行为会改变，须作等价验证|
|正文完整性、ARTICLE_ONCE、真实更新修订|同一不可变内容反复物化/hash；获取成功后因提交失败重新下载|发布/更新语义不变，下载与提交继续分离|
|显式暂停、日历、截止和Profile维护|配置读/维护一慢就以5秒STALE拒绝所有取网；控制超时即推断native死亡|取消技术健康与业务撤销混用；显式撤销及时生效|

不新增一套全局压力硬门禁；不通过降低ticker/频道/频率或限制W1/W2/W3交差。现有身份绑定和真实物理占用不能假归还，但无需为每个可重试只读操作套用关键写入的长期shield/settled等待。

## 8. 优化收益总账与不重复计算的方法

### 8.1 可以直接量化的单项工作量

|项目|当前测得成本|实现后的成本预算/节省|可信边界|
|---|---|---|---|
|Worker ready查询|约1,329MiB/10秒的ready相关逻辑读取|薄索引/合并后预算≤10MiB/10秒，减少>99%这组读取|当前热点可直接验证；不是承诺历史454.8GiB物理读同减99%|
|Site实例失效扫描|约142MiB一次；45秒约2,113MiB|实际命中几条active记录；普通匿名收尾不再调用。每次预算≤1MiB|旧保护实例仍需有限更新，不能简单跳过所有权处理|
|Site旧类别维护|约287MiB/轮，再重复backlog显示|移出常规路径后基本消除该读取；六小时约10GiB逻辑读|约占Site六小时物理读的1.5%量级上界，单项不足以解释TB|
|Read退出反查|10秒约428MiB已归属；另有未归属约680MiB|候选先有界、引用主键查，预算这组减少≥90%|须同时验证真实回收推进；不能把未归属部分也记为已节省|
|UKM后台读取|持续短测约231MiB提交读，Supervisor可归文件读的约29%|禁用无关后台工作后该部分可趋近0；启动/重建节省按次数算|不是Supervisor旧566GiB中已证明29%均为UKM|
|重复Registry/Native物化|存在多层重复读取和对象处理|同版/同篇一次物化，按真实调用计数下降|尚无独立物理字节份额，收益账暂不另加物理GiB|

CPU也不能凭逻辑GiB直接换算“省几核”：Worker该组sqlite_step累计墙钟约0.606秒/10秒，Site失效扫描约4.31秒/45秒，含等待读盘，均非进程CPU实测。它们是调用内时间成本；Python解码、模型构造、Chrome后台CPU还要按相同负载cgroup CPU差分核算。当前证据不能声称仅补索引可节约数个CPU核。

### 8.2 物理I/O的联合预测模型

物理读取≈必要首次/变化读取＋重复逻辑工作×缓存失效率＋必要运行环境refault/Swap。**减少逻辑工作和增加可保留工作集作用在同一份读盘上，不能分别加一遍节省。**

以下是用于决策的条件场景，不是已实施结果或统计置信区间。以共同六小时2,096.51GiB为基数，每个角色只记一次最终净降幅；要求第9节整体完成、缓存不再持续震荡，历史逐文件缺口仍存在。

|互斥角色桶|条件净降幅|六小时可减少GiB|前提|
|---|---:|---:|---|
|Site 703.29GiB|60–85%|422–598|消除匿名收尾/目标历史扫描，控制驻留与重建，正常配置读取变薄|
|Supervisor 566.12GiB|50–80%|283–453|后台无关工作删除、必需暖态稳定、同一运行环境不持续被逐出|
|Worker 454.84GiB|60–90%|273–409|ready历史扫描消除，同时保留真实W1/W2/W3运行环境；其必要首次加载不能删除|
|Maintenance 122.38GiB|70–90%|86–110|退出候选增量处理，不以推迟所有回收代替|
|Collector 95.45GiB|50–85%|48–81|采集路径减法及文件/匿名工作集竞争解除，特别是换页减少|
|其余154.43GiB|20–50%|31–77|Runtime/Read热查询和页面竞争下降，保留必要业务读取|
|合计|约54.5–82.4%|约1,142–1,729|同一缓存收益不再重复加算|

对应残余约368–954GiB/六小时，即约17–45MiB/s。区间较宽的原因是真实历史文件份额缺失、cache miss受工作集非线性影响；**不能将乐观端当成16GB已证明足够**。修复后若仍在45MiB/s以上且refault持续上升，应继续削减工作/分离工作集，不能把它当作“比TB好很多就验收”。

若缓存未脱离震荡，某些改动的物理节省可以接近0；若工作集完全容纳，物理节省可能高于表中上端。用新窗口实测替换系数，而不是将这些假设写进UI当事实。

17–45MiB/s仍不是“新闻业务不可避免的读量”。必要首次加载、实际变化内容和W1/W2/W3上下文读取应逐操作解释；充分命中后应继续争取超过90%的净降幅（六小时约≤210GiB、约≤10MiB/s）。第11节20MiB/s只是初期资源预算，不是允许长期浪费的业务指标。当前短测不能给出所有必要业务读取的精确长期下限。

## 9. 按依赖执行的统一修复方案

### 步骤1：先移除已量化的读放大，不改业务容量

1. `codex_worker/w3_ready.py`、`app.py`、`job_store.py`：正常ready查询走state/scope薄索引；最近失败/成功读薄时间索引；同次configure/readiness合并重复reconcile/count。保留全部ready数量、模型配置及case绑定。没有变更/无过期状态的configure不重新走历史盘点。
2. `site_strategy/governance_repository.py`：为真实非终态目标的identity/instance访问建立小索引；`mark_instance_lost`只读取匹配active行。先缩读取，避免在活跃生产时为了空间马上重写789MiB整库。
3. `site_strategy/public_runtime.py`：匿名Context取消逐publisher受保护实例失效/历史登记；物理runtime owner一次完成真实关闭与本批Context收尾。必要的cleanup_pending仍反映实际占用，不引入假释放。
4. `site_strategy/retention.py`：旧类别兼容完成后退出常规路径，去掉相同范围的backlog二次读取；正常SUCCESS/失败规则按真实增量维护。

验证：真实历史规模上，ready查询无宽表SCAN；失效查询命中当前实例少量行；匿名burst收尾不再触发mark_instance_lost整表读。用相同操作次数的SQL/BIO账证明，不以“单次缓存很快”代替。

可从以下三个薄索引开始，用真实SQL的EXPLAIN确认覆盖；这是实施候选，**本轮未在生产执行**。state/scope索引覆盖正常READY/BUILDING判定；scope时间索引使历史成功/失败比较扫描薄键而非config宽行，避免额外引入新的缓存台账。

```sql
CREATE INDEX IF NOT EXISTS w3_ready_state_scope
ON w3_ready_slots(state,scope,id,thread_id,case_id);
CREATE INDEX IF NOT EXISTS w3_ready_scope_times
ON w3_ready_slots(scope,failed_at DESC,ready_at);
CREATE INDEX IF NOT EXISTS owned_target_active_instance
ON owned_browser_targets(identity_id,browser_instance_id)
WHERE state NOT IN ('CLOSED','LOST');
```

### 步骤2：收敛Read/Runtime/Collector的持续重复职责

1. `v2_read/artifact_registry.py`、`serving_gc.py`、`maintenance.py`：先取有限退出ID，后做引用/hold主键判断；用现有小游标推进，避免多轮从头找可删项。对前置查询也应用实际预算；没有进展不反复加批重扫。
2. `message_bus_v2/collection_plane/{config,worker,store,maintenance}.py`：编译/控制只读取必要状态；UI applied回执和历史维护退出配置刷新链；未变配置不每秒重写显示meta。显式停用/临时期限继续及时生效，删除5秒技术STALE转全来源撤销的耦合。
3. `persistent_runtime_v2`、Scheduler和Read：根据本轮Runtime文件热读/SQL未归属部分定点补一次标签；只取ready/case/effect需要的字段与索引范围。新case/dispatch不展开全部历史，未变结果不重投影。W1/W2/W3正常流程和持久化事实保留。
4. NativeContent与Registry一次请求一个最小快照、同篇一次显式内容物化；完整性验证放到写入/首次读取/实际变化，不在每次缓存命中重hash/重扫全部chunk。

验证：Maintenance同负载逻辑读量及实际回收量同时核对；Runtime未归属读取不能靠猜测填表；配置维护延迟不再阻止实际发网。

### 步骤3：降低实际浏览器/运行环境工作集和单次成本

1. 按实际端点决定HTTP/JSON/Browser路线，删无价值的先失败再fallback；不以RSS覆盖必需浏览器频道。保留受保护站当前Profile/启动方式与Reuters活动页约束。
2. 匿名runtime按正在执行和已排队的真实burst复用；零用户之间可留极短、可立即接回的空闲期，避免每篇启动/停止整套，但不长期常驻。受保护高频页覆盖下一真实需求，不能固定短寿命反复冷建。
3. UKM/segmentation/optimization等与业务无关的后台功能逐项关闭；只保留必要页面资源。对身份依赖站做同Profile等价对照，不直接清Cookie、整份History或全部安全功能。
4. 控制器故障与native生命周期分离；同一实际实例由一个owner处理，取消重复恢复/反复重接。读取超时不升级成所有身份重建。
5. Worker保留W1/W2/W3运行与ready能力，去掉无需求环境重复加载和历史对象驻留；无需给W1/W2/W3新增硬门禁。

验证：每实例私有匿名/PSS、Node/Chrome/renderer实际数量、存活年龄、重建次数、同文件refault均下降；warm页面和正文质量不退化。仅把Site内存转移给Supervisor不算节省。

### 步骤4：一次性处理增长负担，再验证完整负载

1. 终态目标历史、旧ready详情与技术事件从热判定退出；保留必要业务引用/审计摘要与未完成责任。需要压缩文件时在停机/低负载窗口一次处理，不每轮VACUUM或全库反向验证。
2. 先用当前真实历史规模验证薄查询，再做正常ticker数全链路和20ticker容量验证。显式区分真实新稿、更新复查、恢复积压，不一开服务就无差别重跑所有旧文。
3. 恢复前按实际镜像改动范围build正式容器；本轮仅产出方案，不执行部署。保留生产数据、Profile和版本契约。

四步是依赖关系：只删5秒门禁会放进更多流量；只加索引仍可能让浏览器工作集超过内存；只减少Chrome仍会留下随历史增长的全表读取。

## 10. 8核16GB是否足够与基础设施选择

### 10.1 当前架构的容量缺口

历史全局匿名页约12.28GiB，再加约0.49GiB shmem和约1GiB内核开销，已几乎占满实际15.24GiB内存，留不下足够的文件工作集与波峰余量。文件缓存虽然“可回收”，但对持续使用的代码/数据库页不是免费可扔的部分。

用于规划、尚需实测的完整负载文件工作集约3–5GiB，另留2GiB余量，则当前形态需要约18.8–20.8GiB，缺约3.6–5.6GiB。**这是按现有匿名峰值及工作集假设的容量模型，不是把不同容器生命周期峰值相加，也不是精确内存需求实测。** 热页唯一大小没有历史逐页记录，不能给出精确到百MiB的硬件承诺。

要在16GB稳住，工作应至少降到一个可验证的平台，例如：全局匿名页约8.5GiB、shmem约0.5GiB、内核约1GiB、必要文件工作集约2GiB，合计约12GiB，保留约3GiB余量。相对12.28GiB匿名峰值需要消除约3.8GiB不必要驻留/重复对象；这不是给任务设置8.5GiB硬门禁，而是同业务能力下的设计和验收预算。

目前不能证明“不改变W1/W2/W3和受保护站能力就一定能减出3.8GiB”。因此结论是：**现有结构继续原样跑16GB没有稳定性依据；按方案减法后有望适配，但仍必须用完整负载确认，不能预先宣布根治。**

CPU同一六小时核算约4.11个平均占用核：Bus1.07、Scheduler0.74、Site0.57、Supervisor0.55、Worker0.36、Collector0.25，其它约0.57。由`cpu.stat`差分得出，是真实执行消耗，不含等待读盘的“占CPU”。因此当前不能将问题说成“正常业务天然需要超过8核”；内存/页缓存和磁盘等待更先构成约束。但Bus/Scheduler仍有与少量新稿不相称的常规工作迹象，必须审查空轮、未变输入及历史反复物化，不能把这约1.8核全部算作必要成本。可精确归因的SQL调用时间见第8节，其余CPU节省尚不作虚假数值承诺。

### 10.2 资源选项的收益排序

|选项|真实收益|局限/采用条件|
|---|---|---|
|先完成读放大与生命周期减法|同时降CPU、缓存需求、块IO及增长斜率|这是必须做的；扩容不能替代|
|内存升到32GB|相对16GB新增约16GB，足以覆盖上述模型3.6–5.6GiB缺口并给文件工作集余量|实际型号/宿主预算须相应更新；不删除扫描仍会随历史增长复发；不代表可以放任进程增长|
|高性能盘/独立数据盘|为必要DB/日志/恢复读写提供更低延迟，并隔离Profile/Swap与业务库队列|当前约180MB/s/10.8kIOPS量级已接近需求高峰；若保留现负载并留一倍余量，需约≥350–400MiB/s、≥20–25kIOPS级别，且实例支持；这是保留浪费时的容量估算，不推荐作为主修复|
|Chrome/Site与Worker物理分离|拆开匿名与文件工作集、独立磁盘队列，降低跨域故障扩散|增加运维/网络；实测减法后仍不能在16GB留余量时优先考虑，而不是继续堆容器上限|
|Redis|对少量版本/状态分发可省重复读取|不能修Chrome页缓存、ready全表扫描、Native物化和GC反查；新增驻留与一致性成本，本轮没有引入依据|
|数据库分层/独立DB服务|热事实与冷历史真正分离，可减少重扫与本机工作集|先在现有SQLite完成薄索引/薄查询/按需内容读取。直接搬到Postgres不会自动消除同一批浪费；多线程/连接池也不能代替减法|

判断门槛：若修复后完整负载的匿名/热页平台仍高于约12GiB、或必要业务重叠时持续吃掉3GiB余量，应明确承认16GB不足，采用32GB或组件物理分离。不能再用延后消息、降质量、饿死更新或限制W1/W2/W3来维持表面存活。

## 11. 验证标准、缺口和最终运行状态

修复完成后必须同时验收：

1. **物理总账**：同启动同窗口的vda/cgroup字节、提交BIO、命名/未命名inode、Swap分别对账；不能把两块设备层级重复相加。一次长窗口至少给出占主要读取份额的实际文件，不继续只报容器总量。
2. **工作量**：真实历史规模下ready、实例失效、普通接纳、GC的每操作逻辑字节/查询次数明显下降；至少达到第8.1节预算。空转/无变更不随历史库大小增长。
3. **资源平台**：正常盘中与W1/W2/W3重叠负载至少24小时，再看72小时增长。稳态平均读以≤20MiB/s、IOPS≤约2,000为初步预算，波峰允许短暂超过，但refault/Swap/队列不能持续上升。此预算不是网站覆盖率替代品，也不是后台限速值。
4. **业务**：逐必需source/ticker/unit周期接近100%，包含失败/延期/无请求/在途；真实新稿从采集至可消费尽可能≤10秒，真实FULL/SHORT_FULL>80%；独立频道、更新和ARTICLE_ONCE均保留。源级为0不能被大量RSS成功掩盖。
5. **故障与重启**：一个controller失败不使其它身份全部重建；匿名burst退出不做全历史所有权扫描；本地提交失败不重新下载；显式停用/到期仍及时停止新请求。
6. **诊断本身**：避免全库对象展开和无上限SQL导出；短样本未命中某个周期任务要记缺测，不能称无成本。

仍缺证据：旧启动逐文件/逐操作精确份额、Scheduler/Read部分prepared statement关联、历史冷建次数和真正必要唯一热页大小、云控制台实时QoS及实例存储上限。没有这些证据的部分保留模型/区间，不强凑100%已命名账。

最后一轮SQL专门取证已于19:47:17停止五个临时启用的正式容器；19:50:21再次确认全部exited，available约12,019MiB、memory PSI full avg10为0、IO full约0.71%。Swap仍约609MiB是之前换出的存量，当前不为“清零”执行swapoff/重启。该状态不是完整业务负载验收。没有修改生产代码、数据库schema、配置、Profile或任务容量；相关业务状态仅由短暂启动时的原服务正常运行推进。

## 12. 证据索引

本轮原始证据、只读探针与本地分析：`eval/io-attribution-20261029/`。历史共同窗口来源于三轮观察的`host.json`及baseline；前次关机账在`eval/message-pipeline-joint-regression-20261029/shutdown-attribution.json`。所有历史原始数据保持不变。

- `historical-deltas.json`：相同容器启动的六小时分窗差分。
- `historical_analysis.py`、`ledger-and-scenarios.json`：本地重算共同窗口、占比和条件收益；不改变任何生产数据。
- `historical-cpu.json`：同一六小时实际CPU差分；不与IO等待占比重复计算。
- `sar-full.json`、`sar-summary.json`：整盘、回收、Swap及CPU历史；summary展示时钟为北京时间，原始sadf保留UTC。
- `baseline-filtered.json`、`active-windows.json`、`warm-windows.json`、`window-summary.json`：快照、正式运行启停、BIO及逻辑读取；采样前后与有效trace边界分别保留。
- `worker-sql.txt`、`maintenance-sql.txt`、`scheduler-sql.txt`、`sql-summary.json`：SQL与逻辑读关联，空标签不补猜测。
- `site-sql-window.json`、`site-sql-full-window.json`、`site-sql-full-summary.json`：Site专门SQL证据；后者包含45秒正常原服务窗口。
- `schema-audit.json`、`schema-details.json`：只读schema、真实EXPLAIN和薄状态计数；Profile元数据遍历有截断，不将其当全量容量盘点。
- `index-plan-validation.json`：仅在本地空内存schema上验证候选索引能使真实SQL走薄索引；不是生产建索引或性能验收。
- `cloud-metadata-types.txt`、`cloud-disk-type.txt`、`host-infrastructure.txt`：实例、云盘类型、容量与宿主设置。
- `bio-validation.txt`、`final-stop-verification.txt`：归因方法误差检查与最终停机收据。

本报告替代“找到一个低效SQL就解释全部TB”的推断方式：已形成相同窗口的物理角色账、当前文件账和实际业务操作账；历史文件缺口明确保留。下一轮先实施可量化减法，再用同一套账核对节省与剩余容量，避免再次只移动瓶颈。
