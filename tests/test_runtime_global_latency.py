"""Regression checks for useful READY inventory and lean persistence."""
import asyncio
from contextlib import closing
from types import SimpleNamespace
import sqlite3

import pytest

from doxagent.codex_worker.startup_admission import StartupAdmission
from doxagent.codex_worker.capsules import CapsuleRuntime
from doxagent.persistent_runtime_v2.w3_research_store import W3ResearchStore


def test_baseline_refill_ignores_speculative_cpu_hysteresis_but_not_memory_shortage():
    snapshot = SimpleNamespace(level="NORMAL", reasons=())
    admission = StartupAdmission(safety=SimpleNamespace(read=lambda: snapshot))
    admission.startup_pressure = True
    assert admission.allow_baseline()
    snapshot.level = "PRESSURE"
    snapshot.reasons = ("low_available_with_pressure",)
    assert not admission.allow_baseline()
    snapshot.level = "CRITICAL"
    assert not admission.allow_baseline()


@pytest.mark.asyncio
async def test_pressure_preserves_four_current_scope_not_stale_same_model(tmp_path):
    runtime = CapsuleRuntime(tmp_path)
    runtime.w3_ready_targets = {"current": 4}
    def cap(n, scope):
        return SimpleNamespace(identity=n,thread_id=n,ready_slot=True,ready_scope=scope,
            ready_profile="gpt-6-luna",healthy=True,busy=False,process=SimpleNamespace(returncode=None),
            handoff_until=0,idle_at=0)
    runtime.pool = [cap(str(n), "current") for n in range(4)] + [cap("old", "old")]
    async def dispose(values):
        for c in values:runtime.pool.remove(c)
    runtime._dispose_batch = dispose
    await runtime.reap_idle(pressure=True)
    assert len(runtime.pool) == 4
    assert all(c.ready_scope == "current" for c in runtime.pool)


def test_context_binding_pins_assets_and_rebinding_does_not_rewrite_manifest(tmp_path):
    store = W3ResearchStore(tmp_path)
    components = store.install({"d1": "# C1\nresearch"}, "MU")
    first = store.bind("case", "trade_decision", {"components": components})
    manifests = list(store.contexts.glob("*.json"))
    timestamps = [p.stat().st_mtime_ns for p in manifests]
    second = store.bind("case", "trade_decision", {"components": components})
    assert first == second
    assert timestamps == [p.stat().st_mtime_ns for p in manifests]
    with closing(store.connect()) as db:
        assert db.execute("SELECT retained_until FROM w3_asset_builds").fetchone()[0] > "2026"


@pytest.mark.asyncio
async def test_mcp_ready_notification_has_no_polling_and_cleans_waiter(tmp_path):
    from doxagent.mcp.w3_research_server import ResearchMcp
    store = W3ResearchStore(tmp_path)
    mcp = ResearchMcp(store, "test-secret")
    task = asyncio.create_task(mcp.wait_ready("capsule", 1))
    await asyncio.sleep(0)
    token = mcp.capsule.set("capsule")
    try:await mcp.list_tools(None, None)
    finally:mcp.capsule.reset(token)
    await task
    assert not mcp._ready_waiters


def test_prepared_daily_index_is_not_a_daily_freeze(tmp_path):
    from doxagent.persistent_runtime_v2.repository import SQLitePersistentRuntimeV2Repository
    from datetime import date
    repo = SQLitePersistentRuntimeV2Repository(tmp_path / "runtime.db")
    repo.prepared_known_index("head", {"prepared": True})
    assert repo.prepared_known_index("head") == {"prepared": True}
    assert repo.daily_known_index("MU", date(2026, 11, 18)) is None


def test_empty_bridge_completion_is_durable_and_idempotent(tmp_path):
    from doxagent.persistent_runtime_v2.repository import SQLitePersistentRuntimeV2Repository
    repo = SQLitePersistentRuntimeV2Repository(tmp_path / "runtime.db")
    assert not repo.library_bridge_applied("head")
    repo.apply_library_bridge("head", [])
    repo.apply_library_bridge("head", [])
    assert repo.library_bridge_applied("head")
    with repo._connect() as db:
        assert db.execute("SELECT count(*) FROM runtime_values WHERE namespace='w1_library_bridge'").fetchone()[0] == 1


def test_case_technical_time_does_not_copy_source_receipt_but_business_change_does(tmp_path):
    import json
    from doxagent.v2_read.outbox import SourceOutbox
    path = tmp_path / "source.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE runtime_v2_cases(case_id TEXT PRIMARY KEY,status TEXT,updated_at TEXT,payload_json TEXT)")
    SourceOutbox(path, "runtime").migrate(tables=("runtime_v2_cases",))
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO runtime_v2_cases VALUES('case','RUNNING','a',?)", (json.dumps({'updated_at':'a','result':'NEW'}),))
        db.execute("UPDATE runtime_v2_cases SET updated_at='b',payload_json=json_set(payload_json,'$.updated_at','b')")
        assert db.execute('SELECT count(*) FROM v2_source_receipts').fetchone()[0] == 1
        db.execute("UPDATE runtime_v2_cases SET payload_json=json_set(payload_json,'$.result','OLD')")
        assert db.execute('SELECT count(*) FROM v2_source_receipts').fetchone()[0] == 2


def test_gc_rechecks_protection_acquired_during_hash_without_holding_writer(tmp_path, monkeypatch):
    import doxagent.codex_worker.w3_resources as module
    import doxagent.codex_runtime.published_storage as published_storage
    store = W3ResearchStore(tmp_path)
    old = store.install({'d1': '# C1\nold'}, 'MU')
    store.install({'d1': '# C1\nnew'}, 'MU')
    with closing(store.connect()) as db, db:
        db.execute("UPDATE w3_asset_builds SET updated_at='2000-01-01',retained_until='2000-01-01' WHERE id=?", (old['d1'],))
    original = published_storage.read_local_body
    def protect_during_hash(*args, **kwargs):
        store.protect_components(old, '2099-01-01')
        return original(*args, **kwargs)
    monkeypatch.setattr(published_storage, 'read_local_body', protect_during_hash)
    module.gc_derivatives(store, apply=True)
    assert (store.packs / old['d1']).exists()
    with closing(store.connect()) as db:
        assert db.execute('SELECT state FROM w3_asset_builds WHERE id=?', (old['d1'],)).fetchone()[0] == 'READY'


def test_bridge_visibility_does_not_read_content_first_admission_history(tmp_path, monkeypatch):
    from datetime import timedelta
    from tests.test_w1_daily_index import library, event, publish, DAY
    from doxagent.semantic_clock import boundary
    from doxagent.persistent_runtime_v2.repository import SQLitePersistentRuntimeV2Repository
    from doxagent.persistent_runtime_v2.daily_index import freeze_index, retain_new_library_facts
    from doxagent.event_library.repository import EventLibraryRepository
    root, lib, provider = library(tmp_path)
    publish(lib, 1, boundary(DAY - timedelta(days=40)), [event(1)])
    repo = SQLitePersistentRuntimeV2Repository(tmp_path / 'runtime.db')
    frozen, frozen_root, _ = freeze_index(repo, provider, provider.current_index('MU'), 'MU', DAY, str(root), boundary(DAY), 'instructions')
    publish(lib, 2, boundary(DAY), [event(2)])
    def no_history(*args, **kwargs):raise AssertionError('bridge must not trace admissions')
    monkeypatch.setattr(EventLibraryRepository, 'fact_admissions', no_history)
    args = dict(frozen_index=frozen, frozen_root=frozen_root, current_index=provider.current_index('MU'), current_root=str(root))
    retain_new_library_facts(repo, provider, 'MU', DAY, **args)
    assert len(repo.list_provisional('MU', DAY)) == 1
    # Durable receipt prevents re-reading the current Library after a process restart.
    monkeypatch.setattr(provider, 'published_facts', no_history)
    retain_new_library_facts(repo, provider, 'MU', DAY, **args)


def test_completion_dispatches_waiting_case_without_recovery_tick_or_capacity_change(tmp_path):
    from threading import Event
    from doxagent.persistent_runtime_v2.coordinator import RuntimeCoordinator
    from doxagent.persistent_runtime_v2.journal import RuntimeJournal
    journal = RuntimeJournal(tmp_path / 'runtime.db')
    coordinator = RuntimeCoordinator(SimpleNamespace(), journal)
    release, full, successor = Event(), Event(), Event()
    calls = []
    def run(task):
        calls.append(task['id'])
        if len(calls) == journal.CASE_CONCURRENCY_PER_TICKER:
            full.set()
        if task['id'] == 'waiting':
            successor.set()
        else:
            release.wait(5)
        journal.finish(task)
    coordinator._execute = run
    for n in range(journal.CASE_CONCURRENCY_PER_TICKER):
        journal.put_task(f'active-{n}', 'MU', 'CASE', {'mode':'REALTIME'})
    journal.put_task('waiting', 'MU', 'CASE', {'mode':'REALTIME'})
    try:
        coordinator.dispatch_realtime('MU')
        assert full.wait(3)
        assert not successor.is_set()
        assert journal.get_task('waiting')['status'] == 'PENDING'
        release.set()
        assert successor.wait(3)
        assert calls.count('waiting') == 1
    finally:
        release.set()
        coordinator.close()
