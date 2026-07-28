"""N5 mention finalization without object identity resolution."""

from __future__ import annotations

import hashlib
import json
import re
from importlib import resources

from cdecr.contracts import EventMention, Quantity
from cdecr.single_document_contracts import (
    NormalizationDecision,
    NormalizationKind,
    NormalizationMethod,
)

FINALIZATION_VERSION = "mention-finalization-v3"


class MentionFinalizer:
    """Apply deterministic scalar normalization while preserving object fields."""

    def __init__(self) -> None:
        path = resources.files("cdecr.catalogs.v1").joinpath("units.json")
        payload = json.loads(path.read_text(encoding="utf-8"))
        self._currencies = {
            str(key).casefold(): str(value) for key, value in payload["currencies"].items()
        }
        self._units = {str(key).casefold(): str(value) for key, value in payload["units"].items()}
        self._multipliers = {
            str(key).casefold(): float(value) for key, value in payload["multipliers"].items()
        }

    def finalize(self, mention: EventMention) -> tuple[EventMention, list[NormalizationDecision]]:
        quantities: list[Quantity] = []
        decisions: list[NormalizationDecision] = []
        for index, quantity in enumerate(mention.quantities):
            normalized = self._normalize_quantity(quantity)
            quantities.append(normalized)
            decisions.append(
                NormalizationDecision(
                    decision_id=_decision_id(mention.mention_id, f"quantities.{index}"),
                    mention_id=mention.mention_id,
                    field_path=f"quantities.{index}",
                    kind=NormalizationKind.QUANTITY,
                    raw_value=quantity.model_dump(mode="json"),
                    normalized_value=normalized.model_dump(mode="json"),
                    method=NormalizationMethod.M0_EXACT,
                    candidates=[],
                )
            )
        # Participants, predicates, periods, metrics and projections are object fields.
        # N5 deliberately leaves them untouched for N5.5 canonical field resolution.
        return mention.model_copy(update={"quantities": quantities}), decisions

    def _normalize_quantity(self, quantity: Quantity) -> Quantity:
        raw_lower = quantity.raw_text.casefold()
        unit_tokens = set(re.split(r"[^a-z]+", quantity.unit.casefold()))
        normalized_unit = quantity.unit.strip().upper()
        for token, canonical in self._currencies.items():
            if token in raw_lower or token == quantity.unit.casefold() or token in unit_tokens:
                normalized_unit = canonical
                break
        for token, canonical in self._units.items():
            if token in raw_lower or token == quantity.unit.casefold() or token in unit_tokens:
                normalized_unit = canonical
                break
        multiplier = 1.0
        for token, value in self._multipliers.items():
            pattern = (
                rf"(?:\d|\.)\s*{re.escape(token)}\b"
                if len(token) == 1
                else rf"\b{re.escape(token)}\b"
            )
            if re.search(pattern, raw_lower, re.I) or token in unit_tokens:
                multiplier = value
                break
        normalized_value: int | float = quantity.value
        if multiplier > 1 and abs(float(quantity.value)) < multiplier:
            expanded = float(quantity.value) * multiplier
            normalized_value = int(expanded) if expanded.is_integer() else expanded
        return quantity.model_copy(update={"value": normalized_value, "unit": normalized_unit})


def _decision_id(mention_id: str, field_path: str) -> str:
    digest = hashlib.sha256(
        f"{FINALIZATION_VERSION}\0{mention_id}\0{field_path}".encode()
    ).hexdigest()[:24]
    return f"normalization:{digest}"
