# 宏观与行业数据源逐 Tool 真实验收

日期：2026-08-08  
范围：仅真实 provider API 调用；未发起 LLM request。调用通过当前本地 secret 注入完成，本文不记录密钥、完整 URL query 或原始 payload。

## 方法

- 入口：`default_real_tool_registry(DoxAgentSettings())`，以 `C2_MACRO_RESEARCH` 的显式临时 allowlist 调用。
- 每个 tool 只做一次最小业务调用；对于 EIA 首轮 HTTP 400，进行了**一次**基于官方 route/facet 结构的修正并复验。其余失败不循环重试。
- `records` 是返回中可用观测记录数；FRED/BLS 按实际 observation/data 行计数，EIA 按 `response.data` 计数。

## 结果

| Tool | 脱敏最小参数概要 | 状态 | 错误码 | 可用记录/顶层输出 | 耗时 |
|---|---|---|---|---|---|
| `fred.activity_demand` | `real_gdp`, start=2024-01-01, limit=3 | `SUCCEEDED` | — | 3 observations；`series`, `failed_metrics`, `source_coordinates` | 3.033s |
| `fred.inflation_labor` | `core_pce`, start=2024-01-01, limit=3 | `SUCCEEDED` | — | 3 observations；同上 | 1.357s |
| `fred.rates_credit_liquidity` | `nfci`, start=2024-01-01, limit=3 | `SUCCEEDED` | — | 3 observations；同上 | 1.329s |
| `fred.commodities_fx` | `wti`, start=2024-01-01, limit=3 | `SUCCEEDED` | — | 3 observations；同上 | 1.324s |
| `bls.labor_inflation` | `cpi_all_urban`, 2025–2026 | `SUCCEEDED` | — | 18 data rows；`requested_metrics`, `data`, `source_coordinates` | 5.396s |
| `bls.industry_producer_prices` | `final_demand_ppi`, 2025–2026 | `SUCCEEDED` | — | 18 data rows；同上 | 0.990s |
| `bls.import_export_prices` | `import_all_commodities`, 2025–2026 | `SUCCEEDED` | — | 17 data rows；同上 | 2.587s |
| `bea.national_accounts` | NIPA `T10101`, line 1, Q, LAST5 | `FAILED` | `upstream_provider_error` | 0；BEA 返回业务错误：`Error retrieving NIPA data.` | 2.840s |
| `bea.industry_accounts` | `GDPByIndustry`, `SQGDP9N`, line 1, Q, LAST5 | `FAILED` | `upstream_provider_error` | 0；BEA 指出该 dataset 缺少所需参数 | 2.331s |
| `census.manufacturing_orders` | NAICS 334, `new_orders`, from 2024-01 | `FAILED` | `tool_execution_failed` | 0；Census 返回 302 至 `missing_key` 页面，非 JSON | 5.081s |
| `eia.energy_prices` | `retail_gasoline`, start=2025-01-01, limit=3 | `FAILED` | `empty_result` | 0；`failed_metrics`，精确 route/facet 调用为 HTTP 200 但无 data rows | 1.882s |
| `eia.energy_supply_operations` | `us_crude_oil_inventory`, start=2025-01-01, limit=3 | `SUCCEEDED` | — | 3 records；`records`, `failed_metrics`, `source_coordinates` | 1.831s |

## 验收期发现与一次性修正

1. **BEA error envelope**：BEA 会把业务错误放在 `BEAAPI.Results.Error`，不仅是 `BEAAPI.Error`。已修正 provider 检测逻辑；现在不会把该情况误报为 `empty_result`。
2. **EIA route 治理**：EIA 的广泛 route 查询缺少 series facet 时返回 HTTP 400。已把可调用 registry 收束到含固定 facet 的具体业务指标，并移除空的 `start` / `end` query 参数：
   - 价格：美国常规零售汽油；当前请求可达但返回 0 行，保持 `FAILED`，未把它标为可用。
   - 供给：美国商业原油库存；真实返回 3 行，`SUCCEEDED`。
3. **Census key 边界**：当前 `DATA_GOV_API_KEY` 未被 Census M3 接受，服务将请求重定向为 `missing_key`。代码现已增加独立 `CENSUS_API_KEY` 配置位，并保留 `DATA_GOV_API_KEY` 兼容回退；本地仍缺经 Census 注册的 key，不能将配置位存在误标为 Census 已授权。
4. **BEA 参数治理缺口**：NIPA 与 GDPByIndustry 均未获得数据。后续应先用 BEA metadata endpoints 固化 `TableID` / `Industry` / 其他 dataset 专有必填参数，再开放更多 industry aliases；当前不得将 BEA tool 标记为 production-ready。

## 结论

- 本轮真实可用：FRED 4/4、BLS 3/3、EIA 供给 1/2，共 **8/12**。
- 真实失败并已保留明确错误语义：BEA 2、Census M3 1、EIA 价格 1，共 **4/12**。
- `EIA energy_prices`、BEA 与 Census M3 应保持 `tested_failed` / `entitlement_or_parameter_blocked`，直到完成对应的 key 或 metadata 路由补全并另行验收。

## 本地回归

```text
uv run pytest -q tests/test_macro_industry_semantic_tools.py
12 passed

uv run ruff check src/doxagent/tools/providers/macro_industry.py tests/test_macro_industry_semantic_tools.py
All checks passed
```
