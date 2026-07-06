from __future__ import annotations

import asyncio
from http import HTTPStatus
from typing import Any, cast

import httpx
from fastapi.testclient import TestClient

from doxagent.dashboard_api import create_app
from doxagent.dashboard_api.auth import (
    DashboardAuthSettings,
    DashboardPrincipal,
    SupabaseDashboardAuthVerifier,
    clear_dashboard_auth_cache,
)
from doxagent.dashboard_api.mock_fixtures import JsonObject, utc_now_iso
from doxagent.dashboard_api.mock_router import DashboardMockError


class _FakeVerifier:
    async def authenticate(self, token: str) -> DashboardPrincipal:
        if token == "dev-token":
            return DashboardPrincipal(
                user_id="dev-user",
                email="dev@example.test",
                tier="DEVELOPER",
                timezone="Asia/Shanghai",
                auth_mode="supabase",
            )
        if token == "free-token":
            raise DashboardMockError(
                code="FORBIDDEN",
                message="Current user does not have Dashboard dev access.",
                status_code=HTTPStatus.FORBIDDEN,
                retryable=False,
            )
        raise DashboardMockError(
            code="UNAUTHORIZED",
            message="Supabase token is invalid or expired.",
            status_code=HTTPStatus.UNAUTHORIZED,
            retryable=False,
        )


class _FakeDashboardService:
    def overview(self, *, date_text: str | None = None, tz: str | None = None) -> JsonObject:
        return {
            "generated_at": utc_now_iso(),
            "system": {
                "container_status": "normal",
                "current_session_phase": "formal_monitoring",
                "current_session_label": "运行时段",
                "dashboard_api_status": "normal",
                "message_bus_status": "normal",
                "status_color": "green",
            },
            "kpis": {
                "running_ticker_count": 0,
                "today_message_count": 0,
                "today_dtc_count": 0,
                "today_token_cost_usd": None,
                "exception_count": 0,
            },
            "tickers": [],
        }

    def start_ticker(
        self,
        ticker: str,
        *,
        force_initialize: bool,
        monitor_mode: str | None,
    ) -> JsonObject:
        return {
            "operation": "start",
            "status": "accepted",
            "ticker": ticker.upper(),
            "ticker_state": {
                "status": "running",
                "health": "normal",
                "monitor_mode": monitor_mode or "message_monitoring",
            },
        }

    def dashboard_events(
        self,
        *,
        ticker: str | None = None,
        event_types: str | None = None,
        last_event_id: str | None = None,
    ) -> list[JsonObject]:
        return [
            {
                "event_id": "evt_auth_test_001",
                "event_type": "runtime.execution.updated",
                "ticker": ticker or "NVDA",
                "occurred_at": utc_now_iso(),
                "payload": {"status": "completed"},
            }
        ]


def _client() -> TestClient:
    return TestClient(
        create_app(
            mode="real",
            dashboard_auth_settings=DashboardAuthSettings(
                auth_mode="supabase",
                supabase_url="https://example.supabase.co",
                supabase_publishable_key="sb_publishable_test",
            ),
            dashboard_auth_verifier=_FakeVerifier(),
            real_service=cast(Any, _FakeDashboardService()),
        )
    )


def test_dashboard_auth_config_is_public_and_supabase_shaped() -> None:
    response = _client().get("/api/dashboard/v1/auth/config")

    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["provider"] == "supabase"
    assert payload["supabase_url"] == "https://example.supabase.co"
    assert payload["supabase_publishable_key"] == "sb_publishable_test"


def test_dashboard_real_routes_require_valid_dev_user() -> None:
    client = _client()

    unauthorized = client.get("/api/dashboard/v1/overview")
    assert unauthorized.status_code == 401
    assert unauthorized.json()["error"]["code"] == "UNAUTHORIZED"

    invalid = client.get(
        "/api/dashboard/v1/overview",
        headers={"Authorization": "Bearer invalid-token"},
    )
    assert invalid.status_code == 401
    assert invalid.json()["error"]["code"] == "UNAUTHORIZED"

    forbidden = client.get(
        "/api/dashboard/v1/overview",
        headers={"Authorization": "Bearer free-token"},
    )
    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "FORBIDDEN"

    me = client.get(
        "/api/dashboard/v1/auth/me",
        headers={"Authorization": "Bearer dev-token"},
    )
    assert me.status_code == 200
    assert me.json()["data"]["is_dev"] is True


def test_dashboard_mutations_and_sse_are_dev_guarded() -> None:
    client = _client()

    rejected_mutation = client.post(
        "/api/dashboard/v1/tickers",
        json={"ticker": "NVDA"},
        headers={"Authorization": "Bearer free-token"},
    )
    assert rejected_mutation.status_code == 403
    assert rejected_mutation.json()["error"]["code"] == "FORBIDDEN"

    mutation = client.post(
        "/api/dashboard/v1/tickers",
        json={"ticker": "NVDA"},
        headers={"Authorization": "Bearer dev-token"},
    )
    assert mutation.status_code == 200
    assert mutation.json()["data"]["operation"] == "start"

    rejected_sse = client.get("/api/dashboard/v1/events?once=true")
    assert rejected_sse.status_code == 401
    assert rejected_sse.json()["error"]["code"] == "UNAUTHORIZED"

    sse = client.get(
        "/api/dashboard/v1/events?once=true",
        headers={"Authorization": "Bearer dev-token"},
    )
    assert sse.status_code == 200
    assert sse.headers["content-type"].startswith("text/event-stream")
    assert "event: runtime.execution.updated" in sse.text


def test_supabase_auth_verifier_caches_successful_dev_principal() -> None:
    clear_dashboard_auth_cache()
    calls = {"user": 0, "profile": 0}
    settings = DashboardAuthSettings(
        auth_mode="supabase",
        supabase_url="https://example.supabase.co",
        supabase_publishable_key="sb_publishable_test",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/auth/v1/user":
            calls["user"] += 1
            return httpx.Response(200, json={"id": "dev-user", "email": "dev@example.test"})
        if request.url.path == "/rest/v1/user_profiles":
            calls["profile"] += 1
            return httpx.Response(200, json=[{"tier": "DEVELOPER", "timezone": "Asia/Shanghai"}])
        return httpx.Response(404)

    def verifier() -> SupabaseDashboardAuthVerifier:
        return SupabaseDashboardAuthVerifier(
            settings,
            client_factory=lambda: httpx.AsyncClient(
                transport=httpx.MockTransport(handler)
            ),
        )

    first = asyncio.run(verifier().authenticate("dev-token"))
    second = asyncio.run(verifier().authenticate("dev-token"))

    assert first == second
    assert calls == {"user": 1, "profile": 1}
    clear_dashboard_auth_cache()


def test_supabase_auth_verifier_does_not_cache_unauthorized_or_forbidden() -> None:
    clear_dashboard_auth_cache()
    calls = {"invalid_user": 0, "free_user": 0, "free_profile": 0}
    settings = DashboardAuthSettings(
        auth_mode="supabase",
        supabase_url="https://example.supabase.co",
        supabase_publishable_key="sb_publishable_test",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        token = request.headers.get("authorization", "")
        if request.url.path == "/auth/v1/user" and token == "Bearer invalid-token":
            calls["invalid_user"] += 1
            return httpx.Response(401, json={"message": "invalid"})
        if request.url.path == "/auth/v1/user" and token == "Bearer free-token":
            calls["free_user"] += 1
            return httpx.Response(200, json={"id": "free-user", "email": "free@example.test"})
        if request.url.path == "/rest/v1/user_profiles" and token == "Bearer free-token":
            calls["free_profile"] += 1
            return httpx.Response(200, json=[{"tier": "FREE", "timezone": "Asia/Shanghai"}])
        return httpx.Response(404)

    def verifier() -> SupabaseDashboardAuthVerifier:
        return SupabaseDashboardAuthVerifier(
            settings,
            client_factory=lambda: httpx.AsyncClient(
                transport=httpx.MockTransport(handler)
            ),
        )

    for token, expected_code in [
        ("invalid-token", "UNAUTHORIZED"),
        ("invalid-token", "UNAUTHORIZED"),
        ("free-token", "FORBIDDEN"),
        ("free-token", "FORBIDDEN"),
    ]:
        try:
            asyncio.run(verifier().authenticate(token))
        except DashboardMockError as exc:
            assert exc.code == expected_code
        else:
            raise AssertionError(f"{token} should not authenticate")

    assert calls == {"invalid_user": 2, "free_user": 2, "free_profile": 2}
    clear_dashboard_auth_cache()
