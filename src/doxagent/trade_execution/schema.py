"""Versioned execution configuration; no model or research dependencies."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Strategy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
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


class ExecutionProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    profile_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    environment: Literal["PAPER", "LIVE"]
    account_type: Literal["CASH", "MARGIN"] = "MARGIN"
    host: str = "127.0.0.1"
    port: int = Field(ge=1, le=65535)
    client_id: int = Field(ge=1)
    expected_account_id: str = Field(min_length=2)
    long_enabled: bool = True
    short_enabled: bool = True
    quote_profile_revision: str | None = None
    strategy: Strategy = Field(default_factory=Strategy)

    @model_validator(mode="after")
    def validate_environment(self) -> Any:
        # Diagnose a definite reversed mapping at bootstrap; exact account matching
        # remains the submit-time fuse (ports alone never identify an environment).
        if (self.environment == "PAPER" and self.expected_account_id.startswith("U")) or (
            self.environment == "LIVE" and self.expected_account_id.startswith("DU")
        ):
            raise ValueError("ACCOUNT_ENVIRONMENT_MISMATCH")
        return self


def historical_profile(payload: dict) -> ExecutionProfile:
    """Archive decoding for reconciliation/EXIT; no LEGACY opening path."""
    value = dict(payload)
    old = value.pop("account_mode", None)
    value.setdefault("account_type", "CASH" if old == "LIVE_CASH" else "MARGIN")
    strategy = dict(value.get("strategy") or {})
    strategy.pop("capital_model", None)
    strategy.pop("target_notional_usd", None)
    value["strategy"] = strategy
    return ExecutionProfile.model_validate(value)


def account_fuse(profile: ExecutionProfile, accounts: list[str]) -> None:
    if profile.expected_account_id not in accounts:
        raise ValueError("ACCOUNT_MISMATCH")


def legacy_entry_profile(payload: dict) -> bool:
    model = payload.get("strategy", {}).get("capital_model")
    return model == "LEGACY_PER_INTENT" or (
        "account_type" not in payload and model != "SHARED_CYCLE"
    )
