# CDECR Atomic / Package 碎片化离线报告（2026-08-01）

## 口径

本轮新增 `scripts/cdecr_fragmentation_metrics.py`，输入固定的 Source Gold occurrence 映射：

```text
source_occurrence_id
→ gold_atomic_id / gold_package_id
→ redirect 后 predicted_atomic_id / predicted_package_id
```

脚本只离线计算三项：`Pair Fragmentation = 1 - Pair Recall`、Fragmented Cluster Rate 和
Component Profile，并始终把 Pair Precision 放在旁边。它不进入 Prompt、Schema、候选召回或
Apply，也不会写回 Registry。Package 同时支持 end-to-end 与先折叠同一 Gold Atomic 的
conditional 口径。

## 三轮可比结果

| 轮次 | 层级 | Pair Precision | Pair Fragmentation | Fragmented Cluster Rate | 典型组件 |
| --- | --- | ---: | ---: | ---: | --- |
| 2026-07-28 | Atomic | 41.84% | 23.08% | N/A | 13 个明确跨 Atomic FN 事实组 |
| 2026-07-30 | Atomic | 56.45% | 60.89% | N/A | Q3 revenue `8+1+1+1`；EPS 分 4 簇 |
| 2026-07-31 | Atomic | 71.37% provisional | 6.27% provisional | N/A | adjusted margin 3 簇；no-line-of-sight 2 簇 |
| 2026-07-28 | Package E2E | 91.54% | 26.46% | 4/5 = 80.00% | earnings 3 组件；Qualcomm 3 组件 |
| 2026-07-30 | Package E2E | 80.43% | 61.05% | N/A | earnings `23+12+3+2+1` |
| 2026-07-31 | Package E2E | 85.82% provisional | 1.16% provisional | N/A | 51-Atomic 大包强聚合 |
| 三轮 | Package conditional | N/A | N/A | N/A | 历史工件未保存稳定 Source-occurrence 映射 |

`N/A` 不是漏算：2026-07-30/31 使用的是跨轮 provisional Gold 投影，现有报告没有保存每个
Source occurrence 到 Gold cluster 与预测 cluster 的完整机器映射，无法得到可靠的多成员 Gold
cluster 分母。2026-07-28 Package review 保存了完整 5 个多成员 Gold Package，因而只有这一格能
严谨得到 4/5。用已列出的 bad cluster 数量反推其他轮次分母会制造假精度，本报告拒绝这样做。

## 结果解释

- 2026-07-30 的主要问题是过拆：Atomic 和 Package Fragmentation 分别恶化到 60.89% 和
  61.05%，其中 N9 whole-task fallback 与 Package anchor 丢失是明确放大因素。
- 2026-07-31 的低 Fragmentation 不能单独视为进步。Atomic Precision 虽升至 71.37%，仍有
  18-member revenue、12-member EPS/guidance/SCA 污染簇；Package Recall 98.84% 更主要由一个
  51-Atomic 大包贡献，Pair Precision 只有 85.82%。这是“低碎片化但过度聚合”的典型反例。
- 因此后续发布门必须是 Precision 与 Fragmentation 联合门，且必须检查 Component Profile；不得
  通过扩大簇降低 fragmentation。

## 2026-07-28 完整 Package Component Profile

| Gold Package | Gold成员 | 预测组件 | 分布/现状 | 最大组件覆盖 |
| --- | ---: | ---: | --- | ---: |
| Micron FQ3 2026 earnings | 29 | 3 | 25+2+2（主组件另混入 reaction） | 86.21% |
| Qualcomm data-center strategy | 3 | 3 | 1+1+1 | 33.33% |
| IDC Apple 2026 outlook | 2 | 2 | 1+1 | 50.00% |
| Counterpoint Apple price outlook | 2 | 2 | 1+1 | 50.00% |
| Memory supplier reallocation | 2 | 1 | 2 | 100.00% |

## 后续使用约束

下一次完整验收必须先生成一个唯一的 redirect-resolved mapping JSON，再运行离线脚本；缺失、
EXTRA、AMBIGUOUS occurrence 应显式保留在映射审计中，但只把可判 occurrence 放入强指标。这样
才可同时给出 Atomic、Package E2E 和 Package conditional 的三项完整指标。

