# 成本审计修复执行方案：全标的聚合、逐轮模型统计与缓存计价

日期：2026-10-21。状态：**待实施的执行方案**，本文件不代表代码已修复或模型已切换。

依据：[现状勘察与统计口径审查](cost_audit_current_state_review_20261021.md)、`eval/cost_audit_20261021/` 的生产只读样本，以及本轮核实的供应商官方文档。代码审查基线为 `2f9ddcce58b3e0f4a5d766449993c667ae08f5d9`；实施时先比较最新 main，保留其他任务的工作区改动。

将需求中的“过度设计”按上下文理解为“不过度设计”。执行原则：一次修通事实、投影、API 和页面；复用已有 SQLite 与增量贡献机制；不另建财务平台，不增加常驻扫描任务。模型接入提前适配，但与正式模型切换分开交付。

## 1. 交付范围与明确决策

| 项目 | 本方案决定 | 完成标准 |
| --- | --- | --- |
| 统计不完整 | 修正固定 PARTIAL，同时保留真实缺失 | 完整样本可 COMPLETE；91 条未知 usage 不被充零 |
| 节点粒度 | 保留原 node/lane，补 round 维度；页面显示 W1R1 等叶子 | 同一 W2R1 下不同模型、渠道分行，父级可小计 |
| 全标的 | 增加全局成本入口，默认 ALL；ticker 是可选筛选 | 包含已停用／移除 ticker 的保留账本及共享研究 GLOBAL |
| 筛选一致 | KPI、趋势、占比、明细使用同一冻结视图和筛选对象 | 不再出现筛选只改变明细 |
| 成本 | 原始费用与公开价格计算值分开保存，展示明确依据 | OpenRouter 有有效费用则采用；其余按真实渠道规则计价 |
| 缓存 | 分开普通输入、读取、创建；创建支持 5m/1h | Vertex、OpenRouter、百炼分别归一化，不能一套字段硬套 |
| 新模型 | 适配 Vertex Haiku 5.5、百炼 GLM-5.3-FlashX | 离线可验证，生产 profile 默认不激活 |
| 收益 | 保留当前尚未开放的状态 | 本轮不混入 PnL、账户收益率或订阅成本分摊 |

成本审计主线必须独立完成，不能等待 Vertex 项目凭据或未来模型切换。也不能只隐藏“不完整”提示、延长查询超时、增加 worker 或前端循环汇总 ticker 来交差。

## 2. 统计契约先定清楚

### 2.1 账本范围、时间和唯一身份

1. 默认范围为已接入的**正式生产调用与共享研究调用**；历史已移除 ticker 仍保留费用。测试／Pilot 不能因存在于同一源表就自动计入生产。无法证明生产归属的调用列为来源覆盖缺口，不猜归属。
2. ticker 采用显式选择：`ALL`、具体 ticker、`GLOBAL`。GLOBAL 表示实际共享研究所有权；不按 ticker 数量平均分摊，不为 ALL 再写一份全局费用桶。
3. 时间归属为调用 `started_at`；仅历史缺失时回退 `created_at`，并标记回退来源。不要使用 Case.trading_date、完成时间或回执写入日替代。
4. 沿用 COST 当前交易日 ET 02:00 边界。上一交易日、近 N 交易日明确标注；非交易日的调用在“全部保留历史”内仍应出现。新加自然日功能必须使用新枚举，不能暗改 `TRADING_DAYS_7`。
5. Runtime 使用现有 turn_id；其他来源复用现有 invocation/job 稳定身份。一次网络尝试一条事实，业务失败但 usage 已返回也记账。SDK 不偷偷重试以免一次记录包住多次付费调用。
6. Codex 累计 job 与底层 response 不能重复计数；沿现有身份选择唯一账本层级。订阅无逐次可归属费用，USD 为 NOT_APPLICABLE，不能填零或套 API 价格。

### 2.2 请求数与覆盖，不再混用一个分母

返回以下独立计数，均在当前筛选与冻结水位内计算：

| 字段 | 含义 |
| --- | --- |
| observed_attempts | 已持久化并被纳入账本的全部尝试，含失败、超时 |
| usage_known_attempts | 输入、输出完整且合法的调用数；缓存字段另有字段覆盖 |
| usage_unknown_attempts | observed_attempts 减 usage_known_attempts |
| priced_attempts / unpriced_attempts | 有可采信费用／可完整计算费用的调用数及其补集 |
| reported_cost_attempts / estimated_cost_attempts | 有供应商已报费用／使用公开价格计算的调用数 |
| field.known_count | 该字段实际已知的调用数，已知零也计样本 |
| observed_through / source_coverage | 投影水位及来源覆盖，不等价于 usage 全部完整 |

统一现行文档冲突：页面主“请求数”使用有效 usage 样本数，同时紧邻显示“已观测 N／缺 usage M”；均值明确为“每个有效样本平均 token”，分母为 total_tokens 已知样本数。API 同时返回全部尝试数，不把旧 `requests` 名字静默改义；旧接口保留原语义，新报告使用上述明确字段。

每个字段的 coverage 独立计算：源追平且应有字段均已知时 COMPLETE；部分已知为 PARTIAL；全部未知为相应未知状态；无调用且来源覆盖可靠时允许真实 0。源未接入／有缺口时不能因为 `known_count == observed_attempts` 就宣布全平台完整。窗口未结束单独用 provisional 表示，不伪装成 SOURCE_GAP。

去掉 `reasons or ["SOURCE_GAP"]` 这种无证据兜底。原因使用实际事实，如 USAGE_NOT_RECORDED、UNKNOWN_MODEL_PRICE、CACHE_TTL_UNKNOWN、SOURCE_GAP、PROJECTION_LAG；具体枚举与 DTO 一起更新。保留有效部分，不因某个费用未知就抹掉已知输出 token。

### 2.3 缓存命中率

主缓存指标为**输入 token 命中率**，不是“命中请求占比”：

```text
cache_hit_ratio = Σ cache_read_input_tokens / Σ input_tokens_total
```

分子、分母只取两项同时已知的同一批调用；返回该批样本数与全部尝试数。分母为零显示无样本；缺缓存字段不能视为零。需要“命中调用占比”时另命名，不能复用此字段。缓存创建量和费用单列，不算命中。

## 3. 最小数据改造

### 3.1 在现有 usage 对象扩展，无新总账

原始回执仍为证据源；现有 `object_current(kind='usage')` 保存归一化字段，`contributions` 与 `metric_buckets_current/history` 继续承担增量聚合。新增字段建议如下，实施时沿已有命名做兼容映射：

```text
identity: existing usage id, source, source_id, source_revision
dimensions: ticker, channel, node, round, billing_provider, model
provenance: upstream_provider, endpoint, region, transport, execution_domain
time: started_at, attributed_at, attribution_basis
usage:
  input_tokens_total, input_tokens_uncached, output_tokens
  cache_read_input_tokens, cache_write_input_tokens
  cache_write_5m_input_tokens, cache_write_1h_input_tokens
  reasoning_tokens                         # 输出子集，不能再加进总输出
  per-field known/unknown + normalization_version
money:
  reported_cost_native, reported_currency
  calculated_cost_native, calculated_currency
  selected_cost_native, selected_cost_usd, cost_basis
  price_rule_id, price_version, fx_rate, fx_version
coverage: concrete reason codes, usage status
```

不再复制完整提示词、新闻正文或模型输出到成本对象。原始 provider_usage 已在原始回执中的，不在读模型复制整份大 JSON；只投影需要的数量、费用、币种、响应身份和路由元数据。

`billing_provider` 沿现有 provider 字段保留兼容映射。OpenRouter 上游即使是 Google，也仍由 OpenRouter 计费；Vertex 直连才是 vertex。百炼 GLM 为百炼计费、智谱上游，不归到智谱直连接口。

### 3.2 round 与聚合桶

`v2_read/runtime.py` 已有 lane/round 来源，usage 贡献新增 round；保持 node=W1/W2，不修改运行图节点身份。API 的 W2R1 映射为 node=W2、round=R1。历史无来源的记录用 UNKNOWN_ROUND，不能按模型名称猜 round。

桶维度仅保留有限枚举／模型：channel、node、round、provider、model，必要时加 cost_basis 两类；ticker/day 沿原表字段。调用 ID、错误堆栈、prompt hash、价格版本和 endpoint 详情不得进入桶维度，避免每个调用生成独立桶。

新增贡献只覆盖所需 token、字段样本数、尝试／计价计数、缓存分项及费用分项。比率与均值在读取时由可加总量计算，不累计单次比率。成本创建费纳入 input_cost，并与普通输入和缓存读取分开，不能重复加价。

现有桶同时维护实际 day 和 `day='*'`。有限时间窗读实际 day；旧接口仅取 ALL 总计时可读星号桶。新的组合 report 为同时生成历史趋势，在 ALL 模式只读取实际日桶并从同一数据求总计，不能把星号桶再加一次。全局查询跨 ticker 汇总已有桶，不另存 ALL-ticker 桶。

round 初版直接从 `dimensions` JSON 提取供分组与筛选；现有成本桶的 node/provider/model 列保留。不要直接在 SQL 引用尚不存在的 round 物理列。仅当 EXPLAIN／基准证明确有瓶颈，再补成本桶字段与对应 trigger 的小迁移；不调用用于空新库的 `serving_schema.install()` 重装生产 serving-v4。

### 3.3 覆盖全部真实调用来源

先列来源接入矩阵并用现有生产证据逐项确认：C4/C1/C3/C5、O0/O1、CDECR、O2/O3/O4、W1/W2/W3，注明 API／CODEX、原表、稳定身份、生产归属、投影入口、是否观测 usage。

修补确认遗漏的**调用回执接入**，不能删掉 `api_usage.project` 的 control_operation_id 或 Formal/DomainProjectors 的准入约束以“多计一些”。若同一调用经两条 outbox 到达，稳定身份去重。历史源已丢失的范围明确标为无法恢复，不为此部署新的全库巡检服务。

## 4. 计价规则与费用取值

新建轻量的版本化规则文件和纯函数计价模块，例如 `src/doxagent/v2_read/pricing_rules.json`、`pricing.py`；`usage.py` 调用它。启动时加载一次，不定时联网抓价格。规则键至少包含渠道、真实计费 provider、精确 model、地域／端点价格类别、适用时间；保留旧规则以支持历史。

金额从字符串构造 Decimal；中间不转 float、不逐调用四舍五入。原币保存，页面 USD 折算暂沿用固定 CNY/USD=6.8，并标为产品固定折算，保留版本。不要把旧 `model_usage/default_pricing.json` 的 0.45 折扣引入本模块，也不默认任何优惠、免费额度、税费或账单抵扣。

费用选择顺序：

1. 回执有合法的供应商费用、币种及明确语义时，采用 reported cost；OpenRouter 的总 cost 与 cost_details 不可相加，upstream inference cost 不能当成额外费用重复计入。
2. 无有效已报费用时，使用该渠道适用规则和已知 token 计算 estimated cost。返回价格来源、版本；计价覆盖完整也仍是公开价格估算，不声称最终实付。
3. 缺字段无法完整计算时，保留可核实的费用分项，完整总额保持未知／已知小计状态。不把 partial subtotal 命名为一次调用的完整总费。
4. reported 与 estimated 同时保存便于解释，但 selected 总额每次调用只取一个。供应商总费用已知但分项不可拆时，总费用可已知，分项未知。

立即补齐当前在用的 OpenRouter Haiku 和百炼 `deepseek-v4.1-flash`，保留原 qwen3.8-flash、deepseek-v4-flash-0731 规则。DeepSeek 忙闲按调用时间转北京时间选取，历史准确版本与起效日期依据勘察中的官方价格证据，不把新型号价格套到旧型号。

## 5. Vertex Claude：已确认支持创建和读取缓存

### 5.1 已核实事实与接入选择

Google 官方明确支持 Claude 显式 prompt cache；默认 5 分钟，也可 `cache_control: {"type":"ephemeral","ttl":"1h"}`。缓存属于 Google Cloud 项目，相同前缀和相同块上缓存标记才能复用。Haiku 5.5 不在 1h 不支持的旧型号列表中。采用**供应商侧缓存**，不在服务器本地复制一套 token／KV 缓存，不加周期预热或保活请求。[Google 缓存文档](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/partner-models/claude/prompt-caching)

官方模型 ID 为 `claude-haiku-5-5`；不能把 OpenRouter 的 `anthropic/claude-haiku-5.5` 直接作为 Vertex 模型路径。新增 profile 默认指向 global，实际启用时记录 Google project/location 与版本。[Google 模型页](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/partner-models/claude/haiku-5-5)

直连采用 Vertex 的 Anthropic 原生 `rawPredict`／`streamRawPredict` 协议，`anthropic_version="vertex-2023-10-16"`；不经 OpenRouter。使用现有 HTTP 库与轻量 google-auth/ADC，OAuth token 在内存按到期刷新；不逐调用启动 gcloud，也不为此引入整套训练／部署 SDK。凭据走现有 secret 管理，只读挂载，不进入 Case、回执、测试文件或日志。[Google 调用文档](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/partner-models/claude/use-claude)

拟定 global 请求地址：

```text
https://aiplatform.googleapis.com/v1/projects/{project}/locations/global/publishers/anthropic/models/claude-haiku-5-5:streamRawPredict
```

端点位置取实际配置，不把“服务器在新加坡”自动等同于 regional API。global 与 regional/multi-region 必须匹配不同价格规则。[Google 端点说明](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/partner-models/use-partner-models)

### 5.2 原生 usage 的准确归一化

Anthropic 原生 `usage.input_tokens` **只表示未读缓存、未创建缓存的普通输入**，不是总输入。读取以下字段：[Anthropic 官方 usage 定义](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)

| 原生字段 | 归一化字段 |
| --- | --- |
| input_tokens | input_tokens_uncached |
| cache_read_input_tokens | cache_read_input_tokens |
| cache_creation_input_tokens | cache_write_input_tokens |
| cache_creation.ephemeral_5m_input_tokens | cache_write_5m_input_tokens |
| cache_creation.ephemeral_1h_input_tokens | cache_write_1h_input_tokens |
| output_tokens | output_tokens |

```text
input_tokens_total = uncached + cache_read + cache_write
cache_write = cache_write_5m + cache_write_1h  # 分 TTL 字段存在时校验
total_tokens = input_tokens_total + output_tokens
cost = (uncached × P_input + read × P_read
      + write_5m × P_write_5m + write_1h × P_write_1h
      + output × P_output) / 1,000,000
```

OpenRouter 的 prompt_tokens 原本包含缓存读写，不能再次把 cache_write 加到 prompt_tokens；百炼输入通常也是总输入。按 transport/provider 选择归一化函数，禁止仅用字段名称判断含义。

若 Vertex 返回总创建量但未给 TTL 分项：仅在本请求所有缓存块 TTL 一致、冻结请求可证明 TTL 时归入该档，并记录 request-derived；混合 TTL 则总创建量已知、创建分项未知。没有返回且无协议证据不能 `get(...,0)`。SSE 中累计 usage 用最新值合并，不把 message_start 与后续累计数相加；断流保留已收到的输入／缓存与已知输出状态。

### 5.3 价格规则和边界

核对日 2026-10-21 的公开按量价格如下，单位 USD／百万 token；不含 Batch、折扣和最终账单调整。[Google 官方价格表](https://cloud.google.com/gemini-enterprise-agent-platform/generative-ai/pricing)

| 端点／输入档 | 普通输入 | 输出 | 5m 创建 | 1h 创建 | 命中读取 |
| --- | ---: | ---: | ---: | ---: | ---: |
| global，短上下文 | 0.10 | 0.50 | 0.125 | 0.20 | 0.01 |
| global，长上下文 | 0.50 | 2.50 | 0.625 | 1.00 | 0.05 |
| regional／multi-region，短上下文 | 0.11 | 0.55 | 0.1375 | 0.22 | 0.011 |
| regional／multi-region，长上下文 | 0.55 | 2.75 | 0.6875 | 1.10 | 0.055 |

档位依据整次输入含缓存的上下文长度，不按 uncached input 判断。官方表头 `≤100K` 与脚注 `≥100k` 在恰好 100,000 上存在文字冲突：实现先按更具体脚注将 `>=100000` 归长档，并在规则标注边界待账单／官方进一步确认；不能宣称该边界已实测。99999、100000、100001 都要有测试。

算例用于单测：普通输入 100、缓存读取 8,000、1h 创建 2,000、输出 500，总输入 10,100；global 短档费用为 `(100×.10 + 8000×.01 + 2000×.20 + 500×.50)/1e6 = $0.000740`。读写均有费用，创建费不是在普通输入价上再重复加一次。

### 5.4 缓存布局与验证

保留 W1R1/W2R1 当前稳定指令、完整研究／Policy 前缀、已知事件及 provisional facts 批次结构；动态消息放到缓存前缀之后。显式缓存默认延续现有 1h 需求。传输适配不能为省 token 偷删 prompt、降低 reasoning 或缩减 Policy。

不假设切换后可复用 OpenRouter 项目的已热缓存；新项目首轮可能重建。思考参数、schema、前缀变化也可能影响缓存，profile 应冻结这些配置。

未来正式切换前，以超过该模型最小缓存长度的真实形状前缀执行少量独立调用：首次创建、相同前缀更换动态消息、再重复；分别确认 creation>0 与 read>0，原始 usage、归一化、计价一致。记录实际 endpoint、TTL、延迟和调用数。离线夹具不能替代该验证；本轮不发这些付费请求，不以定时预热代替正常业务命中。

## 6. 百炼 GLM-5.3-FlashX 前置适配

精确 model 为 `ZHIPU/GLM-5.3-FlashX`；百炼官方说明支持结构化输出与上下文缓存，华北2北京公开价：普通输入 CNY 2／百万、输出 7／百万、缓存命中输入 0.57／百万。计费 provider=bailian、上游=zhipu；不得套用智谱直连或 OpenRouter 价格。[百炼官方模型页](https://help.aliyun.com/zh/model-studio/glm-5-3-flashx-by-zhipu)

执行方案：

1. 在现有百炼 transport 中增加显式 `chat_completions` 能力分支；优先按百炼实际支持的兼容协议接入。当前客户端使用 Responses，不能仅替换 model 字符串就假设此模型支持 Responses、strict JSON Schema 或 reasoning_effort。
2. 用精简请求离线夹具验证 body 构建；未来获准切换时再核对精确模型的真实 JSON/JSON Schema、思考参数、流式结束 usage。若仅支持 JSON object，沿现有输出模型进行本地严格校验，不能放宽业务 schema；schema 校验失败但 usage 已有仍要留账。
3. 目标 round 为 W1R2、W1R3、W2R2、W2R3。Chat Completions 无旧 Responses previous_response_id，须从本轮冻结输入和必要父轮内容构造显式上下文，保持现有业务信息等价；不把跨 provider 的 response id 透传，也不为此加载整个历史 Case／全部研究正文。
4. usage 按总输入、输出、cached_tokens 解析；reasoning 若为输出子集不得再加一次。缓存字段缺失记未知，不宣称未命中。此页面未给独立缓存创建价格，不能套 Claude 创建溢价；若接口未报告创建 token，显示不适用／未提供，按百炼已证实计价项计算。
5. 复用现有百炼密钥与 endpoint 配置，记录实际地域。北京规则不能自动套到国际 endpoint。对未证明的区域价格返回未配置，不阻塞其他模型的统计。
6. 新增未激活 profile，四个 round 显式配置 provider/model/transport/capabilities；未来统一发布新的冻结配置。现有进行中 Case 延续其冻结模型，新回执如实保留历史混用，不能回填成新模型。

**不要把 FlashX 等同于降本。** 当前 Qwen 规则为 CNY 0.8／0.1／2.7（普通输入／命中／输出），GLM 公开单价更高。固定上一交易日 W1R2 已知 token 与缓存分布不变，仅代入 GLM 单价为 CNY 8.5213246，对比原 CNY 3.1885021，约 2.67 倍；这只是同 token 的价格敏感度，不是迁移预算预测，实际 token 和缓存命中会变化。切换验收需同时比较质量、延迟、用量、费用。

## 7. 后端读路径与前端页面

### 7.1 一个聚合核心，一次桶读取

拟新增 `GET /api/doxagent/v2/audit/cost/report`；继续复用 read-context/view_id。创建视图时明确允许 page=COST 且 ticker 为空表示全局，其他页面校验保持原约束。全局视图与用户、账本范围和水位绑定，不能借此放宽权限或任意 ticker 访问。

报告参数：view_id、scope(API/CODEX)、ticker_selector(ALL/具体/GLOBAL)、node_id、round、provider、model_id。model 必须带 provider；所有模块复用经过校验的同一筛选对象。

报告返回 summary、trend、breakdown、rows、coverage、filter_choices、price/fx 元数据。一次有界查询读取当前视图周期、权限及 API/CODEX 范围内的成本桶，在服务端同一次遍历中应用 ticker/round/provider/model 筛选，产生各板块与候选项；不先查每个 ticker 再调用 totals。候选项遵守必要的级联约束，计算自身候选时排除自身筛选，避免选中后只剩一个选项。若基准证明范围过大，再将不影响候选项的条件下推，不能为每个选项触发一次 SQL。

节点行粒度为 `(channel,node,round,provider,model)`；ticker 对照另按需展开。明细数组按现有保留规模有界输出；若实际模型/节点数量超预算，服务端分页，不读取原始 prompt 或把每次调用全量返回页面。父级小计仅做展示，不再加入总计一次。

沿 MVCC 序列查询正确的 current/history 版本。旧 ticker 端点复用同一聚合函数并保持原响应兼容，不能维护两套计算逻辑。ALL 历史趋势按已有日桶，图表按显示范围合并周／月，但不丢掉总额；长历史不能在浏览器按天×ticker×模型展开无界数据。

### 7.2 页面入口与交互

新增全局 `/audit/cost` 入口；原 ticker 审计链接打开同组件并预选该 ticker。全局入口不依赖某个当前活动 ticker；顶部 ticker 与审计范围明确同步，其他标的页面导航维持原语义。

主表列建议：节点／轮次、计费渠道、模型、有效／全部调用数、输入、缓存读取、缓存创建、输出、token 命中率、成本。5m/1h 创建拆分、原币与费用依据在行展开中看；避免十几列挤压成小字。API 与 Codex 沿现有切换，不在 Codex 列伪造 API 美元费用。

数值右对齐，文字左对齐；表头和行共享同一列定义与 padding。保持 14px 正文；桌面 1262px/1559px 验证，无重叠、页面横向溢出。筛选、刷新、错误重试、无数据、部分数据、全局与 ticker 切换逐项验证。完整性提示仅列真实原因和数量，不增加常驻说明段落。

query key 包含 view_id 和完整筛选；筛选变化取消旧请求，避免慢响应覆盖新结果。每次刷新默认 1 次 context + 1 次 report；不按 ticker 轮询，不并发请求五个相同口径模块。必要 drill-down 才额外请求。

## 8. 历史回填与资源约束

### 8.1 有界、幂等的成本专用回填

新增一次性运维命令，名称可定为 `python -m doxagent.v2_read.cost_backfill`，必须实现后才可运行；支持 `--dry-run`、时间窗、来源、resume cursor、batch size、限速、单批上限。默认先回填 2026-10-20 样本，再逐批覆盖**现有保留历史**，不重新获取已删数据。

1. 按稳定主键／时间+ID 做 keyset 分页；每批从原始账本只读提取需要的字段，关闭源读取后再短事务写读模型。
2. 保留 usage id，补 round、缓存创建、reported cost、价格版本。先计算新结果；未变不写。若源记录版本已更新，重新读最新值，不能用旧快照盖掉在线投影。
3. 通过已有贡献替换语义提交：减旧贡献、加新贡献。提交必须给出该 usage 的**完整成本指标集合**，包括要撤销的旧值／维度。现有通用 ingest 若仅收到非空新指标，可能遗留旧费用；新增成本作用域 replacement/tombstone 处理，不能顺手删除 graph/Policy 等其他贡献。
4. round 从无到有会改变维度，旧粗粒度贡献必须减掉。改价、已报费用补到、未知转已知、已知变无效均测试，禁止追加式“双倍计费”。
5. 一个回填进程，初始每批 100 条、写事务目标 <100ms、批间 200ms；超时缩批，不自动加并发。断点只保留来源、最后 ID、规则版本和计数，不另建大任务库。
6. 无法补回的 91 条 usage 保留未知；没有供应商导出账单和稳定关联身份就不补造数字。后续有真实迟到回执时同 ID 更新贡献。

### 8.2 磁盘与性能预算

| 资源 | 初始实施约束／验收目标 |
| --- | --- |
| 常驻组件 | 不增 DB、Redis、队列、财务 worker、定时全扫任务 |
| API | 既有 query workers/queue/超时不扩容；报告 p95<500ms，全部保留历史 p95<1s，均须实测 |
| 回填 | 单进程、约 1 CPU 上限、额外 RSS 目标<200MB；源库只读，写事务短 |
| 请求量 | 常规刷新 2 个请求，不随 ticker 数线性增长 |
| 查询对象 | 日聚合桶，不在页面请求中扫描 runtime 原始回执／provider JSON |
| 索引 | 先 EXPLAIN 和测量；确需时仅补全局成本桶部分索引，不给原始大 JSON 加索引 |
| 本地／生产证据 | 数量、汇总、哈希和少量脱敏夹具；不重复导出全库、prompt、SSE 全量正文 |
| 回填磁盘 | 先试 1,000 条测增量；预计额外增长应<原读库10%且<500MiB，超出先查维度／MVCC膨胀 |
| WAL／历史 | 观察 WAL 大小、最老活跃 view、history 与 source gap；沿现有安全维护回收，不强制 truncate/vacuum |

这些是待验证的执行预算，不是已测成绩。若慢，优先修全表扫描、重复聚合、候选项查询和读池排队；不以提高超时掩盖。回填触发 API 排队／繁忙、事务 p95>100ms 或 WAL 持续失控时暂停回填并缩批，在线业务照常运行。索引计划采用 SQLite EXPLAIN，不引入 Postgres 改造。

既有 MVCC 旧视图不读新字段结果；回填期间新报告显示迁移进度／尚未覆盖历史，不能混合新旧价格并标完整。保持一个兼容读路径，待回填完成再统一开放新页面默认入口；不复制整套 serving DB 作为新一代账本。

## 9. 按依赖顺序执行的工作包

| 顺序 | 文件与操作 | 产出／验收 | 依赖 |
| --- | --- | --- | --- |
| E01 | 对比最新 main；检查本地 dirty 状态、部署镜像版本；用现有样本整理来源矩阵 | 确认修改边界；不混入 CPU/Bus 等并行任务 | 无 |
| E02 | 更新 `dev_plan/workflow_v2/DOXAGENT_V2_API_CONTRACT.md` 成本节、`DOXAGENT_V2_FRONTEND_FOUNDATION.md` 冲突条目；设计 `frontend/v2/src/core/schema-source.ts` 新报告 DTO | 明确请求数、部分费用、全局范围、round、缓存字段与价格依据 | E01 |
| E03 | `src/doxagent/v2_read/usage.py`，新增 `pricing.py/pricing_rules.json`；独立 normalize 函数 | 现用与新渠道的字段和 Decimal 计价测试；不启用新模型 | E02 |
| E04 | `v2_read/runtime.py`、`api_usage.py`、`formal.py` 及实际调用投影入口 | round/cache-write/provider cost 不丢；来源矩阵中确认遗漏被修复，去重与准入不变 | E03 |
| E05 | `v2_read/repository.py` 的成本贡献替换、一次性 `cost_backfill.py`；若必要才变更 `serving_schema.py` 的定向 upgrade | 同 ID 回填两次无增长；撤销旧粗粒度／旧费用正确，其他指标不变；不重装 serving 库 | E04 |
| E06 | `src/doxagent/api_v2/cost.py`、`views.py`、read-context 参数及 DTO 校验 | 全局 report 一次聚合；coverage 真实；旧接口兼容；current/history 同口径 | E02、E05 |
| E07 | `frontend/v2/src/pages/audit.tsx`、`app.tsx`、现有 page query/context；样式与 schema 生成 | 全局入口、共同筛选、逐轮逐模型行、原币和费用依据；两个桌面宽度验收 | E06 |
| E08 | Runtime 新增 `vertex.py`、必要的归一化／缓存 prompt 共用函数；`transport.py/factory.py/service.py` | 原生协议、ADC、SSE usage、1h 缓存离线适配，未激活 profile | E03，可与主读路径分开提交 |
| E09 | 百炼 Chat Completions 分支、四 round 未激活 profile、能力描述 | 不误用 previous_response_id；schema 与输入语义一致；记录精确 model/provider | E03，可与主读路径分开提交 |
| E10 | `tests/v2_backend`、Runtime 传输测试、前端测试；运行一次真实保留规模读路径基准 | 第10节全部验收；基准不依赖额外扩容 | E05–E09 |
| E11 | 文档、changelog、窄提交；合并并确认 origin/main 包含修复提交 | 保留工作区无关修改；生成发布版本清单 | E10 |
| E12 | 先部署兼容 projector/API；分批回填；对账通过后发布 Web 全局入口 | 相关镜像定向构建，数据与资源验收可复核 | E11 |
| E13 | 独立的未来模型切换窗口：凭据／权限检查、小样本真实缓存与 GLM 协议验证、发布新冻结配置 | 核对账本和真实首轮；已有 Case 不变 | E08/E09 完成，且正式切换另行授权 |

E08/E09 的工程前置适配包括离线实现与测试；缺账号权限时仅阻止 E13，不阻止 E03–E12 成本修复。不得把“模型页写支持结构化输出”当作已经通过本项目严格 schema 的证据。

`service.py::_round_model_settings` 当前按 provider 推导 transport，应改为冻结配置显式 transport/capabilities；缺少新字段的旧 Case 继续走旧兼容推导。保留现有百炼 Responses 和 OpenRouter，仅增加百炼 Chat、Vertex 原生两个明确 adapter 路径，不设计任意供应商插件平台。重试仍由编排层负责。

## 10. 验证矩阵与黄金数据

### 10.1 离线／契约测试必须覆盖

- coverage：全已知、全未知、已知零、无调用但源完整、source gap、迟到、窗口未结束、字段独立完整；比率不是固定 NOT_RECORDED。
- token：原生 Claude 加读写、OpenRouter 不重复加、百炼总输入；缓存字段缺失；混合 TTL；reasoning 子集；非法负数与不一致值仅使相关字段失效。
- 价格：旧型号、新 DeepSeek 忙闲边界、OpenRouter 已报费用优先且不与详情重复相加、Vertex 两 TTL/global/regional/100k 边界、GLM 精确 ID/地域、不明价格、Codex N/A。
- 尝试：成功／失败／待重试均计入有 usage 的费用，超时无 usage 保留未知；SSE 累计与断流不重复。
- 聚合：W1R1/W1R2/W2R1、多模型同节点、GLOBAL／已移除 ticker、API/Codex 去重、父级小计不重复、ALL 星号与日桶不重复。
- 回填：同 ID 重放幂等、旧 round 桶撤销、已报费用替换估价、源更新竞争、删除旧费用贡献、MVCC 旧 view 不变、无关贡献不变。
- 页面：每个筛选同时影响四板块；provider/model 级联；取消旧请求；空态／部分态；全局入口独立于当前 ticker。

### 10.2 2026-10-20 黄金窗口

UTC `[2026-10-20T06:00:00Z,2026-10-21T06:00:00Z)`，对应北京时间 10/20 14:00～10/21 14:00。固定使用审查导出样本，不把后续新增／迟到数据与旧快照强行比较。

| 断言 | 必须得到 |
| --- | ---: |
| Runtime API 调用身份数 | 2,121，无重复无丢失 |
| usage 完整／缺失 | 2,030／91 |
| 已知 input／output／cache-read | 56,346,800／2,242,135／49,820,161 |
| 现有页旧价格小计，仅用于复现 | USD 0.8831937794117647 |
| 补已证实费用／规则后的已知小计 | 约 USD 2.5342394776470588，固定汇率6.8 |
| W2R1 尝试／完整／未知 | 620／615／5 |
| W2R1 input／output／read／write | 32,845,922／537,115／30,345,184／1,769,343 |
| W2R1 已知成本 | USD 0.92792950 + CNY 1.1958572；折算约 USD 1.10379085294 |
| W1R2 尝试／完整／未知 | 634／585／49 |
| W1R2 input／output／read | 2,218,501／805,999／1,089,280 |
| W1R2 已知成本 | CNY 3.1885021；折算约 USD 0.46889736765 |

W1R2 的585个已知样本包括559 OK、19 PENDING_RETRY、7 FAILED，不能仅取成功。黄金金额使用原始 OpenRouter 已报费用与样本期已核实规则；不能用未来 Vertex 直连规则重写旧 OpenRouter 账目。

Codex 样本 1,010 个，967 COMPLETE、43 PARTIAL；其中41空usage、2显式None。单独核对，不混入2,121个API调用。源码／来源检查点追平不能抹掉这些原始缺口。

全局报告、各 ticker 报告相加、按 round/provider/model 的分组相加在相同水位必须一致；token 精确整数，金额在未舍入 Decimal 层一致，展示层允许最后位四舍五入。

### 10.3 实施时运行的命令

```powershell
uv run pytest tests/v2_backend/test_cost_api.py tests/v2_backend/test_usage.py tests/test_codex_turn_usage.py -q
```

新增测试随实现加入上述目录及 Runtime transport 测试文件，再按改动影响运行其集合。前端在 `frontend/v2` 分别执行：

```powershell
pnpm schema
pnpm typecheck
pnpm test
pnpm build
```

schema 从 `src/core/schema-source.ts` 生成，不能只改生成后的 JSON。浏览器验证需真实执行并记录，构建通过不算交互验收。性能测试在生产相同保留规模的只读桶上执行，设请求上限；至少串行基准与4个并发报告请求，同时验证 Overview 无繁忙回归。

## 11. 发布、回退与完成判据

### 11.1 发布顺序

1. 提交前追加 changelog，记录行为、统计口径、数据回填及兼容性；只提交本方案范围文件。生成 Git SHA、规则版本、schema hash、测试结果和对账摘要。
2. 合入并推送 origin/main 后，确认远端 main 包含该 SHA；生产从该提交构建。不能先覆盖生产文件再补 main。
3. 当前 compose 共享后端镜像时仅构建一次后端，再定向更新 `v2-projector`、`v2-api`；前端只构建／更新 `v2-web`。使用 `--no-deps` 或等价定向发布，禁止整体 `compose build/up`。实际服务名和镜像先以生产 compose 核实。
4. 初次审计修复不切运行模型，也不为未激活 adapter 重启 Scheduler。未来启用适配器时再定向更新实际加载 Runtime transport 的进程（通常 Scheduler，需以生产命令核实），不重建 Bus／Site Access／全部 Worker。
5. 先发布兼容投影与 API，保留旧端点；执行小窗 dry-run 和回填，核对黄金数据；完成保留历史后开放全局页面。记录回填进度与未恢复范围。
6. 首次上线观察30–60分钟：查询延迟／队列／繁忙率、projector lag、WAL/history增长、CPU/RSS、Overview回归；不用高频全表探针制造负载。

### 11.2 回退

API/Web 回退到已记录镜像；新增 usage JSON 字段是加法兼容，不做全库逆迁移。若旧 API 无法正确处理新 round／缓存费用桶，回退使用同聚合核心的兼容 ticker 接口或关闭新入口，不能将未知新桶按旧价格误算。

回填可随时停在断点，保持已完成对象幂等。发现计价错误，发布修正规则并按同 ID 限定重算受影响来源／时间窗，不删除原始账本、不全库 restore、不重放全部业务事件。原生 usage 不改写；费用计算记录保留可解释版本。

未来模型切换回退只影响新准入 Case 的冻结配置；在途调用按原冻结路由收尾，不中途改 provider/response identity。新渠道失败不自动偷偷换到另一付费渠道。

### 11.3 完成判据

- 已知 token 与黄金样本逐层对账一致；公开价格可计样本及 OpenRouter 已报费用完整纳入。
- 完整样本存在正常 COMPLETE 路径；真实未知、来源缺口与未计价可定位，不伪造“全部实付已知”。
- 默认全 ticker，逐轮逐模型可查；共享研究与历史 ticker 可筛选；整页筛选一致。
- 缓存创建、读取、TTL、普通输入与价格分项正确；Vertex 预适配无重复计 token，百炼精确模型规则已具备。
- 全局报告满足既有读取预算，Overview不回归；不新增常驻计算／存储服务或不可控历史副本。
- origin/main、生产镜像与规则版本可对应；明确区分成本修复已发布、adapter离线就绪、未来真实模型切换尚未执行。

## 12. 本轮交付说明

本轮仅创建此执行方案，并核实 Vertex 缓存机制、Haiku 5.5 模型标识与公开价格、百炼精确模型与价格。未修改业务代码或现有统计数据；未运行未来 adapter、未调用付费模型、未构建／部署或切换生产配置。正式实施按 E01–E12 执行，E13 单独处理实际模型切换。
