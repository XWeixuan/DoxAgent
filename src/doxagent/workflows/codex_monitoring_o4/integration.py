"""Small D3 publication hook that durably enqueues O4_CONFIGURE."""

from __future__ import annotations

from doxagent.workflows.codex_document2.schema import Document2Document
from doxagent.workflows.codex_document3.schema import PolicySet

from .repository import MonitoringO4Repository


class Document3MonitoringO4Trigger:
    def __init__(self, repository: MonitoringO4Repository) -> None:
        self._repository = repository

    def on_policy_published(
        self, *, policy_set: PolicySet, document2: Document2Document
    ) -> None:
        # Runtime publications must never enqueue O4. Initialization explicitly
        # submits its signed CONFIGURE/DELIVER tasks through the initialization adapter.
        return None



__all__ = ["Document3MonitoringO4Trigger"]
