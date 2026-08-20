+++
kind = "internal_task_skill"
id = "entity-map-and-future-nodes"
name = "Entity Map and Future Nodes"
version = "2026.08.20"
manual_only = true
applicable_task_types = ["generate_global_research"]
+++

# C4 Entity Map and Future Nodes

## Purpose

Provide lightweight research context for later agents.

This task is for:
- building a basic entity relationship map;
- discovering future observable nodes.

Do not produce:
- investment conclusions;
- expectation judgments;
- Unit / Gap analysis;
- financial impact analysis;
- trading conclusions.

Use:
- `BUILD_OR_REFRESH_ENTITY_MAP`
- `SCAN_DIRECT_FUTURE_NODES`
- `ENRICH_FUTURE_NODES`

---

# A. Entity Map

Build a lightweight map of the target company's important business relationships.

Include when relevant:
- major business segments;
- important products or platforms;
- customers;
- suppliers;
- competitors;
- partners;
- clear regulatory, regional, technology, infrastructure, or input exposures.

A relationship only needs to explain:

- who is connected;
- what the relationship is;
- why the relationship exists;
- which business or product it relates to.

Do not:
- guess unnamed counterparties;
- add rumor-only relationships;
- infer financial impact;
- judge importance;
- provide research recommendations.

Output:

| 关系主体 | 关系对象 | 关系类型 | 关系说明 | 关联业务或产品 |

Keep descriptions neutral and factual.

---

# B. Future Nodes

Find future matters that are specific, observable, and time-related.

A valid future node should answer:

**Who will do, announce, decide, launch, deliver, validate, deploy, or report what, and when?**

Do not set a fixed time horizon.

Include both:
- near-term scheduled events;
- long-term announced roadmap or execution milestones.

Examples:
- earnings releases;
- investor events;
- product launches;
- product availability;
- production or shipment milestones;
- customer deployment;
- validation or qualification;
- capacity expansion;
- regulatory decisions;
- court decisions;
- partner or supplier milestones.

Do not turn broad themes into events.

Examples that are NOT future nodes:
- AI demand will continue growing;
- supply may remain tight;
- a company may gain customers;
- technology will continue improving.

---

# Search and Discovery Rules

Future Node tasks are discovery tasks.

The primary discovery method is the available web search capability.

Existing:
- artifacts;
- filings;
- connected data sources;
- previous outputs;
- entity maps;

are only starting context.

Do not rely on them as the only discovery source.

For Future Node tasks:

1. Search broadly first to discover candidate future matters.
2. Then use available sources to confirm the useful candidates.
3. Continue searching when obvious discovery areas have not been covered.

Do not spend excessive time auditing already sufficient information.

The goal is to build a useful future time map, not an exhaustive evidence archive.

Do not:
- repeatedly re-check the same information;
- search only within existing filings;
- prioritize audit completeness over finding useful nodes.

---

# SCAN_DIRECT_FUTURE_NODES

Before C1/C3 research:

Search across:

1. Target company:
- upcoming events;
- product roadmap;
- launches;
- availability;
- production;
- shipment;
- deployment;
- conferences;
- announcements.

2. Major products or platforms:
- roadmap milestones;
- next-generation products;
- release windows;
- customer availability.

3. Clear first-order relationships from the entity map:
- major customers;
- major suppliers;
- key partners;
- important regulators.

Only include external nodes when the relationship to the target is already clear.

The goal is to create a broad initial future timeline, not only an issuer calendar.

---

# ENRICH_FUTURE_NODES

After C1/C3 research:

Use:
- core business drivers;
- important external actors;
- industry milestones;
- unresolved research questions;

as additional search directions.

First perform a broad discovery pass to find missing future nodes.

Then add research-specific nodes that can help observe or validate those research areas.

Do not:
- only search existing C1/C3 conclusions;
- turn every driver into an event;
- create hypothetical future events.

---

# Source Handling

Sources are used to support the existence of a future node.

Record:
- source name;
- publication date.

Prefer useful sources, including:
- company sources;
- partner sources;
- event organizers;
- regulators;
- credible media.

Do not:
- create source reliability scoring;
- perform unnecessary source auditing;
- require multiple-source confirmation for every node.

If a future matter is clearly announced but has no supported time window:

put it under:

`待定未来事项`

Do not invent dates.

---

# Future Node Output

Output only:

| 时间 | 未来事项 | 与目标公司的关系 | 来源 | 来源发布日期 |

Do not include:
- importance scores;
- expected impact;
- bullish/bearish judgment;
- research checklist;
- price reaction;
- priced-in judgment;
- Unit mapping;
- Gap mapping.

---

# Output Format

Return structured output only.

Do not generate Markdown reports.

If the runtime requires a `report_markdown` field:

return:

`"report_markdown": ""`

The structured fields are the only authoritative output.

---

# General Rules

- No IDs.
- No enums.
- No status machines.
- No reliability grades.
- No fixed node count.
- No fixed time horizon.
- Do not drop useful long-dated milestones.
- Do not invent unsupported events or dates.
- Optimize for useful discovery, not audit completeness.