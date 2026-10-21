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
    cookie = solution_cookie(
        "datadome=abc; Domain=.reuters.com; Secure; Path=/", "https://www.reuters.com/"
    )
    assert cookie["domain"] == "reuters.com" and cookie["value"] == "abc"
    assert captcha_url(f'<iframe src="{URL}"/>') == URL
    assert captcha_url('<iframe src="https://captcha-delivery.com.evil.com/?t=fe"/>') is None
    assert challenge_state(URL.replace("fe", "bv"), "") == "IP_BANNED"
    assert challenge_state(URL, "<script>retry()</script>") == "SOLVABLE"
    assert challenge_state(URL, "<button>Retry</button>") == "RETRY"
    assert challenge_state(URL.replace("/captcha/", "/interstitial/"), "") == "UNSUPPORTED"


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
        apply=AsyncMock(),
    )
    solver.browser_factory = lambda *args: browser
    solver.egress_matches = AsyncMock(return_value=True)
    solver.provider.request = AsyncMock(return_value={"taskId": 123})
    yield solver, value, scope, identity, egress, browser


@pytest.mark.asyncio
async def test_paid_task_resumes_and_cookie_does_not_mark_business_success(solver_setup):
    solver, value, scope, identity, egress, browser = solver_setup
    assert await solver.tick(value, scope, identity, egress)
    assert solver.jobs(value.recovery_id)[0]["task_id"] == 123
    solver.provider.request.return_value = {"status": "processing"}
    assert await solver.tick(value, scope, identity, egress)
    assert solver.provider.request.await_args.args[1] == "getTaskResult"
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
    assert not await solver.tick(value, scope, identity, egress)


@pytest.mark.asyncio
async def test_uncertain_create_never_buys_again(solver_setup):
    solver, value, scope, identity, egress, _ = solver_setup
    solver.provider.request.side_effect = SolverError("PROVIDER_TRANSPORT_UNCERTAIN")
    assert not await solver.tick(value, scope, identity, egress)
    assert not await solver.tick(value, scope, identity, egress)
    assert solver.provider.request.await_count == 1


@pytest.mark.asyncio
async def test_retry_reload_is_bounded_and_not_billed_until_real_slider(solver_setup):
    solver, value, scope, identity, egress, browser = solver_setup
    browser.inspect.return_value["state"] = "RETRY"
    assert await solver.tick(value, scope, identity, egress)
    assert not await solver.tick(value, scope, identity, egress)
    browser.reload.assert_awaited_once()
    solver.provider.request.assert_not_awaited()
    assert solver.jobs(value.recovery_id)[0]["reason"] == "DATADOME_RETRY"


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
    assert not await solver.tick(value, scope, identity, egress)
    browser.apply.assert_not_awaited()
    assert solver.jobs(value.recovery_id)[0]["reason"] == "SCENE_OR_EGRESS_CHANGED"


@pytest.mark.asyncio
async def test_global_daily_cap_reserves_before_create(solver_setup):
    solver, value, scope, identity, egress, _ = solver_setup
    original = solver.config()
    solver.config = lambda: {**original, "daily_tasks": 0}
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
