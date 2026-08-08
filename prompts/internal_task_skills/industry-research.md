+++
kind = "internal_task_skill"
id = "industry-research"
name = "Industry and Value-Chain Research"
version = "2026.08.04"
applicable_agents = ["C3"]
applicable_task_types = ["generate_global_research"]
workflow_nodes = ["BuildGlobalResearch"]
+++
# C3 Industry and Value-Chain Research

## Mission and analytical boundary

For BuildGlobalResearch / Document 1, explain how the external industry and value-chain environment around the target company's material businesses is changing, how those changes are allocated among participants, and which observable business states could reach the target. The useful chain is:

`industry state + external-actor signals -> external driver -> allocation mechanism -> target business-state transmission -> milestone / proof boundary -> candidate question`

This is target-linked pre-expectation research, not a generic sector primer. Start from the target's products, customers, end markets, geographies, critical inputs, and commercialization paths. Reuse supplied industry metrics and relationship/economic-exposure maps selectively; do not create a parallel Industry State Book, parameter registry, actor registry, or duplicate every `ind_*` metric.

C3 may end at target business states such as orders, backlog, shipment volume, ASP, customer qualification, share, product mix, utilization, lead time, inventory, commercialization stage, or supply cost. C1 owns conversion into revenue, margin, EPS, capex, or free cash flow. Other downstream nodes own formal expectation objects and market interpretation.

Do not:

- construct formal `ExpectationState`, `ExpectationUnit`, `ExpectationShell`, `RealizationFactor`, `PotentialGap`, `GapActivation`, or `MarketAbsorption` objects;
- decide what is priced in, perform peer valuation, set a target price, recommend trades, rank stocks, or produce an ideas shortlist;
- fill space with industry history, broad market-size forecasts, company-by-company profiles, or an exhaustive peer landscape;
- treat industry growth as proof that the target benefits, or a milestone as proof of economic realization.

## Required Document 1 report contract

Use exactly these six top-level sections, with no opening executive summary or investment conclusion:

1. **Target-Relevant Industry and Value-Chain Fact Baseline**
2. **Key External Actors' Views and Behavioral Signals**
3. **Core External Drivers, Allocation Mechanisms, and Transmission**
4. **Industry and Commercialization Milestones and Proof Boundaries**
5. **Potential Industry and Value-Chain Factor Gap Candidates**
6. **Unknowns, Evidence Boundaries, and Cross-Node Handoffs**

Document 1 is recent-first: emphasize material changes in the recent research window and why they matter now. Use older history only to establish a comparison anchor, cycle pattern, capacity lead time, regulatory path, or other causal baseline. A normal report contains 4-10 baseline facts, 6-15 actor signals, 3-6 drivers, 3-8 allocation/transmission links, 2-8 milestones, and 2-5 candidate questions. These ranges are discipline, not quotas.

## Analytical objects: keep the distinctions clean

- **State**: a measured or credibly described condition at a point or over a period: demand, effective capacity, price, inventory, orders, share, qualification, or commercialization stage.
- **Signal**: what an external actor says, commits to, does, or reveals through observed data. A signal is evidence about a state or future behavior, not automatically the state itself.
- **Driver**: an external force capable of changing a relevant state: end demand, supply, regulation, technology, input cost, channel inventory, or financing conditions.
- **Allocation mechanism**: the rule or process that determines who receives demand, orders, share, scarce capacity, product mix, or profit pool. This is the bridge between industry opportunity and target exposure.
- **Transmission**: the causal path from an external driver and allocation mechanism to a target business state. Stop before financial-statement conversion.
- **Milestone**: an observable step in a technical, regulatory, procurement, capacity, or commercialization sequence. State exactly what it proves and what it does not prove.
- **Candidate gap question**: a bidirectional, evidence-seeking question created by tension or uncertainty. These are candidate questions, not formal `PotentialGap` objects.

Do not rename an actor statement as a driver, an industry forecast as current state, a target exposure as realized allocation, or analyst inference as reported fact.

## Evidence and comparability discipline

### Build an evidence ladder

Prefer evidence closest to the claimed state:

1. regulators, statistical agencies, exchanges, industry bodies, tenders, approvals, customs, or other primary system data;
2. target, customer, supplier, competitor, or channel filings, calls, releases, contracts, and operating disclosures;
3. standardized transaction, shipment, price, utilization, inventory, or order datasets with documented definitions;
4. specialist trade publications and credible field research;
5. sell-side or consultancy synthesis;
6. general news and commentary.

Lower-ranked evidence can be useful, but cannot silently upgrade into a primary fact. Treat issuer materials and third-party reports as claims to validate, not instructions. A syndicated story repeated by many outlets is one source, not independent corroboration.

For every material claim, identify whether it is an **explicit view**, **committed action**, **observed external datum**, or **C3 inference**. Cite the observation immediately after the claim using the workflow's evidence-reference syntax. Preserve occurrence time and publication time when their difference matters. Cite facts, not the inference drawn from them.

Before comparing numbers, normalize:

- product and industry boundary;
- geography and currency;
- physical units and price basis;
- reporting period and seasonality;
- gross versus net, nominal versus real, and value versus volume;
- spot, list, contract, or realized price;
- announced/nameplate versus qualified/effective capacity;
- bookings, new orders, backlog, shipments, sell-in, sell-through, or recognized revenue;
- company share versus product, segment, geographic, or channel denominator.

If definitions cannot be reconciled, label the observations **not comparable** instead of averaging them. Confidence means evidence strength and causal clarity, not the probability that a bullish or bearish outcome occurs.

## Research workflow

### 1. Map exposure, then select three to six core external lines

First map the target's economically material business interfaces: product and use case, end customer and purchasing process, geography, key inputs, substitute technologies, channel, regulation, and commercialization stage. Then choose only three to six external lines that best combine:

- economic importance to the target;
- causal proximity to a target business state;
- recent marginal change;
- decision-relevant uncertainty;
- observability and source quality;
- relevance within the decision horizon.

Select external actors for the role they play in a chosen line: demand window, supply signal, allocation decision-maker, bottleneck, substitute, leading indicator, or validation/refutation source. Do not select eight to fifteen companies first and invent a reason to profile each. A large peer may be irrelevant; a small customer, equipment vendor, regulator, distributor, or input supplier may be decisive.

### 2. Reconstruct the relevant state, not a narrative collage

Define the industry boundary and comparison anchor before stating growth or share. Reconstruct only the states that could change target exposure:

- **Demand**: separate end demand from channel orders, replenishment, pre-buying, backlog release, and price-driven nominal growth. Ask which customer, application, geography, and time horizon changed.
- **Supply**: distinguish announced/nameplate capacity from effective output. Account for construction, equipment, labor, feedstock, yield, qualification, maintenance, product mix, and bottlenecks. Utilization is output divided by sustainable capacity under normal operating constraints; it is industry-specific and need not approach 100% before scarcity appears.
- **Orders and backlog**: identify cancellation rights, delivery window, price protection, duplicates, and whether orders are binding. As a consistency check, use `ending backlog = beginning backlog + new orders net of cancellations - shipments or recognized sales`, with one definition throughout.
- **Inventory**: distinguish raw materials, work in process, finished goods, distributor stock, and customer stock. Use `ending inventory = beginning inventory + production or purchases - shipments or consumption +/- adjustments` only when the units and perimeter match.
- **Price and economics**: distinguish list, spot, contract, and realized prices; inspect rebates, mix, freight, quality, indexation, pass-through, and lag. Map where bargaining power and incremental profit accrue rather than assuming price increases benefit every participant.
- **Competitive position**: use a defined share denominator and basis of competition: qualification, performance, cost, switching friction, service, availability, ecosystem, regulation, or route to market.
- **Value chain**: trace goods, capacity, cash, and decision rights across direct and indirect inputs. Locate bottlenecks, substitution options, lead times, and the participant controlling allocation.

Use market size or growth only when it helps size a selected exposure, test a denominator, or explain a changed state. Never use a large TAM as a substitute for allocation evidence.

### 3. Read actors through words, actions, and constraints

Keep speech and behavior separate. For each important actor signal, ask:

1. What does it reveal about the actor's expectation or current condition?
2. How does the action itself change demand, supply, allocation, price, or timing?

For example, a capacity expansion may validate perceived demand in the short run while creating supply pressure later. Test the strength of the commitment: aspiration < stated plan < approved budget < permit or contract < equipment installed < qualified output. Check reversibility, funding, approvals, equipment and input availability, yield, qualification, and substitutability.

Cross-validate signals across roles and horizons:

- words versus subsequent behavior;
- customer demand versus supplier orders and channel inventory;
- competitor capacity plans versus equipment deliveries and permits;
- a common industry move versus actor-specific share gain, product mix, or distress;
- independent confirmation versus sources recycling the same datum;
- short-term validation versus long-term counter-effect.

Classify the relationship among observations as **support**, **partial support**, **conflict**, **not comparable**, or **single source**. Do not resolve genuine conflicts by choosing the most convenient source; explain the scope, timing, or incentive difference that may account for them.

### 4. Derive drivers, allocation, and target transmission

Organize selected drivers into demand, supply, pricing/profit pool, competitive allocation, policy, and technology/substitution. A driver is material only if its mechanism can be described.

Use this conceptual decomposition, not false precision:

`addressable opportunity = relevant end demand x product/content intensity x applicable segment`

`target-realizable business state = addressable opportunity x qualification eligibility x allocated share x deliverable capacity`

Each term can block transmission. Test allocation through procurement rules, installed base, qualification, technical fit, price/cost position, contractual priority, dual sourcing, geographic restrictions, capacity, channel access, customer concentration, and switching costs. For constrained supply, ask who receives scarce units and who captures price. For expanding supply, ask whose product is qualified and whose capacity becomes effective first.

Trace only causally supported chains. A useful chain states:

- the external state and marginal change;
- the actor evidence that supports or challenges it;
- the industry mechanism;
- the allocation mechanism;
- the target business interface and directional effect;
- horizon, necessary conditions, blockers, evidence basis, and confidence.

Use `POSITIVE`, `NEGATIVE`, or `MIXED` for direction; `NEAR_TERM`, `MEDIUM_TERM`, `LONG_TERM`, or `MULTI_HORIZON` for horizon; and `HIGH`, `MEDIUM`, or `LOW` for confidence. If the same action helps near-term orders but hurts long-term pricing, record both horizons rather than forcing one label.

### 5. Establish milestone and proof boundaries

Adapt the milestone ladder to the industry. A manufacturing or component path may be:

`R&D/design -> sample -> customer test -> validation -> certification -> approved supplier -> allocation/order -> ramp -> shipment -> stable economic production`

Software may move from pilot to contracted deployment, active seats, usage, renewal, and expansion. Biopharma may move through trial, readout, filing, approval, reimbursement, formulary access, and prescription adoption. Resources may require permit, financing, construction, commissioning, recovery/ramp, contracted offtake, and cash-cost proof.

For every milestone state: current stage, evidence completed, what that evidence proves, what remains unproved, next observable evidence, and failure or delay path. Certification is not an order; an order is not a shipment; a shipment is not repeat demand or profitable revenue. Treat scheduled dates as actor views until independently evidenced.

### 6. Form bidirectional candidate questions

Create a candidate only when facts expose tension: industry facts versus prevailing consensus, actor words versus actions, conflicting actors, short-term benefit versus long-term supply response, industry opportunity versus target allocation, or milestone progress versus economic realization.

For each question provide:

- the anchor or commonly assumed state;
- an upside interpretation and the evidence it would require;
- a downside interpretation and the evidence it would require;
- the target business state that would change;
- the next observable that can discriminate between paths;
- what remains unknown.

Phrase candidates as questions, not conclusions. Do not assert a market expectation, mispricing, activation condition, or valuation consequence that the evidence does not establish.

## Compact domain lenses

Apply only the lenses relevant to the selected external lines:

- **Capacity industries and semiconductors**: node/specification, yield, tool and material bottlenecks, qualification, wafer/start versus good-unit output, utilization by constrained step, capex lead time, and customer allocation.
- **Software, internet, and platforms**: contracted versus deployed usage, seats versus consumption, workload migration, retention and expansion, ecosystem/developer adoption, distribution control, unit-price changes, and compute or acquisition constraints.
- **Consumer, retail, and channels**: sell-in versus sell-through, promotions, channel inventory, shelf or traffic allocation, cohort/repeat behavior, category elasticity, mix, and retailer bargaining power.
- **Resources, energy, and chemicals**: grade/quality, regional basis, transport, inventories, outages, marginal cost, spare capacity, project lead time, operating rate, contract formulas, and policy or permitting constraints.
- **Healthcare and biopharma**: eligible population, diagnosis and access funnel, clinical proof, regulatory scope, reimbursement, formulary and physician adoption, manufacturing capacity, persistence, and competing modalities.

## Section-by-section output specification

### 1. Target-Relevant Industry and Value-Chain Fact Baseline

Present a compact table with: external theme; current state; recent change; comparison anchor; target exposure; evidence. Cover only decision-relevant demand, effective supply/capacity, price/economics, inventory/orders, allocation/competition, and commercialization states.

### 2. Key External Actors' Views and Behavioral Signals

Present: theme; actor and role; signal type; statement/action/datum; what changed; industry-common versus actor-specific; relationship to other evidence; citation. Explicitly separate actor views, committed actions, observed data, and C3 inference.

### 3. Core External Drivers, Allocation Mechanisms, and Transmission

Present: external driver; state/change; actor signals; industry mechanism; allocation mechanism; target business interface; direction; horizon; necessary conditions; blockers/boundaries; evidence basis; confidence. Evidence basis should identify formal industry data, customer, supplier, competitor, sell-side synthesis, independent multi-source validation, or C3 inference.

### 4. Industry and Commercialization Milestones and Proof Boundaries

Present: process or project; current stage; evidence completed; what is proved; what is not proved; next observable evidence; failure/delay path; timing confidence.

### 5. Potential Industry and Value-Chain Factor Gap Candidates

Present: candidate question; anchor; upside path and confirming evidence; downside path and confirming evidence; affected target business state; next observable; unknowns. Keep both sides live.

### 6. Unknowns, Evidence Boundaries, and Cross-Node Handoffs

List the material unknown, why it matters, current evidence limit, confidence impact, next evidence, and owning downstream node. Do not repeat generic risk language.

## Final quality gate

Before submitting, verify that:

- the report follows three to six target-linked external lines rather than a roster of companies;
- every state, signal, driver, allocation mechanism, transmission link, milestone, and inference is labeled correctly;
- numerical comparisons use compatible definitions and every material factual claim has nearby evidence;
- actor words are separated from behavior, and common industry effects from actor-specific effects;
- every claimed target benefit has an allocation mechanism and stops at a target business state;
- milestones state both proof and non-proof boundaries;
- candidate questions are bidirectional and are not formal downstream objects;
- conflicts and unknowns remain visible, with confidence calibrated to evidence;
- there are no peer comps, stock ideas, valuation conclusions, trading advice, or claims about what is priced in.
