from types import SimpleNamespace

from doxagent.message_bus_v2.collection_plane.store import AcquisitionStore
from doxagent.message_bus_v2.collection_plane.worker import Collector
from doxagent.message_bus_v2.schema import AcquisitionMode, utc_now
from doxagent.message_bus_v2.scheduler import GlobalPollScheduler
from tests.test_content_enrichment_hub import _setup


def test_retired_reuters_tail_completion_survives_reload_and_only_new_head_reopens(tmp_path):
    _, _, source, binding = _setup(tmp_path)
    source = source.model_copy(update={'source_id': 'reuters_site_search',
        'adapter_ref': 'builtin:reuters_search@1', 'acquisition_mode': AcquisitionMode.BY_SEARCH})
    binding = binding.model_copy(update={'source_id': source.source_id})
    store = AcquisitionStore(tmp_path / 'acquisition.sqlite3', initialize=True)
    head = {'queries': {'q': {'tail_pending': True, 'head_at': 'old-head'}}}
    completed = {'queries': {'q': {'done': True, 'head_at': 'old-head'}}}
    store.save_unit(source, binding, 'QUERY:q', {'checkpoint': head}, 'current', required=True)
    store.save_unit(source, binding, 'TAIL:q', {'checkpoint': completed}, 'current', required=True)
    store.disable_unit(binding.binding_id, 'TAIL:q')
    store.save_unit(source, binding, 'QUERY:obsolete', {'checkpoint': {'old': True}}, 'old', required=True)
    store.disable_unit(binding.binding_id, 'QUERY:obsolete')
    store.save_unit(source, binding, 'TAIL:obsolete', {'checkpoint': {
        'queries': {'obsolete': {'done': True}}}}, 'old', required=True)
    store.disable_unit(binding.binding_id, 'TAIL:obsolete')
    store.save_unit(source, binding, 'TAIL:partial', {'checkpoint': {
        'queries': {'partial': {'done': False}}}}, 'old', required=True)
    store.disable_unit(binding.binding_id, 'TAIL:partial')
    collector = object.__new__(Collector)
    collector.config = SimpleNamespace(seeds={})
    collector.states = store.states()  # Same path as process restart.
    scheduler = object.__new__(GlobalPollScheduler)
    scheduler._query_plans = {(source.source_id, binding.ticker): {'queries': [
        {'query_key': 'q', 'query': 'Micron'}]}}

    for _ in range(3):
        state = collector.binding_state(binding)
        units = dict(scheduler.work_units(source, binding, state, utc_now(), include_all=True))
        assert not any(v['acquisition_unit'] == 'TAIL:q' for v in units.values())
        assert 'QUERY:obsolete' not in state.units
        assert 'TAIL:obsolete' not in state.units and 'TAIL:partial' not in state.units
        assert collector.states[binding.binding_id, 'TAIL:q']['required'] == -1

    collector.states[binding.binding_id, 'QUERY:q']['state']['checkpoint']['queries']['q']['head_at'] = 'new-head'
    units = dict(scheduler.work_units(source, binding, collector.binding_state(binding), utc_now(), include_all=True))
    tail = next(v for v in units.values() if v['acquisition_unit'] == 'TAIL:q')
    assert tail['checkpoint']['queries']['q']['head_at'] == 'new-head'
    assert not tail['checkpoint']['queries']['q']['done']
