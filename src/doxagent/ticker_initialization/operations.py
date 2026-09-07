"""Explicit operator activations never invalidate or execute research successors."""

from datetime import datetime
from typing import Any

from .catalog import default_plan
from .repository import InitializationRepository
from .schema import InitializationError, RunRecord


def submit_activation(
    repository: InitializationRepository,
    ticker: str,
    *,
    artifacts: dict[str, Any],
    reason: str,
    cutoff: datetime,
    operation: str = "ACTIVATE",
    expected_base_revision: str | None = None,
) -> RunRecord:
    if not reason.strip():
        raise ValueError("operator reason required")
    plan = [
        node
        for node in default_plan()
        if node.key in {"activation.prepare", "activation.commit", "bus.ready", "runtime.ready"}
    ]
    plan[0].dependencies = []
    plan[0].inputs = {"artifacts": artifacts, "operator_reason": reason}
    return repository.submit(
        ticker,
        cutoff,
        plan,
        reinitialize=True,
        operation_kind=operation,
        expected_base_revision=expected_base_revision,
    )


def replace_artifact(
    repository: InitializationRepository,
    ticker: str,
    *,
    role: str,
    reference: dict[str, Any],
    reason: str,
) -> RunRecord:
    if role not in {
        "document1",
        "document2",
        "document3",
        "event_library",
        "monitoring_configuration",
    }:
        raise ValueError("unknown artifact role")
    active = repository.active_revision(ticker)
    if active is None:
        raise InitializationError("ticker has no active revision to replace")
    artifacts = {**active["artifacts"], role: reference}
    return submit_activation(
        repository,
        ticker,
        artifacts=artifacts,
        reason=reason,
        cutoff=datetime.fromisoformat(active["research_cutoff_at"]),
        operation="REPLACE_ARTIFACT",
        expected_base_revision=active["revision_id"],
    )
