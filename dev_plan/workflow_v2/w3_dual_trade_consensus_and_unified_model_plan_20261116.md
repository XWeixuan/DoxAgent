# W3 双任务一致性试验与统一模型配置：可执行开发方案

2026-11-16。状态：方案，尚未实施。本轮只勘察本地代码并撰写文档；未改源码、提示词、Schema、配置或生产环境，未调用收费模型。

## 1. 结论与边界

可实现，推荐在现有 **一个 Case → 一个 Trade Effect → 一个最终业务提交 → 一个 Operations** 的骨架内，增加可关闭的 Trade 双副本执行。不开新 Workflow、不建立两个业务 Case、不引入新服务/消息队列/线程池/通用投票框架。默认仍为单任务。

两个需求拆开提交：

1. **确定实施的配置调整**：新进入 W3 的所有 Case 均使用 `gpt-6-luna / max`；Trade 开 fast，Operations 关 fast。与试验开关无关。
2. **可开关的试验能力**：Trade 同时执行 A/B 两份相同任务；取得两份可消费结果后，仅比较 `decision`，同向才保留该方向，不同则最终 `NO_TRADE`。不重新调用第三个模型裁决。

需要修正两个预期：

- 此方法是**方向一致性门控**，不是独立正确性审查。相同模型、提示词和信息可以共同犯同一个错误；不能据此声称基本消除错误，不能把两次错误概率直接相乘。它也可能过滤掉正确的单次交易。
- LONG/SHORT 对冲时，两份原始回执都没有 NO_TRADE。必须由编排生成带明确来源的 NO_TRADE，不能把任一模型的原始输出改写成它“说过 NO_TRADE”。

不用额外修改 Agent 输出 Schema。现有字段名是 `NO_TRADE`，不是 `NOTRADE`。W1/W2、新旧/HIT/RELATED、W1R3、成交资格、交易时窗和输出期限都保持原规则。

## 2. 当前实现勘察

基线为本地 `main=dcded5e6f`，另有其他对话未提交修改。本报告区分已提交实现和工作区变更，不据此断言生产已部署哪个版本。

| 当前代码 | 已确认的行为 | 对此次开发的意义 |
| --- | --- | --- |
| `w3_models.freeze_case_profile` | `_enqueue_route_effects` 在 W2 完成、进入 W3 时冻结 profile；HIT 或 RELATED 选 Sol/medium，否则 Luna/max | 统一模型应改冻结点，不能仅改 SDK 默认值 |
| `w3_models.execution_profile` | Operations 固定 Luna/max，旧已冻结 Trade profile 优先 | 保留旧在途身份；Operations 模型切换其实已具备 |
| `codex_worker/sdk_runtime._start` | Luna 的非 Operations 阶段开 fast，Operations 为 default | 大体符合目标；需检查模板/Ready 与恢复参数 |
| `w3_execution.prepare_public` | 仍为 Luna/max、Sol/medium 两种 profile 准备公共模板 | 新规则不再常态预热 Sol，但不能删其旧 Case 恢复支持 |
| `w3_execution.trade/_commit_trade` | 单结果保存在 Trade stage，随后原子写交易/归档并入队一个 Operations Effect | 聚合必须发生在 `_commit_trade` 之前；副本绝不能各自走提交 |
| `w3_threads` | stage 主键 `(case_id,phase)`；create_case 原子绑定一个 Trade stage；owned(case_id) 返回一行 | 两副本需要两个内部 stage 和 record，不能继续无选择地调用 owned(case_id) |
| `w3.run_phase/_wait_v4` | Future key 已含 Case、record、phase；Worker 回执身份已含 workspace/run_id | 两份独立 workspace 已能隔离 Future/Job，不必重做 Worker 幂等协议 |
| `w3_ready._claim` | 绑定键为 Case＋record，准备时已有 operation_key | 同 Case 两 record 可复用现有 Ready 协议，必须领取两个独立物理线程 |
| `service` Future 续接 | 每个 Effect 维护一个 pending Future，等待期间不占用业务线程 | 保留父 Effect，用一个聚合 Future 承载两份异步任务 |
| `_ack/commit_stage` | ACK 后会精简原始回执并删除部分请求；目前 ACK 往往与阶段业务提交关联 | 两份结构化结果及审计索引必须在 ACK 前独立落盘 |
| `w3_research`、market | context manifest、行情准备、history/PIN/WATCH 冻结以 Case/phase 为键 | 公共输入只准备一次；不能在两份任务里分别抢建或由 A 提前 stop_market |
| `v2_read/runtime/decision_metrics` | Worker attempt 按 Job ID 展示；非模型耗时已有模型时间区间求并集 | 展示两份 attempt；计费累加，墙钟延迟不能累加 |

### 工作区中的并行开发注意事项

- 当前未提交提示词把 `operations.md` 放到 `w3/skills/operations.md`，而已提交 v6 Bundle 校验和注入仍读取 `w3/operations.md`；`contract.json` 仍是 v5。实施前应与提示词资产交付统一路径，不能擅自恢复/覆盖其他对话的内容，也不能把 v6 当作已激活。
- 未提交的 SDK 回测参数 `w3_service_tier` 当前在阶段判断后覆盖 fast 值，存在 Operations 被覆盖为 fast 的可能。本轮确定性要求是 Operations 必须关 fast，后续实现须让该阶段约束优先，显式回测 tier 只作用于 Trade。
- 不把其他对话的新回测输入、IBKR 权限改动、提示词和数据集顺带提交。实施仍先隔离工作树，再审查集成。

## 3. 聚合与代表记录：完整规则

“一致”只比较枚举方向，**不比较 reason、prior_expectation、expectation_delta 文本**。否则两次同向但措辞不同也会被错误拦截。

| A | B | 最终方向 | 正统结果/线程的选择 |
| --- | --- | --- | --- |
| LONG | LONG | LONG | 较长任务的完整四字段结果 |
| SHORT | SHORT | SHORT | 较长任务的完整四字段结果 |
| NO_TRADE | NO_TRADE | NO_TRADE | 较长任务的完整四字段结果 |
| NO_TRADE | LONG/SHORT | NO_TRADE | 原本输出 NO_TRADE 的任务 |
| LONG/SHORT | NO_TRADE | NO_TRADE | 原本输出 NO_TRADE 的任务 |
| LONG | SHORT，或反之 | NO_TRADE | 编排合成四字段结果；较长任务只作为承载线程/审计代表 |

最后一行合成：

```json
{
  "decision": "NO_TRADE",
  "reason": "W3_PARALLEL_DIRECTION_MISMATCH: A=LONG, B=SHORT",
  "prior_expectation": null,
  "expectation_delta": null
}
```

实际 reason 按冻结 A/B 方向确定性生成；来源记录为 `CONSENSUS_ROUTER`，原始 A/B 四字段内容永不修改。其他行直接复制选中回执，不拼接两段分析或伪造一份共同推理。

**较长的定义**：成功的那次 Worker Job 的 `finished_at-started_at`，不累计失败重试、Scheduler 等待和停机时间；它表示任务执行时长，不代表更正确。使用持久化 Job 时间，排序一次后保存；等长选 A。若旧/异常回执缺时间则固定选 A、记录 `duration_unavailable`，不为一个展示选择指标重跑模型或阻塞交易。新试验验收要求时间字段完整。

**Operations 推荐跟随正统结果对应的线程**，不再额外挑一个：一致时自然是较长任务；一份 NO_TRADE 时跟随该份；LONG/SHORT 冲突时跟随较长任务，但本轮 `submitted_decision` 明确给编排最终 NO_TRADE。用户允许 Operations 自选其一，采用此规则最少引入额外映射，也避免在一个判断 LONG 的线程里运营另一个线程的 NO_TRADE。仅启动一次 Operations。

若两份结果尚不完整或有非法输出，不进入上述方向表；技术失败不是 NO_TRADE 票，见第 7 节。

## 4. 开关：一份配置，每 Case 一次冻结

复用 `RuntimeJournal.runtime_values`，只新增一个全局配置值：

```text
namespace=w3_trade_config, key=mode
value="single" | "dual_consensus"
缺失时默认 single
```

在既有 Runtime 运维 CLI 增加 `w3-trade-mode show/set --mode ...`（拟定命令），读取/设置这个值并输出回执。复用现有 Journal、操作日志和正式配置操作方式，不新增 UI、配置服务或另一份 env 同步状态。切换不重启 Worker，不需要修改 Prompt/Skill 或模型 Schema。

首次创建 `ROUTE_TO_W3` Effect 时，和 profile 一起冻结 `case.frozen_inputs.w3_trade_mode`，随 Case/Effect 原子保存。若已有 Trade Effect/stage/Job 而没有该字段，按历史 single 恢复；不能因重试读到新开关而突然增发 B。

- 开关只影响**之后首次进入 W3 的 Case**。single 关闭不把在途 dual 改成单任务，不拆掉已受理的 B。
- 双模式只用于已有统一方向输出的 v5/v6 两阶段契约；旧 v4/单阶段保持 single 并明确记录不支持，不改写旧 Case。v5/v6 各自保持原 Operations 职责，双任务功能不负责激活 v6。
- 配置只有 single/dual 两种，不扩展 ticker 路由、百分比采样、第三票、多数票或按负载自动降级。
- 统一 Luna/max 是单独的确定性改动，关闭双模式不会恢复按 Policy 选 Sol。

## 5. 最小执行结构

```text
W1R2 / W2完成
  └─ 原 ROUTE_TO_W3 Effect，冻结 mode 与 profile
       ├─ single：沿用原 Trade 实现
       └─ dual：公共输入只准备一次
            ├─ A：独立 record / workspace / 物理 thread / Job → 本地校验、保存原结果
            └─ B：独立 record / workspace / 物理 thread / Job → 本地校验、保存原结果
                  ↓ 两份成功结果齐全
             确定性方向聚合
                  ↓
       原 _commit_trade，一次交易/归档与一次 Operations 入队
                  ↓
       一份 Operations，沿代表线程恢复
```

W1R3 仍是原兄弟 Effect，只执行一次，不加入双任务；不等待它。每个原 Case 也只能有一份交易意图、候选、归档、日记录和一次 PIN/WATCH 运营。

### 5.1 共享什么、隔离什么

共享：冻结 AGENTS/Skill/operations、合同、输出 Schema、消息/上游判断、成交历史与标签、PIN/WATCH 快照、D1/D2 版本、cutoff、模型/推理/fast、工具权限、初始时间上下文。

隔离：物理 Thread、record、workspace、Worker Job/dispatch key、输出文件、重试 generation、Future、进度及结果记录。两个任务分别从相同的纯公共模板 Fork，不能 B Fork 自已经做过 A 研判的线程，也不能在同一物理 Thread 并发两个 Turn。

将当前 lazy 的任务投影/context bind 拆出一个轻量 `prepare_trade_input`：在父路径中构造并冻结最终 User JSON/Schema/context_ref **一次**，然后两份执行共同引用。`run_phase` 接受可选的已准备 Trade 输入；single 可保留原包装调用。校验两份初始 prompt/schema/公共资产 hash 一致，内部任务元数据不注入模型。不要 deep-copy 两份完整 Case/D1/D2，不建立两份研究 pack，也不重复查询成交账本。

“相同”指初始任务内容与能力相同。两条真实研究路径的搜索选择、返回时间和实时行情仍可能不同；不为实现字面上的全轨迹相同而冻结/录制所有在线工具或关闭正常研究能力。

Case 级行情预准备只发起一次，两份 Trade 结果/技术处理收尾后才 stop；A 返回不提前停止 B 仍在使用的准备。两份可共享只读内容，不能共享可变进度字典。`_observe_phase_job` 按分支记录，不让 A/B 交替覆盖唯一 `w3_job_state`；Case 总状态在父层归并。

### 5.2 内部记录，不增加一套业务表

复用 `runtime_v2_w3_stages`：

| 内部 phase key | 作用 |
| --- | --- |
| `trade_decision` | 既有正统 Trade stage，保存最终结果、代表 Thread 和聚合摘要 |
| `trade_decision:A` | A 执行 stage，绑定 A record、Job、原结果和持续时间 |
| `trade_decision:B` | B 执行 stage，同上 |
| `post_decision_operations` | 唯一 Operations stage，使用正统 Trade 的 Thread |

`trade_decision:A/B` **只用于 Runtime 内部 stage 索引**，送 Worker/SDK 的 `task_phase` 始终是 `trade_decision`。否则会破坏 fast、Schema、Ready、SSE 和计时分派。

`create_case/_thread` 增加可选 `stage_key`，默认仍 `trade_decision`；以目标 stage 原子创建/绑定 record。双模式每分支一次绑定；主 stage 在聚合前不绑定任意分支为正统。无新表、无两份 RuntimeCase、无第二份业务 Effect。

按 stage.input_refs.record_id 读取 record。双模式相关的 `_thread/operations/recover/restore` 不得使用无序 `owned(case_id)` 选第一行。`owned` 可保留单任务兼容；`release(case_id)` 原本能释放多行，但聚合前不能提前调用。清理按 record 处理，不因一个分支完成就释放另一个活动分支。

主 stage.input_refs 保存一次聚合摘要，建议：

```json
{
  "consensus_version": 1,
  "mode": "dual_consensus",
  "branches": {"A": {"stage_key": "trade_decision:A"}, "B": {"stage_key": "trade_decision:B"}},
  "directions_agree": false,
  "selected_branch": "B",
  "result_source": "MODEL_BRANCH",
  "selection_reason": "NO_TRADE_BRANCH"
}
```

分支 stage 自身保存 Job/record/耗时/完整原结果，摘要通过引用读取，不再重复保存整份 Case/研究输入。LONG/SHORT 冲突时 `result_source=CONSENSUS_ROUTER`、`selection_reason=OPPOSITE_DIRECTIONS`；主 stage 的 `worker_job_id` 留空，代表 Job 放在明确的来源引用中，避免把合成结果伪装成该 Job 原输出。常规行可保持主 stage 指向选中的真实 Job。

不添加一份长期维护的“聚合状态”真值；是否齐全由两份 stage/result 推出，是否已经提交由既有主 stage.COMMITTED 判断。

### 5.3 并行与 Future 续接

新增窄的 `run_trade_pair` 包装，在现有 Agent asyncio loop 上并行运行两个分支，用一个聚合 Future 返回父 Effect。两个分支必须先启动，再等待任一个；不能连续调用两个会阻塞的同步 run_phase，也不新建 ThreadPoolExecutor。

每个分支一返回即完成本地校验和短事务保存，不等另一份再保存；聚合 Future 用 `gather(return_exceptions=True)` 一类已有异步原语收齐，单个异常不取消另一份任务。父 Effect 沿用 `_phase_waiters/_resume_phase`，等待期间不持有 SQLite 事务、W3 锁或后台池线程。恢复时从已保存 stage/Receipt 取结果，仅启动/恢复缺失分支。

现有 Future key、Receipt identity 已包含 record/workspace，不新增一套 Worker identity。需逐一核对 `recover_phase_result/precheck_phase/prepare_request/worker_rejected` 的 lookup 使用对应分支 workspace；公共 context_ref 可以共享，输出与进度键必须隔离。计时增加 branch/record 标签，不把内部 phase 别名送入 SDK。

## 6. 唯一提交与一次 Operations

两份合法结果齐全后，在事务外确定最终四字段、代表线程和小型摘要；在原 `_commit_trade` 的一个短事务内完成：

1. 两分支原结果引用齐全/主 stage 尚未提交的检查，沿用现有 Effect 栅栏。
2. 主 Trade stage 绑定代表 record/thread，写聚合结果/来源摘要。
3. 原交易资格校验、TradeOutputService、归档及日记录逻辑。
4. 原子标主 stage.COMMITTED、更新 Case 正统结果、入队一个原幂等键的 Operations Effect。

副本函数只负责准备、调用、校验和保存证据，不能调用 `_trade_record/_commit_trade/TradeOutputService`。不能先让 A 交易、再等 B 撤销；也不能 A 已是 NO_TRADE 就取消 B，因为用户要求两份记录和基于两份结果选择代表。

原交易过期/闭市/授权规则仍然在最终提交时适用。等待 B 后意图可能过期，这属于本试验实际代价；不延长交易期限、不绕过原 guard，也不因为未成交倒写模型方向。

Operations 必须解析主 stage 已冻结的代表 record，而不是再从全部 owned rows 随机取一条。即使主结果为编排 NO_TRADE，也只给 Operations 注入该最终 `submitted_decision`，绝不能给两分支各发一份运营。沿用该 Case 原合同的运营职责和资产，v6 仍只做 PIN/WATCH。

## 7. 失败、恢复、ACK 与保留

- **一份成功、另一份仍运行**：保存成功份，继续原 Job；不提交、不重跑成功份。
- **一份明确技术失败/非法输出**：只对失败分支沿用现有有界修复/重试规则，包含明确终态后的 generation；不为“判断不一致”重试，也不通过多抽几次挑一致答案。
- **重试耗尽仍缺合法结果**：沿用技术失败终态，不产生交易，记录 `CONSENSUS_INCOMPLETE` 及原错误；不伪装为两模型一致 NO_TRADE，不启动正常 Operations。人工恢复仍消费原成功结果。
- **网络超时/5xx/派发未知**：按原 record/operation_key/idempotency 查询并重连；只有确定性原 Job 不存在才按原规则派发。不能把 A/B 标签重建成新 Case 来规避恢复。
- **Provider Pause**：只约束实际新请求/合法重试；两份成功结果的解析、保存、聚合和业务提交不得等 Pause。保留当前真实限流处理，不新增双任务全局暂停机。
- **重启/租约交接**：读取冻结 mode、两个 stage、原 record 与 Job；同一分支不换物理 Thread。聚合前已存 A、聚合后未 commit、commit 后 ACK 前等切点均需恢复测试。
- **不可双重提交**：唯一父 Effect 的原租约与主 stage.COMMITTED 是提交依据；重复 Future 回调不绕过此栅栏，不新增第二把全局锁。

两份任务都应能审阅。各分支保存原结构化结果、原始输出的持久化引用、Job/Thread/Turn、输入及资产 hash、开始结束时间、有效模型/档位/tier、usage、已有 Summary/工具调用审计索引。Case 正统字段仍只有一份；Read/API 的 attempts 保留两份，另增加一个轻量聚合证据摘要，明确哪份选中、冲突是否合成。无需重做前端工作台。

**ACK 不能清掉唯一证据**。对分支结果，应在上述消费所需证据保存后复用现有 ACK 待办，允许独立 ACK 已成功且已持久化的分支，以释放 Worker 结果资源，不必占着 resident 等另一份；Operations 仍可恢复原物理线程身份。主 stage 无真实 Job 的合成路径也必须 ACK A、B，不能依赖主 stage.worker_job_id。不得在分支 ACK 时标主 Trade 业务 COMMITTED。

持久化引用纳入现有存储生命周期可达性检查，防止 ACK/精简回执后工具索引悬空；完整 rollout/Thread 文件沿用原审计保留规则，不复制两套完整工作目录、无限保留进程或新建归档服务。不能只存一个即将被现有清理删除的绝对路径来声称“两份已留存”。完成选择后，未选中 Thread 不再执行任务，释放 owner/驻留资源并沿现有固定生命周期清理；选中 Thread 留给唯一 Operations。

## 8. 统一模型及 fast：独立落地

`freeze_case_profile` 对之后首次进入 W3 的 Case 统一冻结：

```json
{"model":"gpt-6-luna","effort":"max","provider":"沿用现有 provider"}
```

不再读取 policy_hit/related 来选模型。`execution_profile(...phase=Operations)` 保持 Luna/max；SDK 中 Operations 的 `service_tier=default`、`features.fast_mode=false` 优先于所有显式 Trade 回测 tier 覆盖。Trade 为 fast/true，single 和 A/B 完全相同。

公共预准备只主动准备新 Luna/max profile，A/B 使用同一公共模板的不同子线程。保留旧 Sol 配置解码、已有模板、已受理 Job 和原冻结 profile 的恢复；不批量回写历史 profile 或让一个原 Job 在恢复中更换模型。切点前原在途按原请求完成，切点后新 Case 统一 Luna/max；不为落实新默认取消重跑旧 Case。显式离线历史回测 model override 继续能复现旧配置。

不再创建新的 Sol 常驻预热，但也不在发布时删旧在途 Thread/模板。发布核验要覆盖 Scheduler 的冻结 profile、Worker 实际请求/生效配置、Ready 冷启动及 Operations resume，不能只看某处默认字符串。

## 9. 代价与生产试验验收

### 成本与资源不能用“1000 Case/日”直接证明可行

若单模式每 Case Trade 成本为 T、Operations 为 O，双模式约为 `2T+O`，总倍率为 `(2T+O)/(T+O)`，通常介于 1–2；W1/W2、事实抽取不翻倍。每日约 1000 Case 时，主要新增约 1000 个 Trade agent task，agent task 内还会有多次 SDK 回合/工具调用，不能等同为新增 1000 次简单 HTTP 请求。真实缓存、失败重试及研究路径会改变倍率。

双 Trade 同时在途时，相应 capsule/SDK 客户端资源可能接近翻倍，但 Operations 不翻倍、D1/D2/行情公共准备不应翻倍。不默认把 Ready 空闲库存、线程池、常驻进程和全局模型并发配置也乘二。以**开盘峰值的并发数与单进程 P95 RSS**验证容量，而非日均吞吐。

第一轮生产容量检查应先量出统一 Luna/max **single 基线**，再开双模式，避免把 Sol→Luna 的变化误归因于双任务。当前本地代码勘察不能证明远端剩余内存、额度足够，本报告不虚构该结论。

### 延迟与统计

- 双模式 Trade 墙钟近似 `max(A,B)+共同准备及提交`，不能快于最慢一份；这是用户方案固有的 SDK 长尾代价。即使完全消除编排排队，仍可能错过原交易窗口，必须量化。
- 原 Trade、Operations、完整 Case 的**非 SDK P95≤5秒**底线不变。两分支 SDK 时间取区间并集，包含两者等待重叠，不能简单相加再从墙钟扣除；内部准备、排队、锁、解析保存、重试/退避都照实计入。确实属于 Provider 的等待单列，不把全部“等 B”统称外部等待。
- 记录两份启动时间差、Ready 命中/冷启动、Job 排队、Future→保存→聚合→commit 各段。目标是公共输入只准备一次、聚合逻辑毫秒级，正常双派发间隔 P95≤250ms（公共准备完成后的两份请求提交口径）；超标要拆分内部等待与 Provider 准入，不能调大超时掩盖。
- 每 Case CPU/内存/Swap/物理 I/O、数据库事务数和锁等待、运行/排队 Job、capsule 回收、缓存命中、token、费用和失败率按 single/dual 分组。收费未知保留 unknown，不编造实际 cost。
- 质量统计同时报告九宫格方向分布、共识交易率、分歧率、被门控的 LONG/SHORT、技术不完整率；一致率高不等于准确率高。对过滤掉的已标注 alpha 和一致做错的 badcase 分别复审，不能只统计拦截成功的例子。

### 试验推进

1. 先发布统一模型，双模式默认 off；验收新 Case 配置及单任务路径。
2. 离线冻结样本跑 single/dual，校验两份结果、恢复、聚合和成本，不以技术失败样本充当 NO_TRADE。
3. 低流量窗口短期开启全局 dual，确认实时双派发、资源回收和费用计量，再关闭开关观察在途自然收尾；通过后在峰值短窗口复核，才决定持续启用。不开新灰度平台。
4. 若产生新内部排队、换页/I/O 压力或不可持续额度消耗，停止接纳新的 dual Case，排查或结束试验。**不靠把 B 放后台限速、降低实时并发或静默退成单票来伪装双模式可行**。关开关不是强制停止所有已受理双任务，资源预警时必须留出其收尾余量。

## 10. 实施文件与顺序

| 顺序 | 修改点 | 具体边界 |
| --- | --- | --- |
| 1 | `w3_models.py`、`w3_execution.prepare_public`、`codex_worker/sdk_runtime.py` | 统一新 profile，减少不再需要的 Sol 预热，固定 Operations fast 优先级；保留历史恢复 |
| 2 | `persistent_runtime_v2/operations.py`、CLI、`service._enqueue_route_effects` | 一个现有 KV 开关、首次路由冻结；旧 Effect 不补发 B |
| 3 | `w3_threads.py`、`w3_execution._thread/operations/maintain` | stage_key 可选参数，两个独立 record；正统 record 明确读取；分支 owner/期限/恢复与清理 |
| 4 | `w3.py`、`w3_execution.py` | 公共输入一次准备、run_trade_pair、分支结果即刻保存、单父 Future 续接；Worker task_phase 不变 |
| 5 | 小型纯函数模块 `w3_consensus.py` | 九宫格、代表选择、合成结果；不做模型调用、DB 或框架抽象 |
| 6 | `_commit_trade`、stage/ACK、Read/API | 唯一聚合提交、一次 Operations、两份证据及摘要；历史 single 输出形状不变 |
| 7 | `worker_receipts.py`/生命周期可达性、`w3_timing.py`、Read metrics | 只改经分支测试证实不兼容的 lookup/保留/计时；复用已有 identity/SSE，避免重写 |
| 8 | `w3_backtest/runner.py`、`decision_runner.py`、CLI/report | 接受独立 mode 参数并加入 run 指纹，两结果与最终结果分开评分；旧 run 不静默改 mode。不要求 decision-only 数据集获得其本来缺失的业务上下文 |

不需要新增 WorkerRunRequest 的任务类别、模型输出字段、RuntimeSideEffect 枚举、全局投票锁或数据库大表迁移。必要元数据用现有 stage/input_refs；Read 展示优先复用两个现有 attempts，只补一个有界摘要。

实施拆成“统一模型”和“双任务默认关闭”两个可独立审查提交，每次重要代码变更追加 changelog；不将提示词和其他对话开发一并混入。生产发布按现有正式流程核验 Scheduler、Worker 和必要的 Read/API 版本，不在发布过程中临时重写原 Job。

## 11. 必须通过的回归

1. single 路径请求数、Thread、结果、Operations、恢复行为与原模式相同；除确定性模型调整外不改变业务结果。
2. 九种方向组合全部覆盖；只比较 decision。较长/等长/时间缺失、合成来源都确定性且可追踪。
3. A/B 初始 prompt、Schema、模型/effort/tier/权限/hash 相同，物理 thread/workspace/job/key 不同；没有第二份业务 Case/W1R3/Operations。
4. 挂住 A 或 B，另一份立即保存；两任务并发启动；等待不占用原后台池，也不堵其他实时 Case。
5. A 成功 B 失败/非法/限流/未知派发：只恢复正确分支；永不凭一份 LONG 下单，不把缺失当 NO_TRADE，不多抽结果求一致。
6. 在分支绑定、准备受理未知、Job 成功未保存、仅 A 保存、两份保存未聚合、聚合提交前后、ACK 前后重启/丢租约，均维持原身份且最多一次业务提交。
7. Provider Pause 活跃时两份已成功结果仍可聚合提交；网络异常不等于 Job 不存在。
8. 选择一个 NO_TRADE 后，仅其线程跑 Operations；LONG/SHORT 冲突时主结果明确合成，Ops 接收最终 NO_TRADE；另一份历史记录不被改写。
9. 双份 ACK/receipt 精简、文件生命周期及 Thread 清理后，在约定保留期内仍能审阅两份结果与审计索引；合成路径不漏 ACK、未选中 capsule 不常驻泄漏。
10. 开关来回切换不改变在途 mode/profile；旧 v4/single、旧 Sol Job 正常恢复。错误 mode 明确拒绝设置，不悄悄默认。
11. Policy HIT/RELATED/无 Policy 的新 Case 都是 Luna/max；Trade fast on、Operations fast off，包括 Ready、resume、回测显式 tier 覆盖；两阶段 Schema/Prompt 不因模型统一而变化。
12. 所有交易方向组合均保留上游 NEW/OLD、HIT/RELATED，NO_TRADE 不抹掉 HIT；原时窗过期、资格拒绝、归档、coverage/事实、PIN/WATCH 幂等回归照常。
13. 双模型区间重叠时，成本相加、SDK 时间求并集；缺计时保持 unknown。非 SDK P95 与峰值资源/费用分别验收，不以平均耗时替代。

**最终推荐**：先完成并验收统一 Luna/max；再在现有 Trade 内增加默认关闭的双任务门控。把“原始两份结果”与“唯一最终交易决策”清晰区分，复用当前线程/Job/Effect/恢复机制，只扩展确实需要的分支身份与短聚合路径。
