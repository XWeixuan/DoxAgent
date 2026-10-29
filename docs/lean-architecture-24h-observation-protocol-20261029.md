# 架构更新后生产观察任务

## 范围与时间

- **统一修复90分钟结算**：2026-10-29 14:30:32Z–16:00:32Z已完整记录（含启动回归和修复），结论与实际组件版本见[执行记录](message-pipeline-unified-io-repair-implementation-20261029.md)。最后Collector计时纠偏13d2d63b于16:05:22Z部署，只完成短检；不能称最终版本已通过90分钟，更不能算24/72小时通过。继续既有只读观察和各模块原水位，不重置或遗漏中间区间。

- **最新人工授权恢复**：用户本轮明确授权统一I/O修复、同步main、正式重建及90分钟实测；2026-10-29 22:30:32北京时间正式Collector恢复，Bus/BODY/Site/Supervisor此前依次恢复。此前USER_REQUESTED_PAUSE于此结束，14:30:32Z起按实际授权负载记录；期间启动回归、修复与容器替换保留在同一窗口，不能作为休市或人工停机豁免。观测仍只读，不得自行修复或额外拉起服务；版本以正式镜像标签/receipt与后续实施记录核对。

- **最新人工停机覆盖**：用户确认服务器再次失稳并重启后，授权短暂取证并停止相关服务。2026-10-29 17:54:20–17:54:28北京时间，正式Collector、Bus、BODY、Site Access、Chrome Supervisor已停止，保留数据/Profile及W1/W2/W3相关服务。后续观察不得重新启动它们或通过临时容器制造负载；从宿主只读卷和sysstat取证。该时段标记USER_REQUESTED_PAUSE，保留整体服务可用性缺口，单独于正常运行性能评价；没有新闻/正文进展是人工停机预期，不能每两小时重复告警为新的源故障。观察任务仍继续记录宿主与运行编排资源，等待用户明确授权恢复。
- **专项取证例外已结束**：用户随后授权TB级I/O专项必要短测，19:21、19:31、19:39及19:46附近曾临时启用原正式角色。最后于19:47:17停止，19:50:21确认上述五容器全部exited。证据见`docs/physical-io-attribution-and-lean-repair-plan-20261029.md`及`eval/io-attribution-20261029/`。这些短测不撤销USER_REQUESTED_PAUSE；定时观察不得据此自行恢复服务。
- 起始基线：2026-10-29 10:09 北京时间（02:09 UTC）；至少覆盖至 2026-10-30 10:09 北京时间。
- 每两小时在当前对话运行一次。持续运行，首次完整覆盖至少24小时后生成阶段汇总；不因执行次数达到12次而忽略漏跑区间，也不自动修复或停止生产。
- 已创建并核验应用内 heartbeat 任务，ID `2`，名称“架构更新后生产业务与资源每2小时观察”，状态 `ACTIVE`，绑定当前对话。
- 仅观察正常业务。不开启或延长临时采集开关，不改变交易日历；交易、盘前盘后和休市分别标记，休市无应执行任务不能算作兑现率100%。
- 保持 W1/W2/W3 既有运行能力；不触发额外新闻抓取、浏览器访问、Agent或LLM测试制造样本。

## 每轮交付

记录目录：`eval/lean-architecture-observation-20261029/<UTC时间>/`，包含 `summary.md`、业务指标、编排指标、资源证据和异常调查。原始证据与计算口径一起保留，不输出凭证、Cookie、提示词或新闻完整大对象。起始资源证据在 `baseline/host.json`、`baseline/sysstat.txt`。

每轮记录查询起止时间、实际执行时间、主机boot ID、容器启动时间/版本、证据路径、查询失败及缺测区间。按上次成功水位到当前时间取增量；单次业务查询窗口最多120分钟，迟跑时拆分窗口，不丢掉中间时间。跨窗口文章以canonical article、delivery、attempt等稳定ID关联，边界未完成样本在后续轮次续记，避免只保留完成者。仅在记录落盘后推进相应模块水位，部分失败不能推进整个任务的统一成功水位。

### 消息采集与交付

1. 按 source、ticker及必需端点/查询/频道，列出应执行轮次、真实取网启动、有效新鲜列表返回、失败、延期、缺失、在途、旧结果复用及缺少取网证据。有效新鲜周期覆盖率分母为全部应执行周期；共享物理请求须能关联所有实际覆盖单元，RSS成功不能替代独立浏览器频道。
2. 真实取网启动间隔、新鲜列表间隔：样本数、平均值、p50/p95/p99、最大值、超过配置周期的比例。区分调度调用开始和真正取网，HTTP/SDK证据缺失单列，不能填成成功。
3. 拆分应执行→调度、准入/排队、Site/Identity/Chrome等待、取网/浏览器执行、列表落盘、候选识别、接纳、BODY等待/执行、提交、最终可消费发布。缺失阶段记录不可测；嵌套跨度不得重复相加。
4. 从首次实际采集发现到下游可消费发布，报告≤10秒比例及分位数，分母包括所有应交付文章×ticker，保留失败、未完成、NO_PUBLIC_BODY和长尾；另列明确不应交付者及原因。已发布样本耗时仅作辅助，不替代全量兑现率。
5. BODY按真实新canonical文章报告FULL/SHORT_FULL占比、来源/site拆分、失败原因、等待与执行时间、复用/下载、重试和后链路失败。排除MERGED/NOT_APPLICABLE须说明口径；不要将旧文复用成功计入新文下载成功率，也不能仅凭FULL标签断言正文真实完整。
6. 目标参照：有效周期接近100%，采集至可消费尽可能≤10秒，真实完整正文>80%。小样本、休市和缺证据不得宣称达标。

### 持久化编排的非Agent/LLM耗时

按W1/W2/W3及操作类别记录样本数、成功/失败/重试/未完成、平均值、p50/p95/p99和最大值：触发接收→case持久化、case创建、dispatch→claim、Worker本地准备、thread fork请求→返回、结果本地提交/状态推进→投影可见。首次先核对当前schema和时间戳语义，后续沿用并记录版本变化。

只使用真实持久化事件/日志跨度。Agent执行、LLM推理、模型排队和外部工具执行必须分开，不可将case总生命周期或整段Worker job时间作为非LLM耗时。fork排队与RPC执行有独立证据时分别报告，否则标记合并跨度；无开始/结束事件时写不可测，不能编造为0，也不为测量改生产代码。异常样本按case/job/thread/attempt关联检查本地锁、队列、RPC和存储等待。

代码定位：`src/doxagent/persistent_runtime_v2/w3_execution.py`（fork调用），`src/doxagent/persistent_runtime_v2/w3.py`（Worker适配），`src/doxagent/runtime_scheduler/`、`src/doxagent/codex_worker/`及各自持久化schema。只读核对真实表与索引后再查数据，勿猜字段口径。

### 服务器资源与持续增长

已确认远端存在 `/usr/bin/sar`，`sysstat`及`sysstat-collect.timer`处于active。优先读取 `/var/log/sysstat/saDD` 的区间历史，跨日/重启分段，记录实际采样粒度和缺口。每轮保存CPU busy/iowait、load、可用内存、Swap占用及换入换出、磁盘吞吐/IOPS/await/队列/util的区间均值和采样峰值。采样峰值不是连续真实峰值。

起始核验：既有sysstat每10分钟采样，`sa29`最后写入10:00；此粒度可能遗漏短暂尖峰，不能将无采样异常等同于期间从未异常。本轮没有改变采样服务配置。

另取容器cgroup CPU、memory.current/peak/events、swap、io.stat、pids及宿主PSI、major faults、refault快照，按同一boot/容器epoch计算两轮差分；重启重建计数归零须单列。记录Site、Supervisor、Worker、Collector、Bus、BODY、Projector的贡献与增长，区分共享RSS合计和真实工作集。必要时核对实际Chrome/Node/Python数量、target数量、闲置/活跃身份和残留趋势，不创建页面。

起始瞬时资源：10:09约15,602MiB总内存、1,781MiB available、1,440MiB Swap used、load 8.35/9.35/8.52；这是风险基线，不是根因结论或持续稳定性证明。

## 执行方式与成本边界

- SSH：`ssh -o BatchMode=yes -o ConnectTimeout=12 -o ProxyCommand=none doxagent-sg`；远端仓库 `/home/ubuntu/doxagent`。仅进入正式容器只读查证，不启动临时容器。
- 复用只读runner：`eval/pipeline-structural-regression-20261027/remote.py`。已有 `eval/host-resource-redesign-20261029/implementation/` 下 `pipeline_probe.py`、`observe_host.py`、`functional_probe.py`、`browser_costs.py`等，运行前确认脚本仍只读，使用当前生产schema。
- 先读 `eval/lean-architecture-observation-20261029/state.json`。第二轮完整pipeline及全目录details探针发生180秒墙钟超时，后续不要不加判断重复这些重探针；优先使用小字段、索引限定的只读查询，约3秒SQL预算与远端硬墙钟上限，目录枚举也需预算。完整周期证明暂缺就保留缺口，不用CAPTURED条数替代；有条件时拆小窗口补证。旧eval脚本有硬编码时间，必须核对或参数化后使用，不能重跑旧窗口冒充当前数据。
- 第三轮已验证较低成本的周期取证方式：`eval/lean-architecture-observation-20261029/20261029T080909Z/export_cadence.py`（每10分钟只读导出、单SQL约3秒、远端30秒硬上限）与 `offline_cadence.py`（在本地工作站调用现有cadence函数）。使用时明确传入窗口，核对每个part完整标志；失败片涉及的窗口不能称完整，保留缺口。当前一次约54MB本地证据，不要重复抓取已完整区间；原始物理结果超过窗口结束的记录不得用来证明先前时点新鲜。
- 业务指标参考 `scripts/ops/message_bus_lean_acceptance.py`；Site按 `julianday(occurred_at)`/`julianday(completed_at)`的既有时间索引限定窗口。查询尽量只取ID、时间、状态、原因和必要聚合，每条SQL加进度/时间预算，超时记录缺测并缩小查询，避免锁内全库扫描、完整NativeContent/CDC展开及数据库文件复制。
- 保留历史正式观察报告 `docs/host-resource-lean-implementation-20261029.md` 为版本与已知失败基线，不把旧观察值冒充当前测量。已知DIGITIMES权益、Reuters Challenge、IBKR、Bloom非press-release频道、IHub等问题仍需本轮实际对账，不能默认消失，也不因已知而从分母删除。

## 异常调查与汇总

发现大范围刷新停滞、持续延期、BODY低成功率、编排本地耗时上升、资源工作集增长/Swap活跃或IO拥塞，进一步关联同一时间段的来源、attempt、case、容器、队列及有界日志。逐项区分新异常、已知问题持续、明显恶化或仅证据不足，写时间线、受影响范围、支持/反驳证据、可能机制、置信度和待验证项；不要只写“CPU/IO高”作为根因。

本任务不修改代码或生产配置，不部署、不重启、不登录解Challenge、不清积压、不删数据/镜像/Profile，不调整容量/退避/路由，也不改变W1/W2/W3。服务器不可连接时保留连接错误和观察缺口，不将失败探测算健康。

首次覆盖至少24小时后汇总全部窗口、缺测、版本切换、业务分母、长尾、跨时段资源趋势和异常机制。此后仍每两小时继续记录，等待用户决定是否结束观察；阶段报告不能替代更长时间稳定性证明。

## 10月30日Runtime紧急修复及最新观察覆盖

原观察基线2026-10-30 01:18:19北京时间保留，17:18:19Z–18:40:00Z已完成第二个完整增量结算（含先失败导出、只补失败片、旧计划薄索引补证）。见`eval/lean-architecture-observation-20261029/20261029T184000Z/summary.md`，周期全轮次覆盖64.02%，不能将修复期间短检作为整体达标。Runtime明确接纳/Read契约、混合版本、资源再恶化与缺测均续记。

最新正式Scheduler 5e8f3bb6于2026-10-29T19:00:59.02406943Z（10月30日03:00:59北京时间）生效，API 02a2050f，Bus/BODY/Worker fa683526；最终修复版本阶段24小时汇总不早于2026-10-31 03:00:59北京时间。先前版本和中断保留；此条件仅为汇总最早时间，不能把部分组件停服当完整负载稳定。

**当前仅浏览器服务应急暂停**：18:45:48Z起Site Access和Chrome Supervisor已确认停止；Collector/Bus/BODY、Runtime及W1/W2/W3仍在运行，数据/Profile保留。后续须以人工恢复记录覆盖，不得由观察自动启动。HTTP/RSS正常轮询与浏览器必需频道失败分开统计，暂停引发的可用性缺口保留，不能全部源从分母消失或计休市成功。

当前SDK驻留削减与内存路径仍待用户选择。观察只读：跟踪Worker实际active job与capsule环境、BOUND/READY/USED及回收进展，不以进程数差直接断言全部泄漏，不限制W或清任务；INTC历史积压与最终实时消费继续核对。Owner T1、consumer cursor/inbox、实际case及Read/API必须分开，reader能解析不代表游标及时推进。

**sysstat时区**：宿主Asia/Shanghai；所有UTC区间显式`TZ=UTC`调用sar，UTC10月29日16点后读取sa30。误取同名本地时段的文件已标invalid保留，不用于指标；跨本地日和boot/容器epoch分段。采用直接cgroup差分交叉核对，不混淆逻辑读、共享RSS和物理读。

## 用户授权恢复与10分钟复核（覆盖之前的浏览器暂停状态）

用户明确指出此前Worker峰值处于积压集中消费期，要求恢复服务后再测，不接受无依据削减W3预热。2026-10-29T19:36:08Z已启动既有正式Site/Chrome Supervisor，保留身份/Profile及全部W能力；19:37:23–19:47:23Z完成600秒实测。Worker3.36→2.41GiB，可用内存最低2.34/结束3.88GiB，未重现持续内存/I/O抖动。已验证READY预热约519–610MiB，另1组通用候选123–232MiB，合计约0.7–0.8GiB；不将此前9.9GiB等同正常预热占用，不修改预热策略。详见[本轮人工观察](site-access-restoration-and-worker-warm-memory-observation-20261030.md)。

上一个19:22:19Z定时任务被用户明确忽略，不作为正常生产基线；其已有原始诊断文件保留，不据其非正常状态作容量决策。后续只读观察以最新用户授权和当前运行状态为准，不再沿用“Site/Supervisor仍暂停”的过期判断，不改变交易日历或自动操纵服务。
