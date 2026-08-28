"""Deterministic D3 initial assembly, coverage mapping and patch application."""

from __future__ import annotations

from datetime import datetime

from .identity import allocate_stable_policy_ids
from .schema import (
    CoverageGap,
    CoverageMap,
    CoveragePath,
    Document2Ref,
    EventLibraryRef,
    FailedShellCoverage,
    Policy,
    PolicyPatchSet,
    PolicySet,
    PublicationState,
    ValidationReport,
    WorklistEntry,
)


def build_coverage_map(
    *,
    ticker: str,
    worklist: list[WorklistEntry],
    expected_gap_refs: list[tuple[str, str, str]],
    failed_shells: list[FailedShellCoverage] | None = None,
    warnings: list[str] | None = None,
) -> CoverageMap:
    by_ref: dict[tuple[str, str, str], list[WorklistEntry]] = {}
    for item in worklist:
        by_ref.setdefault((item.shell_id, item.expectation_id, item.gap_id), []).append(item)
    gaps = [
        CoverageGap(
            shell_id=shell_id,
            expectation_id=expectation_id,
            gap_id=gap_id,
            paths=[
                CoveragePath(
                    path_id=item.path_id,
                    direction=item.direction,
                    status=item.status,
                    policy_ids=item.policy_ids,
                    unresolved_reason=item.unresolved_reason,
                )
                for item in by_ref.get((shell_id, expectation_id, gap_id), [])
            ],
        )
        for shell_id, expectation_id, gap_id in expected_gap_refs
    ]
    return CoverageMap(
        ticker=ticker,
        gaps=gaps,
        failed_shells=failed_shells or [],
        warnings=warnings or [],
    )


def assemble_initial_policy_set(
    *,
    ticker: str,
    document2_ref: Document2Ref,
    event_library_ref: EventLibraryRef | None,
    drafts: list[Policy],
    previous: PolicySet | None,
    validation: ValidationReport,
    published_at: datetime,
) -> tuple[PolicySet, dict[str, str]]:
    policies, id_map = allocate_stable_policy_ids(
        ticker=ticker,
        drafts=drafts,
        previous=(previous.policies if previous else []),
    )
    return (
        PolicySet(
            ticker=ticker,
            policy_set_version=(previous.policy_set_version + 1 if previous else 1),
            publication_state=validation.publication_state,
            document2_ref=document2_ref,
            event_library_ref=event_library_ref,
            policies=policies,
            published_at=published_at,
        ),
        id_map,
    )


def apply_patch(
    *, current: PolicySet, patch: PolicyPatchSet, published_at: datetime
) -> PolicySet | None:
    if patch.base_policy_set_version != current.policy_set_version:
        raise ValueError(
            f"stale policy patch: base={patch.base_policy_set_version}, "
            f"current={current.policy_set_version}"
        )
    if patch.is_empty:
        return None
    policies = {item.policy_id: item for item in current.policies}
    for policy_id in patch.retire_policy_ids:
        policies.pop(policy_id, None)
    for policy in patch.upsert_policies:
        policies[policy.policy_id] = policy
    return PolicySet(
        ticker=current.ticker,
        policy_set_version=current.policy_set_version + 1,
        publication_state=PublicationState.COMPLETE,
        document2_ref=current.document2_ref,
        event_library_ref=patch.event_library_ref,
        policies=list(policies.values()),
        published_at=published_at,
    )
