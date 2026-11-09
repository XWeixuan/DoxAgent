import asyncio
import hashlib
import hmac
import json
from types import SimpleNamespace

import pytest

from doxagent.codex_worker.jobs import WorkerJobManager
from doxagent.persistent_runtime_v2.w3_research_store import W3ResearchStore, atomic, compact, sha


def manifest():
    return dict(
        case_id="case",
        phase="trade_decision",
        ticker="MU",
        components={},
        state={"watch": [], "pins": []},
        events={"E9": {"fact": "nine"}, "E10": {"fact": "ten"}},
        history=[],
        evidence={},
        cutoff_at="2026-11-09T07:00:00Z",
    )


def test_context_identity_survives_sorted_journal_roundtrip(tmp_path):
    store = W3ResearchStore(tmp_path)
    value = manifest()
    first = store.bind("case", "trade_decision", value)
    second = store.bind("case", "trade_decision", json.loads(compact(value)))
    assert first == second
    with store.connect() as db:
        assert db.execute("select count(*) from w3_case_contexts").fetchone()[0] == 1


def test_legacy_catalog_order_reuses_exact_original_manifest_and_token(tmp_path):
    store = W3ResearchStore(tmp_path)
    value = manifest()
    first = store.bind("case", "trade_decision", value)
    path = store.contexts / (first["context_manifest_sha"] + ".json")
    legacy = json.loads(path.read_bytes())
    legacy["snapshot_catalog"].reverse()
    raw = compact(legacy).encode()
    identity = sha(raw)
    token = (
        "w3ctx_"
        + hmac.new((tmp_path / ".w3-context-secret").read_bytes(), raw, hashlib.sha256).hexdigest()
    )
    atomic(store.contexts / (identity + ".json"), raw)
    with store.connect() as db, db:
        db.execute(
            "UPDATE w3_case_contexts SET manifest_sha=?,token_hash=?", (identity, sha(token))
        )
    original = (store.contexts / (identity + ".json")).read_bytes()
    binding = store.bind("case", "trade_decision", json.loads(compact(value)))
    assert binding["context_manifest_sha"] == identity and binding["context_ref"] == token
    assert (store.contexts / (identity + ".json")).read_bytes() == original
    _, frozen = store.context(token)
    assert frozen["events"] == value["events"]
    changed = json.loads(compact(value))
    changed["events"]["E9"]["fact"] = "different"
    with pytest.raises(ValueError, match="cannot change"):
        store.bind("case", "trade_decision", changed)
    with pytest.raises(ValueError, match="cannot change"):
        store.bind("case", "trade_decision", {**value, "cutoff_at": "2026-11-10T07:00:00Z"})
    with store.connect() as db:
        assert db.execute("select manifest_sha from w3_case_contexts").fetchone()[0] == identity


@pytest.mark.asyncio
async def test_identity_callback_tolerates_dispatcher_mutation(monkeypatch):
    import doxagent.codex_worker.jobs as jobs

    manager = object.__new__(WorkerJobManager)
    manager._active = {"other": "other", "wanted": "wanted", "removed": "removed"}
    request = SimpleNamespace(run_id="run", attempt_id="attempt")
    values = {
        "other": SimpleNamespace(run_id="other", attempt_id="other"),
        "wanted": SimpleNamespace(run_id="run", attempt_id="attempt"),
    }

    def get(key):
        return values.get(key)

    async def read(call, key):
        await asyncio.sleep(0)
        if key == "other":
            manager._active.pop("removed")
            manager._active["new"] = "new"
            manager._active["newer"] = "newer"
        return call(key)

    writes = []

    async def update(key, **value):
        writes.append((key, value))

    manager.store = SimpleNamespace(get=get)
    manager._update = update
    monkeypatch.setattr(jobs, "read_io", read)
    await manager._capture_identity(request, "thread", "turn")
    assert writes == [("wanted", {"thread_id": "thread", "turn_id": "turn"})]
