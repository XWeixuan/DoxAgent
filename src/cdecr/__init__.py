"""Standalone open event discovery and cross-document coreference contracts."""

from cdecr.canonical_field_resolution import CanonicalFieldResolutionEngine
from cdecr.config import CDECRSettings
from cdecr.contracts import AtomicEvent, EventMention, EventPackage, SourceMessage
from cdecr.cross_document import CrossDocumentEngine
from cdecr.cross_document_contracts import CrossDocumentResult
from cdecr.field_coreference import FieldCoreferenceResolver
from cdecr.field_coreference_contracts import (
    CanonicalFieldLink,
    CanonicalFieldRegistryEntry,
    FieldCoreferenceResult,
    FieldNamespace,
)
from cdecr.identity_compiler import CompiledMentionIdentity, IdentityCompiler
from cdecr.kb_v2 import V2KnowledgeBase
from cdecr.mention_finalization import MentionFinalizer
from cdecr.single_document import SingleDocumentProcessor
from cdecr.single_document_contracts import SingleDocumentResult

__all__ = [
    "AtomicEvent",
    "CanonicalFieldResolutionEngine",
    "CDECRSettings",
    "CrossDocumentEngine",
    "CrossDocumentResult",
    "CompiledMentionIdentity",
    "EventMention",
    "EventPackage",
    "CanonicalFieldLink",
    "CanonicalFieldRegistryEntry",
    "FieldCoreferenceResolver",
    "FieldCoreferenceResult",
    "FieldNamespace",
    "IdentityCompiler",
    "MentionFinalizer",
    "SourceMessage",
    "SingleDocumentProcessor",
    "SingleDocumentResult",
    "V2KnowledgeBase",
]
