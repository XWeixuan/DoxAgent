# CDECR 步骤四交付说明

日期：2026-07-16

## 最终结论

24 篇 `US/MU/2026-06-25 UTC` 真实语料已完成单文档抽取、增量 Atomic Event、Event Package、幂等复跑和 M4 辅助复核。工程与结构化验收通过，但语义质量门槛未通过，因此步骤四不能标记为“质量验收通过”。

- 24/24 单文档处理成功，24/24 跨文档处理成功。
- Event Mention Schema 与 EvidenceSpan 有效率均为 100%。
- 幂等复跑新增模型调用、Mention、Atomic Event、Package 均为 0。
- M4 辅助对照的事件召回率为 76.82%，Mention 精确率为 56.78%，低于各 90% 的目标。
- M4 辅助复核已完成 24/24，但 `human_review_status=PENDING`；程序没有冒充人工金标签字。
- 用户要求暂不评估 Projection，因此 `projection_core_accuracy=null` / `DEFERRED_BY_USER`，不以虚假 100% 代替结果。

## 真实运行结果

| 指标 | 结果 |
| --- | ---: |
| 文档 / 来源 | 24 / 16 |
| Event Mention | 391 |
| Atomic Event | 375 |
| Event Package | 140 |
| Package Membership | 245 |
| Atomic 外部关系 | 20 |
| Package 外部关系 | 130 |
| 未进入 Package 的 Atomic Event | 130 |
| OPEN HOLD | 22 |
| 未进入当前 Atomic Event 的 Mention | 1（明确的 `ATOMIC_DECISION_UNCERTAIN` HOLD） |
| Schema / Evidence 有效率 | 100% / 100% |
| Hard Cannot-Link 检查 | 60,794 cases，0 violation |
| 运行内模型调用 | 1,069 |
| 运行内 input / output tokens | 2,376,027 / 830,904 |
| 幂等复跑 delta | 模型 0、Mention 0、Atomic 0、Package 0 |

M4 辅助复核额外执行 28 次调用（25 次初始、3 次修复），共 287,370 input tokens、79,835 output tokens。24 篇生成 289 个辅助 gold Mention；100 个抽取 Mention 被判为 unsupported。仅 20 万字符超长文档触发一次保守 reconciliation：删除 1 个无精确证据的 gold 项，并将 1 个漏分类 Mention 记为 unsupported。

## 未通过语义门槛的主要证据

- 最大 `EARNINGS_DISCLOSURE` Package 含 72 个 Atomic Event，存在 episode 过扩张风险。
- 同一 Micron fiscal Q3 2026 收入事实仍保留多个措辞不同的 Atomic Event，说明存在跨文档漏合并。
- 20 万字符聚合页产生 167 个 Mention，是精确率下降的主要来源；当前清洗保留了页面中大量独立新闻片段。
- M4 辅助对照得到 222/289 matched gold、222/391 matched extracted，即召回 76.82%、精确率 56.78%。该结果仍需人工复核，不能视为最终人工金标。

## 本轮工程固化

- 所有结构化 M2–M4 调用使用 JSON Mode；M3/M4 `reasoning.effort=none`；模型输出契约不注入金融 Projection。
- 超长文档 Grounder 按最多 24 个候选分批，最多 3 路并发，并用 SQLite v4 不可变批次检查点恢复。
- Judge 对超大草稿集按最多 24 条分批；未知事件族、时间精度和参与者角色只保守降级到契约的 `OTHER/UNKNOWN` 并留审计。
- 跨文档失败重试复用持久化 Mention embedding；Atomic、Package assignment 和 Package merge 使用有界并发小批。
- 同一逻辑 Package 外部关系重跑保留首次不可变记录，消除模型置信度漂移导致的 `ImmutableRecordConflict`。
- 增加 `python -m cdecr evaluation export`，导出全部 Source、Mention、Atomic、Package、Membership、关系、HOLD 与未聚类对象。

## 最终验证

| 检查 | 结果 |
| --- | --- |
| CDECR pytest | 138 passed，3 skipped（显式真实开关测试） |
| scoped Ruff | passed |
| scoped strict mypy | 23 source files passed |
| sdist / wheel | `doxagent-0.1.0.tar.gz` 与 `doxagent-0.1.0-py3-none-any.whl` 构建成功 |
| 24 篇真实单文档与跨文档运行 | 24/24 + 24/24 succeeded |
| M4 辅助复核 | 24/24 completed；human pending |
| 幂等验收 | 全部 delta=0 |
| 语义质量门槛 | failed：recall 76.82%，precision 56.78% |

## 本地交付物

- `.tmp/cdecr/evaluation/step4_final_none_complete_report.json`：技术验收、边界与幂等报告。
- `.tmp/cdecr/evaluation/step4_final_none_m4_review.json`：24 篇 M4 辅助评审，人工状态为 PENDING。
- `.tmp/cdecr/evaluation/CDECR_final_coreference_clusters.json`：完整机器可读共指/聚类结果，包含完整领域 payload 与证据。
- `.tmp/cdecr/evaluation/CDECR_final_coreference_clusters.md`：按 Event Package 展开的可读结果。
- `.tmp/cdecr/evaluation/step4_final_none_v4.sqlite3`：SQLite v4 Registry 与完整审计。

这些文件位于 Git 忽略的 `.tmp`，不会把第三方新闻全文提交到仓库。

## 参考

- [百炼结构化输出（JSON Mode）](https://help.aliyun.com/zh/model-studio/qwen-structured-output)
- [百炼 Responses API](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-responses)
- [Supabase Data API 暴露变更](https://supabase.com/changelog/45329-breaking-change-tables-not-exposed-to-data-and-graphql-api-automatically)
