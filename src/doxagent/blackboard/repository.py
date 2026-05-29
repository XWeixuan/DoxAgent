"""In-memory Blackboard repository."""

from copy import deepcopy

from doxagent.blackboard.errors import RunNotFoundError
from doxagent.blackboard.state import BlackboardRun


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

    def unsafe_get_mutable(self, run_id: str) -> BlackboardRun:
        try:
            return self._runs[run_id]
        except KeyError as exc:
            raise RunNotFoundError(f"Blackboard run not found: {run_id}") from exc

    def snapshot(self) -> dict[str, BlackboardRun]:
        return deepcopy(self._runs)
