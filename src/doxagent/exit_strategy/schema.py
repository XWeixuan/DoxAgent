"""Immutable exit policy, separate from the frozen execution profile."""

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ExitPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    policy_id: str = "mfe50_atr2p5_v1"
    activation_fraction: Decimal = Field(default=Decimal("0.05"), gt=0, lt=1)
    giveback_fraction: Decimal = Field(default=Decimal("0.50"), gt=0, lt=1)
    atr_multiplier: Decimal = Field(default=Decimal("2.5"), gt=0)
    atr_period: Literal[20] = 20
    observation_seconds: Literal[5] = 5
    confirmation_seconds: Literal[30] = 30
    required_breaches: Literal[5] = 5
    mfe_filter_samples: Literal[3] = 3
    quote_max_age_seconds: float = Field(default=5, gt=0, le=5)
    max_spread_fraction: Decimal = Field(default=Decimal("0.005"), gt=0, lt=1)
    min_samples_per_minute: Literal[6] = 6
    borrow_rate: Decimal = Field(default=Decimal("0.02"), ge=0)
    financing_rate: Decimal = Field(default=Decimal("0"), ge=0)


EXIT_REASONS = {
    "TRAILING_PROFIT",
    "SESSION_CUTOFF",
    "HARD_RISK",
    "MESSAGE_INVALID",
    "RESIDUAL_GROUP_EXIT",
}
