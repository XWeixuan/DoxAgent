"""Persistence adapter for horizontal collection run-audit manifests."""

from __future__ import annotations

from doxagent.blackboard import BlackboardService
from doxagent.horizontal_collection.schema import (
    HORIZONTAL_COLLECTION_MANIFEST_ARTIFACT_KIND,
    HorizontalCollectionManifest,
)
from doxagent.models import AgentName, WorkingMemoryEntry


class HorizontalCollectionManifestRepository:
    """Persist manifests as append-only Blackboard run-audit artifacts.

    The content type is dedicated and is not consumed by the ReAct observation
    store.  It also does not enter the Blackboard document buckets used for
    Document 1/2/3 promotion.
    """

    def __init__(self, blackboard: BlackboardService) -> None:
        self.blackboard = blackboard

    def save(self, manifest: HorizontalCollectionManifest) -> WorkingMemoryEntry:
        return self.blackboard.add_working_memory_entry(
            manifest.run_id,
            author_agent=AgentName.SYSTEM,
            content_type=HORIZONTAL_COLLECTION_MANIFEST_ARTIFACT_KIND,
            payload=manifest.model_dump(mode="json"),
        )

    def list_for_run(self, run_id: str) -> tuple[HorizontalCollectionManifest, ...]:
        run = self.blackboard.get_run(run_id)
        return tuple(
            HorizontalCollectionManifest.model_validate(entry.payload)
            for entry in run.working_memory
            if entry.content_type == HORIZONTAL_COLLECTION_MANIFEST_ARTIFACT_KIND
        )
