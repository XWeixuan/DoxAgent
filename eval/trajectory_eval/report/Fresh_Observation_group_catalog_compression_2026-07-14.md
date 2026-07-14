# Fresh Observation 分组目录与 Tool 专项压缩报告

日期：2026-07-14

## 1. 结论

本轮只调整 Fresh Observation 的 Agent-visible 表示、目录回读和相关 `read_observation` 契约，不修改 Micro/Full Compaction。

- 取消 `omitted_block_count` 和“只列前几十条”行为；目录或 fallback index 均完整展示。
- 专项 Tool 使用“高价值完整 blocks + 低价值分组目录”；两部分互斥，且与 task-local 原文合并后覆盖全部 output blocks。
- 分组目录 alias 可用一次标准 `read_observation` 调用读取整组；单组最多约 40,000 原文 chars，超限自动形成 `part_01` 等稳定分页目录。
- 分组策略无法识别的低价值 block 会进入完整 fallback `block_index`；索引项只保留 `alias + path`，不含 `type`。
- 未配置专项策略的 Tool，raw output 超过 50,000 chars 后，前段 blocks 完整展示，后段所有 blocks 进入完整索引。
- 原始 `ToolResult.output` 仍保留在 task-local store；所有验证样本均可从 blocks 精确重建，目录整组回读与原 block 内容一致。

“Agent-visible 不低于 10,000 chars”只适用于成功且 raw 不低于 10,000 chars 的专项大结果。raw 本身不足 10,000、空结果、限额或 provider 失败不能通过填充伪造到 10,000；此时保留完整、准确的返回语义。

## 2. 分组目录与回读契约

Fresh Observation 的大结果结构为：

```json
{
  "outline": {
    "tool_name": "fred.series_observations",
    "delivery_mode": "hybrid_profiled",
    "group_catalog": [
      {
        "path": "/macro/fred/dgs10/earlier_history/part_01",
        "alias": "O45",
        "block_count": 31,
        "chars": 39520
      }
    ]
  },
  "loaded_blocks": ["高价值原文 blocks"]
}
```

模型用标准结构读取整组：

```json
{
  "tool_calls": [
    {
      "tool_name": "read_observation",
      "input": {
        "alias": "O45",
        "include_parent": false,
        "include_children": false
      }
    }
  ]
}
```

Runtime 识别目录 anchor alias 后加载该目录的全部完整 blocks；同一组只作为一次 Fresh Read 注入，避免每个成员 alias 再重复注入。`block_index` alias 仍只读取一个精确 block。禁止 `{"read_observation": {...}}` 快捷结构。

## 3. 专项 Tool 策略

| Tool | 默认完整展示的高价值内容 | 分组目录中的低价值内容 |
| --- | --- | --- |
| `alpha.financial_statements` | 每张报表最近 6 个季度、最近 3 个年度及报表核心字段 | `/financials/{income_statement|balance_sheet|cash_flow}/{quarterly_history|annual_history}` |
| `alpha.earnings_events` | 非估计核心字段和最近 16 条 estimates | `/earnings/estimates/later_periods`、重复 metadata |
| `fred.series_observations` | 每个 series 的 metadata 与最近 120 条 observations | `/macro/fred/{series}/earlier_history` |
| `sec.company_facts_and_filings` | 公司信息、recent filings、key facts、fact directory/status | `/sec/company_facts/fact_pages`，过大时分 part |
| `sec.filing_sections` | filing 身份字段及每个 section 前约 8,000 chars | `/sec/filing_sections/section_{n}/remainder` |
| `bea.nipa_data` | metadata 之外的核心字段及最新约 16,000 chars 时序行 | `/macro/bea/nipa/earlier_periods` |
| `polymarket.market_probability` | 查询信息及排序前 2 个 market 结果 | `/markets/polymarket/lower_ranked_results` |
| `doxa_get_analysis` | media 前 2 个、social 前 3 个 topic 的主记录及核心结构 | `/doxatlas/analysis/{media|social}/source_details` 与 `lower_ranked_topics` |
| `doxatlas.query` | narrative/event/proposition 主记录和较新的 flow | `/doxatlas/narratives/earlier_flow`、可由主记录回读的 detail 与 metadata |
| `doxa_get_narrative_report` | 尽可能保留 narrative/event/proposition 原 blocks 和较新的 flow | 仅较早 flow、重复 provider/source coordinates 及确属低价值的 detail |

每条规则均由 locator、记录序号、日期区间或 DoxAtlas N/E/P/M/S/D semantic 等确定性结构判断，不调用模型摘要，也不改写原文。

## 4. 真实调用结果

以下为同一轮实现中的真实 provider 调用。动态数据会变化；比例按 `Agent-visible chars / raw output chars` 计算。

| Tool | Provider 状态 | Raw chars | Agent-visible chars | 比例 | 完整 blocks / 目录 blocks / 目录组 | 结果 |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| `alpha.financial_statements` | succeeded（较早同轮样本）；最终复核时 rate_limited | 266,420 | 34,153 | 0.128x | 41 / 288 / 10 | PASS；最终限额语义为 failed，未假成功 |
| `alpha.earnings_events` | partial（provider 子请求限额） | 1,682 | 2,093 | 1.244x | 6 / 0 / 0 | 小错误结果完整展示；大结构策略由真实 30k 结构审计和合成测试覆盖 |
| `fred.series_observations` | succeeded | 122,649 | 41,566 | 0.339x | 44 / 84 / 6 | PASS |
| `sec.company_facts_and_filings` | succeeded | 194,180 | 12,198 | 0.063x | 15 / 473 / 6 | PASS |
| `sec.filing_sections` | succeeded | 21,259 | 11,740 | 0.552x | 17 / 13 / 2 | PASS |
| `bea.nipa_data` | failed: `upstream_provider_error` | 2 | 156 | N/A | 1 / 0 / 0 | 状态准确；大 payload 策略由结构化合成测试覆盖 |
| `polymarket.market_probability` | succeeded | 34,613 | 15,589 | 0.450x | 24 / 29 / 2 | PASS |
| `doxa_get_analysis` | succeeded | 225,729 | 34,891 | 0.155x | 约 250 / 296 / 8 | PASS |
| `doxatlas.query` | succeeded | 87,628 | 84,503 | 0.964x | 94 / 8 / 2 | PASS；按特殊策略保留大部分主记录 |
| `doxa_get_narrative_report` | succeeded | 87,639 | 84,514 | 0.964x | 94 / 8 / 2 | PASS；仅归档真正低价值内容 |

典型目录验证：

- SEC facts：metadata + `/sec/company_facts/fact_pages/part_01` 至 `part_05`；
- FRED：DGS10 较早历史分 3 组，CPIAUCSL/UNRATE 各 1 组，再加 metadata；
- DoxAtlas analysis：social source details、lower-ranked topics、media source details 四个分页、media lower-ranked topics 和 metadata；
- narrative/query：`/doxatlas/narratives/earlier_flow` 与 `/metadata/provider_and_source_coordinates`。

对于没有专项策略的 `doxa_get_event_source`，真实 raw 52,342 chars 触发通用 50k 回退：前 59 blocks 完整展示，后 5 blocks 全部进入 type-free index，Agent-visible 为 54,622 chars。少量协议开销使其略高于 raw，但没有显著膨胀，也没有省略索引。

## 5. 验证覆盖

- 93 passed，1 skipped；覆盖分组目录、整组回读一次、完整覆盖与互斥、无 omitted/type、10 个专项策略、通用 50k fallback、SEC 大结果、DoxAtlas 语义切块、payload 重建及 Prompt/tool schema。
- Ruff 与 `git diff --check` 通过。
- 所有成功的大结果：单 block 不超过目标 1,200 chars；完整 blocks、catalog blocks、fallback indexed blocks 的并集可重建原始 `ToolResult.output`。
- 真实调用均写入 LangSmith 结构化验证 summary，不持久化原始大 payload。

## 6. Prompt / Skill 契约同步

- `prompts/workflows/memory.md` 已补充 `group_catalog`、`block_index`、50k fallback 和整组回读规则，并保留完整极简 `read_observation` schema/example。
- Runtime 的 `read_observation` descriptor 已增加正式 JSON `input_schema`，明确 group alias 读取整组、index alias 读取单块，且 `additionalProperties=false`。
- ReAct harness fallback rule 已同步标准 `tool_calls` 结构和快捷结构禁令。
- 仓库内现有 `SKILL.md` 未发现 `read_observation` schema 或同名旧指令，因此没有可修改的 Skill 副本；避免新建重复规则来源。

