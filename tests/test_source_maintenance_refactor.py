"""Lifecycle, storage and UI regressions; no publisher navigation or credentials."""

import importlib.util
import json
import queue
import tarfile
from datetime import timedelta
from pathlib import Path

import pytest

from doxagent.site_strategy.governance_models import MaintenanceSession, OwnedBrowserTarget
from doxagent.site_strategy.governance_repository import GovernanceRepository
from doxagent.site_strategy.isolation_migration import (
    apply_migration,
    import_legacy_state,
    migration_plan,
)
from doxagent.site_strategy.profile_storage import ProfileSnapshotManager
from doxagent.site_strategy.repository import SiteStrategyRepository
from doxagent.site_strategy.retention import RetentionWorker
from doxagent.site_strategy.schema import AccessEvent, BrowserProfile, utc_now
from doxagent.site_strategy.seeds import bootstrap_seed
from doxagent.site_strategy.service import SiteStrategyService
from doxagent.site_strategy.targets import TargetOwner


@pytest.fixture
def seeded(tmp_path):
    repository = SiteStrategyRepository(tmp_path / "registry.sqlite3")
    service = SiteStrategyService(repository, profile_root=tmp_path / "profiles")
    bootstrap_seed(repository, service)
    yield repository, service
    repository.close()


def test_partial_large_cache_scan_can_reclaim_without_touching_cookie(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from doxagent.site_strategy import resource_gc

    cache = tmp_path / "identity" / "Default" / "Cache"
    cache.mkdir(parents=True)
    cached = cache / "regenerable"
    cached.write_bytes(b"fixture")
    cookie = cache.parent / "Cookies"
    cookie.write_bytes(b"keep")

    class Lock:
        def __init__(self, *args):
            pass

        def acquire(self):
            pass

        def release(self):
            pass

    monkeypatch.setattr(resource_gc, "OwnerFileLock", Lock)
    monkeypatch.setattr("doxagent.site_strategy.runtime.profile_browser_processes", lambda _: [])
    monkeypatch.setattr(
        resource_gc,
        "_files",
        lambda *a, **k: ([(cached, SimpleNamespace(st_size=600 * 1024**2, st_mtime=0))], False),
    )
    result = resource_gc.maintain_profile(tmp_path, "identity", allow_cache=True)
    assert result["deleted_bytes"] == 600 * 1024**2
    assert not cached.exists() and cookie.read_bytes() == b"keep"


def test_snapshot_batch_reuses_registry_excludes_cache_and_retains_two(seeded, tmp_path):
    repository, _ = seeded
    root = tmp_path / "profiles"
    manager = ProfileSnapshotManager(root, repository)
    profiles = []
    for name in ("a", "b"):
        profile = BrowserProfile(
            profile_id=name, site_id="barrons", directory_key=name, bound_egress_id="us-standard-5"
        )
        directory = root / name / "Default"
        directory.mkdir(parents=True)
        (directory / "Cookies").write_bytes(b"independent-cookie-" + name.encode())
        (directory / "Cache").mkdir()
        (directory / "Cache" / "large").write_text("regenerable")
        profiles.append(profile)
    first = manager.create(profiles[0], browser_version="153", batch_id="upgrade-batch")
    manager.create(profiles[1], browser_version="153", batch_id="upgrade-batch")
    assert len(list(manager.snapshot_root.glob("registry-*.sqlite3"))) == 1
    with tarfile.open(first["archive"]) as bundle:
        assert not any("/Cache/" in name for name in bundle.getnames())
        assert bundle.extractfile("profile/Default/Cookies").read() == b"independent-cookie-a"
        metadata = json.load(bundle.extractfile("snapshot.json"))
        assert metadata["registry_backup"] == "registry-upgrade-batch.sqlite3"
    for _ in range(3):
        manager.create(profiles[0], browser_version="153", batch_id="upgrade-batch")
    assert len(list(manager.snapshot_root.glob("a-*.tar.gz"))) == 2
    assert (root / "b/Default/Cookies").read_bytes() == b"independent-cookie-b"


def test_failed_snapshot_never_leaves_a_recoverable_half_archive(seeded, tmp_path, monkeypatch):
    repository, _ = seeded
    root = tmp_path / "profiles"
    (root / "a").mkdir(parents=True)
    profile = BrowserProfile(
        profile_id="a", site_id="barrons", directory_key="a", bound_egress_id="us-standard-5"
    )
    manager = ProfileSnapshotManager(root, repository)

    def fail(*_args, **_kwargs):
        raise OSError("simulated full disk")

    monkeypatch.setattr(tarfile.TarFile, "add", fail)
    with pytest.raises(OSError):
        manager.create(profile, browser_version="153", batch_id="failed")
    assert not list(manager.snapshot_root.glob("*.tar.gz"))
    assert not list(manager.snapshot_root.glob("*.tar.json"))


def test_retention_normalizes_offsets_summarizes_once_and_exposes_backlog(seeded):
    repository, _ = seeded
    now = utc_now()
    for number in range(5):
        repository.append_event(
            AccessEvent(
                operation_id=f"old-{number}",
                site_id="barrons",
                category="ACCESS_RESULT",
                occurred_at=now - timedelta(days=8),
                payload={"disposition": "SUCCESS", "identity_id": "main"},
            )
        )
    repository.append_event(
        AccessEvent(
            operation_id="recent",
            site_id="barrons",
            category="ACCESS_RESULT",
            occurred_at=now,
            payload={"disposition": "SUCCESS"},
        )
    )
    retention = RetentionWorker(repository, batch_size=2)
    assert retention.run_once(now)["deleted"]["success_events"] == 2
    retention.run_once(now)
    retention.run_once(now)
    again = retention.run_once(now)
    assert again["deleted"]["success_events"] == 0
    assert again["physical_space_reclaimed"] is False
    assert "backlog" in again
    assert (
        repository._connection.execute("select sum(count) from access_hourly_summaries").fetchone()[
            0
        ]
        == 5
    )
    assert (
        repository._connection.execute(
            "select count(*) from access_events where operation_id='recent'"
        ).fetchone()[0]
        == 1
    )


@pytest.mark.asyncio
async def test_cross_connection_cleanup_is_exact_and_close_failure_is_durable(seeded):
    repository, _ = seeded
    store = GovernanceRepository(repository)
    owner = TargetOwner(repository)
    orphan = store.save_target(
        OwnedBrowserTarget(identity_id="barrons-1", browser_instance_id="chrome", worker_epoch=1)
    )
    human = store.save_target(
        OwnedBrowserTarget(
            identity_id="barrons-1",
            browser_instance_id="chrome",
            worker_epoch=1,
            target_id="human",
            owner_kind="CHALLENGE",
            state="LIVE",
        )
    )
    calls = []

    class CDP:
        failed = True

        async def send(self, method, parameters=None):
            if method == "Target.getTargets":
                return {
                    "targetInfos": [
                        {"targetId": "business", "url": orphan.marker},
                        {"targetId": "human", "url": "https://example.com/"},
                        {"targetId": "unknown", "url": "about:blank"},
                    ]
                }
            calls.append(parameters["targetId"])
            if self.failed:
                raise OSError("connection lost during close")
            return {"success": True}

    cdp = CDP()
    await owner.reconcile(cdp, "barrons-1", "chrome")
    assert store.get_target(orphan.allocation_id).state == "CLOSE_PENDING"
    cdp.failed = False
    await owner.reconcile(cdp, "barrons-1", "chrome")
    assert store.get_target(orphan.allocation_id).state == "CLOSED"
    assert store.get_target(human.allocation_id).state == "LIVE"
    assert calls == ["business", "business"]


def test_legacy_challenge_import_preserves_instance_target_and_profile_login(seeded):
    repository, _ = seeded
    store = GovernanceRepository(repository)
    profile = repository.get_profile("barrons-1")
    repository.save_profile(
        profile.model_copy(
            update={
                "challenge_url": "https://www.barrons.com/articles/real?secret=not-displayed",
                "challenge_site_id": "barrons",
                "challenge_identity_id": "barrons-1",
                "challenge_target_id": "original",
                "maintenance_session_id": "a" * 32,
            }
        )
    )
    scope = store.maintenance_scope("publisher:barrons")
    import_legacy_state(
        repository,
        scope,
        {"barrons-1": {"instance_id": "native-instance", "targets": ["original"]}},
    )
    task = store.current_challenge("barrons-1")
    assert task.browser_instance_id == "native-instance"
    assert task.original_target_id == "original"
    assert task.state == "WAITING_HUMAN"
    assert "secret" not in task.original_url
    assert store.list_sessions()[0].state == "RELEASED"
    assert repository.get_profile("barrons-1").auth_state == profile.auth_state
    import_legacy_state(repository, scope)
    assert len(store.list_challenges()) == 1


def _load_desktop(name):
    path = Path(__file__).parents[1] / "deploy" / name
    spec = importlib.util.spec_from_file_location("maintenance_test_module", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_maintenance_inspection_waits_for_navigation_without_reload(seeded):
    from types import SimpleNamespace

    repository, service = seeded

    class Page:
        calls = 0

        async def content(self):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError(
                    "Unable to retrieve content because the page is navigating and changing the content."
                )
            return "<article>stable original document</article>"

    page = Page()
    assert "stable original" in await service.runtime._maintenance_document(
        SimpleNamespace(page=page)
    )
    assert page.calls == 2


def test_dedicated_body_publisher_inherits_profile_instead_of_losing_cookies(seeded, tmp_path):
    from doxagent.site_strategy.scope import ScopeResolver

    repository, service = seeded
    plan = migration_plan(repository)
    apply_migration(
        repository,
        plan,
        expected_revision=plan["expected_revision"],
        backup_path=tmp_path / "before.sqlite3",
    )
    spec = repository.get_strategy("seeking_alpha")
    candidate = spec.access.combinations[0]
    existing = repository.get_identity(candidate.identity_id or candidate.profile_id)
    before = {p.profile_id for p in repository.list_profiles()}
    resolved = service.resolve("https://seekingalpha.com/article/example")
    inherited = ScopeResolver(repository).resolve(resolved, "source:google_news_search_rss")
    assert inherited.access.combinations[0].identity_id == existing.identity_id
    assert {p.profile_id for p in repository.list_profiles()} == before
    assert repository.get_identity(existing.identity_id).lifecycle.residency.value == "on_demand"


def test_body_verification_does_not_treat_legacy_search_page_as_an_article():
    from types import SimpleNamespace

    from doxagent.site_strategy.verification import body_verification_url

    scope = SimpleNamespace(
        verification_plan={"crawler": {"url": "https://www.reuters.com/site-search/?query=Micron"}}
    )
    article = "https://www.reuters.com/world/chip-story-2026-10-17/"
    assert (
        body_verification_url(scope, "https://www.reuters.com/site-search/?query=BE", article)
        == article
    )
    assert body_verification_url(scope, "https://www.reuters.com/site-search/") is None


@pytest.mark.asyncio
async def test_verification_canary_shares_the_global_business_page_budget():
    import asyncio
    from types import SimpleNamespace

    from doxagent.site_strategy.worker_runtime import WorkerSiteAccessRuntime

    runtime = WorkerSiteAccessRuntime.__new__(WorkerSiteAccessRuntime)
    runtime._business_slots = asyncio.Semaphore(1)
    calls = []

    async def command(*args, **kwargs):
        calls.append(kwargs["timeout"])
        return {"done": True}

    runtime.workers = SimpleNamespace(command=command)
    await runtime._business_slots.acquire()
    task = asyncio.create_task(
        runtime._verify_with_capacity(None, None, "verify", {"timeout_ms": 1000}, timeout=1)
    )
    await asyncio.sleep(0)
    assert not calls
    runtime._business_slots.release()
    assert (await task)["done"] is True and len(calls) == 1
    assert runtime._business_slots._value == 1


def test_maintenance_control_failure_retains_only_safe_machine_reason():
    from doxagent.site_strategy.service import classify_exception

    assert (
        classify_exception(RuntimeError("controller_epoch_conflict"))[1]
        == "controller_epoch_conflict"
    )
    assert (
        "secret"
        not in classify_exception(RuntimeError("invalid_token=https://a.test/?secret=private"))[1]
    )


def test_inventory_does_not_report_stale_credentials_as_valid(seeded, tmp_path):
    from doxagent.site_strategy.maintenance import MaintenanceController
    from doxagent.site_strategy.schema import SiteIdentityAuth

    repository, service = seeded
    plan = migration_plan(repository)
    apply_migration(
        repository,
        plan,
        expected_revision=plan["expected_revision"],
        backup_path=tmp_path / "before.sqlite3",
    )
    scope = GovernanceRepository(repository).get_scope("source:trendforce_news")
    repository.save_site_identity_auth(
        SiteIdentityAuth(
            site_id=scope.owner_site_id,
            identity_id=scope.primary_identity_id,
            auth_state="VALID",
            observed_session_revision=0,
        )
    )
    runtime = repository.get_identity_runtime(scope.primary_identity_id)
    repository.save_identity_runtime(
        runtime.model_copy(update={"session_revision": 1}), expected_generation=runtime.generation
    )
    rows = MaintenanceController(service)._inventory()["profiles"]
    row = next(r for r in rows if r["identity_id"] == scope.primary_identity_id)
    assert row["auth_state"] == "REVERIFY_REQUIRED" and row["stored_auth_state"] == "VALID"


def test_gui_unexpected_thread_error_always_reaches_main_thread_completion():
    module = _load_desktop("site-login-ui.py")
    app = object.__new__(module.SiteLoginApp)
    app._closed = False
    app._pending = set()
    app._generations = {}
    app.mailbox = queue.Queue()

    def fail():
        raise RuntimeError("simulated unexpected exception")

    app._run(fail, lambda _: None)
    channel, generation, _, result = app.mailbox.get(timeout=2)
    assert channel == "command" and generation == 1
    assert result["ok"] is False


def test_late_viewer_callback_requires_exact_current_session_generation():
    module = _load_desktop("site-login-ui.py")
    expected = {"session_id": "s", "identity_id": "a", "generation": 2}
    assert module.viewer_binding_matches(expected, [{**expected, "state": "OPEN"}])
    assert not module.viewer_binding_matches(expected, [{**expected, "state": "RELEASED"}])
    assert not module.viewer_binding_matches(
        expected, [{**expected, "state": "OPEN", "generation": 3}]
    )
    assert not module.viewer_binding_matches(
        expected, [{**expected, "state": "OPEN", "identity_id": "b"}]
    )


@pytest.mark.asyncio
async def test_original_challenge_is_not_misreported_as_missing_backup_login(
    seeded, tmp_path, monkeypatch
):
    from doxagent.site_strategy.governance_models import ChallengeTask
    from doxagent.site_strategy.schema import AccessRequest, AccessResult

    repository, service = seeded
    plan = migration_plan(repository)
    apply_migration(
        repository,
        plan,
        expected_revision=plan["expected_revision"],
        backup_path=tmp_path / "before.sqlite3",
    )
    store = GovernanceRepository(repository)
    scope = store.get_scope("source:trendforce_news")
    target = store.save_target(
        OwnedBrowserTarget(
            identity_id=scope.primary_identity_id,
            browser_instance_id="gone",
            worker_epoch=1,
            target_id="original",
            state="LIVE",
        )
    )
    task = store.handoff_challenge(
        ChallengeTask(
            scope_id=scope.scope_id,
            identity_id=scope.primary_identity_id,
            site_id=scope.owner_site_id,
            purpose="CRAWLER",
            operation_id="poll",
            original_url="https://www.trendforce.com/news/",
            original_target_id="original",
            browser_instance_id="gone",
            allocation_id=target.allocation_id,
            reason="challenge_required",
        )
    )
    store.save_challenge(task.model_copy(update={"state": "TARGET_LOST"}))
    request = AccessRequest(
        operation_id="blocked",
        purpose="CRAWLER",
        mode="BROWSER",
        url="https://www.trendforce.com/news/",
        origin_scope_id=scope.scope_id,
    )

    async def missing_backup_login(request):
        return AccessResult(
            request_id=request.request_id,
            operation_id=request.operation_id,
            effective_scope_id=scope.scope_id,
            runtime_key="scope:" + scope.scope_id,
            site_id=scope.owner_site_id,
            strategy_revision=1,
            disposition="AUTH_REQUIRED",
            failure_category="AUTH_REQUIRED",
            reason_code="no_authenticated_profile_available",
        )

    monkeypatch.setattr(service, "_execute_deadlined", missing_backup_login)
    result = await service.execute(request)
    assert result.failure_category.value == "ACCESS_CHALLENGE"
    assert result.reason_code == "challenge_target_lost"
    assert result.challenge_task_id == task.task_id


@pytest.mark.asyncio
async def test_risk_does_not_activate_unverified_backup_or_accept_stale_success(seeded, tmp_path):
    from doxagent.site_strategy.schema import AccessCombination, AccessMode, FailureCategory

    repository, service = seeded
    plan = migration_plan(repository)
    apply_migration(
        repository,
        plan,
        expected_revision=plan["expected_revision"],
        backup_path=tmp_path / "before.sqlite3",
    )
    scope = GovernanceRepository(repository).get_scope("source:trendforce_news")
    key = f"scope:{scope.scope_id}"
    state = repository.get_runtime(key)
    await service.health.mark_failure(
        key,
        scope.primary_identity_id,
        FailureCategory.ACCESS_CHALLENGE,
        "challenge_required",
        assigned_generation=state.generation,
        transport=AccessMode.BROWSER,
    )
    current, candidates, _ = await service.health.candidates(
        key,
        [
            AccessCombination(
                id=scope.primary_identity_id, identity_id=scope.primary_identity_id, priority=0
            ),
            AccessCombination(
                id=scope.fallback_identity_id, identity_id=scope.fallback_identity_id, priority=1
            ),
        ],
        excluded=set(),
    )
    assert candidates[0].identity_id == scope.fallback_identity_id
    assert current.active_combination_id == scope.primary_identity_id
    await service.health.mark_success(
        key,
        scope.primary_identity_id,
        assigned_generation=state.generation,
        transport=AccessMode.BROWSER,
    )
    latest = repository.get_runtime(key)
    assert latest.combinations[scope.primary_identity_id].manual_attention_required
    assert latest.active_combination_id == scope.primary_identity_id


def test_failure_evidence_is_structural_coalesced_and_contains_no_form_values(seeded):
    from doxagent.site_strategy.failure_shapes import record_failure_shape
    from doxagent.site_strategy.runtime import RuntimeResponse
    from doxagent.site_strategy.schema import AccessRequest, FailureCategory

    repository, service = seeded
    request = AccessRequest(
        operation_id="shape",
        purpose="BODY",
        mode="BROWSER",
        url="https://www.barrons.com/articles/example",
    )
    response = RuntimeResponse(
        200,
        request.url + "?password=secret",
        {},
        '<html><input type="hidden" value="private-account-token"></html>',
    )
    for _ in range(3):
        record_failure_shape(
            repository,
            request,
            service.resolve(request.url),
            "barrons-1",
            response,
            FailureCategory.EXTRACTION_ERROR,
            "extraction_failed",
        )
    rows = repository._connection.execute("select * from access_failure_shapes").fetchall()
    assert len(rows) == 1 and rows[0]["seen_count"] == 3
    assert "secret" not in rows[0]["payload_json"]
    assert "private-account-token" not in rows[0]["payload_json"]


def test_login_target_reference_is_committed_before_lost_open_reply(seeded):
    repository, _ = seeded
    store = GovernanceRepository(repository)
    reserved = store.save_session(
        MaintenanceSession(
            kind="LOGIN",
            scope_id="publisher:barrons",
            site_id="barrons",
            identity_id="barrons-1",
            control_token="a" * 32,
        )
    )
    target = store.save_target(
        OwnedBrowserTarget(
            identity_id="barrons-1",
            browser_instance_id="native",
            worker_epoch=1,
            target_id="login",
            state="LIVE",
        )
    )
    store.handoff_login(target.allocation_id, reserved.control_token, "barrons")
    current = store.get_session(reserved.session_id)
    assert current.target_id == "login" and current.browser_instance_id == "native"
    assert store.get_target(target.allocation_id).owner_kind == "LOGIN"
    assert current.generation > reserved.generation
    with pytest.raises(RuntimeError, match="stale maintenance"):
        store.save_session(reserved.model_copy(update={"state": "CLOSED"}))


@pytest.mark.asyncio
async def test_exact_human_recovery_skips_disposed_orphan_page_from_other_channel(seeded):
    from types import SimpleNamespace

    from doxagent.site_strategy.external_runtime import ExternalChromeRuntime

    repository, _ = seeded
    stale = SimpleNamespace(url="about:blank#doxagent-orphan")
    human = SimpleNamespace(url="https://www.barrons.com/articles/real")

    class Session:
        async def send(self, _method):
            return {"targetInfo": {"targetId": "wanted-human"}}

        async def detach(self):
            pass

    class Context:
        pages = [stale, human]

        async def new_cdp_session(self, page):
            if page is stale:
                raise RuntimeError("page: no object with guid page@disposed")
            return Session()

    identity = repository.get_identity("barrons-1")
    egress = repository.get_egress(identity.egress_id)
    entry = SimpleNamespace(
        context=Context(),
        identity=identity,
        egress=egress,
        instance=SimpleNamespace(instance_id="native", generation=1),
        cdp=SimpleNamespace(_owned_targets=set()),
        active_pages=0,
        last_used=0,
    )
    runtime = ExternalChromeRuntime(SimpleNamespace())

    async def existing(*_args):
        return entry

    runtime._entry = existing
    lease = await runtime.existing_page(identity, egress, target_id="wanted-human")
    assert lease.page is human
    await lease.abandon()
    assert entry.active_pages == 0


def test_inventory_one_request_and_no_network_with_root_file_lock(monkeypatch):
    from contextlib import contextmanager

    module = _load_desktop("site-login-admin.py")
    calls = []
    locked = False

    @contextmanager
    def lock():
        nonlocal locked
        locked = True
        try:
            yield
        finally:
            locked = False

    def request(method, path, *args, **kwargs):
        assert not locked
        calls.append((method, path))
        return {"profiles": [], "sessions": []}

    monkeypatch.setattr(module, "_locked", lock)
    monkeypatch.setattr(module, "_request", request)
    monkeypatch.setattr(module, "_vnc_ready", lambda: True)
    assert module.command_list()["ok"]
    assert calls == [("GET", "/v1/maintenance/inventory")]
    assert module.command_recover()["session"] is None


def test_source_scopes_split_entries_not_tickers_and_redirect_to_publisher(seeded, tmp_path):
    from doxagent.site_strategy.schema import AccessRequest

    repository, service = seeded
    plan = migration_plan(repository)
    apply_migration(
        repository,
        plan,
        expected_revision=plan["expected_revision"],
        backup_path=tmp_path / "before.sqlite3",
    )
    news = service._resolve_request(
        AccessRequest(
            operation_id="news",
            mode="BROWSER",
            purpose="CRAWLER",
            url="https://www.trendforce.com/news/",
            origin_scope_id="source:trendforce_news",
        )
    )
    press = service._resolve_request(
        AccessRequest(
            operation_id="press",
            mode="BROWSER",
            purpose="CRAWLER",
            url="https://www.trendforce.com/presscenter/news",
            origin_scope_id="source:trendforce_press_releases",
        )
    )
    assert news.access.combinations[0].identity_id != press.access.combinations[0].identity_id
    before = len(repository.list_active_identities())
    for ticker in ("MU", "INTC", "BE"):
        same = service._resolve_request(
            AccessRequest(
                operation_id=ticker,
                mode="BROWSER",
                purpose="CRAWLER",
                url="https://www.trendforce.com/news/",
                origin_scope_id="source:trendforce_news",
            )
        )
        assert same.runtime_key == news.runtime_key
    assert len(repository.list_active_identities()) == before
    third_party = service._resolve_request(
        AccessRequest(
            operation_id="body",
            mode="BROWSER",
            purpose="BODY",
            url="https://www.wsj.com/articles/semiconductors",
            origin_scope_id="source:barrons_ticker_news",
        )
    )
    assert third_party.runtime_key == "scope:publisher:wsj"
    assert third_party.access.combinations[0].identity_id != "dowjones-main"
    aggregator = service._resolve_request(
        AccessRequest(
            operation_id="rss-body",
            purpose="BODY",
            mode="BROWSER",
            url="https://www.reuters.com/technology/example/",
            origin_scope_id="source:google_news_search_rss",
        )
    )
    assert aggregator.runtime_key == "scope:publisher:reuters"
    assert aggregator.access.combinations[0].identity_id != "digitimes-de-1"


@pytest.mark.asyncio
async def test_cold_fallback_uses_formal_parser_then_sticky_activation(seeded, tmp_path):
    from doxagent.site_strategy.runtime import RuntimeResponse
    from doxagent.site_strategy.schema import SitePurpose

    repository, service = seeded
    plan = migration_plan(repository)
    apply_migration(
        repository,
        plan,
        expected_revision=plan["expected_revision"],
        backup_path=tmp_path / "before.sqlite3",
    )
    scope = GovernanceRepository(repository).get_scope("source:trendforce_news")
    calls = []

    async def stop(identity, *, reason):
        calls.append(("cold-stop", identity.identity_id))
        return True

    async def execute(request, resolved, combination, profile, egress):
        calls.append(("formal-recipe", combination.identity_id))
        return RuntimeResponse(
            200,
            request.url,
            {},
            body="""<html><div class="insight-list-item">
          <a class="title-link" href="/news/2026/10/17/semiconductor-capacity/">
          Semiconductor manufacturing capacity expands</a></div></html>""",
        )

    service.runtime.browser_runtimes.stop_identity = stop
    service.runtime.execute = execute
    result = await service.failover.activate(scope, SitePurpose.CRAWLER)
    assert result["ready"] is True
    assert calls == [
        ("cold-stop", scope.fallback_identity_id),
        ("formal-recipe", scope.fallback_identity_id),
    ]
    assert (
        repository.get_runtime(f"scope:{scope.scope_id}").active_combination_id
        == scope.fallback_identity_id
    )
    assert service.failover.usable(scope, scope.fallback_identity_id, SitePurpose.CRAWLER)
    assert service.failover.schedule(scope, SitePurpose.CRAWLER) is None


@pytest.mark.asyncio
async def test_body_cold_canary_can_use_the_primary_verified_article(seeded, tmp_path, monkeypatch):
    from types import SimpleNamespace

    from doxagent.site_strategy.runtime import RuntimeResponse
    from doxagent.site_strategy.schema import SitePurpose

    repository, service = seeded
    plan = migration_plan(repository)
    apply_migration(
        repository,
        plan,
        expected_revision=plan["expected_revision"],
        backup_path=tmp_path / "before.sqlite3",
    )
    store = GovernanceRepository(repository)
    scope = store.get_scope("source:trendforce_news")
    url = "https://www.trendforce.com/news/2026/10/17/verified-article/"
    store.record_capability(scope, scope.primary_identity_id, "BODY", "FULL", url=url)

    async def stop(*args, **kwargs):
        return True

    async def attempt(request, *args):
        assert request.url == url
        return RuntimeResponse(200, url, {}, body="capture", body_quality="FULL"), None, "ok", None

    service.runtime.browser_runtimes.stop_identity = stop
    monkeypatch.setattr(service, "_attempt", attempt)
    monkeypatch.setattr(
        "doxagent.site_strategy.failover.validate_capture",
        lambda *a, **k: SimpleNamespace(body="FULL"),
    )
    result = await service.failover.activate(scope, SitePurpose.BODY)
    assert result["ready"] is True
    assert service.failover.usable(scope, scope.fallback_identity_id, SitePurpose.BODY)


@pytest.mark.asyncio
async def test_empty_200_cannot_activate_cold_fallback(seeded, tmp_path):
    from doxagent.site_strategy.runtime import RuntimeResponse
    from doxagent.site_strategy.schema import SitePurpose

    repository, service = seeded
    plan = migration_plan(repository)
    apply_migration(
        repository,
        plan,
        expected_revision=plan["expected_revision"],
        backup_path=tmp_path / "before.sqlite3",
    )
    scope = GovernanceRepository(repository).get_scope("source:trendforce_news")

    async def stop(*_args, **_kwargs):
        return True

    async def execute(request, *_args):
        return RuntimeResponse(200, request.url, {}, body="<html></html>")

    service.runtime.browser_runtimes.stop_identity = stop
    service.runtime.execute = execute
    result = await service.failover.activate(scope, SitePurpose.CRAWLER)
    assert not result["ready"]
    assert (
        repository.get_runtime(f"scope:{scope.scope_id}").active_combination_id
        == scope.primary_identity_id
    )
