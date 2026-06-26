# Step 5.0 Contract Alignment Snapshot

Date: 2026-06-26

## Goal

Before Step 5 changes, verify that the Step 4 detail path is not merely wired through
legacy patches: `GenerateExpectationDetails` now has a candidate/revision primary
state that can feed `ReviewExpectationFields`, while the review node still exposes
the legacy objection bridge that Step 5 must split.

## Snapshot Result

Status: pass for Step 5 entry.

The following targeted checks passed:

```powershell
uv run pytest tests/test_initialization_characterization.py::test_generate_expectation_details_exports_candidate_revisions_not_o1_patches tests/test_initialization_characterization.py::test_document2_detail_and_review_contexts_prefer_document1_context_pack tests/test_phase5_initialization_workflow.py::test_review_expectation_fields_runs_reviewers_concurrently_in_spec_order -q
```

Result: `3 passed, 3 warnings`.

## Confirmed Contracts

1. `GenerateExpectationDetails` requests `ExpectationDetailCandidateResult`.
2. O1 detail tasks have no writable targets.
3. `document2_pending_revisions` is the primary internal detail state.
4. Legacy pending patches are still derived for downstream compatibility.
5. Review tasks consume `Document1ContextPack` and compact Global Research context.
6. Review fan-out remains A1/C1/C3/O4 in deterministic spec order.
7. Review working-memory entries still have empty `patch_ids` under the structured test runner.

## Step 5 Gap To Address

`ReviewExpectationFields` still directly projects reviewer objections and deterministic
numeric sanity objections into Blackboard objections. That must not be treated as a
Document2 blocker fix. Step 5 should introduce typed `Document2ReviewFinding` and
`EvidenceAssessment` as the primary review/evidence state, while keeping the old
Blackboard objection bridge only as a temporary compatibility path for the legacy
resolver.

## Auxiliary Probe Note

A direct read-only Python metadata probe to `ReviewExpectationFields` timed out and
was not retried. The snapshot above is therefore based on the passing targeted tests
and code inspection, not on that auxiliary probe.
