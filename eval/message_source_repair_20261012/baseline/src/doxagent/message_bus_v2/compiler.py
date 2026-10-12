"""Deterministic compilation of one StreamItem into one LLM business input."""

from __future__ import annotations

from dataclasses import dataclass

from doxagent.message_bus_v2.schema import MaterializedStreamItem, MaterializedStreamMember


@dataclass(frozen=True)
class CompiledStreamInput:
    stream_item_id: str
    ticker: str
    member_count: int
    latest: MaterializedStreamMember
    body: str


def format_member(member: MaterializedStreamMember, *, display_index: int) -> str:
    lines = [
        f"[MESSAGE {display_index}]",
        f"source: {member.source}",
        f"published_at: {member.published_at.isoformat()}",
    ]
    if member.title:
        lines.append(f"title: {member.title}")
    lines.extend(("body:", member.body, f"url: {member.url}"))
    return "\n".join(lines)


def compile_members(
    members: list[MaterializedStreamMember],
) -> tuple[str, MaterializedStreamMember]:
    if not members:
        raise ValueError("cannot compile an empty stream item")
    ordered = sorted(members, key=lambda member: (member.published_at, member.member_index))
    latest = max(members, key=lambda member: (member.published_at, member.member_index))
    return "\n\n".join(
        format_member(member, display_index=index) for index, member in enumerate(ordered, start=1)
    ), latest


def compile_stream_item(value: MaterializedStreamItem) -> CompiledStreamInput:
    body, latest = compile_members(value.members)
    return CompiledStreamInput(
        stream_item_id=value.item.stream_item_id,
        ticker=value.item.ticker,
        member_count=value.item.member_count,
        latest=latest,
        body=body,
    )


def compiled_body_length_for_members(members: list[MaterializedStreamMember]) -> int:
    return len(compile_members(members)[0]) if members else 0


__all__ = [
    "CompiledStreamInput",
    "compile_members",
    "compile_stream_item",
    "compiled_body_length_for_members",
    "format_member",
]
