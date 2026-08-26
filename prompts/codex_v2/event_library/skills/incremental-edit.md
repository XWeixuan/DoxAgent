# Incremental: Reconstruct and Edit

Turn the current Delta batch into complete revisions of the Events it changes. The Candidate Map selects history to inspect; the Delta and Event Details determine the final occurrence judgment.

## Work

1. Read the Candidate Map, every assigned Delta, its runtime context, and every loaded Event Detail. Reconstruct each incoming occurrence from its actor, concrete action or disclosure, object or stage, and occurrence time.
2. Compare occurrence identity before topic similarity:
   - the same occurrence and proposition is `DUPLICATE_FACT`;
   - a complementary proposition from the same occurrence updates the existing Event;
   - a correction or clearer expression of the same Fact keeps its stable Fact ID;
   - a different date, institution, action, disclosure, or matter stage forms a new Event.
3. Apply a loose relevance screen. Publish discrete occurrences materially about the ticker or its business, including clearly bounded material market episodes. Use `KEEP_PENDING` for valid but unrelated content, standalone snapshots, or relevant material whose boundary remains unresolved. Use `DROP_INVALID` for clear extraction failure or content without a usable business proposition.
4. Admit a Fact when this occurrence produced, disclosed, confirmed, or materially changed its proposition. Keep Facts minimal and consolidate semantic duplicates. Multiple current Delta supporting one new Fact belong together in `consumes_delta_ids`.
5. Match time precision to the event type. Date-specific public occurrences normally require `DAY`; when their date is broad or `UNKNOWN` but reasonably traceable, use Web Search to resolve it. Use `subject_time: SAME` when a Fact shares the Event time without a distinct subject period, and preserve an explicit reporting period or forecast horizon when it is part of the proposition.
6. For an existing Event, preserve its stable Event ID and every unaffected Fact, then write the complete target revision. Preserve a Fact ID when its semantic identity remains the same. Use temporary IDs only for genuinely new Events or Facts.
7. Merge or split published Events when the affected evidence exposes an occurrence-boundary error. Keep the most appropriate stable ID for the continuing occurrence and express retirements or derived relationships through the current bundle schema.
8. Edit the title, summaries, relationships, and current-schema fields so the Event is coherent after the change. Carry existing importance and reference values into revised Events and give new Events an initial judgment for Reference Review to finalize. Preserve existing `price_analysis`; set it to null for a new Event.
9. Account for each assigned Delta exactly once through a Fact's `consumes_delta_ids` or one residual resolution. A duplicate of a stable Fact targets that Event and Fact; same-batch duplicates for a new Fact are consolidated in that Fact.

## Artifacts

Write the next-stage working set under `output/work/` using only the supplied schemas:

```text
output/work/
  events/E#.json or events/T#.json
  retirements.json
  residual_delta_resolutions.jsonl
```

Include complete revisions only for new or affected Events. Input-only navigation and runtime fields remain outside Canonical Event and Fact files.

## Completion

Return the current `O2RunResult` with `status: PENDING`, `stage: RECONSTRUCT_AND_EDIT`, `bundle_path: null`, the supplied base version, exact Delta coverage, and `validation: NOT_RUN`.
