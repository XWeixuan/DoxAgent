# Phase 3.5 Real Workflow Execution

## Summary

Phase 3.5 keeps the DoxAgent-owned initialization workflow runner and connects
it to the real `AgentTask -> AgentResult` execution boundary introduced in
Phase 3.4. Microsoft Agent Framework remains an agent runtime shell through
`ModelGatewayAgentRunner`; it does not replace workflow orchestration in this
phase.

The default workflow mode remains mock for existing samples and regression
tests. A new `agent_runner` mode can call a real or fake `AgentRunner`, consume
structured agent output, write Working Memory, submit valid patches through
`BlackboardService`, and persist checkpoints after node completion or blocking
failures.

## Key Changes

- Added workflow execution modes: `mock` preserves Phase 5 behavior, while
  `agent_runner` calls the injected runner and records runtime metadata in
  checkpoint state.
- Added `WorkflowAgentResultNormalizer` so runner payloads with
  `payload["structured"]` can be converted into standard `AgentResult`
  collections for patches, evidence refs, objections, delegations, and tool
  calls.
- Expanded workflow task context with ticker, node, completed nodes, stable
  document types, Belief State summary, Working Memory summary, pending patch
  ids, active blockers, and tool request hints.
- In `agent_runner` mode, `BuildGlobalResearch` calls C1, C2, C3, and O4 before
  completing the node. At least one valid Global Research patch must be
  submitted through the Blackboard Service.
- Preserved the existing promotion model: O1 expectation patches remain pending
  until review, unresolved objections and blocking delegations stop promotion,
  and stable state is written only through `BlackboardService.submit_patch()`.
- Working Memory now records payload, patch ids, objection/delegation ids, tool
  calls, skill versions, and model audit summaries for workflow-level review.

## Failure and Recovery Semantics

- Agent failure, invalid structured output, schema validation failure, missing
  evidence, missing required patch, dependency violation, and required tool
  failure block the workflow without writing invalid Belief State.
- Checkpoint metadata records execution mode, runtime/tool mode, last agent
  result summaries, and latest error code/message.
- `resume_latest(run_id)` continues to work with the checkpoint repository; a
  blocked promotion can resume after objections/delegations are resolved outside
  the workflow.
- Partial retry preserves completed commits and does not resubmit already
  completed nodes.

## Non-Goals

- No MAF workflow runtime replacement.
- No real OpenAI/Anthropic/DoxAtlas/market/fact-check calls in default tests.
- No O3 trading strategy implementation.
- No Phase 8 adapter-to-document deep mapping; that remains Phase 3.6.
- No direct Blackboard writes from agent runtime or tools.

## Validation

Tests cover:

- Agent-runner mode calling C1/C2/C3/O4 for Global Research.
- Structured JSON normalization into stable Pydantic contracts.
- Full fake-runner initialization through five document types.
- Invalid structured output and required tool failures blocking safely.
- Manual blocker resolution followed by `resume_latest`.
- Partial retry without duplicate Global Research commits.

Regression commands:

```powershell
uv run pytest
uv run ruff check .
uv run mypy src
```
