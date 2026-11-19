# Runtime 关键链优化：实施、发布与真实验收

2026-11-19，北京时间。依据 `runtime_critical_chain_lean_optimization_plan_20261119.md` 实施。

## 当前结论

开发及生产发布完成，相关 9 个角色的版本、实际源文件 SHA256 和运行状态核验通过；268项统一回归通过，随后29项定向回归通过（有重叠）。Worker/Scheduler 正式配置的私有 READY 目标均为 6，实际已形成 6 份物理 READY。

**阶段验收仍超标：35个完整自然W3 Case，完整Runtime非SDK P50/P95为5.60/14.70秒，最大15.71秒。** 没有达到10秒目标；100例及开盘负载正式验收也尚未满足。已继续定向调查，并形成 `runtime_critical_chain_postrelease_tail_diagnosis_and_lean_plan_20261119.md`，不是以部署健康代替性能验收。

W3核心应用版本：`307db28d2e46d53ef5ba926b901bae8fa123dd56`；Projector最终补丁为`48d0284eecaf0835fa0b5a173b37839257fe3157`。Scheduler 于 **2026-11-18 23:37:28.235663 UTC，即北京时间 11 月19日07:37:28** 启动。文档和测试后续提交不要求重复构建容器。

## 1. 实际完成的修改

| 批次 | 落地内容 | 提交及关键位置 |
| --- | --- | --- |
| A | 正常 v6 main/A/B、独立 Case Thread 身份及必要冻结值一次原子注册；writer 外准备近期成交、global 快照和编码；同 owner 复用返回的权威快照 | `49184c517`；`w3_execution._register_entry`、`w3_threads.register_trade_bundle`、`w3_v6.prepare_recent_trade` |
| B1 | Projector 正常 ACK 在 writer 外计算 pin 差量；首次在只读快照加载，后续复用既有 ACK 缓存；提交重核 source epoch 和 consumer 元数据；正常路径无 DDL | `447dcaf2e`；`projector.py`、`outbox.py`、`source_lifecycle.acknowledge_prepared` |
| B1 | gap 新增、解决及 source-unavailable 成员变化才失效 repair 集合；attempt/退避变化不触发集合重建；ACK 成功后才清脏 | 同上；保留既有 checkpoint/signature/60 秒节流与独立 snapshot consumer |
| B2 | expiry 的 pin 读取与 writer 内重核只覆盖候选页；空页且无清理工作时不进入写事务；保留 epoch、floor、cursor、原始行和引用重核 | `4022236bb`；`source_lifecycle.expire` |
| C | Read v4 提前返回之前也确认 WAL；API/Projector 启动及 alias 激活确认；离线 serving/compact 产物检查 WAL 返回值；不在每个 connect/GET 转换 | `b8b4422a3`；`repository.ensure_wal`、API、Read CLI、generation、shadow、activation |
| C | 新 O3 技术 bootstrap 明确当前工作目录、`./AGENTS.md`、task/common/phase/schema 精确路径，不搜索共享父目录；既有已冻结 request 不重写 | 同上；`workflows/codex_document3/runner_v21.py` |
| C | Executor health/heartbeat 合一笔小事务；Time Awareness 预期 BUSY/LOCKED 降为无堆栈 debug，其他 SQLite 故障仍可见 | 同上；`trade_execution/worker.py`、`w3_time_awareness.py` |
| E | 不再独立保存可重算的 main 共识中间态；从已保存 A/B 计算代表结果，在 Trade 业务事务内重核分支并与 main 绑定、意图、Case、route projection、Operations Effect 一起提交 | `e259765d9`；`w3_execution._commit_pair/_commit_trade` |
| E | W3 Control ticket 校验/插入与 quota/generation admission 共用事务；已有 ticket 仍检查当前 analysis_allowed 和原 Case removal_epoch；成功已受理 Job 消费绕过新请求准入 | 同上及 `307db28d2`；`worker_receipts._admit`、`ControlRepository.dispatch_in_transaction` |
| D | Luna READY 默认和正式环境均设 6；补货及普通压力保留沿用同一个既有目标；单后台构建者、实时 MISS 直启、严重内存保护均保留 | `9c655ace7`；`settings.py`，正式 Compose override |
| 补齐 | archive-time schema 在显式迁移/维护启动阶段准备；expiry 和 consumer retirement 不再临时迁移 | `307db28d2`；`outbox.migrate`、`transport_archive.main`、`retire_consumer` |
| 观测 | Worker trace 延伸至 settlement，记录终态 SQL 返回、notify、audit；client 记录 SSE、GET/POST、接收和解析；子进程 trace 覆盖 Fork receipt 持久化及回包 | `9c655ace7`、`307db28d2`；`jobs.py`、`client.py`、`capsules.py`、`capsule_child.py` |

没有分库、Redis、新常驻服务、新连接池或新的业务状态机；没有降低实时并发、增加后台构建并发、放宽超时/退避或修改模型、业务 Prompt/Skill、输出 Schema、交易规则。O3 修改仅为方案明确允许的技术导航。

### 恢复与原子性边界

- 注册采用 INSERT IF ABSENT；不覆盖原 accepted_at、冻结值、Thread/Job 身份和已保存的 paid 分支。UTC 时间原点与 journal 业务时钟区分。
- 公共 Template/材料未就绪时保留首次 main/frozen 锚点，少数冷恢复路径允许额外事务；不等待 A/B 两支一起 READY 才派发。
- 注册不保存整个 Case，避免覆盖并行 W1R3。Trade→Operations 保持选中线程连续性，不改变跨 Case 隔离。
- 缺失分支和技术错误不会变成 NO_TRADE；synthetic 冲突结果不伪造代表 Job；旧 RESULT_SAVED/COMMITTED 恢复继续兼容。
- Effect 完成、租约、fencing、paid receipt、ACK/资源释放意图等必要屏障保留。请求已受理、未知受理和确定 404 的恢复分支未放宽。
- expiry 规划后新增 pin 时，若尚无完整 exception packet，停止推进并留待下轮，不删除正文或跳过 cursor。

Control合并的收益范围需要明确：健康路径原本已跳过空admit，因此健康请求writer数量没有额外减少；generation/probe更新与Control同发生时才减少独立准入。普通request首次冻结仍有自己的必要顺序，后续精简见诊断报告，不将其写成已完成的节省。

## 2. 测试和发布过程

最终统一回归 **268 passed**，包括正常单/双任务、v6/旧契约、部分结果、提交后恢复、原身份与冻结值、失效 fence、独立并发、Provider Pause/Job 恢复、Control pause/removal、6 份库存、WAL、pin 竞态、Read/API/generation/compact 和 O3 冻结请求恢复。

后续复审发现ACK首次读取使用的connection在projection_session中是借用连接，close为no-op；已确保在进入writer前结束自己的只读事务，追加真实借用连接/元数据变动恢复测试，29项相关定向回归通过，独立补发Projector。没有用新增缓存或状态字段处理这个问题。

一项旧测试原本在独立 `_sync_route_case` 注入提交后错误。Trade route 已按方案并入业务事务，该注入点不再执行；测试改在既有 ACK 提交后钩子注入，保留“提交只一次、不重准备、不重付费调用”的全部断言。专项 11 项和最终统一回归均通过。没有为了满足旧测试恢复被删除的冗余事务。

Time Awareness的一项旧fixture使用`w3-test`却断言v6才支持的per-turn fast/default复用，已改为真实`w3-entry-v6` fixture；旧入口行为未改。六份库存也加入原有并发/idempotency回归参数，未只检查默认数值。

发布使用正式 `scripts/ops/storage_release.py` 构建和组件 release owner，来自已提交 Git archive。A、B1、B2 分别构建发布；E/配套后再发布 READY=6 与 Worker 观测，不将全部修改混成一次发布。

Worker 替换窗口临时停止既有非 W3 派发者并等待已受理 Job 完成，再使用已有 `stop_idle_worker` 的短 admission fence 关闭“检查空闲后又受理新 Job”的竞态。没有强杀在途付费 Job。完成后已恢复原先运行的 owner，包括 O4。未手工重跑历史失败交易 Case。

| 当前角色 | 应用 revision |
| --- | --- |
| Scheduler、Worker、Transport Maintenance、Runtime Maintenance、Initialization、CDECR Executor | `307db28d2` |
| Projector | `48d0284ee` |
| API、Trade Executor | `e259765d9` |

API/Trade Executor已经包含各自所需变更，后续Worker时钟、库存与lifecycle补齐不要求它们重建。逐文件哈希使用 `git show COMMIT:path` 原始字节比较，避免 Windows CRLF 误报。9个角色全部匹配；Runtime、Worker、Research、Read四库实测均为WAL。当前Read已经是WAL，此次启动修复不被记为一次新增的运行提速。

详细发布日志、镜像、启动时间和源文件证明在 `eval/runtime_critical_chain_release_20261119/verify-final.json` 及正式 host release receipts。构建/发布配置中的凭证没有复制进评估材料。

## 3. 自然 Case 验收口径

与前轮相同：持久准入至 Operations 最终 owner 返回；扣除关键模型区间并集，A/B 不相加。W1R3 辅助模型不抵扣 W3 内部工作；内部工具、冷启动、绑定、排队、锁、重试、退避和收尾全部计入。

保留全部入组名单、失败、在途及缺时钟。正式版本窗口从上面的 Scheduler 启动时间开始；不排除启动20分钟，不用双 HIT 小组冒充完整总体，不以阶段 P95 相加计算端到端 P95。

### 冻结的阶段观察窗口

截至 **00:22:53 UTC / 北京时间08:22:53**，43个自然准入全部完成，其中35个进入W3，35个完整，0个在途/失败/缺时钟。105个对应Worker Job全部成功，没有额外失败尝试；70条独立Trade Thread无跨Case复用，Operations代表线程连续性全部通过。

35例Trade内部P50/P95为2.61/8.33秒，Operations为1.48/3.94秒，完整W3为3.93/10.78秒，完整Runtime为5.60/14.70秒。完整Runtime最大15.71秒。前轮105例含业务活跃负载，本轮为盘后窗口，不能把窗口差全归因于代码；但本轮已实际复现超标，不能宣布问题解决。

最早 BE `case_16962a44d4274ba6917f26e3d067712f` 是双 MISS：

| 互斥关键区间 | 秒 |
| --- | ---: |
| W3 Effect 创建→首 owner | 0.442 |
| 首 owner | 0.459 |
| 首 owner 返回→首 SDK | 5.135 |
| 两支 SDK 区间之间未覆盖的间隙 | 0 |
| 最后 SDK 结束→最终 Trade owner | 0.137 |
| 最终 Trade owner | 0.506 |
| Trade 内部合计 | **6.678** |

Operations 为1.473秒，完整 W3为8.007秒，完整 Runtime为9.925秒。分段边界存在并行和嵌套关系，只有表内互斥 Trade 区间求和；不把各阶段分位相加。

该例两支实际私有构建分别约 **3.47/3.62秒**，相互重叠；不是7秒串行等待。首次 claim 返回 unconfigured 后复用原恢复配置路径，最终库存正常补足。首 owner 注册子链为一次 `register_trade_bundle`，116ms；首 owner 总 gate 约27ms。不能把这一个低峰样本外推为高峰锁争用已经消失。

新Worker文件trace已覆盖settlement：该BE三个Job终态SQL返回至Scheduler接收分别约39、20、26ms。扩大样本后SNOW一支出现3.142秒，已定位在client发起GET之前，终态SQL与HTTP返回都快；该支接收仍早于另一分支SDK结束，本例未全暴露到关键链。详见后续报告。SSE直接yield终态事件时未必经过status-terminal close日志，不能把空日志当作未通知。

## 4. READY=6 与资源验收

10分钟有限观测：**23:40:11–23:50:11 UTC**，61个低频 kernel/cgroup 采样；未启用 ptrace、perf 或内核 tracing，也没有新增常驻监控服务。

| 项目 | 结果 |
| --- | ---: |
| 实际物理 READY | 6份，12个进程 |
| 六份 READY 子树 PSS 合计 | 943.68MiB |
| 按本次每份均值推算额外两份 PSS | 约315MiB；不是控制变量实测净增量 |
| Host 最低 MemAvailable | 14.12GiB |
| Worker cgroup 内存峰值 | 2.56GiB |
| Scheduler cgroup 内存峰值 | 0.91GiB |
| Swap-in / Swap-out 增量 | 9.46MiB / 0.004MiB |
| 四个观察角色 OOM-kill 增量 | 全部0 |

CPU some PSI avg10峰值43.47，I/O为11.38，memory为1.62，存在瞬时压力，不能描述为“全程资源空闲”。没有观察到内存、Swap写出或 I/O 持续失控；这只是十分钟资源结果，不能证明开盘突发下长期稳定。已有静态 Swap 占用没有被当作新的换页风暴。

后续00:00–00:10的sysstat显示约75% CPU忙和新的Swap写出；可用内存仍充足。该窗口与上面的十分钟不同，不能用最早健康窗口覆盖后来的资源竞争。后续报告分别保留了历史聚合、晚些时候的进程/cgroup采样及其不可外推边界。

后续资源快照中，READY仍为6；无 realtime 队列、cold waiter、settlement backlog 或 cleanup quarantine。正常的外部模型在途仍保持运行，不因验收人为限流。

ACK另做了一次有界只读身份核验：23:58:53 UTC，生产 Runtime consumer仍持有 **17,207个 exception pins**，与当前 Read 的17,207个数字 gap成员逐项相同，missing/extra均为0，未触及20,000条观察上限。这是跨库近时观察，不冒充跨库原子快照；配合差量、重启及竞态测试，未发现 pin 保护丢失。

## 5. 尚未通过的正式验收与后续最小动作

**性能放行状态：未达到目标。** 本轮35例全部保留后P95约14.70秒；至少100例及开盘覆盖仍未满足。新增的有证据后续方向见 `runtime_critical_chain_postrelease_tail_diagnosis_and_lean_plan_20261119.md`，包括短写owner、未被Read消费的CDC、控制读取连接开销及剩余冷路径；没有把这些建议冒充成已发布能力。

1. 按同一版本、同一口径继续累计，固定连续队列，保留所有冷例与未完成项；分别输出 Trade、Operations、完整 Runtime、HIT/MISS 和失败/缺时钟数量。
2. 已对超过10秒的NBIS、INTC、BE、COHR、MU逐段核对；主要区间及已证实的重复工作见后续报告。不要先扩大库存或添加新调度层。
3. 最早 Case 暴露了 Worker 重启后首次 claim 尚未配置、随后才补齐库存的启动边界；已有冷路径为3.5秒量级，未观察到15–30秒长尾。若后续启动样本反复占主要尾部，应先核对现有 public Future 的首次唤醒及与 calendar reconcile 的先后关系，再考虑把已有公共配置更早发起；当前不把它当作总体 P95 的已证实根因。
4. 若 Worker terminal commit 已快但 Scheduler 接收慢，再细分 SSE/GET/事件读取；若 SQL或gate确实慢，定位当时具体 owner。不能将未观测的时间写成“SQLite”或“资源不足”。
5. 六份库存若出现持续资源回退，先按既有正式配置恢复4份；不得降低实时 W3 并发。当前没有触发该回退条件。

## 6. 可复核材料

目录：`eval/runtime_critical_chain_release_20261119/`。

- `regression.xml / regression.txt`：最终268项回归。
- `expected-final.json / verify-final.json`：分角色提交、源文件哈希、镜像、WAL、OOM/restart核验。
- `cohort-final.json.gz / logs-final.json.gz / acceptance-final.json`：完整入组元数据、现有边界回执及统一计时；在途保留。
- `worker-traces.json / slow-case-critical-chain.json`：选定真实 Job 的 settlement 时钟及首个冷 Case 的互斥链。
- `resources-10m.json / resource-summary.json / pool-memory.json / health-final.json`：资源与实际物理库存。
- `pins-final.json`：生产 ACK exception pin 与 Read gap 身份核验。
- `release.py / worker_release.py / collect.py / logs.py / acceptance.py / worker_traces.py`：有限发布与只读验收方法，不是新增生产常驻模块。

部署健康、单个样本变快和结构性事务减少分别成立；稳定端到端性能仍以正式自然队列验证为准。
