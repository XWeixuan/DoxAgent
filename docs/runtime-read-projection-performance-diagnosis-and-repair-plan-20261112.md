# 全局 Runtime → Read 投影性能故障：生产排查与修复执行方案

日期：2026-11-12；时间除明确标为 UTC 外均为北京时间（UTC+8）。

**状态：仅诊断与方案，未实施修复。** 本文覆盖全局事件投影，不按交易、ticker、Case 或业务结果设置优先级。实际执行本方案、发布代码及存量修复属于下一阶段。

## 1. 核心判断与证据边界

这是共享投影链路在实际业务负载下的服务能力与无效开销问题，不能用某个 Case 的业务优先级、浏览器刷新或强推 checkpoint 解决。本轮确认了三类可直接着手消除的低效：

1. **缺口恢复路径重复建立源数据库连接，并持续重试大量长期失败的历史缺口。** 同一批 60 个真实缺口，暖态逐事件连接读取约 586 ms；使用已经存在的 `event_reader(serving=True)` 约 14–15 ms，返回事件内容哈希完全一致。两次在线进程抽样中，repair 占约 30% / 39% 的总墙钟样本，扣除主循环 sleep 后约为 52% / 57%。这不是推测出来的“也许需要连接池”，而是实际调用链和同输入对照复现的瓶颈。
2. **Serving 先展开完整冻结上下文，之后才裁剪投影字段。** 24 个 Case 回执共重新读取约 41.89 MB 不变 Native 内容，只有 15 个不同 digest；单条回执常仅数 KB，`.frozen_inputs` 却包含约 1.4–2.2 MB 原始 JSON，展开后的 Python JSON 文本更大。24 条平均展开耗时 47.3 ms；另一组 15 条 Case 的源读取均值 76.8 ms，映射均值 31.6 ms。被展开内容含 Prompt、模型配置、大段 projection、round inputs 等，而 Read 最终只保留很小子集。
3. **每轮固定管理工作及无变化事件仍大量进入写事务。** 四源依次执行多次 capture/head/coordinates/健康写入；每轮重复读取、排序全部 Runtime gap 集合并同步 ACK。effect 已被标为 `serving_skipped`，许多 runtime_values 也已跳过业务映射，但仍走逐事件 `ingest([], position=...)`，领取 Read 写锁并提交 checkpoint。存在明确的事务及读取放大，不需要新增服务才能消除。

**不能把这三点说成已经精确分摊了此前每一分钟的全部积压。** 本轮取得的是历史真实消费记录、当前只读阶段探测及追平后的在线栈样本；没有在旧高峰进行原位逐阶段计时，也没有修改正在运行的 Python 进程。高峰中 ingest 内部 SQL、锁等待、内容注册、COMMIT/WAL checkpoint 各占多少，仍需第 6 节的受控分阶段验证闭合。不能将缺少这部分证据包装成“某条 SQL 是唯一根因”，也不能因此退回泛泛的加并发建议。

另有必须分开的事实：**本轮 05:43:17 的 Runtime checkpoint 与 head 已同时为 4,502,071，队列暂时追平；17,204 条历史 gap 没有随之消失。** 没有修复或重启促成此次追平。现有系统能在负载下降后追赶，不代表已具备高峰实时能力，更不代表所有数据完整。

## 2. 范围、现网身份与本轮操作

参考前诊断：[RKLB 诊断](rklb-live-hedge-exit-frontend-diagnosis-20261112.md)。该文用于确认历史故障时间线，不作为本轮实时状态。

本轮检查的生产宿主为 `doxagent-sg`，8 vCPU、约 31 GiB 内存。实际角色与路径如下：

| 项目 | 本轮核验 |
| --- | --- |
| Projector 镜像 | `sha256:5f3c2fee1bc60e5664…`，完整 ID 见 `inventory.json` |
| Projector 命令 | `python -m doxagent.v2_read.cli project … --limit 500` |
| Read 库 | `/data/read/serving-v4-20261018/v2.sqlite3` |
| Runtime 库 | `/data/runtime/runtime.sqlite3` |
| 源轮询次序 | research → initialization → bus → runtime |
| Read GC | 单独 `v2-maintenance` 角色，读取 active alias |
| Transport GC | 单独 `v2-transport-maintenance`，管理四源 |

从正在执行的 Projector 容器导出 13 份所属源码并保存 SHA-256；与本地对应源码按规范化换行比较，13/13 文本一致。本地 `main` 开始时落后 `origin/main` 6 个提交；没有拉取、切分支、覆盖其他任务改动，也没有用 API 容器内未执行的代码代替 Projector 身份核验。

本轮只读数据库连接均使用 `mode=ro`；重查询设置 2–3 秒取消预算，统计范围按源坐标限定。未运行在线全库 integrity_check、VACUUM、全库备份、基准写入、投影重建、CDC migration、gap repair、checkpoint 修改或维护暂停。

额外诊断包括两次 30 秒、49 Hz、`--nonblocking` 的外部 Python 栈采样，以及 8 秒系统调用统计。只在 `/tmp/doxagent-projector-audit-20261112/` 临时放置诊断二进制，取证后核验哈希并删除；其 PyPI wheel 来源与校验、清理收据保存在证据目录。映射实验通过只读 Store、禁止文件发布、将 `put_content` 替换为内存计算引用实现，**没有调用 ingest，也没有登记内容或执行源业务**。这组时间低估了真实映射路径的内容注册开销，不能当作完整生产吞吐。

## 3. 真实负载、消费与积压基线

### 3.1 指标定义

- `H_s(t)`：源 head；`C_s(t)`：Read 对该源的 checkpoint。
- `B_coord=H-C` 是坐标距离。只有确认区间内坐标连续、没有退役跳读后，才能把它当事件数；不能将 Bus 的头部跨越直接当作逐事件成功处理量。
- `B_events`：尚未消费的 hot/archive/pack 回执坐标集合计数，不能把三层重复坐标重复计数。
- `λ=ΔH/Δt`：新增坐标速率；`μ=ΔC/Δt`：消费坐标速率；`dB/dt=λ-μ`。同时记录实际 mapped / skipped / gap / successful mutation 数。
- `L_queue`：当前最老未消费回执距服务器当前时间的年龄；队列空时为 0，而不是取最后一条旧业务消息的年龄。
- `L_projection=projected_at-recorded_at`：已投影事件的数据年龄。`commits.at` 沿用源时间，不能代替写入时钟。现有 `commit_retention.projected_at` 在事务内插入，距真正 COMMIT 返回有极短差别；后续精确 SLO 使用提交返回的单调钟及墙钟。
- `G`：未解决 gap 数及其来源、原因、业务影响；与 `B_events` 分开。`C=H`、`source_health.error=NULL`、HTTP 200 均不能替代 `G` 的验收。

### 3.2 事件产生负载

以 hot outbox 最后 100,000 个坐标为有界入口，取完整的 **11 月 11 日 08:00 至 11 月 12 日 05:00**（UTC 00:00–21:00）分钟桶；空分钟补 0，最后未完成分钟不计。这个 21 小时窗口完整落在两源当前 hot 范围内；不是全历史或完整多交易日容量结论。

| 源 | 事件数 | 均值 /s | 1 分钟速率 P95 / P99 | 1 分钟峰值 /s | 固定 5 分钟桶峰值 /s |
| --- | ---: | ---: | ---: | ---: | ---: |
| Runtime | 59,138 | 0.782 | 2.383 / 3.917 | 6.633（398/min） | 3.437 |
| Bus | 80,770 | 1.068 | 3.233 / 5.967 | 13.267（796/min） | 4.620 |

两源峰值并非同一分钟，不能机械相加称为观测到的并发峰值。research / initialization 平时低频，但正式刷新、activation 等大回执必须进入后续混合回放，不能因均值低而忽略它们的阻塞风险。

Runtime 最近 12,000 条事件中，effect 2,803 条（23.36%），runtime_values 4,264 条（35.53%），Case 1,301 条，Turn 984 条，Task 816 条，W3 Case 600 条。runtime_values 内 `round_inputs`、`worker_receipts`、`worker_requests` 等虽在源 CDC 中保留，Serving 已不直接消费；`worker_invocations` 仍承载 usage/attempt，不能一概删除。`reference_deliveries` 仅 17 条，但单条包约 2.10 MB，应单列长尾。

结论是**每秒个位数的 Runtime 负载就出现过数小时延迟**。这首先要求减少单位事件服务时间与循环固定成本，不构成必须加线程、换消息中间件或扩容的证据。

### 3.3 历史消费与现时变化

从 `commit_retention(projected_at,seq)` 索引读取每分钟实际落地记录，取纯数字 `source_event` 的最大值重建源消费锚点。相邻完整半小时桶末端锚点差 / 1,800 给出近似坐标消费速率；未变化事件不一定生成 commits，因此这是消费锚点估计，不是逐事件计时。最后不完整半小时不纳入容量比较。

| 北京时间半小时区间 | Runtime 估计消费 /s | 该桶实际提交事件最大年龄 |
| --- | ---: | ---: |
| 00:30–01:00 | 0.612 | 5.43 h |
| 01:00–01:30 | 1.063 | 5.54 h |
| 01:30–02:00 | 1.009 | 5.61 h |
| 02:00–02:30 | 1.426 | 5.78 h |
| 02:30–03:00 | 0.661 | 5.88 h |
| 03:00–03:30 | 1.178 | 5.88 h |
| 03:30–04:00 | 1.020 | 5.90 h |
| 04:00–04:30 | 1.222 | 5.91 h |
| 04:30–05:00 | 2.316 | 5.83 h |
| 05:00–05:30 | 3.205 | 5.20 h |

不能把“旧事件提交时年龄最大值”误写成桶结束时的 `L_queue`。不过它清楚表明：大量旧数据在这些时间才写入 Read，后段追赶速度显著提高。Bus 同期多数桶最大年龄为几十秒，个别约 284–332 秒；共享进程内不同源成本与循环时隙不同，不能推断成 Runtime 源停止或全源都恒定落后六小时。

| 独立快照（北京时间） | Runtime checkpoint | head | 坐标距离 | 解读 |
| --- | ---: | ---: | ---: | --- |
| 05:22:14，前诊断 | 4,491,306 | 4,500,895 | 9,589 | 下一事件约落后 4 h 45 min；前诊断证实该范围有相同数量 hot 头 |
| 05:37:31，本轮 | 4,500,170 | 4,501,724 | 1,554 | 对应 hot 范围计数为 1,554，已经在快速消退 |
| 05:43:17，本轮 | 4,502,071 | 4,502,071 | 0 | 暂时追平；gap 仍为 17,204 |

05:22→05:37 的跨快照估计：新增约 0.90/s、消费约 9.66/s、净消退约 8.76/s。它是取证时钟略有先后差异的短窗口估计，也包含负载变化，不能据此保证高峰下持续 9.66/s。

连续 15 分钟采样结果和末次状态见第 9 节；原始样本保留，避免用一次瞬间追平代替稳定性结论。

### 3.4 延迟和吞吐目标

这是事件驱动的 UI 读模型，现有负载不应要求用户等待分钟至小时。本方案将以下目标作为修复验收合同，而非声称当前已达标：

| 维度 | 验收要求 |
| --- | --- |
| 自然事件服务器可读延迟 | 各源分别统计：P95 ≤10 s、P99 ≤30 s；持续业务中最老待投影事件 ≤60 s；单次可解释刷新突发允许 ≤120 s，并在 5 min 内恢复 |
| Runtime 容量 | 生产等价混合回放中，存在积压时持续 ≥12 个源坐标/s，连续 30 min；另外报告实际 mapped/skipped/gap 量，禁止靠新增跳读比例达标 |
| 全局容量 | 四源混合真实回执、同一台规格机器、单 Projector：总坐标吞吐 ≥35/s，其中 Runtime ≥12/s、Bus ≥20/s；research / initialization 有实际服务且不造成其他源长尾失控 |
| 9,589 条积压 | 新增负载按 Runtime 实测分钟峰值 6.633/s 继续注入，≤30 min 退至正常延迟；净消退 ≥5.33/s。`9589/(12-6.633)≈1787s`，约 29.8 min |
| 更大积压 | 用 `T=B/(μ-λ)` 预报；若有 20,000 条、同样最坏持续输入，≤65 min。每 5 min 校验预测和实际，不能只报告 checkpoint 在动 |
| gap | 新正常业务投影缺口新增为 0；存量必须逐类完成核验，所有有效业务 Case / 状态 / 结果的缺失为 0。明确不属 Serving 的原始事实保留可审计结论，不以删 gap、清空表或忽略来源通过验收 |
| 稳定性 | 覆盖至少一个完整美股主要业务时段；24 h 初验、72 h 确认，另做 7 日数据库/空间增长复核。短窗口和回放均不能替代自然高峰 |

12/s、35/s 是有负载依据的目标，不是未测试的性能承诺。总源坐标量不是页面对象量；真实案例、字段、指标桶、错误语义必须在回放前后等价。若实测 SLO 与容量不能同时成立，继续消除热点，不能降低目标成“数小时延迟可接受”。

## 4. 分阶段证据与根因排序

### 4.1 事件读取：连接重复初始化是已复现瓶颈

正常 `_events` 已经使用 `source.event_reader(serving=True)` 复用一个源连接。**repair 却逐条调用 `source.event(seq, serving=True)`，每条另开、另关连接。** SQLite 每个新连接的 schema 初始化及读取开销反复支付；源库中大量表/触发器使这种路径尤其昂贵。

对真实最新 60 个 ValueError gap，按 `per_event → reused → reused → per_event` 顺序读取，避免只比较第一轮冷态：

| 方式 | 一轮总墙钟 | 纯 event 读取合计 | 单条均值 |
| --- | ---: | ---: | ---: |
| 逐条连接，首轮 | 1,611.9 ms | 1,608.3 ms | 26.806 ms |
| 既有 event_reader，第二轮 | 13.7 ms | 10.2 ms | 0.169 ms |
| 既有 event_reader，第三轮 | 14.9 ms | 11.6 ms | 0.193 ms |
| 逐条连接，暖态复测 | 586.3 ms | 582.5 ms | 9.709 ms |

60 个事件四轮内容哈希一致。暖态读阶段约 50 倍差距，仅证明这一段可以明显减负，不把它外推成全链路 50 倍提升。首轮单条约 932 ms 的离群值也不能拿来宣称所有读取都慢。

在线 profile-1 / profile-2 总墙钟样本分别 1,456 / 1,463：

| 互斥阶段 | 第一次 | 第二次 |
| --- | ---: | ---: |
| repair | 30.0% | 38.8% |
| 源准备、健康、ACK、coverage 等 | 25.5% | 24.5% |
| ingest | 2.1% | 3.1% |
| 正常事件映射 | 0.1% | 0.7% |
| 正常事件加载等 | 0.5% | 0.9% |
| 主循环空闲 sleep | 41.8% | 32.1% |

两次均在队列已接近或达到追平的低新增负载时段，不能冒充旧高峰比例。包含 repair 的调用栈中 `outbox.py:488` 的源头定位、关闭连接，以及 `BEGIN IMMEDIATE` 行占明显样本，支持实际事务/初始化成本，而非仅由代码静态猜想。

### 4.2 gap 重试：失败反馈耗费正常投影资源

05:40 的有界 census 实际检查了全部 17,204 个 gap（0.45 s）：

- ValueError 17,138 个，PROVENANCE_UNVERIFIED 66 个。
- 6,382 个 ValueError 仍能匹配 hot `te_events`，另有少量 jobs/attempts/fills/allocations/lots。
- 10,695 个 gap 不在 hot outbox，**不表示源证据丢失**；需检查 exception receipt / archive / pack 层。
- 抽取最新 8 个 ValueError 用真实 mapper 重现，均为 `execution dependency not indexed`。不能据此断言其他 17,196 个都相同或全可忽略。
- 大批历史项 attempts 约 1,762–1,763；部分 provenance 达 9,604。持续重试并未带来恢复。

代码有三处直接浪费：

1. `_tick` 末尾 `repair(limit<=20)`，CLI 又每 5 秒 `repair(limit=100)`；同一修复队列有两处调用，预算各自计算。
2. `repair` 先更新整页 attempts/next_attempt_at，再逐项检查 1 秒预算；没真正尝试的项也会被计为尝试，影响调度公平和诊断可信度。
3. 查询 due gap、按 attempts 排序，随后 `_tick` 又读取全部 gaps、构造并排序 repairs/ACK 签名。长期不变的 17k 集合在每轮重复处理。

故障不局限于历史交易：共享 Projector 的 CPU、源读取与 Read 写锁被这些重复任务占用，影响 Case 创建、状态、结果及所有派生对象。修复目标是消除无效工作，不是赋予交易事件更高优先级。

### 4.3 Native 展开和映射：边界裁剪过晚

`SourceOutbox.event()` → `NativeContent.source_row()` → `text()` 会先展开标记、生成完整 JSON 字符串；`projector.native()` 再解析该字符串，Domain mapper 根据完整业务对象生成记录；`ReadStore.ingest()` 最后才调用 `serving_fields.select()` 裁剪 native 字段。因而瘦身后的 Read 行并没有消除源读取阶段的整份上下文恢复成本。

24 个真实 Case 输入的 `.frozen_inputs` 标记读取合计 41,888,246 bytes，24 次文件加载、15 个不同内容 digest。样例中 frozen projection 约 2.43 MB、round_inputs 约 430 KB、provisional 约 253 KB；这些尺寸是 `json.dumps` 计量值，不能与磁盘压缩后块大小相加当物理 I/O。

108 个真实事件的 mapper 只读实验中，15 个 Case、62 个 runtime_values、4 个 Turn 等成功；8 个历史依赖缺失重现。cProfile 映射累计约 0.738 s，其中 schema validation 约 0.321 s、JSON decode 约 0.204 s，存在调用包含关系，**不可相加宣称独立占比**。数据库 execute 自身约 0.056 s；这组结果不支持先盲目新增数据库索引。

精简不能直接丢掉全部 frozen_inputs：`case_outcomes.decision_direction()` 对 `POLICY_TRADE` 需要 pinned `projection.policies[].activation_conditions[].decision`；另需 `w3_execution_state`、follow-up 字段等。缺少这些字段可能错误投影交易方向、W3 状态和完成时钟。必须以实际消费者字段清单和输出等价为约束，在 Serving 边界做薄解析，而不是改业务冻结输入。

### 4.4 写入、事务、批处理与索引

现有 `--limit 500` 是**最多提取 500 个坐标**，不是一次事务提交 500 事件。满页事件工作预算 5 秒，非满页 1 秒；预算在每个事件完成后检查，单个慢事件可以越过整个轮次预算。四源串行，共享一条正常消费路径。

`ReadStore.ingest()` 的优点需要保留：实体/贡献水位、变更检测、单事件原子提交、旧版本前像、幂等 commit key。当前问题是这些保护之前和周围仍有重复成本：

- skipped / 无业务变化事件仍开始写事务和更新 checkpoint。
- 一个 Case 的多份 reasoning 各调一次 `put_content`，后者独立写 contents / content_locations / artifact_ref；随后 ingest 再开事务写对象与贡献。
- 部分内容编码、不可变文件发布可发生在写锁内；需要计时区分，不能仅看 SQL 数。
- Case、attempt、usage 等不同到达事件会多次重算 Case trade / graph / decision metrics，最终内容不变仍先支付读取、JSON、校验成本。
- 对象当前态/历史态、指标、artifact/definition 引用触发器和多个索引叠加放大一次业务变化的页写入。不能为加速随意去掉 owner / view / 水位保护。

Read 已有 `current_native_identity(kind,id,ticker)`、`current_kind_revision(kind,valid_from)`、`contribution_metric_revision(metric,valid_from)` 等关键索引。`sync_business_facts` 的 `previous=0` 冷启动全量统计触发 2 秒预算，但这不证明增量范围同样慢，也不是缺索引的充分证据。`object_current GROUP BY kind` 触发 3 秒预算后已中止，没有继续全表扫。

8 秒 strace 记录 28,334 次 pread、13,121 次 pwrite、41 次 fdatasync；同步调用总系统调用时间约 8.1 ms。它显示大量页读写，却不支持把故障直接归为“每次 fsync 很慢”。strace 有扰动，只作为路径旁证，不用于计算未经扰动的容量。

### 4.5 维护、调度和服务器资源

05:35 快照：根盘约 374 GiB、可用 26 GiB、94%；内存 available 约 9 GiB，2 GiB swap 已占满。**swap 占满不等于正在猛烈换页**；须结合连续 pswpin/pswpout。负载均值约 19.5，CPU pressure some 的 60 秒均值约 56%，IO pressure some 约 5.3%。CPU 调度竞争与 I/O 干扰是实测放大因素，不能简单当作磁盘硬件已经饱和。

短 iostat 窗口内 vda 读 await 约 0.3 ms、写 await 约 1–2 ms，利用率有明显波动；不能从一次 Docker 累计 BlockIO 数直接推出当前带宽瓶颈。GC 日志反复出现 locked / interrupted，同时有真实提交进度；它既不是完全停止，也不是已证明没有争用。

Read 主文件约 34.75 GB，freelist 约 11.66 GB（内部可重用页，不等于已返还操作系统空间）。现有 26 GiB 外部余量不足以做当前实现所要求的三倍文件空间 shadow compact。**在线 VACUUM 或本机全库复制不是本方案的第一步。** 先减少写放大和无效生成，保持必要回收进度；本轮没有清理数据或关闭维护。

## 5. 全局最优的修复顺序

按下列顺序交付，每一步保留前后对照；不同时上线所有改动后再猜是哪一项有效。目标是在现有机器、现有 SQLite、单 Projector 下完成。

### R0：最小计时与可复现实验基线

**文件：** `v2_read/projector.py`、`cli.py`、`outbox.py`、`repository.py`；只增加计时/计数与有界输出，不改调度行为。

1. 在一轮外层使用 `perf_counter_ns`，统计每源 setup、coordinates、load/decode、map、ingest、repair、ACK/facts/coverage 及整轮墙钟。
2. ingest 再拆为预处理/字段选择、等待 `BEGIN IMMEDIATE`、内容注册/编码、对象写、贡献/指标写、checkpoint/receipt、COMMIT 返回；normalization、codec、row factory 计在真实所属段。记录 CPU time，区分 CPU 计算和墙钟等待。
3. 计数含 input coords、mapped、serving_skipped、无变化、实际 object/contribution mutations、new/resolved gap、实际 repair attempts/success；记录解码 Native bytes、重复 digest 数、每事件写事务数。
4. 默认每 60 秒一条聚合 JSON；只对 >100 ms 事件最多保留每分钟 5 个慢例，含 source/table/namespace/seq/阶段时间和错误码，不写正文/账户/冻结输入。可设置 30 分钟自动关闭，额外 CPU <2%、日志 <5 MiB/h。
5. 验证先用隔离 fixture，再跟随自然生产。若当时无积压，不用 `μ≈λ` 推断容量；使用 R1/R2 的脱敏真实回放作为容量门槛。

**完成条件：** 30 分钟有界计时可复核；各阶段互斥总和与整轮误差 ≤5%；正常、skip、gap、异常和 COMMIT 失败均计数一致。若实际证实 write / WAL 占主导，先处理对应项，不固守本轮排序。

### R1：精简重试和源读取，保持一个恢复入口

**文件：** `projector.py:repair/_tick`、`cli.py`、`outbox.py:event_reader`。

1. repair 按 source 分组，每源使用已有 `event_reader(serving=True)`；不共享线程、不跨轮保留未结束读事务、不新增独立连接池。正常源 capture/head/coordinate 可复用轮内 reader/schema，首轮确认 epoch，后续按既有 epoch 安全约束核验。
2. 合并 `_tick` 与 CLI 的两处 repair 调用为一个调度入口。不是降低业务优先级；所有正常事件继续同等有界轮转。修复工作用一个全局 token / 墙钟预算，建议初值每 5 秒最多 20 条、累计 ≤250 ms；具体以有效业务 gap 恢复速度和正常事件 SLO 联合调节。
3. **真正开始尝试时**才增加 attempts、更新 next_attempt_at；超过预算未尝试的项保留原值。使用原有 gap 主键做条件更新，禁止预先惩罚整页。
4. 将确定性依赖缺失与瞬时 SQLite contention 分开，保留精确原因码。相同依赖长期未变且连续失败的项进入长退避（先 30 min，再最多 6 h），仍保留 gap / receipt / exception pin。依赖修复或显式存量恢复时走同一入口提前重试。不能把失败改成成功或把当前有效 Case 放到无限重试末端。
5. 缓存未变化的 gap 数字坐标/ACK 签名：repair 新增/解决或 gap 集合变化时刷新；跨进程不能靠无效本地假设，可用廉价版本/变化标记或有界周期核验。避免每轮把完整 17k 集合读出、排序和写回。
6. 若 due 查询在 R0 中仍占显著时间，先优化选择顺序与查询形状，再通过 EXPLAIN 决定是否需要一个小索引；不默认给 Read 大表加索引。

**验证：** 本轮 60 个真实事件结果逐项一致；修复批内源连接次数 O(源数)，不是 O(事件数)；超时未尝试项计数不变；不存在 fresh 有效 gap 被历史无效重试长期饿死；瞬时 BUSY 不制造永久业务错误；Runtime gap/pin 不减少至未核验数量。

**完成门槛：** 同输入 read-only 对照 ≥10 倍改善；低新增负载时 repair ≤5% 总墙钟，且已确认有效业务 gap 的有界恢复进度可见。先去除重复读取，不能只调大 budget。

### R2：在 Serving 边界一次解析，早裁剪冻结上下文

**文件：** `outbox.py:event`、`native_content.py`、`projector.py:native`、`projectors.py`、`serving_fields.py`；必要时仅对既有捕获 envelope 加兼容薄字段。

1. 梳理 Runtime Case、Turn、worker_invocations、reference_delivery 的真实 Serving 字段。对每个字段列出生产者、mapper、API/导出使用者及旧记录 fallback，尤其保留 POLICY_TRADE 的 pinned direction、W3 phase/状态、结果、首次/完成/生效时间、正文/推理内容引用。
2. Serving 路径一次 `json.loads`，在事件内传递已解析对象，让 Domain/Formal/Revenue 的 `native()` 共享结果。通用 `source.event(decode=True)` 的完整审计读取保持原契约；不把 dict 偷塞成旧 SQL TEXT 引发已发生过的 JSON subtype 回归。
3. 对 `.frozen_inputs` 内容 digest，在一次展开后提取小的 Serving 子集并保留一个**有明确字节上限的轮内薄结果缓存**，而非缓存完整数 MB Python 图。建议起点 ≤16 MiB，限制条数；所有读者按只读约定或小对象复制，避免跨事件互相修改。
4. 跳过无消费者字段的 Native 深层展开。若整个 frozen_inputs 是单一 marker，第一次仍可能要读全量；不能声称 JSON path 本身能跳过压缩块。可先用事件内一次解析和薄缓存减负，再根据实测决定是否增加现有回执中的小型 serving slice，由源正常写入同步生成，不复制冻结上下文、不新建数据通道。
5. 不做旧全库 rewrite。历史回执保留原 decoder fallback；当前快照重建、在线增量、repair 使用同一个 Serving 字段函数，避免三套解释。
6. 将 Case 的重复 schema 校验移至最终输出边界，保持同等约束与错误可见性；仅在 R0 确认占比显著时评估既有快速 validator，先做语义兼容，不靠取消校验提速。

**验证：** 全部实际可读输出及贡献集合按业务键规范化比较，相同；POLICY_TRADE / EXPERT_TRADE / NO_TRADE、W1/W2/W3、operations 中间态、归档、candidate、多源消息、历史 dict/string envelope 均覆盖。正文/推理引用内容哈希不变。不能只比较 Case.status。

**完成门槛：** 真实 Case decode+map P95 ≤30 ms；热路径不重复解压相同 frozen digest；Native 解码字节至少减少 70%（对重复 Case 更新工作集）；内存增长受字节上限约束。一次性大激活另行设长尾预算，不用它污染一般 Case 基准。

### R3：减小无效写事务和衍生重复计算

**文件：** `projector.py:_events`、`repository.py:ingest/put_content`、`projectors.py`、`runtime.py`、`decision_metrics.py`、`graph.py`。

1. 对**已有契约明确 `serving_skipped`** 的连续事件，在不跨越尚未处理的业务事件/错误事件的前提下，累计到后续成功事件 checkpoint 或轮末一次持久化；源回执保留、不改源 head、不制造假的 mutation commit。不基于 ticker、交易或 Case 重要性跳读。
2. 剩余无变化事件的水位/ACK 只在确有推进时写；不能跳过实体/贡献水位，从而让旧 repair 覆盖新值。
3. 将一个源事件的 content 引用计算、必要不可变文件预发布与内容登记分开：计算/编码尽量在 writer lock 前；`contents`、location、owner/ref 和对象更新在同一事件事务中登记，避免每份 reasoning 独立提交。遇到 reclaim guard 按原坐标重试，不在业务对象里提交悬空引用。
4. 对同一事件所涉及的同一 Case，一次批量取得小 attempt/execution/native 摘要，trade/graph/timing 共享这一份输入；只在依赖字段改变时重算相应派生。保持 incoming 覆盖旧态、来源水位、乱序 repair 与贡献替换语义。
5. 先保留“一个有业务变化事件一次原子提交”。仅当 R0/R1/R2/R3 前四项之后仍达不到容量，才做 ≤16 个事件或 ≤50 ms 的微批实验。

**微批不能直接在现有 `_events` 外套一个大事务。** 当前 mapper 用单独只读连接读取上一事件已提交状态；外套写事务后这些读者看不到批内前一事件，可能误报依赖缺失、计算错误指标。若确需微批，必须显式保证 read-your-writes、savepoint 隔离和 checkpoint 同事务，验证崩溃任意位置重放等价。若这需要大改，保留单事件提交，优先前述精简。

**完成门槛：** skip 事件不再每条领取写锁；标准 Case 每事件内容登记+ingest 总写事务以 1 为目标；写锁持有 P95 ≤20 ms、P99 ≤50 ms、普通事件单次 ≤100 ms。全局 ≥35/s 的混合容量应在此阶段达成；不能通过空映射或丢弃结果达标。

### R4：用既有预算约束 GC、轮转和大回执长尾

**文件：** `maintenance.py`、`source_lifecycle.py`、`cli.py`；沿用现有维护角色。

1. R0 显示锁竞争时，将 GC 的单次写锁限制在约 20–50 ms，保留阶段提交、持久游标、真实 deleted 进度；遇 BUSY 退出本轮，避免反复抢锁。累计维护预算先保持现有级别，再按资源验收缩短无效扫描。
2. 不暂停 GC 来换短期追平。Read/transport/content 保留必要回收能力；72 h 内保留窗口之外的可回收量不能单调增长。
3. 轮转从“每源 1/5 秒工作预算”评估为同等短量子（初值 100–250 ms、有界坐标数），下一轮仍循环四源；这是源间公平，不是业务优先级。先减少 setup，否则更短量子会放大管理成本。
4. 单条昂贵 activation/reference 等记录拆出可提前准备的纯计算/不可变解析，仍在既有事件原子边界发布。若预算超额先记录真实慢例，不为它另建高优先级队列。
5. 只有在 COMMIT/WAL 计时证明确为主导后，才对 checkpoint 频率做一次参数实验；保持数据库 durability 配置，不直接切 `synchronous=OFF/NORMAL` 换分数，不在生产调用 TRUNCATE checkpoint 作为探测。

## 6. 仍需闭合的分阶段验证与执行方法

### 6.1 真实回放材料与环境

下一阶段从现有 hot 回执按 keyset 导出一个**连续坐标、全源混合**工作集，至少含 30 min 高负载和 30 min 普通负载，并包含本轮发现的长尾 Case/reference/activation 与 known gap。必要依赖按主键导出小型当前记录及被引用的不可变内容，给清单、数量、哈希和截断边界；不做生产全库扫描或本机 35 GB shadow copy。

脱敏数据在独立本地环境先跑 baseline/candidate；需要资源结论时，在同规格隔离测试环境或生产 `/tmp` 的小型隔离库逐项计时，测试总数据预算 ≤512 MiB、额外 RSS ≤512 MiB、单进程、单 CPU、低 I/O 权重，超预算停止该次实验并保留证据。不能在生产源库或 Read 库做 rollback 写基准，也不能与业务同时施加一次饱和压力测试。

隔离小库只能验证代码路径、相对改善与正确性，无法复制 35 GB B-tree/页缓存行为。最终锁、WAL、GC 资源结论必须由原位有界计时及自然负载复核，明确区分两类结果。

### 6.2 每项实验的输出

| 阶段 | 必须输出 | 判别动作 |
| --- | --- | --- |
| source setup / SQL read | 连接数、schema/查询时间、坐标数；cold/hot 分开 | 若每项新连接明显占主导，使用已有批内 reader |
| Native load / transform | 按 digest 的 bytes/读次数、一次解析/重编码时间、输出等价 | 先减重复展开与无用字段，避免堆大缓存 |
| mapper | 每类/namespace P50/P95/P99、SQL 数、校验/派生耗时 | 热点依赖批取；禁止笼统关闭 graph/metrics |
| writer acquire | BEGIN 等待 vs 持锁时间、BUSY 次数、writer 身份 | 调整实际冲突者的短事务，而非增加 writer |
| objects / contributions | mutation 数与 SQL 数、dirty page/字节量、每事件放大比 | 缓解重复内容/派生/索引维护，保留必要历史 |
| COMMIT / WAL | COMMIT 返回时间、WAL 页/大小走势、checkpoint 归属 | 证实后再调既有参数；不能只读 WAL 文件大小下结论 |
| repair / ACK / coverage | 真正 attempts/success、due/未尝试量、签名变化次数 | 清除双入口和整页假 attempts，降低不变集扫描 |
| GC / OS | 阶段已提交删除量、CPU/块 I/O、await、PSI、swap-in/out | 用同负载窗口比较；不拿累计 Docker BlockIO 当实时速率 |

若最终仍有一个阶段显著主导，给出最慢 5 个匿名化坐标、调用栈/查询计划和前后差值后再合并代码。不得以“慢 SQL 优化完成”但无整体 μ / SLO 改善结案。

## 7. 存量完整性恢复与发布顺序

1. **先完成 R0/R1 的最小代码与隔离验收，再完成 R2/R3。** 每份变更追加 `changelog`；保持当前无关工作区改动独立。对实际部署角色做文件哈希核验和镜像回退锚点，不能只比仓库 HEAD。
2. 先发布 Projector 侧兼容改动。若 R2 最终需要生产者 envelope 小字段，再发布相关源捕获变更；必须同时兼容旧字符串、历史 JSON subtype 对象、Native marker、DELETE/NULL 和快照路径。不能只修新回执却让老 backlog 无法读。
3. source head/checkpoint、generation、有效 view/cursor、entity/contribution 水位、gap/pin 在发布前后连续；不重置、不切 alias、不全库重建。只替换实际改动角色，按现有发布工具核验。
4. 观察正常新增投影的 SLO 和容量，确认没有新 gap 后，按 gap 原因和原坐标**有界**核验存量。区分 owned execution / Case 依赖与非本系统 broker 事件；不是只挑交易 Case，所有受影响 Case 创建/状态/结果和指标均纳入核对。
5. 依赖仍在源中的，先按原主键恢复对应 read 依赖，再用既有 repair 与实体/贡献水位补其后继；同一原始回执重复执行应幂等。无需模型重跑、任务重调度、交易 intent 重发或券商调用。
6. 对 archive / pack / exception 证据定位不到的项，保留明确缺口与 owner，不能把 hot 查询未返回解释为业务事实不存在。对已证实非 Serving 消费范围的记录，保存判定依据和原始 receipt 追溯后再退出其重试负担；不能批量删 gap。
7. 新 view 与既有有效旧 view 都核验：前者及时看到新值，后者维持原快照一致性，REMOVE/UPDATE 的前像和游标仍可用。API 成功但读模型滞后的状态提示可单独补充，但不能作为吞吐修复完成条件。

## 8. 回归、资源门槛与停止条件

### 8.1 正确性回归

优先扩充 `tests/v2_backend/test_projection_freshness.py`、`test_projector_contention.py`、`test_projection.py`、`test_json_subtype_receipts.py`、`test_source_lifecycle.py`、`test_execution_projection.py`，以及 graph/decision metrics 对应用例。需验证：

- 全源轮转和源内顺序；Case 创建→中间态→终态、Turn/usage/候选/归档、更新/删除和源失联恢复。
- skip 前缀后紧接业务回执，任意点崩溃重放不越过未提交事实；gap 不丢、checkpoint 不假推进。
- 修复旧 gap 不能覆盖更新实体/贡献；源 epoch 不匹配仍正确停止该源。
- 内容注册与 artifact reclaim 竞争、文件发布失败、BEGIN/COMMIT BUSY；没有悬空内容/owner 或新锁死。
- POLICY_TRADE 方向、推理正文和冻结输入引用不丢；旧 subtype / NULL / Native marker 路径一致。
- 未真正尝试的 repair 不增加 attempts；确定失败退避后仍能被依赖修复唤醒；有工作源不会被历史 repair 饿死。
- 对同一输入增量 / 当前快照 / repair 输出规范化后的对象、贡献、指标桶一致。

本轮没有修改应用代码，因此没有把现有大批功能测试重跑当作性能结论；只对诊断脚本做语法检查、证据一致性和只读防护检查。

### 8.2 现有服务器资源验收

绝对阈值与同负载前后比较同时满足；低负载窗口不能替代高峰。超标时先找实际消耗来源，不继续叠加并发。

| 项目 | 初始验收合同 |
| --- | --- |
| Projector 进程 | 单正常投影进程；30 min 平均 ≤1 个 CPU 核，P95 ≤1.5 核；RSS 常态 ≤1 GiB、峰值 ≤1.5 GiB；不存在无界增长 |
| 全机 | 同等业务负载 CPU 5 min 平均 ≤80%；CPU PSI some avg60 目标 ≤20%；可用内存 ≥4 GiB；swap-in/out 不持续增加（5 min 均值 <1 MiB/s） |
| I/O | Projector+Read GC 正常负载实际块写平均 ≤5 MiB/s，追赶 ≤10 MiB/s；每新增有效源事件的逻辑/物理字节分别报告；相同回放写放大至少下降 50% |
| 延迟与争用 | vda write await P95 ≤10 ms、IO PSI some avg60 ≤5%；没有持续 5 min util>85% 且队列增长的组合；Read 写锁等待 P95 ≤20 ms、P99 ≤100 ms |
| GC | 正常保留窗口外积压在 72 h 内不净增长，有已提交退出量；维护平均额外 CPU ≤0.25 核，不因缩短锁时间变成无限忙循环 |
| 空间 | 发布期间可用盘不低于 20 GiB；7 日恢复到 ≥30 GiB 操作余量或给出已验证的平台/退出计划；不能靠放任维护停止来满足投影 SLO |

这些资源数字是待执行的验收门槛。若例如 Chrome/Worker 其他负载导致全机指标超限，必须记录实际归属；Projector 修改仍需证明不额外放大，不能擅自降低其他业务容量、切换网络地区或停业务腾资源。

### 8.3 回退 / 停止

- 新增 gap、结果/方向不一致、checkpoint 越过未处理事件、旧 view 失效：立即停止候选发布并回退代码，保留已提交状态及水位；不要回退业务数据库或源事件。
- 持续 5 min 正常源最老待投影年龄 >120 s、积压正增长，或 CPU/IO 明显恶化：停止继续放量，保存阶段计时，回退引入回归的那一项。
- 薄 slice/缓存无法满足兼容语义，回退该优化，不回退整个源业务；已经持久化的小型兼容字段可保留读取兼容。
- 若 R1–R4 后仍未达 12/35 每秒容量，先凭 R0 热点做一轮定向精简。只有证明单 writer 在合理事件操作量下仍无法满足已量化负载，才另行评估更大架构；本方案不预先建设第二条投影通道、优先级队列、Redis/Kafka 或多 writer。

## 9. 连续窗口与最终取证状态

连续窗口：`samples.jsonl` 每 30 秒一次、31 个点；`resources.jsonl` 每 15 秒一次、61 个点。数据库读取不跨采样保留事务。资源采样中的 `/proc` 单 PID 最初指向容器 init，**不能拿该 init 的 CPU/I/O 数作为 Python 工作量**；容器 cgroup 累计 CPU、io.stat、memory.current、压力数据及全机计数有效；另外用 docker top 核验了实际 Python PID。

窗口为 **05:36:35–05:51:35**，31 个数据库样本、61 个资源点完整返回，单次数据库探测最长约 312 ms。计算脚本为 `analyze.py` / `resource_analysis.py`。

| 源 | 起始 → 末次 checkpoint | 起始 → 末次 head | 窗口新增 /s | 窗口消费坐标 /s | 起始 → 末次距离 |
| --- | --- | --- | ---: | ---: | ---: |
| Runtime | 4,499,053 → 4,502,587 | 4,501,700 → 4,502,587 | 0.985 | 3.926 | 2,647 → 0 |
| Bus | 11,177,705 → 11,178,721 | 11,177,705 → 11,178,721 | 1.129 | 1.129 | 0 → 0 |
| research | 48,108 → 48,122 | 48,108 → 48,122 | 0.016 | 0.016 | 0 → 0 |
| initialization | 156,812 → 156,812 | 156,812 → 156,812 | 0 | 0 | 0 → 0 |

Runtime 在 **05:39:35** 首次采到距离 0，之后出现过短小突发（本窗口追平后最大采样距离 28），末次仍为 0。不能把窗口平均 3.926/s 当饱和服务能力，因为后 12 分钟多数时间没有积压。已有 17,204 个 Runtime gap 末次全部仍在；05:44:05 还采到 1 个 research `SOURCE_UNAVAILABLE`，下一点已消失，原始错误细节没有保留，不能擅自归因于某个锁或网络故障。

每点最近最多 60 个 mutation commits 合并后按 Read seq 去重：Runtime 846 个样本的投影年龄 P50 4.43 s、P95 4,790.82 s、P99 4,822.78 s；包含窗口前段刚落地的旧积压，所以不是追平后稳定延迟。Bus 125 个样本 P95 2.74 s、P99 5.35 s。抽样会漏掉高吞吐时的大量事件，不能称为所有事件总体分位数；research / initialization 没有取到足够 mutation 样本，不能填 0 延迟冒充已测。稳定 SLO 以 R0 按事件聚合为准。

| 资源（15 分钟差分） | 实测 |
| --- | --- |
| 全机 CPU busy（不含 iowait） | 均值 85.39%，15 秒桶 P95 94.24% |
| 全机 iowait | 均值 1.73% |
| vda util | 均值 27.01%，15 秒桶 P95 40.13% |
| vda await | 15 秒桶读 P95 0.70 ms，写 P95 2.13 ms；不是单次 I/O 延迟分位数 |
| CPU / IO PSI some avg60 最大 | 50.88% / 6.37% |
| available 内存最低 | 7,232.9 MiB |
| swap 进 / 出总量 | 32.13 / 34.07 MiB，非持续高带宽换页 |
| Projector cgroup CPU | 均值 0.467 核，15 秒桶 P95 0.604 核 |
| Projector 实际块读 / 写 | 0.953 / 2.810 MiB/s；写 15 秒桶 P95 11.524 MiB/s |
| Read GC 实际块写 | 均值 5.162 MiB/s，15 秒桶 P95 12.779 MiB/s |
| Transport GC 实际块读 / 写 | 0.729 / 0.010 MiB/s |
| Read WAL 文件 | 样本约 10.68–82.03 MiB；文件大小不是本轮写入字节量 |

Projector cgroup 含本轮容器内只读探针，计数有小幅诊断扰动；映射/字段探测各为秒级，不把它算成修复效果。最终单独 `ps` 查询，实际 Projector Python RSS 为 2,284,764 KiB（约 2.18 GiB）；cgroup memory.current 还含缓存等，不能代替 RSS。

因此本轮较强结论是：**CPU 调度竞争明显，维护写放大可量化，但磁盘设备并没有在整个窗口持续饱和。** 应先精简已复现的重试、重复解析和无效事务，不能凭盘满 94% 就直接宣布“换盘即可恢复”。本窗口追平是自然消费结果，没有生产修复、参数调整或维护暂停；长期验收仍未完成。

## 10. 证据目录与复核入口

全部本轮材料在 `eval/runtime_read_projection_audit_20261112/`：

| 材料 | 用途 |
| --- | --- |
| `inventory.json`、`production-sources.json`、`production/` | 实际角色、镜像、命令、挂载及 13 份代码冻结 |
| `details.json`、`rates.json`、`summary.json`、`resource-summary.json` | 查询计划/预算、源分钟负载、实际落地分钟锚点与资源汇总 |
| `samples.jsonl`、`resources.jsonl` | 连续消费、gap、WAL 与服务器资源窗口 |
| `map-probe.json`、`payload-probe.json` | 只读映射/Native 展开成本及调用树 |
| `gap-census.json`、`repair-read-probe.json` | 存量缺口分类与同输入连接复用对照 |
| `live-profile-1.raw`、`live-profile-2.raw`、`live-syscalls.txt` | 进程实际调用栈和有界 syscall 旁证 |
| `remote.py`、各 probe、`analyze.py` | 复现入口；remote 包装器本身不保证任意输入安全，重跑前必须审查输入脚本 |

复算本地统计：`python eval/runtime_read_projection_audit_20261112/analyze.py`。不要未经审查对生产运行 `project`、`repair`、`migrate`、`gc`、`rebuild`、`archive` 等命令。

SQLite 的自动 checkpoint 默认可在达到 1,000 页的 COMMIT 中触发，所以 COMMIT 时间可能包含 checkpoint 工作；这也是必须分段实测、不能直接归咎 fsync 的原因。[SQLite WAL 官方文档](https://www.sqlite.org/wal.html)。`synchronous` 的语义及持久性取舍按官方定义核验，本方案不靠降低持久性解决性能。[SQLite PRAGMA 官方文档](https://www.sqlite.org/pragma.html)。非阻塞外部采样的机制与限制参照 [py-spy 官方说明](https://github.com/benfred/py-spy)。
