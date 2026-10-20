# W3 启动超时第三轮修复：实施与验证记录

日期：2026-10-21（北京时间）。依据：[第三轮实施方案](../dev_plan/workflow_v2/w3_startup_root_cause_repair_plan_20261021.md)。

本轮已完成代码修复、本地故障回归和远端私有 SDK/Capsule 验证。**尚未发布生产版本，未重放或恢复历史业务 Case，也没有据此宣称生产非 SDK p95 已低于 5 秒。** 生产发布、自然流量与存量恢复的验收边界见本文末尾。

## 1. 实际改动

| 方案项目 | 实现与边界 |
|---|---|
| A：缩减启动工作、记录启动进展 | `sdk_runtime.py` 只在启用 O4 operations 时导入并构造 O4 capability，普通 W3 不再通过该依赖加载 O4 服务。`capsule_child.py` 在 schema/SDK 导入前开始输出启动进展；记录 SCHEMA_IMPORT、SDK_IMPORT、RUNTIME_CONSTRUCT、SDK_INITIALIZE、SDK_READY。父进程只接受明确的 SDK_READY，失败保留最后阶段、PID/start_ticks、CPU/schedstat、退出码、父进程结束原因和 stderr 字节数；不留存原始 stderr。 |
| B：冷启动统一准入 | 新增 Worker 进程内 `StartupAdmission`。Capsule、普通预热和首次 lifecycle helper 初始化共享 cold=1；绑定业务优先于空库存/预热，维护优先级更低。已有健康驻留 Thread 或空壳先直接领取；等待 cold 不占 SDK handshake，后者仍按原并发配置运行。没有限制已经运行的模型 turn 数。 |
| C：压力与补池统一 | 空库存补充和普通预热共同检查内存安全状态及 CPU/I/O PSI。CPU avg10≥60 或 I/O avg10≥20 暂停投机工作；两者分别低于 40/10 持续 10 秒后恢复，采样缓存 1 秒，PSI 不可读时放行。该信号不改写全局 CRITICAL；真正的内存 CRITICAL 仍阻止绑定业务创建新的物理进程。 |
| C：避免回收/重建抵消 | PRESSURE 回收时保留每个 profile 一份健康 READY 及有效 handoff，CRITICAL 可以回收全部空闲 Capsule。回收在独立任务中执行，不在调度循环中逐个等待进程关闭；回收错误进入资源状态。死进程不计入暖库存。每个 scope 同时只补一份未绑定 BUILDING，成功后继续补至原 2/1 目标。失败按 scope 冷却 30 秒、连续失败 60 秒；成功重置连续失败范围；永久配置错误等配置身份变化再尝试。已经排队的普通预热也服从冷却。 |
| D：按副作用阶段恢复 | operation 增加 owner_token、fork_dispatched_at、startup_attempts、next_retry_at、last_error、prepare_deadline_at、startup_receipt 及 full_query_started_at。SDK_READY 后、真正发送 Fork 命令前才写派发标记。确定未派发时直接在原身份下重试，不调用 thread/list；已知 child 直接接回；不确定时按原 receipt→精确 cwd/parent 的 state-only 查询→最多一次有界完整查询恢复。查询失败不能解释为零 child。 |
| D：取消与旧版本兼容 | preparations 跟踪实际执行 task，不再保留失去 owner 的 shield 内层任务。取消会等待本 owner 的 Capsule 清理，然后重新抛出 CancelledError。状态写入带 owner_token 条件。新增 operation 的空派发标记表示“确定未派发”；旧行 NULL 表示 UNKNOWN，不能按新行处理。原 request/cutoff、slot/operation/run/child 身份不重建。 |
| E：统一预算与 pending | 首次绑定持久化 180 秒总准备期限，空库存排队不提前消耗该业务期限；其已发生的物理启动次数不会因绑定而重置。单次 boot 90 秒、Fork/Resume/MCP 准备最多 45 秒，最多两次物理 boot。为清理预留约 15 秒，子步骤同时受总剩余时间约束。job 的 120 秒启动预算也预留清理时间，模型 turn 的预算未改。队列等待和恢复查询不增加 prepare 的物理启动计数。 |
| E：避免误花业务重试 | 进行中的基础设施问题保留 CREATING/last_error/next_retry_at，不提前写 slot 最终 error。HTTP claim 丢响应、可重试冲突映射为 `w3_prepare_pending`，继续接回原绑定。移除了原 READY 特殊一次恢复时清零另一套计数的路径。只有明确尚未发送 RUN 命令的 CODEX_START_TIMEOUT 才进入相应自动基础设施重试；空 thread_id/turn_id 不构成该证据。 |
| F：helper 与运营交接 | helper 复用一个客户端、单 RPC 在途，队列最多 64 个等待者、等待上限 30 秒；RPC 上限 10 秒，首次 SDK 初始化另受 cold 准入及 90 秒 boot 限制。排队取消不关闭别人的连接，普通 InvalidRequest/not found 不销毁客户端。致命传输错误才清理该 owner；记录独立 identity、PID/start_ticks、generation、退出状态，重启时纳入原进程收束；清理失败会隔离该 helper，禁止另建 owner 覆盖。 |
| F：成功交易后的驻留 | 成功的 trade_decision 释放后保留默认 90 秒 handoff；正常 30 秒 idle 回收不会破坏这段运营交接。operations 领取原 Thread/SDK/MCP，运营阶段释放时结束 handoff；实际进程存活检查仍然生效。CRITICAL 的硬保护不被 lease 绕过。 |

业务合同、模型/effort、Prompt/Skill、研究内容和交易决策逻辑均未调整。旧 HTTP/embedded 回测入口继续兼容，回测的 Web/Broker 隔离由原权限规则保持。

## 2. 配置与发布内容

| 配置 | 默认值 |
|---|---:|
| `DOXAGENT_W3_BOOT_TIMEOUT_SECONDS` | 90 |
| `DOXAGENT_W3_PREPARE_TIMEOUT_SECONDS` | 180 |
| `DOXAGENT_W3_HANDSHAKE_TIMEOUT_SECONDS` | 45 |
| `DOXAGENT_W3_HANDOFF_SECONDS` | 90 |

Compose 中记录 Worker/chrome-supervisor/site-access 的 CPU shares 分别为 **2048/256/512**。本轮没有修改生产容器，因此这些份额尚未在线生效；正式发布必须核对实际 HostConfig，而不是仅检查 YAML。

未设置新的业务并发上限，READY 容量和 SDK handshake 并发沿用现有配置。PSI 只控制投机启动，内存 CRITICAL 继续承担硬安全职责。

## 3. 验证结果与证据

### 自动回归

**完整回归 259 passed、1 项显式排除；最后的 Resume/owner/预算定向复核 26 passed。** 汇总见 `repair_verification.json`。

证据位于 `eval/w3_startup_root_cause_20261021/`，最终总结果见 `final_repair_tests.xml`；阶段性及最后增量结果保存在 `final_incremental_tests.xml`、`helper_final_tests.xml` 、`latest_admission_helper_tests.xml` 和 `final_resume_helper_tests.xml`。

覆盖 W3 全部现有测试文件、Worker API/资源预算、退出恢复、O4 capability 及编排回归，重点包括：

- 20 个冷请求并发时只有一个物理 cold；原 BUILDING 绑定后提升优先级，canonical request 不变。
- 三个冷 waiter 不占 handshake，也不阻塞已驻留 operations；真实内存 CRITICAL 仍阻止新物理启动。
- CPU/I/O 高但内存 NORMAL 时暂停投机工作，迟滞恢复；PRESSURE 保留必要 READY/handoff，CRITICAL 可回收。
- 实际等待 35 秒的 boot 超过原 30 秒边界仍成功，记录 boot_slow。
- 排队、boot、Fork 派发后、child 已知后取消时等待实际执行收束，旧 owner 的迟到状态更新被拒绝。
- boot-only 失败零 thread/list；共享两次启动预算耗尽即终态；旧 UNKNOWN 在恢复查询失败时不重新 Fork。
- 统一期限不因 POST 刷新，且留出清理时间；可重试 claim/丢响应保持 pending。
- helper 在途超时与另一个排队请求互不误伤；排队取消及正常 not found 不关闭有效客户端。
- 成功交易的 handoff 跨过原 30 秒 idle 窗口，operations 释放后才恢复正常回收；既有“运营失败不重复提交交易”回归继续覆盖 ONDS 类型。
- embedded 回测、唯一 Skill、MCP、Web/Broker 权限和原业务 schema 回归。

发现一项独立的既存 O4 测试失败：`test_configure_without_new_crawler_starts_default_monitoring` 的 FakeRunner 测试仍断言仅 5 个默认消息源，当前配置实际还包含 Barron's、Investing、InvestorsHub 和 GlobeNewswire。它没有执行 SDK 启动；本轮未修改默认消息源行为，也未改该断言。最终修复回归显式排除该项，保留最初的失败记录 `worker_repair_tests.xml`，不将它记作通过。

新模块及重点修改文件 Ruff 检查通过；`repair_lint.json` 对全部 16 个修改/新增 Python 文件与 HEAD 逐条比较，**新增违规为 0**，另有 jobs/settings 原有 9 条风格问题未顺手扩大修改。compileall、`git diff --check` 通过。

### 远端真实 SDK，零模型 turn

生产安装 SDK 中，空白私有 Home 只 thread_start 而不执行模型，不生成可 Fork 的 rollout。首次检查的这一限制保存在 `sdk_zero_turn_result.json`，未为绕过限制发起模型调用。

随后只读复制一份**公共 Template** rollout 到私有临时 Home，未使用生产 Case 或生产 SDK 进程做故障注入：

| 检查 | 结果与边界 |
|---|---|
| 三份私有 Home 的真实 initialize/Fork/Resume | 三组通过；关闭连接后接回相同 child；一组主动结束测试自己的 app-server 再 Resume。`sdk_seeded_zero_turn_result.json`。 |
| 两次 35 秒 SDK 初始化停顿、并发等待 | 全部通过，真实初始化分别约 36.35/35.48 秒，冷初始化最大并发为 1；没有把等待时间当成后续初始化已消耗的预算。`sdk_seeded_delayed_zero_turn_result.json`。 |
| 加载本轮实际 Capsule/Runtime 候选代码 | 首个 Capsule 在约 39 秒后 SDK_READY，三份 child 唯一，cold 最大并发为 1；热领取原 child 成功，所有测试自有进程最终退出。`candidate_capsules_final_result.json`。 |
| 最后候选源码复核 | `candidate_capsules_latest_result.json` 保存最新四个候选模块 SHA、完整启动阶段、冷并发及收束结果；本次不额外注入停顿，用于对齐最后的进程存活/内存保护改动。 |

上述测试均为 **0 个新模型 turn、0 个券商调用**，只写私有临时目录；生产 Template 原文件哈希未变。私有协议检查不等同于已验证生产 Research MCP 全链路，更不代表生产 p95。

## 4. 原失败 Case 的处理边界

`recovery_manifest.json` 和对应只读脚本关联了原 **16 个准备失败 Case + ONDS 运营失败 Case** 的 Case/Effect、slot、operation、record、child、阶段结果、线程期限和可确认的 owner 状态。

- 16 个旧准备失败槽目前均为 RETIRED。不能因为数据库里 Case record 仍为 ACTIVE，就绕过原身份绑定直接分配一个新槽。
- 线程 `expires_at` 不等于交易有效期。取不到明确交易期限时保留 UNKNOWN，不用原接收时间或线程 48 小时有效期推断“现在可以重做交易”。
- ONDS 的交易阶段已有结果，运营阶段失败；后续只允许接回原运营阶段，不能重跑交易决策。
- 旧 operation 没有可靠派发/owner 证据的部分如实标为 LEGACY_UNKNOWN/未记录。没有把“当前找不到进程”单独当成副作用从未发生的证据。

本轮没有修改上述 Case、Effect、交易意图或线程绑定，也没有为了展示恢复而重放历史消息。正式恢复应在新版部署后按原业务受理、结果与 owner 证据逐条接回；不能清零历史启动计数、扩大业务期限或新建身份冒充原恢复。

## 5. 尚未执行的生产验收

1. 按原正式发布流程先 Worker 后 Scheduler/client，收束旧 owner，验证 additive 列、代码 SHA、active Template/profile、READY 数及 CPU shares。
2. 使用自然流量覆盖真实开盘高负载窗口，统计全部冷回退、失败和恢复，验证 cold≤1、无回收补建风暴、无 owner 重叠；不人工派发历史 Case 或做生产故障注入。
3. 以 Effect→交易提交扣除 SDK 交易 turn 区间并集的口径验证非 SDK p95<5 秒；另看 READY/驻留启动 p95<2 秒。不得把本地夹具或本次私有 SDK 测试替代该业务验收。
4. 基于恢复 manifest 先检查原结果/意图、业务期限和 owner，再按原身份接回。ONDS 仅运营阶段；回滚保留新列、证据和原业务记录。

代码完成与生产时效验收分别记录；本轮不报告未经采集的线上改善幅度。
