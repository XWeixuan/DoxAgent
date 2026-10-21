# 收益／成本审计模块现状勘察与统计口径审查

日期：2026-10-21，Asia/Shanghai。审查基线：本地 HEAD `2f9ddcce58b3e0f4a5d766449993c667ae08f5d9`。

本轮交付为代码审查、生产只读对账和修复建议；没有修改业务代码、数据库、生产配置，没有重启、构建或部署。现有其他任务的工作区修改未纳入本次交付。所有生产 SQLite 连接使用 `mode=ro` 与 `PRAGMA query_only=ON`。

## 1. 结论

用户提出的四项体验问题均有实现或数据层面的依据，但不能归结为一个原因：

1. **“统计不完整”既有固定返回行为，也有真实缺失。** 当前成本聚合没有产生 COMPLETE 的正常路径，即使样本字段全部完整也返回 PARTIAL；与此同时，样本中确有 91 条 API 调用回执没有 usage，不能用修正告警掩盖真实未知。
2. **Runtime 有 round，成本投影主动丢弃 round。** 原始记录足以区分 W1R1／W1R2／W2R1，API 的成本维度却只保存 W1／W2，后续 API 与页面无法从汇总桶可靠拆回。
3. **模型统计不清并非只由 round 丢失造成。** 当前已有模型筛选和按模型占比，但节点行把多个模型的用量合并；新模型未计价、OpenRouter 原始费用和缓存写入数据丢失、前端筛选仅影响明细，是另外几条独立问题。
4. **页面与 API 从路由、视图到 SQL 都绑定单 ticker。** 全部 ticker 聚合需要改变模块入口与统计范围，不是把 ticker 参数设为空就能实现。

生产样本的重要区别：**本次没有发现已持久化的 Runtime API 回执漏投影。** 原始账本与读模型的 2,121 个调用身份集合完全相同，已记录 input/output/cache-read token 合计也相同。可量化的缺口主要在上游 usage 缺失和计价覆盖，而不是这批 token 被聚合 SQL 漏掉。

当前各 ticker 的页面成本合计约 **$0.883194**。对已有完整 usage 的调用，补入可核实的新模型价格／OpenRouter 费用后，可核实成本约 **$2.534239**，相差 **$1.651046，约 65.15%**。这个比较仅针对本轮 API 样本，并统一使用产品固定汇率 6.8；**仍不包含 91 条未知 usage 的潜在费用，也不是供应商最终结算账单**。

## 2. 取样范围与复核方法

### 2.1 “上一交易日”的准确边界

生产检查时为北京时间 2026-10-21 19:00 左右。按当前 COST 页的 PREVIOUS_TRADING_DAY 口径，上一交易日为 **2026-10-20**。

| 表达方式 | 半开窗口，包含起点、不包含终点 |
| --- | --- |
| 业务语义日 | 2026-10-20 02:00 ～ 2026-10-21 02:00，America/New_York |
| UTC | 2026-10-20 06:00 ～ 2026-10-21 06:00 |
| 北京时间 | 2026-10-20 14:00 ～ 2026-10-21 14:00 |

这不是美股 09:30～16:00 的常规交易时段，也不是北京时间自然日。按调用 `started_at` 归属；缺少 started_at 时现有投影回退到 created_at。不能按 Case.trading_date 或回执落库时间替代。

本次扫描的调用候选 created_at 范围为 10/19～10/22，再以 started_at 精确筛选上述窗口；原始 RuntimeModelTurn 的 created_at 在调用结束记账时产生。本次样本中有 74 条调用所属 Case 的 trading_date 不是 10/20，包含更早 Case 的重试与后续执行；按 Case 日筛选会造成另一种遗漏。

### 2.2 数据来源和证据

- 原始账本：生产 `/data/runtime/runtime.sqlite3` 的 `runtime_v2_turns`，用 case_id 关联 ticker；历史总回执 32,202 条，本次时间候选 5,250 条，窗口内 2,121 条。
- 页面读模型：`/data/read/serving-v4-20261018/v2.sqlite3` 的 `object_current(kind='usage')`、`metric_buckets_current`。
- Runtime、Research、Initialization、Bus 的 source_health 在对账快照均为 checkpoint=head，且 error 为空。只能证明已捕获事件投影已追平，不能证明供应商侧所有实际调用都留下了 usage。
- 另查初始化账本 `v2_model_invocations`：历史共 5,919 条，本窗口没有记录；因此不能把本窗口 Runtime API 样本直接宣称为“所有研究、初始化和外部工具的全平台总成本”。
- 生产 `cost.py`、`usage.py`、`runtime.py`、`api_usage.py`、`formal.py`、`views.py` 的内容哈希与本地一致，比较时统一 LF，排除 Windows CRLF 差异。

证据目录：[eval/cost_audit_20261021](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/eval/cost_audit_20261021)。`sample.jsonl` 保存所需 token、调用身份和供应商 usage 元数据，没有抽取提示词、新闻正文、模型回答或凭据；`turn_usage.csv` 可逐调用复核，`analyze.py`、`summary.py` 可重新计算。

## 3. 当前统计链路

```text
Runtime API
  RuntimeModelTurn → runtime_v2_turns
    → SourceOutbox → DomainProjectors → runtime.turn_records
    → usage 对象 + observations() 贡献

初始化 API
  usage_capture → 持久化 spool → v2_model_invocations
    → FormalProjectors → api_usage.project（要求 control_operation_id）
    → usage 对象 + observations() 贡献

Codex
  Worker job telemetry.observed_usage / usage_complete
    → 已准入的 runtime_values.worker_invocations 或初始化节点回执
    → worker_records / worker_usage
    → usage 对象 + observations() 贡献

贡献 → metric_buckets_current / history
  → Costs 的 summary / trend / breakdown / filters / nodes
  → /ticker/:ticker/audit 页面
```

共享维度目前只有 `scope, node, provider, model`，没有 Runtime round、缓存写入 token、供应商已报费用、实际上游 provider 或逐调用价格版本。

项目还有独立的旧 `model_usage` 统计系统和价格表；当前 V2 COST 接口没有读取它。旧表默认折扣率 0.45、价格模型和缓存处理也不同，不能把它直接接到 V2 合计后视为统一真值。

## 4. 代码审查发现

### F01 · P1：完整性状态没有依据真实证据收口

位置：[cost.py:69](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/api_v2/cost.py:69)、[cost.py:88](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/api_v2/cost.py:88)、[dto.py:72](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/api_v2/dto.py:72)。

- `coverage(count=requests)` 没有设置 complete，默认始终 PARTIAL。
- 即使字段样本数等于 requests，`reasons or ['SOURCE_GAP']` 仍强制给出 SOURCE_GAP。
- trend、breakdown 同样仅传 count，完整性也不能自动变成 COMPLETE。
- cache_hit_ratio 调用通用 metric() 时，读取并不存在的 `cache_hit_ratio_samples`；之后只修正数值，没有修正 coverage。因此命中率可以是 AVAILABLE，coverage 却显示 known_count=0／NOT_RECORDED。
- 前端 [CoverageNote](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/frontend/v2/src/pages/overview/metrics.tsx:78) 正常展示后端 PARTIAL，主要问题不在这个徽标组件。

可复现结果：只给一条完全观测的调用，input coverage 仍 PARTIAL/SOURCE_GAP；缓存比例为已知的 0，但其 coverage 为 PARTIAL/NOT_RECORDED、known_count=0。生产 CEG 的 13 条调用 input 样本完整，仍返回 SOURCE_GAP。见 `reproduction.json` 和 `verify.jsonl`。

修复时应区分“源采集完整性、已捕获事件投影完整性、字段完整性、价格覆盖、时间窗是否结束”，不能简单把全部标记改为 COMPLETE。

### F02 · P1：新模型／新渠道没有进入当前计价规则

位置：[usage.py:21](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/v2_read/usage.py:21)、[usage.py:81](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/v2_read/usage.py:81)。

当前只支持精确 ID `qwen3.8-flash` 与 `deepseek-v4-flash-0731`；observations() 又只为 `provider='bailian'` 计价。

样本中：

| 计价排除原因 | 条数 | 含义 |
| --- | ---: | --- |
| OpenRouter Haiku 5.5，usage 完整 | 1,150 | W1R1 575、W2R1 575，全部未计价；原始供应商费用可核实 |
| DeepSeek v4.1 Flash，usage 完整 | 39 | 精确模型 ID 未配置价格 |
| usage 缺失 | 91 | 包括上述 DeepSeek 的另 5 条，不能当成零费用 |
| 合计未计价 | **1,280** | 另外 **841** 条可以按旧产品口径计价 |

旧契约明确要求未知模型不套用相似模型价格，这项保护是对的；问题是模型路由升级后价格覆盖没有同步演进。不能用老 DeepSeek 0731 的价格冒充 v4.1，也不能给 OpenRouter 套用百炼价格。

### F03 · P1：round 在成本投影时丢失

位置：[runtime.py:81](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/v2_read/runtime.py:81)、[runtime.py:112](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/v2_read/runtime.py:112)。

attempt 对象保留 `node_id=lane`、`round=round_name`；但 usage 对象和贡献只写 `node=lane`。同一个 W1 节点合并 R1/R2/R3，W2 同样合并多个 round。请求 node_id=W1R2 不能从现有汇总桶查出对应数据。

补救需从原始 turn_id 回溯重建 round 维度，不能根据模型猜 round。本次 W2R1 同时实际使用 Haiku、DeepSeek 和 Qwen，已经证明“一个 round 对应一个模型”的静态假设不成立。

### F04 · P1：节点明细合并模型，只返回模型名称集合

位置：[cost.py:234](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/api_v2/cost.py:234)、[CostNodeRow 契约](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/dev_plan/workflow_v2/api_contract/doxagent-v2-api.types.ts:897)、[audit.tsx:325](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/frontend/v2/src/pages/audit.tsx:325)。

API 按 node 聚合 totals，再把 `(provider,model)` 放进 models 集合；页面将模型名称列在同一行，但 token、成本、命中率仍然是整节点合计。缺少可同时比较的 `round × provider × model` 行。

已有的模型筛选可以将节点行缩小到某模型，这一点不是完全缺失；但它不能代替完整的多模型对照表，也不能恢复 round。

### F05 · P1：前端筛选只作用于节点明细，违反整页筛选契约

位置：[audit.tsx:91](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/frontend/v2/src/pages/audit.tsx:91)、[audit.tsx:133](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/frontend/v2/src/pages/audit.tsx:133)。

summary、trend、breakdown 的 query string 只有 view_id/scope 等参数，没有当前 node/provider/model；只有 nodes 请求带这三个筛选。用户选中某模型后，上方汇总和图仍显示全节点／全模型总数，容易误认为是该模型的成本。

API 原本支持各模块带同一组筛选；旧契约 §12.1 也明确要求筛选影响本页全部模块。此项是独立的前端参数传递缺陷。filters 则应继续不受当前节点／模型选择限制，以免候选互相消失。

### F06 · P1：缓存写入、供应商费用和上游信息未投影

位置：[RuntimeModelTurn](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/persistent_runtime_v2/schema.py:514)、[OpenRouter receipt](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/persistent_runtime_v2/openrouter.py:250)、[runtime.py:104](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/v2_read/runtime.py:104)。

原始 turn 保存 `cache_write_input_tokens` 与 provider_usage，其中包括 `cost`、费用分项和 `response_provider`；成本投影只提取 input/output/cached_input 三项。

W2R1 Haiku 样本有 **1,769,343 个 cache-write token**，使用 1h TTL。这部分是 input 的子集、但计价倍率不同；只用 `(input-cached)*普通价` 会低估，不能修完 model 白名单就认为费用正确。账本中的 OpenRouter `cost` 也是已存在、但页面没有利用的费用证据。

当前“缓存命中比例”是 token 加权命中率 `Σcache_read/Σinput`，不是“命中的请求数/请求数”。这一公式应保留，并区分 cache-read、cache-write 和未知样本。reasoning token 是 output 子集，不再次相加收费。

### F07 · P1：上游有真实未知 usage，页面不能给出精确总实付

位置：[transport.py:196](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/persistent_runtime_v2/transport.py:196)、[usage.py:58](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/v2_read/usage.py:58)。

91 条缺失：85 条 provider_timeout、5 条 provider_request_failed、1 条 provider_server_error。异常发生时可能没有收到 provider usage；本地缺回执不等于供应商没有处理或没有扣费。

因此本报告金额是“已观测 token 的原价核算／OpenRouter 回执费用”，不能称作全部调用的精确结算额。后续需要在有 response/generation ID 时对账恢复，不能默认补 0，也不能通过只统计成功调用掩盖重试费用。

### F08 · P1：单 ticker 设计与统一财务视图不匹配

位置：[audit.tsx:42](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/frontend/v2/src/pages/audit.tsx:42)、[ticker-workspace.tsx:15](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/frontend/v2/src/pages/ticker-workspace.tsx:15)、[cost.py:40](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/api_v2/cost.py:40)。

前端使用 route ticker，read-context 绑定 ticker，API 路径带 ticker，SQL 强制 ticker=?。当前没有全 ticker 成本接口。

建议全局收益／成本审计为主入口，默认全部 ticker，单 ticker 作为筛选及旧链接兼容入口。全局范围应包含窗口内有费用的历史／已暂停／已移除 ticker，并保留 GLOBAL／未分配成本，不限于 Overview 当前活跃列表。共享研究费用应保留独立归属，不能未经规则平均摊给 ticker。

收益入口当前仅返回“暂未开放”，不应把成本页面聚合改造宣称为收益计算已实现。

### F09 · P2：文档之间已存在请求数／均值口径冲突

对比 [API 契约 §12.3](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/dev_plan/workflow_v2/DOXAGENT_V2_API_CONTRACT.md:647) 与 [Frontend Foundation Q2](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/dev_plan/workflow_v2/DOXAGENT_V2_FRONTEND_FOUNDATION.md:76)。

- 当前实现与旧 API 契约：requests 包含所有可证明调用；任一 total 缺失，平均每请求 token 即不可用。
- Foundation Q2 后续修正：页面请求数与均值按有效用量样本；同时要求后续同步 API。
- 目前两者未收口。W1R2 显示 634 次，但只有 585 条完整 usage，平均值不可用。

不建议悄悄改一个 requests 分母。应明确同时保存 `observed_attempts / usage_known_attempts / usage_unknown_attempts / priced_attempts`，UI 主请求数和均值选用经确认的定义，并将分母显示清楚。API attempt、Codex job/turn 的“请求”粒度也不完全等价，不能跨渠道直接比较均值。

### F10 · P2：价格版本、时间窗、覆盖与数据接入需要一起演进

- [CostSummary](/C:/Users/WEIXUANXIE/Desktop/DoxAgent/src/doxagent/api_v2/cost.py:196) 与 TypeScript 契约把 `frontend-fixed-20260907`、汇率 6.8 写成固定值；加入新模型、供应商回执价及历史价格版本需要同步契约和生成 schema。
- 当前 N 日 COST 采用 N 个交易日集合，周末调用不属于该集合；ALL 才包含这些日期。对全局财务审计，建议提供自然日／周／月或明确日期范围，保留交易日作为可选口径，不能将周末成本静默遗漏。
- API 初始化 usage 要有 control_operation_id，Codex usage 要有 admitted lineage。这些是业务统计边界，不能因做全局合计而直接移除；测试、共享研究、生产业务的真实费用应分域保留、避免重复合并。
- Research 的 codex_runtime_records 被捕获，并不自动等于已生成完整的成本 usage。当前成本用量主要由已准入 worker_invocations／初始化回执生成。需要按 C4/C1/C3/C5、O0/O1、CDECR、O2/O3/O4、W1/W2/W3 建立接入清单，不能以“已配置节点名字”代替账本覆盖证明。
- Codex 本窗口有 1,010 条已投影 usage：W3 890、d3_o3_maintain 88、event_library_o2_maintain 32；967 条原始 coverage=COMPLETE、43 条 PARTIAL。43 条都缺少完整 token：41 条 usage 为空对象，2 条含显式 None。复核必须同时识别“字段不存在”和“字段为 None”，不能只数 None 而漏掉空对象。另需保留 telemetry.usage_complete 作为采集证据，不能仅凭存在数值就认定任意未来样本完整。
- Codex 订阅用量没有逐调用 USD 账单，成本 NOT_APPLICABLE 是合理边界；不能拿公开 API 单价直接当作 Codex 实付。

## 5. 上一交易日 W2R1、W1R2 实际观测结果

### 5.1 全 ticker 合计

覆盖 BE、CEG、COHR、INTC、MU、NBIS、ONDS、RKLB、SNOW，共 9 个 ticker。包括失败重试中已经返回 usage 的调用，不以 status=OK 过滤。

| Round | 已记录调用尝试 | usage 完整／未知 | 已知 Input token | 已知 Output token | 已知 Cache-read token | 已知成本，按产品汇率折算 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| **W2R1** | 620 | **615／5** | **32,845,922** | **537,115** | **30,345,184** | **¥7.5057778 ≈ $1.10379085** |
| **W1R2** | 634 | **585／49** | **2,218,501** | **805,999** | **1,089,280** | **¥3.1885021 ≈ $0.46889737** |

**这些是可核实的用量与费用小计，下限意义明确；未知部分没有计作零。** W1R2 的 585 条已知 usage 中含 559 条 OK、19 条 PENDING_RETRY、7 条 FAILED。若只统计成功，会漏掉已有费用证据的 26 次失败／重试。

W2R1 的原币小计是 **$0.92792950 + ¥1.1958572**；表中只为与现有产品比较而按 `1 USD=6.8 CNY` 合并，不是实时汇率或银行卡结算价。

### 5.2 按真实模型拆分

| Round／模型 | 尝试数 | usage 完整数 | Input | Output | Cache-read | Cache-write | 已知费用，原币 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| W2R1 · OpenRouter Haiku 5.5 | 575 | 575 | 31,347,674 | 429,790 | 28,874,080 | 1,769,343 | **$0.92792950** |
| W2R1 · 百炼 DeepSeek v4.1 Flash | 44 | 39 | 1,490,974 | 106,849 | 1,471,104 | 未单列 | **¥1.1887528** |
| W2R1 · 百炼 Qwen3.8 Flash | 1 | 1 | 7,274 | 476 | 0 | 未单列 | **¥0.0071044** |
| W1R2 · 百炼 Qwen3.8 Flash | 634 | 585 | 2,218,501 | 805,999 | 1,089,280 | 未单列 | **¥3.1885021** |

已知样本 token 加权缓存命中率：W2R1 Haiku **92.1092%**，W2R1 DeepSeek **98.6673%**，W1R2 Qwen **49.0998%**。缺少 usage 的调用没有进入分子、分母，因此不能把这些比例宣称为全部调用的完整命中率。

### 5.3 计价公式与外部价格证据

公开价核对日期为 2026-10-21；本报告不套用旧 model_usage 默认 0.45 折扣，不推测免费额度、税费、充值手续费或账户优惠。

**Haiku 5.5**：本样本所有请求 input 均未超过 100,000 token。每百万 token 单价：普通输入 $0.10、缓存读取 $0.01、1h 缓存写入 $0.20、输出 $0.50；超过阈值需切换长上下文价，本样本未触发。价格依据：[Anthropic 官方模型页](https://platform.claude.com/docs/en/models/haiku-5-5/overview)、[OpenRouter 模型页](https://openrouter.ai/anthropic/claude-haiku-5.5/)。

```text
I = total input，R = cache read，W = cache write，O = output
USD = ((I - R - W) × 0.10 + R × 0.01 + W × 0.20 + O × 0.50) / 1,000,000
```

W2R1 的普通输入为 704,251 token。逐调用重算的费用与全部 575 条 provider_usage.cost 一致；另对 W1R1 575 条也完成核对，1,150 条样本无超过 $0.00000001 的差异。OpenRouter 回执费用比仅据网页推算具有更强的逐调用证据，但仍不等同于含账户优惠／充值费的最终支付金额。

**Qwen3.8 Flash**：已核对 scheduler 的百炼中国 endpoint，当前 session-cache 显式开关未设置且默认 False。使用普通输入 ¥0.8、缓存输入 ¥0.1、输出 ¥2.7／百万 token 的原价核算；服务器位于新加坡，不代表使用新加坡百炼计费地域。依据：[百炼官方 Qwen3.8 Flash 价格](https://help.aliyun.com/zh/model-studio/qwen3-8-flash)。

```text
W1R2 CNY = ((2,218,501 - 1,089,280) × 0.8
             + 1,089,280 × 0.1 + 805,999 × 2.7) / 1,000,000
           = 3.1885021
```

**DeepSeek v4.1 Flash**：39 条有 usage 的调用均在北京时间忙时，使用普通输入 ¥2、缓存输入 ¥0.2、输出 ¥8／百万 token；闲时为 22:00～次日 08:00，不能全天用同一倍率。依据：[百炼官方 DeepSeek v4.1 Flash 价格](https://help.aliyun.com/zh/model-studio/deepseek-v4-1-flash)。

```text
CNY = ((1,490,974 - 1,471,104) × 2
         + 1,471,104 × 0.2 + 106,849 × 8) / 1,000,000
    = 1.1887528
```

### 5.4 各 ticker 已知 token 交叉检查

| Ticker | W2R1 Input | W2R1 Output | W1R2 Input | W1R2 Output |
| --- | ---: | ---: | ---: | ---: |
| BE | 2,957,922 | 56,924 | 262,395 | 106,970 |
| CEG | 99,535 | 3,840 | 14,439 | 4,255 |
| COHR | 708,465 | 11,806 | 52,656 | 15,398 |
| INTC | 11,522,320 | 192,043 | 640,525 | 249,728 |
| MU | 13,198,648 | 155,872 | 729,311 | 246,472 |
| NBIS | 2,996,783 | 79,102 | 352,982 | 141,681 |
| ONDS | 167,846 | 1,602 | 11,911 | 1,637 |
| RKLB | 651,842 | 11,095 | 57,829 | 20,040 |
| SNOW | 542,561 | 24,831 | 96,453 | 19,818 |

## 6. 建议的修复边界与数据契约

本节是供下一轮开发的方案，不表示已经实现或上线。

### 6.1 统一事实层，避免再做一个并行总账

以稳定 invocation/attempt 身份记录真实调用；保留 channel、ticker／GLOBAL 归属、node、round、attempt/status、实际 model、计费 provider、上游 provider、started_at、各项 token、usage 完整性、供应商费用及其币种、价格版本／来源、价格计算结果。raw receipt 与计算值分开，未知不得变 0。

Runtime 的 call ID 直接复用 turn_id。已有投影需按原始记录回填 round、cache-write、provider cost，重放同一身份应替换贡献而非追加。原始记录已不可追溯的历史项保留 UNKNOWN_ROUND／未知价格，不能根据模型或节点推测分摊。

Codex job 累计与底层 response 记录必须沿现有稳定身份去重，保持订阅费用不适用的边界。共享研究和生产 ticker 调用明确归属，先保留 GLOBAL，不随意分摊。

### 6.2 全局默认，ticker 和 round/model 作为筛选

建议增加全局审计入口和 API，默认整个业务账本范围；可按 ticker、channel、node/round、provider/model、日期筛选。原 ticker 入口作为预选该 ticker 的兼容链接。

主明细粒度采用 `(channel, node, round, provider, model)`，需要 ticker 对照时再展开 ticker；既支持 W1/W2 父节点小计，也支持 W1R1、W1R2、W2R1 等叶子节点。KPI、趋势、占比与明细共享同一筛选与冻结视图。

不要让前端遍历 9 个 ticker 分别请求 5 组数据再相加。全局统计应在服务端按统一口径聚合，保留 MVCC 视图与 Decimal 精度。生产只读 Costs.totals 单 ticker 样本约数十毫秒；全局改造仍需压测分组查询、索引和读取池排队，不能从单 ticker 正常推断全局性能已通过。

### 6.3 完整性和展示

至少分开报告：observed attempts、usage-known、usage-unknown、priced、unpriced、已报费用样本和估价样本；返回具体原因及 observed_through。缓存比率在已知样本上可计算时注明分母覆盖，不能混同全部请求命中率。

“未观测到调用且源已完整覆盖”的 0、“未采到记录”的未知、“订阅费用不适用”、“模型价格未配置”应分别表达。breakdown 的分母仅有可计价成本时应说明排除条数，不能让 100% 占比看起来代表全部真实费用。

## 7. 验证结果与下一轮验收清单

本轮已做：

- 生产原始回执与读模型 ID 集合、input/output/cache-read 合计对账，一致；source checkpoint 检查通过。
- 使用正式容器 Costs.totals 直接读取生产冻结水位，复核 coverage 与未计价数；没有调用写操作、没有创建额外生产视图 token。
- 当前分支聚焦测试 `test_cost_api.py`、`test_usage.py`、`test_codex_turn_usage.py`：**12 passed**。这些通过仅说明既有测试未失败，不能证明上述审查问题不存在。
- 最小复现固定 PARTIAL、缓存比率覆盖错误、新模型未计价；OpenRouter 费用逐调用重算匹配。
- 本轮没有浏览器布局验收、没有业务代码修复、没有部署。

后续至少补齐：全字段完整可达 COMPLETE、真实零与未知区分、同节点多 round、多模型交叉行、OpenRouter 1h cache-write、超过 100k 的价格阈值、DeepSeek 忙闲切换、失败但有 usage 的计费、unknown usage 不充零、整页筛选一致、全部 ticker 含历史／GLOBAL、API/Codex 去重、重放回填幂等、源缺口与延迟清晰归因、全局查询在既有读取预算内完成。

## 8. 证据文件索引

| 文件 | 用途 |
| --- | --- |
| `eval/cost_audit_20261021/schema.jsonl` | 生产表结构、索引、源健康快照 |
| `sample.jsonl` | 原始调用元数据、usage 读模型与汇总桶快照 |
| `analysis.json` | 分 round/model、缺失与价格对账 |
| `summary.json` | W2R1／W1R2 与 API 总体核算结果 |
| `turn_usage.csv` | 2,121 条调用的逐项复核表 |
| `verify.jsonl` | 正式容器 Costs.totals 输出、源码哈希与公开 endpoint 配置 |
| `scheduler-config.jsonl` | 实际执行容器的公开 endpoint／缓存开关核对 |
| `other.jsonl` | 初始化／研究独立账本存在性与窗口计数 |
| `reproduction.json` | 完整性及新模型未计价最小复现 |
| `analyze.py`、`summary.py`、`reproduce.py` | 本地可重算脚本 |

金额和统计结论均以本次取证时点为准；后续补录供应商回执或修复投影后，应沿用同一窗口和 ID 对账重新核算。
