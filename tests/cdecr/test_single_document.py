from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from cdecr.contracts import Language, SourceMessage, SourceType
from cdecr.models import ModelAdapterError, ModelTier
from cdecr.ports import EmbeddingResult, StructuredModelRequest, StructuredModelResult
from cdecr.preprocessing import exact_document_fingerprint
from cdecr.registry import SQLiteCDECRRegistry
from cdecr.single_document import SingleDocumentProcessor
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
                                    "start_char": 0,
                                    "end_char": 22,
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
                return self._result({"drafts": [], "issue_flags": []})
            candidate_id = candidates[0]["candidate_id"]
            return self._result(
                {
                    "drafts": [
                        {
                            "source_candidate_ids": [candidate_id],
                            "mention": mention_draft(),
                        }
                    ],
                    "issue_flags": [],
                }
            )
        if title == "JudgeModelOutput":
            request_payload = json.loads(request.user_prompt)
            return self._result(
                {
                    "decisions": [
                        {
                            "target_draft_id": draft["draft_id"],
                            "action": "ACCEPT",
                            "reason": "supported",
                            "revised_mention": None,
                            "split_mentions": [],
                            "target_mention_id": None,
                            "attribute": None,
                        }
                        for draft in request_payload["drafts"]
                    ]
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


class InvalidDreamerEvidence(FakeStructured):
    def complete(self, request: StructuredModelRequest) -> StructuredModelResult:
        result = super().complete(request)
        if request.json_schema.get("title") != "DreamerModelOutput":
            return result
        candidates = result.payload["candidates"]
        assert isinstance(candidates, list) and candidates
        candidates[0]["evidence_locations"] = [
            {
                "segment_id": "text:0",
                "start_char": 0,
                "end_char": 25,
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


class RecoverableGrounderContractDrift(FakeStructured):
    def complete(self, request: StructuredModelRequest) -> StructuredModelResult:
        result = super().complete(request)
        if request.json_schema.get("title") != "GrounderModelOutput":
            return result
        drafts = result.payload["drafts"]
        assert isinstance(drafts, list) and drafts
        drafts[0]["source_candidate_ids"] = []
        drafts[0]["mention"]["event_family"] = "MODEL_INVENTED_FAMILY"
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


def test_dreamer_contract_bounds_candidates_per_block() -> None:
    schema = DreamerModelOutput.model_json_schema()
    assert schema["properties"]["candidates"]["maxItems"] == 24


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
        "JudgeModelOutput"
    ]
    assert len(embedding.calls) == 1
    assert result.judge_routing.invoked
    assert result.judge_routing.reasons == ["all_grounder_drafts_m4"]


def test_no_event_document_still_runs_grounder_and_persists_empty_result(
    registry: SQLiteCDECRRegistry,
) -> None:
    save_source(registry, source())
    service, _, _, m3 = processor(registry, no_events=True)
    result = service.process("MSG-1")
    assert result.status is ProcessingStatus.SUCCEEDED
    assert result.mentions == []
    assert [request.json_schema["title"] for request in m3.calls] == ["GrounderModelOutput"]


def test_all_grounder_drafts_route_one_batch_m4_judge(
    registry: SQLiteCDECRRegistry,
) -> None:
    save_source(registry, source())
    service, _, m2, _ = processor(registry)
    result = service.process("MSG-1")
    assert result.status is ProcessingStatus.SUCCEEDED
    assert result.judge_routing.invoked
    assert result.judge_routing.reasons == ["all_grounder_drafts_m4"]
    assert [request.json_schema["title"] for request in m2.calls].count("JudgeModelOutput") == 0
    assert isinstance(service.m4_client, FakeStructured)
    assert [request.json_schema["title"] for request in service.m4_client.calls].count(
        "JudgeModelOutput"
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
    assert m3_titles.count("GrounderModelOutput") == 1
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


def test_invalid_json_gets_one_audited_repair(registry: SQLiteCDECRRegistry) -> None:
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
    assert grounder_calls[0].error_code == "invalid_json"
    assert grounder_calls[0].input_tokens == 20
    assert grounder_calls[1].repaired


def test_invalid_grounder_evidence_is_rejected_by_m4_without_fallback_fabrication(
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
    assert result.status is ProcessingStatus.FAILED
    assert result.mentions == []
    assert result.failures[0].stage == "judge"
    with sqlite3.connect(registry.path) as connection:
        count = connection.execute(
            """
            SELECT COUNT(*) FROM decision_audits
            WHERE run_id = ? AND decision_type = 'STRUCTURED_VALIDATION_FAILURE'
            """,
            (result.run_id,),
        ).fetchone()[0]
    assert count == 2


def test_dreamer_drops_only_candidates_without_valid_evidence(
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
    assert result.mentions == []
    with sqlite3.connect(registry.path) as connection:
        payload = connection.execute(
            """
            SELECT payload_json FROM decision_audits
            WHERE run_id = ? AND decision_type = 'DREAMER_EVIDENCE_RECONCILIATION'
            """,
            (result.run_id,),
        ).fetchone()
    assert payload is not None
    assert json.loads(payload[0])["dropped_candidates"] == 1


def test_materialized_mentions_with_same_identity_are_deduplicated(
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
            WHERE run_id = ? AND decision_type = 'DOCUMENT_MENTION_DEDUPLICATION'
            """,
            (result.run_id,),
        ).fetchone()
    assert payload is not None
    assert json.loads(payload[0])["duplicate_identity_count"] == 1


def test_missing_short_candidate_id_fails_strict_grounder_contract(
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
    assert result.status is ProcessingStatus.FAILED
    assert result.mentions == []
    with sqlite3.connect(registry.path) as connection:
        types = {
            row[0]
            for row in connection.execute(
                "SELECT decision_type FROM decision_audits WHERE run_id = ?",
                (result.run_id,),
            )
        }
    assert "MODEL_ENUM_NORMALIZATION" in types
    assert "STRUCTURED_VALIDATION_FAILURE" in types


def test_second_invalid_structured_response_fails_only_that_document(
    registry: SQLiteCDECRRegistry,
) -> None:
    save_source(registry, source("BAD"))
    save_source(registry, source("GOOD"))
    service, _, m2, _ = processor(registry, invalid_dreamer_responses=2)
    bad = service.process("BAD")
    assert bad.status is ProcessingStatus.FAILED
    assert bad.failures[0].error_code == "schema_validation_failed_after_repair"
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
    call_count = (
        len(embedding.calls) + len(m2.calls) + len(m3.calls) + len(service.m4_client.calls)
    )
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
    attribute = OpenAttributeDraft(
        key="reason",
        value="strong demand",
        evidence_location=EvidenceText(
            segment_id="text:0", text="Micron raised guidance"
        ),
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
