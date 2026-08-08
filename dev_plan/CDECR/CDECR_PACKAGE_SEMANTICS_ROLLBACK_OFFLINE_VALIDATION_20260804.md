# CDECR Package 语义窄回滚与离线验证报告（2026-08-04）

## 1. 结论

两处指定的业务语义变化已完成窄回滚，其余本轮优化均保留：

1. N13 full Decide、compact DTO、候选覆盖、审计和 telemetry 继续运行，但 pair-local Apply 默认关闭；它不再消费 `SAME_PACKAGE` 生成 redirect，也不会产生该 Apply 路径的合并后 M1 embedding。late-wall 仍只记录预算，不参与 Apply 开关。
2. Wave C M0 恢复为：通过既有 hard boundary 后，共享 trusted artifact 或共享 canonical anchor 即可直接归入同一父事件；不再要求双方 `anchor_conflict=false`。
3. `object_scope_difference` 仍在 `PackagePairBoundary` 中计算并可供审计/N13 使用，但从 Wave C M3 的模型 `diff` 中移除。
4. Wave C Prompt 已恢复为父事件成员关系判断，并保留“共同 topic/source/entity 本身不足以合并”的边界。

没有启动完整 30 篇验收，也没有调用真实模型。

## 2. 实现范围

- 默认配置与压测环境：`CDECR_N13_PAIR_LOCAL_APPLY=false`。
- Bulk Epoch 默认构造参数与 CrossDocumentEngine 防御性默认值同步改为 `false`。
- 未恢复 late-wall admission；N13 Decide 和 telemetry 路径未删除。
- Wave C 的 compact DTO 继续使用 Package 字典，只对该节点调用 `compact_signals(include_object_scope=False)`。
- instrument、market measure、issuer、reaction、period、session、analyst 等 hard boundary 及 Candidate/Apply 前复检均未改变。
- N9、Mention、Field、Grounder 和 N13 Prompt 未回滚。

Wave C 当前 Prompt 为：

> Judge shared parent membership, not Atomic equality. Different child facts, missing detail, or Package-family differences do not by themselves create a new parent. Choose DIFFERENT_PARENT only for a material parent-boundary conflict; shared topic, source, or entity alone is insufficient. Return each pair once as SAME_PARENT, DIFFERENT_PARENT, or UNCERTAIN.

## 3. 单元与静态回归

- `tests/cdecr/test_bulk_epoch_v3.py`：21 passed。
- Ruff：全部通过。
- Bulk Epoch 集成断言确认：默认 `pair_local=false`，`n13_pair_local_applied_count=0`，且生成的 `n13_late_apply_plan_v1.payload.pair_local=false`。
- hard boundary 回归继续覆盖 market instrument/measure/session、issuer/reaction/period/analyst 等边界；同一 earnings artifact 下不同 child object 仍不构成 hard block。
- compact DTO 回归确认：`object_scope` 仍可在通用审计协议中输出，但 Wave C 请求明确不发送该项。

## 4. 固定结果离线重放

输入为本轮冻结 Registry、Package Gold 和 65 个双向互选 Atomic；重建出的 Wave C bounded candidate 恰为 64 对。重放没有模型调用。

| 场景 | Pair P | Pair R | F1 | FP | FN | Micron earnings 组件 | missed links | N13 Apply |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 冻结 Wave C + N13 Apply（本轮基线复现） | 89.10% | 72.97% | 80.23% | 34 | 103 | 5 | 103 | 10 |
| 冻结 Wave C + N13 Apply 关闭 | **91.37%** | 66.67% | 77.09% | **24** | 127 | 6 | 127 | 0 |
| 恢复 M0 + 冻结 M3 决策 + N13 Apply 关闭 | **91.37%** | 66.67% | 77.09% | **24** | 127 | 6 | 127 | 0 |
| 历史 R4 同一 65-Atomic 参考 | **90.78%** | **85.30%** | **87.96%** | 33 | 56 | 3 | 56 | 未启用 |

基线 TP/FP/FN 已逐项精确复现，证明重放的分区与当前发布指标一致。关闭 N13 Apply 后，已确认的 KOSPI/Nikkei/Dow/Stoxx 五事实 market-roundup 大簇不再形成；冻结投影中只残留原有的 KOSPI 与 SK Hynix 两事实误合并，未扩展到 Nikkei、Dow、Stoxx。

恢复后的 M0 在 64 对中识别出 4 个共享 artifact/anchor 的直接合并边，其中新增边与本轮已经发生的 M0/M3 合并链重叠，因此在冻结 M3 决策不变时没有额外改善分区。当前剩余的 Micron singleton 需要由恢复后的 Wave C Prompt 重新判定；旧 Prompt 已产生的 `DIFFERENT_PARENT/UNCERTAIN` 决策无法通过无模型重放改写。

## 5. 门槛判断与限制

| 门槛 | 严格离线投影 | 判断 |
| --- | ---: | --- |
| Package Precision ≥90% | 91.37% | 通过 |
| Recall ≥82% | 66.67% | 未验证通过 |
| F1 ≥85% | 77.09% | 未验证通过 |
| Micron earnings ≤3 个组件 | 6 | 未验证通过 |
| missed pair links ≤70 | 127 | 未验证通过 |
| market-roundup 大簇不得形成 | 未形成五事实大簇 | 通过 |

这里的 Recall/Micron 未通过不等于新 Prompt 无效，而是本次约束存在不可消除的验证边界：**不调用真实模型，就不能让 64 对按新 Prompt 重新决策。** 因而，本报告只能确认 N13 关闭与 M0 确定性语义；Prompt 已通过文本、Schema 和 DTO 对齐检查，但不能宣称完成了语义 A/B。

历史 R4 在相同 65-Atomic 口径下达到 P/R/F1=90.78%/85.30%/87.96%，Micron=3、missed links=56，说明恢复方向曾达到全部数值门槛，但它只能作为历史支持证据，不能替代新代码上的模型复验。

## 6. 进入 300 篇前判断

- 代码层窄回滚可以保留：它直接消除了 N13 Apply 的已确认 precision 回归，并恢复了 R4 父事件语义，且没有拆除效能与审计设施。
- 受“不启动真实测试验收”约束，当前不能对新 Prompt 的 Recall 恢复作强保证。
- 若 300 篇将直接承担正式验收，应把 Wave C Recall/Micron 结果视为重点观察项；若允许一个极小预检，最小充分动作是仅重判冻结的 56 个 Wave C M3 pair，而不是重跑 30 篇全流程。

机器可审计重放结果保存在：`D:\DoxAgent_CDECR_Acceptance_20260804_FinalNarrow_R1\package_semantics_rollback_offline_replay.json`。
