import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from doxagent.site_strategy.datadome_browser import NativeChallenge
from doxagent.site_strategy.solver_forensics import WireEvidence, response_summary, request_headers


def test_only_wire_or_header_evidence_attributes_401_and_secrets_never_persist():
    body = '{"url":"https://geo.captcha-delivery.com/captcha/?cid=PRIVATE_CHALLENGE"}'
    document = response_summary("https://www.reuters.com/site-search/?query=PRIVATE_QUERY", 401,
        {}, body)
    assert document["layer"] == "UNCONFIRMED"
    wire = response_summary(document["url"], 401, {"Authorization": "PRIVATE_AUTH",
        "Set-Cookie": "datadome=PRIVATE_COOKIE", "X-DataDome": "protected"}, body, wire_body=True)
    assert wire["layer"] == "DATADOME"
    assert wire["challenge_url"] == "https://geo.captcha-delivery.com/captcha/"
    assert "PRIVATE" not in json.dumps(wire)
    assert response_summary(document["url"], 401, {})["layer"] == "UNCONFIRMED"
    assert response_summary(document["url"], 401, {"WWW-Authenticate": "Bearer realm=private"})["layer"] == "AUTHENTICATION"
    assert request_headers({"User-Agent": "Chrome/153", "Authorization": "PRIVATE",
        "Cookie": "PRIVATE", "Sec-Fetch-Mode": "cors"}) == {"user-agent": "Chrome/153", "sec-fetch-mode": "cors"}


@pytest.mark.asyncio
async def test_native_apply_tracks_sent_cookie_and_server_replacement_without_new_requests():
    calls, cookie = [], "PRIVATE_OLD"
    async def call(method, params=None):
        nonlocal cookie
        calls.append(method)
        if method == "DOM.getDocument":
            return {"root": {"documentURL": "https://www.reuters.com/"}}
        if method == "Network.setCookie":
            cookie = params["value"]
            return {"success": True}
        if method == "Network.getCookies":
            return {"cookies": [{"name": "datadome", "value": cookie, "domain": ".reuters.com"}]}
        if method == "Network.getResponseBody":
            return {"body": '{"url":"https://geo.captcha-delivery.com/captcha/?cid=PRIVATE_CID"}'}
        return {}
    async def observe(seconds):
        nonlocal cookie
        assert seconds == 5
        for method, params in [
            ("Network.requestWillBeSent", {"requestId": "api", "wallTime": 100,
                "request": {"url": "https://www.reuters.com/pf/api/v3/content/fetch/articles-by-search-v2?query=PRIVATE_QUERY"}}),
            ("Network.requestWillBeSentExtraInfo", {"requestId": "api", "headers": {"Cookie": "datadome=PRIVATE_SOLUTION"},
                "associatedCookies": [{"cookie": {"name": "datadome", "value": cookie, "domain": ".reuters.com"}, "blockedReasons": []}]}),
            ("Network.responseReceived", {"requestId": "api", "response": {"url": "https://www.reuters.com/pf/api/v3/content/fetch/articles-by-search-v2", "status": 401, "headers": {"x-datadome": "protected"}}}),
            ("Network.responseReceivedExtraInfo", {"requestId": "api", "statusCode": 401,
                "headers": {"Set-Cookie": "datadome=PRIVATE_NEXT; Domain=.reuters.com; Path=/"}}),
            ("Network.loadingFinished", {"requestId": "api"}),
        ]:
            call.observer({"method": method, "params": params})
        cookie = "PRIVATE_NEXT"
    call.observe_for = observe
    @asynccontextmanager
    async def connection():
        yield call
    browser = NativeChallenge.__new__(NativeChallenge)
    browser.connection = connection
    browser.identity = SimpleNamespace(identity_id="main", profile_id="profile")
    browser.target = SimpleNamespace(browser_instance_id="chrome", target_id="page")
    browser.recovery = SimpleNamespace(recovery_id="incident", site_id="reuters")
    result = await browser.apply("datadome=PRIVATE_SOLUTION; Domain=reuters.com", "https://www.reuters.com/")
    assert result["read_state"] == "BOUNDED_WINDOW_COMPLETE"
    assert result["cookies_before"][0]["fingerprint"] != result["solution_fingerprint"]
    assert result["cookies_after_write"][0]["fingerprint"] == result["solution_fingerprint"]
    row = result["requests"][0]
    assert row["request_cookies"][0]["fingerprint"] == result["solution_fingerprint"]
    assert row["wire_body"]["layer"] == "DATADOME"
    assert result["cookies_after_observation"][0]["fingerprint"] != result["solution_fingerprint"]
    assert row["response_extra"]["set_cookies"][0]["fingerprint"] == result["cookies_after_observation"][0]["fingerprint"]
    assert calls.count("Page.reload") == 1
    assert "PRIVATE" not in json.dumps(result)


@pytest.mark.asyncio
async def test_optional_evidence_failure_never_causes_another_apply_or_blocks_reload():
    calls = []
    async def call(method, params=None):
        calls.append(method)
        if method == "Network.enable":
            raise RuntimeError("observer unavailable")
        return {"DOM.getDocument": {"root": {"documentURL": "https://www.reuters.com/"}},
                "Network.setCookie": {"success": True},
                "Network.getCookies": {"cookies": [{"name": "datadome", "value": "PRIVATE",
                    "domain": ".reuters.com"}]}, "Page.reload": {}}[method]
    call.observe_for = None
    @asynccontextmanager
    async def connection():
        yield call
    browser = NativeChallenge.__new__(NativeChallenge)
    browser.connection = connection
    browser.identity = SimpleNamespace(identity_id="main", profile_id="profile")
    browser.target = SimpleNamespace(browser_instance_id="chrome", target_id="page")
    browser.recovery = SimpleNamespace(recovery_id="incident", site_id="reuters")
    result = await browser.apply("datadome=PRIVATE; Domain=reuters.com", "https://www.reuters.com/")
    assert result["read_state"] == "BEFORE_UNAVAILABLE"
    assert calls.count("Network.setCookie") == calls.count("Page.reload") == 1


def test_request_ring_is_bounded_and_unrelated_traffic_is_ignored():
    trace = WireEvidence("incident")
    for n in range(50):
        trace.event({"method": "Network.requestWillBeSent", "params": {"requestId": str(n),
            "request": {"url": "https://www.reuters.com/site-search/?q=" + str(n)}}})
    assert len(trace.result()) == 6
    trace.event({"method": "Network.requestWillBeSent", "params": {"requestId": "ad",
        "request": {"url": "https://ads.example/ad"}}})
    assert len(trace.result()) == 6


def test_known_request_keeps_cross_path_redirect_chain():
    trace = WireEvidence("incident")
    trace.event({"method": "Network.requestWillBeSent", "params": {"requestId": "a",
        "request": {"url": "https://www.reuters.com/site-search/?q=PRIVATE"}}})
    trace.event({"method": "Network.requestWillBeSent", "params": {"requestId": "a",
        "request": {"url": "https://www.reuters.com/account/login/?token=PRIVATE"},
        "redirectResponse": {"url": "https://www.reuters.com/site-search/?q=PRIVATE", "status": 302,
            "headers": {"location": "https://www.reuters.com/account/login/?token=PRIVATE"}}}})
    assert trace.result()[0]["redirects"][0]["status"] == 302
    assert "PRIVATE" not in json.dumps(trace.result())
