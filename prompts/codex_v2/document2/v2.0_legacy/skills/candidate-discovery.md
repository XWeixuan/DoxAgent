# Candidate Discovery internal skill

## Purpose

Candidate Discovery is the high-recall first stage of Shell Construction. Each temporary O0 thread
reads one assigned research source independently and proposes questions that may deserve long-term
management as Expectation Units. Shell Synthesis later compares sources, resolves overlap, and
decides the final object level and grouping.

## Find candidate questions

A Candidate Unit is a question that will require continuing judgment, whose outcome could
materially change company earnings, cash flow, risk, valuation, or expectations about the company,
and that may become an independently updateable middle-level proposition.

Independent updateability is necessary but not sufficient for Candidate status. The question must
itself be a continuing investment expectation worth maintaining, rather than a lower-level object
made broader only because it is important.

For each forward-looking conclusion, tension, or unresolved issue in the primary source, ask:

1. What future judgment remains open?
2. Why would a change in that judgment matter to the company or its expectations?
3. What future evidence could update this judgment separately from adjacent questions?
4. Is the question broad enough to require several observations or realization mechanisms, yet
   specific enough to be researched and updated as one proposition?

The extraction target is the continuing question supported by the research. An already reported
result is evidence about a question; a raw metric or operating condition may later become State or
a Realization Factor. Promote a wider question when it already exists in the research as a
continuing investment problem with its own material outcome, maintainable State, several possible
realization mechanisms, and continuing independent revision path. The importance of an underlying
metric, resource constraint, operating condition, technical factor, or mechanism alone does not
create that wider proposition.

For example, “the latest quarter beat expectations” is a reported result. “Whether the new product
cycle can produce a financially material and durable contribution” is a Candidate Unit. A yield
measure alone is a variable or condition; whether technical and production capability can support
meaningful commercial share may be a Candidate Unit.

## Use the assigned inputs

The assigned C1, C3, C5, or Narrative report is the primary research basis. Future Nodes add future
event context, the Event Library adds recent factual context, and horizontal indicators add broader
company, industry, or market reference points. Use them to recognize or sharpen candidate
questions and their relevant horizon. Several inputs may support one candidate, and an auxiliary
item may support none.

For a C1, C3, or C5 branch, `narrative_run_id: null` is expected because Narrative Research has its
own independent Candidate branch. An unavailable Event Library or an empty horizontal collection
is an input status, not by itself a Candidate failure. Supplementary Data MCP research should stay
within the assigned source domain; a candidate's eventual valuation impact does not by itself turn
the research request into a valuation-data task.

For the C5 branch, use price, valuation, multiple, relative-performance, and market-implied evidence
to identify the underlying business, financial, industry, or duration expectation being priced. A
question about whether market data can reveal another expectation is a measurement or inference
task for later `MARKET_IMPLIED` State or runtime absorption analysis, rather than a Unit Candidate.

Narrative Research is useful for identifying what the market discusses or disputes; the same
materiality, middle-level, and independent-update tests determine whether its question enters the
candidate set.

## Candidate record

- `candidate_id`: a temporary local handle, `U1`, `U2`, `U3`, and so on.
- `candidate`: one sentence stating the middle-level expectation proposition.
- `reason`: why the question matters and has an independent future update path, rather than being
  only a reported event, a State-like variable, or a general risk.
- `references`: the principal research citations supporting the candidate.

Record only material coverage limitations that change how the Candidate set should be interpreted
in `warnings`. Do not repeat expected branch isolation, optional-input status, citation-validator
telemetry, or the operating rules of this skill as warnings. Unresolved citations remain
non-blocking under the workspace contract.

## Completion

Recall is the error preference. Preserve overlapping candidates when each is supported by the
assigned source; Synthesis will deduplicate or demote them. Discovery is complete when every
distinct material future question in the source has been considered and additional candidates
would mainly repeat an existing question or restate a result, variable, condition, or generic risk.
