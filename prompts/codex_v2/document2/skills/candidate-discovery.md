# Candidate Discovery internal skill

## Purpose and mental model

Discover economic subjects that will remain worth forming and revising expectations about as new
information arrives. A Candidate Unit identifies the subject whose future state, trajectory, or
economic significance will still matter after today's evidence becomes stale. Current facts,
forecasts, tensions, and unresolved questions help reveal that subject; they are not its permanent
definition.

A meaningful future change in the subject should be capable of changing expectations about the
company's business, earnings, cash generation, risk, capital needs, or valuation. This materiality
explains why continuing research matters; it does not prescribe a terminal financial outcome.
O1 later develops the subject's State, Expectation Baseline, Realization Factors, and Potential Gaps.

## What counts as a Candidate Unit

A Candidate Unit is a durable economic subject with an open future that materially matters to the
company and can be meaningfully updated without requiring the entire company thesis to move with it.

- **Durable:** The name identifies something in the economic world, such as a demand base, business,
  commercial market, cash-generation activity, or financing structure. These are examples, not a
  taxonomy. If today's outlook reverses, would the same name still identify a worthwhile subject?
  "AI基础设施需求" survives stronger or weaker demand; "AI需求能否转化为重复订单" embeds a current
  transmission question in the identity.
- **Future-facing:** What will we still need to maintain a view on next quarter, next product cycle,
  or after a major industry change? "Q2服务器单位增长9%" is a reported fact that may reveal
  "企业与云服务器需求" as the continuing subject. A subject need not be controversial today to have
  a material future worth maintaining.
- **Independently updateable:** Could important new evidence materially change the maintained view
  of this subject without mechanically requiring all adjacent subjects to change together?
  Independence concerns economic judgment, not a unique news feed or mutually exclusive evidence.
  The same information may update related subjects differently.

## Discover durable expectation objects

Follow the economic meaning of the assigned research rather than extracting from section headings.
Operating facts, management outlooks, industry changes, external actors, transmission relationships,
market pricing, unknowns, and Future Nodes can all reveal a subject.

1. **Find the material signal.** Identify what makes a fact, tension, or forward-looking conclusion
   economically consequential for this company.
2. **Move from the signal to its subject.** Ask: what persistent economic subject makes this
   information worth caring about? Increasing customer qualification with uncertain orders may
   reveal customer demand and adoption in the relevant business. The continuing object is not
   automatically "whether qualification converts into orders."
3. **Separate identity from today's thesis.** Remove direction, a suspected realization path,
   success thresholds, and explanatory exclusions from the object's name and scope. Keep the
   business features that identify the subject. A customer, product, or stage belongs in the
   identity when it warrants continuing maintenance in its own right, rather than merely locating
   today's event.
4. **Test persistence.** If today's particular event did not occur, would the subject still deserve
   future updates? If its relevance disappears with that event or one proposed mechanism, look for
   the underlying subject instead.

This is a change in abstraction, not an instruction to make every object broader. Preserve the
source's economic meaning while separating what is being studied from what is currently believed
or being tested about it.

## Judge granularity and independent updateability

Choose a coherent subject broad enough to accommodate several future drivers and revision paths,
including developments the source has not anticipated, but focused enough for a major new fact to
change the maintained view of the subject as a whole.

**Too narrow:** Does the candidate mainly isolate a metric, milestone, operating condition, or one
realization step? "某OEM下一代机型增加采购" usually supplies evidence within a demand or adoption
object. The wider subject can respond to customer adoption, replacement cycles, inventories,
competition, and other developments without being renamed. Retain a narrower object when its own
lasting economic significance and independent revisions justify it.

**Too broad:** Would almost any company news fit, while a major development changes only a small,
otherwise unrelated corner? "公司未来业务表现" provides no focused object for O1 to maintain.
Identify the economically distinct subjects inside it.

Use independent updateability to distinguish worthwhile maintained views, not to turn each driver
into a separate candidate. Several observations can jointly inform one object; shared evidence can
also support separate objects. Choose the boundary that preserves a meaningful economic judgment.

## Use the assigned research

In `context.json`, `source_role` identifies the assigned `c1/c3/c5/narrative` perspective and
`primary_source` contains its report; Narrative arrives as a serialized JSON string. Use that
report as the main discovery basis. Its perspective does not determine the eventual architecture.

Use supplied `future_nodes`, `entity_relations`, `horizontal_indicators`, and available
`event_library.payload` to clarify the subjects the report reveals. Treat upstream findings as
the research starting point. Further permitted research is useful when a material ambiguity about
the object or its company relevance remains, rather than to re-audit every upstream conclusion.
Respect `as_of` as the information boundary.

For C5, trace market, price, valuation, and implied-expectation evidence to the economic subject being
priced. Measuring whether the market has priced something is a research task, not itself a Candidate
Unit. Narrative discussion can similarly reveal a durable subject; attention alone does not establish
economic materiality.

Recall is preferred here. Preserve defensible overlapping objects when their identity is not yet
settled; do not suppress one because another branch may discover it. Optional material can be absent,
and `narrative_run_id: null` is normal outside the Narrative branch. Use the content actually present.

## Candidate record and completion

Return `candidates` and `warnings` according to the supplied `output_schema.json`. Each candidate
contains only:

- `name`: a short, stable, noun-based economic subject name, unique within this source's set.
- `scope`: what business aspects of that subject will be studied over time. Be more specific than
  the name while keeping the future open. Scope is not a hidden proposition containing success
  criteria, a preferred causal path, or required exclusions.
- `why_material`: the concrete economic consequence that makes the object worth maintaining
  separately, rather than merely tracking as a lower-level variable inside another object. Give
  enough company-specific meaning for the next stage to judge it without rebuilding the report;
  "affects revenue, profit, and valuation" alone does not do that.
- `ref`: the principal supplied references supporting identification and materiality. Preserve
  their lineage; use `DoxAtlas:<narrative_run_id>` for Narrative provenance when available.

For example, `name: AI基础设施需求` with `scope: AI基础设施需求及公司相关产品在客户、平台和工作负载中的需求与业务暴露。`
describes a maintained object without requiring repeat orders or a particular earnings path.

IDs, `candidate_ref`, propositions, and horizons are not outputs of this stage. Use `[]` for empty
lists. Warnings record material coverage limitations, not routine optional-input absence or an
unresolved reference.

Finish when the materially distinct future-facing subjects revealed by the source have been
considered and further candidates would mainly duplicate a subject, restate a current fact, isolate
a lower-level variable or mechanism, or inflate an object into a company-wide theme. Uncertainty
about future outcomes is compatible with discovery; resolving those outcomes is O1's later work.
