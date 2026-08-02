# CDECR 30 篇真实验收：DeepSeek V4 Flash 正式版 + Thinking（2026-08-02）

## 1. 验收范围与可比性

- 固定语料：`cdecr-dreamer-grounder-ab-v1` 原 30 篇，已核对本轮与 2026-07-31
  报告中的 30 个 `source_row_id` 数量、集合和顺序完全一致。
- 本轮 Registry：`.tmp/cdecr/acceptance_20260802_deepseek_v4/cdecr_30_deepseek_v4_r2.sqlite3`。
- 本轮机器报告：`.tmp/cdecr/acceptance_20260802_deepseek_v4/cdecr_30_deepseek_v4_r2_report.json`。
- 对照 Registry：`.tmp/cdecr/acceptance_20260731_postopt_r3/cdecr_30_postopt_r3.sqlite3`。
- 对照报告：`.tmp/cdecr/acceptance_20260731_postopt_r3/cdecr_30_postopt_r3_report.json`。
- Mention Gold 复用 `.tmp/cdecr/resilience/mention_review_01_10.json`、
  `mention_review_11_20.json`、`mention_review_21_30.json`；Gold 总数仍为 268，不重新标注。

本轮同时包含工作流优化变化与模型/provider 变化，因此环比只能回答“当前组合相对上一轮组合是否
改善”，不能把差异单独归因于模型。用户已明确接受这一可比性限制。

### 1.1 上一轮固定基线

| 指标 | 2026-07-31 基线 |
| --- | ---: |
| 单文档 / 跨文档 / 端到端成功率 | 30/30 / 29/30 / 96.67% |
| Mention 数 | 254 |
| Atomic / Package 数 | 145 / 68 |
| Input / Output Token | 3,276,708 / 500,726 |
| 首轮墙钟 | 6,579,656 ms |
| Mention Precision / Recall（上一轮已发布口径） | 69.29% / 65.67% |
| Atomic Pair Precision / Recall（旧 provisional 口径） | 71.37% / 93.73% |
| N9 MERGE Precision | 74.07% |
| Package Pair Precision / Recall（旧 provisional 口径） | 85.82% / 98.84% |

Package 另增加相同评估器、相同 2026-07-28 reviewed Gold 的严格同口径对照：上一轮在 84 个
高置信对齐 Atomic 上为 Precision 82.31%、Recall 81.66%、F1 81.98%，3 个 Gold 组碎片化、
5 个 excess components、93 个 missed pair links。该口径将用于本轮的正式环比；不把不同
Gold 投影口径的 85.82% / 98.84% 直接拼成趋势。

为消除旧报告与本轮评估器口径差异，Mention 在第 5 节对上一轮 Registry 重新运行了同一评估器；
刷新后的可比基线为 Precision 77.56%、Recall 73.51%，正式环比以该数字为准。

## 2. 模型与 Schema 前置验收

### 2.1 实际配置

| Tier | Provider | Model | Thinking effort | Structured output |
| --- | --- | --- | --- | --- |
| M1 | DashScope | `qwen3.7-text-embedding` | N/A | embedding |
| M2 | DeepSeek 官方 | `deepseek-v4-flash` | `high` | beta strict function |
| M3 | DeepSeek 官方 | `deepseek-v4-flash` | `max` | beta strict function |
| M4 | DashScope | `qwen3.7-max` | disabled | JSON output |

N12 dictionary wire protocol 在此前真实 A/B 中未达到启用门槛，本轮显式固定为 `shadow`；这避免
把已知未通过的协议实验混入正式验收，但仍保留 shadow 审计。

### 2.2 真实 Schema 探针

正式运行前已对所有会落到 M2/M3 的节点输出形状逐一进行真实调用：Dreamer M2/M3、Grounder、
Field Coreference、N9 M2/M3、N12 普通批次、N12 pair M2/M3、N13 wire/legacy，最终全部通过
服务端 strict Schema 校验与本地 Pydantic 校验。最终有效探针强制 Dreamer/Grounder 返回非空
嵌套对象，不再用空数组绕开子结构。

| 真实探针 | Tier | Schema | Input / Output Token | 结果 |
| --- | --- | --- | ---: | --- |
| Dreamer | M2 / M3 | `DreamerModelOutput` | 579/109；592/129 | 通过 |
| Grounder | M3 | `GrounderModelOutput` | 2,174/269 | 通过 |
| Field Coreference | M2 | `FieldCoreferenceModelOutput` | 678/145 | 通过 |
| N9 | M2 / M3 | `AtomicDecisionBatch` | 824/85；837/83 | 通过 |
| N12 assignment | M2 | `PackageDecisionBatch` | 806/94 | 通过 |
| N12 pair | M2 / M3 | `PackagePairDecisionBatch` | 707/90；720/84 | 通过 |
| N13 wire / legacy | M3 | 两种 merge DTO | 516/90；538/86 | 通过 |

首轮探针暴露并修复两项 provider 契约问题：

1. thinking 模式不支持强制 `tool_choice`，因此改为保留 strict function，并用极短 system 指令要求
   恰好调用一次；最小真实探针确认能稳定返回单个 tool call。
2. Field Coreference 的 nullable enum 使用 `$ref` 嵌套在 `anyOf` 中，服务端要求分支具有明确
   `type`。随后一次无效 partial run 进一步证明：服务端虽接受其他嵌套 `$ref`，却没有可靠地
   enforce 其子对象结构；Grounder 把 `mention` 内字段平铺到 draft 根级。最终修复为 provider-only
   wire schema 全量内联本地 `$ref`，不改业务 DTO 或原 Schema。

provider-only strict compiler 还按官方支持边界移除了 wire 副本中的 `title`、`minLength`、
`maxLength`、`minItems`、`maxItems`，并保证所有 object properties 都进入 `required`、
`additionalProperties=false`。运行后的本地 Pydantic 与节点业务校验仍完整保留，因此没有把
provider 对约束关键字的限制变成业务放宽。

### 2.3 被熔断的无效 partial run（不计入验收）

第一次 30 篇启动在 20 分钟时熔断并保留证据 Registry。已完成的 9 篇均为 0 Mention；39 个
Grounder draft 全部缺少根字段 `mention`，同时把 `canonical_proposition`、`predicate`、
`assertion_state`、time、Evidence、quantity、anchor 等字段错误平铺到 draft 根级。39 次单 item
repair 后仍非法；Dreamer 也出现 14 个 `schema_validation_failed_after_repair` block degradation。

熔断时消耗约 241,906 input / 574,836 output tokens、93 次调用，provider 失败为 0。根因是
strict wire schema 的嵌套引用 enforce 缺口，不是语料、业务 Gold 或网络错误。该 Registry 不复用，
不进入成功率、质量、Token、成本或时长环比；它只作为探针为何必须使用非空嵌套样本的审计证据。

## 3. 运行完整性、失败与根因

- 单文档：30/30 成功（100%），与上一轮 30/30 持平。
- 跨文档：28/30 成功（93.33%），上一轮为 29/30（96.67%），下降 3.34pp。
- 端到端：28/30（93.33%），上一轮 29/30（96.67%），下降 3.34pp，失败。
- 产物：274 Mention、168 个报告口径 Atomic、92 Package；上一轮分别为 254、145、68。
- 300/300 Evidence 均为 `VERIFIED`，上一轮为 267/271（98.52%）。Evidence 异常未造成文档失败，
  该项通过。

### 3.1 模型调用失败没有扩大成文档失败

本轮 973 次调用中 10 次失败，全部为 DeepSeek M3 Grounder 路径返回不可解析 tool arguments：

| Stage | `invalid_json` | 实际降级 |
| --- | ---: | --- |
| `grounder` | 5 | 保留合法 item，缺失 candidate 进入 recovery |
| `grounder_item_repair` | 2 | 仅对应非法 draft 未恢复 |
| `grounder_missing_recovery` | 3 | 对应 missing candidate 保持未处置 |

30 篇仍全部完成单文档阶段，说明局部容错有效；但 D18（2 个 Gold）和 D20（1 个 Gold）最终为
0 Mention，属于“技术成功、业务失败”。Strict Schema 探针能证明服务端接受 Schema，却不能证明
thinking + function call 每次都返回合法 arguments；这 10 次失败是本轮真实 provider 可靠性缺口。

### 3.2 两个跨文档失败的共同根因

两篇均在 `add_mention_to_atomic -> merge_event_times -> EventTime` 失败，不是 provider 或 Schema
错误。当前实现分别从两侧取最早 `event_start` 和最晚 `event_end`，但没有处理“一侧只有较晚 start、
另一侧只有较早 end”的情况，最终构造 `event_start > event_end` 并触发 Pydantic `ValidationError`。

1. `34fc2a61-...`：incoming 泛“Q3 earnings beat”只有 `event_start=2026-06-24`，目标 EPS
   Atomic 只有 `event_end=2026-05-28`。这里同时存在 N9 将 umbrella 表述并入具体 EPS 的身份误判。
2. `2b5bba2a-...`：incoming Q3 gross margin 只有 `event_start=2026-06-24`，正确目标 gross-margin
   Atomic 只有 `event_end=2026-05-28`。N9 合并本身合理，纯粹被时间合并实现击穿。

因此影响范围是 2/30 跨文档 run；单文档 Mention 全部保留，但这两篇没有完整 Atomic/Package
assignment，Registry 还存在 Apply 失败前的部分写入。最终质量评估按 Registry 最终可见 current head
口径，并把该限制显式保留。

## 4. Token、成本代理与时长

| 指标 | 上一轮 | 本轮 | 环比 |
| --- | ---: | ---: | ---: |
| 调用数 | 942 | 973 | +3.29% |
| Input Token | 3,276,708 | 3,495,773 | +6.69% |
| Output Token | 500,726 | 3,066,910 | +512.49% |
| Total Token | 3,777,434 | 6,562,683 | +73.73% |
| aggregate model latency | 7,884,073 ms | 23,539,029 ms | +198.56% |
| 首轮墙钟 | 6,579,656 ms | 15,707,654 ms | +138.73% |

墙钟约 4 小时 21 分 48 秒；aggregate latency 是并发调用延迟之和，不等于墙钟。正式运行按要求
每 5 分钟检查一次，未高频轮询。

### 4.1 节点 Token 与运行时占比

| Stage | Calls | Input | Input占比 | Output | Output占比 | aggregate latency | 时长占比 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| package_assignment (N12) | 32 | 1,624,477 | 46.47% | 608,057 | 19.83% | 4,877,009 ms | 20.72% |
| atomic_coreference (N9) | 98 | 802,951 | 22.97% | 1,311,266 | 42.76% | 9,667,025 ms | 41.07% |
| field_coreference | 306 | 353,447 | 10.11% | 227,033 | 7.40% | 2,019,876 ms | 8.58% |
| judge | 28 | 170,470 | 4.88% | 16,302 | 0.53% | 253,570 ms | 1.08% |
| grounder | 30 | 151,573 | 4.34% | 548,494 | 17.88% | 3,986,762 ms | 16.94% |
| package_merge (N13) | 4 | 95,270 | 2.73% | 79,306 | 2.59% | 637,895 ms | 2.71% |
| grounder_item_repair | 17 | 75,582 | 2.16% | 69,594 | 2.27% | 582,341 ms | 2.47% |
| atomic_identity_embedding | 40 | 58,698 | 1.68% | 0 | 0% | 23,632 ms | 0.10% |
| package_embedding_m1 | 29 | 55,327 | 1.58% | 0 | 0% | 18,128 ms | 0.08% |
| dreamer | 30 | 40,462 | 1.16% | 118,398 | 3.86% | 702,024 ms | 2.98% |
| grounder_missing_recovery | 7 | 29,317 | 0.84% | 65,019 | 2.12% | 464,133 ms | 1.97% |
| atomic_coreference_escalation | 2 | 17,784 | 0.51% | 23,441 | 0.76% | 185,944 ms | 0.79% |
| 其余 M1 embedding | 350 | 20,415 | 0.58% | 0 | 0% | 120,690 ms | 0.51% |

### 4.2 成本与模型更换结论

DeepSeek M2/M3 合计 3,190,863 input / 3,050,608 output。按官方当前 cache-miss 正常时段单价
估算，DeepSeek 部分上界约 **9.29 元人民币**；峰值 2 倍时约 **18.58 元**，cache hit 会更低。
M1/M4 仍走 DashScope，因本项目没有可核验的本轮账单/统一价格配置，不把它们伪装成精确成本；
全流程成本比较以 Token 和 DeepSeek 可核验部分双轨报告。定价依据见
<https://api-docs.deepseek.com/zh-cn/quick_start/pricing/>。

“降低 Token”明确失败：input 仅增 6.69%，但 thinking 计入 completion 后 output 增 512.49%。M2
产出 1,661,051 output，M3 产出 1,389,557 output。N9 是最大 output/时长节点，N12 仍是最大
input 节点；Grounder 单次成功请求平均约 19.6k output、143 秒。

N13 input 95,270、占 2.73%，不属于总 Token 主因；但同样 4 次请求，output 从上一轮 2,651
增至 79,306、latency 从 48,708 ms 增至 637,895 ms。根因不是 payload 反弹，而是 M3 max
thinking 长推理。45 个 pair 中 4 个 `SAME_PACKAGE`、41 个 `DIFFERENT_PACKAGE`；每个模型判定
SAME 的 input 成本为 23,817 Token，但在 Gold 判定前不能称为“每个正确 join”成本。

## 5. Mention / Grounder / Judge 质量

本轮与上一轮均使用同一 268 条 source-centered Gold、同一 M4 评估器和严格的一对一
`output ↔ Gold` 口径重新评估；不是把上一轮已发布的旧评估数字直接拼接过来。

| 指标 | 上一轮同口径重评 | 本轮 | 环比 | 目标 |
| --- | ---: | ---: | ---: | ---: |
| output Mention | 254 | 274 | +20 / +7.87% | — |
| strict TP | 197 | 198 | +1 | — |
| partial | 29 | 40 | +11 | — |
| FP | 28 | 36 | +8 | — |
| FN | 71 | 70 | -1 | — |
| Precision | 77.56% | 72.26% | **-5.30pp** | >90% |
| Recall | 73.51% | 73.88% | **+0.37pp** | >85% |
| F1 | 75.48% | 73.06% | -2.42pp | — |

结论是 Mention **不通过**。新增 20 条输出只换来 1 条 strict TP，主要转化为 11 条 partial 和
8 条 FP；thinking 提高了输出量和细节量，但没有把它们转化为严格可交付的最小事实边界。

### 5.1 漏召与误报分布

本轮 70 个 FN 的首个失败位置为：

- `DREAMER_MISSING` 38（上一轮 35）：候选源头漏召仍是第一主因，且未改善；
- `OUTPUT_PARTIAL` 31（上一轮 28）：Grounder/Judge 输出了事实片段，但缺 metric、qualifier、
  对照值、机构动作或同一 occurrence 的另一必要分量；
- `JUDGE_REJECTED_OR_MERGED` 1（上一轮 5）：这一类明显改善；
- 上一轮 3 个 `GROUNDER_MISSING_OR_INVALID` 在本轮归零，但被 10 次 provider `invalid_json`
  的局部损失和更多 partial 抵消。

36 个 FP 中，31 个被评估器标为 `UNKNOWN_GOLD_ID`。主要样式不是随机噪声，而是：

1. 背景或非 Gold 市场信息被提升为 Mention，例如 RSI、国债收益率、泛化的供应短缺背景；
2. 同一 Gold 被 fan-out 成多个互补 fragment，后续片段在严格一对一口径下成为 duplicate/FP；
3. “earnings beat”“record revenue”等 umbrella 表述与具体 metric 同时存在；
4. participant 或 assertion 错位，例如 Sandisk SCA 被写成 Micron、可能举行的 investor day 被写成
   已计划事项。

最集中的 bad cases 是 D05/D06/D08/D09/D11/D12/D16/D28：财务披露中的 revenue、EPS、margin、
SCA 条款和 analyst rating/target 被拆成互补但不完整的 Mention。D18、D20 则因 Grounder 及 missing
recovery 均返回非法 JSON，分别 2 条、1 条 Gold 全漏。

### 5.2 Gold 适配性限制

复用 Gold 对“来源事实是否覆盖”仍然有效，也足以做同口径环比；但它不再完全等价于当前 Mention
atomicity contract。部分 Gold 本身把 revenue+EPS、regular+after-hours、rating+target 等复合信息
作为一个命题，而新流程有意 fan-out。于是互补子 Mention 会同时表现为 partial、FN 和 duplicate FP。
因此上述严格数值适合做保守验收，不能把所有 fragment 都解释为模型胡编；反过来，也不能用这一
Gold 边界冲突掩盖真实的 missing qualifier、错误 participant 和 umbrella FP。

### 5.3 Evidence

300/300 Evidence 为 `VERIFIED`，即 100%；上一轮为 267/271（98.52%）。本轮 semantic Evidence
repair LLM 为 0，Evidence 异常没有造成文档失败。该节点通过，而且改进是真实、可审计的。

## 6. Field 质量

全量 Field 独立评估未能完成，原因是评估所用 DashScope M4 在运行中返回明确的
`Arrearage` HTTP 400。第一次评估还暴露出“模型漏回 Gold ID 导致整批失败”的评估器硬校验；已改为
保留逐文档分片、仅补评缺失 ID，但恢复到本轮 10 篇、上一轮 11 篇时 provider 欠费熔断。没有改用
DeepSeek 自评 DeepSeek 产物，也没有把部分样本冒充全量结论。

以下仅报告两轮都完成的共同 10 篇（D01-D09、D11），属于 **provisional 同口径子集**：

| 端到端 Field accuracy | 上一轮 | 本轮 | 环比 | 门槛 |
| --- | ---: | ---: | ---: | ---: |
| predicate | 82.11% | 80.21% | -1.90pp | 90% |
| participant | 83.16% | 88.54% | +5.38pp | 96% |
| metric | 68.92% | 73.91% | +4.99pp | 90% |
| fiscal period | 86.84% | 88.57% | +1.73pp | 80% |
| total | 79.80% | 82.43% | +2.63pp | 91% |

端到端指标包含 Mention 缺失。只看已经对齐到输出 Mention 的 Gold，能更接近 Field/grounding 本身：

| 已有 Mention 条件下 | 上一轮 | 本轮 | 环比 | 门槛 |
| --- | ---: | ---: | ---: | ---: |
| predicate | 97.50% | 88.51% | **-8.99pp** | 90% |
| participant | 98.75% | 97.70% | -1.05pp | 96% |
| metric | 86.44% | 79.69% | **-6.75pp** | 90% |
| fiscal period | 94.29% | 100.00% | +5.71pp | 80% |
| total | 94.88% | 90.71% | -4.18pp | 91% |

子集上 participant、fiscal period 通过；predicate、metric 和 total 未通过。主要错误不是 canonical ID
映射本身，而是上游 Mention 已缺动作、数值、basis、benchmark 或复合动作的一半，例如 D06 rating
保留但 target 丢失、D08 adjusted EPS 写成 GAAP EPS、D06 SCAs 数量保留但 deposits/commitments 丢失。

Registry 结构指标也显示风险：本轮 1,184 个 Field link 中 `UNRESOLVED_CANONICALIZED=201`
（16.98%），上一轮为 39/1,021（3.82%）；`INTERNAL_COREFERENCE` 从 763 降至 727，而 Mention 数
反而增加。不能把这一变化解释为“更保守所以更准”，因为共同子集的 conditional metric accuracy
同步下降。全量 Field 数值必须在 M4 账户恢复后从已持久化分片续跑，不能据当前 10 篇外推。

## 7. N7 / N9 / Atomic / Identity 质量

两轮均由独立 M4 对 assignment 当时 N7 送入 N9 的每个候选逐 pair 复核：

| 指标 | 上一轮 | 本轮 | 环比 | 本轮目标 |
| --- | ---: | ---: | ---: | ---: |
| tasks | 254 | 256 | +2 | — |
| reviewer candidate coverage | 99.21% | 99.61% | +0.40pp | 100% |
| judgeable SAME opportunity | 110 | 99 | -11 | — |
| correct MERGE | 100 | 85 | -15 | — |
| incorrect MERGE | 8 | 2 | -6 | — |
| N9 MERGE Precision | 92.59% | **97.70%** | +5.11pp | >75% |
| N9 conditional MERGE Recall | 90.91% | **85.86%** | **-5.05pp** | >85% |
| CREATE_NEW accuracy | 93.84% | 91.72% | -2.12pp | — |

本轮 N9 局部准召均过目标，但 Recall 只高于门槛 0.86pp，并且环比下降。reviewer 唯一漏回的是
Apple 涨价 task 的第 5 候选（Defiance DRAM ETF）；该候选与 incoming 在 issuer/action 上显然不同，
不改变 SAME opportunity 或上述准召。这里的 99.61% 是评估器输出覆盖，不是 N7 实际少送候选。

### 7.1 真实错误合并

仅 2 个 N9 MERGE 被独立复核判错，较上一轮 8 个明显改善：

1. `$11.3B / +37%` 的 Q3 revenue 被合入 `$41.46B / +346%` revenue Atomic；metric family 相同，
   但 value/growth 冲突没有成为足够强的 occurrence 边界；
2. “连续第五个季度 revenue 创纪录”的 streak/umbrella 被合入具体 Q3 revenue Atomic。

这两条都进入 18-member revenue cluster，说明 Sidecar 已有效压住跨 metric 污染，但同 metric 的错误
数值、streak 与具体披露之间仍有语义捷径。此前 33-member cross-metric supercluster 没有复现；
当前最大 cluster 为 18，与上一轮最大值持平，而不是监控阶段误读 append-only 历史表得到的 171。

### 7.2 过度拆分

14 个本应 MERGE 的 task 被判为 `N9_RELATED_CREATE_NEW`，上一轮为 9。典型是：

- after-hours `15.78%/$1,213.96` 对同一时段 `about 15%/about $1,215`；
- gross margin 86%、Cloud Memory revenue 13.769B、100% excess cash return 等近等价改写；
- SCA 的“16 个/14 个约 $100B”完整条款与已有 SCA Atomic；
- “供给追不上需求/无 clear line of sight”、tight supply beyond 2027/through 2028；
- SK Hynix 13% 与同一 occurrence 的“more than 10%”。

这解释了 assignment 从上一轮 `108 MERGE / 146 CREATE_NEW` 变为 `87 / 169`，active Atomic 从
146 增至 169、multi-Mention cluster 从 36 降至 31。Precision 的改善是真实的，但代价是更多
fragmentation；不能只看 97.70% Precision 宣称 Identity 全面提升。

最终 Atomic 没有一份覆盖本轮随机 ID 的全量人工 membership Gold，因此不把 N9 决策准召伪装成
最终 cluster Pair P/R。当前最可靠的结论是：N9 局部达到目标，final Atomic 仍有 2 类错误合并和
14 个已确认的过拆 task，整体属于“precision 明显提升、recall 边缘达标且下降”。

## 8. N12 / N13 / Package 与碎片化

Package 采用与上一轮同一 2026-07-28 reviewed Gold、相同 embedding 对齐器。由于本轮 Atomic
边界变化，只有 76/168（45.24%）Atomic 达到 0.90 相似度且 best-vs-second margin≥0.01；指标只
代表高置信可迁移子集，不冒充 168 Atomic 的全量人工 Gold。

### 8.1 最终 Package Pair 与碎片化

| 同一 76-event 映射 | Precision | Recall | F1 | fragmented Gold groups | excess components | singleton components | missed links |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 上一轮 Package 投影 | 83.44% | 72.38% | 77.51% | 2 | 4 | 3 | 100 |
| 本轮最终 Package | 85.55% | 83.43% | 84.48% | 6 | 8 | 12 | 60 |
| 环比 | +2.11pp | +11.05pp | +6.96pp | +4 | +4 | +9 | -40 |

结论是“Pair 准召改善，但碎片化分布变差”：大 earnings 组链接更完整，missed pair 降低 40；
与此同时，更多小 Gold 组被拆成 singleton，碎片化 Gold 组从 2 增至 6。Package Recall 83.43%
达到 >80% 目标，Precision 85.55% 未达到 >90%。若使用上一轮独立 84-event 映射，基线为
82.31%/81.66%/81.98%；方向仍是本轮略升，但不能与 76-event 数字直接相减。

最终 92 Package，12 个多 Atomic Package，最大 Package 54 Atomic。该 earnings Package 包含
20 个 Financial Performance、21 个 Guidance、5 个 Commercial Operation、4 个 Supply、2 个
Transaction 和 2 个 Product/Science Atomic；16 个 anchor 相互冲突、`primary_anchor_id=null`。
多数确属同一 earnings disclosure，但产品背景、一般供需和部分长期事项使它仍是主要 FP 来源。

### 8.2 N12 assignment

在 74 个 Gold 可评估任务上：

- candidate relation Precision 92.86%、Recall 76.47%、F1 83.87%；
- task merge Precision 68.42%、conditional merge Recall 86.67%；
- action accuracy 79.73%；
- recorded N12 partition P/R/F1 为 85.51%/89.85%/87.63%。

N12 input 1,624,477，较上一轮 1,607,339 仅增 1.07%；考虑 Mention 增加 7.87%，payload 控制
基本守住。但 max thinking 令其 output 达 608,057、aggregate latency 4,877 秒，成本与长尾时延
显著恶化。anchor 最终仍只有 10/92 Package 有 anchor IDs、9/92 有 primary anchor，说明 anchor
留存没有转化为足够广的最终 Package 可用边界。

### 8.3 N13

45 个 pair：4 `SAME_PACKAGE`、41 `DIFFERENT_PACKAGE`。在 24 个能由迁移 Gold 判定的 pair 中，
22 个 DIFFERENT 正确、1 个应 SAME 却判 DIFFERENT；4 个 SAME 中只有 1 个可判且为错误合并，
其余 3 个因至少一侧未对齐而不可判。故不能宣称 N13 新增了任何已证实正确 join；可判 SAME 的
样本 Precision 为 0/1，但样本过小，只能定性为“没有正向证据且出现 1 个已确认 FP”。

N13 input 占比仅 2.73%，无需再优先压 input；真正回归是 max thinking 把 output/latency 分别放大
约 29.9 倍/13.1 倍。若继续使用该模型，N13 应优先降低 reasoning effort，而不是再次精简 payload。

## 9. 总结性判断

### 9.1 验收结论

| 层级 | 结论 |
| --- | --- |
| 文档技术成功 | 单文档 30/30；跨文档及端到端 28/30，较上一轮下降，**不通过** |
| Mention | 72.26% / 73.88%，Precision 明显下降、Recall 基本不动，**不通过** |
| Evidence | 100% VERIFIED、0 semantic repair、无文档扩大失败，**通过** |
| Field | 10 篇共同子集 total 82.43%，conditional 90.71%；全量被 M4 欠费阻断，**不能通过** |
| N9 | MERGE P 97.70%、conditional R 85.86%，局部达标但 Recall 环比 -5.05pp |
| Package | Pair P/R 85.55%/83.43%；Recall 达标、Precision 与碎片化不达标 |
| Token/时长 | total Token +73.73%、墙钟 +138.73%，**明确不通过** |

综合结论：**不应把当前 `M2=high、M3=max` 的 DeepSeek V4 Flash + thinking 组合提升为默认正式
配置。** 它让 N9 MERGE Precision、Evidence 和 Package Recall 改善，但没有改善 Mention，降低了
跨文档成功率与 N9 Recall，并把 output Token 放大 6.12 倍、墙钟放大 2.39 倍。

### 9.2 模型与实现的根因拆分

1. **M3 max thinking 是主要成本/可靠性风险。** M2 共 436 call、0 failure；M3 共 90 call、10 个
   `invalid_json`，失败率 11.11%。M3 平均每 call output 约 15.4k，M2 约 3.8k。Grounder、N9、N12、
   N13 的长 reasoning 没有换来与成本同量级的质量收益。
2. **两个端到端失败是本地时间合并 bug。** `merge_event_times` 组合单边 start/end 后形成
   `start > end`；其中一例 N9 语义也错，另一例 N9 正确但被实现击穿。影响 2/30 跨文档任务，
   不影响已生成 Mention，但造成 Atomic/Package assignment 不完整和 partial Apply 状态。
3. **Mention 的核心仍是候选漏召与边界守恒。** 38 个 Dreamer missing、31 个 partial；新增输出主要
   变成背景、umbrella、duplicate 或互补 fragment，而不是 strict TP。
4. **Sidecar 方向有效但不完整。** 跨 metric 大簇没有复现，N9 FP 从 8 降至 2；同 metric value
   冲突、streak/umbrella 与具体 disclosure 的边界仍不足，同时保守判定造成 14 个漏合。
5. **Anchor 覆盖仍不足。** 只有 10/92 Package 有 anchor IDs，无法广泛支撑 Package 边界；N12
   仍占 46.47% input，N13 则已经不是 input 主因。

### 9.3 保留、回退与下一步判断

- 保留 DeepSeek 官方 provider adapter、beta strict Schema compiler 和真实探针；这些修复本身有效，
  也没有放宽本地业务 Schema。
- 不建议保留 M3 `max` 作为当前默认。下一次应单独做 effort A/B，至少把 Grounder/N9/N12/N13 的
  `max` 与 `high` 或更低 effort 隔离比较；本轮按用户要求没有回滚或再改配置。
- 在再次跑 30 篇前，优先修 `merge_event_times` 的单边区间组合；这是确定性、低复杂度且直接恢复
  2 篇端到端成功率的修复。
- Mention 不应继续靠放大输出量；应针对 Dreamer missing、fragment 信息守恒、background/umbrella
  三类做窄修。还应先把 source-centered Gold 中与新 atomicity contract 冲突的复合条目单列，避免
  用错误评分激励反向合并。
- N9 保留现有 Sidecar hard boundary，不整体回滚；只针对同 metric value/streak 错并和 14 个已确认
  过拆样式做局部平衡。
- M4 账户恢复后，从已保存的 Field 分片续跑剩余 20/19 篇，才能给出全量 Field 最终数值；当前报告
  已明确保留这一缺口，不用自评或外推替代。

正式 workflow Token 不包含 Schema 探针、无效 partial run、单篇 smoke 和后续 M4 质量评估调用；
这些被单独留存，避免把测试工具成本混入模型替换本身的运行成本。
