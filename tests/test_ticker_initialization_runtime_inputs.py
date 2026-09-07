from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock

from doxagent.event_library.provider import KnownEventIndexSnapshot
from doxagent.persistent_runtime_v2.providers import RuntimeInputSnapshot
from doxagent.ticker_initialization.repository import InitializationRepository
from doxagent.ticker_initialization.runtime_inputs import ActivatedRuntimeInputs
from doxagent.ticker_initialization.schema import NodeSpec
from doxagent.workflows.codex_document3.schema import RuntimePolicyProjection


def test_runtime_reads_one_activation_and_never_current_heads(tmp_path: Path) -> None:
    control = InitializationRepository(tmp_path / "control.db")
    control.submit("MU", datetime.now(UTC), [NodeSpec(key="activation", block="ACTIVATION")])
    lease = control.claim("test")
    events = Mock()
    policies = Mock()
    events.known_index.return_value = None
    policies.get_projection.return_value = None
    loader = ActivatedRuntimeInputs(control, events, policies)
    assert loader("MU") is None
    control.stage_revision(
        lease,
        "rev-1",
        {
            "document1": {"run_id": "d1-replacement"},
            "document2": {"run_id": "d2-old"},
            "event_library": {"version": 7},
            "document3": {"version": 3},
        },
    )
    control.activate(lease, "rev-1")
    snapshot = loader("MU")
    assert snapshot == RuntimeInputSnapshot(None, None, "rev-1", "d1-replacement", "d2-old")
    events.known_index.assert_called_once_with("MU", version=7)
    policies.get_projection.assert_called_once_with("MU", 3)
    policies.get_current_projection.assert_not_called()


def test_scheduler_admission_and_tick_do_not_initialize_legacy_documents(tmp_path: Path) -> None:
    from doxagent.message_bus_v2.repository import MessageBusV2Repository
    from doxagent.message_bus_v2.service import MessageBusV2Service
    from doxagent.runtime_scheduler import TickerRunStatus
    from tests.test_phase25_runtime_scheduler import _missing_bundle, _scheduler

    scheduler, provider, _, _ = _scheduler(_missing_bundle())
    bus = MessageBusV2Service(MessageBusV2Repository(tmp_path / "bus.db"))
    bus.bootstrap()
    bus.start_ticker("MU")
    snapshot = RuntimeInputSnapshot(
        KnownEventIndexSnapshot.model_construct(ticker="MU", version=7),
        RuntimePolicyProjection.model_construct(ticker="MU", policy_set_version=3),
        "rev-1",
        "d1",
        "d2",
    )
    runtime = Mock(input_snapshot_loader=lambda ticker: snapshot)
    scheduler.runtime_v2_service = runtime
    scheduler.message_bus_v2_service = bus
    scheduler.message_bus_v2_enabled = True
    state = scheduler.admit_activation("MU", "rev-1")
    assert state.status == TickerRunStatus.RUNNING
    scheduler.tick_ticker("MU")
    assert provider.latest_calls == 0
    assert provider.initialize_calls == 0
    assert not scheduler._weekly_update_jobs
