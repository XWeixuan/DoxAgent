+++
kind = "internal_task_skill"
id = "known-events"
name = "Known Events"
version = "2026.07.07"
applicable_agents = ["O1"]
applicable_task_types = ["generate_known_events"]
workflow_nodes = ["GenerateKnownEvents"]
+++
# O1 Known Events

Build a 30-day known-fact index for W1 runtime novelty detection.

Do not summarize narratives. Do not write a research note. Do not rank catalysts. Convert stable Global Research, accepted expectation units, DoxAtlas narrative context, and recent known facts into atomic Known Events.

## Goal

Known Events must help W1 decide whether a future monitoring message is:

- old duplicate
- known-event recap
- material update
- new event

Default target: 15-40 events. For high-attention large-cap tickers, produce at least 20 events unless evidence is clearly insufficient.

## Coverage

Cover material facts from roughly the last 30 days:

1. Company facts:
   earnings, guidance, revenue metrics, margins, capex, product launches, customer wins, partnerships, management comments, filings, litigation, regulatory actions, buybacks, financing, layoffs, outages, security incidents, operational updates.

2. Market-discussed facts:
   facts already widely discussed by media, social, or DoxAtlas narratives. Include them even if they are old news, because W1 must not treat recaps as new events.

3. External facts that affect the ticker:
   peer moves, supplier/customer events, sector policy, macro or industry facts, and thematic chain events that can change the ticker's expectation units.

Do not include pure opinion, price-only movement, technical levels, generic sentiment, or unsourced speculation as Known Events.

## Event unit

Each Known Event must be one atomic factual unit.

Good:
- "Meta raised 2026 capex guidance to $125B-$145B."
- "Meta announced a plan to offer excess AI compute through a cloud business."

Bad:
- "Meta's AI cloud narrative strengthened and the stock rose sharply."
- "Investors are worried about AI capex ROI."
- "Bullish narrative N05 moved to first place."

If a fact and its market reaction are both useful, keep the fact in `core_fact`; mention price reaction only in `description` and set `has_price_reaction=true`.

## Field rules

`core_fact`:
- one concise factual sentence
- no narrative ranking
- no "shows / reflects / proves / marks a transformation" unless the source itself states it
- no price-only or technical signal as the core fact

`description`:
- may add short context
- must separate fact from interpretation
- keep it concise

`duplicate_detection_keys`:
- 4-8 compact keys
- include entity, event type, product/project, counterparty, metric, amount, date/window, status when available
- do not include full sentences
- do not include isolated numbers without labels
- do not use expectation_id as a main key
- do not use broad themes such as "AI", "growth", "risk" alone

`source_note`:
- optional compact human-readable source note
- do not emit source objects or internal Observation identifiers
- follow the independently loaded Evidence Citation Prompt for factual source attribution

`expectation_id`:
- fill only when the event clearly supports, weakens, updates, or recaps that expectation
- leave null if the event is relevant to the ticker but not tied to one unit

`discussed_by_market`:
- true if the event has already been discussed by media, social, DoxAtlas, or price commentary

`has_price_reaction`:
- true only if the input context includes a concrete price reaction

`is_known_old_news`:
- true for already-public facts, recaps, background catalysts, and previously discussed narrative facts

## Source discipline

Use only provided stable context and tool results.

DoxAtlas narrative ids, event ids, and narrative rankings are source clues, not Known Event facts. Do not place them in `core_fact`. Mention them only in `source_note` or rationale if needed.

If coverage is thin, still produce the best known-fact index and state the coverage gap in concise rationale or unknowns if the schema allows it.

Return only `KnownEventsDocument`.
