# MU CDECR 996 Atomic 运行结果排查报告

> 运行范围：本地 DoxAgent V2，`market=US`、`ticker=MU`，仅 CDECR。
>
> 运行完成时间：2026-09-09 16:01:33 UTC。
>
> Bulk Epoch：`bulk-epoch:aa198e495c6d456b7fcbdd97`。
>
> 参考口径：[MU R2 O2 Deterministic Alignment 真实验收报告](C:/Users/WEIXUANXIE/Desktop/DoxAgent/dev_plan/CDECR/CDECR_MU_R2_O2_DETERMINISTIC_ALIGNMENT_REAL_ACCEPTANCE_REPORT_20260827.md)。本报告按要求省略 Gold 比较；没有据此推导 precision、recall 或语义正确率。

## 结论先行

1. **996 这个数量本身可以由本次运行的持久化记录闭合解释，作为“最终 canonical active Atomic 数量”是可信的。** 计算链为：

   ```text
   1,205 Event Mention
   - 208 SAME_EVENT merge
   = 997 Atomic head
   - 1 N9_SAME_SINGLETON_ABSORPTION redirect source
   = 996 个最终 canonical Atomic
   ```

2. **但不能把这 996 个 Atomic 直接视为“完整且语义可靠的 Event Library 结果”。** 本次没有可比 Gold，且只由 Finnhub 实际贡献抓取数据；另有文档处理失败、Field 失败和 late stage 零正向合并，因此语义 precision/recall 未被证明。

3. **本次运行存在一个比数量问题更严重的闭合性异常：Bulk Epoch 内部记录的是 `atomic_count=12`、8 个 Package，而运行结束后的 durable registry/activity 是 996 个 active canonical Atomic。** Package membership 也只有 12 个不同 Atomic。由此判断：

   - `996` 是最终 Atomic 分区/activity 层的数量，计数链可复核；
   - `12` 是运行中临时 eligibility 视图下的内部计数，不是总 Atomic 数；
   - Package/下游快照没有覆盖完整的 996 集合，当前不能作为 release-grade 结果。

4. **最终判定：** 本次 CDECR 是“技术上 FINALIZED、数量可审计、语义与下游闭合未验收”的诊断性产物，不应直接作为完整 Canonical Event Library 或下游生产决策输入。

## 1. 冻结输入与抓取覆盖

### 1.1 抓取结果

| 层级 | 数量/状态 | 解释 |
|---|---:|---|
| Historical candidates | 302 | 15 个日区间的 Finnhub 返回量；Benzinga 15 个日节点均成功但返回 0 条 |
| Selected candidates | 189 | `SELECTED`；全部为 `NEWS`、主 ticker 为 MU |
| Rejected candidates | 113 | 109 条因 `body_not_complete_like`，4 条因时间窗外或缺少时间 |
| Document processing | 187 成功，2 `provider_error` | Epoch 的 `message_count=187` 正是可用于 CDECR 的成功文档数 |
| Event Mention | 1,205 | 来自 187 个成功文档；另有 17 个成功文档没有抽出 Mention |

选中的 189 条 SourceMessage 的发布时间范围为 2026-08-26 至 2026-09-09；其中 170 条至少产生一个 Event Mention。平均值为：

- 每条选中消息约 6.38 个 Mention；
- 在有 Mention 的消息中，平均约 7.09 个 Mention。

这解释了为什么 Atomic 数量可以远大于文档数：CDECR 处理的是消息中的独立事件/命题，不是“一篇文章对应一个 Atomic”。但这只是数量形成机制，不是语义正确性的证明。

### 1.2 Provider 覆盖限制

两条历史 Provider 路径都执行了按日节点，但本次只有 Finnhub 产生了候选数据。Benzinga 节点状态为 `SUCCEEDED`，结果列表长度在所有 15 个日区间均为 0；这不是节点抛错，因此不能简单归类为“请求失败”，但它意味着本次没有第二来源的交叉覆盖。

因此，996 个 Atomic 的召回上限首先受单一有效消息源、113 条被过滤消息、2 个文档处理失败和 17 个零 Mention 成功文档影响。

## 2. 996 的精确数量推导

### 2.1 Assignment 层闭合

`atomic_assignment_decisions` 共 1,205 条，与 1,205 个 Event Mention 一一对应：

| Assignment | 数量 |
|---|---:|
| `CREATE_NEW` | 997 |
| `MERGE / SAME_EVENT` | 208 |
| 合计 | 1,205 |

所以第一步是：

```text
1,205 Mention - 208 SAME_EVENT = 997 个新建 Atomic head
```

这不是重复计数：`CREATE_NEW=997` 本身与上述算式、`atomic_event_heads=997`、`atomic_event_recall=997` 同时一致。

### 2.2 N9 canonicalization 层闭合

`atomic_event_redirects` 只有 1 条，原因是 `N9_SAME_SINGLETON_ABSORPTION`。被吸收的一侧只有 1 个 Mention，目标侧有 15 个 Mention；两者属于同一事件身份路径。该 redirect source 仍保留在历史 head/recall 记录中，但不再属于当前 canonical active view。

因此：

```text
997 Atomic head - 1 个被 redirect 的 source = 996 个当前 canonical Atomic
```

最终两个独立 Atomic 分区都记录了 996 个 event ID：

- `atomic_partition_v1.event_ids = 996`；
- `atomic_late_partition_v1.event_ids = 996`；
- 运行结束后 activity 中 `active_atomic_count = 996`；
- 三者排除的是同一个 redirect source，没有发现额外的非 head ID 或孤儿 ID。

### 2.3 为什么不是更少

本次 Mention 的事件身份较细：共覆盖 12 个 event family、631 个不同的 normalized predicate。N9 主阶段只有 208 个 `SAME_EVENT` 合并，晚收敛阶段的结果是：48 个候选任务、39 个 hard-blocked、9 个 L1 task，`applied_edges=0`、`l0_merge_count=0`、`l1_merge_count=0`。

这表示本次运行没有通过 late stage 进一步把大量候选合并掉，所以最终数量自然接近 997，而不是显著低于 997。它可以解释 996 的形成，但同时提示存在“保守切分/过度拆分”的可能；没有 Gold 或人工抽样，不能判断这些新 Atomic 是否真的应该保持独立。

## 3. 根因：为什么 Epoch 内部显示 12，而最终结果显示 996

这是本次最重要的运行时问题。

### 3.1 观察到的矛盾

| 观察面 | 结果 |
|---|---:|
| Durable `atomic_event_heads` | 997，含 1 个 redirect source |
| Final canonical active Atomic | 996 |
| `atomic_partition_v1` | 996 event IDs |
| `atomic_late_partition_v1` | 996 event IDs |
| `bulk_epochs.result_json.atomic_count` | **12** |
| `runtime_activity_v1.eligible_atomic_ids` | **12** |
| Package 数量 | 8 |
| Package membership 覆盖的不同 Atomic | **12** |

因此，Epoch result 中的 12 不能解释为“本次只产生了 12 个 Atomic”；如果那样解释，就会与 durable registry、Atomic partition 和最终 activity 同时冲突。它实际是运行中 eligibility filter 下的计数。

### 3.2 代码路径定位

当前代码的生命周期是：

1. [`workflow_runner.py:112`](C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/cdecr_integration/workflow_runner.py:112) 在 Bulk Epoch 前激活 60 天 runtime eligibility，并在 Epoch 内保存 `runtime_activity_v1`。
2. [`registry.py:1484`](C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/cdecr/registry.py:1484) 的 `list_current_atomic_events()` 会按内存中的 `_runtime_eligible_atomic_ids` 过滤结果。
3. Durable Atomic 写入后，只有 [`registry.py:2057`](C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/cdecr/registry.py:2057) / [`registry.py:2288`](C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/cdecr/registry.py:2288) 的特定 batch 写入路径会扩展这个内存集合。
4. 本次运行中，997 个 head 已经持久化，但保存下来的 `runtime_activity_v1` 只有 12 个 eligible ID，说明 durable write 与 runtime eligibility closure 已经分叉。
5. Package 阶段读取的是被 eligibility 过滤后的 Atomic 集合，所以它在自己的 12-item 输入上形成了 8 个 Package；其内部校验并不能发现“过滤前的真实 active 集合是 996”。
6. 随后 coordinator 在解除临时 eligibility 后，按 durable registry 重新投影 activity，并在 [`coordinator.py:238`](C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/cdecr_integration/coordinator.py:238) 处得到 996，于是最终 activity/partition 又显示 996。

本次使用了停点继续和已生成的阶段产物；[`engine.py:1107`](C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/cdecr/bulk_epoch/engine.py:1107) 的阶段产物复用、以及 [`cross_document.py:4392`](C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/cdecr/cross_document.py:4392) 等非 `save_atomic_stage_batch()` 写入路径，都没有把“持久化 Atomic 集合”和“临时 eligibility 集合”之间的闭合关系作为最终断言。现有 durable artifact 没有逐个记录 985 个未进入临时集合的具体写入分支，所以可以确定根因在 eligibility 生命周期/恢复边界，不能把缺失进一步武断归因给某一次模型决策。

### 3.3 对 Package 结果的影响

Package V3 的投影代码要求输入集合中的每个 active Atomic 都恰好属于一个 Package（[`package_projection.py:267`](C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/cdecr/package_projection.py:267)）。本次它只看到 12 个 Atomic，因此内部检查可以通过；但在最终 996 active Atomic 视图下，只有 12 个不同 Atomic 有 Package membership，至少 984 个 canonical active Atomic 没有进入 Package membership。

所以本次 Package 数量 8 可以作为“对 12-item 临时输入的内部产物”保存，但不能作为 996 Atomic 的完整 Package 投影。这个问题直接降低下游 Frozen View/Delta 的可信度。

## 4. 完成状态与诊断残留

### 4.1 可以确认的完成事实

- Control run：`SUCCEEDED`，`manual_resume_required=false`。
- CDECR child result：`FINALIZED`，`document_count=187`、`eligible_document_count=187`。
- Bulk Epoch：`FINALIZED`。
- 三个 SQLite 数据库（control/runtime/staging）的 `PRAGMA integrity_check` 均为 `ok`。
- N9 主任务 1,205 条均为 `SUCCEEDED`；N9 late 任务为 `SUCCEEDED`，没有触发 N9 provider fail-open。
- 没有执行 O2 或其他 DAG 节点；本报告没有额外重算业务结果。

### 4.2 不能忽略的残留

- Field task：2,733 `SUCCEEDED`、14 `FAILED_TERMINAL`、4 个旧的 `RUNNING` ledger rows。
- 14 个 Field 失败中，12 个为 `provider_data_inspection_failed`，2 个为 `ImmutableRecordConflict`。
- 2 个文档处理为 `provider_error`，没有产生 Mention。
- Late stage 没有正向 merge，不能用它证明跨文档 fragmentation 已经解决。

这些残留没有改变上面的 996 数量算式，但会削弱字段完整性、合并证据和召回可信度。`FINALIZED` 只说明 epoch 的持久化生命周期完成，不等于无诊断缺陷或语义验收通过。

## 5. 可信度判定

| 判定维度 | 结论 | 依据 |
|---|---|---|
| 数据库持久化与完整性 | 通过 | 三库 integrity check 为 `ok`，控制面和 epoch 均完成 |
| 996 数量及 canonical 去重链 | 通过 | 1,205 Mention、208 merge、997 heads、1 redirect、996 partitions/activity 彼此闭合 |
| Atomic 是否过度拆分 | 未证明 | 997 个 `CREATE_NEW`、late stage 0 merge；无 Gold/人工标注 |
| Atomic 语义 precision/recall | 未证明且偏低可信 | 单一有效 Provider、113 条过滤、2 个 provider error、17 个零 Mention 文档、14 个 Field terminal failure |
| Runtime eligibility 闭合 | 不通过 | durable canonical active=996，但 Epoch/runtime artifact 仅 12 |
| Package 完整覆盖 | 不通过 | 8 个 Package 只有 12 个 Atomic membership，未覆盖 996 active 集合 |
| 作为生产/Canonical 发布结果 | 不建议接受 | 下游输入集合与最终 active 集合不一致 |

### 最终回答

**996 这个数可信，996 个 Atomic 的完整性和语义可信度不可信。** 更准确地说：

- 可以信任它是本次 durable registry 中按 redirect 排除后的 canonical Atomic 数量；
- 不能信任它已经代表完整消息覆盖，也不能信任每个 Atomic 的语义切分和字段都正确；
- 不能信任本次 8 个 Package 已完整承载这 996 个 Atomic；
- 因此本次结果适合排查、继续修复和作为中间证据，不适合直接发布或供下游生产决策消费。

## 6. 修复优先级

1. **先修 eligibility closure。** 在所有 Atomic apply、resume、artifact reuse 和 direct save 路径后，强制重建或校验同一份 durable eligible set；最终必须显式断言 `final active Atomic set == Atomic partition set == Package membership set`（按 redirect 规则归一化）。
2. **修复计数口径。** `bulk_epochs.result_json.atomic_count` 应明确标记为 filtered eligibility count，或改为最终 durable canonical count，避免 `12` 被误读为本次总 Atomic 数。
3. **补一条中断/续跑回归测试。** 覆盖“已有 Mention、Atomic 阶段部分完成、重新激活 eligibility、继续 Package/finalize”的路径；现有 runtime activity 测试只覆盖普通 batch 扩展，不能覆盖本次闭合异常。
4. **补 provider 日级可观测性。** Benzinga 的成功空结果必须在验收 artifact 中显式标注为 `empty`，不能只以节点 `SUCCEEDED` 掩盖单一来源覆盖。
5. **清理诊断残留后再重跑。** 处理 14 个 Field terminal failure 和 4 个 stale ledger rows；修复后尽可能从现有安全 checkpoint 继续，不应在未修复 eligibility closure 前把本次 996 直接提升为正式结果。

## 7. 产物索引

- [CDECR dispatch result](C:/Users/WEIXUANXIE/Desktop/DoxAgent/.tmp/cdecr-mu-v2-local-final-20260910/initialization/cdecr-dispatches/036cf20266c84d7f8a90422248e3f6c7/result.json)
- [CDECR worker log](C:/Users/WEIXUANXIE/Desktop/DoxAgent/.tmp/cdecr-mu-v2-local-final-20260910/initialization/cdecr-dispatches/036cf20266c84d7f8a90422248e3f6c7/worker.log)
- [Control SQLite](C:/Users/WEIXUANXIE/Desktop/DoxAgent/.tmp/cdecr-mu-v2-local-final-20260910/initialization/control.sqlite3)
- [Runtime SQLite](C:/Users/WEIXUANXIE/Desktop/DoxAgent/.tmp/cdecr-mu-v2-local-final-20260910/initialization/workspaces/init-mu-0e4da523297b4dbcaf5b8598b370dd74/registry/US/MU/runtime.sqlite3)
- [Historical staging SQLite](C:/Users/WEIXUANXIE/Desktop/DoxAgent/.tmp/cdecr-mu-v2-local-final-20260910/initialization/workspaces/init-mu-0e4da523297b4dbcaf5b8598b370dd74/state/jobs/cdecr-init-us-mu-4a1817d396ee65e3b48e/historical_staging.sqlite3)
- [Original stopped-run backup](C:/Users/WEIXUANXIE/Desktop/DoxAgent/.tmp/cdecr-mu-v2-local-retry-20260909)

