# CDECR 旧 30 篇真实验收：运行阻断与局部 A/B 评估报告

> 日期：2026-08-08  
> 对照基准：`D:\DoxAgent_CDECR_Acceptance_20260804_ThinkingDownshift_R5`  
> 本轮运行：`D:\DoxAgent_CDECR_Acceptance_20260808_DeterministicRuntime_R1`  
> 语料：复用基准的 `fixed_30_step4_manifest.json`，生产 loader 校验 30 篇、顺序、document ID 与 fingerprint 一致  
> Gold：复用旧 30 篇 Gold；未重新标注  
> 结论口径：本轮是一次真实失败验收，不把局部结果冒充完整 A/B

## 1. 总结性判断

本轮修改**尚不能跑通完整 30 篇 CDECR 工作流，不能进入放量状态**。

- 单文档阶段成功 30/30，得到 243 条 Mention，Evidence 259/259 VERIFIED。
- BULK_EPOCH 在 `FIELD_DECIDE` 因 1/416 个 Field 语义任务触发 `ImmutableRecordConflict`，epoch 被标记为 `PARTIAL`。
- 失败发生在 Atomic 构建之前，因此跨文档成功为 0/30，N9、N12/Wave C、N13 均未运行；Atomic、Package、Package 准召和碎片化均不可评估。
- 失败不是 provider 欠费、限流或 Schema 错误，而是新并发 Field 路径的确定性持久化竞态，并被 orchestrator 从单个 package-anchor 异常扩大成整个 epoch 失败。
- 可独立评估的上游质量也没有证明整体改进：Mention P/R/F1 分别回归 5.84/1.12/3.19pp；Field total 基本持平（-0.09pp），但 fiscal period 回归 9.73pp。
- Field 请求数和 Field Token 确有下降，但该收益目前被阻断性竞态完全抵消。Grounder item repair 请求从 13 增至 28，总 Token 增加 111.21%，是另一项明确的成本回归。

因此本轮验收结论为：**失败；需要先修复 Field 并发 get-or-create、局部降级和 checkpoint resume，再以全新 Registry 重跑同一 30 篇，才能评价 N9/Package 与完整耗时、Token。**

## 2. 测试控制变量

| 项目 | 基准 R5 | 本轮 |
| --- | --- | --- |
| 语料 | 固定 30 篇 | 同一 manifest 与 snapshot |
| Provider/模型 | DeepSeek official / `deepseek-v4-flash` | 不变 |
| M2/M3/M4 thinking | off / low / high | 不变 |
| strict structured output | 开启 | 开启 |
| Registry | 独立 Registry | 全新独立 Registry |
| Gold | 固定旧 Gold | 复用 |

运行前生产 `load_step4_corpus` 实测：`count=30`、`unique_message_ids=30`，首尾 source row 与基准 manifest 一致。本轮没有读取基准决策、没有复用历史 Atomic/Package，也没有在失败后篡改 Registry 继续投影。

## 3. 阻断问题与根因

### 3.1 实际失败点和影响范围

| 指标 | 结果 |
| --- | ---: |
| 单文档成功 | 30/30 |
| 文档级失败 | 0 |
| Field task | 415 SUCCEEDED / 1 FAILED |
| Epoch 状态 | `PARTIAL` |
| Epoch 停止阶段 | `FIELD_DECIDE` |
| Cross-document result | 0/30 |
| N9 / N12 / N13 | 未启动 |

失败 task：`2c0fe45f...42bf442bf9`；错误：`ImmutableRecordConflict`。

该 task 对应 7 条同文档 `local_package_hint.anchor`：raw anchor 均为 `Micron FY2026 Q3 earnings report`，canonical parent identity 均为 `earnings:COMPANY_MU:q3-fy2026`。同一 parent 在并发任务中还出现 `Micron FY2026 Q3 results`、`Micron fiscal Q3 2026 results`、`Micron FY2026 Q3 earnings call`、`Micron FY2026 Q3 results release/announcement` 等合法变体。

### 3.2 发生机制

`FieldCoreferenceResolver._create_entry()` 使用 namespace + parent identity seed 生成相同 registry ID `field:633538...`，这是正确的父事件归一目标；但当前实现先读取、后创建：

1. 多个 raw anchor group 并发检查同一 registry ID，均可能看到“尚不存在”；
2. 一个任务先创建该 ID；
3. 另一个任务仍执行 create，ID 相同但 canonical text 不同；
4. `create_field_registry_entry()` 将其视为不可变记录冲突；
5. `resolve_epoch()` 对任一 future 异常直接 `future.result()` 抛出；
6. BULK_EPOCH 顶层将整个 epoch 标记为 `PARTIAL`。

这是典型 TOCTOU 竞态。串行路径中，后到任务能看到已有 entry，并把不同表述合入 aliases；并行路径没有把“get-or-create + alias merge”做成原子操作。

### 3.3 同时暴露的恢复缺口

虽然 415 个成功 Field task 已写入 task ledger，但 `resolve_epoch()` 没有消费 `ledger.completed("FIELD")` 跳过已完成 task。若直接对同一 Registry 重启，会重新执行大量已成功 Field LLM 请求，不能视为合格的断点续跑。因此本报告没有直接重启，也没有用手工生成 `field_overlay_v1` 绕过失败。

## 4. 成功率、Evidence、Token 与耗时

### 4.1 成功率与 Evidence

| 指标 | 基准 R5 | 本轮 | 变化 |
| --- | ---: | ---: | ---: |
| 单文档成功 | 30/30 | 30/30 | 持平 |
| 跨文档成功 | 30/30 | 0/30 | **阻断回归** |
| Evidence VERIFIED | 238/239 | 259/259 | 99.58% → **100%** |
| Evidence 异常导致文档失败 | 0 | 0 | 持平 |

Evidence 改善是真实收益；但跨文档 0/30 意味着工作流业务交付失败，优先级高于该局部收益。

### 4.2 共同可比上游阶段

完整总 Token 和端到端墙钟不可比较：基准包含 N9/N12/N13，本轮没有。以下只比较两轮都完整执行的 Dreamer/Grounder/Judge/Field/Title embedding 阶段。

| 指标 | 基准 R5 | 本轮 | 变化 |
| --- | ---: | ---: | ---: |
| 模型调用 | 642 | 515 | -19.78% |
| Input Token | 775,409 | 792,693 | +2.23% |
| Output Token | 570,368 | 636,737 | +11.64% |
| Total Token | 1,345,777 | 1,429,430 | **+6.22%** |
| 累计模型延迟 | 4,369,532 ms | 4,628,472 ms | **+5.93%** |
| 模型失败 | 19 | 18 | -1 |

调用数下降但 Token 和累计延迟反升，说明批量化减少了请求外壳，却尚未控制单次输出和 recovery fan-out。

| 节点 | 调用 R5→本轮 | Total Token 变化 | 累计延迟变化 | 失败 R5→本轮 |
| --- | ---: | ---: | ---: | ---: |
| Dreamer | 30→30 | +0.95% | -1.08% | 0→0 |
| Grounder 主请求 | 30→30 | +7.15% | +6.63% | 8→6 |
| Grounder item repair | 13→28 | **+111.21%** | **+69.68%** | 1→4 |
| Grounder missing recovery | 9→7 | -11.09% | -2.09% | 5→3 |
| Grounder missing-item recovery | 8→8 | -13.19% | -28.76% | 4→5 |
| Judge | 28→29 | +8.87% | +6.61% | 0→0 |
| Title embedding | 30→30 | 0% | +348.55% | 0→0 |
| Field coreference | 240→195 | -14.00% | -26.52% | 1→0 |
| Field recall embedding | 254→158 | -61.02% | -8.51% | 0→0 |

Field coreference + recall 合计调用从 494 降至 353，Token 从 301,420 降至 251,030（-16.72%），证明批量/去重方向有效；但一个 parent-anchor 写冲突即可使全部收益归零。

### 4.3 墙钟口径

- 本轮 first pass 在失败处耗时 797,121 ms（约 13.29 分钟），不是完整端到端耗时。
- 基准从启动到 Field 完成的近似同口径为：pre-epoch 252,136 ms + Field 554,508 ms = 806,644 ms。
- 因此到相同阶段边界仅约 -1.18%；不能拿 13.29 分钟与基准完整 55.26 分钟宣称 76% 提速。
- 本轮 pre-epoch 约 337,485 ms，比基准约 252,136 ms 慢 33.85%；Title embedding 延迟和 Grounder/Judge 输出增长抵消了 Field 并发收益。

## 5. Mention 质量

| 指标 | 基准 R5 | 本轮 | 变化 |
| --- | ---: | ---: | ---: |
| Gold | 268 | 268 | 0 |
| 输出 Mention | 229 | 243 | +14 |
| Strict TP | 183 | 180 | -3 |
| Partial | 36 | 43 | +7 |
| FP | 10 | 20 | +10 |
| FN | 85 | 88 | +3 |
| Precision | 79.91% | 74.07% | **-5.84pp** |
| Recall | 68.28% | 67.16% | **-1.12pp** |
| F1 | 73.64% | 70.45% | **-3.19pp** |

结论：输出数增加没有换来 Recall，而是主要增加 Partial/FP；这是实质性回归。

主要分布：

- D11 一篇就损失 7 个 Strict TP、增加 7 个 FN。该文档没有协议错误，但 Grounder 输出 Token 从 16,814 降到 9,398，Judge 从 11,447 降到 8,030，形成明显 under-generation；遗漏 Qualcomm Q2 handset revenue、Meta 首客户、两项 hyperscale deal、Qualcomm/Micron 股价反应、UBS target 等事实。
- D17 Grounder 主请求 `invalid_json` 后走 missing recovery；新增 3 个 FP，并出现 Sandisk/Micron SCA 主体错位、背景预期被当事件等问题。
- D5、D24、D27、D29 主要是 compound/fragmentation：同一 Gold 的必要字段被拆到多条 Mention，或多个可独立 Gold 被压进一条 Mention，导致 Strict TP 变 Partial/FN。
- 新增 FP 主要集中于背景市场表现、技术位、估值/分析上下文以及 article context 中非 Gold 的次级事实；说明当前扩量缺少相应精度约束。

## 6. Field 质量

| Field | 基准 R5 | 本轮 | 变化 |
| --- | ---: | ---: | ---: |
| predicate | 73.88% | 74.25% | +0.37pp |
| participant | 73.03% | 75.75% | +2.71pp |
| metric | 72.92% | 72.08% | -0.84pp |
| fiscal period | 89.47% | 79.75% | **-9.73pp** |
| total | 74.84% | 74.75% | -0.09pp |

Field total 基本持平，participant 有改善；metric 小幅回归。fiscal period 的下降需要处理，但不能简单归咎于 Field coreference：6 个从 CORRECT 变 INCORRECT 的重点样本中，D9/D11/D29 是上游 Mention 完全缺失，D5 是复合事实拆分，只有 D1 的 `this year` 未标准化为发布年 2026 更接近直接时间字段问题。

本轮失败的 7 条 package anchor 不属于上述 predicate/participant/metric/fiscal Gold 统计，因此 Field 表不能掩盖 package-anchor 并发竞态。

## 7. N9、Package 与碎片化

本轮没有形成任何 Atomic 或 Package，以下指标均为**不可评估**，不是 0 分，也不能沿用历史值：

- N7 candidate coverage；
- N9 MERGE Precision / conditional Recall / CREATE_NEW accuracy；
- Atomic pair P/R/F1、最大 cluster、hard-conflict violation；
- Package pair P/R/F1；
- fragmented Gold groups、excess components、singleton components、missed pair links；
- N13 Token、pair 数、每个新增 join 的 Token 成本；
- `pair_registry_read_count=0`、batch audit、chunk apply、embedding batch 等最终 telemetry。

任何使用旧 N9 决策、历史 Package 或离线投影补齐这些表的做法都会掩盖当前真实阻断，故未采用。

## 8. 修复与回滚必要性判断

### P0：必须修复后才可重测

1. 将 package-anchor parent 的 `get/create + alias merge` 改为单事务原子操作。相同确定性 ID、相同 namespace 的 parent anchor 应复用 root 并合并 alias；不同 namespace 或不兼容身份仍保留冲突。
2. 取消单个 Field group 异常对整个 epoch 的放大。只有下游完全无法消费的错误才阻断；本例应局部复用已存在 parent root，至少也应将该 group 标记为 unresolved/degraded 后继续。
3. 真正接通 Field checkpoint resume：启动时读取已成功 task，只执行 failed/missing task；禁止重放 415 个已完成 LLM task。
4. 增加并发回归：两个及以上不同 raw anchor、同一 parent identity 同时创建，验证得到一个 root、aliases 完整、无 epoch failure。

### P1：质量与成本复核

1. 排查 Grounder item repair 从 13→28 的触发分布；单条 repair 语义可保留，但需确认是否重复修复、错误分类过细或主请求批量输出扩大了 repair fan-out。
2. 对 D11 做冻结输入的 Dreamer/Grounder/Judge 节点回放，定位 7 个事实是在 candidate、grounding 还是 Judge 阶段丢失；不应直接用扩大 Mention 数修 Recall。
3. 对 D17 的 recovery 做主体约束复核，防止 Sandisk/Micron、具体报告/背景预期在恢复路径错位。
4. fiscal period 优先修发布日可确定解析（如 `this year`），而不是增加宽松 LLM 猜测。

### 当前不建议的动作

- 不建议整体回滚批量 Field 去重：其调用、Token、累计延迟均显示正收益。
- 不建议在这次失败结果上判断 N9/N12/N13 或 N13 planner 回滚，因为它们根本没有运行。
- 不建议直接续跑当前 Registry并作为正式 A/B：当前 resume 未跳过已完成 Field task，会污染调用量和成本。修复后应使用全新 Registry 做正式重跑；当前 Registry只用于根因复现测试。

## 9. 最终结论

本轮同时暴露了三类问题：

1. **确定的新运行时回归**：并发 package-anchor 持久化竞态 + epoch 级失败放大，导致完整业务成功率从 100% 降为 0%。
2. **确定的上游质量回归**：Mention P/R/F1 均下降，D11 under-generation 和 D17 recovery 错位最突出；Field total 持平但 fiscal period 明显下降。
3. **局部效能收益但尚不可交付**：Field 请求/Token 确实下降，却被 repair fan-out、上游延迟和硬阻断抵消。

所以答案不是“优化已跑通但指标有波动”，而是：**当前优化尚未跑通；先做窄 P0 修复，再以同一 30 篇全新 Registry 重跑，才有资格评价 N9/Package、碎片化和完整效能。**

## 10. 留存产物

- `cdecr_30_deterministic_runtime.sqlite3`：真实失败 Registry；
- `cdecr_30_deterministic_runtime_report.json`：生产 evaluation report；
- `mention_gold_eval.json`：本轮 Mention Gold 评估；
- `field_gold_eval.json`：本轮 Field Gold 评估；
- `root_cause_copy.sqlite3`：只用于只读根因枚举的副本；
- `stdout.log` / `stderr.log`：运行日志（stderr 为空）；
- 基准产物仍位于 `D:\DoxAgent_CDECR_Acceptance_20260804_ThinkingDownshift_R5`。

