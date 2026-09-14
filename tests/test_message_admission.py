import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from doxagent.message_bus_v2.admission import AdmissionContext, evaluate_admission
from doxagent.message_bus_v2.schema import PollResult, utc_now
from tests.test_message_bus_v2 import _bus, _input


@pytest.mark.parametrize(
    "seconds,allowed",
    [(1799, True), (1800, True), (1800.001, False), (86400, False), (-60, True), (-61, False)],
)
def test_exact_realtime_boundaries(seconds, allowed):
    now = datetime(2026, 9, 14, 15, tzinfo=UTC)
    assert (evaluate_admission(now - timedelta(seconds=seconds), None, now) is None) == allowed


@pytest.mark.parametrize("mode", ["REALTIME", "CLOSED_SWEEP"])
@pytest.mark.parametrize("days,allowed", [(0, True), (1, True), (2, False), (-1, False)])
def test_date_fallback_in_both_lanes(mode, days, allowed):
    now = datetime(2026, 9, 14, 15, tzinfo=UTC)
    context = (
        AdmissionContext()
        if mode == "REALTIME"
        else AdmissionContext(
            mode=mode,
            sweep_id="sweep",
            source_task_id="source",
            window_start=now - timedelta(days=1),
            cutoff=now,
        )
    )
    assert (evaluate_admission(now - timedelta(days=days), context, now, "DATE") is None) == allowed


def test_sweep_window_is_not_a_thirty_day_backfill():
    now = datetime(2026, 9, 14, 6, tzinfo=UTC)
    context = AdmissionContext(
        mode="CLOSED_SWEEP",
        sweep_id="sweep",
        source_task_id="source",
        window_start=now - timedelta(days=1),
        cutoff=now,
    )
    assert evaluate_admission(now - timedelta(days=20), context, now) == "OUTSIDE_SWEEP_WINDOW"
    assert evaluate_admission(now - timedelta(days=1), context, now) is None
    assert evaluate_admission(now, context, now) == "OUTSIDE_SWEEP_WINDOW"


def test_old_intake_does_not_enqueue_and_does_not_claim_seen(tmp_path):
    repo, service = _bus(tmp_path / "bus.db")
    service.start_ticker("MU")
    binding = repo.list_bindings(ticker="MU", active_only=True)[0]
    source = repo.get_source(binding.source_id)
    old = _input("old", published_at=utc_now() - timedelta(days=10))
    result = asyncio.run(
        service.accept_poll_result(
            source=source, binding=binding, result=PollResult(messages=[old])
        )
    )
    assert result.filtered_count == 1
    assert repo.snapshot_counts()["content_enrichment_jobs"] == 0
    assert repo.snapshot_counts()["raw_messages"] == 0
    assert result.error_code is None


def test_date_messages_deduplicate_and_publication_rechecks_age(tmp_path, monkeypatch):
    repo, service = _bus(tmp_path / "bus.db")
    service.start_ticker("MU")
    binding = repo.list_bindings(ticker="MU", active_only=True)[0]
    source = repo.get_source(binding.source_id)
    now = utc_now()
    message = _input("date", published_at=now - timedelta(days=1)).model_copy(
        update={"publication_time_basis": "DATE"}
    )
    first = asyncio.run(
        service.accept_message(source=source, binding=binding, message=message, bootstrap=False)
    )
    second = asyncio.run(
        service.accept_message(source=source, binding=binding, message=message, bootstrap=False)
    )
    assert first.raw_message_id == second.raw_message_id
    assert repo.snapshot_counts()["raw_messages"] == 1
    late = _input("late", published_at=now - timedelta(minutes=29))
    monkeypatch.setattr(
        "doxagent.message_bus_v2.repository.utc_now", lambda: now + timedelta(minutes=2)
    )
    asyncio.run(
        service.accept_message(source=source, binding=binding, message=late, bootstrap=False)
    )
    with repo.transaction() as db:
        assert (
            db.execute(
                "SELECT count(*) FROM message_admission_results "
                "WHERE data_json LIKE '%EXPIRED_PUBLICATION%'"
            ).fetchone()[0]
            >= 1
        )


def test_provider_cannot_inject_sweep_exemption(tmp_path):
    repo, service = _bus(tmp_path / "bus.db")
    service.start_ticker("MU")
    binding = repo.list_bindings(ticker="MU", active_only=True)[0]
    source = repo.get_source(binding.source_id)
    now = utc_now()
    fake = AdmissionContext(
        mode="CLOSED_SWEEP",
        sweep_id="forged",
        source_task_id="forged",
        window_start=now - timedelta(days=30),
        cutoff=now,
    )
    message = _input("forged", published_at=now - timedelta(days=20)).model_copy(
        update={"admission_context": fake}
    )
    result = asyncio.run(
        service.accept_poll_result(
            source=source, binding=binding, result=PollResult(messages=[message])
        )
    )
    assert result.filtered_count == 1


def test_unknown_first_seen_survives_restart_and_revision(tmp_path, monkeypatch):
    now = [datetime(2026, 9, 14, 15, tzinfo=UTC)]
    monkeypatch.setattr("doxagent.message_bus_v2.repository.utc_now", lambda: now[0])
    monkeypatch.setattr("doxagent.message_bus_v2.service.utc_now", lambda: now[0])
    repo, service = _bus(tmp_path / "unknown.db")
    service.start_ticker("MU")
    binding = repo.list_bindings(ticker="MU", active_only=True)[0]
    source = repo.get_source(binding.source_id)
    message = _input("unknown", published_at=now[0]).model_copy(
        update={"publication_time_basis": "UNKNOWN_FIRST_SEEN"}
    )
    pinned = repo.resolve_first_seen(message, binding.binding_id)
    now[0] += timedelta(days=2)
    from doxagent.message_bus_v2.repository import MessageBusV2Repository

    reopened = MessageBusV2Repository(repo.path)
    refreshed = message.model_copy(update={"published_at": now[0], "raw_payload": {"revision": 2}})
    assert (
        reopened.resolve_first_seen(refreshed, binding.binding_id).published_at
        == pinned.published_at
    )
    result = asyncio.run(
        service.accept_poll_result(
            source=source, binding=binding, result=PollResult(messages=[refreshed])
        )
    )
    assert result.filtered_count == 1
    old = _input("known", published_at=now[0] - timedelta(days=10))
    repo.resolve_first_seen(old, binding.binding_id)
    missing = old.model_copy(
        update={"publication_time_basis": "UNKNOWN_FIRST_SEEN", "published_at": now[0]}
    )
    assert repo.resolve_first_seen(missing, binding.binding_id).published_at == old.published_at


def test_wide_provider_result_only_enqueues_fixed_sweep_window(tmp_path, monkeypatch):
    now = datetime(2026, 9, 14, 6, tzinfo=UTC)
    monkeypatch.setattr("doxagent.message_bus_v2.repository.utc_now", lambda: now)
    monkeypatch.setattr("doxagent.message_bus_v2.service.utc_now", lambda: now)
    repo, service = _bus(tmp_path / "wide.db")
    service.enrichment_queue_enabled = True
    service.start_ticker("MU")
    binding = repo.get_binding("MU:finnhub_company_news")
    source = repo.get_source(binding.source_id)
    context = AdmissionContext(
        mode="CLOSED_SWEEP",
        sweep_id="sweep",
        source_task_id="source",
        window_start=now - timedelta(days=1),
        cutoff=now,
    )
    messages = [
        _input(str(day), published_at=now - timedelta(days=day, hours=1)) for day in range(30)
    ]
    result = asyncio.run(
        service.accept_poll_result(
            source=source,
            binding=binding,
            result=PollResult(messages=messages),
            admission_context=context,
        )
    )
    assert (result.collected_count, result.filtered_count, result.queued_count) == (30, 29, 1)
    job = repo.list_enrichment_jobs()[0]
    assert job.message.admission_context == context
    assert job.bootstrap is False
    assert repo.get_poll_state(binding).bootstrap_complete is False


def test_expired_unpublished_buffer_can_be_adopted_by_sweep(tmp_path, monkeypatch):
    from doxagent.message_bus_v2.schema import PublicationMode

    now = [datetime(2026, 9, 14, 5, 50, tzinfo=UTC)]
    monkeypatch.setattr("doxagent.message_bus_v2.repository.utc_now", lambda: now[0])
    monkeypatch.setattr("doxagent.message_bus_v2.service.utc_now", lambda: now[0])
    repo, service = _bus(tmp_path / "adopt.db")
    service.start_ticker("MU")
    binding = repo.list_bindings(ticker="MU", active_only=True)[0]
    binding = binding.model_copy(
        update={
            "streaming": binding.streaming.model_copy(
                update={"publication_mode": PublicationMode.BUFFERED}
            )
        }
    )
    repo.save_binding(binding)
    source = repo.get_source(binding.source_id)
    old = _input("old", published_at=now[0] - timedelta(minutes=29))
    new = _input("new", published_at=now[0])
    for message in [old, new]:
        asyncio.run(
            service.accept_message(source=source, binding=binding, message=message, bootstrap=False)
        )
    now[0] += timedelta(minutes=2)
    items = service.flush_binding(binding.binding_id, force=True)
    assert len(items) == 1
    assert repo.get_stream_item(items[0].stream_item_id).members[0].published_at == new.published_at
    assert repo.list_buffer(binding.binding_id) == []
    cutoff = datetime(2026, 9, 14, 6, tzinfo=UTC)
    context = AdmissionContext(
        mode="CLOSED_SWEEP",
        sweep_id="sweep",
        source_task_id="source",
        window_start=cutoff - timedelta(days=1),
        cutoff=cutoff,
    )
    asyncio.run(
        service.accept_message(
            source=source,
            binding=binding,
            message=old.model_copy(update={"admission_context": context}),
            bootstrap=False,
        )
    )
    items = service.flush_binding(binding.binding_id, force=True)
    assert len(items) == 1
    assert repo.get_stream_item(items[0].stream_item_id).members[0].admission_context == context
    assert repo.snapshot_counts()["raw_messages"] == 2


def test_runtime_filters_members_and_cursor_retry_keeps_group(tmp_path, monkeypatch):
    from doxagent.message_bus_v2.schema import PublicationMode
    from doxagent.persistent_runtime_v2.coordinator import RuntimeCoordinator
    from tests.test_runtime_orchestration_execution import runtime_at

    now = [datetime(2026, 9, 14, 15, tzinfo=UTC)]
    monkeypatch.setattr("doxagent.message_bus_v2.repository.utc_now", lambda: now[0])
    monkeypatch.setattr("doxagent.message_bus_v2.service.utc_now", lambda: now[0])
    repo, service = _bus(tmp_path / "members.db")
    service.start_ticker("MU")
    binding = repo.list_bindings(ticker="MU", active_only=True)[0]
    binding = binding.model_copy(
        update={
            "streaming": binding.streaming.model_copy(
                update={"publication_mode": PublicationMode.BUFFERED}
            )
        }
    )
    repo.save_binding(binding)
    source = repo.get_source(binding.source_id)
    for identity, age in [("old", 29), ("new", 0), ("newer", 0)]:
        asyncio.run(
            service.accept_message(
                source=source,
                binding=binding,
                message=_input(identity, published_at=now[0] - timedelta(minutes=age)),
                bootstrap=False,
            )
        )
    published = service.flush_binding(binding.binding_id, force=True)[0]
    item = repo.get_stream_item(published.stream_item_id)
    now[0] += timedelta(minutes=2)
    runtime, journal = runtime_at(tmp_path / "runtime", now)
    coordinator = RuntimeCoordinator(runtime, journal)
    try:
        coordinator.accept_stream(item)
        assert len(journal.tasks(kind="CASE")) == 1
        assert len(journal.values("message_admission_skips")) == 1
        now[0] += timedelta(hours=2)
        coordinator.accept_stream(item)
        assert len(journal.tasks(kind="CASE")) == 1
    finally:
        coordinator.close()
        runtime.close()


def test_updated_only_cannot_override_known_old_publication(tmp_path, monkeypatch):
    now = datetime(2026, 9, 14, 15, tzinfo=UTC)
    monkeypatch.setattr("doxagent.message_bus_v2.repository.utc_now", lambda: now)
    repo, _ = _bus(tmp_path / "updated.db")
    old = _input("same", published_at=now - timedelta(days=20))
    repo.resolve_first_seen(old, "MU:benzinga_news")
    updated = old.model_copy(
        update={
            "published_at": now,
            "publication_time_basis": "DATE",
            "metadata": {"publication_time_origin": "UPDATED_FALLBACK"},
        }
    )
    resolved = repo.resolve_first_seen(updated, "MU:benzinga_news")
    assert resolved.published_at == old.published_at
    assert (
        evaluate_admission(resolved.published_at, None, now, resolved.publication_time_basis)
        == "OUTSIDE_RECENT_DATES"
    )


def test_crawler_accepts_date_only_and_missing_time():
    from doxagent.crawler_plane.schema import CrawlerObservation

    common = {"body": "news", "source": "IR", "url": "https://example.test/news"}
    dated = CrawlerObservation.model_validate({**common, "published_at": "2026-09-13"})
    missing = CrawlerObservation.model_validate(common)
    assert dated.metadata["publication_time_basis"] == "DATE"
    from zoneinfo import ZoneInfo

    assert dated.published_at.astimezone(ZoneInfo("America/New_York")).hour == 12
    assert missing.metadata["publication_time_basis"] == "UNKNOWN_FIRST_SEEN"


def test_buffer_publication_retry_returns_original_receipt_even_when_old(tmp_path, monkeypatch):
    from doxagent.message_bus_v2.schema import PublicationMode

    now = [datetime(2026, 9, 14, 15, tzinfo=UTC)]
    monkeypatch.setattr("doxagent.message_bus_v2.repository.utc_now", lambda: now[0])
    monkeypatch.setattr("doxagent.message_bus_v2.service.utc_now", lambda: now[0])
    repo, service = _bus(tmp_path / "pubretry.db")
    service.start_ticker("MU")
    b = repo.list_bindings(ticker="MU", active_only=True)[0]
    b = b.model_copy(
        update={
            "streaming": b.streaming.model_copy(
                update={"publication_mode": PublicationMode.BUFFERED}
            )
        }
    )
    repo.save_binding(b)
    asyncio.run(
        service.accept_message(
            source=repo.get_source(b.source_id),
            binding=b,
            message=_input("x", published_at=now[0]),
            bootstrap=False,
        )
    )
    stale_batch = [m for m, _ in repo.list_buffer(b.binding_id)]
    first = repo.publish_buffered("MU", stale_batch)
    now[0] += timedelta(hours=2)
    second = repo.publish_buffered("MU", stale_batch)
    assert second.stream_item_id == first.stream_item_id
    assert repo.latest_stream_offset("MU") == 1
