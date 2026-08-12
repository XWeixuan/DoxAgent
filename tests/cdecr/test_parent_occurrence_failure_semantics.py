import json

from cdecr.parent_occurrence import ParentOccurrenceService
from cdecr.parent_occurrence_contracts import (
    ParentInductionBatch,
    ParentInductionDecision,
    ParentInductionGroup,
    ParentMembershipDecision,
)
from tests.cdecr.parent_occurrence_fixtures import (
    ScriptedParentModels,
    registry,
    two_document_inputs,
)


def test_provider_failure_never_materializes_singleton_packages(tmp_path) -> None:
    store = registry(tmp_path)
    sources, mentions, events = two_document_inputs()
    result = ParentOccurrenceService(registry=store).run(
        events=events,
        mentions=mentions,
        sources=sources,
        existing_packages=[],
        models=ScriptedParentModels(fail_stage="parent_induction"),
        run_id="run-failed",
    )
    assert result.status == "PARTIAL_PARENT_RESOLUTION"
    assert result.partition is None
    assert store.list_current_packages(limit=100) == []


class FailPackedInductionOnce(ScriptedParentModels):
    def __init__(self) -> None:
        super().__init__()
        self.batch_sizes: list[int] = []

    def typed_many(self, *, tier, stage, requests, output_type, validators):
        if stage != "parent_induction":
            return super().typed_many(
                tier=tier,
                stage=stage,
                requests=requests,
                output_type=output_type,
                validators=validators,
            )
        outputs = []
        for request, validator in zip(requests, validators, strict=True):
            documents = json.loads(request.user_prompt)["document_tasks"]
            self.batch_sizes.append(len(documents))
            if len(documents) > 1:
                outputs.append(RuntimeError("packed task failed"))
                continue
            document = documents[0]
            value = ParentInductionBatch(
                decisions=[
                    ParentInductionDecision(
                        task_id=document["task_id"],
                        groups=[
                            ParentInductionGroup(
                                local_group_id="G1",
                                scope="PARENT_OCCURRENCE",
                                package_family="COMPANY_DISCLOSURE",
                                label="Micron June update",
                                members=[
                                    ParentMembershipDecision(
                                        atomic_ref=item["atomic_ref"],
                                        membership_relation="COMPONENT_OF",
                                    )
                                    for item in document["atomics"]
                                ],
                            )
                        ],
                    )
                ]
            )
            validator(value)
            outputs.append(value)
        return outputs


def test_packed_induction_failure_retries_each_document_and_reuses_checkpoints(tmp_path) -> None:
    store = registry(tmp_path)
    sources, mentions, events = two_document_inputs()
    store.save_source(sources[0], fingerprint="1" * 64)
    store.start_cross_document_run(
        run_id="induction-resume",
        processing_key="induction-resume",
        message_id=sources[0].message_id,
        engine_version="test",
        prompt_version="test",
        model_config={},
    )
    service = ParentOccurrenceService(registry=store)
    snapshot_type = __import__(
        "cdecr.parent_occurrence", fromlist=["PackageStageSnapshotV2"]
    ).PackageStageSnapshotV2
    snapshot = snapshot_type.load(
        registry=store, events=events, mentions=mentions, sources=sources, packages=[]
    )
    documents, _ = service._slices(snapshot)
    models = FailPackedInductionOnce()

    decisions, failures, telemetry = service._induce(
        documents,
        models=models,
        run_id="induction-resume",
        checkpoint_scope_id="induction-resume",
    )

    assert failures == []
    assert len(decisions) == 2
    assert models.batch_sizes == [2, 1, 1]
    assert telemetry["induction_item_retry_count"] == 2
    checkpoints = store.list_parent_occurrence_checkpoints(
        run_id="induction-resume", stage="PARENT_INDUCE"
    )
    assert {item["status"] for item in checkpoints} == {"SUCCEEDED"}

    reused_models = FailPackedInductionOnce()
    reused, reused_failures, reused_telemetry = service._induce(
        documents,
        models=reused_models,
        run_id="induction-resume",
        checkpoint_scope_id="induction-resume",
    )
    assert reused_failures == []
    assert len(reused) == 2
    assert reused_models.batch_sizes == []
    assert reused_telemetry["induction_reused_task_count"] == 2
