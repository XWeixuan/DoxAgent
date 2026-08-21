+++
kind = "internal_task_skill"
id = "market-implied-expectations"
name = "Market-Implied Expectations Research"
version = "2026.08.20"
applicable_agents = ["C5"]
applicable_task_types = ["generate_global_research"]
workflow_nodes = ["BuildGlobalResearch"]
+++
# C5 Market-Implied Expectations Research

## Mission and boundary

Research what the market currently requires from the target's fundamentals before Document 2 constructs formal expectations. Begin with the economically material C1 company drivers and C3 industry/value-chain drivers, then use point-in-time price, relative performance, valuation, estimates, sell-side research, and selectively useful market evidence to answer:

> Which fundamental themes is the market trading, what business, financial, and duration conditions are broadly required for the current price to hold, and which conclusions are specific enough to become downstream market-anchor candidates or pricing questions?

Treat price as the joint outcome of expected cash flows, their timing, discount rates/risk premia, scenario weights, and market frictions. A price is one observation, not a transcript of investor beliefs. Facts must be reliable and time-consistent; interpretations may be conditional and decisive when the evidence supports a bounded conclusion.

Do not:

- redo C1 company research or C3 industry/value-chain research;
- explain every price move or turn every disclosure into a same-day “priced in / not priced in” judgment;
- infer market belief solely from overall share-price performance;
- calculate a target price, fair value, upside/downside, entry point, support/resistance, or trade;
- equate management guidance or sell-side consensus with the market;
- treat implied volatility as direction or positioning as fundamental belief.

C5 may propose research candidates for a downstream `MARKET_IMPLIED` source. Document 2 owns formal parameter mapping. Later workflows own event-level expectation gaps and market absorption.

## Inputs and research readiness

Use four input classes:

1. **C1** — current company state, management and sell-side baseline, core drivers, business-to-financial transmission, constraints, and candidate fundamental questions.
2. **C3** — external drivers, actors, industry supply/demand/price state, allocation mechanisms, target transmission, milestones, and candidate industry questions.
3. **Market and sell-side data** — price, volume, benchmark/sector/peer returns, equity or enterprise value, forward and historical valuation, point-in-time revenue/EPS/FCF/KPI estimates and revisions; options or positioning only when useful.
4. **Question-driven research** — targeted searches for recent pricing explanations, sell-side views, company/customer/value-chain information, comparable disclosures, and imminent information nodes. Do not perform a broad news sweep or build a long event library.

Not every data class or metric must be available. Missing data should reduce precision and report length, not create an input-audit section. Use the best reliable facts to form a conditional conclusion; if a number is not recoverable, move down the inference ladder from range to binding condition, then to pricing question, and only finally to an Unknown. Never fabricate an input or conceal look-ahead risk.

## Core analytical objects and report contract

Use only five analytical concepts:

- **Market Pricing Baseline** — the current state of price, relative performance, valuation, and earnings expectations.
- **Pricing Theme** — a material fundamental or value-chain variable worth testing as part of current pricing.
- **Repricing Phase** — a recent interval in which price, relative performance, valuation, or estimates show a meaningful change in pricing belief; a discrete event is not required.
- **Market-Implied Condition** — a business, financial, or duration condition broadly required for current price consistency under stated assumptions.
- **Market Anchor Candidate** — a sufficiently specific parameter/state/time inference that Document 2 may consider as `MARKET_IMPLIED` evidence.

Do not create formal `Pricing Distortion` or `Alternative Explanation` objects. Relevant conflicts, market mechanics, or competing accounts may be incorporated naturally where they change the research judgment.

Use exactly these five top-level report sections and these Chinese titles:

1. **一、当前市场定价基线**
2. **二、近期重定价与主要定价驱动**
3. **三、市场隐含的业务、财务与持续期条件**
4. **四、市场锚点与定价问题**
5. **五、关键未知项与识别边界**

Keep the report selective. The information-weighting guide is approximately 10–15%, 20–25%, 35–40%, about 20%, and below 10% respectively. These are priorities, not word quotas. Remove price trivia, generic valuation commentary, repeated causal chains, and themes that cannot map to a meaningful business, financial, or duration condition.

## Evidence, time, and calculation discipline

### Preserve point-in-time integrity

Every factual market claim needs an as-of date. Align price, diluted shares, net debt, consensus estimates, fiscal period, option snapshot, and positioning data as closely as practical. Never combine a historical price with today's revised estimates and call the result a historical multiple. Use point-in-time consensus where available; otherwise disclose look-ahead risk and avoid false precision.

Use the workflow's required citation syntax immediately after factual claims. Reuse exact `market_evidence_snapshot` values when available. A calculation cites its inputs and shows enough formula/arithmetic to reproduce the result. An interpretation cites the facts supporting it and states the condition on which it depends; it does not need a mechanical `LOW / MEDIUM / HIGH` label.

For material measurements preserve only metadata that affects interpretation: date/time zone, adjusted or raw price, currency, fiscal/accounting basis, numerator/denominator, formula/window, and material staleness or coverage limits. Tool names, provider routing, fallback attempts, and generic availability logs do not belong in the report.

### Keep evidence classes distinct without turning them into report bureaucracy

Reason separately about:

1. observed market data;
2. reported sell-side or actor views;
3. C5 calculations from cited inputs;
4. C5 conditional interpretations.

Make the distinction clear in prose when confusion is possible, but do not prefix every sentence with an epistemic label. Correlation is not attribution. Management guidance is a company statement; consensus is an analyst aggregation; a market-implied condition is C5's bounded interpretation of pricing evidence.

## End-to-end research method

### Step 1 — Select the pricing questions that matter now

Start from the short list of C1/C3 core drivers, but do not analyze every driver. Promote a driver to a Pricing Theme only when it materially affects revenue, price, volume, mix, margin, cash flow, capital needs, failure risk, or value duration **and** could determine whether current price holds.

Prefer variables that are changing, disputed, repeatedly associated with repricing, or capable of separating plausible operating paths. A supporting constraint belongs inside the relevant Theme unless it independently changes the economic scale, profitability, duration, or risk of the business.

Build a private working chain:

`C1/C3 driver -> business state -> financial interface -> duration -> expected pricing evidence -> current pricing question`

Use it to focus research, not as another reported registry. Normally a few themes are enough. The research question is not “what could affect the stock?” but “which variables actually determine whether today's valuation and earnings path can be sustained?”

### Step 2 — Establish the current pricing baseline

#### Price and relative performance

Choose only windows relevant to the current pricing regime: for example since the latest results, since a material inflection, year to date, or a longer comparator when needed. Use the same trading dates and adjusted-price convention for the target, a broad benchmark, a sector proxy, and a small economically relevant peer set. Select comparators by exposure before observing the answer.

A simple research proxy is:

`target-specific return proxy = target return - broad-market/sector/peer common return`

This is not causal proof. Do not invent beta coefficients or factor residuals. State benchmark mismatch only when it could change the conclusion.

#### Market value and valuation

Reconcile the numerator before interpreting a multiple:

`basic equity market value = price × actual shares outstanding`

`enterprise value = equity market value + debt + preferred equity + non-controlling interests - cash/non-operating assets`

Treat options, RSUs, convertibles, leases, pensions, associates, and investments consistently when material. Do not multiply price blindly by a diluted-EPS denominator. Match forward horizons and accounting bases across time and peers. Historical percentiles are descriptive, not proof of cheapness or expensiveness; business mix, rates, loss years, and accounting changes may break comparability.

#### Earnings-versus-multiple decomposition

When definitions and forward periods are unchanged:

`price = forward metric × valuation multiple`

For P/E:

`change in ln(price) = change in ln(forward EPS) + change in ln(forward P/E)`

For enterprise multiples, bridge enterprise value and the matching denominator. Separate estimate revision, fiscal-period roll, share-count/capital-structure change, and multiple rerating. A residual multiple change may reflect duration, margin confidence, optionality, failure risk, rates, or risk premium; do not call it optimism by default.

#### Sell-side baseline

Use point-in-time revenue, EPS, FCF, or economically relevant KPI estimates. Check revision direction, magnitude, breadth, dispersion, freshness, fiscal period, and accounting basis. A useful measure is:

`revision breadth = (upward revisions - downward revisions) / active contributors`

Target-price and rating changes are secondary framing evidence, not operating consensus and not the market-implied state.

End the baseline with a direct synthesis of whether current pricing is primarily characterized by earnings change, multiple change, both, or an unresolved mixture. Do not assign detailed drivers yet.

### Step 3 — Identify recent repricing phases and rank drivers

Start with the current pricing regime, not a default one-year timeline. Define a Repricing Phase when a meaningful combination of price/relative performance, valuation, estimate revisions, volume, or persistence indicates that the market's required belief changed. A Phase may be gradual and need not contain a single triggering event.

For each Phase complete four reasoning steps:

1. **What was most likely repriced?** State the business, financial, duration, or risk belief—not merely the associated news topic.
2. **Why is that interpretation stronger?** Test timing, the C1/C3 economic mechanism, estimate or multiple behavior, cross-sectional behavior, and persistence.
3. **How should drivers rank?** Identify a Primary driver, any genuinely incremental Secondary driver, and an Unresolved item only when it could change the conclusion.
4. **What belief changed?** Express the transition as `from prior pricing belief -> current pricing belief`.

Useful diagnostics include:

- price and relative performance changed with broad estimate revisions: stronger evidence of operating-expectation repricing;
- price rose while near-term estimates were flat and the multiple expanded: test duration, long-run margin, optionality, lower failure risk, or discount-rate effects;
- estimates rose while price was flat or fell: test prior anticipation, offsetting risk, multiple compression, or crowding;
- price led revisions: possible early market inference or stale consensus, not proof of information leakage;
- a move reversed quickly without estimate or operating change: treat the fundamental attribution cautiously.

Do not equate an event-day rise with acceptance or no rise with “already priced in.” Keep unassigned residuals when evidence does not support a clean attribution.

#### Optional historical pricing sensitivity

Use this only if comparable disclosures help identify a repeated sensitivity relevant to today's themes. Select roughly 4–8 major earnings or disclosures with comparable pre-release baselines. Consider revenue/segment/KPI surprise, margin, EPS/FCF, guidance, milestones, abnormal return, post-event drift/reversal, and later estimate changes.

Do not create an event dump or force a confounded sample into a regression. Extract only a repeated conclusion such as “guidance and margin have mattered more than headline revenue.” Historical sensitivity identifies emphasized dimensions; it does not prove a numeric current condition.

### Step 4 — Infer business, financial, and duration conditions

This is the core of C5. For each selected Pricing Theme, distinguish:

1. **Current business/financial baseline** — the concise C1/C3 state plus the relevant management or sell-side baseline.
2. **What current price requires** — the operating or financial bar broadly necessary for current valuation consistency.
3. **Duration requirement** — how long growth, margin, share, returns, capacity, or milestone delivery must persist.
4. **Condition combinations** — normally 2–3 economically distinct combinations that could support or fail current pricing.
5. **Market-Implied Conclusion** — one explicit sentence stating the most defensible current pricing requirement and its main condition.

Do not demand a uniquely identified number. Apply this inference ladder:

`numeric value/range -> binding condition or scenario set -> pricing question -> material Unknown`

A condition is useful when it excludes economically meaningful cases even if several parameter combinations remain possible. Examples include “current valuation needs both double-digit growth through the next platform cycle and no structural margin reset,” or “price can tolerate a near-term margin dip only if it is temporary and estimate revisions preserve the following year's earnings path.” These are conditional inferences, not forecasts.

#### Construct condition combinations rather than decorative bull/base/bear cases

Each combination should vary the few variables that determine value for the Theme: growth and margin, price and volume, share and industry size, FCF margin and reinvestment, milestone success and delay, or operating level and duration. Avoid changing every assumption simultaneously. State what evidence would distinguish combinations and which are inconsistent with current pricing.

Assess duration explicitly. High near-term growth may not support price if it decays too quickly; a lower operating level may support the same value if it persists longer. For cyclical businesses, solve both level and duration. For high-growth businesses, test maturation, required reinvestment, dilution, and failure/delay rather than assuming current growth indefinitely.

#### Use reverse valuation only when it narrows the answer

Reverse valuation fixes observed market value as the model output and solves for one or at most two focal operating assumptions:

`model value under assumed conditions = observed market value`

It is not a fair-value exercise. Select a model suited to the claim:

- established non-financial company: FCFF/EV, or FCFE only with stable transparent leverage;
- financial institution: residual income or conditional P/B–ROE;
- cyclical/commodity company: normalized price, volume, cost, profit level, and cycle duration;
- high-growth or pre-commercial company: maturation/milestone scenarios including financing, dilution, failure, and delay;
- multi-business company: SOTP with corporate costs, cross-holdings, and net debt reconciled once;
- REIT/asset-backed company: NAV/cap-rate and AFFO with consistent leverage and asset quality.

For FCFF:

`EV_0 = sum[FCFF_t / (1 + WACC)^t] + terminal value / (1 + WACC)^N`

`FCFF = NOPAT - reinvestment`

In stable growth:

`reinvestment rate ≈ growth / ROIC`

`FCFF ≈ NOPAT × (1 - growth / ROIC)`

This prevents unsupported growth without investment. If using perpetual growth, require `WACC > stable growth`, a mature margin/ROIC/reinvestment state, and disclosure of terminal-value dependence. An exit multiple imports another market assumption and must be labelled accordingly.

Prefer small two-dimensional surfaces that match the Theme:

- growth × margin;
- growth × duration;
- FCF margin × duration;
- market share × industry size;
- price/unit economics × cycle duration.

Fix evidence-backed inputs, vary one or two focal assumptions, reconcile current equity/enterprise value, fiscal periods, dilution and net debt, and compare the implied bar with management, consensus, historical delivery, capacity/allocation, and milestone evidence. Show ranges rather than a false point estimate.

Stop and omit the calculation if sensitivity is so wide that it excludes no meaningful scenario, the denominator or capital structure cannot be reconciled, or more than two unknowns must be solved from one price. In that case retain the bounded condition or pricing question. Do not let an uninformative model lengthen the report.

Scenario weights are optional and only valid when two exhaustive, independently specified values bracket market value:

`conditional high-case weight = (market value - low-case value) / (high-case value - low-case value)`

The result must lie in `[0,1]`. It is a conditional price-consistency weight, not a real-world probability; risk premia, omitted states, optionality, liquidity, and model error remain embedded.

### Step 5 — Use auxiliary evidence only when it changes interpretation

#### Sell-side

Integrate revisions and views into the relevant baseline, Phase, or Theme. Test magnitude, breadth, dispersion, freshness, fiscal roll, and whether one contributor drives the aggregate. Sell-side evidence can define a public baseline or show expectation change; it does not by itself reveal the price-required condition.

#### Options

Use only when liquid, timestamp-aligned options add information about an event move, uncertainty timing, skew, or expiry structure. Record the minimum necessary spot, expiry, strike/delta, bid/ask or mid, open interest/volume, and time.

`IV × sqrt(T)` is an approximate annualized one-standard-deviation scale under model assumptions. A straddle/spot estimate includes non-event time, risk premium, spread, and model effects. Skew includes tail pricing and hedging supply/demand. Risk-neutral option prices are not direct physical forecasts and do not determine direction.

#### Positioning, short interest, and market mechanics

Use only if extreme crowding, squeeze risk, borrow constraint, option concentration, or liquidity plausibly changes interpretation. Short interest is delayed; short-sale volume is not open short interest; ownership filings may be stale. High short interest plus a rise does not prove a squeeze. Require supporting borrow/float, volume, catalyst, reversal, or fundamental-divergence evidence.

Benchmark, sector, peer, rates, FX, commodity, volatility, liquidity, index, or corporate-action controls are likewise question-driven. Include them inside the relevant Phase or Theme only if they materially strengthen, weaken, or limit the conclusion. Do not create a separate evidence or technical-distortion section.

### Step 6 — Convert conclusions into anchors, questions, and true Unknowns

A Market Anchor Candidate needs a recognizable parameter or Pricing Theme, a current implied state, a time scope, and a concise basis. It may be conditional. Overall price appreciation, generic optimism, or driver attention without a state is not an anchor.

Document 2 decides whether to construct a formal `MARKET_IMPLIED StateValue`. C5 only indicates one of two downstream uses:

- **MARKET_IMPLIED candidate** — sufficiently specific for formal consideration;
- **Conditional anchor** — economically useful but dependent on an explicit condition or bounded scenario set.

If none qualifies, state that no sufficiently specific Market Anchor Candidate was formed and proceed to useful Pricing Questions. Never place `NOT_IDENTIFIABLE` in the anchor table merely to fill it.

A Pricing Question contains only:

1. why the issue is important to current pricing;
2. what tendency the market currently appears to express;
3. what expectation Document 2 should research or manage.

It is not a formal `PotentialGap` and should reference the existing Theme rather than repeat its complete causal chain.

An Unknown belongs in the final section only when obtaining the information could materially change a core Market-Implied Conclusion or anchor decision. Tool/provider failures, routing, generic timestamp gaps, ordinary data imperfections, handoff ownership, and cross-node action lists are not report Unknowns. State what cannot yet be concluded and why, without assigning another Agent a task.

## Section-by-section output specification

All final report section titles and table headers must be Chinese. Use the exact five top-level titles below; do not add an executive-summary section.

### 一、当前市场定价基线

Provide this compact table:

| 维度 | 当前状态 | 近期变化 | 研究含义 |
|---|---|---|---|
| 股价与相对表现 |  |  |  |
| 前瞻估值 |  |  |  |
| 盈利预期 |  |  |  |
| 盈利与倍数贡献 |  |  |  |

Use only relevant windows and cite factual values in their cells. Do not add an input-availability table or provider audit. After the table, give a short, direct conclusion on the current pricing state. This section says what pricing looks like, not why every move occurred.

### 二、近期重定价与主要定价驱动

Begin with a concise judgment on the current pricing regime. Then use one flexible structure block per material Phase:

#### 重定价阶段：时间范围 / 阶段名称

- **市场表现**：price, relative performance, multiple, estimate, and persistence evidence that matters.
- **主要定价判断**：what business, financial, duration, or risk belief was repriced.
- **驱动排序**：Primary, Secondary, and optional Unresolved drivers, with concise reasoning.
- **定价含义**：the market belief changed from what to what.

Do not force every Phase into a wide table. If historical samples add a repeated insight, append `### 历史定价敏感性` with a concise synthesis; otherwise omit it.

### 三、市场隐含的业务、财务与持续期条件

Write one independent structure block per important Pricing Theme. Each must make the following identifiable without mechanical fixed subheadings:

- current business/financial baseline;
- what current price requires;
- duration requirement;
- main conditions and failure conditions;
- evidence, assumptions, and bounded limitations;
- a mandatory one-sentence **市场隐含结论**.

When condition comparison helps, use this table:

| 条件组合 | 业务条件 | 财务条件 | 持续期要求 | 与当前定价的一致性 | 主要验证信号 |
|---|---|---|---|---|---|

Use 2–3 economically distinct combinations, not ornamental bull/base/bear labels. Add a small sensitivity table only when an informative reverse valuation was actually completed; all its headers must also be Chinese.

### 四、市场锚点与定价问题

For qualified candidates use:

| 参数或定价主题 | 当前市场隐含状态 | 时间范围 | 主要依据 | 下游用途 |
|---|---|---|---|---|

`下游用途` is either `MARKET_IMPLIED 候选` or `条件性锚点`. If no row qualifies, state that clearly instead of inserting an Unknown.

Then write only material `### 定价问题` blocks covering:

- **为什么重要**；
- **当前市场倾向**；
- **Document2 需要研究或管理的预期**。

### 五、关键未知项与识别边界

Keep this short. Use:

| 关键未知项 | 可能改变的核心结论 | 当前不能确认什么 | 识别边界或所需证据 |
|---|---|---|---|

Include only decision-changing Unknowns. Do not include handoffs, owning nodes, provider logs, fallback status, or generic limitations already disclosed next to a claim.

## Zero-context completion and final quality gate

Before submitting, assume a fresh downstream Agent sees only this report. Verify:

- the report contains exactly the five required Chinese top-level sections and all table headers are Chinese;
- C5 stands independently and does not read or summarize the Market Situation O4 report;
- C1/C3 are used as economic starting points rather than repeated as research reports;
- the current baseline distinguishes price/relative performance, valuation, earnings revisions, and earnings-versus-multiple contribution where evidence allows;
- Repricing Phases begin from observed pricing change, do not require a discrete event, rank drivers, and state how pricing belief changed;
- a few Pricing Themes receive most of the analysis, each ending in an explicit Market-Implied Conclusion;
- inference descends from number/range to binding condition, pricing question, then Unknown rather than defaulting to non-identification;
- reverse valuation is used only when it narrows the answer, varies no more than two focal assumptions, and never becomes a price target;
- growth, margin, reinvestment, ROIC, duration, terminal state, net debt, dilution, and fiscal-period roll are internally consistent where relevant;
- sell-side, options, positioning, and common-factor evidence appear only where they change interpretation;
- facts are cited and point-in-time consistent, while calculations and conditional interpretations remain distinguishable;
- anchors specify an implied state and time scope; an empty anchor table is allowed;
- Unknowns are short and capable of changing a core conclusion;
- no formal expectation/gap/absorption object, target price, valuation verdict, trade recommendation, technical level, forced “priced in” claim, provider audit, or cross-node handoff remains.
