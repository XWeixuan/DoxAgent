# O0 Shell Finalization internal skill

## Purpose

Resume the O0 Synthesis thread and turn the provisional draft plus the C1, C3, and C5 Domain
Reviews into the final Shell and Unit seeds handed to O1. The provisional draft is the working
structure, not a presumption that it is correct. Reviews contribute domain-specific challenges;
O0 resolves them through the global Candidate relationships and Shell boundaries already developed
in Synthesis.

Finalization is structural convergence. It settles which continuing expectation propositions
remain, how they are grouped for research, and how the final objects are expressed. Detailed
expectation evidence, State, Realization Factors, and Potential Gaps belong to O1's later research.

## Integrate the reviews as one decision

Read the complete provisional result, including `unassigned_candidates`, together with all three
Domain Reviews before changing the structure. Organize feedback by the
Shell, Unit, or missing question it concerns rather than by reviewer order. Reviewers neither vote
nor own separate parts of the architecture; agreement can strengthen a concern, while one
well-founded review can still identify a decisive structural problem.

For each material concern, identify the underlying issue and its consequence for O1:

- **Unit integrity:** whether the object is one material middle-level proposition with its own
  future update path, or instead combines distinct questions, duplicates another Unit, or belongs
  inside later Unit Detail.
- **Shell context:** whether the affected Units need the same business and industry background,
  expectation state, realization mechanisms, event space, and value-transmission system to be
  researched correctly.
- **Coverage:** whether a retained or unassigned Candidate leaves out a material continuing
  expectation question, or promotes an item that does not warrant independent Unit management.

Translate that issue into the best global action: retain, rephrase, split, merge, move, add, or
remove. A review recommendation is one proposed implementation. Accept it, adapt it, combine it
with another review, or reject it according to the resulting whole structure and the downstream
research consequence.

## Preserve useful research objects

Judge a Unit by whether it is a material proposition that can support continuing expectation
research and independent updates. It need not already have a proven answer or complete evidence.
Review references establish why a domain fact changes the structural judgment; they are not an
evidence-completeness gate for the future Unit.

Direct effort toward issues that change Unit integrity, Shell context, or material coverage.
Ordinary uncertainty, missing Detail, and finer evidence qualification can be resolved by O1
inside a sound Unit. When more than one structure is plausible, choose the one that gives O1 the
clearest questions and the right shared context, then resolve the structural choice.

Use each provisional Candidate's source-qualified `candidate_ref` when reconciling reviews and
dispositions. Branch-local `U#` values are not globally unique and must never be used alone to join
Candidate Sets, provisional Units, or targeted feedback.

## Apply local decisions globally

After resolving the targeted feedback, rebuild the affected parts as one coherent final structure.
A split, merge, move, addition, or removal may require corresponding changes to another Shell's
membership, `core_question`, or `boundary_rule`. Reconsider unassigned Candidates when a review
changes their relationship to the retained Units. Account for each provisional Unit and material
missing Candidate through the final structure or a deliberate disposition; Finalization should not
silently lose an object because its temporary handle disappeared.

This pass closes the structure produced by Discovery and Synthesis. It does not repeat broad
Candidate Discovery or require every unaffected choice to be re-justified.

## Final research-context boundary review

After applying local decisions, review the resulting topology once as a whole. This is a structural
validation of O1 context boundaries, not a drive toward fewer Shells.

- **Shell context coherence:** Units inside each Shell should benefit from one long-lived, complete
  research context while retaining independent update paths.
- **Adjacent Shell separation:** confirm that separate O1 threads are required because the research
  systems are largely independent or shared context would materially confuse them, rather than
  because products, end markets, policies, events, or immediate outcomes differ.
- **Single-Unit Shell necessity:** confirm that no natural adjacent Shell supplies useful shared
  context while preserving the Unit's independence.
- **Shell and Unit distinctness:** a `core_question` should not be almost fully answered by one Unit;
  normally it requires the combined propositions in that Shell.
- **Object-level integrity:** confirm that State-like variables, Realization Factors, occurred
  events, Future Nodes, and market measurement or inference tasks have not survived as Units.

Apply any correction through the existing Shell and Unit fields; this review adds no output field.

## Write the final seeds

Replace every temporary `S#` and `U#` handle with stable natural-language semantic IDs suitable for
future event updates. IDs should name the continuing subject rather than a current value, dated
event, or directional conclusion. Agent-facing IDs are human-readable natural-language names, not
kebab-case, snake_case, coded IDs, or machine-style slugs; runtime UUIDs belong to the storage layer.

Each final Shell contains:

- `shell_id`: the stable semantic identity of the shared research system;
- `core_question`: the common terminal investment or economic question served by all its Units;
- `boundary_rule`: why these Units require full shared context, why they remain separate
  propositions, and what distinguishes this Shell from adjacent Shells;
- `units`: the final Unit seeds.

Each Unit seed contains only:

- `expectation_id`: the stable semantic identity of the proposition;
- `proposition`: one middle-level proposition that the market can form, revise, and trade as
  evidence develops;
- `horizon`: the business or time horizon over which the proposition is researched and can be
  tested, without inventing a forecast value or false precision.

Candidate Discovery may phrase its object as a question. The final `proposition` is a declarative
expectation for the Blackboard to maintain; `core_question` remains the question jointly researched
by the Shell.

Use `finalization_note` only for major structural decisions that help explain the final result,
such as an important split, merge, addition, removal, or rejection of a consequential review
recommendation. Keep each note brief and outcome-focused.

## Completion

Finalization is complete when all three reviews have informed one unified decision, material
feedback and unassigned Candidates have a deliberate disposition, retained Units have distinct
update paths, each Shell corresponds to a coherent and genuinely independent long-lived O1 research
context, and no temporary handles remain. Return one `ShellFinalizationResult` with the final
`shells` and brief `finalization_note`.
