# 基础预期指标采集重构方案

> **2026-08-20 当前执行口径：** 下文较早的单一 Document 1 collection 顺序仅作设计历史。
> 运行时现按 lane 拆分 registry：Global 只采集 C1/C5 所需 program targets，Market
> Situation 只采集 C2/O4 targets；新命名中旧 O4-A 为 C5，旧 O4-B 为 O4，Global 不再
> 执行 C4 finalization。以 [`global_market_research_lane_rearchitecture_plan_20260820.md`](./global_market_research_lane_rearchitecture_plan_20260820.md)
> 为准。

## 一、板块定位

基础预期指标采集不负责生成 `Expectation Unit`，也不负责提前构造完整的 `Expectation State`。

它负责在 Shell / Unit 尚未形成前，按照较为固定的指标清单，为四个上游研究板块采集一批通用的基础状态，包括：

```text
公司实际经营与财务状态
公司管理层预期
卖方一致预期
行业与产业链状态
宏观与金融环境
估值、期权与仓位状态
```

符合提升条件的采集结果使用新版下游方案中的正式对象：

```text
StateParameter
StateValue
```

工具原始结果、辅助市场基准和 Agent 文本提取先保留为带 provenance 的 Observation；它们不是另一套状态对象，只有通过身份、期间、单位、来源角色和质量校验后才可提升为 StateValue。

基础预期指标采集新增五项能力：

```text
1. 固定的必填指标与可选指标 Registry
2. 以 collection target 为粒度的采集配置
3. collection-target 级采集结果 Manifest
4. 确定性程序与 Agent 的采集职责划分
5. Observation 到 StateParameter / StateValue 的受治理归一与提升
```

基础指标池不是某个 Unit 的最终 State。下游仍然根据具体的 Shell、Unit 和投资命题，从中选择相关状态，并补充法律案件、监管审批、特定产品验证等专属参数。

---

# 二、确定性程序优先原则

在开发该部分之前，必须先调查现有数据源和 Tools 是否能够通过确定性程序稳定提取该指标。

## 1. 调查内容

每个指标需要验证：

```text
数据源是否存在
字段定义是否稳定
时间范围是否明确
单位是否能够统一
财务期间是否能够归一
历史数据是否能够持续获得
缺失值是否能够稳定识别
计算公式是否能够确定
不同公司之间是否存在明显口径冲突
```

## 2. 采集职责划分

完成调查后，每个 `collection_target` 必须声明一种当前可用性/编排模式：

```text
PROGRAM
由确定性程序直接提取或计算。

AGENT
无法由程序稳定获取，需要Agent从财报、电话会、
研究材料或其他文本证据中提取。

UNAVAILABLE
当前没有稳定数据源，也不适合要求Agent持续搜索。
暂时保留 target 定义，但本版本不执行采集。
```

确定性程序能够稳定提取的 target：

* 不再交给 Agent 对同一 `source_role + time_scope` 重复查找；
* 由程序直接生成标准化结果；
* 在 Agent 开始研究时作为输入提供；
* Agent 只负责解释这些数据，不负责重新计算。

Agent 主要负责其他明确声明为 `AGENT` 的 collection targets，例如：

```text
管理层指引及其变化
卖方预期及分歧
非标准化经营KPI
行业和产业链状态
阶段、方向和证据型状态
确定性数据无法表达的口径解释
```

## 3. Collection-target 路由

`metric_id` 只负责定义“是什么指标”，不能单独承担执行路由。实际部署按以下链路路由：

```text
collection_target_id
→ metric_id + source_role + time_scope
→ collection_mode + tool_name
→ output_policy
```

同一 `metric_id` 可以有多个 collection targets。例如 `fin_revenue` 可以分别采集：

```text
ACTUAL + 最新季度 + PROGRAM
MANAGEMENT + 下一季度 + AGENT
SELL_SIDE + 下一财年 + PROGRAM
```

`PROGRAM`、`AGENT`、`UNAVAILABLE` 是 target 的编排与实际可用性元数据，不是路由键。禁止以单一 `metric_id -> PROGRAM/AGENT` 覆盖多个来源角色或期间；也禁止程序和 Agent 对完全相同的 target 各生成一套竞争结果。

---

# 三、必填与可选的定义

## 1. 必填指标

必填表示：

> 系统在每次运行中都必须为该指标实例化适用的 REQUIRED collection targets，并逐 target 解析一个满足 freshness policy 的结果：可以复用仍有效的缓存，也可以执行新采集，但不能静默跳过。

必填不表示结果必定非空。

必填 target 允许以下结果：

```text
FILLED
PARTIAL
EMPTY
NOT_APPLICABLE
FAILED
UNAVAILABLE
```

### `FILLED`

获得满足质量门槛的可靠数据。只有 target 的 `output_policy` 允许时，才生成或更新对应的 `StateParameter` 和 `StateValue`；纯市场基准或辅助证据可以只生成 Observation。

### `PARTIAL`

同一 target 请求多个期间、标的或数据项时，仅部分获得可靠结果。只为成功项保留 Observation 或 StateValue，失败项不得用零值、空区间或占位状态补齐。

### `EMPTY`

已经完成既定的程序查询或主要数据源查询，但没有获得可靠结果。

### `NOT_APPLICABLE`

该指标不适用于当前公司或资产类型。

例如：

* 银行通常不存在具有可比意义的毛利率；
* 前商业化公司可能没有收入；
* 无期权标的不存在期权隐含波动率。

### `FAILED`

已存在生产采集路径，但本次因认证、限流、网络、Schema drift 或计算异常未完成。`FAILED` 与“查询成功但没有数据”的 `EMPTY` 必须区分。

### `UNAVAILABLE`

当前尚无生产可用的 provider/tool、订阅权限或受治理计算方法。它描述实际可用性，不参与指标路由，也不要求 Agent 临时上网兜底。

必填 target 为 `EMPTY`、`NOT_APPLICABLE` 或 `UNAVAILABLE` 后不要求继续死磕搜索。`FAILED` 应进入运行告警与可重试队列，但单个 target 失败不应无限阻塞其他指标和研究报告。

## 2. 可选指标

可选指标表示：

> 指标属于预先定义的候选枚举，但是否采集取决于其对当前标的的适用性、重要性和数据可得性。

Agent 不需要逐项搜索全部可选指标。

当某项可选指标满足以下条件时，可以采集：

```text
与当前公司的主要业务模式相关
在公司或行业研究材料中具有较高出现频率
能够解释收入、利润、现金流或业务兑现
存在可靠数值、阶段、时间、方向或证据状态
与已有指标不存在明显语义重复
```

可选指标必须使用预定义 `metric_id`。只有确实无法映射时，才允许提出新的指标候选。

---

# 四、Collection Target Schema

Metric Registry 定义指标语义；Collection Target Registry 定义实际采集目标。两者是受版本管理的配置，不直接作为 Document 2 核心业务对象，但其版本必须写入运行审计。

```yaml
collection_target_id: string
metric_id: string
requirement: REQUIRED | OPTIONAL
source_role: ACTUAL | MANAGEMENT | SELL_SIDE | INDUSTRY_CHAIN | MARKET_IMPLIED
collection_mode: PROGRAM | AGENT | UNAVAILABLE
tool_name: string | null
```

字段定义：

```text
collection_target_id
一次可独立执行、独立判定成功或失败的采集目标 ID。

metric_id
固定的指标定义代码，用于不同运行、Agent和数据源之间的语义归一。它不是执行路由键。

requirement
必填或可选。

source_role
目标 StateValue 的来源角色。Observation-only target 也要声明其证据性质，防止后续错误提升。

collection_mode
当前采用程序、Agent，或当前不可用。仅用于编排和可用性说明。

tool_name
PROGRAM target 使用的语义 tool；AGENT 或 UNAVAILABLE 时为空。

```

格式、单位、适用对象和 freshness 规则由 Metric Registry 定义；provider fallback、缓存和方法版本由 tool/runtime 管理，不重复塞进 Collection Target。Agent 不接收完整路由配置，只接收指标含义、时间范围和可用采集结果。

同一 metric 可能同时存在多种 `source_role` 和 `time_scope`，因此这些字段必须在 collection target 中显式声明，不能再由指标所在板块隐式推断。

## 1. 归一化采集 Observation

provider/tool 原始返回不能直接写入 StateValue。归一层至少形成以下内部对象：

```yaml
collection_target_id: string
item_key: string | null
value: scalar | range | series | object
as_of: datetime
source_refs: [ObjectRef]
```

`metric_id`、`source_role` 和 `time_scope` 从 Collection Target 继承；单位与格式从 Metric Registry 继承。provider 定位、抓取时间、方法版本和质量日志保留在 tool trace / Manifest 中，不重复暴露给 Agent。

StateValue 前质量检查只决定当前 Observation 能否提升：未通过时不生成 StateValue，记录对应 target 状态后继续。该检查明确为非阻塞检查，不得阻塞 Agent 报告、Document 1、Document 2 或整个 Workflow。

这里的 `CollectionObservation` 是指标采集域对象，不等同于当前 ReAct runtime 用于保存 tool-call 文本块的 observation/retained-observation。开发前必须决定两者是通过 adapter 复用，还是分别持久化；不得仅因名称相同就直接共用现有 runtime memory schema。

---

# 五、统一的 source role 写入规则

写入正式 `StateValue` 时，按照来源性质设置 `source_role`：

```text
公司已经披露的经营和财务结果
→ ACTUAL

公司管理层正式指引、目标或明确前瞻判断
→ MANAGEMENT

卖方一致预期、机构预测和分析师模型
→ SELL_SIDE

客户、供应商、竞争者、渠道和产业链来源
→ INDUSTRY_CHAIN

市场价格、估值、期权和利率市场所表达的状态
→ MARKET_IMPLIED
```

同一 `StateParameter` 可以同时包含不同来源角色的 StateValue。

例如：

```text
fin_revenue

ACTUAL
最新季度实际收入

MANAGEMENT
下一季度管理层收入指引

SELL_SIDE
下一季度卖方收入一致预期
```

管理层收入、卖方收入和实际收入不得因为来源不同而被建立为三个不同的 Parameter。

## 1. 模型派生值的 source role

确定性计算或模型派生不会自动产生新的来源角色。派生值继承其权威输入对应的现有 `source_role`，并在 provenance 中记录：

```text
method_id
method_version
input_refs
calculated_at
```

例如，由期权链计算的 ATM IV 仍为 `MARKET_IMPLIED`；由实际收入和实际成本计算的毛利率仍为 `ACTUAL`。

如果一个计算混合多个 `source_role`，collection target 必须预先声明唯一的权威输出角色、输入优先级和方法边界；无法确定时只生成 Observation，不生成 StateValue。

## 2. 空值与空区间

无可靠状态时不生成 StateValue。禁止生成：

```text
lower: null / upper: null
空 RANGE
零值占位
UNKNOWN StateValue
```

缺失、不可用或执行失败只记录在 collection-target Manifest 中。

## 3. ObjectRef 过渡口径

所有 `basis_refs`、`input_refs` 和 Manifest `output_refs` 使用统一 `ObjectRef` 形状。其 `object_type` 枚举必须允许：

```text
EVENT
METRIC
EVIDENCE
FILING
ANALYST_ESTIMATE
STATE_VALUE
REALIZATION_FACTOR
```

统一 ObjectRef resolver 本轮暂不作为数据源接入的阻塞项，但在 resolver 落地前，每个引用必须同时保留：

```text
provider-specific id / source locator
canonical object id candidate
resolver_status: RESOLVED | CANDIDATE | UNRESOLVED
```

不得仅因对象类型可枚举就宣称引用已经全局可解析。

## 4. 已移除字段

`event_family_ids` 已从方案中移除。本采集板块、Manifest、Registry 和 tool contract 均不得重新引入该字段，也不为其建设治理目录。

---

# 六、C1 个股基本面固定清单

第六至第九节的 YAML/枚举首先定义 Metric Registry 语义与 target 模板。凡一条 `collection_rule` 同时包含多个来源角色或期间，实际开发时必须展开成多个 `collection_target_id`，不得把旧的单条 metric 配置直接当作可执行路由。第九节 O4 清单已按该目标形态显式展开，可作为其他板块迁移的参考。

C1 负责：

```text
公司实际财务和经营状态
管理层预期
卖方一致预期
核心经营驱动
```

标准化财务数据和能够稳定获得的一致预期，应优先由确定性程序采集。

管理层指引、非标准 KPI 和口径解释主要由 Agent 提取。

## 1. 必填指标

### 收入

```yaml
- metric_id: fin_revenue
  requirement: REQUIRED
  format: NUMBER:REPORTING_CURRENCY
  time_scope: MULTI_PERIOD
  collection_rule: >
    采集最新已披露季度的GAAP合并收入。
    同时尝试采集下一季度及当前财年的管理层收入指引，
    以及下一季度和下一财年的卖方收入一致预期。
    不同期间和来源分别生成StateValue，
    但必须指向同一个fin_revenue Parameter。
```

### 毛利率

```yaml
- metric_id: fin_gross_margin
  requirement: REQUIRED
  format: NUMBER:PERCENTAGE
  time_scope: MULTI_PERIOD
  collection_rule: >
    采集最新季度GAAP毛利率。
    同时尝试采集管理层毛利率指引和卖方毛利率预期。
    若行业不存在具有经济意义的毛利率，返回NOT_APPLICABLE。
```

### 营业利润率

```yaml
- metric_id: fin_operating_margin
  requirement: REQUIRED
  format: NUMBER:PERCENTAGE
  time_scope: MULTI_PERIOD
  collection_rule: >
    采集最新季度GAAP营业利润率。
    同时尝试采集管理层和卖方对后续期间的营业利润率预期。
    不使用调整后EBITDA Margin直接替代。
```

### 稀释 EPS

```yaml
- metric_id: fin_diluted_eps
  requirement: REQUIRED
  format: NUMBER:CURRENCY_PER_SHARE
  time_scope: MULTI_PERIOD
  collection_rule: >
    采集最新季度GAAP稀释EPS，包括负值。
    同时尝试采集下一季度和当前财年的管理层EPS指引，
    以及下一季度和下一财年的卖方EPS一致预期。
```

### 自由现金流

```yaml
- metric_id: fin_free_cash_flow
  requirement: REQUIRED
  format: NUMBER:REPORTING_CURRENCY
  time_scope: TRAILING_TWELVE_MONTHS
  collection_rule: >
    优先使用过去十二个月经营现金流减资本开支计算标准自由现金流。
    公司自定义调整后FCF可以作为补充StateValue，
    但不得替代标准口径且必须明确口径差异。
```

### 资本开支

```yaml
- metric_id: fin_capex
  requirement: REQUIRED
  format: NUMBER:REPORTING_CURRENCY
  time_scope: MULTI_PERIOD
  collection_rule: >
    采集过去十二个月现金流量表资本性支出。
    同时尝试采集当前财年的管理层Capex指引及卖方Capex预期。
```

### 现金

```yaml
- metric_id: fin_cash
  requirement: REQUIRED
  format: NUMBER:REPORTING_CURRENCY
  time_scope: LATEST_REPORTED_BALANCE_SHEET_DATE
  collection_rule: >
    采集现金及现金等价物。
    短期投资仅在公司披露口径或标准化数据源明确支持时并入，
    并保持历次运行口径一致。
```

### 总债务

```yaml
- metric_id: fin_total_debt
  requirement: REQUIRED
  format: NUMBER:REPORTING_CURRENCY
  time_scope: LATEST_REPORTED_BALANCE_SHEET_DATE
  collection_rule: >
    采集短期借款、长期债务流动部分和长期债务。
    不计入普通经营性应付款。
```

### 净债务

```yaml
- metric_id: fin_net_debt
  requirement: REQUIRED
  format: NUMBER:REPORTING_CURRENCY
  time_scope: LATEST_REPORTED_BALANCE_SHEET_DATE
  collection_rule: >
    按总债务减现金计算。
    负值表示净现金，不截断为零。
```

## 2. 可选财务指标枚举

以下指标使用统一 `metric_id`，根据公司业务和数据可得性选择采集：

```text
fin_net_income
fin_ebitda
fin_ebitda_margin
fin_operating_cash_flow
fin_adjusted_free_cash_flow
fin_r_and_d_expense
fin_sga_expense
fin_stock_based_compensation
fin_interest_expense
fin_tax_rate
fin_working_capital
fin_inventory
fin_accounts_receivable
fin_accounts_payable
fin_deferred_revenue
fin_diluted_share_count
fin_share_repurchase
fin_dividend
fin_debt_maturity
fin_liquidity
fin_cash_runway
fin_return_on_equity
fin_return_on_invested_capital
```

这些指标可以同时保存：

```text
ACTUAL
MANAGEMENT
SELL_SIDE
```

但只有在对应来源确实提供相关值时生成，不要求 Agent 主观估计。

## 3. 可选核心经营驱动枚举

### 数量与交易规模

```text
op_unit_volume
op_shipment_volume
op_transaction_volume
op_booking_volume
op_order_volume
op_production_volume
op_sales_volume
```

### 价格与单位变现

```text
op_average_selling_price
op_average_order_value
op_arpu
op_subscription_price
op_take_rate
op_yield_per_unit
op_revenue_per_unit
```

### 客户与用户

```text
op_customer_count
op_active_customer_count
op_subscriber_count
op_active_user_count
op_paid_user_count
op_customer_additions
op_user_growth
op_retention_rate
op_churn_rate
```

### 订单与收入可见度

```text
op_bookings
op_backlog
op_remaining_performance_obligations
op_order_growth
op_order_coverage
op_contract_duration
op_renewal_rate
op_cancellation_rate
```

### 产能与交付

```text
op_capacity
op_capacity_addition
op_capacity_utilization
op_yield_rate
op_production_ramp
op_delivery_volume
op_lead_time
op_inventory_days
```

### 市场与竞争位置

```text
op_market_share
op_market_share_change
op_customer_concentration
op_geographic_mix
op_product_mix
op_channel_mix
```

### 商业化与业务阶段

```text
op_product_development_stage
op_customer_validation_stage
op_qualification_stage
op_regulatory_stage
op_commercialization_stage
op_mass_production_stage
op_store_count
op_occupancy_rate
op_same_store_sales_growth
```

未来行业适配指标库负责规定不同业务模式优先使用哪些指标。本版本只提供上述常见枚举，Agent 可以从中选择适用项。

---

# 七、C2 宏观研究固定清单

C2 负责采集：

```text
美国增长状态
就业和通胀状态
政策利率与市场隐含利率路径
长期利率
信用与金融条件
美元与市场风险状态
与标的明确相关的外部宏观变量
```

标准宏观数据应优先全部由确定性程序获取。Agent 主要负责解释数据组合以及补充政策背景。

## 1. 必填指标

```yaml
- metric_id: macro_real_gdp_growth
  requirement: REQUIRED
  format: NUMBER:PERCENTAGE
  time_scope: LATEST_REPORTED_QUARTER_QOQ_SAAR
  collection_rule: >
    采集美国实际GDP最新季度环比年化增速。
    如数据源稳定提供下一季度经济学家一致预期，
    另行生成SELL_SIDE StateValue。

- metric_id: macro_unemployment_rate
  requirement: REQUIRED
  format: NUMBER:PERCENTAGE
  time_scope: LATEST_REPORTED_MONTH
  collection_rule: >
    采集美国失业率最新月度值。

- metric_id: macro_core_pce_inflation
  requirement: REQUIRED
  format: NUMBER:PERCENTAGE
  time_scope: LATEST_REPORTED_MONTH_YOY
  collection_rule: >
    采集美国核心PCE同比增速。

- metric_id: macro_effective_policy_rate
  requirement: REQUIRED
  format: NUMBER:PERCENTAGE
  time_scope: CURRENT
  collection_rule: >
    采集有效联邦基金利率。

- metric_id: macro_implied_policy_rate_12m
  requirement: REQUIRED
  format: NUMBER:PERCENTAGE
  time_scope: TWELVE_MONTHS_FORWARD
  collection_rule: >
    使用联邦基金期货或OIS数据，
    由确定性程序计算十二个月后的市场隐含政策利率。
    尚无生产数据源、订阅或计算实现时返回UNAVAILABLE；
    已执行稳定路径但该时点无结果时才返回EMPTY，不由Agent主观替代。

- metric_id: macro_us_10y_yield
  requirement: REQUIRED
  format: NUMBER:PERCENTAGE
  time_scope: LATEST_MARKET_CLOSE
  collection_rule: >
    采集美国10年期国债收益率。

- metric_id: macro_high_yield_oas
  requirement: REQUIRED
  format: NUMBER:PERCENTAGE_POINT
  time_scope: LATEST_AVAILABLE_CLOSE
  collection_rule: >
    采集美国高收益债期权调整利差。

- metric_id: macro_financial_conditions
  requirement: REQUIRED
  format: NUMBER:INDEX
  time_scope: LATEST_AVAILABLE_WEEK
  collection_rule: >
    固定使用Chicago Fed NFCI。
    保留指数原始值，不额外生成主观评分。

- metric_id: macro_broad_usd
  requirement: REQUIRED
  format: NUMBER:INDEX
  time_scope: LATEST_AVAILABLE_CLOSE
  collection_rule: >
    固定使用美联储广义美元指数。

- metric_id: macro_vix
  requirement: REQUIRED
  format: NUMBER:INDEX
  time_scope: LATEST_MARKET_CLOSE
  collection_rule: >
    采集VIX收盘值。
```

## 2. 可选增长与需求指标枚举

```text
macro_nominal_gdp_growth
macro_real_consumption_growth
macro_retail_sales_growth
macro_industrial_production_growth
macro_capacity_utilization
macro_durable_goods_orders
macro_business_fixed_investment
macro_equipment_investment
macro_construction_spending
macro_ism_manufacturing
macro_ism_services
macro_consumer_confidence
macro_small_business_optimism
macro_housing_starts
macro_existing_home_sales
macro_new_home_sales
macro_auto_sales
```

## 3. 可选就业与通胀指标枚举

```text
macro_nonfarm_payroll_change
macro_initial_jobless_claims
macro_job_openings
macro_labor_force_participation
macro_average_hourly_earnings_growth
macro_core_cpi_inflation
macro_headline_cpi_inflation
macro_headline_pce_inflation
macro_producer_price_inflation
macro_employment_cost_index
macro_inflation_expectation_5y
macro_breakeven_inflation_10y
```

## 4. 可选利率、信用与流动性指标枚举

```text
macro_us_2y_yield
macro_us_30y_yield
macro_yield_curve_2s10s
macro_yield_curve_3m10y
macro_real_yield_10y
macro_investment_grade_oas
macro_bank_lending_standards
macro_money_supply_growth
macro_fed_balance_sheet
macro_reverse_repo_balance
macro_bank_reserves
macro_mortgage_rate_30y
macro_corporate_borrowing_cost
```

## 5. 可选商品、汇率与成本指标枚举

```text
macro_wti_crude_price
macro_brent_crude_price
macro_natural_gas_price
macro_copper_price
macro_aluminum_price
macro_steel_price
macro_gold_price
macro_electricity_price
macro_freight_rate
macro_semiconductor_price_index
macro_food_commodity_index
macro_currency_pair
macro_country_specific_fx
```

## 6. 可选政策与财政指标枚举

```text
macro_federal_spending_growth
macro_defense_spending
macro_infrastructure_spending
macro_government_procurement
macro_subsidy_amount
macro_tax_rate
macro_tariff_rate
macro_export_control_status
macro_sanction_status
macro_policy_approval_stage
macro_regulatory_policy_stage
```

可选宏观指标应当与目标公司的收入、成本、融资、资本开支或主要行业需求存在清晰联系。没有相关性时，不需要因为指标已被枚举而逐项采集。

---

# 八、C3 行业研究固定清单

C3 不负责补充 C1 的卖方收入和 EPS 一致预期。

原因是 C1 与 C3 并行运行，C3 不应依赖 C1 已经创建的 Parameter。公司财务及核心经营驱动的卖方预期统一由 C1 负责。

C3 负责：

```text
行业需求
行业供给
价格与单位经济
库存与订单
产业链可见度
目标公司竞争位置
客户、供应商与竞争者状态
产品和商业化阶段
```

C3 当前不设置通用必填指标。行业数据可得性差异较大，全部采用可选枚举。

## 1. 行业需求指标枚举

```text
ind_market_size
ind_market_growth
ind_end_demand_growth
ind_shipment_growth
ind_consumption_growth
ind_customer_capex
ind_customer_order_growth
ind_utilization_demand
ind_traffic_growth
ind_usage_growth
ind_booking_growth
ind_geographic_demand
ind_segment_demand
```

## 2. 行业供给与产能指标枚举

```text
ind_total_capacity
ind_capacity_growth
ind_capacity_addition
ind_capacity_reduction
ind_capacity_utilization
ind_production_growth
ind_supply_growth
ind_supply_shortage
ind_supply_surplus
ind_lead_time
ind_delivery_time
ind_raw_material_availability
ind_power_availability
ind_labor_availability
```

## 3. 价格与单位经济指标枚举

```text
ind_average_selling_price
ind_spot_price
ind_contract_price
ind_price_growth
ind_discount_rate
ind_unit_cost
ind_input_cost
ind_gross_margin
ind_incremental_margin
ind_customer_acquisition_cost
ind_revenue_per_unit
ind_profit_pool
```

## 4. 库存与订单指标枚举

```text
ind_inventory_level
ind_inventory_days
ind_channel_inventory
ind_customer_inventory
ind_order_growth
ind_order_backlog
ind_order_coverage
ind_book_to_bill
ind_cancellation_rate
ind_contract_duration
ind_renewal_rate
ind_preorder_volume
```

## 5. 市场份额与竞争位置指标枚举

```text
ind_target_market_share
ind_market_share_change
ind_competitor_market_share
ind_relative_price_position
ind_relative_cost_position
ind_relative_performance
ind_customer_concentration
ind_supplier_concentration
ind_channel_position
ind_geographic_position
ind_product_mix_position
```

## 6. 产品与商业化阶段指标枚举

```text
ind_product_development_stage
ind_sampling_stage
ind_customer_testing_stage
ind_customer_validation_stage
ind_qualification_stage
ind_supplier_entry_stage
ind_order_allocation_stage
ind_regulatory_approval_stage
ind_production_ramp_stage
ind_mass_production_stage
ind_commercial_launch_stage
ind_revenue_recognition_stage
```

阶段型指标应使用固定的 `ordered_states`。如果不同产业无法共享同一阶段枚举，可以在 Parameter 定义中建立行业专用顺序，但 `metric_id` 仍应尽量使用上述通用代码。

## 7. 竞争者与产业链预期指标枚举

```text
ind_competitor_capacity_plan
ind_competitor_product_timeline
ind_competitor_pricing_direction
ind_competitor_market_share_outlook
ind_customer_demand_outlook
ind_customer_procurement_timeline
ind_supplier_supply_outlook
ind_supplier_cost_outlook
ind_channel_demand_outlook
ind_industry_balance_timing
ind_industry_cycle_direction
```

这些状态通常写入：

```text
INDUSTRY_CHAIN
```

若来源是正式卖方行业预测，可以写入 `SELL_SIDE`，但不得与 C1 的公司财务一致预期重复。

---

# 九、O4 市场研究固定清单

基础采集只负责 O4-A 所需的标准 `Market Measurement`，不负责形成：

```text
Pricing Theme
Repricing Episode
Implied Outcome
Scenario Consistency
Market Anchor Candidate
Pricing Distortion conclusion
```

上述对象属于 O4-A 报告级研究。O4-A 可以提出可审计的参数级 `MARKET_IMPLIED StateValue` 候选，但基础采集不得提前把单一价格、估值、期权或仓位数据解释成市场隐含业务结论。

O4 的固定必填只保留完成当前定价基准所需的最小集合：

```text
价格与相对定价
市值、企业价值与估值
```

卖方修正、期权、事件不确定性、仓位和拥挤度是重要交叉验证，但不是每次完成 O4-A 的硬前置条件，应根据数据可得性进入可选或按需分析。

公式计算、历史序列比较、期权插值、同业组合、收益控制和空头持仓变化均优先交给确定性程序。O4 Agent 只解释已获得的 Market Measurements、识别限制，并将其与 C1/C3 驱动结合；不得手动重算缺失指标。

## 1. 必填价格与相对定价 targets

| collection_target_id | metric_id | time_scope | output_policy | collection_rule |
|---|---|---|---|---|
| `o4_price_snapshot` | `market_share_price` | `MARKET_SNAPSHOT_TIME` | `STATE_VALUE` | 采集统一快照时点的普通股价格，保存币种、交易所和 session。 |
| `o4_price_volume_history` | `market_daily_ohlcv` | `TRAILING_ONE_YEAR` | `OBSERVATION_ONLY` | 保存目标、broad benchmark 和行业 proxy 的同口径日线序列，供基本收益、成交和事件窗口计算；需要更长历史时按需扩展，不把整段序列写成 StateValue。 |
| `o4_total_return_windows` | `market_total_return` | `1D/5D/1M/3M/6M/1Y` | `OBSERVATION_ONLY` | 使用统一 adjusted-price 与交易日历计算多窗口总收益；各窗口是同一 target 的 item。 |
| `o4_relative_return_broad` | `market_relative_return_broad_market` | `1D/5D/1M/3M/6M/1Y` | `OBSERVATION_ONLY` | 使用事先治理的 broad benchmark，在相同交易日和价格调整口径下计算。 |
| `o4_relative_return_industry` | `market_relative_return_industry` | `1D/5D/1M/3M/6M/1Y` | `OBSERVATION_ONLY` | 使用事先治理的行业/板块 proxy；保存 benchmark id 与选择版本。 |

这些指标只建立最小可比定价基准，不能单独归因为公司特定驱动。O4-A 如需“公司特异性收益代理”，应进行受限解释，不伪造 beta 或因子系数。

## 2. 优先可选的价格与交易指标

以下指标可由当前日线行情、成交量、同业列表或标准股本数据直接计算，适用时优先补充：

```text
market_relative_return_peer_basket
market_abnormal_volume_ratio
market_turnover_rate
market_realized_volatility_20d
market_realized_volatility_60d
market_average_daily_volume
```

peer basket 必须在观察结果前按业务暴露确定并保存版本；没有可靠同业组合时不影响 O4 完成 broad/industry 基准。

## 3. 必填估值 targets

| collection_target_id | metric_id | time_scope | output_policy | collection_rule |
|---|---|---|---|---|
| `o4_market_cap` | `market_cap` | `MARKET_SNAPSHOT_TIME` | `STATE_VALUE` | 当前价格乘实际基础流通股数；fully diluted 口径另行标注，不混用 diluted-EPS denominator。 |
| `o4_enterprise_value` | `market_enterprise_value` | `MARKET_SNAPSHOT_TIME` | `STATE_VALUE` | 按统一口径处理债务、优先股、少数股东权益、现金和非经营资产，并保存各输入时点。 |
| `o4_primary_forward_multiple` | 适用的具体 forward multiple metric | `NEXT_TWELVE_MONTHS` | `STATE_VALUE` | 估值适配器在 `market_forward_pe`、`market_forward_ev_to_ebitda`、`market_forward_ev_to_sales` 等具体 Registry metric 中选择一个适用口径，并在同一个 target 结果中记录 `selected_metric_id`、值、分子、分母和预测期间。 |

对银行、保险、REIT 或前商业化公司，主估值口径可以不同；`market_enterprise_value` 不具有经济意义时允许 `NOT_APPLICABLE`。必填只要求系统尝试形成一个可解释的当前主估值口径，不要求同时计算所有倍数。

## 4. 可选估值指标枚举

```text
market_trailing_pe
market_forward_pe
market_price_to_sales
market_forward_price_to_sales
market_ev_to_sales
market_forward_ev_to_sales
market_ev_to_ebitda
market_forward_ev_to_ebitda
market_price_to_book
market_price_to_tangible_book
market_price_to_free_cash_flow
market_free_cash_flow_yield
market_earnings_yield
market_dividend_yield
market_peg
market_peer_premium
```

优先启用可以由当前价格和标准财务实际值直接计算的 trailing/current metrics。以下指标依赖历史 point-in-time forward consensus，目前只保留定义，不列入优先可选执行：

```text
market_primary_multiple_history_series
market_primary_multiple_percentile
market_earnings_multiple_bridge
```

上述 targets 当前统一设为 `collection_mode: UNAVAILABLE`。

可选估值指标均由程序计算或从标准化数据源直接提取。Agent 不自行拼接财务口径，也不得用当前 consensus 回填历史。

## 5. 卖方输入复用与条件可选修正指标

O4 不另建收入和 EPS 参数。它复用 C1 已采集的：

```text
fin_revenue + SELL_SIDE + 下一季度/当前财年/下一财年
fin_diluted_eps + SELL_SIDE + 下一季度/当前财年/下一财年
fin_free_cash_flow + SELL_SIDE + NTM/下一财年（可得时）
```

以上是 C1 的既有 collection targets，不重复计入 O4 必填清单。O4 只读取可用结果；缺失时降低可识别程度。

历史卖方修正需要真实 point-in-time vintage，因此以下 Observation targets 只在相应数据源已经验证可用时启用：

本版本下述 targets 的 `collection_mode` 均为 `UNAVAILABLE`；真实 point-in-time vintage 验证通过后方可启用。

| collection_target_id | metric_id | time_scope | output_policy | collection_rule |
|---|---|---|---|---|
| `o4_sell_side_consensus_history` | `sell_side_consensus_history` | `POINT_IN_TIME_90D_AND_EVENT_WINDOWS` | `OBSERVATION_ONLY` | 保存 revenue/EPS/FCF 各预测期间的历史快照、contributor count、basis 与 freshness；没有真实 vintage 时不得用当前值回填。 |
| `o4_revenue_consensus_revision` | `sell_side_revenue_revision` | `CURRENT_VS_30D_AND_90D` | `OBSERVATION_ONLY` | 比较同一财务期间、同一会计口径的 point-in-time consensus，保存 contributor count、freshness 与 dispersion。 |
| `o4_eps_consensus_revision` | `sell_side_eps_revision` | `CURRENT_VS_30D_AND_90D` | `OBSERVATION_ONLY` | 不把财年滚动造成的变化误当预测修正。 |
| `o4_revision_breadth` | `sell_side_revision_breadth` | `30D_AND_90D` | `OBSERVATION_ONLY` | `(上修机构数 - 下修机构数) / active contributors`；缺少 contributor-level history 时返回 `UNAVAILABLE`。 |

目标价和评级只作为次级 framing evidence，不是 operating consensus，也不作为隐含业务状态。它们保留为可选指标：

```text
sell_side_price_target_consensus
sell_side_price_target_revision
sell_side_rating_distribution
sell_side_consensus_dispersion
sell_side_fcf_revision
```

## 6. 按需历史财报与重大披露反应分析

历史财报反应是 O4-A 的研究方法，不是每次运行都必须预填的基础指标。只有当前定价主题确实涉及财报敏感性，且所需 C1 与行情数据可得时，才启动以下分析任务：

| analysis_task_id | metric_or_artifact | time_scope | output_policy | analysis_rule |
|---|---|---|---|---|
| `o4_earnings_surprise_panel` | `market_earnings_surprise_vector` | `RECENT_RELEVANT_QUARTERS` | `OBSERVATION_ONLY` | 复用 C1 的 ACTUAL/MANAGEMENT/SELL_SIDE values 构造多维 surprise；不要求固定八个季度，也不压缩为单一 surprise score。 |
| `o4_earnings_event_returns` | `market_earnings_event_return` | `PRE_EVENT/ANNOUNCEMENT/1D/5D/20D` | `OBSERVATION_ONLY` | after-hours 披露对齐下一交易日；按需要计算目标绝对和相对收益、漂移与反转。 |
| `o4_post_event_consensus_revision` | `sell_side_post_event_revision` | `EVENT_TO_5D_AND_20D` | `OBSERVATION_ONLY` | 仅在存在真实 point-in-time consensus 时使用；否则省略，不阻塞 O4。 |

这些 targets 只提供可比较样本。O4-A 负责自然语言判断市场历史上更敏感的基本面维度；基础采集不得自动把相关性压缩为驱动归因或 MARKET_IMPLIED 业务状态。

## 7. 条件可选的期权、事件不确定性与仓位指标

这些指标对判断风险时间、尾部不对称和拥挤度很有价值，但 O4 可以在没有它们时完成较低可识别度的研究，因此全部降为条件可选。只有对应 tool、订阅和质量门槛已经满足时才启用：

| collection_target_id | metric_id | time_scope | output_policy | collection_rule |
|---|---|---|---|---|
| `o4_atm_iv_30d` | `market_atm_iv_30d` | `MARKET_SNAPSHOT_TIME` | `STATE_VALUE` | 优先使用可审计 30D ATM IV；需要插值时按方差时间插值并记录方法版本。无期权标的返回 `NOT_APPLICABLE`。 |
| `o4_next_event_implied_move` | `market_next_event_implied_move` | `NEXT_SCHEDULED_EVENT` | `STATE_VALUE` | 使用跨越已确认事件时间的近 ATM straddle/受治理方法；事件时间不可靠或期限污染时不生成。 |
| `o4_iv_term_slope` | `market_iv_term_slope_30d_90d` | `MARKET_SNAPSHOT_TIME` | `OBSERVATION_ONLY` | 使用同一 moneyness/delta convention 比较 30D 与 90D IV。 |
| `o4_put_call_skew_30d` | `market_put_call_skew_30d` | `MARKET_SNAPSHOT_TIME` | `OBSERVATION_ONLY` | 固定为约 30D 的 `IV(25Δ put) - IV(25Δ call)`；旧的 `market_put_skew_30d` 不再作为正式 metric id。 |
| `o4_short_interest_pct_float` | `market_short_interest_pct_float` | `LATEST_PUBLISHED_SETTLEMENT_DATE` | `STATE_VALUE` | 使用正式 short-interest shares 与自由流通股；不使用 daily short volume 替代。 |
| `o4_days_to_cover` | `market_days_to_cover` | `LATEST_PUBLISHED_SETTLEMENT_DATE` | `STATE_VALUE` | 使用供应商正式值或 short shares / 统一口径 ADV；保存 report date 与 publication date。 |
| `o4_short_interest_change` | `market_short_interest_change` | `CURRENT_VS_PREVIOUS_REPORT` | `OBSERVATION_ONLY` | 比较连续正式报告期；不得将运行时间当作结算日期。 |

期权指标用于描述波动大小、风险时间和尾部不对称，不能把高 IV 直接解释为看空，也不能把 unusual option activity 当作完整期权链。

## 8. 后续扩展的期权指标短名单

```text
market_atm_iv_60d
market_atm_iv_90d
market_iv_realized_vol_spread
market_put_call_volume_ratio
market_put_call_open_interest_ratio
market_option_open_interest
market_option_volume
market_event_iv_premium
```

这部分不在首期 Registry 中一次性全部启用。IBKR option surface 接通并验证字段、订阅和流动性门槛后，再按 O4 实际使用价值逐项开放。

旧草案中的 `market_put_skew_30d` 仅作为 Registry migration alias 映射到 `market_put_call_skew_30d`，不得继续注册为另一个可采集 metric。

## 9. 其他可选仓位和流动性指标枚举

```text
market_short_interest_shares
market_borrow_fee
market_float_shares
market_institutional_ownership
market_insider_ownership
market_block_trade_activity
```

其中尚未接入稳定数据源的 targets，应标记为 `UNAVAILABLE`，而不是要求 O4 Agent 每次尝试搜索。只有 float、持仓或大宗交易来源完成实际 entitlement 和字段验证后，才加入首期可执行 Registry。

## 10. O4 target 与规划 tools 的绑定

| target family | primary semantic tools | fallback / supplement | 当前开发口径 |
|---|---|---|---|
| 价格、OHLCV、相对收益、事件窗口 | `ibkr.market_history` | `twelvedata.daily_ohlcv` | Twelve Data 已有基础实现；IBKR、benchmark/peer basket 与统一 adjustment 尚待接入 |
| 市值、EV、当前估值 | `fmp.valuation_snapshot` + SEC/标准财务 inputs | Alpha Vantage / 受治理计算 | 当前 FMP 仅接了 sector performance，不能标为已支持 |
| 卖方 consensus 与修正 | `fmp.sell_side_estimates` | `twelvedata.sell_side_estimates`、`benzinga.analyst_events` | 当前只能部分覆盖最新/期间预测；历史 point-in-time vintage 未闭合 |
| 期权 surface、IV、skew、事件 move | `ibkr.option_surface` | Benzinga option activity 仅作定位证据 | IBKR tool 与订阅尚待接入；Benzinga signals 不能替代期权链 |
| short interest / days to cover | `UNAVAILABLE` | IBKR shortable shares / fee rate | 当前 Benzinga key 无产品权限；不注册、不路由，报告日与发布日期必须分开 |
| 历史财报反应 panel | market history + earnings events + C1 ACTUAL/MANAGEMENT/SELL_SIDE targets | SEC、Benzinga/FMP events | 必须先完成跨 artifact 的期间和事件时间对齐，不由 O4 Agent 手拼 |

上述 tool 名是目标拓扑，不代表当前 runtime 已实现或已购买相应 entitlement。只有 provider client、tool schema、权限、契约测试与 target-level Manifest 全部接通后，target 才能从 `DOCUMENTED` 升级为 `PRODUCTION_READY`。

---

# 十、采集结果 Manifest

Manifest 只服务于：

```text
运行审计
数据覆盖统计
缺失原因分析
程序与Agent采集效果评估
```

Manifest 不进入下游核心 State Schema，但必须作为独立运行审计 artifact 持久化。单个 target 的数据缺失不得阻塞整个研究 Workflow；Schema、安全、身份映射或 provenance 校验失败时，只能阻止该 target 的结果被提升为 StateValue，不得阻塞 Agent 报告、Document 1、Document 2 或整个 Workflow。

示例：

```json
{
  "collection_target_id": "c1_fin_revenue_sell_side_next_fy",
  "metric_id": "fin_revenue",
  "source_role": "SELL_SIDE",
  "time_scope": "NEXT_FISCAL_YEAR",
  "collection_mode": "PROGRAM",
  "status": "PARTIAL",
  "registry_version": "...",
  "cutoff_at": "...",
  "resolved_from_cache": false,
  "provider_attempts": [
    {
      "provider": "FMP",
      "tool_name": "fmp.sell_side_estimates",
      "status": "SUCCESS"
    }
  ],
  "requested_items": 3,
  "succeeded_items": 2,
  "empty_items": 1,
  "failed_items": 0,
  "output_refs": [
    {
      "object_type": "STATE_VALUE",
      "object_id": "...",
      "resolver_status": "CANDIDATE"
    }
  ],
  "reason_codes": ["ONE_PERIOD_NOT_PUBLISHED"]
}
```

`output_refs` 使用第五节定义的 ObjectRef 过渡口径；在统一 resolver 落地前，同时保存 provider locator，不得写入无法回查的裸字符串。

允许状态：

```text
FILLED
PARTIAL
EMPTY
NOT_APPLICABLE
FAILED
UNAVAILABLE
```

Manifest 记录原则：

1. Manifest 必须以 `collection_target_id` 为唯一结果粒度，不能只按 `metric_id` 汇总；
2. 已获得全部可靠结果时记录 `FILLED`；多个 items 只有部分成功时记录 `PARTIAL`；
3. 已成功执行主要采集流程但没有可靠结果时记录 `EMPTY`；
4. target 不适用于标的时记录 `NOT_APPLICABLE`；
5. 有生产路径但本次执行异常时记录 `FAILED`；无生产路径或 entitlement 时记录 `UNAVAILABLE`；
6. `PARTIAL` 只为成功 items 生成输出；其他非 `FILLED` 状态不得生成伪造的 UNKNOWN StateValue、空 RANGE 或零值；
7. 同一 `metric_id` 的不同 source role、time scope 或 entity scope targets 可以分别成功、失败或不可用；
8. Manifest 缺失或不符合 Schema 是审计/开发问题，但不应把不可靠数据提升为 StateValue；
9. 单个 target 的 Manifest 不完整不得无限阻止研究报告或整个 Workflow 继续运行。

---

# 十一、Agent 内部执行方式

不为当前 DoxAgent 的每个 Loop 写死独立阶段，也不要求 Agent 严格执行两次完全分离的调用。

只需增加以下执行约束：

> 基础预期指标采集应作为研究任务开头的独立前置阶段优先启动，但不能因为个别指标难以取得而停止后续研究。C1/C2/C3 报告可以在共享的程序采集完成后并行研究；O4-B 可以并行形成市场环境控制；O4-A 必须等待本轮 C1、C3、O4-B 的可用结果及基础 Market Measurements，不能继续与 C1/C3 完全并行启动。

建议的逻辑顺序为：

```text
实例化本轮 collection targets
        ↓
批量执行 PROGRAM targets，形成 Observation + target-level Manifest
        ↓
C1 / C2 / C3 与 O4-B 读取共享结果并开展研究
        ↓
各领域 Agent 补充其 AGENT targets，经过归一与质量门槛后提升结果
        ↓
O4-A 读取 C1、C3、O4-B 和基础 Market Measurements
        ↓
先提交基础预期指标采集 artifact，再提交各研究报告
```

具体要求：

1. 确定性程序结果及对应 Manifest 直接作为 Agent 输入；
2. Agent 不重复搜索程序已经提供的相同 collection target；
3. Agent 提交带来源定位的 Observation，不直接绕过 normalization/compiler 持久化 StateValue；
4. Agent 优先处理管理层指引、卖方非标准预期、行业状态和非标准 KPI；
5. 单个 target 无法取得时按 `EMPTY`、`FAILED` 或 `UNAVAILABLE` 记录并继续；
6. 报告研究过程中发现的新指标，只能先作为候选 Observation/target proposal，不在单次运行中静默改写全局 Registry；
7. 不设置全局 State Freeze；StateValue 前质量检查仅决定单项是否提升，检查失败时记录状态并继续；
8. StateValue 前质量检查明确为非阻塞检查；普通数据缺失、契约或安全失败均不得阻塞 Agent 报告、Document 1、Document 2 或整个 Workflow；
9. 报告阶段可以追加或纠正 Observation，但不能无审计地覆盖已持久化 StateValue；
10. 最终响应顺序固定为“基础预期指标采集结果在前，研究报告在后”；
11. O4 节点内部固定为 O4-B 在前、O4-A 在后，并保持两个报告的输出边界。

---

# 十二、指标归一与更新规则

本阶段不建设复杂的跨 Agent 冲突引擎，只保留基础归一规则。

## 1. 使用固定 metric_id

相同指标必须使用相同 `metric_id`。

`metric_id` 与 `parameter_id` 不是同一层 ID：

```text
metric_id
全局 Registry 中的指标定义，例如 fin_revenue。

parameter_id
某个实体/研究对象下的 StateParameter 实例，例如 param_mu_fin_revenue。
```

实例化必须由统一规则完成：

```text
metric definition + canonical entity ref +必要业务维度
→ parameter_id
```

在 ObjectRef resolver 尚未统一前，必须同时保存 provider entity id 与 canonical entity candidate，不能由各 Agent 自行拼接 parameter_id。

例如：

```text
fin_revenue
ind_capacity_utilization
market_forward_pe
macro_us_10y_yield
```

不得因为文本命名不同而分别创建：

```text
company_sales
total_revenue
quarterly_revenue
reported_sales
```

## 2. 同一指标允许多来源状态

例如：

```text
fin_revenue

ACTUAL
MANAGEMENT
SELL_SIDE
```

这些是同一个 Parameter 的不同 StateValue，不是重复指标。

## 3. 可选指标优先映射枚举

Agent 发现某项指标时，应先在本方案的枚举值中寻找匹配项。

只有同时满足以下条件时，才提出新的 `metric_id`：

```text
现有枚举不存在等价指标
该指标具有清晰独立口径
该指标不是已有指标的别名
该指标对当前行业具有持续研究价值
```

新的指标可以作为候选输出，但不要求在本次运行中完成全局 Registry 更新。

## 4. 跨运行更新

同一：

```text
parameter_id
source_role
time_scope
```

出现新值时，继续使用 DoxAgent 现有状态版本和证据基建处理。

`CURRENT` 唯一性必须至少按 `(parameter_id, source_role, time_scope)` 判断。下一季度、当前财年和下一财年等不同期间可以同时各有一个 CURRENT Value；不得按 `(parameter_id, source_role)` 将它们错误互相覆盖。

本方案不新增独立的 Upsert、冲突裁决或阻塞逻辑。

---

# 十三、实际开发顺序

## 第一步：冻结核心契约

开发 tools 之前先冻结：

```text
MetricDefinition 与 entity-scoped StateParameter 的映射
CollectionTarget Schema 与 target status
统一 ToolResult / Observation contract
ObjectRef 过渡字段
StateValue CURRENT 唯一键
HorizontalCollectionManifest 的持久化位置
```

## 第二步：建立指标与 Collection Target Registry

将本方案中的必填和可选 `metric_id` 写入统一 Registry，并为每个指标定义：

```text
标准名称
基础定义
value_type
默认单位
默认time_scope
常见别名
```

同时为每个固定 target 定义 source role、time scope、entity scope、collection mode、provider/tool、method 和 output policy。

## 第三步：调查并接入数据源和 Tools

逐项判断：

```text
是否能直接获得
是否需要确定性计算
是否只能通过文本提取
是否暂时不可获取
```

供应商“存在 endpoint”只能标记为 `DOCUMENTED`；只有完成 entitlement、实现、契约测试和 Manifest 接线后，才能标记为 `PRODUCTION_READY`。

## 第四步：优先开发确定性提取器

优先级建议为：

```text
C1标准财务实际值
C2标准宏观数据
C1卖方一致预期
O4价格、相对收益、市值和当前估值
O4卖方修正序列
O4期权、事件定价和空头仓位计算
```

## 第五步：验证稳定性

对不同业务模式的标的执行测试，检查：

```text
字段是否取错
期间是否错位
单位是否统一
负数是否被错误处理
财报修订是否造成错误覆盖
期权和空头数据时点是否正确
行业不适用指标能否正确返回NOT_APPLICABLE
```

## 第六步：确定 Agent 补充范围

只有确定性程序不能稳定提取的内容，才加入对应 Agent 的采集 Prompt。

典型 Agent 任务包括：

```text
管理层指引
卖方非标准经营预测
核心经营KPI
行业供需和竞争状态
产业链预期
阶段和方向型状态
数据口径解释
```

## 第七步：接入 Workflow 与研究报告

各 Agent 在研究报告生成过程中读取：

```text
程序预填指标
Agent补充指标
现有证据引用
缺失指标记录
```

最终先输出基础预期指标采集结果，再输出对应研究报告。

Workflow 必须显式支持：

```text
PROGRAM collection batch
→ C1/C2/C3/O4-B
→ AGENT observation normalization
→ O4-A
→ Document 1 assembly
```

不能沿用四个研究 Agent 完全并行、且只返回一个通用 `ResearchSection` 的旧编排来宣称新版 horizontal collection 与 O4-A 已经接通。

---

# 十四、最终结构

基础预期指标采集最终由四个部分组成：

```text
基础预期指标采集
├── Metric Registry
│   ├── 必填指标
│   ├── 可选指标枚举
│   └── 统一定义、类型和单位
│
├── Collection Target Registry
│   ├── source role / time scope / entity scope
│   ├── provider / semantic tool / method
│   ├── PROGRAM / AGENT / UNAVAILABLE
│   └── STATE_VALUE / OBSERVATION_ONLY
│
├── Collection Runtime
│   ├── ToolResult 与原始 Observation
│   ├── 归一、计算和质量门槛
│   ├── Agent Observation 提取
│   └── target-level Manifest
│
└── Collection Output
    ├── StateParameter / StateValue（符合提升条件时）
    ├── Observation-only 市场与辅助证据
    ├── ObjectRef + provider locator
    └── 非全局阻塞审计 Manifest
```

该板块的最终目标不是让 Agent 搜索更多数据，而是：

> **通过统一指标命名、确定性程序优先和有限的 Agent 补充，为下游持续提供口径一致、来源清晰、可以直接选择和组合的基础预期状态。**

---

# 十五、2026-08-08 前三步实施状态

本方案“实际开发顺序”的前三步已落地：

1. **核心契约已冻结**：实现 `MetricDefinition`、`CollectionTargetDefinition`、`CollectionObservation`、`ObjectRef`、`StateValueCurrentKey`、target-level `HorizontalCollectionManifest` 及 Blackboard working-memory 持久化；`REALIZATION_FACTOR` 已进入 `ObjectRef` 枚举。
2. **Registry 已建立**：从本文件生成并校验 304 个唯一 `metric_id`；当前固定 Target Registry 包含 38 个 source-role / period 级 target，覆盖 30 个固定必填 canonical metrics，并用候选 metric IDs 表达“主要远期估值倍数”，未虚构计划外 metric。
3. **非派生数据源与 Tools 已接入并验收**：当前中央 registry 覆盖 40 个规划内 tools（含 3 个 IBKR、37 个非 IBKR）；Benzinga short interest/transcript 与 FMP transcript 因 entitlement 标为 `UNAVAILABLE` 且不注册，Finnhub fund ownership composite 已移除并替换为可用的 insider-transactions tool。失败或权限阻断项不升级为 `PRODUCTION_READY`。

本轮只完成前三步及其所需 provider tool 实现，不宣称第四至第七步（批量确定性采集器、跨标的稳定性门槛、Agent 补充边界、Workflow/报告接线）已经完成。详细实现与验收边界见：

- `dev_plan/workflow_v2/d1_horizontal_indicators_collection_first_three_steps_implementation_20260808.md`
- `eval/horizontal_collection_real_acceptance_macro_industry_20260808.md`
- `eval/horizontal_collection_real_acceptance_public_regulatory_20260808.md`
- `eval/horizontal_collection_real_acceptance_commercial_20260808.md`
