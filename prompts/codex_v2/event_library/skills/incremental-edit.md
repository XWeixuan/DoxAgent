# Incremental: Reconstruct and Edit

Turn the current Delta batch into complete revisions of the Events it changes. The Candidate Map selects history to inspect; the Delta and loaded Event Details determine the occurrence judgment.

## Occurrence decision

1. Read the Candidate Map, every assigned Delta with its Runtime context, and every loaded Event Detail, including complete Facts, Event and Fact occurrence times, subject periods, and supersession relationships. Reconstruct each incoming occurrence before deciding where it belongs.
2. Compare occurrence identity before topic similarity:
   - the same occurrence and proposition is `DUPLICATE_FACT`;
   - a complementary proposition from the same occurrence updates the existing Event;
   - a correction or clearer expression of the same Fact keeps its stable Fact ID;
   - a new action, disclosure, matter stage, catalyst, information cycle, or occurrence outside the existing episode boundary forms a new Event; a different Fact date alone does not.
3. Resolve analyst actions through the shared-catalyst, response-pattern, and bounded-window test. Preserve institution-specific actions as Facts within a valid response episode and separate actions driven by a new catalyst or information cycle.
4. Keep material reasonably connected to the ticker while its occurrence can be reconstructed. Use `KEEP_PENDING` for clearly out-of-scope valid content or relevant material whose boundary remains unresolved after reasonable development. Use `DROP_INVALID` for extraction failure or content without a usable proposition.

## Canonical editing

1. Admit a Fact when its Event produced, disclosed, confirmed, corrected, or materially changed the proposition. Keep Facts minimal, consolidate semantic duplicates, and place all same-batch support for one new Fact in `consumes_delta_ids`.
2. Resolve Event occurrence, each Fact occurrence, and subject time separately before using time to decide identity or current state. Follow the Foundation date priority, search a traceable date-specific occurrence within the Frozen `as_of`, keep only genuinely period-wide Events broad, and leave unresolved or conflicting date-specific material Pending.
3. Write `fact_occurred_at` and DAY precision for every Fact in a complete Event revision. Use Fact-occurrence `SAME` only under a same-day DAY Event; a broad Event requires each Fact's exact date. Preserve a distinct reporting, forecast, plan, or scheduled period in `subject_time`.
4. For an existing Event, retain its stable Event ID, unaffected Facts and Fact IDs, relationships, and `price_analysis`, then write the complete target revision with corrected maintenance-v3 time fields. New Events and Facts use temporary IDs and new Event `price_analysis` is null.
5. When the affected evidence exposes an occurrence-boundary error, merge or split within that affected scope, preserve the most appropriate stable ID, and express retirement and relationships through the supplied schemas. When a new Event supersedes an old Event, encode that relationship so both sides enter Reference Review.
6. Edit the title, summaries, relationships, and other schema fields into one coherent target record. For every new or affected Event, judge `is_important`, make an initial Reference decision from its current-state effect, and record the matching basis and concise note. Reference Review rejudges the full candidate independently rather than inheriting this initial flag.
7. Account for every assigned Delta exactly once through one Fact's `consumes_delta_ids` or one residual `resolution`. `DUPLICATE_FACT` identifies its stable Event and Fact target; same-batch duplicates for a new Fact are consolidated in that Fact. Update the Date and provisional Reference ledgers for every Event revision in the working set.

## Artifacts

Write the next-stage working set under `output/work/` using only the supplied schemas:

```text
output/work/
  events/E#.json or events/T#.json
  retirements.json
  residual_delta_resolutions.jsonl
  date_resolution_ledger.jsonl
  reference_view_decision_ledger.jsonl
```

Include complete revisions only for new or affected Events. Input navigation and Runtime fields remain outside Canonical Event and Fact files.

## Completion

Return the current `O2RunResult` with `status: PENDING`, `stage: RECONSTRUCT_AND_EDIT`, `bundle_path: null`, the supplied base version, exact Delta coverage, and `validation: NOT_RUN`.
