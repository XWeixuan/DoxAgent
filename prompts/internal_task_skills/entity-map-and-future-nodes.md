+++
kind = "internal_task_skill"
id = "entity-map-and-future-nodes"
name = "Entity Map and Future Nodes"
version = "2026.08.05"
manual_only = true
applicable_task_types = ["generate_global_research"]
+++
# C4 Entity Map and Future Nodes

## Mission and boundary

Build infrastructure context around Document 1, not a research conclusion. Maintain:

1. a low-frequency **entity-exposure map** of material internal business objects and explainable external interfaces;
2. a high-frequency **future-node list** of matters with an identifiable subject, action/result, supported time window, and public observable.

Use one external C4 agent in exactly three task modes:

- `BUILD_OR_REFRESH_ENTITY_MAP`
- `SCAN_DIRECT_FUTURE_NODES`
- `ENRICH_FUTURE_NODES`

Build/refresh the map first; scan direct company nodes before C1/C3; then use C1/C3, optional dated C2 evidence, and the pre-scan to find and deduplicate research-linked matters.

Do not turn the map into a knowledge graph, financial model, or priority list, or nodes into outcome forecasts. Never produce valuation, target price, trading advice, price reaction, direction, priced-in state, importance score, formal `ExpectationUnit`, `RealizationFactor`, `PotentialGap`, activation, or Unit/Gap binding. Document 2 owns mapping; the event store owns occurred facts.

## Execution discipline

Read `task_mode`, target, `current_date`, timezone, horizon, supplied artifacts, and output format. Default to 12 months only when no horizon is supplied. Resolve "future" against the runtime clock. Label issuer fiscal periods explicitly.

Within 0-90 days, seek reasonably complete direct, evidenced nodes. From day 91 through month 12, admit only a formal date/month/quarter, legal-contractual deadline, or explicit bounded window; vague long-range plans stay out.

Treat each mode as non-blocking:

- return a valid empty list when no reliable node exists;
- omit an unverified relation, or retain it as `UNCERTAIN` only when useful;
- expose missing inputs, stale evidence, conflicts, and exclusions;
- never fill a quota by guessing.

Use supplied artifacts first. Refresh only affected relations unless restructuring invalidates the boundary. Use C1/C3 to narrow enrichment searches, not import conclusions.

## Evidence model

### Separate claim types

Keep four claim types distinct:

- **direct fact**: an issuer, authority, contract party, or official organizer states the relation/schedule;
- **reported fact**: credible named secondary reporting identifies its basis;
- **C4 inference**: a bounded classification or entity-resolution judgment supported by facts;
- **unknown**: identity, status, timing, or observability cannot be verified.

A source proves only what it says. Risk-factor language can prove exposure, not a named counterparty. Syndication is one evidence chain, not corroboration.

### Source order

Search in this order:

1. issuer filings, IR pages, releases, calendars, exhibits, and official product/trial records;
2. regulator, court, exchange, government, procurement, standards, and organizer records;
3. named counterparties discussing the same interface;
4. reputable financial/specialist media with attributable sourcing;
5. analyst synthesis, anonymous reports, social media, and unattributed reposts.

For US issuers, establish the boundary from 10-K/20-F business, segment, concentration, geography, risk, exhibit, and notes; update with 10-Q, 8-K/6-K, proxy, and releases. Use equivalents elsewhere. Filing date is not occurrence date.

For every node record source name/type, title/matter, publication date, stable location, and minimal support for **subject, action, and time**.

Classify source reliability, not outcome probability:

- `HIGH`: direct issuer/authority/court/exchange/organizer schedule or deadline;
- `MEDIUM`: attributable credible secondary or counterparty evidence;
- `LOW`: anonymous, predictive, social, single-rumor, or untraceable evidence.

LOW evidence cannot support a pre-scan node alone. Retain it post-research only as `TENTATIVE` when matter, window, business link, and observable are specific.

## Mode A: build or refresh the entity-exposure map

### 1. Set the business-object boundary

Decompose only far enough to expose a useful interface:

`target company -> material reportable segment/business line -> major product, service, platform, program, or necessary subsidiary/brand`

Prefer current operating/reporting structure. Omit immaterial entities, SKUs, old products, and geographies; keep an object needed to disambiguate an external relation.

Then consider external objects:

- named customers, suppliers, competitors, partners, and channels;
- regulators/policy bodies with a direct interface;
- material regions, inputs, infrastructure, licenses, platforms, or technology routes.

An unnamed concentration does not identify a customer. Do not confuse distributor with end customer, user with purchaser, contract manufacturer with supplier of record, or group with contracting subsidiary.

### 2. Admit an entity relation only through five gates

Require all of the following:

1. both endpoints are identifiable at the resolution supported by evidence;
2. the relationship type is clear and directional;
3. at least one `related_business_object` names the segment/product/service/interface involved;
4. one or two neutral sentences explain why the edge exists;
5. the relation is current or durably relevant, rather than an isolated story or remote third-/fourth-order connection.

Industry membership does not prove competition; a teardown, job post, specification, or rumor does not prove a commercial relation. Do not infer size, margin, timing, beneficiary, or importance.

Use only these relation types:

- `BUSINESS_COMPOSITION`: segment/product/subsidiary structure;
- `CUSTOMER`: external purchaser or user;
- `SUPPLIER`: provider of equipment, material, product, or service;
- `COMPETITOR`: contest over product, customer, order, capacity, or technology;
- `PARTNER`: public collaboration, development, distribution, or coordination;
- `DEPENDENCY`: reliance on infrastructure, license, platform, standard, or capability;
- `REGULATORY`: approval, restriction, support, or oversight;
- `REGIONAL_EXPOSURE`: revenue, capacity, customer, or supply-chain regional interface;
- `COMMODITY_INPUT`: commodity, energy, or raw-material interface;
- `TECHNOLOGY_RELATION`: technology support, dependency, substitution, interoperability, or constraint;
- `OTHER`: necessary relation that cannot be represented above.

Set `relation_status` consistently:

- `ESTABLISHED`: current direct or consistent primary evidence supports edge and interface;
- `REPORTED`: credible attributable reporting supports it without full direct confirmation;
- `UNCERTAIN`: evidence conflicts, is stale, or leaves endpoint/role ambiguity. Retain sparingly and state the exact uncertainty.

### 3. Resolve entities without false precision

Assign stable local IDs and canonical names; merge aliases only for the same actor. Use category objects only for category-level exposure, never as companies.

Use the caller's entity-type enum; otherwise label company, business line, product/service, subsidiary/brand, regulator, region, input, technology, infrastructure, or other object. Customer/supplier/competitor is an edge role, so one company may carry several edges.

Orient internal edges parent -> child and exposure edges target/internal object -> external object: a `CUSTOMER` target is the source's customer. Separate roles/interfaces. Require `basis_refs` for non-obvious, changing, `REPORTED`, or `UNCERTAIN` edges.

Refresh locally for M&A/divestitures, reorganizations, product-route changes, confirmed counterparties, exits, or structural regulatory/region changes. Return added/modified/removed items and stale edges; search incompleteness alone does not justify deletion.

## Modes B1/B2: construct future nodes

### 1. Apply the event grammar

Admit a node only if it answers:

`who -> will do/decide/receive what -> when -> what public artifact or datum will confirm it`

The observable is evidence, not a predicted outcome: release, filing, vote, order, decision, product/trial update, contract notice, qualification/production disclosure, or measured industry release.

Reject trends, unscheduled recurring activity, monitoring topics, unbounded possibilities, and abstractions such as "when supply balances." A deadline is a procedural node, not proof of a decision. An earnings date is a node; surprise direction is not.

Use only these `node_type` values: `EARNINGS_RELEASE`, `INVESTOR_EVENT`, `PRODUCT_RELEASE`, `CUSTOMER_CONTRACT`, `VALIDATION_QUALIFICATION`, `REGULATORY_DECISION`, `COURT_DECISION`, `POLICY_FUNDING`, `CAPACITY_START`, `PRODUCTION_RAMP`, or `OTHER`.

### 2. Respect milestone economics without predicting economics

Know the operating sequence so distinct nodes are not merged and weak milestones are not overstated:

- manufacturing: construction -> tool install -> qualification -> production start -> yield/utilization ramp -> effective output;
- semiconductors/hardware: sample -> validation/qualification -> design win -> order -> shipment;
- biopharma: enrollment -> primary completion -> data readout -> filing -> acceptance -> advisory review -> regulatory action;
- software/platforms: announcement -> availability -> customer deployment -> usage/renewal disclosure;
- M&A: signing -> filings/review -> shareholder vote -> clearance -> close;
- policy: proposal -> comment deadline -> final action -> effective date -> funding award.

One step does not prove the next. Include only separately supported windows; never derive a downstream date from normal lead time.

### 3. Execute the two scans differently

For `SCAN_DIRECT_FUTURE_NODES`, search only target/internal objects: next earnings, investor event, announced product, disclosed contract/closing, direct deadline, or explicit qualification/production/capacity window. Keep 3-10 normally; do not crawl every mapped entity.

For `ENRICH_FUTURE_NODES`, turn C1/C3 drivers, objects, actors, allocation mechanisms, milestones, blockers, questions, and Unknowns into search anchors. Find **specific scheduled matters** that could observe them. Keep target links neutral; never copy a hypothesis into `action_or_result`.

### 4. Normalize time correctly

Separate four clocks:

- occurrence/action date;
- public announcement or filing date;
- source publication date;
- observation/result-availability date.

Match the clock to `action_or_result`; put other clocks in `time_basis`/`notes`. Never sharpen source precision. Preserve half-year, month, quarter, or range. Distinguish fiscal from calendar quarter. Resolve relative phrases from publication date only when unambiguous.

Set `time_precision` to `DATE`, `WEEK`, `MONTH`, `QUARTER`, or `RANGE`. Set status as follows:

- `SCHEDULED`: authoritative exact date or fixed formal schedule;
- `ANNOUNCED_WINDOW`: authoritative bounded window but no exact date;
- `TENTATIVE`: provisional estimate, credible indirect schedule, or unresolved source conflict;
- `DELAYED`: a later source formally moves the window;
- `CANCELLED`: a later source formally cancels it;
- `COMPLETED`: use only while reconciling an existing node for handoff; do not retain it in the active future list.

For conflicting dates, prefer the latest direct update; otherwise use the smallest credible range, set `TENTATIVE`, and explain. Estimated clinical dates, targets, review goals, and court calendars can move.

Do not label a recurring-cadence estimate or calendar aggregator date `SCHEDULED`. Use `TENTATIVE` with its basis or exclude it until the issuer/organizer confirms a window.

### 5. Deduplicate by event identity

Merge only co-referential subject, action/result, object, window, and observable. Combine sources and keep the best-supported precision. Do not merge product release, qualification, production, or earnings steps merely because actor/product match.

## Output contracts

Return lightweight Markdown plus YAML/JSON when requested, not a narrative report.

`entity_exposure_map` must contain `as_of`, `target`, `entities`, `relations`, and for refreshes `change_summary`. Each entity contains:

`entity_id`, `entity_name`, `entity_type`, `aliases`, `brief_description`.

Each relation contains:

`source_entity_id`, `target_entity_id`, `relation_type`, `related_business_objects`, `relation_description`, `relation_status`, `basis_refs`.

`future_nodes_pre_scan` contains accepted pre-scan nodes. `future_nodes_final` updates that list, adds accepted post-research nodes, and deduplicates. Each node contains:

`node_id`, `subject_entity`, `node_type`, `action_or_result`, `related_business_objects`, `relationship_to_target`, `time_window`, `time_precision`, `time_basis`, `observable_output`, `scan_stage`, `status`, `source_refs`, `source_reliability`, `reliability_reason`, `notes`.

Use `PRE_SCAN` for B1 additions and `POST_RESEARCH` for B2 additions. If the caller contract permits `PRE_SCAN_ENRICHED`, use it only for a B1 node materially updated in B2. Keep rejected enrichment candidates outside the official list with a short exclusion reason when the mode requests them.

Keep local IDs stable. `source_refs` exposes source, publication date, and location. `reliability_reason` is 1-2 sentences. `notes` holds conflicts, conditions, fiscal clarification, or exclusions - never impact.

Never add `expected_impact`, `bullish_or_bearish`, `importance_score`, `related_gap`, `related_unit`, `priced_in_state`, `expected_price_move`, or `research_hint`.

Soft controls: 3-12 internal objects, 0-10 customers, 0-10 suppliers, 2-10 competitors, 3-12 environment exposures; 3-10 pre-scan and 5-20 final nodes. Prune weak, indirect, stale, unreliable, vague, remote, and duplicate items first.

## Final quality gate

Before returning, verify:

1. output matches the requested mode/artifact;
2. every relation has identifiable endpoints, direction, business interface, and neutral wording;
3. no unnamed counterparty or mere peer became a relation;
4. every active node remains future and has subject, action/result, supported window, and observable;
5. precision, fiscal/calendar label, status, and reliability match evidence;
6. each milestone claims only what its step proves;
7. pre-scan is direct/light and enrichment follows C1/C3 anchors;
8. duplicates merge while distinct stages remain separate;
9. empty results and Unknowns replace speculation;
10. no forbidden impact, recommendation, priced-in, Gap/Unit, or priority field appears.
