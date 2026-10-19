import json
import os
import sqlite3
import time
from contextlib import closing

from doxagent.codex_worker.job_store import JobStore
from doxagent.codex_worker.w3_resources import (
    extend_context,
    gc_derivatives,
    inventory,
    restore,
    restore_archive,
    retire,
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
