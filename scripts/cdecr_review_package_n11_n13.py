"""Run an independent M4-assisted semantic review of N11-N13 Package output."""

from __future__ import annotations

import argparse
import json
import math
from itertools import combinations
from pathlib import Path
from typing import Literal

from pydantic import ValidationError, model_validator

from cdecr.config import CDECRSettings
from cdecr.contracts import StrictModel
from cdecr.models import DashScopeStructuredModelClient, ModelTier
from cdecr.package_engine import (
    atomic_surface_evidence,
    representative_package_members,
    request_local_source_ids,
)
from cdecr.ports import StructuredModelRequest
from cdecr.registry import SQLiteCDECRRegistry


class PackageReview(StrictModel):
    package_id: str
    cohesive: bool
    wrong_member_event_ids: list[str]
    reason: str


class PairReview(StrictModel):
    left_package_id: str
    right_package_id: str
    relation: Literal["SAME_PACKAGE", "DIFFERENT_PACKAGE", "UNCERTAIN"]
    reason: str

    @model_validator(mode="after")
    def ordered_pair(self) -> PairReview:
        if self.left_package_id >= self.right_package_id:
            raise ValueError("package pair IDs must be in ascending order")
        return self


class ExternalCandidateReview(StrictModel):
    candidate_id: str
    appropriate_external_relation: bool
    reason: str


class PackageSemanticReview(StrictModel):
    package_reviews: list[PackageReview]
    pair_reviews: list[PairReview]
    external_candidate_reviews: list[ExternalCandidateReview]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def _cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    denominator = math.sqrt(sum(value * value for value in left)) * math.sqrt(
        sum(value * value for value in right)
    )
    return (
        0.0
        if denominator == 0
        else sum(a * b for a, b in zip(left, right, strict=True)) / denominator
    )


def main() -> int:
    args = _parser().parse_args()
    registry = SQLiteCDECRRegistry(args.registry)
    registry.initialize()
    settings = CDECRSettings(CDECR_SQLITE_PATH=args.registry)
    packages = registry.list_current_packages(limit=10000)
    package_by_id = {item.package_id: item for item in packages}
    embeddings = {
        item.owner_id: item.vector
        for item in registry.list_latest_embeddings(
            owner_kind="event_package",
            model=settings.model_m1,
            limit=100000,
        )
    }
    pair_ids: list[tuple[str, str]] = []
    for left, right in combinations(packages, 2):
        similarity = _cosine(
            embeddings.get(left.package_id, []),
            embeddings.get(right.package_id, []),
        )
        anchor_overlap = bool(
            set(left.anchor_entities).intersection(right.anchor_entities)
            or set(left.package_anchor_ids).intersection(right.package_anchor_ids)
            or (left.anchor_artifact_id and left.anchor_artifact_id == right.anchor_artifact_id)
            or (left.anchor_period_id and left.anchor_period_id == right.anchor_period_id)
        )
        if left.package_family is right.package_family and (anchor_overlap or similarity >= 0.65):
            pair_ids.append(
                (
                    min(left.package_id, right.package_id),
                    max(left.package_id, right.package_id),
                )
            )
    external_candidates = registry.list_package_external_relation_candidates()
    all_events = registry.list_current_atomic_events(limit=10000)
    source_short_ids = request_local_source_ids(registry, all_events)
    package_payload = []
    for package in packages:
        members = [
            event
            for event_id in package.member_event_ids
            if (event := registry.get_current_atomic_event(event_id)) is not None
        ]
        package_payload.append(
            {
                "package": package.model_dump(mode="json"),
                "members": [
                    {
                        "event_id": event.event_id,
                        "event_family": event.event_family.value,
                        "canonical_proposition": event.canonical_proposition,
                        "identity_profile": event.identity_profile.model_dump(mode="json"),
                        "time": event.time.model_dump(mode="json"),
                        "surface_evidence": atomic_surface_evidence(
                            registry,
                            event,
                            source_short_ids=source_short_ids,
                        )[0].model_dump(mode="json", exclude_none=True),
                        "source_ids": atomic_surface_evidence(
                            registry,
                            event,
                            source_short_ids=source_short_ids,
                        )[1],
                    }
                    for event in representative_package_members(members)
                ],
            }
        )
    pair_payload = [
        {
            "left_package_id": left_id,
            "right_package_id": right_id,
        }
        for left_id, right_id in pair_ids
    ]
    external_payload = []
    for candidate in external_candidates:
        source_event = registry.get_current_atomic_event(candidate.source_event_id)
        target = package_by_id.get(candidate.target_package_id)
        external_payload.append(
            {
                "candidate": candidate.model_dump(mode="json"),
                "source_event": (
                    None
                    if source_event is None
                    else {
                        "event_family": source_event.event_family.value,
                        "canonical_proposition": source_event.canonical_proposition,
                        "time": source_event.time.model_dump(mode="json"),
                        "surface_evidence": atomic_surface_evidence(
                            registry,
                            source_event,
                            source_short_ids=source_short_ids,
                        )[0].model_dump(mode="json", exclude_none=True),
                        "source_ids": atomic_surface_evidence(
                            registry,
                            source_event,
                            source_short_ids=source_short_ids,
                        )[1],
                    }
                ),
                "target_package_title": (None if target is None else target.canonical_title),
                "target_package_summary": (None if target is None else target.canonical_summary),
            }
        )
    expected_packages = {item.package_id for item in packages}
    expected_pairs = set(pair_ids)
    expected_external = {item.candidate_id for item in external_candidates}
    system_prompt = """You are an independent CDECR Package quality reviewer.
Review the supplied real-corpus Package output conservatively.
For every Package, decide whether all listed Atomic Events belong inside one
specific parent container or evolving matter; list any reaction or unrelated
member as wrong. Contents such as actual results, guidance, metrics, and
management commentary may share one earnings disclosure, while different
institutions or different analyst reports remain separate. For every candidate
Package pair, use the combined anchors, entities, raw surfaces, periods, time,
request-local source IDs, and representative members to decide whether they
refer to the same parent container or coherent evolving matter. Missing one
artifact or period field does not force separation. Retrieval similarity is
only a candidate signal. For every external candidate, decide whether the
Atomic Event is genuinely external to but related to the target Package. Cover
every supplied ID exactly once. Return JSON only."""
    user_payload = {
        "packages": package_payload,
        "candidate_pairs": pair_payload,
        "external_candidates": external_payload,
    }
    request = StructuredModelRequest(
        system_prompt=system_prompt,
        user_prompt=json.dumps(user_payload, ensure_ascii=False),
        json_schema=PackageSemanticReview.model_json_schema(),
    )
    client = DashScopeStructuredModelClient(
        tier=ModelTier.M4,
        api_key=settings.require_dashscope(),
        base_url=settings.dashscope_base_url,
        model=settings.model_m4,
        timeout_seconds=settings.model_timeout_seconds,
        fallback_api_keys=settings.dashscope_fallback_api_keys(),
    )

    def validate(output: PackageSemanticReview) -> None:
        if {item.package_id for item in output.package_reviews} != expected_packages:
            raise ValueError("package review coverage mismatch")
        if {
            (item.left_package_id, item.right_package_id) for item in output.pair_reviews
        } != expected_pairs:
            raise ValueError("pair review coverage mismatch")
        if {item.candidate_id for item in output.external_candidate_reviews} != expected_external:
            raise ValueError("external candidate review coverage mismatch")
        for review in output.package_reviews:
            member_ids = set(package_by_id[review.package_id].member_event_ids)
            if not set(review.wrong_member_event_ids) <= member_ids:
                raise ValueError("review named an unknown Package member")

    result = client.complete(request)
    try:
        output = PackageSemanticReview.model_validate(result.payload)
        validate(output)
    except (ValidationError, ValueError) as exc:
        repair = StructuredModelRequest(
            system_prompt=system_prompt,
            user_prompt=json.dumps(
                {
                    "validation_error": type(exc).__name__,
                    "required_package_ids": sorted(expected_packages),
                    "required_pair_ids": [list(item) for item in sorted(expected_pairs)],
                    "required_external_candidate_ids": sorted(expected_external),
                    "previous_output": result.payload,
                    "original_input": user_payload,
                },
                ensure_ascii=False,
            ),
            json_schema=PackageSemanticReview.model_json_schema(),
        )
        repaired = client.complete(repair)
        output = PackageSemanticReview.model_validate(repaired.payload)
        validate(output)

    false_merge_packages = [
        item.package_id
        for item in output.package_reviews
        if not item.cohesive or item.wrong_member_event_ids
    ]
    same_pairs = [
        [item.left_package_id, item.right_package_id]
        for item in output.pair_reviews
        if item.relation == "SAME_PACKAGE"
    ]
    uncertain_pairs = sum(item.relation == "UNCERTAIN" for item in output.pair_reviews)
    correct_external = sum(
        item.appropriate_external_relation for item in output.external_candidate_reviews
    )
    external_accuracy = (
        None
        if not output.external_candidate_reviews
        else correct_external / len(output.external_candidate_reviews)
    )
    metrics = {
        "review_model": settings.model_m4,
        "review_status": "M4_ASSISTED_NOT_HUMAN_SIGNED",
        "package_count": len(packages),
        "noncohesive_package_count": len(false_merge_packages),
        "overexpansion_rate": (0.0 if not packages else len(false_merge_packages) / len(packages)),
        "candidate_pair_count": len(pair_ids),
        "missed_same_package_pair_count": len(same_pairs),
        "fragmentation_candidate_rate": (0.0 if not pair_ids else len(same_pairs) / len(pair_ids)),
        "uncertain_pair_count": uncertain_pairs,
        "external_candidate_count": len(output.external_candidate_reviews),
        "external_candidate_accuracy": external_accuracy,
        "false_merge_package_ids": false_merge_packages,
        "missed_same_package_pairs": same_pairs,
        "semantic_acceptance_passed": bool(
            not false_merge_packages
            and not same_pairs
            and uncertain_pairs == 0
            and (external_accuracy is None or external_accuracy >= 0.9)
        ),
    }
    artifact = {
        "report_version": "cdecr-package-n11-n13-m4-review-v2",
        "metrics": metrics,
        "review": output.model_dump(mode="json"),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(metrics, ensure_ascii=False), flush=True)
    return 0 if metrics["semantic_acceptance_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
