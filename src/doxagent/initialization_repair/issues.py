"""Central, rebuildable init-issue.md ledger."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .repository import RepairRepository
from .schema import IssueEntry, RepairAgentReport


def record_agent_report(
    repository: RepairRepository,
    *,
    entry_id: str,
    incident_id: str,
    round_id: str,
    report: RepairAgentReport,
    node_ordinals: dict[str, int],
    ticker: str,
    initialization_id: str,
    thread_id: str | None,
    turn_id: str | None,
    commit: str | None,
    image_id: str | None,
) -> bool:
    return repository.add_issue(
        IssueEntry(
            entry_id=entry_id,
            incident_id=incident_id,
            round_id=round_id,
            kind="agent_report",
            content={
                "ticker": ticker,
                "initialization_id": initialization_id,
                "node_ordinals": node_ordinals,
                "root_cause": report.root_cause,
                "evidence": report.evidence,
                "changed_files": report.changed_files,
                "tests": report.tests,
                "downstream_implications": report.downstream_implications,
                "remaining_items": report.remaining_items,
                "thread_id": thread_id,
                "turn_id": turn_id,
                "commit": commit,
                "image_id": image_id,
                "execution_result": "PENDING",
            },
        )
    )


def record_execution(
    repository: RepairRepository,
    *,
    entry_id: str,
    incident_id: str,
    round_id: str,
    content: dict[str, Any],
) -> bool:
    return repository.add_issue(
        IssueEntry(
            entry_id=entry_id,
            incident_id=incident_id,
            round_id=round_id,
            kind="execution_result",
            content=content,
        )
    )


def render(repository: RepairRepository) -> str:
    incidents = {item.incident_id: item for item in repository.incidents()}
    entries = repository.issues()
    lines = [
        "# Ticker Initialization Repair Issues",
        "",
        "> Generated from the durable initialization-repair ledger. Do not edit by hand.",
        "",
        "## Incident index",
        "",
        "| Incident | Ticker | Initialization | Status | Updated |",
        "| --- | --- | --- | --- | --- |",
    ]
    for incident in incidents.values():
        lines.append(
            f"| `{incident.incident_id}` | `{incident.ticker}` | "
            f"`{incident.initialization_id}` | {incident.status} | "
            f"{incident.updated_at.isoformat()} |"
        )
    for incident_id, incident in incidents.items():
        lines.extend(
            [
                "",
                f"## {incident.ticker} — {incident_id}",
                "",
                f"- Initialization: `{incident.initialization_id}`",
                f"- Status: `{incident.status}`",
                f"- Phase: `{incident.phase}`",
            ]
        )
        if incident.last_error:
            lines.append(f"- Last error: `{incident.last_error}`")
        for entry in (item for item in entries if item.incident_id == incident_id):
            content = entry.content
            lines.extend(
                [
                    "",
                    f"### {entry.created_at.isoformat()} — {entry.kind}",
                    "",
                    f"- Round: `{entry.round_id or 'n/a'}`",
                ]
            )
            if "node_ordinals" in content:
                lines.append(f"- Node rounds: `{content['node_ordinals']}`")
            for heading, field in (
                ("Root cause", "root_cause"),
                ("Evidence", "evidence"),
                ("Changed files", "changed_files"),
                ("Tests", "tests"),
                ("Downstream implications", "downstream_implications"),
                ("Execution result", "execution_result"),
                ("Remaining items", "remaining_items"),
            ):
                if field not in content:
                    continue
                lines.extend(["", f"#### {heading}", ""])
                value = content[field]
                if isinstance(value, dict):
                    if value:
                        lines.extend(f"- `{key}`: {item}" for key, item in value.items())
                    else:
                        lines.append("- None")
                elif isinstance(value, list):
                    lines.extend(f"- {item}" for item in value) if value else lines.append("- None")
                else:
                    lines.append(str(value))
            references = {
                key: content.get(key)
                for key in ("commit", "image_id", "thread_id", "turn_id")
                if content.get(key)
            }
            if references:
                lines.extend(["", "#### References", ""])
                lines.extend(f"- {key}: `{value}`" for key, value in references.items())
    return "\n".join(lines).rstrip() + "\n"


def render_to(repository: RepairRepository, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(render(repository))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, output)
