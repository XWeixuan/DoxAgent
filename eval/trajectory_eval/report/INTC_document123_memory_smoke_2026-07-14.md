# INTC Document1+2+3 ReAct / Workflow Memory Smoke 报告

## 1. 结论

本次只启动了一条 INTC smoke 链，没有重跑或失败重试：

- Document1+2 execution run：`run_aa92b288379c4a24b52354dd15400c1f`
- Document3 execution run：`run_c8a0a14c74ef4ed38697259497839679`
- Document1+2 成功到达 `PromoteExpectationToBeliefState`。
- Document3 成功生成 Known Events 和 Monitoring Config，但在 `GenerateMonitoringPolicy` 阻塞，最终整体 smoke 失败。
- 阻塞原因不是上下文爆量或 Compaction，而是 `MonitoringPolicyDocument` schema 与 Monitoring Policy skill 不一致：10 条 policy rule 都缺少必填 `strategy_note`。
- LangSmith 可见的最大单轮输入为 55,427 tokens，显著低于 115,200 Micro 阈值和 192,000 Full Compaction 阈值；本次可见 trace 中 Micro/Full Compaction 均为 0。
- Fresh Observation 已从旧 smoke 的数十万 token 降到单轮最高约 37.3k tokens，但 `doxa_get_narrative_report` 仍是主要 Fresh 来源：单次 Agent-visible 128,101 chars，相对原始 87,639 chars 为 1.46x。
- ReAct Memory 的显式 obtained 和 passive carryover 在可比较的连续轮次中正确同步：ReviewExpectationConstruction 的 12 个引用 alias 在下一轮分成 7 个 obtained + 5 个 passive，未丢失也未重复。
- Evidence Ref 实际使用不均衡。部分 review/detail 节点能稳定引用，核心 Construction、部分 Detail 和 C1 财务 review 则没有在 action 正文中输出 citation。
- Event Time annotation 共 25 个，全部来自三个 `GenerateExpectationDetails` 输出；Document3 Known Events 虽有 23 个结构化 `event_time` 字段，但没有输出 `occurred_at/published_at` annotation tag。

重要边界：`run_document1_document2_smoke.py` 本次运行在 `mode=clone`，从 `run_10fe3b3140f344dcb91a0069c7ea067f` 复用了稳定 Document1，起点是 `ReviewGlobalResearch`。因此本报告没有把本次运行表述为重新执行了 `BuildGlobalResearch`；Document1 信息源 fan-out 并未在本条 smoke 中重新调用。

## 2. 部署与时间线

| 事件 | 北京时间 | 结果 |
| --- | --- | --- |
| 本地提交并 push | 2026-07-14 | commit `1ae716c` (`fix: bound fresh observations and compaction loops`) |
| 远端 pull | 2026-07-14 | `/root/doxagent` 更新到 `1ae716c` |
| 完整 build | 2026-07-14 | dashboard、runtime-scheduler、revenue-auditor、legacy-debug `monitoring-poller` 全部构建成功 |
| 服务重建与健康检查 | 2026-07-14 | dashboard、runtime-scheduler healthy；`/healthz` 返回 `ok=true` |
| Document1+2 启动 | 20:05:33 | 容器 `doxagent-smoke-intc-document123-20260714-2004` |
| Document1+2 完成 | 20:44:16 | exit 0；3 个 expectation；0 unresolved objection；0 blocking delegation |
| Document3 启动 | 20:46:28 | 容器 `doxagent-smoke-intc-document3-20260714-2047` |
| Document3 完成 | 21:06:42 | exit 1；阻塞在 `GenerateMonitoringPolicy` |

监测使用一次性 cron 定时唤醒，约每 10 分钟读取一次容器状态；没有创建 heartbeat。任务结束后已删除这些定时唤醒。

## 3. 统计口径

- LangSmith MCP 按一个 workflow run、一个节点分批定位请求，未做跨 trace 大 payload 拉取。
- `input_tokens` 使用 LangSmith/provider 的精确 `prompt_tokens`。
- MCP 单页最大 30,000 chars，无法返回某些完整 prompt。节点定位后，仅按精确 request ID 从同一 LangSmith run API 读取单条 payload，并在本地立即聚合；没有把完整 prompt 写入报告。
- Fresh、Workflow Memory、Obtained、Passive 等 token 是按各 JSON 组件字符权重校准到该轮精确 `prompt_tokens` 的估计值。占比适合判断构成，不等同于 provider 的字段级 tokenizer 账单。
- Citation 只扫描 action 自然语言字段和独立 reasoning channel，按当前输入中真实存在的 alias 校验并去重；structured/text 没有重复计数。
- `citation aliases` 表示合法去重 alias 数；`cite tags` 表示显式 `cite:` 标签出现次数。裸 alias 可被 normalizer 接受，但可能与 `author_agent=O1` 等业务字段碰撞，报告单独标记该风险。
- Event Time tag 只统计 `occurred_at:` / `published_at:` annotation，不把 schema 的 `event_time` 字段混入。

## 4. 每节点、每轮输入

### 4.1 Document1+2

`Obt/Pass` 格式为“block 数 / 估计 tokens”；`Citation A/R` 为 action/reasoning 的合法去重 alias 数。

| Node / loop | input tokens | Fresh tokens / 占比 | Workflow tokens | Obt / Pass | Citation A/R | Event tags | 状态 |
| --- | ---: | ---: | ---: | --- | ---: | ---: | --- |
| O1.GenerateExpectationConstruction.LOOP1 | 14,945 | 0 / 0% | 8,481 | 0/0 / 0/0 | 0/0 | 0 | success |
| O1.GenerateExpectationConstruction.LOOP2 | 53,853 | 37,289 / 69.2% | 9,136 | 0/0 / 0/0 | 0/0 | 0 | success |
| A1.ReviewExpectationConstruction.LOOP1 | 8,586 | 0 / 0% | 2,133 | 0/0 / 0/0 | 0/0 | 0 | success |
| A1.ReviewExpectationConstruction.LOOP2 | 47,450 | 36,815 / 77.6% | 2,492 | 0/0 / 0/0 | 12/0 | 0 | success |
| A1.ReviewExpectationConstruction.LOOP3 | 24,652 | 8,033 / 32.6% | 2,342 | 7/3,921 / 5/2,687 | 46/4 | 0 | success |
| O1.GenerateExpectationDetails.LOOP1 | 16,555 | 0 / 0% | 9,234 | 0/0 / 0/0 | 0/0 | 0 | success |
| O1.GenerateExpectationDetails.LOOP2 | 16,584 | 0 / 0% | 9,249 | 0/0 / 0/0 | 0/0 | 0 | success |
| O1.GenerateExpectationDetails.LOOP3 | 16,572 | 0 / 0% | 9,245 | 0/0 / 0/0 | 0/0 | 0 | success |
| O1.GenerateExpectationDetails.LOOP4 | 55,341 | 37,171 / 67.2% | 9,952 | 0/0 / 0/0 | 1/1 | 10 | success |
| O1.GenerateExpectationDetails.LOOP5 | 55,409 | 37,174 / 67.1% | 9,958 | 0/0 / 0/0 | 10/6 | 10 | success |
| O1.GenerateExpectationDetails.LOOP6 | 55,427 | 37,177 / 67.1% | 9,952 | 0/0 / 0/0 | 0/7 | 5 | success |
| C1.ReviewExpectationFields.LOOP1 | 13,689 | 0 / 0% | 7,789 | 0/0 / 0/0 | 0/0 | 0 | success |
| A1.ReviewExpectationFields.LOOP1 | 15,128 | 0 / 0% | 7,835 | 0/0 / 0/0 | 0/0 | 0 | success |
| C3.ReviewExpectationFields.LOOP1 | 13,469 | 0 / 0% | 7,837 | 0/0 / 0/0 | 0/0 | 0 | success |
| O4.ReviewExpectationFields.LOOP1 | 22,421 | 0 / 0% | 16,625 | 0/0 / 0/0 | 0/0 | 0 | success |
| A1.ReviewExpectationFields.LOOP2 | 17,414 | 985 / 5.7% | 8,187 | 0/0 / 0/0 | 6/2 | 0 | success |
| C3.ReviewExpectationFields.LOOP2 | 17,642 | 3,011 / 17.1% | 7,985 | 0/0 / 0/0 | 7/14 | 0 | success |
| C1.ReviewExpectationFields.LOOP2 | 24,682 | 7,540 / 30.5% | 9,250 | 0/0 / 0/0 | 0/12 | 0 | success |
| O4.ReviewExpectationFields.LOOP2 | 26,350 | 1,711 / 6.5% | 17,864 | 0/0 / 0/0 | 5/1 | 0 | success |
| A1.ReviewExpectationFields.LOOP3 | 15,752 | 0 / 0% | 7,845 | 0/0 / 2/53 | 1/0 | 0 | success |
| C1.ReviewExpectationFields.LOOP3 | 13,688 | 0 / 0% | 7,788 | 0/0 / 0/0 | 0/0 | 0 | success |
| C1.ReviewExpectationFields.LOOP4 | 20,664 | 4,136 / 20.0% | 8,906 | 0/0 / 0/0 | 0/10 | 0 | success |
| O1.ResolveObjectionsAndDelegations.LOOP1 | 10,252 | 0 / 0% | 3,605 | 0/0 / 0/0 | 0/0 | 0 | success |
| O1.ResolveObjectionsAndDelegations.LOOP2 | 9,445 | 0 / 0% | 2,828 | 0/0 / 0/0 | 0/0 | 0 | success |
| O1.ResolveObjectionsAndDelegations.LOOP3 | 10,647 | 0 / 0% | 4,010 | 0/0 / 0/0 | 0/0 | 0 | success |
| O1.ResolveObjectionsAndDelegations.LOOP4 | 10,815 | 0 / 0% | 4,089 | 0/0 / 0/0 | 0/0 | 0 | success |

Document1+2 共 607,432 input tokens。估计构成为：Fresh 211,054（34.7%）、Workflow Memory 204,617（33.7%）、Obtained 3,943（0.65%）、Passive 2,761（0.45%），其余为 system prompt、ReAct protocol、output contract、tool schema 和 task memory 其他字段。

### 4.2 Document3

| Node / loop | input tokens | Fresh tokens / 占比 | Workflow tokens | Obt / Pass | Citation A/R | Event tags | 状态 |
| --- | ---: | ---: | ---: | --- | ---: | ---: | --- |
| O1.GenerateKnownEvents.LOOP1 | 22,184 | 0 / 0% | 16,705 | 0/0 / 0/0 | 0/0 | 0 | success |
| O2.GenerateMonitoringConfig.LOOP1 | 29,142 | 0 / 0% | 23,347 | 0/0 / 0/0 | 0/0 | 0 | success |
| C1.ReviewMonitoringConfig.LOOP1 | 8,701 | 0 / 0% | 3,898 | 0/0 / 0/0 | 0/0 | 0 | success |
| C3.ReviewMonitoringConfig.LOOP1 | 8,451 | 0 / 0% | 3,919 | 0/0 / 0/0 | 0/0 | 0 | success |
| C3.ReviewMonitoringConfig.LOOP2 | 12,936 | 3,542 / 27.4% | 4,145 | 0/0 / 0/0 | 4/6 | 0 | success |
| C1.ReviewMonitoringConfig.LOOP2 | unavailable | input 34,843 chars | unavailable | unavailable | unavailable | unavailable | `CancelledError`，无 usage |
| C1.ReviewMonitoringConfig.LOOP3 | unavailable | input 34,750 chars | unavailable | unavailable | unavailable | unavailable | LangSmith pending，后续 ingest 429 |
| O4.GenerateMonitoringPolicy | unavailable | unavailable | unavailable | unavailable | unavailable | unavailable | trace 因 LangSmith 429 未写入；workflow schema failed |

Document3 后段出现 LangSmith `Monthly unique traces usage limit exceeded`，两个 multipart ingest 返回 429。`GenerateMonitoringPolicy` 的 LLM input/output 因此不能从 LangSmith 恢复，不能伪造其 token 数据；容器和 workflow error 仍保留了最终 schema 错误。

## 5. Fresh Observation 按 Tool

以下是本条 smoke 实际进入 Agent Context 的 Fresh ToolResult；重复出现表示不同 task 独立调用或暴露。

| Tool | 原始 chars | Agent-visible chars | 倍率 | delivery | 说明 |
| --- | ---: | ---: | ---: | --- | --- |
| `doxa_get_narrative_report` | 87,639 | 128,101 | 1.46x | hybrid_profiled | 5 次暴露；150 blocks 中加载 94，另有 2 个 group catalog |
| `doxa_get_ignored_propositions` | 15,682 | 29,746 | 1.90x | full | 绝对量 29.7k；仍有表示层膨胀 |
| `doxa_query_analysis` | 3,049 | 3,583 | 1.18x | full | 正常 |
| `sec.company_facts_and_filings` | 3,765 | 4,371 | 1.16x | full | 本次返回较小，未触发 SEC 大 payload 策略 |
| `alpha.financial_statements` | 83,570 | 11,866 / 11,857 | 0.14x | hybrid_profiled | 110 blocks 中加载 15，4 个 group catalog；压缩有效 |
| `alpha.company_overview` | 2,140 | 2,492 | 1.16x | full | 正常 |
| `tavily.search` | 3,474–6,220 | 4,156–6,955 | 1.11–1.20x | full | 正常 |
| `twelvedata.daily_ohlcv` | 5,309 | 5,998 | 1.13x | full | 正常 |
| `tavily.extract` | 11,299 | 12,624 | 1.12x | full | Document3 review；正常 |
| `finnhub.company_peers` | 406 | 672–676 | 1.66–1.67x | full | 倍率偏高但绝对量很小 |

`alpha.earnings_events` 和一次 `doxa_get_ignored_propositions` 的原始 output 只有 `[]`（2 chars），Agent-visible 约 166–173 chars；表面倍率很大，但不是上下文风险，应按固定 envelope 开销理解。

Fresh 结论：

1. 旧报告中的 10x–17x 结构表示膨胀已经消失。
2. `alpha.financial_statements` 的专用压缩策略有效。
3. `doxa_get_narrative_report` 仍占单轮 67%–78% 输入，是当前主要 Fresh 压力源；1.46x 虽远好于旧实现，但 128k visible chars 仍偏大。
4. `doxa_get_ignored_propositions` 的 1.90x 是本轮最明显的剩余表示膨胀，建议增加与 DoxAtlas narrative 相同的 source/detail 去重与 group catalog 策略。
5. 所有本次可见请求都低于 Micro/Full 阈值，因此没有再次触发 Fresh 无法被 Compaction 压缩的问题。

## 6. ReAct Memory / Workflow Memory / Evidence Ref 表现

### 6.1 Prompt 路由

抽查真实 system input：

| 节点类型 | `memory.md` | `research_memory.md` | `evidence_ref_usage.md` registry block | `read_observation` |
| --- | --- | --- | --- | --- |
| O1 Construction / Details / Known Events | 有 | 有 | 有 | 有 |
| O2 Monitoring Config | 有 | 有 | 有 | 有 |
| A1/C1/C3/O4 review | 有 | 无 | 有 | 有 |
| O1 Resolve | 有 | 无 | 有 | 有 |

`evidence_ref_usage.md` 以 registry id `workflow.observation-annotations` 注入；没有发现 Assembler 重新硬编码 citation 规则。Review 节点不加载 research memory，研究/生成节点同时加载两层 memory，路由符合方案。

### 6.2 Obtained 与 Passive

- 全部可见输入累计 obtained 7 blocks / 约 3,943 tokens，passive 7 blocks / 约 2,761 tokens。
- 最清晰的连续轮次是 A1 Construction Review：LOOP2 action 引用 12 个有效 alias；LOOP3 输入恰好为 7 obtained + 5 passive。
- A1 Field Review 的下一轮输入有 2 个 passive blocks；均为完整 block，没有半截截断证据。
- 未发现 obtained 被 Passive Budget 截断；obtained 仍独立于 passive。
- 大部分 task 在拿到 Fresh 后直接 final，因此没有“下一轮”可观察 carryover；不能把这些 final task 记成 memory 丢失。
- 所有可见 action 都没有调用 `read_observation`。这说明 group catalog 能导航，但本轮 Agent 没有主动回读；对于只加载 15/110 blocks 的 Alpha 报表，review 输出也没有 citation，存在“压缩有效但未深挖目录”的质量风险。

### 6.3 Citation 与 Event Time

- Action 自然语言字段：92 个合法去重 alias，88 次显式 `cite:` tag。
- Provider reasoning channel：63 个合法去重 alias，11 次显式 `cite:` tag；大量 reasoning 仍使用裸 alias。
- `GenerateExpectationConstruction` 使用 37k Fresh tokens 后，最终 action 没有 citation。
- 三个 Detail task 中只有一个在 action 中稳定输出 10 个引用；另一个仅出现裸 `O1`，可能来自 `author_agent=O1`，属于 normalizer 的字段碰撞风险。
- C3/O4/A1 review 的 citation 表现较好；C1 财务 review 在 reasoning 中识别到 alias，但 action 正文没有 citation。
- Event Time annotation 共 25 个，只出现在 Details。Known Events 输出含 23 个结构化 `event_time` 字段，但 `occurred_at/published_at` annotation 为 0，说明 schema 时间字段能力正常，通用 annotation 习惯仍不稳定。

裸 `O1` 风险：当前 normalizer 按“alias 在 Task 中真实存在”即可接受裸 alias；如果自然语言递归扫描到 `author_agent: O1`，它可能被误判为 Citation/Passive 候选。建议 natural-language extractor 排除身份字段，或要求裸 alias 只在真正正文标量中识别。

### 6.4 Compaction

- LangSmith 可见 Full Compaction trace：0。
- 最大输入 55,427 tokens；没有达到 115,200 Micro 或 192,000 Full 阈值。
- 没有连续 Compaction，也没有验证到“Full 成功后下一轮移除 Fresh”的运行分支，因为本次没有触发 Full。
- `GenerateMonitoringPolicy` trace 缺失，因此不能从 LangSmith 绝对证明该请求的 compaction 数；但其上游没有 Fresh，Workflow Memory 规模与其他 Document3 生成节点相当，没有达到阈值的迹象。

## 7. Document3 阻塞根因

直接错误：`MonitoringPolicyDocument` 的 4 条 `direct_trade_rules` 和 6 条 `push_to_agent_rules` 全部缺少 `strategy_note`，产生 10 个 Pydantic `Field required`。

根因链路：

1. `MonitoringPolicyRule.strategy_note` 在 `src/doxagent/models/documents.py` 中是必填 `NonEmptyStr`。
2. ReAct runtime 的 `MonitoringPolicyDocument` output contract 示例也包含 `strategy_note`。
3. 但 `prompts/internal_task_skills/monitoring-policy.md` 的 “Every rule must include” 清单列出 `reasoning`，没有列出 `strategy_note`。
4. 模型按更直接的 skill 字段清单生成了完整规则，但漏掉 `strategy_note`。
5. ReAct runtime 在 final payload 阶段先执行严格 schema validation；失败后 workflow 立即阻塞。
6. Orchestrator 的 `_normalize_policy_rules()` 本可通过 `_policy_strategy_note_text()` 补默认值，但该 normalizer 位于 ReAct schema validation 之后，永远没有机会处理这次输出。

建议修复：

- 在 Monitoring Policy skill 的必填字段和极简 example 中补上 `strategy_note`。
- 在 ReAct final schema validation 前，对 MonitoringPolicy rule 做与 orchestrator 相同的确定性兼容归一化；若模型缺失 `strategy_note`，从 `reasoning/rationale/note` 复制，均不存在时填安全的 runtime routing note。这样该字段仍可在最终 document 中保持非空，同时把模型漏字段变为非阻塞。
- 增加真实形态回归测试：10 条 rules 全部缺 `strategy_note` 时，runtime 应归一化后通过；不能等到 orchestrator 之后才兜底。
- LangSmith 429 是独立可观测性问题，应提升配额或降低非必要 trace 数；它不是本次 schema failure 的业务根因。

## 8. 验收判断

| 项目 | 判断 |
| --- | --- |
| 远端 pull 与完整 build | 通过 |
| 只运行一次整体 smoke | 通过；Document1+2 一次，随后接续同链 Document3 一次 |
| 10 分钟定时唤醒、无 heartbeat | 通过 |
| Document1+2 | 通过 |
| Document3 | 失败；阻塞于 Monitoring Policy schema |
| Fresh Observation 膨胀抑制 | 基本通过；主要工具从旧 10x–17x 降到 0.14x–1.90x，narrative/ignored propositions 仍有优化空间 |
| ReAct obtained/passive 同步 | 通过可观察样本 |
| Workflow Memory Prompt 路由 | 通过抽查 |
| Evidence Ref | 部分通过；review 较好，核心 Construction/部分 Detail/C1 action 不稳定 |
| Event Time annotation | 部分通过；Details 有 25 个，Document3 annotation 为 0 |
| Compaction 修复实测 | 本次未触发，无法验收 Full 分支；确认没有误触发或连环触发 |
| LangSmith 全节点完整性 | 部分失败；后段受月度 trace 配额 429 影响，Policy trace 缺失 |

