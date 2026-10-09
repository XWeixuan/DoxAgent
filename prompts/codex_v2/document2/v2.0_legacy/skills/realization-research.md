# Turn 2: Realization Model — Mechanism Decomposition

## Purpose

Research the reality mechanisms that determine whether each Unit can be realized, when and how
strongly it can be realized, who captures the result, and how the business outcome converts into
financial value. The researched State describes what can be maintained as stable variables and
Values; Realization Factors preserve material conditions whose current meaning requires richer
business context.

A Factor maintains the reality status of a material mechanism, condition, or execution gate. That
gate may be continuous or realized through discrete milestones; future information should be able
to update its status through a concrete observation interface. Work across the complete Shell so
shared actors, dependencies, and value-transmission paths remain consistent, then attach each
Factor to the Unit whose realization it materially changes.

## Working inputs

Continue in the same O1 thread from the completed State turn. In `context.json`, `canonical_shell`
is the refreshed State-complete object to update. Retain the integrated understanding of C1, C3,
C5, and Future Nodes established in Turn 1; revisit a primary asset, horizontal or entity context,
optional Narrative Research, or Event Library only where mechanism analysis needs its specific
detail or later evidence.

## Research the realization model before selecting Factors

Start from the integrated Shell understanding and researched State developed in Turn 1. Use the
retained C1, C3, C5, and Future Node understanding as trusted prior research; revisit the relevant
asset or conduct focused research when a mechanism needs a specific actor, process, contract,
conflict, or additional evidence.

Before decomposing mechanisms, use the current `boundary_rule` and supplied final Shell map to
understand which expectation systems adjacent Shells model in depth. Dependencies may cross Shells.
Use that map to represent the transmission needed for the current Unit's realization while treating
the dependency's full State, mechanism, and event system as the adjacent Shell's depth scope.

Then use each Unit's `proposition` and `horizon` to define the outcome whose realization is being
studied, and use the current State to locate the measurable anchors already established. These
objects locate and organize the research; they are not sufficient sources of mechanism insight.
Trace the reality path from the integrated upstream research and current State to the Unit outcome,
then examine where the path can be enabled, delayed, redirected, weakened, or broken. Relevant
layers may include:

- demand and external conditions;
- qualification, adoption, or commercial access;
- order, resource, or market-share allocation;
- technical, production, operational, or regulatory execution;
- delivery and revenue recognition;
- competitive and bargaining-power distribution; and
- margin, working-capital, cash-flow, or terminal-value conversion.

This is a reasoning map rather than a fixed pipeline. Use the layers the Unit actually requires.
Follow the chain forward to understand realization and backward from the terminal outcome to find
indispensable dependencies, major failure points, and mechanisms that change timing, magnitude,
beneficiary, or conversion efficiency.

For each material link, ask:

- What must be true for value to pass through this link?
- Which company, customer, supplier, competitor, regulator, or technical system controls it?
- What evidence shows the difference between stated intent and actual execution?
- Is success one-off, repeatable, scalable, and economically productive?
- What lag separates progress at this link from the Unit's business or financial outcome?
- Can an alternative route, substitute, or competing actor redirect the value?

Then study the dependency structure rather than treating answers as isolated risks. Identify
sequences in which one condition is useful only after another holds, bottlenecks that dominate the
rest of the chain, feedback or learning effects created by repeated execution, and trade-offs where
improving one mechanism weakens another. The final Factor Set should preserve the few mechanisms
that explain how the system works together, while the State remains the set of current measurable
anchors inside that system.

## Admit a Realization Factor

A candidate belongs in the Realization Model when:

- it is an identifiable reality condition, constraint, or execution gate whose status matters to
  realization; it may be continuing or embodied in a discrete milestone when the business advances
  through such gates;
- its development materially changes realization, failure, timing, strength, beneficiary, or
  financial conversion for the Unit;
- the current State cannot express it faithfully through one stable Parameter and comparable
  Values;
- existing evidence can explain why it matters and what is currently known; and
- future information can confirm, challenge, or update its status.

Use these as object tests rather than a score or count target. A generic operating risk, broad
industry truth, or isolated metric becomes useful only when it exposes a material causal mechanism
for this Unit. Temporal shape alone does not decide the object. A launch, trial, qualification,
approval, or contract award can be central to a Realization Factor when it is the actual execution
gate through which the business progresses. A valid Factor may be widely known by the market
because its purpose is to model reality, not surprise.

## Assign the structural role by causal function

Choose `structural_role` from the Factor's place in the realization chain:

- `REQUIRED`: an important condition that must materially hold for the core realization path;
- `BLOCKER`: a mechanism whose presence or persistence can substantially interrupt that path; or
- `MODIFIER`: a mechanism that mainly changes timing, magnitude, beneficiary, product mix, margin,
  or conversion efficiency without alone deciding the entire proposition.

The role describes causal function, not positive or negative sentiment. Classify the Factor that
actually exists rather than filling all three roles for coverage. A condition can matter without
being strictly necessary, and an adverse influence can be a Modifier rather than a Blocker.

## Research the mechanism with reality evidence

For each candidate mechanism, compare the state required for the Unit to realize with the state
that the integrated C1, C3, C5, Future Node, and current State evidence supports. Identify the
controlling actors, their incentives and actual commitments, the operational dependency, and the
evidence that would distinguish capability from execution. Where those primary assets leave the
mechanism incomplete, define the missing question before new research. Prefer realized results and
actor behavior, then direct customer, supplier, competitor, or regulatory evidence, specific
operating and financial data, and broader commentary when it adds necessary context.

Discrete milestones deserve the same depth as continuous conditions when they carry the business
forward. For a launch, test, approval, qualification, or award, research what the event was designed
to prove, what it actually proved, which dependency remains, and whether the outcome is repeatable,
scalable, and economically relevant. A single success may remove one technical gate while leaving
cadence, reliability, customer adoption, or unit economics unresolved; a single failure may reveal
a blocker without proving the whole proposition impossible.

Once the model is clear, encode its material mechanisms concisely:

- `factor_id` names the mechanism or execution gate, and `condition` defines the one reality
  question whose status can change coherently;
- `current_status` states what is established at the task cutoff, the operational progress, and the
  material evidence boundary; absence of public confirmation remains distinct from an evidenced
  negative;
- `impact` locates the Factor in the chain and states whether it changes realization, timing,
  magnitude, beneficiary, or financial conversion, together with the limiting step that remains;
- `citation` supplies the principal evidence for the mechanism's relevance and current status; and
- `observability.match_condition` identifies future information or actor behavior that would
  confirm, challenge, or materially update the Factor.

In `impact`, keep adjacent economic steps distinct: demand is not target-company capture;
capability is not adoption; qualification is not allocation; capacity is not qualified output;
revenue is not margin or cash conversion; regulatory permission is not scaled commercialization.
Use only the distinctions that matter to the Unit.

A Factor can remain valid when its outcome is unconfirmed if its materiality is grounded and
`current_status` accurately records the evidence boundary. If focused research cannot support the
mechanism's materiality, current relevance, or future observability, move to the next mechanism
rather than repeatedly searching the same unsupported possibility.

## Reconcile State, Factor, and Gap boundaries

Turn 2 may make a local State correction when mechanism research reveals an object-level error. If
future information normally updates or replaces the Value of one stable observation target without
reinterpreting the target, represent the object in State. If each update requires explaining how a
mechanism has progressed, what has been validated, which dependency remains, why reality changed,
and how that status affects the Unit, maintain it as a Realization Factor. Value Type describes
representation form; update behavior distinguishes State from Factor, so an `EVIDENCE` stance does
not by itself turn a complex mechanism into State. Preserve valid State work and revise only the
objects whose boundary has become clear.

Factor and Gap classification depends on the research function, not solely on whether the real-world
item is discrete. A launch or other milestone can appear in the Realization Model as a critical
execution gate with a maintained current status and observation interface. The same future event can
also support a Potential Gap when the object being modeled is the occurrence itself and the
meaningful revision it could force in the Unit. Keep the two cognitive jobs distinct without forcing
a one-to-one Gap for every Factor.

## Review realization coverage across the Shell

Before completing the turn, actively review every Unit through the relevant mechanism lenses:

- Is the main realization path explained beyond the current State?
- Are the principal failure or blocking mechanisms represented?
- Are the decisive dependencies, bottlenecks, and interactions between mechanisms understood?
- Can competition, substitution, allocation, or bargaining power redirect the benefit?
- Does a milestone demonstrate a one-off success, or repeatable and scalable execution?
- What can materially change timing or result magnitude?
- What determines whether the business outcome converts into profit, cash flow, or terminal value?
- Has an important contextual mechanism been forced into State or left as generic background?

This review seeks coverage of the major causal paths, not a fixed number of Factors. Reconcile the
same mechanism across adjacent Units so its identity and current status remain consistent; retain
it in each Unit only where its `impact` is materially relevant to that proposition.

## Completion

Realization Research is complete when every Unit's main realization path, principal failure path,
and important result modifiers have been actively considered; State/Factor boundaries are coherent;
and each retained Factor has a specific condition, causally correct role, evidence-based current
status, bounded impact, principal citation, and usable observation interface. Further research
should mainly produce duplicate, low-impact, or unobservable mechanisms rather than change this
model materially. The Factor Set should explain the current Unit's reality mechanisms while keeping
its research depth consistent with the dependency systems assigned to adjacent Shells.

Return the complete `ExpectationShell`. Preserve the researched State, update each Unit's
`realization_factors`, carry existing `potential_gaps` and other canonical fields through unchanged,
and include only justified local State or semantic refinements discovered during this turn.
