# Turn 1: Expectation State — Convergent Anchoring

## Purpose

Research the current maintainable expectation State for every Unit in the canonical Shell. State
answers two connected questions: what stable variables must remain observable for this proposition,
and where do the relevant source roles currently place those variables? The result should let a
future event update a Value without redefining the underlying Parameter.

Work across the complete Shell before finalizing any Unit. The Units share a research system, so
common evidence and economic variables should be understood consistently; each Unit retains only
the State that materially informs its own proposition and horizon.

## Working inputs

The attempt workspace contains six injected files:

- `AGENTS.md`: workflow-wide execution and citation rules;
- `agent.md`: O1's stable role across all Shell research turns;
- `skill.md`: this turn's State Research method;
- `task.json`: the active node, required files, and any previous failure context;
- `context.json`: the current business object and available research inputs; and
- `output_schema.json`: the exact `ExpectationShell` contract to return.

In `context.json`, `canonical_shell` is the object to update. Although they are technically nested
inside `global_research`, treat its C1 report, C3 report, C5 report, and `future_nodes` as four
parallel primary Document1 research assets. C1 supplies company facts, management and sell-side
expectations, financial change, drivers, constraints, and transmission. C3 supplies industry and
value-chain conditions, external actors, allocation, competition, and commercialization evidence.
C5 supplies the market-implied baseline, repricing drivers, and priced business or financial
assumptions. Future Nodes identify known future variables, milestones, time windows, affected
entities, and evidence interfaces that the expectation system will later need to observe.

Entity relations and horizontal indicators help connect and compare those primary assets.
`narrative_research` is an optional view of current market attention and debate; `event_library` is
optional recent-event context. Read each optional input's availability before using its payload.
`turn` identifies the active research stage.

## Reconstruct Document1 research before defining State

Read the four primary Document1 assets for the complete Shell before designing Parameters. Form a
working research map of current company and financial reality, management and sell-side baselines,
industry and value-chain conditions, market-implied assumptions, and the known future variables or
milestones that will produce new evidence. Reconcile how the same issue appears across the four
assets and retain conflicts that may require separate source-role Values.

Only after this upstream research is understood should `proposition`, `horizon`, and adjacent Unit
boundaries determine which findings belong to each Unit and which stable observation targets are
needed. For each Unit, work backward from its proposition and horizon: which stable dimensions show
where that expectation stands today—what has been realized, where relevant actors place the future,
what can repeatedly change the judgment, and which major dimension remains unresolved? Use this
coverage reasoning to guide Parameter selection, then use upstream findings and focused research to
populate it. The Unit Seed organizes the research; it does not supply the State by itself. A Future
Node may reveal a Parameter, time scope, or future Value source, but it is a research lead rather
than an automatic State object. A data point becomes a Value only after its stable Parameter is
clear.

A Parameter is a continuing observation dimension that locates the proposition's current
realization state, expectation position, or stable premise. It may maintain an actual result,
management or sell-side expectation, industry condition, timing, stage, or market-implied underlying
expectation. Its role is to show where the proposition currently stands, rather than compress the
overall credibility of the proposition into one second-order judgment.

A candidate Parameter is suitable when:

- its identity remains the same as its Values change;
- later periods or source roles can provide new Values for the same defined object;
- Values can be compared under a stable economic and measurement basis; and
- movement in the Parameter would materially change understanding of the Unit.

Use these as object tests rather than a scoring exercise. Select Parameters for explanatory and
update value, not for a preferred count. A topical fact, one-time event, source-specific sentence,
or generic background item does not become a Parameter merely because it is important.

## Define a comparable Parameter

`parameter_id` names the continuing observation target. `definition` fixes the meaning required to
keep later Values comparable; it describes the variable, not its current result. Set only the scope
that prevents a real ambiguity, such as company or product perimeter, geography, fiscal or product
cycle, financial layer, measurement basis, or relevant unit convention.

Resolve distinctions that would otherwise create false comparisons. Depending on the object, these
may include gross versus net, spot versus contract, order versus backlog versus shipment versus
recognized revenue, reported versus adjusted results, or fiscal versus calendar periods. When two
items cannot share one stable definition, they are different Parameters or one is unsuitable for
the current State.

The source role belongs to the State Value, not the Parameter. For example, management and sell-side
views of the same forecast object are different Values of one Parameter when their scope is genuinely
comparable; they are not separate Parameters simply because different actors supplied them.

## Choose the Value Type by the information's true form

Use the narrowest supported representation that preserves the source meaning:

- `NUMBER` for a defined point estimate with a reliable unit;
- `RANGE` when the maintained state is genuinely bounded by lower and upper values;
- `TIME` when the state is a point or window in time;
- `STAGE` when the real process has a recognizable business, technical, regulatory, or commercial
  stage;
- `DIRECTION` when a maintainable direction is more truthful than an unavailable level; and
- `EVIDENCE` when the stable observation target is an evidence stance whose support can be updated
  over time.

`STAGE` should reflect a real progression rather than an invented maturity scale. `DIRECTION` does
not substitute for an obtainable number. `EVIDENCE` fits a stable observation target whose support
state can be updated directly when new information arrives. If each update instead requires renewed
interpretation of how a mechanism is progressing, which dependencies hold, and why the condition
changes the Unit, maintain that richer reality in a Realization Factor's `current_status`.
`EVIDENCE` is a Value representation, not a general compression of complex mechanisms.

Express `DIRECTION` as `IMPROVING`, `STABLE`, or `WEAKENING`. For `EVIDENCE`, use `SUPPORTING`,
`OPPOSING`, or `MIXED` as stance and `WEAK`, `MODERATE`, or `STRONG` as strength. These labels
normalize an already valid Parameter; they do not replace the underlying research judgment.

## Research Values by source role

For each Parameter, determine which source roles can provide a meaningful current Value. The roles
represent different layers of reality and expectation rather than a completeness checklist:

- `ACTUAL`: an already realized operating, financial, technical, or commercial state, regardless
  of which document reports it;
- `MANAGEMENT`: the company's forward guidance, plan, target, or stated expectation;
- `SELL_SIDE`: an analyst or consensus forecast for the defined object;
- `INDUSTRY_CHAIN`: a customer, supplier, competitor, channel, or other industry actor's view or
  behavior that states the Parameter; and
- `MARKET_IMPLIED`: a specific state that can be reasonably inferred from an observable price,
  valuation, options, positioning, or other market anchor.

Keep distinct source-role Values when their difference reveals the current expectation structure.
An issuer's report of an accomplished result is `ACTUAL`, while its claim about the future is
`MANAGEMENT`; classification follows the economic meaning, not the publisher. A broad share-price
move alone does not define a `MARKET_IMPLIED` Value for a specific Parameter.

For `MARKET_IMPLIED`, first infer the underlying expectation the market anchor is pricing, then use
that expectation's natural Value Type: for example, `NUMBER` or `RANGE` for an earnings level,
`TIME` for normalization timing, `STAGE` for a commercial state, or `DIRECTION` for a maintainable
direction. Limited inference precision should first widen the range, coarsen the time window, or
reduce numerical precision rather than automatically turn the object into a generic `EVIDENCE`
stance. Leave the role absent when focused research still cannot support an interpretable Value.

Use all four primary Document1 assets and their citations to discover and support the Parameters and
Values implied by the upstream research, then identify what remains unresolved at the Unit's
required granularity. For every admitted Parameter, actively examine all five source roles and try
to complete each Value that can genuinely express the same defined object. Use focused data or web
research for an applicable role that still lacks a current anchor, comparable earlier Value, clear
scope, resolution of a material conflict, or relevant later information. State the missing research
question first and seek evidence closest to that role and object.

When an important current Value has a readily obtainable prior observation that is economically
comparable under the Parameter's update logic, actively retrieve it. Prior actuals, guidance,
sell-side or industry snapshots, and market-implied snapshots are useful when they reveal change;
historical backfill follows comparison value rather than a requirement that every Value have a
previous observation.

Source roles remain different evidence layers rather than mandatory cells. When a focused,
reasonable attempt does not find a reliable and comparable Value for a role, leave that role absent
and continue with the other Parameters instead of repeatedly searching the same gap. This produces
the fullest supportable State without turning upstream research into an audit queue.

## Preserve time, basis, and provenance

For every State Value:

- `value` must match the Parameter's `value_type` and preserve the source's supported precision;
- `time_scope` identifies the business, forecast, or operating period the Value describes;
- `as_of` identifies when that Value was current or observed;
- `citation` provides the principal evidence for the stated Value; and
- `state_value_id` gives the Value a natural-language semantic identity that remains interpretable
  within its Parameter and source role.

Time scope and `as_of` are different. A forecast for FY2028 uses FY2028 as its time scope and the
dated forecast snapshot as its `as_of`. A source that does not support an exact number should
produce a supported range, direction, stage, or no Value rather than borrowed precision.

Use `previous_value` for a comparable prior observation under the Parameter's own update semantics,
with the same Parameter meaning, source role, and measurement basis. For a rolling realized
Parameter, the preceding business period may be the correct comparison; for a fixed forecast target,
use an earlier snapshot of that same target; for an explicitly rolling forward Parameter, compare
the prior rolling snapshot when the definition makes that economically meaningful. Ask how this
Parameter normally updates and which earlier state is its true comparison. A changed perimeter,
accounting basis, or operating concept still breaks comparability. Include evidence sufficient to
support both sides of a claimed change.

`CURRENT` is the normal validity state for a presently usable Value. `DISPUTED` preserves a live
conflict that the available evidence does not resolve. `RETRACTED` reflects an explicit withdrawal,
and `SUPERSEDED` applies when retaining a record that a newer comparable statement has replaced.
`previous_value` remains the compact way to express an ordinary comparable change.

## Handle absence and disagreement without compression

A missing source role is represented by its absence. If no reliable current Value exists from any
role, retain the Parameter only when its stable monitoring identity is essential to the Unit and a
credible future update path exists; otherwise it is not yet part of the maintainable State.

When Values disagree, first test whether scope, horizon, basis, or source role explains the
difference. Comparable Values from different roles should coexist because the disagreement may be
the most important description of current expectations. A real unresolved conflict within the same
defined object should remain explicit through the available Value and validity semantics rather
than being averaged into a synthetic baseline.

## Reconcile the Shell and complete the turn

Before returning the Shell, reconcile equivalent Parameters across Units so their names,
definitions, Value types, and shared evidence remain semantically consistent. Retain a Parameter in
each Unit only where its movement has actual relevance to that proposition. Confirm that every Value
references a defined Parameter and that similar wording has not created duplicate observation
targets inside a Unit.

State Research is complete when each Unit has enough maintainable State to locate its current
expectation; retained Parameters have stable identities and update paths; every Parameter has been
considered across all source roles, with focused research attempted for applicable missing Values;
unsupported roles have been deliberately left absent; and retained Values preserve role, scope,
time, evidence, and supported precision. Review whether the complete State Set locates the
proposition across its major realization and expectation dimensions, and whether meaningful,
readily obtainable prior observations were considered for key current Values. Important reality
mechanisms that resist stable parameterization remain available for the next turn.

Return the complete `ExpectationShell`. Update each Unit's `state` and carry the other canonical
fields and existing later-stage objects through unchanged, apart from justified semantic refinement
of the existing Shell or Unit definition.
