"""M4-assisted, item-level audit of the frozen 30-document N5.5/N6 result."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import uuid
from collections import Counter, defaultdict
from enum import StrEnum
from itertools import combinations
from pathlib import Path
from typing import Any

from pydantic import Field

from cdecr.config import CDECRSettings
from cdecr.contracts import StrictModel
from cdecr.kb_v2 import V2KnowledgeBase, deterministic_match
from cdecr.models import DashScopeStructuredModelClient, ModelTier
from cdecr.ports import StructuredModelRequest
from cdecr.registry import SQLiteCDECRRegistry

REVIEW_VERSION = "field-resolution-30-review-v1"
BATCH_SIZE = 6


class ReviewVerdict(StrEnum):
    CORRECT = "CORRECT"
    INCORRECT_LINK = "INCORRECT_LINK"
    MISSED_RESOLUTION = "MISSED_RESOLUTION"
    APPROPRIATE_UNRESOLVED = "APPROPRIATE_UNRESOLVED"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    AMBIGUOUS = "AMBIGUOUS"


class MemberOverride(StrictModel):
    item_id: str
    verdict: ReviewVerdict
    should_resolve: bool
    expected_external_id: str | None = None
    expected_canonical_label: str | None = None
    reason: str


class ReviewUnitDecision(StrictModel):
    unit_id: str
    verdict: ReviewVerdict
    should_resolve: bool
    expected_external_id: str | None = None
    expected_canonical_label: str | None = None
    member_overrides: list[MemberOverride] = Field(default_factory=list)
    reason: str

class ReviewBatch(StrictModel):
    decisions: list[ReviewUnitDecision]


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def _normalized(value: str) -> str:
    return "_".join(re.findall(r"[a-z0-9]+", value.casefold()))


def _unit_key(field: dict[str, Any]) -> tuple[str, str]:
    resolution = field["resolution"]
    if resolution is not None:
        return str(field["namespace"]), f"actual:{resolution['registry_id']}"
    return str(field["namespace"]), f"unresolved:{_normalized(str(field['raw_value']))}"


def _auto_exact(field: dict[str, Any]) -> dict[str, Any] | None:
    resolution = field["resolution"]
    if resolution is None or resolution["external_id"] is None:
        return None
    external_id = str(resolution["external_id"])
    if not any(item["external_id"] == external_id for item in field["kb_exact_matches"]):
        return None
    return {
        "item_id": field["item_id"],
        "review_source": "DETERMINISTIC_EXACT_KB",
        "verdict": ReviewVerdict.CORRECT.value,
        "should_resolve": True,
        "expected_external_id": external_id,
        "expected_canonical_label": f"external:{external_id}",
        "reason": "The routed value has a unique exact v2 KB match equal to the stored root.",
    }


def _build_units(fields: list[dict[str, Any]], auto_ids: set[str]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    all_by_root: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for field in fields:
        resolution = field["resolution"]
        if resolution is not None:
            all_by_root[str(resolution["registry_id"])].append(field)
        if field["item_id"] not in auto_ids:
            grouped[_unit_key(field)].append(field)
    units: list[dict[str, Any]] = []
    for index, ((namespace, key), members) in enumerate(sorted(grouped.items())):
        resolution = members[0]["resolution"]
        root_members = (
            all_by_root[str(resolution["registry_id"])] if resolution is not None else members
        )
        exact_candidates = {
            item["external_id"]: item
            for member in members
            for item in member["kb_exact_matches"]
        }
        units.append(
            {
                "unit_id": f"u{index:04d}",
                "namespace": namespace,
                "routed": all(bool(member["routed"]) for member in members),
                "actual_resolution": resolution,
                "member_count": len(members),
                "members": [
                    {
                        "item_id": member["item_id"],
                        "raw_value": member["raw_value"],
                        "source_title": member["source_title"],
                        "canonical_proposition": member["canonical_proposition"],
                        "local_context": str(member["local_context"])[:900],
                        "unrouted_reason": member.get("unrouted_reason"),
                    }
                    for member in members[:12]
                ],
                "all_member_item_ids": [member["item_id"] for member in members],
                "actual_cluster_surfaces": sorted(
                    {str(member["raw_value"]) for member in root_members}
                )[:30],
                "exact_kb_candidates": list(exact_candidates.values()),
                "group_key": key,
            }
        )
    return units


SYSTEM_PROMPT = """You audit canonical field resolution for financial-news event mentions.
Judge normalization only, not whether Grounder should have extracted the event. Different
namespaces must never corefer. A KB link is correct only when the field denotes that exact KB
object in context. A provisional cluster is correct only when all grouped surfaces denote the
same real-world object or concept; use member_overrides for contextual exceptions. Generic
references such as 'the company', vague aggregates, numeric/scalar attributes, and unsupported
free text may appropriately remain unresolved or OUT_OF_SCOPE. MISSED_RESOLUTION means the
field should have linked or joined a same-type object but did not. Use stable lowercase
snake_case English expected_canonical_label values so semantically identical objects receive
the same label across batches. expected_external_id may only be an ID shown in the unit's
actual resolution or exact_kb_candidates. Return exactly the requested JSON object."""


def _call_batch(
    *,
    client: DashScopeStructuredModelClient,
    registry: SQLiteCDECRRegistry,
    units: list[dict[str, Any]],
    known_labels: dict[str, list[str]],
    batch_number: int,
) -> list[ReviewUnitDecision]:
    requested_ids = {str(unit["unit_id"]) for unit in units}
    schema = ReviewBatch.model_json_schema()
    base_input = {
        "review_version": REVIEW_VERSION,
        "known_labels_by_namespace": known_labels,
        "units": units,
    }
    invalid: object | None = None
    for attempt in range(2):
        payload: dict[str, Any] = base_input
        if attempt:
            payload = {
                "request": base_input,
                "invalid_payload": invalid,
                "repair": "Return one decision per requested unit_id and valid member overrides.",
            }
        request = StructuredModelRequest(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            json_schema=schema,
        )
        call_id = str(uuid.uuid4())
        input_hash = hashlib.sha256(request.user_prompt.encode()).hexdigest()
        result = client.complete(request)
        try:
            parsed = ReviewBatch.model_validate(result.payload)
            returned_ids = {item.unit_id for item in parsed.decisions}
            if returned_ids != requested_ids or len(parsed.decisions) != len(units):
                raise ValueError("review output must cover each requested unit exactly once")
            members = {
                str(unit["unit_id"]): set(unit["all_member_item_ids"]) for unit in units
            }
            for decision in parsed.decisions:
                if any(
                    item.item_id not in members[decision.unit_id]
                    for item in decision.member_overrides
                ):
                    raise ValueError("review override references a foreign item_id")
        except (ValueError, TypeError) as exc:
            invalid = result.payload
            registry.record_model_call(
                model_call_id=call_id,
                run_id=None,
                tier="m4",
                model=result.model,
                status="FAILED",
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                latency_ms=result.latency_ms,
                error_code="invalid_review_output",
                metadata={"batch": batch_number, "attempt": attempt + 1},
                stage="field_resolution_review",
                prompt_version=REVIEW_VERSION,
                schema_hash=hashlib.sha256(json.dumps(schema, sort_keys=True).encode()).hexdigest(),
                input_hash=input_hash,
            )
            if attempt:
                raise RuntimeError("M4 review failed strict validation twice") from exc
            continue
        registry.record_model_call(
            model_call_id=call_id,
            run_id=None,
            tier="m4",
            model=result.model,
            status="SUCCEEDED",
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            latency_ms=result.latency_ms,
            error_code=None,
            metadata={"batch": batch_number, "attempt": attempt + 1},
            stage="field_resolution_review",
            prompt_version=REVIEW_VERSION,
            schema_hash=hashlib.sha256(json.dumps(schema, sort_keys=True).encode()).hexdigest(),
            input_hash=input_hash,
        )
        normalized: list[ReviewUnitDecision] = []
        for decision in parsed.decisions:
            if (
                decision.should_resolve
                and decision.verdict is not ReviewVerdict.AMBIGUOUS
                and not decision.expected_external_id
                and not decision.expected_canonical_label
            ):
                decision = decision.model_copy(
                    update={
                        "verdict": ReviewVerdict.AMBIGUOUS,
                        "should_resolve": False,
                        "reason": (
                            "Downgraded to AMBIGUOUS because the reviewer asserted resolvability "
                            "without supplying an expected identity. " + decision.reason
                        ),
                    }
                )
            normalized.append(decision)
        return normalized
    raise AssertionError("unreachable")


def _propagate(
    units: list[dict[str, Any]], decisions: list[ReviewUnitDecision]
) -> list[dict[str, Any]]:
    unit_by_id = {str(unit["unit_id"]): unit for unit in units}
    reviews: list[dict[str, Any]] = []
    for decision in decisions:
        unit = unit_by_id[decision.unit_id]
        default = {
            "review_source": "M4_ASSISTED",
            "verdict": decision.verdict.value,
            "should_resolve": decision.should_resolve,
            "expected_external_id": decision.expected_external_id,
            "expected_canonical_label": decision.expected_canonical_label,
            "reason": decision.reason,
        }
        overrides = {item.item_id: item for item in decision.member_overrides}
        for item_id in unit["all_member_item_ids"]:
            override = overrides.get(item_id)
            if override is None:
                reviews.append({"item_id": item_id, **default})
            else:
                reviews.append(
                    {
                        "item_id": item_id,
                        "review_source": "M4_ASSISTED_OVERRIDE",
                        "verdict": override.verdict.value,
                        "should_resolve": override.should_resolve,
                        "expected_external_id": override.expected_external_id,
                        "expected_canonical_label": override.expected_canonical_label,
                        "reason": override.reason,
                    }
                )
    return reviews


def _enrich_expected_external(
    field: dict[str, Any], review: dict[str, Any], kb: V2KnowledgeBase
) -> None:
    if review["expected_external_id"] or not review["expected_canonical_label"]:
        return
    catalog = str(field["catalog"])
    if not catalog:
        return
    matches = kb.lookup(
        catalog,
        str(review["expected_canonical_label"]),
        kind=field["kind"],
        company_id=field["company_id"],
    )
    unique = deterministic_match(str(review["expected_canonical_label"]), matches)
    if unique is not None:
        review["expected_external_id"] = unique.external_id


def _ratio(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else round(numerator / denominator, 4)


def _metrics(fields: list[dict[str, Any]], reviews: list[dict[str, Any]]) -> dict[str, Any]:
    review_by_id = {item["item_id"]: item for item in reviews}
    evaluated = [
        field for field in fields if review_by_id[field["item_id"]]["verdict"] != "AMBIGUOUS"
    ]
    linked = [field for field in evaluated if field["resolution"] is not None]
    correct_linked = [
        field for field in linked if review_by_id[field["item_id"]]["verdict"] == "CORRECT"
    ]
    expected = [field for field in evaluated if review_by_id[field["item_id"]]["should_resolve"]]
    kb_expected = [
        field
        for field in evaluated
        if review_by_id[field["item_id"]]["expected_external_id"] is not None
    ]
    kb_actual = [
        field
        for field in evaluated
        if field["resolution"] is not None and field["resolution"]["external_id"] is not None
    ]
    kb_correct = [
        field
        for field in kb_actual
        if field["resolution"]["external_id"]
        == review_by_id[field["item_id"]]["expected_external_id"]
    ]
    fc_expected = [
        field
        for field in expected
        if review_by_id[field["item_id"]]["expected_external_id"] is None
    ]
    fc_actual = [
        field
        for field in evaluated
        if field["resolution"] is not None and field["resolution"]["external_id"] is None
    ]
    fc_correct = [
        field for field in fc_actual if review_by_id[field["item_id"]]["verdict"] == "CORRECT"
    ]

    pair_items = [
        field
        for field in evaluated
        if review_by_id[field["item_id"]]["should_resolve"]
        and review_by_id[field["item_id"]]["expected_external_id"] is None
        and review_by_id[field["item_id"]]["expected_canonical_label"]
    ]
    tp = fp = fn = 0
    for left, right in combinations(pair_items, 2):
        if left["namespace"] != right["namespace"]:
            continue
        left_review, right_review = review_by_id[left["item_id"]], review_by_id[right["item_id"]]
        expected_same = (
            _normalized(str(left_review["expected_canonical_label"]))
            == _normalized(str(right_review["expected_canonical_label"]))
        )
        left_root = None if left["resolution"] is None else left["resolution"]["registry_id"]
        right_root = None if right["resolution"] is None else right["resolution"]["registry_id"]
        actual_same = left_root is not None and left_root == right_root
        tp += int(expected_same and actual_same)
        fp += int(not expected_same and actual_same)
        fn += int(expected_same and not actual_same)

    by_namespace: dict[str, dict[str, Any]] = {}
    for namespace in sorted({str(field["namespace"]) for field in fields}):
        subset = [field for field in evaluated if field["namespace"] == namespace]
        subset_linked = [field for field in subset if field["resolution"] is not None]
        subset_correct = [
            field
            for field in subset_linked
            if review_by_id[field["item_id"]]["verdict"] == "CORRECT"
        ]
        subset_expected = [
            field for field in subset if review_by_id[field["item_id"]]["should_resolve"]
        ]
        by_namespace[namespace] = {
            "fields": len(subset),
            "linked": len(subset_linked),
            "should_resolve": len(subset_expected),
            "correct_links": len(subset_correct),
            "precision": _ratio(len(subset_correct), len(subset_linked)),
            "recall": _ratio(len(subset_correct), len(subset_expected)),
        }
    verdicts = Counter(review_by_id[field["item_id"]]["verdict"] for field in fields)
    return {
        "reviewed_fields": len(fields),
        "verdicts": dict(sorted(verdicts.items())),
        "overall_link_precision": _ratio(len(correct_linked), len(linked)),
        "overall_resolution_recall": _ratio(len(correct_linked), len(expected)),
        "kb_link_precision": _ratio(len(kb_correct), len(kb_actual)),
        "kb_link_recall": _ratio(len(kb_correct), len(kb_expected)),
        "field_coreference_precision": _ratio(len(fc_correct), len(fc_actual)),
        "field_coreference_recall": _ratio(len(fc_correct), len(fc_expected)),
        "pairwise_field_coreference": {
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "precision": _ratio(tp, tp + fp),
            "recall": _ratio(tp, tp + fn),
            "f1": _ratio(2 * tp, 2 * tp + fp + fn),
        },
        "by_namespace": by_namespace,
    }


def main() -> int:
    args = _arguments()
    if args.output.exists():
        raise ValueError(f"refusing to overwrite existing output: {args.output}")
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    fields: list[dict[str, Any]] = payload["fields"]
    registry = SQLiteCDECRRegistry(args.registry)
    settings = CDECRSettings()
    api_key = settings.require_dashscope()
    client = DashScopeStructuredModelClient(
        tier=ModelTier.M4,
        api_key=api_key,
        fallback_api_keys=settings.dashscope_fallback_api_keys(),
        base_url=settings.dashscope_base_url,
        model=settings.model_m4,
        timeout_seconds=settings.model_timeout_seconds,
    )
    auto = [item for field in fields if (item := _auto_exact(field)) is not None]
    auto_ids = {str(item["item_id"]) for item in auto}
    units = _build_units(fields, auto_ids)
    checkpoint_path = args.output.with_suffix(".checkpoint.json")
    decisions: list[ReviewUnitDecision] = []
    known_labels: dict[str, list[str]] = defaultdict(list)
    if checkpoint_path.exists():
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        if checkpoint["input_sha256"] != hashlib.sha256(args.input.read_bytes()).hexdigest():
            raise ValueError("review checkpoint input hash does not match")
        decisions = [ReviewUnitDecision.model_validate(item) for item in checkpoint["decisions"]]
        for namespace, labels in checkpoint["known_labels"].items():
            known_labels[str(namespace)].extend(str(label) for label in labels)
    completed_units = len(decisions)
    for offset in range(0, len(units), BATCH_SIZE):
        if offset < completed_units:
            continue
        batch_number = offset // BATCH_SIZE + 1
        batch = units[offset : offset + BATCH_SIZE]
        result = _call_batch(
            client=client,
            registry=registry,
            units=batch,
            known_labels={key: values[-80:] for key, values in known_labels.items()},
            batch_number=batch_number,
        )
        decisions.extend(result)
        namespace_by_unit = {str(unit["unit_id"]): str(unit["namespace"]) for unit in batch}
        for decision in result:
            if decision.expected_canonical_label:
                labels = known_labels[namespace_by_unit[decision.unit_id]]
                if decision.expected_canonical_label not in labels:
                    labels.append(decision.expected_canonical_label)
        checkpoint_path.write_text(
            json.dumps(
                {
                    "input_sha256": hashlib.sha256(args.input.read_bytes()).hexdigest(),
                    "known_labels": dict(known_labels),
                    "decisions": [item.model_dump(mode="json") for item in decisions],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(
            json.dumps({"review_batch": batch_number, "units_complete": len(decisions)}),
            flush=True,
        )

    reviews = [*auto, *_propagate(units, decisions)]
    if len(reviews) != len(fields) or len({item["item_id"] for item in reviews}) != len(fields):
        raise RuntimeError("review propagation did not cover each field exactly once")
    kb = V2KnowledgeBase()
    fields_by_id = {field["item_id"]: field for field in fields}
    for review in reviews:
        _enrich_expected_external(fields_by_id[review["item_id"]], review, kb)
    metrics = _metrics(fields, reviews)
    output = {
        "review_version": REVIEW_VERSION,
        "input_sha256": hashlib.sha256(args.input.read_bytes()).hexdigest(),
        "review_model": settings.model_m4,
        "reasoning_effort": "none",
        "auto_exact_reviews": len(auto),
        "m4_review_units": len(units),
        "m4_batches": (len(units) + BATCH_SIZE - 1) // BATCH_SIZE,
        "metrics": metrics,
        "reviews": sorted(reviews, key=lambda item: item["item_id"]),
        "unit_decisions": [item.model_dump(mode="json") for item in decisions],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(metrics, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
