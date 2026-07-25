# CDECR N11–N13 当前 30 篇测试集真实验收报告

日期：2026-07-26  
结论：**技术结构验收通过，业务语义验收未通过，不满足正式开放预期。**

## 1. 验收范围与真实性边界

- 当前来源 Registry 共 30 篇 SourceMessage。
- 只有 15 篇具备完整且一致的 N10 当前 Atomic，可在不重跑 N4–N10 的前提下直接从 N11 开始。
- 这 15 篇覆盖 41 个冻结 Atomic；另外 15 篇没有可复用 Atomic，不能被静默补齐或计入 N11–N13 成功率。
- 所有 30 篇在候选历史 Registry 中的 Source fingerprint 一致，但不同历史 Registry 的 Atomic 覆盖均只有 15–16 篇。为避免混合不同上游版本，本次只采用最新 `phase12_shadow_30_20260725.sqlite3` 作为冻结输入。
- 本次实际向配置的 DashScope 发送了入选测试内容所需的 Atomic、Surface Evidence 和 Package 候选；最终执行 Registry 与前两份失败证据库隔离。

## 2. 执行结果

| 指标 | 结果 | 判定 |
|---|---:|---|
| 可直接从 N11 开始的文档 | 15/30 | 覆盖不足，不能代表完整 30 篇 |
| 固定输入波数 | 3 | 通过 |
| 文档波次执行 | 45/45 succeeded | 通过 |
| 冻结 Atomic | 41 | 输入保持不变 |
| 当前 Package | 24 | 结构有效 |
| 当前 Membership | 41 | 通过 |
| 多重活跃 Membership | 0 | 通过 |
| ACTIVE / FROZEN / QUARANTINED | 24 / 0 / 0 | 通过 |
| 缺失 Package Embedding | 0 | 通过 |
| Package Embedding hash 不一致 | 0 | 通过 |
| 当前 self-external candidate | 0 | 通过 |
| Wave 1→2 Pair Jaccard | 0.9907407407 | 通过，阈值 0.90 |
| Wave 2→3 Pair Jaccard | 0.9907407407 | 通过，阈值 0.90 |
| 结构验收 | PASS | 通过 |

两次 Jaccard 都低于 1.0，变化来自 SK Hynix 样本：首波为 4 个 Package，第二波收敛为 3 个，第三波保持 3 个。该变化是 N13 延迟合并，并非写入或 membership 损坏。

## 3. M4 辅助业务审阅

M4 仅作为辅助 Reviewer，不能替代人工 Gold 签字。

| 指标 | 结果 | 目标 | 判定 |
|---|---:|---:|---|
| Package 数 | 24 | — | — |
| 非内聚 Package | 1 | 0 | 失败 |
| False Merge Package | 1 | 0 | 失败 |
| Overexpansion rate | 4.17% | 0 | 失败 |
| 高相似候选 Package Pair | 17 | — | — |
| 漏合并 SAME_PACKAGE Pair | 0 | 0 | 通过 |
| Fragmentation candidate rate | 0% | 约定阈值内 | 通过 |
| UNCERTAIN Pair | 0 | — | 通过 |
| 当前 external candidate | 36 | — | — |
| external candidate accuracy | 91.67%（33/36） | 未约定 | 有缺陷 |
| 人工 Gold Precision / Recall | 未执行 | 需达到约定阈值 | 未签字 |

因此 `semantic_acceptance_passed=false`。

## 4. False Merge 根因

失败 Package：

`package:90027bb97905a1a98ff97e93`

当前成员：

1. `atomic:735b15b215c798ea1fabac2e`
   - canonical title 为 Mizuho 对 Micron 的评级与目标价调整。
   - 但该冻结 Atomic 已同时包含：
     - Mizuho 的 analyst action；
     - Citi 对 Micron 的 Buy 评级与目标价调整。
2. `atomic:b65306e217abcfcfb045cc8b`
   - Citi 对 Micron Strategic Customer Agreements 未来收入贡献的预期。

M4 将第二个 Atomic 判定为错误成员，因为最终 Package 以 Mizuho report 为标题，却混入 Citi report 内容。

本地原文核对显示根因更深一层：`atomic:735...` 在进入 N11 前就已经把 Mizuho 与 Citi 两家机构的 analyst action 合成一个 Atomic。`atomic:b653...` 与其中 Citi action 来自同一篇 Citi 二手报道，从 Citi report 边界看本应同包；但加入后会进一步污染以 Mizuho 为标题的 Package。

结论：

- 输出层面必须计为 False Merge，未达到“False Merge Package 数量为 0”的硬门槛。
- 主要根因是冻结 N7–N10 Atomic 的跨机构过度合并。
- N11–N13 仍缺少对“单个 Atomic 已包含互斥父容器身份”的污染隔离或人工复核机制。
- 本轮方案明确不改写 EventMention/Atomic 历史，因此不能在 Package 层无损修复该输入。

## 5. External candidate 错误

3 个错误 candidate 都把“Micron 披露内容 → 股价波动 Package”记录为 `MARKET_REACTION_TO`：

1. Micron CEO 关于 AI 革命的陈述。
2. Micron 投资和超过 80% operating margin 的财务表现。
3. Micron 管理层约 20% 环比收入增长 guidance。

目标 Package 均为 `package:a56b8cc23969a63131f0061b`（Micron stock price surge）。

这些事件可以是股价变化的触发信息，但当前 relation 方向是 `source_event -> target_package`。按 `MARKET_REACTION_TO` 的语义，披露事件本身不是对股价的市场反应，方向与分类均不正确。正式 N14 前需要：

- 明确 relation 的主语/宾语方向；
- 区分 `MARKET_REACTION_TO` 与“可能触发市场反应”的因果候选；
- 保持当前仅 candidate、不正式落 N14 relation 的边界。

## 6. 已通过的核心业务场景

- Micron fiscal Q3 earnings disclosure 的 actual、guidance、财务指标与管理层陈述聚合为一个 15-member Package，未因 EventFamily 差异碎片化。
- Apple、Sandisk、SK Hynix 与 Micron analyst Package 没有发生跨公司错误合并。
- 17 个高相似跨包候选中未发现应合并但被拆开的 Pair。
- 正常大 Package 未因成员数量、EventFamily 数量或长时间跨度被冻结。
- SK Hynix 市场反应边界修复不再产生不可变写入冲突。
- 所有当前 Package Profile 都有与最终 Profile 精确匹配的 Embedding hash。

## 7. 稳定性与调用成本

| 波次 | 模型调用 | Input tokens | Output tokens |
|---|---:|---:|---:|
| Wave 1 | 69 | 672,158 | 43,996 |
| Wave 2 | 63 | 941,306 | 47,878 |
| Wave 3 | 62 | 908,419 | 47,129 |
| 合计 | 194 | 2,521,883 | 139,003 |

其中：

- `package_merge`：157 次，2,359,711 input tokens，占总 input tokens 约 93.57%。
- `package_assignment`：16 次；固定 assignment 的复用基本生效。
- 后两波仍分别发生 63、62 次模型调用，主要来自 N13 全局 Pair 复核。

三波验收本身故意使用不同 processing key，因此不能直接等同于同 key 的生产幂等重放；但结果显示 N13 在固定状态上仍有较高重复复核成本。若生产会对相同 Atomic 集合建立新 run，需要增加 Pair decision 的状态级复用或更窄的 touched-package 召回。

## 8. 本轮执行中修复的问题

1. 常规 N12 与 N13 reaction repair 使用相同 Wire-shadow audit identity，导致同 run 不可变记录冲突。
2. N13 从错误合并 Package 拆出市场反应事件时复用已重定向 singleton ID，无法形成真实独立 Package。
3. N12 模型在 candidate relation 正确时，可能返回不一致的派生 ranking、selected target 或 selection reason，初始与 repair 输出完全相同并导致整篇失败。

修复后：

- Wire audit identity 包含 operation、trigger、attempt。
- boundary split 使用确定性的独立 Package identity。
- N12 只基于模型已给出的 candidate relation 重建冗余派生字段，并写入 `PACKAGE_N12_NORMALIZATION` 审计，不改变业务关系判定。
- 相关离线回归 33/33 通过，Ruff 与 mypy 通过。

## 9. 最终判定

### 可以确认

- N11–N13 技术执行、存储一致性、唯一 membership、Profile/Embedding 生命周期和三波总体稳定性达到预期。
- 本轮优化显著消除了此前的 fragmentation candidate 和 self-external candidate 问题。

### 不能确认

- False Merge 仍为 1，不满足硬门槛 0。
- external candidate 仍有 3 个方向/分类错误。
- 只有 15/30 篇具备可复用前序记录，不能声明完整 30 篇验收通过。
- 尚无人工 Gold Package Pair Precision/Recall 与人工签字。

### 发布建议

**不开放正式 N14，不宣称 N11–N13 业务验收通过。**

下一步应优先：

1. 回到 N7–N10 修复不同机构 analyst action 被合成同一 Atomic 的问题，并重新生成缺失的 15 篇 Atomic。
2. 为含互斥父容器身份的冻结 Atomic 增加 Package 污染审计/人工复核入口。
3. 明确 external relation 的方向合同。
4. 在完整 30 篇、人工 Gold 上重跑 N11–N13 Precision/Recall 与三波稳定性验收。
5. 单独评估 N13 Pair decision 的跨 run 复用，降低固定输入的重复调用。

## 10. 证据文件

- 最终 Registry：`.tmp/cdecr/n11_n13_current30_reusable15_20260726_w3_r3.sqlite3`
  - SHA256 `13F48113400D1E6D1207F630C49E21F8CDA90353D250FD5C44866FB34F788EC6`
- 三波验收报告：`.tmp/cdecr/n11_n13_current30_reusable15_20260726_w3_r3.json`
  - SHA256 `C2E347139D1C80890C0F1D764101B19D6C13A7BD2F4489C933132DD70A8BFABD`
- M4 辅助审阅：`.tmp/cdecr/n11_n13_current30_reusable15_20260726_w3_r3_m4_review.json`
  - SHA256 `DBDACE584DE97C5DF1FD78B231099B9A80A02D57F5F14DF87C1ADCD92CE23E37`

前两份隔离失败库保留用于故障审计，不参与上述最终指标。
