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
2. **Shell:** Which Units must share full research context to be understood and researched
   correctly?

Independent updateability keeps propositions as separate Units; it does not require separate
Shells. Upstream and downstream propositions may remain distinct Units in one Shell when they share
the same business system and value-transmission context. Shell boundaries answer which Units one O1
should research inside a shared, persistent context. Establish a separate Shell when the two
research systems can largely be studied independently and combining them would make unrelated
context persist or confuse State, Factor, event, or transmission interpretation. Different products,
end markets, event paths, policies, or immediate outcomes are not sufficient by themselves.

## Analyze candidate relationships

Use relation analysis as a thinking method rather than a pairwise labeling exercise.

- **Same question:** Candidates are duplicates when their core judgment, future validation events,
  and economic meaning substantially overlap. Retain one handle with normalized wording for the
  provisional Unit and account for the others in `unassigned_candidates` as merged duplicates;
  their sources support the same question.
- **Causal relationship:** Causal proximity alone does not merge propositions. Different horizons,
  event spaces, validation paths, or economic outcomes can justify separate Units.
- **Shared context:** Units belong in one Shell when researching any of them correctly requires much
  of the same business and industry background, external actors, expectation state, realization
  mechanisms, future event space, and value-transmission path.
- **Independent system:** Questions affecting the same ticker may belong to different Shells when
  each can be researched without the other's core context and sharing one persistent context would
  materially mix unrelated systems.

Test the boundary as an execution choice. If two proposed O1 threads would repeatedly load and
reconstruct much of the same business context, actors, State, realization mechanisms, future events,
and value-transmission chain, shared Shell context is a strong fit. If one O1 would instead carry
substantial unrelated context that obscures two otherwise independent research systems, separation
is a strong fit.

A single-Unit Shell is valid but exceptional. Before creating one, examine the natural adjacent
Shells, whether the Unit remains independently maintainable inside one of them, whether shared
context improves its research, and whether separation removes genuinely unrelated context rather
than only making the taxonomy neater. If its `core_question` nearly repeats its sole Unit, reconsider
whether an independent Shell boundary exists.

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
- `core_question`: the common terminal investment question served by the Shell;
- `boundary_reasoning`: the shared economic and value-transmission system, why full context is
  shared, why the included propositions remain separate Units, and what belongs outside;
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
boundary. Before returning, check the topology for context fragmentation: adjacent Shells should not
force separate O1 threads to repeatedly reconstruct substantially the same research system. The
draft should be clear enough for Domain Review to challenge without pre-empting O1's later Detail
research.
