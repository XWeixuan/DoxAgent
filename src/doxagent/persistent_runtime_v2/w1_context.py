"""Deterministic, three-column recall view. Full facts remain available to W1R2."""

from collections.abc import Iterable

from doxagent.event_library.compiler import _wire_cell

from .schema import ProvisionalFactDetail

PROVISIONAL_VIEW_VERSION = "provisional-md-v1"


def provisional_fact_line(event_id: str, occurred_on: str | None, proposition: str) -> str:
    return (
        " | ".join((_wire_cell(event_id), occurred_on or "UNKNOWN", _wire_cell(proposition))) + "\n"
    )


def compile_provisional_view(facts: Iterable[ProvisionalFactDetail]) -> str:
    return "".join(
        provisional_fact_line(
            fact.provisional_event_id,
            fact.candidate.occurrence_date.isoformat() if fact.candidate.occurrence_date else None,
            fact.candidate.proposition,
        )
        for fact in facts
    )
