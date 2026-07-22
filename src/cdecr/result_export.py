"""Human-readable and machine-readable export of final CDECR clustering state."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cdecr.evaluation import EvaluationMetrics
from cdecr.registry import SQLiteCDECRRegistry


def _write_json(payload: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8"
    )
    temporary.replace(path)


def export_final_clusters(
    *,
    registry: SQLiteCDECRRegistry,
    json_path: Path,
    markdown_path: Path,
    quality_metrics: EvaluationMetrics | None = None,
) -> dict[str, int]:
    atomic_events = registry.list_current_atomic_events(limit=10000)
    packages = registry.list_current_packages(limit=10000)
    clustered_mention_ids = {
        mention_id for event in atomic_events for mention_id in event.mention_ids
    }
    all_mentions = registry.list_all_mentions(limit=100000)
    mentions = {mention.mention_id: mention for mention in all_mentions}
    sources = {
        source.message_id: source for source in registry.list_all_sources(limit=10000)
    }
    unclustered_mentions = [
        mention for mention in all_mentions if mention.mention_id not in clustered_mention_ids
    ]
    memberships = {
        package.package_id: registry.list_memberships_for_package(package.package_id)
        for package in packages
    }
    atomic_relations = registry.list_external_relations()
    package_relations = registry.list_package_external_relations()
    open_holds = registry.list_open_holds(limit=1000)
    packaged_event_ids = {
        membership.event_id for values in memberships.values() for membership in values
    }
    payload: dict[str, Any] = {
        "export_version": "cdecr-final-clusters-v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "scope": {
            "document_count": len(sources),
            "mention_count": len(mentions),
            "atomic_event_count": len(atomic_events),
            "package_count": len(packages),
            "membership_count": sum(len(values) for values in memberships.values()),
            "atomic_external_relation_count": len(atomic_relations),
            "package_external_relation_count": len(package_relations),
            "unpackaged_atomic_event_count": sum(
                event.event_id not in packaged_event_ids for event in atomic_events
            ),
            "unclustered_mention_count": len(unclustered_mentions),
            "open_hold_count": len(open_holds),
        },
        "quality_metrics": (
            quality_metrics.model_dump(mode="json") if quality_metrics is not None else None
        ),
        "documents": [
            {
                "message_id": source.message_id,
                "title": source.title,
                "source_name": source.source_name,
                "published_at": source.published_at.isoformat(),
                "url": source.url,
                "mention_ids": sorted(
                    mention.mention_id
                    for mention in mentions.values()
                    if mention.message_id == source.message_id
                ),
            }
            for source in sources.values()
        ],
        "mentions": [
            mention.model_dump(mode="json")
            for mention in sorted(mentions.values(), key=lambda item: item.mention_id)
        ],
        "unclustered_mentions": [
            mention.model_dump(mode="json") for mention in unclustered_mentions
        ],
        "atomic_events": [
            {
                "event": event.model_dump(mode="json"),
                "mentions": [
                    mentions[mention_id].model_dump(mode="json")
                    for mention_id in event.mention_ids
                    if mention_id in mentions
                ],
            }
            for event in sorted(atomic_events, key=lambda item: item.event_id)
        ],
        "event_packages": [
            {
                "package": package.model_dump(mode="json"),
                "memberships": [
                    item.model_dump(mode="json") for item in memberships[package.package_id]
                ],
                "member_events": [
                    event.model_dump(mode="json")
                    for event in atomic_events
                    if event.event_id in package.member_event_ids
                ],
            }
            for package in sorted(packages, key=lambda item: item.package_id)
        ],
        "atomic_external_relations": [
            item.model_dump(mode="json") for item in atomic_relations
        ],
        "package_external_relations": [
            item.model_dump(mode="json") for item in package_relations
        ],
        "open_holds": [item.model_dump(mode="json") for item in open_holds],
    }
    _write_json(payload, json_path)

    event_by_id = {event.event_id: event for event in atomic_events}
    lines = [
        "# CDECR 最终共指 / 聚类结果",
        "",
        f"生成时间：{payload['generated_at']}",
        "",
        "## 汇总",
        "",
        f"- 文档：{len(sources)}",
        f"- Event Mention：{len(mentions)}",
        f"- Atomic Event：{len(atomic_events)}",
        f"- Event Package：{len(packages)}",
        f"- Package Membership：{sum(len(values) for values in memberships.values())}",
        f"- Atomic 外部关系：{len(atomic_relations)}",
        f"- Package 外部关系：{len(package_relations)}",
        f"- 未进入当前 Atomic Event 的 Mention：{len(unclustered_mentions)}",
        f"- Open HOLD：{len(open_holds)}",
    ]
    if quality_metrics is not None:
        lines.extend(
            [
                "",
                "## M4 辅助质量对照",
                "",
                f"- 事件召回率：{quality_metrics.event_recall:.2%}",
                f"- Mention 精确率：{quality_metrics.mention_precision:.2%}",
                f"- Schema 有效率：{quality_metrics.valid_schema_rate:.2%}",
                f"- Evidence 有效率：{quality_metrics.valid_evidence_rate:.2%}",
                "- 说明：M4 辅助评审，尚未取得人工金标签字。",
            ]
        )
    lines.extend(["", "## Event Packages", ""])
    for package in sorted(
        packages, key=lambda item: (-len(item.member_event_ids), item.package_id)
    ):
        lines.extend(
            [
                f"### {package.canonical_title}",
                "",
                f"- Package ID：`{package.package_id}`",
                (
                    f"- Family / Kind：`{package.package_family.value}` / "
                    f"`{package.package_kind.value}`"
                ),
                (
                    f"- 成员数：{len(package.member_event_ids)}；"
                    f"版本：{package.version}"
                ),
                f"- 摘要：{package.canonical_summary or '（空）'}",
                "",
            ]
        )
        for event_id in package.member_event_ids:
            event = event_by_id.get(event_id)
            if event is None:
                lines.append(f"- `{event_id}`（已重定向或当前不可见）")
                continue
            lines.append(
                f"- `{event.event_id}` [{event.event_family.value}] "
                f"{event.canonical_proposition}（mentions={len(event.mention_ids)}, "
                f"version={event.version}）"
            )
        lines.append("")
    unpackaged = [
        event for event in atomic_events if event.event_id not in packaged_event_ids
    ]
    lines.extend(["## 未进入 Package 的 Atomic Events", ""])
    for event in sorted(unpackaged, key=lambda item: item.event_id):
        lines.append(
            f"- `{event.event_id}` [{event.event_family.value}] "
            f"{event.canonical_proposition}（mentions={len(event.mention_ids)}）"
        )
    lines.extend(["", "## 外部关系", ""])
    for relation in atomic_relations:
        lines.append(
            f"- Atomic `{relation.source_event_id}` --{relation.relation.value}--> "
            f"`{relation.target_event_id}`"
        )
    for package_relation in package_relations:
        lines.append(
            f"- Atomic `{package_relation.source_event_id}` "
            f"--{package_relation.relation.value}--> "
            f"Package `{package_relation.target_package_id}`"
        )
    lines.extend(["", "## Unclustered Event Mentions", ""])
    for mention in unclustered_mentions:
        lines.append(
            f"- `{mention.mention_id}` [{mention.event_family.value}] "
            f"{mention.canonical_proposition} (document=`{mention.message_id}`)"
        )
    lines.extend(["", "## Open HOLD Queue", ""])
    for hold in open_holds:
        lines.append(
            f"- `{hold.hold_id}` [{hold.kind.value}] subject=`{hold.subject_id}`; "
            f"reasons={', '.join(hold.reason_codes)}"
        )
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_markdown = markdown_path.with_suffix(markdown_path.suffix + ".tmp")
    temporary_markdown.write_text("\n".join(lines) + "\n", encoding="utf-8")
    temporary_markdown.replace(markdown_path)
    return {key: int(value) for key, value in payload["scope"].items()}
