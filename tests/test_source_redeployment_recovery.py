from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from doxagent.site_strategy import runtime as module
from doxagent.site_strategy.repository import SiteStrategyRepository
from doxagent.site_strategy.schema import (
    AccessMode,
    AccessRequest,
    AuthState,
    BrowserIdentitySpec,
    BrowserRuntimeKind,
    IdentityOperationalState,
    SitePurpose,
)
from doxagent.site_strategy.seeds import bootstrap_seed
from doxagent.site_strategy.service import SiteStrategyService


@pytest.fixture
def service(tmp_path):
    repo = SiteStrategyRepository(tmp_path / "registry.sqlite3")
    service = SiteStrategyService(repo, profile_root=tmp_path / "profiles")
    bootstrap_seed(repo, service)
    yield service
    repo.close()


def stale_links(directory):
    directory.mkdir(parents=True, exist_ok=True)
    try:
        for name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
            (directory / name).symlink_to("retired-container-198484")
    except OSError:
        pytest.skip("symlinks unavailable")


@pytest.mark.asyncio
async def test_managed_redeploy_recovers_links_only_under_writer_lock(service, monkeypatch):
    profile = service.repository.get_profile("reuters-1")
    egress = service.repository.get_egress(profile.bound_egress_id)
    pool = module.PersistentBrowserPool(
        service.runtime.browser_pool.root,
        headless=False,
        channel=None,
        max_processes=1,
        max_pages=1,
        idle_seconds=60,
    )
    directory = pool.root / profile.directory_key
    stale_links(directory)
    (directory / "Cookies").write_bytes(b"preserved")

    async def launch(*args, **kwargs):
        assert not (directory / "SingletonLock").is_symlink()
        contender = module.OwnerFileLock(
            pool.root / ".profile-locks" / (profile.directory_key + ".lock")
        )
        with pytest.raises(RuntimeError, match="already held"):
            contender.acquire()
        return SimpleNamespace(close=AsyncMock())

    pool._playwright = SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=launch))
    monkeypatch.setattr(
        pool, "_connect_profile_cdp", AsyncMock(return_value=SimpleNamespace(close=AsyncMock()))
    )
    await pool._entry(profile, egress)
    assert (directory / "Cookies").read_bytes() == b"preserved"
    assert await pool.close_profile_if_idle(profile.profile_id, reason="test")


def test_unknown_singleton_aborts_without_partial_removal(tmp_path):
    stale_links(tmp_path)
    cookie = tmp_path / "SingletonCookie"
    cookie.unlink()
    cookie.write_text("do not remove")
    with pytest.raises(RuntimeError, match="profile_busy_unknown_singleton"):
        module.clear_profile_runtime_links(tmp_path)
    assert (tmp_path / "SingletonLock").is_symlink()
    assert cookie.read_text() == "do not remove"


def test_profile_lock_failure_is_runtime_not_network():
    from doxagent.site_strategy.service import classify_exception

    category, reason = classify_exception(RuntimeError("Failed to create a ProcessSingleton"))
    assert category.value == "RUNTIME_UNAVAILABLE"
    assert reason == "browser_profile_busy"


def test_cdp_attach_timeout_is_not_a_website_network_retry():
    from doxagent.site_strategy.service import classify_exception

    category, reason = classify_exception(
        TimeoutError("BrowserType.connect_over_cdp: Timeout 20000ms exceeded. <ws connected>")
    )
    assert category.value == "RUNTIME_UNAVAILABLE"
    assert reason == "browser_cdp_attach_failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("late_challenge", [True, False])
async def test_investing_selector_failure_inspects_document_before_cleanup(
    monkeypatch, late_challenge
):
    monkeypatch.setattr(module, "public_url", AsyncMock())
    url = "https://www.investing.com/equities/intel-corp-news"
    final = (
        "<html><title>Verify you are human</title></html>"
        if late_challenge
        else "<html><h1>Intel news</h1></html>"
    )
    response = SimpleNamespace(status=200, all_headers=AsyncMock(return_value={}))
    locator = SimpleNamespace(wait_for=AsyncMock(side_effect=TimeoutError("selector timed out")))
    page = SimpleNamespace(
        url=url,
        goto=AsyncMock(return_value=response),
        content=AsyncMock(side_effect=["<html></html>", final]),
        locator=lambda _: SimpleNamespace(first=locator),
    )
    lease = SimpleNamespace(
        __aenter__=AsyncMock(return_value=page),
        __aexit__=AsyncMock(),
        provenance=SimpleNamespace(instance_id="instance"),
        browser_cdp=object(),
    )
    gate = SimpleNamespace(
        start=AsyncMock(), close=AsyncMock(), root_target_id="original", blocked=[]
    )
    monkeypatch.setattr(module, "_DocumentNavigationGate", lambda *args: gate)
    monkeypatch.setattr(
        module, "wait_for_native_document_redirect", AsyncMock(return_value=response)
    )
    runtime = module.SiteAccessRuntime.__new__(module.SiteAccessRuntime)
    runtime._maintenance_pages = {}
    runtime.resolver = object()
    runtime.browser_runtimes = SimpleNamespace(page=AsyncMock(return_value=lease))
    runtime.repository = SimpleNamespace(
        get_identity_runtime=lambda _: SimpleNamespace(
            instance_id="instance", operational_state=IdentityOperationalState.AVAILABLE
        )
    )
    identity = BrowserIdentitySpec(
        identity_id="public",
        profile_id="public",
        egress_id="proxy",
        runtime_kind=BrowserRuntimeKind.EXTERNAL_CHROME,
    )
    request = AccessRequest(
        operation_id="late", url=url, purpose=SitePurpose.CRAWLER, mode=AccessMode.BROWSER
    )
    if late_challenge:
        result = await runtime._browser(
            request, SimpleNamespace(site_id="investing"), identity, object()
        )
        assert result.challenge_token in runtime._maintenance_pages
        assert result.body == final and result.status_code == 200
        lease.__aexit__.assert_not_awaited()
    else:
        with pytest.raises(TimeoutError, match="selector"):
            await runtime._browser(
                request, SimpleNamespace(site_id="investing"), identity, object()
            )
        assert not runtime._maintenance_pages
        lease.__aexit__.assert_awaited_once()
    gate.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("has_error_document", [True, False])
async def test_reuters_recipe_challenge_preserves_original_page(monkeypatch, has_error_document):
    monkeypatch.setattr(module, "public_url", AsyncMock())
    url = "https://www.reuters.com/site-search/?query=Micron&offset=0"
    html = "<html><title>Verify you are human</title></html>"
    page = SimpleNamespace(url=url, content=AsyncMock(return_value=html))
    lease = SimpleNamespace(
        __aenter__=AsyncMock(return_value=page),
        __aexit__=AsyncMock(),
        provenance=SimpleNamespace(instance_id="instance"),
        browser_cdp=object(),
    )
    gate = SimpleNamespace(start=AsyncMock(), close=AsyncMock(), root_target_id="original")
    monkeypatch.setattr(module, "_DocumentNavigationGate", lambda *args: gate)
    error = RuntimeError("search failed")
    if has_error_document:
        error.status_code, error.response_body = 401, html
    monkeypatch.setattr(module, "capture_reuters_search", AsyncMock(side_effect=error))
    runtime = module.SiteAccessRuntime.__new__(module.SiteAccessRuntime)
    runtime._maintenance_pages = {}
    runtime.resolver = object()
    runtime.browser_runtimes = SimpleNamespace(page=AsyncMock(return_value=lease))
    runtime.repository = SimpleNamespace(
        get_identity_runtime=lambda key: SimpleNamespace(
            instance_id="instance", operational_state=IdentityOperationalState.AVAILABLE
        )
    )
    identity = BrowserIdentitySpec(
        identity_id="reuters-test",
        profile_id="reuters-test",
        egress_id="proxy",
        runtime_kind=BrowserRuntimeKind.EXTERNAL_CHROME,
    )
    response = await runtime._browser(
        AccessRequest(
            operation_id="test",
            url=url,
            purpose=SitePurpose.CRAWLER,
            mode=AccessMode.BROWSER,
            recipe_ref="builtin:reuters_search@1",
            recipe_parameters={"query": "Micron"},
        ),
        SimpleNamespace(site_id="reuters"),
        identity,
        object(),
    )
    assert response.challenge_token in runtime._maintenance_pages
    assert response.challenge_target_id == "original"
    assert runtime._maintenance_pages[response.challenge_token].challenge_url == url
    lease.__aexit__.assert_not_awaited()
    gate.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_public_challenge_verification_releases_access_without_claiming_subscription(service):
    url = "https://www.reuters.com/site-search/?query=Micron&offset=0"
    token = "a" * 32
    async with service.profile_use.business("reuters-1"):
        await service.profile_use.begin_challenge(
            "reuters-1",
            token=token,
            site_id="reuters",
            identity_id="reuters-1",
            combination_id="reuters-1",
            article_url=url,
            target_id="original",
        )
    await service.profile_use.finish_challenge("reuters-1", token)
    service.runtime.verify_login = AsyncMock(
        return_value=module.RuntimeResponse(
            200, url, {}, "<html><h1>Search results for Micron</h1></html>"
        )
    )
    service.runtime.close_login = AsyncMock(return_value="reuters-1")
    updated, reason = await service.verify_identity("reuters", "reuters-1", url, token)
    assert reason == "public_access_ready"
    assert updated.auth_state is AuthState.UNKNOWN
    assert updated.challenge_verified
    await service.close_profile_login(token)
    assert service.repository.get_profile("reuters-1").challenge_url is None


def test_gui_keeps_viewer_and_session_after_failed_challenge_verification(monkeypatch):
    path = Path(__file__).parents[1] / "deploy/site-login-ui.py"
    spec = importlib.util.spec_from_file_location("site_login_ui_test", path)
    ui = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ui)
    monkeypatch.setattr(ui.messagebox, "showwarning", Mock())
    app = SimpleNamespace(_stop_owned_viewer=Mock(), _recover=Mock(), session={"active": True})
    ui.SiteLoginApp._verified(app, {"closed": False, "message": "Keep original tab open"})
    app._stop_owned_viewer.assert_not_called()
    assert app.session == {"active": True}
    app._recover.assert_called_once()


def test_admin_closes_verified_public_challenge_without_subscription_claim(tmp_path, monkeypatch):
    from contextlib import nullcontext

    path = Path(__file__).parents[1] / "deploy/site-login-admin.py"
    spec = importlib.util.spec_from_file_location("site_admin_public_test", path)
    admin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(admin)
    session = {
        "profile_id": "reuters-1",
        "site_id": "reuters",
        "login_token": "a" * 32,
        "challenge_url": "https://www.reuters.com/site-search/",
    }
    monkeypatch.setattr(admin, "_locked", nullcontext)
    monkeypatch.setattr(admin, "_read_session", lambda: session)
    monkeypatch.setattr(admin, "_inventory", lambda: {"profiles": [session]})
    monkeypatch.setattr(
        admin,
        "_request",
        lambda *args, **kwargs: {
            "profile": {"auth_state": "UNKNOWN", "challenge_verified": True},
            "reason": "public_access_ready",
        },
    )
    close = Mock(return_value=True)
    monkeypatch.setattr(admin, "_close_token", close)
    monkeypatch.setattr(admin, "_delete_session", Mock())
    result = admin.command_verify("reuters-1", None, "reuters")
    assert result["closed"] and result["auth_state"] == "UNKNOWN"
    close.assert_called_once()
