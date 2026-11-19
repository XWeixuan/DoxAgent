# 关键链优化发布后：剩余长尾诊断与最小后续方案

2026-11-19。本文是按原方案开发、发布后，因实测仍超标而追加的调查；建议部分尚未实施。实施与发布明细见 `runtime_critical_chain_implementation_release_and_acceptance_20261119.md`。

## 结论

**常态已经改善，10秒目标仍未达到。** 最终 W3 核心版本窗口起点为 UTC 11月18日23:37:28；冻结阶段观察截至11月19日00:22:53。43个自然准入均已完成，其中35个进入W3，全部完成，105个Worker Job全部成功，无重付费重派证据；70条Trade Thread互不跨Case共享，Operations均沿用对应代表Thread。

| 同口径内部耗时 | P50 | P95 | 最大值 |
| --- | ---: | ---: | ---: |
| Trade | 2.61s | 8.33s | 11.09s |
| Operations | 1.48s | 3.94s | 5.42s |
| W3整体 | 3.93s | 10.78s | 13.15s |
| 完整Runtime | **5.60s** | **14.70s** | **15.71s** |

35例是完整保留的盘后阶段样本，包含首次双冷准备及后来的突发；没有裁掉慢例。它不足以通过100例/开盘负载正式验收，但已经复现了超过目标的实际关键链。前轮105例的10.12/20.69秒来自不同负载窗口，不能把数值差全算成本次代码收益。

剩余问题是几种成本叠加：**高频控制/空探测与连接初始化、串行短写owner上的重复工作、突发MISS准备，以及前置W1/W2内部成本。** 不能继续只改某一条慢SQL，也不能仅靠把库存再扩两份解决。

## 1. 原方案并非只改了指标

- 新正常注册确实一次事务；真实冷例首owner的注册子链约116ms、整个首owner约459ms。高负载NBIS仍只有一次注册，却在同一次准入内累计8次gate尝试。事务合并生效，但共享争用没有自动消失。
- paid A/B保留，派生main与Trade提交合并，route projection同事务保存，已有Control/admit合并均已在运行源文件中核验。
- 六份是可认领物理库存，不是逻辑状态；十分钟PSS合计约944MiB，无OOM，实时MISS继续直启。
- 生产17,207个exception pin与Read gap成员一致。最终复审另修正了首次ACK借用连接的只读快照未及时结束问题，仅补发Projector至`48d0284ee`，没有重启W3。核心Scheduler/Worker仍为`307db28d2`。
- 本地统一回归268项通过；随后真实borrowed-connection快照/ACK路径追加29项定向回归通过（两组有重叠，不能相加成297个独立测试）。

需要纠正原方案的一项收益预期：**旧健康路径的`_admit`已经跳过空事务**。Control并入admit在generation/probe有更新时减少一次独立准入；普通健康请求仍然需要同样的一次Control准入，不能冒领一笔节省。普通`worker_requests`首次冻结仍独立提交；进一步合并须保留“原Job恢复先于新请求准入”的顺序，而不是让成功结果再次等Provider Pause。

下面是未被这些修改消除的实际工作。

## 2. 最慢Case已能闭合到具体区间

| Case | Runtime内部 | W1/W2前置内部 | Trade内部 | Operations内部 | 核心发现 |
| --- | ---: | ---: | ---: | ---: | --- |
| NBIS `case_c2bfd71444834e71be42e064b3311361` | 15.71 | 2.56 | 11.09 | 2.25 | 双MISS；注册仍等待gate，派发前另有短写队列 |
| INTC `case_e82250d49bdb410690fe8f8a2c2c7e76` | 14.70 | 3.92 | 8.33 | 3.37 | **双HIT仍超标**，不是缺库存能单独解释 |
| BE `case_1b35e17087394745a322f48d97f41a49` | 14.37 | 4.32 | 7.36 | 2.89 | 一HIT一MISS，前置已消耗4秒以上 |
| COHR `case_94891cf570c4464d86541bb29b3d1038` | 13.29 | 4.11 | 7.63 | 1.54 | 双MISS，单改Operations覆盖不了问题 |
| MU `case_e563ee5cd24a42b4a852af0c06a89a53` | 11.22 | 1.67 | 4.46 | 5.42 | 双HIT，Operations准备与收尾占比突出 |

这些阶段边界可能重叠，**不相加冒充端到端耗时**；端到端按原SDK区间并集公式计算。

最慢NBIS的互斥Trade链：Effect→首owner1.510s；首owner1.986s；首owner→首SDK6.411s；两个SDK区间间隙0；末SDK→末owner0.365s；末owner0.818s，合计11.090s。不存在把两支SDK推理串行相加的问题。

注册子链1.156s中，gate累计约782ms、退避211ms，实际成功BEGIN仅约29ms。前两者属于**Scheduler进程内准入**，不能直接归给跨进程Projector。尚未拿到该历史窗口每次gate的实际持有者，不能把未闭合部分硬填成某个维护任务。

## 3. 确定瓶颈：短写owner排队与不必要CDC

### 3.1 等的不是模型，也不全是SQL执行

INTC两支已有READY：claim约394/513ms；其中一支冻结request的`RuntimeJournal.set`在短写owner队列等待 **1,567ms**，实际callback约101ms。另一支同类等待763ms。NBIS一支在准备上下文时排队1,414ms，callback205ms。

这些是`io.write.queued_ms`记录的实际调度等待，不能当外部Provider等待，也不能只看`journal.hold_ms`很短就判定路径健康。

代码仍在`write_io(journal.set, worker_requests, ...)`的串行callback中执行`NativeContent.encode`。该编码可能包含JSON遍历、hash、压缩与新内容发布/fsync。位置是确定的，**其占这1.5秒队列的具体份额尚未测完**；不能说全部都是编码。manifest/binding/request等独立提交也仍会反复进入同一owner。

### 3.2 当前Read跳过的技术输入仍产生CDC

生产近期512条Runtime source outbox记录，覆盖UTC00:11:21–00:26:25：

| namespace | 记录数 | 现行Serving处理 |
| --- | ---: | --- |
| worker_requests | 60 | serving_skipped |
| worker_receipts | 47 | serving_skipped |
| round_inputs | 33 | serving_skipped |

合计 **140/512，约27.3%**。证据是`outbox.py`的capture allowlist仍包含它们，而同文件serving读取及`serving_fields.VALUE_NAMES`会跳过它们。这是有界近时观察，不代表全日比例，更不是历史慢窗口writer持锁时间比例。

临时库用同一source迁移生成触发器：30条合成worker request写入，保留捕获时SQLite `total_changes=120`（owner行、receipt、outbox、head）；只关闭该临时库的capture触发器时为30，owner数据量相同，Serving明确跳过原捕获。这证明额外工作，不证明生产耗时必然变成四分之一。生产没有关闭任何触发器，也没有删除业务记录。

**建议P0：** 审清其他CDC消费者/归档合同后，去掉这些只为当前Read生成、而Read明确不消费的副本。必须保留Runtime原始冻结记录、paid receipt、历史request/receipt、Native引用保护与原Job恢复；同步对齐SourceSnapshot/backfill范围。`worker_invocations`用于成本/调用投影，不能一起删；交易、watch/pins和必要业务状态继续捕获。

## 4. 确定冗余：控制读取反复承担连接/Schema成本

UTC00:17:25–45的kernel窗口，Scheduler使用约 **0.90个CPU核**；Worker约0.08、Projector约0.05。该窗口晚于00:04慢突发，不能倒推当时CPU归属，但说明常态Scheduler自身仍有持续成本。

随后六次`py-spy dump --nonblocking`（每次约18–24ms，未暂停、发信号或读取locals）多次命中：

- `runtime-dispatch → _recover_existing_once → ControlRepository.get/read`；
- MainThread的`admit_runtime_revisions/current_revision_admitted`及`tick_ticker → ControlRepository.get`；
- `runtime-effects → claim_effects`的无到期任务预探测/连接退出。

`ControlRepository.read()`每次重新连接Runtime库。只读低优先级机制测量，执行相同两个小标量查询：5次新连接墙钟约8–57ms（首次CPU44.8ms）；同一连接5次约0.06–0.17ms。这里包含首个查询及schema装载，不能全称为`connect()`系统调用成本。这是生产工作量证据，不把五次实验当稳定P95。

**建议P0：** 同一调度轮、同一线程用已有只读scope批量读取控制状态，去掉进入`tick_ticker`前后的重复同义读取；同轮只做必要的一次Effect兜底探测，保留所有新结果、容量释放、失败恢复的即时wake。优先局部批读，不新增连接池、跨轮布尔缓存或定时状态同步层。实际准入、模型派发和交易提交仍在原短事务重核最新epoch/removal/analysis权限。

不能误删后台dispatcher：`background_runtime_dispatch=True`时主循环并不会再调用同一`coordinator.tick`；后台恢复与消息准入职责不同。确实有重复控制读取和calendar reconcile入口，但不是两套完整业务执行都可以直接删掉。

## 5. READY6仍会被突发耗尽，冷路径尚需缩短

最慢NBIS两支实际等待准备约4–5秒，构建与SQL排队共同组成派发前6.41秒。INTC已双HIT，仍需4.41秒从首owner走到首SDK，说明库存不是唯一瓶颈。

MISS还包括认领正在后台构建的slot。例如BE一支在00:04:38开始构建，00:04:40.996才绑定，00:04:41.934 READY；Case实际剩余等待不到1秒，不能把整个3.74秒构建墙钟全算成它的等待。

**建议顺序：** 先去上面的写入/连接工作，使已有补货和实时准备少受争用；再按真实bound→READY和Slot→SDK净时间拆Python启动、Native Fork与绑定。保持6与单后台构建者，不直接扩8/增加构建并发，不让Case等库存。干净物理预热复用若要试验，必须证明新Case工作目录、MCP capability及逻辑Thread完全隔离，不能直接循环使用带旧Case状态的Client。

## 6. 结果交接大多已快，但确认一个心跳机制风险

首次86笔可关联Job中，SDK结束→终态SQL最大约143ms，绝大多数commit→Scheduler接收低于约0.2秒。SNOW一支出现3.142秒：

- Worker SQL返回00:05:00.275928，notify为00:05:00.276169；
- client直到00:05:03.387317才开始GET；
- HTTP返回仅再29ms，解析约0.35ms。

瓶颈在GET之前，不是该笔终态SQL/JSON解析。该支接收仍早于另一支SDK结束，**本例没有把这3.142秒全部暴露到Case关键链上**。

代码复核发现：`jobs.events`共享全局Condition；心跳只在一次condition wait完整超时才发送。其他Job反复notify可以持续重置安静stream的15秒等待。离线复现：心跳100ms、其他Job每30ms通知，650ms内不发心跳，停止外部通知后才发。现有HTTPX read timeout为60秒，连接异常后固定等5秒再恢复原Job，构成一个可解释额外交接等待的机制。

**边界：** SNOW当时异常类型和实际心跳发送时钟没有留下，不能断言该次就是ReadTimeout；5秒恢复路径与时间吻合只属于推断。

**建议P1：** 心跳按每个stream上次实际发包的绝对期限计算，保留15秒周期及持久cursor，其他Job通知不延后deadline；不靠放宽read timeout。SSE断流时对已知原Job先做一次即时GET，只有该查询也不可用才沿用基础设施退避；绝不新增POST或把网络异常当404。补异常类型/发生时刻的内容无关标量，明确取消仍向上传播。当前这不是35例P95的主要贡献，不应拿它替代上面的P0减法。

## 7. 资源证据与不能作出的归因

00:00–00:10 sysstat聚合显示8核CPU约75%忙、I/O wait约1.45%，有Swap写出；00:10可用内存仍约14.8GiB。存在资源竞争，但没有本轮OOM/假死证据。没有当时逐进程样本，不能把聚合写入量全算给WAL、GC或某个线程池。

慢突发主要在00:04；本轮Projector补发提交在00:07:52，构建更晚，**不能把这些慢Case归给这个补丁构建**。Reuters会话的发布/成功也不是W3提速或变慢的直接证明。后续kernel/PID窗口只用于明确其自身时间段的责任，不回填历史未知区间。

## 8. 最小后续实施批次与验收

1. **先减真正无效的Source CDC工作，整理短写callback为SQL-only。** 核对消费者/保留合同，Native编码在既有后台read/file执行路径准备；不删除冻结/paid数据。阶段观察看callback时间、队列、gate与端到端一起变，不承诺按mutation数线性提速。
2. **批读调度控制与去同轮空探测。** 不改变即时新消息派发、现存Job恢复和授权重核，不删background recovery；不新增池或跨轮缓存。先用调用数和CPU验证实际工作量下降。
3. **再判断是否需要合并更多W3阶段提交。** 可研究manifest、binding、冻结request、admit的重叠，但只有在证明冻结顺序和原Job恢复屏障不被破坏后才合并。先做1/2，不能把它们变成新通用状态机。
4. **定向心跳/原Job恢复修正，补齐SSE异常证据。** 不提高超时，不增加250ms兜底轮询。

每批保留原并发、模型与6份库存。所有内部等待仍计入，Trade/Operations及完整Runtime分列。正式通过仍需至少100个连续完整自然Case及真实开盘覆盖、完整Runtime非SDK P95≤10秒；本轮35例的结论是**仍超标**，不是通过。

证据位于 `eval/runtime_critical_chain_release_20261119/`：`acceptance-final.json`、`slow-case-critical-chain.json`、`integrity-final.json`、`handoff-analysis.json`、`snow-handoff-trace.json`、`capture-sample.json`、`capture-mechanism.json`、`scheduler-stacks.json.gz`、`scheduler-stack-summary.json`、`read-connection-probe.json`、`heartbeat-before.json`、`sar-burst.txt`及kernel窗口。样本范围、采样时间和未知区间均保留，没有新增生产常驻诊断模块。
