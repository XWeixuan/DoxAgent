from __future__ import annotations

import json
from datetime import UTC, datetime

from cdecr.contracts import Language, SourceMessage, SourceType
from cdecr.ports import ResponsesModelRequest, StructuredModelRequest, StructuredModelResult
from cdecr.preprocessing import preprocess_source
from cdecr.relevance_filter import (
    RelevanceMode,
    dreamer_block_exposed_lengths,
    dreamer_block_request,
    frozen_relevance_response_request,
    relevance_response_request,
    select_candidates_fail_open,
    target_profile_for_source,
)
from cdecr.single_document import SingleDocumentProcessor
from cdecr.single_document_contracts import DreamCandidate, DreamerModelOutput


def source() -> SourceMessage:
    return SourceMessage(
        message_id="MSG-REL",
        source_type=SourceType.NEWS,
        title="Micron and unrelated update",
        text="Micron raised guidance. A university published an admission list.",
        published_at=datetime(2026, 8, 1, tzinfo=UTC),
        source_name="Wire",
        url="https://example.test/relevance",
        ticker_hints=["MU"],
        language=Language.EN,
    )


def candidates() -> list[DreamCandidate]:
    return [
        DreamCandidate(
            candidate_id="candidate:1",
            statement="Micron raised guidance.",
            evidence_locations=[
                {
                    "segment_id": "text:0",
                    "start_char": 0,
                    "end_char": 22,
                    "text": "Micron raised guidance",
                }
            ],
        ),
        DreamCandidate(
            candidate_id="candidate:2",
            statement="A university published an admission list.",
            evidence_locations=[
                {
                    "segment_id": "text:0",
                    "start_char": 24,
                    "end_char": 64,
                    "text": "A university published an admission list",
                }
            ],
        ),
    ]


def test_target_profile_requires_one_configured_target() -> None:
    value = source()
    assert target_profile_for_source(
        value,
        {"MU": "Micron Technology (MU); DRAM, NAND and HBM memory semiconductors"},
    ) == "Micron Technology (MU); DRAM, NAND and HBM memory semiconductors"
    assert target_profile_for_source(value, {}) is None


def test_dreamer_block_request_is_shared_and_exposes_exact_segments() -> None:
    value = source()
    document = preprocess_source(value, known_documents=[]).document
    block = document.document_blocks[0]
    request = dreamer_block_request(
        source=value,
        document=document,
        block=block,
        system_prompt="dreamer prompt",
    )
    payload = json.loads(request.user_prompt)
    exposed = dreamer_block_exposed_lengths(document, block)
    assert payload["allowed_segment_ids"] == list(exposed)
    assert payload["block"] == block.text
    assert request.json_schema["title"] == "DreamerModelOutput"


def test_relevance_selection_is_item_scoped_and_fail_open() -> None:
    values = candidates()
    selection = select_candidates_fail_open(
        values,
        {
            "results": [
                {"id": "c1", "relevance": "RELEVANT"},
                {"id": "c2", "relevance": "IRRELEVANT"},
                {"id": "extra", "relevance": "IRRELEVANT"},
            ]
        },
        short_to_full={"c1": "candidate:1", "c2": "candidate:2"},
    )
    assert [item.candidate_id for item in selection.retained] == ["candidate:1"]
    assert selection.ignored_ids == ("extra",)

    conflict = select_candidates_fail_open(
        values,
        {
            "results": [
                {"id": "c1", "relevance": "IRRELEVANT"},
                {"id": "c1", "relevance": "RELEVANT"},
                {"id": "c2", "relevance": "UNKNOWN"},
            ]
        },
        short_to_full={"c1": "candidate:1", "c2": "candidate:2"},
    )
    assert conflict.retained == values
    assert [item.reason for item in conflict.decisions] == [
        "DUPLICATE_CONFLICT",
        "INVALID_ENUM",
    ]

    invalid_batch = select_candidates_fail_open(
        values,
        {"results": "not-a-list"},
        short_to_full={"c1": "candidate:1", "c2": "candidate:2"},
    )
    assert invalid_batch.retained == values
    assert {item.reason for item in invalid_batch.decisions} == {"INVALID_BATCH_PAYLOAD"}


def test_production_and_frozen_requests_share_the_production_schema() -> None:
    values = candidates()
    live, mapping = relevance_response_request(
        target="Micron Technology (MU); memory semiconductors",
        candidates=values,
        previous_response_id="resp-1",
        system_prompt="gate prompt",
    )
    assert live.previous_response_id == "resp-1"
    assert live.reasoning_effort == "low"
    assert mapping == {"c1": "candidate:1", "c2": "candidate:2"}

    dreamer_request = StructuredModelRequest(
        system_prompt="dreamer prompt",
        user_prompt=json.dumps({"block": "document"}),
        json_schema=DreamerModelOutput.model_json_schema(),
    )
    frozen, frozen_mapping = frozen_relevance_response_request(
        dreamer_request=dreamer_request,
        frozen_output=DreamerModelOutput(candidates=[]),
        target="Micron Technology (MU); memory semiconductors",
        candidates=values,
        relevance_system_prompt="gate prompt",
    )
    assert frozen.previous_response_id is None
    assert [item["role"] for item in frozen.input] == [
        "system",
        "user",
        "assistant",
        "system",
        "user",
    ]
    assert frozen_mapping == mapping


class FakeRegistry:
    def __init__(self) -> None:
        self.model_calls: list[dict[str, object]] = []
        self.audits: list[object] = []

    def record_model_call(self, **kwargs: object) -> bool:
        self.model_calls.append(kwargs)
        return True

    def append_decision_audit(self, record: object) -> bool:
        self.audits.append(record)
        return True


class FakeResponses:
    model = "deepseek-v4-flash-0731"

    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.calls: list[ResponsesModelRequest] = []

    def complete_response(self, request: ResponsesModelRequest) -> StructuredModelResult:
        self.calls.append(request)
        return StructuredModelResult(
            model=self.model,
            payload=self.payload,
            input_tokens=100,
            cached_input_tokens=80,
            output_tokens=10,
            latency_ms=5,
            response_id="resp-gate",
        )


def test_block_gate_enforces_valid_irrelevant_and_audits_cache() -> None:
    value = source()
    document = preprocess_source(value, known_documents=[]).document
    block = document.document_blocks[0]
    output = DreamerModelOutput(
        candidates=[
            {
                "statement": "Micron raised guidance.",
                "evidence_locations": [
                    {"segment_id": "text:0", "text": "Micron raised guidance"}
                ],
            },
            {
                "statement": "A university published an admission list.",
                "evidence_locations": [
                    {
                        "segment_id": "text:0",
                        "text": "A university published an admission list",
                    }
                ],
            },
        ]
    )
    registry = FakeRegistry()
    responses = FakeResponses(
        {
            "results": [
                {"id": "c1", "relevance": "RELEVANT"},
                {"id": "c2", "relevance": "IRRELEVANT"},
            ]
        }
    )
    processor = object.__new__(SingleDocumentProcessor)
    processor.relevance_filter_mode = RelevanceMode.ENFORCE
    processor.relevance_target_profiles = {
        "MU": "Micron Technology (MU); DRAM, NAND and HBM memory semiconductors"
    }
    processor.relevance_responses_client = responses
    processor.model_m2 = responses.model
    processor.registry = registry

    filtered = processor._apply_relevance_gate(
        source=value,
        document=document,
        block=block,
        output=output,
        response_id="resp-dreamer",
        run_id="RUN-1",
        summaries=[],
        attempt="initial",
    )

    assert [item.statement for item in filtered.candidates] == ["Micron raised guidance."]
    assert responses.calls[0].previous_response_id == "resp-dreamer"
    assert responses.calls[0].reasoning_effort == "low"
    assert registry.model_calls[0]["metadata"]["cached_input_tokens"] == 80  # type: ignore[index]


def test_block_gate_shadow_records_drop_without_filtering() -> None:
    value = source()
    document = preprocess_source(value, known_documents=[]).document
    block = document.document_blocks[0]
    output = DreamerModelOutput(
        candidates=[
            {
                "statement": candidate.statement,
                "evidence_locations": [
                    {"segment_id": item.segment_id, "text": item.text}
                    for item in candidate.evidence_locations
                ],
            }
            for candidate in candidates()
        ]
    )
    registry = FakeRegistry()
    responses = FakeResponses(
        {
            "results": [
                {"id": "c1", "relevance": "RELEVANT"},
                {"id": "c2", "relevance": "IRRELEVANT"},
            ]
        }
    )
    processor = object.__new__(SingleDocumentProcessor)
    processor.relevance_filter_mode = RelevanceMode.SHADOW
    processor.relevance_target_profiles = {
        "MU": "Micron Technology (MU); DRAM, NAND and HBM memory semiconductors"
    }
    processor.relevance_responses_client = responses
    processor.model_m2 = responses.model
    processor.registry = registry

    retained = processor._apply_relevance_gate(
        source=value,
        document=document,
        block=block,
        output=output,
        response_id="resp-dreamer",
        run_id="RUN-SHADOW",
        summaries=[],
        attempt="initial",
    )

    assert retained == output
    audit = registry.audits[-1]
    assert audit.payload["after_count"] == 2  # type: ignore[attr-defined,index]
    assert audit.payload["simulated_dropped_count"] == 1  # type: ignore[attr-defined,index]
