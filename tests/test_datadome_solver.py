import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests import test_browser_recovery as recovery_tests
from doxagent.site_strategy.datadome_browser import (
    captcha_url,
    challenge_state,
    solution_cookie,
    NativeChallenge,
)
from doxagent.site_strategy.datadome_solver import SolverError

URL = "https://geo.captcha-delivery.com/captcha/?t=fe&cid=challenge"


@pytest.fixture
def setup(tmp_path):
    yield from recovery_tests.setup.__wrapped__(tmp_path)


@pytest.mark.parametrize(
    "raw",
    [
        "session=steal",
        "datadome=x; Domain=evil.com",
        "datadome=x; Domain=com",
        "datadome=x\r\nX: injection",
        "datadome=",
        "datadome=x; other=evil",
    ],
)
def test_cookie_cannot_escape_publisher(raw):
    with pytest.raises(ValueError):
        solution_cookie(raw, "https://www.reuters.com/site-search/")


def test_original_cookie_and_challenge_url_not_synthesized():
    from doxagent.site_strategy.datadome_browser import loaded_challenge_url

    cookie = solution_cookie(
        "datadome=abc; Domain=.reuters.com; Secure; Path=/", "https://www.reuters.com/"
    )
    assert cookie["domain"] == ".reuters.com" and cookie["value"] == "abc"
    assert captcha_url(f'<iframe src="{URL}"/>') == URL
    assert captcha_url('<iframe src="https://captcha-delivery.com.evil.com/?t=fe"/>') is None
    assert challenge_state(URL.replace("fe", "bv"), "") == "IP_BANNED"
    assert challenge_state(URL, "<script>retry()</script>") == "SOLVABLE"
    assert challenge_state(URL, "<button>Retry</button>") == "RETRY"
    assert challenge_state(URL.replace("/captcha/", "/interstitial/"), "") == "INTERSTITIAL"
    assert loaded_challenge_url(URL) == URL
    assert loaded_challenge_url("https://captcha-delivery.com.evil.com/captcha/") is None


@pytest.mark.parametrize('attribute,expected', [
    ('; Domain=reuters.com', '.reuters.com'),
    ('; Domain=.reuters.com', '.reuters.com'),
    ('', 'www.reuters.com'),
])
def test_provider_cookie_scope_preserves_domain_attribute(attribute, expected):
    assert solution_cookie('datadome=token'+attribute, 'https://www.reuters.com/')['domain'] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize('visible', [True, False])
async def test_native_apply_requires_cookie_visible_on_actual_publisher(monkeypatch, visible):
    from contextlib import asynccontextmanager

    calls = []
    async def call(method, params=None):
        calls.append(method)
        return {
            'DOM.getDocument': {'root': {'documentURL':'https://www.reuters.com/'}},
            'Network.setCookie': {'success':True},
            'Network.getCookies': {'cookies': ([{'name':'datadome','value':'token','domain':'.reuters.com'}]
                                              if visible else [])},
            'Page.reload': {},
        }[method]
    @asynccontextmanager
    async def connection():
        yield call
    browser = NativeChallenge.__new__(NativeChallenge)
    monkeypatch.setattr(browser, 'connection', connection)
    if visible:
        await browser.apply('datadome=token; Domain=reuters.com', 'https://www.reuters.com/')
        assert calls[-1] == 'Page.reload'
    else:
        with pytest.raises(RuntimeError, match='solver_cookie_not_visible_to_publisher'):
            await browser.apply('datadome=token; Domain=reuters.com', 'https://www.reuters.com/')
        assert 'Page.reload' not in calls


def test_blocking_datadome_bootstrap_is_retained_before_iframe_loads():
    from doxagent.site_strategy.challenge_detection import assess

    stub = (
        "<html>Please enable JS and disable any ad blocker"
        '<script>var dd={"t":"fe"}</script>'
        '<script src="https://ct.captcha-delivery.com/c.js"></script></html>'
    )
    assert assess(stub, status=403).provider == "DATADOME"
    assert assess(stub, status=403).kind == "UNKNOWN"
    assert (
        assess(
            '<article>Normal</article><script src="https://ct.captcha-delivery.com/c.js"/>',
            status=200,
        ).provider
        == "UNKNOWN"
    )


def test_datadome_device_check_is_automatic_not_a_human_slider(setup):
    from doxagent.site_strategy.challenge_detection import assess

    sample = assess(
        '<iframe src="https://geo.captcha-delivery.com/interstitial/?x=y"/>', blocked=True
    )
    assert sample.provider == "DATADOME" and sample.kind == "AUTO_CHECK"
    assert sample.handling_mode == "AUTO"
    _, _, store, _ = setup
    recovery, target, _ = recovery_tests.retained(setup, human=True)
    store.save_recovery(
        recovery.model_copy(update={"handling_mode": "AUTO"}),
        expected_generation=recovery.generation,
    )
    assert store.get_recovery(recovery.identity_id).phase != "RESOLVED"
    assert store.get_target(target.allocation_id).state == "LIVE"
    state = store.repository.get_runtime(f"scope:{recovery.scope_id}")
    assert not state.combinations[recovery.identity_id].manual_attention_required


def test_device_check_escalation_creates_exact_human_fallback_task(setup):
    _, _, store, _ = setup
    recovery, target, _ = recovery_tests.retained(setup)
    updated = store.save_recovery(
        recovery.model_copy(update={"handling_mode": "HUMAN"}),
        expected_generation=recovery.generation,
    )
    task = store.current_challenge(recovery.identity_id)
    assert updated.challenge_task_id == task.task_id
    assert task.original_target_id == target.target_id
    assert store.get_target(target.allocation_id).owner_ref == task.task_id
    assert store.get_recovery(recovery.identity_id).phase != "RESOLVED"


def test_reclassified_old_task_cannot_steal_replacement_human_target(setup):
    from doxagent.site_strategy.governance_models import OwnedBrowserTarget

    _, _, store, _ = setup
    recovery, old_target, _ = recovery_tests.retained(setup, human=True)
    old_task = recovery.challenge_task_id
    recovery = store.save_recovery(
        recovery.model_copy(update={"handling_mode": "AUTO"}),
        expected_generation=recovery.generation,
    )
    assert store.get_challenge(old_task).state == "RESOLVED"
    assert store.get_target(old_target.allocation_id).owner_kind == "RECOVERY"
    new = store.save_target(
        OwnedBrowserTarget(
            identity_id=recovery.identity_id,
            browser_instance_id="chrome",
            target_id="real-new-slider",
            state="LIVE",
            worker_epoch=1,
        )
    )
    value = store.begin_recovery(
        recovery.model_copy(
            update={
                "handling_mode": "HUMAN",
                "original_allocation_id": new.allocation_id,
                "original_target_id": new.target_id,
            }
        ).model_dump()
    )
    task = store.current_challenge(recovery.identity_id)
    assert task.task_id != old_task and task.original_target_id == new.target_id
    assert store.get_target(new.allocation_id).owner_ref == task.task_id == value.challenge_task_id


def test_changed_logical_token_still_requires_exact_physical_page():
    from doxagent.site_strategy.runtime import retained_entry

    page = SimpleNamespace(
        identity_id="identity",
        lease=SimpleNamespace(provenance=SimpleNamespace(instance_id="chrome")),
        gate=SimpleNamespace(root_target_id="original"),
    )
    assert retained_entry({"old-task": page}, "identity", "chrome", "original", "new-task") == (
        "old-task",
        page,
    )
    assert (
        retained_entry({"old-task": page}, "identity", "other-chrome", "original", "new-task")[1]
        is None
    )


@pytest.mark.asyncio
async def test_fresh_original_does_not_inherit_old_incident_ttl(setup):
    from datetime import timedelta
    from doxagent.site_strategy.schema import utc_now
    from doxagent.site_strategy.runtime import RuntimeResponse

    _, service, store, _ = setup
    value, target, _ = recovery_tests.retained(setup)
    store.save_recovery(
        value.model_copy(
            update={
                "opened_at": utc_now() - timedelta(hours=2),
                "next_probe_at": utc_now() + timedelta(minutes=10),
            }
        ),
        expected_generation=value.generation,
    )
    service.recovery._observe = AsyncMock(
        return_value=RuntimeResponse(
            403,
            recovery_tests.URL,
            {},
            '<iframe src="https://challenges.cloudflare.com/test"/>',
            provenance=SimpleNamespace(instance_id="chrome"),
        )
    )
    service.runtime.close_recovery = AsyncMock()
    await service.recovery._run(value.identity_id)
    service.runtime.close_recovery.assert_not_awaited()
    assert store.get_target(target.allocation_id).state == "LIVE"


@pytest.fixture
def solver_setup(setup):
    repo, service, store, scope = setup
    value, _, _ = recovery_tests.retained(setup, human=True)
    value = store.save_recovery(
        value.model_copy(update={"assessment": {"provider": "DATADOME"}}),
        expected_generation=value.generation,
    )
    value = store.claim_recovery(value.identity_id, owner="test")
    identity = repo.get_identity(value.identity_id)
    from doxagent.site_strategy.schema import BrowserRuntimeKind

    identity = identity.model_copy(update={"runtime_kind": BrowserRuntimeKind.EXTERNAL_CHROME})
    egress = repo.get_egress(identity.egress_id)
    solver = service.recovery.datadome_solver
    binding = {
        "egress_id": egress.egress_id,
        "proxyAddress": "1.2.3.4",
        "localAddress": "proxy",
        "proxyPort": 18791,
        "proxyLogin": "test-user",
        "proxyPassword": "test-password",
    }
    solver.config = lambda: {
        "enabled": True,
        "api_key": "test-key",
        "sites": ["investing"],
        "bindings": {identity.identity_id: binding},
    }
    browser = SimpleNamespace(
        inspect=AsyncMock(
            return_value={
                "url": URL,
                "user_agent": "Real Chrome",
                "website": "https://www.investing.com/",
                "state": "SOLVABLE",
            }
        ),
        reload=AsyncMock(),
        prepare_retry=AsyncMock(),
        apply=AsyncMock(),
    )
    solver.browser_factory = lambda *args: browser
    solver.egress_matches = AsyncMock(return_value=True)
    solver.provider.request = AsyncMock(return_value={"taskId": 123})
    yield solver, value, scope, identity, egress, browser


@pytest.mark.asyncio
@pytest.mark.parametrize("task_id", [123, "123"])
async def test_paid_task_resumes_and_cookie_does_not_mark_business_success(solver_setup, task_id):
    solver, value, scope, identity, egress, browser = solver_setup
    solver.provider.request.return_value = {"taskId": task_id}
    assert await solver.tick(value, scope, identity, egress)
    assert solver.jobs(value.recovery_id)[0]["task_id"] == 123
    solver.provider.request.return_value = {"status": "processing"}
    assert await solver.tick(value, scope, identity, egress)
    assert solver.provider.request.await_args.args[1] == "getTaskResult"
    browser.inspect.return_value["state"] = "RETRY"
    solver.provider.request.return_value = {
        "status": "ready",
        "solution": {"cookie": "datadome=secret"},
        "cost": "0.003",
    }
    assert await solver.tick(value, scope, identity, egress)
    browser.apply.assert_awaited_once()
    saved = solver.jobs(value.recovery_id)[0]
    assert saved["status"] == "APPLIED" and "secret" not in json.dumps(saved)
    assert solver.coordinator.store.get_recovery(value.identity_id).phase != "RESOLVED"
    assert await solver.tick(value, scope, identity, egress)  # Free RETRY preparation after application; no repurchase.
    browser.prepare_retry.assert_awaited_once()
    assert sum(c.args[1] == 'createTask' for c in solver.provider.request.await_args_list) == 1


@pytest.mark.asyncio
async def test_uncertain_create_never_buys_again(solver_setup):
    solver, value, scope, identity, egress, _ = solver_setup
    solver.provider.request.side_effect = SolverError("PROVIDER_TRANSPORT_UNCERTAIN")
    assert not await solver.tick(value, scope, identity, egress)
    assert not await solver.tick(value, scope, identity, egress)
    assert solver.provider.request.await_count == 1


@pytest.mark.asyncio
async def test_waiting_slider_updates_terminal_ip_denial_without_purchase(solver_setup):
    solver,value,scope,identity,egress,browser=solver_setup
    job=solver.new_job(value,{},'route')
    job.update(status='WAITING_SLIDER',reason='DATADOME_INTERSTITIAL')
    solver.save(job)
    browser.inspect.return_value['state']='IP_BANNED'
    assert not await solver.tick(value,scope,identity,egress)
    assert solver.jobs(value.recovery_id)[0]['reason']=='DATADOME_IP_BANNED'
    browser.reload.assert_not_awaited()
    solver.provider.request.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_click_is_durable_and_not_billed_until_real_slider(solver_setup):
    solver, value, scope, identity, egress, browser = solver_setup
    browser.inspect.return_value["state"] = "RETRY"
    assert await solver.tick(value, scope, identity, egress)
    from datetime import timedelta
    from doxagent.site_strategy.schema import utc_now

    stored=solver.coordinator.store.get_recovery(value.identity_id)
    stored.scene_preparation['started_at']=(utc_now()-timedelta(seconds=61)).isoformat()
    solver.coordinator.store.save_recovery(stored,expected_generation=stored.generation)
    assert await solver.tick(value, scope, identity, egress)
    stored=solver.coordinator.store.get_recovery(value.identity_id)
    stored.scene_preparation['started_at']=(utc_now()-timedelta(seconds=61)).isoformat()
    solver.coordinator.store.save_recovery(stored,expected_generation=stored.generation)
    assert not await solver.tick(value,scope,identity,egress)
    assert browser.prepare_retry.await_count == 2
    browser.reload.assert_not_awaited()
    solver.provider.request.assert_not_awaited()
    assert not solver.jobs(value.recovery_id)
    browser.inspect.return_value["state"] = "SOLVABLE"
    assert await solver.tick(value, scope, identity, egress)
    solver.provider.request.assert_awaited_once()


@pytest.mark.asyncio
async def test_unloaded_datadome_bootstrap_gets_one_recovery_refresh(solver_setup):
    solver, value, scope, identity, egress, browser = solver_setup
    browser.inspect.return_value.update(url=None, state="UNAVAILABLE")
    assert await solver.tick(value, scope, identity, egress)
    from datetime import timedelta
    from doxagent.site_strategy.schema import utc_now

    assert not await solver.tick(value, scope, identity, egress)
    browser.reload.assert_awaited_once()
    solver.provider.request.assert_not_awaited()
    assert solver.coordinator.store.get_recovery(value.identity_id).scene_preparation['status']=='BOOTSTRAP_REFRESHED'


@pytest.mark.asyncio
async def test_egress_mismatch_no_purchase(solver_setup):
    solver, value, scope, identity, egress, _ = solver_setup
    solver.egress_matches.return_value = False
    assert not await solver.tick(value, scope, identity, egress)
    solver.provider.request.assert_not_awaited()


@pytest.mark.asyncio
async def test_poll_network_error_reuses_task(solver_setup):
    solver, value, scope, identity, egress, _ = solver_setup
    assert await solver.tick(value, scope, identity, egress)
    solver.provider.request.side_effect = SolverError("PROVIDER_TRANSPORT_UNCERTAIN")
    assert await solver.tick(value, scope, identity, egress)
    assert solver.jobs(value.recovery_id)[0]["status"] == "PENDING"


@pytest.mark.asyncio
async def test_changed_scene_rejects_stale_paid_result(solver_setup):
    solver, value, scope, identity, egress, browser = solver_setup
    assert await solver.tick(value, scope, identity, egress)
    browser.inspect.return_value["url"] += "&new=scene"
    solver.provider.request.return_value = {"status": "ready", "cost": "0.00145", "solution": {"cookie": "secret"}}
    assert not await solver.tick(value, scope, identity, egress)
    browser.apply.assert_not_awaited()
    assert solver.jobs(value.recovery_id)[0]["reason"] == "SCENE_OR_EGRESS_CHANGED"
    assert solver.jobs(value.recovery_id)[0]["provider_cost"] == "0.00145"


@pytest.mark.asyncio
async def test_global_daily_cap_reserves_before_create(solver_setup):
    solver, value, scope, identity, egress, _ = solver_setup
    original = solver.config()
    solver.config = lambda: {**original, "daily_tasks": 0}
    assert not await solver.tick(value, scope, identity, egress)
    solver.provider.request.assert_not_awaited()


@pytest.mark.asyncio
async def test_uncertain_submission_still_consumes_daily_reservation(solver_setup):
    from doxagent.site_strategy.schema import utc_now

    solver, value, scope, identity, egress, _ = solver_setup
    prior = solver.new_job(value.model_copy(update={"recovery_id": "older-incident"}), {}, "route")
    prior.update(status="FAILED", submitted_at=utc_now().isoformat(), reason="SUBMISSION_OUTCOME_UNKNOWN")
    solver.save(prior)
    config = solver.config()
    solver.config = lambda: {**config, "daily_tasks": 1}
    assert not await solver.tick(value, scope, identity, egress)
    solver.provider.request.assert_not_awaited()


def test_native_mutation_rejects_open_viewer(solver_setup):
    solver, value, scope, identity, _, _ = solver_setup
    from doxagent.site_strategy.governance_models import MaintenanceSession

    solver.coordinator.store.save_session(
        MaintenanceSession(
            kind="CHALLENGE",
            scope_id=scope.scope_id,
            site_id="investing",
            identity_id=identity.identity_id,
        )
    )
    with pytest.raises(RuntimeError, match="user_maintenance"):
        NativeChallenge(solver.coordinator, value, identity).check()


def test_ownership_fence_ignores_only_metadata_touch_not_owner_change(solver_setup):
    from doxagent.site_strategy.schema import utc_now

    solver, value, _, identity, _, _ = solver_setup
    browser = NativeChallenge(solver.coordinator, value, identity)
    store = solver.coordinator.store
    store.save_target(browser.target.model_copy(update={"updated_at": utc_now()}))
    browser.check()
    store.save_target(browser.target.model_copy(update={"owner_ref": "different-owner"}))
    with pytest.raises(RuntimeError, match="solver_target_changed"):
        browser.check()


def test_proxy_config_denies_open_relay_and_never_direct():
    path = Path(__file__).parents[1] / "deploy/configure-datadome.py"
    spec = importlib.util.spec_from_file_location("proxy_config", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    config = module.squid_config(
        {"reuters": {"proxyLogin": "dd-user", "egress_id": "egress"}},
        {"egress": ("doxagent-egress-clash", 18080)},
        "172.18.0.0/16",
    )
    assert "never_direct allow all" in config and "ssl_bump" not in config
    assert "http_access deny !allowed" in config and "http_access deny !solver_src" in config
    assert "proxy_auth dd-user" in config and "http_access deny all" in config


@pytest.mark.asyncio
async def test_oopif_retry_is_read_and_child_connection_always_detached():
    from doxagent.site_strategy.datadome_browser import loaded_frame_document

    call = AsyncMock(
        side_effect=[
            {"sessionId": "child"},
            {"root": {"nodeId": 12}},
            {"outerHTML": "<button>Retry</button>"},
            {},
        ]
    )
    html = await loaded_frame_document(call, "exact-frame")
    assert challenge_state(URL, html) == "RETRY"
    assert call.await_args.args == ("Target.detachFromTarget", {"sessionId": "child"})
    assert call.await_args_list[1].kwargs == {"session_id": "child"}


@pytest.mark.asyncio
async def test_existing_public_http_cannot_starve_browser_recovery(setup):
    _, service, _, scope = setup
    identity = service.repository.get_identity(scope.primary_identity_id)
    async with service.profile_use.business(identity.profile_id, browser=False):
        async with service.profile_use.control(identity.profile_id, identity.identity_id):
            assert service.profile_use._active[identity.profile_id] == 2
        assert service.profile_use._active[identity.profile_id] == 1


@pytest.mark.asyncio
async def test_control_reservation_drains_old_page_and_rejects_new_browser(setup):
    import asyncio
    from doxagent.site_strategy.service import ProfileUnavailableError

    _, service, _, scope = setup
    identity = service.repository.get_identity(scope.primary_identity_id)
    started, release = asyncio.Event(), asyncio.Event()

    async def old_work():
        async with service.profile_use.business(identity.profile_id):
            started.set()
            await release.wait()

    async def control():
        async with service.profile_use.control(identity.profile_id, identity.identity_id):
            assert not service.profile_use._browser_active.get(identity.profile_id)

    old = asyncio.create_task(old_work())
    await started.wait()
    next_control = asyncio.create_task(control())
    await asyncio.sleep(0)
    with pytest.raises(ProfileUnavailableError):
        async with service.profile_use.business(identity.profile_id):
            pass
    release.set()
    await asyncio.gather(old, next_control)
    assert not service.profile_use._active.get(identity.profile_id)
    assert identity.profile_id not in service.profile_use._exclusive


@pytest.mark.parametrize(
    "query,offset,ready", [("Micron", 0, True), ("Samsung", 0, False), ("Micron", 20, False)]
)
def test_reuters_recipe_default_offset_is_not_a_false_recovery_failure(setup, query, offset, ready):
    from doxagent.site_strategy.recovery import verify_capture, recovery_context
    from doxagent.site_strategy.runtime import RuntimeResponse
    from doxagent.site_strategy.schema import AccessRequest

    _, service, store, _ = setup
    scope = store.get_scope("source:reuters_site_search")
    context = recovery_context(
        AccessRequest(
            operation_id="test",
            url="https://www.reuters.com/site-search/?query=Micron",
            purpose="CRAWLER",
            mode="BROWSER",
            recipe_ref="builtin:reuters_search@1",
            recipe_parameters={"query": "Micron"},
        )
    )
    capture = RuntimeResponse(
        200,
        f"https://www.reuters.com/site-search/?query={query}&offset={offset}",
        {},
        recipe_result={
            "rows": [{"url": "/technology/micron-earnings-2026-10-22/", "title": "Micron earnings"}]
        },
        provenance=SimpleNamespace(instance_id="chrome"),
    )
    assert (verify_capture(service, scope, context, capture).crawler == "READY") is ready
