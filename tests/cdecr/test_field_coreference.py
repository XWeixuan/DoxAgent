from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from cdecr.contracts import (
    AssertionState,
    AtomicEvent,
    EventFamily,
    EventMention,
    EventTime,
    EvidenceSpan,
    Language,
    OpenIdentityFields,
    OpenIdentityProfile,
    Participant,
    ParticipantRole,
    Predicate,
    SourceMessage,
    SourceType,
    TimePrecision,
)
from cdecr.field_coreference import (
    FIELD_REGISTRY_OWNER_KIND,
    FieldCoreferenceError,
    FieldCoreferenceResolver,
    PreparedFieldDecision,
    _batch_decision_schema,
    field_inputs_for_mention,
    normalize_field_text,
    normalize_field_wire_item,
    resolved_field_entries,
    trusted_field_identity_conflict,
)
from cdecr.field_coreference_contracts import (
    CanonicalFieldLink,
    CanonicalFieldRegistryEntry,
    FieldCoreferenceCandidate,
    FieldCoreferenceInput,
    FieldCoreferenceModelOutput,
    FieldCoreferenceResult,
    FieldDecision,
    FieldLinkMethod,
    FieldNamespace,
)
from cdecr.ports import EmbeddingResult, StructuredModelRequest, StructuredModelResult
from cdecr.registry import (
    SCHEMA_VERSION,
    ImmutableRecordConflict,
    RegistryError,
    SQLiteCDECRRegistry,
)


def test_field_wire_normalization_is_lossless_for_forbidden_extras() -> None:
    normalized = normalize_field_wire_item(
        {
            "decision": " new ",
            "canonical_id": "should-drop",
            "target_namespace": "metric",
            "ignored": "value",
        },
        FieldNamespace.CONCEPT_PREDICATE,
        {},
    )
    assert normalized.output.decision is FieldDecision.NEW
    assert normalized.output.canonical_id is None
    assert normalized.output.target_namespace is None
    assert set(normalized.rules) == {
        "DROP_NON_LINK_CANONICAL_ID",
        "DROP_FORBIDDEN_TARGET_NAMESPACE",
    }


def test_field_batch_schema_restricts_target_namespace_by_input_namespace() -> None:
    ordinary = _batch_decision_schema(FieldNamespace.METRIC)
    unknown = _batch_decision_schema(FieldNamespace.PARTICIPANT_UNKNOWN)
    ordinary_target = ordinary["properties"]["decisions"]["items"]["properties"][
        "target_namespace"
    ]
    unknown_target = unknown["properties"]["decisions"]["items"]["properties"][
        "target_namespace"
    ]
    assert ordinary_target == {"type": "null"}
    assert None in unknown_target["enum"]


class FakeEmbeddingClient:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> EmbeddingResult:
        values = list(texts)
        self.calls.append(values)
        vectors = [
            [float(len(value)), float(sum(ord(char) for char in value) % 997)] for value in values
        ]
        return EmbeddingResult(
            model="text-embedding-v4",
            dimensions=2,
            vectors=vectors,
            input_tokens=len(values),
            latency_ms=1,
        )


class FakeStructuredClient:
    def __init__(self, payloads: list[dict[str, object]] | None = None) -> None:
        self.payloads = list(payloads or [])
        self.requests: list[StructuredModelRequest] = []

    def complete(self, request: StructuredModelRequest) -> StructuredModelResult:
        self.requests.append(request)
        if not self.payloads:
            raise AssertionError("unexpected model call")
        return StructuredModelResult(
            model="deepseek-v4-flash",
            payload=self.payloads.pop(0),
            input_tokens=10,
            output_tokens=2,
            latency_ms=1,
            transport="chat_json_object",
            output_mode="json_object",
            effective_reasoning_effort="none",
            provider_key_fingerprint="test-key-fingerprint",
        )


class EchoFieldBatchClient:
    def __init__(self) -> None:
        self.requests: list[StructuredModelRequest] = []

    def complete(self, request: StructuredModelRequest) -> StructuredModelResult:
        self.requests.append(request)
        tasks = json.loads(request.user_prompt)["tasks"]
        return StructuredModelResult(
            model="fake",
            payload={
                "decisions": [
                    {
                        "task_id": task["task_id"],
                        "decision": "UNRESOLVED",
                        "canonical_id": None,
                        "target_namespace": None,
                    }
                    for task in tasks
                ]
            },
            input_tokens=1,
            output_tokens=1,
            latency_ms=1,
        )


class InvalidCoverageOnceFieldClient(EchoFieldBatchClient):
    def complete(self, request: StructuredModelRequest) -> StructuredModelResult:
        if not self.requests:
            self.requests.append(request)
            return StructuredModelResult(
                model="fake",
                payload={"decisions": []},
                input_tokens=1,
                output_tokens=1,
                latency_ms=1,
            )
        return super().complete(request)


def test_planned_field_decisions_pack_by_namespace_and_cover_every_item(
    registry: SQLiteCDECRRegistry,
) -> None:
    model = EchoFieldBatchClient()
    resolver = FieldCoreferenceResolver(
        registry=registry,
        embedding_client=FakeEmbeddingClient(),
        model_client=model,
    )
    plans = [
        PreparedFieldDecision(
            semantic_task_id=f"task-{index:02d}",
            value=FieldCoreferenceInput(
                namespace=FieldNamespace.OBJECT_FACILITY,
                raw_value=f"facility {index}",
                local_context="facility context",
            ),
            candidates=(),
            mention_id=f"mention-{index}",
            field_path="open_attributes[0].value",
            run_id=None,
        )
        for index in range(13)
    ]
    outputs, errors, telemetry = resolver.decide_prepared(plans, max_workers=4)
    assert not errors
    assert set(outputs) == {plan.semantic_task_id for plan in plans}
    assert len(model.requests) == 2
    assert telemetry["planned_item_count"] == 13
    assert telemetry["physical_batch_count"] == 2
    assert telemetry["batch_size_max"] == 12
    assert telemetry["item_fallback_count"] == 0


def test_planned_field_invalid_batch_coverage_splits_once_without_losing_items(
    registry: SQLiteCDECRRegistry,
) -> None:
    model = InvalidCoverageOnceFieldClient()
    resolver = FieldCoreferenceResolver(
        registry=registry,
        embedding_client=FakeEmbeddingClient(),
        model_client=model,
    )
    plans = [
        PreparedFieldDecision(
            semantic_task_id=f"task-{index}",
            value=FieldCoreferenceInput(
                namespace=FieldNamespace.OBJECT_PROJECT,
                raw_value=f"project {index}",
                local_context="project context",
            ),
            candidates=(),
            mention_id=f"mention-{index}",
            field_path="open_attributes[0].value",
            run_id=None,
        )
        for index in range(4)
    ]
    outputs, errors, telemetry = resolver.decide_prepared(plans, max_workers=1)
    assert not errors
    assert set(outputs) == {plan.semantic_task_id for plan in plans}
    assert len(model.requests) == 3
    assert telemetry["batch_split_count"] == 1


def test_field_item_repair_uses_local_t1_and_is_fully_audited(
    registry: SQLiteCDECRRegistry,
) -> None:
    model = FakeStructuredClient(
        [
            {
                "decisions": [
                    {
                        "task_id": "t1",
                        "decision": "LINK",
                        "canonical_id": "k99",
                        "target_namespace": None,
                    }
                ]
            },
            {
                "decisions": [
                    {
                        "task_id": "t1",
                        "decision": "UNRESOLVED",
                        "canonical_id": None,
                        "target_namespace": None,
                    }
                ]
            },
        ]
    )
    resolver = FieldCoreferenceResolver(
        registry=registry,
        embedding_client=FakeEmbeddingClient(),
        model_client=model,
    )
    plan = PreparedFieldDecision(
        semantic_task_id="semantic-task-7",
        value=FieldCoreferenceInput(
            namespace=FieldNamespace.OBJECT_PROJECT,
            raw_value="project seven",
            local_context="project context",
        ),
        candidates=(),
        mention_id="mention-7",
        field_path="open_attributes[0].value",
        run_id=None,
    )

    outputs, errors, telemetry = resolver.decide_prepared([plan], max_workers=1)

    assert not errors
    assert outputs[plan.semantic_task_id].decision is FieldDecision.UNRESOLVED
    repair_payload = json.loads(model.requests[1].user_prompt)
    assert [task["task_id"] for task in repair_payload["tasks"]] == ["t1"]
    calls = registry.list_model_call_summaries()
    assert sum(call.stage == "field_coreference_item_repair" for call in calls) == 1
    assert telemetry["item_repair_request_count"] == 1
    assert telemetry["item_repair_succeeded_count"] == 1


def test_field_item_repair_local_forbidden_error_falls_back_without_task_failure(
    registry: SQLiteCDECRRegistry,
) -> None:
    invalid = {
        "decisions": [
            {
                "task_id": "t1",
                "decision": "NEW",
                "canonical_id": None,
                "target_namespace": "participant.company",
            }
        ]
    }
    model = FakeStructuredClient([invalid, invalid])
    resolver = FieldCoreferenceResolver(
        registry=registry,
        embedding_client=FakeEmbeddingClient(),
        model_client=model,
    )
    plan = PreparedFieldDecision(
        semantic_task_id="semantic-task-local-invalid",
        value=FieldCoreferenceInput(
            namespace=FieldNamespace.OBJECT_PROJECT,
            raw_value="project local invalid",
            local_context="project context",
        ),
        candidates=(),
        mention_id="mention-local-invalid",
        field_path="open_attributes[0].value",
        run_id=None,
    )

    outputs, errors, telemetry = resolver.decide_prepared([plan], max_workers=1)

    assert not errors
    assert outputs[plan.semantic_task_id].decision is FieldDecision.NEW
    assert telemetry["item_repair_request_count"] == 0
    assert telemetry["item_repair_provider_failed_count"] == 0


def test_apply_prepared_reuses_existing_external_identity_before_immutable_create(
    registry: SQLiteCDECRRegistry,
) -> None:
    mention = _mention("M-APPLY-REUSE", "S-APPLY-REUSE")
    _persist_mention(registry, mention)
    resolver = FieldCoreferenceResolver(
        registry=registry,
        embedding_client=FakeEmbeddingClient(),
        model_client=FakeStructuredClient(),
    )
    transient, dimensions = resolver.transient_external_entry(
        external_id="KB:PROJECT:7",
        canonical_text="Project Seven",
        aliases=["Project Seven", "Seventh Project"],
        namespace=FieldNamespace.OBJECT_PROJECT,
        hard_dimensions={},
    )
    registry.create_field_registry_entry(
        transient.model_copy(update={"aliases": ["Project Seven"]})
    )
    plan = PreparedFieldDecision(
        semantic_task_id="apply-reuse",
        value=FieldCoreferenceInput(
            namespace=FieldNamespace.OBJECT_PROJECT,
            raw_value="Seventh Project",
            local_context="Project Seven was discussed.",
        ),
        candidates=(
            FieldCoreferenceCandidate(
                canonical_id=transient.id,
                aliases=["Project Seven"],
                hard_dimensions={},
            ),
        ),
        mention_id=mention.mention_id,
        field_path="open_attributes[0].value",
        run_id=None,
        transient_entries=(transient,),
        transient_dimensions=((transient.id, dimensions),),
    )

    result = resolver.apply_prepared(
        plan,
        FieldCoreferenceModelOutput(
            decision=FieldDecision.LINK,
            canonical_id=transient.id,
        ),
    )

    assert result.canonical_id == transient.id


def test_epoch_field_requests_are_namespace_batched_with_simple_wire_schema(
    registry: SQLiteCDECRRegistry,
) -> None:
    mention = _mention("M-BATCH", "S-BATCH")
    _persist_mention(registry, mention)
    registry.create_field_registry_entry(
        CanonicalFieldRegistryEntry(
            id="FIELD-ONE",
            namespace=FieldNamespace.OBJECT_FACILITY,
            canonical_text="Boise facility one",
            aliases=["Boise facility one"],
        )
    )
    model = FakeStructuredClient(
        [
            {
                "decisions": [
                    {
                        "task_id": "t1",
                        "decision": "UNRESOLVED",
                        "canonical_id": None,
                        "target_namespace": None,
                    },
                    {
                        "task_id": "t2",
                        "decision": "UNRESOLVED",
                        "canonical_id": None,
                        "target_namespace": None,
                    },
                ]
            }
        ]
    )
    resolver = FieldCoreferenceResolver(
        registry=registry,
        embedding_client=FakeEmbeddingClient(),
        model_client=model,
    )
    values = [
        FieldCoreferenceInput(
            namespace=FieldNamespace.OBJECT_FACILITY,
            raw_value=f"Unclear facility {index}",
            local_context=f"Unclear facility {index} was discussed.",
        )
        for index in range(2)
    ]
    resolver.begin_epoch_snapshot()
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(
                    lambda item: resolver.resolve(
                        item[1],
                        mention_id=mention.mention_id,
                        field_path=f"open_attributes[{item[0]}].value",
                    ),
                    enumerate(values),
                )
            )
    finally:
        resolver.end_epoch_snapshot()
    assert all(result.canonical_id for result in results)
    assert len(model.requests) == 1
    payload = json.loads(model.requests[0].user_prompt)
    assert len(payload["tasks"]) == 2
    assert isinstance(payload["candidates"], dict)
    assert "allOf" not in json.dumps(model.requests[0].json_schema)
    with sqlite3.connect(registry.path) as connection:
        metadata = json.loads(
            connection.execute(
                "SELECT metadata_json FROM model_calls "
                "WHERE stage = 'field_coreference' ORDER BY created_at DESC LIMIT 1"
            ).fetchone()[0]
        )
    assert metadata["transport"] == "chat_json_object"
    assert metadata["output_mode"] == "json_object"
    assert metadata["effective_reasoning_effort"] == "none"
    assert metadata["provider_key_fingerprint"] == "test-key-fingerprint"


def _source(message_id: str) -> SourceMessage:
    return SourceMessage(
        message_id=message_id,
        source_type=SourceType.NEWS,
        title="Micron expands Fab 21",
        text="Micron said Fab 21 in Boise will expand.",
        published_at=datetime(2026, 7, 1, tzinfo=UTC),
        source_name="Wire",
        url=f"https://example.test/{message_id}",
        ticker_hints=["MU"],
        language=Language.EN,
    )


def _mention(
    mention_id: str,
    message_id: str,
    *,
    facility: str = "Fab 21",
) -> EventMention:
    return EventMention(
        mention_id=mention_id,
        message_id=message_id,
        evidence_spans=[EvidenceSpan(field="title", start_char=0, end_char=6, text="Micron")],
        canonical_proposition="Micron expands a fabrication facility.",
        source_claim=None,
        event_family=EventFamily.COMMERCIAL_OPERATION,
        predicate=Predicate(raw="expands", normalized="expand_facility"),
        participants=[Participant(surface="Micron", entity_id=None, role=ParticipantRole.SUBJECT)],
        locations=[],
        time=EventTime(event_start=date(2026, 7, 1), precision=TimePrecision.DAY),
        assertion_state=AssertionState.PLANNED,
        quantities=[],
        open_attributes=[],
    ).model_copy(
        update={
            "open_attributes": [],
            "locations": [facility],
        }
    )


def _atomic(mention: EventMention) -> AtomicEvent:
    return AtomicEvent(
        event_id=f"EVENT-{mention.mention_id}",
        canonical_proposition=mention.canonical_proposition,
        event_family=mention.event_family,
        identity_profile=OpenIdentityProfile(
            fields=OpenIdentityFields(
                normalized_predicate=mention.predicate.normalized,
                principal_participant_ids=[],
                event_time=mention.time,
                reference_period_id=None,
                location_or_asset_ids=mention.locations,
                assertion_state=mention.assertion_state,
            )
        ),
        time=mention.time,
        assertion_state=mention.assertion_state,
        mention_ids=[mention.mention_id],
        representative_mention_ids=[mention.mention_id],
        consensus_claims={},
        conflict_flags=[],
        version=1,
    )


@pytest.fixture
def registry(tmp_path: Path) -> SQLiteCDECRRegistry:
    value = SQLiteCDECRRegistry(tmp_path / "field.sqlite3")
    value.initialize()
    return value


def _persist_mention(registry: SQLiteCDECRRegistry, mention: EventMention) -> None:
    registry.save_source(_source(mention.message_id), fingerprint=mention.message_id * 8)
    registry.save_mention(mention)


def _input(mention: EventMention, raw_value: str = "Fab 21") -> FieldCoreferenceInput:
    return FieldCoreferenceInput(
        namespace=FieldNamespace.OBJECT_FACILITY,
        raw_value=raw_value,
        local_context=mention.canonical_proposition,
    )


def _resolve(
    resolver: FieldCoreferenceResolver,
    mention: EventMention,
    raw_value: str = "Fab 21",
) -> FieldCoreferenceResult:
    return resolver.resolve(
        _input(mention, raw_value),
        mention_id=mention.mention_id,
        field_path="locations[0]",
    )


def test_contracts_are_strict_and_model_decision_target_is_conditional() -> None:
    assert set(FieldCoreferenceInput.model_fields) == {
        "namespace",
        "raw_value",
        "local_context",
        "hints",
    }
    assert set(FieldCoreferenceResult.model_fields) == {
        "canonical_id",
        "external_id",
        "resolution_method",
    }
    assert (
        FieldCoreferenceModelOutput(
            decision=FieldDecision.LINK, canonical_id="FIELD-1"
        ).canonical_id
        == "FIELD-1"
    )
    with pytest.raises(ValidationError):
        FieldCoreferenceModelOutput(decision=FieldDecision.NEW, canonical_id="FIELD-1")
    with pytest.raises(ValidationError):
        FieldCoreferenceCandidate(canonical_id="FIELD-1", aliases=[])
    with pytest.raises(ValidationError):
        CanonicalFieldRegistryEntry(
            id="FIELD-1",
            namespace=FieldNamespace.OBJECT_FACILITY,
            canonical_text="Fab 21",
            aliases=["Fab 21"],
            extra="forbidden",  # type: ignore[call-arg]
        )


def test_normalization_is_deterministic_and_namespace_is_typed() -> None:
    assert normalize_field_text("  Micron_Technology, Inc.  ", company_suffixes=True) == (
        "micron technology"
    )
    assert FieldNamespace.OBJECT_PROJECT.value == "object.project"


def test_empty_registry_creates_once_and_exact_alias_reuses_without_llm(
    registry: SQLiteCDECRRegistry,
) -> None:
    first = _mention("M-1", "S-1")
    second = _mention("M-2", "S-2")
    _persist_mention(registry, first)
    _persist_mention(registry, second)
    model = FakeStructuredClient()
    resolver = FieldCoreferenceResolver(
        registry=registry,
        embedding_client=FakeEmbeddingClient(),
        model_client=model,
    )

    created = _resolve(resolver, first)
    reused = _resolve(resolver, second, "Fab-21")

    assert created.canonical_id is not None
    assert reused.canonical_id == created.canonical_id
    assert not model.requests
    assert _resolve(resolver, first).canonical_id == created.canonical_id


def test_embedding_recall_uses_strict_model_and_one_repair(
    registry: SQLiteCDECRRegistry,
) -> None:
    mention = _mention("M-1", "S-1")
    _persist_mention(registry, mention)
    entry = CanonicalFieldRegistryEntry(
        id="FIELD-FAB",
        namespace=FieldNamespace.OBJECT_FACILITY,
        canonical_text="Boise fabrication complex",
        aliases=["Boise fabrication complex"],
    )
    registry.create_field_registry_entry(entry)
    model = FakeStructuredClient(
        [
            {"decision": "LINK", "canonical_id": "NOT-A-CANDIDATE"},
            {"decision": "LINK", "canonical_id": "FIELD-FAB"},
        ]
    )
    embeddings = FakeEmbeddingClient()
    resolver = FieldCoreferenceResolver(
        registry=registry, embedding_client=embeddings, model_client=model
    )

    result = _resolve(resolver, mention, "Boise fab complex")

    assert result.canonical_id == "FIELD-FAB"
    assert len(model.requests) == 2
    assert _resolve(resolver, mention, "Boise fab complex").canonical_id == "FIELD-FAB"
    assert len(model.requests) == 2
    assert (
        registry.get_embedding(
            owner_kind=FIELD_REGISTRY_OWNER_KIND,
            owner_id="FIELD-FAB",
            model="text-embedding-v4",
            input_hash=next(
                item.input_hash
                for item in registry.list_latest_embeddings(
                    owner_kind=FIELD_REGISTRY_OWNER_KIND, model="text-embedding-v4"
                )
            ),
        )
        is not None
    )


def test_second_invalid_model_output_fails_without_loose_parsing(
    registry: SQLiteCDECRRegistry,
) -> None:
    mention = _mention("M-1", "S-1")
    _persist_mention(registry, mention)
    registry.create_field_registry_entry(
        CanonicalFieldRegistryEntry(
            id="FIELD-FAB",
            namespace=FieldNamespace.OBJECT_FACILITY,
            canonical_text="Other complex",
            aliases=["Other complex"],
        )
    )
    resolver = FieldCoreferenceResolver(
        registry=registry,
        embedding_client=FakeEmbeddingClient(),
        model_client=FakeStructuredClient([{"bad": True}, {"decision": "LINK"}]),
    )
    with pytest.raises(FieldCoreferenceError):
        _resolve(resolver, mention, "lowercase unclear phrase")


def test_generic_value_is_persisted_as_unresolved_canonical(
    registry: SQLiteCDECRRegistry,
) -> None:
    mention = _mention("M-1", "S-1")
    _persist_mention(registry, mention)
    resolver = FieldCoreferenceResolver(
        registry=registry,
        embedding_client=FakeEmbeddingClient(),
        model_client=FakeStructuredClient(
            [{"decision": "NEW", "target_namespace": "participant.company"}]
        ),
    )
    result = _resolve(resolver, mention, "the facility")
    assert result.canonical_id is not None
    assert result.resolution_method is not None
    assert result.resolution_method.value == "UNRESOLVED_CANONICALIZED"
    assert registry.get_field_link(mention.mention_id, "locations[0]") is not None
    unknown = resolver.resolve(
        FieldCoreferenceInput(
            namespace=FieldNamespace.PARTICIPANT_UNKNOWN,
            raw_value="Unclassified Name",
            local_context="Unclassified Name appeared in the report.",
        ),
        mention_id=mention.mention_id,
        field_path="participants[0]",
    )
    assert unknown.canonical_id is not None
    entry = registry.resolve_field_registry_entry(unknown.canonical_id)
    assert entry is not None
    assert entry.namespace is FieldNamespace.PARTICIPANT_COMPANY
    assert not registry.list_field_registry_entries(namespace=FieldNamespace.PARTICIPANT_UNKNOWN)


def test_prompt_v2_policies_and_llm_visible_candidate_contract(
    registry: SQLiteCDECRRegistry,
) -> None:
    mention = _mention("M-1", "S-1")
    _persist_mention(registry, mention)
    registry.create_field_registry_entry(
        CanonicalFieldRegistryEntry(
            id="FIELD-FAB",
            namespace=FieldNamespace.OBJECT_FACILITY,
            canonical_text="Boise fabrication complex",
            aliases=["Boise fabrication complex", "Boise fab"],
        )
    )
    model = FakeStructuredClient([{"decision": "LINK", "canonical_id": "k1"}])
    resolver = FieldCoreferenceResolver(
        registry=registry,
        embedding_client=FakeEmbeddingClient(),
        model_client=model,
        catalog_hash="catalog-hash",
    )
    result = _resolve(resolver, mention, "Boise fab complex")
    assert result.canonical_id == "FIELD-FAB"
    assert len(resolver._policies) == len(FieldNamespace)
    request = model.requests[0]
    assert request.system_prompt.startswith(
        "Resolve each independent typed field task against only its supplied "
        "same-namespace candidates."
    )
    payload = json.loads(request.user_prompt)
    candidate = payload["candidates"][0]
    assert set(candidate) == {"canonical_id", "aliases", "hard_dimensions"}
    assert candidate["canonical_id"] == "k1"
    assert "canonical_text" not in request.user_prompt
    assert "external_id" not in request.user_prompt


def test_invalid_unknown_participant_namespace_repairs_to_unresolved(
    registry: SQLiteCDECRRegistry,
) -> None:
    mention = _mention("M-1", "S-1")
    _persist_mention(registry, mention)
    resolver = FieldCoreferenceResolver(
        registry=registry,
        embedding_client=FakeEmbeddingClient(),
        model_client=FakeStructuredClient(
            [
                {"decision": "NEW", "target_namespace": "made_up_organization_type"},
                {
                    "decision": "UNRESOLVED",
                    "canonical_id": None,
                    "target_namespace": None,
                },
            ]
        ),
    )

    result = resolver.resolve(
        FieldCoreferenceInput(
            namespace=FieldNamespace.PARTICIPANT_UNKNOWN,
            raw_value="Ambiguous Group",
            local_context="Ambiguous Group appeared in the report.",
        ),
        mention_id=mention.mention_id,
        field_path="participants[0]",
    )

    assert result.canonical_id is not None
    assert result.resolution_method is not None
    assert result.resolution_method.value == "UNRESOLVED_CANONICALIZED"
    assert registry.get_field_link(mention.mention_id, "participants[0]") is not None
    with sqlite3.connect(registry.path) as connection:
        count = connection.execute(
            """
            SELECT COUNT(*) FROM decision_audits
            WHERE decision_type = 'FIELD_MODEL_ADAPTER_FALLBACK'
            """
        ).fetchone()[0]
    assert count == 0
    assert len(resolver.model_client.requests) == 2


def test_candidate_order_is_stable_across_registry_insertion_order(
    tmp_path: Path,
) -> None:
    requests: list[list[str]] = []
    for index, order in enumerate(
        [
            [("METRIC-A", "Revenue value"), ("METRIC-B", "Revenue growth")],
            [("METRIC-B", "Revenue growth"), ("METRIC-A", "Revenue value")],
        ]
    ):
        registry = SQLiteCDECRRegistry(tmp_path / f"order-{index}.sqlite3")
        registry.initialize()
        mention = _mention(f"M-{index}", f"S-{index}")
        _persist_mention(registry, mention)
        for registry_id, surface in order:
            registry.create_field_registry_entry(
                CanonicalFieldRegistryEntry(
                    id=registry_id,
                    namespace=FieldNamespace.METRIC,
                    canonical_text=surface,
                    aliases=[surface],
                )
            )
        model = FakeStructuredClient([{"decision": "UNRESOLVED"}])
        resolver = FieldCoreferenceResolver(
            registry=registry,
            embedding_client=FakeEmbeddingClient(),
            model_client=model,
        )
        resolver.resolve(
            FieldCoreferenceInput(
                namespace=FieldNamespace.METRIC,
                raw_value="revenue metric",
                local_context="The filing compared revenue value and revenue growth.",
            ),
            mention_id=mention.mention_id,
            field_path="quantities[0].metric_id",
        )
        payload = json.loads(model.requests[0].user_prompt)
        requests.append([item["canonical_id"] for item in payload["candidates"]])
    assert requests[0] == requests[1]


def test_external_link_upgrades_provisional_in_place_and_reuses_root(
    registry: SQLiteCDECRRegistry,
) -> None:
    first = _mention("M-1", "S-1")
    second = _mention("M-2", "S-2")
    _persist_mention(registry, first)
    _persist_mention(registry, second)
    resolver = FieldCoreferenceResolver(
        registry=registry,
        embedding_client=FakeEmbeddingClient(),
        model_client=FakeStructuredClient(),
    )
    provisional = _resolve(resolver, first)
    upgraded = resolver.link_external(
        _input(first),
        mention_id=first.mention_id,
        field_path="locations[0]",
        external_id="KB:FAB21",
    )
    reused = resolver.link_external(
        _input(second),
        mention_id=second.mention_id,
        field_path="locations[0]",
        external_id="KB:FAB21",
    )

    assert upgraded.canonical_id == provisional.canonical_id
    assert reused.canonical_id == provisional.canonical_id
    assert upgraded.resolution_method is FieldLinkMethod.EXTERNAL_LINKING
    assert (
        registry.find_field_registry_by_external_id(
            namespace=FieldNamespace.OBJECT_FACILITY, external_id="KB:FAB21"
        )
        is not None
    )


def test_redirects_follow_roots_and_reject_cycles_or_trusted_conflicts(
    registry: SQLiteCDECRRegistry,
) -> None:
    for entry in (
        CanonicalFieldRegistryEntry(
            id="A",
            namespace=FieldNamespace.OBJECT_PROJECT,
            canonical_text="Project A",
            aliases=["Project A"],
        ),
        CanonicalFieldRegistryEntry(
            id="B",
            namespace=FieldNamespace.OBJECT_PROJECT,
            canonical_text="Project B",
            aliases=["Project B"],
        ),
    ):
        registry.create_field_registry_entry(entry)
    assert registry.save_field_redirect("A", "B")
    assert registry.resolve_field_registry_entry("A").id == "B"  # type: ignore[union-attr]
    with pytest.raises(RegistryError):
        registry.save_field_redirect("B", "A")

    registry.set_field_registry_external_id("B", "KB:B")
    registry.create_field_registry_entry(
        CanonicalFieldRegistryEntry(
            id="C",
            namespace=FieldNamespace.OBJECT_PROJECT,
            canonical_text="Project C",
            aliases=["Project C"],
            external_id="KB:C",
        )
    )
    with pytest.raises(ImmutableRecordConflict):
        registry.save_field_redirect("C", "B")


def test_current_link_can_be_corrected_without_rewriting_mention(
    registry: SQLiteCDECRRegistry,
) -> None:
    mention = _mention("M-1", "S-1")
    _persist_mention(registry, mention)
    for registry_id in ("F-1", "F-2"):
        registry.create_field_registry_entry(
            CanonicalFieldRegistryEntry(
                id=registry_id,
                namespace=FieldNamespace.OBJECT_FACILITY,
                canonical_text=registry_id,
                aliases=[registry_id],
            )
        )
    registry.save_field_link(
        CanonicalFieldLink(
            mention_id=mention.mention_id,
            field_path="locations[0]",
            registry_id="F-1",
            method=FieldLinkMethod.INTERNAL_COREFERENCE,
        )
    )
    original = registry.get_mention(mention.mention_id)
    resolver = FieldCoreferenceResolver(
        registry=registry,
        embedding_client=FakeEmbeddingClient(),
        model_client=FakeStructuredClient(),
    )
    corrected = resolver.update_link(
        mention_id=mention.mention_id,
        field_path="locations[0]",
        registry_id="F-2",
        resolution_method=FieldLinkMethod.INTERNAL_COREFERENCE,
        reason="manual false merge correction",
    )

    assert corrected.canonical_id == "F-2"
    assert registry.get_field_link(mention.mention_id, "locations[0]").registry_id == "F-2"  # type: ignore[union-attr]
    assert registry.get_mention(mention.mention_id) == original


def test_field_extraction_excludes_values_dates_units_and_assertion_state() -> None:
    mention = _mention("M-1", "S-1")
    inputs = field_inputs_for_mention(
        mention, local_context=mention.canonical_proposition, source_ticker="MU"
    )
    paths = {field_path for field_path, _ in inputs}
    assert "participants[0]" in paths
    assert "locations[0]" in paths
    assert "local_package_hint.anchor" not in paths
    assert all("time" not in path and "assertion_state" not in path for path in paths)




@pytest.mark.parametrize(
    "namespace",
    [
        FieldNamespace.PLACE,
        FieldNamespace.OBJECT_TECHNOLOGY,
        FieldNamespace.OBJECT_PROGRAM,
    ],
)
def test_atomic_field_recall_includes_place_technology_and_program(
    registry: SQLiteCDECRRegistry,
    namespace: FieldNamespace,
) -> None:
    suffix = namespace.value.replace(".", "-")
    mention = _mention(f"M-{suffix}", f"S-{suffix}")
    _persist_mention(registry, mention)
    entry = CanonicalFieldRegistryEntry(
        id=f"FIELD-{suffix}",
        namespace=namespace,
        canonical_text=mention.locations[0],
        aliases=[mention.locations[0]],
    )
    registry.create_field_registry_entry(entry)
    registry.save_field_link(
        CanonicalFieldLink(
            mention_id=mention.mention_id,
            field_path="locations[0]",
            registry_id=entry.id,
            method=FieldLinkMethod.INTERNAL_COREFERENCE,
        )
    )
    event = _atomic(mention)
    registry.save_atomic_event(event)

    recalled = registry.recall_atomic_event_ids(
        entity_ids=[],
        event_family="OTHER",
        normalized_predicate="other",
        schema_type="OPEN",
        reference_period_id=None,
        event_start=None,
        event_end=None,
        source_fingerprint=None,
        field_ids=[(namespace, entry.id)],
    )

    assert recalled[event.event_id] == {"FIELD_ID"}


def test_participant_canonical_identity_uses_core_entity_recall(
    registry: SQLiteCDECRRegistry,
) -> None:
    mention = _mention("M-company", "S-company")
    _persist_mention(registry, mention)
    event = _atomic(mention).model_copy(
        update={
            "identity_profile": OpenIdentityProfile(
                fields=OpenIdentityFields(
                    normalized_predicate=mention.predicate.normalized,
                    principal_participant_ids=["COMPANY_MU"],
                    event_time=mention.time,
                    reference_period_id=None,
                    location_or_asset_ids=[],
                    assertion_state=mention.assertion_state,
                )
            )
        }
    )
    registry.save_atomic_event(event)

    recalled = registry.recall_atomic_event_ids(
        entity_ids=["COMPANY_MU"],
        event_family="OTHER",
        normalized_predicate="other",
        schema_type="OPEN",
        reference_period_id=None,
        event_start=None,
        event_end=None,
        source_fingerprint=None,
    )

    assert recalled[event.event_id] == {"CORE_ENTITY"}


def test_different_provisional_ids_are_neutral_but_external_ids_can_conflict(
    registry: SQLiteCDECRRegistry,
) -> None:
    for index in (1, 2):
        mention = _mention(f"M-{index}", f"S-{index}")
        _persist_mention(registry, mention)
        entry = CanonicalFieldRegistryEntry(
            id=f"F-{index}",
            namespace=FieldNamespace.OBJECT_FACILITY,
            canonical_text=f"Facility {index}",
            aliases=[f"Facility {index}"],
        )
        registry.create_field_registry_entry(entry)
        registry.save_field_link(
            CanonicalFieldLink(
                mention_id=mention.mention_id,
                field_path="locations[0]",
                registry_id=entry.id,
                method=FieldLinkMethod.INTERNAL_COREFERENCE,
            )
        )
    left = resolved_field_entries(registry, ["M-1"], namespaces={FieldNamespace.OBJECT_FACILITY})
    right = resolved_field_entries(registry, ["M-2"], namespaces={FieldNamespace.OBJECT_FACILITY})
    assert not trusted_field_identity_conflict(left, right)
    registry.set_field_registry_external_id("F-1", "KB:1")
    registry.set_field_registry_external_id("F-2", "KB:2")
    left = resolved_field_entries(registry, ["M-1"], namespaces={FieldNamespace.OBJECT_FACILITY})
    right = resolved_field_entries(registry, ["M-2"], namespaces={FieldNamespace.OBJECT_FACILITY})
    assert trusted_field_identity_conflict(left, right)


def test_v5_registry_migrates_to_v6_without_replacing_existing_data(tmp_path: Path) -> None:
    path = tmp_path / "v5.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA user_version=5")
    connection.commit()
    connection.close()

    registry = SQLiteCDECRRegistry(path)
    registry.initialize()

    assert registry.pragma_state()["user_version"] == SCHEMA_VERSION
    with sqlite3.connect(path) as check:
        tables = {
            row[0]
            for row in check.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
    assert {"canonical_field_registry", "canonical_field_links"}.issubset(tables)
