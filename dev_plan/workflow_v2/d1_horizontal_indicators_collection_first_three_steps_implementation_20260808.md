# D1 Horizontal Indicators Collection 前三步实施记录

日期：2026-08-08

## 结论

`d1_horizontal_indicators_collection.md` 的实际开发顺序前三步已经落地；本轮同时完成 Document 1 / Document 2 方案中 43 个非派生 semantic tools 的实现或现有工具接线，并完成逐 tool、无 LLM 的真实只读验收。

真实结果合计为：

| 结果 | 数量 | 解释 |
|---|---:|---|
| SUCCEEDED | 27 | 真实 provider 调用返回可用结构化结果 |
| PARTIAL | 3 | 保留可用子结果或所选文件中只命中部分目标章节 |
| FAILED / BLOCKED | 13 | 凭证、entitlement、Gateway、参数治理或真实空结果未通过 |
| 合计 | 43 | 不含任何派生指标 tool |

上述结果只证明单 tool 的当前检索能力，不等于第四至第七步的批量采集、稳定性、Agent 归一和 Workflow 接线已经完成。

## 第一步：冻结核心契约

实现位置：`src/doxagent/horizontal_collection/schema.py`、`artifacts.py`。

- `MetricDefinition` 与 entity-scoped StateParameter 的身份字段已固定。
- Collection Target 使用独立 target ID，路由键不再混入 PROGRAM / AGENT / UNAVAILABLE 可用性状态。
- `CollectionObservation` 与现有 ReAct Observation 分离。
- `ObjectRef` 支持 `REALIZATION_FACTOR`，并保留 provider locator 过渡字段。
- `StateValueCurrentKey` 至少包含 parameter、source role、time scope。
- Manifest 以 collection target 为粒度表达 succeeded / partial / empty / failed / blocked，并持久化为 Blackboard working-memory artifact；不会伪装成 belief document 或 StateValue。

## 第二步：建立 Metric / Collection Target Registry

实现位置：`scripts/build_horizontal_metric_catalog.py`、`src/doxagent/horizontal_collection/generated_metric_catalog.py`、`registry.py`。

- 从治理文档生成 304 个唯一 metric IDs，并以测试防止文档与代码目录漂移。
- 固定 Target Registry 当前含 38 个 target，覆盖 30 个固定必填 canonical metrics。
- 同一 metric 可以按 source role、time scope 拆成多个 target，例如 actual / management / sell-side revenue。
- “主要远期估值倍数”使用受治理候选 metric IDs 路由，避免创建文档中不存在的 `market_primary_forward_multiple`。
- 派生指标保持 UNAVAILABLE / BLOCKED，本轮未实现计算器。

## 第三步：调查并接入数据源和 Tools

中央注册位置：`src/doxagent/tools/factory.py`；Agent allowlist 接线：`src/doxagent/agents/config.py`。

| 数据源组 | Tools | 真实验收 |
|---|---:|---|
| FRED / BLS / BEA / Census M3 / EIA | 12 | 8 succeeded，4 failed |
| SEC / USAspending / SAM / Regulations.gov / Federal Register / Congress / openFDA / issuer IR | 15 | 13 succeeded，2 partial |
| IBKR / Benzinga / FMP / Twelve Data / Finnhub | 16 | 6 succeeded，1 partial，9 failed/blocked |

真实调用期间修正了 BEA error envelope、EIA route/facet、USAspending detail ID 串联、Regulations.gov 分页下限，以及 issuer IR 的显式 HTML user-agent 与结构化链接提取。

当前不得标为 production-ready 的主要边界：

- Benzinga 缺本地 API key，5 个工具在 HTTP 前阻断。
- IBKR 缺已认证 Gateway/session，3 个工具均认证失败。
- Census M3 需要独立、有效的 `CENSUS_API_KEY`；代码已提供配置位，本地尚未配置。
- BEA 两个 semantic tools 已能正确识别 provider 业务错误，但参数 metadata 治理未通过真实验收。
- EIA price route 可达但真实返回空数据；只保留失败结果。
- FMP transcript fallback 单次返回 upstream provider error，需要 entitlement/季次另行确认。

## 凭证与秘密管理

- `.env.example` 只记录变量名与非敏感默认 URL。
- 本轮收到且可用的 provider keys 写入 git 忽略的 `.env.providers.local`；Settings 按 `.env`、`.env.providers.local` 顺序读取。
- 未在代码、测试、验收报告或日志中记录任何 key 或 raw provider payload。
- 因原始 key 曾出现在用户提供的图片中，建议后续轮换相关 key。

## 验收证据

- `eval/horizontal_collection_real_acceptance_macro_industry_20260808.md`
- `eval/horizontal_collection_real_acceptance_public_regulatory_20260808.md`
- `eval/horizontal_collection_real_acceptance_commercial_20260808.md`
- Provider/契约回归：`tests/test_horizontal_collection_contracts.py` 与三个 provider test modules。
