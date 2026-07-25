# CDECR 运行效率、Token 与审计优化交付报告

日期：2026-07-25  
测试语料：冻结的 30 篇 `cdecr-dreamer-grounder-ab-v1` 数据集  
结论状态：工程优化已完成并通过离线回归；两次真实全流程运行均未通过完整业务验收，当前不建议继续放大并发或上线激进 Wire 短 ID 协议。

## 1. 执行结论

本轮按“先留基线、Phase 0、第一次 30 篇、Phase 1/2、第二次 30 篇、对比”执行。最终保留了不改变决策语义的并发、批处理、Schema 和审计改动；激进候选字典/长 ID 映射在真实模型试跑中触发系统性结构化输出失败，因此没有继续作为生产请求协议使用，而是降级为不发送给模型的 shadow 审计。

第二轮相对 Phase 0：

- 首轮墙钟时间从 2,492,192 ms 降至 2,358,994 ms，下降 5.34%。
- 含幂等复跑的总时间从 2,777,988 ms 降至 2,507,824 ms，下降 9.73%。
- 汇总输入 token 下降 17.08%，输出 token 下降 3.42%，请求 payload 字节下降 23.42%。
- 文档成功数从 17 增至 18，但跨文档成功数从 15 降至 14。
- 两轮 Mention Schema 与 Evidence Span 有效率均为 100%。
- 两轮完整业务验收均为失败；成本下降不能解释为业务效果改善。

这里最重要的判断是：第二轮的总量指标受到真实模型随机性、成功文档集合变化、Mention 与候选集合变化的共同影响，不是严格配对实验。可以确认“运行更快、总消耗更低”，但不能把 17.08% 输入 token 降幅全部归因于 Wire 精简，更不能据此宣称语义等价。

## 2. 提交与执行顺序

| 顺序 | Commit | 内容 |
|---|---|---|
| 1 | `344e027` | 开始优化前的完整工作区 checkpoint |
| 2 | `dd6c580` | Phase 0：调度、并发、低风险批处理、Schema title 精简、审计与遥测 |
| 3 | `08180dc` | 固化第一次 30 篇 Phase 0 运行结果 |
| 4 | `3e71b01` | Phase 1 候选 Wire DTO/短 ID 方案 |
| 5 | `57633b9` | 真实试跑失败后将风险 Wire 协议改为 shadow，不再发给模型 |
| 6 | 本报告提交 | 固化第二次 30 篇结果、两轮对比程序与结论 |

## 3. 用户约束的执行情况

| 约束 | 执行结果 |
|---|---|
| 不删除 Schema `description` | 保留 |
| 不删除 Schema `default/examples` | 保留 |
| 仅允许删除 Schema `title` | 只在 provider-facing schema 副本删除生成器 title；进程内 DTO 仍保留 title 供路由 |
| 不修改 prompt | System Prompt、决策说明和输出要求未修改；N9/N12/N13 仅把等价 JSON 序列化改为无空白格式 |
| reason 协议不变，正常通过也要有原因 | 未删、未缩、未改 reason 合同 |
| 不做 N5.5 Snapshot 批量 LLM Decide | 未做；N5.5 的顺序决策语义保持 |
| batch 不激进增大，也不保守下调 | 只增加文档级并发、Embedding 合批和安全的同层 fan-out；复杂 N9/N12/N13 LLM 判断批量上限未提高也未下调 |

## 4. Phase 0 实际改动

### 4.1 全局调度与并发

增加共享、按模型层级限流的调度器，默认并发为：

| Lane | 默认并发 | 业务判断 |
|---|---:|---|
| M1 | 2 | Embedding 调用轻，但仍受 provider 连接与限流约束 |
| M2 | 6 | 轻量决策调用占比高，可获得主要并行收益 |
| M3 | 3 | 跨文档聚类上下文较重，避免大并发放大延迟和失败 |
| M4 | 2 | Judge 最慢且输出质量敏感，保持低并发 |
| 文档编排 | 3 | 允许不同文档并行，单篇内部业务依赖不被打乱 |

这些值是可配置的运行默认值，不是硬编码的 provider 最大吞吐。它们把“同一模型 lane 的压力”与“文档 fan-out”分开，避免 `文档并发 × 节点并发` 无界相乘。

### 4.2 单文档“先规划、后并行”

编排层先按精确文档指纹形成处理计划：

- 非重复文档各自作为代表文档。
- 精确重复文档只选一个代表执行完整流程，其余文档复用代表结果。
- 代表选择是确定性的，只作用于同一精确指纹组；不存在重复关系的文档不会互相竞争“唯一代表”。
- 计划完成后，不同代表文档在文档并发上限内执行。
- 同一超长文档的 Dreamer/Grounder block 仍遵守该文档内部合并与 checkpoint 依赖。

因此去重不是语义相似度去重，也不会用标题近似等高风险规则吞掉有效文档。

### 4.3 安全的局部并行与批处理

- Atomic 与 Mention Embedding 刷新并行。
- Package Embedding 改为 provider 支持的合批。
- N12 按已有 tier/依赖关系分组执行。
- Package hint 在一次文档处理内只解析一次。
- title Embedding 保持 fail-fast，没有做可能浪费调用的投机并发。

没有增大复杂决策节点的模型 batch。真实运行中，Grounder、Judge、Atomic Coreference、Package Assignment 和 Package Merge 的输入长度与候选数波动较大；在没有质量曲线前，扩大单次 batch 会增加注意力稀释、漏项、结构化输出失败和整批重试成本。

### 4.4 Schema 精简

只对发送给 provider 的 JSON Schema 深拷贝移除 `title`。`description/default/examples/required/enum` 和所有校验约束保留，内部 DTO 及 JSON Mode 选择逻辑不变。

因此：

- 各节点仍使用原 JSON Mode。
- 输出校验仍使用原持久化/业务 DTO。
- 没有修改数据库模型。
- 没有把 Wire DTO 变成新的持久化合同。

### 4.5 审计与遥测

- 在 N5.5 前创建跨文档 trace，失败路径也能关联到本次运行。
- 每个最终 Mention 写入 `MENTION_DERIVATION`，保存来源 message、ACCEPT/SPLIT/去重/复用路径及关联 normalization decision。
- Atomic Assignment 审计保存输入 Mention hash、候选 Event 版本与 identity hash、N9 决策、结果版本。
- Package Assignment 审计保存输入 Atomic Event 版本与 identity hash、候选 Package 版本/profile hash、结果版本。
- 每个模型调用增加 queue wait、request item/candidate 数、payload bytes 和 stage/tier 指标。

第二轮 Registry 中有 105 个 Mention 和 105 条 `MENTION_DERIVATION`；另有 76 条 Atomic Assignment、60 条 Package Assignment 以及 84 条 Wire Shadow 审计。Assignment 审计是 append-only 决策历史，数量不等同于最终活跃实体数量。

## 5. Phase 1/2：为什么没有直接上线激进 Wire DTO

### 5.1 Wire DTO 的定义

Wire DTO 是“只存在于 LLM 请求/响应边界的传输结构”。它可以把持久化模型中的长 ID、重复字段或候选对象转换为请求内短 ID，模型响应后再映射回原业务对象。它不应成为数据库模型，也不应改变最终业务合同。

本轮候选方案对 N9/N12/N13 做了：

- 候选对象请求内短 ID；
- 重复 canonical ID 的请求内字典；
- 紧凑 JSON 序列化；
- 原输出 Schema 与映射回业务对象的适配。

### 5.2 真实试跑触发的熔断

在候选协议 commit `3e71b01` 上启动 30 篇真实运行后，前五个已完成的跨文档路径中有四个在 `atomic_coreference_escalation` 发生 `structured_output_invalid`；当时 Registry 还保留 1 个成功、4 个失败和 1 个被中止的运行。

这不是偶发单文档失败，而是新表示法与既有 Prompt/Schema 组合不稳定。继续跑完 30 篇只会消耗更多 token，且无法证明业务等价，因此按异常熔断：

1. 停止该次运行；
2. 恢复 Phase 0 的模型可见语义结构；
3. 仅保留无空白 JSON 序列化；
4. 将候选短 ID/字典结构改为 `WIRE_PAYLOAD_SHADOW`，只计算和审计，不发送给 LLM。

### 5.3 Shadow 得到的潜在空间

| 节点 | 样本 | 原 payload | 候选 payload | 净字节节省 | 净节省比例 | 是否发给模型 |
|---|---:|---:|---:|---:|---:|---|
| Atomic Coreference | 32 | 485,904 | 381,059 | 104,845 | 21.58% | 否 |
| Package Assignment | 16 | 535,323 | 291,826 | 243,497 | 45.49% | 否 |
| Package Merge | 36 | 1,131,230 | 566,163 | 565,067 | 49.95% | 否 |

这些是同一请求状态下可复算的 JSON 字节估计，不是已实现的 token 节省，也不是质量通过后的成本收益。候选协议如果未来继续推进，必须单节点 A/B，并让 Prompt 明确理解字典协议；但本轮有“不修改 Prompt”的约束，所以不应继续强推。

### 5.4 Phase 2 的边界

原方案中可能涉及 reason 缩减、Prompt 调整、动态模型路由、N5.5 批量 Decide 或定向 repair 的项目，本轮均未激活：

- reason 缩减与用户明确要求冲突；
- Prompt 调整被明确禁止；
- N5.5 batch 被明确排除；
- 动态路由与定向 repair 需要独立质量 A/B，不能混入这两次运行后声称因果收益。

因此本轮 Phase 1/2 的安全交付是“紧凑 JSON + 可审计的 shadow 候选协议 + 真实质量熔断证据”，而不是为了完成清单而上线已证实不稳定的表示法。

## 6. 两次 30 篇完整运行

### 6.1 可复核制品

Phase 0：

- Registry：`.tmp/cdecr/runtime_optimization/phase0_30_20260725_b.sqlite3`
- Registry SHA256：`0787DAA73F15C7D5E100F3458F5846168990AD61C87CE8F2EA133C9D5BD1A7FC`
- Report：`.tmp/cdecr/runtime_optimization/phase0_30_20260725_b_report.json`
- Report SHA256：`24381C7776862113773389FDAAE9D7E57C6923F00C667BFC7E560DD9C2DD1838`

Phase 1/2 safe-shadow：

- Registry：`.tmp/cdecr/runtime_optimization/phase12_shadow_30_20260725.sqlite3`
- Registry SHA256：`134D70E1B166A672BAED38920537DD04C0F29399AAD1B7C9B4F7D89676E62F76`
- Report：`.tmp/cdecr/runtime_optimization/phase12_shadow_30_20260725_report.json`
- Report SHA256：`98A23875938CCDA2C6BE41CF5FD4ECA183804DE52ACE10C88D1A4B12F51A508D`

`.tmp` 原始制品不提交 Git，tracked summary 保存路径、hash 和核心指标，能够检查本机原始证据是否漂移。

### 6.2 效率与成本

| 指标 | Phase 0 | Phase 1/2 safe-shadow | 变化 |
|---|---:|---:|---:|
| 首轮墙钟时间 | 2,492,192 ms | 2,358,994 ms | -5.34% |
| 含幂等复跑总时间 | 2,777,988 ms | 2,507,824 ms | -9.73% |
| 首轮模型调用 | 420 | 428 | +1.90% |
| 总模型调用 | 451 | 435 | -3.55% |
| 输入 token | 1,473,596 | 1,221,850 | -17.08% |
| 输出 token | 295,554 | 285,438 | -3.42% |
| 请求 payload 字节 | 4,151,433 | 3,179,344 | -23.42% |
| 模型 latency 累计 | 4,860,955 ms | 4,503,813 ms | -7.35% |
| repair 调用 | 28 | 25 | -10.71% |
| scheduler queue wait | 652 ms | 615 ms | -5.67% |

首轮调用反而增加 8 次，而总调用减少 16 次，主要差异来自幂等复跑调用从 31 次降至 7 次。不能把“总调用下降”理解为首轮编排减少了调用。

### 6.3 关键 LLM 节点

| 节点 | Calls 0→1/2 | Input token 0→1/2 | Payload bytes 0→1/2 | P50 latency 0→1/2 |
|---|---:|---:|---:|---:|
| Grounder | 30→30 | 116,949→105,230 | 257,486→204,551 | 39,895→44,525 ms |
| Grounder Repair | 18→19 | 96,226→102,975 | 296,835→313,298 | 46,933→58,580 ms |
| Judge | 21→21 | 94,510→94,065 | 170,864→168,829 | 8,028→6,258 ms |
| Atomic Coreference | 33→32 | 182,112→180,587 | 492,030→485,904 | 11,366→8,841 ms |
| Package Assignment | 16→16 | 214,848→176,060 | 684,466→535,323 | 26,674→22,368 ms |
| Package Merge | 50→36 | 554,849→365,279 | 1,840,076→1,131,230 | 15,006→14,545 ms |

Package Merge 的总量下降最大，但其调用数从 50 变为 36，说明候选/聚类状态已经不同，不能用该行直接计算单请求 Wire 收益。真正同状态的候选节省只应参考 shadow 表。

## 7. 业务效果与稳定性

### 7.1 成功率和输出规模

| 指标 | Phase 0 | Phase 1/2 safe-shadow |
|---|---:|---:|
| 文档成功 | 17/30 | 18/30 |
| 跨文档成功 | 15 | 14 |
| Mention | 96 | 105 |
| Atomic Event | 37 | 41 |
| Package | 31 | 30 |
| Mention Schema 有效率 | 100% | 100% |
| Evidence Span 有效率 | 100% | 100% |
| 完整 acceptance | 失败 | 失败 |

两轮文档成功集合只有 15 篇相同：Phase 0 独有 2 篇成功，第二轮独有 3 篇成功，Jaccard 为 0.75。跨文档成功集合有 13 篇相同，Jaccard 为 0.8125。

Mention ID 仅有 18 个跨轮相同，Jaccard 为 0.098。由于 Mention ID 来自模型输出内容，后续 Atomic/Package 共簇 pair 的可比公共集合太小，不能据此证明聚类更好或更差。这个结果进一步说明：真实模型单次重放不足以做语义等价验收，需要固定上游 Mention 或进行多次配对重放。

### 7.2 失败分布

Phase 0：

- Grounder repair 后语义校验失败：9。
- Judge evidence/JSON/语义失败：4。
- N6 Identity unresolved：2。

Phase 1/2 safe-shadow：

- Grounder repair 后语义校验失败：9。
- Judge evidence 校验失败：3。
- N6 Identity unresolved：2。
- 跨文档编排 `ImmutableRecordConflict`：1。
- 跨文档编排 `ValueError`：1。

第二轮没有消除主要 Grounder 失败，同时出现两个新的跨文档编排失败。因此即使运行时间下降，也不能通过业务验收。

### 7.3 业务边界

| 边界 | Phase 0 | Phase 1/2 safe-shadow | 判断 |
|---|---:|---:|---|
| Hard cannot-link shadow observations | 116/4,106（2.82%） | 78/4,983（1.57%） | 比例下降，但候选集合不同且仍非零 |
| Reaction in earnings package | 0/28 | 1/30 | 第二轮出现 1 个违规 |
| 其他三类边界 | 0 个可评估样本 | 0 个可评估样本 | 不能视作通过 |

Hard cannot-link 当前仍是 shadow 配置；这些数字是风险观测，不是已经阻断的 false merge。第二轮 violation rate 较低，但业务输入不成对，不能作为优化带来的确定性改善。

### 7.4 幂等性

- 成功文档两轮均完全复用。
- 跨文档成功事件两轮均未全部复用。
- 幂等复跑模型调用从 31 次降至 7 次，改善 77.42%，但验收条件仍未满足。
- 两轮复跑都没有新增 Mention/Atomic/Package，问题集中在跨文档 processing key/全局状态收敛，而不是持久化实体重复创建。

## 8. 关于 batch 大小的最终评估

“增大 batch”只在以下条件同时满足时有净收益：

1. 每项任务结构近似，模型不需要在大量异构候选之间反复切换；
2. 输出条目能被严格做 coverage 校验；
3. 单项失败不会导致整批昂贵重试；
4. 上下文仍远离模型性能下降区；
5. provider 的 latency 随 batch 增长显著小于线性。

本轮采取的平衡是：

- 文档之间并行，因为业务状态可以按文档隔离。
- Embedding 合批，因为输出是定长向量且可按位置严格校验。
- 安全同层节点有限 fan-out。
- Grounder/Judge/N9/N12/N13 不激进增大 batch。
- 不下调原有复杂节点 batch，避免因过度保守增加调用次数。

真实数据支持这一选择：Grounder repair 仍有 18/19 次，且激进 Wire 表示已经使 Atomic escalation 发生 4/5 系统性失败。如果此时再扩大复杂判断 batch，整批失败的 token 损失和诊断难度都会更高。

下一步若要调 batch，应单节点做 1×/1.5×/2×阶梯实验，同时记录：

- first-pass structured success；
- coverage/漏项率；
- repair 率；
- 每 item 输入/输出 token；
- P50/P95 latency；
- 单个业务边界违规率。

只有质量不劣且单位 item 成本下降时才提高默认值。

## 9. 最终验收判断与建议

### 可以保留

- Phase 0 全局分层调度和文档级并行。
- 精确重复文档的确定性代表规划。
- Embedding 合批和安全同层 fan-out。
- provider-facing schema 仅删除 title。
- 紧凑 JSON 序列化。
- Mention/Atomic/Package derivation audit 和跨文档 trace。
- stage/payload/queue/candidate 遥测。
- Wire candidate shadow 审计。

### 暂不应上线

- N9/N12/N13 候选字典和长 ID 映射作为真实模型输入。
- 更大的复杂 LLM batch。
- N5.5 Snapshot 批量 Decide。
- reason 缩减。
- 未经单独 A/B 的 Prompt、repair 或动态模型路由调整。

### 后续 P0

1. 修复第二轮的 `ImmutableRecordConflict` 与 `ValueError`，并让成功跨文档路径零调用复用。
2. 继续处理 Grounder/Judge 的主要严格校验失败；这是当前完成率瓶颈，优先级高于继续压 token。
3. 对固定上游 Mention 的 N9、N12、N13 分节点 A/B；不要再以完整随机重放作为 Wire 语义等价的唯一证据。
4. Hard cannot-link 在从 shadow 转 enforce 前，人工抽查违规样本并确认规则误报率。

## 10. 机器可读结果

- Phase 0 summary：`dev_plan/CDECR/experiments/runtime_optimization_phase0_30_summary.json`
- Phase 1/2 safe-shadow summary：`dev_plan/CDECR/experiments/runtime_optimization_phase12_30_summary.json`
- 完整对比：`dev_plan/CDECR/experiments/runtime_optimization_phase0_phase12_comparison.json`
- 可复跑对比程序：`scripts/cdecr_compare_runtime_optimization.py`
