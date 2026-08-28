# O2 Event Library workspace rules

## Read order and context

1. Read `task.json` first to obtain the current stage, `content_input_order`, assigned Delta, access scope, prior work paths, output paths, and the Date Resolution and Reference View Decision Ledger paths.
2. Read `AGENTS.md`, `agent.md`, the combined `skill.md`, `context.json`, and `output_schema.json` in `content_input_order`. `skill.md` is the immutable snapshot of the Shared Canonical Foundation plus the Current Stage Workflow.
3. Read the Frozen View manifest and the business files required by the current stage: the complete Known Event Index, assigned Atomic Delta, Runtime Package index, permitted Event Details, reference-review candidates, prior-attempt work, upstream context, and the ledger schemas referenced by the manifest when the stage writes those artifacts.

Known Event Index rows contain `event_id | occurred_at | title` and an optional fourth `known_event_summary` cell; an absent fourth cell means the summary duplicated the title.

For initialization, read the locally copied C1, C3, and C5 report bodies and their hash manifest. Use D1 to understand actors, business context, relationships, forward matters, and possible boundaries.

## Evidence and adjudication

Assigned Atomic Delta and loaded Event Detail support Canonical publication. D1 informs interpretation; when D1 and Delta conflict, use Delta as the formal factual input and keep the item pending when the conflict remains unresolved.

Runtime Package membership, `runtime_hint_ids`, and `target_suggestion_ids` are navigation hints. `entities` supports Delta matching. Canonical assignment follows occurrence judgment, and Canonical Facts contain only fields supplied by their schema.

Treat legacy Atomic `time` and `subject_time_anchors` as raw or subject-period context rather than occurrence dates. Use `occurrence_date_candidates` for Event and Fact occurrence resolution, preserving each candidate's source and the distinction between occurrence and subject time.

Use focused Web Search to adjudicate conflicting propositions, contradictions with existing Canonical content, unclear occurrence boundaries, or a reasonably traceable date-specific public occurrence whose Frozen time is broad or `UNKNOWN`. Apply only information available by the Frozen `as_of`; search results remain adjudication context rather than Canonical Source fields.

Every assigned `D#` is accounted for exactly once through one Fact's `consumes_delta_ids` or one residual `resolution`. Canonical outputs exclude Mention, Evidence, Source, Runtime identifiers, citation aliases, Observation IDs, audit, lineage, and reasoning.

Preserve existing non-null `price_analysis`; new Events use null.

## Workspace and output boundaries

Treat `context/event_library/<frozen_view_id>/` and `attempts/<attempt_id>/input/` as read-only.

Write stage work only under the current attempt's `output/work/`. Initialization stages before `GLOBAL_RECONCILIATION` and incremental stages before `REFERENCE_REVIEW` leave working artifacts for later Turns.

Write a formal Event-per-file Revision Bundle only at the current task's `output_bundle_path` under `attempts/<attempt_id>/output/revision_bundle/`. Return that same workspace-relative path as `bundle_path`.

At `GLOBAL_RECONCILIATION` and `REFERENCE_REVIEW`, write the Date Resolution and Reference View Decision Ledgers to both the task's work paths and `bundle_ledger_paths`; each work ledger must match its Bundle copy.

The deterministic layer owns `artifacts/`, `published/`, SQLite, Runtime Registry data, validation, ID materialization, versioning, and publication controls.

## Completion

Return exactly one JSON object matching `output_schema.json`.
