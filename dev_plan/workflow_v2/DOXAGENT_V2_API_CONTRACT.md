# DoxAgent V2 API Contract

- 契约版本：`2.0.0-draft.2`
- 日期：2026-09-07
- 状态：供评审的完整契约草案；描述目标 API，不表示接口已实现或已部署。
- 范围：V2 前端与后端的数据交换、业务语义、操作反馈、分页、缓存和增量协议。
- 配套类型：[doxagent-v2-api.types.ts](api_contract/doxagent-v2-api.types.ts)。类型与本文共同构成契约；字段形状以类型文件为准，HTTP、约束及业务定义以本文为准。
- 类型化示例：[doxagent-v2-api.examples.ts](api_contract/doxagent-v2-api.examples.ts)，覆盖真实零/缓存缺失/Codex 不计价/上一窗口为零/并发冲突/图计数替换；全部为合成示例，不是生产观测。
- 源码审查记录：[DOXAGENT_V2_API_SOURCE_AUDIT.md](DOXAGENT_V2_API_SOURCE_AUDIT.md)。审查对象为当前工作区，包含尚未提交的 V2 改动；不是仅按 Git HEAD 审查。
- 业务输入：[前端数据需求](DOXAGENT_V2_FRONTEND_DATA_REQUIREMENTS.md)、[PRD Part 1](DOXAGENT_V2_FRONTEND_PRD_PART1.md)、[PRD Part 2](DOXAGENT_V2_FRONTEND_PRD_PART2.md)，已逐条纳入 Part 1 §4.6。

本文不规定服务分层、数据库迁移、表或索引设计、缓存组件、任务拆解、实施顺序、部署方案。类型文件是文档配套定义，不接入当前后端或前端。

## 1. 契约边界与审查结论

### 1.1 V2 的真实边界

所有业务 API 使用独立前缀 **`/api/doxagent/v2`**。前端页面路由可保持 PRD 约定；页面路由与 API 路径版本独立。

只有经来源验证属于 V2 的数据可被接纳。V2 缺失时返回缺失或不可用，不回退其他 workflow，不读取旧 DTO 补齐。全局统计也必须先限定 V2 身份及调用者可见范围。

内部版本名不能按字符串后缀判代：`codex_global_research_v1`、`codex_document2_v1`、`codex_document3_v1`、`event-library-foundation-v1` 都是当前 V2 执行链使用的独立合同名称。本 API 保留它们的产物 schema 元数据，不改写源产物，也不把它们误判为已废弃 workflow。

### 1.2 现有事实、可派生事实与新增能力

| 范围 | 当前源码事实 | 本契约要求 |
|---|---|---|
| 初始化 | 独立控制库、父状态、动态节点、attempt/event、失败恢复、不可变激活引用与 Bus/Runtime ACK 已有 | 六步摘要、精确首次开始/结算时刻、完整失败集合、HTTP 操作回执 |
| D1 | Global Research Bundle 与各报告 ArtifactRef、发布与 run summary 已有；Bundle 无独立 `updated_at` | 只读摘要、分项正文、历史；真实更新时间缺失必须返回 `NOT_RECORDED` |
| D2 | `document2.v2`、Shell/Unit/State/Factor/Gap、ShellOutcome、COMPLETE/PARTIAL 已有 | 从激活引用选定 run，按 Shell 读取，失败 Shell 保留正式可用部分 |
| D3 | `document3.v2.2`、固定 OR、PolicySet 版本、Policy 消费记录已有 | 分离三种版本身份，生命周期事实与时间线，按筛选/窗口聚合 |
| Event Library | Event/Fact 独立版本、成员关系、Reference View 与 Delta 已有 | Canonical 与日度 Reference Delta 分开；结构化 add/modify/remove 与移除前详情 |
| Message Bus | Standard/Raw/Stream/Member、Source/Binding、PollState 已有 | 同一批次所有消息到 Case 的关联、筛选一致性、连续运行区间、UI 专用可恢复增量 |
| Runtime | Case、W1/W2 turn、W3、effect、provisional、archive/badcase/trade、编排 journal 已有 | 新图与节点聚合、完整阶段时间、结果集合、Case/Fill 关联及图增量 |
| Trade Executor | Intent pin/intake、order attempt、修正后的 fill、归属份额与退出账本已有 | ticker 级环境控制、准确 triggered 时间、净收益及佣金完整性 |
| 用量 | W1/W2 nullable usage、Worker usage、通用 ModelUsageEvent 分散存在 | 全 V2 可证明调用身份、API/Codex 分域、缺失 cached input 不补零、固定计价 |

当前 Dashboard HTTP 前缀为 `/api/dashboard/v1`，部分 research 与 Bus 控制能力已接 V2 底层，但并非本契约。尤其现有消息卡片转换还返回 `summary`、固定 processing 状态及空 Runtime 关联；不能直接视为满足本期前端需求。这里只确认当前接入边界，不展开已废弃实现。

### 1.3 本版明确裁定的歧义

1. **运行激活与 Policy 消费版本不是同一身份。** API 使用 `runtime_activation_id` 表示整套输入；`policy_activation_revision` 表示 `ar_…` 消费边界；`policy_revision_id` 表示 Policy 完整业务内容的独立修订身份。
2. **Standard Message 与 Runtime Case 不是一对一。** `buffered` StreamItem 的全部 member 共享一次 Case。不得只关联 envelope 中的最后一条消息。
3. **触发、接收、委托、成交分层。** TRADE 路由、TradeRecord、READY intent、EXECUTION_ACCEPTED 均不构成成交；只有真实有效 Fill 构成交易执行。
4. **当前交易模式不能从全局 profile 推测。** 源码仍使用 `trade_execution/active` 全局指针；ticker 级能力补齐前，Paper/Live 能力必须不可用，禁止启动操作静默切换全局 profile。
5. **正文低流量规则与默认可见内容兼容。** D1 首次进入单独读取默认 C1；D2 默认读取首个 Shell 的 Units；其余分项懒加载。默认内容不嵌入摘要/KPI。
6. **事件“新增事件”页面是 Reference View 的日度变化。** 它的 add/remove 不等于 Canonical Event 的创建/失效。
7. **休市包含法定休市日。** 会话枚举使用 `CLOSED_MAINTENANCE/CLOSED_SLEEP`，中文展示“休市维护/休市休眠”，不把所有休市称为周末。
8. **接口契约完整与数据当前齐备分开。** 缺失能力必须通过资源状态与 coverage 暴露，不能返回“看起来完整”的假零。§13 列出发布前的数据能力条件。
9. **休市链不是固定 W1/W2 并行。** 当前 REALTIME 并行，CLOSED 先 W1 后 W2，OLD/normal 还可能跳过 W2。API 明确返回 first_round_shape，不为了图形或延时指标伪造并行 attempt。
10. **Library version 不是跨分支全局唯一键。** Runtime 存在 copy-on-write 库分支，内部 pin 还包含 root；API 用不透明 library_snapshot_id 精确固定逻辑库、版本与内容身份，不向浏览器泄露路径，也不单用 ticker+数字版本定位历史详情。

## 2. HTTP 与公共数据规范

### 2.1 标量、空值和版本

- JSON 使用 UTF-8、snake_case。除显式 `JsonObject` 外，结构是闭合对象；请求未知字段返回 422。可选字段仅用于 PATCH 的“未修改”，普通响应中的 nullable 字段必须显式返回。
- `Id` 是不透明非空字符串，调用方不得解析前缀、run 名称或数组位置。Ticker 服务端 trim/uppercase，允许字符 `[A-Z0-9][A-Z0-9.\-]{0,19}`；是否支持该标的由服务端验证。
- `Instant` 为带 `Z` 的 UTC RFC3339；`Day` 为 `YYYY-MM-DD`。业务原始文本时间，例如 Event 的季度、D2 horizon，不转成伪造的 timestamp。
- 数量字段为非负安全整数；Metric 中所有数值以 DecimalString 传送，包括整数指标；数量 Metric 必须是整数字符串。价格、数量、成本与收益使用十进制字符串，禁止 NaN、Infinity、指数或格式化货币符号。比率值域为 0–1；`change_pct` 为百分数，`10` 表示 +10%。
- 业务文档的 `schema_version`、HTTP `contract_version`、`representation_revision` 各有用途。representation revision 只用于缓存与响应替换，不冒充业务版本。
- 缺失 D1/D2 run 用缺失状态，不能创造顺序整数“文档版本”；Event Library 与 PolicySet 使用源版本号。
- 业务版本是不可变引用；修正历史审计投影会递增 representation revision，不能篡改源产物。

### 2.2 响应与错误

除另行声明外，GET 的响应为 `Response<Resource<T>>`，其中 `T` 为接口表列出的类型。例如 Overview KPI 为 `Response<Resource<OverviewMetrics>>`。认证、capabilities、ReadContext 返回 `Response<T>`；操作接口返回 `Response<Operation>`。写 binding 返回 `Response<BindingConfig>` 或 `Response<MutationReceipt>`。

`Resource` 规则：

| state | data | 含义 |
|---|---|---|
| AVAILABLE | 非 null | 当前资源可读；完整性另见 coverage |
| EMPTY | 非 null，列表为空 | 已确认此范围没有记录，非加载失败 |
| PARTIAL | 非 null | 有可展示部分，同时有缺失模块/字段 |
| NOT_PRODUCED | null | 尚无正式产物 |
| UNAVAILABLE | null | 来源、固定引用或能力不可用 |
| FORBIDDEN | null | 已获准访问上层范围，但某子资源不可读 |
| ERROR | null | 本次独立模块读取失败 |

`Value<T>` 将字段不可用原因细化：未生成用 NOT_PRODUCED，未记录用 NOT_RECORDED，不适用用 NOT_APPLICABLE，权限用 FORBIDDEN，读取失败用 ERROR。`AVAILABLE.value=0` 是确认零值。不可用时 value 必须为 null，reason 必须非 null；AVAILABLE 时 reason=null。

请求本身或整个目标资源无法处理时，返回非 2xx `ErrorResponse`；聚合内单个失败使用 Resource/Value，保留其余模块。客户端刷新失败保留同 scope 的旧数据；若服务端提供旧响应，则 `meta.freshness=STALE` 且 `refresh_error` 非 null，`as_of` 仍为旧数据时点，不能改成现在。

| HTTP | code 示例 | 客户端行为 |
|---|---|---|
| 400 | INVALID_CURSOR、SCOPE_MISMATCH、INVALID_VIEW | 修正范围/游标；不可静默改用另一范围 |
| 401 | UNAUTHENTICATED | 重新认证；不得以公开模式回退 |
| 403 | FORBIDDEN | 展示无权限，不重试读取隐含详情 |
| 404 | TICKER_NOT_FOUND、RESOURCE_NOT_FOUND | 对象不存在；跨 ticker 身份也按未找到处理 |
| 409 | NO_ACTIVE_REVISION、OPERATION_IN_PROGRESS、TICKER_REMOVED、IDEMPOTENCY_CONFLICT、MODE_UNAVAILABLE | 展示原因，保持真实状态 |
| 410 | CURSOR_EXPIRED、VIEW_EXPIRED | 只重建受影响组件/页面范围的基线 |
| 412 | REVISION_CONFLICT | 保留编辑内容，读取最新目标对象后由用户重新保存 |
| 413 | PAYLOAD_TOO_LARGE | 不重试相同大请求；使用指定有界读取或下载 |
| 422 | VALIDATION_FAILED、PERIOD_NOT_SELECTABLE | 使用 fields 指明具体字段，不自动改周期 |
| 428 | PRECONDITION_REQUIRED | 补齐目标对象 If-Match |
| 429 | RATE_LIMITED | 遵守 Retry-After |
| 503 | SOURCE_UNAVAILABLE、CAPABILITY_UNAVAILABLE | 保留缓存，遵守 Retry-After；禁止无限重试 |

`ApiError.code` 可扩展，客户端必须提供未知 code 的通用显示；状态枚举增减、计数口径或字段类型改变属于契约变更。message 是可读业务信息，不含本地路径、SQL、账号、token、worker capability、prompt 或原始堆栈。

### 2.3 认证与权限

- 除 auth/config 外均要求 `Authorization: Bearer <Supabase access token>`。服务端验证身份与可信开发者权限；本期沿用项目的开发者访问边界，不新增多租户产品能力。
- `/auth/me` 返回 `can_read/can_operate`。服务端每次操作及每个对象查询仍做授权，不信任 UI 按钮状态或客户端传入的 owner。
- 前端只允许直接使用 Supabase Auth；V2 业务数据通过本 API 读取，不直接订阅业务表或下载数据库存储地址。service_role、账户配置与 Worker 凭证不进入 DTO。
- SSE 使用支持 Authorization 和 Last-Event-ID header 的流式客户端；不得把 bearer token 放在 URL。下载也使用认证请求。
- capabilities 只表示当前服务能执行该操作；用户在启动表单选择 Live 并提交即为该次业务授权，不新增 PRD 排除的风险弹窗或二次确认。

### 2.4 分页、限额和排序

- `limit` 默认 20，1–100；列表按响应字节上限可少于 limit。`has_more` 与 next_cursor 必须一致。没有 total 全量计数要求；KPI 单独返回聚合。
- `cursor` 为不透明快照游标，绑定 ticker、授权范围、view、筛选、排序及高水位；不可移用于其他查询。首个 Page 返回 snapshot_id，后续页维持相同数据集合和排序。禁止 offset 翻页造成动态数据漂移。
- 一般历史按 `(业务时间 DESC, 稳定身份 DESC)`；初始化内部次序与 D2 Shell/Unit 使用正式产物原始顺序。Policy 默认按固定 PolicySet 的正式顺序，历史筛选按最近匹配事件时间倒序。
- Event 按已解析发生时间 DESC，再 event_id；UNKNOWN/缺失统一在末尾。MONTH/QUARTER/YEAR 用对应区间起点排序，INTERVAL 用已知起点；排序锚点仅为服务端排序事实，不改变展示精度。无法解析视为 UNKNOWN 并注记 coverage。
- 消息按 `(stream_published_at DESC, stream_offset DESC, member_index DESC, standard_message_id DESC)`；进入流时间从 StreamItem/Member 获取，不使用 source published 或 normalized time 替代。
  `MessageSummary.stream_offset` 为必填 Count，直接来自原生 StreamItem；REST、分页游标和 SSE 消费端使用同一完整排序键。
- 首屏摘要/列表每响应不超过 256 KiB（未压缩 JSON）；普通单对象结构化详情不超过 1 MiB；单个文本 chunk 不超过 128 KiB；SSE event 不超过 32 KiB；配置写请求不超过 64 KiB。超长内容按 chunk/分页继续，不截断且宣称完整。
- 单个不可再分的结构化对象超过 1 MiB 时返回 413 与其已授权 content_id，可通过 `/contents/{content_id}` 按 chunk 完整读取 JSON，拼接完成后解析；列表给 ContentRef 及明确的 unavailable/size 状态，不删除该对象。
- 增长型时间线、索引、source 列表、模型/节点筛选选项、attempt、Fill 和历史版本都分页。图的固定节点/边有界；不返回无限 Case 集合。`ALL` 也不解除分页。
- view 与列表游标至少保留 24 小时；过期返回 410。指定历史 run/version 的内容不依赖过期 view，可重新按身份取得。详情不得借过期自动改读 current。

## 3. 一致性、语义时间和指标合同

### 3.1 ReadContext

首次打开页面、第一次切换未缓存周期或人工刷新时，取得 ReadContext，然后将 `view_id` 用于该次页面读取。服务端固定 `as_of`、窗口、可见 ticker 范围、当前激活引用及各数据高水位。它是页面的一致读范围，不是业务 Activation Revision。

跨库事实不假装具有未实现的原子同步：同一 view 的聚合及列表必须使用同一已确认水位；落后的来源通过 coverage 表达。若不能证明关联，返回 PROVENANCE_UNVERIFIED 或 PROJECTION_LAG，不混合另一版本补齐。

- D1 当前页、D2 当前页、PolicySet 和 Canonical 当前库都由该 view 固定的 active revision 定位。后台候选发布不改变当前页；没有 active 时 current 资源返回 NO_ACTIVE_REVISION，历史仍可按身份读取。
- 特定历史 run/版本直接显式寻址；不得再读 current 来拼 D1 section、D2 Shell、Policy 来源或 Event Fact。
- 消息/Runtime 普通分钟读取使用 `refresh=MINUTE` 创建当前范围的新 view；不自动替换已打开的大详情。相同分钟、相同范围可合并读取。图/消息 SSE 基线用各自 stream cursor 继续，分钟请求不重建 SSE。
- 当语义日切换导致当前周期窗口改变，服务端发 `scope.rolled`，前端只为当前可见组件重建相应窗口基线。普通静态页面仍等人工刷新或首次选择新范围。

### 3.2 时间窗

服务端统一调用 V2 的 ET 02:00 语义时钟与有效交易日历，返回冬夏令时、临时日历覆盖、正常/提前收市的实际边界。春季不存在的 02:00 取首个有效时刻，与当前 `semantic_clock.boundary()` 一致。

| period | 当前窗口 | 上一窗口 |
|---|---|---|
| PREVIOUS_TRADING_DAY | 当前语义日之前最近一个有效交易日 | 再前一个有效交易日 |
| CURRENT_TRADING_DAY | 当前语义日；非交易日不可选 | 前一有效交易日 |
| TRADING_DAYS_7 / 30 | 截至当前语义日最近 N 个有效交易日，含今日若有效 | 紧邻此前 N 个有效交易日 |
| ALL | 该范围已知 V2 历史首时刻至 as_of，含休市语义日 | 无 |

N 日窗成员只包含返回的 trading_days：周末/休市的时间虽然位于首尾 bounding interval 之间，也不计入 N 个有效交易日指标；不可只做单段时间范围求和。ALL 包含休市的实际调用、消息和 Case，且不显示环比。休市日 Reference Delta 独立按 semantic_day 读取。

DayWindow.start_at/end_at 表示完整语义窗口；observed_until 表示该 view 已观察到的截止点。当前日未结束时可以显示截至目前的累计量，但 coverage 至少为 PARTIAL，环比 WINDOW_INCOMPLETE。休市“本交易日”不自动改成前一日。

ALL 不内嵌无限交易日数组：trading_days=[]、membership=ALL_SEMANTIC_DAYS，同时返回 trading_day_count；需要检查历法时使用有界 calendar 接口。

### 3.3 Metric 与 coverage

- 相对环比固定 `(current - previous) / previous * 100`，成功率等比率指标仍按该公式，不计算百分点差。上一值为负时仍按原公式；为零时 PREVIOUS_ZERO，不使用绝对值分母。
- 全历史、历史不足、任一窗口不完整、任一值无效或未知，change_pct 不适用，给对应 reason。判断按指标自身来源覆盖范围进行，不能因消息完整就假设成本完整。
- 当前局部观测值可 AVAILABLE 配合 PARTIAL coverage；明确它是已知子集。无法确定任何有效样本时不返回 0。
- 所选窗口的可信计数确认为空则 0；无延时样本、无补全尝试、无输入 token 的比率为 NOT_APPLICABLE/NO_SAMPLES。
- 多 ticker Overview 限定该 view 中持久可见的 V2 ticker，包含已暂停/停止 ticker 的窗口事实，排除 removed；两比较窗口使用同一 ticker 集合，不用当前状态删掉它的旧数据。任何成员历史不足影响相应指标可比性。
- `known_count/excluded_count` 只用于已知数量，不知道总缺口时 excluded_count=null。不得声称“全量”但覆盖证明为空。

### 3.4 去重身份与归属时间

| 指标/对象 | 去重键 | 窗口归属事实 |
|---|---|---|
| Overview Policy 命中 | ticker + policy_id | 最终有效 hit 的结算时间；同 Policy 多消费版本只计一个 |
| 策略 Policy 命中 | ticker + policy_id + policy_activation_revision | 最终有效判定的 hit 时间；不是只数被执行的 Policy |
| Runtime Policy 命中 | case_id | Case semantic_day；最终有效判定，W3 已完成时覆盖 W2 |
| Standard Revision | ticker + standard_message_id + revision | 首次正式 stream publication 时间；精确重复不增量 |
| Runtime 处理/NEW/OLD/W3 | case_id | 已固定的 Case semantic_day，接收时间保留用于排序 |
| 原子候选 | ticker + semantic_day + runtime signature 的正式唯一分配身份 | 首次成功保存候选时的语义日 |
| 正式触发 | intent_id | 正式释放进入 READY 的真实时间；closed candidate 到正式释放才计 |
| Overview 执行/策略执行 | execution_id | 首个有效 ENTRY Fill 的 broker time；策略只含 executed_policy_id 非空 |
| Runtime 交易执行 | case_id | 所选 Case cohort 内有有效 ENTRY Fill；不以收到回执时间另造 Case |
| Event/Fact 新增/修改/失效 | ticker + event_key / fact_key | 正式 Library 变更发布时刻，非 occurred_at |
| Policy 新增/修改/Retire | ticker + policy_id | 生效版本的生命周期变更时刻 |
| 用量 | V2 真实 provider call/SDK turn 稳定身份 | 真实调用开始时间；重读回执不新增，实际重试请求单独计 |
| 净收益 | 有效 Fill + DoxAgent 归属份额 | 实际 EXIT Fill 的 broker time |

Overview 的触发与成功是各自在该窗口发生的业务事件，跨窗口结算时成功数可能大于触发数，不以裁剪数字“修正”。Runtime 页是 Case cohort 视图：同 Case 后续成交会修正该 cohort 的执行指标。两者时间口径不同，接口和 UI 不应强求相等。

消息页周期新增按 Standard Revision，与 StreamItem 数不同；一个 buffered item 有 20 条 member，消息数可为 20 而 Case 数为 1。Overview 的唯一 Standard Message 以正式 revision 为计数单位，与消息页统一。

## 4. 全局接口与 Ticker 管理

以下路径均相对 `/api/doxagent/v2`。`V` 表示必需 query `view_id`；`P` 表示 `limit?,cursor?`。表中无 body 的 GET 禁止请求正文。未列出的 query 返回 422。

| 方法 | 路径 | 输入 | 响应 T / 特例 |
|---|---|---|---|
| GET | `/auth/config` | 无 | AuthConfig，Response<T> |
| GET | `/auth/me` | 无 | Principal，Response<T> |
| GET | `/capabilities` | `ticker?` | Capabilities，Response<T> |
| GET | `/read-context` | `page,ticker?,period?,refresh?` | ReadContext，Response<T> |
| GET | `/calendar/trading-days` | `view_id,start_day,end_day,P` | Page<TradingDay> |
| GET | `/tickers` | `visibility=NAVIGATION`，P | Page<TickerNavigation> |
| GET | `/tickers/{ticker}` | 无 | TickerState |
| GET | `/overview/status` | V | OverviewStatus |
| GET | `/overview/metrics` | V | OverviewMetrics |
| GET | `/overview/tickers` | V，P，`run_state?`、`health?` | Page<TickerOverview> |
| POST | `/tickers` | StartTickerRequest | 202 Response<Operation> |
| POST | `/tickers/{ticker}/pause` | `{}` | 202 Response<Operation> |
| POST | `/tickers/{ticker}/restart` | `{}` | 202 Response<Operation> |
| DELETE | `/tickers/{ticker}` | 无 body | 202 Response<Operation> |
| GET | `/tickers/{ticker}/initializations/{initialization_id}` | 无 | InitializationProgress |
| POST | `/tickers/{ticker}/initializations/{initialization_id}/resume` | `{}` | 202 Response<Operation> |
| GET | `/operations/{operation_id}` | 无 | Response<Operation> |

生产能力查询补充（2026-09-09）：`ticker` 可为尚未 START 的合法标的，用于查询预先配置的正式 Paper/Live binding；不存在控制记录不构成 404。无 binding 时交易能力为不可用。交易能力还要求 control、delivery、executor 心跳有效；未传 ticker 不推断其他标的的账户绑定。启动表单按目标 ticker 查询，提交仍由后端原子校验绑定。

ReadContext：OVERVIEW 禁止 ticker，其余 page 必须 ticker。RESEARCH/EXPECTATIONS 不接受 period；其他默认 PREVIOUS_TRADING_DAY。OVERVIEW 禁止 ALL。refresh 为 `OPEN`（默认）、`MANUAL` 或 `MINUTE`；MINUTE 仅 MESSAGE_BUS/RUNTIME 可用。calendar 单次 start/end 范围最多 366 天，仍分页。

导航包含 running、blocked、paused、stopped；`initialization_incomplete=true`（含失败待恢复）或 removed 的 ticker 均不进入。每次切 ticker 保持当前业务页面；缓存键必须包含 ticker。

### 4.1 操作幂等与并发

- 所有 mutation 必需 `Idempotency-Key`，非空 8–128 ASCII 字符。作用域为用户 + 方法 + 路径；相同 key/body 返回同一操作/结果，不重复启动、恢复、移除或保存。相同 key 不同 body 返回 409。已接受操作的幂等关联保留至少 7 天，回执至少 30 天。
- 对既有 ticker 的控制操作必需 If-Match=TickerState.control_etag；初始化恢复使用 InitializationProgress.control_etag；binding 修改/删除使用 BindingConfig.control_etag。创建不存在的 ticker 不需 If-Match；POST /tickers 命中既有 ticker 必需其 control_etag。POST bindings 尚无 binding ETag，不需 If-Match，但必须原子验证未绑定且 source_version 匹配。
- 优先认领相同幂等请求，再检查新操作的 If-Match，避免首次成功后网络重试被自身版本更新误拒绝。
- 同一 ticker 的冲突 mutation 返回 OPERATION_IN_PROGRESS；不因网络断开取消已经接受的任务。返回 Location 指向 operation；操作查询读取回执不触发再次执行。
- 前端只在操作待结算期间按服务端 retry_after_seconds 查询；下限 2 秒，退避上限 30 秒，最长自动追踪 5 分钟。到时保留 operation_id 和“仍在处理”，后续人工刷新/再次查看继续查询，不能显示失败。页面隐藏停止追踪，用户重新进入待处理操作时可补读回执。
- 操作回执的“初始化已排队”表示 START 操作成功，不表示初始化已成功；初始化随后使用六步状态、普通 Overview 刷新查看，不新增 Overview 常驻轮询或 SSE。
- 底层恢复/操作要求的审计 reason 由服务端记录固定动作描述及认证 actor，不要求前端新增原因输入。审计记录必须能关联 operation_id 与 idempotency key 的安全摘要。

### 4.2 各操作的确定语义

| 操作 | 接受条件 | 成功后的事实 |
|---|---|---|
| REUSE_ACTIVE | 有有效 active revision；所选模式可用 | 使用原固定成果重新接纳 Bus/Runtime；真实 ACK 后 outcome=RUNNING |
| FORCE_INITIALIZE | 标的及模式有效，无冲突初始化 | 新初始化持久排队，outcome=INITIALIZATION_QUEUED；旧 active 在候选激活成功前仍保留 |
| PAUSE | 当前可运行/已暂停，无冲突 | 停止新采集与新 Case/新交易释放；持久 PAUSED，已有成果不变 |
| RESTART | 未移除且有有效 active，无未完成初始化 | 重新接纳原激活及当前 ticker 模式，不做研究全链重跑 |
| RESUME_INITIALIZATION | 父 FAILED 且 manual_resume_allowed | 同 initialization_id 当前失败集合恢复；已成功节点不重跑 |
| REMOVE | 目标未移除或同一幂等请求重试 | 停止该 ticker 新采集/处理/释放，持久 removed；从列表和导航移除，保留历史 |

消息监测仍运行分析和记录 Policy hit，但不 claim/消费 Policy、不创建正式交易 intent；分析判断以 ANALYSIS_ONLY 单列，之后切交易模式不得补发监测期间的 Case、candidate 或 backlog。一个 ticker 共享研究、策略和消费生命周期，Paper/Live 仅决定执行环境，新正式输出使用当时 ticker 的绑定，旧 intent 保留原 pin。requested_mode 与 effective_mode 不同时展示真实 effective 与原因；FORCE_INITIALIZE 的新模式仅在候选接纳后生效。模式不可用在接收前失败，不能接受后悄悄变成消息监测。

暂停/移除只关闭新分析 dispatch 与新 intent 生成。停止事务之前已正式释放的 intent（包括尚未交付者）继续按原 pin 和有效期交付；已持久接纳的 Entry（包括尚未发单、待报价、重试）及 Exit 继续原状态机，不因暂停撤单或平仓。新 intent 与 Policy claim 在同一事务检查控制 gate；在途模型回执保留，但不授权后继轮次或越过停止边界释放新 intent。若无法确认跨库停止边界，Operation 保持 RUNNING 或真实失败，不能报成功。前端不新增退出/平仓操作。

移除成功时用 operation 返回的 ticker_state 立即更新本标签页导航/列表缓存；下一次读取仍隐藏。直接指定历史 run 可授权只读。允许通过原 `POST /tickers` 启动表单显式重新添加：使用新幂等请求及当前 control_etag，保留历史和消费状态，以新控制代次隔离旧初始化/分析；不新增恢复按钮。旧 DELETE 幂等重试仍返回原回执，不再次删除新启动。

存在未完成初始化时 pause/restart 不承担初始化控制，actions 给出不可用原因。remove 可终止该 ticker 后续接纳，但必须保证旧初始化/重试的迟到回执不能重新激活或恢复可见性；进行中的初始化按父 FAILED 结算、error.code=OPERATOR_STOPPED、manual_resume_allowed=false，既有成功产物和历史保留。已有 active 的强制初始化期间，可继续真实 RUNNING，同时 initialization_incomplete=true；不能为了初始化卡片伪造已暂停。

### 4.3 六步初始化映射

| step_key | 原生节点/范围 |
|---|---|
| RESEARCH | d1 + cdecr（并行），含动态内部节点 |
| EVENT_LIBRARY | o2 |
| EXPECTATIONS | d2/O0/O1 |
| POLICIES | d3/Trigger Calibration/Compile/Final Review/Publish |
| SOURCES_ACTIVATION | o4.configure、o4.deliver、o4.register、activation.prepare、activation.commit |
| START_RUNTIME | bus.ready、runtime.ready 内的真实 ACK/验证 |

当前默认 plan 没有独立名为 `verify.ready` 的节点；不能为了配合 UI 伪造它。动态内部节点归并到所属步骤，不变更步骤数量。

任一必需节点 FAILED → FAILED；否则有 RUNNING → RUNNING；全部必需节点 SUCCEEDED → SUCCEEDED；其余 PENDING。PARTIAL/DEGRADED/NOOP 是 quality_annotations。耗时从本步骤首次开始到最终结算，含暂停/重试等待；并行取最早开始和最后结算，不能累计 attempt 时长。源时间缺失用 NOT_RECORDED。重新恢复时清除当前 settled_at，继续同一步首次起点。完整成功或无初始化时 TickerOverview.initialization=null，隐藏卡片；已知存在未完成初始化却读失败时必须返回非 null 的 ERROR 资源，不能隐藏故障。单独历史初始化接口仍可读。

`failed_node_keys` 必须完整，不能使用云端当前 LIMIT 10 的失败摘要直接冒充完整集合。恢复请求不让客户端提交节点子集或 advanced rerun 参数。

### 4.4 Overview 指标

正常/阻塞只对无未完成初始化、未暂停/停止、未移除的 ticker 统计；BLOCKED 计阻塞；NORMAL/DEGRADED 且仍可运行计正常。UNKNOWN 不归入任一类，对应计数 coverage=PARTIAL。个别源失败或孤立 Case gap 不能自动升为整个 ticker BLOCKED。

OverviewMetrics 的 policy_hits/trade_executed/trade_triggered/messages 依 §3.4。api_token_cost 只计可计价 V2 API 调用，Codex 不估算美元费用，缺少可计价部分通过 coverage 暴露。nonroutine_repairs 固定 AVAILABLE 0、无环比，明确此版本 Repair 未纳入自动运行。收益依 §10，Paper/Live 分开。

## 5. 基础投研与预期研究

### 5.1 接口

路径中的 run_id 必须是该 ticker 对应研究 lane 的真实 run。`current` 只出现在摘要选择接口；内容请求随后使用返回的固定 run。

| 方法 | 路径 | 输入 | 响应 T |
|---|---|---|---|
| GET | `/tickers/{ticker}/research/current` | V | ResearchSummary |
| GET | `/tickers/{ticker}/research/runs` | P | Page<RunSummary> |
| GET | `/tickers/{ticker}/research/runs/{run_id}` | 无 | ResearchSummary |
| GET | `/tickers/{ticker}/research/runs/{run_id}/sections/{section}` | `cursor?`；section=C1/C3/C5 | ContentChunk |
| GET | `/tickers/{ticker}/research/runs/{run_id}/future-nodes` | P | Page<FutureNodeRow> |
| GET | `/tickers/{ticker}/research/runs/{run_id}/download` | 无 | ZIP 下载，见 §5.2 |
| GET | `/tickers/{ticker}/expectations/current` | V，`limit?` | ExpectationsSummary |
| GET | `/tickers/{ticker}/expectations/runs` | P | Page<RunSummary> |
| GET | `/tickers/{ticker}/expectations/runs/{run_id}` | `limit?` | ExpectationsSummary |
| GET | `/tickers/{ticker}/expectations/runs/{run_id}/shells` | P | Page<ShellSummary> |
| GET | `/tickers/{ticker}/expectations/runs/{run_id}/shells/{shell_id}/units` | P | ShellContent |
| GET | `/tickers/{ticker}/expectations/runs/{run_id}/shells/{shell_id}/units/{expectation_id}` | 无 | ExpectationUnit |
| GET | `/tickers/{ticker}/contents/{content_id}` | `cursor?` | ContentChunk |
| GET | `/tickers/{ticker}/contents/{content_id}/citations` | P | Page<Citation> |

### 5.2 D1

ResearchSummary 精确声明四个 section 的存在性、格式和引用，只在初次打开后追加 C1 内容请求。C3/C5/Future Nodes 第一次选择时读取。历史选择同样只读取被选 run 的当前 section。

`RunSummary.run_status` 来自运行摘要，publication_status 来自 Bundle，二者分开。D1 没有源生 COMPLETE/PARTIAL 发布字段时 publication_state=null；质量只来自正式诊断，不凭空标成 COMPLETE。当前 Global Research 在 failed_nodes 非空时会阻止 Bundle 发布；如果某些报告已正式发布可单项返回，未正式发布 workspace 草稿不能作为成功正文。

`updated_at` 表示文档内容独立修改时刻。GlobalResearchBundle.created_at、run_summary.updated_at、published_at 和读取时间均不能替代；现有不具备时 NOT_RECORDED。

FutureNode 的五项中文源字段转换为类型文件的英文键，文字原样保留；item_key 是该不可变内容内稳定分配的条目身份，ordinal 只管排序，不用 UI 数组位置当身份。

下载 `Content-Type: application/zip`、Content-Disposition filename*=UTF-8，包含该 run 的 C1.md、C3.md、C5.md、future_nodes.json（FutureNodeRow[]）与 manifest.json（ResearchDownloadManifest）。manifest.files 固定四项，INCLUDED 必须有 sha256/size_bytes，MISSING 不写对应空文件且必须有 reason；原生无单独 Artifact 时 artifact_id=null，不虚构上游身份。不包含 Entity Map、C4 中间文件、D2、完整工作目录或汇总报告。缺项明确列入 manifest，不能跨 run 补齐。

### 5.3 D2

首屏取 ExpectationsSummary 与第一个正式顺序 Shell 的 units。ShellSummary 的 ordinal 是 D2 O0 最终确定顺序，不按 id/名称排序；成功和失败 Shell 均在原位置。分页路由保留这个顺序。

ShellContent 返回该 Shell 的完整 Unit 业务对象；超过页限时前端在“全部”范围继续加载该 Shell 的后续 Units，不读全 D2，也不预取其他 Shell。State/Factors/Gaps 和单 Unit 切换是对已加载 Unit 本地显示。存在更多 Units 时必须显示继续加载状态，不能把第一页命名为完整全集。

业务 schema 与 `codex_document2/schema.py` 对齐：

- State parameters 的 `value_type` 决定关联 value 的 NUMBER/RANGE/TIME/STAGE/DIRECTION/EVIDENCE 形状；不能因 optional 字段猜类型。每个 parameter_id 必须可解析。
- StateValue 保留 source_role、previous_value、time_scope、as_of、citation、validity_state。
- Factor 保留 structural_role 与 observability.match_condition；Gap 保留 derivation、expected_revision、recognition_criteria。
- StateValue/Factor/Gap 的引用由 citations endpoint 解析；UNRESOLVED 不生成伪 URL，不清空正文。
- failed Shell 返回 failed_stage、failure_kind 和 seed 的问题/边界；只有正式发布产物内可消费的 Units 才返回 PUBLISHED_PARTIAL。原生 seed 单有命题不等于已研究 Unit，单纯工作区 checkpoint 也不等于正式内容。
- D2 bundle.current 不能覆盖 activation 固定引用的 published PARTIAL run；PARTIAL 内成功 Shell 正常展示。

本期无 D1/D2 activate、rollback、rerun、编辑正文接口。

## 6. 交易策略

### 6.1 接口

| 方法 | 路径 | 输入 | 响应 T |
|---|---|---|---|
| GET | `/tickers/{ticker}/policies/context` | V，`limit?` | PolicyContext |
| GET | `/tickers/{ticker}/policies/shells` | V，P | Page<Pick<ShellSummary,shell_id/ordinal/core_question>> |
| GET | `/tickers/{ticker}/policies/metrics` | V，`shell_id` | PolicyMetrics |
| GET | `/tickers/{ticker}/policies` | V，`shell_id,filter`，P | Page<PolicySummary> |
| GET | `/tickers/{ticker}/policies/{policy_id}/revisions/{policy_revision_id}` | `policy_set_version,view_id?` | PolicyDetail |
| GET | `/tickers/{ticker}/policies/changes` | V，`shell_id,policy_id?`，P | Page<ChangeEvent> |
| GET | `/tickers/{ticker}/policy-sets/{policy_set_version}/download` | `runtime_activation_id` | JSON 下载 |

shell_id 必填，可取返回的具体 Shell 或保留字 `ALL`；默认选第一个具体 Shell，ALL 始终在最右，不作为源 Shell 保存。无 Shell 时使用 ALL 空范围。filter 默认为 ACTIVE，值域见 PolicyFilter。所有策略指标跟随 Shell/周期，状态筛选只影响 Policy 列表，不改变 KPI。

PolicySet 下载为 PolicySetDownload，固定为当前页面 activation 对应的完整 `document3.v2.2` JSON，含其原始 source refs 和 published_at；Content-Type=application/json、Content-Disposition=attachment，不套 Response envelope。原生产物内部引用保持原字段，本 API 的 library_snapshot_id 由外层 activation 解析，不改写下载内容。禁止用侧边时间线选择的历史 Policy revision 改变下载目标，也不按 Shell 截断下载。activation 与 version 不一致返回 409。

### 6.2 版本与有效性

`policy_revision_id` 绑定整份规范 Policy 内容，任一正式业务字段实质改变会变化；它不同于 PolicySet version，同一 Policy 原文进入其他 PolicySet 可保留同一 revision。

读取 revision 必须带其所属 policy_set_version，解决同一内容在多份 PolicySet 内出现时的来源上下文。列表用返回的 version；时间线 MODIFY/ADD 用 to_version，RETIRE 用 from_version。无 view_id 为只读历史详情，其 effective/consumed 为 NOT_APPLICABLE，matched_filters=[]；有 view_id 时消费/生效信息严格按该 view，且被选 revision 不在 active 集合时 effective=false，不将历史内容伪装成当前策略。

`policy_activation_revision` 直接采用 `policy_activation_revision()` 当前固定 OR 条件与 calibration 计算结果，不按 full-policy hash 或 runtime_activation_id 重新生成。源算法不包含 title、source_refs、decision、match_scope；这些字段修改是否重新允许消费不能由 API 擅自决定。契约遵守实际消费账本，同时把完整 revision 变化展示在时间线上。

`effective=true` 仅当：属于 view 固定 PolicySet、正式 lifecycle=ACTIVE，且 `(ticker,policy_id,policy_activation_revision)` 未被消费。没有消费账本覆盖证明时 effective 为 UNAVAILABLE，不能判为未消费。当前策略内容与消费状态必须具有同一 view 的水位；历史详情不宣称自己当前生效。

一个 Policy 的 source_refs 可以跨 Shell，相关 Shell 内均可出现，ALL 用 policy_id 去重。D2 Shell/Unit/Gap 身份要在 PolicySet 自身的 document2_ref 中解析，不用最新 D2 强行补关联。

若人工替换导致 active D2 与 PolicySet.source D2 不同，PolicyContext 同时返回 document2 与 source_document2，source_context_consistent=false；PolicyDetail 固定返回自己的 source_document2。只能在当前 D2 中确切解析的 Shell 关联进入 shell_ids，无法解析的进入 unresolved_shell_ids，仍可在 ALL 查看，不能凭相同标题配对，也不能让单项断链清空整个策略页。

### 6.3 KPI 和生命周期

- active、long_active、short_active、long_ratio、short_ratio 是当前 effective 集合，无环比；active=0 时方向比例 NO_SAMPLES。
- added：第一次进入正式生效 lineage 的唯一 Policy；同一身份重新出现为 RESTORE，不伪装第一次新增。
- modified：在窗口内至少一次完整业务内容实质变化的唯一 Policy；retired：窗口内正式失效的唯一 Policy。候选产物、保留原文的重复发布及发布时刻变化不构成修改。
- hit：最终有效判定命中的唯一 policy_id + policy_activation_revision，不仅是实际选中执行的那一个；Runtime 页按 Case 计，Overview 按 Policy 计。
- executed：与所选 Shell 的 Policy 关联且有有效 ENTRY Fill 的唯一 execution_id。W3 专家独立交易 executed_policy_id=null，不计策略执行，可计 Overview/Runtime。
- 生命周期比较基于正式生效版本链或已证明的显式 patch/retire 事实；只比较 current 与某次旧集合不足以证明中间变化。旧历史没有基线时 added/modified/retired 用 coverage，不将缺失前版本视为空集合。
- 时间线逐条 ChangeEvent，MODIFY 后 revision 和 RETIRE 前最后 revision 可直接读取；`summary/changed_paths` 从正式差异确定，不从显示标题猜变更。一个对象同窗可属于多个筛选，matched_filters 明确给出。
- 历史筛选对应 Policy 已不在 current PolicySet 时，返回其最后匹配事件所关联的完整 revision。不能先拿 current 集合再筛，以免丢失已失效项。

## 7. 事件库

### 7.1 接口

| 方法 | 路径 | 输入 | 响应 T |
|---|---|---|---|
| GET | `/tickers/{ticker}/event-library/current` | V | LibraryRef |
| GET | `/tickers/{ticker}/event-library/metrics` | V | EventMetrics |
| GET | `/tickers/{ticker}/event-library/events` | V，`filter?`，P | Page<EventSummary> |
| GET | `/tickers/{ticker}/event-library/index` | V，`filter?`，P | Page<EventSummary> |
| GET | `/tickers/{ticker}/event-library/snapshots/{library_snapshot_id}/events/{event_id}` | `limit?` | EventDetail |
| GET | `/tickers/{ticker}/event-library/snapshots/{library_snapshot_id}/events/{event_id}/facts` | P | Page<FactRow> |
| GET | `/tickers/{ticker}/reference-deltas/days` | V，P | Page<DeltaDay> |
| GET | `/tickers/{ticker}/reference-deltas/days/{semantic_day}` | V，P | ReferenceDelta |
| GET | `/tickers/{ticker}/reference-deltas/{delta_id}/changes/{change_id}/event` | `side=before|after`，P | EventDetail |

Event filter 默认 ACTIVE，可选 ADDED/MODIFIED/RETIRED；只改变列表/索引，不改变 EventMetrics。KPI 一直属于完整 Canonical Library 的周期，不随 Reference Delta 的日期或模式改变。Reference Delta 模式不接受状态筛选。

EventDetail 的 event 等于 CanonicalEvent 除 facts 外所有字段；facts 独立有界页。没有隐式嵌入其他 Event 或整个 Library。price_analysis 为可空只读保留业务数据，不为新 O2 Event生成价格分析。

### 7.2 Event/Fact 生命周期与排序

Event 源 E#、Fact 源 F# 均需 ticker + library_snapshot_id context；临时 T#/TF# 不出现在正式 Canonical 页面。event_key/fact_key 是服务端提供的跨版本稳定业务身份：继承同一创建来源的分支副本保持相同 key，不同分支独立创建但碰巧同号的 E#/F# 必须不同 key。无法证明历史沿袭时不按同号强行合并，相关生命周期统计标 PROVENANCE_UNVERIFIED。Provisional E# 在 Runtime 通过 kind=PROVISIONAL 与语义日/快照单独标识，event_key=null，不能路由成 Canonical Event。

Event 原生 status 是 ACTIVE/SUPPRESSED/MERGED；Fact 自身不带 lifecycle 字段，其生命周期来自正式 fact state 与版本成员关系，API 在 FactRow 显式补充，不能复制 Event status 代替。

KPI 规则：active 统计 current 库中唯一 ACTIVE Event 与仍有效成员的唯一 Fact；added 为首次正式发布；modified 为业务字段/有效成员关系发生实质变化；retired 为 ACTIVE→SUPPRESSED/MERGED，Fact 为不再属于任何有效 Event。跨 Event 移动同 F# 不新增/失效该 Fact。Event Facts 成员变更算 Event 修改，纯成员顺序变化不算 Fact 修改。Reference inclusion、is_important 等正式业务字段改变属于 Event 修改，但 include=false 不等于失效。

周期内多次变化按唯一身份聚合；同一身份可以分别计入 added/modified/retired。RETIRED 列表保留失效前正文和失效状态，不因不在 current active 集合就漏掉。Fact 顺序跟随该次正式 Event revision 的完整成员次序，ordinal 不重新按时间排序。

### 7.3 Reference Delta

原生 ReferenceViewDeltaSnapshot 只有 from/to 版本、文本和 removed_event_ids，不能直接充当结构化日增量。本 API 返回选定语义日已正式提交给 O3 的变更及完整版本身份，day 不由 published_at 猜测。

- add：进入 Reference View；modify：仍在其中且正式内容改变；remove：离开 Reference View。
- add/modify 详情指向 after revision/目标库；remove 指向 before revision/基库，即使当前库已无该 Event，仍能阅读移除前的 Event/Facts。
- 一天多个已提交阶段按该日链条的首基线与最后有效目标合并成净变化，并稳定去重；同日 add 后 remove 的最终净零不生成可见条目。若链条不完整则 PARTIAL/UNAVAILABLE，不能仅拼文本冒充完整。
- review-only 可能 from=to，但 Reference membership 仍发生改变。需要固定的 Reference 前后快照身份与变更事实，不能仅依赖 Library version 差。
- ReferenceDelta 明确 before/after_reference_snapshot_id；变更详情用 delta_id + change_id + side 读取对应快照，EventDetail.reference_snapshot_id 标识该视图，Facts 后续页继续使用同一路径游标。普通 Canonical EventDetail 的 reference_snapshot_id=null。不存在的一侧（add.before/remove.after）返回 NOT_PRODUCED，不改读另一侧。
- 已确认无变化返回 DeltaDay.NO_CHANGE 和 ReferenceDelta 空页；没有执行、正在执行、失败、历史不可用分别表示，不能统一返回空数组。
- 日期清单默认选前一有效交易日；可包含产生正式 Delta 的休市语义日。不预读其他日期正文。

## 8. 消息总线与配置

### 8.1 接口

| 方法 | 路径 | 输入 | 响应 T / 特例 |
|---|---|---|---|
| GET | `/tickers/{ticker}/message-bus/status` | V | BusStatus |
| GET | `/tickers/{ticker}/message-bus/metrics` | V，F | BusMetrics |
| GET | `/tickers/{ticker}/message-bus/sources` | V，P | Page<SourceStatus> |
| GET | `/tickers/{ticker}/messages` | V，F，`q?`，P | MessageBaseline |
| GET | `/tickers/{ticker}/messages/{standard_message_id}/revisions/{revision}` | V，`stream_cursor?` | MessageSummary |
| GET | `/tickers/{ticker}/messages/{standard_message_id}/revisions/{revision}/body` | `cursor?` | ContentChunk |
| GET | `/tickers/{ticker}/messages/events` | V，F，`q?`，`cursor?` | SSE，§11 |
| GET | `/tickers/{ticker}/bindings/{binding_id}` | 无 | BindingConfig |
| PATCH | `/tickers/{ticker}/bindings/{binding_id}` | BindingPatch | 200 Response<BindingConfig> |
| DELETE | `/tickers/{ticker}/bindings/{binding_id}` | 无 body | 200 Response<MutationReceipt> |
| GET | `/tickers/{ticker}/available-api-sources` | P | Page<AvailableSource> |
| GET | `/tickers/{ticker}/available-api-sources/{source_id}` | 无 | AvailableSourceDetail |
| POST | `/tickers/{ticker}/bindings` | BindSourceRequest | 201 Response<BindingConfig> |

F 为 `source_kind?=api|crawler,source_id?,route?=MessageRouteFilter`；缺省表示全部。多个筛选做交集。source_id 不属于当前 ticker 可见历史或与 kind 矛盾时 422，不放宽条件。q 为 trim 后 1–200 字符，空字符串等同无搜索；服务端对标题和完整标准正文作 Unicode casefold 后字面子串匹配，不解释通配符/正则。关键词仅影响消息列表/SSE，不传入 metrics。

### 8.2 消息关联与状态

StandardMessage 本身没有进入流时间，必须通过 StreamMember/StreamItem 取得。Runtime envelope.from_stream_item 的 collected_at 当前取 stream 发布时间；消息卡片采集时间必须回到 StandardMessage.collected_at，不传播这个同名字段的不同含义。

同一 buffered StreamItem 的所有成员都通过 stream_item_id 关联 Case，member_index 保持原次序。即使 member 不是 envelope 的 source_message_id，也必须显示其共享 Case 路由。Case 详情可以显示组合消息内容，单消息正文仍是该 Standard Revision 的完整 body。

服务端给出 route_group：最终已结算 route 优先，其次初始非 W3 route；进入 W3 未结算为 W3_PENDING；不能继续处理的终态失败为 FAILED；未有 Case 为 NOT_PROCESSED。initial_route 始终保留。Route=TRADE 仍只代表流程路径，不能用于成交徽标。NOT_PROCESSED 与失败不能合并。

消息没有 Summary 字段。标题原生为 null 时使用 Value.NOT_RECORDED，前端可显示“无标题”；不把正文截取伪称源标题。展开 body 才读取全文，ContentChunk.text 保留可用段落，未经许可不对源文改写摘要。

### 8.3 KPI、轮询与运行区间

- published_revisions：F 交集内正式 Stream member 的唯一 Standard Revision；bootstrap suppressed、无效输入、精确重复不计。
- body_completion_success_ratio：同一 F/窗口内标准 Revision 所关联的可证明正文补全尝试，成功尝试数/已记录尝试数。未执行补全的原生完整正文不入分母；只知道最终 metadata、无法证明尝试总数时标 PARTIAL/NOT_RECORDED，不推定 100%。重复抓取不能重复计同一次 materialization attempt。
- continuous_run_started_at 取最近连续 RUNNING 区间起点；暂停或停止打断区间，进程重启但运行事实未中断不自动重置。旧 started_at 若不能证明该语义则 NOT_RECORDED。
- source health：全局/Binding/polling 任一禁用或 never_polled → EXCLUDED；enabled 且 succeeded → NORMAL；partial/failed → ABNORMAL。窗口暂未开放不把正常源改判异常。
- 平均轮询延迟是纳入 normal/abnormal 集合的各源最近一次有效 `last_latency_ms` 的算术均值，转换秒；这是轮询耗时，不是新闻时延或调度拖延。null 不补零，返回 latency_sample_count。
- next_target_at 优先表示实际 target_due_at，并遵守暂停、active window、休市轮询计划；next_dispatch_at 如受 scheduler 限制不能直接当业务目标倒计时。无法证明下次目标时 UNKNOWN。
- last_published_count 指最近已完成轮询的正式新增 Standard Revision 数，不能把累计收集数、raw count 或 buffered stream item 数冒充。

### 8.4 Binding 修改边界

只允许修改目标 ticker 的 TickerSourceBinding；不暴露全局 SourceDefinition/profile 修改、爬虫新建/promote/certify/repair、账号或凭证设置。

- API source 返回已适配表单信息及 JSON editor；crawler 仅 JSON 参数编辑。所有 Binding 都提供 target_interval_seconds 表单。
- PATCH 使用“缺字段不变”；显式 null 不代表清除，除 schema 允许的参数值外返回 422。polling、streaming.buffer 按键合并，active_windows 数组整体替换；source_parameters 是可写参数的完整替换，未开放/redacted 路径保持不变，客户端不能通过空对象删除 secret。
- Polling 约束与原生一致：interval≥1；tolerance_ratio 0–0.5；alert_after_seconds≥1；weekdays 为 0=周一至6=周日、唯一且非空；IANA timezone；HH:mm:ss。相同 start/end 表示该 weekday 全天，跨午夜区间属于开始日。
- Buffer：max_items 1–10000，max_wait_seconds≥1，max_compiled_body_chars 1000–120000。参数以返回的 parameter_schema 验证；服务端约束优先，不能只做前端验证。
- 返回实际保存并复读的 effective 与递增 binding_version。当前在途 Poll 使用其冻结旧配置，后续 Poll 使用新版本；不会改写已发布消息。
- 修改期间 SourceDefinition 版本已改变也视为 REVISION_CONFLICT，避免用过期参数 schema 保存。
- 添加候选仅已注册、全局 enabled、适用该 ticker、尚未绑定的 API Source。提交 source_version 固定其 schema/default；已绑定返回409，不隐式覆盖。
- 删除 Binding 是 ticker 范围解除绑定，既有消息、审计与 Case 仍保留；对待发布 buffer 的确定结算结果须完成后再返回成功，不能无记录丢弃成员。

## 9. 运行状态与执行详情

### 9.1 接口

| 方法 | 路径 | 输入 | 响应 T |
|---|---|---|---|
| GET | `/tickers/{ticker}/runtime/metrics` | V | RuntimeMetrics |
| GET | `/tickers/{ticker}/runtime/graph` | V，`limit?` | GraphBaseline |
| GET | `/tickers/{ticker}/runtime/graph/events` | V，`cursor?` | SSE，§11 |
| GET | `/tickers/{ticker}/runtime/nodes/{node_id}` | V，`limit?` | NodeDetail |
| GET | `/tickers/{ticker}/runtime/nodes/{node_id}/cases` | V，P | Page<CaseSummary> |
| GET | `/tickers/{ticker}/runtime/cases` | V，`result?,source_id?`，P | Page<CaseSummary> |
| GET | `/tickers/{ticker}/runtime/cases/{case_id}` | V，`stream_cursor?` | CaseDetail |
| GET | `/tickers/{ticker}/runtime/cases/{case_id}/attempts` | `node=W1|W2|W3`，P | Page<ModelAttempt> |
| GET | `/tickers/{ticker}/runtime/cases/{case_id}/messages` | V，P | Page<MessageSummary> |
| GET | `/tickers/{ticker}/runtime/cases/{case_id}/candidates` | P | Page<Candidate> |
| GET | `/tickers/{ticker}/runtime/cases/{case_id}/executions` | P | Page<ExecutionSummary> |
| GET | `/tickers/{ticker}/executions/{execution_id}` | `limit?` | ExecutionDetail |
| GET | `/tickers/{ticker}/executions/{execution_id}/orders` | P | Page<OrderSummary> |
| GET | `/tickers/{ticker}/executions/{execution_id}/fills` | P | Page<Fill> |

result 值域为 ResultKind，不指定表示全部；source_id 限制消息来源。它们只影响最近记录列表，不改变全页 Runtime KPI/图；节点记录自动加 node_id 的 Case membership 条件。排序为 received_at DESC、case_id DESC。

### 9.2 原生状态与结果

CaseStatus、TechnicalStatus、W3 status 保留当前 V2 枚举，不能套用初始化父状态。`COMPLETED` 是 Runtime effect 结算，不保证 broker 执行/退出已经完成。Case.completed_at 只表示 Runtime 生命周期本次正式完成/终态失败时刻；不能用不断更新的 updated_at 或交易退出时间代替。

CaseSummary.results 是已发生的业务结果集合，可为空或多项：

| ResultKind | 必须存在的事实 |
|---|---|
| ARCHIVE | 已完成 archive record/effect |
| EVENT_DISCOVERY | 至少一个成功持久化且去重后的新 RuntimeFactCandidate |
| BADCASE | 正式 BadcaseRecord |
| TRADE_EXECUTION | 关联执行至少一个有效 ENTRY Fill |
| FAILURE | 不可继续/已终态失败的 Case、W1/W2/W3、effect 或交易阶段；阶段与错误可回溯 |

PENDING_RETRY、W3 等待不提前算最终失败。失败与已发生结果允许共存，例如 R3 候选成功后交易失败。`result_settled` 仅当相关 Runtime 与 ENTRY 执行已结算、没有等待中的结果分支时为 true；真实成交修正可在后续提高 revision 后修正结果。EXIT 失败属于阶段失败，可与已有交易执行共存。

Reasoning 仅返回已持久化、面向业务解释的 W1/W2 `reason` 与 W3 novelty/policy/expert_trade reason，不返回模型内部 token reasoning、system prompt 或整个 Worker transcript。Case 详情给 ContentRef，打开 reasoning 时读取 contents；消息 body 同理按需。

W1 原生最终只给 E# reference_ids，没有已判定 F# 时 references.fact_ids=[]，不创造“命中 Fact”。W3 对应引用也保留真实粒度。provisional 与 canonical 通过 EventLink.kind/快照上下文区分。

局部固定引用缺失不使整个 CaseDetail 返回404：保留可读取的判断、轮次、正文和固定版本，相关 W1/W2/W3 Resource 标为 PARTIAL，reason=UNRESOLVED_REFERENCE、coverage 含 PINNED_ARTIFACT_MISSING。无法解析的原始身份通过可选 `unresolved_reference_ids` / `unresolved_policy_ids` 保留，不伪造 EventLink/PolicyLink，不换用最新版本。缺少整个激活 pin 的情况仍保留正式错误边界。

### 9.3 KPI 与耗时

- processed_cases：窗口 Case cohort 的唯一 Case，包含在途及失败；其余完成型指标只计有该事实者。
- hot_path_mean_seconds：已完整结束 W1/W2 并行阶段的 Case 墙钟平均耗时。W1/W2 R1→R2 的并行区间取最早开始到最后必需结束，不相加；W1-R3 不在该阶段。按 PRD 的“并行阶段”口径，仅 first_round_shape=PARALLEL 的 Case 入该均值；CLOSED 的 SEQUENTIAL/W1_ONLY 不冒充并行样本，详情仍返回真实 Timing，排除数量在 coverage 中明确。失败或无可靠区间另标覆盖缺口。
- w3_mean_seconds：已结算 Case 的 W3 从首个真实 Worker attempt 开始至最终 attempt 结束墙钟时长，含中间实际等待；不能把每次 Worker 时长相加。未结算者不入平均分母。
- new_cases/old_cases：最终有效 novelty；W3 已结算覆盖 W1。进入 W3 未结算不提前以 W1 加入最终 NEW/OLD。
- hit_cases：最终有效 W2/W3 判定命中至少一个 Policy 的 Case 数；hit_case_ratio 分母是已形成最终有效 Policy 判定的 Case 数，无判定/跳过者不冒充未命中。
- w3_cases：实际进入 W3 的 Case；new_fact_candidates 为 W1-R3/W3 已持久化的稳定唯一候选，不把 NEW Case 数直接当候选数。
- executed_cases：存在真实有效 ENTRY Fill 的唯一 Case，Paper/Live 合并，同 Case 多 Fill 只计一次。
- Timing 必须标 basis。原生 RuntimeModelTurn.created_at/latency_ms 只有在确认写入时刻与请求结束语义一致时可派生起止；不能直接把本次 `_resume_hot()` 的 hot_path_latency_ms 当跨进程恢复后完整阶段时长。不可重建则 NOT_RECORDED。
- attempts 显示实际 round/attempt 顺序与状态，成功回执恢复不产生新 attempt；新外部请求重试才新增。

### 9.4 图与节点

固定 NodeId 为 SOURCE、W1、W2、W3 及五类结果。REALTIME 的 W1/W2 并行；CLOSED 根据 first_round_shape 表达顺序边或 W2 跳过，不制造 W2 节点处理记录。边表示 Case 实际经过的关联，非互斥流量守恒图。同 Case 可同时进入多个结果，因此结果节点计数之和可大于 SOURCE。

基础图返回固定拓扑、节点/边计数、有限最近 Case 摘要与 stream_cursor，不传 Case 全史。NodeCounts.case_count 为该节点实际涉及的唯一 Case；failed_case_count 为该节点终态失败的唯一 Case；W1/W2 返回 low_confidence_case_count，其他节点为 null。W3 result_counts 为最终流向各结果的唯一 Case 数。

SOURCE 不凭未创建 Case 的原始消息来增加 Case 图数量。Event discovery 图节点仍按 Case 计，其 KPI 按 Candidate 计，二者不能混用。

## 10. 交易事实与 Overview 净收益

ExecutionSummary 明确 intent/intake/entry_result/Fill，OrderSummary 保留本地 state 与 broker_status 两套事实。order/ref/profile/account 不可作为前端成交推断规则；接口不暴露明文 broker account ID、host、port 或 client credential。

- `has_actual_fill` 从有效 broker executions 得出。entry_result=FILLED 但 Fill 尚未持久化时不能进入 KPI，按数据未同步表达。
- Fill 稳定身份是经过账户作用域处理的不透明 ID，去重源键为 account+exec_id；同 execution family 的 correction 只保留有效最高修订，修正/撤销会更新对应成交数量和统计，不能重复累加。
- Order 的多个重试、部分成交、迟到 Fill 不改变 execution_id；ExecutionSummary 承认 partial fill。DIRECTION_DISABLED、EXPIRED、REJECTED、UNKNOWN 或无 Fill 不计交易执行。
- 正式触发必须有可证明的首次释放时间；现有只保留 READY 状态但无 released_at 的历史不能靠 expires_at 减一天伪造。被 closed-cycle selection 淘汰的 Candidate、重复 Policy 抑制不计正式触发。
- 已实现收益仅从 `leg=EXIT` 的实际退出 Fill 与 DoxAgent 所属份额计算，部分退出按已退出量；未退出浮盈、其他账户持仓、无成交退出不计。
- 成本基础从该归属份额对应有效 ENTRY Fill 的可证明匹配成本取得；LONG 为退出收入减进入成本，SHORT 为进入收入减回补成本。扣已取得且归属明确的进出佣金；佣金未返回则 provisional=true、coverage reason=COMMISSION_PENDING，不虚构佣金为真实零。
- 归属账本不完整、价格/汇率缺失或 BROKER_HISTORY_GAP 时金额不可确认部分必须隔离。没有任何 EXIT 且完整覆盖时可确认 0。
- 现有 ENTRY 的反向 FIFO_OFFSET 不伪装成 PRD 规定的 EXIT leg；该部分若需纳入财务收益必须另行变更产品口径。本合同的 Overview 严格按 EXIT Fill 统计。
- 收益历史修正重算原 EXIT 语义日；Paper/Live 依据每次 execution 的冻结 profile，不能依据今天 active profile 分类。

本期没有 broker 下单/撤单/改单、profile 管理、收益审计正文 API；这些交易接口都是业务只读。收益审计入口使用 capabilities.revenue_audit=false，前端不读取收益明细。

## 11. SSE、分钟读取和缓存

### 11.1 原子基线与流语义

SSE 仅用于消息流和 Runtime 图。`text/event-stream; charset=utf-8`，禁止缓存；建立连接不隐式返回全量历史。首次列表/图响应中的 stream_cursor 必须与基线同一快照边界，覆盖基线读取与 SSE 建连之间发生的变更。

事件格式：

```text
id: <opaque resumable cursor>
event: message.delta
data: {"event_id":"…","stream_cursor":"…","view_id":"…","scope_key":"…","sequence":"…","emitted_at":"…","payload":{…}}

```

| event | payload | 客户端应用 |
|---|---|---|
| `message.delta` | MessageDelta | 按 standard_message_id + revision / row_revision 幂等插入或替换；REMOVE 移出当前筛选 |
| `graph.delta` | GraphDelta | 校验 previous_graph_revision，原子应用受影响 Case/节点/边；计数是替换值，不是可重复累加的 +N |
| `stream.reset` | StreamReset | 停止旧流，只重建受影响消息首屏或图基线 |
| `scope.rolled` | StreamReset，reason=SCOPE_MISMATCH | 当前语义周期已变化，重建对应新范围 |

服务端 heartbeat 用 SSE comment（例如 `: keepalive`），不包含业务查询或推进业务游标。

### 11.2 恢复、过滤与有界状态

- 采用至少一次投递；客户端按 event_id 去重、按 revision 防止旧消息覆盖新消息。sequence 只在该流排序，不能转换为 JS 不安全整数或当作跨库业务版本。
- 首次使用 baseline.stream_cursor；重连通过 Last-Event-ID，cursor query 只在首次设置。二者同时出现但不一致返回400。
- cursor 固定 ticker、过滤条件（含 q）、period/view 的窗口边界、授权范围和流类型；修改筛选须获取该范围自己的基线，不沿用另一范围游标。
- 服务端至少保留 24 小时可重放事件。保留期内无漏收；超过保留期明确 reset，不声称永远可恢复，也不让客户端无限尝试过期游标。
- MessageDelta 的 matches_scope 由服务端判断，支持完整正文关键词而不发送正文。新符合范围者 UPSERT；路由变化后不再符合者 REMOVE。Row 包含卡片摘要，禁止 body 文本/summary/reasoning。
- 首屏移位不是筛选失配，不发送 REMOVE。增量按发生变化的稳定身份判断前后是否符合范围，覆盖已加载后续页中的身份和路由更新；不扫描/重发全列表。升级前采用首屏语义的旧 SSE 游标按 CURSOR_EXPIRED 失效，重新获取基线。
- 超过单 SSE event 字节上限时 UPSERT 可以 row=null，只带已验证的身份、版本和 matches_scope，客户端最多按该单条身份补读摘要；REMOVE 的 row 必须为 null。不得以此为由重读列表。
- Row revision 与 Standard revision 分开：基线 MessageSummary 和 MessageDelta 都携带 row_revision，同一不可变消息关联的 Case 状态变化递增 row_revision；Standard revision 本身不因 Case 更新而变化。delta.row 非 null 时其 row_revision 必须与外层一致。
- ReadContext 的 as_of 是初始基线时刻；SSE 在保持该窗口与查询条件的同时推进自己的水位，不修改旧 Page 的快照。补读已应用事件对应的消息/Case 时携带该流最新已应用 stream_cursor，服务端据其水位返回详情，并在 meta.as_of 表达新水位。不能因为消息晚于初始 as_of 而404，也不能混入尚未应用事件的后续状态；不带 stream_cursor 的请求仍按 view 基线。
- 消息 SSE 只维护当前首屏的最多 limit 条；更新较旧页的已缓存身份时可按 key 标 stale，不整体补读历史。继续加载使用初始快照 cursor 并按稳定身份去重，不拿 SSE 新头重排旧分页。
- GraphDelta 带替换后的计数及最多受影响的有限 Case；每批原子从 previous_graph_revision 到 graph_revision。版本断档时只重建图基线，不去重新读全部记录/KPI。图只保留已声明的最近 Case 页，多余 Case 通过 remove_case_ids 逐出有限可见集合。
- SSE 故障不触发 KPI、配置、全文、完整图或全部列表重读。自动重连采用指数退避 1–30 秒及抖动；401 先认证，403停止；持续错误显示一次业务提示。

### 11.3 缓存与更新矩阵

| 页面/组件 | 自动读取 | 首次/人工/未缓存范围 | 详情 |
|---|---|---|---|
| Overview | 无 | ReadContext + status + metrics + ticker 首屏 | 初始化按需；不预取隐藏 ticker |
| D1 | 无 | summary + 默认 C1 | 其他 section/历史/下载 |
| D2 | 无 | summary + 首 Shell units | 其他 Shell/Unit/历史 |
| Policy | 无 | context + metrics + 默认 Shell policy 首屏 | 选中 Policy/时间线/下载 |
| Event Library | 无 | current ref + metrics + 当前事件首屏 | Event/Fact/索引后续页/Reference Delta 日期 |
| Message Bus status/metrics/source 状态 | 可见时真实分钟边界 | 当前 F 范围 | 配置、参数 schema、正文 |
| 消息流 | 可见时 SSE | 首屏原子基线 | 后续页/正文 |
| Runtime metrics/records/已打开节点 | 可见时真实分钟边界 | 当前组件 | Case/attempt/reasoning/交易 |
| Runtime 图 | 可见时 SSE | 图基线 | 节点/Case |
| Cost | 无 | 当前 scope/周期/筛选的聚合 | 未选 scope、后续节点页 |

分钟边界由 clock.next_minute_at/服务器时钟驱动，不能从 mount 时另算60秒。页面不可见时停止分钟读取并断开 SSE，恢复可见先显示缓存；SSE 按游标恢复，分钟模块等下个真实分钟边界。普通页面返回、focus、网络恢复均不自动刷新。

缓存键至少包含用户授权范围、ticker、page、period/window、筛选、历史 run/version、对象身份与分页范围。已加载 Unit/正文折叠再打开使用缓存。变更成功仅定向失效实际受影响范围，不全局清空。

GET 可返回 ETag；If-None-Match 匹配时304不带 body。304 不允许以扫描 Supabase 全表/拉取大文档计算 ETag。前端本标签页内存缓存是主刷新策略；服务端 ETag 不构成自动轮询授权。

## 12. 成本审计

### 12.1 接口

| 方法 | 路径 | 输入 | 响应 T |
|---|---|---|---|
| GET | `/tickers/{ticker}/audit/cost/filters` | V，`scope,dimension?`，P | AuditFilters |
| GET | `/tickers/{ticker}/audit/cost/summary` | V，C | CostSummary |
| GET | `/tickers/{ticker}/audit/cost/trend` | V，C，`max_points?` | CostTrend |
| GET | `/tickers/{ticker}/audit/cost/breakdown` | V，C，`dimension=NODE|MODEL,limit?` | CostBreakdown |
| GET | `/tickers/{ticker}/audit/cost/nodes` | V，C，P | Page<CostNodeRow> |

C 为 `scope=API|CODEX,node_id?,provider?,model_id?`；scope 必填，前端默认 API。model_id 必须与 provider 一起出现，provider 可单独过滤。节点和模型筛选影响本页全部模块；filters 返回当前 ticker/period/scope 内真实有用量的候选，与当前 node/model 选择无关，避免互相消失。filters.dimension 可取 NODE/MODEL，不指定时均给各自首屏，分页时必须指定单一维度且另一 Page 返回空页。

trend.max_points 默认100，范围1–200；服务端选择 SEMANTIC_DAY/WEEK/MONTH 的最细可容纳粒度，不截掉较旧数据冒充整个窗口。bucket start/end 为 UTC；不得用前端对原始调用聚合。N 日窗口的桶只聚合窗口成员日；ALL 可含休市日。

breakdown.limit 默认10，最大20；其余汇入 other。API value 为可计价 USD 成本，CODEX 为 token；ratio 分母为该筛选的可计价总成本/有效总 token。分母零时 Resource.EMPTY，而非伪造占比。unpriced 不进入“其他成本”。

### 12.2 用量证据与分域

业务节点名称从已验证的 V2 调用 lineage 产生，至少覆盖 Global Research 的 C4/C1/C3/C5、D2 O0/O1、CDECR 真实模型阶段、O2、O3 初始化/维护、O4 CONFIGURE/DELIVER、W1 R1/R2/R3、W2 R1/R2、W3/休市选择实际模型调用。没有模型请求的 deterministic 节点不能为了填图制造用量。

API 与 CODEX 依据真实调用渠道分类，不按模型名或节点名猜测。Worker job 累计 usage 与单 turn 重放不得双计；一个底层调用同时被通用 recorder/turn 文件记录时，只认一个稳定调用身份。真实失败重试有用量则计，无 usage 则未知。

源 ModelUsageEvent 和 WorkerTokenUsage 的默认0无法证明 provider 实际返回0；历史需要原始 usage/回执证据。缺 cached input 不从 input、pricing 或“无缓存字段”猜0；不能假设 qwen 与 deepseek 的相似名称是同一模型。

### 12.3 数量、成本与覆盖

`total_tokens=input_tokens+output_tokens`；cached 是 input 子集，必须 0≤cached≤input。reasoning_output_tokens 如已包含在 output 不再次加总。违反关系的行 INVALID_USAGE，不污染总量。字段缺失时可保留已知分项，但总量、均值和缓存比例是否可得分别判断。

UsageTotals.requests 统计该范围所有可证明真实调用身份（含真实失败/重试）；average_total_tokens_per_request 只有全部已计请求的 total 已知时可完整求值，否则只保留带 PARTIAL coverage 的已知样本均值，且不能标签为全部请求均值。本版选择缺失任何 total 时该均值 UNAVAILABLE，避免混淆分母。

UI 六项 KPI 使用 total_tokens、total_cost_usd、input_tokens/input_cost_usd、output_tokens/output_cost_usd、cached_input_tokens、cache_hit_ratio。API 返回原始 token 数，不预先除1M四舍五入；前端按 M 显示三位小数。美元内部计算保持精度，显示时才四舍五入。成本本期无强制环比，Metric.previous/change_pct 为 NOT_APPLICABLE。

固定价格版本 `frontend-fixed-20260907` 来自产品需求，是本产品审计口径，**不是本次查询的供应商现行报价**：

| provider | 精确 model_id | Asia/Shanghai 时段 | 非缓存 Input CNY/1M | Cached Input CNY/1M | Output CNY/1M |
|---|---|---|---:|---:|---:|
| bailian | qwen3.8-flash | 全天 | 0.8 | 0.1 | 2.7 |
| bailian | deepseek-v4-flash-0731 | [08:00,22:00) | 3.0 | 0.3 | 9.0 |
| bailian | deepseek-v4-flash-0731 | [22:00,次日08:00) | 1.5 | 0.15 | 4.5 |

按真实调用开始时刻判断时段，不能用回执写入/重读时间。provider 的供应商同义来源必须先经明确登记规范化为 bailian，其他 provider 不自动套用价格。

```text
noncached_input = input_tokens - cached_input_tokens
input_cost_cny = noncached_input * input_price / 1e6
                 + cached_input_tokens * cached_price / 1e6
output_cost_cny = output_tokens * output_price / 1e6
total_cost_usd = (input_cost_cny + output_cost_cny) / 6.8
```

缓存计价包含于 input_cost，不在 total 重复加。cache_hit_ratio 用 Σcached/Σinput，不平均各行比率。

未知精确 model_id 仍计有效 token，成本 NOT_APPLICABLE/UNKNOWN_MODEL_PRICE。cached 缺失导致 input/total 无法完整计价，但已知 output 可单独计价；total_cost 只包含可完整计价的调用，unpriced_request_count 说明排除量，coverage=PARTIAL。分项与完整总额不再可直接核对时必须据 coverage 标明，不将部分 output 冒充完整请求成本。

CODEX 所有美元成本与周额度映射 NOT_APPLICABLE/CODEX_SUBSCRIPTION_NOT_PRICED，仅展示真实 token。API/CODEX 切换时整页数据同 scope，不复用另一 scope 的缓存值。

CostNodeRow.models 为该范围内真实出现的模型集合；无“通常模型”的静态猜测。不提供逐调用明细接口，因为本期前端没有该需求。

## 13. Egress 与发布验收条件

### 13.1 强制读取边界

以下是 API 可验收的行为约束，不是后端实现方案：

1. 摘要、KPI、状态、配置摘要不得附带完整 payload、report、reasoning、source raw_payload、execution bundle 或无关集合。
2. 为计算局部指标不得从 Supabase 拉完整行/完整历史到应用再过滤。请求需限定来源、ticker/授权范围、字段及时间/对象/游标；跨对象语义只交付该页面需要的有界结果。
3. ALL KPI/趋势获得聚合结果，不向客户端或中间读取路径搬运全量大 payload。没有满足该读取边界的聚合能力时返回 CAPABILITY_UNAVAILABLE，不能偷偷退回全表读取。
4. SSE 恢复必须走有界增量事实，不用固定间隔扫描所有 Case/Source/全文重构；未变化的分钟组件不能引发无关 Supabase 集合读取。
5. 详情按稳定对象、run/version 命中；下载仅点击时读取。有多个组件共享同一 summary 时合并请求并共享缓存。
6. 冷首屏不得预取所有 tab、历史、周期、ticker、Source 参数或 Policy 全文。读单 Event 不拉整个 Library；读单 Shell 不拉整个 D2。
7. Byte/行/次数预算同时适用于缓存未命中路径；前端响应很小不代表上游 Egress 合格。本地 SQLite 也要有界输出与版本隔离。

Supabase 官方说明 Egress 包括数据库向连接客户端传出的数据，减少字段/条目与不必要请求是其优化措施；因此不能仅在最后的 HTTP 返回时裁剪大对象来声称节省 Egress。[官方 Egress 文档](https://supabase.com/docs/guides/platform/manage-your-usage/egress)

### 13.2 尚未具备的能力与目标返回

| 能力缺口 | 缺口存在时契约行为 | 不能冒充的事实 |
|---|---|---|
| ticker 级 Paper/Live | capabilities=false、MODE_UNAVAILABLE | 全局 active profile=某环境不证明该 ticker 模式 |
| 持久 removed / 暂停统一边界 | 操作不可成功；503 或明确 pending/失败 | 仅当前浏览器隐藏不是删除成功 |
| 初始化完整失败集/步骤时间 | 真实集合；时间 NOT_RECORDED | 云端前10失败节点、updated_at 不是完整进度 |
| D1 独立更新时间 | NOT_RECORDED | run 状态变化/发布时间不等于内容修改 |
| D2 failed Shell 阶段内容未正式发布 | Shell 失败保留，内容 NOT_PRODUCED | seed/checkpoint 不是完整 Unit |
| Policy 生命周期/完整 hit 历史 | 相应 metrics PARTIAL/UNAVAILABLE | 消费记录不代表全部 hit、current 差集不是时间线 |
| Reference Delta 结构化变更/前镜像 | PARTIAL/UNAVAILABLE | 文本+removed IDs 不是可读完整增量 |
| Case 完整时间/消息成员关联 | 关联/耗时 NOT_RECORDED 或 UNAVAILABLE | 最后消息身份、updated_at、当前恢复耗时不能替代 |
| UI SSE durable cursor | 对应 capability 503，尚不能通过实时验收 | 内部 worker event sequence 不是跨 Case UI 流 |
| 全 V2 usage/缓存证据 | 分项可用、coverage 不完整 | 默认0、旧价格、SDK累计量重计 |
| triggered_at/佣金归属/净收益 | 字段不可用或暂估、BROKER_HISTORY_GAP | intent时间猜测、外部持仓和未实现盈亏 |

这些是接口交付所需结果能力，不是后端工作计划；不得因允许返回缺失就宣称相关前端业务已完整上线。

### 13.3 联调验收用例

1. 只有其他 workflow 数据而无 V2 active：current 返回缺失，历史/Overview 不混入其他代数据。
2. 激活 A 后发布候选 B：同一 view 的 D1/D2/Policy/Event 仍固定 A，刷新得到新 active 才切换；历史 section 同 run。
3. runtime_activation_id 改变但 Policy 条件不变：policy_activation_revision 保持，已消费不恢复生效；full revision 与边界 revision 可独立变化。
4. D2 PARTIAL 含成功与失败 Shell：原始顺序稳定，成功 Units 可读，失败详情保留，父不因质量注解误判失败。
5. 并行 d1/cdecr 和多个失败节点：六步归并正确、耗时不相加，resume 不重跑已成功节点；失败超过10也完整恢复。
6. buffered 20 条消息只生成一个 Case：20个正式 Revision/1个Case；全部 member 得到相同 Case 路由，正文保持各自版本。
7. 精确重复采集、provider实际修改生成 Revision：前者不增加周期新增，后者增加；关键词不改变 KPI，F 交集一致。
8. W3 pending→resolved 改路由：同一消息状态增量更新/移出筛选，最终 novelty/policy 以 W3 覆盖；不提前填 completed_at。
9. TRADE intent、EXECUTION_ACCEPTED、委托无 Fill：执行 KPI=0（完整来源前提）；部分 Fill=1；多 Fill/迟到/修正不重复计。
10. 同 Case 有事件发现、归档和 Badcase：结果集合/图各节点准确；候选 KPI 按候选计，图按 Case计。
11. 未退出、部分退出、佣金缺失与 Fill correction：Overview 净收益分别不计/按份额/暂估/修正原归属日；Paper/Live冻结分类。
12. 02:00、DST、提前收市、休市、7/30有效交易日、ALL、窗口不足与上一值零：时间上下文和不可比原因一致。
13. 同 Policy 跨 Shell：各相关 Shell可见、ALL只一条；Retire 后历史筛选仍可读最后 revision。
14. Reference remove 在 current Canonical仍ACTIVE：Delta显示移除前内容，但不凭此计Canonical失效；from=to 的review变化不丢失。
15. SSE 在基线与建连间有新增、重放重复、丢包/断档、24小时内重连、游标过期：分别无漏/幂等/局部重建；不全页拉取。
16. 分页中出现新消息与路由变化：旧快照翻页无重复错位；新首屏独立维护；不把旧 cursor 套到新筛选。
17. 页面隐藏/返回、整分钟、人工刷新失败：遵守 §11.3，保留旧缓存，无 mount/focus 额外轮询。
18. binding If-Match 过期、幂等重试、跨 ticker ID、source_version变更：412/相同结果/404/412；不修改全局 Source。
19. mode 尚为全局能力：不能成功创建 ticker 级 Paper/Live；删除后重开页面仍不显示；历史保留。
20. API/CODEX切换、未知模型、missing cached、input=0、失败重试、重复回执：分域/不估价/不补零/无样本/真实计数/不重计。
21. 截获首屏/分钟/SSE/详情所触发的实际来源读取：满足字段、时间、行数和字节边界；没有为小DTO读取整表/整份大产物。

## 14. 前端需求追溯

| 数据需求章节 | 本契约章节/主要类型 |
|---|---|
| §2 隔离与身份 | §1、§2、§3；Activation、DocumentRef、PolicySummary、MessageKey |
| §3 时间与环比 | §3；ClockContext、PeriodContext、Metric |
| §4 状态/空值/分页 | §2；Value、Resource、Coverage、Page |
| §5 全局导航 | §4；TickerNavigation |
| §6 Overview/初始化/管理 | §4、§10；OverviewMetrics、TickerOverview、InitializationProgress、Operation |
| §7 基础投研 | §5；ResearchSummary、ContentChunk、FutureNodeRow |
| §8 预期研究 | §5；ExpectationsSummary、ShellSummary、ExpectationUnit |
| §9 交易策略 | §6；PolicyContext、PolicyMetrics、PolicyDetail、ChangeEvent |
| §10 事件库 | §7；EventDetail、EventMetrics、ReferenceDelta |
| §11 消息总线 | §8、§11；MessageSummary、BusStatus、BindingConfig、MessageDelta |
| §12 运行状态 | §9、§10、§11；CaseDetail、RuntimeMetrics、GraphDelta、ExecutionDetail |
| §13 收益/成本 | §10、§12；UsageTotals、CostSummary、CostNodeRow |
| §14 更新缓存 | §11.3；ReadContext、Meta、StreamEnvelope |
| §15 Egress | §2.4、§13.1；有界列表/聚合/正文拆分 |
| §16 能力缺口 | §1.2、§13.2 |
| §17 验收 | §13.3 |

后续开发以本文与类型文件的同一 contract_version 为依据。若改变本版已裁定的业务语义，先形成契约修订；不通过复用旧字段或静默默认值绕过。

### 2026-09-08 Overview 筛选补充

`GET /overview/tickers` 支持可选 `run_state=INITIALIZING|RUNNING|PAUSED|STOPPED` 与 `health=NORMAL|DEGRADED|BLOCKED|UNKNOWN`。省略表示全部；两者取交集，仅影响标的列表。服务端在 view 固定候选集上先筛选后分页，游标绑定这两个筛选；跨筛选复用游标返回 INVALID_CURSOR。未传参数的已有调用保持兼容。
### 2026-09-08 Runtime 节点路径补充

`NodeDetail.path_edges?: GraphEdge[]` 返回当前 ticker、view 和完整周期内实际经过该节点的 Case 所贡献的边及去重 Case 数，最多为固定节点集合的边数量。它不从最近 Case 分页或全局边做可达性拼接。节点点击及分钟详情读取同时取得此字段，前端据此高亮；缺少该字段时不推导替代路径。`/runtime/nodes/{node_id}/cases` 用于节点记录续页。
