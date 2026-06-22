+++
kind = "external_skill_package"
id = "competitive-analysis"
name = "Competitive Analysis"
version = "2026.06.01"
source_project = "anthropics/financial-services"
source_kind = "financial_services"
applicable_agents = ["C3"]
applicable_task_types = ["generate_global_research"]
+++
---

name: competitive-analysis
description: Framework for building competitive landscape reports — market positioning, competitor deep-dives, comparative analysis, strategic synthesis. Use when the agent needs to analyze a competitive landscape, competitor analysis, peer comparison, market positioning assessment, strategic review, or investment memo section. Also triggers on "who are the competitors to X", "benchmark X against peers", "build a market map", or any request to systematically evaluate competitive dynamics across an industry.
--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

# Competitive Landscape Mapping

Build a complete competitive analysis report. The workflow is: scope the analysis, gather data through available tools, then produce a structured report.

## Phase 1 — Data Gathering

Data should be gathered through available tools. If some data is unavailable or unreliable, mark it as missing and move on. Do not over-spend effort on non-critical gaps.

## Phase 2 — Build the report

Create a concise structured report with section titles and one-line content logic before writing the full content. In DoxAgent workflows, do not wait for manual outline approval unless the runtime explicitly supports user clarification.

Use Markdown sections and tables. Keep the report suitable for downstream agents to read and reuse.

---

## Standards — apply throughout

### Prompt fidelity

When the task specifies something, that's a requirement, not a suggestion:

* **Section titles and names** — preserve exact wording when provided.
* **narrative** — a short written synthesis is requested, keep it concise.
* **Complete data series** — if the task lists 7 competitors, include all 7 unless data is unavailable.
* **Exact values and ratios** — preserve specified values and ratios; do not silently replace them with approximations.

### Source quality, when sources conflict

1. 10-Ks / annual reports / filings
2. Earnings calls / investor presentations
3. internal tool outputs with traceable source IDs
4. Market data / financial data tools
5. Sell-side research
6. Industry reports
7. News, for recent developments only; prioritize items that changed market expectations, customer demand, supply, pricing, regulation, or peer positioning.

### Data comparability

* All competitor metrics should be from the same fiscal year where possible; flag exceptions explicitly.
* Use the same metric definitions across competitors.
* Convert to USD for international companies if necessary; note the exchange rate and date if used.
* Missing data shows as `-` or `N/A`, with `[E]` for estimates.
* Important numbers should include source references or evidence IDs where available.

---

## Analysis workflow

### Step 0 — Industry-defining metrics

Before anything else: what 3-5 metrics does this industry actually run on? Use these consistently across every competitor.

| Industry     | Key metrics                                                     |
| ------------ | --------------------------------------------------------------- |
| SaaS         | ARR, NRR, CAC payback, LTV/CAC, Rule of 40                      |
| Payments     | GPV, take rate, attach rate, transaction margin                 |
| Marketplaces | GMV, take rate, buyer/seller ratio, repeat rate                 |
| Retail       | Same-store sales, inventory turns, sales per sq ft              |
| Logistics    | Volume, cost per unit, on-time delivery %, capacity utilization |

Industry not listed — pick the metrics investors and operators benchmark on.

### Step 1 — Market context

Size, growth, drivers, headwinds. With sources where available.

Correct: "Embedded payments is $80-100B in 2024, growing 20-25% CAGR (McKinsey 2024)"

Wrong: "The market is large and growing rapidly"

If reliable market sizing is unavailable, skip exact sizing and state the limitation.

### Step 2 — Industry economics

Map how value flows. Approach depends on industry structure:

* Vertically structured — value chain layers, typical margin at each
* Platform/network — ecosystem participants, value flows between them
* Fragmented — consolidation dynamics, margin differences by scale

### Step 3 — Target company profile

| Metric        | Value             |
| ------------- | ----------------- |
| Revenue       | $4.96B            |
| Growth        | +26% YoY          |
| Gross Margin  | 45%               |
| Profitability | $373M Adj. EBITDA |
| Customers     | 134K              |
| Retention     | 92%               |
| Market Share  | ~15%              |

Multi-segment companies add a breakdown when relevant:

| Segment | Revenue | Rev YoY | Rev % | EBITDA | EBITDA YoY | Margin |
| ------- | ------- | ------- | ----- | ------ | ---------- | ------ |
| Seg A   | $25.1B  | +26%    | 57%   | $6.5B  | +31%       | 26%    |
| Seg B   | $13.8B  | +31%    | 31%   | $2.5B  | +64%       | 18%    |
| Seg C   | $5.1B   | -2%     | 12%   | -$74M  | -16%       | -1%    |
| Total   | $44.0B  | +18%    | 100%  | $6.5B* | -          | 15%    |

*Note corporate costs if applicable.

### Step 4 — Competitor mapping

Group by whichever lens fits:

* By business model — platform / vertical / horizontal
* By segment — enterprise / SMB / consumer
* By posture — direct / adjacent / emerging
* By origin — incumbent / disruptor / new entrant

### Step 5 — Positioning view

| Type                   | When                                     |
| ---------------------- | ---------------------------------------- |
| 2×2 matrix logic       | Two dominant competitive factors         |
| Radar-style comparison | Multi-factor comparison                  |
| Tier diagram logic     | Natural clustering into strategic groups |
| Value chain map logic  | Vertical industries                      |
| Ecosystem map logic    | Platform markets                         |

Describe the positioning in text or Markdown tables. Use visual logic without producing visual artifacts.

### Step 6 — Competitor deep-dives

Two tables per competitor where data is available.

**Metrics:**

| Metric        | Value        |
| ------------- | ------------ |
| Revenue       | $X.XB        |
| Growth        | +XX% YoY     |
| Gross Margin  | XX%          |
| Market Cap    | $X.XB        |
| Profitability | $XXXM EBITDA |
| Customers     | XXK          |
| Retention     | XX%          |
| Market Share  | ~XX%         |

**Qualitative:**

| Category   | Assessment                  |
| ---------- | --------------------------- |
| Business   | What they do, in 1 sentence |
| Strengths  | 2-3 bullets                 |
| Weaknesses | 2-3 bullets                 |
| Strategy   | Current priorities          |

If data is not available, do not force the table. Provide a short note and mark key gaps.

### Step 7 — Comparative analysis

| Dimension | Company A | Company B | Company C |
| --------- | --------- | --------- | --------- |
| Scale     | $160B     | $45B      | $8B       |
| Growth    | +26%      | +35%      | +22%      |
| Margins   | 7.5%      | 3.2%      | 15%       |

Use concise interpretation after the table. Focus on what matters for competitive position, industry variables, and market expectations.

### Step 8 — Strategic context

M&A transactions, partnership trends, capital raising patterns, regulatory developments, product launches, capacity expansion, pricing changes, and major customer wins/losses. In DoxAgent Document 1, lead with recent strategic changes before broader competitive background.

Use this section only when it is relevant and data is available.

### Step 9 — Synthesis

Moat assessment — rate each competitor Strong / Moderate / Weak on:

| Moat              | What to assess                                                      |
| ----------------- | ------------------------------------------------------------------- |
| Network effects   | User/supplier flywheel strength; cross-side vs same-side            |
| Switching costs   | Technical integration depth, contractual lock-in, behavioral habits |
| Scale economies   | Unit cost advantages at volume; minimum efficient scale             |
| Intangible assets | Brand, proprietary data, regulatory licenses, patents               |

Required synthesis elements:

* Durable advantages — map to moat categories
* Structural vulnerabilities
* Current state vs. trajectory
* Key competitive variables relevant to market expectations
* Monitoring implications for competitors, industry events, supply chain, regulation, or customer demand

For investment contexts only:

| Scenario | Probability | Key driver                               |
| -------- | ----------- | ---------------------------------------- |
| Bull     | 30%         | Market share gains, margin expansion     |
| Base     | 50%         | Current trajectory continues             |
| Bear     | 20%         | Competitive pressure, margin compression |

Skip scenario framing if the task does not require it or if data is insufficient.

---

## Quality checklist

Before finishing:

### Prompt fidelity

* Section names match what the task specified, if provided
* Every competitor/year/data point listed in the task is addressed
* Exact values and formats are preserved when specified

### Data consistency

* Same metric shows the same value wherever it appears
* Same fiscal period is used where possible, or exceptions are flagged
* Missing data is marked as N/A, -, or [E]
* Important numbers have source references or evidence IDs where available

### Content

* Industry-defining metrics are identified
* Competitors are grouped logically
* Target company position is clear
* Comparative analysis leads to a clear synthesis
* Strategic context is included only when relevant
* Unsupported or unavailable parts are skipped rather than forced

### DoxAgent fit

* Output is a structured Markdown report
* Data comes from tools/search/context, not assumed user files
* The report can support C3 industry research, O1 expectation construction, and O2 monitoring configuration
* If a section lacks enough data, state the gap briefly and move on
