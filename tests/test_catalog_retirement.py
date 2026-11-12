import pytest

from doxagent.message_bus_v2.manifests import initial_default_profile, initial_sources
from doxagent.message_bus_v2.retired_sources import REPLACEMENTS
from doxagent.message_bus_v2.schema import DefaultProfileEntry, UpdateActor
from scripts.ops.retire_catalog_sources import configure
from tests.fixtures.message_bus_v2 import _bus, _input


def test_retired_ids_cannot_be_bootstrapped_or_registered(tmp_path):
    repo, service = _bus(tmp_path / "bus.db")
    assert not set(REPLACEMENTS) & {s.source_id for s in initial_sources()}
    assert not set(REPLACEMENTS) & {e.source_id for e in initial_default_profile().entries}
    source = service.require_source("benzinga_news").model_copy(update={"source_id": "technews_ai"})
    with pytest.raises(ValueError, match="source retired"):
        service.register_source(source)


async def test_retirement_does_not_flush_and_preserves_history_and_other_sources(tmp_path):
    repo, service = _bus(tmp_path / "bus.db")
    service.start_ticker("MU")
    # Seed a database containing a source registered before retirement.
    source = service.require_source("benzinga_news").model_copy(
        update={"source_id": "globenewswire_search"}
    )
    repo.save_source(source)
    binding = service.configure_binding(
        ticker="MU", source_id=source.source_id, actor=UpdateActor.SYSTEM
    )
    service.update_binding(
        binding.binding_id,
        {
            "streaming": {
                "publication_mode": "buffered",
                "buffer": {"max_items": 10, "max_wait_seconds": 600},
            }
        },
        actor=UpdateActor.USER,
    )
    binding = repo.get_binding(binding.binding_id)
    repo.save_poll_unit_state(repo.get_poll_state(binding), "old-unit")
    profile = repo.get_default_profile()
    service.save_default_profile(
        profile.model_copy(
            update={"entries": [*profile.entries, DefaultProfileEntry(source_id=source.source_id)]}
        )
    )
    await service.accept_message(
        source=source, binding=binding, message=_input("retired-buffer"), bootstrap=False
    )
    before_raw = len(repo.list_raw(ticker="MU"))
    before_standard = len(repo.list_standard(ticker="MU"))
    before_offset = repo.latest_stream_offset("MU")
    unused = service.require_source("benzinga_news")
    service.disable_source(unused.source_id, actor=UpdateActor.USER)
    # A genuine disabled source is retained; only the explicit obsolete roster is removed.
    preview = configure(service)
    assert repo.get_source(source.source_id) is not None and preview["applied"] is False
    result = configure(service, apply=True)
    assert any(r["source_id"] == source.source_id for r in result["removed"])
    assert repo.get_source(source.source_id) is None
    assert repo.get_binding(binding.binding_id, include_tombstoned=True) is None
    assert len(repo.list_raw(ticker="MU")) == before_raw
    assert len(repo.list_standard(ticker="MU")) == before_standard
    assert repo.latest_stream_offset("MU") == before_offset
    assert repo.list_source_revisions(source.source_id)
    assert repo.get_source(unused.source_id) is not None
    assert all(e.source_id != source.source_id for e in repo.get_default_profile().entries)
    with repo._connect() as db:
        assert not db.execute(
            "SELECT 1 FROM poll_unit_states WHERE binding_id=?", (binding.binding_id,)
        ).fetchone()
    assert configure(service, apply=True)["removed"] == []


def test_retirement_cancels_only_live_work_and_does_not_requeue_deleted_source(tmp_path):
    from doxagent.message_bus_v2.live_repository import LiveRepository
    from doxagent.message_bus_v2.schema import utc_now

    repo, service = _bus(tmp_path / "bus.db")
    source = service.require_source("benzinga_news").model_copy(
        update={"source_id": "globenewswire_search"}
    )
    repo.save_source(source)
    binding = service.configure_binding(
        ticker="MU", source_id=source.source_id, actor=UpdateActor.SYSTEM
    )
    live = LiveRepository(repo)
    live.install()
    now = utc_now().isoformat()
    message = _input("queued-retired", published_at=utc_now()).model_copy(
        update={"body": None, "raw_payload": {}}
    )
    prepared = live.prepare_input(
        source, [(binding.binding_id, "MU", 0)], [message], observed_at=now, unit="HEAD"
    )
    live.accept(prepared)
    result = configure(service, apply=True)
    assert result["cancelled_articles"] == 1 and result["cancelled_deliveries"] == 1
    with repo.transaction() as db:
        assert db.execute("SELECT state FROM live_articles").fetchone()[0] == "CANCELLED"
        assert db.execute("SELECT state FROM live_deliveries").fetchone()[0] == "CANCELLED"
        # A retained completed historical article is never scheduled for BODY recheck again.
        db.execute(
            "UPDATE live_articles SET state='READY',last_error='domain_cooldown',next_check_at=?",
            (now,),
        )
    assert live.enqueue_checks() == 0
    with repo._connect() as db:
        assert db.execute("SELECT state FROM live_articles").fetchone()[0] == "READY"
