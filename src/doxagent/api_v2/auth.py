"""Supabase Auth plus the existing trusted developer profile, with bounded caching.

There is no public/mock mode. Offline tests inject a verifier explicitly.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx

from .errors import ApiFailure


@dataclass(frozen=True)
class Principal:
    user_id: str
    tier: str
    expires_at: float

    def wire(self) -> dict[str, Any]:
        return {"user_id": self.user_id, "tier": self.tier, "can_read": True, "can_operate": True}


class SupabaseAuth:
    def __init__(
        self,
        url: str,
        publishable_key: str,
        *,
        profile_table: str = "user_profiles",
        developer_tier: str = "DEVELOPER",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not url.startswith("https://") or not publishable_key:
            raise ValueError("V2 Supabase authentication must be configured")
        if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", profile_table):
            raise ValueError("invalid profile table")
        # A privileged key must never appear in auth-config.
        if publishable_key.startswith("sb_secret_"):
            raise ValueError("publishable authentication key required")
        if publishable_key.count(".") == 2:
            try:
                part = publishable_key.split(".")[1]
                role = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))).get(
                    "role"
                )
                if role != "anon":
                    raise ValueError("anon authentication key required")
            except (ValueError, KeyError, TypeError) as exc:
                raise ValueError("invalid public authentication key") from exc
        self.url, self.key, self.table, self.tier = (
            url.rstrip("/"),
            publishable_key,
            profile_table,
            developer_tier,
        )
        self.client = client or httpx.AsyncClient(timeout=10, follow_redirects=False)
        self._owns_client = client is None
        self.cache: dict[str, tuple[float, Principal]] = {}

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    def configuration(self) -> dict[str, str]:
        return {
            "provider": "supabase",
            "supabase_url": self.url,
            "supabase_publishable_key": self.key,
        }

    async def authenticate(self, token: str) -> Principal:
        now = time.time()
        try:
            if len(token) > 16384 or token.count(".") != 2:
                raise ValueError("invalid token")
            part = token.split(".")[1]
            claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
            expires = float(claims["exp"])
            if not math.isfinite(expires) or expires <= now:
                raise ValueError("expired token")
        except (ValueError, KeyError, TypeError, OverflowError):
            raise ApiFailure("UNAUTHORIZED", 401) from None
        # exp is only a rejection/cache bound; Auth validates identity cryptographically.
        digest = hashlib.sha256(token.encode()).hexdigest()
        cached = self.cache.get(digest)
        if cached and cached[0] > now:
            return cached[1]
        headers = {"Authorization": f"Bearer {token}", "apikey": self.key}
        try:
            result = await self.client.get(self.url + "/auth/v1/user", headers=headers)
            if result.status_code in {401, 403}:
                raise ApiFailure("UNAUTHORIZED", 401)
            result.raise_for_status()
            user_id = result.json()["id"]
            result = await self.client.get(
                self.url + "/rest/v1/" + self.table,
                headers=headers,
                params={"select": "tier", "user_id": "eq." + user_id, "limit": "1"},
            )
            result.raise_for_status()
            profiles = result.json()
            if not profiles or profiles[0].get("tier") != self.tier:
                self.cache.pop(digest, None)
                raise ApiFailure("FORBIDDEN", 403)
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            raise ApiFailure("AUTH_UNAVAILABLE", 503, retryable=True) from None
        principal = Principal(user_id, self.tier, expires)
        if len(self.cache) >= 2048:
            self.cache = {key: value for key, value in self.cache.items() if value[0] > now}
            if len(self.cache) >= 2048:
                self.cache.pop(next(iter(self.cache)))
        self.cache[digest] = (min(expires, now + 30), principal)
        return principal
