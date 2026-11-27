# W3快速单任务与普通双任务实施

依据：w3_source_fast_single_and_normal_dual_adjustment_plan_20261127.md。

仅调整首次W3配置冻结。service先freeze_case_profile，再freeze_mode；freeze_mode读取同一份冻结source_fast标记选single，其余仍读取原全局single/dual_consensus配置。已冻结值、历史路由/契约保护不变。来源名单仍只有DOXAGENT_W3_FAST_SOURCE_IDS，生产目标为sec_edgar,corporate_ir_press_releases；全局仍dual_consensus。

快速Trade=gpt-6.2-sol/high/fast，一个真实Job及Case Thread；普通Trade=两个Luna/max/fast；Operations均一次Luna/max/default，复用Trade自己的/代表Thread。沿用上轮Luna Template、READY和SDK逐轮配置，不新建Sol池，不新增队列、同步数据库读写或模式状态表。原提交原子性、付费回执、恢复、ACK、资源生命周期及Schema/Prompt均不改。

原三项共识测试改为验证存储JSON可读及完整30000字符可恢复，不强迫物理外置表示；原决策、A/B调用、重入不重派、Operations和意图数量断言保留。核心77项通过；补充同ticker快单/普通双混跑及阶段绑定后10项通过（重叠）。覆盖真实首次登记顺序、来源矩阵、全局切换不改历史、历史模型不重分类、快单无A/B Stage、普通有两票、Operations同线程一次；SDK Luna→Sol→Operations无额外Resume的原测试继续通过。

上轮源码尚未生产发布，因此本次需要连同上轮Worker SDK和Scheduler一并发布，不能直接使用旧Scheduler候选。以实际镜像为基底做精确文件增量，保留生产已有行情开关，不带入其他会话未提交文件。Worker已受理任务检查仍保留，不通过先停Scheduler再等待付费任务制造长消费空窗。

## 发布结果与 SDK 兼容阻塞

路由代码已以 `3c0c09d1e7d3` 提交 origin/main，并按现有 owner 发布 Worker、Scheduler。Worker 镜像 `543c6014d830`，Scheduler 镜像 `9dd39deb702c`。配置及隔离 journal 检查证明快来源 single、普通 dual，准备配置全部复用 Luna；生产曾有 6 份 Luna 私有 READY，无 Sol 池。

真实 POOL_HIT canary 在旧 SDK/Native 0.159.3 上调用 gpt-6.2-sol，返回 400/model_not_in_accessible_catalog。**将其解释为订阅无权限是不正确的。** 同一订阅、同一生产凭据在独立 0.172.0 候选中可列出 gpt-6.2-sol 与 gpt-6.2-astra；随后最小真实调用在同一 Thread 完成：

| 阶段 | 模型/推理 | Tier | 状态 | 整轮墙钟 |
|---|---|---|---|---:|
| 模拟 Trade | gpt-6.2-sol/high | fast | completed | 3.531s |
| 模拟 Operations | gpt-6-luna/max | default | completed | 2.637s |

该调用只返回 `{"ready":true}`，不包含业务数据、工具或交易动作，证明模型和逐轮切换可调用，**不代表真实 W3 时效或研判质量验收**。另有新版 Python SDK 下 170 项相关回归通过。

**当前生产安全配置：快速来源名单临时为空，全局仍 dual_consensus，全部新 Case 继续 Luna 双任务。** 已发布路由能力保留；尚未完成新版 SDK/Native 正式切换及快通道最终启用。没有自然新快通道业务样本，不宣称业务验收完成。

新版适配必须同时迁移现有 Native 的 W3 Elapsed 历史过滤/提醒、诊断日志减量、有证明的私有线程定向清理及同版 code-mode-host，不能只更新 Python 包而继续指向旧自定义 codex。新版补丁和锁文件已在隔离开发树准备，但 Native 尚未完成编译及机制回归，暂不发布。兼容构建因本机 C 盘耗尽、Docker 停止而受阻，已向用户请求恢复构建空间；未在资源敏感的生产机启动 Rust 编译。

## O2 发布中断、断点恢复与持续运行原因

用户授权仅中断 O2 后，先以 CAS 暂停当前任务和可补位的 due PENDING O2，再取消对应 Worker O2 Job；没有取消 W3。首个窗口共 23 个被取消 Job，节点均为 event_library_o2_maintain。保持 Task ID、run_id、冻结版本、已完成阶段回执、原 Thread 和失败预算；确认为取消的未完成阶段使用原机制 retry generation。这里的断点是阶段级，不是从中断 token 恢复。

主发布后北京时间 19:13:30 恢复 179 个 HELD 任务；安全关闭快通道的配置窗口后北京时间 19:28:11 恢复另外一次快照中的 170 个任务。两个数字对应不同时点、存在重叠，不能相加作为独立任务总量。恢复检查未发现预算变化；多个任务已在原 Thread 进入 incremental-edit-retry/reference-review-retry，先前成功阶段没有重跑。首阶段尚未建立 Thread 的任务沿原正常入口创建。

运行中的任务主要是 **SUPPLEMENTAL**，不是每日 O2 原任务一直未结束。8 个 ticker 的每日父维护已于 UTC 08:04–08:50 成功；一度另有 209 个 PENDING 维护，恢复后的观察为 174 个 SUPPLEMENTAL PENDING。新补充任务不断补位，因此仅取消屏幕上 8 个 Job 无法得到发布空窗。

O2 为 known-index-map → incremental-edit → reference-review，1800 秒超时按单模型阶段计算，维护总等待上限另为 7200 秒；不能把新阶段/新补充任务当成同一个 Job 超时失效。例：COHR map 263s、edit 521s，COIN map 238s、edit 545s。小候选集合仍有全索引映射和多阶段 Luna/max 成本；持续积压和补位已得到证据支持，但本轮未对每次 SDK 内部思考、工具调用做专项审计，不能据此认定某个 CPU/I/O 瓶颈。

后续新版候选通过 Native/暖线程验证后，再在新的发布快照窗口替换 Worker、启用两个来源、恢复当时的 O2；不得复用已经失效的租约快照，不得取消其他 Job，不得重复付费回放生产 Case。
