"""Strict contracts for website strategy governance and the access owner."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

JsonObject = dict[str, Any]
AccessOrderItem = Literal["http_public", "browser", "browser_fetch", "reader"]
AuthRequirement = Literal["none", "optional", "required"]
VerificationKind = Literal["public_access", "subscription_article"]


def default_access_order() -> list[AccessOrderItem]:
    return ["http_public", "browser", "reader"]


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def digest_json(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def normalize_host(value: str) -> str:
    result = value.strip().rstrip(".").casefold()
    if not result or "/" in result or ":" in result:
        raise ValueError("host must contain only a DNS hostname")
    try:
        result = result.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError("host is not valid IDNA") from exc
    if len(result) > 253 or any(
        not label or len(label) > 63 or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label)
        for label in result.split(".")
    ):
        raise ValueError("host is not a valid DNS hostname")
    return result


class SiteModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MatchType(StrEnum):
    EXACT = "exact"
    SUFFIX = "suffix"


class DomainRole(StrEnum):
    PUBLISHER = "publisher"
    API = "api"
    ASSET = "asset"
    LOGIN = "login"


class DomainRule(SiteModel):
    match: MatchType = MatchType.EXACT
    host: str
    role: DomainRole = DomainRole.PUBLISHER
    exclude: bool = False

    @field_validator("host")
    @classmethod
    def _host(cls, value: str) -> str:
        return normalize_host(value)

    def matches(self, host: str) -> bool:
        target = normalize_host(host)
        return target == self.host or (
            self.match is MatchType.SUFFIX and target.endswith("." + self.host)
        )


class AccessCombination(SiteModel):
    combination_id: str = Field(alias="id", min_length=1, max_length=128)
    profile_id: str = Field(min_length=1, max_length=128)
    egress_id: str = Field(min_length=1, max_length=128)
    priority: int = Field(default=100, ge=0, le=10_000)
    enabled: bool = True

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    @field_validator("combination_id", "profile_id", "egress_id")
    @classmethod
    def _identifier(cls, value: str) -> str:
        result = value.strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]*", result):
            raise ValueError("invalid resource identifier")
        return result


class AccessPolicy(SiteModel):
    combinations: list[AccessCombination] = Field(default_factory=list)
    overrides: dict[Literal["body", "crawler"], list[str]] = Field(default_factory=dict)
    probe_url: str | None = None
    max_concurrency: int = Field(default=2, ge=1, le=8)
    min_interval_ms: int = Field(default=500, ge=0, le=60_000)
    body_queue_timeout_ms: int = Field(default=30_000, ge=1, le=180_000)
    crawler_queue_timeout_ms: int = Field(default=10_000, ge=1, le=180_000)

    @field_validator("probe_url")
    @classmethod
    def _probe_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlsplit(value.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("probe_url must be absolute HTTP(S)")
        return value.strip()

    @model_validator(mode="after")
    def _unique_combinations(self) -> AccessPolicy:
        ids = [item.combination_id for item in self.combinations]
        if len(ids) != len(set(ids)):
            raise ValueError("access combination ids must be unique")
        known = set(ids)
        for purpose, values in self.overrides.items():
            if not values or not set(values) <= known:
                raise ValueError(f"{purpose} override references an unknown combination")
        return self


class StrategyPointer(SiteModel):
    ref: str = Field(min_length=1, max_length=256)
    parameters: JsonObject = Field(default_factory=dict)


class BodyStrategyPointer(StrategyPointer):
    access_order: list[AccessOrderItem] = Field(default_factory=default_access_order)


class AuthPolicy(SiteModel):
    requirement: AuthRequirement = "none"
    crawler_requirement: Literal["inherit", "none", "optional", "required"] = "inherit"
    body_requirement: Literal["inherit", "none", "optional", "required"] = "inherit"
    login_url: str | None = None
    maintenance_url: str | None = None
    verification_url: str | None = None
    verification_kind: VerificationKind = "subscription_article"

    @field_validator("login_url", "maintenance_url", "verification_url")
    @classmethod
    def _absolute_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlsplit(value.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("auth URLs must be absolute HTTP(S)")
        return value.strip()


class SiteStrategySpec(SiteModel):
    site_id: str
    revision: int = Field(default=0, ge=0)
    display_name: str | None = None
    domains: list[DomainRule] = Field(default_factory=list)
    support_hosts: list[DomainRule] = Field(default_factory=list)
    access: AccessPolicy = Field(default_factory=AccessPolicy)
    crawler: StrategyPointer | None = None
    body: BodyStrategyPointer = Field(
        default_factory=lambda: BodyStrategyPointer(ref="builtin:generic@1")
    )
    auth: AuthPolicy = Field(default_factory=AuthPolicy)

    @field_validator("site_id")
    @classmethod
    def _site_id(cls, value: str) -> str:
        result = value.strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]*", result):
            raise ValueError("invalid site_id")
        return result

    @model_validator(mode="after")
    def _rules(self) -> SiteStrategySpec:
        if self.site_id != "generic" and not self.domains:
            raise ValueError("registered site requires at least one publisher domain rule")
        if any(item.role not in {DomainRole.PUBLISHER, DomainRole.API} for item in self.domains):
            raise ValueError("domains may only contain publisher or api roles")
        if any(item.exclude for item in self.support_hosts):
            raise ValueError("support host rules cannot be exclusions")
        return self

    def with_revision(self, revision: int) -> SiteStrategySpec:
        return self.model_copy(update={"revision": revision})


class SiteStrategyHead(SiteModel):
    site_id: str
    active_revision: int
    enabled: bool = True
    updated_at: datetime = Field(default_factory=utc_now)


class ProxyEgress(SiteModel):
    egress_id: str
    node_ref: str
    node_fingerprint: str
    listener_port: int = Field(ge=1, le=65_535)
    endpoint: str
    enabled: bool = True
    observed_ip: str | None = None
    observed_at: datetime | None = None
    probe_endpoint: str | None = None
    generation: int = Field(default=1, ge=1)
    status: Literal["READY", "UNVERIFIED", "CONFIG_MISSING", "UNAVAILABLE"] = "UNVERIFIED"

    @field_validator("egress_id")
    @classmethod
    def _egress_id(cls, value: str) -> str:
        result = value.strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]*", result):
            raise ValueError("invalid egress_id")
        return result

    @field_validator("endpoint")
    @classmethod
    def _endpoint(cls, value: str) -> str:
        parsed = urlsplit(value.strip())
        if parsed.scheme not in {"http", "https", "socks5", "socks5h"} or not parsed.hostname:
            raise ValueError("egress endpoint must be an HTTP(S) or SOCKS proxy URL")
        if parsed.username or parsed.password:
            raise ValueError("egress endpoint must not embed credentials")
        return value.strip()


class AuthState(StrEnum):
    UNKNOWN = "UNKNOWN"
    MAINTENANCE = "MAINTENANCE"
    VALID = "VALID"
    REAUTH_REQUIRED = "REAUTH_REQUIRED"
    ENTITLEMENT_MISSING = "ENTITLEMENT_MISSING"


class ProfileOperationalState(StrEnum):
    AVAILABLE = "AVAILABLE"
    DRAINING_FOR_MAINTENANCE = "DRAINING_FOR_MAINTENANCE"
    MAINTENANCE = "MAINTENANCE"
    DRAINING_FOR_SHUTDOWN = "DRAINING_FOR_SHUTDOWN"


class BrowserEnvironment(SiteModel):
    """Stable, non-randomized browser environment bound to a Profile."""

    locale: str = Field(default="en-US", pattern=r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")
    timezone_id: str = Field(default="Asia/Singapore", min_length=1, max_length=64)
    window_width: int = Field(default=1440, ge=800, le=3840)
    window_height: int = Field(default=1000, ge=600, le=2160)
    revision: int = Field(default=1, ge=1)


class BrowserProfile(SiteModel):
    profile_id: str
    site_id: str
    bound_egress_id: str
    directory_key: str
    credential_ref: str | None = None
    login_url: str | None = None
    auth_state: AuthState = AuthState.UNKNOWN
    operational_state: ProfileOperationalState = ProfileOperationalState.AVAILABLE
    operational_revision: int = Field(default=0, ge=0)
    maintenance_session_id: str | None = Field(default=None, max_length=128)
    environment: BrowserEnvironment = Field(default_factory=BrowserEnvironment)
    session_revision: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("profile_id", "site_id", "bound_egress_id", "directory_key")
    @classmethod
    def _id(cls, value: str) -> str:
        result = value.strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]*", result):
            raise ValueError("invalid profile identifier")
        return result


class CombinationRuntime(SiteModel):
    # The legacy fields are the browser lane state and remain wire-compatible.
    state: Literal["READY", "COOLDOWN", "HALF_OPEN"] = "READY"
    risk_strikes: int = Field(default=0, ge=0)
    cooldown_until: datetime | None = None
    last_failure: str | None = None
    last_failure_at: datetime | None = None
    last_success_at: datetime | None = None
    probe_in_flight: bool = False
    manual_attention_required: bool = False
    last_browser_result: str | None = None
    last_browser_at: datetime | None = None
    http_state: Literal["READY", "COOLDOWN"] = "READY"
    http_risk_strikes: int = Field(default=0, ge=0)
    http_cooldown_until: datetime | None = None
    last_http_result: str | None = None
    last_http_at: datetime | None = None


class SiteRuntimeState(SiteModel):
    runtime_key: str
    active_combination_id: str | None = None
    combinations: dict[str, CombinationRuntime] = Field(default_factory=dict)
    generation: int = Field(default=0, ge=0)
    updated_at: datetime = Field(default_factory=utc_now)


class SitePurpose(StrEnum):
    BODY = "BODY"
    CRAWLER = "CRAWLER"
    PROBE = "PROBE"
    LOGIN = "LOGIN"


class AccessMode(StrEnum):
    HTTP_PUBLIC = "HTTP_PUBLIC"
    BROWSER = "BROWSER"
    BROWSER_FETCH = "BROWSER_FETCH"


class AccessDisposition(StrEnum):
    SUCCESS = "SUCCESS"
    ACCESS_EXHAUSTED = "ACCESS_EXHAUSTED"
    CONTENT_ERROR = "CONTENT_ERROR"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    REDIRECT_REQUIRED = "REDIRECT_REQUIRED"
    BUDGET_DEFERRED = "BUDGET_DEFERRED"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    SITE_DISABLED = "SITE_DISABLED"


class FailureCategory(StrEnum):
    ACCESS_RATE_LIMIT = "ACCESS_RATE_LIMIT"
    ACCESS_CHALLENGE = "ACCESS_CHALLENGE"
    ACCESS_BLOCK = "ACCESS_BLOCK"
    AUTH_OR_ACCESS_UNKNOWN = "AUTH_OR_ACCESS_UNKNOWN"
    REGION_RESTRICTED = "REGION_RESTRICTED"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    ENTITLEMENT_MISSING = "ENTITLEMENT_MISSING"
    CONTENT_ERROR = "CONTENT_ERROR"
    EXTRACTION_ERROR = "EXTRACTION_ERROR"
    EMPTY_SUCCESS = "EMPTY_SUCCESS"
    TRANSIENT_TRANSPORT = "TRANSIENT_TRANSPORT"
    EGRESS_UNAVAILABLE = "EGRESS_UNAVAILABLE"
    RUNTIME_UNAVAILABLE = "RUNTIME_UNAVAILABLE"
    BUDGET_DEFERRED = "BUDGET_DEFERRED"
    UNKNOWN = "UNKNOWN"


class ResolvedSite(SiteModel):
    site_id: str
    runtime_key: str
    strategy_revision: int
    enabled: bool = True
    match_evidence: JsonObject = Field(default_factory=dict)
    body: BodyStrategyPointer
    crawler: StrategyPointer | None = None
    auth: AuthPolicy = Field(default_factory=AuthPolicy)
    access: AccessPolicy


class AccessRequest(SiteModel):
    request_id: str = Field(default_factory=lambda: new_id("access"))
    operation_id: str
    parent_id: str | None = None
    purpose: SitePurpose
    url: str
    method: Literal["GET", "POST"] = "GET"
    parameters: JsonObject = Field(default_factory=dict)
    allowed_headers: dict[str, str] = Field(default_factory=dict)
    mode: AccessMode
    recipe_ref: str | None = None
    recipe_parameters: JsonObject = Field(default_factory=dict)
    strategy_revision: int | None = Field(default=None, ge=1)
    remaining_budget_ms: int = Field(default=30_000, ge=1, le=180_000)
    max_response_bytes: int = Field(default=8_000_000, ge=1, le=20_000_000)
    excluded_combinations: list[str] = Field(default_factory=list)

    @field_validator("url")
    @classmethod
    def _url(cls, value: str) -> str:
        parsed = urlsplit(value.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("url must be absolute HTTP(S)")
        if parsed.username or parsed.password:
            raise ValueError("url must not contain credentials")
        return value.strip()

    @field_validator("allowed_headers")
    @classmethod
    def _headers(cls, value: dict[str, str]) -> dict[str, str]:
        forbidden = {"authorization", "cookie", "proxy-authorization", "host", "connection"}
        if any(key.casefold() in forbidden for key in value):
            raise ValueError("request includes a forbidden header")
        return value


class AccessAttempt(SiteModel):
    combination_id: str
    generation: int
    transport: AccessMode
    status_code: int | None = None
    failure_category: FailureCategory | None = None
    reason_code: str | None = None
    queue_wait_ms: int = 0
    network_ms: int = 0
    exit_ip: str | None = None
    observed_at: datetime = Field(default_factory=utc_now)


class AccessResult(SiteModel):
    request_id: str
    operation_id: str
    disposition: AccessDisposition
    status_code: int | None = None
    final_url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    body: str = ""
    recipe_result: JsonObject | list[JsonObject] | None = None
    failure_category: FailureCategory | None = None
    reason_code: str | None = None
    retry_not_before: datetime | None = None
    redirect_url: str | None = None
    site_id: str
    runtime_key: str
    strategy_revision: int
    body_strategy_ref: str | None = None
    combination_id: str | None = None
    generation: int = 0
    profile_id: str | None = None
    egress_id: str | None = None
    exit_ip_observation: str | None = None
    attempts: list[AccessAttempt] = Field(default_factory=list)
    queue_wait_ms: int = 0
    network_ms: int = 0


class AccessEvent(SiteModel):
    event_id: str = Field(default_factory=lambda: new_id("sae"))
    operation_id: str
    site_id: str
    combination_id: str | None = None
    occurred_at: datetime = Field(default_factory=utc_now)
    category: str
    payload: JsonObject = Field(default_factory=dict)


class BodyOutcome(SiteModel):
    job_id: str
    final_site_id: str
    strategy_ref: str
    combination_id: str | None = None
    outcome: str
    reason: str | None = None
    completed_at: datetime = Field(default_factory=utc_now)
    payload: JsonObject = Field(default_factory=dict)


__all__ = [name for name in globals() if not name.startswith("_")]
