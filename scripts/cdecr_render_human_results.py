"""Render the final CDECR Package -> Atomic -> Mention hierarchy."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _short(identifier: str | None) -> str:
    if not identifier:
        return "—"
    prefix, _, suffix = identifier.partition(":")
    return f"{prefix}:{suffix[:10]}…" if suffix else identifier


def _clean(value: Any) -> str:
    return str(value or "—").replace("\n", " ").strip()


def _cell(value: Any) -> str:
    return _clean(value).replace("|", "\\|")


def _evidence_status(mention: dict[str, Any]) -> str:
    records = mention.get("evidence_records") or []
    if not records:
        return "NO_EVIDENCE"
    return "/".join(
        sorted({str(record.get("status") or "UNKNOWN") for record in records})
    )


def _participants(mention: dict[str, Any]) -> str:
    values = []
    for participant in mention.get("participants") or []:
        surface = participant.get("surface") or participant.get("entity_id")
        if surface:
            values.append(f"{surface} ({participant.get('role') or '—'})")
    return "；".join(values) or "—"


def _quantities(mention: dict[str, Any]) -> str:
    values = []
    for quantity in mention.get("quantities") or []:
        raw = quantity.get("raw_text") or quantity.get("value")
        values.append(f"{raw} [{quantity.get('metric_id') or '—'}]")
    return "；".join(values) or "—"


def _time_text(item: dict[str, Any]) -> str:
    time = item.get("time") or {}
    values = [
        time.get("reference_period_id"),
        time.get("event_start"),
        time.get("event_end"),
        time.get("precision"),
    ]
    return " / ".join(str(value) for value in values if value) or "—"


def render(
    clusters: dict[str, Any],
    summary: dict[str, Any],
    atomic_review: dict[str, Any],
    package_review: dict[str, Any],
) -> str:
    documents = {item["message_id"]: item for item in clusters["documents"]}
    mentions = {item["mention_id"]: item for item in clusters["mentions"]}
    atomics = {
        item["event"]["event_id"]: item["event"] for item in clusters["atomic_events"]
    }

    flagged_atomics = {
        item["atomic_id"]: item
        for item in atomic_review.get("multi_atomic_reviews") or []
        if item.get("judgment") not in {"VALID", "VALID_SINGLE_FACT"}
    }
    false_merge_packages = {
        item["package_id"]: item
        for item in (
            package_review.get("atomic_pair_membership", {}).get("false_merge_packages")
            or []
        )
        if item.get("package_id")
    }

    execution = summary["execution"]
    human = summary["human_review"]
    tokens = summary["tokens"]
    lines = [
        "# CDECR 30 篇真实测试集：最终层级结果",
        "",
        "> 本文档按照 CDECR 的最终业务产物组织："
        "`Package → Atomic Event → Event Mention → Source Evidence`。"
        "文章仅作为 Mention 的来源与审计坐标，不作为结果层级。",
        "",
        "> 本文档展示冻结运行的实际结果，而非人工修正后的 Gold。"
        "“边界错误”与“需拆分”是逐条审计已确认的问题。",
        "",
        "## 1. 总览",
        "",
        f"- 输入文档：{execution['document_success']['total']}；成功："
        f"{execution['document_success']['completed']}/"
        f"{execution['document_success']['total']}",
        f"- 最终层级：{execution['outputs']['packages']} Package → "
        f"{execution['outputs']['atomic_events']} Atomic → "
        f"{execution['outputs']['mentions']} Mention",
        f"- Token：输入 {tokens['input']:,} / 输出 {tokens['output']:,} / "
        f"合计 {tokens['combined']:,}",
        f"- Mention P/R/F1：{human['mention']['micro_precision']:.2%} / "
        f"{human['mention']['micro_recall']:.2%} / "
        f"{human['mention']['micro_f1']:.2%}",
        f"- Atomic Pair P/R/F1：{human['atomic']['precision']:.2%} / "
        f"{human['atomic']['recall']:.2%} / {human['atomic']['f1']:.2%}",
        f"- Package Pair P/R/F1：{human['package']['precision']:.2%} / "
        f"{human['package']['recall']:.2%} / {human['package']['f1']:.2%}",
        "",
        "## 2. Package 索引",
        "",
        "| # | Package | Family / Kind | Atomic | Mention | 标题 | 审计 |",
        "| ---: | --- | --- | ---: | ---: | --- | --- |",
    ]

    packages = clusters["event_packages"]
    for index, wrapped in enumerate(packages, start=1):
        package = wrapped["package"]
        event_ids = package.get("member_event_ids") or []
        mention_count = sum(
            len(atomics[event_id].get("mention_ids") or [])
            for event_id in event_ids
            if event_id in atomics
        )
        flag = false_merge_packages.get(package["package_id"])
        audit = "边界错误" if flag else "—"
        lines.append(
            f"| {index} | `{_short(package['package_id'])}` | "
            f"`{_cell(package.get('package_family'))}` / "
            f"`{_cell(package.get('package_kind'))}` | {len(event_ids)} | "
            f"{mention_count} | {_cell(package.get('canonical_title'))} | {audit} |"
        )

    lines.extend(["", "## 3. Package → Atomic → Mention 最终结构", ""])
    seen_atomics: set[str] = set()
    seen_mentions: set[str] = set()

    for package_index, wrapped in enumerate(packages, start=1):
        package = wrapped["package"]
        package_id = package["package_id"]
        event_ids = package.get("member_event_ids") or []
        package_flag = false_merge_packages.get(package_id)
        package_mention_count = sum(
            len(atomics[event_id].get("mention_ids") or [])
            for event_id in event_ids
            if event_id in atomics
        )
        lines.extend(
            [
                f"### P{package_index:02d}. {package.get('canonical_title') or 'Untitled Package'}",
                "",
                f"- Package ID：`{package_id}`",
                f"- Family / Kind：`{package.get('package_family') or '—'}` / "
                f"`{package.get('package_kind') or '—'}`",
                f"- 状态：`{package.get('status') or '—'}`；Quality："
                f"`{package.get('quality_state') or '—'}`；Version："
                f"{package.get('version') or '—'}",
                f"- 层级规模：{len(event_ids)} Atomic / {package_mention_count} Mention",
                f"- Anchor entities：{', '.join(package.get('anchor_entities') or []) or '—'}",
                f"- Anchor artifact / period："
                f"{package.get('anchor_artifact_id') or '—'} / "
                f"{package.get('anchor_period_id') or '—'}",
                f"- 摘要：{package.get('canonical_summary') or '—'}",
            ]
        )
        if package_flag:
            lines.append(
                f"- **人工审计：边界错误。** "
                f"{package_flag.get('reason') or 'Package membership 需要修正。'}"
            )
        lines.append("")

        for atomic_index, event_id in enumerate(event_ids, start=1):
            atomic = atomics[event_id]
            seen_atomics.add(event_id)
            atomic_flag = flagged_atomics.get(event_id)
            lines.extend(
                [
                    f"#### P{package_index:02d}-A{atomic_index:02d}. "
                    f"{atomic.get('canonical_proposition') or 'Untitled Atomic'}",
                    "",
                    f"- Atomic ID：`{event_id}`",
                    f"- Family / Assertion：`{atomic.get('event_family') or '—'}` / "
                    f"`{atomic.get('assertion_state') or '—'}`",
                    f"- Mention 数：{len(atomic.get('mention_ids') or [])}；"
                    f"Version：{atomic.get('version') or '—'}",
                    f"- 时间：{_time_text(atomic)}",
                ]
            )
            if atomic_flag:
                partition_labels = ", ".join(atomic_flag.get("partition_labels") or [])
                lines.append(
                    f"- **人工审计：需拆分。** 该 Atomic 产生 "
                    f"{atomic_flag.get('predicted_fp_pairs', '—')} 个 FP pair"
                    + (f"；建议分区：{partition_labels}" if partition_labels else "")
                )
            lines.append("")

            for mention_index, mention_id in enumerate(
                atomic.get("mention_ids") or [], start=1
            ):
                mention = mentions[mention_id]
                seen_mentions.add(mention_id)
                document = documents.get(mention["message_id"], {})
                source_title = document.get("title") or document.get("source_name") or "—"
                url = document.get("url")
                source = f"[{source_title}]({url})" if url else source_title
                lines.extend(
                    [
                        f"##### M{mention_index:02d}. "
                        f"{mention.get('canonical_proposition') or 'Untitled Mention'}",
                        "",
                        f"- Mention ID：`{mention_id}`",
                        f"- 来源：{source}；`{mention.get('message_id')}`",
                        f"- Family / Assertion：`{mention.get('event_family') or '—'}` / "
                        f"`{mention.get('assertion_state') or '—'}`",
                        f"- 参与者：{_participants(mention)}",
                        f"- 数量：{_quantities(mention)}",
                        f"- 时间：{_time_text(mention)}",
                        f"- Evidence 状态：`{_evidence_status(mention)}`",
                    ]
                )
                for evidence_index, record in enumerate(
                    mention.get("evidence_records") or [], start=1
                ):
                    text = _clean(record.get("text"))
                    if len(text) > 360:
                        text = f"{text[:357]}…"
                    lines.append(
                        f"  - E{evidence_index:02d} "
                        f"`{record.get('status') or 'UNKNOWN'}` / "
                        f"`{record.get('segment_id') or '—'}`：{text}"
                    )
                lines.append("")

    missing_atomics = set(atomics) - seen_atomics
    missing_mentions = set(mentions) - seen_mentions
    lines.extend(
        [
            "## 4. 层级完整性与质量警告",
            "",
            f"- Package 覆盖：{len(packages)}/{execution['outputs']['packages']}",
            f"- Atomic 唯一覆盖：{len(seen_atomics)}/"
            f"{execution['outputs']['atomic_events']}；未归包：{len(missing_atomics)}",
            f"- Mention 唯一覆盖：{len(seen_mentions)}/"
            f"{execution['outputs']['mentions']}；未归 Atomic：{len(missing_mentions)}",
            f"- 已确认 Atomic FP pair：{human['atomic']['fp']}；"
            "主要为不同 metric、交易时段、产品或业务动作的过合并。",
            f"- 已确认 Package FP/FN pair：{human['package']['fp']}/"
            f"{human['package']['fn']}；主要为 reaction/earnings 边界错误和包碎片化。",
            "- 本文档是运行结果的业务层级展示；人工 Gold、节点指标与根因详见配套验收报告。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--clusters", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--atomic-review", type=Path, required=True)
    parser.add_argument("--package-review", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    output = render(
        _load(args.clusters),
        _load(args.summary),
        _load(args.atomic_review),
        _load(args.package_review),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(output, encoding="utf-8", newline="\n")
    print(f"wrote {args.output} ({len(output.splitlines())} lines)")


if __name__ == "__main__":
    main()
