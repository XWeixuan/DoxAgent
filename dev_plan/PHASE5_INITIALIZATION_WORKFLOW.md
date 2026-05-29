# Phase 5 Blackboard Initialization Workflow

## Scope

Phase 5 introduces the deterministic Blackboard initialization workflow MVP. It
uses DoxAgent-owned workflow models and an in-memory checkpoint, then keeps all
stable business writes routed through `BlackboardService.submit_patch`.

This phase does not call a real Microsoft Agent Framework workflow, real model
provider, DoxAtlas service, market-data API, fact-check provider, or external
GitHub agent project.

## Workflow Runner

`BlackboardInitializationWorkflow` runs the fixed initialization sequence:

1. StartTickerInitialization
2. BuildGlobalResearch
3. ReviewGlobalResearch
4. GenerateExpectationUnits
5. ReviewExpectationFields
6. ResolveObjectionsAndDelegations
7. PromoteExpectationToBeliefState
8. GenerateKnownEvents
9. GenerateMonitoringConfig
10. GenerateMonitoringPolicy
11. FinalizeInitialization

The runner produces a `WorkflowCheckpoint` after each node. Checkpoints are
Pydantic models and can round-trip through JSON for same-process resume. File or
database persistence is intentionally deferred.

## Agent and Blackboard Boundary

Mock workflow outputs are produced through `MockAgentRunner` using a deterministic
result factory. The workflow writes agent results to Working Memory first. Stable
documents enter Belief State only through Blackboard patches submitted by
`BlackboardService`.

The workflow enforces document dependencies:

- Known Events requires Global Research and Expectation Unit.
- Monitoring Config requires Global Research, Expectation Unit, and Known Events.
- Monitoring Policy requires Global Research, Expectation Unit, Known Events, and
  Monitoring Config.

## Blocking Behavior

Review nodes can create objections and delegations. Open objections and active
delegations block expectation promotion. When auto-resolution is enabled, the
workflow resolves mock blockers before promotion. When disabled, the workflow
stops in a blocked state and can resume after manual lifecycle transitions.
