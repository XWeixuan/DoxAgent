import json
import runpy
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from doxagent.event_library.compiler import EventLibraryViewCompiler
from doxagent.event_library.contracts import FrozenRuntimeSnapshot
from doxagent.event_library.repository import EventLibraryRepository
from doxagent.event_library.service import EventLibraryService
from doxagent.persistent_runtime_v2.event_branch import branch_library
from tests.test_codex_event_library_incremental import FIXTURE, _publish_v1


def selected_branch(tmp_path, *, cache):
    source = EventLibraryRepository(tmp_path / 'source.db')
    _publish_v1(source, tmp_path / 'fixture')
    with source._write() as db:
        db.execute("INSERT INTO library_versions VALUES('MU',2,1,'[]','PUBLISHED',"
                   "'2026-01-02','2026-01-02T00:00:00+00:00')")
        db.execute("UPDATE library_heads SET published_version=2 WHERE ticker='MU'")
    if cache:
        EventLibraryViewCompiler(source).reference_view_delta('MU', from_version=1, to_version=2)
    return branch_library(source.path, tmp_path / 'branch.db', 'MU', 2)


def test_selected_branch_exports_cached_delta_without_parent_history(tmp_path):
    branch = selected_branch(tmp_path, cache=True)
    with branch._read() as db:
        assert [r[0] for r in db.execute('SELECT version FROM library_versions')] == [2]
    exports = EventLibraryViewCompiler(branch).export_published(
        ticker='MU', output_dir=tmp_path / 'export', version=2
    )
    assert json.loads(exports['reference_view_delta'].read_text())['from_library_version'] == 1
    assert exports['known_event_index'].is_file()


def test_missing_parent_delta_does_not_fail_unchanged_publication_export(tmp_path):
    branch = selected_branch(tmp_path, cache=False)
    with pytest.warns(RuntimeWarning, match='parent version is not retained'):
        exports = EventLibraryViewCompiler(branch).export_published(
            ticker='MU', output_dir=tmp_path / 'export', version=2
        )
    assert 'reference_view_delta' not in exports
    assert json.loads(exports['json'].read_text())
    assert exports['known_event_index'].is_file()


def test_resume_uses_frozen_view_after_review_projection_changes(tmp_path, monkeypatch):
    repository = EventLibraryRepository(tmp_path / 'events.db')
    service = EventLibraryService(repository)
    snapshot = FrozenRuntimeSnapshot.model_validate_json(
        (FIXTURE / 'runtime_snapshot.json').read_text(encoding='utf-8')
    )
    batch = service.delta_compiler.compile(snapshot)
    args = dict(run_root=tmp_path / 'run', run_id='recovery', mode='INITIALIZE',
                batches=[batch], as_of=snapshot.as_of)
    root, manifest = service.views.materialize_frozen_view(**args)
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    monkeypatch.setattr(repository, 'published_events', lambda *a: pytest.fail('mutable reread'))
    resumed, saved = service.views.materialize_frozen_view(
        **args, resume_frozen_view_id=manifest.frozen_view_id
    )
    assert resumed == root and saved == manifest
    assert before == {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    with pytest.raises(ValueError, match='frozen maintenance input'):
        service.views.materialize_frozen_view(
            **{**args, 'as_of': snapshot.as_of + timedelta(days=1)},
            resume_frozen_view_id=manifest.frozen_view_id,
        )


def test_exhausted_date_does_not_hold_next_date_or_another_ticker(tmp_path, monkeypatch):
    from doxagent.persistent_runtime_v2.journal import RuntimeJournal
    from doxagent.persistent_runtime_v2.repository import SQLitePersistentRuntimeV2Repository

    path = tmp_path / 'runtime.db'
    SQLitePersistentRuntimeV2Repository(path)
    journal = RuntimeJournal(path)
    with journal.transaction() as db:
        db.execute('CREATE TABLE IF NOT EXISTS v2_ticker_control(ticker PRIMARY KEY,payload)')
    failed = 'manual:o2-backfill:MU:2026-10-19:20261101'
    journal.put_task(failed, 'MU', 'MAINTENANCE', {'day': '2026-10-19'})
    with journal.transaction() as db:
        db.execute("UPDATE runtime_tasks SET status='FAILED' WHERE id=?", (failed,))
    journal.set('operator_o2_recovery_20261101_r2', failed, 2)
    root = tmp_path / 'backfill'
    root.mkdir()
    entry = {'case_ids': [], 'uncovered': [], 'failed_tasks': []}
    (root / 'plan.json').write_text(json.dumps({
        'exclusive_day': '2026-11-01', 'tickers': {
            'MU': {'days': {'2026-10-19': entry, '2026-10-20': entry}},
            'BE': {'days': {'2026-10-21': entry}},
        },
    }))
    monkeypatch.setenv('DOXAGENT_O2_BACKFILL_ROOT', str(root))
    monkeypatch.setattr('doxagent.settings.DoxAgentSettings', lambda: SimpleNamespace(
        persistent_runtime_v2_sqlite_path=path
    ))
    runpy.run_path(str(Path('scripts/ops/o2_provisional_backfill_20261101.py')))
    assert journal.get_task(failed)['status'] == 'FAILED'
    assert journal.get_task('manual:o2-backfill:MU:2026-10-20:20261101')['status'] == 'PENDING'
    assert journal.get_task('manual:o2-backfill:BE:2026-10-21:20261101')['status'] == 'PENDING'
    assert not (root / 'done.json').exists()
