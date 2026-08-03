# CDECR Bulk Epoch V3 Late Convergence：30 篇真实 A/B 验收报告

> 日期：2026-08-04
> 本轮正式运行：`LateConvergence_R4`，Registry 位于 `D:\DoxAgent_CDECR_Acceptance_20260803_LateConvergence_R4`
> 唯一正式对照：[CDECR_BULK_EPOCH_V3_STAGE_GRAPH_30_REAL_AB_ACCEPTANCE_REPORT_20260803.md](C:/Users/WEIXUANXIE/Desktop/DoxAgent/dev_plan/CDECR/CDECR_BULK_EPOCH_V3_STAGE_GRAPH_30_REAL_AB_ACCEPTANCE_REPORT_20260803.md)
> 对照 Registry：`D:\DoxAgent_CDECR_Acceptance_20260803_StageGraph_V3_R3\cdecr_30_stage_graph_v3.sqlite3`
> 语料与 Gold：相同固定 30 篇、268 条 Mention Gold、Field Gold、N9 独立评审口径和 2026-07-28 Package 人工边界

## 0. 更正声明与结论

上一版报告虽然读取了对照 Registry，但存在一处实质性口径错误：把 Package evaluator 的 `previous_actual_packages_on_matched_gold` 称为“上一轮 Stage Graph Package”。该字段实际来自传入的 2026-07-28 Gold Registry 历史分区，并不是本报告指定的上一轮正式 Stage Graph 运行。原 89.42%/85.26% 对照值不再用于两轮 A/B 结论。

本报告以指定的上一轮正式验收文档及其 `StageGraph_V3_R3` Registry 为唯一基线。结论如下：

1. **可靠性持平且阻塞修复有效**：两轮均为 30/30 单文档、30/30 跨文档成功；本轮幂等增量仍为 0。正式运行前暴露的 EventTime 倒置已修复，R4 没有出现 `ATOMIC_APPLY_DEGRADED` 或 epoch 失败。
2. **效能明显回退**：墙钟 2,726,440→3,541,139 ms，**+29.88%**；Input/Output/总 Token 分别 **+5.39%/+8.98%/+7.47%**。Package 与 N13 串行尾部各增长约一倍，是主要墙钟回退来源。
3. **Mention 以 Precision 换 Recall**：Precision 73.71%→70.80%（-2.90pp），Recall 69.03%→72.39%（+3.36pp）；仍远未达到 >90%/>85% 目标。
4. **Field 全项改善但仍未达总门槛**：total 76.14%→81.90%，+5.76pp；只有 fiscal period 达标。
5. **N9 Recall 显著恢复，但错误 Merge 增多**：MERGE Precision 92.00%→84.62%，conditional Recall 69.70%→86.84%。召回达标，Precision 回退 7.38pp。
6. **Package Recall 明显改善，但不是无代价改善**：各轮发布口径 P/R 从 98.94%/65.18% 变为 93.56%/88.71%，但覆盖集合不同。真正的 61-Atomic 同集辅助对比显示 P/R 从 95.60%/57.24% 提升到 97.06%/75.99%；同时 false-merge groups 2→3、fragmented groups 4→5、singleton components 8→11。
7. **Late 分项结论**：Atomic late 0 个新增 join，应关闭；Wave C 是本轮 Package recall 改善的主要运行时来源，但 24 条 redirect 带来新的错误簇，应退回 shadow；N13 pair-local Apply 被预算门槛跳过，本轮没有验收它。
8. **不整体回滚 Stage Graph**：保留 one-shot、async lane、单 writer、dictionary DTO、artifact/task ledger、neutral failure 与预算 admission；仅关闭或重做 late Apply，并撤回影响 core task 的宽松 Prompt。

## 1. 执行范围、成功率与输出规模

| 指标 | 上一轮 Stage Graph 正式验收 | 本轮 Late Convergence | 变化 |
| --- | ---: | ---: | ---: |
| 单文档成功 | 30/30 | 30/30 | 持平 |
| 跨文档成功 | 30/30 | 30/30 | 持平 |
| stderr / workflow failure | 0 | 0 | 持平 |
| Mention | 251 | 274 | +23 |
| Atomic | 199 | 194 | -5 |
| Package | 116 | 91 | -25 |
| 最大 Atomic cluster | 7 | 16 | +9，污染半径风险 |
| shadow hard-conflict pair violations | 72 | 220 | +148，明显回退 |
| 幂等复跑 calls/Mention/Atomic/Package 增量 | 0/0/0/0 | 0/0/0/0 | 持平 |

Package 与 Atomic 数量下降不能单独解释为碎片化改善：本轮同时存在正确收敛、错误大簇和小型 Gold group 继续碎裂，必须结合 Gold pair 与 component 指标判断。

本轮局部模型格式失败共 5 次：Judge 3、N9 core 1、Grounder 1；上一轮为 14 次。另有 4 个 N12 Wave B task 局部失败，上一轮为 7 个。所有异常均被 item/task 级恢复隔离，没有扩大为文档失败。

## 2. 效能、Token 与节点占比

### 2.1 总量 A/B

| 指标 | 上一轮 Stage Graph | 本轮 Late Convergence | 变化 |
| --- | ---: | ---: | ---: |
| 首轮墙钟 | 2,725,633 ms | 3,540,330 ms | **+29.89%** |
| 含幂等复跑总墙钟 | 2,726,440 ms | 3,541,139 ms | **+29.88%** |
| 模型调用 | 816 | 873 | +57 / +6.99% |
| Input Token | 1,582,413 | 1,667,778 | **+85,365 / +5.39%** |
| Output Token | 2,174,729 | 2,369,932 | **+195,203 / +8.98%** |
| 总 Token | 3,757,142 | 4,037,710 | **+280,568 / +7.47%** |
| aggregate model latency | 16,869,771 ms | 19,149,101 ms | +13.51% |

Input 没超过方案 8% late 预算，但墙钟远超 +12% 门槛。Async executor queue wait 仅 4 ms，M2/M3 峰值并发均为 24，故不是 provider lane 排队；瓶颈是 stage barrier 后新增/变慢的 N12、Wave C 和 N13 串行尾部。

### 2.2 真实 stage 墙钟

| 阶段 | 上一轮 | 本轮 | 变化 | 本轮占比 |
| --- | ---: | ---: | ---: | ---: |
| 单文档 N1-N5 | 545,605 | 376,553 | -30.98% | 10.64% |
| Field | 619,712 | 600,471 | -3.10% | 16.96% |
| Atomic / N9（含 late） | 654,855 | 699,783 | +6.86% | 19.77% |
| Package / N12（含 Wave C） | 377,692 | 791,462 | **+109.55%** | 22.36% |
| N13 | 527,607 | 1,072,061 | **+103.19%** | 30.28% |

Atomic late 自身为 15,925 ms；Wave C 为 236,680 ms。`n13_late_apply_ms=247,625` 不能解释为 pair-local 成本，因为本轮 admission 结果为 `pair_local=false`；它实际覆盖普通 N13 尾部 Apply/commit，字段命名仍有误导性。

### 2.3 关键节点成本变化

| 节点 | calls | Input 变化 | Output 变化 | 判断 |
| --- | ---: | ---: | ---: | --- |
| N9 core | 64→70 | 277,504→316,502（+14.05%） | 315,925→360,690（+14.17%） | Mention/task 增加 |
| N9 escalation | 14→25 | 64,884→119,339（+83.93%） | 193,967→383,115（+97.52%） | 最大新增推理成本 |
| Field core | 247→258 | +3.44% | -3.69% | 基本稳定 |
| Judge | 27→30 | +6.71% | -16.26% | 输出下降 |
| N12 assignment | 9→12 | +24.66% | +25.79% | 加上 Wave C 后更高 |
| Wave C | 0→5 | +21,104 | +68,037 | 纯新增 |
| N13 merge | 37→25 | -26.47% | +6.54% | 请求减少但推理更长 |
| Grounder | 30→30 | -0.50% | -9.31% | 改善 |

本轮 Input 占比前三为 N9 core 18.98%、Field 16.89%、Judge 12.76%；Output 占比前三为 Grounder 23.22%、N9 escalation 16.17%、N13 15.89%。N9 escalation 与 Package/N13 尾阶段是下一轮主要效能目标。

## 3. 逐层质量对比

### 3.1 Mention

| 指标 | 上一轮 Stage Graph | 本轮 Late Convergence | 变化 | 目标 |
| --- | ---: | ---: | ---: | ---: |
| Gold / output | 268 / 251 | 268 / 274 | output +23 | — |
| strict TP | 185 | 194 | +9 | — |
| partial | 41 | 55 | +14 | 越低越好 |
| FP | 25 | 25 | 0 | — |
| FN | 83 | 74 | -9 | — |
| Precision | **73.71%** | 70.80% | **-2.90pp** | >90% |
| Recall | 69.03% | **72.39%** | **+3.36pp** | >85% |
| F1 | 71.29% | 71.59% | +0.30pp | — |

Grounder missing recovery 的方向有效：FN 减少 9。但新增 23 个输出只转化为 9 个 strict TP，partial 增加 14，表明本轮主要把一部分“完全漏掉”转成了“不完整、碎片化或复合表达”。因此不应回滚 recovery；应收紧 recovered candidate 的 atomic completeness、compound 与 umbrella 边界。

### 3.2 Evidence

| 指标 | 上一轮 | 本轮 | 判断 |
| --- | ---: | ---: | --- |
| Evidence records | 275 | 308 | 随 Mention 增加 |
| VERIFIED | 275/275 | 308/308 | 100% |
| semantic Evidence repair LLM | 0 | 0 | 通过 |
| Evidence 异常导致文档失败 | 0 | 0 | 通过 |

Evidence 层没有质量或阻塞回归。

### 3.3 Field

| Field | 上一轮 Stage Graph | 本轮 Late Convergence | 变化 | 门槛 |
| --- | ---: | ---: | ---: | ---: |
| predicate | 75.37% | 80.22% | +4.85pp | 90% |
| participant | 77.24% | 82.09% | +4.85pp | 96% |
| metric | 71.94% | 79.47% | +7.53pp | 90% |
| fiscal period | 85.19% | 93.33% | +8.15pp | 80% |
| total | 76.14% | 81.90% | **+5.76pp** | 91% |

Field 全项环比改善，但 predicate、participant、metric 与 total 仍未达标。本轮没有启用 Field late，改善不能归因于未执行的分支；它是 Mention 样本变化、上游字段质量和模型波动的综合结果。

### 3.4 N7/N9 与 Atomic

| 指标 | 上一轮 Stage Graph | 本轮 Late Convergence | 变化 | 目标/判断 |
| --- | ---: | ---: | ---: | --- |
| N9 task | 251 | 274 | +23 | 随 Mention 增加 |
| candidate coverage | 100% | 100% | 0 | 通过 |
| judgeable SAME opportunities | 66 | 76 | +10 | 样本变化 |
| correct MERGE | 46 | 66 | +20 | 改善 |
| incorrect MERGE | 4 | 12 | +8 | 回退 |
| MERGE Precision | **92.00%** | 84.62% | **-7.38pp** | 低于 88% 验收线 |
| conditional MERGE Recall | 69.70% | **86.84%** | **+17.14pp** | 达到 >85% |
| CREATE_NEW accuracy | 90.05% | 94.90% | +4.85pp | 改善 |
| 最大 Atomic cluster | 7 | 16 | +9 | 回退 |
| hard-conflict pair violations | 72 | 220 | +148 | 严重回退 |

N9 的过拆问题确实缓解，但不是无风险恢复：多得到 20 个正确 MERGE 的同时增加 8 个错误 MERGE；错误进入 cluster 后被组合放大为 220 个 hard-conflict pair violations。当前 core Prompt 的宽松句影响了所有首轮 N9 task，而不仅是 residual late task，和 Precision 回退具有直接机制相关性。

Atomic late 自身没有贡献上述召回改善：48 个 residual 中 44 个被 hard boundary 阻断，4 个进入 L1，`l0_same=0`、`l1_same=0`、applied=0；消耗 3,854 Input、1,304 Output 与 15.9 秒，正确新增 join 为 0。

N9 独立评估是 assignment-time candidate 口径，不等于最终全局 Atomic Pair P/R。当前仍没有覆盖全部最终 cluster 的同口径人工 Atomic Gold，因此不虚报全量 Atomic Pair 指标。

### 3.5 Package：发布口径直接对比

两轮都对齐到同一个 2026-07-28 Gold Registry，但高置信覆盖集合不同：上一轮 96/199（48.24%），本轮 89/194（45.88%）。以下是两份正式报告各自发布口径，必须带着覆盖差异解读：

| 指标 | 上一轮 Stage Graph（96） | 本轮 Late Convergence（89） | 表面变化 |
| --- | ---: | ---: | ---: |
| Pair Precision | **98.94%** | 93.56% | -5.38pp |
| Pair Recall | 65.18% | **88.71%** | +23.53pp |
| Pair F1 | 78.59% | **91.07%** | +12.48pp |
| TP / FP / FN | 745 / 8 / 398 | 668 / 46 / 85 | 覆盖不同，不直接比数量 |
| multi-event Gold groups | 9 | 7 | 覆盖不同 |
| fragmented Gold groups | 8 | 7 | -1 |
| excess components | 16 | 9 | -7 |
| singleton components | 20 | 13 | -7 |
| missed pair links | 398 | 85 | -313 |

发布口径显示 Recall 与碎片化大幅改善、Precision 下滑，但不能把全部变化归因于优化，因为评估集合少了 7 个 Atomic、2 个 multi-event Gold group。

### 3.6 Package：真正跨轮同集辅助对比

为避免上一版错误使用 Gold Registry 历史 Package 作为“上一轮”，本次新增真实跨轮同集计算：

1. 用两轮 Atomic embedding 做双向互选；阈值 0.9、best-vs-second margin 0.01。
2. 仅保留 R3↔R4 互相最佳的匹配。
3. 再要求上一轮 Atomic 已高置信映射到人工 Package Gold。
4. 最终得到 61 个两轮共同、Gold 可判 Atomic；比较两轮真实落库 Package partition。

| 同一 61 Atomic | 上一轮 Stage Graph | 本轮 Late Convergence | 变化 |
| --- | ---: | ---: | ---: |
| Pair Precision | 95.60% | **97.06%** | +1.46pp |
| Pair Recall | 57.24% | **75.99%** | +18.75pp |
| Pair F1 | 71.60% | **85.24%** | +13.64pp |
| TP / FP / FN | 174 / 8 / 130 | 231 / 7 / 73 | +57 / -1 / -57 |
| multi-event Gold groups | 5 | 5 | 持平 |
| fragmented Gold groups | 4 | 5 | +1，恶化 |
| excess components | 7 | 7 | 持平 |
| singleton components | 8 | 11 | +3，恶化 |
| missed pair links | 130 | 73 | -57，改善 |
| false-merge groups | 2 | 3 | +1，恶化 |

这组结果支持一个更准确的判断：**本轮 Package 的主要收益是真实 Recall 提升和大组收敛，而不是对齐集合幻觉；但小组碎片化与错误簇数量没有同步改善。** Pair Precision 在共同子集上没有下降，说明发布口径的 -5.38pp 部分来自覆盖变化；不过 false-merge group 增加仍是真实风险。

本轮 4 个发布口径错误簇包括：

- memory supplier reallocation 并入 Micron FQ3 earnings；
- KOSPI、SK Hynix 与其他韩国市场 occurrence 混并；
- Apple volume、Nasdaq 与其他证券/指标混并；
- 不同时间尺度的 Micron 股价事实合并。

上一轮已经存在跨证券市场事实混并，本轮仍复现；memory supplier reallocation→earnings 与更大的 Micron 市场时间混并是本轮新增/放大的边界问题。

## 4. Late Convergence 分项归因

### 4.1 Atomic late L0/L1

- residual candidate 48；hard blocked 44；L1 task 4；新增 SAME/Apply 0。
- 成本：3,854 Input、1,304 Output、15,925 ms。
- 结论：本轮 N9 Recall 改善来自更激进的 core N9 决策，不是 late branch。**关闭 Atomic late Apply；保留 planner、artifact 与 ledger 供离线 replay。**

### 4.2 Package Wave C

- bounded candidate 64 对；M0 redirect 4 条、M3 redirect 20 条，共 Apply 24 条。
- 成本：21,104 Input、68,037 Output、236,680 ms。
- 本轮 Registry 的全部 24 条 Package redirect 都来自 Wave C；N13 没有新增 redirect。因此 Package Recall 收益与错误簇都主要与 Wave C Apply 相关。
- 24 条边不是 24 个独立 Gold pair：redirect 会改变整个组件，既可一次恢复大量 TP，也会把一个错误边放大成多个 FP。
- 结论：**保留有界索引、64-pair cap、task ledger 与失败中性语义；自动 Apply 退回 shadow。** 下一版必须把具体 parent identity、business object、market instrument/session/measure 纳入 pair-local boundary，再做固定输入 node A/B。

### 4.3 N13 pair-local Apply

- admission 时 late Input ratio=3.72%，低于 8%；late wall ratio=13.73%，高于 12%。
- 最终 artifact 明确记录 `pair_local=false`，没有执行 hub-and-spoke pair-local Apply。
- 普通 N13 评审了 316 对，得到 83 SAME、228 DIFFERENT、5 UNCERTAIN，但没有新增 Package redirect；另有 59 条 weak-boundary SAME 未 Apply。
- N13 Input 下降 26.47%，但 Output 增加 6.54%，stage 墙钟增加 103.19%。说明瓶颈是更长的模型推理和串行尾部，而不是 pair-local Apply。
- 结论：**本轮不能评价 pair-local Apply 的准召；保持关闭，另做 node-only A/B。**

## 5. 失败根因与可靠性判断

正式 R4 前的一次失败运行在第 79 条 Atomic assignment 终止。根因是：已有 Atomic 使用披露发生日 `2026-06-24`，incoming Mention 使用财期截止日 `2026-05-28`；通用时间并集把二者拼为倒置 interval，触发 `event_end must not precede event_start`。

修复包括：

1. occurrence date 与 fiscal-period boundary 冲突时不再伪造 interval，保留已建立的 Atomic 时间。
2. 主合并或 singleton absorption 若仍触发局部 Pydantic ValidationError，仅降级当前 item，并保留紧凑审计。
3. R4 中 `ATOMIC_APPLY_DEGRADED=0`，说明确定性归一已经直接消除该 bad case；30/30 完整运行证明异常没有再扩大。

这项修复应保留。它修的是数据语义与失败隔离，不是为测试集定制的规则。

## 6. 保留、关闭与重做判断

| 修改项 | 相对上一轮正式验收的证据 | 判断 |
| --- | --- | --- |
| Stage Graph、冻结 snapshot、async lane、单 writer | 两轮均 30/30、幂等 0 增量 | **保留** |
| dictionary N9/N12/N13 DTO | 本轮总 Input 仅 +5.39%，仍远低于 Stage Graph 前旧架构 | **保留** |
| neutral invalid-task / item-local fallback | 格式失败 14→5，均未扩大 | **保留** |
| Atomic 时间归一与 Apply 隔离 | 原阻塞根因消失 | **必须保留** |
| Grounder missing recovery | FN -9、Recall +3.36pp；partial +14 | 保留召回，收紧完整性 |
| Atomic late L0/L1 | 0 join、纯成本 | **默认关闭** |
| N9 core 宽松 Prompt | Recall +17.14pp，但 P -7.38pp、hard violations 72→220 | **撤回共享宽松句；late 使用独立窄 Prompt** |
| Wave C bounded planner/index/ledger | 64 对有界、技术可靠 | 保留为 shadow |
| Wave C 自动 Apply | 同集 Recall +18.75pp，但错误簇 2→3、尾阶段 +109.55% | **默认关闭并重做边界** |
| N13 full bounded Decide | Input -26.47%，覆盖 316 pair | 保留 |
| N13 pair-local Apply | 本轮未执行 | 保持关闭，单独验收 |
| 整套 `b1e532b` 回滚 | 会丢失 failure isolation、budget、ledger 与有界 planner | **不建议** |

建议的最小修正顺序：

1. 关闭三个 late Apply 开关，恢复上一轮 core N9/N12/N13 Prompt 边界；不要整体回滚 Stage Graph。
2. Atomic late 改为独立 Prompt，先对上一轮 20 个漏合和本轮 12 个错误 MERGE 做固定记录 replay。
3. Wave C 保持 shadow，在 parent/object/instrument/session 边界达到高精度后再只开放 Tier A。
4. N13 pair-local 用固定 Package snapshot 做 node-only A/B；不得从本轮“被跳过”推导生产可用性。
5. Mention recovery 不减少 candidate coverage，只优化 partial/compound/umbrella 表达完整性。

本报告只评估回滚必要性，没有执行任何回滚。

## 7. 验证、产物与口径限制

代码验证：

- focused late/预算/时间回归：6 passed。
- `uv run ruff check src/cdecr tests/cdecr/test_bulk_epoch_v3.py tests/cdecr/test_cross_document.py`：通过。
- `uv run mypy src/cdecr`：51 source files 通过。
- 此前完整实现门槛：`tests/cdecr` 289 passed、3 skipped。

本轮正式产物：

- `D:\DoxAgent_CDECR_Acceptance_20260803_LateConvergence_R4\cdecr_30_late_convergence.sqlite3`
- `D:\DoxAgent_CDECR_Acceptance_20260803_LateConvergence_R4\cdecr_30_late_convergence_report.json`
- `D:\DoxAgent_CDECR_Acceptance_20260803_LateConvergence_R4\diagnostics.json`
- `D:\DoxAgent_CDECR_Acceptance_20260803_LateConvergence_R4\mention_gold_eval.json`
- `D:\DoxAgent_CDECR_Acceptance_20260803_LateConvergence_R4\field_gold_eval.json`
- `D:\DoxAgent_CDECR_Acceptance_20260803_LateConvergence_R4\n9_gold_eval.json`
- `D:\DoxAgent_CDECR_Acceptance_20260803_LateConvergence_R4\package_gold_eval.json`
- `D:\DoxAgent_CDECR_Acceptance_20260803_LateConvergence_R4\package_stagegraph_mutual_atomic_comparison.json`

口径限制：

- 两轮发布 Package 指标覆盖集合不同，因此以直接发布值展示整体观测，以 61 个双向互选共同 Atomic 作为跨轮因果辅助；后者覆盖较窄，不能外推全部 194 Atomic。
- Package Gold 来自 2026-07-28 人工边界，跨轮 Atomic 通过 embedding 对齐；对齐不是人工逐条重标。
- N9 指标是 assignment-time 独立评审，不等于最终 Atomic cluster 全量 Pair P/R。
- 真实模型存在随机波动。效能变化具有强观测证据；质量变化可确认结果与机制相关性，但不能把每一项波动都归因于单一代码修改。
