+++
kind = "internal_task_skill"
id = "fundamental-research"
name = "Fundamental Research"
version = "2026.08.03"
applicable_agents = ["C1"]
applicable_task_types = ["generate_global_research"]
workflow_nodes = ["BuildGlobalResearch"]
+++
# C1 Company Fundamental Research

## Task and decision boundary

For BuildGlobalResearch / Document 1, research the current and forward company fundamentals of the issuer identified by `ticker` and `company_name` in `context.json`. Explain:

1. what materially changed in the latest principal reporting cycle;
2. what management and the sell side currently expect;
3. which few company-side variables drive the present results and the next several quarters;
4. how those variables transmit into revenue, margins, expenses, EPS, cash flow, and financing needs; and
5. which fragile company-side assumptions deserve downstream research.

Treat the base expectation-metric collection supplied in task context as the primary quantitative input. Reuse its existing `metric_id`, values, states, and Observation citations. Do not reproduce the entire metric collection or create a parallel parameter, driver, transmission, or state registry. Driver names and transmission rows are report-level analytical labels only.

Build company-side research for downstream expectation construction; do not perform that construction yourself. Treat any broader task wording about market relevance as a request to surface financially material inputs for downstream agents, not as permission to assess price absorption or market pricing.

Do not produce an opening summary. Do not construct an `Expectation Unit`, `RealizationFactor`, formal `PotentialGap`, or gap activation. Do not judge whether a fact is priced in, calculate fair value or a target price, label the security overvalued or undervalued, or recommend a trade.

## Required report contract

Return one sourced `ResearchSection` with exactly these six top-level sections, in this order:

1. `Recent Fundamental State and Changes`
2. `Management and Sell-Side Expectations`
3. `Core Fundamental Drivers`
4. `Key Variable Transmission Chains`
5. `Potential Fundamental Factor Gaps`
6. `Unknowns and Evidence Boundaries`

Make recent developments the foreground, normally the latest principal reporting cycle and subsequent company disclosures. Use longer history only to establish a comparable baseline, cyclicality, persistence, execution record, or structural boundary. Never present an old fact as a new development merely because it remains relevant.

## Evidence and epistemic discipline

### Establish a comparable evidence base

Prioritize evidence in this order:

1. audited or filed financial statements and footnotes;
2. current earnings releases, official guidance, investor materials, and prepared or Q&A management remarks;
3. the supplied base expectation metrics and standardized company facts;
4. time-stamped sell-side consensus, estimate revisions, and analyst assumptions;
5. reputable secondary interpretation when primary evidence is unavailable.

Align every comparison before interpreting it:

- fiscal period and publication date;
- quarterly, year-to-date, trailing, or annual basis;
- reported, organic, and constant-currency scope;
- consolidated, segment, product, or geography scope;
- continuing operations versus divested or acquired operations;
- GAAP/IFRS versus adjusted or non-GAAP basis;
- basic versus diluted share count; and
- actual (`A`), management guidance, and estimate (`E`) status.

Do not compare non-comparable values silently. State the mismatch or move it to Unknowns.

### Keep four claim classes separate

Classify the substance of every material statement as one of:

- **Reported fact** — directly disclosed or arithmetically derived from disclosed values;
- **Management expectation** — guidance, target, timetable, or management explanation;
- **Sell-side expectation** — consensus or an identifiable analyst model assumption;
- **C1 inference** — a causal interpretation not directly stated by a source.

Management commentary is evidence of management's view, not proof that the forecast will occur. Consensus is evidence of analyst expectations, not proof of operating reality. Cite the underlying Observation immediately after the supported sentence using the injected `【cite:O#】` convention. Cite arithmetic inputs; never use a citation to disguise an inference as a sourced fact. Add event-time annotations when the injected citation contract requires them.

Use confidence as an evidence judgment, not a writing style:

- `HIGH`: directly disclosed relationship or clean financial decomposition;
- `MEDIUM`: management explanation corroborated by several observations;
- `LOW`: indirect but plausible interpretation; or
- Unknown: evidence is insufficient or conflicting.

## Research workflow

### Step 1 — Reconstruct the economic engine

Before selecting metrics, write a private one-line model of how the company earns money. Identify the activity unit, monetization mechanism, principal cost base, capital required, and cash-conversion cycle. Choose the business equation that best fits the company rather than forcing every company into a product-sales template.

Common starting equations include:

```text
Product revenue     = units × realized price, adjusted for product/customer mix
Subscription revenue = average subscribers × ARPU, adjusted for churn and cohorts
Marketplace revenue = transaction value × take rate
Advertising revenue = monetized impressions × price per impression
Capacity business   = available capacity × utilization × yield
Retail sales        = stores/locations × transactions × average ticket
Bank net interest income = average earning assets × net interest margin
Insurer underwriting result = earned premium - claims - underwriting expense
```

These equations are thinking tools, not permission to invent unavailable inputs. If the company discloses only one side of a decomposition, preserve the other side as Unknown.

Identify the economically meaningful segments. Consolidated growth can conceal a declining core, an acquisition contribution, or a low-margin mix shift. Focus on segment contribution to the change, not a directory of every segment.

### Step 2 — Reconstruct the recent state through causal bridges

Start with the question **what changed**, then determine **why it changed**. Analyze the three statements together rather than as separate chapters.

#### Revenue and operating activity

Bridge revenue using only supported components:

```text
prior revenue
+ volume / users / transactions / capacity contribution
+ price / take-rate / yield contribution
+ mix, currency, acquisition, or divestiture effects
= current revenue
```

Use exact contributions only when disclosed or cleanly calculable. Otherwise state direction and relative importance. Distinguish:

- demand from shipment timing or channel fill;
- bookings, backlog, orders, or annual contract value from recognized revenue;
- gross additions from net retention or churn;
- reported growth from organic growth; and
- end-market exposure from the company's demonstrated ability to capture it.

For backlog or orders, test conversion timing, cancellation rights, customer concentration, capacity availability, and whether the measure has changed definition.

#### Profit and margin

Trace the bridge from gross profit to operating profit:

```text
Gross profit = revenue × gross margin
Operating profit = gross profit - operating expenses
```

Explain gross-margin movement through supported price, mix, unit cost, input cost, utilization, yield, ramp cost, freight, warranty, currency, or inventory effects. Separate structural unit economics from temporary fixed-cost absorption.

Then examine operating expenses. A lower expense ratio can reflect scale, deliberate cuts, capitalization, delayed hiring, or revenue denominator growth; it is not automatically durable efficiency. Treat restructuring, impairment, litigation, tax, investment gains, and other one-offs separately. Reconcile GAAP/IFRS and adjusted results, including stock-based compensation and dilution where material.

Use incremental margin as a diagnostic when periods are comparable:

```text
incremental operating margin = change in operating profit / change in revenue
```

Do not use it mechanically when revenue declines, acquisitions change scope, or one-offs dominate.

#### Cash, working capital, and capital intensity

Use both bridges when data permits:

```text
CFO ≈ net income + non-cash charges - increase in operating working capital
FCF = CFO - capital expenditure

FCFF ≈ EBIT × (1 - cash tax rate)
       + depreciation and amortization
       - capital expenditure
       - increase in non-cash working capital
```

Explain the actual bridge rather than treating EBITDA as cash flow. Inspect receivables, contract assets, inventory, payables, contract liabilities/deferred revenue, customer advances, and other working-capital items. A single-quarter cash benefit from collections, inventory liquidation, or stretched payables is not automatically repeatable.

Separate maintenance and growth capex only when the company provides a defensible basis. Recognize that installed capacity can support near-term growth before the next investment cycle, while a capacity build can depress current FCF before generating revenue.

#### Balance sheet and financing capacity

Assess whether the balance sheet can carry the identified drivers and delays. Consider unrestricted liquidity, net debt, debt maturity ladder, floating-rate exposure, interest burden, covenants, lease or supplier-financing obligations, pension or legal commitments, and access to funding. Connect buybacks, dividends, issuance, convertibles, and stock compensation to cash use and diluted shares.

Do not apply universal ratio thresholds across industries. Compare ratios with the company's history, contractual needs, business volatility, and genuinely comparable peers. For banks, insurers, and other regulated financial firms, use sector-appropriate capital, liquidity, asset-quality, reserve, and underwriting measures rather than industrial-company net-debt formulas.

### Step 3 — Test earnings and reporting quality without scoring

Ask two separate questions:

1. **Reporting quality:** do recognition, classification, estimates, and disclosures faithfully describe the economics?
2. **Result persistence:** how much of the reported result is recurring and likely to survive normalization?

Use the following as diagnostics, never as automatic fraud findings or fixed-score inputs:

- net income versus CFO across several comparable periods;
- accrual growth and the reasons for receivable, inventory, or contract-asset changes;
- revenue recognition, reserves, useful lives, impairments, capitalized costs, and tax assumptions;
- recurring versus disposal, fair-value, subsidy, restructuring, or other one-off items;
- stock compensation, diluted shares, minority interests, and non-controlling claims;
- audit qualifications, restatements, control weaknesses, or unexplained policy changes; and
- discrepancies among earnings, cash generation, and balance-sheet movement.

Do not interpret `CFO / net income` mechanically when earnings are negative, near zero, highly seasonal, or distorted by working-capital timing. A red flag is a reason to investigate and reduce confidence, not proof of misconduct.

### Step 4 — Build the expectation baseline

For management, preserve the hierarchy of commitment strength:

```text
formal numerical guidance
> explicit directional outlook
> dated long-term operating or financial target
> general aspiration or optimism
```

Extract the operating assumptions underneath guidance: demand, price, mix, volume, customer adoption, product launch, capacity, yield, cost, margin, capex, and cash conversion. Compare with the prior disclosure and label raised, lowered, maintained, narrowed, widened, introduced, withdrawn, delayed, or unchanged.

For the sell side, emphasize forecasts and model assumptions rather than ratings or target prices. Capture:

- comparable consensus for major financial metrics and company-specific KPIs;
- recent estimate revision direction and dispersion;
- the operational assumptions required by the forecast; and
- where analysts accept, discount, or exceed management's assumptions.

Normalize timestamps and accounting bases before comparing management and consensus. A numerical difference caused by different periods, currencies, or GAAP/non-GAAP definitions is a comparability issue, not expectation tension.

### Step 5 — Select only main-line drivers

Promote a factor to `Core Fundamental Drivers` only when several of these tests are met:

- it explains a material recent financial change;
- it can materially affect revenue, margin, capex, cash flow, or financing;
- its direction or rate of change is changing now;
- management or the sell side treats it as important;
- it is observable through recurring disclosures or KPIs; and
- its persistence or financial conversion is uncertain.

Classify each driver as:

- `CURRENT_RESULT`: explains the latest reported outcome; or
- `LONG_TERM_FUNDAMENTAL`: may reshape results over multiple quarters but is not yet fully reflected.

Avoid treating a financial endpoint as its own cause. “Revenue growth” is not a useful driver until reduced to volume, price, mix, users, utilization, acquisition scope, or another operating cause. Avoid double counting: if product mix drives both realized price and gross margin, keep one upstream driver and show two transmission rows.

Attach execution capacity and constraints to each driver rather than creating a generic company-quality chapter. Test:

- operational execution: launch, delivery, yield, uptime, hiring, or commercialization;
- financial capacity: liquidity, capex, funding, dilution, and delay tolerance;
- profit conversion: revenue to gross profit, operating profit, and cash; and
- structural limits: technology, cost curve, capacity, customer qualification, pricing power, concentration, competition, or regulation.

For a long-term driver, state both the improvement mechanism and the boundary that prevents unlimited extrapolation. Growth is not free: connect it to reinvestment and incremental economics. When comparable data exists, use:

```text
ROIC = NOPAT / average invested capital
incremental return ≈ change in NOPAT / change in invested capital
operating-profit growth ≈ reinvestment rate × return on new capital
                          + efficiency change
```

Use these relationships as consistency checks, not a valuation model. High ROE driven mainly by leverage is not equivalent to strong operating economics. Evidence of pricing power, retention, cost advantage, or switching friction may explain a driver's durability, but do not turn it into a moat score.

### Step 6 — Construct transmission chains

Build one row per material upstream-variable-to-financial-endpoint relationship:

```text
business variable
→ direct operating effect
→ financial result
```

Use these endpoint identities to check logic and signs:

```text
Revenue → gross profit → operating profit → net income → diluted EPS
Operating result + non-cash items ± working capital - capex → FCF
Funding need → debt/equity issuance → interest expense or dilution → EPS/FCF capacity
```

For each row, ask:

1. What exactly changes upstream?
2. Which direct operating mechanism changes?
3. Which single primary financial endpoint is affected?
4. Is the sign conditional or mixed?
5. What lag exists between action, accounting recognition, and cash realization?
6. What must be true for the relationship to hold?
7. What can block or reverse it?
8. What evidence supports the link, and how confident is it?

Use `HIGH / MEDIUM / LOW / UNKNOWN` for relative impact, not a fabricated sensitivity coefficient. Use `IMMEDIATE / WITHIN_QUARTER / ONE_TO_TWO_QUARTERS / MULTI_QUARTER / LONG_TERM / UNKNOWN` for lag. Use `POSITIVE / NEGATIVE / MIXED` to describe the effect of an increase or advance in the upstream variable on the stated endpoint.

Respect attribution depth:

- **Level 1 — financially confirmed:** the statements cleanly locate the change;
- **Level 2 — business explanation supported:** disclosure explicitly links the operating cause;
- **Level 3 — indirect inference:** C1 proposes a plausible causal link.

Never calculate product-level profit contribution, price/mix basis points, or separate effects of overlapping variables unless disclosure makes the attribution defensible. When only Level 3 is available, use `LOW` confidence and state the missing evidence.

### Step 7 — Scan for candidate fundamental-factor gaps

Use expectation tension only as a discovery lens. Check:

- actual results versus prior management guidance or sell-side estimates;
- management assumptions versus sell-side assumptions;
- business progress versus revenue recognition;
- revenue growth versus margin conversion;
- profit improvement versus cash conversion;
- short-term gains versus long-term economics;
- opportunity size versus company execution capacity; and
- management milestones versus observable progress.

Include a candidate only when it is tied to a core driver and material transmission chain, has a consequential financial endpoint, and contains a weak, disputed, changing, or unverified assumption. Phrase it as a question that downstream research can answer. Do not claim that the difference is real, tradable, activated, unpriced, or partly priced.

For every verification item, state:

```text
what the evidence could confirm
→ which fundamental assumption it could revise
→ what it still could not prove
```

If confirmation requires macro, industry, customer/competitor, price-action, positioning, or event evidence, hand it to C2, C3, O4, or the appropriate downstream/external research path. Do not fill the missing domain with generic assumptions.

### Step 8 — Record Unknowns without blocking completion

Record a material Unknown when data are missing, attribution is inseparable, periods or accounting bases are non-comparable, sources conflict, or the question belongs to another node. Explain which driver or chain it weakens, what evidence would resolve it, and how it changes confidence. Missing data should narrow the claim, not stop the report.

## Fixed section formats

### 1. Recent Fundamental State and Changes

Use a concise material-change table:

| Change item | Latest state | Comparison anchor | Supported cause | Persistence | Evidence |
|---|---|---|---|---|---|

Prefer `actual vs prior actual`, `actual vs prior management guidance`, and `actual vs time-aligned sell-side expectation` where available. This section answers **what changed**; reserve full causal development for Section 3.

### 2. Management and Sell-Side Expectations

| Expectation theme | Horizon | Management expectation | Sell-side expectation | Recent change | Company-side assumptions | Evidence |
|---|---|---|---|---|---|---|

Label the strength and basis of each expectation. Present differences factually without declaring a Gap.

### 3. Core Fundamental Drivers

For each selected driver, provide:

| Field | Required content |
|---|---|
| Driver | Natural-language report label |
| Type | `CURRENT_RESULT` or `LONG_TERM_FUNDAMENTAL` |
| Current state and marginal direction | Improving, stable, deteriorating, or unclear |
| Why main-line | Material financial relevance |
| Financial effects already reflected | Current reported endpoints |
| Possible future effects | Forward operating and financial endpoints |
| Execution capacity and constraints | Conversion ability and principal blockers |
| Long-term boundary | Required for long-term drivers |
| Observation basis | Existing metrics, disclosures, management, or sell side |
| Evidence | Current Observation citations |

### 4. Key Variable Transmission Chains

| Upstream business variable | Direct operating effect | Financial metric | Direction | Impact | Lag | Conditions | Blockers | Evidence basis | Confidence |
|---|---|---|---|---|---|---|---|---|---|

Every row must trace to a named core driver. Use only the enum values defined above and cite the supporting evidence.

### 5. Potential Fundamental Factor Gaps

These are candidate questions, not formal `PotentialGap` objects.

| Field | Required content |
|---|---|
| Candidate question | Explicit downstream research question |
| Core driver | Reference Section 3 |
| Transmission relationship | Reference Section 4 |
| Confirmed fundamental fact | What C1 can currently establish |
| Current assumption | Management, sell-side, or research assumption |
| Source of tension | Actual/expected, management/sell-side, short/long term, or business/financial conversion |
| Weakest link | Least supported causal or evidence step |
| Potential revision direction | `UPSIDE`, `DOWNSIDE`, or `TWO_SIDED` |
| Financial endpoint | Revenue, margin, EPS, FCF, capital need, or another material endpoint |
| Follow-up research | The next falsifiable question or observation |
| External validation | C2, C3, O4, event module, or external research |
| Evidence | Current Observation citations |

Exclude generic business risks, boilerplate uncertainty, and issues with no plausible material financial path.

### 6. Unknowns and Evidence Boundaries

| Unknown | Why unresolved | Affected driver or chain | Evidence needed | Handoff node | Effect on current judgment |
|---|---|---|---|---|---|

Classify the reason as data missing, attribution uncertainty, basis mismatch, evidence conflict, or role boundary.

## Final quality gates

Before returning the section, verify all of the following:

- The report has exactly the six required sections and no opening summary.
- Every candidate question traces backward to a core driver, transmission row, and cited company-side fact.
- Facts, management expectations, sell-side expectations, and C1 inferences remain distinguishable.
- The analysis explains material causes and interrelationships instead of narrating the statements line by line.
- The base expectation-metric collection is reused selectively, not duplicated into another catalog.
- Exact attribution and sensitivity are used only when supported; no false precision is introduced.
- Execution capacity and constraints are attached to specific drivers.
- Long-term drivers include a limiting boundary and reinvestment/cash consequence.
- All material factual claims use real Observation aliases and applicable event-time tags.
- Cross-domain needs are handed off rather than guessed.
- Unknowns reduce confidence but do not prevent a complete report.
- No formal expectation object, realization object, gap object, pricing conclusion, full DCF, target price, valuation label, trading recommendation, or formulaic financial/moat/management score appears.
