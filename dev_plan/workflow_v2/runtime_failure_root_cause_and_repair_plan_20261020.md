# 2026-10-19 生产 Runtime 失败排查与修复建议

排查日期：2026-10-20。主快照时间：北京时间 2026-10-20 17:05（UTC 09:05）；后续补充读取 Worker 记录、当前容器源码及日志。

本轮仅排查、整理证据和提出方案。未修改业务实现、未重启容器、未恢复或重放 Case、未发起模型测试。计数表示快照时的持久化状态，不保证此后没有人工恢复或新部署。

## 1. 结论与统计口径

这次不是同一个模型故障造成的失败。主要问题集中在：停机时调度任务与 Case 状态脱节、SQLite 写争用、W3 Fork 准备超时，以及准备失败后重复命中同一失败绑定。另有账户限额、模型输出契约问题和少量供应商错误。

“昨天”存在两个实际相关口径，不能混用：

| 口径 | Case 总量 | COMPLETED | FAILED | UNAVAILABLE | 长期 RUNNING |
|---|---:|---:|---:|---:|---:|
| 北京时间 10 月 19 日创建 | 421 | 338 | 47 | 2 | 34 |
| Case 自身 `trading_date=2026-10-19` | 713 | 621 | 55 | 3 | 34 |

北京时间窗口为 UTC 10 月 18 日 16:00 至 19 日 16:00。交易日口径直接使用 Case 的业务字段，不用美东自然日自行替代；该批实际创建时间为 UTC 19 日 06:00 至 20 日 05:57。

**按北京时间昨天：49 个终态失败，7 种错误码。按业务交易日：58 个终态失败，9 种错误码。** 本文逐类分析使用覆盖夜间美股运行的业务交易日口径，并同时保留北京时间对照。

| 终态错误码 | 交易日 Case 数 | 北京时间 Case 数 | 实际问题 |
|---|---:|---:|---|
| `TimeoutError` | 24 | 24 | 旧 W3 Fork/线程准备失败，并非模型输出超时 |
| `OperationalError` | 15 | 15 | SQLite `database is locked` |
| `HTTPStatusError` | 6 | 2 | 新 W3 READY claim 返回 409，底层 slot 准备已超时 |
| `CODEX_TURN_FAILED` | 5 | 5 | Codex 账户 `usage_limit_exceeded` |
| `provider_request_failed` | 2 | 1 | 百炼 `BadRequestError`，上游详情未保留 |
| `structured_output_validation_failed` | 2 | 1 | W2R2 缺字段、W1R2 事实归属不一致 |
| `provider_timeout` | 2 | 1 | 百炼模型调用超时 |
| `semantic_output_validation_failed` | 1 | 0 | W2R2 返回 R1 候选之外的 Policy |
| `provider_rate_limited` | 1 | 0 | OpenRouter 429 |
| **合计** | **58** | **49** | FAILED 与 UNAVAILABLE 合计 |

还存在两组不能遗漏、也不能直接加进上述错误码表的问题：

- **34 个 Case 长期 RUNNING，但对应调度任务已 FAILED。** 其中 33 个对应 service closed，1 个对应数据库锁。这是业务没有继续推进、页面却仍显示运行中的独立问题。
- **23 个 Case 的 W3 post-decision operations 失败，但 Case 为 COMPLETED。** 北京时间口径为 16 个。交易判定已完成，后续事件/Policy 处理未完整完成，不能当作交易判定失败，也不能理解成整个流程完全成功。

交易日样本中的 W1/W2 一共记录 72 次失败 attempt，其中 54 次后来在同 Case 同轮获得成功输出。这里按该批 Case 在快照前的全部 attempt 统计；“失败 attempt 数”不等于“最终失败 Case 数”。

## 2. 生产版本核对

排查时 scheduler 与 Worker 容器镜像 ID、启动时间不同。但对相关模块进行换行归一后的源码比较，scheduler 使用的相关实现与本地一致；Worker 实际执行的 SDK、线程准备、READY 模块也与本地对应实现一致。

因此，不能仅凭镜像 ID 不同就认定此次仍是“修复没进新镜像”。Worker 镜像中部分 scheduler 专用文件的差异，不足以解释 Worker 执行错误。此次同时存在昨天旧执行契约冻结的 Case、昨天发布切换过程的错误，以及当前实现仍然存在的问题，必须分别处理。

## 3. 停机控制错误被当成业务失败，留下 34 个悬挂 Case（P0）

### 证据和根因

`service.py:321` 的 `close()` 先设置 `self._closed=True`，随后才调用 coordinator.close 等待已有工作退出。`execute_message()` 在入口检查该标记，直接抛出 `RuntimeError("Persistent Runtime V2 service is closed")`。coordinator `_execute_owned()` 的普通异常分支调用 `journal.fail()`。

这意味着：已经被认领、但尚未走到入口的任务，在 drain 过程中遇到 service closed，按普通异常消耗失败次数并被标 FAILED；已经创建的 Case 未同步结束或恢复为可调度状态。

UTC 19 日 08:25:56—08:26:17（北京时间 16:25:56—16:26:17）密集出现 70 条 service closed 调度 gap。通过已有 receipt 能确认其中 33 个对应当前悬挂 Case；其余记录不能仅凭缺少 Case receipt 就认定为“从未创建 Case”。另有 1 个悬挂 Case 对应数据库锁异常。

示例：`case_21b2c7c0c0dd4473b8d042bee10f8796`（MU），已存在成功 W1 输出，但任务失败、Case 仍 RUNNING。

### 最优修复边界

1. 分开“停止接收新工作”和“服务彻底关闭”。先停认领，允许已持有租约的工作继续到安全检查点；最后再关闭模型客户端与线程池。
2. 停机/取消/租约丢失属于运行控制语义，不能混入普通业务失败预算。停止时未完成的任务应保留检查点并可恢复；租约丢失仍立即停止写入。
3. 增加启动时的窄范围对账：仅对已证实由停机控制或 SQLite BUSY 导致的 task/Case 状态脱节恢复调度，不能把全部 FAILED 一键重置。
4. 恢复原 Case、原冻结输入、原成功轮次和 Effect 身份，不重新生成成功的 W1/W2，也不重新产生已提交交易意图。

回归必须覆盖“任务已认领、排队期间开始关闭、随后进入 execute_message”，而不只是线程池关闭后是否允许 submit。

## 4. SQLite 写锁冲突导致 15 个终态失败，并影响另 1 个悬挂 Case（P0）

### 证据和根因边界

15 个终态错误均为 `database is locked`。有的在模型前失败，有的 W1/W2 已成功后才失败，因此不能归因于某个 LLM 节点。

示例：`case_ed78463b78df4f4fb9faa3ec96e53011`（INTC），已有成功 W1R1/R2/R3、W2R1，之后被数据库锁终止。另一个 Case 在 W2 超时后又遇到锁异常，最终 Case 错误只留下数据库锁，说明还存在后续异常覆盖最初错误的问题。

`journal.py:118` 已使用 WAL、`BEGIN IMMEDIATE` 和 30 秒 busy timeout，当前也有事务耗时日志。这些不是缺失待补的能力。仍存在以下争用放大路径：

- `journal.set()`、`freeze()` 在写事务内执行 `content.encode()`；相关内容可能涉及 native 内容文件序列化、写入。
- `w3_execution.py:708` 在写事务内编码完整 Case。
- 多个运行线程共享同一 SQLite 文件，单写者争用会传播到任务、回执、Case 和 Effect 状态落盘。

当前日志可见数秒级写锁等待。但事发时缺少完整持锁事务身份，**尚不能证明某一个具体函数就是当时唯一长时间持锁者**。确认的是写锁竞争造成业务失败，以及当前存在应缩短的事务路径。

### 修复方案

1. 将不可变内容编码、哈希和 native 文件 I/O 移到 `BEGIN` 前；事务内只保留必要 SQL、租约/版本校验和原子状态更新。
2. 对确认为 SQLite BUSY/LOCKED 的短事务增加有界退避重试，必须重试完整事务并重新检查租约；不要把外部模型请求包在重试块内。
3. 锁暂时不可用时保留恢复检查点，避免直接留下 task FAILED / Case RUNNING。不能吞掉其他 SQL 错误或取消、租约控制错误。
4. 保留现有日志，补齐 operation 名、Case/task、扩展 SQLite 错误码、等待/持锁时长；保留原始失败原因链。

不建议以全局降低并发、单纯继续拉长 busy timeout 或立即迁移数据库作为第一轮方案。

## 5. 24 个旧 W3 在 Fork 准备阶段超时（P0）

### 已确认的故障链

24 个 Case 均冻结了旧入口契约，没有 `w3_entry_version`。对应 Worker operation 的 source_case_id 能一一关联，最终为 FAILED / RECONCILING / TimeoutError，恢复计数达到 3，未获得有效 thread_id。

代表：`case_a6c10053633c4e83a5fde446c803846c`（NBIS），Fork operation 为 `w3-fork:ed3ed8007f47483aa19d18e2361f50f7`。

计时记录显示，某失败 Case 的 context.prepare 约 89ms，而 `phase.materialize_and_model` 约 149 秒并抛 W3PreparationError。故障发生在 Worker 准备链，**不能解释成提示词过长、模型研判慢或上下文压缩**。

`codex_worker/thread_lifecycle.py:173-294` 将进程恢复等待、启动信号量排队和实际 prepare_fork 包在同一个启动任务中；先等 10 秒，再等 55 秒。拥堵时排队也消耗这约 65 秒的预算。恢复多次后 operation 终态失败，之后原身份重试不能自然变成一次健康准备。

现有历史计时不足以进一步量化每次是排队、MCP 初始化还是 Fork RPC 占主导。可以确认故障阶段和超时/恢复设计问题，不能把全部 24 个强行归因于 MCP 单点。

### 修复方案

- 分开记录和限制排队等待、实际 SDK 启动/Fork；在有有效 owner 和进展时持久化为准备中，保留总耗时上限。
- 查询原 operation、固定 cwd 和已有 thread receipt 后再恢复。确认原准备已结束且没有已接收的业务 turn 时，才允许新的准备 generation。
- 配置非法、鉴权等确定错误应快速终止并告警，不当作瞬时慢启动反复等待。
- 兼容恢复旧 Case 的冻结业务契约；不能为了恢复旧任务而直接注入新版 prompt 或改业务身份。

新版 READY 池能改善新 Case 的冷启动，但不会自动修复已终态失败的旧 Fork operation。

## 6. 新入口 READY 失败绑定导致 6 个 Case 反复 409（P0）

这 6 个不是普通 HTTP 网络故障。对应 slot 均在 pool miss 后绑定 Case，准备超时，随后被退休并保留错误。`w3_ready.py:266` 的 claim 再次读取原绑定，遇到 `row['error']` 立即抛 ValueError；API 将其转成 409。Case 重试仍领取同一失败绑定，无法得到健康线程。

代表：`case_5dfebc1c132f4520967480fe519a6956`，slot `da0a9d0733aa4553982ab23e4a0f6f3b`。其中 2 个发生在北京时间昨天，另 4 个发生在其后的同一业务交易日。

建议：

1. READY API 区分未配置、准备中、准备失败、身份冲突等结构化错误；scheduler 保留实际错误码和原因，避免统一成为 HTTPStatusError。
2. 对准备超时先 reconcile 原 operation。仅当确认无已接收业务 turn，且原准备已终结，才更换准备 generation / slot 绑定。
3. 未配置及永久配置错误进入可见告警和补池冷却，不能按普通 Case 重试持续创建失败准备。

不能简单放宽 claim 身份校验，也不能无条件删 slot 重试，否则会引入同一 Case 多线程执行风险。

## 7. Codex 额度耗尽：5 个 Case，重复了 15 次失败 job（P1）

Worker 原始错误明确为 `usage_limit_exceeded`，不是一般 CODEX_TURN_FAILED。相同额度问题被每个 Case 重试 3 次，无法靠短期立即重试恢复。

示例：`case_8d973efc4f5841cebb90c2cda0074058`（MU）。后续 Worker 存在成功 job，因此这里只能确认当时达到额度限制，不能宣称当前账户仍不可用。

应保留供应商细分错误，对确定额度耗尽暂停受影响账户的派发并告警，将 Case 留在可恢复/等待状态，避免耗尽普通重试次数。额度恢复后根据 Case 时效决定恢复原阶段或明确过期结束；不自动切账户、模型或重新提交交易。

## 8. W1/W2 输出契约问题：有重复模式，应定点修复（P1）

### 8.1 W2R3 prompt 要求 `reason`，Schema 不接收

当前 `prompts/persistent_runtime_v2/w2_r3.md:21` 明确要求输出 reason，但 `schema.py:438-454` 只定义 condition_results / related_policy_results 及对象 ID、严格布尔值，没有 reason。昨天出现 3 次 W2R3 结构校验失败，其中包含多余 reason 的实际输出；另有不完整 Condition ID 等错误。

最小修复是删除多余 reason 输出要求，提供严格结构示例，保留完整 ID、严格布尔值和覆盖校验。无需为迁就多余输出新增业务字段。

**保留当前设计：** 对齐非法/不完整应记为 unavailable 并交 Router，不能折成 false；NEW + RELATED 在没有完整有效否定判断时继续 W3。W2R3 attempt 失败不能直接等同 Case 失败。

### 8.2 W1R2 事实归属与 reference_ids 不一致

交易日样本有 26 次 W1R2 结构失败 attempt，均为 qwen3.8-flash，其中反复出现 `Fact attribution must uniquely reference selected Events`。常见形态为 NEW、空 reference_ids，却在 fact_attributions 引用已有 Event。

应对齐 prompt 的 NEW/OLD、已知事实归属和 reference_ids 使用规则：NEW 可以包含旧事实，但如果为旧事实提供 Event attribution，该 Event 必须在允许引用的集合内并满足 Schema 关联规则。提供一组“部分已知但整体 NEW”的有效示例；重试携带具体冲突位置及允许 ID。不要通过清空 attribution、伪造 ID 或放宽语义校验来过关。

最终有 1 个该类 W1R2 Case 失败：`case_e9a4209a50454c10867e8d6e85c28e1b`（BE）。其余大量已通过修复重试恢复，但增加延迟和成本。

### 8.3 W2R2 缺 RELATED 身份、越过候选范围

- `case_437d638990c643e9ba1a2fbbbde4be34`：缺必填 `related_policy_ids`，最终 UNAVAILABLE。
- `case_4a771e9830e440339d007f05cd435e20`：返回未被 R1 选中的 Policy，最终语义失败。

当前必须维持 HIT / RELATED 身份分离和候选范围校验。修复重点是模型输出示例与定点纠错，不允许把 R1 全部候选直接变成 RELATED，也不能把未知 Policy 猜测映射成合法 ID。本次没有足够证据证明这些 Case 的冻结 prompt 一定是旧版，不能仅凭缺字段就认定部署遗漏。

### 8.4 JSON 外层 Markdown 包装

交易日 10 次 W1R1 结构失败均来自 qwen3.8-flash，出现完整 JSON 外包代码围栏等问题。当前 R1 已切到 Haiku，不能把这些历史失败当作当前 Haiku 的失败。

可在确实只有一整个 JSON 代码块时做窄范围包装剥离，之后照常执行严格结构/语义校验。不要变成任意文本提取、字段猜测或布尔值强制转换。

## 9. W2R2 的 120 秒长尾仍存在（P1，需单独评估模型优化）

业务交易日 W2R2 记录 10 次 provider_timeout attempt，实际冻结配置均为 qwen3.8-flash / medium / 120 秒。最终失败 Case 里只有 2 个以 provider_timeout 收尾；其他超时有恢复或被后续错误覆盖，不能把 10 次全部算作 10 个最终失败 Case。

成功 attempt 耗时如下，按该批 Case 在快照前记录计算：

| 节点 | 成功 attempt 数 | P50 | P95 | 最大 |
|---|---:|---:|---:|---:|
| W1R1 | 708 | 17.77s | 39.17s | 75.96s |
| W1R2 | 703 | 22.09s | 59.95s | 101.80s |
| W2R1 | 648 | 5.69s | 19.07s | 154.26s |
| W2R2 | 239 | 46.42s | 104.33s | 118.09s |

这是混合冻结配置的生产样本，不是模型速度公平对照。它足以说明：现阶段不能继续把主要长尾都归给 W2R1；W2R2 仍接近 120 秒上限。

建议沿用已有真实样本对照结果，单独评估 W2R2 更低推理强度/更快模型，重点检验 HIT/RELATED、条件归属及结构能力；生产重试应携带明确错误，控制累计预算。不要本轮未经评估直接切模型，也不建议所有节点统一提高超时。现有数据不能将模型长尾归因于跨境网络。

## 10. 百炼 400 与 OpenRouter 429：区分观测不足和限流（P1/P2）

业务交易日 2 个 Case 最终为百炼 `BadRequestError`，分别在 W2R1、W1R2。`transport.py:432` 的安全错误包装最终只留下类似 `Bailian request failed: BadRequestError`，未保留足够的上游错误信息。

**400 的具体根因目前无法确认。** 可能涉及具体请求配置或服务端契约，但现有记录不足以在这些可能性之间判定，不能编造为 previous_response_id、鉴权、结构化输出或网络故障。

先补充经过脱敏的 HTTP status、provider code/message、request ID、模型、round、schema hash 和关键配置；禁止记录密钥。400 按错误码区分永久/可修复问题，不盲目重试。之后才能针对原样本制定精确适配。

另有 `case_ccd9c60938844e3c93e982a1b21c00a1`（COHR）的 W2R1 连续 3 次 OpenRouter 429，最终失败。先支持 Retry-After 和有限抖动退避、保留配额错误原因；仅在证实持续并发限流时加 provider 级限流器，不因一个 Case 就全局降速或静默切端点。

## 11. 23 个后续 operations 失败：交易已判定，事件处理没有完整落地（P1）

业务交易日 23 个 Case 的 operations 失败均发生于验证阶段，分类为：

| 分类 | Case 数 |
|---|---:|
| reference_ids 使用 URL、文章 ID、pin ID、F# 等非合法 E# | 12 |
| E# 不在该 Case pinned Reference View 允许范围 | 7 |
| OLD 却没有引用任何 Reference View Event | 4 |

这与终态失败的 58 个 Case 分开统计。这里应修复的是引用契约与 operations 修复能力，不能重新启动 trade_decision，更不能把已提交决策当作没发生。

建议：

1. 在 operations 输入里明确、紧凑地给出该冻结版本允许引用的 E# 集合，区分事件 ID 与新闻来源、research pin、URL。使 Agent 可见范围与 validator 使用范围一致。
2. 明确 OLD 表示事实被合法 Event 覆盖；“读过相同文章”“正文不足”等理由不能替代合法事实引用。
3. 验证失败后增加一次有界、带具体错误及允许 ID 的 operations 纠错，沿用已经接受的交易决策；只重做该阶段输出。
4. Effect 和下游写入保持既有幂等身份，防止修复 operations 重复应用。
5. 页面和告警分开显示“交易判定完成”和“后续操作失败”，方便准确发现数据维护失败。

当前 ReceiptWorker 已有 rejected receipt 的新 generation 重试逻辑，不能错误归因成“所有重试只读同一个失败缓存”。需要加强的是纠错上下文与阶段边界。当前 `_prepare_v4` 也已在追加 provisional 后同步重建 reference_event_ids；不应再重复实现这一已有逻辑。

尚未逐条证明 7 个越界 E# 究竟来自不允许的详情、其他版本，还是具体输入可见范围问题。因此不能批量扩大 validator 到任意数据库 Event，也不能事后用当前库改写历史允许集合。

## 12. 同期 Worker 后台补池故障：215 次 -32600，不能算成 215 个失败 Case

Worker 持久化 operation 还显示 UTC 19 日 08:35—09:42（北京时间 16:35—17:42）密集出现 215 次 `-32600 failed to load configuration: invalid transport`，分别指向 d2_discovery、source_capture、o4_operations、data MCP 配置。这些主要是后台 READY 准备失败；对应 slot 已退休。

这发生在 24 个旧 Case 准备失败集中时段之后，不能倒置时间关系，声称这 215 次错误导致了此前全部超时。当前 SDK 对禁用 MCP 的完整配置处理已经存在，相关源码与本地一致，后续也出现正常 Worker 执行，不宜重复按历史故障直接修改配置。

另有进程关闭错误的 stderr 提到 PATH 缺少 bubblewrap，但同一文本明确表示会使用 bundled bubblewrap。当前 `which bwrap` 为空，**这本身不足以证明缺少系统包就是进程关闭的根因或当前上线阻断项**。

应补的是准备操作按 profile 的永久错误冷却与告警：连续相同配置错误时停止高频补池，避免短时间产生数百次无效初始化。对于 SDK 关闭保留实际退出码和 stderr，不把一条 fallback 提示当作致命原因。

## 13. 推荐实施与历史恢复顺序

### 第一批：先消除会让任务永远停住的编排故障

1. 修复停机 admission/drain/closed 顺序和控制错误分类；增加 task/Case 对账。
2. 缩短写事务，增加窄范围 BUSY 恢复及可定位日志。
3. 修复 Fork 启动预算、READY 失败绑定与原 operation 恢复，补池永久错误冷却。
4. 完成相应离线回归后，生成待恢复原 Case 清单，逐个核对已接受阶段、线程、Effect、交易意图及业务时效，再恢复未完成阶段。

恢复范围必须依据具体原因区分：34 个状态脱节 Case；24 个旧准备失败；6 个 READY 失败；以及数据库锁终态失败。不能直接将整批失败重放成新消息，不能自动追发已经过期的交易机会。

### 第二批：消除确定的契约冲突与无效重试

1. 同批对齐 W2R3 prompt/Schema，补 W1R2、W2R2 的定点输出纠错。
2. 对 operations 加引用边界说明与一次有界修复，仅恢复该阶段。
3. 对额度耗尽、429、400 分类处理，保留原始供应商诊断。

### 第三批：性能优化

针对 W2R2 的真实长尾，使用已有对照样本评估模型/推理强度；不与第一批正确性修复捆绑成一次大范围模型迁移。

### 验收重点

- 队列中已有任务遇到正常关闭，不再产生 service closed 终态失败或悬挂 Case。
- SQLite 暂时争用可从原检查点恢复，成功模型结果不重复执行；非 BUSY SQL 错误仍明确暴露。
- Worker 准备排队、实际启动分别可见；超时后先 reconcile，不出现同一失败绑定无限 409，也不产生双业务线程。
- quota/config 等确定错误不被连续普通重试放大。
- W2R3 有效 true、完整 false、非法/漏项的 Router 语义保持原设计；不得将非法对齐当成 false。
- operations 修复不重跑 trade_decision、不重复提交交易或 Effect；引用始终属于冻结允许范围。

## 14. 证据文件与限制

证据目录：`eval/runtime_failure_audit_20261020/`。

- `snapshot.json`：Case、模型 attempt、Effect、W3 阶段、调度任务快照。
- `detail.json`：代表 Case 的冻结契约与轮次上下文摘要。
- `worker_ops.json`、`worker.json`：Worker 线程准备 operation、slot、相关 job。
- `telemetry.json`：runtime gaps 和分段耗时。
- `scheduler_sources.json`：生产相关源码快照；`scheduler_current.log`：当前容器日志，不能替代已被轮换的旧容器日志。
- `analysis.json`、`analyze.py`：双口径确定性统计及生成程序。
- `affected_cases.csv`：业务交易日终态失败、状态悬挂和 operations 失败 Case 明细。

本次已确认到代码机制的主要问题是停机状态脱节、READY 失败绑定及 W2R3 契约冲突。数据库实际持锁者、旧 Fork 的更细 SDK 子步骤、百炼 400 具体原因，以及部分越界 Event 引用来源，证据尚不足以唯一定位。以上已分别明确下一步观测或定点修复，不将推测写成结论。
