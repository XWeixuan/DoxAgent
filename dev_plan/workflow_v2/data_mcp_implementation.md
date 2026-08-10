# Data MCP 实现与验收记录

日期：2026-08-09  
适用工作流：`codex_d1_v2`  
设计基线：`data_mcp_development.md`

## 1. 结论

Data MCP 的运行时代码、Codex worker 接线、共享 Observation 生命周期、Source Capture 共用 O#、权限治理、bounded delivery、cited-only promotion、测试与本地真实 provider 验收已经落地。旧 `ToolRegistry.call()`、旧 Agent allowlist 和 Legacy ReAct 路径保持可直接调用，没有被 MCP 替代。

当前已知不可用能力仍按真实状态保留：Benzinga 与 FMP entitlement 不足。IBKR 已在本机通过官方 TWS socket 接入；只有 `IBKR_TWS_ENABLED=true` 的环境才暴露三个 `ibkr.*` 工具。合约和历史行情真实可用，当前账户会话的 snapshot 报价流返回零 tick，因此按 `market_data_unavailable/degraded` 处理。已移除的 `finnhub.fund_ownership` 没有重新注册。

## 2. 已实现模块

| 能力 | 实现位置 | 结果 |
|---|---|---|
| 版本化 Data Tool / launch / result contract | `src/doxagent/data_runtime/contracts.py` | 76 个当前 registry 工具均生成唯一 canonical id、MCP name 和有效 JSON Schema；静态不可用与 read-only exposure 分离 |
| 三层权限和 capability | `src/doxagent/data_runtime/policy.py` | Ed25519 短期 token 绑定 workflow/run/node/attempt/role/ticker/cutoff/tool set/expiry；服务端再与静态策略求交集 |
| 确定性工具指引 | `src/doxagent/data_runtime/guidance.py` | `data_tool_guide` 最多返回 5 个已授权候选，并显示 required input、fallback、PIT 风险和 unavailable gap；不调用 provider 或 LLM |
| 共享执行核心 | `src/doxagent/data_runtime/execution.py` | 继续调用同一 `ToolRegistry.call()`，注入 ticker、Agent、cutoff 和审计上下文；区分 execution status 与 availability |
| 独立清洗和分段 | `src/doxagent/observations/profiles.py`、`segmenter.py`、`kernel.py` | raw 只进私有审计；provider wrapper、debug、重复 metadata、空字段不进正文；记录、表格、时间序列和长文本确定性分段，不依赖 ReAct runtime |
| attempt O# 和审计 | `src/doxagent/observations/store.py` | SQLite `BEGIN IMMEDIATE` 并发分配；Data MCP、Source Capture、主 Agent 与 Sub-agent 共用 attempt-local O#；记录 latency/payload/inline/pack bytes |
| 大结果 Pack | `src/doxagent/observations/pack.py` | 同卷临时目录原子 rename，`manifest/selected/catalog/blocks` 完整落盘；selected 正文不超过 12,000 字符；兼容 `data_read_observation` 精简回读 |
| cited-only promotion | `src/doxagent/observations/promotion.py`、D1 orchestrator | 节点 report 完成时按 attempt 解析 `【cite:O#】`，仅提升被引用块；最终 Document 1 合并带 anchor/attempt 的 manifests，允许不同 attempt 各自拥有 O1 |
| MCP Adapter | `src/doxagent/mcp/data_server.py` | MCP 2.0 stdio `tools/list/call`；每个 semantic tool 独立展示业务用途和输入 schema；无通用任意 endpoint 调用器 |
| Source Capture 合流 | `src/doxagent/mcp/source_capture_server.py` | 抓取器保持独立权限，但 cleaned content 进入同一 Observation Store 和 O# sequence |
| Worker / SDK 接线 | `src/doxagent/codex_worker/sdk_runtime.py`、`app.py` | Data MCP 为 required stdio server；Source Capture 为 optional；每 attempt 独立 control root 和 enabled tools |
| 私有权威与 retention | worker Workspace API / store | promotion 从 Agent 不可写的私有 SQLite 读取 canonical block，再校验 workspace mirror/Pack；attempt 删除同时清理 workspace 和 `.control` |

## 3. 设计偏差与原因

1. 设计稿写的是签名 capability file；实现改为 worker 通过 stdio process env 注入短期 Ed25519 token，MCP 仅持有公钥。这样无需在 Codex 可见 workspace 投影 capability 文件，同时保留签名、audience、expiry 和 scope 校验。
2. Windows 的只读位不能作为唯一安全边界。实现把 `.control/<run>/<attempt>/observations.sqlite3` 作为权威，workspace mirror 和 Pack 只是只读投影；promotion 会把投影与私有 canonical 内容逐块比较，篡改只产生非阻塞 warning，不提升来源。
3. 设计时 Source Capture 只冻结接口；本次为消除两套 O# 分配器，已把现有 Source Capture MCP 接到统一 Kernel，但没有新增或扩大抓取器能力。
4. `tools/list` 不重复携带每个工具相同的大型 output schema，避免把发现 payload 放大；稳定输出契约仍由 `data_mcp_result/1.0`、服务端模型与测试强制。

## 4. 权限与暴露结果

当前 real registry 共 76 个 contracts；在本机启用 TWS 的配置下，全局满足 read-only、concurrent-safe、非静态 unavailable 的为 66 个，O4 可见 7 个 semantic tools（含 3 个 IBKR），另加 guide/read 两个公共只读工具。未启用 TWS 的环境仍回到原有 unavailable 状态。计数不是新的授权来源，最终仍以服务端策略、签名 capability 和 Codex `enabled_tools` 三层交集为准。

control root 必须精确等于 worker 约定的 `.control/<run>/<attempt>`；run cwd、attempt、role 或签名任一不一致，server 直接拒绝启动。Agent 不能通过普通 tool input 覆盖 ticker、run、node、attempt、cutoff 或 agent identity。

## 5. 结果质量与真实调用

已有 2026-08-08 全部非 IBKR 数据工具真实调用结果继续作为 provider 基线，详见 `eval/horizontal_collection_tool_result_quality_acceptance_20260808.md`。本轮没有发起 LLM request，只对新 MCP 路径做真实 provider 调用：

| 验收 | 实际结果 |
|---|---|
| MCP discovery | `mcporter` 和 MCP Python Client 均完成 initialize/list/call；O4 列表只含 guide/read、Finnhub insider、Twelve Data 两项和 yfinance，未暴露 Benzinga/FMP/IBKR |
| SEC `issuer_filings` / MU | succeeded/available；返回公司身份与 3 条 filing 表，共 2 个 O#、788 个 inline 字符；清洗前的 provider、CIK 和 source-coordinate 独立块已移除，来源坐标附到两个业务块 |
| FRED `activity_demand` | succeeded/available；返回 real GDP、real PCE、工业生产、零售和耐用品订单的小窗口序列 |
| Federal Register / `Micron Technology` | succeeded/available；5 条相关完整记录对应 5 个 O#，不再按字段碎裂 |
| Federal Register / `semiconductor` 大结果 | succeeded/available；20 个完整记录块，delivery=`pack`，只内联 7 块/11,704 字符，manifest、selected、catalog 均存在 |
| yfinance `daily_ohlcv` / MU | succeeded/available；真实 5 日 OHLCV 与 market evidence snapshot；wrapper 字段转入 envelope |
| 精确回读 | `data_read_observation` 可跨 stdio process 从同一私有 attempt store 完整读取指定 O#，不返回 raw payload |

### 2026-08-10 IBKR 本机增量验收

- 官方 `ibapi 10.49.2`、TWS `127.0.0.1:7496`、server version 225 握手成功。
- MU 合约解析为 `conId=9939 / SMART / NASDAQ / USD`；一个月日线返回 22 条。
- 新 attempt 的真实 stdio MCP `initialize/list/call` 通过，工具列表为 guide/read 加三个 IBKR；contract/history 各只暴露一个高价值 O#，分别为 table 和 time_series。
- snapshot、delayed-frozen snapshot 与 streaming 均零 tick、无请求级拒绝码；不使用历史 close 冒充 snapshot，返回 `market_data_unavailable` 且 availability 为 `degraded`。
- 本轮 `mcporter` 在 Windows 下能启动 executable，但其 stdio 配置没有应用 attempt cwd，因而被 Data MCP 的 cwd/run_id 安全校验拒绝；没有放宽校验。最终 provider 调用验收使用 MCP Python client 完成，不涉及 LLM。

SEC 样本的 Agent 可见块由旧通用分段的 5 个降为 2 个，减少 60%，但私有 raw 审计仍完整保留。Federal Register 大结果确认 Windows 目录原子切换和只读 retention 均可用。

## 6. 自动化验收

- `tests/test_data_mcp_runtime.py` 覆盖 contracts、已知 unavailable、Finnhub removal、签名/篡改/role scope、并发 O#、Data/Source Capture 共用 alias、私有 canonical/retention、ticker 注入、secret redaction、large Pack、cited-only promotion 和真实 stdio 协议。
- Codex D1、Workspace、horizontal collection 和 Evidence/annotation 相关回归通过。
- Data MCP/Observation/Codex worker/D1 模块 Ruff 与 strict mypy 通过。
- `cryptography` 已成为显式 runtime dependency，`types-jsonschema` 成为 dev dependency；lockfile 已更新。
- 排除 real API/DB/LLM markers 的全仓 `pytest` 在 6 分钟命令上限内未完成且没有给出具体失败，因此不计为通过；本报告只把上述 29 项重点回归记为通过。

## 7. 尚未执行的外部验收

代码和无 LLM MCP/provider 验收已完成，但设计门槛第 10 项所述“真实 Codex D1 node 自主选工具、读 Pack、引用并 publish”本轮没有执行，因为沿用此前不发起真实 LLM request 的边界。现有 fake D1 DAG、真实 MCP/provider、promotion/publish 分项均通过，不能把它们冒充真实模型端到端质量认证。

远端容器部署、真实 worker volume、网络策略和数据库迁移也未在本轮执行。启用生产流量前应在目标环境运行一个受控 ticker/node 的真实 Codex 垂直切片，并检查最终 Citation Manifest 的 attempt/anchor/source checksum。

## 8. 回滚

关闭 `DOXAGENT_CODEX_D1_V2_ENABLED` 或停用 codex-worker profile 即可回到旧 workflow。Data MCP 没有替换 provider clients、Tool Registry 或旧 ReAct 入口；无需删除 Observation 数据或回滚 provider 工具。
