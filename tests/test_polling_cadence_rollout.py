from doxagent.message_bus_v2.manifests import initial_sources, initial_default_profile
from doxagent.message_bus_v2.polling_cadence import RETIRED_SOURCES, SOURCE_INTERVALS, unit_interval
from doxagent.message_bus_v2.schema import sha256_text

def test_approved_defaults_and_retirement():
    sources = {s.source_id: s for s in initial_sources()}
    assert not RETIRED_SOURCES.intersection(sources)
    for sid, period in SOURCE_INTERVALS.items():
        if sid not in sources:  # Existing manually registered official feeds retain stored defaults.
            continue
        assert sources[sid].default_polling_config.target_interval_seconds == period
    assert sources['yahoo_finance_news'].default_polling_config.target_interval_seconds == 30
    assert sources['ibkr_news'].default_polling_config.target_interval_seconds == 20
    assert sources['reuters_site_search'].default_polling_config.target_interval_seconds == 60
    ir = {e['endpoint_id']: e['target_interval_seconds'] for e in sources['corporate_ir_press_releases'].default_parameters['endpoints']}
    assert ir['be.press'] == ir['mu.ir'] == 30
    assert ir['mu.newsroom'] == ir['cohr.financial'] == 20
    for entry in initial_default_profile().entries:
        assert entry.polling.target_interval_seconds == SOURCE_INTERVALS.get(entry.source_id, 60)

def test_workday_uses_slow_endpoint_without_slowing_siblings():
    from types import SimpleNamespace
    source = SimpleNamespace(source_id='enterprise_apps_official_rss')
    workday = 'FEED:' + sha256_text('https://newsroom.workday.com/press-releases?pagetemplate=rss')[:24]
    assert unit_interval(source, workday, 20) == 30
    assert unit_interval(source, workday, 60) == 60
    assert unit_interval(source, 'FEED:other', 20) == 20


def test_owner_apply_is_idempotent_and_preserves_disabled_subscription(tmp_path):
    import runpy
    from pathlib import Path
    from doxagent.message_bus_v2.repository import MessageBusV2Repository
    from doxagent.message_bus_v2.service import MessageBusV2Service
    from doxagent.message_bus_v2.schema import UpdateActor
    from doxagent.message_bus_v2.manifests import _source
    configure = runpy.run_path(str(Path(__file__).parents[1] / 'scripts/ops/configure_polling_cadence.py'))['configure']
    repo = MessageBusV2Repository(tmp_path / 'bus.sqlite3')
    service = MessageBusV2Service(repo)
    source = next(s for s in initial_sources() if s.source_id == 'benzinga_news')
    service.register_source(source)
    binding = service.configure_binding(ticker='MU', source_id=source.source_id, actor=UpdateActor.USER)
    service.update_binding(binding.binding_id, {'enabled': False, 'polling': {**binding.polling.model_dump(mode='json'), 'target_interval_seconds': 60}}, actor=UpdateActor.USER)
    legacy = _source('globenewswire_semiconductors_rss', 'Legacy')
    service.register_source(legacy)
    service.configure_binding(ticker='MU', source_id=legacy.source_id, actor=UpdateActor.USER)
    report = configure(service, apply=True)
    updated = repo.get_binding(binding.binding_id)
    assert updated.polling.target_interval_seconds == 15
    assert not updated.enabled
    assert repo.get_source(legacy.source_id) is None
    assert repo.list_bindings(source_id=legacy.source_id) == []
    again = configure(service, apply=True)
    assert not any(s['changed'] for s in again['sources'])
    assert again['removed'] == []
    assert repo.get_binding(binding.binding_id).version == updated.version


def test_cache_key_changes_with_subminute_poll_not_every_request():
    from datetime import datetime, UTC
    from types import SimpleNamespace as N
    from doxagent.message_bus_v2.public_listing import fresh_query_bucket
    context = N(binding=N(polling=N(target_interval_seconds=20)), requested_at=datetime.fromtimestamp(120, UTC))
    assert fresh_query_bucket(context) == 6
    context.requested_at = datetime.fromtimestamp(139, UTC)
    assert fresh_query_bucket(context) == 6
    context.requested_at = datetime.fromtimestamp(140, UTC)
    assert fresh_query_bucket(context) == 7
