"""Read-only Published Event Library port shared by W1 and Document2."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import Field

from doxagent.event_library.compiler import EventLibraryViewCompiler
from doxagent.event_library.contracts import CanonicalEvent, StrictModel
from doxagent.event_library.repository import EventLibraryRepository


class KnownEventIndexSnapshot(StrictModel):
    ticker: str
    version: int = Field(ge=1)
    published_at: datetime | None
    known_event_index: str
    sha256: str


class EventDetailSnapshot(StrictModel):
    ticker: str
    version: int = Field(ge=1)
    events: list[CanonicalEvent]


class ReferenceEventViewSnapshot(StrictModel):
    ticker: str
    version: int = Field(ge=1)
    published_at: datetime | None
    reference_view: dict[str, Any]
    sha256: str


class PublishedEventLibraryReader:
    """Open only a ticker's Published SQLite view; never expose working state."""

    interface_version = "event-library-read-v1"
    read_only = True

    def __init__(self, root: str | Path, *, market: str = "US") -> None:
        self._root = Path(root).resolve()
        self._market = market.strip().upper()

    def _repository(self, ticker: str) -> EventLibraryRepository | None:
        path = self._root / self._market / ticker.strip().upper() / "event_library.sqlite3"
        if not path.is_file():
            return None
        return EventLibraryRepository(path, read_only=True)

    def known_index(self, ticker: str) -> KnownEventIndexSnapshot | None:
        repository = self._repository(ticker)
        if repository is None:
            return None
        version, published_at = repository.published_metadata(ticker)
        if version == 0:
            return None
        payload = EventLibraryViewCompiler(repository).known_event_index(ticker, version)
        return KnownEventIndexSnapshot(
            ticker=ticker.upper(),
            version=version,
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
        events = [repository.get_event(ticker, event_id, selected) for event_id in event_ids]
        return EventDetailSnapshot(
            ticker=ticker.upper(),
            version=selected,
            events=[event for event in events if event is not None],
        )

    def reference_view(self, ticker: str) -> ReferenceEventViewSnapshot | None:
        repository = self._repository(ticker)
        if repository is None:
            return None
        version, published_at = repository.published_metadata(ticker)
        if version == 0:
            return None
        payload = EventLibraryViewCompiler(repository).reference_view(ticker, version)
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return ReferenceEventViewSnapshot(
            ticker=ticker.upper(),
            version=version,
            published_at=published_at,
            reference_view=payload,
            sha256=hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        )
