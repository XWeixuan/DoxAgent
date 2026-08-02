"""Low-level contracts shared by N6 identity, N7 ranking, and N9 adjudication."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from cdecr.contracts import StrictModel

ATOMIC_IDENTITY_SIDECAR_VERSION = "atomic-identity-sidecar-v2-trusted-fields"


class AtomicIdentityAdapterKind(StrEnum):
    FINANCIAL_GUIDANCE = "FINANCIAL_GUIDANCE"
    MARKET_MOVEMENT = "MARKET_MOVEMENT"
    ACTION_ARTIFACT = "ACTION_ARTIFACT"
    OUTLOOK_STATE = "OUTLOOK_STATE"
    GENERIC_OPEN = "GENERIC_OPEN"


class IdentityAxis(StrEnum):
    REFERENT = "REFERENT"
    OCCURRENCE = "OCCURRENCE"
    FACET = "FACET"


class IdentityAxisVerdict(StrEnum):
    MATCH = "MATCH"
    CONFLICT = "CONFLICT"
    AMBIGUOUS = "AMBIGUOUS"


class IdentityAxisAssessment(StrictModel):
    axis: IdentityAxis
    verdict: IdentityAxisVerdict


class AtomicIdentitySidecar(StrictModel):
    adapter_kind: AtomicIdentityAdapterKind
    referent: list[str] = Field(default_factory=list)
    occurrence: list[str] = Field(default_factory=list)
    facet: list[str] = Field(default_factory=list)
    applicable_axes: list[IdentityAxis] = Field(default_factory=list)
    compiler_version: str = ATOMIC_IDENTITY_SIDECAR_VERSION
    signature_hash: str

    @model_validator(mode="after")
    def validate_axes(self) -> AtomicIdentitySidecar:
        expected = [
            axis
            for axis, values in (
                (IdentityAxis.REFERENT, self.referent),
                (IdentityAxis.OCCURRENCE, self.occurrence),
                (IdentityAxis.FACET, self.facet),
            )
            if values
        ]
        if self.applicable_axes != expected:
            raise ValueError("applicable_axes must exactly cover non-empty sidecar axes")
        return self

    def values(self, axis: IdentityAxis) -> frozenset[str]:
        return frozenset(getattr(self, axis.value.casefold()))
