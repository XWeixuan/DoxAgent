# CDECR Field 并发修复后旧 30 篇真实重跑报告

> 日期：2026-08-08  
> 正式基准：`D:\DoxAgent_CDECR_Acceptance_20260804_ThinkingDownshift_R5`  
> 首次失败运行：`D:\DoxAgent_CDECR_Acceptance_20260808_DeterministicRuntime_R1`  
> 修复后重跑：`D:\DoxAgent_CDECR_Acceptance_20260808_DeterministicRuntime_R2`  
> 语料：与基准完全一致的固定 30 篇 manifest/snapshot/fingerprint  
> Gold：沿用旧 Gold；没有重新标注

## 1. 结论

Field 阻断问题已修复并通过真实运行验证：R2 单文档 30/30、跨文档 30/30，BULK_EPOCH 成功 `FINALIZED`，幂等重跑模型调用及 Mention/Atomic/Package 增量全部为 0。R1 的 `ImmutableRecordConflict` 没有复现，414 个 Field task 全部成功。

但本轮**不能作为完整质量 A/B 验收通过**：provider 余额在 Package 后段耗尽。紧随生产运行启动的 Gold evaluator 明确返回 HTTP 402 `Insufficient Balance`；同一时间段生产运行中的 Wave C、N13 full Decide 与 pair repair 均以 `provider_invalid_request_error` 失败。因此：

- Field 修复和工作流非阻塞能力已验证；
- 到 Atomic 完成前的效能改善可评价；
- Package 输出受到余额耗尽导致的保守降级和碎片化污染；
- Mention/Field/N9 新 Gold 指标因 evaluator 402 未完成，不能伪造或沿用 R1/R5；
- N13 Token 不能宣称优化，因为本轮后段请求根本没有获得有效响应。

## 2. 实施的窄修复

### 2.1 Package-anchor 原子复用

同一确定性 package-anchor ID 的创建与 alias 合并现在位于同一 SQLite `BEGIN IMMEDIATE` 事务内。相同 namespace 的 parent anchor 并发创建时复用一个 root，按规范化表面去重并保留最多 8 个 aliases；其他 namespace 或不兼容身份仍保持原冲突边界。

### 2.2 Field task 局部降级

单个 Field semantic group 异常不再从 `future.result()` 扩大为整个 epoch 失败。失败 task 仍写入 ledger 和错误码，但其他 group、Atomic、Package 可以继续；summary 记录 `failed_group_count`。

### 2.3 真正的 Field resume

`resolve_epoch()` 读取 Field ledger 已完成 task ID，只执行 failed/missing group，并记录 `skipped_group_count`。不会再为恢复一个失败 task 重放数百个已成功 LLM 请求。

### 2.4 验证

- 并发 parent-anchor 测试：两个不同 raw anchor 得到一个 root，aliases 完整；
- 局部失败测试：一个 group 失败、其他 group 成功，epoch 不抛出；
- resume 测试：成功 task 被跳过，只重跑失败 task；
- 定向测试：28 passed；
- 完整 `tests/cdecr`：313 passed、3 skipped，耗时 345.91 秒；
- Ruff、Mypy、`git diff --check`：通过。

## 3. 真实运行完整性

| 指标 | 基准 R5 | 修复后 R2 |
| --- | ---: | ---: |
| 单文档成功 | 30/30 | 30/30 |
| 跨文档成功 | 30/30 | 30/30 |
| 文档失败 | 0 | 0 |
| Mention schema rate | 100% | 100% |
| Evidence span rate | 100% | 100% |
| Field task | — | 414/414 成功 |
| Epoch | FINALIZED | FINALIZED |
| 幂等模型调用增量 | 0 | 0 |

R2 得到 240 条 Mention、188 个 Atomic、107 个 Package；Evidence 为 254/254 VERIFIED。基准为 229 Mention、165 Atomic、80 Package、238/239 Evidence VERIFIED。

## 4. 效能与 Token

### 4.1 表面全程数据

| 指标 | 基准 R5 | R2 | 表面变化 |
| --- | ---: | ---: | ---: |
| 模型调用记录 | 792 | 820 | +3.54% |
| Input Token | 1,363,869 | 1,321,345 | -3.12% |
| Output Token | 1,064,405 | 1,061,669 | -0.26% |
| Total Token | 2,428,274 | 2,383,014 | -1.86% |
| 累计模型延迟 | 7,856,953 ms | 7,391,834 ms | -5.92% |
| 端到端墙钟 | 3,315,832 ms | 1,116,711 ms | -66.32% |

这些全程变化不能直接当成正常模型效能提升：R2 有 119 次 Package LLM 请求因余额不足在服务端立即拒绝，既没有 input/output token，也快速结束。尤其 Package/N13 墙钟和 Token 被人为压低。

### 4.2 可比的 pre-Package 关键路径

为排除 402 污染，比较从启动到 Atomic/late convergence 完成的共同路径：

| 区段 | 基准 R5 | R2 | 变化 |
| --- | ---: | ---: | ---: |
| 单文档/pre-epoch | 252,136 ms | 235,217 ms | -6.71% |
| Field | 554,508 ms | 502,970 ms | -9.29% |
| Atomic/N9 | 399,572 ms | 150,643 ms | **-62.30%** |
| Atomic late | 15,043 ms | 15,826 ms | +5.21% |
| 合计 | 1,221,259 ms | 904,656 ms | **-25.92%** |

这部分没有受到 Package 余额耗尽影响，可以确认 Snapshot/Card/批量持久化等编排优化对真实关键路径有明显收益。

### 4.3 主要节点 Token/延迟占比

占比以 R2 实际 2,383,014 Total Token、7,391,834 ms 累计模型延迟计算；失败且零 Token 的 Package 请求不进入 Token 占比。

| 节点 | Total Token | Token占比 | 累计延迟 | 延迟占比 | 相对基准 Token |
| --- | ---: | ---: | ---: | ---: | ---: |
| N9 atomic coreference | 496,003 | 20.81% | 1,653,994 ms | 22.38% | -5.61% |
| Judge | 429,190 | 18.01% | 1,619,661 ms | 21.91% | -1.73% |
| Grounder | 373,683 | 15.68% | 1,471,209 ms | 19.90% | +8.77% |
| N12 package assignment | 270,466 | 11.35% | 700,788 ms | 9.48% | +54.07% |
| Field coreference | 235,177 | 9.87% | 244,526 ms | 3.31% | -17.19% |
| N9 escalation | 171,527 | 7.20% | 631,768 ms | 8.55% | -5.87% |
| Grounder missing recovery | 86,561 | 3.63% | 313,295 ms | 4.24% | +22.60% |
| Grounder item repair | 73,772 | 3.10% | 120,593 ms | 1.63% | -3.50% |
| Grounder missing-item recovery | 54,302 | 2.28% | 133,016 ms | 1.80% | +1.86% |

Field 主请求从240降至187，Field recall从254降至159；两者合计 Token 从301,420降至242,218（-19.64%），且414个语义 task全部提交，属于有效收益。

### 4.4 新 deterministic runtime telemetry

- Atomic candidate：1,130 pair，`pair_registry_read_count=0`，snapshot query=3，2,189条 audit仅5个事务，未降级；
- Atomic Apply：17个事务，retry=0，degraded=0；
- Package snapshot query=6，Apply 10个 chunk，retry=0，degraded=0；
- Wave C pair registry read=0，522条 audit仅2个事务；
- runtime flags：batch audit、stage snapshot、chunk apply、batch ledger、embedding batch均启用；
- `audit_degraded=false`，幂等增量全部为0。

Embedding batch 映射完整、最终 `failed_owner_ids=[]`，但内部出现21次 atomic identity、26次 atomic recall 的 `ValueError` 后递归拆批，101个批次呈64→32→16→8的反复拆分。业务未丢失，但说明当前默认64与真实 embedding provider能力不匹配，后续应把已探明的安全 batch size缓存/下调，避免每轮重复试错。

## 5. Provider 余额耗尽及影响范围

紧随生产运行启动的 Mention/N9 Gold evaluator 均返回：HTTP 402 `Insufficient Balance`。生产 Registry 同时记录：

| 路径 | 结果 | 影响 |
| --- | ---: | --- |
| N12 Wave C M3 | 4/4 batch请求失败，45 task neutral | 只剩3条M0 merge，父事件收敛大幅减少 |
| N13 full Decide | 9/9 batch请求失败 | 需逐 pair repair |
| N13 pair repair | 106/106失败 | 106个 pair保持 UNJUDGEABLE/不合并 |
| N12 assignment | 186/188成功，2个 item fallback | 两个 Atomic安全 CREATE_NEW singleton |

N13 ledger 为176 SUCCEEDED、106 FAILED：成功部分主要来自不需要本次有效 LLM响应的确定性/已有决策路径；失败部分没有形成错误 merge，而是保守拆分。系统的“局部异常不扩大成文档失败”目标得到验证，但代价是显著 Package fragmentation。

## 6. 质量评估状态

### 6.1 Mention / Field / N9

本轮 Gold evaluator 在写入任何完整结果前因402退出，parts=0。因此下列指标不可评估：

- Mention Precision/Recall/F1及bad case分布；
- predicate/participant/metric/fiscal period准确率；
- N7 candidate coverage、N9 MERGE Precision和conditional Recall。

不能使用 R1 的 Mention/Field评估代替：R1和R2是真实模型的两次不同输出，R1为243 Mention，R2为240 Mention。

### 6.2 Package 与碎片化：只表示402降级后结果

离线 Package Gold evaluator不需要新模型调用，因此完成。不同覆盖集的发布口径如下：

| 指标 | 基准 R5 | R2降级结果 | 变化 |
| --- | ---: | ---: | ---: |
| 对齐 Atomic | 85 | 92 | +7 |
| Pair Precision | 91.07% | 94.75% | +3.68pp |
| Pair Recall | 75.83% | 59.29% | **-16.54pp** |
| Pair F1 | 82.75% | 72.94% | **-9.81pp** |
| fragmented Gold groups | 4/8 | 6/8 | +2 |
| excess components | 7 | 14 | +7 |
| singleton components | 9 | 17 | +8 |
| missed pair links | 182 | 322 | +140 |

在两轮共同高置信对齐的60个 Gold Atomic上，公平同集比较为：

| 指标 | 基准 R5 | R2降级结果 | 变化 |
| --- | ---: | ---: | ---: |
| Pair Precision | 89.77% | 89.71% | -0.07pp |
| Pair Recall | 67.23% | 51.91% | **-15.32pp** |
| Pair F1 | 76.89% | 65.77% | **-11.12pp** |
| fragmented groups | 1/3 | 2/3 | +1 |
| excess components | 3 | 7 | +4 |
| singleton components | 2 | 7 | +5 |
| missed pair links | 77 | 113 | +36 |

Micron FQ3 earnings在共同集从4个组件（18+2+1+1）变成7个组件（16+1+1+1+1+1+1）；全对齐集则为9个组件。该模式与45个Wave C和106个N13 pair保守失败完全一致。它证明降级策略保住 Precision和成功率，但不证明正常余额下的N12/N13代码本身会产生同样Recall回归。

## 7. 综合判断

### 已验证可以保留

- package-anchor并发原子复用修复；
- Field局部失败隔离和resume跳过；
- stage snapshot、candidate card、批量audit/task、chunk Apply；
- pair registry read=0；
- idempotent FINALIZED复用；
- pre-Package墙钟约25.92%的真实改善。

### 需要后续处理

1. Provider充值后，只需重新做一次**全新正式运行**或从 Package 前冻结边界做受控重放；当前R2已经被402降级结果污染，不宜仅补跑Gold evaluator后宣称完整质量通过。
2. Embedding executor应记忆安全batch size，当前反复64→32→16→8拆分造成47次零Token失败记录和额外延迟。
3. N12两个 `N12_UNJUDGEABLE_FAILED_SINGLETON` 应单独复核，但它们已正确局部降级，没有扩大成epoch失败。

### 最终验收结论

**修复本身通过；工作流可跑通；完整质量验收因provider余额耗尽未完成。**

不能回滚Field修复：真实R2证明它消除了阻断并保留了约20%的Field Token收益。也不能依据R2的Package Recall回滚N12/N13语义，因为最关键的151个late decision task没有得到有效provider响应。当前最准确的发布判断是：运行时修复可保留，但在正常余额下完成一轮未污染的Package/N9质量复验之前，不应宣布整套优化正式验收通过。

## 8. 留存产物

R2目录：`D:\DoxAgent_CDECR_Acceptance_20260808_DeterministicRuntime_R2`

- `cdecr_30_deterministic_runtime.sqlite3`：真实FINALIZED Registry；
- `cdecr_30_deterministic_runtime_report.json`：生产运行报告；
- `package_gold_eval.json`：402降级结果的离线Package评估；
- `mention_eval.stderr.log`、`n9_eval.stderr.log`：HTTP 402原始证据；
- `stdout.log`、`stderr.log`：生产运行日志；
- `fixed_30_step4_manifest.json`：固定30篇manifest。

