+++
kind = "internal_task_skill"
id = "market-implied-expectations"
name = "Market-Implied Expectations Research"
version = "2026.08.04"
applicable_agents = ["O4"]
applicable_task_types = ["generate_global_research"]
workflow_nodes = ["BuildGlobalResearch"]
+++
# O4-A Market-Implied Expectations Research

## Mission and boundary

Research the target's **pricing structure** before formal expectation construction. Start from C1/C3 drivers; use necessary C2/O4-B controls, the event timeline, and point-in-time price, valuation, sell-side, options, and positioning evidence to answer:

> Which business drivers does the market appear to be trading, what business, financial, duration, and risk-premium conditions are broadly consistent with the current price, and which conclusions are identifiable enough to become downstream market-anchor candidates?

This skill governs **O4-A only**. O4-B first describes macro/sector, liquidity, volatility, trend, and tradability. Use it only to control common effects; never import support/resistance, entry timing, or trading conclusions.

Treat price as the outcome of expected cash flows, their timing, discount rates/risk premia, scenario weights, and market frictions. A price is one observation, not a transcript of investor beliefs. Never claim that price uniquely reveals several unknown assumptions.

Do not:

- rediscover the complete company fundamentals or industry/value chain;
- turn every event into a same-day "priced in / not priced in" judgment;
- construct formal `ExpectationUnit`, `RealizationFactor`, `PotentialGap`, `GapActivation`, `MarketAbsorption`, or persisted `StateValue` objects;
- replace parameter-specific market inference with overall share-price performance;
- calculate a target price, fair-value recommendation, upside/downside, entry point, support/resistance, or trade;
- treat sell-side consensus as the market itself, implied volatility as direction, or positioning as fundamental belief.

O4-A proposes auditable candidates for a downstream `MARKET_IMPLIED` source. Document 2 owns the formal parameter mapping. Event-time priced-in analysis and `MarketAbsorption` belong after a State or Factor update, not in this initialization report.

## Required inputs and readiness gate

Use supplied inputs in this order:

1. **O4-B**: broad/sector state, risk appetite, volatility/liquidity, common trend, deleveraging, squeeze, or other disturbance.
2. **C1**: recent company state, management/sell-side expectations, business-to-financial transmission, core drivers, questions, and Unknowns.
3. **C3**: demand, supply, price, allocation, actor signals, target-business transmission, milestones, questions, and Unknowns.
4. **C2**: only rates, credit, FX, commodities, liquidity, or policy controls needed for attribution.
5. **Base market measurements**: price, equity/enterprise value, forward multiples, historical/peer context, options, positioning, and their timestamps.
6. **Event timeline**: occurrence and first-public times, related atomic events, and candidate driver links.

Record each input as `AVAILABLE`, `STALE`, `PARTIAL`, or `MISSING` with its as-of time. Reuse reliable measurements; do not build another Market Metric Registry or copy every metric.

If current orchestration does not expose this run's C1/C3/O4-B result, state the dependency gap. Do not silently substitute generic company knowledge, invent a driver, or imply that a price-only interpretation is target-linked. Complete the reliable baseline and label unsupported O4-A conclusions `NOT_IDENTIFIABLE`.

## Required O4-A report contract

Use exactly these six top-level O4-A sections, with no investment summary or recommendation:

1. **Current Market Pricing Baseline**
2. **Major Repricing Episodes and Pricing Drivers**
3. **Market-Implied Business, Financial, and Time Scenarios**
4. **Pricing Evidence, Market Uncertainty, and Technical Distortions**
5. **Candidate Market Anchors and Potential Pricing Questions**
6. **Unknowns, Identification Boundaries, and Cross-Node Handoffs**

Keep the report selective: normally 3-6 pricing themes, 2-6 repricing episodes, 4-12 comparable earnings/major-disclosure samples, 2-6 implied-scenario units, 1-5 anchor candidates, and 2-5 pricing questions. These are soft limits. Remove price trivia, isolated one-day stories, generic valuation commentary, and themes that cannot map to a business, financial, or time condition.

## Analytical objects: do not collapse them

- **Market Measurement**: timestamped price, return, volume, valuation, estimate, option, or positioning datum. It is evidence, not an expectation conclusion.
- **Pricing Theme**: a C1/C3 driver selected for market-pricing research, such as margin recovery, customer qualification, supply duration, or share allocation.
- **Repricing Episode**: a sustained interval of abnormal price, multiple, estimate, or volatility change associated with a cluster of potentially relevant information. It replaces event-by-event storytelling.
- **Implied Outcome**: a business, financial, duration, or milestone condition broadly consistent with current price under explicit assumptions.
- **Scenario Consistency**: evidence that price is closer to one defined scenario set than another without claiming a unique number.
- **Market Anchor Candidate**: a parameter- and time-specific inference suitable for downstream consideration as a `MARKET_IMPLIED` state.
- **Pricing Distortion**: positioning, liquidity, flow, mechanical hedging, or market-regime influence that weakens a fundamental interpretation.
- **Alternative Explanation**: another causal account that can explain the same market evidence.

All are report labels only. Do not create a parallel registry or new schema.

## Evidence, time, and calculation discipline

### Preserve point-in-time integrity

Every market inference must identify a valuation date. Align price, diluted shares, net debt, consensus estimates, fiscal period, option snapshot, and positioning data as closely as possible to that date. Never combine a historical price with today's revised estimates and call the result a historical multiple. Use point-in-time consensus when available; otherwise label look-ahead risk.

For every measurement state the source, timestamp/time zone, raw/adjusted and split/dividend treatment, price/total-return basis, currency, fiscal and accounting basis, formula/window, and stale/delayed/sparse/survivorship limits.

Use durable `market_evidence_snapshot` values when present so exact OHLCV dates and numbers survive context compaction. Cite each factual measurement immediately with the workflow's Observation syntax. Cite the input data, not an uncited calculation or inference. Show enough arithmetic for another analyst to reproduce every implied range.

### Separate four epistemic classes

Label each important statement as:

1. **observed market datum**;
2. **reported sell-side/actor expectation**;
3. **O4 calculation** from cited inputs;
4. **O4 interpretation** conditional on assumptions.

Do not upgrade correlation into attribution. `HIGH`, `MEDIUM`, and `LOW` attribution confidence describe evidence quality, not the probability that a driver is true or that price will rise.

## End-to-end research workflow

### Step 1 — Select three to six candidate pricing themes

Start with the short list of C1/C3 main-line drivers, not the price chart. Add a theme only when several of these hold:

- material to revenue, margin, cash flow, balance-sheet risk, or long-run value;
- recently changed or newly evidenced;
- visible in price, valuation, estimates, options, or positioning;
- distinguishable from other themes;
- relevant to the current business cycle and a plausible Document 2 parameter/time scope.

Sources are C1/C3 core drivers, material event clusters, broad revisions, or unusual price/valuation changes. Exclude ordinary news, stale stories, isolated price days, common market/sector moves, and narratives without a business or financial endpoint.

Create a working theme map:

`theme -> C1/C3 driver -> target business state -> financial interface -> expected market evidence -> competing explanations`

Do not report the map as a new registry.

### Step 2 — Build a comparable current pricing baseline

#### Price and relative performance

Use the same trading dates and adjusted-price convention for target, broad benchmark, sector proxy, and a small economically relevant peer basket. Choose comparisons based on business exposure before observing which one produces the desired conclusion. State weights for a peer basket.

Report multi-window absolute and relative returns, material volume/turnover changes, and whether target-specific residual behavior survives simple controls. A research proxy is:

`target-specific return proxy = target return - broad-market/sector/peer common return`

This is not causal proof. If a pre-estimated beta or stable factor model is genuinely available, use it; otherwise do not invent regression coefficients. Disclose benchmark mismatch, changing beta, overlapping sector/peer exposures, corporate actions, and missing bars.

#### Market capitalization, enterprise value, and valuation

Reconcile the valuation numerator before interpreting it:

`basic equity market capitalization = current price x actual shares outstanding`

`enterprise value = basic equity market capitalization + debt + preferred equity + non-controlling interests - cash/non-operating assets`

Reconcile options, RSUs, and convertibles separately when moving to fully diluted value or per-share results; do not blindly multiply price by a diluted-EPS denominator. Treat leases, pensions, associates, investments, and convertibles consistently. Do not mix basic/diluted shares or stale debt with current equity value.

Choose a primary multiple whose denominator matches the business and capital structure. Use the same forward horizon and accounting basis across time and peers. A historical percentile is descriptive only; accounting changes, business-mix change, loss years, interest-rate regimes, and negative denominators can make it incomparable.

#### Earnings-versus-multiple decomposition

When the definitions and forward period are unchanged:

`price = forward metric x valuation multiple`

For P/E, the exact log bridge is:

`change in ln(price) = change in ln(forward EPS) + change in ln(forward P/E)`

For enterprise multiples, perform the bridge on enterprise value and the matching denominator. Separate estimate revision, fiscal-period roll, share-count change, capital-structure change, dividend, and multiple rerating. Do not attribute the residual multiple change to optimism by default; rates, risk premium, duration, mix, and failure-risk changes are alternatives.

#### Sell-side expectation state

Use point-in-time revenue, EPS, FCF, and relevant KPI estimates. Record contributor count, mean/median, dispersion, freshness, basis, and revision window. Measure magnitude and breadth:

`revision breadth = (number of upward revisions - number of downward revisions) / active contributors`

Do not mix accounting bases or fiscal periods. More contributors do not cure stale/non-comparable estimates. Target-price/rating changes are secondary framing evidence, not operating consensus or an implied state.

Output only the baseline here. Do not yet assign each change to a driver.

### Step 3 — Identify repricing episodes, not convenient event days

Define candidate episode boundaries before reading a favored narrative. Look for a sustained combination of:

- abnormal absolute or relative return;
- multiple expansion/contraction or enterprise-value change;
- repeated estimate revisions;
- unusual volume/turnover or persistent post-event drift;
- a volatility-level or term-structure change;
- a cluster of events linked to selected C1/C3 themes;
- price movement before formal confirmation.

Compare pre-information, announcement, short and medium post-event windows. Align after-hours releases to the next session. Use first credible public availability, checking rumors, earlier chain disclosures, and opposite evidence.

For each episode, test candidate drivers on six axes:

1. **timing fit** — did evidence arrive before or during the episode?
2. **mechanism fit** — does C1/C3 show a path to cash flow, duration, or risk?
3. **estimate bridge** — did relevant forecasts or valuation inputs change?
4. **cross-sectional fit** — target-specific, sector-wide, or concentrated in similarly exposed peers?
5. **persistence** — reversal, temporary squeeze, or sustained repricing?
6. **counterfactual fit** — can macro, rates, sector rotation, capital structure, or another theme explain the move as well?

Assign `HIGH` when independent evidence classes align and alternatives are limited; `MEDIUM` when the theme matters but co-moves with alternatives; `LOW` for mainly temporal or single-series evidence. Keep residuals visible.

Never infer:

`event-day rise -> event accepted` or `no rise -> already priced in`.

### Step 4 — Learn the market's sensitivity from comparable disclosures

Use 4-12 comparable earnings/major-disclosure samples. Build each surprise vector against the point-in-time pre-release baseline:

- revenue and relevant segment/KPI surprise;
- gross/operating margin surprise;
- EPS and FCF surprise;
- guidance change;
- product, customer, capacity, regulatory, or milestone progress.

Observe pre-run, announcement abnormal return, drift/reversal, later revisions, and earnings-versus-multiple contribution. Control broad market and sector on the same windows.

Do not force a small, confounded sample into a single-factor regression. Look for repeated qualitative sensitivities: for example, guidance and margin may repeatedly dominate headline revenue. State sample comparability and co-surprises. Historical sensitivity identifies which dimensions the market has emphasized; it does not prove a current numeric implied outcome.

### Step 5 — Reverse-engineer only what the price can identify

#### Begin with the inverse-problem rule

One observed price cannot uniquely solve revenue growth, margin, reinvestment, duration, discount rate, and scenario probability simultaneously. Freeze evidence-backed inputs, vary one or at most two focal assumptions, and show a grid or range. If many combinations fit, report scenario consistency or driver attention rather than a false point estimate.

Example: if the same EV fits 10% growth with a 22% margin and 15% growth with an 18% margin, neither input is identified. Freeze the C1-supported margin band before solving growth; if that band remains wide, report scenario consistency.

Use current price as the model output target. This is not a fair-value exercise:

`model value under assumed conditions = observed market value`

Solve for the operating condition that makes the equality hold. Never turn the solved condition into a price target or a claim that the market is wrong.

#### Select a model that matches the economic claim

- **Established non-financial**: FCFF/EV separates operations from leverage; use FCFE only with stable, transparent leverage/equity cash flow.
- **Financial institution**: use residual income or conditional P/B-ROE; debt is operating, so ordinary EV subtraction is inappropriate.
- **Cyclical/commodity**: solve normalized price, volume, cost, mid-cycle earnings, and peak/trough duration; current P/E often moves inversely with the cycle.
- **High-growth/pre-commercial**: use maturation or milestone scenarios with financing/dilution and failure/delay; do not infer a unique success probability.
- **Multi-business**: use SOTP; reconcile corporate cost, cross-holdings, and net debt once.
- **REIT/asset-backed**: use NAV/cap-rate and AFFO with consistent leverage and asset quality.

#### Apply valuation identities consistently

For an FCFF model:

`EV_0 = sum[FCFF_t / (1 + WACC)^t] + terminal value / (1 + WACC)^N`

`FCFF = NOPAT - reinvestment`

In a stable phase, a useful consistency bridge is:

`reinvestment rate approximately = growth / return on invested capital`

`FCFF approximately = NOPAT x (1 - growth / ROIC)`

This prevents high growth without required investment. Growth creates value only when incremental returns exceed capital cost; growth-option value depends on investment scale, return spread, and duration.

When R&D, brand building, or customer acquisition is economically investment but expensed, test an adjusted reinvestment/ROIC case. Otherwise accounting ROIC can overstate returns and hide the spending required for growth.

If using perpetual growth:

`terminal value_N = FCFF_(N+1) / (WACC - stable growth)`

Require `WACC > stable growth`, mature margin/ROIC/reinvestment, and sustainable long-run growth. Show terminal-value share. An exit multiple imports a future relative assumption; label the circularity.

For a stable financial company under clean-surplus and consistent payout assumptions, a conditional relationship is:

`P/B approximately = (ROE - long-run growth) / (cost of equity - long-run growth)`

Prefer a multistage residual-income model when ROE, credit losses, capital requirements, or payout are transitioning. For cyclicals, solve both **level** and **duration**: the same value may reflect a higher peak for fewer periods or a lower profit level for longer.

#### Build an auditable reverse-solve

For each pricing theme:

1. fix the valuation date and reconcile equity/enterprise value;
2. map the C1/C3 driver to revenue, margin, reinvestment, duration, or risk;
3. set a public/sell-side base case and cite every input;
4. define downside/base/upside or short/base/extended-duration scenarios without assigning invented probabilities;
5. vary one or two focal inputs until model value brackets current market value;
6. run sensitivity across discount rate, terminal assumption, margin, growth/duration, and dilution as relevant;
7. compare the implied bar with management, sell-side, historical delivery, capacity/allocation, and milestone evidence;
8. list other input combinations that also fit the same price.

Use numerical intervals and scenario surfaces, not a single over-precise output. Round to the precision supported by inputs.

#### Use scenario weights only under strict conditions

If exactly two exhaustive scenario values are independently specified and comparable:

`implied weight of high case = (market value - low-case value) / (high-case value - low-case value)`

Require the result to lie in `[0, 1]`; otherwise the scenario set fails to bracket price. This is only a conditional price-consistency weight, not necessarily a real-world probability: risk premia, omitted states, optionality, liquidity, and model error contaminate it. One price cannot identify three or more weights. Never solve both milestone payoff and success probability from the same price.

### Step 6 — Cross-check the implied interpretation

#### Sell-side revisions

Interpret combinations, not single signals:

- price up + broad earnings revisions up: stronger evidence of fundamental-expectation repricing;
- price up + unchanged near-term estimates + multiple up: possible duration, long-run margin, optionality, lower failure risk, or lower discount rate;
- estimates up + price flat/down: possible prior anticipation, offsetting risk, multiple compression, or crowded positioning;
- price leads revisions: possible early market inference, stale consensus, or non-fundamental movement—not proof of information leakage.

Check revision breadth, dispersion, analyst coverage, stale contributors, fiscal roll, and whether one analyst drives the aggregate.

#### Options and uncertainty

Use only liquid, timestamp-aligned options; record spot, expiry, strike/delta, bid/ask or mid, open interest, volume, and time.

- Annualized ATM IV over `T` gives an approximate one-standard-deviation scale of `IV x sqrt(T)` under model assumptions.
- ATM straddle/spot roughly estimates an event-window move but includes non-event time, risk premium, spread, and model effects.
- Compare event expiry with adjacent maturities or isolate forward variance when reliable.
- Compare skew at constant delta/maturity; it includes tail pricing and hedging supply/demand, not clean direction.
- Open interest/volume lack direction without opening/closing and customer/dealer side.

Option distributions are risk-neutral pricing objects, not direct real-world forecasts. They describe uncertainty magnitude, timing, and tails. If data are missing/illiquid, record the gap; never relabel realized volatility as implied.

#### Positioning and crowding

Treat positioning as a reliability modifier:

- short interest is a delayed settlement-date snapshot;
- `days to cover = short interest / average daily share volume` under the provider window;
- short-sale volume is not open short interest, and shorts may be hedges;
- borrow cost/utilization/availability, float, turnover, and option concentration require matched timestamps;
- lagged ownership filings cannot establish current crowding.

High short interest plus a rise does not prove a squeeze. Require constrained borrow/float, extreme volume, catalyst timing, reversal, or fundamental divergence. Mechanical moves do not establish broad belief.

#### O4-B and other alternative explanations

Re-test every theme against broad-market return, sector move, rates/discount rate, FX/commodity exposure, volatility regime, liquidity, deleveraging, index rebalancing, corporate actions, and peer-specific news. State whether each evidence class `STRENGTHENS`, `WEAKENS`, or `DOES_NOT_IDENTIFY` the proposed market-implied interpretation.

### Step 7 — Classify identifiability and hand off candidates

Assign every material implied conclusion one of:

- **HIGH**: direct parameter-value link, explicit reverse method, narrow sensitivity, limited alternatives, and multiple independent market evidence classes.
- **MEDIUM**: a defensible range or scenario ranking, but meaningful sensitivity to valuation, timing, or another driver.
- **LOW**: primarily price/event association; several plausible alternatives; useful only as tentative pricing insight.
- **NOT_IDENTIFIABLE**: available evidence cannot isolate the parameter or scenario. Do not propose a formal market anchor.

Identifiability is not confidence probability and is not written into a downstream schema unless that schema explicitly asks for it.

Use three output layers:

1. **Directly quantifiable** — parameter value/range, phase, direction, and time scope are reproducible. May become a `MARKET_IMPLIED StateValue` candidate.
2. **Scenario consistency** — current price is closer to one stated scenario set than another, conditional on assumptions. Provide as anchor research; recommend formal mapping only if the parameter/time scope is sufficiently clear.
3. **Driver attention** — episodes show which driver repeatedly matters, but no specific state is recoverable. Provide as pricing insight only.

## Section-by-section output specification

### 1. Current Market Pricing Baseline

Provide a compact table:

| Dimension | Current state | Change versus prior point | Main comparator | Research implication | Data/calculation citation |
|---|---|---|---|---|---|

Include only relevant absolute/relative performance, volume, equity/enterprise value, primary valuation, earnings-versus-multiple bridge, and point-in-time consensus/revision state. State valuation date and data-quality limitations. Do not attribute drivers in this section.

### 2. Major Repricing Episodes and Pricing Drivers

Provide:

| Window | Abnormal price/valuation change | Related event cluster | Candidate C1/C3 driver | Estimate revision | Persistence | Alternative explanation | Attribution confidence | Evidence |
|---|---|---|---|---|---|---|---|---|

After the table, explain competing interpretations and unassigned residuals. Add a concise historical earnings/major-disclosure sensitivity synthesis based on the surprise vectors; do not create a separate event dump.

### 3. Market-Implied Business, Financial, and Time Scenarios

Write one independent analysis unit per important theme:

1. pricing theme and corresponding C1/C3 driver;
2. current public/sell-side base;
3. model selected and why;
4. current-price-consistent business/financial outcome or scenario;
5. implied time/duration;
6. valuation, capital-structure, reinvestment, and dilution assumptions;
7. sensitivity grid/range and other combinations that fit;
8. scenarios current price is closer to and inconsistent with;
9. alternatives and identifiability;
10. evidence and reproducible arithmetic.

Use small tables inside a unit when numerical comparisons help. Do not force heterogeneous themes into one wide table.

### 4. Pricing Evidence, Market Uncertainty, and Technical Distortions

For each core theme synthesize:

- sell-side revision direction, magnitude, breadth, dispersion, and lead/lag;
- options-implied move, term/tail information, or explicit data absence;
- short/crowding/liquidity evidence and reporting lag;
- O4-B market/sector/rates/volatility control;
- combined effect: `STRENGTHENS`, `WEAKENS`, or `DOES_NOT_IDENTIFY`;
- evidence citations.

Do not repeat chart trends or technical levels.

### 5. Candidate Market Anchors and Potential Pricing Questions

For anchor candidates provide:

| Pricing theme | Candidate parameter/scenario | Implied state | Time scope | Reverse method | Key assumptions | Main evidence | Identifiability | Recommend downstream `MARKET_IMPLIED` mapping? | Validation needed |
|---|---|---|---|---|---|---|---|---|---|

Recommend mapping only when the parameter, state, time scope, derivation, and sensitivity are auditable. Overall price appreciation is never a parameter-specific anchor.

Then write each potential pricing question separately:

- current pricing phenomenon;
- linked C1/C3 driver;
- stronger/outcome interpretation;
- cautious or alternative interpretation;
- weakest evidence;
- next discriminating observable;
- Document 2 State/Factor/anchor issue to test.

These are research questions, not formal `PotentialGap` or absorption conclusions.

### 6. Unknowns, Identification Boundaries, and Cross-Node Handoffs

Provide:

| Unknown or handoff | Why it is not identifiable | Affected pricing theme | Data/research needed | Owning node | Effect on anchor candidate |
|---|---|---|---|---|---|

Cover multi-driver collinearity, market/sector interference, model and terminal-value sensitivity, event-time ambiguity, stale or sparse consensus, illiquid options, lagged positioning, missing C1/C3/O4-B inputs, and parameter non-identification.

Handoff ownership:

- **C1**: correct or deepen company financial transmission and scenario inputs;
- **C3**: validate industry state, allocation, and milestone assumptions;
- **C2/O4-B**: validate macro, discount-rate, liquidity, volatility, or common-market controls;
- **Document 2/O1**: decide formal parameter mapping and create expectation/gap objects;
- **later activation/absorption workflow**: compare a post-event State/Factor update with the pre-event market anchor.

## Zero-context completion and final quality gate

Before submitting, assume a fresh reviewer sees only this report. Verify that the reviewer can reproduce the reasoning without guessing:

- O4-B and O4-A are visibly separate and O4-A contains no tradability or technical-level conclusion.
- The report states what C1/C3/O4-B inputs were actually available; missing upstream context is not fabricated.
- Three to six themes originate in material C1/C3 drivers and end in business/financial/time conditions.
- Every market series is point-in-time aligned, definition-consistent, timestamped, and cited.
- Relative performance controls market/sector/peer effects but is never presented as causal proof.
- Repricing uses episodes and event clusters, not isolated event-day storytelling.
- Historical disclosure analysis uses multi-variable surprises and acknowledges confounding.
- Each reverse valuation fixes price, selects a suitable model, solves no more than two focal assumptions, and shows sensitivity plus alternative solutions.
- Growth, reinvestment, ROIC, duration, terminal state, net debt, dilution, and fiscal-period roll are internally consistent where relevant.
- Price changes are separated into estimate/fundamental changes, multiple/risk-premium changes, and unexplained residuals where data allow.
- Sell-side, options, positioning, and O4-B controls cross-check rather than define the fundamental expectation.
- Implied volatility is not called direction; option weights are not called physical probabilities; short-sale volume is not called short interest.
- Every implied conclusion has an identifiability label; `NOT_IDENTIFIABLE` produces an Unknown, not a number.
- Directly quantifiable states, scenario consistency, and driver attention remain separate.
- Candidate anchors specify parameter, state/range, time scope, method, assumptions, sensitivity, and evidence.
- There is no formal expectation/gap/absorption object, target price, valuation verdict, trade recommendation, or unsupported "priced in" claim.
