# CDECR 最终收尾式窄修复：30 篇真实 A/B 验收报告

## 1. 结论

本轮实现完整落地，30/30 文档和跨文档阶段全部完成，Schema 与 Evidence 有效率均为 100%。效能目标显著达成：相对上一轮 R4，总墙钟下降 4.40%，真实工作流总 Token 下降 17.67%，模型调用减少 10.65%；N13 input Token 下降 60.22%，p95 延迟下降 17.36%。

但本轮**不通过整体业务质量验收**，不应原样发布：

- Mention Precision 小幅提升 0.74pp，但 Recall 下降 2.99pp，未守住方案要求的 72.39%。
- Field 总准确率下降 4.34pp；主要原因是上游 Mention 缺失增加，而不是所有字段抽取能力同步退化。
- 修正评估器后，N9 MERGE Precision/conditional Recall 为 98.61%/89.87%，均高于 R4 同口径的 97.44%/88.37%；原报告的 72.22% 是评估器漏评物化 selected target 造成的假回退。
- Package 在双向互选的 65 个同集 Atomic 上，P/R/F1 从 90.78%/85.30%/87.96% 降至 89.10%/72.97%/80.23%。Recall 下跌 12.34pp，是本轮最严重的真实回归。
- N13 pair-local Apply 确认产生一组严重错误合并：模型以“同一 source/market roundup”为依据，将五个不同市场事实 Package 合并。该功能是第一回滚候选。
- 新 Package 边界避免了一部分旧错误簇，但也把同一 Micron FY2026 Q3 earnings Gold 从 3 个组件进一步拆为 5 个组件；应保留统一边界架构和 DTO 优化，校准 artifact/anchor 的可信度及父事件边界，不宜整体回滚 Wave C。

最终建议：**保留效能重构、DTO 去重、批 embedding、尾批平衡、telemetry 和高置信市场边界；回滚 N13 pair-local Apply 的业务启用；单独修正 selected-target 评估器；对 Package artifact/anchor 边界做窄校准后再复测。** 本报告仅评估，未实施业务回滚。

## 2. A/B 定义与可复现材料

### 2.1 基线与实验组

- 基线：`D:\DoxAgent_CDECR_Acceptance_20260803_LateConvergence_R4`
- 基线报告：`CDECR_BULK_EPOCH_V3_LATE_CONVERGENCE_30_REAL_AB_ACCEPTANCE_REPORT_20260803.md`
- 实验组：`D:\DoxAgent_CDECR_Acceptance_20260804_FinalNarrow_R1`
- 实验 Registry：`cdecr_30_final_narrow.sqlite3`
- 实验运行报告：`cdecr_30_final_narrow_report.json`
- 实验诊断：`diagnostics.json`
- Mention/Field/N9/Package 评估：`mention_gold_eval.json`、`field_gold_eval.json`、`n9_gold_eval_corrected.json`、`package_gold_eval.json`
- 双向互选同集 Package 比较：`package_r4_mutual_atomic_comparison.json`
- 零 LLM R4 反事实回放：`final_narrow_offline_replay.json`

比较原则：

1. 工作流 Token、耗时、调用次数使用两轮真实 Registry 的原始 `model_calls`；评估模型自身 Token 不混入工作流成本。
2. Mention/Field 使用同一 30 篇 Gold 与同一评估方法。
3. Package 同时报告发布口径和双向互选同集口径；跨轮最终 Atomic 不同，主结论以同集口径为准。
4. N9 原评估口径存在 selected target 漏项；本报告保留原数值用于审计，并以修正后对称口径作为质量结论。

### 2.2 实现和本地验证

实现提交：`f5bc245 feat(cdecr): finalize bounded late package convergence`。

主要落地项：

- N13 pair-local Apply 不再被 late wall admission 关闭，late budget 仅保留为 telemetry。
- N13 accepted retrieval text 统一做一次 M1 batch embedding，再按 writer 顺序 Apply。
- `PackagePairBoundary` 增加 instrument、market measure、object scope，并被 Wave C candidate/Decide/Apply 和 N13 eligibility/Apply 共同使用。
- Wave C 改为 batch 内唯一 Package 字典和 compact `same/diff` boundary。
- M0 仅接受 trusted artifact 或双方均无冲突的 canonical anchor。
- N13 尾批只在 25→24 且重平衡后每批不超过 13 时触发。
- telemetry 拆分为 post-decide、pair-local apply、eligible/applied/boundary-blocked。
- 完成 N9、N13、Grounder recovery、Wave C 四处短 Prompt 对齐。

验证结果：

- `tests/cdecr/test_cross_document.py`：33 passed。
- `tests/cdecr/test_single_document.py`：31 passed。
- `tests/cdecr/test_bulk_epoch_v3.py`：20 passed。
- 变更脚本 Ruff 与 Python compile：通过。
- R4 零 LLM 三场景回放完整复现基线 TP/FP/FN。

## 3. 运行成功率与局部失败

| 指标 | R4 | 本轮 | 变化 |
| --- | ---: | ---: | ---: |
| 文档完成 | 30/30 | 30/30 | 持平 |
| 跨文档完成 | 30/30 | 30/30 | 持平 |
| 文档失败 | 0 | 0 | 持平 |
| Mention Schema 有效 | 100% | 100% | 持平 |
| Evidence Span 有效 | 100% | 100% | 持平 |
| VERIFIED Evidence | 100% | 282/282 | 持平 |
| Atomic | 194 | 187 | -7 |
| Package | 91 | 98 | +7 |

主流程没有文档级失败。真实模型局部失败均被 item/task 级降级吸收：

| 节点 | 错误 | 次数 | 影响范围 |
| --- | --- | ---: | --- |
| Judge | invalid JSON | 4 | 局部 batch/item 恢复；不导致文档失败 |
| N9 | invalid JSON | 1 | 局部任务恢复 |
| N9 escalation | invalid JSON | 1 | 局部 candidate/task 恢复 |
| Grounder | invalid JSON | 1 | 保留合法 draft，触发局部修复 |
| Grounder item repair | invalid JSON | 1 | 对应非法 item 降级 |
| missing item recovery | invalid JSON | 1 | 对应 missing candidate 未恢复 |
| missing recovery | invalid JSON | 1 | 该批局部恢复不完整 |

此外，第一次 N9 Gold 评估曾遇到一次 TLS `UNEXPECTED_EOF_WHILE_READING`；这是评估请求的外部网络错误，不影响已完成的主运行。采用更低并发重试后评估完成。

## 4. 墙钟、调用量与 Token

| 指标 | R4 | 本轮 | 变化 |
| --- | ---: | ---: | ---: |
| 总墙钟 | 3,541,139 ms | 3,385,244 ms | **-155,895 ms / -4.40%** |
| 首轮墙钟 | 3,540,330 ms | 3,384,349 ms | -4.41% |
| 模型调用 | 873 | 780 | **-93 / -10.65%** |
| Input Token | 1,667,778 | 1,449,648 | **-218,130 / -13.08%** |
| Output Token | 2,369,932 | 1,874,586 | **-495,346 / -20.90%** |
| 总 Token | 4,037,710 | 3,324,234 | **-713,476 / -17.67%** |
| 模型延迟累计 | 19,149,101 ms | 14,937,672 ms | -22.00% |

全部效能硬门槛通过。墙钟改善小于累计模型延迟改善，原因是 one-shot stage graph 中同波并发请求的延迟重叠，节省的单请求累计时间不会一比一转成墙钟；Grounder、N9 和 Judge 仍构成关键路径。

## 5. 节点 Token 与运行时占比

下表 Token 占比以本轮 3,324,234 工作流 Token 为分母；延迟占比以各模型调用 latency 累计为分母，不能等同于墙钟占比。

| 节点 | 调用 | Input | Output | Token占比 | 延迟 | 延迟占比 | Token环比 | 延迟环比 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Grounder | 30 | 142,437 | 525,157 | 20.08% | 3,787,777 | 25.36% | -3.68% | -5.41% |
| N9 coreference | 68 | 299,019 | 326,623 | 18.82% | 2,551,304 | 17.08% | -7.61% | -8.06% |
| Judge | 30 | 210,506 | 175,604 | 11.62% | 1,463,788 | 9.80% | +4.64% | +12.17% |
| N9 escalation | 21 | 93,999 | 267,652 | 10.88% | 2,203,583 | 14.75% | -28.02% | -28.49% |
| Field coreference | 233 | 254,284 | 75,062 | 9.91%吗  | 777,920 | 5.21% | -9.60% | -5.57% |
| N12 assignment | 8 | 146,125 | 140,074 | 8.61% | 1,046,430 | 7.01% | -40.68% | -54.15% |
| N13 merge | 11 | 65,824 | 163,741 | 6.91% | 1,387,360 | 9.29% | -57.65% | -57.62% |
| Wave C | 5 | 12,751 | 107,015 | 3.60% | 887,330 | 5.94% | +34.36% | +49.78% |
| Grounder item repair | 12 | 52,891 | 31,726 | 2.55% | 245,925 | 1.65% | -30.04% | -49.51% |
| Dreamer | 30 | 38,092 | 37,814 | 2.28% | 222,841 | 1.49% | -0.63% | -2.95% |
| 其余节点合计 | 283 | 143,820 | 61,716 | 6.18% | 363,594 | 2.43% | — | — |

N13 细项：

- 调用数 25→11；Input 165,481→65,824（-60.22%）；Output 376,549→163,741（-56.51%）。
- p50 92,321→96,028 ms（+4.02%）；p95 285,107→235,623 ms（-17.36%）；最大值 579,926→327,369 ms。
- 本轮实际调用数不是 25，因此 25→24 尾批平衡没有成为主要收益来源；主要收益来自边界前置过滤、M0 和更少的 M3 batch。
- Apply 后 M1 embedding 合并为 1 次，3,646 Input Token、1,081 ms；没有新增 M3 请求。

Wave C DTO 把 Input 从 21,104 降至 12,751（-39.58%），明显超过方案预期；但 Output 从 68,037 增至 107,015（+57.29%），p50 从 124,622 增至 220,249 ms。输出 Schema 没有增加 reasoning 字段，因此最可能是 DeepSeek hidden thinking/服务端 output 计费增长，而不是 DTO 输出字段膨胀。这一推断需 provider 逐请求 reasoning-token 可见性才能完全证实。

## 6. Mention 质量

| 指标 | R4 | 本轮 | 变化 | 门槛 |
| --- | ---: | ---: | ---: | ---: |
| 输出 Mention | 274 | 260 | -14 | — |
| Strict TP | 194 | 186 | -8 | — |
| Partial | 55 | 51 | -4 | `<55` 通过 |
| FP | 25 | 23 | -2 | — |
| FN | 74 | 82 | +8 | `≤74` 失败 |
| Precision | 70.80% | 71.54% | +0.74pp | `≥70.80%` 通过 |
| Recall | 72.39% | 69.40% | **-2.99pp** | `≥72.39%` 失败 |
| F1 | 71.59% | 70.45% | -1.14pp | — |

坏例分布：

| 首个失败位置 | R4 | 本轮 | 变化 |
| --- | ---: | ---: | ---: |
| Dreamer missing | 35 | 40 | +5 |
| Output partial | 37 | 39 | +2 |
| Grounder missing/invalid | 0 | 2 | +2 |
| Judge rejected/merged | 2 | 1 | -1 |

判断：新的 recovery 指令确实把 partial 从 55 降至 51、FP 从 25 降至 23，但没有保住总召回。新增缺失主要发生在正常 Dreamer/Grounder 输出，而 missing-recovery 只触发 2 批且其中 1 批再次 invalid JSON，因此它无法补回 14 条输出差额。典型仍复现的坏例包括：

- “hundred-year flood”、分析师认为 US listing 改善估值等背景但业务有效的事实仍未成为 candidate。
- compound Gold 被拆成多个局部 Mention，例如 capex 的 Q4 与 FY2026 两部分、合作与供应协议两部分。
- assertion/qualifier 不完整，例如 planned 与 actual、估值缺少与 Nvidia 的 benchmark。
- 新增缺失集中于 30 篇中的少数长文，`Why Everyone Is Talking About Micron`、`MU Stock Soars...`、KOSPI 文各有 6 个 FN。

因此，Grounder recovery Prompt 可以保留，但不能宣称本轮 Mention 修复成功。下一轮应优先检查 candidate 生成覆盖和正常 Grounder disposition，不应继续扩大 recovery 请求或靠接受 umbrella 换 Recall。

## 7. Field 质量

### 7.1 Gold 全口径

| Field | R4 | 本轮 | 变化 |
| --- | ---: | ---: | ---: |
| predicate | 80.22% | 78.36% | -1.86pp |
| participant | 82.09% | 77.15% | -4.94pp |
| metric | 79.47% | 73.37% | -6.10pp |
| fiscal period | 93.33% | 87.01% | -6.32pp |
| total | 81.90% | 77.56% | **-4.34pp** |

### 7.2 排除整条 Mention 缺失后的条件准确率

| Field | R4 | 本轮 | 变化 |
| --- | ---: | ---: | ---: |
| predicate | 92.67% | 95.45% | +2.78pp |
| participant | 94.83% | 93.64% | -1.19pp |
| metric | 87.28% | 85.38% | -1.90pp |
| fiscal period | 97.22% | 98.53% | +1.31pp |

全口径下降主要由 Mention 缺失扩大：本轮 `MISSING_OUTPUT` 对 predicate/participant/metric/fiscal 分别贡献 48/47/28/9 个错误，R4 为 36/36/17/3。已有输出上的 predicate 和 fiscal 反而改善；participant 常见错误是遗漏信息来源/判断主体（IDC、Counterpoint），metric 常见错误是缺少 comparison、benchmark、单位或 compound 中第二个量。Field 路径本轮未修改，不能把全部下降归因于 Field resolver；首要根因仍是上游 Recall。

## 8. N9 / Atomic Identity

### 8.1 原评估器问题

原始报告为：

| 指标 | R4 原口径 | 本轮原口径 |
| --- | ---: | ---: |
| MERGE Precision | 84.62% | 72.22% |
| conditional Recall | 86.84% | 85.25% |

二次排查发现，本轮 72 个 MERGE 中恰有 20 个 `candidate_event_id` 不在评估器重建的 N7 candidate cards 中；这 20 个又被评估器全部自动计为 incorrect，而没有送给 M4 判断。原因是运行时从 provisional candidate 合并到物化 Atomic ID，评估器只按 N7 candidate root 回读，漏掉了最终 selected target。20 条包括大量显然相同的 FY2026 Q3 revenue、EPS、Q4 guidance 事实，不能当作 Gold 错误合并。

修正方法：从 assignment 时点的 materialized target version 中排除当前 incoming Mention，恢复“合并前 target card”；复用既有候选评审，只把遗漏 selected target 送 M4。对 R4 也用同一算法修正。

### 8.2 修正后对称口径

| 指标 | R4 修正 | 本轮修正 | 变化 |
| --- | ---: | ---: | ---: |
| task | 274 | 260 | -14 |
| candidate coverage | 99.64% | 100% | +0.36pp |
| judgeable SAME opportunities | 86 | 79 | -7 |
| correct MERGE | 76 | 71 | -5 |
| incorrect MERGE | 2 | 1 | -1 |
| MERGE Precision | 97.44% | **98.61%** | +1.18pp |
| conditional MERGE Recall | 88.37% | **89.87%** | +1.50pp |
| CREATE_NEW accuracy | 94.90% | 95.74% | +0.85pp |

N9 Prompt 收紧通过保护线，且没有造成 Recall 下跌；可保留。真实剩余问题是 N9 opportunities 随 Mention 总量下降，以及少量选中目标错误，不是原先声称的 20 条大规模误合并。

本轮唯一经修正后仍确认的错误 MERGE 是：`fiscal Q3 revenue $11.3B / +37% YoY` 被合入 `$41.456B / +345.72% YoY` 的 FY2026 Q3 revenue Atomic。主 metric 名称相同，但数值和增长率不可调和、incoming period 又未标准化到 FY2026；这说明同 metric 的 occurrence/period 保护仍有一个真实漏口，但不构成回滚整句 N9 Prompt 的依据。

## 9. Package 准召与碎片化

### 9.1 发布口径

本轮可对齐 91 个当前 Atomic，得到 TP/FP/FN=674/50/165，P/R/F1=93.09%/80.33%/86.24%。R4 发布口径为 93.56%/88.71%/91.07%。两轮对齐集合分别为 91 与 89 个 Atomic，不完全相同，因此该口径只作方向参考：P -0.46pp、R -8.38pp、F1 -4.83pp。

### 9.2 双向互选同集主口径

跨轮双向高置信互选得到 65 个共同且 Gold 可判 Atomic：

| 指标 | R4 | 本轮 | 变化 |
| --- | ---: | ---: | ---: |
| TP pair | 325 | 278 | -47 |
| FP pair | 33 | 34 | +1 |
| FN pair | 56 | 103 | +47 |
| Precision | 90.78% | 89.10% | **-1.68pp** |
| Recall | 85.30% | 72.97% | **-12.34pp** |
| F1 | 87.96% | 80.23% | **-7.73pp** |
| false-merge groups | 4 | 2 | -2 |
| fragmented Gold groups | 4 | 2 | -2 |
| excess components | 5 | 5 | 持平 |
| singleton components | 8 | 6 | -2 |
| missed pair links | 56 | 103 | **+47** |

“fragmented group 数减少”不能解读为碎片化改善：本轮碎片集中到 Micron earnings 大 Gold，28 个事实被拆为 `[24,1,1,1,1]`，产生 102 个 missed links；R4 为 `[26,1,1]`，产生 53 个 missed links。碎片组更少，但单组破坏更重。

本轮四个被拆出的 earnings 子事实为：SCA 累计收入承诺、提高 excess cash return、笼统 Q3 beat、季度股息。N13 将它们判为不同 Package 的理由集中在“不同 trusted artifact/anchor”“独立资本回报或 dividend episode”。这里暴露的是 Gold 的父事件边界与当前 artifact/anchor 可信度不一致：同一 earnings disclosure 的不同来源/局部 hint 被提升成互斥父身份。

## 10. Wave C 与 N13 Apply 的直接归因

### 10.1 Wave C

- candidate pairs：64；M3 tasks：56。
- M0 redirects：2；M3 redirects：12；合计 14。
- hard-boundary candidate skips：671。
- 边界命中边际计数：reaction 458、market measure 168、issuer 95、analyst 4、instrument 3、period 1；同一 pair 可同时命中多项。
- 主要单一组合：reaction-only 413、market-measure-only 156、issuer-only 39。

671 是 scheduler 候选过滤审计次数，不代表 671 个原本会送 M3 的最终 pair，因此不能用它直接估算 Recall 损失；它证明共享边界已前置生效并解释了 Wave C Input 大降。market measure/instrument/issuer 边界在业务上仍合理，没有发现它们把 earnings 内不同 child metric 当成硬冲突。

### 10.2 N13 pair-local Apply

- `SAME_PACKAGE` 决策 42 条；eligible 10 条；选中并实际 Apply 6 个 hub plan、吸收 10 个 source Package。
- Apply 后只发起 1 次 M1 embedding；没有额外 M3。
- 18 个 SAME 因弱边界未 Apply。
- final boundary blocked 0；post-decide 33,413 ms，其中 pair-local Apply 26,792 ms。
- late admission 的 wall check 实际为 false（observed ratio 47.10%），但 Apply 仍执行，证明“budget 只做 telemetry、不再丢弃已付费 Decide”的实现生效。

六个 Apply plan 中，可在共同 Gold 上直接审计的结果：

1. **市场综述 plan：明确错误。** 目标原含 KOSPI 与 SK Hynix，随后吸收 Nikkei、Dow、Stoxx；五个 Atomic 属于五个不同 Gold Package。三个模型 reason 都把“同一 source/market roundup”当作同一 parent container，直接违反 Prompt 的 “shared source alone is not parent identity”。这是当前 false-merge group 中最严重的新簇。
2. **Micron earnings plan：主体正确。** HBM sold-out 与 Q3 earnings 同属 Gold；另一条 consensus Atomic 未进入共同对齐集。目标簇原先已混入 supplier cleanroom reallocation，因此 Apply 没制造该旧污染，但进入了已有污染大簇。
3. **Qualcomm data-center plan：共同 Gold 可见部分正确。** Dragonfly 与 non-handset/data-center strategy 属同一 Gold；另一个 hyperscale deal 未进入共同对齐集。
4. Sandisk scheduled earnings/forecast、US market recap、SCA guidance 三个 plan 因当前共同 Gold 覆盖不足，不能宣称正确。

零 LLM R4 反事实在真实运行前已发出相同预警：打开 N13 Apply 后 P/R 从 93.56%/88.71% 变为 88.82%/93.89%，即 Recall 上升但 Precision 明显受损；V2 boundary 仍只能恢复至 89.14%/93.76%。真实运行确认该风险不是纯离线假设。

结论：当前 Tier A/B 与 final boundary gate 没有阻止“same source/article/roundup = same parent”的语义捷径。既然 full Decide 已经误判，pair-local Apply 会把局部 pair 错误物化为多成员簇；在补足 source-only/container-only 的确定性拒绝条件前，应回滚 Apply 开关，而不是删除 Decide、DTO 或 telemetry。

## 11. 验收门槛

| 指标 | 门槛 | 本轮 | 结论 |
| --- | ---: | ---: | --- |
| 成功率 | 30/30 | 30/30 | 通过 |
| 总墙钟 | ≤3,541,139 ms | 3,385,244 | 通过 |
| 总 Token | ≤4,037,710 | 3,324,234 | 通过 |
| Mention Precision | ≥70.80% | 71.54% | 通过 |
| Mention Recall | ≥72.39% | 69.40% | **失败** |
| Mention partial | <55 | 51 | 通过 |
| Mention FN | ≤74 | 82 | **失败** |
| N9 MERGE P（修正） | ≥R4 修正 97.44% | 98.61% | 通过 |
| N9 conditional R | ≥85% | 89.87% | 通过 |
| candidate coverage | 100% | 100% | 通过 |
| Package 同集 P | 不低于 R4 | -1.68pp | **失败** |
| Package 同集 R | 不低于 R4 | -12.34pp | **失败** |
| Package 同集 F1 | 不低于 R4 | -7.73pp | **失败** |
| false-merge groups | ≤4 | 2 | 表面通过，但新增严重市场簇 |
| fragmented groups | ≤4 | 2 | 表面通过，但 missed links 恶化 |
| singleton components | ≤8 | 6 | 通过 |
| Evidence 异常致文档失败 | 0 | 0 | 通过 |

整体结论：**效能验收通过，N9 修正口径通过，Mention/Field/Package 质量验收失败。**

## 12. 回滚与保留建议

### 12.1 建议立即回滚业务启用

1. **N13 pair-local Apply 解锁。** 恢复为不 Apply 或显式 feature flag off；保留 full Decide 和所有审计。理由是已有明确 Gold FP，不需要等待更多样本证明风险。
2. 不回滚整个 Late Convergence：只撤销消费 SAME 的物化动作，避免丢掉已付费决策和后续分析价值。

### 12.2 保留

- Wave C 唯一 Package 字典、compact boundary DTO。
- N13 尾批平衡。
- 合并后单批 M1 embedding。
- 细分 telemetry。
- instrument/market measure/issuer/reaction 等高置信边界及 candidate 前置过滤。
- N9 Prompt 收紧；修正口径显示 Precision 与 Recall 同时提升。
- Grounder recovery Prompt；它降低 partial/FP，但不能独立解决 Recall。
- selected target 恢复后的 N9 评估器修复。

### 12.3 下一轮窄修复，不建议扩大系统复杂性

1. N13 Apply 若要重新启用，增加一个现有编排层 guard：当 SAME 的唯一正证据是 shared source/article/roundup，且没有 trusted parent artifact/canonical anchor/member identity support 时，不 Apply。无需新 LLM、Schema 或 reasoning 字段。
2. 校准 canonical anchor 的“trusted”来源：source-derived/local hint 不能因为规范化后 ID 不同就成为硬 artifact conflict；只有可证明为两个不同现实父 occurrence/artifact 时才硬阻断。
3. 对 Micron earnings 四个 singleton 做只读回放，验证分别是 N12 candidate 未覆盖、Wave C hard skip、N13 DIFFERENT，还是 Apply eligibility 未通过；修复应落在首个失败位置，不添加第二轮 convergence。
4. Mention Recall 回到 candidate 生成和正常 Grounder disposition 排查；保留 missing recovery 的小范围角色，不提高复杂节点 batch，也不增加全量 repair。
5. Package 复测必须继续报告 `missed_pair_links` 和大 Gold 的 component sizes，不能只看 fragmented group 数。

## 13. 本轮实际 Prompt 变更

N13：

> Choose SAME_PACKAGE only when the evidence identifies one parent occurrence or continuing matter. Missing child detail is not a boundary, but shared topic, source, entity, or family alone is not parent identity.

> Give one short reason without restating the cards.

N9：

> Treat wording, granularity, or omitted detail as non-boundaries only when the supplied referent, occurrence, object/action, and facet evidence still identifies one minimal fact.

Grounder missing recovery：

> Return every supplied candidate exactly once. Recover the smallest complete atomic fact or facts supported by its evidence; split independent facts and reject an umbrella exhausted by recovered children. Missing optional detail alone is not a defect. Return no other IDs.

Wave C：

> Decide whether both cards identify one specific parent occurrence or continuing matter. Different child facts may share that parent; shared topic, source, or entity alone does not prove it. Use the supplied scope differences when deciding the parent boundary. Return each pair once as SAME_PARENT, DIFFERENT_PARENT, or UNCERTAIN.

这些语句与当前 Schema/编排对齐，长度受控。市场综述坏例说明，仅靠 Prompt 无法约束所有 source-container 捷径，必须在 Apply 层使用已有结构化信号兜底。

## 14. 最终判断

本轮不是“优化全面失败”：它把总 Token 降了 17.67%、N13 Input 降了 60.22%、墙钟降了 4.40%，并使修正口径的 N9 P/R 同时提高。失败集中在两个可以独立处理的业务边界：

- N13 pair-local 把错误 SAME 真正 Apply，制造市场综述误合并；
- anchor/artifact 父身份过强，扩大了 earnings 大 Gold 的碎片化。

因此最平衡的处置不是整体回滚 `f5bc245`，而是关闭 pair-local Apply、保留效能基础设施，再窄调 anchor/artifact 的可信边界。Mention Recall 则是独立的上游覆盖问题，不应通过放松 Package 或 Atomic 合并来补偿。
