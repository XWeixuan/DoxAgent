# CDECR N9 P0/P1 正式优化与 15 篇单节点 A/B（2026-08-02）

## 1. 结论

N9 的全部 P0/P1 已落到正式唯一执行路径，不保留 N9 shadow/legacy 双轨。15 篇 fixed-corpus
isolated A/B 只真实运行 B 组，A 组直接复用上一轮持久化 decision 与独立 M4 Gold。

成本目标显著超过预期，但质量门槛没有守住：

| 指标 | A（上轮持久化） | B（本轮真实） | 变化 |
| --- | ---: | ---: | ---: |
| N9 input tokens | 491,383 | 185,588 | **-62.23%** |
| 模型调用数（含 repair/escalation） | 60 | 45 | -25.00% |
| Action accuracy | 92.36% | 90.97% | **-1.39pp** |
| MERGE precision | 95.35% | 93.02% | **-2.33pp** |
| Conditional MERGE recall | 82.00% | 80.00% | **-2.00pp** |
| CREATE_NEW accuracy | 97.87% | 96.81% | -1.06pp |
| fragmented tasks | 9 | 10 | +1 |
| fragmentation rate | 18.00% | 20.00% | **+2.00pp** |
| projected excess Atomic components | 9 | 10 | +1 |
| SAME-candidate link recall | 75.93% | 74.07% | -1.85pp |

因此本轮的客观判断是：压缩成功，质量小幅但明确下降。它没有达到原方案的 precision/recall
下降不超过 0.5pp 门槛，也没有达到 conditional recall 不低于 85% 的门槛。按本轮用户明确要求，
该结果不触发 shadow 回退；代码继续以优化协议为正式路径。

## 2. 正式实现

### 2.1 P0：确定性 pair 移出 LLM

- `PRIMARY_METRIC_FAMILY`、`METRIC`、`COMPLETE_REFERENT`、`ASSERTION_STATE` 命中的
  enforced pair 不再进入模型 payload。
- 编排层生成 `ATOMIC_N9_DETERMINISTIC_NOT_SAME`，保留 candidate ID、规则、轴、
  `sent_to_model=false`，并生成完整 deterministic assessment。
- 部分阻断 task 在模型返回后合并 deterministic 与 LLM assessments；全阻断 task 直接
  `CREATE_NEW`；无候选 task 沿用生产 `CREATE_NEW` 语义。
- 本次 680 个 assignment-time candidate pair 中 383 个被确定性剔除，剔除率 **56.32%**；
  模型实际判断 297 个 pair，candidate coverage 为 **100%**。
- 144 个 task 中 109 个送入模型，33 个因全 deterministic 阻断而跳过，另 2 个原本无候选。

### 2.2 P0：请求级 Atomic Dictionary

正式请求改为 `mentions + atomics + tasks`：同一 Atomic 在一个请求中只定义一次，task edge 仅保留
Atomic ID、适用轴 `R/O/F`、exact flag 与 deterministic warning。旧 `batch_index/batch_count`、
task-local 整卡复制和 legacy payload 发送路径已移除；`CDECR_N9_WIRE_PROTOCOL` 只允许 `on`。

### 2.3 P1：Compact Identity Card

模型只接收 Sidecar 的实际 `referent/occurrence/facet` 值，以及非空 roles、locations、objects 和
紧凑 time tuple。完整 `identity_profile`、`identity_adapter`、`identity_axes`、重复 assertion/period/time
和 compiler metadata 不再发送。Quantity 改为保留 `metric/role/value/unit/raw comparator` 的 tuple。

### 2.4 P1：选择性文本

- Atomic representative claim 按数字/比较符/session/period/state/定性边界的信息增量选择，跳过与
  canonical proposition 高度重复的文本，最多 2 条。
- Incoming evidence 取覆盖 EvidenceSpan 的最小完整句，上限 600 字符。
- `source_claim` 与 evidence 或 canonical proposition 等价时不重复发送。

### 2.5 版本与强制启用

- Engine：`cdecr-cross-document-v20`
- Prompt：`cdecr-cross-document-prompts-v15`
- Wire：`cdecr-cross-document-wire-atomic-dictionary-v9`
- Assignment policy：`atomic-assignment-policy-v4-deterministic-pair-filter`
- 默认 hard-cannot-link 为 `enforce`，N9 wire 为强制 `on`。

## 3. M2/M3/M4 调用策略

三档均使用 DeepSeek 官方 `deepseek-v4-flash` 与 strict function：

| Tier | effort | strict | 真实 schema probe |
| --- | --- | --- | --- |
| M2 | low | true | `AtomicDecisionBatch` 745/77 tokens，通过 |
| M3 | high | true | `AtomicDecisionBatch` 824/85 tokens，通过 |
| M4 | max | true | `JudgeCommandOutput` 3,353/145 tokens，通过 |

真实 B 组出现 2 次 M2 `invalid_json`，两次都由 strict repair 成功恢复；另有 1 次 M3 escalation。
这些调用及其 token/latency 全部计入 B 组，没有从成本中剔除。

### 3.1 Output / thinking token 量级

15 篇 B 组业务运行按实际调用 tier 汇总如下；`output tokens` 是 DeepSeek usage
返回的总 completion token，历史 Registry 没有单独持久化 reasoning 明细：

| Tier / effort | 调用数 | output tokens | 平均每调用 |
| --- | ---: | ---: | ---: |
| M2 / low（含 2 次 repair） | 44 | 230,281 | 5,233.7 |
| M3 / high（escalation） | 1 | 12,616 | 12,616.0 |
| M4 / max | 0 | 0 | 不参与 N9 B 组 |

由于 M3 仅 1 次且请求难度与 M2 不同，上表只能反映本轮真实成本，不能把差值纯归因于 effort。
为观察 thinking token 的纯量级，另以相同 `AtomicDecisionBatch` Prompt、Schema 与期望输出各调用一次，
只改变 effort；DeepSeek 原始 usage 已返回 `reasoning_tokens`：

| effort | input | total output | reasoning / thinking | visible output | thinking 占 output |
| --- | ---: | ---: | ---: | ---: | ---: |
| low | 745 | 68 | 20 | 48 | 29.41% |
| high | 824 | 77 | 29 | 48 | 37.66% |
| max | 837 | 79 | 31 | 48 | 39.24% |

本次单样本中，相对 low，high/max thinking token 分别增加 45%/55%；high 到 max 仅增加约 6.9%。
这是量级探针而非统计显著性结论，且 provider 对同一业务内容在不同 effort 下报告的 input token 也略有差异。
证据文件为 `.tmp/cdecr/n9_ab_20260802/effort_token_probe.json`。

## 4. A/B 方法

- 文档：上一轮 30 篇 fixed corpus 的 D01-D15，共 15 篇、144 个 N9 task。
- A：直接复用
  `.tmp/cdecr/acceptance_20260802_deepseek_v4/current_n9_gold_eval.json` 中上一轮 N9 action，
  不重新调用 A 模型。
- Gold：复用同一文件中独立 M4 对 assignment-time N7 candidates 的逐 pair review。
- B：从上一轮 Registry 的 `ATOMIC_ASSIGNMENT.candidate_refs` 重建 assignment-time Atomic version；
  同批 `provisional:*` 由原 Mention 与 compiled identity 重建 singleton，并校验稳定 ID。
- 每篇内部保持生产 3-task batch、validation、repair 和 M2→M3 escalation；15 篇分成三个互不重叠
  的 5 篇 shard 并行运行，汇总时累计原始 TP/FP/FN/TN 计数，不平均百分比。
- 本轮只评估 N9 节点决策；不 Apply 新 Atomic cluster，因此碎片化报告为 N9 task-level
  fragmented tasks、fragmentation rate、projected excess components 与 SAME-candidate link recall，
  不冒充完整终态 cluster membership Gold。

## 5. 质量变化明细

相对 A 共有 6 个 task 分类变化：

- 新增 2 个 FP：
  - Apple intraday `-0.56%` 被并入“涨价后跌逾 5%”Atomic；session/value/因果边界未守住。
  - Micron Q3 profit `$28.2B/~15x` 被并入宽泛“posted Q3 results” provisional umbrella。
- 修复 1 个旧 FP：连续第五次季度收入纪录不再并入具体 Q3 revenue `$41.46B`。
- 新增 2 个 FN：
  - tight memory beyond 2027 未并入 supply constraints through 2028；
  - Micron YTD gain `>260%` 未并入同一 cluster 的 `233.45% YTD` 表述。
- 修复 1 个旧 FN：SK Hynix `+13% Thursday` 正确并入 June 25 early-trading `>10%`。

汇总计数从 A 的 `TP=41, FP=2, FN=9, TN=92` 变为 B 的
`TP=40, FP=3, FN=10, TN=91`。P0 deterministic filter 本身实现了完整 candidate coverage；质量变化来自
新 payload 表达与新的模型 effort/provider 组合，按用户预先说明不能把差异单独归因于压缩。

## 6. 产物

- 合并报告：`.tmp/cdecr/n9_ab_20260802/n9_ab_15_combined_r2.json`
- 三个 B shard 报告：同目录 `n9_b_15_shard_01_05.json`、`06_10.json`、`11_15.json`
- 三个隔离 Registry：与 shard 报告同名 `.sqlite3`
- schema probe：`.tmp/cdecr/n9_ab_20260802/schema_probe_m234.json`
- 同构 effort token probe：`.tmp/cdecr/n9_ab_20260802/effort_token_probe.json`
- runner：`scripts/cdecr_n9_node_ab.py`
- shard combiner：`scripts/cdecr_combine_n9_ab.py`

第一次串行 replay 与第一次 provisional 重建尝试均在完整结果前熔断，不计入 A/B；对应隔离副本因
本地删除安全策略拒绝而保留在 `.tmp/cdecr/n9_ab_20260802/`，不被合并报告引用。

## 7. 验证

- `ruff check`：通过。
- `mypy src/cdecr` 及三个相关脚本：44 个源文件无问题。
- `tests/cdecr/test_cross_document.py` + `tests/cdecr/test_boundary_and_models.py`：52/52 通过。
- `git diff --check`：通过。
