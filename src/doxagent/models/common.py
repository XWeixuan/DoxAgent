"""Shared enums and type aliases for DoxAgent contracts."""

from enum import StrEnum


class AgentRole(StrEnum):
    OPERATOR = "operator"
    CONSULTANT = "consultant"
    AUDIT = "audit"
    SYSTEM = "system"


class AgentName(StrEnum):
    O1_EXPECTATION_OWNER = "O1"
    O2_MONITORING_CONFIG = "O2"
    O3_TRADING_STRATEGY = "O3"
    O4_MARKET_TRACE = "O4"
    C1_FUNDAMENTAL_RESEARCH = "C1"
    C2_MACRO_RESEARCH = "C2"
    C3_INDUSTRY_RESEARCH = "C3"
    A1_DOXATLAS_AUDIT = "A1"
    A2_FACT_CHECK = "A2"
    SYSTEM = "SYSTEM"


class DocumentType(StrEnum):
    GLOBAL_RESEARCH = "global_research"
    EXPECTATION_UNIT = "expectation_unit"
    KNOWN_EVENTS = "known_events"
    MONITORING_CONFIG = "monitoring_config"
    MONITORING_POLICY = "monitoring_policy"


class TaskType(StrEnum):
    GENERATE_GLOBAL_RESEARCH = "generate_global_research"
    GENERATE_GLOBAL_NARRATIVE_REPORT = "generate_global_narrative_report"
    GENERATE_EXPECTATION_UNIT = "generate_expectation_unit"
    GENERATE_EXPECTATION_DETAIL = "generate_expectation_detail"
    REVIEW_EXPECTATION_FIELD = "review_expectation_field"
    FACT_CHECK = "fact_check"
    DELEGATED_RETRIEVAL = "delegated_retrieval"
    GENERATE_KNOWN_EVENTS = "generate_known_events"
    GENERATE_MONITORING_CONFIG = "generate_monitoring_config"
    GENERATE_MONITORING_POLICY = "generate_monitoring_policy"


class PatchOperation(StrEnum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    PROMOTE = "promote"


class ResultStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    PARTIAL = "partial"


class ValidationStatus(StrEnum):
    PENDING = "pending"
    VALID = "valid"
    INVALID = "invalid"


class ObjectionSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    BLOCKING = "blocking"


class ObjectionStatus(StrEnum):
    OPEN = "open"
    ACCEPTED = "accepted"
    PARTIALLY_ACCEPTED = "partially_accepted"
    REJECTED = "rejected"
    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"


class DelegationStatus(StrEnum):
    OPEN = "open"
    ASSIGNED = "assigned"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ExpectationDirection(StrEnum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"
    RISK = "risk"


class EvidenceSourceType(StrEnum):
    DOXATLAS_SOURCE = "doxatlas_source"
    MARKET_DATA = "market_data"
    FACT_CHECK = "fact_check"
    EXTERNAL_REPORT = "external_report"
    AGENT_OUTPUT = "agent_output"
    TOOL_RESULT = "tool_result"


class PolicyActionType(StrEnum):
    DIRECT_TRADE = "direct_trade"
    PUSH_TO_AGENT = "push_to_agent"
    CACHE = "cache"
