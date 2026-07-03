"""Authentication helpers for the real Dashboard State API."""

from __future__ import annotations

import os
from dataclasses import dataclass
from http import HTTPStatus
from typing import Protocol, cast

import httpx
from fastapi import Request

from doxagent.dashboard_api.mock_fixtures import JsonObject
from doxagent.dashboard_api.mock_router import DashboardMockError, require_mock_auth

REAL_DASHBOARD_AUTH_MODES = {"supabase", "real", "required"}
MOCK_DASHBOARD_AUTH_MODES = {"", "open", "off", "mock-open", "mock-required", "mock-forbidden"}


@dataclass(frozen=True)
class DashboardAuthSettings:
    auth_mode: str
    supabase_url: str | None
    supabase_publishable_key: str | None
    user_profiles_table: str = "user_profiles"
    dev_tier_value: str = "DEVELOPER"

    @property
    def normalized_mode(self) -> str:
        return self.auth_mode.strip().lower()

    @property
    def is_supabase_mode(self) -> bool:
        return self.normalized_mode in REAL_DASHBOARD_AUTH_MODES

    @property
    def is_mock_mode(self) -> bool:
        return self.normalized_mode in MOCK_DASHBOARD_AUTH_MODES


@dataclass(frozen=True)
class DashboardPrincipal:
    user_id: str
    email: str | None
    tier: str
    timezone: str | None
    auth_mode: str

    @property
    def is_dev(self) -> bool:
        return self.tier.upper() == "DEVELOPER"

    def public_payload(self) -> JsonObject:
        return {
            "user_id": self.user_id,
            "email": self.email,
            "tier": self.tier,
            "timezone": self.timezone,
            "is_dev": self.is_dev,
            "auth_mode": self.auth_mode,
        }


class DashboardAuthVerifier(Protocol):
    async def authenticate(self, token: str) -> DashboardPrincipal:
        """Return a verified dev principal or raise DashboardMockError."""


def dashboard_auth_settings_from_env(*, default_auth_mode: str) -> DashboardAuthSettings:
    return DashboardAuthSettings(
        auth_mode=os.getenv("DOXAGENT_DASHBOARD_AUTH_MODE", default_auth_mode),
        supabase_url=_first_env(
            "DOXAGENT_DASHBOARD_SUPABASE_URL",
            "DOXATLAS_SUPABASE_URL",
            "SUPABASE_URL",
        ),
        supabase_publishable_key=_first_env(
            "DOXAGENT_DASHBOARD_SUPABASE_PUBLISHABLE_KEY",
            "DOXAGENT_DASHBOARD_SUPABASE_ANON_KEY",
            "DOXATLAS_SUPABASE_PUBLISHABLE_KEY",
            "DOXATLAS_SUPABASE_ANON_KEY",
            "SUPABASE_PUBLISHABLE_KEY",
            "SUPABASE_ANON_KEY",
        ),
        user_profiles_table=os.getenv("DOXAGENT_DASHBOARD_USER_PROFILES_TABLE", "user_profiles"),
        dev_tier_value=os.getenv("DOXAGENT_DASHBOARD_DEV_TIER", "DEVELOPER").upper(),
    )


def auth_config_payload(settings: DashboardAuthSettings) -> JsonObject:
    return {
        "auth_mode": settings.auth_mode,
        "provider": "supabase" if settings.is_supabase_mode else "mock",
        "supabase_url": settings.supabase_url if settings.is_supabase_mode else None,
        "supabase_publishable_key": (
            settings.supabase_publishable_key if settings.is_supabase_mode else None
        ),
        "dev_tier": settings.dev_tier_value,
    }


async def require_dashboard_auth(request: Request) -> DashboardPrincipal:
    settings = _settings_from_request(request)
    if settings.is_mock_mode:
        await require_mock_auth(request)
        principal = DashboardPrincipal(
            user_id="mock-dev-user",
            email=None,
            tier=settings.dev_tier_value,
            timezone=None,
            auth_mode=settings.auth_mode,
        )
        request.state.dashboard_user = principal
        return principal

    if not settings.is_supabase_mode:
        raise DashboardMockError(
            code="UNAUTHORIZED",
            message="Dashboard auth mode is not configured.",
            status_code=HTTPStatus.UNAUTHORIZED,
            retryable=False,
            details={"auth_mode": settings.auth_mode},
        )

    token = _bearer_token(request)
    verifier = cast(
        DashboardAuthVerifier | None,
        getattr(request.app.state, "dashboard_auth_verifier", None),
    )
    if verifier is None:
        verifier = SupabaseDashboardAuthVerifier(settings)
    principal = await verifier.authenticate(token)
    request.state.dashboard_user = principal
    return principal


class SupabaseDashboardAuthVerifier:
    def __init__(self, settings: DashboardAuthSettings) -> None:
        if not settings.supabase_url or not settings.supabase_publishable_key:
            raise DashboardMockError(
                code="UNAUTHORIZED",
                message="Dashboard Supabase auth is not configured.",
                status_code=HTTPStatus.UNAUTHORIZED,
                retryable=False,
                details={
                    "missing": [
                        name
                        for name, value in {
                            "supabase_url": settings.supabase_url,
                            "supabase_publishable_key": settings.supabase_publishable_key,
                        }.items()
                        if not value
                    ]
                },
            )
        self.settings = settings
        self.supabase_url = settings.supabase_url.rstrip("/")
        self.supabase_publishable_key = settings.supabase_publishable_key

    async def authenticate(self, token: str) -> DashboardPrincipal:
        async with httpx.AsyncClient(timeout=8.0) as client:
            user = await self._fetch_user(client, token)
            user_id = _text(user.get("id"))
            if not user_id:
                raise _unauthorized("Supabase token did not resolve to a user.")
            profile = await self._fetch_profile(client, token, user_id)

        tier = _text(profile.get("tier")).upper()
        if tier != self.settings.dev_tier_value:
            raise DashboardMockError(
                code="FORBIDDEN",
                message="Current user does not have Dashboard dev access.",
                status_code=HTTPStatus.FORBIDDEN,
                retryable=False,
            )
        return DashboardPrincipal(
            user_id=user_id,
            email=_text(user.get("email")) or None,
            tier=tier,
            timezone=_text(profile.get("timezone")) or None,
            auth_mode=self.settings.auth_mode,
        )

    async def _fetch_user(self, client: httpx.AsyncClient, token: str) -> JsonObject:
        response = await client.get(
            f"{self.supabase_url}/auth/v1/user",
            headers=self._headers(token),
        )
        if response.status_code in {HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN}:
            raise _unauthorized("Supabase token is invalid or expired.")
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise _unauthorized("Supabase user verification failed.") from exc
        payload = response.json()
        if not isinstance(payload, dict):
            raise _unauthorized("Supabase user verification returned an invalid payload.")
        return cast(JsonObject, payload)

    async def _fetch_profile(
        self,
        client: httpx.AsyncClient,
        token: str,
        user_id: str,
    ) -> JsonObject:
        response = await client.get(
            f"{self.supabase_url}/rest/v1/{self.settings.user_profiles_table}",
            params={
                "select": "tier,timezone",
                "user_id": f"eq.{user_id}",
                "limit": "1",
            },
            headers=self._headers(token) | {"Accept": "application/json"},
        )
        if response.status_code in {HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN}:
            raise _unauthorized("Supabase profile lookup was rejected.")
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise _unauthorized("Supabase profile lookup failed.") from exc
        payload = response.json()
        if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
            raise DashboardMockError(
                code="FORBIDDEN",
                message="Current user does not have a Dashboard dev profile.",
                status_code=HTTPStatus.FORBIDDEN,
                retryable=False,
            )
        return cast(JsonObject, payload[0])

    def _headers(self, token: str) -> dict[str, str]:
        return {
            "apikey": self.supabase_publishable_key,
            "Authorization": f"Bearer {token}",
        }


def _settings_from_request(request: Request) -> DashboardAuthSettings:
    settings = getattr(request.app.state, "dashboard_auth_settings", None)
    if isinstance(settings, DashboardAuthSettings):
        return settings
    default_auth_mode = str(getattr(request.app.state, "dashboard_auth_mode", "supabase"))
    return dashboard_auth_settings_from_env(default_auth_mode=default_auth_mode)


def _bearer_token(request: Request) -> str:
    authorization = request.headers.get("authorization", "")
    if not authorization.startswith("Bearer "):
        raise _unauthorized("Missing Authorization bearer token.")
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise _unauthorized("Missing Authorization bearer token.")
    return token


def _unauthorized(message: str) -> DashboardMockError:
    return DashboardMockError(
        code="UNAUTHORIZED",
        message=message,
        status_code=HTTPStatus.UNAUTHORIZED,
        retryable=False,
    )


def _first_env(*names: str) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value and value.strip():
            return value.strip()
    return None


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""
