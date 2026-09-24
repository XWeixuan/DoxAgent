from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient

from doxagent.content_enrichment.extractor import SharedContentExtractor
from doxagent.content_enrichment.quality import choose_candidate, inspect_html
from doxagent.monitoring.media_enrichment import MediaEnrichmentRecord
from doxagent.site_strategy.api import create_app
from doxagent.site_strategy.client import SiteAccessClient
from doxagent.site_strategy.egress import probe_egress, render_fixed_listeners
from doxagent.site_strategy.identity_rollout import apply_first_wave
from doxagent.site_strategy.repository import SiteStrategyRepository
from doxagent.site_strategy.runtime import RuntimeResponse
from doxagent.site_strategy.schema import (
    AccessDisposition,
    AccessMode,
    AccessRequest,
    BrowserProfile,
    BrowserIdentityLifecycle,
    FailureCategory,
    ProxyEgress,
    SitePurpose,
)
from doxagent.site_strategy.seeds import bootstrap_seed
from doxagent.site_strategy.service import SiteStrategyService


def test_browser_profile_preserves_legacy_challenge_markers() -> None:
    profile = BrowserProfile.model_validate(
        {
            "profile_id": "dowjones-main",
            "site_id": "barrons",
            "bound_egress_id": "us-standard-5",
            "directory_key": "dowjones-main",
            "challenge_url": None,
            "challenge_site_id": None,
            "challenge_identity_id": None,
            "challenge_combination_id": None,
            "challenge_target_id": None,
            "challenge_verified": False,
        }
    )
    assert "challenge_verified" in profile.model_dump()
    assert BrowserProfile.model_validate_json(profile.model_dump_json()) == profile


def test_browser_identity_lifecycle_preserves_legacy_context_page_limit() -> None:
    lifecycle = BrowserIdentityLifecycle.model_validate(
        {"residency": "on_demand", "idle_seconds": 43_200, "max_context_pages": 200}
    )
    assert lifecycle.max_context_pages == 200
    assert BrowserIdentityLifecycle.model_validate_json(lifecycle.model_dump_json()) == lifecycle


@pytest.fixture
def site_service(tmp_path: Path) -> SiteStrategyService:
    repository = SiteStrategyRepository(tmp_path / "site.sqlite3")
    service = SiteStrategyService(repository, profile_root=tmp_path / "profiles")
    bootstrap_seed(repository, service)
    yield service
    repository.close()


@pytest.mark.asyncio
async def test_egress_probe_requires_two_failures_before_withdrawing_ready_node(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = SiteStrategyRepository(tmp_path / "egress.sqlite3")
    repository.upsert_egress(
        ProxyEgress(
            egress_id="stable-node",
            node_ref="node-a",
            node_fingerprint="digest",
            listener_port=18080,
            endpoint="http://clash:18080",
            status="READY",
            observed_ip="203.0.113.8",
            observed_at=datetime(2026, 9, 22, tzinfo=UTC),
            probe_endpoint="https://probe.test/ip",
        )
    )

    class FailingClient:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def __aenter__(self) -> FailingClient:
            return self

        async def __aexit__(self, *_args: object) -> None:
            pass

        async def get(self, _url: str) -> object:
            raise httpx.ConnectError("transient probe failure")

    monkeypatch.setattr("doxagent.site_strategy.egress.httpx.AsyncClient", FailingClient)
    try:
        first = await probe_egress(repository, "stable-node")
        assert first.status == "READY"
        assert first.observed_ip == "203.0.113.8"
        assert first.consecutive_probe_failures == 1
        assert first.last_probe_error_at is not None

        second = await probe_egress(repository, "stable-node")
        assert second.status == "UNAVAILABLE"
        assert second.observed_ip == "203.0.113.8"
        assert second.consecutive_probe_failures == 2

        class SuccessfulResponse:
            def raise_for_status(self) -> None:
                pass

            def json(self) -> dict[str, str]:
                return {"ip": "203.0.113.8"}

        class SuccessfulClient(FailingClient):
            async def get(self, _url: str) -> SuccessfulResponse:
                return SuccessfulResponse()

        monkeypatch.setattr(
            "doxagent.site_strategy.egress.httpx.AsyncClient", SuccessfulClient
        )
        recovered = await probe_egress(repository, "stable-node")
        assert recovered.status == "READY"
        assert recovered.consecutive_probe_failures == 0
        assert recovered.last_probe_error_at is None
    finally:
        repository.close()


def test_seed_resolver_uses_exact_ownership_and_isolated_generic_runtime(
    site_service: SiteStrategyService,
) -> None:
    yahoo = site_service.resolve("https://finance.yahoo.com/quote/MU/latest-news/")
    barrons = site_service.resolve("https://www.barrons.com/articles/example")
    unknown_a = site_service.resolve("https://a.example.test/story")
    unknown_b = site_service.resolve("https://b.example.test/story")

    assert yahoo.site_id == "yahoo_finance"
    assert yahoo.crawler and yahoo.crawler.ref == "builtin:yahoo_page@1"
    assert barrons.site_id == "barrons"
    assert barrons.auth.requirement == "required"
    assert unknown_a.site_id == "generic"
    assert unknown_a.runtime_key != unknown_b.runtime_key


def test_seed_adds_verification_urls_and_idempotently_upgrades_missing_value(
    site_service: SiteStrategyService,
) -> None:
    barrons = site_service.repository.get_strategy("barrons")
    assert barrons is not None
    assert barrons.auth.verification_url
    assert site_service.resolve(barrons.auth.verification_url).site_id == "barrons"

    without_url = barrons.model_copy(
        update={"auth": barrons.auth.model_copy(update={"verification_url": None})}
    )
    missing = site_service.apply_strategy(
        without_url, expected_revision=barrons.revision, actor="test:remove-verification"
    )
    bootstrap_seed(site_service.repository, site_service)
    upgraded = site_service.repository.get_strategy("barrons")
    assert upgraded is not None
    assert upgraded.revision == missing.revision + 1
    assert upgraded.auth.verification_url
    assert (
        site_service.repository.list_revisions("barrons")[0]["actor"]
        == "seed:browser-environment-v2"
    )

    bootstrap_seed(site_service.repository, site_service)
    unchanged = site_service.repository.get_strategy("barrons")
    assert unchanged is not None
    assert unchanged.revision == upgraded.revision


def test_barrons_crawler_and_body_share_dow_jones_identity_policy(
    site_service: SiteStrategyService,
) -> None:
    repository = site_service.repository
    current = repository.get_strategy("barrons")
    assert current is not None
    old = site_service.apply_strategy(
        current.model_copy(
            update={
                "auth": current.auth.model_copy(update={"crawler_requirement": "none"}),
                "access": current.access.model_copy(update={"overrides": {}}),
            }
        ),
        expected_revision=current.revision,
        actor="test:legacy-crawler-policy",
    )
    bootstrap_seed(repository, site_service)
    upgraded = repository.get_strategy("barrons")
    assert upgraded is not None and upgraded.revision == old.revision + 1
    assert upgraded.auth.crawler_requirement == "required"
    assert upgraded.access.overrides["crawler"] == upgraded.access.overrides["body"]
    assert upgraded.access.overrides["body"] == ["barrons-1", "barrons-2"]
    apply_first_wave(repository)
    shared = site_service.resolve("https://www.barrons.com/articles/example")
    assert [item.identity_id for item in shared.access.combinations[:2]] == [
        "dowjones-main",
        "dowjones-backup",
    ]
    assert repository.get_identity("dowjones-main").profile_id == "dowjones-main"
    rolled_out_revision = repository.get_strategy("barrons").revision
    assert [c.combination_id for c in site_service._purpose_combinations(
        shared, SitePurpose.CRAWLER
    )] == [c.combination_id for c in site_service._purpose_combinations(
        shared, SitePurpose.BODY
    )]
    bootstrap_seed(repository, site_service)
    assert repository.get_strategy("barrons").revision == rolled_out_revision


def test_seed_does_not_override_registered_verification_url(
    site_service: SiteStrategyService,
) -> None:
    wsj = site_service.repository.get_strategy("wsj")
    assert wsj is not None
    custom_url = "https://www.wsj.com/articles/operator-selected-verification"
    customized = site_service.apply_strategy(
        wsj.model_copy(
            update={"auth": wsj.auth.model_copy(update={"verification_url": custom_url})}
        ),
        expected_revision=wsj.revision,
        actor="operator",
    )
    bootstrap_seed(site_service.repository, site_service)
    current = site_service.repository.get_strategy("wsj")
    assert current is not None
    assert current.revision == customized.revision
    assert current.auth.verification_url == custom_url


def test_strategy_apply_is_revision_cas_and_profile_binding_is_immutable(
    site_service: SiteStrategyService,
) -> None:
    current = site_service.repository.get_strategy("reuters")
    assert current is not None
    updated = site_service.apply_strategy(
        current.model_copy(update={"display_name": "Reuters News"}),
        expected_revision=current.revision,
        actor="test",
    )
    assert updated.revision == current.revision + 1
    with pytest.raises(RuntimeError, match="revision conflict"):
        site_service.apply_strategy(current, expected_revision=current.revision, actor="stale")

    profile = site_service.repository.get_profile("reuters-1")
    assert profile is not None
    with pytest.raises(ValueError, match="immutable"):
        site_service.repository.save_profile(
            profile.model_copy(update={"bound_egress_id": "us-standard-5"})
        )


@pytest.mark.asyncio
async def test_single_http_429_switches_candidate_without_freezing_browser_identity(
    site_service: SiteStrategyService,
) -> None:
    calls: list[str] = []

    async def execute_runtime(request, resolved, combination, profile, egress):
        del request, resolved, profile, egress
        calls.append(combination.combination_id)
        if len(calls) == 1:
            return RuntimeResponse(429, "https://finance.yahoo.com/story", {}, "rate limited")
        return RuntimeResponse(
            200,
            "https://finance.yahoo.com/story",
            {"content-type": "text/html"},
            "<html><article><h1>Story</h1><p>Body.</p></article></html>",
        )

    site_service.runtime.execute = execute_runtime  # type: ignore[method-assign]
    result = await site_service.execute(
        AccessRequest(
            operation_id="poll-1",
            purpose=SitePurpose.CRAWLER,
            url="https://finance.yahoo.com/story",
            mode=AccessMode.HTTP_PUBLIC,
        )
    )

    assert result.disposition is AccessDisposition.SUCCESS
    assert calls == ["yahoo_finance-1", "yahoo_finance-2"]
    state = site_service.repository.get_runtime("yahoo_finance")
    assert state.active_combination_id == "yahoo_finance-2"
    assert state.combinations["yahoo_finance-1"].state == "READY"
    assert state.combinations["yahoo_finance-1"].http_state == "COOLDOWN"


@pytest.mark.asyncio
async def test_content_404_does_not_switch_egress(site_service: SiteStrategyService) -> None:
    calls: list[str] = []

    async def execute_runtime(request, resolved, combination, profile, egress):
        del request, resolved, profile, egress
        calls.append(combination.combination_id)
        return RuntimeResponse(404, "https://finance.yahoo.com/missing", {}, "missing")

    site_service.runtime.execute = execute_runtime  # type: ignore[method-assign]
    result = await site_service.execute(
        AccessRequest(
            operation_id="body-404",
            purpose=SitePurpose.BODY,
            url="https://finance.yahoo.com/missing",
            mode=AccessMode.HTTP_PUBLIC,
        )
    )

    assert result.disposition is AccessDisposition.CONTENT_ERROR
    assert calls == ["yahoo_finance-1"]
    state = site_service.repository.get_runtime("yahoo_finance")
    assert state.combinations["yahoo_finance-1"].state == "READY"


@pytest.mark.asyncio
async def test_admin_profile_probe_targets_exact_profile_without_auth_mutation(
    site_service: SiteStrategyService,
) -> None:
    calls: list[tuple[str, str]] = []

    async def execute_runtime(request, resolved, combination, profile, egress):
        del request, resolved, combination
        calls.append((profile.profile_id, egress.egress_id))
        return RuntimeResponse(
            200,
            "https://www.barrons.com/",
            {"content-type": "text/html"},
            "<html><title>Barron's</title><main>Public home page.</main></html>",
        )

    before = site_service.repository.get_profile("barrons-1")
    assert before is not None
    site_service.runtime.execute = execute_runtime  # type: ignore[method-assign]
    result = await site_service.probe_profile("barrons-1", "https://www.barrons.com/")

    assert result.disposition is AccessDisposition.SUCCESS
    assert calls == [("barrons-1", "us-standard-5")]
    after = site_service.repository.get_profile("barrons-1")
    assert after is not None
    assert after.auth_state is before.auth_state


def test_api_separates_worker_and_admin_tokens(site_service: SiteStrategyService) -> None:
    app = create_app(
        site_service,
        worker_token="worker-secret",
        admin_token="admin-secret",
    )
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/v1/sites").status_code == 401
        assert (
            client.get(
                "/v1/resolve",
                params={"url": "https://www.reuters.com/world/test"},
                headers={"Authorization": "Bearer worker-secret"},
            ).json()["site_id"]
            == "reuters"
        )
        assert (
            client.get("/v1/sites", headers={"Authorization": "Bearer admin-secret"}).status_code
            == 200
        )


@pytest.mark.asyncio
async def test_expired_cooldown_requires_half_open_probe_and_does_not_steal_active(
    site_service: SiteStrategyService,
) -> None:
    spec = site_service.repository.get_strategy("yahoo_finance")
    assert spec is not None
    state, candidates, _ = await site_service.health.candidates(
        "yahoo_finance", spec.access.combinations, excluded=set()
    )
    first = candidates[0]
    await site_service.health.mark_failure(
        "yahoo_finance",
        first.combination_id,
        FailureCategory.ACCESS_RATE_LIMIT,
        "http_429",
        assigned_generation=state.generation,
    )
    await site_service.health.candidates("yahoo_finance", spec.access.combinations, excluded=set())
    runtime = site_service.repository.get_runtime("yahoo_finance")
    second_id = runtime.active_combination_id
    assert second_id and second_id != first.combination_id
    runtime.combinations[first.combination_id].cooldown_until = datetime.now(UTC) - timedelta(
        seconds=1
    )
    site_service.repository.save_runtime(runtime, expected_generation=runtime.generation)

    _, ready, _ = await site_service.health.candidates(
        "yahoo_finance", spec.access.combinations, excluded=set()
    )
    assert first.combination_id not in {item.combination_id for item in ready}
    claimed = await site_service.health.claim_due_probe("yahoo_finance", spec.access.combinations)
    assert claimed is not None
    combination, generation = claimed
    assert combination.combination_id == first.combination_id
    await site_service.health.mark_success(
        "yahoo_finance", combination.combination_id, assigned_generation=generation
    )
    assert site_service.repository.get_runtime("yahoo_finance").active_combination_id == second_id


@pytest.mark.asyncio
async def test_profile_verification_uses_bound_browser_and_real_article(
    site_service: SiteStrategyService,
) -> None:
    async def open_login(profile, egress, url, *, token=None):
        assert profile.bound_egress_id == egress.egress_id
        assert url
        return {"login_token": token, "url": url}

    async def verify_login(token, article_url):
        assert token
        assert article_url.endswith("/example")
        return RuntimeResponse(
            200,
            "https://www.barrons.com/articles/example",
            {"content-type": "text/html"},
            "<html><h1>Example</h1><article><p>Subscriber article text.</p></article></html>",
        )

    site_service.runtime.open_login = open_login  # type: ignore[method-assign]
    site_service.runtime.verify_login = verify_login  # type: ignore[method-assign]
    session = await site_service.open_profile_login("barrons-1")
    profile, reason = await site_service.verify_profile(
        "barrons-1",
        "https://www.barrons.com/articles/example",
        session["login_token"],
    )

    assert profile.auth_state.value == "VALID"
    assert profile.session_revision == 1
    assert reason == "ok"


def test_mihomo_render_replaces_only_managed_fixed_listener() -> None:
    source = yaml.safe_dump(
        {
            "mixed-port": 7893,
            "listeners": [
                {"name": "unrelated", "type": "http", "port": 19000, "proxy": "A"},
                {
                    "name": "doxagent-jp-standard-6",
                    "type": "http",
                    "port": 17000,
                    "proxy": "old",
                },
            ],
        }
    )
    rendered = render_fixed_listeners(
        source,
        [
            ProxyEgress(
                egress_id="jp-standard-6",
                node_ref="node-new",
                node_fingerprint="digest",
                listener_port=18080,
                endpoint="http://clash:18080",
            )
        ],
    )
    config = yaml.safe_load(rendered)
    assert config["mixed-port"] == 7893
    assert {item["name"] for item in config["listeners"]} == {
        "unrelated",
        "doxagent-jp-standard-6",
    }
    managed = next(item for item in config["listeners"] if item["name"] == "doxagent-jp-standard-6")
    assert managed["port"] == 18080
    assert "proxy" not in managed
    assert managed["rule"] == "doxagent-egress-jp-standard-6"
    assert config["sub-rules"][managed["rule"]][-1] == "MATCH,node-new"


def test_registry_strategy_parameters_select_precise_marketwatch_body() -> None:
    html = """
    <html><h1>MarketWatch Example</h1><article>
      <div class="article__body"><p>This is the actual article sentence.</p>
      <p>It is deliberately structured, contains enough concrete reporting detail to pass the
      short-article floor, excludes surrounding recommendations, and ends cleanly.</p></div>
      <div class="recommended"><p>Recommended unrelated story.</p></div>
    </article></html>
    """
    info = inspect_html(
        html,
        "https://www.marketwatch.com/story/example",
        "MarketWatch Example",
        strategy_ref="builtin:marketwatch@1",
        strategy_parameters={
            "body_xpath": ['//article//*[contains(@class,"article__body")]'],
            "remove_xpath": ['//*[contains(@class,"recommended")]'],
        },
    )
    candidate, outcome, _ = choose_candidate(info, "MarketWatch Example")
    assert outcome == "SHORT_FULL"
    assert candidate is not None
    assert "Recommended" not in candidate.text


@pytest.mark.asyncio
async def test_body_v22_uses_registry_strategy_and_records_access_provenance(
    site_service: SiteStrategyService,
) -> None:
    body = " ".join(
        f"Sentence {index} contains material reporting detail about the company."
        for index in range(30)
    )

    async def execute_runtime(request, resolved, combination, profile, egress):
        del request, resolved, combination, profile, egress
        return RuntimeResponse(
            200,
            "https://finance.yahoo.com/news/example.html",
            {"content-type": "text/html"},
            f"<html><h1>Example Company Report</h1><article><p>{body}</p></article></html>",
        )

    site_service.runtime.execute = execute_runtime  # type: ignore[method-assign]
    client = SiteAccessClient(service=site_service)
    extractor = SharedContentExtractor(
        pipeline_enabled=True,
        site_access_client=client,
    )
    try:
        result = await extractor.extract_version(
            MediaEnrichmentRecord(
                standard_message_id="std-1",
                raw_message_id="raw-1",
                source_id="yahoo",
                ticker="MU",
                title="Example Company Report",
                body=None,
                url="https://finance.yahoo.com/news/example.html",
            ),
            "body_v2.2",
        )
    finally:
        await extractor.close()

    assert result.succeeded
    assert result.diagnostics["pipeline_version"] == "body_v2.2"
    assert result.diagnostics["site_id"] == "yahoo_finance"
    assert result.diagnostics["strategy_ref"] == "builtin:yahoo@1"
    trace = result.diagnostics["site_access_trace"]
    assert trace[-1]["combination_id"] == "yahoo_finance-1"
