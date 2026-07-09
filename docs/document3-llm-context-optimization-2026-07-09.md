# Document 3 LLM Context Optimization

Date: 2026-07-09

This note records the current Document 3 LLM request shape after the Document3-only
prompt/context compaction pass. It is intended as a reference before applying the
same pattern to Document2 or broader workflow context.

## Real ReAct Request Top-Level Fields

Each real AgentRunner ReAct loop sends one JSON user payload with these top-level
fields:

- `react_protocol`: ReAct limits and expected response schema.
- `task`: task envelope, including `task_id`, `ticker`, `agent_name`, `task_type`,
  `workflow_node`, `required_output_schema`, `permissions`, and compacted
  `input_context`.
- `tool_call_policy`: allowed/required tool guidance.
- `output_contract`: required final payload examples and schema-critical rules.
- `context_snapshot`: optional. For Document3 this is omitted when there are no
  scoped belief-state documents.
- `available_tools`: model-visible tool descriptors.
- `available_skills`: model-visible external skill catalog items.
- `loaded_skills`: skills already loaded in the ReAct task.
- `plan`: only the latest `plan_update`, not cumulative historical plans.
- `task_ledger`: cumulative public task ledger updates.
- `compacted_evidence_summary`: ReAct tool evidence compaction summaries.
- `market_evidence_snapshot`: latest market evidence snapshot from tool results.
- `recent_trajectory`: recent ReAct loop entries.
- `scratchpad_warnings`: recent runtime warnings.

The non-ReAct `PromptAssembler` path uses the same Document3 snapshot visibility
rules. Its payload contains `task_summary`, optional `context_snapshot`, and
`tool_results`.

## Document3 `task.input_context`

All Document3 nodes now remove these base workflow-history/index fields from
`AgentTask.input_context`:

- `completed_nodes`
- `stable_document_types`
- `belief_state_summary`
- `working_memory_summary`
- `unresolved_objections`
- `blocking_delegations`

Document3 generate nodes also remove base `pending_patch_ids` and
`pending_patches`.

### GenerateKnownEvents

`input_context` keeps:

- `global_research_context`

`input_context` removes the top-level duplicate `document1_context_pack`. The
same pack remains available at:

- `global_research_context.document1_context_pack`

Main document正文 comes from `context_snapshot.belief_state_documents`:

- `global_research`
- `expectation_unit`

### GenerateMonitoringConfig

`input_context` keeps:

- `global_research_context`

Top-level `document1_context_pack` is removed; callers should use
`global_research_context.document1_context_pack`.

Main document正文 comes from `context_snapshot.belief_state_documents`:

- `global_research`
- `expectation_unit`
- `known_events`

### ReviewMonitoringConfig

`input_context` is scoped to review data:

- `document3_pending_patch`
- `review_scope`
- `review_instruction`

It does not include global pending/history fields, full belief-state indexes,
or `global_research_context`.

No `context_snapshot` is injected unless the builder later defines a scoped
document bucket for this review node.

### ResolveMonitoringConfig

`input_context` is scoped to resolver data:

- `document3_pending_patch`
- `document3_review_objections`

It does not depend on global `unresolved_objections`; only the already-filtered
Document3 objections are visible.

No `context_snapshot` is injected unless the builder later defines a scoped
document bucket for this resolve node.

### GenerateMonitoringPolicy

`input_context` keeps:

- `global_research_context`

Top-level `document1_context_pack` is removed; callers should use
`global_research_context.document1_context_pack`.

Main document正文 comes from `context_snapshot.belief_state_documents`:

- `global_research`
- `expectation_unit`
- `known_events`
- `monitoring_config`

### ReviewMonitoringPolicy

`input_context` is scoped to review data:

- `document3_pending_patch`
- `review_scope`
- `review_instruction`
- `monitoring_config_brief`

It does not include global pending/history fields, full belief-state indexes,
or `global_research_context`.

No `context_snapshot` is injected unless the builder later defines a scoped
document bucket for this review node.

### ResolveMonitoringPolicy

`input_context` is scoped to resolver data:

- `document3_pending_patch`
- `document3_review_objections`
- `monitoring_config_brief`

It does not depend on global `unresolved_objections`; only the already-filtered
Document3 objections are visible.

No `context_snapshot` is injected unless the builder later defines a scoped
document bucket for this resolve node.

## Document3 `context_snapshot`

The internal `AgentContextSnapshot` model still uses the generic field name
`belief_state_summary`, but the LLM-visible payload is transformed for
Document3:

```json
{
  "belief_state_documents": {
    "global_research": ["full scoped document payloads"],
    "expectation_unit": ["full scoped document payloads"],
    "known_events": ["full scoped document payloads"],
    "monitoring_config": ["full scoped document payloads"]
  }
}
```

For Document3, these duplicated or low-value snapshot fields are no longer sent
to the model:

- `ticker`
- `agent_name`
- `task_type`
- `readable_scopes`
- `run_id`
- `workflow_state`
- `task_input`
- `prompt_summaries`
- `skill_summaries`
- `belief_state_summary`
- empty `working_memory_summary`
- `evidence_refs`
- `unresolved_objections`
- `blocking_delegations`

If a Document3 review/resolve node has no scoped document bucket, the visible
`context_snapshot` key is omitted.

## Output Contract Adjustments

The `output_contract` remains present. Only Document3-facing schema examples were
compacted:

- `KnownEventsDocument`: rules were reduced to durable fact/date/source essentials,
  and an `event_shape` sample now explicitly includes complete `source:
  EvidenceRef` fields.
- `MonitoringConfigDocument`: API-shape rules were kept, while duplicated
  metadata explanation was removed from the contract and left to the internal
  skill.
- `MonitoringPolicyDocument`: the visible contract now uses a compact ASCII
  helper that preserves `strategy_note`, structured `confirmation`,
  `risk_guard`, and `action`. It also states that omitting `direct_trade` or
  `escalate` requires document-level `no_action_rationale`.
- `ResearchSection`: already minimal, so no schema template expansion was added.

## ReAct Plan Adjustment

`Scratchpad.plan` now stores only the latest non-empty `plan_update`. This keeps
`plan` from duplicating long-lived history already represented by
`recent_trajectory` and `task_ledger`.

## Modified Areas

- Document3 workflow input compaction in
  `src/doxagent/workflows/initialization/orchestrator.py`.
- Document3 visible snapshot transformation in
  `src/doxagent/prompts/assembler.py`.
- ReAct latest-plan behavior and ReAct snapshot omission in
  `src/doxagent/agents/runtime/react.py`.
- Document3/ReAct regression coverage in
  `tests/test_phase20_initialization_quality_hardening.py` and
  `tests/test_phase16_react_harness.py`.
