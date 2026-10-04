"""Shared V2 identities, including persisted document type values."""

from enum import StrEnum


class AgentName(StrEnum):
    W1_RUNTIME_NOVELTY = "W1"
    W2_RUNTIME_POLICY = "W2"
    O1_EXPECTATION_OWNER = "O1"
    O2_MONITORING_CONFIG = "O2"
    O3_TRADING_STRATEGY = "O3"
    O4_MARKET_TRACE = "O4"
    C5_MARKET_IMPLIED_EXPECTATIONS = "C5"
    C1_FUNDAMENTAL_RESEARCH = "C1"
    C2_MACRO_RESEARCH = "C2"
    C3_INDUSTRY_RESEARCH = "C3"
    A1_DOXATLAS_AUDIT = "A1"
    A2_FACT_CHECK = "A2"
    SYSTEM = "SYSTEM"


class DocumentType(StrEnum):
    GLOBAL_RESEARCH = "global_research"
    MARKET_SITUATION_RESEARCH = "market_situation_research"
    EXPECTATION_UNIT = "expectation_unit"
    KNOWN_EVENTS = "known_events"
    MONITORING_CONFIG = "monitoring_config"
    MONITORING_POLICY = "monitoring_policy"


class ResultStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PARTIAL = "partial"
    EMPTY = "empty"
    NOT_APPLICABLE = "not_applicable"
