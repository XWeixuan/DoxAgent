# Canonical Foundation

Apply these semantics throughout initialization and incremental maintenance.

## Canonical model

### Event Occurrence

An Event Occurrence is one identifiable action, disclosure, decision, result, incident, milestone, external shock, or material market episode anchored to when it occurred or became public.

Determine its identity from four linked dimensions: occurrence or release time, actor and concrete action, object or matter stage, and disclosure context. One release may contain multiple Facts with different subject periods. A later communication, update, reaffirmation, denial, or milestone can be a new occurrence even when it refers to earlier content.

Package and Atomic boundaries organize evidence. Merge across Packages when the occurrence is the same and split a Package when it contains distinct occurrences. Shared ticker, topic, day, event type, subject period, or wording alone does not establish identity.

### Analyst response episodes and ongoing matters

Analyst actions may form one bounded response episode when they concern the same ticker and explicit catalyst, show homogeneous responses within a limited information-digestion window, and each action has low independent research value. Preserve each institution's action date, old and new rating or target, direction, and rationale as separate Facts. A new catalyst, information cycle, thesis, report, or factual trigger starts a new Event.

Represent an ongoing transaction, litigation, regulatory, product, or capacity matter through independently meaningful milestone Events connected by relationships rather than one expanding record.

### Canonical Fact

A Fact is one minimal, independently useful proposition that its Event produced, disclosed, confirmed, corrected, or materially changed. Preserve the actor, action or metric, material figures, direction, conditions, horizon, assertion state, and subject period that distinguish it. Consolidate semantic duplicates and retain complementary Facts.

## Identity and Delta resolution

Compare occurrence identity before topic similarity:

- same occurrence and same proposition: `DUPLICATE_FACT` against a stable Event and Fact;
- same occurrence and complementary proposition: add a Fact to the existing Event;
- same occurrence and corrected proposition: retain the stable Fact identity and revise it;
- new action, disclosure, matter stage, or occurrence outside the current episode or theme-cluster boundary: create a new Event; a different Fact date alone does not split a valid cluster;
- plausibly relevant material whose occurrence remains unresolved: `KEEP_PENDING`;
- extraction failure or content without a usable proposition: `DROP_INVALID`.

For same-batch duplicates supporting a new Fact, consume all supporting Delta IDs in that Fact. Use the formal wire field `resolution` for residual Delta. Stable Event and Fact IDs follow semantic identity rather than wording.

## Canonical fields

### Event type

Use the first-version vocabulary in uppercase snake case:

```text
EARNINGS_RELEASE
GUIDANCE_UPDATE
ANALYST_ACTION
ANALYST_RESPONSE_EPISODE
INVESTOR_EVENT
PRODUCT_ANNOUNCEMENT
PRODUCT_MILESTONE
CAPACITY_OR_CAPEX
SUPPLY_DEMAND_UPDATE
MATERIAL_CONTRACT
FINANCING
CAPITAL_RETURN
M_AND_A
REGULATORY_ACTION
LITIGATION_MILESTONE
MANAGEMENT_CHANGE
MARKET_EPISODE
OTHER_CORPORATE_EVENT
```

Reuse an existing type for the same meaning. Use `OTHER_CORPORATE_EVENT` when no listed type fits; vocabulary expansion is a contract change rather than a Turn-level choice.

### Occurrence and subject time

Keep three time questions separate:

- Event time answers “when did this Event or theme cluster occur?” `occurred_at` is the action's actual time or the time the information first became public.
- Fact occurrence time answers “when was this specific statement, rating, forecast, or disclosure made?” `fact_occurred_at` is when that Fact was stated, disclosed, confirmed, or formed.
- Subject time answers “which period is this Fact about?” `subject_time` is the reporting, forecast, plan, or scheduled target period discussed by the Fact.

| `occurrence_time_precision` | `occurred_at` form |
| --- | --- |
| `TIMESTAMP` | timezone-aware ISO-8601 |
| `DAY` | `YYYY-MM-DD` |
| `MONTH` | `YYYY-MM` |
| `QUARTER` | `YYYY-Q1..Q4` |
| `YEAR` | `YYYY` |
| `INTERVAL` | `<canonical-start>..<canonical-end>` |
| `UNKNOWN` | `UNKNOWN` |

Resolve occurrence dates from explicit proposition or evidence dates, official release dates, Runtime-confirmed occurrence dates, source `published_at`, then focused Web Search within the Frozen `as_of`. A source publication date is a candidate rather than a substitute for an explicit historical action date.

Earnings releases, filings, announcements, analyst actions, transaction milestones, and other traceable date-specific public occurrences resolve to `DAY`. A genuinely period-wide Event may use broader precision and is recorded as `GENUINELY_PERIOD_WIDE`; a date-specific occurrence that remains conflicting or unresolved stays Pending rather than escaping into YEAR or another broad value. Event and Fact occurrence dates cannot be later than the Frozen `as_of`.

Every Fact written in a maintenance-v3 Event revision records `fact_occurred_at` and `fact_occurrence_time_precision: DAY`. `SAME` is valid only when the parent Event is `DAY` and the Fact occurred on that same day. A MONTH-, QUARTER-, YEAR-, INTERVAL-, or UNKNOWN-level Event requires an explicit `YYYY-MM-DD` for each Fact. Preserve those Fact dates when aggregating an analyst response episode or another valid theme cluster; different Fact dates do not by themselves require separate Events.

```text
Wrong:   an analyst forecasts FY2027 revenue growth on 2026-08-13
         Event occurred_at = 2027

Correct: Event occurred_at = 2026-08-13
         Fact fact_occurred_at = SAME
         Fact subject_time = FY2027

Theme cluster:
         Event occurred_at = 2026-08 [MONTH]
         Fact A fact_occurred_at = 2026-08-05
         Fact B fact_occurred_at = 2026-08-13
         Fact C fact_occurred_at = 2026-08-19
```

When a company announces on one day that earnings will be released on a future date, the announcement day is the Event and Fact occurrence time; the future release date is the scheduled subject time.

### Assertion state and subject time

Set `assertion_state` from the proposition's business state:

- `ACTUAL`: occurred or formally disclosed;
- `GUIDANCE`: formal issuer or management operating or financial guidance;
- `FORECAST`: analyst, third-party, or model forecast;
- `PLAN`: stated but incomplete plan;
- `RUMOR`: unconfirmed report or market rumor;
- `DENIAL`: explicit denial;
- `SCHEDULED`: formally scheduled future occurrence;
- `ONGOING`: continuing state or process;
- `EXPECTED`: supported expectation that is neither formal guidance nor a defined forecast;
- `HYPOTHETICAL`: conditional scenario;
- `UNKNOWN`: state cannot be determined reliably.

`PLANNED`, `RUMORED`, and `DENIED` remain compatibility values. New or substantively revised Facts use `PLAN`, `RUMOR`, and `DENIAL`. Article tone does not replace the proposition's business state.

Use `subject_time: SAME` when the Fact shares its Event time and has no distinct subject period. Use `null` when subject time does not apply. Express financial periods in stable forms such as `FY2026-Q3`; dates and intervals reuse Canonical time forms. If an explicit subject time equals `occurred_at`, use `SAME`. Use `CURRENT`, `FUTURE`, `NEAR_TERM`, or `LONG_TERM` only when that imprecision is part of the proposition and no more precise period is available.

### Titles and summaries

Make the title identify the actor, action, and distinguishing object. Write `canonical_summary` in one or two sentences stating what occurred and its principal business result without repeating every Fact. Write `known_event_summary` for W1 recognition, preserving the date, actor, action, stage, material figures, terms, horizon, institutions, and old-to-new distinctions that separate nearby Events. It may match the title only when a simple Event has no additional distinguishing information.

### Importance and Reference use

Judge `is_important` from durable relevance to expectations, price analysis, or long-term understanding, including results and guidance, capital allocation, material contracts, products or capacity, financing, M&A, regulation, litigation, management, supply-demand change, and comparable consequential occurrences.

Keep three decisions distinct. Full-Library admission asks whether the occurrence is valid and has a direct ticker relationship or a concrete, credible, non-trivial `Event → exposure → target consequence` path. `is_important` asks whether it has durable research significance. `include_in_reference_view` asks whether it still belongs in the compact projection of current reality at the Frozen `as_of`. The Reference View is neither an important-news ranking nor a recent-N-day feed, and exclusion from it does not remove Library history.

Use this complete decision question:

> At the Frozen `as_of`, if this Event were omitted, could D2 form a materially incomplete view of the current expectation, reality baseline, or future revision space, or could O3 hold a stale view of a Policy's current starting point, satisfied conditions, or remaining boundary? If yes, set `include_in_reference_view=true`.

Apply the test in order:

1. **Target path:** establish the direct target relationship or the concrete indirect economic transmission.
2. **Current-state effect:** determine whether the Event establishes, changes, or constrains the current baseline, an open question, an effective forward commitment, a controlling update, or an unabsorbed change.
3. **Information state:** distinguish latest, evolving, unresolved, or still-effective information from information that has been absorbed, superseded, completed without remaining state effect, or made redundant.
4. **Time state:** test whether an old Event still defines reality and whether a recent Event is merely routine noise; age alone decides neither inclusion nor exclusion.
5. **Omission test:** ask whether removing it could leave downstream understanding of where reality stands incomplete or stale.
6. **Redundancy test:** ask whether a later, fuller, or more controlling Event already expresses the same current state.

Choose the supplied `reference_view_basis` that states the decisive information condition. Inclusion bases are `CURRENT_BASELINE`, `OPEN_OR_EVOLVING_MATTER`, `LATEST_CONTROLLING_UPDATE`, `STILL_EFFECTIVE_FORWARD_ITEM`, and `RECENT_UNABSORBED_UPDATE`. Exclusion bases are `SUPERSEDED`, `COMPLETED_AND_ABSORBED`, `SUBJECT_HORIZON_PASSED`, `ROUTINE_OR_REDUNDANT`, and `NO_CURRENT_DOWNSTREAM_UTILITY`.

All four importance/Reference combinations are valid without being quotas: a current major result may be true/true; a historically important result fully replaced by a later quarter may be true/false; a limited recent analyst episode not yet absorbed may be false/true; an obsolete routine target-price snapshot may be false/false. Retain an old but effective capacity commitment or unresolved lawsuit; exclude a recent but redundant target-price action. A future earnings-date announcement may remain until the release occurs, after which the actual earnings Event usually supersedes the schedule. Review age triggers a fresh semantic judgment rather than automatic expiration.

### Decision ledgers

Use the supplied Date Resolution Ledger to preserve date candidates, the selected date and precision, its Event-occurrence, Fact-occurrence, or subject-time role, and resolution status. Use the Reference View Decision Ledger for the independent importance and Reference decisions, the matching basis, concise note, review reason, and Frozen `as_of`. Keep these decision fields in the supplied ledger and review artifacts rather than adding them to Canonical Event or Fact objects.

These ledgers are O2 decision audit records. Deterministic import preparation may normalize their wire representation or omit an unreadable sidecar row, but it does not use them to override or remove O2 Event/Fact semantics. Global Reconciliation or the final incremental review is the last business-semantic decision point.

### Relationships, status, and retirement

- `related_event_ids`: independent related milestones without replacement or derivation direction;
- `supersedes_event_id`: current Event → Event it replaces or updates;
- `derived_from_event_ids`: new or split Event → source Event.

`related_event_ids` need not be mechanically symmetric; keep relationship intent coherent within one Bundle. A merge or split jointly determines the continuing stable ID, successor relationships, and retirement.

- `ACTIVE`: normal Published object;
- `MERGED`: object merged into its redirect target;
- `SUPPRESSED`: invalid object removed from the active set.

Use `MERGED_DUPLICATE_OCCURRENCE` for a merge, `SUPPRESSED_INVALID_OCCURRENCE` for invalid suppression, and `SPLIT_TO_SUCCESSOR` when a split retires the source toward its primary successor. Retirement applies to Canonical objects; `KEEP_PENDING` applies to unresolved Delta.

## Coverage and editing posture

The full Library favors broad coverage of valid ticker-specific occurrences; downstream compression comes from flags rather than omission. Keep plausibly ticker-relevant material in reconstruction while its context can reasonably be developed. Clearly unrelated occurrences, standalone market, technical, valuation, ratio, forecast or trading snapshots, portfolio or ETF commentary, and stale background belong in `KEEP_PENDING`. A bounded material market episode may qualify as an Event. This admission judgment is only the first layer of the separate Reference View decision.

Use assigned Delta and loaded Event Detail for Canonical judgment; Package context, indexes, candidate maps, and drafts provide navigation. Concentrate editing on the current run and affected scope. Preserve stable identities and unaffected content, retain existing non-null `price_analysis`, and use null for a new Event.
