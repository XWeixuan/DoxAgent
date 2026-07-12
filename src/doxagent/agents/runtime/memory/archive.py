"""Audit-only persistence boundary for task-local observations and raw tool results."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Protocol

from doxagent.agents.runtime.memory.observations import (
    ObservationBlock,
    ObservationService,
    RawToolResultRecord,
)


@dataclass(frozen=True)
class ObservationArchiveKey:
    run_id: str
    task_id: str


class ObservationArchive(Protocol):
    def save_task(
        self,
        *,
        run_id: str,
        task_id: str,
        observations: ObservationService,
    ) -> None: ...


class InMemoryObservationArchive:
    """Reference implementation; normal workflow reads never use this archive."""

    def __init__(self) -> None:
        self.raw_results: dict[tuple[str, str, str], RawToolResultRecord] = {}
        self.blocks: dict[tuple[str, str, str], ObservationBlock] = {}

    def save_task(
        self,
        *,
        run_id: str,
        task_id: str,
        observations: ObservationService,
    ) -> None:
        for record in observations.raw_store.records():
            self.raw_results[(run_id, task_id, record.tool_call_id)] = deepcopy(record)
        for block in observations.block_store.records():
            self.blocks[(run_id, task_id, block.block_id)] = deepcopy(block)

    def get_raw_result(
        self, run_id: str, task_id: str, tool_call_id: str
    ) -> RawToolResultRecord | None:
        return deepcopy(self.raw_results.get((run_id, task_id, tool_call_id)))

    def get_block(
        self, run_id: str, task_id: str, block_id: str
    ) -> ObservationBlock | None:
        return deepcopy(self.blocks.get((run_id, task_id, block_id)))


__all__ = ["InMemoryObservationArchive", "ObservationArchive", "ObservationArchiveKey"]
