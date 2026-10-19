"""Investing public-list recognition and proof-based lost-task recovery."""

from types import SimpleNamespace

import pytest

from doxagent.content_enrichment import quality
from doxagent.site_strategy.governance_models import (
    ChallengeTask,
    MaintenanceSession,
    OwnedBrowserTarget,
)
from doxagent.site_strategy.governance_repository import GovernanceRepository
from doxagent.site_strategy.isolation_migration import apply_migration, migration_plan
from doxagent.site_strategy.repository import SiteStrategyRepository
from doxagent.site_strategy.schema import CombinationRuntime
from doxagent.site_strategy.seeds import bootstrap_seed
from doxagent.site_strategy.service import SiteStrategyService
from doxagent.site_strategy.verification import VerificationReport, body_verification_url

LISTING = """<html><title>Intel News Today (INTC) - Investing.com</title>
<main><h1>Intel Corporation (INTC)</h1><h2>INTC News</h2>
<article data-test="article-item"><a data-test="article-title-link"
href="/news/stock-market-news/intel-12345">Intel production update</a>
<time datetime="2026-10-19 19:17:49"/><p>News summary.</p></article></main>
<div role="dialog">Sign in to Investing.com</div>
<script src="/cdn-cgi/challenge-platform/scripts/jsd/main.js"></script></html>"""
URL = "https://www.investing.com/equities/intel-corp-news"


def test_public_news_list_with_optional_signin_is_not_article_or_challenge(monkeypatch):
    def unexpected():
        raise AssertionError("listing must not run generic body extraction")

    monkeypatch.setattr(quality, "_default_extractor", unexpected)
    info = quality.inspect_html(LISTING, URL, "")
    assert info.page_kind == "listing" and info.access_reason is None
    assert info.candidates == []


@pytest.mark.parametrize(
    "markup",
    [
        "<title>Verify you are human</title><script>_cf_chl_opt={}</script>"
        '<script src="/cdn-cgi/challenge-platform/main.js"></script>',
        "<title>Access is temporarily restricted</title>",
    ],
)
def test_actual_challenge_is_not_a_normal_listing(markup):
    info = quality.inspect_html(markup, URL, "", generic_extraction=False)
    assert info.access_reason == "challenge_required"


def test_body_verification_excludes_other_ticker_lists_too():
    article = "https://www.investing.com/news/stock-market-news/intel-12345"
    scope = SimpleNamespace(
        verification_plan={
            "crawler": {"url": "https://www.investing.com/equities/micron-tech-news"}
        }
    )
    assert body_verification_url(scope, URL, article) == article
    assert body_verification_url(scope, URL) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "markup,kind", [(LISTING, "listing"), ("<title>Verify you are human</title>", "challenge")]
)
async def test_native_document_capture_observes_dom_without_evaluating_javascript(markup, kind):
    from doxagent.site_strategy.investing_document import capture_document

    calls = []

    class CDP:
        async def send(self, method, params, **kwargs):
            calls.append(method)
            if method == "Target.attachToTarget":
                assert params["targetId"] == "owned"
                return {"sessionId": "observer"}
            if method == "DOM.getDocument":
                return {"root": {"nodeId": 1, "documentURL": URL}}
            if method == "DOM.getOuterHTML":
                return {"outerHTML": markup}
            assert method == "Target.detachFromTarget"
            return {}

    html, url = await capture_document(CDP(), "owned", URL, timeout=1)
    assert quality.inspect_html(html, url, "", generic_extraction=False).page_kind == kind
    assert calls == [
        "Target.attachToTarget",
        "DOM.getDocument",
        "DOM.getOuterHTML",
        "Target.detachFromTarget",
    ]


@pytest.mark.asyncio
async def test_verify_reuses_exact_live_configured_listing_and_still_checks_real_body(tmp_path):
    from unittest.mock import AsyncMock

    from doxagent.site_strategy.runtime import RuntimeResponse

    repo = SiteStrategyRepository(tmp_path / "verify.db")
    service = SiteStrategyService(repo, profile_root=tmp_path / "profiles")
    try:
        bootstrap_seed(repo, service)
        plan = migration_plan(repo)
        apply_migration(
            repo,
            plan,
            expected_revision=plan["expected_revision"],
            backup_path=tmp_path / "before.db",
        )
        store = GovernanceRepository(repo)
        scope = store.get_scope("source:investing_ticker_news")
        assert scope.verification_plan["crawler"]["url"] == URL
        session = store.save_session(
            MaintenanceSession(
                kind="LOGIN",
                scope_id=scope.scope_id,
                site_id="investing",
                identity_id=scope.primary_identity_id,
                browser_instance_id="chrome",
                control_token="test",
            )
        )
        original = RuntimeResponse(
            0,
            URL,
            {},
            LISTING,
            provenance=SimpleNamespace(instance_id="chrome"),
            challenge_cleared=True,
        )
        service.runtime.inspect_challenge = AsyncMock(return_value=original)
        service.runtime.verify_crawler = AsyncMock(side_effect=AssertionError("duplicate listing"))
        text = (
            "<h1>Intel production update</h1><article>"
            + "<p>Intel production continues to expand and improve.</p>" * 15
            + "</article>"
        )
        service.runtime.verify_login = AsyncMock(
            return_value=RuntimeResponse(
                200,
                "https://www.investing.com/news/stock-market-news/intel-12345",
                {},
                text,
                provenance=SimpleNamespace(instance_id="chrome"),
            )
        )
        report = await service.verify_scope_session(session)
        assert report.crawler == "READY" and report.body in {"FULL", "SHORT_FULL"}
        service.runtime.verify_crawler.assert_not_awaited()
        service.runtime.verify_login.assert_awaited_once()
        assert service.runtime.verify_login.await_args.args[1].endswith("/intel-12345")
    finally:
        repo.close()


@pytest.mark.asyncio
async def test_external_body_verification_does_not_recover_a_playwright_human_page():
    from unittest.mock import AsyncMock

    from doxagent.site_strategy.runtime import RuntimeResponse
    from doxagent.site_strategy.schema import BrowserRuntimeKind
    from doxagent.site_strategy.worker_runtime import WorkerSiteAccessRuntime

    runtime = WorkerSiteAccessRuntime.__new__(WorkerSiteAccessRuntime)
    identity = SimpleNamespace(runtime_kind=BrowserRuntimeKind.EXTERNAL_CHROME)
    runtime._binding = lambda token: ("investing", identity, "egress")
    runtime.resolver = SimpleNamespace(resolve=lambda url: SimpleNamespace(site_id="investing"))
    original = RuntimeResponse(
        0,
        URL,
        {},
        LISTING,
        provenance=SimpleNamespace(instance_id="chrome"),
        challenge_cleared=True,
    )
    runtime.inspect_challenge = AsyncMock(return_value=original)
    runtime._browser = AsyncMock(
        return_value=RuntimeResponse(
            200, URL, {}, "<article/>", provenance=SimpleNamespace(instance_id="chrome")
        )
    )
    runtime.workers = SimpleNamespace(
        command=AsyncMock(side_effect=AssertionError("human recovery"))
    )
    result = await runtime.verify_login("session", URL)
    assert result.challenge_cleared
    runtime._browser.assert_awaited_once()
    runtime.workers.command.assert_not_awaited()
    runtime.inspect_challenge.return_value = RuntimeResponse(
        0, URL, {}, "<title>Verify you are human</title>"
    )
    runtime._browser.reset_mock()
    await runtime.verify_login("session", URL)
    runtime._browser.assert_not_awaited()


@pytest.mark.parametrize(
    "task_state,purpose,body,crawler,other_scope,cleared",
    [
        ("TARGET_LOST", "BODY", "FULL", "READY", False, True),
        ("TARGET_LOST", "BODY", "NOT_TESTED", "READY", False, False),
        ("TARGET_LOST", "BODY", "EXTRACTION_ERROR", "READY", False, False),
        ("TARGET_LOST", "CRAWLER", "NOT_TESTED", "READY", False, True),
        ("WAITING_HUMAN", "BODY", "FULL", "READY", False, False),
        ("TARGET_LOST", "BODY", "FULL", "READY", True, False),
    ],
)
def test_normal_login_can_recover_proven_lost_blocker_only(
    tmp_path, task_state, purpose, body, crawler, other_scope, cleared
):
    repo = SiteStrategyRepository(tmp_path / "registry.db")
    service = SiteStrategyService(repo, profile_root=tmp_path / "profiles")
    try:
        bootstrap_seed(repo, service)
        plan = migration_plan(repo)
        apply_migration(
            repo,
            plan,
            expected_revision=plan["expected_revision"],
            backup_path=tmp_path / "before.db",
        )
        store = GovernanceRepository(repo)
        scope = store.get_scope("source:investing_ticker_news")
        identity = scope.primary_identity_id
        target = store.save_target(
            OwnedBrowserTarget(
                identity_id=identity,
                browser_instance_id="old-chrome",
                worker_epoch=1,
                target_id="lost-target",
                state="LIVE",
            )
        )
        task = store.handoff_challenge(
            ChallengeTask(
                scope_id="publisher:investing" if other_scope else scope.scope_id,
                identity_id=identity,
                site_id="investing",
                purpose=purpose,
                operation_id="historical",
                original_url=URL,
                original_target_id="lost-target",
                browser_instance_id="old-chrome",
                allocation_id=target.allocation_id,
                reason="historical",
                state=task_state,
            )
        )
        state = repo.get_runtime(f"scope:{scope.scope_id}")
        state.combinations[identity] = CombinationRuntime(
            state="COOLDOWN", manual_attention_required=True, last_browser_result="ACCESS_CHALLENGE"
        )
        repo.save_runtime(state, expected_generation=state.generation)
        session = store.save_session(
            MaintenanceSession(
                kind="LOGIN",
                scope_id=scope.scope_id,
                site_id="investing",
                identity_id=identity,
                target_id="new-target",
                browser_instance_id="new-chrome",
            )
        )
        report = VerificationReport(
            challenge="CLEARED", crawler=crawler, body=body, runtime_instance_id="new-chrome"
        )
        store.commit_verification(scope, identity, report, session=session)
        updated = next(
            t for t in store.list_challenges(include_resolved=True) if t.task_id == task.task_id
        )
        assert (updated.state == "RESOLVED") == cleared
        member = repo.get_runtime(f"scope:{scope.scope_id}").combinations[identity]
        assert member.manual_attention_required == (not cleared)
    finally:
        repo.close()
