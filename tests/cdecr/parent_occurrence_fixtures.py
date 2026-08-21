from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from cdecr.contracts import MembershipRelation
from cdecr.models import ModelTier
from cdecr.package_v3_contracts import (
    PackageV3Description,
    PackageV3DescriptionOutput,
    PackageV3ExistingAssignment,
    PackageV3InitialCluster,
    PackageV3InitialClusteringOutput,
    PackageV3RollingClusteringOutput,
)
from cdecr.parent_occurrence_contracts import (
    ParentInductionBatch,
    ParentInductionDecision,
    ParentInductionGroup,
    ParentMembershipDecision,
)
from cdecr.registry import SQLiteCDECRRegistry
from tests.cdecr.test_registry import atomic, mention, source


class ScriptedParentModels:
    model_m1 = "fake-embedding"

    def __init__(self, *, fail_stage: str | None = None) -> None:
        self.fail_stage = fail_stage
        self.stages: list[tuple[ModelTier, str]] = []

    def typed_many(
        self, *, tier, stage, requests, output_type, validators, execution_tier=None
    ):
        self.stages.extend((tier, stage) for _ in requests)
        if self.fail_stage == stage:
            return [RuntimeError("provider unavailable") for _ in requests]
        outputs = []
        for request, validator in zip(requests, validators, strict=True):
            payload = json.loads(request.user_prompt)
            if stage not in {"parent_induction", "parent_induction_repartition"}:
                raise AssertionError(f"unexpected Parent stage: {stage}")
            decisions = []
            for document in payload["document_tasks"]:
                decisions.append(
                    ParentInductionDecision(
                        task_id=document["task_id"],
                        groups=[
                            ParentInductionGroup(
                                local_group_id="G1",
                                scope="PARENT_OCCURRENCE",
                                label="Micron June update",
                                members=[
                                    ParentMembershipDecision(
                                        atomic_ref=item["atomic_ref"],
                                        membership_relation=MembershipRelation.COMPONENT_OF,
                                    )
                                    for item in document["atomics"]
                                ],
                            )
                        ],
                    )
                )
            value = ParentInductionBatch(decisions=decisions)
            validator(value)
            outputs.append(value)
        return outputs

    def typed_response(
        self, *, tier, stage, request, output_type, validator, execution_tier=None
    ):
        self.stages.append((tier, stage))
        if self.fail_stage == stage:
            raise RuntimeError("provider unavailable")
        payload = json.loads(request.input[-1]["content"])
        if stage == "package_v3_initial_clustering":
            occurrence_ids = [
                item["occurrence_id"] for item in payload["parent_occurrences"]
            ]
            value = PackageV3InitialClusteringOutput(
                clusters=[
                    PackageV3InitialCluster(
                        mcp_id="C1",
                        canonical="Micron June update",
                        occurrence_ids=occurrence_ids,
                    )
                ]
            )
        elif stage == "package_v3_description":
            value = PackageV3DescriptionOutput(
                descriptions=[
                    PackageV3Description(
                        mcp_id=item["mcp_id"],
                        compressed_description="Micron June update.",
                    )
                    for item in payload["mcps"]
                ]
            )
        elif stage == "package_v3_rolling_clustering":
            value = PackageV3RollingClusteringOutput(
                existing_assignments=[
                    PackageV3ExistingAssignment(
                        mcp_id=payload["registry"][0]["mcp_id"],
                        occurrence_ids=[
                            item["occurrence_id"]
                            for item in payload["new_parent_occurrences"]
                        ],
                    )
                ],
                new_mcps=[],
                merges=[],
            )
        else:
            raise AssertionError(f"unexpected Responses stage: {stage}")
        assert isinstance(value, output_type)
        validator(value)
        return value


def registry(tmp_path: Path) -> SQLiteCDECRRegistry:
    value = SQLiteCDECRRegistry(tmp_path / "parent-v2.sqlite3")
    value.initialize()
    return value


def two_document_inputs():
    first_source = source("MSG-1")
    second_source = source("MSG-2").model_copy(
        update={
            "title": "Micron June results",
            "text": "Micron reported another fact in the June update.",
            "url": "https://example.test/2",
            "published_at": datetime(2026, 6, 25, 13, tzinfo=UTC),
        }
    )
    first_mention = mention("MENTION-1", "MSG-1")
    second_mention = mention("MENTION-2", "MSG-2").model_copy(
        update={"canonical_proposition": "Micron reported another June fact."}
    )
    first_event = atomic(event_id="EVENT-1", mention_ids=["MENTION-1"])
    second_event = atomic(event_id="EVENT-2", mention_ids=["MENTION-2"]).model_copy(
        update={"canonical_proposition": "Micron reported another June fact."}
    )
    return (
        [first_source, second_source],
        [first_mention, second_mention],
        [first_event, second_event],
    )
