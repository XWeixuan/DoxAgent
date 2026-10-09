from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from doxagent.message_bus_v2.reuters_sources import wait_for_native_document_redirect
from doxagent.site_strategy.repository import SiteStrategyRepository
from doxagent.site_strategy.runtime import RuntimeResponse
from doxagent.site_strategy.schema import AccessMode, AccessRequest, FailureCategory, SitePurpose
from doxagent.site_strategy.seeds import bootstrap_seed
from doxagent.site_strategy.service import SiteStrategyService


@pytest.fixture
def service(tmp_path):
    repo = SiteStrategyRepository(tmp_path / "site.db")
    svc = SiteStrategyService(repo, profile_root=tmp_path / "profiles")
    bootstrap_seed(repo, svc)
    yield svc
    repo.close()


def test_missing_probe_uses_only_registered_public_endpoint(service):
    spec = service.repository.get_strategy("investing")
    spec.access.probe_url = None
    assert service._recovery_probe_url(spec) == spec.auth.verification_url
    spec.auth.verification_url = "https://www.reuters.com/"
    spec.auth.maintenance_url = None
    assert service._recovery_probe_url(spec) is None
    spec.auth.verification_kind = "subscription_article"
    spec.auth.maintenance_url = "https://www.investing.com/"
    assert service._recovery_probe_url(spec) is None


@pytest.mark.asyncio
async def test_uncaught_probe_failure_releases_claim_and_other_sites_progress(service, monkeypatch):
    specs = [service.repository.get_strategy(s) for s in ("investing", "reuters")]
    expired = datetime.now(UTC) - timedelta(minutes=2)
    for spec in specs:
        combo = spec.access.combinations[0]
        await service.health.mark_failure(
            spec.site_id,
            combo.combination_id,
            FailureCategory.ACCESS_RATE_LIMIT,
            "http_429",
            assigned_generation=0,
            transport=AccessMode.BROWSER,
        )
        state = service.repository.get_runtime(spec.site_id)
        state.combinations[combo.combination_id].cooldown_until = expired
        service.repository.save_runtime(state, expected_generation=state.generation)
    monkeypatch.setattr(service.repository, "list_egresses", lambda: [])
    monkeypatch.setattr(
        service.repository,
        "list_active_strategies",
        lambda: [(SimpleNamespace(enabled=True), spec) for spec in specs],
    )
    called = []

    async def probe(spec, combo, generation):
        called.append(spec.site_id)
        if spec.site_id == "investing":
            raise RuntimeError("unexpected probe failure")
        await service.health.mark_success(
            spec.site_id,
            combo.combination_id,
            assigned_generation=generation,
            transport=AccessMode.BROWSER,
        )

    monkeypatch.setattr(service, "_probe_combination", probe)
    await service._run_due_probes()
    assert called == ["investing", "reuters"]
    state = service.repository.get_runtime("investing").combinations[
        specs[0].access.combinations[0].combination_id
    ]
    assert state.state == "COOLDOWN" and not state.probe_in_flight
    assert not state.manual_attention_required


@pytest.mark.asyncio
async def test_identity_reference_profile_probe_materializes_and_uses_joint_budget(
    service, monkeypatch
):
    spec = service.repository.get_strategy("investing")
    combo = spec.access.combinations[0]
    identity = service._identity_for(combo, service.repository.get_profile(combo.profile_id))
    legacy_profile = service.repository.get_profile(identity.profile_id)
    shared_profile = legacy_profile.model_copy(
        update={
            "profile_id": "shared-public",
            "directory_key": "shared-public",
            "site_id": "investorshub",
        }
    )
    service.repository.save_profile(shared_profile)
    identity = service.repository.apply_identity(
        identity.model_copy(
            update={
                "identity_id": "shared-public",
                "profile_id": "shared-public",
                "revision": 0,
            }
        ),
        actor="test",
        expected_revision=None,
    )
    spec.access.combinations[0] = combo.model_copy(
        update={
            "identity_id": identity.identity_id,
            "profile_id": None,
            "egress_id": None,
        }
    )
    service.repository.apply_strategy(spec, actor="test", expected_revision=spec.revision)
    seen = []

    @asynccontextmanager
    async def permit(*args, **kwargs):
        seen.append(kwargs)
        yield 0

    monkeypatch.setattr(service.budgets.joint, "permit", permit)
    monkeypatch.setattr(
        service.runtime,
        "execute",
        AsyncMock(
            return_value=RuntimeResponse(
                200,
                "https://www.investing.com/equities/intel-corp-news",
                {},
                "<article>News</article>",
            )
        ),
    )
    result = await service.probe_profile(
        identity.profile_id, "https://www.investing.com/equities/intel-corp-news"
    )
    assert result.status_code == 200
    assert seen[0]["identity_id"] == identity.identity_id
    with pytest.raises(ValueError, match="not referenced"):
        await service.probe_profile(identity.profile_id, "https://www.reuters.com/")


@pytest.mark.asyncio
async def test_native_redirect_only_accepts_real_main_document_response():
    initial = SimpleNamespace(status=401)
    main = object()
    final = SimpleNamespace(
        status=200, frame=main, request=SimpleNamespace(is_navigation_request=lambda: True)
    )
    page = SimpleNamespace(
        content=AsyncMock(return_value="Please enable JS https://geo.captcha-delivery.com/"),
        main_frame=main,
        wait_for_event=AsyncMock(return_value=final),
    )
    assert await wait_for_native_document_redirect(page, initial) is final
    predicate = page.wait_for_event.call_args.kwargs["predicate"]
    assert predicate(final)
    assert not predicate(SimpleNamespace(status=200, frame=object(), request=final.request))
    page.wait_for_event.side_effect = TimeoutError()
    assert await wait_for_native_document_redirect(page, initial) is initial


@pytest.mark.asyncio
async def test_static_denial_does_not_wait_reload_or_claim_success():
    initial = SimpleNamespace(status=403)
    page = SimpleNamespace(
        content=AsyncMock(return_value="Access Denied"), wait_for_event=AsyncMock()
    )
    assert await wait_for_native_document_redirect(page, initial) is initial
    page.wait_for_event.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("success", [True, False])
async def test_human_verification_releases_health_only_on_real_success(
    service, monkeypatch, success
):
    spec = service.repository.get_strategy("reuters")
    combo = spec.access.combinations[0]
    profile = service.repository.get_profile(combo.profile_id)
    identity = service._identity_for(combo, profile)
    for _ in range(2):
        generation = service.repository.get_runtime("reuters").generation
        await service.health.mark_failure(
            "reuters",
            combo.combination_id,
            FailureCategory.ACCESS_CHALLENGE,
            "challenge_required",
            assigned_generation=generation,
            transport=AccessMode.BROWSER,
        )
    assert (
        service.repository.get_runtime("reuters")
        .combinations[combo.combination_id]
        .manual_attention_required
    )
    token = await service.profile_use.begin_maintenance(profile.profile_id)
    monkeypatch.setattr(
        service.runtime,
        "verify_login",
        AsyncMock(
            return_value=RuntimeResponse(
                200 if success else 401,
                spec.auth.verification_url,
                {},
                "<html><body>Search results for Micron</body></html>"
                if success
                else "<html><title>Verify you are human</title></html>",
            )
        ),
    )
    try:
        _, reason = await service.verify_identity(
            "reuters", identity.identity_id, spec.auth.verification_url, token
        )
    finally:
        await service.profile_use.end_maintenance(profile.profile_id, token)
    state = service.repository.get_runtime("reuters").combinations[combo.combination_id]
    if success:
        assert reason == "public_access_ready"
        assert (
            state.state == "READY"
            and not state.manual_attention_required
            and state.risk_strikes == 0
        )
    else:
        assert state.manual_attention_required and state.risk_strikes == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.investing.com/equities/intel-corp-news", '[data-test="article-item"]'),
        (
            "https://www.investing.com/news/stock-market-news/example-123",
            '#article, [data-test="article-content"]',
        ),
        ("https://investorshub.advfn.com/stock-market/NASDAQ/intc/news", ".quote-news-item"),
    ],
)
async def test_public_probe_waits_for_correct_page_surface(service, monkeypatch, url, expected):
    resolved = service.resolve(url)
    combo = resolved.access.combinations[0]
    profile = service.repository.get_profile(combo.profile_id)
    identity = service._identity_for(combo, profile)
    egress = service.repository.get_egress(combo.egress_id)
    seen = []
    locator = SimpleNamespace(first=SimpleNamespace(wait_for=AsyncMock()))

    def locate(selector):
        seen.append(selector)
        return locator

    page = SimpleNamespace(
        url=url,
        goto=AsyncMock(
            return_value=SimpleNamespace(status=200, all_headers=AsyncMock(return_value={}))
        ),
        content=AsyncMock(return_value="<html>news</html>"),
        locator=locate,
    )

    class Lease:
        provenance = SimpleNamespace(instance_id="test-instance")
        browser_cdp = None

        async def __aenter__(self):
            return page

        async def __aexit__(self, *_args):
            pass

    monkeypatch.setattr(service.runtime.browser_runtimes, "page", AsyncMock(return_value=Lease()))
    monkeypatch.setattr("doxagent.site_strategy.runtime.public_url", AsyncMock())
    monkeypatch.setattr(
        "doxagent.site_strategy.runtime._DocumentNavigationGate",
        lambda *args: SimpleNamespace(
            start=AsyncMock(),
            close=AsyncMock(),
            blocked=None,
        ),
    )
    response = await service.runtime._browser(
        AccessRequest(
            operation_id="test-probe",
            purpose=SitePurpose.PROBE,
            mode=AccessMode.BROWSER,
            url=url,
        ),
        resolved,
        identity,
        egress,
    )
    assert response.status_code == 200
    assert seen == [expected]
