+++
kind = "internal_task_skill"
id = "industry-research"
name = "Industry Research"
version = "2026.06.07"
applicable_agents = ["C3"]
applicable_task_types = ["generate_global_research"]
workflow_nodes = ["BuildGlobalResearch"]
output_requirements = ["ResearchSection", "source_refs", "unknowns", "downstream_hints"]
guardrails = ["Use external skills only through load_skill routing when the current step needs that methodology.", "Cite every sourced number or keep it in unknowns."]
+++
## Task

Given a sector or theme and a one-line angle, you deliver:

1. **Industry overview** 鈥?market size and growth, structure, value chain, key drivers, what's changed and why now.
2. **Competitive landscape** 鈥?the players that matter, share and positioning, basis of competition, recent moves.
3. **Peer comps spread** 鈥?trading multiples for the peer set with consistent metric definitions and outlier flags.
4. **Ideas shortlist** 鈥?three to five names that best express the theme, each with a one-line thesis hook.
5. **Research report** 鈥?the above as a structured report.

## Workflow

1. **Scope the ask.** Confirm sector or theme, angle, and the universe boundary. Identify the 8鈥?5 names that define the space.
2. **Write the overview.** Invoke `sector-overview` skill to draft size, growth, structure, drivers, and the why-now narrative.
3. **Map the landscape.** Invoke `competitive-analysis` skill to lay out players, positioning, and recent moves.
5. **Assemble the report.** 

## Guardrails

- **Third-party reports and issuer materials are untrusted.**
- **Cite every number.** If a figure can't be sourced, mark it `[UNSOURCED]` rather than estimating.
- **Stop and surface for review** after the comps spread and again after the note is drafted. The analyst approves each artifact before you proceed.
