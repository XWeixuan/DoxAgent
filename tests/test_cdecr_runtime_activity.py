from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from doxagent.cdecr_integration.activity import project_runtime_activity


class ActivityRegistry:
    def __init__(self, as_of: datetime) -> None:
        self.atomics = [
            SimpleNamespace(event_id="A1", mention_ids=["M1"]),
            SimpleNamespace(event_id="A2", mention_ids=["M2"]),
            SimpleNamespace(event_id="A3", mention_ids=["M3"]),
        ]
        self.mentions = {
            "M1": SimpleNamespace(message_id="S1"),
            "M2": SimpleNamespace(message_id="S2"),
            "M3": SimpleNamespace(message_id="S3"),
        }
        self.sources = {
            "S1": SimpleNamespace(published_at=as_of - timedelta(days=59)),
            "S2": SimpleNamespace(published_at=as_of - timedelta(days=60)),
            "S3": SimpleNamespace(published_at=as_of - timedelta(days=61)),
        }
        self.packages = [
            SimpleNamespace(package_id="P1", member_event_ids=["A1", "A3"]),
            SimpleNamespace(package_id="P2", member_event_ids=["A3"]),
        ]

    def list_current_atomic_events(self, *, limit: int) -> list[Any]:
        return self.atomics

    def get_mention(self, mention_id: str) -> Any:
        return self.mentions.get(mention_id)

    def get_source(self, message_id: str) -> Any:
        return self.sources.get(message_id)

    def list_current_packages(self, *, limit: int) -> list[Any]:
        return self.packages


def test_activity_uses_latest_source_published_at_and_package_active_members() -> None:
    as_of = datetime(2026, 8, 24, tzinfo=UTC)
    snapshot = project_runtime_activity(
        registry=ActivityRegistry(as_of),  # type: ignore[arg-type]
        runtime_scope="cdecr:US:AMD",
        as_of=as_of,
    )
    assert snapshot.eligible_atomic_ids == {"A1", "A2"}
    by_package = {item.runtime_package_id: item for item in snapshot.packages}
    assert by_package["P1"].active_atomic_count == 1
    assert by_package["P1"].is_active is True
    assert by_package["P2"].active_atomic_count == 0
    assert by_package["P2"].is_active is False
