"""Versioned execution configuration; no model or research dependencies."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Strategy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    capital_model: Literal["LEGACY_PER_INTENT", "SHARED_CYCLE"] = "SHARED_CYCLE"
    target_notional_usd: Decimal | None = Field(default=None, gt=0)
    min_entry_notional_ratio: Decimal = Field(default=Decimal("0.25"), gt=0, le=1)
    initial_tolerance: Decimal = Field(default=Decimal("0.005"), ge=0, lt=1)
    non_rth_retry_tolerance: Decimal = Field(default=Decimal("0.01"), ge=0, lt=1)
    order_wait_seconds: float = Field(default=5, gt=0, le=60)
    rth_max_retries: Literal[1] = 1
    non_rth_max_retries: Literal[2] = 2
    exit_offset_minutes: int = Field(default=30, gt=0, lt=180)
    fractional_shares: Literal[False] = False
    quote_max_age_seconds: float = Field(default=5, gt=0, le=60)
    request_timeout_seconds: float = Field(default=5, gt=0, le=30)

    @model_validator(mode="before")
    @classmethod
    def recognize_historical_profile(cls, value: Any) -> Any:
        if (
            isinstance(value, dict)
            and "capital_model" not in value
            and value.get("target_notional_usd")
        ):
            return {**value, "capital_model": "LEGACY_PER_INTENT"}
        return value

    @model_validator(mode="after")
    def validate_capital_model(self) -> Strategy:
        if self.capital_model == "LEGACY_PER_INTENT" and self.target_notional_usd is None:
            raise ValueError("legacy capital model requires target_notional_usd")
        if self.capital_model == "SHARED_CYCLE" and self.target_notional_usd is not None:
            raise ValueError("shared cycle must not set target_notional_usd")
        return self


class ExecutionProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    profile_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    environment: Literal["PAPER", "LIVE"]
    account_mode: Literal["PAPER", "LIVE_CASH", "LIVE_MARGIN"]
    host: str = "127.0.0.1"
    port: int = Field(ge=1, le=65535)
    client_id: int = Field(ge=1)
    expected_account_id: str = Field(min_length=2)
    long_enabled: bool = True
    short_enabled: bool = False
    quote_profile_revision: str | None = None
    strategy: Strategy = Field(default_factory=Strategy)

    @model_validator(mode="after")
    def validate_environment(self) -> Any:
        if (self.environment == "PAPER") != (self.account_mode == "PAPER"):
            raise ValueError("environment/account_mode mismatch")
        if self.account_mode == "LIVE_CASH" and self.short_enabled:
            raise ValueError("LIVE_CASH cannot enable SHORT entries")
        # Diagnose a definite reversed mapping at bootstrap; exact account matching
        # remains the submit-time fuse (ports alone never identify an environment).
        if (self.environment == "PAPER" and self.expected_account_id.startswith("U")) or (
            self.environment == "LIVE" and self.expected_account_id.startswith("DU")
        ):
            raise ValueError("ACCOUNT_ENVIRONMENT_MISMATCH")
        return self


def account_fuse(profile: ExecutionProfile, accounts: list[str]) -> None:
    if profile.expected_account_id not in accounts:
        raise ValueError("ACCOUNT_MISMATCH")
