from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from doxagent.site_strategy.governance_models import ChallengeTask, OwnedBrowserTarget
from doxagent.site_strategy.governance_repository import GovernanceRepository
from doxagent.site_strategy.isolation_migration import apply_migration, migration_plan
from doxagent.site_strategy.repository import SiteStrategyRepository
from doxagent.site_strategy.runtime import RuntimeResponse
from doxagent.site_strategy.schema import CombinationRuntime
from doxagent.site_strategy.seeds import bootstrap_seed
from doxagent.site_strategy.service import SiteStrategyService

URL = "https://www.investing.com/equities/vector-acquisition-news"
LISTING = (
    '<h2>RKLB News</h2><article data-test="article-item">'
    '<a data-test="article-title-link" href="/news/stock-market-news/rocket-12345">'
    'Rocket Lab news</a><time datetime="2026-10-19 20:17:49"/></article>'
)


@pytest.mark.asyncio
async def test_wait_more_than_eight_seconds_for_passive_cloudflare(monkeypatch):
    import asyncio
    import time

    from doxagent.site_strategy import investing_document as module

    now = time.monotonic()
    start = now
    original_sleep = asyncio.sleep

    async def sleep(seconds):
        nonlocal now
        now += seconds
        await original_sleep(0)

    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: now))
    monkeypatch.setattr(module.asyncio, "sleep", sleep)

    class CDP:
        async def send(self, method, params, **kwargs):
            if method == "Target.attachToTarget":
                return {"sessionId": "observe"}
            if method == "DOM.getDocument":
                return {"root": {"nodeId": 1, "documentURL": URL}}
            if method == "DOM.getOuterHTML":
                return {
                    "outerHTML": "<title>Verify you are human</title>"
                    if now - start < 14
                    else LISTING
                }
            assert method == "Target.detachFromTarget"
            return {}

    html, _ = await module.capture_document(CDP(), "original", URL, timeout=25)
    assert html == LISTING and now - start >= 14


@pytest.mark.asyncio
async def test_external_crawler_verify_does_not_recover_human_playwright_handle():
    from doxagent.site_strategy.schema import AccessRequest, BrowserRuntimeKind
    from doxagent.site_strategy.worker_runtime import WorkerSiteAccessRuntime

    runtime = WorkerSiteAccessRuntime.__new__(WorkerSiteAccessRuntime)
    identity = SimpleNamespace(runtime_kind=BrowserRuntimeKind.EXTERNAL_CHROME)
    runtime._binding = lambda token: ("investing", identity, "egress")
    runtime.resolver = SimpleNamespace(resolve=lambda url: "resolved")
    runtime._maintenance_refs = lambda token: {"expected_instance_id": "chrome"}
    runtime._browser = AsyncMock(
        return_value=RuntimeResponse(
            200, URL, {}, LISTING, provenance=SimpleNamespace(instance_id="chrome")
        )
    )
    runtime.workers = SimpleNamespace(command=AsyncMock(side_effect=AssertionError("old handle")))
    request = AccessRequest(operation_id="verify", url=URL, purpose="CRAWLER", mode="BROWSER")
    await runtime.verify_crawler("token", request)
    assert runtime._browser.await_args.args[0].recipe_parameters["_maintenance_canary"]
    runtime.workers.command.assert_not_awaited()


@pytest.mark.asyncio
async def test_manual_verify_proves_affected_rklb_not_default_intc(tmp_path):
    from doxagent.site_strategy.governance_models import MaintenanceSession

    repo = SiteStrategyRepository(tmp_path / "site.db")
    service = SiteStrategyService(repo, profile_root=tmp_path / "profiles")
    bootstrap_seed(repo, service)
    plan = migration_plan(repo)
    apply_migration(
        repo, plan, expected_revision=plan["expected_revision"], backup_path=tmp_path / "before.db"
    )
    store = GovernanceRepository(repo)
    scope = store.get_scope("source:investing_ticker_news")
    identity = scope.primary_identity_id
    target = store.save_target(
        OwnedBrowserTarget(
            identity_id=identity,
            browser_instance_id="chrome",
            worker_epoch=1,
            target_id="original",
            state="LIVE",
        )
    )
    task = store.handoff_challenge(
        ChallengeTask(
            scope_id=scope.scope_id,
            identity_id=identity,
            site_id="investing",
            purpose="CRAWLER",
            operation_id="poll",
            original_url=URL,
            original_target_id="original",
            browser_instance_id="chrome",
            allocation_id=target.allocation_id,
            reason="challenge_required",
        )
    )
    session = store.save_session(
        MaintenanceSession(
            kind="CHALLENGE",
            scope_id=scope.scope_id,
            identity_id=identity,
            site_id="investing",
            task_id=task.task_id,
            control_token=task.task_id,
            target_id="original",
            allocation_id=target.allocation_id,
            browser_instance_id="chrome",
        )
    )
    service.runtime.inspect_challenge = AsyncMock(
        return_value=RuntimeResponse(
            0,
            URL,
            {},
            LISTING,
            provenance=SimpleNamespace(instance_id="chrome"),
            challenge_cleared=True,
        )
    )
    service.runtime.verify_crawler = AsyncMock(side_effect=AssertionError("wrong ticker canary"))
    service.runtime.verify_login = AsyncMock(return_value=RuntimeResponse(500, URL, {}, ""))
    try:
        report = await service.verify_scope_session(session)
        assert report.crawler == "READY"
        service.runtime.verify_crawler.assert_not_awaited()
        assert store.get_challenge(task.task_id).state == "RESOLVED"
    finally:
        repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["cleared", "blocked", "other_url", "human_open", "vendor_unknown", "stale"]
)
async def test_passive_recovery_requires_exact_live_business_proof(tmp_path, monkeypatch, case):
    from doxagent.site_strategy import maintenance_document
    from doxagent.site_strategy.challenge_inspector import ChallengeInspector
    from doxagent.site_strategy.governance_models import MaintenanceSession

    repo = SiteStrategyRepository(tmp_path / "site.db")
    service = SiteStrategyService(repo, profile_root=tmp_path / "profiles")
    bootstrap_seed(repo, service)
    plan = migration_plan(repo)
    apply_migration(
        repo, plan, expected_revision=plan["expected_revision"], backup_path=tmp_path / "before.db"
    )
    store = GovernanceRepository(repo)
    scope = store.get_scope("source:investing_ticker_news")
    identity = scope.primary_identity_id
    target = store.save_target(
        OwnedBrowserTarget(
            identity_id=identity,
            browser_instance_id="chrome",
            worker_epoch=1,
            target_id="original",
            state="LIVE",
        )
    )
    task = store.handoff_challenge(
        ChallengeTask(
            scope_id=scope.scope_id,
            identity_id=identity,
            site_id="investing",
            purpose="CRAWLER",
            operation_id="poll",
            original_url=URL,
            original_target_id="original",
            browser_instance_id="chrome",
            allocation_id=target.allocation_id,
            reason="challenge_required",
        )
    )
    profile = repo.get_profile(repo.get_identity(identity).profile_id)
    await service.profile_use.finish_challenge(profile.profile_id, task.task_id)
    state = repo.get_runtime(f"scope:{scope.scope_id}")
    state.combinations[identity] = CombinationRuntime(
        state="COOLDOWN", manual_attention_required=True, last_browser_result="ACCESS_CHALLENGE"
    )
    repo.save_runtime(state, expected_generation=state.generation)
    if case == "human_open":
        store.save_session(
            MaintenanceSession(
                kind="LOGIN", scope_id=scope.scope_id, identity_id=identity, site_id="investing"
            )
        )
    service.runtime.evidence_store = SimpleNamespace(
        for_task=lambda task: {
            "frames": {
                "confirmed": {
                    "classification": {
                        "provider": "UNKNOWN" if case == "vendor_unknown" else "CLOUDFLARE"
                    }
                }
            }
        }
    )

    async def read(*args, **kwargs):
        if case == "stale":
            store.save_challenge(task.model_copy(update={"state": "TARGET_LOST"}))
        return RuntimeResponse(
            0,
            "https://www.investing.com/" if case == "other_url" else URL,
            {},
            "<title>Verify you are human</title>" if case == "blocked" else LISTING,
            provenance=SimpleNamespace(instance_id="chrome"),
        )

    monkeypatch.setattr(maintenance_document, "read_original_document", read)
    service.runtime.close_login = AsyncMock()
    try:
        inspector = ChallengeInspector(service)
        if case == "stale":
            with pytest.raises(RuntimeError, match="stale"):
                await inspector.recover_passive_investing(task, object())
        else:
            result = await inspector.recover_passive_investing(task, object())
            assert result == (case == "cleared")
        recovered = store.get_challenge(task.task_id).state == "RESOLVED"
        assert recovered == (case == "cleared")
        if recovered:
            member = repo.get_runtime(f"scope:{scope.scope_id}").combinations[identity]
            assert (
                not member.manual_attention_required
                and member.capabilities["CRAWLER"]["result"] == "READY"
            )
            assert "BODY" not in member.capabilities
            assert repo.get_profile(profile.profile_id).maintenance_session_id is None
            service.runtime.close_login.assert_awaited_once()
        else:
            service.runtime.close_login.assert_not_awaited()
    finally:
        repo.close()
