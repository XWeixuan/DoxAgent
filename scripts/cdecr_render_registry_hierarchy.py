"""Render a frozen CDECR Registry as Package -> Atomic -> Mention Markdown."""

# Markdown lines intentionally preserve compact human-readable fields.
# ruff: noqa: E501

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any


def _text(value: Any, default: str = "—") -> str:
    if value is None or value == "":
        return default
    return str(value).replace("\n", " ").strip()


def _cell(value: Any) -> str:
    return _text(value).replace("|", "\\|")


def _short(identifier: str) -> str:
    prefix, separator, suffix = identifier.partition(":")
    return f"{prefix}:{suffix[:12]}…" if separator else identifier[:18]


def _time(value: dict[str, Any] | None) -> str:
    value = value or {}
    parts = [
        value.get("reference_period_id"),
        value.get("event_start"),
        value.get("event_end"),
        value.get("precision"),
    ]
    return " / ".join(_text(item) for item in parts if item) or "—"


def _participants(value: list[dict[str, Any]] | None) -> str:
    result = []
    for item in value or []:
        name = item.get("surface") or item.get("entity_id") or "—"
        result.append(f"{name} ({item.get('role') or '—'})")
    return "；".join(result) or "—"


def _quantities(value: list[dict[str, Any]] | None) -> str:
    result = []
    for item in value or []:
        raw = item.get("raw_text") or item.get("value") or "—"
        metric = item.get("metric_id") or "—"
        unit = item.get("unit") or "—"
        result.append(f"{raw} [{metric}, {unit}, {item.get('role') or '—'}]")
    return "；".join(result) or "—"


def _evidence(mention: dict[str, Any]) -> list[str]:
    result = []
    for index, item in enumerate(mention.get("evidence_records") or [], start=1):
        text = _text(item.get("text"))
        if len(text) > 360:
            text = f"{text[:357]}…"
        result.append(
            f"E{index:02d} `{_text(item.get('status'))}` "
            f"`{_text(item.get('segment_id'))}`：{text}"
        )
    return result or ["—"]


def render(
    registry: Path,
    *,
    title: str = "CDECR 300 篇 MU 新闻：真实工作流层级结果",
    state_note: str = (
        "N12 + Wave C 已落库；N13 pair Decide、N13 Apply、finalize 尚未执行；"
        "这是中断时的真实结果，不是完整终态。"
    ),
) -> str:
    connection = sqlite3.connect(f"file:{registry}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row

    mentions = {
        str(row["mention_id"]): json.loads(str(row["payload_json"]))
        for row in connection.execute("SELECT mention_id, payload_json FROM event_mentions")
    }
    sources = {
        str(row["message_id"]): json.loads(str(row["payload_json"]))
        for row in connection.execute("SELECT message_id, payload_json FROM source_messages")
    }
    atomics = {
        str(row["event_id"]): json.loads(str(row["payload_json"]))
        for row in connection.execute(
            """
            SELECT v.event_id, v.payload_json
            FROM atomic_event_heads h
            JOIN atomic_event_versions v
              ON v.event_id = h.event_id AND v.version = h.current_version
            """
        )
    }
    packages = {
        str(row["package_id"]): json.loads(str(row["payload_json"]))
        for row in connection.execute(
            """
            SELECT v.package_id, v.payload_json
            FROM event_package_heads h
            JOIN event_package_versions v
              ON v.package_id = h.package_id AND v.version = h.current_version
            """
        )
    }
    package_events: dict[str, list[str]] = defaultdict(list)
    for row in connection.execute(
        "SELECT package_id, event_id FROM active_package_memberships ORDER BY package_id, event_id"
    ):
        package_events[str(row["package_id"])].append(str(row["event_id"]))
    atomic_mentions: dict[str, list[str]] = defaultdict(list)
    for row in connection.execute(
        """
        SELECT h.event_id, m.mention_id
        FROM atomic_event_heads h
        JOIN atomic_event_mentions m
          ON m.event_id = h.event_id AND m.event_version = h.current_version
        ORDER BY h.event_id, m.mention_id
        """
    ):
        atomic_mentions[str(row["event_id"])].append(str(row["mention_id"]))
    active_package_ids = set(package_events)
    active_packages = {
        package_id: packages.get(
            package_id,
            {
                "package_id": package_id,
                "package_family": None,
                "package_kind": None,
                "member_event_ids": event_ids,
            },
        )
        for package_id, event_ids in package_events.items()
        if package_id in active_package_ids
    }
    lines = [
        f"# {title}",
        "",
        f"> 数据来源：`{registry.name}` 的真实 Registry；未读取 Gold，未进行人工改写。",
        f"> 当前状态：{state_note}",
        "> 本文档按真实 **Package → Atomic → Mention → Evidence** 层级展示，不使用 Gold 投影。",
        "",
        f"- Package：{len(active_packages)}（仅 active membership；Package heads 总数 {len(packages)}）",
        f"- Atomic：{len(atomics)}；Mention：{len(mentions)}；Source：{len(sources)}",
        "",
        "## Package 索引",
        "",
        "| # | Package | Family / Kind | Atomic | Mention | Title | Anchor |",
        "| ---: | --- | --- | ---: | ---: | --- | --- |",
    ]
    ordered_packages = sorted(
        active_packages.items(),
        key=lambda item: (
            _text(item[1].get("package_family")),
            _text(item[1].get("canonical_title")),
            item[0],
        ),
    )
    for index, (package_id, package) in enumerate(ordered_packages, start=1):
        event_ids = package_events.get(package_id, list(package.get("member_event_ids") or []))
        mention_count = sum(len(atomic_mentions.get(event_id, [])) for event_id in event_ids)
        anchors = package.get("primary_anchor_id") or ", ".join(package.get("package_anchor_ids") or [])
        lines.append(
            f"| {index} | `{_short(package_id)}` | "
            f"`{_cell(package.get('package_family'))}` / `{_cell(package.get('package_kind'))}` | "
            f"{len(event_ids)} | {mention_count} | {_cell(package.get('canonical_title'))} | "
            f"`{_cell(anchors)}` |"
        )

    lines.extend(["", "## Package → Atomic → Mention → Evidence", ""])
    seen_atomics: set[str] = set()
    seen_mentions: set[str] = set()
    for package_index, (package_id, package) in enumerate(ordered_packages, start=1):
        event_ids = package_events.get(package_id, list(package.get("member_event_ids") or []))
        lines.extend(
            [
                f"### P{package_index:04d} · {_text(package.get('canonical_title'), 'Untitled Package')}",
                "",
                f"- Package ID：`{package_id}`",
                f"- Family / Kind：`{_text(package.get('package_family'))}` / `{_text(package.get('package_kind'))}`",
                f"- Status：`{_text(package.get('status'))}`；Quality：`{_text(package.get('quality_state'))}`",
                f"- Summary：{_text(package.get('canonical_summary'))}",
                f"- Anchors：{_text(package.get('primary_anchor_id'))}；all={_text(', '.join(package.get('package_anchor_ids') or []))}",
                "",
            ]
        )
        for atomic_index, event_id in enumerate(event_ids, start=1):
            atomic = atomics.get(event_id)
            if atomic is None:
                lines.extend([f"#### A{atomic_index:03d} · Missing Atomic `{event_id}`", ""])
                continue
            seen_atomics.add(event_id)
            identity = atomic.get("identity_profile") or {}
            lines.extend(
                [
                    f"#### P{package_index:04d}-A{atomic_index:03d} · "
                    f"{_text(atomic.get('canonical_proposition'), 'Untitled Atomic')}",
                    "",
                    f"- Atomic ID：`{event_id}`",
                    f"- Family / Assertion：`{_text(atomic.get('event_family'))}` / `{_text(atomic.get('assertion_state'))}`",
                    f"- Identity schema：`{_text(identity.get('schema_type'))}`；fields=`{_cell(json.dumps(identity.get('fields') or {}, ensure_ascii=False, separators=(',', ':')))}`",
                    f"- Time：{_time(atomic.get('time'))}",
                    f"- Mention count：{len(atomic_mentions.get(event_id, []))}",
                    "",
                ]
            )
            for mention_index, mention_id in enumerate(atomic_mentions.get(event_id, []), start=1):
                mention = mentions.get(mention_id)
                if mention is None:
                    lines.extend([f"##### M{mention_index:03d} · Missing Mention `{mention_id}`", ""])
                    continue
                seen_mentions.add(mention_id)
                source = sources.get(str(mention.get("message_id")), {})
                predicate = mention.get("predicate") or {}
                lines.extend(
                    [
                        f"##### P{package_index:04d}-A{atomic_index:03d}-M{mention_index:03d} · "
                        f"{_text(mention.get('canonical_proposition'), 'Untitled Mention')}",
                        "",
                        f"- Mention ID：`{mention_id}`",
                        f"- Source：{_text(source.get('title'))}；{_text(source.get('source_name'))}；published={_text(source.get('published_at'))}",
                        f"- Message ID：`{_text(mention.get('message_id'))}`",
                        f"- Predicate：`{_text(predicate.get('normalized'))}` / `{_text(predicate.get('raw'))}`",
                        f"- Assertion：`{_text(mention.get('assertion_state'))}`；Participants：{_participants(mention.get('participants'))}",
                        f"- Quantities：{_quantities(mention.get('quantities'))}",
                        f"- Time：{_time(mention.get('time'))}",
                        f"- local_package_hint：`{_text(mention.get('local_package_hint'))}`",
                        "- Evidence：",
                    ]
                )
                lines.extend(f"  - {item}" for item in _evidence(mention))
                lines.append("")

    orphan_atomics = sorted(set(atomics) - seen_atomics)
    orphan_mentions = sorted(set(mentions) - seen_mentions)
    lines.extend(
        [
            "## 层级完整性",
            "",
            f"- Active Package membership 未匹配的 Atomic：{len(orphan_atomics)}",
            f"- Atomic membership 未展示的 Mention：{len(orphan_mentions)}",
            f"- 未匹配 Atomic 示例：{', '.join(_short(item) for item in orphan_atomics[:20]) or '—'}",
            f"- 未匹配 Mention 示例：{', '.join(_short(item) for item in orphan_mentions[:20]) or '—'}",
            "",
            "> 以上层级和字段均来自真实 Registry；是否为完整终态以文档头部的运行状态说明为准。",
            "",
        ]
    )
    if orphan_atomics or orphan_mentions:
        lines.extend(["## 未归属的真实结果", "", "> 以下记录已落库，但在中断时没有 active Package→Atomic→Mention 完整路径。", ""])
        for index, event_id in enumerate(orphan_atomics, start=1):
            atomic = atomics[event_id]
            lines.extend(
                [
                    f"### 未归属 Atomic U-A{index:03d} · {_text(atomic.get('canonical_proposition'), 'Untitled Atomic')}",
                    "",
                    f"- Atomic ID：`{event_id}`",
                    f"- Family / Assertion：`{_text(atomic.get('event_family'))}` / `{_text(atomic.get('assertion_state'))}`",
                    f"- Time：{_time(atomic.get('time'))}",
                    f"- Mention IDs：{', '.join(f'`{item}`' for item in atomic_mentions.get(event_id, [])) or '—'}",
                    "",
                ]
            )
        for index, mention_id in enumerate(orphan_mentions, start=1):
            mention = mentions[mention_id]
            source = sources.get(str(mention.get("message_id")), {})
            predicate = mention.get("predicate") or {}
            lines.extend(
                [
                    f"### 未归属 Mention U-M{index:03d} · {_text(mention.get('canonical_proposition'), 'Untitled Mention')}",
                    "",
                    f"- Mention ID：`{mention_id}`",
                    f"- Source：{_text(source.get('title'))}；published={_text(source.get('published_at'))}",
                    f"- Predicate：`{_text(predicate.get('normalized'))}` / `{_text(predicate.get('raw'))}`",
                    f"- Assertion：`{_text(mention.get('assertion_state'))}`；Participants：{_participants(mention.get('participants'))}",
                    f"- Quantities：{_quantities(mention.get('quantities'))}",
                    f"- Time：{_time(mention.get('time'))}",
                    "- Evidence：",
                ]
            )
            lines.extend(f"  - {item}" for item in _evidence(mention))
            lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--title", default="CDECR 300 篇 MU 新闻：真实工作流层级结果")
    parser.add_argument(
        "--state-note",
        default=(
            "N12 + Wave C 已落库；N13 pair Decide、N13 Apply、finalize 尚未执行；"
            "这是中断时的真实结果，不是完整终态。"
        ),
    )
    args = parser.parse_args()
    output = render(args.registry, title=args.title, state_note=args.state_note)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(output, encoding="utf-8", newline="\n")
    print(f"wrote {args.output} ({len(output.splitlines())} lines, {len(output)} chars)")


if __name__ == "__main__":
    main()
