# Runtime → Read 中断后未自动恢复：诊断与修复方案

日期：2026-11-14。范围：全局四源投影、中断恢复、缺口完整性。状态：**完成生产只读诊断，尚未执行启动、代码修复或部署**。磁盘清理与容量治理由另一会话负责，本方案不重复清理。

## 1. 结论

当前不是 Projector 仍在缓慢追赶，而是 **Projector 已退出，没有消费者运行**。释放磁盘空间不会自动修复这次容器启动失败，也不会自动补齐已经记为 gap 的事件。

故障有三层：

1. 存储写入失败后，应用没有保留进程等待恢复；`SQLITE_FULL` 逃出主循环，进程退出。
2. Docker 自动重启尝试在挂载准备阶段再次遇到 ENOSPC，容器留在 `exited`。虽然配置 `unless-stopped`，清出空间后本轮多次观测仍未恢复；不能把 restart policy 当作端到端恢复保证。
3. 事件消费把 `OSError`、`sqlite3.OperationalError` 与映射数据错误一起转成 gap，并可能推进 checkpoint。此次新增 4 条 `OperationalError` gap，其中一条是 Case 创建事件。即使主队列重新追平，仍需恢复这些事件及其依赖。

这是中断恢复及完整性处理缺陷。现有证据不支持用增加并发、重建数据库或切换复杂架构解决它，也不能据此宣称此前全部吞吐指标已经达标。

## 2. 生产证据

以下时间均为北京时间，原始 JSON 使用 UTC。采样跨越 11 月 13 日至 14 日。

| 时间 | 观察 |
|---|---|
| 11-13 23:09:32 | Projector 在处理 `turn Case must be indexed first` 后写入 gap，COMMIT 抛出 `database or disk is full`，退出 |
| 23:09:33–23:09:37 | 再启动后退出；Docker State.Error 为 overlayfs 挂载目录创建失败：`no space left on device` |
| 23:58:53、23:59:32 | 根盘已约 9.82 GiB 可用，Projector 仍 exited，四源 checkpoint 不变 |
| 11-14 00:02:02 | 仍 exited；可用 10,501,840,896 bytes，约 9.78 GiB；checkpoint 继续不变，部分源 head 增长 |

最终采样：

| 源 | Read checkpoint | Source head | 坐标距离 | 首个待消费事件 UTC |
|---|---:|---:|---:|---|
| runtime | 4,624,593 | 4,625,046 | 453 | 15:09:31.623 |
| bus | 11,406,842 | 11,406,844 | 2 | 15:09:33.457 |
| initialization | 158,740 | 158,754 | 14 | 15:43:09.378 |
| research | 61,340 | 61,442 | 102 | 15:26:30.621 |

00:02 时 Runtime 最老待消费事件已约 52 分 31 秒。23:58:53 至 00:02:02，Runtime 坐标距离从 444 增至 453，消费进度为零。距离是序号差，不等于实际事件数；本次没有运行消费者，不能拿它估算消费者吞吐能力。部分上游也退出，低产出和低资源占用不代表系统健康。

Read generation 为 `75a54e2449a84065a0aee86dac09e353`，路径为 `/data/read/serving-v4-20261018/v2.sqlite3`。四源 Read epoch 与源 epoch 一致，checkpoint 均高于已过期 floor：

| 源 | expired floor | Source consumer ACK |
|---|---:|---:|
| runtime | 3,619,202 | 4,624,590 |
| bus | 9,533,500 | 11,406,842 |
| initialization | 153,756 | 158,740 |
| research | 22,786 | 61,340 |

这支持保留原 generation 和 checkpoint 续跑。Runtime ACK 落后 Read checkpoint 3 个坐标，不应人工把 ACK 推到 head；由消费者根据已提交状态恢复。未做全库完整性扫描，不能据此声称所有历史内容无损。

### 2.1 本次局部缺口链

当前 runtime gap：`ValueError=17,138`、`PROVENANCE_UNVERIFIED=66`、`OperationalError=4`，合计 17,208。前两项是此前已知历史问题，本次不可假报清零。

新增 OperationalError 坐标：`4624585 / 4624587 / 4624591 / 4624593`。其中：

- `4624587` 是 `runtime_v2_cases INSERT`；源端 Case 存在，Read 原生 Case 不存在，该坐标有 OperationalError gap。
- `4624595` 是该 Case 的 `runtime_v2_turns INSERT`，位于 checkpoint 后。现有 mapper 要先从 Read 查到父 Case 才能投影 turn，因此恢复消费后仍会遇到缺依赖，除非先修复父事件。
- `4624585/4624591` 对应同一 Case 的 turn，父 Case 事件 `4624578` 无 gap、Read 父对象存在；`4624593` 的父 Case 事件 `4624582` 同样存在。无需把所有 Case 重建一遍。

gap 只保存了泛化的 `OperationalError`，不能逐条证明原始 SQLite 扩展错误码；它们处于磁盘满故障边界，与上述 Case 缺失共同构成本次必须核验的恢复清单。

## 3. 代码与恢复机制审查

从**已停止的生产 Projector 容器**用 `docker cp ... -` 读取源码，而非只依据本地 checkout。关键路径已核对：

- `src/doxagent/v2_read/cli.py:306`：外层只将 BUSY/LOCKED 留在循环，FULL 会重新抛出并退出；OSError ENOSPC 也未覆盖。
- `projector.py:222`：映射及 ingest 的 OperationalError/OSError 落入写 gap 分支。基础设施故障有机会变成可跳过的业务事件错误；如果 gap 写入也失败，则退出。
- `repository.py:177`：事务 commit 失败会 rollback。应延续此事务边界，失败事件不推进提交坐标。
- `projector.py:140`：源 ACK 路径也仅特殊处理锁竞争，需要纳入基础设施异常传播规则。
- `projector.py:257` 起：repair 每次选择少量到期 gap，按 attempts、source、事件倒序尝试；失败退避可达 6 小时。新 gap attempts=0 会先于历史高 attempts gap，但子事件可能先于父事件尝试，失败后等待下一轮。因此既不能断言历史 gap 必然堵住新 gap，也不能保证父子缺口立刻收敛。
- repair 将尝试次数与下次时间先提交，然后才映射。存储异常不应被当作一次业务修复失败而长期退避。

生产存在 `doxagent-release-resources.timer`，调用 storage release 的 `reconcile-resources`；其用途是发布资源回收。另有 resource-safety controller。当前没有观测到 Projector 停止后的有效恢复；不能将这些组件视作已经具备投影期望状态恢复能力。

## 4. 修复执行顺序

### P0：一次性恢复本次中断

实施时先与空间治理会话核对当前容量与正在进行的维护，避免两边同时启动、替换或清理组件。

1. 保存新的容器 inspect、generation、四源 head/checkpoint/ACK/epoch/floor、gap 清单和源端保留状态；确认没有另一个 Projector writer。恢复前最低可用空间采用 `max(2 GiB, 最近实测10分钟写入预算)`，并确认空间不在快速下降；此门槛只是允许恢复，长期容量标准由空间治理方案负责。
2. **仅启动现有 Projector 容器**，保持原镜像、挂载、generation、checkpoint 和单 writer：`sudo docker start doxagent-v2-v2-projector-1`。不重启 Docker daemon，不清卷、不重置坐标、不全量重放、不启动业务执行器。若另一会话已启动，则跳过此步骤，只观测。
3. 每 10 秒采样，最多先观察 5 分钟：容器状态、四源 checkpoint/head、最老 pending、gap 新增、ACK、CPU/RSS/写入量。启动后 60 秒内必须出现待处理源的有效进度。若仍退出，读取新的首个失败栈，按挂载、权限、锁、存储、数据损坏分别处理；不盲目循环重启。
4. 恢复本次 4 条 gap：先 `4624587` 父 Case，再其余 turn，并检查 `4624595` 是否在恢复中形成新 gap；若形成则随后修复。优先复用现有 repair/ingest API，在现有单 writer 路径增加一次性明确坐标选择；不要在运行中的 writer 外另开修复写进程。若现有自动 repair 已成功，则只核验结果。
5. 回放必须使用源正式 receipt/保留 exception packet，沿用真实 provenance、幂等键、贡献和 `resolved_gap` 事务；不得直接删除 gap 或伪造空投影成功。修复旧坐标不得回退高水位，也不得覆盖已有更新版本。先验证现有 ingest 的单调性，再执行定向修复。
6. 验证对应 Case、turn、状态及前端列表/详情完整性，确认 gap 实际解除、ACK 最终收敛。历史 17,204 条另列未解决，不把本次恢复等同于全历史修复。

如果源 epoch 已变化、待消费事件或 exception packet 已被回收，停止直接续跑假设，按缺失范围做可审计补投影。只有证据证明无法局部恢复才评估 snapshot；当前没有全库重建依据。

### P1：修正异常分类，确保空间恢复后能续跑

修改范围以 `cli.py`、`projector.py` 和少量共享错误分类函数为主，保持单消费者及现有事务设计。

1. 在正常消费、repair、源读取/coverage/ACK 等边界，先分类存储基础设施异常，再处理业务映射错误。`SQLITE_FULL` 和 `OSError(errno.ENOSPC)` 一律不写业务 gap、不推进失败事件坐标，rollback 后向主循环传递可恢复故障。
2. 主循环按 1/2/5/10/30 秒退避，最高 30 秒；恢复有效工作后归零。每次失败关闭本轮连接，下一轮重新打开，保留 writer 锁避免并行接管。日志限频，输出阶段、源、坐标、错误码和等待时间；不要靠向已满 Read 库写入健康状态来证明故障。
3. 仅保留业务数据错误为 gap；BUSY/LOCKED 继续按锁竞争处理，不再误记业务 gap。IOERR 按实际基础/扩展码区分可重试存储故障与持久故障，禁止无差别吞掉所有 OperationalError。CORRUPT、NOTADB、epoch mismatch、权限/路径配置错误明确报错，保留现场，不自动清库。
4. ACK 失败保留旧 ACK，下一轮基于 Read 已提交 checkpoint 重试；Read 成功、ACK 未成功不应重做全部 materialization。repair 中基础设施不可用不应增加业务失败的长退避；必要时把尝试状态更新移到业务结果确定后。
5. 对近期中断缺口保留小批有界 repair；父对象缺失时识别并恢复可证明的直接父事件，再重试子事件。先解决本次已知链，只有测试证明常规机制仍无法及时收敛时才增加通用父依赖处理。不要为此构建新的全局依赖调度系统。

### P2：补齐“容器未能启动”的恢复闭环

应用重试覆盖不了 overlayfs 挂载失败。增加轻量宿主机恢复检查，复用已有发布工具与状态记录，避免引入服务平台或 sidecar。

- 每 60 秒检查正式发布清单中**期望运行的 Projector**。使用独立廉价检查入口；不把资源清理任务整体提频到每分钟。
- 只在容器 exited、上次错误为已恢复的容量/启动准备类故障、空间满足恢复门槛且无维护锁时，尝试启动现有容器。互斥锁、5 分钟失败冷却、限频错误记录，禁止启动风暴。
- 明确维护停止标记；运维 stop 必须能表达“期望停止”。无可靠期望状态时先补该标记，不能从 `unless-stopped` 推断人工停止意图。检查不得擅自拉起 executor、历史 one-off 或其他业务角色。
- running 但无进度只报故障与证据，不直接 kill/restart；区分无新事件、慢事件、writer contention、存储退避和永久数据错误。
- 覆盖 API 仍 healthy 但 Projector 已停的情况：分别展示 consumer 状态、故障阶段、最后进度、当前 pending 和未解决 gap。磁盘恢复后的“API 可访问”不能替代投影恢复验收。

## 5. 测试、发布与回退

测试使用临时小库/可控故障注入，禁止再填满生产根盘。

| 场景 | 必须验证 |
|---|---|
| 源读取、映射依赖读取、ingest、COMMIT 出现 FULL/ENOSPC | 不产生存储型业务 gap；失败坐标不提交；空间恢复后原进程自动继续 |
| 写 gap 时 FULL；repair 期间 FULL | 原业务缺口仍可追踪，失败恢复不会转成长时间业务退避或丢事件 |
| Read 已提交、ACK 失败；kill 后再启动 | 不丢已提交数据，不重复累加 contribution，ACK 保守且最终收敛 |
| 父 Case gap、后续 turn 到达 | 父子最终完整；repair 不回退 checkpoint，不覆盖较新状态 |
| BUSY/LOCKED、CORRUPT、epoch mismatch | 瞬态与永久故障按分类处理，不自动清库或无限忙循环 |
| 容器启动准备失败后空间恢复 | 宿主机检查自动拉起期望运行容器；维护停止不被拉起，冷却锁有效 |

先跑相关 projector/repository/source lifecycle 测试，追加针对上述事务与恢复语义的回归。按现有窄范围发布流程只替换 Projector 必要代码及恢复检查；不做全组件重建，部署期间保留旧镜像/恢复检查版本。重要代码修改追加 `changelog`，记录异常语义及恢复策略。

回退只回退程序/检查版本，保留数据库、坐标、事件保留和 gap；禁止通过恢复旧 Read 文件来回退。若回退到遇 FULL 即退出的版本，必须明确自动恢复能力也随之撤回。

## 6. 量化验收与停止条件

以下是实施目标，不是本轮已经取得的成绩。

1. **恢复时间**：进程存活的 FULL 故障解除后 60 秒内恢复提交；容器启动失败场景在恢复门槛满足后 120 秒内启动并产生进度。永久错误不得假报自动恢复成功。
2. **积压消退**：记录启动时真实待消费条数 N 和实测输入速率 λ；以滚动窗口测 μ，按 `N/(μ-λ)` 估算消退。此次约数百坐标量级，目标 5 分钟内四源正常队列进入实时区间；未达标立即按已采集慢阶段定位，不无限延长观察掩盖问题。
3. **实时性**：上游恢复正常产出后连续 30 分钟，四源非空样本端到端投影延迟 P95≤10秒、P99≤30秒，最老正常 pending≤60秒。未产出源标记无样本；若重型激活导致超标，如实报告并追加最小瓶颈修复，不能沿用此前未达标数据宣称通过。
4. **吞吐余量**：有真实积压时滚动 5 分钟 μ≥1.5λ，队列持续净下降。无积压时 μ≈λ 只说明跟随负载，不证明容量；额外容量验证用代表性隔离回放，不向生产制造业务事件。
5. **完整性**：本次四个 OperationalError gap 及其派生依赖缺口全部完成内容核验并解除；新增存储故障型 gap=0；checkpoint/ACK 单调且最终收敛；generation 不变。历史 17,204 条独立披露。
6. **资源**：保持单 Projector；同类负载下 Projector CPU、RSS、磁盘写入相对恢复前可比正常窗口增加不超过 10%；恢复检查平均 CPU<0.01核、单次 RSS<64MiB；存储等待不忙轮询。宿主 MemAvailable≥2GiB、无新增 OOM，CPU/IO PSI full 的 5分钟均值<5%。当前停止态不能用作性能基线；缺少可比窗口则资源验收标记未完成。
7. **持续性**：完成隔离故障恢复测试和生产 30 分钟混合负载验收后交付；保留后续正常运维观测指标，不以要求无限观察拖延结项。容量长期不再耗尽由空间治理方案另行验收。

## 7. 证据文件与本轮操作边界

目录：`eval/store_unavailable_diskfull_audit_20261113/`。

- `inventory.json`：初始磁盘与容器状态。
- `projection-recovery-1.json`、`projection-recovery-2.json`、`projection-recovery-final.json`：释放空间后状态与四源位置。
- `replay-boundary.json`：generation、epoch、floor、ACK、gap 与边界源对象核验。
- `dependency.json`：本次父 Case/turn 事件链及宿主 systemd 单元。
- `deployed-recovery.json`：生产容器源码关键段、SHA256 与 timers。

本轮仅进行了只读 SSH、只读数据库查询、容器源码读取与本地证据/本文档写入；没有重启 Projector、修改生产库、删除 gap、调整调度或实施代码修复。
