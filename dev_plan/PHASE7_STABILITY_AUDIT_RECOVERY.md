# Phase 7 Stability, Audit, and Recovery

## Scope

Phase 7 strengthens the Phase 6 mock MVP so it is easier to audit, debug, and
recover in-process. It adds an in-memory business audit query API, run debug
reports, and failure/retry tests around workflow and contract errors.

This phase does not add databases, migrations, real provider calls, real tool
calls, full CLI behavior, or external GitHub agent adapters.

## Audit Query API

`AuditQueryService` reads a `BlackboardRun` or a run id through
`BlackboardService`. It can list Commit Log records, trace a stable field back
to its patch/commit/agent/evidence, report unresolved objections, and report
blocking delegations.

The audit query layer is read-only. It does not replace `BlackboardService` and
does not mutate Working Memory, Belief State, objections, delegations, or Commit
Log entries.

## Debug Reports

`build_run_debug_report` summarizes workflow status, completed nodes, next node,
Belief State document types, Working Memory count, Commit Log count, blockers,
and residual risks.

The report explicitly separates DoxAgent business audit from LangSmith or model
tracing. Commit Log remains the business audit record.

## Recovery Boundary

Recovery remains same-process and checkpoint-based. Blocked checkpoints can
resume after objection/delegation lifecycle transitions. Partial retry from a
checkpoint must not duplicate already committed documents. Contract failures
such as failed agent results, missing patches, missing evidence, or dependency
violations stop with a clear blocked/debuggable state and must not corrupt
stable Belief State.
