# Runtime 非 SDK 延时：根本机制补证与精益修复实施方案

2026-11-18。本轮仅做代码审查、生产观测、隔离控制 RPC 和离线机制验证。**未修改业务源码、生产配置、Prompt、Skill 或模型 Schema，未发布或重启服务；没有主动发起模型 turn。**

本方案承接 `runtime_nonsdk_remaining_critical_path_audit_and_plan_20261118.md`，以本轮新增证据修正实施优先级。上一轮已有效的即时领取、Future 续接、v6 业务事务合并、每日索引和 Fact Bridge 不重复开发。

## 一、总体判断：先消除供给阻塞与无效工作，再调整容量

现在可以把主要问题收敛到四条互相放大的链路：

1. **基础 READY 补货仍可能被不具备启动资格的其他后台任务挡住。** `PriorityGate` 按所有等待者选队首，不按“当前可启动者”选队首；资源策略允许基础补货、禁止普通投机启动时，同优先级的旧等待者会挡住后来的补货。已有确定性复现，且补回的生产长准备记录显示大段时间发生在物理 Boot 之前。
2. **缺货后的同步冷启动集中消耗 CPU，同时 Native 执行不必要的插件市场工作。** 大量分支同时进入 Python 导入、Native 初始化及 Fork 后，原本约 3–6 秒的路径被放大。需要修正供给阻塞、删无关工作；不能仅增加 READY，也不能限制实时 Case 的启动。
3. **Scheduler 的共享 W3 异步线程还在做 v6 不再需要的完整 PolicySet、Reference View 准备。** 实际进程栈已采到同步解析、编译和哈希。它既增加每轮准备，又可能延后同一 loop 上其他 Case 的回执消费与写入重试恢复。
4. **跨进程维护的“有界”处理没有真正约束检查工作量。** Native GC 的 `LIMIT 32` 限制的是最终找出的可删除项；当大量历史资产仍受引用保护时，仍可能在 Runtime writer 内检查大量资产。应改成检查候选有界、重工作在事务外、短事务做最终引用与身份核验。

结果保存需要局部减法，但应与上述根因一起处理。仅把双分支保存从两笔改为一笔，不能抵消其他进程长时间占据 writer，也不能解决共享异步线程上的同步工作。

**推荐先完成：准入资格修正 → W3 专用初始化精简及配置对齐 → v6 上下文减法/移出异步线程 → 维护候选有界和短事务 → 结果链减写。** Operations 使用已有每轮 tier 覆盖，可与结果链一起优化。库存先保持 4，不新增执行器、服务或常驻通用池。

## 二、版本、测量与当前尾部

### 2.1 实际版本没有变化

- Worker：`d227e7629826d065201df476705ce3d87dfb6bc2`。
- Scheduler：`1f961d5a4993ccf8dd2b33b00515e98a01323823`。
- 生产 Python SDK：`openai-codex 0.159.3`；Native 构建锁定上游提交为 `01fc69f4026735edfdf6789820549727a4867b11`。
- Transport Maintenance 的两个相关源文件前轮已与本地做过换行归一后的 SHA256 对比，内容一致；不能把旧镜像标签直接当成“修复没有发布”。

### 2.2 扩大窗口后，尾部比 34 例窗口更重

统计截止 **2026-11-17 20:55:30 UTC**：纳入 99 个实时 W3 Case，98 个完整闭合、1 个尚未完成，窗口没有被最近 512 条候选截断。

| 非 SDK 指标 | P50 | P95 | 最大 |
|---|---:|---:|---:|
| Trade | 4.985s | 29.064s | 31.872s |
| Operations | 2.050s | 5.873s | 10.396s |
| W3 整体 | 7.098s | 30.606s | 33.780s |
| **完整 Runtime** | **8.991s** | **34.274s** | **37.875s** |

43/98 个完整 Case 超过 10 秒。最慢一组集中在 20:10 UTC，早于本轮约 20:30 UTC 开始的观测和后续隔离 SDK 实验，不能归因于本轮实验。

统计沿用原口径：并行模型时间取并集；W1R3 辅助推理不遮住 W3 准备；内部排队、重试、锁等待、Native 控制 RPC 均计入。阶段重叠不重复累计，缺失不算零。当前窗口包含有限诊断实验，其少量资源成本未从 Case 中人为扣除；这不是发布后的最终 SLA 验收。

### 2.3 本轮补证方式

- Scheduler 6Hz、Worker 4Hz、Transport Maintenance 3Hz，各 60 秒、`--nonblocking` 的外部 Python 栈采样，不读取局部变量。Scheduler/Worker 各有极少量采样错误，未将错误算为空闲。
- 有期限的进程、Boot、gate 与 PSI 观察；有界分段读取少量已存在的历史 timing 回执。
- 生产 Worker 容器内临时目录的 SQLite 实验。
- 复制一份约 100KB、已发布的公共 Template rollout 与认证文件到权限受限的临时 Codex home，逐个启动隔离 client，执行本地控制 RPC 后关闭、清理。不修改生产 Thread/Job/READY 注册表，不发起模型 turn，不导出认证文件。

一次旧版 stdout 长连接观察中途断开，未取得有效产物，不作为“没有慢事件”的证据；随后改为远端保存有界采样文件。

## 三、冷准备：现在能区分两种不同的长尾

### 3.1 第一类：物理 Boot 之前的后台供给等待

新增找回的生产回执：

- 槽位 `371b7347ca0f4230a1c05efd17b3787b`。
- 创建：20:10:23.471；绑定：20:10:53.289；READY：20:10:59.558 UTC。
- 完整 preparation 约 **35.895 秒**；`capsule.spawn` 到相对 **29.869 秒**才开始，实际 spawn **4.276 秒**。
- 最终绑定后到 READY 仅 **6.269 秒**。这里不能把创建以来的 36 秒都算成该 Case 等待；绑定前的等待影响的是公共库存供给。
- 该 operation 的 io.write 执行合计约 173ms、无错误；io.read 合计约 612ms。它们与父 span 有包含关系，不能相加来解释全部 29.8 秒。

另一个公共槽位 `7cf43c02396e45619cfec8d5cd1a7d4d` 构建约 **104.9 秒**，operation→Fork 标记约 **103.6 秒**，但 Boot wall 只有 **2.114 秒**、Python CPU **1.463 秒**，Native Fork 回执约 **591ms**。该槽位直到构建完成数分钟后才被 HIT；**104 秒不是当次 Case 的 READY 等待**，但证明后台供给可以出现大段空转。

这些实例纠正了“所有长准备都是子进程初始化太慢”的解释。

### 3.2 准入资格缺陷的确定性复现

位置：`codex_worker/startup_admission.py:PriorityGate.acquire`。

当前条件近似为：

```python
while active or min(all_waiters, key=priority_and_sequence) != me or not allowed_for_me():
    await condition.wait()
```

普通后台启动与基础 READY 补货都可能是优先级 1，但前者受 `allow_speculation()` 限制，后者应使用 `allow_baseline()`。当旧普通任务不获准启动时，它仍会被 `min(all_waiters)` 选为队首。

使用实际 `StartupAdmission` 的离线测试，令基础补货允许、普通投机禁止，先排普通任务、后排补货：

- 约 0.59 秒后：`cold_active=false`、owner 不存在、等待者 2 个、没有任何任务进入。
- 将该补货模拟为已绑定实时 Case 后，它通过现有 bypass 立即进入。

**这是已证实的代码缺陷。** 历史长例缺少同时刻的完整 waiter 身份，不能断言每个 100 秒等待都由它产生；但它与“绑定前长停顿、绑定后很快完成”的生产记录吻合，必须先修。

#### 最小修改

在已有 waiter entry 中保存自己的 eligibility 回调；队首只从**当前有资格进入**的等待者中按原优先级/FIFO 选择。保留现有 Condition 和有界重查，无新队列、无新优先级状态机。

- 当前 active owner 不可被抢占。
- 实时 W3 的 bypass 保持，不因后台 eligibility 检查增加排队。
- CRITICAL、quarantine 和真实内存不足仍阻止后台基础补货。
- 被允许的基础补货可越过当前不允许的普通投机任务；后者重新合格后仍按原序竞争。
- 取消路径仍在任何可取消 await 前同步清理逻辑 ownership，避免再次出现 gate 永久 active。
- 不采用“把所有补货改成实时”“去掉背景冷启动保护”或扩大同时 Boot 数来绕过问题。

回归覆盖：空闲 gate+不合格队首、资格恢复、相同/不同优先级、运行中的 owner、Case 动态绑定升级、取消和 quarantine。

### 3.3 第二类：真实并发冷启动的 CPU/控制链膨胀

找回的另外一批真实慢准备：

| 槽位 | preparation | spawn | Native Fork |
|---|---:|---:|---:|
| `49c3b5d7…` | 23.742s | 15.097s | 5.252s |
| `3625375c…` | 17.978s | 8.748s | 5.460s |
| `6d3014c4…` | 17.191s | 8.646s | 本轮未找到对应子回执 |
| `6a5b56c0…` | 21.251s | 15.016s | 本轮未找到对应子回执 |

例如 `6d3014c4…` 的 Boot wall **7.813s**、Python CPU **1.657s**；其中 Python/Runtime 就绪前 **6.045s**、SDK initialize **1.768s**。已有文件证明慢在 spawn/Native 区间，不能全部解释成 Worker SQL；该 preparation 的 io.read/write 执行合计仅约 66ms。

同一服务器的 sysstat 10 分钟粒度记录，20:00–20:20 UTC 对应本地 04:00–04:20：

- 8 CPU，idle 仅 **2.35–2.66%**，user+system 约 **96%**，steal 为 **0**。
- 04:10/04:20 的 run queue 分别 **38/48**。
- 存在文件页回收、直接 reclaim 及实际 Swap I/O；不能用“有 Swap 存量”替代这些增量。
- 已检查的应用祖先 cgroup 没有 CPU quota throttling，不能把它归因于已证实的 cgroup 配额节流。

这些是与慢窗口一致的调度/页缓存竞争证据，**仍不足以把某个 15 秒 Boot 的每一秒分到具体 PID**。应通过减少基础供给停顿和无关 CPU/I/O 工作打断放大链，而不是以“机器忙”为结论。

Native 约 5 秒、Python wall 约 15 秒的历史回执，不等于其业务必需成本；前轮已存在 0.75–1.43 秒 Native Fork 和 1.5–2.5 秒 Boot 的同版本实例。

### 3.4 后续冷路径观测的最小补齐

复用现有 boundary timing，加以下子 span，不新增持久化状态：

1. cold admission 等待；进入时记录 own rank、eligibility、当前 owner 和不合格队首标签。
2. subprocess 创建、`pid_start_ticks` 的 executor 提交/执行、首条 boot 消息、SDK_READY 到父进程消费。
3. handshake 等待、Fork 标记完成、pipe 发送、子进程开始 RPC、RPC 完成、父进程收到、最终短事务与槽位发布。
4. 仅对慢准备采样父/子/Native CPU、每线程 run queue、缺页与对应 cgroup I/O；缺页和线程等待不重复算墙钟。

已有 BOOTING 同步 checkpoint 删除、operation/registry 合并不再重复实施。库存 builder 有本地 preparation task 时直接等 task 完成后读取一次终态；没有本地 owner 的恢复才查原 operation。绑定升级所需通知和 deadline 仍保留，不删除整个监视器。

## 四、W3 专用 Native 初始化：已经验证可以删的工作

### 4.1 插件市场关闭的真实控制 RPC 实验

首次用未执行过 turn 的空 Thread 做 Fork 得到 `no rollout found`，不作为性能样本。随后改为复制生产已发布公共 Template rollout，在隔离 home 中执行：恢复公共模板 → Fork → 同配置 Resume → Operations 配置 Resume。

| 项目 | 关闭插件市场 | 默认插件设置 |
|---|---:|---:|
| initialize | 0.234s | 0.315s |
| 公共模板 Resume | 0.979s | 1.442s |
| Fork | 0.313s | 0.569s |
| 同配置 Resume | 0.025s | 0.010s |
| Operations 配置 Resume | 0.025s | 0.026s |
| 关闭后子进程累计 CPU | 1.963s | 3.463s |
| Native `/proc/io.write_bytes` 快照 | 10.53MB | 133.54MB |

每种仅一组，顺序和主机负载存在影响；这些值不当作 P95、固定加速比或设备净写入。它们支持“相关控制能力不依赖插件市场，默认市场工作确实增加资源”，不支持“15 秒已变成 1 秒”。

额外关闭市场的工具发现实验成功列出 `w3_research` 的 **catalog/search/read/market_view** 四项工具；AGENTS 与唯一 `skill.md` 的 SHA256 相同。没有真实模型 turn，因此没有据此宣称研判质量验收完成。Web Search 应保持现有 live 配置，后续自然业务 canary 验证，不改成禁用。

具体启动 override：

```text
features.plugins=false
features.remote_plugin=false
features.recommended_plugins=false
```

只关 recommended_plugins 不够。应在 client 初始化前通过现有 `CodexConfig.config_overrides` 设置。

#### 实施边界

- 在 Capsule 创建时明确其 W3 bootstrap profile；生产 W3 template、private prepare、恢复和 Trade/Operations 使用一致设置。
- 只增加最小的 profile 区分，不新建 client manager。空闲物理 Capsule 选择必须检查 bootstrap 兼容性，不能把关闭插件的 W3 client 分给需要插件的其他工作流。
- 纯控制用途的 lifecycle helper 同样不需要市场发现，可使用相同关闭项；D2/D3 等模型工作流保持原配置。
- 保留本地 AGENTS/Skill 继承、显式 Research MCP、Web Search、沙箱、模型与 effort。
- Native 仍能发现一些内置系统 Skill；本轮不据此启用实验性 `skip_host_skill_discovery` 或删除系统目录，也不改 W3 的提示词语义。
- 第一批不修改 Native 底层实现。

### 4.2 修复 READY 指纹差异

W3 prepare 和 recovery request 显式传 `allow_subagents=False,max_subagents=0`，与实际 Trade 一致。现有默认 2 会生成 `agents.max_threads=2`，实际 Trade 生成 1；已用真实适配器+Fake SDK 证明它触发额外 Resume。

不修改全工作流默认值，不放宽权限/profile 比较。存量旧 resident 若不兼容，仍正常 Resume；不能假装其配置已经一致。

### 4.3 不为“简化”删除恢复所需的 Native 数据库

实测文件大小约为：`state_5.sqlite` **407.6MB**、`logs_2.sqlite` **6.21GB**、`thread_history_1.sqlite` **3.80GB**。最近日志含 TRACE outgoing-message 和 DEBUG stream-event 项。

这些尺寸是历史存量，不等于当前增长速率，也没有证明历史库单独造成了 Boot 长尾。不能关闭 SQLite、删除 Thread history 或迁移到一个没有原身份的新 home 来规避恢复成本。

锁定的[Native 日志实现](https://github.com/openai/codex/blob/01fc69f4026735edfdf6789820549727a4867b11/codex-rs/state/src/log_db.rs)使用独立的默认 TRACE filter；app-server 的 `RUST_LOG` 控制 stderr 层，不能据此承诺降低 SQLite 日志量。第一批先移除市场工作和重复控制调用；若文件级观测证明该日志 sink 仍是主要持续写入者，再考虑仅作用于 Worker 的最小 sink filter 补丁，保留 WARN/ERROR、业务 receipt、rollout 和完整审计。**不把 Native 补丁作为本轮首批修复的前置条件。**

## 五、共享 W3 异步线程：删除 v6 遗留上下文工作

### 5.1 已采到实际执行，不只是代码猜测

Scheduler 60 秒栈采样中，W3 loop 有 357 个有效线程样本：348 个停在 epoll，其余采到的实际工作包括：

- `PublishedW3ContextProvider.load → _load → get_version → parse_policy_set → JSON/Pydantic`。
- `_reference → reference_view → published_events`、Reference Markdown 编译。
- `_install_context` 的 HTTP JSON 编码和摘要计算。

不能把约 97.5% 的 epoll 样本当成“没有长尾”：短时同步工作会阻塞同一 loop 上其他 Case 的通知与重试，空闲时间再长也无法补偿该次延迟。采样没有证明这就是历史 MU 那个精确 1.430 秒的全部来源，但已证明应清理的路径实际存在。

当前 `PublishedW3ContextProvider._load` 仍取完整 PolicySet、Policy origins、Reference View，并把它们纳入部分身份计算；v6 `_install_context` 最后却只发送 D1/D2。异步函数中多项同步查询、JSON/Pydantic 和编译未离开 loop。

### 5.2 最小实现方案

在现有 Provider 中按冻结的 W3 contract 分支，不另建上下文系统：

1. v6/PIN-WATCH 路径只加载按原 pin 定位的 D1/D2、原始正文和必要 provenance/hash/发布时间；跳过完整 PolicySet、Reference 编译及已消费 Policy 投影。
2. W1/W2 判定、case_type/tags、WATCH/PIN 和消息仍由原 Case 冻结输入提供。不是把 W1/W2 的业务结论删掉。
3. 将现有内部 `W3PreparedContext` 中仅旧契约需要的 Policy/Reference 字段显式允许不适用，v6 返回 None；旧契约加载分支继续强制校验存在。不伪造一个空 PolicySet 通过校验，不改变发给模型的 input/output Schema。v6 的 `_install_context` 不再对 Reference 字段做正则，保存的旧引用列表按“不适用”处理。
4. 旧 v4/v5 与历史回放仍走原加载、引用验证与身份逻辑；v6 不再创建没有消费者的 citation scope/Reference 派生物。
5. 复用已有缓存和组件内容身份。若 v6 组件身份不再包含 Library/Policy 内容，Case 自己的 `version_pin` 仍来自其原冻结 Case，不能从另一 Case 共用组件记录中借用错误的 Event/Policy 版本。
6. 现有旧 key 保留读取；新 v6 内容身份使用明确的 v6 discriminator。不能静默覆盖旧 frozen refs，不能按当前最新 D1/D2 替换旧 pin。
7. 所有保留下来的同步 repository/解析/编译工作放到现有 I/O 执行机制中；不新增专用线程池。批量取得同步 repository 元数据，保留真正异步 published-storage 的 await，解析/校验/哈希集中离开 loop；不能把整个 async loader 直接传给 `to_thread`，也不在新线程里另建事件循环运行原 storage client。保持按既有内容身份去重，避免把串行 loop 阻塞简单变成重复并行编译。

验证方式：v6 测试依赖的 Policy/Reference repository 设为“调用即失败”，D1/D2 仍正确加载；旧契约必须仍调用原依赖。对照原冻结样本核对 Agent 实际收到的 D1/D2 内容、消息、判定和 WATCH/PIN 均不变；再用共享 loop 的并发消费测试确认一个上下文准备不阻塞其他成功结果。

## 六、Transport Maintenance：约束检查量，缩短 writer 占用

### 6.1 新证据指向无效的全量候选筛选

60 秒 Maintenance 栈采样共 179 点：140 点在维护周期 sleep；**30 点在 `source_native_gc.collect` 第 172 行候选 SQL**；其他少量在 inventory 的目录枚举等路径。它是该窗口采到的主要活跃位置，不能把整段都归为 manifest 文件读取。

该 SQL 在 `BEGIN IMMEDIATE` 之后运行：对 aged LIVE Native 资产判断 NOT EXISTS references/hold，然后 `LIMIT 32`。如果没有符合“可删”的项，LIMIT 不会阻止扫描大量受保护资产。

本地同形 SQL、全受引用保护的数据：

| 资产数 | 当前查询返回 | 约 VM 步数 | 当前查询耗时 | 先取 64 个裸候选 |
|---|---:|---:|---:|---:|
| 20,000 | 0 | 520,000 | 14ms | 0.13ms |
| 80,000 | 0 | 2,080,000 | 50ms | 0.11ms |

本地耗时不外推生产。实验说明算法工作量随历史规模增长，即使没有删除结果；生产栈及前轮 writer 归属采样说明它确实运行在关键数据库的写入竞争面上。

### 6.2 明确采用的修改方式

保留当前 SourceOutbox、artifact/ref/hold、snapshot pin 和 quarantine，不新增维护调度器：

**collect 候选：**

- 按稳定主键先分页读取至多 64 个资产标量，再检查 age/codec/location/state。不要把 NOT EXISTS 放在无界前缀中、再以“找到32个”作为退出条件。
- 分页游标复用 `v2_transport_gc_state` 的独立 kind，例如 `native-age-scan`，保留原 transport cursor/floor；如存 TEXT 主键，用 `id:` 前缀避免 INTEGER affinity 改写。不要混进决定 refs-ready 的 `v2_native_ref_scan`，避免循环依赖。
- 单页之外可在现有维护周期预算内继续，初始每 source 最多 50ms/4096 个检查候选；这是后台检查预算，不是实时 Case 限流。整库一轮结束后循环，因此主键较小的晚到项不会永久遗漏。
- 显式 owner-exit 仍优先处理；通用分页继续覆盖废弃 put 和历史遗漏，不能只依赖当前未覆盖全部消费者的 exit 信号。
- 仅对少量实际候选进入短事务，重新确认 source 当前可见身份、所有引用、hold、snapshot lease 和资产状态，然后决定迁入 quarantine。文件递归删除在提交后进行。

**reconcile：**

- schema/table/PK 定义读取、候选 payload 读取和 JSON/native-ref 解析放在 writer 外；只按已有 schema_version 复用一份进程内 schema 说明，不建新的业务缓存。
- 已完成回填的路径不重复注册同一套 owner/scan 元数据。先短读确认是否有实际变更。
- 短写事务内核验 mutable owner 的选中原始值/现有版本仍相同，再替换引用和推进 cursor；并发变化则留待原游标重查，不宣称 ready，不删旧保护。
- 正常 Source capture 对新引用的事务保护不能因后台回填优化而被撤销。

**inventory：**

- 目录枚举、排序、manifest 读取/校验/stat 在事务外，单页中去重；仅登记原 source/digest 对应的标量与 cursor。
- 目录数量也要有界，不让“成功登记64个”的限制掩盖扫描海量目录。
- 原子替换/晚到文件按现有格式验证，不能将缺失或变化当作已经成功登记。

**expire 与文件退出：**

- 候选读取、pack 解压和 JSON 准备移出 writer。短事务重核验消费 checkpoint、epoch、pin 和待删除身份，再删除在线记录/登记原退出表。
- 仅必要的原子 rename 留在短保护区；manifest 编码及大文件处理移出事务。最终递归删除不能直接“提交后 rmtree”了事：必须先验证 publication/新引用复活与删除代际的保护，不能删掉刚恢复的 canonical 文件。
- 第一批先将无变化扫描、解析及目录读取移出 writer，退出每笔最多一个实际资产；最终 unlink 若尚未有可证明安全的脱离方式，保留原保护边界，实测其耗时，不用它阻塞主要 SQL/扫描修复。需要移出重删除时，复用已有 artifact 退出状态和独立物理路径的 claim/recheck，先通过“put 后尚未提交引用、并发恢复、新引用到达”的竞态测试，不另造一套 GC 状态机。
- 复用已有 quarantine/退出记录完成中断恢复、新引用恢复和残留清理；提交后崩溃不能留下永远无人清理的路径。不能以少持锁为由削弱文件发布与引用的原子保护。

每页提交后释放 writer，沿用原维护周期与资源规则。先保留总维护频率，测出有效吞吐和无变化成本后再调整低价值重复检查的频率；不要先停掉维护或单纯无限延长 sleep，把性能问题变成磁盘积压。

### 6.3 正确性与时效验收

测试包含：全受保护大库、0 个可删项、晚到小主键、过期但有 hold、snapshot 跨页、引用并发新增/删除、quarantine 后引用恢复、删除中断、旧 archive/pack pin、cursor 重新启动。要求检查数有上界、JSON/文件操作不在 writer 内，事务通常为毫秒级；正常资源下 writer hold P95 以 **20ms** 为工程目标，若单页接近 50ms，减少后台页大小或删除页内无效工作，而不是放宽 timeout。

## 七、成功结果链：保留恢复屏障，删除重复中转

### 7.1 更新后的证据

99 例窗口共有 295 个阶段回执消费区间，`worker.result_received → worker.receipt_saved` P50 **97ms**、P95 **714ms**、最大 **5.076 秒**。

- 原 CEG Operations **2.576 秒**已确认发生在 Scheduler 收到成功结果之后，不能再归为 SSE/Future 迟到。
- 新例 `case_021eac8122d84bd09bb92416539445af` 有 **5.076 秒**消费区间；其完整 phase trace 有 8 次 SQL 错误及明显 writer 排队。phase totals 包含收到结果前的动作，**不能把 totals 全部加到这 5.076 秒上**。
- 原 MU 最后 owner 的 1.430 秒空档尚无当时的 coroutine/线程栈，仍保持未知。新增的 loop 同步准备证据和维护争用给出了具体可修的机制，但没有精确复原历史空档。

### 7.2 双分支保存

`run_trade_pair` 已通过 gather 获得本轮两个结果，当前 owner 却分别执行 save、读 stage/request、编码 refs、入 writer：

1. owner 在事务外一次读取所需分支 stage/request 标量，准备结果、refs、hash 和 ACK intent。
2. 两份有效结果用一笔已有短事务分别调用 `save_result_in_transaction` 并写 refs/ACK intent；不新增 batch 持久化对象。
3. 混合成功/失败仍先保存本轮有效结果，再传播原异常；取消、租约丢失、控制错误不被“批处理容错”吞掉。
4. 原保存结果不可变，原 Job ID/generation 不得替换。只对必要新保存写入；已经保存的结果不重新产生 ACK/结果副本。
5. 原共识聚合与交易提交继续走现有原子业务边界，不把交易动作搬到保存回调，也不重复交易。

先做上述最小批保存，不把回执、分支、交易、Effect、Task 五种职责强行拼成一个巨型事务。

### 7.3 Worker 正常成功路径

当前成功结果会先更新 execution timing，再写 CLEANING，最后写终态；每次 `_update` 都持久化 Job、写 audit 镜像及事件。

- 对**可靠成功 receipt 已存在、只释放 busy 而继续保留同 Case resident**的路径，把 timing 与终态字段准备为一个快照，完成必要 release 后一次保存和通知。
- 去掉该快路径仅用于中转的 CLEANING 和重复完整镜像。真正的失败、进程终止、cleanup unknown/quarantine 继续保留清理状态与最终确认。
- 不删除 SDK completion receipt，不把“任务已算完”与“物理资源已释放”混为一个状态；崩溃后仍可从原 receipt 收敛，不再发模型。
- 终态 SQL 成功后先通知的已上线优化保留。HTTP progress observer 也已经离开异步线程并去重，不重复实施。

### 7.4 回执、owner 读取与收尾

保留 `worker_invocations` 的首次调用事实和当前 generation 的 `worker_receipts`，先预编码、再短事务按原 idempotency key/fencing 保存。可复用同一 owner 已读取的 stage/request 和必要 Case 标量，但跨 Future、跨 owner 或恢复后必须重新取权威状态，不能覆盖并发 W1R3 的更新。

v6 Operations 的 WATCH/PIN、stage、Case/route 与线程释放已合并，保持。Effect/control 收尾先减少重复编码、读取及无变化投影；只有确认同一 fence、同一原子职责时才合并尾部短写，不删除 ACK intent 或提前标记完成。

给 write_result 增加现有 trace 内的 submit、loop start、writer callback start/end、loop resume 时钟。它们用于确认还剩多少 loop/队列成本，不用于替换业务结果时间或给内部等待改名。

另外，`journal.transaction(timeout=0)` 当前仍设置 `busy_timeout=100`；本地对外部写锁实验等待约 **173ms**才失败。修正显式零等待语义：零预算使用非阻塞 gate/SQLite busy_timeout=0，正预算不超过现有上限。它是小范围语义修复，不是 MU 1.430 秒的已证实解释，也不扩大任何超时或重试预算。

## 八、Operations：用现有每轮 tier 覆盖减少必要性已消失的 Resume

同模型、同权限的当前 Luna Case，Trade→Operations 原生 Resume 的主要合法配置变化是 fast/default。生产 SDK **已经暴露** `AsyncThread.turn(...,turn_service_tier=...)`，映射为 `serviceTierForTurn`；锁定的[Native turn processor](https://github.com/openai/codex/blob/01fc69f4026735edfdf6789820549727a4867b11/codex-rs/app-server/src/request_processors/turn_processor.rs)将其作为本轮 start option 消费。

**推荐实现：**将 W3 client/thread 的稳定配置与每轮服务 tier 分开。当前 Luna Trade 明确传 fast，Operations 明确传 default；不再仅为这项本轮参数重新 Resume 整个 Thread。

- 只处理已确认的 per-turn 差异，不忽略模型、provider、effort、沙箱、MCP、cwd、指令版本等真实 scope 变化。
- 同模型当前 v6 可直接使用 resident handle；旧 Sol→Luna 等模型切换仍保留必要 Resume。
- Native Thread ID 不变，Operations 全文规则仍按原逻辑注入；本轮不改 Prompt/Schema。
- 历史回放的 tier override 保持，Operations 标准速度规则不被覆盖。
- 仅在启动新的 Operations turn 时应用 default；原 Job 恢复/重连不创建新 turn，已运行 turn 不因重试更换 tier。
- 验证有效的**本轮**服务 tier，不能只看 Thread 默认 tier/UI fast 标志。出现版本不支持或实际 tier 不符，保留现有合法 Resume 路径，不静默忽略。

隔离实验的 Operations Resume 约 25ms，生产慢例约 1.506 秒，说明不应把 1.5 秒当成必要步骤标准。per-turn 方式可直接去掉该 RPC，但总体收益仍取决于准备/结果链同时改善。上线前需一个受控业务 canary核实实际 tier；本轮没有用真实模型完成此项业务验收。

## 九、Worker GC、持久化与其他生命周期减法

### 9.1 GC 保护水位回填不再恢复 COLD

前轮已证明 `gc_derivatives` 的旧上下文水位回填调用 `protect_components`，会触发 COLD 编译。修改为回填页内聚合 component→最大有效 expires_at，只更新 retained_until，绝不 build。实际 bind/read 再从不可变 source 恢复。

NULL 资产在回填完整结束前继续保护；游标只在本页水位写入成功后推进。单页文件读取、JSON 编码不在 writer 内。复用原字段和 cursor，不新增回填系统。

### 9.2 Worker SQLite：本轮补了 Linux 同容器实验

临时约 5MB 数据库、100 次 FULL 小 UPDATE：

| 方式 | 总耗时 | 单次 P95 | shm 每次关闭后消失 | `ru_oublock` 增量 |
|---|---:|---:|---:|---:|
| 每次开关最后一个连接 | 0.403s | 6.25ms | 100/100 | 9,600 × 512B |
| 保留一个空闲、无读事务连接 | 0.170s | 2.97ms | 0/100 | 1,608 × 512B |

这是受控实验，不是生产 2.4 倍 SLA 改善。结合前轮 Worker shm 频繁消失的观测，推荐使用现有 JobStore/短写 owner 生命周期保留一个无快照事务的连接，减少最后连接关闭的 WAL churn；不新建连接池或后台线程，不改 FULL，不关闭自动 checkpoint。关闭流程中释放该连接，验证 WAL 可控及正常回收。

### 9.3 旧 Thread 删除

保留原 run/parent/thread 身份、active Job 检查和 tombstone/retire 状态。`thread not loaded` 不代表持久化 Thread 不存在，10 秒 RPC 超时也不代表未受理。

**本轮协议实验修正了前轮的一个假设：新 helper 未加载过隔离子 Thread，也能成功直接 delete，随后 Native 索引行变为不存在；再次 Resume/Delete 得到 no rollout found。** 因此不能将所有 `thread not loaded` 都解释为“必须先 Resume”，不应无条件给正常删除增加一次加载。

生产旧槽位 `da4e3dd2…` 仍 RETIRING、保留原 not-loaded 错误，但对应 Thread 已查不到 Native state index 行。这更像需要核实终态的退役残留，尚不能仅据此断定所有文件表示都不存在。

实施时先沿用同身份 delete；错误后用原身份、原 run/parent 和 Native 支持的查询核实，不因网络/5xx/超时或单纯 not-loaded 就标成功。只有确定已经删除/不存在，才幂等收敛旧 registry/slot；若有持久化 Thread 则走该协议支持的同身份处理，不重新 Fork。保留原审计，避免无条件 Resume 再 Delete 的额外工作。

当前没有它阻塞实时 Case 的直接证据，因此与 metadata GC/keeper 一起列为后续小批，不作为暂停实时业务的理由。

## 十、库存、资源竞争与实施依赖

### 10.1 库存决策

前轮 4 份库存约 90% 时间充足，但一度 70 次领取中 12 次 MISS；本轮又确认基础补货本身会被错误队首挡住。**先修队首资格和单份成本，再比较 4/6。**

修复后记录 READY 可领取区间、后台 unbound 构建、绑定后实时构建、完成→下次补货、空库存和需求突发。按实际新服务时间做 trace 回放，不把已绑定的冷构建时长当作后台构建者持续占用。

只有仍存在供给缺口且冷路径不能满足整体 10 秒时，再做单次 4→6 库存试验。前轮 4 份约 1.34GiB PSS，6 份的额外约 0.67GiB仅是粗估。有限双构建流水线也只在证明 CPU/IO 和吞吐收益后采用；二者不同时盲目放大。

实时 MISS 保持直接准备，不等待库存，不新增 W3 并发门槛。

### 10.2 全局资源只作为有归属的证据

本轮一个 15 秒容器差分窗口：Chrome Supervisor/Site Access 分别使用约 **20.88/18.75 CPU 秒**，Scheduler 约 **11.20 CPU 秒**，Worker 约 **3.61 CPU 秒**。证明当时竞争者不限于 Worker；该窗口不与 20:10 慢例同时，不能倒推两者历史份额。

应同时消除 W3 多余 Native 工作、Scheduler 遗留准备和维护扫描，减少供需放大；如仍需调整其他后台任务，只用已有资源规则并以同窗证据为依据，不借此降低实时并发或把整个系统停下来。

磁盘与 Native 库增长另报文件级增量，区分可回收空间、逻辑文件大小和真实新增块。已经 6.21GB 的 logs 库并不证明某一分钟所有写入都来自它。部署只清理确认无引用的构建缓存/旧镜像，不删除 profile、卷、SQLite 或原始 rollout 来制造余量。

### 10.3 实施顺序

| 批次 | 改动 | 关键文件 | 验收后再推进 |
|---|---|---|---|
| A | eligible 队首；max_subagents 对齐；W3/control-only 关闭市场；v6 上下文减法并移出 loop | startup_admission、capsules/capsule_child、thread_lifecycle、w3_ready、w3 | 基础补货可前进、真实权限完整、旧恢复不变、无多余 Resume |
| B | 维护按检查候选有界；读/解析/文件操作移出 writer；结果双分支批保存 | source_native_gc、source_lifecycle、transport_archive、w3_execution | 大库零删除也有界；引用与事务正确；paid结果可恢复 |
| C | per-turn tier；成功 Job 中转减写；回执/owner 减重复；补齐残留交接时钟 | sdk_runtime、jobs、worker_receipts、w3_execution | Trade fast/Operations default 实际生效；成功结果不会重发 |
| D | COLD 水位回填；无读事务 keeper；本地 task 等待；旧删除协议；库存 4/6 决策 | w3_resources、w3_research_store、job_store、w3_ready、thread_lifecycle | 资源收益实测，且没有新增恢复/退出缺口 |

在批次 A 中先做准入修复和配置对齐，再做 client/上下文精简，避免一次变更多种权限边界；每个小提交留 changelog。B 的结果侧改动和维护改动虽有关联，也分别保留前后测量，避免无法判断收益来源。

## 十一、正确性与性能验收

### 11.1 不可改变的边界

- 新 Case 独立物理 Thread；A/B 不共享对话历史；同 Case Trade→Operations 同线程。
- 受理/明确未受理/未知派发三态保持：只有确定原 Job 不存在才能考虑新派发，网络错误不当 404。
- paid completion receipt、当前 owner fencing、结果不可变、交易提交原子性、ACK intent 和资源 release 继续成立。
- Provider Pause 不影响已成功结果；取消、租约丢失等控制异常不转成普通输出重试。
- W1/W2 路由、NEW/OLD、HIT/RELATED、LONG/SHORT/NO_TRADE、WATCH/PIN、模型/effort、冻结输入和 Prompt/Schema不改变。

### 11.2 必要测试

1. Gate：不合格队首、资格变化、不同优先级、取消、绑定提升、CRITICAL/quarantine及 owner liveness。
2. Context：v6 不调用 Policy/Reference 依赖；旧契约仍读取；跨 Case共用组件不借用错误 pin；并发载入不阻塞已有成功回执。
3. SDK：关闭市场时模板继承和四项 MCP 工具可用；Web Search 及原权限配置保留；真实 scope变化必须 Resume；实际每轮 fast/default正确。
4. 双任务：两成功、部分失败、全部失败、结果保存冲突、保存后 lease 丢失、提交前后重启和晚到 ACK；不重新调用成功分支、不重复交易或运营动作。
5. Maintenance：大库所有资产受保护、零删除、晚到文件/小主键、并发引用、pin/hold/snapshot、quarantine 恢复和崩溃续接。
6. Worker：成功快路径、receipt 存在但终态未保存、cleanup 未知/失败、ACK 先于镜像完成、连接关闭、WAL checkpoint 和资源归属。

### 11.3 分批生产验收

每批使用连续自然窗口，不择取快 Case。至少 100 个完整实时 Case且覆盖需求突发与 MISS；同时报纳入、未完成、失败和缺失时钟数量。最后再覆盖一段足够长的真实高峰，不把一个短窗口当稳定 P95。

工程预算仍从实际工作量出发：状态/通知毫秒级、少量短事务几十毫秒；正常准备尽量 3 秒量级，冷准备也要进入完整指标。最终**完整 Runtime 非 SDK P95≤10 秒**，并继续压低 P50；内部排队、锁等待、重试、Native初始化/RPC和本地工具全部计入，外部 Provider 等待按原证据单列。

每批同时验收：

- 无 active cold owner 时，不再被不合格任务挡住基础补货；实时 Case 不排人为队列。
- 无新增同 profile Resume；插件市场大写入消失，必要模型/工具仍正常。
- 维护检查数量与单页时间有界，无 JSON/目录/递归删除长持 writer；结果 BEGIN 重试和 loop 恢复间隙下降。
- 不增加常驻线程池/服务，Worker 子树 PSS、进程数、CPU/IO PSI、Swap 实际增量和磁盘增长没有恶化。
- 仍不达标时根据残留子 span 调整，不延长 timeout、不加重试、不改计时口径、不排除冷启动。

本轮没有精确复原所有历史 15 秒 Boot 和 1.430 秒调度空档；已将可复现准入缺陷、实际无效 CPU 工作、无界维护筛选与结果链成本分开。**推荐先实施这些有证据的机制修正，再评估剩余冷启动物理成本和库存，而不是继续增加保护层。**

## 十二、证据文件

目录：`eval/runtime_latency_root_mechanism_20261118/`。

- `acceptance-end.json`、`cohort-end.json`、`logs-end.json`、`followup-summary.json`：99/98 例及295段回执时钟。
- `admission-hol-test.json`、`admission_hol_test.py`：真实准入类的确定性复现。
- `selected-timing*.json`、`historical-prepare-summary.json`：有界取回的历史准备/Fork回执，保留截断标记；有些子回执未取得不算零。
- `scheduler.raw`、`maintenance.raw`、`worker.raw` 与采样日志：不读取局部变量的实际进程栈。
- `sdk-template-probe.json`、`sdk-tools-probe.json`、`sdk-signature.json`：隔离真实 RPC、工具列表及生产 SDK参数；`sdk-probe.json` 保存最初空 Thread 的失败，不纳入有效 Fork样本。
- `sdk-delete-probe.json`：隔离环境中的旧 Thread 删除协议实验。
- `candidate-bound-test.json`、`linux-wal.json`、`zero-timeout-test.json`：候选工作量、Linux连接生命周期与零等待语义实验。
- `observation-gates.json`、`sar-slow-window.txt`、`container-resources.json`：有时窗的资源与 owner观测。
- `native-state.json`、`inventory.json`：数据库结构/大小与槽位标量；无模型思维链或凭据输出。
- `upstream/`：锁定 Native 提交的相关源码。前轮完整证据仍位于 `eval/runtime_nonsdk_deep_attribution_20261118/`。
