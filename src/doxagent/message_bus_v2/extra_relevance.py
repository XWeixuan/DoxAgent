"""Optional binding-owned second gate using the native term syntax and matcher."""

from __future__ import annotations

import json
import re
from types import SimpleNamespace
from typing import TYPE_CHECKING

from .monitoring_terms import L2Language, validate_rule_map
from .relevance import _match, _normalize

if TYPE_CHECKING:
    from .schema import JsonObject, RawMessageInput, TickerSourceBinding


def validate_extra(value: JsonObject) -> JsonObject:
    rules = L2Language.model_validate(value)
    if not rules.groups:
        raise ValueError("extra_relevance needs nonempty groups; use null to remove")
    # A binding already identifies its source/languages, so no language dispatch is needed.
    validate_rule_map({"binding": rules}, "extra_relevance")
    compact = rules.model_dump(mode="json", exclude_defaults=True, exclude_none=True)
    if len(json.dumps(compact, ensure_ascii=False).encode("utf-8")) > 24576:
        raise ValueError("extra_relevance exceeds 24 KiB")
    return rules.model_dump(mode="json")


def extra_allows(binding: TickerSourceBinding, message: RawMessageInput) -> bool:
    if binding.extra_relevance is None:
        return True
    endings = {
        "huggingnews_rss": r"Earlier version from|You.re reading an older version|Continues from|Polymarket odds",
        "technews_hardware": r"延伸閱讀\s*[:：]?|文章看完覺得有幫助",
        "tomshardware_rss": r"If you.re looking for more[^.]{0,40}savings|Download the Tom.s Hardware app|Follow Tom.s Hardware on Google News",
        "quantum_zeitgeist": r"See today.s quantum computing news on Quantum Zeitgeist",
    }
    ending = endings.get(binding.source_id)
    if ending:
        # Clean only this optional gate's view, once. Keep original publication
        # content and first-layer regex/model inputs unchanged.
        message = SimpleNamespace(title=message.title, **{
            field: re.split(ending, _normalize(getattr(message, field)), maxsplit=1, flags=re.I)[0]
            for field in ("summary", "body")
        })
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
