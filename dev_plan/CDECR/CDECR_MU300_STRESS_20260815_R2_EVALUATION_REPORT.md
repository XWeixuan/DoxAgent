# CDECR MU300 真实压力测试 R2 评估报告（2026-08-15）

## 结论

本轮测试不能作为有效的 300 篇端到端质量验收，结论为 **INCOMPLETE / QUALITY GATE FAIL**。

300 篇文档级处理均返回 `SUCCEEDED`，但这只是文档层的局部失败吸收结果；N9 有 427 个任务落为 `UNJUDGEABLE_FAILED`，Package V3 初始全量聚类和恢复后的滚动聚类各自触发一次 `ValueError`，只有首个 50 条 occurrence 批次进入了 V3 registry，最终没有可用的 active Package。因此本轮不能计算新的 Package 准确率、召回率或碎片化指标，也不能宣称相对上一轮质量提升。

本次在两次 Package 恢复失败后停止继续真实调用，避免用不稳定的恢复路径继续消耗额度并污染评估口径。

## 1. 测试对象与可复核产物

| 项目 | 本轮 R2 |
| --- | --- |
| 语料 | `D:\\DoxAgent_CDECR_Stress_20260804_MU300_R1\\mu_300_snapshot.jsonl` |
| 语料条数 / SHA256 | 300 / `FF4CCBD3F71123E70CCF95F0C74C0920F036DB440A3C186333820A746C7C2F48` |
| Manifest | `D:\\DoxAgent_CDECR_Stress_20260804_MU300_R1\\mu_300_manifest.json` |
| Manifest SHA256 | `B34F1E2BE4A35EF0342DEA9158A29F12F4844BB1B393DA5758023F4961FEBECD` |
| 本轮 Registry | `D:\\DoxAgent_CDECR_Stress_20260815_MU300_R2\\cdecr_mu_300.sqlite3` |
| 上轮基线 Registry | `D:\\DoxAgent_CDECR_Stress_20260804_MU300_R1\\pre_n13_frozen.sqlite3` |
| 监控方式 | 按要求以约 30 分钟间隔检查，未高频轮询 |

上轮基线本身是 pre-N13 冻结结果，报告已经注明其 bulk epoch 未完整结束；因此以下比较是“运行产物与失败形态”的基线比较，不是两个完整成功版本的严格 A/B。

## 2. 运行过程与失败位置

1. 首次 CLI 运行完成 300/300 文档层处理，耗时报告值为约 2 小时 07 分；在 Package V3 初始聚类阶段失败。初始请求覆盖约 982 个 Parent occurrence，模型返回后触发 `_validate_initial`。
2. `_validate_initial` 要求每个 occurrence 恰好出现一次、MCP ID 唯一且 canonical 非空。失败被持久化成泛化的 `ValueError`，没有保存具体的违规 occurrence 或 validator message，因此无法从 DB 还原是哪一条模型输出违反了哪一项。
3. 直接 CLI resume 在 Parent immutable proposal / artifact 写入处触发 `ImmutableRecordConflict`，不能安全地从原 coordinator run 续跑。
4. 临时恢复路径绕过已存在的 immutable proposal/artifact 后，首个 50 occurrence 批次成功提交为 registry version 1（33 个 MCP）；第二个滚动批次再次触发 `ValueError`。最终只有 1 个 V3 registry batch `FINALIZED`，后续批次为 `FAILED_RETRYABLE`。
5. 当前 DB 完整性检查通过，但 `event_package_heads=0`、`active_package_memberships=0`，所以不存在可供下游评估的最终 Package。

临时恢复脚本为 `.tmp/cdecr/recover_mu300_package_resume.py`，仅用于本次故障排查，不是生产路径改动。

## 3. 与上一轮 300 篇基线的结果比较

| 指标 | 上轮 pre-N13 | 本轮 R2 | 判断 |
| --- | ---: | ---: | --- |
| 文档处理成功 | 300/300 | 300/300 | 文档层均成功，但不等于全流程成功 |
| 有 Mention 的文档 | 290/300（96.67%） | 251/300（83.67%） | 下降 39 篇；强烈提示召回退化 |
| Mention 数 | 3,572 | 2,105 | -41.07%，不能仅解释为优化收益 |
| Atomic heads | 3,134 | 1,837 | -41.38%；本轮仍含 N9 降级影响 |
| active Package | 2,467 | 0 | 本轮 Package 质量不可测 |
| 模型调用 | 12,527 | 6,667 | -46.78%，但本轮未完成 Package，不能称成本优化 |
| Input Token | 19,804,124 | 15,012,312 | -24.20% |
| Output Token | 13,504,733 | 4,021,173 | -70.22% |
| Total Token | 33,308,857 | 19,033,485 | -42.86%，主要由缺失输出和失败请求造成 |
| 累计 provider latency | 基线报告未给同口径总值 | 41,996,443 ms | 这是并发请求时长和，不是墙钟 |
| Package Pair P/R/F1 | 7.71% / 42.98% / 13.08% | 不可计算 | 没有最终 Package |
| Atomic Pair P/R/F1 | 35.48% / 23.67% / 28.39% | 不可严格计算 | 新旧 Mention ID 无重叠，且本轮 N9 有大量失败 |

当前总 Token 只有上轮的 57.14%，不能解读为效率提升：本轮少产出 1,467 条 Mention、没有任何 active Package，并且发生了 1,066 次失败模型调用。

## 4. 当前运行的成本与节点占比

本轮 DB 直接汇总为 6,667 次调用、Input 15,012,312、Output 4,021,173、Total 19,033,485。调用状态为 5,601 成功、1,066 失败（失败率 15.99%），其中：

- `provider_arrearage`：748 次；
- `invalid_json`：314 次；
- `timeout`：2 次；
- `empty_response`：2 次。

主要节点的 Total Token 占比 / 累计 latency 占比如下。累计 latency 受并发影响，不等于实际墙钟占比。

| 节点 | Total Token 占比 | 累计 latency 占比 |
| --- | ---: | ---: |
| atomic_coreference | 11.77% | 11.39% |
| field_coreference | 11.29% | 7.51% |
| dreamer_relevance | 11.03% | 19.30% |
| grounder | 10.31% | 16.53% |
| parent_induction | 9.58% | 3.72% |
| judge | 9.07% | 4.04% |
| grounder_missing_item_recovery | 6.03% | 5.41% |
| parent_induction_repartition | 5.32% | 2.02% |
| grounder_missing_recovery | 5.18% | 8.20% |
| dreamer | 3.97% | 7.94% |
| Package V3 全部调用 | 1.14% | 4.33% |

Package V3 自身共消耗约 20,885 Input、195,505 Output，合计 216,390 Token；它没有产生可用的最终 Package，不能作为有效的“低成本聚类”结果。

## 5. 失败影响范围与根因判断

### 5.1 已确认的直接影响

- 300 个文档处理记录为成功，但只有 251 个文档产生 Mention，49 个文档没有 Mention；上轮只有 10 个无 Mention 文档。
- 2,105 条 Mention 中有 427 个 N9 任务为 `UNJUDGEABLE_FAILED`，约 20.29%。这些失败主要由 provider 余额/配额错误和 JSON 失败引起，后续 Atomic/Package 只能保守降级。
- Package V3 只完成 50 条 occurrence 的第一批 registry 写入，未进入 `event_package_heads` 或 `active_package_memberships`，所以 Package 结果整体不可用。

### 5.2 最可能的业务质量回归来源

本轮相关性过滤为 `enforce` 模式：312 个 gate audit 的候选由 5,222 条降到 2,718 条，明确丢弃 2,504 条，另有 133 条 fail-open。上轮没有 `DREAMER_RELEVANCE_GATE` 记录。它与 Mention 数下降 41.07%、无 Mention 文档从 10 增至 49 同时出现，属于强相关、很可能造成召回回归的变更；但由于本轮 Mention ID 与上轮完全不重叠，不能伪造精确 Mention Gold 准召结论。

### 5.3 工程根因

1. Package V3 初始/滚动响应的覆盖校验失败，但错误持久化只保留 `ValueError`，缺少违规 ID、期望/实际集合差异和原始 validator message，导致恢复无法定向修复。
2. Resume 不是以原 coordinator run 的 immutable proposal/artifact 为可复用输入；直接恢复会把既有不可变记录当冲突，迫使临时脚本绕过写入保护。
3. 恢复后的滚动批次仍失败，说明问题不是单一首批超大 payload，而是滚动响应/校验或 MCP 状态转换仍未闭环。
4. provider_arrearage 与 invalid_json 大量出现，使上游产物本身已不是正常质量输入；即便强行完成 Package，也不能把那次结果作为正式质量验收。

## 6. 质量评估结论

- **文档层成功率：** 300/300，但只是局部失败吸收后的文档状态。
- **Mention/Field/Atomic：** 本轮没有可与上轮 Gold 严格对齐的有效评估，且 N9 失败率 20.29%，不应宣称准召保持或提升。
- **Package：** 本轮没有最终 active Package，Pair Precision、Recall、F1、singleton 比例、超大簇误合率均为“不可测”，不是 0，也不是通过。
- **成本：** Token 下降不能归因于优化，因输出规模显著下降、失败调用增多、Package 未完成。
- **整体：** 本轮 300 篇压力测试为运行失败/不完整验收，不具备与上一轮做质量优劣判断的资格。上一轮的 Package P/R/F1 只能作为待修复基线，不能被本轮结果“刷新”。

## 7. 后续必要动作（本报告不执行）

1. 先修复 V3 validator 诊断  与 batch/rolling 的覆盖协议，至少持久化 `expected_ids`、`actual_ids`、duplicate/missing/unknown IDs 和具体校验码。
2. 设计同一 registry 的安全 resume：复用同一 epoch/coordinator 的 immutable proposal 与已成功 registry batch；失败批次必须可重试且不能再次改写已 finalized batch。
3. 在重新进行 300 篇真实测试前，先用冻结 Parent occurrence 做 Package-only smoke，验证初始、滚动、description、CAS 和幂等；不得继续使用本轮已污染的 R2 registry 作为质量输入。
4. 单独复核 `relevance-filter-v2` 的 enforce 规则，先用固定 Mention Gold 做 candidate recall 对照，再决定是否在 300 篇压测中继续启用。

