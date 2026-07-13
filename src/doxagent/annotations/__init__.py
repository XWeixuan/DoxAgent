"""Non-blocking text annotations for agent outputs."""

from doxagent.annotations.models import (
    AnnotationBatch,
    AnnotationMetrics,
    CitationAnnotation,
    ProcessedText,
    TimeAnnotation,
)
from doxagent.annotations.citations import (
    CitationMention,
    normalize_citation_mentions,
    normalized_citation_aliases,
    strip_resolved_citations,
)
from doxagent.annotations.processor import TextAnnotationProcessor, render_time_tags
from doxagent.annotations.store import InMemoryAnnotationStore

__all__ = [
    "AnnotationBatch",
    "AnnotationMetrics",
    "CitationAnnotation",
    "CitationMention",
    "InMemoryAnnotationStore",
    "ProcessedText",
    "TextAnnotationProcessor",
    "TimeAnnotation",
    "render_time_tags",
    "normalize_citation_mentions",
    "normalized_citation_aliases",
    "strip_resolved_citations",
]
