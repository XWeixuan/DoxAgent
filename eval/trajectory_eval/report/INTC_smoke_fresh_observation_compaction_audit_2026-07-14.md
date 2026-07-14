# INTC 多轮 Smoke：Fresh Observation / Compaction / Obtained Observation 数据审计

## 1. 结论

本次按“一个 run、一个节点”逐批读取 LangSmith，不做跨 trace 大 payload 查询。审计覆盖 6 个 run、9 个 run-node 组合、151 次 LLM 请求：115 次普通 Agent 请求和 36 次 Full Compaction 请求。

核心结论如下：

1. **上下文爆量的主因是 Fresh Observation，不是 Prompt/Skill，也不是 Obtained Observation。** 74 次有 provider token usage 的普通请求累计 8,301,760 input tokens；其中 Fresh Observation 校准估算占 85.30%，System Prompt 占 3.55%，Workflow Memory 占 2.23%，Obtained Observation 仅占 0.62%。
2. **既有原文切块发生严重表示层膨胀。** `alpha.financial_statements` 的真实原始输出仅 132,471 chars，但 Fresh 序列化后达到 2,213,120 chars，放大 16.71 倍；`doxa_get_narrative_report` 从 87,639 chars 放大到 1,494,838 chars，放大 17.06 倍。主要不是 provider 原文天然达到 1.5M–2.2M chars，而是 dict/list 被继续拆成大量标量 block，并为每个 block 重复 `context_envelope`、alias、locator 等包装。
3. **既有 Full Compaction 对 Fresh 无压缩能力。** 25 次有 usage 的 Full Compaction 本身又消耗 12,053,820 input tokens，占本次可计量总输入的 59.22%；成功 Compaction 后的下一普通请求仍可达到 284,816–710,822 tokens。Compaction 输出通常只有 0.7k–8.0k chars，但输入中的 153k–566k token Active Context 基本原样保留。
4. **存在连续 Compaction。** 正常模式通常为同一 react step 连续两次 Full Compaction：第一次 `mode=micro`，第二次 `mode=full_compaction_result`；二者前后的 projected/active context 几乎不变。另有两组失败风暴：`run_c0b...` 同一 311,437-token projection 连续 6 次失败；`run_799.../ReviewExpectationFields` 同一 416,380-token projection 连续 4 次 Arrearage。
5. **显式 retain 的协议能力总体可用，但没有解决 Fresh 爆量。** 成功普通轮共声明 370 个 retain，354 个 alias 经当前 Task 校验有效（95.68%）。7 个可比较的“retain 后同任务下一正常研究轮”中，6 个完整同步到下一轮 Obtained Context；唯一归零发生在 pre-final/challenge 型请求，不应直接判定为普通研究轮 memory 丢失。
6. **Citation 明显强于 event-time。** 38 个成功且含 Fresh 的普通轮中，33 个有合法 Citation（86.84%），27 个有 retain（71.05%），只有 8 个输出 event-time tag（21.05%）。全部成功普通轮共识别 847 个去重合法 Citation alias、111 个 event-time tag；Citation 基本可用，event-time 输出不稳定。
7. **Document3 未进入。** 本批 smoke 最远到 `ReviewExpectationFields`，因此没有 Document3 节点 trace，不能对 Document3 作实际表现结论。

## 2. 数据文件与口径

逐请求明细不在本文重复铺开，以下三个 CSV 是本报告的完整数据底表：

- `INTC_smoke_loop_input_components_2026-07-14.csv`：每个节点、每一轮普通 Agent input 的 token、字符、组件占比、Fresh/Obtained/Passive、retain、Citation、event-time、`read_observation`。
- `INTC_smoke_fresh_tool_exposures_2026-07-14.csv`：每次 Fresh ToolResult 暴露的工具、原始大小、Agent-visible 大小、block 数、包装字符、结构和占比。
- `INTC_smoke_compaction_events_2026-07-14.csv`：36 次 Full Compaction 的位置、时间、触发模式、前置大小、Fresh/Obtained block、provider input、结果和下一普通请求大小。

口径说明：

- `provider_input_tokens` 是 LangSmith/provider usage 的精确值。provider 在调用前失败且未返回 usage 时留空，不能解释为 0 tokens。
- LangSmith/Bailian 不提供 JSON 字段级 tokenizer 账单。组件 token 使用“Unicode 加权序列化长度”按该请求精确 provider total 校准；组件占比是估算，字符数是精确值。
- Citation 复用生产代码 `normalize_citation_mentions`，多 alias 拆分、同 channel 去重、裸 alias 必须在当前 Task 中存在。本文的 Citation 数是“合法去重 alias 数”，不是简单正则命中次数。
- `Obtained input blocks` 按 `retained_observations` 顶层项计数，不再把同一个 Observation 内部 alias 字段重复计数。
- Micro Maintenance 不调用 LLM，因此没有独立 LangSmith run。本文区分：runtime audit 的精确 Micro 次数，以及 Full Compaction user payload 中可见的 `mode=micro` 前置标记。

## 3. 覆盖范围

| run | 节点 | 普通请求 | 有 usage 普通请求 | Full 请求 | 结果/边界 |
| --- | --- | ---: | ---: | ---: | --- |
| `run_c0b301944a744b058d5699c15b8f3d5c` | BuildGlobalResearch | 16 | 15 | 8 | D1 schema 阻塞；含 6 次连续 Full 失败 |
| `run_03c62e479e57456e9cfbcd2dc629d374` | BuildGlobalResearch | 15 | 12 | 6 | D1 schema 阻塞；6 次 Full 成功返回 |
| `run_4ea034df84d2462480023d2984efbc27` | BuildGlobalResearch | 9 | 9 | 2 | D1 schema 阻塞；第二次 Full 失败 |
| `run_07da484ebf2342e9a0a36a0d6b1e093f` | BuildGlobalResearch | 20 | 19 | 4 | 成功生成 D1，作为 clone source |
| `run_799e096d5f964e5eb1dbf4553058a47f` | Generate/Review Construction、Generate Details、Review Fields | 31 | 19 | 16 | 推进 D2，最终被数据库 objection upsert 阻塞 |
| `run_fc293967919e4faab6783ac46d91e104` | BuildGlobalResearch | 24 | 0 | 0 | 24 次均在 provider 侧 Arrearage；无 usage，不纳入 token 汇总 |

总计 151 次 LLM 请求。99 次有 provider usage：74 次普通请求、25 次 Full Compaction。另有 52 次失败请求没有 usage。

## 4. 每轮输入与组件占比

### 4.1 全局组件

74 次有 usage 的普通请求累计 8,301,760 tokens：

| 组件 | 校准估算 tokens | 占比 |
| --- | ---: | ---: |
| Fresh Observations | 7,081,238 | 85.30% |
| JSON/结构包装 overhead | 451,597 | 5.44% |
| System Prompt / Prompt Registry / Skills | 294,366 | 3.55% |
| Workflow Memory | 184,922 | 2.23% |
| 其他 user/task payload | 123,558 | 1.49% |
| Obtained Observations | 51,160 | 0.62% |
| Task Memory 其他字段 | 31,651 | 0.38% |
| ReAct protocol | 29,387 | 0.35% |
| Task contract | 35,641 | 0.43% |
| Passive Carryover | 18,240 | 0.22% |

18 次普通请求实际达到或超过 128,000 tokens；最大值 710,822。所有超大请求中 Fresh 通常占 87%–93%。

### 4.2 最大普通请求

| run / node / agent / step | input tokens | Fresh | Fresh blocks | Obtained | Prompt | 说明 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| `c0b...` BuildGlobalResearch C1 step3 | 710,822 | 92.92% | 3,898 | 0.14% | 0.83% | 5 个 ToolResult，共同叠加；alpha financial 最大 |
| `799...` GenerateExpectationDetails O1 step4 | 547,293 | 90.94% | 2,199 | 0% | 1.02% | 单个 DoxAtlas narrative ToolResult 即撑爆 |
| `799...` GenerateExpectationDetails O1 step3 | 546,101 | 90.95% | 2,199 | 0% | 1.03% | 单个 DoxAtlas narrative |
| `799...` GenerateExpectationConstruction O1 step2 | 544,198 | 91.36% | 2,199 | 0% | 1.00% | 单个 DoxAtlas narrative |
| `799...` ReviewExpectationConstruction A1 step2 | 538,708 | 92.59% | 2,199 | 0% | 0.81% | review 也被同一完整 Fresh 撑爆 |
| `07da...` BuildGlobalResearch C1 step2 | 518,845 | 92.78% | 2,839 | 0% | 1.12% | 5 个 ToolResult 叠加 |
| `03c...` BuildGlobalResearch C1 step2 | 511,663 | 92.79% | 2,818 | 0% | 1.14% | 5 个 ToolResult 叠加 |
| `c0b...` BuildGlobalResearch C2 step4 | 484,293 | 91.12% | 1,401 | 0% | 0.94% | 8 个 ToolResult 叠加 |
| `4ea...` BuildGlobalResearch C2 step2 | 322,424 | 90.76% | 1,097 | 0% | 1.29% | 8 个 ToolResult 叠加 |

结论既不是“永远只有一个大 Tool”，也不是“仅多个普通 Tool 相加”：

- Document2 是**单个 DoxAtlas narrative** 足以撑到约 539k–547k。
- Document1 同时存在**单个大 Tool**和**多 Tool 累积**；最极端轮中 `alpha.financial_statements` 单项最大，另叠加 OHLCV、Tavily、FRED/Polymarket 等结果。

## 5. Fresh Observation 按工具拆分

### 5.1 真实原文与 Agent-visible 包装

下表取每类工具本批最大单次 Fresh 暴露：

| Tool | 原始 output chars | Agent-visible chars | 放大倍数 | loaded blocks | envelope 占最终 payload | 真实结构 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| `alpha.financial_statements` | 132,471 | 2,213,120 | 16.71x | 3,850 | 85.5% | `/provider /symbol /statements /provider_errors /source_coordinates`；3,849 个 string scalar |
| `doxa_get_narrative_report` | 87,639 | 1,494,838 | 17.06x | 2,175 | 84.2% | `/data /provider /source_coordinates`；N/E/P/M/S/D 语义存在，但被拆为大量 scalar |
| `polymarket.market_probability` | 34,504 | 381,647 | 11.06x | 683 | 78.3% | provider/query/market/data/label；673 个 json scalar，少量 text/table |
| `twelvedata.daily_ohlcv` | 12,444 | 115,813 | 9.31x | 71 | 62.2% | OHLCV time series 10 块，另有 61 个 metadata/json scalar |
| `tavily.search` | 20,600 | 80,276 | 3.90x | 89 | 51.9% | search results 被拆为 57 json + 32 text |
| `fred.series_observations` | 120,463 | 276,973 | 2.30x | 255 | 43.5% | 118 个 time-series block + 137 metadata/json scalar |
| `bea.nipa_data` | 58,707 | 129,166 | 2.20x | 81 | 33.6% | 63 table block + 18 json block |
| `tavily.extract` | 74,276 | 137,289 | 1.85x | 95 | 31.9% | 89 text block + wrapper fields |
| `sec.company_facts_and_filings` | 286,530 | 15,287 | 0.05x | 3 | 8.2% | indexed_sec；只加载必要 dict/table，其他页通过 ref 读取 |

SEC 专项策略表现正确；本轮 SEC 不是爆量源。真正异常的是普通 `full` 模式在“完整可重建”之外又产生了远大于原文的重复包装。

### 5.2 累计暴露

由于同一类结果会在多个 agent/task 中重新调用或重新暴露，按“进入模型的暴露次数”累计：

| Tool | 暴露次数 | 累计 Agent-visible chars | 累计校准 tokens |
| --- | ---: | ---: | ---: |
| `alpha.financial_statements` | 8 | 9,855,275 | 1,546,275 |
| `doxa_get_narrative_report` | 6 | 8,963,258 | 2,983,805 |
| `twelvedata.daily_ohlcv` | 36 | 4,066,251 | 1,172,925 |
| `tavily.search` | 48 | 2,494,363 | 509,112 |
| `polymarket.market_probability` | 5 | 1,851,972 | 282,069 |
| `fred.series_observations` | 11 | 1,304,055 | 304,282 |

### 5.3 代码层根因

`observations.py::_value_blocks` 在 `force_structure=True` 时对每层 dict 递归继续强制结构化，并把该标记传给全部子节点。结果是：

- 原本可以在约 1,200 chars 内成组保存的相邻字段，继续拆成单个 string/int/float/bool；
- 每个 scalar block 都重复 `alias + block_type + context_envelope + locator/path`；
- `full` 模式把所有非-outline block 一次性装入 Fresh；
- 是否分页只看 `original_token_estimate > 128k`，没有检查**切块包装后的最终 Fresh 序列化大小**。

因此，1,200 chars 的“单 block 目标上限”被实现成了“结构递归尽量拆细”，完整性虽保留，但传输表示极不经济。

## 6. Obtained / Citation / Event Time

### 6.1 总体

74 个成功普通请求中：

| 指标 | 数值 |
| --- | ---: |
| 含 Fresh 的成功轮 | 38 |
| Fresh blocks 累计 | 30,742 |
| retain 声明 | 370 |
| 当前 Task 中有效 retain | 354（95.68%） |
| 输入中的 Obtained Observation | 184 |
| 输入中的 Passive Carryover | 62 |
| 合法去重 Citation alias | 847 |
| 有 Citation 的成功轮 | 34 |
| event-time tags | 111 |
| 有 event-time 的成功轮 | 9 |
| `read_observation` 调用 | 8，分布于 4 轮 |

含 Fresh 的 38 轮中：

- 33 轮有 Citation（86.84%）；
- 27 轮有 retain（71.05%）；
- 8 轮有 event-time（21.05%）。

### 6.2 retain 到下一轮同步

可比较的同任务正常转场：

| run / agent | 本轮 valid retain | 下一正常研究轮 Obtained | 结果 |
| --- | ---: | ---: | --- |
| `03c...` C3 | 17 | 17 | 完整同步 |
| `07da...` C2 step6→7 | 6 | 6 | 完整同步 |
| `07da...` C2 step8→9 | 6 | 6 | 完整同步 |
| `07da...` C2 step9→10 | 6 | 6 | 完整同步 |
| `4ea...` C3 | 12 | 12 | 完整同步 |
| `c0b...` C1 step2→3 | 34 | 34 | 完整同步 |
| `c0b...` C1 step3→pre-final/challenge | 16 | 0 | 特殊请求不装载正常 memory |

因此“显式 obtain 完全失效”不符合本批数据。更准确的判断是：

- 正常研究轮的 retain→Obtained 同步大多正确；
- Obtained 总 token 仅占 0.62%，不是上下文主因；
- Document2 多个 O1 任务各自独立，且大量任务在首次巨型 Fresh 后立即完成或进入 Compaction，没有形成丰富的 Obtained 使用轨迹；
- `read_observation` 仅 8 次，Agent 更多依赖首次完整 Fresh，而不是主动按需回读。

## 7. Compaction 数据

### 7.1 总量

| 指标 | 数值 |
| --- | ---: |
| Full Compaction 请求 | 36 |
| 有 provider usage 的 Full | 25 |
| 有 usage Full 累计 input tokens | 12,053,820 |
| Full 平均 input tokens | 482,153 |
| Full 最大 input tokens | 715,158 |
| Full 失败且无 usage | 11 |
| LangSmith 可观察 `mode=micro` 前置标记 | 23 |
| `run_799...` runtime bounded audit 精确 Micro | 8 |

`run_799...` 的 Micro=8 来自此前同一 smoke 报告已验证的 PostgreSQL bounded audit。其余旧 run 的 checkpoint 聚合本轮因远端数据库连接超时而熔断，不能把 23 个 `mode=micro` 标记直接声明为精确 Micro 次数。

### 7.2 典型连续 Full

| run / node / agent step | 第一次 before → provider | 第二次 before → provider | 下一普通请求 | 变化 |
| --- | --- | --- | ---: | --- |
| `03c...` Build C1 step2 | 436,990 → 506,497 | 436,982 → 506,463 | 511,663 | 几乎不变 |
| `07da...` Build C1 step2 | 443,480 → 513,182 | 443,535 → 513,309 | 518,845 | 反而略增 |
| `799...` GenerateConstruction O1 step2 | 404,102 → 530,996 | 404,185 → 531,139 | 544,198 | 反而略增 |
| `799...` ReviewConstruction A1 step2 | 402,132 → 531,026 | 402,222 → 531,214 | 538,708 | 反而略增 |
| `799...` GenerateDetails O1 step4 | 405,634 → 532,486 | 405,694 → 532,643 | 547,293 | 反而略增 |
| `c0b...` Build C1 step3 | 605,489 → 715,012 | 605,567 → 715,158 | 710,822 | 无实质压缩 |

### 7.3 失败重试

- `run_c0b.../BuildGlobalResearch/C2 step4`：相同 input hash、311,437 projected、285,258 active、1,401 Fresh blocks 连续请求 6 次；均失败，输入没有任何缩小。
- `run_799.../ReviewExpectationFields/C1 step2`：416,380 projected、384,714 active、2,680 Fresh blocks连续 4 次 Arrearage；输入没有变化。

### 7.4 为什么 Full 无法压缩

Full Compaction 的 system prompt 工作正常，模型也返回了 `synthesis_update / research_update / plan_update / retained_observation_update` 等压缩字段；问题不在“没有调用专用 prompt”。

真正原因是：

1. Full 请求把完整 `active_context` 作为 user payload 再发给模型，Fresh 本身已经占绝大部分；
2. Full 只能重写 Synthesis、Agenda、Retained 等可维护状态；
3. Fresh Observation 被视为当前轮必须消费，Compaction 结果不会删除或分页 Fresh；
4. 下一次 budget projection 仍包含同一批 Fresh，故继续 over-hard；
5. runtime 最终仍允许超预算普通请求进入 provider。

Full 因而从“缩小上下文”退化为“先额外支付一次 300k–715k 输入，再发送同样超大的普通请求”。

## 8. 修复建议

按优先级：

1. **先修 Observation 表示层膨胀。** `force_structure` 只控制当前自然结构边界，不应递归强制把所有子 dict 拆成 scalar。dict 应把连续字段打包到约 1,200 chars；list/table/time-series 应按连续行或自然 item 分组。
2. **共享 envelope，不在每个 block 重复。** Tool/provider/source coordinates 等稳定元数据放在一次性的 call outline/header；block 只保留 alias、ref、locator、type 和原文 content。当前 alpha 85.5%、DoxAtlas 84.2% 的最终 payload 都是重复 envelope/结构包装。
3. **用最终 Agent-visible 序列化大小做分页判定。** 不能只看 `ToolResult.output` 的 original token estimate。完成切块后若 `outline + loaded_blocks` 仍会使请求超过预算，必须转 `paged_oversized`，先给目录和完整自然页；不得继续标为 `full`。
4. **DoxAtlas 按 N/E/P/M/S/D 记录成组。** 当前语义标签已恢复，但 2,175 blocks 中大多仍是 scalar。应按 narrative/event/proposition/media/social/source 的完整记录或连续记录组切块，而不是按每个字段拆分。
5. **provider 紧凑化作为第二层。** Alpha financial 保留需要的 statement/period/核心字段；Polymarket 按市场 item；FRED/BEA/OHLCV 让 limit、日期范围真实控制行数；Tavily extract 对单篇超长正文分页。原文仍保存在 task-local raw store，通过稳定 ref 回读。
6. **Full 前增加不可压缩 Fresh 判定。** 若超限量主要来自 Fresh，跳过无效 Full 重试，直接分页/紧凑化；若仍无法缩到 128k，hard-block 当前 provider 请求并返回非阻塞 unavailable/partial，而不是发送 500k 请求。
7. **限制相同 hash 的 Full 重试。** 同一 input hash 且 Compaction 后 projected 未下降时，不得再次调用 Full；一次失败或一次无效结果后进入明确 fallback。
8. **继续强化 event-time，而非优先扩大 retain。** Citation 和 retain 已有基本能力；当前更明显的行为缺口是 event-time 仅覆盖 21.05% 的 Fresh 成功轮，以及 Agent 很少使用 `read_observation`。

## 9. 最终判断

| 验证项 | 结论 |
| --- | --- |
| 每节点每轮 input token 可观测 | 通过；完整见 loop CSV，失败无 usage 的请求明确留空 |
| Fresh/Obtained/Prompt/Skill 占比 | 已完成；Fresh 是绝对主因 |
| Fresh 按 ToolResult 细拆 | 已完成；既有单 Tool 爆量，也有多 Tool 叠加 |
| Obtained 与 Fresh 同步 | 正常研究转场大体通过；特殊 challenge 请求不装载正常 memory |
| Citation | 基本符合预期 |
| Event time | 不符合预期，覆盖率偏低 |
| Micro 精确统计 | `run_799` 有 runtime audit 精确值；其他旧 run 仅能报告 LangSmith 前置标记 |
| Full Compaction 实际压缩 | 不通过 |
| 128k hard budget | 不通过；最大普通 input 710,822 tokens |
| Fresh 原文切块效率 | 不通过；最高产生 17.06x 表示膨胀 |

本轮是只读轨迹审计，没有修改 runtime、Tool 或 Observation 代码。
