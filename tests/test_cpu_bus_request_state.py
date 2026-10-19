from datetime import UTC, datetime, timedelta

from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.schema import SchedulerGroupState


def test_request_start_uses_one_transaction_and_preserves_other_fields(tmp_path, monkeypatch):
    repo = MessageBusV2Repository(tmp_path / "bus.db")
    now = datetime.now(UTC)
    repo.save_scheduler_group_state(
        SchedulerGroupState(
            scheduler_group="group",
            schedule_signature="signature",
            active_request_count=3,
            next_request_at=now + timedelta(minutes=1),
        )
    )
    connections = []
    old = repo._connect

    def connected():
        connections.append(1)
        return old()

    monkeypatch.setattr(repo, "_connect", connected)
    repo.record_scheduler_request_start("group", now)
    assert len(connections) == 1
    state = repo.get_scheduler_group_state("group")
    assert state.last_request_at == now
    assert state.schedule_signature == "signature" and state.active_request_count == 3
    assert state.next_request_at == now + timedelta(minutes=1)
    repo.record_scheduler_request_start("new", now)
    assert MessageBusV2Repository(repo.path).get_scheduler_group_state("new").last_request_at == now
