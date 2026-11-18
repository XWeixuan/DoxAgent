import json
import os
import sqlite3
import time
from contextlib import closing
from datetime import UTC, datetime, timedelta

import pytest

from doxagent.codex_worker.job_store import JobStore
from doxagent.codex_worker.w3_resources import (
    extend_context,
    gc_derivatives,
    inventory,
    restore,
    restore_archive,
    retire,
    retire_settled_contexts,
)
from doxagent.codex_worker.workspace_store import LocalWorkspaceStore
from doxagent.persistent_runtime_v2.w3_research_store import W3ResearchStore, atomic, compact, sha


def test_legacy_cleanup_rechecks_new_owner_and_restores_exact_frozen_copy(tmp_path):
    root = tmp_path / "worker"
    JobStore(root)
    runtime = tmp_path / "runtime.sqlite3"
    with sqlite3.connect(runtime) as db:
        db.execute(
            "CREATE TABLE runtime_v2_w3_threads(kind,workspace,owner_case_id,status,expires_at)"
        )
        db.execute(
            "INSERT INTO runtime_v2_w3_threads "
            "VALUES('CASE','w3-case-old',NULL,'ACTIVE','2000-01-01')"
        )
    data = b"frozen original research"
    name = "d2.json"
    pack = root / ".w3-packs" / ("a" * 64)
    atomic(pack / "files" / name, data)
    atomic(pack / "manifest.json", compact({name: sha(data)}).encode())
    duplicate = root / "w3-case-old/context/research/pinned" / name
    atomic(duplicate, data)
    report = inventory(root, runtime)
    assert report["reclaimable_bytes"] == len(data)
    with sqlite3.connect(runtime) as db:
        db.execute("UPDATE runtime_v2_w3_threads SET owner_case_id='new-owner'")
    assert retire(report) == [] and duplicate.exists()
    with sqlite3.connect(runtime) as db:
        db.execute("UPDATE runtime_v2_w3_threads SET owner_case_id=NULL")
    assert retire(report) == [str(duplicate)]
    assert not duplicate.exists() and (pack / "files" / name).read_bytes() == data
    restore(root, "w3-case-old")
    assert duplicate.read_bytes() == data


def test_view_gc_preserves_live_context_and_recovers_expired_view_from_immutable_source(tmp_path):
    store = W3ResearchStore(tmp_path)
    old = store.install({"d1": "# C1\nold research"}, "MU")
    binding = store.bind("case1", "trade_decision", {"components": old})
    store.install({"d1": "# C1\nnew research"}, "MU")
    directory = store.packs / old["d1"]
    os.utime(directory, (time.time() - 172800, time.time() - 172800))
    assert str(directory) not in gc_derivatives(store)
    with closing(store.connect()) as db, db:
        db.execute("UPDATE w3_case_contexts SET expires_at='2000-01-01'")
        db.execute("UPDATE w3_asset_builds SET retained_until='2000-01-01',updated_at='2000-01-01' WHERE id=?", (old["d1"],))
    assert str(directory) in gc_derivatives(store, apply=True)
    assert not directory.exists()
    extend_context(store, "case1")
    page = store.call(
        "read",
        {"context_ref": binding["context_ref"], "objects": [{"kind": "d1_section", "id": "C1"}]},
    )
    assert "old research" in page["body"] and directory.exists()


def test_ready_workspace_archive_preserves_audit_without_second_research_pack(tmp_path):
    workspace = LocalWorkspaceStore(tmp_path)
    run = "w3-ready-finished"
    workspace.materialize_case(
        run, {}, {"AGENTS.md": "control", "attempts/c/audit/result.json": '{"decision":"NO_TRADE"}'}
    )
    workspace.retire_w3_ready_workspace(run, preserve_evidence=True)
    assert not (tmp_path / run).exists()
    restore_archive(tmp_path, run)
    assert (
        json.loads((tmp_path / run / "attempts/c/audit/result.json").read_bytes())["decision"]
        == "NO_TRADE"
    )


def test_settled_context_waits_for_ack_and_keeps_exact_replay(tmp_path):
    from doxagent.persistent_runtime_v2.journal import RuntimeJournal
    from doxagent.persistent_runtime_v2.repository import SQLitePersistentRuntimeV2Repository
    from doxagent.persistent_runtime_v2.w3_threads import W3ThreadRegistry

    store = W3ResearchStore(tmp_path / 'worker')
    manifest = {'components': {}, 'state': {'facts': ['exact frozen input']}}
    binding = store.bind('case', 'trade_decision', manifest)
    now = datetime.now(UTC)
    at = (now - timedelta(days=2)).isoformat()
    path = tmp_path / 'runtime.sqlite3'
    journal = RuntimeJournal(path)
    SQLitePersistentRuntimeV2Repository(path)
    W3ThreadRegistry(journal)
    with journal.transaction() as db:
        db.execute('INSERT INTO runtime_v2_cases VALUES(?,?,?,?,?,?,?,?,?)',
                   ('case', 'message', 'MU', '2026-11-16', 'COMPLETED', 'OK', '{}', at, at))
        db.executemany('INSERT INTO runtime_v2_w3_stages '
                       '(case_id,phase,status,result_json,worker_job_id,accepted_at,committed_at) '
                       'VALUES(?,?,\'COMMITTED\',?,?,?,?)', (
                           ('case', 'trade_decision', '{"decision":"NO_TRADE"}', 'job', at, at),
                           ('case', 'post_decision_operations', '{"pin_actions":[],"watch_actions":[]}', 'ops', at, at),
                       ))
        db.execute("INSERT INTO runtime_values VALUES('w3_ack_pending','job','true')")
    assert not retire_settled_contexts(store, path, now=now)
    with journal.transaction() as db:
        db.execute("DELETE FROM runtime_values WHERE namespace='w3_ack_pending'")
    assert retire_settled_contexts(store, path, now=now)
    with pytest.raises(PermissionError, match='replay extension'):
        store.context(binding['context_ref'])
    extend_context(store, 'case')
    assert store.context(binding['context_ref'])[1]['state'] == manifest['state']
    assert not retire_settled_contexts(store, path, now=now)
