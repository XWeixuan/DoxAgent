# CDECR Relevance Gate v1 可复现性真实测试

日期：2026-08-12  
语料：MU relevance 30 v1，30篇 / 409 frozen Dreamer candidates  
模型：DashScope `deepseek-v4-flash-0731`  
复测运行：`runs/frozen_gate_v1_retest_20260812_live1_retry1`  
原v1基线：`runs/frozen_gate_20260811_retry1`  

## 结论

原v1的高分可以部分复现，但不是稳定上界：

- 两次v1在389条有效候选上的标签一致率为98.20%（382/389），说明核心行为高度稳定，并非整轮偶然。
- 仅7条标签发生变化，但7条全部是“原v1正确 -> 本次错误”；没有任何原v1错误在复测中被修复。
- Relevant recall从90.20%降至88.24%，drop precision从89.95%降至88.00%，valid accuracy从93.32%降至91.52%。
- 两次v1均有同一条Direct FN：D021的Micron低于20倍PE估值事件。

因此，原v1成绩包含一小部分有利采样，不能认定v1是当前确定最优版本。更准确的判断是：v1具有稳定的高精度、偏严格行为，但召回和综合质量对少量边界候选存在约1.8%的采样波动。

v2单次运行在recall、drop precision和accuracy上均高于本次v1复测及两次v1均值，但v2也只运行过一次，尚不足以证明v2稳定最优。当前所有版本仍不满足Enforce要求，继续Shadow。

## 回退边界

运行时Gate已完整恢复原v1：

- `RELEVANCE_PROMPT_VERSION = relevance-filter-v1`
- 第二轮事件只传 `id + statement`
- 移除 `exact_evidence`
- 移除显式 `containing_paragraph`
- 恢复v1的阈值、list-membership、业务关系枚举和“never invent”措辞
- 当前 `.env` 没有MU profile，target仅为 `Micron Technology (MU)`，与原v1实际运行一致

`relevance_filter.py`和Prompt相对仓库原v1无内容diff。

评测基础设施继续使用Gold v2和修正后的Direct计分。这些字段不进入模型请求，只用于把原v1和复测放在同一标尺上，避免恢复旧Gold边界或旧Direct漏计错误。

外发仍只有30篇正文和409条冻结candidates；mentions、Gold、`relevance_gold`及事后评分映射没有进入请求。

## 原v1与复测指标

原v1 raw predictions按Gold v2重新计分。

| 指标 | 原v1 | v1复测 | 变化 |
| --- | ---: | ---: | ---: |
| Valid predictions | 389 | 389 | 0 |
| Fail-open | 20 | 20 | 0 |
| Simulated drops | 199 | 200 | +1 |
| Relevant recall | **90.20%** | 88.24% | -1.96pp |
| Relevant precision | **96.84%** | 95.24% | -1.60pp |
| Dropped-candidate precision | **89.95%** | 88.00% | -1.95pp |
| Valid accuracy | **93.32%** | 91.52% | -1.80pp |
| Relevant F1 | **93.40%** | 91.60% | -1.80pp |
| Direct FN | 1/80 | 1/80 | 持平 |

仅389条有效标签的混淆矩阵：

| | TP | FP | FN | TN |
| --- | ---: | ---: | ---: | ---: |
| 原v1 | 184 | 6 | 20 | 179 |
| v1复测 | 180 | 9 | 24 | 176 |

### 切片

| 切片 | 原v1 recall | 复测 recall | 原v1 drop precision | 复测 drop precision | 原v1 accuracy | 复测 accuracy |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| hard indirect | **87.85%** | 84.11% | **71.74%** | 64.58% | **88.81%** | 84.62% |
| mixed | 84.78% | 84.78% | 94.40% | 94.35% | **94.01%** | 93.41% |
| mostly high | 100.00% | 100.00% | N/A | N/A | 100.00% | 100.00% |
| very low / none | N/A | N/A | 100.00% | 100.00% | 100.00% | 100.00% |

退化主要来自hard-indirect，mixed/high/low基本稳定。

## 逐候选稳定性

| 项目 | 数量 |
| --- | ---: |
| 两次均有效 | 389 |
| 标签一致 | 382 |
| 标签变化 | 7 |
| 一致率 | 98.20% |
| 原错误在复测修复 | 0 |
| 原正确在复测变FN | 4 |
| 原正确在复测变FP | 3 |

两次共同错误为20 FN + 6 FP，说明v1的主要偏差是稳定的，不是随机：

- 共同FN：D021 Micron直接估值；D072 AMD GPU/data-center；D095 Ciena AI interconnect；D001/D097内存竞争者证券事件；D045 HPE memory-linked call；D102 RTX Spark；D138 AI基础设施；D293 Apple memory-price margin传导。
- 共同FP：D147泛hardware components；D167 Oracle整体指引/股价；D025 AMD整体revenue/EPS/net income。

### 本次新增4条FN

- D001 SanDisk Q3 revenue guidance +171%。
- D001 SanDisk gross margin guidance 65.9%。
- D045 HPE Q1 revenue增长18%。
- D045 HPE FY2026 revenue/free-cash-flow guidance。

这些都属于竞争者或客户/硬件公司整体经营指标。它们从原v1的Relevant变为Irrelevant，符合v1偏严格的边界，且说明原v1在这些边缘间接事件上恰好给出了更有利标签。

### 本次新增3条FP

- D021 DeepSeek更便宜且性能相当。
- D061 Kioxia ADR上涨460%。
- D061 Kioxia股价年内翻逾四倍。

DeepSeek是泛AI事件；Kioxia虽为明确NAND竞争者，但Gold v2要求证券表现必须有局部具体传导路径。三条均说明v1对文章全文背景的继承并不完全稳定。

## “v1是否当前最优”的判断

| 版本/统计 | Recall | Drop precision | Accuracy | Direct FN |
| --- | ---: | ---: | ---: | ---: |
| 原v1 | 90.20% | 89.95% | **93.32%** | 1 |
| v1复测 | 88.24% | 88.00% | 91.52% | 1 |
| 两次v1均值 | 89.22% | 88.98% | 92.42% | 1 |
| v2单次 | **91.67%** | **91.10%** | 92.80% | **0** |

证据支持以下结论：

1. v1不是“整轮偶发”：98.20%的候选标签可复现，26条核心错误全部复现。
2. 原v1的最高accuracy存在偶发成分：7个波动标签全部恰好在原轮正确，这种单向变化不能视为稳定优势。
3. v1的高precision相对稳定，但recall较低且Direct保护不完整。
4. v2目前是质量更均衡的候选版本，但必须进行至少一次同配置复测，才能与两次v1的稳定性证据公平比较。
5. 在v2复测前，不应宣称任何版本“当前最优”；可确认的是原v1单轮高点不是可靠发布依据。

## Provider与fail-open

- 28/30文档成功；D017 14条、D089 6条在首轮和一次定向重试中均为 `provider_arrearage`，随后熔断。
- 20条全部KEEP_FAIL_OPEN，不计作模型分类正确。
- 首轮墙钟约91秒。
- 复测成功调用：input 98,797、cached input 18,688、output 29,117、reasoning 23,205 tokens。
- 原v1成功调用：input 98,013、cached input 2,048、output 27,487、reasoning 21,646 tokens。
- token差异包含provider缓存和单次生成波动；30篇困难集不能用于净成本结论。

## 验收判定

| 项目 | 判定 |
| --- | --- |
| 运行时完整回退v1 | PASS |
| 输入与Gold隔离 | PASS |
| v1行为可复现性 | PASS，98.20%标签一致 |
| 原v1最高分稳定复现 | **FAIL** |
| v1可确认为当前最优 | **NO** |
| Enforce | **禁止，继续Shadow** |

## 可复核产物

- `runs/frozen_gate_v1_retest_20260812_live1_retry1/report.json`
- `runs/frozen_gate_v1_retest_20260812_live1_retry1/predictions.jsonl`
- `runs/frozen_gate_v1_retest_20260812_live1_retry1/candidate_mention_mapping.jsonl`
- 原v1：`runs/frozen_gate_20260811_retry1/predictions.jsonl`
- `cdecr_mu_relevance_30_candidates_gold_readjudicated_v2.jsonl`

SHA256：

- v1 Prompt：`b40167a9c8a7c746016d681ee304483ec944f63e14e3f979f9547f2f774b9a93`
- Retest report：`8d4085ecbdd6630d9e6fbfe46cd497404d1ce245c9598c1ce06fbd5661676443`
- Retest predictions：`4dc51e91522360eb1904e5e52db5b5faf8ebe1d3d0f532e5404e937912d93256`
