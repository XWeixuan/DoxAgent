from pathlib import Path

from .evidence import redact
from .repository import MaintenanceRepository


def render(repository: MaintenanceRepository, path: Path) -> str:
    lines = [
        "# Message Source Maintenance Issues",
        "",
        "RECOVERED is not STABLE. Branch/manifest is not a main merge.",
        "",
    ]
    for item in repository.incidents():
        rounds = repository.rounds(item.incident_id)
        actions = repository.actions(item.incident_id)
        lines += [
            f"## {item.incident_id}: {item.stage}",
            "",
            f"- Resource: {item.resource_key}",
            f"- Sources: {', '.join(item.source_ids)}",
            f"- Bindings: {', '.join(item.affected_bindings)}",
            f"- Failure: {item.failure_class}; rounds: {len(rounds)}",
            f"- Last observed: {item.last_observed_at.isoformat()}",
            f"- Human action: {item.human_action or 'none'}",
            f"- Changes: {item.context.get('changed_files', [])}",
            f"- Candidate commit: {item.context.get('candidate_commit', 'none')}",
            f"- Actions: {[(a.kind.value, a.status) for a in actions]}",
            "",
        ]
    text = str(redact("\n".join(lines)))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8")
    return text
