from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from doxagent.site_strategy.human_verification import manual_review_reason, observe_human_transition
from doxagent.site_strategy.runtime import RuntimeResponse


def page(kind):
    return RuntimeResponse(0, "https://www.reuters.com/site-search/", {},
        body="<main>Current document</main>", assessment={"kind":kind})


@pytest.mark.parametrize("delay", [4, 5, 9])
async def test_human_verify_waits_for_delayed_page_transition_without_input_or_navigation(delay):
    now = [0]
    async def sleep(seconds): now[0] += seconds
    async def observe(): return page("INTERACTIVE" if now[0] < delay else "NONE")
    stages = []
    result = await observe_human_transition(observe, clock=lambda:now[0], sleep=sleep,
                                           on_wait=stages.append)
    assert result.assessment["kind"] == "NONE"
    assert now[0] == delay
    assert stages == ["WAITING_FOR_PAGE_TRANSITION"]


async def test_human_verify_persistent_challenge_is_bounded_and_never_marked_clear():
    now = [0]
    async def sleep(seconds): now[0] += seconds
    observe = AsyncMock(return_value=page("INTERACTIVE"))
    result = await observe_human_transition(observe, clock=lambda:now[0], sleep=sleep)
    assert now[0] == 15 and result.assessment["kind"] == "INTERACTIVE"
    assert not result.challenge_cleared
    assert observe.await_count == 15


async def test_explicit_rejection_and_lost_target_do_not_wait():
    observe = AsyncMock(return_value=page("ACCESS_BLOCK"))
    result = await observe_human_transition(observe)
    assert result.assessment["kind"] == "ACCESS_BLOCK" and observe.await_count == 1
    lost = AsyncMock(side_effect=RuntimeError("original_challenge_target_lost"))
    with pytest.raises(RuntimeError, match="target_lost"):
        await observe_human_transition(lost)
    assert lost.await_count == 1


def test_terminal_provider_outcome_is_manual_diagnostic_not_another_pending_loop():
    recovery = SimpleNamespace(phase="BACKOFF", manual_review_reason=None, handling_mode="HUMAN",
                               scene_preparation={"attempt_no":2})
    assert manual_review_reason(recovery, {"status":"FAILED", "reason":"ERROR_CAPTCHA_UNSOLVABLE"}) == "provider_unsolvable_manual_review"
    assert manual_review_reason(recovery, {"status":"APPLIED", "verification_state":"STILL_BLOCKED"}) == "provider_result_still_blocked_manual_review"
    assert manual_review_reason(recovery, {"status":"PENDING"}) is None
    assert manual_review_reason(recovery) == "challenge_retry_limit_manual_review"
    recovery.phase = "RESOLVED"
    assert manual_review_reason(recovery, {"status":"FAILED"}) is None
