import json
from datetime import timedelta

import pytest

from doxagent.event_library.admission_evidence import backfill, ready
from doxagent.event_library.repository import EventLibraryRepository
from doxagent.persistent_runtime_v2.event_branch import branch_library
from doxagent.persistent_runtime_v2.retained_events import build
from doxagent.semantic_clock import boundary
from tests.test_w1_daily_index import DAY, event, publish


def complete(repository):
    for _ in range(200):
        if backfill(repository, 'MU', limit=1):
            return
    raise AssertionError('bounded migration did not finish')


def test_backfill_skips_cold_no_change_ancestors_and_resumes_old_checkpoint(tmp_path):
    source = EventLibraryRepository(tmp_path/'source.sqlite3')
    old = boundary(DAY-timedelta(days=30))
    publish(source, 1, old, [event(1)])
    middle = branch_library(source.path, tmp_path/'middle.sqlite3', 'MU', 1)
    leaf = branch_library(middle.path, tmp_path/'leaf.sqlite3', 'MU', 1)
    publish(leaf, 2, boundary(DAY), [event(2)])
    expected = leaf.fact_admissions('MU', 2)
    expected_index = leaf.runtime_known_events('MU', 2, DAY)
    # The old owner may have checkpointed immediately before the cold branch.
    with leaf._write() as db:
        db.execute('INSERT INTO event_storage_progress VALUES(?,?)', (
            'admissions-backfill:MU', json.dumps({'paths': [
                [str(middle.path), 1, 1], [str(source.path), -1, 1],
                [str(leaf.path), 1, 2]], 'index': 0, 'phase': 'clocks', 'cursor': 0})))
    middle.path.unlink()
    complete(leaf)
    source.path.unlink()
    assert leaf.fact_admissions('MU', 2) == expected
    assert leaf.runtime_known_events('MU', 2, DAY) == expected_index


@pytest.mark.parametrize('copy_kind', ['branch', 'retained'])
def test_completed_evidence_survives_copy_and_content_reversion(tmp_path, copy_kind):
    source = EventLibraryRepository(tmp_path/'source.sqlite3')
    old = boundary(DAY-timedelta(days=30))
    publish(source, 1, old, [event(1)])
    revised = event(1, facts=[{'fact_id': 'F1', 'proposition': 'changed',
                              'assertion_state': 'ACTUAL'}])
    publish(source, 2, boundary(DAY), [revised])
    complete(source)
    target = tmp_path/'child.sqlite3'
    if copy_kind == 'branch':
        child = branch_library(source.path, target, 'MU', 2)
    else:
        build(source.path, target, 'MU', [2])
        child = EventLibraryRepository(target)
    with child._read() as db:
        assert ready(db, 'MU')
    source.path.unlink()
    publish(child, 3, boundary(DAY), [event(1)])
    assert child.fact_admissions('MU', 3)['F1'] == (1, old)
    assert child.runtime_known_events('MU', 3, DAY) == []
