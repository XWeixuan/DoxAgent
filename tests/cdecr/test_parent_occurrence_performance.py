from cdecr.contracts import MembershipRelation, PackageFamily
from cdecr.parent_occurrence import PackageStageSnapshotV2, ParentOccurrenceService
from cdecr.parent_occurrence_contracts import ParentProposalCard
from tests.cdecr.parent_occurrence_fixtures import (
    ScriptedParentModels,
    registry,
    two_document_inputs,
)


def test_snapshot_is_bounded_and_resolution_uses_shared_stage_waves(tmp_path) -> None:
    store = registry(tmp_path)
    sources, mentions, events = two_document_inputs()
    store.save_source(sources[0], fingerprint="a" * 64)
    store.start_cross_document_run(
        run_id="run-ok",
        processing_key="parent-v2-test",
        message_id=sources[0].message_id,
        engine_version="test",
        prompt_version="test",
        model_config={},
    )
    snapshot = PackageStageSnapshotV2.load(
        registry=store,
        events=events,
        mentions=mentions,
        sources=sources,
        packages=[],
    )
    assert snapshot.query_count == 0
    models = ScriptedParentModels()
    result = ParentOccurrenceService(registry=store).run(
        events=events,
        mentions=mentions,
        sources=sources,
        existing_packages=[],
        models=models,
        run_id="run-ok",
    )
    assert result.status == "FINALIZED"
    assert result.telemetry["snapshot_query_count"] <= 8
    assert result.telemetry["pair_registry_read_count"] == 0
    assert int(result.telemetry["resolution_wave_count"]) <= 3


def test_parent_profile_embeddings_respect_provider_batch_limit(tmp_path) -> None:
    class BatchLimitedModels(ScriptedParentModels):
        def __init__(self) -> None:
            super().__init__()
            self.embedding_batch_sizes: list[int] = []

        def embed(self, texts, *, stage):
            self.embedding_batch_sizes.append(len(texts))
            if len(texts) > 10:
                raise ValueError("provider batch limit exceeded")
            return super().embed(texts, stage=stage)

    store = registry(tmp_path)
    service = ParentOccurrenceService(registry=store)
    proposals = [
        ParentProposalCard(
            proposal_ref=f"G{index}",
            proposal_id=f"proposal-{index}",
            supporting_proposal_ids=[f"proposal-{index}"],
            scope="PARENT_OCCURRENCE",
            package_family=PackageFamily.EARNINGS_DISCLOSURE,
            label=f"Micron disclosure {index}",
            atomic_refs=[f"A{index}"],
            event_ids=[f"EVENT-{index}"],
            membership_by_event={f"EVENT-{index}": MembershipRelation.COMPONENT_OF},
            document_refs=[f"DOC-{index}"],
        )
        for index in range(23)
    ]
    models = BatchLimitedModels()

    embedded, _, telemetry = service._embed_cards(proposals, [], models=models)

    assert sorted(models.embedding_batch_sizes) == [3, 10, 10]
    assert all(item.embedding for item in embedded)
    assert telemetry["embedding_failure_count"] == 0
