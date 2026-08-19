from cdecr.bulk_epoch.package_stage import (
    project_parent_partition,
    resolve_parent_partition,
)
from cdecr.package_global_clustering import PackageWorkflowV3Service
from cdecr.parent_occurrence import ParentOccurrenceService
from tests.cdecr.parent_occurrence_fixtures import (
    ScriptedParentModels,
    registry,
    two_document_inputs,
)


def test_bulk_facade_resolves_one_frozen_partition_and_projects_once(tmp_path) -> None:
    store = registry(tmp_path)
    sources, mentions, events = two_document_inputs()
    for index, source in enumerate(sources):
        store.save_source(source, fingerprint=str(index + 1) * 64)
    for mention in mentions:
        store.save_mention(mention)
    for event in events:
        store.save_atomic_event(event)
    store.start_cross_document_run(
        run_id="bulk-parent-v2",
        processing_key="bulk-parent-v2",
        message_id=sources[0].message_id,
        engine_version="test",
        prompt_version="test",
        model_config={},
    )
    result = resolve_parent_partition(
        service=ParentOccurrenceService(registry=store),
        package_service=PackageWorkflowV3Service(registry=store),
        events=events,
        mentions=None,
        sources=None,
        existing_packages=[],
        models=ScriptedParentModels(),
        run_id="bulk-parent-v2",
    )
    assert result.status == "FINALIZED"
    assert result.partition is not None
    projected = project_parent_partition(
        result.partition, events=events, existing_packages=[], run_id="bulk-parent-v2"
    )
    assert len(projected[0]) == 1
    assert len(projected[1]) == 2
    assert result.telemetry["registry_batch_count"] == 1
    assert result.telemetry["package_count"] == 1


def test_parent_service_reuses_successful_induction_checkpoint_without_resolution(tmp_path) -> None:
    store = registry(tmp_path)
    sources, mentions, events = two_document_inputs()
    for index, source in enumerate(sources):
        store.save_source(source, fingerprint=str(index + 1) * 64)
    for mention in mentions:
        store.save_mention(mention)
    for event in events:
        store.save_atomic_event(event)
    for suffix in ("first", "second"):
        store.start_cross_document_run(
            run_id=f"parent-resume-{suffix}",
            processing_key=f"parent-resume-{suffix}",
            message_id=sources[0].message_id,
            engine_version="test",
            prompt_version="test",
            model_config={},
        )
    service = ParentOccurrenceService(registry=store)
    first_models = ScriptedParentModels()
    first = service.build_parent_occurrence_pool(
        events=events,
        mentions=None,
        sources=None,
        existing_packages=[],
        models=first_models,
        run_id="parent-resume-first",
        persistence_scope_id="epoch-parent-resume",
    )
    assert first.status == "FINALIZED"
    assert {stage for _, stage in first_models.stages} == {"parent_induction"}
    assert first.telemetry is not None
    assert first.telemetry["repartition_request_count"] == 0

    second_models = ScriptedParentModels()
    second = service.build_parent_occurrence_pool(
        events=events,
        mentions=None,
        sources=None,
        existing_packages=[],
        models=second_models,
        run_id="parent-resume-second",
        persistence_scope_id="epoch-parent-resume",
    )
    assert second.status == "FINALIZED"
    assert second.proposals == first.proposals
    assert second_models.stages == []
