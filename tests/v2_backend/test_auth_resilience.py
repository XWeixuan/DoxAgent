"""Short upstream outages must not multiply authentication requests or bypass checks."""

import asyncio
import base64
import json
import time

import httpx
import pytest

from doxagent.api_v2.auth import SupabaseAuth
from doxagent.api_v2.errors import ApiFailure


def token(expires=None):
    payload = base64.urlsafe_b64encode(json.dumps({"exp": expires or time.time() + 60}).encode())
    return "header." + payload.decode().rstrip("=") + ".signature"


def success(request):
    return httpx.Response(
        200, json={"id": "alice"} if request.url.path.endswith("/user") else [{"tier": "DEVELOPER"}]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/auth/v1/user", "/rest/v1/user_profiles"])
@pytest.mark.parametrize("failure", ["timeout", "gateway"])
async def test_retry_once_and_never_log_credentials(path, failure, caplog):
    calls = []

    def transport(request):
        calls.append(request.url.path)
        if request.url.path == path and calls.count(path) == 1:
            if failure == "timeout":
                raise httpx.ReadTimeout("must not log raw request", request=request)
            return httpx.Response(503)
        return success(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        auth = SupabaseAuth("https://test.supabase.co", "sb_publishable_fixture", client=client)
        bearer = token()
        assert (await auth.authenticate(bearer)).user_id == "alice"
        assert calls.count(path) == 2 and len(calls) == 3
        assert await auth.authenticate(bearer) == auth.cache[next(iter(auth.cache))][1]
        assert len(calls) == 3
        assert "supabase_auth stage=" in caplog.text
        assert bearer not in caplog.text and "sb_publishable_fixture" not in caplog.text
        assert "must not log raw request" not in caplog.text


@pytest.mark.asyncio
async def test_same_session_shares_inflight_and_cancel_does_not_abort_other_waiter():
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def transport(request):
        calls.append(request.url.path)
        if request.url.path.endswith("/user"):
            entered.set()
            await release.wait()
        return success(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        auth = SupabaseAuth("https://test.supabase.co", "sb_publishable_fixture", client=client)
        bearer = token()
        first = asyncio.create_task(auth.authenticate(bearer))
        await entered.wait()
        others = [asyncio.create_task(auth.authenticate(bearer)) for _ in range(12)]
        await asyncio.sleep(0)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        release.set()
        assert all(p.user_id == "alice" for p in await asyncio.gather(*others))
        assert len(calls) == 2 and not auth._inflight


@pytest.mark.asyncio
async def test_different_sessions_never_share_principals():
    first, second = token(time.time() + 60), token(time.time() + 120)
    calls = []

    async def transport(request):
        calls.append(request.url.path)
        await asyncio.sleep(0)
        if request.url.path.endswith("/user"):
            user = "alice" if request.headers["authorization"] == "Bearer " + first else "bob"
            return httpx.Response(200, json={"id": user})
        return success(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        auth = SupabaseAuth("https://test.supabase.co", "sb_publishable_fixture", client=client)
        result = await asyncio.gather(auth.authenticate(first), auth.authenticate(second))
        assert [p.user_id for p in result] == ["alice", "bob"]
        assert len(calls) == 4


@pytest.mark.asyncio
async def test_exhausted_retry_is_uncached_and_next_request_can_recover():
    calls = []
    failing = True

    def transport(request):
        calls.append(request.url.path)
        if failing:
            raise httpx.ReadTimeout("unavailable", request=request)
        return success(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        auth = SupabaseAuth("https://test.supabase.co", "sb_publishable_fixture", client=client)
        bearer = token()
        with pytest.raises(ApiFailure) as error:
            await auth.authenticate(bearer)
        assert error.value.code == "AUTH_UNAVAILABLE" and error.value.retryable
        assert len(calls) == 2 and not auth.cache and not auth._inflight
        failing = False
        assert (await auth.authenticate(bearer)).user_id == "alice"
        assert len(calls) == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403])
async def test_credential_rejection_is_not_retried_or_cached(status):
    calls = []

    def transport(request):
        calls.append(request.url.path)
        return httpx.Response(status)

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        auth = SupabaseAuth("https://test.supabase.co", "sb_publishable_fixture", client=client)
        with pytest.raises(ApiFailure) as error:
            await auth.authenticate(token())
        assert error.value.code == "UNAUTHORIZED"
        assert len(calls) == 1 and not auth.cache


@pytest.mark.asyncio
async def test_non_developer_profile_still_forbidden():
    def transport(request):
        return httpx.Response(
            200, json={"id": "alice"} if request.url.path.endswith("/user") else [{"tier": "BASIC"}]
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        auth = SupabaseAuth("https://test.supabase.co", "sb_publishable_fixture", client=client)
        with pytest.raises(ApiFailure) as error:
            await auth.authenticate(token())
        assert error.value.code == "FORBIDDEN" and not auth.cache


@pytest.mark.asyncio
async def test_overall_budget_releases_inflight_tasks(monkeypatch):
    from doxagent.api_v2 import auth as module

    monkeypatch.setattr(module, "AUTH_SECONDS", 0.02)

    async def transport(request):
        await asyncio.Event().wait()

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        auth = SupabaseAuth("https://test.supabase.co", "sb_publishable_fixture", client=client)
        with pytest.raises(ApiFailure) as error:
            await asyncio.wait_for(auth.authenticate(token()), 1)
        assert error.value.code == "AUTH_UNAVAILABLE"
        assert not auth._inflight and not auth.cache
        await auth.close()


@pytest.mark.asyncio
async def test_token_expiring_during_verification_cannot_be_promoted(monkeypatch):
    from doxagent.api_v2 import auth as module

    clock = [1000]
    monkeypatch.setattr(module.time, "time", lambda: clock[0])

    def transport(request):
        if not request.url.path.endswith("/user"):
            clock[0] = 1101
        return success(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        auth = SupabaseAuth("https://test.supabase.co", "sb_publishable_fixture", client=client)
        with pytest.raises(ApiFailure) as error:
            await auth.authenticate(token(1100))
        assert error.value.code == "UNAUTHORIZED" and not auth.cache
