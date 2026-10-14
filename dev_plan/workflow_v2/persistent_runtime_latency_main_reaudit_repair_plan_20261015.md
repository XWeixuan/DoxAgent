# 持久化运行耗时：MAIN 本地复核与修复实施方案

日期：2026-10-15，北京时间。复核基线：本地 main，HEAD 为 f1957b71a1253deeff56434bf88420d202c8df39；本地已有 origin/main 引用与 HEAD 相同。开始时无已跟踪文件修改，仅有其他任务留下的未跟踪文档与评估资料。

本轮严格限定本地源码、Git 历史、已有本地证据和离线测试，不连接远端、不 fetch、不调用真实模型、不修改业务实现或生产。远端/GitHub 已同步是用户提供的现状，本轮不把本地检查描述为重新验证了运行容器。

## 1. 结论与上一轮方案修正

上一轮排查文件为 docs/persistent-runtime-latency-investigation-20261014.md。它对应当时实际运行代码，不能直接当作当前 main 的待办清单。

| 上一轮发现或建议 | 当前 main 实现 | 本轮结论 |
| --- | --- | --- |
| 全 ticker 实时 W1/W2 共用两个线程 | service.py:295 仍是全局 _hot_executor，max_workers=2 | **仍需修复，主要改动** |
| visibility 落后时回退读取所有历史未处理 provisional | service._visible_provisional 已不判断 visibility；SQLite 查询严格 trading_date=? | **已有修复，不再重做可见集合或召回** |
| 旧候选需要按发布 watermark 重新分类/清理 | 当前热路径只读 Case 当天，旧未处理候选可继续保留维护语义 | **撤回本轮清理或新淘汰机制建议** |
| 维护失败加剧历史回放 | 已恢复局部错误降级、阶段重试、明确消费集合和 visibility 单调推进 | **保留现有修复，不再加一套恢复机制** |
| W3 Fork writer 不释放 | thread_lifecycle.py 的 Fork finally 已关闭创建者，RPC 与 close 同锁 | **已有修复** |
| W3 Data MCP 20 秒冷启动超时 | W3 90 秒；O2/O3 maintain 60 秒；其他节点 20 秒 | **已有修复，不统一加超时** |
| UI 耗时可能与模型时间不同 | duration_seconds 仍是完成减准入，hot_path_latency_ms 包含内部等待 | **口径有效，补排队观测，不隐藏总耗时** |
| 网络/流量包可能是原因 | 本地代码无法证明当前云套餐或链路；上一轮探针是历史证据 | **不新增网络修复项，不沿用旧实测推断当前网络** |

恢复这些逻辑的关键提交是 21b9b7bd（Restore production fixes and consolidate reproducible MAIN deployment）。本次不能因旧容器缺补丁，再在新 main 上实现一次相同功能。

## 2. 当前仍然存在的确定瓶颈

### 2.1 并发层次不匹配

当前完整调用关系：

1. RuntimeCoordinator._realtime：全服务 64 个 Case 调度线程。
2. RuntimeJournal.claim：按 ticker 限制 CASE 为 4；SWEEP、MAINTENANCE、SELECTION 各为 1。
3. PersistentRuntimeV2Service：全服务只有一个两线程 _hot_executor。
4. REALTIME 的 _resume_hot：W1、W2 各提交一个 Future 到这个全局池，然后等待结果。
5. 一条 lane 的 R1/R2、应用层重试与退避都在同一个执行线程中完成。

因此多个 ticker 可以各显示 4 个 RUNNING Case，但实际实时 W1/W2 在途工作只能争用两个槽位。一个 ticker 的慢请求占住线程时，其他 ticker 也被拖延。外层调度池较大不等于内层请求有并发能力。

上一轮历史 INTC 示例总耗时约 34 分钟，其中首次模型调用前等待约 31 分钟，模型阶段约 3 分钟。此数值是旧运行证据，不是本轮重新测得；当前源码中的同一瓶颈尚未消除。

### 2.2 现有测试缺口

tests/test_persistent_runtime_v2.py 中 test_service_runs_parallel_hot_path_then_w3_owned_delta 验证单 Case 业务路径与输出，不能证明多 ticker 真正独立并发。现有覆盖不足以阻止“全局两槽位”回归。

### 2.3 未发现另一个 HTTP 小连接池闸门

当前 transport.BailianRuntimeResponsesClient 复用一个同步 OpenAI 客户端，SDK max_retries=0，应用层负责重试。当前本地已安装 SDK 的默认 max_connections=1000、max_keepalive_connections=100，不是两个连接，也不是 15–25 ticker 的直接连接数瓶颈。

该检查只说明本地安装版本，不声称远端依赖已重新核验。不为预想中的限流预先引入全局两槽位替代闸门；若后续真实出现 429，再依据 provider 配额处理。

## 3. 必须实施的修复：按 ticker 隔离实时 W1/W2 执行池

### 3.1 采用的设计

按用户最终要求，将每 ticker 的 Case 准入由 4 提升为 6，每 ticker 懒创建一个 12 worker 的线程池，承接该 ticker 的两条 lane。不同 ticker 不共用小容量执行池。

- 每 ticker：最多 6 个正常准入 Case × 2 lane = 12 个热路径执行槽位。
- 两个 ticker：可独立运行各自 12 条 lane，合计 24。
- 15 个 ticker：不被这个内层池压成全局 2；实际并发仍受现有外层调度、每 ticker 准入和 provider 实际容量约束。
- 重试仍在原 lane 内占用该 ticker 的槽位，不重排其他 ticker。
- CLOSED/sweep 路径继续由现有波次与顺序驱动，不改成并行 sweep。
- 不新增异步客户端、调度服务、工作队列或通用资源管理框架。

不采用把全局 2 简单改成 8/16 的方案，它只是在更多 ticker 接入后产生同类瓶颈。

### 3.2 逐文件实施粒度

**src/doxagent/persistent_runtime_v2/journal.py**

把 claim 中 CASE:4 提取为 RuntimeJournal 的明确类常量，例如 CASE_CONCURRENCY_PER_TICKER=6，claim 继续使用该值。服务按它计算池容量，避免两个独立硬编码未来漂移。CASE 准入改为 6；其他任务种类限额不动，不新增配置项或迁移。

**src/doxagent/persistent_runtime_v2/service.py**

1. 删除单个 _hot_executor，增加 ticker -> ThreadPoolExecutor 字典，以及保护创建/提交/关闭交接的 Lock。现有 threading.Lock 已导入。
2. 增加一个小的提交辅助方法：按 case.ticker 获取/创建池，max_workers=2*RuntimeJournal.CASE_CONCURRENCY_PER_TICKER，thread_name_prefix 带 ticker；在同一短锁内完成池获取及 W1/W2 两个 Future 提交。
3. _resume_hot 的 REALTIME 分支使用该辅助方法；等待结果、NEW/OLD 判定、W2 R3、路由、冻结输入和输出持久化沿用原逻辑。
4. 锁只覆盖字典操作和 submit，绝不能覆盖 Future.result、HTTP 调用、sleep 或 shutdown(wait=True)。
5. close 保留先停止新业务、再 coordinator.close 排空已提交工作、再关闭热路径池的顺序。排空期间允许已提交 Case 完成 lane 提交；不要因为现有 _closed 已置 true，就在辅助方法中提前拒绝这些在途工作。
6. 真正进入热路径池关闭阶段时，在字典锁内禁止新池/新提交并取池快照，锁外逐个 shutdown(wait=True,cancel_futures=False)。随后继续原 effect 池、W3 线程与 agent 的关闭顺序。保持 close 幂等。
7. 同 ticker 并发创建池必须只有一个实例，关闭后不允许重新创建池。不为 ticker 生命周期设计提前回收；进程关闭统一回收足够。

这次不修改 coordinator 的 64 Case 池；journal 的每 ticker Case 准入按最终要求由 4 改为 6，也不改变业务 schema、提示词、模型与 reasoning effort。

**tests/test_runtime_hot_lane_concurrency.py（新增）**

使用线程安全 FakeResponses、Event/Barrier 验证真实进入模型调用的数量。正常测试走 service.execute_message/_resume_hot，不只检查 executor.max_workers。所有阻塞 fake 在 finally 释放，避免失败测试挂住。

**changelog**

在实际实现完成时追加：移除全局两槽位瓶颈、每 ticker 12 lane、保持原准入/业务边界、回归结果。本轮只有方案，不提前记录“已实现”。

### 3.3 必需回归用例

| 用例 | 必须证明的行为 |
| --- | --- |
| 两 ticker 各 6 REALTIME Case | 两边各 12 条 W1/W2 lane 都能进入 FakeResponses，再统一释放；旧全局池应无法满足 |
| 慢 ticker 隔离 | ticker A 的 12 条 lane 全阻塞，ticker B 的 Case 仍能完成 |
| 同 ticker 创建竞争 | 并发首次提交只创建一个池，容量稳定为 12 |
| 每 ticker 准入 | 通过 journal/coordinator 验证第 7 个 Case 排队；第二 ticker 仍能认领，不用直接 service 调用伪造准入测试 |
| lane 失败/重试 | 一条 W1/W2 按原预算失败或重试，不改变另一 ticker 完成能力，不改变原业务错误 |
| 关闭排空与幂等 | coordinator 已提交的工作正常排空；shutdown 不持字典锁；close 两次安全；关闭后无新池及残留热路径线程 |
| CLOSED/sweep | 原 20 member 波次、W1/W2 顺序及 checkpoint 恢复测试继续通过 |
| 路由与结果 | 已有 W1/W2、W2 R3、Policy 使用与 W3 接口测试不受影响 |

并发测试以“进入事件全部满足”断言容量，不依赖真实模型速度，也不把小于 30 分钟当验收标准。


执行中补充：当前 coordinator.close 原本 wait=False，不能视作已排空。修改其仅对 _realtime 池 shutdown(wait=True,cancel_futures=True)，先排空正在执行的实时 Case，再关闭 lane 池；其他后台池维持原关闭策略。

## 4. 同次可完成的小改动：排队观测

不新增数据库字段或前端大改。在上述 lane 提交/执行辅助方法内记录结构化日志：

- 提交时捕获 perf_counter；执行开始计算 queue_wait_ms。
- lane 结束记录 lane_elapsed_ms（含该 lane 内重试与退避）及成功/异常状态；原异常原样向上传递。
- 日志字段限定 case_id、ticker、lane、时间、耗时和状态，禁止记录 prompt、正文、冻结输入或密钥。
- 使用标准 logging；日志故障或日志内容不成为业务准入开关。
- 同 Case 的 W1/W2 是并行跨度，不能将两条 lane 时长相加充当 Case 总耗时。

这可以直接回答“Future 已提交但模型尚未发出”的等待问题。跨重启完整时序 UI 可后续再做，不作为本次瓶颈修复的依赖。现有 duration_seconds、hot_path_latency_ms 和逐轮 latency_ms 的定义不改。

## 5. 已恢复逻辑的边界：保留、验证，不再重写

### 5.1 provisional 与已有冻结 Case

service._visible_provisional(case) 当前直接按 Case.trading_date 读取；SQLite visible_provisional SQL 严格 trading_date=?。保留同日候选排序、身份映射、admission exclusion 和 R2 按召回对象读详情。

tests/test_maintenance_local_degradation.py::test_provisional_reader_is_same_case_day_regardless_of_visibility 已覆盖 visibility 缺失、落后、相同、超前四种状态。这项修复不是“强制旧数据已消费”；历史 PENDING 仍可供维护处理。

必须区别：

- 新 Case 在当前版本冻结输入：受当天 provisional 过滤保护。
- 已在旧代码下冻结了跨日大输入的 Case：恢复时仍可能使用旧 frozen_inputs/round_inputs；当前修复不会自动缩小旧快照。
- 本次不篡改旧冻结输入，不批量标 PROCESSED、不删除候选、不重跑历史消息。若后续确认仍有这类旧待办，另列 Case 清单，按明确的新执行身份开展受控恢复，不能把它混入并发修复。

因此，上一轮提出的 watermark 重新分类、紧凑召回或历史候选淘汰机制不进入本方案。

### 5.2 维护与 W3

当前已有并保留：

- Event Library 局部身份错误取消相关编辑、健康内容仍可 PARTIAL 发布；全局身份/版本错误仍拒绝。
- O2 resolved 候选限定本 source snapshot；_consume 只消费明确 resolved 候选，未解决记录不被强制消费。
- visibility 取当前/既有发布 metadata/本轮截止语义日的最大值，保持单调。
- MaintenancePhaseFailure 按 phase/scope 记录有界预算；基础设施与后续语义阶段不共用失败次数；显式 resume 重置阶段计数。
- 已确认失败的 timeout/artifact 才进入原有回执恢复，不对运行中或不确定 job 生成重复任务。
- FAILED/HELD 维护 owner 阻止额外补维护任务重复创建。
- W3 Fork finally 释放创建者 writer、请求与关闭互斥；响应丢失也释放。
- Data MCP 冷启动预算 W3=90 秒、O2/O3 maintain=60 秒，required/权限仍有效。

没有本地证据证明这些路径还需要新的业务改造。保留回归即可，不把旧生产故障数量当作当前 main 缺实现的证据。

### 5.3 请求超时与缓存

当前 W1/W2 的默认单请求预算为 120 秒，SDK 自动重试关闭，业务应用层最多额外重试一次，首轮退避 5 秒。修复执行池不会移除这些约束，也不自动启用 session cache。先消除确定的排队和已恢复的跨日回放问题，再根据新请求回执判断是否调整网络/模型预算。

## 6. 实施顺序、发布与验收

1. 在当前 main 上完成 journal 常量、service 的 per-ticker 池、轻量日志与新并发测试，追加 changelog。
2. 运行新并发测试，再运行本次列出的维护/W3回归与 persistent runtime 核心回归；对修改文件运行 Ruff 和 git diff --check。
3. 正式提交并进入 main，标准镜像从包含该提交的 main 构建。不要再次只在临时目录 COPY 一个 hotfix 文件。
4. 后续获准发布时，只重建这项变更需要的 v2-scheduler；本次不需要重建 Codex Worker、Message Bus、前端或迁移数据库。使用当前标准生产 compose，不依赖旧覆盖镜像。
5. 将提交 SHA、镜像 ID、运行容器 service.py/journal.py hash 与测试回执写入发布记录；检查运行代码确实具有按 ticker 的池，并保留本次 main 已恢复的 provisional 过滤。
6. 用发布后新 Case 核验多个 ticker 真正在途并发、lane queue_wait_ms、首个模型 started_at、端到端耗时及 provider_timeout/429。历史已冻结 Case 单独标识，不与新 Case 混算。
7. 验收目标为取消跨 ticker 全局两槽位限制，并保持相同输入的路由/决策契约；不承诺模型/网络慢时 Case 仍在固定秒数内完成。

Dockerfile.v2 当前已经 COPY src/prompts，标准生产 compose 有统一应用镜像构建入口；无需新建发布框架。回滚使用正式版本化 main 镜像与既有 compose 流程，保留数据，不回滚或改写业务记录。本方案初写轮次不执行开发/发布；用户后续已授权按最终 6 Case / 12 worker 规格实现、提交 origin/main 并发布对应容器，具体结果以实施回执为准。

## 7. 本轮本地验证

已完成源码/Git 对照及离线回归：

- tests/test_maintenance_local_degradation.py
- tests/test_maintenance_phase_recovery.py
- tests/test_w3_writer_handoff.py
- tests/test_w3_ibkr_permissions.py

上述合计 **41 passed**，1 个第三方 opentelemetry/importlib_metadata 弃用 warning，无失败。此结果验证当前已有修复，不代表尚未开发的多 ticker 池已通过测试。

另完成 tests/test_persistent_runtime_v2.py、tests/test_runtime_orchestration_execution.py、tests/test_runtime_orchestration_recovery.py，合计 **56 passed**。两组共 **97 passed**。git diff --check 通过；已跟踪工作区仍无修改。整个过程没有网络探测、远端执行、真实模型调用、源码修改或部署。
