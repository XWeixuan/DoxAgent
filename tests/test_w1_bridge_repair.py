from types import SimpleNamespace
from datetime import timedelta

import pytest

from tests.test_w1_daily_index import DAY, library, publish, event
from tests.test_persistent_runtime_v2 import _FakeResponses, _FakePolicies
from doxagent.semantic_clock import boundary
from doxagent.event_library.repository import EventLibraryRepository
from doxagent.event_library.provider import PublishedEventLibraryReader
from doxagent.persistent_runtime_v2.providers import PublishedEventLibraryRuntimeProvider
from doxagent.persistent_runtime_v2.event_branch import branch_library
from doxagent.persistent_runtime_v2.daily_index import freeze_index, retain_new_library_facts, fact_candidate
from doxagent.persistent_runtime_v2.repository import InMemoryPersistentRuntimeV2Repository, SQLitePersistentRuntimeV2Repository
from doxagent.persistent_runtime_v2.service import PersistentRuntimeV2Service


@pytest.mark.parametrize('kind', ['memory', 'sqlite'])
def test_root_switch_diffs_full_library_and_filters_existing_pollution(tmp_path, kind):
    root, repo, provider = library(tmp_path)
    publish(repo, 1, boundary(DAY-timedelta(days=40)), [event(1, ref=True), event(2)])
    runtime = (InMemoryPersistentRuntimeV2Repository() if kind == 'memory'
               else SQLitePersistentRuntimeV2Repository(tmp_path/'runtime.db'))
    index, _, _ = freeze_index(runtime, provider, provider.current_index('MU'), 'MU', DAY,
                              str(root), boundary(DAY), 'fixed prompt')
    assert 'E2 |' not in index.known_event_index
    old = repo.published_events('MU', 1)[1]
    candidate = fact_candidate(old, old.facts[0])
    for source in ['library-bridge:MU:old:v1:F2', 'runtime-original']:
        runtime.allocate_provisional(ticker='MU', trading_date=DAY, source_message_id=source,
            candidate_index=0, candidate=candidate, published_max_event_numeric_id=2,
            already_published=True)
    historical = [v.model_dump(mode='json') for v in runtime.list_provisional('MU', DAY)]
    branch_root = tmp_path/'branch'
    branch = branch_library(repo.path, branch_root/'US/MU/event_library.sqlite3', 'MU', 1)
    current = PublishedEventLibraryRuntimeProvider(PublishedEventLibraryReader(branch_root))
    retain_new_library_facts(runtime, provider, 'MU', DAY, frozen_index=index,
        frozen_root=str(root), current_index=current.current_index('MU'), current_root=str(branch_root))
    assert len(runtime.list_provisional('MU', DAY)) == 2
    publish(branch, 2, boundary(DAY), [event(1, ref=False), event(3)])
    retain_new_library_facts(runtime, provider, 'MU', DAY, frozen_index=index,
        frozen_root=str(root), current_index=current.current_index('MU'), current_root=str(branch_root))
    assert len(runtime.list_provisional('MU', DAY)) == 3
    if kind == 'sqlite':
        runtime = SQLitePersistentRuntimeV2Repository(tmp_path/'runtime.db')
    service = PersistentRuntimeV2Service(repository=runtime, responses=_FakeResponses(),
        known_events=provider, policies=_FakePolicies(), dispatch_effects=False)
    case = SimpleNamespace(ticker='MU', trading_date=DAY,
        frozen_inputs={'w1_daily_index':{'event_library_root':str(root),'event_library_version':1}},
        version_pin=SimpleNamespace(event_library_version=2))
    try:
        visible = service._visible_provisional(case)
        assert [v.candidate.proposition for v in visible] == ['fact 2', 'fact 3']
        assert visible[0].source_message_id == 'runtime-original'
        assert [v.model_dump(mode='json') for v in runtime.list_provisional('MU', DAY)][:2] == historical
        assert runtime.daily_known_index('MU', DAY)['index'] == index.model_dump(mode='json')
        assert runtime.list_daily_candidates('MU', DAY) == []
    finally:
        service.close()


def test_unrelated_root_does_not_compare_version_numbers(tmp_path):
    root, repo, provider = library(tmp_path)
    publish(repo, 1, boundary(DAY), [event(1)])
    publish(repo, 2, boundary(DAY), [event(2)])
    other_root = tmp_path/'independent'
    other = EventLibraryRepository(other_root/'US/MU/event_library.sqlite3')
    publish(other, 1, boundary(DAY), [event(1), event(3)])
    current = PublishedEventLibraryRuntimeProvider(PublishedEventLibraryReader(other_root))
    runtime = InMemoryPersistentRuntimeV2Repository()
    retain_new_library_facts(runtime, provider, 'MU', DAY, frozen_index=provider.current_index('MU'),
        frozen_root=str(root), current_index=current.current_index('MU'), current_root=str(other_root))
    assert [v.candidate.proposition for v in runtime.list_provisional('MU', DAY)] == ['fact 3']
