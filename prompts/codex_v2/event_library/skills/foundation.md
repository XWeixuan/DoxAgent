# Canonical Foundation

Apply these semantics throughout initialization and incremental maintenance.

## Event Occurrence

An Event Occurrence is one identifiable action, disclosure, decision, result, incident, milestone, external shock, or material market episode anchored to when it occurred or became public.

Determine occurrence identity from the time or release anchor, actor and concrete action, object or matter stage, and disclosure context. One release may contain Facts about different subject periods. Different dates, institutions, actions, stages, or separately issued disclosures normally form different Events. Represent an ongoing matter as linked milestone Events rather than one expanding record.

Package and Atomic boundaries organize evidence; they do not determine Canonical boundaries. Merge across Packages when the occurrence is the same, and split a Package when it contains distinct occurrences.

## Fact

A Fact is one minimal, independently useful proposition that the occurrence produced, disclosed, confirmed, corrected, or materially changed. Preserve the actor, material figures, direction, conditions, horizon, and assertion state that distinguish the proposition. Set `assertion_state` from the proposition rather than the article's tone. Consolidate semantic duplicates and retain complementary Facts.

Use `subject_time: "SAME"` when the proposition shares the Event time and has no distinct reporting period or forecast horizon. Otherwise preserve the explicit subject period.

## Existing or new

Compare occurrence identity before topic similarity:

- same occurrence and same proposition: duplicate Fact;
- same occurrence and complementary proposition: revise the existing Event;
- same occurrence and corrected proposition: retain the stable Fact identity and revise it;
- new action, disclosure, date, institution, or matter stage: create a new Event.

A new communication can be a new occurrence even when it reiterates earlier content. Connect related milestones through the relationship fields supplied by the schema.

## Time

`occurred_at` is the occurrence or information-release time, not the Fact's subject period or a range of article dates. Match `occurrence_time_precision` to the event type. Earnings releases, filings, announcements, analyst actions, transaction milestones, and other date-specific public occurrences normally require `DAY`.

When such an occurrence is broad-period or `UNKNOWN` and the day is reasonably traceable, use focused Web Search to confirm it. Use broader precision for genuinely period-wide occurrences or when a focused search cannot resolve the date.

## Relevance

The full library favors broad coverage of valid ticker-specific occurrences; importance controls downstream selection, not admission.

Use `KEEP_PENDING` for valid content clearly outside the library's purpose: another company's occurrence without a direct ticker action or exposure, standalone price, technical, valuation, ratio, forecast, or trading snapshots, portfolio or ETF commentary, and stale background without a current occurrence. A clearly bounded material market episode may qualify as an Event.

Keep plausibly ticker-relevant material in reconstruction while its context can reasonably be resolved. Use `DROP_INVALID` for extraction failure or content without a usable business proposition.

## Canonical editing

Treat assigned Delta and loaded Event Detail as primary evidence. Use Package context, indexes, candidate maps, and earlier drafts for navigation. Concentrate quality review on material issues within the affected scope.

Make the title identify the actor, action, and distinguishing object. Let `canonical_summary` state what happened and its material outcome. Make `known_event_summary` easy for a smaller W1 model to recognize by retaining the date, action, stage, material terms, figures, and horizon that distinguish the Event.

Set `is_important` from durable decision relevance. Set `include_in_reference_view` from present usefulness to D2/D3. Preserve existing non-empty `price_analysis`; use null for a new Event.
