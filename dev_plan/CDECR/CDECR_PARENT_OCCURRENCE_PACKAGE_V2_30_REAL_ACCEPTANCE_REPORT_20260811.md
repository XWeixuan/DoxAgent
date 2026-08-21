# CDECR Parent Occurrence Package V2：30 篇真实全流程验收报告

> 日期：2026-08-11  
> 实现方案：`CDECR_PARENT_OCCURRENCE_PACKAGE_V2_ONE_SHOT_REFACTOR_PLAN_20260810.md`  
> 测试范围：固定的 30 篇 `US/MU/2026-06-25` 全文语料；未运行 MU300  
> 最终结论：**技术路径通过，业务质量验收失败，不建议发布 V2。**

## 1. 结论

Parent Occurrence Package V2 已按方案完成一次性切换：旧 N12/Wave C/N13 的活跃 Package 生成路径被替换为文档内 Parent Induction、R1/R2 有界集合归一、局部 Reconcile、冻结分区 Apply；批量与增量共用同一服务、Prompt、Schema 和 reducer。30 篇真实全流程完成 30/30，最终 epoch 为 `FINALIZED`，幂等重跑新增 model call / Mention / Atomic / Package 均为 0。

但它没有达到发布门槛：

- 同一 99 个高置信 Gold 对齐 Atomic 上，最终 Package Pair Precision / Recall / F1 为 **63.78% / 36.10% / 46.10%**，门槛分别为 90% / 82% / 85%。
- 27 个 singleton Package 中，14 个可由现有 Gold 高置信判断，其中 4 个应被合并，可判漏合并比例为 **28.57%**，高于 5% 门槛。
- Micron FQ3 2026 earnings 的 40 个 Gold Atomic 被拆为 **12 个组件**，产生 496 条 missed links；碎片化是本轮最主要的回归。
- 最大 Package 仍有 79 个 Atomic，独立审计确认至少 22/79（27.8%）不属于该 earnings 父发生，远高于 5% 错误成员门槛。
- R1/R2 parent candidate coverage 分别只有 **91.59% / 87.80%**，低于 98% 门槛。

因此，按照方案“任一正式门槛失败则整体回滚”的约定，发布判断为 **FAIL**。本轮没有直接改回基线，以保留隔离 Registry、代码差异和完整失败证据；若决定结束该方案，应把代码恢复到标签 `cdecr-package-v1-baseline-20260810` 对应边界，并同步恢复 Package 数据快照，而不是只回滚 Git。

## 2. 实现范围

已完成的核心修改：

1. 新增 Parent Occurrence DTO、校验和统一协议，模型输出采用 request-local 短引用，持久层仍保存稳定 ID。
2. 新增文档内 Parent Induction：每个 Atomic 恰好进入一个文档内父发生提案；外部关联由所属 group 输出。
3. 新增有界多通道召回、R1/R2 set resolution、局部 reconcile 与 oversized review；禁止全量 `P²` pair scan。
4. 新增冻结 partition projector，最终 Package membership 只由完整、已校验的 partition Apply。
5. provider/Schema/任务失败保留 `FAILED_RETRYABLE`，不再伪造 `CREATE_NEW singleton`；恢复只重跑失败 task。
6. bulk epoch 与 incremental path 共用 Parent Occurrence 服务；阶段 checkpoint 使用稳定 epoch scope，可跨 coordinator run 恢复。
7. embedding 采用批量 executor、稳定 profile hash 缓存和局部失败处理。
8. 删除活跃 N12/Wave C/N13 Package 判定、旧 package anchor、旧 Prompt/config/contracts/tests；保留兼容读取和必要 ID 连续性。
9. 新增 SQLite v12 派生 Package 状态迁移、分区/提案/checkpoint 审计、批量写入和 stage telemetry。
10. oversized review 输入补充紧凑 Atomic facts；重叠 event 分配采用确定性唯一归属，空组被安全丢弃，不扩大成整任务失败。

主要落点：

- `src/cdecr/parent_occurrence_contracts.py`
- `src/cdecr/parent_occurrence.py`
- `src/cdecr/package_projection.py`
- `src/cdecr/bulk_epoch/package_stage.py`
- `src/cdecr/bulk_epoch/engine.py`
- `src/cdecr/cross_document.py`
- `src/cdecr/registry.py`
- `src/cdecr/prompts/v1/parent_occurrence_induction.md`
- `src/cdecr/prompts/v1/parent_occurrence_resolution.md`

## 3. 测试身份与 Provider

- 分支：`codex/cdecr-parent-occurrence-package-v2`
- 回滚标签：`cdecr-package-v1-baseline-20260810`
- 固定语料：`.tmp/cdecr/baselines/us_mu_2026-06-25.jsonl`
- 语料 SHA256：`e3ace...`（完整值保存在 manifest）
- manifest：`.tmp/cdecr/parent_v2_fixed_30_manifest.json`
- 最终隔离 Registry：`.tmp/cdecr/parent_v2_final_review_replay.sqlite3`
- bulk epoch：`bulk-epoch:8c11cd49bb287277c46bce1b`

按用户确认，本轮保持当前百炼配置，未切回 DeepSeek 官方。运行时设置复核为：

| Tier | Provider | Model | Thinking | Strict |
| --- | --- | --- | --- | --- |
| M2 | `dashscope` | `deepseek-v4-flash-0731` | `none` | true |
| M3 | `dashscope` | `deepseek-v4-flash-0731` | `low` | true |
| M4 | `dashscope` | `deepseek-v4-flash-0731` | `high` | true |

本轮没有持久化修改 `.env`。SQLite `model_calls` 记录了模型名但不保存 provider endpoint，因此“百炼”由实际加载的运行时配置确认，而不是从历史 DB 反推。

Parent Induction 和 Parent Resolution 各完成过一次真实百炼 Schema probe，结构化响应可通过当前协议；真实全流程中两类节点也均产生成功调用。若 provider 不支持 function strict，现有客户端保持 JSON object 的非阻塞兼容路径，不把技术协议差异扩大成文档失败。

## 4. 真实全流程结果

| 项目 | 最终结果 | 判定 |
| --- | ---: | --- |
| 单文档成功 | 30/30 | 通过 |
| 跨文档成功 | 30/30 | 通过 |
| Mention | 349 | 记录 |
| Atomic | 281 | 记录 |
| Package | 75 | 记录 |
| active membership | 281 | 完整 |
| Package external relation | 43 | 记录 |
| 未分包 Atomic | 0 | 通过 |
| valid Mention schema | 100% | 通过 |
| valid Evidence span | 100% | 通过 |
| epoch | `FINALIZED` | 通过 |
| 幂等重跑增量 | 0 call / 0 Mention / 0 Atomic / 0 Package | 通过 |

停电后的恢复没有从头重跑：成功的 30 个 induction checkpoint、R1/R2 和 reconcile checkpoint 均被复用。最终重放只执行 reducer/projector，Package stage 为 1.489 秒、provider 调用 0、embedding cache 107 hit / 0 miss、Apply 6 chunks，且无 degraded/retry。

这 1.489 秒只能证明恢复和幂等，不能当作一次完整 Package 生成的真实墙钟。

## 5. Package 准召与碎片化

Gold 评估只覆盖 281 个当前 Atomic 中 99 个可高置信跨轮对齐项，覆盖率为 35.23%。因此下面的 P/R/F1 是可审计子集指标，不代表未对齐开放世界事实；但差距足够大，验收结论不受这一限制影响。

| 口径（同 99 Atomic） | Precision | Recall | F1 | FP | FN |
| --- | ---: | ---: | ---: | ---: | ---: |
| 上一实际 Package 投影 | 81.22% | 77.23% | 79.17% | 142 | 181 |
| V2 67-Package 中间态 | 61.88% | 52.08% | 56.56% | 255 | 381 |
| V2 最终 75-Package | **63.78%** | **36.10%** | **46.10%** | **163** | **508** |

最终 review 相对 67-Package 中间态减少了 92 个 FP pair，但新增 127 个 FN pair，Precision 只上升 1.90 个百分点，Recall 下降 15.97 个百分点，F1 再下降 10.45 个百分点。它不是“安全拆分”，而是用显著碎片化换取了有限 Precision。

主要碎片化 bad cases：

- `G_MICRON_FQ3_2026_EARNINGS`：40 个 Gold events 被拆为 12 个组件，大小为 `[24,4,2,2,1,1,1,1,1,1,1,1]`，missed links=496。
- MU 同日 price move：被拆进 3 个 Package。
- Sandisk Citi price-target/reaction：被拆为 `package:08ada823...` 与 `package:2210c6af...`。
- MU YTD performance：被拆为 `package:8c14ee73...` 与 `package:7c7ea391...`。
- KOSPI June 25：被拆为 3 个 Package。
- memory supplier reallocation、MU forward P/E 仍有明确跨包漏合并。

### 5.1 singleton

| 指标 | 结果 |
| --- | ---: |
| Package singleton | 27/75 = 36.00% |
| Gold 可判 Package singleton | 14/27 |
| 可判且应合并 | 4/14 = **28.57%** |
| 全体保守下限 | 4/27 = 14.81% |
| Atomic singleton | 254/281 = 90.39% |
| Gold 可判 Atomic singleton | 83/254 |
| 可判且应合并 | 33/83 = 39.76% |

四个高置信漏合并 singleton Package 分别是 Sandisk Citi reaction、Micron SCA introduction、MU Thursday +11.5% reaction、MU YTD +233.45%；均能指向现有目标 Package。

Atomic singleton 是上游 N9 控制项，不归因于 Package V2。最大 Atomic 漏合并组包括 gross-margin 7-way、MU price-move 4-way、supply-demand 3-way；本轮没有用 Package 改善掩盖这一问题。

### 5.2 超大簇与反向控制

最终 Package size 分布：

```text
1x27, 2x16, 3x15, 4x4, 5x5, 6x4, 7x1, 12x1, 14x1, 79x1
```

独立审计结论：

- `package:4723fa8c...` 有 79 个 Atomic，是唯一 top-1% 超大簇。Gold 对齐子集中 5/29（17.2%）为异类；全量保守人工审计至少 22/79（27.8%）不属于 Micron earnings 父发生，包括 market reaction、valuation、SCA、行业/产品叙事。
- 该簇从中间态 96 降到 79，但移出的 18 个成员同时包含错误项和真正 earnings 事实；拆分没有稳定遵循父发生边界。
- size 14 的 global AI selloff/recovery 与 size 12 的 Wedbush/Ives commentary 基本合理。
- 新增多个小型错包：Q4 gross-margin guide 与 market-cap/stock reaction 混包；sequential-revenue guidance、RSI reaction 与 Q3 gross margin 混包；pre-market reaction、net income、SCA 与 earnings time 混包。
- 最大错误 Atomic `atomic:654d1d...` 仍为 16 mentions，其中至少 6 个不是同一 FQ3 revenue 最小事实；这是上游 N9 问题，不归因于 Package V2。

## 6. Parent 候选与失败语义

| 阶段 | candidate coverage | 门槛 | 判定 |
| --- | ---: | ---: | --- |
| R1 | 91.59% | >=98% | 失败 |
| R2 | 87.80% | >=98% | 失败 |
| reconcile | 100% | 参考 | 通过但覆盖范围较小 |

R1/R2 的 proposal 数分别为 98 和 72；reconcile 只覆盖 21 个残余 proposal，不能弥补前两轮召回图缺口。当前 telemetry 未持久化各通道 candidate purity 和 R2 后候选图连通率，这两项只能判定为“未报告”，不能宣称通过。

最终 epoch 中：

- induction 30、R1 5、R2 5、reconcile 2 个 checkpoint 全部 `SUCCEEDED`；
- failure-created singleton=0；
- resolution waves=3；
- snapshot query=2，pair Registry read=0；
- Apply 完整且无局部失败。

历史隔离库仍保留停电前/恢复过程的 `FAILED_RETRYABLE` 和失败调用，用于审计，但它们不属于最终 epoch 的未完成任务。

## 7. Token、调用与墙钟口径

由于停电、分阶段恢复和两次 isolated review replay，最终 Registry 的累计 `model_calls` 混合了原始尝试、失败、repair、恢复和质量实验，不能把累计值冒充“一次稳态运行成本”。

最终报告中的全工作流累计口径为：

- 1,131 calls；
- input 2,557,172；output 565,104；
- 累计 provider latency 8,183,638 ms。

其中 Parent 阶段累计成功/失败与 token 如下；这仍是审计累计值：

| Stage | 调用 | Input | Output | 累计 latency |
| --- | ---: | ---: | ---: | ---: |
| induction success | 42 | 501,397 | 62,877 | 946.641s |
| induction failed | 1 | 15,680 | 1,732 | 16.362s |
| induction repair | 6 | 118,486 | 9,387 | 120.306s |
| R1 success | 5 | 43,620 | 3,495 | 52.854s |
| R1 failed | 3 | 无可靠 token | 无可靠 token | 232.642s |
| R2 success | 10 | 74,190 | 7,475 | 104.920s |
| reconcile | 19 | 274,373 | 10,856 | 171.718s |
| reconcile repair | 4 | 85,541 | 2,191 | 22.893s |
| M1 parent embedding success | 29 | 38,117 | 0 | 22.747s |

最后一次有真实模型调用的成功恢复段只补齐 R2/reconcile/M1：18 calls、81,790 input、6,157 output、99.533 秒累计 latency；它没有重跑 induction/R1，不能代表完整 Parent 成本。最终 checkpoint replay 为 0 call / 0 token。

因此，`Package stage <=356k token` 和完整 Package 墙钟门槛当前为 **不可判定**，而非通过。结构性性能门槛中，无 `P²`、最多 3 waves、无逐 pair Registry read、幂等 0 增量均已通过。

## 8. 根因判断

本轮失败不是 provider 402 或文档失败造成的；最终所有 Parent task 均成功，失败来自业务聚合本身。

1. **R1/R2 候选覆盖不足。** R2 coverage 反而低于 R1，未形成方案预期的全局二次补召回。
2. **集合级 resolver 同时存在过合并与过拆。** 常规 R1/R2 容易把 earnings、reaction、SCA、valuation 和行业叙事收进同一大父发生；oversized review 又不能稳定把 Atomic 按父边界拆开。
3. **review 协议对模型仍然困难。** 即使输入了 request-local event refs 和 compact Atomic facts，模型仍会输出 proposal/event 重叠或把正确 earnings facts列为例外。运行时可以非阻塞地确定唯一归属，却不能替模型恢复正确语义。
4. **“default parent + explicit exceptions”压缩协议有系统性偏置。** 它适合少量明确例外，不适合已经混入多类父发生的 79/96-member 污染簇；强制严格格式又曾导致整 task invalid，因此没有继续增加阻塞校验。
5. **Atomic 上游质量放大了 Package 难度。** 281 Atomic 中 254 个 singleton，且 revenue、gross margin、price move 等仍有 Atomic 层错并/漏并。Package V2 不应通过跨 Atomic 粗合并来修补 N9。

继续在当前架构上叠加 hard conflict、更多 anchor 层级或 pair repair，既违反方案的低复杂度约束，也很可能继续在 Precision/Recall 间摆动。因此不建议以窄补丁推进发布。

## 9. Prompt 变更全文

### 9.1 Parent Induction

```text
An Atomic is one minimal fact. A parent occurrence is the bounded real-world occurrence or process that contains one or more Atomics. A continuing matter is the same identifiable matter persisting across reports. Neither is an article, entity, topic, or mere shared context.

For each document, partition all supplied Atomics by parent identity. Use the full article and Atomic evidence to identify the occurrence or matter containing each fact. The parent need not be named verbatim, but it must be supported by the article. Group different child facts when they belong to the same parent. Separate reactions, consequences, independent reports, and distinct occurrences; link them externally when supported.
Place each external link in the group containing its source Atomic.

Work in this order:
1. Identify candidate parents in the article.
2. Compare artifact, participants, object, time, and event context.
3. Assign every Atomic exactly once.
4. Check that each group shares one bounded parent, not only an entity or topic.

A one-Atomic group is valid only when no other supplied Atomic belongs to its parent. Missing detail is not a reason to isolate it. Output only the schema.
```

### 9.2 Parent Resolution

```text
A proposal is one document's candidate parent. A prototype is an existing or provisional parent. Two items share a parent only when they identify the same bounded occurrence or continuing matter; they may contain different child facts. A shared entity, source, family, time, or topic alone is insufficient.

Partition the supplied proposals and prototypes by parent identity.

Work in this order:
1. Check Atomic overlap and explicit artifact identity.
2. Compare participants, object, time or period, family, and representative facts.
3. Reuse or combine prototypes only when the combined evidence identifies one parent.
4. Check that every group has one parent boundary.

Assign every proposal exactly once. Each prototype may appear at most once. Leave `event_refs` empty and `includes_remaining_events=false` unless the review instruction requires an Atomic partition. A one-proposal group means a distinct parent, not uncertainty or missing detail. Output only the schema.
```

### 9.3 Oversized review 追加语句

```text
Review mode uses one default parent plus explicit exceptions. Put every proposal_ref on the single includes_remaining_events group; all other groups use proposal_refs=[] and list their event_refs. A disclosure, its market or analyst reaction, a separate contract, and a broad industry or valuation theme are different parents. The default receives every unlisted event_ref.
```

该追加语句在最终结果中未达到预期：它把最大簇从 96 缩至 79，但同时把正确 earnings facts 拆出，并把 Gold earnings 组件从 9 个增加到 12 个。

## 10. 自动化与独立验收

- CDECR 全量回归：**263 passed, 3 skipped**。
- Ruff：通过。
- mypy（Parent Occurrence 核心模块）：通过。
- `git diff --check -- ... src/cdecr tests/cdecr`：通过，仅有既有 CRLF 提示。
- 两个真实 Parent Schema probe：通过。
- 三名独立 `gpt-5.6-terra` medium Agent 分别完成 singleton、oversized cluster、runtime/checkpoint 只读复核；最终指标已合并到本报告。
- 未运行真实 MU300，符合本轮明确限制。

## 11. 产物

- 最终 Registry：`.tmp/cdecr/parent_v2_final_review_replay.sqlite3`
- 30 篇运行报告：`.tmp/cdecr/parent_v2_final_review_report.json`
- Package Gold：`.tmp/cdecr/parent_v2_final_package_gold_eval.json`
- 机器可读层级：`.tmp/cdecr/parent_v2_final_clusters.json`
- 人类可读 Package -> Atomic -> Mention：`.tmp/cdecr/parent_v2_final_hierarchy.md`
- 原始成功全流程 Registry 备份：`D:/DoxAgent_CDECR_Acceptance_20260811_ParentOccurrenceV2_R1/`
- 最终无覆盖归档：`D:/DoxAgent_CDECR_Acceptance_20260811_ParentOccurrenceV2_Final/`

## 12. 最终建议

1. 不把 Parent Occurrence Package V2 合入生产或用于 MU300。
2. 保留当前分支、隔离 Registry 和报告用于设计复盘；不要继续对该失败版本做局部线上开关。
3. 若执行方案约定的整体回滚，应同时恢复代码和 Package 数据快照。
4. 下一轮若重做，应先在冻结 Atomic 上建立可审计的一次性 Package-only harness，确保每次投影使用同一 partition/Gold，并在正式 30 篇前先达到 P/R/F1、singleton 和 oversized 四项联合门槛。
5. N9 的 Atomic fragmentation/污染应单独处理，不要让 Package 层承担修复 Atomic 身份的职责。
