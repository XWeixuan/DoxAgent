from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cdecr.contracts import Language, SourceMessage, SourceType
from cdecr.models import ModelAdapterError, ModelTier
from cdecr.ports import EmbeddingResult, StructuredModelRequest, StructuredModelResult
from cdecr.preprocessing import exact_document_fingerprint
from cdecr.registry import SQLiteCDECRRegistry
from cdecr.single_document import (
    SingleDocumentProcessor,
    _mention_semantic_codes,
    _safe_validation_errors,
)
from cdecr.single_document_contracts import (
    DreamerModelOutput,
    EvidenceText,
    GroundedMentionDraft,
    JudgeAction,
    JudgeDecisionRecord,
    MentionDraft,
    OpenAttributeDraft,
    ProcessingStatus,
)


class FakeEmbedding:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> EmbeddingResult:
        self.calls.append(list(texts))
        return EmbeddingResult(
            model="text-embedding-v4",
            dimensions=1024,
            vectors=[[0.0] * 1024 for _ in texts],
            input_tokens=len(texts),
            latency_ms=1,
        )


class FailingEmbedding:
    def embed(self, texts: list[str]) -> EmbeddingResult:
        raise ModelAdapterError(
            tier=ModelTier.M1,
            code="provider_arrearage",
            status_code=400,
            latency_ms=2,
        )


class FakeStructured:
    def __init__(
        self,
        *,
        model: str,
        no_events: bool = False,
        invalid_dreamer_responses: int = 0,
    ) -> None:
        self.model = model
        self.no_events = no_events
        self.invalid_dreamer_responses = invalid_dreamer_responses
        self.calls: list[StructuredModelRequest] = []

    def complete(self, request: StructuredModelRequest) -> StructuredModelResult:
        self.calls.append(request)
        if request.system_prompt.startswith("Repair"):
            repair_payload = json.loads(request.user_prompt)
            request = request.model_copy(
                update={
                    "system_prompt": "repaired test request",
                    "user_prompt": repair_payload["original_request"],
                }
            )
        title = str(request.json_schema.get("title"))
        if title == "DreamerModelOutput":
            if self.invalid_dreamer_responses > 0:
                self.invalid_dreamer_responses -= 1
                return self._result({"candidates": [], "illegal_extra": True})
            if self.no_events or "block:" in request.user_prompt:
                return self._result({"candidates": []})
            return self._result(
                {
                    "candidates": [
                        {
                            "statement": "Micron raised guidance.",
                            "evidence_locations": [
                                {
                                    "segment_id": "text:0",
                                    "text": "Micron raised guidance",
                                }
                            ],
                        }
                    ]
                }
            )
        if title == "GrounderModelOutput":
            request_payload = json.loads(request.user_prompt)
            candidates = request_payload["candidates"]
            if not candidates:
                return self._result(
                    {
                        "drafts": [],
                        "rejected_candidates": [],
                        "issue_flags": [],
                    }
                )
            candidate_id = candidates[0]["candidate_id"]
            return self._result(
                {
                    "drafts": [
                        {
                            "source_candidate_ids": [candidate_id],
                            "mention": mention_draft(),
                        }
                    ],
                    "rejected_candidates": [],
                    "issue_flags": [],
                }
            )
        if title == "JudgeCommandOutput":
            request_payload = json.loads(request.user_prompt)
            return self._result(
                {
                    "accepted": [
                        {
                            "id": draft["id"],
                            "reason": "supported",
                        }
                        for draft in request_payload["drafts"]
                    ],
                    "rejected": [],
                    "split": [],
                    "duplicates": [],
                    "attribute_merges": [],
                }
            )
        if title == "_SelectionBatch":
            return self._result({"selections": []})
        raise AssertionError(f"unexpected schema {title}")

    def _result(self, payload: dict[str, object]) -> StructuredModelResult:
        return StructuredModelResult(
            model=self.model,
            payload=payload,
            input_tokens=10,
            output_tokens=5,
            latency_ms=1,
        )


class FencedGrounderOnce(FakeStructured):
    def __init__(self) -> None:
        super().__init__(model="qwen3.7-plus")
        self.failed = False

    def complete(self, request: StructuredModelRequest) -> StructuredModelResult:
        if request.json_schema.get("title") == "GrounderModelOutput" and not self.failed:
            self.calls.append(request)
            self.failed = True
            raise ModelAdapterError(
                tier=ModelTier.M3,
                code="invalid_json",
                latency_ms=3,
                input_tokens=20,
                output_tokens=10,
                raw_response_text="```json\n{}\n```",
            )
        return super().complete(request)


class EmptyDreamerOnce(FakeStructured):
    def __init__(self) -> None:
        super().__init__(model="deepseek-v4-flash")
        self.returned_empty = False

    def complete(self, request: StructuredModelRequest) -> StructuredModelResult:
        if request.json_schema.get("title") == "DreamerModelOutput" and not self.returned_empty:
            self.calls.append(request)
            self.returned_empty = True
            return self._result({"candidates": []})
        return super().complete(request)


class InvalidOptionalGrounderEvidence(FakeStructured):
    def complete(self, request: StructuredModelRequest) -> StructuredModelResult:
        result = super().complete(request)
        if request.json_schema.get("title") != "GrounderModelOutput":
            return result
        drafts = result.payload["drafts"]
        assert isinstance(drafts, list) and drafts
        mention = drafts[0]["mention"]
        assert isinstance(mention, dict)
        bad_locator = {
            "segment_id": "text:0",
            "text": "Micron increased guidance",
        }
        mention["evidence_locations"] = [bad_locator]
        mention["open_attributes"] = [
            {"key": "detail", "value": "unsupported", "evidence_location": bad_locator}
        ]
        return result


class InvalidAuxiliaryGrounderEvidence(FakeStructured):
    def complete(self, request: StructuredModelRequest) -> StructuredModelResult:
        result = super().complete(request)
        if request.json_schema.get("title") != "GrounderModelOutput":
            return result
        drafts = result.payload["drafts"]
        assert isinstance(drafts, list) and drafts
        mention = drafts[0]["mention"]
        assert isinstance(mention, dict)
        mention["open_attributes"] = [
            {
                "key": "detail",
                "value": "unsupported",
                "evidence_location": {
                    "segment_id": "text:0",
                    "text": "Micron increased guidance",
                },
            }
        ]
        return result


class UnboundedDayPrecisionGrounder(FakeStructured):
    def complete(self, request: StructuredModelRequest) -> StructuredModelResult:
        result = super().complete(request)
        if request.json_schema.get("title") != "GrounderModelOutput":
            return result
        drafts = result.payload["drafts"]
        assert isinstance(drafts, list) and drafts
        time = drafts[0]["mention"]["time"]
        assert isinstance(time, dict)
        time.update(
            {
                "event_start": None,
                "event_end": None,
                "precision": "DAY",
                "reference_period_id": "FY2026 Q4",
            }
        )
        return result


class InvalidJudgeEvidenceWithValidFieldChange(FakeStructured):
    def complete(self, request: StructuredModelRequest) -> StructuredModelResult:
        result = super().complete(request)
        if request.json_schema.get("title") != "JudgeCommandOutput":
            return result
        accepted = result.payload["accepted"]
        assert isinstance(accepted, list) and accepted
        accepted[0]["changes"] = {
            "canonical_proposition": "Micron increased its guidance.",
            "evidence_locations": [
                {
                    "segment_id": "text:0",
                    "text": "Micron increased guidance",
                }
            ],
        }
        return result


class InvalidDreamerEvidence(FakeStructured):
    def __init__(self, *, model: str) -> None:
        super().__init__(model=model)
        self.returned_invalid = False

    def complete(self, request: StructuredModelRequest) -> StructuredModelResult:
        result = super().complete(request)
        if request.json_schema.get("title") != "DreamerModelOutput" or self.returned_invalid:
            return result
        self.returned_invalid = True
        candidates = result.payload["candidates"]
        assert isinstance(candidates, list) and candidates
        candidates[0]["evidence_locations"] = [
            {
                "segment_id": "text:0",
                "text": "Micron increased guidance",
            }
        ]
        return result


class DuplicateGrounderDrafts(FakeStructured):
    def complete(self, request: StructuredModelRequest) -> StructuredModelResult:
        result = super().complete(request)
        if request.json_schema.get("title") != "GrounderModelOutput":
            return result
        drafts = result.payload["drafts"]
        assert isinstance(drafts, list) and drafts
        duplicate = json.loads(json.dumps(drafts[0]))
        duplicate["mention"]["canonical_proposition"] = "Micron guidance was raised."
        drafts.append(duplicate)
        return result


class InvalidJudgeKeepTarget(FakeStructured):
    def complete(self, request: StructuredModelRequest) -> StructuredModelResult:
        result = super().complete(request)
        if request.json_schema.get("title") != "JudgeCommandOutput":
            return result
        result.payload["accepted"] = []
        result.payload["duplicates"] = [{"id": "d1", "reason": "self target", "keep_id": "d1"}]
        return result


class DuplicateJudgeCoverage(FakeStructured):
    def complete(self, request: StructuredModelRequest) -> StructuredModelResult:
        result = super().complete(request)
        if request.json_schema.get("title") != "JudgeCommandOutput":
            return result
        if "Return exactly one Judge command" in request.system_prompt:
            return result
        accepted = result.payload["accepted"]
        assert isinstance(accepted, list) and accepted
        accepted.append(json.loads(json.dumps(accepted[0])))
        return result


class RepeatedSemanticInvalidJudge(FakeStructured):
    def complete(self, request: StructuredModelRequest) -> StructuredModelResult:
        result = super().complete(request)
        if request.json_schema.get("title") != "JudgeCommandOutput":
            return result
        accepted = result.payload["accepted"]
        assert isinstance(accepted, list) and accepted
        accepted[0]["changes"] = {
            "predicate": {"raw": "guided", "normalized": "guide_metric"},
            "assertion_state": "ACTUAL",
        }
        return result


class RepeatedSchemaInvalidJudge(FakeStructured):
    def complete(self, request: StructuredModelRequest) -> StructuredModelResult:
        if request.json_schema.get("title") == "JudgeCommandOutput":
            self.calls.append(request)
            return self._result({"invalid": True})
        return super().complete(request)


class RecoverableGrounderContractDrift(FakeStructured):
    def complete(self, request: StructuredModelRequest) -> StructuredModelResult:
        payload = json.loads(request.user_prompt)
        if request.json_schema.get("title") == "GrounderModelOutput" and "invalid_draft" in payload:
            self.calls.append(request)
            repaired = payload["invalid_draft"]
            repaired.pop("illegal_extra", None)
            repaired["mention"]["event_family"] = "OTHER"
            return self._result(
                {
                    "drafts": [repaired],
                    "rejected_candidates": [],
                    "issue_flags": [],
                }
            )
        result = super().complete(request)
        if request.json_schema.get("title") != "GrounderModelOutput":
            return result
        drafts = result.payload["drafts"]
        assert isinstance(drafts, list) and drafts
        drafts[0]["mention"]["event_family"] = "MODEL_INVENTED_FAMILY"
        drafts[0]["illegal_extra"] = True
        return result


class TwoCandidateDreamer(FakeStructured):
    def complete(self, request: StructuredModelRequest) -> StructuredModelResult:
        result = super().complete(request)
        if request.json_schema.get("title") != "DreamerModelOutput":
            return result
        candidates = result.payload["candidates"]
        assert isinstance(candidates, list) and candidates
        duplicate = json.loads(json.dumps(candidates[0]))
        duplicate["statement"] = "Micron updated its guidance."
        candidates.append(duplicate)
        return result


class ParallelItemRepairGrounder(FakeStructured):
    def __init__(self, *, model: str) -> None:
        super().__init__(model=model)
        self.barrier = threading.Barrier(2)
        self.lock = threading.Lock()
        self.active_repairs = 0
        self.max_active_repairs = 0

    def complete(self, request: StructuredModelRequest) -> StructuredModelResult:
        payload = json.loads(request.user_prompt)
        if request.json_schema.get("title") == "GrounderModelOutput" and "invalid_draft" in payload:
            self.calls.append(request)
            with self.lock:
                self.active_repairs += 1
                self.max_active_repairs = max(
                    self.max_active_repairs,
                    self.active_repairs,
                )
            self.barrier.wait(timeout=2)
            repaired = payload["invalid_draft"]
            repaired.pop("illegal_extra", None)
            with self.lock:
                self.active_repairs -= 1
            return self._result(
                {
                    "drafts": [repaired],
                    "rejected_candidates": [],
                    "issue_flags": [],
                }
            )
        result = super().complete(request)
        if request.json_schema.get("title") != "GrounderModelOutput":
            return result
        payload = json.loads(request.user_prompt)
        candidates = payload["candidates"]
        drafts = result.payload["drafts"]
        assert isinstance(candidates, list) and len(candidates) == 2
        assert isinstance(drafts, list) and drafts
        second = json.loads(json.dumps(drafts[0]))
        second["source_candidate_ids"] = [candidates[1]["candidate_id"]]
        drafts[0]["illegal_extra"] = True
        second["illegal_extra"] = True
        drafts.append(second)
        return result


class RejectingGrounder(FakeStructured):
    def complete(self, request: StructuredModelRequest) -> StructuredModelResult:
        if request.json_schema.get("title") != "GrounderModelOutput":
            return super().complete(request)
        self.calls.append(request)
        payload = json.loads(request.user_prompt)
        return self._result(
            {
                "drafts": [],
                "rejected_candidates": [
                    {
                        "id": item["candidate_id"],
                        "code": "BACKGROUND",
                    }
                    for item in payload["candidates"]
                ],
                "issue_flags": [],
            }
        )


class OverlappingMissingRecoveryGrounder(FakeStructured):
    def complete(self, request: StructuredModelRequest) -> StructuredModelResult:
        result = super().complete(request)
        if (
            request.json_schema.get("title") == "GrounderModelOutput"
            and "Return every supplied candidate exactly once" in request.system_prompt
        ):
            payload = json.loads(request.user_prompt)
            candidate_id = payload["candidates"][0]["candidate_id"]
            result.payload["rejected_candidates"] = [{"id": candidate_id, "code": "BACKGROUND"}]
        return result


def mention_draft() -> dict[str, object]:
    return {
        "evidence_locations": [
            {
                "segment_id": "text:0",
                "text": "Micron raised guidance",
            }
        ],
        "canonical_proposition": "Micron raised guidance.",
        "source_claim": None,
        "event_family": "GUIDANCE_EXPECTATION",
        "predicate": {"raw": "raised", "normalized": "raise_guidance"},
        "participants": [{"surface": "Micron", "role": "SUBJECT"}],
        "locations": [],
        "time": {
            "event_start": "2026-06-25",
            "event_end": None,
            "precision": "DAY",
            "reference_period_id": None,
        },
        "assertion_state": "ACTUAL",
        "quantities": [],
        "open_attributes": [],
        "local_package_hint": {
            "anchor": "Micron FY2026 earnings release",
            "relation_to_anchor": "DISCLOSED_IN",
        },
    }


def test_model_facing_mention_contract_excludes_schema_projections() -> None:
    serialized = json.dumps(MentionDraft.model_json_schema(), sort_keys=True)
    assert "schema_projection" not in serialized
    assert "FinancialMetricProjection" not in serialized
    assert "GuidanceProjection" not in serialized
    assert "AnalystActionProjection" not in serialized

    payload = mention_draft()
    payload["schema_projection"] = None
    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        MentionDraft.model_validate(payload)


def test_judge_semantic_validator_detects_opposing_subject_actions() -> None:
    payload = mention_draft()
    payload.update(
        {
            "canonical_proposition": "Foreign investors bought while institutions sold.",
            "predicate": {"raw": "traded", "normalized": "trade"},
            "event_family": "MARKET_MOVEMENT",
            "participants": [
                {"surface": "foreign investors", "role": "ACTOR"},
                {"surface": "institutions", "role": "ACTOR"},
            ],
        }
    )
    assert _mention_semantic_codes(MentionDraft.model_validate(payload)) == {
        "OPPOSING_CORE_ACTIONS",
        "OPPOSING_SUBJECT_ACTIONS",
    }


def test_dreamer_contract_bounds_candidates_per_block() -> None:
    schema = DreamerModelOutput.model_json_schema()
    assert schema["properties"]["candidates"]["maxItems"] == 24
    serialized = json.dumps(schema, sort_keys=True)
    assert "start_char" not in serialized
    assert "end_char" not in serialized


def source(message_id: str = "MSG-1", *, long: bool = False) -> SourceMessage:
    text = "Micron raised guidance after strong demand."
    if long:
        text = "\n\n".join(f"Paragraph {index} " + ("memory " * 5_000) for index in range(8))
    return SourceMessage(
        message_id=message_id,
        source_type=SourceType.NEWS,
        title="Micron update",
        text=text,
        published_at=datetime(2026, 6, 25, 12, tzinfo=UTC),
        source_name="Wire",
        url=f"https://example.test/{message_id}",
        ticker_hints=["MU"],
        language=Language.EN,
    )


@pytest.fixture
def registry(tmp_path: Path) -> SQLiteCDECRRegistry:
    value = SQLiteCDECRRegistry(tmp_path / "cdecr.sqlite3")
    value.initialize()
    return value


def processor(
    registry: SQLiteCDECRRegistry,
    *,
    no_events: bool = False,
    invalid_dreamer_responses: int = 0,
) -> tuple[SingleDocumentProcessor, FakeEmbedding, FakeStructured, FakeStructured]:
    embedding = FakeEmbedding()
    m2 = FakeStructured(
        model="deepseek-v4-flash",
        no_events=no_events,
        invalid_dreamer_responses=invalid_dreamer_responses,
    )
    m3 = FakeStructured(model="qwen3.7-plus", no_events=no_events)
    m4 = FakeStructured(model="qwen3.7-max", no_events=no_events)
    return (
        SingleDocumentProcessor(
            registry=registry,
            embedding_client=embedding,
            m2_client=m2,
            m3_client=m3,
            m4_client=m4,
        ),
        embedding,
        m2,
        m3,
    )


def save_source(registry: SQLiteCDECRRegistry, value: SourceMessage) -> None:
    registry.save_source(value, fingerprint=exact_document_fingerprint(value))


def test_short_document_routes_m2_dreamer_and_always_m3_grounder(
    registry: SQLiteCDECRRegistry,
) -> None:
    save_source(registry, source())
    service, embedding, m2, m3 = processor(registry)
    result = service.process("MSG-1")
    assert result.status is ProcessingStatus.SUCCEEDED
    assert len(result.mentions) == 1
    result.mentions[0].validate_evidence(source())
    assert [request.json_schema["title"] for request in m2.calls] == ["DreamerModelOutput"]
    assert [request.json_schema["title"] for request in m3.calls] == ["GrounderModelOutput"]
    assert isinstance(service.m4_client, FakeStructured)
    assert [request.json_schema["title"] for request in service.m4_client.calls] == [
        "JudgeCommandOutput"
    ]
    assert len(embedding.calls) == 1
    assert result.judge_routing.invoked
    assert result.judge_routing.reasons == ["all_grounder_drafts_m4"]


def test_model_payloads_share_published_at_and_judge_uses_short_n4_dto(
    registry: SQLiteCDECRRegistry,
) -> None:
    save_source(registry, source())
    service, _, m2, m3 = processor(registry)
    result = service.process("MSG-1")
    assert result.status is ProcessingStatus.SUCCEEDED
    assert isinstance(service.m4_client, FakeStructured)

    dreamer_payload = json.loads(m2.calls[0].user_prompt)
    grounder_payload = json.loads(m3.calls[0].user_prompt)
    judge_payload = json.loads(service.m4_client.calls[0].user_prompt)
    for payload in (dreamer_payload, grounder_payload, judge_payload):
        assert payload["published_at"] == "2026-06-25T08:00:00"
    assert "message_id" not in dreamer_payload
    assert "segment_lengths" not in dreamer_payload

    judge_draft = judge_payload["drafts"][0]
    assert judge_draft["id"] == "d1"
    assert set(judge_draft) == {"id", "mention"}
    assert "source_candidate_ids" not in judge_draft
    assert "local_package_hint" not in judge_draft["mention"]


def test_no_event_document_skips_grounder_and_persists_empty_result(
    registry: SQLiteCDECRRegistry,
) -> None:
    save_source(registry, source())
    service, _, _, m3 = processor(registry, no_events=True)
    result = service.process("MSG-1")
    assert result.status is ProcessingStatus.SUCCEEDED
    assert m3.calls == []


def test_zero_candidate_dreamer_gets_one_narrow_recovery_pass(
    registry: SQLiteCDECRRegistry,
) -> None:
    save_source(registry, source())
    dreamer = EmptyDreamerOnce()
    service = SingleDocumentProcessor(
        registry=registry,
        embedding_client=FakeEmbedding(),
        m2_client=dreamer,
        m3_client=FakeStructured(model="qwen3.7-plus"),
        m4_client=FakeStructured(model="qwen3.7-max"),
    )

    result = service.process("MSG-1")

    assert result.status is ProcessingStatus.SUCCEEDED
    assert len(result.mentions) == 1
    assert sum(summary.stage == "dreamer_zero_recovery" for summary in result.model_calls) == 1


def test_all_grounder_drafts_route_one_batch_m4_judge(
    registry: SQLiteCDECRRegistry,
) -> None:
    save_source(registry, source())
    service, _, m2, _ = processor(registry)
    result = service.process("MSG-1")
    assert result.status is ProcessingStatus.SUCCEEDED
    assert result.judge_routing.invoked
    assert result.judge_routing.reasons == ["all_grounder_drafts_m4"]
    assert [request.json_schema["title"] for request in m2.calls].count("JudgeCommandOutput") == 0
    assert isinstance(service.m4_client, FakeStructured)
    assert [request.json_schema["title"] for request in service.m4_client.calls].count(
        "JudgeCommandOutput"
    ) == 1


def test_long_document_uses_m3_dreamer_blocks_and_m3_grounder(
    registry: SQLiteCDECRRegistry,
) -> None:
    value = source(long=True)
    save_source(registry, value)
    service, _, m2, m3 = processor(registry, no_events=True)
    result = service.process("MSG-1")
    assert result.status is ProcessingStatus.SUCCEEDED
    assert not any(request.json_schema["title"] == "DreamerModelOutput" for request in m2.calls)
    m3_titles = [request.json_schema["title"] for request in m3.calls]
    assert m3_titles.count("DreamerModelOutput") >= 2
    assert m3_titles.count("GrounderModelOutput") == 0
    assert not result.judge_routing.invoked


def test_structured_schema_failure_gets_exactly_one_repair(
    registry: SQLiteCDECRRegistry,
) -> None:
    save_source(registry, source())
    service, _, m2, _ = processor(registry, invalid_dreamer_responses=1)
    result = service.process("MSG-1")
    assert result.status is ProcessingStatus.SUCCEEDED
    dreamer_calls = [
        request for request in m2.calls if request.json_schema["title"] == "DreamerModelOutput"
    ]
    assert len(dreamer_calls) == 2
    assert any(summary.repaired for summary in result.model_calls)


def test_invalid_grounder_root_json_degrades_only_that_batch(
    registry: SQLiteCDECRRegistry,
) -> None:
    save_source(registry, source())
    grounder = FencedGrounderOnce()
    service = SingleDocumentProcessor(
        registry=registry,
        embedding_client=FakeEmbedding(),
        m2_client=FakeStructured(model="deepseek-v4-flash"),
        m3_client=grounder,
        m4_client=FakeStructured(model="qwen3.7-max"),
    )
    result = service.process("MSG-1")
    assert result.status is ProcessingStatus.SUCCEEDED
    grounder_calls = [
        summary for summary in result.model_calls if summary.stage.startswith("grounder")
    ]
    assert [summary.status for summary in grounder_calls] == ["FAILED", "SUCCEEDED"]
    assert grounder_calls[1].stage == "grounder_missing_recovery"
    assert len(result.mentions) == 1
    assert grounder_calls[0].error_code == "invalid_json"
    assert grounder_calls[0].input_tokens == 20
    with sqlite3.connect(registry.path) as connection:
        count = connection.execute(
            """
            SELECT COUNT(*) FROM decision_audits
            WHERE run_id = ? AND decision_type = 'GROUNDER_BATCH_DEGRADED'
            """,
            (result.run_id,),
        ).fetchone()[0]
    assert count == 1


def test_invalid_unique_grounder_main_evidence_is_retained_without_repair(
    registry: SQLiteCDECRRegistry,
) -> None:
    save_source(registry, source())
    service = SingleDocumentProcessor(
        registry=registry,
        embedding_client=FakeEmbedding(),
        m2_client=FakeStructured(model="deepseek-v4-flash"),
        m3_client=InvalidOptionalGrounderEvidence(model="qwen3.7-plus"),
        m4_client=FakeStructured(model="qwen3.7-max"),
    )
    result = service.process("MSG-1")
    assert result.status is ProcessingStatus.SUCCEEDED
    assert len(result.mentions) == 1
    assert result.mentions[0].evidence_spans == []
    assert result.mentions[0].evidence_records[0].status.value == "TEXT_NOT_FOUND"
    with sqlite3.connect(registry.path) as connection:
        count = connection.execute(
            """
            SELECT COUNT(*) FROM decision_audits
            WHERE run_id = ? AND decision_type = 'MENTION_EVIDENCE_LOCATION'
            """,
            (result.run_id,),
        ).fetchone()[0]
    assert count >= 1


def test_invalid_auxiliary_grounder_evidence_is_retained_without_losing_document(
    registry: SQLiteCDECRRegistry,
) -> None:
    save_source(registry, source())
    service = SingleDocumentProcessor(
        registry=registry,
        embedding_client=FakeEmbedding(),
        m2_client=FakeStructured(model="deepseek-v4-flash"),
        m3_client=InvalidAuxiliaryGrounderEvidence(model="qwen3.7-plus"),
        m4_client=FakeStructured(model="qwen3.7-max"),
    )
    result = service.process("MSG-1")
    assert result.status is ProcessingStatus.SUCCEEDED
    assert len(result.mentions) == 1
    attribute = result.mentions[0].open_attributes[0]
    assert attribute.evidence_record is not None
    assert attribute.evidence_record.status.value == "TEXT_NOT_FOUND"
    assert attribute.evidence_span is None
    with sqlite3.connect(registry.path) as connection:
        payload = connection.execute(
            """
            SELECT payload_json FROM decision_audits
            WHERE run_id = ? AND decision_type = 'MENTION_EVIDENCE_LOCATION'
              AND json_extract(payload_json, '$.evidence_kind') = 'ATTRIBUTE'
            """,
            (result.run_id,),
        ).fetchone()
    assert payload is not None
    assert json.loads(payload[0])["status"] == "TEXT_NOT_FOUND"


def test_unbounded_time_precision_is_normalized_without_changing_reference_period(
    registry: SQLiteCDECRRegistry,
) -> None:
    save_source(registry, source())
    service = SingleDocumentProcessor(
        registry=registry,
        embedding_client=FakeEmbedding(),
        m2_client=FakeStructured(model="deepseek-v4-flash"),
        m3_client=UnboundedDayPrecisionGrounder(model="qwen3.7-plus"),
        m4_client=FakeStructured(model="qwen3.7-max"),
    )
    result = service.process("MSG-1")
    assert result.status is ProcessingStatus.SUCCEEDED
    assert result.mentions[0].time.precision.value == "UNKNOWN"
    assert result.mentions[0].time.reference_period_id == "FY2026 Q4"
    with sqlite3.connect(registry.path) as connection:
        payload = connection.execute(
            """
            SELECT payload_json FROM decision_audits
            WHERE run_id = ? AND decision_type = 'TIME_SEMANTIC_NORMALIZATION'
            """,
            (result.run_id,),
        ).fetchone()
    assert payload is not None
    assert json.loads(payload[0])["reason_code"] == "NO_EVENT_BOUNDS"


def test_judge_invalid_evidence_change_is_retained_with_valid_field_change(
    registry: SQLiteCDECRRegistry,
) -> None:
    save_source(registry, source())
    service = SingleDocumentProcessor(
        registry=registry,
        embedding_client=FakeEmbedding(),
        m2_client=FakeStructured(model="deepseek-v4-flash"),
        m3_client=FakeStructured(model="qwen3.7-plus"),
        m4_client=InvalidJudgeEvidenceWithValidFieldChange(model="qwen3.7-max"),
    )
    result = service.process("MSG-1")
    assert result.status is ProcessingStatus.SUCCEEDED
    assert result.mentions[0].canonical_proposition == "Micron increased its guidance."
    assert result.mentions[0].evidence_spans == []
    assert result.mentions[0].evidence_records[0].status.value == "TEXT_NOT_FOUND"
    with sqlite3.connect(registry.path) as connection:
        payload = connection.execute(
            """
            SELECT payload_json FROM decision_audits
            WHERE run_id = ? AND decision_type = 'MENTION_EVIDENCE_LOCATION'
            """,
            (result.run_id,),
        ).fetchone()
    assert payload is not None
    assert json.loads(payload[0])["status"] == "TEXT_NOT_FOUND"


def test_judge_keep_target_must_be_a_distinct_final_accept(
    registry: SQLiteCDECRRegistry,
) -> None:
    save_source(registry, source())
    service = SingleDocumentProcessor(
        registry=registry,
        embedding_client=FakeEmbedding(),
        m2_client=FakeStructured(model="deepseek-v4-flash"),
        m3_client=FakeStructured(model="qwen3.7-plus"),
        m4_client=InvalidJudgeKeepTarget(model="qwen3.7-max"),
    )
    result = service.process("MSG-1")
    assert result.status is ProcessingStatus.SUCCEEDED
    assert len(result.mentions) == 1


def test_judge_semantic_repair_failure_degrades_to_grounder_item(
    registry: SQLiteCDECRRegistry,
) -> None:
    save_source(registry, source())
    service = SingleDocumentProcessor(
        registry=registry,
        embedding_client=FakeEmbedding(),
        m2_client=FakeStructured(model="deepseek-v4-flash"),
        m3_client=FakeStructured(model="qwen3.7-plus"),
        m4_client=RepeatedSemanticInvalidJudge(model="qwen3.7-max"),
    )
    result = service.process("MSG-1")
    assert result.status is ProcessingStatus.SUCCEEDED
    assert result.mentions[0].predicate.normalized == "raise_guidance"
    judge_calls = [summary for summary in result.model_calls if summary.stage.startswith("judge")]
    assert [summary.status for summary in judge_calls] == ["SUCCEEDED", "SUCCEEDED"]
    assert judge_calls[1].stage == "judge_item_repair"
    assert not judge_calls[1].repaired
    with sqlite3.connect(registry.path) as connection:
        codes = {
            json.loads(row[0])["code"]
            for row in connection.execute(
                """
                SELECT payload_json FROM decision_audits
                WHERE run_id = ? AND decision_type = 'JUDGE_SEMANTIC_VALIDATION'
                """,
                (result.run_id,),
            )
        }
        degraded = connection.execute(
            """
            SELECT COUNT(*) FROM decision_audits
            WHERE run_id = ? AND decision_type = 'JUDGE_SEMANTIC_DEGRADED'
            """,
            (result.run_id,),
        ).fetchone()[0]
    assert codes == {"GUIDANCE_ASSERTION_CONFLICT"}
    assert degraded == 1


def test_validation_errors_are_json_safe_and_retain_business_code() -> None:
    payload = mention_draft()
    payload["quantities"] = [
        {
            "metric_id": "revenue",
            "value": 1,
            "unit": "USD",
            "raw_text": "$1 revenue",
            "role": "PRIMARY",
        },
        {
            "metric_id": "eps",
            "value": 2,
            "unit": "USD",
            "raw_text": "$2 EPS",
            "role": "PRIMARY",
        },
    ]
    with pytest.raises(Exception) as captured:
        MentionDraft.model_validate(payload)
    errors = _safe_validation_errors(captured.value)
    assert errors
    json.dumps(errors)
    assert any(item["code"] == "PRIMARY_QUANTITY_COUNT" for item in errors)


def test_judge_schema_repair_failure_retains_grounder_batch(
    registry: SQLiteCDECRRegistry,
) -> None:
    save_source(registry, source())
    service = SingleDocumentProcessor(
        registry=registry,
        embedding_client=FakeEmbedding(),
        m2_client=FakeStructured(model="deepseek-v4-flash"),
        m3_client=FakeStructured(model="qwen3.7-plus"),
        m4_client=RepeatedSchemaInvalidJudge(model="qwen3.7-max"),
    )
    result = service.process("MSG-1")
    assert result.status is ProcessingStatus.SUCCEEDED
    assert len(result.mentions) == 1
    with sqlite3.connect(registry.path) as connection:
        degraded = connection.execute(
            """
            SELECT COUNT(*) FROM decision_audits
            WHERE run_id = ? AND decision_type = 'JUDGE_BATCH_DEGRADED'
            """,
            (result.run_id,),
        ).fetchone()[0]
    assert degraded == 1


def test_judge_duplicate_coverage_uses_distinct_immutable_audits_and_recovers(
    registry: SQLiteCDECRRegistry,
) -> None:
    save_source(registry, source())
    service = SingleDocumentProcessor(
        registry=registry,
        embedding_client=FakeEmbedding(),
        m2_client=FakeStructured(model="deepseek-v4-flash"),
        m3_client=FakeStructured(model="qwen3.7-plus"),
        m4_client=DuplicateJudgeCoverage(model="qwen3.7-max"),
    )

    result = service.process("MSG-1")

    assert result.status is ProcessingStatus.SUCCEEDED
    assert len(result.mentions) == 1
    assert any(summary.stage == "judge_coverage_recovery" for summary in result.model_calls)
    with sqlite3.connect(registry.path) as connection:
        audits = connection.execute(
            "SELECT audit_id, payload_json FROM decision_audits "
            "WHERE run_id = ? AND decision_type = 'JUDGE_COVERAGE_DEGRADED'",
            (result.run_id,),
        ).fetchall()
    assert len(audits) == 2
    assert len({audit_id for audit_id, _ in audits}) == 2
    payloads = [json.loads(payload) for _, payload in audits]
    assert any(payload["duplicates"] for payload in payloads)
    assert any(payload["missing"] for payload in payloads)


def test_dreamer_drops_invalid_quote_and_recovers_without_model_offsets(
    registry: SQLiteCDECRRegistry,
) -> None:
    save_source(registry, source())
    service = SingleDocumentProcessor(
        registry=registry,
        embedding_client=FakeEmbedding(),
        m2_client=InvalidDreamerEvidence(model="deepseek-v4-flash"),
        m3_client=FakeStructured(model="qwen3.7-plus"),
        m4_client=FakeStructured(model="qwen3.7-max"),
    )
    result = service.process("MSG-1")
    assert result.status is ProcessingStatus.SUCCEEDED
    assert len(result.mentions) == 1
    with sqlite3.connect(registry.path) as connection:
        payload = connection.execute(
            """
            SELECT payload_json FROM decision_audits
            WHERE run_id = ? AND decision_type = 'DREAMER_EVIDENCE_RECONCILIATION'
            """,
            (result.run_id,),
        ).fetchone()
    assert payload is not None
    audit = json.loads(payload[0])
    assert audit["invalid_evidence_locators"] == 1
    assert audit["dropped_candidates"] == 1


def test_grounder_duplicate_atomic_drafts_collapse_without_invalidating_disposition(
    registry: SQLiteCDECRRegistry,
) -> None:
    save_source(registry, source())
    service = SingleDocumentProcessor(
        registry=registry,
        embedding_client=FakeEmbedding(),
        m2_client=FakeStructured(model="deepseek-v4-flash"),
        m3_client=DuplicateGrounderDrafts(model="qwen3.7-plus"),
        m4_client=FakeStructured(model="qwen3.7-max"),
    )
    result = service.process("MSG-1")
    assert result.status is ProcessingStatus.SUCCEEDED
    assert len(result.mentions) == 1
    with sqlite3.connect(registry.path) as connection:
        payload = connection.execute(
            """
            SELECT payload_json FROM decision_audits
            WHERE run_id = ? AND decision_type = 'GROUNDER_DISPOSITION_DEGRADED'
            """,
            (result.run_id,),
        ).fetchone()
    assert payload is None


def test_invalid_grounder_draft_repairs_only_that_draft_and_preserves_result(
    registry: SQLiteCDECRRegistry,
) -> None:
    save_source(registry, source())
    service = SingleDocumentProcessor(
        registry=registry,
        embedding_client=FakeEmbedding(),
        m2_client=FakeStructured(model="deepseek-v4-flash"),
        m3_client=RecoverableGrounderContractDrift(model="qwen3.7-plus"),
        m4_client=FakeStructured(model="qwen3.7-max"),
    )
    result = service.process("MSG-1")
    assert result.status is ProcessingStatus.SUCCEEDED
    assert len(result.mentions) == 1
    assert any(summary.stage == "grounder_item_repair" for summary in result.model_calls)
    assert not any(summary.stage == "grounder_repair" for summary in result.model_calls)
    with sqlite3.connect(registry.path) as connection:
        rows = connection.execute(
            """
            SELECT decision_type, payload_json
            FROM decision_audits
            WHERE run_id = ?
            """,
            (result.run_id,),
        ).fetchall()
    types = {row[0] for row in rows}
    disposition = next(
        json.loads(row[1]) for row in rows if row[0] == "GROUNDER_CANDIDATE_DISPOSITION"
    )
    invalid_draft = next(json.loads(row[1]) for row in rows if row[0] == "GROUNDER_INVALID_DRAFT")
    assert "MODEL_ENUM_NORMALIZATION" in types
    assert invalid_draft["source_candidate_ids"] == ["c1"]
    assert any(error["code"] == "EXTRA_FIELD" for error in invalid_draft["errors"])
    assert invalid_draft["next_action"] == "INDIVIDUAL_ITEM_REPAIR"
    assert disposition["candidate_count"] == 1
    assert disposition["used_candidate_count"] == 1
    assert disposition["rejected_candidate_count"] == 0
    assert disposition["repair_triggered"]
    assert disposition["repair_scope"] == "INDIVIDUAL_INVALID_DRAFT"
    assert disposition["initial_disposition_failures"] == []


def test_multiple_invalid_grounder_drafts_repair_in_parallel(
    registry: SQLiteCDECRRegistry,
) -> None:
    save_source(registry, source())
    grounder = ParallelItemRepairGrounder(model="qwen3.7-plus")
    service = SingleDocumentProcessor(
        registry=registry,
        embedding_client=FakeEmbedding(),
        m2_client=TwoCandidateDreamer(model="deepseek-v4-flash"),
        m3_client=grounder,
        m4_client=FakeStructured(model="qwen3.7-max"),
    )
    result = service.process("MSG-1")
    assert result.status is ProcessingStatus.SUCCEEDED
    assert grounder.max_active_repairs == 2
    assert sum(summary.stage == "grounder_item_repair" for summary in result.model_calls) == 2


def test_grounder_rejection_ledger_preserves_complete_candidate_disposition(
    registry: SQLiteCDECRRegistry,
) -> None:
    save_source(registry, source())
    service = SingleDocumentProcessor(
        registry=registry,
        embedding_client=FakeEmbedding(),
        m2_client=FakeStructured(model="deepseek-v4-flash"),
        m3_client=RejectingGrounder(model="qwen3.7-plus"),
        m4_client=FakeStructured(model="qwen3.7-max"),
    )
    result = service.process("MSG-1")
    assert result.status is ProcessingStatus.SUCCEEDED
    assert result.mentions == []
    assert not result.judge_routing.invoked
    with sqlite3.connect(registry.path) as connection:
        audit = json.loads(
            connection.execute(
                """
                SELECT payload_json FROM decision_audits
                WHERE run_id = ? AND decision_type = 'GROUNDER_CANDIDATE_DISPOSITION'
                """,
                (result.run_id,),
            ).fetchone()[0]
        )
    assert audit["candidate_count"] == 1
    assert audit["used_candidate_count"] == 0
    assert audit["rejected_candidate_count"] == 1
    assert audit["rejected_code_counts"] == {"BACKGROUND": 1}
    assert not audit["repair_triggered"]


def test_missing_recovery_keeps_legal_draft_when_model_also_rejects_candidate(
    registry: SQLiteCDECRRegistry,
) -> None:
    save_source(registry, source())
    service = SingleDocumentProcessor(
        registry=registry,
        embedding_client=FakeEmbedding(),
        m2_client=TwoCandidateDreamer(model="deepseek-v4-flash"),
        m3_client=OverlappingMissingRecoveryGrounder(model="qwen3.7-plus"),
        m4_client=FakeStructured(model="qwen3.7-max"),
    )

    result = service.process("MSG-1")

    assert result.status is ProcessingStatus.SUCCEEDED
    with sqlite3.connect(registry.path) as connection:
        audit = connection.execute(
            """
            SELECT payload_json FROM decision_audits
            WHERE run_id = ?
              AND decision_type = 'GROUNDER_MISSING_RECOVERY_PARTIAL'
            """,
            (result.run_id,),
        ).fetchone()
        disposition = connection.execute(
            """
            SELECT payload_json FROM decision_audits
            WHERE run_id = ?
              AND decision_type = 'GROUNDER_DOCUMENT_DISPOSITION'
            """,
            (result.run_id,),
        ).fetchone()
    assert audit is not None
    assert disposition is not None
    payload = json.loads(audit[0])
    assert payload["failed_candidate_ids"] == []
    assert payload["ignored_rejection_count"] == 1
    disposition_payload = json.loads(disposition[0])
    assert disposition_payload["used_candidate_count"] == 2
    assert disposition_payload["failed_technical_candidate_ids"] == []


def test_second_invalid_dreamer_response_degrades_only_that_block(
    registry: SQLiteCDECRRegistry,
) -> None:
    save_source(registry, source("BAD"))
    save_source(registry, source("GOOD"))
    service, _, m2, _ = processor(registry, invalid_dreamer_responses=2)
    bad = service.process("BAD")
    assert bad.status is ProcessingStatus.SUCCEEDED
    assert len(bad.mentions) == 1
    assert any(summary.stage == "dreamer_zero_recovery" for summary in bad.model_calls)
    with sqlite3.connect(registry.path) as connection:
        rows = connection.execute(
            """
            SELECT payload_json
            FROM decision_audits
            WHERE run_id = ? AND decision_type = 'STRUCTURED_VALIDATION_FAILURE'
            ORDER BY audit_id
            """,
            (bad.run_id,),
        ).fetchall()
    assert len(rows) == 2
    payloads = [json.loads(row[0]) for row in rows]
    assert {payload["attempt"] for payload in payloads} == {"initial", "repair"}
    assert all(payload["error_kind"] == "schema_validation" for payload in payloads)
    assert all(payload["errors"] for payload in payloads)
    serialized = json.dumps(payloads)
    assert "invalid_payload" not in serialized
    assert source("BAD").text not in serialized
    with sqlite3.connect(registry.path) as connection:
        degraded = connection.execute(
            """
            SELECT COUNT(*) FROM decision_audits
            WHERE run_id = ? AND decision_type = 'DREAMER_BLOCK_DEGRADED'
            """,
            (bad.run_id,),
        ).fetchone()[0]
    assert degraded == 1
    m2.invalid_dreamer_responses = 0
    good = service.process("GOOD")
    assert good.status is ProcessingStatus.SUCCEEDED


def test_failed_model_call_is_counted_and_attributed_to_its_stage(
    registry: SQLiteCDECRRegistry,
) -> None:
    save_source(registry, source())
    service = SingleDocumentProcessor(
        registry=registry,
        embedding_client=FailingEmbedding(),
        m2_client=FakeStructured(model="deepseek-v4-flash"),
        m3_client=FakeStructured(model="qwen3.7-plus"),
        m4_client=FakeStructured(model="qwen3.7-max"),
    )
    result = service.process("MSG-1")
    assert result.status is ProcessingStatus.FAILED
    assert result.failures[0].stage == "title_embedding"
    assert result.model_calls[0].status == "FAILED"
    assert result.model_calls[0].error_code == "provider_arrearage"
    assert registry.count_model_calls(run_id=result.run_id) == 1


def test_processing_key_rerun_has_zero_new_calls_and_mentions(
    registry: SQLiteCDECRRegistry,
) -> None:
    save_source(registry, source())
    service, _, m2, m3 = processor(registry)
    first = service.process("MSG-1")
    assert isinstance(service.m4_client, FakeStructured)
    call_count = len(m2.calls) + len(m3.calls) + len(service.m4_client.calls)
    with sqlite3.connect(registry.path) as connection:
        mention_count = connection.execute("SELECT COUNT(*) FROM event_mentions").fetchone()[0]
    second = service.process("MSG-1")
    assert first.mentions[0].mention_id == second.mentions[0].mention_id
    assert second.reused
    assert second.model_calls == []
    assert len(m2.calls) + len(m3.calls) + len(service.m4_client.calls) == call_count
    with sqlite3.connect(registry.path) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM event_mentions").fetchone()[0] == mention_count
        )


def test_exact_duplicate_reuses_prior_result_without_model_calls(
    registry: SQLiteCDECRRegistry,
) -> None:
    first_source = source("A")
    second_source = first_source.model_copy(
        update={"message_id": "B", "url": "https://other.test/reprint"}
    )
    save_source(registry, first_source)
    save_source(registry, second_source)
    service, embedding, m2, m3 = processor(registry)
    first = service.process("A")
    assert isinstance(service.m4_client, FakeStructured)
    call_count = len(embedding.calls) + len(m2.calls) + len(m3.calls) + len(service.m4_client.calls)
    second = service.process("B")
    assert first.status is second.status is ProcessingStatus.SUCCEEDED
    assert second.reused
    assert second.mentions[0].message_id == "B"
    assert second.mentions[0].mention_id != first.mentions[0].mention_id
    assert (
        len(embedding.calls) + len(m2.calls) + len(m3.calls) + len(service.m4_client.calls)
        == call_count
    )


def test_judge_application_supports_revision_reject_split_duplicate_and_attribute_merge(
    registry: SQLiteCDECRRegistry,
) -> None:
    service, _, _, _ = processor(registry)
    base = MentionDraft.model_validate(mention_draft())
    revised = base.model_copy(update={"canonical_proposition": "Revised supported claim."})
    drafts = [
        GroundedMentionDraft(
            draft_id=f"D{index}",
            source_candidate_ids=["C1"],
            mention=base,
        )
        for index in range(1, 6)
    ]
    duplicate_evidence = EvidenceText(segment_id="text:0", text="strong demand")
    drafts[3] = drafts[3].model_copy(
        update={"mention": base.model_copy(update={"evidence_locations": [duplicate_evidence]})}
    )
    attribute = OpenAttributeDraft(
        key="reason",
        value="strong demand",
        evidence_location=EvidenceText(segment_id="text:0", text="Micron raised guidance"),
    )
    decisions = [
        JudgeDecisionRecord(
            decision_id="J1",
            target_draft_id="D1",
            action=JudgeAction.ACCEPT,
            reason="revise",
            revised_mention=revised,
        ),
        JudgeDecisionRecord(
            decision_id="J2",
            target_draft_id="D2",
            action=JudgeAction.REJECT,
            reason="unsupported",
        ),
        JudgeDecisionRecord(
            decision_id="J3",
            target_draft_id="D3",
            action=JudgeAction.SPLIT,
            reason="two claims",
            split_mentions=[base, revised],
        ),
        JudgeDecisionRecord(
            decision_id="J4",
            target_draft_id="D4",
            action=JudgeAction.DUPLICATE,
            reason="duplicate",
            target_mention_id="D1",
        ),
        JudgeDecisionRecord(
            decision_id="J5",
            target_draft_id="D5",
            action=JudgeAction.MERGE_AS_ATTRIBUTE,
            reason="attribute",
            target_mention_id="D1",
            attribute=attribute,
        ),
    ]
    accepted = service._apply_judge(drafts, decisions)
    assert len(accepted) == 3
    assert accepted[0].canonical_proposition == "Revised supported claim."
    assert accepted[0].open_attributes == [attribute]
    assert accepted[0].evidence_locations == [
        *revised.evidence_locations,
        duplicate_evidence,
    ]
