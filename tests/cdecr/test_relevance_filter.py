from __future__ import annotations

import json
from datetime import UTC, datetime

from cdecr.contracts import Language, SourceMessage, SourceType
from cdecr.ports import ResponsesModelRequest, StructuredModelRequest, StructuredModelResult
from cdecr.preprocessing import exact_document_fingerprint, preprocess_source
from cdecr.registry import SQLiteCDECRRegistry
from cdecr.relevance_filter import (
    RelevanceMode,
    dreamer_block_exposed_lengths,
    dreamer_block_request,
    frozen_relevance_response_request,
    relevance_response_request,
    select_candidates_fail_open,
    target_profile_for_source,
    validate_relevance_coverage,
)
from cdecr.single_document import SingleDocumentProcessor
from cdecr.single_document_contracts import DreamCandidate, DreamerModelOutput
from tests.cdecr.test_single_document import FakeEmbedding, FakeStructured


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
    assert (
        target_profile_for_source(
            value,
            {"MU": "Micron Technology (MU); DRAM, NAND and HBM memory semiconductors"},
        )
        == "Micron Technology (MU); DRAM, NAND and HBM memory semiconductors"
    )
    assert target_profile_for_source(value, {}) == "MU"

    ambiguous = value.model_copy(update={"ticker_hints": ["MU", "AMD"]})
    assert target_profile_for_source(ambiguous, {}) is None


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
        system_prompt="gate prompt",
    )
    assert live.previous_response_id is None
    assert live.output_mode == "json_object"
    assert live.strict is False
    assert live.session_cache is False
    assert live.reasoning_effort == "none"
    assert mapping == {"c1": "candidate:1", "c2": "candidate:2"}
    gate_payload = json.loads(live.input[-1]["content"])
    assert gate_payload["events"] == [
        {
            "id": "c1",
            "statement": "Micron raised guidance.",
            "exact_evidence": ["Micron raised guidance"],
        },
        {
            "id": "c2",
            "statement": "A university published an admission list.",
            "exact_evidence": ["A university published an admission list"],
        },
    ]

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
    assert [item["role"] for item in frozen.input] == ["system", "user"]
    assert frozen_mapping == mapping
    assert json.loads(frozen.input[-1]["content"])["events"] == gate_payload["events"]


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


class SequenceResponses(FakeResponses):
    def __init__(self, payloads: list[dict[str, object]]) -> None:
        super().__init__(payloads[0])
        self.payloads = list(payloads)

    def complete_response(self, request: ResponsesModelRequest) -> StructuredModelResult:
        self.payload = self.payloads.pop(0)
        return super().complete_response(request)


def test_relevance_coverage_requires_every_candidate_exactly_once() -> None:
    validate_relevance_coverage(
        {
            "results": [
                {"id": "c1", "relevance": "RELEVANT"},
                {"id": "c2", "relevance": "IRRELEVANT"},
            ]
        },
        expected_short_ids={"c1", "c2"},
    )
    for payload in (
        {"results": [{"id": "c1", "relevance": "RELEVANT"}]},
        {
            "results": [
                {"id": "c1", "relevance": "RELEVANT"},
                {"id": "c1", "relevance": "IRRELEVANT"},
            ]
        },
    ):
        try:
            validate_relevance_coverage(payload, expected_short_ids={"c1", "c2"})
        except ValueError:
            pass
        else:  # pragma: no cover
            raise AssertionError("invalid candidate coverage must fail")


def test_block_gate_enforces_valid_irrelevant_and_audits_cache() -> None:
    value = source()
    document = preprocess_source(value, known_documents=[]).document
    block = document.document_blocks[0]
    output = DreamerModelOutput(
        candidates=[
            {
                "statement": "Micron raised guidance.",
                "evidence_locations": [{"segment_id": "text:0", "text": "Micron raised guidance"}],
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
    processor.relevance_target_profiles = {}
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
    assert responses.calls[0].previous_response_id is None
    assert responses.calls[0].output_mode == "json_object"
    assert responses.calls[0].strict is False
    assert responses.calls[0].session_cache is False
    assert responses.calls[0].reasoning_effort == "none"
    gate_payload = json.loads(responses.calls[0].input[1]["content"])
    assert gate_payload["target"] == "MU"
    call_metadata = registry.model_calls[0]["metadata"]
    assert call_metadata["cached_input_tokens"] == 80  # type: ignore[index]
    assert call_metadata["request_transport"] == "responses_json_object"  # type: ignore[index]
    assert call_metadata["request_reasoning_effort"] == "none"  # type: ignore[index]
    assert call_metadata["request_previous_response_id"] is None  # type: ignore[index]
    assert call_metadata["request_session_cache"] is False  # type: ignore[index]


def test_relevance_repairs_same_independent_batch_once_then_enforces() -> None:
    value = source()
    document = preprocess_source(value, known_documents=[]).document
    block = document.document_blocks[0]
    output = DreamerModelOutput(
        candidates=[
            {
                "statement": "Micron raised guidance.",
                "evidence_locations": [{"segment_id": "text:0", "text": "Micron raised guidance"}],
            },
            {
                "statement": "A university published an admission list.",
                "evidence_locations": [
                    {"segment_id": "text:0", "text": "A university published an admission list"}
                ],
            },
        ]
    )
    responses = SequenceResponses(
        [
            {"results": [{"id": "c1", "relevance": "RELEVANT"}]},
            {
                "results": [
                    {"id": "c1", "relevance": "RELEVANT"},
                    {"id": "c2", "relevance": "IRRELEVANT"},
                ]
            },
        ]
    )
    processor = object.__new__(SingleDocumentProcessor)
    processor.relevance_filter_mode = RelevanceMode.ENFORCE
    processor.relevance_target_profiles = {}
    processor.relevance_responses_client = responses
    processor.model_m2 = responses.model
    processor.registry = FakeRegistry()

    filtered = processor._apply_relevance_gate(
        source=value,
        document=document,
        block=block,
        output=output,
        response_id="ignored-dreamer-response",
        run_id="RUN-REPAIR",
        summaries=[],
        attempt="initial",
    )

    assert len(responses.calls) == 2
    assert all(call.previous_response_id is None for call in responses.calls)
    assert all(call.session_cache is False for call in responses.calls)
    assert [item.statement for item in filtered.candidates] == ["Micron raised guidance."]


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
    processor.relevance_target_profiles = {}
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


def test_enforced_irrelevant_candidate_never_enters_grounder_or_mentions(tmp_path) -> None:
    class TwoCandidateDreamer(FakeStructured):
        def complete(self, request: StructuredModelRequest) -> StructuredModelResult:
            if request.json_schema.get("title") != "DreamerModelOutput":
                return super().complete(request)
            self.calls.append(request)
            return self._result(
                {
                    "candidates": [
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
                }
            )

    value = source()
    store = SQLiteCDECRRegistry(tmp_path / "relevance-integration.sqlite3")
    store.initialize()
    store.save_source(value, fingerprint=exact_document_fingerprint(value))
    dreamer = TwoCandidateDreamer(model="deepseek-v4-flash")
    grounder = FakeStructured(model="qwen3.7-plus")
    judge = FakeStructured(model="qwen3.7-max")
    gate = FakeResponses(
        {
            "results": [
                {"id": "c1", "relevance": "RELEVANT"},
                {"id": "c2", "relevance": "IRRELEVANT"},
            ]
        }
    )
    processor = SingleDocumentProcessor(
        registry=store,
        embedding_client=FakeEmbedding(),
        m2_client=dreamer,
        m3_client=grounder,
        m4_client=judge,
        relevance_responses_client=gate,
        relevance_filter_mode="enforce",
    )

    result = processor.process(value.message_id)

    grounder_payload = json.loads(grounder.calls[0].user_prompt)
    assert [item["statement"] for item in grounder_payload["candidates"]] == [
        "Micron raised guidance."
    ]
    assert len(result.mentions) == 1
    assert result.mentions[0].canonical_proposition == "Micron raised guidance."
    persisted = store.get_latest_dream_candidates_for_processing_key(
        processor.processing_key(value)
    )
    assert [item.statement for item in persisted] == ["Micron raised guidance."]
