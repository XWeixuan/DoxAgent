from __future__ import annotations

import pytest
from pydantic import ValidationError

from doxagent.event_library.compiler import (
    EventLibraryViewCompiler,
    _display_occurrence_time,
    _reference_fact_lines,
    _summary_duplicates_title,
)
from doxagent.event_library.contracts import (
    CanonicalEvent,
    CanonicalFact,
    CanonicalSubjectTimeMarker,
)


def test_occurrence_timestamp_uses_eastern_calendar_date_and_dst() -> None:
    assert _display_occurrence_time("2026-08-19T00:00:00Z") == "2026-08-18"
    assert _display_occurrence_time("2026-01-15T05:00:00+00:00") == "2026-01-15"
    assert _display_occurrence_time("2026-08-19") == "2026-08-19"
    assert _display_occurrence_time("2026-08-19T12:00:00") == "2026-08-19T12:00:00"
    assert _display_occurrence_time("Q3") == "Q3"


def test_known_summary_omission_is_conservative() -> None:
    assert _summary_duplicates_title(
        title="Analog Devices executive appointment",
        summary="Analog Devices executive appointment occurrence.",
    )
    assert _summary_duplicates_title(
        title="Analog Devices executive appointment",
        summary="ANALOG DEVICES — executive appointment!",
    )
    assert not _summary_duplicates_title(
        title="Analog Devices Q3 earnings release",
        summary="Analog Devices Q3 earnings release raised Q4 revenue guidance to $4.3B.",
    )


def test_canonical_fact_contract_rejects_retired_entities_field() -> None:
    with pytest.raises(ValidationError, match="entities"):
        CanonicalFact.model_validate(
            {
                "fact_id": "F1",
                "proposition": "Analog Devices reported quarterly results.",
                "assertion_state": "ACTUAL",
                "subject_time": "fiscal Q3 2026",
                "entities": ["Analog Devices"],
            }
        )


def test_canonical_fact_schema_exposes_same_subject_time_marker() -> None:
    schema = CanonicalFact.model_json_schema()
    assert schema["$defs"]["CanonicalSubjectTimeMarker"]["enum"] == ["SAME"]
    fact = CanonicalFact(
        fact_id="F1",
        proposition="Analog Devices reported quarterly results.",
        assertion_state="ACTUAL",
        subject_time=CanonicalSubjectTimeMarker.SAME,
    )
    assert fact.model_dump(mode="json")["subject_time"] == "SAME"


def test_reference_fact_lines_show_occurrence_and_subject_separately() -> None:
    singleton = _event(
        event_id="E1",
        facts=[
            CanonicalFact(
                fact_id="F1",
                proposition="Analog Devices appointed a new director.",
                assertion_state="ACTUAL",
                subject_time="2026-08-19",
            )
        ],
    )
    assert _reference_fact_lines(singleton) == [
        "facts:",
        "",
        "- Fact occurred_at: LEGACY_UNAVAILABLE",
        "  Fact subject_time: 2026-08-19",
        "  Proposition: Analog Devices appointed a new director.",
    ]

    multi = _event(
        event_id="E2",
        facts=[
            CanonicalFact(
                fact_id="F2",
                proposition="Analog Devices reported fiscal Q3 results.",
                assertion_state="ACTUAL",
                subject_time=CanonicalSubjectTimeMarker.SAME,
            ),
            CanonicalFact(
                fact_id="F3",
                proposition="Analog Devices guided fiscal Q4 revenue.",
                assertion_state="GUIDANCE",
                subject_time="fiscal Q4 2026",
            ),
        ],
    )
    assert _reference_fact_lines(multi) == [
        "facts:",
        "",
        "- Fact occurred_at: LEGACY_UNAVAILABLE",
        "  Fact subject_time: SAME",
        "  Proposition: Analog Devices reported fiscal Q3 results.",
        "- Fact occurred_at: LEGACY_UNAVAILABLE",
        "  Fact subject_time: fiscal Q4 2026",
        "  Proposition: Analog Devices guided fiscal Q4 revenue.",
    ]


def test_reference_view_displays_singleton_fact_time_semantics() -> None:
    singleton = _event(
        event_id="E1",
        facts=[
            CanonicalFact(
                fact_id="F1",
                proposition="Analog Devices appointed a new director.",
                assertion_state="ACTUAL",
                subject_time=CanonicalSubjectTimeMarker.SAME,
            )
        ],
    )

    class Repository:
        def published_version(self, ticker: str) -> int:
            return 1

        def published_events(
            self, ticker: str, version: int | None = None
        ) -> list[CanonicalEvent]:
            return [singleton]

    view = EventLibraryViewCompiler(Repository()).reference_view("ADI")  # type: ignore[arg-type]
    assert "canonical_summary: Analog Devices disclosed an update." in view
    assert "facts:" in view
    assert "Fact occurred_at: LEGACY_UNAVAILABLE" in view
    assert "Fact subject_time: SAME" in view
    assert singleton.facts[0].proposition in view


def test_reference_view_delta_contains_only_changed_and_removed_events() -> None:
    prior_changed = _event(
        event_id="E1",
        facts=[CanonicalFact(fact_id="F1", proposition="Old fact.", assertion_state="ACTUAL")],
    )
    removed = _event(
        event_id="E2",
        facts=[CanonicalFact(fact_id="F2", proposition="Removed fact.", assertion_state="ACTUAL")],
    )
    changed = prior_changed.model_copy(
        update={"canonical_summary": "Analog Devices disclosed a changed update."}
    )

    class Repository:
        read_only = False

        def __init__(self) -> None:
            self.saved = None

        def published_version(self, ticker: str) -> int:
            return 2

        def published_events(
            self, ticker: str, version: int | None = None
        ) -> list[CanonicalEvent]:
            return [prior_changed, removed] if version == 1 else [changed]

        def save_reference_view_delta(self, value: object) -> None:
            self.saved = value

    repository = Repository()
    delta = EventLibraryViewCompiler(repository).reference_view_delta(  # type: ignore[arg-type]
        "ADI", from_version=1, to_version=2
    )
    assert "E1" in delta.reference_view_delta
    assert "E2" not in delta.reference_view_delta
    assert delta.removed_event_ids == ["E2"]
    assert repository.saved == delta


def _event(*, event_id: str, facts: list[CanonicalFact]) -> CanonicalEvent:
    return CanonicalEvent(
        event_id=event_id,
        ticker="ADI",
        title="Analog Devices corporate update",
        event_type="Corporate update",
        occurred_at="2026-08-19",
        occurrence_time_precision="DAY",
        canonical_summary="Analog Devices disclosed an update.",
        known_event_summary="Analog Devices corporate update occurrence.",
        is_important=True,
        include_in_reference_view=True,
        facts=facts,
    )
