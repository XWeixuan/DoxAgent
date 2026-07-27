# CDECR 项级容错优化与 30 篇真实验收报告

## 1. 结论

本轮项级容错实现达到了工程稳定性目标，但没有达到业务语义验收目标。

- 固定 30 篇语料的单文档与跨文档流程均为 30/30 成功，文档成功率 100%，没有任何单条 Evidence、字段或批次任务异常扩大为整篇文档失败。
- 重启复跑 30/30 全部复用，新增模型调用、Mention、Atomic Event、Package 均为 0。
- 940 次模型调用中 938 次直接成功；1 次 Judge 非法 JSON 经一次修复成功，1 次 Field Coreference 非法结构被隔离为持久化 UNRESOLVED。两者均未导致文档失败。
- 225 条 Mention 的 Surface Proposition 全部能在来源中找到支持，但严格 Mention Precision/Recall/F1 仅为 85.33%/71.64%/77.89%。
- Field Resolution 严格正确率为 83.29%；Atomic Pair Precision/Recall/F1 为 41.84%/76.92%/54.20%；Package Pair Precision/Recall/F1 为 91.54%/73.37%/81.45%。
- Atomic 层存在显著过合并，Package 层仍有反应事件并入财报、同一业务包碎片化等错误，因此语义验收失败。

结论是：本轮代码可以证明“异常不扩散、流程可完成、审计可追踪”，不能证明“聚类质量已可接受”。按照方案的门槛，P2 的 legacy/shadow/canary 清理未执行。

## 2. 范围与证据

固定输入：

- Snapshot：`.tmp/cdecr/grounder_quality_v5/live_all.jsonl`
- Manifest：`dev_plan/CDECR/experiments/grounder_quality_30_manifest.json`
- 文档数：30

本轮输出：

- Registry：`.tmp/cdecr/resilience/resilience_30_20260728.sqlite3`
- Runtime report：`.tmp/cdecr/resilience/resilience_30_20260728_report.json`
- Cluster export：`.tmp/cdecr/resilience/resilience_30_20260728_clusters.json`
- Mention 分片人工复核：`.tmp/cdecr/resilience/mention_review_01_10.json`、`mention_review_11_20.json`、`mention_review_21_30.json`
- Field/Identity 全量复核：`.tmp/cdecr/resilience/field_identity_audit_all.json`
- Atomic 全量复核：`.tmp/cdecr/resilience/atomic_review_all.json`
- Package 全量复核：`.tmp/cdecr/resilience/package_review_all.json`

关键哈希：

| Artifact | SHA-256 |
| --- | --- |
| Registry | `64AD6C5015D4D66E592BD4B74F030E96BBC1D25EE17B243C6FEADB81C709274F` |
| Runtime report | `678B172CE3648A4C9000879E36917D65A3D8CAD91795AAE312EBA7B59CF2FB18` |
| Cluster export | `FC29548728B68284F7A8D5461305A4FA51CBDA142F849B399579559F762E8877` |

## 3. 运行成功率与产物

| 指标 | 结果 |
| --- | ---: |
| 单文档成功 | 30/30，100% |
| 跨文档成功 | 30/30，100% |
| 失败文档 | 0 |
| Mention | 225 |
| Atomic Event | 95 |
| Package | 65 |
| Active membership | 95 |
| 模型调用 | 940 |
| 首轮耗时 | 5,397.721 秒，约 89 分 58 秒 |
| 含重启验证总耗时 | 5,408.298 秒，约 90 分 08 秒 |
| 重启新增模型调用/实体 | 0/0 |

Runtime report 中的 `acceptance_passed=false` 不是运行失败，而是语义边界违反导致的验收失败。

## 4. Token 与运行成本

总输入 Token 为 4,403,264，总输出 Token 为 443,843，合计 4,847,107，平均每篇 161,570.23 Token；模型请求 Payload 总计 12,781,656 bytes。

| 节点 | 调用数 | 输入 Token | 输出 Token | 合计 Token | 合计占比 |
| --- | ---: | ---: | ---: | ---: | ---: |
| package_merge（N13） | 162 | 2,535,383 | 117,989 | 2,653,372 | 54.74% |
| package_assignment（N12） | 43 | 715,487 | 80,974 | 796,461 | 16.43% |
| atomic_coreference（N9） | 84 | 513,828 | 90,200 | 604,028 | 12.46% |
| grounder | 30 | 111,447 | 84,945 | 196,392 | 4.05% |
| field_coreference | 170 | 171,627 | 2,939 | 174,566 | 3.60% |
| judge | 30 | 140,587 | 20,029 | 160,616 | 3.31% |
| 其余节点 | 421 | 215,505 | 126,767 | 342,272 | 7.06% |

N9、N12、N13 合计占总 Token 的 83.63%；N13 单节点占 54.74%，是下一轮效能优化的第一优先级。Grounder 单次延迟最高，但不是总成本的主要来源。后续不能通过激进增大复杂判断批次来换吞吐，应优先减少 N13 比较对、提升 anchor 召回质量并压缩重复候选上下文。

## 5. Mention 质量

三名 Codex 审核分片覆盖全部 30 篇来源；审核时先读来源并建立 Gold，再对系统输出，避免用系统结果定义真值。

| 指标 | 结果 |
| --- | ---: |
| Gold Mention | 268 |
| 输出 Mention | 225 |
| 严格 TP | 192 |
| 严格 FP | 33 |
| 严格 FN | 76 |
| Micro Precision | 85.33% |
| Micro Recall | 71.64% |
| Micro F1 | 77.89% |
| 来源支持的输出 | 225/225，100% |
| Macro Precision | 83.16% |
| Macro Recall | 70.36% |
| Macro F1 | 75.66% |

主要缺陷：

1. 召回不足：遗漏财务比较量、次级业务事实和部分市场动作。
2. 原子性不足：一个 Mention 捆绑多个事实，导致严格匹配失败并污染下游 Atomic。
3. assertion/time/quantity 结构化错误：例如预测与实际混淆、交易时段不一致、`$1.2 trillion` 被结构成 `1.2 USD`。
4. 少量低信息重复 Mention。

这解释了“225/225 文本有依据”与“严格 Precision 只有 85.33%”并不矛盾：前者只验证来源支持，后者还要求事实边界和字段均正确。

## 6. Evidence、Field 与 Identity

### 6.1 Evidence

- 352/352 条 Raw Evidence 均落库。
- Main Evidence：234 VERIFIED、9 TEXT_NOT_FOUND。
- Attribute Evidence：102 VERIFIED、7 TEXT_NOT_FOUND。
- 16 条定位失败的原始 Evidence 均保留，没有扩散为 Mention 或文档失败。
- 语义 Evidence 定位/校准 LLM repair 为 0。
- 另有 1 次 Dreamer 结构修复，原因是两个候选的 Evidence text 为空；这属于允许的结构修复，不是语义定位修复。

### 6.2 Field Resolution

独立按 225 predicate、261 participant、20 location、206 metric、70 period、8 routed attribute 重建出 790 个必需字段；790/790 均有持久化链接。

| 字段 | 正确/总数 | 正确率 |
| --- | ---: | ---: |
| predicate.normalized | 188/225 | 83.56% |
| participants | 242/261 | 92.72% |
| quantities.metric_id | 167/206 | 81.07% |
| locations | 19/20 | 95.00% |
| time.reference_period_id | 35/70 | 50.00% |
| open_attributes | 7/8 | 87.50% |
| 合计 | 658/790 | 83.29% |

UNRESOLVED 共 52 条，其中正确保守弃权 19 条、错误弃权 33 条，准确率 36.54%。错误主要是可识别的 Micron Q3/Q4 period 被保留为 issuer-scoped unresolved，虽然避免错误外链，但损失了可用 canonical coverage。

### 6.3 Identity

- 225/225 Identity Profile 生成成功，必需字段缺失为 0。
- 225/225 Profile 均为 OPEN，schema projection 全部为空。
- Quantity metric 没有进入 identity 强约束。
- 对全部 956 个预测同簇 pair 复核，Identity 可支持的 TP/FP 为 285/671，Precision 29.80%。
- 只在 308 个可高置信恢复的真 pair 范围内，FN 为 23，judgeable Recall 92.50%；由于负对 Gold 无法从现有留痕完整恢复，不把该 Recall 外推为全量 Recall。

首个系统性分歧出现在 N5.5/N6：字段虽然都有链接，但 metric、period、schema 没有形成足够强的 Atomic 身份边界，N9 随后把这些碰撞放大为误合并。

## 7. Atomic 质量与 N7/N9

全量复核覆盖 225 Mention、95 个预测 Atomic。Gold 按“最小同一事实”定义：同 issuer、同 period、同一披露中的不同 metric、accounting basis、产品、交易时段、分析机构、assertion 或业务动作必须是不同 Atomic；它们最多在 Package 层相聚。

| Pair 指标 | 结果 |
| --- | ---: |
| 预测 Atomic | 95 |
| Gold Atomic | 115 |
| 预测正对 | 956 |
| Gold 正对 | 520 |
| TP | 400 |
| FP | 556 |
| FN | 120 |
| Precision | 41.84% |
| Recall | 76.92% |
| F1 | 54.20% |

主要误合并集中在：

- `atomic:bb912...`：把 revenue、EPS、gross margin、free cash flow、segment revenue 等不同指标并入一个 33-member Atomic。
- `atomic:d600...`：把 SCA 签署、220 亿美元承诺、未来收入占比、合同条款混为一体。
- `atomic:a464...`：把 revenue/gross-margin/EPS guidance、capex plan 混合。
- `atomic:471...`：混合盘前、盘后、正常交易时段、market-cap level。
- `atomic:486...`：混合不同 accounting basis 的 gross margin 与 net income。

N7/N9 的重建指标为：

| 指标 | 结果 |
| --- | ---: |
| N7 Recall@K | 108/110，98.18% |
| N9 MERGE Precision | 94/130，72.31% |
| N9 conditional MERGE Recall | 94/108，87.04% |
| CREATE_NEW Accuracy | 86/95，90.53% |

这些节点指标标记为 `PROVISIONAL_RECONSTRUCTION`：本次 Registry 没有持久化规范的历史 N7 ranked snapshot，审计通过 `ATOMIC_ASSIGNMENT` candidate refs 和 provisional/global ID 后缀归一重建。它们在冻结 Registry 上可复现，但不应作为长期生产指标。下一轮应在编排层用短记录持久化 `mention_id → candidate event_id/rank/route/score`，无需增加 LLM payload。

N9 有 1 个任务缺 3/5 candidate assessment，任务级保守回退 CREATE_NEW；该 Bank of America/Micron analyst action 经人工复核本应新建，因此没有造成不良合并或召回损失。

## 8. Package、N12 与 N13

全量复核覆盖 95 Atomic 和 65 Package。

| Pair 指标 | 结果 |
| --- | ---: |
| 预测同 Package pair | 331 |
| Gold 同 Package pair | 413 |
| TP/FP/FN/TN | 303/28/110/4,024 |
| Precision | 91.54% |
| Recall | 73.37% |
| F1 | 81.45% |

三类最终错误：

1. 主 Micron Earnings Package 混入一个 market reaction Atomic，产生 25 个 FP pair。
2. Anthropic 6 月 22 日独立公告与 earnings-origin SCA/investment comments 混合，产生 2 个 FP pair。
3. Sandisk 不同时间/实体边界的反应事件合并，且其中一个 Atomic 已被 Citi target action 污染，产生 1 个 FP pair。

Package 碎片化导致 110 个 FN pair：Micron earnings、Qualcomm strategy、IDC Apple outlook 各被拆成 3 个组件。

N12 共 188 条历史决策，108 ADD_TO_PACKAGE、80 CREATE_NEW_PACKAGE，没有 invalid/missing-task degradation。以最终 active join edge 为代理，N12 Precision 62.50%、Recall 15.15%；该 Recall 只能说明 N12 单阶段保守，因为 N13 本应负责跨 seed 合并，不能替代最终 Package Recall。

N13 共 1,768 个 pair decisions；人工相关子集为 TP 17、FP 9、FN 9，conditional Precision/Recall/F1 均为 65.38%。9 个错误 SAME 中 7 个是 reaction 并入 earnings；9 个错误 DIFFERENT 中 6 个是 Micron supply guidance 与 earnings 的应合未合，3 个是 Qualcomm strategy 碎片。

Metadata：

- kind fidelity：59/65，90.77%。
- family fidelity：59/65，90.77%。
- title/summary fidelity：64/65，98.46%。
- anchor entity：65/65。
- anchor artifact：0/65。
- package_anchor_ids：0/65。
- anchor period：5/65。

Artifact/period anchor 缺失是 N12/N13 依赖主题相似度、难以守住“分析报告/财报/反应事件”父边界的重要根因。

## 9. 异常、降级与根因

| 现象 | 节点 | 直接原因 | 处理结果 | 是否文档失败 |
| --- | --- | --- | --- | --- |
| 非法 JSON | Judge | 一次模型输出不是合法 JSON | 一次 repair 后成功 | 否 |
| LINK 缺 canonical_id | Field Coreference | 非法结构化输出 | 单字段持久化 UNRESOLVED | 否 |
| 两个 Evidence text 为空 | Dreamer | 候选结构不合法 | 一次结构 repair | 否 |
| 3/5 candidate assessment 缺失 | N9 | 批内任务覆盖不完整 | 仅该任务 CREATE_NEW | 否 |
| 16 条 Evidence 找不到原文 | Evidence localization | 模型 Evidence 文本与 Source 不精确匹配 | 原始 Evidence + TEXT_NOT_FOUND 落库 | 否 |
| Atomic 大规模误合并 | N5.5/N6 → N9 | metric/period/schema identity 约束不足；N9 把相关当相同 | 运行成功但语义失败 | 否 |
| Reaction 并入 Earnings | N13 | 包级 artifact/period anchor 缺失，边界判定偏主题相似 | 运行成功但语义失败 | 否 |

Runtime report 的 664 个 hard-conflict violation 是最终同簇 mention pair 的组合检查，不是 664 次独立 N9 错误。它们集中在 21 个 Atomic，前五个 supercluster 占 611/664；其中既有真实跨 metric/session/act 误合并，也有 time/predicate 表达差异造成的规则假阳性。`hard_cannot_link_mode=shadow` 只审计不拦截，是误合并得以进入最终 Atomic 的直接运行配置因素，但不能简单把 664 全部当作人工 Gold FP。

## 10. 验收门槛

| 门槛 | 结果 |
| --- | --- |
| 文档/跨文档成功率 ≥ 90% | 通过：100%/100% |
| Evidence/UNRESOLVED 导致文档失败为 0 | 通过：0 |
| Candidate leakage 为 0 | 通过：0 |
| Raw invalid Evidence 持久化 | 通过：16/16 |
| 语义 Evidence repair 为 0 | 通过：0 |
| UNRESOLVED 可持久化 | 通过；真实 run 未发生 redirect，redirect 仅由离线回归覆盖 |
| False merge 为 0 | 失败：Atomic 556 FP pair；Package 28 FP pair |
| 审计留痕足以稳定计算所有节点指标 | 部分失败：N7 ranked snapshot 未持久化 |
| 总体验收 | **失败** |

## 11. 下一步建议

优先级按业务影响与改动重量排序：

1. 在 N6 identity 中纳入可审计的 metric、accounting basis、period 与 schema projection；先增强强冲突特征，不重写 Prompt。
2. 将“不同 metric/交易时段/分析机构/业务动作”从 shadow 规则中筛出高精度子集，先作为 N9 merge 的确定性否决；不要直接启用全部 664 规则。
3. 在 N11/N12/N13 补齐 artifact/period anchor 的编排层派生与持久化，再处理 reaction/earnings 边界；不增加 LLM reasoning payload。
4. 在 N7 编排层持久化短候选快照，使 Recall@K、N9 conditional accuracy 可直接计算。
5. Token 优化聚焦 N13 比较对与重复上下文，而不是激进增大复杂批次。所有语义改动必须复跑本 30 篇 Gold，并以 Atomic/Package Pair P/R 和 reaction boundary 为门槛。

在 Atomic false merge 与 Package 边界问题消除前，不应清理 legacy/shadow/canary 路径，也不应把 30/30 成功率解释为生产质量通过。
