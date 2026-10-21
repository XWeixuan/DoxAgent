"""No live publisher requests, credentials, CAPTCHA interaction or Chrome starts."""

import base64
import json
import os
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from doxagent.site_strategy.challenge_evidence import EvidenceStore, cdp_screenshot, classify

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aX1sAAAAASUVORK5CYII="
)
CONTEXT = dict(
    scope_id="source:reuters_site_search",
    site_id="reuters",
    identity_id="reuters-1",
    browser_instance_id="instance",
    target_id="original",
    operation_id="poll-1",
    url="https://www.reuters.com/site-search/?token=secret",
)


@pytest.mark.parametrize(
    "markup,headers,provider,kind",
    [
        (
            '<form><iframe src="https://geo.captcha-delivery.com/captcha/?t=SECRET"/></form>',
            {},
            "DATADOME",
            "UNKNOWN",
        ),
        (
            '<div id="px-captcha-wrapper" class="px-captcha-visible">PRESS &amp; HOLD</div>',
            {},
            "HUMAN_PERIMETERX",
            "PRESS_AND_HOLD",
        ),
        (
            "<title>Checking your browser</title>",
            {"cf-mitigated": "challenge"},
            "CLOUDFLARE",
            "BROWSER_CHECK",
        ),
        ('<iframe src="https://newassets.hcaptcha.com/captcha/"/>', {}, "HCAPTCHA", "UNKNOWN"),
        (
            '<iframe src="https://www.google.com/recaptcha/api2/anchor?k=public"/>',
            {},
            "RECAPTCHA",
            "UNKNOWN",
        ),
        (
            '<script src="/cdn-cgi/challenge-platform/scripts/jsd/main.js"/>',
            {},
            "UNKNOWN",
            "UNKNOWN",
        ),
        ("<title>Access is temporarily restricted</title>", {}, "UNKNOWN", "ACCESS_BLOCK"),
        ('<iframe src="https://evilhcaptcha.com/"/>', {}, "UNKNOWN", "UNKNOWN"),
        ("<p>Sign in to continue</p>", {}, "UNKNOWN", "UNKNOWN"),
    ],
)
def test_evidence_classification_requires_active_evidence(markup, headers, provider, kind):
    value = classify(markup, headers=headers)
    assert (value["provider"], value["interaction"]) == (provider, kind)
    assert "SECRET" not in json.dumps(value)


def test_metadata_never_stores_form_values_scripts_auth_or_queries():
    value = classify(
        '<title>Test person@example.com</title><input value="PASSWORD123">'
        "<textarea>SECRET-TEXT</textarea><script>secret=SCRIPTSECRET</script>"
        '<iframe src="https://geo.captcha-delivery.com/captcha/?token=HIDDEN"/>',
        headers={"Authorization": "BEARER", "Set-Cookie": "COOKIE"},
    )
    for secret in (
        "person@example.com",
        "PASSWORD123",
        "SECRET-TEXT",
        "SCRIPTSECRET",
        "HIDDEN",
        "BEARER",
        "COOKIE",
    ):
        assert secret not in json.dumps(value)


@pytest.mark.asyncio
async def test_capture_two_frames_auto_clear_and_immutable_first(tmp_path):
    store = EvidenceStore(tmp_path / "evidence")
    screen = AsyncMock(return_value=PNG)
    evidence_id = await store.capture(
        CONTEXT, "<title>Checking your browser</title>", phase="first", screenshot=screen
    )
    await store.capture(
        CONTEXT, "<title>Different document</title>", phase="first", screenshot=screen
    )
    screen.assert_awaited_once()
    await store.capture(CONTEXT, "<title>Access denied</title>", screenshot=screen)
    await store.capture(CONTEXT, "<article>normal</article>", phase="cleared")
    record = store.get(evidence_id)
    assert record["state"] == "AUTO_CLEARED"
    assert set(record["frames"]) == {"first", "confirmed"}
    assert record["frames"]["first"]["classification"]["title"] == "Checking your browser"
    assert "secret" not in record["url"]
    assert store.image_path(evidence_id, "first").read_bytes() == PNG
    assert len(list(store.root.glob("*.png"))) == 2
    if os.name == "posix":
        assert store.image_path(evidence_id, "first").stat().st_mode & 0o777 == 0o600
        assert store.root.stat().st_mode & 0o777 == 0o700


@pytest.mark.asyncio
async def test_screenshot_failure_keeps_metadata_and_never_fails_operation(tmp_path):
    store = EvidenceStore(tmp_path / "evidence")
    evidence_id = await store.capture(
        CONTEXT, "<title>Access denied</title>", screenshot=AsyncMock(side_effect=TimeoutError)
    )
    record = store.get(evidence_id)
    assert record["frames"]["confirmed"]["screenshot_status"] == "TimeoutError"
    assert store.image_path(evidence_id, "confirmed") is None
    assert (
        await EvidenceStore(tmp_path / "no-parent" / "child").capture(
            {}, "<html>broken context</html>"
        )
        is None
    )


@pytest.mark.asyncio
async def test_confirmed_timeout_gets_one_bounded_later_retry(tmp_path):
    store = EvidenceStore(tmp_path / "evidence")
    first = AsyncMock(side_effect=TimeoutError)
    evidence_id = await store.capture(CONTEXT, "<title>Just a moment</title>", screenshot=first)
    retry = AsyncMock(return_value=PNG)
    await store.capture(CONTEXT, "<title>Just a moment</title>", screenshot=retry)
    await store.capture(CONTEXT, "<title>Just a moment</title>", screenshot=retry)
    retry.assert_awaited_once()
    assert store.get(evidence_id)["frames"]["confirmed"]["frame_sequence"] == 2


@pytest.mark.asyncio
async def test_confirmed_evidence_keeps_the_loaded_frame_assessment_sample(tmp_path):
    from doxagent.site_strategy.challenge_detection import assess

    markup = '<iframe src="https://geo.captcha-delivery.com/captcha/"/>'
    assessment = assess(markup, blocked=True, frame_html=["Slide right to secure your access"])
    store = EvidenceStore(tmp_path / "evidence")
    key = await store.capture(CONTEXT, markup, assessment=assessment.model_dump(mode="json"))
    frame = store.get(key)["frames"]["confirmed"]
    assert frame["sample_id"] == assessment.sample_id
    assert frame["classification"]["assessment"]["interaction"] == "SLIDER"


@pytest.mark.asyncio
async def test_native_screenshot_only_observes_exact_target_and_detaches():
    cdp = SimpleNamespace(
        send=AsyncMock(
            side_effect=[
                {"sessionId": "observation"},
                {"data": base64.b64encode(PNG).decode()},
                {},
            ]
        )
    )
    assert await cdp_screenshot(cdp, "original") == PNG
    assert [call.args[0] for call in cdp.send.await_args_list] == [
        "Target.attachToTarget",
        "Page.captureScreenshot",
        "Target.detachFromTarget",
    ]
    assert cdp.send.await_args_list[0].args[1]["targetId"] == "original"
    assert not cdp.send.await_args_list[1].args[1]["captureBeyondViewport"]


def test_retention_is_age_and_size_bounded_without_touching_other_files(tmp_path):
    store = EvidenceStore(tmp_path / "evidence", max_bytes=1)
    evidence_id = store.save(CONTEXT, classify("<title>Access denied</title>"), "confirmed", PNG)
    outside = tmp_path / "Cookies"
    outside.write_text("KEEP")
    foreign = store.root / "operator-note.txt"
    foreign.write_text("KEEP")
    result = store.prune()
    assert result["deleted"] == 2 and result["bytes"] == 0
    assert store.get(evidence_id) is None
    assert outside.read_text() == foreign.read_text() == "KEEP"
    store = EvidenceStore(tmp_path / "evidence")
    store.save(CONTEXT, {}, "confirmed", PNG)
    assert store.prune(now=time.time() + 15 * 86400)["deleted"] == 2
    with pytest.raises(ValueError):
        store.get("../Cookies")


def test_admin_api_reads_lost_page_evidence_without_browser_access(tmp_path):
    from doxagent.site_strategy.api import create_app
    from doxagent.site_strategy.governance_models import ChallengeTask
    from doxagent.site_strategy.governance_repository import GovernanceRepository
    from doxagent.site_strategy.repository import SiteStrategyRepository
    from doxagent.site_strategy.service import SiteStrategyService

    repo = SiteStrategyRepository(tmp_path / "registry.db")
    service = SiteStrategyService(repo, profile_root=tmp_path / "profiles")
    task = ChallengeTask(
        scope_id=CONTEXT["scope_id"],
        site_id="reuters",
        identity_id="reuters-1",
        purpose="CRAWLER",
        operation_id="poll-1",
        original_url=CONTEXT["url"],
        original_target_id="original",
        browser_instance_id="instance",
        allocation_id="allocation",
        state="TARGET_LOST",
        reason="test",
    )
    store = GovernanceRepository(repo)
    with repo.transaction() as db:
        store._write_task(db, task)  # Pre-existing durable TARGET_LOST fixture.
    service.runtime.evidence_store.save(
        CONTEXT, classify("<title>Access denied</title>"), "confirmed", PNG
    )
    app = create_app(service, worker_token="worker", admin_token="admin")
    # No app lifespan: this test must never start a browser or maintenance loop.
    client = TestClient(app)
    path = f"/v1/challenges/{task.task_id}/evidence/confirmed"
    assert client.get(path).status_code == 401
    assert client.get(path, headers={"Authorization": "Bearer worker"}).status_code == 401
    result = client.get(path, headers={"Authorization": "Bearer admin"})
    assert result.status_code == 200
    assert base64.b64decode(result.json()["image_base64"]) == PNG
    assert result.headers["cache-control"] == "no-store"
    assert (
        client.get(
            path.replace("confirmed", "other"), headers={"Authorization": "Bearer admin"}
        ).status_code
        == 422
    )
    other = task.model_copy(
        update={"task_id": "challenge_other", "original_target_id": "new", "identity_id": "other"}
    )
    with repo.transaction() as db:
        store._write_task(db, other)
    assert (
        client.get(
            f"/v1/challenges/{other.task_id}/evidence", headers={"Authorization": "Bearer admin"}
        ).status_code
        == 404
    )
    client.close()
    repo.close()
