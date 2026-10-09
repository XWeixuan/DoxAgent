# Turn 4: Shell Research Finalization — Curation and Schema Closure

## Purpose

Turn the research-complete canonical Shell into a durable expectation model. The preceding turns
optimized State, Realization Factors, and Potential Gaps separately; Finalization makes them one
system with aligned meanings, resolved references, usable evidence and time semantics, and a
complete structure under the supplied `ExpectationShell` schema.

Continue in the same O1 thread on the refreshed `canonical_shell`. This is curation, not a fourth
research turn. Resolve new factual ambiguity only where it prevents a material object from being
interpreted or repaired.

## Recover the model's intent

Read the Shell as an expectation system before editing fields. `core_question` defines the shared
research question, `boundary_rule` defines which propositions belong in that context, and each
Unit's `proposition` and `horizon` define one expectation and its update period.

For every object, ask what future update it helps interpret and which Unit that update would revise.
A shared object may appear in several Units when it materially informs each proposition; keep its
core meaning consistent and its consequence Unit-specific.

Prefer repair to loss of research: normalize wording, correct references or time bases, reclassify,
improve object formulation, and reconcile true duplicates. Remove an item only when it has no
distinct function after repair, is unsupported as an object, or genuinely duplicates a retained
item.

## Close State, Factor, and Gap boundaries

Classify each object by its update function:

- **State** is a stable observation target and its current source-role Values. New evidence updates
  a Value while the Parameter keeps the same meaning.
- **Realization Factor** is a material current condition, mechanism, constraint, or execution gate
  whose contextual status changes whether, when, how strongly, or for whom a Unit can be realized.
- **Potential Gap** is a recognizable future occurrence or bounded development that the model can
  use to revise the Unit if it happens.

The same subject may validly appear in different classes when it performs different jobs: a current
qualification stage may be State, qualification dependence a Factor, and an unexpected failure a
Gap. Repair cross-class repetition only when two objects perform the same job. Reclassify misplaced
content by its update behavior while preserving its researched substance.

## Finalize the State graph

Read each Unit's Parameters and Values together as a maintainable observation graph.

Each `parameter_id` names one stable object; `definition` fixes the scope needed for comparability.
Merge Parameters only when meaning, perimeter, basis, and update path are the same. Similar topics
remain separate where different scopes or bases would create false comparisons.

Every `StateValue.parameter_id` resolves within its Unit, and `value` and `previous_value` match the
Parameter's `value_type`. `source_role` identifies the evidence layer, `time_scope` the period
described, and `as_of` when the Value was current. A missing role remains absent.

Keep `previous_value` when the earlier observation is comparable under the Parameter's update
semantics, with the same Parameter meaning, source role, and measurement basis. The prior business
period may fit a rolling realized Parameter; an earlier snapshot of the same target fits a fixed
forecast; a prior rolling snapshot may fit an explicitly rolling expectation. Use `validity_state`
for meaningful disputes, withdrawals, or supersession.

Normalize shared IDs and definitions without erasing Unit-specific scope. Retained Parameters need
a credible update identity; Values need interpretable role, scope, date, precision, and evidence.
Then read the State as a whole: it should locate the proposition across its major current
realization and expectation dimensions. When accumulated research has clearly exposed an important,
maintainable dimension omitted in Turn 1, backfill that local State object.

## Finalize the Realization Model

Read Factors as a causal system explaining how the proposition is enabled, blocked, delayed,
strengthened, redirected among actors, or converted into business and financial outcomes.

Align each Factor around one mechanism:

- `condition` identifies the maintained reality condition or gate;
- `structural_role` states whether it is REQUIRED, a BLOCKER, or a MODIFIER;
- `current_status` states its presently researched status;
- `impact` explains how it changes this Unit's realization; and
- `observability.match_condition` identifies future evidence that would update its status.

Merge Factors only when mechanism, causal position, and observation interface match. Keep related
mechanisms separate when they can change independently or act through different paths. Shared
Factors keep a consistent identity while `impact` remains Unit-specific.

Uncertain status is compatible with a material Factor when stated honestly; improve its causal
boundary and observation interface rather than manufacturing certainty.

## Finalize Potential Gaps without losing future coverage

Preserve the open event coverage developed in Gap Research rather than reducing it to Document1
events, obvious reversals of current Factors, or only the most probable scenarios.

For each Gap:

- `possible_occurrence` describes one recognizable future occurrence or bounded development;
- `derivation` explains why it belongs in the plausible possibility space;
- `expected_revision` states how occurrence would change the Unit's proposition, path, timing,
  magnitude, allocation, or financial interpretation; and
- `recognition_criteria` resolves the event boundary when ordinary wording is materially ambiguous.

Keep a bounded sequence when its elements jointly form one recognition thesis and one revision
thesis. Split components that can occur independently and cause different revisions. Merge only
when both occurrence boundary and expected revision match. A cross-Unit occurrence keeps one
semantic identity and Unit-specific revisions.

When a Gap is immature, recover the future revision function it was meant to cover and improve its
occurrence, derivation, expected-revision boundary, or overlap with adjacent Gaps. Low probability,
no direct upstream clue, baseline conflict, or an empty event-level citation does not make it
redundant. It loses independent value only when reasonable repair leaves no distinct, possible,
material, and recognizable revision function.

## Reconcile the complete Shell

Compare adjacent Units and the full Shell. Units remain distinct when outcome, horizon, beneficiary,
financial layer, or revision path differs. Repair overlap where two Units now express the same
expectation and would be updated identically.

Use interpretable natural-language semantic IDs. Keep shared definitions consistent without erasing
Unit-specific scope; follow semantic identity and update behavior rather than keyword similarity.

Within each Unit, align proposition and horizon, current State, realization mechanisms, and future
revision events. They need not map one-to-one, but must describe the same expectation without an
unexplained shift in perimeter, product, geography, horizon, actor, beneficiary, or financial end.

## Close evidence and time semantics

Keep citations attached to the supported premise after edits: State citations support Value and
scope; Factor citations support condition, mechanism, or status; Gap citations support its known
derivation rather than imply that the future event occurred.

Retain citation lineage according to the workspace contract and prefer evidence material to the
final wording. Unresolved citations or empty lists are non-blocking; they do not justify invented
aliases or automatic removal. Investigate only a visible, material citation mismatch, temporal
conflict, or unsupported precision.

## Refine the Shell Seed where research requires it

O0's Shell membership, Unit identities, and core semantic boundaries are the canonical research
structure. Refine `core_question`, `boundary_rule`, `proposition`, or `horizon` where wording can
express the same object more accurately, while maintaining that Shell and Unit identity.

## Schema closure and completion

Return exactly one complete `ExpectationShell` matching `output_schema.json`, not edits or partial
Units. Use only supplied fields, enums, and value shapes. Confirm that:

- Shell, Unit, Parameter, Value, Factor, and Gap IDs are stable natural-language semantic names and
  unique within their reference scope;
- every internal reference resolves and every typed Value matches its Parameter;
- required strings contain finished business meaning rather than placeholders;
- no object exists merely for apparent completeness;
- no obvious object-boundary, time-basis, duplication, or Unit-semantic conflict remains; and
- useful disagreement, mechanism detail, uncertainty, and future-event breadth remain represented.

Completion requires a coherent, updateable model, not equal object counts, complete source-role
coverage, certain Factors, or directly evidenced high-probability Gaps. Preserve remaining
uncertainty through the schema rather than hide it with artificial completion.

Output the final Shell JSON without a narrative conclusion, market view, probability assessment,
trading bias, or an additional wrapper.
