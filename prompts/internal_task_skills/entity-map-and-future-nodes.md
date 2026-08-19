+++
kind = "internal_task_skill"
id = "entity-map-and-future-nodes"
name = "Entity Map and Future Nodes"
version = "2026.08.18"
manual_only = true
applicable_task_types = ["generate_global_research"]
+++

# C4 Entity Map and Future Nodes

## Purpose

Provide lightweight context for later research. Do not produce investment conclusions.

Use:
- `BUILD_OR_REFRESH_ENTITY_MAP`
- `SCAN_DIRECT_FUTURE_NODES`
- `ENRICH_FUTURE_NODES`

## A. Entity map

Build a simple map of the target's material business objects and clear external relationships.

Cover when relevant:
- major segments, products, or platforms;
- confirmed customers, suppliers, competitors, partners;
- clear regulatory, regional, input, infrastructure, or technology exposures.

A relation only needs to explain **who is related to whom, what the relationship is, why it exists, and which business/product it touches**.

Do not guess unnamed or rumor-only counterparties. Do not treat industry membership as a commercial relationship. Prefer official evidence; credible reporting may supplement it.

Output only:

| 关系主体 | 关系对象 | 关系类型 | 关系说明 | 关联业务或产品 |

Keep wording neutral. Do not add importance, research hints, financial impact, expected direction, Gap/Unit mapping, or trading conclusions.

## B. Future nodes

Find future matters that are concrete, time-locatable, and publicly observable.

A valid node should answer:

**who will do, announce, decide, launch, deliver, validate, deploy, or report what; and when.**

Use **no fixed research horizon**. Preserve the source's precision: date, month, quarter, half-year, year, or bounded window. Never invent a more precise date.

Search both:
- scheduled events such as earnings, investor events, conferences, regulatory or court decisions;
- roadmap/execution milestones such as launches, validation, qualification, production, shipments, capacity start, or customer deployment.

### Search requirement

For both Future Node modes, **actively use the available web/search tool to discover future matters**.

Existing artifacts, filings, connected data-source tools, entity maps, and supplied research are **seed context only**. They are not sufficient discovery by themselves.

A Future Node task is not complete until a search-discovery pass has been performed.

Use broad search to discover candidates, then prefer official or direct sources to verify useful candidates.

Do not restrict discovery to sources already present in the supplied context.

### `SCAN_DIRECT_FUTURE_NODES`

Before C1/C3, perform broad future-node discovery around:

1. the target company's announced events;
2. major product/platform roadmaps and execution milestones;
3. obvious first-order read-through nodes from **confirmed** entity-map relationships, such as key customer earnings/deployments, supplier capacity or product milestones, partner launches, or regulator decisions.

Do not crawl every mapped entity. External nodes should have a clear target-company business link without requiring deeper C1/C3 analysis.

The goal of pre-scan is to build a useful **future time map**, not merely an issuer IR calendar.

### `ENRICH_FUTURE_NODES`

After C1/C3, use their core drivers, key actors, milestones, and unresolved questions as additional search anchors.

Actively search for additional **specific future matters** that can observe or verify those research areas, then merge them with the pre-scan list.

Do not turn broad drivers, hypotheses, or monitoring topics into events.

## Future-node output

Output:

| 时间 | 未来事项 | 与目标公司的关系 | 来源 | 来源发布日期 |

Only explain the direct business/read-through link. Do not add importance scores, expected impact, research checklists, price reactions, priced-in judgments, or Unit/Gap bindings.

If an announced relevant matter has no supported time window, do not invent one; place it briefly under `待定未来事项`.

Record only source name and publication date. Prefer official/company/counterparty/authority sources for verification; credible secondary sources may be used when necessary.

## Output format

Return **structured output only**.

Do not generate a Markdown report or duplicate the structured result into narrative text.

If the runtime response envelope requires a `report_markdown` field, always return:

`"report_markdown": ""`

The structured `entity_relations` / `future_nodes` fields are the authoritative output.

## General rules

- No IDs, enums, status machines, reliability grades, or complex normalization.
- No fixed node count, entity quota, or time horizon.
- Do not drop useful long-dated nodes.
- Omit unsupported claims.
- Return only the artifact required by the current task mode.