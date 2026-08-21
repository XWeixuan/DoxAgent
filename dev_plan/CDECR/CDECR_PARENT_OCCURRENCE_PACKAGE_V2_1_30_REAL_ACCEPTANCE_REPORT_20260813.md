# CDECR Parent Occurrence Package V2.1：30 篇真实全流程验收报告

> 验收日期：2026-08-13（Asia/Shanghai）  
> 本轮方案：`CDECR_PARENT_OCCURRENCE_PACKAGE_V2_1_ONE_SHOT_QUALITY_EFFICIENCY_REFACTOR_PLAN_20260813.md`  
> **唯一主比较基线**：`CDECR_PARENT_OCCURRENCE_PACKAGE_V2_30_REAL_ACCEPTANCE_REPORT_20260811.md`  
> 当前运行：修正后的干净 V2.1 R3  
> 边界：真实 DashScope/百炼；固定 30 篇 MU；Relevance Gate=`enforce`；未运行 MU300；按用户要求不执行回滚

## 1. 结论先行

本报告的主问题是：**V2.1 相比 2026-08-11 正式 V2 验收，到底改善了什么、回归了什么。** 同日 R2 仅是 V2.1 修复过程中的中间运行，不作为主比较基线。

V2.1 已完成代码重构并跑通一次新的、干净 Registry 的真实 30 篇全流程。30/30 文档成功，epoch `FINALIZED`，幂等复验增量为 0；273/273 Mention 均进入 active Atomic；百炼 Provider 和 Relevance Enforce 均真实生效。

但相对正式 V2，V2.1 的最终质量结论仍是 **FAIL**：

- **误合并显著下降**：严格共同 50 个 Gold Atomic 上，Precision 从 **73.23% 提升至 97.56%**，+24.33pp；正式 V2 的 79-Atomic 污染超大簇消失，V2.1 最大 Package 为 29，未发现同等级 supercluster。
- **聚合召回显著回归**：同一严格共同 50 项上，Recall 从 **44.08% 降至 18.96%**，-25.12pp；F1 从 **55.03% 降至 31.75%**，-23.28pp。
- **Package 碎片化大幅恶化**：Package 从 75 增至 119；Package/Atomic 从 26.69% 增至 61.98%；singleton 从 27/75（36.00%）增至 88/119（73.95%）。
- **可判 singleton 漏合并恶化**：正式 V2 为 4/14（28.57%），V2.1 为 13/29（44.83%），数量 +9、比例 +16.26pp。
- **Micron earnings 继续恶化**：正式报告中 40 个 Gold Atomic 被拆为 12 个组件、496 missed links；V2.1 自身高置信对齐的 37 项被拆成 15 个组件、533 missed links。严格共同 21 项下，组件从 6 增至 10，missed links 从 117 增至 170。
- **成本显著降低**：全流程累计 input/output 相对正式 V2 下降 32.18%/23.30%；Parent input 从 1,151,404 降至 487,524，下降 57.66%。不过正式 V2 是停电、恢复和 replay 混合累计口径，故只能确认方向，不能把降幅冒充严格稳态 A/B。
- **V2.1 自身效能仍有未过项**：Parent total token 527,404，高于 500k；repair/reconcile 占 19.82%，高于 15%；完整 clean wall 为 21.34 分钟。
- **候选与拆分机制仍失败**：R1∪R2 累计 lineage coverage 只有 57.58%；最终 33 次 boundary split 将 19 个父组拆成 52 个子组，其中 42 个 singleton。

最终判断：**V2.1 相比正式 V2，成功解决了最严重的超大簇污染并显著降低模型成本，但代价是 Package 聚合能力和碎片化进一步恶化，整体 F1 下降，不能进入 MU300 或发布。** 按原方案 Gate 应执行整体回滚；本轮遵照用户指示，仅评估，不执行回滚。

## 2. 主基线与可比性

### 2.1 正式 V2 基线

本报告只把以下文档作为主基线：

`dev_plan/CDECR/CDECR_PARENT_OCCURRENCE_PACKAGE_V2_30_REAL_ACCEPTANCE_REPORT_20260811.md`

正式 V2 的关键冻结指标：

| 指标 | 正式 V2 |
| --- | ---: |
| 文档成功 | 30/30 |
| Mention | 349 |
| Atomic | 281 |
| Package | 75 |
| active membership | 281 |
| Package singleton | 27/75 = 36.00% |
| Atomic singleton | 254/281 = 90.39% |
| 最大 Package | 79 Atomic |
| Gold accepted alignment | 99/281 = 35.23% |
| Package P/R/F1 | 63.78% / 36.10% / 46.10% |
| Micron earnings | 40 items / 12 components / 496 missed links |
| 全流程累计 calls | 1,131 |
| 全流程累计 input/output | 2,557,172 / 565,104 |
| 累计 provider latency | 8,183,638 ms |

### 2.2 三种比较口径

为了避免再次混淆，质量比较分三层：

1. **严格共同 Gold**：同一批 Atomic 同时存在于 V2 与 V2.1，用于主 P/R/F1 A/B；这是最可比的质量结论。
2. **各自高置信对齐子集**：V2 为 99 项，V2.1 为 73 项；用于描述各轮冻结结果，但不能直接将差值归因于重构。
3. **全量结构指标**：Package/Atomic、singleton、最大簇、Mention active coverage；它们反映真实最终交付物，但受到 Relevance Enforce 和上游模型波动影响。

成本方面，V2 累计 Registry 混合停电、失败、repair、恢复和 isolated replay；V2.1 R3 是干净首跑。因此 token 可用于确认大方向，wall 不做严格跨轮倍数结论。

## 3. V2.1 实施范围

### 3.1 Parent V2.1 主路径

1. 新增统一 Parent boundary signal compiler，批量编译 issuer、artifact、institution、counterparty、market scope、metric、object、period、coarse role 与三态 compatibility。
2. Document-local Induction 改为共享 `document_context` + `evidence_refs`，删除重复全文；模型不再生成可确定性派生的 `package_family`。
3. Induction 后执行局部纯度扫描；仅可疑 group 进入相同 Schema 的局部 repartition，长文可疑项允许一次受限宽窗口升级。
4. R1/R2 使用结构化多路召回、语义补充、hard negative、weighted microcomponent 和 bridge ledger；R2 coverage 以 R1∪R2 计算。
5. 删除旧 oversized review 和第三个常规模型波次；最终冲突只做局部 reconcile。
6. route quota、total K、context/token budget 配置化；结构化索引改为倒排查找，route 内先评分后截断。
7. Resolution 调用前做确定性 token 估计与超预算拆分。
8. Prompt/schema/compiler/engine identity 全部升级，防止误复用旧 V2 checkpoint。

### 3.2 正式验收前补齐的实现缺陷

- Atomic Apply 在每次读写前解析 redirect root，避免 Mention 继续写入 inactive source；本轮 active coverage 为 273/273。
- N9_LATE 恢复会闭合遗留 task ledger 并恢复冻结 artifact，不重跑上游模型。
- 显式 Source artifact 仅接受 filing/announcement URL 或明确 parent message，普通新闻 source 不再伪装成 artifact。
- coarse role 增加 predicate/counterparty 证据；新增受限 source/family recall hints，但不允许 source/topic 单独 admission。
- 最终 Package 做全量 deterministic boundary scan，只把确定冲突拆开，不把 UNKNOWN 当冲突。

### 3.3 保留边界

- 未开启 N13 pair-local Apply，未恢复全局 pair scan。
- 未新增第三套 repair Schema 或 LLM reasoning 字段。
- 未增加测试集公司、数值或 bad-case 专用规则。
- M2/M3/M4 保持百炼 `deepseek-v4-flash-0731`，未切回 DeepSeek 官方。
- 按用户要求直接完成真实 30 篇，但未运行真实 MU300。

## 4. 相关性与全流程完整性

### 4.1 Relevance Enforce

| 指标 | V2.1 R3 |
| --- | ---: |
| Gate audit | 30/30 |
| mode | `enforce` |
| Gate 前 candidate | 423 |
| Gate 后 candidate | 354 |
| 明确 `IRRELEVANT` 并删除 | 69 |
| fail-open | 0 |
| 持久化 candidate | 354 |
| Grounder candidate | 354 |
| 逐文档不一致 | 0 |

69 个明确不相关候选在 Grounder 前删除，未进入持久化候选或后续流程。正式 V2 运行时没有同口径 Enforce，因此 Mention/Atomic 数量变化不能全部归因于 Parent V2.1。

### 4.2 成功率与数据完整性

| 指标 | 正式 V2 | V2.1 R3 | 变化 |
| --- | ---: | ---: | ---: |
| 文档成功 | 30/30 | 30/30 | 持平 |
| Epoch | FINALIZED | FINALIZED | 持平 |
| Mention | 349 | 273 | -76 / -21.78% |
| active Atomic | 281 | 192 | -89 / -31.67% |
| Package | 75 | 119 | +44 / +58.67% |
| Package / Atomic | 26.69% | 61.98% | +35.29pp |
| active membership | 281 | 192 | 与 Atomic 一致 |
| 未分包 Atomic | 0 | 0 | 持平 |
| active Mention 缺失 | 0 | 0 | 持平 |
| external relations | 43 | 19 | -24 |

V2.1 的 Atomic 数比 V2 少 31.67%，Package 却多 58.67%；这已经排除“只是上游产出变少”的解释，说明 Parent 聚合率本身显著下降。

### 4.3 Task ledger 与幂等

V2.1 R3 的 809 个 Bulk task 全部 `SUCCEEDED`：FIELD 521、N9 273、ATOMIC_APPLY 14、N9_LATE 1。幂等复验 model call、Mention、Atomic、Package delta 全为 0。

## 5. 主质量 A/B：正式 V2 → V2.1 R3

### 5.1 严格共同 Gold 50 项

这是本报告最重要的准召比较：

| 运行 | Precision | Recall | F1 |
| --- | ---: | ---: | ---: |
| 正式 V2 | 73.23% | 44.08% | 55.03% |
| V2.1 R3 | 97.56% | 18.96% | 31.75% |
| 变化 | **+24.33pp** | **-25.12pp** | **-23.28pp** |

V2.1 没有通过“平衡地改善聚合”的目标。它把错误形态从过度合并大幅推向过度拆分：Precision 的提升是真实的，但 Recall 和 F1 的损失更大。

### 5.2 各自冻结子集（非严格 A/B）

| 运行 | Gold覆盖 | TP | FP | FN | Precision | Recall | F1 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 正式 V2 | 99/281 | 287 | 163 | 508 | 63.78% | 36.10% | 46.10% |
| V2.1 R3 | 73/192 | 134 | 1 | 539 | 99.26% | 19.91% | 33.17% |

两行对齐集合不同，只用于刻画各自冻结结果。它与严格共同 50 项结论一致：V2.1 几乎消除了 Gold 可见的 false merge，但 missed merge 仍极多。

## 6. 碎片化与反向控制

### 6.1 Singleton

| 指标 | 正式 V2 | V2.1 R3 | 变化 |
| --- | ---: | ---: | ---: |
| Package singleton | 27/75 = 36.00% | 88/119 = 73.95% | +61个 / +37.95pp |
| Gold 可判 singleton | 14 | 29 | +15 |
| 可判且应合并 | 4/14 = 28.57% | 13/29 = 44.83% | +9个 / +16.26pp |
| 全体保守漏合并下限 | 4/27 = 14.81% | 13/88 = 14.77% | 比例近似，绝对数+9 |
| Atomic singleton | 254/281 = 90.39% | 159/192 = 82.81% | -7.58pp |

全体保守下限比例相近，是因为 V2.1 的不可判 singleton 数量大幅增加；不能用它掩盖 Gold 可判漏合并数量从 4 增至 13、可判比例从 28.57% 升至 44.83%。

明确漏合并包括：Micron earnings 内投资、现金回报、股息、gross margin、revenue growth、供需表述；约 16% 的盘前/盘后反应；supplier reallocation；forward P/E；SK Hynix Nasdaq listing。

### 6.2 Micron FQ3 earnings

| 口径 | 正式 V2 | V2.1 R3 |
| --- | ---: | ---: |
| 各自冻结 Gold | 40 items / 12 components / 496 missed | 37 items / 15 components / 533 missed |
| 严格共同 21 items | 6 components / 117 missed | 10 components / 170 missed |

V2.1 没有修复正式 V2 已确认的核心碎片化，反而在严格共同集上增加 4 个组件和 53 条 missed links。

### 6.3 超大簇与错误合并

| 指标 | 正式 V2 | V2.1 R3 |
| --- | ---: | ---: |
| 最大 Package | 79 | 29 |
| 最大簇明确误成员 | 至少22/79 = 27.8% | 高置信16项均属 earnings，未见同等级 intruder |
| Gold false-merge pair | 163 FP（99项子集） | 1 FP（73项子集） |

这是 V2.1 最明确的成功：正式 V2 的 79-Atomic earnings supercluster 已消失，最大 29-Atomic Package 未发现同等级污染。

但仍有较小错包：

- 一个 4-member Micron market Package 混合年度涨幅、单日市值增加、盘后涨幅和笼统 Thursday surge；至少 2/4 属于不同 occurrence。
- 一个 3-member Apple market Package 混合 intraday -0.56% 与 close -5%/-6.12%。
- Gold 对齐确认“memory/storage prices quadrupled”与“supplier production redirect to HBM”被错误并包，产生 1 FP pair。

因此，V2.1 解决了 supercluster，但没有完全解决 market occurrence 与 source/context 捷径。

## 7. 候选覆盖与 final split 根因

### 7.1 正式 V2 与 V2.1 coverage 不能直接做数值 A/B

正式 V2 报告记录 R1/R2 coverage 为 91.59%/87.80%，但旧指标定义与 V2.1 的 lineage coverage 不同。V2.1 修正后的口径是“有至少一个合格邻居的 lineage / lineage universe”。因此不能写成从 91.59% 降至 57.58%的严格回归；两轮共同结论只能是：**均未达到 98%，且 R2 都没有完成充分补召回。**

V2.1 真值：

| Wave | universe | qualified | no qualified | eligible/selected edges | pair eval |
| --- | ---: | ---: | ---: | ---: | ---: |
| R1 | 132 | 74 | 58 | 274/274 | 13,696 |
| R2 本轮局部 | 132 | 19 | 74 | 49/49 | 6,698 |
| R1∪R2 累计 | 132 | 76 | 56 | — | — |

R1=56.06%，累计=57.58%；R2 只新增 2 个 lineage。两轮共进行 20,394 次 pair evaluation，当前规模已接近 P²，不能据此宣称 MU300 近线性扩展性通过。

### 7.2 Final boundary split

V2.1 最终 119 个 Package 内 deterministic incompatible pair=0，说明 Parent hard-boundary purity 达成；但代价是：

- final boundary split=33；
- 19 个父组被拆成 52 个子组；
- 其中 42 个子组为 singleton；
- 同一 Micron earnings disclosure 被拆为 `29+2+1×6`；earnings-call commentary 被拆为 `2+1×6`；Q4 guidance 被拆为 `5+1+1`。

正式 V2 的主要失败是污染大簇，V2.1 的主要失败则是 **候选只覆盖 57.58% + final purity 过度拆分**。这解释了为什么 Precision 大幅提升而 Recall/F1 和 singleton 同时恶化。

## 8. 成本与墙钟：正式 V2 → V2.1 R3

### 8.1 全流程累计方向

| 指标 | 正式 V2累计 | V2.1 R3 clean | 变化 |
| --- | ---: | ---: | ---: |
| calls | 1,131 | 981 | -150 / -13.26% |
| input | 2,557,172 | 1,734,241 | -822,931 / -32.18% |
| output | 565,104 | 433,442 | -131,662 / -23.30% |
| total token | 3,122,276 | 2,167,683 | -954,593 / -30.57% |
| provider latency sum | 8,183,638 ms | 5,280,872 ms | -35.47% |

正式 V2 的累计值受停电、repair、恢复和 replay 污染，V2.1 R3 是 clean run。因此这些降幅证明效能方向显著改善，但不等于严格同条件稳态收益。

正式 V2 报告没有可用的 clean 完整 wall；V2.1 R3 的真实完整 wall 为 21.34 分钟。不能用 V2 最终 1.489 秒 checkpoint replay 与其比较。

### 8.2 Parent stage

| 指标 | 正式 V2累计 | V2.1 R3 | 变化 / 判定 |
| --- | ---: | ---: | --- |
| calls | 120 | 85 | -29.17% |
| input | 1,151,404 | 487,524 | -57.66%，通过相对门槛 |
| output | 98,013 | 39,880 | -59.31% |
| total token | 1,249,417 | 527,404 | -57.79%，但高于500k |
| Package-stage wall | 不可判 | 249.5s / 4.16min | V2.1绝对门槛通过 |

V2.1 Parent 占全流程 total token 24.33%。显式 repair+reconcile 为 104,508 token，占 Parent 19.82%，未过 15%；若计入 repartition 主请求，则为 36.87%。

### 8.3 V2.1 主要节点占比

| Stage | calls | Input | Output | Total占比 | latency占比 |
| --- | ---: | ---: | ---: | ---: | ---: |
| atomic_coreference | 64 | 258,550 | 85,545 | 15.87% | 14.50% |
| field_coreference | 283 | 263,864 | 10,728 | 12.67% | 7.13% |
| grounder | 30 | 123,713 | 117,382 | 11.12% | 23.44% |
| parent_induction | 17 | 194,704 | 24,021 | 10.09% | 4.41% |
| judge | 30 | 181,912 | 16,382 | 9.15% | 3.82% |
| dreamer_relevance | 30 | 123,787 | 46,983 | 7.88% | 10.76% |
| grounder_missing_recovery | 12 | 50,424 | 45,559 | 4.43% | 8.20% |
| parent_induction_repartition | 12 | 82,283 | 7,666 | 4.15% | 2.75% |
| parent_induction_repair | 5 | 70,791 | 4,639 | 3.48% | 0.99% |
| dreamer | 30 | 31,229 | 34,361 | 3.03% | 6.72% |
| atomic_coreference_escalation | 11 | 46,200 | 13,863 | 2.77% | 7.77% |
| parent_resolution_r1 | 17 | 46,640 | 1,427 | 2.22% | 0.68% |

其余单 stage token 占比均低于 2.3%。Parent 的主要消耗来自 Induction/repartition/repair，不是 Resolution R1/R2。

## 9. 失败、降级与上游约束

V2.1 R3 有 86 条失败 model-call 记录：50 次 `ValueError`、33 次 `invalid_json`、3 次 `provider_arrearage`。它们均被局部处理，未造成文档失败。

50 次 `ValueError` 全部来自 M1 Atomic embedding/recall：运行时仍会先发送 64/32 条超 provider 约束的 batch，再 fallback。这是独立于 Parent V2.1 的现存效能缺口。

上游 Atomic 仍存在污染：18-member revenue Atomic 混入不同季度 revenue 与 umbrella results；8-member gross-margin Atomic 混入 EPS；7-member earnings Atomic 混合 umbrella/EPS/net income；4-member analyst Atomic 混合 Mizuho 与 Needham。正式 V2 与 V2.1 都受上游 N9 质量影响，Package 层不应以粗合并替代 Atomic 修复。

## 10. Prompt 与 Schema 对齐

### 10.1 最终 Induction Prompt

```text
A parent is a bounded real-world occurrence, process, or identifiable continuing matter. It is not an article, entity, topic, period, or shared background.

Partition every supplied Atomic exactly once. A disclosure and facts the evidence identifies as content of that disclosure may share one parent. A market or analyst reaction, a separate agreement or transaction, an independent report, and background or industry context are different parents even when the article discusses them together. A statement belongs to a call, filing, or release only when its evidence identifies it as content of that disclosure.

Use event family, participants, object, artifact/report, metric, and normalized time as identity evidence. Shared issuer, period, family, source, or topic alone is insufficient.

Link separate parents only when the evidence supports the relation. A one-Atomic group is valid only when no other supplied Atomic belongs to its parent. Assign every Atomic once and output only the schema.
```

局部 repartition 只追加：

```text
Repartition only these Atomics. They came from one group with incompatible parent boundaries. Keep every ref exactly once; do not force different occurrences back together.
```

### 10.2 最终 Resolution Prompt

```text
A proposal is one document-local candidate parent; a prototype is a provisional or existing parent. Partition them by the same bounded occurrence or continuing matter.

Merge only when the combined evidence identifies one parent-specific bridge, such as the same artifact, report, agreement, or market session, or an equivalent occurrence description with compatible role, participants, object, and time. Shared issuer, period, family, source, or topic alone is insufficient. Different child facts or values may share a parent when they are content of the same occurrence.

A disclosure, its market or analyst reaction, a separate transaction, an independent report, and background context remain different parents. Assign every proposal exactly once; use each prototype at most once; output only the schema.
```

Prompt 与 payload 字段一致，模型只承担 parent partition，不承担候选召回、family 派生或额外 reasoning。当前失败证据主要指向 cue known-rate、candidate admission 与 final split，不是 Prompt 未解释新增字段。

## 11. 测试与独立审计

| 检查 | 结果 |
| --- | --- |
| CDECR tests | 276 passed, 3 skipped |
| Ruff | passed |
| strict mypy（核心文件） | passed |
| diff check | passed；仅 LF/CRLF warning |
| redirect source 后续写入 | passed |
| explicit IRRELEVANT 不进入下游 | passed |
| task ledger/idempotency | passed |

独立质量 Agent 与运行时 Agent 均只读审查 V2.1 R3 Registry、Gold、层级和运行时记录。二者共同结论是：运行、百炼、Relevance、幂等、Mention active coverage 与 Parent 最终 deterministic purity 通过；相对正式 V2，supercluster/Precision 与 token 改善，但 Recall、F1、singleton 和 fragmentation 未通过。

## 12. Gate 判定

| Gate | 要求 | 正式 V2 | V2.1 R3 | V2.1判定 |
| --- | ---: | ---: | ---: | --- |
| 文档成功 | 30/30 | 30/30 | 30/30 | 通过 |
| Package | ≤100 | 75 | 119 | 失败 |
| Package/Atomic | ≤35% | 26.69% | 61.98% | 失败 |
| strict-common Pair Recall | 不显著回归 | 44.08% | 18.96% | 失败 |
| strict-common Pair F1 | 不显著回归 | 55.03% | 31.75% | 失败 |
| judgeable singleton漏合并 | ≤6且比例可控 | 4/14 | 13/29 | 失败 |
| 超大簇误成员 | ≤10% | ≥27.8% | 最大簇未见同等级污染 | 改善/通过 |
| cumulative coverage | ≥98% | 旧口径91.59/87.80 | 新口径57.58% | 失败 |
| Parent input相对V2 | 降低≥45% | 基线 | -57.66% | 通过 |
| Parent total token | ≤500k | 不可判 | 527,404 | 失败 |
| repair/reconcile占比 | ≤15% | 不可判 | 19.82% | 失败 |
| Parent clean wall | ≤15min | 不可判 | 4.16min | 通过 |
| resolution waves | 2 | 3 | 2 | 通过 |
| oversized review | 0 | >0 | 0 | 通过 |

Parent Package 内 deterministic incompatible pair=0，但全工作流 Atomic 层仍有 170 个 hard-cannot-link violation，不能把 Parent 纯度扩大宣称成全工作流 hard boundary 通过。

## 13. 最终判断

以正式 V2 为唯一主基线，V2.1 的净效果是：

```text
明显减少 supercluster 与 FP
+ 明显降低 input/output/token latency
- Package 数和 singleton 大幅增加
- 严格共同集 Recall/F1 显著下降
- Micron earnings 更碎
= 工程路径可运行，但业务质量不通过
```

如果严格执行 V2.1 方案停止闸门，当前已经满足整体回滚条件。不过用户明确要求本轮无论结果如何暂不回滚，因此本轮：

- 保留代码和冻结数据；
- 不把 V2.1 宣称为验收通过；
- 不进入真实 MU300；
- 不通过放松 hard boundary、扩大 batch 或增加 bad-case 专用 Prompt 即时追指标；
- 后续若继续，必须先在冻结 Package-only proposal 上解决真实 candidate recall 与 final split 局部化，再与本报告的正式 V2 基线进行同集复验。

## 14. R2 辅助诊断（非主基线）

R2 只用于确认修正代码从同日中间态到 R3 是否产生局部作用，不参与主发布结论。严格共同 44 项上，R3 相比 R2 Recall +7.58pp、F1 +10.69pp；这说明本轮补召回代码并非完全无效。但该小幅改善没有改变“R3 相比正式 V2 明显回归”的结论。

## 15. 冻结产物

- 正式 V2 基线报告：`dev_plan/CDECR/CDECR_PARENT_OCCURRENCE_PACKAGE_V2_30_REAL_ACCEPTANCE_REPORT_20260811.md`
- V2.1 R3 Registry：`.tmp/cdecr/parent_v21_full30_real_20260813_r3.sqlite3`
- V2.1 R3 全流程报告：`.tmp/cdecr/parent_v21_full30_real_20260813_r3_report.json`
- V2.1 R3 Package Gold：`.tmp/cdecr/parent_v21_full30_real_20260813_r3_package_gold_eval.json`
- V2.1 R3 完整层级 JSON：`.tmp/cdecr/parent_v21_full30_real_20260813_r3_clusters.json`
- V2.1 R3 人类可读层级：`.tmp/cdecr/parent_v21_full30_real_20260813_r3_hierarchy.md`
- V2.1 R3 可移植导出：`.tmp/cdecr/parent_v21_full30_real_20260813_r3_export.md`

除第 14 节外，本报告所有跨轮判断均以 2026-08-11 正式 V2 验收为主基线。
