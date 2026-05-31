"""Blackboard repository contracts and in-memory implementation."""

from collections.abc import Callable
from copy import deepcopy
from typing import Protocol

from doxagent.blackboard.errors import RunNotFoundError
from doxagent.blackboard.state import BlackboardRun

RunMutator = Callable[[BlackboardRun], BlackboardRun]


class BlackboardRepository(Protocol):
    def add(self, run: BlackboardRun) -> BlackboardRun: ...

    def get(self, run_id: str) -> BlackboardRun: ...

    def save(self, run: BlackboardRun) -> BlackboardRun: ...

    def list_by_ticker(self, ticker: str, *, limit: int = 20) -> list[BlackboardRun]: ...

    def mutate(self, run_id: str, mutator: RunMutator) -> BlackboardRun: ...


class InMemoryBlackboardRepository:
    def __init__(self) -> None:
        self._runs: dict[str, BlackboardRun] = {}

    def add(self, run: BlackboardRun) -> BlackboardRun:
        self._runs[run.run_id] = run.model_copy(deep=True)
        return run.model_copy(deep=True)

    def get(self, run_id: str) -> BlackboardRun:
        try:
            return self._runs[run_id].model_copy(deep=True)
        except KeyError as exc:
            raise RunNotFoundError(f"Blackboard run not found: {run_id}") from exc

    def save(self, run: BlackboardRun) -> BlackboardRun:
        if run.run_id not in self._runs:
            raise RunNotFoundError(f"Blackboard run not found: {run.run_id}")
        self._runs[run.run_id] = run.model_copy(deep=True)
        return run.model_copy(deep=True)

    def list_by_ticker(self, ticker: str, *, limit: int = 20) -> list[BlackboardRun]:
        runs = [run for run in self._runs.values() if run.ticker == ticker]
        runs.sort(key=lambda item: item.created_at, reverse=True)
        return [run.model_copy(deep=True) for run in runs[:limit]]

    def mutate(self, run_id: str, mutator: RunMutator) -> BlackboardRun:
        run = self.get(run_id)
        updated = mutator(run)
        return self.save(updated)

    def unsafe_get_mutable(self, run_id: str) -> BlackboardRun:
        try:
            return self._runs[run_id]
        except KeyError as exc:
            raise RunNotFoundError(f"Blackboard run not found: {run_id}") from exc

    def snapshot(self) -> dict[str, BlackboardRun]:
        return deepcopy(self._runs)
