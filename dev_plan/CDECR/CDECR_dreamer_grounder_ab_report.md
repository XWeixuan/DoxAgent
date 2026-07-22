# CDECR Dreamer → Grounder 对照实验报告

## 结论

在本次经过全文形态审核的 10 篇 MU 新闻上，Dreamer → Grounder 相比纯 Grounder
存在明显且稳定的质量优势，主要收益来自事件召回，同时精确率也小幅提高。当前证据支持
保留 Dreamer 作为单文档事件发现链路的高召回前置节点。

预先声明的“明显差异”判据为：两组 macro-F1 绝对差至少 0.05，且逐文档配对
Bootstrap 95% 置信区间不包含 0。本次结果满足该判据。

## 实验控制

- 数据来自 DoxAtlas Supabase `public.raw_media` 的受限只读 GET，查询固定为
  `US / MU / 2026-06-25 UTC`。
- 从 87 篇合格候选中逐篇阅读全文，最终选择 10 篇、6 个来源、正文 1,382–4,032
  字符的单主题文章。
- 明确排除了超长聚合页、市场收盘综述、analyst-calls 聚合、视频转录和
  earnings-call highlights。
- 两组都在 Grounder 输出后停止，不进入 Judge、归一、Atomic Event 或 Package。
- 两组使用相同的 M3 `qwen3.7-plus`、JSON Mode、`effort=none`、Grounder Schema
  和严格 EvidenceLocator 回映。
- Dreamer 组使用 M2 `deepseek-v4-flash` 生成候选；纯 Grounder 组不生成或伪造
  Dreamer candidates，只使用临时 direct-mode Prompt 分支。
- 纯 Grounder Prompt 修改前已备份；实验结束后已恢复，活动文件与备份
  SHA-256 完全一致，且不再包含 direct-mode marker。
- M4 `qwen3.7-max` 按文档随机将两组标为 X/Y，建立同一份原子 gold events，
  并将每个 extracted mention 恰好分类为 supported 或 unsupported。

## 汇总结果

| 指标 | Dreamer → Grounder | 纯 Grounder | 差值 |
|---|---:|---:|---:|
| 完成文档 | 10/10 | 9/10 | +1 |
| Grounder Mentions | 121 | 85 | +36 |
| M4 Gold Events | 97 | 97 | — |
| Supported Mentions | 90 | 59 | +31 |
| Recalled Gold Events | 90 | 53 | +37 |
| Micro Precision | 74.38% | 69.41% | +4.97 pp |
| Micro Recall | 92.78% | 54.64% | +38.14 pp |
| Micro F1 | 82.57% | 61.15% | +21.42 pp |
| Macro F1 | 81.12% | 60.28% | +20.84 pp |

- 全部 10 篇的 macro-F1 差值为 `+0.2084`，配对 Bootstrap 95% CI 为
  `[+0.0465, +0.3986]`。
- Dreamer 组逐文档 6 胜、1 平、3 负。
- 排除纯 Grounder 唯一失败文档后，9 个完整配对的 macro-F1 差值仍为
  `+0.1316`，95% CI 为 `[+0.0189, +0.2564]`，结论不依赖该失败样本。

## 逐文档结果

| 标题（缩写） | DG 数量 | DG F1 | G 数量 | G F1 | 结果 |
|---|---:|---:|---:|---:|---|
| Tim Cook / memory crisis | 8 | 0.667 | 14 | 0.727 | 纯 G 胜 |
| SK Hynix US listing | 13 | 0.889 | 13 | 0.889 | 平 |
| Defiance 2X DRAM ETF | 5 | 0.889 | 4 | 0.500 | DG 胜 |
| Micron–Anthropic collaboration | 9 | 0.800 | 7 | 0.845 | 纯 G 胜 |
| Micron CAPEX / cash return | 7 | 0.923 | 6 | 0.800 | DG 胜 |
| MU Q3 / Citi target | 11 | 0.632 | 10 | 0.667 | 纯 G 胜 |
| Wedbush chip thesis | 16 | 0.667 | 8 | 0.250 | DG 胜 |
| Micron record results | 22 | 0.900 | 0 | 0.000 | DG 胜；纯 G 失败 |
| Memory supply outlook | 10 | 0.824 | 12 | 0.750 | DG 胜 |
| Micron investment analysis | 20 | 0.923 | 11 | 0.600 | DG 胜 |

纯 Grounder 的失败文档在初始调用和一次修复调用后都出现
`evidence_segment_slice_mismatch`。按照两组共用的“一次修复后失败”规则，没有进行
第三次模型调用或启发式伪造证据。

## 解释与边界

Dreamer 的主要作用是把文档展开为高召回、带证据的候选空间，使 Grounder 更容易覆盖
次级事实、计划、预期和复合报道中的独立事件。它并非在每篇文章上都占优：在三篇相对
紧凑或事实边界清楚的文章上，纯 Grounder 的 F1 略高；Dreamer 也产生了更多绝对数量的
unsupported mentions（31 对 26）。因此后续优化重点应是减少 Dreamer 候选冗余，而不是
删除 Dreamer。

本结论仍有三个限制：样本量为 10；语料集中于同一 ticker 和交易日；质量标注由 M4
辅助而非独立人工金标。它足以证明当前样本上的明显差距，但不应直接外推为所有市场和
文档类型上的固定收益。

## 完整产物

- `.tmp/cdecr/experiments/dreamer_grounder_ab/dreamer_grounder.json`
- `.tmp/cdecr/experiments/dreamer_grounder_ab/grounder_only.json`
- `.tmp/cdecr/experiments/dreamer_grounder_ab/paired_m4_review.json`
- `.tmp/cdecr/experiments/dreamer_grounder_ab/comparison.json`
- `.tmp/cdecr/experiments/dreamer_grounder_ab/comparison.md`
- `dev_plan/CDECR/experiments/dreamer_grounder_ab_manifest.json`
