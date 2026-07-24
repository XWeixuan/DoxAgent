# CDECR N11–N13 真实语料验收报告

## 结论

本轮已在隔离 SQLite Registry 中完成真实模型验收。N11–N13 的执行可靠性和
主要持久化不变量通过，但独立 M4 辅助语义门槛未通过，因此不能标记为生产
语义验收完成。

最终状态：

- 工程/结构验收：通过；
- 独立 M4 语义验收：失败；
- 人工签字：未执行；
- N14 正式 External Relation：保持关闭。

## 验收边界

冻结输入来自：

`.tmp/cdecr/n7_n10_real_20260724_v4_10.sqlite3`

该 Registry 有 10 篇 SourceMessage，但只有 7 篇具有 N7–N10 已完成的当前
Atomic Event。为避免把空 Package 输入计为成功，最终有效语料限定为这 7 篇：

- 7 篇真实文档；
- 34 个冻结 Atomic Event；
- 保留原 SourceMessage、EventMention、Canonical Field Link 和 Atomic ID；
- 不重跑 N4–N10；
- 不改写冻结源 Registry；
- 在新 Registry 中只运行 N11 Package Hint、N12 Assignment 和 N13
  Package Coreference/Boundary；
- Package Hard Conflict 使用生产默认 `off`。

最终机器报告：

- `.tmp/cdecr/n11_n13_real_acceptance_20260724_eligible7_r3.json`
- `.tmp/cdecr/n11_n13_real_acceptance_20260724_eligible7_r3_m4_review.json`
- `.tmp/cdecr/n11_n13_real_acceptance_20260724_eligible7_r3_hash_verify.sqlite3`

验收工具：

- `scripts/cdecr_accept_package_n11_n13.py`
- `scripts/cdecr_review_package_n11_n13.py`

## 真实执行结果

最终第三波结果：

- 7/7 文档成功；
- 34/34 Atomic Event 产生唯一 Active Membership；
- 20 个当前 Package；
- 17 ACTIVE、3 FROZEN、0 QUARANTINED；
- 60 次 N11–N13 真实模型调用；
- 40 条 Package External Relation Candidate；
- 0 条 N14 正式 Package External Relation；
- EventMention 前后 hash 完全一致；
- Package Hard Conflict 观察数和阻断数均为 0。

### Embedding 与 Redirect/Current View

真实验收首次暴露出质量状态变化后的 Embedding 失效问题：FROZEN/
QUARANTINED 写入后，Retrieval Text 已变化，但同步函数只处理 ACTIVE
Package，导致当前 Profile Hash 与最新 Embedding Hash 不一致。

修复后对第三波 Registry 的隔离副本执行一次真实 M1 批量补算：

- 20 个当前 Package；
- 1 次 M1 批调用；
- 当前精确 `input_hash` mismatch：0；
- Redirect Source 不进入当前 Package 列表；
- 当前 Package 无 Embedding 缺失。

同时修复了真实大批次发现的 repair 序列化缺陷：N12 联合校验失败时，
Pydantic `ctx.error` 曾被直接放入 repair JSON，触发本地 `TypeError`，导致
模型修复根本未发送。现在 repair 仅传递可序列化的 `loc/type`。修复后的
15-Atomic 大批次成功完成。

## 独立 M4 语义复核

使用独立 `qwen3.7-max` 对最终 20 个 Package、10 个高相关 Package Pair 和
40 条 External Candidate 做严格覆盖复核。该结果属于 M4 辅助审核，不等同于
人工签字。

指标：

| 指标 | 结果 | 结论 |
|---|---:|---|
| Non-cohesive Package | 1 / 20 | 失败 |
| Overexpansion Rate | 5.00% | 失败 |
| 应合并但仍分裂的候选 Pair | 6 / 10 | 失败 |
| Fragmentation Candidate Rate | 60.00% | 失败 |
| Uncertain Pair | 0 | 通过 |
| External Candidate Accuracy | 38 / 40 = 95.00% | 通过 |

### False Merge

`package:12c1fe4bcd24294a423b724f` 把 Micron 的 Needham/UBS 分析师行为与
Counterpoint 关于 Apple iPhone 零部件成本的事件放在同一 Package。

错误成员：

`atomic:cd7681bdc9f089cb2deaaf87`

该成员与 Micron 分析师报告容器不属于同一报告或同一事件过程，属于明确的
False Merge。

### Fragmentation

Micron fiscal Q3 同一 earnings disclosure 被拆成四个 Package：

- `package:3d18eb319f25cd5f80b62beb`
- `package:a1c0ff5cd2caa8d01544a595`
- `package:b3a9b78280f9ed8d7dfa54aa`
- `package:f18748548e910ef8cc5424f7`

它们分别承载实际财务指标、CapEx guidance、其他 Q3 指标和 earnings call
管理层陈述。独立 M4 将四者的 6 个 Pair 全部判为同一披露 Package。

当前 N13 容易把“实际值与 guidance 不同”“指标不同”误当成
`DIFFERENT_PACKAGE`，但 Package Identity 应优先服从同一报告/披露 Artifact，
这与 Atomic Event Identity 的粒度不同。

### External Candidate

40 条候选中 38 条被判定为正确。2 条错误候选的 source Atomic 在 N13 合并后
已经成为 target Package 内部成员，因此当前视图中不应继续作为 External
Candidate 展示。历史审计可以保留，但需要派生当前候选视图过滤
source-event 与 target-root 已同包的记录。

## 跨波次稳定性

使用相同 34 个冻结 Atomic Event 执行第二、第三有效波次：

- 11/34 Event 的最终 Package Target ID 发生变化；
- 第二波同包 Event Pair 为 19，第三波为 29；
- 两波同包 Pair 交集为 10；
- Pair Jaccard 仅 0.2632。

局部 Package 数量也有明显漂移，例如相同前三篇从 `8/3/1` 变为 `5/3/2`。
因此即使单波 7/7 执行成功，当前输出仍不满足稳定生产聚类要求。

## 验收判定

通过项：

- Package Conflict OFF；
- Mention/Atomic 输入保持不变；
- 唯一 Active Membership；
- Append-only Membership Decision 正常；
- Package Profile/Embedding 当前 Hash 精确一致；
- External 仅写候选、不写 N14 正式关系；
- 真实大批次 repair 路径可完成；
- 7/7 有效文档技术成功。

失败项：

- False Merge 不为 0；
- Fragmentation Rate 过高；
- 相同固定输入跨波次不稳定；
- 当前 External Candidate 视图包含合并后已同包的陈旧候选；
- 尚无人工 Package Gold/签字。

最终结论：

> N11–N13 的工程持久化和执行可靠性已通过真实验收，但 Package 语义边界仍未
> 通过，核心问题由旧的过扩张转为“少量 False Merge + 严重 Fragmentation +
> 跨波次不稳定”。在修复 Artifact 优先级、N13 actual/guidance 粒度和当前
> External Candidate 过滤之前，不应开放 N14 正式关系写入，也不应标记为
> 生产语义完成。

