# CDECR Runtime / Token / Audit Remediation 交付报告

> 执行日期：2026-07-26
> 冻结语料：`grounder_quality_30_manifest.json`，30 篇
> 基线提交：`ac9a7a1`
> Batch 1 提交：`fafc361`

## 1. 最终结论

本轮完成了 Batch 1 确定性可靠性修复、两次有效的 Batch 2 全流程运行、一次供应商故障熔断、
节点级 Wire 开关与审计协议实现，以及最终安全回退。

最终没有启用任何第二批 LLM Wire 输入优化：

- N9 task-local assessment、N12 no-ranking、Grounder issue_codes、targeted repair 未完成所需
  节点级业务 A/B，保持 legacy/shadow 并 fail-closed；
- N13 pair-inline 候选在真实 30 篇运行中降低了成本，但严格 reaction boundary 从 0 增至 2，
  因此 `canary/on` 已 fail-closed；
- N9/N12/N13 仅保留不会进入模型请求的 self-contained shadow DTO，用于后续固定上游 A/B；
- Prompt、reason 协议、JSON 输出 Schema、持久化模型和复杂节点 Batch 上限均未改变。

因此，本轮可声明的线上收益来自 Batch 1 的可靠性与幂等修复，不能把 Batch 2 的 shadow
估算或随机全流程 token 下降表述成已启用成本收益。

## 2. 实现内容

### 2.1 Batch 1：已通过专项门槛

- `null/null` 事件时间确定性归一为 `UNKNOWN`，不拿发布时间或报告期伪造事件发生时间；
- Evidence 对齐器优先恢复精确 Source Span，无法安全恢复时只做保守辅助 Evidence 降级；
- Judge Evidence 回退只撤销非法 Evidence 修改，保留其余合法修正；
- N9 coverage、重复项、非法 target 进入 typed validation/repair 边界，不再抛裸 `ValueError`；
- Wire audit identity 增加 operation/trigger/attempt/batch，避免重试与 reassessment 相互覆盖；
- Mention derivation 保存最终 Mention ID 与 before/after hash；
- 成功跨文档路径在 N5.5 前复用已完成 snapshot，重启复跑新增模型调用为 0。

Batch 1 专项门槛结果：27/30 文档、19 条跨文档成功，Mention Schema 与 Evidence Span 100%，
reaction 0/56，幂等复跑所有 delta 为 0。

### 2.2 Batch 2：独立开关与安全回退

新增独立配置：

- `CDECR_N9_WIRE_PROTOCOL`
- `CDECR_N12_WIRE_PROTOCOL`
- `CDECR_N13_WIRE_PROTOCOL`
- `CDECR_GROUNDER_ISSUE_PROTOCOL`
- `CDECR_TARGETED_REPAIR`

每个开关支持 `legacy/shadow/canary/on` 配置值，但未通过门槛的 `canary/on` 会在构造处理器时
直接拒绝。N9/N12 shadow 不再使用顶层字典和引用，而是任务内 self-contained DTO；N13
shadow 每个 pair 内联 source/target decision view，并只保留一份 retrieval signals。

## 3. 真实 30 篇运行

### 3.1 运行制品

| 运行 | Registry SHA256 | Report SHA256 | 用途 |
|---|---|---|---|
| Batch 1 | `E774D8BE...F625303` | `02D91F17...B46F230` | 确定性修复基线 |
| N13-on 候选 | `00AFBCC9...DC2E98` | `A5D34920...FEF7C` | 被拒绝的主动协议候选 |
| Batch 2 safe-shadow | `8B66BADB...E0A2FF` | `1ACC3415...69563` | 最终安全回退制品 |

一次较早的 N13-on 运行因供应商 `provider_arrearage` 中断。主 key 与两个 fallback 均失败后，
运行被立即熔断；该 Registry 只保留为事故证据，不进入任何指标。

### 3.2 业务与成本总览

| 指标 | Batch 1 | N13-on 候选 | safe-shadow |
|---|---:|---:|---:|
| 文档成功 | 27 | 28 | 26 |
| 跨文档成功 | 19 | 22 | 19 |
| Atomic | 63 | 50 | 40 |
| Package | 38 | 28 | 31 |
| Model calls | 621 | 665 | 538 |
| Input tokens | 2,118,571 | 1,880,863 | 1,324,849 |
| Output tokens | 347,796 | 290,861 | 226,302 |
| Total tokens | 2,466,367 | 2,171,724 | 1,551,151 |
| 每成功文档 tokens | 91,346.93 | 77,561.57 | 59,659.65 |
| Request payload bytes | 6,025,679 | 5,087,433 | 3,465,402 |
| First-pass wall clock | 3,256,769 ms | 3,238,932 ms | 2,479,972 ms |
| Mention Schema | 100% | 100% | 100% |
| Evidence Span | 100% | 100% | 100% |
| reaction violations | 0/56 | 2/54 | 1/47 |
| 幂等复跑新增 calls | 0 | 0 | 0 |

safe-shadow 没有改变模型输入。它的 aggregate token 更低，主要来自真实模型输出、成功文档、
Atomic/Package 数量和候选规模不同，不能归因于 Wire 优化。

## 4. N13 候选为什么回退

N13-on 候选的 `package_merge` 阶段表面改善明显：

| 指标 | Batch 1 legacy | N13-on | 跨运行变化 |
|---|---:|---:|---:|
| Calls | 51 | 44 | -13.73% |
| Input tokens | 802,128 | 570,447 | -28.88% |
| Output tokens | 40,845 | 34,511 | -15.51% |
| Payload bytes | 2,686,333 | 1,888,394 | -29.70% |

但跨运行数字混入了不同 Package 图和候选量。用 N13-on 同一运行、同一候选做 legacy/wire
序列化对照，payload 仅从 1,925,495 降至 1,888,394，即 1.93%。这说明当前生产输入本身
已经接近 pair-inline，单纯把 retrieval signals 从两侧提升到 pair 层的纯结构收益有限。

更重要的是，严格 reaction boundary 从 Batch 1 的 0 增至 2，因此完整门槛失败。路径追踪显示：

- 两个违规 Mention 都由 N12 `NO_MEMBER_CREATE_NEW_PACKAGE` 创建为
  `EARNINGS_DISCLOSURE/BOUNDED`；
- N13 对涉及这些 Package 的候选均保留 `DIFFERENT_PACKAGE`，没有执行错误合并。

这可以说明问题并非直接由 N13 merge 决策制造，却不能绕过“业务违规不得高于第一批”的提交门槛。
safe-shadow legacy 轮也出现 1 个 reaction violation，进一步证明全流程随机性较高；本轮仍选择
保守回退，而不是以归因解释替代业务验收。

## 5. Shadow 收益与是否值得继续

safe-shadow 使用相同运行内的 baseline/candidate payload 对照：

| 节点 | Samples | Baseline bytes | Candidate bytes | 估算节省 |
|---|---:|---:|---:|---:|
| N9 atomic_coreference | 40 | 612,344 | 610,963 | 0.23% |
| N12 package_assignment | 21 | 676,274 | 676,061 | 0.03% |
| N13 package_merge | 35 | 1,244,602 | 1,217,155 | 2.21% |

这些比例明显低于原计划 8%–35% 的节点级预期。N9/N12 当前没有足够收益覆盖业务 A/B 与
维护成本；N13 仍是三者中最值得继续的候选，但下一轮应使用固定上游 Package/pair 集合做
真实模型配对 A/B，而不是再用两次随机全流程总量判断等价性。

## 6. 门槛判定

### Batch 1

- 专项可靠性门槛：通过；
- 全量业务 acceptance：仍为 false，严格业务失败如实保留。

### Batch 2

- 文档成功不低于 27：失败，safe-shadow 为 26；
- 跨文档成功不低于 19：通过，safe-shadow 为 19；
- Mention Schema / Evidence Span 100%：通过；
- 裸 `ValueError` / `ImmutableRecordConflict`：0，通过；
- 幂等复跑所有实体与模型调用 delta：0，通过；
- reaction boundary 不高于 0：失败；
- 至少一个已启用目标节点 token 下降：不适用，没有节点获准启用。

最终状态：Batch 2 完整门槛未通过；所有第二批模型输入协议保持 legacy，候选只在 shadow。

## 7. 后续建议

1. 不继续扩展 N9/N12 DTO；它们当前同运行 shadow 收益低于 0.3%。
2. 为 N13 建立固定 Package graph 与 pair 集合的真实模型配对 A/B，至少重复三次并单独统计
   relation 一致率、repair、reason 完整性和 reaction boundary。
3. 单独修复 Package profile compiler 把分析师行动错误建成
   `EARNINGS_DISCLOSURE/BOUNDED` 的问题；该问题不应通过 N13 merge 来补救。
4. 将 Grounder/Judge 随机严格校验失败作为独立质量项目，不再用一次 30 篇全流程完成率判断
   Wire DTO 的因果效果。
5. 保持 Prompt、reason、复杂节点 Batch 和 N5.5 snapshot-batch 现状，直到上述固定上游门槛通过。
