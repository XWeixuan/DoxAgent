---
name: message-bus-monitoring-terms-authoring
description: >
  Author and refine ticker-level Message Bus L1 search terms, L2 deterministic
  distribution rules, and Jev relevance definitions. Use when creating or
  revising TickerMonitoringTerms for by_search / by_distribution news monitoring.
---

# Goal

Produce compact monitoring terms that retrieve a ticker's relevant news environment without trying to predict which future events will matter.

Core model:

- **L1 finds the news pool.**
- **L2 removes obvious noise.**
- **Jev judges semantic transmission paths that keywords cannot express.**

Optimize relevance by **disambiguation**, not by enumerating every possible event.

# 1. Authoring mindset

Do not start by asking:

> What future events will matter to this stock?

Ask instead:

> What stable words and expressions are likely to appear in news that belongs to this ticker's information environment?

Future events are unknown. Monitoring rules should recognize durable language patterns, not encode an event ontology.

Prefer the **minimum sufficient expression**.

Examples:

```text
HBM
```

is preferable to:

```text
HBM AND (price|capacity|orders|yield|qualification|...)
```

when `HBM` itself is already sufficiently precise.

Likewise:

```text
Samsung AND (HBM|DRAM|NAND|memory)
```

is useful because the second condition disambiguates Samsung's unrelated businesses—not because only those Samsung events can affect the ticker.

# 2. Research before writing

For every supported language, inspect real financial, technology and industry news.

Do not translate English terms mechanically.

Determine how local journalists actually refer to:

- the company;
- core products and technologies;
- major competitors;
- industry concepts;
- relevant infrastructure or demand categories.

Treat `en`, `ko`, and `zh-Hant` as separate news corpora.

Examples of legitimate asymmetry:

```text
en: DRAM
ko: D램
zh-Hant: DRAM
```

If local media normally retain an English brand or acronym, retain it.

Because distribution evaluates the entry-language rules **plus English rules**, do not duplicate English terminology inside `ko` or `zh-Hant` unless the local corpus genuinely uses it as part of its normal writing.

# 3. L1 — search-page recall

`l1_concepts` contains **1–3 concepts**.

Each concept contains one search expression per required language.

Use L1 for high-value search anchors such as:

- company names;
- core product categories;
- highly specific industry concepts.

Prefer terms that can independently create a useful news pool.

Good:

```text
Micron
HBM
DRAM
```

Usually too broad:

```text
memory
AI
semiconductor
data center
```

Do not put event hypotheses into L1:

```text
HBM shortage
DRAM price increase
Micron production expansion
```

L1 is not an event classifier.

Each expression is one search expression. Do not embed `OR` or `|` to bypass the concept limit.

`by_search` renders concepts according to its own `search_policy` (`separate` or `or`).

Always inspect the generated `search_plans` with `terms validate`.

# 4. L2 — deterministic relevance filtering

`l2.<language>.groups` are ORed.

Inside one group:

- `any`: at least one must match;
- `all`: every item must match;
- `none`: none may match;
- at least one positive `any` or `all` condition is required.

## 4.1 Classify terms by information density

### Strong terms

Terms whose presence already strongly locates the article inside the ticker's business space.

Examples:

```text
Micron
HBM
DRAM
NAND
```

Allow these to match directly when appropriate.

Do not add event conditions merely to make the rule look more precise.

### Context-dependent terms

Terms that are relevant but have large unrelated semantic spaces.

Examples:

```text
Samsung
memory
AI
data center
```

Add the **smallest contextual condition needed to remove obvious noise**.

Examples:

```text
Samsung
AND
(HBM|DRAM|NAND|memory)
```

```text
AI
AND
(GPU|accelerator|AI server|data center|infrastructure|compute)
```

The second condition is for disambiguation, not event prediction.

### Weak generic terms

Avoid using generic commercial language as primary selectors:

```text
investment
revenue
cost
launch
agreement
forecast
production
demand
```

These occur in too many unrelated articles.

They may be useful only inside an already well-bounded group.

## 4.2 Upstream and downstream coverage

Do not attempt to enumerate the entire supply chain.

Avoid rules built around long lists such as every equipment vendor, material supplier, packaging company or customer.

Instead identify the language traces that relevant upstream/downstream reporting tends to contain.

Examples:

```text
memory AND shortage
memory AND bottleneck
memory AND capacity
HBM AND packaging
AI AND GPU
AI AND data center
AI AND capex
```

The goal is to recognize the **relationship described in the article**, not guess which named company will cause the next event.

## 4.3 Competitors

Major competitors are valid entity anchors because the competitive relationship is stable.

Keep the list selective.

Use:

```text
major_peer
AND
core_business_context
```

Do not include every company that participates somewhere in the industry.

Smaller-company events can still enter through core product or industry-language rules when they become genuinely relevant.

## 4.4 Regex discipline

Prefer short, readable expressions.

Use `literal` when exact matching is sufficient.

Use `regex` when handling:

- common spelling variants;
- whitespace variants;
- abbreviations;
- compact synonym sets.

Do not build giant synonym/event dictionaries.

Every extra alternative should have a clear reason:

1. it captures a real language variant seen in reporting; or
2. it fixes a recurring false positive / false negative.

Avoid catastrophic backtracking. A single regex has an approximately 20 ms matching budget.

Text is Unicode-NFC and whitespace normalized.

Do not rely on English `\b` behavior for Korean or Traditional Chinese.

`field` may be:

```text
all
title
summary
body
```

Default is `all`.

`case_sensitive` defaults to `false`.

`whole_word` applies only to literals.

`none` should be used sparingly for stable, recurring ambiguity—not as a large blacklist.

# 5. Jev definition — semantic relevance

`definition` contains exactly:

```yaml
definition:
  relevant: ...
  irrelevant: ...
```

There are not separate language-specific definitions.

Jev is not a natural-language rewrite of L2.

Do not give Jev another keyword catalogue.

Its role is to judge whether the article describes information that can plausibly enter the ticker's economic or competitive information environment.

## Relevant

Define:

1. direct company information; and
2. plausible external transmission paths.

A useful pattern is:

> Relevant if the news directly concerns the company, or if an external development could plausibly change expectations for its core product supply, demand, pricing, cost, availability, competitive position, market share, customer demand, or technology requirements.

Adapt the economic exposures to the ticker.

Do **not** require the article to prove a material stock-price impact.

Monitoring occurs before the full consequence of an event is known.

Prefer:

```text
could plausibly change
may alter expectations
provides new information about
```

over:

```text
has a demonstrated material impact
```

## Irrelevant

Describe the closest confusing neighboring content, not obviously unrelated topics.

Examples:

- consumer product reviews;
- generic AI software/model news without hardware-demand implications;
- unrelated businesses of a diversified competitor;
- broad semiconductor news with no connection to the ticker's business exposure;
- stock-price commentary without a new underlying event;
- incidental mentions of a product or technology.

The irrelevant definition should help Jev distinguish **near misses**.

# 6. YAML contract

```yaml
ticker: MU
expected_revision: 0

l1_concepts:
  - concept_id: company
    expressions:
      en: Micron
      ko: 마이크론
      zh-Hant: 美光

l2:
  en:
    groups:
      - id: company
        any:
          - {literal: Micron, whole_word: true}

  ko:
    groups:
      - id: company
        any:
          - {literal: 마이크론}

  zh-Hant:
    groups:
      - id: company
        any:
          - {literal: 美光}

definition:
  relevant: >-
    ...
  irrelevant: >-
    ...
```

Currently required languages are:

```text
en
ko
zh-Hant
```

Use the language set returned by `terms validate` if the implementation later changes.

L2 has no three-concept limit, but the complete serialized configuration must remain below 64 KiB.

L2 applies only to `by_distribution`; it does not modify L1 search recall.

# 7. Iteration workflow

Treat monitoring-term development like Meltwater / media-monitoring query tuning.

Start small.

Then test real articles and classify bad cases:

```text
false positive
false negative
correct match
correct rejection
```

For each false positive, ask:

> What is the smallest extra context needed to disambiguate it?

For each false negative, ask:

> What stable language signal did this relevant article contain that the rules missed?

Change the rule only when the answer produces a reusable pattern.

Do not redesign the ontology around one unusual article.

Prefer empirical bad-case iteration over theoretical completeness.

# 8. Validation and submission

Validate:

```bash
python -m doxagent.message_bus_v2.cli terms validate --file monitoring/MU.yaml
```

Apply:

```bash
python -m doxagent.message_bus_v2.cli terms apply --file monitoring/MU.yaml --actor user
```

Inspect current configuration/history:

```bash
python -m doxagent.message_bus_v2.cli terms show --ticker MU
python -m doxagent.message_bus_v2.cli terms history --ticker MU
python -m doxagent.message_bus_v2.cli terms preview --ticker MU
```

Test an article:

```bash
python -m doxagent.message_bus_v2.cli terms test \
  --ticker MU \
  --source ctee_semiconductor \
  --article sample-article.json
```

`terms test` evaluates L2 only; it does not invoke Jev.

`expected_revision` is optimistic concurrency control:

- first revision: `0`;
- later updates: use the current revision.

`validate` does not persist changes.

`apply` creates the new immutable revision.

Do not modify SQLite directly and do not maintain independent copies of monitoring terms per source.

# 9. Jev runtime boundary

Jev is a supplemental path, not the only distribution mechanism.

When enabled, its state contains only:

```text
title
summary
body
source
language
```

It does not receive:

```text
URL
HTML
publication time
L1 rules
L2 rules
other articles
historical messages
```

Each subscribed ticker receives an independent relevance question.

Rules that already match continue to distribute normally.

When rules do not match, Jev can supplement distribution when its score reaches the configured threshold (currently `>= 0.5`).

Jev failure, timeout or disablement must not block deterministic L2 matches.

# 10. Final authoring checklist

Before accepting a configuration, verify:

- L1 contains only 1–3 strong search anchors.
- Local-language terms come from real local reporting, not literal translation.
- Strong product/company terms are not unnecessarily constrained.
- Broad terms use only the minimum context required for disambiguation.
- Competitor lists contain major competitors, not the whole industry.
- Upstream/downstream logic recognizes language relationships rather than enumerating companies.
- Generic business words are not acting as primary selectors.
- Regexes are short and readable.
- `none` is used only for recurring ambiguity.
- Jev describes causal/business relevance rather than keywords.
- Jev `irrelevant` describes near misses.
- Every added term has a concrete retrieval or bad-case reason.
- `terms validate` and representative article tests pass.

## Guiding rule

> **Do not predict which future events will matter. Identify the stable language signals that relevant news is likely to contain. Strong terms may match directly; broad terms receive only the minimum context required to disambiguate them. Let L1 find the pool, L2 remove noise, and Jev reason about semantic transmission.**