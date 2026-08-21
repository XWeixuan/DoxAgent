# CDECR Relevance Gate v2 可复现性与 v1 严谨比较

日期：2026-08-12  
语料：MU relevance 30 v1，30篇 / 409 frozen Dreamer candidates  
模型：DashScope `deepseek-v4-flash-0731`  
复测运行：`runs/frozen_gate_v2_retest_20260812_live1_retry1`  
原v2：`runs/frozen_gate_v2_20260812_live1_retry1`  

## 结论

v2复测不仅复现了原v2质量，而且取得更好结果。结合两次v1和两次v2，当前证据支持：**v2是现有版本中质量最优、最均衡的版本，但仍只能保持Shadow。**

- v2两轮平均 Relevant recall 92.40%、drop precision 91.90%、valid accuracy 93.70%，全面高于v1两轮平均的89.22%、88.98%、92.42%。
- v2最差一轮仍有91.67% recall和91.10% drop precision，均高于v1最好一轮的90.20%和89.95%。
- v2两轮均为0/80 Direct FN；v1两轮均为1/80 Direct FN。
- v2复测相对原v2修复12个错误，只新增5个错误；最终FN/FP从17/11降到14/7。
- v2候选标签一致率95.63%，低于v1的98.20%，说明v2在间接相关边界上更活跃、波动更大；但波动没有破坏总体优势。

因此原v1的高accuracy确实包含有利采样，且v1不是当前最优。v2当前应作为后续优化与复验的主基线。

## 精确复现条件

本次恢复了原v2的完整运行条件：

- `RELEVANCE_PROMPT_VERSION = relevance-filter-v2`
- 第二轮每个event传 `id + statement + exact_evidence`
- 不显式传 `containing_paragraph`
- Prompt SHA256与原v2一致：`3d560276af057b541718d12a2fb94a757a3261f33144f093e3717bf30bae940e`
- 进程内MU profile与原v2一致：`Micron Technology (MU); DRAM, NAND and HBM memory semiconductors`
- 相同Gold v2、模型、冻结输入和4-worker并发

MU profile仅通过本次进程环境注入，没有永久修改 `.env`。外发仍只有30篇正文和409条冻结candidates；mentions、Gold和事后评分映射未进入请求。

## 两次v2指标

| 指标 | 原v2 | v2复测 | 两轮均值 |
| --- | ---: | ---: | ---: |
| Relevant recall | 91.67% | **93.14%** | **92.40%** |
| Relevant precision | 94.44% | **96.45%** | **95.45%** |
| Dropped-candidate precision | 91.10% | **92.71%** | **91.90%** |
| Valid accuracy | 92.80% | **94.60%** | **93.70%** |
| Direct FN | 0/80 | 0/80 | 0 |
| FN / FP | 17 / 11 | **14 / 7** | 15.5 / 9 |

仅389条有效标签的复测混淆矩阵：

| | Gold Relevant | Gold Irrelevant |
| --- | ---: | ---: |
| Predicted Relevant | TP 190 | FP 7 |
| Predicted Irrelevant | FN 14 | TN 178 |

### 复测切片

| 切片 | Recall | Drop precision | Valid accuracy |
| --- | ---: | ---: | ---: |
| hard indirect | 89.72% | 73.81% | 88.81% |
| mixed | 93.48% | 97.54% | 97.01% |
| mostly high | 100.00% | N/A | 100.00% |
| very low / none | N/A | 100.00% | 100.00% |

hard-indirect仍是稳定短板，但mixed在复测中明显改善。

## 两次v2的逐候选稳定性

| 项目 | 数量 |
| --- | ---: |
| 两次均有效 | 389 |
| 标签一致 | 372 |
| 标签变化 | 17 |
| 一致率 | 95.63% |
| 原v2错误在复测修复 | 12 |
| 原v2正确在复测变FN | 4 |
| 原v2正确在复测变FP | 1 |

与v1不同，v2的波动方向净改善：12条修复对5条新增错误。原v1复测则是0条修复、7条新增错误。

### 修复的12条原v2错误

- D072 ×3：AMD hyperscaler deals、data-center revenue、OpenAI 6GW GPU deployment。
- D190 ×3：SK Hynix NAND/HBM/DRAM市场份额。
- D293 Apple margin pressure。
- D027 AMD独立证券表现FP。
- D167 Oracle整体指引FP。
- D025 Intel业绩/股价FP。
- D045 HPE EPS与Dell/HPE一般盈利预期FP ×2。

这些修复说明v2确实能在部分运行中正确建立GPU/HBM、竞争份额、memory-price成本路径，也能压制部分宽上下文财务/证券错召；但单次标签仍有波动。

### 两次共同的10条FN

- D095 ×3：Ciena earnings、AI optical component demand、DCI market。
- D001 ×2：SanDisk估值与历史证券表现。
- D097 ×4：Samsung、SanDisk、Kioxia、SK Hynix评级/目标价。
- D102 ×1：RTX Spark PC SoC。

这10条是v2最稳定的召回缺口，集中在光互联互补需求、内存竞争者证券事件和较弱的AI-PC产品表述。

### 两次共同的6条FP

- D021 DeepSeek泛AI事件。
- D147 Supermicro泛hardware components采购。
- D025 AMD整体revenue/EPS/net income ×3。
- D045 Dell目标价上调。

它们是v2最稳定的错召边界：泛AI、泛硬件和文章级data-center/memory narrative仍会被过度继承。

### 复测新增5条错误

- FN：D102 RTX Spark 1 petaflop性能。
- FN：D138 Broadcom AI销售、Oracle hyperscaler RPO、NVIDIA margin ×3。
- FP：D180 SK Hynix单日股价上涨5.79%。

D138三条从原v2正确变为复测错误，说明较远AI基础设施传导仍具采样不稳定性。D180是内存竞争者的纯证券表现，按Gold v2缺少局部具体路径。

## v1与v2的两轮比较

| 版本统计 | Recall | Drop precision | Accuracy | Direct FN | 标签一致率 |
| --- | ---: | ---: | ---: | ---: | ---: |
| v1最好轮 | 90.20% | 89.95% | 93.32% | 1 | — |
| v1最差轮 | 88.24% | 88.00% | 91.52% | 1 | — |
| **v1两轮均值** | **89.22%** | **88.98%** | **92.42%** | **1** | **98.20%** |
| v2最好轮 | 93.14% | 92.71% | 94.60% | 0 | — |
| v2最差轮 | 91.67% | 91.10% | 92.80% | 0 | — |
| **v2两轮均值** | **92.40%** | **91.90%** | **93.70%** | **0** | **95.63%** |

v2相对v1两轮均值：

- recall +3.18pp
- drop precision +2.92pp
- accuracy +1.28pp
- Direct FN从1降至0

即使采用保守的“v2最差轮 vs v1最好轮”，v2的recall和drop precision仍分别高1.47pp和1.15pp；v1最好轮accuracy高0.52pp，但其Direct FN和较低召回不适合前置删除Gate。

## 版本判断

1. **v1不是当前最优。** 两次复跑显示其高precision行为稳定，但原最高分包含有利采样，且两轮均有同一Direct FN。
2. **v2是当前最优候选。** 两轮均在最关键的recall、drop precision和Direct保护上超过v1，并且复测没有回归到v1水平。
3. **v2不是完全稳定。** 95.63%标签一致率和D138等边界波动意味着仍需原因码、确定性保护或多次投票/规则兜底。
4. **仍不能Enforce。** v2最差轮recall仅91.67%，hard-indirect drop precision约72.5%至73.8%，稳定共同FN仍有10条。
5. 后续优化应以v2为基线，不应回退v1，也不应采用显式paragraph的v3当前实现。

## Provider与fail-open

- 28/30文档成功；D017 14条、D089 6条在首轮和一次定向重试中均为 `provider_arrearage`，随后熔断。
- 20条全部KEEP_FAIL_OPEN，不计作模型正确分类。
- 首次执行的终端命令在约124秒达到命令超时，但脚本已完整写出30篇/409条、报告和映射，且无残留进程；产物通过独立完整性校验后纳入本报告。
- 复测成功调用：input 115,632、cached input 48,128、output 43,568、reasoning 37,755 tokens。
- provider累计latency 454,305ms；包含定向重试为458,562ms。缓存和生成波动使其不适合从单轮推断成本。

## 验收判定

| 项目 | 判定 |
| --- | --- |
| 原v2运行条件复现 | PASS |
| 输入隔离 | PASS |
| v2质量复现 | PASS，复测更高 |
| v2可作为当前主基线 | **YES** |
| v2可启用Enforce | **NO，继续Shadow** |

## 可复核产物

- `runs/frozen_gate_v2_retest_20260812_live1_retry1/report.json`
- `runs/frozen_gate_v2_retest_20260812_live1_retry1/predictions.jsonl`
- `runs/frozen_gate_v2_retest_20260812_live1_retry1/candidate_mention_mapping.jsonl`
- 原v2：`runs/frozen_gate_v2_20260812_live1_retry1/predictions.jsonl`
- 两次v1：`runs/frozen_gate_20260811_retry1/predictions.jsonl`、`runs/frozen_gate_v1_retest_20260812_live1_retry1/predictions.jsonl`

SHA256：

- v2 Prompt：`3d560276af057b541718d12a2fb94a757a3261f33144f093e3717bf30bae940e`
- Retest report：`9da2d3f51667e7153d9adafdb3c59265256cd00a81b9c59f5c8b668fe58350d5`
- Retest predictions：`316511de4cc85b063a4ae88763036e60f77769aa87c432ba4ab9cef14210f7f7`
