"""Optional binding-owned second gate using the native term syntax and matcher."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from .monitoring_terms import L2Language, validate_rule_map
from .relevance import _match

if TYPE_CHECKING:
    from .schema import JsonObject, RawMessageInput, TickerSourceBinding


def validate_extra(value: JsonObject) -> JsonObject:
    rules = L2Language.model_validate(value)
    if not rules.groups:
        raise ValueError("extra_relevance needs nonempty groups; use null to remove")
    # A binding already identifies its source/languages, so no language dispatch is needed.
    validate_rule_map({"binding": rules}, "extra_relevance")
    result = rules.model_dump(mode="json")
    if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > 16384:
        raise ValueError("extra_relevance exceeds 16 KiB")
    return result


def extra_allows(binding: TickerSourceBinding, message: RawMessageInput) -> bool:
    if binding.extra_relevance is None:
        return True
    rules = L2Language.model_validate(binding.extra_relevance)
    # A timeout must never turn a negative condition into a successful exclusion check.
    # Propagate it to the existing delivery retry path; do not publish or call a model here.
    for group in rules.groups:
        if group.any and not any(_match(term, message, strict=True) for term in group.any):
            continue
        if not all(_match(term, message, strict=True) for term in group.all):
            continue
        if any(_match(term, message, strict=True) for term in group.none):
            continue
        return True
    return False
