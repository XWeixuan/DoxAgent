"""Best-effort persistence boundary for text annotations."""

from __future__ import annotations

import hashlib
from copy import deepcopy
from typing import Protocol

from doxagent.annotations.models import CitationAnnotation, TimeAnnotation


class AnnotationStore(Protocol):
    def save_citations(self, records: list[CitationAnnotation]) -> None: ...

    def save_times(self, records: list[TimeAnnotation]) -> None: ...


class InMemoryAnnotationStore:
    def __init__(self) -> None:
        self.citations: list[CitationAnnotation] = []
        self.times: list[TimeAnnotation] = []

    def save_citations(self, records: list[CitationAnnotation]) -> None:
        self.citations.extend(deepcopy(records))

    def save_times(self, records: list[TimeAnnotation]) -> None:
        self.times.extend(deepcopy(records))

    def times_for_text(self, plain_text: str) -> list[TimeAnnotation]:
        text_hash = hashlib.sha256(plain_text.encode("utf-8")).hexdigest()
        return [deepcopy(item) for item in self.times if item.text_hash == text_hash]


__all__ = ["AnnotationStore", "InMemoryAnnotationStore"]
