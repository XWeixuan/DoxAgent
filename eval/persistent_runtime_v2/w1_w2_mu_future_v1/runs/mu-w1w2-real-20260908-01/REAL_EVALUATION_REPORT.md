# MU W1/W2 真实评测与逐轮归因报告

## 结论

本轮使用当前代码、当前 W1/W2 Prompt、百炼 `qwen3.8-flash`（`medium`）和同一份冻结 MU 25-Case 测试集重新执行。25/25 Case 完成，94/98 次模型尝试形成可校验的 strict 输出，4 次格式/结构失败均按 5/10 秒退避策略恢复；未出现鉴权、欠费、端点或 Runtime 中断问题。技术链路通过。

业务质量仍应判为 **PARTIAL**，不能据此批准生产交易：

- W1 NEW/OLD verdict 从旧运行的 96% 提升为 **100%（25/25）**，024 的同日 provisional 去重问题已修复。
- W1-R1 required recall 从 85.29% 提升为 **94.12%**，但 allowed-candidate precision 从 51.61% 降为 **46.67%**；召回更完整，但候选明显变宽。
- W2-R2 已从旧运行的 0 次实际进入，变为对全部 6 个非空 R1 Candidate Case **6/6 进入**；其中 5 个最终裁决符合 Gold。
- W2 最终 Policy candidate 为 **100% precision / 100% recall**；真正 activation 仍为 **80% precision / 100% recall**，唯一 false activation 仍是 022，且会直接产生错误 Trade。
- 冻结 Gold 的 Router 口径已落后于 2026-09-06 后的 W3/Router 契约。直接读取 `metrics.json` 会得到 13/25；按当前 `ADD_TO_DELTA` 契约重算为 **23/25（92%）**，两个语义错例为 022、023，不存在独立 Router 实现错误。
- 隐式缓存调优在完整真实运行中得到验证：总 cache share 从旧运行的 2.456% 提升为 **76.884%**；W1-R1 / W2-R1 的稳定前缀各只有一个 fingerprint，cache share 分别为 90.82% / 77.66%。

## 评测边界与输入完整性

- Run ID：`mu-w1w2-real-20260908-01`
- Evaluation profile：`w1_w2_round_isolated`
- Model / provider / effort：`qwen3.8-flash` / Bailian / `medium`
- Transport：Responses compatible endpoint，strict JSON schema，本地 Pydantic fail-closed validation
- 并发：单 ticker 5 workers；观测到的最大 Case 重叠数为 5
- Session Cache：关闭；使用大段固定前缀的 implicit cache
- 消息、Gold、manifest、Event Library SQLite、Known Event Index、PolicySet 均由 runner 在调用前校验；消息与 Gold SHA-256 分别为 `98efd72f...15bec`、`17e81307...8c72`。
- 冻结 manifest 仍固定测试集创建时的旧 Prompt 哈希，因此原始 `validate_dataset.py` 会在 Prompt 项失败。本轮显式使用 runner 支持的 `--prompt-root prompts/persistent_runtime_v2`，跳过的仅是旧 Prompt pin；实际使用的六份当前 Prompt 哈希已写入 `run_manifest.json`，其余冻结输入继续严格校验。
- PolicySet 本身的 publication state 为 `PARTIAL`；本报告评估 W1/W2 在该固定输入上的行为，不把技术成功等同于 D3 或生产发布批准。

## 分节点指标

| 节点/轮次 | 主指标 | 本轮结果 | 旧运行 | 解释 |
|---|---:|---:|---:|---|
| W1-R1 | allowed-candidate precision / required recall | 46.67% / 94.12% | 51.61% / 85.29% | 35 个预测在 allowed、40 个超出 allowed；32/34 required 命中；exact 6/25 |
| W1-R2 | NEW precision / recall | 100% / 100% | 92.86% / 100% | 13 TP、0 FP、0 FN |
| W1-R2 | OLD precision / recall | 100% / 100% | 100% / 91.67% | 12 TP、0 FP、0 FN |
| W1-R2 | verdict / confidence accuracy | 100% / 96% | 96% / 96% | 仅 023 confidence 分歧 |
| W1-R2 | reference allowed precision / required recall | 96.43% / 80.65% | 77.42% / 70.97% | 27/28 预测在 allowed；25/31 required 命中；exact 19/25 |
| W1-R3 | 当前契约 invocation precision / recall | 92.31% / 100% | 不可直接比较 | 应执行 12，实际 13；额外 022 来自 W2 FP |
| W2-R1 | allowed-candidate precision / required recall | 87.50% / 83.33% | 100% / 83.33% | 8 个预测中 7 个在 required/near-miss 集合；5/6 required 被召回 |
| W2-R2 | non-empty R1 的进入率 | 6/6（100%） | 0/3 | 7 次尝试、6 次成功裁决；022 重试一次 |
| W2-R2 | final exact | 5/6（83.33%） | 强制对照 2/3 | 唯一错例 022 |
| W2 final | Policy candidate precision / recall | 100% / 100% | 100% / 100% | 5 TP、0 FP、0 FN |
| W2 final | true activation precision / recall | 80% / 100% | 80% / 100% | 4 TP、1 FP、0 FN；FP 为 022 |
| Router | 当前契约 exact | 23/25（92%） | 22/25（88%） | 错误均由 022/023 上游结果决定 |

### 集合指标口径修正

当前 `metrics.json` 的通用集合函数把落入 `may_include` / `near_miss_policy_ids` 的预测也累计为 TP，同时只用 must-include FN 构造 recall 分母。这两个分母不是同一 truth set，不能作为标准 precision/recall 对。本报告对 W1-R1、W1 reference、W2-R1 统一使用：precision 衡量预测是否落在 required+allowed 集合，recall 只衡量 must-include required 是否取回。

因此 raw metrics 中 W1-R1 的 94.59% 修正为 32/34 = 94.12%，W1 reference 的 81.82% 修正为 25/31 = 80.65%。W2-R1 原始文件显示 87.5% / 87.5%，报告修正为：

- allowed-candidate precision = 7 / 8 = 87.50%；
- required recall = 5 / 6 = 83.33%。

若把 near miss 也一律当错，strict precision 为 5/8 = 62.50%；但这会错误惩罚 R1 将真实边界候选交给 R2 的设计目标。Case exact 为 23/25，失败分别是 017 的 required FN 和 019 的额外 Candidate。

## 每 Case 的召回数量

| 位置 | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 平均 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| W1-R1 Event candidates | 3 | 0 | 6 | 7 | 3 | 6 | — | — | — | — | 3.00 |
| W1-final references | 9 | 7 | 6 | 3 | — | — | — | — | — | — | 1.12 |
| W1-R3 candidates（13 次） | — | — | — | 1 | 1 | 2 | 2 | 2 | 4 | 1 | 6.46 |
| W2-R1 Policy candidates | 19 | 4 | 2 | — | — | — | — | — | — | — | 0.32 |
| W2-final Policies | 21 | 3 | 1 | — | — | — | — | — | — | — | 0.20 |

W1-R1 有 6 条达到五候选上限，比旧运行的 4 条更多。W2 则保持稀疏：76% 的消息没有 R1 Candidate，最终 84% 没有 Policy。

## 逐轮归因

### W1-R1：召回改善，代价是更宽的上下文

2 个 strict FN 均为 E47：

- 013 的核心是 Orion Cloud 对 Micron 245TB QLC SSD 完成 qualification 并接收首批商业货；E47 是宽泛 AI/NAND 背景，不是决定 novelty 的事实。
- 021 已取回真正决定 512GB→640GB 变化的 E171；未取回 E47 不影响两条 Policy 或 NEW 判断。

两处 FN 都没有传导为 R2 novelty 或 W2 activation FN。相反，40 个 strict FP 分布在 19 条 Case；NEW 组占 25 个、OLD 组占 15 个。主要模式是：

1. 对 NEW 消息召回同产品/同产业但不同 occurrence 的历史背景；014、022 均占满 5 个候选。
2. 对 OLD 聚合或回顾消息召回多个同主题 sibling Event；例如 002、004、005。
3. 对无必要历史参照的 NEW 进展仍提供上下文候选；015、020、025 的 Gold allowed 为空，但各返回 2 个。

这说明 R1 当前“适度偏 recall”的指示已经成功消除关键漏召回，却仍未充分约束同主题、不同 occurrence 的候选。由于 R2 判定 25/25 正确，本轮未观察到 precision 偏低造成语义错误；实际代价是 R2 payload 变宽、详情读取和推理成本增加。

### W1-R2：verdict 全对，provisional 修复有效

- 024：R1 取回 E186/E187，R2 正确理解这些 provisional facts 来自当前消息之前，输出 OLD/normal。旧运行的唯一 novelty 错误已消失，直接验证了新 Prompt 中“由其他消息暂时记录、已属于当前已知现实”的身份说明。
- 023：仍输出 OLD/normal，Gold 为 OLD/low。模型把 “broad commercial delivery” 视为 E43 已记录的 mass production/commercial shipments 的状态重述；Gold 则保留“交付范围扩大”的另一种合理解释。这是 confidence calibration 分歧，不是 verdict 错误。

Reference exact 为 19/25。6 个 FN Case 为 004、011、012、013、021、022：

- 004 用 E27/E56/E28 覆盖供需/价格背景，漏掉 Gold 要求的 E23；E28 是未列入 allowed 的唯一 reference FP，但属于可解释的替代背景。
- 011 的 E8 已足以证明旧的 Clay 开发阶段，遗漏 E7 不改变“permit + groundbreaking 是新阶段”。
- 012 的 E42 已直接表达 80% yield rumor，足以识别“正式确认”这一新事实；E43 不是必要证据。
- 013/021 漏掉的 E47 是宽泛背景；022 对新监管范围输出空 reference，虽不满足 Gold 的 E117 要求，但不影响 NEW。

因此 reference required recall 80.65% 不应等同于 novelty recall；这 6 个 FN 均未造成 verdict 错误，其中多项反映 Gold 对“最小决定性 reference”和“可替代背景”的边界仍偏严。

### W1-R3：调用口径已变化，抽取仍偏宽

冻结 Gold 创建时，普通 `NEW + no Policy` 会进入 W3，因此只把 018、021 两个 TRADE Case 标为 W1-R3 expected。当前 Router 已按新 W3 方案改为：`NEW/normal + no Policy → ADD_TO_DELTA + EMIT_DELTA`，由 W1-R3 直接编译事实。因而原始 metrics 的 expected 2 / actual 13 不能用于发布判断。

按当前契约和 Gold 上游语义重算：应执行 12 次、实际 13 次，precision 92.31%、recall 100%。额外的 022 是 W2 false activation 将本应进入 W3 的 Case 路由为 TRADE 后产生的下游调用；Router/R3 本身按输入确定性执行。

只有 018、021 具有冻结语义 Gold：

- 018：4/4 required facts 均覆盖，无投资解读或公司背景重复；但输出 8 条，超过 Gold 的 3–6 范围。多抽取了其他区域仍运行、维修完成周、非质量问题、非普通排程等辅助事实。核心 recall 完整，precision/压缩度不足。
- 021：6/6 required facts 均覆盖，7 条落在 5–9 范围，无 forbidden content；但把 wafer-start allocation 与 advanced-packaging allocation 合并为一条，属于轻微 atomicity 缺陷。

其余 11 次是当前路由新增的合理调用，但旧 Gold 没有 R3 required/forbidden truth set，不能伪造 formal precision/recall。逐条人工复核显示存在系统性过抽取：拒绝评论、未披露条款、未改变既有范围、后续计划和聚合稿中低相关公司的事实也常被编译。010、015、016、014、025 尤为明显。应将“只抽新增核心事实”进一步落实为可操作的排除规则，并为当前路由重新补齐 R3 Gold 后再给出正式指标。

### W2-R1：Candidate recall 更符合两阶段职责

本轮 6 条消息召回 8 个 Candidate，全部进入 R2：

- 018：召回目标中断 Policy，正确。
- 019：召回目标中断 Policy，同时多召回一个关键投入供应中断 Policy；后者把内部冷却水回路故障误当成可能的外部供应链投入约束，属于 R1 的额外 Candidate，但 R2 正确排除。
- 016：召回 CXL pooling Policy。消息确有 production deployment 与 qualified supplier，属于合理 near miss；R2 因缺少净容量和采购增长证据正确排除。
- 021：两个目标 Policy 均召回，正确。
- 022：目标监管 Policy 召回，正确。
- 023：召回 competitor HBM4 supply/market relaxation Policy。消息存在 commercial-delivery 事实，属于合理 near miss；R2 因缺少多客户重复交付及交期/价格下降证据正确排除。

唯一 required FN 为 017：Gold 要求把 repeat production deployment Policy 送入后续判断，但 R1 将“同一客户、同一 qualification program 的第二批”直接视为不具触发可能。最终 Gold 本来也是 NO HIT，因此未造成 activation FN；它暴露的是 R1 对边界 near miss 的 recall 不足。

### W2-R2：闭环恢复，022 的相对基线 FP 仍未解决

新编排不再让 R1 直接输出 HIT/NO HIT。所有非空 Candidate 均读取完整 Policy/Calibration 并进入 R2：6 个 Case、7 次尝试，进入率 100%。018、019、016、021、023 均与最终 Gold 一致；019 的额外 Candidate、016/023 的 near miss 均被正确过滤。

022 仍错误 normal hit `pol_e28a1b1e8eedc9bf53f5/C1`。模型把 Annex III 中“早期公开摘要未复述的项目表述”推断成此前可服务范围之外的新限制，并以取消 Micron pending releases 作为后果证据。但 Policy criterion 要求相对“此前可服务”的范围扩张；消息故意没有给出旧/新范围 crosswalk。当前 W2-R2 Prompt 已明确“以前没提到不能推断以前不存在”，模型仍越过该规则，因此不是 R1 缺详情，而是相对基线证据约束未被稳定执行。

022 会从应有的 W3/low 变成 TRADE/normal，是本轮唯一直接影响交易 precision 的 P0 问题。

### Router：按当前契约重算为 23/25

旧 Gold 对 10 条普通 NEW/no-policy Case 期待 W3；当前契约期待 ADD_TO_DELTA。它们的模型 W1/W2 结果正确，Router 输出也与当前 16 行矩阵一致，不能计为 10 个 Router 错误。

当前契约下只剩：

- 022：W2 normal false activation，Gold 应为 W3，实际 TRADE，并额外触发 W1-R3。
- 023：W1 应为 OLD/low，实际 OLD/normal，Gold 应为 W3，实际 ARCHIVE。

024 已由旧运行的错误 W3 修复为 ARCHIVE。没有发现独立 Router 分支、side-effect 或顺序错误。

## 技术稳定性、时效与缓存

### 重试与 strict validation

- 总尝试 98，成功 strict outputs 94，失败后恢复 4。
- 019 W1-R2 两次返回 4 个 `reference_ids`，违反最大 3 条限制，第三次成功。
- 022 W2-R2 首次返回无效 JSON，第二次成功。
- 023 W1-R2 首次返回 OLD 但没有 reference，违反本地语义合同，第二次成功。
- 所有失败均被本地 validation 拦截，没有污染最终 Case；没有超出有限重试预算。

这再次证明 provider 接受 strict schema 不等于绝不会返回结构违规结果，本地 fail-closed validation 与延迟重试必须保留。

### 四层时效

| 层级 | n | 总耗时 | mean | median | p90 | min–max |
|---|---:|---:|---:|---:|---:|---:|
| Case 整体（含实际 R3） | 25 | 1378.641s | 55.146s | 59.267s | 70.050s | 24.414–146.827s |
| W1 整体（R1→R2，不含 R3） | 25 | 857.886s | 34.315s | 33.917s | 48.360s | 17.098–72.030s |
| W2 整体（实际 R1→R2） | 25 | 950.713s | 38.029s | 33.335s | 52.731s | 19.293–111.043s |

完整墙钟为 341.767s（约 5 分 42 秒），Case 串行耗时总和为 1378.641s，观测加速约 4.03x；最大重叠数为 5。Case 耗时不能用 W1+W2 相加，因为两条 lane 在 Case 内并行。

### 按轮次时效与 Token

| 轮次 | 尝试/成功 | mean latency | median | p90 | input | cached | cache share | output | reasoning |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| W1-R1 | 25/25 | 17.302s | 15.110s | 27.899s | 339,363 | 308,224 | 90.82% | 34,345 | 33,858 |
| W1-R2 | 28/25 | 14.460s | 13.855s | 24.498s | 93,561 | 40,960 | 43.78% | 27,878 | 22,032 |
| W1-R3 | 13/13 | 23.121s | 20.990s | 35.692s | 32,766 | 12,288 | 37.50% | 25,209 | 19,484 |
| W2-R1 | 25/25 | 31.085s | 30.350s | 46.515s | 166,141 | 129,024 | 77.66% | 69,514 | 69,137 |
| W2-R2 | 7/6 | 24.085s | 18.127s | 53.682s | 19,619 | 10,368 | 52.85% | 13,712 | 8,163 |
| 合计 | 98/94 | — | — | — | 651,450 | 500,864 | 76.884% | 170,658 | 152,674 |

失败但 provider 已返回 usage 的尝试也计入真实消耗；没有 usage 的网络失败不会被虚构为 token。`cached_input_tokens` 是 provider 返回值，不是本地估算。

W1-R1 与 W2-R1 各只有 1 个 `prefix_fingerprint`，证明同一 ticker/version 的稳定前缀字节一致；R2 因 Candidate Detail 组合不同分别有 22 和 6 个 fingerprint，但仍命中共同固定前段。相比旧运行：

- cache share：2.456% → 76.884%，提高 74.428 个百分点；
- 总 input tokens：957,492 → 651,450，下降 31.96%；
- 调用数从 81 增至 98，主要因为 W2-R2 闭环恢复及当前路由下 W1-R3 调用增加，因此 output/reasoning token 与总 latency 上升不能简单归因于缓存退化。

## 优先修复建议

1. **P0 — W2 相对基线硬约束**：针对“新增范围、首次、扩大、下降、超过此前计划”等 Policy，在 R2 明确要求消息或 Policy reference state 提供可核对的 before/after crosswalk；“旧摘要未出现”必须被列为禁止性证据。022 应作为固定反例。仅重复现有自然语言规则已被本轮证明不够稳定。
2. **P0 — 更新评测契约而非修改历史 Gold**：为当前 Router 另建版本化 expected route/R3 truth，保留旧 Gold 以支持历史可比；普通 NEW/no-policy 应标为 ADD_TO_DELTA/W1-R3，W3 只保留 exception-plane Case。同步修正 W2-R1 聚合，分开 allowed precision 与 required recall。
3. **P1 — 收紧 W1-R1 occurrence identity**：优先处理 Gold 无候选却仍召回宽泛背景的 NEW Case，以及达到 5 条上限的 014/022；不应以牺牲本轮已达到的 100% novelty verdict 为代价。
4. **P1 — W1-R3 核心事实压缩**：显式排除拒绝评论、未披露条款、无变化陈述、普通后续计划及聚合稿的低相关旁支；补齐当前 12 个正常执行 Case 的 R3 semantic Gold 后再量化 precision/recall。
5. **P2 — W1 confidence calibration**：加入与 023 等价的“既可视为既有状态重述、也可能是范围扩大”的 low 正例，避免低置信边界长期被 normal 吞并。

## 发布判断

真实技术验收通过；缓存验收通过；W1 novelty 质量通过。W2 两阶段闭环技术上已落地并显著优于旧运行，但业务验收仍为 **PARTIAL**：022 的 false activation 会生成错误 Trade，属于生产发布前必须修复的 precision 缺陷；023 会把应交 W3 的边界 Case 归档。修复后至少重跑 022、023、017、019，以及当前路由下新增 R3 Gold 的完整 25 条回归。
