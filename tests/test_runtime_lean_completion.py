"""Regressions for material handoff, component identities and bounded maintenance."""
import asyncio
import json
import time
from concurrent.futures import Future
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from doxagent.persistent_runtime_v2.journal import RuntimeJournal
from doxagent.persistent_runtime_v2.w3 import CodexW3AgentRunner, PublishedW3ContextProvider, W3Error
from doxagent.persistent_runtime_v2.w3_contracts import W3_V6_CONTRACT


def test_material_completion_resumes_original_effect_without_recovery_tick(tmp_path, monkeypatch):
    from tests.test_w3_v4 import runtime, enqueue

    service, journal, agent, _ = runtime(tmp_path, "NO_TRADE")
    case = enqueue(service)
    pending = Future()
    original = agent.prepare_case
    calls = []

    def prepare(value):
        calls.append(value.case_id)
        if not pending.done():
            error = W3Error("w3_prepare_pending", "original material")
            error.pending_future = pending
            raise error
        return original(value)

    monkeypatch.setattr(agent, "prepare_case", prepare)
    try:
        assert service.dispatch_pending_effects() == 1
        deadline = time.monotonic() + 5
        while not service._phase_waiters and time.monotonic() < deadline:
            time.sleep(.005)
        assert service._phase_waiters
        pending.set_result(None)
        deadline = time.monotonic() + 3
        while service._active_effect_ids and time.monotonic() < deadline:
            time.sleep(.005)
        assert len(calls) == 2
        effect = next(e for e in service.repository.list_effects(case.case_id) if e.effect_type == "route_to_w3")
        assert effect.status == "COMPLETED" and not service._phase_waiters
    finally:
        pending.cancel()
        service.close()


def test_material_rpc_failure_preserves_business_attempt_budget(tmp_path, monkeypatch):
    from tests.test_w3_v4 import runtime, enqueue

    service, journal, agent, _ = runtime(tmp_path, "NO_TRADE")
    case = enqueue(service)
    def unavailable(value):
        raise httpx.ConnectError("worker restarting")
    monkeypatch.setattr(agent, "prepare_case", unavailable)
    try:
        service.process_pending_effects()
        effect = next(e for e in service.repository.list_effects(case.case_id) if e.effect_type == "route_to_w3")
        assert effect.status == "PENDING_RETRY" and effect.attempt_count == 0
        assert service.repository.get_case(case.case_id).status != "FAILED"
    finally:
        service.close()


def test_v6_reuses_components_without_borrowing_other_case_version_pin(tmp_path):
    from tests.test_persistent_runtime_w3 import _PublishedContextRuntime, _runtime_case

    runtime = _PublishedContextRuntime()
    forbidden = SimpleNamespace(get_version=lambda *a: pytest.fail("v6 Policy"),
                                reference_view=lambda *a, **k: pytest.fail("v6 Reference"))
    provider = PublishedW3ContextProvider(runtime_repository=runtime,
        policy_repository=forbidden, event_library_reader=forbidden)
    first = _runtime_case("one")
    first.w3_contract_version = W3_V6_CONTRACT
    second = first.model_copy(deep=True, update={"case_id": "two"})
    second.version_pin = first.version_pin.model_copy(update={
        "policy_set_version": 90, "event_library_version": 99,
    })
    a = asyncio.run(provider.load(first))
    b = asyncio.run(provider.load(second))
    assert runtime.document_reads == 2
    assert a.research_identity == b.research_identity
    assert b.version_pin.event_library_version == 99
    assert a.version_pin.event_library_version != 99
    runner = object.__new__(CodexW3AgentRunner)
    runner._journal = RuntimeJournal(tmp_path / "r.db")
    runner._component_installations = {}
    runner._wait_v4 = asyncio.run
    runner._worker = SimpleNamespace(prepare_w3_research=AsyncMock(return_value={"d1": "a", "d2": "b"}))
    installed_a = runner._install_context(first, a)
    installed_b = runner._install_context(second, b)
    assert runner._worker.prepare_w3_research.await_count == 1
    assert installed_a["components"] == installed_b["components"]
    assert installed_b["version_pin"]["policy_set_version"] == 90


def test_inventory_does_not_reverify_registered_single_file(tmp_path, monkeypatch):
    from tests.v2_backend.test_source_lifecycle import fixture_source
    from doxagent.v2_read import source_native_gc as gc
    from doxagent.v2_read.content_files import ContentFiles

    source = fixture_source(tmp_path)
    monkeypatch.setenv("DOXAGENT_CONTENT_SINGLE_FILE", "1")
    for n in range(8):
        source.content.files.put(json.dumps({"n": n, "text": "abc" * 6000}).encode())
    while not gc.reconcile(source, seconds=2)["ready"]:
        pass
    original = ContentFiles.verify
    checks = []
    def verify(files, identity):
        checks.append(identity)
        return original(files, identity)
    monkeypatch.setattr(ContentFiles, "verify", verify)
    assert gc.inventory(source) == 8
    for _ in range(3):
        assert gc.inventory(source) == 0
    assert len(checks) == 8


def test_published_metadata_skips_body_and_full_reader_keeps_missing_body_error(tmp_path, monkeypatch):
    from doxagent.codex_runtime.repository import SQLiteCodexRuntimeRepository
    from doxagent.codex_runtime.published_storage import put_local_body, LOCAL_BODY_CODEC
    from tests.test_persistent_runtime_w3 import _PublishedContextRuntime

    repository = SQLiteCodexRuntimeRepository(tmp_path / "research.db")
    document = _PublishedContextRuntime().documents[("d1-mu", "d1-doc")]
    payload = document.model_dump(mode="json", exclude={"content_text"})
    payload["local_content_codec"] = LOCAL_BODY_CODEC
    with repository._connect() as db:
        db.execute("INSERT INTO codex_runtime_records(record_type,record_key,run_id,payload_json) "
                   "VALUES('published_documents',?,?,?)",
                   (document.artifact_id, document.run_id, json.dumps(payload)))
    metadata = repository.get_published_document_metadata(document.run_id, document.artifact_id)
    assert metadata.content_text is None and metadata.sha256 == document.sha256
    with pytest.raises(ValueError, match="REMOTE_CONTENT_BACKFILL_REQUIRED"):
        repository.get_published_document(document.run_id, document.artifact_id)
    put_local_body(tmp_path, document.sha256, document.content_text.encode(), compressed=True)
    assert repository.get_published_document(document.run_id, document.artifact_id) == document


def test_native_inventory_bounds_checked_entries_and_continues_same_prefix(tmp_path, monkeypatch):
    import os
    from tests.v2_backend.test_source_lifecycle import fixture_source
    from doxagent.v2_read import source_native_gc as gc

    source = fixture_source(tmp_path)
    while not gc.reconcile(source, seconds=2)["ready"]:
        pass
    prefix = source.path.parent / "native-files" / "00"
    prefix.mkdir(parents=True)
    for n in range(40):
        (prefix / f"invalid-{n}").touch()
    scanned = []
    original = os.scandir
    def scandir(path):
        with original(path) as items:
            for item in items:
                scanned.append(item.name)
                yield item
    monkeypatch.setattr(os, "scandir", scandir)
    assert gc.inventory(source, limit=4) == 0
    assert len(scanned) == 4
    assert gc.inventory(source, limit=4) == 0
    assert len(scanned) == 8 and len(set(scanned)) == 8


def test_native_schema_cache_invalidates_on_ddl(tmp_path, monkeypatch):
    from contextlib import closing
    from tests.v2_backend.test_source_lifecycle import fixture_source
    from doxagent.v2_read import source_native_gc as gc

    source = fixture_source(tmp_path)
    while not gc.reconcile(source, seconds=2)["ready"]:
        pass
    gc.reconcile(source)
    queries = []
    original = source.connection
    def connection(*args, **kwargs):
        db = original(*args, **kwargs)
        db.set_trace_callback(queries.append)
        return db
    monkeypatch.setattr(source, "connection", connection)
    gc.reconcile(source)
    assert not any("PRAGMA table_info" in sql for sql in queries)
    with closing(source.connection(write=True)) as db, db:
        db.execute("CREATE TABLE extra_owner(id TEXT PRIMARY KEY,payload TEXT)")
    gc.reconcile(source)
    assert any("PRAGMA table_info" in sql for sql in queries)


@pytest.mark.asyncio
@pytest.mark.parametrize("trade_model", ["gpt-6-luna", "gpt-6.2-sol"])
async def test_v6_turn_tiers_use_same_resident_and_explicit_fast_default(tmp_path, monkeypatch, trade_model):
    from datetime import UTC, datetime
    from doxagent.codex_worker.sdk_runtime import OpenAICodexRuntime
    from doxagent.codex_worker.schema import WorkerRunRequest
    from doxagent.settings import DoxAgentSettings
    from tests.test_codex_runtime_v2 import _AsyncSdkClient

    sdk = _AsyncSdkClient()
    monkeypatch.setattr("doxagent.codex_worker.sdk_runtime.AsyncCodex", lambda *a, **k: sdk)
    runtime = OpenAICodexRuntime(capability_secret="s" * 32, container_isolated=True,
                                settings=DoxAgentSettings(ibkr_tws_enabled=False))
    request = WorkerRunRequest(
        workflow_version="persistent_runtime_w3_v1", research_lane="persistent_runtime",
        run_id="case", ticker="MU", node="persistent_runtime_w3",
        agent_role="w3_duty_trading_expert", attempt_id="trade",
        cutoff_at=datetime.now(UTC), prompt="test", output_schema={"type": "object"},
        task_phase="trade_decision", w3_entry_version="w3-entry-v6",
        research_assets_mcp_enabled=True,
        data_mcp_enabled=False, read_only=True, allow_subagents=False, max_subagents=0,
        model="gpt-6-luna", effort="max",
    )
    await runtime.start(request, tmp_path)
    assert sdk.thread.turn_kwargs["turn_service_tier"] == "fast"
    await runtime.start(request.model_copy(update={
        "attempt_id":"sol-trade", "thread_id":sdk.thread.id,
        "model":trade_model, "effort":"high" if trade_model == "gpt-6.2-sol" else "max",
    }), tmp_path)
    assert sdk.thread.turn_kwargs["model"] == trade_model
    assert sdk.thread.turn_kwargs["effort"].value == ("high" if trade_model == "gpt-6.2-sol" else "max")
    assert sdk.thread.turn_kwargs["turn_service_tier"] == "fast"
    assert sdk.thread_resume_kwargs is None
    await runtime.start(request.model_copy(update={
        "attempt_id": "ops", "thread_id": sdk.thread.id,
        "task_phase": "post_decision_operations",
    }), tmp_path)
    assert sdk.thread.turn_kwargs["turn_service_tier"] == "default"
    assert sdk.thread_resume_kwargs is None

