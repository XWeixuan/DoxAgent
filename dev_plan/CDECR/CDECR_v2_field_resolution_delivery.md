# CDECR v2 KB 与 Field Coreference 生产衔接交付说明

## 交付结论

已将 v2 对象知识库与 Field Coreference 作为正式 N5.5 接入独立 CDECR workflow，运行顺序为：

```text
N5 Mention Finalization
→ 文内确定性别名分组
→ N5.5 v2 KB Linking
→ unresolved/ambiguous Field Coreference
→ CanonicalFieldLink
→ N6 link-aware Identity Compiler
→ N7/N8 Atomic recall 与 Hard Cannot-Link
→ N11 Package Hint / Artifact Resolution
```

EventMention 保持不可变；字段解析结果只使用既有 `CanonicalFieldRegistryEntry`、
`CanonicalFieldLink` 和 `DecisionAuditRecord`，未新增 Field Resolution Record 或
Canonical Field View 表。

## 实现内容

- N5 改为轻量 `MentionFinalizer`：保留 Evidence/ID/文内 Mention 去重和数值、币种、单位的确定性处理；不再执行 v1 Entity/Metric Linking、Embedding/M2 归一、非 issuer-aware 财期映射、Projection 改写、`entity_id` 回写或 `UNKNOWN_METRIC` 覆盖。
- 新增只读、内存有界的 `V2KnowledgeBase`：对十二个 v2 JSON Array 目录进行流式精确召回，目录哈希与构建报告一致，为 `4d3f0cb842a59f983eabd5630957a32300bd1c8a8f343b1261ee621749f1ef76`。
- 新增 N5.5 `CanonicalFieldResolutionEngine`：完成字段类型路由、同文档别名组、KB 优先链接、同类型 Field Coreference 兜底、issuer-aware Fiscal Period Linking 和追加式审计。
- 泛指或无法确定参与者类型的表达保持 unresolved，不创建跨类型 provisional；Concept、Metric、Named Object 和 Artifact 均按 namespace/kind 隔离。
- 新增 `IdentityCompiler`：只从不可变 Mention 和当前 resolved links 编译 Financial Metric、Guidance、Analyst Action 或 Open Identity；正式 ID 使用 `entry.external_id or entry.id`。
- typed Identity 缺少 issuer/period/metric 等必需链接时返回不完整编译结果并进入既有 HOLD，不使用原始字符串补齐，也不将缺失字段用于 Hard Cannot-Link。
- CrossDocumentEngine v7 在 N7/N8 中复用唯一的编译结果，不再从原始 Mention 临时重编 Identity；链接或 redirect 变化会改变处理键并只触发受影响文档/事件重算。
- `local_package_hint` 已从 N5.5 Atomic 字段集合移除，只在 N11 执行 Artifact KB Linking 或 Package/Artifact Coreference，再构造 Package Seed。
- 稳定处理键的 completed-result 检查位于任何 N5.5 模型调用之前；稳定重跑新增模型调用为零，catalog/link/redirect 变化仍会触发增量重算。

## 性能修正

首次真实 smoke 暴露出 ontology 字段在 v2 KB 未命中后仍对整个 Registry 做 Embedding+M2 的问题：进程在约十分钟时被熔断，已完成部分记录显示 133 次 Field Coreference M2 与 238 次 Field recall M1。

修正后：

- predicate/metric 等 ontology namespace 在无词法候选时建立类型隔离的 provisional，不做无意义的跨类型语义召回；
- 只有开放世界对象保留 Embedding 召回，并加入 0.82 接受阈值；
- 唯一 KB 命中更新 alias 时不提前生成 Registry Embedding，后续真正需要召回时再惰性生成；
- 结构化修复调用显式携带上一份非法 payload，仍严格限制一次修复。

## 真实语料验收

语料来自此前已经逐篇审核、排除超长聚合新闻的 30 篇 Grounder 数据集；稳定哈希抽取 10 篇，正文上限 20,000 字符。新闻正文只存在 `.tmp` Registry 中，报告不导出正文。

最终结果：

| 指标 | 结果 |
|---|---:|
| 文档完成 | 10/10 SUCCEEDED |
| Mention | 72 |
| Identity 完整 | 72/72 |
| 正式 KB Field Links | 102 |
| Provisional Field Links | 102 |
| Atomic Events | 63 |
| Event Packages | 18 |
| HOLD | 11 |
| Atomic recalled / hard-blocked | 131 / 125 |
| Package recalled / hard-blocked | 175 / 26 |
| EventMention 不可变 | 是 |
| 稳定重跑复用 | 10/10 |
| 稳定重跑新增模型调用 | 0 |

真实报告：`.tmp/cdecr/v2_runtime_real_optimized_20260723_final_report.json`。

本次真实语料没有人工标注的 Atomic candidate gold，因此上述 recalled 数量只能证明召回路径和 Hard Cannot-Link 的生产执行，不能冒充 Atomic Recall@K 质量分数。若要验收 Recall@K，需要另建 mention→gold Atomic 映射；本交付没有用旧聚类结果自证新引擎质量。

## 验证

- 新增 N5 不改写对象字段、文内别名组、v2 KB 外部链接、link-aware N6、财期 HOLD 与 N11 延迟 Package Hint 测试。
- CDECR 非真实测试集通过（155 passed、3 deselected），scoped ruff 与 strict mypy 通过。
- 完整仓库非真实回归为 494 passed、21 skipped、26 deselected、2 failed；失败是既有非 CDECR 问题：缺少 `dev_plan/PHASE0_BASELINE.md`，以及 Dashboard token cost 测试期望 `None`、实际为 `0.0`。本交付未越界修改它们。
- wheel 使用本地缓存离线构建成功；在线隔离构建仅因本机策略禁止访问 PyPI 失败，不是代码错误。
- `python -m cdecr registry init` 与 `python -m cdecr doctor` 通过；doctor 校验十二目录哈希、N5.5/N6 版本、SQLite v6、Supabase 只读、JSON Mode 和 `effort=none`。
