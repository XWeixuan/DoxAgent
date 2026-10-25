import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest

from doxagent.message_bus_v2.collection_override import (
    REQUEST_GATE,
    CollectionControl,
    CollectionLease,
    CollectionStopped,
    matches,
)
from doxagent.message_bus_v2.poll_trace import install
from doxagent.message_bus_v2.scheduler import GlobalPollScheduler, SchedulerGroupLimiter
from doxagent.message_bus_v2.schema import PollResult, SchedulerConstraints
from doxagent.persistent_runtime_v2.bus_orchestration import BusOrchestration
from doxagent.persistent_runtime_v2.journal import RuntimeJournal
from tests.fixtures.message_bus_v2 import _bus
from tests.test_content_enrichment_hub import _message, _setup


def enable(control, **kwargs):
    return control.update(enabled=True, actor="test", reason="off-session probe", **kwargs)


def test_persistent_state_scope_expiry_and_history(tmp_path):
    repo, _ = _bus(tmp_path / "bus.db")
    now = [datetime(2026, 10, 25, 12, tzinfo=UTC)]
    control = CollectionControl(repo, clock=lambda: now[0])
    assert control.status()["state"] == "DISABLED"
    state = enable(control, expires_at=now[0] + timedelta(minutes=5))
    assert matches(state, "MU", "anything-enabled", now[0])
    scoped = {**state, "tickers": ["MU"], "sources": ["yahoo"]}
    assert matches(scoped, "MU", "yahoo", now[0])
    assert not matches(scoped, "BE", "yahoo", now[0])
    assert not matches(scoped, "MU", "other", now[0])
    # Process restart uses the same durable state; no timer process is needed.
    restored = CollectionControl(type(repo)(repo.path), clock=lambda: now[0])
    assert restored.status()["state"] == "ACTIVE"
    now[0] += timedelta(minutes=5)
    assert restored.status()["state"] == "EXPIRED"
    assert not matches(restored.status(), "MU", "anything-enabled", now[0])
    restored.update(enabled=False, actor="operator", reason="finished early")
    assert restored.status()["state"] == "DISABLED"
    assert restored.history()[0]["reason"] == "finished early"
    assert restored.history()[1]["expires_at"] == state["expires_at"]
    assert not matches({**state, "tickers": ["MU"], "sources": ["yahoo"]}, "BE", "yahoo", now[0])
    assert not matches({**state, "tickers": ["MU"], "sources": ["yahoo"]}, "MU", "other", now[0])
    with pytest.raises(ValueError):
        enable(control, expires_at=now[0])
    with pytest.raises(ValueError):
        enable(control, sources=["typo-source"])
    with pytest.raises(ValueError):
        control.update(enabled=True, actor="test", reason="")


@pytest.mark.parametrize("ending", ["disable", "expire", "replace"])
async def test_waiting_requests_stop_inflight_finishes_and_permits_release(tmp_path, ending):
    repo, _ = _bus(tmp_path / "bus.db")
    now = [datetime(2026, 10, 25, 12, tzinfo=UTC)]
    control = CollectionControl(repo, clock=lambda: now[0])
    state = enable(control, expires_at=now[0] + timedelta(seconds=5))
    binding = SimpleNamespace(ticker="MU", source_id="yahoo")
    lease = CollectionLease(repo.path, state, [binding], clock=lambda: now[0])
    limiter = SchedulerGroupLimiter(
        SchedulerConstraints(max_concurrency=1, minimum_request_gap_seconds=0)
    )
    entered, release = asyncio.Event(), asyncio.Event()
    completed = []

    async def request(first):
        async with limiter.permit():
            if first:
                entered.set()
                await release.wait()
            completed.append(first)

    token = REQUEST_GATE.set(lease)
    try:
        first = asyncio.create_task(request(True))
        await entered.wait()
        waiting = asyncio.create_task(request(False))
        await asyncio.sleep(0)
        if ending == "disable":
            control.update(enabled=False, actor="test", reason="stop")
        elif ending == "expire":
            now[0] += timedelta(seconds=5)
        else:
            enable(control)  # Old tasks cannot inherit a newly enabled session.
        release.set()
        await first
        with pytest.raises(CollectionStopped):
            await waiting
        assert completed == [True]
    finally:
        REQUEST_GATE.reset(token)
    async with limiter.permit():
        pass  # Normal collection/body paths do not inherit temporary revocation.


async def test_http_pool_checks_revocation_before_send(tmp_path):
    repo, _ = _bus(tmp_path / "bus.db")
    control = CollectionControl(repo)
    state = enable(control)
    lease = CollectionLease(repo.path, state, [SimpleNamespace(ticker="MU", source_id="yahoo")])
    sent = []
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: sent.append(r) or httpx.Response(200))
    ) as client:
        install(client)
        token = REQUEST_GATE.set(lease)
        try:
            control.update(enabled=False, actor="test", reason="stop")
            with pytest.raises(CollectionStopped):
                await client.get("https://example.test/feed")
        finally:
            REQUEST_GATE.reset(token)
        await client.get("https://example.test/feed")
    assert len(sent) == 1


@pytest.mark.parametrize("end", ["disable", "expire"])
async def test_scoped_closed_dispatch_disable_restart_and_next_trading_day(tmp_path, end):
    now = [datetime(2026, 10, 25, 12, tzinfo=UTC)]
    journal = RuntimeJournal(tmp_path / "runtime.db", clock=lambda: now[0])
    repo, service = _bus(tmp_path / "bus.db")
    for ticker in ("MU", "BE"):
        service.start_ticker(ticker)
    binding = repo.list_bindings(ticker="MU", active_only=True)[0]
    source = repo.get_source(binding.source_id)
    other = next(
        b
        for b in repo.list_bindings(ticker="BE", active_only=True)
        if b.source_id == source.source_id
    )
    control = CollectionControl(repo, clock=lambda: now[0])
    calls = []

    async def poll(s, b, at):
        from doxagent.message_bus_v2.collection_override import check_request_allowed

        check_request_allowed()
        calls.append(b.ticker)
        return SimpleNamespace(error_code=None)

    scheduler = SimpleNamespace(
        repository=repo,
        service=service,
        distribution_worker=None,
        _poll=poll,
        _prepare_poll_cycle=lambda at: (
            [(source, binding), (source, other)],
            [],
            {b.binding_id: repo.get_poll_state(b) for b in (binding, other)},
        ),
    )
    owner = BusOrchestration(journal)
    await owner.run_once(scheduler)
    assert calls == []
    enable(
        control, tickers=["MU"], sources=[source.source_id], expires_at=now[0] + timedelta(hours=1)
    )
    await owner.run_once(scheduler)
    await asyncio.gather(*owner._inflight.values())
    assert calls == ["MU"]
    assert not journal.get("schedule", "MU")
    assert journal.get("calendar", "XNYS:2026-10-25")["session"] is False
    if end == "disable":
        control.update(enabled=False, actor="test", reason="stop")
    else:
        now[0] += timedelta(hours=1)
    await owner.run_once(scheduler)
    assert calls == ["MU"]
    owner = BusOrchestration(journal)  # Disabled remains disabled after restart.
    await owner.run_once(scheduler)
    assert calls == ["MU"]
    now[0] = datetime(2026, 10, 26, 12, tzinfo=UTC)
    await owner.run_once(scheduler)
    await asyncio.gather(*owner._inflight.values())
    assert sorted(calls) == ["BE", "MU", "MU"]
    assert journal.get("calendar", "XNYS:2026-10-26")["session"] is True
    await owner.close()


async def test_shared_source_scope_does_not_add_other_tickers(tmp_path):
    from doxagent.message_bus_v2.schema import AcquisitionMode

    now = datetime(2026, 10, 25, 12, tzinfo=UTC)
    journal = RuntimeJournal(tmp_path / "runtime.db", clock=lambda: now)
    repo, service = _bus(tmp_path / "bus.db")
    for ticker in ("MU", "BE"):
        service.start_ticker(ticker)
    binding = repo.list_bindings(ticker="MU", active_only=True)[0]
    other = next(
        b
        for b in repo.list_bindings(ticker="BE", active_only=True)
        if b.source_id == binding.source_id
    )
    source = repo.get_source(binding.source_id).model_copy(
        update={"acquisition_mode": AcquisitionMode.BY_DISTRIBUTION}
    )
    control = CollectionControl(repo, clock=lambda: now)
    enable(control, tickers=["MU"])
    rosters = []

    async def poll(source, bindings, at):
        rosters.append([b.ticker for b in bindings])
        assert REQUEST_GATE.get().bindings == [("MU", source.source_id)]

    scheduler = SimpleNamespace(
        repository=repo,
        service=service,
        distribution_worker=None,
        _poll_distribution=poll,
        distribution_due=lambda *args, **kw: True,
        _prepare_poll_cycle=lambda at: (
            [(source, binding), (source, other)],
            [],
            {b.binding_id: repo.get_poll_state(b) for b in (binding, other)},
        ),
    )
    owner = BusOrchestration(journal)
    await owner.run_once(scheduler)
    await asyncio.gather(*owner._inflight.values())
    assert rosters == [["MU"]]
    await owner.close()


def test_cli_enable_and_early_close(tmp_path, monkeypatch, capsys):
    import json

    from doxagent.message_bus_v2 import cli

    repo, _ = _bus(tmp_path / "bus.db")
    monkeypatch.setattr(
        cli, "DoxAgentSettings", lambda: SimpleNamespace(message_bus_v2_sqlite_path=repo.path)
    )
    monkeypatch.setattr("sys.argv", ["bus", "collection", "enable", "--reason", "manual probe"])
    cli.main()
    assert json.loads(capsys.readouterr().out)["state"] == "ACTIVE"
    monkeypatch.setattr("sys.argv", ["bus", "collection", "disable", "--reason", "done"])
    cli.main()
    assert json.loads(capsys.readouterr().out)["state"] == "DISABLED"


async def test_switch_does_not_reset_checkpoint_or_duplicate_body_jobs(tmp_path):
    repo, service, source, binding = _setup(tmp_path)
    control = CollectionControl(repo)
    calls = []

    class Adapter:
        async def poll(self, context):
            async with context.request_permit():
                calls.append(context.checkpoint)
            return PollResult(messages=[_message(98765)], next_checkpoint={"cursor": "preserved"})

    adapters = SimpleNamespace(resolve=lambda *a, **kw: Adapter(), crawler_plane=None)
    scheduler = GlobalPollScheduler(repo, service, adapters)
    from doxagent.message_bus_v2.schema import utc_now

    try:
        state = enable(control)
        token = REQUEST_GATE.set(CollectionLease(repo.path, state, [binding]))
        try:
            await scheduler._poll(source, binding, utc_now())
            control.update(enabled=False, actor="test", reason="stop")
            stopped = await scheduler._poll(source, binding, utc_now())
            assert stopped.site_access_deferred_reason == "TEMPORARY_COLLECTION_ENDED"
        finally:
            REQUEST_GATE.reset(token)
        before = len(repo.list_enrichment_jobs())
        # Same article on the next normal run reuses its existing durable body job.
        await scheduler._poll(source, binding, utc_now() + timedelta(days=1))
        assert calls[1] == {"cursor": "preserved"}
        assert len(repo.list_enrichment_jobs()) == before == 1
        assert repo.get_poll_state(binding).checkpoint == {"cursor": "preserved"}
    finally:
        await scheduler.close()


async def test_persisted_intake_survives_closed_acquisition(tmp_path):
    from doxagent.message_bus_v2.collection_override import check_request_allowed
    from doxagent.message_bus_v2.polling import CaptureIntake
    from doxagent.message_bus_v2.schema import utc_now

    repo, service, source, binding = _setup(tmp_path)
    control = CollectionControl(repo)
    state = enable(control)
    repo.polling.commit(
        repo.polling.prepare(
            source=source,
            binding=binding,
            result=PollResult(messages=[_message(54321)]),
            run_id="persisted",
            observed_at=utc_now(),
            checkpoint={"page": 2},
        )
    )
    worker = CaptureIntake(service)
    original = worker.journal.accept_piece

    def accept(*args, **kwargs):
        check_request_allowed()  # Must not inherit the ended acquisition lease.
        return original(*args, **kwargs)

    worker.journal.accept_piece = accept
    token = REQUEST_GATE.set(CollectionLease(repo.path, state, [binding]))
    try:
        control.update(enabled=False, actor="test", reason="stop before intake")
        receipt = await asyncio.wait_for(worker.wait("persisted"), 10)
        assert receipt.queued_count == 1
        assert len(repo.list_enrichment_jobs()) == 1
        assert repo.get_poll_state(binding).checkpoint == {"page": 2}
    finally:
        REQUEST_GATE.reset(token)
        await worker.close()
