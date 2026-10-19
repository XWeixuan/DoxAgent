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
