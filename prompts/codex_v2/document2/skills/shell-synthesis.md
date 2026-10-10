# Shell Synthesis internal skill

## Purpose and mental model

Turn independently discovered economic subjects into a provisional research topology for Domain
Review. Make two separate decisions: which subjects deserve independent Unit-level maintenance,
and which retained subjects should share one bounded O1 research context.

A Unit is a durable economic subject worth forming and revising expectations about. A Shell is a
bounded shared research context. This stage establishes objects and research ownership; O1 later
develops their current State, Expectation Baseline, Realization Factors, materiality, and Potential
Gaps. A provisional object can be structurally sound while its future outcome remains uncertain.

## Use the available evidence

Start with `context.json.candidate_sets`, grouped by source perspective. Each record supplies
`name/scope/why_material/ref` plus a runtime-added `candidate_ref`. Read the sets together with
`future_nodes`, `entity_relations`, `horizontal_indicators`, and available `event_library.payload`.
Use `as_of` as the information boundary. Source perspectives inform judgment rather than define
separate architecture partitions.

The current context also supplies original reports in `global_research.reports` under `c1/c3/c5`
and an optional `narrative_research` object with status and payload. Consult relevant passages when
a consequential structural choice needs more context:

- similar candidates may identify different objects, or different framings may identify one;
- compressed scope or materiality obscures the proper object granularity;
- placement depends on a business system or actor network absent from the summary;
- a candidate appears to abstract its source too narrowly or too broadly.

When candidate records already support the choice, proceed. Original reports resolve topology
questions; do not rerun broad Candidate Discovery from them to ensure nothing was missed. If they
reveal a material omitted object, record it in `warnings` for Domain Review or Finalization rather
than inventing an input candidate or reference.

A missing source set does not mean that source found no relevant objects. Use present material and
carry forward source limitations when they materially affect the topology. Optional context absence
alone does not invalidate the draft.

## Interpret Candidate objects

Compare the economic subjects being maintained, rather than their wording, current direction, or
preferred validation events. Ask: if today's explanation or outlook changed, what would each record
still be asking the system to study?

One source may describe an object through demand, another through profitability, and another through
market pricing. Those framings can identify the same object, but their source roles do not establish
either sameness or difference. Compare their actual economic scope and company relevance.

Conversely, a shared causal chain or evidence source can support distinct objects. Important future
evidence can materially change the maintained view of one without mechanically requiring every
adjacent object to change together. Unique news feeds and entirely separate catalysts are not
necessary for independent maintenance.

For example, "AI基础设施需求", "数据中心现场电力市场", and "AI数据中心增长持续期" call for a comparison
of the actual demand base, business exposure, and scope behind those names. They could overlap,
describe a wider and narrower object, or warrant separate maintained views. Matching today's
validation path would not resolve that choice.

## Decide provisional Unit status

Retain a candidate when it identifies one coherent, durable subject whose future materially matters
to the company and whose outlook deserves separate maintenance. Judge four connected properties:
persistence beyond today's issue, material economic relevance, independent revisions, and a
granularity that accommodates several future drivers without absorbing the whole company thesis.

Make that structural judgment separately from current measurability. Whether a subject is a
distinct economic object worth maintaining is not the same question as whether current disclosures
provide standalone revenue, margin, volume, or other directly separable State data for it. Limited
measurement can constrain O1's current precision without making a broader aggregate object the
better identity. The subject must still have a distinguishable economic meaning and enough material
independent variation to justify separate maintenance.

Place a candidate in `unassigned_candidates` when it is better understood as a reported event,
State-like variable, realization mechanism, narrow occurrence, measurement task, or umbrella theme
too broad for one maintained object. Explain the actual mismatch with independent maintenance;
uncertainty or incomplete measurement alone is not that mismatch.

When records appear to describe the same eventual Unit, first distinguish their shared economic
subject from any materially different research responsibility carried by one record. Then retain
the strongest representative in the provisional topology: the record that most clearly captures
the durable subject, its scope, and economic importance.

Representative selection disposes of overlapping records; it does not by itself establish that
the representative's scope is semantically complete. Before placing an overlapping record in
`unassigned_candidates`, ask whether any material, non-duplicate part of what it says should remain
inside the eventual Unit or elsewhere in the topology. If that responsibility has no clear home,
retain the distinction provisionally or surface the specific scope gap for Review or Finalization.

Put genuinely overlapping records in `unassigned_candidates` with a reason naming the retained
object's full `candidate_ref` and explaining the overlap. Finalization can consolidate wording and
provenance into the final Unit. This check preserves material research responsibility, not every
detail or phrasing difference in the source records.

Keep every candidate's `candidate_ref/name/scope/why_material/ref` exactly as received in either
destination. Structural judgment does not authorize silently rewriting the branch's evidence.
If a candidate needs reframing that cannot be expressed by placement or disposition, describe the
specific issue in a warning for the next stages.

## Build shared research contexts

After identifying provisional Units, ask what persistent research they repeatedly reuse: business
and market background, core actors and counterparties, operating or industry knowledge, recurring
datasets and evidence sources, competitive or regulatory environments, and relationships needed
to interpret their economic significance.

Imagine O1 researching the proposed group over several months. Which background would repeatedly
be reconstructed if the Units were researched separately? Keeping that body of knowledge together
should improve depth and consistency. Different products, actors, datasets, mechanisms, or horizons
can remain Unit-specific depth within a shared core. A separate Shell is useful when those
differences amount to a substantially different research system, such that joint maintenance would
materially dilute depth or mix largely independent systems.

Assess the group as a whole. A Shell's coherence comes from a shared body of research directly useful
across its Units, not a chain of pairwise overlaps:

- **Causal connection can cross Shells.** Product demand, profitability, and financing can affect
  each other without needing the same detailed research owner.
- **Context overlap is not transitive.** A and B sharing important background, and B and C sharing
  other background, does not establish one common research context for A, B, and C.
- **Independent Units can share a Shell.** Separate updates do not require separate research owners.

A single-Unit Shell is valid but exceptional. First examine natural adjacent Shells: can the Unit
remain independently maintained there while reusing their main research context? Prefer that
placement when it preserves depth and meaningful distinctions. Keep a separate Shell when the
Unit's main research needs warrant a distinct context that those adjacent Shells cannot provide
without diluting research. This is a placement test, not a reason to discard the Unit or force it
into an unrelated domain.

Before accepting the topology, describe internally in one sentence the long-lived Research Owner
implied by each Shell: what it must keep understanding, researching, and updating. Compare those
owners across the whole set. Repeatedly rebuilding substantially the same core suggests fragmented
ownership; one owner spanning largely unrelated business systems suggests over-merging. Compare
the underlying research work, not merely the wording of the owner descriptions, and revisit the
affected grouping while preserving valid Unit identities and material research responsibilities.

For any proposed Shell with 6 or more Units, perform a Boundary Challenge. First identify whether
one common research core still organizes the main work and lets O1 study the included Units deeply.
If distinct research centers emerge, compare the existing grouping with a natural partition:
would separation create meaningful depth and clarity, or mostly reconstruct the same core to study
different local outcomes? Retain the larger Shell when joint research remains coherent and useful;
separate genuinely different systems when carrying them together would weaken the research. Six
triggers review, not a Unit ceiling, a preferred size, or a presumption that a split must be found.

Test a plausible partition in both directions. Would separation let each O1 deepen a distinct
research system, or make both rebuild essentially the same background? Would combination reuse
that background, or burden one owner with largely separate systems? Each Shell starts a full O1
research line and shapes downstream D3 expansion, so weigh the gain in depth against duplicated
research and fragmented downstream coverage. Choose coherent research ownership, not the fewest
or most Shells. These are internal reasoning checks, not additional output fields or research
instructions to place in Shell definitions.

## Express provisional Shells

For each nonempty `provisional_shells` item, use the supplied schema's fields:

- `shell_temp_id`: a distinct temporary handle, such as `S1`, for review to locate this draft.
- `name`: a stable, noun-based business research domain, such as "计算产品市场" or
  "数据中心电力市场". Identify the research domain rather than summarize a common outcome.
- `scope`: the economic subjects, activities, and market system covered by this context. Describe
  the shared research domain; the Units need not jointly answer a single overarching question.
- `boundary`: the division of research ownership with adjacent domains. For example, product and
  end-market research may sit here while external Foundry manufacturing and company financing sit
  elsewhere. This is not a restriction on cross-Shell influence, evidence use, or future mechanisms.
- `ref`: principal references supporting the domain and boundary.
- `candidate_units`: the retained input candidate records, including their full `candidate_ref`.

Names, scope, and boundaries identify what economic subjects are maintained and where research depth
belongs. They leave direction, success conditions, complete transmission chains, evidence
interpretation, confirmation standards, and modeling procedures to downstream research. A useful
evidence caveat or anti-double-counting rule can inform the structural choice without becoming part
of the Shell or Unit definition.

A domain such as "数据中心电力市场" need not become a question about demand, qualification,
financing, and delivery jointly converting into accepted capacity.

## Output discipline and completion

Return exactly `provisional_shells`, `unassigned_candidates`, and `warnings` as defined in
`output_schema.json`. Every unassigned record preserves the same five candidate fields and adds
`reason`.

Each input `candidate_ref` must appear exactly once, either in one Shell's `candidate_units` or in
`unassigned_candidates`. The handle is `<UPPERCASE_SOURCE>:<name>`, such as
`C1:AI基础设施需求`; preserve the supplied value even when another source has the same name.
Semantic consolidation does not create a merged handle or allow provenance to disappear.
This stage adds no final Unit IDs, horizons, research detail, or extra disposition fields.

Warnings carry material unresolved context, source coverage failures, important omitted objects, or
specific reframing needs that the next stages should examine. Ordinary cross-source overlap,
optional-input absence, unresolved citations, and a narration of the analysis are not warnings.

Finish with four checks:

1. **Candidate and semantic accounting:** Every input record has one destination, with its text and
   references preserved and concrete reasons for unassigned records. For overlap dispositions,
   confirm that any materially distinct research responsibility in the unassigned record is either
   represented in the retained topology or explicitly surfaced for the next stage.
2. **Unit quality:** Retained subjects are durable, material, independently maintainable objects
   at useful granularity, rather than events, mechanisms, or company-wide themes.
3. **Shell coherence:** Each Shell gives one O1 a recognizable shared body of persistent research
   in which all included subjects can be studied deeply. Complete the 6+ Unit Boundary Challenge
   where applicable.
4. **Boundary quality:** Compare the proposed research owners across the whole topology. Single-Unit
   Shells have a substantive reason for separation; shared research has not been fragmented, and
   largely independent systems have not been combined merely to reduce Shell count.

The draft is ready when these structural choices are defensible from available material and clear
enough for Domain Review to challenge. Further work is useful when it could change object identity,
retention, or research ownership, rather than merely increase confidence in an already resolved
choice.
