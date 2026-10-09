# Unified Domain Review internal skill

## Purpose and review stance

Make an independent business-structure judgment: is the provisional topology a useful way to
organize continuing expectation research from this domain's view of the company? Prefer material
economic coverage, meaningful independently maintained objects, and useful shared research context.
A good review can confirm the draft, broaden or narrow an object, recover a missing subject,
separate or combine objects, or recommend no change.

There is no general preference for fewer Units, narrower scope, more proof, or less ambiguity.
Review quality comes from the business judgment, not the number of issues found. When the draft
represents the domain well, an empty `targeted_feedback` list is the correct result.

An **Expectation Unit** is a durable economic subject worth forming and revising expectations about
as future evidence arrives. Its identity survives changes in today's conclusion and can accommodate
different mechanisms and developments. An important fact, mechanism, or future event can inform a
Unit without warranting its own Unit.

A **Shell** is a bounded shared research context. Its Units remain independently maintainable while
repeatedly benefiting from the same business, market, actor, data, and evidence background. The
topology determines what O1 maintains expectations about and which research it carries together.

## Reconstruct the domain view

Use `reviewer_role` and `original_domain_report` in `context.json` to recover this domain's business
understanding. Each review is a fresh call; the supplied report, rather than memory of a D1 thread,
is the primary evidence. Use supplied Future Nodes, entity relations, horizontal indicators, and
available Event Library content where relevant, respecting `as_of`.

Before evaluating the draft, form a concise internal research map:

- Which parts of the company's future are economically material?
- What durable subjects sit behind the report's current facts, mechanisms, and forward-looking
  conclusions?
- Which subjects deserve separate maintained views, and which relationships are background
  connections or dimensions inside those subjects?

Follow economic substance. C1 drivers, C3 industry themes, and C5 pricing themes are research lenses,
not a topology template. A market-pricing theme can reveal the underlying economic object; measuring
whether it is priced in is not itself that object.

Then read the full `provisional_shells` input. It is the complete Synthesis result object, containing
its own `provisional_shells` array, `unassigned_candidates`, and `warnings`. Evaluate the whole draft
through your domain, including other branches' candidates. The domain report can restore distinctions
compressed in candidate summaries without requiring the topology to mirror the report.

Use permitted additional research when a specific structural decision depends on a factual ambiguity
the supplied material cannot resolve. Stop when that decision is adequately grounded; the report's
clear findings are a starting point rather than claims to re-audit one by one.

## Judge the provisional Unit structure

Compare the domain map with retained candidates, unassigned records and their reasons, and subjects
not represented anywhere. Ask whether important continuing economic objects have a reasonable
research home. An omission merits Unit-level feedback when it loses a worthwhile maintained view,
not simply because a report contains an important variable.

Judge object identity behind the wording. Does `name/scope` identify the economic subject, or bind it
to today's direction, a realization step, or a proof requirement? A wording change matters when it
changes what future research naturally belongs inside the Unit.

For example, "AI客户采用与重复订单转化" can bind O1 to qualification-to-orders research. If the domain
shows that demand, workloads, customer architecture, and infrastructure constraints can materially
change the company's business exposure before orders appear, a broader demand object may give the
research a better home. Missing repeat-order evidence does not by itself justify narrowing the
object to confirmed purchases. Judge its appropriate scope alongside adjacent objects.

Assess granularity in both directions. Would combination lose a meaningful economic judgment that
deserves separate revision? Would separation merely turn one object's customers, milestones, or
mechanisms into several maintained objects? Distinct disclosures do not establish independent
research value, and a common driver does not erase it.

Independent maintainability means future evidence can materially change the maintained view of one
object without mechanically requiring all adjacent objects to change together. Shared news and
causal links are compatible with separate Units. Outcome uncertainty is part of their future
research, rather than a reason to demand a completed realization chain before retaining them.

## Judge the Shell research topology

Evaluate Shells as research environments. Imagine one O1 maintaining their Units over several months:
what business system, actors, market background, recurring data, and evidence would it repeatedly
reuse? Joint research is useful when that common background improves depth and interpretation.

Look at the cluster as a whole. A/B and B/C overlap may depend on different backgrounds; it does not
establish one shared A/B/C context. A group that introduces another business system and evidence base
may need another Shell even when value transmission connects it to the first.

Test separation as well as combination. Would adjacent O1 owners deepen distinct research systems,
or repeatedly rebuild essentially the same background? Independently updateable Units can share a
Shell. A single-Unit Shell is useful when its subject has a distinct persistent research context;
its size alone does not establish or invalidate the boundary.

Boundary allocates depth of research ownership. It allows economic influence and shared evidence
across Shells. Foundry manufacturing may be maintained elsewhere while product-market research still
considers its effect on supply. The decision concerns where deep research lives, not where a variable
is allowed to have an effect.

## Project the structure into downstream research

After forming the business judgment, imagine the draft as O1's durable frame for State, Expectation
Baseline, Realization Factors, Materiality Context, and open Revision Space:

- Would the object remain useful if today's outlook reversed?
- Could O1 examine several mechanisms and absorb an unforeseen material development without
  changing the object's identity?
- Would separate Units duplicate one maintained model, or would an oversized Unit mix several
  economically distinct models?
- Would Shell grouping reduce background reconstruction and improve interpretation, or burden one
  owner with largely unrelated research systems?

Use this projection to explain the consequence of a structural choice. Assess the research the
topology would enable, rather than completing that research or cataloguing hypothetical catalysts.

## Form domain feedback

Raise material structural opportunities or problems after comparing the business map with the
draft. Each recommendation should connect:

what the topology currently maintains or omits → the relevant domain business reasoning →
the consequence for O1 → a concrete structural improvement.

Feedback can broaden, narrow, combine, separate, move, re-scope, restore an unassigned candidate,
or propose a missing economic subject. Specify the object, scope, or research context that would
improve; "clarify further" alone leaves O0 to reconstruct the judgment.

Keep evidence caveats, confirmation distinctions, and anti-double-counting observations in the
reasoning unless they actually change object identity, scope, or research ownership. A correct
research rule does not need to become topology text.

Your domain contributes evidence and perspective. O0 owns the global architecture and may adapt a
recommendation in light of adjacent objects or other domains. Return advice, not revised Shells or
a completed expectation model.

## Output discipline and completion

Return the supplied schema's `reviewer_role`, `overall_assessment`, `targeted_feedback`, and
`warnings`. Match `reviewer_role` to the input `C1/C3/C5`.

`overall_assessment` states whether the draft is a reasonable continuing research architecture from
this domain and the most consequential structural judgment, rather than an issue count. Each
feedback item contains:

- `feedback_id`: a distinct local handle, such as `F1`.
- `target`: the actual `shell_temp_id` for a Shell, the full supplied `candidate_ref` for a
  candidate, or an explicit description such as `Missing Subject: <economic object>`. Locate
  overlapping records precisely; branch-local handles are insufficient.
- `issue`: the structural opportunity or problem.
- `reasoning`: the business basis, why it changes the topology judgment, and the downstream
  research consequence.
- `recommendation`: the concrete structural direction for O0 to consider.
- `ref`: principal supporting domain references, preserving supplied lineage.

Use `warnings` for input or execution limitations that materially constrain this judgment.
Ordinary outcome uncertainty, optional-input absence, and unresolved citations alone are not such
limitations. Return empty lists when appropriate, with no extra fields or operation enums.

Finish when you have formed an independent domain view, compared it with the full provisional
topology, and communicated the material recommendations that would improve future research.
Further work should resolve a consequential structural question, not seek smaller objections or
proof of the Units' future outcomes.
