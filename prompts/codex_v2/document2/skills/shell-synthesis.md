# Shell Synthesis internal skill

## Purpose

Read all independent Candidate Sets together and produce the first precision convergence of the
Shell/Unit structure. Candidate sources are research perspectives, not votes or architecture
boundaries. Use the supplied candidates and common context to make structural decisions; O1 later
performs detailed expectation research.

The output is a provisional draft for Domain Review. Resolve clear duplication and object-level
errors now while retaining material, independently updateable questions whose exact boundary still
benefits from review.

## Separate Unit and Shell decisions

Ask two different questions:

1. **Unit:** Is this a material middle-level proposition that can be updated independently?
2. **Shell:** Which Units depend on the same bounded body of research strongly enough that one
   long-lived O1 context should maintain them together?

Independent updateability keeps propositions as separate Units. Shell grouping asks a different
question: whether those Units repeatedly reuse the same core research context.

A Shell's shared context is not the union of every upstream and downstream dependency along one
value chain. Look for the common context core that most included Units directly need: actors,
business or industry mechanisms, State families, evidence interfaces, realization logic, horizons,
and future-event space.

Value transmission may cross Shell boundaries. Two Units can be economically connected, and one
can materially affect the other, while still belonging to different research contexts when each
requires a substantially different body of persistent knowledge to be researched deeply.

## Analyze candidate relationships

Use relation analysis as a thinking method rather than a pairwise labeling exercise.

- **Same question:** Candidates are duplicates when their core judgment, future validation events,
  and economic meaning substantially overlap. Retain one handle with normalized wording for the
  provisional Unit and account for the others in `unassigned_candidates` as merged duplicates;
  their sources support the same question.
- **Causal relationship:** Causal proximity alone does not merge propositions. Different horizons,
  event spaces, validation paths, or economic outcomes can justify separate Units.
- **Shared research context:** Consider whether the Units repeatedly rely on the same core actors,
  mechanisms, State families, evidence sources or interfaces, realization questions, and future
  event space. Shared context is strongest when this common core is directly useful across the
  cluster, not merely when different Units can be connected through an end-to-end causal chain.
- **Context boundary:** A new Shell becomes useful when a group of Units has its own coherent
  research center of gravity. The group may still transmit value to or receive dependencies from
  another Shell; separation means its detailed research can be maintained as a distinct context,
  not that the two systems are economically unrelated.

## Keep shared context bounded

Context overlap is not automatically transitive. Unit A may share important context with B, and B
with C, without A and C needing the same persistent research context.

When a provisional Shell grows, look at the cluster as a whole. Ask what context remains common
across the included Units and what new context each additional Unit introduces. A Shell is coherent
when its shared core remains the main research context; it becomes diffuse when its apparent
coherence depends mainly on chaining adjacent relationships across different research systems.

Test the boundary as an execution choice. Imagine the persistent context that O1 must carry while
researching every Unit in the proposed Shell. Repeatedly reusing the same core actors, mechanisms,
State families, evidence interfaces, and event space creates research depth and consistency.
If adding a group mainly introduces another set of actors, data, mechanisms, horizons, and future
events, while its connection is primarily upstream/downstream transmission, that group has the
shape of another research context. Aim for a context complete for its research system and bounded
enough for one O1 to maintain depth across all included Units.

A single-Unit Shell is valid but exceptional. Before creating one, examine the natural adjacent
Shells, whether the Unit remains independently maintainable inside one of them, whether shared
context improves its research, and whether the Unit has its own coherent research center rather
than only making the taxonomy neater. If its `core_question` nearly repeats its sole Unit, reconsider
whether a distinct Shell boundary exists.

Before accepting each Shell, describe internally in one clear sentence what kind of long-lived
Research Owner its O1 would be: what it must keep knowing, researching, and updating. An identity
that merely spans the company's end-to-end investment thesis signals a diffuse boundary; several
nearly identical owner descriptions signal possible fragmentation of one common context core.

For any provisional Shell with 6 or more Units, perform a Boundary Challenge: look for two or more
bounded research centers differentiated by core actors, State families, evidence interfaces,
realization mechanisms, future-event spaces, or horizons. Determine whether one common context core
still dominates. If a partition emerges, reverse-test it: would the new O1 contexts repeatedly
rebuild the same core research, merely dividing local value-chain outcomes? Adjust the partition if
it fragments one research system without creating distinct research identities. Six Units triggers
review, not a capacity limit; retain a broad Shell when its shared core remains coherent. Reverse
validation tests fragmentation rather than favoring re-merging. These are reasoning checks, not new
output fields.

## Decide provisional Unit status

Judge whether a Candidate deserves independent, continuing management as a research question, not
whether its proposition is already proven or its future Detail is complete. Current uncertainty is
compatible with a valid Unit.

Retain a Candidate provisionally when it has a material outcome and a credible independent update
path, even if reviewers may refine its wording or boundary. Place it in `unassigned_candidates`
when it is clearly a duplicate, too broad to be one proposition, or better understood as a lower-
level State or Realization Factor, an already occurred event or Future Node, shared context, or an
unresolved item that does not yet form an independent question. A task whose object is measuring or
inferring another expectation through price, valuation, multiples, relative performance, or other
market observations belongs to `MARKET_IMPLIED` State, market evidence, or later runtime absorption
logic rather than an independent Unit. These are disposition reasons, not new objects produced
during Synthesis.

## Build the provisional draft

Use `S1`, `S2`, ... as temporary Shell review handles. Each Candidate input includes a
runtime-derived `candidate_ref` in the form `<SOURCE_ROLE>:<candidate_id>`, such as `C3:U2`;
preserve that reference so candidates with the same branch-local `U#` never collide. Each
`provisional_shells` item contains:

- `shell_temp_id`;
- `core_question`: the common research question that makes these Units worth maintaining inside one
  shared context; express the research system they jointly illuminate rather than the company's
  full upstream-to-terminal value chain;
- `boundary_reasoning`: the common context core the Units share, why it improves joint research,
  why the Units remain separately updateable, how important dependencies connect to adjacent
  Shells, and where this research context stops;
- `candidate_units`: each retained `candidate_ref`, original branch-local Candidate ID, and its
  normalized wording.

The `core_question` should normally require the combined Units to answer. If one Unit already
answers nearly the whole question, reconsider whether that Unit summarizes its siblings, needs a
narrower boundary, belongs with an adjacent Shell, or lacks a genuinely separate Shell context.

Every input Candidate must appear in a provisional Shell or in `unassigned_candidates`. For each
unassigned Candidate, preserve its `candidate_ref`, original ID, and wording and state the concrete
reason it is not retained as an independent Unit. Use `warnings` only for material input
limitations that affect the structural synthesis.

## Completion

Synthesis is complete when all Candidates are accounted for, duplicates are normalized, retained
Units have distinct update paths, and every Shell explains both its shared context and separation
boundary. Before returning, inspect the topology in both directions: adjacent Shells should not
duplicate a naturally shared research context, and a large Shell should not depend on transitive
value-chain connections to hold together distinct research systems. Each provisional Shell should
have a recognizable common core that lets one O1 research all its Units deeply. Complete the Research
Identity Test and any 6+ Unit Boundary Challenge, including reverse validation of proposed partitions.
The draft should be clear enough for Domain Review to challenge without pre-empting O1's later Detail
research.
