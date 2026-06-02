"""Workflow storage factory for Blackboard and checkpoint persistence."""

from __future__ import annotations

from dataclasses import dataclass

from doxagent.blackboard import (
    BlackboardService,
    InMemoryBlackboardRepository,
    PostgresBlackboardRepository,
)
from doxagent.settings import DoxAgentSettings
from doxagent.workflows.checkpoint_repository import (
    InMemoryWorkflowCheckpointRepository,
    PostgresWorkflowCheckpointRepository,
    WorkflowCheckpointRepository,
)


@dataclass(frozen=True)
class WorkflowStorage:
    blackboard: BlackboardService
    checkpoint_repository: WorkflowCheckpointRepository


def default_workflow_storage(settings: DoxAgentSettings | None = None) -> WorkflowStorage:
    resolved = settings or DoxAgentSettings()
    if resolved.storage_mode == "postgres":
        return WorkflowStorage(
            blackboard=BlackboardService(PostgresBlackboardRepository.from_settings(resolved)),
            checkpoint_repository=PostgresWorkflowCheckpointRepository.from_settings(resolved),
        )
    return WorkflowStorage(
        blackboard=BlackboardService(InMemoryBlackboardRepository()),
        checkpoint_repository=InMemoryWorkflowCheckpointRepository(),
    )
