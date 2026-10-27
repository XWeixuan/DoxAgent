# Runtime / W3 P0 修复实施与基础验收

依据：`dev_plan/workflow_v2/runtime_case_latency_w3_timeout_root_cause_and_repair_plan_20261028.md`（含四项 Bus review 修订）。

本轮实现不改 Prompt/Skill、模型路由、交易资格、原冻结输入和既有模型重试策略；没有增加 795 秒阶段期限或跨尝试共享 600 秒限制。

## 实现

| 范围 | 交付 |
| --- | --- |
| R0 恢复与 admission | 已有 task/Effect 的恢复先于新入站，不依赖最新 ACK；当前版本安装证明与心跳 TTL 分离，旧 Case 继续依原冻结身份恢复。Bus 配置安装只有一个控制任务，心跳独立维护；在线 Repository 不隐式建库/迁移，配置 head 做 CAS。生产 Runtime 也采用进程生命周期心跳 |
| R1 短事务 | claim 只领租约，冻结在工作线程中、写锁之外进行；输入准备有可恢复标记和 CAS。调度仅读 task 标量，不展开所有排队正文；续租不读正文。移除 CASE 嵌套心跳，global state 读取不占 writer。短锁等待与显式 SQL 单元重试在 owner 外退避 |
| R2 异步隔离 | W3 物化/ReceiptWorker 的同步控制操作、Worker 调度/状态/事件及 API 读取移出 loop；按数据库划分有限写队列，物理写未完成不能因取消释放写槽。目录走校验过的索引，已知读取筛选组件与 snapshot；行情快照也离开 loop。保持缓存上限，修复并发驱逐竞态，旧索引原地补齐派生校验信息 |
| R2 / R3 SDK 期限与 owner | SDK 派发时开表，Capsule 在原 SDK 任务中执行 timeout；父进程监督同一 deadline。真实完成事实先于可选 usage/probe 工作保存，晚到回执按实际完成时点接回。外层等待超时保留原 phase Future，后续接回，不盲目新派发；Effect 到期按 lease_until，token CAS 拒绝旧 owner |
| R3 / R4 已付费结果 | W1/W2 hot lane 各自 copy_context，父 owner 等两 lane 收束。Provider 派发/完成采用原 attempt 不可变身份，在 SQL turn 索引前原子保存完成凭证；SQL 失败只接回结果。未知派发显式保留，不自动发第二次请求；业务提交继续受租约和控制 fence 约束 |
| R5 时延与状态 | Case 明细展示接纳至决定/结束、已记录调用区间并集和未覆盖时间，失败/重试不覆盖；W3 缺失 SDK 时长显示未记录。保留事务 operation、锁等待/hold/commit，loop lag 标量及有界异步诊断；Scheduler 有小型健康快照 |
| R6 发布 | 使用已提交的源代码构建，逐组件设置 release revision 并核对源码摘要；按原 Case/job 清单恢复，不重置全部失败计数，不新建历史 Case |

## 基础验收

- 核心 Runtime/W3/MCP/激活回归分批完成；其中核心集合 124 项通过，Worker 资源/取消集合 21 项通过，后续专项继续覆盖改动后的路径。集合存在重叠，不相加宣称测试总数。
- 新增机制测试覆盖锁外冻结、准备中断接回、ACK 过期/覆盖、按库写隔离及取消、已付费结果复用、未知派发、SDK 完成早于回执、原 Future 接回与 hot lane lease context。
- 前端 schema 生成、TypeScript 检查、生产构建通过；1262/1559px，正常与后置失败分支共四项浏览器测试通过。
- `test_server_resource_envelope` 因本轮开始前已有的未提交 Compose 改动缺少 memswap_limit 而失败；该配置未纳入本轮提交与发布。其余 Worker 集合 21 项通过。
- 只做基础发布验收，不将 mock、低峰或短窗口称为生产 p95 <5 秒已达标；不保证 SDK p95 <100 秒。

## 发布与恢复记录

发布前只读清单：`eval/runtime_p0_repair_20261028/preflight.json`，UTC 2026-10-27 19:38:11：282 条活动 CASE task、19 条活动 Effect、39 条未完成 Case；Worker 无 running job，仅一条 queued 的 O3 integration。数量是全局存量，不能当作需要重跑的 Case 数。

### 首次发布与补齐

首次代码提交 `44d9f4f7`，合入同期交易执行修复后发布 `43ef103c43bc90348aae4f3d11cc2b5621e88a77`。Worker、Projector、API、Message Bus、Scheduler、Web 六组件均更新；五个 Python 组件各核验 30 个源文件摘要，六组件 release revision 一致，其他服务容器身份未变。回执 `eval/runtime_p0_repair_20261028/release-first.json`，远端 `/var/lib/doxagent/releases/runtime-p0-43ef103c43bc/receipt.json`。

首次基础验收：六组件 running/restart=0，Worker/API/Web 健康，原持久化 W3 job 已有成功结果。**同时观察到 Worker 最大 loop lag 44.6 秒、Runtime 写等待长尾，因此继续补齐，未将首次发布当作 P0 已完整解决。**

补齐代码 `24a5fde508e5196f04ca135b50ec8409b85d8596` 已同步 origin/main 与本地 main：

1. ReadySlot claim/fill/build/retire、生命周期 SQL 与启动进度回调使用所属 job DB 的短写 owner/读 offload。SDK 仍留在原事件循环与原任务；物理 build/prepare owner 在 SQL commit 后建立，原 prepare deadline 不因恢复刷新。行情停止 slot SQL、提交前身份/保留状态读取一并移出 loop。
2. 修正 Runtime 独立心跳将大写字符串与实际小写 TickerRunStatus 比较的问题，增加真实枚举回归。
3. 资源退役将引用解析、/proc 扫描、fsync、递归删除、正文 encode 移出业务 writer；扫描前后 Runtime data_version 一致才允许短事务内原子发布/改名。业务提交插入其间时放弃本轮退役，按原周期后续重试；保留正文、版本及活动 owner 保护。
4. 补齐回归 **75 passed / 2 warnings**，见 `followup-tests.txt`；覆盖真实 SQLite 竞争时 loop timer 可推进、原 Fork 身份接回和取消等待物理结束。资源/P0 定点集合另有 13 项通过，与 75 项有重叠，不累加。Ruff/diff 检查通过。

### 发布过程异常

旧 Scheduler 原配置退出等待 1200 秒；只读栈确认其主线程在 journal.gap 等 SQLite，没有在途 W1/W2 provider 帧，W3 则是新 Worker 中独立持久化 job。为完成替换，仅终止旧 Scheduler 容器，未终止 Worker/模型/券商或其他服务。证据 `scheduler-stop.json`、`old-scheduler-shutdown-stack.txt`。

补齐发布前还遇到管理通道暂时失联：默认 SSH 和直连均在公钥认证后无 session 响应，独立 true 命令与只读转发也超时。随后连接恢复，远端仓库已同步补齐提交；未擅自重启主机。该现象本身不足以将原因断言为网络、磁盘、内存或本轮代码。

### 最终发布与基础验收

六组件补齐版 `24a5fde5` 已完成发布及 34 文件/组件源码校验，见 `release-followup.json`。随后收口代码 `416fb2fae526473892eb92bce8e25a9995e51323` 仅更新 Worker、Scheduler，其他四组件保持 `24a5fde5`；不为文档重复重启服务。

收口补齐包括：readiness/configure/operation API 的剩余 SQL 与 ACK offload；Worker 结算重试异步读取，取消回调用内存 entered 标记识别未进入的协程；同类活动 task 直接使用已有租约元数据，不循环 get_task 展开正文；SIGTERM 在 ticker/task 调度边界停止新增派发，已提交请求继续依原 owner 收束。相关集合 **40 passed / 1 deselected**（排除上文既有 Compose envelope 问题）及 **16 passed**，见 `final-api-tests.txt`、`final-scheduler-tests.txt`，集合存在重叠。

用户明确指令“强杀进程吧，快点完成”后，仅强停旧 `24a5fde5` Worker、Scheduler 容器，并终止本轮等待排空的发布进程，立即按 canonical release 发布已构建的收口镜像。没有清空任务、改写批量失败状态或人工重新派发历史消息；在途请求可能因强停中断，已持久化结果和执行身份保留，未知的已付费派发继续待核验，不假定能够全部无损恢复。

- 最终 Worker、Scheduler 镜像：`sha256:e41360625f486086b6adea28fdbc395a81f822d12a48d55ba12b50303bc60f02`。
- 两组件各 34 个源码摘要匹配 `416fb2fa`，Worker healthy、restart=0，Scheduler running、restart=0；未更新的服务容器身份全部保持。最终回执 `release-final.json`，远端 `/var/lib/doxagent/releases/runtime-p0-worker-416fb2fae526.json`。
- UTC 2026-10-27 20:53:01 基础状态：Worker dispatcher RUNNING / ready=true / processes_recovered=true，无 dispatcher error、无待结算 owner；active=3、queued=1，已恢复原任务并出现发布后新启动的自然 W3 trade_decision job。没有人工触发模型。
- 同次快照 event_loop_lag current=1ms、max=3319ms（包括启动段）；不能把该短窗口与旧进程累计 max=173696ms 直接作同口径性能对比。此时预热 profiles 尚为空，不能把 dispatcher 已恢复等同于全部公共 Template 已预热完成。
- 首次读取 Scheduler 健康文件仍是前代快照，补充读取遇到 SSH banner 超时，不能将前代快照当作新 Scheduler 的健康证明；原 Case 的租约更新时间和 Worker 新任务已推进。只做基础发布核对，不宣称所有存量 Case 已清空或生产尾延迟目标已达标。

发布过程中读取的主机 load 曾为 113.70，IO pressure 的 300 秒 some/full 曾达到约 65%/39%。这是实测资源压力，不能仅凭单点将来源归因于某个服务，也没有通过扩大线程、模型并发、内存或关闭耐久性来掩盖它。

证据目录：`eval/runtime_p0_repair_20261028/`。保留其他会话的脏改动；没有人工派发历史消息、调用券商或手工重写业务状态。
