# 4C16G 服务 cgroup 调整与 CDECR 独立执行方案

日期：2026-09-21。状态：已在本地工作区实施，尚未部署远端；同构 Linux cgroup 与 15–25 ticker 混合负载验收待部署轮执行。

适用目标：4 核、16 GiB 级服务器，15–25 个 ticker 同时监测和交易，健康状态允许 10–20+ Codex/LLM execution。本文取代 2026-09-16 资源方案 §10.2 中逐服务固定容量的配置口径，以及与之冲突的“每个 capsule 已有独立物理隔离”假设；保留顶层 14G/15G/1G、弹性启动、局部物理并发和三态 Safety 架构。

## 1. 确定采用的调整

**顶层 cgroup 是容量边界；服务级 cgroup 是故障边界，不能成为正常业务扩张的容量边界。**

1. 保留 `doxagent-app.slice` 的 `MemoryHigh=14G / MemoryMax=15G / MemorySwapMax=1G`。不预留新的静态内存份额，不计算服务上限之和，不恢复 reservation。
2. Codex、API、Message Bus、Runtime、Enrichment、Projector 等弹性域取消低于顶层的实际内存天花板。部署统一使用 `mem_limit: 15g / memswap_limit: 16g`：子级与父级 RAM 上限相等，不会在父级尚有余量时先撞 1G/2G/8G；每个服务都有资格使用父级共享的最多 1 GiB swap。
3. Control、Delivery、交易 Executor、Web 等轻服务保留放宽后的故障上限。绝不按当前空闲 RSS 的小倍数推导容量。
4. 新增单一职责的 `v2-cdecr-executor` 常驻服务，将 CDECR 重数据阶段从 Initialization controller 移走。初始 `MemoryMax=6 GiB`，可使用顶层共享 swap；这是单个 CDECR 执行域的泄漏熔断线，不是启动所需预算。用户实测 1.5GB 下限绝不作为新上限。
5. 修正局部 OOM 被升级成全局 CRITICAL 的事件归因。局部熔断只处理自己的失败；真实父级/宿主压力才触发全局降载。
6. 修补现有可选 repair 容器和外置浏览器的 cgroup 归属缺口，不再添加中央资源管理服务。

这里选择显式 15g/16g，而不是依赖不同 Compose/Engine 版本中“省略 mem_limit + 单独设置 memswap_limit”的组合行为。它在容量效果上等同于继承父级边界，但配置和验收更直接。不得另加更低的 service `memory.high`、`mem_reservation` 或 `deploy.resources.limits.memory`。

## 2. 审计依据与证据边界

本轮审阅本地 HEAD `d7d1c24c` 及当前工作区。现有 Initialization/repair/镜像等文件存在其他未提交改动，实施时应在其当前行为上做窄改动，不覆盖这些工作。

本轮没有连接生产主机。用户提供的生产占用、OOMKilled=0、restart=0 是历史快照，不代表 09-21 最新运行态。以下硬上限已与本地 server overlay 核对；实际生效值仍须在后续部署时从最终 Compose 和 `/proc/<pid>/cgroup` 验证。本文不把空闲 RSS 当作业务峰值，不宣称 25 ticker 容量已实测通过。

| 已核对源码 | 现状及影响 |
|---|---|
| `deploy/docker-compose.server.yml` | 几乎所有服务都有独立低上限；多数 RAM+swap 总额等于 RAM，额外 swap 为零 |
| `deploy/doxagent-app.slice` | 顶层已经是 14G/15G/1G，不需要再设计一层资源控制 |
| `docker-compose.v2-production.yml` | 服务共用 `/data`，Initialization 已是独立进程；查询池、Worker 等在各自容器内 |
| `codex_worker/capsules.py::Capsule.launch` | capsule 是 Python subprocess，带进程归属标识和清理；没有创建独立 cgroup，所有 capsule 共用 Worker 8G |
| `ticker_initialization/cdecr_process.py::execute_cdecr/run_child` | controller 直接启动 child；child 继承其 1.5G；`_LOCAL_CDECR` 仅进程内 semaphore，不是宿主级隔离 |
| `ticker_initialization/research_adapter.py::_events` | controller 创建 CDECR coordinator，支持预构建采用；当前 server/hk overlay 强制 PREBUILT_REQUIRED |
| `cdecr_integration/coordinator.py::initialize/run_o2_with_upstream_context` | child 之外仍有 runtime factory、activity、snapshot、Delta 编译；O2 continuation 再次创建 runtime factory/冻结快照，单搬 child 不完整 |
| `api_v2/app.py/query_runner.py/background_queries.py` | API 内有 read、stream、control、后台查询进程；默认 read=2、stream=1，另有独立 control/background runner；2G 包含这些子进程与预热成本 |
| `persistent_runtime_v2/coordinator.py` | realtime/background/sweep 使用 64/32/32 个上限的线程执行器；并非每个线程都常驻大内存，但合法并发确实随 ticker/Case 增长 |
| `content_enrichment/browser.py`、`crawler_plane/runtime.py` | 自建 Chromium 计入启动者容器；CDP 连接的 operator Chrome 仍计入宿主浏览器 cgroup；4 page 是各实例局部限制，非全机总数 |
| `scripts/resource_safety_controller.py` | 读取父级层级累计 `memory.events`，将任意新增 oom/oom_kill 立即转 CRITICAL，有局部故障扩散问题 |
| `deploy/docker-compose.initialization-repair.yml`、`initialization_repair/containers.py` | 工作区新增可选 Guardian/临时 Agent/Executor；Compose 未声明 app 父级，动态 create 未设置 cgroup-parent/memory/PID；不能假定它们继承生产模板的物理隔离 |
| `deploy/doxagent-reuters-chrome.service` | 外置浏览器 unit 无 Slice/MemoryMax；CDP 客户端限额管不到浏览器本体 |

## 3. 服务级配置表

所有 G/M 按 GiB/MiB 二进制单位写入；物理服务器“16GB”以实际 MemTotal 为准。表中的数值是建议部署默认值，不是测得的需求峰值，也不是预约量。

`memswap_limit` 是 RAM+swap 总额，不是额外 swap。表中所有额外 swap 共享父级的 **1 GiB 总额**，不是每个服务各自获得 1 GiB 可同时兑现的储备。

| 服务/执行域 | 旧 RAM / 额外 swap | 新 mem_limit / memswap_limit | 处理理由 |
|---|---|---|---|
| Codex Worker + capsules + MCP | 8G / 1G | **15g / 16g** | 合法执行数弹性增长，移除最明显的局部容量瓶颈 |
| API + 查询进程 | 2G / 0 | **15g / 16g** | 查询结果/多个预热子进程可突增；继续用现有查询超时、回收和有界队列隔离坏查询 |
| Message Bus + 其自建 crawler/browser | 2G / 0 | **15g / 16g** | 多 ticker 多 provider 突发正常；不得让整个接入循环因合法子进程增长 OOM |
| Scheduler / W1/W2 / Runtime | 2G / 0 | **15g / 16g** | 时效性并发与输入大小增长，不能用 2G 间接压成固定 Case 并发 |
| Content Enrichment + 自建 Chromium | 1G / 0 | **15g / 16g** | 多 profile/context/page、解析及正文突发；保留现有 page/站点控制，不新建 Browser 平台 |
| Projector | 1G / 0 | **15g / 16g** | 保留批量、游标和查询边界；大 payload/恢复尖峰不应把 UI 投影杀掉 |
| Initialization controller | 1.5G / 0 | **4g / 5g** | 重阶段移走后仍有并行编排、O2 输入/结果处理；先给宽松故障空间，不根据 123MiB 空闲值定额 |
| O4 controller | 768M / 0 | **4g / 5g** | 模型执行主要在 Worker，但配置生成/验证/回执仍可突发，768M 偏窄 |
| 新 CDECR executor + 一个 child | 无独立 cgroup | **6g / 7g** | 包含 Python/registry/Bulk/快照/Delta 峰值，显著高于 1.5GB 实测下限 |
| Control | 512M / 0 | **2g / 3g** | 轻量控制面，保留多倍故障余量 |
| Delivery | 768M / 0 | **2g / 3g** | 保持回执/传递可靠，故障上限不代表并发配额 |
| Trade Executor | 512M / 0 | **2g / 3g** | 交易状态单 writer 继续保留，内存放宽不改变交易并发语义 |
| v2-maintenance（read-store GC） | 512M / 0 | **2g / 3g** | 这是数据库 GC worker，不是全部 ticker Maintenance；后者主要位于 Scheduler/Worker |
| v2-migrate | 768M / 0 | **4g / 5g** | 一次性 schema/迁移过程有尖峰；不把它误归类成永久小常驻服务 |
| Web | 128M / 0 | **512m / 1536m** | 静态服务，独立小故障上限足够宽松 |
| Safety Controller | 128M / systemd | **维持 128M** | 保持在 app slice 外的观测路径；不作为业务容量域 |
| 可选 Initialization repair Guardian | 未显式设置 | **2g / 3g，加入 app slice** | 控制进程；不能因辅助服务绕过总边界 |
| repair Agent | 未显式设置 | **15g / 16g，加入 app slice** | Codex/工具执行为弹性域，仍保留已有 repair 业务并发和权限限制 |
| repair Initialization Executor | 未显式设置 | **4g / 5g，加入 app slice** | 与正式 controller 同类；不得再次内嵌 CDECR 重 child |
| repair CDECR Executor（确有候选代码时） | 未存在 | **6g / 7g，加入 app slice** | 使用该 repair round 的候选镜像，沿用同一 CDECR dispatch/fencing |
| operator Chromium | unit 无显式限制 | **MemoryMax=6G / MemorySwapMax=1G，加入 app slice** | 它是外置浏览器的实际物理边界，不是 CDP 客户端的限额；保留登录 profile |
| Clash | 用户快照 256M / 256M | **本轮维持** | 共享代理仍在 app 外；实测宿主占用计入 host pressure，不借本轮重排网络基础设施 |

以上轻服务/CDECR/Browser 的故障线同样不是永久真理：若正常样本在父级健康时逼近它，按合法业务扩张提升该域上限；不得补预测预约、固定 ticker slot 或先降业务并发。新 6G CDECR 不是保证绝不局部 OOM，而是明确选择限制单执行域的极端故障范围。

15g/16g 弹性域的取舍也必须说清：单个失控任务可能消耗整个 app 余量，软件 Safety 无法保证每次都在突发 OOM 前干预。本方案接受该风险以消除提前截断；不宣称仍有“每个 Codex capsule 独立物理熔断”。真正的硬兜底是父级。

## 4. 顶层、swap 与其他边界

### 4.1 保留 14G/15G，但准确解释作用

`MemoryHigh=14G` 会引发内核回收/节流，不是只发一条告警；即使 Safety 尚未 PRESSURE，内核也可能开始延迟分配。保留它作为靠近 15G 时的回收区间，不为任何子服务增加更低 soft limit。

15G 是 app RAM 边界，不保证宿主一定剩 1G。实际 MemTotal、Docker daemon、SSH、Clash、桌面会话、kernel 和 slice 外服务都会占用物理内存。继续用真实 MemAvailable/PSI 处理该问题，不先下调顶层限额；将 DoxAgent 专用 operator Chromium 纳入 app，防止大型浏览器逃逸计账。共享桌面/xrdp、代理、Docker daemon 不整体迁入。

### 4.2 swap 只是一块共享缓冲

- 每个 app 容器配置 `memswap_limit = mem_limit + 1GiB`；祖先 `MemorySwapMax=1G` 限制所有后代总量。
- 不新增 swap 文件、不扩大顶层 swap、不让 reservation 瓜分 swap。
- 子服务 `memory.swap.max=0` 不再是默认值；验证宿主确有可用 swap，若宿主没有 swap，配置权限本身不会创造缓冲。
- Safety 继续看真实 swap I/O 与 PSI，不因 `swap.current > 0` 就暂停业务。

### 4.3 PID、/dev/shm、局部并发

这些不是本轮主要改造对象，但不能成为放宽 RAM 后的新隐形门槛。建议 Worker `pids_limit` 从 1024 提到 4096，API 128→512，Enrichment 128→512；新 CDECR executor 512，controller 256 保留，其余沿用当前值。Linux PID 计数包含线程，验收必须检查 `pids.events`，不是只数主进程。

保留 Message Bus `/dev/shm=512m`；Enrichment 自建 Chromium 的 shm 配置在最终 Compose 中显式核对，需要共享内存时给 512m。shm 大小只是可用上限，实际触页仍计入 cgroup，不是额外免费 RAM，也不预先预约。

保留 Codex launch wave=3、cold-start=3、Browser 实例内 page=4；不把这次 cgroup 调整扩展成调度器重写。page 数量并不限制所有 idle profile/context，须沿用关闭与空闲回收。不要假定所有 Browser 实例共用一个四页上限。

## 5. CDECR：选择独立常驻执行器，不做动态容器平台

### 5.1 物理拓扑

```text
doxagent-app.slice  [high 14G / max 15G / swap 1G]
  ├─ v2-initialization       [4G，业务编排/轻量句柄]
  ├─ v2-cdecr-executor       [6G，dispatch consumer + 当前 child]
  │    └─ CDECR stage child [与 executor 同 cgroup，阶段结束释放进程]
  ├─ codex-worker           [15G，capsules + MCP]
  ├─ runtime/message-bus/... [弹性共享]
  └─ operator Chromium      [6G，DoxAgent 专用宿主服务]
```

新增一个 Compose 常驻服务，使用相同正式 backend 镜像、同一 `/data` 路径和 Safety 只读文件。controller 不挂 Docker socket、不调用 docker run/systemd-run，不需要 cgroup delegation 或 privileged。执行器无需对外开放 HTTP 端口；共享 SQLite/文件工件完成交接。

第一版每个正式 CDECR executor 同时运行一个完整重阶段子进程，阶段结束立即退出 child。等待队列不创建 child。这个限制只在 CDECR 域内生效，不阻止其他 ticker 的监测、W1/W2/W3、O2/O3/O4、D1 或 Maintenance。

必须诚实描述：当前整个 CDECR stage 包含远端模型等待，不是纯 CPU 区间；因此这个“1”也会串行两个 ticker 的 CDECR stage。第一版为减少共享 registry/峰值复杂性接受此局部限制，不宣称已经实现“网络等待释放 CPU slot”。不额外添加全局 CPU Pool；若验收证实多次 CDECR 等待明显影响初始化吞吐，再单独允许两个独立 executor，不能简单在同一 6G 域里塞两个完整 child。

### 5.2 移动完整重数据边界

不是仅把 `run_child()` 换个启动命令。正式远端 CDECR stage 必须覆盖：

1. 历史输入准备/导入、registry 绑定和 runtime factory；
2. Bulk epoch/native task 执行及原生断点恢复；
3. activity projection、FINALIZED snapshot、Delta 编译及必要导出；
4. 原子写出与当前 `TickerPipelineResult` 兼容的结果和冻结工件引用。

controller 只提交身份/截止时间/路径/版本等轻量输入，等待 durable 结果，校验身份后完成既有 `cdecr` 节点。尽量引用已有 message 清单文件，避免在 controller/SQLite receipt 中再复制巨大数组。

`run_o2_with_upstream_context()` 当前会重新构建 runtime 并冻结快照，必须一起调整：优先读取上述冻结 snapshot/Delta 工件，在执行器侧生成 O2 需要的大型输入包；controller 只传递引用给已有 O2/Codex 工作流，不在其进程内重新构造 CDECR runner。若 O2 仍必须解析完整冻结对象，限定在其实际执行边界并记录峰值，不能把“child 已拆出”当作 controller 已完全轻量化的证据。O2 远端模型执行继续走已有 Codex Worker；CDECR executor 不长期等待 O2/O3/O4 完成。

显式采用旧 prebuilt 包的兼容路径继续有效；import_registry、必要校验/物化等大内存步骤也交给 executor。服务器默认不再要求先在本地跑完 CDECR。

### 5.3 最小 durable 交接与恢复

复用 Initialization SQLite 作为权威状态、`cdecr-dispatches` 作为输入/结果目录、原生 registry 作为 CDECR 业务断点。仅补一张 CDECR 专用 dispatch 表，或在已有可原子领取节点记录中补等价字段；不要新建覆盖所有执行域的 Job 平台。建议用专用表，避免混淆 node RUNNING 和实际 child RUNNING。

必要字段仅为 dispatch_id、initialization_id/node_key、execution_version、input_ref/hash、status、worker owner/generation、heartbeat、result_ref、error。状态为 QUEUED/RUNNING/SUCCEEDED/FAILED/CANCELLED；沿用基础设施失败与业务失败分类。唯一键绑定一次逻辑节点执行；基础设施恢复更新 attempt/generation，不制造新的业务任务身份。

- 提交即落盘，原子 claim 才创建 child；重复提交返回已有 dispatch/receipt。
- QUEUED 不持有模型线程、child 或内存预算，不开始执行 timeout，不消耗 native task 的业务重试次数。
- controller 等待时只保留轻量 coroutine/结果查询。现有 workflow lease 是业务一致性所有权，不是资源 reservation，保留必要 heartbeat；不得为了“无 reservation”删除 fencing。
- 当前 child 绑定 controller lease 的 watchdog 需要改为绑定 executor dispatch ownership，并检查取消/执行版本 fence。controller 短暂重启不应直接杀掉独立执行中的 CDECR。
- controller 恢复先按稳定 dispatch ID 接回已有执行；禁止因为 node 旧状态 RUNNING 就创建第二份 child 或把等待算成一次业务失败。需调整当前 `service._execute` 的恢复分支。
- cancel/显式替换节点：递增 fence，TERM→有界等待→KILL 清理旧 child 进程树，确认退出后才能启动替代者。registry OS 文件锁继续保留，仅保护同一 registry，不扩展为跨 ticker 全局锁。
- 所有写入 node receipt/native task 的调用携带当前有效 fence；不能把旧 controller Lease 原样序列化后长期复用。沿用既有 repository CAS，不另造一套全局租约服务。
- 结果采用 attempt 唯一路径、原子 rename 和当前 generation 校验，防止旧 `result.json` 被新执行误认。
- executor/child OOM：先核对有效成功工件和原生 FINALIZED 状态；没有成果则记基础设施中断，从 native checkpoint 恢复。不能靠自动不停重跑掩盖同输入重复撞 6G；重复同因失败保留断点并报明确故障。

executor 可因自己的 6G OOM 整体重启；它与 controller 已是兄弟容器，不影响 controller 活性。第一版不创建每 child 子 cgroup，因此不声称 dispatcher 在本域 OOM 中必定存活。

### 5.4 运行模式切换

新增清晰的生产枚举 `REMOTE_EXECUTOR`，保持本地 `LOCAL_ONLY/LOCAL_OR_PREBUILT` 和显式 `PREBUILT_REQUIRED` 兼容。

必须同步检查 `settings.py`、生产 Compose、server/hk overlay、Control 创建请求校验、CLI 及 repair 环境模板。仅修改 `.env` 无法覆盖 overlay 中写死的 PREBUILT_REQUIRED。

切换前 executor health/DB schema 就绪；新初始化默认走远端 native CDECR。已有已绑定 prebuilt 的初始化继续使用原包身份/截止时间，不能静默替换。executor 暂时不可用只等待/报告 CDECR 执行域问题，不回退到 controller 本地运行，不阻塞其他 ticker 的实时路径。

### 5.5 与现有自动 repair 的一致性

工作区已有 repair Agent/候选镜像/临时 Initialization Executor，不能让候选 controller 将修复后的 CDECR dispatch 发给仍使用正式旧镜像的常驻 executor。

dispatch 绑定代码/image identity；正式 worker 只领取匹配正式版本的任务。需要修复 CDECR 代码时，现有 repair Guardian 启动同 candidate image 的专用 CDECR executor（仍是上述角色和同一协议），加入 app slice、6g/7g、512 PID，限制为该 round 的 dispatch。它不能领取正常 ticker 的任务。复用现有 repair round 生命周期及容器 label，不新增容器调度平台。该 round 结束后确认 child 退出再清理容器；正式 executor 不执行候选代码。

## 6. Safety 只补事件归因，不扩展状态机

父级 `memory.events` 会累计子级事件。当前实现将层级 `oom + oom_kill` 增量直接当成全局 CRITICAL；放宽部分上限并保留 CDECR 故障线后，不能继续这样处理。

修改原则：

1. 父级 `memory.events` 继续作为诊断遥测；另读父级 `memory.events.local`。使用父级 local 的 `oom` 增长辨别父级分配边界事件，不仅凭 `oom_kill` 杀进程计数推断 OOM 发源层级。
2. 全局状态仍由父级真实逼近上限、父级本地 OOM、host MemAvailable/PSI/swap 等现有指标决定。
3. 只有子级 OOM 而父级/宿主健康时：记录局部故障，由对应 executor/restart/receipt 恢复；不冻结所有 background，更不能触发 Codex 杀掉无关 capsule。
4. 不支持或读取失败时不把累计子级事件强行当父级 OOM；输出缺失遥测并继续已有真实压力判断。Safety 状态文件失效仍 fail-open。
5. 每个执行域只处理自己明确归属的可恢复任务。CDECR NORMAL 下正常执行，PRESSURE 不启动新 child，已执行 child 不因普通 PRESSURE 被杀；CRITICAL 停新启动，优先依赖现有降载和父级兜底。本轮不引入跨域“选出全机最大 Job”的中央仲裁器，避免多个域各自同时杀一个任务形成放大。

保持已有 2 秒采样、持续压力/恢复迟滞，不新增 quota score、动态 service memory resize 或预测器。

## 7. 文件级实施清单

| 范围 | 修改内容 |
|---|---|
| `deploy/docker-compose.server.yml` | 按 §3 改 RAM+swap、PID；删除会覆盖新值的旧资源字段 |
| `docker-compose.v2-production.yml` | 新增 CDECR executor 角色、同卷路径/镜像/健康检查；模式切换和服务依赖 |
| `deploy/docker-compose.hk.yml` | 清理 PREBUILT_REQUIRED 强制覆盖，同步角色镜像 |
| `ticker_initialization/research_adapter.py/cdecr_process.py` | 改 durable stage dispatch、独立 child 归属；保留本地兼容入口 |
| `ticker_initialization/repository.py/service.py` | 最小 dispatch schema/claim/fencing、等待接回、恢复/取消；不得重置既有业务 attempt |
| `cdecr_integration/coordinator.py` | 重准备阶段完整迁出、冻结结果/O2 引用消费，消除 controller 重建重 runtime |
| 新 `ticker_initialization/cdecr_executor.py` | 专用 consumer/child lifecycle，非通用 Executor 框架 |
| `settings.py`、Control/CLI 模式校验 | REMOTE_EXECUTOR 与 prebuilt 显式兼容 |
| `scripts/resource_safety_controller.py` | 父子 OOM 来源区分，仅必要字段补充 |
| `initialization_repair/containers.py` 等 | 动态容器显式物理归属、角色边界及候选 CDECR 版本一致性 |
| `deploy/doxagent-reuters-chrome.service` | DoxAgent 专用浏览器 Slice/宽松 MemoryMax/SwapMax；保留 profile 与 CDP 地址 |
| 原 09-16 方案与运维文档、changelog | 更新 §10.2、capsule 隔离事实与执行模式，不再引用低服务配额 |

另查最终加载的 override、动态 Docker create、临时服务和已有容器的 HostConfig。Docker socket 客户端创建的容器不会自动继承客户端自身 cgroup；BuildKit 镜像构建也不是 Agent 容器的子进程。正式验收记录构建进程归属，生产主机上的临时重构建安排在维护操作内，不能声称已受 app 父级保护。

## 8. 预期资源形态与验收

不把当前 187MiB Codex、123MiB Initialization 等空闲值外推成未来峰值。Codex 的真实成本包括 Python capsule、SDK/Node/MCP；API 包含多个解释器和查询结果；Browser 包含 profile、renderer 和页面；CDECR 包含 registry、模型 batch、中间集合与快照。各域峰值可能重叠，15G 是共同物理限制，任何方案都不能承诺任意规模 25 份重任务同时装入 16GB。

这里保证的是：15–25 ticker 的生命周期不会被低服务限额提前截断，实时任务与后台研究可并行；真正超出宿主物理能力时按既定 Safety 退让。6G CDECR 是可用上限而非预占，空闲 executor 不会“扣掉”其他服务 6G。

必要验证只围绕本改造，使用隔离环境/合成输入，不在生产做内存炸弹或真实下单：

1. **配置落地**：最终 Compose config 与 Docker HostConfig 一致；所有 app 容器（含可选 repair）位于实际 app 子树；无旧 8G/2G 覆盖。核验父级 14/15/1、子级 RAM/swap/PID 与有效祖先限制。
2. **弹性越过旧线**：受控测试 Codex 域超过旧 8G、Enrichment 超过旧 1G 时，父级健康、未触发新上限，局部 `max/oom/oom_kill` 不增长。分场景测，不能同时人为分配到父级失控再把 OOM 算作局部失败。
3. **共享 swap**：有真实 swap 的隔离 Linux 环境验证子级不再是 swap.max=0，祖先 swap 总量仍 <=1G；不得仅凭 Compose 文本或容器 free 命令验收。
4. **CDECR 分离**：查 controller/executor/child 的 `/proc/<pid>/cgroup`，child 绝不能出现在 Initialization 容器内；用能超过 1.5G 的代表输入验证 controller 不随之增长到同样体量，执行成功且原生成果完整。
5. **完整链路**：远端同构环境执行 native CDECR→snapshot/Delta→O2 交接；无需本地包上传。既有 prebuilt 显式路径另做兼容用例；不重跑已经成功的业务阶段。
6. **恢复矩阵**：controller 重启接回、executor/child 中断、结果提交前后崩溃、取消与旧结果迟到、候选镜像 repair 领取，各自保持单写者、原身份、正确 fence 和 checkpoint。
7. **局部 OOM 不扩散**：在隔离环境临时降低 CDECR 测试限额制造本域 OOM；controller/Message Bus/Runtime 活着，父级健康时 Safety 不因该局部事件进入 CRITICAL。生产默认仍为 6G，不沿用测试小上限。
8. **25 ticker 混合负载**：15→20→25 ticker，叠加实时消息/W1/W2/W3、后台 Codex 和一份真实代表 CDECR；记录 dispatch p95/max、active execution、RSS/PSS、cgroup current/peak/events、PSI、swap I/O、PID。目标沿用 W1/W3 dispatch p95<15秒、W1 max<60秒，拆开外部模型等待与本地排队。
9. **真实父级压力**：隔离测试持续压力时停止后台新 fan-out、保留轻量控制/落盘；不能通过固定总并发或永久 realtime 保留槽让测试“通过”。

只对实际跑过的负载报告容量；若某合法样本撞服务故障线而父级健康，优先放宽该服务边界并保留证据。不要把一次失控样本转化为全系统永久低并发。

## 9. 实施与发布顺序

1. 本地先完成 CDECR 执行域、durable 接回、Safety 事件归因及定向测试；配置表可并行落地，但不会因此自动部署。
2. 在相同 cgroup v2/systemd Docker 驱动的隔离环境核验最终配置与进程归属。controller 保持 prebuilt 兼容，先确认 executor 可用再切新初始化默认模式。
3. 后续获准部署时，先备份既有控制/原生断点状态；等待相关当前节点的安全交接点，按服务重建使 HostConfig 生效。已有容器不会因改 YAML 自动获得新边界。
4. 将 DoxAgent 专用 Chrome 纳入 slice 需要计划重启其 unit；保留用户 profile，不清理身份目录。动态 repair 容器按新的 round 生命周期生效，不能在执行中强制替换。
5. 验证健康、真实业务推进和 15–25 ticker 混合负载；报告哪些已实测、哪些只是配置检查。

失败回退限定在 CDECR transport/mode 或单一服务变更：保留原生成果/dispatch，暂停有问题的新 CDECR 启动，显式恢复旧 prebuilt 兼容路径。绝不自动回退成在 controller 内执行重 child，不恢复旧全局互斥/预约，不删除 registry 或重做已成功 ticker。

## 10. 明确不做

- 不新建全局资源数据库、Resource Manager、四级压力机、weighted slots、内存借额或按 ticker 分配 RAM。
- 不将 25 ticker 理解成启动 25 份 CDECR/Chromium。
- 不把 capsule/process group 当作独立 cgroup，不把 cgroup 上限之和当作物理用量。
- 不关闭 OOM killer，不扩大 swap 掩盖压力，不新增硬 CPU quota。
- 不为了本轮拆分改写 CDECR 研究语义、模型质量、交易策略、native checkpoint 或最终发布契约。

## 11. 技术语义参考

- [Docker Compose services：mem_limit、memswap_limit、cgroup_parent](https://docs.docker.com/reference/compose-file/services/)：swap 字段是 RAM+swap 总额；相等即不允许额外 swap。
- [Linux cgroup v2](https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html)：祖先约束、memory.high 回收节流、memory.max、层级 memory.events 与 memory.events.local 的区别。

## 12. 本地实施记录

本轮已完成服务级 cgroup/swap/PID 配置、CDECR 独立 executor、同一初始化节点的 durable dispatch/lease fencing、冻结 snapshot/Delta 交接、REMOTE_EXECUTOR/prebuilt 兼容、repair 候选执行域、Safety 父/子 OOM 归因、外置 Chromium 归属以及定向回归。dispatch 同时绑定 execution identity、完整 execution version、稳定 input reference/hash；不匹配代码身份的 executor 不会领取任务。

当前边界是“本地实现与离线验证完成”，并非“4C16G 生产容量已验收”。本轮没有连接、重建或重启远端服务，没有执行真实模型/CDECR 大样本、OOM 注入、真实 swap 或 15–25 ticker 混合压测。第 8、9 节列出的 HostConfig、`/proc/<pid>/cgroup`、峰值、PSI、swap I/O、局部 OOM 和业务时延指标仍必须在后续获准部署时按顺序验证；如合法样本在父级健康时撞 6G CDECR/6G Chromium 故障线，按本方案放宽该域，而不是恢复预测预约或全局串行。
