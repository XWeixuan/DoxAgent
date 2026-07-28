"""Render a human-readable CDECR result document from frozen evaluation artifacts."""

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
    return f"{prefix}:{suffix[:8]}…" if suffix else identifier


def _cell(value: Any) -> str:
    return str(value or "—").replace("|", "\\|").replace("\n", " ")


def _evidence_status(mention: dict[str, Any]) -> str:
    records = mention.get("evidence_records") or []
    if not records:
        return "NO_EVIDENCE"
    statuses = sorted({str(record.get("status") or "UNKNOWN") for record in records})
    return "/".join(statuses)


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
        raw = quantity.get("raw_text")
        metric = quantity.get("metric_id")
        values.append(f"{raw or quantity.get('value')} [{metric or '—'}]")
    return "；".join(values) or "—"


def _time_text(mention: dict[str, Any]) -> str:
    time = mention.get("time") or {}
    period = time.get("reference_period_id")
    start = time.get("event_start")
    end = time.get("event_end")
    precision = time.get("precision")
    parts = [part for part in (period, start, end, precision) if part]
    return " / ".join(str(part) for part in parts) or "—"


def render(
    clusters: dict[str, Any],
    summary: dict[str, Any],
    atomic_review: dict[str, Any],
    package_review: dict[str, Any],
) -> str:
    mentions = {item["mention_id"]: item for item in clusters["mentions"]}
    atomic_by_mention: dict[str, dict[str, Any]] = {}
    atomic_by_id: dict[str, dict[str, Any]] = {}
    for wrapped in clusters["atomic_events"]:
        event = wrapped["event"]
        atomic_by_id[event["event_id"]] = event
        for mention_id in event.get("mention_ids") or []:
            atomic_by_mention[mention_id] = event

    package_by_atomic: dict[str, dict[str, Any]] = {}
    package_by_id: dict[str, dict[str, Any]] = {}
    for wrapped in clusters["event_packages"]:
        package = wrapped["package"]
        package_by_id[package["package_id"]] = package
        for event_id in package.get("member_event_ids") or []:
            package_by_atomic[event_id] = package

    flagged_atomics: dict[str, dict[str, Any]] = {}
    for review in atomic_review.get("multi_atomic_reviews") or []:
        if review.get("judgment") not in {"VALID", "VALID_SINGLE_FACT"}:
            flagged_atomics[review["atomic_id"]] = review

    false_merge_packages = {
        item["package_id"]: item
        for item in (
            package_review.get("atomic_pair_membership", {}).get("false_merge_packages") or []
        )
        if item.get("package_id")
    }

    execution = summary["execution"]
    tokens = summary["tokens"]
    human = summary["human_review"]
    lines = [
        "# CDECR 30 篇真实测试集：人类可读结果",
        "",
        "> 本文档展示本次冻结运行的实际输出，不是人工修正后的理想结果。"
        "带有“需复核”标记的 Atomic/Package 已由逐条人工审计确认存在边界风险；"
        "其余结果也不等同于业务真值。完整质量判断以配套验收报告为准。",
        "",
        "## 1. 运行概览",
        "",
        f"- 文档成功：{execution['document_success']['completed']}/"
        f"{execution['document_success']['total']}（100%）",
        f"- 跨文档成功：{execution['cross_document_success']['completed']}/"
        f"{execution['cross_document_success']['total']}（100%）",
        f"- 输出：{execution['outputs']['mentions']} Mention / "
        f"{execution['outputs']['atomic_events']} Atomic / "
        f"{execution['outputs']['packages']} Package",
        f"- Token：输入 {tokens['input']:,} / 输出 {tokens['output']:,} / "
        f"合计 {tokens['combined']:,}",
        f"- Mention P/R/F1：{human['mention']['micro_precision']:.2%} / "
        f"{human['mention']['micro_recall']:.2%} / {human['mention']['micro_f1']:.2%}",
        f"- Atomic Pair P/R/F1：{human['atomic']['precision']:.2%} / "
        f"{human['atomic']['recall']:.2%} / {human['atomic']['f1']:.2%}",
        f"- Package Pair P/R/F1：{human['package']['precision']:.2%} / "
        f"{human['package']['recall']:.2%} / {human['package']['f1']:.2%}",
        "",
        "## 2. 阅读说明",
        "",
        "- 每篇文档下列出该文档生成的全部 Mention，而不是新闻原文摘要。",
        "- `Evidence` 显示原始证据定位状态；`TEXT_NOT_FOUND` 仍按本轮容错协议保留。",
        "- `Atomic` 是系统最终归并结果；标注“需拆分”的 Atomic 存在已确认的过合并。",
        "- `Package` 是系统最终事件包；标注“边界错误”的 Package 存在已确认的误纳入。",
        "- 同一错误可能在多篇文档中重复出现，这是跨文档聚类的自然展示，不代表多个独立根因。",
        "",
        "## 3. 30 篇文档目录",
        "",
        "| # | 来源 | 标题 | Mention 数 |",
        "| ---: | --- | --- | ---: |",
    ]

    for index, document in enumerate(clusters["documents"], start=1):
        title = _cell(document.get("title"))
        url = document.get("url")
        linked_title = f"[{title}]({url})" if url else title
        lines.append(
            f"| {index} | {_cell(document.get('source_name'))} | {linked_title} | "
            f"{len(document.get('mention_ids') or [])} |"
        )

    lines.extend(["", "## 4. 按文档结果", ""])
    for index, document in enumerate(clusters["documents"], start=1):
        title = document.get("title") or "Untitled"
        lines.extend(
            [
                f"### {index}. {title}",
                "",
                f"- 来源：{document.get('source_name') or '—'}",
                f"- 发布时间：{document.get('published_at') or '—'}",
                f"- Message ID：`{document.get('message_id')}`",
                f"- 原文：{document.get('url') or '—'}",
                f"- Mention 数：{len(document.get('mention_ids') or [])}",
                "",
            ]
        )
        for mention_index, mention_id in enumerate(document.get("mention_ids") or [], start=1):
            mention = mentions[mention_id]
            atomic = atomic_by_mention.get(mention_id)
            atomic_id = atomic.get("event_id") if atomic else None
            package = package_by_atomic.get(atomic_id) if atomic_id else None
            package_id = package.get("package_id") if package else None
            atomic_flag = flagged_atomics.get(atomic_id or "")
            package_flag = false_merge_packages.get(package_id or "")

            lines.extend(
                [
                    f"#### {index}.{mention_index} {mention['canonical_proposition']}",
                    "",
                    f"- Mention：`{_short(mention_id)}`；Family："
                    f"`{mention.get('event_family') or '—'}`；Assertion："
                    f"`{mention.get('assertion_state') or '—'}`",
                    f"- 参与者：{_participants(mention)}",
                    f"- 数量：{_quantities(mention)}",
                    f"- 时间：{_time_text(mention)}",
                    f"- Evidence：`{_evidence_status(mention)}`",
                ]
            )
            if atomic:
                warning = ""
                if atomic_flag:
                    warning = (
                        f"；**需拆分复核**（该簇人工核定 FP pair="
                        f"{atomic_flag.get('predicted_fp_pairs', '—')}）"
                    )
                lines.append(
                    f"- Atomic：`{_short(atomic_id)}` — "
                    f"{atomic.get('canonical_proposition') or '—'}{warning}"
                )
            else:
                lines.append("- Atomic：—")
            if package:
                warning = ""
                if package_flag:
                    warning = (
                        f"；**边界错误**：{package_flag.get('reason') or '需人工复核'}"
                    )
                lines.append(
                    f"- Package：`{_short(package_id)}` — "
                    f"{package.get('canonical_title') or '—'}{warning}"
                )
            else:
                lines.append("- Package：—")

            evidence = mention.get("evidence_records") or []
            if evidence:
                quoted = str(evidence[0].get("text") or "").replace("\n", " ").strip()
                if len(quoted) > 280:
                    quoted = f"{quoted[:277]}…"
                lines.extend(["", f"> Evidence: {quoted}"])
            lines.append("")

    lines.extend(
        [
            "## 5. 最终 Package 索引",
            "",
            "| Package | Family / Kind | 成员 Atomic | 标题 | 审计标记 |",
            "| --- | --- | ---: | --- | --- |",
        ]
    )
    for wrapped in clusters["event_packages"]:
        package = wrapped["package"]
        package_id = package["package_id"]
        flag = false_merge_packages.get(package_id)
        audit = f"边界错误：{flag.get('reason')}" if flag else "未在已确认误包列表中"
        lines.append(
            f"| `{_short(package_id)}` | `{_cell(package.get('package_family'))}` / "
            f"`{_cell(package.get('package_kind'))}` | "
            f"{len(package.get('member_event_ids') or [])} | "
            f"{_cell(package.get('canonical_title'))} | {_cell(audit)} |"
        )

    lines.extend(
        [
            "",
            "## 6. 已确认的全局质量警告",
            "",
            f"- Atomic：预测正对 {human['atomic']['predicted_positive_pairs']}，"
            f"其中 FP {human['atomic']['fp']}；主要问题是不同 metric、交易时段、"
            "产品或业务动作被误认为同一事实。",
            f"- Package：预测正对 {human['package']['predicted_positive_pairs']}，"
            f"其中 FP {human['package']['fp']}、FN {human['package']['fn']}；"
            "主要问题是 reaction/earnings 边界和包碎片化。",
            f"- Field Resolution：{human['field_resolution']['correct']}/"
            f"{human['field_resolution']['required_and_persisted_links']} 正确；"
            f"UNRESOLVED 正确率 {human['field_resolution']['unresolved_accuracy']:.2%}。",
            "- 本文档用于人工浏览系统实际产物，不应替代 JSON、Registry 或逐条审计文件。",
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
