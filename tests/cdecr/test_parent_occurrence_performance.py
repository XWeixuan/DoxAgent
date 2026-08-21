from cdecr.parent_occurrence import PackageStageSnapshotV2, ParentOccurrenceService
from tests.cdecr.parent_occurrence_fixtures import (
    ScriptedParentModels,
    registry,
    two_document_inputs,
)


def test_snapshot_is_bounded_and_active_parent_path_runs_induction_only(
    tmp_path, monkeypatch
) -> None:
    store = registry(tmp_path)
    sources, mentions, events = two_document_inputs()
    store.save_source(sources[0], fingerprint="a" * 64)
    store.start_cross_document_run(
        run_id="run-ok",
        processing_key="parent-v3-test",
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
    assert snapshot.query_count == 1

    models = ScriptedParentModels()
    service = ParentOccurrenceService(registry=store)

    def fail_if_repartitioned(*args, **kwargs):
        raise AssertionError("V3 Parent pool must not execute Repartition")

    monkeypatch.setattr(service, "_repartition_suspect_groups", fail_if_repartitioned)
    result = service.build_parent_occurrence_pool(
        events=events,
        mentions=mentions,
        sources=sources,
        existing_packages=[],
        models=models,
        run_id="run-ok",
    )

    assert result.status == "FINALIZED"
    assert result.telemetry is not None
    assert result.telemetry["snapshot_query_count"] <= 8
    assert result.telemetry["repartition_enabled"] is False
    assert result.telemetry["suspect_group_count"] == 0
    assert result.telemetry["repartition_request_count"] == 0
    assert models.stages
    assert {stage for _, stage in models.stages} == {"parent_induction"}
