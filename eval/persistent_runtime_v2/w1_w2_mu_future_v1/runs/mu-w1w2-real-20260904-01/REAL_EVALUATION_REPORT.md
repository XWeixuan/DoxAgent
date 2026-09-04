# MU W1/W2 真实评测报告

## 结论

本轮 25/25 Case 均通过真实百炼传输、严格结构化输出和 Runtime 本地校验，技术链路可用；但业务质量尚不应判为无条件发布通过。W1 新旧判断准确率为 96%，W2 最终 activation 召回率为 100%、精度为 80%，最终路由准确率为 88%（22/25）。主要缺陷集中在同日 provisional 的“先前已知”身份表达、W2 对相对基线证据的过度推断，以及 R1 `low` 升级口径与当前 Gold 的不一致。

本次采用 `w1_w2_round_isolated` profile：每条消息使用新 activation ledger；013→024、018→019 注入同日 provisional facts。019 不按完整顺序 Runtime 中 018 已消费同一 Policy revision 后的行为计分。

## 分节点指标

| 节点/轮次 | 主指标 | 结果 | 解释 |
|---|---:|---:|---|
| W1 R1 | Event ID micro precision / recall | 51.61% / 86.49% | 32 TP、30 FP、5 FN；严格集合 exact 9/25 |
| W1 R2 | NEW precision / recall | 92.86% / 100% | 13 TP、1 FP、0 FN |
| W1 R2 | OLD precision / recall | 100% / 91.67% | 11 TP、0 FP、1 FN |
| W1 R2 | verdict / confidence accuracy | 96% / 96% | verdict 错例 024；confidence 错例 023 |
| W1 R2 | reference precision / recall | 77.42% / 72.73% | exact 15/25；不等于 novelty verdict 质量 |
| W1 R3 | invocation precision / recall | 66.67% / 100% | 应执行 2 次、实际 3 次；额外一次由 W2 错路由引起 |
| W2 R1 | candidate Policy precision / recall | 100% / 83.33% | 5 TP、0 FP、1 FN；confidence 22/25 |
| W2 R2 | expected / actual invocation | 3 / 0 | 三条 Gold-low 均被 R1 判为 normal |
| W2 final | candidate retention precision / recall | 100% / 100% | 包含 low unresolved candidate，不等于 activation |
| W2 final | true activation precision / recall | 80% / 100% | 4 TP、1 FP、0 FN；FP 为 022 |
| Router | exact | 22/25（88%） | Router 本身按上游结果确定性工作 |

## 逐轮归因

### W1 R1：原始 precision 偏低，但 Gold 候选集合明显过窄

14 条 Case 返回了 Gold 未列出的候选，只有 4 条缺少某个 must-include Event。逐条复核表明，这 5 个 FN 没有造成一次最终 verdict 错误：004 用 E28/E30 替代 E23 覆盖同一供需事实；012 载入 E42 即足以判断“传闻约 80% yield→正式确认”为 NEW；013 的 E47 只是 AI inference/NAND 的宏观背景；021 已载入真正关键的 512GB 基线 E171，未载入的 E15/E47 属宽泛背景。

因此 51.61%/86.49% 是“严格匹配人工穷举集合”的分数，不能直接解释成检索质量只有该水平。真实问题是候选偏宽导致 R2 上下文和 token 成本增加；本轮没有发现由 canonical Event 漏召回直接导致的 novelty 错判。

### W1 R2：唯一 verdict 错误来自 provisional 时间身份

- 024：R1 已正确取回 E186/E187，但 R2 把它们理解成“当前消息自身的 extraction”，错误输出 NEW，导致 ARCHIVE→W3。对照实验仅增加“这些 provisional facts 来自当前消息之前的另一条消息”这一业务语义，结果立即修正为 OLD/normal，并正确引用 E186/E187。根因是 provisional payload 虽被命名为 known reality，却缺少足够显式的先后身份，而不是候选召回失败。
- 023：模型认为官方文本明确是既有 HBM4 commercial delivery 的 status update，输出 OLD/normal；Gold 认为“broad delivery”仍可能代表范围扩大，因此要求 OLD/low。它是 confidence 边界分歧，且直接导致 Gold 的 W3 被实际 ARCHIVE。
- reference 指标低于 verdict 指标，主要来自 NEW 消息附带过多历史背景引用，以及 Gold 对替代性 Event 覆盖不够。019 原计分还存在“最多 3 个 reference、却要求 4 个”的不可能约束，本报告已按 max_items 修正为前三个 required、全部四个 allowed。

### W1 R3：内容召回良好，调用精度受 W2 牵连

018 与 021 两个应执行 Case 均处于 Gold 数量范围，覆盖 10/10 个 required facts，未输出禁止的投资解读或重复公司背景。021 将“wafer-start allocation 增加”和“advanced-packaging allocation 增加”合并在同一 candidate，信息未漏，但 10 个 required facts 中只有 9 个保持严格原子拆分。

022 的第三次 R3 提取内容本身基本忠实，但该 Case 按 Gold 应由 W2 low 路由 W3，不应走 TRADE/EMIT_DELTA；因此这是 W2 activation FP 的下游结果，不是 R3 自主召回错误。

### W2 R1/R2：升级召回失败与测试 Gold 口径冲突并存

- 017：R1 直接读出“单一客户、同一 qualification program”，按 Prompt 中“明确缺事实应 normal no-hit”的规则拒绝 Policy。强制 R2 后结果与 Gold final 完全一致。这里最终判断正确，失败仅是 Gold 要求必须先 low 再 R2。
- 021：消息自身已经写明 512GB→640GB、增量绑定订单和资源分配，R1 可直接 normal hit。强制 R2 后仍与主运行和 Gold final 完全一致。这里同样是过程覆盖 Gold 过度规定，而非最终判定错误。
- 022：R1 normal hit；强制提供完整 Policy/Calibration 后仍 normal hit。模型把“未出现在此前公开摘要中的 Annex III 类别”推断成“此前 Micron 可服务、现在新增受限”，但消息没有完成该 crosswalk。Policy criterion 明确要求相对“此前可服务”的新增范围，因此这是实际的 W2 逻辑 FP；R2 不能自动修复。

当前 Gold 所设计的 3 个 R2 Case 都没有在真实运行中触发，说明这批测试并未成功构造出模型认为“Projection 确实不足以裁决”的输入。017/021 应修改 R1 Gold 为 normal，或另造真正需要 Calibration 才能解决的消息；022 应继续作为 low/false-activation 边界 Case。

### Router：3 个错误全部可追溯到上游

- 024：W1 provisional 去重错误，Gold ARCHIVE，实际 W3。
- 022：W2 false activation，Gold W3，实际 TRADE。
- 023：W1 confidence 分歧，Gold W3，实际 ARCHIVE。

不存在独立 Router 规则错误。

## 技术稳定性与成本

- 成功结构化输出：78 次；总尝试：81 次。
- 自动重试：3 次。005 的 W1-R2 两次违反 `reference_ids <= 3` 后第三次通过；021 的 W2-R1 一次 provider timeout 后通过。
- 本节时效性采用每个 turn 的 `created_at - latency_ms` 作为请求开始、`created_at` 作为请求结束；节点窗口包含该节点的轮次串行关系、重试等待和 5/10 秒退避，但不把并行的另一节点耗时重复计入。Case 窗口从 Runtime Case 开始到最终落盘完成。
- 五并发实际主运行窗口：2026-09-04 01:53:33 至 01:57:20（约 3 分 46 秒）；25 条 Case 的串行耗时总和为 924.769s，墙钟为 226.457s，约 4.08x 加速，观测到的 Case 最大重叠数为 5。因此本轮确实是并行执行：每个 Case 内 W1/W2 也由 Runtime 并行启动，Case 之间再由 5-worker pool 并发。

### 四层时效性

| 层级 | n | 总耗时 | mean | median | p90 | min–max |
|---|---:|---:|---:|---:|---:|---:|
| Case 整体（含 W1/W2 与实际 R3） | 25 | 924.769s | 36.991s | 31.711s | 47.253s | 16.064–105.657s |
| W1 整体（仅 R1→R2，不含 R3） | 25 | 766.695s | 30.668s | 29.541s | 38.494s | 16.003–65.482s |
| W2 整体（实际 R1→R2） | 25 | 602.550s | 24.102s | 19.031s | 34.634s | 9.149–89.152s |

Case 整体不是 W1 与 W2 耗时相加：W1/W2 在 Case 内并行，所以 Case 通常接近两者较慢者；实际 R3 只在 3 条 Case 上追加。

### 按轮次时效性

| 轮次 | 实际调用 | mean | median | p90 | min–max |
|---|---:|---:|---:|---:|---:|
| W1-R1 | 25 | 18.072s | 15.884s | 24.233s | 7.085–35.562s |
| W1-R2 | 25（含 2 次重试） | 12.578s | 11.172s | 17.370s | 5.631–51.543s |
| W1-R3 | 3 | 20.932s | 16.438s | 16.438s | 13.609–32.748s |
| W2-R1 | 25（含 1 次重试） | 24.102s | 19.031s | 34.634s | 9.149–89.152s |
| W2-R2 | 0（Gold 预期 3） | N/A | N/A | N/A | N/A |

W1-R2 的 p90 受带重试的长尾影响；W2-R1 的最大 89.152s 对应一次 timeout 后重试。W2-R2 没有实际调用，不能用 0s 表示。

### Token 与缓存命中

下表 token 是 provider 返回 usage 的成功响应累计；失败/timeout 的重试没有 usage，不虚构计费量。`cached_input_tokens` 是 provider 单独返回的缓存输入 token，`cache share` = cached/input；本轮只有 2 个成功调用返回了非零缓存 token。

| 层级 | 尝试/成功 | input | cached input | cache share | output | reasoning |
|---|---:|---:|---:|---:|---:|---:|
| Case 整体 | 81/78 | 957,492 | 23,519 | 2.456% | 109,553 | 101,306 |
| W1 整体（仅 R1+R2） | 52/50 | 728,730 | 16,204 | 2.224% | 52,654 | 49,446 |
| W2 整体（R1+R2） | 26/25 | 179,616 | 7,315 | 4.073% | 50,120 | 46,450 |

| 轮次 | 尝试/成功 | input | cached input | cache share | output | reasoning |
|---|---:|---:|---:|---:|---:|---:|
| W1-R1 | 25/25 | 337,887 | 0 | 0% | 31,768 | 31,333 |
| W1-R2 | 27/25 | 390,843 | 16,204 | 4.146% | 20,886 | 18,113 |
| W1-R3 | 3/3 | 49,146 | 0 | 0% | 6,779 | 5,410 |
| W2-R1 | 26/25 | 179,616 | 7,315 | 4.073% | 50,120 | 46,450 |
| W2-R2 | 0/0 | 0 | 0 | N/A | 0 | 0 |

因此可以单独统计缓存命中，但当前命中量很低：全流程仅 2.456% 的输入 token 被 provider 标记为 cached；W1-R1、W1-R3 没有缓存命中，W1-R2 和 W2-R1 各有 1 次调用命中。缓存 token 不是本地推算，是否计费仍以百炼账单口径为准。

- 四次归因对照调用另消耗 40,056 input、3,490 output token。

严格 schema 并未阻止 005 首两次返回超过 Pydantic max-items 的数组，说明百炼 strict mode 仍必须保留本地 fail-closed validation 与延迟重试；当前实现正确完成了恢复。

## 建议优先级

1. **P0 — W1 provisional payload**：增加明确的业务语义，如 `known_before_current_message: true`，或等价的“来自更早消息”标记；不要重新注入版本、协议或审计字段。
2. **P0 — W2 相对基线证据**：对“扩大、下降、首次、超过当前计划”等相对变化，要求消息提供可验证的前后 crosswalk；缺失时不得从“此前摘要未列出”等弱证据推导，应该 low。该规则应同时反馈给 O3 Calibration 编译。
3. **P1 — R2 测试设计**：将 017/021 的 R1 Gold 与当前 Prompt 的 normal 规则对齐，并新增真正无法仅靠 compact Projection 决断的 Case。
4. **P1 — W1 R1 候选成本**：继续压低仅共享宽泛主题的候选，但先扩充 Gold 的 `may_include`，避免把合理替代 Event 当作 FP。
5. **P2 — W1 low 与 R3 atomicity**：增加“范围扩大措辞但无明确新阶段”的 low 校准示例；加强 R3 对两个独立资源分配事实的拆分。

## 发布判断

技术验收通过；业务验收为 **PARTIAL**。W1/W2 对绝大多数真实长文本表现稳定，且没有 Policy 漏激活，但 022 的 false activation 会直接生成错误 Trade，属于发布前应解决的高优先级精度问题；024 与 023 会造成 W3/ARCHIVE 流量偏差。修复后应至少重跑 022、024、023、017、021，并随后复跑完整 25 条确认无回归。
