"""Non-blocking semantic diagnostics for the O3 Initialize handoff.

The metrics in this module identify suspicious generation patterns; they do not
decide investment quality. Final Review receives the complete report and must
either explain the pattern, repair the artifacts, or retain an advisory issue.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from .schema import (
    PathStatus,
    Policy,
    ReviewIssue,
    ReviewResult,
    SemanticDiagnosticFinding,
    SemanticDiagnostics,
    WorklistEntry,
)

_HIDDEN_OR = re.compile(r"(?:或|或者|任一|二者之一|\beither\b|\balternatively\b|\bor\b)", re.I)
_DEGREE_WORD = re.compile(
    r"(?:重大|显著|大规模|实质(?:性)?|明显|大幅|material|major|significant|"
    r"substantial|meaningful|sharply)",
    re.I,
)
_COMPARATOR = re.compile(
    r"(?:\d|%|％|>=|<=|>|<|至少|至多|超过|低于|高于|不低于|不高于|"
    r"达到|降至|升至|推迟至|提前至|相对|较(?:当前|基线|预期)|"
    r"non-binding|binding|sample|qualification|production|proposal|final rule|"
    r"cross-quarter|standard production BOM)",
    re.I,
)
_SEMANTIC_SCRIPT_NAME = re.compile(
    r"(?:^|[/\\])(?:_?(?:build|generate|generator|compile)[^/\\]*"
    r"(?:stage|trigger|calibration|polic)|[^/\\]*(?:stage_a|policies?))"
    r"\.(?:py|js|mjs|cjs|ps1|sh)(?:\b|['\"])",
    re.I,
)
_SEMANTIC_MAPPING = re.compile(
    r"(?:candidate_trigger\s*=\s*[^\n;]*possible_occurrence|"
    r"criterion\s*=\s*[^\n;]*candidate_trigger|"
    r"reference_state\s*=\s*[^\n;]*current_state|"
    r"activation_conditions|trigger_calibrations\.jsonl|output/work/policies)",
    re.I,
)


def _normalized(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


def _ratio(count: int, total: int) -> float:
    return count / total if total else 0.0


def _extract_named_strings(value: Any, field_name: str) -> set[str]:
    found: set[str] = set()

    def visit(item: Any, *, selected: bool = False) -> None:
        if isinstance(item, Mapping):
            for key, child in item.items():
                visit(child, selected=selected or str(key) == field_name)
            return
        if isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)):
            for child in item:
                visit(child, selected=selected)
            return
        if selected and isinstance(item, str) and item.strip():
            found.add(_normalized(item))

    visit(value)
    return found


def _batch_generation_evidence(
    *, workspace_paths: Iterable[str], command_summaries: Iterable[str]
) -> list[str]:
    evidence: list[str] = []
    for value in [*workspace_paths, *command_summaries]:
        compact = " ".join(value.split())
        if _SEMANTIC_SCRIPT_NAME.search(compact):
            evidence.append(compact[:500])
        elif _SEMANTIC_MAPPING.search(compact) and re.search(
            r"(?:for\s*\(|for\s+\w+\s+in|foreach|\.map\s*\()", compact, re.I
        ):
            evidence.append(compact[:500])
    return list(dict.fromkeys(evidence))[:20]


def build_semantic_diagnostics(
    *,
    document2_payload: Mapping[str, Any],
    worklist: list[WorklistEntry],
    policies: list[Policy],
    workspace_paths: Iterable[str] = (),
    command_summaries: Iterable[str] = (),
) -> SemanticDiagnostics:
    conditions = [
        (policy.policy_id, condition)
        for policy in policies
        for condition in policy.activation_conditions
    ]
    condition_count = len(conditions)
    criterion_boundary_ids = [
        f"{policy_id}/{condition.condition_id}"
        for policy_id, condition in conditions
        if _normalized(condition.criterion)
        == _normalized(condition.calibration.trigger_boundary)
    ]
    reference_counts = Counter(
        _normalized(condition.calibration.reference_state) for _, condition in conditions
    )
    duplicate_reference_count = sum(count - 1 for count in reference_counts.values() if count > 1)
    d2_occurrences = _extract_named_strings(document2_payload, "possible_occurrence")
    d2_copy_ids = [
        f"{policy_id}/{condition.condition_id}"
        for policy_id, condition in conditions
        if _normalized(condition.criterion) in d2_occurrences
    ]
    hidden_or_ids = [
        f"{policy_id}/{condition.condition_id}"
        for policy_id, condition in conditions
        if _HIDDEN_OR.search(condition.criterion)
    ]
    unanchored_degree_ids = [
        f"{policy_id}/{condition.condition_id}"
        for policy_id, condition in conditions
        if _DEGREE_WORD.search(condition.criterion) and not _COMPARATOR.search(condition.criterion)
    ]
    boundary_true = sum(item.d2_boundary_sufficient for item in worklist)
    unresolved = sum(item.status is PathStatus.UNRESOLVED for item in worklist)
    group_sizes = {policy.policy_id: len(policy.activation_conditions) for policy in policies}
    batch_evidence = _batch_generation_evidence(
        workspace_paths=workspace_paths,
        command_summaries=command_summaries,
    )

    criterion_boundary_ratio = _ratio(len(criterion_boundary_ids), condition_count)
    reference_duplicate_ratio = _ratio(duplicate_reference_count, condition_count)
    d2_copy_ratio = _ratio(len(d2_copy_ids), condition_count)
    boundary_true_ratio = _ratio(boundary_true, len(worklist))
    findings: list[SemanticDiagnosticFinding] = []

    def warn(code: str, message: str, affected_ids: list[str] | None = None) -> None:
        findings.append(
            SemanticDiagnosticFinding(
                code=code,
                message=message,
                affected_ids=affected_ids or [],
            )
        )

    if condition_count >= 3 and criterion_boundary_ratio >= 0.8:
        warn(
            "CRITERION_BOUNDARY_EXACT_COPY_HIGH",
            f"criterion == trigger_boundary for {len(criterion_boundary_ids)}/{condition_count} "
            "Conditions; inspect for mechanical field mapping.",
            criterion_boundary_ids,
        )
    if condition_count >= 3 and reference_duplicate_ratio >= 0.5:
        warn(
            "REFERENCE_STATE_DUPLICATION_HIGH",
            f"duplicate reference_state excess is {duplicate_reference_count}/{condition_count}; "
            "inspect for Unit/Shell summary reuse.",
        )
    if condition_count >= 3 and d2_copy_ratio >= 0.5:
        warn(
            "D2_POSSIBLE_OCCURRENCE_EXACT_COPY_HIGH",
            f"criterion exact-copies D2 possible_occurrence for "
            f"{len(d2_copy_ids)}/{condition_count} Conditions.",
            d2_copy_ids,
        )
    if len(worklist) >= 3 and boundary_true_ratio >= 0.9:
        warn(
            "D2_BOUNDARY_SUFFICIENT_NEAR_ALL",
            f"d2_boundary_sufficient=true for {boundary_true}/{len(worklist)} Paths; "
            "verify current reality, market expectation, minimum surprise, material scope, "
            "and message route were actually present in D2.",
            [item.path_id for item in worklist if item.d2_boundary_sufficient],
        )
    if hidden_or_ids:
        warn(
            "HIDDEN_OR_CANDIDATES",
            f"{len(hidden_or_ids)} Condition criteria contain possible internal alternatives.",
            hidden_or_ids,
        )
    if unanchored_degree_ids:
        warn(
            "UNANCHORED_DEGREE_TERMS",
            f"{len(unanchored_degree_ids)} Condition criteria contain degree terms without "
            "an obvious numeric or categorical comparator.",
            unanchored_degree_ids,
        )
    max_group_size = max(group_sizes.values(), default=0)
    if max_group_size >= 4:
        affected = [policy_id for policy_id, size in group_sizes.items() if size >= 4]
        warn(
            "LARGE_OR_GROUP_CANDIDATES",
            f"{len(affected)} Policies contain four or more OR Conditions; inspect one-time "
            "decision-boundary grouping.",
            affected,
        )
    if batch_evidence:
        warn(
            "SEMANTIC_BATCH_GENERATION_DETECTED",
            "Turn telemetry or workspace paths contain candidate semantic batch-generation "
            "scripts/commands; inspect their actual role rather than treating script use itself "
            "as a deterministic error.",
        )
    if unresolved == 0 and any(
        finding.code
        in {
            "CRITERION_BOUNDARY_EXACT_COPY_HIGH",
            "REFERENCE_STATE_DUPLICATION_HIGH",
            "D2_POSSIBLE_OCCURRENCE_EXACT_COPY_HIGH",
            "D2_BOUNDARY_SUFFICIENT_NEAR_ALL",
            "SEMANTIC_BATCH_GENERATION_DETECTED",
        }
        for finding in findings
    ):
        warn(
            "ZERO_UNRESOLVED_WITH_SYSTEMIC_ANOMALIES",
            "No Path is unresolved despite one or more systemic semantic diagnostics; Final "
            "Review must verify this reflects genuine research closure.",
        )

    return SemanticDiagnostics(
        condition_count=condition_count,
        path_count=len(worklist),
        criterion_equals_trigger_boundary_count=len(criterion_boundary_ids),
        criterion_equals_trigger_boundary_ratio=criterion_boundary_ratio,
        duplicate_reference_state_count=duplicate_reference_count,
        duplicate_reference_state_ratio=reference_duplicate_ratio,
        d2_possible_occurrence_exact_copy_count=len(d2_copy_ids),
        d2_possible_occurrence_exact_copy_ratio=d2_copy_ratio,
        d2_boundary_sufficient_true_count=boundary_true,
        d2_boundary_sufficient_true_ratio=boundary_true_ratio,
        unresolved_path_count=unresolved,
        hidden_or_condition_ids=hidden_or_ids,
        unanchored_degree_condition_ids=unanchored_degree_ids,
        or_group_sizes=group_sizes,
        max_or_group_size=max_group_size,
        semantic_batch_generation_detected=bool(batch_evidence),
        semantic_batch_generation_evidence=batch_evidence,
        requires_explanation=bool(findings),
        findings=findings,
    )


def reconcile_review_with_diagnostics(
    review: ReviewResult, diagnostics: SemanticDiagnostics
) -> ReviewResult:
    """Ensure diagnostics cannot disappear behind an unexplained zero-issue PASS."""

    issues = list(review.issues)
    existing_codes = {item.code for item in issues}
    if (
        not review.diagnostics_reviewed
        and "SEMANTIC_DIAGNOSTICS_NOT_REVIEWED" not in existing_codes
    ):
        issues.append(
            ReviewIssue(
                code="SEMANTIC_DIAGNOSTICS_NOT_REVIEWED",
                message=(
                    "Final Review did not acknowledge the supplied semantic diagnostics; "
                    "the omission is retained as a non-blocking review issue."
                ),
            )
        )
    if (
        diagnostics.requires_explanation
        and len(review.diagnostics_explanation.strip()) < 20
        and "SEMANTIC_DIAGNOSTICS_UNEXPLAINED" not in existing_codes
    ):
        issues.append(
            ReviewIssue(
                code="SEMANTIC_DIAGNOSTICS_UNEXPLAINED",
                message=(
                    "Significant semantic diagnostic patterns were supplied, but Final Review "
                    "returned no substantive explanation or retained issue."
                ),
                affected_policy_ids=list(
                    dict.fromkeys(
                        affected_id.split("/", 1)[0]
                        for finding in diagnostics.findings
                        for affected_id in finding.affected_ids
                        if affected_id
                    )
                ),
            )
        )
    return review.model_copy(
        update={
            "status": "REVIEW_BLOCKED" if issues else "PASSED",
            "issues": issues,
            "issue_count": len(issues),
            "blocking_issue_count": sum(item.blocking for item in issues),
        }
    )
