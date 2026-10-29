# Runtime消息消费失效：根因、精益修复与发布验证

## 已确认根因

北京时间10月30日01:24只读取证：BE、CEG、COHR、INTC、MU、NBIS的persistent_runtime_v2游标分别停于2062、143、300、5004、5101、856，队首均被StandardMessage.body的dict→str校验挡住，待消费47/23/82/171/127/77，共527条。逐条显式解码原NativeContent引用后，正文16,271–41,988字符均可通过相同模型校验。不是正文文件丢失，也不是没有消息。

`MessageBusV2Repository._materialized_members`把两列JSON别名成relation_json/standard_json；NativeContent.row仅对明确JSON列名展开，别名不在名单，后续却直接model_validate_json。正文或嵌套字段达到16KiB会以引用落盘，小正文通过，大正文必然失败；顺序流不前进，后续新文被队首阻挡。两个单独正确的约束（薄读不展开任意列、业务需要完整正文）在边界没有接上。

源码追溯：join别名来自f7dc71723（2026-09-02）；NativeContent行白名单c0887ce84（09-14），后续00ee814cd增加definition标记识别。并非只因Scheduler416fb2fa旧于Bus2fe86d0f，当前main同一缺陷仍在，单纯重build不足。MU/COHR队首发布时间为10月28日17:11/17:14Z，早于本轮统一I/O发布，说明此次验收遗漏了既有端到端消费失败。

Read中SNOW当日11条可以从生产API读库路径返回，INTC/BE当日0、ALL各返回20条历史；Read runtime checkpoint=head且无当前source error。前端默认CURRENT_TRADING_DAY，相关ticker没有当日case会显示空，不等于清库。另有独立API查询超时，不用本修复冒充全部UI问题已解决。

## 修复边界与步骤

1. 在真正物化StreamMember/StandardMessage的业务边界，先json解析、调用已有NativeContent.decode，再model_validate；一次必要展开，不重序列化整个正文，不扩大全局row白名单，不把所有薄状态查询改为宽读。
2. 仅对选中的stream_item成员解码，保持原limit、顺序、ARTICLE_ONCE、consumer cursor和持久化去重。真实缺失/损坏仍明确失败；不skip、不挪游标、不清库、不重发旧消息。
3. 用已有真实接纳/发布fixtures覆盖跨16KiB正文、多成员及嵌套大metadata、重启读取、无关大正文不被解码、缺失内容不推进游标；再通过Scheduler→Runtime执行入口验证case接纳与游标推进、二次tick不重消费。先复现红灯再修改。
4. 同时审查同模块其它JSON别名→模型边界，限定修复实际存在的漏解码，不新建兼容层/恢复队列。Owner发布时点与Runtime实际消费时点分开，今后观察同时跟踪游标、真实case和UI，不再仅PUBLISHED标签证明可消费。
5. 相关测试与冻结已提交归档Review，追加changelog，推送main。从正式release归档build相关生产消费者。优先Scheduler与Bus；核对BODY legacy后链路与MonitoringTool实际角色，更新必要消费者，保留Profile、数据库、inflight W作业及现有恢复协议，不夹带其它会话未提交交易改动。
6. 发布前后保存相同游标/队首/积压账；逐个证明6ticker从原队首可读、游标实际增长、Runtime case/去重或明确历史接纳决定正常落盘，并核对Read可见。旧文可能被既有业务窗口拒绝，不强行触发历史交易；禁止跳过真正应处理新消息。
7. 观察资源及本地编排跨度，记录积压自然恢复与新稿。恢复两小时一次、至少24小时只读观察，以最终修复版本稳定生效起点明确续计，旧证据和版本切换保留。

## 初始分离项与生产恢复后的调整

W3 claim已有6个真实receipt，claim跨度mean3.646秒/max8.011秒，EXPLAIN为SCAN runtime_v2_effects+临时排序，约17,532历史rowid；索引/OR历史扫描属于独立非LLM耗时问题。当前优先修复导致完全不消费的确定性契约错误，不在紧急补丁里重写W1/W2/W3调度。

Site匿名内存增长、Reuters/IBKR/Calcalist/DIGITIMES访问失败和API慢查询继续记录，不用提高全局并发或延长超时掩盖。

## 实施/验收记录

待补：代码、红绿回归、正式版本与逐ticker生产续作证据。原始故障证据在`eval/lean-architecture-observation-20261029/20261029T171819Z/`；本次补丁证据在`eval/runtime-stream-consumption-repair-20261030/`。


### 开发验证与Review

- 新增4项：即时/缓冲两种大正文及嵌套metadata跨重启传入Scheduler（原游标和重复tick验证）；真实SQLite Runtime执行并保留唯一case；limit=1不加载后续缺失大正文，遇到真正缺失时游标不动。旧实现先红（同生产body dict→str），补丁后4项通过。
- 组合套件首跑64通过、11失败：10条为旧CPU admission测试桩缺少10月28日已引入的current_revision_admitted，1条Benzinga精确字典断言未包含既有validated_request_ids。未修改对应生产逻辑，更新桩为当前revision+worker ACK/max_age语义，未到期ACK复用（不要求每tick重写ACK），mock无发网证据仍断言空ID；相关合同重跑另记。
- 除Stream成员join外，同仓库其它实际模型别名均为payload_json，已在原codec名单。没有拓宽NativeContent.row白名单或增加任意列全对象解码；新代码每个选中对象JSON解析/解码一次，避免text展开→json dump→再次模型JSON解析。
- 实际消费者：Scheduler负责消费、Bus record_consumable会读流，BODY legacy finalize复用Bus接纳，Worker默认工具注册的monitoring.recent_events也会读流。用一份正式backend镜像更新以上4角色；API只查Read，不为此无关重启。Worker滚动沿现有持久化续作/进程归属协议，不添加W门禁。

- 最终合同重跑19/19通过；精确fa683526已提交归档17/17通过（包含新4项、admission12项及原Scheduler交接）。组合首轮其余64项通过；失败与fixture纠偏均保留XML，不宣称首轮全绿。已同步origin/main，正式四角色release进行中。


### 第二阶段：接回负载暴露的高频历史领取

首补丁fa683526于17:43:34Z正式Scheduler启动；17:44后BE/CEG/COHR原游标已推进、新case及Read出现。17:47内存可用约1.74GiB、IO PSI显著升高，17:52换页仍活跃，不能就此宣称稳定。恢复后的正确业务负载需要原有W容量，未暂停W或降源周期。

将已证实的effect领取全表扫描纳入紧急修复：PENDING/PENDING_RETRY沿已有部分索引取前N；RUNNING新增仅effect_id的非终态部分索引，租约判断只读取运行中行；两个互斥集合各取前N后按原available_at,rowid全局合并前N。BEGIN IMMEDIATE、原lease_token递增、120秒租约、legacy updated_at过期回收、重试/优先顺序保持不变。索引不含宽payload/JSON，不给续租增加大JSON索引维护。

成本测试用2000条宽终态历史、5个真实活跃边界和SQLite VM工作量预算验证，领取不再随终态历史增长；两独立Repository并发不会领取同一effect。与大正文4项及W3 v4/故障合同合跑32通过、1个旧CLOSED recovery测试失败；在精确fa683526归档同样复现该失败，不通过改已存在W调度行为掩盖。新补丁只需正式重建Scheduler，保持已在途Worker/BODY/Bus。

不能把此次claim减法直接等同于全部内存/I/O压力已解决，后续同时记录真实恢复成本、case进展与资源。


### 第二阶段发布切换的实际中断

Scheduler配置20分钟stop_grace_period，service.close等待所有提交case/维护/W3线程排空；SIGTERM停止新ticker循环，但既有维护步骤和工作轮次仍可继续启动其已排队子步骤。此次切换停在Compose Recreate，不能把该状态当新版本已运行。

18:00:55Z先保存Worker实际running job IDs（5个，含4个runtime-maintain-O2及1个ready作业）、Runtime RUNNING任务及effect租约。只对17:43:34Z/指定旧镜像的**退役Scheduler控制容器ID**核验后终止，交给已在执行的正式Compose发布替换；Worker/Chrome/数据卷/游标均未停止或删除。在途任务沿原持久化run_id/Worker receipt及lease续作，不新建业务副本。需后续对这些原ID状态和case/effect实际进展逐一核验；不能仅因新的进程启动就声称无遗漏/无重复模型尝试。


## 生产验证进展与宿主再次失联（未完成验收）

- fa683526正式Scheduler17:43:34Z启动，Bus/BODY/Worker同期正式更新；6条原队首均可读取，未改游标或删除旧消息。17:52:21Z游标BE2110、CEG168、COHR383、INTC5028、MU5201、NBIS881，全部较原值推进。
- 17:55:39Z起新建可见case包括BE12、CEG12、COHR14、MU6、NBIS11、SNOW1；18:02:19Z前100条近期case样本含BE18、CEG18、COHR19、MU12、NBIS11、SNOW1，共79，最新20项均可在Read按主键找到。INTC尚未证明创建新case；其流已过原堵头，但仍有147条积压，必须继续验证，不能把“reader OK”当恢复完成。
- b233414d正式Scheduler18:01:38.420816677Z启动，Bus/BODY/Worker仍fa683526兼容版本；生产EXPLAIN已沿pending部分索引和仅RUNNING部分索引，有界只读候选样本7.68ms/0.34ms。这不是实际claim全跨度，更不代表全部负载已稳定。冻结提交24项通过。
- 17:47:38Z宿主可用内存约1.74GiB，Worker匿名从发布前973MiB增至3278MiB，Python/Codex进程由11个增至44个左右（19Python、16Codex、9code-mode），当时jobs.running只有3。Site匿名3614MiB、Supervisor匿名3175MiB，二者仍占约6.63GiB。较少运行job不等于这些额外控制/准备进程都必须保留；需要按capsule busy/prepared/ready/bound及原owner进一步核对，不能仅凭进程数即杀进程或减少W容量。
- 17:52:21Z抽样可用1573MiB、Swap1676MiB，vmstat瞬时读盘约145508KiB/s、换入39656KiB/s、iowait23%；资源再度进入回收/重读压力。case恢复释放了之前被堵头隐蔽的真实负载，先前资源低负载/Owner发布统计不足以证明全链路容量。
- 18:02之后API探针、Docker日志及SSH新会话无返回，连续server not responding。18:06尝试仅应急停止Site/Chrome Supervisor释放资源，SSH未成功，因此**未确认停服命令执行**。未声称生产健康或停服成功。已请用户通过云控制台确认/恢复主机；当前无法完成余下生产验收。

### 恢复访问后的优先步骤

1. 第一时间核验主机/容器epoch，临时控制浏览器服务以保留取证窗口与Runtime/Worker；停采缺口照实记，不作为正常频率达标。
2. 保存内核/sysstat、cgroup refault/Swap及Worker capsule owner、busy/prepared/ready_slot、实际job/thread/进程映射，确认16个Codex/19个Python究竟在途必要、排队预备、已完成未退还是遗留子进程。Site也按当前Identity driver实测驻留核算，不恢复共享全局故障域。
3. 已完成job应及时释放真实运行环境，保留持久化thread/receipt及必要READY容量；预备与实际Worker准入的顺序要避免先常驻大量已fork的SDK、再等执行槽。此方向需实际映射证明后实施，不能先改TTL/提高并发/硬挡W，或仅凭本次样本宣称全为泄漏。
4. 逐一核对18:00:55快照的5个Worker job IDs、CASE任务和2个effect leases，确认原身份续作/终态；验INTC新case、其余原游标及新消息延迟，再核对API DTO完整路径。当前已确证流解析修复有效、领取历史扫描已去掉，但宿主不稳定及部分后续交付未完成，业务恢复验收仍未通过。

### 第三阶段：结果确认写锁与SSH恢复

18:13:16Z SSH恢复，boot未变，无需用户重启，之前应急停止Site/Supervisor命令未成功，服务仍在运行。18:15:02日志明确`w3_execution._ack`总8.481秒、持写锁8.255秒，其间接纳/租约写入等待约7–8秒。原代码对整个worker_receipts命名空间JSON反查job_id，并在写事务内展开/压缩结果；不是CPU/IO高的泛泛推测，是确认的高频历史读取与写锁放大。

修复：ReceiptWorker在既有invocation写入receipt_key，不另建台账；ACK仅按job_id和receipt_key主键读两个当前对象，在写锁外解码NativeContent、去除已确认的final_response/telemetry.events并编码；写事务只做有条件更新、删除对应请求及ACK待办。以原payload作CAS，避免晚ACK覆盖新generation，失败仍由既有w3_ack_pending续作，不重跑模型。既有非模板W3由原确定命名推导键；极少没有scope的旧模板不做历史反查，只压缩精确invocation，保留原receipt/request供既有生命周期收尾，不清库/扫历史迁移。

新增成本回归用2000条历史回执和VM预算证明只读当前键，覆盖大NativeContent在写锁外展开、旧ACK不删除新generation请求、外部ACK失败待办、旧格式确定键。38项相关测试通过。测试首轮2失败是预算误施加到初始化建索引，修正fixture在预算前完成初始化，不改业务实现以迎合测试。

18:28:15Z近期100条case样本已出现INTC4、COHR35、MU24、NBIS27等，说明原6ticker均已越过堵头并真实建case；样本上限100不能当全量计数。资源仍未稳定：18:26:43Z Worker匿名约2.89GiB（6 running/5queued），Site约4.08GiB、Supervisor约2.74GiB，前三项合计约9.7GiB，另有Scheduler/API/宿主工作集；大量文件refault/换页与业务峰值同时发生。下一步按同epoch差分与真实进程/任务归属核算，不能宣称修复ACK等于解决全部资源问题。
