# Phase 4 Agent Runtime, Context Builder, and Tool Boundary

## Scope

Phase 4 introduces the DoxAgent-owned agent runtime boundary. Agent execution is
represented by `AgentTask -> AgentResult`; context is built through a bounded
Context Builder; tools return normalized results and evidence only.

This phase does not run real Microsoft Agent Framework agents, call real model
providers, query DoxAtlas, call market-data APIs, or write Blackboard state from
tools.

## Agent Runtime

The agent package defines runtime configuration, default agent definitions, a
registry, and a runner protocol. The default registry covers O1, O2, O4, A1,
A2, C1, C2, and C3.

`MockAgentRunner` validates the agent exists and returns an `AgentResult`.
`MafAgentAdapter` is a placeholder boundary for future MAF-backed execution and
does not import MAF types into DoxAgent models or Blackboard code.

## Context Builder

`ContextBuilder` reads from `BlackboardService` and returns an
`AgentContextSnapshot`. The snapshot is permission-bounded and includes task
input, selected Belief State documents, Working Memory summaries when allowed,
unresolved objections, blocking delegations, and related evidence.

The snapshot does not expose repository internals, mutable run objects, Commit
Log internals, or a Blackboard write path.

## Tool Boundary

The tool package defines `ToolRequest`, `ToolResult`, `ToolError`,
`ToolClient`, and `ToolRegistry`. The registry enforces
`AgentPermissions.allowed_tools` before dispatching a request.

Phase 4 mock tools cover DoxAtlas query, DoxAtlas source lookup, market data,
fact-check search, and external research. Tool results can be converted into
`EvidenceRef` objects. Tools cannot create patches or mutate Blackboard state.
