# Prompt And Skill Separation

## Summary

DoxAgent now separates prompt injection into three categories:

- System / Agent Prompt Block: system, role, and workflow instructions.
- Internal Task Skill: DoxAgent-owned SOPs for a specific agent task or node.
- External Skill Package: optional migrated capabilities from external projects.

Prompt resources are stored as reviewable Markdown files under `prompts/` with
TOML front matter. They are loaded by `PromptRegistry` and assembled by
`PromptAssembler`; external packages remain visible through the compatibility
`SkillRegistry`.

## Registry Boundaries

- `PromptRegistry` owns all prompt resources and reads `prompts/**/*.md`.
- `PromptInjectionPolicy` selects prompt blocks, internal task skills, and
  external packages for each `AgentTask`.
- `SkillRegistry` is a compatibility layer for external skill packages only.
  It no longer owns system prompts or DoxAgent internal task SOPs.
- `AgentTask.prompt_bundle` carries prompt resources separately from
  `AgentTask.skill_bundle`.

## Runtime Assembly

`ModelGatewayAgentRunner` now assembles prompts in this order:

1. System / agent / workflow prompt blocks.
2. Internal task skills.
3. External skill package summaries.
4. Bounded context snapshot.
5. Tool results.
6. Required output schema and hard rules.

The runner records selected prompt ids, internal task skill ids, external skill
package ids, and versions in `AgentResult.payload` for audit visibility.

## Non-goals

- No Supabase prompt persistence.
- No UI/editor for prompt resources.
- No business-logic changes to existing agents.
- No removal of adapter `skills` / `skill_versions` compatibility fields.
