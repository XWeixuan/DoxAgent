# CDECR Bulk Epoch V3 Late Convergence：30 篇真实 A/B 验收报告

> 日期：2026-08-03
> 正式运行：`LateConvergence_R4`，最终代码基线 `d6d6e31`
> 对照：`StageGraph_V3_R3`
> Gold：复用固定 268 条 Mention Gold、Field Gold、N9 独立评审口径与 2026-07-28 Package Gold
> 说明：Gold 评估器的模型调用不计入 workflow Token。

## 1. 结论

本轮实现已完整落地，技术可靠性通过，但不应将整套 late convergence 直接视为质量优化完成态。

- 30/30 单文档、30/30 跨文档成功；幂等复跑的模型调用、Mention、Atomic、Package 增量均为 0。
- R3→R4：墙钟 **+29.89%**，Input Token **+5.39%**，Output Token **+8.98%**，总 Token **+7.47%**。
- Mention P/R/F1 为 **70.80% / 72.39% / 71.59%**：Recall +3.36pp，但 Precision -2.90pp，仍未达到 >90% / >85% 的长期目标。
- N9 MERGE P / conditional R 为 **84.62% / 86.84%**，均明显好于此前未带最终预算控制的非正式 R2，但 Precision 仍低于目标 88%。
- 同一批当前 89 个高置信对齐 Atomic 上，Package Pair P/R/F1 从对照投影的 **89.42% / 85.26% / 87.29%** 提升到 **93.56% / 88.71% / 91.07%**；但 fragmented Gold groups 从 2 增至 7，说明大组召回改善的同时，小组完整性恶化。
- Atomic late 0 个新增 join，建议默认关闭；Wave C 有召回收益但污染边界，建议保持 shadow/重做边界后再启用；N13 pair-local Apply 被预算门槛跳过，本轮没有形成有效质量验收证据。

## 2. 执行、失败隔离与修复闭环

| 指标 | R3 | R4 | 判断 |
| --- | ---: | ---: | --- |
| 单文档成功 | 30/30 | 30/30 | 通过 |
| 跨文档成功 | 30/30 | 30/30 | 通过 |
| Mention | 251 | 274 | +23 |
| Atomic | 199 | 194 | -5，不代表自动改善 |
| Package | 116 | 91 | -25，需结合污染与碎片化判断 |
| Evidence VERIFIED | 275/275 | 308/308 | 100% |
| 幂等复跑增量 | 0/0/0/0 | 0/0/0/0 | 通过 |

正式 R4 前的 R3 尝试曾在第 79 条 Atomic assignment 失败：目标 Atomic 的披露日 `2026-06-24` 与 incoming Mention 的财期截止日 `2026-05-28` 被拼成倒置区间。修复后：

1. 不再把 occurrence date 与 fiscal-period boundary 伪造成同一 interval；冲突时保留已有 Atomic 时间。
2. 主合并或 singleton absorption 若仍出现局部 Pydantic 错误，只降级当前 item 并写紧凑审计。
3. R4 中 `ATOMIC_APPLY_DEGRADED=0`，说明归一化直接消除了该 bad case；即使未来出现同类异常，也不会扩大为 epoch 失败。

本轮模型格式异常为 Judge `invalid_json` 3 次、Atomic 1 次、Grounder 1 次，均由既有 item/task 局部恢复承接，没有造成文档失败。

## 3. 效能与成本

### 3.1 总量

| 指标 | R3 | R4 | 环比 |
| --- | ---: | ---: | ---: |
| 首轮墙钟 | 2,725,633 ms | 3,540,330 ms | **+29.89%** |
| 含幂等复跑总墙钟 | 2,726,440 ms | 3,541,139 ms | +29.88% |
| 模型调用 | 816 | 873 | +6.99% |
| Input Token | 1,582,413 | 1,667,778 | **+5.39%** |
| Output Token | 2,174,729 | 2,369,932 | **+8.98%** |
| 总 Token | 3,757,142 | 4,037,710 | **+7.47%** |
| aggregate model latency | 16,869,771 ms | 19,149,101 ms | +13.51% |

Input 仍在方案 8% late 预算范围内，但墙钟超过 +12% 门槛。主因不是 lane 排队：async executor 累计 queue wait 仅 4 ms，M2/M3 峰值并发均达到 24；实际瓶颈转移到串行尾部 Package/N13。

### 3.2 真实 stage 墙钟

| 阶段 | R3 | R4 | 环比 | R4 占比 |
| --- | ---: | ---: | ---: | ---: |
| 单文档 N1-N5 | 545,605 | 376,553 | -30.98% | 10.64% |
| Field | 619,712 | 600,471 | -3.10% | 16.96% |
| Atomic（含 late） | 654,855 | 699,783 | +6.86% | 19.77% |
| Package（含 Wave C） | 377,692 | 791,462 | **+109.55%** | 22.36% |
| N13 | 527,607 | 1,072,061 | **+103.19%** | 30.28% |

明确 late 增量：Atomic late 15,925 ms；Wave C 236,680 ms。`n13_late_apply_ms=247,625` 不能解释为 pair-local Apply 成本，因为 admission 已将 `pair_local=false`；该值实际覆盖普通 N13 的尾部 Apply/commit 区间，名称仍易误导。

### 3.3 节点 Token 与 aggregate latency 占比

| 节点 | calls | Input | Input 占比 | Output | Output 占比 | latency 占比 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| N9 core | 70 | 316,502 | 18.98% | 360,690 | 15.22% | 14.49% |
| Field core | 258 | 281,737 | 16.89% | 82,580 | 3.48% | 4.30% |
| Judge | 30 | 212,830 | 12.76% | 156,151 | 6.59% | 6.82% |
| N12 assignment | 12 | 201,774 | 12.10% | 280,684 | 11.84% | 11.92% |
| N13 merge | 25 | 165,481 | 9.92% | 376,549 | 15.89% | 17.10% |
| Grounder | 30 | 142,739 | 8.56% | 550,333 | 23.22% | 20.91% |
| N9 escalation | 25 | 119,339 | 7.16% | 383,115 | 16.17% | 16.09% |
| Grounder item repair | 13 | 58,296 | 3.50% | 62,650 | 2.64% | 2.54% |
| Wave C | 5 | 21,104 | 1.27% | 68,037 | 2.87% | 3.09% |
| Atomic late | 1 | 3,854 | 0.23% | 1,304 | 0.06% | 0.05% |

Grounder 仍是最大 Output 节点；N9 core+escalation 是最大 Input/推理组合。新增 late Token 本身受控，整体变慢主要来自 N12/N13 尾阶段请求时延和串行 barrier。

## 4. 逐层质量

### 4.1 Mention 与 Evidence

| Mention | R3 | R4 | 变化 |
| --- | ---: | ---: | ---: |
| Gold / output | 268 / 251 | 268 / 274 | output +23 |
| strict TP | 185 | 194 | +9 |
| partial | 41 | 55 | +14 |
| FP | 25 | 25 | 0 |
| FN | 83 | 74 | -9 |
| Precision | 73.71% | 70.80% | **-2.90pp** |
| Recall | 69.03% | 72.39% | **+3.36pp** |
| F1 | 71.29% | 71.59% | +0.30pp |

Missing recovery 把一部分完全漏召回转化为可见输出，但新增 strict TP 的同时 partial 增长更多，说明“召回后表述不完整/碎片化”仍是主矛盾。该机制不应整体回滚；应收紧 recovered candidate 的 atomic completeness 与 compound/umbrella 边界，而不是减少 candidate coverage。

Evidence 308/308 均 VERIFIED；semantic Evidence repair LLM=0；没有 Evidence 异常造成文档失败。该层通过。

### 4.2 Field

| Field | R3 | R4 | 变化 | 门槛 |
| --- | ---: | ---: | ---: | ---: |
| predicate | 75.37% | 80.22% | +4.85pp | 90% |
| participant | 77.24% | 82.09% | +4.85pp | 96% |
| metric | 71.94% | 79.47% | +7.53pp | 90% |
| fiscal period | 85.19% | 93.33% | +8.15pp | 80% |
| total | 76.14% | 81.90% | +5.76pp | 91% |

Field 全项环比改善，但 predicate、participant、metric 和 total 均未达门槛。本轮没有启用 Field late，因此不能把改善归因于未执行的优化项；更可能来自 Mention 样本变化与模型波动。

### 4.3 N7/N9 与 Atomic

| 指标 | R3 | R4 | 变化 | 判断 |
| --- | ---: | ---: | ---: | --- |
| candidate coverage | 100% | 100% | 0 | 通过 |
| judgeable SAME opportunities | 66 | 76 | +10 | 样本变化 |
| correct MERGE | 46 | 66 | +20 | 改善 |
| incorrect MERGE | 4 | 12 | +8 | 回归 |
| MERGE Precision | 92.00% | 84.62% | **-7.38pp** | 低于 88% 门槛 |
| conditional MERGE Recall | 69.70% | 86.84% | **+17.14pp** | 通过 85% 目标 |
| CREATE_NEW accuracy | 90.05% | 94.90% | +4.85pp | 改善 |

Atomic late 实际轨迹：48 个 residual candidate 中 44 个被 hard boundary 阻断，4 个进入 L1，`l0_same=0`、`l1_same=0`、applied=0。它消耗 3,854 Input / 1,304 Output / 15.9 秒，却没有新增正确 join，故默认启用没有收益证据。

最终 Atomic cluster 的最大簇为 16，出现 220 个 shadow hard-cannot-link pair violations。N9 节点评估只覆盖 assignment-time candidate 判断，不等同于全量 cluster Pair P/R；当前没有完整、同口径的 Atomic cluster Gold，不能虚报该指标。

### 4.4 Package 与碎片化

当前运行仅有 89/194（45.88%）Atomic 高置信对齐到历史 Gold。为降低跨轮对齐差异，优先使用“同一 89 个当前 Atomic 上的上一轮 Package 投影”作对照：

| 同一 89 Atomic | 对照 Package | R4 Package | 变化 |
| --- | ---: | ---: | ---: |
| Pair Precision | 89.42% | **93.56%** | +4.14pp |
| Pair Recall | 85.26% | **88.71%** | +3.45pp |
| Pair F1 | 87.29% | **91.07%** | +3.78pp |
| TP / FP / FN | 642 / 76 / 111 | 668 / 46 / 85 | +26 / -30 / -26 |
| fragmented Gold groups | **2/7** | 7/7 | +5，恶化 |
| excess components | **3** | 9 | +6，恶化 |
| singleton components | **3** | 13 | +10，恶化 |
| missed pair links | 111 | **85** | -26，改善 |

解释：R4 把大 Gold 组聚得更集中，因此 pair recall 与 missed links 改善；但更多小 Gold 组被拆成多个 component，导致“碎片化组数、excess、singleton”同时恶化。不能只看 Package 数从 116 降到 91，也不能只看 pair recall 宣称碎片化已解决。

若直接使用各轮各自高置信子集，R3 为 96/199、R4 为 89/194，Package P/R 从 98.94%/65.18% 变为 93.56%/88.71%；该数字受覆盖集合变化显著影响，只作旁证。

## 5. Late Convergence 分项归因

### Atomic late

- 候选 48，hard blocked 44，L1 task 4，新增 join 0。
- 每个正确新增 join 的 Token 成本不可计算，因为分母为 0。
- 判断：**默认关闭；保留 planner/ledger 代码供 shadow replay。**

### Package Wave C

- bounded candidate 64 对；M0 判断 SAME 4 条，M3 最终 Apply 20 条，共 24 条 redirect。
- 额外消耗 21,104 Input、68,037 Output、236.7 秒 stage wall。
- 它对大组 recall 有贡献，但当前 Gold 可见污染包括：
  - memory supplier reallocation 并入 Micron FQ3 earnings；
  - KOSPI、SK Hynix 与其他韩国市场 occurrence 混并；
  - Apple volume、Nasdaq 与其他不同证券/指标混并；
  - 不同时间尺度的 Micron 股价事实被并入同一 Package。
- 判断：**不适合默认 Apply；保留 bounded index 与任务账本，将 Apply 改为 shadow，补足具体 parent/object/market instrument 边界后再验。**

### N13 pair-local Apply

- admission 时 late Input ratio=3.72%，通过 8% 门槛；late wall ratio=13.73%，超过 12% 门槛。
- 最终 `pair_local=false`，没有执行 pair-local hub-and-spoke Apply。
- 判断：**本轮没有覆盖其质量，不能宣称通过或失败；保持关闭，另做固定输入 node A/B。**

## 6. 回滚必要性

| 修改项 | 证据 | 建议 |
| --- | --- | --- |
| Stage Graph、冻结 snapshot、异步 lane、单 writer | 30/30；幂等 0 增量 | 保留 |
| Neutral invalid-task / item-local fallback | 5 次格式异常均未扩大 | 保留 |
| Atomic 时间归一与 Apply 隔离 | 原阻塞 case 消失，degraded=0 | 保留 |
| Grounder missing recovery | FN -9，但 partial +14 | 保留机制，收紧完整性 |
| Atomic late L0/L1 Apply | 0 join、纯成本 | 默认关闭 |
| Wave C bounded planner/index/ledger | 有界 64 对，技术可靠 | 保留为 shadow |
| Wave C 自动 Apply | 边界污染且 Package 尾阶段 +109.55% | 默认关闭/重做后再验 |
| N13 dictionary/full bounded Decide | Input 仍受控，25 个批次完成 | 保留 |
| N13 pair-local Apply | 被预算门槛跳过 | 保持关闭，不能据本轮决策启用 |
| 本轮三段宽松 Prompt | N9 Precision -7.38pp，Wave C 出现 context/object 混并 | 撤回共享 core Prompt 的宽松句；如研究 late，应使用独立窄 Prompt |

不建议整体回滚 `b1e532b`：neutral semantics、failure isolation、artifact/ledger、预算 admission 与 bounded planner 均有明确价值。建议只关闭三个现有粗粒度 Apply 开关，并撤回影响全量 core task 的宽松 Prompt 句；本报告只评估回滚必要性，没有擅自执行回滚。

## 7. 可复核产物

正式运行目录：`D:\DoxAgent_CDECR_Acceptance_20260803_LateConvergence_R4`

- `cdecr_30_late_convergence.sqlite3`
- `cdecr_30_late_convergence_report.json`
- `diagnostics.json`
- `mention_gold_eval.json`
- `field_gold_eval.json`
- `n9_gold_eval.json`
- `package_gold_eval.json`
- `run.log`

非正式/失败运行：R1 因 Wave C 曾先枚举全量 pair 而主动终止；R3 因局部 EventTime 倒置而中止。二者只作为根因证据，不进入最终 A/B 指标。R2 在最终预算修复前完成，亦不代表最终代码。
