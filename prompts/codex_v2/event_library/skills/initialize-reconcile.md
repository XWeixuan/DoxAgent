# Initialize: Global Reconciliation

Produce the initialization Revision Bundle as the single global Canonical judgment. This stage combines occurrence reconciliation with complete Canonical editing.

## Working context

Read the complete pending Delta, published C1/C3/C5 reports, Future Nodes, Survey ledger and catalog, and every Wave index, draft, and provisional decision ledger. Original Delta supplies the factual basis; D1 research supplies business interpretation; Survey and Wave outputs are prior research hypotheses to refine from the global view.

## Global occurrence synthesis

1. Reconstruct the Event set from occurrence identity rather than Package, topic, wave, or temporary ID. Merge cross-wave drafts for the same occurrence and split drafts that combine distinct actions, disclosures, matter stages, catalysts, or information cycles. Different Fact dates remain within one Event when the shared-catalyst and bounded-window test establishes a valid theme cluster.
2. Resolve analyst response episodes from their shared catalyst, response pattern, and information window. Preserve institution-specific actions as Facts and separate actions driven by a new catalyst, thesis, or information cycle.
3. Distinguish the same occurrence and proposition, a complementary Fact within the same occurrence, a corrected proposition, and a genuinely new occurrence. Connect related milestones through the supplied relationship fields.
4. Apply the loose relevance screen after reconstruction. Publish valid occurrences materially connected to the ticker or its business, including bounded material market episodes. Develop plausibly relevant but incomplete material from the full context before assigning a residual resolution. Use `KEEP_PENDING` for unresolved or clearly out-of-scope valid content and `DROP_INVALID` for extraction failure or content without a usable proposition.

## Canonical finalization

Finalize each Event through the following linked judgments:

1. Confirm occurrence identity, global temporary ID, ticker, status, title, and canonical event type.
2. Resolve Event occurrence, every Fact occurrence, and subject time as separate judgments using the supplied candidates and Foundation priority. Date-specific public occurrences resolve to `DAY`; focused Web Search may complete a traceable date within the Frozen `as_of`. Publish a broad Event time only for a genuinely period-wide Event, preserve an exact DAY for every Fact beneath it, and leave conflicting or unresolved date-specific material Pending.
3. Confirm that every Fact was produced, disclosed, confirmed, corrected, or materially changed by its Event. Keep propositions minimal, preserve decision-relevant qualifiers, consolidate semantic duplicates, and set assertion state from the proposition. Write `fact_occurred_at` and DAY precision for every Fact; use Fact-occurrence `SAME` only under a same-day DAY Event, and use `subject_time: SAME` only when no distinct subject period exists.
4. Write `canonical_summary` as the compact account of what occurred and its principal business result. Write `known_event_summary` with the date, actor, action, stage, figures, and horizon needed for W1 to distinguish nearby known Events.
5. Rejudge `is_important` from durable decision relevance. Then apply the Foundation's six-step Reference test, omission test, and redundancy test independently to select `include_in_reference_view`, its matching `reference_view_basis`, and a concise current-state note for every Event.
6. Finalize relationships, retirement, and `price_analysis` under the supplied schemas. Assign globally unique `T#` and `TF#` IDs and populate exactly the schema fields.
7. Account for every pending Delta exactly once through one Fact's `consumes_delta_ids` or one residual entry. Use the schema field `resolution`; `DUPLICATE_FACT` identifies its stable target, while `KEEP_PENDING` and `DROP_INVALID` carry no target. Give every Delta at least one Date Resolution Ledger row, every Event and Fact revision its matching occurrence row, and every Event its Reference decision and ledger row.

## Final quality pass

Run two targeted checks before assembly:

- **Time:** find subject periods or future scheduled targets used as occurrence, occurrence dates after `as_of`, broad Events whose Facts use `SAME` or lack exact dates, valid theme clusters split only by Fact date, and different cycles or themes merged too broadly.
- **Reference:** apply omission and redundancy tests to find old-but-active omissions, recent-but-routine inclusions, superseded Events still retained, and flags copied from importance rather than judged independently.

Also reconsider duplicate titles, inconsistent event types, unusually large multi-Fact Events, or missing relationships despite evident milestone sequences. All-true or all-false flag patterns are diagnostic signals only; selection quality remains an Event-by-Event judgment rather than a quota.

## Bundle

Write the complete Bundle at the task's `output_bundle_path` using the supplied schemas:

```text
revision_bundle/
  manifest.json
  events/T#.json
  retirements.json
  residual_delta_resolutions.jsonl
  reference_review_decisions.jsonl
  date_resolution_ledger.jsonl
  reference_view_decision_ledger.jsonl
```

Write the two ledgers to their task-supplied work paths and copy the same rows into the Bundle ledger paths.

## Completion

Return the current `O2RunResult` with `status: BUNDLE_READY`, `stage: GLOBAL_RECONCILIATION`, the workspace-relative bundle path, the supplied base version, exact Delta coverage, and `validation: NOT_RUN`. Deterministic validation follows this stage.
