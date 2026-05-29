# Phase 3 Blackboard Service

## Scope

Phase 3 introduces an in-memory Blackboard Service. It gives Working Memory,
Belief State, Objection, Delegation, Evidence, and Commit Log a single business
state owner. It does not introduce file persistence, databases, workflow
orchestration, agent runtime behavior, tool calls, or crash-recovery storage.

## Repository

`InMemoryBlackboardRepository` stores `BlackboardRun` objects by run id. Each run
contains ticker metadata, workflow state, Working Memory entries, a Belief State
snapshot, objections, delegations, and commit log entries.

Repository reads return deep copies so callers cannot mutate state without going
through `BlackboardService`.

## Patch Submission

`submit_patch` validates:

- run and target ticker compatibility;
- `AgentPermissions.can_propose_patch`;
- document type membership in `AgentPermissions.writable_targets`;
- at least one evidence reference;
- no unresolved objection for the target;
- no open or assigned delegation for the target.

Successful patches are applied to Belief State using dot-path dict writes:

```text
document_type -> document_id/expectation_id/default -> field_path
```

List-index path writes are intentionally unsupported in Phase 3.

## Lifecycle

Objections can be accepted, partially accepted, rejected, resolved, or marked
unresolved. Open and unresolved objections block patch submission.

Delegations can be assigned, completed, failed, retried, or cancelled. Open and
assigned delegations block patch submission. Completed and cancelled delegations
do not block. A retried delegation returns to the assigned blocking state.

## Commit Log

Every successful patch writes one Commit Log entry. Failed patch validation does
not mutate Belief State and does not write Commit Log.
