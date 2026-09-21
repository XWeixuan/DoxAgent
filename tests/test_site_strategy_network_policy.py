from __future__ import annotations

import asyncio
import inspect
from pathlib import Path

import pytest
import yaml

from doxagent.site_strategy.egress import render_fixed_listeners
from doxagent.site_strategy.repository import SiteStrategyRepository
from doxagent.site_strategy.runtime import (
    PersistentBrowserPool,
    RuntimeResponse,
    SiteAccessRuntime,
    _DocumentNavigationGate,
)
from doxagent.site_strategy.schema import AccessMode, FailureCategory, ProxyEgress
from doxagent.site_strategy.seeds import bootstrap_seed, seed_specs
from doxagent.site_strategy.service import SiteStrategyService, classify_response


def test_http_412_never_becomes_success_without_semantic_evidence() -> None:
    category, reason = classify_response(
        RuntimeResponse(412, "https://www.barrons.com/", {}, "ordinary precondition failure")
    )
    assert category is FailureCategory.UNKNOWN
    assert reason == "http_412_unclassified"


def test_challenge_is_detected_on_200_and_412() -> None:
    page = (
        "<html><title>Access is temporarily restricted</title>"
        "<body>unusual activity from your network</body></html>"
    )
    for status in (200, 412):
        category, reason = classify_response(
            RuntimeResponse(status, "https://www.barrons.com/", {}, page)
        )
        assert category is FailureCategory.ACCESS_CHALLENGE
        assert reason == "challenge_required"


def test_fixed_listener_uses_private_range_rejections_and_fixed_terminal() -> None:
    egress = ProxyEgress(
        egress_id="server-direct",
        node_ref="DIRECT",
        node_fingerprint="digest",
        listener_port=18084,
        endpoint="http://clash:18084",
    )
    value = yaml.safe_load(render_fixed_listeners("{}", [egress]))
    listener = value["listeners"][0]
    assert "proxy" not in listener
    rules = value["sub-rules"][listener["rule"]]
    assert "IP-CIDR,127.0.0.0/8,REJECT" in rules
    assert "IP-CIDR6,fc00::/7,REJECT" in rules
    assert all("no-resolve" not in rule for rule in rules)
    assert rules[-1] == "MATCH,DIRECT"


def test_strict_sites_have_browser_first_and_shared_three_second_budget() -> None:
    specs = {item.site_id: item for item in seed_specs()}
    for site_id in {
        "yahoo_finance",
        "reuters",
        "barrons",
        "wsj",
        "seeking_alpha",
        "marketwatch",
    }:
        spec = specs[site_id]
        assert spec.access.max_concurrency == 1
        assert spec.access.min_interval_ms == 3000
        assert spec.body.access_order[0] == "browser"
    assert specs["reuters"].auth.maintenance_url
    assert specs["reuters"].auth.verification_kind == "public_access"


def test_browser_runtime_has_no_playwright_route_or_identity_override() -> None:
    source = inspect.getsource(SiteAccessRuntime)
    assert ".route(" not in source
    assert "user_agent=" not in source
    assert "extra_http_headers" not in source
    assert "service_workers=" not in source
    assert "no_viewport=True" in inspect.getsource(
        __import__(
            "doxagent.site_strategy.runtime", fromlist=["PersistentBrowserPool"]
        ).PersistentBrowserPool
    )


@pytest.mark.asyncio
async def test_http_success_cannot_clear_browser_manual_attention(tmp_path: Path) -> None:
    repository = SiteStrategyRepository(tmp_path / "site.sqlite3")
    service = SiteStrategyService(repository, profile_root=tmp_path / "profiles")
    bootstrap_seed(repository, service)
    try:
        spec = repository.get_strategy("barrons")
        assert spec is not None
        combination = spec.access.combinations[0]
        state, _, _ = await service.health.candidates(
            "barrons", spec.access.combinations, excluded=set()
        )
        await service.health.mark_failure(
            "barrons",
            combination.combination_id,
            FailureCategory.ACCESS_CHALLENGE,
            "challenge_required",
            assigned_generation=state.generation,
            transport=AccessMode.BROWSER,
        )
        state = repository.get_runtime("barrons")
        await service.health.mark_failure(
            "barrons",
            combination.combination_id,
            FailureCategory.ACCESS_CHALLENGE,
            "challenge_required",
            assigned_generation=state.generation,
            transport=AccessMode.BROWSER,
        )
        state = repository.get_runtime("barrons")
        await service.health.mark_success(
            "barrons",
            combination.combination_id,
            assigned_generation=state.generation,
            transport=AccessMode.HTTP_PUBLIC,
        )

        current = repository.get_runtime("barrons").combinations[combination.combination_id]
        assert current.manual_attention_required is True
        assert current.last_http_result == "SUCCESS"
        _, browser_candidates, _ = await service.health.candidates(
            "barrons",
            spec.access.combinations,
            excluded=set(),
            transport=AccessMode.BROWSER,
        )
        _, http_candidates, _ = await service.health.candidates(
            "barrons",
            spec.access.combinations,
            excluded=set(),
            transport=AccessMode.HTTP_PUBLIC,
        )
        assert combination.combination_id not in {
            item.combination_id for item in browser_candidates
        }
        assert combination.combination_id in {item.combination_id for item in http_candidates}
    finally:
        repository.close()


@pytest.mark.asyncio
async def test_popup_private_document_is_blocked_before_navigation(tmp_path: Path) -> None:
    repository = SiteStrategyRepository(tmp_path / "site.sqlite3")
    service = SiteStrategyService(repository, profile_root=tmp_path / "profiles")
    bootstrap_seed(repository, service)
    profile = repository.get_profile("barrons-1")
    egress = repository.get_egress("us-standard-5")
    assert profile is not None and egress is not None
    pool = PersistentBrowserPool(
        tmp_path / "popup-profiles",
        headless=True,
        channel=None,
        max_processes=1,
        max_pages=1,
        idle_seconds=60,
    )
    lease = await pool.page(profile, egress)
    try:
        async with lease as page:
            gate = _DocumentNavigationGate(
                page,
                service.resolver,
                service.resolve("https://www.barrons.com/"),
                lease.entry.cdp,
            )
            try:
                await gate.start()
                await page.set_content(
                    '<button id="open" onclick="window.open(\'http://127.0.0.1:9/private\')">open</button>'
                )
                await page.locator("#open").click()
                for _ in range(50):
                    if gate.blocked:
                        break
                    await asyncio.sleep(0.02)
                assert gate.blocked
                assert any("127.0.0.1" in value for value in gate.blocked)
            finally:
                await gate.close()
    finally:
        await pool.close()
        repository.close()
