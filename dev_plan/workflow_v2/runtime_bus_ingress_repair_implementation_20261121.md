# Runtime Bus 入站延时修复实施与本地验证

日期：2026-11-21。依据：`runtime_bus_ingress_latency_root_cause_and_lean_repair_plan_20261121.md`。

## 1. 结论与交付边界

开发轮完成方案 A/B/C 的代码修改、D 的正式发布入口改造和本地回归。随后按用户指令完成生产发布，详见第6节；尚无稳定生产P95结论。 本地微基准证明重复连接与空轮写入已实际减少，不能据此宣布真实 Bus→Runtime 准入 P95≤1 秒，更不能宣布完整 Runtime 非 SDK P95≤10 秒。

修复覆盖两类原因：常态消费轮次中重复读取、反复争抢 writer 和 claim 锁传播；发布时提前停止消费者造成的分钟级消费空窗。W1/W2/W3 的业务路由、Prompt、Skill、模型、输出 Schema、交易资格及既有执行并发均未调整。

## 2. 修复后的执行链

```text
每轮批读当前 activation/ACK、Scheduler state、Control 与必要授权证明
  → 批读各 ticker 的 Stream head/cursor
  → 有消息时才物化正文并检查必要语义日安排
  → writer 外过滤成员、确定分组、编译 Source、编码 Native 内容
  → 一次 Runtime 短事务：重核原身份 + 最新准入 + 全部 Task + compact 回执 + 高水位
  → 提交 Bus cursor
  → 按本次 Task ID 立即 claim/派发
  → 状态变化即时保存；空轮仅按既有 15 秒健康节奏批量更新心跳
  → 只等待 250ms 周期的剩余时间
```

未增加 ticker 线程池、跨轮授权缓存、持久化队列、数据库或常驻消费者。恢复扫描保持原有机制与至少一秒间隔，避免提高入站频率后将后台扫描也放大四倍。实时新 Task、容量释放与 Effect 完成的即时唤醒不依赖该扫描间隔。

### A. 同轮事实复用与空轮减量

- `ticker_initialization/repository.py`、`consumers.py`：一次读取当前 revision 和 ACK 元数据，结合批量 Scheduler state、Control 和必要 policy proof，复用原准入证明。ACK 新鲜度同时校验 revision 身份，旧 revision 的新心跳不能被当作当前 ACK。
- `runtime_scheduler/repository.py`：增加批读；状态成功写入后不再立即回读。空轮心跳只改时间戳，并比较已观测版本，不能覆盖并发暂停、模式和计数更新。
- `message_bus_v2/repository.py`：批查索引化 head/cursor；pending 数量使用有上限的标量查询，不重复读取 Native 正文，也不假设 offset 无洞。
- `runtime_scheduler/service.py`、`v2.py`：同轮数据用完即丢弃。短同步单元复用原 `connection_scope`；不将连接或读事务带入 Future、模型调用或其他线程。
- `coordinator.ensure_schedule`：当前语义日已对齐时不再前置重复 reconcile。首次安排和换日仍走必要 reconcile，后台恢复保留。

### B. 原子登记与崩溃恢复

`RuntimeJournal.register_stream_admission` 将普通新 Stream 的四次 Runtime 准入相关写事务合并为一次 `stream.register`。claim、租约和后续 inputs 冻结仍有其必要独立事务，不计作已消除。

写事务外完成正文处理与 Native 编码；事务内使用 `ControlRepository.admit_in_transaction` 重核最新权限，全部分组 Task、跳过依据、compact 回执和单调高水位一并提交。等待 writer 期间跨过语义日或首分钟模式边界时，撤销本次登记并重新准备，不以旧时钟提交。

回执只存版本、Stream 身份、Task ID 和跳过依据，Source 只冻在 Task。回执保存到既有 `runtime_values` 的新 namespace `stream_admission_receipts`，**没有新增表**。旧 `stream_admission_plans` 数组保持原样：这是为避免旧代码回滚时把新字典当旧数组读取，而作出的兼容调整。部分完成的旧计划仍按原冻结分组恢复，已有 Task 不改首次 `admitted_at`、原输入和原交易资格。

两库仍不做跨库事务：

| 中断位置 | 恢复结果 |
| --- | --- |
| Runtime 提交前 | 整笔登记回滚；Bus cursor 不前进 |
| Runtime 提交后、Bus ACK 前 | 重放读取原回执/Task，不重复准入 |
| Bus ACK 后、派发前 | 原 durable pending Task 被既有恢复扫描领取 |
| 分组中途失败 | 全组回滚，不能只提交部分新 Task 或跨越失败消息 |

Bus ACK 保留每条 Stream 的短提交，没有额外引入连续前缀批提交机制；ACK 已移至 claim 前，不等待模型或 Case 执行完成。

### C. claim 不再传播内存锁等待

`RuntimeCoordinator._submit_task` 改为短锁预查 → 锁外数据库 claim/输入读取 → 短锁重核并注册 Future。数据库租约仍裁决两个 dispatcher 的竞争；claim 成功后遇到关闭或线程池提交失败，只按原 owner/token defer，不消耗失败预算。

用一个本地 `_claims_inflight` 计数覆盖 claim 到 Future 注册之间的空档，仅用于安全退出判断，不增加持久状态或业务限流。

测试还暴露原 `close()` 在持 `_tasks_lock` 时取消 Future 的死锁：`cancel()` 会同步执行重新获取同一锁的完成回调。现改为锁内标记关闭并保存 Future/原 lease，锁外 cancel/defer；回调即使删除注册项，也不会丢失需要归还的原 lease。

轮询由“整轮工作 + 固定睡 1 秒”改为单调时钟 deadline，默认周期 250ms。超周期不补睡、不堆积轮次，仍为单消费者、原 ticker 顺序和原页上限。

### D. 正式发布入口与消费空窗

改动集中在 `scripts/ops/storage_release.py` 和 Scheduler 协作退出入口：

1. 构建和无关角色替换继续在 Scheduler 服务期间完成；Worker/Scheduler 排在正式发布顺序最后。
2. Scheduler 只在 ingress 轮次边界尝试退出。原任务、付费结果 owner、W3、公共准备或 claim 正在运行时拒绝切换，继续正常消费；不先停止消费后等待长时间模型排空。
3. Worker 先做只读在途检查，再请求 Scheduler 空闲退出，最后使用原 queued/running Job writer fence 检查并停止空闲 Worker。期间新 Job 到达则中止，恢复本次操作停止的 Scheduler。
4. owner 单文件复制到独立审计目录也能运行，不依赖额外同目录辅助脚本。暂时关闭旧容器自动重启，防止协作退出后被 `unless-stopped` 立即重启；忙碌/失败退出恢复原服务及策略，成功替换由新 Compose 配置接管。
5. Worker-only 发布、角色健康检查失败和后续记账失败均覆盖 Scheduler 恢复；已受理 Job、原请求身份和必要 paid receipt 不删除、不重派。
6. 记录协作停止请求/完成、运行侧接受退出、首成功 poll 与首 Stream 登记时钟；常态不增加业务更新。

**首次上线限制：** 当前旧镜像若尚无协作退出能力，正式 owner 在发送 USR1 前明确拒绝，不把默认终止信号当作安全退出。第一次升级需使用已审查的旧版排空流程，尽量缩短真实切换窗口；之后才可使用本轮协作协议。旧临时发布 wrapper 未作为通用入口改写，后续应使用正式 owner，不能继续调用会提前停消费者的历史脚本。

单消费者进程替换仍有真实短空窗；本轮消除可避免的长时间停顿，不承诺持续繁忙时无缝更换消费者。忙碌时退出发布尝试，不强杀模型调用。

## 3. 观测与未扩大项

沿用 `w3_timing`、Runtime journal 的 gate/BEGIN/commit 时钟和既有有界异步诊断通道，记录 round/ticker/Stream 的准备、登记、Bus cursor、派发与状态保存区间，慢于一秒才输出详细 trace。真实登记返回与 cursor 返回使用独立 mark，不用 Case `created_at` 反推 commit 完成。不记录正文、模型输入或密钥。

Scheduler SQLite 改 WAL 是原方案的**条件性 P1**，本轮未启用：先降低写入，再用生产残留等待判断是否需要。未放宽 SQLite timeout、租约、重试预算；未扩大到无关维护或模型改造。

## 4. 本地验证与证据

证据目录：`eval/runtime_ingress_implementation_20261121/`。

- 相关完整回归：`regression-final.xml`，**160 passed，0 skipped**。覆盖准入业务、Stream/Native、调度、恢复、热点并发、执行和正式发布。
- 最终新增保护的聚合回归：**45 passed**，见 `final-focused.xml`、`final-focused.txt`；与前述套件有重叠，不能将两者简单相加当独立覆盖数。
- 补齐 activation、Stream head/cursor 批读和心跳批保存的分段时钟后，调度相关回归 **11 passed**，见 `timing-final.xml`；这些纯观测改动不新增数据库写入。
- Ruff F 类检查与 `git diff --check` 通过。
- 首次完整回归中三个旧测试在不可变 HEAD 独立副本也失败。本轮只将其断言/fixture 对齐当前已有契约：W3 路由独占事实编译、未知模型派发不自动重发，以及 Scheduler 测试装配补齐当前所需依赖。保留原回执、路由与幂等断言，不修改业务以迎合过时测试、不跳过失败。复现记录为 `head-baseline-test.txt`、`head-baseline-extra-tests.txt`。

关键新增测试包括：准入失败全回滚、多组原子性、旧计划部分恢复、CLOSED_SWEEP owner/window 与实时组混合、首分钟跨界重算、ACK 两侧崩溃、重复消费、计数不读正文、两个 dispatcher 只领取一次、claim 后 shutdown/submit 失败、同步 cancel 回调、空轮健康节奏及心跳并发覆盖保护、发布竞态和正式 owner 独立运行。

### 本地空轮微基准

`benchmark_empty.py` 使用临时真实 SQLite、10 个 ticker、实际新代码的空入站轮次，排除后台恢复工作：

| 轮次 | 墙钟 | 新连接 | 业务 DML |
| --- | ---: | ---: | ---: |
| 首轮 | 27.64ms | 4 | 0 |
| 后续四轮 | 8.47 / 8.53 / 9.30 / 9.18ms | 每轮 3 | 0 |

这证明空轮减量已落地；它不是生产负载、不是稳定 P95，不能将原生产约 140 次连接/轮与本地时长直接计算成实际收益。

## 5. 发布后必须补做的真实验收

1. 固定版本窄发布 Scheduler；本轮没有 Worker SDK 改动，不为入站修复额外替换 Worker。首次旧镜像按上述升级限制处理，保留全部已受理任务。
2. 连续观察至少完整交易日和开盘突发。全队列保留发布空窗，分别列已形成 Case、失败无 Case、跳过、在途和缺时钟，不能只看成功 Case。
3. Bus→真实登记返回 P95 目标≤1 秒；10 ticker 空轮 P95≤100ms、普通登记≤150ms 为初始预算。逐项区分轮次等待、正文读取、准备、gate、BEGIN、SQL/commit、claim 与 cursor；未知区间不填零。
4. 准入后 inputs_ready 与完整 Runtime 非 SDK 指标同时核对；既有 inputs_ready P95 约0.380秒不得明显回退，完整 Runtime 目标仍为 P95≤10秒。
5. 确认250ms轮询下 CPU、写入和内存没有持续放大。游标追平仅证明队列已消费，不代替延时与恢复正确性验收。

本轮不将本地通过等同于生产达标。后续若仍有 writer 长尾，应按新增 Stream 关联时钟定位实际持有者，再决定条件性 P1，而不是继续扩大超时或凭经验归因。


## 6. 生产发布与首例验收（2026-11-21 北京时间）

- 代码提交 `5391ea39f0e3ac59d3d3bcfe468801369f79cc18` 已推送 origin/main，远端同步。仅以当前实际生产Scheduler镜像为基底复制本次10个修改源文件并预编译；不替换Worker、模型、提示词及其他角色。
- 构建与等待期间旧Scheduler持续工作。自然空闲检查多次因真实W3 Effect拒绝切换。旧镜像不支持新协作退出协议，因此按已说明的首次升级路径发送正常TERM并等待有序排空，不强杀、不取消付费Job。排空加替换38.199秒；这是真实发布消费空窗，不从统计中删除。
- 新Scheduler启动：北京时间02:26:16.831；首成功poll：02:26:25.510，覆盖10个ticker。运行镜像与候选一致，10份源文件SHA-256全部吻合。
- 首条自然样本INTC：Bus 02:28:11.694400，admitted_at 02:28:12.142572，真实登记返回日志02:28:12.157387，inputs_ready 02:28:12.357654。Bus到admitted_at约0.448秒，到登记返回约0.463秒；admitted_at到inputs_ready约0.215秒。入站Task为SUCCEEDED；这是入站任务成功，不将其等同于全部W3业务或交易完成。
- 检查时10个ticker的Bus cursor均追平head；新compact登记回执1份，启动后日志未发现ERROR/Traceback。游标追平及单例成功均不代替持续P95验收。
- Scheduler短窗内存约354–362MiB，CPU约1.2–1.4核；这是发布早期观测，没有同负载对照，不能断言资源效率已达标。服务器剩余磁盘约12GiB、既有Swap接近满，未在本次发布中删除业务数据或重启其他服务。持续资源及完整交易日延时验收仍待后续观测。
- 本地证据：`production-acceptance-latest.json`、`production-natural.txt`、`production-followup.txt`；远端正式owner回执：`/var/lib/doxagent/releases/ingress-5391ea39f0e3/release.json`及相应`w3-latency-5391ea39f0e3`构建/配置记录。私有配置仅留远端，不导出。
