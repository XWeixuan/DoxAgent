"""High-level deterministic Event Library foundation service."""

from __future__ import annotations

from doxagent.event_library.compiler import EventLibraryViewCompiler
from doxagent.event_library.delta_compiler import DeltaCompiler
from doxagent.event_library.importer import RevisionBundleImporter
from doxagent.event_library.repository import EventLibraryRepository
from doxagent.event_library.validator import RevisionBundleValidator


class EventLibraryService:
    def __init__(self, repository: EventLibraryRepository) -> None:
        self.repository = repository
        self.delta_compiler = DeltaCompiler(repository)
        self.views = EventLibraryViewCompiler(repository)
        self.importer = RevisionBundleImporter(repository)
        self.validator = RevisionBundleValidator(repository)
