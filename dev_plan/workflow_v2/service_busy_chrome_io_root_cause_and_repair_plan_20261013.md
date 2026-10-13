# 2026-10-13 SERVICE_BUSY、Chrome 生命周期与 I/O 异常诊断及修复方案

检查窗口：北京时间 02:21–02:41。本轮为诊断与方案，不实施生产变更。没有重启、关闭页面/浏览器、停止研究任务、迁移数据库或清理数据。

方案复审：按用户要求，以正常业务稳定运行、修复已确认缺陷为设计基准。删除新增内存压力门控、实例定期回收、磁盘余量门槛、研究Worker资源包络及跨驱动持久化target台账。本次核心实施范围收敛到Site Access页面/连接生命周期和API读取worker初始化；以下现场数据保留为诊断证据，不转换为新的业务准入条件。

## 1. 结论及可信度

本次前端失败的直接原因已经确定：普通读取池的两名 worker 硬超时被回收；替补 worker 启动时尝试对 Message Bus 数据库执行 `BEGIN IMMEDIATE`/migration，因 BUSY 反复退出，页面请求排队 1 秒后返回 SERVICE_BUSY。不是根据前端文字猜测，实际日志包含路由、request_id、hard_timeout、capacity_timeout 和完整启动栈。

更上游存在真实的 Chrome 生命周期异常和容器内内存挤压，而不是正常业务负载下一个合理的“有点忙”：

1. 浏览器已有 target 与 Site Access 自己的页面/任务记账失配；页面申请与失败收尾有确定代码缺口，并完成线上同 SHA 源码的离线故障注入。
2. Chrome 总容器在固定 5 GiB 内存/1 GiB Swap 限制内发生大量内存回收，最终 memcg OOM；系统全局仍有空闲内存不能解除这个局部限制。
3. 同期磁盘持续高读取与长等待拖慢全机；OOM 杀掉 Yahoo 树下 renderer 后压力显著下降，API 自动恢复。该时间关系、内核 dump 和回收统计支持 Chrome 工作集挤压/文件页反复回读是重要上游因素。

不能将最后一项扩大成已经证明“峰值所有磁盘读都由 Chrome 发起”：没有事发前逐容器 I/O 差分历史。当前无完整 target 生命周期台账，无法逐一证明 34 个空白页面都是业务泄漏，也无法从已死亡 renderer 反推确切创建请求和最初触发页面。修复方案以已确认的代码缺口为主，并把这些边界纳入故障注入与验收。

## 2. 事发经过与现场证据

| 北京时间 | 可核实事实 | 含义 |
|---|---|---|
| 10/12 20:45 起 | Site Access 已有 Yahoo prewarm timeout；20:49 起有 document gate cleanup timeout | 浏览器控制异常早于正式 V2.1 后端切换 |
| 10/12 20:52:57 | 消息源验收快照 stalled=false、driver epoch=1；Barron's 当时请求年龄 39.7s/预算45s | 当时有限检查通过，不能证明后来没有挂死 |
| 约 10/12 21:08 | 根据 02:31:24 诊断中的请求年龄反推，一条 Barron's 请求开始；到检查时仍卡在 `context.new_page()` | 同一请求超过5小时，45秒预算失效，早于22:15 Bus切换和22:24 API切换 |
| 10/12 22:15 / 22:24 | V2.1 Bus / r2 API 容器开始运行 | 不能把先发生的浏览器挂死直接归因于此次 API 合同改造 |
| 10/13 00:49:50 | 日志记录多个 `raw CDP unmanaged target resume failed`，含 `C5F2BAE612E595C3C4647327B04ABB8B` 等 | 失败 target ID 在02:34的公共市场空白页面列表中仍存在；不是仅瞬间请求失败 |
| 01:00–02:20 | sysstat 每10分钟样本：vda读约159744 KiB/s，队列约237–250，await约214–436ms | 持续磁盘读拥塞，不是一次慢查询 |
| 02:10 / 02:20 | sysstat blocked=118 / 202；load 137 / 121 | blocked统计是内核任务/线程，不应表述为202个独立业务进程 |
| 02:19:25–29 | read-context、capabilities执行约4004ms硬超时，两名worker回收；随后连续capacity_timeout | 轻量能力接口也受影响，故障并非只出现在新版研究正文 |
| 02:20–02:21:54 | 替补worker反复启动失败；栈为 `create_app -> MonitoringTermsControl.__init__ -> BEGIN IMMEDIATE -> database is locked` | 恢复路径依赖无关数据库写锁，使故障持续 |
| 02:21:41 | 内核 `CONSTRAINT_MEMCG` OOM，杀掉Chrome PID2516824；该PID的现场父进程1691942属于yahoo-main树 | 这是Chrome容器局部OOM，不是全机物理内存耗尽 |
| 02:23–02:24 | API readyz恢复200；read=2/2，stream=1/1，control=1/1，无排队；API restart_count=0 | 自愈，不是本轮操作修好；不能用健康恢复代替机制修复 |

### 2.1 OOM 当时实际内存组成

内核 dump：Chrome容器 usage=5,242,776 KiB，limit=5,242,880 KiB；Swap usage=1,027,004 KiB，limit=1,048,576 KiB。anon=4,196,380,672 B，file=699,637,760 B，其中shmem=592,113,664 B，kernel=387,858,432 B。file包含shmem，不能重复相加。

因此占用以匿名内存为主，不能用“只是可回收缓存”解释。恢复后cgroup memory.stat仍记录约8.89亿次文件页refault、14.46亿次direct scan，Chrome io.stat累计读取约3.52TB；这些是容器生命周期累计值，不是本次事件独占值。sysstat同时显示01:00–02:20换页吞吐很低，说明不能把156MiB/s磁盘读全称为swap风暴；文件工作集反复回读更符合目前证据。

先前单次 `/proc/<zygote>/io` 出现数GiB跳变不能当成实时读吞吐：父进程回收子进程可能累加I/O记账。本报告排除该样本作为峰值归因依据。恢复后的同步cgroup差分显示Chrome读取已降到约0.27MiB/s。

### 2.2 实例、页面与内存归属

02:34前后，通过Chrome只读 `/json/list` 检查，没有附加第二控制器，也没有关闭target：

| Profile | CDP端口 | page数 | about:blank数 | 02:31附近进程树PSS / SwapPSS MiB |
|---|---:|---:|---:|---:|
| yahoo-main | 9224 | 11 | 7 | 2122.5 / 418.9 |
| public-markets-nl | 9227 | 16 | 15 | 468.9 / 0.6 |
| dowjones-main | 9222 | 6 | 6 | 382.5 / 211.2 |
| digitimes-nl-1 | 9226 | 5 | 4 | 461.9 / 53.1 |
| seeking-alpha-main | 9223 | 1 | 1 | 151.2 / 85.0 |
| digitimes-de-1 | 9228 | 1 | 1 | 224.6 / 19.5 |
| 合计 | | 40 | 34 | 非同一瞬间，不用于精确总额 |

PSS分摊共享页，不能用各进程RSS相加当物理占用。Yahoo主进程本身RSS约1.1GiB，树PSS约2.1GiB，是当前最大单实例工作集。被杀renderer VmSize约1.5TB是Chrome虚拟地址预留，不能声称它占用1.5TB物理内存；OOM日志实际anon RSS约594MiB。

业务配置并发上限4只控制已租出的业务permit，不约束历史遗留target或人工页；所以40减4也不是确切泄漏量。确定异常是：长时间未完成申请、无法resume的同一批target持续存在，以及diagnostics active_pages=0仍有挂死业务任务。现场driver epoch=11、attachments=4，driver仍READY但整体stalled=true，控制驱动轮换没有消除真实Chrome占用。

## 3. 已定位的代码缺陷

### A. 页面申请与实际target所有权不完整

线上 `external_runtime.py` SHA256为 `5dedc506b4964e02d76f73fcd5f61a91ead7ac52d8e11c4833a682d029ec7b17`，与消息源发布staging一致；本地当前源码不同，后续不能直接用脏树整文件覆盖。

`ExternalChromeRuntime.page()` 先 `context.new_page()`，成功返回后才增加active_pages/创建lease；失败只归还permit，不回收Chrome已创建但控制器尚未接收的target。`deadline`在该方法内仅用于等待page semaphore，没有覆盖new_page；外层45s预算在现场也没有完成退出，实际取消失效细节仍需隔离复现。

导航gate启动后才给Page登记target_id；更早阶段创建失败的空白target没有同等的归属/清理保证。active_pages只数成功lease，因此不能作为“无页面、可以恢复”的充分条件。

### B. 恢复收尾失败会丢失连接/target的管理引用

`_detach_driver()`先clear entries；然后在同一6秒try内逐target close和raw-CDP close。任一target close异常/超时都可能跳过raw-CDP close，旧entry已不再可查。`_RawBrowserCDP`对所有page启用autoAttach/waitForDebugger；不受治理target resume连续失败仅记录日志，没有关闭该失败控制连接的完整保证。这能留下失去管理的控制/页面状态，但本轮socket快照没有发现几十条连接，不能声称当前存在大量CDP连接泄漏。

仅轮换Playwright驱动会保留Chrome进程、Profile和既有target，因此不能修复未收尾的页面。ALWAYS_ON实例长期运行、memory.high=max本身不是已确认的代码缺陷；正常情况下应允许浏览器保持常驻。此次OOM不能推出必须新增实例内存保护或按年龄重启。

### C. 读取池恢复强耦合写库迁移

`create_app(query_worker=True)`仍创建MonitoringTermsControl，其构造函数总执行写事务migration。该行为自9/29已存在；线上app.py/query_runner.py/monitoring_terms.py与本地检查源码SHA一致。读取能力恢复被Bus写锁拖住，这是本次SERVICE_BUSY持续的确定原因。

### 离线故障注入结果

运行：`uv run --no-sync python eval/service_busy_diagnosis_20261013/lifecycle_fault_probe.py --source eval/message_source_repair_20261012/staging/src/doxagent/site_strategy/external_runtime.py`。

使用与线上SHA完全一致的External实现，模拟target已分配但Page回复失败：target未关闭、active_pages=0、acquiring=0、permit已归还。模拟target close异常：attachment registry已空、Playwright driver已stop，但raw CDP close未调用。

这是可复现的失败收尾缺口，不是实际Chrome创建失败的完整重演；不据此断言现场每个blank都由同一路径产生。

## 4. 与上线变更的关系及其他风险

- 新提示文案自10/3存在；API启动写锁自9/29存在；new_page失败只归还permit的核心路径自9/22存在，10/1调整取消收尾、10/10增加driver/申请记账。不是本轮新增V2.1 wire schema错误。
- 10/12消息源修复修改了控制探测/恢复逻辑，并保留Chrome进程。存在发布相关性；该修复并未覆盖上述target生命周期缺口。不能简单宣称“上线完全无关”，但已有异常开始早于V2.1正式切换，证据不支持回退研究合同作为首选修复。
- 02:37 Site Access仍unhealthy且有挂死任务；API恢复不代表采集恢复。
- 根盘由02:21约4.6GiB降至02:41约3.8GiB；V2.1备份占约8.9GiB，其中read.sqlite3.gz约4.36GiB、bus.sqlite3.gz约2.41GiB。当前业务Read库约30GiB、Bus库约20GiB。容量不足是独立发布遗留问题，但无本次SQLITE_FULL证据。02:41 API仍healthy、Site Access仍unhealthy，I/O some avg10约46%，并未完全稳定。
- 四个独立V2.1研究Worker均memory=0、cpuquota=0、cgroup_parent为空，位于system.slice，未进入production doxagent-app资源包络。当前RKLB约1.66GiB、约一核，其余较空闲。它们增加共享主机竞争，但不能因此认定它们造成Chrome局部5GiB OOM；不应为诊断停止正在进行的研究。
- 当前未确定事发时Bus数据库的具体持锁事务；lsof/打开fd只能证明使用者，不能证明写锁持有者。

## 5. 修复方案与实施顺序

### P0-A：修正页面创建与控制连接的生命周期

1. **先复现45秒预算失效的具体路径**：在隔离实例重演new_page等待、取消和driver切换，查清为什么外层现有deadline没有结束请求。修正实际失效点，让页面创建及收尾遵守现有请求预算；不叠加多套watchdog、阶段超时、准入检查或恢复状态机。正常请求路径、并发数和身份间并行保持原状。
2. **创建的页面由创建者完整收尾**：复用现有entry、lease和CDP事件，尽早保存本次业务创建的target ID；成功、异常或取消后都能准确关闭自己的页面。若Playwright接口无法在失败前提供target ID，只调整创建入口以现有CDP连接创建并绑定页面，不另建控制器。申请中的计数纳入现有诊断，收尾统一归还permit。不新建数据库、持久化租约或跨epoch对账系统，也不凭about:blank删除人工页面。
3. **连接清理进入独立finally**：单个target关闭失败不再跳过raw CDP socket关闭与自有task收尾；清理结束再释放现有管理引用。修复现有detach路径即可，不增加后台清理队列、重试调度器或定期驱动/浏览器重启。
4. **一次性处理现场残留**：修复发布时由现有owner结束已逾期业务请求、清理明确属于业务的遗留target。仍无法正常控制的实例可用同一Profile重启一次，保留人工维护页及登录状态；这属于恢复现场，不作为日常按内存或年龄自动重启的策略。无需为了收尾重启整个Supervisor。

本项不引入PSS/内存压力阈值门控、请求降速或拒绝、按压力停止实例、降低并发、实例数量限制或新增资源保护服务。修复后用正常采集负载核对实际工作集；若现有5GiB确实不足以容纳正常六实例，按常规容量需求调整配置，不把5GiB上限当产品约束，也不要求先增加保护机制才能调整。

### P0-B：读取池启动去掉无关写锁依赖

1. 把监测词/绑定schema migration移到受控启动/升级阶段，一次执行并明确失败；query worker构造读取服务时不执行migration/BEGIN IMMEDIATE。
2. GET所需监测词服务使用不迁移的读取初始化；控制worker只在真正命令事务内获取写锁。缺schema时明确部署/兼容错误，不伪造空数据。
3. 沿用现有请求预算和读取池恢复逻辑，先修无关写锁依赖；不新增schema轮询、每请求版本检查或恢复阻塞校验。已有启动日志足够验证本项，缺少的信息仅按实际需要补充。
4. 分别窄发布API和Site Access，沿各自实际生产基线保留现有修复；本方案不要求修改Supervisor，也不把容量处理或其他业务改造作为发布前置条件。

### 独立运维事项：处理已发生的磁盘占用，不新增保护策略

根盘余量低是实测事实，不能忽略，但它不是本次新建磁盘保护系统的理由，也不是已证实的SERVICE_BUSY直接原因。可以按已有运维方式清理无运行/动态依赖的临时镜像，将Read/Bus备份转存并校验后删除远端重复副本，或按业务增长安排扩容。两份备份约占6.8GiB；不设置20GiB固定业务准入门槛、低空间暂停业务或新的定时告警，不把本项与核心修复绑成一套机制。

独立研究Worker当前没有资源包络只作为现场背景记录。本轮没有证据要求给它们新增硬限制、启动节流或迁移cgroup，故从修复范围删除；是否停止已完成测试容器按其实际任务生命周期处理。

## 6. 针对性验收及回滚

- 页面正常创建/关闭，创建阶段超时或取消，以及单个target关闭失败后detach，分别验证target和permit完整收尾；重复相关故障路径确认没有累积即可，不规定30轮固定门槛，不扩展成通用极端故障平台。
- 用隔离实例复测本次new_page挂死场景，证明现有请求预算会实际结束请求；同时正常identity仍可并行工作。保留人工维护页和既有Profile，正常路径不新增远端探测、资源等待或逐页面数据库写入。
- 持有真实测试Bus库写锁时，读取worker仍能启动；复测替补worker能恢复以及read-context/capabilities可用。验证具体已发生的恢复缺陷，不增加每请求schema校验。
- 生产登录Overview和研究页可读取，正常消息采集与正文样本继续工作；在相关自然轮询中对比target数和实际内存/I/O，检查是否还在增长。此项是修复效果测量，不是新增业务门控或持续监控工程；短期观测不能冒称长期泄漏已完全排除。
- 保留API/Site Access原镜像与有效部署参数，需要时仅回滚对应服务代码，不覆盖切换后业务数据库。无需Supervisor改造、全机重启或研究合同回退。

完成标准是：页面随业务创建和结束正常释放、已有超时真正生效、读取worker不依赖无关写锁，正常业务可用且并行能力不下降。内存/磁盘保护、资源包络、持久化target治理和额外调度系统不属于本次修复。

## 7. 授权后的实施记录

2026-10-13 已按用户后续授权实施上述修复，窄发布API/Site Access，恢复单个Yahoo实例并清理精确确认过期的业务分配target。具体代码、SDK取消机制复现、40项回归、生产验收、真实失败及边界见 [实施验收记录](service_busy_repair_acceptance_20261013.md)。本文开头的“诊断与方案、不实施”描述仅属于原诊断轮，不代表后续授权实施未进行。
