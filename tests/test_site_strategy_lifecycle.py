from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from doxagent.site_strategy.repository import SiteStrategyRepository
from doxagent.site_strategy.runtime import (
    OwnerFileLock,
    PersistentBrowserPool,
    RuntimeResponse,
    _close_owned_page,
)
from doxagent.site_strategy.schema import (
    AccessMode,
    AccessRequest,
    BrowserRuntimeKind,
    ProfileOperationalState,
    SitePurpose,
)
from doxagent.site_strategy.seeds import bootstrap_seed
from doxagent.site_strategy.service import ProfileUnavailableError, SiteStrategyService


@pytest.mark.asyncio
async def test_page_cleanup_timeout_closes_only_the_owned_target() -> None:
    closed = []

    class Page:
        _doxagent_target_id = "owned-target"
        async def close(self):
            await asyncio.Event().wait()

    class CDP:
        async def send(self, method, params):
            closed.append((method, params))
            return {"success": True}

    await _close_owned_page(Page(), CDP(), timeout_seconds=0.01)
    assert closed == [("Target.closeTarget", {"targetId": "owned-target"})]


@pytest.mark.asyncio
async def test_access_deadline_includes_pre_admission_work(service, monkeypatch) -> None:
    from doxagent.site_strategy.schema import AccessMode, AccessRequest, SitePurpose

    async def stalled(_request):
        await asyncio.Event().wait()

    monkeypatch.setattr(service, "_execute_uncached", stalled)
    result = await service.execute(AccessRequest(
        operation_id="bounded-access", purpose=SitePurpose.CRAWLER,
        url="https://www.reuters.com/site-search/?query=Micron",
        mode=AccessMode.BROWSER, remaining_budget_ms=10,
    ))
    assert result.reason_code == "access_budget_exhausted"
    assert not service._inflight
    assert not service.access_diagnostics()["stalled"]


@pytest.mark.asyncio
async def test_cancelled_external_page_creation_returns_capacity(service, monkeypatch) -> None:
    from types import SimpleNamespace

    from doxagent.site_strategy.external_runtime import ExternalChromeRuntime

    started = asyncio.Event()
    class Context:
        async def new_page(self):
            started.set()
            await asyncio.Event().wait()

    adapter = ExternalChromeRuntime(None, max_pages=1)
    async def entry(_identity, _egress):
        return SimpleNamespace(context=Context())
    monkeypatch.setattr(adapter, "_entry", entry)
    monkeypatch.setattr(adapter, "_create_owned_page", lambda entry: entry.context.new_page())
    task = asyncio.create_task(adapter.page(None, None))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    async with asyncio.timeout(0.1):
        await adapter._page_slots.acquire()
    adapter._page_slots.release()


@pytest.fixture
def service(tmp_path: Path) -> SiteStrategyService:
    repository = SiteStrategyRepository(tmp_path / "registry.sqlite3")
    value = SiteStrategyService(repository, profile_root=tmp_path / "profiles")
    bootstrap_seed(repository, value)
    yield value
    repository.close()


@pytest.mark.asyncio
async def test_maintenance_drains_active_user_and_rejects_queued_user(
    service: SiteStrategyService,
) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    async def active_user() -> None:
        async with service.profile_use.business("barrons-1"):
            entered.set()
            await release.wait()

    task = asyncio.create_task(active_user())
    await entered.wait()
    maintenance = asyncio.create_task(
        service.profile_use.begin_maintenance("barrons-1", timeout_seconds=2)
    )
    for _ in range(50):
        profile = service.repository.get_profile("barrons-1")
        assert profile is not None
        if profile.operational_state is ProfileOperationalState.DRAINING_FOR_MAINTENANCE:
            break
        await asyncio.sleep(0.01)
    with pytest.raises(ProfileUnavailableError, match="maintenance"):
        async with service.profile_use.business("barrons-1"):
            pass
    release.set()
    await task
    token = await maintenance
    profile = service.repository.get_profile("barrons-1")
    assert profile is not None
    assert profile.operational_state is ProfileOperationalState.MAINTENANCE
    await service.profile_use.end_maintenance("barrons-1", token)
    assert (
        service.repository.get_profile("barrons-1").operational_state
        is ProfileOperationalState.AVAILABLE
    )


@pytest.mark.asyncio
async def test_verify_keeps_profile_exclusive_until_close(service: SiteStrategyService) -> None:
    async def open_login(profile, egress, url, *, token=None):
        return {"login_token": token, "url": url}

    async def verify_login(token, article_url):
        return RuntimeResponse(
            200,
            article_url,
            {},
            "<html><h1>Article</h1><article><p>Subscriber body text.</p></article></html>",
        )

    async def close_login(token):
        return "barrons-1"

    service.runtime.open_login = open_login  # type: ignore[method-assign]
    service.runtime.verify_login = verify_login  # type: ignore[method-assign]
    service.runtime.close_login = close_login  # type: ignore[method-assign]
    opened = await service.open_profile_login("barrons-1")
    token = opened["login_token"]
    updated, _ = await service.verify_profile(
        "barrons-1", "https://www.barrons.com/articles/example", token
    )
    assert updated.auth_state.value == "VALID"
    assert updated.operational_state is ProfileOperationalState.MAINTENANCE
    with pytest.raises(ProfileUnavailableError):
        async with service.profile_use.business("barrons-1"):
            pass
    await service.close_profile_login(token)
    assert (
        service.repository.get_profile("barrons-1").operational_state
        is ProfileOperationalState.AVAILABLE
    )


@pytest.mark.asyncio
async def test_only_one_profile_can_be_maintained(service: SiteStrategyService) -> None:
    first = await service.profile_use.begin_maintenance("barrons-1")
    with pytest.raises(ProfileUnavailableError, match="another_profile"):
        await service.profile_use.begin_maintenance("wsj-1")
    await service.profile_use.end_maintenance("barrons-1", first)


@pytest.mark.asyncio
async def test_snapshot_round_trip_is_consistent(
    service: SiteStrategyService, tmp_path: Path
) -> None:
    profile = service.repository.get_profile("barrons-1")
    assert profile is not None
    profile_dir = tmp_path / "profiles" / profile.directory_key
    profile_dir.mkdir(parents=True)
    marker = profile_dir / "Cookies.test"
    marker.write_text("before", encoding="utf-8")
    snapshot = await service.snapshot_profile("barrons-1", browser_version="153.0.8010.12")
    marker.write_text("after", encoding="utf-8")
    await service.restore_profile("barrons-1", snapshot["snapshot_id"])
    assert marker.read_text(encoding="utf-8") == "before"


@pytest.mark.asyncio
async def test_concurrent_profile_start_creates_one_browser(tmp_path: Path, service) -> None:
    launches = 0

    class Context:
        async def close(self, **_kwargs):
            return None

    class Chromium:
        async def launch_persistent_context(self, *_args, **_kwargs):
            nonlocal launches
            launches += 1
            await asyncio.sleep(0.05)
            return Context()

    class Playwright:
        chromium = Chromium()

    class CDP:
        async def close(self):
            return None

    pool = PersistentBrowserPool(
        tmp_path / "race-profiles",
        headless=False,
        channel=None,
        max_processes=4,
        max_pages=4,
        idle_seconds=60,
    )
    pool._playwright = Playwright()

    async def connect_cdp(_directory):
        return CDP()

    pool._connect_profile_cdp = connect_cdp  # type: ignore[method-assign]
    profile = service.repository.get_profile("barrons-1")
    egress = service.repository.get_egress("us-standard-5")
    assert profile is not None and egress is not None
    first, second = await asyncio.gather(
        pool._entry(profile, egress),
        pool._entry(profile, egress),
    )
    assert first is second
    assert launches == 1
    # A crashed managed browser must not remain cached forever; relaunch only
    # after a graceful context close releases its single-writer lock.
    class Disconnected:
        def is_connected(self):
            return False

    first.context.browser = Disconnected()
    third = await pool._entry(profile, egress)
    assert third is not first
    assert launches == 2
    async def cancelled_page():
        raise asyncio.CancelledError()

    third.context.new_page = cancelled_page
    with pytest.raises(asyncio.CancelledError):
        await pool.page(profile, egress)
    assert third.active_pages == 0
    assert pool._page_slots._value == 4
    assert await pool.close_profile_if_idle(profile.profile_id, reason="test")


@pytest.mark.asyncio
async def test_failed_context_close_keeps_profile_writer_lock(
    tmp_path: Path, service: SiteStrategyService
) -> None:
    class Context:
        async def close(self, **_kwargs):
            raise RuntimeError("close failed")

    class Chromium:
        async def launch_persistent_context(self, *_args, **_kwargs):
            return Context()

    class Playwright:
        chromium = Chromium()

    class CDP:
        async def close(self):
            return None

    pool = PersistentBrowserPool(
        tmp_path / "unclean-profiles",
        headless=False,
        channel=None,
        max_processes=1,
        max_pages=1,
        idle_seconds=60,
    )
    pool.root.mkdir(parents=True)
    pool._playwright = Playwright()

    async def connect_cdp(_directory):
        return CDP()

    pool._connect_profile_cdp = connect_cdp  # type: ignore[method-assign]
    profile = service.repository.get_profile("barrons-1")
    egress = service.repository.get_egress("us-standard-5")
    assert profile is not None and egress is not None
    await pool._entry(profile, egress)

    with pytest.raises(RuntimeError, match="close failed"):
        await pool.close_profile_if_idle(profile.profile_id, reason="test_failure")
    contender = OwnerFileLock(
        pool.root / ".profile-locks" / f"{profile.directory_key}.lock"
    )
    with pytest.raises(RuntimeError, match="already held"):
        contender.acquire()
    assert len(pool._unclean_entries) == 1
    pool._unclean_entries[0].owner_lock.release()


@pytest.mark.asyncio
async def test_dead_playwright_driver_is_not_ready_and_triggers_restart(
    service: SiteStrategyService, monkeypatch: pytest.MonkeyPatch
) -> None:
    pool = service.runtime.browser_pool
    process = SimpleNamespace(returncode=None)
    pool._playwright = SimpleNamespace(
        _impl_obj=SimpleNamespace(
            _connection=SimpleNamespace(_transport=SimpleNamespace(_proc=process))
        )
    )
    assert pool.driver_ready
    process.returncode = 134
    assert not pool.driver_ready
    with pytest.raises(RuntimeError, match="playwright_driver_exited:134"):
        await pool.start()
    signals: list[tuple[int, int]] = []
    monkeypatch.setattr(
        "doxagent.site_strategy.service.os.kill", lambda pid, sig: signals.append((pid, sig))
    )
    await service._maintenance_loop()
    assert len(signals) == 1


@pytest.mark.asyncio
async def test_challenge_pauses_its_identity_while_another_profile_is_maintained(
    service: SiteStrategyService,
) -> None:
    manual_token = await service.profile_use.begin_maintenance("wsj-1")
    challenge_token = "b" * 32
    async with service.profile_use.business("barrons-1"):
        await service.profile_use.begin_challenge(
            "barrons-1",
            token=challenge_token,
            site_id="barrons",
            identity_id="barrons-1",
            combination_id="barrons-1",
            article_url="https://www.barrons.com/articles/example",
            target_id="target-2",
        )
    await service.profile_use.finish_challenge("barrons-1", challenge_token)
    await service.profile_use.assert_session("barrons-1", challenge_token)
    await service.profile_use.end_maintenance("wsj-1", manual_token)
    await service.profile_use.assert_session("barrons-1", challenge_token)
    await service.profile_use.end_maintenance("barrons-1", challenge_token)


@pytest.mark.asyncio
async def test_exact_profile_probe_also_hands_off_a_challenge(
    service: SiteStrategyService,
) -> None:
    async def challenge(*_args):
        return RuntimeResponse(
            200,
            "https://www.barrons.com/articles/example",
            {},
            "<html><title>Access is temporarily restricted</title></html>",
            challenge_token="c" * 32,
            challenge_target_id="target-3",
        )

    service.runtime.execute = challenge  # type: ignore[method-assign]
    result = await service.probe_profile("barrons-1", "https://www.barrons.com/articles/example")
    assert result.failure_category is not None
    assert result.failure_category.value == "ACCESS_CHALLENGE"
    profile = service.repository.get_profile("barrons-1")
    assert profile is not None
    assert profile.operational_state is ProfileOperationalState.MAINTENANCE
    assert profile.challenge_target_id == "target-3"


@pytest.mark.asyncio
async def test_challenge_keeps_original_article_and_blocks_identity_until_verified(
    service: SiteStrategyService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    article = "https://www.barrons.com/articles/example"
    token = "a" * 32
    combination = "barrons-1"
    async with service.profile_use.business("barrons-1"):
        await service.profile_use.begin_challenge(
            "barrons-1",
            token=token,
            site_id="barrons",
            identity_id="barrons-1",
            combination_id=combination,
            article_url=article,
            target_id="chrome-target-1",
        )
        with pytest.raises(ProfileUnavailableError, match="maintenance"):
            async with service.profile_use.business("barrons-1"):
                pass
    await service.profile_use.finish_challenge("barrons-1", token)
    profile = service.repository.get_profile("barrons-1")
    assert profile is not None
    assert profile.operational_state is ProfileOperationalState.MAINTENANCE
    assert profile.challenge_target_id == "chrome-target-1"
    identity = service.repository.get_identity("barrons-1")
    assert identity is not None
    external = identity.model_copy(update={"runtime_kind": BrowserRuntimeKind.EXTERNAL_CHROME})
    original_list = service.repository.list_active_identities
    monkeypatch.setattr(
        service.repository,
        "list_active_identities",
        lambda: [
            (head, external if item.identity_id == "barrons-1" else item)
            for head, item in original_list()
        ],
    )
    await service.profile_use.recover()
    recovered = service.repository.get_profile("barrons-1")
    assert recovered is not None
    assert recovered.operational_state is ProfileOperationalState.MAINTENANCE
    assert recovered.challenge_target_id == "chrome-target-1"

    async def adopt_challenge(profile, egress, *, identity, site_id, token):
        return {"login_token": token, "url": article, "challenge_url": article}

    async def verify_login(token, article_url):
        return RuntimeResponse(
            200,
            article_url,
            {},
            "<html><h1>Article</h1><article><p>Subscriber body text.</p></article></html>",
        )

    async def close_login(token):
        return "barrons-1"

    service.runtime.adopt_challenge = adopt_challenge  # type: ignore[method-assign]
    service.runtime.recover_login = adopt_challenge  # type: ignore[method-assign]
    service.runtime.verify_login = verify_login  # type: ignore[method-assign]
    service.runtime.close_login = close_login  # type: ignore[method-assign]
    with pytest.raises(ProfileUnavailableError, match="profile_in_maintenance"):
        await service.open_identity_login("barrons", "barrons-1")
    opened = await service.recover_identity_login(
        "barrons", "barrons-1", token, target_id="chrome-target-1"
    )
    assert opened["login_token"] == token
    with pytest.raises(RuntimeError, match="not been verified"):
        await service.close_profile_login(token)
    with pytest.raises(ValueError, match="triggering article"):
        await service.verify_identity(
            "barrons", "barrons-1", "https://www.barrons.com/articles/other", token
        )
    updated, _ = await service.verify_identity("barrons", "barrons-1", article, token)
    assert updated.challenge_verified is True
    await service.close_profile_login(token)
    resumed = service.repository.get_profile("barrons-1")
    assert resumed is not None
    assert resumed.operational_state is ProfileOperationalState.AVAILABLE
    assert resumed.challenge_url is None


@pytest.mark.asyncio
async def test_page_use_limit_recycles_only_idle_profile(
    tmp_path: Path, service: SiteStrategyService
) -> None:
    launches = 0
    closes = 0

    class Page:
        async def close(self):
            pass

    class Context:
        async def new_page(self):
            return Page()

        async def close(self, **_kwargs):
            nonlocal closes
            closes += 1

    class Chromium:
        async def launch_persistent_context(self, *_args, **_kwargs):
            nonlocal launches
            launches += 1
            return Context()

    class Playwright:
        chromium = Chromium()

        async def stop(self):
            pass

    class CDP:
        async def close(self):
            pass

    pool = PersistentBrowserPool(
        tmp_path / "recycle-profiles",
        headless=True,
        channel=None,
        max_processes=1,
        max_pages=4,
        idle_seconds=60,
    )
    pool._playwright = Playwright()

    async def connect_cdp(_directory):
        return CDP()

    pool._connect_profile_cdp = connect_cdp  # type: ignore[method-assign]
    profile = service.repository.get_profile("barrons-1")
    egress = service.repository.get_egress("us-standard-5")
    assert profile is not None and egress is not None
    leases = [await pool.page(profile, egress, max_context_pages=2) for _ in range(3)]
    assert launches == 1 and closes == 0
    for lease in leases:
        await lease.__aexit__(None, None, None)
    next_lease = await pool.page(profile, egress, max_context_pages=2)
    assert launches == 2 and closes == 1
    assert next_lease.entry.pages_opened == 1
    await next_lease.__aexit__(None, None, None)
    await pool.close()


@pytest.mark.asyncio
async def test_challenge_reconnect_failure_preserves_original_pause(
    service: SiteStrategyService, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = "b" * 32
    article = "https://www.barrons.com/articles/example"
    identity = service.repository.get_identity("barrons-1")
    assert identity is not None
    original_get = service.repository.get_identity
    external = identity.model_copy(update={"runtime_kind": BrowserRuntimeKind.EXTERNAL_CHROME})
    monkeypatch.setattr(
        service.repository, "get_identity",
        lambda name: external if name == identity.identity_id else original_get(name),
    )
    await service.profile_use.begin_challenge(
        "barrons-1", token=token, site_id="barrons", identity_id="barrons-1",
        combination_id="barrons-1", article_url=article, target_id="retained-target",
    )
    await service.profile_use.finish_challenge("barrons-1", token)

    async def disconnected(*_args, **_kwargs):
        raise RuntimeError("controller disconnected")

    monkeypatch.setattr(service.runtime, "recover_login", disconnected)
    with pytest.raises(ValueError, match="binding mismatch"):
        await service.recover_identity_login("wsj", "barrons-1", token)
    with pytest.raises(RuntimeError, match="controller disconnected"):
        await service.recover_identity_login("barrons", "barrons-1", token)
    profile = service.repository.get_profile("barrons-1")
    assert profile is not None
    assert profile.operational_state is ProfileOperationalState.MAINTENANCE
    assert profile.maintenance_session_id == token
    assert profile.challenge_url == article
    assert profile.challenge_target_id == "retained-target"
    with pytest.raises(ProfileUnavailableError, match="maintenance"):
        async with service.profile_use.business("barrons-1"):
            pass


@pytest.mark.asyncio
async def test_browser_challenge_preserves_primary_and_allows_fallback(
    service: SiteStrategyService, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Profile ownership/fallback test; pacing is covered by budget tests.
    spec = service.repository.get_strategy("yahoo_finance")
    service.apply_strategy(spec.model_copy(update={"access":spec.access.model_copy(update={"min_interval_ms":0})}),
        expected_revision=spec.revision,actor="test")
    calls = []
    token = "c" * 32
    article = "https://finance.yahoo.com/markets/stocks/articles/example.html"

    async def challenge(request, _resolved, combination, profile, _egress):
        calls.append((combination.combination_id, profile.profile_id))
        if len(calls) == 2:
            return RuntimeResponse(
                200, request.url, {}, "<html><body>fallback result</body></html>"
            )
        return RuntimeResponse(
            200, request.url, {},
            "<html><title>Access is temporarily restricted</title></html>",
            challenge_token=token, challenge_target_id="yahoo-target",
        )

    monkeypatch.setattr(service.runtime, "execute", challenge)
    result = await service.execute(AccessRequest(
        operation_id="challenge-fallback", purpose=SitePurpose.CRAWLER,
        mode=AccessMode.BROWSER, url=article,
    ))
    assert len(calls) == 2
    assert result.combination_id == calls[1][0]
    profile = service.repository.get_profile(calls[0][1])
    assert profile is not None
    assert profile.operational_state is ProfileOperationalState.MAINTENANCE
    assert profile.maintenance_session_id == token
    assert profile.challenge_url == article
