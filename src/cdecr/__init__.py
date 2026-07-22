"""Standalone open event discovery and cross-document coreference contracts."""

from cdecr.config import CDECRSettings
from cdecr.contracts import AtomicEvent, EventMention, EventPackage, SourceMessage
from cdecr.cross_document import CrossDocumentEngine
from cdecr.cross_document_contracts import CrossDocumentResult
from cdecr.single_document import SingleDocumentProcessor
from cdecr.single_document_contracts import SingleDocumentResult

__all__ = [
    "AtomicEvent",
    "CDECRSettings",
    "CrossDocumentEngine",
    "CrossDocumentResult",
    "EventMention",
    "EventPackage",
    "SourceMessage",
    "SingleDocumentProcessor",
    "SingleDocumentResult",
]
