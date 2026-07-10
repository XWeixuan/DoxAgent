# ReAct Memory 真实 Tool Payload 与 Supabase Egress 验证

日期：2026-07-11  
范围：DoxAgent 当前 agent/workflow 实际允许调用的全部非 DoxAtlas 信息源工具。

## 1. 验证方法

- 取 `default_agent_definitions()` 中 `allowed_tools` 的并集，排除全部 DoxAtlas 工具和写操作 `monitoring.update_ticker_config`，共 23 个读取型工具。
- 通过 `default_real_tool_registry()` 调用真实 provider；主样本为 `META`，港股专用接口使用 `0700.HK`。
- 每次调用写入 LangSmith tool run，统一标签为 `react-memory-real-tool-chunking-2026-07-11`。LangSmith 中共确认 39 条修复前后记录、23 个唯一工具。
- “原始字符数”是对 `ToolResult.output` 做 canonical JSON 后的字符数，不包含 `raw` 的重复副本。
- 报告只保存尺寸、结构和 hash 指标，不保存 provider 原文。
- 结构验收要求：Raw ToolResult 精确保留、block hash 正确、`read_observation` 精确回读、block 内容来自原文、单块不超过 16,000 字符。

需要特别区分：`structure_ok=true` 只表示 Memory 正确切块，不代表 provider 返回了有效业务数据。

## 2. 总体结论

- 23 个工具本轮合计返回 2,845,776 字符。
- `sec.company_facts_and_filings` 单次为 2,702,442 字符，占本轮总量约 95%，是最主要的上下文和持久化风险。
- 修复前存在 4 个真实巨块：SEC companyfacts、FRED、Polymarket、AnySearch，最大单块约 270 万字符。
- 修复后 23 个工具的结构验收全部通过，所有 block 均不超过 16,000 字符。
- 切块完成后仍不能把全部 block 放入 prompt。当前只展示有限 outline 和 2 至 3 个 selected block；Micro Maintenance 下进一步只展示一个 selected block。
- 完整 Raw ToolResult 只留在当前 task 内存，不再随 `AgentResult.react_audit` 写入 Blackboard/Supabase。

## 3. 各数据源原始输出形态

下表是 2026-07-11 真实调用快照。动态接口的条数和字符数会随时间变化。

| Tool | Provider 状态 | 原始字符数 | 原始输出形态 | 修复后切块 | 对 workflow 的问题 |
| --- | --- | ---: | --- | --- | --- |
| `sec.company_facts_and_filings` | succeeded | 2,702,442 | `{provider,cik,submissions,companyfacts}`；`companyfacts.facts.us-gaap` 有 456 个 concept，约 269 万字符 | 723 blocks；最大 15,984 | P0。绝不能整体进入 Fresh Context、audit 或 checkpoint；outline 也必须限长。 |
| `sec.filing_sections` | partial | 776 | `{provider_status,sections,unknowns,warnings,error}`；本次 `sections=[]` | 10 blocks；最大 501 | 当前调用没有取得 filing 原文，不能当作 SEC 正文证据。descriptor 使用 `items`，provider 实际读取 `sections`，且自动 primary document 回退存在失败风险。 |
| `alpha.company_overview` | succeeded，但 payload 为 provider `Information` | 325 | `{provider,function,symbol,data}`；`data` 是单字段提示对象 | inline 1 block | 状态语义错误：配额/提示响应仍被包装成 succeeded，agent 可能误判为已取得公司指标。 |
| `alpha.financial_statements` | succeeded，但三个 statement 均为 `Information` | 862 | `{provider,symbol,statements}`；三种报表各是嵌套提示对象 | 9 blocks；最大 235 | 没有真实报表行却被判 succeeded；必须在 provider 层识别 `Information`/`Note`/`Error Message`。 |
| `alpha.shares_outstanding` | succeeded，但 payload 为 `Information` | 335 | `{provider,function,symbol,data}` | inline 1 block | 与 company overview 相同，存在假成功。 |
| `alpha.earnings_events` | succeeded，部分子接口为 `Information` | 730 | `{provider,symbol,earnings}`；历史/预期为提示对象，calendar 为 1 行数组 | inline 1 block | 混合有效/无效子结果却整体 succeeded，workflow 无法知道哪些字段可用。 |
| `yfinance.hk_basic_snapshot` | succeeded | 192 | 8 个扁平估值/盈利指标 | inline 1 block | 体积小，但属于 unofficial fallback，不能覆盖主数据源或提高为高置信度事实。 |
| `fred.series_observations` | succeeded | 39,852 | `{provider,series,failed_series,...}`；DGS10 有 396 observations，CPIAUCSL 有 17 | 40 blocks；最大 4,813 | 输入 `limit=24` 没有下推给 FRED，DGS10 仍返回 396 行；请求规模不受控会增加延迟和 task 内存。 |
| `bls.timeseries` | succeeded | 5,557 | `data.Results.series[2].data`，分别约 29/30 行 | 10 blocks；最大 5,442 | descriptor 暴露 `start_year/end_year`，provider 读取 `startyear/endyear`，agent 按 descriptor 调用时年份过滤会被忽略。 |
| `bea.nipa_data` | succeeded，但 `data.BEAAPI.Error` 存在 | 706 | `{provider,dataset,data}`；`data` 内含 Request 与 Error | 11 blocks；最大 514 | provider 没有把 BEA API 业务错误映射为 failed/partial，会产生假成功。 |
| `fed.fomc_calendar_materials` | succeeded | 6,996 | `{calendar_text,links[80],unknowns,parser}` | 7 blocks；最大 3,974 | 结构正常；80 条链接不应全部默认装入 prompt，只需 outline 与按需读取。 |
| `polymarket.market_probability` | succeeded | 34,303 | `data.items[5]`；每个 market 约 6.5K 至 7.1K 字符、89 至 91 字段，并嵌套 events | 9 blocks；最大 15,055 | 原始 schema 过宽，且 search 结果可能与问题不相关；应保留市场级 ref，不应整体注入。 |
| `twelvedata.daily_ohlcv` | succeeded；首次调用曾 transient unavailable | 7,922 | `{provider,symbol,interval,ohlcv[60],meta,market_evidence_snapshot}` | 2 blocks；最大 6,065 | 时间序列适合按日期范围切块；上游瞬时失败必须保留 retryable 状态，不能回退为“无行情”。 |
| `yfinance.daily_ohlcv` | succeeded | 9,160 | `{provider,symbol,interval,ohlcv[60],market_evidence_snapshot,...}` | 2 blocks；最大 7,226 | 结构正常，但为 unofficial fallback；必须保留 fallback/unofficial 标记。 |
| `finnhub.company_peers` | succeeded | 134 | `{provider,symbol,peers.items[11]}` | inline 1 block | 体积小；peer 列表只能构造研究范围，不能直接证明竞争关系。 |
| `fmp.sector_performance` | succeeded | 1,183 | `sector_performance.items[11]`，每行 4 字段 | 3 blocks；最大 1,197 | 结构正常；free-tier 日期/交易所 fallback 必须随证据保留。 |
| `tavily.search` | succeeded | 7,015 | `search.results[5]`；每条约 1.3K 至 1.5K 字符 | 14 blocks；最大 1,474 | 搜索结果必须保持逐条 ref，不能合并成一个表格 ref；content 仍只是搜索摘要。 |
| `tavily.extract` | succeeded | 1,791 | `extract.results[1].raw_content`，正文约 1,502 字符 | 2 blocks；最大 1,759 | 本次较小；多 URL 或长正文时必须继续按 URL/段落递归切块。 |
| `anysearch.search` | succeeded | 22,345 | `search.data.results[5]`；每条约 4.2K 至 4.5K，正文约 4K | 15 blocks；最大 4,545 | 单次 5 条结果已超过 22K；必须逐结果 ref，不能把 `search` 整体放入 prompt。 |
| `finnhub.trade_stream` | succeeded，但 events 为空 | 53 | `{provider,symbols[1],events[]}` | 4 blocks；最大 35 | 1 秒有界样本在当前市场状态没有成交；空样本不能解释为“没有交易活动”。 |
| `monitoring.get_ticker_config` | succeeded | 248 | ticker 配置摘要；本次有 6 个 `missing_source_ids` | inline 1 block | 体积小，但结果表明当前 monitoring coverage 不完整。 |
| `monitoring.list_status` | succeeded | 2,836 | `sources[6]`，bindings/poll states/recent items 本次为空 | inline 1 block | 目前可 inline；若 future recent messages 增长，descriptor 应转 indexed，且 limit 必须继续下推。 |
| `monitoring.recent_events` | succeeded，空结果 | 13 | `{events[]}` | outline 1 block | 空 replay 只说明当前查询窗口没有持久化事件，不能否定事件源存在。 |

## 4. 对整体 workflow 的主要问题

### P0：原始 payload 不能进入 prompt 或 Supabase audit

SEC companyfacts 一次约 270 万字符。即使模型窗口足够大，也不能把它当作普通 observation：

- 会挤掉 Synthesis、Agenda、Fresh Observation 和最终输出预算；
- 会使 Micro/Full Compaction 每轮都被迫触发；
- 如果复制进 Working Memory/checkpoint，会再次造成大行写入和 pooler egress；
- 如果 outline 罗列全部 723 个 ref，本身也会成为新的大上下文。

当前修复：Raw Store 保留原文；Observation Store 递归切块；正常 outline 最多列 48 个 block，并给出总数、遗漏数和 locator prefix；Fresh View 只加载少量 selected block。

### P0：provider 的 succeeded 不等于取得有效数据

Alpha Vantage 的 `Information`、BEA 的 `BEAAPI.Error` 都被当前 client 包装成 succeeded。这比切块失败更危险，因为 workflow 会把数据缺口误判为已完成工具调用。后续应在各 provider client 的 `_success` 前统一识别：

- `Information`
- `Note`
- `Error Message`
- provider 自有 `Error` 对象
- HTTP 200 中的业务错误码

并映射为 `partial` 或 `failed`。

### P1：descriptor 与 provider 参数不一致

- SEC descriptor 暴露 `items`，provider 读取 `sections`。
- BLS descriptor 暴露 `start_year/end_year`，provider 读取 `startyear/endyear`。
- FRED descriptor 暴露 `limit`，provider 没有把它传给上游或在本地截断。

这些问题会让 agent 生成“schema 看似正确、实际被 provider 忽略”的调用。

### P1：fallback 与空响应必须保留语义

- yfinance 必须继续标记为 unofficial fallback。
- Finnhub 1 秒 stream 的空 events 不能转化为市场结论。
- Monitoring 空 events、空 poll state 或 missing source 只能作为 coverage gap。

## 5. Memory 上下文修复

本轮增加的约束如下：

1. 大 dict 按字段递归；大 list 优先按自然 item/table 拆分。
2. 表格同时受 50 行和 16,000 字符双重上限约束。
3. 长文本按段落和字符上限拆分。
4. 搜索结果保留 `/results/0` 这类 item-level ref。
5. time series 保留日期范围 ref。
6. outline 最多展示 48 个 block，其余通过计数和 locator prefix 暴露。
7. `read_observation` 仍返回精确 block 原文；Raw Store 仍能恢复完整 ToolResult。