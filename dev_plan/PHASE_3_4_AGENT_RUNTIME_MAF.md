# Phase 3.4 Agent Runtime and MAF Integration

## Scope

Phase 3.4 adds the first real DoxAgent Agent Runtime backed by Microsoft Agent
Framework (MAF). MAF is used only as the agent execution shell. DoxAgent still
owns the stable business boundary:

- Agent calls enter through `AgentTask`.
- Agent calls return `AgentResult`.
- Model calls go through `ModelGateway`.
- Skills are selected by `SkillInjector`.
- Tools are accessed through DoxAgent `ToolRegistry`.
- Blackboard writes remain forbidden inside the runtime.

The Phase 3.2 real tools exist but are still under debugging, so this phase is
validated with fake gateway and mock tools. The runtime is nevertheless wired
against the same tool names, permission checks, and `ToolResult` schema used by
the real tool layer.

## Runtime Components

`ModelGatewayChatClient` adapts MAF chat requests to DoxAgent model requests. It
inherits MAF `BaseChatClient`, converts MAF messages into `ModelRequest`, calls
`ModelGateway.complete()`, and returns a MAF `ChatResponse`. It preserves the
last gateway request and response for audit/debug handling.

`MafAgentFactory` maps an `AgentDefinition` into a MAF `Agent`. The mapping
includes agent name, role instruction, output schema, skill ids, readable
context scopes, writable targets, and allowed tools as runtime metadata.

`ModelGatewayAgentRunner` is the primary runner implementation. It validates the
task against the agent definition, injects a minimal `SkillBundle`, optionally
builds a bounded context snapshot, executes requested tools through
`ToolRegistry`, runs the MAF agent, and normalizes the result into
`AgentResult`.

`MafAgentAdapter` is now a compatibility wrapper around
`ModelGatewayAgentRunner` instead of a placeholder.

## Execution Flow

1. Resolve `AgentDefinition` from the default or injected registry.
2. Reject disallowed task types before any model call.
3. Inject skills without mutating the original task.
4. Build a permission-bounded context snapshot if a `ContextBuilder` is present.
5. Execute explicit runtime tool requests if `tool_mode` is not disabled.
6. Fail early when a required tool fails.
7. Assemble a compact JSON instruction payload for MAF.
8. Send MAF chat execution through `ModelGatewayChatClient`.
9. Parse structured JSON output.
10. Return a standard `AgentResult`.

The prompt includes role/context/tool/skill summaries and explicit rules that
agent runtime output must not write Blackboard state directly.

## Tool Compatibility

Supported tool modes are:

- `disabled`: no tool registry is exposed.
- `mock`: use the existing offline mock registry unless a registry is injected.
- `real`: accept an injected real registry or use an empty registry as a safe
  fallback while Phase 3.2 tools are still being debugged.

Tool execution is driven by `task.input_context["tool_requests"]`. Required
tools can be declared with `task.input_context["required_tool_names"]`. Tool
success is summarized in `AgentResult.tool_calls`, and tool evidence refs are
copied into `AgentResult.evidence_refs`. Tool failure is normalized into
`ToolCallSummary`; it only fails the agent run when the failed tool is required.

Tools cannot write Blackboard state and are not passed repository/service
objects by the runtime.

## Audit and Tracing Boundary

`AgentResult.payload` includes:

- `runtime="maf"`
- structured model output
- model audit summary
- injected skill ids and versions
- tool mode
- agent definition summary
- optional bounded context snapshot

ModelGateway metadata includes ticker, run id, agent name, task type, workflow
node, and skill versions. This is model/runtime observability only; it does not
replace Blackboard Commit Log business audit.

## Non-Goals

- No MAF workflow replacement.
- No direct provider SDK calls from agent runtime.
- No direct Blackboard mutations.
- No real external tool smoke tests while Phase 3.2 tools are still unstable.
- No streaming, cost accounting, or long-running MAF harness persistence.
- No production prompt optimization or O3 strategy implementation.

## Validation

The Phase 3.4 tests cover:

- Fake gateway success path.
- Invalid JSON/object schema failure.
- Gateway error mapping to `AgentError`.
- `MafAgentAdapter` delegating to the real runner.
- Automatic skill injection for runtime payloads.
- Bounded context consumption.
- Mock tool success and required-tool failure behavior.

Final local checks:

```powershell
uv run pytest
uv run ruff check .
uv run mypy src
```
