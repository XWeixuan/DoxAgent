# Fresh Observation 膨胀优化与逐 Tool 验证报告

日期：2026-07-14

## 1. 结论

本轮只修改 Fresh Observation 的切块与 Agent-visible 表示，不修改 Micro/Full Compaction、Prompt、Retained/Passive Observation 或 workflow 业务逻辑。

当前真实 Tool Registry 共 38 个 Tool。本轮逐个调用并验证其中全部 35 个只读信息源 Tool；排除会修改外部状态的 `doxa_run_analysis`、`doxa_run_narrative_research` 和 `monitoring.update_ticker_config`。

- 35/35 通过 Agent-visible 尺寸验收；
- 35/35 可从 task-local blocks 精确重建原始 `ToolResult.output`；
- 35/35 的 `read_observation` 逐块回读与 content hash 一致；
- 全部 block 均不超过 1,200 chars，最大值恰为 1,200；
- 真实结果合计由 1,168,868 raw chars 变为 1,062,542 Agent-visible chars，整体为 0.909x；
- 对 raw output 不小于 1,024 chars、且采用 full delivery 的 24 个结果，放大倍数中位数为 1.110x，最大为 1.209x；
- 30 个 Tool 返回 `succeeded`，1 个返回 `partial`，4 个返回 `failed`；失败/部分成功均保留 provider 的真实语义，没有假成功。

验收阈值为：

```text
agent_visible_chars <= max(raw_output_chars * 1.25, raw_output_chars + 1,024)
```

1,024 chars 的固定余量只用于极小、空或错误结果的必要 alias/path/outline 协议开销；对正常 payload 采用严格的 1.25x 上限。

## 2. 核心改动

1. Agent-visible block 不再重复完整 `context_envelope`、`block_type`、tool call metadata 和大目录；full delivery 的 outline 只保留 Tool、模式、原始字符数和 block 数。
2. `force_structure` 不再递归强制拆散所有后代。大 dict 按连续字段打包，大 scalar list 按连续 item 打包，目标上限为 1,200 chars。
3. DoxAtlas 继续先解包 `output.data`，但按 N/E/P/M/S/D 自然记录组织；Agent-visible block 使用短 `kind`，不再为每个标量重复长 locator/envelope。
4. table/time-series 的 Agent-visible 内容移除由 rows 可推导的重复 `columns`，正文不摘要、不改写。
5. full/indexed 判断改为检查最终 Agent-visible payload 的 token 估算；SEC company facts 保持原有专项 indexed/page 策略。
6. 补齐空 dict/list、字段组、item 组的精确重建，避免通过“省略空容器”换取虚假压缩。

完整原始 `ToolResult`、source coordinates、父子关系和 audit metadata 仍保存在 task-local store/audit 中，只是不再反复发送给模型。

## 3. 审计问题的前后对照

| Tool | 审计时 raw → visible | 审计时倍数 | 本轮真实 raw → visible | 本轮倍数 | block 变化 |
| --- | ---: | ---: | ---: | ---: | ---: |
| `alpha.financial_statements` | 132,471 → 2,213,120 | 16.71x | 266,420 → 292,779 | 1.099x | 3,850 → 414 |
| `doxa_get_narrative_report` | 87,639 → 1,494,838 | 17.06x | 87,639 → 91,425 | 1.043x | 2,175 → 150 |
| `polymarket.market_probability` | 34,504 → 381,647 | 11.06x | 34,636 → 37,698 | 1.088x | 683 → 75 |
| `twelvedata.daily_ohlcv` | 12,444 → 115,813 | 9.31x | 8,809 → 9,749 | 1.107x | 71 → 14 |

不同时间的 provider 数据量可能变化，因此这里只比较表示倍数和 block 数，不把 raw chars 的变化归因于本次代码。

## 4. 非 DoxAtlas 信息源逐 Tool 结果

| Tool | Provider 状态 | Raw chars | Agent-visible chars | 倍数 | Blocks | 最大 block | Delivery | 验收 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- |
| `alpha.company_overview` | succeeded | 2,082 | 2,417 | 1.161x | 7 | 1,185 | full | PASS |
| `alpha.earnings_events` | succeeded | 40,915 | 45,629 | 1.115x | 63 | 1,191 | full | PASS |
| `alpha.financial_statements` | succeeded | 266,420 | 292,779 | 1.099x | 414 | 1,200 | full | PASS |
| `alpha.shares_outstanding` | failed: `empty_result` | 2 | 167 | 83.500x（+165） | 1 | 2 | full | PASS |
| `anysearch.search` | succeeded | 15,587 | 17,320 | 1.111x | 29 | 1,146 | full | PASS |
| `bea.nipa_data` | failed: `upstream_provider_error` | 2 | 156 | 78.000x（+154） | 1 | 2 | full | PASS |
| `bls.timeseries` | failed: `upstream_unavailable` | 2 | 157 | 78.500x（+155） | 1 | 2 | full | PASS |
| `fed.fomc_calendar_materials` | succeeded | 7,306 | 8,098 | 1.108x | 12 | 1,199 | full | PASS |
| `finnhub.company_peers` | succeeded | 403 | 661 | 1.640x（+258） | 4 | 247 | full | PASS |
| `finnhub.trade_stream` | partial: `empty_stream_sample` | 85 | 341 | 4.012x（+256） | 4 | 14 | full | PASS |
| `fmp.sector_performance` | succeeded | 1,828 | 2,088 | 1.142x | 4 | 1,146 | full | PASS |
| `fred.series_observations` | succeeded | 5,005 | 6,002 | 1.199x | 18 | 1,141 | full | PASS |
| `monitoring.get_ticker_config` | succeeded | 248 | 546 | 2.202x（+298） | 5 | 117 | full | PASS |
| `monitoring.list_status` | succeeded | 2,836 | 3,429 | 1.209x | 9 | 1,052 | full | PASS |
| `monitoring.recent_events` | succeeded | 13 | 174 | 13.385x（+161） | 1 | 2 | full | PASS |
| `polymarket.market_probability` | succeeded | 34,636 | 37,698 | 1.088x | 75 | 1,198 | full | PASS |
| `sec.company_facts_and_filings` | succeeded | 194,180 | 3,653 | 0.019x | 489 stored / 3 selected | 1,176 | indexed_sec | PASS |
| `sec.filing_sections` | succeeded | 21,259 | 23,472 | 1.104x | 32 | 1,122 | full | PASS |
| `tavily.extract` | succeeded | 19,003 | 21,711 | 1.143x | 32 | 1,196 | full | PASS |
| `tavily.search` | succeeded | 7,466 | 8,252 | 1.105x | 21 | 1,150 | full | PASS |
| `twelvedata.daily_ohlcv` | succeeded | 8,809 | 9,749 | 1.107x | 14 | 1,160 | full | PASS |
| `yfinance.daily_ohlcv` | succeeded | 9,305 | 10,413 | 1.119x | 16 | 1,166 | full | PASS |
| `yfinance.hk_basic_snapshot` | succeeded | 326 | 754 | 2.313x（+428） | 9 | 112 | full | PASS |

## 5. DoxAtlas 信息源逐 Tool 结果

| Tool | Provider 状态 | Raw chars | Agent-visible chars | 倍数 | Blocks | 最大 block | Delivery | 验收 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- |
| `doxa_get_narrative_report` | succeeded | 87,639 | 91,425 | 1.043x | 150 | 1,200 | full | PASS |
| `doxatlas.query` | succeeded | 87,628 | 91,403 | 1.043x | 150 | 1,200 | full | PASS |
| `doxa_query_analysis` | succeeded | 2,692 | 3,165 | 1.176x | 10 | 441 | full | PASS |
| `doxa_get_analysis` | succeeded | 225,729 | 239,967 | 1.063x | 555 | 1,198 | full | PASS |
| `doxa_query_propositions` | succeeded | 2,501 | 2,766 | 1.106x | 7 | 999 | full | PASS |
| `doxa_get_ignored_propositions` | succeeded | 15,682 | 17,724 | 1.130x | 42 | 536 | full | PASS |
| `doxa_get_social_result` | succeeded（0 items） | 674 | 882 | 1.309x（+208） | 3 | 378 | full | PASS |
| `doxa_get_social_result_detail` | failed: `invalid_scope` | 2 | 172 | 86.000x（+170） | 1 | 2 | full | PASS |
| `doxa_get_media_result` | succeeded | 1,591 | 1,799 | 1.131x | 3 | 1,166 | full | PASS |
| `doxa_get_media_result_detail` | succeeded | 2,327 | 2,751 | 1.182x | 9 | 948 | full | PASS |
| `doxa_get_event_source` | succeeded | 52,342 | 57,410 | 1.097x | 76 | 1,147 | full | PASS |
| `doxatlas.source_lookup` | succeeded | 52,343 | 57,412 | 1.097x | 76 | 1,147 | full | PASS |

所选 event 的 social compact 查询返回 0 items，因此 detail 使用合法 `S01` 请求真实端点后得到 `invalid_scope`。这不是切块失败；结果没有被包装为 `succeeded`，空失败 payload 的固定协议开销为 170 chars。

## 6. 测试与边界

- 真实调用脚本：`scripts/validate_react_memory_real_tools.py --group all` 和 `--group doxatlas`；每次只向 LangSmith 写入结构指标，不持久化原始 provider payload。
- 真实校验项：参数调用、状态语义、block 上限、内容 hash、原文派生、task-local raw 精确性、完整 payload 重建、标准 `read_observation` 精确回读、Agent-visible 尺寸。
- 相关回归：73 passed，2 skipped；覆盖 ReAct Memory、Evidence Ref、真实 Tool contract、ReAct harness 和 Document1/2 schema integration。
- 静态检查：修改文件通过 Ruff。
- 本轮没有修改或验证 Full Compaction 的压缩行为；审计报告中的 Compaction 问题仍留待后续独立任务处理。

