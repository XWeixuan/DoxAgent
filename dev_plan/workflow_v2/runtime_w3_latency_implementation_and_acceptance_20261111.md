# W3 时效优化实施与生产验收报告（2026-11-11）

本轮已按实际工作量设定预算、实施有证据的优化、合入 origin/main 并正式发布，完成固定版本10分钟资源观察。**完整Case内部P95仍为7.192秒，5秒硬指标未通过；本轮不能作为性能目标已经完成。****不能以回归通过、单阶段低于5秒或少数快Case，宣布完整Case的非SDK P95≤5秒已经达标。**

## 1. 独立工程预算与计量

| 必要工作与典型工作量 | 预期P50 | 预期P95 |
| --- | ---: | ---: |
| 内存判断、Future回调、事件通知 | 1ms | 10ms |
| Effect索引领取与短线程派发 | 20ms | 100ms |
| 约1–3MB冻结输入读取及准备 | 60ms | 200ms |
| context绑定、小文件原子发布 | 50ms | 200ms |
| 已兼容Ready领取 | 10ms | 50ms |
| 必要fork/resume控制RPC | 150ms | 600ms |
| 物理进程、模块及SDK初始化 | 300ms | 800ms |
| Job受理到启动 | 10ms | 100ms |
| 数KB结果解析与结构校验 | 2ms | 20ms |
| 回执及必要短事务业务提交 | 40ms | 200ms |
| 终态通知和资源释放 | 5ms | 50ms |

Trade预算P50/P95=0.8/2.2秒，Operations=0.6/1.8秒，完整Case=1.5/4.5秒，保留0.5秒余量。这些是正常工作量下的工程目标，**不是对当前实测的合理化或已达成承诺**。模块图确实很大，但不能据此将几秒冷启动定义为正常。

完整Case计量从Trade Effect产生至Operations owner实际返回，扣除全部SDK Turn区间的并集，并把重叠的DoxAgent本地研究工具处理加回。Operations从可执行时刻开始，Trade/Operations重叠收尾不重复加总；不相加阶段P95。SDK Turn是边界包络，含其原生工具/网络，不能称纯模型推理耗时。fork/resume、冷启动、内部排队/锁等待/重试/退避全部留在内部。无供应商原始期限证据时，外部Provider豁免为0，普通429自建60秒退避不扣除。

缺少SDK尝试、业务commit真实返回、Effect/owner尾部边界、日志或元数据覆盖的Case保留为不完整，不纳入“通过”样本；同时保留其已知等待。最终还需正常开盘高峰、建议连续1小时且每阶段至少100例。

## 2. 修复前问题、方向与已实现路径

### 2.1 领取、公共准备、Ready和冷启动

- 原空Effect轮询也进入writer；实时领取之后还等待后台claim。现在先用有索引的只读空探测，实际领取仍在原写事务重查和递增租约；已领取实时任务先启动，再处理原有8个后台位置。没有W3人为并行门槛或新线程池。
- 已READY模板查询和未变化公共marker重复占writer。现在READY只读快路径，缺失/未就绪才走原创建CAS；marker按变化写，保留30秒configure及原补池机制，Worker重启不会被旧成功marker阻止恢复。兼容键、profile、租约与失败恢复保持。
- 实际镜像上的应用/SDK源码在冷进程重复编译。现在最终源码overlay后按实际导入图编译checked-hash pyc，核验源码哈希、路径及运行用户可读性；不增加常驻预热进程。Scheduler另编译实际日历/pandas依赖。
- W3只消费resume响应中的线程ID，但此前接收完整历史回包。仅W3设置`include_turns=False`，逻辑线程和模型会话上下文不变。实际安装SDK签名、镜像内契约和官方文档均核对过。[官方App Server契约](https://developers.openai.com/codex/app-server/)
- 保留目标线程resident与健康unbound复用、Trade→Operations handoff和取消清理；无证据时不扩大池、TTL或新增跨线程复用分支。

### 2.2 准备I/O与结果提交

- 小研究pack/context/materialize与无关inventory争全局I/O槽位。已有按pack/run锁及原子发布的路径改用现有取消安全`blocking`，只移除冗余外层串行位置；密钥、scope、对象锁和资源归属保障保持。snapshot、inventory和ACK镜像原保护保留。
- Operations原有候选分配、daily记录、关联、路由镜像及owner释放多次独立写入。现在在writer外准备不可变大内容，在原fenced业务事务中一次完成必要本地提交，保留`result_saved`屏障、候选幂等身份和动作幂等键。回滚不消耗编号、不留半完成动作；没有把HTTP/SDK/行情/ACK装进事务。
- Trade保持交易输出、stage提交、Case字段与Operations Effect创建同一事务；结果先持久化，再业务提交。ACK提交后异步且可恢复。
- Effect完成后，原收尾扫描Effects、展开整Case、全量重写终态并再次读取投影。现在原Effect事务直接更新相同终态标量；终态投影按主键读取现有投影字段和turn COUNT，不展开research。不覆盖并发Operations结果，不新增投影Schema/队列/缓存，outbox语义保持。
- Trade/Operations此前对未变化冻结资料再次序列化、哈希和编码。正常阶段提交现在只预编码W3结果，再在原UPDATE中patch该阶段和原有控制字段，保留当前行冻结Native引用及其他字段。显式历史恢复helper仍保持完整恢复语义。
- `get_case`原Native路径为展开→完整JSON编码→Pydantic再解析。现在Native展开后直接做完整RuntimeCase验证，非Native保留原JSON校验；仍每次读当前行，不引入对象缓存或跳过业务校验。

### 2.3 日历冷路径

首个新版慢Case在payload区间耗时3.306秒，Case读取只有101ms、没有相应长journal写锁。只读重放证实主要为日历依赖导入和41年日程构造。现在按查询年份前后各一年构造必要日程，单实例仍只有一份memo；跨窗口更换该memo，保留原2000–2040支持边界、缓存版本、覆盖配置和回退语义。退出时间、DST、早收市、跨年及Sandy停市等通过旧完整日程逐日等价验证。

### 2.4 观测不冒充优化

复用已有256项有界可丢诊断通道和Docker日志轮转，记录owner真实尾部、异步phase、gate/SQLite BEGIN、执行器等待、回执/Future/解析/commit区间；不新增诊断数据库写入。Worker补标准WARNING输出，仅W3边界摘要启用INFO，不开全局详细日志。计时覆盖用于验证以上实际执行路径精简，不把时间戳提前来美化耗时。

## 3. 已证实的因果收益与限制

| 只读或隔离同条件探针 | 原路径 | 优化路径 | 证据边界 |
| --- | ---: | ---: | --- |
| 无关inventory持槽200ms时，小研究包HTTP handler | 231.13ms | 13.23ms | 实际ASGI隔离探针，无SDK |
| 36轮线程摘要查询/完整回包 | 1.60s、1.52MB | 首读36ms、30KB | 查询不resume、不调用模型，不能解释所有resume延迟 |
| 同Case冷trading_context | 4.382s | 部署后2.048s | 生产SQLite只读，日历缓存写模拟在内存；相同退出时刻 |
| 隔离日历依赖导入AB | 3117ms CPU2139ms | 813ms CPU686ms | 仅编译/tmp，调度噪声仍存在 |
| 同Case阶段提交前编码 | 273.56ms CPU219.62ms | 0.335ms CPU0.336ms | 生产零写，编码落隔离/tmp |
| Native读取例A完整合同等价 | 186.14ms | 52.81ms | 零写、不新增缓存 |
| Native读取例B完整合同等价 | 59.10ms | 32.07ms | 同上 |

部署前候选断网探针仍有Schema导入386ms+SDK模块导入1362ms，超过800ms冷启动预算。字节码消除了重复编译，但不能声称已经解决第三方模型构建和全部初始化开销。

## 4. 生产分窗结果：负面样本同样保留

| 版本/窗口 | Trade内部P50/P95 | Operations内部P50/P95 | 完整Case内部P50/P95 | 说明 |
| --- | --- | --- | --- | --- |
| 原55例冻结基线 | 5.16/13.96s | 2.92/7.38s | 9.27/21.05s | 下界，原观测覆盖不完整 |
| 03:50–03:59:47，首版Scheduler/旧Worker | 16.625/24.077s | 3.387/14.115s | 18.502/25.902s | 11例；包含外部停派恢复突发，不能当稳定AB |
| 04:29:43同批版本8a7fdeb7，采至04:44:58 | 5.131/7.154s（8例） | 2.966/4.446s（7例） | 8.041/10.803s（7例） | 另1例未完；明确不达标 |
| 05:02:03第二轮29fecbc1，冻结至05:10:07 | 3.004/4.123s（4例） | 1.473/2.505s（3例） | 5.284/5.406s（3例） | 第4例在截止时未提交/缺owner，跨R3发布后才提交；下文另列其下界，不能当作快例 |

以上时间均北京时间。并行会话在04:48替换Worker启用Luna Fast，05:10又发布R3模型迁移并重启Scheduler；因此相邻窗口不是控制变量齐全的AB试验，不把全部改善归给本轮代码。只读同条件探针用于确认具体机制收益。

中间窗口的10.33/8.70秒Ready等待，按scope、创建/绑定/ready时间重建，当时没有其他可选READY/BUILDING对象；没有证据是错误slot排序，不为此新增选择机制。另有1.67秒SQLite BEGIN争用和本地writer等待，这些保留为内部，不把整个慢Case笼统归因SQLite。

## 5. 发布、正确性与资源

- 按canonical组件发布锁和既有状态文件执行，候选基于实际已接受镜像，逐文件hash校验；保留先前已经部署的存储生命周期、维护协程、收益启动修复以及并行会话正式发布的模型配置。
- 初次Worker发布用户授权临时暂停六个非W3生产者；实时Scheduler继续工作。等待全部已受理Job结束后替换Worker，04:29:45恢复原六个容器。暂停前后10个相关D3 Job均成功、身份未变。该临时窗口不进入长期调度机制。
- Scheduler后续单角色发布不再暂停非W3，也不重启Worker。发布前停止已核验的不可变容器ID，避免Compose继承1200秒停止宽限。其他会话占发布锁时不绕过；正式镜像变化则重建基于新实际镜像的候选。
- 已完成212项首轮回归，日历/字段提交/终态投影等合并回归218项通过；Native读取及恢复161项通过；再次合并R3与发布工具变更后49项通过、2项平台相关跳过。重复执行的测试不累加成唯一测试数。
- 覆盖原Job受理/明确404/未知防重派、Pause不拦成功消费、结果保存屏障、租约/fencing、事务整体回滚、候选编号幂等、并发Operations结果不被终态覆盖、SSE/Future续接、MCP与取消清理、日历等价及Native完整合同。
- 04:30:30–04:40:30固定8a7fdeb7资源窗41次采样：可用RAM最低11.421GiB；Scheduler/Worker内存峰值0.951/4.885GiB；kernel OOM增量0，两者无OOM/自动重启。Swap-in/out增量18656/46025页，仍有换页，不能声称“没有资源竞争”。同时恢复了后台研究，不能把全部内存变化归给本轮优化。
- 05:02开始的第二窗包含05:10外部R3发布，按多版本窗口保留，不冒充最终版本连续10分钟。

## 6. 保留与未实施边界

原Job、Effect、阶段、线程身份及恢复保持；成功结果消费不受新模型请求准入约束。保留leases、fencing、原子性、结果先保存、同线程handoff、MCP隔离、冻结上下文及两阶段职责。没有改W1/W2路由、W3决策、Prompt/Skill、Agent Schema和交易规则；并行会话的模型迁移属于独立发布，本轮仅保留其实际配置。

没有降低实时并发、扩大Ready/常驻池或线程池，没有提高SQLite timeout/重试/退避，也未新增Redis、业务缓存、队列或状态机。未证实的跨线程client复用、ACK去重、slot排序先不改；第三方SDK模型构建、冷resume控制RPC及真实高峰争用继续按细分证据评估。

## 7. 最终版本验收


正式版本于北京时间05:15:00发布：Scheduler `62d6be3311df`，源码提交`b8d9ed13`；Worker保持并行会话已经正式启用Fast的`14121db49ff6`（`17c8e4b6`），此次不重启。最终22个相关源码文件与合并后的main指纹匹配；实际解释器为`/app/.venv/bin/python`，SDK/calendar依赖图的checked-hash pyc按真实路径验证，非假定系统site-packages路径。

05:15:02–05:25:02固定版本连续600秒、41次采样、无采集错误：可用RAM最低9.713GiB，Scheduler/Worker内存峰值0.731/6.570GiB；两角色版本及启动时间不变、自动restart=0、OOMKilled=false、kernel OOM无增量。全机swap-in/out增量约174.7/385.9MiB，**仍存在换页与资源竞争，不能宣称资源问题全部消失**。此时Job由4增至10，包含原有后台工作和新实时W3；不把全部RSS变化当作优化因果收益。没有扩大任何常驻目标。

入组区间为05:15:00–05:25:02，05:25:05截断时共11个Case：Trade完成8、未完3；Operations完成4、未完/未启动7。无选择性丢弃，日志和元数据预算均未截断；未完不等于失败。

| 内部指标（已完成且证据齐全子集） | 样本数 | P50 | P95 | >5秒 |
| --- | ---: | ---: | ---: | ---: |
| Trade | 8 | 2.789s | 4.476s | 0/8 |
| Operations | 4 | 2.703s | 3.067s | 0/4 |
| 完整Case | 4 | 5.307s | **7.192s** | **3/4** |

**结论：发布/基础正确性与本次资源观察通过，完整Case时效未通过；单阶段暂低于5秒不等于累计达标，更不等于高峰稳定P95通过。** 原55例21.05秒与当前7.192秒不是严格AB，仅可作为不同生产窗口现象比较。

最终最慢Case `case_5357abcc6bbd494db906f19095c4cb37` 的直接证据：

- Trade内部4.476s：到Job1.178s，队列31ms，启动70ms，Worker标记结束→业务commit 2.709s，尾部352ms。
- 其中Worker结束21:23:33.312732 UTC，Scheduler收到结果21:23:35.102217，间隔**1.789s**；收到后到commit约920ms。Future完成到续接派发0.015ms、线程启动2.872ms，不能把1.789s归因于Future。
- Trade续接Case读取341ms，业务阶段本体261ms，Effect保存167ms；相应SQLite-BEGIN只有0.619ms、本地gate 0.048ms，**不支持把该阶段全部归因于SQLite锁**。
- Operations内部3.067s，其中三次本地研究工具208/246/337ms，总计约0.791s已经加回内部，不能因发生在SDK Turn中而扣除。它们执行器排队仅约0.2–3.5ms，主要是实际读取/解码工作而非共享线程池排队。

需继续针对两个剩余点探测后优化，优先级不再是Future重写、增大池或改slot排序：

1. **Worker终态持久化/通知→SSE/最终GET→Scheduler接收。** 当前`finished_at`在`_update`编码及SQL返回前赋值，因此1.789s只能证明整个结果可见/交接区间慢，尚不能区分SQLite提交、通知、HTTP回包或事件循环竞争。下一步在现有有界诊断内补终态SQL真实返回、notify及最终GET收发边界，再针对占比改动；禁止把它当Provider等待。Worker自本次启动以来曾出现3.066s最大事件循环延迟，但现有摘要没有该尖峰时间，不能断言它造成这一例。
2. **仍超预算的Case/本地研究读取和提交收尾。** Native文本往返已消除，但压力下仍有167–341ms Case读取、200–337ms研究工具处理及多笔几十毫秒控制读写。先测实际解码字节数、CPU/墙钟及重复读对象，确认可使用现有冻结引用/短事务减少哪部分工作，再决定精简；不添加整Case常驻缓存、不放宽超时或增加重试，也不删业务校验来达标。

跨发布慢例必须单独保留：`case_b448eb2624794de581b9ae0dc977d071` 两个SDK Job均成功，Operations Worker于05:09:57.481结束，跨独立R3 Scheduler发布后于05:11:24.974提交。结果→stage commit下界**87.492秒**，完整Case内部下界**90.751秒**，缺owner最终返回，绝不能当作无耗时或Provider等待。这与最终固定版本cohort分开列示，但仍是实际生产超标；不据此声称全部87秒都已完成子段归因。

发布脚本已补一个仅供运维使用的只读空闲检查：既查Job，还查活跃结果owner及当前已可执行的W3 Effect；Job成功不代表结果已消费。发现未收尾就保持服务，不停止Scheduler，不增加运行中W3准入门槛。它不是原子停派屏障，新消息到达竞态仍依赖原租约/Job恢复保障；正式发布造成的任何等待仍按内部计入，不能豁免。9项发布边界与下界验收回归通过；此项只更新主机发布工具，不需再重启容器。

## 8. 证据与复核

- [最终版本/逐阶段统计/资源摘要](../../eval/w3_latency_implementation_20261111/final-production-summary.json)、[22文件版本指纹](../../eval/w3_latency_implementation_20261111/final-version-proof.json)。
- [第一轮8a生产窗](../../eval/w3_latency_implementation_20261111/cohort-8a-summary.json)、[第二轮29f生产窗](../../eval/w3_latency_implementation_20261111/cohort-29f-summary.json)、[跨发布耗时下界](../../eval/w3_latency_implementation_20261111/cross-release-lower-bound.json)。
- [218项合并回归](../../eval/w3_latency_implementation_20261111/merged-followup-regression.xml)、[Native/恢复161项](../../eval/w3_latency_implementation_20261111/decode-regression.xml)、[发布与下界9项](../../eval/w3_latency_implementation_20261111/publication-boundary-regression.xml)。
- 同目录保留只读日历、编码、Native读取、线程历史及隔离I/O因果探针。完整带trace原始账本保存在主机私有`/var/lib/doxagent/releases/w3-latency-*/`，不导出完整Prompt、消息正文或密钥。
