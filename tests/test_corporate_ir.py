import asyncio
from datetime import timedelta
from email.utils import format_datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from doxagent.message_bus_v2.corporate_ir import SOURCE_ID
from doxagent.message_bus_v2.corporate_ir.adapter import CorporateIrAdapter
from doxagent.message_bus_v2.corporate_ir.catalog import initial_endpoints
from doxagent.message_bus_v2.corporate_ir.coordinator import IrCoordinator
from doxagent.message_bus_v2.corporate_ir.http import Capture, PublicHttp
from doxagent.message_bus_v2.corporate_ir.parsers import IrContractError, parse_listing, publication
from doxagent.message_bus_v2.corporate_ir.repository import get_state, save_state
from doxagent.message_bus_v2.corporate_ir.schema import IrEndpointState
from doxagent.message_bus_v2.schema import UpdateActor, utc_now
from tests.fixtures.message_bus_v2 import _bus


@pytest.mark.parametrize("spec", initial_endpoints(), ids=lambda s: s.endpoint_id)
def test_actual_listing_contracts(spec):
    raw = (
        Path(__file__).parent / "fixtures" / "corporate_ir" / (spec.endpoint_id + ".txt")
    ).read_text(encoding="utf-8")
    rows, _ = parse_listing(spec, raw)
    assert len(rows) == 2
    assert all(m.title and m.external_id == m.url for m in rows)
    assert all(m.publication_time_basis in {"EXACT", "DATE"} for m in rows)
    assert all(m.metadata["ir_endpoint_spec"]["owner_ticker"] == spec.owner_ticker for m in rows)


def test_invalid_precision_and_blocked_empty_are_not_success():
    with pytest.raises(ValueError):
        publication("2026-2-30TAD::Z", "America/New_York", exact=True)
    day, basis = publication("October 1, 2026", "America/New_York")
    assert basis == "DATE" and day.hour == 0
    spec = initial_endpoints()[4]
    with pytest.raises(IrContractError):
        parse_listing(spec, "<html><title>Access Denied</title></html>")
    with pytest.raises(IrContractError):
        parse_listing(spec, "<html><title>Loading</title></html>")
    assert parse_listing(initial_endpoints()[0], "<rss><channel/></rss>")[0] == []


def configured(tmp_path, ticker="COHR"):
    repo, bus = _bus(tmp_path / "bus.db")
    bus.enrichment_queue_enabled = True
    bus.start_ticker(ticker)
    ids = [s.endpoint_id for s in initial_endpoints() if s.owner_ticker == ticker]
    binding = bus.configure_binding(
        ticker=ticker,
        source_id=SOURCE_ID,
        source_parameters={"endpoint_ids": ids},
        actor=UpdateActor.USER,
    )
    source = repo.get_source(SOURCE_ID)
    adapter = CorporateIrAdapter()
    scheduler = SimpleNamespace(
        repository=repo,
        service=bus,
        runtime_orchestration=None,
        adapters=SimpleNamespace(resolve=lambda *a, **k: adapter),
    )
    coordinator = IrCoordinator(scheduler)
    for spec in initial_endpoints():
        if spec.owner_ticker == ticker:
            save_state(
                repo,
                IrEndpointState(
                    binding_id=binding.binding_id,
                    endpoint_id=spec.endpoint_id,
                    config_revision=spec.fingerprint,
                    next_due_at=utc_now() - timedelta(seconds=1),
                ),
            )
    return repo, bus, source, binding, adapter, coordinator


def fresh_rss():
    date = format_datetime(utc_now())
    return f"<rss><channel><item><title>A company announcement</title><link>https://www.intc.com/new-release</link><pubDate>{date}</pubDate></item></channel></rss>"


@pytest.mark.asyncio
async def test_sibling_hang_does_not_delay_success_or_next_poll(tmp_path):
    repo, bus, source, binding, adapter, coordinator = configured(tmp_path)
    gate = asyncio.Event()

    async def fetch(spec, **kwargs):
        if spec.endpoint_id == "cohr.corporate":
            await gate.wait()
            raise TimeoutError("one listing stalled")
        raw = (Path(__file__).parent / "fixtures/corporate_ir/cohr.financial.txt").read_text(
            encoding="utf-8"
        )
        return Capture(200, spec.entry_url, raw, {"etag": "one"}, len(raw))

    adapter.http.fetch = AsyncMock(side_effect=fetch)
    try:
        await coordinator.tick()
        for _ in range(100):
            if get_state(repo, binding.binding_id, "cohr.financial").last_success_at:
                break
            await asyncio.sleep(0.01)
        assert get_state(repo, binding.binding_id, "cohr.financial").status == "succeeded"
        assert not coordinator.inflight[(binding.binding_id, "cohr.corporate")][0].done()
        assert repo.get_poll_state(binding).status.value == "partial"
        financial = get_state(repo, binding.binding_id, "cohr.financial")
        save_state(
            repo,
            financial.model_copy(
                update={"next_due_at": utc_now() - timedelta(seconds=1), "next_dispatch_at": None}
            ),
        )
        await coordinator.tick()
        await asyncio.sleep(0.08)
        assert (
            sum(
                call.args[0].endpoint_id == "cohr.financial"
                for call in adapter.http.fetch.call_args_list
            )
            == 2
        )
    finally:
        await coordinator.close()
        await adapter.close()


@pytest.mark.asyncio
async def test_atomic_enqueue_failure_rolls_back_validator_and_jobs(tmp_path, monkeypatch):
    repo, bus, source, binding, adapter, coordinator = configured(tmp_path, "INTC")
    spec = initial_endpoints()[0]
    state = get_state(repo, binding.binding_id, spec.endpoint_id)
    adapter.http.fetch = AsyncMock(
        return_value=Capture(200, spec.entry_url, fresh_rss(), {"etag": "fresh"}, 120)
    )
    original = bus.enqueue_enrichment

    def broken(**kwargs):
        original(**kwargs)
        raise OSError("simulated disk failure after enqueue")

    monkeypatch.setattr(bus, "enqueue_enrichment", broken)
    try:
        await coordinator._run(source, binding, spec, state)
        failed = get_state(repo, binding.binding_id, spec.endpoint_id)
        assert failed.status == "failed" and failed.etag is None
        with repo._connect() as db:
            assert db.execute("SELECT count(*) FROM content_enrichment_jobs").fetchone()[0] == 0
        monkeypatch.setattr(bus, "enqueue_enrichment", original)
        await coordinator._run(source, binding, spec, failed)
        consumed = get_state(repo, binding.binding_id, spec.endpoint_id)
        assert consumed.etag == "fresh" and consumed.bootstrap_complete
        with repo._connect() as db:
            assert db.execute("SELECT count(*) FROM content_enrichment_jobs").fetchone()[0] == 1
    finally:
        await adapter.close()


@pytest.mark.asyncio
async def test_304_requires_baseline_and_does_not_create_articles(tmp_path):
    repo, bus, source, binding, adapter, coordinator = configured(tmp_path, "INTC")
    spec = initial_endpoints()[0]
    state = get_state(repo, binding.binding_id, spec.endpoint_id)
    adapter.http.fetch = AsyncMock(return_value=Capture(304, spec.entry_url, "", {}, 0))
    try:
        await coordinator._run(source, binding, spec, state)
        assert get_state(repo, binding.binding_id, spec.endpoint_id).status == "failed"
        adapter.http.fetch.return_value = Capture(
            200, spec.entry_url, fresh_rss(), {"etag": "first"}, 120
        )
        await coordinator._run(source, binding, spec, state)
        consumed = get_state(repo, binding.binding_id, spec.endpoint_id)
        adapter.http.fetch.return_value = Capture(304, spec.entry_url, "", {}, 0)
        await coordinator._run(source, binding, spec, consumed)
        current = get_state(repo, binding.binding_id, spec.endpoint_id)
        assert current.status == "succeeded" and current.last_change_at == consumed.last_change_at
        assert adapter.http.fetch.call_args.kwargs["headers"]["If-None-Match"] == "first"
        with repo._connect() as db:
            assert db.execute("SELECT count(*) FROM content_enrichment_jobs").fetchone()[0] == 1
    finally:
        await adapter.close()


@pytest.mark.asyncio
async def test_changed_binding_fences_old_result(tmp_path):
    repo, bus, source, binding, adapter, coordinator = configured(tmp_path, "INTC")
    spec = initial_endpoints()[0]
    state = get_state(repo, binding.binding_id, spec.endpoint_id)
    bus.update_binding(binding.binding_id, {"enabled": False}, actor=UpdateActor.USER)
    adapter.http.fetch = AsyncMock(return_value=Capture(200, spec.entry_url, fresh_rss(), {}, 100))
    try:
        await coordinator._run(source, binding, spec, state)
        assert get_state(repo, binding.binding_id, spec.endpoint_id).representation_hash is None
        with repo._connect() as db:
            assert db.execute("SELECT count(*) FROM content_enrichment_jobs").fetchone()[0] == 0
    finally:
        await adapter.close()


@pytest.mark.asyncio
async def test_public_transport_does_not_follow_unapproved_redirect():
    requests = []

    def transport(request):
        requests.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://unapproved.example/private"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        http = PublicHttp(client)
        with pytest.raises(ValueError, match="not approved"):
            await http.fetch(initial_endpoints()[0])
    assert len(requests) == 1


def test_binding_owner_validation_and_independent_identity(tmp_path):
    repo, bus, source, binding, adapter, coordinator = configured(tmp_path, "INTC")
    with pytest.raises(ValueError, match="does not belong"):
        bus.update_binding(
            binding.binding_id,
            {"source_parameters": {"endpoint_ids": ["mu.ir"]}},
            actor=UpdateActor.USER,
        )
    assert binding.polling.target_interval_seconds == 30 and binding.polling.active_windows == []
    assert source.site_id is None


def test_owner_configuration_is_idempotent_and_preserves_other_rss(tmp_path):
    from scripts.ops.configure_corporate_ir_sources import TICKERS, configure

    repo, bus = _bus(tmp_path / "bus.db")
    for ticker in TICKERS:
        bus.start_ticker(ticker)
    legacy = bus.configure_binding(
        ticker="INTC",
        source_id="newswire_rss",
        source_parameters={
            "rss_urls": [initial_endpoints()[0].entry_url, "https://other.example/feed"]
        },
        actor=UpdateActor.USER,
    )
    first = configure(bus)
    second = configure(bus)
    assert first["configured_endpoint_count"] == 12
    assert all(s["action"] == "unchanged" for s in second["bindings"])
    assert repo.get_binding(legacy.binding_id).source_parameters["rss_urls"] == [
        "https://other.example/feed"
    ]
    assert len(repo.list_bindings(source_id=SOURCE_ID)) == 9


@pytest.mark.asyncio
async def test_sweep_failed_channel_does_not_prevent_other_channel(tmp_path):
    repo, bus, source, binding, adapter, coordinator = configured(tmp_path)

    async def fetch(spec, **kwargs):
        if spec.endpoint_id == "cohr.corporate":
            return Capture(503, spec.entry_url, "unavailable", {}, 11)
        raw = (Path(__file__).parent / "fixtures/corporate_ir/cohr.financial.txt").read_text(
            encoding="utf-8"
        )
        return Capture(200, spec.entry_url, raw, {}, len(raw))

    adapter.http.fetch = AsyncMock(side_effect=fetch)
    context = SimpleNamespace(
        source=source, binding=binding, checkpoint={}, window_start=utc_now() - timedelta(days=1)
    )
    try:
        first = await adapter.poll(context)
        assert not first.window_done and first.window_coverage == "PARTIAL"
        context.checkpoint = first.next_checkpoint
        second = await adapter.poll(context)
        assert second.messages and second.window_done and second.window_coverage == "PARTIAL"
        assert "cohr.corporate" in second.next_checkpoint["errors"]
        assert not get_state(repo, binding.binding_id, "cohr.financial").bootstrap_complete
    finally:
        await adapter.close()


def test_ir_does_not_consult_calendar_and_wire_keeps_internal_states(tmp_path):
    from doxagent.api_v2.bus import source_status
    from doxagent.message_bus_v2.corporate_ir.repository import aggregate

    repo, bus, source, binding, adapter, coordinator = configured(tmp_path)
    coordinator.scheduler.runtime_orchestration = SimpleNamespace(
        journal=SimpleNamespace(get=lambda *a: None)
    )
    assert len(coordinator._roster()) == 2
    now = utc_now()
    with repo.atomic_intake():
        aggregate(repo, source, binding, now)
    poll = repo.get_poll_state(binding).model_dump(mode="json")
    poll["target_due_at"] = (now + timedelta(seconds=30)).isoformat()
    values = {
        "native:source_definitions": source.model_dump(mode="json"),
        "native:poll_states": poll,
        "ticker": {"run_state": "RUNNING"},
    }
    store = SimpleNamespace(get=lambda kind, *args: values.get(kind))
    view = {"as_of": now.isoformat(), "wire": {"clock": {"session": {"value": "CLOSED_SLEEP"}}}}
    result = source_status(store, binding.model_dump(mode="json"), 1, view)
    assert result["countdown_state"] == "SCHEDULED"
    assert len(result["internal_endpoints"]) == 2


@pytest.mark.asyncio
async def test_anchor_recovery_keeps_head_validator_and_clears_real_gap(tmp_path):
    repo, bus, source, binding, adapter, coordinator = configured(tmp_path)
    spec = initial_endpoints()[4]
    raw = (Path(__file__).parent / "fixtures/corporate_ir/cohr.corporate.txt").read_text(
        encoding="utf-8"
    )
    head = raw.replace(
        "</html>", '<li class="pager__item--next"><a href="?page=1">Next</a></li></html>'
    )
    rows, _ = parse_listing(spec, raw)
    anchor = "https://ir.coherent.com/news-releases/news-release-details/old-anchor"
    archive = raw.replace(
        rows[0].url.split("ir.coherent.com")[1], "/news-releases/news-release-details/old-anchor"
    )
    state = get_state(repo, binding.binding_id, spec.endpoint_id).model_copy(
        update={
            "bootstrap_complete": True,
            "representation_hash": "previous",
            "checkpoint": {"items": {anchor: "old"}},
        }
    )
    save_state(repo, state)
    adapter.http.fetch = AsyncMock(
        side_effect=[
            Capture(200, spec.entry_url, head, {"etag": "head"}, len(head)),
            Capture(200, spec.entry_url + "?page=1", archive, {"etag": "archive"}, len(archive)),
        ]
    )
    try:
        await coordinator._run(source, binding, spec, state)
        saved = get_state(repo, binding.binding_id, spec.endpoint_id)
        assert saved.etag == "head" and saved.checkpoint["coverage"] == "COMPLETE"
        assert not saved.checkpoint.get("recovery_url")
        assert saved.representation_hash == Capture(200, "", head, {}, 0).digest
        assert adapter.http.fetch.await_count == 2
    finally:
        await adapter.close()
