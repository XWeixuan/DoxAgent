"""Published upstream adapters consumed by Persistent Runtime V2."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from doxagent.event_library.provider import (
    EventDetailSnapshot,
    KnownEventIndexSnapshot,
    PublishedEventLibraryReader,
)
from doxagent.workflows.codex_document3.repository import Document3PolicyRepository
from doxagent.workflows.codex_document3.runtime_projection import (
    Document3RuntimeProjectionConsumer,
)
from doxagent.workflows.codex_document3.schema import (
    PolicyDecision,
    PolicyDetailSnapshot,
    RuntimePolicyProjection,
    RuntimePolicyRecord,
)


@dataclass(frozen=True)
class RuntimeInputSnapshot:
    index: KnownEventIndexSnapshot | None
    projection: RuntimePolicyProjection | None
    activation_revision_id: str | None = None
    document1_run_id: str | None = None
    document2_run_id: str | None = None
    event_library_root: str | None = None
    visibility_day: str | None = None


class RuntimeKnownEventProvider(Protocol):
    def current_index(self, ticker: str) -> KnownEventIndexSnapshot | None: ...

    def details(
        self, ticker: str, version: int, event_ids: list[str]
    ) -> EventDetailSnapshot | None: ...

    def max_event_numeric_id(self, ticker: str, version: int) -> int | None: ...


class RuntimePolicyProvider(Protocol):
    def current_projection(self, ticker: str) -> RuntimePolicyProjection | None: ...

    def details(self, ticker: str, version: int, policy_ids: list[str]) -> PolicyDetailSnapshot: ...

    def decision(self, ticker: str, version: int, policy_id: str) -> PolicyDecision | None: ...

    def activation(
        self, ticker: str, version: int, policy_id: str
    ) -> RuntimePolicyRecord | None: ...


class PublishedEventLibraryRuntimeProvider:
    def __init__(self, reader: PublishedEventLibraryReader) -> None:
        self.reader = reader

    def current_index(self, ticker: str) -> KnownEventIndexSnapshot | None:
        return self.reader.known_index(ticker)

    def details(
        self, ticker: str, version: int, event_ids: list[str]
    ) -> EventDetailSnapshot | None:
        return self.reader.event_details(ticker, event_ids, version=version)

    def max_event_numeric_id(self, ticker: str, version: int) -> int | None:
        return self.reader.max_event_numeric_id(ticker, version=version)


class Document3RuntimePolicyProvider:
    def __init__(
        self,
        repository: Document3PolicyRepository,
        *,
        projection_ttl_seconds: float = 300.0,
    ) -> None:
        self.repository = repository
        self.consumer = Document3RuntimeProjectionConsumer(
            repository,
            ttl_seconds=projection_ttl_seconds,
        )

    def current_projection(self, ticker: str) -> RuntimePolicyProjection | None:
        return self.consumer.current(ticker)

    def details(self, ticker: str, version: int, policy_ids: list[str]) -> PolicyDetailSnapshot:
        return self.repository.get_policy_details(ticker, version, policy_ids)

    def decision(self, ticker: str, version: int, policy_id: str) -> PolicyDecision | None:
        policy_set = self.repository.get_version(ticker, version)
        if policy_set is None:
            return None
        return next(
            (policy.decision for policy in policy_set.policies if policy.policy_id == policy_id),
            None,
        )

    def activation(self, ticker: str, version: int, policy_id: str) -> RuntimePolicyRecord | None:
        projection = self.consumer.version(ticker, version)
        if projection is None:
            return None
        return next((item for item in projection.policies if item.policy_id == policy_id), None)
