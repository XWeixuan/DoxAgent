import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from doxagent.persistent_runtime_v2.journal import RuntimeJournal
from doxagent.runtime_scheduler.schema import TickerRunStatus
from doxagent.ticker_initialization.consumers import admit_runtime_revisions
from doxagent.v2_control.repository import ControlRepository


def admission(tmp_path):
    journal = RuntimeJournal(tmp_path / "runtime.db")
    ControlRepository(journal).migrate()
    desired = dict(epoch=1, requested_mode="PAPER_TRADING", admission_allowed=True)
    revisions = [dict(ticker="MU", revision_id="first")]
    acks = {("first", "bus")}
    state = [None]
    calls = []

    def publish_control():
        with journal.transaction() as db:
            db.execute(
                "INSERT OR REPLACE INTO v2_ticker_control VALUES('MU',1,?)",
                (json.dumps(desired),),
            )

    def admit(ticker, identity):
        calls.append(identity)
        state[0] = SimpleNamespace(
            status=TickerRunStatus.RUNNING,
            metadata={
                "activation_revision_id": identity,
                "v2_control_epoch": desired["epoch"],
                "v2_control_mode": desired["requested_mode"],
            },
        )
        journal.set("policy_authorization_head", ticker, identity)
        journal.set("policy_authorization_applied", identity, dict(revision_id=identity))

    def acknowledge(ticker, identity, worker):
        if revisions[0]["revision_id"] != identity:
            return False
        acks.add((identity, worker))
        return True

    publish_control()
    control = SimpleNamespace(
        active_revisions=lambda: revisions,
        revision_acknowledged=lambda ticker, identity, worker: (identity, worker) in acks,
        acknowledge_revision=Mock(side_effect=acknowledge),
        admission_error=Mock(),
    )
    scheduler = SimpleNamespace(
        repository=SimpleNamespace(get_state=lambda ticker: state[0]),
        runtime_v2_service=SimpleNamespace(journal=journal),
        admit_activation=admit,
    )
    return SimpleNamespace(**locals())


def test_stable_admission_retains_degraded_state_and_refreshes_ack(tmp_path):
    x = admission(tmp_path)
    assert admit_runtime_revisions(x.control, x.scheduler) == {"MU"}
    x.state[0].status = TickerRunStatus.DEGRADED
    for _ in range(3):
        assert admit_runtime_revisions(x.control, x.scheduler) == {"MU"}
    assert x.calls == ["first"]
    assert x.state[0].status == TickerRunStatus.DEGRADED
    assert x.control.acknowledge_revision.call_count == 4


@pytest.mark.parametrize(
    "change",
    [
        "epoch",
        "mode",
        "metadata",
        "ack",
        "head",
        "receipt",
        "restart",
        "revision",
    ],
)
def test_changed_or_incomplete_admission_runs_full_path(tmp_path, change):
    x = admission(tmp_path)
    admit_runtime_revisions(x.control, x.scheduler)
    if change == "epoch":
        x.desired["epoch"] += 1
        x.publish_control()
    elif change == "mode":
        x.desired["requested_mode"] = "MESSAGE_MONITORING"
        x.publish_control()
    elif change == "metadata":
        x.state[0].metadata["v2_control_epoch"] = -1
    elif change == "ack":
        x.acks.remove(("first", "runtime"))
    elif change == "head":
        x.journal.delete("policy_authorization_head", "MU")
    elif change == "receipt":
        x.journal.delete("policy_authorization_applied", "first")
    elif change == "restart":
        x.scheduler._runtime_admission_proofs = {}
    elif change == "revision":
        x.revisions[0] = dict(ticker="MU", revision_id="second")
        x.acks.add(("second", "bus"))
    assert admit_runtime_revisions(x.control, x.scheduler) == {"MU"}
    assert len(x.calls) == 2


def test_stopped_admission_waits_for_resume_and_failed_retry_does_not_keep_proof(tmp_path):
    x = admission(tmp_path)
    admit_runtime_revisions(x.control, x.scheduler)
    x.state[0].status = TickerRunStatus.STOPPED
    x.desired["admission_allowed"] = False
    x.publish_control()
    assert admit_runtime_revisions(x.control, x.scheduler) == set()
    assert len(x.calls) == 1
    x.desired.update(admission_allowed=True, epoch=2)
    x.publish_control()
    x.scheduler.admit_activation = Mock(side_effect=ValueError("not ready"))
    assert admit_runtime_revisions(x.control, x.scheduler) == set()
    assert "MU" not in x.scheduler._runtime_admission_proofs
    x.scheduler.admit_activation = x.admit
    assert admit_runtime_revisions(x.control, x.scheduler) == {"MU"}


@pytest.mark.asyncio
@pytest.mark.parametrize("include_detail", [True, False])
async def test_background_tick_keeps_consumption_and_skips_only_details(
    tmp_path, monkeypatch, include_detail
):
    from doxagent.message_bus_v2.schema import utc_now
    from doxagent.runtime_scheduler.repository import InMemoryRuntimeSchedulerRepository
    from doxagent.runtime_scheduler.service import UnifiedRuntimeSchedulerService
    from tests.fixtures.message_bus_v2 import _AcceptingRuntimeV2, _bus, _input

    repo, bus = _bus(tmp_path / "bus.db")
    bus.start_ticker("MU")
    runtime = _AcceptingRuntimeV2()
    runtime.input_snapshot_loader = lambda ticker: SimpleNamespace(
        activation_revision_id="first", index=object(), projection=object()
    )
    scheduler = UnifiedRuntimeSchedulerService(
        InMemoryRuntimeSchedulerRepository(),
        runtime_v2_service=runtime,
        message_bus_v2_service=bus,
    )
    now = utc_now()
    scheduler.admit_activation("MU", "first", now=now)
    binding = repo.get_binding("MU:benzinga_news")
    await bus.accept_message(
        source=repo.get_source("benzinga_news"),
        binding=binding,
        message=_input(
            "after-admission", body="Micron MU announces quarterly results", published_at=now
        ),
        bootstrap=False,
    )
    detail = Mock(wraps=scheduler.detail)
    monkeypatch.setattr(scheduler, "detail", detail)
    assert len(repo.read_stream("MU", after_offset=0)) == 1
    result = scheduler.tick_ticker("MU", now=now, include_detail=include_detail)
    assert len(runtime.envelopes) == 1
    assert repo.get_consumer_offset("persistent_runtime_v2", "MU").stream_offset == 1
    assert scheduler.repository.get_state("MU").counters.events_consumed == 1
    assert detail.call_count == int(include_detail)
    assert (result is not None) == include_detail
