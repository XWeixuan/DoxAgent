"""Read-only Published Event Library port shared by W1 and Document2."""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

from pydantic import Field

from doxagent.event_library.compiler import (
    KNOWN_EVENT_INDEX_CONTRACT_VERSION,
    REFERENCE_VIEW_CONTRACT_VERSION,
    EventLibraryViewCompiler,
)
from doxagent.event_library.contracts import (
    CanonicalEvent,
    ReferenceViewDeltaSnapshot,
    StrictModel,
)
from doxagent.event_library.repository import EventLibraryRepository


class KnownEventIndexSnapshot(StrictModel):
    contract_version: str = KNOWN_EVENT_INDEX_CONTRACT_VERSION
    ticker: str
    version: int = Field(ge=1)
    published_at: datetime | None
    known_event_index: str
    sha256: str


class EventDetailSnapshot(StrictModel):
    ticker: str
    version: int = Field(ge=1)
    requested_event_ids: list[str] = Field(default_factory=list)
    events: list[CanonicalEvent]
    missing_event_ids: list[str] = Field(default_factory=list)


class ReferenceEventViewSnapshot(StrictModel):
    contract_version: str = REFERENCE_VIEW_CONTRACT_VERSION
    ticker: str
    version: int = Field(ge=1)
    published_at: datetime | None
    reference_view: str
    sha256: str


class PublishedEventLibraryReader:
    """Open only a ticker's Published SQLite view; never expose working state."""

    interface_version = "event-library-read-v2"
    read_only = True

    def __init__(self, root: str | Path, *, market: str = "US") -> None:
        self._root = Path(root).resolve()
        self._market = market.strip().upper()

    def _repository(self, ticker: str) -> EventLibraryRepository | None:
        path = self._root / self._market / ticker.strip().upper() / "event_library.sqlite3"
        if not path.is_file():
            return None
        return EventLibraryRepository(path, read_only=True)

    def known_index(
        self, ticker: str, *, version: int | None = None
    ) -> KnownEventIndexSnapshot | None:
        repository = self._repository(ticker)
        if repository is None:
            return None
        selected, published_at = repository.published_metadata(ticker, version)
        if selected == 0:
            return None
        payload = EventLibraryViewCompiler(repository).known_event_index(ticker, selected)
        return KnownEventIndexSnapshot(
            ticker=ticker.upper(),
            version=selected,
            published_at=published_at,
            known_event_index=payload,
            sha256=hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        )

    def event_details(
        self, ticker: str, event_ids: list[str], *, version: int | None = None
    ) -> EventDetailSnapshot | None:
        repository = self._repository(ticker)
        if repository is None:
            return None
        selected = repository.published_version(ticker) if version is None else version
        if selected == 0:
            return None
        requested = list(dict.fromkeys(event_ids))
        events = [repository.get_event(ticker, event_id, selected) for event_id in requested]
        return EventDetailSnapshot(
            ticker=ticker.upper(),
            version=selected,
            requested_event_ids=requested,
            events=[event for event in events if event is not None],
            missing_event_ids=[
                event_id for event_id, event in zip(requested, events, strict=True)
                if event is None
            ],
        )

    def max_event_numeric_id(self, ticker: str, *, version: int | None = None) -> int | None:
        repository = self._repository(ticker)
        if repository is None:
            return None
        selected = repository.published_version(ticker) if version is None else version
        if selected == 0:
            return None
        return repository.max_published_event_numeric_id(ticker, selected)

    def reference_view(
        self, ticker: str, *, version: int | None = None
    ) -> ReferenceEventViewSnapshot | None:
        repository = self._repository(ticker)
        if repository is None:
            return None
        selected, published_at = repository.published_metadata(ticker, version)
        if selected == 0:
            return None
        payload = EventLibraryViewCompiler(repository).reference_view(ticker, selected)
        return ReferenceEventViewSnapshot(
            ticker=ticker.upper(),
            version=selected,
            published_at=published_at,
            reference_view=payload,
            sha256=hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        )

    def reference_view_delta(
        self,
        ticker: str,
        *,
        from_version: int,
        to_version: int,
    ) -> ReferenceViewDeltaSnapshot | None:
        repository = self._repository(ticker)
        if repository is None:
            return None
        cached = repository.get_reference_view_delta(ticker, from_version, to_version)
        if cached is not None:
            return ReferenceViewDeltaSnapshot.model_validate(cached)
        return EventLibraryViewCompiler(repository).reference_view_delta(
            ticker,
            from_version=from_version,
            to_version=to_version,
            persist=False,
        )
