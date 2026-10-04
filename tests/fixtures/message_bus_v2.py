"""Current V2 fixtures; safe to import without collecting unrelated suites."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.schema import (
    RawMessageInput,
)
from doxagent.message_bus_v2.service import MessageBusV2Service
from doxagent.persistent_runtime_v2.schema import RuntimeCaseStatus, SourceMessageEnvelope

NOW = datetime(2026, 9, 1, 12, tzinfo=UTC)


def _bus(path: Path) -> tuple[MessageBusV2Repository, MessageBusV2Service]:
    repository = MessageBusV2Repository(path)
    service = MessageBusV2Service(repository)
    service.bootstrap()
    return repository, service


def _input(
    external_id: str,
    *,
    body: str | None = None,
    published_at: datetime = NOW,
) -> RawMessageInput:
    return RawMessageInput(
        external_id=external_id,
        title=f"Title {external_id}",
        body=body or f"Body {external_id}",
        source="Reuters",
        url=f"https://example.test/messages/{external_id}",
        published_at=published_at,
        raw_payload={"id": external_id, "body": body or f"Body {external_id}"},
    )


class _AcceptingRuntimeV2:
    journal = None

    def __init__(self) -> None:
        from doxagent.persistent_runtime_v2.repository import InMemoryPersistentRuntimeV2Repository

        self.repository = InMemoryPersistentRuntimeV2Repository()
        self.envelopes: list[SourceMessageEnvelope] = []

    def process_pending_effects(self, *, limit: int) -> list[object]:
        return []

    def execute_message(self, value: SourceMessageEnvelope) -> object:
        self.envelopes.append(value)
        return type(
            "AcceptedCase",
            (),
            {
                "case_id": f"case-{len(self.envelopes)}",
                "status": RuntimeCaseStatus.COMPLETED,
                "route": None,
            },
        )()


class _DashboardRuntimeV2:
    def __init__(self, source_message_id: str) -> None:
        self.repository = type("Repository", (), {"list_cases": lambda _self, _ticker: []})()
        self.journal = type(
            "Journal",
            (),
            {
                "tasks": lambda _self, **_kwargs: [
                    {
                        "id": f"inbox:MU:{source_message_id}",
                        "status": "PENDING",
                        "inputs": {"source": {"source_message_id": source_message_id}},
                    }
                ]
            },
        )()
