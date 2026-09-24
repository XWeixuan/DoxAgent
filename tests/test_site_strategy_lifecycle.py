from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from doxagent.site_strategy.repository import SiteStrategyRepository
from doxagent.site_strategy.runtime import OwnerFileLock, PersistentBrowserPool, RuntimeResponse
from doxagent.site_strategy.schema import ProfileOperationalState
from doxagent.site_strategy.seeds import bootstrap_seed
from doxagent.site_strategy.service import ProfileUnavailableError, SiteStrategyService


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
