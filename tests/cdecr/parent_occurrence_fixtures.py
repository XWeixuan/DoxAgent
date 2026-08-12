from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from cdecr.contracts import MembershipRelation
from cdecr.models import ModelTier
from cdecr.parent_occurrence_contracts import (
    ParentInductionBatch,
    ParentInductionDecision,
    ParentInductionGroup,
    ParentMembershipDecision,
    ParentResolutionBatch,
    ParentResolutionGroup,
)
from cdecr.ports import EmbeddingResult
from cdecr.registry import SQLiteCDECRRegistry
from tests.cdecr.test_registry import atomic, mention, source


class ScriptedParentModels:
    model_m1 = "fake-embedding"

    def __init__(self, *, fail_stage: str | None = None) -> None:
        self.fail_stage = fail_stage
        self.stages: list[tuple[ModelTier, str]] = []

    def typed_many(self, *, tier, stage, requests, output_type, validators):
        self.stages.extend((tier, stage) for _ in requests)
        if self.fail_stage == stage:
            return [RuntimeError("provider unavailable") for _ in requests]
        outputs = []
        for request, validator in zip(requests, validators, strict=True):
            payload = json.loads(request.user_prompt)
            if stage == "parent_induction":
                decisions = []
                for document in payload["document_tasks"]:
                    decisions.append(
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
                                            membership_relation=MembershipRelation.COMPONENT_OF,
                                        )
                                        for item in document["atomics"]
                                    ],
                                )
                            ],
                        )
                    )
                value = ParentInductionBatch(decisions=decisions)
            else:
                proposals = payload["proposals"]
                prototypes = payload["prototypes"]
                value = ParentResolutionBatch(
                    groups=[
                        ParentResolutionGroup(
                            resolution_group_id="R1",
                            proposal_refs=[item["proposal_ref"] for item in proposals],
                            existing_parent_refs=(
                                [prototypes[0]["parent_ref"]] if prototypes else []
                            ),
                            canonical_label="Micron June update",
                        )
                    ]
                )
            validator(value)
            outputs.append(value)
        return outputs

    def embed(self, texts, *, stage):
        return EmbeddingResult(
            model=self.model_m1,
            dimensions=2,
            vectors=[[1.0, 0.0] for _ in texts],
            input_tokens=len(texts),
            latency_ms=1,
        )


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
