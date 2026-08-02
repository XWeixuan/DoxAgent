from __future__ import annotations

import argparse
import json
import re
import sqlite3
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

GENERIC_ANCHORS = {
    "business update",
    "company update",
    "earnings",
    "latest earnings",
    "latest report",
    "quarterly report",
    "report",
}
DISCLOSURE_FAMILIES = {
    "COMMERCIAL_OPERATION",
    "FINANCIAL_PERFORMANCE",
    "GOVERNANCE_PERSONNEL",
    "GUIDANCE_EXPECTATION",
    "PRODUCTION_SUPPLY",
    "REGULATORY_LEGAL_POLICY",
    "TRANSACTION_CAPITAL",
}
CONTAINER_WORDS = {
    "analysis",
    "call",
    "disclosure",
    "guidance",
    "listing",
    "plan",
    "policy",
    "release",
    "report",
    "results",
}


def _normalized(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold().replace("_", " ")
    return " ".join(re.sub(r"[^\w\s]", " ", value).split())


def _payload(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    parsed = json.loads(value)
    return parsed if isinstance(parsed, dict) else {}


def _resolve_redirect(
    connection: sqlite3.Connection,
    table: str,
    id_column: str,
    target_column: str,
    value: str,
) -> str:
    seen: set[str] = set()
    current = value
    while current not in seen:
        seen.add(current)
        row = connection.execute(
            f"SELECT {target_column} FROM {table} WHERE {id_column} = ?", (current,)
        ).fetchone()
        if row is None or not row[0]:
            return current
        current = str(row[0])
    return current


def _hint_is_valid(
    hint: str,
    *,
    proposition: str,
    family: str,
    participant_surfaces: list[str],
) -> tuple[bool, str | None]:
    normalized = _normalized(hint)
    if not normalized or normalized in GENERIC_ANCHORS:
        return False, "GENERIC_OR_EMPTY"
    if normalized in {_normalized(value) for value in participant_surfaces}:
        return False, "ENTITY_ONLY"
    if len(normalized.split()) == 1:
        return False, "ONE_TOKEN"
    if family == "MARKET_MOVEMENT" and re.search(
        r"\b(?:earnings|results?|release|call)\b", normalized
    ):
        return False, "REACTION_INHERITED_DISCLOSURE_PARENT"
    proposition_words = set(_normalized(proposition).split())
    hint_words = set(normalized.split())
    if (
        hint_words
        and hint_words.issubset(proposition_words)
        and not hint_words.intersection(CONTAINER_WORDS)
    ):
        return False, "MENTION_RESTATEMENT"
    return True, None


def _subject_surfaces(mention: dict[str, Any]) -> list[str]:
    participants = mention.get("participants")
    if not isinstance(participants, list):
        return []
    return [
        str(item["surface"])
        for item in participants
        if isinstance(item, dict) and item.get("surface")
    ]


def _expected_anchor(
    *,
    title: str,
    mention: dict[str, Any],
    valid_hint: bool,
    anchored_subjects: set[str],
) -> tuple[str, str]:
    if valid_hint:
        return "SUPPORTED", "VALID_OUTPUT_HINT"
    family = str(mention.get("event_family") or "")
    if family in {"MARKET_MOVEMENT", "ANALYST_ACTION"}:
        return "NOT_SUPPORTED", "REACTION_OR_ANALYST_FACT_HAS_NO_PROVEN_PARENT"
    subjects = {_normalized(value) for value in _subject_surfaces(mention)}
    if family in DISCLOSURE_FAMILIES and subjects.intersection(anchored_subjects):
        return "SUPPORTED", "SAME_SOURCE_SAME_SUBJECT_DISCLOSURE_SIBLING"
    title_normalized = _normalized(title)
    proposition_normalized = _normalized(str(mention.get("canonical_proposition") or ""))
    if (
        family in DISCLOSURE_FAMILIES
        and re.search(r"\b(?:earnings|quarter|results?)\b", title_normalized)
        and re.search(r"\bmicron\b", proposition_normalized)
    ):
        return "SUPPORTED", "SOURCE_TITLE_IDENTIFIES_EARNINGS_PARENT"
    return "AMBIGUOUS", "NO_CONSERVATIVE_PARENT_PROOF"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit CDECR Package Anchor propagation")
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    connection = sqlite3.connect(args.registry)
    connection.row_factory = sqlite3.Row

    sources = {
        str(row["message_id"]): _payload(row["payload_json"])
        for row in connection.execute("SELECT message_id, payload_json FROM source_messages")
    }
    grounder_hints: dict[str, str | None] = {}
    for row in connection.execute("SELECT payload_json FROM grounder_batch_results"):
        for draft in _payload(row["payload_json"]).get("drafts", []):
            if not isinstance(draft, dict) or not draft.get("draft_id"):
                continue
            mention: dict[str, Any] = (
                draft["mention"] if isinstance(draft.get("mention"), dict) else {}
            )
            hint = mention.get("local_package_hint")
            grounder_hints[str(draft["draft_id"])] = (
                str(hint.get("anchor")) if isinstance(hint, dict) and hint.get("anchor") else None
            )

    derivations: dict[str, dict[str, Any]] = {}
    for row in connection.execute(
        "SELECT subject_id, payload_json FROM decision_audits "
        "WHERE decision_type = 'MENTION_DERIVATION'"
    ):
        derivations[str(row["subject_id"])] = _payload(row["payload_json"])

    field_links: dict[str, str] = {}
    for row in connection.execute(
        "SELECT mention_id, registry_id FROM canonical_field_links "
        "WHERE field_path = 'local_package_hint.anchor'"
    ):
        field_links[str(row["mention_id"])] = _resolve_redirect(
            connection,
            "canonical_field_registry",
            "id",
            "redirect_to",
            str(row["registry_id"]),
        )

    mention_rows: list[dict[str, Any]] = []
    by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    raw_mentions: list[tuple[str, str, dict[str, Any]]] = []
    for row in connection.execute(
        "SELECT mention_id, message_id, payload_json FROM event_mentions "
        "ORDER BY message_id, mention_id"
    ):
        mention = _payload(row["payload_json"])
        raw_mentions.append((str(row["mention_id"]), str(row["message_id"]), mention))
        by_source[str(row["message_id"])].append(mention)

    anchored_subjects_by_source: dict[str, set[str]] = defaultdict(set)
    for _, message_id, mention in raw_mentions:
        hint = mention.get("local_package_hint")
        anchor = str(hint.get("anchor")) if isinstance(hint, dict) and hint.get("anchor") else ""
        valid, _ = (
            _hint_is_valid(
                anchor,
                proposition=str(mention.get("canonical_proposition") or ""),
                family=str(mention.get("event_family") or ""),
                participant_surfaces=_subject_surfaces(mention),
            )
            if anchor
            else (False, None)
        )
        if valid:
            anchored_subjects_by_source[message_id].update(
                _normalized(value) for value in _subject_surfaces(mention)
            )

    inheritance_eligible = 0
    inheritance_success = 0
    for mention_id, message_id, mention in raw_mentions:
        hint = mention.get("local_package_hint")
        actual_hint = (
            str(hint.get("anchor")) if isinstance(hint, dict) and hint.get("anchor") else None
        )
        valid_hint, invalid_reason = (
            _hint_is_valid(
                actual_hint,
                proposition=str(mention.get("canonical_proposition") or ""),
                family=str(mention.get("event_family") or ""),
                participant_surfaces=_subject_surfaces(mention),
            )
            if actual_hint
            else (False, None)
        )
        derivation = derivations.get(mention_id, {})
        draft_ids = [
            str(draft_id)
            for item in derivation.get("derivations", [])
            if isinstance(item, dict)
            for draft_id in item.get("grounder_draft_ids", [])
        ]
        draft_anchor_values = sorted(
            {
                anchor
                for draft_id in draft_ids
                for anchor in [grounder_hints.get(draft_id)]
                if anchor is not None
            }
        )
        family = str(mention.get("event_family") or "")
        inheritance_expected = bool(
            draft_anchor_values and family not in {"MARKET_MOVEMENT", "ANALYST_ACTION"}
        )
        if inheritance_expected:
            inheritance_eligible += 1
            if actual_hint:
                inheritance_success += 1
        source = sources.get(message_id, {})
        expected, expected_reason = _expected_anchor(
            title=str(source.get("title") or ""),
            mention=mention,
            valid_hint=valid_hint,
            anchored_subjects=anchored_subjects_by_source[message_id],
        )
        if inheritance_expected and not actual_hint:
            lost_stage = "JUDGE_OR_MATERIALIZATION"
        elif expected == "SUPPORTED" and not actual_hint:
            lost_stage = "GROUNDER"
        elif actual_hint and mention_id not in field_links:
            lost_stage = "N11"
        else:
            lost_stage = None
        item = {
            "mention_id": mention_id,
            "message_id": message_id,
            "source_title": source.get("title"),
            "event_family": mention.get("event_family"),
            "canonical_proposition": mention.get("canonical_proposition"),
            "expected_anchor": expected,
            "expected_reason": expected_reason,
            "actual_hint": actual_hint,
            "relation_to_anchor": (
                hint.get("relation_to_anchor") if isinstance(hint, dict) else None
            ),
            "valid_hint": valid_hint if actual_hint else None,
            "invalid_reason": invalid_reason,
            "grounder_hints": draft_anchor_values,
            "canonical_anchor_id": field_links.get(mention_id),
            "lost_stage": lost_stage,
        }
        mention_rows.append(item)

    supported = [row for row in mention_rows if row["expected_anchor"] == "SUPPORTED"]
    hint_rows = [row for row in mention_rows if row["actual_hint"]]
    valid_rows = [row for row in hint_rows if row["valid_hint"]]
    missing_supported = [row for row in supported if not row["valid_hint"]]
    invalid_rows = [row for row in hint_rows if not row["valid_hint"]]

    package_rows: list[dict[str, Any]] = []
    package_roots: set[str] = set()
    for row in connection.execute("SELECT package_id FROM event_package_heads"):
        package_roots.add(
            _resolve_redirect(
                connection,
                "package_redirects",
                "source_package_id",
                "target_package_id",
                str(row["package_id"]),
            )
        )
    for package_id in sorted(package_roots):
        head = connection.execute(
            "SELECT current_version FROM event_package_heads WHERE package_id = ?",
            (package_id,),
        ).fetchone()
        if head is None:
            continue
        version = connection.execute(
            "SELECT payload_json FROM event_package_versions WHERE package_id = ? AND version = ?",
            (package_id, head[0]),
        ).fetchone()
        if version is None:
            continue
        package = _payload(version[0])
        canonical_members = {
            _resolve_redirect(
                connection,
                "atomic_event_redirects",
                "source_event_id",
                "target_event_id",
                str(event_id),
            )
            for event_id in package.get("member_event_ids", [])
        }
        package_rows.append(
            {
                "package_id": package_id,
                "title": package.get("canonical_title"),
                "canonical_member_count": len(canonical_members),
                "anchor_count": len(set(package.get("package_anchor_ids", []))),
                "anchor_conflict": bool(package.get("anchor_conflict")),
                "primary_anchor_id": package.get("primary_anchor_id"),
            }
        )

    summary = {
        "mention_count": len(mention_rows),
        "hint_count": len(hint_rows),
        "hint_coverage": len(hint_rows) / len(mention_rows) if mention_rows else 0.0,
        "conservative_supported_count": len(supported),
        "conservative_anchor_recall": (
            (len(supported) - len(missing_supported)) / len(supported) if supported else None
        ),
        "syntactic_semantic_proxy_precision": (
            len(valid_rows) / len(hint_rows) if hint_rows else None
        ),
        "missing_supported_count": len(missing_supported),
        "invalid_hint_count": len(invalid_rows),
        "judge_inheritance_eligible": inheritance_eligible,
        "judge_inheritance_success": inheritance_success,
        "judge_inheritance_rate": (
            inheritance_success / inheritance_eligible if inheritance_eligible else None
        ),
        "canonical_anchor_link_count": len(field_links),
        "canonical_anchor_root_count": len(set(field_links.values())),
        "package_count": len(package_rows),
        "package_anchor_count_distribution": dict(
            sorted(Counter(row["anchor_count"] for row in package_rows).items())
        ),
        "package_anchor_conflicts": sum(row["anchor_conflict"] for row in package_rows),
        "max_package_anchor_count": max((row["anchor_count"] for row in package_rows), default=0),
        "invalid_reason_distribution": dict(
            sorted(Counter(row["invalid_reason"] for row in invalid_rows).items())
        ),
        "missing_reason_distribution": dict(
            sorted(Counter(row["expected_reason"] for row in missing_supported).items())
        ),
        "methodology_warning": (
            "SUPPORTED/valid_hint are conservative deterministic review proxies, not an "
            "independently labelled Anchor Gold; do not use them as a release gate."
        ),
    }
    report = {
        "report_version": "cdecr-anchor-audit-v1",
        "summary": summary,
        "mentions": mention_rows,
        "packages": package_rows,
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [
        "# CDECR Anchor 离线审计",
        "",
        "> 本报告读取固定 Registry，不调用模型、不修改业务数据。`SUPPORTED` 与 "
        "`valid_hint` 是保守的确定性复核代理，不是独立人工 Anchor Gold，因此只用于定位，"
        "不能作为发布门。",
        "",
        "## 汇总",
        "",
        "| 指标 | 结果 |",
        "| --- | ---: |",
        f"| Mention | {summary['mention_count']} |",
        f"| 有 hint | {summary['hint_count']} ({summary['hint_coverage']:.2%}) |",
        f"| 保守 SUPPORTED | {summary['conservative_supported_count']} |",
        f"| 保守 Anchor Recall 代理 | {summary['conservative_anchor_recall']:.2%} |",
        f"| Anchor Precision 代理 | {summary['syntactic_semantic_proxy_precision']:.2%} |",
        f"| missing SUPPORTED | {summary['missing_supported_count']} |",
        f"| invalid hint | {summary['invalid_hint_count']} |",
        f"| Judge 继承 | {inheritance_success}/{inheritance_eligible} "
        f"({summary['judge_inheritance_rate']:.2%}) |",
        f"| canonical Anchor links / roots | {len(field_links)} / "
        f"{len(set(field_links.values()))} |",
        f"| Package Anchor conflict | {summary['package_anchor_conflicts']}/"
        f"{summary['package_count']} |",
        "",
        "## Missing SUPPORTED（按文档聚合）",
        "",
        "| 文档 | 数量 | 代表缺失事实 |",
        "| --- | ---: | --- |",
    ]
    missing_by_title: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in missing_supported:
        missing_by_title[str(row["source_title"] or row["message_id"])].append(row)
    for title, rows in sorted(missing_by_title.items(), key=lambda item: (-len(item[1]), item[0])):
        sample = str(rows[0]["canonical_proposition"] or "").replace("|", "\\|")
        safe_title = title.replace("|", "\\|")
        lines.append(f"| {safe_title} | {len(rows)} | {sample} |")
    lines.extend(
        [
            "",
            "## Invalid hint",
            "",
            "| 文档 | Mention | hint | 原因 |",
            "| --- | --- | --- | --- |",
        ]
    )
    for row in invalid_rows:
        safe_title = str(row["source_title"]).replace("|", "\\|")
        safe_hint = str(row["actual_hint"]).replace("|", "\\|")
        lines.append(
            f"| {safe_title} | {row['mention_id']} | {safe_hint} | {row['invalid_reason']} |"
        )
    lines.extend(
        [
            "",
            "## Package Anchor 分布",
            "",
            f"- Anchor 数量分布：`{summary['package_anchor_count_distribution']}`",
            f"- 最大 Package Anchor 数：{summary['max_package_anchor_count']}",
            f"- 冲突 Package：{summary['package_anchor_conflicts']} 个。冲突集合会保留，"
            "但 N12 card 只暴露最多 4 个 Anchor。",
            "",
            "完整逐 Mention 明细见同名 JSON。",
        ]
    )
    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
