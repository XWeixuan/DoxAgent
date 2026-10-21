import pytest

from doxagent.site_strategy.challenge_detection import assess
from doxagent.site_strategy.schema import ChallengePolicy


@pytest.mark.parametrize(
    "html,headers,blocked,kind,mode",
    [
        (
            '<iframe src="https://geo.captcha-delivery.com/captcha/"></iframe>Slide right to secure your access',
            {},
            True,
            "INTERACTIVE",
            "HUMAN",
        ),
        (
            '<iframe src="https://geo.captcha-delivery.com/interstitial/"></iframe>Verifying the device',
            {},
            True,
            "AUTO_CHECK",
            "AUTO",
        ),
        (
            "<title>Just a moment</title>Performing security verification",
            {"cf-mitigated": "challenge"},
            True,
            "AUTO_CHECK",
            "AUTO",
        ),
        ("<title>Access is temporarily restricted</title>", {}, True, "ACCESS_BLOCK", "AUTO"),
        ("<article>News about Cloudflare CAPTCHA technology.</article>", {}, False, "NONE", "AUTO"),
        (
            '<script src="/cdn-cgi/challenge-platform/jsd.js"></script><article>News</article>',
            {},
            False,
            "NONE",
            "AUTO",
        ),
        (
            '<title>Login</title><input type="password">Sign in to continue',
            {},
            False,
            "NONE",
            "AUTO",
        ),
        ("<div>Advertisement: sign in for offers</div>", {}, False, "NONE", "AUTO"),
        ("<title>Forbidden</title>", {}, False, "NONE", "AUTO"),
        (
            '<title>Verification Required</title><iframe src="https://geo.captcha-delivery.com/captcha/"></iframe>',
            {},
            True,
            "UNKNOWN",
            "AUTO",
        ),
        (
            '<title>Verify you are human</title><input type="checkbox">',
            {"cf-mitigated": "challenge"},
            True,
            "INTERACTIVE",
            "HUMAN",
        ),
    ],
)
def test_classification_uses_active_evidence(html, headers, blocked, kind, mode):
    value = assess(html, headers=headers, blocked=blocked)
    assert (value.kind, value.handling_mode) == (kind, mode)


def test_unknown_reuters_policy_does_not_turn_explicit_device_check_into_human():
    policy = ChallengePolicy(unknown_handling="human")
    assert assess("Verification Required", policy=policy, blocked=True).handling_mode == "HUMAN"
    value = assess(
        'Verifying the device<iframe src="https://geo.captcha-delivery.com/"/>',
        policy=policy,
        blocked=True,
    )
    assert value.kind == "AUTO_CHECK" and value.handling_mode == "AUTO"


def test_normal_document_proof_beats_stale_challenge_header():
    result = assess(
        "<article>Actual full news</article>",
        headers={"cf-mitigated": "challenge"},
        business_ready=True,
    )
    assert result.kind == "NONE" and result.policy_reason == "business_document_verified"


def test_loaded_provider_frame_can_supply_slider_instruction():
    result = assess(
        '<iframe src="https://geo.captcha-delivery.com/"/>',
        blocked=True,
        frame_html=["Slide right to secure your access"],
    )
    assert result.kind == "INTERACTIVE" and result.interaction == "SLIDER"


def test_provider_header_confidence_does_not_fabricate_a_known_challenge_type():
    value = assess("<html><body></body></html>", headers={"cf-mitigated": "challenge"})
    assert value.provider == "CLOUDFLARE" and value.provider_confidence == "HIGH"
    assert value.kind == "UNKNOWN" and value.confidence == "UNKNOWN"


def test_hidden_checkbox_is_not_an_interactive_requirement():
    value = assess(
        '<title>Just a moment</title><div hidden><input type="checkbox">Verify you are human</div>'
        "Checking your browser",
        headers={"cf-mitigated": "challenge"},
        blocked=True,
    )
    assert value.kind == "AUTO_CHECK"


def test_loaded_visible_checkbox_frame_is_human():
    value = assess(
        '<iframe src="https://challenges.cloudflare.com/check"/>',
        blocked=True,
        frame_html=['<input type="checkbox">Verify you are human'],
    )
    assert value.kind == "INTERACTIVE" and value.interaction == "CHECKBOX"
