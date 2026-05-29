# Phase 1 Contracts

## Scope

Phase 1 defines DoxAgent's domain contracts only. It does not implement
Blackboard Service persistence, MAF runner behavior, workflow orchestration,
tools, Model Gateway, or external GitHub agent adapters.

## Model Boundary

All future agent, workflow, tool, and adapter integrations must communicate
through DoxAgent-owned models under `src/doxagent/models`:

- `AgentTask` and `AgentResult` are the standard agent execution boundary.
- `BlackboardPatch` is the standard state-change proposal.
- `EvidenceRef` is the standard evidence reference.
- `Objection` and `Delegation` express blocking review and task dependencies.
- Five document models represent the PRD work products for one ticker.

These models intentionally do not import Microsoft Agent Framework types.

## Field Strategy

The document schemas use structured core fields. PRD-stable sections are first
class fields, while report content remains in reusable `ResearchSection` objects
with text, summary, evidence references, author, and reviewers.

All cross-object references are string IDs. Phase 1 does not introduce a
repository, database relation, or service-level state machine.

## ID Strategy

Schemas accept non-empty string IDs. The helper `new_id(prefix)` generates
readable IDs such as `task_<uuid>`, `patch_<uuid>`, and `evidence_<uuid>`.

## Promotion Blocking

Phase 1 includes only stateless validation helpers:

- unresolved objections block promotion;
- open or assigned delegations block promotion;
- resolved objections and completed delegations do not block promotion.

Actual Working Memory to Belief State transitions belong to Blackboard Service
in a later phase.
