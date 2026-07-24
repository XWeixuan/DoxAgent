"""Consistency-correct an M4 audit and render the complete field/N6 review."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from cdecr_review_field_resolution_30 import _metrics


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, required=True)
    return parser.parse_args()


def _normalized(value: str) -> str:
    return "_".join(re.findall(r"[a-z0-9]+", value.casefold()))


def _surface_matches_expected(field: dict[str, Any], review: dict[str, Any]) -> bool:
    resolution = field["resolution"]
    expected = review["expected_canonical_label"]
    if resolution is None or not expected:
        return False
    expected_key = _normalized(str(expected))
    raw_matches = _normalized(str(field["raw_value"])) == expected_key
    actual_surfaces = [resolution["canonical_text"], *resolution["aliases"]]
    actual_matches = expected_key in {_normalized(str(value)) for value in actual_surfaces}
    return raw_matches and actual_matches


def _refine(field: dict[str, Any], review: dict[str, Any]) -> dict[str, Any]:
    item = dict(review)
    item["original_verdict"] = review["verdict"]
    item["refinement_reason"] = None
    resolution = field["resolution"]
    if review["review_source"] == "DETERMINISTIC_EXACT_KB":
        return item
    reason = str(review["reason"])
    if resolution is None and review["verdict"] == "INCORRECT_LINK":
        item["verdict"] = (
            "MISSED_RESOLUTION" if review["should_resolve"] else "APPROPRIATE_UNRESOLVED"
        )
        item["refinement_reason"] = "A null link cannot be an incorrect link."
    elif resolution is not None and review["verdict"] == "MISSED_RESOLUTION":
        item["verdict"] = "INCORRECT_LINK"
        item["refinement_reason"] = "A present link cannot be a missed resolution."
    if (
        field["namespace"] in {"metric", "concept.predicate"}
        and item["verdict"] == "INCORRECT_LINK"
        and _surface_matches_expected(field, item)
    ):
        item["verdict"] = "CORRECT"
        item["refinement_reason"] = (
            "The individual field denotes the metric/predicate concept named by both the raw "
            "value and canonical entry; cluster impurities are scored separately pairwise."
        )
    if item["verdict"] == "INCORRECT_LINK" and (
        "Thus CORRECT" in reason or "Verdict remains CORRECT" in reason
    ):
        item["verdict"] = "CORRECT"
        item["refinement_reason"] = "The reviewer rationale explicitly concluded CORRECT."
    if field["namespace"] == "fiscal_period" and resolution is None and item["should_resolve"]:
        item["verdict"] = "MISSED_RESOLUTION"
        item["expected_external_id"] = None
        item["expected_canonical_label"] = _normalized(str(field["raw_value"]))
        item["refinement_reason"] = (
            "Evaluate issuer-aware period linking against the extracted surface; do not rewrite "
            "the Grounder period to an unsupported fiscal year."
        )
    return item


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.2f}%"


def _escape(value: object, limit: int = 120) -> str:
    text = str(value).replace("|", "\\|").replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _render(
    runtime: dict[str, Any],
    reviews: list[dict[str, Any]],
    metrics: dict[str, Any],
) -> str:
    fields = runtime["fields"]
    review_by_id = {item["item_id"]: item for item in reviews}
    fields_by_message: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for field in fields:
        fields_by_message[str(field["message_id"])].append(field)
    identity_by_message: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for identity in runtime["identities"]:
        identity_by_message[str(identity["message_id"])].append(identity)

    lines = [
        "# CDECR 30 篇 Grounder→N6（跳过 Judge）完整字段审查",
        "",
        "## 总体指标",
        "",
        f"- 字段逐项审查：{metrics['reviewed_fields']} / {len(fields)}。",
        f"- Overall link precision：{_pct(metrics['overall_link_precision'])}。",
        f"- Overall resolution recall：{_pct(metrics['overall_resolution_recall'])}。",
        f"- KB Linking precision / recall：{_pct(metrics['kb_link_precision'])} / "
        f"{_pct(metrics['kb_link_recall'])}。",
        f"- Field Coreference field precision / recall："
        f"{_pct(metrics['field_coreference_precision'])} / "
        f"{_pct(metrics['field_coreference_recall'])}。",
        f"- Field Coreference pairwise precision / recall / F1："
        f"{_pct(metrics['pairwise_field_coreference']['precision'])} / "
        f"{_pct(metrics['pairwise_field_coreference']['recall'])} / "
        f"{_pct(metrics['pairwise_field_coreference']['f1'])}。",
        f"- N6 Schema complete：{runtime['summary']['identity_complete']} / "
        f"{len(runtime['identities'])}。",
        f"- N6 core-field clean：{metrics['n6']['core_field_clean']} / "
        f"{len(runtime['identities'])}；同时要求非空 principal："
        f"{metrics['n6']['strict_usable']} / {len(runtime['identities'])}。",
        "",
        "> 口径：M4 辅助逐项判定后，执行确定性一致性修订。metric 表示指标概念，",
        "> 不因数值不同而拆分；cluster 污染由 pairwise 指标单独计分。AMBIGUOUS 不进入",
        "> precision/recall 分母。本报告不是独立人工金标准。",
        "",
        "## Namespace 结果",
        "",
        "| Namespace | Fields | Linked | Should resolve | Correct | Precision | Recall |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for namespace, value in metrics["by_namespace"].items():
        lines.append(
            f"| {namespace} | {value['fields']} | {value['linked']} | "
            f"{value['should_resolve']} | {value['correct_links']} | "
            f"{_pct(value['precision'])} | {_pct(value['recall'])} |"
        )

    lines.extend(
        [
            "",
            "## 逐文档结果",
            "",
            "| # | Title | Fields | Correct | Incorrect | Missed | Appropriate unresolved | "
            "N6 complete |",
            "|---:|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for index, (message_id, document_fields) in enumerate(fields_by_message.items(), 1):
        verdicts = Counter(review_by_id[item["item_id"]]["verdict"] for item in document_fields)
        identities = identity_by_message[message_id]
        lines.append(
            f"| {index} | {_escape(document_fields[0]['source_title'], 80)} | "
            f"{len(document_fields)} | {verdicts['CORRECT']} | {verdicts['INCORRECT_LINK']} | "
            f"{verdicts['MISSED_RESOLUTION']} | {verdicts['APPROPRIATE_UNRESOLVED']} | "
            f"{sum(item['identity_profile'] is not None for item in identities)}/"
            f"{len(identities)} |"
        )

    cluster_fields: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for field in fields:
        if field["resolution"] is not None:
            cluster_fields[str(field["resolution"]["registry_id"])].append(field)
    bad_clusters: list[tuple[str, list[dict[str, Any]], set[str]]] = []
    for registry_id, members in cluster_fields.items():
        labels = {
            _normalized(str(review_by_id[item["item_id"]]["expected_canonical_label"]))
            for item in members
            if review_by_id[item["item_id"]]["expected_canonical_label"]
        }
        if len(labels) > 1:
            bad_clusters.append((registry_id, members, labels))
    bad_clusters.sort(key=lambda item: (-len(item[1]), item[0]))
    lines.extend(
        [
            "",
            "## 混杂 Cluster",
            "",
            "| Registry root | Namespace | Members | Expected labels | Surfaces |",
            "|---|---|---:|---|---|",
        ]
    )
    for registry_id, members, labels in bad_clusters:
        lines.append(
            f"| {_escape(registry_id, 28)} | {members[0]['namespace']} | {len(members)} | "
            f"{_escape(', '.join(sorted(labels)), 100)} | "
            f"{_escape(', '.join(sorted({str(item['raw_value']) for item in members})), 140)} |"
        )

    lines.extend(
        [
            "",
            "## 928 条逐字段审查",
            "",
            "| # | Document | Mention | Field | Namespace | Raw | Actual | Verdict | "
            "Expected | Reason |",
            "|---:|---|---|---|---|---|---|---|---|---|",
        ]
    )
    for index, field in enumerate(fields, 1):
        review = review_by_id[field["item_id"]]
        resolution = field["resolution"]
        actual = "UNRESOLVED"
        if resolution is not None:
            actual = str(resolution["external_id"] or resolution["canonical_text"])
        expected = review["expected_external_id"] or review["expected_canonical_label"] or "—"
        reason = str(review["reason"])
        if review["refinement_reason"]:
            reason = f"[consistency refinement] {review['refinement_reason']} M4: {reason}"
        lines.append(
            f"| {index} | {_escape(field['source_title'], 32)} | "
            f"{_escape(field['mention_id'], 18)} | {_escape(field['field_path'], 34)} | "
            f"{field['namespace']} | {_escape(field['raw_value'], 42)} | "
            f"{_escape(actual, 42)} | {review['verdict']} | {_escape(expected, 42)} | "
            f"{_escape(reason, 150)} |"
        )

    lines.extend(
        [
            "",
            "## 227 条 N6 Identity",
            "",
            "| # | Document | Mention | Schema | Missing | Processing key |",
            "|---:|---|---|---|---|---|",
        ]
    )
    for index, identity in enumerate(runtime["identities"], 1):
        profile = identity["identity_profile"]
        schema = "NONE" if profile is None else profile["schema_type"]
        lines.append(
            f"| {index} | {_escape(identity['source_title'], 44)} | "
            f"{_escape(identity['mention_id'], 22)} | {schema} | "
            f"{_escape(', '.join(identity['missing_required_fields']) or '—', 40)} | "
            f"{_escape(identity['processing_key'], 18)} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    args = _arguments()
    for path in (args.output_json, args.output_markdown):
        if path.exists():
            raise ValueError(f"refusing to overwrite existing output: {path}")
    runtime = json.loads(args.runtime.read_text(encoding="utf-8"))
    review_payload = json.loads(args.review.read_text(encoding="utf-8"))
    fields = runtime["fields"]
    fields_by_id = {item["item_id"]: item for item in fields}
    reviews = [
        _refine(fields_by_id[item["item_id"]], item) for item in review_payload["reviews"]
    ]
    metrics = _metrics(fields, reviews)
    review_by_id = {item["item_id"]: item for item in reviews}
    evaluated = [
        field for field in fields if review_by_id[field["item_id"]]["verdict"] != "AMBIGUOUS"
    ]
    linked = [field for field in evaluated if field["resolution"] is not None]
    expected = [field for field in evaluated if review_by_id[field["item_id"]]["should_resolve"]]
    metrics["counts"] = {
        "evaluated": len(evaluated),
        "linked": len(linked),
        "correct_linked": sum(
            review_by_id[field["item_id"]]["verdict"] == "CORRECT" for field in linked
        ),
        "should_resolve": len(expected),
        "kb_actual": sum(
            field["resolution"] is not None
            and field["resolution"]["external_id"] is not None
            for field in evaluated
        ),
        "kb_expected": sum(
            review_by_id[field["item_id"]]["expected_external_id"] is not None
            for field in evaluated
        ),
        "fc_actual": sum(
            field["resolution"] is not None
            and field["resolution"]["external_id"] is None
            for field in evaluated
        ),
        "fc_expected": sum(
            review_by_id[field["item_id"]]["should_resolve"]
            and review_by_id[field["item_id"]]["expected_external_id"] is None
            for field in evaluated
        ),
    }
    core_verdicts: dict[str, list[str]] = defaultdict(list)
    for field in fields:
        if (
            field["field_path"] == "predicate.normalized"
            or str(field["field_path"]).startswith("participants[")
            or str(field["field_path"]).startswith("locations[")
            or field["field_path"] == "time.reference_period_id"
        ):
            core_verdicts[str(field["mention_id"])].append(
                str(review_by_id[field["item_id"]]["verdict"])
            )
    acceptable = {"CORRECT", "APPROPRIATE_UNRESOLVED", "OUT_OF_SCOPE"}
    core_clean = 0
    strict_usable = 0
    empty_principal = 0
    for identity in runtime["identities"]:
        clean = all(
            verdict in acceptable for verdict in core_verdicts[str(identity["mention_id"])]
        )
        core_clean += int(clean)
        profile = identity["identity_profile"]
        principals = [] if profile is None else profile["fields"]["principal_participant_ids"]
        empty_principal += int(not principals)
        strict_usable += int(clean and bool(principals))
    metrics["n6"] = {
        "schema_complete": runtime["summary"]["identity_complete"],
        "core_field_clean": core_clean,
        "strict_usable": strict_usable,
        "empty_principal_participants": empty_principal,
    }
    output = {
        "review_version": "field-resolution-30-review-v1-consistency-corrected",
        "metrics": metrics,
        "refined_count": sum(item["refinement_reason"] is not None for item in reviews),
        "reviews": reviews,
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    args.output_markdown.write_text(_render(runtime, reviews, metrics), encoding="utf-8")
    print(json.dumps({"refined_count": output["refined_count"], **metrics}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
