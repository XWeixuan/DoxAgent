import copy
import json
from pathlib import Path

import pytest

from doxagent.persistent_runtime_v2.bailian_chat import chat_body
from doxagent.persistent_runtime_v2.schema import W1NoveltyResult
from doxagent.persistent_runtime_v2.transport import (
    RuntimeResponsesRequest,
    _cache_optimized_input,
    normalize_w1_attributions,
)
from doxagent.persistent_runtime_v2.w1_r2_context import (
    build_input,
    cell,
    clean_w1_r2_body,
    compile_fact_view,
    frozen_event_details,
    time_cell,
)


def test_every_frozen_fact_survives_without_mutating_source():
    dataset = json.loads(
        Path(__file__)
        .parent.joinpath("fixtures/w1_r2_context_cases.json")
        .read_text(encoding="utf8")
    )
    facts = provisional = 0
    for old in dataset:
        before = copy.deepcopy(old)
        payload, diagnostics = build_input(old["source_message"], old["event_details"])
        view = payload["known_fact_view"]
        for event in old["event_details"]["canonical_events"]:
            assert "## " + cell(event["event_id"]) + " | " in view
            assert cell(event["title"]) in view
            for fact in event["facts"]:
                line = " | ".join(
                    [
                        cell(fact["fact_id"]),
                        time_cell(
                            fact.get("fact_occurred_at"), fact.get("fact_occurrence_time_precision")
                        ),
                        cell(fact["assertion_state"]),
                        cell(fact.get("subject_time")),
                        cell(fact["proposition"]),
                    ]
                )
                assert line in view
                facts += 1
        for event in old["event_details"]["provisional_events"]:
            assert cell(event["candidate"]["proposition"]) in view
            provisional += 1
        assert old == before
        assert diagnostics["input_view"] == "w1-r2-fact-view-v1"
        wire, _ = _cache_optimized_input(payload, ("known_fact_view",))
        assert wire.startswith(view)
        assert "reference_context" not in wire
    assert (facts, provisional) == (240, 34)


def test_cells_and_precision_preserve_information_and_empty_sets():
    assert cell("a\\b|c\n\rd") == r"a\\b\|c\n\rd"
    for value, precision in [
        ("2026-10", "MONTH"),
        ("2026-Q3", "QUARTER"),
        ("2026/2027", "INTERVAL"),
    ]:
        assert time_cell(value, precision) == value
    assert time_cell("Fall 2026", "INTERVAL") == "Fall 2026 [precision=INTERVAL]"
    assert time_cell(None) == ""
    assert compile_fact_view({}).count("(empty)") == 2


def test_cleaning_is_template_scoped_and_keeps_dates_numbers_updates():
    body = (
        "Title\r\n\r\nUpdated Oct 22: not $450 | $400\\test\r\n\r\n\r\n"
        "Correction: 2027, conditional."
    )
    clean, diagnostic = clean_w1_r2_body(dict(title="Title", body=body))
    assert clean == "Updated Oct 22: not $450 | $400\\test\n\nCorrection: 2027, conditional."
    assert "exact_leading_title" in diagnostic["rules"]
    toolbar = "Facebook\n\nX\n\nWhatsapp\n\nReddit\n\nPinterest\n\nFlipboard\n\nEmail\n\n"
    assert (
        clean_w1_r2_body(dict(source_name="tomshardware.com", body=toolbar + "News"))[0] == "News"
    )
    assert (
        clean_w1_r2_body(dict(source_name="unknown", body=toolbar + "News"))[0] == toolbar + "News"
    )
    feed = (
        "← Back to live feed · 1 stories across 1 day\nMonday, Oct 12, 2026\n"
        "AI Oct 12, 11:16 AM EDT\nNews"
    )
    assert clean_w1_r2_body(dict(body=feed))[0] == feed.split("\n", 1)[1]
    assert (
        clean_w1_r2_body(dict(title="Title", body="Similar Title\nNews"))[0]
        == "Similar Title\nNews"
    )


def test_reference_compatibility_and_glm_stateless_lineage():
    details = dict(
        canonical_events=[dict(event_id="E1")], provisional_events=[dict(provisional_event_id="E2")]
    )
    assert frozen_event_details(dict(payload=dict(event_details=details))) is details
    assert (
        frozen_event_details(
            dict(
                payload=dict(known_fact_view="unparseable"),
                reference_context=dict(event_details=details),
            )
        )
        is details
    )
    request = RuntimeResponsesRequest(
        instructions="JSON",
        payload=dict(known_fact_view="view", source_message={}),
        cache_context_keys=("known_fact_view",),
        reference_context=dict(event_details=details),
        output_model=W1NoveltyResult,
        schema_name="w1_novelty_result",
        model="ZHIPU/GLM-5.3-FlashX",
        reasoning_effort="high",
        previous_response_id="lineage-only",
    )
    body, _ = chat_body(request)
    assert len(body["messages"]) == 2
    assert "reference_context" not in json.dumps(body)
    text, warnings = normalize_w1_attributions(
        json.dumps(dict(fact_attributions=[dict(event_id="E2", fact_ids=["F9"])])), request
    )
    assert json.loads(text)["fact_attributions"][0]["fact_ids"] == [] and warnings


@pytest.mark.parametrize("view,frozen_round", [(False, False), (True, False), (True, True)])
def test_service_input_version_and_frozen_replay(tmp_path, view, frozen_round):
    from datetime import date

    from doxagent.event_library.contracts import CanonicalEvent, CanonicalFact
    from doxagent.event_library.provider import EventDetailSnapshot
    from doxagent.persistent_runtime_v2.journal import RuntimeJournal, digest
    from doxagent.persistent_runtime_v2.repository import SQLitePersistentRuntimeV2Repository
    from doxagent.persistent_runtime_v2.schema import ProvisionalFactDetail, W1Round1Result
    from doxagent.persistent_runtime_v2.service import PersistentRuntimeV2Service
    from tests.test_persistent_runtime_v2 import _FakeKnownEvents, _FakePolicies, _FakeResponses
    from tests.test_runtime_policy_direction_usage import fixture_case

    class Known(_FakeKnownEvents):
        def details(self, ticker, version, event_ids):
            return EventDetailSnapshot(
                ticker=ticker,
                version=version,
                requested_event_ids=event_ids,
                events=[
                    CanonicalEvent(
                        event_id="E1",
                        ticker="MU",
                        title="A | B",
                        event_type="OTHER",
                        occurred_at="2026-10-01",
                        occurrence_time_precision="DAY",
                        canonical_summary="background",
                        known_event_summary="background",
                        is_important=False,
                        include_in_reference_view=True,
                        facts=[
                            CanonicalFact(
                                fact_id="F1",
                                proposition="Not yet launched",
                                assertion_state="DENIED",
                                subject_time="SAME",
                            )
                        ],
                    )
                ],
                missing_event_ids=[],
            )

    class Responses(_FakeResponses):
        def complete(self, request):
            result = super().complete(request)
            if request.output_model is W1Round1Result:
                from dataclasses import replace

                return replace(result, value=W1Round1Result(event_ids=["E1", "E2"]))
            return result

    journal = RuntimeJournal(tmp_path / "r.db")
    responses = Responses()
    service = PersistentRuntimeV2Service(
        repository=SQLitePersistentRuntimeV2Repository(journal.path),
        responses=responses,
        known_events=Known(),
        policies=_FakePolicies(),
        journal=journal,
        dispatch_effects=False,
    )
    case, _ = fixture_case()
    case.frozen_inputs["models"] = {"w12_model": "old-model"}
    if view:
        case.frozen_inputs["models"]["w1_r2_input_view"] = "w1-r2-fact-view-v1"
    provisional = ProvisionalFactDetail(
        provisional_event_id="E2",
        ticker="MU",
        trading_date=date(2026, 9, 30),
        source_message_id="prior-message",
        candidate_index=0,
        candidate=dict(
            proposition="Prior provisional",
            assertion_state="ACTUAL",
            occurrence_date=None,
            entities=["MU"],
        ),
        runtime_signature="a" * 64,
        snapshot_version=1,
        created_at=case.created_at,
    )
    case.frozen_inputs["provisional"] = [provisional.model_dump(mode="json")]
    service.repository.save_case(case)
    frozen = dict(
        payload=dict(source_message={"title": "frozen source"}, event_details={}),
        instructions="frozen old prompt",
        previous_response_id="frozen lineage",
        inherit_context=False,
        cache_context_keys=["event_details"],
        schema_hash=digest(W1NoveltyResult.model_json_schema()),
        model_settings=dict(
            model="frozen-model",
            reasoning_effort="medium",
            timeout_seconds=99,
            transport="responses_json_schema",
        ),
    )
    if frozen_round:
        journal.set("round_inputs", case.case_id + ":W1:R2", frozen)
    try:
        service._run_w1_hot(case, Known().current_index("MU"))
        request = next(r for r in responses.calls if r.output_model is W1NoveltyResult)
        saved = journal.get("round_inputs", case.case_id + ":W1:R2")
        if frozen_round:
            assert saved == frozen
            assert request.payload == frozen["payload"]
            assert request.instructions == "frozen old prompt"
            assert request.model == "frozen-model" and request.timeout_seconds == 99
            turn = next(
                t
                for t in service.repository.list_turns(case.case_id)
                if t.lane == "W1" and t.round_name == "R2"
            )
            assert turn.model == request.model and turn.timeout_seconds == 99
        elif view:
            assert "Not yet launched" in request.payload["known_fact_view"]
            assert "SAME" in request.payload["known_fact_view"]
            assert "Prior provisional" in request.payload["known_fact_view"]
            assert "semantic_day" not in request.payload["known_fact_view"]
            assert frozen_event_details(saved)["canonical_events"][0]["facts"][0]["fact_id"] == "F1"
            assert (
                frozen_event_details(saved)["provisional_events"][0]["provisional_event_id"] == "E2"
            )
            assert saved["reference_context"]["diagnostics"]["input_view"] == "w1-r2-fact-view-v1"
        else:
            assert "known_fact_view" not in request.payload
            assert request.payload["event_details"]["canonical_events"][0]["event_id"] == "E1"
    finally:
        service.close()


@pytest.mark.parametrize("compact", [False, True])
def test_w3_evidence_consumers_use_full_snapshot(tmp_path, compact):
    from types import SimpleNamespace

    from doxagent.persistent_runtime_v2.journal import RuntimeJournal
    from doxagent.persistent_runtime_v2.w3 import CodexW3AgentRunner
    from doxagent.persistent_runtime_v2.w3_execution import W3Execution
    from tests.test_runtime_policy_direction_usage import fixture_case

    journal = RuntimeJournal(tmp_path / "w3.db")
    event = dict(event_id="E1", title="Original Event", facts=[dict(fact_id="F1")])
    details = dict(canonical_events=[event], provisional_events=[])
    frozen = (
        dict(
            payload=dict(known_fact_view="Do not parse this view"),
            reference_context=dict(event_details=details),
        )
        if compact
        else dict(payload=dict(event_details=details))
    )
    case, _ = fixture_case()
    case.w1_final = case.w1_final.model_copy(update=dict(reference_ids=["E1"]))
    case.frozen_inputs["w3_contracts"] = dict(w3_citation_scope_version="loaded-events.v1")
    journal.set("round_inputs", case.case_id + ":W1:R2", frozen)
    executor = SimpleNamespace(journal=journal)
    assert W3Execution._events(executor, case) == [event]
    # The components branch mutates the already prepared public context in place.
    context = SimpleNamespace(components={}, reference_event_ids=frozenset())
    runner = SimpleNamespace(_journal=journal)
    assert CodexW3AgentRunner._citation_context(runner, case, context) is context
    assert context.citation_events == {"E1": event}
    assert context.reference_event_ids == frozenset({"E1"})


def test_case_local_frozen_input_survives_retry_without_journal():
    from doxagent.persistent_runtime_v2.repository import InMemoryPersistentRuntimeV2Repository
    from doxagent.persistent_runtime_v2.service import PersistentRuntimeV2Service
    from tests.test_persistent_runtime_v2 import _FakeKnownEvents, _FakePolicies, _FakeResponses
    from tests.test_runtime_policy_direction_usage import fixture_case

    responses = _FakeResponses()
    service = PersistentRuntimeV2Service(
        repository=InMemoryPersistentRuntimeV2Repository(),
        responses=responses,
        known_events=_FakeKnownEvents(),
        policies=_FakePolicies(),
        dispatch_effects=False,
    )
    case, _ = fixture_case()
    frozen = dict(
        instructions="old instructions",
        payload={"source_message": {"title": "old"}},
        previous_response_id="old-lineage",
        inherit_context=False,
        cache_context_keys=[],
    )
    case.frozen_inputs["round_inputs"] = {"W1:R2": frozen}
    try:
        service._call_with_retry(
            case=case,
            lane="W1",
            round_name="R2",
            round_prompt="new",
            payload={"known_fact_view": "new"},
            output_model=W1NoveltyResult,
            schema_name="w1_novelty_result",
        )
        assert responses.calls[0].payload == frozen["payload"]
        assert responses.calls[0].instructions == "old instructions"
        assert case.frozen_inputs["round_inputs"]["W1:R2"] == frozen
    finally:
        service.close()
