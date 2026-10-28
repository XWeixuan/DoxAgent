import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


async def test_failed_physical_close_keeps_lease_and_capacity():
    from doxagent.site_strategy.browser_runtime import BrowserRuntimeManager
    from doxagent.site_strategy.schema import BrowserIdentitySpec, ProxyEgress

    page = SimpleNamespace()
    inner = SimpleNamespace(
        page=page,
        browser_cdp=None,
        provenance=None,
        __aexit__=AsyncMock(side_effect=[RuntimeError("close lost"), None]),
    )
    adapter = SimpleNamespace(page=AsyncMock(return_value=inner))
    manager = BrowserRuntimeManager(adapter, None, max_pages=1)
    identity = BrowserIdentitySpec(identity_id="test", profile_id="test", egress_id="test")
    egress = ProxyEgress(
        egress_id="test",
        endpoint="http://127.0.0.1:1080",
        node_ref="test",
        node_fingerprint="test",
        listener_port=1080,
    )
    lease = await manager.page(identity, egress)
    with pytest.raises(RuntimeError, match="close lost"):
        await lease.__aexit__(None, None, None)
    assert manager._page_slots._value == 0
    assert lease in manager._pending_closes
    await lease.__aexit__(None, None, None)
    assert manager._page_slots._value == 1
    assert not manager._pending_closes


async def test_public_burst_shares_execution_and_exits_after_last_task(monkeypatch):
    import doxagent.site_strategy.public_runtime as module
    from doxagent.site_strategy.schema import AccessRequest

    entered, release = asyncio.Event(), asyncio.Event()
    runtimes = []

    class Runtime:
        def __init__(self, *args, **kwargs):
            self.browser_pool = object()
            self.closed = False
            runtimes.append(self)

        async def _browser(self, request, *args):
            assert request.recipe_parameters["_public_ephemeral"]
            entered.set()
            await release.wait()
            return request.url

        async def close(self):
            self.closed = True

    monkeypatch.setattr(module, "SiteAccessRuntime", Runtime)
    backend = module.PublicExecution(None, None, {"profile_root": "/unused"})
    request = AccessRequest(
        operation_id="a", url="https://example.com/a", purpose="BODY", mode="BROWSER"
    )
    tasks = [asyncio.create_task(backend.execute(request, None, None, None)) for _ in range(2)]
    await entered.wait()
    await asyncio.sleep(0)
    assert len(runtimes) == 1 and backend.users == 2
    assert not runtimes[0].closed
    release.set()
    await asyncio.gather(*tasks)
    assert runtimes[0].closed and backend.runtime is None


async def test_public_cleanup_error_does_not_lose_successful_download(monkeypatch):
    import doxagent.site_strategy.public_runtime as module
    from doxagent.site_strategy.schema import AccessRequest

    class Runtime:
        def __init__(self, *args, **kwargs):
            self.browser_pool = object()

        async def _browser(self, *args):
            return "downloaded full article"

    monkeypatch.setattr(module, "SiteAccessRuntime", Runtime)
    backend = module.PublicExecution(None, None, {"profile_root": "/unused"})
    backend._close_runtime = AsyncMock(side_effect=RuntimeError("local ledger busy"))
    value = await backend.execute(
        AccessRequest(
            operation_id="test", url="https://example.test/", purpose="BODY", mode="BROWSER"
        ),
        None,
        None,
        None,
    )
    assert value == "downloaded full article"
    assert backend.cleanup_pending and backend.runtime is not None


def test_provider_retry_floor_is_not_capped():
    from doxagent.content_enrichment.service import ContentEnrichmentHub

    service = object.__new__(ContentEnrichmentHub)
    service.retry_delay_seconds = 5
    assert service._retry_after([SimpleNamespace(retry_after_seconds=600)]) == 600
    assert service._retry_after([]) == 5


def test_process_snapshot_is_shared_between_owners(monkeypatch):
    import doxagent.codex_worker.process_tree as tree

    calls = []
    monkeypatch.setattr(tree, "_sampled_at", 0)
    monkeypatch.setattr(tree.time, "monotonic", lambda: 20)
    monkeypatch.setattr(
        tree,
        "_scan_owners",
        lambda: calls.append(1) or {"a": {1: ("10", 20)}, "b": {2: ("11", 30)}},
    )
    assert tree.owned_processes("a") == {1: ("10", 20)}
    assert tree.owned_processes("b") == {2: ("11", 30)}
    assert calls == [1]


async def test_recover_owners_uses_one_scan_and_retires_records(tmp_path, monkeypatch):
    import doxagent.codex_worker.process_tree as tree
    from doxagent.codex_worker.capsules import CapsuleRuntime

    calls = []
    monkeypatch.setattr(tree, "process_owners", lambda **kwargs: calls.append(1) or {})
    for i in range(20):
        p = tmp_path / "capsules" / str(i) / "owner.json"
        p.parent.mkdir(parents=True)
        p.write_text(json.dumps({"identity": str(i)}))
    await CapsuleRuntime(tmp_path).recover()
    assert calls == [1]
    assert not list((tmp_path / "capsules").glob("*/owner.json"))
    assert len(list((tmp_path / "capsules").glob("*/owner.closed"))) == 20


def test_body_search_is_built_once_per_digest(tmp_path, monkeypatch):
    from doxagent.v2_read.repository import ReadStore

    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    reference = store.put_content("MU", "STRASSE 中文全文 " * 200)
    marker = store.search_content("MU", reference)
    monkeypatch.setattr(store, "content", lambda *args: pytest.fail("unchanged body read again"))
    assert store.search_content("MU", reference) == marker
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM content_search").fetchone()[0] == 1
        assert "strasse" in db.execute("SELECT text FROM content_search").fetchone()[0]


def test_search_digest_preserves_history_unicode_substring_and_ticker_scope(tmp_path):
    from doxagent.v2_read.repository import ReadStore

    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    ref = store.put_content("MU", "Straße 中文正文")
    marker = store.search_content("MU", ref)

    def record(title, search):
        return dict(
            kind="message",
            ticker="MU",
            id="a",
            sort="2026-10-29",
            search=search,
            data={"title": {"value": title}, "body_ref": ref},
        )

    old = store.ingest("test", "1", [record("Old title", marker)])
    new = store.ingest("test", "2", [record("New title", marker)])
    for seq in (old, new):
        assert len(store.page("message", "MU", seq, q="STRASSE")) == 1
        assert len(store.page("message", "MU", seq, q="中文")) == 1
        assert store.page("message", "INTC", seq, q="中文") == []
    assert store.page("message", "MU", old, q="Old title")
    assert not store.page("message", "MU", new, q="Old title")
    latest = store.ingest(
        "test",
        "3",
        [dict(kind="message", ticker="MU", id="legacy", search="legacy inline text", data={})],
    )
    assert store.page("message", "MU", latest, q="inline")


async def test_startup_migrates_valid_outage_work_and_retires_only_old_rechecks(tmp_path):
    from datetime import timedelta

    from doxagent.content_enrichment.schema import EnrichmentJob
    from doxagent.message_bus_v2.admission import AdmissionContext
    from doxagent.message_bus_v2.lean_startup import StartupCleanup
    from doxagent.message_bus_v2.schema import utc_now
    from tests.test_content_enrichment_hub import _message, _setup

    repo, bus, source, binding = _setup(tmp_path)
    captured = utc_now() - timedelta(hours=2)
    for key, kind in [
        ("valid", "new_article"),
        ("old-check", "recheck"),
        ("expired", "new_article"),
    ]:
        message = _message(key).model_copy(
            update={
                "published_at": captured,
                "admission_context": AdmissionContext(observed_at=captured)
                if key != "expired"
                else None,
            }
        )
        repo.enqueue_enrichment_job(
            EnrichmentJob(
                job_id=key,
                intake_key=key,
                poll_run_id="test",
                source=source,
                binding=binding,
                message=message,
                created_at=captured,
                not_before=utc_now() + timedelta(minutes=5),
                deadline_at=captured + timedelta(seconds=180),
                work_kind=kind,
            )
        )
    cleanup = StartupCleanup(
        SimpleNamespace(
            message_bus_v2_sqlite_path=repo.path,
            persistent_runtime_v2_sqlite_path=tmp_path / "missing.db",
        ),
        tmp_path / "receipt.jsonl",
        apply=True,
        reason="test",
    )
    result = await cleanup.run()
    assert result["migrate_valid_continuation"] == 1
    assert result["retire_legacy_recheck"] == result["retire_expired_legacy"] == 1
    with repo._connect() as db:
        row = db.execute("SELECT first_seen_at,due_at FROM live_articles").fetchone()
        assert row[0] == captured.isoformat() and row[1] > utc_now().isoformat()
        assert not db.execute(
            "SELECT 1 FROM content_enrichment_jobs WHERE status IN ('queued','running','retry_wait')"
        ).fetchone()
        assert db.execute(
            "SELECT 1 FROM live_migrations WHERE name='legacy-body-complete'"
        ).fetchone()


def test_live_immutable_load_reuses_files_but_does_not_share_mutable_metadata(
    tmp_path, monkeypatch
):
    from doxagent.message_bus_v2.live_repository import LiveRepository
    from tests.test_content_enrichment_hub import _setup

    repo, *_ = _setup(tmp_path)
    live = LiveRepository(repo)
    ref, _ = live.put({"body": "Body " * 10000, "metadata": {"ticker": "MU"}})
    first = live.load(ref)
    first["metadata"]["ticker"] = "INTC"
    monkeypatch.setattr(
        repo.content, "decode", lambda *_: pytest.fail("unchanged result decoded again")
    )
    assert live.load(ref)["metadata"]["ticker"] == "MU"


def test_site_startup_migration_is_versioned_idempotent_and_preserves_source_identity(tmp_path):
    import importlib.util
    from pathlib import Path

    from doxagent.site_strategy.governance_models import BrowserRecovery, SourceAccessScope
    from doxagent.site_strategy.governance_repository import GovernanceRepository
    from doxagent.site_strategy.repository import SiteStrategyRepository
    from doxagent.site_strategy.schema import BrowserIdentitySpec, BrowserProfile, ProxyEgress

    spec = importlib.util.spec_from_file_location(
        "lean_site_startup", Path(__file__).parents[1] / "scripts/ops/lean_site_startup.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    repo = SiteStrategyRepository(tmp_path / "site.db")
    repo.upsert_egress(
        ProxyEgress(
            egress_id="egress",
            node_ref="x",
            node_fingerprint="x",
            endpoint="http://127.0.0.1:9",
            listener_port=9,
        )
    )
    governance = GovernanceRepository(repo)
    for key, scope_id, site in [
        ("public", "publisher:example.test", "generic"),
        ("source", "source:reuters", "reuters"),
    ]:
        repo.save_profile(
            BrowserProfile(
                profile_id=key, site_id=site, bound_egress_id="egress", directory_key=key
            )
        )
        repo.apply_identity(
            BrowserIdentitySpec(
                identity_id=key, profile_id=key, egress_id="egress", owner_scope_id=scope_id
            ),
            expected_revision=None,
            actor="test",
        )
        governance.apply_scope(
            SourceAccessScope(
                scope_id=scope_id,
                source_id="reuters" if key == "source" else None,
                owner_site_id=site,
                primary_identity_id=key,
            ),
            None,
        )
        governance.save_recovery(
            BrowserRecovery(identity_id=key, scope_id=scope_id, site_id=site),
            expected_generation=None,
        )
    assert module.migrate(repo.path, tmp_path / "dry.jsonl")["public_context"] == 1
    assert repo.get_identity("public").revision == 1
    module.migrate(repo.path, tmp_path / "applied.jsonl", apply=True)
    assert repo.get_identity("public").execution_mode == "public_context"
    assert repo.get_identity("source").revision == 1
    assert governance.get_recovery("public").automation_active is False
    assert governance.get_recovery("source").automation_active is True
    assert (
        module.migrate(repo.path, tmp_path / "twice.jsonl", apply=True).get("public_context", 0)
        == 0
    )
    assert repo.get_identity("public").revision == 2
    repo.close()


async def test_startup_accepts_persisted_delta_once_without_fetching(tmp_path):
    from doxagent.message_bus_v2.lean_startup import StartupCleanup
    from tests.test_message_collection_plane import setup_plane

    repo, _, collector, adapter, store = setup_plane(tmp_path)
    await collector.refresh(seeds=True)
    await collector.acquire(*next(iter(collector.desired.values())))
    assert len(store.pending()) == 1
    cleanup = StartupCleanup(
        SimpleNamespace(
            message_bus_v2_sqlite_path=repo.path,
            persistent_runtime_v2_sqlite_path=tmp_path / "missing.db",
        ),
        tmp_path / "cleanup.jsonl",
        apply=True,
        reason="test",
    )
    result = await cleanup.run()
    assert result["accept_valid_persisted_delta"] == 1 and adapter.calls == 1
    assert not store.pending()
    with repo._connect() as db:
        assert db.execute("SELECT count(*) FROM live_articles").fetchone()[0] == 1
    await cleanup.run()
    with repo._connect() as db:
        assert db.execute("SELECT count(*) FROM live_articles").fetchone()[0] == 1
