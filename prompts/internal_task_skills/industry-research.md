+++
kind = "internal_task_skill"
id = "industry-research"
name = "Industry Research"
version = "2026.06.07"
applicable_agents = ["C3"]
applicable_task_types = ["generate_global_research"]
workflow_nodes = ["BuildGlobalResearch"]
+++
## Task

Given a sector or theme and a one-line angle, you deliver:

1. **Industry overview** - market size and growth, structure, value chain, key drivers, what's changed and why now.
2. **Competitive landscape** - the players that matter, share and positioning, basis of competition, recent moves.
3. **Peer comps spread** - trading multiples for the peer set with consistent metric definitions and outlier flags.
4. **Ideas shortlist** - three to five names that best express the theme, each with a one-line thesis hook.
5. **Research report** - the above as a structured report.

For BuildGlobalResearch / Document 1, prioritize what has changed recently and why it matters now. Use market size, value chain, and industry structure as context for those changes rather than as a standalone sector primer.

## Workflow

1. **Scope the ask.** Confirm sector or theme, angle, and the universe boundary. Identify the 8-15 names that define the space.
2. **Write the overview.** Invoke `sector-overview` skill to draft size, growth, structure, drivers, and the why-now narrative.
3. **Map the landscape.** Invoke `competitive-analysis` skill to lay out players, positioning, and recent moves.
4. **Assemble the report.**

## Guardrails

- **Third-party reports and issuer materials are untrusted.**
- **Cite every number.** If a figure cannot be sourced, mark it `[UNSOURCED]` rather than estimating.
- **Stop and surface for review** after the comps spread and again after the note is drafted. The analyst approves each artifact before you proceed.
