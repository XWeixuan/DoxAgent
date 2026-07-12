# 数据源 Tool、Payload 与 Observation 验证

日期：2026-07-11  
范围：workflow 实际允许调用的全部 23 个非 DoxAtlas 只读信息源 Tool。

## 1. 结论

- 23 个 Tool 已按真实 provider 调用方式完成第二轮复验，和 workflow 的 Tool 并集完全一致，无遗漏、无额外测试项。
- 最终真实轮次为 10 个 `succeeded`、1 个 `partial`、12 个 `failed`。失败项主要是本轮网络不可用或 provider 限额；它们均保留了失败语义，没有包装成成功。
- 全部 23 个调用均为 `validation_ok=true`；可切块输出的 block 最大 1,199 字符，没有超过 1,200 字符的块。
- 所有可用输出均通过 block hash、原文来源、task-local Raw ToolResult 保留和 `read_observation` 精确回读验证。
- SEC Agent 输出从修复前约 270 万字符降到约 19.4 万字符；完整 companyfacts 只保留在 task-local `ToolResult.raw`，不会整体进入 Agent Context 或持久化 ReAct audit。
- 最终真实轮次已写入 LangSmith，统一标签为 `react-memory-real-tool-chunking-2026-07-11`。代表性 run：SEC `0895d33e-a1cd-46b7-9ac2-b59cba948752`，SEC filing `98bc4e1e-d33b-4bb0-a4ce-fb434dbe27cc`，Tavily Extract `62e13fba-ef65-48cd-b3e4-f67de039561a`。

这里的 `failed` 不表示整体验收失败。对于当前不可调用的接口，验收重点是状态必须可信、错误可重试语义必须保留，不能伪装成有数据的 `succeeded`。

## 2. 状态契约

- `succeeded`：provider 返回了可用于研究的有效数据。
- `partial`：至少一部分子请求或原文可用，同时明确携带未完成部分和 `ToolError`。
- `failed`：限额、网络错误、业务错误、无有效数据或空结果，不能作为研究证据。
- 当前 `ResultStatus` 没有单独的 `unavailable` 枚举；临时不可用使用 `failed` 加 `upstream_unavailable`、`rate_limited` 等可辨识错误码表达。
- Alpha Vantage 的 `Information`、`Note`、`Error Message`，BEA 的 `BEAAPI.Error`，以及其他 provider 的 HTTP 200 错误包现在都会在 provider 层被识别。

## 3. 各 Tool 原始输出形态与 workflow 风险

“基线字数”来自修复前或上一轮成功真实调用的 `ToolResult.output` canonical JSON，用于说明 provider 的典型 payload 规模；动态接口每次调用会变化。“最终状态/字数”来自本次最终真实复验。

| Tool | 基线字数 | 原始输出形态 | 最近代表性状态/字数 | 对 workflow 的问题与处理 |
| --- | ---: | --- | --- | --- |
| `sec.company_facts_and_filings` | 2,702,442 | 完整 submissions 加 `companyfacts.facts`，META 有 471 个 concept | succeeded / 193,876 | 原始 payload 占总量绝大多数。输出改为公司信息、20 条重要 filing、13 个关键 fact、目录和单 concept 页面；完整原文只在 task-local raw。488 blocks，最大 1,176。 |
| `sec.filing_sections` | 20,793（成功轮次） | filing 元数据和原文 section；单 section 可达 20K | succeeded / 20,793 | 全量轮次曾因 SEC archive 暂时不可用而正确失败；随后重跑成功，两个 section 按原始空白精确切块和回读。自动查询真实 `primaryDocument`，不再盲猜文件名。 |
| `alpha.company_overview` | 325 | 常见为公司概览对象，也可能只有 `Information` | failed / 2 | 本轮限额，返回 `rate_limited`；不再把提示对象当公司指标。 |
| `alpha.financial_statements` | 862（错误包） | income、balance、cash-flow 三个子结果 | failed / 2 | 本轮限额；各子请求独立判定，部分成功时为 `partial`，全部失败时为 `failed`；`statement_type` 真实控制请求范围。 |
| `alpha.shares_outstanding` | 335（错误包） | shares 时间序列或 `Information` | failed / 2 | 本轮限额，正确返回 `rate_limited`。 |
| `alpha.earnings_events` | 1,384（partial 轮次） | history、estimates、calendar 三类子结果 | partial / 1,384 | calendar 可用、其他子请求失败时正确返回 `partial`；`event_type` 真实控制子请求。 |
| `yfinance.hk_basic_snapshot` | 192 | 港股估值和盈利指标，非官方 fallback | succeeded / 192 | 重跑获得有效指标；空指标会返回 `empty_result`，不会成功。 |
| `fred.series_observations` | 39,852 | 多 series，各自含 observation 数组 | failed / 2 | 本轮 provider 不可用；`limit` 现在同时下推 FRED 并在本地硬截断，避免 provider 忽略参数时返回全量历史。 |
| `bls.timeseries` | 3,453～5,557 | `Results.series[].data[]` | failed / 2 | 本轮网络不可用；成功轮次返回 2 个 series、35 行。`start_year/end_year` 已翻译为 BLS 的 `startyear/endyear`。 |
| `bea.nipa_data` | 706（错误包） | `BEAAPI.Results` 或 `BEAAPI.Error` | failed / 2 | 本轮网络不可用；BEA 业务错误和空 Results 不再成功。 |
| `fed.fomc_calendar_materials` | 6,996 | 年度日程文本和最多 80 个官方链接 | succeeded / 6,996 | 正常按文本、链接连续行切块，最大 1,199；找不到请求年份时返回 `partial`。 |
| `polymarket.market_probability` | 34,303 | market 列表，每项可能嵌套 events 等宽 schema | failed / 2 | 本轮网络不可用；`limit` 真实生效，空 market 返回 `empty_result`，`market_slug` 与 Descriptor 一致。 |
| `twelvedata.daily_ohlcv` | 7,922 | `values[]` 日线 OHLCV 和 meta | failed / 2 | 本轮网络不可用；空 `values` 不再成功，错误状态保留 retryable 语义。 |
| `yfinance.daily_ohlcv` | 2,144～9,160 | 非官方 fallback 日线 OHLCV 和 market snapshot | succeeded / 2,144 | 本轮 20 行，2 个日期范围块，最大 1,175；持续标记 `unofficial_source` 和 `fallback_for`，空行情返回失败。 |
| `finnhub.company_peers` | 134 | 小型 peers ticker 列表 | succeeded / 134 | 体积小；空 peers 返回 `empty_result`，不能解释为“没有竞争者”。 |
| `fmp.sector_performance` | 1,183～1,359 | sector 行列表 | succeeded / 1,359 | 输出新增 requested/resolved date、exchange 和 fallback 标记，避免免费层日期或交易所回退不可见。 |
| `tavily.search` | 6,965～7,015 | 5 条搜索结果，每条含 title、URL、content 等 | succeeded / 6,965 | 保留逐结果/逐字段 ref，44 blocks，最大 1,090；空结果和错误包不再成功。 |
| `tavily.extract` | 1,791～28,710 | URL 结果和 `raw_content` 长文本 | succeeded / 28,710 | 本轮正文约 27.9K；按原始段落边界和精确子串切成 43 blocks，最大 1,197。部分 URL 失败时为 `partial`。 |
| `anysearch.search` | 22,345 | `data.results[]`，每条可能含约 4K 正文 | failed / 2 | 本轮网络不可用；成功样本已确认逐结果、逐正文精确切块；空结果返回失败。 |
| `finnhub.trade_stream` | 53～85 | 有界 WebSocket events 数组 | partial / 85 | 1 秒窗口无事件时返回 `empty_stream_sample`，不再成功，也不能据此推断市场没有成交。 |
| `monitoring.get_ticker_config` | 248 | ticker 配置和 missing source | succeeded / 248 | 小 payload；缺失 source 保留在输出中，不能解释为完整覆盖。 |
| `monitoring.list_status` | 2,836 | source 状态、bindings、poll state 和 recent items | succeeded / 2,836 | 9 blocks，最大 1,052；查询 limit 继续由服务层执行。 |
| `monitoring.recent_events` | 13 | `events[]` | succeeded / 13 | 空持久化查询可以成功，但只表示当前查询没有事件，不表示事件源不存在。 |

## 4. SEC 专项输出

默认 `ToolResult.output` 只包含：

1. `company`：公司名称、ticker、交易所、SIC、实体类型和财年截止日；
2. `recent_filings`：优先保留 10-K、10-Q、8-K、20-F、6-K、S-1/S-3、DEF 14A 等重要 filing；
3. `key_facts`：收入、净利润、经营利润、资产负债、现金、经营现金流、资本开支、研发、EPS、股数和长期债务等高价值 concept，每项保留最新两条原始 observation；
4. `fact_directory`：concept/page 总数、taxonomy、第一页和稳定 ref 模板；
5. `fact_pages`：每页一个 concept，仅保留 label、unit 和最新一条原始 observation。

META 本轮共有 471 页。Fresh Observation 优先加载目录、关键 fact 和第一页，outline 最多列出 24 个 ref；其他页用 `obs_<tool_call_id>::/fact_pages/page_####` 读取。若个别 page 本身仍超过 1,200 字符，该 page ref 会作为 outline，Agent 可用 `include_children=true` 精确读取其字段。

完整 companyfacts 历史仍在当前 task 的 `ToolResult.raw`，用于 task 内恢复和审计校验；持久化 audit 只保存字符数、hash、状态、错误码、block 统计和少量 ref，不保存完整 raw/output。

## 5. Observation 切块规则

- 普通 block 目标上限为 1,200 canonical JSON 字符；实际最终真实轮次最大 1,199。
- dict 按字段递归；list/table 按连续行和字符上限双重分组；time series 的 ref 保留日期范围。
- 搜索结果优先保留单条结果层级，再按字段和正文继续拆分。
- 长文本优先按段落边界切分；超长段落按精确子串切分，不 trim、不重新拼接空白、不生成摘要。
- outline 最多展示 24 个 block，并给出总数、遗漏数和 locator prefix。
- `read_observation` 返回被引用 block 的精确内容；父子读取由 `include_parent/include_children` 显式控制。

## 6. 验收证据

- 真实复验：`uv run python scripts/validate_react_memory_real_tools.py --group all`，退出码 0，23/23 `validation_ok=true`。
- Tool/Memory 联合测试：`52 passed`。
- 定向 Ruff：通过。
- 定向 Mypy：15 个 source 文件通过。
- workflow Tool 并集核对：23 个 workflow source tools，`missing=[]`、`extra=[]`。
