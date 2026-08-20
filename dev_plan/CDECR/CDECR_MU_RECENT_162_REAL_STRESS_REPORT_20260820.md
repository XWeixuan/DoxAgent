# CDECR MU 近期真实新闻压力测试简报（2026-08-20）

## 结论

本轮按“近7天优先、不足300篇则扩展至14天、仍不足则使用实际合格正文”的口径，构造出 **162篇** 不重复且正文完整的 MU 新闻测试集。真实模型运行在约33分钟后自行结束，但只完成单文档层，跨文档阶段在 `ATOMIC_DECIDE` 因3个 `provider_error` 终止，最终为 **PARTIAL / FAIL**，没有形成 Atomic 或 Package。因此本轮可用于评估语料构造、单文档吞吐和失败韧性，不能用于评价最终 CDECR 聚合质量。

未启动第二轮或从头重跑。原因是冻结 epoch 已基于144篇成功文档的882条 Mention 写入 immutable manifest、Field overlay 与 Atomic plan；直接补跑18篇失败文档可能改变 Mention 集合并与既有 epoch 冲突，只续跑跨文档阶段又会永久漏掉18篇，当前不满足“可保证完整断点续跑”的要求。

## 1. 测试集构造

| 项目 | 结果 |
| --- | ---: |
| Message Bus 原始记录 | 303 |
| 14天内完整正文合格 | 180 |
| 规范化重复 | 12 |
| 近重复 | 6 |
| 近7天去重合格 | 110 |
| 近14天最终去重合格 | **162** |
| 唯一 URL / fingerprint / message ID | **162 / 162 / 162** |
| 正文字符数 min / median / max | 827 / 3,291 / 21,796 |
| 发布时间范围（UTC） | 2026-08-08 10:03 至 2026-08-19 17:09 |

正文门槛沿用 Message Bus `complete_like`：至少800字符且至少4句；Finnhub 摘要本身不算合格，必须经正文提取后再次通过门槛。正文提取尝试247条，成功129条；其余主要因上游站点401/403、摘要页或不支持的媒体格式被排除。

## 2. 运行结果

| 指标 | 结果 |
| --- | ---: |
| 输入文档 | 162 |
| 单文档成功 | **144 / 162（88.89%）** |
| 单文档失败 | 18 / 162（11.11%） |
| 成功文档产生 Mention | 882 |
| 有 Mention 的文档 | 108 |
| Mention / Evidence Schema 有效率 | 100% / 100% |
| 跨文档成功 | **0 / 162** |
| Atomic / Package / External Relation | **0 / 0 / 0** |
| Epoch | `PARTIAL`, stopped at `ATOMIC_DECIDE` |
| 首轮钟墙 | 1,997.771 秒（33分17.8秒） |
| 含幂等复验总钟墙 | 2,000.162 秒（33分20.2秒） |
| SQLite quick/integrity check | `ok` / `ok` |

18篇文档失败的首个业务失败点均为 Grounder 的 `provider_http_error`。跨文档层随后完成 Field 计划与2,081个成功 Field task（另2个失败），但3个 N9/Atomic 请求返回 `provider_error` 后，编排把整个 Atomic Decide 阶段收口为失败；882个 N9 task 留在 `RUNNING`，暴露出任务账本未闭合的问题。

## 3. Token 与调用

| 指标 | 结果 |
| --- | ---: |
| 模型调用 | 1,707 |
| 成功 / 失败调用 | 1,306 / 401 |
| Input Token | **3,290,493** |
| Output Token | **1,014,380** |
| Total Token | **4,304,873** |
| Repair 调用 | 374（21.91%） |

主要 Token 占比：

| Stage | Total Token | 占比 |
| --- | ---: | ---: |
| atomic_coreference | 899,805 | 20.90% |
| grounder | 817,115 | 18.98% |
| judge | 611,186 | 14.20% |
| dreamer | 409,463 | 9.51% |
| field_coreference | 380,931 | 8.85% |
| grounder_item_repair | 356,286 | 8.28% |
| dreamer_relevance | 269,689 | 6.26% |
| field_coreference_item_repair | 238,117 | 5.53% |

## 4. 失败分布与判断

401次失败调用中：

- `provider_http_error`: 155；
- Field item repair 本地非法输出：`PYDANTIC_VALIDATION_FAILED` 136、`TARGET_NAMESPACE_FORBIDDEN` 75、`TARGET_NAMESPACE_UNKNOWN` 17、`LINK_CANDIDATE_UNKNOWN` 5；
- `provider_arrearage`: 5；
- Atomic `provider_error`: 3；
- `invalid_json`: 3；
- provider data inspection: 2。

最严重的不是单个 provider 错误，而是两个放大机制：

1. Field item repair 共263次，只有29次成功，234次修复后仍非法；这消耗约238k Token，却未形成有效修复。
2. 3个 Atomic/N9 provider 错误导致整个跨文档批次失败，未局部降级或闭合882个任务，最终完全没有 Atomic/Package 交付物。

因此当前版本仍不适合再次直接做更大规模压力测试。下一步应先在冻结输入上修复 Field repair 合法率与 Atomic task-local failure containment，并设计显式的“文档失败补齐后重建 epoch”或版本化 manifest 恢复路径；本轮不因该结论自动改代码或重跑模型。

## 5. 留存产物

- Corpus Snapshot: `.tmp/cdecr/mu_recent_news_20260820/mu_recent_news_snapshot.jsonl`
- Corpus Manifest: `.tmp/cdecr/mu_recent_news_20260820/mu_recent_news_manifest.json`
- Corpus build report: `.tmp/cdecr/mu_recent_news_20260820/mu_recent_news_build_report.json`
- Registry: `.tmp/cdecr/mu_recent_news_stress_20260820_r1/cdecr_mu_recent_162.sqlite3`
- Runtime report: `.tmp/cdecr/mu_recent_news_stress_20260820_r1/cdecr_mu_recent_162_report.json`
- Logs: `.tmp/cdecr/mu_recent_news_stress_20260820_r1/stress.stdout.log`, `stress.stderr.log`
