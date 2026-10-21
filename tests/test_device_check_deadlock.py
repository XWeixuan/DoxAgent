from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests import test_browser_recovery as base
from doxagent.site_strategy.challenge_detection import assess
from doxagent.site_strategy.recovery import recovery_listing_url_matches
from doxagent.site_strategy.runtime import RuntimeResponse
from doxagent.site_strategy.schema import AccessRequest, utc_now
from doxagent.site_strategy.verification import VerificationReport
from doxagent.site_strategy.verification_frames import observe_verification_frames

PARENT = '<iframe src="https://geo.captcha-delivery.com/interstitial/?old=1"></iframe>'


@pytest.fixture
def setup(tmp_path):
    yield from base.setup.__wrapped__(tmp_path)


retained = base.retained


@pytest.mark.asyncio
async def test_dom_owner_finds_banned_oopif_without_frame_tree():
    calls = []

    async def call(method, params=None, **kwargs):
        calls.append((method, params, kwargs))
        return {
            "DOM.querySelectorAll": {"nodeIds": [2]},
            "DOM.describeNode": {
                "node": {
                    "frameId": "owned-frame",
                    "attributes": ["src", "https://geo.captcha-delivery.com/interstitial/"],
                }
            },
            "Target.getTargetInfo": {
                "targetInfo": {
                    "targetId": "owned-frame",
                    "type": "iframe",
                    "url": "https://geo.captcha-delivery.com/captcha/?t=bv",
                }
            },
            "Target.attachToTarget": {"sessionId": "child"},
            "DOM.getDocument": {"root": {"nodeId": 3}},
            "DOM.getOuterHTML": {
                "outerHTML": "<title>You have been blocked</title>Access is temporarily restricted"
            },
            "Target.detachFromTarget": {},
        }[method]

    frames = await observe_verification_frames(call, {"nodeId": 1}, PARENT)
    assert frames[0]["loaded"] and frames[0]["url"].endswith("t=bv")
    assert "Page.getFrameTree" not in [c[0] for c in calls]
    assert calls[-1][0] == "Target.detachFromTarget"
    value = assess(PARENT, frame_html=[frames[0]["html"]], frame_urls=[frames[0]["url"]])
    assert value.kind == "ACCESS_BLOCK" and value.policy_reason == "datadome_ip_banned"


@pytest.mark.asyncio
async def test_unrelated_iframe_target_cannot_replace_owned_frame():
    call = AsyncMock(
        side_effect=[
            {"nodeIds": [2]},
            {
                "node": {
                    "frameId": "owned",
                    "attributes": ["src", "https://geo.captcha-delivery.com/interstitial/"],
                }
            },
            {
                "targetInfo": {
                    "targetId": "foreign",
                    "type": "iframe",
                    "url": "https://geo.captcha-delivery.com/captcha/?t=fe",
                }
            },
        ]
    )
    frames = await observe_verification_frames(call, {"nodeId": 1}, PARENT)
    assert not frames[0]["loaded"] and call.await_count == 3


def test_automatic_timeout_is_manual_review_not_fake_interactive(setup):
    value, target, _ = retained(setup)
    _, service, store, _ = setup
    value = value.model_copy(update={"opened_at": utc_now() - timedelta(minutes=5)})
    store.save_recovery(value, expected_generation=value.generation)
    spec = service.repository.get_strategy(value.site_id)
    capture = RuntimeResponse(403, "https://www.investing.com/", {}, body=PARENT)
    value = service.recovery._classify(
        value, capture, spec, VerificationReport(challenge="BLOCKED")
    )
    assert (
        value.handling_mode == "HUMAN"
        and value.manual_review_reason == "automatic_verification_stalled"
    )
    task = store.current_challenge(value.identity_id)
    assert task.reason == "automatic_verification_stalled"
    assert value.assessment["kind"] == "AUTO_CHECK"
    assert store.get_target(target.allocation_id).owner_ref == task.task_id
    again = service.recovery._classify(
        value, capture, spec, VerificationReport(challenge="BLOCKED")
    )
    assert (
        again.handling_mode == "HUMAN"
        and store.current_challenge(value.identity_id).task_id == task.task_id
    )
    from doxagent.site_strategy.maintenance import MaintenanceController

    assert task.task_id in [
        t["task_id"] for t in MaintenanceController(service)._inventory()["challenges"]
    ]


def test_lost_automatic_page_has_reopenable_manual_review_task(setup):
    value, target, _ = retained(setup)
    _, service, store, _ = setup
    store.save_target(
        target.model_copy(
            update={"owner_kind": "RECOVERY", "owner_ref": value.recovery_id, "state": "LOST"}
        )
    )
    value = value.model_copy(
        update={"opened_at": utc_now() - timedelta(minutes=6), "page_status": "LOST"}
    )
    store.save_recovery(value, expected_generation=value.generation)
    value = service.recovery._classify(
        value,
        RuntimeResponse(403, "https://www.investing.com/", {}, body=PARENT),
        service.repository.get_strategy(value.site_id),
        VerificationReport(challenge="BLOCKED"),
    )
    assert value.handling_mode == "HUMAN"
    assert store.current_challenge(value.identity_id).state == "TARGET_LOST"


@pytest.mark.parametrize(
    "actual,ok",
    [
        ("https://investorshub.advfn.com/stock-market/NASDAQ/micron-technology-MU/news", True),
        ("https://investorshub.advfn.com/stock-market/NASDAQ/MU/news", True),
        ("https://investorshub.advfn.com/stock-market/NASDAQ/intel-INTC/news", False),
        ("https://investorshub.advfn.com/stock-market/NYSE/micron-MU/news", False),
        ("https://evil.test/stock-market/NASDAQ/micron-MU/news", False),
        ("https://investorshub.advfn.com/login", False),
        ("https://investorshub.advfn.com/stock-market/NASDAQ/micron-MU/news?arbitrary=1", False),
    ],
)
def test_investorshub_canonicalization_is_same_instrument_only(actual, ok):
    request = AccessRequest(
        url="https://investorshub.advfn.com/stock-market/NASDAQ/MU/news",
        operation_id="probe",
        purpose="CRAWLER",
        mode="BROWSER",
        recipe_parameters={"ticker": "MU"},
    )
    assert (
        recovery_listing_url_matches(
            SimpleNamespace(source_id="investorshub_ticker_news"), request, actual
        )
        == ok
    )


def test_explicit_access_block_immediately_enters_review(setup):
    value, _, _ = retained(setup)
    _, service, store, _ = setup
    capture = RuntimeResponse(
        0,
        base.URL,
        {},
        body=PARENT,
        assessment={
            "kind": "ACCESS_BLOCK",
            "provider": "DATADOME",
            "policy_reason": "datadome_ip_banned",
            "handling_mode": "HUMAN",
        },
    )
    value = service.recovery._classify(
        value,
        capture,
        service.repository.get_strategy(value.site_id),
        VerificationReport(challenge="BLOCKED"),
    )
    assert value.manual_review_reason == "datadome_ip_banned"
    assert store.current_challenge(value.identity_id).state == "WAITING_HUMAN"


def test_inconclusive_normal_document_exits_hidden_automatic_loop(setup):
    value, _, _ = retained(setup)
    _, service, store, _ = setup
    value = value.model_copy(update={"opened_at": utc_now() - timedelta(minutes=5)})
    store.save_recovery(value, expected_generation=value.generation)
    value = service.recovery._classify(
        value,
        RuntimeResponse(200, base.URL, {}, body="<main>Incomplete app shell</main>"),
        service.repository.get_strategy(value.site_id),
        VerificationReport(crawler="CONTENT_ERROR"),
    )
    assert value.manual_review_reason == "recovery_contract_not_satisfied"
    assert value.assessment["kind"] == "NONE"
    assert store.current_challenge(value.identity_id).reason == "recovery_contract_not_satisfied"


@pytest.mark.asyncio
async def test_pending_body_does_not_starve_independent_crawler_proof(setup):
    value, _, context = retained(setup, purpose="BODY")
    _, service, store, _ = setup
    crawler = {**context, "purpose": "CRAWLER"}
    value = value.model_copy(update={"affected_contexts": [context, crawler]})
    store.save_recovery(value, expected_generation=value.generation)
    service.recovery._observe = AsyncMock(
        return_value=RuntimeResponse(200, base.URL, {}, body=base.LISTING)
    )
    service.recovery._commit = AsyncMock()
    await service.recovery._run(value.identity_id)
    assert service.recovery._commit.await_args.args[2]["purpose"] == "CRAWLER"
